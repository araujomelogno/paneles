"""R-CS — recuperación y verificación de consultas semánticas.

`specs/ADDENDUM_solicitud_consultas_semanticas.md`, en el orden del addendum:

* cambio 1 / A5 — `input_type` del criterio, configurable y reversible;
* cambio 3 — unidades de evidencia: 25 respuestas iguales cuestan una
  verificación y no se comen los lugares;
* cambio 4 — `irrelevante`, y que corrige la agregación;
* A5 — evidencias contradictorias dentro de la misma persona;
* A1.3 — el diagnóstico de cada ejecución queda registrado;
* A2 — estimación, presupuesto obligatorio y costo real;
* A4 — el contrato que lee COLOQUIO (`detalle`, `estado`, versión);
* A6 — `limite` y el recorte visible;
* cambio 5 — la ejecución completa: exhaustiva, paginada, con presupuesto,
  gate re-evaluado y reintento, sobre el fixture de 137 personas.

Los proveedores de rerank y verificación que importan acá son dobles con
reglas explícitas: lo que se prueba es la selección y la agregación, no la
calidad de un modelo.
"""

import pytest

from panel_api import (
    auth,
    consentimiento,
    consulta_completa,
    consultas,
    costo_consulta,
    diferida,
    embeddings as mod_embeddings,
    encuestas,
    paneles,
    personas,
    ruteo,
    semantica,
    verificacion as mod_verificacion,
)
from panel_api import reranker as mod_reranker
from panel_api.errores import Conflicto, DatosInvalidos, SinPermiso

from conftest import ContextoDePrueba
from corpus import VERSION

CRITERIO = "gente que usa un celular xiaomi"
CONTRATO = "Soy el titular del contrato"
MARCA = "Tengo un Xiaomi"

PREGUNTAS = [
    {"codigo": "T1", "texto": "¿Quién es el titular de la línea?", "tipo": "abierta",
     "orden": 1},
    {"codigo": "M1", "texto": "¿Qué marca de celular usa?", "tipo": "abierta", "orden": 2},
]


# ── Dobles con reglas a la vista ─────────────────────────────────────

class RerankerPorPalabra(mod_reranker.Reranker):
    """Pone primero lo que lleva «titular»: reproduce el caso del addendum,
    donde la respuesta repetida e irrelevante ocupa el tope del ranking."""

    nombre = "por_palabra"

    def __init__(self, parcial=False):
        self.parcial = parcial
        self.llamadas = []

    def reordenar(self, criterio, textos, top_k=None):
        self.llamadas.append(list(textos))
        puntajes = [(i, 0.9 if "titular" in t.lower() else 0.5) for i, t in enumerate(textos)]
        if self.parcial:
            puntajes = [(i, None if i % 2 else p) for i, p in puntajes]
        return sorted(puntajes, key=lambda par: (-(par[1] or 0), par[0]))


class VerificadorPorPalabra(mod_verificacion.Verificador):
    """`titular` → irrelevante; `no uso` → no_cumple; `xiaomi` → cumple;
    lo demás, dudoso. Anota cada texto que se le mandó a juzgar."""

    nombre = "por_palabra"

    def __init__(self):
        self.vistos = []

    def verificar(self, criterio, candidatos):
        crudos = []
        for n, c in enumerate(candidatos):
            texto = (c.get("valor_texto") or "").lower()
            self.vistos.append(texto)
            if "titular" in texto:
                v = mod_verificacion.IRRELEVANTE
            elif "no uso" in texto:
                v = mod_verificacion.NO_CUMPLE
            elif "xiaomi" in texto:
                v = mod_verificacion.CUMPLE
            else:
                v = mod_verificacion.DUDOSO
            crudos.append({"n": n, "veredicto": v, "razon": f"regla: {v}"})
        return mod_verificacion._completar(crudos, candidatos, criterio)


# ── El corpus: N personas, dos preguntas ─────────────────────────────

