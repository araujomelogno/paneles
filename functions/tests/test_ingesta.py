"""R1.5 — Fielding, ingesta y vínculo de respuestas con `id_persona`.

Corre contra los dos stores reales: el conn_boveda (conn_boveda) y el store semántico (pgvector).
"""

import pytest

from panel_api import db, encuestas, ingesta, paneles, personas, pii, semantica

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


# ── Procedencia: de qué estudio es cada respuesta ────────────────────

@pytest.fixture
def dos_olas(conn_boveda, conn_semantica, proveedor):
    """La misma persona respondiendo en dos estudios distintos, con el mismo
    código de pregunta en los dos. Es el caso donde la procedencia importa."""
    panel = paneles.crear(conn_boveda, "Panel bebidas")
    id_persona = _panelista(conn_boveda, "1-1", "R-001")
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    olas = []
    for nombre, fecha, valor in (
        ("Ola 1 bebidas", "2026-03-01", "1"),
        ("Ola 2 bebidas", "2026-09-01", "3"),
    ):
        encuesta = encuestas.crear(conn_boveda, panel["id"], nombre, fecha)
        encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
        encuestas.ingestar(
            conn_boveda, conn_semantica, encuesta["id"], PREGUNTAS,
            [{"id_en_origen": "R-001", "P1": valor}], proveedor=proveedor,
        )
        olas.append(encuesta)
    return {"id_persona": id_persona, "olas": olas}


def test_el_mismo_codigo_en_dos_estudios_son_dos_preguntas(conn_semantica, dos_olas):
    # `respuesta` no guarda el estudio, pero no hace falta: cada `pregunta`
    # pertenece a un solo `cuestionario`, así que el mismo código en dos
    # estudios da dos preguntas distintas y las respuestas no colisionan.
    p1 = db.todas(
        conn_semantica,
        "select id, cuestionario_id from pregunta where codigo = 'P1' order by id",
    )
    assert len(p1) == 2                                  # una por cuestionario
    assert len({p["cuestionario_id"] for p in p1}) == 2  # y son cuestionarios distintos
    assert db.una(conn_semantica, "select count(*)::int as n from respuesta")["n"] == 2


def test_cada_respuesta_dice_de_que_estudio_es(conn_semantica, dos_olas):
    filas = db.todas(
        conn_semantica,
        """
        select estudio, fecha_campo, ref_estudio, pregunta_codigo, valor_texto
          from v_respuesta_estudio
         order by fecha_campo
        """,
    )
    assert [f["estudio"] for f in filas] == ["Ola 1 bebidas", "Ola 2 bebidas"]
    assert [f["valor_texto"] for f in filas] == ["Fernet", "Cerveza"]
    # El ref_estudio de cada respuesta es el de SU ola, no el de la otra.
    refs = {str(f["ref_estudio"]) for f in filas}
    assert refs == {o["ref_estudio"] for o in dos_olas["olas"]}


def test_la_vista_de_procedencia_no_expone_pii(conn_semantica, dos_olas):
    columnas = {
        f["column_name"]
        for f in db.todas(
            conn_semantica,
            """
            select column_name from information_schema.columns
             where table_name = 'v_respuesta_estudio'
            """,
        )
    }
    assert columnas & pii.CAMPOS_PII == set()
    # Y la auditoría en vivo, que recorre todas las columnas del esquema
    # incluidas las de vistas, sigue limpia.
    assert semantica.auditar_columnas(conn_semantica) == []


def test_la_vista_sirve_para_la_evolucion_de_una_persona(conn_semantica, dos_olas):
    # R4.1: seguir al mismo id_persona a través de olas.
    filas = db.todas(
        conn_semantica,
        """
        select fecha_campo, valor_texto from v_respuesta_estudio
         where id_persona = %s
         order by fecha_campo
        """,
        (dos_olas["id_persona"],),
    )
    assert [f["valor_texto"] for f in filas] == ["Fernet", "Cerveza"]


# ── Borrado acotado a un estudio ─────────────────────────────────────

def test_borrar_las_respuestas_de_un_estudio_deja_las_de_los_otros(
    conn_semantica, dos_olas
):
    primera, segunda = dos_olas["olas"]

    resultado = semantica.borrar_respuestas_de_estudio(
        conn_semantica, dos_olas["id_persona"], primera["ref_estudio"]
    )

    assert resultado["respuestas_borradas"] == 1
    quedan = db.todas(
        conn_semantica, "select estudio, valor_texto from v_respuesta_estudio"
    )
    assert [(f["estudio"], f["valor_texto"]) for f in quedan] == [
        ("Ola 2 bebidas", "Cerveza")
    ]


