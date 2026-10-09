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


def hashes_de_estudio(conn, ref_estudio):
    """`{(individuo_id, pregunta_id): hash_texto}` de lo ya ingestado.

    Se consulta antes de embeber: lo que venga con el mismo texto no se
    manda al proveedor.
    """
    filas = db.todas(
        conn,
        """
        select r.individuo_id, r.pregunta_id, r.hash_texto
          from respuesta r
          join pregunta p     on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where c.ref_estudio = %s and r.hash_texto is not null
        """,
        (str(ref_estudio),),
    )
    return {(f["individuo_id"], f["pregunta_id"]): f["hash_texto"] for f in filas}


def upsert_respuestas(conn, respuestas):
    """`respuestas`: [{individuo_id, pregunta_id, valor_texto, texto_embebido,
    embedding}]. Idempotente por (individuo, pregunta): re-ingestar no duplica.

    `embedding` puede venir en None cuando la ingesta salteó el embedding
    porque el texto no cambió: en ese caso se actualiza todo menos el vector,
    que sigue siendo el correcto.
    """
    pii.validar_sin_pii(
        [{k: v for k, v in r.items() if k != "embedding"} for r in respuestas],
        contexto="respuesta",
    )
    escritas = 0
    for r in respuestas:
        huella = r.get("hash_texto") or hash_texto(r["texto_embebido"])
        if r.get("embedding") is None:
            # Sin vector nuevo: solo puede ser un UPDATE de una fila que ya
            # existe (si no existiera, no habría con qué comparar el hash).
            escritas += db.ejecutar(
                conn,
                """
                update respuesta
                   set valor_texto = %s, texto_embebido = %s, hash_texto = %s
                 where individuo_id = %s and pregunta_id = %s
                """,
                (
                    r.get("valor_texto"), r["texto_embebido"], huella,
                    r["individuo_id"], r["pregunta_id"],
                ),
            )
            continue
        escritas += db.ejecutar(
            conn,
            """
            insert into respuesta
                   (individuo_id, pregunta_id, valor_texto, texto_embebido,
                    embedding, hash_texto)
                 values (%s, %s, %s, %s, %s::vector, %s)
            on conflict (individuo_id, pregunta_id) do update
                    set valor_texto = excluded.valor_texto,
                        texto_embebido = excluded.texto_embebido,
                        embedding = excluded.embedding,
                        hash_texto = excluded.hash_texto
            """,
            (
                r["individuo_id"],
                r["pregunta_id"],
                r.get("valor_texto"),
                r["texto_embebido"],
                _vector(r["embedding"]),
                huella,
            ),
        )
    return escritas


# ════════════════════════════════════════════════════════════════════
#  R2.7 — Recuperación semántica (recall por vecino aproximado)
# ════════════════════════════════════════════════════════════════════

