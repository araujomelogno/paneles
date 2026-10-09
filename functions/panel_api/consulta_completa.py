"""R-CS · cambio 5 — la consulta de alcance completo.

La exploratoria verifica las mejores `top_k` personas, dentro de la request.
La completa verifica **todas las unidades de evidencia elegibles** —todas
las respuestas distintas de las personas habilitadas, opcionalmente hasta
una distancia—, y eso no entra en una request: sobre el corpus actual son
miles de unidades y cientos de llamadas a Claude por criterio (A2).

── Cómo corre ──

Es el patrón de la ingesta diferida (`diferida.py`, boveda/0019), reutilizado
y no reescrito en otro idioma (A3):

1. **Estimar** (`estimar`): se embeben los criterios, se cuentan las
   unidades elegibles y se calcula el costo. No se crea nada. Es lo que la
   pantalla muestra antes de confirmar.
2. **Lanzar** (`lanzar`): exige permiso propio (`consulta_completa`),
   presupuesto y confirmación. Si la estimación supera el presupuesto, no
   se lanza. El plan —qué unidades, en qué lotes— **se congela** al
   confirmar y las tareas no lo reinterpretan.
3. **Cada tarea verifica un lote** (`procesar_lote`): antes de llamar a
   Claude suma lo gastado **sobre los lotes** (no un acumulador) y si el
   lote no entra en el presupuesto, no llama: queda `omitido` y la
   ejecución termina `detenida_por_presupuesto`. El gate de consentimiento
   se re-evalúa en cada lote. Los veredictos se escriben con upsert en el
   store semántico (`veredicto_unidad`), así que un reintento es seguro.
4. **Leer** (`resultado`): las personas se arman **al leer**, con el gate
   de hoy, a partir de los veredictos por unidad, con la misma regla de
   agregación que la exploratoria (`consultas.hallazgo_de` y
   `consultas._combinar`). Paginado: `limite` es el tamaño de página, y no
   tiene nada que ver con cuánto se verificó.

── Lo que no se hace ──

* **No se guarda el resultado.** Ni la lista de personas ni sus evidencias:
  un resultado persistido envejece mal y saltea el gate (P1). Lo que queda
  son veredictos sobre textos, que se vencen a los 30 días.
* **No hay excepción por presupuesto.** Una ejecución que llega al tope se
  detiene; para seguir hay que ampliar el presupuesto a propósito
  (`reintentar` con `presupuesto_usd`), dentro del máximo configurado.
* **Lo que no se verificó no se presenta como juzgado.** Una unidad de un
  lote fallido u omitido es `sin_verificar`, con su motivo: no excluye ni
  aprueba (D66).
"""

import json
import math
import os
import time
import uuid

from . import (
    consentimiento,
    consultas,
    costo_consulta,
    db,
    demografia,
    diferida,
    embeddings as mod_embeddings,
    semantica,
    verificacion as mod_verificacion,
)
from .errores import Conflicto, DatosInvalidos, NoEncontrado

# ── Parámetros ───────────────────────────────────────────────────────

UNIDADES_POR_LOTE = int(os.environ.get("CONSULTA_UNIDADES_POR_LOTE", "100"))
"""Unidades por tarea. Con lotes de verificación de 25 son cuatro llamadas a
Claude: pocas para que un reintento rehaga poco, bastantes para que la
cola no se llene de tareas mínimas."""

PRESUPUESTO_MAXIMO_POR_DEFECTO = 25.0
SEGUNDOS_POR_LOTE = 900
"""Tiempo de verificación de una tarea. La función de tareas tiene 1800 s;
la mitad deja lugar al rerank, a la escritura y a un reintento interno."""

ENCOLADA, PROCESANDO = "encolada", "procesando"
TERMINADA, TERMINADA_CON_ERRORES = "terminada", "terminada_con_errores"
DETENIDA, CANCELADA, FALLIDA = "detenida_por_presupuesto", "cancelada", "fallida"
TERMINALES = (TERMINADA, TERMINADA_CON_ERRORES, DETENIDA, CANCELADA, FALLIDA)

LOTE_PENDIENTE, LOTE_PROCESANDO, LOTE_OK = "pendiente", "procesando", "ok"
LOTE_FALLIDO, LOTE_OMITIDO = "fallido", "omitido"

NO_PROCESADO = "lote_no_procesado"
"""El `fallo` de una unidad cuyo lote falló, se omitió o no corrió todavía."""


