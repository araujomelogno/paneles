"""R2.7 a R2.11 — el motor de consultas: recall, reranking, verificación.

Una consulta es una lista de criterios y un modo. Cada criterio es de uno de
dos tipos, y esa es la distinción que organiza todo el módulo:

* **demográfico** — sexo, tramo etario, localidad. Vive en la bóveda, es
  exacto y **filtra**: quien no lo cumple no está.
* **semántico** — una frase en lenguaje natural sobre lo que la gente
  respondió. Vive en el store semántico, es difuso y **ordena**: nadie
  «cumple» un criterio semántico de forma binaria, se parece más o menos.

De ahí salen los tres casos que el spec pide, y los tres pasan por acá:

    solo demográficos   → R2.4, se resuelve entero en la bóveda y no se
                          abre siquiera la conexión al store semántico.
    solo semánticos     → R2.7 + R2.8 + R2.9 sobre todo el corpus habilitado.
    los dos             → R2.5, consulta mixta, con las dos estrategias de
                          puente y la elegida registrada en la respuesta.

El oleoducto semántico, en orden:

    1. embedding del criterio      mismo proveedor y modelo que la ingesta
    2. recall (ANN)                top_n respuestas más cercanas
    3. gate de consentimiento      R2.11, antes de que exista el pool
    4. reranking                   cross-encoder, reordena el pool
    5. colapso a individuo         una entrada por persona, la de mejor score
    6. top-k                       lo que se manda a verificar
    7. verificación con Claude     cumple / no cumple / dudoso, con evidencia
    8. combinación de criterios    R2.10, puntaje por criterio y combinado

Dos decisiones de orden que vale la pena dejar dichas, porque el spec no las
fija:

* El reranking corre **antes** del colapso a individuo. Colapsar primero
  obligaría a elegir la evidencia de cada persona por distancia, y la
  distancia es justo lo que el reranking está para corregir: alguien que
  respondió «me encanta» en una ola y «lo dejé» en otra quedaría
  representado por la respuesta equivocada.
* Un veredicto `no_cumple` saca a la persona del ranking final en los dos
  modos, no solo en el estricto. Mostrar como coincidencia a quien la
  evidencia contradice no es un resultado laxo, es un resultado falso. Lo
  que el modo laxo tolera es la **ausencia** de evidencia y la duda, no la
  contradicción.
* La contradicción se busca en **todas** las respuestas que la persona puso
  en el pool, no solo en su mejor evidencia. Es lo que el caso de polaridad
  opuesta obliga: alguien que contestó «fernet» a qué toma y «no me gusta el
  fernet, lo detesto» a por qué, tiene una respuesta que lo pone arriba y
  otra que lo desmiente. Si se verificara solo la primera, entraría al
  ranking con la evidencia que le conviene y la contradicción quedaría
  tapada. Se verifican hasta `MAX_EVIDENCIAS_POR_INDIVIDUO` por persona, y
  un `no_cumple` en cualquiera de ellas manda. El verificador las parte en
  lotes (ver `verificacion.py`); que dos evidencias de la misma persona
  caigan en lotes distintos no cambia nada, porque la salida vuelve en el
  orden de la entrada.
* **`sin_verificar` no es `dudoso`.** Una evidencia que el verificador no
  llegó a juzgar —un lote truncado, la API caída, el tiempo agotado— no
  excluye a nadie ni cuenta como cumplimiento, en ningún modo. Pero la
  persona tampoco queda «validada»: el resultado la marca con
  `verificacion_incompleta` y confianza baja, y la consulta lo dice en
  `degradaciones` y en `verificacion`. Los veredictos que sí se emitieron
  se aplican igual: una falla parcial no desactiva ninguna regla.

Nada de lo que sale de acá es PII: los resultados se identifican por
`id_persona`. Traducir eso a un nombre es una operación aparte, con permiso
aparte, y queda registrada (ver `auditoria.py`).
"""

import csv
import io
import json
import time
import uuid

from . import (
    consentimiento,
    costo_consulta,
    demografia,
    db,
    embeddings as mod_embeddings,
    semantica,
    verificacion as mod_verificacion,
)
from .errores import DatosInvalidos, NoEncontrado

# ── Parámetros, todos configurables por consulta ─────────────────────

TOP_N_POR_DEFECTO = 200
"""Tamaño del pool de recall, en RESPUESTAS (no en personas). Después del
colapso hay como mucho `top_n` individuos, y en general bastantes menos:
una persona suele aportar varias respuestas cercanas al mismo criterio."""

TOP_K_POR_DEFECTO = 25
"""Cuántos individuos se mandan a verificar. Es el parámetro caro: cada uno
es texto que va a la API de Claude."""

TOP_N_MAXIMO = 2000
TOP_K_MAXIMO = 200
LIMITE_POR_DEFECTO = 50

FACTOR_SOBREPEDIDO = 4
"""Con «semántico primero» el gate se aplica DESPUÉS del recall, así que hay
que pedir de más para no quedarse corto cuando muchos candidatos no tienen
consentimiento. Se piden `top_n * factor` y se recorta a `top_n` una vez
filtrado."""

UMBRAL_DISTANCIA = 0.55
"""P1 — umbral de confianza. Distancia coseno: 0 es idéntico, 1 es
ortogonal. Por encima de esto, la mejor evidencia de la persona ya no se
parece mucho al criterio y el resultado se marca de confianza baja. No la
excluye: la marca."""

MAX_EVIDENCIAS_POR_INDIVIDUO = 3
"""Cuántas respuestas de la misma persona se verifican. Es el precio de
detectar la contradicción: con una sola evidencia por persona, quien se
contradice entra al ranking con la que le conviene. Tres alcanza para el
caso real (una respuesta cerrada más una o dos abiertas sobre el mismo tema)
y mantiene acotado el texto que va a la API."""

UMBRAL_SEGMENTO = 5000
"""Hasta cuántas personas conviene mandarle al store semántico como filtro.
Por encima, pasar la lista sale más caro que filtrar después."""

ESTRICTO, LAXO = "estricto", "laxo"
MODOS = (ESTRICTO, LAXO)

# R-CS · cambio 2 — el alcance es otro eje que el modo. El modo dice cómo se
# interpreta lo que no se pudo afirmar (estricto deja afuera, laxo incluye
# penalizado); el alcance dice **cuánto se verifica**: las mejores `top_k`
# personas (exploratorio, en la request) o todas las unidades elegibles
# (completo, en lotes diferidos y con presupuesto; ver `consulta_completa`).
EXPLORATORIO, COMPLETO = "exploratorio", "completo"
ALCANCES = (EXPLORATORIO, COMPLETO)

RONDAS_MAXIMAS = 3
"""R-CS · cambio 3 — cuántas veces la exploratoria va a buscar más personas
cuando las que verificó resultaron irrelevantes. La primera ronda es la de
siempre (las `top_k` mejores); las siguientes reemplazan a las que no
aportaron evidencia pertinente, sin pasar del presupuesto de unidades."""

SIN_EVIDENCIA_PERTINENTE = "sin_evidencia_pertinente"
"""Laxo: ninguno de los criterios semánticos tiene evidencia que hable del
tema. Antes no podía pasar —sin hallazgo no se entraba al universo—; con
`irrelevante` sí, y mostrarla penalizada sería el falso resultado que el
estado nuevo existe para evitar."""

# R-CS · el estado de una persona frente a un criterio, y en el resultado.
# Es lo que COLOQUIO y la pantalla leen sin tener que reinterpretar la
# combinación de veredictos (R4.2 de la solicitud).
CONFIRMADA, POSIBLE, DESCARTADA, PENDIENTE = (
    "confirmada", "posible", "descartada", "pendiente")
ESTADO_POR_VEREDICTO = {
    mod_verificacion.CUMPLE: CONFIRMADA,
    mod_verificacion.DUDOSO: POSIBLE,
    mod_verificacion.NO_CUMPLE: DESCARTADA,
    mod_verificacion.SIN_VERIFICAR: PENDIENTE,
    mod_verificacion.IRRELEVANTE: mod_verificacion.IRRELEVANTE,
}

VERSION_CONTRATO = 2
"""R-CS · A4 — la forma de la respuesta. 2 trae `estado`, `irrelevante`,
`alcance`, `recorte` y `costo`. Un cliente que no encuentre un campo de la
versión 2 tiene que leerlo como «no se sabe», nunca como un valor favorable:
una persona sin `estado` no está «confirmada»."""

DEMOGRAFICO_PRIMERO = "demografico_primero"
SEMANTICO_PRIMERO = "semantico_primero"
ESTRATEGIAS = (DEMOGRAFICO_PRIMERO, SEMANTICO_PRIMERO)

SIN_EVIDENCIA = "sin_evidencia"


