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
  tapada. Se verifican hasta `MAX_EVIDENCIAS_POR_INDIVIDUO` por persona, en
  una sola llamada al verificador, y un `no_cumple` en cualquiera de ellas
  manda.

Nada de lo que sale de acá es PII: los resultados se identifican por
`id_persona`. Traducir eso a un nombre es una operación aparte, con permiso
aparte, y queda registrada (ver `auditoria.py`).
"""

import csv
import io
import json
import time

from . import (
    consentimiento,
    demografia,
    db,
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


def normalizar_criterio(crudo, orden):
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
        criterio = demografia.normalizar_criterio(crudo)
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


def normalizar_definicion(cruda):
    """Valida y completa el cuerpo de `POST /consultas`."""
    cruda = cruda or {}
    crudos = cruda.get("criterios")
    if crudos is None and cruda.get("texto"):
        crudos = [cruda["texto"]]
    criterios = [normalizar_criterio(c, i) for i, c in enumerate(crudos or [])]
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

    return {
        "criterios": criterios,
        "modo": modo,
        "panel_id": panel_id,
        "top_n": _entero(cruda.get("top_n"), TOP_N_POR_DEFECTO, TOP_N_MAXIMO),
        "top_k": _entero(cruda.get("top_k"), TOP_K_POR_DEFECTO, TOP_K_MAXIMO),
        "limite": _entero(cruda.get("limite"), LIMITE_POR_DEFECTO, TOP_K_MAXIMO),
        "estrategia_puente": estrategia,
        "umbral_distancia": umbral,
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
    otra respuesta suya.
    """
    pares = list(zip(evidencias, juicios))
    for candidato, juicio in pares:
        if juicio["veredicto"] == mod_verificacion.NO_CUMPLE:
            return candidato, juicio
    for candidato, juicio in pares:
        if juicio["veredicto"] == mod_verificacion.CUMPLE:
            return candidato, juicio
    return pares[0]