def _sembrar(conn_boveda, conn_semantica, proveedor, filas):
    """`filas`: [(T1, M1)] por persona. Devuelve los `id_persona` en orden."""
    panel = paneles.crear(conn_boveda, "Panel Celulares", "R-CS")
    ids = []
    for i, _ in enumerate(filas):
        alta = personas.alta(conn_boveda, {
            "persona": {"nombre": f"Persona {i:03d}", "sexo": "F" if i % 2 else "M",
                        "fecha_nacimiento": "1990-01-01", "localidad": "Montevideo",
                        "email": f"p{i:03d}@ejemplo.uy"},
            "consentimientos": [{"finalidad": f, "version_texto": VERSION}
                                for f in ("contacto_participacion", "uso_semantico")],
            "origen": "dooblo", "id_en_origen": f"C-{i:03d}", "panel_id": panel["id"],
        }, actor="prueba")
        ids.append(alta["id_persona"])
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola Celulares", "2026-09-01")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    encuestas.ingestar(
        conn_boveda, conn_semantica, encuesta["id"], PREGUNTAS,
        [{"id_en_origen": f"C-{i:03d}", "T1": t, "M1": m}
         for i, (t, m) in enumerate(filas)],
        proveedor=proveedor)
    conn_boveda.commit()
    conn_semantica.commit()
    return ids


@pytest.fixture
def contrato(conn_boveda, conn_semantica, proveedor):
    """El caso del addendum: 25 personas con la misma respuesta sobre el
    contrato y una sola que menciona la marca."""
    filas = [(CONTRATO, "") for _ in range(25)] + [("", MARCA)]
    return _sembrar(conn_boveda, conn_semantica, proveedor, filas)


def _ctx(conn_boveda, conn_semantica, proveedor, **kw):
    return ContextoDePrueba(conn_boveda, conn_semantica, proveedor, **kw)


# ════════════════════════════════════════════════════════════════════
#  Cambio 1 / A5 — input_type del criterio
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def voyage_espiado(monkeypatch):
    enviados = []

    class RespuestaFalsa:
        status_code = 200
        text = ""

        def __init__(self, n):
            self.n = n

        def json(self):
            return {"data": [{"index": i, "embedding": [0.0] * 512} for i in range(self.n)]}

    def post_falso(url, headers=None, json=None, timeout=None):
        enviados.append(json)
        return RespuestaFalsa(len(json["input"]))

    import requests

    monkeypatch.setattr(requests, "post", post_falso)
    return mod_embeddings.Voyage(api_key="x"), enviados


def test_el_criterio_se_embebe_como_consulta_y_las_respuestas_como_documento(
        voyage_espiado, monkeypatch):
    proveedor, enviados = voyage_espiado
    monkeypatch.delenv("EMBEDDINGS_TIPO_CONSULTA", raising=False)
    proveedor.embeber(["una respuesta"])
    proveedor.embeber_criterio(["un criterio"], tipo=mod_embeddings.tipo_de_criterio())
    assert [e["input_type"] for e in enviados] == ["document", "query"]


def test_el_input_type_del_criterio_se_vuelve_atras_sin_tocar_codigo(monkeypatch):
    """A5 — «poder volver atrás con configuración, sin redesplegar»: el
    entorno y la consulta mandan, en ese orden inverso."""
    monkeypatch.setenv("EMBEDDINGS_TIPO_CONSULTA", "document")
    assert mod_embeddings.tipo_de_criterio() == "document"
    assert mod_embeddings.tipo_de_criterio(pedido="query") == "query"
    monkeypatch.setenv("EMBEDDINGS_TIPO_CONSULTA", "cualquiera")
    assert mod_embeddings.tipo_de_criterio() == "query"
    with pytest.raises(DatosInvalidos):
        consultas.normalizar_definicion(
            {"criterios": ["x"], "tipo_embedding_criterio": "otro"})