class _Reloj:
    """Cronómetro de etapas, para el diagnóstico (P1)."""

    def __init__(self):
        self.arranque = time.perf_counter()
        self.etapas = []

    def marca(self, etapa, desde, **datos):
        self.etapas.append({
            "etapa": etapa,
            "ms": round((time.perf_counter() - desde) * 1000, 1),
            **datos,
        })

    @property
    def ms_total(self):
        return round((time.perf_counter() - self.arranque) * 1000, 1)


# ════════════════════════════════════════════════════════════════════
#  Normalización de la definición de consulta
# ════════════════════════════════════════════════════════════════════

def _entero(valor, por_defecto, maximo):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return por_defecto
    return max(1, min(n, maximo))


def normalizar_criterio(crudo, orden, dimensiones=None, catalogo_por_clave=None):
    """Un criterio, en cualquiera de sus formas de entrada, a forma canónica.

    Se acepta un string suelto como atajo de un criterio semántico: es la
    forma en que lo escribe la interfaz cuando el usuario tipea una frase.
    """
    if isinstance(crudo, str):
        crudo = {"tipo": "semantico", "texto": crudo}
    if not isinstance(crudo, dict):
        raise DatosInvalidos(f"Criterio mal formado: {crudo!r}.")

    tipo = (crudo.get("tipo") or "").strip().lower()
    if not tipo:
        tipo = "demografico" if crudo.get("dimension") else "semantico"

    if tipo == "demografico":
        criterio = demografia.normalizar_criterio(
            crudo, dimensiones=dimensiones, catalogo_por_clave=catalogo_por_clave)
        criterio["orden"] = orden
        criterio["peso"] = float(crudo.get("peso") or 1)
        return criterio

    if tipo != "semantico":
        raise DatosInvalidos(
            f"Tipo de criterio desconocido: {tipo!r}.",
            {"tipos_validos": ["semantico", "demografico"]},
        )

    texto = (crudo.get("texto") or "").strip()
    if not texto:
        raise DatosInvalidos("Un criterio semántico necesita texto.")
    try:
        peso = float(crudo.get("peso") if crudo.get("peso") is not None else 1)
    except (TypeError, ValueError):
        raise DatosInvalidos(f"Peso inválido en el criterio «{texto}».")
    if peso <= 0:
        raise DatosInvalidos(f"El peso del criterio «{texto}» tiene que ser > 0.")

    return {
        "tipo": "semantico",
        "texto": texto,
        "peso": peso,
        # Un criterio semántico duro filtra como si fuera demográfico: quien
        # no tiene evidencia que lo cumpla queda afuera, sea cual sea el modo.
        "duro": bool(crudo.get("duro")),
        "etiqueta": crudo.get("etiqueta") or texto,
        "orden": orden,
    }


def normalizar_definicion(cruda, conn=None):
    """Valida y completa el cuerpo de `POST /consultas`.

    R3.14 — con `conn` a mano, las dimensiones demográficas admitidas salen
    del catálogo de atributos; sin él se validan contra el núcleo. Quien
    ejecuta o guarda una consulta siempre tiene conexión, así que en la
    práctica se valida contra el catálogo.
    """
    cruda = cruda or {}
    crudos = cruda.get("criterios")
    if crudos is None and cruda.get("texto"):
        crudos = [cruda["texto"]]
    por_clave = demografia.catalogo(conn) if conn is not None else None
    dimensiones = (
        set(por_clave) | set(demografia.DIMENSIONES_BASE) if por_clave else None)
    criterios = [
        normalizar_criterio(c, i, dimensiones=dimensiones,
                            catalogo_por_clave=por_clave)
        for i, c in enumerate(crudos or [])
    ]
    if not criterios:
        raise DatosInvalidos("La consulta necesita al menos un criterio.")

    modo = (cruda.get("modo") or ESTRICTO).strip().lower()
    if modo not in MODOS:
        raise DatosInvalidos(
            f"Modo desconocido: {modo!r}.", {"modos_validos": list(MODOS)}
        )

    estrategia = cruda.get("estrategia_puente")
    if estrategia is not None:
        estrategia = str(estrategia).strip().lower()
        if estrategia not in ESTRATEGIAS:
            raise DatosInvalidos(
                f"Estrategia de puente desconocida: {estrategia!r}.",
                {"estrategias_validas": list(ESTRATEGIAS)},
            )

    panel_id = cruda.get("panel_id")
    try:
        panel_id = int(panel_id) if panel_id not in (None, "") else None
    except (TypeError, ValueError):
        raise DatosInvalidos(f"panel_id inválido: {cruda.get('panel_id')!r}.")

    crudo_umbral = cruda.get("umbral_distancia")
    try:
        # `or` no sirve acá: `umbral_distancia: 0` es un valor legítimo (exigir
        # coincidencia exacta) y es falsy.
        umbral = UMBRAL_DISTANCIA if crudo_umbral is None else float(crudo_umbral)
    except (TypeError, ValueError):
        umbral = UMBRAL_DISTANCIA

    alcance = (cruda.get("alcance") or EXPLORATORIO).strip().lower()
    if alcance not in ALCANCES:
        raise DatosInvalidos(
            f"Alcance desconocido: {alcance!r}.", {"alcances_validos": list(ALCANCES)})

    # R-CS · A5 — `input_type` del criterio, por consulta. Solo se guarda si
    # se pidió: sin él manda el entorno, y una consulta guardada sigue al
    # entorno en vez de congelar el valor de hoy.
    tipo_criterio = cruda.get("tipo_embedding_criterio")
    if tipo_criterio not in (None, ""):
        tipo_criterio = str(tipo_criterio).strip().lower()
        if tipo_criterio not in mod_embeddings.TIPOS_DE_CRITERIO:
            raise DatosInvalidos(
                f"`tipo_embedding_criterio` desconocido: {tipo_criterio!r}.",
                {"validos": list(mod_embeddings.TIPOS_DE_CRITERIO)})
    else:
        tipo_criterio = None

    presupuesto = cruda.get("presupuesto_usd")
    if presupuesto not in (None, ""):
        try:
            presupuesto = round(float(presupuesto), 4)
        except (TypeError, ValueError):
            raise DatosInvalidos(f"`presupuesto_usd` inválido: {presupuesto!r}.")
        if presupuesto <= 0:
            raise DatosInvalidos("El presupuesto tiene que ser mayor que cero.")
    else:
        presupuesto = None

    distancia_maxima = cruda.get("distancia_maxima")
    if distancia_maxima not in (None, ""):
        try:
            distancia_maxima = float(distancia_maxima)
        except (TypeError, ValueError):
            raise DatosInvalidos(f"`distancia_maxima` inválida: {distancia_maxima!r}.")
    else:
        distancia_maxima = None

    return {
        "criterios": criterios,
        "modo": modo,
        "alcance": alcance,
        "panel_id": panel_id,
        "top_n": _entero(cruda.get("top_n"), TOP_N_POR_DEFECTO, TOP_N_MAXIMO),
        "top_k": _entero(cruda.get("top_k"), TOP_K_POR_DEFECTO, TOP_K_MAXIMO),
        # A6 — «Personas a mostrar». En la exploratoria recorta el ranking;
        # en la completa es el tamaño de página, que no tiene nada que ver
        # con cuánto se verifica.
        "limite": _entero(cruda.get("limite"), LIMITE_POR_DEFECTO, TOP_K_MAXIMO),
        "estrategia_puente": estrategia,
        "umbral_distancia": umbral,
        "tipo_embedding_criterio": tipo_criterio,
        "presupuesto_usd": presupuesto,
        "distancia_maxima": distancia_maxima,
    }


# ════════════════════════════════════════════════════════════════════
#  R2.5 — elección de la estrategia de puente
# ════════════════════════════════════════════════════════════════════

def elegir_estrategia(definicion, hay_demograficos, personas_en_segmento):
    """Cuál de los dos lados filtra primero, y por qué.

    El criterio es la selectividad. Si el segmento demográfico es chico,
    conviene resolverlo en la bóveda y darle al store semántico una lista
    corta de `id_persona`: la búsqueda de vecinos se hace sobre menos filas.
    Si el segmento es medio padrón, pasar esa lista cuesta más que filtrar
    después, y conviene dejar que el ANN recorte primero.

    Devuelve `(estrategia, motivo)`; el motivo queda en la respuesta porque
    el spec pide que se pueda saber cuál se usó.
    """
    forzada = definicion.get("estrategia_puente")
    if forzada:
        return forzada, "Estrategia forzada en la consulta."

    if not hay_demograficos:
        return (
            SEMANTICO_PRIMERO,
            "No hay criterios demográficos: no hay segmento local con el que "
            "recortar antes del recall.",
        )
    if personas_en_segmento <= UMBRAL_SEGMENTO:
        return (
            DEMOGRAFICO_PRIMERO,
            f"El segmento demográfico tiene {personas_en_segmento} personas "
            f"(≤ {UMBRAL_SEGMENTO}): es más selectivo que el corpus, así que "
            f"se filtra en la bóveda y se le pasan los id_persona al store "
            f"semántico.",
        )
    return (
        SEMANTICO_PRIMERO,
        f"El segmento demográfico tiene {personas_en_segmento} personas "
        f"(> {UMBRAL_SEGMENTO}): pasar esa lista sale más caro que recuperar "
        f"primero y filtrar después.",
    )