def _resolver_criterio_semantico(ctx, criterio, definicion, ids_permitidos,
                                 filtro_posterior, reloj, degradaciones,
                                 reranker, verificador):
    """Corre el oleoducto completo para UN criterio semántico.

    Devuelve `{id_persona: hallazgo}`, donde el hallazgo trae el puntaje, la
    evidencia con su procedencia y el veredicto.
    """
    etiqueta = criterio["etiqueta"]

    # 1. Embedding del criterio. Mismo proveedor, modelo y dimensión que la
    #    ingesta: si no fueran los mismos, las distancias no significarían nada.
    desde = time.perf_counter()
    vector = ctx.embeddings.embeber([criterio["texto"]])[0]
    reloj.marca("embedding", desde, criterio=etiqueta, dims=len(vector))

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
        return {}

    # 4. Reranking sobre el pool, antes del colapso (ver el docstring del
    #    módulo).
    desde = time.perf_counter()
    relevancias = {}
    if reranker.disponible:
        textos = [c["texto_embebido"] or c["valor_texto"] or "" for c in pool]
        try:
            relevancias = {
                indice: (max(0.0, min(1.0, puntaje)) if puntaje is not None else None)
                for indice, puntaje in reranker.reordenar(criterio["texto"], textos)
            }
            relevancias = {i: p for i, p in relevancias.items() if p is not None}
        except Exception as error:  # noqa: BLE001 — degradar, no romper
            relevancias = {}
            degradaciones.append({
                "etapa": "reranking",
                "proveedor": reranker.nombre,
                "motivo": f"El proveedor falló: {error}",
                "consecuencia": "Se sigue con el orden del recall (distancia).",
            })
    reloj.marca(
        "reranking", desde, criterio=etiqueta, aplicado=bool(relevancias),
        proveedor=reranker.nombre, entrada=len(pool),
    )

    # 5. Agrupación por individuo y 6. top-k.
    desde = time.perf_counter()
    grupos = _agrupar_por_individuo(pool, relevancias)
    top_k = grupos[: definicion["top_k"]]
    reloj.marca(
        "colapso", desde, criterio=etiqueta,
        individuos=len(grupos), top_k=len(top_k),
        evidencias_a_verificar=sum(len(g["evidencias"]) for g in top_k),
    )

    # 7. Verificación. Todas las evidencias del top-k van en una sola llamada:
    #    la contradicción puede estar en cualquiera de ellas.
    desde = time.perf_counter()
    planas = [evidencia for grupo in top_k for evidencia in grupo["evidencias"]]
    try:
        juicios = verificador.verificar(criterio["texto"], planas)
    except Exception as error:  # noqa: BLE001 — degradar, no romper
        juicios = mod_verificacion.NoDisponible(str(error)).verificar(
            criterio["texto"], planas
        )
        degradaciones.append({
            "etapa": "verificacion",
            "proveedor": verificador.nombre,
            "motivo": f"El proveedor falló: {error}",
            "consecuencia": (
                "Ningún candidato se excluye por veredicto: no se puede excluir "
                "por un juicio que no se emitió."
            ),
        })
    reloj.marca(
        "verificacion", desde, criterio=etiqueta,
        aplicada=verificador.disponible, proveedor=verificador.nombre,
        evidencias_verificadas=len(planas), individuos=len(top_k),
        cumple=sum(1 for j in juicios if j["veredicto"] == mod_verificacion.CUMPLE),
        no_cumple=sum(1 for j in juicios if j["veredicto"] == mod_verificacion.NO_CUMPLE),
        dudoso=sum(1 for j in juicios if j["veredicto"] == mod_verificacion.DUDOSO),
    )

    hallazgos, cursor = {}, 0
    for grupo in top_k:
        cuantas = len(grupo["evidencias"])
        del_grupo = juicios[cursor : cursor + cuantas]
        cursor += cuantas
        candidato, juicio = _elegir_evidencia(grupo["evidencias"], del_grupo)
        relevancia = candidato.get("relevancia")
        hallazgos[grupo["id_persona"]] = {
            "criterio": etiqueta,
            "orden": criterio["orden"],
            "puntaje": round(
                relevancia if relevancia is not None
                else _similitud(candidato["distancia"]),
                4,
            ),
            "distancia": round(candidato["distancia"], 4),
            "relevancia": None if relevancia is None else round(relevancia, 4),
            "veredicto": juicio["veredicto"],
            "razon": juicio["razon"],
            "aviso_polaridad": juicio["aviso_polaridad"],
            "evidencia": _evidencia(candidato),
            # Todas las respuestas suyas que se miraron, con su veredicto:
            # es lo que deja auditar por qué quedó adentro o afuera.
            "evidencias_evaluadas": [
                {**_evidencia(otro), "veredicto": otro_juicio["veredicto"],
                 "razon": otro_juicio["razon"]}
                for otro, otro_juicio in zip(grupo["evidencias"], del_grupo)
            ],
        }
    return hallazgos


# ════════════════════════════════════════════════════════════════════
#  R2.10 — combinación de criterios
# ════════════════════════════════════════════════════════════════════

