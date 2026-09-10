"""R2.1 y R2.6 — participación por ola y tablero de salud del panel.

La Fase 1 ya captura el dato: `participacion` tiene una fila por (encuesta,
persona) con cuándo se la convocó, si respondió y en qué estado quedó la
calidad. Lo que faltaba era leerlo. Este módulo no agrega información: la
consolida y la expone.

Tres lecturas, que son las tres preguntas que se le hacen a un panel:

* **¿Responde?** Tasa de respuesta, global y por ola. Es la métrica de
  salud más directa: un panel con 15 % de respuesta no es un panel de mil
  personas, es uno de ciento cincuenta.
* **¿Hace cuánto que no lo molesto?** Tiempo desde el último contacto, por
  persona y en distribución. Sirve para las dos cosas: no quemar a quien
  acaba de responder tres olas, y no dejar dormido a quien no se contactó
  en un año (después no vuelve).
* **¿Reparto parejo?** Distribución de convocatorias. Si el 10 % del panel
  se come el 60 % de las convocatorias, la muestra no es del panel: es de
  ese 10 %.

Todo pasa en la bóveda. Las listas de personas salen con `id_persona` y
nombre, porque el tablero se usa para decidir a quién convocar; quien lo
abre está viendo PII y la ruta registra la reidentificación.
"""

from . import db
from .errores import NoEncontrado

TRAMOS_CONTACTO = (
    ("nunca", None, None),
    ("hasta_30_dias", 0, 30),
    ("31_a_90_dias", 31, 90),
    ("91_a_180_dias", 91, 180),
    ("mas_de_180_dias", 181, None),
)

TRAMOS_CONVOCATORIAS = ("0", "1", "2", "3", "4", "5_o_mas")

DIAS_DORMIDO = 30
"""Desde cuántos días sin contacto una persona entra en la lista de dormidos.

La lista es para actuar, no para mirar: ordenar a todo el panel por
antigüedad de contacto y mostrar los primeros quince no dice nada cuando a
todos se los convocó la semana pasada. Un mes es cuando la falta de contacto
empieza a importar."""


def _tasa(respondieron, convocados):
    return round(respondieron / convocados, 4) if convocados else None


def por_ola(conn, panel_id=None):
    """R2.1 — una fila por encuesta: convocados, respuestas, tasa y calidad."""
    filas = db.todas(
        conn,
        """
        select e.id, e.panel_id, e.nombre, e.fecha_campo, e.estado, e.ref_estudio,
               pa.nombre as panel,
               count(p.id)::int                                          as convocados,
               count(p.id) filter (where p.respondio)::int               as respondieron,
               count(p.id) filter (where p.calidad_estado = 'ok')::int    as calidad_ok,
               count(p.id) filter (where p.calidad_estado = 'sospechoso')::int
                                                                          as calidad_sospechosa,
               count(p.id) filter (where p.calidad_estado = 'pendiente')::int
                                                                          as calidad_pendiente,
               min(p.convocado_en) as primera_convocatoria,
               max(p.respondio_en) as ultima_respuesta
          from encuesta e
          join panel pa on pa.id = e.panel_id
          left join participacion p on p.encuesta_id = e.id
         where (%s::bigint is null or e.panel_id = %s::bigint)
         group by e.id, e.panel_id, e.nombre, e.fecha_campo, e.estado,
                  e.ref_estudio, pa.nombre
         order by e.fecha_campo desc nulls last, e.id desc
        """,
        (panel_id, panel_id),
    )
    return [
        {
            "encuesta_id": f["id"],
            "panel_id": f["panel_id"],
            "panel": f["panel"],
            "nombre": f["nombre"],
            "fecha_campo": f["fecha_campo"].isoformat() if f["fecha_campo"] else None,
            "estado": f["estado"],
            "ref_estudio": str(f["ref_estudio"]),
            "convocados": f["convocados"],
            "respondieron": f["respondieron"],
            "tasa_respuesta": _tasa(f["respondieron"], f["convocados"]),
            "calidad_ok": f["calidad_ok"],
            "calidad_sospechosa": f["calidad_sospechosa"],
            "calidad_pendiente": f["calidad_pendiente"],
            "primera_convocatoria": (
                f["primera_convocatoria"].isoformat() if f["primera_convocatoria"] else None
            ),
            "ultima_respuesta": (
                f["ultima_respuesta"].isoformat() if f["ultima_respuesta"] else None
            ),
        }
        for f in filas
    ]


def _por_persona(conn, panel_id, estado="activo"):
    """Una fila por miembro del panel con su historial de participación.

    El `left join` es lo que hace útil la consulta: quien nunca fue
    convocado tiene que aparecer con 0, y con un `inner join` sería
    invisible. Un miembro nunca convocado es exactamente el caso que el
    tablero está para encontrar.
    """
    return db.todas(
        conn,
        """
        select m.id_persona, p.nombre, p.email, d.sexo, d.localidad, d.tramo_etario,
               count(pa.id)::int                            as convocatorias,
               count(pa.id) filter (where pa.respondio)::int as respuestas,
               max(pa.convocado_en)                         as ultimo_contacto,
               max(pa.respondio_en)                         as ultima_respuesta,
               count(pa.id) filter (where pa.calidad_estado = 'sospechoso')::int
                                                            as calidad_sospechosa
          from membresia m
          join persona p on p.id_persona = m.id_persona
          left join v_demografia d on d.id_persona = m.id_persona
          left join participacion pa on pa.id_persona = m.id_persona
          left join encuesta e on e.id = pa.encuesta_id and e.panel_id = m.panel_id
         where m.panel_id = %s and (%s::text is null or m.estado = %s::text)
           and (pa.id is null or e.id is not null)
         group by m.id_persona, p.nombre, p.email, d.sexo, d.localidad, d.tramo_etario
         order by p.nombre nulls last
        """,
        (panel_id, estado, estado),
    )