# ════════════════════════════════════════════════════════════════════
#  Etapas
# ════════════════════════════════════════════════════════════════════

def _similitud(distancia):
    """Distancia coseno → puntaje en [0, 1]. Es una lectura, no una métrica
    nueva: sirve para poder combinar criterios en la misma escala."""
    return max(0.0, min(1.0, 1.0 - float(distancia)))


def _evidencia(candidato):
    """La parte del candidato que se muestra y se exporta: la respuesta con
    su procedencia. Sin embedding y sin nada de la bóveda."""
    return {
        "respuesta_id": candidato["respuesta_id"],
        "valor_texto": candidato["valor_texto"],
        "texto_embebido": candidato["texto_embebido"],
        "estudio": candidato["estudio"],
        "ref_estudio": candidato["ref_estudio"],
        "fecha_campo": candidato["fecha_campo"],
        "pregunta_codigo": candidato["pregunta_codigo"],
        "pregunta_texto": candidato["pregunta_texto"],
    }


def _agrupar_por_individuo(candidatos, relevancias):
    """Pool de respuestas → un grupo por individuo, ordenado por su mejor score.

    `relevancias` es `{indice: puntaje}` del reranking, o vacío si no se
    aplicó; sin reranking el orden es el del recall, que ya viene por
    distancia. Menor clave = mejor, para las dos escalas (la relevancia
    entra negada), y como el reranking se aplica al pool completo o a
    ninguno, las claves siempre son comparables entre sí.

    De cada persona se guardan sus mejores `MAX_EVIDENCIAS_POR_INDIVIDUO`
    respuestas, no solo la primera: la contradicción puede estar en la
    segunda.
    """
    grupos = {}
    for indice, candidato in enumerate(candidatos):
        relevancia = relevancias.get(indice)
        grupos.setdefault(candidato["id_persona"], []).append({
            **candidato,
            "relevancia": relevancia,
            "clave": -relevancia if relevancia is not None else candidato["distancia"],
        })

    salida = []
    for id_persona, evidencias in grupos.items():
        evidencias.sort(key=lambda c: (c["clave"], c["respuesta_id"]))
        salida.append({
            "id_persona": id_persona,
            "evidencias": evidencias[:MAX_EVIDENCIAS_POR_INDIVIDUO],
            "clave": evidencias[0]["clave"],
        })
    salida.sort(key=lambda g: (g["clave"], g["id_persona"]))
    return salida


def _elegir_evidencia(evidencias, juicios):
    """Con qué evidencia queda representada la persona, y con qué veredicto.

    El orden de preferencia no es el del score: primero se busca una
    contradicción. Si la hay, esa es la evidencia que importa —es la que
    explica por qué la persona no entra— aunque su score sea peor que el de
    otra respuesta suya. Después un `cumple`, después un `dudoso`, después
    una `sin_verificar`: un juicio emitido siempre le gana a la falta de
    juicio, y la falta de juicio le gana a `irrelevante` —una evidencia que
    nadie leyó todavía puede ser pertinente; una irrelevante, no—. Recién si
    todas son irrelevantes, la persona queda representada por una de ellas.
    """
    pares = list(zip(evidencias, juicios))
    for veredicto in (mod_verificacion.NO_CUMPLE, mod_verificacion.CUMPLE,
                      mod_verificacion.DUDOSO, mod_verificacion.SIN_VERIFICAR):
        for candidato, juicio in pares:
            if juicio["veredicto"] == veredicto:
                return candidato, juicio
    return pares[0]


def clave_unidad(candidato):
    """R-CS · cambio 3 — la unidad de evidencia de una respuesta: un texto
    distinto dentro de una pregunta. La huella viene del store; si no viene
    (un doble de pruebas), se calcula con el mismo algoritmo."""
    huella = candidato.get("hash_texto") or semantica.hash_texto(
        candidato.get("texto_embebido") or "")
    return (candidato.get("pregunta_id"), huella)


def _agrupar_en_unidades(pool):
    """El pool (respuestas, por distancia) → unidades, en el orden de su mejor
    respuesta. `miembros` son los índices del pool que comparten el texto."""
    unidades, por_clave = [], {}
    for indice, candidato in enumerate(pool):
        clave = clave_unidad(candidato)
        unidad = por_clave.get(clave)
        if unidad is None:
            unidad = {"clave": clave, "representante": candidato, "miembros": []}
            por_clave[clave] = unidad
            unidades.append(unidad)
        unidad["miembros"].append(indice)
    return unidades


def _rerankear_unidades(criterio, etiqueta, unidades, reranker, degradaciones):
    """`{indice_de_unidad: relevancia}`, o vacío si no se aplicó.

    Se rerankea **una vez por unidad**, no por respuesta: 25 copias del mismo
    texto son un solo documento para el cross-encoder.

    Un proveedor que contesta sin puntajes, o con puntajes para una parte, es
    una degradación y se dice (A1.3). Antes se descartaban los `None` en
    silencio: la consulta seguía con el orden de la distancia y nadie se
    enteraba de que el reranker no había corrido. Y una cobertura parcial no
    se usa: mezclar relevancias con distancias da un orden sin escala común.
    """
    if not reranker.disponible or not unidades:
        return {}
    textos = [u["representante"]["texto_embebido"] or u["representante"]["valor_texto"] or ""
              for u in unidades]
    try:
        crudas = reranker.reordenar(criterio, textos)
    except Exception as error:  # noqa: BLE001 — degradar, no romper
        degradaciones.append({
            "etapa": "reranking",
            "proveedor": reranker.nombre,
            "criterio": etiqueta,
            "motivo": f"El proveedor falló: {error}",
            "consecuencia": "Se sigue con el orden del recall (distancia).",
        })
        return {}
    relevancias = {
        indice: max(0.0, min(1.0, float(puntaje)))
        for indice, puntaje in crudas if puntaje is not None
    }
    if len(relevancias) != len(unidades):
        degradaciones.append({
            "etapa": "reranking",
            "proveedor": reranker.nombre,
            "criterio": etiqueta,
            "motivo": (f"El proveedor devolvió puntaje para {len(relevancias)} "
                       f"de {len(unidades)} unidades."),
            "consecuencia": ("Se sigue con el orden del recall (distancia): "
                             "mezclar puntajes de dos escalas daría un orden "
                             "sin sentido."),
        })
        return {}
    return relevancias


def _sumar_informes(informes, evidencias=None):
    """Los informes de varias rondas de verificación, como uno solo."""
    total = mod_verificacion.nuevo_informe(
        evidencias if evidencias is not None else sum(i["evidencias"] for i in informes))
    for informe in informes:
        for clave in ("verificadas", "sin_verificar", "lotes_iniciales", "llamadas",
                      "subdivisiones", "reintentos", "duracion_ms"):
            total[clave] += informe.get(clave) or 0
        for clave, valor in (informe.get("veredictos") or {}).items():
            total["veredictos"][clave] = total["veredictos"].get(clave, 0) + valor
        for clave, valor in (informe.get("por_fallo") or {}).items():
            total["por_fallo"][clave] = total["por_fallo"].get(clave, 0) + valor
        for clave, valor in (informe.get("stop_reasons") or {}).items():
            total["stop_reasons"][clave] = total["stop_reasons"].get(clave, 0) + valor
        for clave, valor in (informe.get("anomalias") or {}).items():
            total["anomalias"][clave] = total["anomalias"].get(clave, 0) + valor
        total["tokens"]["entrada"] += informe["tokens"]["entrada"]
        total["tokens"]["salida"] += informe["tokens"]["salida"]
        total["tam_lote"] = total["tam_lote"] or informe.get("tam_lote")
        total["concurrencia"] = total["concurrencia"] or informe.get("concurrencia")
        total["presupuesto_s"] = total["presupuesto_s"] or informe.get("presupuesto_s")
        total["presupuesto_agotado"] = (
            total["presupuesto_agotado"] or informe.get("presupuesto_agotado", False))
        total["lotes"].extend(informe.get("lotes") or [])
    return total


