"""R3.2 — Chequeos de calidad de las respuestas.

Distingue una respuesta trabajada de una completada al azar. Importa por dos
motivos: los datos malos ensucian la consulta semántica, y a partir de R3.4
la calidad es lo que habilita el punto —premiar volumen en vez de calidad es
la forma más rápida de arruinar un panel—.

Tres chequeos, y una regla que atraviesa a los tres: **silencio no es
aprobado**. Si el export de campo no trae tiempos, el chequeo de speeder no
corre; lo que no puede pasar es que la ausencia de datos se lea como que todo
está bien. Cada informe dice explícitamente qué no se pudo evaluar y por qué.

- **speeder** — respondió más rápido que el mínimo del estudio. El umbral es
  por encuesta y no global: lo que es rápido en un cuestionario de cinco
  minutos no lo es en uno de treinta.
- **straightliner** — contestó lo mismo a toda una batería de escalas. Se
  mide como varianza de los códigos dentro de la batería; cuando los códigos
  no son numéricos, alcanza con que todas las respuestas sean idénticas, que
  es la misma idea sin aritmética.
- **duplicado** — la misma persona entró dos veces. Después de ingestar,
  la unicidad `(individuo, pregunta)` del store semántico impide verlo
  directamente, así que se detecta por lo que sí queda: dos individuos con
  exactamente el mismo juego de respuestas.

Marcar es reversible (R3.2). Un falso positivo de speeder —alguien que
responde rápido pero bien— le cuesta puntos a una persona real, así que
`revisar()` deja revertir la marca y guarda quién lo hizo. Y una corrida
automática nunca pisa una revisión humana: la respeta y lo informa.
"""

import json
import statistics

from . import db
from .errores import DatosInvalidos, NoEncontrado

# Defaults documentados (R3.2: los umbrales son configurables por estudio;
# estos son el punto de partida, no una medición). Están sin calibrar contra
# los datos de Equipos, que es lo que la spec marca pendiente en §11.
SPEEDER_SEGUNDOS = 120
STRAIGHTLINER_VARIANZA = 0.25
# Cuántos ítems tiene que tener una batería para que su varianza signifique
# algo. Con tres, un patrón legítimo (de acuerdo, de acuerdo, de acuerdo)
# es indistinguible de no leer.
MIN_ITEMS_BATERIA = 4

SPEEDER = "speeder"
STRAIGHTLINER = "straightliner"
DUPLICADO = "duplicado"
OK = "ok"
SOSPECHOSO = "sospechoso"
PENDIENTE = "pendiente"

ESTADOS = (PENDIENTE, OK, SOSPECHOSO)


def umbrales_de(encuesta):
    """Los umbrales del estudio, con los defaults donde no haya configuración."""
    return {
        "speeder_segundos": (
            encuesta.get("umbral_speeder_segundos") or SPEEDER_SEGUNDOS
        ),
        "straightliner_varianza": (
            float(encuesta["umbral_straightliner"])
            if encuesta.get("umbral_straightliner") is not None
            else STRAIGHTLINER_VARIANZA
        ),
        "min_items_bateria": MIN_ITEMS_BATERIA,
        "son_defaults": (
            encuesta.get("umbral_speeder_segundos") is None
            and encuesta.get("umbral_straightliner") is None
        ),
    }


# ── Baterías y valores ──────────────────────────────────────────────

def _codigo_de(opciones, etiqueta):
    """Recupera el código a partir de la etiqueta guardada.

    La ingesta resuelve el código a etiqueta antes de embeber, porque lo que
    se vectoriza tiene que ser texto con significado. Para medir varianza hay
    que volver al número, y el mapa de opciones de la pregunta lo permite.
    """
    if not opciones or etiqueta is None:
        return None
    for codigo, texto in opciones.items():
        if str(texto).strip() == str(etiqueta).strip():
            try:
                return float(codigo)
            except (TypeError, ValueError):
                return None
    return None


