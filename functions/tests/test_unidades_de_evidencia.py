"""R-UE — unidades de evidencia: un vector por texto distinto.

`specs/SPEC_unidades_de_evidencia.md`, D73. Medido sobre el corpus real,
21.340 respuestas eran 861 textos distintos: el 96 % eran copias, y las
copias rompían el índice HNSW (0 % de recall para «celular xiaomi») y
gastaban la verificación en la misma idea. Las pruebas de aceptación del
spec, en su orden (§5):

* la migración deja una unidad por `hash_texto` y ninguna respuesta sin
  unidad (sobre una base nueva, con la 0010 de verdad);
* «xiaomi» recupera su unidad y la resuelve a **todas** sus personas;
* `top_k = 25` verifica 25 unidades distintas;
* un veredicto sobre una unidad llega a todas sus personas;
* quien no tiene `uso_semantico` vigente no aparece aunque su unidad esté en
  el recall;
* una respuesta nueva con un texto existente no dispara un embedding;
* no regresión: el pool de personas contiene al que daba el recall por
  respuestas.

Y lo que el diseño agrega: la baja se lleva la unidad que se queda sin
respuestas, la base no deja una respuesta apuntando a otro texto, el código
anterior sigue pudiendo insertar durante la transición, y la 0011 (quitar
`respuesta.embedding`) no rompe nada de lo que el código nuevo hace.
"""

import pathlib
import uuid

import psycopg
import pytest

from panel_api import (
    consentimiento,
    consultas,
    db,
    encuestas,
    paneles,
    personas,
    semantica,
    verificacion as mod_verificacion,
)

from conftest import ContextoDePrueba
from corpus import VERSION

RAIZ = pathlib.Path(__file__).resolve().parents[2]

PREGUNTAS = [
    {"codigo": "MARCA", "texto": "¿De qué marca es tu celular?", "tipo": "abierta",
     "orden": 1},
]
XIAOMI = "¿De qué marca es tu celular? → Xiaomi"


# ── Dobles ───────────────────────────────────────────────────────────

class Contador:
    """Envuelve al proveedor y anota cada texto que se mandó a embeber."""

    def __init__(self, envuelto, criterio=None):
        self.envuelto = envuelto
        self.dims = envuelto.dims
        self.textos = []
        self.criterio = criterio

    def embeber(self, textos):
        self.textos.extend(textos)
        return self.envuelto.embeber(textos)

    def embeber_por_lotes(self, textos, tamano_lote=128):
        self.textos.extend(textos)
        yield from self.envuelto.embeber_por_lotes(textos, tamano_lote)

    def embeber_criterio(self, textos, tipo=None):
        """El criterio se embebe como el texto que se le indique: el doble
        determinístico no sabe de significado, y lo que se prueba acá es qué
        pasa **después** de tener el vector."""
        if self.criterio:
            return self.envuelto.embeber([self.criterio] * len(textos))
        return self.envuelto.embeber(textos)


class VerificadorQueAnota(mod_verificacion.Verificador):
    """`xiaomi` → cumple; lo demás, dudoso. Anota cada texto que juzgó."""

    nombre = "que_anota"

    def __init__(self):
        self.vistos = []

    def verificar(self, criterio, candidatos):
        crudos = []
        for n, c in enumerate(candidatos):
            texto = c.get("valor_texto") or ""
            self.vistos.append(texto)
            v = (mod_verificacion.CUMPLE if "xiaomi" in texto.lower()
                 else mod_verificacion.DUDOSO)
            crudos.append({"n": n, "veredicto": v, "razon": f"regla: {v}"})
        return mod_verificacion._completar(crudos, candidatos, criterio)


# ── El corpus ────────────────────────────────────────────────────────

