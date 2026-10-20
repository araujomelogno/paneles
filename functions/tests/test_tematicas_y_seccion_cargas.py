"""R-TEM y R-CAR — Temáticas incorporadas y sección de cargas.

`specs/SPEC_tematicas_y_seccion_cargas.md`. El archivo sigue el Definition of
Done: la propuesta en la revisión, qué viaja a Claude, el vocabulario
consistente, la falla que no bloquea, la edición antes y después de
confirmar, el listado y el detalle, las cargas anteriores y los permisos.

Claude no se llama: se usa el proveedor de verdad (`tematicas.Claude`) con un
transporte doble, así lo que se prueba es el cuerpo que de verdad saldría
—herramienta forzada, solo textos de preguntas— y la interpretación de la
respuesta, incluidas la truncada y el error.

Corre contra Postgres de verdad: la unicidad del nombre, el `check` de la
normalización y la cascada al borrar la carga son de la base.
"""

import json
import pathlib
import re

import psycopg
import pytest

from panel_api import cargas, db, diferida, ruteo, tematicas
from panel_api.errores import DatosInvalidos, SinPermiso

from test_r_org_origen_y_ambitos import (
    DEMOGRAFICAS, EVIDENCIA, PREGUNTAS, _cargar, _fila, _panelista,
)

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CARGAS_JS = RAIZ / "web" / "public" / "js" / "paginas" / "cargas.js"


# ── El doble de la API de Claude ─────────────────────────────────────

class _Respuesta:
    def __init__(self, status_code, cuerpo):
        self.status_code = status_code
        self.text = cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
        self.headers = {}


def _respuesta_con(tematicas_, stop_reason="tool_use"):
    return _Respuesta(200, {
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 900, "output_tokens": 120},
        "content": [{"type": "tool_use", "name": "proponer_tematicas",
                     "input": {"tematicas": tematicas_}}],
    })


class Transporte:
    """Guarda cada cuerpo enviado y devuelve lo que se le indique."""

    def __init__(self, respuesta=None, error=None):
        self.respuesta = respuesta
        self.error = error
        self.enviados = []

    def __call__(self, cuerpo, timeout):
        self.enviados.append(json.loads(cuerpo))
        if self.error:
            raise self.error
        return self.respuesta

    @property
    def mensaje(self):
        return self.enviados[-1]["messages"][0]["content"]


def _claude(transporte):
    return tematicas.Claude(api_key="clave-de-prueba", modelo="modelo-x",
                            tiempo_s=30, transporte=transporte)


PROPUESTA_BEBIDAS = [
    {"tematica": "Consumo de bebidas alcohólicas", "es_nueva": True,
     "preguntas_relacionadas": ["P1", "P2"]},
    {"tematica": "hábitos de compra", "es_nueva": True,
     "preguntas_relacionadas": ["P2", "NO_EXISTE"]},
    {"tematica": "marcas de bebidas", "es_nueva": True,
     "preguntas_relacionadas": ["P1"]},
]

# Las preguntas de la revisión: las del R-ORG más una abierta y el nombre,
# marcado como identidad. Las respuestas de las filas llevan textos que no
# tienen que aparecer nunca en lo que sale hacia Claude.
PREGUNTAS_REVISION = PREGUNTAS + [
    {"codigo": "P2", "texto": "¿Dónde compra sus bebidas y por qué?",
     "tipo": "abierta", "orden": 3},
    {"codigo": "NOM", "texto": "Nombre y apellido del entrevistado",
     "tipo": "abierta", "orden": 4},
]


def _filas_revision():
    return [dict(_fila(i), P2=f"En el almacén de la esquina, respuesta-{i}")
            for i in range(1, 4)]


def _cuerpo_de_carga(**extra):
    return {"filas": _filas_revision(), "columna_id": "ID", "origen": "omnibus",
            "tipo_identificador": "alias", "modo": "crear_individuos",
            "preguntas": PREGUNTAS_REVISION, "demograficas": DEMOGRAFICAS,
            "evidencia_consentimiento": EVIDENCIA, **extra}


