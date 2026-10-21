"""R-CLI — Los clientes de cada carga y de cada panelista (D80).

Un cliente es **para quién se hizo un estudio**. Es una entidad compartida
entre cargas (`cliente`, boveda/0031), con el nombre normalizado —igual que
las temáticas—, así «Antel», «ANTEL» y «antel» son el mismo, y antes de crear
uno se busca el que difiera solo en tildes.

Los clientes de una persona **se derivan** de sus cargas (`v_persona_cliente`)
y no se copian. Con las temáticas es al revés, y no por descuido: una
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
    (sin tildes ni mayúsculas), con cuántas cargas tiene cada uno."""
    filas = db.todas(
        conn,
        """select c.id, c.nombre, count(cc.carga_id) as cargas
             from cliente c left join carga_cliente cc on cc.cliente_id = c.id
            group by c.id order by count(cc.carga_id) desc, c.nombre""")
    clave = tematicas.clave_de_busqueda(busqueda) if busqueda else ""
    elegidos = [f for f in filas
                if not clave or clave in tematicas.clave_de_busqueda(f["nombre"])]
    return [{"id": f["id"], "nombre": f["nombre"], "cargas": f["cargas"]}
            for f in elegidos[:max(1, int(limite or 20))]]


def de_cargas(conn, ids_carga):
    """`{carga_id: [{id, nombre}]}` de esas cargas, de una vez."""
    ids = [int(i) for i in ids_carga or []]
    if not ids:
        return {}
    salida = {i: [] for i in ids}
    for f in db.todas(
            conn,
            """select cc.carga_id, c.id, c.nombre
                 from carga_cliente cc join cliente c on c.id = cc.cliente_id
                where cc.carga_id = any(%s) order by c.nombre""",
            (ids,)):
        salida[f["carga_id"]].append({"id": f["id"], "nombre": f["nombre"]})
    return salida


def de_carga(conn, carga_id):
    return de_cargas(conn, [carga_id]).get(int(carga_id), [])


def guardar_de_carga(conn, carga_id, nombres):
    """Deja a la carga con **exactamente** esos clientes (por nombre; los
    nuevos se crean). Los que se quitan dejan de verse en sus panelistas en
    el acto: se derivan, no se copiaron."""
    if nombres is None:
        return de_carga(conn, carga_id)
    if not isinstance(nombres, list):
        raise DatosInvalidos("Los clientes van como una lista de nombres.",
                             {"campo": "clientes"})
    if not db.una(conn, "select 1 from carga where id = %s", (carga_id,)):
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    vocabulario = _vocabulario(conn)
    ids = list(dict.fromkeys(asegurar(conn, n, vocabulario) for n in nombres
                             if normalizar(n)))
    db.ejecutar(conn,
                "delete from carga_cliente where carga_id = %s and cliente_id <> all(%s)",
                (carga_id, ids))
    for cliente_id in ids:
        db.ejecutar(conn,
                    """insert into carga_cliente (carga_id, cliente_id) values (%s, %s)
                       on conflict do nothing""",
                    (carga_id, cliente_id))
    return de_carga(conn, carga_id)


def de_persona(conn, id_persona):
    """Los clientes de una persona, con las cargas de las que sale cada uno."""
    filas = db.todas(
        conn,
        """select v.cliente_id, v.cliente, array_agg(distinct v.carga_id) as cargas
             from v_persona_cliente v where v.id_persona = %s
            group by v.cliente_id, v.cliente order by v.cliente""",
        (str(id_persona),))
    return [{"id": f["cliente_id"], "nombre": f["cliente"], "cargas": f["cargas"]}
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
