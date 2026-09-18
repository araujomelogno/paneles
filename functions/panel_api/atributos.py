"""R3.14 — Catálogo de atributos demográficos y valores por persona.

La bóveda tenía una lista **fija** de segmentadores —`persona.sexo`,
`persona.localidad` y el tramo derivado de `persona.fecha_nacimiento`— y eran
los únicos por los que se podía filtrar, segmentar y fijar cuotas. Nivel
educativo, nivel socioeconómico, ocupación, composición del hogar: nada de eso
tenía dónde guardarse. O se perdía, o terminaba embebido como una pregunta más
del lado semántico, que es justo lo que el marcado de demográficas evita. Y
agregar uno nuevo costaba una migración.

Este módulo reemplaza esa lista por un **catálogo** que administra un admin
desde la app. De ahí en más el atributo sirve igual que sexo o localidad: en
los filtros de consulta, en la composición, en las cuotas y en el muestreo.

Tres cosas que conviene tener presentes al leer:

**Un solo mecanismo.** `sexo`, `localidad`, `tramo_etario` y `edad` viven en el
catálogo como cualquier otro atributo, con las mismas claves de siempre para
que los objetivos de composición ya cargados sigan resolviendo. No hay
segmentadores «de fábrica» por un lado y definidos por el usuario por otro.

**Los derivados no se escriben, se calculan.** `tramo_etario` y `edad` son de
tipo `derivado`: lo que se guarda en `persona_atributo` es una *entrada* —la
edad declarada de un archivo, con su fecha de referencia— y la vista
`v_atributo_persona` resuelve el valor efectivo con la precedencia de R3.14.g.
La fecha de nacimiento gana siempre que exista.

**El valor crudo se conserva.** Canonizar un segmentador es lo que hace que un
filtro devuelva siempre lo mismo y que la aritmética de cuotas cierre, pero
congela el error si el mapeo salió mal. Por eso cada valor guarda también lo
que decía el archivo: con eso se recalcula sin volver a pedir nada (R3.14.h).

**R4.1.a — los valores no se pisan: se cierran.** Cada fila de
`persona_atributo` vale en `[desde, hasta)`; la vigente es la de `hasta is
null`. Cambiar un valor cierra el anterior e inserta uno nuevo, así que el
historial queda. Sin eso, recalcular la composición de una ola de hace un año
la calculaba con la demografía de hoy: no era una feature que faltaba, era un
número que estaba mal y no avisaba.
"""

import json

from . import db
from .errores import Conflicto, DatosInvalidos, NoEncontrado

TIPOS = ("categorico", "numerico", "fecha", "derivado")

# Las cuatro claves que siembra la migración 0008. No son especiales en el
# modelo —se leen y se filtran igual que cualquier otra— pero sí en el
# gobierno: son las que el resto del sistema nombra por su clave, así que no
# se pueden borrar ni desactivar sin romper composición, muestreo y las
# consultas guardadas.
CLAVES_DEL_NUCLEO = ("sexo", "localidad", "tramo_etario", "edad")

# Los derivados no aceptan que se les fije el valor efectivo: aceptan la
# entrada de la que se derivan. `edad` guarda la edad declarada del archivo;
# `tramo_etario` guarda el tramo cargado directamente, que es el último
# recurso de R3.14.g.
DERIVADOS = ("tramo_etario", "edad")

# De dónde salió un valor. No hay check en la base a propósito: si mañana
# aparece un origen nuevo, perder el dato sería peor que guardarlo con una
# etiqueta que esta lista no previó.
ORIGENES = ("alta", "edicion", "ingesta", "carga", "inscripcion", "migracion")

ADVERTENCIA_ESPECIAL = (
    "Este atributo queda marcado como categoría especial de la Ley 18.331. "
    "Su tratamiento exige consentimiento específico y protección reforzada: "
    "no alcanza con el consentimiento del alta ni con el de la landing. "
    "Además queda excluido por defecto de las exportaciones con datos."
)


# ── Serialización ────────────────────────────────────────────────────

def _serializar(fila, categorias=None, con_datos=None):
    salida = {
        "id": fila["id"],
        "clave": fila["clave"],
        "etiqueta": fila["etiqueta"],
        "tipo": fila["tipo"],
        "descripcion": fila["descripcion"],
        "es_especial": fila["es_especial"],
        "activo": fila["activo"],
        "orden": fila["orden"],
        "del_nucleo": fila["clave"] in CLAVES_DEL_NUCLEO,
        "creado_en": fila["creado_en"].isoformat(),
    }
    if categorias is not None:
        salida["categorias"] = categorias
    if con_datos is not None:
        salida["tiene_datos"] = con_datos
    return salida