def baterias(preguntas):
    """Agrupa las escalas que comparten juego de opciones.

    Una batería es, en la práctica, el bloque de ítems que comparten la misma
    escala de respuesta. Agrupar por el juego de opciones lo captura sin
    pedirle al analista que declare nada, y no se equivoca con dos bloques
    distintos que casualmente tienen códigos parecidos.
    """
    grupos = {}
    for pregunta in preguntas:
        if pregunta.get("tipo") != "escala":
            continue
        opciones = pregunta.get("opciones") or {}
        if not opciones:
            continue
        clave = json.dumps(
            {str(k): str(v) for k, v in opciones.items()}, sort_keys=True
        )
        grupos.setdefault(clave, []).append(pregunta)
    return [g for g in grupos.values() if len(g) >= MIN_ITEMS_BATERIA]


# ── Los tres chequeos, como funciones puras ─────────────────────────

def evaluar_speeder(duracion, umbral):
    """`(veredicto, detalle)`. `veredicto` es None cuando no se pudo evaluar."""
    if duracion is None:
        return None, {
            "evaluado": False,
            "motivo": "el export de campo no trajo duración por respuesta",
        }
    return (duracion < umbral), {
        "evaluado": True, "duracion_segundos": duracion, "umbral": umbral,
    }


def evaluar_straightliner(valores_por_bateria, umbral, min_items=MIN_ITEMS_BATERIA):
    """`valores_por_bateria`: lista de listas, una por batería. Cada una trae
    los valores de esa persona: números si se pudo recuperar el código, o el
    texto de la etiqueta si no."""
    evaluadas = []
    for valores in valores_por_bateria:
        if len(valores) < min_items:
            continue
        numeros = [v for v in valores if isinstance(v, (int, float))]
        if len(numeros) == len(valores):
            varianza = statistics.pvariance(numeros) if len(numeros) > 1 else 0.0
            evaluadas.append({
                "items": len(valores), "varianza": round(varianza, 4),
                "recto": varianza < umbral,
            })
        else:
            # Sin códigos numéricos no hay varianza, pero sí hay la misma
            # idea: todas las respuestas iguales es una línea recta.
            todas_iguales = len(set(map(str, valores))) == 1
            evaluadas.append({
                "items": len(valores), "varianza": None,
                "todas_iguales": todas_iguales, "recto": todas_iguales,
            })
    if not evaluadas:
        return None, {
            "evaluado": False,
            "motivo": (
                f"el estudio no tiene ninguna batería de escalas de al menos "
                f"{min_items} ítems con el mismo juego de opciones"
            ),
        }
    return (
        any(b["recto"] for b in evaluadas),
        {"evaluado": True, "baterias": evaluadas, "umbral": umbral},
    )


# ── Corrida sobre un estudio ────────────────────────────────────────

def _encuesta(conn, encuesta_id):
    encuesta = db.una(
        conn,
        """
        select id, panel_id, nombre, ref_estudio, umbral_speeder_segundos,
               umbral_straightliner, puntos_participacion
          from encuesta where id = %s
        """,
        (encuesta_id,),
    )
    if not encuesta:
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    return encuesta


def _respuestas_del_estudio(conn_semantica, ref_estudio):
    """Respuestas del estudio, por persona. Del store semántico solo sale
    `id_persona` y contenido: ninguna PII cruza (CLAUDE.md)."""
    return db.todas(
        conn_semantica,
        """
        select i.id_persona, p.codigo, p.tipo, p.opciones, r.valor_texto,
               r.texto_embebido
          from respuesta r
          join individuo i    on i.id = r.individuo_id
          join pregunta p     on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where c.ref_estudio = %s
         order by i.id_persona, p.orden nulls last, p.codigo
        """,
        (str(ref_estudio),),
    )


def _preguntas_del_estudio(conn_semantica, ref_estudio):
    return db.todas(
        conn_semantica,
        """
        select p.codigo, p.texto, p.tipo, p.opciones, p.orden
          from pregunta p
          join cuestionario c on c.id = p.cuestionario_id
         where c.ref_estudio = %s
         order by p.orden nulls last, p.codigo
        """,
        (str(ref_estudio),),
    )


def _duplicados_por_contenido(respuestas_por_persona):
    """Dos individuos con exactamente el mismo juego de respuestas.

    Después de ingestar, la unicidad `(individuo, pregunta)` del store
    semántico hace imposible que una misma persona tenga la respuesta dos
    veces, así que un duplicado real solo deja este rastro: dos ids distintos
    con contenido idéntico. Es lo que pasa cuando alguien contesta dos veces
    y el campo le asigna dos identificadores.
    """
    por_huella = {}
    for id_persona, respuestas in respuestas_por_persona.items():
        if not respuestas:
            continue
        huella = tuple(sorted(r["texto_embebido"] for r in respuestas))
        por_huella.setdefault(huella, []).append(id_persona)
    duplicados = {}
    for ids in por_huella.values():
        if len(ids) > 1:
            for id_persona in ids:
                duplicados[id_persona] = sorted(str(o) for o in ids if o != id_persona)
    return duplicados


