"""R-TEM.7 a R-TEM.12 — La taxonomía de temáticas: área → temática.

Con cada carga la lista de temáticas crece, y en poco tiempo el filtro se
vuelve una columna larga, sin orden y con casi-duplicados. Este módulo agrega
**un** nivel arriba, el área, y nada más: con tres o más niveles nadie sabe en
qué rama va cada cosa.

Lo que no hay que deshacer:

1. **Se calcula una vez y se guarda.** Claude propone el arranque (R-TEM.8),
   ubica cada temática nueva al proponer las de una carga (R-TEM.9, en la
   misma llamada) y sugiere reorganizaciones a pedido (R-TEM.10). **Nada de
   eso escribe**: lo que se guarda es lo que el analista aprueba. Abrir el
   filtro o consultar lee lo guardado y no llama a nadie: una taxonomía
   regenerada cada vez reorganizaría el filtro solo y rompería las consultas
   guardadas. Dinámica en cómo crece, estable en cómo se ve.
2. **Todo por id.** Renombrar un área no rompe un filtro ni una consulta
   guardada.
3. **La marca especial efectiva vive en la base** (`v_tematica`): la propia
   de la temática **o** la de su área. La herencia suma y no resta (R-TEM.11).
4. **Cada operación aplicada queda registrada** (`taxonomia_cambio`): quién,
   cuándo, qué, con los nombres de ese momento. Sirve para entender por qué
   una temática está donde está; no para deshacer (una fusión reapunta
   referencias).
"""

import json
import logging

from . import db
from .errores import DatosInvalidos, NoEncontrado, SinPermiso
from .tematicas import (
    LARGO_MAXIMO, LARGO_MINIMO, MANUAL, ORIGENES, PROPUESTA_IA, FalloPropuesta,
    _sin_tildes, normalizar,
)

log = logging.getLogger(__name__)

# R-TEM.8 — la guía del arranque.
MIN_AREAS, MAX_AREAS = 8, 20

ARRANQUE = "arranque"
MOVER_TEMATICA = "mover_tematica"
FUSIONAR_TEMATICAS = "fusionar_tematicas"
RENOMBRAR_AREA = "renombrar_area"
FUSIONAR_AREAS = "fusionar_areas"
DIVIDIR_AREA = "dividir_area"
MARCAR_AREA_ESPECIAL = "marcar_area_especial"
# Las que puede proponer la reorganización (R-TEM.10). Marcar un área como
# especial no la propone Claude: la decide el analista en la pantalla.
OPERACIONES_PROPONIBLES = (FUSIONAR_TEMATICAS, MOVER_TEMATICA, RENOMBRAR_AREA,
                           FUSIONAR_AREAS, DIVIDIR_AREA)
OPERACIONES = OPERACIONES_PROPONIBLES + (MARCAR_AREA_ESPECIAL,)

OK = "ok"
FALLO = "fallo"
NO_DISPONIBLE = "no_disponible"
SIN_TEMATICAS = "sin_tematicas"

AVISO_FALLO = ("No se pudo obtener la propuesta ({motivo}). La taxonomía no "
               "cambió: se puede volver a pedir más tarde.")


# ════════════════════════════════════════════════════════════════════
#  El vocabulario de áreas
# ════════════════════════════════════════════════════════════════════

def vocabulario_de_areas(conn):
    """`({nombre: id}, {nombre_sin_tildes: nombre})` de todas las áreas."""
    filas = db.todas(conn, "select id, nombre from area_tematica order by id")
    exactos = {f["nombre"]: f["id"] for f in filas}
    sin_tildes = {}
    for f in filas:
        sin_tildes.setdefault(_sin_tildes(f["nombre"]), f["nombre"])
    return exactos, sin_tildes


def resolver_area(nombre, areas):
    """El id del área existente que corresponde a `nombre`, o None.

    La misma regla que las temáticas: normalizado, y si difiere solo en
    tildes, es la existente."""
    nombre = normalizar(nombre)
    if not nombre:
        return None
    exactos, sin_tildes = areas
    if nombre in exactos:
        return exactos[nombre]
    existente = sin_tildes.get(_sin_tildes(nombre))
    return exactos.get(existente) if existente else None


def nombre_de_area(area_id, areas):
    return next((n for n, i in areas[0].items() if i == area_id), None)


def _validar_nombre(crudo, que="área"):
    nombre = normalizar(crudo)
    if not nombre or not (LARGO_MINIMO <= len(nombre) <= LARGO_MAXIMO):
        raise DatosInvalidos(
            f"El nombre de {que} {crudo!r} está vacío o fuera de largo "
            f"({LARGO_MINIMO} a {LARGO_MAXIMO} caracteres).")
    return nombre


def asegurar_area(conn, nombre, origen=MANUAL, descripcion=None, es_especial=False):
    """El id del área, creándola si no existe (reutiliza la que difiere solo
    en tildes). Una existente conserva su descripción; su marca especial solo
    puede **sumar**."""
    nombre = _validar_nombre(nombre)
    origen = origen if origen in ORIGENES else MANUAL
    existente = resolver_area(nombre, vocabulario_de_areas(conn))
    if existente is not None:
        if es_especial:
            db.ejecutar(conn, "update area_tematica set es_especial = true where id = %s",
                        (existente,))
        return existente
    return db.una(
        conn,
        """insert into area_tematica (nombre, descripcion, es_especial, origen)
           values (%s, %s, %s, %s) returning id""",
        (nombre, (str(descripcion).strip() or None) if descripcion else None,
         bool(es_especial), origen))["id"]


