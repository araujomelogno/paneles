"""BUG — el diagnóstico A1.2 mide mal (`specs/BUG_diagnostico_a1_distancia_y_hallazgo.md`).

Con un `where` selectivo, un `order by <=>` y un `limit`, Postgres resuelve por
el índice HNSW: el índice devuelve sus vecinos de **todo el corpus** (como
mucho `hnsw.ef_search`, 40 por defecto) y recién después aplica el filtro. Si
ninguno de esos vecinos lo cumple, el resultado es vacío, sin error.

El corpus de estas pruebas reproduce el caso de producción: 1.500 respuestas
repetidas sobre la titularidad del contrato, todas pegadas al criterio, y 30
sobre la marca del celular, lejos. Con el plan viejo, `a1-distancia` decía que
no había respuestas con «xiaomi»; `a1-texto` las encontraba.

Lo que se prueba, en el orden del Definition of Done:

* A1.2 devuelve la distancia de las respuestas con el patrón;
* el conteo de «cuántas están más cerca» da el mismo número que antes y que
  la cuenta a mano;
* un patrón ausente y una medición que no devolvió filas son dos mensajes;
* ninguna consulta del script combina filtro, `order by <=>` y `limit` sin
  materializar primero.

Y, porque el bug dice que el mismo plan explica el problema original, el
recall de la consulta (`semantica.recuperar`): con el gate por personas y con
un `top_n` mayor que `ef_search`.
"""

import argparse
import importlib.util
import pathlib
import random
import re
import uuid

import pytest

from panel_api import consultas, semantica

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "scripts" / "diagnosticar_consulta.py"

DIMS = 512
REPETIDAS = 1500
MARCAS = 30


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
    """El caso del addendum, a escala: el criterio, 1.500 respuestas
    repetidas pegadas a él y 30 respuestas de marca lejos."""
    azar = random.Random(20261009)
    criterio = _normal([azar.gauss(0, 1) for _ in range(DIMS)])
    conn = conn_semantica
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

        filas = []
        for i in range(REPETIDAS + MARCAS):
            if i < REPETIDAS:
                ruido = [azar.gauss(0, 0.05) for _ in range(DIMS)]
                vector = _normal([c + r for c, r in zip(criterio, ruido)])
                pregunta, valor = preguntas["var270"], "Soy el titular del contrato"
                embebido = "¿Quién es el titular del contrato? → Soy el titular del contrato"
            else:
                vector = _normal([azar.gauss(0, 1) for _ in range(DIMS)])
                pregunta, valor = preguntas["var273"], "Xiaomi"
                embebido = "¿De qué marca es tu celular? → Xiaomi"
            filas.append((str(uuid.uuid4()), pregunta, valor, embebido, vector))

        ids_persona = {}
        for id_persona, pregunta, valor, embebido, vector in filas:
            cur.execute("insert into individuo (id_persona) values (%s) returning id",
                        (id_persona,))
            individuo = cur.fetchone()["id"]
            cur.execute(
                "insert into respuesta (individuo_id, pregunta_id, valor_texto, "
                "texto_embebido, embedding) values (%s, %s, %s, %s, %s::vector) "
                "returning id",
                (individuo, pregunta, valor, embebido, semantica._vector(vector)))
            ids_persona[cur.fetchone()["id"]] = id_persona
        cur.execute("analyze respuesta")
    conn.commit()
    # Con 1.530 filas el planificador local prefiere recorrer la tabla; con
    # las 21.340 de producción gana el índice. Se lo empuja al plan de
    # producción para la sesión (la conexión se cierra al terminar la
    # prueba): las consultas arregladas tienen que dar bien **aun** cuando
    # el planificador prefiere el índice.
    with conn.cursor() as cur:
        cur.execute("set enable_seqscan = off")
    conn.commit()
    marcas = [rid for rid, (_, _, valor, _, _) in zip(ids_persona, filas) if valor == "Xiaomi"]
    return {
        "conn": conn,
        "criterio": criterio,
        "vectores": {rid: f[4] for rid, f in zip(ids_persona, filas)},
        "marcas": marcas,
        "personas_marca": [ids_persona[r] for r in marcas],
    }


def _plan_viejo(conn, vector, patron, limite):
    """La consulta de antes del arreglo, tal cual."""
    return semantica.db.todas(
        conn,
        """select r.id, r.valor_texto, r.embedding <=> %s::vector as distancia
             from respuesta r
            where r.valor_texto ilike %s or r.texto_embebido ilike %s
            order by 3 limit %s""",
        (semantica._vector(vector), f"%{patron}%", f"%{patron}%", limite))


