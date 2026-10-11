#!/usr/bin/env python3
"""R-CS · A1, A2 y A5 — las verificaciones previas del addendum, contra la base real.

`specs/ADDENDUM_solicitud_consultas_semanticas.md` pide, **antes de decidir el
alcance**, tres consultas de cinco minutos (A1), un número de costo (A2) y una
medición del cambio de `input_type` (A5). Este script las hace, sin escribir
nada en ninguna base:

    # con el Auth Proxy abierto contra cada instancia (ver verificar_esquema.py)
    export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
    export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
    export EMBEDDINGS_API_KEY=...        # solo para a1-distancia, input-type y estimar

    python3 scripts/diagnosticar_consulta.py a1-texto --patron xiam
    python3 scripts/diagnosticar_consulta.py a1-distancia --criterio "usa un celular xiaomi" \\
            --patron xiam --contra "titular"
    python3 scripts/diagnosticar_consulta.py a1-reranker --ultimas 5
    python3 scripts/diagnosticar_consulta.py recall-indice --criterio "usa un celular xiaomi"
    python3 scripts/diagnosticar_consulta.py unidades
    python3 scripts/diagnosticar_consulta.py estimar --criterio "usa un celular xiaomi"
    python3 scripts/diagnosticar_consulta.py input-type --consultas consultas_conocidas.json

Qué contesta cada uno:

* **a1-texto** (A1.1) — ¿la respuesta existe en el store semántico? Si no
  devuelve nada, la evidencia **nunca entró**: es un problema de ingesta y
  ningún cambio de recuperación lo arregla. Marca las filas embebidas como
  `→ <código>` o `→ Checked`, que es el problema ya documentado (Fase 8).
* **a1-distancia** (A1.2) — ¿a qué distancia quedó del criterio, comparada
  con las respuestas que sí entraron (por ejemplo las del contrato)? Si está
  más lejos, el problema es la calidad del embedding y las unidades de
  evidencia no lo resuelven; si está más cerca y no llegó, era el recorte.
  Lo mide con los dos `input_type`, porque el cambio 1 mueve los criterios,
  y dice si la respuesta entra en el recall real (`top_n`).
  Mide filas **conocidas**: el filtro se materializa antes de ordenar por
  distancia, porque con el índice HNSW de por medio un `where` selectivo
  devolvía vacío sin error (`specs/BUG_diagnostico_a1_distancia_y_hallazgo.md`).
  Sale con 0 si midió, con 1 si el patrón no está en el corpus (ingesta) y
  con 2 si el patrón está pero la medición no lo encontró, o si ninguna
  respuesta está más cerca y el recall igual no la trae (es el recall, no
  la medición). Las conclusiones son excluyentes: si la respuesta no entró
  al recall, no concluye nada sobre el recorte, que pasa después. Lista
  además los textos que más se repiten en el pool.
* **recall-indice** — para uno o más criterios, compara lo que trae el
  índice HNSW contra el recorrido exacto y dice cuántas de las `top_n`
  exactas faltan. Un recall equivocado devuelve la cantidad justa: esta es
  la única forma de verlo (`specs/BUG_recall_hnsw_completo_no_correcto.md`).
  Informa también los parámetros del índice y qué plan usa producción.
* **unidades** (R-UE.5) — antes de la semantica/0010: cuántas respuestas y
  cuántos textos distintos hay, y si algún texto tiene **más de un vector**
  entre sus respuestas (la inconsistencia que la migración no esconde).
  Después: cuántas unidades hay, si alguna respuesta quedó sin unidad o con
  otro texto que su unidad, y cuántas personas hay detrás de las unidades
  más compartidas. Solo números y huellas, nunca el texto. Sale con 1 si
  encuentra una inconsistencia.
* **a1-reranker** (A1.3) — ¿el reranker corrió? Lee `consulta_ejecucion`
  (bóveda/0025). Las ejecuciones anteriores a la 0025 no dejaron registro:
  para ellas hay que volver a correr la consulta, y el script lo dice.
* **estimar** (A2) — lo que costaría una completa sobre el corpus real, con
  las tarifas del entorno (`COSTO_*`), al lado de la exploratoria.
* **input-type** (A5) — con un juego de consultas conocidas, cuántas de las
  respuestas esperadas trae el recall con `document` y con `query`, y cuánto
  se parecen las dos listas. Es la medición «antes y después».

Lo que imprime es contenido de encuesta (sin PII: el store semántico no la
tiene) y números. No lo pegues en un canal abierto.
"""

