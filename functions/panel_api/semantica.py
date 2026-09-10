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


def asegurar_cuestionario(conn, ref_estudio, nombre, fecha_campo=None, metadata=None):
    """Crea (o recupera) el cuestionario del estudio. Idempotente por
    `ref_estudio`, que es la misma uuid que `encuesta.ref_estudio` del
    store de bóveda: ese es el puente entre los dos stores."""
    metadata = metadata or {}
    pii.validar_sin_pii({"nombre": nombre, "metadata": metadata}, contexto="cuestionario")

    fila = db.una(
        conn,
        """
        insert into cuestionario (nombre, fecha_campo, ref_estudio, metadata)
             values (%s, %s, %s, %s::jsonb)
        on conflict (ref_estudio) do update
                set nombre = excluded.nombre,
                    fecha_campo = coalesce(excluded.fecha_campo, cuestionario.fecha_campo)
          returning id
        """,
        (nombre, fecha_campo, str(ref_estudio), json.dumps(metadata)),
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


def upsert_preguntas(conn, cuestionario_id, preguntas):
    """`preguntas`: [{codigo, texto, tipo, opciones, orden}]. Idempotente por
    (cuestionario, codigo)."""
    pii.validar_sin_pii(preguntas, contexto="pregunta")
    codigos = []
    for p in preguntas:
        db.ejecutar(
            conn,
            """
            insert into pregunta (cuestionario_id, codigo, texto, tipo, opciones, orden)
                 values (%s, %s, %s, %s, %s::jsonb, %s)
            on conflict (cuestionario_id, codigo) do update
                    set texto = excluded.texto,
                        tipo = excluded.tipo,
                        opciones = excluded.opciones,
                        orden = excluded.orden
            """,
            (
                cuestionario_id,
                p["codigo"],
                p["texto"],
                p.get("tipo"),
                json.dumps(p.get("opciones")) if p.get("opciones") else None,
                p.get("orden"),
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
        select respuesta_id, id_persona, ref_estudio, estudio, fecha_campo,
               pregunta_codigo, pregunta_texto, pregunta_tipo,
               valor_texto, texto_embebido
          from v_respuesta_estudio
         where respuesta_id = any(%s::bigint[])
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
        }
        for f in filas
    ]
    candidatos.sort(key=lambda c: (c["distancia"], c["respuesta_id"]))
    return candidatos


def borrar_persona(conn, id_persona):
    """Borra el individuo y, en cascada, todas sus respuestas y embeddings.

    Es el lado semántico de la cascada de retiro de consentimiento.
    """
    n = db.ejecutar(conn, "delete from individuo where id_persona = %s", (str(id_persona),))
    return {"id_persona": str(id_persona), "individuos_borrados": n}


def borrar_respuestas_de_estudio(conn, id_persona, ref_estudio):
    """Borrado acotado a un estudio (retiro de `uso_semantico` para una ola)."""
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
