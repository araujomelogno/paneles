"""R4.3 — Verificación de contacto de la landing.

La landing de Fase 3 aceptaba cualquier envío: nadie comprobaba que el celular
o el correo fueran de quien se estaba inscribiendo. Eso deja entrar dos cosas
distintas y las dos malas —datos inventados, que ensucian la cola de
aprobación, y **datos ajenos**, que es inscribir a alguien sin que se entere—.

El mecanismo es un código de un solo uso: se pide, se manda al contacto, y la
inscripción **no existe** hasta que se verifica. No es una casilla más del
formulario: es una precondición de escribir.

Cuatro decisiones que vale la pena explicar:

**El código se guarda hasheado.** Un código en claro en la base es una
credencial de un solo uso al alcance de cualquiera que lea la tabla, y alcanza
para inscribir a nombre de otro. Se guarda el hash y se compara el hash.

**La IP también se hashea.** Para limitar la tasa por origen alcanza con saber
que dos pedidos vinieron del mismo lado; de cuál no hace falta, y guardarlo
sería juntar un dato personal más en una superficie pública.

**Los intentos se cuentan y el código se agota.** Sin eso, seis dígitos se
adivinan por fuerza bruta en minutos.

**El envío va detrás de una interfaz.** Igual que los embeddings y el
reranker: en producción un proveedor real (`workspace`, ver `correo.py`); en
desarrollo, `log` o `ninguno`.

**Sin proveedor no hay atajo** (R-MAIL.2). Hasta R-MAIL, la falta de
proveedor devolvía el código —y el enlace del portal— en la respuesta, «modo
desarrollo». En el sitio público eso era un acceso directo a la cuenta ajena:
alcanzaba con escribir el correo de otra persona. Ahora son dos condiciones
distintas que no se colapsan: la **ausencia de proveedor** es un error de
configuración (`EnvioNoConfigurado`), y el **modo desarrollo** es una señal
explícita (`ENVIO_MODO_DESARROLLO`) que además se ignora en el entorno
desplegado.
"""

import hashlib
import hmac
import os
import secrets

from . import db
from .errores import (
    Conflicto, DatosInvalidos, EnvioFallido, EnvioNoConfigurado, NoEncontrado,
)

CELULAR = "celular"
EMAIL = "email"
CANALES = (CELULAR, EMAIL)

# Cuánto vive un código. Diez minutos es el equilibrio habitual: alcanza para
# ir a buscar el teléfono y no tanto como para que un código filtrado sirva al
# día siguiente.
MINUTOS_DE_VIDA = 10

# Cuántas veces se puede errar antes de quemar el código.
MAX_INTENTOS = 5

# Cuántos códigos se pueden pedir desde el mismo origen en una hora. Es el
# freno a la tasa inusual de envíos: no bloquea a nadie legítimo —nadie se
# inscribe diez veces por hora— y corta el uso de la landing como oráculo o
# como forma de mandarle mensajes a terceros.
MAX_POR_ORIGEN_POR_HORA = 10

# Y cuántos al mismo destino, que es el freno que protege a la persona del
# otro lado: sin él, la landing sirve para bombardear a un número ajeno.
MAX_POR_DESTINO_POR_HORA = 5

DIGITOS = 6


def _sal():
    """La sal con la que se hashean códigos y orígenes.

    Sale del entorno. Sin ella el hash de un código de seis dígitos es
    trivialmente reversible con una tabla de un millón de entradas, y el
    hash de una IP también.
    """
    return os.environ.get("VERIFICACION_SAL", "").encode() or b"paneles-dev"


def _hash(valor):
    return hmac.new(_sal(), str(valor).encode(), hashlib.sha256).hexdigest()


def hash_origen(origen):
    """El origen del pedido (IP, por ejemplo), hasheado."""
    return _hash(f"origen:{origen}") if origen else None


