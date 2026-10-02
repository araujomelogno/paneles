"""Los embeddings en 512 dimensiones, y el contrato con la base.

La dimensión no es una preferencia: es un contrato entre tres cosas que
tienen que decir lo mismo, y que viven en lugares distintos.

    EMBEDDINGS_DIMS  →  lo que el proveedor le pide a Voyage
    `output_dimension` →  lo que Voyage devuelve
    vector(512)      →  lo que la columna acepta

Si las tres no coinciden, el síntoma no es un error claro: es una factura.
La ingesta embebe el lote entero —eso es lo que cuesta plata— y recién al
insertar Postgres rechaza el vector por largo. Con 200.000 respuestas eso es
pagar por nada.

Por eso este archivo prueba las tres uniones, no solo que el número sea 512:

  1 · que el pedido a Voyage **lleve** el parámetro (sin él, la API devuelve
      su default y `EMBEDDINGS_DIMS` no hace nada);
  2 · que el proveedor **rechace** lo que vuelva con otro largo, por si el
      parámetro alguna vez cambia de nombre y la API lo ignora en silencio;
  3 · que la ingesta **se frene antes** de embeber cuando la columna y el
      proveedor no están de acuerdo.
"""

import pytest

from panel_api import db, embeddings as mod, encuestas, esquema, paneles, personas
from panel_api.errores import DatosInvalidos

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Cerveza"}, "orden": 1},
]
FILAS = [
    {"id_en_origen": "D-001", "P1": "1"},
    {"id_en_origen": "D-002", "P1": "2"},
]


@pytest.fixture
def ola_para_dimension(conn_boveda):
    """Un panel con dos panelistas convocados, listo para ingestar."""
    panel = paneles.crear(conn_boveda, "Panel de dimensión")
    ids = []
    for documento, id_en_origen in (("D1", "D-001"), ("D2", "D-002")):
        ids.append(personas.alta(conn_boveda, {
            "persona": {"documento": documento, "nombre": f"Panelista {documento}"},
            "consentimientos": consentimientos(*AMBAS),
            "origen": "dooblo", "id_en_origen": id_en_origen,
        })["id_persona"])
    for id_persona in ids:
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola de dimensión",
                               "2026-03-01")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    return {"panel": panel, "encuesta": encuesta}


# ════════════════════════════════════════════════════════════════════
#  1 · El pedido a Voyage lleva la dimensión
# ════════════════════════════════════════════════════════════════════

class RespuestaFalsa:
    """Lo mínimo de `requests.Response` que el proveedor mira."""

    def __init__(self, vectores, status=200):
        self.status_code = status
        self._vectores = vectores
        self.text = ""

    def json(self):
        return {"data": [{"index": i, "embedding": v}
                         for i, v in enumerate(self._vectores)]}


@pytest.fixture
def voyage_espiado(monkeypatch):
    """Un Voyage que no sale a la red y guarda con qué cuerpo lo llamaron."""
    enviados = {}

    def crear(dims=512, devuelve=None):
        proveedor = mod.Voyage(api_key="clave-de-prueba", dims=dims)

        def post_falso(url, headers=None, json=None, timeout=None):
            enviados["url"] = url
            enviados["cuerpo"] = json
            largo = devuelve if devuelve is not None else dims
            return RespuestaFalsa([[0.0] * largo for _ in json["input"]])

        import requests

        monkeypatch.setattr(requests, "post", post_falso)
        return proveedor, enviados

    return crear


def test_el_pedido_a_voyage_lleva_output_dimension(voyage_espiado):
    """El bug que el plan vino a arreglar: `self.dims` se guardaba y no se
    mandaba, así que la API devolvía su default de 1024 pase lo que pase y
    cambiar `EMBEDDINGS_DIMS` no tenía ningún efecto."""
    proveedor, enviados = voyage_espiado(dims=512)

    proveedor.embeber(["hola", "chau"])

    assert enviados["cuerpo"]["output_dimension"] == 512
    # Y el resto del cuerpo sigue igual: esto no cambia qué se embebe.
    assert enviados["cuerpo"]["model"] == "voyage-3.5"
    assert enviados["cuerpo"]["input_type"] == "document"
    assert enviados["cuerpo"]["input"] == ["hola", "chau"]