def test_el_corpus_reproduce_la_trampa_del_indice(corpus):
    """Sin esto las demás pruebas serían vacías: con el plan viejo, las 30
    respuestas con «xiaomi» existen y la consulta no devuelve ninguna."""
    conn = corpus["conn"]
    assert _script()._coincidencias(conn, "xiaomi") == MARCAS
    assert _plan_viejo(conn, corpus["criterio"], "xiaomi", 5) == []


# ── DoD 1 — A1.2 devuelve la distancia ──────────────────────────────

def test_a1_distancia_mide_las_respuestas_con_el_patron(corpus):
    script = _script()
    medicion = script.medir(corpus["conn"], corpus["criterio"], "xiaomi")

    assert medicion["estado"] == script.MEDIDO
    assert medicion["coincidencias"] == MARCAS
    esperadas = sorted(_coseno(corpus["criterio"], corpus["vectores"][r])
                       for r in corpus["marcas"])
    obtenidas = [float(f["distancia"]) for f in medicion["filas"]]
    assert obtenidas == pytest.approx(esperadas[:5], abs=1e-4)
    assert all(f["id"] in corpus["marcas"] for f in medicion["filas"])


def test_a1_distancia_completo_imprime_la_distancia(corpus, monkeypatch, capsys):
    """El subcomando entero, como lo corre el operador: ya no manda a correr
    a1-texto cuando a1-texto encuentra las respuestas."""
    script = _script()

    class Fijo:
        def embeber_criterio(self, textos, tipo=None):
            return [corpus["criterio"]]

    monkeypatch.setattr(script, "_conectar", lambda variable: corpus["conn"])
    monkeypatch.setattr(script.embeddings, "crear", lambda: Fijo())
    salida = script.a1_distancia(argparse.Namespace(
        criterio="gente que usa un celular xiaomi", patron="xiaomi", contra="titular"))

    impreso = capsys.readouterr().out
    assert salida == 0
    assert "no hay respuestas" not in impreso
    assert "correr a1-texto" not in impreso
    assert f"{MARCAS} respuesta(s) con «xiaomi»" in impreso
    assert "está a 0." in impreso or "está a 1." in impreso
    # Las 1.500 repetidas están delante: la respuesta no entra al pool. Es el
    # recorte, medido y no supuesto.
    assert f"{REPETIDAS + MARCAS - 1}" not in impreso or "NO entra" in impreso
    assert "NO entra" in impreso
    assert f"«titular»: {REPETIDAS} respuesta(s), {REPETIDAS} más cerca" in impreso


# ── DoD 2 — el conteo de «delante» no cambió ────────────────────────

def test_el_conteo_de_delante_es_el_de_siempre_y_es_exacto(corpus):
    script = _script()
    conn, criterio = corpus["conn"], corpus["criterio"]
    mejor = float(script.medir(conn, criterio, "xiaomi")["filas"][0]["distancia"])

    antes = semantica.db.una(
        conn, "select count(*) as n from respuesta r where r.embedding <=> %s::vector < %s",
        (semantica._vector(criterio), mejor))["n"]
    a_mano = sum(1 for v in corpus["vectores"].values() if _coseno(criterio, v) < mejor - 1e-6)

    assert script._delante(conn, criterio, mejor) == antes
    assert antes == pytest.approx(a_mano, abs=1)
    assert antes >= REPETIDAS


# ── DoD 3 — ausente y no medido son dos cosas ───────────────────────

def test_un_patron_ausente_y_una_medicion_vacia_se_distinguen(corpus, monkeypatch):
    script = _script()
    conn, criterio = corpus["conn"], corpus["criterio"]

    ausente = script.medir(conn, criterio, "huawei")
    assert ausente == {"estado": script.AUSENTE, "coincidencias": 0, "filas": []}

    # La medición con el plan viejo: el patrón está, la consulta vuelve vacía.
    monkeypatch.setattr(script, "_distancias", _plan_viejo)
    no_medido = script.medir(conn, criterio, "xiaomi")
    assert no_medido["estado"] == script.NO_MEDIDO
    assert no_medido["coincidencias"] == MARCAS

    texto_ausente = script.MENSAJE_AUSENTE.format(patron="xiaomi")
    texto_no_medido = script.MENSAJE_NO_MEDIDO.format(patron="xiaomi", coincidencias=MARCAS)
    assert texto_ausente != texto_no_medido
    assert "NUNCA ENTRÓ" in texto_ausente and "NUNCA ENTRÓ" not in texto_no_medido
    assert "No es ausencia" in texto_no_medido


