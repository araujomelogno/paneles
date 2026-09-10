"""R2.1 y R2.6 — participación por ola y tablero de salud del panel."""

import pytest

from panel_api import db, encuestas, paneles, participacion, personas
from panel_api.errores import NoEncontrado

from conftest import consentimientos


@pytest.fixture
def panel_con_olas(conn_boveda):
    """Tres miembros y dos olas, con historial desparejo a propósito:

        Ana   convocada a las dos, respondió las dos
        Bea   convocada a una, no respondió
        Caro  nunca convocada
    """
    panel = paneles.crear(conn_boveda, "Panel Nacional")
    ids = {}
    for nombre in ("Ana", "Bea", "Caro"):
        alta = personas.alta(conn_boveda, {
            "persona": {"nombre": nombre, "sexo": "F", "localidad": "Montevideo",
                        "email": f"{nombre.lower()}@ej.uy"},
            "consentimientos": consentimientos(),
            "panel_id": panel["id"],
        })
        ids[nombre] = alta["id_persona"]

    ola1 = encuestas.crear(conn_boveda, panel["id"], "Ola 1", "2026-01-15")
    encuestas.convocar(conn_boveda, ola1["id"], [ids["Ana"], ids["Bea"]])
    encuestas.marcar_respuesta(conn_boveda, ola1["id"], [ids["Ana"]])

    ola2 = encuestas.crear(conn_boveda, panel["id"], "Ola 2", "2026-06-15")
    encuestas.convocar(conn_boveda, ola2["id"], [ids["Ana"]])
    encuestas.marcar_respuesta(conn_boveda, ola2["id"], [ids["Ana"]])
    conn_boveda.commit()
    return {"panel": panel, "ids": ids, "olas": [ola1, ola2]}


# ── R2.1: participación por ola ──────────────────────────────────────

def test_la_participacion_por_ola_consolida_lo_que_la_fase_1_captura(
    conn_boveda, panel_con_olas
):
    """R2.1 no agrega dato nuevo: expone el que ya estaba en `participacion`."""
    olas = participacion.por_ola(conn_boveda, panel_con_olas["panel"]["id"])
    por_nombre = {o["nombre"]: o for o in olas}

    assert por_nombre["Ola 1"]["convocados"] == 2
    assert por_nombre["Ola 1"]["respondieron"] == 1
    assert por_nombre["Ola 1"]["tasa_respuesta"] == 0.5
    assert por_nombre["Ola 2"]["tasa_respuesta"] == 1.0
    assert por_nombre["Ola 1"]["calidad_pendiente"] == 2
    assert por_nombre["Ola 1"]["ref_estudio"] == panel_con_olas["olas"][0]["ref_estudio"]


def test_una_ola_sin_convocar_a_nadie_no_da_division_por_cero(conn_boveda):
    panel = paneles.crear(conn_boveda, "Vacío")
    encuestas.crear(conn_boveda, panel["id"], "Ola sin convocatoria")
    conn_boveda.commit()
    ola = participacion.por_ola(conn_boveda, panel["id"])[0]
    assert ola["convocados"] == 0
    assert ola["tasa_respuesta"] is None


# ── R2.6: tablero ────────────────────────────────────────────────────

def test_el_tablero_muestra_tasa_de_respuesta_ultimo_contacto_y_convocatorias(
    conn_boveda, panel_con_olas
):
    """DoD: «tablero de participación muestra tasa de respuesta, último
    contacto y distribución de convocatorias»."""
    tablero = participacion.tablero(conn_boveda, panel_con_olas["panel"]["id"])

    # Tasa de respuesta.
    assert tablero["respuesta"]["convocatorias_emitidas"] == 3
    assert tablero["respuesta"]["respuestas"] == 2
    assert tablero["respuesta"]["tasa_respuesta"] == pytest.approx(2 / 3, abs=1e-4)
    assert tablero["respuesta"]["miembros_nunca_convocados"] == 1

    # Último contacto.
    distribucion = tablero["ultimo_contacto"]["distribucion"]
    assert distribucion["nunca"] == 1                 # Caro
    assert distribucion["hasta_30_dias"] == 2         # Ana y Bea, recién convocadas
    assert [p["nombre"] for p in tablero["ultimo_contacto"]["nunca_contactados"]] == \
        ["Caro"]

    # Distribución de convocatorias.
    convocatorias = tablero["convocatorias"]["distribucion"]
    assert convocatorias["0"] == 1                    # Caro
    assert convocatorias["1"] == 1                    # Bea
    assert convocatorias["2"] == 1                    # Ana
    assert tablero["convocatorias"]["maximo"] == 2
    assert tablero["convocatorias"]["promedio_por_miembro"] == 1.0


