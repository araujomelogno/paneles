"""Fase 8 — calidad del dato semántico.

El motor de búsqueda es tan bueno como el texto que se embebe, y ese texto
salía casi tal cual del archivo. En la primera carga real eso produjo:

    "Cigarrillos:Pensando en el ÚLTIMO mes, ¿has consumido alguno de estos
     productos? Seleccione los que correpondan → Checked"

Tres problemas en una línea: `Checked` no significa nada en español, la
pregunta arrastra un prefijo de batería y una consigna de instrumento, y se
guardan también las respuestas que nadie dio (las opciones no marcadas). Y
uno que no se ve: una abierta de «Otro: especificar» puede traer un teléfono.

Este módulo **detecta** esos patrones y **propone** la corrección. No aplica
ninguna: el texto es lo que se embebe, y una reescritura automática
equivocada degrada la búsqueda en silencio. El sistema propone, el analista
confirma, el original se conserva (`pregunta.texto_original`) y la vista
previa muestra el texto embebido real antes de ingestar.

── Sobre qué trabaja ──

Todo se calcula sobre **distribuciones**: `{codigo: {valor_crudo: filas}}`.
Es lo que se puede armar igual desde un `.sav` (`sav.analizar`), desde las
filas de un `.csv` (`distribucion_de_filas`) y desde un estudio ya ingestado
(`reproceso.distribuciones`), así que la detección es una sola para las tres
puertas. Y todo lo que dice «cómo va a quedar» pasa por
`ingesta.respuesta_de`, que es lo mismo que corre al ingestar.

── Jerarquía ──

Más detección es más decisiones por carga, y hay un punto donde el paso de
revisión se vuelve tan largo que la gente confirma sin mirar. Por eso cada
hallazgo lleva una severidad y la lista sale ordenada: primero lo que
**rompe** (PII, códigos sin traducir, textos truncados), después lo que
**mejora** (baterías, no respuesta, textos, pares, tipos), y al final lo que
es solo **información** (variables casi constantes).

── Lo que no hace ──

No corrige ortografía ni reescribe lo que la gente contestó, no canoniza las
abiertas, y no detecta PII con un modelo: son patrones, con los falsos
positivos (un número largo que no es un teléfono) y negativos (un nombre
suelto es indistinguible de una palabra) que eso implica. Se informa como
indicio, no como certeza.
"""

import re
import unicodedata

from . import ingesta

ROMPE = "rompe"
MEJORA = "mejora"
INFO = "info"
_ORDEN = {ROMPE: 0, MEJORA: 1, INFO: 2}

CERRADA, ABIERTA, ESCALA, NUMERICA = "cerrada", "abierta", "escala", "numerica"

# ── R8.5 · La lista por defecto de valores de no respuesta ───────────
#
# Configurable por carga: la pantalla la muestra editable y la manda en
# `valores_no_respuesta`. Los textos se comparan sin mayúsculas ni tildes.
# Los códigos (98, 99) solo cuentan en variables **con etiquetas**: en una
# numérica, 99 puede ser una edad.
VALORES_NO_RESPUESTA = (
    "98", "99",
    "No sabe", "No contesta", "No aplica", "No responde",
    "NS", "NC", "NS/NC", "Ns/Nc", "No sabe / No contesta", "No sabe/No contesta",
    "Prefiero no responder", "Prefiere no responder", "Sin dato",
    "Don't know", "Refused", "N/A",
)
# Cómo se etiqueta un código de no respuesta que vino sin etiqueta, si se
# decide ingestarlo igual: nunca con el código (R8.5).
ETIQUETA_NO_RESPUESTA = "No sabe / No contesta"

# ── R8.7 ──
PROPORCION_CASI_CONSTANTE = 0.95
RESPUESTAS_PARA_CONSTANTE = 20

# ── R8.2 ──
LARGO_TRUNCADO_SPSS = 250        # bytes: SPSS corta los labels en 256
EJEMPLOS_DE_PII = 3
EJEMPLOS_DE_VISTA_PREVIA = 3


