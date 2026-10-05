"""R-MAIL — Envío de correo por Google Workspace.

Hasta acá `VERIFICACION_ENVIO_PROVEEDOR` admitía `ninguno` y `log`, y ninguno
de los dos mandaba nada: el portal decía «vas a recibir un enlace» y el enlace
no salía. Este módulo es el proveedor real: SMTP de Google Workspace,
autenticando como `notificaciones@equipos.com.uy` con una **contraseña de
aplicación** que vive en Secret Manager (`SMTP_PASSWORD`).

Tres decisiones que conviene dejar escritas:

**Cada correo tiene su plantilla.** El asunto y el cuerpo dicen quién escribe
y por qué. Un correo genérico que dice «tu código: 123456» cae en spam y,
peor, entrena a la gente a abrir correos genéricos con códigos. Las
plantillas viven acá, en un solo lugar, y no repartidas entre el portal y la
landing: dos textos parecidos en dos lugares terminan divergiendo.

**Reintentar es acotado, y lo acota el tiempo.** Un fallo transitorio
(conexión cortada, un 4xx del servidor) se reintenta, pero la persona está
esperando la respuesta de la página: el envío entero tiene un presupuesto de
segundos (`SMTP_PRESUPUESTO_S`) y cada intento toma lo que queda. Un fallo
permanente —credencial rechazada, destinatario inválido— no se reintenta:
insistir no lo arregla.

**El envío se registra, salga o no** (`envio_correo`, bóveda/0023). Es lo que
deja ver los fallidos con su motivo y contar cuántos correos salen por día:
Workspace corta en 2.000 destinatarios diarios por cuenta, y es mejor ver que
se acerca el tope que descubrirlo con envíos rechazados. El registro está en
la bóveda porque el destinatario es un dato personal; una baja lo borra
(`bajas.retirar`) y los registros viejos se purgan solos.

La contraseña nunca se loguea, ni se devuelve, ni aparece en un mensaje de
error: los errores de `smtplib` traen el código y el texto del servidor, no
las credenciales.
"""

import os
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from . import db

HOST_POR_DEFECTO = "smtp.gmail.com"
PUERTO_POR_DEFECTO = 587
USUARIO_POR_DEFECTO = "notificaciones@equipos.com.uy"
REMITENTE_POR_DEFECTO = "Equipos Consultores <notificaciones@equipos.com.uy>"

# Cuánto puede tardar un intento y cuánto el envío entero. La página espera
# la respuesta: más de unos segundos y la persona vuelve a apretar el botón.
TIEMPO_POR_INTENTO_S = 8
PRESUPUESTO_S = 15
REINTENTOS = 2

# Workspace: 2.000 destinatarios por día por cuenta (§7 de la spec).
TOPE_DIARIO = 2000

# Cuánto se conserva el registro de envíos. Alcanza para mirar un problema de
# entregabilidad de la semana pasada y no acumula direcciones para siempre.
DIAS_DE_REGISTRO = 90

# ── Los tipos de correo ──────────────────────────────────────────────

CODIGO_VERIFICACION = "codigo_verificacion"
ALTA_CLAVE = "alta_clave"
RECUPERACION_CLAVE = "recuperacion_clave"
ACCESO_USUARIO = "acceso_usuario"
PRUEBA = "prueba"

_FIRMA = (
    "\n\n—\nEquipos Consultores · Panel de opinión\n"
    "Este correo se envió automáticamente. Si no lo pediste, podés ignorarlo."
)