def presupuesto_maximo(entorno=None):
    """El techo por ejecución, por configuración. Ninguna completa lo pasa,
    ni siquiera ampliando el presupuesto de una detenida."""
    entorno = os.environ if entorno is None else entorno
    return costo_consulta._float(entorno.get("CONSULTA_PRESUPUESTO_MAXIMO_USD", ""),
                                 PRESUPUESTO_MAXIMO_POR_DEFECTO)


def _tam_lote_verificacion(verificador):
    return getattr(verificador, "tam_lote", None) or mod_verificacion.TAM_LOTE_POR_DEFECTO


# ── El encolador: el de la ingesta, con otra cola ────────────────────

def crear_encolador():
    """La misma interfaz que `diferida`, apuntando a la función
    `procesarconsulta`. La cola la crea el CLI con el id de la función, igual
    que `procesaringesta`."""
    if (os.environ.get("ENCOLADOR_TAREAS") or "").lower() in ("memoria", "test"):
        return diferida.EncoladorEnMemoria()
    return diferida.EncoladorCloudTasks(
        cola=os.environ.get("TAREAS_CONSULTA_COLA", "procesarconsulta"),
        url=os.environ.get("TAREAS_CONSULTA_URL", ""),
        prefijo="consulta")


# ════════════════════════════════════════════════════════════════════
#  1 · Estimar
# ════════════════════════════════════════════════════════════════════

def _ids_habilitados(conn_boveda, definicion):
    """Las personas que pueden aportar evidencia: el segmento demográfico
    (si hay) con `uso_semantico` vigente. Se recalcula en cada lote y al
    leer: quien retira el consentimiento a mitad de camino deja de contar."""
    demograficos = [c for c in definicion["criterios"] if c["tipo"] == "demografico"]
    return demografia.ids_del_segmento(
        conn_boveda, demograficos, panel_id=definicion["panel_id"],
        finalidad=consentimiento.SEMANTICO)


def _plan(ctx, definicion):
    """Por criterio semántico, sus unidades elegibles. Es caro en cómputo de
    base (fuerza bruta sobre el corpus de las personas habilitadas) y barato
    en dinero (un embedding por criterio)."""
    semanticos = [c for c in definicion["criterios"] if c["tipo"] == "semantico"]
    if not semanticos:
        raise DatosInvalidos(
            "Una consulta completa necesita al menos un criterio semántico: "
            "la demográfica ya es exhaustiva.")
    ids = _ids_habilitados(ctx.boveda, definicion)
    tipo = mod_embeddings.tipo_de_criterio(pedido=definicion.get("tipo_embedding_criterio"))
    vectores = ctx.embeddings.embeber_criterio([c["texto"] for c in semanticos], tipo=tipo)
    plan = []
    for criterio, vector in zip(semanticos, vectores):
        unidades = semantica.unidades_elegibles(
            ctx.semantica, vector, ids, definicion.get("distancia_maxima"))
        plan.append({"criterio": criterio, "unidades": unidades})
    return plan, len(ids), tipo


def _estimacion(plan, tam_lote, conn_boveda):
    return costo_consulta.estimar(
        [{"criterio": p["criterio"]["texto"],
          "unidades": len(p["unidades"]),
          "tokens_textos": sum(math.ceil(u["largo"] / costo_consulta.CARACTERES_POR_TOKEN)
                               for u in p["unidades"])}
         for p in plan],
        tam_lote, calibrado=costo_consulta.calibracion(conn_boveda))


def estimar(ctx, definicion_cruda, verificador=None):
    """Lo que costaría la completa, y la cota de la exploratoria al lado.
    No crea nada ni llama a Claude."""
    definicion = consultas.normalizar_definicion(
        {**(definicion_cruda or {}), "alcance": consultas.COMPLETO}, ctx.boveda)
    verificador = verificador or ctx.verificador
    tam_lote = _tam_lote_verificacion(verificador)
    plan, personas, tipo = _plan(ctx, definicion)
    estimacion = _estimacion(plan, tam_lote, ctx.boveda)
    exploratoria = costo_consulta.estimar_exploratoria(
        [p["criterio"]["texto"] for p in plan], definicion["top_k"],
        consultas.MAX_EVIDENCIAS_POR_INDIVIDUO, tam_lote)
    maximo = presupuesto_maximo()
    return {
        "alcance": consultas.COMPLETO,
        "solo_estimar": True,
        "personas_habilitadas": personas,
        "tipo_embedding_criterio": tipo,
        "estimacion": estimacion,
        "exploratoria": exploratoria,
        "presupuesto_maximo_usd": maximo,
        "presupuesto_sugerido_usd": min(maximo, max(0.01, round(estimacion["usd"] * 1.2, 2))),
        "lotes": sum(math.ceil(len(p["unidades"]) / UNIDADES_POR_LOTE) for p in plan),
        "excede_el_maximo": estimacion["usd"] > maximo,
    }


