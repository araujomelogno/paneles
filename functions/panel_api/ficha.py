"""La ficha de un panelista vista desde un resultado (R7.3 y R7.6).

Un resultado de consulta es una lista de `id_persona`. Para saber quién es
cada uno había que salir a la sección de panelistas y buscarlo, justo cuando
se está evaluando si el resultado sirve.

── Dos cosas distintas, y conviene no mezclarlas ──

**La ficha seudónima** (R7.3) muestra los atributos del catálogo y nada
identificatorio: ni nombre, ni documento, ni correo, ni celular. No es una
omisión piadosa — es lo que mantiene honesto el registro de
reidentificación. Si la ficha mostrara el nombre con un clic, la auditoría
de R3.10 dejaría de reflejar quién vio los datos de quién, que es
exactamente lo que existe para demostrar. Ver quién es sigue siendo la
acción deliberada de siempre, con motivo y registro.

**Las respuestas procesadas** (R7.6) sí cruzan la separación entre los dos
stores, a propósito: muestran qué opinó una persona identificada. Es
necesario para operar —es la única forma de entender por qué alguien aparece
o no en una consulta— y por eso **se registra igual que una
reidentificación**, con su motivo propio (`respuestas_panelista`). No revela
contacto, pero une identidad y contenido, que es justo lo que los dos stores
mantienen separado.

── Una advertencia que no es código ──

Los demográficos combinados son cuasi-identificadores: sexo, localidad y
tramo etario juntos pueden señalar a una persona en un panel chico. La ficha
no es reidentificación, pero tampoco es anónima.
"""

from . import atributos, auditoria, db

MOTIVO_RESPUESTAS = "respuestas_panelista"

# Lo que una ficha seudónima **no** devuelve nunca. La lista está acá y no
# implícita en el `select` porque hay una prueba que la recorre: si alguien
# agrega un campo al dict de salida, la prueba lo atrapa.
CAMPOS_PROHIBIDOS = ("nombre", "documento", "email", "celular")

PAGINA_POR_DEFECTO = 25
PAGINA_MAXIMA = 200


def seudonima(conn_boveda, id_persona, momento=None):
    """Los atributos de la persona, sin un solo dato identificatorio.

    `momento` llega hasta `atributos.valores_de` para que la ficha abierta
    desde una ola vieja pueda mostrar los valores de entonces (R4.1.a).
    """
    fila = db.una(
        conn_boveda,
        "select id_persona, estado, creado_en from persona where id_persona = %s",
        (str(id_persona),))
    if not fila:
        from .errores import NoEncontrado

        raise NoEncontrado(f"No existe la persona {id_persona}.")

    paneles = db.todas(
        conn_boveda,
        """select p.id, p.nombre
             from membresia m join panel p on p.id = m.panel_id
            where m.id_persona = %s and m.estado = 'activo'
            order by p.nombre""",
        (str(id_persona),))

    return {
        "id_persona": str(fila["id_persona"]),
        "estado": fila["estado"],
        "enrolado_en": fila["creado_en"].isoformat(),
        "atributos": atributos.valores_de(
            conn_boveda, str(id_persona), momento=momento),
        "paneles": [{"id": p["id"], "nombre": p["nombre"]} for p in paneles],
    }


# Cuántas evidencias se aceptan por pedido. Un individuo del ranking trae una
# por criterio semántico; ninguna consulta razonable tiene más que esto, y el
# tope impide usar la ficha para bajar el contenido entero de alguien sin
# pasar por R7.6, que es la vía que deja registro.
EVIDENCIAS_MAXIMAS = 20


def ids_de_evidencia(crudo):
    """`"12,34"` o `[12, 34]` → `[12, 34]`. Lo que no es un entero se ignora:
    un id mal formado no es motivo para no mostrar la ficha."""
    if crudo is None:
        return []
    partes = crudo if isinstance(crudo, (list, tuple)) else str(crudo).split(",")
    ids = []
    for parte in partes:
        try:
            ids.append(int(str(parte).strip()))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(ids))[:EVIDENCIAS_MAXIMAS]


def evidencia(conn_semantica, id_persona, respuesta_ids):
    """R7.3 — la evidencia del resultado: qué respondió y de qué estudio.

    La evidencia **depende de qué consulta se está mirando**, así que no se
    recalcula acá: llega desde el resultado, que ya la trae (cada individuo
    del ranking viene con los `respuesta_id` que lo justificaron). Recalcularla
    con el criterio como parámetro podría dar otra respuesta que la que el
    analista tiene en pantalla, y la ficha tiene que explicar *ese* resultado.

    Lo que sí hace el servidor es **leerla de la base** y no confiar en lo que
    manda la pantalla: devuelve el texto que está guardado, y solo las
    respuestas que son **de esta persona**. Un id ajeno se descarta en
    silencio —la ficha no es una vía para leer respuestas de otro— y se
    informa cuántos se descartaron.

    No registra reidentificación: es contenido atado a un `id_persona`, que es
    exactamente lo que ya mostraba la lista de resultados. Lo que se audita es
    unir identidad y contenido (R7.6), y acá no hay identidad.
    """
    ids = ids_de_evidencia(respuesta_ids)
    if not ids:
        return {"items": [], "descartadas": 0}
    filas = db.todas(
        conn_semantica,
        """
        select respuesta_id, ref_estudio, estudio, fecha_campo,
               pregunta_codigo, pregunta_texto, pregunta_tipo,
               valor_texto, texto_embebido
          from v_respuesta_estudio
         where respuesta_id = any(%s::bigint[])
           and id_persona = %s
        """,
        (ids, str(id_persona)),
    )
    por_id = {f["respuesta_id"]: f for f in filas}
    items = [
        {
            "respuesta_id": f["respuesta_id"],
            "ref_estudio": str(f["ref_estudio"]) if f["ref_estudio"] else None,
            "estudio": f["estudio"],
            "fecha_campo": (f["fecha_campo"].isoformat()
                            if f["fecha_campo"] else None),
            "codigo": f["pregunta_codigo"],
            "pregunta": f["pregunta_texto"],
            "tipo": f["pregunta_tipo"],
            "respuesta": f["valor_texto"],
            "texto_embebido": f["texto_embebido"],
        }
        # En el orden en que llegaron, que es el del ranking.
        for f in (por_id[i] for i in ids if i in por_id)
    ]
    return {"items": items, "descartadas": len(ids) - len(items)}


