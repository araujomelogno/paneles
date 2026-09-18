"""R4.2 — Optimizador de muestreo.

Las reglas de R3.1 priorizan brechas y excluyen sobre-convocados, y eso
alcanza cuando hay holgura. Cuando no la hay, cerrar la cuota y cuidar a la
gente tiran para lados opuestos, y las reglas no saben **negociar**: eligen
mal o no eligen.

Este módulo no reemplaza a R3.1. Con holgura los dos coinciden; la diferencia
aparece justo donde duele —segmentos escasos y muy convocados—, y por eso
conviene mantener las reglas: sirven de referencia y de respaldo. De hecho
`comparar()` corre las dos y muestra la diferencia, que es lo que permite
justificar el cambio de método ante quien pregunte.

Cuatro decisiones que conviene tener presentes:

**Duro y blando son cosas distintas.** Consentimiento, preferencia de canal,
pertenencia al panel y tamaño pedido son restricciones **duras**: no se
negocian, ni con el peso más alto del mundo. La fatiga y la equidad de
rotación son **blandas**: se penalizan. Meterlas en la misma bolsa sería o
convocar a quien no consintió, o no poder cerrar nunca una cuota.

**Es un voraz, no un solver, y es a propósito.** La spec pide que cada
individuo incluido sea *explicable*: por qué segmento entró y qué peso tuvo.
Un óptimo de programación entera da una asignación mejor en el margen y
ninguna explicación por persona. Acá cada elegido sale con el déficit que
tenía su segmento cuando entró, lo que aportó a la brecha y lo que costó en
fatiga y en equidad. Con una función objetivo convexa y una sola dimensión de
cuota, el voraz llega al mismo lugar en la enorme mayoría de los casos.

**Cuando no se puede, lo dice.** Una cuota infactible no se cierra violando
una restricción ni devolviendo menos gente en silencio: se informan las tres
salidas con su número —reducir el tamaño, aflojar la fatiga, aceptar la
brecha— y elige una persona.

**Propone; convocar sigue siendo explícito.** Igual que R3.1.
"""

from . import composicion, consentimiento, db, demografia, muestreo, preferencias
from .errores import DatosInvalidos, NoEncontrado

PESOS_POR_DEFECTO = {
    # La escala contra la que se leen los otros dos. Subirlo es decir «cerrá
    # la cuota aunque duela».
    "peso_brecha": 10.0,
    # Cuánto cuesta convocar a alguien que ya viene siendo convocado. Es lo
    # que evita que la muestra recaiga siempre sobre los mismos, que es como
    # se queman los paneles.
    "peso_fatiga": 1.0,
    # Cuánto cuesta el desbalance en el reparto. Distinto de la fatiga:
    # alguien puede estar lejos del umbral y aun así ser siempre el elegido
    # de su celda.
    "peso_equidad": 0.5,
}

# Cuánto se afloja el tope de la ventana al cuantificar esa alternativa. Es
# un paso, no una recomendación: la alternativa se muestra con su costo.
PASOS_DE_FATIGA = (1, 2, 3)


# ── Pesos ────────────────────────────────────────────────────────────

def obtener_pesos(conn, panel_id):
    fila = db.una(
        conn,
        "select peso_brecha, peso_fatiga, peso_equidad, actualizado_por, "
        "       actualizado_en from peso_optimizador where panel_id = %s",
        (panel_id,))
    if not fila:
        return dict(PESOS_POR_DEFECTO, configurados=False)
    return {
        "peso_brecha": float(fila["peso_brecha"]),
        "peso_fatiga": float(fila["peso_fatiga"]),
        "peso_equidad": float(fila["peso_equidad"]),
        "configurados": True,
        "actualizado_por": fila["actualizado_por"],
        "actualizado_en": fila["actualizado_en"].isoformat(),
    }


