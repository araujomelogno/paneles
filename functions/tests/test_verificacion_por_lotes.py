"""SPEC_verificacion_por_lotes — la verificación en lotes, con recuperación
ante truncamiento y el estado `sin_verificar`.

Todo con **respuestas simuladas**: el transporte del proveedor `Claude` se
reemplaza por una función que contesta como la API —con `stop_reason`,
`usage` y un bloque `tool_use`—, así que se prueba el proveedor real, su
lectura de la respuesta y la orquestación de lotes, sin una sola llamada
paga.

Las de §5 que necesitan el pipeline entero (personas en dos lotes, una
contradicción que excluye aunque otra evidencia falle) corren contra el
corpus de la Fase 2.
"""

import json
import re

import pytest

from panel_api import consultas, ruteo, semantica
from panel_api import verificacion as V
from panel_api.errores import SinPermiso

from corpus import CRITERIO_FERNET, nombres, nombres_excluidos, sembrar

CLAVE = "sk-ant-api-CLAVE-QUE-NUNCA-SE-CAPTURA"
LINEA = re.compile(r"^\[(\d+)\] (.*)$")


# ── El doble de la API ───────────────────────────────────────────────

class Respuesta:
    def __init__(self, status_code=200, cuerpo=None, texto=None, headers=None):
        self.status_code = status_code
        self.text = texto if texto is not None else json.dumps(cuerpo)
        self.headers = headers or {}


def _leer(cuerpo):
    """Lo que la API recibiría: criterio y candidatos con su `n` local."""
    datos = json.loads(cuerpo)
    contenido = datos["messages"][0]["content"]
    criterio = contenido.splitlines()[0].removeprefix("Criterio: ")
    candidatos = [
        (int(m.group(1)), m.group(2))
        for m in map(LINEA.match, contenido.splitlines()) if m
    ]
    return datos, criterio, candidatos


def _ok(veredictos, stop="tool_use", salida=100):
    return Respuesta(cuerpo={
        "stop_reason": stop,
        "usage": {"input_tokens": 1000, "output_tokens": salida},
        "content": [{"type": "tool_use", "name": "registrar_veredictos",
                     "input": {"veredictos": veredictos}}],
    })


def _como_lexico(criterio, candidatos):
    """Los veredictos que daría el verificador léxico sobre esos textos: un
    modelo simulado determinístico y que ya sabe de polaridad."""
    juicios = V.Lexico().verificar(criterio, [{"valor_texto": t} for _, t in candidatos])
    return [{"n": n, "veredicto": j["veredicto"], "razon": "simulado"}
            for (n, _), j in zip(candidatos, juicios)]


class Api:
    """Transporte simulado. `decidir(criterio, candidatos, llamada)` devuelve
    una `Respuesta`; por defecto, la del léxico. Anota cada llamada."""

    def __init__(self, decidir=None):
        self.decidir = decidir or (lambda criterio, cands, llamada: _ok(_como_lexico(criterio, cands)))
        self.llamadas = []

    def __call__(self, cuerpo, timeout):
        datos, criterio, candidatos = _leer(cuerpo)
        self.llamadas.append({"cuerpo": cuerpo, "candidatos": candidatos, "timeout": timeout})
        return self.decidir(criterio, candidatos, len(self.llamadas))


def _claude(api, **kw):
    kw.setdefault("tam_lote", 25)
    kw.setdefault("concurrencia", 1)
    return V.Claude(CLAVE, transporte=api, **kw)


def _candidatos(n):
    """`n` evidencias; la de índice múltiplo de 7 niega el criterio."""
    return [
        {"respuesta_id": 1000 + i, "pregunta_texto": "¿Qué opina del fernet?",
         "valor_texto": (f"No me gusta el fernet, caso {i}" if i % 7 == 0
                         else f"Me encanta el fernet, caso {i}")}
        for i in range(n)
    ]


def _esperado(i):
    return V.NO_CUMPLE if i % 7 == 0 else V.CUMPLE


# ════════════════════════════════════════════════════════════════════
#  R-VER.1 / R-VER.2 — lotes, orden y correspondencia
# ════════════════════════════════════════════════════════════════════