def normalizar_destino(canal, destino):
    canal = (canal or "").strip().lower()
    if canal not in CANALES:
        raise DatosInvalidos(
            f"Canal de verificación desconocido: {canal!r}.",
            {"canales_validos": list(CANALES)})
    crudo = str(destino or "").strip()
    if not crudo:
        raise DatosInvalidos("Falta el contacto a verificar.")
    if canal == CELULAR:
        from . import preferencias

        normalizado = preferencias.normalizar_celular(crudo)
        if not normalizado:
            raise DatosInvalidos(
                "Ese celular no parece válido. Escribilo con el código de "
                "país o en formato local (por ejemplo, 099 123 456).")
        return canal, normalizado
    if "@" not in crudo or "." not in crudo.split("@")[-1]:
        raise DatosInvalidos("Ese correo no parece válido.")
    return canal, crudo.lower()


def _contar(conn, columna, valor, horas=1):
    fila = db.una(
        conn,
        f"""
        select count(*)::int as n from verificacion_contacto
         where {columna} = %s and creado_en > now() - make_interval(hours => %s)
        """,
        (valor, horas),
    )
    return fila["n"]


def pedir_codigo(conn, canal, destino, origen=None, enviar=None):
    """Emite un código y lo manda. Devuelve qué mostrar en la pantalla.

    **Nunca devuelve el código** fuera del modo desarrollo: si lo devolviera,
    la verificación no verificaría nada —quien pide el código lo recibe en la
    misma respuesta, sin necesidad de tener acceso al contacto—.

    El proveedor se resuelve **antes** de emitir nada: sin proveedor (y sin
    modo desarrollo) no se quema un código ni se cuenta un pedido.
    """
    canal, destino = normalizar_destino(canal, destino)
    enviador = enviar or proveedor_de_envio(canal=canal)
    origen_hash = hash_origen(origen)

    if origen_hash and _contar(conn, "origen_hash", origen_hash) >= MAX_POR_ORIGEN_POR_HORA:
        raise Conflicto(
            "Se pidieron demasiados códigos desde este dispositivo. Esperá un "
            "rato y volvé a intentar.",
            {"motivo": "tasa_por_origen"})
    if _contar(conn, "destino", destino) >= MAX_POR_DESTINO_POR_HORA:
        raise Conflicto(
            "Se pidieron demasiados códigos para este contacto. Esperá un "
            "rato y volvé a intentar.",
            {"motivo": "tasa_por_destino"})

    codigo = f"{secrets.randbelow(10 ** DIGITOS):0{DIGITOS}d}"
    # Los pendientes anteriores del mismo destino se queman: si no, quedan
    # varios códigos vivos a la vez y el más viejo sigue sirviendo.
    db.ejecutar(
        conn,
        "update verificacion_contacto set estado = 'vencido' "
        "where destino = %s and canal = %s and estado = 'pendiente'",
        (destino, canal))
    fila = db.una(
        conn,
        """
        insert into verificacion_contacto
               (canal, destino, codigo_hash, vence_en, origen_hash)
             values (%s, %s, %s, now() + make_interval(mins => %s), %s)
          returning id, vence_en
        """,
        (canal, destino, _hash(codigo), MINUTOS_DE_VIDA, origen_hash),
    )
    conn.commit()

    resultado = enviar_y_registrar(
        conn, enviador, canal, destino, codigo, tipo="codigo_verificacion",
        minutos=MINUTOS_DE_VIDA)
    salida = {
        "verificacion_id": fila["id"],
        "canal": canal,
        "vence_en": fila["vence_en"].isoformat(),
        "minutos": MINUTOS_DE_VIDA,
        "enviado_por": resultado.get("proveedor"),
    }
    if resultado.get("sin_proveedor") and modo_desarrollo():
        # Modo desarrollo, encendido a propósito y nunca en el entorno
        # desplegado: no hay a dónde mandarlo, así que vuelve en la respuesta
        # y se dice con todas las letras que eso no es verificar. Ninguna
        # página pública lo muestra (R-MAIL.2).
        salida["codigo_sin_enviar"] = codigo
        salida["aviso"] = (
            "No hay proveedor de envío configurado: el código vuelve en esta "
            "respuesta y la verificación no prueba nada. No usar así en "
            "producción.")
    return salida


