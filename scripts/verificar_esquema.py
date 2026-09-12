#!/usr/bin/env python3
"""Comprueba que las dos bases tengan aplicadas todas las migraciones.

Es la misma verificación que hace la aplicación en Cumplimiento → Esquema y en
`GET /api/diagnostico/esquema`, pero desde la línea de comandos: sirve **antes**
de desplegar, o cuando lo que se quiere es revisar la base sin pasar por la app.

Las migraciones se aplican a mano contra cada instancia de Cloud SQL, y una que
no se aplicó no rompe el arranque: rompe la primera pantalla que la necesita,
semanas después. Este script existe para que eso se vea el día que pasa.

    # con el Auth Proxy abierto contra cada instancia
    cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda   --port 5432 &
    cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &

    export DSN_BOVEDA="postgresql://app_paneles:CLAVE@127.0.0.1:5432/paneles_boveda"
    export DSN_SEMANTICA="postgresql://app_paneles:CLAVE@127.0.0.1:5433/paneles_semantica"
    python3 scripts/verificar_esquema.py

Sale con 0 si las dos bases están al día y con 1 si falta algo, así que se
puede encadenar en un script de despliegue.

Con `--sql` no se conecta a ninguna base: imprime una consulta para pegar
dentro de una sesión de `psql` ya abierta (por ejemplo la de
`gcloud sql connect`). Sirve cuando no se puede correr Python contra la base
pero sí se está adentro de la sesión —o cuando no hay `psycopg` instalado, ya
que `--sql` no se conecta a ninguna base y no necesita el driver—. Como cada
sesión de `psql` está abierta contra una sola base, `--sql boveda` y
`--sql semantica` imprimen la consulta de ese store solo:

    python3 scripts/verificar_esquema.py --sql semantica | psql "$DSN_SEMANTICA"

Devuelve una fila por migración faltante, y ninguna fila si está al día.

Ojo con esa tubería: es `psql` quien se conecta, no este script, así que el
DSN tiene que estar en el entorno y el Auth Proxy abierto. Si `$DSN_SEMANTICA`
está vacío, `psql` cae en sus valores por omisión —el socket local— y falla
con «connection to server on socket "/tmp/.s.PGSQL.5432" failed». Eso no es la
base remota diciendo nada: es que nunca se la llamó.

Y en una máquina que sí tenga un Postgres local, el DSN vacío es peor que un
error: `psql` entra a la base `postgres`, que no tiene ninguna de estas
tablas, y la consulta contesta que faltan todas las migraciones. Por eso el
resultado trae una columna `base` con `current_database()`: si no dice
`paneles_boveda` o `paneles_semantica`, no hay que leer las otras columnas.
"""

import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "functions"))

from panel_api import esquema  # noqa: E402

ROJO, AMARILLO, VERDE, GRIS, FIN = (
    "\033[31m", "\033[33m", "\033[32m", "\033[90m", "\033[0m"
)

PUERTOS = {"boveda": 5432, "semantica": 5433}


def consulta_sql(store):
    """Una consulta autocontenida que dice qué falta en ese store.

    Se genera de la misma lista que usa la aplicación, así que no se
    desactualiza cuando se agrega una migración.
    """
    filas = []
    for archivo, objetos in esquema.STORES[store]:
        for objeto in objetos:
            tabla, _, columna = objeto.partition(".")
            filas.append(
                f"    ('{archivo}', '{objeto}', '{tabla}', "
                f"{'NULL' if not columna else repr(columna).replace(chr(39), chr(39))})"
            )
    valores = ",\n".join(filas).replace("'None'", "NULL")
    return f"""-- Migraciones que faltan en el store «{store}».
-- Sin filas = está al día.
-- La columna «base» dice contra qué base se corrió: si no es
-- paneles_{store}, el resultado no significa nada.
with esperado (migracion, objeto, relacion, columna) as (values
{valores}
)
select current_database() as base, e.migracion, e.objeto
  from esperado e
 where not exists (
         select 1 from information_schema.columns c
          where c.table_schema = 'public'
            and c.table_name = e.relacion
            and (e.columna is null or c.column_name = e.columna))
 order by e.migracion, e.objeto;"""