def test_cien_evidencias_se_verifican_en_lotes_y_conservan_orden():
    api = Api()
    candidatos = _candidatos(100)
    juicios, informe = _claude(api).verificar_con_informe(CRITERIO_FERNET, candidatos)

    assert len(api.llamadas) == 4, "100 evidencias en lotes de 25 son 4 llamadas"
    assert informe["lotes_iniciales"] == 4
    assert len(juicios) == 100
    for i, (candidato, juicio) in enumerate(zip(candidatos, juicios)):
        assert juicio["evidencia"] == candidato["valor_texto"], f"orden roto en {i}"
        assert juicio["veredicto"] == _esperado(i)
    assert informe["verificadas"] == 100 and informe["sin_verificar"] == 0


def test_los_indices_que_ve_el_modelo_son_locales_al_lote():
    """R-VER.2 — la traducción a índices globales ocurre al combinar, no en
    el prompt: cada lote numera desde 0."""
    api = Api()
    _claude(api, tam_lote=10).verificar(CRITERIO_FERNET, _candidatos(30))
    for llamada in api.llamadas:
        assert [n for n, _ in llamada["candidatos"]] == list(range(10))
    assert "caso 20" in api.llamadas[2]["candidatos"][0][1]


def test_el_tamano_de_lote_se_configura_por_entorno():
    verificador = V.crear(entorno={
        "VERIFICACION_PROVEEDOR": "claude", "CLAUDE_API_KEY": "x",
        "VERIFICACION_LOTE": "7", "VERIFICACION_CONCURRENCIA": "3",
        "VERIFICACION_PRESUPUESTO_S": "45", "VERIFICACION_PROFUNDIDAD_MAX": "4"})
    assert (verificador.tam_lote, verificador.concurrencia,
            verificador.presupuesto_s, verificador.profundidad_max) == (7, 3, 45, 4)
    por_defecto = V.crear(entorno={"VERIFICACION_PROVEEDOR": "claude", "CLAUDE_API_KEY": "x"})
    assert por_defecto.tam_lote == 25


def test_la_concurrencia_no_cambia_el_resultado():
    candidatos = _candidatos(100)
    en_serie = _claude(Api(), concurrencia=1).verificar(CRITERIO_FERNET, candidatos)
    en_paralelo = _claude(Api(), concurrencia=4, tam_lote=9).verificar(CRITERIO_FERNET, candidatos)
    assert [j["veredicto"] for j in en_serie] == [j["veredicto"] for j in en_paralelo]


# ════════════════════════════════════════════════════════════════════
#  R-VER.3 — truncamiento: se descarta y se subdivide
# ════════════════════════════════════════════════════════════════════

def test_un_lote_truncado_se_subdivide_y_sus_veredictos_parciales_no_se_usan():
    """La respuesta truncada trae una herramienta **bien formada** con
    veredictos al revés. Si se aceptara —el bug de origen— la mitad del lote
    saldría con el veredicto equivocado."""

    def decidir(criterio, cands, llamada):
        if len(cands) > 13:
            al_reves = [{"n": n, "veredicto": V.NO_CUMPLE, "razon": "parcial"}
                        for n, _ in cands[:5]]
            return _ok(al_reves, stop="max_tokens", salida=16384)
        return _ok(_como_lexico(criterio, cands))

    api = Api(decidir)
    candidatos = _candidatos(25)
    juicios, informe = _claude(api).verificar_con_informe(CRITERIO_FERNET, candidatos)

    assert informe["subdivisiones"] == 1
    assert len(api.llamadas) == 3, "el truncado y sus dos mitades"
    assert [len(l["candidatos"]) for l in api.llamadas] == [25, 13, 12]
    assert [j["veredicto"] for j in juicios] == [_esperado(i) for i in range(25)]
    assert all(j["razon"] != "parcial" for j in juicios)
    assert informe["stop_reasons"] == {"max_tokens": 1, "tool_use": 2}


def test_un_fallo_persistente_termina_acotado_y_queda_sin_verificar():
    """Siempre trunca: se subdivide hasta una evidencia y ahí para."""
    api = Api(lambda criterio, cands, llamada: _ok([], stop="max_tokens"))
    juicios, informe = _claude(api, profundidad_max=5).verificar_con_informe(
        CRITERIO_FERNET, _candidatos(25))

    assert all(j["veredicto"] == V.SIN_VERIFICAR for j in juicios)
    assert all(j["fallo"] == V.TRUNCAMIENTO for j in juicios)
    # Árbol binario sobre 25 hojas: 2·25 − 1 nodos como mucho.
    assert len(api.llamadas) <= 49
    assert informe["por_fallo"] == {V.TRUNCAMIENTO: 25}