def _verificar_unidades(ctx_verificacion, criterio, etiqueta, representantes,
                        verificador, degradaciones):
    """Verifica una tanda de unidades. Devuelve `(juicios, informe)` en el
    orden de `representantes`, y nunca levanta: un proveedor que explota deja
    la tanda «sin verificar» y lo dice."""
    if not representantes:
        return [], mod_verificacion.nuevo_informe(0)
    # El presupuesto de tiempo es **de la consulta**, no de cada criterio ni
    # de cada ronda: arranca con la primera verificación y lo comparten las
    # siguientes. Si fuera por criterio, tres criterios triplicarían la
    # espera de COLOQUIO.
    if ctx_verificacion.get("limite") is None and getattr(verificador, "presupuesto_s", None):
        ctx_verificacion["limite"] = time.monotonic() + verificador.presupuesto_s
    try:
        return verificador.verificar_con_informe(
            criterio, representantes, limite=ctx_verificacion.get("limite"),
            captura=ctx_verificacion.get("captura"), etiqueta=etiqueta)
    except Exception as error:  # noqa: BLE001 — degradar, no romper
        if not any(d.get("etapa") == "verificacion" and d.get("criterio") == etiqueta
                   and not d.get("parcial") for d in degradaciones):
            degradaciones.append({
                "etapa": "verificacion",
                "proveedor": verificador.nombre,
                "criterio": etiqueta,
                "motivo": f"El proveedor falló: {error}",
                "consecuencia": (
                    "Ningún candidato se excluye por veredicto: no se puede excluir "
                    "por un juicio que no se emitió."
                ),
            })
        return mod_verificacion.NoDisponible(str(error)).verificar_con_informe(
            criterio, representantes)


def _resolver_criterio_semantico(ctx, criterio, definicion, ids_permitidos,
                                 filtro_posterior, reloj, degradaciones,
                                 reranker, verificador, verificacion=None):
    """Corre el oleoducto completo para UN criterio semántico.

    Devuelve `{id_persona: hallazgo}`, donde el hallazgo trae el puntaje, la
    evidencia con su procedencia y el veredicto.

    R-CS · cambio 3 — lo que se rerankea y se verifica son **unidades de
    evidencia** (un texto distinto dentro de una pregunta), no respuestas.
    Y la selección rellena: si las personas verificadas resultan tener solo
    evidencia irrelevante, se va a buscar a las siguientes del ranking —hasta
    `RONDAS_MAXIMAS` y sin pasar de `top_k × MAX_EVIDENCIAS_POR_INDIVIDUO`
    unidades, que es el mismo tope de evidencias que había antes—. Con eso,
    25 personas que dieron la misma respuesta sobre la titularidad del
    contrato cuestan **una** unidad verificada y liberan sus 25 lugares.
    """
    etiqueta = criterio["etiqueta"]
    verificacion = verificacion if verificacion is not None else {}

    # 1. Embedding del criterio. Mismo proveedor, modelo y dimensión que la
    #    ingesta; con `input_type` de consulta (R-CS · cambio 1), salvo que
    #    el entorno o la consulta pidan otro.
    desde = time.perf_counter()
    tipo = mod_embeddings.tipo_de_criterio(pedido=definicion.get("tipo_embedding_criterio"))
    vector = ctx.embeddings.embeber_criterio([criterio["texto"]], tipo=tipo)[0]
    verificacion.setdefault("tokens_embedding", 0)
    verificacion["tokens_embedding"] += costo_consulta.tokens_de(criterio["texto"])
    reloj.marca("embedding", desde, criterio=etiqueta, dims=len(vector), input_type=tipo)

    # 2. Recall.
    desde = time.perf_counter()
    pedido = definicion["top_n"] * (FACTOR_SOBREPEDIDO if filtro_posterior else 1)
    crudos = semantica.recuperar(ctx.semantica, vector, pedido, ids_permitidos)
    reloj.marca(
        "recall", desde, criterio=etiqueta, pedidos=pedido, crudos=len(crudos),
        filtrado_en_la_base=ids_permitidos is not None,
    )

    # 3. Gate de consentimiento (R2.11) y segmento, cuando el recall corrió
    #    sin filtro. Pasa ANTES de que exista el pool: quien no tiene
    #    `uso_semantico` vigente no llega a ser candidato.
    if filtro_posterior:
        desde = time.perf_counter()
        del_recall = list(dict.fromkeys(c["id_persona"] for c in crudos))
        habilitados = set(filtro_posterior(del_recall))
        descartados = len(del_recall) - len(habilitados)
        crudos = [c for c in crudos if c["id_persona"] in habilitados]
        reloj.marca(
            "gate_consentimiento", desde, criterio=etiqueta,
            personas_evaluadas=len(del_recall), personas_descartadas=descartados,
        )

    pool = crudos[: definicion["top_n"]]
    if not pool:
        verificacion.setdefault("informes", []).append(
            {"criterio": etiqueta, **mod_verificacion.nuevo_informe(0)})
        return {}

    # 4. Unidades de evidencia (R-CS · cambio 3) y reranking por unidad,
    #    antes del colapso a individuo (ver el docstring del módulo).
    desde = time.perf_counter()
    unidades = _agrupar_en_unidades(pool)
    reloj.marca("unidades", desde, criterio=etiqueta, respuestas=len(pool),
                unidades=len(unidades), repetidas=len(pool) - len(unidades))

    desde = time.perf_counter()
    por_unidad = _rerankear_unidades(criterio["texto"], etiqueta, unidades,
                                     reranker, degradaciones)
    relevancias = {
        miembro: por_unidad[i]
        for i, unidad in enumerate(unidades) if i in por_unidad
        for miembro in unidad["miembros"]
    }
    reloj.marca(
        "reranking", desde, criterio=etiqueta, aplicado=bool(relevancias),
        proveedor=reranker.nombre, entrada=len(unidades),
    )

    # 5. Agrupación por individuo.
    desde = time.perf_counter()
    grupos = _agrupar_por_individuo(pool, relevancias)
    reloj.marca("colapso", desde, criterio=etiqueta, individuos=len(grupos))

    # 6 y 7. Top-k y verificación, por rondas. Todas las evidencias de cada
    #    persona, no solo la mejor: la contradicción puede estar en
    #    cualquiera. Cada unidad se verifica una sola vez.
    desde = time.perf_counter()
    representantes = {u["clave"]: u["representante"] for u in unidades}
    miembros = {u["clave"]: len(u["miembros"]) for u in unidades}
    top_k = definicion["top_k"]
    tope_unidades = top_k * MAX_EVIDENCIAS_POR_INDIVIDUO
    veredictos, informes, seleccionados = {}, [], []
    cursor, pertinentes, rondas, tope_alcanzado = 0, 0, 0, False
    while pertinentes < top_k and cursor < len(grupos):
        tanda, nuevas = [], []
        while cursor < len(grupos) and len(tanda) < top_k - pertinentes:
            grupo = grupos[cursor]
            propias = [clave_unidad(e) for e in grupo["evidencias"]]
            faltan = [c for c in dict.fromkeys(propias)
                      if c not in veredictos and c not in nuevas]
            if len(veredictos) + len(nuevas) + len(faltan) > tope_unidades:
                tope_alcanzado = True
                break
            nuevas.extend(faltan)
            tanda.append(grupo)
            cursor += 1
        if not tanda:
            break
        # Una tanda cuyas unidades ya se juzgaron no cuesta nada y no cuenta
        # como ronda: es justo el caso de las 25 respuestas repetidas, que
        # se resuelven sin volver a llamar al verificador.
        if nuevas and rondas >= RONDAS_MAXIMAS:
            break
        if nuevas:
            rondas += 1
            juicios, informe = _verificar_unidades(
                verificacion, criterio["texto"], etiqueta,
                [representantes[c] for c in nuevas], verificador, degradaciones)
            informes.append(informe)
            veredictos.update(zip(nuevas, juicios))
        for grupo in tanda:
            seleccionados.append(grupo)
            if any(veredictos[clave_unidad(e)]["veredicto"] != mod_verificacion.IRRELEVANTE
                   for e in grupo["evidencias"]):
                pertinentes += 1
        if tope_alcanzado:
            break

    informe = _sumar_informes(informes, evidencias=len(veredictos))
    if verificador.disponible and informe["sin_verificar"]:
        # R-VER.6 — falla parcial: los lotes buenos se conservan y la
        # consulta lo dice, en vez de presentar un ranking que parece
        # normal y está a medio verificar.
        detalle = ", ".join(
            f"{fallo}: {n}" for fallo, n in sorted(informe["por_fallo"].items()))
        degradaciones.append({
            "etapa": "verificacion",
            "proveedor": verificador.nombre,
            "parcial": True,
            "criterio": etiqueta,
            "motivo": (
                f"{informe['sin_verificar']} de {informe['evidencias']} "
                f"unidad(es) de evidencia quedaron sin verificar ({detalle})."
            ),
            "consecuencia": (
                "Esas evidencias no excluyen ni cuentan como cumplimiento; "
                "las personas afectadas quedan marcadas «verificación "
                "incompleta». Los veredictos emitidos se aplican igual."
            ),
        })
    respuestas_cubiertas = sum(miembros[c] for c in veredictos)
    verificacion.setdefault("informes", []).append({
        "criterio": etiqueta, **informe,
        "unidades": len(veredictos),
        "respuestas_cubiertas": respuestas_cubiertas,
        "rondas": rondas,
        "irrelevantes": informe["veredictos"].get(mod_verificacion.IRRELEVANTE, 0),
    })
    verificacion.setdefault("personas_verificadas", set()).update(
        g["id_persona"] for g in seleccionados)
    verificacion.setdefault("personas_pertinentes", set()).update(
        g["id_persona"] for g in seleccionados
        if any(veredictos[clave_unidad(e)]["veredicto"] != mod_verificacion.IRRELEVANTE
               for e in g["evidencias"]))
    reloj.marca(
        "verificacion", desde, criterio=etiqueta,
        aplicada=verificador.disponible, proveedor=verificador.nombre,
        evidencias_verificadas=informe["verificadas"],
        respuestas_cubiertas=respuestas_cubiertas,
        sin_verificar=informe["sin_verificar"],
        individuos=len(seleccionados), pertinentes=pertinentes, rondas=rondas,
        tope_de_unidades=tope_alcanzado,
        lotes=informe["lotes_iniciales"], llamadas=informe["llamadas"],
        subdivisiones=informe["subdivisiones"],
        cumple=informe["veredictos"].get(mod_verificacion.CUMPLE, 0),
        no_cumple=informe["veredictos"].get(mod_verificacion.NO_CUMPLE, 0),
        dudoso=informe["veredictos"].get(mod_verificacion.DUDOSO, 0),
        irrelevante=informe["veredictos"].get(mod_verificacion.IRRELEVANTE, 0),
    )

    return {
        grupo["id_persona"]: hallazgo_de(
            criterio, grupo["evidencias"],
            [veredictos[clave_unidad(e)] for e in grupo["evidencias"]],
            repeticiones=miembros)
        for grupo in seleccionados
    }


