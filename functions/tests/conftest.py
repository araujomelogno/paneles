"""Fixtures de las pruebas.

Corren contra un Postgres real (no hay dobles de la base): el dedup, el gate
de consentimiento y la cascada dependen de índices únicos, de `on delete
cascade` y de pgvector, así que probarlos contra un fake no probaría nada.

`scripts/pg_pruebas.sh` levanta el cluster y exporta DSN_LOCAL y DSN_REMOTO.
Sin esas variables, las pruebas que necesitan base se saltean; las que no
(auditoría de PII sobre el DDL, ruteo) corren igual.
"""

import os
import pathlib
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "functions"))

TABLAS_LOCALES = [
    "alta_en_revision", "persona_borrada", "canje", "puntos_movimiento",
    "objetivo_composicion", "participacion", "encuesta", "consentimiento",
    "membresia", "alias_origen", "panel", "persona", "catalogo_premio",
]
TABLAS_REMOTAS = ["respuesta", "pregunta", "individuo", "cuestionario"]

VERSION_TEXTO = "consentimiento-2026-01"


def _conectar(dsn):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn, row_factory=dict_row, autocommit=False)


@pytest.fixture(scope="session")
def dsn_local():
    dsn = os.environ.get("DSN_LOCAL")
    if not dsn:
        pytest.skip("Sin DSN_LOCAL: correr `source scripts/pg_pruebas.sh` primero.")
    return dsn


@pytest.fixture(scope="session")
def dsn_remoto():
    dsn = os.environ.get("DSN_REMOTO")
    if not dsn:
        pytest.skip("Sin DSN_REMOTO: correr `source scripts/pg_pruebas.sh` primero.")
    return dsn


@pytest.fixture
def local(dsn_local):
    conn = _conectar(dsn_local)
    with conn.cursor() as cur:
        cur.execute(f"truncate {', '.join(TABLAS_LOCALES)} restart identity cascade")
    conn.commit()
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def remota(dsn_remoto):
    """Conexión al store remoto. Se llama `remota` y no `remoto` para no
    tapar al módulo `panel_api.remoto` en los tests que lo importan."""
    conn = _conectar(dsn_remoto)
    with conn.cursor() as cur:
        cur.execute(f"truncate {', '.join(TABLAS_REMOTAS)} restart identity cascade")
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