def ubicar_si_falta(conn, tematica_id, area, origen=MANUAL):
    """R-TEM.9 — pone la temática en el área **solo si no tiene**.

    `area` es `{id}` o `{nombre, descripcion}`. Lo ya clasificado no se toca:
    moverlo es `aplicar(mover_tematica)`, que se aprueba y se registra."""
    actual = db.una(conn, "select area_id from tematica where id = %s", (tematica_id,))
    if not actual or actual["area_id"] is not None:
        return None
    area_id = area.get("id")
    if area_id:
        if not db.una(conn, "select 1 as hay from area_tematica where id = %s", (area_id,)):
            raise DatosInvalidos(f"No existe el área {area_id}.")
    else:
        area_id = asegurar_area(conn, area.get("nombre"), origen,
                                area.get("descripcion"))
    db.ejecutar(conn, "update tematica set area_id = %s where id = %s",
                (area_id, tematica_id))
    return area_id


def para_prompt(conn):
    """`[(área o None, [temáticas])]`: lo que ve Claude al proponer las
    temáticas de una carga. Las áreas **con sus temáticas**, para que la
    ubicación sea por contenido y no solo por nombre (R-TEM.9)."""
    grupos = {}
    for f in db.todas(conn, "select nombre, area from v_tematica order by area nulls last, nombre"):
        grupos.setdefault(f["area"], []).append(f["nombre"])
    for f in db.todas(conn, "select nombre from area_tematica order by nombre"):
        grupos.setdefault(f["nombre"], [])
    con_area = sorted((a, ts) for a, ts in grupos.items() if a)
    return con_area + ([(None, grupos[None])] if None in grupos else [])


# ════════════════════════════════════════════════════════════════════
#  Leer
# ════════════════════════════════════════════════════════════════════

def _tematicas_con_conteos(conn):
    return db.todas(
        conn,
        """
        select t.id, t.nombre, t.area_id, t.es_especial, t.es_especial_propia,
               (select count(*)::int from carga_tematica ct
                 where ct.tematica_id = t.id) as cargas,
               (select count(distinct pt.id_persona)::int from persona_tematica pt
                 where pt.tematica_id = t.id) as personas
          from v_tematica t
         order by t.nombre
        """)


def listar(conn, incluir_especiales=True):
    """La taxonomía completa: áreas con sus temáticas y las temáticas sin
    área, con cuántas cargas y cuántas personas tiene cada una.

    Sin el permiso de especiales, las especiales se listan (el nombre de un
    tema no es un dato de nadie) pero sin la cuenta de personas.
    """
    areas = {f["id"]: {"id": f["id"], "nombre": f["nombre"],
                       "descripcion": f["descripcion"],
                       "es_especial": f["es_especial"], "origen": f["origen"],
                       "tematicas": [], "personas": f["personas"]}
             for f in db.todas(
                 conn,
                 """select a.id, a.nombre, a.descripcion, a.es_especial, a.origen,
                           (select count(distinct pt.id_persona)::int
                              from persona_tematica pt
                              join tematica t on t.id = pt.tematica_id
                             where t.area_id = a.id) as personas
                      from area_tematica a order by a.nombre""")}
    sin_area = []
    for f in _tematicas_con_conteos(conn):
        item = {"id": f["id"], "nombre": f["nombre"], "es_especial": f["es_especial"],
                "es_especial_propia": f["es_especial_propia"], "cargas": f["cargas"],
                "personas": (f["personas"] if incluir_especiales or not f["es_especial"]
                             else None)}
        (areas[f["area_id"]]["tematicas"] if f["area_id"] in areas else sin_area).append(item)
    if not incluir_especiales:
        for area in areas.values():
            if area["es_especial"]:
                area["personas"] = None
    return {"areas": list(areas.values()), "sin_area": sin_area,
            "arrancada": bool(areas)}


def filtro(conn, incluir_especiales=False):
    """R-TEM.12 y R-TEM.14 — lo que ofrece el filtro de temáticas.

    Agrupado por área, con **cuántas personas** tienen información sobre cada
    temática y cada área (no cuántas cargas: el filtro sirve para encontrar
    gente). Una temática con **cero personas no se ofrece**: es la
    corrección de «nivel educativo», que estaba en una carga y nadie la
    tenía. Sin el permiso, las especiales —propias o por su área— no se
    ofrecen, y el conteo de un área cuenta solo sus temáticas no especiales.

    Solo lee lo guardado: abrir el filtro no llama a Claude ni cambia nada.
    """
    filas = db.todas(
        conn,
        """
        select t.id, t.nombre, t.area_id, t.area, t.es_especial,
               count(distinct pt.id_persona)::int as personas
          from v_tematica t
          join persona_tematica pt on pt.tematica_id = t.id
         where (%s or not t.es_especial)
         group by t.id, t.nombre, t.area_id, t.area, t.es_especial
        having count(distinct pt.id_persona) > 0
         order by t.area nulls last, t.nombre
        """,
        (bool(incluir_especiales),))
    personas_por_area = {f["area_id"]: f["personas"] for f in db.todas(
        conn,
        """
        select t.area_id, count(distinct pt.id_persona)::int as personas
          from v_tematica t
          join persona_tematica pt on pt.tematica_id = t.id
         where t.area_id is not null and (%s or not t.es_especial)
         group by t.area_id
        """,
        (bool(incluir_especiales),))}
    especiales = {f["id"]: f["es_especial"] for f in db.todas(
        conn, "select id, es_especial from area_tematica")}
    grupos = {}
    for f in filas:
        clave = f["area_id"]
        grupo = grupos.setdefault(clave, {
            "id": clave, "nombre": f["area"] or "Sin área",
            "es_especial": especiales.get(clave, False),
            "personas": personas_por_area.get(clave), "tematicas": []})
        grupo["tematicas"].append({"id": f["id"], "nombre": f["nombre"],
                                   "es_especial": f["es_especial"],
                                   "personas": f["personas"]})
    return {"areas": sorted(grupos.values(),
                            key=lambda g: (g["id"] is None, g["nombre"]))}