@pytest.fixture
def carga(conn_boveda):
    return cargas.crear(conn_boveda, "Ómnibus bebidas", fecha_estudio="2026-08-15",
                        publico_objetivo="Adultos", tipo_datos_origen="encuesta",
                        modo_relevamiento="telefonico")


def _revisar(ctx, actor, carga, **extra):
    _, resumen = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta",
        _cuerpo_de_carga(solo_revisar=True, **extra), {}, actor("analista"), ctx)
    return resumen


def _nombres(conn):
    return [f["nombre"] for f in db.todas(conn, "select nombre from tematica order by nombre")]


# ════════════════════════════════════════════════════════════════════
#  R-TEM.2 · La propuesta aparece en la revisión
# ════════════════════════════════════════════════════════════════════

def test_la_revision_de_una_carga_trae_las_tematicas_propuestas(ctx, actor, carga):
    ctx.proponente_tematicas = _claude(Transporte(_respuesta_con(PROPUESTA_BEBIDAS)))

    resumen = _revisar(ctx, actor, carga)

    propuesta = resumen["tematicas"]
    assert propuesta["estado"] == tematicas.OK
    assert propuesta["aviso"] is None
    assert [t["nombre"] for t in propuesta["propuesta"]] == [
        "consumo de bebidas alcohólicas", "hábitos de compra", "marcas de bebidas"]
    assert all(t["es_nueva"] for t in propuesta["propuesta"])
    # De dónde salió cada una, con el texto y solo con lo que se mandó.
    habitos = propuesta["propuesta"][1]
    assert habitos["preguntas_relacionadas"] == [
        {"codigo": "P2", "texto": "¿Dónde compra sus bebidas y por qué?"}]
    # Revisar no escribe nada: ni temáticas ni vínculos.
    assert _nombres(ctx.boveda) == []
    assert cargas.obtener(ctx.boveda, carga["id"])["tematicas"] == []


def test_la_respuesta_se_pide_con_la_herramienta_forzada(ctx, actor, carga):
    transporte = Transporte(_respuesta_con(PROPUESTA_BEBIDAS))
    ctx.proponente_tematicas = _claude(transporte)

    _revisar(ctx, actor, carga)

    enviado = transporte.enviados[0]
    assert enviado["tool_choice"] == {"type": "tool", "name": "proponer_tematicas"}
    assert [h["name"] for h in enviado["tools"]] == ["proponer_tematicas"]
    esquema = enviado["tools"][0]["input_schema"]["properties"]["tematicas"]["items"]
    assert set(esquema["required"]) == {"tematica", "es_nueva", "preguntas_relacionadas"}
    assert enviado["model"] == "modelo-x"


def test_a_claude_viajan_solo_los_textos_de_las_preguntas(ctx, actor, carga):
    """DoD: sin respuestas y sin variables demográficas ni de identidad."""
    transporte = Transporte(_respuesta_con(PROPUESTA_BEBIDAS))
    ctx.proponente_tematicas = _claude(transporte)

    _revisar(ctx, actor, carga)

    enviado = json.dumps(transporte.enviados[0], ensure_ascii=False)
    preguntas = transporte.mensaje.split("Preguntas del cuestionario (código: texto):\n")[1]
    assert preguntas.splitlines() == [
        "P1: ¿Qué bebida consume habitualmente?",
        "P2: ¿Dónde compra sus bebidas y por qué?",
    ]
    # Ninguna respuesta del archivo, ni etiqueta de opción, ni dato de nadie.
    for prohibido in ("almacén de la esquina", "respuesta-1", "Fernet", "Whisky",
                      "Persona 1", "20-1", "o-1", "Nombre y apellido", "SEXO",
                      "NOM", "DOC", "CONS"):
        assert prohibido not in enviado, prohibido


