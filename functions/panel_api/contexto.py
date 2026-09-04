"""Contexto de una request: las dos conexiones y el proveedor de embeddings.

El store semántico se abre en forma perezosa: la mayoría de las rutas de la
Fase 1 no lo tocan, y cada conexión de más al store de embeddings es
superficie de exposición que no hace falta abrir.
"""

import contextlib

from . import config, db, embeddings as mod_embeddings


class Contexto:
    def __init__(self, conn_boveda, cfg=None):
        self.boveda = conn_boveda
        self.cfg = cfg
        self._semantica = None
        self._embeddings = None
        self._pila = contextlib.ExitStack()

    @property
    def semantica(self):
        if self._semantica is None:
            cfg = self.cfg or config.cargar()
            self._semantica = self._pila.enter_context(db.conectar(cfg.dsn_semantica))
        return self._semantica

    @property
    def embeddings(self):
        if self._embeddings is None:
            self._embeddings = mod_embeddings.crear(self.cfg or config.cargar())
        return self._embeddings

    def cerrar(self):
        self._pila.close()
        self._semantica = None


@contextlib.contextmanager
def abrir(cfg=None):
    cfg = cfg or config.cargar()
    with db.conectar(cfg.dsn_boveda) as conn_boveda:
        ctx = Contexto(conn_boveda, cfg)
        try:
            yield ctx
        finally:
            ctx.cerrar()
