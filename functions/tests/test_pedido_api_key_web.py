"""PEDIDO — La API key web fuera de Secret Manager, y el error de
`verificar_clave` en el log.

El incidente: el secreto `FIREBASE_WEB_API_KEY` tenía el placeholder `AIza...`
porque Firebase no deja cargar nada cuyo nombre empiece con `FIREBASE_`.
Identity Toolkit contestaba 400 «API key not valid», el código lo convertía
en `None` sin dejar una línea de log, y el panelista leía «contraseña
incorrecta» con cualquier contraseña. Diagnosticarlo llevó más de una hora.

El archivo sigue el Definition of Done:

* una contraseña incorrecta sigue dando el mismo mensaje genérico;
* un fallo de Identity Toolkit deja en el log el estado y el cuerpo;
* la contraseña y la key **no** aparecen en ningún registro;
* un 5xx o un timeout no se presentan como «contraseña incorrecta» ni
  cuentan para el límite de intentos.

Lo de Identity Toolkit se prueba con respuestas simuladas: se le inyecta a
`CredencialesFirebase` un `urlopen` de mentira. El resto, contra Postgres de
verdad, como todo R6.1.a.
"""

import io
import json
import socket
import urllib.error

import pytest

from panel_api import credenciales as cred, db, personas, portal
from panel_api.errores import ComprobacionNoDisponible, NoAutenticado

from conftest import consentimientos

KEY = "AIza" + "B" * 35
EMAIL = "panelista@ejemplo.invalid"
CLAVE = "la-clave-secreta-123"


class _Respuesta:
    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def read(self):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _http_error(estado, cuerpo):
    return urllib.error.HTTPError(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={KEY}",
        estado, "error", {}, io.BytesIO(json.dumps(cuerpo).encode()))


def _proveedor(respuesta=None, error=None, pedidos=None):
    """Un `CredencialesFirebase` cuyo `urlopen` contesta lo que se le diga."""
    def abrir(pedido, timeout=None):
        if pedidos is not None:
            pedidos.append(pedido)
        if error is not None:
            raise error
        return _Respuesta(json.dumps(respuesta or {}).encode())
    return cred.CredencialesFirebase(auth_fb=object(), api_key=KEY, abrir=abrir)


CREDENCIAL_INVALIDA = {"error": {"code": 400,
                                 "message": "INVALID_LOGIN_CREDENTIALS"}}