def test_un_vector_del_largo_equivocado_se_rechaza(voyage_espiado):
    """La red de seguridad del punto 1: si el parámetro alguna vez cambiara
    de nombre, la API lo ignoraría **en silencio**. Sin este control, el
    síntoma aparecería recién al insertar, con el lote ya pagado."""
    proveedor, _ = voyage_espiado(dims=512, devuelve=1024)

    with pytest.raises(mod.ErrorEmbeddings, match="1024.*se le pidieron 512"):
        proveedor.embeber(["hola"])


def test_una_dimension_que_el_modelo_no_genera_se_rechaza_al_construir():
    """Y no cuando la API conteste un 400 a mitad de una ingesta larga."""
    with pytest.raises(mod.ErrorEmbeddings, match="no genera vectores de 777"):
        mod.Voyage(api_key="x", dims=777)

    for valida in mod.DIMS_VALIDAS:
        assert mod.Voyage(api_key="x", dims=valida).dims == valida


# ════════════════════════════════════════════════════════════════════
#  2 · El default es 512 en todos lados, y es uno solo
# ════════════════════════════════════════════════════════════════════

def test_el_default_es_512_y_sale_de_un_solo_lugar():
    from panel_api import config

    assert mod.DIMS_POR_DEFECTO == 512
    # Los tres caminos por los que se construye un proveedor coinciden.
    assert mod.crear(entorno={"EMBEDDINGS_PROVEEDOR": "deterministico"}).dims == 512
    cfg = config.cargar({"DSN_BOVEDA": "postgresql://a/b",
                         "DSN_SEMANTICA": "postgresql://a/c"})
    assert cfg.dims_embeddings == 512


def test_el_doble_de_pruebas_usa_la_misma_dimension_que_el_real():
    """Si no, las pruebas correrían contra un sistema que no es el que se
    despliega: insertarían vectores de un largo que producción no produce."""
    assert mod.BolsaDePalabras().dims == mod.DIMS_POR_DEFECTO
    assert len(mod.BolsaDePalabras().embeber(["cualquier cosa"])[0]) == 512


def test_el_entorno_puede_cambiarla_y_ahora_sirve_de_algo(voyage_espiado):
    """Antes `EMBEDDINGS_DIMS` se leía y se ignoraba. La prueba de que ahora
    no: el valor del entorno llega hasta el cuerpo del pedido."""
    import requests

    proveedor = mod.crear(entorno={"EMBEDDINGS_PROVEEDOR": "voyage",
                                   "EMBEDDINGS_API_KEY": "x",
                                   "EMBEDDINGS_DIMS": "256"})
    assert proveedor.dims == 256

    enviados = {}
    original = requests.post
    try:
        requests.post = lambda url, headers=None, json=None, timeout=None: (
            enviados.update(cuerpo=json)
            or RespuestaFalsa([[0.0] * 256 for _ in json["input"]]))
        proveedor.embeber(["hola"])
    finally:
        requests.post = original
    assert enviados["cuerpo"]["output_dimension"] == 256


# ════════════════════════════════════════════════════════════════════
#  3 · La base y el proveedor tienen que estar de acuerdo
# ════════════════════════════════════════════════════════════════════

def test_la_columna_quedo_en_512(conn_semantica):
    filas = {f["columna"]: f["dimension"] for f in db.todas(
        conn_semantica, "select columna, dimension from v_dimension_embeddings")}

    assert filas == {"respuesta.embedding": 512,
                     "pregunta.embedding_texto": 512}


def test_la_vista_solo_mira_tablas_de_verdad(conn_semantica):
    """El índice HNSW y `v_respuesta_estudio` también tienen una columna
    `embedding`. Si la vista las contara, una respuesta de dos filas serían
    cuatro y el diagnóstico hablaría de columnas que nadie puede migrar."""
    columnas = [f["columna"] for f in db.todas(
        conn_semantica, "select columna from v_dimension_embeddings")]

    assert not [c for c in columnas if c.startswith("v_")]
    assert not [c for c in columnas if "idx" in c]


