"""R4.1.b — Series comparables entre olas.

Comparar «la misma pregunta» entre dos olas es difícil porque cada
cuestionario la redacta distinto, y este sistema **no canoniza respuestas**
por decisión de diseño. Canonizar automáticamente congelaría una equivalencia
que puede ser falsa —dos preguntas parecidas que miden cosas distintas— y lo
haría en el dato, donde ya no se ve.

La salida es que la comparabilidad la **declare el analista**, por caso y
explícitamente. Es la misma distinción de siempre: declararla la hace
revisable y la pone donde corresponde, en quien sabe qué se preguntó y para
qué.

Tres cosas que este módulo garantiza:

**Sugiere, no agrega.** El sistema propone preguntas candidatas de otras olas
por similitud semántica y las devuelve con su distancia. Ninguna entra a la
serie sin que alguien la acepte, y las que entraron por sugerencia quedan
marcadas: si más adelante una serie resulta mal armada, saber cuáles entraron
así dice si el problema fue el criterio o la herramienta.

**Una opción sin mapear no se cuenta.** Es la misma regla que «(sin dato)» en
la composición: meterla en una categoría real haría que el movimiento entre
olas mienta.

**Editable y auditable.** La serie se corrige sin rehacer las olas, y cada
cambio queda con autor y fecha. El rastro va a la bóveda: la serie es
contenido y vive del lado semántico, pero quien la editó es una persona, y
ninguna persona se escribe de ese lado.
"""

import json
import re

from . import db, embeddings
from .errores import Conflicto, DatosInvalidos, NoEncontrado

ACCIONES = ("alta", "edicion", "baja", "pregunta_agregada", "pregunta_quitada",
            "mapeo", "categoria")

MAX_SUGERENCIAS = 20
DISTANCIA_MAXIMA = 0.45
"""Hasta qué distancia coseno una pregunta se considera candidata.

Es un umbral de **presentación**, no de decisión: lo único que hace es evitar
una lista larga de cosas sin relación. Quien decide es el analista, así que
errar por incluir de más es más barato que por excluir."""


# ── Auditoría (bóveda) ───────────────────────────────────────────────

def _auditar(boveda, serie_clave, accion, actor=None, detalle=None):
    db.ejecutar(
        boveda,
        """
        insert into serie_auditoria
               (serie_clave, accion, detalle, actor_uid, actor_email)
             values (%s, %s, %s::jsonb, %s, %s)
        """,
        (serie_clave, accion, json.dumps(detalle or {}),
         getattr(actor, "uid", None), getattr(actor, "email", None)),
    )