import argparse
import json
import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "functions"))

from panel_api import (  # noqa: E402
    consulta_completa,
    consultas,
    embeddings,
    semantica,
    verificacion,
)

MARCAS_DE_CODIGO = ("→ checked", "→ unchecked")


def _conectar(variable):
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get(variable)
    if not dsn:
        sys.exit(f"Falta {variable} en el entorno (ver el encabezado de este script).")
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)


def _parece_codigo(texto):
    """`→ 11427` o `→ Checked`: la etiqueta no se tradujo antes de embeber."""
    cola = (texto or "").split("→")[-1].strip().lower()
    return cola.isdigit() or (texto or "").strip().lower().endswith(MARCAS_DE_CODIGO)


# ── A1.1 ─────────────────────────────────────────────────────────────

def a1_texto(args):
    conn = _conectar("DSN_SEMANTICA")
    patron = f"%{args.patron}%"
    filas = semantica.db.todas(
        conn,
        """select r.id, p.codigo, p.texto, r.valor_texto, r.texto_embebido
             from respuesta r join pregunta p on p.id = r.pregunta_id
            where r.valor_texto ilike %s or r.texto_embebido ilike %s
            order by r.id limit %s""",
        (patron, patron, args.limite))
    if not filas:
        print(f"A1.1 · Ninguna respuesta contiene «{args.patron}».")
        print("      → La evidencia NUNCA ENTRÓ al store semántico. Revisar la ingesta:")
        print("        ¿se mapeó la variable? ¿se embebió el código en vez de la etiqueta?")
        print("        Ningún cambio de recuperación lo arregla.")
        return 1
    print(f"A1.1 · {len(filas)} respuesta(s) con «{args.patron}» (máx. {args.limite}):")
    for f in filas:
        marca = "  ⚠ código sin traducir" if _parece_codigo(f["texto_embebido"]) else ""
        print(f"  #{f['id']} [{f['codigo']}] {f['texto'][:60]!r}")
        print(f"      valor_texto    = {f['valor_texto']!r}")
        print(f"      texto_embebido = {f['texto_embebido']!r}{marca}")
    conjuntos = semantica.db.todas(
        conn,
        """select p.codigo, count(*) as n from respuesta r join pregunta p on p.id = r.pregunta_id
            where r.texto_embebido ~ '→ *[0-9]+$' or r.texto_embebido ilike '%%→ checked'
            group by 1 order by 2 desc limit 10""")
    if conjuntos:
        print("\n      Preguntas con respuestas embebidas como código (problema de ingesta conocido):")
        for c in conjuntos:
            print(f"        {c['codigo']}: {c['n']}")
    return 0


# ── A1.2 ─────────────────────────────────────────────────────────────
#
# Dos consultas con intenciones opuestas, y cada una necesita su plan:
#
# * «¿a qué distancia quedaron ESTAS filas?» (`_distancias`) mide filas
#   conocidas. Es un `where` selectivo y después un orden, y el índice HNSW
#   no corresponde: con `order by <=> … limit` Postgres resuelve por el
#   índice, que devuelve sus vecinos de **todo el corpus** (del orden de
#   `hnsw.ef_search`) y recién después aplica el `ilike`. Si ninguno de esos
#   vecinos tiene el patrón, el resultado es vacío, sin error
#   (`specs/BUG_diagnostico_a1_distancia_y_hallazgo.md`). Por eso el filtro
#   se materializa primero y la distancia se ordena sobre ese conjunto.
#
# * «¿cuántas del corpus están más cerca?» (`_delante`) es una pregunta
#   sobre el corpus entero. Es un conteo con la distancia en el `where`, sin
#   `order by … limit`: Postgres la resuelve recorriendo el corpus completo y
#   el número es exacto. Desde R-UE cuenta **unidades** (textos distintos),
#   que es lo que cuenta `top_n`.
#
# * R-UE — el vector vive en `unidad_evidencia`; una respuesta llega a él por
#   `respuesta.unidad_id`. Nada de este script lee `respuesta.embedding`, que
#   la semantica/0011 quita.

