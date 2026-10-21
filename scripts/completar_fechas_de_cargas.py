#!/usr/bin/env python3
"""Completa la fecha del cuestionario de las cargas ya ingestadas.

Hasta la corrección del PEDIDO de la fecha del estudio, la ingesta de una
carga creaba su cuestionario sin fecha (`cuestionario.fecha_campo` vacía) y
la ficha mostraba «sin fecha de campo» en todas sus respuestas. Este script
copia `carga.fecha_estudio` (bóveda) a `cuestionario.fecha_campo`
(semántico), por `ref_estudio`, **solo donde esté vacía**.

Es una operación única entre las dos bases. Sin `--aplicar` es un ensayo:
dice qué completaría y no escribe nada. Se puede correr dos veces: la
segunda no encuentra nada que completar.

    export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
    export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
    python3 scripts/completar_fechas_de_cargas.py            # ensayo
    python3 scripts/completar_fechas_de_cargas.py --aplicar
"""

import argparse
import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "functions"))

from panel_api import cargas, db  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aplicar", action="store_true",
                        help="escribir; sin esto es un ensayo")
    argumentos = parser.parse_args()

    dsn_boveda = os.environ.get("DSN_BOVEDA", "").strip()
    dsn_semantica = os.environ.get("DSN_SEMANTICA", "").strip()
    if not dsn_boveda or not dsn_semantica:
        print("Faltan DSN_BOVEDA y DSN_SEMANTICA en el entorno.")
        return 2

    with db.conectar(dsn_boveda, "bóveda") as boveda, \
            db.conectar(dsn_semantica, "semántico") as semantica:
        salida = cargas.completar_fechas(boveda, semantica)
        for c in salida["completadas"]:
            print(f"  {'completada' if argumentos.aplicar else 'a completar'}: "
                  f"carga {c['id']} · {c['nombre']}")
        print(f"{salida['revisadas']} carga(s) con fecha del estudio · "
              f"{len(salida['completadas'])} "
              f"{'completada(s)' if argumentos.aplicar else 'a completar'}")
        if argumentos.aplicar:
            semantica.commit()
        else:
            semantica.rollback()
            print("Ensayo: no se escribió nada. Para aplicar: --aplicar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
