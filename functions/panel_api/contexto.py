"""Contexto de una request: las conexiones y los proveedores.

Todo se abre en forma perezosa, y no por prolijidad:

* **El store semántico.** La mayoría de las rutas no lo tocan, y cada
  conexión de más al store de embeddings es superficie de exposición que no
  hace falta abrir. Además es lo que hace verificable R2.4: una consulta
  puramente demográfica no toca la propiedad, y la prueba lo comprueba
  mirando `abrio_semantica`.
* **Reranker y verificador.** Construirlos no cuesta nada, pero cada uno lee
  su clave de Secret Manager; que no se instancien en una request que no
  consulta es un secreto menos leído.
* **El padrón de usuarios.** Levanta el cliente de Firestore. Solo lo usan
  las rutas de Configuración.
"""

import contextlib

from . import (
    config,
    db,
    embeddings as mod_embeddings,
    reranker as mod_reranker,
    verificacion as mod_verificacion,
)


class Contexto:
    def __init__(self, conn_boveda, cfg=None):
        self.boveda = conn_boveda
        self.cfg = cfg
        self._semantica = None
        self._embeddings = None
        self._reranker = None
        self._verificador = None
        self._padron = None
        self._pila = contextlib.ExitStack()

    @property
    def abrio_semantica(self):
        """¿Esta request abrió la conexión al store semántico?

        Es lo que hace comprobable la garantía de R2.4. No se usa para
        decidir nada: se informa.
        """
        return self._semantica is not None

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

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = mod_reranker.crear(self.cfg)
        return self._reranker

    @property
    def verificador(self):
        if self._verificador is None:
            self._verificador = mod_verificacion.crear(self.cfg)
        return self._verificador

    @property
    def padron(self):
        if self._padron is None:
            from . import usuarios

            self._padron = usuarios.crear_padron(self.cfg)
        return self._padron

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
