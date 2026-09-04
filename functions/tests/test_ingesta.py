"""R1.5 — Fielding, ingesta y vínculo de respuestas con `id_persona`.

Corre contra los dos stores reales: el conn_boveda (conn_boveda) y el store semántico (pgvector).
"""

import pytest

from panel_api import db, encuestas, ingesta, paneles, personas, semantica

from conftest import consentimientos

PREGUNTAS = [
    {
        "codigo": "P1",
        "texto": "¿Qué bebida consume habitualmente?",
        "tipo": "cerrada",
        "opciones": {"1": "Fernet", "2": "Whisky", "3": "Cerveza"},
        "orden": 1,
    },
    {"codigo": "P2", "texto": "¿Por qué la elige?", "tipo": "abierta", "orden": 2},
]

FILAS = [
    {"id_en_origen": "R-001", "P1": "1", "P2": "Porque es amargo"},
    {"id_en_origen": "R-002", "P1": "3", "P2": ""},
]

AMBAS = ("contacto_participacion", "uso_semantico")


def _panelista(conn, documento, id_en_origen, finalidades=AMBAS):
    return personas.alta(
        conn,
        {
            "persona": {"documento": documento, "nombre": f"Panelista {documento}"},
            "consentimientos": consentimientos(*finalidades),
            "origen": "dooblo",
            "id_en_origen": id_en_origen,
        },
    )["id_persona"]


@pytest.fixture
def ola(conn_boveda):
    """Un panel con dos panelistas convocados a una encuesta."""
    panel = paneles.crear(conn_boveda, "Panel bebidas")
    ana = _panelista(conn_boveda, "1-1", "R-001")
    beto = _panelista(conn_boveda, "2-2", "R-002")
    for id_persona in (ana, beto):
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1 bebidas", "2026-03-01")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    return {"panel": panel, "encuesta": encuesta, "ana": ana, "beto": beto}


def _respuestas_semantica(conn_semantica, ref_estudio):
    return db.todas(
        conn_semantica,
        """
        select i.id_persona, p.codigo, r.valor_texto, r.texto_embebido
          from respuesta r
          join individuo i on i.id = r.individuo_id
          join pregunta p on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where c.ref_estudio = %s
         order by p.codigo
        """,
        (str(ref_estudio),),
    )


# ── El pipeline, sin base ────────────────────────────────────────────

def test_despivotar_pasa_de_ancho_a_largo_y_resuelve_codigos():
    largo = ingesta.despivotar(FILAS, PREGUNTAS, "id_en_origen")

    # 3 celdas con contenido: R-001 responde las dos, R-002 solo P1.
    assert len(largo) == 3
    por_clave = {(r[0], r[1]): r for r in largo}
    # El código "1" se resolvió a su etiqueta antes de embeber.
    assert por_clave[("R-001", "P1")][2] == "Fernet"
    assert por_clave[("R-001", "P1")][3] == "¿Qué bebida consume habitualmente? → Fernet"
    # La celda vacía de R-002 no genera respuesta: una no-respuesta no es dato.
    assert ("R-002", "P2") not in por_clave


def test_una_abierta_se_embebe_con_su_texto_tal_cual():
    largo = ingesta.despivotar(FILAS, PREGUNTAS, "id_en_origen")
    abierta = next(r for r in largo if r[1] == "P2")
    assert abierta[3] == "¿Por qué la elige? → Porque es amargo"


# ── El cruce entre stores ────────────────────────────────────────────

def test_cada_respuesta_remota_queda_asociada_al_id_persona_correcto(
    conn_boveda, conn_semantica, proveedor, ola
):
    resultado = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )

    assert resultado["respuestas_escritas"] == 3
    filas = _respuestas_semantica(conn_semantica, ola["encuesta"]["ref_estudio"])
    por_persona = {}
    for f in filas:
        por_persona.setdefault(str(f["id_persona"]), {})[f["codigo"]] = f["valor_texto"]

    assert por_persona[ola["ana"]] == {"P1": "Fernet", "P2": "Porque es amargo"}
    assert por_persona[ola["beto"]] == {"P1": "Cerveza"}


def test_los_dos_stores_comparten_el_mismo_ref_estudio(conn_boveda, conn_semantica, proveedor, ola):
    encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    ref_estudio = ola["encuesta"]["ref_estudio"]
    ref_semantica = db.una(
        conn_semantica, "select ref_estudio from cuestionario where ref_estudio = %s", (ref_estudio,)
    )
    assert ref_semantica is not None
    assert str(ref_semantica["ref_estudio"]) == ref_estudio

    cruce = encuestas.verificar_cruce(conn_boveda, conn_semantica, ola["encuesta"]["id"])
    assert cruce["cruce_ok"]
    assert cruce["semantica"]["individuos"] == 2


def test_reingestar_el_mismo_estudio_no_duplica(conn_boveda, conn_semantica, proveedor, ola):
    primera = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    segunda = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )

    assert primera["respuestas_escritas"] == segunda["respuestas_escritas"] == 3
    assert len(_respuestas_semantica(conn_semantica, ola["encuesta"]["ref_estudio"])) == 3
    assert db.una(conn_semantica, "select count(*)::int as n from cuestionario")["n"] == 1
    assert db.una(conn_semantica, "select count(*)::int as n from individuo")["n"] == 2


def test_sin_uso_semantico_vigente_la_persona_no_se_ingesta(conn_boveda, conn_semantica, proveedor):
    panel = paneles.crear(conn_boveda, "Panel")
    solo_contacto = _panelista(conn_boveda, "3-3", "R-003", ("contacto_participacion",))
    completo = _panelista(conn_boveda, "4-4", "R-004")
    for id_persona in (solo_contacto, completo):
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola gate")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)

    filas = [
        {"id_en_origen": "R-003", "P1": "1"},
        {"id_en_origen": "R-004", "P1": "2"},
    ]
    resultado = encuestas.ingestar(
        conn_boveda, conn_semantica, encuesta["id"], PREGUNTAS, filas, proveedor=proveedor
    )

    assert resultado["sin_consentimiento"] == [solo_contacto]
    ingestadas = {
        str(f["id_persona"]) for f in _respuestas_semantica(conn_semantica, encuesta["ref_estudio"])
    }
    assert ingestadas == {completo}


def test_la_ingesta_marca_que_la_persona_respondio(conn_boveda, conn_semantica, proveedor, ola):
    encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    participacion = {
        p["id_persona"]: p["respondio"]
        for p in encuestas.listar_participacion(conn_boveda, ola["encuesta"]["id"])
    }
    assert participacion == {ola["ana"]: True, ola["beto"]: True}


def test_el_embedding_llega_con_la_dimension_del_esquema(conn_boveda, conn_semantica, proveedor, ola):
    encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    dims = db.una(
        conn_semantica, "select vector_dims(embedding) as d from respuesta limit 1"
    )["d"]
    assert dims == 1024


def test_una_fila_de_alguien_que_no_es_panelista_no_se_ingesta(
    conn_boveda, conn_semantica, proveedor, ola
):
    filas = FILAS + [{"id_en_origen": "R-999", "P1": "2"}]
    resultado = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, filas, proveedor=proveedor
    )

    assert resultado["sin_mapear"] == ["R-999"]
    assert resultado["respuestas_escritas"] == 3