def test_el_diagnostico_dice_con_que_input_type_corrio(
        conn_boveda, conn_semantica, proveedor, contrato):
    ctx = _ctx(conn_boveda, conn_semantica, proveedor)
    resultado = consultas.ejecutar(
        ctx, {"criterios": [CRITERIO], "tipo_embedding_criterio": "document"})
    assert resultado["diagnostico"]["tipo_embedding_criterio"] == "document"
    embedding = next(e for e in resultado["diagnostico"]["etapas"] if e["etapa"] == "embedding")
    assert embedding["input_type"] == "document"


# ════════════════════════════════════════════════════════════════════
#  Cambio 3 — unidades de evidencia
# ════════════════════════════════════════════════════════════════════

def test_la_huella_de_la_migracion_es_la_misma_que_la_de_python(conn_semantica):
    """El backfill de la semantica/0009 calcula `hash_texto` en SQL; la
    ingesta, en Python. Si divergieran, la misma respuesta sería dos
    unidades distintas según cuándo entró."""
    texto = "¿Qué marca usa? → Ñandú, «Xiaomi» y €"
    fila = semantica.db.una(
        conn_semantica,
        "select encode(sha256(convert_to(%s, 'UTF8')), 'hex') as h", (texto,))
    assert fila["h"] == semantica.hash_texto(texto)


def test_las_respuestas_repetidas_se_verifican_una_vez_y_liberan_sus_lugares(
        conn_boveda, conn_semantica, proveedor, contrato):
    """El caso que motivó la solicitud. Con `top_k = 3`, antes se
    verificaban 3 personas con la respuesta del contrato —3 veces el mismo
    texto— y la de la marca nunca llegaba. Ahora el texto del contrato es
    una unidad, se juzga una vez como irrelevante, y la selección va a
    buscar a la siguiente persona: la que usa Xiaomi."""
    verificador = VerificadorPorPalabra()
    ctx = _ctx(conn_boveda, conn_semantica, proveedor,
               reranker=RerankerPorPalabra(), verificador=verificador)
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO], "top_k": 3,
                                         "modo": "laxo"})

    assert [i["id_persona"] for i in resultado["items"]] == [contrato[-1]]
    assert resultado["items"][0]["estado"] == consultas.CONFIRMADA
    vistos_del_contrato = [t for t in verificador.vistos if "titular" in t]
    assert len(vistos_del_contrato) == 1, "el texto repetido se verifica una sola vez"

    unidades = next(e for e in resultado["diagnostico"]["etapas"] if e["etapa"] == "unidades")
    assert unidades["repetidas"] >= 24
    informe = resultado["diagnostico"]["verificacion"][0]
    assert informe["irrelevantes"] >= 1
    assert informe["rondas"] >= 2
    assert informe["respuestas_cubiertas"] >= 25


def test_el_reranker_recibe_cada_texto_una_vez(conn_boveda, conn_semantica, proveedor,
                                               contrato):
    reranker = RerankerPorPalabra()
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, reranker=reranker,
               verificador=VerificadorPorPalabra())
    consultas.ejecutar(ctx, {"criterios": [CRITERIO]})
    textos = reranker.llamadas[0]
    assert len(textos) == len(set(textos))


def test_un_reranker_que_devuelve_puntajes_a_medias_degrada_y_lo_dice(
        conn_boveda, conn_semantica, proveedor, contrato):
    """A1.3 — un reranker que «corrió» pero no devolvió puntajes para todo
    era una degradación silenciosa: se descartaban los `None` y se seguía
    con el orden de la distancia sin avisar."""
    ctx = _ctx(conn_boveda, conn_semantica, proveedor,
               reranker=RerankerPorPalabra(parcial=True),
               verificador=VerificadorPorPalabra())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO]})
    degradadas = [d for d in resultado["degradaciones"] if d["etapa"] == "reranking"]
    assert degradadas and "puntaje para" in degradadas[0]["motivo"]