def _serializar_categoria(fila):
    return {
        "id": fila["id"],
        "clave": fila["clave"],
        "etiqueta": fila["etiqueta"],
        "orden": fila["orden"],
        "activo": fila["activo"],
    }


# ── Auditoría ────────────────────────────────────────────────────────

def _auditar(conn, atributo_id, clave, accion, actor=None, detalle=None):
    db.ejecutar(
        conn,
        """
        insert into atributo_auditoria
               (atributo_id, clave, accion, detalle, actor_uid, actor_email)
             values (%s, %s, %s, %s::jsonb, %s, %s)
        """,
        (
            atributo_id, clave, accion,
            json.dumps(detalle or {}, ensure_ascii=False, default=str),
            getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None),
            getattr(actor, "email", None),
        ),
    )


def auditoria(conn, atributo_id=None, limite=200):
    filas = db.todas(
        conn,
        """
        select id, atributo_id, clave, accion, detalle, actor_uid, actor_email,
               creado_en
          from atributo_auditoria
         where (%s::bigint is null or atributo_id = %s::bigint)
         order by creado_en desc, id desc
         limit %s
        """,
        (atributo_id, atributo_id, limite),
    )
    return [
        {
            "id": f["id"], "atributo_id": f["atributo_id"], "clave": f["clave"],
            "accion": f["accion"], "detalle": f["detalle"],
            "actor_uid": f["actor_uid"], "actor_email": f["actor_email"],
            "creado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]


# ── Lectura del catálogo ─────────────────────────────────────────────

def categorias_de(conn, atributo_id, solo_activas=False):
    filas = db.todas(
        conn,
        """
        select id, clave, etiqueta, orden, activo
          from atributo_categoria
         where atributo_id = %s and (%s::bool is not true or activo)
         order by orden, clave
        """,
        (atributo_id, solo_activas),
    )
    return [_serializar_categoria(f) for f in filas]


def tiene_datos(conn, atributo_id):
    return bool(db.una(
        conn, "select 1 from persona_atributo where atributo_id = %s limit 1",
        (atributo_id,)))


def listar(conn, solo_activos=False, con_categorias=True, incluir_especiales=True):
    """El catálogo. `incluir_especiales=False` lo deja sin las categorías
    especiales, que es lo que necesitan las exportaciones (R3.14.e)."""
    filas = db.todas(
        conn,
        """
        select a.id, a.clave, a.etiqueta, a.tipo, a.descripcion, a.es_especial,
               a.activo, a.orden, a.creado_en,
               exists (select 1 from persona_atributo pa
                        where pa.atributo_id = a.id) as tiene_datos
          from atributo_demografico a
         where (%s::bool is not true or a.activo)
           and (%s::bool is not false or not a.es_especial)
         order by a.orden, a.clave
        """,
        (solo_activos, incluir_especiales),
    )
    return [
        _serializar(
            f,
            categorias=categorias_de(conn, f["id"]) if con_categorias else None,
            con_datos=f["tiene_datos"],
        )
        for f in filas
    ]


def obtener(conn, referencia):
    """Por id o por clave, según lo que llegue. Las dos formas se usan: la
    interfaz tiene el id, y la carga y los filtros tienen la clave."""
    if isinstance(referencia, int) or (
            isinstance(referencia, str) and referencia.isdigit()):
        fila = db.una(
            conn,
            "select id, clave, etiqueta, tipo, descripcion, es_especial, activo, "
            "orden, creado_en from atributo_demografico where id = %s",
            (int(referencia),))
    else:
        fila = db.una(
            conn,
            "select id, clave, etiqueta, tipo, descripcion, es_especial, activo, "
            "orden, creado_en from atributo_demografico where clave = %s",
            (str(referencia).strip(),))
    if not fila:
        raise NoEncontrado(f"No existe el atributo demográfico {referencia!r}.")
    return _serializar(fila, categorias=categorias_de(conn, fila["id"]),
                       con_datos=tiene_datos(conn, fila["id"]))


def mapa_por_clave(conn, solo_activos=True):
    """`{clave: atributo}`. Lo usan la carga y los filtros, que trabajan con
    claves y necesitan resolver tipo y categorías en una sola consulta."""
    return {a["clave"]: a for a in listar(conn, solo_activos=solo_activos)}


# ── Escritura del catálogo ───────────────────────────────────────────

def _clave_valida(crudo, que="atributo"):
    clave = (crudo or "").strip().lower()
    if not clave:
        raise DatosInvalidos(f"Falta la clave del {que}.")
    if not all(c.isalnum() or c in "_-+<>." for c in clave):
        raise DatosInvalidos(
            f"La clave de un {que} admite letras, números, guiones y guión "
            f"bajo: llegó {crudo!r}.")
    return clave


def crear(conn, datos, actor=None):
    clave = _clave_valida(datos.get("clave"))
    etiqueta = (datos.get("etiqueta") or "").strip()
    if not etiqueta:
        raise DatosInvalidos("El atributo necesita una etiqueta visible.")
    tipo = (datos.get("tipo") or "categorico").strip().lower()
    if tipo not in TIPOS:
        raise DatosInvalidos(f"Tipo desconocido: {tipo!r}.",
                             {"tipos_validos": list(TIPOS)})
    # `derivado` significa «lo calcula el sistema a partir de otro dato», y
    # esos cálculos están en la vista: no se pueden inventar desde la app.
    if tipo == "derivado":
        raise DatosInvalidos(
            "Un atributo derivado se calcula a partir de otro dato de la "
            "persona, y ese cálculo vive en el esquema: no se puede definir "
            "desde la app. Los que existen son el tramo etario y la edad.")
    if db.una(conn, "select 1 from atributo_demografico where clave = %s", (clave,)):
        raise Conflicto(f"Ya existe un atributo con la clave «{clave}».")

    es_especial = bool(datos.get("es_especial"))
    fila = db.una(
        conn,
        """
        insert into atributo_demografico
               (clave, etiqueta, tipo, descripcion, es_especial, orden, creado_por)
             values (%s, %s, %s, %s, %s, coalesce(%s, 100), %s)
          returning id, clave, etiqueta, tipo, descripcion, es_especial, activo,
                    orden, creado_en
        """,
        (clave, etiqueta, tipo, (datos.get("descripcion") or "").strip() or None,
         es_especial, datos.get("orden"),
         getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None)),
    )
    for categoria in (datos.get("categorias") or []):
        _insertar_categoria(conn, fila["id"], categoria)
    _auditar(conn, fila["id"], clave, "alta", actor,
             {"tipo": tipo, "es_especial": es_especial})
    salida = obtener(conn, fila["id"])
    if es_especial:
        salida["advertencia"] = ADVERTENCIA_ESPECIAL
    return salida


def _insertar_categoria(conn, atributo_id, datos):
    if isinstance(datos, str):
        datos = {"clave": datos, "etiqueta": datos}
    # A diferencia de la clave del atributo, la de una categoría conserva
    # mayúsculas y acentos: `F`/`M`, los tramos (`65+`) y los departamentos
    # (`Río Negro`) son las claves de siempre, y cambiarlas rompería los
    # objetivos de composición ya cargados.
    clave = (datos.get("clave") or "").strip()
    if not clave:
        raise DatosInvalidos("Falta la clave de la categoría.")
    etiqueta = (datos.get("etiqueta") or clave).strip()
    if db.una(conn,
              "select 1 from atributo_categoria where atributo_id = %s and clave = %s",
              (atributo_id, clave)):
        raise Conflicto(f"La categoría «{clave}» ya existe en este atributo.")
    fila = db.una(
        conn,
        """
        insert into atributo_categoria (atributo_id, clave, etiqueta, orden)
             values (%s, %s, %s, coalesce(%s, 100))
          returning id, clave, etiqueta, orden, activo
        """,
        (atributo_id, clave, etiqueta, datos.get("orden")),
    )
    return _serializar_categoria(fila)


def agregar_categoria(conn, atributo_id, datos, actor=None):
    atributo = obtener(conn, atributo_id)
    if atributo["tipo"] not in ("categorico", "derivado"):
        raise DatosInvalidos(
            f"«{atributo['clave']}» es de tipo {atributo['tipo']}: no tiene "
            f"categorías.")
    categoria = _insertar_categoria(conn, atributo["id"], datos)
    _auditar(conn, atributo["id"], atributo["clave"], "categoria", actor,
             {"accion": "alta", "categoria": categoria["clave"]})
    return categoria


def editar_categoria(conn, atributo_id, categoria_id, cambios, actor=None):
    """Solo la etiqueta visible y el orden. La clave no se toca: es lo que
    guardan los objetivos de composición y los valores ya cargados, y
    cambiarla los dejaría apuntando a nada."""
    atributo = obtener(conn, atributo_id)
    actual = db.una(
        conn,
        "select id, clave, etiqueta, orden, activo from atributo_categoria "
        "where id = %s and atributo_id = %s",
        (categoria_id, atributo["id"]))
    if not actual:
        raise NoEncontrado(f"No existe la categoría {categoria_id}.")
    if "clave" in cambios and (cambios["clave"] or "").strip() != actual["clave"]:
        raise Conflicto(
            "La clave de una categoría no se cambia: es lo que guardan los "
            "objetivos de composición y los valores ya cargados. Se puede "
            "cambiar su etiqueta visible.")
    etiqueta = (cambios.get("etiqueta") or actual["etiqueta"]).strip()
    activo = cambios.get("activo")
    fila = db.una(
        conn,
        """
        update atributo_categoria
           set etiqueta = %s,
               orden = coalesce(%s, orden),
               activo = coalesce(%s, activo)
         where id = %s
     returning id, clave, etiqueta, orden, activo
        """,
        (etiqueta, cambios.get("orden"),
         None if activo is None else bool(activo), categoria_id),
    )
    _auditar(conn, atributo["id"], atributo["clave"], "categoria", actor,
             {"accion": "edicion", "categoria": actual["clave"]})
    return _serializar_categoria(fila)


def editar(conn, atributo_id, cambios, actor=None):
    """Etiqueta, descripción, orden y la marca de especial. **La clave no**,
    si ya hay datos cargados: es el identificador estable con el que la
    nombran las cuotas y las consultas guardadas."""
    actual = obtener(conn, atributo_id)
    nueva_clave = (cambios.get("clave") or "").strip().lower()
    if nueva_clave and nueva_clave != actual["clave"]:
        if actual["tiene_datos"]:
            raise Conflicto(
                f"«{actual['clave']}» ya tiene datos cargados, así que su "
                f"clave no se puede cambiar: es lo que guardan los objetivos "
                f"de composición y los valores de cada persona. Sí se puede "
                f"cambiar su etiqueta visible.")
        if actual["del_nucleo"]:
            raise Conflicto(
                f"«{actual['clave']}» es uno de los segmentadores que el resto "
                f"del sistema nombra por su clave. Cambiarla rompería la "
                f"composición y el muestreo.")
        nueva_clave = _clave_valida(nueva_clave)
    else:
        nueva_clave = actual["clave"]

    es_especial = cambios.get("es_especial")
    fila = db.una(
        conn,
        """
        update atributo_demografico
           set clave = %s,
               etiqueta = coalesce(%s, etiqueta),
               descripcion = coalesce(%s, descripcion),
               orden = coalesce(%s, orden),
               es_especial = coalesce(%s, es_especial)
         where id = %s
     returning id, clave, etiqueta, tipo, descripcion, es_especial, activo,
               orden, creado_en
        """,
        (nueva_clave,
         (cambios.get("etiqueta") or "").strip() or None,
         (cambios.get("descripcion") or "").strip() or None,
         cambios.get("orden"),
         None if es_especial is None else bool(es_especial),
         actual["id"]),
    )
    _auditar(conn, actual["id"], nueva_clave, "edicion", actor,
             {"antes": {k: actual[k] for k in
                        ("clave", "etiqueta", "es_especial", "orden")}})
    salida = obtener(conn, fila["id"])
    if es_especial and not actual["es_especial"]:
        salida["advertencia"] = ADVERTENCIA_ESPECIAL
    return salida


def desactivar(conn, atributo_id, activo=False, actor=None):
    """Un atributo con datos no se borra: se desactiva. Deja de ofrecerse en
    cargas y filtros nuevos y conserva todo lo cargado."""
    actual = obtener(conn, atributo_id)
    if not activo and actual["del_nucleo"]:
        raise Conflicto(
            f"«{actual['clave']}» es uno de los segmentadores sobre los que "
            f"funcionan la composición, las cuotas y el muestreo: no se puede "
            f"desactivar.")
    db.ejecutar(conn, "update atributo_demografico set activo = %s where id = %s",
                (bool(activo), actual["id"]))
    _auditar(conn, actual["id"], actual["clave"],
             "reactivacion" if activo else "desactivacion", actor)
    return obtener(conn, atributo_id)


def eliminar(conn, atributo_id, actor=None):
    """Solo si no tiene ningún valor cargado. Con datos, se desactiva."""
    actual = obtener(conn, atributo_id)
    if actual["del_nucleo"]:
        raise Conflicto(
            f"«{actual['clave']}» es uno de los segmentadores del núcleo: no "
            f"se elimina.")
    if actual["tiene_datos"]:
        raise Conflicto(
            f"«{actual['clave']}» tiene valores cargados. Borrarlo los "
            f"perdería sin dejar rastro, así que no se borra: se desactiva, y "
            f"deja de ofrecerse en cargas y filtros nuevos.",
            {"atributo_id": actual["id"], "alternativa": "desactivar"})
    db.ejecutar(conn, "delete from atributo_demografico where id = %s", (actual["id"],))
    _auditar(conn, None, actual["clave"], "baja", actor)
    return {"id": actual["id"], "clave": actual["clave"], "estado": "eliminado"}


# ── Valores por persona ──────────────────────────────────────────────

def _resolver_categoria(conn, atributo, crudo):
    """Del valor del archivo a la categoría canónica. Devuelve `None` si no
    corresponde a ninguna: **no se inventa** (R3.14.c)."""
    texto = str(crudo).strip()
    if not texto:
        return None
    fila = db.una(
        conn,
        """
        select id, clave from atributo_categoria
         where atributo_id = %s
           and (clave = %s or lower(clave) = lower(%s) or lower(etiqueta) = lower(%s))
         order by (clave = %s) desc
         limit 1
        """,
        (atributo["id"], texto, texto, texto, texto),
    )
    return fila


def valores_de(conn, id_persona, incluir_especiales=True, momento=None):
    """Los atributos efectivos de una persona, resueltos por la vista.

    Es lo que ve la ficha: valor, etiqueta, de dónde salió y —para los
    derivados— si se derivó, se envejeció o se cargó tal cual (R3.14.g).

    R4.1.a — con `momento` devuelve los valores que estaban vigentes en esa
    fecha, incluidos los derivados: la edad de una persona en una ola de 2024
    es la que tenía en 2024, no la de hoy. Sin `momento`, el comportamiento es
    exactamente el de antes.
    """
    filas = db.todas(
        conn,
        """
        select v.atributo, v.valor, v.etiqueta_valor, v.valor_num, v.valor_fecha,
               v.valor_crudo, v.origen, v.procedencia,
               a.etiqueta as atributo_etiqueta, a.tipo, a.es_especial, a.orden,
               pa.fecha_referencia
          from f_atributo_persona(coalesce(%s::timestamptz, now())) v
          join atributo_demografico a on a.id = v.atributo_id
          left join persona_atributo pa
                 on pa.id_persona = v.id_persona and pa.atributo_id = v.atributo_id
                and pa.desde <= coalesce(%s::timestamptz, now())
                and (pa.hasta is null
                     or coalesce(%s::timestamptz, now()) < pa.hasta)
         where v.id_persona = %s
           and (%s::bool is not false or not a.es_especial)
         order by a.orden, a.clave
        """,
        (momento, momento, momento, id_persona, incluir_especiales),
    )
    return [
        {
            "clave": f["atributo"],
            "etiqueta": f["atributo_etiqueta"],
            "tipo": f["tipo"],
            "es_especial": f["es_especial"],
            "valor": f["valor"],
            "etiqueta_valor": f["etiqueta_valor"],
            "valor_num": float(f["valor_num"]) if f["valor_num"] is not None else None,
            "valor_fecha": f["valor_fecha"].isoformat() if f["valor_fecha"] else None,
            "valor_crudo": f["valor_crudo"],
            "origen": f["origen"],
            # Para un derivado: `derivado` (de la fecha de nacimiento),
            # `envejecido` (de la edad declarada) o `cargado`. Va a la ficha
            # para que la precisión del dato sea visible.
            "procedencia": f["procedencia"],
            "fecha_referencia": (
                f["fecha_referencia"].isoformat() if f["fecha_referencia"] else None),
        }
        for f in filas
    ]


def _antes(a, b):
    """¿`a` es anterior a `b`? Compara aunque uno venga como texto ISO."""
    import datetime

    def normalizar(v):
        if isinstance(v, str):
            return datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    a, b = normalizar(a), normalizar(b)
    if a.tzinfo is None and b.tzinfo is not None:
        a = a.replace(tzinfo=b.tzinfo)
    elif b.tzinfo is None and a.tzinfo is not None:
        b = b.replace(tzinfo=a.tzinfo)
    return a < b


def historial_de(conn, id_persona, clave=None):
    """R4.1.a — todos los valores que tuvo una persona, con su vigencia.

    El valor actual es el de `hasta = None`. Los derivados no aparecen: no se
    guardan, se calculan, y su «historial» es el de la entrada de la que
    derivan (la edad declarada) más el paso del tiempo.
    """
    filas = db.todas(
        conn,
        """
        select a.clave, a.etiqueta as atributo_etiqueta, a.tipo, a.es_especial,
               coalesce(c.clave, pa.valor_num::text, pa.valor_fecha::text) as valor,
               coalesce(c.etiqueta, pa.valor_num::text,
                        pa.valor_fecha::text) as etiqueta_valor,
               pa.valor_crudo, pa.origen, pa.hasta, pa.actualizado_en,
               case when pa.desde = '-infinity'::timestamptz then null
                    else pa.desde end as desde
          from persona_atributo pa
          join atributo_demografico a on a.id = pa.atributo_id
          left join atributo_categoria c on c.id = pa.categoria_id
         where pa.id_persona = %s
           and (%s::text is null or a.clave = %s::text)
         order by a.orden, a.clave, pa.desde, pa.id
        """,
        (id_persona, clave, clave),
    )
    return [
        {
            "clave": f["clave"],
            "etiqueta": f["atributo_etiqueta"],
            "tipo": f["tipo"],
            "es_especial": f["es_especial"],
            "valor": f["valor"],
            "etiqueta_valor": f["etiqueta_valor"],
            "valor_crudo": f["valor_crudo"],
            "origen": f["origen"],
            # `-infinity` es el primer valor conocido: sabemos cuándo cambió,
            # no cuándo empezó. Se informa como `None` para que la pantalla
            # pueda decirlo con palabras en vez de mostrar una fecha falsa.
            # `None` = el primer valor conocido: sabemos cuándo cambió, no
            # cuándo empezó. La pantalla lo dice con palabras en vez de
            # mostrar una fecha falsa.
            "desde": f["desde"].isoformat() if f["desde"] else None,
            "hasta": f["hasta"].isoformat() if f["hasta"] else None,
            "vigente": f["hasta"] is None,
        }
        for f in filas
    ]


def _fila_actual(conn, id_persona, atributo_id):
    """La fila vigente, que es la de vigencia abierta (R4.1.a)."""
    return db.una(
        conn,
        """
        select pa.id, pa.categoria_id, pa.valor_num, pa.valor_fecha,
               pa.valor_crudo, c.clave as categoria,
               -- `-infinity` no entra en un `datetime` de Python, y además
               -- `None` es justo lo que significa: no sabemos desde cuándo.
               case when pa.desde = '-infinity'::timestamptz then null
                    else pa.desde end as desde,
               -- `now()` es la hora de inicio de la transacción, así que esto
               -- es cierto exactamente cuando la fila la escribió esta misma
               -- transacción. Ver el comentario de `fijar`.
               (pa.actualizado_en = now()) as de_esta_transaccion
          from persona_atributo pa
          left join atributo_categoria c on c.id = pa.categoria_id
         where pa.id_persona = %s and pa.atributo_id = %s
           and pa.hasta is null
        """,
        (id_persona, atributo_id),
    )


def fijar(conn, id_persona, clave, valor, origen="edicion", crudo=None,
          fecha_referencia=None, pisar=True, vigencia_desde=None):
    """Fija el valor de un atributo para una persona.

    `pisar=False` implementa la regla del addendum R3.9.d, que es la que rige
    en las cargas: **si ya hay un valor distinto no se sobrescribe**, se
    informa la discrepancia y decide un responsable. El archivo de un estudio
    no es autoridad sobre la ficha del panelista.

    Devuelve `{"estado": "completado"|"sin_cambio"|"discrepancia"|
    "sin_categoria", ...}`.
    """
    atributo = obtener(conn, clave)
    crudo = crudo if crudo is not None else (
        None if valor is None else str(valor))

    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return {"estado": "sin_valor", "clave": atributo["clave"]}

    categoria_id = valor_num = valor_fecha = None
    canonico = None
    if atributo["tipo"] in ("categorico",) or (
            atributo["tipo"] == "derivado" and atributo["clave"] == "tramo_etario"):
        categoria = _resolver_categoria(conn, atributo, valor)
        if not categoria:
            # R3.14.c — no se inventa la categoría. La fila queda sin este
            # atributo y el resultado de la carga lo informa: inventarla es
            # exactamente lo que rompe la aritmética de las cuotas.
            return {"estado": "sin_categoria", "clave": atributo["clave"],
                    "valor_crudo": crudo}
        categoria_id, canonico = categoria["id"], categoria["clave"]
    elif atributo["tipo"] == "fecha":
        valor_fecha = str(valor).strip()[:10]
        canonico = valor_fecha
    else:
        try:
            valor_num = float(str(valor).strip().replace(",", "."))
        except (TypeError, ValueError):
            return {"estado": "no_numerico", "clave": atributo["clave"],
                    "valor_crudo": crudo}
        canonico = str(valor_num)

    actual = _fila_actual(conn, id_persona, atributo["id"])
    if actual:
        mismo = (
            (categoria_id is not None and actual["categoria_id"] == categoria_id)
            or (valor_num is not None and actual["valor_num"] is not None
                and float(actual["valor_num"]) == valor_num)
            or (valor_fecha is not None and actual["valor_fecha"] is not None
                and actual["valor_fecha"].isoformat() == valor_fecha)
        )
        if mismo:
            return {"estado": "sin_cambio", "clave": atributo["clave"],
                    "valor": canonico}
        if not pisar:
            return {
                "estado": "discrepancia",
                "clave": atributo["clave"],
                "en_boveda": actual["categoria"] or (
                    str(actual["valor_num"]) if actual["valor_num"] is not None
                    else (actual["valor_fecha"].isoformat()
                          if actual["valor_fecha"] else None)),
                "en_el_archivo": canonico,
            }

    # R4.1.a — el valor anterior no se pisa: se cierra. El nuevo abre su
    # vigencia en el mismo instante en que el viejo la cierra, y como el
    # intervalo es semiabierto no hay ningún momento con dos valores.
    #
    # `vigencia_desde` existe para cargar un cambio que ya ocurrió: un archivo
    # de campo de hace seis meses trae la situación de entonces, no la de hoy.
    # Sin él, la única fecha posible sería la de la carga y el historial diría
    # que la persona cambió el día que alguien subió el archivo.
    if actual and vigencia_desde is not None and actual["desde"] is not None:
        if _antes(vigencia_desde, actual["desde"]):
            # Insertar un valor **anterior** al que ya está vigente no es
            # cerrar un período: es reescribir el pasado, y no hay una forma
            # correcta de adivinar dónde encaja. Se informa y decide una
            # persona, igual que con una discrepancia de carga.
            raise Conflicto(
                f"El valor que se quiere cargar para «{atributo['clave']}» "
                f"vale desde una fecha anterior a la del valor vigente. "
                f"Corregir el historial hacia atrás es una edición aparte.",
                {"vigente_desde": str(actual["desde"]),
                 "se_pide_desde": str(vigencia_desde)})

    # ── Cómo se mueve la vigencia ────────────────────────────────────
    # Un cambio cierra el valor anterior en `now()` —la hora de inicio de la
    # transacción— y abre el nuevo en ese mismo instante, para que no quede un
    # hueco en el medio.
    #
    # El caso que hay que tratar aparte es el de **dos cambios en la misma
    # transacción**, que es lo que hace una carga que corrige un valor que
    # acaba de escribir. Ahí `now()` es el mismo para los dos, y cerrar el
    # primero dejaría un intervalo que afirma que ese valor rigió desde
    # siempre hasta ahora, cuando en realidad nunca existió fuera de la
    # transacción. Eso envenenaría toda composición retroactiva. Una
    # transacción es atómica: desde afuera, el único valor que existió es el
    # último. Así que en ese caso el valor se **pisa**, que es lo que
    # corresponde, y el historial no registra un cambio que nadie pudo ver.
    if actual and actual["de_esta_transaccion"] and vigencia_desde is None:
        db.ejecutar(
            conn,
            """
            update persona_atributo
               set categoria_id = %s, valor_num = %s, valor_fecha = %s,
                   valor_crudo = %s, origen = %s, fecha_referencia = %s,
                   actualizado_en = now()
             where id = %s
            """,
            (categoria_id, valor_num, valor_fecha, crudo, origen,
             fecha_referencia, actual["id"]))
        return {"estado": "completado", "clave": atributo["clave"],
                "valor": canonico}

    if actual:
        db.ejecutar(
            conn,
            "update persona_atributo "
            "   set hasta = coalesce(%s::timestamptz, now()) where id = %s",
            (vigencia_desde, actual["id"]))

    # R4.1.a — el **primer** valor de un atributo vale desde siempre. Sabemos
    # cuándo un valor cambió; no sabemos cuándo el primero empezó, y ponerle
    # la fecha de carga dejaría a la persona sin dato en toda composición
    # retroactiva anterior a su alta. Es la misma regla con la que la
    # migración 0010 sembró lo que ya existía.
    primero = not actual and vigencia_desde is None

    db.ejecutar(
        conn,
        """
        insert into persona_atributo
               (id_persona, atributo_id, categoria_id, valor_num, valor_fecha,
                valor_crudo, origen, fecha_referencia, desde, actualizado_en)
             values (%s, %s, %s, %s, %s, %s, %s, %s,
                     case when %s then '-infinity'::timestamptz
                          else coalesce(%s::timestamptz, now()) end,
                     now())
        """,
        (id_persona, atributo["id"], categoria_id, valor_num, valor_fecha,
         crudo, origen, fecha_referencia, primero, vigencia_desde),
    )
    resultado = {"estado": "completado", "clave": atributo["clave"],
                 "valor": canonico}
    if actual:
        resultado["valor_anterior"] = actual["categoria"] or (
            str(actual["valor_num"]) if actual["valor_num"] is not None
            else (actual["valor_fecha"].isoformat()
                  if actual["valor_fecha"] else None))
    return resultado


def borrar_valor(conn, id_persona, clave):
    """Deja a la persona sin valor vigente para el atributo.

    R4.1.a — cierra la vigencia en vez de borrar la fila. Que hoy no tenga
    valor no significa que nunca lo haya tenido, y una composición de una ola
    pasada tiene que seguir contándola en la categoría de entonces.
    """
    atributo = obtener(conn, clave)
    actual = _fila_actual(conn, id_persona, atributo["id"])
    if not actual:
        return {"clave": atributo["clave"], "borrados": 0, "cerrados": 0}
    if actual["de_esta_transaccion"]:
        # Escrito y borrado en la misma transacción: nunca existió afuera, y
        # cerrarlo dejaría en el historial un período que nadie vivió.
        n = db.ejecutar(conn, "delete from persona_atributo where id = %s",
                        (actual["id"],))
        return {"clave": atributo["clave"], "borrados": n, "cerrados": 0}
    n = db.ejecutar(
        conn, "update persona_atributo set hasta = now() where id = %s",
        (actual["id"],))
    return {"clave": atributo["clave"], "borrados": n, "cerrados": n}


# ── R3.14.h · Corregir un mapeo sin recargar el archivo ──────────────

def recalcular(conn, atributo_id, actor=None):
    """Recalcula los valores canónicos a partir de los crudos guardados.

    Es la salvaguarda de la canonización: si el vocabulario se corrigió
    —se agregó la categoría que faltaba, se arregló una etiqueta— los valores
    que habían quedado mal apuntados se vuelven a resolver contra el catálogo
    actual, sin volver a pedir el archivo original.
    """
    atributo = obtener(conn, atributo_id)
    if atributo["tipo"] != "categorico":
        raise DatosInvalidos(
            f"El recálculo resuelve valores crudos contra las categorías del "
            f"atributo, y «{atributo['clave']}» es de tipo {atributo['tipo']}.")

    filas = db.todas(
        conn,
        """
        select pa.id, pa.id_persona, pa.categoria_id, pa.valor_crudo
          from persona_atributo pa
         where pa.atributo_id = %s and pa.valor_crudo is not null
        """,
        (atributo["id"],))

    cambiados, sin_categoria = 0, []
    for fila in filas:
        categoria = _resolver_categoria(conn, atributo, fila["valor_crudo"])
        if not categoria:
            sin_categoria.append(fila["valor_crudo"])
            continue
        if categoria["id"] == fila["categoria_id"]:
            continue
        db.ejecutar(
            conn,
            "update persona_atributo set categoria_id = %s, actualizado_en = now() "
            "where id = %s",
            (categoria["id"], fila["id"]))
        cambiados += 1

    resultado = {
        "clave": atributo["clave"],
        "revisados": len(filas),
        "recalculados": cambiados,
        "sin_categoria": sorted(set(sin_categoria)),
    }
    _auditar(conn, atributo["id"], atributo["clave"], "recalculo", actor, resultado)
    return resultado