def hallazgo_de(criterio, evidencias, juicios, repeticiones=None):
    """Lo que una persona aporta a un criterio, a partir de sus evidencias y
    los juicios de cada una. Lo usan la exploratoria y la completa: la regla
    de agregación (R4.2) es una sola."""
    candidato, juicio = _elegir_evidencia(evidencias, juicios)
    relevancia = candidato.get("relevancia")
    distancia = candidato.get("distancia")
    pendientes = sum(
        1 for j in juicios if j["veredicto"] == mod_verificacion.SIN_VERIFICAR)
    vistos = {j["veredicto"] for j in juicios}
    if relevancia is not None:
        puntaje = relevancia
    elif distancia is not None:
        puntaje = _similitud(distancia)
    else:
        puntaje = 0.0
    repeticiones = repeticiones or {}
    return {
        "criterio": criterio["etiqueta"],
        "orden": criterio["orden"],
        "puntaje": round(puntaje, 4),
        "distancia": None if distancia is None else round(distancia, 4),
        "relevancia": None if relevancia is None else round(relevancia, 4),
        "veredicto": juicio["veredicto"],
        "estado": ESTADO_POR_VEREDICTO.get(juicio["veredicto"]),
        "razon": juicio["razon"],
        "fallo": juicio.get("fallo"),
        "aviso_polaridad": juicio.get("aviso_polaridad", False),
        # Cuántas de sus evidencias quedaron sin juzgar. Con una sola,
        # la persona no está «validada» aunque otra diga `cumple`: la
        # contradicción podía estar justo en la que faltó.
        "pendientes_de_verificar": pendientes,
        # R-CS · A5 — un `cumple` y un `no_cumple` de la misma persona. Manda
        # el `no_cumple` (ver el docstring del módulo), y se marca para que
        # no quede escondido detrás de un conteo que no lo delata.
        "contradiccion": (mod_verificacion.CUMPLE in vistos
                          and mod_verificacion.NO_CUMPLE in vistos),
        "evidencia": _evidencia(candidato),
        # Todas las respuestas suyas que se miraron, con su veredicto:
        # es lo que deja auditar por qué quedó adentro o afuera.
        "evidencias_evaluadas": [
            {**_evidencia(otro), "veredicto": otro_juicio["veredicto"],
             "razon": otro_juicio["razon"], "fallo": otro_juicio.get("fallo"),
             # Cuántas respuestas del pool comparten este mismo texto.
             "repeticiones": repeticiones.get(clave_unidad(otro), 1)}
            for otro, otro_juicio in zip(evidencias, juicios)
        ],
    }


# ════════════════════════════════════════════════════════════════════
#  R2.10 — combinación de criterios
# ════════════════════════════════════════════════════════════════════