KEY_INVALIDA = {"error": {
    "code": 400, "message": "API key not valid. Please pass a valid API key.",
    "status": "INVALID_ARGUMENT",
    "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                 "reason": "API_KEY_INVALID"}]}}


# ════════════════════════════════════════════════════════════════════
#  R1 · La key es una variable común, y un placeholder no se manda
# ════════════════════════════════════════════════════════════════════

def test_la_key_se_lee_de_web_api_key(monkeypatch):
    monkeypatch.setenv("WEB_API_KEY", KEY)
    assert cred.CredencialesFirebase(auth_fb=object()).api_key == KEY


def test_el_nombre_viejo_ya_no_alcanza(monkeypatch):
    """`FIREBASE_WEB_API_KEY` no se puede cargar en Firebase; leerlo como
    alternativa solo serviría para que un valor muerto parezca vigente."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", KEY)
    with pytest.raises(ComprobacionNoDisponible, match="Falta `WEB_API_KEY`"):
        cred.CredencialesFirebase(auth_fb=object()).api_key


def test_sin_key_el_mensaje_dice_que_nadie_entra(monkeypatch, capsys):
    """El mensaje de siempre se conserva: es correcto y útil."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with pytest.raises(ComprobacionNoDisponible) as capturado:
        cred.CredencialesFirebase(auth_fb=object()).api_key
    assert "nadie entra al portal" in str(capturado.value)
    assert "sin_api_key" in capsys.readouterr().out


def test_un_placeholder_se_frena_antes_de_llamar(capsys):
    """El caso del incidente: `AIza...` en vez de la key. Se reconoce por la
    forma y no se manda, así que no puede volver como «contraseña
    incorrecta»."""
    pedidos = []
    proveedor = _proveedor(respuesta={"localId": "x"}, pedidos=pedidos)
    proveedor._api_key = "AIza..."
    with pytest.raises(ComprobacionNoDisponible, match="placeholder"):
        proveedor.verificar_clave(EMAIL, CLAVE)
    assert pedidos == []
    salida = capsys.readouterr().out
    assert "api_key_con_forma_invalida" in salida
    assert "AIza..." not in salida


# ════════════════════════════════════════════════════════════════════
#  R2 · El error de Identity Toolkit queda en el log
# ════════════════════════════════════════════════════════════════════

def test_una_credencial_incorrecta_sigue_siendo_none():
    """No regresión: la interfaz no cambia para el caso de siempre."""
    proveedor = _proveedor(error=_http_error(400, CREDENCIAL_INVALIDA))
    assert proveedor.verificar_clave(EMAIL, CLAVE) is None


def test_un_400_deja_en_el_log_el_estado_y_el_cuerpo(capsys):
    _proveedor(error=_http_error(400, CREDENCIAL_INVALIDA)).verificar_clave(
        EMAIL, CLAVE)
    salida = capsys.readouterr().out
    assert "identity_toolkit_http" in salida
    assert "estado=400" in salida
    assert "INVALID_LOGIN_CREDENTIALS" in salida
    # El correo sí: ya está en los registros de intentos.
    assert EMAIL in salida


def test_la_key_invalida_queda_en_el_log_con_su_motivo(capsys):
    """Lo que habría ahorrado la hora de diagnóstico: el cuerpo decía «API
    key not valid» desde el primer intento."""
    with pytest.raises(ComprobacionNoDisponible):
        _proveedor(error=_http_error(400, KEY_INVALIDA)).verificar_clave(
            EMAIL, CLAVE)
    assert "API key not valid" in capsys.readouterr().out


@pytest.mark.parametrize("error", [
    _http_error(400, CREDENCIAL_INVALIDA),
    _http_error(400, {"error": {"message": f"rechazada la key {KEY}"}}),
    _http_error(503, {"error": {"code": 503, "message": "UNAVAILABLE"}}),
    urllib.error.URLError(f"no route to identitytoolkit (key={KEY})"),
    socket.timeout("timed out"),
])
def test_ni_la_contrasena_ni_la_key_aparecen_en_el_log(error, capsys):
    proveedor = _proveedor(error=error)
    try:
        proveedor.verificar_clave(EMAIL, CLAVE)
    except ComprobacionNoDisponible:
        pass
    salida = capsys.readouterr().out
    assert salida, "un fallo de Identity Toolkit no puede pasar sin log"
    assert CLAVE not in salida
    assert KEY not in salida


def test_la_contrasena_tampoco_aparece_en_el_error_que_sube(capsys):
    with pytest.raises(ComprobacionNoDisponible) as capturado:
        _proveedor(error=_http_error(500, {"error": {"message": "INTERNAL"}})
                   ).verificar_clave(EMAIL, CLAVE)
    texto = f"{capturado.value} {capturado.value.detalle}"
    assert CLAVE not in texto and KEY not in texto


# ════════════════════════════════════════════════════════════════════
#  R3 · «No se pudo comprobar» no es «no entró»
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("error", [
    _http_error(403, {"error": {"code": 403, "message": "PERMISSION_DENIED"}}),
    _http_error(429, {"error": {"code": 429, "message": "RESOURCE_EXHAUSTED"}}),
    _http_error(500, {"error": {"code": 500, "message": "INTERNAL"}}),
    _http_error(503, {"error": {"code": 503, "message": "UNAVAILABLE"}}),
    _http_error(400, KEY_INVALIDA),
    urllib.error.URLError("Name or service not known"),
    socket.timeout("timed out"),
    TimeoutError("timed out"),
])
def test_lo_que_no_permite_comprobar_levanta_en_vez_de_devolver_none(error):
    with pytest.raises(ComprobacionNoDisponible):
        _proveedor(error=error).verificar_clave(EMAIL, CLAVE)


def test_una_respuesta_ilegible_tampoco_es_un_veredicto():
    def abrir(pedido, timeout=None):
        return _Respuesta(b"<html>proxy error</html>")
    proveedor = cred.CredencialesFirebase(auth_fb=object(), api_key=KEY,
                                          abrir=abrir)
    with pytest.raises(ComprobacionNoDisponible):
        proveedor.verificar_clave(EMAIL, CLAVE)


def test_la_credencial_correcta_sigue_entrando():
    assert _proveedor(respuesta={"localId": "uid-1"}).verificar_clave(
        EMAIL, CLAVE) == {"uid": "uid-1"}


# ── Contra el portal, con Postgres de verdad ─────────────────────────

def _panelista_con_clave(conn, credenciales):
    id_persona = personas.alta(
        conn,
        {"persona": {"documento": "PEDIDO-1", "nombre": "Panelista",
                     "email": EMAIL, "celular": "+59899000222"},
         "consentimientos": consentimientos("contacto_participacion",
                                            "uso_semantico")},
    )["id_persona"]
    credenciales.crear(EMAIL, CLAVE)
    return id_persona


def _intentos(conn):
    return db.una(conn, "select count(*)::int as n from acceso_portal "
                        "where motivo = 'login_fallido'")["n"]


def test_un_fallo_tecnico_no_se_presenta_como_contrasena_incorrecta(
        conn_boveda, credenciales):
    _panelista_con_clave(conn_boveda, credenciales)
    credenciales.falla = "proveedor_inalcanzable"

    with pytest.raises(ComprobacionNoDisponible) as capturado:
        portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE, credenciales)

    assert str(capturado.value) == portal.MENSAJE_COMPROBACION_NO_DISPONIBLE
    assert portal.MENSAJE_CREDENCIAL_INVALIDA not in str(capturado.value)
    assert capturado.value.status == 503


def test_un_fallo_tecnico_no_cuenta_para_el_limite_de_intentos(
        conn_boveda, credenciales):
    """Una caída del servicio no puede dejar a nadie bloqueado una hora."""
    _panelista_con_clave(conn_boveda, credenciales)
    credenciales.falla = "proveedor_respondio_error"

    for _ in range(portal.MAX_FALLOS_POR_CORREO_POR_HORA + 3):
        with pytest.raises(ComprobacionNoDisponible):
            portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE, credenciales)
    assert _intentos(conn_boveda) == 0

    # Y cuando el servicio vuelve, entra al primer intento.
    credenciales.falla = None
    salida = portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE, credenciales)
    assert salida["token_de_sesion"]


