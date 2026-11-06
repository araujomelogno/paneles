"""Cloud Functions for Firebase — API del sistema de gestión de paneles.

Un único punto de entrada HTTP (`api`) que rutea la superficie de la Fase 1.
Firebase Hosting reescribe `/api/**` hacia acá (ver firebase.json), así que
el frontend y la API comparten origen y no hace falta CORS en producción.

Secretos por variable de entorno / Secret Manager; nunca en el repo.
"""

import json
import os
import traceback

import firebase_admin
from firebase_functions import https_fn, options, tasks_fn

from panel_api import auth, config, contexto, esquema, ruteo
from panel_api.errores import ErrorApi

# Idempotente: el descubrimiento de funciones y el emulador pueden cargar
# este módulo más de una vez, y initialize_app() falla si ya hay app default.
if not firebase_admin._apps:
    firebase_admin.initialize_app()

# Si cambia, hay que cambiar también la región del rewrite de /api/** en
# firebase.json: los dos tienen que apuntar al mismo lado.
REGION = "southamerica-east1"

# Conector de Acceso a VPC sin servidor. Es la vía por la que la función llega
# a las IP privadas de las dos instancias de Cloud SQL; sin él no hay base.
# Se lee del entorno del deploy (no del runtime): `export VPC_CONNECTOR=...`
# antes de `firebase deploy`. Ver docs/DESPLIEGUE.md.
VPC_CONNECTOR = os.environ.get("VPC_CONNECTOR") or None

SECRETOS = [
    "DSN_BOVEDA",         # bóveda: PII + módulo de paneles
    "DSN_SEMANTICA",      # store semántico: embeddings (instancia distinta)
    "EMBEDDINGS_API_KEY",
    # ── Fase 2 ──
    # Las dos etapas de la consulta semántica que llaman a un modelo ajeno.
    # Si falta alguna, la consulta no se cae: degrada y lo dice en la
    # respuesta (ver panel_api/reranker.py y panel_api/verificacion.py). Aun
    # así los secretos tienen que existir en Secret Manager, porque el deploy
    # falla si declara un secreto que no está; el manual de despliegue de la
    # Fase 2 los crea, con valor vacío si todavía no hay clave.
    "RERANKER_API_KEY",   # cross-encoder de reranking (R2.8)
    "CLAUDE_API_KEY",     # verificación de evidencia (R2.9)
    # ── Fase 4 ──
    # Un secreto que está en Secret Manager pero no figura acá **no llega al
    # runtime**: `firebase deploy` solo monta los declarados. El síntoma es
    # desconcertante, porque el sistema se degrada como si no estuviera
    # configurado —«sin `WHATSAPP_TOKEN` no se pueden listar las plantillas»—
    # justo después de haberlo cargado. `test_main.py` comprueba que esta
    # lista cubra todo lo que el código lee.
    "VERIFICACION_SAL",           # R4.3 — con qué se hashean códigos y orígenes
    "DESAFIO_SECRETO",            # R4.3 — clave del desafío anti-automatización
    "WHATSAPP_TOKEN",             # R4.5 — token de la app de Meta
    "WHATSAPP_PHONE_NUMBER_ID",   # R4.5 — el número emisor
    "WHATSAPP_WABA_ID",           # R4.5 — de acá sale la lista de plantillas
    # ── Fase 6 ──
    # A dónde vuelve el enlace de acceso del portal. No es un secreto en el
    # sentido de confidencial, pero sí en el de «configuración que cambia
    # entre ambientes y no puede vivir en el código»: el enlace apunta acá, y
    # apuntarlo mal manda a los panelistas a otro lado.
    "PORTAL_URL",
    # ── R6.1.a / PEDIDO R1 ──
    # La *web API key* del proyecto **ya no está acá**. Es pública por
    # diseño —va en la configuración del frontend— y guardarla en Secret
    # Manager no agregaba seguridad y sí una vía de falla: con el nombre
    # `FIREBASE_WEB_API_KEY` el valor real no se podía cargar nunca
    # (`firebase-tools` rechaza el prefijo `FIREBASE_`), el placeholder quedó
    # en el runtime y nadie entraba al portal. Ahora es `WEB_API_KEY`, una
    # variable común de `functions/.env`, visible en un `describe`.
    # ── Ingesta diferida ──
    # A qué URL le pega Cloud Tasks para procesar un lote, y con qué cuenta
    # de servicio firma el token. No son confidenciales, pero cambian entre
    # ambientes y no pueden vivir en el código: la función de tareas es
    # privada, así que una URL o una cuenta equivocadas no fallan al
    # desplegar —fallan cuando el primer lote no lo toma nadie—.
    "TAREAS_URL",
    "TAREAS_CUENTA",
    # ── R-MAIL ──
    # La contraseña de aplicación de `notificaciones@equipos.com.uy` para el
    # SMTP de Google Workspace. Sin ella —o sin declararla acá— el portal y la
    # landing informan que el envío falló, y Cumplimiento → Contacto lo dice.
    "SMTP_PASSWORD",
    # ── R-CS · consulta de alcance completo ──
    # A qué URL le pega Cloud Tasks para verificar un lote de una consulta
    # completa (la función `procesarconsulta`). Mismo motivo que
    # `TAREAS_URL`: no es confidencial, cambia entre ambientes, y si falta no
    # falla el deploy sino la primera consulta completa —con un aviso de
    # lotes sin encolar—. La cuenta que firma es la misma `TAREAS_CUENTA`.
    "TAREAS_CONSULTA_URL",
]