def auditoria(conn_boveda, serie_clave=None, limite=100):
    filas = db.todas(
        conn_boveda,
        """
        select serie_clave, accion, detalle, actor_email, creado_en
          from serie_auditoria
         where (%s::text is null or serie_clave = %s::text)
         -- Por `id` además de por fecha: varios cambios de una misma
         -- operación comparten `now()`, y sin el desempate el rastro se
         -- mostraría en cualquier orden.
         order by creado_en desc, id desc
         limit %s
        """,
        (serie_clave, serie_clave, limite),
    )
    return [
        {
            "serie": f["serie_clave"],
            "accion": f["accion"],
            "detalle": f["detalle"],
            "actor": f["actor_email"],
            "creado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]


# ── El catálogo de series ────────────────────────────────────────────

def _clave_valida(clave):
    clave = (clave or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,48}", clave):
        raise DatosInvalidos(
            "La clave de una serie va en minúsculas, empieza con letra y "
            "admite letras, números y guion bajo.",
            {"clave": clave})
    return clave


def listar(conn, incluir_inactivas=False):
    filas = db.todas(
        conn,
        """
        select s.id, s.clave, s.nombre, s.descripcion, s.activa, s.creada_en,
               (select count(*) from serie_pregunta sp
                 where sp.serie_id = s.id)::int as preguntas,
               (select count(*) from serie_categoria sc
                 where sc.serie_id = s.id)::int as categorias
          from serie s
         where (%s::bool is true or s.activa)
         order by s.nombre
        """,
        (incluir_inactivas,),
    )
    return [
        {
            "id": f["id"], "clave": f["clave"], "nombre": f["nombre"],
            "descripcion": f["descripcion"], "activa": f["activa"],
            "preguntas": f["preguntas"], "categorias": f["categorias"],
            "creada_en": f["creada_en"].isoformat(),
        }
        for f in filas
    ]


def obtener(conn, clave_o_id):
    """La serie con sus categorías y sus preguntas, cada una con su mapeo."""
    fila = db.una(
        conn,
        "select id, clave, nombre, descripcion, activa, creada_en from serie "
        " where clave = %s or id::text = %s",
        (str(clave_o_id), str(clave_o_id)))
    if not fila:
        raise NoEncontrado(f"No existe la serie {clave_o_id!r}.")

    categorias = db.todas(
        conn,
        "select id, clave, etiqueta, orden from serie_categoria "
        " where serie_id = %s order by orden, clave", (fila["id"],))
    preguntas = db.todas(
        conn,
        """
        select sp.id, sp.pregunta_id, sp.origen, sp.agregada_en,
               p.codigo, p.texto, p.tipo, p.opciones,
               c.id as cuestionario_id, c.nombre as ola, c.fecha_campo,
               c.ref_estudio
          from serie_pregunta sp
          join pregunta p on p.id = sp.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where sp.serie_id = %s
         order by c.fecha_campo nulls last, c.id, p.orden nulls last, p.codigo
        """,
        (fila["id"],))

    mapeos = {}
    for fila_mapeo in db.todas(
        conn,
        """
        select m.serie_pregunta_id, m.opcion, m.categoria_id, sc.clave
          from serie_mapeo m
          join serie_pregunta sp on sp.id = m.serie_pregunta_id
          left join serie_categoria sc on sc.id = m.categoria_id
         where sp.serie_id = %s
        """,
        (fila["id"],),
    ):
        mapeos.setdefault(fila_mapeo["serie_pregunta_id"], {})[
            fila_mapeo["opcion"]] = fila_mapeo["clave"]

    return {
        "id": fila["id"], "clave": fila["clave"], "nombre": fila["nombre"],
        "descripcion": fila["descripcion"], "activa": fila["activa"],
        "creada_en": fila["creada_en"].isoformat(),
        "categorias": [
            {"id": c["id"], "clave": c["clave"], "etiqueta": c["etiqueta"],
             "orden": c["orden"]}
            for c in categorias
        ],
        "preguntas": [
            {
                "id": p["id"],
                "pregunta_id": p["pregunta_id"],
                "codigo": p["codigo"],
                "texto": p["texto"],
                "tipo": p["tipo"],
                "opciones": p["opciones"],
                "origen": p["origen"],
                "ola": p["ola"],
                "cuestionario_id": p["cuestionario_id"],
                "ref_estudio": str(p["ref_estudio"]) if p["ref_estudio"] else None,
                "fecha_campo": (p["fecha_campo"].isoformat()
                                if p["fecha_campo"] else None),
                "mapeo": mapeos.get(p["id"], {}),
                # Cuáles de sus opciones todavía no se mapearon. Una opción sin
                # mapear no se cuenta en la comparación, así que es lo primero
                # que hay que ver al revisar una serie.
                "sin_mapear": sorted(
                    set((p["opciones"] or {}).keys()) - set(mapeos.get(p["id"], {}))
                ) if p["opciones"] else [],
            }
            for p in preguntas
        ],
    }


def crear(conn, boveda, cuerpo, actor=None):
    clave = _clave_valida((cuerpo or {}).get("clave"))
    nombre = ((cuerpo or {}).get("nombre") or "").strip()
    if not nombre:
        raise DatosInvalidos("La serie necesita un nombre.")
    if db.una(conn, "select 1 from serie where clave = %s", (clave,)):
        raise Conflicto(f"Ya existe una serie con la clave «{clave}».")

    fila = db.una(
        conn,
        "insert into serie (clave, nombre, descripcion) values (%s, %s, %s) "
        "returning id",
        (clave, nombre, ((cuerpo or {}).get("descripcion") or "").strip() or None))

    for orden, categoria in enumerate((cuerpo or {}).get("categorias") or [], 1):
        _agregar_categoria(conn, fila["id"], categoria, orden * 10)

    _auditar(boveda, clave, "alta", actor,
             {"nombre": nombre,
              "categorias": [c.get("clave") for c in
                             ((cuerpo or {}).get("categorias") or [])]})
    return obtener(conn, fila["id"])


def _agregar_categoria(conn, serie_id, categoria, orden_default=100):
    clave = ((categoria or {}).get("clave") or "").strip()
    if not clave:
        raise DatosInvalidos("Cada categoría de la serie necesita una clave.")
    return db.una(
        conn,
        """
        insert into serie_categoria (serie_id, clave, etiqueta, orden)
             values (%s, %s, %s, %s)
        on conflict (serie_id, clave) do update
                set etiqueta = excluded.etiqueta, orden = excluded.orden
          returning id
        """,
        (serie_id, clave, (categoria.get("etiqueta") or clave).strip(),
         categoria.get("orden") or orden_default),
    )


def agregar_categoria(conn, boveda, clave_o_id, categoria, actor=None):
    serie = obtener(conn, clave_o_id)
    _agregar_categoria(conn, serie["id"], categoria)
    _auditar(boveda, serie["clave"], "categoria", actor, {"categoria": categoria})
    return obtener(conn, serie["id"])


def editar(conn, boveda, clave_o_id, cuerpo, actor=None):
    """Corrige nombre, descripción o estado. La clave **no** se cambia: es lo
    que referencia todo lo que apunta a la serie desde afuera."""
    serie = obtener(conn, clave_o_id)
    cambios = {}
    for campo in ("nombre", "descripcion"):
        if campo in (cuerpo or {}):
            cambios[campo] = (cuerpo[campo] or "").strip() or None
    if "activa" in (cuerpo or {}):
        cambios["activa"] = bool(cuerpo["activa"])
    if not cambios:
        return serie
    if cambios.get("nombre") is None and "nombre" in cambios:
        raise DatosInvalidos("La serie necesita un nombre.")

    asignaciones = ", ".join(f"{c} = %s" for c in cambios)
    db.ejecutar(conn, f"update serie set {asignaciones} where id = %s",
                tuple(cambios.values()) + (serie["id"],))
    _auditar(boveda, serie["clave"],
             "baja" if cambios.get("activa") is False else "edicion",
             actor, cambios)
    return obtener(conn, serie["id"])


# ── Las preguntas de la serie ────────────────────────────────────────

def agregar_pregunta(conn, boveda, clave_o_id, pregunta_id, mapeo=None,
                     origen="declarada", actor=None):
    """Declara que esta pregunta es la misma medición que las otras de la serie.

    `mapeo` es `{opcion: clave_de_categoria}`. Lo que no se mapea no se
    cuenta: es la misma regla que «(sin dato)» en la composición.
    """
    serie = obtener(conn, clave_o_id)
    if origen not in ("declarada", "sugerida_aceptada"):
        raise DatosInvalidos(f"Origen desconocido: {origen!r}.")

    pregunta = db.una(
        conn,
        "select p.id, p.codigo, p.texto, p.cuestionario_id, c.nombre as ola "
        "  from pregunta p join cuestionario c on c.id = p.cuestionario_id "
        " where p.id = %s", (pregunta_id,))
    if not pregunta:
        raise NoEncontrado(f"No existe la pregunta {pregunta_id}.")

    # Dos preguntas de la **misma** ola en una serie no es un error de tipeo
    # necesariamente —un cuestionario puede preguntar lo mismo dos veces—,
    # pero casi siempre lo es, y al comparar olas duplicaría a esa gente. Se
    # avisa y no se bloquea: quien sabe es el analista.
    ya_de_esa_ola = [
        p for p in serie["preguntas"]
        if p["cuestionario_id"] == pregunta["cuestionario_id"]
    ]

    fila = db.una(
        conn,
        """
        insert into serie_pregunta (serie_id, pregunta_id, origen)
             values (%s, %s, %s)
        on conflict (serie_id, pregunta_id) do update set origen = excluded.origen
          returning id
        """,
        (serie["id"], pregunta_id, origen))

    if mapeo:
        _guardar_mapeo(conn, serie["id"], fila["id"], mapeo)

    _auditar(boveda, serie["clave"], "pregunta_agregada", actor,
             {"pregunta_id": pregunta_id, "codigo": pregunta["codigo"],
              "ola": pregunta["ola"], "origen": origen})

    salida = obtener(conn, serie["id"])
    if ya_de_esa_ola:
        salida["aviso"] = (
            f"La serie ya tenía {len(ya_de_esa_ola)} pregunta(s) de la ola "
            f"«{pregunta['ola']}». Al comparar entre olas, esa gente se "
            f"contaría dos veces."
        )
    return salida


def quitar_pregunta(conn, boveda, clave_o_id, pregunta_id, actor=None):
    serie = obtener(conn, clave_o_id)
    n = db.ejecutar(
        conn,
        "delete from serie_pregunta where serie_id = %s and pregunta_id = %s",
        (serie["id"], pregunta_id))
    if n:
        _auditar(boveda, serie["clave"], "pregunta_quitada", actor,
                 {"pregunta_id": pregunta_id})
    return obtener(conn, serie["id"])


def _guardar_mapeo(conn, serie_id, serie_pregunta_id, mapeo):
    categorias = {
        c["clave"]: c["id"] for c in db.todas(
            conn, "select id, clave from serie_categoria where serie_id = %s",
            (serie_id,))
    }
    desconocidas = sorted(set(mapeo.values()) - set(categorias) - {None})
    if desconocidas:
        raise DatosInvalidos(
            f"La serie no tiene las categorías {desconocidas}. Una opción no se "
            f"puede mapear a una categoría que no existe.",
            {"categorias": sorted(categorias)})
    for opcion, categoria in mapeo.items():
        db.ejecutar(
            conn,
            """
            insert into serie_mapeo (serie_pregunta_id, opcion, categoria_id)
                 values (%s, %s, %s)
            on conflict (serie_pregunta_id, opcion) do update
                    set categoria_id = excluded.categoria_id
            """,
            (serie_pregunta_id, str(opcion), categorias.get(categoria)))


def mapear(conn, boveda, clave_o_id, pregunta_id, mapeo, actor=None):
    serie = obtener(conn, clave_o_id)
    fila = db.una(
        conn,
        "select id from serie_pregunta where serie_id = %s and pregunta_id = %s",
        (serie["id"], pregunta_id))
    if not fila:
        raise NoEncontrado(
            f"La pregunta {pregunta_id} no está en la serie «{serie['clave']}».")
    _guardar_mapeo(conn, serie["id"], fila["id"], mapeo or {})
    _auditar(boveda, serie["clave"], "mapeo", actor,
             {"pregunta_id": pregunta_id, "mapeo": mapeo})
    return obtener(conn, serie["id"])


# ── Sugerencias: propone, no agrega ──────────────────────────────────

def _asegurar_embeddings(conn, preguntas, proveedor=None):
    """Embebe los textos que todavía no están cacheados.

    Hasta R4.1.b solo se embebían las respuestas (`pregunta -> respuesta`),
    que sirve para buscar qué contestó la gente pero no para preguntarse qué
    preguntas se parecen: dos olas pueden preguntar lo mismo y recibir
    respuestas opuestas.
    """
    faltan = [p for p in preguntas if p["embedding_texto"] is None]
    if not faltan:
        return 0
    from .semantica import _vector

    proveedor = proveedor or embeddings.crear()
    vectores = proveedor.embeber_en_lotes([p["texto"] for p in faltan])
    for pregunta, vector in zip(faltan, vectores):
        db.ejecutar(
            conn,
            "update pregunta set embedding_texto = %s::vector where id = %s",
            (_vector(vector), pregunta["id"]))
    return len(faltan)


def sugerir(conn, clave_o_id=None, pregunta_id=None, limite=MAX_SUGERENCIAS,
            distancia_maxima=DISTANCIA_MAXIMA, proveedor=None):
    """Preguntas candidatas de **otras** olas, por similitud semántica.

    Devuelve una lista ordenada por distancia. **No agrega ninguna**: el
    resultado es una propuesta, y entra a la serie solo si alguien la acepta
    con `agregar_pregunta(..., origen="sugerida_aceptada")`.
    """
    if not clave_o_id and not pregunta_id:
        raise DatosInvalidos(
            "Para sugerir hace falta una serie (se compara contra sus "
            "preguntas) o una pregunta de referencia.")

    serie = obtener(conn, clave_o_id) if clave_o_id else None
    referencia_ids = [p["pregunta_id"] for p in serie["preguntas"]] if serie else []
    if pregunta_id:
        referencia_ids.append(int(pregunta_id))
    if not referencia_ids:
        return {
            "serie": serie["clave"] if serie else None,
            "sugerencias": [],
            "motivo": (
                "La serie no tiene todavía ninguna pregunta: sin una de "
                "referencia no hay contra qué comparar. Agregá la primera a "
                "mano y de ahí en más el sistema propone las de las otras olas."
            ),
        }

    todas = db.todas(
        conn, "select id, texto, embedding_texto from pregunta")
    embebidas = _asegurar_embeddings(conn, todas, proveedor)

    # Las olas que ya están en la serie quedan fuera: lo que se busca es la
    # misma medición en **otra** ola, y proponer otra pregunta de una ola ya
    # cubierta duplicaría a esa gente al comparar.
    olas_cubiertas = [p["cuestionario_id"] for p in serie["preguntas"]] if serie else []

    filas = db.todas(
        conn,
        """
        select p.id, p.codigo, p.texto, p.tipo, p.opciones,
               c.id as cuestionario_id, c.nombre as ola, c.fecha_campo,
               min(p.embedding_texto <=> r.embedding_texto) as distancia
          from pregunta p
          join cuestionario c on c.id = p.cuestionario_id
          join pregunta r on r.id = any(%s)
         where p.embedding_texto is not null
           and r.embedding_texto is not null
           and p.id <> all(%s)
           and c.id <> all(%s)
         group by p.id, p.codigo, p.texto, p.tipo, p.opciones,
                  c.id, c.nombre, c.fecha_campo
        having min(p.embedding_texto <=> r.embedding_texto) <= %s
         order by distancia
         limit %s
        """,
        (referencia_ids, referencia_ids, olas_cubiertas or [-1],
         distancia_maxima, limite),
    )
    return {
        "serie": serie["clave"] if serie else None,
        "embebidas_ahora": embebidas,
        # Se dice explícito para que la pantalla no pueda presentarlo como
        # una decisión del sistema.
        "propone_no_agrega": True,
        "sugerencias": [
            {
                "pregunta_id": f["id"],
                "codigo": f["codigo"],
                "texto": f["texto"],
                "tipo": f["tipo"],
                "opciones": f["opciones"],
                "ola": f["ola"],
                "cuestionario_id": f["cuestionario_id"],
                "fecha_campo": (f["fecha_campo"].isoformat()
                                if f["fecha_campo"] else None),
                "distancia": round(float(f["distancia"]), 4),
            }
            for f in filas
        ],
    }
