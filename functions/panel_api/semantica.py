"""Cliente del store semántico (Postgres + pgvector).

Es el único módulo que escribe del otro lado. Toda escritura pasa primero
por el guardrail de PII (R1.6): si un payload trae una clave de PII, la
operación aborta antes de tocar la base.

Lo único que viaja al store semántico: `id_persona`, `ref_estudio`, metadatos del
cuestionario y de las preguntas, el texto de respuesta ya despersonalizado
y su embedding.
"""

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


def upsert_respuestas(conn, respuestas):
    """`respuestas`: [{individuo_id, pregunta_id, valor_texto, texto_embebido,
    embedding}]. Idempotente por (individuo, pregunta): re-ingestar no duplica."""
    pii.validar_sin_pii(
        [{k: v for k, v in r.items() if k != "embedding"} for r in respuestas],
        contexto="respuesta",
    )
    escritas = 0
    for r in respuestas:
        escritas += db.ejecutar(
            conn,
            """
            insert into respuesta
                   (individuo_id, pregunta_id, valor_texto, texto_embebido, embedding)
                 values (%s, %s, %s, %s, %s::vector)
            on conflict (individuo_id, pregunta_id) do update
                    set valor_texto = excluded.valor_texto,
                        texto_embebido = excluded.texto_embebido,
                        embedding = excluded.embedding
            """,
            (
                r["individuo_id"],
                r["pregunta_id"],
                r.get("valor_texto"),
                r["texto_embebido"],
                _vector(r["embedding"]),
            ),
        )
    return escritas


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
