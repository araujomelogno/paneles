"""SPEC_envio_correo_workspace — el correo sale de verdad, y el «modo
desarrollo» deja de estar al alcance del sitio público.

El SMTP de Google Workspace se simula con un servidor de mentira que imita la
interfaz de `smtplib` (`login`, `send_message`, `quit`) y puede fallar como
falla el de verdad. Lo que se prueba es lo nuestro: qué se manda, desde
quién, cuántas veces se reintenta, qué se le contesta al usuario y qué queda
registrado.
"""

import pathlib
import re
import smtplib
import socket

import pytest

from panel_api import (
    bajas, correo, personas, portal, ruteo, usuarios,
    verificacion_contacto as verif,
)
from panel_api.errores import EnvioFallido, EnvioNoConfigurado, SinPermiso

from conftest import consentimientos

RAIZ = pathlib.Path(__file__).resolve().parents[2]
EMAIL = "panelista@ejemplo.invalid"
CLAVE_APP = "abcd efgh ijkl mnop"
WORKSPACE = {
    "VERIFICACION_ENVIO_PROVEEDOR": "workspace",
    "SMTP_PASSWORD": CLAVE_APP,
    "SMTP_REPLY_TO": "panel@equipos.com.uy",
}


# ── El servidor SMTP de mentira ──────────────────────────────────────

class Servidor:
    """Imita a smtplib. `fallas` es una lista de excepciones que se levantan
    en orden, una por intento; vacía, entrega."""

    def __init__(self, fallas=()):
        self.fallas = list(fallas)
        self.entregados = []
        self.conexiones = []

    def __call__(self, host, puerto, timeout, con_ssl):
        self.conexiones.append((host, puerto, timeout, con_ssl))
        servidor = self

        class Conexion:
            def login(self, usuario, clave):
                servidor.login = (usuario, clave)

            def send_message(self, msg):
                if servidor.fallas:
                    raise servidor.fallas.pop(0)
                servidor.entregados.append(msg)

            def quit(self):
                pass

        return Conexion()


def _workspace(servidor, **kw):
    kw.setdefault("reply_to", "panel@equipos.com.uy")
    return correo.Workspace(CLAVE_APP, smtp=servidor, esperar=lambda s: None, **kw)


def _panelista(conn, email=EMAIL, documento="MAIL-1"):
    return personas.alta(conn, {
        "persona": {"documento": documento, "nombre": "Panelista de prueba",
                    "email": email},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico"),
    })["id_persona"]


def _enviador_workspace(servidor):
    """El enviador real de `proveedor_de_envio`, con el SMTP simulado."""
    original = correo.Workspace.desde_entorno

    def con_servidor(entorno=None):
        w = original(entorno)
        w._smtp = servidor
        w._esperar = lambda s: None
        return w

    return con_servidor


@pytest.fixture
def con_workspace(monkeypatch):
    for clave, valor in WORKSPACE.items():
        monkeypatch.setenv(clave, valor)
    servidor = Servidor()
    monkeypatch.setattr(correo.Workspace, "desde_entorno",
                        staticmethod(_enviador_workspace(servidor)))
    return servidor


@pytest.fixture
def sin_modo_desarrollo(monkeypatch):
    monkeypatch.delenv("ENVIO_MODO_DESARROLLO", raising=False)
    monkeypatch.delenv("VERIFICACION_ENVIO_PROVEEDOR", raising=False)


# ════════════════════════════════════════════════════════════════════
#  R-MAIL.1 — el proveedor SMTP
# ════════════════════════════════════════════════════════════════════

def test_el_correo_sale_desde_notificaciones_con_reply_to():
    servidor = Servidor()
    _workspace(servidor).enviar(EMAIL, *correo.armar(correo.ALTA_CLAVE, "https://x/clave?t=1"))

    (msg,) = servidor.entregados
    assert msg["From"] == "Equipos Consultores <notificaciones@equipos.com.uy>"
    assert msg["Reply-To"] == "panel@equipos.com.uy"
    assert msg["To"] == EMAIL
    assert "contraseña" in msg["Subject"].lower()
    assert msg["Message-ID"].endswith("@equipos.com.uy>")
    assert servidor.login == ("notificaciones@equipos.com.uy", CLAVE_APP)
    assert servidor.conexiones[0][:2] == ("smtp.gmail.com", 587)