# ════════════════════════════════════════════════════════════════════
#  2 · Lanzar
# ════════════════════════════════════════════════════════════════════

def lanzar(ctx, definicion_cruda, actor=None, encolador=None, verificador=None):
    """Congela el plan, lo parte en lotes y los encola. No verifica nada."""
    cruda = definicion_cruda or {}
    definicion = consultas.normalizar_definicion(
        {**cruda, "alcance": consultas.COMPLETO}, ctx.boveda)
    presupuesto = definicion["presupuesto_usd"]
    maximo = presupuesto_maximo()
    if presupuesto is None:
        raise DatosInvalidos(
            "Una consulta completa necesita `presupuesto_usd`: es lo que la "
            "frena sola si se lanzó sobre más de lo que se quería.")
    if presupuesto > maximo:
        raise DatosInvalidos(
            f"El presupuesto (US$ {presupuesto:.2f}) supera el máximo por "
            f"ejecución (US$ {maximo:.2f}, `CONSULTA_PRESUPUESTO_MAXIMO_USD`).",
            {"presupuesto_maximo_usd": maximo})
    if not cruda.get("confirmar_costo"):
        raise DatosInvalidos(
            "Falta confirmar el costo (`confirmar_costo: true`). Pedí antes la "
            "estimación con `solo_estimar: true`.")

    verificador = verificador or ctx.verificador
    tam_lote = _tam_lote_verificacion(verificador)
    plan, personas, tipo = _plan(ctx, definicion)
    estimacion = _estimacion(plan, tam_lote, ctx.boveda)
    if estimacion["usd"] > presupuesto:
        raise Conflicto(
            f"La estimación (US$ {estimacion['usd']:.2f}) supera el presupuesto "
            f"(US$ {presupuesto:.2f}). Subí el presupuesto, acotá la consulta "
            f"(panel, criterios demográficos, distancia máxima) o usá la "
            f"exploratoria.", {"estimacion": estimacion})

    ejecucion_id = str(uuid.uuid4())
    lotes = []
    for parte in plan:
        unidades = [[u["pregunta_id"], u["hash_texto"], round(u["distancia"], 6)]
                    for u in parte["unidades"]]
        for inicio in range(0, len(unidades), UNIDADES_POR_LOTE):
            lotes.append((parte["criterio"]["orden"], unidades[inicio:inicio + UNIDADES_POR_LOTE]))

    definicion_guardada = {**definicion, "tipo_embedding_criterio_efectivo": tipo}
    db.ejecutar(
        ctx.boveda,
        """insert into consulta_ejecucion
                  (id, alcance, modo, tipo, definicion, estado, presupuesto_usd,
                   costo_estimado, lotes_total, unidades_total, creado_por,
                   diagnostico)
           values (%s, 'completo', %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s,
                   %s::jsonb)""",
        (ejecucion_id, definicion["modo"],
         "mixta" if any(c["tipo"] == "demografico" for c in definicion["criterios"])
         else "semantica",
         json.dumps(definicion_guardada, ensure_ascii=False, default=str),
         ENCOLADA if lotes else TERMINADA, presupuesto,
         json.dumps(estimacion, default=str), len(lotes),
         sum(len(u) for _, u in lotes), actor,
         json.dumps({"personas_habilitadas_al_lanzar": personas,
                     "tipo_embedding_criterio": tipo}, default=str)))
    for indice, (orden, unidades) in enumerate(lotes):
        db.ejecutar(
            ctx.boveda,
            """insert into consulta_lote
                      (ejecucion_id, indice, criterio_orden, unidades, unidades_total)
               values (%s, %s, %s, %s::jsonb, %s)""",
            (ejecucion_id, indice, orden, json.dumps(unidades), len(unidades)))
    if not lotes:
        db.ejecutar(ctx.boveda,
                    "update consulta_ejecucion set terminado_en = now() where id = %s",
                    (ejecucion_id,))
    # Como en la ingesta diferida: commit **antes** de encolar. Una tarea que
    # arranca antes de que su lote esté guardado no encuentra nada.
    ctx.boveda.commit()

    encolador = encolador or crear_encolador()
    sin_encolar = []
    for indice in range(len(lotes)):
        try:
            encolador.encolar(ejecucion_id, indice)
        except Exception as error:  # noqa: BLE001
            sin_encolar.append({"indice": indice, "error": str(error)})
    salida = estado(ctx.boveda, ejecucion_id)
    salida["estimacion"] = estimacion
    if sin_encolar:
        salida["sin_encolar"] = sin_encolar
        salida["aviso"] = (
            f"{len(sin_encolar)} lote(s) no se pudieron encolar. La ejecución "
            f"quedó guardada: se los puede reintentar.")
    return salida


