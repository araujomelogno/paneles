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
                 api_key_embeddings, modelo_embeddings, dims_embeddings):
        self.dsn_boveda = dsn_boveda
        self.dsn_semantica = dsn_semantica
        self.proveedor_embeddings = proveedor_embeddings
        self.api_key_embeddings = api_key_embeddings
        self.modelo_embeddings = modelo_embeddings
        self.dims_embeddings = dims_embeddings


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
    )