def test_el_puerto_465_va_por_ssl():
    servidor = Servidor()
    _workspace(servidor, puerto=465).enviar(EMAIL, "a", "b")
    assert servidor.conexiones[0][3] is True


def test_cada_tipo_de_correo_tiene_su_plantilla():
    asuntos = {tipo: correo.armar(tipo, "CONTENIDO")[0] for tipo in correo.TIPOS}
    assert len(set(asuntos.values())) == len(correo.TIPOS), "un asunto por tipo"
    for tipo in (correo.ALTA_CLAVE, correo.RECUPERACION_CLAVE, correo.ACCESO_USUARIO):
        assert "CONTENIDO" in correo.armar(tipo, "CONTENIDO")[1], "el enlace va en el cuerpo"
    asunto, cuerpo = correo.armar(correo.CODIGO_VERIFICACION, "123456")
    assert "123456" in asunto and "Equipos Consultores" in cuerpo


def test_un_fallo_transitorio_se_reintenta_y_despues_sale():
    servidor = Servidor([smtplib.SMTPServerDisconnected("cortado"),
                         socket.timeout("lento")])
    resultado = _workspace(servidor).enviar(EMAIL, "a", "b")
    assert resultado["intentos"] == 3 and len(servidor.entregados) == 1


def test_un_fallo_transitorio_persistente_se_da_por_fallido_acotado():
    servidor = Servidor([smtplib.SMTPServerDisconnected("x")] * 10)
    with pytest.raises(correo.ErrorEnvio) as error:
        _workspace(servidor).enviar(EMAIL, "a", "b")
    assert error.value.transitorio is True
    assert error.value.intentos == 1 + correo.REINTENTOS


def test_un_fallo_permanente_no_se_reintenta():
    servidor = Servidor([smtplib.SMTPAuthenticationError(535, b"bad credentials")])
    with pytest.raises(correo.ErrorEnvio) as error:
        _workspace(servidor).enviar(EMAIL, "a", "b")
    assert error.value.intentos == 1 and not error.value.transitorio
    assert "contraseña de aplicación" in error.value.motivo
    assert CLAVE_APP not in error.value.motivo


def test_el_envio_no_bloquea_mas_que_su_presupuesto():
    reloj = {"t": 0.0}

    class Lento(Servidor):
        def __call__(self, host, puerto, timeout, con_ssl):
            assert timeout <= 6 + 1e-9
            reloj["t"] += timeout
            raise socket.timeout("lento")

    w = correo.Workspace(CLAVE_APP, smtp=Lento(), presupuesto=12, tiempo=6,
                         reloj=lambda: reloj["t"], esperar=lambda s: None)
    with pytest.raises(correo.ErrorEnvio):
        w.enviar(EMAIL, "a", "b")
    assert reloj["t"] <= 12


def test_sin_smtp_password_no_intenta():
    w = correo.Workspace("", smtp=Servidor())
    with pytest.raises(correo.ErrorEnvio, match="SMTP_PASSWORD"):
        w.enviar(EMAIL, "a", "b")


# ════════════════════════════════════════════════════════════════════
#  R-MAIL.2 — el modo desarrollo deja de existir en el sitio público
# ════════════════════════════════════════════════════════════════════

PAGINAS_PUBLICAS = [
    RAIZ / "web" / "public" / "portal.html",
    RAIZ / "web" / "public" / "inscribirse.html",
    RAIZ / "web" / "public" / "js" / "portal.js",
]


@pytest.mark.parametrize("pagina", PAGINAS_PUBLICAS, ids=lambda p: p.name)
def test_el_modo_desarrollo_no_aparece_en_el_sitio_publico(pagina):
    """Ninguna página pública lee `enlace_sin_enviar` ni `codigo_sin_enviar`:
    aunque el servidor los devolviera, no hay dónde mostrarlos."""
    texto = pagina.read_text(encoding="utf-8")
    assert "sin_enviar" not in texto
    assert not re.search(r"modo (desarrollo|prueba)", texto, re.I)