def verificar(conn, canal, destino, codigo):
    """Comprueba el código. Devuelve el registro verificado.

    Un código verificado queda `verificado` y sirve una sola vez: lo consume
    la inscripción que lo usa (`consumir`).
    """
    canal, destino = normalizar_destino(canal, destino)
    fila = db.una(
        conn,
        """
        select id, codigo_hash, estado, intentos, vence_en
          from verificacion_contacto
         where canal = %s and destino = %s and estado = 'pendiente'
         order by creado_en desc
         limit 1
        """,
        (canal, destino),
    )
    if not fila:
        raise NoEncontrado(
            "No hay un código pendiente para ese contacto. Pedí uno nuevo.")

    if fila["vence_en"] <= _ahora(conn):
        db.ejecutar(conn, "update verificacion_contacto set estado = 'vencido' "
                          "where id = %s", (fila["id"],))
        conn.commit()
        raise Conflicto("El código venció. Pedí uno nuevo.",
                        {"motivo": "vencido"})

    if fila["intentos"] + 1 >= MAX_INTENTOS and not hmac.compare_digest(
            fila["codigo_hash"], _hash(str(codigo or "").strip())):
        db.ejecutar(conn, "update verificacion_contacto "
                          "set estado = 'vencido', intentos = intentos + 1 "
                          "where id = %s", (fila["id"],))
        conn.commit()
        raise Conflicto(
            "Se agotaron los intentos de ese código. Pedí uno nuevo.",
            {"motivo": "intentos_agotados"})

    if not hmac.compare_digest(fila["codigo_hash"], _hash(str(codigo or "").strip())):
        db.ejecutar(conn, "update verificacion_contacto set intentos = intentos + 1 "
                          "where id = %s", (fila["id"],))
        conn.commit()
        raise DatosInvalidos(
            "El código no coincide.",
            {"intentos_restantes": MAX_INTENTOS - fila["intentos"] - 1})

    db.ejecutar(
        conn,
        "update verificacion_contacto "
        "set estado = 'verificado', verificado_en = now() where id = %s",
        (fila["id"],))
    conn.commit()
    return {"verificacion_id": fila["id"], "canal": canal, "destino": destino,
            "estado": "verificado"}


def esta_verificado(conn, canal, destino):
    """¿Hay una verificación vigente para ese contacto?

    Vigente = verificada y todavía no consumida por otra inscripción.
    """
    try:
        canal, destino = normalizar_destino(canal, destino)
    except DatosInvalidos:
        return False
    return bool(db.una(
        conn,
        "select 1 from verificacion_contacto "
        "where canal = %s and destino = %s and estado = 'verificado'",
        (canal, destino)))


def consumir(conn, canal, destino):
    """Marca la verificación como usada por una inscripción.

    Un código verificado sirve para **una** inscripción. Sin consumirlo, el
    mismo código serviría para inscribir a diez personas distintas con el
    mismo contacto.
    """
    try:
        canal, destino = normalizar_destino(canal, destino)
    except DatosInvalidos:
        return False
    return bool(db.ejecutar(
        conn,
        "update verificacion_contacto set estado = 'usado' "
        "where id = (select id from verificacion_contacto "
        "             where canal = %s and destino = %s and estado = 'verificado' "
        "             order by verificado_en desc limit 1)",
        (canal, destino)))


def _ahora(conn):
    return db.una(conn, "select now() as ahora")["ahora"]


# ── El envío, detrás de una interfaz ─────────────────────────────────

PROVEEDORES = ("workspace", "log", "ninguno")
_VERDADERO = ("1", "si", "sí", "true", "on", "yes")


def desplegado(entorno=None):
    """¿Corre en Cloud Functions / Cloud Run de verdad?

    `K_SERVICE` lo pone el runtime desplegado; el emulador de Firebase pone
    además `FUNCTIONS_EMULATOR=true`, y ése cuenta como desarrollo.
    """
    entorno = os.environ if entorno is None else entorno
    emulador = (entorno.get("FUNCTIONS_EMULATOR") or "").strip().lower() == "true"
    return bool((entorno.get("K_SERVICE") or "").strip()) and not emulador


def modo_desarrollo(entorno=None):
    """R-MAIL.2 — ¿se puede devolver el código o el enlace en la respuesta?

    Solo con la señal **explícita** `ENVIO_MODO_DESARROLLO` encendida, y
    nunca en el entorno desplegado aunque la señal esté: esa combinación es
    exactamente el agujero que esta regla cierra, y un `.env` copiado de la
    máquina de alguien no puede reabrirlo.
    """
    entorno = os.environ if entorno is None else entorno
    pedido = (entorno.get("ENVIO_MODO_DESARROLLO") or "").strip().lower() in _VERDADERO
    return pedido and not desplegado(entorno)


