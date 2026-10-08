"""R2.2 y R2.3 — universo de referencia, composición observada y brecha.

Un panel no se juzga por su tamaño sino por su parecido con el universo que
pretende representar. Este módulo hace las dos mitades de eso:

* **R2.2 — cargar el objetivo.** Un universo de referencia es un conjunto de
  proporciones por dimensión y categoría: sexo 52 % F / 48 % M, tramo etario
  18-24 14 %, 25-34 19 %, etc. Se valida que las proporciones de **cada
  dimensión sumen 1**; una dimensión que suma 0,8 o 1,3 no es un universo,
  es un error de carga, y aceptarlo haría que todas las brechas de esa
  dimensión mientan.

* **R2.3 — describir y comparar.** Con objetivo cargado se devuelve la
  composición observada junto a la brecha. Sin objetivo se devuelve la
  composición observada igual, y la brecha se marca **no disponible**. Es la
  diferencia entre «no hay brecha» y «no se puede calcular la brecha», y la
  respuesta las distingue explícitamente.

Y el cruce de dos dimensiones (P1): sexo × tramo etario, que es donde
aparecen los huecos que cada dimensión por separado esconde (un panel puede
tener bien el sexo y bien la edad, y no tener ninguna mujer de más de 65).

Todo pasa en la bóveda: los atributos demográficos son autoritativos acá.

**R-ORG.2 — el ámbito.** La composición se calcula sobre un conjunto de
personas, y ese conjunto ya no es siempre un panel:

* **todos** — la bóveda entera, incluidas las personas cargadas sin panel
  (R3.13), que hasta acá no aparecían en ninguna composición;
* **panel** — los miembros, lo de siempre y con el mismo SQL;
* **carga** — las personas vinculadas a una carga (R-ORG.1), creadas o
  reutilizadas.

Lo único que cambia entre los tres es *quién cuenta*; cómo se cuenta, cómo
se separan los «sin dato» y cómo se calcula la brecha es la misma función.
Objetivo admiten «panel» y «todos»; una carga no (R-ORG.3, D67): es un hecho
del pasado y no se corrige reclutando, así que su brecha es «no disponible»
con ese motivo, nunca un cero.

**R4.1.a — la composición a fecha.** Con `momento`, la composición se calcula
con los atributos que estaban vigentes entonces y con la membresía que existía
entonces. Es una corrección, no un extra: sin historial, recalcular la
composición de una ola del año pasado la calculaba con la demografía de hoy y
devolvía un número que no era el de esa ola, sin avisar de nada. Una cuota que
cerró con 30 % de menores de 35 puede mostrar 22 % un año después sin que nadie
se haya ido del panel, solo porque esa gente cumplió años.
"""

from . import db, demografia
from .errores import DatosInvalidos, NoEncontrado

TOLERANCIA = 0.005
"""Cuánto se le perdona a la suma de proporciones de una dimensión. Un
universo cargado a dos decimales no suma exactamente 1 casi nunca (0,52 +
0,48 sí, pero siete tramos etarios redondeados, no), así que se acepta medio
punto porcentual de desvío. Más que eso es un error de carga."""


