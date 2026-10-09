"""R2.9 — Verificación del top-k con Claude, detrás de una interfaz.

El recall trae lo que habla del tema y el reranking lo ordena. Ninguna de
las dos etapas se compromete con una afirmación: la distancia y la
relevancia son parecidos, no juicios. La verificación es la etapa que se
compromete: sobre el top-k dice, para cada individuo, si **cumple**, **no
cumple** o es **dudoso**, y señala la respuesta concreta que lo justifica,
con su procedencia (estudio y pregunta).

Dos garantías que están en el diseño y no en el prompt:

1. **No puede inventar evidencia.** El modelo no escribe la cita: devuelve
   el número de candidato, y la cita se arma acá con el `valor_texto` que ya
   estaba en el store semántico. Si devuelve un número que no existe, ese
   veredicto se descarta. Un modelo que alucine una frase no tiene por dónde
   meterla en el resultado.
2. **No ve PII.** Lo que sale hacia la API es texto de pregunta y texto de
   respuesta, los dos del store semántico, que por el invariante central no
   tiene PII. El `id_persona` tampoco viaja: los candidatos se numeran, y la
   correspondencia número → persona se queda de este lado.

── Verificación por lotes (SPEC_verificacion_por_lotes) ──

Hasta acá todos los candidatos iban en **una** llamada, y con volúmenes
reales Claude agotaba el presupuesto de salida antes de terminar la
herramienta (`stop_reason=max_tokens`). Subir `max_tokens` corre la pared de
lugar: la salida crece con las evidencias y el techo del modelo es fijo. Y el
modo de falla peor no era el error: una respuesta truncada que alcanzaba a
traer **algunos** veredictos se aceptaba, el resto se completaba como
`dudoso`, y una verificación incompleta se presentaba como completa.

Ahora:

* **Lotes** de tamaño configurable (`VERIFICACION_LOTE`, 25), con índices
  **locales al lote** en el prompt; la traducción a índices globales ocurre
  al combinar (`verificar_por_lotes`), no en el mensaje.
* **`stop_reason` se mira antes de aceptar nada.** Un lote truncado se
  descarta entero —sus veredictos parciales no se usan nunca— y se parte en
  dos. Lo mismo con una respuesta sin herramienta o ilegible.
* **Presupuesto acotado**: profundidad de subdivisión máxima, un reintento
  para lo transitorio, y un tiempo total (`VERIFICACION_PRESUPUESTO_S`). Lo
  que no entra queda **sin verificar**, y se dice.
* **`sin_verificar` es un estado propio**, distinto de `dudoso`. `dudoso`
  sigue significando lo que siempre: Claude evaluó y no pudo decidir.
  `sin_verificar` es un fallo técnico, lleva su modo de falla (`fallo`), y
  `consultas.py` lo trata aparte: no excluye ni cuenta como cumplimiento.
* **Concurrencia acotada** entre lotes (`VERIFICACION_CONCURRENCIA`): con
  100 evidencias son cuatro llamadas, y en serie no entran en los 90
  segundos que COLOQUIO le da a una consulta.

── Modo de depuración (R-VER.10) ──

Con `VERIFICACION_DEPURACION` encendido, cada llamada —lotes, mitades y
reintentos— deja su solicitud exacta y la respuesta tal cual llegó en
`verificacion_captura` (store semántico, semantica/0008). No es un log: va
apagado por defecto, vence a los 7 días, tiene tope de tamaño y lo lee solo
un admin. El payload no lleva identificadores, así que la captura tampoco.
"""

import json
import os
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import db, pii, polaridad

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
VERSION_API = "2023-06-01"
MODELO_POR_DEFECTO = "claude-sonnet-5"

CUMPLE, NO_CUMPLE, DUDOSO = "cumple", "no_cumple", "dudoso"
IRRELEVANTE = "irrelevante"
"""R-CS · cambio 4 — la respuesta no habla del criterio: no sirve para
afirmarlo ni para negarlo. No es `no_cumple` (no contradice nada) ni `dudoso`
(no «habla del tema sin alcanzar»): es como si no estuviera en el pool. Que
25 respuestas sobre la titularidad del contrato se juzguen irrelevantes para
«usa Xiaomi» evita el falso resultado; **no** recupera los lugares que esas
25 ocuparon —eso lo hacen las unidades de evidencia (cambio 3)—."""
VEREDICTOS = (CUMPLE, NO_CUMPLE, DUDOSO, IRRELEVANTE)
"""Lo que el modelo puede decir. Son juicios: alguien leyó la evidencia."""

SIN_VERIFICAR = "sin_verificar"
"""Lo que queda cuando nadie la leyó: un fallo técnico, no un juicio."""

ESTADOS = VEREDICTOS + (SIN_VERIFICAR,)

# ── Modos de falla. Se diferencian porque se diagnostican distinto ──
TRUNCAMIENTO = "truncamiento"
HERRAMIENTA_AUSENTE = "herramienta_ausente"
RESPUESTA_INVALIDA = "respuesta_invalida"
ERROR_HTTP = "error_http"
ERROR_RED = "error_red"
OMITIDO = "omitido"
PRESUPUESTO_AGOTADO = "presupuesto_agotado"
SIN_PROVEEDOR = "sin_proveedor"

FALLOS = {
    TRUNCAMIENTO: "La respuesta del verificador se cortó por el límite de salida "
                  "y se descartó entera.",
    HERRAMIENTA_AUSENTE: "El verificador no devolvió la herramienta de veredictos.",
    RESPUESTA_INVALIDA: "La respuesta del verificador no se pudo interpretar.",
    ERROR_HTTP: "La API del verificador devolvió un error.",
    ERROR_RED: "No se pudo hablar con la API del verificador.",
    OMITIDO: "El verificador no se pronunció sobre este candidato: queda sin "
             "verificar.",
    PRESUPUESTO_AGOTADO: "Se agotó el tiempo de verificación antes de llegar a "
                         "este candidato.",
    SIN_PROVEEDOR: "No hay verificador disponible.",
}

