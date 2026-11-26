"""D99 · R-ALB.2 — el catálogo local de encuestas de Alchemer.

La búsqueda de D97 bajaba la lista entera de la API en cada pedido y recién
después filtraba por título: con 2.172 encuestas (22 páginas a ~8 s) se
pasaba de los 60 s de Hosting, y con su tope de 10 páginas no veía las
últimas. Ahora la búsqueda es una consulta a una tabla, y la tabla la llena
una **sincronización en segundo plano**:

* **Una página de la API por tarea** de Cloud Tasks, por la cola de siempre
  (`procesaringesta`, que despacha por `tipo`). La primera página dice
  cuántas hay y encola el resto. Sin tope.
* **El avance se deriva de las páginas** (`alchemer_sincronizacion_pagina`),
  no de un contador: con tareas en paralelo un acumulador se desincroniza.
* **Una sola en curso**, y lo garantiza el índice único parcial de la
  `0039`. Pedir otra mientras corre devuelve la que corre; si quedó
  estancada (un lote que nadie tomó), pedirla la **reanuda**.
* **Incremental si se puede, y comprobado.** Después de una completa, las
  siguientes piden a Alchemer solo lo modificado desde la última fecha del
  catálogo (`filter[field]=modified_on`). La documentación de la API v5 no se
  pudo consultar al escribir esto (R-ALB.4), así que **no se confía**: si
  Alchemer rechaza el filtro o devuelve una encuesta anterior a la fecha
  pedida, la corrida pasa a completa y lo deja anotado (`filtro`).
* **Una completa borra lo que no vio**: las encuestas que ya no existen en
  Alchemer. Una incremental no borra nada.

Del catálogo solo sale id, título, estado y fechas: ni cuestionario ni
respuestas.
"""

import datetime
import os

from . import alchemer, db, diferida
from .errores import Conflicto, DatosInvalidos

TIPO_TAREA = "alchemer_catalogo"
INDICE_UNA_EN_CURSO = "alchemer_sincronizacion_una_en_curso"

COMPLETA, INCREMENTAL = "completa", "incremental"
EN_CURSO, TERMINADA, FALLIDA = "en_curso", "terminada", "fallida"

# Una sincronización sin ninguna página procesada en este tiempo se da por
# estancada: «Actualizar» la reanuda en lugar de esperar para siempre.
ESTANCADA_MINUTOS = 15
# La incremental pide desde la última modificación del catálogo, con este
# margen: las fechas de Alchemer no traen zona horaria.
MARGEN_INCREMENTAL = datetime.timedelta(days=2)

# R-ALB.2 — «activas» son todas menos las cerradas, archivadas o borradas:
# una encuesta en diseño también se vincula antes de salir a campo.
ESTADOS_INACTIVOS = ("closed", "archived", "deleted", "cerrada", "archivada")
LIMITE_DE_BUSQUEDA = 50

AVISO_CATALOGO_VACIO = (
    "El catálogo de encuestas de Alchemer todavía está vacío: apretá «Actualizar» "
    "(tarda unos minutos y corre en segundo plano) o pegá la URL o el id de la encuesta.")


def crear_encolador():
    """La cola de la ingesta (`procesaringesta`), con su propio `tipo` y
    prefijo de nombre de tarea: no agrega una cola ni un secreto."""
    if (os.environ.get("ENCOLADOR_TAREAS") or "").lower() in ("memoria", "test"):
        return diferida.EncoladorEnMemoria()
    return diferida.EncoladorCloudTasks(prefijo="alchemer", tipo=TIPO_TAREA)


# ── Fechas de Alchemer ────────────────────────────────────────────────

def _fecha(valor):
    """`2026-11-15 10:23:45` (o con `T`, o solo la fecha), sin zona."""
    if not valor:
        return None
    try:
        return datetime.datetime.fromisoformat(str(valor).strip()[:19]).replace(tzinfo=None)
    except ValueError:
        return None


# ── Escribir el catálogo ──────────────────────────────────────────────