def test_la_profundidad_maxima_corta_la_subdivision():
    api = Api(lambda criterio, cands, llamada: _ok([], stop="max_tokens"))
    _claude(api, profundidad_max=1).verificar(CRITERIO_FERNET, _candidatos(25))
    assert len(api.llamadas) == 3


# ════════════════════════════════════════════════════════════════════
#  R-VER.5 — los modos de falla se distinguen
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("respuesta,fallo", [
    (Respuesta(cuerpo={"stop_reason": "end_turn", "usage": {},
                       "content": [{"type": "text", "text": "No sé"}]}),
     V.HERRAMIENTA_AUSENTE),
    (Respuesta(texto="<html>502 Bad Gateway</html>"), V.RESPUESTA_INVALIDA),
    (Respuesta(status_code=400, cuerpo={"error": {"type": "invalid_request_error"}}),
     V.ERROR_HTTP),
])
def test_cada_modo_de_falla_queda_con_su_nombre(respuesta, fallo):
    api = Api(lambda *a: respuesta)
    juicios = _claude(api, tam_lote=1).verificar(CRITERIO_FERNET, _candidatos(2))
    assert {j["fallo"] for j in juicios} == {fallo}
    assert all(j["veredicto"] == V.SIN_VERIFICAR for j in juicios)


def test_un_error_de_red_es_su_propio_modo():
    def caida(cuerpo, timeout):
        raise ConnectionError("conexión rechazada")

    juicios = V.Claude(CLAVE, transporte=caida, tam_lote=5, concurrencia=1).verificar(
        CRITERIO_FERNET, _candidatos(5))
    assert {j["fallo"] for j in juicios} == {V.ERROR_RED}


def test_se_detectan_indices_duplicados_invalidos_y_faltantes():
    def decidir(criterio, cands, llamada):
        return _ok([
            {"n": 0, "veredicto": V.CUMPLE, "razon": "a"},
            {"n": 0, "veredicto": V.NO_CUMPLE, "razon": "duplicado"},
            {"n": 99, "veredicto": V.CUMPLE, "razon": "fuera de rango"},
            {"n": "x", "veredicto": V.CUMPLE, "razon": "ilegible"},
            {"n": 1, "veredicto": "quizas", "razon": "no permitido"},
            {"n": 2, "veredicto": V.DUDOSO, "razon": "evaluó y no pudo"},
            # el 3 falta
        ])

    juicios, informe = _claude(Api(decidir)).verificar_con_informe(
        CRITERIO_FERNET, _candidatos(4))

    assert [j["veredicto"] for j in juicios] == [
        V.CUMPLE, V.SIN_VERIFICAR, V.DUDOSO, V.SIN_VERIFICAR]
    assert juicios[0]["razon"] == "a", "el primero gana; el duplicado se ignora"
    assert juicios[1]["fallo"] == V.RESPUESTA_INVALIDA
    assert juicios[3]["fallo"] == V.OMITIDO
    assert informe["anomalias"] == {
        "duplicados": 1, "fuera_de_rango": 1, "ilegibles": 1,
        "veredicto_no_permitido": 1, "omitidos": 1}


def test_dudoso_y_sin_verificar_son_distintos():
    """R-VER.6 — `dudoso` es un juicio; `sin_verificar`, su ausencia."""
    juicios = V.NoDisponible("sin clave").verificar(CRITERIO_FERNET, _candidatos(2))
    assert {j["veredicto"] for j in juicios} == {V.SIN_VERIFICAR}
    assert V.SIN_VERIFICAR not in V.VEREDICTOS, "el modelo no puede devolverlo"
    assert V.SIN_VERIFICAR not in V.HERRAMIENTA["input_schema"]["properties"][
        "veredictos"]["items"]["properties"]["veredicto"]["enum"]