PLANTILLAS = {
    CODIGO_VERIFICACION: {
        "asunto": "Tu código de verificación: {contenido}",
        "texto": (
            "Hola:\n\n"
            "Alguien —seguramente vos— pidió verificar esta dirección de correo "
            "en el panel de Equipos Consultores.\n\n"
            "Tu código es: {contenido}\n\n"
            "Vence en {minutos} minutos. Si no fuiste vos, no hagas nada: sin "
            "el código nadie puede usar tu correo."
        ),
    },
    ALTA_CLAVE: {
        "asunto": "Creá tu contraseña del portal del panelista",
        "texto": (
            "Hola:\n\n"
            "Para entrar al portal del panel de Equipos Consultores —donde ves "
            "tus puntos, corregís tus datos y elegís cómo querés que te "
            "contactemos— primero tenés que crear tu contraseña.\n\n"
            "Hacelo desde este enlace:\n{contenido}\n\n"
            "El enlace sirve una sola vez y vence en {horas} horas."
        ),
    },
    RECUPERACION_CLAVE: {
        "asunto": "Recuperá tu contraseña del portal del panelista",
        "texto": (
            "Hola:\n\n"
            "Recibimos un pedido para cambiar la contraseña de tu cuenta en el "
            "portal del panel de Equipos Consultores.\n\n"
            "Elegí una nueva desde este enlace:\n{contenido}\n\n"
            "El enlace sirve una sola vez y vence en {horas} horas. Si no lo "
            "pediste vos, ignorá este correo: tu contraseña actual sigue igual."
        ),
    },
    ACCESO_USUARIO: {
        "asunto": "Tu acceso al sistema de gestión de paneles",
        "texto": (
            "Hola:\n\n"
            "Te dieron acceso al sistema de gestión de paneles de Equipos "
            "Consultores. Para entrar, fijá tu contraseña desde este enlace:\n"
            "{contenido}\n\n"
            "Si el enlace vence, pedí otro con «¿Olvidaste tu contraseña?» en "
            "la pantalla de ingreso."
        ),
    },
    PRUEBA: {
        "asunto": "Correo de prueba del sistema de gestión de paneles",
        "texto": (
            "Este es un correo de prueba enviado desde Cumplimiento → Contacto "
            "para comprobar la configuración de envío.\n\n"
            "Si lo estás leyendo, el envío por Google Workspace funciona. "
            "Mirá también que no haya caído en spam: si cayó, revisá SPF, "
            "DKIM y DMARC del dominio."
        ),
    },
}
TIPOS = tuple(PLANTILLAS)


class ErrorEnvio(RuntimeError):
    """El proveedor no pudo entregar. `transitorio` dice si vale reintentar."""

    def __init__(self, motivo, transitorio=False, intentos=1):
        super().__init__(motivo)
        self.motivo = motivo
        self.transitorio = transitorio
        self.intentos = intentos


def armar(tipo, contenido, **datos):
    """`(asunto, texto)` del correo de ese tipo."""
    plantilla = PLANTILLAS.get(tipo)
    if not plantilla:
        raise ValueError(f"Tipo de correo desconocido: {tipo!r}.")
    valores = {"contenido": contenido, "minutos": 10, "horas": 24, **datos}
    return (plantilla["asunto"].format(**valores),
            plantilla["texto"].format(**valores) + _FIRMA)


def _entero(valor, por_defecto):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return por_defecto