def _guardar(conn, filas, sincronizacion_id=None):
    for f in filas:
        db.ejecutar(conn, """
            insert into alchemer_encuesta
                   (id, titulo, titulo_busqueda, estado, creada, modificada, vista_en,
                    actualizado_en)
            values (%s, %s, %s, %s, %s, %s, %s, now())
            on conflict (id) do update
               set titulo = excluded.titulo, titulo_busqueda = excluded.titulo_busqueda,
                   estado = excluded.estado,
                   creada = coalesce(excluded.creada, alchemer_encuesta.creada),
                   modificada = coalesce(excluded.modificada, alchemer_encuesta.modificada),
                   vista_en = coalesce(excluded.vista_en, alchemer_encuesta.vista_en),
                   actualizado_en = now()
        """, (f["id"], f["titulo"], alchemer._clave(f["titulo"]), f["estado"],
              f["creada"], f["modificada"], sincronizacion_id))
    return len(filas)


def _de_la_api(cruda):
    return {"id": str(cruda.get("id")), "titulo": alchemer._texto(cruda.get("title")),
            "estado": cruda.get("status"), "creada": _fecha(cruda.get("created_on")),
            "modificada": _fecha(cruda.get("modified_on"))}


def refrescar(conn, traido):
    """R-ALB.2 — al vincular una encuesta se refresca su fila con lo que ya
    trajo `alchemer.cuestionario`, aunque el catálogo esté desactualizado.
    No hace otra llamada."""
    if not traido or not traido.get("alchemer_id"):
        return
    _guardar(conn, [{"id": str(traido["alchemer_id"]), "titulo": traido.get("titulo") or "",
                     "estado": traido.get("estado"), "creada": _fecha(traido.get("creada")),
                     "modificada": _fecha(traido.get("modificada"))}])


# ── Buscar ────────────────────────────────────────────────────────────

def buscar(conn, q=None, todas=False, limite=LIMITE_DE_BUSQUEDA):
    """R-ALB.2 — contra el catálogo, sin llamar a Alchemer. Por defecto las
    activas; con `todas`, también las cerradas. Las más recientes primero."""
    condiciones, params = [], []
    clave = alchemer._clave(q)
    if clave:
        condiciones.append("titulo_busqueda like %s")
        params.append(f"%{clave}%")
        if clave.isdigit():
            condiciones[-1] = "(titulo_busqueda like %s or id = %s)"
            params.append(clave)
    if not todas:
        condiciones.append("lower(coalesce(estado, '')) <> all(%s)")
        params.append(list(ESTADOS_INACTIVOS))
    where = ("where " + " and ".join(condiciones)) if condiciones else ""
    filas = db.todas(conn, f"""
        select id, titulo, estado, modificada from alchemer_encuesta {where}
         order by modificada desc nulls last, id desc limit %s
    """, (*params, int(limite) + 1))
    total = db.una(conn, "select count(*)::int as n from alchemer_encuesta")["n"]
    return {"items": [{"id": f["id"], "titulo": f["titulo"], "estado": f["estado"],
                       "modificada": f["modificada"].isoformat() if f["modificada"] else None}
                      for f in filas[:int(limite)]],
            "hay_mas": len(filas) > int(limite), "en_catalogo": total}


# ── El estado de la sincronización ────────────────────────────────────