# ════════════════════════════════════════════════════════════════════
#  R-VER.4 / R-VER.6 — presupuesto y fallas parciales
# ════════════════════════════════════════════════════════════════════

def test_los_lotes_exitosos_se_conservan_ante_fallas_parciales():
    def decidir(criterio, cands, llamada):
        if llamada == 2:
            return Respuesta(status_code=401, cuerpo={"error": "no autorizado"})
        return _ok(_como_lexico(criterio, cands))

    juicios, informe = _claude(Api(decidir), tam_lote=10).verificar_con_informe(
        CRITERIO_FERNET, _candidatos(30))

    assert [j["veredicto"] for j in juicios[:10]] == [_esperado(i) for i in range(10)]
    assert {j["veredicto"] for j in juicios[10:20]} == {V.SIN_VERIFICAR}
    assert [j["veredicto"] for j in juicios[20:]] == [_esperado(i) for i in range(20, 30)]
    assert informe["por_fallo"] == {V.ERROR_HTTP: 10}


def test_un_error_transitorio_se_reintenta_una_vez():
    def decidir(criterio, cands, llamada):
        if llamada == 1:
            return Respuesta(status_code=529, cuerpo={"error": "overloaded"},
                             headers={"retry-after": "0"})
        return _ok(_como_lexico(criterio, cands))

    api = Api(decidir)
    juicios, informe = _claude(api).verificar_con_informe(CRITERIO_FERNET, _candidatos(5))
    assert len(api.llamadas) == 2 and informe["reintentos"] == 1
    assert all(j["veredicto"] in V.VEREDICTOS for j in juicios)


def test_un_error_transitorio_que_persiste_no_se_reintenta_sin_fin():
    api = Api(lambda *a: Respuesta(status_code=503, cuerpo={}, headers={"retry-after": "0"}))
    juicios = _claude(api).verificar(CRITERIO_FERNET, _candidatos(5))
    assert len(api.llamadas) == 1 + V.REINTENTOS_TRANSITORIOS
    assert {j["fallo"] for j in juicios} == {V.ERROR_HTTP}


def test_se_respeta_el_presupuesto_total_de_tiempo():
    """Con un reloj simulado: cada llamada «tarda» 20 s y el presupuesto es
    de 50. Entran dos lotes; el tercero sale con los 10 s que quedan y corta
    por timeout, y el resto queda sin verificar por tiempo sin haber llamado
    a la API."""
    reloj = {"t": 0.0}
    llamadas = []

    def llamar(criterio, cands, lote, tiempo_max):
        llamadas.append(lote.id)
        assert tiempo_max <= 50 - reloj["t"] + 1e-9, "cada llamada lleva lo que queda"
        if tiempo_max < 20:
            reloj["t"] += tiempo_max
            raise V.FalloLote(V.ERROR_RED, "timeout", transitorio=True)
        reloj["t"] += 20
        return {"crudos": [{"n": i, "veredicto": V.CUMPLE, "razon": "ok"}
                           for i in range(len(cands))], "stop_reason": "tool_use"}

    juicios, informe = V.verificar_por_lotes(
        CRITERIO_FERNET, _candidatos(100), llamar, tam_lote=25, concurrencia=1,
        limite=50, reloj=lambda: reloj["t"], dormir=lambda s: None)

    assert llamadas == ["L1", "L2", "L3"]
    assert reloj["t"] <= 50
    assert informe["presupuesto_agotado"] is True
    assert [j["veredicto"] for j in juicios[:50]] == [V.CUMPLE] * 50
    assert {j["fallo"] for j in juicios[50:]} == {V.PRESUPUESTO_AGOTADO}


def test_el_timeout_de_cada_llamada_no_pasa_el_presupuesto():
    api = Api()
    verificador = _claude(api, presupuesto_s=10)
    verificador.tiempo = 120
    verificador.verificar(CRITERIO_FERNET, _candidatos(3))
    assert api.llamadas[0]["timeout"] <= 10