# ════════════════════════════════════════════════════════════════════
#  3 · Procesar un lote (lo que corre la tarea)
# ════════════════════════════════════════════════════════════════════

def _ejecucion(conn, ejecucion_id):
    fila = db.una(conn, "select * from consulta_ejecucion where id = %s",
                  (str(ejecucion_id),))
    if not fila:
        raise NoEncontrado(f"No existe la ejecución {ejecucion_id}.")
    return fila


def _tomar_lote(conn, ejecucion_id, indice):
    """El `update … where estado in (…)` hace segura la entrega duplicada:
    de dos tareas sobre el mismo lote, solo una gana la fila."""
    fila = db.una(
        conn,
        """update consulta_lote
              set estado = 'procesando', intentos = intentos + 1,
                  actualizado_en = now(), error = null, reintentable = null
            where ejecucion_id = %s and indice = %s
              and estado in ('pendiente', 'fallido')
        returning id, criterio_orden, unidades, unidades_total, intentos""",
        (str(ejecucion_id), indice))
    if fila:
        db.ejecutar(
            conn,
            """update consulta_ejecucion
                  set estado = 'procesando', terminado_en = null
                where id = %s and estado in ('encolada', 'terminada_con_errores',
                                             'fallida', 'detenida_por_presupuesto')""",
            (str(ejecucion_id),))
    conn.commit()
    return fila


def _gastado(conn, ejecucion_id):
    fila = db.una(conn, "select costo_lotes_usd from v_consulta_progreso "
                        "where ejecucion_id = %s", (str(ejecucion_id),))
    return float((fila or {}).get("costo_lotes_usd") or 0)


def _marcar(conn, ejecucion_id, indice, estado_lote, error=None, reintentable=None):
    db.ejecutar(
        conn,
        """update consulta_lote
              set estado = %s, error = %s, reintentable = %s, actualizado_en = now()
            where ejecucion_id = %s and indice = %s""",
        (estado_lote, (error or "")[:4000] or None, reintentable,
         str(ejecucion_id), indice))
    conn.commit()


def procesar_lote(conn_boveda, conn_semantica, ejecucion_id, indice,
                  reranker=None, verificador=None, tarifas=None):
    """Verifica un lote. Como en `diferida.procesar_lote`: un error de datos
    no se propaga (reintentar no lo arregla) y uno transitorio sí (el 5xx es
    lo que le pide a Cloud Tasks que reintente)."""
    ejecucion = _ejecucion(conn_boveda, ejecucion_id)
    lote = _tomar_lote(conn_boveda, ejecucion_id, indice)
    if lote is None:
        return {"estado": "ya_tomado", "ejecucion_id": str(ejecucion_id), "indice": indice}
    if ejecucion["estado"] == CANCELADA:
        _marcar(conn_boveda, ejecucion_id, indice, LOTE_OMITIDO, "Ejecución cancelada.")
        return {"estado": LOTE_OMITIDO, "cierre": _cerrar_si_termino(conn_boveda, ejecucion_id)}

    definicion = ejecucion["definicion"]
    criterio = next(c for c in definicion["criterios"]
                    if c["tipo"] == "semantico" and c["orden"] == lote["criterio_orden"])
    tarifas = tarifas or costo_consulta.Tarifas.desde_entorno()

    # A2 — el presupuesto se mira ANTES de gastar, sobre la suma de los
    # lotes. Con tareas en paralelo dos lotes pueden pasar el control a la
    # vez: el desvío posible es de a lo sumo un lote por tarea concurrente,
    # y por eso el lote es chico y la concurrencia de la cola está acotada.
    verificador = verificador or mod_verificacion.crear()
    previsto = costo_consulta.estimar_criterio(
        criterio["texto"], lote["unidades_total"], _tam_lote_verificacion(verificador),
        tarifas, calibrado=costo_consulta.calibracion(conn_boveda))["usd"]
    gastado = _gastado(conn_boveda, ejecucion_id)
    if gastado + previsto * costo_consulta.MARGEN > float(ejecucion["presupuesto_usd"]):
        _marcar(conn_boveda, ejecucion_id, indice, LOTE_OMITIDO,
                f"Presupuesto: gastado US$ {gastado:.4f}, este lote ~US$ "
                f"{previsto:.4f}, tope US$ {float(ejecucion['presupuesto_usd']):.4f}.")
        return {"estado": LOTE_OMITIDO, "cierre": _cerrar_si_termino(conn_boveda, ejecucion_id)}

    try:
        resultado = _verificar_lote(conn_boveda, conn_semantica, ejecucion, lote,
                                    criterio, reranker, verificador, tarifas)
    except Exception as error:  # noqa: BLE001
        conn_semantica.rollback()
        conn_boveda.rollback()
        reintentable = not isinstance(error, DatosInvalidos)
        _marcar(conn_boveda, ejecucion_id, indice, LOTE_FALLIDO, str(error), reintentable)
        cierre = _cerrar_si_termino(conn_boveda, ejecucion_id)
        if reintentable:
            raise
        return {"estado": LOTE_FALLIDO, "error": str(error), "cierre": cierre}

    db.ejecutar(
        conn_boveda,
        """update consulta_lote
              set estado = 'ok', llamadas = %s, tokens_entrada = %s,
                  tokens_salida = %s, tokens_rerank = %s, costo_usd = %s,
                  resultado = %s::jsonb, error = null, reintentable = null,
                  actualizado_en = now()
            where ejecucion_id = %s and indice = %s""",
        (resultado["llamadas"], resultado["tokens_entrada"], resultado["tokens_salida"],
         resultado["tokens_rerank"], resultado["costo_usd"],
         json.dumps(resultado, default=str), str(ejecucion_id), indice))
    conn_boveda.commit()
    return {"estado": LOTE_OK, "resultado": resultado,
            "cierre": _cerrar_si_termino(conn_boveda, ejecucion_id)}