class Workspace:
    """SMTP de Google Workspace: 587 con STARTTLS, o 465 con SSL."""

    nombre = "workspace"

    def __init__(self, clave, host=HOST_POR_DEFECTO, puerto=PUERTO_POR_DEFECTO,
                 usuario=USUARIO_POR_DEFECTO, remitente=REMITENTE_POR_DEFECTO,
                 reply_to=None, tiempo=TIEMPO_POR_INTENTO_S,
                 presupuesto=PRESUPUESTO_S, reintentos=REINTENTOS,
                 smtp=None, reloj=time.monotonic, esperar=time.sleep):
        self.clave = clave
        self.host = host
        self.puerto = int(puerto)
        self.usuario = usuario
        self.remitente = remitente
        self.reply_to = reply_to or None
        self.tiempo = tiempo
        self.presupuesto = presupuesto
        self.reintentos = reintentos
        # Para las pruebas: una fábrica `(host, puerto, timeout, ssl) -> conexión`
        # que imita a smtplib. En producción es smtplib.
        self._smtp = smtp
        self._reloj = reloj
        self._esperar = esperar

    @classmethod
    def desde_entorno(cls, entorno=None):
        entorno = os.environ if entorno is None else entorno
        return cls(
            clave=(entorno.get("SMTP_PASSWORD") or "").strip(),
            host=(entorno.get("SMTP_HOST") or HOST_POR_DEFECTO).strip(),
            puerto=_entero(entorno.get("SMTP_PORT") or PUERTO_POR_DEFECTO,
                           PUERTO_POR_DEFECTO),
            usuario=(entorno.get("SMTP_USUARIO") or USUARIO_POR_DEFECTO).strip(),
            remitente=(entorno.get("SMTP_REMITENTE") or REMITENTE_POR_DEFECTO).strip(),
            reply_to=(entorno.get("SMTP_REPLY_TO") or "").strip() or None,
            tiempo=_entero(entorno.get("SMTP_TIEMPO_S") or TIEMPO_POR_INTENTO_S,
                           TIEMPO_POR_INTENTO_S),
            presupuesto=_entero(entorno.get("SMTP_PRESUPUESTO_S") or PRESUPUESTO_S,
                                PRESUPUESTO_S),
        )

    # ── El mensaje ──

    def mensaje(self, destino, asunto, texto):
        msg = EmailMessage()
        msg["From"] = self.remitente
        msg["To"] = destino
        msg["Subject"] = asunto
        msg["Date"] = formatdate(localtime=False)
        dominio = (parseaddr(self.remitente)[1] or self.usuario).split("@")[-1]
        msg["Message-ID"] = make_msgid(domain=dominio)
        if self.reply_to:
            msg["Reply-To"] = self.reply_to
        msg.set_content(texto)
        return msg

    # ── La conexión ──

    def _conectar(self, timeout):
        if self._smtp is not None:
            return self._smtp(self.host, self.puerto, timeout, self.puerto == 465)
        contexto = ssl.create_default_context()
        if self.puerto == 465:
            return smtplib.SMTP_SSL(self.host, self.puerto, timeout=timeout,
                                    context=contexto)
        conexion = smtplib.SMTP(self.host, self.puerto, timeout=timeout)
        conexion.ehlo()
        conexion.starttls(context=contexto)
        conexion.ehlo()
        return conexion

    def _un_intento(self, msg, timeout):
        conexion = self._conectar(timeout)
        try:
            conexion.login(self.usuario, self.clave)
            conexion.send_message(msg)
        finally:
            try:
                conexion.quit()
            except Exception:  # noqa: BLE001 — cerrar no puede tapar el resultado
                pass

    @staticmethod
    def _clasificar(error):
        """`(motivo, transitorio)`. El motivo no lleva credenciales: smtplib
        reporta el código y el texto del servidor, nada más."""
        if isinstance(error, smtplib.SMTPAuthenticationError):
            return (f"Workspace rechazó la credencial ({error.smtp_code}). "
                    "Revisá SMTP_USUARIO y que SMTP_PASSWORD sea una contraseña "
                    "de aplicación vigente.", False)
        if isinstance(error, smtplib.SMTPRecipientsRefused):
            respuestas = list(error.recipients.values())
            codigos = [c for c, _ in respuestas]
            transitorio = all(400 <= c < 500 for c in codigos) if codigos else False
            detalle = "; ".join(
                f"{c} {t.decode('utf-8', 'replace') if isinstance(t, bytes) else t}"
                for c, t in respuestas)
            return (f"El servidor rechazó el destinatario: {detalle[:200]}", transitorio)
        if isinstance(error, smtplib.SMTPSenderRefused):
            return (f"El servidor rechazó el remitente ({error.smtp_code}). "
                    "SMTP_REMITENTE tiene que ser la cuenta o un alias suyo.",
                    400 <= error.smtp_code < 500)
        if isinstance(error, smtplib.SMTPResponseException):
            texto = error.smtp_error
            if isinstance(texto, bytes):
                texto = texto.decode("utf-8", "replace")
            return (f"El servidor respondió {error.smtp_code}: {str(texto)[:200]}",
                    400 <= error.smtp_code < 500)
        if isinstance(error, (smtplib.SMTPServerDisconnected,
                              smtplib.SMTPConnectError)):
            return (f"Se cortó la conexión con {_texto_de_error(error)}.", True)
        if isinstance(error, (socket.timeout, TimeoutError)):
            return ("El servidor de correo no respondió a tiempo.", True)
        if isinstance(error, OSError):
            # DNS, red, egress bloqueado: el caso que la spec pide verificar
            # con el correo de prueba antes de darlo por hecho.
            return (f"No se pudo llegar al servidor de correo: "
                    f"{_texto_de_error(error)}", True)
        return (f"Error inesperado al enviar: {_texto_de_error(error)}", False)

    def enviar(self, destino, asunto, texto):
        """Manda el mensaje. Devuelve `{intentos, ms}` o levanta `ErrorEnvio`."""
        if not self.clave:
            raise ErrorEnvio(
                "Falta SMTP_PASSWORD (la contraseña de aplicación de "
                f"{self.usuario}, en Secret Manager).", transitorio=False,
                intentos=0)
        msg = self.mensaje(destino, asunto, texto)
        desde = self._reloj()
        limite = desde + self.presupuesto
        intentos, ultimo = 0, None
        while intentos <= self.reintentos:
            restante = limite - self._reloj()
            if restante <= 0.5:
                break
            intentos += 1
            try:
                self._un_intento(msg, min(self.tiempo, restante))
                return {"intentos": intentos,
                        "ms": int((self._reloj() - desde) * 1000)}
            except Exception as error:  # noqa: BLE001 — se clasifica abajo
                motivo, transitorio = self._clasificar(error)
                ultimo = ErrorEnvio(motivo, transitorio, intentos)
                if not transitorio:
                    raise ultimo from error
                # Espera corta y creciente, sin pasarse del presupuesto.
                pausa = min(0.5 * intentos, max(0.0, limite - self._reloj() - 1))
                if pausa > 0 and intentos <= self.reintentos:
                    self._esperar(pausa)
        if ultimo is None:
            ultimo = ErrorEnvio("Se agotó el tiempo para enviar el correo.",
                                True, intentos)
        raise ultimo


