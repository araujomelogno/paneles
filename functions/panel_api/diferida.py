"""La ingesta deja de correr adentro de una request.

La ingesta funcionaba con unos cientos de respuestas y **no llega** con una
base real: 200.000 respuestas son ~1.560 llamadas al proveedor de embeddings
en serie, media hora larga solo de eso, y Cloud Run corta la request a los
300 segundos. La carga fallaba con un error genérico después de haber
procesado una parte, y no había forma de retomar.

Subir el timeout corre la pared de lugar —el máximo de una función de 2ª gen
son 60 minutos—. Lo que hace falta es que el trabajo deje de vivir en una
request.

── Cómo queda ──

1. **Confirmar encola.** La ruta resuelve el mapeo una vez, parte las filas
   en lotes, los guarda y encola una tarea por lote. Responde en segundos.
2. **Cada tarea procesa su lote** llamando a la ingesta de siempre con su
   rebanada de filas. No hay una segunda implementación de la ingesta.
3. **El último lote consolida** el resumen, que es el mismo que devolvía la
   vía sincrónica.

── Dos cosas que este módulo decide y conviene no deshacer ──

**La tarea llama a `encuestas.ingestar()` / `cargas.ingestar()`, las de
siempre.** No reimplementa nada. Eso tiene tres consecuencias que son el
motivo de la decisión: el gate de consentimiento se re-evalúa en cada lote
—quien retira `uso_semantico` a mitad de carga no entra en los que faltan—,
el guardrail de PII sigue corriendo, y la idempotencia que ya tenían los
upserts vale igual para un reintento.

**El plan se congela al confirmar y las tareas no lo reinterpretan.** Si cada
tarea volviera a resolver qué variables son demográficas, dos tareas de la
misma carga podrían usar mapeos distintos y el estudio quedaría con la mitad
de las respuestas teniendo una pregunta que la otra mitad no.
"""

import json
import os

from . import db
from .errores import Conflicto, DatosInvalidos, NoEncontrado

# ── Parámetros ───────────────────────────────────────────────────────

# Cuántas filas del archivo entran en un lote. Con el patrón de memoria
# arreglado (R-ASYNC.2.b) el tamaño dejó de ser una condición de
# supervivencia y pasó a ser lo que es: cuánto trabajo se rehace cuando un
# lote falla. 2.000 deja cada tarea muy por debajo del techo de tiempo y
# pierde poco en un reintento.
FILAS_POR_LOTE = int(os.environ.get("INGESTA_FILAS_POR_LOTE", "2000"))

# Estados del trabajo y del lote. Espejan el catálogo de la `0019`; hay una
# prueba que falla si divergen.
ENCOLADA = "encolada"
PROCESANDO = "procesando"
TERMINADA = "terminada"
TERMINADA_CON_ERRORES = "terminada_con_errores"
FALLIDA = "fallida"

LOTE_PENDIENTE = "pendiente"
LOTE_PROCESANDO = "procesando"
LOTE_OK = "ok"
LOTE_FALLIDO = "fallido"

DESTINOS = ("encuesta", "carga")


class ErrorDeDatos(DatosInvalidos):
    """Un fallo que reintentar no arregla.

    Existe para que el procesador pueda decirle a Cloud Tasks «no insistas».
    Reintentar cinco veces un lote que falla por una fila mal formada gasta
    tiempo y embeddings en algo que va a fallar igual; el reintento sirve
    para lo transitorio.
    """


# ── El encolador, detrás de una interfaz ─────────────────────────────

class Encolador:
    """Lo que hace falta de Cloud Tasks, y nada más.

    Misma forma que el `Padron` de R2.12 y las `Credenciales` de R6.1.a, y
    por el mismo motivo: las reglas que importan —cómo se parte, qué pasa
    cuando un lote falla, cuándo se consolida— se tienen que poder probar
    contra Postgres de verdad sin desplegar una cola.
    """

    def encolar(self, trabajo_id, indice, intento=0):
        raise NotImplementedError