def _combinar(definicion, por_criterio, umbral):
    """Un puntaje por criterio y uno combinado, con la regla del modo.

    Reglas, en el orden en que se aplican a cada persona:

      no_cumple en cualquier criterio  → afuera, en los dos modos.
      criterio duro sin cumplir        → afuera, en los dos modos.
      dudoso o sin evidencia, estricto → afuera.
      dudoso o sin evidencia, laxo     → adentro, marcada como penalizada.
      sin_verificar                    → adentro en los dos modos, con su
                                         puntaje de recall/reranking, y
                                         marcada «verificación incompleta».
      irrelevante (R-CS · cambio 4)    → como sin evidencia: no habla del
                                         criterio, así que ni afirma ni
                                         contradice. Afuera en estricto o
                                         duro; penalizada en laxo.
      laxo sin ninguna evidencia       → afuera (`sin_evidencia_pertinente`).
      pertinente en ningún criterio      Antes no podía pasar; con
                                         `irrelevante` sí, y mostrarla sería
                                         el falso resultado.

    «Irrelevante» corrige la interpretación, **no** el recall: los lugares
    que ocuparon las respuestas irrelevantes ya se gastaron. Lo que los
    recupera es la selección por unidades (cambio 3), no esta función.

    `sin_verificar` tiene su propia rama a propósito (R-VER.7). Antes, una
    verificación caída dejaba todo en `dudoso` y había que apagar la regla
    del modo estricto para no vaciar el ranking —«excluir a todo el mundo
    por un juicio que nunca se emitió no es ser estricto»—. Ahora el fallo
    técnico no se disfraza de juicio, así que la regla de `dudoso` se aplica
    siempre que haya un `dudoso`, y la ausencia de juicio ni excluye ni
    aprueba. La ausencia de **evidencia** sí sigue excluyendo en estricto,
    porque eso lo dice el recall y no el verificador.
    """
    semanticos = [c for c in definicion["criterios"] if c["tipo"] == "semantico"]
    demograficos = [c for c in definicion["criterios"] if c["tipo"] == "demografico"]
    estricto = definicion["modo"] == ESTRICTO

    universo = {p for hallazgos in por_criterio.values() for p in hallazgos}
    items, excluidos = [], []

    for id_persona in universo:
        detalle, excluir = [], None
        # Los criterios demográficos ya filtraron: quien está en el pool los
        # cumple. Se listan igual para que el resultado explique por qué.
        for criterio in demograficos:
            detalle.append({
                "criterio": criterio["etiqueta"],
                "tipo": "demografico",
                "puntaje": 1.0,
                "peso": criterio["peso"],
                "veredicto": mod_verificacion.CUMPLE,
                "razon": "Filtro demográfico aplicado en la bóveda.",
                "evidencia": None,
            })

        for criterio in semanticos:
            hallazgo = por_criterio.get(criterio["orden"], {}).get(id_persona)
            if hallazgo is None:
                detalle.append({
                    "criterio": criterio["etiqueta"],
                    "tipo": "semantico",
                    "puntaje": 0.0,
                    "peso": criterio["peso"],
                    "veredicto": SIN_EVIDENCIA,
                    "razon": "No hay ninguna respuesta suya cerca de este criterio.",
                    "evidencia": None,
                })
                if criterio["duro"] or estricto:
                    excluir = excluir or {
                        "criterio": criterio["etiqueta"],
                        "motivo": SIN_EVIDENCIA,
                    }
                continue

            if hallazgo["veredicto"] == mod_verificacion.IRRELEVANTE:
                # Se miró y no habla del criterio: para la combinación es
                # ausencia de evidencia, con su propio rótulo para que se
                # vea por qué.
                detalle.append({
                    "criterio": criterio["etiqueta"],
                    "tipo": "semantico",
                    "puntaje": 0.0,
                    "peso": criterio["peso"],
                    "veredicto": mod_verificacion.IRRELEVANTE,
                    "estado": mod_verificacion.IRRELEVANTE,
                    "razon": hallazgo["razon"],
                    "distancia": hallazgo["distancia"],
                    "relevancia": hallazgo["relevancia"],
                    "evidencia": None,
                    "evidencias_evaluadas": hallazgo.get("evidencias_evaluadas", []),
                })
                if criterio["duro"] or estricto:
                    excluir = excluir or {
                        "criterio": criterio["etiqueta"],
                        "motivo": mod_verificacion.IRRELEVANTE,
                        "evidencia": hallazgo["evidencia"]["valor_texto"],
                    }
                continue

            veredicto = hallazgo["veredicto"]
            puntaje = hallazgo["puntaje"]
            if veredicto == mod_verificacion.NO_CUMPLE:
                # La evidencia dice lo contrario: no entra al ranking final
                # en ningún modo (ver el docstring del módulo).
                puntaje = 0.0
                excluir = excluir or {
                    "criterio": criterio["etiqueta"],
                    "motivo": mod_verificacion.NO_CUMPLE,
                    "evidencia": hallazgo["evidencia"]["valor_texto"],
                    "contradiccion": hallazgo.get("contradiccion", False),
                }
            elif veredicto == mod_verificacion.DUDOSO:
                if criterio["duro"] or estricto:
                    excluir = excluir or {
                        "criterio": criterio["etiqueta"],
                        "motivo": mod_verificacion.DUDOSO,
                    }
            elif veredicto == mod_verificacion.SIN_VERIFICAR:
                # Ni excluye ni aprueba: nadie juzgó la evidencia. El puntaje
                # queda el del recall/reranking y la persona se marca abajo.
                pass
            detalle.append({
                "criterio": criterio["etiqueta"],
                "tipo": "semantico",
                "puntaje": round(puntaje, 4),
                "peso": criterio["peso"],
                "veredicto": veredicto,
                "estado": hallazgo.get("estado") or ESTADO_POR_VEREDICTO.get(veredicto),
                "razon": hallazgo["razon"],
                "contradiccion": hallazgo.get("contradiccion", False),
                "distancia": hallazgo["distancia"],
                "relevancia": hallazgo["relevancia"],
                "aviso_polaridad": hallazgo["aviso_polaridad"],
                "fallo": hallazgo.get("fallo"),
                "pendientes_de_verificar": hallazgo.get("pendientes_de_verificar", 0),
                "evidencia": hallazgo["evidencia"],
                # Todas las que se miraron, con su veredicto: la contradicción
                # y las repeticiones se ven acá.
                "evidencias_evaluadas": hallazgo.get("evidencias_evaluadas", []),
            })

        if not excluir and semanticos and not any(
                d["tipo"] == "semantico" and d["veredicto"] not in (
                    SIN_EVIDENCIA, mod_verificacion.IRRELEVANTE)
                for d in detalle):
            excluir = {"criterio": semanticos[0]["etiqueta"],
                       "motivo": SIN_EVIDENCIA_PERTINENTE}

        if excluir:
            excluidos.append({"id_persona": id_persona, "estado": DESCARTADA, **excluir})
            continue

        pesos = sum(d["peso"] for d in detalle) or 1.0
        combinado = sum(d["puntaje"] * d["peso"] for d in detalle) / pesos
        distancias = [d["distancia"] for d in detalle if d.get("distancia") is not None]
        mejor_distancia = min(distancias) if distancias else None
        penalizado = any(
            d["tipo"] == "semantico"
            and d["veredicto"] in (SIN_EVIDENCIA, mod_verificacion.DUDOSO,
                                   mod_verificacion.IRRELEVANTE)
            for d in detalle
        )
        incompleta = any(
            d["tipo"] == "semantico" and d.get("pendientes_de_verificar")
            for d in detalle
        )
        confianza_baja = penalizado or incompleta or (
            mejor_distancia is not None and mejor_distancia > umbral
        )

        criterios_ordenados = sorted(detalle, key=lambda d: (d["tipo"], d["criterio"]))
        items.append({
            "id_persona": id_persona,
            # R-CS — el estado de la persona en el resultado: `pendiente` si
            # algo quedó sin verificar, `confirmada` si todos los semánticos
            # se confirmaron, `posible` si entró por la tolerancia del laxo.
            "estado": (PENDIENTE if incompleta
                       else POSIBLE if penalizado else CONFIRMADA),
            "puntaje": round(combinado, 4),
            "confianza": "baja" if confianza_baja else "alta",
            "mejor_distancia": mejor_distancia,
            "penalizado": penalizado,
            # R-VER.7 — alguna de sus evidencias quedó sin verificar. No es un
            # resultado completo, y el ranking lo dice persona por persona.
            "verificacion_incompleta": incompleta,
            "criterios": criterios_ordenados,
            # R-CS · A4 — COLOQUIO (`motor._evidencias()`) lee `detalle` y
            # paneles siempre lo llamó `criterios`: por eso los veredictos
            # nunca se veían del otro lado. Mismo contenido, los dos
            # nombres, hasta que COLOQUIO lea `criterios`.
            "detalle": criterios_ordenados,
            "evidencias": [d["evidencia"] for d in detalle if d.get("evidencia")],
        })

    items.sort(key=lambda i: (-i["puntaje"], i["id_persona"]))
    excluidos.sort(key=lambda e: e["id_persona"])
    return items, excluidos


# ════════════════════════════════════════════════════════════════════
#  Punto de entrada
# ════════════════════════════════════════════════════════════════════

# SC2 — lo que `demografia.consultar` trae de la bóveda y no sale en el
# resultado de una consulta. Se pide aparte, con «Ver quiénes son».
CAMPOS_PII_DEL_SEGMENTO = frozenset({"nombre", "email", "documento", "celular"})


