"""R1.1 / R1.3 — Consentimiento por finalidad y el gate que lo hace valer."""

import pytest

from panel_api import consentimiento, db, encuestas, paneles, personas
from panel_api.errores import ConsentimientoFaltante, DatosInvalidos

from conftest import VERSION_TEXTO, consentimientos


def _persona(conn, finalidades=("contacto_participacion",), **campos):
    campos.setdefault("documento", campos.pop("doc", None) or "0.000.000-0")
    return personas.alta(
        conn,
        {"persona": campos, "consentimientos": consentimientos(*finalidades)},
    )["id_persona"]


def test_alta_sin_consentimiento_se_rechaza(local):
    with pytest.raises(DatosInvalidos, match="consentimiento"):
        personas.alta(local, {"persona": {"nombre": "Sin Consentir"}, "consentimientos": []})
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 0


def test_alta_sin_version_de_texto_se_rechaza(local):
    with pytest.raises(DatosInvalidos, match="version_texto"):
        personas.alta(
            local,
            {
                "persona": {"nombre": "Sin Versión"},
                "consentimientos": [{"finalidad": "contacto_participacion"}],
            },
        )
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 0


def test_las_dos_finalidades_son_independientes(local):
    id_persona = _persona(local, ("contacto_participacion",), documento="1-1")

    assert consentimiento.esta_vigente(local, id_persona, consentimiento.CONTACTO)
    assert not consentimiento.esta_vigente(local, id_persona, consentimiento.SEMANTICO)

    consentimiento.otorgar(local, id_persona, consentimiento.SEMANTICO, VERSION_TEXTO)
    assert consentimiento.esta_vigente(local, id_persona, consentimiento.SEMANTICO)


def test_el_alta_guarda_finalidad_estado_timestamp_y_version(local):
    id_persona = _persona(local, ("contacto_participacion", "uso_semantico"), documento="2-2")
    filas = consentimiento.listar(local, id_persona)

    assert {f["finalidad"] for f in filas} == {"contacto_participacion", "uso_semantico"}
    for fila in filas:
        assert fila["estado"] == "vigente"
        assert fila["version_texto"] == VERSION_TEXTO
        assert fila["otorgado_en"]


def test_reotorgar_agrega_fila_y_conserva_el_historial(local):
    id_persona = _persona(local, ("contacto_participacion",), documento="3-3")
    consentimiento.marcar_retirado(local, id_persona, consentimiento.CONTACTO)
    consentimiento.otorgar(local, id_persona, consentimiento.CONTACTO, "consentimiento-2026-02")

    historial = consentimiento.listar(local, id_persona)
    assert len(historial) == 2
    assert {f["estado"] for f in historial} == {"vigente", "retirado"}
    assert consentimiento.esta_vigente(local, id_persona, consentimiento.CONTACTO)


def test_exigir_aborta_cuando_falta_la_finalidad(local):
    id_persona = _persona(local, ("contacto_participacion",), documento="4-4")

    consentimiento.exigir(local, id_persona, consentimiento.CONTACTO)  # no lanza
    with pytest.raises(ConsentimientoFaltante) as excepcion:
        consentimiento.exigir(local, id_persona, consentimiento.SEMANTICO)
    assert excepcion.value.detalle["finalidad"] == "uso_semantico"


def test_finalidad_desconocida_se_rechaza(local):
    id_persona = _persona(local, ("contacto_participacion",), documento="5-5")
    with pytest.raises(DatosInvalidos):
        consentimiento.otorgar(local, id_persona, "marketing", VERSION_TEXTO)


def test_gate_de_convocatoria_deja_afuera_a_quien_no_consintio_contacto(local):
    panel = paneles.crear(local, "Panel nacional")
    con_contacto = _persona(local, ("contacto_participacion",), documento="6-6")
    solo_semantico = _persona(local, ("uso_semantico",), documento="7-7")
    for id_persona in (con_contacto, solo_semantico):
        paneles.agregar_miembro(local, panel["id"], id_persona)

    encuesta = encuestas.crear(local, panel["id"], "Ola 1")
    resultado = encuestas.convocar(local, encuesta["id"], todo_el_panel=True)

    assert resultado["convocados_total"] == 1
    assert resultado["sin_consentimiento"] == [solo_semantico]
    participantes = {p["id_persona"] for p in encuestas.listar_participacion(local, encuesta["id"])}
    assert participantes == {con_contacto}


def test_filtrar_con_consentimiento_parte_la_lista(local):
    con = _persona(local, ("uso_semantico",), documento="8-8")
    sin = _persona(local, ("contacto_participacion",), documento="9-9")

    habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        local, [con, sin], consentimiento.SEMANTICO
    )
    assert habilitadas == [con]
    assert bloqueadas == [sin]