# ════════════════════════════════════════════════════════════════════
#  Cambio 4 y A5 — irrelevante, y la contradicción dentro de una persona
# ════════════════════════════════════════════════════════════════════

def test_irrelevante_no_excluye_como_no_cumple_ni_entra_como_dudoso(
        conn_boveda, conn_semantica, proveedor, contrato):
    """Las 25 personas del contrato no contradicen el criterio: no van a
    excluidos como `no_cumple` (eso sería decir que no usan Xiaomi) y en laxo
    tampoco aparecen penalizadas (no hay ninguna evidencia del tema)."""
    ctx = _ctx(conn_boveda, conn_semantica, proveedor,
               reranker=RerankerPorPalabra(), verificador=VerificadorPorPalabra())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO], "modo": "laxo",
                                         "top_k": 30})
    motivos = {e["motivo"] for e in resultado["excluidos"]}
    assert mod_verificacion.NO_CUMPLE not in motivos
    assert motivos <= {consultas.SIN_EVIDENCIA_PERTINENTE}
    assert len(resultado["items"]) == 1


def test_evidencias_contradictorias_de_la_misma_persona(conn_boveda, conn_semantica,
                                                        proveedor):
    """A5 — el caso que el fixture de 137 no prueba: un `cumple` y un
    `no_cumple` pertinentes de la misma persona. Manda el `no_cumple`, en los
    dos modos, y la contradicción queda marcada en vez de esconderse detrás
    de un conteo. Quien tiene un `cumple` y un `irrelevante` queda
    confirmada: lo irrelevante no diluye."""
    ids = _sembrar(conn_boveda, conn_semantica, proveedor, [
        ("No uso celular de esa marca", MARCA),     # se contradice
        (CONTRATO, MARCA),                          # cumple + irrelevante
    ])
    ctx = _ctx(conn_boveda, conn_semantica, proveedor,
               reranker=RerankerPorPalabra(), verificador=VerificadorPorPalabra())
    for modo in consultas.MODOS:
        resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO], "modo": modo})
        excluido = next(e for e in resultado["excluidos"] if e["id_persona"] == ids[0])
        assert excluido["motivo"] == mod_verificacion.NO_CUMPLE
        assert excluido["contradiccion"] is True
        assert excluido["estado"] == consultas.DESCARTADA
        item = next(i for i in resultado["items"] if i["id_persona"] == ids[1])
        assert item["estado"] == consultas.CONFIRMADA
        assert item["penalizado"] is False


# ════════════════════════════════════════════════════════════════════
#  A4 · A6 · A1.3 · A2 en la exploratoria
# ════════════════════════════════════════════════════════════════════

def test_el_contrato_que_lee_coloquio(conn_boveda, conn_semantica, proveedor, contrato):
    """A4 — COLOQUIO lee `item["detalle"]`; paneles lo llamaba `criterios`.
    Van los dos, con el mismo contenido, y la versión del contrato."""
    ctx = _ctx(conn_boveda, conn_semantica, proveedor,
               reranker=RerankerPorPalabra(), verificador=VerificadorPorPalabra())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO]})
    assert resultado["version_contrato"] == consultas.VERSION_CONTRATO
    assert resultado["alcance"] == consultas.EXPLORATORIO
    for item in resultado["items"]:
        assert item["detalle"] == item["criterios"]
        assert item["estado"] in (consultas.CONFIRMADA, consultas.POSIBLE,
                                  consultas.PENDIENTE)
        for criterio in item["detalle"]:
            assert criterio["estado"]


