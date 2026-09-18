"""R4.1.c — La vista longitudinal.

El activo más valioso de un panel es seguir a la misma gente en el tiempo, y
hasta acá el sistema no lo explotaba: tenía olas acumuladas de las mismas
personas y ninguna forma de ver cómo se movieron.

Dos vistas, y son distintas a propósito:

**La línea de tiempo de una persona** (`de_persona`) cruza los dos stores: las
olas y las fechas salen de la bóveda, las respuestas del store semántico, y se
unen por `id_persona` y `ref_estudio`. Ver la línea de tiempo de una persona
**identificada** es una reidentificación y se registra como tal (R3.10): es
justo la operación que deshace la seudonimización, y hacerla más cómoda sin
registrarla sería aflojar el diseño de dos stores por la puerta de atrás.

**El movimiento entre categorías** (`transiciones`) no reidentifica a nadie:
trabaja sobre conjuntos de `id_persona` y devuelve conteos. Es la matriz de
cuántos pasaron de cada categoría a cada otra entre dos olas de una serie.

Una persona presente en una sola ola se muestra como tal, sin inventar
continuidad: aparece en la diagonal de nada, en una fila aparte de
`solo_en_una`. Rellenar el hueco con su único valor diría que no cambió, que
es una afirmación que nadie hizo.
"""

from . import auditoria, db, series
from .errores import DatosInvalidos, NoEncontrado

SIN_MAPEAR = "(sin mapear)"
"""Una opción que la serie no mapeó a ninguna categoría común.

Se informa aparte y nunca dentro de una categoría real: es la misma regla que
«(sin dato)» en la composición. Si engrosara una categoría, el movimiento
entre olas mentiría."""

SIN_RESPUESTA = "(no respondió)"
"""La persona estuvo en la ola pero no contestó esa pregunta. Distinto de no
haber estado: por eso son dos etiquetas y no una."""


# ── La línea de tiempo de una persona ────────────────────────────────

def de_persona(boveda, semantica, id_persona, actor=None, con_respuestas=True):
    """En qué olas participó, cuándo, y qué contestó en cada una.

    Registra la reidentificación: la ruta no tiene que acordarse.
    """
    persona = db.una(
        boveda,
        "select id_persona, nombre, documento, estado from persona "
        " where id_persona = %s", (str(id_persona),))
    if not persona:
        raise NoEncontrado(f"No existe la persona {id_persona}.")

    olas = db.todas(
        boveda,
        """
        select e.id, e.nombre, e.fecha_campo, e.ref_estudio, e.estado,
               pa.convocado_en, pa.respondio, pa.respondio_en,
               pa.calidad_estado, pa.origen,
               p.id as panel_id, p.nombre as panel
          from participacion pa
          join encuesta e on e.id = pa.encuesta_id
          join panel p on p.id = e.panel_id
         where pa.id_persona = %s
         order by coalesce(e.fecha_campo, pa.convocado_en::date), e.id
        """,
        (str(id_persona),))

    respuestas_por_estudio = {}
    if con_respuestas and olas:
        for fila in db.todas(
            semantica,
            """
            select c.ref_estudio, p.codigo, p.texto, p.tipo, r.valor_texto
              from respuesta r
              join pregunta p on p.id = r.pregunta_id
              join cuestionario c on c.id = p.cuestionario_id
              join individuo i on i.id = r.individuo_id
             where i.id_persona = %s
               and c.ref_estudio = any(%s::uuid[])
             order by c.id, p.orden nulls last, p.codigo
            """,
            (str(id_persona), [str(o["ref_estudio"]) for o in olas]),
        ):
            respuestas_por_estudio.setdefault(str(fila["ref_estudio"]), []).append({
                "codigo": fila["codigo"],
                "texto": fila["texto"],
                "tipo": fila["tipo"],
                "respuesta": fila["valor_texto"],
            })

    auditoria.registrar_reidentificacion(
        boveda, [str(id_persona)], actor=actor, motivo="ficha",
        contexto={"vista": "longitudinal",
                  "olas": len(olas)})

    return {
        "id_persona": str(persona["id_persona"]),
        "nombre": persona["nombre"],
        "estado": persona["estado"],
        "olas": [
            {
                "encuesta_id": o["id"],
                "encuesta": o["nombre"],
                "panel": o["panel"],
                "panel_id": o["panel_id"],
                "fecha_campo": (o["fecha_campo"].isoformat()
                                if o["fecha_campo"] else None),
                "convocado_en": o["convocado_en"].isoformat(),
                "respondio": o["respondio"],
                "respondio_en": (o["respondio_en"].isoformat()
                                 if o["respondio_en"] else None),
                "calidad_estado": o["calidad_estado"],
                "origen": o["origen"],
                "ref_estudio": str(o["ref_estudio"]),
                "respuestas": respuestas_por_estudio.get(str(o["ref_estudio"]), []),
            }
            for o in olas
        ],
        # Con una sola ola no hay línea de tiempo, y decirlo es mejor que
        # dibujar una de un punto.
        "aviso": (
            "Esta persona participó en una sola ola: no hay evolución que "
            "mostrar todavía."
        ) if len(olas) == 1 else None,
    }