def sin_personas(conn):
    """R-TEM.14 — las temáticas que están en una carga y que **nadie** de esa
    carga tiene: candidatas a quitar. Es la revisión general después del
    arreglo de «nivel educativo»."""
    return [
        {"carga_id": f["carga_id"], "carga": f["carga"],
         "tematica_id": f["tematica_id"], "tematica": f["tematica"],
         "preguntas": list(f["preguntas"] or [])}
        for f in db.todas(
            conn,
            """
            select c.id as carga_id, c.nombre as carga, t.id as tematica_id,
                   t.nombre as tematica,
                   array(select tp.codigo_pregunta from tematica_pregunta tp
                          where tp.carga_id = ct.carga_id
                            and tp.tematica_id = ct.tematica_id
                          order by 1) as preguntas
              from carga_tematica ct
              join carga c on c.id = ct.carga_id
              join tematica t on t.id = ct.tematica_id
             where not exists (select 1 from persona_tematica pt
                                where pt.carga_id = ct.carga_id
                                  and pt.tematica_id = ct.tematica_id)
             order by c.nombre, t.nombre
            """)
    ]


def historial(conn, limite=50):
    return [
        {"id": f["id"], "tipo": f["tipo"], "detalle": f["detalle"],
         "actor": f["actor_email"] or f["actor"],
         "creado_en": f["creado_en"].isoformat()}
        for f in db.todas(
            conn,
            "select id, tipo, detalle, actor, actor_email, creado_en "
            "from taxonomia_cambio order by creado_en desc, id desc limit %s",
            (max(1, min(int(limite or 50), 500)),))
    ]


def _registrar(conn, tipo, detalle, actor):
    db.ejecutar(
        conn,
        """insert into taxonomia_cambio (tipo, detalle, actor, actor_email)
           values (%s, %s::jsonb, %s, %s)""",
        (tipo, json.dumps(detalle, ensure_ascii=False, default=str),
         getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None),
         getattr(actor, "email", None)))


# ════════════════════════════════════════════════════════════════════
#  R-TEM.8 · El arranque
# ════════════════════════════════════════════════════════════════════

INSTRUCCIONES_ARRANQUE = """\
Vas a recibir las temáticas con las que se describen los estudios de \
investigación de mercado de un panel, en español rioplatense. Tu tarea es \
agruparlas en áreas: un nivel arriba, a nivel de sector o ámbito de \
investigación.

Reglas:
- Entre 8 y 20 áreas en total (menos si hay pocas temáticas: nunca un área \
por temática).
- Cada área es una frase nominal de dos a cuatro palabras, en minúsculas: \
«telecomunicaciones y conectividad», «consumo masivo», «finanzas y medios \
de pago».
- Una descripción corta de cada área, de una línea.
- Cada temática va en exactamente un área, escrita exactamente igual que en \
la lista.
- Si te paso áreas que ya existen, ubicá primero en ellas; proponé áreas \
nuevas solo para lo que no entra en ninguna, y usá el nombre existente tal \
cual.
- Exactamente dos niveles: área y temática. No propongas subáreas.
- Marcá podria_ser_especial = true en las áreas que tocan una categoría \
especial de datos personales: salud, consumo de sustancias, ideología u \
opinión política, religión, vida sexual. El analista lo confirma.

Registrá tu propuesta con la herramienta proponer_taxonomia."""

HERRAMIENTA_ARRANQUE = {
    "name": "proponer_taxonomia",
    "description": "Registra las áreas y qué temáticas van en cada una.",
    "input_schema": {
        "type": "object",
        "properties": {
            "areas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "area": {"type": "string"},
                        "descripcion": {"type": "string"},
                        "podria_ser_especial": {"type": "boolean"},
                        "tematicas": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["area", "descripcion", "podria_ser_especial",
                                 "tematicas"],
                },
            },
        },
        "required": ["areas"],
    },
}


def _mensaje_arranque(conn, a_ubicar):
    lineas = ["Temáticas a ubicar:"] + [f"- {t['nombre']}" for t in a_ubicar]
    existentes = [(a, ts) for a, ts in para_prompt(conn) if a]
    lineas += ["", "Áreas que ya existen (con sus temáticas):"]
    if not existentes:
        lineas.append("(todavía no hay ninguna)")
    for area, nombres in existentes:
        lineas.append(f"Área «{area}»: {', '.join(nombres) or '(sin temáticas)'}")
    return "\n".join(lineas)