def test_lo_que_se_manda_es_lo_que_el_resumen_lleva_al_store_semantico():
    """La única decisión de qué sale: demográficas, identidad, la columna que
    identifica, las que no tienen texto y las de código de PII quedan afuera."""
    preguntas = [
        {"codigo": "ID", "texto": "Identificador"},
        {"codigo": "P1", "texto": "  ¿Usa   celular?  ", "opciones": {"1": "Sí"}},
        {"codigo": "SEXO", "texto": "Sexo"},
        {"codigo": "email", "texto": "Correo"},
        {"codigo": "P3", "texto": ""},
        {"codigo": "P4", "texto": "Batería", "excluida": True},
        {"codigo": "P1", "texto": "repetida"},
    ]
    assert tematicas.preguntas_para_proponer(
        preguntas, {"SEXO": "sexo"}, "ID") == [{"codigo": "P1", "texto": "¿Usa celular?"}]


# ════════════════════════════════════════════════════════════════════
#  R-TEM.3 · Vocabulario consistente
# ════════════════════════════════════════════════════════════════════

def test_mayusculas_y_espacios_quedan_como_una_sola(conn_boveda, carga):
    """DoD: «Telefonía móvil» y «telefonía móvil» son la misma."""
    otra = cargas.crear(conn_boveda, "Tracking", tipo_datos_origen="encuesta",
                        modo_relevamiento="web")
    tematicas.guardar(conn_boveda, carga["id"], ["Telefonía móvil"])
    tematicas.guardar(conn_boveda, otra["id"], ["  telefonía   MÓVIL. "])

    assert _nombres(conn_boveda) == ["telefonía móvil"]
    assert tematicas.listar(conn_boveda)[0]["cargas"] == 2


def test_la_normalizacion():
    assert tematicas.normalizar("  Telefonía   Móvil. ") == "telefonía móvil"
    assert tematicas.normalizar("«Consumo de tabaco»") == "consumo de tabaco"
    assert tematicas.normalizar("   ") is None


def test_una_variante_sin_tildes_reutiliza_la_existente(conn_boveda, carga):
    tematicas.guardar(conn_boveda, carga["id"], ["telefonía móvil"])
    otra = cargas.crear(conn_boveda, "Otra", tipo_datos_origen="encuesta",
                        modo_relevamiento="web")
    salida = tematicas.guardar(conn_boveda, otra["id"], ["Telefonia movil"])
    assert [t["nombre"] for t in salida] == ["telefonía móvil"]
    assert _nombres(conn_boveda) == ["telefonía móvil"]


def test_la_base_rechaza_un_nombre_sin_normalizar(conn_boveda):
    """El `check` de la 0027: un insert que no pase por Python no crea la
    variante."""
    for nombre in ("Telefonia", "telefonia  movil", " telefonia", "x"):
        with conn_boveda.transaction():
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn_boveda.transaction():
                    db.ejecutar(conn_boveda,
                                "insert into tematica (nombre) values (%s)", (nombre,))