def test_el_diagnostico_no_lleva_contenido(capsys):
    """R-VER.9 — métricas sí; criterio, evidencias y clave, no."""
    _claude(Api()).verificar_con_informe(CRITERIO_FERNET, _candidatos(30), etiqueta="C1")
    linea = capsys.readouterr().out
    assert "[verificacion]" in linea
    datos = json.loads(linea.split("[verificacion] ", 1)[1].splitlines()[0])
    for clave in ("lotes_iniciales", "verificadas", "sin_verificar", "subdivisiones",
                  "duracion_ms", "stop_reasons", "tokens"):
        assert clave in datos
    assert datos["tokens"]["salida"] > 0
    assert "fernet" not in linea.lower()
    assert CLAVE not in linea


# ════════════════════════════════════════════════════════════════════
#  R-VER.7 — la combinación en consultas.py
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def corpus(conn_boveda, conn_semantica, proveedor):
    contexto = sembrar(conn_boveda, conn_semantica, proveedor)
    conn_boveda.commit()
    conn_semantica.commit()
    return contexto


def test_una_persona_con_evidencias_en_dos_lotes_recibe_todas_bien_atribuidas(ctx, corpus):
    """Con lotes de una evidencia cada persona queda repartida entre lotes.
    El resultado tiene que ser idéntico al de verificar todo junto."""
    junto = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET], "modo": "laxo"})

    ctx._verificador = _claude(Api(), tam_lote=1)
    en_lotes = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET], "modo": "laxo"})

    def por_persona(resultado):
        return {
            item["id_persona"]: [c["veredicto"] for c in item["criterios"]]
            for item in resultado["items"]
        }

    assert por_persona(en_lotes) == por_persona(junto)
    assert nombres_excluidos(en_lotes, corpus) == nombres_excluidos(junto, corpus)
    assert en_lotes["verificacion"]["completa"] is True
    assert en_lotes["verificacion"]["evidencias"] > len(en_lotes["items"]), (
        "hay personas con más de una evidencia, o la prueba no prueba nada")


def test_una_contradiccion_valida_excluye_aunque_otra_evidencia_falle(ctx, corpus):
    """Beto: «fernet» a qué toma (falla la verificación) y «no me gusta» a
    por qué (no_cumple válido). Sigue afuera."""

    def decidir(criterio, cands, llamada):
        _, texto = cands[0]
        if "no me gusta" in texto.lower():
            return _ok(_como_lexico(criterio, cands))
        return Respuesta(status_code=500, cuerpo={})

    ctx._verificador = _claude(Api(decidir), tam_lote=1)
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})

    assert nombres_excluidos(resultado, corpus).get("Beto Silva") == V.NO_CUMPLE
    assert resultado["verificacion"]["completa"] is False


def test_una_evidencia_sin_verificar_no_es_rechazo_ni_aprobacion(ctx, corpus):
    """En modo estricto, todo sin verificar: nadie se excluye por veredicto,
    nadie figura como `cumple`, y todos quedan marcados."""
    api = Api(lambda *a: _ok([], stop="max_tokens"))
    ctx._verificador = _claude(api, profundidad_max=0)
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET], "modo": "estricto"})

    assert resultado["items"], "sin_verificar no excluye"
    assert not [e for e in resultado["excluidos"]
                if e["motivo"] in (V.NO_CUMPLE, V.DUDOSO, V.SIN_VERIFICAR)]
    for item in resultado["items"]:
        semanticos = [c for c in item["criterios"] if c["tipo"] == "semantico"]
        assert all(c["veredicto"] == V.SIN_VERIFICAR for c in semanticos)
        assert item["verificacion_incompleta"] is True
        assert item["confianza"] == "baja"
    parcial = [d for d in resultado["degradaciones"] if d.get("parcial")]
    assert parcial and "sin verificar" in parcial[0]["motivo"]
    assert resultado["verificacion"]["personas_con_pendientes"] == len(resultado["items"])


def test_dudoso_sigue_excluyendo_en_estricto_aunque_otro_lote_falle(ctx, corpus):
    """R-VER.7 — una falla parcial no desactiva las reglas de los veredictos
    válidos. Antes, cualquier degradación de la verificación apagaba la
    regla del `dudoso` para toda la consulta."""

    def decidir(criterio, cands, llamada):
        if llamada == 1:
            return Respuesta(status_code=400, cuerpo={})
        return _ok([{"n": n, "veredicto": V.DUDOSO, "razon": "no alcanza"}
                    for n, _ in cands])

    ctx._verificador = _claude(Api(decidir), tam_lote=1)
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET], "modo": "estricto"})
    motivos = {e["motivo"] for e in resultado["excluidos"]}
    assert V.DUDOSO in motivos


