"""Fase 2 — el motor de consultas: R2.7 a R2.11, y los P1 que lo acompañan.

Cada prueba de acá corresponde a un punto del Definition of Done, y el
docstring dice a cuál. Corren contra Postgres real con pgvector, y con los
proveedores sin red (embeddings de bolsa de palabras, reranker léxico,
verificador léxico): el resultado es determinístico y no depende de que
Voyage ni la API de Claude estén disponibles.
"""

import pytest

from panel_api import consentimiento, consultas, demografia, personas
from panel_api import reranker as mod_reranker
from panel_api import verificacion as mod_verificacion
from panel_api.errores import DatosInvalidos

from corpus import CRITERIO_FERNET, nombres, nombres_excluidos, sembrar


@pytest.fixture
def corpus(conn_boveda, conn_semantica, proveedor):
    contexto = sembrar(conn_boveda, conn_semantica, proveedor)
    conn_boveda.commit()
    conn_semantica.commit()
    return contexto


# ════════════════════════════════════════════════════════════════════
#  R2.7 — recuperación semántica
# ════════════════════════════════════════════════════════════════════

def test_un_criterio_semantico_devuelve_individuos_con_evidencia_y_procedencia(
    ctx, corpus
):
    """DoD: «devuelve individuos rankeados con evidencia y procedencia»."""
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})

    assert resultado["tipo"] == "semantica"
    assert resultado["items"], "el criterio no recuperó a nadie"
    assert "Ana Pérez" in nombres(resultado, corpus)

    primero = resultado["items"][0]
    assert primero["puntaje"] > 0
    evidencia = primero["evidencias"][0]
    # Procedencia: qué estudio y qué pregunta originaron la respuesta.
    assert evidencia["estudio"] == "Ola 1 — Bebidas"
    assert evidencia["ref_estudio"] == corpus["encuesta"]["ref_estudio"]
    assert evidencia["pregunta_codigo"] in {"P1", "P2"}
    assert evidencia["pregunta_texto"]
    assert evidencia["valor_texto"]


def test_el_resultado_no_lleva_pii_solo_id_persona(ctx, corpus):
    """El invariante central, del lado de la salida: una consulta identifica
    por token opaco. Traducirlo a una persona es otra operación."""
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    prohibidos = {"nombre", "email", "documento", "celular", "fecha_nacimiento"}
    for item in resultado["items"]:
        assert set(item) & prohibidos == set()
        for evidencia in item["evidencias"]:
            assert set(evidencia) & prohibidos == set()


def test_el_recall_trae_al_candidato_de_polaridad_opuesta(ctx, corpus):
    """Antes de probar que el filtro funciona, hay que probar que hay algo
    que filtrar: el candidato que dice lo contrario está en el pool, y de
    hecho está bien arriba. Si esta prueba fallara, la siguiente pasaría por
    la razón equivocada."""
    from panel_api import semantica

    vector = ctx.embeddings.embeber([CRITERIO_FERNET])[0]
    pool = semantica.recuperar(ctx.semantica, vector, 20)
    ids = [c["id_persona"] for c in pool[:3]]
    assert corpus["por_nombre"]["Beto Silva"] in ids


# ════════════════════════════════════════════════════════════════════
#  R2.8 + R2.9 — polaridad opuesta
# ════════════════════════════════════════════════════════════════════

def test_el_candidato_de_polaridad_opuesta_no_esta_en_el_ranking_final(ctx, corpus):
    """DoD: «un caso de polaridad opuesta preparado a propósito no aparece en
    el ranking final»."""
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})

    assert "Beto Silva" not in nombres(resultado, corpus)
    excluidos = nombres_excluidos(resultado, corpus)
    assert excluidos.get("Beto Silva") == mod_verificacion.NO_CUMPLE


def test_la_contradiccion_se_busca_en_todas_las_respuestas_de_la_persona(ctx, corpus):
    """Beto contestó «fernet» a qué toma y «no me gusta» a por qué. La
    primera respuesta lo pone arriba; la segunda lo desmiente. Si se
    verificara solo su mejor evidencia, entraría al ranking con la que le
    conviene."""
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    beto = next(
        e for e in resultado["excluidos"]
        if e["id_persona"] == corpus["por_nombre"]["Beto Silva"]
    )
    assert "no me gusta" in (beto["evidencia"] or "").lower()