def guardar_pesos(conn, panel_id, cambios, actor=None):
    if not db.una(conn, "select 1 from panel where id = %s", (panel_id,)):
        raise NoEncontrado(f"No existe el panel {panel_id}.")
    actuales = obtener_pesos(conn, panel_id)
    nuevos = {}
    for clave in PESOS_POR_DEFECTO:
        if clave not in (cambios or {}):
            nuevos[clave] = actuales[clave]
            continue
        try:
            valor = float(cambios[clave])
        except (TypeError, ValueError):
            raise DatosInvalidos(f"«{clave}» tiene que ser un número.")
        if valor < 0:
            raise DatosInvalidos(
                f"«{clave}» no puede ser negativo: un peso negativo invertiría "
                f"el sentido de la restricción (premiaría convocar a los más "
                f"convocados).")
        nuevos[clave] = valor

    db.ejecutar(
        conn,
        """
        insert into peso_optimizador
               (panel_id, peso_brecha, peso_fatiga, peso_equidad,
                actualizado_por, actualizado_en)
             values (%s, %s, %s, %s, %s, now())
        on conflict (panel_id) do update
                set peso_brecha = excluded.peso_brecha,
                    peso_fatiga = excluded.peso_fatiga,
                    peso_equidad = excluded.peso_equidad,
                    actualizado_por = excluded.actualizado_por,
                    actualizado_en = now()
        """,
        (panel_id, nuevos["peso_brecha"], nuevos["peso_fatiga"],
         nuevos["peso_equidad"], getattr(actor, "email", None)),
    )
    return obtener_pesos(conn, panel_id)


# ── Costos blandos ───────────────────────────────────────────────────

def _costo_fatiga(candidato, umbrales):
    """Cuánto «cuesta» molestar a esta persona, en [0, 1).

    El tope de la ventana ya excluye a quien lo superó, así que esto gradúa
    el tramo de adentro: alguien con dos convocatorias de tres permitidas
    cuesta más que alguien con cero. Sin esta gradación, el motor trataría
    igual a los dos y volvería a caer sobre el mismo.
    """
    ventana = max(1, umbrales["max_convocatorias_ventana"])
    return min(1.0, candidato["recientes"] / ventana)


def _costo_equidad(candidato, promedio_totales):
    """Cuánto se aleja esta persona del uso típico del pool, en [0, ~2].

    Es lo que la fatiga no mide: alguien puede estar lejos del umbral de la
    ventana y aun así ser el que siempre sale elegido de su celda, porque su
    celda tiene poca gente. Se compara contra el promedio del pool elegible y
    no contra un absoluto, porque «muy convocado» quiere decir cosas
    distintas en un panel nuevo y en uno de cinco años.
    """
    return candidato["totales"] / (1.0 + promedio_totales)


# ── El motor ─────────────────────────────────────────────────────────

