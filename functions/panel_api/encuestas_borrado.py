"""R-EU.9 — Borrar una encuesta: «estos datos no tienen que estar más» (D81).

El caso típico es un cliente que pide que las respuestas de su estudio se
borren. Se borra **el estudio**, no a la gente:

* **Se eliminan** sus respuestas del store semántico —el cuestionario, sus
  preguntas y respuestas, y las unidades de evidencia que se quedan sin
  respuestas (las borra el trigger de la `semantica/0010`; las compartidas
  con otro estudio se conservan solas)—, sus participaciones, sus cargas
  (con el vínculo persona ↔ carga) y sus temáticas por panelista.
* **Se conservan** las personas y sus atributos demográficos, aunque hayan
  llegado en ese archivo. Borrar una *carga* es otra cosa (`cargas_borrado`,
  R-EU.10): ahí sí se ofrece dar de baja a quien esa carga creó.

── Cómo corre ──

Por pasos y reanudable, **primero el store semántico y después la bóveda**,
como el borrado de una carga: si algo falla en el medio quedan personas con
participación y sin respuestas —se arregla volviendo a pedirlo—, nunca
respuestas de un estudio que ya no existe. Cada paso se commitea, y volver a
pedir el borrado de una encuesta con uno en curso lo termina. No hay
personas que procesar una por una, así que no hace falta la vía diferida.

`encuesta_borrado` (boveda/0032) es la auditoría: quién, cuándo, la vista
previa y los conteos. Sobrevive a la encuesta. Bloqueado con una ingesta en
curso en cualquiera de sus cargas.
"""

import json

import psycopg

from . import db, diferida, encuestas, semantica
from .errores import Conflicto, DatosInvalidos

EN_CURSO = "en_curso"
TERMINADO = "terminado"


def _cargas(conn, encuesta_id):
    return [f["id"] for f in db.todas(
        conn, "select id from carga where encuesta_id = %s order by id", (encuesta_id,))]


def exigir_sin_ingesta(conn, encuesta_id):
    """Una ingesta en curso en cualquier carga del estudio lo bloquea."""
    for carga_id in _cargas(conn, encuesta_id):
        diferida.exigir_sin_trabajo_activo(conn, "carga", carga_id)


def previa(conn_boveda, conn_semantica, encuesta_id):
    """Lo que el borrado va a hacer, sin hacer nada."""
    encuesta = encuestas.obtener(conn_boveda, encuesta_id)
    estudio = semantica.contar_estudio(conn_semantica, encuesta["ref_estudio"])
    bov = db.una(
        conn_boveda,
        """select (select count(*) from participacion where encuesta_id = %s)::int
                                                              as participaciones,
                  (select count(*) from carga where encuesta_id = %s)::int as cargas,
                  (select count(distinct id_persona) from persona_tematica
                    where encuesta_id = %s)::int              as con_tematicas,
                  (select count(distinct pc.id_persona) from persona_carga pc
                     join carga c on c.id = pc.carga_id
                    where c.encuesta_id = %s)::int             as personas""",
        (encuesta_id,) * 4)
    return {
        "encuesta": {"id": encuesta["id"], "nombre": encuesta["nombre"],
                     "tipo": encuesta["tipo"], "estado": encuesta["estado"],
                     "ref_estudio": encuesta["ref_estudio"]},
        "respuestas_a_borrar": estudio["respuestas"],
        "unidades_que_se_borran": estudio["unidades_que_se_borran"],
        "participaciones_a_borrar": bov["participaciones"],
        "cargas_a_borrar": bov["cargas"],
        "personas_con_tematicas_del_estudio": bov["con_tematicas"],
        # Las personas no se tocan: es lo que distingue esto de borrar una
        # carga.
        "personas_que_se_conservan": bov["personas"],
        "irreversible": True,
    }


def _en_curso(conn, ref_estudio):
    return db.una(
        conn, "select * from encuesta_borrado where ref_estudio = %s and estado = %s",
        (str(ref_estudio), EN_CURSO))