def test_el_reranker_pone_la_polaridad_opuesta_por_debajo(ctx, corpus):
    """R2.8 en aislamiento: el reranker por sí solo tiene que hundir al
    candidato que dice lo contrario, sin ayuda de la verificación."""
    textos = [
        "¿Qué opina del fernet? → No me gusta el fernet, lo detesto",
        "¿Qué opina del fernet? → Me encanta el fernet, lo tomo siempre",
    ]
    orden = mod_reranker.Lexico().reordenar(CRITERIO_FERNET, textos)
    assert orden[0][0] == 1, "el que sí cumple tiene que quedar primero"
    assert orden[-1][0] == 0


def test_el_verificador_no_puede_inventar_la_evidencia_que_cita(ctx):
    """R2.9: «nunca inventa evidencia, cita `valor_texto` existente».

    Se le da un verificador que devuelve un índice inexistente y una razón
    inventada. La cita del resultado sale igual del registro propio, y el
    veredicto sin respaldo cae a «dudoso».
    """
    candidatos = [
        {"pregunta_texto": "¿Qué toma?", "valor_texto": "Fernet con cola"},
    ]

    class Mentiroso(mod_verificacion.Verificador):
        nombre = "mentiroso"

        def verificar(self, criterio, cands):
            return mod_verificacion._completar(
                [{"n": 99, "veredicto": "cumple", "razon": "dijo que ama el whisky"}],
                cands, criterio,
            )

    salida = Mentiroso().verificar(CRITERIO_FERNET, candidatos)
    assert salida[0]["evidencia"] == "Fernet con cola"
    assert salida[0]["veredicto"] == mod_verificacion.DUDOSO
    assert "no se pronunció" in salida[0]["razon"]


# ════════════════════════════════════════════════════════════════════
#  R2.8 — degradación explícita
# ════════════════════════════════════════════════════════════════════

def test_sin_reranker_la_consulta_sigue_y_lo_informa(ctx, corpus):
    """DoD: «si el reranker falla, la consulta degrada explícitamente y lo
    informa»."""
    ctx._reranker = mod_reranker.NoDisponible("no hay clave configurada")
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})

    assert resultado["items"], "la consulta no tiene que caerse"
    degradacion = next(
        d for d in resultado["degradaciones"] if d["etapa"] == "reranking"
    )
    assert "no hay clave" in degradacion["motivo"]
    assert degradacion["consecuencia"]
    assert resultado["diagnostico"]["reranker"] == "ninguno"


def test_un_reranker_que_explota_degrada_en_vez_de_romper_la_consulta(ctx, corpus):
    class Roto(mod_reranker.Reranker):
        nombre = "roto"

        def reordenar(self, criterio, textos, top_k=None):
            raise RuntimeError("503 del proveedor")

    ctx._reranker = Roto()
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    degradacion = next(
        d for d in resultado["degradaciones"] if d["etapa"] == "reranking"
    )
    assert "503" in degradacion["motivo"]


def test_sin_verificador_no_se_excluye_a_nadie_por_veredicto(ctx, corpus):
    """Sin verificación no hay veredictos, y sin veredictos no se puede
    excluir por veredicto. La consulta lo dice en vez de devolver un ranking
    vacío en modo estricto."""
    ctx._verificador = mod_verificacion.NoDisponible("sin clave de Claude")
    resultado = consultas.ejecutar(
        ctx, {"criterios": [CRITERIO_FERNET], "modo": "estricto"}
    )
    degradacion = next(
        d for d in resultado["degradaciones"] if d["etapa"] == "verificacion"
    )
    assert "sin clave" in degradacion["motivo"]
    assert resultado["items"], (
        "con la verificación caída, el modo estricto no puede vaciar el ranking"
    )


# ════════════════════════════════════════════════════════════════════
#  R2.10 — criterios combinados y modos
# ════════════════════════════════════════════════════════════════════