def optimizar(conn, encuesta_id, dimension="sexo", cantidad=100,
              estado="activo", pesos=None, canal=None,
              max_convocatorias_ventana=None, con_alternativas=True):
    """Devuelve el conjunto que mejor cierra la brecha respetando lo duro.

    `canal` agrega la preferencia de ese canal como restricción dura: si se
    va a convocar por WhatsApp, quien no lo aceptó no es candidato, y
    seleccionarlo sería armar una muestra que después no se puede contactar.
    """
    encuesta = db.una(
        conn,
        "select id, panel_id, nombre, estado from encuesta where id = %s",
        (encuesta_id,))
    if not encuesta:
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")

    categoricas = demografia.categoricas(conn)
    if dimension not in categoricas:
        raise DatosInvalidos(
            f"«{dimension}» no es una dimensión de cuota.",
            {"dimensiones_validas": list(categoricas)})
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise DatosInvalidos(f"«cantidad» tiene que ser un número: {cantidad!r}.")
    if cantidad <= 0:
        raise DatosInvalidos("«cantidad» tiene que ser mayor que cero.")

    panel_id = encuesta["panel_id"]
    umbrales = dict(muestreo.obtener_umbrales(conn, panel_id))
    if max_convocatorias_ventana is not None:
        # Para cuantificar la alternativa «aflojar el umbral»: se corre el
        # mismo motor con el tope movido y se mira qué cambia.
        umbrales["max_convocatorias_ventana"] = int(max_convocatorias_ventana)
    pesos = dict(PESOS_POR_DEFECTO, **(pesos or obtener_pesos(conn, panel_id)))

    elegibles, excluidos, conteo = _elegibles(
        conn, panel_id, encuesta_id, dimension, umbrales, canal)

    objetivos, hay_objetivo, aviso_objetivo = _objetivos(
        conn, panel_id, dimension, cantidad, estado, elegibles)

    seleccion, sin_cubrir = _elegir(elegibles, objetivos, cantidad, pesos,
                                    umbrales, hay_objetivo)

    elegidos = {p["id_persona"] for p in seleccion}
    for candidato in elegibles:
        if str(candidato["id_persona"]) in elegidos:
            continue
        conteo[muestreo.CUOTA_CUBIERTA] = conteo.get(muestreo.CUOTA_CUBIERTA, 0) + 1
        excluidos.append({
            "id_persona": str(candidato["id_persona"]),
            "categoria": candidato["categoria"],
            "motivo": muestreo.CUOTA_CUBIERTA,
            "explicacion": muestreo.EXPLICACIONES[muestreo.CUOTA_CUBIERTA],
            "convocatorias_recientes": candidato["recientes"],
            "convocatorias_totales": candidato["totales"],
        })

    # `con_alternativas=False` es lo que corta la recursión: cuantificar la
    # alternativa «aflojar la fatiga» corre este mismo motor con el tope
    # movido, y esa corrida no tiene que volver a buscarse alternativas.
    factibilidad = _factibilidad(
        conn, encuesta_id, dimension, cantidad, estado, pesos, canal,
        umbrales, elegibles, seleccion, objetivos, sin_cubrir, hay_objetivo,
        con_alternativas)

    return {
        "encuesta": {"id": encuesta["id"], "nombre": encuesta["nombre"],
                     "panel_id": panel_id, "estado": encuesta["estado"]},
        "metodo": "optimizador",
        "dimension": dimension,
        "canal": canal,
        "cantidad_pedida": cantidad,
        "umbrales": umbrales,
        "pesos": pesos,
        "brecha_disponible": hay_objetivo,
        "aviso_objetivo": aviso_objetivo,
        "objetivos": objetivos,
        "propuesta": seleccion,
        "elegibles": len(elegibles),
        "excluidos": excluidos,
        "resumen_exclusiones": [
            {"motivo": motivo, "personas": n,
             "explicacion": muestreo.EXPLICACIONES.get(motivo, motivo)}
            for motivo, n in sorted(conteo.items(), key=lambda kv: -kv[1])
        ],
        **factibilidad,
        # Igual que R3.1: esto propone. Convocar sigue siendo un acto
        # explícito de un responsable.
        "convoca": False,
    }


def _elegibles(conn, panel_id, encuesta_id, dimension, umbrales, canal):
    """Los candidatos que pasan **todas** las restricciones duras."""
    candidatos = muestreo._candidatos(
        conn, panel_id, encuesta_id, dimension, umbrales)
    ids = [c["id_persona"] for c in candidatos]
    habilitadas, _ = consentimiento.filtrar_con_consentimiento(
        conn, ids, consentimiento.CONTACTO)
    con_consentimiento = set(habilitadas)

    # R4.4 — la preferencia de canal es una restricción dura cuando se sabe
    # por dónde se va a convocar. Una muestra óptima a la que no se le puede
    # mandar nada no es una muestra.
    contactables = None
    if canal:
        contactables = set(preferencias.filtrar_contactables(
            conn, ids, canal)[0])

    ahora = db.una(conn, "select now() as ahora")["ahora"]
    elegibles, excluidos, conteo = [], [], {}
    for candidato in candidatos:
        motivo = muestreo._motivo_de_exclusion(
            candidato, umbrales, con_consentimiento, ahora)
        if not motivo and contactables is not None \
                and str(candidato["id_persona"]) not in contactables:
            motivo = f"sin_preferencia_{canal}"
        if motivo:
            conteo[motivo] = conteo.get(motivo, 0) + 1
            excluidos.append({
                "id_persona": str(candidato["id_persona"]),
                "categoria": candidato["categoria"],
                "motivo": motivo,
                "explicacion": muestreo.EXPLICACIONES.get(
                    motivo, f"no acepta ser contactada por {canal}"),
                "convocatorias_recientes": candidato["recientes"],
                "convocatorias_totales": candidato["totales"],
            })
            continue
        elegibles.append(candidato)
    return elegibles, excluidos, conteo