def test_sin_proveedor_el_portal_informa_y_no_ofrece_el_atajo(
        conn_boveda, sin_modo_desarrollo):
    _panelista(conn_boveda)
    for email in (EMAIL, "no-existe@ejemplo.invalid"):
        with pytest.raises(EnvioNoConfigurado) as error:
            portal.pedir_enlace_de_clave(conn_boveda, email)
        # La misma respuesta exista o no la dirección: el error es de
        # configuración y se decide antes de mirar el padrón.
        assert error.value.status == 503
        assert "configurado" in error.value.mensaje
    emitidos = conn_boveda.execute(
        "select count(*) as n from acceso_portal where token_hash is not null").fetchone()["n"]
    assert emitidos == 0, "sin proveedor no se emite un enlace que nadie va a recibir"


def test_sin_proveedor_la_landing_no_devuelve_el_codigo(conn_boveda, sin_modo_desarrollo):
    with pytest.raises(EnvioNoConfigurado):
        verif.pedir_codigo(conn_boveda, verif.EMAIL, "alguien@ejemplo.invalid")


def test_por_la_ruta_publica_sin_proveedor_no_vuelve_el_enlace(ctx, conn_boveda,
                                                                sin_modo_desarrollo):
    from panel_api.auth import Actor

    _panelista(conn_boveda)
    publico = Actor(uid=None, email=None, rol=None, nombre="público")
    with pytest.raises(EnvioNoConfigurado):
        ruteo.despachar("POST", "/portal/clave/enlace", {"email": EMAIL}, {}, publico, ctx)


def test_el_modo_desarrollo_es_una_senal_explicita_y_no_la_falta_de_proveedor():
    assert verif.modo_desarrollo({}) is False
    assert verif.modo_desarrollo({"VERIFICACION_ENVIO_PROVEEDOR": "ninguno"}) is False
    assert verif.modo_desarrollo({"ENVIO_MODO_DESARROLLO": "1"}) is True


def test_en_el_entorno_desplegado_el_modo_desarrollo_se_ignora():
    desplegado = {"ENVIO_MODO_DESARROLLO": "1", "K_SERVICE": "api"}
    assert verif.modo_desarrollo(desplegado) is False
    with pytest.raises(EnvioNoConfigurado):
        verif.proveedor_de_envio(desplegado)
    emulador = {**desplegado, "FUNCTIONS_EMULATOR": "true"}
    assert verif.modo_desarrollo(emulador) is True


def test_con_modo_desarrollo_explicito_el_enlace_vuelve(conn_boveda, monkeypatch):
    monkeypatch.setenv("ENVIO_MODO_DESARROLLO", "1")
    monkeypatch.delenv("VERIFICACION_ENVIO_PROVEEDOR", raising=False)
    _panelista(conn_boveda)
    salida = portal.pedir_enlace_de_clave(conn_boveda, EMAIL)
    assert "enlace_sin_enviar" in salida


def test_ninguno_y_log_siguen_existiendo(capsys):
    enviar = verif.proveedor_de_envio({"VERIFICACION_ENVIO_PROVEEDOR": "log"})
    assert enviar("email", EMAIL, "123", tipo="codigo_verificacion")["enviado"]
    assert "123" in capsys.readouterr().out
    enviar = verif.proveedor_de_envio({"VERIFICACION_ENVIO_PROVEEDOR": "ninguno",
                                       "ENVIO_MODO_DESARROLLO": "1"})
    assert enviar("email", EMAIL, "123")["sin_proveedor"]


# ════════════════════════════════════════════════════════════════════
#  R-MAIL.1/3 — portal, landing y alta de usuarios, por Workspace
# ════════════════════════════════════════════════════════════════════

def test_crear_la_contrasena_manda_el_correo_y_no_devuelve_el_enlace(
        conn_boveda, con_workspace):
    _panelista(conn_boveda)
    salida = portal.pedir_enlace_de_clave(conn_boveda, EMAIL, motivo=portal.ALTA_CLAVE)

    assert "enlace_sin_enviar" not in salida
    assert salida["mensaje"] == portal.RESPUESTA_DE_ENLACE
    (msg,) = con_workspace.entregados
    assert msg["To"] == EMAIL and "Creá tu contraseña" in msg["Subject"]
    cuerpo = msg.get_content()
    assert "https://portal.ejemplo.invalid/clave?t=" in cuerpo, (
        "el enlace apunta al dominio del portal (PORTAL_URL)")
    fila = conn_boveda.execute("select * from envio_correo").fetchone()
    assert (fila["tipo"], fila["estado"], fila["proveedor"]) == (
        "alta_clave", "enviado", "workspace")
    assert "clave?t=" not in repr(fila), "el registro no guarda el enlace"