# ── El movimiento entre categorías de una serie ──────────────────────

def _categoria_de(valor, opciones, mapeo):
    """De lo que contestó la persona a la categoría común de la serie.

    `valor_texto` guarda la etiqueta de la opción, no su clave, así que se
    resuelve por las dos puntas: la ingesta escribe el texto y el mapeo se
    declara sobre las claves de `pregunta.opciones`.
    """
    if valor is None or str(valor).strip() == "":
        return SIN_RESPUESTA
    texto = str(valor).strip()
    clave = texto
    for opcion, etiqueta in (opciones or {}).items():
        if str(etiqueta).strip().lower() == texto.lower():
            clave = str(opcion)
            break
    return mapeo.get(clave) or mapeo.get(texto) or SIN_MAPEAR


def transiciones(semantica, clave_serie, desde=None, hasta=None,
                 ids_persona=None):
    """Cuántas personas pasaron de cada categoría a cada otra, entre dos olas.

    `desde` y `hasta` son `cuestionario_id`. Sin ellos se toman la primera y
    la última ola de la serie por fecha de campo, que es la comparación que
    casi siempre se quiere.

    No reidentifica: trabaja con `id_persona` y devuelve conteos.
    """
    serie = series.obtener(semantica, clave_serie)
    if len(serie["preguntas"]) < 2:
        raise DatosInvalidos(
            f"La serie «{serie['clave']}» tiene {len(serie['preguntas'])} "
            f"pregunta(s): para ver un movimiento hacen falta dos olas.",
            {"preguntas": len(serie["preguntas"])})

    por_ola = {p["cuestionario_id"]: p for p in serie["preguntas"]}
    ordenadas = list(serie["preguntas"])
    origen = por_ola.get(int(desde)) if desde else ordenadas[0]
    destino = por_ola.get(int(hasta)) if hasta else ordenadas[-1]
    if not origen or not destino:
        raise NoEncontrado(
            "Alguna de las dos olas pedidas no está en la serie.")
    if origen["cuestionario_id"] == destino["cuestionario_id"]:
        raise DatosInvalidos("Las dos olas de una comparación tienen que ser "
                             "distintas.")

    def respuestas_de(pregunta):
        filas = db.todas(
            semantica,
            """
            select i.id_persona, r.valor_texto
              from respuesta r
              join individuo i on i.id = r.individuo_id
             where r.pregunta_id = %s
               and (%s::uuid[] is null or i.id_persona = any(%s::uuid[]))
            """,
            (pregunta["pregunta_id"],
             [str(i) for i in ids_persona] if ids_persona else None,
             [str(i) for i in ids_persona] if ids_persona else None))
        return {
            str(f["id_persona"]): _categoria_de(
                f["valor_texto"], pregunta["opciones"], pregunta["mapeo"])
            for f in filas
        }

    antes, despues = respuestas_de(origen), respuestas_de(destino)
    en_las_dos = set(antes) & set(despues)

    matriz = {}
    for id_persona in en_las_dos:
        matriz.setdefault(antes[id_persona], {}).setdefault(
            despues[id_persona], 0)
        matriz[antes[id_persona]][despues[id_persona]] += 1

    categorias = [c["clave"] for c in serie["categorias"]]
    estables = sum(matriz.get(c, {}).get(c, 0) for c in categorias)

    return {
        "serie": serie["clave"],
        "desde": {"cuestionario_id": origen["cuestionario_id"],
                  "ola": origen["ola"], "fecha_campo": origen["fecha_campo"],
                  "codigo": origen["codigo"]},
        "hasta": {"cuestionario_id": destino["cuestionario_id"],
                  "ola": destino["ola"], "fecha_campo": destino["fecha_campo"],
                  "codigo": destino["codigo"]},
        "categorias": categorias,
        "en_las_dos_olas": len(en_las_dos),
        "celdas": [
            {"desde": origen_categoria, "hasta": destino_categoria,
             "personas": cuantos}
            for origen_categoria, destinos in sorted(matriz.items())
            for destino_categoria, cuantos in sorted(destinos.items())
        ],
        "estables": estables,
        "se_movieron": len(en_las_dos) - estables,
        # Una persona presente en una sola ola se muestra como tal: no se le
        # inventa continuidad rellenando el hueco con su único valor, que
        # diría que no cambió.
        "solo_en_una": {
            "solo_al_inicio": len(set(antes) - set(despues)),
            "solo_al_final": len(set(despues) - set(antes)),
            "aviso": (
                "No entran en la matriz: no hay un «de dónde» o un «a dónde» "
                "para ellas, y ponerlas en la diagonal diría que no cambiaron."
            ),
        },
        "sin_mapear": sum(
            1 for id_persona in en_las_dos
            if SIN_MAPEAR in (antes[id_persona], despues[id_persona])),
    }
