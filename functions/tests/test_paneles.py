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


def test_una_persona_en_dos_paneles_no_se_duplica(local):
    uno = paneles.crear(local, "Panel Montevideo")
    dos = paneles.crear(local, "Panel Interior")
    id_persona = _persona(local, "1-1")

    paneles.agregar_miembro(local, uno["id"], id_persona)
    paneles.agregar_miembro(local, dos["id"], id_persona)

    assert db.una(local, "select count(*)::int as n from persona")["n"] == 1
    assert db.una(local, "select count(*)::int as n from membresia")["n"] == 2
    # La pertenencia al primero no se alteró (criterio de aceptación de R1.4).
    miembros_uno = paneles.listar_miembros(local, uno["id"])
    assert [m["id_persona"] for m in miembros_uno] == [id_persona]
    assert miembros_uno[0]["estado"] == "activo"


def test_agregar_dos_veces_al_mismo_panel_es_idempotente(local):
    panel = paneles.crear(local, "Panel")
    id_persona = _persona(local, "2-2")

    primera = paneles.agregar_miembro(local, panel["id"], id_persona)
    segunda = paneles.agregar_miembro(local, panel["id"], id_persona)

    assert primera["id"] == segunda["id"]
    assert db.una(local, "select count(*)::int as n from membresia")["n"] == 1


def test_baja_de_membresia_no_borra_a_la_persona_ni_su_otra_membresia(local):
    uno = paneles.crear(local, "Panel A")
    dos = paneles.crear(local, "Panel B")
    id_persona = _persona(local, "3-3")
    paneles.agregar_miembro(local, uno["id"], id_persona)
    paneles.agregar_miembro(local, dos["id"], id_persona)

    paneles.dar_de_baja_miembro(local, uno["id"], id_persona)

    assert paneles.listar_miembros(local, uno["id"], estado="activo") == []
    assert len(paneles.listar_miembros(local, dos["id"], estado="activo")) == 1
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 1


def test_reagregar_a_quien_estaba_de_baja_lo_reactiva(local):
    panel = paneles.crear(local, "Panel")
    id_persona = _persona(local, "4-4")
    paneles.agregar_miembro(local, panel["id"], id_persona)
    paneles.dar_de_baja_miembro(local, panel["id"], id_persona)

    reactivada = paneles.agregar_miembro(local, panel["id"], id_persona)

    assert reactivada["estado"] == "activo"
    assert db.una(local, "select count(*)::int as n from membresia")["n"] == 1


def test_el_alta_puede_sumar_directo_a_un_panel(local):
    panel = paneles.crear(local, "Panel")
    resultado = personas.alta(
        local,
        {
            "persona": {"documento": "5-5", "nombre": "Directo"},
            "consentimientos": consentimientos("contacto_participacion"),
            "panel_id": panel["id"],
        },
    )
    assert resultado["membresia"]["panel_id"] == panel["id"]
    assert len(paneles.listar_miembros(local, panel["id"])) == 1


def test_panel_inexistente_da_404(local):
    with pytest.raises(NoEncontrado):
        paneles.agregar_miembro(local, 9999, _persona(local, "6-6"))


def test_la_ficha_muestra_todos_los_paneles_de_la_persona(local):
    uno = paneles.crear(local, "Panel A")
    dos = paneles.crear(local, "Panel B")
    id_persona = _persona(local, "7-7")
    paneles.agregar_miembro(local, uno["id"], id_persona)
    paneles.agregar_miembro(local, dos["id"], id_persona)

    ficha = personas.ficha(local, id_persona)
    assert {p["nombre"] for p in ficha["paneles"]} == {"Panel A", "Panel B"}