def test_el_borrado_acotado_no_toca_a_otras_personas(
    conn_boveda, conn_semantica, proveedor, dos_olas
):
    # Otra persona en la misma ola: su respuesta no se toca.
    otra = _panelista(conn_boveda, "2-2", "R-002")
    primera = dos_olas["olas"][0]
    paneles.agregar_miembro(conn_boveda, primera["panel_id"], otra)
    encuestas.convocar(conn_boveda, primera["id"], ids_persona=[otra])
    encuestas.ingestar(
        conn_boveda, conn_semantica, primera["id"], PREGUNTAS,
        [{"id_en_origen": "R-002", "P1": "2"}], proveedor=proveedor,
    )

    semantica.borrar_respuestas_de_estudio(
        conn_semantica, dos_olas["id_persona"], primera["ref_estudio"]
    )

    quedan = db.todas(
        conn_semantica,
        "select id_persona, estudio from v_respuesta_estudio order by estudio",
    )
    assert {(str(f["id_persona"]), f["estudio"]) for f in quedan} == {
        (otra, "Ola 1 bebidas"),
        (dos_olas["id_persona"], "Ola 2 bebidas"),
    }


# ════════════════════════════════════════════════════════════════════
#  P1 (Fase 2) — saltear el re-embedding cuando el texto no cambió
# ════════════════════════════════════════════════════════════════════

class ProveedorQueCuenta:
    """Envuelve un proveedor y cuenta cuántos textos le pidieron embeber.

    Es la única forma de probar que el ahorro existe: el resultado de la
    ingesta es el mismo se re-embeba o no, así que lo que hay que observar es
    la llamada al proveedor.
    """

    def __init__(self, envuelto):
        self.envuelto = envuelto
        self.dims = envuelto.dims
        self.textos = []

    def embeber(self, textos):
        self.textos.extend(textos)
        return self.envuelto.embeber(textos)

    def embeber_en_lotes(self, textos, tamano_lote=128):
        self.textos.extend(textos)
        return self.envuelto.embeber_en_lotes(textos, tamano_lote)


def test_reingestar_el_mismo_texto_no_vuelve_a_embeber(
    conn_boveda, conn_semantica, proveedor, ola
):
    """P1: «saltear el re-embedding por hash en re-ingestas».

    Re-ingestar una ola es normal —una corrección de campo, una pregunta que
    faltaba— y embeber es la parte que cuesta plata y tiempo. El mismo texto
    con el mismo modelo da el mismo vector.
    """
    contador = ProveedorQueCuenta(proveedor)
    primera = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS,
        proveedor=contador,
    )
    assert primera["embebidas"] == 3      # dos de Ana, una de Beto
    assert primera["reutilizadas"] == 0
    assert len(contador.textos) == 3

    contador.textos.clear()
    segunda = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS,
        proveedor=contador,
    )
    assert segunda["embebidas"] == 0
    assert segunda["reutilizadas"] == 3
    assert contador.textos == [], "no se le pidió nada al proveedor"


def test_una_respuesta_corregida_si_se_vuelve_a_embeber(
    conn_boveda, conn_semantica, proveedor, ola
):
    """El ahorro no puede convertirse en un dato viejo: si el texto cambió, el
    vector se recalcula."""
    encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS,
        proveedor=proveedor,
    )
    corregidas = [
        {"id_en_origen": "R-001", "P1": "1", "P2": "Porque me lo recomendaron"},
        {"id_en_origen": "R-002", "P1": "3", "P2": ""},
    ]
    contador = ProveedorQueCuenta(proveedor)
    resultado = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, corregidas,
        proveedor=contador,
    )
    assert resultado["embebidas"] == 1
    assert resultado["reutilizadas"] == 2
    assert contador.textos == ["¿Por qué la elige? → Porque me lo recomendaron"]

    guardadas = {
        (f["id_persona"], f["codigo"]): f["valor_texto"]
        for f in _respuestas_semantica(conn_semantica, ola["encuesta"]["ref_estudio"])
    }
    import uuid

    assert guardadas[(uuid.UUID(ola["ana"]), "P2")] == "Porque me lo recomendaron"


def test_el_hash_guardado_es_del_texto_que_se_vectorizo(
    conn_boveda, conn_semantica, proveedor, ola
):
    encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS,
        proveedor=proveedor,
    )
    filas = db.todas(
        conn_semantica, "select texto_embebido, hash_texto from respuesta"
    )
    assert filas
    for fila in filas:
        assert fila["hash_texto"] == semantica.hash_texto(fila["texto_embebido"])


def test_una_fila_sin_hash_previo_se_re_embebe_una_vez(
    conn_boveda, conn_semantica, proveedor, ola
):
    """Las respuestas ingestadas antes de la migración 0003 quedan con
    `hash_texto` nulo. La ingesta las trata como «no sé qué había» y las
    re-embebe una vez, que es el comportamiento anterior; no hay backfill
    porque el hash se calcula solo en la próxima ingesta."""
    encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS,
        proveedor=proveedor,
    )
    db.ejecutar(conn_semantica, "update respuesta set hash_texto = null")

    contador = ProveedorQueCuenta(proveedor)
    resultado = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, FILAS,
        proveedor=contador,
    )
    assert resultado["embebidas"] == 3
    assert len(contador.textos) == 3
    assert all(
        f["hash_texto"] is not None
        for f in db.todas(conn_semantica, "select hash_texto from respuesta")
    )