def test_limite_recorta_y_el_recorte_se_ve(conn_boveda, conn_semantica, proveedor):
    """A6 — `limite` se respeta y el resultado dice cuántas personas se
    verificaron frente a cuántas se muestran: el recorte no se confunde con
    «no hay más gente que cumpla»."""
    ids = _sembrar(conn_boveda, conn_semantica, proveedor,
                   [("", f"{MARCA} modelo {i}") for i in range(6)])
    ctx = _ctx(conn_boveda, conn_semantica, proveedor,
               reranker=RerankerPorPalabra(), verificador=VerificadorPorPalabra())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO], "top_k": 6, "limite": 2})
    assert len(resultado["items"]) == 2
    recorte = resultado["recorte"]
    assert recorte == {**recorte, "personas_verificadas": len(ids),
                       "personas_pertinentes": len(ids), "en_ranking": 6,
                       "mostradas": 2, "recortado": True, "limite": 2, "top_k": 6}
    assert resultado["parametros"]["limite"] == 2


def test_cada_ejecucion_deja_su_diagnostico(conn_boveda, conn_semantica, proveedor,
                                            contrato):
    """A1.3 — «¿el reranker corrió en esa consulta?» se contesta con una fila
    de `consulta_ejecucion`, no con el JSON que vio el navegador. Sin el
    resultado: ni personas ni evidencias."""
    ctx = _ctx(conn_boveda, conn_semantica, proveedor,
               reranker=mod_reranker.NoDisponible("sin clave"),
               verificador=VerificadorPorPalabra())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO]})
    fila = semantica.db.una(
        conn_boveda, "select * from consulta_ejecucion where id = %s",
        (resultado["diagnostico"]["ejecucion_id"],))
    assert fila["alcance"] == "exploratorio" and fila["estado"] == "terminada"
    diagnostico = fila["diagnostico"]
    assert diagnostico["reranker"] == "ninguno"
    assert any(d["etapa"] == "reranking" for d in diagnostico["degradaciones"])
    texto = str(diagnostico)
    assert all(i not in texto for i in contrato), "el diagnóstico no lleva personas"


def test_el_costo_real_sale_de_los_tokens(conn_boveda, conn_semantica, proveedor, contrato):
    class ConTokens(VerificadorPorPalabra):
        def verificar_con_informe(self, criterio, candidatos, **kw):
            juicios, informe = super().verificar_con_informe(criterio, candidatos, **kw)
            informe["tokens"] = {"entrada": 1_000_000, "salida": 100_000}
            return juicios, informe

    ctx = _ctx(conn_boveda, conn_semantica, proveedor, reranker=RerankerPorPalabra(),
               verificador=ConTokens())
    resultado = consultas.ejecutar(ctx, {"criterios": [CRITERIO], "top_k": 1})
    tarifas = costo_consulta.Tarifas.desde_entorno()
    rondas = resultado["diagnostico"]["verificacion"][0]["rondas"]
    esperado = rondas * (tarifas.claude_entrada + tarifas.claude_salida / 10)
    assert resultado["costo"]["usd"] == pytest.approx(esperado, rel=0.01)


def test_la_estimacion_crece_con_las_unidades_y_se_calibra():
    tarifas = costo_consulta.Tarifas()
    chica = costo_consulta.estimar([{"criterio": "x", "unidades": 25}], 25, tarifas)
    grande = costo_consulta.estimar([{"criterio": "x", "unidades": 2500}], 25, tarifas)
    assert chica["llamadas"] == 1 and grande["llamadas"] == 100
    assert grande["usd"] > 50 * chica["usd"]
    assert grande["usd"] == pytest.approx(grande["usd_central"] * costo_consulta.MARGEN,
                                          abs=1e-3)
    calibrada = costo_consulta.estimar(
        [{"criterio": "x", "unidades": 100}], 25, tarifas,
        calibrado={"entrada_por_unidad": 100, "salida_por_unidad": 50,
                   "rerank_por_unidad": 0})
    assert calibrada["calibrada"] is True
    assert calibrada["usd_central"] == pytest.approx(
        (10_000 * 2 + 5_000 * 10) / 1e6 + 1 * tarifas.embedding / 1e6, rel=0.01)