def _combinar(definicion, por_criterio, umbral, verificacion_aplicada=True):
    """Un puntaje por criterio y uno combinado, con la regla del modo.

    Reglas, en el orden en que se aplican a cada persona:

      no_cumple en cualquier criterio  → afuera, en los dos modos.
      criterio duro sin cumplir        → afuera, en los dos modos.
      dudoso o sin evidencia, estricto → afuera.
      dudoso o sin evidencia, laxo     → adentro, con 0 en ese criterio y
                                         marcada como penalizada.

    Con `verificacion_aplicada=False` —no hay proveedor, o falló— el
    `dudoso` deja de excluir. Es el único caso en que el modo estricto se
    ablanda, y por un motivo simple: cuando la verificación no corrió, TODOS
    los candidatos quedan en `dudoso`, así que aplicar la regla vaciaría el
    ranking. Excluir a todo el mundo por un juicio que nunca se emitió no es
    ser estricto: es devolver una lista vacía y llamarla resultado. La
    ausencia de evidencia sí sigue excluyendo, porque eso lo dice el recall y
    no el verificador.
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
                }
            elif veredicto == mod_verificacion.DUDOSO:
                if verificacion_aplicada and (criterio["duro"] or estricto):
                    excluir = excluir or {
                        "criterio": criterio["etiqueta"],
                        "motivo": mod_verificacion.DUDOSO,
                    }
            detalle.append({
                "criterio": criterio["etiqueta"],
                "tipo": "semantico",
                "puntaje": round(puntaje, 4),
                "peso": criterio["peso"],
                "veredicto": veredicto,
                "razon": hallazgo["razon"],
                "distancia": hallazgo["distancia"],
                "relevancia": hallazgo["relevancia"],
                "aviso_polaridad": hallazgo["aviso_polaridad"],
                "evidencia": hallazgo["evidencia"],
            })

        if excluir:
            excluidos.append({"id_persona": id_persona, **excluir})
            continue

        pesos = sum(d["peso"] for d in detalle) or 1.0
        combinado = sum(d["puntaje"] * d["peso"] for d in detalle) / pesos
        distancias = [d["distancia"] for d in detalle if d.get("distancia") is not None]
        mejor_distancia = min(distancias) if distancias else None
        penalizado = any(
            d["tipo"] == "semantico"
            and d["veredicto"] in (SIN_EVIDENCIA, mod_verificacion.DUDOSO)
            for d in detalle
        )
        confianza_baja = penalizado or (
            mejor_distancia is not None and mejor_distancia > umbral
        )

        items.append({
            "id_persona": id_persona,
            "puntaje": round(combinado, 4),
            "confianza": "baja" if confianza_baja else "alta",
            "mejor_distancia": mejor_distancia,
            "penalizado": penalizado,
            "criterios": sorted(detalle, key=lambda d: (d["tipo"], d["criterio"])),
            "evidencias": [d["evidencia"] for d in detalle if d.get("evidencia")],
        })

    items.sort(key=lambda i: (-i["puntaje"], i["id_persona"]))
    excluidos.sort(key=lambda e: e["id_persona"])
    return items, excluidos


# ════════════════════════════════════════════════════════════════════
#  Punto de entrada
# ════════════════════════════════════════════════════════════════════

def ejecutar(ctx, definicion_cruda, reranker=None, verificador=None):
    """Corre una consulta y devuelve el ranking, la evidencia y el diagnóstico.

    `ctx` es el `Contexto` de la request. La conexión al store semántico es
    perezosa: si la consulta es puramente demográfica, este método no la
    toca, y esa es exactamente la garantía de R2.4.
    """
    definicion = normalizar_definicion(definicion_cruda)
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
        resultado["modo"] = definicion["modo"]
        resultado["puente"] = {
            "estrategia": None,
            "motivo": (
                "La consulta no tiene criterios semánticos: se resuelve entera "
                "en la bóveda y no se abre conexión al store semántico."
            ),
        }
        resultado["degradaciones"] = []
        resultado["diagnostico"] = {
            "ms_total": reloj.ms_total,
            "etapas": reloj.etapas,
            "abrio_semantica": False,
        }
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
                "Todos los candidatos quedan «dudoso» y ninguno se excluye por "
                "veredicto: no se puede excluir por un juicio que no se emitió."
            ),
        })

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
    for criterio in semanticos:
        por_criterio[criterio["orden"]] = _resolver_criterio_semantico(
            ctx, criterio, definicion, ids_permitidos, filtro_posterior,
            reloj, degradaciones, reranker, verificador,
        )

    desde = time.perf_counter()
    items, excluidos = _combinar(
        definicion, por_criterio, definicion["umbral_distancia"],
        verificacion_aplicada=verificador.disponible and not any(
            d["etapa"] == "verificacion" for d in degradaciones
        ),
    )
    reloj.marca(
        "combinacion", desde, modo=definicion["modo"],
        candidatos=len(items) + len(excluidos), excluidos=len(excluidos),
    )

    return {
        "tipo": "mixta" if demograficos else "semantica",
        "modo": definicion["modo"],
        "panel_id": definicion["panel_id"],
        "criterios": definicion["criterios"],
        "parametros": {
            "top_n": definicion["top_n"],
            "top_k": definicion["top_k"],
            "umbral_distancia": definicion["umbral_distancia"],
        },
        "puente": {
            "estrategia": estrategia,
            "motivo": motivo,
            "personas_en_segmento": personas_en_segmento,
            "gate": consentimiento.SEMANTICO,
        },
        "total": len(items),
        "items": items[: definicion["limite"]],
        "excluidos": excluidos,
        "degradaciones": degradaciones,
        "diagnostico": {
            "ms_total": reloj.ms_total,
            "etapas": reloj.etapas,
            "abrio_semantica": True,
            "reranker": reranker.nombre,
            "verificador": verificador.nombre,
        },
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
        escritor.writerow([item["id_persona"], item["puntaje"], " | ".join(partes)])
    return salida.getvalue()


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
    definicion = normalizar_definicion(definicion_cruda)

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