def _objetivos(conn, panel_id, dimension, cantidad, estado, elegibles):
    """Cuántas personas de cada categoría querría tener la muestra.

    Con universo de referencia cargado, la proporción del universo por el
    tamaño pedido. Sin él no hay brecha que cerrar y el objetivo pasa a ser
    el reparto proporcional a los elegibles, que es lo más neutro que se
    puede hacer sin inventar un universo.
    """
    detalle = composicion.composicion(
        conn, panel_id, dimensiones=[dimension], estado=estado
    )["dimensiones"][0]
    hay_objetivo = detalle["brecha_disponible"]

    if hay_objetivo:
        return (
            {c["categoria"]: int(round((c["proporcion_objetivo"] or 0) * cantidad))
             for c in detalle["categorias"]
             if c["proporcion_objetivo"] is not None},
            True,
            None,
        )

    por_categoria = {}
    for candidato in elegibles:
        por_categoria[candidato["categoria"]] = \
            por_categoria.get(candidato["categoria"], 0) + 1
    total = sum(por_categoria.values()) or 1
    return (
        {categoria: int(round(n / total * cantidad))
         for categoria, n in por_categoria.items()},
        False,
        detalle["motivo_sin_brecha"],
    )


def _elegir(elegibles, objetivos, cantidad, pesos, umbrales, hay_objetivo):
    """El voraz: en cada paso, la persona con mejor puntaje marginal.

    El puntaje es lo que la persona aporta a cerrar la brecha menos lo que
    cuesta en fatiga y en equidad. El aporte usa el error cuadrático sobre el
    conteo del segmento: la derivada de `(s - objetivo)²` al sumar uno es
    `2·déficit − 1`, que es fuertemente positiva mientras falte gente y pasa a
    negativa apenas la categoría se pasa. Eso da el comportamiento que se
    quiere sin ningún caso especial: llenar primero lo más vacío, y dejar de
    llenar lo que ya está.
    """
    promedio = (sum(c["totales"] for c in elegibles) / len(elegibles)
                if elegibles else 0.0)
    restantes = list(elegibles)
    seleccion, contados = [], {}
    escala = max(1, cantidad)

    while restantes and len(seleccion) < cantidad:
        mejor, mejor_puntaje, mejor_detalle = None, None, None
        for candidato in restantes:
            categoria = candidato["categoria"]
            deficit = objetivos.get(categoria, 0) - contados.get(categoria, 0)
            aporte = pesos["peso_brecha"] * (2 * deficit - 1) / escala
            fatiga = pesos["peso_fatiga"] * _costo_fatiga(candidato, umbrales)
            equidad = pesos["peso_equidad"] * _costo_equidad(candidato, promedio)
            puntaje = aporte - fatiga - equidad
            # El desempate replica el criterio de R3.1: menos convocado
            # primero y, a igualdad, quien más responde. Sin él, dos personas
            # idénticas se ordenarían por el orden de la consulta.
            clave = (puntaje, -candidato["recientes"], -candidato["totales"],
                     candidato["respondidas"], str(candidato["id_persona"]))
            if mejor_puntaje is None or clave > mejor_puntaje:
                mejor, mejor_puntaje = candidato, clave
                mejor_detalle = {"deficit": deficit, "aporte": aporte,
                                 "fatiga": fatiga, "equidad": equidad,
                                 "puntaje": puntaje}

        # Se corta cuando ninguna categoría necesita gente: seguir sería
        # empeorar la composición para llegar al número, que es exactamente
        # lo que la cuota existe para evitar.
        #
        # La condición mira el **déficit** y no el puntaje total a propósito.
        # Con `peso_brecha` en cero —alguien que pidió ignorar la cuota— todos
        # los puntajes son negativos, y cortar por el puntaje devolvería una
        # muestra vacía en vez de la que se pidió.
        if hay_objetivo and mejor_detalle["deficit"] <= 0:
            break

        restantes.remove(mejor)
        categoria = mejor["categoria"]
        contados[categoria] = contados.get(categoria, 0) + 1
        seleccion.append({
            "id_persona": str(mejor["id_persona"]),
            "categoria": categoria,
            "orden": len(seleccion) + 1,
            "convocatorias_recientes": mejor["recientes"],
            "convocatorias_totales": mejor["totales"],
            "respuestas_previas": mejor["respondidas"],
            # Por qué entró, en los términos en que se decidió. Es lo que
            # hace la selección defendible ante quien pregunte.
            "porque": {
                "deficit_del_segmento_al_entrar": mejor_detalle["deficit"],
                "aporte_a_la_brecha": round(mejor_detalle["aporte"], 4),
                "costo_fatiga": round(mejor_detalle["fatiga"], 4),
                "costo_equidad": round(mejor_detalle["equidad"], 4),
                "puntaje": round(mejor_detalle["puntaje"], 4),
            },
        })

    sin_cubrir = {
        categoria: objetivo - contados.get(categoria, 0)
        for categoria, objetivo in objetivos.items()
        if objetivo - contados.get(categoria, 0) > 0
    }
    return seleccion, sin_cubrir


