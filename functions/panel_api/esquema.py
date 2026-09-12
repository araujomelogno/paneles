"""Verificación de que el esquema desplegado tiene lo que el código espera.

Las migraciones se aplican a mano contra cada instancia de Cloud SQL (ver los
manuales de despliegue). Eso deja abierta una falla silenciosa: si alguna no se
aplicó, el sistema arranca igual, la mayoría de las pantallas funcionan, y la
que necesitaba el objeto que falta se cae con un error de Postgres que solo
aparece en los logs del servidor. Al usuario le llega «Error interno del
servidor», que no le dice nada y no le permite hacer nada.

Este módulo cierra ese hueco por los dos lados:

* `verificar` compara lo que hay en la base con lo que el código espera, y
  devuelve qué falta y qué migración lo crea. Se expone en una ruta y se
  muestra en la pantalla de Cumplimiento.
* `explicar_error` traduce el error de Postgres a una frase accionable, para
  que el 500 diga cuál es el archivo que hay que aplicar en lugar de callarse.

**Por qué la lista está acá y no se lee de los `.sql`.** Sería preferible
derivarla de las propias migraciones, que es la fuente de verdad. Pero la
carpeta `db/` no se despliega con la función —el `source` de Cloud Functions es
`functions/`— así que en producción esos archivos no existen. La lista se
declara acá y una prueba comprueba que coincida con lo que las migraciones
crean realmente, que es la forma de que no se desactualice.
"""

import re

# Migración → objetos que crea. Un objeto con punto (`tabla.columna`) es una
# columna; el resto son tablas o vistas, que a estos efectos son lo mismo:
# algo que tiene que existir para que una consulta no falle.
MIGRACIONES_BOVEDA = (
    ("0001_init.sql", (
        "persona", "alias_origen", "v_demografia", "panel", "membresia",
        "consentimiento", "encuesta", "participacion", "objetivo_composicion",
        "puntos_movimiento", "catalogo_premio", "canje",
    )),
    ("0002_revision_alta.sql", ("alta_en_revision",)),
    ("0003_baja_persona.sql", ("persona_borrada",)),
    ("0004_fase2.sql", ("consulta_guardada", "usuario_auditoria", "reidentificacion")),
)

MIGRACIONES_SEMANTICA = (
    ("0001_init.sql", ("cuestionario", "individuo", "pregunta", "respuesta")),
    ("0002_vista_procedencia.sql", ("v_respuesta_estudio",)),
    ("0003_hash_texto.sql", ("respuesta.hash_texto",)),
)

STORES = {
    "boveda": MIGRACIONES_BOVEDA,
    "semantica": MIGRACIONES_SEMANTICA,
}

# Para qué se usa cada objeto, cuando no es evidente por el nombre. Sirve para
# que el aviso diga qué dejó de funcionar y no solo qué falta.
PARA_QUE = {
    "v_respuesta_estudio": "la procedencia de las respuestas en las consultas semánticas",
    "respuesta.hash_texto": "saltear el re-embedding cuando una re-ingesta trae el mismo texto",
    "consulta_guardada": "guardar consultas para reutilizarlas",
    "usuario_auditoria": "la auditoría de la gestión de usuarios",
    "reidentificacion": "el registro de quién tradujo un id_persona a datos de contacto",
    "alta_en_revision": "las altas que quedan esperando decisión humana",
    "persona_borrada": "el rastro de las bajas ya atendidas",
    "objetivo_composicion": "el universo de referencia de la composición",
}


def _presentes(conn):
    """Tablas, vistas y columnas que existen hoy en el esquema `public`."""
    # El import va acá adentro a propósito: la lista de migraciones de más
    # arriba es la fuente de verdad de qué tiene que existir, y se consulta
    # desde herramientas que no abren ninguna conexión (`verificar_esquema.py
    # --sql`, y las pruebas que comparan la lista contra los `.sql`). Con el
    # import arriba, esas herramientas exigirían tener psycopg instalado para
    # no conectarse a nada.
    from . import db

    # Se consulta pg_catalog y no information_schema, que es lo natural, por
    # un motivo que cuesta caro: information_schema filtra por privilegios. Un
    # usuario que se conectó a la base correcta pero sin permisos sobre las
    # tablas no ve ninguna fila, y este módulo concluiría que el esquema está
    # vacío —o sea, que faltan todas las migraciones—. La consecuencia no es
    # un mensaje molesto: es un informe que manda a re-correr migraciones
    # sobre una base que ya las tiene. pg_catalog no filtra: dice qué existe,
    # que es exactamente la pregunta.
    filas = db.todas(
        conn,
        """
        select c.relname as table_name, a.attname as column_name
          from pg_catalog.pg_class c
          join pg_catalog.pg_namespace n on n.oid = c.relnamespace
          join pg_catalog.pg_attribute a on a.attrelid = c.oid
         where n.nspname = 'public'
           and c.relkind in ('r', 'v', 'm', 'p', 'f')
           and a.attnum > 0
           and not a.attisdropped
        """,
    )
    relaciones = {f["table_name"].lower() for f in filas}
    columnas = {f'{f["table_name"].lower()}.{f["column_name"].lower()}' for f in filas}
    return relaciones, columnas