# Los que se arreglan partiendo el lote: un lote más chico produce menos
# salida, y el modelo tiene menos con qué confundirse.
SE_SUBDIVIDEN = (TRUNCAMIENTO, HERRAMIENTA_AUSENTE, RESPUESTA_INVALIDA)

ESTADOS_HTTP_TRANSITORIOS = frozenset({408, 429, 500, 502, 503, 504, 529})

# ── Parámetros, configurables por entorno (ver `crear`) ──
TAM_LOTE_POR_DEFECTO = 25
CONCURRENCIA_POR_DEFECTO = 4
PRESUPUESTO_S_POR_DEFECTO = 60
"""Segundos para toda la verificación de una consulta. COLOQUIO le da 90 a
la consulta entera: 60 deja margen para embedding, recall y reranking."""
PROFUNDIDAD_MAX_POR_DEFECTO = 5
"""25 → 13 → 7 → 4 → 2 → 1: cinco subdivisiones llegan a una evidencia."""
REINTENTOS_TRANSITORIOS = 1
SEGUNDOS_MINIMOS_POR_LLAMADA = 3
"""Con menos que esto no se empieza una llamada: no va a terminar."""
ESPERA_MAXIMA_S = 10

# ── Modo de depuración (R-VER.10) ──
DIAS_DE_CAPTURA = 7
MAX_BYTES_POR_ENTRADA = 256 * 1024
MAX_BYTES_POR_EJECUCION = 4 * 1024 * 1024

INSTRUCCIONES = """\
Sos el verificador de una consulta sobre respuestas de encuestas de \
investigación de mercado, en español rioplatense.

Recibís un criterio y una lista numerada de candidatos. Cada candidato es \
una respuesta que alguien dio a una pregunta de una encuesta.

Para cada candidato decidí:
  cumple    — la respuesta muestra que la persona satisface el criterio.
  no_cumple — la respuesta muestra lo contrario del criterio, o lo niega.
  dudoso    — la respuesta habla del tema pero no alcanza para decidir.
  irrelevante — la respuesta no habla del criterio: no sirve ni para \
afirmarlo ni para negarlo.

Reglas:
* Juzgá SOLO por el texto del candidato. No supongas nada que no diga.
* Una respuesta que menciona el tema no cumple por eso: «no me gusta el \
fernet» habla de fernet y NO cumple el criterio «le gusta el fernet».
* `no_cumple` es para lo que contradice el criterio. Una respuesta sobre \
otra cosa —quién es el titular del contrato, cuando el criterio es la marca \
del celular— es `irrelevante`, no `no_cumple` ni `dudoso`.
* No escribas la respuesta del candidato: referenciala por su número. La \
cita se arma del registro original.
* Devolvé un veredicto por cada candidato, ninguno de más.

Registrá todo con la herramienta `registrar_veredictos`."""

HERRAMIENTA = {
    "name": "registrar_veredictos",
    "description": "Registra un veredicto por cada candidato recibido.",
    "input_schema": {
        "type": "object",
        "properties": {
            "veredictos": {
                "type": "array",
                "description": "Un elemento por candidato.",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {
                            "type": "integer",
                            "description": "Número del candidato, tal como se recibió.",
                        },
                        "veredicto": {"type": "string", "enum": list(VEREDICTOS)},
                        "razon": {
                            "type": "string",
                            "description": (
                                "Una oración: por qué. Sin transcribir la respuesta."
                            ),
                        },
                    },
                    "required": ["n", "veredicto", "razon"],
                },
            }
        },
        "required": ["veredictos"],
    },
}


class ErrorVerificacion(RuntimeError):
    """El proveedor no pudo verificar. Quien lo atrapa degrada y avisa."""


class FalloLote(Exception):
    """Un lote que no produjo veredictos utilizables, con su modo de falla.

    `transitorio` dice si reintentar el mismo lote tiene sentido (un 429, un
    503, una red cortada); `espera` cuánto aguardar antes, si la API lo dijo.
    """

    def __init__(self, modo, detalle, transitorio=False, stop_reason=None,
                 uso=None, espera=None):
        super().__init__(detalle)
        self.modo = modo
        self.detalle = detalle
        self.transitorio = transitorio
        self.stop_reason = stop_reason
        self.uso = uso or {}
        self.espera = espera


def _texto_de(candidato):
    """La evidencia de un candidato: la respuesta, con su pregunta como
    contexto. Es lo único que se le muestra al verificador."""
    pregunta = (candidato.get("pregunta_texto") or "").strip()
    respuesta = (candidato.get("valor_texto") or candidato.get("texto_embebido") or "").strip()
    return f"{pregunta} → {respuesta}" if pregunta else respuesta


def sin_verificar(candidato, fallo, detalle=None):
    """El juicio de una evidencia que nadie leyó. Lleva el modo de falla."""
    return {
        "veredicto": SIN_VERIFICAR,
        "razon": detalle or FALLOS.get(fallo) or "Sin verificar.",
        "fallo": fallo,
        "evidencia": candidato.get("valor_texto"),
        "aviso_polaridad": False,
    }


# ════════════════════════════════════════════════════════════════════
#  Validación de lo que devolvió el modelo (R-VER.5)
# ════════════════════════════════════════════════════════════════════