AUSENTE = "ausente"
"""El patrón no está en ninguna respuesta del corpus: es ingesta."""

NO_MEDIDO = "no_medido"
"""El patrón está, pero la consulta de distancia no devolvió esas filas: es
un fallo de la medición (plan, índice), no la ausencia de la respuesta."""

MEDIDO = "medido"

MENSAJE_AUSENTE = (
    "ninguna respuesta del corpus contiene «{patron}»: la evidencia NUNCA "
    "ENTRÓ (problema de ingesta; a1-texto da el detalle).")
MENSAJE_NO_MEDIDO = (
    "FALLO DE MEDICIÓN: {coincidencias} respuesta(s) contienen «{patron}», "
    "pero la consulta de distancia no devolvió ninguna. No es ausencia: "
    "no interpretar como resultado.")

_FILTRO_PATRON = "r.valor_texto ilike %s or r.texto_embebido ilike %s"


def _coincidencias(conn, patron):
    """Cuántas respuestas contienen el patrón, sin vector ni índice de por
    medio: es la referencia contra la que se controla la medición."""
    return semantica.db.una(
        conn, f"select count(*) as n from respuesta r where {_FILTRO_PATRON}",
        (f"%{patron}%", f"%{patron}%"))["n"]


def _distancias(conn, vector, patron, limite):
    """Las `limite` respuestas con el patrón más cercanas al vector, con su
    distancia exacta. El `materialized` impide que el planificador vuelva a
    juntar el filtro con el orden y elija el índice HNSW."""
    return semantica.db.todas(
        conn,
        f"""with filtradas as materialized (
                 select r.id, r.valor_texto, u.embedding <=> %s::vector as distancia
                   from respuesta r
                   join unidad_evidencia u on u.id = r.unidad_id
                  where {_FILTRO_PATRON}
             )
             select id, valor_texto, distancia
               from filtradas
              order by distancia, id
              limit %s""",
        (semantica._vector(vector), f"%{patron}%", f"%{patron}%", limite))


def _delante(conn, vector, distancia):
    """Cuántas unidades de evidencia (textos distintos) del corpus entero
    están más cerca que `distancia`. Unidades y no respuestas: es lo que
    cuenta `top_n` desde R-UE."""
    return semantica.db.una(
        conn,
        "select count(*) as n from unidad_evidencia u where u.embedding <=> %s::vector < %s",
        (semantica._vector(vector), distancia))["n"]


def _mas_cerca_con_patron(conn, vector, patron, distancia):
    """De las respuestas con el patrón, cuántas están más cerca que
    `distancia`. Conteo exacto, sin tope: antes salía de las primeras 1000
    filas de `_distancias`."""
    return semantica.db.una(
        conn,
        f"""select count(*) as total,
                   count(*) filter (where u.embedding <=> %s::vector < %s) as mas_cerca
              from respuesta r
              join unidad_evidencia u on u.id = r.unidad_id
             where {_FILTRO_PATRON}""",
        (semantica._vector(vector), distancia, f"%{patron}%", f"%{patron}%"))


def medir(conn, vector, patron, limite=5):
    """La medición de A1.2 para un vector, sin imprimir nada.

    Devuelve `estado` (`MEDIDO`, `AUSENTE` o `NO_MEDIDO`), cuántas respuestas
    contienen el patrón y, si se pudo medir, las más cercanas con su
    distancia. Que el resultado venga vacío se explica siempre: o el patrón
    no está, o la medición falló, y son dos mensajes distintos.
    """
    coincidencias = _coincidencias(conn, patron)
    if not coincidencias:
        return {"estado": AUSENTE, "coincidencias": 0, "filas": []}
    filas = _distancias(conn, vector, patron, limite)
    if not filas:
        return {"estado": NO_MEDIDO, "coincidencias": coincidencias, "filas": []}
    return {"estado": MEDIDO, "coincidencias": coincidencias, "filas": filas}


RECALL_INCORRECTO = (
    "→ EL RECALL NO LA TRAJO (plan={plan}): hay {delante} unidad(es) más cerca, "
    "menos que las {top_n} del pool, y no está entre las que devuelve. No es una "
    "falla de esta medición: es lo que hace el recall de la consulta con ese plan.")