# ── Infactibilidad: decirlo, con números ─────────────────────────────

def _factibilidad(conn, encuesta_id, dimension, cantidad, estado, pesos, canal,
                  umbrales, elegibles, seleccion, objetivos, sin_cubrir,
                  hay_objetivo, con_alternativas=True):
    """¿Se pudo? Y si no, cuáles son las salidas y cuánto cuesta cada una.

    No elige ninguna: las devuelve cuantificadas para que decida una persona.
    """
    # `sum(objetivos)` puede quedar uno o dos por debajo de `cantidad` por el
    # redondeo de las proporciones. Eso no es infactibilidad: la cuota está
    # cumplida, y reportarlo como un fracaso mandaría a aflojar umbrales por
    # un decimal.
    alcanzable = min(cantidad, sum(objetivos.values()) or cantidad) \
        if hay_objetivo else cantidad
    factible = len(seleccion) >= alcanzable and not sin_cubrir
    if factible:
        return {"factible": True, "sin_cubrir": {}, "alternativas": []}
    if not con_alternativas:
        return {"factible": False, "sin_cubrir": sin_cubrir, "alternativas": []}

    alternativas = []

    # 1 · Reducir el tamaño. Lo que sí se puede armar hoy sin tocar nada.
    alternativas.append({
        "opcion": "reducir_el_tamano",
        "descripcion": (
            f"Armar la muestra con {len(seleccion)} personas en vez de "
            f"{cantidad}, sin tocar los umbrales ni aceptar brecha."),
        "tamano": len(seleccion),
        "cuesta": f"{cantidad - len(seleccion)} persona(s) menos de muestra",
    })

    # 2 · Aflojar el umbral de fatiga. Se mide corriendo el mismo motor con el
    # tope movido: es la única forma honesta de decir «cuántos más entran».
    tope = umbrales["max_convocatorias_ventana"]
    for paso in PASOS_DE_FATIGA:
        relajado = optimizar(
            conn, encuesta_id, dimension=dimension, cantidad=cantidad,
            estado=estado, pesos=pesos, canal=canal,
            max_convocatorias_ventana=tope + paso, con_alternativas=False)
        alternativas.append({
            "opcion": "aflojar_la_fatiga",
            "descripcion": (
                f"Subir el tope de la ventana de {tope} a {tope + paso} "
                f"convocatorias."),
            "max_convocatorias_ventana": tope + paso,
            "tamano": len(relajado["propuesta"]),
            "elegibles": relajado["elegibles"],
            "sin_cubrir": relajado["sin_cubrir"],
            "cuesta": (
                f"{relajado['elegibles'] - len(elegibles)} persona(s) más "
                f"pasan a ser convocables, a costa de molestar más seguido a "
                f"quienes ya venían siendo convocados."),
            "alcanza": len(relajado["propuesta"]) >= cantidad
                       and not relajado["sin_cubrir"],
        })
        if len(relajado["propuesta"]) >= cantidad and not relajado["sin_cubrir"]:
            break

    # 3 · Aceptar la brecha: quedarse con lo que hay y saber cuánto se desvía.
    if hay_objetivo and sin_cubrir:
        total = len(seleccion) or 1
        desvio = sum(
            abs(len([p for p in seleccion if p["categoria"] == categoria]) / total
                - (objetivo / max(1, cantidad)))
            for categoria, objetivo in objetivos.items()) / 2
        alternativas.append({
            "opcion": "aceptar_la_brecha",
            "descripcion": (
                "Convocar igual y asumir que la muestra no calza con el "
                "universo de referencia."),
            "sin_cubrir": sin_cubrir,
            "disimilitud": round(desvio, 4),
            "cuesta": (
                f"La muestra queda a {round(desvio * 100, 1)} puntos de "
                f"disimilitud del universo: habría que mover esa fracción de "
                f"categoría para que calce."),
        })

    return {
        "factible": False,
        "sin_cubrir": sin_cubrir,
        "motivo": (
            f"No se puede llenar la cuota pedida sin violar una restricción: "
            f"hay {len(elegibles)} persona(s) elegibles y se pidieron "
            f"{cantidad}."
            if len(seleccion) < cantidad else
            f"Se llegó al tamaño pedido pero quedaron categorías sin cubrir: "
            f"no hay elegibles suficientes en {sorted(sin_cubrir)}."),
        # Las tres salidas, cuantificadas. El sistema **no elige**.
        "alternativas": alternativas,
        "elige_el_sistema": False,
    }