def _validar_objetivos(objetivos, categoricas=None):
    """Deja los objetivos en forma canónica y verifica cada dimensión.

    La validación es por dimensión, no sobre el total: cargar solo `sexo`
    tiene que poder hacerse, y entonces el total del panel es 1, no 2.

    R3.14 — `categoricas` son las dimensiones de cuota admitidas, que salen
    del catálogo de atributos. Sin ellas se valida contra el núcleo.
    """
    if not objetivos:
        raise DatosInvalidos("No hay objetivos para cargar.")
    categoricas = list(categoricas or demografia.DIMENSIONES_CATEGORICAS)

    por_dimension = {}
    for crudo in objetivos:
        if not isinstance(crudo, dict):
            raise DatosInvalidos(f"Objetivo mal formado: {crudo!r}.")
        dimension = (crudo.get("dimension") or "").strip().lower()
        if dimension not in categoricas:
            raise DatosInvalidos(
                f"Dimensión desconocida para composición: {dimension!r}.",
                {"dimensiones_validas": list(categoricas)},
            )
        categoria = str(crudo.get("categoria") or "").strip()
        if not categoria:
            raise DatosInvalidos(
                f"Falta la categoría de un objetivo de «{dimension}»."
            )
        crudo_proporcion = crudo.get("proporcion")
        if crudo_proporcion is None:
            crudo_proporcion = crudo.get("proporcion_objetivo")
        try:
            proporcion = float(crudo_proporcion)
        except (TypeError, ValueError):
            raise DatosInvalidos(
                f"Proporción inválida en {dimension}/{categoria}: "
                f"{crudo_proporcion!r}."
            )
        if not 0 <= proporcion <= 1:
            raise DatosInvalidos(
                f"La proporción de {dimension}/{categoria} tiene que estar entre "
                f"0 y 1; llegó {proporcion}. Si viene en porcentaje, dividila "
                f"por 100."
            )
        anterior = por_dimension.setdefault(dimension, {})
        if categoria in anterior:
            raise DatosInvalidos(
                f"La categoría {categoria} de {dimension} viene dos veces."
            )
        anterior[categoria] = proporcion

    for dimension, categorias in por_dimension.items():
        suma = sum(categorias.values())
        if abs(suma - 1.0) > TOLERANCIA:
            raise DatosInvalidos(
                f"Las proporciones de «{dimension}» suman {suma:.4f} y tienen "
                f"que sumar 1. Un universo de referencia incompleto haría que "
                f"todas las brechas de esa dimensión estén mal.",
                {
                    "dimension": dimension,
                    "suma": round(suma, 6),
                    "tolerancia": TOLERANCIA,
                    "categorias": categorias,
                },
            )
    return por_dimension


# R-ORG.2 — los tres ámbitos. El orden es el de la pantalla.
TODOS = "todos"
PANEL = "panel"
CARGA = "carga"
AMBITOS = (TODOS, PANEL, CARGA)
# R-ORG.3 — los que admiten universo de referencia (D67).
AMBITOS_CON_OBJETIVO = (PANEL, TODOS)


def _donde_objetivo(ambito, panel_id):
    """El filtro de `objetivo_composicion` para un ámbito."""
    if ambito == TODOS:
        return "ambito = 'todos'", ()
    return "ambito = 'panel' and panel_id = %s", (panel_id,)


def _guardar(conn, ambito, panel_id, objetivos):
    por_dimension = _validar_objetivos(objetivos, demografia.categoricas(conn))
    donde, params = _donde_objetivo(ambito, panel_id)
    for dimension, categorias in por_dimension.items():
        db.ejecutar(
            conn,
            f"delete from objetivo_composicion where {donde} and dimension = %s",
            params + (dimension,),
        )
        for categoria, proporcion in categorias.items():
            db.ejecutar(
                conn,
                """
                insert into objetivo_composicion
                       (ambito, panel_id, dimension, categoria, proporcion_objetivo)
                     values (%s, %s, %s, %s, %s)
                """,
                (ambito, panel_id if ambito == PANEL else None, dimension,
                 categoria, proporcion),
            )


def guardar_objetivo(conn, panel_id, objetivos):
    """Carga (o reemplaza) el universo de referencia de un panel.

    Reemplaza **por dimensión**: cargar sexo no borra el tramo etario que ya
    estaba, pero volver a cargar sexo sí reemplaza las categorías anteriores
    de sexo. Si no fuera así, quitar una categoría del universo sería
    imposible desde la app.
    """
    if not db.una(conn, "select 1 from panel where id = %s", (panel_id,)):
        raise NoEncontrado(f"No existe el panel {panel_id}.")
    _guardar(conn, PANEL, panel_id, objetivos)
    return obtener_objetivo(conn, panel_id)


def guardar_objetivo_de_todos(conn, objetivos):
    """R-ORG.3 — el universo de referencia de **toda** la bóveda.

    Es otro universo que el de cualquier panel, y por eso es otra fila: el
    objetivo del panel nacional y el del total de la bóveda no se pisan.
    Mismas reglas de validación y de reemplazo por dimensión.
    """
    _guardar(conn, TODOS, None, objetivos)
    return obtener_objetivo_de_todos(conn)


def _leer(conn, ambito, panel_id):
    donde, params = _donde_objetivo(ambito, panel_id)
    filas = db.todas(
        conn,
        f"""
        select dimension, categoria, proporcion_objetivo
          from objetivo_composicion
         where {donde}
         order by dimension, categoria
        """,
        params,
    )
    por_dimension = {}
    for f in filas:
        por_dimension.setdefault(f["dimension"], {})[f["categoria"]] = float(
            f["proporcion_objetivo"]
        )
    return {
        "dimensiones": por_dimension,
        "items": [
            {
                "dimension": f["dimension"],
                "categoria": f["categoria"],
                "proporcion_objetivo": float(f["proporcion_objetivo"]),
            }
            for f in filas
        ],
    }