# ════════════════════════════════════════════════════════════════════
#  Cambio 5 — la ejecución completa
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def ciento_treinta_y_siete(conn_boveda, conn_semantica, proveedor):
    """El fixture de aceptación: 137 personas que cumplen —más de cinco
    veces el `top_k` por defecto, y más de dos páginas de 50—. Para que no
    sea solo un conteo, hay textos repetidos (unidades compartidas), uno
    irrelevante para todos y una contradicción."""
    filas = []
    for i in range(137):
        marca = MARCA if i % 3 else f"{MARCA}, el modelo {i % 7}"
        filas.append((CONTRATO, marca))
    filas.append(("No uso celular de esa marca", MARCA))     # contradictoria
    filas.append((CONTRATO, ""))                             # solo irrelevante
    return _sembrar(conn_boveda, conn_semantica, proveedor, filas)


def _lanzar(ctx, encolador, **extra):
    return consulta_completa.lanzar(ctx, {
        "criterios": [CRITERIO], "presupuesto_usd": 5, "confirmar_costo": True,
        "limite": 50, **extra}, actor="prueba", encolador=encolador,
        verificador=ctx.verificador)


def _procesar_todo(ctx, encolador, ejecucion_id):
    for (_, indice) in list(encolador.encoladas):
        consulta_completa.procesar_lote(
            ctx.boveda, ctx.semantica, ejecucion_id, indice,
            reranker=ctx.reranker, verificador=ctx.verificador)
    encolador.encoladas.clear()


def test_estimar_no_crea_nada_y_compara_con_la_exploratoria(
        conn_boveda, conn_semantica, proveedor, ciento_treinta_y_siete):
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, verificador=VerificadorPorPalabra())
    estimacion = consulta_completa.estimar(ctx, {"criterios": [CRITERIO]})
    assert estimacion["personas_habilitadas"] == 139
    assert estimacion["estimacion"]["unidades"] > 0
    assert estimacion["exploratoria"]["usd"] > 0
    assert estimacion["presupuesto_sugerido_usd"] > 0
    assert semantica.db.una(conn_boveda, "select count(*) as n from consulta_ejecucion")["n"] == 0


def test_lanzar_exige_presupuesto_confirmacion_y_que_la_estimacion_entre(
        conn_boveda, conn_semantica, proveedor, ciento_treinta_y_siete, monkeypatch):
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, verificador=VerificadorPorPalabra())
    encolador = diferida.EncoladorEnMemoria()
    with pytest.raises(DatosInvalidos, match="presupuesto_usd"):
        consulta_completa.lanzar(ctx, {"criterios": [CRITERIO], "confirmar_costo": True},
                                 encolador=encolador)
    with pytest.raises(DatosInvalidos, match="confirmar"):
        consulta_completa.lanzar(ctx, {"criterios": [CRITERIO], "presupuesto_usd": 1},
                                 encolador=encolador)
    with pytest.raises(DatosInvalidos, match="máximo"):
        consulta_completa.lanzar(ctx, {"criterios": [CRITERIO], "presupuesto_usd": 10_000,
                                       "confirmar_costo": True}, encolador=encolador)
    with pytest.raises(Conflicto, match="supera el presupuesto"):
        consulta_completa.lanzar(ctx, {"criterios": [CRITERIO], "presupuesto_usd": 0.0001,
                                       "confirmar_costo": True}, encolador=encolador)
    assert not encolador.encoladas