def recuperar(conn, vector, top_n, ids_persona=None):
    """Las `top_n` respuestas más cercanas al vector del criterio.

    Cada candidato vuelve con lo que el PRD pide para poder mostrarlo y
    auditarlo: `id_persona`, la respuesta, su procedencia (estudio y
    pregunta) y la distancia.

    `ids_persona` restringe la búsqueda a un conjunto de personas: es el lado
    semántico del puente entre stores, y el vehículo del gate de
    consentimiento cuando la estrategia es «demográfico primero». Lista vacía
    = nadie habilitado, y entonces no hay nada que buscar.

    La búsqueda se hace en dos pasos a propósito. Primero el vecino más
    cercano sobre `respuesta` sola, que es la tabla con el índice HNSW;
    después la procedencia, con un join sobre las pocas filas que volvieron.
    Poner el join arriba del ORDER BY del operador de distancia le complica
    al planificador usar el índice, y el corpus es la parte grande.
    """
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

    cercanas = db.todas(
        conn,
        """
        select r.id, r.embedding <=> %s::vector as distancia
          from respuesta r
         where (%s::bigint[] is null or r.individuo_id = any(%s::bigint[]))
         order by r.embedding <=> %s::vector
         limit %s
        """,
        (literal, ids_individuo, ids_individuo, literal, top_n),
    )
    if not cercanas:
        return []

    distancia_por_id = {f["id"]: float(f["distancia"]) for f in cercanas}
    filas = db.todas(
        conn,
        """
        select v.respuesta_id, v.id_persona, v.ref_estudio, v.estudio,
               v.fecha_campo, v.pregunta_codigo, v.pregunta_texto,
               v.pregunta_tipo, v.valor_texto, v.texto_embebido,
               r.pregunta_id,
               coalesce(r.hash_texto,
                        encode(sha256(convert_to(r.texto_embebido, 'UTF8')), 'hex'))
                 as hash_texto
          from v_respuesta_estudio v
          join respuesta r on r.id = v.respuesta_id
         where v.respuesta_id = any(%s::bigint[])
        """,
        (list(distancia_por_id),),
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
            "distancia": distancia_por_id[f["respuesta_id"]],
            # R-CS · cambio 3 — la unidad de evidencia de esta respuesta.
            "pregunta_id": f["pregunta_id"],
            "hash_texto": f["hash_texto"],
        }
        for f in filas
    ]
    candidatos.sort(key=lambda c: (c["distancia"], c["respuesta_id"]))
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

    Es el lado semántico de la cascada de retiro de consentimiento. Se lleva
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
# Una unidad es un texto distinto dentro de una pregunta:
# `(pregunta_id, hash_texto)`. Estas funciones son de la ejecución completa,
# que exige la semantica/0009 (sin `veredicto_unidad` no hay dónde guardar
# nada), y la 0009 completa `hash_texto` en todas las filas: por eso agrupan
# por la columna, que es la que tiene índice. La exploratoria, que tiene que
# funcionar también sin la 0009, calcula la huella con `coalesce` en
# `recuperar`.

def _ids_individuo(conn, ids_persona):
    filas = db.todas(
        conn, "select id from individuo where id_persona = any(%s::uuid[])",
        ([str(i) for i in ids_persona],))
    return [f["id"] for f in filas]


def unidades_elegibles(conn, vector, ids_persona, distancia_maxima=None):
    """Todas las unidades con al menos una respuesta de una persona habilitada,
    con su mejor distancia al criterio. Es el universo de una ejecución
    completa.

    Recorre el corpus de esas personas a fuerza bruta y no con el índice
    HNSW: el índice sirve para «los N más cercanos», y acá se quieren todos.
    Es el cambio de patrón de acceso que el addendum (A2) pide medir en la
    prueba de carga antes de producción.
    """
    if not ids_persona:
        return []
    individuos = _ids_individuo(conn, ids_persona)
    if not individuos:
        return []
    filas = db.todas(
        conn,
        f"""
        select r.pregunta_id, r.hash_texto as hash_texto,
               min(r.embedding <=> %s::vector) as distancia,
               max(length(r.texto_embebido)) as largo,
               count(*) as respuestas
          from respuesta r
         where r.individuo_id = any(%s::bigint[])
         group by 1, 2
        having (%s::float8 is null or min(r.embedding <=> %s::vector) <= %s::float8)
         order by 3, 1, 2
        """,
        (_vector(vector), individuos, distancia_maxima, _vector(vector),
         distancia_maxima))
    return [{"pregunta_id": f["pregunta_id"], "hash_texto": f["hash_texto"],
             "distancia": float(f["distancia"]), "largo": int(f["largo"] or 0),
             "respuestas": int(f["respuestas"])} for f in filas]


def _claves(unidades):
    return ([int(u[0]) for u in unidades], [str(u[1]) for u in unidades])