class EncoladorCloudTasks(Encolador):
    """La cola de verdad.

    La tarea es un `POST` HTTP a la función `procesaringesta`, autenticado
    con un token OIDC de la cuenta de servicio: la función es privada y no la
    puede invocar cualquiera que conozca la URL.

    El default de `TAREAS_COLA` es ese mismo nombre porque **la cola la crea
    el CLI con el id de la función**, y un Queue ID no admite guiones bajos.
    Si alguna vez se renombra la función, esta constante va atrás.
    """

    def __init__(self, proyecto=None, region=None, cola=None, url=None,
                 cuenta=None, cliente=None):
        self.proyecto = proyecto or os.environ.get("GCLOUD_PROJECT") or \
            os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        self.region = region or os.environ.get("TAREAS_REGION", "southamerica-east1")
        self.cola = cola or os.environ.get("TAREAS_COLA", "procesaringesta")
        self.url = url or os.environ.get("TAREAS_URL", "")
        self.cuenta = cuenta or os.environ.get("TAREAS_CUENTA", "")
        self._cliente = cliente

    @property
    def cliente(self):
        if self._cliente is None:
            from google.cloud import tasks_v2

            self._cliente = tasks_v2.CloudTasksClient()
        return self._cliente

    def encolar(self, trabajo_id, indice, intento=0):
        if not self.url:
            raise DatosInvalidos(
                "Falta `TAREAS_URL`: sin la dirección de la función que "
                "procesa los lotes no se puede encolar nada, y la carga "
                "quedaría guardada sin que la tome nadie.")
        cuerpo = json.dumps({"data": {"trabajo_id": trabajo_id,
                                      "indice": indice}}).encode()
        tarea = {
            "http_request": {
                "http_method": "POST",
                "url": self.url,
                "headers": {"Content-Type": "application/json"},
                "body": cuerpo,
                "oidc_token": {"service_account_email": self.cuenta,
                               "audience": self.url},
            },
            # El nombre hace la tarea única: encolar dos veces el mismo lote
            # por una confirmación repetida no duplica el trabajo, Cloud
            # Tasks rechaza el segundo.
            #
            # **Con el intento adentro**, y eso no es un detalle: Cloud Tasks
            # recuerda los nombres usados por un rato largo después de que la
            # tarea termina, así que sin el sufijo un reintento manual del
            # mismo lote sería rechazado por duplicado y el botón de la
            # pantalla no haría nada. Que la deduplicación funcione para la
            # confirmación repetida y no para el reintento es exactamente lo
            # que se quiere.
            "name": self.cliente.task_path(
                self.proyecto, self.region, self.cola,
                f"ingesta-{trabajo_id}-{indice}-{intento}"),
        }
        padre = self.cliente.queue_path(self.proyecto, self.region, self.cola)
        return self.cliente.create_task(request={"parent": padre, "task": tarea})


class EncoladorEnMemoria(Encolador):
    """Anota qué se encoló. Para las pruebas y el emulador."""

    def __init__(self):
        self.encoladas = []

    def encolar(self, trabajo_id, indice, intento=0):
        self.encoladas.append((trabajo_id, indice))
        return {"trabajo_id": trabajo_id, "indice": indice, "intento": intento}


def crear_encolador(cfg=None):
    if (os.environ.get("ENCOLADOR_TAREAS") or "").lower() in ("memoria", "test"):
        return EncoladorEnMemoria()
    return EncoladorCloudTasks()


# ── R-ASYNC.1 · Confirmar encola ─────────────────────────────────────

def _lotes_de(filas, tamano):
    for inicio in range(0, len(filas), tamano):
        yield filas[inicio : inicio + tamano]