def verificar(conn, migraciones):
    """Compara el esquema con lo que el código espera.

    Devuelve `{completo, faltantes, migraciones}`. `faltantes` lista un
    elemento por objeto ausente, con la migración que lo crea; `migraciones`
    resume el estado de cada archivo, que es la unidad en la que se actúa (se
    aplica una migración entera, no un objeto suelto).
    """
    relaciones, columnas = _presentes(conn)

    faltantes, resumen = [], []
    for archivo, objetos in migraciones:
        ausentes = [
            objeto for objeto in objetos
            if objeto not in (columnas if "." in objeto else relaciones)
        ]
        resumen.append({
            "migracion": archivo,
            "aplicada": not ausentes,
            "objetos": list(objetos),
            "faltantes": ausentes,
        })
        for objeto in ausentes:
            faltantes.append({
                "objeto": objeto,
                "tipo": "columna" if "." in objeto else "relación",
                "migracion": archivo,
                "para_que": PARA_QUE.get(objeto),
            })

    return {
        "completo": not faltantes,
        "faltantes": faltantes,
        "migraciones": resumen,
    }


def revisar_stores(conn_boveda, conn_semantica):
    """El estado de los dos stores, con las instrucciones para arreglarlo."""
    salida = {
        "boveda": verificar(conn_boveda, MIGRACIONES_BOVEDA),
        "semantica": verificar(conn_semantica, MIGRACIONES_SEMANTICA),
    }
    salida["completo"] = all(s["completo"] for s in salida.values() if isinstance(s, dict))
    salida["como_aplicar"] = [
        f"psql -h 127.0.0.1 -p {puerto} -U app_paneles -d paneles_{store} "
        f"-v ON_ERROR_STOP=1 -f db/{store}/{m['migracion']}"
        for store, puerto in (("boveda", 5432), ("semantica", 5433))
        for m in salida[store]["migraciones"] if not m["aplicada"]
    ]
    return salida


# ── Traducción del error de Postgres ────────────────────────────────

_RE_RELACION = re.compile(r'relation "([a-z0-9_]+)" does not exist', re.I)
_RE_COLUMNA = re.compile(r'column "?([a-z0-9_.]+)"? does not exist', re.I)


def _buscar(objeto):
    """En qué migración se crea un objeto. `(store, archivo, declarado)` o None.

    Postgres nombra la columna que falta con el alias de la consulta
    («column r.hash_texto does not exist»), así que para las columnas se
    compara por el nombre suelto y no por `tabla.columna`.
    """
    suelto = objeto.rsplit(".", 1)[-1]
    for store, migraciones in STORES.items():
        for archivo, objetos in migraciones:
            for declarado in objetos:
                if declarado == objeto:
                    return store, archivo, declarado
                if "." in declarado and declarado.rsplit(".", 1)[-1] == suelto:
                    return store, archivo, declarado
    return None


def explicar_error(error):
    """Si el error es «no existe tal cosa» y esa cosa la crea una migración,
    devuelve una explicación accionable. Si no, None.

    Solo se nombra el objeto y el archivo de la migración: nada de lo que
    devuelve puede contener datos de una persona, que es la razón por la que el
    resto de los errores no se le muestran al cliente.
    """
    texto = str(error)
    encontrado = _RE_RELACION.search(texto) or _RE_COLUMNA.search(texto)
    if not encontrado:
        return None

    objeto = encontrado.group(1).lower()
    ubicacion = _buscar(objeto)
    if not ubicacion:
        return None

    store, archivo, declarado = ubicacion
    para_que = PARA_QUE.get(declarado)
    detalle = f" —lo que habilita {para_que}—" if para_que else ""
    return (
        f"Falta aplicar la migración «{archivo}» en el store «{store}»: no "
        f"existe «{objeto}»{detalle}. La base quedó con una versión anterior "
        f"del esquema. Ver el manual de despliegue de la fase correspondiente, "
        f"o revisar el estado completo en Cumplimiento → Esquema."
    )