def _sembrar(conn_boveda, conn_semantica, proveedor, marcas, nombre="Ola Celulares",
             panel=None):
    """Una persona por elemento de `marcas`, con esa respuesta a MARCA."""
    panel = panel or paneles.crear(conn_boveda, f"Panel {uuid.uuid4().hex[:6]}", "R-UE")
    prefijo = uuid.uuid4().hex[:6]
    ids = []
    for i, _ in enumerate(marcas):
        alta = personas.alta(conn_boveda, {
            "persona": {"nombre": f"Persona {prefijo}{i:03d}", "sexo": "F",
                        "fecha_nacimiento": "1990-01-01", "localidad": "Montevideo",
                        "email": f"p{prefijo}{i:03d}@ejemplo.uy"},
            "consentimientos": [{"finalidad": f, "version_texto": VERSION}
                                for f in ("contacto_participacion", "uso_semantico")],
            "origen": "dooblo", "id_en_origen": f"{prefijo}-{i:03d}",
            "panel_id": panel["id"],
        }, actor="prueba")
        ids.append(alta["id_persona"])
    encuesta = encuestas.crear(conn_boveda, panel["id"], nombre, "2026-09-01")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    resultado = encuestas.ingestar(
        conn_boveda, conn_semantica, encuesta["id"], PREGUNTAS,
        [{"id_en_origen": f"{prefijo}-{i:03d}", "MARCA": m} for i, m in enumerate(marcas)],
        proveedor=proveedor)
    conn_boveda.commit()
    conn_semantica.commit()
    return {"ids": ids, "panel": panel, "resultado": resultado}


def _unidades(conn):
    return db.una(conn, "select count(*) as n from unidad_evidencia")["n"]


# ════════════════════════════════════════════════════════════════════
#  R-UE.5 — la migración, sobre una base nueva
# ════════════════════════════════════════════════════════════════════

def _base_nueva(dsn_semantica, nombre):
    """Una base vacía en el mismo cluster, con las migraciones 0001–0009.
    Devuelve el DSN. La borra quien la pidió."""
    admin = psycopg.connect(dsn_semantica.rsplit("/", 1)[0] + "/postgres", autocommit=True)
    admin.execute(f"drop database if exists {nombre}")
    admin.execute(f"create database {nombre}")
    admin.close()
    dsn = dsn_semantica.rsplit("/", 1)[0] + "/" + nombre
    with psycopg.connect(dsn, autocommit=True) as conn:
        for archivo in sorted((RAIZ / "db" / "semantica").glob("000*.sql")):
            conn.execute(archivo.read_text(encoding="utf-8"))
    return dsn


def _borrar_base(dsn_semantica, nombre):
    admin = psycopg.connect(dsn_semantica.rsplit("/", 1)[0] + "/postgres", autocommit=True)
    admin.execute(f"drop database if exists {nombre} with (force)")
    admin.close()


def _corpus_previo(conn, vector_de):
    """Lo que había antes de la 0010: respuestas con su propio vector. Seis
    textos distintos en 20 respuestas, y el mismo texto en dos olas."""
    cur = conn.cursor()
    preguntas = []
    for ola in ("Ola 1", "Ola 2"):
        cur.execute("insert into cuestionario (nombre) values (%s) returning id", (ola,))
        cuestionario = cur.fetchone()[0]
        cur.execute("insert into pregunta (cuestionario_id, codigo, texto) "
                    "values (%s, 'MARCA', '¿De qué marca es tu celular?') returning id",
                    (cuestionario,))
        preguntas.append(cur.fetchone()[0])
    textos = ["Xiaomi"] * 8 + ["Samsung"] * 5 + ["Motorola"] * 4 + ["Apple", "Nokia", "LG"]
    for i, marca in enumerate(textos):
        cur.execute("insert into individuo (id_persona) values (%s) returning id",
                    (str(uuid.uuid4()),))
        individuo = cur.fetchone()[0]
        embebido = f"¿De qué marca es tu celular? → {marca}"
        # Las mismas marcas, repartidas en las dos olas: la misma pregunta con
        # el mismo texto en dos olas es **una** unidad.
        cur.execute(
            "insert into respuesta (individuo_id, pregunta_id, valor_texto, "
            "texto_embebido, embedding) values (%s, %s, %s, %s, %s::vector)",
            (individuo, preguntas[i % 2], marca, embebido, vector_de(embebido)))
    return len(set(textos)), len(textos)