def _serializar(conn, fila):
    if not fila:
        return None
    paginas = db.una(conn, """
        select count(*) filter (where estado = 'procesada')::int as procesadas,
               count(*) filter (where estado = 'fallida')::int   as fallidas,
               count(*)::int                                      as conocidas,
               max(procesada_en)                                  as ultima_pagina
          from alchemer_sincronizacion_pagina where sincronizacion_id = %s
    """, (fila["id"],))
    total = fila["paginas_total"]
    ultima = max(x for x in (paginas["ultima_pagina"], fila["reanudada_en"],
                             fila["iniciada_en"]) if x is not None)
    estancada = (fila["estado"] == EN_CURSO and ultima is not None
                 and ultima < datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(minutes=ESTANCADA_MINUTOS))
    return {
        "id": fila["id"], "modo": fila["modo"], "estado": fila["estado"],
        "filtro": fila["filtro"],
        "desde": fila["desde"].isoformat() if fila["desde"] else None,
        "paginas_total": total, "paginas_procesadas": paginas["procesadas"],
        "paginas_fallidas": paginas["fallidas"],
        # El avance se deriva de las páginas. Sin la primera no se sabe el total.
        "avance": (paginas["procesadas"] / total) if total else 0.0,
        "iniciada_en": fila["iniciada_en"].isoformat(),
        "terminada_en": fila["terminada_en"].isoformat() if fila["terminada_en"] else None,
        "iniciada_por": fila["iniciada_por"], "eliminadas": fila["eliminadas"],
        "error": fila["error"], "estancada": bool(estancada),
    }


def _ultima(conn, condicion="true"):
    fila = db.una(conn, f"""select * from alchemer_sincronizacion where {condicion}
                             order by iniciada_en desc, id desc limit 1""")
    return _serializar(conn, fila) if fila else None


def estado(conn):
    """La última sincronización (con su avance) y la última terminada: es lo
    que la pantalla muestra junto a «Actualizar»."""
    return {"ultima": _ultima(conn),
            "ultima_terminada": _ultima(conn, "estado = 'terminada'"),
            "en_catalogo": db.una(conn, "select count(*)::int as n from alchemer_encuesta")["n"]}


# ── Lanzar ────────────────────────────────────────────────────────────

def _encolar(encolador, sincronizacion_id, paginas, intento=0):
    for pagina in paginas:
        encolador.encolar(sincronizacion_id, pagina, intento)


def lanzar(conn, actor=None, encolador=None, entorno=None, forzar_completa=False):
    """R-ALB.2 — pide una sincronización. Si hay una en curso la devuelve (y
    si está estancada, la reanuda). Hace `commit`: la tarea tiene que
    encontrar la fila."""
    alchemer._exigir_configuracion(entorno)
    encolador = encolador or crear_encolador()
    actual = _ultima(conn, "estado = 'en_curso'")
    if actual:
        if actual["estancada"]:
            return {**reanudar(conn, actual["id"], encolador), "reanudada": True}
        return {**actual, "ya_en_curso": True}

    desde = None
    hubo_completa = db.una(conn, """select 1 as hay from alchemer_sincronizacion
                                     where estado = 'terminada' limit 1""")
    if hubo_completa and not forzar_completa:
        ultima = db.una(conn, "select max(modificada) as m from alchemer_encuesta")["m"]
        desde = (ultima - MARGEN_INCREMENTAL) if ultima else None
    modo = INCREMENTAL if desde else COMPLETA
    try:
        with conn.transaction():
            fila = db.una(conn, """
                insert into alchemer_sincronizacion (modo, desde, iniciada_por)
                     values (%s, %s, %s) returning id
            """, (modo, desde, getattr(actor, "email", None)))
            db.ejecutar(conn, """insert into alchemer_sincronizacion_pagina
                                        (sincronizacion_id, pagina) values (%s, 1)""",
                        (fila["id"],))
    except Exception as error:  # noqa: BLE001
        if INDICE_UNA_EN_CURSO in str(error):
            conn.rollback()
            return {**_ultima(conn, "estado = 'en_curso'"), "ya_en_curso": True}
        raise
    conn.commit()
    try:
        _encolar(encolador, fila["id"], [1])
    except Exception as error:  # noqa: BLE001
        db.ejecutar(conn, """update alchemer_sincronizacion
                                set estado = 'fallida', terminada_en = now(), error = %s
                              where id = %s""",
                    (f"No se pudo encolar: {getattr(error, 'mensaje', None) or error}"[:500],
                     fila["id"]))
        conn.commit()
        raise Conflicto("No se pudo encolar la sincronización del catálogo de Alchemer.",
                        {"motivo": "sin_cola"}) from None
    return _ultima(conn, f"id = {int(fila['id'])}")