def lanzar(conn_boveda, conn_semantica, encuesta_id, confirmacion=None, actor=None,
           solo_revisar=False):
    """`(status, cuerpo)`. Con `solo_revisar`, la vista previa —de la misma
    ruta que borra—. Sin él, exige `confirmacion` igual al nombre de la
    encuesta y borra (o termina un borrado que quedó a medias)."""
    encuesta = encuestas.obtener(conn_boveda, encuesta_id)
    en_curso = _en_curso(conn_boveda, encuesta["ref_estudio"])
    if solo_revisar:
        if not en_curso:
            exigir_sin_ingesta(conn_boveda, encuesta_id)
        salida = previa(conn_boveda, conn_semantica, encuesta_id)
        salida["borrado_en_curso"] = ({"borrado_id": en_curso["id"]}
                                      if en_curso else None)
        return 200, salida

    if (confirmacion or "").strip() != encuesta["nombre"].strip():
        raise DatosInvalidos(
            "Para borrar la encuesta hay que escribir su nombre exacto.",
            {"campo": "confirmacion"})
    if not en_curso:
        exigir_sin_ingesta(conn_boveda, encuesta_id)
        vista = previa(conn_boveda, conn_semantica, encuesta_id)
        try:
            en_curso = db.una(
                conn_boveda,
                """insert into encuesta_borrado
                          (encuesta_id, encuesta_nombre, ref_estudio, vista_previa,
                           actor_uid, actor_email)
                   values (%s, %s, %s, %s::jsonb, %s, %s)
                returning *""",
                (encuesta_id, encuesta["nombre"], encuesta["ref_estudio"],
                 json.dumps(vista, default=str), getattr(actor, "uid", None),
                 getattr(actor, "email", None)))
        except psycopg.errors.UniqueViolation:
            conn_boveda.rollback()
            raise Conflicto("Ya hay un borrado de esta encuesta en curso. Volvé "
                            "a pedirlo para terminarlo.") from None
        conn_boveda.commit()
    return 200, terminar(conn_boveda, conn_semantica, en_curso["id"])


def terminar(conn_boveda, conn_semantica, borrado_id):
    """Los pasos, en orden y cada uno commiteado. Idempotente: un paso que
    ya se hizo no encuentra nada."""
    borrado = db.una(conn_boveda, "select * from encuesta_borrado where id = %s",
                     (borrado_id,))
    if borrado["estado"] == TERMINADO:
        return _final(borrado)

    # 1 · El store semántico, primero.
    semantico = semantica.borrar_estudio(conn_semantica, borrado["ref_estudio"])
    conn_semantica.commit()
    respuestas = max(semantico["respuestas_borradas"],
                     (borrado["vista_previa"] or {}).get("respuestas_a_borrar", 0))

    # 2 · La bóveda: participaciones, cargas (y su vínculo con las personas),
    # y la encuesta, que se lleva sus temáticas y clientes en cascada. Las
    # personas y sus atributos quedan.
    participaciones = cargas_borradas = 0
    if borrado["encuesta_id"] is not None:
        participaciones = db.ejecutar(
            conn_boveda, "delete from participacion where encuesta_id = %s",
            (borrado["encuesta_id"],))
        cargas_borradas = db.ejecutar(
            conn_boveda, "delete from carga where encuesta_id = %s",
            (borrado["encuesta_id"],))
        db.ejecutar(conn_boveda, "delete from encuesta where id = %s",
                    (borrado["encuesta_id"],))
    previa_ = borrado["vista_previa"] or {}
    final = db.una(
        conn_boveda,
        """update encuesta_borrado
              set estado = %s, terminado_en = now(), respuestas_borradas = %s,
                  participaciones_borradas = %s, cargas_borradas = %s
            where id = %s
        returning *""",
        (TERMINADO, respuestas,
         max(participaciones, previa_.get("participaciones_a_borrar", 0)),
         max(cargas_borradas, previa_.get("cargas_a_borrar", 0)), borrado_id))
    conn_boveda.commit()
    print(f"[borrado] encuesta={borrado['encuesta_id']} borrado={borrado_id} "
          f"respuestas={final['respuestas_borradas']} "
          f"participaciones={final['participaciones_borradas']} "
          f"cargas={final['cargas_borradas']}", flush=True)
    return _final(final)


def _final(borrado):
    return {"borrado_id": borrado["id"], "estado": borrado["estado"],
            "encuesta": borrado["encuesta_nombre"],
            "respuestas_borradas": borrado["respuestas_borradas"],
            "participaciones_borradas": borrado["participaciones_borradas"],
            "cargas_borradas": borrado["cargas_borradas"]}
