#!/usr/bin/env python3
"""La migración de la fecha de campo obligatoria (boveda/0038, D96), con su
corrida de prueba y su verificación.

La 0038 **cambia datos**: a cada encuesta sin fecha de campo le pone la fecha
de su **primera carga** (o la de creación, si no tiene cargas), la anota en
`encuesta_fecha_completada` y después vuelve la columna `not null`. Tres
pasos (ver «DESPLIEGUE - Alchemer, participación y bandeja de WhatsApp»):

    export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

    # Antes de aplicar: corre la 0038 en una transacción que se deshace y
    # lista las encuestas que va a completar, con la fecha y de dónde sale.
    python3 scripts/migrar_fecha_de_campo.py prueba

    psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0038_alchemer_participacion_y_bandeja_whatsapp.sql

    # Después: ninguna encuesta sin fecha, y la lista para revisar.
    python3 scripts/migrar_fecha_de_campo.py verificar

`prueba` ejecuta **el mismo archivo** que después se aplica con `psql`. Sale
con 0 si todo cierra, 1 si algo no, y 2 si no se pudo correr.
"""

import argparse
import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "functions"))

from panel_api import db  # noqa: E402

MIGRACION = RAIZ / "db" / "boveda" / "0038_alchemer_participacion_y_bandeja_whatsapp.sql"

ETIQUETA_FUENTE = {"primera_carga": "fecha de su primera carga",
                   "creacion": "fecha de creación (no tiene cargas)"}


def _sin_transaccion(sql):
    """El archivo sin su `begin;` y su `commit;`: la transacción la pone el
    script, para poder deshacerla."""
    lineas = [l for l in sql.splitlines() if l.strip().lower() not in ("begin;", "commit;")]
    return "\n".join(lineas)


def _sin_fecha(conn):
    return db.una(conn, "select count(*)::int as n from encuesta where fecha_campo is null")["n"]


def _completadas(conn):
    return db.todas(
        conn,
        """select f.encuesta_id, e.nombre, f.fecha_campo, f.fuente
             from encuesta_fecha_completada f join encuesta e on e.id = f.encuesta_id
            order by f.encuesta_id""")


def _listar(filas):
    for f in filas:
        print(f"  encuesta {f['encuesta_id']} «{f['nombre']}» → {f['fecha_campo']} "
              f"({ETIQUETA_FUENTE.get(f['fuente'], f['fuente'])})")


def prueba(conn):
    antes = _sin_fecha(conn)
    try:
        db.ejecutar(conn, _sin_transaccion(MIGRACION.read_text(encoding="utf-8")))
        completadas = _completadas(conn)
        despues = _sin_fecha(conn)
    finally:
        conn.rollback()
    print("Corrida de prueba de la 0038 (deshecha: no se escribió nada)")
    print(f"  Encuestas sin fecha de campo hoy: {antes}")
    print(f"  Va a completar: {len(completadas)}")
    _listar(completadas)
    cierra = despues == 0
    print(f"  Encuestas sin fecha después de aplicar: {despues}"
          + ("" if cierra else "  ← NO CIERRA"))
    print("Mandá esta lista al equipo: cada fecha se corrige desde «Datos del estudio».")
    return 0 if cierra else 1


def verificar(conn):
    if not db.una(conn, "select to_regclass('encuesta_fecha_completada') is not null as hay")["hay"]:
        print("La 0038 no está aplicada (falta encuesta_fecha_completada).")
        return 2
    sin_fecha = _sin_fecha(conn)
    nula = db.una(conn, """select is_nullable = 'YES' as nula from information_schema.columns
                            where table_name = 'encuesta' and column_name = 'fecha_campo'""")["nula"]
    completadas = _completadas(conn)
    print("Verificación de la 0038")
    print(f"  Encuestas sin fecha de campo: {sin_fecha} · columna obligatoria: "
          f"{'no' if nula else 'sí'}")
    print(f"  Completadas por la migración (para revisar): {len(completadas)}")
    _listar(completadas)
    cierra = sin_fecha == 0 and not nula
    print("  Cierra." if cierra else "  NO CIERRA: no despliegues el código hasta resolverlo.")
    return 0 if cierra else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    pasos = parser.add_subparsers(dest="paso", required=True)
    pasos.add_parser("prueba", help="corre la 0038 y la deshace")
    pasos.add_parser("verificar", help="después de aplicar la 0038")
    argumentos = parser.parse_args()
    dsn = os.environ.get("DSN_BOVEDA", "").strip()
    if not dsn:
        print("Falta DSN_BOVEDA en el entorno.")
        return 2
    with db.conectar(dsn, "bóveda") as boveda:
        return {"prueba": prueba, "verificar": verificar}[argumentos.paso](boveda)


if __name__ == "__main__":
    sys.exit(main())