def test_olvide_mi_contrasena_usa_su_propia_plantilla(conn_boveda, con_workspace):
    _panelista(conn_boveda)
    portal.pedir_enlace_de_clave(conn_boveda, EMAIL, motivo=portal.RECUPERACION)
    assert "Recuperá" in con_workspace.entregados[0]["Subject"]


def test_un_correo_inexistente_no_manda_nada_y_contesta_igual(conn_boveda, con_workspace):
    salida = portal.pedir_enlace_de_clave(conn_boveda, "nadie@ejemplo.invalid")
    assert salida["mensaje"] == portal.RESPUESTA_DE_ENLACE
    assert con_workspace.entregados == []


def test_la_landing_recibe_su_codigo_por_correo(conn_boveda, con_workspace):
    salida = verif.pedir_codigo(conn_boveda, verif.EMAIL, "nueva@ejemplo.invalid")
    assert "codigo_sin_enviar" not in salida
    assert salida["enviado_por"] == "workspace"
    (msg,) = con_workspace.entregados
    codigo = re.search(r"\b(\d{6})\b", msg["Subject"]).group(1)
    assert verif.verificar(conn_boveda, verif.EMAIL, "nueva@ejemplo.invalid",
                           codigo)["estado"] == "verificado"


def test_workspace_no_cubre_el_celular_y_lo_dice(conn_boveda, con_workspace,
                                                 sin_modo_desarrollo, monkeypatch):
    for clave, valor in WORKSPACE.items():
        monkeypatch.setenv(clave, valor)
    with pytest.raises(EnvioNoConfigurado, match="SMS"):
        verif.pedir_codigo(conn_boveda, verif.CELULAR, "099123456")


def test_un_fallo_de_envio_no_reporta_exito_y_queda_registrado(
        conn_boveda, con_workspace):
    _panelista(conn_boveda)
    con_workspace.fallas = [smtplib.SMTPAuthenticationError(535, b"bad")]

    with pytest.raises(EnvioFallido) as error:
        portal.pedir_enlace_de_clave(conn_boveda, EMAIL)
    assert error.value.status == 503
    assert "No pudimos enviar" in error.value.mensaje
    assert "535" not in error.value.mensaje, "el motivo técnico no va al usuario"

    conn_boveda.rollback()  # lo que haría la request al fallar
    fila = conn_boveda.execute(
        "select * from envio_correo where estado = 'fallido'").fetchone()
    assert fila and fila["destinatario"] == EMAIL and "535" in fila["motivo"]
    assert CLAVE_APP not in fila["motivo"]


def test_un_fallo_en_la_landing_tampoco_reporta_exito(conn_boveda, con_workspace):
    con_workspace.fallas = [smtplib.SMTPRecipientsRefused({"x@y": (550, b"no")})]
    with pytest.raises(EnvioFallido):
        verif.pedir_codigo(conn_boveda, verif.EMAIL, "x@ejemplo.invalid")


def test_el_alta_de_un_usuario_interno_manda_su_enlace(conn_boveda, con_workspace, actor):
    padron = usuarios.PadronEnMemoria()
    salida = usuarios.alta(conn_boveda, padron,
                           {"email": "nuevo@equipos.com.uy", "rol": "analista"},
                           actor("admin"))
    assert salida["acceso"]["correo"]["enviado"] is True
    assert con_workspace.entregados[0]["Subject"] == correo.armar(correo.ACCESO_USUARIO, "")[0]


def test_sin_proveedor_el_alta_de_usuario_igual_funciona(conn_boveda, actor,
                                                         sin_modo_desarrollo):
    padron = usuarios.PadronEnMemoria()
    salida = usuarios.alta(conn_boveda, padron,
                           {"email": "otro@equipos.com.uy", "rol": "analista"},
                           actor("admin"))
    assert salida["acceso"]["link"]
    assert salida["acceso"]["correo"]["enviado"] is False