def test_la_completa_encuentra_a_las_137_y_pagina(conn_boveda, conn_semantica, proveedor,
                                                  ciento_treinta_y_siete):
    """Exhaustividad y paginación: las 137 que cumplen están, cada una una
    vez, repartidas en tres páginas de 50; la contradictoria y la que solo
    tiene evidencia irrelevante, no."""
    verificador = VerificadorPorPalabra()
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, reranker=RerankerPorPalabra(),
               verificador=verificador)
    encolador = diferida.EncoladorEnMemoria()
    lanzada = _lanzar(ctx, encolador)
    ejecucion_id = lanzada["ejecucion_id"]
    assert lanzada["estado"] == "encolada" and encolador.encoladas
    _procesar_todo(ctx, encolador, ejecucion_id)

    estado = consulta_completa.estado(conn_boveda, ejecucion_id)
    assert estado["estado"] == "terminada" and estado["porcentaje"] == 100
    # Cada texto distinto se juzgó una vez.
    assert len(verificador.vistos) == len(set(verificador.vistos))

    vistos, paginas = [], None
    for pagina in (1, 2, 3):
        r = consulta_completa.resultado(ctx, ejecucion_id, pagina=pagina)
        paginas = r["paginacion"]["paginas"]
        vistos += [i["id_persona"] for i in r["items"]]
    assert paginas == 3
    assert len(vistos) == len(set(vistos)) == 137
    assert set(vistos) == set(ciento_treinta_y_siete[:137])
    excluidos = {e["id_persona"]: e for e in r["excluidos"]}
    assert excluidos[ciento_treinta_y_siete[137]]["contradiccion"] is True
    assert ciento_treinta_y_siete[138] not in vistos
    assert r["verificacion"]["completa"] is True


def test_el_presupuesto_frena_la_ejecucion_y_ampliarlo_la_continua(
        conn_boveda, conn_semantica, proveedor, ciento_treinta_y_siete, monkeypatch):
    """A2 — «una evaluación completa lanzada por error debería frenarse
    sola». Con tarifas carísimas el primer lote ya no entra: queda omitido,
    la ejecución termina detenida, y lo que no se verificó figura pendiente,
    no juzgado. Ampliar el presupuesto la retoma."""
    monkeypatch.setattr(consulta_completa, "UNIDADES_POR_LOTE", 2)
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, reranker=RerankerPorPalabra(),
               verificador=VerificadorPorPalabra())
    encolador = diferida.EncoladorEnMemoria()
    lanzada = _lanzar(ctx, encolador)
    ejecucion_id = lanzada["ejecucion_id"]

    caras = costo_consulta.Tarifas(claude_entrada=1e6, claude_salida=1e6)
    for (_, indice) in list(encolador.encoladas):
        consulta_completa.procesar_lote(conn_boveda, conn_semantica, ejecucion_id, indice,
                                        reranker=ctx.reranker, verificador=ctx.verificador,
                                        tarifas=caras)
    encolador.encoladas.clear()
    estado = consulta_completa.estado(conn_boveda, ejecucion_id)
    assert estado["estado"] == "detenida_por_presupuesto"
    assert estado["lotes_omitidos"] == estado["lotes_total"]
    parcial = consulta_completa.resultado(ctx, ejecucion_id)
    assert parcial["verificacion"]["completa"] is False
    assert all(i["estado"] == consultas.PENDIENTE for i in parcial["items"])

    with pytest.raises(DatosInvalidos, match="ampliar"):
        consulta_completa.reintentar(conn_boveda, ejecucion_id, presupuesto_usd=1,
                                     encolador=encolador)
    consulta_completa.reintentar(conn_boveda, ejecucion_id, presupuesto_usd=10,
                                 encolador=encolador)
    _procesar_todo(ctx, encolador, ejecucion_id)
    assert consulta_completa.estado(conn_boveda, ejecucion_id)["estado"] == "terminada"