def proponer_arranque(conn, proponente):
    """Una sola llamada: las áreas y en cuál va cada temática **sin área**.
    La primera vez son todas. **No escribe nada**: lo que se guarda es lo
    que el analista confirme con `guardar_arranque`."""
    a_ubicar = db.todas(conn, "select id, nombre from tematica where area_id is null "
                              "order by nombre")
    if not a_ubicar:
        return {"estado": SIN_TEMATICAS, "aviso": "No hay temáticas sin área.",
                "areas": [], "sin_ubicar": []}
    try:
        entrada = proponente.llamar(INSTRUCCIONES_ARRANQUE,
                                    _mensaje_arranque(conn, a_ubicar),
                                    HERRAMIENTA_ARRANQUE, etiqueta="taxonomia")
        crudas = entrada.get("areas")
        if not isinstance(crudas, list):
            raise FalloPropuesta("la respuesta no trae áreas")
    except FalloPropuesta as error:
        log.warning("[taxonomia] sin propuesta de arranque: %s", error)
        return {"estado": NO_DISPONIBLE if not getattr(proponente, "disponible", True)
                else FALLO, "aviso": AVISO_FALLO.format(motivo=str(error)),
                "areas": [], "sin_ubicar": [dict(t) for t in a_ubicar]}

    por_nombre = {t["nombre"]: t for t in a_ubicar}
    por_clave = {_sin_tildes(t["nombre"]): t for t in a_ubicar}
    vocabulario = vocabulario_de_areas(conn)
    ubicadas, areas = set(), {}
    for cruda in crudas:
        if not isinstance(cruda, dict):
            continue
        nombre = normalizar(cruda.get("area"))
        if not nombre or not (LARGO_MINIMO <= len(nombre) <= LARGO_MAXIMO):
            continue
        existente = resolver_area(nombre, vocabulario)
        nombre = nombre_de_area(existente, vocabulario) if existente else nombre
        area = areas.setdefault(nombre, {
            "nombre": nombre, "id": existente, "es_nueva": existente is None,
            "descripcion": (str(cruda.get("descripcion") or "").strip() or None),
            "podria_ser_especial": bool(cruda.get("podria_ser_especial")),
            "tematicas": []})
        for crudo in cruda.get("tematicas") or []:
            t = por_nombre.get(normalizar(crudo)) or por_clave.get(_sin_tildes(normalizar(crudo) or ""))
            if t and t["id"] not in ubicadas:
                ubicadas.add(t["id"])
                area["tematicas"].append({"id": t["id"], "nombre": t["nombre"]})
    return {"estado": OK, "aviso": None,
            "areas": [a for a in areas.values() if a["tematicas"]],
            "sin_ubicar": [dict(t) for t in a_ubicar if t["id"] not in ubicadas],
            "guia": {"min_areas": MIN_AREAS, "max_areas": MAX_AREAS}}


def guardar_arranque(conn, areas, actor=None, puede_desmarcar=False):
    """Guarda la taxonomía que confirmó el analista: `[{nombre, descripcion,
    es_especial, origen, tematicas: [ids]}]`.

    Solo ubica temáticas **sin área**: lo ya clasificado se cambia con una
    operación (`aplicar`). Una temática no puede ir en dos áreas.
    """
    if not isinstance(areas, list) or not areas:
        raise DatosInvalidos("Mandá las áreas confirmadas (`areas`).")
    vistas = set()
    for area in areas:
        for tid in area.get("tematicas") or []:
            tid = int(tid.get("id") if isinstance(tid, dict) else tid)
            if tid in vistas:
                raise DatosInvalidos(
                    f"La temática {tid} está en dos áreas: cada temática va en una.")
            vistas.add(tid)
    detalle = []
    for area in areas:
        especial = bool(area.get("es_especial"))
        area_id = area.get("id")
        if area_id:
            actual = db.una(conn, "select id, nombre, es_especial from area_tematica "
                                  "where id = %s", (area_id,))
            if not actual:
                raise NoEncontrado(f"No existe el área {area_id}.")
            if actual["es_especial"] and not especial and not puede_desmarcar:
                raise SinPermiso(
                    "Quitarle la marca de especial a un área la vuelve a exponer: lo "
                    "puede hacer quien administra los atributos.",
                    {"permiso": "gestionar_atributos"})
            if actual["es_especial"] != especial:
                db.ejecutar(conn, "update area_tematica set es_especial = %s where id = %s",
                            (especial, area_id))
        else:
            area_id = asegurar_area(conn, area.get("nombre"), area.get("origen") or PROPUESTA_IA,
                                    area.get("descripcion"), especial)
        ids = [int(t.get("id") if isinstance(t, dict) else t)
               for t in area.get("tematicas") or []]
        movidas = db.todas(
            conn,
            "update tematica set area_id = %s where id = any(%s::bigint[]) "
            "and area_id is null returning id, nombre",
            (area_id, ids)) if ids else []
        nombre = db.una(conn, "select nombre from area_tematica where id = %s",
                        (area_id,))["nombre"]
        detalle.append({"area_id": area_id, "area": nombre, "es_especial": especial,
                        "tematicas": [{"id": f["id"], "nombre": f["nombre"]}
                                      for f in movidas]})
    _registrar(conn, ARRANQUE, {"areas": detalle}, actor)
    return listar(conn)


