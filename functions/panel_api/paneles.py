"""R1.4 — Paneles y membresías N:M.

Una persona puede pertenecer a varios paneles sin duplicarse: la membresía
es la relación, la persona es única. `unique (panel_id, id_persona)` hace
que agregar a alguien dos veces sea idempotente en vez de un error.
"""

from . import db
from .errores import NoEncontrado


def crear(conn, nombre, descripcion=None):
    nombre = (nombre or "").strip()
    if not nombre:
        raise NoEncontrado("El panel necesita un nombre.")
    fila = db.una(
        conn,
        "insert into panel (nombre, descripcion) values (%s, %s) "
        "returning id, nombre, descripcion, estado, creado_en",
        (nombre, (descripcion or "").strip() or None),
    )
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "descripcion": fila["descripcion"],
        "estado": fila["estado"],
        "creado_en": fila["creado_en"].isoformat(),
        "miembros": 0,
    }


def listar(conn):
    filas = db.todas(
        conn,
        """
        select p.id, p.nombre, p.descripcion, p.estado, p.creado_en,
               (select count(*) from membresia m
                 where m.panel_id = p.id and m.estado = 'activo')::int as miembros,
               (select count(*) from encuesta e where e.panel_id = p.id)::int as encuestas
          from panel p
         order by p.estado, p.nombre
        """,
    )
    return [
        {
            "id": f["id"],
            "nombre": f["nombre"],
            "descripcion": f["descripcion"],
            "estado": f["estado"],
            "creado_en": f["creado_en"].isoformat(),
            "miembros": f["miembros"],
            "encuestas": f["encuestas"],
        }
        for f in filas
    ]