FUERA_POR_DISTANCIA = (
    "→ Quedó fuera del pool por distancia: hay {delante} unidad(es) más cerca "
    "y el pool es de {top_n}.")
NO_APLICA_RECORTE = (
    "→ La conclusión del recorte NO APLICA: el recorte pasa después del recall, "
    "y esta respuesta no entró al recall.")
CALIDAD_EMBEDDING = (
    "→ Más lejos que 25 repeticiones de la otra respuesta: es CALIDAD DEL "
    "EMBEDDING; las unidades de evidencia no lo resuelven.")
RECORTE = (
    "→ Entró al recall y está más cerca que las repeticiones: si no llegó al "
    "resultado, fue el RECORTE posterior (cambio 3 justificado).")


def conclusiones(entra, delante, top_n, plan, mas_cerca_contra=None):
    """Lo que se concluye de una medición de A1.2, sin imprimir.

    Son excluyentes: si la respuesta no entró al recall, ninguna conclusión
    sobre lo que pasa **después** del recall (el recorte) se sostiene
    (`specs/BUG_recall_hnsw_completo_no_correcto.md` §5). Devuelve la lista de
    líneas y si es un defecto del sistema (para la salida 2).
    """
    if not entra and delante < top_n:
        lineas = [RECALL_INCORRECTO.format(plan=plan, delante=delante, top_n=top_n)]
        if mas_cerca_contra is not None:
            lineas.append(NO_APLICA_RECORTE)
        return lineas, True
    if not entra:
        lineas = [FUERA_POR_DISTANCIA.format(delante=delante, top_n=top_n)]
        if mas_cerca_contra is not None:
            lineas.append(CALIDAD_EMBEDDING if mas_cerca_contra >= 25 else NO_APLICA_RECORTE)
        return lineas, False
    if mas_cerca_contra is None:
        return [], False
    return [CALIDAD_EMBEDDING if mas_cerca_contra >= 25 else RECORTE], False


def _repetidas(candidatos, cuantas=5):
    """Los textos que más se repiten en el pool: qué ocupa los lugares."""
    cuenta = {}
    for c in candidatos:
        clave = (c["pregunta_codigo"], c["texto_embebido"])
        cuenta[clave] = cuenta.get(clave, 0) + 1
    return sorted(((n, codigo, texto) for (codigo, texto), n in cuenta.items() if n > 1),
                  key=lambda t: (-t[0], t[1] or "", t[2] or ""))[:cuantas]


def a1_distancia(args):
    conn = _conectar("DSN_SEMANTICA")
    proveedor = embeddings.crear()
    top_n = consultas.TOP_N_POR_DEFECTO
    print(f"A1.2 · criterio «{args.criterio}»")
    salida = 0
    for tipo in embeddings.TIPOS_DE_CRITERIO:
        vector = proveedor.embeber_criterio([args.criterio], tipo=tipo)[0]
        medicion = medir(conn, vector, args.patron)
        if medicion["estado"] == AUSENTE:
            print(f"  [{tipo}] " + MENSAJE_AUSENTE.format(patron=args.patron))
            salida = max(salida, 1)
            continue
        if medicion["estado"] == NO_MEDIDO:
            print(f"  [{tipo}] " + MENSAJE_NO_MEDIDO.format(
                patron=args.patron, coincidencias=medicion["coincidencias"]))
            salida = 2
            continue
        objetivo = medicion["filas"]
        mejor = float(objetivo[0]["distancia"])
        delante = _delante(conn, vector, mejor)
        print(f"  [{tipo}] {medicion['coincidencias']} respuesta(s) con «{args.patron}»; "
              f"la mejor (#{objetivo[0]['id']}, {objetivo[0]['valor_texto']!r}) "
              f"está a {mejor:.4f};")
        print(f"          {delante} unidad(es) de evidencia del corpus están más cerca "
              f"del criterio (top_n por defecto: {top_n} unidades).")
        # Lo que de verdad trae el recall de la consulta, con el mismo
        # `semantica.recuperar` y el mismo plan que producción.
        plan = {}
        candidatos = semantica.recuperar(conn, vector, top_n, informe=plan)
        recall = {c["respuesta_id"] for c in candidatos}
        entra = any(f["id"] in recall for f in objetivo)
        print(f"          recall real (top_n={top_n}, plan={plan.get('plan')}, "
              f"{plan.get('motivo_plan')}): {plan.get('unidades', 0)} unidad(es), "
              f"{len(recall)} respuesta(s); "
              f"{'entra' if entra else 'NO entra'} la mejor con «{args.patron}».")
        repetidas = _repetidas(candidatos)
        if repetidas:
            print("          textos más repetidos en el pool:")
            for n, codigo, texto in repetidas:
                print(f"            {n}× [{codigo}] {texto[:90]!r}")
        mas_cerca = None
        if args.contra:
            contra = _mas_cerca_con_patron(conn, vector, args.contra, mejor)
            mas_cerca = contra["mas_cerca"]
            print(f"          «{args.contra}»: {contra['total']} respuesta(s), "
                  f"{mas_cerca} más cerca que la buscada.")
        lineas, defecto = conclusiones(entra, delante, top_n, plan.get("plan"), mas_cerca)
        for linea in lineas:
            print(f"          {linea}")
        if defecto:
            salida = 2
    return salida


