"""R1.5 — Fielding, ingesta y vínculo de respuestas con `id_persona`.

Corre contra los dos stores reales: el local (bóveda) y el remoto (pgvector).
"""

import pytest

from panel_api import db, encuestas, ingesta, paneles, personas, remoto

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
def ola(local):
    """Un panel con dos panelistas convocados a una encuesta."""
    panel = paneles.crear(local, "Panel bebidas")
    ana = _panelista(local, "1-1", "R-001")
    beto = _panelista(local, "2-2", "R-002")
    for id_persona in (ana, beto):
        paneles.agregar_miembro(local, panel["id"], id_persona)
    encuesta = encuestas.crear(local, panel["id"], "Ola 1 bebidas", "2026-03-01")
    encuestas.convocar(local, encuesta["id"], todo_el_panel=True)
    return {"panel": panel, "encuesta": encuesta, "ana": ana, "beto": beto}


def _respuestas_remotas(conn_remoto, ref_estudio):
    return db.todas(
        conn_remoto,
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
    local, remota, proveedor, ola
):
    resultado = encuestas.ingestar(
        local, remota, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )

    assert resultado["respuestas_escritas"] == 3
    filas = _respuestas_remotas(remota, ola["encuesta"]["ref_estudio"])
    por_persona = {}
    for f in filas:
        por_persona.setdefault(str(f["id_persona"]), {})[f["codigo"]] = f["valor_texto"]

    assert por_persona[ola["ana"]] == {"P1": "Fernet", "P2": "Porque es amargo"}
    assert por_persona[ola["beto"]] == {"P1": "Cerveza"}


def test_los_dos_stores_comparten_el_mismo_ref_estudio(local, remota, proveedor, ola):
    encuestas.ingestar(
        local, remota, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    ref_local = ola["encuesta"]["ref_estudio"]
    ref_remoto = db.una(
        remota, "select ref_estudio from cuestionario where ref_estudio = %s", (ref_local,)
    )
    assert ref_remoto is not None
    assert str(ref_remoto["ref_estudio"]) == ref_local

    cruce = encuestas.verificar_cruce(local, remota, ola["encuesta"]["id"])
    assert cruce["cruce_ok"]
    assert cruce["remoto"]["individuos"] == 2


def test_reingestar_el_mismo_estudio_no_duplica(local, remota, proveedor, ola):
    primera = encuestas.ingestar(
        local, remota, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    segunda = encuestas.ingestar(
        local, remota, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )

    assert primera["respuestas_escritas"] == segunda["respuestas_escritas"] == 3
    assert len(_respuestas_remotas(remota, ola["encuesta"]["ref_estudio"])) == 3
    assert db.una(remota, "select count(*)::int as n from cuestionario")["n"] == 1
    assert db.una(remota, "select count(*)::int as n from individuo")["n"] == 2


def test_sin_uso_semantico_vigente_la_persona_no_se_ingesta(local, remota, proveedor):
    panel = paneles.crear(local, "Panel")
    solo_contacto = _panelista(local, "3-3", "R-003", ("contacto_participacion",))
    completo = _panelista(local, "4-4", "R-004")
    for id_persona in (solo_contacto, completo):
        paneles.agregar_miembro(local, panel["id"], id_persona)
    encuesta = encuestas.crear(local, panel["id"], "Ola gate")
    encuestas.convocar(local, encuesta["id"], todo_el_panel=True)

    filas = [
        {"id_en_origen": "R-003", "P1": "1"},
        {"id_en_origen": "R-004", "P1": "2"},
    ]
    resultado = encuestas.ingestar(
        local, remota, encuesta["id"], PREGUNTAS, filas, proveedor=proveedor
    )

    assert resultado["sin_consentimiento"] == [solo_contacto]
    ingestadas = {
        str(f["id_persona"]) for f in _respuestas_remotas(remota, encuesta["ref_estudio"])
    }
    assert ingestadas == {completo}


def test_la_ingesta_marca_que_la_persona_respondio(local, remota, proveedor, ola):
    encuestas.ingestar(
        local, remota, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    participacion = {
        p["id_persona"]: p["respondio"]
        for p in encuestas.listar_participacion(local, ola["encuesta"]["id"])
    }
    assert participacion == {ola["ana"]: True, ola["beto"]: True}


def test_el_embedding_llega_con_la_dimension_del_esquema(local, remota, proveedor, ola):
    encuestas.ingestar(
        local, remota, ola["encuesta"]["id"], PREGUNTAS, FILAS, proveedor=proveedor
    )
    dims = db.una(
        remota, "select vector_dims(embedding) as d from respuesta limit 1"
    )["d"]
    assert dims == 1024


def test_una_fila_de_alguien_que_no_es_panelista_no_se_ingesta(
    local, remota, proveedor, ola
):
    filas = FILAS + [{"id_en_origen": "R-999", "P1": "2"}]
    resultado = encuestas.ingestar(
        local, remota, ola["encuesta"]["id"], PREGUNTAS, filas, proveedor=proveedor
    )

    assert resultado["sin_mapear"] == ["R-999"]
    assert resultado["respuestas_escritas"] == 3