def test_el_mensaje_tecnico_no_revela_si_el_correo_existe(
        conn_boveda, credenciales):
    _panelista_con_clave(conn_boveda, credenciales)
    credenciales.falla = "proveedor_inalcanzable"
    errores = []
    for email in (EMAIL, "nadie@ejemplo.invalid"):
        with pytest.raises(ComprobacionNoDisponible) as capturado:
            portal.iniciar_sesion(conn_boveda, email, CLAVE, credenciales)
        errores.append(capturado.value)
    assert {str(e) for e in errores} == {portal.MENSAJE_COMPROBACION_NO_DISPONIBLE}
    assert {str(e.detalle) for e in errores} == {str(errores[0].detalle)}
    assert {e.status for e in errores} == {503}


def test_una_contrasena_incorrecta_sigue_dando_el_mensaje_generico(
        conn_boveda, credenciales):
    """No regresión del criterio de R6.1.b: el pedido no lo toca."""
    _panelista_con_clave(conn_boveda, credenciales)
    with pytest.raises(NoAutenticado) as capturado:
        portal.iniciar_sesion(conn_boveda, EMAIL, "otra-cosa", credenciales)
    assert str(capturado.value) == portal.MENSAJE_CREDENCIAL_INVALIDA
    assert _intentos(conn_boveda) == 1


def test_la_reautenticacion_tampoco_cuenta_un_fallo_tecnico(
        conn_boveda, credenciales):
    """Lo irreversible sigue fallando cerrado —la acción no ocurre— pero no
    por eso suma intentos."""
    id_persona = _panelista_con_clave(conn_boveda, credenciales)
    portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE, credenciales)
    credenciales.falla = "proveedor_inalcanzable"

    with pytest.raises(ComprobacionNoDisponible):
        portal.reautenticar(conn_boveda, id_persona, CLAVE, credenciales)
    assert _intentos(conn_boveda) == 0


# ════════════════════════════════════════════════════════════════════
#  A2 · Ningún `except` convierte una falla en «no existe» sin decirlo
# ════════════════════════════════════════════════════════════════════

class UserNotFoundError(Exception):
    pass


class _AuthFalso:
    UserNotFoundError = UserNotFoundError

    def __init__(self, error):
        self.error = error

    def get_user_by_email(self, email):
        raise self.error


def test_buscar_devuelve_none_solo_si_la_cuenta_no_existe():
    proveedor = cred.CredencialesFirebase(
        auth_fb=_AuthFalso(UserNotFoundError("no existe")), api_key=KEY)
    assert proveedor.buscar(EMAIL) is None


def test_buscar_no_disfraza_una_caida_de_cuenta_inexistente(capsys):
    """Antes cualquier excepción era None: Firebase caído se leía como «no
    hay cuenta» y el portal intentaba crear una."""
    proveedor = cred.CredencialesFirebase(
        auth_fb=_AuthFalso(ConnectionError("firebase caído")), api_key=KEY)
    with pytest.raises(ConnectionError):
        proveedor.buscar(EMAIL)
    assert "buscar_cuenta_fallo" in capsys.readouterr().out


def test_el_padron_tampoco_disfraza_una_caida(capsys):
    from panel_api import usuarios

    padron = usuarios.PadronFirebase.__new__(usuarios.PadronFirebase)
    padron.auth = _AuthFalso(ConnectionError("firebase caído"))
    with pytest.raises(ConnectionError):
        padron.buscar_por_email(EMAIL)
    padron.auth = _AuthFalso(UserNotFoundError("no existe"))
    assert padron.buscar_por_email(EMAIL) is None
