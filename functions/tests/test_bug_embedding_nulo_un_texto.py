"""BUG — La ingesta de una carga falla siempre en el mismo texto:
`respuesta.embedding` nulo (`docs/BUG_ingesta_embedding_nulo_un_texto.md`).

La carga 28 quedó con 910 personas creadas y ninguna respuesta: el lote 0
fallaba en sus cinco intentos, siempre en «¿Cuál es tu área de formación o
carrera universitaria? → Educación», con «null value in column "embedding"
… violates not-null constraint» y nada más.

La causa: `upsert_respuestas` escribía fila por fila, y el trigger que borra
las unidades huérfanas (`semantica/0010`) corre al final de **cada**
sentencia. Si el lote trae dos respuestas de la misma persona a la misma
pregunta —dos filas del archivo que el dedup fusionó en una persona— con
textos distintos, la segunda es un `UPDATE` que deja a la unidad del primer
texto sin respuestas, y el trigger la borra **a mitad del lote**. Si más
adelante otra persona dio ese mismo texto, su respuesta apunta a la unidad
recién borrada (el mapa huella → unidad se armó antes), el `select embedding
into` del trigger no encuentra filas, y el vector queda nulo.

Lo mismo con una re-ingesta: alguien cambió su respuesta y otra persona,
nueva, dio la que él tenía antes.

Corre contra el store semántico real, que es donde vive el trigger.
"""

import uuid

import pytest

from panel_api import cargas, db, semantica
from panel_api.errores import DatosInvalidos  # noqa: F401  (contexto del módulo)

from test_r_org_origen_y_ambitos import DEMOGRAFICAS, EVIDENCIA, _fila

TEXTO = "¿Cuál es tu área de formación o carrera universitaria?"


@pytest.fixture
def estudio(conn_semantica):
    cuestionario = semantica.asegurar_cuestionario(
        conn_semantica, str(uuid.uuid4()), "Estudio de prueba")
    pregunta = semantica.upsert_preguntas(conn_semantica, cuestionario, [
        {"codigo": "P1", "texto": TEXTO, "tipo": "cerrada", "orden": 1}])["P1"]
    individuos = semantica.asegurar_individuos(
        conn_semantica, [str(uuid.uuid4()) for _ in range(3)])
    return {"pregunta": pregunta, "individuos": list(individuos.values())}


def _respuesta(individuo, pregunta, valor):
    return {"individuo_id": individuo, "pregunta_id": pregunta, "valor_texto": valor,
            "texto_embebido": f"{TEXTO} → {valor}"}


def _escritas(conn, pregunta):
    return {f["individuo_id"]: f["valor_texto"] for f in db.todas(
        conn, "select individuo_id, valor_texto from respuesta where pregunta_id = %s",
        (pregunta,))}


def test_dos_respuestas_de_una_persona_y_el_mismo_texto_en_otra(
        conn_semantica, proveedor, estudio):
    """El caso de la carga 28: X aparece dos veces (Educación, después
    Ingeniería) e Y, más adelante, también dijo Educación."""
    x, y, _ = estudio["individuos"]
    p = estudio["pregunta"]
    salida = semantica.escribir_respuestas(conn_semantica, [
        _respuesta(x, p, "Educación"),
        _respuesta(x, p, "Ingeniería"),
        _respuesta(y, p, "Educación"),
    ], proveedor)

    # Queda la última de X, como antes; Y, con la suya.
    assert _escritas(conn_semantica, p) == {x: "Ingeniería", y: "Educación"}
    assert salida["escritas"] == 2
    assert salida["repetidas"] == 1
    # Ninguna respuesta sin vector, y ninguna unidad huérfana ni faltante.
    assert not db.todas(conn_semantica,
                        "select 1 from respuesta where embedding is null")
    assert db.una(conn_semantica, """
        select count(*) as n from unidad_evidencia u
         where not exists (select 1 from respuesta r where r.unidad_id = u.id)
    """)["n"] == 0


def test_una_reingesta_donde_otra_persona_da_el_texto_que_alguien_dejo(
        conn_semantica, proveedor, estudio):
    """X dijo Educación en la primera ingesta; en la segunda cambió a
    Ingeniería, e Y —nueva— dijo Educación. La unidad de Educación no puede
    irse en el medio."""
    x, y, _ = estudio["individuos"]
    p = estudio["pregunta"]
    semantica.escribir_respuestas(conn_semantica, [_respuesta(x, p, "Educación")],
                                  proveedor)
    semantica.escribir_respuestas(conn_semantica, [
        _respuesta(x, p, "Ingeniería"),
        _respuesta(y, p, "Educación"),
    ], proveedor)
    assert _escritas(conn_semantica, p) == {x: "Ingeniería", y: "Educación"}
    assert not db.todas(conn_semantica,
                        "select 1 from respuesta where embedding is null")


