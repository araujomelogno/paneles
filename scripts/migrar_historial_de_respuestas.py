#!/usr/bin/env python3
"""La migración del historial de respuestas por carga (semantica/0013, D95),
con su corrida de prueba y su verificación.

La 0013 **cambia datos**: crea `respuesta_carga` y le carga una fila por cada
respuesta vigente, en la carga que la escribió por última vez
(`respuesta.carga_id`). Las cargas anteriores a la última no tienen registro
—ese dato nunca se guardó—: el historial empieza completo solo para las
cargas nuevas. Dos pasos, en este orden (ver «DESPLIEGUE - historial de
respuestas por carga»):

    export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"

    # Antes de aplicar: corre la 0013 en una transacción que se deshace y
    # dice cuántas filas de historial va a crear.
    python3 scripts/migrar_historial_de_respuestas.py prueba

    psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0013_historial_de_respuestas.sql

    # Después: toda respuesta vigente con carga tiene su fila de historial.
    python3 scripts/migrar_historial_de_respuestas.py verificar

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

MIGRACION = RAIZ / "db" / "semantica" / "0013_historial_de_respuestas.sql"


def _sin_transaccion(sql):
    """El archivo sin su `begin;` y su `commit;`: la transacción la pone el
    script, para poder deshacerla."""
    lineas = [l for l in sql.splitlines() if l.strip().lower() not in ("begin;", "commit;")]
    return "\n".join(lineas)


def _hay_tabla(conn):
    return bool(db.una(conn, "select to_regclass('respuesta_carga') is not null as hay")["hay"])


def _vigentes_con_carga(conn):
    return db.una(conn, "select count(*)::int as n from respuesta "
                        " where carga_id is not null")["n"]


def sin_historial(conn):
    """Respuestas vigentes con carga que no tienen su fila de historial en esa
    carga. Después de aplicar tienen que ser cero."""
    return db.una(
        conn,
        """select count(*)::int as n from respuesta r
            where r.carga_id is not null
              and not exists (select 1 from respuesta_carga h
                               where h.individuo_id = r.individuo_id
                                 and h.pregunta_id = r.pregunta_id
                                 and h.carga_id = r.carga_id)""")["n"]


def prueba(conn):
    if not db.una(conn, """select 1 as hay from information_schema.columns
                            where table_name = 'respuesta' and column_name = 'carga_id'"""):
        print("Falta la 0012 (respuesta.carga_id): aplicala primero.")
        return 2
    antes = (db.una(conn, "select count(*)::int as n from respuesta_carga")["n"]
             if _hay_tabla(conn) else 0)
    vigentes = _vigentes_con_carga(conn)
    sin_carga = db.una(conn, "select count(*)::int as n from respuesta "
                             " where carga_id is null")["n"]
    try:
        db.ejecutar(conn, _sin_transaccion(MIGRACION.read_text(encoding="utf-8")))
        despues = db.una(conn, "select count(*)::int as n from respuesta_carga")["n"]
        faltan = sin_historial(conn)
    finally:
        conn.rollback()
    print("Corrida de prueba de la 0013 (deshecha: no se escribió nada)")
    print(f"  Respuestas vigentes con carga: {vigentes} · sin carga (anteriores a la "
          f"0012): {sin_carga}")
    print(f"  Va a crear: {despues - antes} fila(s) de historial"
          + (f" (ya había {antes})" if antes else ""))
    cierra = faltan == 0
    print(f"  Respuestas con carga sin su fila de historial después de aplicar: {faltan}"
          + ("" if cierra else "  ← NO CIERRA"))
    return 0 if cierra else 1


def verificar(conn):
    if not _hay_tabla(conn):
        print("La 0013 no está aplicada (falta respuesta_carga).")
        return 2
    faltan = sin_historial(conn)
    filas = db.una(conn, "select count(*)::int as n from respuesta_carga")["n"]
    print("Verificación de la 0013")
    print(f"  Filas de historial: {filas} · respuestas vigentes con carga: "
          f"{_vigentes_con_carga(conn)}")
    print(f"  Respuestas con carga sin su fila de historial: {faltan}")
    print("  Cierra." if faltan == 0
          else "  NO CIERRA: no despliegues el código hasta resolverlo.")
    return 0 if faltan == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    pasos = parser.add_subparsers(dest="paso", required=True)
    pasos.add_parser("prueba", help="corre la 0013 y la deshace")
    pasos.add_parser("verificar", help="después de aplicar la 0013")
    argumentos = parser.parse_args()
    dsn = os.environ.get("DSN_SEMANTICA", "").strip()
    if not dsn:
        print("Falta DSN_SEMANTICA en el entorno.")
        return 2
    with db.conectar(dsn, "semántico") as semantico:
        return {"prueba": prueba, "verificar": verificar}[argumentos.paso](semantico)


if __name__ == "__main__":
    sys.exit(main())
