#!/usr/bin/env python3
"""La migración de las cargas incrementales (boveda/0037, D94), con su corrida
de prueba, su verificación y el listado de lo vacío que quedó de
cancelaciones anteriores (D92).

La 0037 **cambia datos**: en cada encuesta con más de una carga, el vínculo
persona ↔ carga de la **primera** carga conserva su origen y los de las
siguientes pasan a `repetida` (R-INC.2). Tres pasos, en este orden (ver
«DESPLIEGUE - cancelar una carga, carga mixta y cargas incrementales»):

    export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

    # Antes de aplicar: las cargas y encuestas vacías que dejaron las
    # cancelaciones de antes. No se borran solas: lo decide el equipo.
    python3 scripts/migrar_cargas_incrementales.py vacias

    # Corre la 0037 en una transacción que se deshace y dice, por encuesta,
    # cuántos vínculos pasan a `repetida`.
    python3 scripts/migrar_cargas_incrementales.py prueba

    psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0037_cancelar_carga_mixta_e_incrementales.sql

    # Después: ninguna persona tiene más de un vínculo que no sea `repetida`
    # dentro de una misma encuesta.
    python3 scripts/migrar_cargas_incrementales.py verificar

`prueba` ejecuta **el mismo archivo** que después se aplica con `psql`: no hay
una segunda versión de la migración que pueda decir otra cosa. Sale con 0 si
todo cierra, 1 si algo no, y 2 si no se pudo correr.
"""

import argparse
import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "functions"))

from panel_api import db  # noqa: E402

MIGRACION = RAIZ / "db" / "boveda" / "0037_cancelar_carga_mixta_e_incrementales.sql"

# Dentro de una encuesta, cada persona tiene a lo sumo un vínculo que no es
# `repetida`: el de su primera carga. Es la consulta del DoD de R-INC.
CONSULTA_DUPLICADOS = """
select c.encuesta_id, pc.id_persona, count(*)::int as vinculos
  from persona_carga pc join carga c on c.id = pc.carga_id
 where pc.origen <> 'repetida'
 group by c.encuesta_id, pc.id_persona
having count(*) > 1
"""


def _sin_transaccion(sql):
    """El archivo sin su `begin;` y su `commit;`: la transacción la pone el
    script, para poder deshacerla."""
    lineas = [l for l in sql.splitlines() if l.strip().lower() not in ("begin;", "commit;")]
    return "\n".join(lineas)


def _origenes_por_encuesta(conn):
    return {
        f["encuesta_id"]: f
        for f in db.todas(
            conn,
            """select c.encuesta_id, e.nombre as encuesta,
                      count(distinct c.id)::int as cargas,
                      count(*)::int as vinculos,
                      count(*) filter (where pc.origen = 'repetida')::int as repetidos
                 from persona_carga pc
                 join carga c on c.id = pc.carga_id
                 join encuesta e on e.id = c.encuesta_id
                group by c.encuesta_id, e.nombre""")
    }


def _duplicados(conn):
    return db.una(conn, f"select count(*)::int as n from ({CONSULTA_DUPLICADOS}) d")["n"]