def test_a1_distancia_con_medicion_vacia_sale_con_error(corpus, monkeypatch, capsys):
    """Un fallo de medición no termina en 0 ni en el código de «no está»."""
    script = _script()

    class Fijo:
        def embeber_criterio(self, textos, tipo=None):
            return [corpus["criterio"]]

    monkeypatch.setattr(script, "_conectar", lambda variable: corpus["conn"])
    monkeypatch.setattr(script.embeddings, "crear", lambda: Fijo())
    monkeypatch.setattr(script, "_distancias", _plan_viejo)
    salida = script.a1_distancia(argparse.Namespace(
        criterio="gente que usa un celular xiaomi", patron="xiaomi", contra=None))
    assert salida == 2
    assert "FALLO DE MEDICIÓN" in capsys.readouterr().out


# ── DoD 4 — ninguna consulta del script repite el patrón ────────────

def test_ninguna_consulta_filtra_y_ordena_por_distancia_sin_materializar():
    fuente = SCRIPT.read_text(encoding="utf-8")
    consultas_sql = re.findall(r'"""(.*?)"""', fuente, re.S)
    culpables = [
        sql for sql in consultas_sql
        if "<=>" in sql and re.search(r"\bwhere\b", sql, re.I)
        and re.search(r"order\s+by", sql, re.I) and re.search(r"\blimit\b", sql, re.I)
        and "materialized" not in sql.lower()
    ]
    assert culpables == []


# ── El mismo plan en el recall de la consulta ───────────────────────

def test_el_recall_con_gate_trae_a_las_personas_habilitadas(corpus):
    """El gate por personas es un `where`: antes el índice devolvía vecinos de
    todo el corpus y las personas habilitadas quedaban afuera, sin error."""
    conn, criterio = corpus["conn"], corpus["criterio"]
    candidatos = semantica.recuperar(conn, criterio, 200, corpus["personas_marca"])

    assert sorted(c["respuesta_id"] for c in candidatos) == sorted(corpus["marcas"])
    distancias = [c["distancia"] for c in candidatos]
    assert distancias == sorted(distancias)


def test_el_recall_con_un_gate_amplio_es_el_exacto(corpus):
    """El caso de producción: la mitad del corpus habilitada. El filtro no es
    selectivo, el planificador elige el índice, y el índice devolvía sus 40
    vecinos —la mitad, de gente no habilitada— en vez de los 200 pedidos."""
    conn, criterio = corpus["conn"], corpus["criterio"]
    personas = semantica.db.todas(
        conn, "select r.id, i.id_persona from respuesta r join individuo i on i.id = r.individuo_id")
    habilitadas = {f["id"]: str(f["id_persona"]) for f in personas if f["id"] % 2 == 0}
    plan = {}
    candidatos = semantica.recuperar(conn, criterio, 200, list(habilitadas.values()), plan)

    assert plan == {"plan": "exacto"}
    exactas = sorted(habilitadas, key=lambda r: _coseno(criterio, corpus["vectores"][r]))[:200]
    assert len(candidatos) == 200
    assert {c["respuesta_id"] for c in candidatos} == set(exactas)


def test_el_recall_sin_filtro_trae_top_n_y_no_ef_search(corpus):
    """`top_n` 200 con `ef_search` 40 devolvía 40: el pool era un quinto de lo
    que la consulta decía."""
    conn, criterio = corpus["conn"], corpus["criterio"]
    plan = {}
    candidatos = semantica.recuperar(conn, criterio, consultas.TOP_N_POR_DEFECTO, informe=plan)
    assert len(candidatos) == consultas.TOP_N_POR_DEFECTO
    assert plan == {"plan": "indice"}


def test_el_recall_mas_grande_que_el_indice_es_exacto(corpus):
    conn, criterio = corpus["conn"], corpus["criterio"]
    pedido = semantica.EF_SEARCH_MAXIMO + 100
    candidatos = semantica.recuperar(conn, criterio, pedido)
    assert len(candidatos) == pedido
    exactas = sorted(_coseno(criterio, v) for v in corpus["vectores"].values())[:pedido]
    assert max(c["distancia"] for c in candidatos) == pytest.approx(exactas[-1], abs=1e-4)


def test_un_recall_corto_del_indice_se_registra_y_se_recalcula(corpus, monkeypatch, capsys):
    """Si el índice devuelve menos de lo pedido habiendo más en el corpus, no
    se presenta como «no hay más evidencia»."""
    conn, criterio = corpus["conn"], corpus["criterio"]
    original = semantica.db.todas

    def indice_corto(c, sql, params=()):
        filas = original(c, sql, params)
        if "materialized" not in sql and "order by r.embedding <=>" in sql:
            return filas[:10]
        return filas

    monkeypatch.setattr(semantica.db, "todas", indice_corto)
    plan = {}
    candidatos = semantica.recuperar(conn, criterio, 100, informe=plan)
    assert len(candidatos) == 100
    assert plan == {"plan": "exacto", "indice_corto": 10}
    assert "[recall] el índice devolvió 10 de 100" in capsys.readouterr().out