def test_criterios_combinados_dan_un_ranking_unico_por_persona(ctx, corpus):
    """DoD: «ranking único a nivel persona» con puntaje por criterio y
    combinado."""
    resultado = consultas.ejecutar(ctx, {
        "criterios": [CRITERIO_FERNET, {"dimension": "sexo", "valor": "F"}],
        "modo": "laxo",
    })
    ids = [i["id_persona"] for i in resultado["items"]]
    assert len(ids) == len(set(ids)), "una persona no puede aparecer dos veces"

    for item in resultado["items"]:
        tipos = {c["tipo"] for c in item["criterios"]}
        assert tipos == {"semantico", "demografico"}
        assert 0 <= item["puntaje"] <= 1


def test_el_modo_estricto_excluye_y_el_laxo_incluye_penalizado(ctx, corpus):
    """DoD: «el modo estricto/laxo cambia el resultado como se espera».

    Cora habla de whisky: es mujer (cumple el criterio duro) y no tiene
    evidencia del criterio semántico. En estricto queda afuera; en laxo entra
    con 0 en ese criterio y marcada como penalizada.
    """
    definicion = {
        "criterios": [CRITERIO_FERNET, {"dimension": "sexo", "valor": "F"}],
    }

    estricto = consultas.ejecutar(ctx, {**definicion, "modo": "estricto"})
    laxo = consultas.ejecutar(ctx, {**definicion, "modo": "laxo"})

    assert "Cora Díaz" not in nombres(estricto, corpus)
    assert "Cora Díaz" in nombres(laxo, corpus)
    assert set(nombres(estricto, corpus)) <= set(nombres(laxo, corpus))

    cora = next(
        i for i in laxo["items"]
        if i["id_persona"] == corpus["por_nombre"]["Cora Díaz"]
    )
    assert cora["penalizado"] is True
    ana = next(
        i for i in laxo["items"]
        if i["id_persona"] == corpus["por_nombre"]["Ana Pérez"]
    )
    assert ana["puntaje"] > cora["puntaje"], (
        "quien cumple los dos criterios tiene que ir por encima del penalizado"
    )


def test_el_modo_laxo_tampoco_incluye_a_quien_contradice(ctx, corpus):
    """Lo que el modo laxo tolera es la ausencia de evidencia y la duda, no la
    contradicción: mostrar como coincidencia a quien la evidencia desmiente
    no es un resultado laxo, es un resultado falso."""
    resultado = consultas.ejecutar(
        ctx, {"criterios": [CRITERIO_FERNET], "modo": "laxo"}
    )
    assert "Beto Silva" not in nombres(resultado, corpus)


def test_un_criterio_duro_filtra_aunque_el_modo_sea_laxo(ctx, corpus):
    resultado = consultas.ejecutar(ctx, {
        "criterios": [{"tipo": "semantico", "texto": CRITERIO_FERNET, "duro": True}],
        "modo": "laxo",
    })
    assert "Cora Díaz" not in nombres(resultado, corpus)


def test_los_pesos_cambian_el_orden(ctx, corpus):
    """Un criterio con más peso pesa más en el combinado. Se comprueba con la
    aritmética, que es lo que se puede afirmar sin depender del corpus."""
    liviano = consultas.ejecutar(ctx, {
        "criterios": [
            {"tipo": "semantico", "texto": CRITERIO_FERNET, "peso": 1},
            {"dimension": "sexo", "valor": "F", "peso": 1},
        ],
        "modo": "laxo",
    })
    pesado = consultas.ejecutar(ctx, {
        "criterios": [
            {"tipo": "semantico", "texto": CRITERIO_FERNET, "peso": 9},
            {"dimension": "sexo", "valor": "F", "peso": 1},
        ],
        "modo": "laxo",
    })
    id_cora = corpus["por_nombre"]["Cora Díaz"]
    de_liviano = next(i for i in liviano["items"] if i["id_persona"] == id_cora)
    de_pesado = next(i for i in pesado["items"] if i["id_persona"] == id_cora)
    # Cora solo aporta el criterio demográfico: si ese pesa menos, su
    # combinado baja.
    assert de_pesado["puntaje"] < de_liviano["puntaje"]


def test_una_consulta_sin_criterios_no_se_acepta(ctx):
    with pytest.raises(DatosInvalidos):
        consultas.ejecutar(ctx, {"criterios": []})


# ════════════════════════════════════════════════════════════════════
#  R2.11 — gate de consentimiento
# ════════════════════════════════════════════════════════════════════