def estudios_con_respuestas(conn_semantica, id_persona):
    """Para el filtro por estudio: solo los estudios donde esta persona habló."""
    filas = db.todas(
        conn_semantica,
        """select c.ref_estudio, c.nombre as estudio, c.fecha_campo,
                  count(*) as respuestas
             from respuesta r
             join individuo i    on i.id = r.individuo_id
             join pregunta p     on p.id = r.pregunta_id
             join cuestionario c on c.id = p.cuestionario_id
            where i.id_persona = %s
            group by c.ref_estudio, c.nombre, c.fecha_campo
            order by c.fecha_campo desc nulls last, c.nombre""",
        (str(id_persona),))
    return [
        {"ref_estudio": str(f["ref_estudio"]), "estudio": f["estudio"],
         "fecha_campo": f["fecha_campo"].isoformat() if f["fecha_campo"] else None,
         "respuestas": f["respuestas"]}
        for f in filas
    ]


def respuestas(conn_semantica, id_persona, *, ref_estudio=None, busqueda=None,
               pagina=1, tamano=PAGINA_POR_DEFECTO):
    """Las respuestas procesadas de una persona, paginadas **en la base**.

    No se trae todo y se recorta en el cliente: con una persona de veinte
    olas eso son miles de filas y cada una lleva su texto embebido. El
    `count(*)` va en la misma consulta por la misma razón.

    El `embedding` **no se selecciona nunca**: son 512 números por fila que
    nadie va a mirar y que multiplicarían por diez el tamaño de la respuesta.
    """
    tamano = max(1, min(int(tamano or PAGINA_POR_DEFECTO), PAGINA_MAXIMA))
    pagina = max(1, int(pagina or 1))
    patron = f"%{busqueda.strip()}%" if (busqueda or "").strip() else None

    filas = db.todas(
        conn_semantica,
        """
        select c.ref_estudio, c.nombre as estudio, c.fecha_campo,
               p.codigo as pregunta_codigo, p.texto as pregunta_texto,
               p.tipo as pregunta_tipo, p.orden as pregunta_orden,
               r.valor_texto, r.texto_embebido,
               count(*) over () as total
          from respuesta r
          join individuo i    on i.id = r.individuo_id
          join pregunta p     on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where i.id_persona = %s
           and (%s::uuid is null or c.ref_estudio = %s::uuid)
           and (%s::text is null
                or p.texto ilike %s::text
                or r.valor_texto ilike %s::text)
         order by c.fecha_campo desc nulls last, c.nombre,
                  p.orden nulls last, p.codigo
         limit %s offset %s
        """,
        (str(id_persona), ref_estudio, ref_estudio,
         patron, patron, patron, tamano, (pagina - 1) * tamano))

    total = filas[0]["total"] if filas else 0
    return {
        "items": [
            {
                "ref_estudio": str(f["ref_estudio"]),
                "estudio": f["estudio"],
                "fecha_campo": (f["fecha_campo"].isoformat()
                                if f["fecha_campo"] else None),
                "codigo": f["pregunta_codigo"],
                "pregunta": f["pregunta_texto"],
                "tipo": f["pregunta_tipo"],
                "respuesta": f["valor_texto"],
                # R7.6 — lo que realmente se vectorizó. Es la única forma de
                # ver que una pregunta cerrada quedó sin traducir: dice
                # «¿Qué marca fumás? → 11427» en vez de «→ Nevada», y esa
                # respuesta está en la base pero es inútil para buscar.
                "texto_embebido": f["texto_embebido"],
            }
            for f in filas
        ],
        "total": total,
        "pagina": pagina,
        "tamano": tamano,
        "paginas": (total + tamano - 1) // tamano if total else 0,
        "sin_respuestas": total == 0 and not ref_estudio and not patron,
    }


def respuestas_con_registro(conn_boveda, conn_semantica, id_persona, *,
                            actor=None, **filtros):
    """Lo mismo, dejando el rastro. Es el único camino que usan las rutas.

    Separado de `respuestas()` para que la consulta se pueda probar sola,
    pero la ruta llama **siempre** a esta: el registro no es opcional y no
    depende de que quien escriba la próxima ruta se acuerde.
    """
    salida = respuestas(conn_semantica, id_persona, **filtros)
    auditoria.registrar_reidentificacion(
        conn_boveda, [str(id_persona)], actor=actor, motivo=MOTIVO_RESPUESTAS,
        contexto={"respuestas_vistas": len(salida["items"]),
                  "ref_estudio": filtros.get("ref_estudio"),
                  "pagina": salida["pagina"]})
    conn_boveda.commit()
    return salida