# ── Recall real: índice contra exacto ────────────────────────────────

def recall_indice(args):
    """Compara, para cada criterio, lo que trae el índice contra el exacto.

    Es la salvaguarda de `specs/BUG_recall_hnsw_completo_no_correcto.md`: un
    recall equivocado devuelve la cantidad justa y no se ve en ninguna
    cuenta. Solo lee. Sale con 1 si al índice le falta alguna.
    """
    conn = _conectar("DSN_SEMANTICA")
    proveedor = embeddings.crear()
    criterios = list(args.criterio or [])
    if args.consultas:
        criterios += [c["criterio"] for c in
                      json.loads(pathlib.Path(args.consultas).read_text(encoding="utf-8"))]
    if not criterios:
        sys.exit("Pasar --criterio (una o más veces) o --consultas.")
    corpus = semantica.tamano_corpus(conn)
    umbral = semantica.umbral_recall_exacto()
    print(f"Recall real · corpus ~{corpus} unidades de evidencia · RECALL_EXACTO_HASTA={umbral} · "
          f"producción usa {'EXACTO' if corpus <= umbral else 'ÍNDICE'} sin filtro")
    for indice in _indices_hnsw(conn):
        print(f"  índice {indice['indexname']}: {indice['parametros']} · {indice['tamano']}")
    salida = 0
    for criterio in criterios:
        for tipo in embeddings.TIPOS_DE_CRITERIO:
            vector = proveedor.embeber_criterio([criterio], tipo=tipo)[0]
            r = semantica.comparar_recall(conn, vector, args.top_n, args.ef_search)
            print(f"«{criterio}» [{tipo}] · ef_search={r['ef_search']} · "
                  f"recall@{r['top_n']} {100 * r['recall']:.1f}% · "
                  f"faltan {len(r['faltan'])} de {r['exactas']}")
            for f in r["faltan"][:5]:
                print(f"    puesto {f['puesto']} del exacto: unidad #{f['unidad_id']} "
                      f"a {f['distancia']:.4f}")
            if r["faltan"]:
                salida = 1
    return salida


def _indices_hnsw(conn):
    """Los índices HNSW de `unidad_evidencia` (el que usa el recall desde
    R-UE) y, mientras exista, el de `respuesta`, con `m` y `ef_construction`
    (los defaults de pgvector, 16 y 64, si el índice no los declara)."""
    filas = semantica.db.todas(
        conn,
        """select c.relname as indexname, c.reloptions,
                  pg_size_pretty(pg_relation_size(c.oid)) as tamano
             from pg_index i
             join pg_class c on c.oid = i.indexrelid
             join pg_am a on a.oid = c.relam
            where i.indrelid in ('unidad_evidencia'::regclass, 'respuesta'::regclass)
              and a.amname = 'hnsw'""")
    for f in filas:
        opciones = dict(o.split("=", 1) for o in (f["reloptions"] or []))
        f["parametros"] = (f"m={opciones.get('m', '16 (default)')}, "
                           f"ef_construction={opciones.get('ef_construction', '64 (default)')}")
    return filas