def encolar(conn, destino_tipo, destino_id, plan, filas, actor=None,
            tamano_lote=None, encolador=None):
    """Persiste el trabajo, lo parte en lotes y los encola. No procesa nada.

    `plan` es el mapeo ya resuelto: preguntas, demográficas, `columna_id`,
    `origen`, tipo de identificador y lo que cada destino necesite. Se guarda
    tal cual y las tareas lo usan sin reinterpretarlo.
    """
    if destino_tipo not in DESTINOS:
        raise DatosInvalidos(f"Destino de ingesta desconocido: {destino_tipo!r}.",
                             {"destinos": list(DESTINOS)})
    filas = list(filas or [])
    if not filas:
        raise DatosInvalidos("El archivo no tenía filas para ingestar.")

    tamano = int(tamano_lote or FILAS_POR_LOTE)
    if tamano < 1:
        raise DatosInvalidos("El tamaño de lote tiene que ser al menos 1.")
    lotes = list(_lotes_de(filas, tamano))

    trabajo = db.una(
        conn,
        """insert into ingesta_trabajo
                  (destino_tipo, destino_id, plan, estado, lotes_total,
                   filas_total, creado_por)
           values (%s, %s, %s::jsonb, %s, %s, %s, %s)
        returning id""",
        (destino_tipo, destino_id, json.dumps(plan, default=str), ENCOLADA,
         len(lotes), len(filas), getattr(actor, "uid", None)))
    trabajo_id = trabajo["id"]

    for indice, lote in enumerate(lotes):
        db.ejecutar(
            conn,
            """insert into ingesta_lote (trabajo_id, indice, filas, filas_total)
               values (%s, %s, %s::jsonb, %s)""",
            (trabajo_id, indice, json.dumps(lote, default=str), len(lote)))

    # El commit va **antes** de encolar, y el orden importa: una tarea que
    # arranca antes de que su lote esté guardado no encuentra nada y falla.
    # Al revés —guardado y sin encolar— el trabajo queda esperando, que es
    # recuperable con el reintento manual.
    conn.commit()

    encolador = encolador or crear_encolador()
    encoladas, sin_encolar = 0, []
    for indice in range(len(lotes)):
        try:
            encolador.encolar(trabajo_id, indice)
            encoladas += 1
        except Exception as error:  # noqa: BLE001
            sin_encolar.append({"indice": indice, "error": str(error)})

    salida = estado(conn, trabajo_id)
    salida["encoladas"] = encoladas
    if sin_encolar:
        # No se oculta: un lote sin encolar no lo va a tomar nadie, y la
        # carga se quedaría «procesando» para siempre sin que nada falle.
        salida["sin_encolar"] = sin_encolar
        salida["aviso"] = (
            f"{len(sin_encolar)} lote(s) no se pudieron encolar. La carga "
            f"quedó guardada: se los puede reintentar desde la pantalla.")
    return salida


# ── R-ASYNC.3 · Estado y progreso ────────────────────────────────────

def _fila_de_progreso(conn, trabajo_id):
    fila = db.una(
        conn, "select * from v_ingesta_progreso where trabajo_id = %s",
        (trabajo_id,))
    if not fila:
        raise NoEncontrado(f"No existe el trabajo de ingesta {trabajo_id}.")
    return fila


def estado(conn, trabajo_id, con_lotes=False):
    """El avance de una carga. Sale de la base, así que sobrevive a un
    refresco del navegador y a que la pestaña se cierre."""
    fila = _fila_de_progreso(conn, trabajo_id)
    hechos = fila["lotes_ok"] + fila["lotes_fallidos"]
    salida = {
        "trabajo_id": fila["trabajo_id"],
        "destino": {"tipo": fila["destino_tipo"], "id": fila["destino_id"]},
        "estado": fila["estado"],
        "estado_etiqueta": fila["estado_etiqueta"],
        "terminal": fila["terminal"],
        "lotes_total": fila["lotes_total"],
        "lotes_ok": fila["lotes_ok"],
        "lotes_fallidos": fila["lotes_fallidos"],
        "lotes_en_curso": fila["lotes_en_curso"],
        "lotes_pendientes": fila["lotes_pendientes"],
        "filas_total": fila["filas_total"],
        "filas_procesadas": int(fila["filas_procesadas"]),
        "creado_en": fila["creado_en"].isoformat(),
        "terminado_en": (fila["terminado_en"].isoformat()
                         if fila["terminado_en"] else None),
        "segundos_transcurridos": fila["segundos_transcurridos"],
    }
    salida["porcentaje"] = (
        round(100 * hechos / fila["lotes_total"]) if fila["lotes_total"] else 0)
    # Lo que falta, estimado sobre lo que llevó hasta ahora. Es una regla de
    # tres y se informa como tal: con lotes de tamaño parejo no se equivoca
    # mucho, y vale más que una pantalla sin ninguna referencia.
    if hechos and not fila["terminal"]:
        por_lote = fila["segundos_transcurridos"] / hechos
        salida["segundos_restantes"] = int(por_lote * (fila["lotes_total"] - hechos))

    resumen = db.una(
        conn,
        "select resumen, plan->>'operacion' as operacion "
        "  from ingesta_trabajo where id = %s",
        (trabajo_id,))
    salida["resumen"] = (resumen or {}).get("resumen")
    # Fase 8 — un reproceso (R8.9) es un trabajo diferido como una carga, y
    # la pantalla los tiene que poder distinguir.
    salida["operacion"] = (resumen or {}).get("operacion") or "ingesta"
    if con_lotes:
        salida["lotes"] = lotes_de(conn, trabajo_id)
    salida["fallidos"] = lotes_de(conn, trabajo_id, solo_fallidos=True)
    return salida