def respuestas_de_unidades(conn, unidades, ids_persona, solo_representante=False):
    """Las respuestas de esas unidades que son de personas habilitadas **hoy**.

    `unidades`: `[(pregunta_id, hash_texto), …]`. Con `solo_representante`
    devuelve una por unidad (la de menor id): es lo que se le muestra al
    verificador. Sin él, todas: es a quién alcanza cada veredicto.
    """
    if not unidades or not ids_persona:
        return []
    individuos = _ids_individuo(conn, ids_persona)
    if not individuos:
        return []
    preguntas, huellas = _claves(unidades)
    distinto = "distinct on (r.pregunta_id, hash_texto)" if solo_representante else ""
    filas = db.todas(
        conn,
        f"""
        with claves as (
          select * from unnest(%s::bigint[], %s::text[]) as k(pregunta_id, hash_texto)
        )
        select {distinto}
               r.id as respuesta_id, r.pregunta_id, r.hash_texto as hash_texto,
               v.id_persona, v.ref_estudio, v.estudio, v.fecha_campo,
               v.pregunta_codigo, v.pregunta_texto, v.pregunta_tipo,
               v.valor_texto, v.texto_embebido
          from respuesta r
          join claves k on k.pregunta_id = r.pregunta_id
                       and k.hash_texto = r.hash_texto
          join v_respuesta_estudio v on v.respuesta_id = r.id
         where r.individuo_id = any(%s::bigint[])
         order by r.pregunta_id, hash_texto, r.id
        """,
        (preguntas, huellas, individuos))
    return [
        {
            "respuesta_id": f["respuesta_id"],
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
    duplica y deja el último juicio."""
    for v in veredictos:
        db.ejecutar(
            conn,
            """insert into veredicto_unidad
                      (ejecucion_id, criterio_orden, pregunta_id, hash_texto,
                       veredicto, razon, fallo, aviso_polaridad, relevancia,
                       distancia)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (ejecucion_id, criterio_orden, pregunta_id, hash_texto)
               do update set veredicto = excluded.veredicto,
                             razon = excluded.razon,
                             fallo = excluded.fallo,
                             aviso_polaridad = excluded.aviso_polaridad,
                             relevancia = excluded.relevancia,
                             distancia = excluded.distancia,
                             creado_en = now()""",
            (str(ejecucion_id), criterio_orden, v["pregunta_id"], v["hash_texto"],
             v["veredicto"], v.get("razon"), v.get("fallo"),
             bool(v.get("aviso_polaridad")), v.get("relevancia"), v.get("distancia")))
    return len(veredictos)


def veredictos_de(conn, ejecucion_id):
    """`{criterio_orden: {(pregunta_id, hash): veredicto}}` de una ejecución."""
    purgar_veredictos(conn)
    filas = db.todas(
        conn,
        """select criterio_orden, pregunta_id, hash_texto, veredicto, razon,
                  fallo, aviso_polaridad, relevancia, distancia
             from veredicto_unidad where ejecucion_id = %s""",
        (str(ejecucion_id),))
    salida = {}
    for f in filas:
        salida.setdefault(f["criterio_orden"], {})[(f["pregunta_id"], f["hash_texto"])] = f
    return salida


def purgar_veredictos(conn):
    """Los veredictos vencidos (30 días). Si la 0009 no está, no hay nada."""
    if not db.una(conn, "select to_regclass('veredicto_unidad') as t")["t"]:
        return 0
    return db.ejecutar(conn, "delete from veredicto_unidad where vence_en <= now()")


def borrar_veredictos_huerfanos(conn):
    """El alcance de una baja sobre los veredictos: el de una unidad que se
    quedó sin respuestas habla de un texto que ya no está, y se va.

    Los de unidades que siguen teniendo respuestas de otras personas se
    quedan: no son de nadie en particular, y la razón describe un texto que
    sigue en el store.
    """
    if not db.una(conn, "select to_regclass('veredicto_unidad') as t")["t"]:
        return 0
    return db.ejecutar(
        conn,
        f"""delete from veredicto_unidad v
             where not exists (
                   select 1 from respuesta r
                    where r.pregunta_id = v.pregunta_id
                      and r.hash_texto = v.hash_texto)""")