# ════════════════════════════════════════════════════════════════════
#  R-TEM.10 · Reorganización a pedido
# ════════════════════════════════════════════════════════════════════

INSTRUCCIONES_REORGANIZAR = """\
Vas a recibir la taxonomía de temáticas de un panel de investigación de \
mercado: áreas y, en cada una, sus temáticas, en español rioplatense. Tu \
tarea es proponer operaciones concretas para mejorarla. No se aplica nada \
sin que el analista apruebe cada una por separado.

Operaciones posibles (campo tipo):
- fusionar_tematicas: dos o más temáticas que significan lo mismo \
(«telefonía celular» y «telefonía móvil»). En tematicas las que se van, en \
tematica_destino la que queda.
- mover_tematica: una temática que está en el área equivocada. En tematica \
la temática, en area el área de destino (existente, escrita igual).
- renombrar_area: un nombre de área poco claro. En area el actual, en \
nuevo_nombre el nuevo.
- fusionar_areas: áreas que se superponen. En areas las que se van, en \
area_destino la que queda.
- dividir_area: un área demasiado amplia. En area la que se divide, en \
nuevas_areas las nuevas con sus temáticas.

Reglas:
- Usá los nombres exactamente como aparecen en la lista.
- Proponé solo lo que mejora claramente la taxonomía; mejor pocas \
operaciones buenas que muchas dudosas. Si está bien como está, no \
propongas nada.
- Exactamente dos niveles: no propongas subáreas.
- Nombres nuevos: frases nominales de dos a cuatro palabras, en minúsculas.
- En motivo, una línea que explique por qué.

Registrá tu propuesta con la herramienta proponer_operaciones."""

HERRAMIENTA_REORGANIZAR = {
    "name": "proponer_operaciones",
    "description": "Registra las operaciones propuestas sobre la taxonomía.",
    "input_schema": {
        "type": "object",
        "properties": {
            "operaciones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tipo": {"type": "string", "enum": list(OPERACIONES_PROPONIBLES)},
                        "motivo": {"type": "string"},
                        "tematicas": {"type": "array", "items": {"type": "string"}},
                        "tematica_destino": {"type": "string"},
                        "tematica": {"type": "string"},
                        "area": {"type": "string"},
                        "nuevo_nombre": {"type": "string"},
                        "areas": {"type": "array", "items": {"type": "string"}},
                        "area_destino": {"type": "string"},
                        "nuevas_areas": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "area": {"type": "string"},
                                    "descripcion": {"type": "string"},
                                    "tematicas": {"type": "array",
                                                  "items": {"type": "string"}},
                                },
                                "required": ["area", "tematicas"],
                            },
                        },
                    },
                    "required": ["tipo", "motivo"],
                },
            },
        },
        "required": ["operaciones"],
    },
}


def _mensaje_reorganizar(taxonomia):
    lineas = []
    for area in taxonomia["areas"]:
        lineas.append(f"Área «{area['nombre']}»"
                      + (f" — {area['descripcion']}" if area.get("descripcion") else "")
                      + ":")
        lineas += [f"- {t['nombre']}" for t in area["tematicas"]] or ["- (sin temáticas)"]
    if taxonomia["sin_area"]:
        lineas.append("Sin área:")
        lineas += [f"- {t['nombre']}" for t in taxonomia["sin_area"]]
    return "\n".join(lineas)


def _ids_por_nombre(conn):
    tematicas = {f["nombre"]: f["id"] for f in db.todas(conn, "select id, nombre from tematica")}
    areas = vocabulario_de_areas(conn)
    t_sin = {_sin_tildes(n): i for n, i in tematicas.items()}

    def tematica(nombre):
        nombre = normalizar(nombre)
        return tematicas.get(nombre) or t_sin.get(_sin_tildes(nombre or ""))

    def area(nombre):
        return resolver_area(nombre, areas)

    return tematica, area


