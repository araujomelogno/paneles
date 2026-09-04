"""Cloud Functions for Firebase — API del sistema de gestión de paneles.

Un único punto de entrada HTTP (`api`) que rutea la superficie de la Fase 1.
Firebase Hosting reescribe `/api/**` hacia acá (ver firebase.json), así que
el frontend y la API comparten origen y no hace falta CORS en producción.

Secretos por variable de entorno / Secret Manager; nunca en el repo.
"""

import json

import firebase_admin
from firebase_functions import https_fn, options

from panel_api import auth, config, contexto, ruteo
from panel_api.errores import ErrorApi

firebase_admin.initialize_app()

# Debe coincidir con FUNCTIONS_REGION en el frontend.
REGION = "us-central1"

SECRETOS = [
    "DSN_LOCAL",       # store local: bóveda de PII + paneles
    "DSN_REMOTO",      # store remoto: embeddings (instancia distinta)
    "EMBEDDINGS_API_KEY",
]

PREFIJO = "/api"


def _json(status, cuerpo):
    return https_fn.Response(
        json.dumps(cuerpo, ensure_ascii=False, default=str),
        status=status,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def _camino_de(req):
    camino = req.path or "/"
    if camino.startswith(PREFIJO):
        camino = camino[len(PREFIJO):] or "/"
    return camino


@https_fn.on_request(
    region=REGION,
    secrets=SECRETOS,
    cors=options.CorsOptions(cors_origins=["*"], cors_methods=["get", "post", "patch", "delete", "options"]),
    memory=options.MemoryOption.MB_512,
    timeout_sec=300,
)
def api(req: https_fn.Request) -> https_fn.Response:
    if req.method == "OPTIONS":
        return https_fn.Response("", status=204)

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
        with contexto.abrir(cfg) as ctx:
            status, respuesta = ruteo.despachar(
                req.method, _camino_de(req), cuerpo, consulta, actor, ctx
            )
            return _json(status, respuesta)
    except ErrorApi as error:
        return _json(error.status, error.como_dict())
    except config.ErrorConfig as error:
        return _json(500, {"error": "config", "mensaje": str(error)})
    except Exception as error:  # noqa: BLE001
        # No se filtra el detalle al cliente: puede traer fragmentos de PII.
        print(f"[api] error no manejado: {error!r}")
        return _json(500, {"error": "interno", "mensaje": "Error interno del servidor."})
