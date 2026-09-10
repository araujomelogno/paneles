"""Configuración por variables de entorno. Ningún secreto vive en el repo.

Los dos stores son instancias Cloud SQL distintas (CLAUDE.md): nunca
comparten DSN. `cargar()` lo verifica y falla temprano si alguien los
apunta a la misma base.
"""

import os


class ErrorConfig(RuntimeError):
    pass


class Config:
    def __init__(self, dsn_boveda, dsn_semantica, proveedor_embeddings,
                 api_key_embeddings, modelo_embeddings, dims_embeddings,
                 proveedor_reranker=None, api_key_reranker=None,
                 modelo_reranker=None, proveedor_verificacion=None,
                 api_key_claude=None, modelo_claude=None):
        self.dsn_boveda = dsn_boveda
        self.dsn_semantica = dsn_semantica
        self.proveedor_embeddings = proveedor_embeddings
        self.api_key_embeddings = api_key_embeddings
        self.modelo_embeddings = modelo_embeddings
        self.dims_embeddings = dims_embeddings
        # ── Fase 2: las dos etapas que llaman a un modelo ajeno ──
        # Si falta la clave de alguna de las dos, la consulta no se cae:
        # degrada y lo dice en la respuesta (ver reranker.py y
        # verificacion.py). Por eso acá no se valida nada más que los DSN.
        self.proveedor_reranker = proveedor_reranker
        self.api_key_reranker = api_key_reranker
        self.modelo_reranker = modelo_reranker
        self.proveedor_verificacion = proveedor_verificacion
        self.api_key_claude = api_key_claude
        self.modelo_claude = modelo_claude


def _limpiar_dsn(dsn):
    """Normaliza un DSN para compararlo (sin usuario/clave ni parámetros)."""
    sin_credenciales = dsn.split("@")[-1]
    return sin_credenciales.split("?")[0].strip().rstrip("/").lower()


def cargar(entorno=None):
    entorno = os.environ if entorno is None else entorno

    dsn_boveda = entorno.get("DSN_BOVEDA", "").strip()
    dsn_semantica = entorno.get("DSN_SEMANTICA", "").strip()
    if not dsn_boveda:
        raise ErrorConfig("Falta DSN_BOVEDA (store de bóveda: PII + paneles).")
    if not dsn_semantica:
        raise ErrorConfig("Falta DSN_SEMANTICA (store semántico: embeddings).")
    if _limpiar_dsn(dsn_boveda) == _limpiar_dsn(dsn_semantica):
        raise ErrorConfig(
            "DSN_BOVEDA y DSN_SEMANTICA apuntan a la misma base. Los dos stores "
            "tienen que estar en instancias Cloud SQL distintas: la separación "
            "es parte del diseño de privacidad (ver CLAUDE.md)."
        )

    return Config(
        dsn_boveda=dsn_boveda,
        dsn_semantica=dsn_semantica,
        proveedor_embeddings=entorno.get("EMBEDDINGS_PROVEEDOR", "voyage"),
        api_key_embeddings=entorno.get("EMBEDDINGS_API_KEY", ""),
        modelo_embeddings=entorno.get("EMBEDDINGS_MODELO", "voyage-3.5"),
        dims_embeddings=int(entorno.get("EMBEDDINGS_DIMS", "1024")),
        proveedor_reranker=entorno.get("RERANKER_PROVEEDOR", "voyage"),
        api_key_reranker=entorno.get("RERANKER_API_KEY", ""),
        modelo_reranker=entorno.get("RERANKER_MODELO", "rerank-2.5"),
        proveedor_verificacion=entorno.get("VERIFICACION_PROVEEDOR", "claude"),
        api_key_claude=entorno.get("CLAUDE_API_KEY", ""),
        modelo_claude=entorno.get("CLAUDE_MODELO", "claude-sonnet-5"),
    )