def vacias(conn):
    """Las cargas sin ninguna persona vinculada ni trabajo de ingesta, y las
    encuestas independientes sin cargas ni participaciones: lo que dejaron las
    cancelaciones de antes de la 0037. Solo lista."""
    cargas = db.todas(
        conn,
        """select c.id, c.nombre, c.encuesta_id, e.nombre as encuesta, c.creado_en
             from carga c join encuesta e on e.id = c.encuesta_id
            where not exists (select 1 from persona_carga pc where pc.carga_id = c.id)
              and not exists (select 1 from ingesta_trabajo t
                               where t.destino_tipo = 'carga' and t.destino_id = c.id)
            order by c.creado_en""")
    encuestas = db.todas(
        conn,
        """select e.id, e.nombre, e.origen, e.creado_en
             from encuesta e
            where e.tipo = 'independiente'
              and not exists (select 1 from participacion pa where pa.encuesta_id = e.id)
              and not exists (select 1 from carga c
                               where c.encuesta_id = e.id
                                 and exists (select 1 from persona_carga pc
                                              where pc.carga_id = c.id))
            order by e.creado_en""")
    print(f"Cargas vacías (sin personas ni trabajo de ingesta): {len(cargas)}")
    for c in cargas:
        print(f"  carga {c['id']} «{c['nombre']}» · encuesta {c['encuesta_id']} "
              f"«{c['encuesta']}» · creada {c['creado_en']:%Y-%m-%d %H:%M}")
    print(f"Encuestas independientes sin respuestas: {len(encuestas)}")
    for e in encuestas:
        print(f"  encuesta {e['id']} «{e['nombre']}» · origen {e['origen']} · "
              f"creada {e['creado_en']:%Y-%m-%d %H:%M}")
    print("No se borra nada. Si el equipo decide borrarlas: la carga desde la "
          "sección Cargas (Borrar) y la encuesta desde Encuestas (Borrar).")
    return 0


def prueba(conn):
    if not db.una(conn, "select to_regclass('persona_carga') is not null as hay")["hay"]:
        print("Falta la 0024 (persona_carga): aplicala primero.")
        return 2
    antes = _origenes_por_encuesta(conn)
    try:
        db.ejecutar(conn, _sin_transaccion(MIGRACION.read_text(encoding="utf-8")))
        despues = _origenes_por_encuesta(conn)
        duplicados = _duplicados(conn)
    finally:
        conn.rollback()
    print("Corrida de prueba de la 0037 (deshecha: no se escribió nada)")
    cambian = 0
    for encuesta_id, fila in sorted(despues.items()):
        nuevos = fila["repetidos"] - antes.get(encuesta_id, {}).get("repetidos", 0)
        if not nuevos:
            continue
        cambian += nuevos
        print(f"  encuesta {encuesta_id} «{fila['encuesta']}» · {fila['cargas']} "
              f"cargas · {fila['vinculos']} vínculos · pasan a repetida: {nuevos}")
    print(f"  Total de vínculos que pasan a `repetida`: {cambian}")
    cierra = duplicados == 0
    print(f"  Personas con más de un vínculo no repetida en una encuesta después "
          f"de aplicar: {duplicados}" + ("" if cierra else "  ← NO CIERRA"))
    return 0 if cierra else 1


def verificar(conn):
    if not db.una(conn, """select 1 as hay from information_schema.columns
                            where table_name = 'carga' and column_name = 'provisoria'"""):
        print("La 0037 no está aplicada (falta carga.provisoria).")
        return 2
    duplicados = _duplicados(conn)
    total = sum(f["repetidos"] for f in _origenes_por_encuesta(conn).values())
    print("Verificación de la 0037")
    print(f"  Vínculos `repetida`: {total}")
    print(f"  Personas con más de un vínculo no repetida en una encuesta: {duplicados}")
    print("  Cierra." if duplicados == 0
          else "  NO CIERRA: no despliegues el código hasta resolverlo.")
    return 0 if duplicados == 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    pasos = parser.add_subparsers(dest="paso", required=True)
    pasos.add_parser("vacias", help="lista lo vacío que dejaron las cancelaciones")
    pasos.add_parser("prueba", help="corre la 0037 y la deshace")
    pasos.add_parser("verificar", help="después de aplicar la 0037")
    argumentos = parser.parse_args()
    dsn_boveda = os.environ.get("DSN_BOVEDA", "").strip()
    if not dsn_boveda:
        print("Falta DSN_BOVEDA en el entorno.")
        return 2
    with db.conectar(dsn_boveda, "bóveda") as boveda:
        return {"vacias": vacias, "prueba": prueba,
                "verificar": verificar}[argumentos.paso](boveda)


if __name__ == "__main__":
    sys.exit(main())