def test_una_tematica_existente_se_reutiliza_en_vez_de_duplicarse(ctx, actor, carga):
    """DoD. El prompt lleva las existentes, y lo que vuelve con otra
    capitalización —o con tildes de menos— es la existente, no una nueva."""
    otra = cargas.crear(ctx.boveda, "Estudio anterior", tipo_datos_origen="encuesta",
                        modo_relevamiento="web")
    tematicas.guardar(ctx.boveda, otra["id"], ["consumo de bebidas alcohólicas",
                                               "telefonía móvil"])
    transporte = Transporte(_respuesta_con([
        {"tematica": "Consumo de Bebidas Alcoholicas", "es_nueva": True,
         "preguntas_relacionadas": ["P1"]},
        {"tematica": "lugares de compra", "es_nueva": False,
         "preguntas_relacionadas": ["P2"]},
    ]))
    ctx.proponente_tematicas = _claude(transporte)

    propuesta = _revisar(ctx, actor, carga)["tematicas"]["propuesta"]

    assert "- consumo de bebidas alcohólicas" in transporte.mensaje
    assert "- telefonía móvil" in transporte.mensaje
    assert "reutilizalas textualmente" in transporte.mensaje
    assert [(t["nombre"], t["es_nueva"]) for t in propuesta] == [
        ("consumo de bebidas alcohólicas", False),
        # Claude dijo que existía y no existe: la novedad la decide la base.
        ("lugares de compra", True),
    ]

    ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas",
                    {"tematicas": [{"nombre": t["nombre"], "origen": "propuesta_ia"}
                                   for t in propuesta]},
                    {}, actor("analista"), ctx)
    assert _nombres(ctx.boveda) == [
        "consumo de bebidas alcohólicas", "lugares de compra", "telefonía móvil"]


def test_la_propuesta_se_recorta_a_diez_y_sin_repetidas(conn_boveda):
    crudas = [{"tematica": f"tema número {i % 12}", "es_nueva": True,
               "preguntas_relacionadas": []} for i in range(15)]
    crudas.insert(1, {"tematica": "TEMA NÚMERO 0", "es_nueva": True,
                      "preguntas_relacionadas": []})
    salida = tematicas.proponer(
        conn_boveda, _claude(Transporte(_respuesta_con(crudas))),
        [{"codigo": "P1", "texto": "Pregunta"}])
    nombres = [t["nombre"] for t in salida["propuesta"]]
    assert len(nombres) == tematicas.MAX_PROPUESTAS == 10
    assert len(set(nombres)) == 10


# ════════════════════════════════════════════════════════════════════
#  Si Claude falla o tarda, la carga no se bloquea
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("transporte", [
    Transporte(error=TimeoutError("lento")),
    Transporte(_Respuesta(529, {"error": "overloaded"})),
    Transporte(_Respuesta(200, "no es json")),
    Transporte(_respuesta_con(PROPUESTA_BEBIDAS, stop_reason="max_tokens")),
    Transporte(_Respuesta(200, {"stop_reason": "end_turn",
                                "content": [{"type": "text", "text": "hola"}]})),
], ids=["timeout", "http-529", "no-json", "truncada", "sin-herramienta"])
def test_si_claude_falla_la_carga_se_confirma_igual_y_avisa(
        ctx, actor, carga, transporte):
    """DoD. La revisión llega con la lista vacía y el aviso, y confirmar la
    carga funciona igual. Una respuesta truncada se descarta entera: sus
    temáticas parciales no se aceptan."""
    ctx.proponente_tematicas = _claude(transporte)

    resumen = _revisar(ctx, actor, carga)
    assert resumen["tematicas"]["estado"] == tematicas.FALLO
    assert resumen["tematicas"]["propuesta"] == []
    assert "agregalas a mano" in resumen["tematicas"]["aviso"]
    # El resto del resumen, intacto.
    assert resumen["al_store_semantico"]["cuantas"] == 2

    status, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta", _cuerpo_de_carga(), {},
        actor("analista"), ctx)
    assert status == 202
    assert salida["creacion_de_individuos"]["resumen"]["creados"] == 3


def test_sin_clave_de_claude_la_revision_avisa_y_no_llama_a_nadie(ctx, actor, carga):
    """El contexto de pruebas no tiene proponente: es el camino de un
    despliegue sin `CLAUDE_API_KEY`."""
    resumen = _revisar(ctx, actor, carga)
    assert resumen["tematicas"]["estado"] == tematicas.NO_DISPONIBLE
    assert resumen["tematicas"]["propuesta"] == []


