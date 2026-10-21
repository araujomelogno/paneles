"""Cliente del store semántico (Postgres + pgvector).

Es el único módulo que escribe del otro lado. Toda escritura pasa primero
por el guardrail de PII (R1.6): si un payload trae una clave de PII, la
operación aborta antes de tocar la base.

Lo único que viaja al store semántico: `id_persona`, `ref_estudio`, metadatos del
cuestionario y de las preguntas, el texto de respuesta ya despersonalizado
y su embedding.
"""

import hashlib
import json
import os

import psycopg

from . import db, pii
from .errores import NoEncontrado


def _vector(valores):
    """Un embedding como literal de pgvector."""
    return "[" + ",".join(f"{float(v):.7g}" for v in valores) + "]"


def asegurar_cuestionario(conn, ref_estudio, nombre, fecha_campo=None, metadata=None,
                          normalizacion=None):
    """Crea (o recupera) el cuestionario del estudio. Idempotente por
    `ref_estudio`, que es la misma uuid que `encuesta.ref_estudio` del
    store de bóveda: ese es el puente entre los dos stores.

    `normalizacion` (Fase 8) es la configuración de la carga que no es de
    ninguna pregunta —la lista de valores de no respuesta—. Se guarda para
    que un reproceso sepa qué se había decidido. `None` no pisa lo que haya:
    una re-ingesta sin configuración no borra la de la carga anterior.
    """
    metadata = metadata or {}
    pii.validar_sin_pii({"nombre": nombre, "metadata": metadata,
                         "normalizacion": normalizacion or {}},
                        contexto="cuestionario")

    fila = db.una(
        conn,
        """
        insert into cuestionario (nombre, fecha_campo, ref_estudio, metadata,
                                  normalizacion)
             values (%s, %s, %s, %s::jsonb, coalesce(%s::jsonb, '{}'::jsonb))
        on conflict (ref_estudio) do update
                set nombre = excluded.nombre,
                    fecha_campo = coalesce(excluded.fecha_campo, cuestionario.fecha_campo),
                    normalizacion = coalesce(%s::jsonb, cuestionario.normalizacion)
          returning id
        """,
        (nombre, fecha_campo, str(ref_estudio), json.dumps(metadata),
         json.dumps(normalizacion) if normalizacion else None,
         json.dumps(normalizacion) if normalizacion else None),
    )
    return fila["id"]


def fechar_cuestionario(conn, ref_estudio, fecha_campo, solo_si_vacia=False):
    """Fija la fecha del cuestionario de un estudio.

    `asegurar_cuestionario` no pisa una fecha con `None`, y está bien para
    una re-ingesta; pero la fecha de una carga se corrige, y la corrección
    tiene que llegar. Con `solo_si_vacia` no pisa la que haya: es lo que
    usa completar las cargas anteriores. Devuelve si cambió algo.
    """
    fila = db.una(
        conn,
        """update cuestionario set fecha_campo = %s::date
            where ref_estudio = %s
              and (not %s or fecha_campo is null)
              and fecha_campo is distinct from %s::date
        returning id""",
        (fecha_campo, str(ref_estudio), solo_si_vacia, fecha_campo),
    )
    return fila is not None


def asegurar_individuos(conn, ids_persona):
    """Crea los `individuo` que falten y devuelve `{id_persona: individuo_id}`.

    Un `individuo` del store semántico es un token opaco y nada más: no tiene ni puede
    tener PII.
    """
    ids = [str(i) for i in dict.fromkeys(ids_persona)]
    if not ids:
        return {}
    pii.validar_sin_pii({"id_persona": ids}, contexto="individuo")

    with conn.cursor() as cur:
        cur.executemany(
            "insert into individuo (id_persona) values (%s) "
            "on conflict (id_persona) do nothing",
            [(i,) for i in ids],
        )
    filas = db.todas(
        conn,
        "select id, id_persona from individuo where id_persona = any(%s::uuid[])",
        (ids,),
    )
    return {str(f["id_persona"]): f["id"] for f in filas}


# Las decisiones de normalización de una pregunta (Fase 8) que se guardan
# con ella. Son las mismas claves que lee `ingesta.respuesta_de`, más las que
# documentan de dónde salió la pregunta (`fusionada_con`, `bateria`) y lo que
# el analista aceptó a conciencia (`pii_aceptada`).
CLAVES_DE_NORMALIZACION = (
    "solo_marcadas", "valores_marcados", "excluir_valores",
    "prefijo_respuesta", "fusionada_con", "bateria", "pii_aceptada",
    "excluida",
)


def normalizacion_de(pregunta):
    """La parte de la pregunta que es decisión de normalización."""
    return {c: pregunta[c] for c in CLAVES_DE_NORMALIZACION
            if pregunta.get(c) not in (None, "", [], False)}


def upsert_preguntas(conn, cuestionario_id, preguntas):
    """`preguntas`: [{codigo, texto, tipo, opciones, orden, ...}]. Idempotente
    por (cuestionario, codigo).

    Fase 8 — guarda también el **texto original** del archivo junto al
    editado (`texto_original`, y las etiquetas en `opciones_originales`),
    para poder volver y para auditar qué se cambió, y las decisiones de
    normalización con las que se embebió. El original no se pisa con nada
    vacío: una re-ingesta que no lo trae conserva el que había.

    Si el texto cambia, la huella de la pregunta para sugerir series
    (`embedding_texto`) queda vieja: se anula y `series` la recalcula a pedido.
    """
    pii.validar_sin_pii(preguntas, contexto="pregunta")
    codigos = []
    for p in preguntas:
        original = (p.get("texto_original") or p.get("texto_del_archivo")
                    or None)
        db.ejecutar(
            conn,
            """
            insert into pregunta (cuestionario_id, codigo, texto, tipo, opciones,
                                  orden, texto_original, opciones_originales,
                                  normalizacion)
                 values (%s, %s, %s, %s, %s::jsonb, %s,
                         coalesce(%s, %s), coalesce(%s::jsonb, %s::jsonb),
                         %s::jsonb)
            on conflict (cuestionario_id, codigo) do update
                    set texto = excluded.texto,
                        tipo = excluded.tipo,
                        opciones = excluded.opciones,
                        orden = excluded.orden,
                        texto_original = coalesce(%s, pregunta.texto_original,
                                                  excluded.texto_original),
                        opciones_originales = coalesce(
                            %s::jsonb, pregunta.opciones_originales,
                            excluded.opciones_originales),
                        normalizacion = excluded.normalizacion,
                        embedding_texto = case
                            when pregunta.texto is distinct from excluded.texto
                            then null else pregunta.embedding_texto end
            """,
            (
                cuestionario_id,
                p["codigo"],
                p["texto"],
                p.get("tipo"),
                json.dumps(p.get("opciones")) if p.get("opciones") else None,
                p.get("orden"),
                original, p["texto"],
                json.dumps(p.get("opciones_originales"))
                if p.get("opciones_originales") else None,
                json.dumps(p.get("opciones")) if p.get("opciones") else None,
                json.dumps(normalizacion_de(p)),
                original,
                json.dumps(p.get("opciones_originales"))
                if p.get("opciones_originales") else None,
            ),
        )
        codigos.append(p["codigo"])
    filas = db.todas(
        conn,
        "select id, codigo from pregunta where cuestionario_id = %s and codigo = any(%s)",
        (cuestionario_id, codigos),
    )
    return {f["codigo"]: f["id"] for f in filas}