def revisar(store, dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return esquema.verificar(conn, esquema.STORES[store])


def informar(store, estado):
    print(f"\n  {store}")
    for migracion in estado["migraciones"]:
        if migracion["aplicada"]:
            print(f"    {VERDE}✓{FIN} {migracion['migracion']}")
        else:
            print(f"    {ROJO}✗{FIN} {migracion['migracion']}")
            for objeto in migracion["faltantes"]:
                para_que = esquema.PARA_QUE.get(objeto)
                nota = f"  {GRIS}({para_que}){FIN}" if para_que else ""
                print(f"        falta {objeto}{nota}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sql", nargs="?", const="ambos", choices=("ambos", *esquema.STORES),
        help="imprime la consulta para pegar en psql, sin conectarse a nada; "
             "con un store imprime solo la de ese store",
    )
    argumentos = parser.parse_args()

    if argumentos.sql:
        stores = esquema.STORES if argumentos.sql == "ambos" else (argumentos.sql,)
        for store in stores:
            if len(stores) > 1:
                print(f"\n{'═' * 70}")
            print(consulta_sql(store))
        return 0

    faltan_dsn = [
        variable for variable in ("DSN_BOVEDA", "DSN_SEMANTICA")
        if not os.environ.get(variable, "").strip()
    ]
    if faltan_dsn:
        print(f"{ROJO}Falta {' y '.join(faltan_dsn)} en el entorno.{FIN}")
        print("Ver el encabezado de este archivo, o usar --sql para revisarlo "
              "desde una sesión de psql ya abierta.")
        return 2

    try:
        import psycopg  # noqa: F401
    except ModuleNotFoundError:
        print(f"{ROJO}Falta el driver de Postgres para conectarse.{FIN}")
        print('  pip install "psycopg[binary]"')
        print(f"\n{AMARILLO}O, sin instalar nada:{FIN} --sql imprime la misma "
              "verificación como\nconsulta suelta, para pegar en una sesión de "
              "psql ya abierta.\n")
        print("  python3 scripts/verificar_esquema.py --sql semantica")
        print("  python3 scripts/verificar_esquema.py --sql boveda")
        return 2

    print("Esquema de las dos bases")
    pendientes = []
    for store in esquema.STORES:
        try:
            estado = revisar(store, os.environ[f"DSN_{store.upper()}"])
        except Exception as error:  # noqa: BLE001
            print(f"\n  {store}\n    {ROJO}no se pudo conectar:{FIN} {error}")
            return 2
        informar(store, estado)
        pendientes += [
            (store, m["migracion"]) for m in estado["migraciones"] if not m["aplicada"]
        ]

    if not pendientes:
        print(f"\n{VERDE}Las dos bases están al día.{FIN}")
        return 0

    print(f"\n{ROJO}Faltan {len(pendientes)} migración(es).{FIN} Para aplicarlas, "
          f"con el Auth Proxy abierto:\n")
    for store, migracion in pendientes:
        print(f"  psql -h 127.0.0.1 -p {PUERTOS[store]} -U app_paneles "
              f"-d paneles_{store} \\\n       -v ON_ERROR_STOP=1 "
              f"-f db/{store}/{migracion}")
    print(f"\n{AMARILLO}El orden importa: se aplican de menor a mayor.{FIN}")
    return 1


if __name__ == "__main__":
    try:
        codigo = main()
        sys.stdout.flush()
    except BrokenPipeError:
        # El otro extremo de la tubería cerró antes de tiempo: `| psql` que no
        # pudo conectarse, o un `| head` que ya tuvo suficiente. No es una
        # falla de este script, y el traceback que Python imprime al cerrar
        # tapa el error de verdad —el del otro comando—, que es el que hay que
        # leer. Se redirige el descriptor a /dev/null para que el flush del
        # intérprete no vuelva a fallar.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        codigo = 1
    sys.exit(codigo)