def test_la_pantalla_puede_omitir_la_propuesta(ctx, actor, carga):
    """Volver a revisar con una lista ya editada no paga otra llamada."""
    transporte = Transporte(_respuesta_con(PROPUESTA_BEBIDAS))
    ctx.proponente_tematicas = _claude(transporte)
    resumen = _revisar(ctx, actor, carga, proponer_tematicas=False)
    assert resumen["tematicas"] is None
    assert transporte.enviados == []


# ════════════════════════════════════════════════════════════════════
#  R-TEM.1 / R-TEM.4 · Editar antes y después de confirmar
# ════════════════════════════════════════════════════════════════════

def _vinculos(conn, carga_id):
    return {f["nombre"]: f["origen"] for f in db.todas(
        conn,
        "select t.nombre, ct.origen from carga_tematica ct "
        "join tematica t on t.id = ct.tematica_id where ct.carga_id = %s",
        (carga_id,))}


def test_quitar_agregar_y_editar_antes_y_despues_de_confirmar(ctx, actor, carga):
    """DoD. Antes de confirmar la pantalla guarda la lista revisada; después,
    se edita desde la sección Cargas. Mismo camino las dos veces."""
    # Antes de confirmar: dos propuestas aceptadas (una se quitó) y una manual.
    _, salida = ruteo.despachar(
        "PUT", f"/cargas/{carga['id']}/tematicas",
        {"tematicas": [{"nombre": "Consumo de bebidas alcohólicas",
                        "origen": "propuesta_ia"},
                       {"nombre": "marcas de bebidas", "origen": "propuesta_ia"},
                       "Fiestas y celebraciones"]},
        {}, actor("analista"), ctx)
    assert [t["nombre"] for t in salida["tematicas"]] == [
        "consumo de bebidas alcohólicas", "fiestas y celebraciones",
        "marcas de bebidas"]
    assert _vinculos(ctx.boveda, carga["id"]) == {
        "consumo de bebidas alcohólicas": "propuesta_ia",
        "marcas de bebidas": "propuesta_ia",
        "fiestas y celebraciones": "manual"}

    status, _ = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta", _cuerpo_de_carga(), {},
        actor("analista"), ctx)
    assert status == 202

    # Después: se quita una, se agrega otra, y la que queda conserva su
    # origen aunque la pantalla la mande como manual.
    ruteo.despachar(
        "PUT", f"/cargas/{carga['id']}/tematicas",
        {"tematicas": [{"nombre": "consumo de bebidas alcohólicas",
                        "origen": "manual"},
                       {"nombre": "lugares de compra", "origen": "manual"}]},
        {}, actor("operaciones"), ctx)
    assert _vinculos(ctx.boveda, carga["id"]) == {
        "consumo de bebidas alcohólicas": "propuesta_ia",
        "lugares de compra": "manual"}
    # La que se quitó de la carga sigue en el vocabulario: es compartida.
    assert "marcas de bebidas" in _nombres(ctx.boveda)

    # Y vaciar la lista es una edición válida.
    ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas", {"tematicas": []},
                    {}, actor("analista"), ctx)
    assert _vinculos(ctx.boveda, carga["id"]) == {}


@pytest.mark.parametrize("cuerpo, mensaje", [
    ({}, "lista de temáticas"),
    ({"tematicas": ["x"]}, "demasiado corta"),
    ({"tematicas": ["a" * 81]}, "demasiado larga"),
    ({"tematicas": [{"nombre": "tema", "origen": "claude"}]}, "Origen"),
    ({"tematicas": [f"tema {i}" for i in range(31)]}, "máximo por carga"),
])
def test_lo_que_no_es_una_tematica_se_rechaza(ctx, actor, carga, cuerpo, mensaje):
    with pytest.raises(DatosInvalidos, match=mensaje):
        ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas", cuerpo, {},
                        actor("analista"), ctx)
    assert _nombres(ctx.boveda) == []