def lotes_de(conn, trabajo_id, solo_fallidos=False):
    donde = "and estado = 'fallido'" if solo_fallidos else ""
    filas = db.todas(
        conn,
        f"""select indice, estado, intentos, filas_total, error, reintentable,
                   actualizado_en
              from ingesta_lote
             where trabajo_id = %s {donde}
             order by indice""",
        (trabajo_id,))
    return [
        {**f, "actualizado_en": f["actualizado_en"].isoformat()}
        for f in filas
    ]


def listar(conn, destino_tipo=None, destino_id=None, limite=20):
    """Las cargas de un destino, la más reciente primero. Es lo que deja
    volver a la pantalla y encontrar la carga de ayer."""
    filas = db.todas(
        conn,
        """select trabajo_id from v_ingesta_progreso
            where (%s::text is null or destino_tipo = %s)
              and (%s::bigint is null or destino_id = %s)
            order by creado_en desc
            limit %s""",
        (destino_tipo, destino_tipo, destino_id, destino_id, limite))
    return [estado(conn, f["trabajo_id"]) for f in filas]


# ── R-ASYNC.2 · Cada lote es una tarea ───────────────────────────────

# Lo que un resultado parcial aporta al consolidado. Las claves que se suman
# y las que se concatenan están separadas a propósito: sumar una lista o
# concatenar un entero no falla, da un resultado sin sentido.
SUMABLES = (
    "respuestas_escritas", "personas", "preguntas", "embebidas", "reutilizadas",
    "alias_registrados", "demograficos_completados",
    "membresias_nuevas", "membresias_existentes",
    "participaciones_nuevas", "participaciones_actualizadas",
    # Fase 8 — lo que la normalización dejó afuera (R8.1, R8.5).
    "descartadas_no_marcadas", "descartadas_no_respuesta",
    # Fase 8 — el reproceso (R8.9).
    "reembebidas", "sin_cambios", "borradas", "sin_consentimiento_reproceso",
)
CONCATENABLES = (
    "sin_mapear", "sin_mapear_detalle", "sin_consentimiento",
    "ids_persona_ingestados", "discrepancias_demograficas",
    "excluidas_por_demografica", "membresias_en_baja",
)