def test_la_migracion_deja_una_unidad_por_texto_y_ninguna_respuesta_sin_unidad(
        dsn_semantica, proveedor):
    """«El corpus migrado tiene una unidad por `hash_texto` distinto» y
    «ninguna respuesta queda sin unidad», con la 0010 de verdad y sin
    re-embeber nada: el vector de cada unidad es el de sus respuestas."""
    nombre = "paneles_prueba_r_ue"
    dsn = _base_nueva(dsn_semantica, nombre)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            distintos, respuestas = _corpus_previo(
                conn, lambda t: semantica._vector(proveedor.embeber([t])[0]))
            conn.execute((RAIZ / "db" / "semantica" / "0010_unidad_evidencia_vector.sql")
                         .read_text(encoding="utf-8"))
            fila = conn.execute(
                """select (select count(*) from unidad_evidencia) as unidades,
                          (select count(distinct hash_texto) from respuesta) as huellas,
                          (select count(*) from respuesta where unidad_id is null) as sin_unidad,
                          (select count(*) from respuesta r
                             join unidad_evidencia u on u.id = r.unidad_id
                            where u.embedding::text <> r.embedding::text) as otro_vector,
                          (select count(*) from respuesta) as respuestas""").fetchone()
            assert fila == (distintos, distintos, 0, 0, respuestas)
            # Y correrla dos veces no cambia nada.
            conn.execute((RAIZ / "db" / "semantica" / "0010_unidad_evidencia_vector.sql")
                         .read_text(encoding="utf-8"))
            assert conn.execute("select count(*) from unidad_evidencia").fetchone()[0] \
                == distintos
    finally:
        _borrar_base(dsn_semantica, nombre)


def test_la_migracion_no_esconde_un_texto_con_dos_vectores(dsn_semantica, proveedor):
    """R-UE.5 — si el mismo texto tiene vectores distintos (otra dimensión,
    otro `input_type`), la migración se detiene y lo dice; con la decisión
    explícita, toma el de la respuesta de menor id."""
    nombre = "paneles_prueba_r_ue_inconsistente"
    dsn = _base_nueva(dsn_semantica, nombre)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            contador = iter(range(10_000))

            def vector_de(texto):
                # Un vector distinto por respuesta, aunque el texto se repita.
                return semantica._vector(proveedor.embeber([f"{texto} {next(contador)}"])[0])

            _corpus_previo(conn, vector_de)
            migracion = (RAIZ / "db" / "semantica" / "0010_unidad_evidencia_vector.sql"
                         ).read_text(encoding="utf-8")
            with pytest.raises(psycopg.errors.RaiseException, match="más de un vector"):
                conn.execute(migracion)
            conn.execute("rollback")
            assert conn.execute("select to_regclass('unidad_evidencia')").fetchone()[0] is None

            conn.execute("set paneles.unidades_inconsistentes = 'tomar_la_primera'")
            conn.execute(migracion)
            primera = conn.execute(
                """select u.embedding::text = (select r.embedding::text from respuesta r
                                          where r.hash_texto = u.hash_texto
                                          order by r.id limit 1)
                     from unidad_evidencia u""").fetchall()
            assert primera and all(f[0] for f in primera)
    finally:
        _borrar_base(dsn_semantica, nombre)