# ── A1.3 ─────────────────────────────────────────────────────────────

def a1_reranker(args):
    conn = _conectar("DSN_BOVEDA")
    existe = semantica.db.una(conn, "select to_regclass('consulta_ejecucion') as t")["t"]
    if not existe:
        print("A1.3 · La bóveda no tiene `consulta_ejecucion` (falta la 0025). Las ejecuciones")
        print("       anteriores no dejaron registro del reranker: aplicar la 0025 y volver")
        print("       a correr la consulta para saberlo.")
        return 1
    donde, parametros = "", [args.ultimas]
    if args.ejecucion_id:
        donde, parametros = "where id = %s", [args.ejecucion_id, 1]
    filas = semantica.db.todas(
        conn,
        f"""select id, alcance, creado_en, definicion->'criterios' as criterios,
                   diagnostico->>'reranker' as reranker,
                   diagnostico->>'tipo_embedding_criterio' as input_type,
                   diagnostico->'degradaciones' as degradaciones,
                   costo_real_usd
              from consulta_ejecucion {donde}
             order by creado_en desc limit %s""", parametros)
    if not filas:
        print("A1.3 · No hay ejecuciones registradas todavía.")
        return 1
    for f in filas:
        criterios = [c.get("etiqueta") or c.get("texto") or c.get("dimension")
                     for c in (f["criterios"] or [])]
        degradaciones = f["degradaciones"] or []
        rerank = [d for d in degradaciones if d.get("etapa") == "reranking"]
        print(f"A1.3 · {f['creado_en']:%Y-%m-%d %H:%M} {f['id']} ({f['alcance']})")
        print(f"       criterios: {criterios}")
        print(f"       reranker efectivo: {f['reranker']} · input_type: {f['input_type']} "
              f"· costo: US$ {float(f['costo_real_usd'] or 0):.4f}")
        if rerank or f["reranker"] in (None, "ninguno"):
            print("       → EL RERANKER NO CORRIÓ (o corrió a medias):")
            for d in rerank:
                print(f"         {d.get('motivo')} — {d.get('consecuencia')}")
            print("         Esto solo puede explicar un top lleno de respuestas irrelevantes.")
        else:
            print("       → el reranker corrió sin degradaciones.")
    return 0


# ── A2 ───────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self):
        self.boveda = _conectar("DSN_BOVEDA")
        self.semantica = _conectar("DSN_SEMANTICA")
        self.embeddings = embeddings.crear()
        self.verificador = verificacion.crear()


def estimar(args):
    ctx = _Ctx()
    definicion = {"criterios": args.criterio, "panel_id": args.panel_id,
                  "distancia_maxima": args.distancia_maxima}
    e = consulta_completa.estimar(ctx, definicion)
    est = e["estimacion"]
    print(f"A2 · {e['personas_habilitadas']} persona(s) habilitadas · {est['unidades']} "
          f"unidad(es) · {e['lotes']} lote(s) · ~{est['llamadas']} llamada(s) a Claude")
    for c in est["por_criterio"]:
        print(f"     «{c['criterio']}»: {c['unidades']} unidades, {c['llamadas']} llamadas, "
              f"{c['tokens_entrada']} → {c['tokens_salida']} tokens, US$ {c['usd']:.4f}")
    print(f"     Completa: US$ {est['usd']:.4f} (central {est['usd_central']:.4f}, "
          f"margen ×{est['margen']})")
    print(f"     Exploratoria (cota, top_k={consultas.TOP_K_POR_DEFECTO}): "
          f"US$ {e['exploratoria']['usd']:.4f}")
    print(f"     Máximo por ejecución: US$ {e['presupuesto_maximo_usd']:.2f} · "
          f"sugerido: US$ {e['presupuesto_sugerido_usd']:.2f}")
    print(f"     Tarifas: {json.dumps(est['tarifas'])}")
    print(f"     Supuestos: {est['supuestos']}")
    return 0


# ── A5 ───────────────────────────────────────────────────────────────

