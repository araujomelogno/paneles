"""Polaridad de una frase: ¿afirma o niega?

La usan dos etapas de la consulta semántica, y por el mismo motivo: la
similitud de embeddings no distingue «me encanta el fernet» de «no me gusta
nada el fernet». Los dos textos hablan de lo mismo, así que los dos salen
cerca del criterio «gente a la que le gusta el fernet», y uno de los dos es
exactamente lo contrario de lo que se buscaba.

En producción esa distinción la hacen el cross-encoder (reranking) y Claude
(verificación), que leen la frase. Este módulo es el respaldo sin red: tres
léxicos chicos, en español rioplatense.

    negadores  no, nunca, jamás, tampoco, ni, nada, ningún
    rechazo    odio, detesto, evito, me aburre, horrible, dejé
    adhesión   me gusta, me encanta, uso, tomo, siempre, mi favorito

La regla es una sola: **la negación gana**. Basta un negador o una marca de
rechazo para que la frase se lea negativa, sin importar cuántas marcas de
adhesión haya; justamente porque «no me gusta» tiene una de cada una y
significa lo primero. La contracara es que una doble negación
(«no me disgusta») sale mal clasificada: es el precio de no analizar la
oración, y por eso esto es un respaldo y no el criterio.

Se usa en tres lugares:

* el reranker léxico, que corre cuando no hay proveedor de reranking;
* el verificador determinístico, que corre en las pruebas;
* como control de coherencia sobre lo que devuelve Claude, para detectar un
  veredicto «cumple» apoyado en una evidencia manifiestamente negativa.

Cuando el proveedor real está disponible, manda él.
"""

import re
import unicodedata

NEGADORES = (
    "no", "nunca", "jamas", "tampoco", "ni", "nada",
    "ningun", "ninguna", "ninguno",
)

RECHAZO = (
    "odio", "odia", "detesto", "detesta", "rechazo", "rechaza",
    "evito", "evita", "disgusta", "desagrada", "molesta", "aburre",
    "desconfio", "desconfia", "deje", "dejo", "abandone",
    "horrible", "malisimo", "pesimo", "espantoso",
)

ADHESION = (
    "si", "claro", "obvio", "siempre", "mucho", "muchisimo", "bastante",
    "me gusta", "gusta", "encanta", "adoro", "prefiero", "prefiere",
    "elijo", "elige", "uso", "usa", "consumo", "consume", "tomo", "toma",
    "compro", "compra", "recomiendo", "recomienda",
    "excelente", "buenisimo", "genial", "favorito", "fanatico",
    "seguido", "habitualmente", "todos los dias",
)


def normalizar(texto):
    """Minúsculas sin tildes: el léxico se escribe una sola vez."""
    descompuesto = unicodedata.normalize("NFD", str(texto or ""))
    sin_tilde = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", sin_tilde).lower()


def palabras(texto):
    return re.findall(r"[a-z0-9ñ]+", normalizar(texto))


def _cuenta(texto, marcas):
    """Cuántas marcas del léxico aparecen. Las de una palabra se buscan como
    palabra entera (para que «nada» no matchee dentro de «nadaba»); las de
    varias, como subcadena."""
    normalizado = normalizar(texto)
    fichas = set(palabras(texto))
    presente = lambda marca: (
        marca in normalizado if " " in marca else marca in fichas
    )
    return sum(1 for marca in marcas if presente(marca))


def marcas(texto):
    """Las tres cuentas, para poder explicar una decisión."""
    return {
        "negadores": _cuenta(texto, NEGADORES),
        "rechazo": _cuenta(texto, RECHAZO),
        "adhesion": _cuenta(texto, ADHESION),
    }


def signo(texto):
    """+1 afirmativo, -1 negativo, 0 sin marcas. Es un signo, no un puntaje."""
    cuentas = marcas(texto)
    if cuentas["negadores"] or cuentas["rechazo"]:
        return -1
    if cuentas["adhesion"]:
        return 1
    return 0


def opuesta(criterio, evidencia):
    """¿La evidencia dice lo contrario de lo que pide el criterio?

    Solo cuando los dos signos existen y son distintos. Sin marcas en la
    evidencia la respuesta es «no lo sé», que acá se codifica como False: la
    etapa que decide excluir es la verificación, y una duda no debería
    excluir a nadie por su cuenta.
    """
    signo_criterio = signo(criterio) or 1  # un criterio sin marcas se lee afirmativo
    signo_evidencia = signo(evidencia)
    return signo_evidencia != 0 and signo_evidencia != signo_criterio