def hash_texto(texto):
    """Huella del texto exacto que se vectoriza.

    Con el mismo proveedor y el mismo modelo, el mismo texto da el mismo
    vector. Guardar la huella deja saltear el embedding en una re-ingesta
    (P1), que es la parte que cuesta plata y tiempo.
    """
    return hashlib.sha256(str(texto).encode("utf-8")).hexdigest()


# ════════════════════════════════════════════════════════════════════
#  R-UE — Unidades de evidencia: un vector por texto distinto
# ════════════════════════════════════════════════════════════════════
#
# El vector vive en `unidad_evidencia` (semantica/0010), una vez por texto
# embebido distinto; cada respuesta la referencia por `unidad_id`. Medido
# sobre el corpus real, 21.340 respuestas eran 861 textos: el 96 % del
# corpus eran copias, y las copias rompían el índice HNSW (bloques de puntos
# idénticos que la búsqueda no alcanza) y gastaban la verificación en la
# misma idea repetida. Ver `specs/SPEC_unidades_de_evidencia.md` y D73.
#
# **Una sola forma de escribir respuestas**: `escribir_respuestas`, que usan
# la ingesta y el reproceso. Resuelve las unidades que ya existen, embebe
# **solo los textos nuevos** —una vez cada uno, aunque lleguen mil copias en
# la misma carga— y después escribe las respuestas apuntando a su unidad.

def unidades_por_hash(conn, huellas):
    """`{hash_texto: unidad_id}` de las huellas que ya tienen unidad."""
    huellas = list(dict.fromkeys(str(h) for h in huellas))
    if not huellas:
        return {}
    filas = db.todas(
        conn,
        "select id, hash_texto from unidad_evidencia where hash_texto = any(%s::text[])",
        (huellas,))
    return {f["hash_texto"]: f["id"] for f in filas}


def crear_unidades(conn, unidades):
    """`unidades`: `[{hash_texto, texto_embebido, embedding}]`. Idempotente
    por huella: si otra tarea ya creó la unidad, se conserva la que estaba
    (el mismo texto con el mismo modelo da el mismo vector)."""
    if not unidades:
        return 0
    pii.validar_sin_pii(
        [{k: v for k, v in u.items() if k != "embedding"} for u in unidades],
        contexto="unidad_evidencia")
    with conn.cursor() as cur:
        cur.executemany(
            """insert into unidad_evidencia (hash_texto, texto_embebido, embedding)
               values (%s, %s, %s::vector)
               on conflict (hash_texto) do nothing""",
            [(u["hash_texto"], u["texto_embebido"], _vector(u["embedding"]))
             for u in unidades])
    return len(unidades)


class SinUnidad(RuntimeError):
    """Se intentó escribir una respuesta cuyo texto nunca se embebió. Es un
    error de programación: `escribir_respuestas` crea las unidades antes."""


class RespuestaSinVector(SinUnidad):
    """Una respuesta iba a apuntar a una unidad que no existe o no tiene
    vector. Dice **qué** huellas y **qué** preguntas: el error de Postgres
    («null value in column "embedding"… violates not-null constraint») no lo
    decía, y reconstruirlo a mano costó horas (BUG del embedding nulo)."""


def _sin_repetidas(respuestas):
    """Una respuesta por `(individuo_id, pregunta_id)`: la **última**, que es
    con la que quedaba la escritura fila por fila de antes. Pasa cuando el
    dedup fusiona dos filas del archivo en una persona, o con la fusión de
    una cerrada con su «Otro, especificar». Devuelve `(unicas, repetidas)`."""
    ultima = {}
    for r in respuestas:
        ultima[(r["individuo_id"], r["pregunta_id"])] = r
    unicas = list(ultima.values())
    return unicas, len(respuestas) - len(unicas)


def _exigir_unidades_con_vector(conn, respuestas, unidad_de):
    """Las unidades a las que van a apuntar estas respuestas existen y tienen
    vector, y quedan **bloqueadas** (`for key share`) hasta el commit: otra
    tarea que deje una de ellas huérfana no la puede borrar en el medio.

    Si falta alguna, levanta `RespuestaSinVector` con la huella y el código
    de pregunta de cada una, y lo deja en el log (`[ingesta]`, huellas y
    códigos: nunca el texto, que es contenido)."""
    ids = sorted({unidad_de[r["hash_texto"]] for r in respuestas})
    con_vector = {
        f["id"] for f in db.todas(
            conn,
            """select id from unidad_evidencia
                where id = any(%s::bigint[]) and embedding is not null
                for key share""",
            (ids,))
    }
    malas = [r for r in respuestas if unidad_de[r["hash_texto"]] not in con_vector]
    if malas:
        _falta_vector(conn, malas)


