#!/usr/bin/env python3
"""La migración de encuestas y cargas unificadas (boveda/0032, D81), con sus
puntos de control.

La 0032 **cambia datos**: crea una encuesta por cada carga existente y una
carga por cada encuesta con respuestas, más las participaciones de las
personas de las cargas. Es el punto de no retorno del despliegue, así que
tiene tres pasos que se corren en este orden (ver «DESPLIEGUE - encuestas y
cargas unificadas»):

    export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
    export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"

    # Punto de control 2: corre la 0032 en una transacción que se deshace y
    # dice qué va a crear. Guarda la foto de «antes» para el paso 3.
    python3 scripts/migrar_encuestas_y_cargas.py prueba --guardar antes.json

    psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0032_encuestas_y_cargas_unificadas.sql
    psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0012_respuesta_carga.sql

    # `respuesta.carga_id` de lo que ya estaba (sin --aplicar es un ensayo).
    python3 scripts/migrar_encuestas_y_cargas.py semantico --aplicar

    # Punto de control 3: toda carga con encuesta, ninguna respuesta sin
    # cuestionario de una encuesta, y los filtros de temáticas y clientes
    # devuelven las mismas personas que antes.
    python3 scripts/migrar_encuestas_y_cargas.py verificar --antes antes.json

`prueba` ejecuta **el mismo archivo** que después se aplica con `psql`: no
hay una segunda versión de la migración que pueda decir otra cosa. No
re-embebe nada ni llama a ninguna API. Sale con 0 si todo cierra, 1 si algo
no, y 2 si no se pudo correr.
"""

import argparse
import json
import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "functions"))

from panel_api import db, semantica  # noqa: E402

MIGRACION = RAIZ / "db" / "boveda" / "0032_encuestas_y_cargas_unificadas.sql"


def _sin_transaccion(sql):
    """El archivo sin su `begin;` y su `commit;`: la transacción la pone el
    script, para poder deshacerla."""
    lineas = [l for l in sql.splitlines() if l.strip().lower() not in ("begin;", "commit;")]
    return "\n".join(lineas)


def _aplicada(conn):
    return bool(db.una(
        conn,
        """select 1 from information_schema.columns
            where table_name = 'carga' and column_name = 'encuesta_id'"""))


def _foto(conn):
    """Lo que tiene que quedar igual: quién tiene cada temática y cada
    cliente. Antes de la 0032 se lee de `persona_tematica` por carga y de
    `v_persona_cliente` por carga; después, por encuesta. Las personas son
    las mismas."""
    tematicas = {}
    for f in db.todas(conn, "select tematica_id, id_persona::text as p "
                            "from persona_tematica"):
        tematicas.setdefault(str(f["tematica_id"]), set()).add(f["p"])
    clientes = {}
    for f in db.todas(conn, "select cliente_id, id_persona::text as p "
                            "from v_persona_cliente"):
        clientes.setdefault(str(f["cliente_id"]), set()).add(f["p"])
    return {"tematicas": {k: sorted(v) for k, v in tematicas.items()},
            "clientes": {k: sorted(v) for k, v in clientes.items()}}


def _conteos(conn):
    return db.una(
        conn,
        """select (select count(*) from encuesta)::int      as encuestas,
                  (select count(*) from carga)::int         as cargas,
                  (select count(*) from participacion)::int as participaciones,
                  (select count(*) from persona_carga)::int as vinculos""")


def _diferencias(antes, despues):
    """Por temática y por cliente, quién está en un lado y no en el otro."""
    salida = []
    for clave in ("tematicas", "clientes"):
        a, d = antes.get(clave, {}), despues.get(clave, {})
        for ident in sorted(set(a) | set(d)):
            sobran = sorted(set(d.get(ident, [])) - set(a.get(ident, [])))
            faltan = sorted(set(a.get(ident, [])) - set(d.get(ident, [])))
            if sobran or faltan:
                salida.append({"filtro": clave, "id": ident,
                               "personas_de_mas": len(sobran),
                               "personas_de_menos": len(faltan)})
    return salida