def obtener_objetivo(conn, panel_id):
    return {"panel_id": panel_id, **_leer(conn, PANEL, panel_id)}


def obtener_objetivo_de_todos(conn):
    return {"ambito": TODOS, **_leer(conn, TODOS, None)}


def _borrar(conn, ambito, panel_id, dimension=None):
    donde, params = _donde_objetivo(ambito, panel_id)
    if dimension:
        return db.ejecutar(
            conn,
            f"delete from objetivo_composicion where {donde} and dimension = %s",
            params + (dimension,),
        )
    return db.ejecutar(
        conn, f"delete from objetivo_composicion where {donde}", params)


def borrar_objetivo(conn, panel_id, dimension=None):
    """Saca el objetivo de una dimensión (o el del panel entero).

    Después de esto la composición de esa dimensión vuelve a ser puramente
    descriptiva, con la brecha marcada no disponible.
    """
    n = _borrar(conn, PANEL, panel_id, dimension)
    return {"panel_id": panel_id, "dimension": dimension, "borradas": n}


def borrar_objetivo_de_todos(conn, dimension=None):
    n = _borrar(conn, TODOS, None, dimension)
    return {"ambito": TODOS, "dimension": dimension, "borradas": n}


SIN_DATO = "(sin dato)"
"""Cómo se nombra a quien no tiene valor para la dimensión.

R3.14.d — se informa **aparte**, nunca dentro de una categoría: si los sin
dato engrosaran una categoría real, la brecha de esa categoría mentiría y la
cuota se cerraría con gente que no se sabe si corresponde."""


_MEMBRESIA_VIGENTE = """(
     case when %s::timestamptz is null
          -- Sin fecha: el estado de hoy, que es el comportamiento de siempre.
          then (%s::text is null or m.estado = %s::text)
          -- R4.1.a — a una fecha pasada, «activo» no es el estado de hoy sino
          -- el de entonces. Alguien que se dio de baja el año pasado **estaba**
          -- en el panel el año anterior, y contarlo como baja al recalcular esa
          -- ola la deja con menos gente de la que tuvo.
          else m.fecha_alta <= %s::timestamptz
               and (%s::text is distinct from 'activo'
                    or m.fecha_baja is null or %s::timestamptz < m.fecha_baja)
     end)"""


def _params_membresia(estado, momento):
    return (momento, estado, estado, momento, estado, momento)


def _universo_panel(panel_id, estado="activo", momento=None):
    """Quiénes cuentan en la composición de un panel: sus miembros.

    Devuelve `(from, where, parámetros)`. Las tres consultas que
    cuentan (total, por dimensión, cruce) lo usan igual, así que el ámbito
    se decide en un solo lugar.
    """
    return (
        "from membresia m join persona p on p.id_persona = m.id_persona",
        "m.panel_id = %s and " + _MEMBRESIA_VIGENTE,
        (panel_id,) + _params_membresia(estado, momento),
    )


def _universo_todos():
    """R-ORG.2 — la bóveda entera, con y sin panel."""
    return "from persona p", "true", ()


def _universo_carga(carga_id):
    """R-ORG.2 — las personas vinculadas a la carga, creadas o reutilizadas.

    Mira la carga **desde hoy**: quien se dio de baja ya no tiene fila ni
    vínculo, así que no aparece. La pantalla lo dice.
    """
    return (
        "from persona_carga pc join persona p on p.id_persona = pc.id_persona",
        "pc.carga_id = %s",
        (carga_id,),
    )


def _observada(conn, universo, dimension, momento=None):
    """Cuántas personas del universo hay en cada categoría de una dimensión.

    R3.14 — la dimensión puede ser cualquier atributo del catálogo, así que
    el conteo va contra la resolución de atributos y no contra columnas fijas.
    Quien no tiene valor no tiene fila: cae en `(sin dato)`.

    R4.1.a — con `momento`, el valor de cada persona es el que estaba vigente
    entonces (y, en un panel, la membresía también: quien se incorporó
    después no estaba en el panel ese día).
    """
    desde, donde, params = universo
    filas = db.todas(
        conn,
        f"""
        select coalesce(va.valor, %s) as categoria,
               count(*)::int as observados
          {desde}
          left join f_atributo_persona(coalesce(%s::timestamptz, now())) va
                 on va.id_persona = p.id_persona and va.atributo = %s
         where {donde}
         group by 1
         order by 1
        """,
        (SIN_DATO, momento, dimension) + params,
    )
    return {f["categoria"]: f["observados"] for f in filas}