def _tomar_lote(conn, trabajo_id, indice):
    """Marca el lote como en curso y devuelve su trabajo. `None` si ya está.

    El `update ... where estado in (...)` en una sola sentencia es lo que
    hace segura la entrega duplicada: Cloud Tasks puede despachar la misma
    tarea dos veces, y de dos tareas simultáneas sobre el mismo lote solo una
    gana la fila. La otra ve `None` y se va sin trabajar.
    """
    fila = db.una(
        conn,
        """update ingesta_lote
              set estado = %s, intentos = intentos + 1,
                  actualizado_en = now(), error = null, reintentable = null
            where trabajo_id = %s and indice = %s
              and estado in (%s, %s)
        returning id, filas, filas_total, intentos""",
        (LOTE_PROCESANDO, trabajo_id, indice, LOTE_PENDIENTE, LOTE_FALLIDO))
    if not fila:
        return None
    # Tomar un lote **reabre** el trabajo, venga del estado que venga. No es
    # solo para el `encolada → procesando` del principio: un trabajo cuyo
    # único lote falló queda en un estado terminal, y el reintento
    # automático de Cloud Tasks vuelve por acá sin pasar por `reintentar()`.
    # Si no se reabriera, ese lote entraría bien y el trabajo seguiría
    # diciendo «fallida» para siempre.
    db.ejecutar(
        conn,
        """update ingesta_trabajo
              set estado = %s, terminado_en = null, resumen = null
            where id = %s and estado <> %s""",
        (PROCESANDO, trabajo_id, PROCESANDO))
    conn.commit()
    return fila


def _trabajo(conn, trabajo_id):
    fila = db.una(
        conn,
        "select id, destino_tipo, destino_id, plan, estado, lotes_total "
        "  from ingesta_trabajo where id = %s",
        (trabajo_id,))
    if not fila:
        raise NoEncontrado(f"No existe el trabajo de ingesta {trabajo_id}.")
    return fila


def _ingestar_el_lote(conn_boveda, conn_semantica, trabajo, filas, proveedor):
    """Despacha a la ingesta de siempre, con la rebanada de este lote.

    Acá no hay lógica de ingesta: hay una elección de destino. Es
    deliberado —ver el encabezado del módulo—, y es lo que hace que el gate
    de consentimiento, el guardrail de PII y la idempotencia valgan igual por
    esta vía sin haberlos vuelto a escribir.
    """
    from . import cargas, encuestas, reproceso

    plan = trabajo["plan"] or {}
    # Fase 8 · R8.9 — un reproceso no lee filas de un archivo: su lote son
    # ids de respuestas ya ingestadas. Tampoco es una segunda ingesta: no
    # resuelve identidades, no crea individuos ni toca la bóveda; recompone
    # el texto con `ingesta.respuesta_de` —la misma de siempre— y escribe por
    # `semantica.upsert_respuestas`, con el guardia de PII y el gate de
    # consentimiento re-evaluado en cada lote.
    if plan.get("operacion") == reproceso.OPERACION:
        return reproceso.procesar_lote(conn_boveda, conn_semantica, plan,
                                       filas, proveedor)
    comunes = {
        "columna_id": plan.get("columna_id") or "id_en_origen",
        "origen": plan.get("origen"),
        "proveedor": proveedor,
        "demograficas": plan.get("demograficas"),
        "tipo_identificador": plan.get("tipo_identificador"),
        # Fase 8 — la configuración de normalización de la carga, para que
        # quede guardada con el cuestionario. Las decisiones por pregunta ya
        # viajan adentro de cada pregunta del plan.
        "normalizacion": plan.get("normalizacion"),
    }
    preguntas = plan.get("preguntas") or []
    if trabajo["destino_tipo"] == "encuesta":
        return encuestas.ingestar(conn_boveda, conn_semantica,
                                  trabajo["destino_id"], preguntas, filas,
                                  **comunes)
    return cargas.ingestar(conn_boveda, conn_semantica, trabajo["destino_id"],
                           preguntas, filas, **comunes)