def _texto_de_error(error):
    return str(error)[:200] or type(error).__name__


# ── Registro de envíos (bóveda/0023) ─────────────────────────────────

def registrar(conn, tipo, destinatario, proveedor, estado, motivo=None,
              intentos=1, duracion_ms=None):
    """Una fila por envío, salga o no. Purga lo viejo de paso: el registro
    es para diagnosticar, no un archivo de direcciones."""
    db.ejecutar(
        conn,
        """insert into envio_correo
                  (tipo, destinatario, proveedor, estado, motivo, intentos,
                   duracion_ms)
           values (%s, %s, %s, %s, %s, %s, %s)""",
        (tipo, destinatario, proveedor or "desconocido", estado,
         (motivo or None) and str(motivo)[:500], intentos, duracion_ms))
    db.ejecutar(
        conn,
        "delete from envio_correo "
        "where creado_en < now() - make_interval(days => %s)",
        (DIAS_DE_REGISTRO,))


def enviados_ultimas_24h(conn):
    fila = db.una(
        conn,
        "select count(*)::int as n from envio_correo "
        "where estado = 'enviado' and creado_en > now() - interval '24 hours'")
    return fila["n"]


def listar(conn, estado=None, limite=100):
    """Los envíos recientes, para la pantalla de Cumplimiento."""
    filas = db.todas(
        conn,
        """select id, tipo, destinatario, proveedor, estado, motivo, intentos,
                  duracion_ms, creado_en
             from envio_correo
            where (%s::text is null or estado = %s)
            order by creado_en desc, id desc
            limit %s""",
        (estado, estado, int(limite)))
    return [{**f, "creado_en": f["creado_en"].isoformat()} for f in filas]


def por_dia(conn, dias=14):
    filas = db.todas(
        conn,
        "select dia, enviados, fallidos from v_envio_correo_por_dia "
        "where dia > (now() at time zone 'America/Montevideo')::date - %s "
        "order by dia desc",
        (int(dias),))
    return [{**f, "dia": f["dia"].isoformat()} for f in filas]


def borrar_de(conn, email):
    """R1.3 — una baja se lleva también el rastro de envíos a su correo."""
    if not (email or "").strip():
        return 0
    return db.ejecutar(
        conn, "delete from envio_correo where lower(destinatario) = lower(%s)",
        (email.strip(),))
