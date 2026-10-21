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

# Espejo de `motivo_reidentificacion` (boveda/0020). El catálogo vive en la
# base para poder consultarlo desde ahí, pero **sin clave foránea**: ver el
# encabezado de esa migración. Que no diverjan lo cuida
# `test_fase7_auditoria.py`.
MOTIVOS_REIDENTIFICACION = (
    "ficha",           # se abrió la ficha de un panelista
    "consulta",        # se resolvió un resultado de consulta a datos de contacto
    "convocatoria",    # se armó una convocatoria con nombres
    "exportacion",     # se bajó un archivo con PII
    "cumplimiento",    # atención de un pedido de baja o de acceso
    # R7.6 — ver qué respondió una persona identificada. No revela contacto,
    # pero une identidad y contenido, que es lo que los dos stores mantienen
    # separado. Se registra por eso, con etiqueta propia.
    "respuestas_panelista",
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


def eventos_de_reidentificacion(conn, actor=None, desde=None, hasta=None,
                                panelista=None, limite=50, desplazamiento=0):
    """R-AUD.1 — el registro de reidentificaciones, **por evento**: quién,
    cuándo, con qué motivo y a cuántas personas alcanzó, con sus ids.

    Se guarda una fila por persona (así se puede preguntar «¿quién vio los
    datos de esta?» sin abrir un json), y un evento son las filas que dejó
    una misma llamada: mismo actor, motivo, contexto y momento. Por eso se
    agrupa al leer, y no se agrega un id de evento a la tabla.

    `actor` filtra por uid o por correo (parcial); `desde`/`hasta`, por
    fecha (inclusive); `panelista`, por `id_persona`, documento o correo de
    alguien que **todavía** está en la bóveda: deja los eventos que lo
    alcanzaron, completos. Es la pregunta de un pedido de acceso."""
    condiciones, parametros = [], []
    if actor:
        condiciones.append("(r.actor_uid = %s or r.actor_email ilike %s)")
        parametros += [actor.strip(), f"%{actor.strip()}%"]
    if desde:
        condiciones.append("r.creado_en >= %s::date")
        parametros.append(desde)
    if hasta:
        condiciones.append("r.creado_en < %s::date + 1")
        parametros.append(hasta)
    donde = " and ".join(condiciones) or "true"
    if panelista:
        crudo = panelista.strip()
        if len(crudo) == 36 and crudo.count("-") == 4:
            alcanzados = [crudo]
        else:
            alcanzados = [str(f["id_persona"]) for f in db.todas(
                conn,
                """select id_persona from persona
                    where documento = %s or lower(email) = lower(%s)""",
                (crudo, crudo))]
        filtro_evento = "having bool_or(r.id_persona = any(%s::uuid[]))"
        parametros_alcanzados = [alcanzados]
    else:
        filtro_evento, parametros_alcanzados = "", []
    agrupado = f"""
        select min(r.id) as evento_id, r.actor_uid, r.actor_email, r.motivo,
               min(r.motivo_etiqueta) as motivo_etiqueta,
               bool_or(r.expone_pii) as expone_pii, r.contexto, r.creado_en,
               count(*)::int as personas,
               array_agg(r.id_persona::text order by r.id) as ids_persona
          from v_reidentificacion r
         where {donde}
         group by r.actor_uid, r.actor_email, r.motivo, r.contexto, r.creado_en
         {filtro_evento}"""
    todos = tuple(parametros + parametros_alcanzados)
    total = db.una(conn, f"select count(*)::int as n from ({agrupado}) e", todos)["n"]
    filas = db.todas(
        conn,
        f"{agrupado} order by r.creado_en desc, min(r.id) desc limit %s offset %s",
        todos + (int(limite), int(desplazamiento)))
    return {
        "total": total,
        "items": [{
            "evento_id": f["evento_id"],
            "actor_uid": f["actor_uid"], "actor_email": f["actor_email"],
            "motivo": f["motivo"], "motivo_etiqueta": f["motivo_etiqueta"] or f["motivo"],
            "expone_pii": f["expone_pii"],
            "contexto": f["contexto"], "creado_en": f["creado_en"].isoformat(),
            "personas": f["personas"], "ids_persona": f["ids_persona"],
        } for f in filas],
    }


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
        # La etiqueta viene de `accion_usuario` y no de un diccionario en el
        # frontend: una acción nueva se ve bien el día que se agrega, sin
        # tocar el JavaScript. La vista es la que la resuelve, así que en las
        # escrituras —que devuelven la fila cruda— no viene.
        "accion_etiqueta": fila.get("accion_etiqueta"),
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
        select id, accion, accion_etiqueta, uid_objetivo, email_objetivo,
               rol_anterior, rol_nuevo, actor_uid, actor_email, detalle, creado_en
          from v_usuario_auditoria
         where (%s::text is null or uid_objetivo = %s::text)
         order by creado_en desc, id desc
         limit %s
        """,
        (uid_objetivo, uid_objetivo, limite),
    )
    return [_serializar_usuario(f) for f in filas]