def ejecutar(ctx, definicion_cruda, reranker=None, verificador=None, actor=None):
    """Corre una consulta y devuelve el ranking, la evidencia y el diagnóstico.

    `ctx` es el `Contexto` de la request. La conexión al store semántico es
    perezosa: si la consulta es puramente demográfica, este método no la
    toca, y esa es exactamente la garantía de R2.4.
    """
    definicion = normalizar_definicion(definicion_cruda, ctx.boveda)
    if definicion["alcance"] == COMPLETO:
        # La completa no corre en la request: se estima, se confirma con
        # presupuesto y se encola (ver `consulta_completa`). Llegar acá con
        # alcance completo es un error de quien llama, no una degradación.
        raise DatosInvalidos(
            "Una consulta de alcance completo no se ejecuta en el momento: "
            "se estima su costo y se lanza con presupuesto "
            "(POST /consultas con `alcance: completo`).")
    ejecucion_id = str(uuid.uuid4())
    reloj = _Reloj()
    degradaciones = []

    demograficos = [c for c in definicion["criterios"] if c["tipo"] == "demografico"]
    semanticos = [c for c in definicion["criterios"] if c["tipo"] == "semantico"]

    # ── R2.4: puramente demográfica, entera en la bóveda ──
    if not semanticos:
        desde = time.perf_counter()
        resultado = demografia.consultar(
            ctx.boveda, demograficos,
            panel_id=definicion["panel_id"],
            finalidad=definicion_cruda.get("finalidad") if definicion_cruda else None,
            limite=definicion["limite"],
        )
        reloj.marca("consulta_demografica", desde, personas=resultado["total"])
        # SC2 — el resultado demográfico es seudónimo, igual que el semántico.
        # Hasta acá traía nombre y correo, y la pantalla los mostraba sin que
        # quedara registrada ninguna reidentificación: el camino más
        # transitado era justo el que no dejaba rastro. Ahora ver quiénes son
        # es la misma acción —y el mismo registro— en los dos tipos.
        resultado["items"] = [
            {k: v for k, v in item.items() if k not in CAMPOS_PII_DEL_SEGMENTO}
            for item in resultado["items"]
        ]
        resultado["seudonimo"] = True
        resultado["modo"] = definicion["modo"]
        resultado["puente"] = {
            "estrategia": None,
            "motivo": (
                "La consulta no tiene criterios semánticos: se resuelve entera "
                "en la bóveda y no se abre conexión al store semántico."
            ),
        }
        resultado["degradaciones"] = []
        resultado["alcance"] = EXPLORATORIO
        resultado["version_contrato"] = VERSION_CONTRATO
        resultado["costo"] = {"usd": 0.0, "tokens": {}}
        resultado["diagnostico"] = {
            "ms_total": reloj.ms_total,
            "etapas": reloj.etapas,
            "abrio_semantica": False,
            "ejecucion_id": ejecucion_id,
        }
        registrar_ejecucion(ctx.boveda, ejecucion_id, definicion, resultado,
                            actor=actor)
        return resultado

    # ── R2.5: hay parte semántica. Se elige la estrategia de puente ──
    reranker = reranker or ctx.reranker
    verificador = verificador or ctx.verificador
    if not reranker.disponible:
        degradaciones.append({
            "etapa": "reranking",
            "proveedor": reranker.nombre,
            "motivo": getattr(reranker, "motivo", "No hay proveedor de reranking."),
            "consecuencia": "Se ordena por distancia del recall, sin cross-encoder.",
        })
    if not verificador.disponible:
        degradaciones.append({
            "etapa": "verificacion",
            "proveedor": verificador.nombre,
            "motivo": getattr(verificador, "motivo", "No hay verificador."),
            "consecuencia": (
                "Todos los candidatos quedan «sin verificar» y ninguno se excluye "
                "por veredicto: no se puede excluir por un juicio que no se emitió."
            ),
        })

    # R-VER.10 — el identificador de esta ejecución. Va al diagnóstico, y con
    # el modo de depuración encendido agrupa las capturas del intercambio
    # con Claude. Sin el modo, `captura` es None y no se guarda nada.
    # R-CS — es también la clave de su fila en `consulta_ejecucion`.
    verificacion = {"limite": None, "captura": mod_verificacion.nueva_captura(ejecucion_id)}
    tokens_rerank_al_empezar = getattr(reranker, "tokens", 0) or 0

    desde = time.perf_counter()
    personas_en_segmento = demografia.contar_segmento(
        ctx.boveda, demograficos,
        panel_id=definicion["panel_id"],
        finalidad=consentimiento.SEMANTICO,
    )
    reloj.marca(
        "segmento_boveda", desde, personas=personas_en_segmento,
        finalidad=consentimiento.SEMANTICO,
    )

    estrategia, motivo = elegir_estrategia(
        definicion, bool(demograficos), personas_en_segmento
    )

    ids_permitidos, filtro_posterior = None, None
    if estrategia == DEMOGRAFICO_PRIMERO:
        desde = time.perf_counter()
        ids_permitidos = demografia.ids_del_segmento(
            ctx.boveda, demograficos,
            panel_id=definicion["panel_id"],
            finalidad=consentimiento.SEMANTICO,
        )
        reloj.marca("ids_del_segmento", desde, personas=len(ids_permitidos))
    else:
        # El gate se aplica después del recall, pero antes de armar el pool.
        def filtro_posterior(ids):  # noqa: F811
            return demografia.ids_del_segmento(
                ctx.boveda, demograficos,
                panel_id=definicion["panel_id"],
                finalidad=consentimiento.SEMANTICO,
                ids_persona=ids,
            )

    por_criterio = {}
    try:
        for criterio in semanticos:
            por_criterio[criterio["orden"]] = _resolver_criterio_semantico(
                ctx, criterio, definicion, ids_permitidos, filtro_posterior,
                reloj, degradaciones, reranker, verificador, verificacion,
            )
    finally:
        # Lo capturado se escribe aunque la consulta falle después: la
        # respuesta rara es justo la que hay que poder mirar.
        _persistir_captura(ctx, verificacion.get("captura"))

    desde = time.perf_counter()
    items, excluidos = _combinar(
        definicion, por_criterio, definicion["umbral_distancia"],
    )
    reloj.marca(
        "combinacion", desde, modo=definicion["modo"],
        candidatos=len(items) + len(excluidos), excluidos=len(excluidos),
    )

    informes = verificacion.get("informes", [])
    tokens = {
        "verificacion_entrada": sum(i["tokens"]["entrada"] for i in informes),
        "verificacion_salida": sum(i["tokens"]["salida"] for i in informes),
        "rerank": (getattr(reranker, "tokens", 0) or 0) - tokens_rerank_al_empezar,
        "embedding": verificacion.get("tokens_embedding", 0),
    }
    tarifas = costo_consulta.Tarifas.desde_entorno()
    personas_verificadas = len(verificacion.get("personas_verificadas", ()))
    mostradas = items[: definicion["limite"]]
    resultado = {
        "tipo": "mixta" if demograficos else "semantica",
        "version_contrato": VERSION_CONTRATO,
        "alcance": EXPLORATORIO,
        "modo": definicion["modo"],
        "panel_id": definicion["panel_id"],
        "criterios": definicion["criterios"],
        "parametros": {
            "top_n": definicion["top_n"],
            "top_k": definicion["top_k"],
            "limite": definicion["limite"],
            "umbral_distancia": definicion["umbral_distancia"],
            "tipo_embedding_criterio": mod_embeddings.tipo_de_criterio(
                pedido=definicion.get("tipo_embedding_criterio")),
        },
        "puente": {
            "estrategia": estrategia,
            "motivo": motivo,
            "personas_en_segmento": personas_en_segmento,
            "gate": consentimiento.SEMANTICO,
        },
        "total": len(items),
        "items": mostradas,
        "excluidos": excluidos,
        # A6 — que el recorte se vea. Subir `top_k` sin subir `limite`
        # verificaba (y pagaba) personas que nadie veía, y desde la pantalla
        # parecía que el parámetro no hacía nada.
        "recorte": {
            "personas_verificadas": personas_verificadas,
            # De esas, cuántas tenían algo que decir sobre el criterio.
            "personas_pertinentes": len(verificacion.get("personas_pertinentes", ())),
            "en_ranking": len(items),
            "mostradas": len(mostradas),
            "recortado": len(mostradas) < len(items),
            "limite": definicion["limite"],
            "top_k": definicion["top_k"],
        },
        "degradaciones": degradaciones,
        "verificacion": _resumen_verificacion(informes, items, excluidos),
        "costo": {
            "usd": costo_consulta.costo(
                tarifas, tokens["verificacion_entrada"], tokens["verificacion_salida"],
                tokens["rerank"], tokens["embedding"]),
            "tokens": tokens,
            "tarifas": tarifas.como_dict(),
        },
        "diagnostico": {
            "ms_total": reloj.ms_total,
            "etapas": reloj.etapas,
            "abrio_semantica": True,
            "reranker": reranker.nombre,
            "verificador": verificador.nombre,
            "ejecucion_id": ejecucion_id,
            "tipo_embedding_criterio": mod_embeddings.tipo_de_criterio(
                pedido=definicion.get("tipo_embedding_criterio")),
            # Si el modo de depuración estaba encendido en ESTA ejecución.
            # La pantalla lo usa para decir «estaba apagado» en vez de
            # mostrar un intercambio vacío.
            "captura_depuracion": verificacion.get("captura") is not None,
            "verificacion": [
                {k: v for k, v in informe.items() if k != "lotes"} | {
                    "lotes_detalle": informe.get("lotes", [])}
                for informe in informes
            ],
        },
    }
    registrar_ejecucion(ctx.boveda, ejecucion_id, definicion, resultado,
                        actor=actor)
    return resultado


def registrar_ejecucion(conn, ejecucion_id, definicion, resultado, actor=None):
    """R-CS · A1.3 — deja la fila de una ejecución exploratoria.

    Guarda la definición, el diagnóstico (degradaciones, proveedores
    efectivos, `input_type`, etapas) y el costo. **No** guarda el resultado:
    ni personas ni evidencias. Si la bóveda/0025 no está aplicada, la
    consulta sigue igual —registrar no puede tumbar lo que registra— y queda
    una línea en el log.
    """
    diagnostico = {
        "degradaciones": resultado.get("degradaciones", []),
        "reranker": resultado.get("diagnostico", {}).get("reranker"),
        "verificador": resultado.get("diagnostico", {}).get("verificador"),
        "tipo_embedding_criterio": resultado.get("diagnostico", {}).get(
            "tipo_embedding_criterio"),
        "etapas": resultado.get("diagnostico", {}).get("etapas", []),
        "verificacion": resultado.get("verificacion"),
        "recorte": resultado.get("recorte"),
        "costo": resultado.get("costo"),
        "puente": {k: v for k, v in (resultado.get("puente") or {}).items()
                   if k != "motivo"},
        "total": resultado.get("total"),
        "excluidos": len(resultado.get("excluidos") or []),
    }
    try:
        db.ejecutar(
            conn,
            """insert into consulta_ejecucion
                      (id, alcance, modo, tipo, definicion, estado, costo_real_usd,
                       diagnostico, creado_por, terminado_en)
               values (%s, %s, %s, %s, %s::jsonb, 'terminada', %s, %s::jsonb, %s, now())""",
            (ejecucion_id, EXPLORATORIO, definicion["modo"], resultado.get("tipo"),
             json.dumps(definicion, ensure_ascii=False, default=str),
             (resultado.get("costo") or {}).get("usd"),
             json.dumps(diagnostico, ensure_ascii=False, default=str), actor))
        conn.commit()
    except Exception as error:  # noqa: BLE001
        conn.rollback()
        print(f"[consultas] no se pudo registrar la ejecución: {type(error).__name__}")


def _persistir_captura(ctx, captura):
    """Escribe la captura de depuración en el store semántico. Si falla —la
    migración semantica/0008 sin aplicar, por ejemplo— la consulta sigue: la
    depuración no puede tumbar lo que está depurando."""
    if captura is None or not captura.filas:
        return
    try:
        captura.persistir(ctx.semantica)
    except Exception as error:  # noqa: BLE001
        ctx.semantica.rollback()
        print(f"[verificacion] no se pudo guardar la captura de depuración: "
              f"{type(error).__name__}")


