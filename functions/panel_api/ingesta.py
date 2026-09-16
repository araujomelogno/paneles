"""Pipeline de ingesta: Excel ancho → formato largo → embeddings → store semántico.

Mecánica (PRD del módulo de consulta semántica):

1. El export de campo viene **ancho**: una fila por individuo, una columna
   por pregunta, y el encabezado de cada columna es el `codigo` de la
   pregunta.
2. Se despivota a **largo**: una fila por (individuo, pregunta).
3. En las cerradas, el valor suele ser un código; se resuelve a su etiqueta
   con el mapa de `opciones` antes de embeber.
4. El texto a vectorizar se compone como `"pregunta → respuesta"`.
5. Se embebe en lotes y se hace upsert; re-ingestar el mismo estudio no
   duplica (unicidad por individuo+pregunta).

El puente entre stores: la columna de identidad del Excel trae el id que usó
la plataforma de campo (Dooblo, Alchemer). Ese id se traduce a `id_persona`
**en el store de bóveda** contra `alias_origen`. Lo único que cruza al store semántico es
el `id_persona`. La PII se queda de este lado.
"""

import re

from . import consentimiento, db, embeddings as mod_embeddings, semantica
from .errores import DatosInvalidos


def _etiqueta(valor, opciones):
    """Resuelve un código a su etiqueta. Si no es un código conocido, el
    valor ya es texto (abierta, numérica, o etiqueta directa)."""
    if opciones and valor is not None:
        clave = str(valor).strip()
        if clave in opciones:
            return str(opciones[clave])
    return None if valor is None else str(valor).strip()


def componer_texto(texto_pregunta, etiqueta_respuesta):
    """El string exacto que se vectoriza. Se guarda tal cual en
    `texto_embebido` para poder auditar por qué algo matcheó."""
    return f"{texto_pregunta.strip()} → {etiqueta_respuesta.strip()}"


def despivotar(filas, preguntas, columna_id):
    """Ancho → largo. Devuelve `[(id_en_origen, codigo, etiqueta, texto)]`.

    Las celdas vacías se descartan: una no-respuesta no es una respuesta y
    no debe generar un embedding.
    """
    por_codigo = {p["codigo"]: p for p in preguntas}
    largo = []
    for fila in filas:
        id_origen = str(fila.get(columna_id) or "").strip()
        if not id_origen:
            continue
        for codigo, valor in fila.items():
            if codigo == columna_id or codigo not in por_codigo:
                continue
            if valor is None or str(valor).strip() == "":
                continue
            pregunta = por_codigo[codigo]
            etiqueta = _etiqueta(valor, pregunta.get("opciones"))
            if not etiqueta:
                continue
            largo.append(
                (
                    id_origen,
                    codigo,
                    etiqueta,
                    componer_texto(pregunta["texto"], etiqueta),
                )
            )
    return largo


# ── R3.12 · Qué tipo de identificador trae la columna ────────────────
#
# El mapeo se apoyaba siempre en `alias_origen`, o sea en el id que la
# plataforma de campo le puso al respondente. Eso asume que ese id es estable
# por persona entre estudios, y no lo es: cada encuesta genera ids nuevos para
# el mismo individuo. Al ingestar un estudio nuevo no matcheaba ninguna fila y
# todas caían en `sin_mapear`, con la ingesta en cero y nada roto.
#
# La salida es que el identificador del sistema viaje **hacia** el campo en
# vez de adivinarlo a la vuelta: la muestra se exporta con `id_persona`, se
# precarga en el instrumento y vuelve en el archivo.
POR_ID_PERSONA = "id_persona"
POR_ALIAS = "alias"
POR_DOCUMENTO = "documento"
POR_EMAIL = "email"
TIPOS_DE_IDENTIFICADOR = (POR_ID_PERSONA, POR_ALIAS, POR_DOCUMENTO, POR_EMAIL)