# ════════════════════════════════════════════════════════════════════
#  R-MAIL.4 — diagnóstico, correo de prueba y envíos fallidos
# ════════════════════════════════════════════════════════════════════

def test_el_diagnostico_dice_si_el_proveedor_esta_configurado(conn_boveda, con_workspace):
    estado = verif.diagnostico(conn=conn_boveda)
    assert estado["proveedor_envio"] == "workspace"
    assert estado["envia_de_verdad"] is True
    assert estado["smtp"]["clave_configurada"] is True
    assert CLAVE_APP not in repr(estado), "la contraseña no sale nunca"
    assert estado["envios"]["tope_diario"] == 2000


def test_el_diagnostico_sin_proveedor_avisa(conn_boveda, sin_modo_desarrollo):
    estado = verif.diagnostico(conn=conn_boveda)
    assert estado["envia_de_verdad"] is False
    assert any("Sin proveedor" in a for a in estado["avisos"])


def test_el_correo_de_prueba_funciona_desde_cumplimiento(ctx, conn_boveda, con_workspace,
                                                         actor):
    status, salida = ruteo.despachar(
        "POST", "/diagnostico/contacto/correo-prueba",
        {"destino": "dpo@equipos.com.uy"}, {}, actor("dpo"), ctx)
    assert status == 200 and salida["enviado"] is True
    assert con_workspace.entregados[0]["To"] == "dpo@equipos.com.uy"

    con_workspace.fallas = [OSError("Network is unreachable")] * 5
    status, salida = ruteo.despachar(
        "POST", "/diagnostico/contacto/correo-prueba",
        {"destino": "dpo@equipos.com.uy"}, {}, actor("admin"), ctx)
    assert salida["enviado"] is False
    assert "No se pudo llegar" in salida["motivo"], "a quien prueba sí se le dice por qué"


def test_el_correo_de_prueba_no_es_para_cualquiera(ctx, actor, con_workspace):
    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", "/diagnostico/contacto/correo-prueba",
                        {"destino": "x@ejemplo.invalid"}, {}, actor("analista"), ctx)


def test_los_envios_fallidos_quedan_consultables(ctx, conn_boveda, con_workspace, actor):
    _panelista(conn_boveda)
    con_workspace.fallas = [smtplib.SMTPAuthenticationError(535, b"bad")]
    with pytest.raises(EnvioFallido):
        portal.pedir_enlace_de_clave(conn_boveda, EMAIL)

    status, salida = ruteo.despachar(
        "GET", "/diagnostico/contacto/envios", {}, {}, actor("dpo"), ctx)
    (fallido,) = salida["items"]
    assert fallido["destinatario"] == EMAIL
    assert fallido["creado_en"] and "535" in fallido["motivo"]
    assert salida["por_dia"][0]["fallidos"] == 1


def test_se_cuentan_los_correos_por_dia(conn_boveda, con_workspace):
    for i in range(3):
        verif.pedir_codigo(conn_boveda, verif.EMAIL, f"n{i}@ejemplo.invalid")
    assert correo.enviados_ultimas_24h(conn_boveda) == 3
    assert correo.por_dia(conn_boveda)[0]["enviados"] == 3


def test_una_baja_borra_el_rastro_de_envios(conn_boveda, con_workspace):
    id_persona = _panelista(conn_boveda)
    portal.pedir_enlace_de_clave(conn_boveda, EMAIL)
    assert correo.listar(conn_boveda)
    bajas.retirar(conn_boveda, id_persona)
    assert correo.listar(conn_boveda) == []


def test_la_contrasena_de_aplicacion_no_esta_en_el_repositorio():
    """Auditoría del DoD: ningún archivo le asigna un valor a SMTP_PASSWORD."""
    asignacion = re.compile(r"SMTP_PASSWORD\s*[=:]\s*['\"]?[A-Za-z0-9]{4}")
    for archivo in RAIZ.rglob("*"):
        if (not archivo.is_file() or ".git" in archivo.parts
                or "node_modules" in archivo.parts
                or archivo.suffix not in (".py", ".js", ".json", ".env", ".md",
                                          ".sh", ".yaml", ".yml", ".html", "")):
            continue
        if archivo.name == pathlib.Path(__file__).name:
            continue
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        assert not asignacion.search(texto), f"{archivo} parece tener la contraseña"