def procesar_lote(conn_boveda, conn_semantica, trabajo_id, indice,
                  proveedor=None):
    """Procesa un lote. Es lo que corre la tarea de Cloud Tasks.

    Devuelve `{estado, ...}`. **No** levanta la excepción de un fallo de
    datos: la deja registrada y devuelve `fallido`, porque propagarla haría
    que Cloud Tasks reintentara algo que no se arregla reintentando. Un
    fallo transitorio sí se propaga, que es como se pide el reintento.
    """
    trabajo = _trabajo(conn_boveda, trabajo_id)
    lote = _tomar_lote(conn_boveda, trabajo_id, indice)
    if lote is None:
        # Ya lo tomó otra tarea, o ya terminó. Las dos cosas son normales con
        # entrega duplicada y ninguna es un error.
        return {"estado": "ya_tomado", "trabajo_id": trabajo_id, "indice": indice}
    if lote["filas"] is None:
        _marcar_fallido(conn_boveda, trabajo_id, indice,
                        "Las filas de este lote ya se purgaron: no hay con qué "
                        "reprocesarlo. Volvé a cargar el archivo.",
                        reintentable=False)
        _cerrar_si_termino(conn_boveda, trabajo_id)
        return {"estado": LOTE_FALLIDO, "trabajo_id": trabajo_id, "indice": indice}

    try:
        resultado = _ingestar_el_lote(
            conn_boveda, conn_semantica, trabajo, lote["filas"], proveedor)
    except Exception as error:  # noqa: BLE001
        conn_semantica.rollback()
        conn_boveda.rollback()
        reintentable = not isinstance(error, (ErrorDeDatos, DatosInvalidos))
        _marcar_fallido(conn_boveda, trabajo_id, indice, str(error),
                        reintentable=reintentable)
        _cerrar_si_termino(conn_boveda, trabajo_id)
        if reintentable:
            # Se propaga: el 5xx es lo que le dice a Cloud Tasks que lo
            # vuelva a intentar con espera creciente.
            raise
        return {"estado": LOTE_FALLIDO, "trabajo_id": trabajo_id,
                "indice": indice, "error": str(error), "reintentable": False}

    conn_semantica.commit()
    db.ejecutar(
        conn_boveda,
        """update ingesta_lote
              set estado = %s, resultado = %s::jsonb, error = null,
                  reintentable = null, actualizado_en = now()
            where trabajo_id = %s and indice = %s""",
        (LOTE_OK, json.dumps(resultado, default=str), trabajo_id, indice))
    conn_boveda.commit()

    cierre = _cerrar_si_termino(conn_boveda, trabajo_id)
    return {"estado": LOTE_OK, "trabajo_id": trabajo_id, "indice": indice,
            "resultado": resultado, "trabajo": cierre}


def _marcar_fallido(conn, trabajo_id, indice, mensaje, reintentable):
    db.ejecutar(
        conn,
        """update ingesta_lote
              set estado = %s, error = %s, reintentable = %s,
                  actualizado_en = now()
            where trabajo_id = %s and indice = %s""",
        (LOTE_FALLIDO, mensaje[:4000], reintentable, trabajo_id, indice))
    conn.commit()


# ── Consolidación ────────────────────────────────────────────────────

def consolidar(conn, trabajo_id):
    """Junta los resultados parciales en el mismo resumen que devolvía la
    ingesta sincrónica.

    Lo que se suma se suma y lo que es una lista se concatena, sin repetir:
    un `id_persona` que aparece en dos lotes es la misma persona, y contarla
    dos veces haría que el resumen de una carga partida no coincida con el de
    la misma carga entera. Esa coincidencia es lo que esta función existe
    para sostener.
    """
    filas = db.todas(
        conn,
        "select resultado from ingesta_lote "
        " where trabajo_id = %s and estado = 'ok' order by indice",
        (trabajo_id,))
    resumen = {clave: 0 for clave in SUMABLES}
    listas = {clave: [] for clave in CONCATENABLES}
    extras = {}
    for fila in filas:
        parcial = fila["resultado"] or {}
        for clave in SUMABLES:
            valor = parcial.get(clave)
            if isinstance(valor, (int, float)):
                resumen[clave] += valor
        for clave in CONCATENABLES:
            valor = parcial.get(clave)
            if isinstance(valor, list):
                listas[clave].extend(valor)
        # Lo que no es ni sumable ni lista —`ref_estudio`, `encuesta_id`,
        # `tipo_identificador`, `sin_panel`— es igual en todos los lotes: se
        # toma el del primero que lo traiga.
        for clave, valor in parcial.items():
            if clave not in SUMABLES and clave not in CONCATENABLES \
                    and clave not in extras and not isinstance(valor, (list, dict)):
                extras[clave] = valor

    for clave, valores in listas.items():
        # `dict.fromkeys` conserva el orden y saca repetidos; lo que no es
        # hashable —los detalles son dicts— se deja tal cual.
        try:
            resumen[clave] = list(dict.fromkeys(valores))
        except TypeError:
            resumen[clave] = valores
    resumen.update(extras)
    return resumen