def test_el_tablero_incluye_a_quien_nunca_fue_convocado(conn_boveda, panel_con_olas):
    """Es el caso que el tablero está para encontrar, y el que un `inner
    join` haría invisible."""
    tablero = participacion.tablero(conn_boveda, panel_con_olas["panel"]["id"])
    caro = next(
        p for p in tablero["ultimo_contacto"]["nunca_contactados"]
        if p["nombre"] == "Caro"
    )
    assert caro["convocatorias"] == 0
    assert caro["ultimo_contacto"] is None
    assert caro["dias_sin_contacto"] is None
    assert caro["tramo_contacto"] == "nunca"


def test_la_concentracion_avisa_cuando_pocos_se_comen_las_convocatorias(
    conn_boveda, panel_con_olas
):
    """Si el decil más convocado se come la mayor parte de las convocatorias,
    la muestra no es del panel: es de ese pedacito."""
    tablero = participacion.tablero(conn_boveda, panel_con_olas["panel"]["id"])
    # 3 miembros → el decil superior es 1 persona, y Ana tiene 2 de las 3.
    assert tablero["convocatorias"]["concentracion_decil_superior"] == \
        pytest.approx(2 / 3, abs=1e-4)
    assert tablero["convocatorias"]["mas_convocados"][0]["nombre"] == "Ana"


def test_el_tablero_cuenta_solo_las_olas_de_su_propio_panel(conn_boveda, panel_con_olas):
    """Una persona puede estar en varios paneles. Sus convocatorias en otro
    panel no son historial de este."""
    otro = paneles.crear(conn_boveda, "Otro panel")
    paneles.agregar_miembro(conn_boveda, otro["id"], panel_con_olas["ids"]["Ana"])
    ola = encuestas.crear(conn_boveda, otro["id"], "Ola del otro panel")
    encuestas.convocar(conn_boveda, ola["id"], [panel_con_olas["ids"]["Ana"]])
    conn_boveda.commit()

    tablero = participacion.tablero(conn_boveda, panel_con_olas["panel"]["id"])
    ana = next(
        p for p in tablero["convocatorias"]["mas_convocados"] if p["nombre"] == "Ana"
    )
    assert ana["convocatorias"] == 2, "no tiene que sumar la del otro panel"


def test_el_tramo_de_contacto_se_calcula_con_los_dias_reales(
    conn_boveda, panel_con_olas
):
    """Se envejece una convocatoria a mano para verificar los tramos: sin
    esto, en un test recién sembrado todo cae en «hasta 30 días»."""
    db.ejecutar(
        conn_boveda,
        "update participacion set convocado_en = now() - interval '200 days' "
        "where id_persona = %s",
        (panel_con_olas["ids"]["Bea"],),
    )
    conn_boveda.commit()
    tablero = participacion.tablero(conn_boveda, panel_con_olas["panel"]["id"])
    assert tablero["ultimo_contacto"]["distribucion"]["mas_de_180_dias"] == 1
    dormido = tablero["ultimo_contacto"]["mas_dormidos"][0]
    assert dormido["nombre"] == "Bea"
    assert dormido["dias_sin_contacto"] >= 200


def test_el_tablero_de_un_panel_que_no_existe_da_404(conn_boveda):
    with pytest.raises(NoEncontrado):
        participacion.tablero(conn_boveda, 9999)