def correr(conn_boveda, conn_semantica, encuesta_id, actor=None,
           duplicados_de_ingesta=None):
    """Corre los tres chequeos sobre las participaciones de una encuesta.

    Devuelve el informe y deja marcadas las participaciones. Lo que no se
    pudo evaluar se dice: la lista `no_evaluado` es tan parte del resultado
    como las marcas.
    """
    encuesta = _encuesta(conn_boveda, encuesta_id)
    umbrales = umbrales_de(encuesta)

    participaciones = db.todas(
        conn_boveda,
        """
        select id, id_persona, respondio, duracion_segundos, calidad_estado,
               calidad_revisada_por
          from participacion
         where encuesta_id = %s
         order by id
        """,
        (encuesta_id,),
    )
    if not participaciones:
        return {
            "encuesta": {"id": encuesta["id"], "nombre": encuesta["nombre"]},
            "umbrales": umbrales, "evaluadas": 0, "marcadas": 0,
            "resultados": [], "no_evaluado": [], "respetadas": [],
            "resumen": {},
        }

    preguntas = _preguntas_del_estudio(conn_semantica, encuesta["ref_estudio"])
    grupos = baterias(preguntas)
    codigos_por_bateria = [{p["codigo"] for p in g} for g in grupos]
    opciones_por_codigo = {p["codigo"]: (p.get("opciones") or {}) for p in preguntas}

    filas = _respuestas_del_estudio(conn_semantica, encuesta["ref_estudio"])
    respuestas_por_persona = {}
    for fila in filas:
        respuestas_por_persona.setdefault(str(fila["id_persona"]), []).append(fila)

    duplicados = _duplicados_por_contenido(respuestas_por_persona)
    for id_persona in (duplicados_de_ingesta or {}):
        duplicados.setdefault(str(id_persona), []).append("detectado en la ingesta")

    hay_tiempos = any(p["duracion_segundos"] is not None for p in participaciones)

    resultados, respetadas = [], []
    resumen = {OK: 0, SOSPECHOSO: 0, PENDIENTE: 0}
    for participacion in participaciones:
        id_persona = str(participacion["id_persona"])

        # Una corrida automática no pisa una revisión humana. Quien revisó
        # miró el caso; el chequeo, no.
        if participacion["calidad_revisada_por"]:
            respetadas.append({
                "id_persona": id_persona,
                "estado": participacion["calidad_estado"],
                "revisada_por": participacion["calidad_revisada_por"],
            })
            resumen[participacion["calidad_estado"]] = \
                resumen.get(participacion["calidad_estado"], 0) + 1
            continue

        # Quien no respondió no tiene calidad que evaluar.
        if not participacion["respondio"]:
            resumen[PENDIENTE] += 1
            continue

        detalle, motivos = {}, []

        es_speeder, det = evaluar_speeder(
            participacion["duracion_segundos"], umbrales["speeder_segundos"]
        )
        detalle["speeder"] = det
        if es_speeder:
            motivos.append(SPEEDER)

        valores_por_bateria = []
        propias = respuestas_por_persona.get(id_persona, [])
        por_codigo = {r["codigo"]: r for r in propias}
        for codigos in codigos_por_bateria:
            valores = []
            for codigo in sorted(codigos):
                respuesta = por_codigo.get(codigo)
                if respuesta is None:
                    continue
                numero = _codigo_de(
                    opciones_por_codigo.get(codigo), respuesta["valor_texto"]
                )
                valores.append(
                    numero if numero is not None else respuesta["valor_texto"]
                )
            valores_por_bateria.append(valores)

        es_recto, det = evaluar_straightliner(
            valores_por_bateria, umbrales["straightliner_varianza"],
            umbrales["min_items_bateria"],
        )
        detalle["straightliner"] = det
        if es_recto:
            motivos.append(STRAIGHTLINER)

        otros = duplicados.get(id_persona)
        detalle["duplicado"] = {"evaluado": True, "coincide_con": otros or []}
        if otros:
            motivos.append(DUPLICADO)

        estado = SOSPECHOSO if motivos else OK
        resumen[estado] += 1
        db.ejecutar(
            conn_boveda,
            """
            update participacion
               set calidad_estado = %s, motivo_calidad = %s,
                   calidad_detalle = %s::jsonb, calidad_evaluada_en = now()
             where id = %s
            """,
            (estado, ",".join(motivos) or None, json.dumps(detalle),
             participacion["id"]),
        )
        resultados.append({
            "id_persona": id_persona, "estado": estado,
            "motivos": motivos, "detalle": detalle,
        })

    conn_boveda.commit()

    no_evaluado = []
    if not hay_tiempos:
        no_evaluado.append({
            "chequeo": SPEEDER,
            "mensaje": (
                "Ninguna participación trae duración, así que el chequeo de "
                "speeder no se aplicó. Que no haya marcas de speeder NO "
                "significa que no los haya: significa que no se pudo mirar. "
                "Para habilitarlo, el export de campo tiene que traer el "
                "tiempo de respuesta."
            ),
        })
    if not grupos:
        no_evaluado.append({
            "chequeo": STRAIGHTLINER,
            "mensaje": (
                f"El estudio no tiene ninguna batería de escalas de al menos "
                f"{MIN_ITEMS_BATERIA} ítems con el mismo juego de opciones, "
                f"así que el chequeo de straightliner no se aplicó."
            ),
        })

    return {
        "encuesta": {"id": encuesta["id"], "nombre": encuesta["nombre"]},
        "umbrales": umbrales,
        "baterias_detectadas": [sorted(c) for c in codigos_por_bateria],
        "evaluadas": len(resultados),
        "marcadas": sum(1 for r in resultados if r["estado"] == SOSPECHOSO),
        "resultados": resultados,
        "respetadas": respetadas,
        "no_evaluado": no_evaluado,
        "resumen": resumen,
    }