def _cuestionarios_sin_encuesta(boveda, semantico):
    """Los cuestionarios con respuestas cuyo `ref_estudio` no es el de
    ninguna encuesta (contra la bóveda de esta transacción)."""
    refs = {str(f["ref_estudio"]) for f in db.todas(boveda, "select ref_estudio from encuesta")}
    con_respuestas = db.todas(
        semantico,
        """select c.ref_estudio::text as ref, count(r.id)::int as respuestas
             from cuestionario c
             join pregunta p on p.cuestionario_id = c.id
             join respuesta r on r.pregunta_id = p.id
            group by c.ref_estudio""")
    return [f for f in con_respuestas if f["ref"] not in refs]


def prueba(boveda, semantico, guardar=None):
    if _aplicada(boveda):
        print("La 0032 ya está aplicada en esta bóveda: no hay nada que probar. "
              "Seguí con `semantico` y `verificar`.")
        return 2
    antes_conteos = _conteos(boveda)
    antes = _foto(boveda)
    encuestas_con_respuestas = db.una(
        boveda,
        """select count(*)::int as n from encuesta e
            where exists (select 1 from participacion pa
                           where pa.encuesta_id = e.id and pa.respondio)""")["n"]
    if guardar:
        pathlib.Path(guardar).write_text(json.dumps(antes, indent=1), encoding="utf-8")
        print(f"Foto de «antes» guardada en {guardar}.")

    try:
        with boveda.cursor() as cur:
            cur.execute(_sin_transaccion(MIGRACION.read_text(encoding="utf-8")))
        despues_conteos = _conteos(boveda)
        despues = _foto(boveda)
        sin_encuesta = db.una(boveda, "select count(*)::int as n from carga "
                                      "where encuesta_id is null")["n"]
        cuestionarios = _cuestionarios_sin_encuesta(boveda, semantico)
    finally:
        boveda.rollback()

    creadas_encuestas = despues_conteos["encuestas"] - antes_conteos["encuestas"]
    creadas_cargas = despues_conteos["cargas"] - antes_conteos["cargas"]
    print("Corrida de prueba de la 0032 (no se escribió nada):")
    print(f"  cargas existentes ............ {antes_conteos['cargas']}")
    print(f"  encuestas a crear ............ {creadas_encuestas} "
          f"(una por carga: tiene que ser {antes_conteos['cargas']})")
    print(f"  encuestas con respuestas ..... {encuestas_con_respuestas}")
    print(f"  cargas a crear ............... {creadas_cargas} "
          f"(una por encuesta con respuestas: tiene que ser {encuestas_con_respuestas})")
    print(f"  participaciones a crear ...... "
          f"{despues_conteos['participaciones'] - antes_conteos['participaciones']}")
    print(f"  vínculos persona-carga a crear "
          f"{despues_conteos['vinculos'] - antes_conteos['vinculos']}")
    print(f"  cargas sin encuesta después .. {sin_encuesta}")
    print(f"  cuestionarios sin encuesta ... {len(cuestionarios)}")
    diferencias = _diferencias(antes, despues)
    print(f"  filtros de temáticas y clientes con otras personas: {len(diferencias)}")
    for d in diferencias[:20]:
        print(f"    {d}")
    for c in cuestionarios[:20]:
        print(f"    cuestionario sin encuesta: {c['ref']} ({c['respuestas']} respuestas)")
    cierra = (creadas_encuestas == antes_conteos["cargas"]
              and creadas_cargas == encuestas_con_respuestas
              and sin_encuesta == 0 and not cuestionarios and not diferencias)
    print("Los números cierran." if cierra else
          "Los números NO cierran: corregí la migración antes de aplicarla.")
    return 0 if cierra else 1