def _falta_vector(conn, malas, causa=None):
    codigo_de = {
        f["id"]: f["codigo"] for f in db.todas(
            conn, "select id, codigo from pregunta where id = any(%s::bigint[])",
            (sorted({r["pregunta_id"] for r in malas}),))
    }
    casos = sorted({(r["hash_texto"], codigo_de.get(r["pregunta_id"], "?"))
                    for r in malas})
    for huella, codigo in casos[:20]:
        print(f"[ingesta] respuesta sin vector: huella={huella} pregunta={codigo}"
              + (f" error={type(causa).__name__}" if causa else ""), flush=True)
    detalle = "; ".join(f"pregunta {c}, huella {h}" for h, c in casos[:5])
    raise RespuestaSinVector(
        f"{len(malas)} respuesta(s) iban a apuntar a una unidad de evidencia "
        f"que no existe o no tiene vector ({detalle}"
        f"{'; …' if len(casos) > 5 else ''}). Ninguna se escribió. "
        f"Reintentar el lote vuelve a resolver las unidades."
        # La primera línea del error de Postgres, si lo hubo: dice qué
        # restricción saltó. El DETAIL no, que trae la fila con su texto.
        + (f" Error de Postgres: {str(causa).splitlines()[0]}" if causa else "")
    ) from causa


def upsert_respuestas(conn, respuestas):
    """`respuestas`: [{individuo_id, pregunta_id, valor_texto, texto_embebido,
    hash_texto?, embedding?}]. Idempotente por (individuo, pregunta):
    re-ingestar no duplica. Si llegan dos para la misma persona y pregunta,
    queda la última (`_sin_repetidas`).

    Cada respuesta apunta a la unidad de su texto. Si la fila trae
    `embedding`, la unidad se crea con ese vector cuando falta; si no lo
    trae, la unidad **tiene** que existir (`SinUnidad`): una respuesta sin
    vector sería invisible para la búsqueda y contaría en los totales.

    **Se escriben en una sola sentencia**, y no es por velocidad. El trigger
    que borra las unidades huérfanas (`semantica/0010`) corre al final de
    cada sentencia: escribiendo fila por fila, un `UPDATE` que cambiaba el
    texto de una respuesta borraba la unidad del texto anterior **a mitad del
    lote**, y si otra respuesta posterior tenía ese mismo texto, apuntaba a
    una unidad que ya no estaba y su vector quedaba nulo (BUG del embedding
    nulo, carga 28). En una sola sentencia el trigger corre cuando todas ya
    apuntan a su unidad, y solo borra la que de verdad quedó sin nadie.

    `respuesta.embedding` no se escribe: mientras exista (hasta la
    semantica/0011), lo completa el trigger de la 0010 desde la unidad.
    """
    pii.validar_sin_pii(
        [{k: v for k, v in r.items() if k != "embedding"} for r in respuestas],
        contexto="respuesta",
    )
    for r in respuestas:
        r["hash_texto"] = r.get("hash_texto") or hash_texto(r["texto_embebido"])
    respuestas, _ = _sin_repetidas(respuestas)
    if not respuestas:
        return 0
    crear_unidades(conn, [
        {"hash_texto": r["hash_texto"], "texto_embebido": r["texto_embebido"],
         "embedding": r["embedding"]}
        for r in respuestas if r.get("embedding") is not None])
    unidad_de = unidades_por_hash(conn, (r["hash_texto"] for r in respuestas))
    faltan = {r["hash_texto"] for r in respuestas} - set(unidad_de)
    if faltan:
        raise SinUnidad(
            f"{len(faltan)} texto(s) sin unidad de evidencia: hay que embeberlos "
            f"antes de escribir sus respuestas (semantica.escribir_respuestas).")
    _exigir_unidades_con_vector(conn, respuestas, unidad_de)

    # En un savepoint, para poder diagnosticar si falla sin deshacer lo que
    # quien llama ya hizo en esta transacción.
    db.ejecutar(conn, "savepoint antes_de_las_respuestas")
    try:
        return db.ejecutar(
            conn,
            """
            insert into respuesta
                   (individuo_id, pregunta_id, valor_texto, texto_embebido,
                    hash_texto, unidad_id)
            select * from unnest(%s::bigint[], %s::bigint[], %s::text[],
                                 %s::text[], %s::text[], %s::bigint[])
            on conflict (individuo_id, pregunta_id) do update
                    set valor_texto = excluded.valor_texto,
                        texto_embebido = excluded.texto_embebido,
                        hash_texto = excluded.hash_texto,
                        unidad_id = excluded.unidad_id
            """,
            ([r["individuo_id"] for r in respuestas],
             [r["pregunta_id"] for r in respuestas],
             [r.get("valor_texto") for r in respuestas],
             [r["texto_embebido"] for r in respuestas],
             [r["hash_texto"] for r in respuestas],
             [unidad_de[r["hash_texto"]] for r in respuestas]),
        )
    except (psycopg.errors.NotNullViolation,
            psycopg.errors.ForeignKeyViolation) as error:
        # No debería pasar con las unidades bloqueadas; si pasa, el error
        # dice cuáles, en vez de la violación sola.
        db.ejecutar(conn, "rollback to savepoint antes_de_las_respuestas")
        vivas = {f["id"] for f in db.todas(
            conn, "select id from unidad_evidencia where id = any(%s::bigint[]) "
                  "and embedding is not null",
            (sorted(set(unidad_de.values())),))}
        _falta_vector(conn, [r for r in respuestas
                             if unidad_de[r["hash_texto"]] not in vivas] or respuestas,
                      causa=error)