# ── Revisión humana ─────────────────────────────────────────────────

def revisar(conn, participacion_id, estado, actor, motivo=None):
    """Revierte o confirma una marca automática (R3.2: marcar es reversible).

    Queda registrado quién lo hizo. Un `sospechoso` cuesta puntos, así que
    quién decidió no es un detalle: es la diferencia entre una regla y una
    persona que se hace cargo.
    """
    if estado not in (OK, SOSPECHOSO):
        raise DatosInvalidos(
            f"«{estado}» no es un estado de calidad revisable.",
            {"estados_validos": [OK, SOSPECHOSO]},
        )
    fila = db.una(
        conn,
        "select id, encuesta_id, id_persona, calidad_estado, motivo_calidad "
        "from participacion where id = %s",
        (participacion_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la participación {participacion_id}.")

    db.ejecutar(
        conn,
        """
        update participacion
           set calidad_estado = %s,
               calidad_revisada_por = %s,
               calidad_revisada_en = now(),
               calidad_motivo_revision = %s
         where id = %s
        """,
        (estado, getattr(actor, "uid", None), motivo, participacion_id),
    )
    conn.commit()
    return {
        "id": fila["id"],
        "encuesta_id": fila["encuesta_id"],
        "id_persona": str(fila["id_persona"]),
        "estado_anterior": fila["calidad_estado"],
        "estado": estado,
        "motivo_automatico": fila["motivo_calidad"],
        "motivo_revision": motivo,
        "revisada_por": getattr(actor, "uid", None),
        # R3.4 — al volver a `ok` queda un punto liquidable que antes no
        # estaba. Se dice acá para que la app pueda ofrecerlo.
        "habilita_liquidacion": estado == OK,
    }


def detectar_duplicados_en_filas(filas, columna_id):
    """Ids que vienen más de una vez en el archivo de campo.

    Es el único momento en que un duplicado se ve como tal: después de
    ingestar, el store semántico ya lo colapsó a una fila por
    `(individuo, pregunta)`.
    """
    vistos, repetidos = set(), {}
    for fila in filas:
        clave = str(fila.get(columna_id) or "").strip()
        if not clave:
            continue
        if clave in vistos:
            repetidos[clave] = repetidos.get(clave, 1) + 1
        vistos.add(clave)
    return repetidos