def obtener(conn, panel_id):
    fila = db.una(
        conn,
        "select id, nombre, descripcion, estado, creado_en from panel where id = %s",
        (panel_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe el panel {panel_id}.")
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "descripcion": fila["descripcion"],
        "estado": fila["estado"],
        "creado_en": fila["creado_en"].isoformat(),
    }


def archivar(conn, panel_id, estado="archivado"):
    n = db.ejecutar(conn, "update panel set estado = %s where id = %s", (estado, panel_id))
    if not n:
        raise NoEncontrado(f"No existe el panel {panel_id}.")
    return obtener(conn, panel_id)


def agregar_miembro(conn, panel_id, id_persona):
    """Idempotente: si ya era miembro, lo reactiva en lugar de duplicarlo."""
    obtener(conn, panel_id)  # 404 temprano si el panel no existe
    fila = db.una(
        conn,
        """
        insert into membresia (panel_id, id_persona)
             values (%s, %s)
        on conflict (panel_id, id_persona) do update
                set estado = 'activo', fecha_baja = null
          returning id, panel_id, id_persona, estado, fecha_alta
        """,
        (panel_id, id_persona),
    )
    return {
        "id": fila["id"],
        "panel_id": fila["panel_id"],
        "id_persona": str(fila["id_persona"]),
        "estado": fila["estado"],
        "fecha_alta": fila["fecha_alta"].isoformat(),
    }


def dar_de_baja_miembro(conn, panel_id, id_persona):
    """Marca la membresía en `baja`. No borra: el historial de participación
    de esa persona en ese panel sigue siendo cierto."""
    n = db.ejecutar(
        conn,
        """
        update membresia
           set estado = 'baja', fecha_baja = now()
         where panel_id = %s and id_persona = %s and estado = 'activo'
        """,
        (panel_id, id_persona),
    )
    if not n:
        raise NoEncontrado(
            f"La persona {id_persona} no tiene membresía activa en el panel {panel_id}."
        )
    return {"panel_id": panel_id, "id_persona": str(id_persona), "estado": "baja"}


def listar_miembros(conn, panel_id, estado="activo", limite=200, desplazamiento=0):
    obtener(conn, panel_id)
    filas = db.todas(
        conn,
        """
        select m.id_persona, m.estado, m.fecha_alta,
               p.nombre, p.email, p.sexo, p.localidad, d.tramo_etario,
               exists (select 1 from consentimiento c
                        where c.id_persona = p.id_persona
                          and c.finalidad = 'contacto_participacion'
                          and c.estado = 'vigente') as consiente_contacto,
               exists (select 1 from consentimiento c
                        where c.id_persona = p.id_persona
                          and c.finalidad = 'uso_semantico'
                          and c.estado = 'vigente') as consiente_semantico
          from membresia m
          join persona p on p.id_persona = m.id_persona
          left join v_demografia d on d.id_persona = p.id_persona
         where m.panel_id = %s and (%s::text is null or m.estado = %s::text)
         order by p.nombre nulls last
         limit %s offset %s
        """,
        (panel_id, estado, estado, limite, desplazamiento),
    )
    return [
        {
            "id_persona": str(f["id_persona"]),
            "nombre": f["nombre"],
            "email": f["email"],
            "sexo": f["sexo"],
            "localidad": f["localidad"],
            "tramo_etario": f["tramo_etario"],
            "estado": f["estado"],
            "fecha_alta": f["fecha_alta"].isoformat(),
            "consiente_contacto": f["consiente_contacto"],
            "consiente_semantico": f["consiente_semantico"],
        }
        for f in filas
    ]


# ════════════════════════════════════════════════════════════════════
#  R3.11 — un panel a partir del resultado de una consulta
# ════════════════════════════════════════════════════════════════════

def desde_consulta(conn, nombre, resultado, definicion=None, descripcion=None,
                   actor=None, consulta_id=None):
    """Crea un panel con los individuos que salieron de una consulta.

    Materializar un ranking en un panel es, en los hechos, fijar una lista de
    personas: por eso la operación deja el mismo tipo de rastro que una
    reidentificación cuando el resultado venía de una consulta semántica
    (R3.11). No es la misma acción —acá nadie ve un nombre— pero sí es un
    momento en que una consulta deja de ser una pregunta y pasa a ser un
    grupo de gente sobre el que se va a trabajar.

    El panel es **una foto**. Se guarda la definición que lo originó para
    poder rastrear de dónde salió su composición, no para recalcularla: un
    panel que se actualizara solo con la consulta es un no-goal explícito de
    la fase.

    Idempotente en las membresías: quien ya era miembro no se duplica ni se
    reactiva silenciosamente si estaba de baja.
    """
    import json

    from . import auditoria, db
    from .errores import DatosInvalidos

    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosInvalidos("El panel necesita un nombre.")

    ids = [
        str(item["id_persona"])
        for item in (resultado or {}).get("items", []) or []
        if item.get("id_persona")
    ]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise DatosInvalidos(
            "El resultado no tiene ningún individuo, así que no hay panel que "
            "crear."
        )

    fila = db.una(
        conn,
        """
        insert into panel (nombre, descripcion, origen, origen_definicion,
                           origen_consulta_id, creado_por)
        values (%s, %s, 'consulta', %s::jsonb, %s, %s)
        returning id, nombre, descripcion, estado, creado_en
        """,
        (nombre, (descripcion or "").strip() or None,
         json.dumps(definicion or (resultado or {}).get("definicion") or {},
                    default=str),
         consulta_id, getattr(actor, "uid", None)),
    )
    panel_id = fila["id"]

    # Los ids que ya no existen en la bóveda no son un error: alguien pudo
    # darse de baja entre la consulta y esta operación, y eso es la baja
    # funcionando. Se informan aparte.
    existentes = {
        str(f["id_persona"])
        for f in db.todas(
            conn,
            "select id_persona from persona where id_persona = any(%s::uuid[])",
            (ids,),
        )
    }
    altas, ya_estaban = [], []
    for id_persona in ids:
        if id_persona not in existentes:
            continue
        agregado = db.ejecutar(
            conn,
            "insert into membresia (panel_id, id_persona) values (%s, %s) "
            "on conflict (panel_id, id_persona) do nothing",
            (panel_id, id_persona),
        )
        (altas if agregado else ya_estaban).append(id_persona)

    # Quién puede ser convocado y quién no. El gate de R1.3 sigue aplicando:
    # la membresía puede existir sin consentimiento, la convocatoria no.
    sin_consentimiento = [
        str(f["id_persona"])
        for f in db.todas(
            conn,
            """
            select p.id_persona from persona p
             where p.id_persona = any(%s::uuid[])
               and (p.estado <> 'activa'
                 or not exists (select 1 from consentimiento c
                                 where c.id_persona = p.id_persona
                                   and c.finalidad = 'contacto_participacion'
                                   and c.estado = 'vigente'))
            """,
            (ids,),
        )
    ]

    fue_semantica = bool(
        (definicion or (resultado or {}).get("definicion") or {}).get("criterios")
    ) or bool((resultado or {}).get("diagnostico"))
    if fue_semantica:
        auditoria.registrar_reidentificacion(
            conn, ids, actor=actor, motivo="panel_desde_consulta",
            contexto={"panel_id": panel_id, "nombre": nombre,
                      "personas": len(ids)},
        )

    conn.commit()
    return {
        "id": panel_id,
        "nombre": fila["nombre"],
        "descripcion": fila["descripcion"],
        "estado": fila["estado"],
        "creado_en": fila["creado_en"].isoformat(),
        "origen": "consulta",
        "miembros": len(altas) + len(ya_estaban),
        "altas": len(altas),
        "ya_eran_miembros": len(ya_estaban),
        "no_encontrados": [i for i in ids if i not in existentes],
        "no_convocables": sin_consentimiento,
        "aviso": (
            {
                "personas": len(sin_consentimiento),
                "mensaje": (
                    f"{len(sin_consentimiento)} de los {len(ids)} integrantes "
                    f"no tienen consentimiento vigente de contacto o están "
                    f"pendientes de consentimiento. Son miembros del panel, "
                    f"pero no pueden ser convocados hasta regularizarlo."
                ),
            } if sin_consentimiento else None
        ),
        "registrado_como_reidentificacion": fue_semantica,
    }