def _dias(fecha, ahora):
    if fecha is None:
        return None
    return max(0, (ahora - fecha).days)


def _tramo_contacto(dias):
    if dias is None:
        return "nunca"
    for nombre, desde, hasta in TRAMOS_CONTACTO[1:]:
        if (desde is None or dias >= desde) and (hasta is None or dias <= hasta):
            return nombre
    return "mas_de_180_dias"


def _tramo_convocatorias(cuantas):
    return str(cuantas) if cuantas < 5 else "5_o_mas"


def tablero(conn, panel_id, estado="activo", limite_listas=15):
    """R2.6 — tablero de participación de un panel.

    Devuelve las tres lecturas juntas más dos listas cortas para actuar: a
    quién hace más que no se contacta, y a quién se está convocando de más.
    """
    panel = db.una(
        conn, "select id, nombre from panel where id = %s", (panel_id,)
    )
    if not panel:
        raise NoEncontrado(f"No existe el panel {panel_id}.")

    ahora = db.una(conn, "select now() as ahora")["ahora"]
    filas = _por_persona(conn, panel_id, estado)

    personas = []
    for f in filas:
        dias = _dias(f["ultimo_contacto"], ahora)
        personas.append({
            "id_persona": str(f["id_persona"]),
            "nombre": f["nombre"],
            "email": f["email"],
            "sexo": f["sexo"],
            "localidad": f["localidad"],
            "tramo_etario": f["tramo_etario"],
            "convocatorias": f["convocatorias"],
            "respuestas": f["respuestas"],
            "tasa_respuesta": _tasa(f["respuestas"], f["convocatorias"]),
            "calidad_sospechosa": f["calidad_sospechosa"],
            "ultimo_contacto": (
                f["ultimo_contacto"].isoformat() if f["ultimo_contacto"] else None
            ),
            "dias_sin_contacto": dias,
            "tramo_contacto": _tramo_contacto(dias),
        })

    olas = por_ola(conn, panel_id)
    convocados = sum(o["convocados"] for o in olas)
    respondieron = sum(o["respondieron"] for o in olas)

    distribucion_contacto = {nombre: 0 for nombre, _, _ in TRAMOS_CONTACTO}
    for persona in personas:
        distribucion_contacto[persona["tramo_contacto"]] += 1

    distribucion_convocatorias = {tramo: 0 for tramo in TRAMOS_CONVOCATORIAS}
    for persona in personas:
        distribucion_convocatorias[_tramo_convocatorias(persona["convocatorias"])] += 1

    total_personas = len(personas)
    nunca = distribucion_contacto["nunca"]
    dias_conocidos = [p["dias_sin_contacto"] for p in personas
                      if p["dias_sin_contacto"] is not None]

    # Concentración: qué parte de las convocatorias se come el 10 % más
    # convocado. Es la lectura que dice si la muestra es del panel o de un
    # pedacito del panel.
    por_convocatorias = sorted(
        personas, key=lambda p: (-p["convocatorias"], p["id_persona"])
    )
    cabeza = max(1, round(total_personas * 0.1)) if total_personas else 0
    convocatorias_totales = sum(p["convocatorias"] for p in personas)
    de_la_cabeza = sum(p["convocatorias"] for p in por_convocatorias[:cabeza])

    return {
        "panel_id": panel["id"],
        "panel": panel["nombre"],
        "estado_membresia": estado,
        "miembros": total_personas,
        "olas": olas,
        "respuesta": {
            "convocatorias_emitidas": convocados,
            "respuestas": respondieron,
            "tasa_respuesta": _tasa(respondieron, convocados),
            "miembros_que_respondieron_alguna": sum(
                1 for p in personas if p["respuestas"]
            ),
            "miembros_nunca_convocados": nunca,
        },
        "ultimo_contacto": {
            "distribucion": distribucion_contacto,
            "mediana_dias": (
                sorted(dias_conocidos)[len(dias_conocidos) // 2]
                if dias_conocidos else None
            ),
            "umbral_dormido_dias": DIAS_DORMIDO,
            "mas_dormidos": sorted(
                (
                    p for p in personas
                    if p["dias_sin_contacto"] is not None
                    and p["dias_sin_contacto"] >= DIAS_DORMIDO
                ),
                key=lambda p: -p["dias_sin_contacto"],
            )[:limite_listas],
            "nunca_contactados": [
                p for p in personas if p["dias_sin_contacto"] is None
            ][:limite_listas],
        },
        "convocatorias": {
            "distribucion": distribucion_convocatorias,
            "promedio_por_miembro": (
                round(convocatorias_totales / total_personas, 2)
                if total_personas else None
            ),
            "maximo": max((p["convocatorias"] for p in personas), default=0),
            "concentracion_decil_superior": (
                round(de_la_cabeza / convocatorias_totales, 4)
                if convocatorias_totales else None
            ),
            "mas_convocados": por_convocatorias[:limite_listas],
        },
    }