PREFIJO = "/api"

# ── Ingesta diferida ────────────────────────────────────────────────
#
# Cuántos lotes se procesan a la vez. Tres es conservador a propósito: el
# techo real no es la función sino los límites de tasa del proveedor de
# embeddings y la instancia de la base. Con `paneles-semantica` en un tier
# chico, subir esto degrada o fuerza a agrandarla.
TAREAS_EN_PARALELO = int(os.environ.get("TAREAS_EN_PARALELO", "3"))

# Cuántas veces se reintenta un lote antes de darlo por fallido. Aplica solo
# a lo transitorio: un error de datos no propaga excepción y por lo tanto no
# se reintenta nunca (ver `diferida.procesar_lote`).
TAREAS_REINTENTOS = int(os.environ.get("TAREAS_REINTENTOS", "5"))


def _json(status, cuerpo):
    return https_fn.Response(
        json.dumps(cuerpo, ensure_ascii=False, default=str),
        status=status,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def _origen_de(req):
    """De dónde viene la request, para los límites por origen.

    Sale de `X-Forwarded-For`, que es lo que pone Firebase Hosting delante
    de la función, y no de un campo del cuerpo. La diferencia no es de
    estilo: un límite cuyo identificador lo elige quien lo sufre no limita
    nada —alcanza con mandar un `origen` distinto en cada intento—.

    Se toma la **primera** dirección de la cadena, que es la del cliente;
    las que siguen son los proxies. Un cliente puede mandar su propio
    `X-Forwarded-For` y Hosting le antepone la IP real, así que el primer
    valor puede ser inventado; por eso el límite por origen es el secundario
    y el que muerde de verdad es el de por correo, que es por cuenta.
    """
    cadena = (req.headers.get("X-Forwarded-For")
              or req.headers.get("x-forwarded-for") or "")
    primera = cadena.split(",")[0].strip()
    return primera or (req.remote_addr or None)


def _camino_de(req):
    camino = req.path or "/"
    if camino.startswith(PREFIJO):
        camino = camino[len(PREFIJO):] or "/"
    return camino


@https_fn.on_request(
    region=REGION,
    secrets=SECRETOS,
    vpc_connector=VPC_CONNECTOR,
    # Solo el tráfico a las IP privadas sale por la VPC; el resto (Voyage,
    # Secret Manager) sigue saliendo directo.
    vpc_connector_egress_settings=(
        options.VpcEgressSetting.PRIVATE_RANGES_ONLY if VPC_CONNECTOR else None
    ),
    cors=options.CorsOptions(cors_origins=["*"], cors_methods=["get", "post", "put", "patch", "delete", "options"]),
    # 1 GiB y no 512 MB por la ingesta de `.sav` (R3.9): leer el archivo
    # levanta pandas y pyreadstat, y el archivo pasa por memoria tres
    # veces —base64, bytes, DataFrame—. Con 512 MB el proceso moría sin
    # log y el navegador veía un 500 sin explicación. El resto de las
    # rutas no importa pandas, así que no pagan la diferencia salvo en
    # instancias que ya sirvieron una ingesta.
    memory=options.MemoryOption.GB_1,
    timeout_sec=300,
)
def api(req: https_fn.Request) -> https_fn.Response:
    if req.method == "OPTIONS":
        return https_fn.Response("", status=204)

    # R3.7 — la landing de inscripción es pública y no puede exigir token.
    # La lista es de rutas concretas y no de un prefijo: un prefijo abierto
    # se convierte, la primera vez que alguien agrega una ruta debajo, en un
    # agujero que nadie eligió abrir.
    if ruteo.es_publica(req.method, _camino_de(req)):
        actor = auth.Actor(uid=None, email=None, rol=None, nombre="público")
    elif ruteo.es_del_portal(_camino_de(req)):
        # Fase 6 — el portal autentica contra Firebase Auth pero **no**
        # contra el padrón interno: un panelista no es usuario de la
        # administración. El actor que sale de acá no tiene rol, así que no
        # puede entrar a ninguna ruta interna aunque se cuele en el ruteo.
        try:
            actor = auth.actor_de_portal(req.headers)
        except ErrorApi as error:
            return _json(error.status, error.como_dict())
    else:
        try:
            actor = auth.actor_de_request(req.headers)
        except ErrorApi as error:
            return _json(error.status, error.como_dict())

    try:
        cuerpo = req.get_json(silent=True) or {}
    except Exception:
        cuerpo = {}
    consulta = dict(req.args or {})

    try:
        cfg = config.cargar()
        with contexto.abrir(cfg, origen=_origen_de(req)) as ctx:
            status, respuesta = ruteo.despachar(
                req.method, _camino_de(req), cuerpo, consulta, actor, ctx
            )
            return _json(status, respuesta)
    except ErrorApi as error:
        return _json(error.status, error.como_dict())
    except config.ErrorConfig as error:
        return _json(500, {"error": "config", "mensaje": str(error)})
    except Exception as error:  # noqa: BLE001
        # Con el traceback: sin él, un 500 obliga a reproducir el caso a
        # ciegas. No lleva datos de la request —`format_exc()` imprime el
        # código, no los valores—, así que no filtra PII al log.
        print(f"[api] error no manejado: {error!r}\n{traceback.format_exc()}")
        # Un objeto que no existe casi siempre es una migración sin aplicar, y
        # eso el usuario lo puede resolver. Se le dice cuál es: el mensaje solo
        # nombra el objeto y el archivo, así que no filtra ningún dato.
        falta = esquema.explicar_error(error)
        if falta:
            return _json(500, {
                "error": "esquema_desactualizado",
                "mensaje": falta,
                "detalle": {"ruta_de_diagnostico": "GET /api/diagnostico/esquema"},
            })
        # Para el resto no se filtra el detalle: puede traer fragmentos de PII.
        return _json(500, {"error": "interno", "mensaje": "Error interno del servidor."})


# ════════════════════════════════════════════════════════════════════
#  Ingesta diferida — la función que procesa un lote
# ════════════════════════════════════════════════════════════════════
#
# La contraparte de `diferida.encolar()`. Cloud Tasks le pega una vez por
# lote, con el mismo acceso a bases y secretos que la función HTTP: **mismo
# conector VPC y misma región**, porque sin eso no llega a las IP privadas de
# las dos instancias de Cloud SQL y el síntoma es un timeout opaco.
#
# El contrato con `diferida.procesar_lote()` es lo que hace que los
# reintentos sirvan:
#
#   · un error **transitorio** se propaga → esta función devuelve 5xx →
#     Cloud Tasks reintenta con espera creciente;
#   · un error **de datos** no se propaga → devuelve 200 con el lote marcado
#     fallido → Cloud Tasks no insiste, porque insistir no lo arregla.
#
# Reintentar cinco veces un lote que falla por una fila mal formada gasta
# tiempo y embeddings en algo que va a fallar igual.
#
# **El nombre va sin guión bajo, y no es estética.** El SDK de Python deriva
# el id del endpoint del nombre de esta función —no hay opción para fijarlo—
# y el CLI crea la cola de Cloud Tasks con ese id. Un Queue ID solo admite
# letras, números y guiones, así que `procesar_ingesta` despliega la función
# y después falla el deploy entero:
#
#   Queue ID "procesar_ingesta" can contain only letters ([A-Za-z]),
#   numbers ([0-9]), or hyphens (-).
#
# El nombre de la función es, de hecho, el nombre de la cola.
@tasks_fn.on_task_dispatched(
    region=REGION,
    secrets=SECRETOS,
    vpc_connector=VPC_CONNECTOR,
    vpc_connector_egress_settings=(
        options.VpcEgressSetting.PRIVATE_RANGES_ONLY if VPC_CONNECTOR else None
    ),
    retry_config=options.RetryConfig(
        max_attempts=TAREAS_REINTENTOS,
        min_backoff_seconds=10,
        max_backoff_seconds=300,
    ),
    rate_limits=options.RateLimits(
        max_concurrent_dispatches=TAREAS_EN_PARALELO,
    ),
    # Lo mismo que la función HTTP y por la misma razón: un lote de un `.sav`
    # pasa por pandas. El techo de tiempo de una tarea es el de la función,
    # pero ahora ninguna tarea tiene que acercarse: cada una hace un lote.
    memory=options.MemoryOption.GB_1,
    timeout_sec=1800,
)
def procesaringesta(req: https_fn.CallableRequest) -> dict:
    from panel_api import diferida

    datos = req.data or {}
    trabajo_id = datos.get("trabajo_id")
    indice = datos.get("indice")
    if trabajo_id is None or indice is None:
        # Sin esto no hay nada que hacer, y reintentarlo no va a cambiar.
        # Se devuelve en vez de levantar: una tarea mal armada reintentada
        # cinco veces es ruido en los logs y nada más.
        print(f"[tareas] tarea sin trabajo_id/indice: {datos!r}")
        return {"estado": "descartada"}

    cfg = config.cargar()
    with contexto.abrir(cfg) as ctx:
        return diferida.procesar_lote(
            ctx.boveda, ctx.semantica, int(trabajo_id), int(indice),
            proveedor=ctx.embeddings)


# ════════════════════════════════════════════════════════════════════
#  R-CS · Consulta de alcance completo — la función que verifica un lote
# ════════════════════════════════════════════════════════════════════
#
# La contraparte de `consulta_completa.lanzar()`. Mismo contrato que
# `procesaringesta`: un error transitorio se propaga (5xx → Cloud Tasks
# reintenta), uno de datos no. Mismo conector VPC y misma región.
#
# La concurrencia es **baja a propósito**: el control de presupuesto se hace
# antes de cada lote sobre la suma de los anteriores, y con N tareas en
# paralelo el desvío posible es de N lotes. Con 2 y lotes de 100 unidades es
# del orden de centavos. Subirla acelera y agranda ese desvío.
CONSULTA_TAREAS_EN_PARALELO = int(os.environ.get("CONSULTA_TAREAS_EN_PARALELO", "2"))


@tasks_fn.on_task_dispatched(
    region=REGION,
    secrets=SECRETOS,
    vpc_connector=VPC_CONNECTOR,
    vpc_connector_egress_settings=(
        options.VpcEgressSetting.PRIVATE_RANGES_ONLY if VPC_CONNECTOR else None
    ),
    retry_config=options.RetryConfig(
        max_attempts=TAREAS_REINTENTOS,
        min_backoff_seconds=30,
        max_backoff_seconds=600,
    ),
    rate_limits=options.RateLimits(
        max_concurrent_dispatches=CONSULTA_TAREAS_EN_PARALELO,
    ),
    memory=options.MemoryOption.GB_1,
    timeout_sec=1800,
)
def procesarconsulta(req: https_fn.CallableRequest) -> dict:
    from panel_api import consulta_completa

    datos = req.data or {}
    ejecucion_id = datos.get("trabajo_id")
    indice = datos.get("indice")
    if ejecucion_id is None or indice is None:
        print(f"[tareas] tarea de consulta sin trabajo_id/indice: {datos!r}")
        return {"estado": "descartada"}

    cfg = config.cargar()
    with contexto.abrir(cfg) as ctx:
        salida = consulta_completa.procesar_lote(
            ctx.boveda, ctx.semantica, str(ejecucion_id), int(indice),
            reranker=ctx.reranker, verificador=ctx.verificador)
        # Sin contenido: estado y números.
        print("[consulta_completa] " + json.dumps(
            {"ejecucion_id": str(ejecucion_id), "indice": indice,
             "estado": salida.get("estado"),
             "costo_usd": (salida.get("resultado") or {}).get("costo_usd")},
            default=str))
        return {"estado": salida.get("estado")}