def validar_veredictos(crudos, cantidad):
    """`(por_indice, anomalias)` a partir de la lista de la herramienta.

    Se aceptan solo índices enteros, en rango, una vez cada uno y con un
    veredicto permitido. Todo lo demás se cuenta en `anomalias`, que va al
    diagnóstico: un modelo que repite índices o se saltea candidatos es
    información sobre el prompt, no ruido.
    """
    por_indice, rechazados = {}, set()
    anomalias = {"duplicados": [], "fuera_de_rango": [], "ilegibles": 0,
                 "veredicto_no_permitido": [], "faltantes": []}
    for item in crudos or []:
        if not isinstance(item, dict):
            anomalias["ilegibles"] += 1
            continue
        try:
            indice = int(item.get("n"))
        except (TypeError, ValueError):
            anomalias["ilegibles"] += 1
            continue
        if not 0 <= indice < cantidad:
            anomalias["fuera_de_rango"].append(indice)
            continue
        if indice in por_indice or indice in rechazados:
            anomalias["duplicados"].append(indice)
            continue
        veredicto = item.get("veredicto")
        if veredicto not in VEREDICTOS:
            anomalias["veredicto_no_permitido"].append(indice)
            rechazados.add(indice)
            continue
        por_indice[indice] = {
            "veredicto": veredicto,
            "razon": (item.get("razon") or "").strip() or None,
        }
    anomalias["faltantes"] = [
        i for i in range(cantidad) if i not in por_indice and i not in rechazados
    ]
    return por_indice, anomalias


def _completar_con_anomalias(crudos, candidatos, criterio):
    """Arma la salida final a partir de lo que dijo el verificador.

    Acá viven las dos garantías: la longitud y el orden los fija la lista de
    candidatos (no el proveedor), y la cita se toma del registro propio. Lo
    que el modelo no juzgó —o juzgó con un veredicto que no existe— queda
    `sin_verificar`, **no** `dudoso`: no se presenta como un juicio.
    """
    por_indice, anomalias = validar_veredictos(crudos, len(candidatos))
    no_permitidos = set(anomalias["veredicto_no_permitido"])
    salida = []
    for indice, candidato in enumerate(candidatos):
        juicio = por_indice.get(indice)
        if juicio is None:
            salida.append(sin_verificar(
                candidato,
                RESPUESTA_INVALIDA if indice in no_permitidos else OMITIDO))
            continue
        evidencia = _texto_de(candidato)
        # Control de coherencia: si el léxico ve una negación clara y el
        # verificador dijo «cumple», se avisa. No se corrige el veredicto: el
        # modelo leyó la oración y el léxico no (ver polaridad.py).
        aviso = (
            juicio["veredicto"] == CUMPLE
            and polaridad.opuesta(criterio, evidencia)
        )
        salida.append({
            "veredicto": juicio["veredicto"],
            "razon": juicio["razon"],
            "fallo": None,
            "evidencia": candidato.get("valor_texto"),
            "aviso_polaridad": bool(aviso),
        })
    return salida, anomalias


def _completar(crudos, candidatos, criterio):
    return _completar_con_anomalias(crudos, candidatos, criterio)[0]


# ════════════════════════════════════════════════════════════════════
#  El informe de diagnóstico (R-VER.9): métricas, nunca contenido
# ════════════════════════════════════════════════════════════════════

def nuevo_informe(evidencias):
    return {
        "evidencias": evidencias,
        "verificadas": 0,
        "sin_verificar": 0,
        "veredictos": {v: 0 for v in ESTADOS},
        "por_fallo": {},
        "lotes_iniciales": 0,
        "tam_lote": None,
        "concurrencia": None,
        "llamadas": 0,
        "subdivisiones": 0,
        "reintentos": 0,
        "stop_reasons": {},
        "tokens": {"entrada": 0, "salida": 0},
        "anomalias": {"duplicados": 0, "fuera_de_rango": 0, "ilegibles": 0,
                      "veredicto_no_permitido": 0, "omitidos": 0},
        "presupuesto_s": None,
        "presupuesto_agotado": False,
        "duracion_ms": 0,
        "lotes": [],
    }


def _cerrar_informe(informe, juicios, desde):
    informe["duracion_ms"] = int((time.monotonic() - desde) * 1000)
    informe["veredictos"] = {v: 0 for v in ESTADOS}
    informe["por_fallo"] = {}
    for juicio in juicios:
        informe["veredictos"][juicio["veredicto"]] = (
            informe["veredictos"].get(juicio["veredicto"], 0) + 1)
        if juicio["veredicto"] == SIN_VERIFICAR:
            fallo = juicio.get("fallo") or "desconocido"
            informe["por_fallo"][fallo] = informe["por_fallo"].get(fallo, 0) + 1
    informe["sin_verificar"] = informe["veredictos"][SIN_VERIFICAR]
    informe["verificadas"] = len(juicios) - informe["sin_verificar"]
    return informe


def _sumar_uso(informe, uso):
    uso = uso or {}
    informe["tokens"]["entrada"] += int(uso.get("input_tokens") or 0)
    informe["tokens"]["salida"] += int(uso.get("output_tokens") or 0)


def registrar_diagnostico(informe, etiqueta=None):
    """Una línea por verificación en el log. Solo números y códigos: ni el
    criterio, ni las evidencias, ni la clave. El contenido, si hace falta,
    va por el modo de depuración, que no es un log."""
    resumen = {k: v for k, v in informe.items() if k != "lotes"}
    resumen["criterio"] = etiqueta
    print("[verificacion] " + json.dumps(resumen, ensure_ascii=False, default=str))


# ════════════════════════════════════════════════════════════════════
#  La interfaz
# ════════════════════════════════════════════════════════════════════