def _plano(texto):
    """Minúsculas, sin tildes, sin espacios de más: la forma de comparar."""
    texto = unicodedata.normalize("NFD", str(texto or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return " ".join(texto.lower().split())


def lista_no_respuesta(valores=None):
    """La lista efectiva. `None` o vacía → la de por defecto."""
    limpia = [str(v).strip() for v in (valores or []) if str(v).strip()]
    return limpia or list(VALORES_NO_RESPUESTA)


def distribucion_de_filas(filas, codigos=None):
    """`{codigo: {valor: filas}}` de un conjunto de filas anchas.

    Las celdas vacías no son valores del archivo y no se cuentan. Los
    números de punto flotante enteros llegan ya como texto desde
    `sav.filas_de` («1.0» → «1»); acá se normaliza igual por si vienen de
    otro lado.
    """
    codigos = set(codigos) if codigos is not None else None
    distribucion = {}
    for fila in filas or []:
        for codigo, valor in fila.items():
            if codigos is not None and codigo not in codigos:
                continue
            if valor is None or valor != valor:     # noqa: PLR0124 — NaN
                continue
            if isinstance(valor, float) and valor.is_integer():
                valor = int(valor)
            texto = str(valor).strip()
            if not texto:
                continue
            por_valor = distribucion.setdefault(codigo, {})
            por_valor[texto] = por_valor.get(texto, 0) + 1
    return distribucion


def _ordenados(por_valor):
    """`[(valor, filas)]` del más frecuente al menos, con desempate estable."""
    return sorted((por_valor or {}).items(), key=lambda p: (-p[1], p[0]))


# ════════════════════════════════════════════════════════════════════
#  R8.4 · Pares conocidos
# ════════════════════════════════════════════════════════════════════

# (afirmativo, negativo) en forma plana. El orden importa: el afirmativo es
# el que cuenta como «marcado» en una batería.
PARES = (
    ("checked", "unchecked"),
    ("yes", "no"),
    ("si", "no"),
    ("selected", "not selected"),
    ("seleccionado", "no seleccionado"),
    ("seleccionada", "no seleccionada"),
    ("marcado", "no marcado"),
    ("marcada", "no marcada"),
    ("mentioned", "not mentioned"),
    ("mencionado", "no mencionado"),
    ("mencionada", "no mencionada"),
    ("true", "false"),
    ("verdadero", "falso"),
)


def clasificar_par(opciones, valores=None):
    """¿Es una dicotómica de las conocidas? `{marcado, no_marcado, normalizar}`.

    Con etiquetas, por el par de etiquetas. Sin etiquetas, si los valores del
    archivo son solo `0` y `1` (el caso de «`1` / `0` con etiquetas vacías»).
    `normalizar` es la propuesta `{código: "Sí"|"No"}`, o `None` si ya dice
    Sí/No y no hay nada que proponer.
    """
    opciones = {str(k): str(v) for k, v in (opciones or {}).items()}
    if len(opciones) == 2:
        planas = {_plano(v): k for k, v in opciones.items()}
        for afirmativo, negativo in PARES:
            if afirmativo in planas and negativo in planas:
                marcado, no_marcado = planas[afirmativo], planas[negativo]
                propuesta = {marcado: "Sí", no_marcado: "No"}
                ya_esta = (opciones[marcado], opciones[no_marcado]) == ("Sí", "No")
                return {"marcado": marcado, "no_marcado": no_marcado,
                        "normalizar": None if ya_esta else propuesta}
        # «1 = 1» y «0 = 0»: etiquetas que no dicen nada.
        if set(opciones) == {"0", "1"} and all(
                not v.strip() or v.strip() == k for k, v in opciones.items()):
            return {"marcado": "1", "no_marcado": "0",
                    "normalizar": {"1": "Sí", "0": "No"}}
        return None
    if not opciones and valores is not None:
        presentes = {str(v) for v in valores}
        if presentes and presentes <= {"0", "1"}:
            return {"marcado": "1", "no_marcado": "0",
                    "normalizar": {"1": "Sí", "0": "No"}}
    return None


def _limpiar_etiqueta(etiqueta):
    """Espacios y mayúsculas, sin tocar el contenido. `None` si no hay nada
    que cambiar."""
    original = str(etiqueta)
    limpia = " ".join(original.split())
    letras = [c for c in limpia if c.isalpha()]
    if len(letras) > 3 and all(c.isupper() for c in letras):
        limpia = limpia[:1].upper() + limpia[1:].lower()
    return limpia if limpia != original else None


# ════════════════════════════════════════════════════════════════════
#  R8.2 · Texto de pregunta autocontenido
# ════════════════════════════════════════════════════════════════════

# Consignas del instrumento: le dicen al encuestado cómo contestar y no
# aportan significado. Con las erratas de los exports reales («correpondan»).
_CONSIGNAS = [re.compile(p, re.IGNORECASE) for p in (
    r"\(?\s*(?:selecci[oó]ne|marque|elija|indique|se[ñn]ale)\s+"
    r"(?:todos?|todas?)?\s*(?:los|las)?\s*(?:opciones)?\s*(?:que|las que|los que)\s+"
    r"corr?e?s?pond[ae]n?\s*\.?\s*\)?",
    r"\(?\s*(?:marque|selecci[oó]ne|elija)\s+(?:una|un[ao]?\s+sola|s[oó]lo\s+una)"
    r"(?:\s+(?:opci[oó]n|respuesta))?\s*\.?\s*\)?",
    r"\(?\s*puede\s+(?:marcar|seleccionar|elegir)\s+m[aá]s\s+de\s+una"
    r"(?:\s+(?:opci[oó]n|respuesta))?\s*\.?\s*\)?",
    r"\(?\s*(?:respuesta|opci[oó]n)\s+(?:m[uú]ltiple|[uú]nica)\s*\.?\s*\)?",
    r"\(?\s*(?:leer|lea)\s+(?:las\s+)?(?:opciones|alternativas)\s*\.?\s*\)?",
    r"\((?:espont[aá]nea|guiada|estimulada|rm|ru|ru/rm)\)",
)]

# Frases que en una batería ocupan el lugar de la opción: «¿has consumido
# alguno de estos productos?» con la opción «Cigarrillos» es «¿has consumido
# cigarrillos?».
_HUECOS = (
    "alguno de estos productos", "alguna de estas marcas", "alguno de estos",
    "alguna de estas", "algunos de estos", "algunas de estas",
    "alguno de los siguientes", "alguna de las siguientes",
    "algunos de los siguientes", "algunas de las siguientes",
)

# Un prefijo «P5:» o «Q12a:» es numeración del cuestionario, no una opción.
_NUMERACION = re.compile(r"^[A-Za-z]{0,4}\d+[A-Za-z0-9_.]*$")
_OPCION_Y_PREGUNTA = re.compile(r"^\s*([^:¿?]{1,60}?)\s*:\s*(\S.*)$", re.DOTALL)

# Palabras cortas con las que una frase puede terminar sin estar cortada.
_FINALES_CORTOS = {"a", "o", "y", "e", "u", "de", "la", "el", "en", "un",
                   "su", "al", "lo", "se", "mi", "tu", "no", "si", "sí", "ya"}


def partir_opcion(etiqueta):
    """`"Cigarrillos:Pensando…"` → `("Cigarrillos", "Pensando…")`.

    Devuelve `(None, etiqueta)` si no hay prefijo, y `("", resto)` si el
    prefijo es numeración del cuestionario («P5: …»), que se propone quitar
    y no integrar.
    """
    texto = " ".join(str(etiqueta or "").split())
    m = _OPCION_Y_PREGUNTA.match(texto)
    if not m:
        return None, texto
    prefijo, resto = m.group(1).strip(), m.group(2).strip()
    if _NUMERACION.match(prefijo):
        return "", resto
    return prefijo, resto


def quitar_consignas(texto):
    limpio = texto
    for patron in _CONSIGNAS:
        limpio = patron.sub(" ", limpio)
    limpio = re.sub(r"\s+([?.,;:!])", r"\1", limpio)
    limpio = re.sub(r"([?!])\s*[.,;:]+", r"\1", limpio)
    return " ".join(limpio.split()).strip(" .,;:-")


def bajar_enfasis(texto):
    """«el ÚLTIMO mes» → «el último mes». Solo si la frase está en minúscula
    y la palabra en mayúscula es énfasis (cuatro letras o más): una frase
    entera en mayúsculas es otra cosa, y una sigla corta se deja."""
    letras = [c for c in texto if c.isalpha()]
    if not letras or sum(c.islower() for c in letras) < len(letras) / 2:
        return texto
    return re.sub(r"\b([^\W\d_]{4,})\b",
                  lambda m: m.group(1).lower() if m.group(1).isupper() else m.group(1),
                  texto)


def _integrar(pregunta, opcion):
    """Pone la opción en el hueco de la pregunta, o la agrega al final."""
    if not opcion:
        return pregunta
    # Una sigla («TV», «UTE») se deja como está; el resto va en minúscula
    # porque queda en medio de la frase.
    llana = opcion if opcion.isupper() else opcion[:1].lower() + opcion[1:]
    plana = pregunta.lower()
    for hueco in _HUECOS:
        indice = plana.find(hueco)
        if indice >= 0:
            return pregunta[:indice] + llana + pregunta[indice + len(hueco):]
    return f"{pregunta.rstrip()} — {opcion}"


def parece_truncado(etiqueta):
    """Indicios de un label cortado por SPSS: llega al límite, abre una
    pregunta o un paréntesis que no cierra, o termina en un pedazo de
    palabra."""
    texto = str(etiqueta or "").strip()
    if not texto:
        return False
    if len(texto.encode("utf-8")) >= LARGO_TRUNCADO_SPSS:
        return True
    if texto.count("¿") > texto.count("?") or texto.count("(") > texto.count(")"):
        return True
    if texto[-1] in "?.!):»\"'":
        return False
    ultima = re.findall(r"[^\W\d_]+$", texto)
    return bool(ultima) and len(ultima[0]) <= 2 and ultima[0].lower() not in _FINALES_CORTOS


def proponer_texto(etiqueta):
    """R8.2 — una propuesta de texto autocontenido. **No se aplica sola.**

    Devuelve `{texto, cambios, truncado, opcion}`; `texto` es `None` si no
    hay nada que proponer.
    """
    original = " ".join(str(etiqueta or "").split())
    cambios = []
    opcion, pregunta = partir_opcion(original)
    if opcion:
        cambios.append("integra la opción en la pregunta")
    elif opcion == "":
        cambios.append("quita la numeración del cuestionario")
    sin_consigna = quitar_consignas(pregunta)
    if sin_consigna != pregunta:
        cambios.append("quita la consigna del instrumento")
    sin_enfasis = bajar_enfasis(sin_consigna)
    if sin_enfasis != sin_consigna:
        cambios.append("baja las mayúsculas de énfasis")
    propuesto = _integrar(sin_enfasis, opcion) if opcion else sin_enfasis
    propuesto = propuesto[:1].upper() + propuesto[1:] if propuesto else propuesto
    return {
        "texto": propuesto if propuesto and propuesto != original else None,
        "cambios": cambios,
        "truncado": parece_truncado(original),
        "opcion": opcion or None,
    }


# ════════════════════════════════════════════════════════════════════
#  R8.3 · El tipo de variable
# ════════════════════════════════════════════════════════════════════

_ESCALAS = (
    {"muy de acuerdo", "de acuerdo", "ni de acuerdo ni en desacuerdo",
     "en desacuerdo", "muy en desacuerdo", "totalmente de acuerdo",
     "totalmente en desacuerdo", "algo de acuerdo", "algo en desacuerdo"},
    {"nunca", "casi nunca", "a veces", "algunas veces", "casi siempre",
     "siempre", "frecuentemente", "rara vez"},
    {"muy bueno", "bueno", "regular", "malo", "muy malo", "excelente", "pesimo"},
    {"nada", "poco", "algo", "bastante", "mucho", "muy poco"},
    {"muy satisfecho", "satisfecho", "ni satisfecho ni insatisfecho",
     "insatisfecho", "muy insatisfecho", "algo satisfecho", "algo insatisfecho"},
    {"seguro que si", "probablemente si", "no se", "probablemente no",
     "seguro que no", "definitivamente si", "definitivamente no"},
    {"muy importante", "importante", "poco importante", "nada importante",
     "algo importante"},
)

# «P5_otro», «P5Othr», «Q5_7_TEXT»: una abierta que especifica la opción
# «Otro» de una cerrada.
_OTRO = re.compile(
    r"^(?P<base>.+?)[_.]?(?:othr|other|otro|otra|oth|esp|espec|especificar|text)\d*$",
    re.IGNORECASE)


def _es_numero(texto):
    return bool(re.fullmatch(r"-?\d+(?:[.,]\d+)?", str(texto).strip()))


def _cerrada_de(codigo, por_codigo):
    """La cerrada de la que esta variable es el «Otro», si existe."""
    m = _OTRO.match(codigo or "")
    if not m:
        return None
    base = m.group("base").rstrip("_.")
    candidatos = [base]
    # Qualtrics: `Q5_7_TEXT` especifica la opción 7 de `Q5`.
    while re.search(r"[_.]\d+$", base):
        base = re.sub(r"[_.]\d+$", "", base)
        candidatos.append(base)
    for candidato in candidatos:
        otra = por_codigo.get(candidato)
        if otra and otra is not por_codigo.get(codigo) and otra.get("opciones"):
            return otra
    return None


def _etiqueta_de_otro(cerrada):
    for etiqueta in (cerrada.get("opciones") or {}).values():
        if re.match(r"^\s*otr[oa]s?\b", _plano(etiqueta)):
            return str(etiqueta).strip().rstrip(":")
    return "Otro"


def inferir_tipo(pregunta, por_valor, por_codigo):
    """Propuestas de tipo para una variable. Lista de `(tipo, propuesta,
    mensaje)`; vacía si no hay nada que proponer."""
    tipo = pregunta.get("tipo")
    opciones = {str(k): str(v) for k, v in (pregunta.get("opciones") or {}).items()}
    propuestas = []

    # Etiquetas iguales al código (16: "16", 17: "17"): el mapeo no aporta.
    if opciones and len(opciones) >= 3 and all(
            _es_numero(k) and str(v).strip() == k for k, v in opciones.items()):
        if tipo != NUMERICA:
            propuestas.append((NUMERICA, {"tipo": NUMERICA, "opciones": None},
                               "las etiquetas son el mismo número que el código: "
                               "es una numérica, y el mapeo no aporta nada"))
        return propuestas

    # Escala ordinal reconocible.
    if tipo == CERRADA and len(opciones) >= 3:
        planas = {_plano(v) for v in opciones.values()}
        for escala in _ESCALAS:
            if len(planas & escala) >= max(3, int(len(planas) * 0.6)):
                propuestas.append((ESCALA, {"tipo": ESCALA},
                                   "las opciones son una escala ordinal"))
                break

    # «Otro: especificar» asociada a una cerrada.
    if tipo == ABIERTA or not opciones:
        cerrada = _cerrada_de(pregunta.get("codigo"), por_codigo)
        if cerrada:
            propuestas.append((None, {
                "fusionada_con": cerrada["codigo"],
                "texto": cerrada.get("texto") or pregunta.get("texto"),
                "prefijo_respuesta": f"{_etiqueta_de_otro(cerrada)}:",
                "tipo": ABIERTA,
            }, f"es el «Otro: especificar» de {cerrada['codigo']}: fusionada, "
               f"se embebe con la pregunta de la cerrada"))
    return propuestas


# ════════════════════════════════════════════════════════════════════
#  R8.5 · No respuesta
# ════════════════════════════════════════════════════════════════════

def detectar_no_respuesta(pregunta, por_valor, lista):
    """Los códigos de no respuesta de una variable: `[{valor, etiqueta,
    filas}]`. Busca en los valores del archivo y en las etiquetas."""
    opciones = {str(k): str(v) for k, v in (pregunta.get("opciones") or {}).items()}
    textos = {_plano(v) for v in lista if not _es_numero(v)}
    codigos = {str(v).strip() for v in lista if _es_numero(v)}
    con_etiquetas = bool(opciones)

    hallados = {}
    for valor in set(por_valor or {}) | set(opciones):
        etiqueta = opciones.get(valor)
        if etiqueta is not None and _plano(etiqueta) in textos:
            hallados[valor] = etiqueta
        elif etiqueta is None and _plano(valor) in textos:
            hallados[valor] = None          # una abierta que dice «No sabe»
        elif (valor in codigos and con_etiquetas and etiqueta is None
              and pregunta.get("tipo") != NUMERICA):
            hallados[valor] = None          # 99 sin etiqueta en una cerrada
    return [
        {"valor": v, "etiqueta": e, "filas": (por_valor or {}).get(v, 0)}
        for v, e in sorted(hallados.items(), key=lambda p: p[0])
    ]


# ════════════════════════════════════════════════════════════════════
#  R8.6 · PII en texto libre
# ════════════════════════════════════════════════════════════════════

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# Celulares uruguayos (09x xxx xxx, con o sin +598) y fijos (2xxx xxxx,
# 4xxx xxxx). Con separadores opcionales: así se escriben en una abierta.
_TELEFONO = re.compile(
    r"(?<![\d.])(?:\+?598[\s.-]?)?(?:0?9\d|[24]\d)[\s.-]?\d{3}[\s.-]?\d{3}(?![\d])")
_CEDULA = re.compile(r"(?<![\d.])(\d)\.?(\d{3})\.?(\d{3})[-\s]?(\d)(?![\d])")


def _cedula_valida(digitos):
    """Dígito verificador de la cédula uruguaya. Reduce los falsos positivos:
    un número de ocho cifras al azar pasa uno de cada diez veces."""
    cuerpo, verificador = digitos[:-1].rjust(7, "0"), int(digitos[-1])
    suma = sum(int(d) * p for d, p in zip(cuerpo, (2, 9, 8, 7, 6, 3, 4)))
    return (10 - suma % 10) % 10 == verificador


def _enmascarar(texto, inicio, fin):
    """Deja ver la forma, no el dato: «ju•••••@gm•••.com»."""
    dato = texto[inicio:fin]
    visible = 2 if len(dato) > 6 else 1
    oculto = "".join(c if c in "@.-+ " else "•"
                     for c in dato[visible:len(dato) - visible])
    contexto_antes = texto[max(0, inicio - 20):inicio]
    contexto_despues = texto[fin:fin + 20]
    return (f"{'…' if inicio > 20 else ''}{contexto_antes}"
            f"{dato[:visible]}{oculto}{dato[len(dato) - visible:]}"
            f"{contexto_despues}{'…' if fin + 20 < len(texto) else ''}")


def buscar_pii(texto):
    """`[(tipo, inicio, fin)]` de los indicios de PII en un texto."""
    texto = str(texto or "")
    hallazgos = []
    ocupado = []

    def libre(i, f):
        return all(f <= a or i >= b for a, b in ocupado)

    for tipo, patron in (("correo", _EMAIL), ("url", _URL)):
        for m in patron.finditer(texto):
            if libre(m.start(), m.end()):
                hallazgos.append((tipo, m.start(), m.end()))
                ocupado.append((m.start(), m.end()))
    for m in _CEDULA.finditer(texto):
        digitos = "".join(m.groups())
        if libre(m.start(), m.end()) and _cedula_valida(digitos):
            hallazgos.append(("cedula", m.start(), m.end()))
            ocupado.append((m.start(), m.end()))
    for m in _TELEFONO.finditer(texto):
        if libre(m.start(), m.end()):
            hallazgos.append(("telefono", m.start(), m.end()))
            ocupado.append((m.start(), m.end()))
    return sorted(hallazgos, key=lambda h: h[1])


def detectar_pii(por_valor):
    """Indicios de PII en los valores de una variable.

    `None` si no hay ninguno. Si hay, `{casos, por_tipo, ejemplos}`: cuántas
    filas tienen al menos un indicio, de qué tipo, y algunos ejemplos
    **enmascarados** —alcanza con ver la forma para decidir, y el aviso no
    tiene por qué repetir el dato que advierte—.
    """
    casos, por_tipo, ejemplos = 0, {}, []
    for valor, filas in _ordenados(por_valor):
        hallazgos = buscar_pii(valor)
        if not hallazgos:
            continue
        casos += filas
        for tipo, inicio, fin in hallazgos:
            por_tipo[tipo] = por_tipo.get(tipo, 0) + filas
        if len(ejemplos) < EJEMPLOS_DE_PII:
            tipo, inicio, fin = hallazgos[0]
            ejemplos.append({"tipo": tipo, "texto": _enmascarar(valor, inicio, fin)})
    if not casos:
        return None
    return {"casos": casos, "por_tipo": por_tipo, "ejemplos": ejemplos}


def _es_texto_libre(pregunta, por_valor):
    """¿Los valores de esta variable son lo que la gente escribió? Una
    abierta, o cualquier variable cuyos valores no son códigos con etiqueta."""
    if pregunta.get("tipo") == ABIERTA:
        return True
    opciones = pregunta.get("opciones") or {}
    sueltos = [v for v in (por_valor or {}) if v not in opciones]
    return bool(sueltos) and not all(_es_numero(v) for v in sueltos)


# ════════════════════════════════════════════════════════════════════
#  R8.1 · Baterías de respuesta múltiple
# ════════════════════════════════════════════════════════════════════

def _raiz_del_codigo(codigo):
    """`var138O1320` → `var138O`. Lo que comparten las variables de una
    batería: el prefijo antes del número de opción."""
    return re.sub(r"[_.]?\d+$", "", str(codigo or ""))


def detectar_baterias(preguntas, distribucion):
    """Las variables dicotómicas de una misma pregunta, agrupadas.

    Las tres señales del requisito: el **prefijo del código**, el **texto
    después de los dos puntos** y el **mismo par de opciones**. Con el texto
    partido (`Opción:Pregunta`) se agrupa por pregunta y par; sin él, por
    raíz del código y par. Un grupo de una sola variable no es una batería.
    """
    grupos = {}
    for p in preguntas:
        if p.get("tipo") == ABIERTA:
            continue
        por_valor = distribucion.get(p.get("codigo")) or {}
        par = clasificar_par(p.get("opciones"), por_valor.keys())
        if not par:
            continue
        opcion, pregunta = partir_opcion(p.get("texto_original") or p.get("texto"))
        firma = (par["marcado"], par["no_marcado"])
        clave = (("t", _plano(pregunta), firma) if opcion
                 else ("c", _raiz_del_codigo(p.get("codigo")), firma))
        grupos.setdefault(clave, []).append((p, opcion, pregunta, par))

    baterias = []
    for indice, (clave, miembros) in enumerate(
            sorted(grupos.items(), key=lambda g: g[1][0][0].get("orden") or 0)):
        if len(miembros) < 2:
            continue
        marcadas = no_marcadas = 0
        for p, _o, _q, par in miembros:
            por_valor = distribucion.get(p.get("codigo")) or {}
            for valor, filas in por_valor.items():
                if valor == par["marcado"]:
                    marcadas += filas
                elif valor == par["no_marcado"]:
                    no_marcadas += filas
        p0, _o0, pregunta0, par0 = miembros[0]
        baterias.append({
            "clave": f"bateria-{indice + 1}",
            "pregunta": quitar_consignas(pregunta0) if clave[0] == "t"
                        else (p0.get("texto") or ""),
            "variables": [
                {"codigo": p.get("codigo"),
                 "opcion": o or (p.get("texto") or p.get("codigo"))}
                for p, o, _q, _par in miembros],
            "valores_marcados": [par0["marcado"]],
            "respuestas_marcadas": marcadas,
            "respuestas_no_marcadas": no_marcadas,
        })
    return baterias


# ════════════════════════════════════════════════════════════════════
#  R8.8 · Vista previa del texto embebido
# ════════════════════════════════════════════════════════════════════

def vista_previa(pregunta, por_valor, ejemplos=EJEMPLOS_DE_VISTA_PREVIA):
    """Cómo va a quedar el texto embebido de una variable, con sus valores
    reales.

    Pasa por `ingesta.respuesta_de`, que es lo que corre al ingestar: lo que
    se ve acá es lo que se escribe. Los ejemplos incluyen **el valor más
    frecuente y el menos frecuente** de los que generan respuesta (R8.8), y
    uno descartado si lo hay, para que se vea también qué queda afuera.
    """
    pregunta = dict(pregunta)
    pregunta.setdefault("texto", pregunta.get("codigo") or "")
    filas_total, genera, descartadas = 0, 0, {}
    generan, descartados = [], []
    for valor, filas in _ordenados(por_valor):
        filas_total += filas
        etiqueta, texto, motivo = ingesta.respuesta_de(pregunta, valor)
        if motivo:
            if motivo != ingesta.VACIA:
                descartadas[motivo] = descartadas.get(motivo, 0) + filas
                descartados.append({"valor": valor, "filas": filas,
                                    "motivo": motivo})
            continue
        genera += filas
        generan.append({"valor": valor, "filas": filas, "etiqueta": etiqueta,
                        "texto_embebido": texto})

    elegidos = []
    if generan:
        elegidos.append(generan[0])                     # el más frecuente
        if len(generan) > 1:
            elegidos.append(generan[-1])                # el menos frecuente
        if len(generan) > 2 and ejemplos > 2:
            elegidos.insert(1, generan[len(generan) // 2])
    if descartados:
        elegidos.append(descartados[0])
    return {
        "ejemplos": elegidos[:max(ejemplos, 2) + 1],
        "respuestas": filas_total,
        "genera": genera,
        "descartadas": descartadas,
        "valores_distintos": len(por_valor or {}),
    }


# ════════════════════════════════════════════════════════════════════
#  El diagnóstico entero
# ════════════════════════════════════════════════════════════════════

def _hallazgo(tipo, severidad, titulo, mensaje, variables, cuantos=0,
              acciones=None, detalle=None):
    return {
        "tipo": tipo, "severidad": severidad, "titulo": titulo,
        "mensaje": mensaje, "variables": list(variables), "cuantos": cuantos,
        "acciones": acciones or [], "detalle": detalle,
    }


def diagnosticar(preguntas, distribucion, filas_total=None,
                 valores_no_respuesta=None):
    """Todo lo que la Fase 8 detecta sobre un conjunto de variables.

    `preguntas`: las que van al store semántico (`codigo`, `texto`, `tipo`,
    `opciones` y, si las hay, las decisiones de normalización ya tomadas).
    `distribucion`: `{codigo: {valor: filas}}`.

    Devuelve `{hallazgos, variables, baterias, valores_no_respuesta,
    resumen}`. Cada hallazgo trae **acciones**: propuestas por variable que
    la pantalla aplica si el analista las elige. Ninguna está aplicada.
    """
    preguntas = [p for p in (preguntas or []) if p.get("codigo")]
    distribucion = distribucion or {}
    lista = lista_no_respuesta(valores_no_respuesta)
    por_codigo = {p["codigo"]: p for p in preguntas}
    hallazgos, variables = [], {}

    # ── Por variable ──
    textos, truncados, sin_traducir, pares, no_respuesta = [], [], [], [], []
    tipos, fusiones, piis, vacias, constantes, etiquetas_sucias = [], [], [], [], [], []
    for p in preguntas:
        codigo = p["codigo"]
        por_valor = distribucion.get(codigo) or {}
        respuestas = sum(por_valor.values())
        previa = vista_previa(p, por_valor, ejemplos=2)
        info = {
            "respuestas": respuestas,
            "genera": previa["genera"],
            "descartadas": previa["descartadas"],
            "valores_distintos": len(por_valor),
            "avisos": [],
        }
        variables[codigo] = info

        # R8.7 — sin respuestas.
        if respuestas == 0:
            vacias.append(codigo)
            continue

        # R8.2 — texto.
        texto = proponer_texto(p.get("texto_original") or p.get("texto"))
        if texto["texto"] and texto["texto"] != p.get("texto"):
            info["texto_propuesto"] = texto["texto"]
            info["cambios_de_texto"] = texto["cambios"]
            textos.append((codigo, texto))
        if texto["truncado"] or parece_truncado(p.get("texto")):
            info["truncado"] = True
            truncados.append(codigo)

        opciones = {str(k): str(v) for k, v in (p.get("opciones") or {}).items()}
        tipo = p.get("tipo")

        # R8.3 — tipo.
        for tipo_nuevo, propuesta, mensaje in inferir_tipo(p, por_valor, por_codigo):
            if propuesta.get("fusionada_con"):
                fusiones.append((codigo, propuesta, mensaje))
            else:
                tipos.append((codigo, propuesta, mensaje))
            info["avisos"].append(mensaje)

        # R8.4 — pares conocidos y etiquetas.
        par = clasificar_par(opciones, por_valor.keys()) if tipo != ABIERTA else None
        if par and par["normalizar"]:
            pares.append((codigo, par))
        limpias = {k: _limpiar_etiqueta(v) for k, v in opciones.items()}
        limpias = {k: v for k, v in limpias.items() if v}
        if limpias:
            etiquetas_sucias.append((codigo, {**opciones, **limpias}))

        # R8.4 — sin traducir. Solo en cerradas y escalas: en una numérica el
        # número *es* la respuesta, y en una abierta no hay códigos.
        if tipo in (CERRADA, ESCALA):
            ruido = {d["valor"] for d in detectar_no_respuesta(p, por_valor, lista)}
            if opciones:
                faltan = {v: n for v, n in por_valor.items()
                          if v not in opciones and v not in ruido}
                iguales = {k for k, v in opciones.items()
                           if v.strip() == k and _es_numero(k)}
                faltan.update({k: por_valor.get(k, 0) for k in iguales
                               if len(iguales) < len(opciones)})
            elif not par and all(_es_numero(v) for v in por_valor):
                faltan = dict(por_valor)
            else:
                faltan = {}
            if faltan:
                sin_traducir.append((codigo, faltan))
                info["sin_traducir"] = sorted(faltan)

        # R8.5 — no respuesta.
        detectados = detectar_no_respuesta(p, por_valor, lista)
        if detectados:
            no_respuesta.append((codigo, detectados))
            info["no_respuesta"] = detectados

        # R8.6 — PII en texto libre.
        if _es_texto_libre(p, por_valor):
            pii = detectar_pii({v: n for v, n in por_valor.items()
                                if v not in opciones})
            if pii:
                info["pii"] = pii
                piis.append((codigo, pii))

        # R8.7 — casi constante.
        if respuestas >= RESPUESTAS_PARA_CONSTANTE and len(por_valor) >= 1:
            mayor = _ordenados(por_valor)[0]
            if mayor[1] / respuestas >= PROPORCION_CASI_CONSTANTE:
                constantes.append((codigo, mayor, respuestas))

    # ── R8.1 · Baterías ──
    baterias = detectar_baterias(preguntas, distribucion)
    en_bateria = {v["codigo"] for b in baterias for v in b["variables"]}

    # ════ Lo que rompe ════
    for codigo, pii in piis:
        tipos_vistos = ", ".join(sorted(pii["por_tipo"]))
        hallazgos.append(_hallazgo(
            "pii_en_texto_libre", ROMPE,
            f"Posibles datos personales en {codigo}",
            f"{pii['casos']} respuesta(s) de «{codigo}» tienen algo con forma "
            f"de {tipos_vistos}. Es un indicio, no una certeza: la "
            f"detección es por patrones y tiene falsos positivos (un número "
            f"largo que no es un teléfono) y negativos (un nombre suelto no se "
            f"detecta). El store semántico está pensado para no tener datos "
            f"identificatorios: decidí si esta variable entra.",
            [codigo], pii["casos"],
            acciones=[
                {"etiqueta": "Excluir la variable",
                 "propuesta": {codigo: {"incluir": False}}},
                {"etiqueta": "Ingestarla igual, a conciencia",
                 "propuesta": {codigo: {"pii_aceptada": True}}},
            ],
            detalle=pii))

    for codigo, faltan in sin_traducir:
        cuantos = sum(faltan.values())
        muestra = ", ".join(sorted(faltan)[:6])
        hallazgos.append(_hallazgo(
            "sin_traducir", ROMPE,
            f"Códigos sin traducir en {codigo}",
            f"{cuantos} respuesta(s) de «{codigo}» tienen un código sin "
            f"etiqueta ({muestra}): se van a embeber como un número sin "
            f"significado —«¿Qué marca fumás? → 11427»—, que está en la base, "
            f"cuenta como respuesta y no sirve para buscar. Completá las "
            f"etiquetas o excluí esos valores.",
            [codigo], cuantos, detalle={"valores": faltan}))

    if truncados:
        hallazgos.append(_hallazgo(
            "texto_truncado", ROMPE,
            "Textos de pregunta que parecen cortados",
            f"{len(truncados)} variable(s) tienen un texto que parece truncado "
            f"por SPSS (llega al límite, abre una pregunta que no cierra o "
            f"termina a mitad de palabra). El texto es la mitad de lo que se "
            f"embebe: completalo a mano.",
            truncados, len(truncados)))

    # ════ Lo que mejora ════
    for bateria in baterias:
        codigos = [v["codigo"] for v in bateria["variables"]]
        hallazgos.append(_hallazgo(
            "bateria", MEJORA,
            f"Batería de {len(codigos)} opciones: {bateria['pregunta'][:80]}",
            f"Estas variables son las opciones de una misma pregunta de "
            f"respuesta múltiple. Hoy se ingestarían también las "
            f"{bateria['respuestas_no_marcadas']} respuesta(s) no marcadas, "
            f"que dicen «no» sobre cosas que la persona simplemente no eligió: "
            f"no aportan, compiten en el ranking y se pagan como embeddings. "
            f"Ingestando solo lo marcado, quien no marcó ninguna no genera "
            f"respuestas para esta pregunta. Ojo: deja de poder buscarse "
            f"«quiénes no consumen» por la vía semántica.",
            codigos, bateria["respuestas_no_marcadas"],
            acciones=[{
                "etiqueta": "Ingestar solo lo marcado",
                "propuesta": {c: {"solo_marcadas": True,
                                  "valores_marcados": bateria["valores_marcados"],
                                  "bateria": bateria["clave"]}
                              for c in codigos},
            }],
            detalle=bateria))

    for codigo, detectados in no_respuesta:
        cuantos = sum(d["filas"] for d in detectados)
        valores = [d["valor"] for d in detectados]
        sin_etiqueta = {d["valor"]: ETIQUETA_NO_RESPUESTA
                        for d in detectados if d["etiqueta"] is None
                        and _es_numero(d["valor"])}
        acciones = [{"etiqueta": "No ingestarlos",
                     "propuesta": {codigo: {"excluir_valores": valores}}}]
        if sin_etiqueta:
            opciones = dict(por_codigo[codigo].get("opciones") or {})
            acciones.append({
                "etiqueta": "Ingestarlos con su etiqueta en texto",
                "propuesta": {codigo: {"opciones": {**opciones, **sin_etiqueta}}}})
        hallazgos.append(_hallazgo(
            "no_respuesta", MEJORA,
            f"Valores de no respuesta en {codigo}",
            f"{cuantos} respuesta(s) de «{codigo}» son de no respuesta "
            f"({', '.join(d['etiqueta'] or d['valor'] for d in detectados)}). "
            f"Embebidas compiten en el ranking con respuestas reales sobre el "
            f"mismo tema y no dicen nada de la persona. A veces sí interesa "
            f"saber quién no contestó: por eso es una opción.",
            [codigo], cuantos, acciones=acciones, detalle={"valores": detectados}))

    for codigo, texto in textos:
        hallazgos.append(_hallazgo(
            "texto_propuesto", MEJORA,
            f"Texto más claro para {codigo}",
            f"Propuesta: «{texto['texto']}» ({'; '.join(texto['cambios'])}). "
            f"El texto de la pregunta es la mitad de lo que se embebe. Revisá "
            f"que no cambie el sentido: el original se conserva.",
            [codigo], 1,
            acciones=[{"etiqueta": "Usar el texto propuesto",
                       "propuesta": {codigo: {"texto": texto["texto"]}}}]))

    for codigo, par in pares:
        opciones = dict(por_codigo[codigo].get("opciones") or {})
        hallazgos.append(_hallazgo(
            "par_conocido", MEJORA,
            f"Etiquetas sin sentido en español en {codigo}",
            f"«{codigo}» es una dicotómica cuyas etiquetas no dicen lo que "
            f"representan ({', '.join(sorted(opciones.values())) or '0 y 1 sin etiqueta'}). "
            f"Se propone normalizarlas a Sí / No.",
            [codigo], sum((distribucion.get(codigo) or {}).values()),
            acciones=[{"etiqueta": "Normalizar a Sí / No",
                       "propuesta": {codigo: {"opciones": {**opciones,
                                                           **par["normalizar"]}}}}]))

    for codigo, opciones in etiquetas_sucias:
        hallazgos.append(_hallazgo(
            "etiquetas_inconsistentes", MEJORA,
            f"Espacios o mayúsculas inconsistentes en {codigo}",
            f"Las etiquetas de «{codigo}» tienen espacios de más o están en "
            f"mayúsculas. Se propone normalizarlas sin cambiar su contenido.",
            [codigo], 0,
            acciones=[{"etiqueta": "Normalizar las etiquetas",
                       "propuesta": {codigo: {"opciones": opciones}}}]))

    for codigo, propuesta, mensaje in tipos:
        hallazgos.append(_hallazgo(
            "tipo", MEJORA, f"Tipo de {codigo}: {propuesta['tipo']}",
            f"«{codigo}»: {mensaje}.", [codigo], 0,
            acciones=[{"etiqueta": f"Tratarla como {propuesta['tipo']}",
                       "propuesta": {codigo: propuesta}}]))

    for codigo, propuesta, mensaje in fusiones:
        hallazgos.append(_hallazgo(
            "fusion_otro", MEJORA, f"«Otro» de {propuesta['fusionada_con']}: {codigo}",
            f"«{codigo}» {mensaje}: «{propuesta['texto']} → "
            f"{propuesta['prefijo_respuesta']} …» dice más que una respuesta "
            f"abierta sin contexto.",
            [codigo], sum((distribucion.get(codigo) or {}).values()),
            acciones=[{"etiqueta": "Fusionar con su cerrada",
                       "propuesta": {codigo: propuesta}}]))

    if vacias:
        hallazgos.append(_hallazgo(
            "sin_respuestas", MEJORA, "Variables sin ninguna respuesta",
            f"{len(vacias)} variable(s) no tienen ni una respuesta en el "
            f"archivo: no aportan nada. Se propone excluirlas.",
            vacias, len(vacias),
            acciones=[{"etiqueta": "Excluirlas",
                       "propuesta": {c: {"incluir": False} for c in vacias}}]))

    # ════ Información ════
    for codigo, (valor, filas), respuestas in constantes:
        if codigo in en_bateria:
            continue    # en una batería es lo esperable: casi todos «no»
        etiqueta = (por_codigo[codigo].get("opciones") or {}).get(valor) or valor
        hallazgos.append(_hallazgo(
            "casi_constante", INFO, f"{codigo} es casi siempre igual",
            f"El {round(100 * filas / respuestas)}% de las respuestas de "
            f"«{codigo}» son «{etiqueta}»: aporta poco a una búsqueda y "
            f"mucho volumen ({respuestas} respuestas).",
            [codigo], respuestas))

    hallazgos.sort(key=lambda h: (_ORDEN[h["severidad"]], -h["cuantos"]))
    for info in variables.values():
        info["avisos"] = list(dict.fromkeys(info["avisos"]))
    return {
        "hallazgos": hallazgos,
        "variables": variables,
        "baterias": baterias,
        "valores_no_respuesta": lista,
        "filas": filas_total,
        "resumen": {s: sum(1 for h in hallazgos if h["severidad"] == s)
                    for s in (ROMPE, MEJORA, INFO)},
    }


def vistas_previas(preguntas, distribucion):
    """`{codigo: vista_previa}` de un conjunto de preguntas."""
    return {
        p["codigo"]: vista_previa(p, (distribucion or {}).get(p["codigo"]) or {})
        for p in (preguntas or []) if p.get("codigo")
    }