def escribir_respuestas(conn, respuestas, proveedor):
    """El camino único de escritura de la ingesta y el reproceso (R-UE.4).

    1. Calcula la huella de cada respuesta.
    2. Las huellas que ya tienen unidad se reutilizan **sin embeber**.
    3. Los textos nuevos se embeben **una vez cada uno**, sub-lote a
       sub-lote, y cada sub-lote se guarda como unidades y se suelta antes
       de pedir el siguiente (R-ASYNC.2.b: nunca hay más de un sub-lote de
       vectores vivo).
    4. Se escriben las respuestas, apuntando a su unidad.

    Antes de mandar nada al proveedor se compara su dimensión con la de
    `unidad_evidencia.embedding`: un desajuste haría pagar el lote para que
    el insert lo rechace después.

    Devuelve `{escritas, textos_embebidos, respuestas_reutilizadas,
    repetidas}`: cuántas respuestas se escribieron, cuántos textos se
    mandaron a embeber, cuántas apuntaron a una unidad que ya existía y
    cuántas se descartaron por repetir persona y pregunta.
    """
    from . import esquema
    from .errores import DatosInvalidos

    for r in respuestas:
        r["hash_texto"] = r.get("hash_texto") or hash_texto(r["texto_embebido"])
        r["embedding"] = None
    # Antes de embeber: un texto que solo traía la respuesta repetida que se
    # descarta no se paga, y su unidad no quedaría huérfana para siempre (el
    # trigger solo mira las respuestas que se borran o cambian).
    respuestas, repetidas = _sin_repetidas(respuestas)
    if repetidas:
        print(f"[ingesta] {repetidas} respuesta(s) repetidas para la misma "
              f"persona y pregunta: queda la última", flush=True)
    existentes = unidades_por_hash(conn, (r["hash_texto"] for r in respuestas))
    nuevos = {}
    for r in respuestas:
        if r["hash_texto"] not in existentes:
            nuevos.setdefault(r["hash_texto"], r["texto_embebido"])

    if nuevos:
        desajuste = esquema.desajuste_de_dimension(conn, getattr(proveedor, "dims", None))
        if desajuste:
            raise DatosInvalidos(desajuste, {"motivo": "dimension_de_embeddings"})
        # La guarda de PII va antes de embeber: el texto de una columna
        # identificatoria no tiene que llegar ni al proveedor.
        pii.validar_sin_pii(
            [{k: v for k, v in r.items() if k != "embedding"} for r in respuestas],
            contexto="respuesta")
        pendientes = list(nuevos.items())
        for inicio, vectores in proveedor.embeber_por_lotes([t for _, t in pendientes]):
            sublote = pendientes[inicio:inicio + len(vectores)]
            crear_unidades(conn, [
                {"hash_texto": h, "texto_embebido": t, "embedding": v}
                for (h, t), v in zip(sublote, vectores)])
            del vectores

    escritas = upsert_respuestas(conn, respuestas) if respuestas else 0
    return {
        "escritas": escritas,
        "textos_embebidos": len(nuevos),
        "respuestas_reutilizadas": sum(
            1 for r in respuestas if r["hash_texto"] in existentes),
        "repetidas": repetidas,
    }


# ════════════════════════════════════════════════════════════════════
#  R2.7 — Recuperación semántica (recall por vecino aproximado)
# ════════════════════════════════════════════════════════════════════

EF_SEARCH_MINIMO = 40
"""El `hnsw.ef_search` por defecto de pgvector."""

EF_SEARCH_MAXIMO = 1000
"""El tope que acepta pgvector. Un pedido más grande no cabe en el índice."""

MARGEN_EF_SEARCH = 2
"""Cuántas veces `top_n` se le pide al índice (`ef_search`). Con `ef_search`
igual a `top_n` el grafo explora apenas lo justo, y el vecino de verdad
puede quedar afuera aunque vuelvan las `top_n` filas pedidas (D72)."""

RECALL_EXACTO_HASTA_POR_DEFECTO = 200_000
"""Hasta cuántas **unidades de evidencia** el recall sin filtro es exacto
(D72, D73). Medido en local, recorrer 21.340 vectores de 512 dimensiones son
~115 ms, así que 200 mil rondan el segundo: todavía poco frente a los
segundos de la verificación con Claude. Desde R-UE se cuentan unidades (861
con el corpus de hoy), no respuestas: el umbral queda mucho más lejos."""


def umbral_recall_exacto(entorno=None):
    """`RECALL_EXACTO_HASTA`, o el default si falta o no es un entero ≥ 0.

    Un valor ilegible no se adivina ni apaga el camino exacto: se usa el
    default y queda una línea en el log.
    """
    entorno = os.environ if entorno is None else entorno
    crudo = (entorno.get("RECALL_EXACTO_HASTA") or "").strip().replace("_", "")
    if not crudo:
        return RECALL_EXACTO_HASTA_POR_DEFECTO
    try:
        valor = int(crudo)
        if valor < 0:
            raise ValueError
        return valor
    except ValueError:
        print(f"[recall] RECALL_EXACTO_HASTA={crudo!r} no es un entero ≥ 0; "
              f"se usa {RECALL_EXACTO_HASTA_POR_DEFECTO}")
        return RECALL_EXACTO_HASTA_POR_DEFECTO


def ef_search_para(top_n):
    """El `ef_search` del índice para un pedido: `MARGEN_EF_SEARCH × top_n`,
    entre el default de pgvector y su tope."""
    return min(max(top_n * MARGEN_EF_SEARCH, EF_SEARCH_MINIMO), EF_SEARCH_MAXIMO)


def tamano_corpus(conn):
    """Cuántas unidades de evidencia hay (R-UE: el recall busca entre
    unidades, no entre respuestas), para decidir el plan.

    La estimación del catálogo (`reltuples`) y no un `count(*)`: decidir el
    plan no puede costar un recorrido de la tabla, que es justo lo que el
    índice evita por encima del umbral. Si la tabla nunca se analizó
    (`reltuples` = -1), se cuenta.
    """
    fila = db.una(conn, "select reltuples::bigint as n from pg_class "
                        "where oid = 'unidad_evidencia'::regclass")
    if fila and fila["n"] is not None and fila["n"] >= 0:
        estimado = int(fila["n"])
        if estimado > 0:
            return estimado
    return int(db.una(conn, "select count(*) as n from unidad_evidencia")["n"])


def _vecinos_exactos(conn, literal, top_n, ids_individuo):
    """Las `top_n` **unidades** más cercanas recorriendo el conjunto entero,
    sin índice. Con `ids_individuo`, solo las unidades que tienen al menos
    una respuesta de esas personas.

    El `materialized` es lo que lo garantiza: sin él, el planificador junta
    el filtro con el `order by <=> … limit` y vuelve a elegir el índice HNSW.
    """
    return db.todas(
        conn,
        """
        with candidatas as materialized (
            select u.id, u.embedding <=> %s::vector as distancia
              from unidad_evidencia u
             where (%s::bigint[] is null
                    or u.id in (select r.unidad_id from respuesta r
                                 where r.individuo_id = any(%s::bigint[])))
        )
        select id, distancia from candidatas order by distancia, id limit %s
        """,
        (literal, ids_individuo, ids_individuo, top_n),
    )


def _vecinos_indice(conn, literal, top_n, ef_search):
    """Las `top_n` unidades que devuelve el índice HNSW con ese `ef_search`.
    Sin filtro: con un `where` el índice no corresponde (D71)."""
    db.una(conn, "select set_config('hnsw.ef_search', %s, true)", (str(ef_search),))
    return db.todas(
        conn,
        """
        select u.id, u.embedding <=> %s::vector as distancia
          from unidad_evidencia u
         order by u.embedding <=> %s::vector
         limit %s
        """,
        (literal, literal, top_n),
    )


