"""P1 — registro de reidentificación: cada traducción `id_persona` → PII.

El diseño de dos stores se apoya en que el resultado de una consulta sea una
lista de tokens opacos. Pero el puente existe justamente para poder deshacer
eso: sin traducir el token a una persona no se convoca a nadie. Lo que hace
que el diseño se sostenga es que la traducción quede anotada.
"""

import pytest

from panel_api import auditoria, auth, personas, ruteo
from panel_api.errores import DatosInvalidos, SinPermiso

from conftest import ContextoDePrueba, consentimientos


@pytest.fixture
def gente(conn_boveda):
    ids = {}
    for nombre in ("Ana", "Beto"):
        alta = personas.alta(conn_boveda, {
            "persona": {"nombre": nombre, "email": f"{nombre.lower()}@ej.uy",
                        "celular": "099 123 456", "localidad": "Montevideo"},
            "consentimientos": consentimientos(),
        })
        ids[nombre] = alta["id_persona"]
    conn_boveda.commit()
    return ids


@pytest.fixture
def contexto(conn_boveda, proveedor):
    return ContextoDePrueba(conn_boveda, None, proveedor)


def _despachar(metodo, camino, contexto, quien, cuerpo=None, consulta=None):
    return ruteo.despachar(metodo, camino, cuerpo or {}, consulta or {}, quien, contexto)


# ── La traducción ────────────────────────────────────────────────────

def test_reidentificar_devuelve_los_datos_de_contacto_en_el_orden_pedido(
    conn_boveda, gente
):
    """El orden importa: el que llega es el del ranking de la consulta."""
    pedidos = [gente["Beto"], gente["Ana"]]
    resultado = personas.reidentificar(conn_boveda, pedidos)

    assert [i["id_persona"] for i in resultado["items"]] == pedidos
    assert [i["nombre"] for i in resultado["items"]] == ["Beto", "Ana"]
    assert resultado["items"][0]["email"] == "beto@ej.uy"
    assert resultado["no_encontrados"] == []


def test_un_id_que_ya_no_existe_no_es_un_error(conn_boveda, gente):
    """Una persona que se dio de baja después de la consulta desaparece de la
    bóveda, y eso es el sistema funcionando bien."""
    fantasma = "00000000-0000-4000-8000-000000000000"
    resultado = personas.reidentificar(conn_boveda, [gente["Ana"], fantasma])
    assert len(resultado["items"]) == 1
    assert resultado["no_encontrados"] == [fantasma]


# ── El registro ──────────────────────────────────────────────────────

def test_la_ruta_de_reidentificacion_registra_quien_a_quien_y_cuando(
    conn_boveda, contexto, gente, actor
):
    quien = actor("operaciones", uid="uid-ops", email="ops@equipos.com.uy")
    status, resultado = _despachar(
        "POST", "/reidentificacion", contexto, quien,
        {"ids_persona": [gente["Ana"], gente["Beto"]], "motivo": "convocatoria"},
    )
    assert status == 200
    assert len(resultado["items"]) == 2

    registros = auditoria.listar_reidentificaciones(conn_boveda)
    assert len(registros) == 2
    for registro in registros:
        assert registro["actor_uid"] == "uid-ops"
        assert registro["actor_email"] == "ops@equipos.com.uy"
        assert registro["motivo"] == "convocatoria"
        assert registro["creado_en"]
        assert registro["contexto"]["ruta"] == "POST /reidentificacion"
    assert {r["id_persona"] for r in registros} == set(gente.values())


def test_abrir_la_ficha_de_un_panelista_tambien_queda_registrado(
    conn_boveda, contexto, gente, actor
):
    """Abrir una ficha es ver la PII de una persona identificada: es una
    reidentificación deliberada como cualquier otra."""
    quien = actor("analista", uid="uid-analista")
    _despachar("GET", f"/panelistas/{gente['Ana']}", contexto, quien)

    registros = auditoria.listar_reidentificaciones(conn_boveda)
    assert len(registros) == 1
    assert registros[0]["id_persona"] == gente["Ana"]
    assert registros[0]["motivo"] == "ficha"
    assert registros[0]["actor_uid"] == "uid-analista"


def test_listar_panelistas_no_cuenta_como_reidentificacion(
    conn_boveda, contexto, gente, actor
):
    """Se registra la traducción deliberada de un token a una persona, no
    cada pantalla que ya venía mostrando el padrón. Si registrara el listado,
    el registro sería ruido y dejaría de servir para auditar."""
    _despachar("GET", "/panelistas", contexto, actor("analista"))
    assert auditoria.listar_reidentificaciones(conn_boveda) == []


def test_el_registro_sobrevive_a_la_baja_de_la_persona(conn_boveda, gente, actor):
    """Es el caso en que más hace falta: hay que poder demostrar quién había
    visto los datos de alguien que después pidió la baja. Por eso la tabla no
    tiene FK a `persona`."""
    auditoria.registrar_reidentificacion(
        conn_boveda, [gente["Ana"]], actor=actor("operaciones"), motivo="consulta"
    )
    conn_boveda.commit()

    from panel_api import db

    db.ejecutar(conn_boveda, "delete from persona where id_persona = %s",
                (gente["Ana"],))
    conn_boveda.commit()

    registros = auditoria.listar_reidentificaciones(conn_boveda, id_persona=gente["Ana"])
    assert len(registros) == 1


def test_el_registro_se_puede_filtrar_por_persona_y_por_actor(conn_boveda, gente, actor):
    auditoria.registrar_reidentificacion(
        conn_boveda, [gente["Ana"]], actor=actor("operaciones", uid="uid-1"),
        motivo="consulta",
    )
    auditoria.registrar_reidentificacion(
        conn_boveda, [gente["Beto"]], actor=actor("admin", uid="uid-2"),
        motivo="cumplimiento",
    )
    conn_boveda.commit()

    assert len(auditoria.listar_reidentificaciones(
        conn_boveda, id_persona=gente["Ana"])) == 1
    assert len(auditoria.listar_reidentificaciones(conn_boveda, actor_uid="uid-2")) == 1


def test_una_lista_vacia_no_registra_nada(conn_boveda, actor):
    assert auditoria.registrar_reidentificacion(
        conn_boveda, [], actor=actor("admin")
    )["registradas"] == 0


# ── Permisos ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("rol,puede", [
    ("admin", True), ("operaciones", True), ("analista", False), ("dpo", False),
])
def test_reidentificar_exige_su_propio_permiso(actor, rol, puede):
    """Deshacer la seudonimización es una capacidad aparte de leer: la tiene
    quien necesita convocar."""
    quien = actor(rol)
    assert quien.puede("reidentificar") is puede


def test_un_analista_no_puede_reidentificar_por_la_ruta(contexto, gente, actor):
    with pytest.raises(SinPermiso):
        _despachar("POST", "/reidentificacion", contexto, actor("analista"),
                   {"ids_persona": [gente["Ana"]]})


def test_el_registro_lo_lee_cumplimiento(contexto, actor):
    """Es la evidencia de que el puente entre stores se usa y se controla:
    la mira quien responde por el cumplimiento, no quien consulta."""
    _despachar("GET", "/reidentificacion", contexto, actor("dpo"))
    with pytest.raises(SinPermiso):
        _despachar("GET", "/reidentificacion", contexto, actor("analista"))


def test_reidentificar_sin_ids_se_rechaza(contexto, actor):
    with pytest.raises(DatosInvalidos):
        _despachar("POST", "/reidentificacion", contexto, actor("operaciones"), {})