def test_la_persona_con_pendientes_no_queda_validada_aunque_otra_diga_cumple(ctx, corpus):
    def decidir(criterio, cands, llamada):
        if llamada % 2 == 0:
            return Respuesta(status_code=400, cuerpo={})
        return _ok([{"n": n, "veredicto": V.CUMPLE, "razon": "sí"} for n, _ in cands])

    ctx._verificador = _claude(Api(decidir), tam_lote=1)
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET], "modo": "laxo"})
    mixtos = [
        i for i in resultado["items"]
        if any(c["veredicto"] == V.CUMPLE and c.get("pendientes_de_verificar")
               for c in i["criterios"])
    ]
    assert mixtos, "la prueba necesita al menos una persona con cumple + pendiente"
    assert all(i["verificacion_incompleta"] and i["confianza"] == "baja" for i in mixtos)


def test_el_presupuesto_es_de_la_consulta_y_no_de_cada_criterio(ctx, corpus, monkeypatch):
    vistos = []
    original = V.Claude.verificar_con_informe

    def espiar(self, criterio, candidatos, limite=None, captura=None, etiqueta=None):
        vistos.append(limite)
        return original(self, criterio, candidatos, limite=limite, captura=captura,
                        etiqueta=etiqueta)

    monkeypatch.setattr(V.Claude, "verificar_con_informe", espiar)
    ctx._verificador = _claude(Api(), presupuesto_s=60)
    consultas.ejecutar(ctx, {"criterios": [
        CRITERIO_FERNET, {"tipo": "semantico", "texto": "gente que toma mate"}]})
    assert len(vistos) == 2 and vistos[0] is not None and vistos[0] == vistos[1]


# ════════════════════════════════════════════════════════════════════
#  R-VER.10 — modo de depuración
# ════════════════════════════════════════════════════════════════════

def _con_truncado_y_no_json(criterio, cands, llamada):
    if llamada == 1:
        return _ok([{"n": 0, "veredicto": V.CUMPLE, "razon": "parcial"}], stop="max_tokens")
    if llamada == 2:
        return Respuesta(status_code=502, texto="<html>Bad Gateway</html>",
                         headers={"retry-after": "0"})
    return _ok(_como_lexico(criterio, cands))


def test_con_el_modo_encendido_cada_llamada_deja_su_par(ctx, corpus, conn_semantica,
                                                        monkeypatch):
    monkeypatch.setenv("VERIFICACION_DEPURACION", "1")
    api = Api(_con_truncado_y_no_json)
    ctx._verificador = _claude(api)
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})

    ejecucion = resultado["diagnostico"]["ejecucion_id"]
    assert resultado["diagnostico"]["captura_depuracion"] is True
    capturas = V.capturas_de(conn_semantica, ejecucion)

    assert len(capturas) == len(api.llamadas), "lotes, mitades y reintentos: todas"
    assert [c["solicitud"] for c in capturas] == [l["cuerpo"] for l in api.llamadas], (
        "el cuerpo capturado es exactamente el que se envió")
    truncada = capturas[0]
    assert truncada["resultado"] == V.TRUNCAMIENTO
    assert '"max_tokens"' in truncada["respuesta_api"], "la truncada se guarda igual"
    no_json = next(c for c in capturas if c["estado_http"] == 502)
    assert no_json["respuesta_es_json"] is False
    assert no_json["respuesta_api"] == "<html>Bad Gateway</html>"
    assert {c["lote"] for c in capturas} >= {"L1", "L1.1", "L1.2"}
    reintento = [c for c in capturas if c["intento"] == 2]
    assert reintento and reintento[0]["lote_padre"] == "L1"


def test_la_api_key_no_aparece_en_ninguna_captura(ctx, corpus, conn_semantica, monkeypatch):
    monkeypatch.setenv("VERIFICACION_DEPURACION", "si")
    ctx._verificador = _claude(Api(_con_truncado_y_no_json))
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    filas = conn_semantica.execute(
        "select row_to_json(c)::text as t from verificacion_captura c "
        "where ejecucion_id = %s", (resultado["diagnostico"]["ejecucion_id"],)).fetchall()
    assert filas
    assert all(CLAVE not in f["t"] and "x-api-key" not in f["t"] for f in filas)