def _vecinos(conn, literal, top_n, ids_individuo=None, informe=None, entorno=None):
    """Las `top_n` unidades de evidencia más cercanas, con el plan que
    corresponde. Devuelve `[{id, distancia}]`, con `id` de la unidad.

    R-UE — se busca entre **unidades** y no entre respuestas: con cientos de
    respuestas idénticas, el índice veía bloques de vectores iguales que no
    alcanzaba desde afuera (0 % de recall para «celular xiaomi» con
    `ef_search = 200`). Entre textos distintos ese bloque no existe.

    El índice HNSW es **aproximado** y **no sabe de filtros**. Eso deja tres
    trampas, y ninguna falla: devuelven algo plausible.

    * Con un filtro por personas (el gate de consentimiento), el índice
      devuelve vecinos de todo el corpus **antes** del filtro y el recall de
      los habilitados sale corto (D71). Con filtro, siempre exacto.
    * Un `top_n` mayor que `ef_search` recibe `ef_search` filas (D71).
    * Y aun con la cantidad justa, **las filas pueden no ser las más
      cercanas**: el índice puede devolver 200 de 200 dejando afuera al
      vecino más cercano del corpus
      (`specs/BUG_recall_hnsw_completo_no_correcto.md`). Ninguna cuenta lo
      detecta. Por eso, sin filtro, el recall es exacto mientras el corpus
      no pase de `RECALL_EXACTO_HASTA` (200 mil por defecto); por encima, el
      índice con `ef_search` = `MARGEN_EF_SEARCH × top_n`, y el chequeo de
      recall real (`comparar_recall`) es lo que dice cuánto pierde.

    Si el índice devuelve menos de lo pedido habiendo más, se registra
    (`[recall]`) y se recalcula exacto.

    `informe`, si viene, se completa con el plan (`indice` o `exacto`), el
    motivo del plan y, con índice, el `ef_search`: va al diagnóstico.
    """
    informe = informe if informe is not None else {}
    if ids_individuo is not None:
        informe.update(plan="exacto", motivo_plan="filtro_por_personas")
        return _vecinos_exactos(conn, literal, top_n, ids_individuo)

    corpus = tamano_corpus(conn)
    umbral = umbral_recall_exacto(entorno)
    if corpus <= umbral:
        informe.update(plan="exacto", motivo_plan="corpus_bajo_umbral")
        return _vecinos_exactos(conn, literal, top_n, None)

    ef_search = ef_search_para(top_n)
    if ef_search <= top_n:
        # No hay margen posible dentro del tope de pgvector.
        informe.update(plan="exacto", motivo_plan="top_n_sin_margen_en_el_indice")
        return _vecinos_exactos(conn, literal, top_n, None)

    informe.update(plan="indice", motivo_plan="corpus_sobre_umbral", ef_search=ef_search)
    cercanas = _vecinos_indice(conn, literal, top_n, ef_search)
    if len(cercanas) < top_n and corpus > len(cercanas):
        print(f"[recall] el índice devolvió {len(cercanas)} de {top_n} unidades pedidas con "
              f"~{corpus} en el corpus; se recalcula sin índice")
        informe.update(plan="exacto", indice_corto=len(cercanas))
        return _vecinos_exactos(conn, literal, top_n, None)
    return cercanas


def comparar_recall(conn, vector, top_n, ef_search=None):
    """El chequeo de recall real: índice contra exacto, para un criterio.

    Es la única forma de ver un recall equivocado, porque **no se ve en las
    cuentas**: el índice puede devolver `top_n` de `top_n` y no ser las
    correctas. Devuelve cuántas de las `top_n` exactas faltan en lo que trae
    el índice (con el `ef_search` que usaría producción, o el que se pida),
    cuáles, y en qué puesto del exacto estaban. Solo lee.

    Desde R-UE compara **unidades** (`unidad_id`): es lo que busca el recall.
    """
    literal = _vector(vector)
    ef = ef_search if ef_search is not None else ef_search_para(top_n)
    exactas = _vecinos_exactos(conn, literal, top_n, None)
    del_indice = {f["id"] for f in _vecinos_indice(conn, literal, top_n, ef)}
    faltan = [{"puesto": i + 1, "unidad_id": f["id"], "distancia": float(f["distancia"])}
              for i, f in enumerate(exactas) if f["id"] not in del_indice]
    return {
        "top_n": top_n,
        "ef_search": ef,
        "exactas": len(exactas),
        "del_indice": len(del_indice),
        "faltan": faltan,
        "recall": (1 - len(faltan) / len(exactas)) if exactas else 1.0,
    }


