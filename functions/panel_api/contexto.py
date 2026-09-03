"""Contexto de una request: las dos conexiones y el proveedor de embeddings.

El remoto se abre en forma perezosa: la mayoría de las rutas de Fase 1 no lo
tocan, y cada conexión de más al store de embeddings es superficie de
exposición que no hace falta abrir.
"""

import contextlib

from . import config, db, embeddings as mod_embeddings


class Contexto:
    def __init__(self, conn_local, cfg=None):
        self.local = conn_local
        self.cfg = cfg
        self._remoto = None
        self._embeddings = None
        self._pila = contextlib.ExitStack()

    @property
    def remoto(self):
        if self._remoto is None:
            cfg = self.cfg or config.cargar()
            self._remoto = self._pila.enter_context(db.conectar(cfg.dsn_remoto))
        return self._remoto

    @property
    def embeddings(self):
        if self._embeddings is None:
            self._embeddings = mod_embeddings.crear(self.cfg or config.cargar())
        return self._embeddings

    def cerrar(self):
        self._pila.close()
        self._remoto = None


@contextlib.contextmanager
def abrir(cfg=None):
    cfg = cfg or config.cargar()
    with db.conectar(cfg.dsn_local) as conn_local:
        ctx = Contexto(conn_local, cfg)
        try:
            yield ctx
        finally:
            ctx.cerrar()