def _nombre_proveedor(entorno):
    return (entorno.get("VERIFICACION_ENVIO_PROVEEDOR") or "").strip().lower() or "ninguno"


def proveedor_de_envio(entorno=None, canal=EMAIL):
    """Devuelve `enviar(canal, destino, contenido, tipo=..., **datos) -> {...}`.

    Mismo patrón que `embeddings` y `reranker`: el proveedor se elige por
    variable de entorno. Lo que cambió con R-MAIL.2 es qué pasa sin
    proveedor: en vez de devolver un enviador que «no envía», levanta
    `EnvioNoConfigurado` —salvo en modo desarrollo—. Se llama **antes** de
    mirar si el destinatario existe, así que la respuesta es la misma para
    cualquier dirección.
    """
    entorno = os.environ if entorno is None else entorno
    nombre = _nombre_proveedor(entorno)

    if nombre not in PROVEEDORES:
        raise EnvioNoConfigurado(
            f"Proveedor de envío desconocido: {nombre!r}.",
            {"proveedores": list(PROVEEDORES)})

    if nombre == "log":
        # Para desarrollo: queda en los logs del servidor, no en la respuesta.
        def por_log(canal, destino, contenido, tipo=None, **_):
            print(f"[envio] {tipo or 'mensaje'} para {canal} {destino}: {contenido}")
            return {"proveedor": "log", "enviado": True}
        return por_log

    if nombre == "workspace" and canal == EMAIL:
        from . import correo

        servidor = correo.Workspace.desde_entorno(entorno)

        def por_workspace(canal, destino, contenido, tipo=None, **datos):
            asunto, texto = correo.armar(tipo or correo.PRUEBA, contenido, **datos)
            try:
                envio = servidor.enviar(destino, asunto, texto)
            except correo.ErrorEnvio as error:
                return {"proveedor": "workspace", "enviado": False,
                        "motivo": error.motivo, "intentos": error.intentos}
            return {"proveedor": "workspace", "enviado": True, **envio}
        return por_workspace

    # `ninguno`, o `workspace` para un canal que Workspace no cubre (celular).
    if modo_desarrollo(entorno):
        def sin_proveedor(canal, destino, contenido, tipo=None, **_):
            return {"proveedor": "ninguno", "sin_proveedor": True}
        return sin_proveedor

    if nombre == "workspace":
        raise EnvioNoConfigurado(
            "Por ahora no podemos enviar códigos por SMS: el envío está "
            "configurado solo para correo. Dejá el celular vacío —lo podés "
            "agregar después— o escribinos.",
            {"canal": canal, "proveedor": nombre})
    raise EnvioNoConfigurado(
        "El envío de correos no está configurado, así que no podemos mandarte "
        "el mensaje. Es un problema nuestro, no de tus datos: avisanos o "
        "volvé a intentar más tarde.",
        {"canal": canal, "proveedor": nombre})


def enviar_y_registrar(conn, enviador, canal, destino, contenido, tipo, **datos):
    """Manda por `enviador` y deja el rastro en `envio_correo`.

    **Un fallo no se reporta como éxito** (R-MAIL.1): se registra con el
    motivo, se confirma el registro —para que sobreviva al rollback de la
    request— y se levanta `EnvioFallido`. Al usuario no le llega el motivo
    técnico: ése queda en Cumplimiento → Contacto.
    """
    resultado = enviador(canal, destino, contenido, tipo=tipo, **datos) or {}
    if resultado.get("sin_proveedor"):
        return resultado
    enviado = resultado.get("enviado", True)
    if canal == EMAIL:
        from . import correo

        correo.registrar(
            conn, tipo, destino, resultado.get("proveedor"),
            "enviado" if enviado else "fallido",
            motivo=resultado.get("motivo"),
            intentos=resultado.get("intentos") or 1,
            duracion_ms=resultado.get("ms"))
    if not enviado:
        conn.commit()
        print(f"[envio] falló un {tipo} por {resultado.get('proveedor')}: "
              f"{resultado.get('motivo')}")
        raise EnvioFallido(
            "No pudimos enviar el correo en este momento. Probá de nuevo en "
            "unos minutos.", {"motivo": "envio_fallido"})
    return resultado