def _verificar_lote(conn_boveda, conn_semantica, ejecucion, lote, criterio,
                    reranker, verificador, tarifas):
    from . import reranker as mod_reranker

    reranker = reranker or mod_reranker.crear()
    distancia = {(u[0], u[1]): u[2] for u in lote["unidades"]}
    # El gate, re-evaluado en cada lote.
    ids = _ids_habilitados(conn_boveda, ejecucion["definicion"])
    representantes = semantica.respuestas_de_unidades(
        conn_semantica, list(distancia), ids, solo_representante=True)
    sin_miembros = len(distancia) - len(representantes)

    degradaciones = []
    relevancias = {}
    tokens_rerank = getattr(reranker, "tokens", 0) or 0
    if representantes:
        unidades = [{"representante": r} for r in representantes]
        relevancias = consultas._rerankear_unidades(
            criterio["texto"], criterio["etiqueta"], unidades, reranker, degradaciones)
    tokens_rerank = (getattr(reranker, "tokens", 0) or 0) - tokens_rerank

    juicios, informe = consultas._verificar_unidades(
        {"limite": time.monotonic() + SEGUNDOS_POR_LOTE, "captura": None},
        criterio["texto"], criterio["etiqueta"], representantes, verificador,
        degradaciones)
    filas = []
    for i, (rep, juicio) in enumerate(zip(representantes, juicios)):
        filas.append({
            "pregunta_id": rep["pregunta_id"], "hash_texto": rep["hash_texto"],
            "veredicto": juicio["veredicto"], "razon": juicio.get("razon"),
            "fallo": juicio.get("fallo"),
            "aviso_polaridad": juicio.get("aviso_polaridad"),
            "relevancia": relevancias.get(i),
            "distancia": distancia.get((rep["pregunta_id"], rep["hash_texto"])),
        })
    semantica.guardar_veredictos(conn_semantica, ejecucion["id"], criterio["orden"], filas)
    conn_semantica.commit()

    entrada, salida = informe["tokens"]["entrada"], informe["tokens"]["salida"]
    return {
        "unidades": len(distancia),
        "verificadas": informe["verificadas"],
        "sin_verificar": informe["sin_verificar"],
        "sin_miembros_habilitados": sin_miembros,
        "veredictos": informe["veredictos"],
        "por_fallo": informe["por_fallo"],
        "llamadas": informe["llamadas"],
        "tokens_entrada": entrada,
        "tokens_salida": salida,
        "tokens_rerank": tokens_rerank,
        "costo_usd": costo_consulta.costo(tarifas, entrada, salida, tokens_rerank),
        "degradaciones": degradaciones,
        "reranker": reranker.nombre,
        "verificador": verificador.nombre,
    }