def test_el_diagnostico_de_unidades_ve_la_inconsistencia(conn_semantica, proveedor):
    """`diagnosticar_consulta.py unidades`: después de migrar, una respuesta
    que conserva un vector distinto del de su unidad se informa."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "diagnosticar_consulta", RAIZ / "scripts" / "diagnosticar_consulta.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    cur = conn_semantica.cursor()
    cur.execute("insert into cuestionario (nombre) values ('X') returning id")
    cuestionario = cur.fetchone()["id"]
    cur.execute("insert into pregunta (cuestionario_id, codigo, texto) values (%s, 'P', 'P') "
                "returning id", (cuestionario,))
    pregunta = cur.fetchone()["id"]
    for i in range(2):
        cur.execute("insert into individuo (id_persona) values (%s) returning id",
                    (str(uuid.uuid4()),))
        individuo = cur.fetchone()["id"]
        # El «código anterior»: inserta con su propio vector.
        cur.execute(
            "insert into respuesta (individuo_id, pregunta_id, valor_texto, texto_embebido, "
            "embedding) values (%s, %s, 'x', 'P → x', %s::vector)",
            (individuo, pregunta, semantica._vector(proveedor.embeber([f"otro {i}"])[0])))
    r = script.chequear_unidades(conn_semantica)
    assert r["migrada"] and r["unidades"] == 1 and r["sin_unidad"] == 0
    assert len(r["textos_con_mas_de_un_vector"]) == 1
    assert r["problemas"]


# ════════════════════════════════════════════════════════════════════
#  R-UE.4 — ingesta: un embedding por texto nuevo
# ════════════════════════════════════════════════════════════════════

def test_mil_copias_del_mismo_texto_se_embeben_una_vez(conn_boveda, conn_semantica,
                                                        proveedor):
    contador = Contador(proveedor)
    marcas = ["Xiaomi"] * 30 + ["Samsung"] * 12 + ["Motorola"]
    sembrado = _sembrar(conn_boveda, conn_semantica, contador, marcas)
    assert sorted(contador.textos) == sorted({f"¿De qué marca es tu celular? → {m}"
                                              for m in marcas})
    assert sembrado["resultado"]["embebidas"] == 3
    assert sembrado["resultado"]["respuestas_escritas"] == len(marcas)
    assert _unidades(conn_semantica) == 3
    # Ninguna respuesta sin unidad, y cada una con la de su texto.
    assert db.una(conn_semantica, """
        select count(*) as n from respuesta r join unidad_evidencia u on u.id = r.unidad_id
         where u.texto_embebido <> r.texto_embebido""")["n"] == 0


def test_una_respuesta_nueva_con_un_texto_existente_no_dispara_un_embedding(
        conn_boveda, conn_semantica, proveedor):
    """«→ Xiaomi» en otra ola, con la misma pregunta: el texto embebido es
    idéntico, así que es la misma unidad y no se paga otra vez."""
    _sembrar(conn_boveda, conn_semantica, proveedor, ["Xiaomi", "Samsung"])
    contador = Contador(proveedor)
    segunda = _sembrar(conn_boveda, conn_semantica, contador, ["Xiaomi", "Xiaomi", "Nokia"],
                       nombre="Ola 2")
    assert contador.textos == ["¿De qué marca es tu celular? → Nokia"]
    assert segunda["resultado"]["embebidas"] == 1
    assert segunda["resultado"]["reutilizadas"] == 2
    assert _unidades(conn_semantica) == 3


def test_una_respuesta_sin_unidad_no_se_escribe(conn_semantica):
    """Escribir la respuesta de un texto que nunca se embebió es un error de
    programación: sería invisible para la búsqueda y contaría en los
    totales."""
    with pytest.raises(semantica.SinUnidad):
        semantica.upsert_respuestas(conn_semantica, [{
            "individuo_id": 1, "pregunta_id": 1, "valor_texto": "x",
            "texto_embebido": "un texto que nadie embebió"}])


def test_la_base_no_deja_una_respuesta_apuntando_a_otro_texto(
        conn_boveda, conn_semantica, proveedor):
    _sembrar(conn_boveda, conn_semantica, proveedor, ["Xiaomi", "Samsung"])
    otra = db.una(conn_semantica, "select id from unidad_evidencia where texto_embebido "
                                  "like '%%Samsung'")["id"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.ejecutar(conn_semantica,
                    "update respuesta set unidad_id = %s where valor_texto = 'Xiaomi'",
                    (otra,))
    conn_semantica.rollback()


def test_el_codigo_anterior_sigue_pudiendo_insertar_durante_la_transicion(
        conn_semantica, proveedor):
    """Hasta la 0011, el trigger de la 0010 resuelve la unidad de un insert
    que trae su vector y no su `unidad_id` —lo que hace el código anterior—:
    volver atrás el código no exige volver atrás la base."""
    cur = conn_semantica.cursor()
    cur.execute("insert into cuestionario (nombre) values ('X') returning id")
    cuestionario = cur.fetchone()["id"]
    cur.execute("insert into pregunta (cuestionario_id, codigo, texto) values (%s, 'P', 'P') "
                "returning id", (cuestionario,))
    pregunta = cur.fetchone()["id"]
    vector = semantica._vector(proveedor.embeber(["P → x"])[0])
    for _ in range(3):
        cur.execute("insert into individuo (id_persona) values (%s) returning id",
                    (str(uuid.uuid4()),))
        cur.execute(
            "insert into respuesta (individuo_id, pregunta_id, valor_texto, texto_embebido, "
            "embedding) values (%s, %s, 'x', 'P → x', %s::vector)",
            (cur.fetchone()["id"], pregunta, vector))
    assert _unidades(conn_semantica) == 1
    assert db.una(conn_semantica, "select count(distinct unidad_id) as n, "
                                  "count(*) filter (where unidad_id is null) as nulas "
                                  "from respuesta") == {"n": 1, "nulas": 0}


# ════════════════════════════════════════════════════════════════════
#  R-UE.2 y R-UE.3 — recall y verificación sobre unidades
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def celulares(conn_boveda, conn_semantica, proveedor):
    """40 personas con Xiaomi, y 30 marcas distintas de a 2 personas."""
    otras = [f"Marca{n:02d}" for n in range(30) for _ in range(2)]
    sembrado = _sembrar(conn_boveda, conn_semantica, proveedor, ["Xiaomi"] * 40 + otras)
    sembrado["xiaomi"] = sembrado["ids"][:40]
    return sembrado


def _ctx(conn_boveda, conn_semantica, proveedor, verificador, criterio=XIAOMI):
    return ContextoDePrueba(conn_boveda, conn_semantica, Contador(proveedor, criterio),
                            verificador=verificador)


def test_xiaomi_recupera_su_unidad_y_la_resuelve_a_todas_sus_personas(
        conn_semantica, proveedor, celulares):
    """El caso del spec: antes, 40 vectores idénticos formaban un bloque que
    el índice no alcanzaba. Ahora la unidad es **una** y vuelve con sus 40
    respuestas, aunque el pool sea de una sola unidad."""
    plan = {}
    candidatos = semantica.recuperar(conn_semantica, proveedor.embeber([XIAOMI])[0], 1,
                                     informe=plan)
    assert plan["unidades"] == 1 and plan["respuestas"] == 40
    assert {c["texto_embebido"] for c in candidatos} == {XIAOMI}
    assert {c["id_persona"] for c in candidatos} == set(celulares["xiaomi"])
    assert len({c["unidad_id"] for c in candidatos}) == 1


def test_el_indice_sobre_unidades_trae_al_bloque_que_antes_no_alcanzaba(
        conn_semantica, proveedor, celulares, monkeypatch):
    """Con el plan del índice (umbral en 0), la unidad de Xiaomi entra: el
    índice indexa textos distintos, sin bloques de vectores idénticos."""
    monkeypatch.setenv("RECALL_EXACTO_HASTA", "0")
    vector = proveedor.embeber([XIAOMI])[0]
    plan = {}
    candidatos = semantica.recuperar(conn_semantica, vector, 5, informe=plan)
    assert plan["plan"] == "indice"
    assert {c["id_persona"] for c in candidatos} >= set(celulares["xiaomi"])
    assert semantica.comparar_recall(conn_semantica, vector, 5)["recall"] == 1.0


def test_un_veredicto_sobre_una_unidad_llega_a_todas_sus_personas(
        conn_boveda, conn_semantica, proveedor, celulares):
    """`top_k = 1`: se verifica **una** unidad, una vez, y el `cumple` llega
    a las 40 personas que la dieron. Antes, `top_k = 1` era una persona."""
    verificador = VerificadorQueAnota()
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, verificador)
    resultado = consultas.ejecutar(ctx, {"criterios": ["gente que usa un celular xiaomi"],
                                         "top_k": 1, "limite": 200, "modo": "estricto"})
    assert verificador.vistos == ["Xiaomi"]
    assert {i["id_persona"] for i in resultado["items"]} == set(celulares["xiaomi"])
    assert all(i["estado"] == consultas.CONFIRMADA for i in resultado["items"])
    informe = resultado["diagnostico"]["verificacion"][0]
    assert informe["unidades"] == 1 and informe["personas_alcanzadas"] == 40
    evidencia = resultado["items"][0]["criterios"][0]["evidencias_evaluadas"][0]
    assert evidencia["personas_en_la_unidad"] == 40


def test_top_k_25_verifica_25_unidades_distintas(conn_boveda, conn_semantica, proveedor,
                                                  celulares):
    verificador = VerificadorQueAnota()
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, verificador)
    resultado = consultas.ejecutar(ctx, {"criterios": ["celulares"], "top_k": 25,
                                         "limite": 200, "modo": "laxo"})
    assert len(verificador.vistos) == 25
    assert len(set(verificador.vistos)) == 25, "ninguna unidad se verificó dos veces"
    informe = resultado["diagnostico"]["verificacion"][0]
    assert informe["unidades_principales"] == 25
    # Las 25 unidades alcanzan a más de 25 personas: el veredicto les llega
    # a todas las que las dieron.
    assert informe["personas_alcanzadas"] > 25


def test_quien_no_tiene_uso_semantico_no_aparece_aunque_su_unidad_si(
        conn_boveda, conn_semantica, proveedor, celulares):
    retirada = celulares["xiaomi"][0]
    consentimiento.marcar_retirado(conn_boveda, retirada, consentimiento.SEMANTICO)
    conn_boveda.commit()
    verificador = VerificadorQueAnota()
    ctx = _ctx(conn_boveda, conn_semantica, proveedor, verificador)
    resultado = consultas.ejecutar(ctx, {"criterios": ["gente que usa un celular xiaomi"],
                                         "top_k": 1, "limite": 200})
    encontrados = {i["id_persona"] for i in resultado["items"]}
    assert retirada not in encontrados
    assert encontrados == set(celulares["xiaomi"][1:])
    assert verificador.vistos == ["Xiaomi"]


def test_no_regresion_el_pool_contiene_a_las_personas_del_recall_por_respuestas(
        conn_semantica, proveedor, celulares):
    """«Los resultados no cambian en personas encontradas, salvo para
    mejor.» Las `top_n` respuestas más cercanas (el recall de antes, sobre
    `respuesta.embedding`) caen en a lo sumo `top_n` unidades, que son las
    más cercanas: el pool por unidades las contiene, con **todas** sus
    respuestas."""
    vector = semantica._vector(proveedor.embeber(["un criterio cualquiera"])[0])
    for top_n in (5, 20, 60):
        antes = db.todas(conn_semantica, """
            with c as materialized (
                select r.id, r.individuo_id, r.embedding <=> %s::vector as d from respuesta r)
            select i.id_persona::text as id_persona from c
              join individuo i on i.id = c.individuo_id
             order by c.d, c.id limit %s""", (vector, top_n))
        ahora = semantica.recuperar(conn_semantica,
                                    proveedor.embeber(["un criterio cualquiera"])[0], top_n)
        assert {f["id_persona"] for f in antes} <= {c["id_persona"] for c in ahora}
        assert len({c["id_persona"] for c in ahora}) >= len({f["id_persona"] for f in antes})


# ════════════════════════════════════════════════════════════════════
#  La baja: el vector se va con la última respuesta que lo usaba
# ════════════════════════════════════════════════════════════════════

def test_la_baja_se_lleva_la_unidad_que_solo_era_suya(conn_boveda, conn_semantica,
                                                       proveedor):
    sembrado = _sembrar(conn_boveda, conn_semantica, proveedor,
                        ["Xiaomi", "Xiaomi", "Una marca que solo dijo ella"])
    assert _unidades(conn_semantica) == 2
    semantica.borrar_persona(conn_semantica, sembrado["ids"][2])
    conn_semantica.commit()
    textos = {f["texto_embebido"] for f in db.todas(
        conn_semantica, "select texto_embebido from unidad_evidencia")}
    assert textos == {XIAOMI}, "el texto y el vector de la baja no quedan en el store"
    # La compartida se queda mientras alguien la tenga.
    semantica.borrar_persona(conn_semantica, sembrado["ids"][0])
    conn_semantica.commit()
    assert _unidades(conn_semantica) == 1
    semantica.borrar_persona(conn_semantica, sembrado["ids"][1])
    conn_semantica.commit()
    assert _unidades(conn_semantica) == 0


def test_el_retiro_por_estudio_tambien_se_lleva_la_unidad(conn_boveda, conn_semantica,
                                                          proveedor):
    sembrado = _sembrar(conn_boveda, conn_semantica, proveedor, ["Solo mía"])
    ref = db.una(conn_semantica, "select ref_estudio::text as r from cuestionario")["r"]
    semantica.borrar_respuestas_de_estudio(conn_semantica, sembrado["ids"][0], ref)
    conn_semantica.commit()
    assert _unidades(conn_semantica) == 0


# ════════════════════════════════════════════════════════════════════
#  R-UE.5 — después de la 0011, sin `respuesta.embedding`
# ════════════════════════════════════════════════════════════════════

def test_sin_respuesta_embedding_la_ingesta_y_el_recall_siguen_andando(
        conn_semantica, proveedor):
    """La 0011 se aplica a mano, recién después de verificar. Se la corre
    dentro de una transacción que se deshace: lo que importa es que el
    código nuevo no dependa de la columna que quita."""
    sql = (RAIZ / "db" / "semantica" / "posteriores"
           / "0011_quitar_embedding_de_respuesta.sql").read_text(encoding="utf-8")
    sql = sql.replace("begin;", "").replace("commit;", "")
    try:
        conn_semantica.execute(sql)
        assert not db.una(conn_semantica, """
            select 1 as x from information_schema.columns
             where table_name = 'respuesta' and column_name = 'embedding'""")
        cuestionario = semantica.asegurar_cuestionario(conn_semantica, uuid.uuid4(), "X")
        preguntas = semantica.upsert_preguntas(
            conn_semantica, cuestionario, [{"codigo": "P", "texto": "P"}])
        individuos = semantica.asegurar_individuos(
            conn_semantica, [str(uuid.uuid4()) for _ in range(3)])
        contador = Contador(proveedor)
        escritura = semantica.escribir_respuestas(conn_semantica, [
            {"individuo_id": i, "pregunta_id": preguntas["P"], "valor_texto": "x",
             "texto_embebido": "P → x"} for i in individuos.values()], contador)
        assert escritura == {"escritas": 3, "textos_embebidos": 1,
                             "respuestas_reutilizadas": 0, "repetidas": 0}
        candidatos = semantica.recuperar(conn_semantica, proveedor.embeber(["P → x"])[0], 1)
        assert len(candidatos) == 3
        assert db.una(conn_semantica, "select count(*) as n from v_respuesta_estudio "
                                      "where embedding is not null")["n"] == 3
    finally:
        conn_semantica.rollback()