class Verificador:
    """Interfaz.

    `verificar(criterio, candidatos)` recibe los candidatos del top-k (dicts
    con `pregunta_texto` y `valor_texto`) y devuelve una lista paralela de
    `{veredicto, razon, fallo, evidencia, aviso_polaridad}`, misma longitud
    y mismo orden que la entrada. `veredicto` es uno de `ESTADOS`.

    `verificar_con_informe` devuelve además el diagnóstico. La versión de
    acá envuelve a `verificar`; el proveedor real la reemplaza con lotes.
    """

    nombre = "interfaz"
    disponible = True
    presupuesto_s = None

    def verificar(self, criterio, candidatos):
        raise NotImplementedError

    def verificar_con_informe(self, criterio, candidatos, limite=None,
                              captura=None, etiqueta=None):
        desde = time.monotonic()
        juicios = self.verificar(criterio, candidatos)
        informe = nuevo_informe(len(candidatos))
        informe["lotes_iniciales"] = informe["llamadas"] = 1 if candidatos else 0
        informe["tam_lote"] = len(candidatos)
        return juicios, _cerrar_informe(informe, juicios, desde)


# ════════════════════════════════════════════════════════════════════
#  Lotes, subdivisión y presupuesto (R-VER.1 a R-VER.4, R-VER.8)
# ════════════════════════════════════════════════════════════════════

class Lote:
    """Un tramo `[inicio, fin)` de la lista global de candidatos.

    `id` es estable entre reintentos (L2 sigue siendo L2 en el intento 2) y
    jerárquico entre subdivisiones (L2 → L2.1 y L2.2), así la captura de
    depuración se lee como un árbol.
    """

    __slots__ = ("id", "padre", "inicio", "fin", "profundidad", "intento", "espera")

    def __init__(self, id, inicio, fin, padre=None, profundidad=0, intento=1,
                 espera=None):
        self.id = id
        self.padre = padre
        self.inicio = inicio
        self.fin = fin
        self.profundidad = profundidad
        self.intento = intento
        self.espera = espera

    @property
    def tamano(self):
        return self.fin - self.inicio


def verificar_por_lotes(criterio, candidatos, llamar, *, tam_lote=TAM_LOTE_POR_DEFECTO,
                        concurrencia=CONCURRENCIA_POR_DEFECTO,
                        profundidad_max=PROFUNDIDAD_MAX_POR_DEFECTO,
                        reintentos=REINTENTOS_TRANSITORIOS, limite=None,
                        reloj=time.monotonic, dormir=time.sleep):
    """Verifica `candidatos` de a lotes. Devuelve `(juicios, informe)`.

    `llamar(criterio, candidatos_del_lote, lote, tiempo_max)` es una llamada
    al modelo: devuelve `{crudos, stop_reason, uso}` con índices **locales**
    o levanta `FalloLote`. Es lo único que sabe de la API; todo lo demás
    —partir, reintentar, cortar por tiempo, recomponer en orden— vive acá y
    se prueba con respuestas simuladas.

    Garantías:
      · la salida tiene la longitud y el orden de la entrada;
      · un lote fallido no aporta **ningún** veredicto, ni parcial;
      · todo termina: profundidad acotada, reintentos acotados, y `limite`
        (un instante de `reloj`) corta lo que no llegó a empezar.
    """
    n = len(candidatos)
    informe = nuevo_informe(n)
    informe["tam_lote"] = tam_lote
    informe["concurrencia"] = concurrencia
    desde = time.monotonic()
    juicios = [None] * n
    if not n:
        return [], _cerrar_informe(informe, [], desde)

    tam_lote = max(1, int(tam_lote))
    concurrencia = max(1, int(concurrencia))
    pendientes = deque(
        Lote(f"L{k + 1}", inicio, min(inicio + tam_lote, n))
        for k, inicio in enumerate(range(0, n, tam_lote))
    )
    informe["lotes_iniciales"] = len(pendientes)

    def restante():
        return None if limite is None else limite - reloj()

    def marcar(lote, fallo, detalle=None):
        for g in range(lote.inicio, lote.fin):
            juicios[g] = sin_verificar(candidatos[g], fallo, detalle)

    def correr(lote, tiempo_max):
        if lote.espera:
            dormir(lote.espera)
        return llamar(criterio, candidatos[lote.inicio:lote.fin], lote, tiempo_max)

    def anotar(lote, ms, resultado, stop_reason=None, omitidos=0):
        informe["lotes"].append({
            "lote": lote.id, "padre": lote.padre, "profundidad": lote.profundidad,
            "intento": lote.intento, "evidencias": lote.tamano,
            "desde": lote.inicio, "ms": ms, "resultado": resultado,
            "stop_reason": stop_reason, "omitidos": omitidos,
        })

    ejecutor = ThreadPoolExecutor(max_workers=concurrencia)
    en_vuelo = {}
    try:
        while pendientes or en_vuelo:
            while pendientes and len(en_vuelo) < concurrencia:
                lote = pendientes.popleft()
                queda = restante()
                if queda is not None and queda < SEGUNDOS_MINIMOS_POR_LLAMADA + (lote.espera or 0):
                    informe["presupuesto_agotado"] = True
                    marcar(lote, PRESUPUESTO_AGOTADO)
                    continue
                informe["llamadas"] += 1
                tiempo_max = None if queda is None else queda - (lote.espera or 0)
                en_vuelo[ejecutor.submit(correr, lote, tiempo_max)] = (lote, reloj())
            if not en_vuelo:
                break

            queda = restante()
            espera = None if queda is None else max(0.0, queda) + SEGUNDOS_MINIMOS_POR_LLAMADA
            hechos, _ = wait(list(en_vuelo), timeout=espera, return_when=FIRST_COMPLETED)
            if not hechos:
                # El tiempo se terminó con llamadas colgadas: lo que no volvió
                # queda sin verificar, y lo que no empezó también.
                informe["presupuesto_agotado"] = True
                for futuro, (lote, _) in en_vuelo.items():
                    futuro.cancel()
                    marcar(lote, PRESUPUESTO_AGOTADO)
                    anotar(lote, None, PRESUPUESTO_AGOTADO)
                en_vuelo.clear()
                while pendientes:
                    marcar(pendientes.popleft(), PRESUPUESTO_AGOTADO)
                break

            for futuro in hechos:
                lote, arranque = en_vuelo.pop(futuro)
                ms = int((reloj() - arranque) * 1000)
                try:
                    respuesta = futuro.result()
                except FalloLote as error:
                    falla = error
                except Exception as error:  # noqa: BLE001 — se registra como fallo
                    falla = FalloLote(ERROR_RED, f"{type(error).__name__}: {error}")
                else:
                    falla = None

                if falla is None:
                    _sumar_uso(informe, respuesta.get("uso"))
                    stop = respuesta.get("stop_reason")
                    if stop:
                        informe["stop_reasons"][stop] = informe["stop_reasons"].get(stop, 0) + 1
                    del_lote, anomalias = _completar_con_anomalias(
                        respuesta.get("crudos"), candidatos[lote.inicio:lote.fin], criterio)
                    for local, juicio in enumerate(del_lote):
                        juicios[lote.inicio + local] = juicio
                    for clave in ("duplicados", "fuera_de_rango", "veredicto_no_permitido"):
                        informe["anomalias"][clave] += len(anomalias[clave])
                    informe["anomalias"]["ilegibles"] += anomalias["ilegibles"]
                    informe["anomalias"]["omitidos"] += len(anomalias["faltantes"])
                    anotar(lote, ms, "ok", stop, len(anomalias["faltantes"]))
                    continue

                _sumar_uso(informe, falla.uso)
                if falla.stop_reason:
                    informe["stop_reasons"][falla.stop_reason] = (
                        informe["stop_reasons"].get(falla.stop_reason, 0) + 1)
                anotar(lote, ms, falla.modo, falla.stop_reason)

                if falla.modo in SE_SUBDIVIDEN:
                    if lote.tamano > 1 and lote.profundidad < profundidad_max:
                        mitad = lote.inicio + (lote.tamano + 1) // 2
                        informe["subdivisiones"] += 1
                        pendientes.append(Lote(f"{lote.id}.1", lote.inicio, mitad,
                                               lote.id, lote.profundidad + 1))
                        pendientes.append(Lote(f"{lote.id}.2", mitad, lote.fin,
                                               lote.id, lote.profundidad + 1))
                    else:
                        marcar(lote, falla.modo)
                elif falla.transitorio and lote.intento <= reintentos:
                    informe["reintentos"] += 1
                    pendientes.append(Lote(
                        lote.id, lote.inicio, lote.fin, lote.padre, lote.profundidad,
                        lote.intento + 1,
                        espera=min(falla.espera or float(lote.intento), ESPERA_MAXIMA_S)))
                else:
                    marcar(lote, falla.modo, f"{FALLOS[falla.modo]} {falla.detalle}".strip()
                           if falla.modo in (ERROR_HTTP, ERROR_RED) else None)
    finally:
        ejecutor.shutdown(wait=False, cancel_futures=True)

    for g, juicio in enumerate(juicios):
        if juicio is None:  # no debería pasar; si pasa, no se inventa un juicio
            juicios[g] = sin_verificar(candidatos[g], PRESUPUESTO_AGOTADO)
    return juicios, _cerrar_informe(informe, juicios, desde)