def test_la_captura_no_agrega_identificadores_al_payload(ctx, corpus, conn_semantica,
                                                         monkeypatch):
    monkeypatch.setenv("VERIFICACION_DEPURACION", "1")
    ctx._verificador = _claude(Api())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    for captura in V.capturas_de(conn_semantica, resultado["diagnostico"]["ejecucion_id"]):
        for id_persona in corpus["por_id"]:
            assert str(id_persona) not in captura["solicitud"]


def test_con_el_modo_apagado_no_se_guarda_nada(ctx, corpus, conn_semantica, monkeypatch):
    monkeypatch.delenv("VERIFICACION_DEPURACION", raising=False)
    ctx._verificador = _claude(Api())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    assert resultado["diagnostico"]["captura_depuracion"] is False
    total = conn_semantica.execute(
        "select count(*) as n from verificacion_captura").fetchone()["n"]
    assert total == 0


def test_las_capturas_vencen_y_se_purgan(ctx, corpus, conn_semantica, monkeypatch):
    monkeypatch.setenv("VERIFICACION_DEPURACION", "1")
    ctx._verificador = _claude(Api())
    ejecucion = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})["diagnostico"]["ejecucion_id"]
    assert V.capturas_de(conn_semantica, ejecucion)
    vence = conn_semantica.execute(
        "select min(vence_en - creado_en) as d from verificacion_captura").fetchone()["d"]
    assert vence.days == V.DIAS_DE_CAPTURA

    conn_semantica.execute(
        "update verificacion_captura set vence_en = now() - interval '1 minute'")
    conn_semantica.commit()
    assert V.capturas_de(conn_semantica, ejecucion) == []
    assert conn_semantica.execute(
        "select count(*) as n from verificacion_captura").fetchone()["n"] == 0


def test_una_captura_que_supera_el_tope_se_trunca_y_lo_informa():
    captura = V.Captura("00000000-0000-0000-0000-000000000000",
                        max_bytes_entrada=100, max_bytes_ejecucion=250)
    lote = V.Lote("L1", 0, 3)
    for _ in range(3):
        captura.registrar(criterio="c", lote=lote, respuesta_ids=[1, 2, 3],
                          solicitud="x" * 500, estado_http=200, respuesta="y" * 500,
                          resultado="ok", duracion_ms=5)
    primera, _, tercera = captura.filas
    assert primera["solicitud_truncada"] and len(primera["solicitud"]) == 100
    assert primera["solicitud_bytes"] == 500, "se informa el tamaño original"
    assert tercera["omitida_por_tope"] is True
    assert tercera["solicitud"] is None and tercera["respuesta_api"] is None


def test_un_no_admin_no_accede_a_las_capturas(ctx, actor):
    ruta = "/consultas/capturas/00000000-0000-0000-0000-000000000000"
    for rol in ("operaciones", "analista", "dpo"):
        with pytest.raises(SinPermiso):
            ruteo.despachar("GET", ruta, {}, {}, actor(rol), ctx)
    status, salida = ruteo.despachar("GET", ruta, {}, {}, actor("admin"), ctx)
    assert status == 200 and salida["capturas"] == []
    assert "apagado" in salida["explicacion"], (
        "sin capturas se dice por qué, en vez de mostrarse vacío")


def test_la_baja_se_lleva_las_capturas_con_sus_respuestas(ctx, corpus, conn_semantica,
                                                          monkeypatch):
    monkeypatch.setenv("VERIFICACION_DEPURACION", "1")
    ctx._verificador = _claude(Api(), tam_lote=1)
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO_FERNET]})
    ejecucion = resultado["diagnostico"]["ejecucion_id"]
    antes = len(V.capturas_de(conn_semantica, ejecucion))
    beto = corpus["por_nombre"]["Beto Silva"]

    semantica.borrar_persona(conn_semantica, beto)
    conn_semantica.commit()

    despues = V.capturas_de(conn_semantica, ejecucion)
    assert 0 < len(despues) < antes
    assert all("lo detesto" not in (c["solicitud"] or "") for c in despues)