def _contar(conn, universo):
    desde, donde, params = universo
    return db.una(conn, f"select count(*)::int as n {desde} where {donde}",
                  params)["n"]


def contar_miembros(conn, panel_id, estado="activo", momento=None):
    return _contar(conn, _universo_panel(panel_id, estado, momento))


def _dimension(conn, universo, dimension, objetivos, total, momento=None,
               motivo_sin_objetivo=None, quienes="miembro(s)"):
    observada = _observada(conn, universo, dimension, momento)
    del_objetivo = objetivos.get(dimension) or {}
    hay_objetivo = bool(del_objetivo)

    # R3.14.d — los «sin dato» salen de la lista de categorías y se informan
    # aparte. Dejarlos adentro tenía dos efectos, los dos malos: aparecían
    # como una categoría de cuota que nadie cargó, y su peso en el
    # denominador bajaba la proporción observada de todas las demás, con lo
    # cual la brecha marcaba un déficit que no existía.
    sin_dato = observada.pop(SIN_DATO, 0)
    con_dato = sum(observada.values())

    categorias = []
    for categoria in sorted(set(observada) | set(del_objetivo)):
        observados = observada.get(categoria, 0)
        # La composición se compara contra el universo **entre quienes tienen
        # el dato**: es la única base sobre la que las proporciones suman 1 y
        # la comparación significa algo.
        proporcion = (observados / con_dato) if con_dato else 0.0
        fila = {
            "categoria": categoria,
            "observados": observados,
            "proporcion_observada": round(proporcion, 4),
            "proporcion_objetivo": None,
            "brecha": None,
            "faltan": None,
        }
        if hay_objetivo and categoria in del_objetivo:
            objetivo = del_objetivo[categoria]
            # El faltante, en cambio, se cuenta contra el panel entero: es
            # gente que hay que sumar, y el panel es del tamaño que es.
            esperados = round(objetivo * total)
            fila.update({
                "proporcion_objetivo": round(objetivo, 4),
                # Brecha en puntos de proporción: negativa = falta gente.
                "brecha": round(proporcion - objetivo, 4),
                "faltan": max(0, esperados - observados),
                "sobran": max(0, observados - esperados),
                "esperados": esperados,
            })
        categorias.append(fila)

    return {
        "dimension": dimension,
        "brecha_disponible": hay_objetivo,
        "motivo_sin_brecha": None if hay_objetivo else (
            motivo_sin_objetivo
            or f"No hay universo de referencia cargado para «{dimension}»: la "
               f"composición es descriptiva y la brecha no se puede calcular."
        ),
        "categorias": categorias,
        "con_dato": con_dato,
        "sin_dato": sin_dato,
        "proporcion_sin_dato": round(sin_dato / total, 4) if total else 0.0,
        "aviso_sin_dato": (
            f"{sin_dato} {quienes} no tienen «{dimension}» cargado. No se "
            f"cuentan en ninguna categoría: las proporciones de arriba son "
            f"sobre los {con_dato} que sí lo tienen."
        ) if sin_dato else None,
        # Índice de disimilitud: la mitad de la suma de las diferencias
        # absolutas. Se lee como «qué fracción del panel habría que mover de
        # categoría para calzar con el universo». 0 = calza exacto.
        "disimilitud": round(
            sum(abs(c["brecha"]) for c in categorias if c["brecha"] is not None) / 2, 4
        ) if hay_objetivo else None,
    }


def _validar_cruce(conn, dimension_a, dimension_b):
    categoricas = demografia.categoricas(conn)
    for dimension in (dimension_a, dimension_b):
        if dimension not in categoricas:
            raise DatosInvalidos(
                f"Dimensión desconocida para composición: {dimension!r}.",
                {"dimensiones_validas": list(categoricas)},
            )
    if dimension_a == dimension_b:
        raise DatosInvalidos("El cruce necesita dos dimensiones distintas.")


