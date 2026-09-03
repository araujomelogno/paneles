"""Superficie de API y control de acceso por rol.

No levanta Firebase: el router es independiente del runtime a propósito.
"""

import pytest

from panel_api import auth, ruteo
from panel_api.errores import ErrorApi, NoAutenticado, NoEncontrado, SinPermiso

# Las rutas que el HANDOFF de Fase 1 exige, con su requisito.
RUTAS_DEL_HANDOFF = [
    ("POST", "/panelistas"),
    ("GET", "/panelistas/3f5b8a1e-0000-4000-8000-000000000001"),
    ("POST", "/paneles"),
    ("POST", "/paneles/1/miembros"),
    ("DELETE", "/paneles/1/miembros/3f5b8a1e-0000-4000-8000-000000000001"),
    ("POST", "/consentimientos/3f5b8a1e-0000-4000-8000-000000000001/retiro"),
    ("POST", "/encuestas"),
    ("POST", "/encuestas/1/ingesta"),
]


@pytest.mark.parametrize("metodo,camino", RUTAS_DEL_HANDOFF)
def test_todas_las_rutas_del_handoff_existen(metodo, camino):
    funcion, permiso, params = ruteo.resolver(metodo, camino)
    assert callable(funcion)


def test_ruta_inexistente_da_404():
    with pytest.raises(NoEncontrado):
        ruteo.resolver("GET", "/no-existe")


def test_metodo_equivocado_avisa_cuales_sirven():
    with pytest.raises(ErrorApi) as excepcion:
        ruteo.resolver("PUT", "/paneles")
    assert "POST" in excepcion.value.detalle["metodos"]


# ── Roles ────────────────────────────────────────────────────────────

def _actor(rol):
    return auth.Actor(uid=f"uid-{rol}", email=f"{rol}@equipos.com.uy", rol=rol)


@pytest.mark.parametrize(
    "rol,permiso,esperado",
    [
        ("admin", "enrolar", True),
        ("admin", "cumplimiento", True),
        ("operaciones", "enrolar", True),
        ("operaciones", "cumplimiento", False),
        ("analista", "enrolar", False),
        ("analista", "fieldear", True),
        ("analista", "ingestar", True),
        ("dpo", "cumplimiento", True),
        ("dpo", "gestionar_paneles", False),
        ("dpo", "leer", True),
    ],
)
def test_matriz_de_permisos(rol, permiso, esperado):
    assert _actor(rol).puede(permiso) is esperado


def test_exigir_permiso_que_no_se_tiene_aborta():
    with pytest.raises(SinPermiso):
        _actor("analista").exigir("cumplimiento")


def test_el_retiro_de_consentimiento_solo_lo_hace_cumplimiento():
    _, permiso, _ = ruteo.resolver(
        "POST", "/consentimientos/3f5b8a1e-0000-4000-8000-000000000001/retiro"
    )
    assert permiso == "cumplimiento"
    assert not _actor("operaciones").puede(permiso)
    assert _actor("dpo").puede(permiso)


# ── Autenticación ────────────────────────────────────────────────────

def test_sin_header_authorization_no_hay_actor():
    with pytest.raises(NoAutenticado):
        auth.token_de({})


def test_usuario_no_habilitado_no_entra():
    with pytest.raises(SinPermiso):
        auth.actor_de_request(
            {"Authorization": "Bearer tok"},
            verificar_token=lambda t: {"uid": "u1", "email": "x@equipos.com.uy"},
            buscar_usuario=lambda uid: {"rol": "admin", "activo": False},
        )


def test_usuario_sin_ficha_en_el_padron_no_entra():
    with pytest.raises(SinPermiso):
        auth.actor_de_request(
            {"Authorization": "Bearer tok"},
            verificar_token=lambda t: {"uid": "u1"},
            buscar_usuario=lambda uid: None,
        )


def test_token_invalido_da_401():
    def explota(token):
        raise ValueError("firma inválida")

    with pytest.raises(NoAutenticado):
        auth.actor_de_request(
            {"Authorization": "Bearer tok"},
            verificar_token=explota,
            buscar_usuario=lambda uid: {"rol": "admin"},
        )


def test_actor_valido_se_resuelve_con_su_rol():
    actor = auth.actor_de_request(
        {"Authorization": "Bearer tok"},
        verificar_token=lambda t: {"uid": "u1", "email": "ana@equipos.com.uy"},
        buscar_usuario=lambda uid: {"rol": "operaciones", "nombre": "Ana", "activo": True},
    )
    assert actor.rol == "operaciones"
    assert actor.puede("enrolar")