def _cerrar_si_termino(conn, ejecucion_id):
    """Lo llama cada tarea al terminar; el `update` condicional hace que
    cierre una sola."""
    fila = db.una(conn, "select * from v_consulta_progreso where ejecucion_id = %s",
                  (str(ejecucion_id),))
    if not fila or fila["lotes_pendientes"] or fila["lotes_en_curso"]:
        return None
    if fila["estado"] == CANCELADA:
        nuevo = CANCELADA
    elif fila["lotes_omitidos"]:
        nuevo = DETENIDA
    elif fila["lotes_ok"] == fila["lotes_total"]:
        nuevo = TERMINADA
    elif fila["lotes_ok"]:
        nuevo = TERMINADA_CON_ERRORES
    else:
        nuevo = FALLIDA
    db.ejecutar(
        conn,
        """update consulta_ejecucion
              set estado = %s, costo_real_usd = %s,
                  terminado_en = coalesce(terminado_en, now())
            where id = %s and (estado not in ('terminada', 'terminada_con_errores',
                                              'detenida_por_presupuesto', 'fallida')
                               or estado = 'cancelada')""",
        (nuevo, fila["costo_lotes_usd"], str(ejecucion_id)))
    conn.commit()
    return {"estado": nuevo}


# ════════════════════════════════════════════════════════════════════
#  4 · Estado, resultado, reintentar, cancelar
# ════════════════════════════════════════════════════════════════════

def estado(conn, ejecucion_id):
    """El avance y el gasto. Sale de la base: sobrevive a cerrar la pestaña."""
    fila = db.una(conn, "select * from v_consulta_progreso where ejecucion_id = %s",
                  (str(ejecucion_id),))
    if not fila:
        raise NoEncontrado(f"No existe la ejecución {ejecucion_id}.")
    extra = db.una(conn, "select definicion, costo_estimado, costo_real_usd, diagnostico "
                         "  from consulta_ejecucion where id = %s", (str(ejecucion_id),))
    hechos = fila["lotes_ok"] + fila["lotes_fallidos"] + fila["lotes_omitidos"]
    salida = {
        "ejecucion_id": str(fila["ejecucion_id"]),
        "alcance": fila["alcance"],
        "modo": fila["modo"],
        "tipo": fila["tipo"],
        "estado": fila["estado"],
        "estado_etiqueta": fila["estado_etiqueta"],
        "terminal": fila["terminal"],
        "lotes_total": fila["lotes_total"],
        "lotes_ok": fila["lotes_ok"],
        "lotes_fallidos": fila["lotes_fallidos"],
        "lotes_omitidos": fila["lotes_omitidos"],
        "lotes_en_curso": fila["lotes_en_curso"],
        "lotes_pendientes": fila["lotes_pendientes"],
        "unidades_total": fila["unidades_total"],
        "unidades_verificadas": int(fila["unidades_verificadas"]),
        "porcentaje": round(100 * hechos / fila["lotes_total"]) if fila["lotes_total"] else 100,
        "presupuesto_usd": float(fila["presupuesto_usd"]) if fila["presupuesto_usd"] is not None else None,
        "costo_real_usd": float(fila["costo_lotes_usd"] or 0) if fila["alcance"] == consultas.COMPLETO
        else float(extra["costo_real_usd"] or 0),
        "costo_estimado": extra["costo_estimado"],
        "tokens": {"entrada": int(fila["tokens_entrada"]), "salida": int(fila["tokens_salida"])},
        "llamadas": int(fila["llamadas"]),
        "criterios": [c.get("etiqueta") for c in (extra["definicion"] or {}).get("criterios", [])],
        "creado_por": fila["creado_por"],
        "creado_en": fila["creado_en"].isoformat(),
        "terminado_en": fila["terminado_en"].isoformat() if fila["terminado_en"] else None,
        "segundos_transcurridos": fila["segundos_transcurridos"],
    }
    if hechos and not fila["terminal"] and fila["lotes_total"]:
        salida["segundos_restantes"] = int(
            fila["segundos_transcurridos"] / hechos * (fila["lotes_total"] - hechos))
    salida["problemas"] = [
        {**f, "actualizado_en": f["actualizado_en"].isoformat()}
        for f in db.todas(
            conn,
            """select indice, criterio_orden, estado, intentos, error, reintentable,
                      actualizado_en
                 from consulta_lote
                where ejecucion_id = %s and estado in ('fallido', 'omitido')
                order by indice""", (str(ejecucion_id),))
    ]
    return salida


def listar(conn, limite=20):
    filas = db.todas(conn, "select id from consulta_ejecucion where alcance = 'completo' "
                           "order by creado_en desc limit %s", (limite,))
    return [estado(conn, f["id"]) for f in filas]