def asignar_cargas(boveda, semantico, aplicar=False):
    """`respuesta.carga_id` de lo que ya estaba: cada cuestionario tenía una
    sola carga (la de su encuesta), así que sus respuestas son de ella."""
    if not _aplicada(boveda):
        print("Falta la 0032 en la bóveda: aplicala antes de este paso.")
        return 2
    if not db.una(semantico, """select 1 from information_schema.columns
                                 where table_name = 'respuesta'
                                   and column_name = 'carga_id'"""):
        print("Falta la semantica/0012: aplicala antes de este paso.")
        return 2
    total, por_carga = 0, 0
    for f in db.todas(
            boveda,
            """select min(c.id) as carga_id, e.ref_estudio
                 from carga c join encuesta e on e.id = c.encuesta_id
                group by e.id having count(c.id) = 1"""):
        n = semantica.asignar_carga(semantico, f["ref_estudio"], f["carga_id"])
        total += n
        por_carga += 1 if n else 0
    sin = db.una(semantico, "select count(*)::int as n from respuesta "
                            "where carga_id is null")["n"]
    if aplicar:
        semantico.commit()
    else:
        semantico.rollback()
    print(f"{total} respuesta(s) {'asignadas' if aplicar else 'a asignar'} a "
          f"{por_carga} carga(s). Sin carga {'después' if aplicar else 'si se aplica'}: "
          f"{sin}.")
    if not aplicar:
        print("Ensayo: no se escribió nada. Para aplicar: --aplicar")
    return 0


def verificar(boveda, semantico, antes=None):
    """Punto de control 3. Si falla, se restaura el respaldo: no se despliega
    el código."""
    if not _aplicada(boveda):
        print("La 0032 no está aplicada.")
        return 2
    problemas = []
    sin_encuesta = db.una(boveda, "select count(*)::int as n from carga "
                                  "where encuesta_id is null")["n"]
    if sin_encuesta:
        problemas.append(f"{sin_encuesta} carga(s) sin encuesta")
    cuestionarios = _cuestionarios_sin_encuesta(boveda, semantico)
    if cuestionarios:
        problemas.append(f"{len(cuestionarios)} cuestionario(s) con respuestas sin "
                         f"encuesta en la bóveda")
    sin_carga = db.una(semantico, "select count(*)::int as n from respuesta "
                                  "where carga_id is null")["n"]
    if sin_carga:
        problemas.append(f"{sin_carga} respuesta(s) sin `carga_id` "
                         f"(falta correr `semantico --aplicar`)")
    if antes:
        diferencias = _diferencias(json.loads(pathlib.Path(antes).read_text(
            encoding="utf-8")), _foto(boveda))
        if diferencias:
            problemas.append(f"{len(diferencias)} filtro(s) de temáticas o clientes "
                             f"con otras personas que antes: {diferencias[:5]}")
    else:
        print("Sin --antes no se comparan los filtros de temáticas y clientes.")
    boveda.rollback()
    semantico.rollback()
    for p in problemas:
        print(f"  ✗ {p}")
    if problemas:
        print("La verificación FALLÓ: restaurá el respaldo y no despliegues el código.")
        return 1
    print("✓ Toda carga tiene encuesta, ninguna respuesta queda sin cuestionario "
          "de una encuesta" + (" y los filtros devuelven las mismas personas." if antes
                               else "."))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    pasos = parser.add_subparsers(dest="paso", required=True)
    p = pasos.add_parser("prueba", help="corre la 0032 y la deshace")
    p.add_argument("--guardar", help="archivo donde guardar la foto de «antes»")
    s = pasos.add_parser("semantico", help="llena respuesta.carga_id")
    s.add_argument("--aplicar", action="store_true")
    v = pasos.add_parser("verificar", help="punto de control 3")
    v.add_argument("--antes", help="la foto que guardó `prueba`")
    argumentos = parser.parse_args()

    dsn_boveda = os.environ.get("DSN_BOVEDA", "").strip()
    dsn_semantica = os.environ.get("DSN_SEMANTICA", "").strip()
    if not dsn_boveda or not dsn_semantica:
        print("Faltan DSN_BOVEDA y DSN_SEMANTICA en el entorno.")
        return 2
    with db.conectar(dsn_boveda, "bóveda") as boveda, \
            db.conectar(dsn_semantica, "semántico") as semantico:
        if argumentos.paso == "prueba":
            return prueba(boveda, semantico, argumentos.guardar)
        if argumentos.paso == "semantico":
            return asignar_cargas(boveda, semantico, argumentos.aplicar)
        return verificar(boveda, semantico, argumentos.antes)


if __name__ == "__main__":
    sys.exit(main())
