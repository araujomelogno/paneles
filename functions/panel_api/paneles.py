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
