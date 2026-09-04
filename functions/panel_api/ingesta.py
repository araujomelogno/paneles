"""Pipeline de ingesta: Excel ancho → formato largo → embeddings → remoto.

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
**en el store local** contra `alias_origen`. Lo único que cruza al remoto es
el `id_persona`. La PII se queda de este lado.
"""

from . import consentimiento, db, embeddings as mod_embeddings, remoto
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


def mapear_a_id_persona(conn_local, origen, ids_en_origen):
    """Traduce ids de la plataforma de campo a `id_persona`, contra la
    bóveda. Devuelve `(mapa, sin_mapear)`."""
    ids = [str(i) for i in dict.fromkeys(ids_en_origen)]
    if not ids:
        return {}, []
    filas = db.todas(
        conn_local,
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
    conn_local,
    conn_remoto,
    encuesta,
    preguntas,
    filas,
    columna_id="id_en_origen",
    origen=None,
    proveedor=None,
    mapa_personas=None,
):
    """Corre la ingesta de un estudio. `encuesta` es el dict del store local
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
            "sin_consentimiento": [],
            "aviso": "El archivo no tenía celdas con respuesta.",
        }

    ids_origen = list(dict.fromkeys(r[0] for r in largo))

    # ── Traducción a id_persona: pasa enteramente en el store local ──
    if mapa_personas:
        mapa = {str(k): str(v) for k, v in mapa_personas.items()}
        sin_mapear = [i for i in ids_origen if i not in mapa]
    elif origen:
        mapa, sin_mapear = mapear_a_id_persona(conn_local, origen, ids_origen)
    else:
        raise DatosInvalidos(
            "Para ingestar hace falta `origen` (para resolver los alias contra "
            "la bóveda) o un `mapa_personas` explícito."
        )

    # ── Gate de consentimiento (R1.3 / regla de finalidad) ──
    habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        conn_local, set(mapa.values()), consentimiento.SEMANTICO
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
            "sin_consentimiento": bloqueadas,
            "aviso": "Ninguna respuesta quedó habilitada para ingestar.",
        }

    # ── A partir de acá se escribe del lado remoto: solo id_persona ──
    cuestionario_id = remoto.asegurar_cuestionario(
        conn_remoto,
        ref_estudio,
        encuesta["nombre"],
        encuesta.get("fecha_campo"),
        {"panel_id": encuesta.get("panel_id")},
    )
    id_por_codigo = remoto.upsert_preguntas(conn_remoto, cuestionario_id, preguntas)
    ids_persona = [mapa[r[0]] for r in largo]
    individuo_por_persona = remoto.asegurar_individuos(conn_remoto, ids_persona)

    textos = [r[3] for r in largo]
    vectores = proveedor.embeber_en_lotes(textos)

    respuestas = [
        {
            "individuo_id": individuo_por_persona[mapa[id_origen]],
            "pregunta_id": id_por_codigo[codigo],
            "valor_texto": etiqueta,
            "texto_embebido": texto,
            "embedding": vector,
        }
        for (id_origen, codigo, etiqueta, texto), vector in zip(largo, vectores)
    ]
    escritas = remoto.upsert_respuestas(conn_remoto, respuestas)

    return {
        "ref_estudio": ref_estudio,
        "respuestas_escritas": escritas,
        "personas": len(individuo_por_persona),
        "preguntas": len(id_por_codigo),
        "sin_mapear": sin_mapear,
        "sin_consentimiento": bloqueadas,
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
