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
def conectar(dsn):
    """Conexión con filas como dict y transacción explícita."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        yield conn


@contextlib.contextmanager
def local(cfg=None):
    cfg = cfg or config.cargar()
    with conectar(cfg.dsn_local) as conn:
        yield conn


@contextlib.contextmanager
def remoto(cfg=None):
    cfg = cfg or config.cargar()
    with conectar(cfg.dsn_remoto) as conn:
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