def _cruce(conn, universo, dimension_a, dimension_b, momento=None):
    _validar_cruce(conn, dimension_a, dimension_b)
    total = _contar(conn, universo)
    desde, donde, params = universo
    filas = db.todas(
        conn,
        f"""
        select coalesce(va.valor, %s) as a,
               coalesce(vb.valor, %s) as b,
               count(*)::int as observados
          {desde}
          left join f_atributo_persona(coalesce(%s::timestamptz, now())) va
                 on va.id_persona = p.id_persona and va.atributo = %s
          left join f_atributo_persona(coalesce(%s::timestamptz, now())) vb
                 on vb.id_persona = p.id_persona and vb.atributo = %s
         where {donde}
         group by 1, 2
         order by 1, 2
        """,
        (SIN_DATO, SIN_DATO, momento, dimension_a, momento, dimension_b) + params,
    )
    return {
        "dimensiones": [dimension_a, dimension_b],
        "miembros": total,
        "brecha_disponible": False,
        "motivo_sin_brecha": (
            "El universo de referencia se carga por dimensión (marginales), no "
            "por celda cruzada: el cruce es descriptivo."
        ),
        "celdas": [
            {
                dimension_a: f["a"],
                dimension_b: f["b"],
                "observados": f["observados"],
                "proporcion_observada": round(f["observados"] / total, 4) if total else 0.0,
            }
            for f in filas
        ],
    }


def cruce(conn, panel_id, dimension_a, dimension_b, estado="activo",
          momento=None):
    """P1 — composición por el cruce de dos dimensiones.

    Es donde aparecen los huecos que las marginales esconden: un panel puede
    tener la proporción correcta de mujeres y la correcta de mayores de 65, y
    no tener ninguna mujer mayor de 65.

    No lleva brecha: un objetivo cruzado es un universo distinto, con una
    celda por combinación, y `objetivo_composicion` guarda marginales. El
    cruce es descriptivo por diseño, y lo dice.
    """
    _validar_cruce(conn, dimension_a, dimension_b)
    return {"panel_id": panel_id,
            **_cruce(conn, _universo_panel(panel_id, estado, momento),
                     dimension_a, dimension_b, momento)}


def _dimensiones_pedidas(conn, dimensiones):
    categoricas = demografia.categoricas(conn)
    pedidas = [d.strip().lower() for d in (dimensiones or categoricas)]
    desconocidas = [d for d in pedidas if d not in categoricas]
    if desconocidas:
        raise DatosInvalidos(
            f"Dimensiones desconocidas: {desconocidas}.",
            {"dimensiones_validas": list(categoricas)},
        )
    return pedidas


def composicion(conn, panel_id, dimensiones=None, estado="activo",
                cruce_de=None, momento=None):
    """R2.3 — composición del panel, con brecha si hay objetivo cargado.

    R4.1.a — con `momento` la composición es la de esa fecha: los atributos
    que estaban vigentes y la membresía que existía. Sin `momento`, idéntica
    a la de siempre.
    """
    panel = db.una(conn, "select id, nombre from panel where id = %s", (panel_id,))
    if not panel:
        raise NoEncontrado(f"No existe el panel {panel_id}.")

    pedidas = _dimensiones_pedidas(conn, dimensiones)
    objetivos = obtener_objetivo(conn, panel_id)["dimensiones"]
    universo = _universo_panel(panel_id, estado, momento)
    total = _contar(conn, universo)

    salida = {
        "panel_id": panel_id,
        # R-ORG.2 — el ámbito va junto al resultado, también para el panel.
        "ambito": {"tipo": PANEL, "id": panel_id, "nombre": panel["nombre"]},
        "objetivo_admitido": True,
        "estado_membresia": estado,
        "miembros": total,
        "objetivo_cargado": bool(objetivos),
        "dimensiones": [
            _dimension(conn, universo, dimension, objetivos, total, momento)
            for dimension in pedidas
        ],
        "avisos": [],
    }
    if momento:
        salida["momento"] = str(momento)
        salida["retroactiva"] = True
        # El objetivo de composición **no** se historiza: `objetivo_composicion`
        # guarda el universo de referencia vigente, y cargar uno nuevo pisa al
        # anterior. Así que la brecha de una composición retroactiva compara la
        # foto de entonces contra el universo de hoy. Decirlo es la diferencia
        # entre un número que se entiende y uno que engaña.
        salida["aviso_objetivo"] = (
            "La composición es la de esa fecha, pero la brecha se calcula "
            "contra el universo de referencia cargado **hoy**: los objetivos "
            "no se historizan."
        ) if objetivos else None
    if cruce_de:
        salida["cruce"] = cruce(conn, panel_id, cruce_de[0], cruce_de[1], estado,
                                momento)
    return salida


