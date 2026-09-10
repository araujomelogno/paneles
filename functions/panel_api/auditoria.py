"""Rastros que hay que poder mostrar después: reidentificación y usuarios.

Dos registros distintos, los dos en la bóveda, los dos con autor y fecha.

**Reidentificación** (P1). El puente entre stores existe justamente para
poder traducir un `id_persona` a una persona: sin eso, un ranking de tokens
opacos no sirve para convocar a nadie. Pero es la operación que deshace la
seudonimización, así que cada vez que se hace queda anotada: quién, a quién,
cuándo y con qué motivo. Sin este registro el diseño de dos stores se
sostiene solo en la buena voluntad.

Lo que se registra es la traducción **deliberada**: abrir la ficha de un
panelista, resolver un resultado de consulta a datos de contacto, exportar
una convocatoria. No se registra listar personas que ya se venían mostrando
en pantalla, ni el propio resultado de una consulta (que es de `id_persona`).

**Gestión de usuarios** (R2.12). Dar de alta a alguien en la app, cambiarle
el rol o desactivarlo es escalada de privilegios: alcanza para que otra
persona vea toda la bóveda. Cada una de esas acciones queda con autor y
fecha, y el registro es de solo agregar: no hay forma de editarlo desde la
app.
"""

from . import db

MOTIVOS_REIDENTIFICACION = (
    "ficha",           # se abrió la ficha de un panelista
    "consulta",        # se resolvió un resultado de consulta a datos de contacto
    "convocatoria",    # se armó una convocatoria con nombres
    "exportacion",     # se bajó un archivo con PII
    "cumplimiento",    # atención de un pedido de baja o de acceso
)

ACCIONES_USUARIO = (
    "alta", "cambio_rol", "desactivacion", "reactivacion", "actualizacion",
)


# ── Reidentificación ─────────────────────────────────────────────────

def registrar_reidentificacion(conn, ids_persona, actor=None, motivo="consulta",
                               contexto=None):
    """Anota una o varias traducciones `id_persona` → PII.

    No falla nunca por el contenido: si el motivo no está en la lista se
    guarda igual, porque perder el rastro es peor que guardarlo con una
    etiqueta rara. Lo que sí se ignora es una lista vacía.
    """
    import json

    if isinstance(ids_persona, (str, bytes)) or not hasattr(ids_persona, "__iter__"):
        ids_persona = [ids_persona]
    ids = [str(i) for i in dict.fromkeys(i for i in ids_persona if i)]
    if not ids:
        return {"registradas": 0}

    uid = getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None)
    email = getattr(actor, "email", None)
    carga = json.dumps(contexto or {}, ensure_ascii=False, default=str)

    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into reidentificacion
                   (id_persona, actor_uid, actor_email, motivo, contexto)
                 values (%s, %s, %s, %s, %s::jsonb)
            """,
            [(i, uid or "desconocido", email, motivo, carga) for i in ids],
        )
    return {"registradas": len(ids), "motivo": motivo}


def listar_reidentificaciones(conn, id_persona=None, actor_uid=None, limite=200):
    filas = db.todas(
        conn,
        """
        select id, id_persona, actor_uid, actor_email, motivo, contexto, creado_en
          from reidentificacion
         where (%s::uuid is null or id_persona = %s::uuid)
           and (%s::text is null or actor_uid = %s::text)
         order by creado_en desc, id desc
         limit %s
        """,
        (id_persona, id_persona, actor_uid, actor_uid, limite),
    )
    return [
        {
            "id": f["id"],
            "id_persona": str(f["id_persona"]),
            "actor_uid": f["actor_uid"],
            "actor_email": f["actor_email"],
            "motivo": f["motivo"],
            "contexto": f["contexto"],
            "creado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]


# ── Gestión de usuarios de la app ────────────────────────────────────

def registrar_usuario(conn, accion, uid_objetivo, actor=None, email_objetivo=None,
                      rol_anterior=None, rol_nuevo=None, detalle=None):
    """Anota una acción de gestión de usuarios. Devuelve la fila guardada."""
    import json

    uid = getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None)
    fila = db.una(
        conn,
        """
        insert into usuario_auditoria
               (accion, uid_objetivo, email_objetivo, rol_anterior, rol_nuevo,
                actor_uid, actor_email, detalle)
             values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
          returning id, accion, uid_objetivo, email_objetivo, rol_anterior,
                    rol_nuevo, actor_uid, actor_email, detalle, creado_en
        """,
        (
            accion, uid_objetivo, email_objetivo, rol_anterior, rol_nuevo,
            uid or "desconocido", getattr(actor, "email", None),
            json.dumps(detalle or {}, ensure_ascii=False, default=str),
        ),
    )
    return _serializar_usuario(fila)


def _serializar_usuario(fila):
    return {
        "id": fila["id"],
        "accion": fila["accion"],
        "uid_objetivo": fila["uid_objetivo"],
        "email_objetivo": fila["email_objetivo"],
        "rol_anterior": fila["rol_anterior"],
        "rol_nuevo": fila["rol_nuevo"],
        "actor_uid": fila["actor_uid"],
        "actor_email": fila["actor_email"],
        "detalle": fila["detalle"],
        "creado_en": fila["creado_en"].isoformat(),
    }


def listar_usuario(conn, uid_objetivo=None, limite=200):
    filas = db.todas(
        conn,
        """
        select id, accion, uid_objetivo, email_objetivo, rol_anterior, rol_nuevo,
               actor_uid, actor_email, detalle, creado_en
          from usuario_auditoria
         where (%s::text is null or uid_objetivo = %s::text)
         order by creado_en desc, id desc
         limit %s
        """,
        (uid_objetivo, uid_objetivo, limite),
    )
    return [_serializar_usuario(f) for f in filas]