def test_sin_uso_semantico_vigente_la_persona_no_aparece_nunca(ctx, corpus):
    """DoD: «individuos sin `uso_semantico` vigente nunca aparecen en
    resultados semánticos».

    Elena dice «me encanta el fernet, es mi bebida favorita»: cumpliría el
    criterio de sobra. No consintió el uso semántico entre estudios, así que
    no está —ni en el ranking ni entre los excluidos, porque nunca llegó a
    ser candidata.
    """
    for estrategia in consultas.ESTRATEGIAS:
        resultado = consultas.ejecutar(ctx, {
            "criterios": [CRITERIO_FERNET],
            "modo": "laxo",
            "estrategia_puente": estrategia,
        })
        presentes = {i["id_persona"] for i in resultado["items"]}
        presentes |= {e["id_persona"] for e in resultado["excluidos"]}
        assert corpus["por_nombre"]["Elena Núñez"] not in presentes, estrategia


def test_retirar_el_uso_semantico_saca_a_la_persona_de_las_consultas(
    ctx, conn_boveda, corpus
):
    """El gate se aplica en el punto de uso, no en el alta: retirar la
    finalidad tiene efecto en la consulta siguiente."""
    antes = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    assert "Ana Pérez" in nombres(antes, corpus)

    consentimiento.marcar_retirado(
        conn_boveda, corpus["por_nombre"]["Ana Pérez"], consentimiento.SEMANTICO
    )
    conn_boveda.commit()

    despues = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    assert "Ana Pérez" not in nombres(despues, corpus)


def test_la_respuesta_declara_cual_es_el_gate(ctx, corpus):
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    assert resultado["puente"]["gate"] == consentimiento.SEMANTICO


# ════════════════════════════════════════════════════════════════════
#  R2.5 — las dos estrategias del puente
# ════════════════════════════════════════════════════════════════════

def test_la_mixta_funciona_por_las_dos_estrategias_y_registra_cual_uso(ctx, corpus):
    """DoD: «una consulta mixta devuelve resultados correctos por ambas
    estrategias del puente, y registra cuál usó»."""
    definicion = {
        "criterios": [CRITERIO_FERNET, {"dimension": "sexo", "valor": "F"}],
        "modo": "estricto",
    }
    por_estrategia = {}
    for estrategia in consultas.ESTRATEGIAS:
        resultado = consultas.ejecutar(
            ctx, {**definicion, "estrategia_puente": estrategia}
        )
        assert resultado["puente"]["estrategia"] == estrategia
        assert resultado["puente"]["motivo"]
        por_estrategia[estrategia] = nombres(resultado, corpus)

    # Sobre este corpus las dos estrategias tienen que dar lo mismo: son dos
    # caminos hacia el mismo conjunto. En un corpus grande pueden diferir en
    # la cola, porque «semántico primero» recorta el recall antes de filtrar;
    # acá no hay cola que recortar, así que la igualdad es exigible.
    assert por_estrategia[consultas.DEMOGRAFICO_PRIMERO] == \
        por_estrategia[consultas.SEMANTICO_PRIMERO]


def test_sin_estrategia_forzada_elige_por_selectividad_y_lo_explica(ctx, corpus):
    """Con un segmento chico se filtra en la bóveda; sin criterios
    demográficos no hay segmento con el que recortar y se recupera primero."""
    mixta = consultas.ejecutar(ctx, {
        "criterios": [CRITERIO_FERNET, {"dimension": "sexo", "valor": "F"}],
    })
    assert mixta["puente"]["estrategia"] == consultas.DEMOGRAFICO_PRIMERO
    assert "personas" in mixta["puente"]["motivo"]

    solo_semantica = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    assert solo_semantica["puente"]["estrategia"] == consultas.SEMANTICO_PRIMERO


def test_no_hay_columnas_demograficas_del_lado_semantico(conn_semantica):
    """R2.5: «sin duplicar columnas demográficas en el store semántico».

    El puente es por `id_persona` justamente para no tener que copiar sexo,
    localidad ni fecha de nacimiento del otro lado del muro.
    """
    from panel_api import db

    filas = db.todas(
        conn_semantica,
        "select table_name, column_name from information_schema.columns "
        "where table_schema = 'public'",
    )
    columnas = {f["column_name"].lower() for f in filas}
    assert columnas & {"sexo", "localidad", "tramo_etario", "edad",
                       "fecha_nacimiento"} == set()


