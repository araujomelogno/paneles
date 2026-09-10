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
"""

from . import db, demografia
from .errores import DatosInvalidos, NoEncontrado

TOLERANCIA = 0.005
"""Cuánto se le perdona a la suma de proporciones de una dimensión. Un
universo cargado a dos decimales no suma exactamente 1 casi nunca (0,52 +
0,48 sí, pero siete tramos etarios redondeados, no), así que se acepta medio
punto porcentual de desvío. Más que eso es un error de carga."""


def _validar_objetivos(objetivos):
    """Deja los objetivos en forma canónica y verifica cada dimensión.

    La validación es por dimensión, no sobre el total: cargar solo `sexo`
    tiene que poder hacerse, y entonces el total del panel es 1, no 2.
    """
    if not objetivos:
        raise DatosInvalidos("No hay objetivos para cargar.")

    por_dimension = {}
    for crudo in objetivos:
        if not isinstance(crudo, dict):
            raise DatosInvalidos(f"Objetivo mal formado: {crudo!r}.")
        dimension = (crudo.get("dimension") or "").strip().lower()
        if dimension not in demografia.DIMENSIONES_CATEGORICAS:
            raise DatosInvalidos(
                f"Dimensión desconocida para composición: {dimension!r}.",
                {"dimensiones_validas": list(demografia.DIMENSIONES_CATEGORICAS)},
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


def guardar_objetivo(conn, panel_id, objetivos):
    """Carga (o reemplaza) el universo de referencia de un panel.

    Reemplaza **por dimensión**: cargar sexo no borra el tramo etario que ya
    estaba, pero volver a cargar sexo sí reemplaza las categorías anteriores
    de sexo. Si no fuera así, quitar una categoría del universo sería
    imposible desde la app.
    """
    if not db.una(conn, "select 1 from panel where id = %s", (panel_id,)):
        raise NoEncontrado(f"No existe el panel {panel_id}.")
    por_dimension = _validar_objetivos(objetivos)

    for dimension, categorias in por_dimension.items():
        db.ejecutar(
            conn,
            "delete from objetivo_composicion where panel_id = %s and dimension = %s",
            (panel_id, dimension),
        )
        for categoria, proporcion in categorias.items():
            db.ejecutar(
                conn,
                """
                insert into objetivo_composicion
                       (panel_id, dimension, categoria, proporcion_objetivo)
                     values (%s, %s, %s, %s)
                """,
                (panel_id, dimension, categoria, proporcion),
            )
    return obtener_objetivo(conn, panel_id)


def obtener_objetivo(conn, panel_id):
    filas = db.todas(
        conn,
        """
        select dimension, categoria, proporcion_objetivo
          from objetivo_composicion
         where panel_id = %s
         order by dimension, categoria
        """,
        (panel_id,),
    )
    por_dimension = {}
    for f in filas:
        por_dimension.setdefault(f["dimension"], {})[f["categoria"]] = float(
            f["proporcion_objetivo"]
        )
    return {
        "panel_id": panel_id,
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


def borrar_objetivo(conn, panel_id, dimension=None):
    """Saca el objetivo de una dimensión (o el del panel entero).

    Después de esto la composición de esa dimensión vuelve a ser puramente
    descriptiva, con la brecha marcada no disponible.
    """
    if dimension:
        n = db.ejecutar(
            conn,
            "delete from objetivo_composicion where panel_id = %s and dimension = %s",
            (panel_id, dimension),
        )
    else:
        n = db.ejecutar(
            conn, "delete from objetivo_composicion where panel_id = %s", (panel_id,)
        )
    return {"panel_id": panel_id, "dimension": dimension, "borradas": n}


def _observada(conn, panel_id, dimension, estado="activo"):
    """Cuántos miembros hay en cada categoría de una dimensión."""
    columna = demografia.DIMENSIONES[dimension]
    filas = db.todas(
        conn,
        f"""
        select coalesce({columna}::text, '(sin dato)') as categoria,
               count(*)::int as observados
          from membresia m
          join persona p on p.id_persona = m.id_persona
          left join v_demografia d on d.id_persona = p.id_persona
         where m.panel_id = %s and (%s::text is null or m.estado = %s::text)
         group by 1
         order by 1
        """,
        (panel_id, estado, estado),
    )
    return {f["categoria"]: f["observados"] for f in filas}


def contar_miembros(conn, panel_id, estado="activo"):
    fila = db.una(
        conn,
        """
        select count(*)::int as n from membresia
         where panel_id = %s and (%s::text is null or estado = %s::text)
        """,
        (panel_id, estado, estado),
    )
    return fila["n"]


def _dimension(conn, panel_id, dimension, objetivos, total, estado):
    observada = _observada(conn, panel_id, dimension, estado)
    del_objetivo = objetivos.get(dimension) or {}
    hay_objetivo = bool(del_objetivo)

    categorias = []
    for categoria in sorted(set(observada) | set(del_objetivo)):
        observados = observada.get(categoria, 0)
        proporcion = (observados / total) if total else 0.0
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
            f"No hay universo de referencia cargado para «{dimension}»: la "
            f"composición es descriptiva y la brecha no se puede calcular."
        ),
        "categorias": categorias,
        # Índice de disimilitud: la mitad de la suma de las diferencias
        # absolutas. Se lee como «qué fracción del panel habría que mover de
        # categoría para calzar con el universo». 0 = calza exacto.
        "disimilitud": round(
            sum(abs(c["brecha"]) for c in categorias if c["brecha"] is not None) / 2, 4
        ) if hay_objetivo else None,
    }


def cruce(conn, panel_id, dimension_a, dimension_b, estado="activo"):
    """P1 — composición por el cruce de dos dimensiones.

    Es donde aparecen los huecos que las marginales esconden: un panel puede
    tener la proporción correcta de mujeres y la correcta de mayores de 65, y
    no tener ninguna mujer mayor de 65.

    No lleva brecha: un objetivo cruzado es un universo distinto, con una
    celda por combinación, y `objetivo_composicion` guarda marginales. El
    cruce es descriptivo por diseño, y lo dice.
    """
    for dimension in (dimension_a, dimension_b):
        if dimension not in demografia.DIMENSIONES_CATEGORICAS:
            raise DatosInvalidos(
                f"Dimensión desconocida para composición: {dimension!r}.",
                {"dimensiones_validas": list(demografia.DIMENSIONES_CATEGORICAS)},
            )
    if dimension_a == dimension_b:
        raise DatosInvalidos("El cruce necesita dos dimensiones distintas.")

    columna_a = demografia.DIMENSIONES[dimension_a]
    columna_b = demografia.DIMENSIONES[dimension_b]
    total = contar_miembros(conn, panel_id, estado)
    filas = db.todas(
        conn,
        f"""
        select coalesce({columna_a}::text, '(sin dato)') as a,
               coalesce({columna_b}::text, '(sin dato)') as b,
               count(*)::int as observados
          from membresia m
          join persona p on p.id_persona = m.id_persona
          left join v_demografia d on d.id_persona = p.id_persona
         where m.panel_id = %s and (%s::text is null or m.estado = %s::text)
         group by 1, 2
         order by 1, 2
        """,
        (panel_id, estado, estado),
    )
    return {
        "panel_id": panel_id,
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


def composicion(conn, panel_id, dimensiones=None, estado="activo",
                cruce_de=None):
    """R2.3 — composición del panel, con brecha si hay objetivo cargado."""
    if not db.una(conn, "select 1 from panel where id = %s", (panel_id,)):
        raise NoEncontrado(f"No existe el panel {panel_id}.")

    pedidas = [
        d.strip().lower() for d in (dimensiones or demografia.DIMENSIONES_CATEGORICAS)
    ]
    desconocidas = [d for d in pedidas if d not in demografia.DIMENSIONES_CATEGORICAS]
    if desconocidas:
        raise DatosInvalidos(
            f"Dimensiones desconocidas: {desconocidas}.",
            {"dimensiones_validas": list(demografia.DIMENSIONES_CATEGORICAS)},
        )

    objetivos = obtener_objetivo(conn, panel_id)["dimensiones"]
    total = contar_miembros(conn, panel_id, estado)

    salida = {
        "panel_id": panel_id,
        "estado_membresia": estado,
        "miembros": total,
        "objetivo_cargado": bool(objetivos),
        "dimensiones": [
            _dimension(conn, panel_id, dimension, objetivos, total, estado)
            for dimension in pedidas
        ],
    }
    if cruce_de:
        salida["cruce"] = cruce(conn, panel_id, cruce_de[0], cruce_de[1], estado)
    return salida
