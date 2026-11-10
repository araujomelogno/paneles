#!/usr/bin/env python3
"""La migración de la sección «WhatsApp Flow» a los mensajes (boveda/0036,
D87), con su corrida de prueba y su verificación.

La 0036 **cambia datos**: por cada encuesta que usó la sección vieja crea un
mensaje «WhatsApp Flow» con su plantilla y su Flow, y por cada participación
enviada (o fallida) crea su fila en `envio`. Sin eso, «No enviado aún» le
repetiría el Flow a quien ya lo recibió. Dos pasos, en este orden (ver
«DESPLIEGUE - mensajes, canales, selección aleatoria y audiencias»):

    export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

    # Antes de aplicar: corre la 0036 en una transacción que se deshace y
    # dice cuántos mensajes y envíos va a crear.
    python3 scripts/migrar_flow_a_mensajes.py prueba

    psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0036_mensajes_canales_seleccion_y_audiencias.sql

    # Después: toda participación enviada tiene su fila en `envio`, y nadie
    # quedó en «No enviado aún» habiendo recibido el Flow.
    python3 scripts/migrar_flow_a_mensajes.py verificar

`prueba` ejecuta **el mismo archivo** que después se aplica con `psql`: no hay
una segunda versión de la migración que pueda decir otra cosa. No llama a
Meta ni manda nada. Sale con 0 si todo cierra, 1 si algo no, y 2 si no se
pudo correr.
"""

import argparse
import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "functions"))

from panel_api import db  # noqa: E402

MIGRACION = RAIZ / "db" / "boveda" / "0036_mensajes_canales_seleccion_y_audiencias.sql"
MARCA = "migracion_0036"


def _sin_transaccion(sql):
    """El archivo sin su `begin;` y su `commit;`: la transacción la pone el
    script, para poder deshacerla."""
    lineas = [l for l in sql.splitlines() if l.strip().lower() not in ("begin;", "commit;")]
    return "\n".join(lineas)


def _pendiente(conn):
    """Lo que la sección vieja dejó registrado en `participacion`."""
    return db.una(
        conn,
        """select count(distinct encuesta_id) filter (where envio_estado is not null)::int
                                                              as encuestas_con_envios,
                  count(*) filter (where envio_estado = 'enviado')::int  as enviados,
                  count(*) filter (where envio_estado = 'fallido')::int  as fallidos
             from participacion""")


def _migrado(conn):
    if not db.una(conn, "select to_regclass('encuesta_mensaje') is not null as hay")["hay"]:
        return {"mensajes": 0, "envios": 0, "aceptados": 0, "fallidos": 0}
    return db.una(
        conn,
        """select (select count(*) from encuesta_mensaje where creado_por = %s)::int
                                                                  as mensajes,
                  count(*)::int                                   as envios,
                  count(*) filter (where e.estado = 'aceptado')::int as aceptados,
                  count(*) filter (where e.estado = 'fallido')::int  as fallidos
             from envio e where e.lanzado_por = %s""", (MARCA, MARCA))


def _sin_fila(conn):
    """Participaciones enviadas o fallidas sin su fila en `envio` del mensaje
    migrado: tienen que ser cero después de aplicar."""
    return db.una(
        conn,
        """select count(*)::int as n
             from participacion pa
            where pa.envio_estado in ('enviado', 'fallido')
              and not exists (
                    select 1 from envio e
                      join encuesta_mensaje m on m.id = e.encuesta_mensaje_id
                     where m.encuesta_id = pa.encuesta_id
                       and m.creado_por = %s
                       and e.id_persona = pa.id_persona)""", (MARCA,))["n"]


def _requisito(conn):
    """La 0036 va sobre la 0035: sin `encuesta_mensaje` no hay dónde migrar."""
    return bool(db.una(conn, "select to_regclass('envio') is not null as hay")["hay"])


def prueba(conn):
    if not _requisito(conn):
        print("Falta la 0035 (encuesta_mensaje y envio): aplicala primero.")
        return 2
    antes = _pendiente(conn)
    ya = _migrado(conn)
    try:
        db.ejecutar(conn, _sin_transaccion(MIGRACION.read_text(encoding="utf-8")))
        despues = _migrado(conn)
        sin_fila = _sin_fila(conn)
    finally:
        conn.rollback()
    mensajes = despues["mensajes"] - ya["mensajes"]
    envios = despues["envios"] - ya["envios"]
    print("Corrida de prueba de la 0036 (deshecha: no se escribió nada)")
    print(f"  En participacion: {antes['encuestas_con_envios']} encuesta(s) con envíos "
          f"de la sección vieja · {antes['enviados']} enviados · {antes['fallidos']} fallidos")
    print(f"  Va a crear: {mensajes} mensaje(s) «WhatsApp Flow» y {envios} envío(s) "
          f"({despues['aceptados'] - ya['aceptados']} aceptados, "
          f"{despues['fallidos'] - ya['fallidos']} fallidos)")
    if ya["mensajes"]:
        print(f"  Ya migrado antes: {ya['mensajes']} mensaje(s) y {ya['envios']} envío(s); "
              f"no se vuelven a crear.")
    cierra = sin_fila == 0
    print(f"  Participaciones enviadas sin su fila en `envio` después de aplicar: {sin_fila}"
          + ("" if cierra else "  ← NO CIERRA"))
    return 0 if cierra else 1


def verificar(conn):
    if not db.una(conn, """select 1 as hay from information_schema.columns
                            where table_name = 'encuesta_mensaje'
                              and column_name = 'boton'"""):
        print("La 0036 no está aplicada (falta encuesta_mensaje.boton).")
        return 2
    antes = _pendiente(conn)
    migrado = _migrado(conn)
    sin_fila = _sin_fila(conn)
    print("Verificación de la 0036")
    print(f"  Mensajes migrados: {migrado['mensajes']} · envíos: {migrado['envios']} "
          f"({migrado['aceptados']} aceptados, {migrado['fallidos']} fallidos)")
    print(f"  En participacion: {antes['enviados']} enviados · {antes['fallidos']} fallidos")
    print(f"  Participaciones enviadas sin su fila en `envio`: {sin_fila}")
    cierra = (sin_fila == 0 and migrado["aceptados"] >= antes["enviados"]
              and migrado["fallidos"] >= antes["fallidos"])
    print("  Cierra." if cierra else "  NO CIERRA: no despliegues el código hasta resolverlo.")
    return 0 if cierra else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    pasos = parser.add_subparsers(dest="paso", required=True)
    pasos.add_parser("prueba", help="corre la 0036 y la deshace")
    pasos.add_parser("verificar", help="después de aplicar la 0036")
    argumentos = parser.parse_args()
    dsn_boveda = os.environ.get("DSN_BOVEDA", "").strip()
    if not dsn_boveda:
        print("Falta DSN_BOVEDA en el entorno.")
        return 2
    with db.conectar(dsn_boveda, "bóveda") as boveda:
        if argumentos.paso == "prueba":
            return prueba(boveda)
        return verificar(boveda)


if __name__ == "__main__":
    sys.exit(main())