def _resolver_operacion(conn, cruda):
    """La operación del modelo (por nombres) → la que se aplica (por ids),
    o un `str` con el motivo por el que se descarta."""
    tematica, area = _ids_por_nombre(conn)
    tipo = cruda.get("tipo")
    if tipo == FUSIONAR_TEMATICAS:
        destino = tematica(cruda.get("tematica_destino"))
        origenes = [i for i in dict.fromkeys(tematica(n) for n in cruda.get("tematicas") or [])
                    if i and i != destino]
        if not destino or not origenes:
            return "nombra temáticas que no existen"
        return {"tipo": tipo, "destino_id": destino, "origen_ids": origenes}
    if tipo == MOVER_TEMATICA:
        tid, aid = tematica(cruda.get("tematica")), area(cruda.get("area"))
        if not tid:
            return "nombra una temática que no existe"
        if not aid:
            return "el área de destino no existe (para crear una, dividir un área)"
        return {"tipo": tipo, "tematica_id": tid, "area_id": aid}
    if tipo == RENOMBRAR_AREA:
        aid = area(cruda.get("area"))
        nuevo = normalizar(cruda.get("nuevo_nombre"))
        if not aid or not nuevo:
            return "nombra un área que no existe o un nombre vacío"
        return {"tipo": tipo, "area_id": aid, "nombre": nuevo}
    if tipo == FUSIONAR_AREAS:
        destino = area(cruda.get("area_destino"))
        origenes = [i for i in dict.fromkeys(area(n) for n in cruda.get("areas") or [])
                    if i and i != destino]
        if not destino or not origenes:
            return "nombra áreas que no existen"
        return {"tipo": tipo, "destino_id": destino, "origen_ids": origenes}
    if tipo == DIVIDIR_AREA:
        aid = area(cruda.get("area"))
        nuevas = []
        for n in cruda.get("nuevas_areas") or []:
            ids = [i for i in (tematica(x) for x in n.get("tematicas") or []) if i]
            nombre = normalizar(n.get("area"))
            if nombre and ids:
                nuevas.append({"nombre": nombre, "descripcion": n.get("descripcion"),
                               "tematica_ids": ids})
        if not aid or not nuevas:
            return "nombra un área que no existe o no trae áreas nuevas"
        return {"tipo": tipo, "area_id": aid, "nuevas": nuevas}
    return f"tipo de operación desconocido: {tipo!r}"


def describir(conn, operacion):
    """Una frase para la pantalla, con los nombres de hoy."""
    t = lambda i: (db.una(conn, "select nombre from tematica where id = %s", (i,)) or {}).get("nombre", f"#{i}")  # noqa: E731
    a = lambda i: (db.una(conn, "select nombre from area_tematica where id = %s", (i,)) or {}).get("nombre", f"#{i}")  # noqa: E731
    tipo = operacion["tipo"]
    if tipo == FUSIONAR_TEMATICAS:
        return (f"Fusionar {', '.join('«' + t(i) + '»' for i in operacion['origen_ids'])} "
                f"en «{t(operacion['destino_id'])}»")
    if tipo == MOVER_TEMATICA:
        destino = (a(operacion["area_id"]) if operacion.get("area_id")
                   else (operacion.get("area_nueva") or {}).get("nombre"))
        return f"Mover «{t(operacion['tematica_id'])}» al área «{destino}»"
    if tipo == RENOMBRAR_AREA:
        return f"Renombrar el área «{a(operacion['area_id'])}» a «{operacion['nombre']}»"
    if tipo == FUSIONAR_AREAS:
        return (f"Fusionar las áreas {', '.join('«' + a(i) + '»' for i in operacion['origen_ids'])} "
                f"en «{a(operacion['destino_id'])}»")
    if tipo == DIVIDIR_AREA:
        return (f"Dividir «{a(operacion['area_id'])}» en "
                + ", ".join(f"«{n['nombre']}» ({', '.join(t(i) for i in n['tematica_ids'])})"
                            for n in operacion["nuevas"]))
    if tipo == MARCAR_AREA_ESPECIAL:
        return (f"{'Marcar' if operacion.get('es_especial') else 'Desmarcar'} el área "
                f"«{a(operacion['area_id'])}» como especial")
    return tipo


def proponer_reorganizacion(conn, proponente):
    """R-TEM.10 — Claude recibe toda la taxonomía y propone operaciones.
    **No aplica ninguna**: cada una se aprueba o se descarta por separado
    con `aplicar`. Las que nombran cosas que no existen se devuelven en
    `descartadas`, con el motivo."""
    taxonomia = listar(conn)
    if not taxonomia["areas"] and not taxonomia["sin_area"]:
        return {"estado": SIN_TEMATICAS, "aviso": "No hay temáticas que reorganizar.",
                "operaciones": [], "descartadas": []}
    try:
        entrada = proponente.llamar(INSTRUCCIONES_REORGANIZAR,
                                    _mensaje_reorganizar(taxonomia),
                                    HERRAMIENTA_REORGANIZAR, etiqueta="taxonomia")
        crudas = entrada.get("operaciones")
        if not isinstance(crudas, list):
            raise FalloPropuesta("la respuesta no trae operaciones")
    except FalloPropuesta as error:
        log.warning("[taxonomia] sin propuesta de reorganización: %s", error)
        return {"estado": NO_DISPONIBLE if not getattr(proponente, "disponible", True)
                else FALLO, "aviso": AVISO_FALLO.format(motivo=str(error)),
                "operaciones": [], "descartadas": []}
    operaciones, descartadas = [], []
    for cruda in crudas:
        if not isinstance(cruda, dict):
            continue
        resuelta = _resolver_operacion(conn, cruda)
        if isinstance(resuelta, str):
            descartadas.append({"propuesta": cruda, "motivo": resuelta})
            continue
        resuelta["motivo"] = str(cruda.get("motivo") or "").strip() or None
        resuelta["descripcion"] = describir(conn, resuelta)
        operaciones.append(resuelta)
    return {"estado": OK, "aviso": None, "operaciones": operaciones,
            "descartadas": descartadas}


# ════════════════════════════════════════════════════════════════════
#  Aplicar una operación aprobada
# ════════════════════════════════════════════════════════════════════