def input_type(args):
    """El archivo es una lista de `{criterio, esperadas: [respuesta_id…]}`."""
    conn = _conectar("DSN_SEMANTICA")
    proveedor = embeddings.crear()
    conocidas = json.loads(pathlib.Path(args.consultas).read_text(encoding="utf-8"))
    totales = {t: 0 for t in embeddings.TIPOS_DE_CRITERIO}
    esperadas_total = 0
    for caso in conocidas:
        listas = {}
        for tipo in embeddings.TIPOS_DE_CRITERIO:
            vector = proveedor.embeber_criterio([caso["criterio"]], tipo=tipo)[0]
            listas[tipo] = [c["respuesta_id"] for c in
                            semantica.recuperar(conn, vector, args.top_n)]
        esperadas = set(caso.get("esperadas") or [])
        esperadas_total += len(esperadas)
        partes = []
        for tipo, ids in listas.items():
            halladas = len(esperadas & set(ids))
            totales[tipo] += halladas
            partes.append(f"{tipo}: {halladas}/{len(esperadas)}")
        comun = len(set(listas["query"]) & set(listas["document"]))
        largo = max(len(listas["query"]), len(listas["document"]), 1)
        print(f"«{caso['criterio']}» · recall@{args.top_n} {' · '.join(partes)} · "
              f"solapamiento de las dos listas {comun}/{largo}")
    if esperadas_total:
        print("\nTotal: " + " · ".join(
            f"{t}: {n}/{esperadas_total} ({100 * n / esperadas_total:.0f}%)"
            for t, n in totales.items()))
        print("Si `query` no mejora o empeora, volver atrás con "
              "EMBEDDINGS_TIPO_CONSULTA=document (sin redesplegar).")
    return 0


# ── R-UE.5 · unidades de evidencia ───────────────────────────────────

def chequear_unidades(conn):
    """El estado de las unidades de evidencia, sin imprimir nada.

    `migrada` dice si la semantica/0010 está aplicada. Antes: respuestas,
    textos distintos y textos con más de un vector (R-UE.5). Después,
    además: unidades, respuestas sin unidad, respuestas cuyo texto no es el
    de su unidad, unidades huérfanas y la distribución de personas por
    unidad. `problemas` es la lista de lo que no cierra.
    """
    db = semantica.db
    migrada = bool(db.una(conn, "select to_regclass('unidad_evidencia') as t")["t"])
    tiene_vector = bool(db.una(
        conn, """select 1 as x from information_schema.columns
                  where table_name = 'respuesta' and column_name = 'embedding'"""))
    base = db.una(conn, """select count(*) as respuestas,
                                  count(distinct texto_embebido) as textos,
                                  count(*) filter (where hash_texto is null) as sin_huella
                             from respuesta""")
    salida = {"migrada": migrada, "respuestas": base["respuestas"],
              "textos_distintos": base["textos"], "sin_huella": base["sin_huella"],
              "problemas": []}
    if tiene_vector:
        filas = db.todas(conn, """
            select coalesce(hash_texto, encode(sha256(convert_to(texto_embebido, 'UTF8')), 'hex'))
                     as hash_texto,
                   count(*) as respuestas, count(distinct embedding::text) as vectores
              from respuesta
             group by 1
            having count(distinct embedding::text) > 1
             order by 2 desc""")
        salida["textos_con_mas_de_un_vector"] = [dict(f) for f in filas]
        if filas:
            salida["problemas"].append(
                f"{len(filas)} texto(s) con más de un vector entre sus respuestas")
    if not migrada:
        return salida
    despues = db.una(conn, """
        select (select count(*) from unidad_evidencia) as unidades,
               (select count(*) from respuesta where unidad_id is null) as sin_unidad,
               (select count(*) from respuesta r join unidad_evidencia u on u.id = r.unidad_id
                 where u.texto_embebido <> r.texto_embebido) as texto_distinto,
               (select count(*) from unidad_evidencia u
                 where not exists (select 1 from respuesta r where r.unidad_id = u.id))
                 as huerfanas""")
    salida.update({k: despues[k] for k in ("unidades", "sin_unidad", "texto_distinto",
                                            "huerfanas")})
    salida["mas_compartidas"] = [dict(f) for f in db.todas(conn, """
        select unidad_id, respuestas, individuos as personas, preguntas
          from v_unidad_evidencia order by individuos desc, unidad_id limit 10""")]
    if salida["unidades"] != salida["textos_distintos"]:
        salida["problemas"].append(
            f"{salida['unidades']} unidades para {salida['textos_distintos']} textos distintos")
    for clave, texto in (("sin_unidad", "respuesta(s) sin unidad"),
                         ("texto_distinto", "respuesta(s) con otro texto que su unidad"),
                         ("huerfanas", "unidad(es) sin respuestas")):
        if salida[clave]:
            salida["problemas"].append(f"{salida[clave]} {texto}")
    return salida


