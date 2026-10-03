"""Cuando una conexión falla, el error dice **a cuál de los dos stores**.

Salió de una falla real en producción: la función que procesa un lote murió
con

    connection to server at "172.26.0.3", port 5432 failed:
    server closed the connection unexpectedly

y nada más. Con dos instancias de Cloud SQL distintas, saber cuál de las dos
no contesta es la diferencia entre mirar el conector de VPC y mirar la base
equivocada; averiguarlo costó buscar la IP privada de cada instancia.

Y lo que **no** puede aparecer nunca es el DSN: lleva la contraseña.
"""

import psycopg
import pytest

from panel_api import db

# Loopback y un puerto donde no escucha nadie: el intento muere enseguida
# con «connection refused», sin esperar el timeout de SYN que sí se come un
# destino inalcanzable de verdad.
CLAVE = "clave-secretisima"
DSN_MUERTO = f"postgresql://app:{CLAVE}@127.0.0.1:1/nada?connect_timeout=2"


def test_el_error_nombra_el_store():
    with pytest.raises(psycopg.OperationalError) as fallo:
        with db.conectar(DSN_MUERTO, "bóveda"):
            pass
    assert "bóveda" in str(fallo.value)


def test_el_error_no_filtra_el_dsn():
    with pytest.raises(psycopg.OperationalError) as fallo:
        with db.conectar(DSN_MUERTO, "semántico"):
            pass
    texto = str(fallo.value)
    assert CLAVE not in texto
    assert "postgresql://" not in texto


def test_sin_nombre_el_error_queda_como_estaba():
    """`conectar(dsn)` a secas no cambia de forma: el nombre es opcional."""
    with pytest.raises(psycopg.OperationalError) as fallo:
        with db.conectar(DSN_MUERTO):
            pass
    assert "no se pudo conectar al store" not in str(fallo.value)


def test_un_fallo_trabajando_no_se_disfraza_de_fallo_de_conexion(dsn_boveda):
    """Solo el `connect` se etiqueta.

    Un `OperationalError` que pasa con la conexión ya abierta —la que se
    corta a mitad de una ingesta larga— no es un problema de red, y decir que
    sí mandaría a revisar el conector de VPC para nada.
    """
    with pytest.raises(psycopg.OperationalError) as fallo:
        with db.conectar(dsn_boveda, "bóveda"):
            raise psycopg.OperationalError("se cortó a mitad del lote")
    assert "no se pudo conectar" not in str(fallo.value)
    assert "se cortó a mitad del lote" in str(fallo.value)