# ════════════════════════════════════════════════════════════════════
#  El proveedor de producción
# ════════════════════════════════════════════════════════════════════

class Claude(Verificador):
    """Verificación con la API de Claude. Es el proveedor de producción."""

    nombre = "claude"

    def __init__(self, api_key, modelo=MODELO_POR_DEFECTO, max_tokens=16384, tiempo=120,
                 tam_lote=TAM_LOTE_POR_DEFECTO, concurrencia=CONCURRENCIA_POR_DEFECTO,
                 presupuesto_s=PRESUPUESTO_S_POR_DEFECTO,
                 profundidad_max=PROFUNDIDAD_MAX_POR_DEFECTO, transporte=None):
        if not api_key:
            raise ErrorVerificacion(
                "Falta CLAUDE_API_KEY (por variable de entorno / Secret Manager; "
                "nunca en el repo)."
            )
        self.api_key = api_key
        self.modelo = modelo
        self.max_tokens = max_tokens
        self.tiempo = tiempo
        self.tam_lote = tam_lote
        self.concurrencia = concurrencia
        self.presupuesto_s = presupuesto_s
        self.profundidad_max = profundidad_max
        # Para las pruebas: `(cuerpo_json, timeout) -> respuesta` con
        # `status_code`, `text` y `headers`. En producción es `requests.post`.
        self._transporte = transporte

    def _mensaje(self, criterio, candidatos):
        lineas = [f"Criterio: {criterio}", "", "Candidatos:"]
        for indice, candidato in enumerate(candidatos):
            lineas.append(f"[{indice}] {_texto_de(candidato)}")
        return "\n".join(lineas)

    def _cuerpo(self, criterio, candidatos):
        return {
            "model": self.modelo,
            "max_tokens": self.max_tokens,
            "system": INSTRUCCIONES,
            "messages": [{"role": "user", "content": self._mensaje(criterio, candidatos)}],
            "tools": [HERRAMIENTA],
            "tool_choice": {"type": "tool", "name": HERRAMIENTA["name"]},
        }

    def _enviar(self, cuerpo, timeout):
        if self._transporte is not None:
            return self._transporte(cuerpo, timeout)
        import requests  # import diferido: solo hace falta con el proveedor real

        return requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": VERSION_API,
                "content-type": "application/json",
            },
            data=cuerpo.encode("utf-8"),
            timeout=timeout,
        )

    @staticmethod
    def _interpretar(estado, texto, cabeceras):
        """La respuesta de la API → `{crudos, stop_reason, uso}` o `FalloLote`.

        El orden importa: **`stop_reason` se mira antes de buscar los
        veredictos**. Una respuesta truncada puede traer una herramienta
        parcial y bien formada; aceptarla es exactamente el modo de falla
        silencioso que esta spec cierra.
        """
        if estado != 200:
            espera = None
            try:
                espera = float((cabeceras or {}).get("retry-after"))
            except (TypeError, ValueError):
                pass
            # Solo el código: el cuerpo del error queda en la captura de
            # depuración, no en la razón que viaja al resultado.
            return FalloLote(ERROR_HTTP, f"HTTP {estado}.",
                             transitorio=estado in ESTADOS_HTTP_TRANSITORIOS,
                             espera=espera)
        try:
            datos = json.loads(texto)
        except (TypeError, ValueError):
            return FalloLote(RESPUESTA_INVALIDA, "La respuesta no es JSON.")
        if not isinstance(datos, dict):
            return FalloLote(RESPUESTA_INVALIDA, "La respuesta no es un objeto.")

        stop = datos.get("stop_reason")
        uso = datos.get("usage") or {}
        if stop == "max_tokens":
            return FalloLote(
                TRUNCAMIENTO,
                f"stop_reason=max_tokens; output_tokens={uso.get('output_tokens')}",
                stop_reason=stop, uso=uso)

        bloque = next(
            (b for b in datos.get("content") or []
             if isinstance(b, dict) and b.get("type") == "tool_use"
             and b.get("name") == HERRAMIENTA["name"]),
            None)
        if bloque is None:
            tipos = [b.get("type") for b in datos.get("content") or [] if isinstance(b, dict)]
            return FalloLote(HERRAMIENTA_AUSENTE, f"stop_reason={stop}; bloques={tipos}",
                             stop_reason=stop, uso=uso)
        entrada = bloque.get("input") or {}
        if isinstance(entrada, str):
            try:
                entrada = json.loads(entrada)
            except ValueError:
                entrada = None
        crudos = entrada.get("veredictos") if isinstance(entrada, dict) else None
        if not isinstance(crudos, list) or not crudos:
            return FalloLote(RESPUESTA_INVALIDA,
                             "La herramienta no trae una lista de veredictos.",
                             stop_reason=stop, uso=uso)
        return {"crudos": crudos, "stop_reason": stop, "uso": uso}

    def _llamar(self, criterio, candidatos, lote, tiempo_max, captura=None,
                etiqueta=None, respuesta_ids=None):
        # Tripwire: lo que sale hacia la API no lleva claves de PII. El store
        # semántico no tiene PII, así que esto siempre pasa; está para que si
        # alguien agrega un campo al payload, salte acá y no en producción.
        carga = [{"n": i, "evidencia": _texto_de(c)} for i, c in enumerate(candidatos)]
        pii.validar_sin_pii(carga, contexto="verificacion")

        cuerpo = json.dumps(self._cuerpo(criterio, candidatos), ensure_ascii=False)
        timeout = self.tiempo if tiempo_max is None else max(1.0, min(self.tiempo, tiempo_max))
        arranque = time.monotonic()
        estado, texto, cabeceras = None, None, {}
        try:
            respuesta = self._enviar(cuerpo, timeout)
            estado = respuesta.status_code
            texto = respuesta.text
            cabeceras = {k.lower(): v for k, v in (respuesta.headers or {}).items()}
            resultado = self._interpretar(estado, texto, cabeceras)
        except Exception as error:  # noqa: BLE001 — red cortada, timeout, DNS
            texto = f"{type(error).__name__}: {error}"
            resultado = FalloLote(ERROR_RED, texto[:300], transitorio=True)

        if captura is not None:
            captura.registrar(
                criterio=etiqueta or criterio, lote=lote, respuesta_ids=respuesta_ids,
                solicitud=cuerpo, estado_http=estado, respuesta=texto,
                resultado=resultado.modo if isinstance(resultado, FalloLote) else "ok",
                duracion_ms=int((time.monotonic() - arranque) * 1000))
        if isinstance(resultado, FalloLote):
            raise resultado
        return resultado

    def verificar(self, criterio, candidatos):
        return self.verificar_con_informe(criterio, candidatos)[0]

    def verificar_con_informe(self, criterio, candidatos, limite=None,
                              captura=None, etiqueta=None):
        if limite is None and self.presupuesto_s:
            limite = time.monotonic() + self.presupuesto_s
        ids = [c.get("respuesta_id") for c in candidatos]

        def llamar(crit, del_lote, lote, tiempo_max):
            return self._llamar(crit, del_lote, lote, tiempo_max, captura=captura,
                                etiqueta=etiqueta,
                                respuesta_ids=ids[lote.inicio:lote.fin])

        juicios, informe = verificar_por_lotes(
            criterio, candidatos, llamar, tam_lote=self.tam_lote,
            concurrencia=self.concurrencia, profundidad_max=self.profundidad_max,
            limite=limite)
        informe["presupuesto_s"] = self.presupuesto_s
        registrar_diagnostico(informe, etiqueta)
        return juicios, informe