def test_borrar_la_carga_se_lleva_sus_vinculos_pero_no_la_tematica(conn_boveda, carga):
    tematicas.guardar(conn_boveda, carga["id"], ["telefonía móvil"])
    db.ejecutar(conn_boveda, "delete from carga where id = %s", (carga["id"],))
    assert db.una(conn_boveda, "select count(*) as n from carga_tematica")["n"] == 0
    assert _nombres(conn_boveda) == ["telefonía móvil"]


def test_el_vocabulario_se_lista_con_su_uso(ctx, actor, carga):
    tematicas.guardar(ctx.boveda, carga["id"], ["telefonía móvil", "streaming"])
    _, salida = ruteo.despachar("GET", "/tematicas", {}, {}, actor("dpo"), ctx)
    assert salida["items"] == [
        {"id": salida["items"][0]["id"], "nombre": "streaming", "cargas": 1},
        {"id": salida["items"][1]["id"], "nombre": "telefonía móvil", "cargas": 1},
    ]


# ════════════════════════════════════════════════════════════════════
#  R-TEM.5 · Cargas existentes
# ════════════════════════════════════════════════════════════════════

def test_una_carga_anterior_recibe_temáticas_sin_volver_a_subir_el_archivo(
        ctx, actor, carga):
    """DoD. Las preguntas salen del store semántico por el `ref_estudio`:
    las demográficas nunca llegaron ahí. La propuesta no se aplica sola."""
    _cargar(ctx, carga, [_fila(1), _fila(2)])
    transporte = Transporte(_respuesta_con([
        {"tematica": "consumo de bebidas", "es_nueva": True,
         "preguntas_relacionadas": ["P1"]}]))
    ctx.proponente_tematicas = _claude(transporte)

    _, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/tematicas/propuesta", {}, {},
        actor("analista"), ctx)

    assert salida["estado"] == tematicas.OK
    assert [t["nombre"] for t in salida["propuesta"]] == ["consumo de bebidas"]
    assert salida["actuales"] == []
    preguntas = transporte.mensaje.split("(código: texto):\n")[1].splitlines()
    assert preguntas == ["P1: ¿Qué bebida consume habitualmente?"]
    # Proponer no escribe: lo que se guarda es lo que se confirme después.
    assert _vinculos(ctx.boveda, carga["id"]) == {}
    assert _nombres(ctx.boveda) == []


def test_una_carga_sin_preguntas_guardadas_avisa(ctx, actor, carga):
    ctx.proponente_tematicas = _claude(Transporte(_respuesta_con(PROPUESTA_BEBIDAS)))
    _, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/tematicas/propuesta", {}, {},
        actor("analista"), ctx)
    assert salida["estado"] == tematicas.SIN_PREGUNTAS
    assert salida["propuesta"] == []


# ════════════════════════════════════════════════════════════════════
#  R-CAR.1 · El listado
# ════════════════════════════════════════════════════════════════════