def _ids(valores, que):
    try:
        ids = [int(v) for v in dict.fromkeys(valores or [])]
    except (TypeError, ValueError):
        raise DatosInvalidos(f"{que}: se esperaban ids.") from None
    if not ids:
        raise DatosInvalidos(f"{que}: falta al menos uno.")
    return ids


def _exigir_tematicas(conn, ids):
    existentes = {f["id"]: f for f in db.todas(
        conn, "select id, nombre, area_id, es_especial from tematica "
              "where id = any(%s::bigint[])", (ids,))}
    faltan = [i for i in ids if i not in existentes]
    if faltan:
        raise NoEncontrado(f"No existen las temáticas {faltan}.")
    return existentes


def _exigir_areas(conn, ids):
    existentes = {f["id"]: f for f in db.todas(
        conn, "select id, nombre, es_especial from area_tematica "
              "where id = any(%s::bigint[])", (ids,))}
    faltan = [i for i in ids if i not in existentes]
    if faltan:
        raise NoEncontrado(f"No existen las áreas {faltan}.")
    return existentes


def _fusionar_tematicas(conn, origen_ids, destino_id):
    """Reapunta `carga_tematica`, `tematica_pregunta` y `persona_tematica`
    de cada origen a la que queda, **sin duplicar filas**, y borra el
    origen. Devuelve las cargas afectadas, para recalcular."""
    cargas = [f["carga_id"] for f in db.todas(
        conn, "select distinct carga_id from carga_tematica "
              "where tematica_id = any(%s::bigint[])", (origen_ids,))]
    for origen in origen_ids:
        db.ejecutar(conn, """
            insert into carga_tematica (carga_id, tematica_id, origen, creado_en)
            select carga_id, %s, origen, creado_en from carga_tematica
             where tematica_id = %s
            on conflict (carga_id, tematica_id) do nothing""", (destino_id, origen))
        db.ejecutar(conn, """
            insert into tematica_pregunta (carga_id, tematica_id, codigo_pregunta)
            select carga_id, %s, codigo_pregunta from tematica_pregunta
             where tematica_id = %s
            on conflict do nothing""", (destino_id, origen))
        # Una persona con las dos temáticas en la misma carga queda con una
        # fila. La cuenta de preguntas se recalcula después, si hay con qué.
        db.ejecutar(conn, """
            insert into persona_tematica (id_persona, tematica_id, carga_id,
                                          preguntas_respondidas, creado_en)
            select id_persona, %s, carga_id, preguntas_respondidas, creado_en
              from persona_tematica where tematica_id = %s
            on conflict (id_persona, tematica_id, carga_id) do update
              set preguntas_respondidas = greatest(
                    persona_tematica.preguntas_respondidas,
                    excluded.preguntas_respondidas)""", (destino_id, origen))
        # Se van los vínculos del origen (y en cascada sus preguntas y
        # personas, ya copiadas) y después el origen.
        db.ejecutar(conn, "delete from carga_tematica where tematica_id = %s", (origen,))
    db.ejecutar(conn, """
        update tematica d set es_especial = d.es_especial or o.especial,
                              area_id = coalesce(d.area_id, o.area_id)
          from (select bool_or(es_especial) as especial,
                       (array_agg(area_id) filter (where area_id is not null))[1] as area_id
                  from tematica where id = any(%s::bigint[])) o
         where d.id = %s""", (origen_ids, destino_id))
    db.ejecutar(conn, "delete from tematica where id = any(%s::bigint[])", (origen_ids,))
    return cargas