# ── Contra las reglas de R3.1 ────────────────────────────────────────

def comparar(conn, encuesta_id, dimension="sexo", cantidad=100,
             estado="activo", canal=None):
    """La misma pregunta por los dos métodos, y en qué se diferencian.

    Con holgura las dos selecciones coinciden casi del todo. La diferencia
    aparece en segmentos escasos y muy convocados, que es donde el optimizador
    vale, y poder mostrarla es lo que permite justificar el cambio de método.
    """
    por_reglas = muestreo.proponer(
        conn, encuesta_id, dimension=dimension, cantidad=cantidad, estado=estado)
    optimizada = optimizar(
        conn, encuesta_id, dimension=dimension, cantidad=cantidad,
        estado=estado, canal=canal)

    ids_reglas = {p["id_persona"] for p in por_reglas["propuesta"]}
    ids_optimo = {p["id_persona"] for p in optimizada["propuesta"]}

    def fatiga_media(propuesta):
        if not propuesta:
            return 0.0
        return round(sum(p["convocatorias_recientes"] for p in propuesta)
                     / len(propuesta), 3)

    def por_categoria(propuesta):
        salida = {}
        for persona in propuesta:
            salida[persona["categoria"]] = salida.get(persona["categoria"], 0) + 1
        return salida

    return {
        "encuesta_id": encuesta_id,
        "dimension": dimension,
        "cantidad_pedida": cantidad,
        "reglas": {
            "metodo": "reglas (R3.1)",
            "personas": len(ids_reglas),
            "por_categoria": por_categoria(por_reglas["propuesta"]),
            "fatiga_media": fatiga_media(por_reglas["propuesta"]),
        },
        "optimizador": {
            "metodo": "optimizador (R4.2)",
            "personas": len(ids_optimo),
            "por_categoria": por_categoria(optimizada["propuesta"]),
            "fatiga_media": fatiga_media(optimizada["propuesta"]),
            "factible": optimizada["factible"],
        },
        "en_las_dos": len(ids_reglas & ids_optimo),
        "solo_en_reglas": sorted(ids_reglas - ids_optimo),
        "solo_en_optimizador": sorted(ids_optimo - ids_reglas),
        "coinciden": ids_reglas == ids_optimo,
    }