# ════════════════════════════════════════════════════════════════════
#  R2.4 — consulta puramente demográfica
# ════════════════════════════════════════════════════════════════════

def test_la_consulta_solo_demografica_no_abre_el_store_semantico(
    ctx_solo_boveda, conn_boveda, conn_semantica, proveedor
):
    """DoD: «una consulta solo demográfica no abre conexión al store
    semántico (test automatizado)».

    El contexto de esta prueba explota si alguien pide la conexión semántica,
    así que la garantía se verifica por construcción y no por una aserción
    indirecta.
    """
    sembrar(conn_boveda, conn_semantica, proveedor)
    conn_boveda.commit()

    resultado = consultas.ejecutar(
        ctx_solo_boveda, {"criterios": [{"dimension": "sexo", "valor": "F"}]}
    )
    assert resultado["tipo"] == "demografica"
    assert resultado["abrio_semantica"] is False
    assert resultado["diagnostico"]["abrio_semantica"] is False
    assert resultado["puente"]["estrategia"] is None
    assert {i["nombre"] for i in resultado["items"]} == {
        "Ana Pérez", "Cora Díaz", "Elena Núñez"
    }


def test_la_consulta_demografica_no_exige_uso_semantico(ctx_solo_boveda, corpus):
    """Elena no consintió el uso semántico, y aparece igual en una consulta
    demográfica: esa finalidad habilita el contenido de las respuestas, no
    los atributos que la bóveda ya tiene."""
    resultado = consultas.ejecutar(
        ctx_solo_boveda, {"criterios": [{"dimension": "localidad", "valor": "Colonia"}]}
    )
    assert [i["nombre"] for i in resultado["items"]] == ["Elena Núñez"]


def test_la_consulta_demografica_puede_exigir_una_finalidad(ctx_solo_boveda, corpus):
    resultado = consultas.ejecutar(ctx_solo_boveda, {
        "criterios": [{"dimension": "sexo", "valor": "F"}],
        "finalidad": consentimiento.SEMANTICO,
    })
    assert "Elena Núñez" not in {i["nombre"] for i in resultado["items"]}


@pytest.mark.parametrize("criterio", [
    {"dimension": "inventada", "valor": "x"},
    {"dimension": "sexo", "operador": "raro", "valor": "F"},
    {"dimension": "sexo"},
    {"dimension": "edad", "operador": "gte", "valor": "no es un número"},
])
def test_un_criterio_demografico_mal_formado_se_rechaza(criterio):
    with pytest.raises(DatosInvalidos):
        demografia.normalizar_criterio(criterio)


def test_los_valores_del_criterio_no_se_interpolan_en_el_sql(conn_boveda, corpus):
    """Las dimensiones están en lista blanca y los valores van como
    parámetros: un valor con comillas es un valor, no SQL."""
    ids = demografia.ids_del_segmento(
        conn_boveda,
        [demografia.normalizar_criterio(
            {"dimension": "localidad", "valor": "'; drop table persona; --"}
        )],
    )
    assert ids == []
    assert personas.listar(conn_boveda)["total"] == 5


# ════════════════════════════════════════════════════════════════════
#  P1 — diagnóstico, umbral de confianza, CSV, consultas guardadas
# ════════════════════════════════════════════════════════════════════

def test_el_diagnostico_informa_tamanos_y_tiempos_de_cada_etapa(ctx, corpus):
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    diagnostico = resultado["diagnostico"]
    etapas = {e["etapa"] for e in diagnostico["etapas"]}

    assert {"embedding", "recall", "reranking", "colapso", "verificacion",
            "combinacion"} <= etapas
    assert all("ms" in e for e in diagnostico["etapas"])
    assert diagnostico["ms_total"] >= 0

    recall = next(e for e in diagnostico["etapas"] if e["etapa"] == "recall")
    assert recall["crudos"] > 0
    verificacion = next(
        e for e in diagnostico["etapas"] if e["etapa"] == "verificacion"
    )
    assert verificacion["evidencias_verificadas"] > 0