def _cerrar_si_termino(conn, trabajo_id):
    """Si no queda ningún lote por hacer, consolida y cierra el trabajo.

    Lo llama **cada** tarea al terminar, no una tarea coordinadora: con
    varias en paralelo, la última en terminar es la que encuentra el conteo
    completo y el `update` condicional hace que solo una cierre.
    """
    fila = _fila_de_progreso(conn, trabajo_id)
    if fila["lotes_pendientes"] or fila["lotes_en_curso"]:
        return None

    if fila["lotes_ok"] == fila["lotes_total"]:
        nuevo = TERMINADA
    elif fila["lotes_ok"]:
        nuevo = TERMINADA_CON_ERRORES
    else:
        # Ni uno entró. Se distingue de la anterior porque no hay nada que
        # conservar y conviene rehacer la carga entera.
        nuevo = FALLIDA

    resumen = consolidar(conn, trabajo_id)
    db.ejecutar(
        conn,
        """update ingesta_trabajo
              set estado = %s, resumen = %s::jsonb, terminado_en = now()
            where id = %s and estado not in (%s, %s, %s)""",
        (nuevo, json.dumps(resumen, default=str), trabajo_id,
         TERMINADA, TERMINADA_CON_ERRORES, FALLIDA))
    conn.commit()
    return {"estado": nuevo, "resumen": resumen}


# ── R-ASYNC.4 · Reintentar un lote suelto ────────────────────────────

def reintentar(conn, trabajo_id, indice=None, encolador=None):
    """Vuelve a encolar los lotes fallidos de un trabajo, o uno solo.

    **Sin rehacer la carga completa**, que es el punto: los lotes que
    entraron quedaron escritos y reprocesarlos sería gastar embeddings de
    nuevo. Y reprocesar el fallido es seguro porque la ingesta es idempotente
    —el upsert por `(individuo, pregunta)`, la membresía por `(panel,
    persona)` y la participación por `(encuesta, persona)`—.
    """
    _trabajo(conn, trabajo_id)
    donde = "and indice = %s" if indice is not None else ""
    parametros = [trabajo_id] + ([indice] if indice is not None else [])
    fallidos = db.todas(
        conn,
        f"""select indice, intentos, filas is not null as tiene_filas
              from ingesta_lote
             where trabajo_id = %s and estado = 'fallido' {donde}
             order by indice""",
        tuple(parametros))
    if not fallidos:
        raise Conflicto(
            "No hay lotes fallidos para reintentar en esta carga."
            if indice is None else
            f"El lote {indice} no está fallido: no hay nada que reintentar.",
            {"trabajo_id": trabajo_id})

    sin_filas = [f["indice"] for f in fallidos if not f["tiene_filas"]]
    if sin_filas:
        raise Conflicto(
            f"Los lotes {sin_filas} ya no tienen sus filas guardadas (se "
            f"purgaron), así que no se pueden reprocesar. Volvé a cargar el "
            f"archivo.", {"lotes": sin_filas})

    # Vuelven a `pendiente` antes de encolar: si quedaran en `fallido`, el
    # trabajo seguiría contándose como terminado y la tarea nueva se
    # encontraría con un cierre ya hecho.
    db.ejecutar(
        conn,
        f"""update ingesta_lote set estado = %s, actualizado_en = now()
             where trabajo_id = %s and estado = 'fallido' {donde}""",
        tuple([LOTE_PENDIENTE] + parametros))
    db.ejecutar(
        conn,
        """update ingesta_trabajo
              set estado = %s, terminado_en = null, resumen = null
            where id = %s""",
        (PROCESANDO, trabajo_id))
    conn.commit()

    encolador = encolador or crear_encolador()
    for fila in fallidos:
        encolador.encolar(trabajo_id, fila["indice"], intento=fila["intentos"])
    salida = estado(conn, trabajo_id)
    salida["reencolados"] = [f["indice"] for f in fallidos]
    return salida