def _pendiente(motivo):
    return {"veredicto": mod_verificacion.SIN_VERIFICAR, "razon": motivo,
            "fallo": NO_PROCESADO, "aviso_polaridad": False}


def resultado(ctx, ejecucion_id, pagina=1, por_pagina=None, todas=False):
    """Las personas, armadas al leer. Se puede pedir con la ejecución en
    curso: lo que falta figura «sin verificar» y la respuesta lo dice.

    `todas` es para el CSV: el archivo lleva el ranking entero, no la página
    que se está mirando."""
    ejecucion = _ejecucion(ctx.boveda, ejecucion_id)
    if ejecucion["alcance"] != consultas.COMPLETO:
        raise Conflicto("Esa ejecución es exploratoria: su resultado no se "
                        "guarda; volvé a correr la consulta.")
    definicion = ejecucion["definicion"]
    progreso = estado(ctx.boveda, ejecucion_id)
    por_pagina = max(1, min(int(por_pagina or definicion.get("limite")
                                or consultas.LIMITE_POR_DEFECTO), consultas.TOP_K_MAXIMO))
    pagina = max(1, int(pagina or 1))

    ids = _ids_habilitados(ctx.boveda, definicion)
    planificadas = {}
    for fila in db.todas(ctx.boveda,
                         "select criterio_orden, unidades, estado from consulta_lote "
                         "where ejecucion_id = %s", (str(ejecucion_id),)):
        for u in fila["unidades"]:
            planificadas.setdefault(fila["criterio_orden"], {})[(u[0], u[1])] = (
                u[2], fila["estado"])
    juzgadas = semantica.veredictos_de(ctx.semantica, ejecucion_id)

    por_criterio, resumen = {}, []
    for criterio in (c for c in definicion["criterios"] if c["tipo"] == "semantico"):
        orden = criterio["orden"]
        veredictos = juzgadas.get(orden, {})
        plan = planificadas.get(orden, {})
        # Solo hace falta ir a buscar a quién alcanzan las unidades que
        # pueden poner o sacar a alguien: las irrelevantes no aportan nada.
        utiles = [clave for clave in plan
                  if (veredictos.get(clave) or {}).get("veredicto") != mod_verificacion.IRRELEVANTE]
        respuestas = semantica.respuestas_de_unidades(ctx.semantica, utiles, ids)
        por_persona = {}
        for r in respuestas:
            clave = (r["pregunta_id"], r["hash_texto"])
            v = veredictos.get(clave)
            distancia, estado_lote = plan.get(clave, (None, None))
            juicio = ({"veredicto": v["veredicto"], "razon": v["razon"], "fallo": v["fallo"],
                       "aviso_polaridad": v["aviso_polaridad"]} if v
                      else _pendiente("El lote de esta evidencia no se verificó "
                                      f"({estado_lote or 'sin lote'})."))
            candidato = {**r, "distancia": distancia if v is None or v["distancia"] is None
                         else float(v["distancia"]),
                         "relevancia": None if v is None or v["relevancia"] is None
                         else float(v["relevancia"])}
            por_persona.setdefault(r["id_persona"], []).append((candidato, juicio))
        hallazgos = {}
        for id_persona, pares in por_persona.items():
            pares.sort(key=lambda p: (p[0]["distancia"] if p[0]["distancia"] is not None else 9,
                                      p[0]["respuesta_id"]))
            hallazgos[id_persona] = consultas.hallazgo_de(
                criterio, [p[0] for p in pares], [p[1] for p in pares])
        por_criterio[orden] = hallazgos
        resumen.append({
            "criterio": criterio["etiqueta"],
            "unidades": len(plan),
            "verificadas": sum(1 for v in veredictos.values()
                               if v["veredicto"] != mod_verificacion.SIN_VERIFICAR),
            "irrelevantes": sum(1 for v in veredictos.values()
                                if v["veredicto"] == mod_verificacion.IRRELEVANTE),
            "sin_verificar": len(plan) - sum(
                1 for v in veredictos.values()
                if v["veredicto"] != mod_verificacion.SIN_VERIFICAR),
        })

    items, excluidos = consultas._combinar(definicion, por_criterio,
                                           definicion["umbral_distancia"])
    total = len(items)
    if todas:
        pagina, por_pagina = 1, max(1, total)
    inicio = (pagina - 1) * por_pagina
    sin_verificar = sum(r["sin_verificar"] for r in resumen)
    return {
        "tipo": ejecucion["tipo"],
        "version_contrato": consultas.VERSION_CONTRATO,
        "alcance": consultas.COMPLETO,
        "modo": definicion["modo"],
        "panel_id": definicion.get("panel_id"),
        "criterios": definicion["criterios"],
        "ejecucion": progreso,
        "total": total,
        "items": items[inicio:inicio + por_pagina],
        "paginacion": {"pagina": pagina, "por_pagina": por_pagina, "total": total,
                       "paginas": max(1, math.ceil(total / por_pagina))},
        "excluidos": excluidos,
        "degradaciones": [],
        "verificacion": {
            "completa": sin_verificar == 0 and progreso["terminal"],
            "evidencias": sum(r["unidades"] for r in resumen),
            "verificadas": sum(r["verificadas"] for r in resumen),
            "sin_verificar": sin_verificar,
            "irrelevantes": sum(r["irrelevantes"] for r in resumen),
            "por_criterio": resumen,
            "por_fallo": {},
            "personas_con_pendientes": sum(1 for i in items if i.get("verificacion_incompleta")),
            "presupuesto_agotado": progreso["estado"] == DETENIDA,
        },
        "costo": {"usd": progreso["costo_real_usd"],
                  "presupuesto_usd": progreso["presupuesto_usd"],
                  "estimado": progreso["costo_estimado"]},
        "diagnostico": {"ejecucion_id": str(ejecucion_id), "abrio_semantica": True},
    }


