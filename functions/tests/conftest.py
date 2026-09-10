"""Fixtures de las pruebas.

Corren contra un Postgres real (no hay dobles de la base): el dedup, el gate
de consentimiento y la cascada dependen de índices únicos, de `on delete
cascade` y de pgvector, así que probarlos contra un fake no probaría nada.

`scripts/pg_pruebas.sh` levanta el cluster y exporta DSN_BOVEDA y DSN_SEMANTICA.
Sin esas variables, las pruebas que necesitan base se saltean; las que no
(auditoría de PII sobre el DDL, ruteo) corren igual.
"""

import os
import pathlib
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "functions"))

TABLAS_BOVEDA = [
    "alta_en_revision", "persona_borrada", "canje", "puntos_movimiento",
    "objetivo_composicion", "participacion", "encuesta", "consentimiento",
    "membresia", "alias_origen", "panel", "persona", "catalogo_premio",
    # Fase 2
    "consulta_guardada", "usuario_auditoria", "reidentificacion",
]
TABLAS_SEMANTICA = ["respuesta", "pregunta", "individuo", "cuestionario"]

VERSION_TEXTO = "consentimiento-2026-01"


def _conectar(dsn):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)


@pytest.fixture(scope="session")
def dsn_boveda():
    dsn = os.environ.get("DSN_BOVEDA")
    if not dsn:
        pytest.skip("Sin DSN_BOVEDA: correr `source scripts/pg_pruebas.sh` primero.")
    return dsn


@pytest.fixture(scope="session")
def dsn_semantica():
    dsn = os.environ.get("DSN_SEMANTICA")
    if not dsn:
        pytest.skip("Sin DSN_SEMANTICA: correr `source scripts/pg_pruebas.sh` primero.")
    return dsn


@pytest.fixture
def conn_boveda(dsn_boveda):
    conn = _conectar(dsn_boveda)
    with conn.cursor() as cur:
        cur.execute(f"truncate {', '.join(TABLAS_BOVEDA)} restart identity cascade")
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def conn_semantica(dsn_semantica):
    """Conexión al store semántico. Las fixtures se llaman `conn_*` porque son
    conexiones, y así no tapan al módulo `panel_api.semantica`."""
    conn = _conectar(dsn_semantica)
    with conn.cursor() as cur:
        cur.execute(f"truncate {', '.join(TABLAS_SEMANTICA)} restart identity cascade")
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def proveedor():
    from panel_api.embeddings import Deterministico

    return Deterministico(dims=1024)


def consentimientos(*finalidades):
    return [
        {"finalidad": f, "version_texto": VERSION_TEXTO}
        for f in (finalidades or ("contacto_participacion",))
    ]


@pytest.fixture
def alta_basica():
    """Fabrica el cuerpo de un alta con consentimiento, para no repetirlo."""

    def _alta(**campos):
        finalidades = campos.pop("finalidades", ("contacto_participacion",))
        cuerpo = {
            "persona": campos,
            "consentimientos": consentimientos(*finalidades),
        }
        for clave in ("origen", "id_en_origen", "panel_id"):
            if clave in campos:
                cuerpo[clave] = campos.pop(clave)
        return cuerpo

    return _alta


# ════════════════════════════════════════════════════════════════════
#  Fase 2
# ════════════════════════════════════════════════════════════════════

class ContextoDePrueba:
    """Un `Contexto` de mentira, con la misma superficie que el de verdad.

    La conexión al store semántico se entrega perezosamente y se registra si
    se entregó: eso es lo que deja comprobar la garantía de R2.4 —una
    consulta puramente demográfica no abre el store semántico— sin tener que
    espiar a psycopg.
    """

    def __init__(self, conn_boveda, conn_semantica, embeddings,
                 reranker=None, verificador=None, padron=None):
        self.boveda = conn_boveda
        self.cfg = None
        self.embeddings = embeddings
        self._semantica = conn_semantica
        self.abrio_semantica = False
        self._reranker = reranker
        self._verificador = verificador
        self._padron = padron

    @property
    def semantica(self):
        self.abrio_semantica = True
        return self._semantica

    @property
    def reranker(self):
        from panel_api import reranker as mod

        if self._reranker is None:
            self._reranker = mod.Lexico()
        return self._reranker

    @property
    def verificador(self):
        from panel_api import verificacion as mod

        if self._verificador is None:
            self._verificador = mod.Lexico()
        return self._verificador

    @property
    def padron(self):
        from panel_api import usuarios

        if self._padron is None:
            self._padron = usuarios.PadronEnMemoria()
        return self._padron


@pytest.fixture
def ctx(conn_boveda, conn_semantica, proveedor):
    """Contexto con los dos stores y los proveedores sin red.

    Reranker y verificador son los léxicos: la Fase 2 se prueba sin llamar a
    Voyage ni a la API de Claude, y con resultados determinísticos.
    """
    return ContextoDePrueba(conn_boveda, conn_semantica, proveedor)


@pytest.fixture
def ctx_solo_boveda(conn_boveda, proveedor):
    """Contexto cuyo store semántico explota si alguien lo toca.

    Es la prueba de R2.4 en su forma más directa: si la consulta demográfica
    abriera el store semántico, el test falla con una excepción y no con una
    aserción sutil.
    """

    class SinSemantica(ContextoDePrueba):
        @property
        def semantica(self):
            raise AssertionError(
                "Una consulta puramente demográfica no debe tocar el store "
                "semántico (R2.4)."
            )

    return SinSemantica(conn_boveda, None, proveedor)


@pytest.fixture
def padron():
    from panel_api import usuarios

    return usuarios.PadronEnMemoria()


@pytest.fixture
def actor():
    """Fabrica actores con un rol, para probar los permisos."""
    from panel_api.auth import Actor

    def _actor(rol="admin", uid=None, email=None, nombre=None):
        return Actor(
            uid=uid or f"uid-{rol}",
            email=email or f"{rol}@equipos.com.uy",
            rol=rol,
            nombre=nombre or rol.capitalize(),
        )

    return _actor