# Por qué una fila no resolvió. Las tres se arreglan distinto, y una lista sin
# motivo obliga a adivinar cuál de las tres pasó.
FORMATO_INVALIDO = "formato_invalido"
NO_ENCONTRADO = "no_encontrado"
SIN_ALIAS = "sin_alias_para_ese_origen"

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def resolver_identificadores(conn_boveda, tipo, valores, origen=None):
    """Traduce lo que trae la columna de identidad a `id_persona`.

    Devuelve `(mapa, motivos)`, donde `motivos` dice por qué cada valor que no
    resolvió no resolvió.
    """
    if tipo not in TIPOS_DE_IDENTIFICADOR:
        raise DatosInvalidos(
            f"«{tipo}» no es un tipo de identificador conocido.",
            {"tipos_validos": list(TIPOS_DE_IDENTIFICADOR)},
        )
    valores = [str(v).strip() for v in dict.fromkeys(valores) if str(v).strip()]
    if not valores:
        return {}, {}

    if tipo == POR_ALIAS:
        if not origen:
            raise DatosInvalidos(
                "Para mapear por alias hace falta declarar el origen (la "
                "plataforma de campo que emitió esos ids)."
            )
        mapa, _ = mapear_a_id_persona(conn_boveda, origen, valores)
        return mapa, {v: SIN_ALIAS for v in valores if v not in mapa}

    if tipo == POR_ID_PERSONA:
        # Un uuid mal formado no se puede ni consultar: Postgres aborta la
        # transacción entera con un error de tipo. Se filtran antes, y se
        # informan aparte de los que sí son uuid pero no existen —un typo y
        # una persona borrada por baja se arreglan de forma distinta—.
        motivos = {v: FORMATO_INVALIDO for v in valores if not _UUID.match(v)}
        candidatos = [v for v in valores if v not in motivos]
        existentes = {
            str(f["id_persona"]).lower()
            for f in db.todas(
                conn_boveda,
                "select id_persona from persona where id_persona = any(%s::uuid[])",
                (candidatos,),
            )
        } if candidatos else set()
        mapa = {v: v.lower() for v in candidatos if v.lower() in existentes}
        motivos.update({v: NO_ENCONTRADO for v in candidatos if v not in mapa})
        return mapa, motivos

    columna = "documento" if tipo == POR_DOCUMENTO else "email"
    comparacion = (
        "documento = any(%s)" if tipo == POR_DOCUMENTO
        else "lower(email) = any(%s)"
    )
    buscados = valores if tipo == POR_DOCUMENTO else [v.lower() for v in valores]
    filas = db.todas(
        conn_boveda,
        f"select {columna}, id_persona from persona where {comparacion}",
        (buscados,),
    )
    encontrados = {
        (f[columna] if tipo == POR_DOCUMENTO else (f[columna] or "").lower()):
            str(f["id_persona"])
        for f in filas
    }
    mapa, motivos = {}, {}
    for valor in valores:
        clave = valor if tipo == POR_DOCUMENTO else valor.lower()
        if clave in encontrados:
            mapa[valor] = encontrados[clave]
        else:
            motivos[valor] = NO_ENCONTRADO
    return mapa, motivos


def mapear_a_id_persona(conn_boveda, origen, ids_en_origen):
    """Traduce ids de la plataforma de campo a `id_persona`, contra la
    bóveda. Devuelve `(mapa, sin_mapear)`."""
    ids = [str(i) for i in dict.fromkeys(ids_en_origen)]
    if not ids:
        return {}, []
    filas = db.todas(
        conn_boveda,
        """
        select id_en_origen, id_persona
          from alias_origen
         where origen = %s and id_en_origen = any(%s)
        """,
        (origen, ids),
    )
    mapa = {f["id_en_origen"]: str(f["id_persona"]) for f in filas}
    return mapa, [i for i in ids if i not in mapa]