def diagnostico(entorno=None, conn=None):
    """Qué tan endurecida está la landing hoy, y si el correo sale. Va a la
    pantalla de Cumplimiento, para que la falta de proveedor no sea una
    sorpresa (R-MAIL.4)."""
    entorno = os.environ if entorno is None else entorno
    nombre = _nombre_proveedor(entorno)
    hay_sal = bool(entorno.get("VERIFICACION_SAL"))
    desarrollo = modo_desarrollo(entorno)
    pedido_desarrollo = (entorno.get("ENVIO_MODO_DESARROLLO") or "").strip().lower() in _VERDADERO

    from . import correo

    smtp = None
    if nombre == "workspace":
        servidor = correo.Workspace.desde_entorno(entorno)
        smtp = {
            "host": servidor.host,
            "puerto": servidor.puerto,
            "usuario": servidor.usuario,
            "remitente": servidor.remitente,
            "reply_to": servidor.reply_to,
            # Si está, no cuál es: la contraseña no sale nunca de la función.
            "clave_configurada": bool(servidor.clave),
        }

    envios = None
    if conn is not None:
        try:
            envios = {
                "ultimas_24h": correo.enviados_ultimas_24h(conn),
                "tope_diario": correo.TOPE_DIARIO,
                "fallidos_24h": db.una(
                    conn,
                    "select count(*)::int as n from envio_correo "
                    "where estado = 'fallido' "
                    "and creado_en > now() - interval '24 hours'")["n"],
            }
        except Exception:  # noqa: BLE001 — la 0023 sin aplicar no tumba la pantalla
            conn.rollback()
            envios = None

    avisos = [
        ("Sin proveedor de envío: el portal y la landing no pueden mandar "
         "códigos ni enlaces, y lo informan como un problema de configuración. "
         "La landing no se puede anunciar así.")
        if nombre == "ninguno" else None,
        ("El proveedor es `log`: los códigos y enlaces quedan en los logs del "
         "servidor y no le llegan a nadie. Sirve para desarrollo, no para "
         "producción.") if nombre == "log" else None,
        ("Workspace configurado sin `SMTP_PASSWORD`: ningún correo va a salir.")
        if smtp and not smtp["clave_configurada"] else None,
        ("Workspace solo envía correo: la verificación por celular no tiene "
         "proveedor.") if nombre == "workspace" else None,
        ("`ENVIO_MODO_DESARROLLO` está encendido: sin proveedor, el código y el "
         "enlace vuelven en la respuesta. Nunca en un entorno con panelistas "
         "reales.") if desarrollo else None,
        ("`ENVIO_MODO_DESARROLLO` está encendido en el entorno desplegado. Se "
         "ignora, pero apagalo: no tiene que estar ahí.")
        if pedido_desarrollo and not desarrollo else None,
        (f"Se enviaron {envios['ultimas_24h']} correos en las últimas 24 horas: "
         f"cerca del tope de Workspace ({correo.TOPE_DIARIO} por día).")
        if envios and envios["ultimas_24h"] >= 0.8 * correo.TOPE_DIARIO else None,
        (f"Hubo {envios['fallidos_24h']} envío(s) fallido(s) en las últimas 24 "
         "horas: revisá el detalle abajo.")
        if envios and envios["fallidos_24h"] else None,
        ("Falta aplicar la migración boveda/0023: los envíos no se pueden "
         "registrar ni contar.") if conn is not None and envios is None else None,
        ("Sin `VERIFICACION_SAL`: los códigos y los orígenes se hashean con "
         "una sal de desarrollo, que es pública.") if not hay_sal else None,
    ]
    return {
        "proveedor_envio": nombre,
        "envia_de_verdad": nombre == "workspace" and bool(smtp and smtp["clave_configurada"]),
        "modo_desarrollo": desarrollo,
        "smtp": smtp,
        "envios": envios,
        "sal_configurada": hay_sal,
        "avisos": [a for a in avisos if a],
    }
