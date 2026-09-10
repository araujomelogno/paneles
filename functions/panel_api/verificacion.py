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

Igual que el reranking, degrada avisando: sin proveedor la consulta sigue,
todos los candidatos quedan `dudoso` y la respuesta dice que la verificación
no se aplicó. Con la verificación caída, además, ningún candidato se excluye
por veredicto: no se puede excluir a nadie por un juicio que no se emitió.
"""

import json
import os

from . import pii, polaridad

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
VERSION_API = "2023-06-01"
MODELO_POR_DEFECTO = "claude-sonnet-5"

CUMPLE, NO_CUMPLE, DUDOSO = "cumple", "no_cumple", "dudoso"
VEREDICTOS = (CUMPLE, NO_CUMPLE, DUDOSO)

INSTRUCCIONES = """\
Sos el verificador de una consulta sobre respuestas de encuestas de \
investigación de mercado, en español rioplatense.

Recibís un criterio y una lista numerada de candidatos. Cada candidato es \
una respuesta que alguien dio a una pregunta de una encuesta.

Para cada candidato decidí:
  cumple    — la respuesta muestra que la persona satisface el criterio.
  no_cumple — la respuesta muestra lo contrario del criterio, o lo niega.
  dudoso    — la respuesta habla del tema pero no alcanza para decidir.

Reglas:
* Juzgá SOLO por el texto del candidato. No supongas nada que no diga.
* Una respuesta que menciona el tema no cumple por eso: «no me gusta el \
fernet» habla de fernet y NO cumple el criterio «le gusta el fernet».
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


def _texto_de(candidato):
    """La evidencia de un candidato: la respuesta, con su pregunta como
    contexto. Es lo único que se le muestra al verificador."""
    pregunta = (candidato.get("pregunta_texto") or "").strip()
    respuesta = (candidato.get("valor_texto") or candidato.get("texto_embebido") or "").strip()
    return f"{pregunta} → {respuesta}" if pregunta else respuesta


class Verificador:
    """Interfaz.

    `verificar(criterio, candidatos)` recibe los candidatos del top-k (dicts
    con `pregunta_texto` y `valor_texto`) y devuelve una lista paralela de
    `{veredicto, razon}`, misma longitud y mismo orden que la entrada.
    """

    nombre = "interfaz"
    disponible = True

    def verificar(self, criterio, candidatos):
        raise NotImplementedError


def _completar(crudos, candidatos, criterio):
    """Arma la salida final a partir de lo que dijo el verificador.

    Acá viven las dos garantías: la longitud y el orden los fija la lista de
    candidatos (no el proveedor), y la cita se toma del registro propio.
    """
    por_indice = {}
    for item in crudos or []:
        try:
            indice = int(item.get("n"))
        except (TypeError, ValueError):
            continue
        if 0 <= indice < len(candidatos) and indice not in por_indice:
            veredicto = item.get("veredicto")
            por_indice[indice] = {
                "veredicto": veredicto if veredicto in VEREDICTOS else DUDOSO,
                "razon": (item.get("razon") or "").strip() or None,
            }

    salida = []
    for indice, candidato in enumerate(candidatos):
        juicio = por_indice.get(indice) or {
            "veredicto": DUDOSO,
            "razon": "El verificador no se pronunció sobre este candidato.",
        }
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
            "evidencia": candidato.get("valor_texto"),
            "aviso_polaridad": bool(aviso),
        })
    return salida


class Claude(Verificador):
    """Verificación con la API de Claude. Es el proveedor de producción."""

    nombre = "claude"

    def __init__(self, api_key, modelo=MODELO_POR_DEFECTO, max_tokens=4096, tiempo=120):
        if not api_key:
            raise ErrorVerificacion(
                "Falta CLAUDE_API_KEY (por variable de entorno / Secret Manager; "
                "nunca en el repo)."
            )
        self.api_key = api_key
        self.modelo = modelo
        self.max_tokens = max_tokens
        self.tiempo = tiempo

    def _mensaje(self, criterio, candidatos):
        lineas = [f"Criterio: {criterio}", "", "Candidatos:"]
        for indice, candidato in enumerate(candidatos):
            lineas.append(f"[{indice}] {_texto_de(candidato)}")
        return "\n".join(lineas)

    def verificar(self, criterio, candidatos):
        import requests  # import diferido: solo hace falta con el proveedor real

        if not candidatos:
            return []

        # Tripwire: lo que sale hacia la API no lleva claves de PII. El store
        # semántico no tiene PII, así que esto siempre pasa; está para que si
        # alguien agrega un campo al payload, salte acá y no en producción.
        carga = [{"n": i, "evidencia": _texto_de(c)} for i, c in enumerate(candidatos)]
        pii.validar_sin_pii(carga, contexto="verificacion")

        respuesta = requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": VERSION_API,
                "content-type": "application/json",
            },
            json={
                "model": self.modelo,
                "max_tokens": self.max_tokens,
                "system": INSTRUCCIONES,
                "messages": [{"role": "user", "content": self._mensaje(criterio, candidatos)}],
                "tools": [HERRAMIENTA],
                "tool_choice": {"type": "tool", "name": HERRAMIENTA["name"]},
            },
            timeout=self.tiempo,
        )
        if respuesta.status_code != 200:
            raise ErrorVerificacion(
                f"La API de Claude devolvió {respuesta.status_code}: "
                f"{respuesta.text[:300]}"
            )

        crudos = []
        for bloque in respuesta.json().get("content", []):
            if bloque.get("type") == "tool_use" and bloque.get("name") == HERRAMIENTA["name"]:
                entrada = bloque.get("input") or {}
                if isinstance(entrada, str):
                    try:
                        entrada = json.loads(entrada)
                    except ValueError:
                        entrada = {}
                crudos = entrada.get("veredictos") or []
                break
        if not crudos:
            raise ErrorVerificacion(
                "La API de Claude respondió sin usar la herramienta de veredictos."
            )
        return _completar(crudos, candidatos, criterio)


class Lexico(Verificador):
    """Verificador sin red: léxico de polaridad más solapamiento de palabras.

    Determinístico, y por eso es el que corre en las pruebas: el caso de
    polaridad opuesta del DoD tiene que dar el mismo resultado siempre, sin
    depender de una llamada a la API.

    Decide así:
      * polaridad opuesta al criterio            → no_cumple
      * comparte suficientes palabras del tema   → cumple
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
            else:
                crudos.append({
                    "n": indice,
                    "veredicto": DUDOSO,
                    "razon": "La respuesta toca el tema pero no alcanza para decidir.",
                })
        return _completar(crudos, candidatos, criterio)


class NoDisponible(Verificador):
    """Marcador de «no hay verificación». Deja todo en dudoso y explica."""

    nombre = "ninguno"
    disponible = False

    def __init__(self, motivo):
        self.motivo = motivo

    def verificar(self, criterio, candidatos):
        return [
            {
                "veredicto": DUDOSO,
                "razon": f"Sin verificación: {self.motivo}",
                "evidencia": c.get("valor_texto"),
                "aviso_polaridad": False,
            }
            for c in candidatos
        ]


def crear(cfg=None, entorno=None):
    """Elige el proveedor. Nunca levanta excepción: si no se puede, devuelve
    un `NoDisponible` con el motivo, y la consulta degrada avisando."""
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
            )
        except ErrorVerificacion as error:
            return NoDisponible(str(error))
    return NoDisponible(f"Proveedor de verificación desconocido: {nombre!r}.")