def _resumen_verificacion(informes, items, excluidos):
    """R-VER.6/7 — ¿la verificación de esta consulta está completa?

    Va en la respuesta, no solo en el diagnóstico: un ranking que parece
    normal y está parcialmente sin verificar es peor que uno que avisa.
    """
    evidencias = sum(i["evidencias"] for i in informes)
    sin_verificar = sum(i["sin_verificar"] for i in informes)
    por_fallo = {}
    for informe in informes:
        for fallo, n in informe["por_fallo"].items():
            por_fallo[fallo] = por_fallo.get(fallo, 0) + n
    return {
        "completa": sin_verificar == 0,
        "evidencias": evidencias,
        "verificadas": evidencias - sin_verificar,
        "sin_verificar": sin_verificar,
        "por_fallo": por_fallo,
        "personas_con_pendientes": sum(
            1 for i in items if i.get("verificacion_incompleta")),
        "presupuesto_agotado": any(i["presupuesto_agotado"] for i in informes),
        # R-CS — lo que se verificó son unidades; cuántas respuestas cubrían
        # y cuántas resultaron no hablar del criterio.
        "respuestas_cubiertas": sum(i.get("respuestas_cubiertas", 0) for i in informes),
        "irrelevantes": sum(i.get("irrelevantes", 0) for i in informes),
    }


# ════════════════════════════════════════════════════════════════════
#  P1 — exportación a CSV
# ════════════════════════════════════════════════════════════════════

def a_csv(resultado):
    """El ranking como CSV: `id_persona`, `puntaje`, `evidencia`.

    Tres columnas y nada más, como pide el spec. La procedencia va dentro de
    la celda de evidencia —«[estudio · pregunta] texto»— porque una evidencia
    sin procedencia no se puede verificar, y agregar columnas rompería el
    contrato.

    No lleva nombre ni email: el archivo identifica por `id_persona`. Poner
    PII acá sería sacar la bóveda por la puerta de atrás; traducir esos ids a
    personas es una operación aparte y queda registrada.
    """
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\n")
    escritor.writerow(["id_persona", "puntaje", "evidencia"])
    for item in resultado.get("items", []):
        partes = []
        for evidencia in item.get("evidencias") or []:
            if not evidencia:
                continue
            procedencia = " · ".join(
                p for p in (evidencia.get("estudio"), evidencia.get("pregunta_codigo")) if p
            )
            texto = evidencia.get("valor_texto") or evidencia.get("texto_embebido") or ""
            partes.append(f"[{procedencia}] {texto}" if procedencia else texto)
        # SC2 — un resultado demográfico no tiene puntaje: la celda queda
        # vacía y el contrato de tres columnas se mantiene. Un cero diría que
        # la persona puntuó mal, que no es lo que pasó.
        escritor.writerow([item["id_persona"], item.get("puntaje", ""),
                           " | ".join(partes)])
    return salida.getvalue()


# R3.10 — los campos que salen en el CSV identificado. Son exactamente los
# que devuelve la reidentificación, **menos** la fecha de nacimiento exacta y
# las observaciones. No es una omisión: la fecha exacta es un identificador
# fino y las observaciones son texto libre donde suele terminar cayendo dato
# sensible. El tramo etario da la información demográfica sin el identificador.
#
# R3.14.e — la lista es **fija** y no crece con el catálogo. Que un admin
# defina un atributo nuevo no puede hacer que empiece a salir solo en los
# archivos que dejan el sistema, y menos si es una categoría especial. Sumar
# un atributo a esta exportación es una decisión explícita, no un efecto
# secundario de haber ampliado el vocabulario.
CAMPOS_IDENTIFICADOS = (
    "id_persona", "nombre", "documento", "email", "celular", "contacto",
    "sexo", "localidad", "tramo_etario",
)

ENCABEZADO_PII = (
    "# ATENCIÓN: este archivo contiene datos personales de panelistas. "
    "Tratarlo según la política de protección de datos: no reenviarlo fuera "
    "del equipo de campo, no subirlo a servicios de terceros y borrarlo "
    "cuando termine el trabajo para el que se pidió."
)


def a_csv_identificado(reidentificacion, resultado=None):
    """R3.10 — el ranking con datos de contacto, para pasarle al equipo de campo.

    Toma la reidentificación **ya resuelta** y no vuelve a consultar la
    bóveda: exportar no puede ser una segunda reidentificación encubierta,
    porque entonces habría dos caminos para sacar PII y solo uno auditado.

    El archivo se marca en su primera línea. Un CSV con nombres y documentos
    que viaja por correo sin decir lo que es termina, tarde o temprano, en un
    escritorio compartido.
    """
    salida = io.StringIO()
    salida.write(ENCABEZADO_PII + "\n")
    escritor = csv.writer(salida, lineterminator="\n")

    puntajes, evidencias = {}, {}
    for item in (resultado or {}).get("items", []) or []:
        puntajes[str(item["id_persona"])] = item.get("puntaje")
        partes = []
        for evidencia in item.get("evidencias") or []:
            if not evidencia:
                continue
            procedencia = " · ".join(
                p for p in (evidencia.get("estudio"),
                            evidencia.get("pregunta_codigo")) if p
            )
            texto = evidencia.get("valor_texto") or evidencia.get("texto_embebido") or ""
            partes.append(f"[{procedencia}] {texto}" if procedencia else texto)
        evidencias[str(item["id_persona"])] = " | ".join(partes)

    columnas = list(CAMPOS_IDENTIFICADOS) + ["puntaje", "evidencia"]
    escritor.writerow(columnas)
    for persona in reidentificacion.get("items", []):
        id_persona = str(persona["id_persona"])
        escritor.writerow(
            [persona.get(campo) for campo in CAMPOS_IDENTIFICADOS]
            + [puntajes.get(id_persona), evidencias.get(id_persona, "")]
        )
    return salida.getvalue()


def nombre_archivo_identificado(cuando=None):
    """El nombre lleva la marca también: se ve antes de abrirlo."""
    import datetime

    momento = cuando or datetime.datetime.now()
    return f"consulta-CON-DATOS-PERSONALES-{momento:%Y%m%d-%H%M}.csv"


# ════════════════════════════════════════════════════════════════════
#  P1 — consultas guardadas (la definición, nunca el resultado)
# ════════════════════════════════════════════════════════════════════

def _serializar_guardada(fila):
    definicion = fila["definicion"]
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "descripcion": fila["descripcion"],
        "definicion": json.loads(definicion) if isinstance(definicion, str) else definicion,
        "panel_id": fila["panel_id"],
        "creado_por": fila["creado_por"],
        "creado_en": fila["creado_en"].isoformat(),
        "actualizado_por": fila["actualizado_por"],
        "actualizado_en": (
            fila["actualizado_en"].isoformat() if fila["actualizado_en"] else None
        ),
    }


CAMPOS_GUARDADA = (
    "id, nombre, descripcion, definicion, panel_id, creado_por, creado_en, "
    "actualizado_por, actualizado_en"
)


def guardar(conn, nombre, definicion_cruda, descripcion=None, actor=None):
    """Guarda una consulta para volver a correrla.

    Se guarda la DEFINICIÓN, nunca el resultado. Un resultado cacheado
    envejece mal —el padrón cambia, los consentimientos se retiran— y sería
    una lista de personas persistida sin volver a pasar por el gate. Guardar
    la pregunta y volver a hacerla es lo correcto y además es barato.

    La definición se normaliza antes de guardarla: si tiene un criterio mal
    formado, se entera quien la guarda y no quien la corre tres semanas
    después.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosInvalidos("La consulta guardada necesita un nombre.")
    definicion = normalizar_definicion(definicion_cruda, conn)

    fila = db.una(
        conn,
        f"""
        insert into consulta_guardada
               (nombre, descripcion, definicion, panel_id, creado_por)
             values (%s, %s, %s::jsonb, %s, %s)
        on conflict (nombre) do update
                set descripcion = excluded.descripcion,
                    definicion = excluded.definicion,
                    panel_id = excluded.panel_id,
                    actualizado_por = excluded.creado_por,
                    actualizado_en = now()
          returning {CAMPOS_GUARDADA}
        """,
        (
            nombre,
            (descripcion or "").strip() or None,
            json.dumps(definicion, ensure_ascii=False, default=str),
            definicion["panel_id"],
            actor or "desconocido",
        ),
    )
    return _serializar_guardada(fila)


def listar_guardadas(conn, panel_id=None):
    filas = db.todas(
        conn,
        f"""
        select {CAMPOS_GUARDADA}
          from consulta_guardada
         where (%s::bigint is null or panel_id = %s::bigint)
         order by nombre
        """,
        (panel_id, panel_id),
    )
    return [_serializar_guardada(f) for f in filas]


def obtener_guardada(conn, consulta_id):
    fila = db.una(
        conn,
        f"select {CAMPOS_GUARDADA} from consulta_guardada where id = %s",
        (consulta_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la consulta guardada {consulta_id}.")
    return _serializar_guardada(fila)


def borrar_guardada(conn, consulta_id):
    if not db.ejecutar(conn, "delete from consulta_guardada where id = %s", (consulta_id,)):
        raise NoEncontrado(f"No existe la consulta guardada {consulta_id}.")
    return {"id": consulta_id, "estado": "borrada"}