def test_el_listado_trae_atributos_temáticas_y_se_filtra(ctx, actor):
    vieja = cargas.crear(ctx.boveda, "Ómnibus 2025", fecha_estudio="2025-11-01",
                         tipo_datos_origen="encuesta", modo_relevamiento="web")
    sin_fecha = cargas.crear(ctx.boveda, "Base histórica",
                             tipo_datos_origen="otro",
                             tipo_datos_origen_otro="Base de clientes",
                             modo_relevamiento="web")
    nueva = cargas.crear(ctx.boveda, "Ómnibus 2026", fecha_estudio="2026-09-01",
                         publico_objetivo="Adultos", tipo_datos_origen="encuesta",
                         modo_relevamiento="telefonico")
    tematicas.guardar(ctx.boveda, vieja["id"], ["telefonía móvil"])
    tematicas.guardar(ctx.boveda, nueva["id"], ["telefonía móvil", "streaming"])
    _cargar(ctx, nueva, [_fila(1), _fila(2)])

    _, salida = ruteo.despachar("GET", "/cargas", {}, {}, actor("dpo"), ctx)
    # La más reciente primero, por fecha del estudio; la que no la declaró,
    # por cuándo se cargó (hoy).
    assert [c["nombre"] for c in salida["items"]] == [
        "Base histórica", "Ómnibus 2026", "Ómnibus 2025"]
    item = salida["items"][1]
    assert (item["fecha_estudio"], item["publico_objetivo"], item["personas"]) == (
        "2026-09-01", "Adultos", 2)
    assert [t["nombre"] for t in item["tematicas"]] == ["streaming", "telefonía móvil"]
    assert salida["opciones"] == cargas.opciones()

    telefonia = next(t["id"] for t in tematicas.listar(ctx.boveda)
                     if t["nombre"] == "telefonía móvil")
    _, filtrado = ruteo.despachar("GET", "/cargas", {},
                                  {"tematica_id": str(telefonia)}, actor("dpo"), ctx)
    assert [c["nombre"] for c in filtrado["items"]] == ["Ómnibus 2026", "Ómnibus 2025"]

    _, buscado = ruteo.despachar("GET", "/cargas", {}, {"q": "ómnibus 2025"},
                                 actor("dpo"), ctx)
    assert [c["nombre"] for c in buscado["items"]] == ["Ómnibus 2025"]


# ════════════════════════════════════════════════════════════════════
#  R-CAR.2 · El detalle
# ════════════════════════════════════════════════════════════════════

def test_el_detalle_separa_creados_y_reutilizados_y_lista_a_cada_uno(
        ctx, actor, carga):
    existente = _panelista(ctx.boveda, "20-2")
    _cargar(ctx, carga, [_fila(1), _fila(2), _fila(3)])
    tematicas.guardar(ctx.boveda, carga["id"], ["consumo de bebidas"])

    _, detalle = ruteo.despachar("GET", f"/cargas/{carga['id']}", {}, {},
                                 actor("analista"), ctx)
    assert (detalle["personas"], detalle["personas_creadas"],
            detalle["personas_reutilizadas"]) == (3, 2, 1)
    assert [t["nombre"] for t in detalle["tematicas"]] == ["consumo de bebidas"]
    for clave in ("nombre", "descripcion", "fecha_estudio", "publico_objetivo",
                  "creado_en"):
        assert clave in detalle

    # La lista paginada sale de la ruta de panelistas, con el origen de cada
    # uno en esa carga.
    _, pagina = ruteo.despachar("GET", "/panelistas", {},
                                {"carga_id": str(carga["id"]), "limite": "2"},
                                actor("analista"), ctx)
    assert pagina["total"] == 3 and len(pagina["items"]) == 2
    _, resto = ruteo.despachar("GET", "/panelistas", {},
                               {"carga_id": str(carga["id"]), "limite": "2",
                                "desde": "2"}, actor("analista"), ctx)
    origenes = {p["id_persona"]: p["origen_en_carga"]
                for p in pagina["items"] + resto["items"]}
    assert origenes[str(existente)] == "reutilizada"
    assert sorted(origenes.values()) == ["creada", "creada", "reutilizada"]


def test_sin_filtro_por_carga_el_listado_de_panelistas_no_cambia(ctx, actor, carga):
    _cargar(ctx, carga, [_fila(1)])
    _, salida = ruteo.despachar("GET", "/panelistas", {}, {}, actor("analista"), ctx)
    assert "origen_en_carga" not in salida["items"][0]


# ════════════════════════════════════════════════════════════════════
#  Permisos
# ════════════════════════════════════════════════════════════════════