def ingestar(
    conn_boveda,
    conn_semantica,
    encuesta,
    preguntas,
    filas,
    columna_id="id_en_origen",
    origen=None,
    proveedor=None,
    mapa_personas=None,
    motivos_sin_mapear=None,
):
    """Corre la ingesta de un estudio. `encuesta` es el dict del store de bóveda
    (necesita `ref_estudio`, `nombre`, `fecha_campo`).

    Aplica el gate de `uso_semantico`: quien no lo tenga vigente queda fuera
    de la ingesta, y se informa en el resultado. No es un error del lote.
    """
    proveedor = proveedor or mod_embeddings.crear()
    ref_estudio = str(encuesta["ref_estudio"])

    largo = despivotar(filas, preguntas, columna_id)
    if not largo:
        return {
            "ref_estudio": ref_estudio,
            "respuestas_escritas": 0,
            "personas": 0,
            "sin_mapear": [],
            "sin_mapear_detalle": [],
            "sin_consentimiento": [],
            "ids_persona_ingestados": [],
            "aviso": "El archivo no tenía celdas con respuesta.",
        }

    ids_origen = list(dict.fromkeys(r[0] for r in largo))

    # ── Traducción a id_persona: pasa enteramente en el store de bóveda ──
    if mapa_personas:
        mapa = {str(k): str(v) for k, v in mapa_personas.items()}
        sin_mapear = [i for i in ids_origen if i not in mapa]
    elif origen:
        mapa, sin_mapear = mapear_a_id_persona(conn_boveda, origen, ids_origen)
        motivos_sin_mapear = {i: SIN_ALIAS for i in sin_mapear}
    else:
        raise DatosInvalidos(
            "Para ingestar hace falta `origen` (para resolver los alias contra "
            "la bóveda) o un `mapa_personas` explícito."
        )

    # R3.12.d — el motivo por fila. Las tres causas —el valor no es un uuid,
    # el uuid no existe, esa plataforma no tiene ese alias— se arreglan de
    # forma distinta, y una lista sin motivo obliga a adivinar cuál pasó.
    motivos_sin_mapear = motivos_sin_mapear or {}
    detalle_sin_mapear = [
        {"id_en_origen": i, "motivo": motivos_sin_mapear.get(i, NO_ENCONTRADO)}
        for i in sin_mapear
    ]

    # ── Gate de consentimiento (R1.3 / regla de finalidad) ──
    habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        conn_boveda, set(mapa.values()), consentimiento.SEMANTICO
    )
    habilitadas = set(habilitadas)

    largo = [
        r for r in largo if r[0] in mapa and mapa[r[0]] in habilitadas
    ]
    if not largo:
        return {
            "ref_estudio": ref_estudio,
            "respuestas_escritas": 0,
            "personas": 0,
            "sin_mapear": sin_mapear,
            "sin_mapear_detalle": detalle_sin_mapear,
            "sin_consentimiento": bloqueadas,
            "ids_persona_ingestados": [],
            "aviso": "Ninguna respuesta quedó habilitada para ingestar.",
        }

    # ── A partir de acá se escribe del lado semántico: solo id_persona ──
    cuestionario_id = semantica.asegurar_cuestionario(
        conn_semantica,
        ref_estudio,
        encuesta["nombre"],
        encuesta.get("fecha_campo"),
        {"panel_id": encuesta.get("panel_id")},
    )
    id_por_codigo = semantica.upsert_preguntas(conn_semantica, cuestionario_id, preguntas)
    ids_persona = [mapa[r[0]] for r in largo]
    individuo_por_persona = semantica.asegurar_individuos(conn_semantica, ids_persona)

    # ── Qué hace falta embeber (P1: saltear lo que no cambió) ──
    # Re-ingestar una ola es normal (una corrección de campo, una pregunta
    # que faltaba). Lo que llega con el mismo texto ya tiene su vector, y el
    # mismo texto con el mismo modelo da el mismo vector: se saltea. Es la
    # parte cara del pipeline.
    ya_ingestado = semantica.hashes_de_estudio(conn_semantica, ref_estudio)

    respuestas, a_embeber = [], []
    for id_origen, codigo, etiqueta, texto in largo:
        clave = (individuo_por_persona[mapa[id_origen]], id_por_codigo[codigo])
        huella = semantica.hash_texto(texto)
        fila = {
            "individuo_id": clave[0],
            "pregunta_id": clave[1],
            "valor_texto": etiqueta,
            "texto_embebido": texto,
            "hash_texto": huella,
            "embedding": None,
        }
        respuestas.append(fila)
        if ya_ingestado.get(clave) != huella:
            a_embeber.append(fila)

    if a_embeber:
        vectores = proveedor.embeber_en_lotes([f["texto_embebido"] for f in a_embeber])
        for fila, vector in zip(a_embeber, vectores):
            fila["embedding"] = vector

    escritas = semantica.upsert_respuestas(conn_semantica, respuestas)

    return {
        "ref_estudio": ref_estudio,
        "respuestas_escritas": escritas,
        "personas": len(individuo_por_persona),
        "preguntas": len(id_por_codigo),
        "embebidas": len(a_embeber),
        "reutilizadas": len(respuestas) - len(a_embeber),
        "sin_mapear": sin_mapear,
        "sin_mapear_detalle": detalle_sin_mapear,
        "sin_consentimiento": bloqueadas,
        # Quiénes quedaron efectivamente ingestados —mapeados y con
        # `uso_semantico` vigente—. Lo necesita la bóveda para incorporarlos
        # al panel y registrarles la participación: son `id_persona` y nada
        # más, así que no es PII cruzando de vuelta.
        "ids_persona_ingestados": list(dict.fromkeys(ids_persona)),
    }


def leer_excel_ancho(ruta_o_stream, hoja=None):
    """Lee un export ancho (.xlsx) a `[{encabezado: valor}]`.

    La primera fila son los encabezados = `codigo` de cada pregunta.
    """
    import openpyxl  # import diferido: solo hace falta al cargar archivos

    libro = openpyxl.load_workbook(ruta_o_stream, read_only=True, data_only=True)
    hoja_activa = libro[hoja] if hoja else libro.worksheets[0]
    iterador = hoja_activa.iter_rows(values_only=True)
    encabezados = [str(c).strip() if c is not None else "" for c in next(iterador)]
    filas = []
    for valores in iterador:
        fila = {
            encabezado: valor
            for encabezado, valor in zip(encabezados, valores)
            if encabezado
        }
        if any(v is not None and str(v).strip() != "" for v in fila.values()):
            filas.append(fila)
    return filas
