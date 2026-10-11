"""BUG — el recall por índice devuelve la cantidad pedida, pero no las filas
correctas (`specs/BUG_recall_hnsw_completo_no_correcto.md`, D72).

El índice HNSW es aproximado: puede devolver 200 de 200 dejando afuera al
vecino más cercano del corpus. El control de D71 solo miraba si el recall
venía **corto**, y este viene completo. En producción, la respuesta de Xiaomi
era la más cercana de todo el corpus y no entraba entre las 200.

Un índice que se equivoca no se puede provocar de forma determinística en una
prueba, así que el «índice equivocado» es un doble explícito: devuelve las
`top_n` filas pedidas **sin** la más cercana, que es exactamente lo que se vio.
Lo que se prueba, en el orden de la §6 del bug:

* sin filtro, la mejor respuesta entra en el recall, aunque el índice se
  equivoque (porque bajo el umbral no se lo usa);
* bajo el umbral, plan `exacto` e igual a una búsqueda exhaustiva;
* sobre el umbral, plan `indice` con `ef_search > top_n`;
* el chequeo de recall real reporta lo que el índice omite (con el doble y
  con el índice de verdad, empobrecido a propósito);
* el script no concluye «recorte» cuando la evidencia no entró al recall;
* con filtro, el camino exacto de siempre.
"""

import argparse
import importlib.util
import json
import pathlib
import random
import uuid

import pytest

from panel_api import consultas, semantica

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "diagnosticar_consulta.py"

DIMS = 512
OTRAS = 1500
TOP_N = consultas.TOP_N_POR_DEFECTO