def aplicar(conn, operacion, actor=None, conn_semantica=None, puede_desmarcar=False):
    """Aplica **una** operación aprobada y la registra. Las ids se validan
    contra el estado de hoy: una propuesta vieja que nombra algo que ya no
    existe falla con el motivo en vez de aplicarse a medias.

    Una fusión de temáticas recalcula las temáticas de las personas de las
    cargas afectadas si hay conexión al store semántico (la cuenta de
    preguntas respondidas de dos temáticas fusionadas no se puede sumar a
    ciegas).
    """
    from . import tematicas as mod_tematicas

    if not isinstance(operacion, dict):
        raise DatosInvalidos("Mandá la operación (`operacion`).")
    tipo = operacion.get("tipo")
    if tipo not in OPERACIONES:
        raise DatosInvalidos(f"Operación desconocida: {tipo!r}.",
                             {"operaciones": list(OPERACIONES)})
    descripcion = describir(conn, operacion) if tipo != DIVIDIR_AREA else None
    detalle = {"motivo": operacion.get("motivo")}

    if tipo == MOVER_TEMATICA:
        tid = _ids([operacion.get("tematica_id")], "tematica_id")[0]
        tematica = _exigir_tematicas(conn, [tid])[tid]
        if operacion.get("area_id"):
            aid = _ids([operacion["area_id"]], "area_id")[0]
            _exigir_areas(conn, [aid])
        else:
            nueva = operacion.get("area_nueva") or {}
            aid = asegurar_area(conn, nueva.get("nombre"), MANUAL, nueva.get("descripcion"))
        db.ejecutar(conn, "update tematica set area_id = %s where id = %s", (aid, tid))
        detalle.update({"tematica": {"id": tid, "nombre": tematica["nombre"]},
                        "desde_area_id": tematica["area_id"], "area_id": aid})

    elif tipo == FUSIONAR_TEMATICAS:
        destino = _ids([operacion.get("destino_id")], "destino_id")[0]
        origenes = [i for i in _ids(operacion.get("origen_ids"), "origen_ids") if i != destino]
        if not origenes:
            raise DatosInvalidos("Una fusión necesita al menos una temática distinta de la que queda.")
        existentes = _exigir_tematicas(conn, origenes + [destino])
        cargas = _fusionar_tematicas(conn, origenes, destino)
        if conn_semantica is not None:
            for carga_id in cargas:
                mod_tematicas.recalcular_carga(conn, conn_semantica, carga_id)
        detalle.update({
            "destino": {"id": destino, "nombre": existentes[destino]["nombre"]},
            "origenes": [{"id": i, "nombre": existentes[i]["nombre"]} for i in origenes],
            "cargas_afectadas": cargas, "recalculada": conn_semantica is not None})

    elif tipo == RENOMBRAR_AREA:
        aid = _ids([operacion.get("area_id")], "area_id")[0]
        anterior = _exigir_areas(conn, [aid])[aid]["nombre"]
        nombre = _validar_nombre(operacion.get("nombre"))
        otra = resolver_area(nombre, vocabulario_de_areas(conn))
        if otra is not None and otra != aid:
            raise DatosInvalidos(
                f"Ya existe el área «{nombre}»: para juntarlas, fusionalas.")
        db.ejecutar(conn, "update area_tematica set nombre = %s, "
                          "descripcion = coalesce(%s, descripcion) where id = %s",
                    (nombre, (operacion.get("descripcion") or None), aid))
        detalle.update({"area_id": aid, "antes": anterior, "despues": nombre})

    elif tipo == FUSIONAR_AREAS:
        destino = _ids([operacion.get("destino_id")], "destino_id")[0]
        origenes = [i for i in _ids(operacion.get("origen_ids"), "origen_ids") if i != destino]
        if not origenes:
            raise DatosInvalidos("Una fusión necesita al menos un área distinta de la que queda.")
        existentes = _exigir_areas(conn, origenes + [destino])
        db.ejecutar(conn, "update tematica set area_id = %s where area_id = any(%s::bigint[])",
                    (destino, origenes))
        # La marca especial suma: si alguna lo era, la que queda lo es.
        if any(existentes[i]["es_especial"] for i in origenes):
            db.ejecutar(conn, "update area_tematica set es_especial = true where id = %s",
                        (destino,))
        db.ejecutar(conn, "delete from area_tematica where id = any(%s::bigint[])", (origenes,))
        detalle.update({
            "destino": {"id": destino, "nombre": existentes[destino]["nombre"]},
            "origenes": [{"id": i, "nombre": existentes[i]["nombre"]} for i in origenes]})

    elif tipo == DIVIDIR_AREA:
        aid = _ids([operacion.get("area_id")], "area_id")[0]
        area = _exigir_areas(conn, [aid])[aid]
        descripcion = describir(conn, operacion)
        nuevas = operacion.get("nuevas") or []
        if not nuevas:
            raise DatosInvalidos("Dividir un área necesita las áreas nuevas.")
        resultado = []
        for nueva in nuevas:
            ids = _ids(nueva.get("tematica_ids"), "tematica_ids")
            fuera = [i for i, t in _exigir_tematicas(conn, ids).items() if t["area_id"] != aid]
            if fuera:
                raise DatosInvalidos(f"Las temáticas {fuera} no están en el área que se divide.")
            # Las nuevas heredan la sensibilidad de la que se divide.
            nid = asegurar_area(conn, nueva.get("nombre"), PROPUESTA_IA,
                                nueva.get("descripcion"),
                                area["es_especial"] or bool(nueva.get("es_especial")))
            db.ejecutar(conn, "update tematica set area_id = %s where id = any(%s::bigint[])",
                        (nid, ids))
            resultado.append({"area_id": nid, "nombre": normalizar(nueva.get("nombre")),
                              "tematica_ids": ids})
        quedan = db.una(conn, "select count(*)::int as n from tematica where area_id = %s",
                        (aid,))["n"]
        if not quedan:
            db.ejecutar(conn, "delete from area_tematica where id = %s", (aid,))
        detalle.update({"area": {"id": aid, "nombre": area["nombre"]},
                        "nuevas": resultado, "area_original_borrada": not quedan})

    elif tipo == MARCAR_AREA_ESPECIAL:
        aid = _ids([operacion.get("area_id")], "area_id")[0]
        area = _exigir_areas(conn, [aid])[aid]
        especial = bool(operacion.get("es_especial"))
        if area["es_especial"] and not especial and not puede_desmarcar:
            raise SinPermiso(
                "Quitarle la marca de especial a un área la vuelve a exponer: lo "
                "puede hacer quien administra los atributos.",
                {"permiso": "gestionar_atributos"})
        db.ejecutar(conn, "update area_tematica set es_especial = %s where id = %s",
                    (especial, aid))
        detalle.update({"area": {"id": aid, "nombre": area["nombre"]},
                        "es_especial": especial})

    detalle["descripcion"] = descripcion
    _registrar(conn, tipo, detalle, actor)
    return {"tipo": tipo, "descripcion": descripcion, "detalle": detalle,
            "taxonomia": listar(conn)}