def unidades(args):
    conn = _conectar("DSN_SEMANTICA")
    r = chequear_unidades(conn)
    print(f"R-UE · semantica/0010 {'APLICADA' if r['migrada'] else 'SIN APLICAR'}")
    print(f"  {r['respuestas']} respuestas · {r['textos_distintos']} textos distintos"
          + (f" · {100 * (1 - r['textos_distintos'] / r['respuestas']):.1f}% son copias"
             if r["respuestas"] else ""))
    if "textos_con_mas_de_un_vector" in r:
        inconsistentes = r["textos_con_mas_de_un_vector"]
        print(f"  textos con más de un vector: {len(inconsistentes)}")
        for f in inconsistentes[:10]:
            print(f"    {f['hash_texto'][:16]}… · {f['respuestas']} respuestas · "
                  f"{f['vectores']} vectores distintos")
    if r["migrada"]:
        print(f"  {r['unidades']} unidades · {r['sin_unidad']} respuestas sin unidad · "
              f"{r['texto_distinto']} con otro texto · {r['huerfanas']} huérfanas")
        print("  unidades con más personas detrás:")
        for f in r["mas_compartidas"]:
            print(f"    unidad #{f['unidad_id']}: {f['personas']} personas, "
                  f"{f['respuestas']} respuestas, {f['preguntas']} pregunta(s)")
    for problema in r["problemas"]:
        print(f"  → PROBLEMA: {problema}")
    if not r["problemas"]:
        print("  → sin inconsistencias")
    return 1 if r["problemas"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("a1-texto", help="A1.1 — ¿la respuesta está en el store?")
    p.add_argument("--patron", required=True)
    p.add_argument("--limite", type=int, default=50)
    p.set_defaults(funcion=a1_texto)

    p = sub.add_parser("a1-distancia", help="A1.2 — distancia al criterio")
    p.add_argument("--criterio", required=True)
    p.add_argument("--patron", required=True, help="texto de la respuesta buscada")
    p.add_argument("--contra", help="texto de la respuesta repetida que ocupó el top")
    p.set_defaults(funcion=a1_distancia)

    p = sub.add_parser("recall-indice",
                       help="¿el índice trae las mismas que el recorrido exacto?")
    p.add_argument("--criterio", action="append")
    p.add_argument("--consultas", help="JSON: [{criterio, …}] (el mismo de input-type)")
    p.add_argument("--top-n", type=int, default=consultas.TOP_N_POR_DEFECTO)
    p.add_argument("--ef-search", type=int,
                   help="por defecto, el que usaría producción (2 × top_n, tope 1000)")
    p.set_defaults(funcion=recall_indice)

    p = sub.add_parser("unidades",
                       help="R-UE.5 — ¿mismo texto, mismo vector? ¿toda respuesta con unidad?")
    p.set_defaults(funcion=unidades)

    p = sub.add_parser("a1-reranker", help="A1.3 — ¿corrió el reranker?")
    p.add_argument("--ultimas", type=int, default=5)
    p.add_argument("--ejecucion-id")
    p.set_defaults(funcion=a1_reranker)

    p = sub.add_parser("estimar", help="A2 — costo de una completa")
    p.add_argument("--criterio", action="append", required=True)
    p.add_argument("--panel-id", type=int)
    p.add_argument("--distancia-maxima", type=float)
    p.set_defaults(funcion=estimar)

    p = sub.add_parser("input-type", help="A5 — query vs document en consultas conocidas")
    p.add_argument("--consultas", required=True, help="JSON: [{criterio, esperadas}]")
    p.add_argument("--top-n", type=int, default=consultas.TOP_N_POR_DEFECTO)
    p.set_defaults(funcion=input_type)

    args = parser.parse_args()
    sys.exit(args.funcion(args))


if __name__ == "__main__":
    main()