class Lexico(Verificador):
    """Verificador sin red: léxico de polaridad más solapamiento de palabras.

    Determinístico, y por eso es el que corre en las pruebas: el caso de
    polaridad opuesta del DoD tiene que dar el mismo resultado siempre, sin
    depender de una llamada a la API.

    Decide así:
      * polaridad opuesta al criterio            → no_cumple
      * comparte suficientes palabras del tema   → cumple
      * no comparte ninguna                      → irrelevante
      * el resto                                 → dudoso
    """

    nombre = "lexico"

    UMBRAL_SOLAPAMIENTO = 0.34

    VACIAS = frozenset({
        "de", "del", "la", "las", "el", "los", "un", "una", "unos", "unas",
        "que", "quien", "quienes", "a", "al", "y", "o", "en", "con", "por",
        "para", "se", "su", "sus", "lo", "le", "les", "me", "mi", "gente",
        "personas", "persona", "es", "son", "esta", "estan", "ser", "hay",
        "no", "nunca", "jamas", "tampoco", "ni", "nada",
    })

    def _fichas(self, texto):
        return {
            p for p in polaridad.palabras(texto)
            if p not in self.VACIAS and len(p) > 2
        }

    def verificar(self, criterio, candidatos):
        del_criterio = self._fichas(criterio)
        crudos = []
        for indice, candidato in enumerate(candidatos):
            evidencia = _texto_de(candidato)
            if polaridad.opuesta(criterio, evidencia):
                crudos.append({
                    "n": indice,
                    "veredicto": NO_CUMPLE,
                    "razon": "La respuesta niega o rechaza lo que pide el criterio.",
                })
                continue
            fichas = self._fichas(evidencia)
            solapamiento = (
                len(del_criterio & fichas) / len(del_criterio) if del_criterio else 0.0
            )
            if solapamiento >= self.UMBRAL_SOLAPAMIENTO:
                crudos.append({
                    "n": indice,
                    "veredicto": CUMPLE,
                    "razon": "La respuesta afirma el tema que pide el criterio.",
                })
            elif not solapamiento:
                crudos.append({
                    "n": indice,
                    "veredicto": IRRELEVANTE,
                    "razon": "La respuesta no habla del tema del criterio.",
                })
            else:
                crudos.append({
                    "n": indice,
                    "veredicto": DUDOSO,
                    "razon": "La respuesta toca el tema pero no alcanza para decidir.",
                })
        return _completar(crudos, candidatos, criterio)