def test_el_gate_se_reaplica_al_leer_y_la_baja_se_lleva_los_veredictos_huerfanos(
        conn_boveda, conn_semantica, proveedor, contrato):
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, reranker=RerankerPorPalabra(),
               verificador=VerificadorPorPalabra())
    encolador = diferida.EncoladorEnMemoria()
    ejecucion_id = _lanzar(ctx, encolador)["ejecucion_id"]
    _procesar_todo(ctx, encolador, ejecucion_id)
    xiaomi = contrato[-1]
    assert [i["id_persona"] for i in consulta_completa.resultado(ctx, ejecucion_id)["items"]] \
        == [xiaomi]

    consentimiento.marcar_retirado(conn_boveda, xiaomi, consentimiento.SEMANTICO)
    conn_boveda.commit()
    assert consulta_completa.resultado(ctx, ejecucion_id)["items"] == []

    antes = semantica.db.una(conn_semantica, "select count(*) as n from veredicto_unidad")["n"]
    semantica.borrar_persona(conn_semantica, xiaomi)
    conn_semantica.commit()
    despues = semantica.db.una(conn_semantica, "select count(*) as n from veredicto_unidad")["n"]
    assert despues == antes - 1, "solo se va el veredicto de la unidad que quedó sin respuestas"


def test_cancelar_omite_lo_que_no_empezo(conn_boveda, conn_semantica, proveedor, contrato):
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, verificador=VerificadorPorPalabra())
    encolador = diferida.EncoladorEnMemoria()
    ejecucion_id = _lanzar(ctx, encolador)["ejecucion_id"]
    estado = consulta_completa.cancelar(conn_boveda, ejecucion_id)
    assert estado["estado"] == "cancelada"
    for (_, indice) in encolador.encoladas:
        salida = consulta_completa.procesar_lote(conn_boveda, conn_semantica, ejecucion_id,
                                                 indice, verificador=ctx.verificador)
        assert salida["estado"] == "ya_tomado"
    with pytest.raises(Conflicto):
        consulta_completa.cancelar(conn_boveda, ejecucion_id)


def test_lanzar_una_completa_es_un_permiso_aparte(conn_boveda, conn_semantica, proveedor,
                                                  contrato, monkeypatch):
    """El analista estima y lee; la lanza quien responde por el gasto."""
    monkeypatch.setenv("ENCOLADOR_TAREAS", "memoria")
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, verificador=VerificadorPorPalabra())
    analista = auth.Actor(uid="a1", email="a@ej.uy", rol="analista")
    cuerpo = {"criterios": [CRITERIO], "alcance": "completo", "presupuesto_usd": 5,
              "confirmar_costo": True}
    status, estimacion = ruteo.despachar("POST", "/consultas", {**cuerpo, "solo_estimar": True},
                                         {}, analista, ctx)
    assert status == 200 and estimacion["solo_estimar"]
    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", "/consultas", cuerpo, {}, analista, ctx)

    operaciones = auth.Actor(uid="o1", email="o@ej.uy", rol="operaciones")
    status, lanzada = ruteo.despachar("POST", "/consultas", cuerpo, {}, operaciones, ctx)
    assert status == 202
    status, leida = ruteo.despachar(
        "GET", f"/consultas/ejecuciones/{lanzada['ejecucion_id']}", {}, {}, analista, ctx)
    assert status == 200 and leida["alcance"] == "completo"


def test_ejecutar_no_corre_una_completa_en_la_request(ctx):
    with pytest.raises(DatosInvalidos, match="no se ejecuta en el momento"):
        consultas.ejecutar(ctx, {"criterios": ["x"], "alcance": "completo"})


def test_los_estados_espejan_el_catalogo_de_la_migracion(conn_boveda):
    """Como en la ingesta diferida: el código y la 0025 nombran los mismos
    estados, o la pantalla muestra un código crudo."""
    filas = semantica.db.todas(conn_boveda, "select codigo from estado_consulta_ejecucion")
    assert {f["codigo"] for f in filas} == {
        consulta_completa.ENCOLADA, consulta_completa.PROCESANDO, *consulta_completa.TERMINALES}
    filas = semantica.db.todas(conn_boveda, "select codigo from estado_lote_consulta")
    assert {f["codigo"] for f in filas} == {
        consulta_completa.LOTE_PENDIENTE, consulta_completa.LOTE_PROCESANDO,
        consulta_completa.LOTE_OK, consulta_completa.LOTE_FALLIDO,
        consulta_completa.LOTE_OMITIDO}
