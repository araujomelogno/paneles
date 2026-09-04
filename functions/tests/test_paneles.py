"""R1.4 — Paneles y membresías N:M."""

import pytest

from panel_api import db, paneles, personas
from panel_api.errores import NoEncontrado

from conftest import consentimientos


def _persona(conn, documento):
    return personas.alta(
        conn,
        {
            "persona": {"documento": documento, "nombre": f"Panelista {documento}"},
            "consentimientos": consentimientos("contacto_participacion"),
        },
    )["id_persona"]


def test_una_persona_en_dos_paneles_no_se_duplica(conn_boveda):
    uno = paneles.crear(conn_boveda, "Panel Montevideo")
    dos = paneles.crear(conn_boveda, "Panel Interior")
    id_persona = _persona(conn_boveda, "1-1")

    paneles.agregar_miembro(conn_boveda, uno["id"], id_persona)
    paneles.agregar_miembro(conn_boveda, dos["id"], id_persona)

    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1
    assert db.una(conn_boveda, "select count(*)::int as n from membresia")["n"] == 2
    # La pertenencia al primero no se alteró (criterio de aceptación de R1.4).
    miembros_uno = paneles.listar_miembros(conn_boveda, uno["id"])
    assert [m["id_persona"] for m in miembros_uno] == [id_persona]
    assert miembros_uno[0]["estado"] == "activo"


def test_agregar_dos_veces_al_mismo_panel_es_idempotente(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    id_persona = _persona(conn_boveda, "2-2")

    primera = paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    segunda = paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    assert primera["id"] == segunda["id"]
    assert db.una(conn_boveda, "select count(*)::int as n from membresia")["n"] == 1


def test_baja_de_membresia_no_borra_a_la_persona_ni_su_otra_membresia(conn_boveda):
    uno = paneles.crear(conn_boveda, "Panel A")
    dos = paneles.crear(conn_boveda, "Panel B")
    id_persona = _persona(conn_boveda, "3-3")
    paneles.agregar_miembro(conn_boveda, uno["id"], id_persona)
    paneles.agregar_miembro(conn_boveda, dos["id"], id_persona)

    paneles.dar_de_baja_miembro(conn_boveda, uno["id"], id_persona)

    assert paneles.listar_miembros(conn_boveda, uno["id"], estado="activo") == []
    assert len(paneles.listar_miembros(conn_boveda, dos["id"], estado="activo")) == 1
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1


def test_reagregar_a_quien_estaba_de_baja_lo_reactiva(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    id_persona = _persona(conn_boveda, "4-4")
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    paneles.dar_de_baja_miembro(conn_boveda, panel["id"], id_persona)

    reactivada = paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    assert reactivada["estado"] == "activo"
    assert db.una(conn_boveda, "select count(*)::int as n from membresia")["n"] == 1


def test_el_alta_puede_sumar_directo_a_un_panel(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    resultado = personas.alta(
        conn_boveda,
        {
            "persona": {"documento": "5-5", "nombre": "Directo"},
            "consentimientos": consentimientos("contacto_participacion"),
            "panel_id": panel["id"],
        },
    )
    assert resultado["membresia"]["panel_id"] == panel["id"]
    assert len(paneles.listar_miembros(conn_boveda, panel["id"])) == 1


def test_panel_inexistente_da_404(conn_boveda):
    with pytest.raises(NoEncontrado):
        paneles.agregar_miembro(conn_boveda, 9999, _persona(conn_boveda, "6-6"))


def test_la_ficha_muestra_todos_los_paneles_de_la_persona(conn_boveda):
    uno = paneles.crear(conn_boveda, "Panel A")
    dos = paneles.crear(conn_boveda, "Panel B")
    id_persona = _persona(conn_boveda, "7-7")
    paneles.agregar_miembro(conn_boveda, uno["id"], id_persona)
    paneles.agregar_miembro(conn_boveda, dos["id"], id_persona)

    ficha = personas.ficha(conn_boveda, id_persona)
    assert {p["nombre"] for p in ficha["paneles"]} == {"Panel A", "Panel B"}