# R-ORG.3 — por qué una carga no tiene brecha. Es un motivo, no un hueco: la
# pantalla lo muestra donde iría la brecha, para que no se lea como cero.
MOTIVO_SIN_OBJETIVO_CARGA = (
    "Una carga no tiene universo de referencia: es un hecho del pasado —lo "
    "que entró, entró— y no algo que se corrija reclutando. La composición "
    "es descriptiva y la brecha no está disponible."
)

# R-ORG §7 — lo que la pantalla tiene que decir de cada ámbito nuevo.
AVISO_CARGA = (
    "La composición de una carga muestra cómo está hoy ese grupo, no un "
    "registro fiel de lo que se cargó: quien se dio de baja ya no aparece. "
    "Y cuenta solo a las personas vinculadas desde que el sistema registra "
    "de qué carga viene cada una."
)
AVISO_TODOS = (
    "Este universo de referencia es el de la bóveda entera —todas las "
    "personas, con y sin panel—, no el de un panel. Un objetivo del panel "
    "nacional y uno del total de la bóveda son comparaciones distintas."
)


def composicion_de_ambito(conn, ambito, referencia=None, dimensiones=None,
                          cruce_de=None, estado="activo", momento=None):
    """R-ORG.2 — la composición de cualquiera de los tres ámbitos.

    El panel delega en `composicion()`, que es la de siempre (y la que usan
    el muestreo y el optimizador), así que su resultado no cambia. Los dos
    nuevos usan las mismas funciones de conteo con otro universo.
    """
    ambito = (ambito or PANEL).strip().lower()
    if ambito not in AMBITOS:
        raise DatosInvalidos(
            f"Ámbito desconocido: {ambito!r}.", {"ambitos": list(AMBITOS)})
    if ambito == PANEL:
        if referencia is None:
            raise DatosInvalidos("Para el ámbito «panel» hace falta el panel.")
        return composicion(conn, referencia, dimensiones=dimensiones,
                           estado=estado, cruce_de=cruce_de, momento=momento)
    if momento:
        # La composición a fecha (R4.1.a) existe para reconstruir una ola de
        # un panel. Para «todos» o una carga no hay membresía que fechar, y
        # devolver los atributos de entonces con las personas de hoy sería
        # una mezcla que no corresponde a ningún momento.
        raise DatosInvalidos(
            "La composición a una fecha está disponible solo por panel.",
            {"ambito": ambito})

    pedidas = _dimensiones_pedidas(conn, dimensiones)
    if ambito == TODOS:
        universo = _universo_todos()
        objetivos = obtener_objetivo_de_todos(conn)["dimensiones"]
        descriptor = {"tipo": TODOS, "id": None, "nombre": "Todos los panelistas",
                      "descripcion": "La bóveda entera, con y sin panel."}
        motivo = None
        avisos = [AVISO_TODOS] if objetivos else []
        quienes = "persona(s)"
    else:
        from . import cargas

        if referencia is None:
            raise DatosInvalidos("Para el ámbito «carga» hace falta la carga.")
        carga = cargas.obtener(conn, referencia)
        universo = _universo_carga(referencia)
        objetivos = {}
        descriptor = {"tipo": CARGA, "id": carga["id"], "nombre": carga["nombre"],
                      "fecha_estudio": carga["fecha_estudio"],
                      "publico_objetivo": carga["publico_objetivo"],
                      "creado_en": carga["creado_en"]}
        motivo = MOTIVO_SIN_OBJETIVO_CARGA
        avisos = [AVISO_CARGA]
        quienes = "persona(s)"

    total = _contar(conn, universo)
    salida = {
        "ambito": descriptor,
        "objetivo_admitido": ambito in AMBITOS_CON_OBJETIVO,
        "miembros": total,
        "objetivo_cargado": bool(objetivos),
        "dimensiones": [
            _dimension(conn, universo, dimension, objetivos, total,
                       motivo_sin_objetivo=motivo, quienes=quienes)
            for dimension in pedidas
        ],
        "avisos": avisos,
    }
    if ambito == TODOS:
        # Cuántos de esos no están en ningún panel: son los que este ámbito
        # muestra por primera vez (R3.13).
        salida["sin_panel"] = db.una(
            conn,
            """select count(*)::int as n from persona p
                where not exists (select 1 from membresia m
                                   where m.id_persona = p.id_persona
                                     and m.estado = 'activo')""")["n"]
    if cruce_de:
        salida["cruce"] = {"ambito": descriptor,
                           **_cruce(conn, universo, cruce_de[0], cruce_de[1])}
    return salida
