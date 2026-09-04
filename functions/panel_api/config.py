"""Configuración por variables de entorno. Ningún secreto vive en el repo.

Los dos stores son instancias Cloud SQL distintas (CLAUDE.md): nunca
comparten DSN. `cargar()` lo verifica y falla temprano si alguien los
apunta a la misma base.
"""

import os


class ErrorConfig(RuntimeError):
    pass


class Config:
    def __init__(self, dsn_local, dsn_remoto, proveedor_embeddings,
                 api_key_embeddings, modelo_embeddings, dims_embeddings):
        self.dsn_local = dsn_local
        self.dsn_remoto = dsn_remoto
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

    dsn_local = entorno.get("DSN_LOCAL", "").strip()
    dsn_remoto = entorno.get("DSN_REMOTO", "").strip()
    if not dsn_local:
        raise ErrorConfig("Falta DSN_LOCAL (store local: bóveda de PII + paneles).")
    if not dsn_remoto:
        raise ErrorConfig("Falta DSN_REMOTO (store remoto: embeddings).")
    if _limpiar_dsn(dsn_local) == _limpiar_dsn(dsn_remoto):
        raise ErrorConfig(
            "DSN_LOCAL y DSN_REMOTO apuntan a la misma base. Los dos stores "
            "tienen que estar en instancias Cloud SQL distintas: la separación "
            "es parte del diseño de privacidad (ver CLAUDE.md)."
        )

    return Config(
        dsn_local=dsn_local,
        dsn_remoto=dsn_remoto,
        proveedor_embeddings=entorno.get("EMBEDDINGS_PROVEEDOR", "voyage"),
        api_key_embeddings=entorno.get("EMBEDDINGS_API_KEY", ""),
        modelo_embeddings=entorno.get("EMBEDDINGS_MODELO", "voyage-3.5"),
        dims_embeddings=int(entorno.get("EMBEDDINGS_DIMS", "1024")),
    )