def test_la_lista_de_panelistas_de_una_carga_es_la_de_la_seccion_panelistas():
    """DoD: mismos permisos que la sección de panelistas. No por una copia
    de la regla sino porque **es la misma ruta**: el detalle de la carga pide
    `api.panelistas.listar` con `carga_id`, y ninguna ruta bajo `/cargas/`
    devuelve personas."""
    fuente = CARGAS_JS.read_text(encoding="utf-8")
    assert re.search(r"api\.panelistas\.listar\(\s*\{[^}]*carga_id", fuente)
    patrones = [patron for _, _, _, _, patron in ruteo.RUTAS
                if patron.startswith("/cargas")]
    assert not [p for p in patrones if re.search(r"panelista|persona", p)], patrones
    _, permiso, _ = ruteo.resolver("GET", "/panelistas")
    assert permiso == "leer"


def test_sin_rol_no_se_ve_la_lista_ni_las_cargas(ctx, carga):
    """Una sesión sin rol —la del portal— no ve nada, igual que en
    Panelistas."""
    from panel_api.auth import Actor

    sin_rol = Actor(uid="u", email="p@ejemplo.invalid", rol=None)
    for metodo, camino, consulta in (
            ("GET", "/panelistas", {"carga_id": str(carga["id"])}),
            ("GET", f"/cargas/{carga['id']}", {}),
            ("GET", "/cargas", {}),
            ("GET", "/tematicas", {})):
        with pytest.raises(SinPermiso):
            ruteo.despachar(metodo, camino, {}, consulta, sin_rol, ctx)


def test_editar_y_proponer_temáticas_es_de_quien_puede_ingestar(ctx, actor, carga):
    """El dpo lee la sección pero no edita la carga ni gasta en Claude."""
    for metodo, camino, cuerpo in (
            ("PUT", f"/cargas/{carga['id']}/tematicas", {"tematicas": ["tema"]}),
            ("POST", f"/cargas/{carga['id']}/tematicas/propuesta", {})):
        with pytest.raises(SinPermiso):
            ruteo.despachar(metodo, camino, cuerpo, {}, actor("dpo"), ctx)
    ruteo.despachar("GET", f"/cargas/{carga['id']}", {}, {}, actor("dpo"), ctx)


# ════════════════════════════════════════════════════════════════════
#  Configuración: los mismos valores que la verificación
# ════════════════════════════════════════════════════════════════════

def test_modelo_y_tiempo_son_los_de_la_verificacion():
    proponente = tematicas.crear(entorno={
        "CLAUDE_API_KEY": "k", "CLAUDE_MODELO": "modelo-verificacion",
        "VERIFICACION_PRESUPUESTO_S": "45"})
    assert isinstance(proponente, tematicas.Claude)
    assert (proponente.modelo, proponente.tiempo_s) == ("modelo-verificacion", 45)


def test_modelo_y_tiempo_se_pueden_separar():
    proponente = tematicas.crear(entorno={
        "CLAUDE_API_KEY": "k", "CLAUDE_MODELO": "modelo-verificacion",
        "TEMATICAS_MODELO": "otro", "TEMATICAS_TIEMPO_S": "20"})
    assert (proponente.modelo, proponente.tiempo_s) == ("otro", 20)


def test_sin_clave_o_apagado_no_hay_proponente():
    assert not tematicas.crear(entorno={}).disponible
    assert not tematicas.crear(entorno={"CLAUDE_API_KEY": "k",
                                        "TEMATICAS_PROVEEDOR": "ninguno"}).disponible


def test_el_procesamiento_de_la_carga_no_depende_de_las_tematicas(ctx, actor, carga):
    """Confirmar no llama a Claude: la propuesta es de la revisión."""
    transporte = Transporte(error=AssertionError("confirmar no propone"))
    ctx.proponente_tematicas = _claude(transporte)
    _, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta", _cuerpo_de_carga(), {},
        actor("analista"), ctx)
    for indice in range(len(ctx.encolador.encoladas)):
        diferida.procesar_lote(ctx.boveda, ctx.semantica, salida["trabajo_id"],
                               indice, proveedor=ctx.embeddings)
    assert transporte.enviados == []
