"""Conexiones a los dos stores.

En producción cada DSN apunta a su instancia de Cloud SQL (a través del
socket del conector o de la IP privada de la VPC). En desarrollo y en las
pruebas, a un Postgres local. El código de dominio no conoce el DSN: recibe
una conexión ya abierta.
"""

import contextlib

import psycopg
from psycopg.rows import dict_row

from . import config


@contextlib.contextmanager
def conectar(dsn, nombre=None):
    """Conexión con filas como dict y transacción explícita.

    `nombre` es cuál de los dos stores es, y existe para el día que la
    conexión falle en producción. El error de psycopg nombra la IP y nada
    más; con dos instancias distintas, saber cuál de las dos no contesta es
    la diferencia entre mirar el conector de VPC y mirar la base
    equivocada. El DSN **no** se incluye nunca: lleva la contraseña.
    """
    try:
        conn = psycopg.connect(dsn, row_factory=dict_row)
    except psycopg.OperationalError as error:
        if not nombre:
            raise
        raise psycopg.OperationalError(
            f"no se pudo conectar al store «{nombre}»: {error}") from error
    # Solo el `connect` va adentro del `try`: un `OperationalError` que pase
    # trabajando —la conexión que se corta a mitad de una ingesta larga— no
    # es un fallo de conexión y etiquetarlo así mandaría a mirar la red.
    with conn:
        yield conn


@contextlib.contextmanager
def boveda(cfg=None):
    cfg = cfg or config.cargar()
    with conectar(cfg.dsn_boveda, "bóveda") as conn:
        yield conn


@contextlib.contextmanager
def semantica(cfg=None):
    cfg = cfg or config.cargar()
    with conectar(cfg.dsn_semantica, "semántico") as conn:
        yield conn


def una(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def todas(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def ejecutar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount
