"""R-CLI — Los clientes de cada carga y de cada panelista (D80).

Un cliente es **para quién se hizo un estudio**. Es una entidad compartida
entre cargas (`cliente`, boveda/0031), con el nombre normalizado —igual que
las temáticas—, así «Antel», «ANTEL» y «antel» son el mismo, y antes de crear
uno se busca el que difiera solo en tildes.

Desde D81 (`boveda/0032`) el cliente es **del estudio** (`encuesta_cliente`):
todas las cargas de una encuesta comparten sus clientes. Los de una persona
**se derivan** de las encuestas que respondió (`v_persona_cliente`) y no se
copian. Con las temáticas es al revés, y no por descuido: una
temática se asigna solo a quien **respondió** sobre ese tema (D75), porque
estar en una carga no garantiza haber contestado esas preguntas. El cliente
sí se hereda: quien estuvo en una carga hecha para Antel participó en un
estudio para Antel, contestara lo que contestara.

**Confidencial** (R-CLI.4): el nombre del cliente es información comercial,
y el respondente no suele saber para quién es el estudio. Por eso:

* nunca en el portal del panelista (`portal.py` no lo lee);
* nunca en la superficie de COLOQUIO (ninguna tabla ni vista tiene `grant`);
* no va en las exportaciones (sus columnas son fijas: `consultas.a_csv`,
  `CAMPOS_IDENTIFICADOS`). Se puede agregar como columna **en pantalla**.
"""

from . import db, tematicas
from .errores import DatosInvalidos, NoEncontrado

LARGO_MAXIMO = 120
COLUMNA = "clientes"
ETIQUETA_COLUMNA = "Clientes"


def normalizar(nombre):
    """`"  ANTEL  "` → `"antel"`. Minúsculas y un espacio entre palabras: la
    regla del `check cliente_nombre_normalizado`. A diferencia de las
    temáticas, la puntuación de los bordes se conserva («S.A.»): es parte del
    nombre de una empresa."""
    if nombre is None:
        return None
    texto = " ".join(str(nombre).split()).lower()
    return texto or None


def separar(valores):
    """`[3, "Antel S.A.", "7"]` → `([3, 7], ["antel s.a."])`: ids y nombres
    normalizados con la regla de los clientes (que conserva la puntuación)."""
    ids, nombres = [], []
    for v in valores if isinstance(valores, list) else [valores]:
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, int) or (isinstance(v, str) and v.strip().isdigit()):
            ids.append(int(v))
        elif normalizar(v):
            nombres.append(normalizar(v))
    return ids, nombres


def _validar(crudo):
    nombre = normalizar(crudo)
    if not nombre:
        raise DatosInvalidos("Un cliente necesita un nombre.", {"campo": "clientes"})
    if len(nombre) > LARGO_MAXIMO:
        raise DatosInvalidos(
            f"El nombre del cliente «{nombre[:40]}…» es demasiado largo "
            f"(máximo {LARGO_MAXIMO} caracteres).", {"campo": "clientes"})
    return nombre


def _vocabulario(conn):
    filas = db.todas(conn, "select id, nombre from cliente")
    exactos = {f["nombre"]: f["id"] for f in filas}
    sin_tildes = {tematicas.clave_de_busqueda(f["nombre"]): f["nombre"] for f in filas}
    return exactos, sin_tildes


def asegurar(conn, crudo, vocabulario=None):
    """El id del cliente con ese nombre; lo crea si no existe. Una variante
    que difiere solo en tildes reutiliza la existente («Teléfonica» y
    «telefonica» son uno)."""
    nombre = _validar(crudo)
    exactos, sin_tildes = vocabulario or _vocabulario(conn)
    if nombre in exactos:
        return exactos[nombre]
    parecido = sin_tildes.get(tematicas.clave_de_busqueda(nombre))
    if parecido:
        return exactos[parecido]
    fila = db.una(
        conn,
        """insert into cliente (nombre) values (%s)
           on conflict (nombre) do update set nombre = excluded.nombre
        returning id""",
        (nombre,))
    exactos[nombre] = fila["id"]
    sin_tildes[tematicas.clave_de_busqueda(nombre)] = nombre
    return fila["id"]


def listar(conn, busqueda=None, limite=20):
    """El vocabulario, para autocompletar: los que coinciden con la búsqueda
    (sin tildes ni mayúsculas), con cuántos estudios tiene cada uno. La clave
    sigue siendo `cargas` porque es lo que la pantalla muestra desde R-CLI."""
    filas = db.todas(
        conn,
        """select c.id, c.nombre, count(ec.encuesta_id) as cargas
             from cliente c left join encuesta_cliente ec on ec.cliente_id = c.id
            group by c.id order by count(ec.encuesta_id) desc, c.nombre""")
    clave = tematicas.clave_de_busqueda(busqueda) if busqueda else ""
    elegidos = [f for f in filas
                if not clave or clave in tematicas.clave_de_busqueda(f["nombre"])]
    return [{"id": f["id"], "nombre": f["nombre"], "cargas": f["cargas"]}
            for f in elegidos[:max(1, int(limite or 20))]]


def de_encuestas(conn, ids_encuesta):
    """`{encuesta_id: [{id, nombre}]}` de esos estudios, de una vez."""
    ids = [int(i) for i in ids_encuesta or [] if i is not None]
    if not ids:
        return {}
    salida = {i: [] for i in ids}
    for f in db.todas(
            conn,
            """select ec.encuesta_id, c.id, c.nombre
                 from encuesta_cliente ec join cliente c on c.id = ec.cliente_id
                where ec.encuesta_id = any(%s) order by c.nombre""",
            (ids,)):
        salida[f["encuesta_id"]].append({"id": f["id"], "nombre": f["nombre"]})
    return salida


def de_encuesta(conn, encuesta_id):
    return de_encuestas(conn, [encuesta_id]).get(int(encuesta_id), [])


def de_cargas(conn, ids_carga):
    """`{carga_id: [{id, nombre}]}`: los del estudio de cada carga."""
    ids = [int(i) for i in ids_carga or []]
    if not ids:
        return {}
    encuesta_de = {f["id"]: f["encuesta_id"] for f in db.todas(
        conn, "select id, encuesta_id from carga where id = any(%s)", (ids,))}
    por_encuesta = de_encuestas(conn, list(encuesta_de.values()))
    return {i: por_encuesta.get(encuesta_de.get(i), []) for i in ids}


def de_carga(conn, carga_id):
    return de_cargas(conn, [carga_id]).get(int(carga_id), [])


def guardar_de_encuesta(conn, encuesta_id, nombres):
    """Deja al estudio con **exactamente** esos clientes (por nombre; los
    nuevos se crean). Los que se quitan dejan de verse en sus panelistas en
    el acto: se derivan, no se copiaron."""
    if nombres is None:
        return de_encuesta(conn, encuesta_id)
    if not isinstance(nombres, list):
        raise DatosInvalidos("Los clientes van como una lista de nombres.",
                             {"campo": "clientes"})
    if not db.una(conn, "select 1 from encuesta where id = %s", (encuesta_id,)):
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    vocabulario = _vocabulario(conn)
    ids = list(dict.fromkeys(asegurar(conn, n, vocabulario) for n in nombres
                             if normalizar(n)))
    db.ejecutar(conn,
                "delete from encuesta_cliente where encuesta_id = %s "
                "and cliente_id <> all(%s)",
                (encuesta_id, ids))
    for cliente_id in ids:
        db.ejecutar(conn,
                    """insert into encuesta_cliente (encuesta_id, cliente_id)
                       values (%s, %s) on conflict do nothing""",
                    (encuesta_id, cliente_id))
    return de_encuesta(conn, encuesta_id)


def guardar_de_carga(conn, carga_id, nombres):
    """Los clientes del estudio de la carga (D81): ver `guardar_de_encuesta`."""
    fila = db.una(conn, "select encuesta_id from carga where id = %s", (carga_id,))
    if not fila:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    return guardar_de_encuesta(conn, fila["encuesta_id"], nombres)


def de_persona(conn, id_persona):
    """Los clientes de una persona, con los estudios de los que sale cada uno."""
    filas = db.todas(
        conn,
        """select v.cliente_id, v.cliente, array_agg(distinct v.encuesta_id) as encuestas
             from v_persona_cliente v where v.id_persona = %s
            group by v.cliente_id, v.cliente order by v.cliente""",
        (str(id_persona),))
    return [{"id": f["cliente_id"], "nombre": f["cliente"], "encuestas": f["encuestas"]}
            for f in filas]


def columna_de_varias(conn, ids_persona):
    """R-CLI.3 — la columna «Clientes» de un conjunto de resultados, en
    pantalla. No va en ninguna exportación (R-CLI.4)."""
    ids = [str(i) for i in dict.fromkeys(i for i in (ids_persona or []) if i)]
    if not ids:
        return {}
    filas = db.todas(
        conn,
        """select id_persona::text as id_persona,
                  array_agg(distinct cliente order by cliente) as nombres
             from v_persona_cliente where id_persona = any(%s::uuid[])
            group by id_persona""",
        (ids,))
    return {
        f["id_persona"]: {COLUMNA: {
            "valor": " | ".join(f["nombres"]), "etiqueta_valor": " | ".join(f["nombres"]),
            "origen": "cliente", "procedencia": None}}
        for f in filas
    }