def reanudar(conn, sincronizacion_id, encolador=None):
    """Vuelve a encolar las páginas pendientes de una sincronización
    estancada. Es seguro: procesar una página dos veces la vuelve a guardar
    igual."""
    encolador = encolador or crear_encolador()
    pendientes = [f["pagina"] for f in db.todas(conn, """
        select pagina from alchemer_sincronizacion_pagina
         where sincronizacion_id = %s and estado = 'pendiente' order by pagina
    """, (sincronizacion_id,))]
    intento = int(datetime.datetime.now().timestamp())
    _encolar(encolador, sincronizacion_id, pendientes, intento)
    # La reanudación cuenta como actividad: no vuelve a estar estancada en
    # el acto.
    db.ejecutar(conn, """update alchemer_sincronizacion set reanudada_en = now()
                          where id = %s and estado = 'en_curso'""", (sincronizacion_id,))
    conn.commit()
    return {**_ultima(conn, f"id = {int(sincronizacion_id)}"),
            "paginas_reencoladas": len(pendientes)}


# ── Procesar una página (la tarea) ────────────────────────────────────

def _pedir_pagina(datos, pagina, desde, pedir):
    consulta = {"page": pagina, "resultsperpage": alchemer.POR_PAGINA}
    if desde:
        # R-ALB.4 — el filtro por fecha de modificación. Se comprueba la
        # respuesta: no se confía en que Alchemer lo aplique.
        consulta.update({"filter[field][0]": "modified_on", "filter[operator][0]": ">=",
                         "filter[value][0]": desde.strftime("%Y-%m-%d %H:%M:%S")})
    return pedir(alchemer._url(datos, "survey", **consulta))


def _transitorio(error):
    detalle = getattr(error, "detalle", None) or {}
    return (detalle.get("motivo") in ("alchemer_tiempo", "alchemer_red")
            or int(detalle.get("status") or 0) >= 500 or detalle.get("status") == 429)


def _pasar_a_completa(conn, sincronizacion_id, motivo):
    """El filtro no sirvió: la corrida recorre todo desde la página 1."""
    db.ejecutar(conn, """update alchemer_sincronizacion
                            set modo = 'completa', filtro = %s where id = %s""",
                (motivo, sincronizacion_id))


def procesar_pagina(conn, sincronizacion_id, pagina, encolador=None, entorno=None,
                    pedir=None):
    """Una página de la API, una llamada. Lo transitorio (tiempo, red, 5xx,
    429) se propaga para que Cloud Tasks reintente; lo demás deja la página
    `fallida` y la corrida sigue."""
    pedir = pedir or alchemer._pedir
    sinc = db.una(conn, "select * from alchemer_sincronizacion where id = %s",
                  (sincronizacion_id,))
    if not sinc or sinc["estado"] != EN_CURSO:
        return {"estado": "descartada"}
    fila_pagina = db.una(conn, """select estado from alchemer_sincronizacion_pagina
                                   where sincronizacion_id = %s and pagina = %s""",
                         (sincronizacion_id, pagina))
    if not fila_pagina or fila_pagina["estado"] == "procesada":
        return {"estado": "ya_procesada" if fila_pagina else "descartada"}
    datos = alchemer._exigir_configuracion(entorno)
    desde = sinc["desde"] if sinc["modo"] == INCREMENTAL else None

    try:
        respuesta = _pedir_pagina(datos, pagina, desde, pedir)
    except Conflicto as error:
        if _transitorio(error):
            raise
        if desde and pagina == 1:
            # Alchemer rechazó el filtro: se recorre todo.
            _pasar_a_completa(conn, sincronizacion_id, "rechazado")
            conn.commit()
            return procesar_pagina(conn, sincronizacion_id, pagina, encolador, entorno, pedir)
        return _fallo(conn, sincronizacion_id, pagina, error)

    crudas = [c for c in (respuesta.get("data") or []) if isinstance(c, dict)]
    filas = [_de_la_api(c) for c in crudas if c.get("id") is not None]
    if desde and pagina == 1:
        # R-ALB.4 — ¿lo aplicó? Una encuesta anterior a `desde`, o sin
        # fecha, quiere decir que no.
        if any(f["modificada"] is None or f["modificada"] < desde for f in filas):
            _pasar_a_completa(conn, sincronizacion_id, "ignorado")
            conn.commit()
            return procesar_pagina(conn, sincronizacion_id, pagina, encolador, entorno, pedir)
        db.ejecutar(conn, "update alchemer_sincronizacion set filtro = 'aplicado' where id = %s",
                    (sincronizacion_id,))

    _guardar(conn, filas, sincronizacion_id)
    nuevas = []
    if pagina == 1:
        total = max(1, int(respuesta.get("total_pages") or 1))
        db.ejecutar(conn, "update alchemer_sincronizacion set paginas_total = %s where id = %s",
                    (total, sincronizacion_id))
        for p in range(2, total + 1):
            if db.ejecutar(conn, """insert into alchemer_sincronizacion_pagina
                                           (sincronizacion_id, pagina) values (%s, %s)
                                    on conflict do nothing""", (sincronizacion_id, p)):
                nuevas.append(p)
    db.ejecutar(conn, """update alchemer_sincronizacion_pagina
                            set estado = 'procesada', encuestas = %s, error = null,
                                procesada_en = now()
                          where sincronizacion_id = %s and pagina = %s""",
                (len(filas), sincronizacion_id, pagina))
    conn.commit()
    if nuevas:
        _encolar(encolador or crear_encolador(), sincronizacion_id, nuevas)
    _cerrar_si_termino(conn, sincronizacion_id)
    return {"estado": "procesada", "encuestas": len(filas), "encoladas": len(nuevas)}