def recuperar(conn, vector, top_n, ids_persona=None, informe=None):
    """Las `top_n` **unidades de evidencia** más cercanas al vector del
    criterio, resueltas a sus respuestas (R-UE.2).

    Devuelve una fila por **respuesta**: todas las de cada unidad
    recuperada (de las personas habilitadas, si hay filtro), con la
    distancia de su unidad. Cada candidato vuelve con lo que el PRD pide para
    poder mostrarlo y auditarlo: `id_persona`, la respuesta, su procedencia
    (estudio y pregunta), la distancia, y su `unidad_id` / `hash_texto`.

    `top_n` cuenta unidades: «→ Xiaomi», elegida por 400 personas, es **una**
    de las `top_n` y vuelve con sus 400 respuestas. Antes ocupaba 400 lugares
    del pool (o ninguno, si el índice no alcanzaba el bloque).

    `ids_persona` restringe la búsqueda a un conjunto de personas: es el lado
    semántico del puente entre stores, y el vehículo del gate de
    consentimiento cuando la estrategia es «demográfico primero». Lista vacía
    = nadie habilitado, y entonces no hay nada que buscar. Con filtro, se
    buscan las unidades que tienen alguna respuesta de esas personas, y de
    cada una vuelven **solo** las respuestas de esas personas: una unidad
    puede estar en el recall aunque alguna de sus personas no tenga
    `uso_semantico` vigente; esa persona simplemente no aparece.

    La búsqueda se hace en dos pasos a propósito. Primero el vecino más
    cercano sobre `unidad_evidencia` sola, que es la tabla con el índice
    HNSW; después las respuestas y su procedencia, con un join sobre las
    unidades que volvieron.

    Cuándo se usa el índice y cuándo no lo decide `_vecinos` (ver ahí).
    `informe` (opcional) vuelve con el plan que se usó y cuántas unidades y
    respuestas trajo.
    """
    informe = informe if informe is not None else {}
    if ids_persona is not None and not ids_persona:
        return []

    literal = _vector(vector)
    ids_individuo = None
    if ids_persona is not None:
        filas = db.todas(
            conn,
            "select id from individuo where id_persona = any(%s::uuid[])",
            ([str(i) for i in ids_persona],),
        )
        ids_individuo = [f["id"] for f in filas]
        if not ids_individuo:
            return []

    cercanas = _vecinos(conn, literal, top_n, ids_individuo, informe)
    informe["unidades"] = len(cercanas)
    if not cercanas:
        informe["respuestas"] = 0
        return []

    distancia_por_unidad = {f["id"]: float(f["distancia"]) for f in cercanas}
    filas = db.todas(
        conn,
        """
        select v.respuesta_id, v.id_persona, v.ref_estudio, v.estudio,
               v.fecha_campo, v.pregunta_codigo, v.pregunta_texto,
               v.pregunta_tipo, v.valor_texto, v.texto_embebido,
               r.pregunta_id, r.hash_texto, r.unidad_id
          from respuesta r
          join v_respuesta_estudio v on v.respuesta_id = r.id
         where r.unidad_id = any(%s::bigint[])
           and (%s::bigint[] is null or r.individuo_id = any(%s::bigint[]))
        """,
        (list(distancia_por_unidad), ids_individuo, ids_individuo),
    )

    candidatos = [
        {
            "respuesta_id": f["respuesta_id"],
            "id_persona": str(f["id_persona"]),
            "ref_estudio": str(f["ref_estudio"]) if f["ref_estudio"] else None,
            "estudio": f["estudio"],
            "fecha_campo": f["fecha_campo"].isoformat() if f["fecha_campo"] else None,
            "pregunta_codigo": f["pregunta_codigo"],
            "pregunta_texto": f["pregunta_texto"],
            "pregunta_tipo": f["pregunta_tipo"],
            "valor_texto": f["valor_texto"],
            "texto_embebido": f["texto_embebido"],
            "distancia": distancia_por_unidad[f["unidad_id"]],
            # R-UE — la unidad de evidencia de esta respuesta.
            "unidad_id": f["unidad_id"],
            "pregunta_id": f["pregunta_id"],
            "hash_texto": f["hash_texto"],
        }
        for f in filas
    ]
    candidatos.sort(key=lambda c: (c["distancia"], c["unidad_id"], c["respuesta_id"]))
    informe["respuestas"] = len(candidatos)
    return candidatos


def _borrar_capturas(conn, consulta_ids, parametros):
    """Las capturas de depuración que llevan esas respuestas. Si la 0008 no
    está aplicada no hay capturas, y eso no puede frenar una baja."""
    from . import verificacion

    if not db.una(conn, "select to_regclass('verificacion_captura') as t")["t"]:
        return 0
    ids = [f["id"] for f in db.todas(conn, consulta_ids, parametros)]
    return verificacion.borrar_capturas_de_respuestas(conn, ids)


def borrar_persona(conn, id_persona):
    """Borra el individuo y, en cascada, todas sus respuestas y embeddings.

    Es el lado semántico de la cascada de retiro de consentimiento. Desde
    R-UE el vector vive en la unidad de evidencia y no en la respuesta: las
    unidades que se quedan sin respuestas las borra el trigger de la
    semantica/0010 en el mismo `delete`, así que si la persona era la única
    que dio un texto, el texto y su vector se van con ella. Se lleva
    también las capturas de depuración de la verificación que incluyeron
    alguna de sus respuestas (R-VER.10): son copias de ese mismo contenido.
    """
    _borrar_capturas(conn, """
        select r.id from respuesta r join individuo i on i.id = r.individuo_id
         where i.id_persona = %s""", (str(id_persona),))
    n = db.ejecutar(conn, "delete from individuo where id_persona = %s", (str(id_persona),))
    # R-CS — los veredictos de unidades que se quedaron sin respuestas.
    borrar_veredictos_huerfanos(conn)
    return {"id_persona": str(id_persona), "individuos_borrados": n}


def borrar_respuestas_de_estudio(conn, id_persona, ref_estudio):
    """Borrado acotado a un estudio (retiro de `uso_semantico` para una ola)."""
    _borrar_capturas(conn, """
        select r.id from respuesta r
          join individuo i on i.id = r.individuo_id
          join pregunta p on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where i.id_persona = %s and c.ref_estudio = %s""",
        (str(id_persona), str(ref_estudio)))
    n = db.ejecutar(
        conn,
        """
        delete from respuesta r
         using individuo i, pregunta p, cuestionario c
         where r.individuo_id = i.id
           and r.pregunta_id = p.id
           and p.cuestionario_id = c.id
           and i.id_persona = %s
           and c.ref_estudio = %s
        """,
        (str(id_persona), str(ref_estudio)),
    )
    borrar_veredictos_huerfanos(conn)
    return {"respuestas_borradas": n}


# ── PEDIDO del celular, parte 3 · Borrar una carga ───────────────────
#
# El lado semántico del borrado de una carga (`cargas_borrado.py`). Mismo
# patrón que la baja: capturas primero, `delete` después, y las unidades
# que quedan sin respuestas las borra el trigger de la `semantica/0010`; las
# compartidas con otro estudio se quedan solas, sin que nadie las elija.

_RESPUESTAS_DEL_ESTUDIO = """
    select r.id from respuesta r
      join pregunta p on p.id = r.pregunta_id
      join cuestionario c on c.id = p.cuestionario_id
     where c.ref_estudio = %s"""