def reintentar(conn, ejecucion_id, presupuesto_usd=None, encolador=None):
    """Vuelve a encolar los lotes fallidos y, si se amplió el presupuesto,
    los omitidos. Nunca por encima del máximo."""
    ejecucion = _ejecucion(conn, ejecucion_id)
    if ejecucion["alcance"] != consultas.COMPLETO:
        raise Conflicto("Solo una ejecución completa tiene lotes que reintentar.")
    estados = ["fallido"]
    if presupuesto_usd not in (None, ""):
        try:
            nuevo = round(float(presupuesto_usd), 4)
        except (TypeError, ValueError):
            raise DatosInvalidos(f"`presupuesto_usd` inválido: {presupuesto_usd!r}.")
        if nuevo <= float(ejecucion["presupuesto_usd"]):
            raise DatosInvalidos("Para seguir hay que ampliar el presupuesto, no achicarlo.")
        if nuevo > presupuesto_maximo():
            raise DatosInvalidos(
                f"El presupuesto supera el máximo por ejecución "
                f"(US$ {presupuesto_maximo():.2f}).")
        db.ejecutar(conn, "update consulta_ejecucion set presupuesto_usd = %s where id = %s",
                    (nuevo, str(ejecucion_id)))
        estados.append("omitido")
    lotes = db.todas(
        conn,
        """select indice, intentos from consulta_lote
            where ejecucion_id = %s and estado = any(%s) order by indice""",
        (str(ejecucion_id), estados))
    if not lotes:
        conn.rollback()
        raise Conflicto("No hay lotes para reintentar en esta ejecución.")
    db.ejecutar(
        conn,
        """update consulta_lote set estado = 'pendiente', actualizado_en = now()
            where ejecucion_id = %s and estado = any(%s)""",
        (str(ejecucion_id), estados))
    db.ejecutar(conn, "update consulta_ejecucion set estado = 'procesando', "
                      "terminado_en = null where id = %s", (str(ejecucion_id),))
    conn.commit()
    encolador = encolador or crear_encolador()
    for fila in lotes:
        encolador.encolar(str(ejecucion_id), fila["indice"], intento=fila["intentos"])
    salida = estado(conn, ejecucion_id)
    salida["reencolados"] = [f["indice"] for f in lotes]
    return salida


def cancelar(conn, ejecucion_id):
    """Los lotes que no empezaron no se procesan; los que están en curso
    terminan y lo que verificaron vale."""
    ejecucion = _ejecucion(conn, ejecucion_id)
    if ejecucion["estado"] in TERMINALES:
        raise Conflicto(f"La ejecución ya terminó ({ejecucion['estado']}).")
    db.ejecutar(conn, "update consulta_ejecucion set estado = 'cancelada' where id = %s",
                (str(ejecucion_id),))
    db.ejecutar(conn, """update consulta_lote set estado = 'omitido',
                                error = 'Ejecución cancelada.', actualizado_en = now()
                          where ejecucion_id = %s and estado = 'pendiente'""",
                (str(ejecucion_id),))
    conn.commit()
    _cerrar_si_termino(conn, ejecucion_id)
    return estado(conn, ejecucion_id)