def _fallo(conn, sincronizacion_id, pagina, error):
    db.ejecutar(conn, """update alchemer_sincronizacion_pagina
                            set estado = 'fallida', error = %s, procesada_en = now()
                          where sincronizacion_id = %s and pagina = %s""",
                ((getattr(error, "mensaje", None) or str(error))[:500], sincronizacion_id, pagina))
    conn.commit()
    _cerrar_si_termino(conn, sincronizacion_id)
    return {"estado": "fallida"}


def _cerrar_si_termino(conn, sincronizacion_id):
    """Con todas las páginas resueltas, la cierra. Una sola tarea la cierra:
    se toma la fila con `for update`. Una completa sin fallas borra del
    catálogo lo que no vio (encuestas que ya no existen en Alchemer)."""
    sinc = db.una(conn, """select * from alchemer_sincronizacion
                            where id = %s for update""", (sincronizacion_id,))
    if not sinc or sinc["estado"] != EN_CURSO or not sinc["paginas_total"]:
        conn.commit()
        return
    paginas = db.una(conn, """
        select count(*) filter (where estado = 'pendiente')::int as pendientes,
               count(*) filter (where estado = 'fallida')::int   as fallidas,
               count(*)::int                                      as total
          from alchemer_sincronizacion_pagina where sincronizacion_id = %s
    """, (sincronizacion_id,))
    if paginas["pendientes"] or paginas["total"] < sinc["paginas_total"]:
        conn.commit()
        return
    eliminadas = 0
    if not paginas["fallidas"] and sinc["modo"] == COMPLETA:
        eliminadas = db.ejecutar(conn, """delete from alchemer_encuesta
                                           where vista_en is distinct from %s""",
                                 (sincronizacion_id,))
    db.ejecutar(conn, """
        update alchemer_sincronizacion
           set estado = %s, terminada_en = now(), eliminadas = %s,
               error = case when %s > 0 then %s || ' página(s) fallaron.' else null end
         where id = %s
    """, (FALLIDA if paginas["fallidas"] else TERMINADA, eliminadas,
          paginas["fallidas"], str(paginas["fallidas"]), sincronizacion_id))
    conn.commit()


def exigir_texto(texto):
    if not str(texto or "").strip():
        raise DatosInvalidos("Pegá la URL o el id de la encuesta de Alchemer.",
                             {"campo": "texto"})
    return str(texto).strip()