def test_la_latencia_de_una_consulta_tipica_queda_registrada_y_es_baja(ctx, corpus):
    """DoD: «latencia de una consulta típica dentro del objetivo acordado».

    El objetivo real se acuerda contra el corpus de producción; lo que se
    puede fijar acá es que la latencia se mide y se informa, y que el
    oleoducto sobre un corpus chico y con proveedores locales no se va de
    unos pocos segundos. Si esta prueba empieza a fallar, hay una regresión
    de rendimiento en el motor, no en los proveedores.
    """
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    assert resultado["diagnostico"]["ms_total"] < 5000


def test_la_confianza_baja_cuando_la_mejor_evidencia_esta_lejos(ctx, corpus):
    """P1 — umbral de confianza. Con un umbral imposible de cumplir, todo el
    ranking queda marcado de confianza baja; con uno holgado, no."""
    exigente = consultas.ejecutar(
        ctx, {"criterios": [CRITERIO_FERNET], "umbral_distancia": 0.0}
    )
    assert all(i["confianza"] == "baja" for i in exigente["items"])
    assert exigente["parametros"]["umbral_distancia"] == 0.0

    holgado = consultas.ejecutar(
        ctx, {"criterios": [CRITERIO_FERNET], "umbral_distancia": 2.0}
    )
    assert any(i["confianza"] == "alta" for i in holgado["items"])


def test_el_csv_exporta_id_persona_puntaje_y_evidencia(ctx, corpus):
    """P1 — exportar el resultado a CSV. Tres columnas, y sin PII."""
    import csv
    import io

    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    texto = consultas.a_csv(resultado)
    filas = list(csv.reader(io.StringIO(texto)))

    assert filas[0] == ["id_persona", "puntaje", "evidencia"]
    assert len(filas) == len(resultado["items"]) + 1
    assert "Ana" not in texto, "el CSV no puede llevar nombres"
    # La procedencia viaja dentro de la celda de evidencia.
    assert "Ola 1 — Bebidas" in filas[1][2]


def test_una_consulta_guardada_guarda_la_definicion_y_no_el_resultado(
    conn_boveda, corpus
):
    """P1 — «guardar consultas frecuentes como definiciones reutilizables (no
    cachear resultados)»."""
    guardada = consultas.guardar(
        conn_boveda, "Fernet en Montevideo",
        {"criterios": [CRITERIO_FERNET, {"dimension": "localidad",
                                         "valor": "Montevideo"}]},
        descripcion="Para la ola de bebidas.", actor="uid-admin",
    )
    conn_boveda.commit()

    assert guardada["creado_por"] == "uid-admin"
    assert len(guardada["definicion"]["criterios"]) == 2
    # Nada que se parezca a un resultado guardado.
    assert "items" not in guardada["definicion"]
    assert "id_persona" not in str(guardada["definicion"])

    listadas = consultas.listar_guardadas(conn_boveda)
    assert [c["nombre"] for c in listadas] == ["Fernet en Montevideo"]
    assert consultas.obtener_guardada(conn_boveda, guardada["id"])["nombre"] == \
        "Fernet en Montevideo"


def test_guardar_dos_veces_con_el_mismo_nombre_actualiza(conn_boveda):
    consultas.guardar(conn_boveda, "Una", {"criterios": ["algo"]}, actor="uid-1")
    consultas.guardar(conn_boveda, "Una", {"criterios": ["otra cosa"]}, actor="uid-2")
    conn_boveda.commit()
    guardadas = consultas.listar_guardadas(conn_boveda)
    assert len(guardadas) == 1
    assert guardadas[0]["definicion"]["criterios"][0]["texto"] == "otra cosa"
    assert guardadas[0]["actualizado_por"] == "uid-2"


def test_una_consulta_guardada_mal_formada_se_rechaza_al_guardarla(conn_boveda):
    """Que falle quien la guarda, no quien la corre tres semanas después."""
    with pytest.raises(DatosInvalidos):
        consultas.guardar(
            conn_boveda, "Rota", {"criterios": [{"dimension": "inventada",
                                                 "valor": "x"}]},
        )


def test_una_consulta_guardada_se_puede_volver_a_correr(ctx, conn_boveda, corpus):
    guardada = consultas.guardar(
        conn_boveda, "Fernet", {"criterios": [CRITERIO_FERNET]}, actor="uid-1"
    )
    conn_boveda.commit()
    resultado = consultas.ejecutar(ctx, guardada["definicion"])
    assert "Ana Pérez" in nombres(resultado, corpus)