def contar_estudio(conn, ref_estudio):
    """Respuestas y unidades de evidencia que se irían con el estudio. Las
    unidades son las que no tienen ninguna respuesta de otro estudio."""
    fila = db.una(
        conn,
        """
        with del_estudio as materialized (
          select r.unidad_id
            from respuesta r
            join pregunta p on p.id = r.pregunta_id
            join cuestionario c on c.id = p.cuestionario_id
           where c.ref_estudio = %s
        )
        select (select count(*) from del_estudio) as respuestas,
               (select count(*) from (select distinct unidad_id from del_estudio) u
                 where not exists (
                   select 1 from respuesta r2
                     join pregunta p2 on p2.id = r2.pregunta_id
                     join cuestionario c2 on c2.id = p2.cuestionario_id
                    where r2.unidad_id = u.unidad_id and c2.ref_estudio <> %s))
                 as unidades_que_se_borran
        """,
        (str(ref_estudio), str(ref_estudio)))
    return {"respuestas": fila["respuestas"],
            "unidades_que_se_borran": fila["unidades_que_se_borran"]}


def con_respuestas_de_otro_estudio(conn, ids_persona, ref_estudio):
    """De esas personas, las que tienen alguna respuesta de **otro** estudio:
    esas tienen otra vida en el sistema y no se dan de baja."""
    if not ids_persona:
        return set()
    return {
        str(f["id_persona"])
        for f in db.todas(
            conn,
            """
            select distinct i.id_persona
              from individuo i
              join respuesta r on r.individuo_id = i.id
              join pregunta p on p.id = r.pregunta_id
              join cuestionario c on c.id = p.cuestionario_id
             where i.id_persona = any(%s::uuid[]) and c.ref_estudio <> %s
            """,
            ([str(i) for i in ids_persona], str(ref_estudio)))
    }


def borrar_respuestas_de_estudio_de(conn, ids_persona, ref_estudio):
    """Las respuestas de ese estudio de esas personas, de una vez (un lote
    del borrado). Las de otros estudios no se tocan."""
    if not ids_persona:
        return 0
    ids = [str(i) for i in ids_persona]
    _borrar_capturas(conn, """
        select r.id from respuesta r
          join individuo i on i.id = r.individuo_id
          join pregunta p on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where i.id_persona = any(%s::uuid[]) and c.ref_estudio = %s""",
        (ids, str(ref_estudio)))
    n = db.ejecutar(
        conn,
        """
        delete from respuesta r
         using individuo i, pregunta p, cuestionario c
         where r.individuo_id = i.id
           and r.pregunta_id = p.id
           and p.cuestionario_id = c.id
           and i.id_persona = any(%s::uuid[])
           and c.ref_estudio = %s
        """,
        (ids, str(ref_estudio)))
    borrar_veredictos_huerfanos(conn)
    return n


def borrar_estudio(conn, ref_estudio):
    """El cuestionario entero: sus preguntas y **todas** las respuestas que
    queden —también las de quien ya no tiene vínculo con la carga—, en
    cascada. Idempotente: sin cuestionario, no hace nada."""
    _borrar_capturas(conn, _RESPUESTAS_DEL_ESTUDIO, (str(ref_estudio),))
    respuestas = db.una(
        conn, f"select count(*) as n from ({_RESPUESTAS_DEL_ESTUDIO}) r",
        (str(ref_estudio),))["n"]
    cuestionarios = db.ejecutar(
        conn, "delete from cuestionario where ref_estudio = %s", (str(ref_estudio),))
    borrar_veredictos_huerfanos(conn)
    return {"respuestas_borradas": respuestas,
            "cuestionario_borrado": bool(cuestionarios)}


def auditar_columnas(conn):
    """Auditoría en vivo del esquema semántico: columnas con nombre de PII.

    Complementa la auditoría estática del DDL, por si alguien agregó una
    columna a mano en la base (cosa que CLAUDE.md prohíbe, pero se verifica
    igual). Lista vacía = store limpio.
    """
    filas = db.todas(
        conn,
        """
        select table_name, column_name
          from information_schema.columns
         where table_schema = 'public'
         order by table_name, ordinal_position
        """,
    )
    hallazgos = []
    for f in filas:
        columna = f["column_name"].lower()
        tabla = f["table_name"].lower()
        if columna in pii.CAMPOS_PII and not (
            tabla in pii.CONTEXTOS_QUE_PERMITEN_NOMBRE and columna == "nombre"
        ):
            hallazgos.append({"tabla": tabla, "columna": columna})
    return hallazgos


def resumen_de_estudio(conn, ref_estudio):
    """Cuántas respuestas y personas quedaron del lado semántico para un estudio.

    Es la verificación de R1.5 desde la UI: si el número cierra con las
    participaciones de la bóveda, el puente `ref_estudio` funcionó.
    """
    fila = db.una(
        conn,
        """
        select c.id, c.nombre,
               (select count(*) from pregunta p where p.cuestionario_id = c.id)::int
                 as preguntas,
               (select count(*) from respuesta r
                  join pregunta p on p.id = r.pregunta_id
                 where p.cuestionario_id = c.id)::int as respuestas,
               (select count(distinct r.individuo_id) from respuesta r
                  join pregunta p on p.id = r.pregunta_id
                 where p.cuestionario_id = c.id)::int as individuos
          from cuestionario c
         where c.ref_estudio = %s
        """,
        (str(ref_estudio),),
    )
    if not fila:
        raise NoEncontrado(f"El store semántico no tiene el estudio {ref_estudio}.")
    return {
        "ref_estudio": str(ref_estudio),
        "nombre": fila["nombre"],
        "preguntas": fila["preguntas"],
        "respuestas": fila["respuestas"],
        "individuos": fila["individuos"],
    }


# ════════════════════════════════════════════════════════════════════
#  R-CS · cambio 3 y 5 — unidades de evidencia y veredictos persistidos
# ════════════════════════════════════════════════════════════════════
#
# Desde R-UE (D73) una unidad es un **texto embebido distinto**, una fila de
# `unidad_evidencia`, y su clave es `hash_texto`. Antes era
# `(pregunta_id, hash_texto)`; como el texto embebido ya incluye la pregunta,
# la diferencia es solo la misma pregunta con el mismo texto en dos olas,
# que el verificador leería dos veces idéntica. Estas funciones son de la
# ejecución completa.

def _ids_individuo(conn, ids_persona):
    filas = db.todas(
        conn, "select id from individuo where id_persona = any(%s::uuid[])",
        ([str(i) for i in ids_persona],))
    return [f["id"] for f in filas]