class NoDisponible(Verificador):
    """Marcador de «no hay verificación». Deja todo **sin verificar** —no
    `dudoso`: nadie evaluó nada— y explica por qué."""

    nombre = "ninguno"
    disponible = False

    def __init__(self, motivo):
        self.motivo = motivo

    def verificar(self, criterio, candidatos):
        return [
            sin_verificar(c, SIN_PROVEEDOR, f"Sin verificación: {self.motivo}")
            for c in candidatos
        ]


def _entero(valor, por_defecto, minimo=1):
    try:
        return max(minimo, int(str(valor).strip()))
    except (TypeError, ValueError):
        return por_defecto


def crear(cfg=None, entorno=None):
    """Elige el proveedor. Nunca levanta excepción: si no se puede, devuelve
    un `NoDisponible` con el motivo, y la consulta degrada avisando.

    Los parámetros de los lotes salen del entorno y no del código (R-VER.1):
    el tamaño de lote es el número a calibrar con datos reales.
    """
    entorno = os.environ if entorno is None else entorno
    nombre = (
        getattr(cfg, "proveedor_verificacion", None)
        or entorno.get("VERIFICACION_PROVEEDOR", "claude")
    ).lower()

    if nombre in ("ninguno", "off", "no"):
        return NoDisponible("La verificación está desactivada por configuración.")
    if nombre in ("lexico", "deterministico", "test"):
        return Lexico()
    if nombre == "claude":
        try:
            return Claude(
                api_key=(
                    getattr(cfg, "api_key_claude", None)
                    or entorno.get("CLAUDE_API_KEY", "")
                ),
                modelo=(
                    getattr(cfg, "modelo_claude", None)
                    or entorno.get("CLAUDE_MODELO", MODELO_POR_DEFECTO)
                ),
                tam_lote=_entero(
                    entorno.get("VERIFICACION_LOTE") or TAM_LOTE_POR_DEFECTO,
                    TAM_LOTE_POR_DEFECTO),
                concurrencia=_entero(
                    entorno.get("VERIFICACION_CONCURRENCIA") or CONCURRENCIA_POR_DEFECTO,
                    CONCURRENCIA_POR_DEFECTO),
                presupuesto_s=_entero(
                    entorno.get("VERIFICACION_PRESUPUESTO_S") or PRESUPUESTO_S_POR_DEFECTO,
                    PRESUPUESTO_S_POR_DEFECTO, minimo=5),
                profundidad_max=_entero(
                    entorno.get("VERIFICACION_PROFUNDIDAD_MAX") or PROFUNDIDAD_MAX_POR_DEFECTO,
                    PROFUNDIDAD_MAX_POR_DEFECTO, minimo=0),
            )
        except ErrorVerificacion as error:
            return NoDisponible(str(error))
    return NoDisponible(f"Proveedor de verificación desconocido: {nombre!r}.")


# ════════════════════════════════════════════════════════════════════
#  R-VER.10 — Modo de depuración: la captura del intercambio
# ════════════════════════════════════════════════════════════════════

_VERDADERO = ("1", "si", "sí", "true", "on", "yes")


def depuracion_activa(entorno=None):
    """Solo con la señal explícita. Nunca por el nivel de log ni porque hubo
    un error: una captura que se enciende sola deja de ser excepcional."""
    entorno = os.environ if entorno is None else entorno
    return (entorno.get("VERIFICACION_DEPURACION") or "").strip().lower() in _VERDADERO


def _recortar(texto, maximo):
    """`(texto, truncado)` con a lo sumo `maximo` bytes en UTF-8."""
    if texto is None:
        return None, False
    crudo = texto.encode("utf-8")
    if len(crudo) <= maximo:
        return texto, False
    return crudo[:max(0, maximo)].decode("utf-8", "ignore"), True