def _script():
    spec = importlib.util.spec_from_file_location("diagnosticar_consulta", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _normal(v):
    norma = sum(x * x for x in v) ** 0.5
    return [x / norma for x in v]


def _coseno(a, b):
    return 1 - sum(x * y for x, y in zip(a, b))


@pytest.fixture
def corpus(conn_semantica):
    """El caso del bug: la respuesta de Xiaomi es la más cercana del corpus;
    1.500 respuestas sobre el titular del contrato, más lejos."""
    azar = random.Random(20261010)
    criterio = _normal([azar.gauss(0, 1) for _ in range(DIMS)])
    conn = conn_semantica
    vectores, personas = {}, {}
    with conn.cursor() as cur:
        cur.execute("insert into cuestionario (nombre) values ('A1') returning id")
        cuestionario = cur.fetchone()["id"]
        preguntas = {}
        for codigo, texto in (("var270", "¿Quién es el titular del contrato?"),
                              ("var273", "¿De qué marca es tu celular?")):
            cur.execute(
                "insert into pregunta (cuestionario_id, codigo, texto, tipo) "
                "values (%s, %s, %s, 'cerrada') returning id",
                (cuestionario, codigo, texto))
            preguntas[codigo] = cur.fetchone()["id"]

        # R-UE — cada respuesta con su propio texto embebido, y por eso su
        # propia unidad: lo que se prueba es el recall sobre muchos vectores
        # distintos. Con el mismo texto, las 1.500 serían una sola unidad.
        def alta(pregunta, valor, embebido, vector):
            embebido = f"{embebido} #{len(vectores)}"
            id_persona = str(uuid.uuid4())
            cur.execute("insert into individuo (id_persona) values (%s) returning id",
                        (id_persona,))
            individuo = cur.fetchone()["id"]
            cur.execute(
                "insert into respuesta (individuo_id, pregunta_id, valor_texto, "
                "texto_embebido, embedding) values (%s, %s, %s, %s, %s::vector) "
                "returning id",
                (individuo, pregunta, valor, embebido, semantica._vector(vector)))
            rid = cur.fetchone()["id"]
            vectores[rid], personas[rid] = vector, id_persona
            return rid

        xiaomi = alta(preguntas["var273"], "Xiaomi", "¿De qué marca es tu celular? → Xiaomi",
                      _normal([c + azar.gauss(0, 0.005) for c in criterio]))
        for _ in range(OTRAS):
            alta(preguntas["var270"], "Soy el titular del contrato",
                 "¿Quién es el titular del contrato? → Soy el titular del contrato",
                 _normal([c + azar.gauss(0, 0.05) for c in criterio]))
        cur.execute("analyze respuesta")
    conn.commit()
    return {"conn": conn, "criterio": criterio, "xiaomi": xiaomi,
            "vectores": vectores, "personas": personas}


def _plan(informe):
    """El plan del recall, sin las cuentas que R-UE le sumó."""
    return {k: v for k, v in informe.items() if k not in ("unidades", "respuestas")}


def _unidad(corpus, respuesta_id):
    """R-UE — el recall y su chequeo hablan de unidades; cada respuesta de
    este corpus tiene la suya."""
    return semantica.db.una(corpus["conn"], "select unidad_id from respuesta where id = %s",
                            (respuesta_id,))["unidad_id"]


@pytest.fixture
def indice_equivocado(monkeypatch):
    """Un índice que devuelve las `top_n` filas pedidas sin la más cercana:
    completo, y equivocado. Anota si se lo llamó."""
    original = semantica._vecinos_indice
    llamadas = []

    def equivocado(conn, literal, top_n, ef_search):
        llamadas.append(ef_search)
        filas = original(conn, literal, top_n + 1, ef_search)
        return filas[1:top_n + 1]

    monkeypatch.setattr(semantica, "_vecinos_indice", equivocado)
    return llamadas


def _exhaustivo(corpus, n):
    return sorted(corpus["vectores"],
                  key=lambda r: (_coseno(corpus["criterio"], corpus["vectores"][r]), r))[:n]


def test_el_corpus_reproduce_el_caso(corpus):
    """La de Xiaomi es la más cercana de todo el corpus: nada está delante."""
    assert _exhaustivo(corpus, 1) == [corpus["xiaomi"]]
    script = _script()
    mejor = float(script.medir(corpus["conn"], corpus["criterio"], "xiaomi")
                  ["filas"][0]["distancia"])
    assert script._delante(corpus["conn"], corpus["criterio"], mejor) == 0


# ── §6.1 — sin filtro, la mejor respuesta entra ─────────────────────

def test_sin_filtro_la_mejor_respuesta_entra_aunque_el_indice_se_equivoque(
        corpus, indice_equivocado):
    plan = {}
    candidatos = semantica.recuperar(corpus["conn"], corpus["criterio"], TOP_N, informe=plan)

    assert candidatos[0]["respuesta_id"] == corpus["xiaomi"]
    assert _plan(plan) == {"plan": "exacto", "motivo_plan": "corpus_bajo_umbral"}
    assert indice_equivocado == []


def test_con_el_indice_equivocado_la_mejor_no_entraba(corpus, indice_equivocado, monkeypatch):
    """El defecto, reproducido: con el índice forzado, 200 de 200 y sin la
    mejor. Ninguna cuenta lo detecta: no hay `[recall]` ni `indice_corto`."""
    monkeypatch.setenv("RECALL_EXACTO_HASTA", "0")
    plan = {}
    candidatos = semantica.recuperar(corpus["conn"], corpus["criterio"], TOP_N, informe=plan)

    assert len(candidatos) == TOP_N
    assert corpus["xiaomi"] not in {c["respuesta_id"] for c in candidatos}
    assert plan["plan"] == "indice" and "indice_corto" not in plan


# ── §6.2 — bajo el umbral, exacto ───────────────────────────────────

def test_bajo_el_umbral_el_plan_es_exacto_y_coincide_con_el_exhaustivo(corpus):
    plan = {}
    candidatos = semantica.recuperar(corpus["conn"], corpus["criterio"], TOP_N, informe=plan)

    assert plan["plan"] == "exacto"
    assert [c["respuesta_id"] for c in sorted(
        candidatos, key=lambda c: (c["distancia"], c["respuesta_id"]))] == _exhaustivo(corpus, TOP_N)


def test_el_umbral_se_lee_del_entorno_y_un_valor_ilegible_no_lo_apaga(capsys):
    assert semantica.umbral_recall_exacto({}) == 200_000
    assert semantica.umbral_recall_exacto({"RECALL_EXACTO_HASTA": "50_000"}) == 50_000
    assert semantica.umbral_recall_exacto({"RECALL_EXACTO_HASTA": "0"}) == 0
    assert semantica.umbral_recall_exacto({"RECALL_EXACTO_HASTA": "mucho"}) == 200_000
    assert semantica.umbral_recall_exacto({"RECALL_EXACTO_HASTA": "-1"}) == 200_000
    assert "RECALL_EXACTO_HASTA" in capsys.readouterr().out


def test_el_tamano_del_corpus_sale_del_catalogo(corpus):
    assert semantica.tamano_corpus(corpus["conn"]) == OTRAS + 1


# ── §6.3 — sobre el umbral, índice con margen ───────────────────────

def test_sobre_el_umbral_el_indice_va_con_ef_search_mayor_que_top_n(corpus, monkeypatch):
    monkeypatch.setenv("RECALL_EXACTO_HASTA", "100")
    conn = corpus["conn"]
    plan = {}
    candidatos = semantica.recuperar(conn, corpus["criterio"], TOP_N, informe=plan)

    assert plan["plan"] == "indice" and plan["motivo_plan"] == "corpus_sobre_umbral"
    assert plan["ef_search"] > TOP_N
    assert plan["ef_search"] == 2 * TOP_N
    # Y es el que quedó fijado en la transacción de la consulta.
    assert semantica.db.una(conn, "show hnsw.ef_search")["hnsw.ef_search"] == str(2 * TOP_N)
    assert len(candidatos) == TOP_N


def test_el_margen_respeta_el_tope_de_pgvector():
    assert semantica.ef_search_para(10) == semantica.EF_SEARCH_MINIMO
    assert semantica.ef_search_para(200) == 400
    assert semantica.ef_search_para(800) == semantica.EF_SEARCH_MAXIMO


def test_sin_margen_posible_en_el_indice_va_exacto(corpus, monkeypatch, indice_equivocado):
    """Con `top_n` ≥ 1000 no hay `ef_search` mayor: el índice no se usa."""
    monkeypatch.setenv("RECALL_EXACTO_HASTA", "0")
    plan = {}
    candidatos = semantica.recuperar(corpus["conn"], corpus["criterio"], 1000, informe=plan)
    assert _plan(plan) == {"plan": "exacto", "motivo_plan": "top_n_sin_margen_en_el_indice"}
    assert candidatos[0]["respuesta_id"] == corpus["xiaomi"]
    assert indice_equivocado == []


# ── §6.4 — el chequeo de recall real ────────────────────────────────

def test_el_chequeo_de_recall_real_reporta_lo_que_el_indice_omite(corpus, indice_equivocado):
    r = semantica.comparar_recall(corpus["conn"], corpus["criterio"], TOP_N)

    assert r["exactas"] == TOP_N and r["del_indice"] == TOP_N
    assert [f["unidad_id"] for f in r["faltan"]] == [_unidad(corpus, corpus["xiaomi"])]
    assert r["faltan"][0]["puesto"] == 1
    assert r["recall"] == pytest.approx(1 - 1 / TOP_N)
    assert r["ef_search"] == 2 * TOP_N


def test_el_chequeo_de_recall_real_contra_el_indice_de_verdad(corpus):
    """Sin dobles: con un `ef_search` mínimo el índice de verdad trae muy
    poco, y el chequeo lo cuenta."""
    with corpus["conn"].cursor() as cur:
        # Con 1.501 filas el planificador local prefiere recorrer la tabla;
        # en producción gana el índice (ver test_bug_diagnostico_a1).
        cur.execute("set local enable_seqscan = off")
    pobre = semantica.comparar_recall(corpus["conn"], corpus["criterio"], TOP_N, ef_search=10)
    assert len(pobre["faltan"]) >= TOP_N - 10
    assert pobre["recall"] < 0.1


def test_recall_indice_del_script_sale_con_1_si_faltan(corpus, indice_equivocado,
                                                        monkeypatch, capsys, tmp_path):
    script = _script()

    class Fijo:
        def embeber_criterio(self, textos, tipo=None):
            return [corpus["criterio"]]

    monkeypatch.setattr(script, "_conectar", lambda variable: corpus["conn"])
    monkeypatch.setattr(script.embeddings, "crear", lambda: Fijo())
    archivo = tmp_path / "consultas.json"
    archivo.write_text(json.dumps([{"criterio": "gente que usa un celular xiaomi"}]))
    salida = script.recall_indice(argparse.Namespace(
        criterio=None, consultas=str(archivo), top_n=TOP_N, ef_search=None))

    impreso = capsys.readouterr().out
    assert salida == 1
    assert "producción usa EXACTO sin filtro" in impreso
    assert "m=16 (default), ef_construction=64 (default)" in impreso
    assert f"faltan 1 de {TOP_N}" in impreso
    assert f"puesto 1 del exacto: unidad #{_unidad(corpus, corpus['xiaomi'])}" in impreso


# ── §6.5 — el script no concluye «recorte» si no entró al recall ────

def test_las_conclusiones_son_excluyentes():
    script = _script()

    lineas, defecto = script.conclusiones(False, 0, TOP_N, "indice", mas_cerca_contra=1)
    assert defecto
    assert lineas[0].startswith("→ EL RECALL NO LA TRAJO (plan=indice)")
    assert script.RECORTE not in lineas and script.NO_APLICA_RECORTE in lineas
    assert "FALLO DE MEDICIÓN" not in " ".join(lineas)

    lineas, defecto = script.conclusiones(False, 500, TOP_N, "exacto", mas_cerca_contra=3)
    assert not defecto and script.RECORTE not in lineas

    lineas, defecto = script.conclusiones(True, 0, TOP_N, "exacto", mas_cerca_contra=3)
    assert lineas == [script.RECORTE] and not defecto

    lineas, _ = script.conclusiones(True, 40, TOP_N, "exacto", mas_cerca_contra=30)
    assert lineas == [script.CALIDAD_EMBEDDING]


def _a1_distancia(script, corpus, monkeypatch):
    class Fijo:
        def embeber_criterio(self, textos, tipo=None):
            return [corpus["criterio"]]

    monkeypatch.setattr(script, "_conectar", lambda variable: corpus["conn"])
    monkeypatch.setattr(script.embeddings, "crear", lambda: Fijo())
    return script.a1_distancia(argparse.Namespace(
        criterio="gente que usa un celular xiaomi", patron="xiaomi", contra="titular"))


def test_a1_distancia_con_el_indice_equivocado_no_dice_recorte(
        corpus, indice_equivocado, monkeypatch, capsys):
    """La salida del bug, reproducida con el índice forzado: antes imprimía
    «FALLO DE MEDICIÓN» y «fue el RECORTE» a la vez."""
    monkeypatch.setenv("RECALL_EXACTO_HASTA", "0")
    script = _script()
    salida = _a1_distancia(script, corpus, monkeypatch)

    impreso = capsys.readouterr().out
    assert salida == 2
    assert "0 unidad(es) de evidencia del corpus están más cerca" in impreso
    assert "NO entra" in impreso
    assert "EL RECALL NO LA TRAJO (plan=indice)" in impreso
    assert "La conclusión del recorte NO APLICA" in impreso
    assert "fue el RECORTE" not in impreso


def test_a1_distancia_con_el_plan_de_produccion_la_trae(
        corpus, indice_equivocado, monkeypatch, capsys):
    script = _script()
    salida = _a1_distancia(script, corpus, monkeypatch)

    impreso = capsys.readouterr().out
    assert salida == 0
    assert "plan=exacto, corpus_bajo_umbral" in impreso
    assert "entra la mejor con «xiaomi»" in impreso
    # R-UE — el pool cuenta unidades: 200 textos distintos.
    assert f"{TOP_N} unidad(es), {TOP_N} respuesta(s)" in impreso


# ── §6.6 — con filtro, sin cambios ──────────────────────────────────

def test_con_filtro_sigue_el_camino_exacto(corpus, indice_equivocado, monkeypatch):
    monkeypatch.setenv("RECALL_EXACTO_HASTA", "0")
    habilitados = list(corpus["personas"].values())[:300]
    plan = {}
    candidatos = semantica.recuperar(corpus["conn"], corpus["criterio"], TOP_N,
                                     habilitados, plan)

    assert _plan(plan) == {"plan": "exacto", "motivo_plan": "filtro_por_personas"}
    assert candidatos[0]["respuesta_id"] == corpus["xiaomi"]
    assert indice_equivocado == []
