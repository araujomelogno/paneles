"""R2.8 — Reranking del pool de recuperación, detrás de una interfaz.

El recall por ANN (R2.7) trae lo que **habla de** lo que se buscó. Eso
alcanza para no perder candidatos, y no alcanza para ordenarlos: la
distancia entre embeddings mide parecido temático, no si la respuesta
efectivamente cumple el criterio. El caso que lo muestra es el de polaridad
opuesta: «no me gusta el fernet» está tan cerca de «gente a la que le gusta
el fernet» como «me encanta el fernet».

El reranker es un cross-encoder: mira criterio y respuesta juntos y devuelve
una relevancia. Es más caro que la distancia, así que corre sobre el pool
(top_n), no sobre el corpus, y su salida es el top_k.

Misma forma que `embeddings.py`: una interfaz, un proveedor real y uno sin
red. El resto del código solo conoce `reordenar(criterio, textos)`.

**Degradación explícita.** Si no hay proveedor —falta la clave, la API
responde error— la consulta NO se cae: sigue con el orden del recall y lo
dice en la respuesta (`reranking.aplicado = false` y el motivo). Un ranking
peor avisado es utilizable; un ranking peor callado es un resultado falso.
"""

import os

VOYAGE_URL = "https://api.voyageai.com/v1/rerank"
MODELO_POR_DEFECTO = "rerank-2.5"

# Cuántos documentos se le mandan de una. El pool por defecto (top_n=200)
# entra en un solo pedido; si alguien lo sube mucho, se parte.
LOTE_MAXIMO = 500


class ErrorReranker(RuntimeError):
    """El proveedor no pudo reordenar. Quien lo atrapa degrada y avisa."""


class Reranker:
    """Interfaz. `reordenar` devuelve `[(indice, puntaje)]` de mayor a menor.

    Los índices son posiciones de la lista `textos` que se recibió; el
    puntaje es una relevancia comparable entre candidatos de la MISMA
    llamada (no entre llamadas distintas).
    """

    nombre = "interfaz"
    disponible = True

    def reordenar(self, criterio, textos, top_k=None):
        raise NotImplementedError


class Voyage(Reranker):
    """Cross-encoder de Voyage. Es el proveedor de producción."""

    nombre = "voyage"

    def __init__(self, api_key, modelo=MODELO_POR_DEFECTO):
        if not api_key:
            raise ErrorReranker(
                "Falta RERANKER_API_KEY para Voyage (por variable de entorno / "
                "Secret Manager; nunca en el repo)."
            )
        self.api_key = api_key
        self.modelo = modelo

    def reordenar(self, criterio, textos, top_k=None):
        import requests  # import diferido: solo hace falta con el proveedor real

        if not textos:
            return []
        puntajes = {}
        for inicio in range(0, len(textos), LOTE_MAXIMO):
            lote = textos[inicio : inicio + LOTE_MAXIMO]
            respuesta = requests.post(
                VOYAGE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": criterio, "documents": list(lote), "model": self.modelo},
                timeout=60,
            )
            if respuesta.status_code != 200:
                raise ErrorReranker(
                    f"Voyage rerank devolvió {respuesta.status_code}: "
                    f"{respuesta.text[:300]}"
                )
            for fila in respuesta.json().get("data", []):
                puntajes[inicio + fila["index"]] = float(fila["relevance_score"])

        orden = sorted(puntajes.items(), key=lambda par: (-par[1], par[0]))
        return orden[:top_k] if top_k else orden


class Lexico(Reranker):
    """Reranker sin red: solapamiento de palabras y penalización de polaridad.

    No es un cross-encoder y no pretende serlo. Hace dos cosas que la
    distancia de embeddings no hace y que se pueden hacer sin modelo:

    1. Premia que las palabras del criterio aparezcan en la respuesta.
    2. Castiga la polaridad opuesta (ver `polaridad.py`), que es el error
       específico que el reranking tiene que corregir.

    Sirve para las pruebas —es determinístico— y como red de contención
    cuando no hay proveedor configurado. Que corra en vez de Voyage se
    informa igual en la respuesta: es una degradación.
    """

    nombre = "lexico"

    # Peso de cada componente. El castigo de polaridad es más grande que el
    # solapamiento máximo: un candidato que dice lo contrario tiene que
    # quedar debajo de cualquiera que no lo diga, no solo un poco más abajo.
    PESO_SOLAPAMIENTO = 1.0
    CASTIGO_POLARIDAD = 2.0

    # Palabras que aparecen en cualquier criterio y no discriminan nada.
    VACIAS = frozenset({
        "de", "del", "la", "las", "el", "los", "un", "una", "unos", "unas",
        "que", "quien", "quienes", "a", "al", "y", "o", "en", "con", "por",
        "para", "se", "su", "sus", "lo", "le", "les", "me", "mi", "gente",
        "personas", "persona", "es", "son", "esta", "estan", "ser", "hay",
    })

    def _fichas(self, texto):
        from . import polaridad

        return {p for p in polaridad.palabras(texto) if p not in self.VACIAS and len(p) > 2}

    def reordenar(self, criterio, textos, top_k=None):
        from . import polaridad

        del_criterio = self._fichas(criterio)
        puntajes = []
        for indice, texto in enumerate(textos):
            fichas = self._fichas(texto)
            solapamiento = (
                len(del_criterio & fichas) / len(del_criterio) if del_criterio else 0.0
            )
            puntaje = self.PESO_SOLAPAMIENTO * solapamiento
            if polaridad.opuesta(criterio, texto):
                puntaje -= self.CASTIGO_POLARIDAD
            puntajes.append((indice, round(puntaje, 6)))

        orden = sorted(puntajes, key=lambda par: (-par[1], par[0]))
        return orden[:top_k] if top_k else orden


class NoDisponible(Reranker):
    """Marcador de «no hay reranking». No reordena y explica por qué.

    Existe para que la degradación sea un objeto y no un `None` suelto: el
    motor pregunta `disponible` y arma el aviso con `motivo`.
    """

    nombre = "ninguno"
    disponible = False

    def __init__(self, motivo):
        self.motivo = motivo

    def reordenar(self, criterio, textos, top_k=None):
        orden = [(i, None) for i in range(len(textos))]
        return orden[:top_k] if top_k else orden


def crear(cfg=None, entorno=None):
    """Elige el proveedor. Nunca levanta excepción: si no se puede, devuelve
    un `NoDisponible` con el motivo, y la consulta degrada avisando."""
    entorno = os.environ if entorno is None else entorno
    nombre = (
        getattr(cfg, "proveedor_reranker", None)
        or entorno.get("RERANKER_PROVEEDOR", "voyage")
    ).lower()

    if nombre in ("ninguno", "off", "no"):
        return NoDisponible("El reranking está desactivado por configuración.")
    if nombre in ("lexico", "deterministico", "test"):
        return Lexico()
    if nombre == "voyage":
        api_key = (
            getattr(cfg, "api_key_reranker", None)
            or entorno.get("RERANKER_API_KEY", "")
        )
        modelo = (
            getattr(cfg, "modelo_reranker", None)
            or entorno.get("RERANKER_MODELO", MODELO_POR_DEFECTO)
        )
        try:
            return Voyage(api_key=api_key, modelo=modelo)
        except ErrorReranker as error:
            return NoDisponible(str(error))
    return NoDisponible(f"Proveedor de reranking desconocido: {nombre!r}.")