def unidades_elegibles(conn, vector, ids_persona, distancia_maxima=None):
    """Todas las unidades con al menos una respuesta de una persona habilitada,
    con su distancia al criterio. Es el universo de una ejecución completa.

    Recorre las unidades de esas personas a fuerza bruta y no con el índice
    HNSW: el índice sirve para «los N más cercanos», y acá se quieren todas.
    Con unidades el universo es de textos distintos (861 con el corpus de
    hoy, no 21.340 respuestas). `pregunta_id` es el de una de sus respuestas
    y es informativo: la unidad es la huella.
    """
    if not ids_persona:
        return []
    individuos = _ids_individuo(conn, ids_persona)
    if not individuos:
        return []
    filas = db.todas(
        conn,
        """
        with habilitadas as materialized (
            select r.unidad_id, min(r.pregunta_id) as pregunta_id, count(*) as respuestas
              from respuesta r
             where r.individuo_id = any(%s::bigint[])
             group by r.unidad_id
        )
        select u.id as unidad_id, u.hash_texto, h.pregunta_id, h.respuestas,
               u.embedding <=> %s::vector as distancia,
               length(u.texto_embebido) as largo
          from habilitadas h
          join unidad_evidencia u on u.id = h.unidad_id
         where (%s::float8 is null or u.embedding <=> %s::vector <= %s::float8)
         order by distancia, u.id
        """,
        (individuos, _vector(vector), distancia_maxima, _vector(vector),
         distancia_maxima))
    return [{"unidad_id": f["unidad_id"], "pregunta_id": f["pregunta_id"],
             "hash_texto": f["hash_texto"], "distancia": float(f["distancia"]),
             "largo": int(f["largo"] or 0), "respuestas": int(f["respuestas"])}
            for f in filas]


def respuestas_de_unidades(conn, huellas, ids_persona, solo_representante=False):
    """Las respuestas de esas unidades que son de personas habilitadas **hoy**.

    `huellas`: `[hash_texto, …]`. Con `solo_representante` devuelve una por
    unidad (la de menor id): es lo que se le muestra al verificador. Sin él,
    todas: es a quién alcanza cada veredicto.
    """
    huellas = list(dict.fromkeys(str(h) for h in huellas))
    if not huellas or not ids_persona:
        return []
    individuos = _ids_individuo(conn, ids_persona)
    if not individuos:
        return []
    distinto = "distinct on (r.hash_texto)" if solo_representante else ""
    filas = db.todas(
        conn,
        f"""
        select {distinto}
               r.id as respuesta_id, r.pregunta_id, r.hash_texto, r.unidad_id,
               v.id_persona, v.ref_estudio, v.estudio, v.fecha_campo,
               v.pregunta_codigo, v.pregunta_texto, v.pregunta_tipo,
               v.valor_texto, v.texto_embebido
          from respuesta r
          join v_respuesta_estudio v on v.respuesta_id = r.id
         where r.hash_texto = any(%s::text[])
           and r.individuo_id = any(%s::bigint[])
         order by r.hash_texto, r.id
        """,
        (huellas, individuos))
    return [
        {
            "respuesta_id": f["respuesta_id"],
            "unidad_id": f["unidad_id"],
            "pregunta_id": f["pregunta_id"],
            "hash_texto": f["hash_texto"],
            "id_persona": str(f["id_persona"]),
            "ref_estudio": str(f["ref_estudio"]) if f["ref_estudio"] else None,
            "estudio": f["estudio"],
            "fecha_campo": f["fecha_campo"].isoformat() if f["fecha_campo"] else None,
            "pregunta_codigo": f["pregunta_codigo"],
            "pregunta_texto": f["pregunta_texto"],
            "pregunta_tipo": f["pregunta_tipo"],
            "valor_texto": f["valor_texto"],
            "texto_embebido": f["texto_embebido"],
        }
        for f in filas
    ]


def guardar_veredictos(conn, ejecucion_id, criterio_orden, veredictos):
    """Upsert por (ejecución, criterio, unidad): reprocesar un lote no
    duplica y deja el último juicio. `pregunta_id` es informativo."""
    for v in veredictos:
        db.ejecutar(
            conn,
            """insert into veredicto_unidad
                      (ejecucion_id, criterio_orden, pregunta_id, hash_texto,
                       veredicto, razon, fallo, aviso_polaridad, relevancia,
                       distancia)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (ejecucion_id, criterio_orden, hash_texto)
               do update set veredicto = excluded.veredicto,
                             pregunta_id = excluded.pregunta_id,
                             razon = excluded.razon,
                             fallo = excluded.fallo,
                             aviso_polaridad = excluded.aviso_polaridad,
                             relevancia = excluded.relevancia,
                             distancia = excluded.distancia,
                             creado_en = now()""",
            (str(ejecucion_id), criterio_orden, v.get("pregunta_id"), v["hash_texto"],
             v["veredicto"], v.get("razon"), v.get("fallo"),
             bool(v.get("aviso_polaridad")), v.get("relevancia"), v.get("distancia")))
    return len(veredictos)


def veredictos_de(conn, ejecucion_id):
    """`{criterio_orden: {hash_texto: veredicto}}` de una ejecución."""
    purgar_veredictos(conn)
    filas = db.todas(
        conn,
        """select criterio_orden, pregunta_id, hash_texto, veredicto, razon,
                  fallo, aviso_polaridad, relevancia, distancia
             from veredicto_unidad where ejecucion_id = %s""",
        (str(ejecucion_id),))
    salida = {}
    for f in filas:
        salida.setdefault(f["criterio_orden"], {})[f["hash_texto"]] = f
    return salida


def purgar_veredictos(conn):
    """Los veredictos vencidos (30 días). Si la 0009 no está, no hay nada."""
    if not db.una(conn, "select to_regclass('veredicto_unidad') as t")["t"]:
        return 0
    return db.ejecutar(conn, "delete from veredicto_unidad where vence_en <= now()")


def borrar_veredictos_huerfanos(conn):
    """El alcance de una baja sobre los veredictos: el de una unidad que se
    quedó sin respuestas —y que por eso ya no existe: la borra el trigger de
    la semantica/0010— habla de un texto que ya no está, y se va.

    Los de unidades que siguen teniendo respuestas de otras personas se
    quedan: no son de nadie en particular, y la razón describe un texto que
    sigue en el store.
    """
    if not db.una(conn, "select to_regclass('veredicto_unidad') as t")["t"]:
        return 0
    return db.ejecutar(
        conn,
        """delete from veredicto_unidad v
            where not exists (
                  select 1 from unidad_evidencia u
                   where u.hash_texto = v.hash_texto)""")
