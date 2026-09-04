"""Cascada de retiro de consentimiento: PII de la conn_boveda + embeddings del store semántico +
salida del muestreo."""

import pytest

from panel_api import bajas, consentimiento, db, encuestas, paneles, personas
from panel_api.errores import DatosInvalidos, NoEncontrado

from conftest import consentimientos

PREGUNTAS = [{"codigo": "P1", "texto": "¿Qué bebida consume?", "tipo": "abierta"}]
AMBAS = ("contacto_participacion", "uso_semantico")


@pytest.fixture
def panelista_con_datos(conn_boveda, conn_semantica, proveedor):
    """Un panelista enrolado, en un panel, convocado y con respuestas
    ingestadas de los dos lados."""
    panel = paneles.crear(conn_boveda, "Panel bebidas")
    id_persona = personas.alta(
        conn_boveda,
        {
            "persona": {"documento": "4.123.456-7", "nombre": "Ana Pérez",
                        "email": "ana@ej.uy"},
            "consentimientos": consentimientos(*AMBAS),
            "origen": "dooblo",
            "id_en_origen": "R-001",
        },
    )["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    encuestas.ingestar(
        conn_boveda, conn_semantica, encuesta["id"], PREGUNTAS,
        [{"id_en_origen": "R-001", "P1": "Fernet"}], proveedor=proveedor,
    )
    return {"panel": panel, "encuesta": encuesta, "id_persona": id_persona}


def _cuenta_boveda(conn, tabla, id_persona):
    return db.una(
        conn, f"select count(*)::int as n from {tabla} where id_persona = %s", (id_persona,)
    )["n"]


def _respuestas_semantica(conn, id_persona):
    return db.una(
        conn,
        """
        select count(*)::int as n
          from respuesta r join individuo i on i.id = r.individuo_id
         where i.id_persona = %s
        """,
        (str(id_persona),),
    )["n"]


def test_retiro_total_borra_pii_embeddings_y_saca_del_muestreo(
    conn_boveda, conn_semantica, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]
    assert _respuestas_semantica(conn_semantica, id_persona) == 1

    resultado = bajas.retirar(conn_boveda, id_persona, bajas.TODAS, actor="uid-dpo",
                              conn_semantica=conn_semantica)

    assert resultado["pii_borrada"]
    assert resultado["semantica"]["estado"] == "ok"
    # PII de la conn_boveda: no queda nada.
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 0
    for tabla in ("alias_origen", "membresia", "consentimiento", "participacion"):
        assert _cuenta_boveda(conn_boveda, tabla, id_persona) == 0
    # Embeddings remotos: tampoco.
    assert _respuestas_semantica(conn_semantica, id_persona) == 0
    assert db.una(
        conn_semantica, "select count(*)::int as n from individuo where id_persona = %s",
        (str(id_persona),),
    )["n"] == 0


def test_el_retiro_total_deja_lapida_auditable_sin_pii(conn_boveda, conn_semantica, panelista_con_datos):
    id_persona = panelista_con_datos["id_persona"]
    bajas.retirar(conn_boveda, id_persona, bajas.TODAS, actor="uid-dpo", conn_semantica=conn_semantica)

    lapida = db.una(
        conn_boveda, "select * from persona_borrada where id_persona = %s", (id_persona,)
    )
    assert lapida["motivo"] == "retiro_consentimiento"
    assert lapida["solicitado_por"] == "uid-dpo"
    assert lapida["borrado_semantica_en"] is not None
    # La lápida solo guarda el token opaco: ningún dato identificatorio.
    assert set(lapida) == {
        "id_persona", "motivo", "finalidad", "solicitado_por",
        "borrado_local_en", "borrado_semantica_en", "semantica_error",
    }


def test_retirar_solo_uso_semantico_borra_embeddings_y_deja_a_la_persona_en_el_panel(
    conn_boveda, conn_semantica, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]

    resultado = bajas.retirar(
        conn_boveda, id_persona, consentimiento.SEMANTICO, conn_semantica=conn_semantica
    )

    assert not resultado["pii_borrada"]
    assert _respuestas_semantica(conn_semantica, id_persona) == 0
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1
    # Sigue en el panel y sigue pudiendo ser convocada.
    assert len(paneles.listar_miembros(conn_boveda, panelista_con_datos["panel"]["id"])) == 1
    assert consentimiento.esta_vigente(conn_boveda, id_persona, consentimiento.CONTACTO)
    assert not consentimiento.esta_vigente(conn_boveda, id_persona, consentimiento.SEMANTICO)


def test_retirar_solo_contacto_lo_saca_del_muestreo_y_deja_los_embeddings(
    conn_boveda, conn_semantica, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]

    resultado = bajas.retirar(
        conn_boveda, id_persona, consentimiento.CONTACTO, conn_semantica=conn_semantica
    )

    assert resultado["membresias_dadas_de_baja"] == 1
    assert paneles.listar_miembros(conn_boveda, panelista_con_datos["panel"]["id"]) == []
    assert _respuestas_semantica(conn_semantica, id_persona) == 1
    assert consentimiento.esta_vigente(conn_boveda, id_persona, consentimiento.SEMANTICO)


def test_quien_se_dio_de_baja_no_puede_volver_a_ser_convocado(
    conn_boveda, conn_semantica, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]
    encuesta = panelista_con_datos["encuesta"]
    bajas.retirar(conn_boveda, id_persona, consentimiento.CONTACTO, conn_semantica=conn_semantica)

    resultado = encuestas.convocar(conn_boveda, encuesta["id"], ids_persona=[id_persona])
    assert resultado["convocados_total"] == 0
    assert resultado["sin_consentimiento"] == [id_persona]


def test_si_el_semantico_falla_el_retiro_en_la_boveda_igual_se_completa(
    conn_boveda, conn_semantica, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]

    # Sin conexión al store semántico: la baja conn_boveda no puede quedar esperando.
    resultado = bajas.retirar(conn_boveda, id_persona, bajas.TODAS, conn_semantica=None)

    assert resultado["pii_borrada"]
    assert resultado["semantica"]["estado"] == "pendiente"
    pendientes = bajas.pendientes_de_borrado_semantica(conn_boveda)
    assert [p["id_persona"] for p in pendientes] == [id_persona]

    # El reintento cierra el círculo.
    bajas.reintentar_borrado_semantica(conn_boveda, conn_semantica)
    assert bajas.pendientes_de_borrado_semantica(conn_boveda) == []
    assert _respuestas_semantica(conn_semantica, id_persona) == 0


def test_retiro_sobre_persona_inexistente_da_404(conn_boveda):
    with pytest.raises(NoEncontrado):
        bajas.retirar(conn_boveda, "3f5b8a1e-0000-4000-8000-000000000099")


def test_finalidad_desconocida_se_rechaza(conn_boveda, panelista_con_datos):
    with pytest.raises(DatosInvalidos):
        bajas.retirar(conn_boveda, panelista_con_datos["id_persona"], "marketing")