def test_sin_desajuste_no_se_informa_nada(conn_semantica):
    assert esquema.desajuste_de_dimension(conn_semantica, 512) is None


def test_un_desajuste_se_explica_con_los_dos_numeros(conn_semantica):
    mensaje = esquema.desajuste_de_dimension(conn_semantica, 1024)

    assert mensaje
    assert "1024" in mensaje and "512" in mensaje
    # Y dice qué hacer, que es la mitad que convierte un error en algo
    # accionable.
    assert "EMBEDDINGS_DIMS" in mensaje


def test_sin_la_migracion_aplicada_no_se_inventa_un_desajuste(conn_semantica):
    """`None` y no «no coinciden»: no poder saberlo es distinto de saber que
    está mal, y confundirlos haría fallar ingestas que estaban bien."""
    conn_semantica.execute("drop view v_dimension_embeddings")

    assert esquema.dimension_de_la_columna(conn_semantica) is None
    assert esquema.desajuste_de_dimension(conn_semantica, 1024) is None


def test_consultar_la_dimension_no_se_lleva_puesta_la_transaccion(conn_semantica):
    """La ingesta llama a esto **en el medio** de su transacción, con los
    individuos ya creados. Si al no encontrar la vista hiciera `rollback()`
    en vez de soltar un savepoint, se llevaría ese trabajo sin que nadie se
    entere, y el error aparecería mucho después y en otro lado."""
    conn_semantica.execute(
        "insert into cuestionario (ref_estudio, nombre) "
        "values (gen_random_uuid(), 'En curso')")
    conn_semantica.execute("drop view v_dimension_embeddings")

    assert esquema.dimension_de_la_columna(conn_semantica) is None

    # La fila de antes sigue ahí: la transacción no se abortó.
    assert db.una(conn_semantica,
                  "select count(*)::int as n from cuestionario")["n"] == 1


def test_el_diagnostico_informa_la_dimension(conn_boveda, conn_semantica):
    estado = esquema.revisar_stores(conn_boveda, conn_semantica,
                                    dims_proveedor=512)

    assert estado["embeddings"]["dimension_de_la_columna"] == 512
    assert estado["embeddings"]["dimension_del_proveedor"] == 512
    assert estado["embeddings"]["desajuste"] is None
    # Y la desalineación no se cuenta como migración faltante: son cosas
    # distintas y se arreglan distinto.
    assert estado["semantica"]["completo"] is True


# ════════════════════════════════════════════════════════════════════
#  4 · La ingesta se frena **antes** de gastar
# ════════════════════════════════════════════════════════════════════

class ProveedorQueCobra(mod.BolsaDePalabras):
    """Un proveedor que anota si lo llamaron. Es la pregunta de esta sección:
    no «¿falló?», sino «¿falló antes de pagar?»."""

    def __init__(self, dims):
        super().__init__(dims=dims)
        self.llamadas = 0

    def embeber(self, textos):
        self.llamadas += 1
        return super().embeber(textos)


def test_la_ingesta_se_frena_antes_de_embeber_si_la_dimension_no_coincide(
        conn_boveda, conn_semantica, ola_para_dimension):
    caro = ProveedorQueCobra(dims=1024)

    with pytest.raises(DatosInvalidos, match="dimensiones"):
        encuestas.ingestar(conn_boveda, conn_semantica,
                           ola_para_dimension["encuesta"]["id"],
                           PREGUNTAS, FILAS, proveedor=caro)

    assert caro.llamadas == 0, (
        "se mandó el lote a embeber antes de descubrir el desajuste: eso es "
        "exactamente la factura que esta guarda existe para evitar")


def test_con_la_dimension_correcta_la_ingesta_pasa(conn_boveda, conn_semantica,
                                                   ola_para_dimension):
    bien = ProveedorQueCobra(dims=512)
    salida = encuestas.ingestar(conn_boveda, conn_semantica,
                                ola_para_dimension["encuesta"]["id"],
                                PREGUNTAS, FILAS, proveedor=bien)

    assert salida["respuestas_escritas"] == len(FILAS)
    assert bien.llamadas >= 1