class Captura:
    """Lo que se mandó y lo que volvió, por llamada, de **una** ejecución.

    Junta en memoria —las llamadas corren en hilos y una conexión de psycopg
    no se comparte entre hilos— y escribe todo junto al final con
    `persistir`. Los topes se aplican al registrar: por entrada (cada cuerpo)
    y por ejecución (la suma). Superado el de ejecución, la llamada queda
    anotada igual, sin contenido y con `omitida_por_tope`, para que se vea
    que existió.
    """

    def __init__(self, ejecucion_id, max_bytes_entrada=MAX_BYTES_POR_ENTRADA,
                 max_bytes_ejecucion=MAX_BYTES_POR_EJECUCION, dias=DIAS_DE_CAPTURA):
        self.ejecucion_id = str(ejecucion_id)
        self.max_bytes_entrada = max_bytes_entrada
        self.max_bytes_ejecucion = max_bytes_ejecucion
        self.dias = dias
        self.filas = []
        self.bytes_usados = 0
        self._candado = threading.Lock()

    def registrar(self, *, criterio, lote, respuesta_ids, solicitud, estado_http,
                  respuesta, resultado, duracion_ms):
        es_json = False
        if respuesta is not None:
            try:
                json.loads(respuesta)
                es_json = True
            except (TypeError, ValueError):
                es_json = False
        solicitud_bytes = len((solicitud or "").encode("utf-8"))
        respuesta_bytes = len((respuesta or "").encode("utf-8"))
        with self._candado:
            disponible = self.max_bytes_ejecucion - self.bytes_usados
            omitida = disponible <= 0
            if omitida:
                sol, sol_t, resp, resp_t = None, True, None, True
            else:
                por_campo = min(self.max_bytes_entrada, max(1, disponible // 2))
                sol, sol_t = _recortar(solicitud, por_campo)
                resp, resp_t = _recortar(respuesta, por_campo)
                self.bytes_usados += (len((sol or "").encode("utf-8"))
                                      + len((resp or "").encode("utf-8")))
            self.filas.append({
                "criterio": criterio,
                "lote": lote.id,
                "lote_padre": lote.padre,
                "profundidad": lote.profundidad,
                "intento": lote.intento,
                "primera_evidencia": lote.inicio,
                "evidencias": lote.tamano,
                "respuesta_ids": [int(i) for i in (respuesta_ids or []) if i is not None],
                "solicitud": sol,
                "solicitud_bytes": solicitud_bytes,
                "solicitud_truncada": sol_t,
                "estado_http": estado_http,
                "respuesta_api": resp,
                "respuesta_bytes": respuesta_bytes,
                "respuesta_truncada": resp_t,
                "respuesta_es_json": es_json,
                "omitida_por_tope": omitida,
                "resultado": resultado,
                "duracion_ms": duracion_ms,
            })

    def persistir(self, conn):
        """Escribe lo capturado y purga lo vencido. Devuelve cuántas filas."""
        purgar_capturas(conn)
        for fila in self.filas:
            db.ejecutar(
                conn,
                """insert into verificacion_captura
                          (ejecucion_id, criterio, lote, lote_padre, profundidad,
                           intento, primera_evidencia, evidencias, respuesta_ids,
                           solicitud, solicitud_bytes, solicitud_truncada,
                           estado_http, respuesta_api, respuesta_bytes,
                           respuesta_truncada, respuesta_es_json, omitida_por_tope,
                           resultado, duracion_ms, vence_en)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s,
                           now() + make_interval(days => %s))""",
                (self.ejecucion_id, fila["criterio"], fila["lote"], fila["lote_padre"],
                 fila["profundidad"], fila["intento"], fila["primera_evidencia"],
                 fila["evidencias"], fila["respuesta_ids"], fila["solicitud"],
                 fila["solicitud_bytes"], fila["solicitud_truncada"],
                 fila["estado_http"], fila["respuesta_api"], fila["respuesta_bytes"],
                 fila["respuesta_truncada"], fila["respuesta_es_json"],
                 fila["omitida_por_tope"], fila["resultado"], fila["duracion_ms"],
                 self.dias))
        conn.commit()
        return len(self.filas)


def nueva_captura(ejecucion_id, entorno=None):
    """La captura de una ejecución, o `None` si el modo está apagado."""
    entorno = os.environ if entorno is None else entorno
    if not depuracion_activa(entorno):
        return None
    return Captura(
        ejecucion_id,
        dias=_entero(entorno.get("VERIFICACION_DEPURACION_DIAS") or DIAS_DE_CAPTURA,
                     DIAS_DE_CAPTURA))


def purgar_capturas(conn):
    """Borra las capturas vencidas. Corre en cada escritura y cada lectura."""
    return db.ejecutar(conn, "delete from verificacion_captura where vence_en <= now()")


def capturas_de(conn, ejecucion_id):
    """El intercambio de una ejecución, en el orden en que ocurrió."""
    purgar_capturas(conn)
    conn.commit()
    filas = db.todas(
        conn,
        """select id, criterio, lote, lote_padre, profundidad, intento,
                  primera_evidencia, evidencias, solicitud, solicitud_bytes,
                  solicitud_truncada, estado_http, respuesta_api, respuesta_bytes,
                  respuesta_truncada, respuesta_es_json, omitida_por_tope,
                  resultado, duracion_ms, creado_en, vence_en
             from verificacion_captura
            where ejecucion_id = %s
            order by id""",
        (str(ejecucion_id),))
    return [{**f, "creado_en": f["creado_en"].isoformat(),
             "vence_en": f["vence_en"].isoformat()} for f in filas]


def borrar_capturas_de_respuestas(conn, respuesta_ids):
    """El alcance de una baja: las capturas que llevan alguna de esas
    respuestas se van enteras (no se puede sacar una evidencia de un cuerpo
    capturado sin dejar de ser «lo que se mandó»)."""
    if not respuesta_ids:
        return 0
    return db.ejecutar(
        conn,
        "delete from verificacion_captura where respuesta_ids && %s::bigint[]",
        (list(respuesta_ids),))