def test_la_unidad_que_de_verdad_queda_huerfana_se_sigue_borrando(
        conn_semantica, proveedor, estudio):
    """La corrección no apaga el trigger: si nadie más da el texto viejo, su
    unidad se va al final de la escritura, como siempre."""
    x, _, _ = estudio["individuos"]
    p = estudio["pregunta"]
    semantica.escribir_respuestas(conn_semantica, [_respuesta(x, p, "Educación")],
                                  proveedor)
    semantica.escribir_respuestas(conn_semantica, [_respuesta(x, p, "Ingeniería")],
                                  proveedor)
    textos = {f["texto_embebido"] for f in db.todas(
        conn_semantica, "select texto_embebido from unidad_evidencia")}
    assert textos == {f"{TEXTO} → Ingeniería"}


def test_una_unidad_sin_vector_es_un_error_explicito_con_huella_y_pregunta(
        conn_semantica, proveedor, estudio, monkeypatch, capsys):
    """La salvaguarda: si una respuesta va a apuntar a una unidad que no
    existe, el error lo dice —qué huella, qué pregunta— en vez de una
    violación de `not null` sin contexto, y queda en el log."""
    x, _, _ = estudio["individuos"]
    p = estudio["pregunta"]
    # Una unidad que desaparece entre que se resolvió y se escribe: lo que
    # pasaba con el trigger, o con otra tarea que la borró.
    original = semantica.unidades_por_hash

    def con_una_que_no_existe(conn, huellas):
        mapa = original(conn, huellas)
        return {h: 999_999_999 for h in mapa}

    semantica.escribir_respuestas(conn_semantica, [_respuesta(x, p, "Educación")],
                                  proveedor)
    monkeypatch.setattr(semantica, "unidades_por_hash", con_una_que_no_existe)
    with pytest.raises(semantica.RespuestaSinVector) as error:
        semantica.upsert_respuestas(conn_semantica, [_respuesta(x, p, "Educación")])

    huella = semantica.hash_texto(f"{TEXTO} → Educación")
    assert huella in str(error.value)
    assert "P1" in str(error.value)
    assert isinstance(error.value, semantica.SinUnidad)
    log = capsys.readouterr().out
    assert f"huella={huella}" in log and "pregunta=P1" in log
    assert "Educación" not in log          # el log lleva huellas, no contenido


def test_una_carga_con_una_persona_repetida_en_el_archivo_entra_completa(ctx):
    """De punta a punta, como la carga 28: dos filas del archivo con el mismo
    documento (el dedup las fusiona en una persona) y respuestas distintas, y
    una tercera persona que repite la primera respuesta."""
    carga = cargas.crear(ctx.boveda, "Carga 28", tipo_datos_origen="encuesta",
                         modo_relevamiento="web")
    preguntas = [{"codigo": "P1", "texto": TEXTO, "tipo": "abierta", "orden": 1},
                 {"codigo": "SEXO", "texto": "Sexo", "tipo": "cerrada",
                  "opciones": {"1": "M", "2": "F"}, "orden": 2}]
    filas = [dict(_fila(1), P1="Educación"),
             dict(_fila(2, documento="20-1"), P1="Ingeniería"),   # la misma persona
             dict(_fila(3), P1="Educación")]
    cargas.crear_individuos(
        ctx.boveda, filas, {"nombre": "NOM", "documento": "DOC", "sexo": "SEXO"},
        origen="omnibus", columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        opciones_por_variable={"SEXO": {"1": "M", "2": "F"}}, carga_id=carga["id"])
    resultado = cargas.ingestar(
        ctx.boveda, ctx.semantica, carga["id"], preguntas, filas, columna_id="ID",
        origen="omnibus", proveedor=ctx.embeddings, demograficas=DEMOGRAFICAS)

    assert len(resultado["ids_persona_ingestados"]) == 2
    assert not db.todas(ctx.semantica,
                        "select 1 from respuesta where embedding is null")
    assert db.una(ctx.semantica, "select count(*) as n from respuesta")["n"] == 2


def test_si_postgres_rechaza_igual_el_error_dice_cual(
        conn_semantica, proveedor, estudio, monkeypatch):
    """La red de seguridad detrás de la salvaguarda: si la violación llega
    igual (la verificación previa se salteó), se traduce al mismo error con
    huella y pregunta, y la transacción de quien llama sigue usable."""
    x, _, _ = estudio["individuos"]
    p = estudio["pregunta"]
    semantica.escribir_respuestas(conn_semantica, [_respuesta(x, p, "Educación")],
                                  proveedor)
    original = semantica.unidades_por_hash
    monkeypatch.setattr(semantica, "unidades_por_hash",
                        lambda conn, huellas: {h: 999_999_999
                                               for h in original(conn, huellas)})
    monkeypatch.setattr(semantica, "_exigir_unidades_con_vector", lambda *a: None)
    with pytest.raises(semantica.RespuestaSinVector) as error:
        semantica.upsert_respuestas(conn_semantica, [_respuesta(x, p, "Educación")])
    assert "P1" in str(error.value)
    assert error.value.__cause__ is not None
    assert "Error de Postgres:" in str(error.value)
    assert "Educación" not in str(error.value)      # ni el texto de la fila
    assert db.una(conn_semantica, "select count(*) as n from respuesta")["n"] == 1
