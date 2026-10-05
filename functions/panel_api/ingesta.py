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

from . import consentimiento, db, embeddings as mod_embeddings, esquema, semantica
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


# ── Fase 8 · Qué se embebe de cada celda ────────────────────────────
#
# Por qué una celda no genera respuesta. `vacia` existía desde siempre; las
# otras dos son decisiones de normalización que el analista confirmó (R8.1 y
# R8.5) y se cuentan aparte, porque el resultado tiene que poder decir
# cuántas respuestas se descartaron por cada motivo.
VACIA = "vacia"
NO_MARCADA = "no_marcada"
NO_RESPUESTA = "no_respuesta"


def _comparable(valor):
    """Cómo se comparan los códigos y etiquetas de las decisiones: sin
    mayúsculas ni espacios de más. «No sabe», «no sabe » y «NO SABE» son el
    mismo valor de no respuesta."""
    return " ".join(str(valor).split()).lower() if valor is not None else ""


def respuesta_de(pregunta, valor):
    """`(etiqueta, texto_embebido, motivo)` de una celda.

    **Es la única implementación de «qué se embebe»**: la usan la ingesta
    (`despivotar`), la vista previa (R8.8) y el reproceso (R8.9). Si cada uno
    compusiera el texto por su cuenta, la vista previa mostraría una cosa y
    la ingesta escribiría otra, que es exactamente lo que la vista previa
    existe para impedir.

    Si la celda no genera respuesta devuelve `(None, None, motivo)`.

    Las decisiones de normalización viajan **en la pregunta** y son todas
    opcionales: una pregunta sin ellas se embebe exactamente como antes de la
    Fase 8. Ninguna se aplica sola —el sistema las propone, el analista las
    confirma— y por eso acá solo se leen.

    * `solo_marcadas` + `valores_marcados` — R8.1: de una batería de
      dicotómicas solo entra lo marcado. Lo no marcado no es información, es
      el reverso de una ausencia.
    * `excluir_valores` — R8.5: los códigos (o etiquetas) de no respuesta que
      se decidió no ingestar.
    * `prefijo_respuesta` — R8.3: una «Otro: especificar» fusionada con su
      cerrada se embebe como «¿Qué marca fumás? → Otra: Nevada Blue».
    """
    if valor is None or str(valor).strip() == "":
        return None, None, VACIA
    crudo = str(valor).strip()
    opciones = pregunta.get("opciones")

    if pregunta.get("solo_marcadas"):
        marcados = {_comparable(v) for v in (pregunta.get("valores_marcados") or ["1"])}
        if _comparable(crudo) not in marcados:
            return None, None, NO_MARCADA

    etiqueta = _etiqueta(crudo, opciones)
    excluir = {_comparable(v) for v in (pregunta.get("excluir_valores") or [])}
    if excluir and (_comparable(crudo) in excluir or _comparable(etiqueta) in excluir):
        return None, None, NO_RESPUESTA
    if not etiqueta:
        return None, None, VACIA

    prefijo = (pregunta.get("prefijo_respuesta") or "").strip()
    mostrada = f"{prefijo} {etiqueta}" if prefijo else etiqueta
    return etiqueta, componer_texto(pregunta["texto"], mostrada), None


def despivotar(filas, preguntas, columna_id, descartes=None):
    """Ancho → largo. Devuelve `[(id_en_origen, codigo, etiqueta, texto)]`.

    Las celdas vacías se descartan: una no-respuesta no es una respuesta y
    no debe generar un embedding. Desde la Fase 8 tampoco entran las
    opciones no marcadas de una batería ni los valores de no respuesta, si
    el analista lo decidió así; `descartes`, si se pasa, cuenta cuántas
    celdas quedaron afuera por cada uno de esos dos motivos.
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
            etiqueta, texto, motivo = respuesta_de(por_codigo[codigo], valor)
            if motivo:
                if descartes is not None and motivo != VACIA:
                    descartes[motivo] = descartes.get(motivo, 0) + 1
                continue
            largo.append((id_origen, codigo, etiqueta, texto))
    return largo


def _descartadas(descartes):
    """Las dos claves sumables del resultado, siempre presentes."""
    return {
        "descartadas_no_marcadas": descartes.get(NO_MARCADA, 0),
        "descartadas_no_respuesta": descartes.get(NO_RESPUESTA, 0),
    }


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


def _por_que_no_entro_nada(ids_origen, sin_mapear, motivos, bloqueadas):
    """El aviso cuando el lote no escribió nada.

    «Ninguna respuesta quedó habilitada para ingestar» era verdad y no
    servía: no distingue *no matcheó nadie* de *nadie consintió*, que se
    arreglan de forma opuesta. Peor todavía en el caso que lo hizo evidente
    —1131 filas, el 100% sin mapear por falta de alias—, donde el sistema
    tenía toda la información para decir que el modo elegido no era el que
    hacía falta, y en vez de eso mandó a buscar el problema a otro lado.
    """
    total = len(ids_origen)
    todos_sin_mapear = total and len(sin_mapear) == total
    solo_falta_alias = todos_sin_mapear and all(
        motivos.get(i) == SIN_ALIAS for i in sin_mapear)

    if solo_falta_alias:
        return (
            f"Ninguno de los {total} identificadores del archivo corresponde "
            f"a un panelista que ya exista en el sistema. Si querés dar de "
            f"alta a estas personas, elegí el modo «crear los individuos en "
            f"esta carga». Si tenían que existir, revisá que la columna "
            f"identificadora y el origen sean los correctos.")
    if todos_sin_mapear:
        porque = ", ".join(sorted({motivos.get(i, NO_ENCONTRADO)
                                   for i in sin_mapear}))
        return (
            f"Ninguno de los {total} identificadores del archivo se pudo "
            f"vincular a una persona del sistema ({porque}).")
    if bloqueadas and not sin_mapear:
        return (
            f"Las {len(bloqueadas)} personas del archivo existen, pero "
            f"ninguna tiene vigente el consentimiento de uso semántico, así "
            f"que sus respuestas no se conservan.")
    return (
        f"No entró ninguna respuesta: {len(sin_mapear)} identificador(es) sin "
        f"vincular y {len(bloqueadas)} persona(s) sin consentimiento de uso "
        f"semántico.")


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
    normalizacion=None,
):
    """Corre la ingesta de un estudio. `encuesta` es el dict del store de bóveda
    (necesita `ref_estudio`, `nombre`, `fecha_campo`).

    Aplica el gate de `uso_semantico`: quien no lo tenga vigente queda fuera
    de la ingesta, y se informa en el resultado. No es un error del lote.

    `normalizacion` (Fase 8) es la configuración de la carga que no es de
    ninguna pregunta en particular —hoy, la lista de valores de no respuesta
    que se usó para detectar—. Se guarda con el cuestionario para que un
    reproceso sepa qué se había decidido.
    """
    proveedor = proveedor or mod_embeddings.crear()
    ref_estudio = str(encuesta["ref_estudio"])

    descartes = {}
    largo = despivotar(filas, preguntas, columna_id, descartes)
    if not largo:
        return {
            **_descartadas(descartes),
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
            **_descartadas(descartes),
            "ref_estudio": ref_estudio,
            "respuestas_escritas": 0,
            "personas": 0,
            "sin_mapear": sin_mapear,
            "sin_mapear_detalle": detalle_sin_mapear,
            "sin_consentimiento": bloqueadas,
            "ids_persona_ingestados": [],
            "aviso": _por_que_no_entro_nada(
                ids_origen, sin_mapear, motivos_sin_mapear, bloqueadas),
        }

    # ── A partir de acá se escribe del lado semántico: solo id_persona ──
    cuestionario_id = semantica.asegurar_cuestionario(
        conn_semantica,
        ref_estudio,
        encuesta["nombre"],
        encuesta.get("fecha_campo"),
        {"panel_id": encuesta.get("panel_id")},
        normalizacion=normalizacion,
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

    # ── Escribir ──
    #
    # R-ASYNC.2.b — **embeber un sub-lote, guardarlo y soltarlo**, antes de
    # pedir el siguiente. En ningún momento hay más de un sub-lote de
    # vectores vivo.
    #
    # Antes esto era: embeber todo, después escribir todo. Con 200.000
    # respuestas eso son varios GB de números contra el 1 GiB de la función,
    # y el síntoma es un error genérico difícil de atribuir. Partir la
    # ingesta en tareas (R-ASYNC.1) **no alcanza** para arreglarlo: con lotes
    # de 2.000 el problema no aparece, pero por el tamaño elegido y no porque
    # el patrón esté bien. El día que alguien suba el lote buscando
    # velocidad, vuelve.
    escritas = 0

    if a_embeber:
        # Antes de mandar nada al proveedor, y antes de escribir nada: que la
        # dimensión que va a devolver sea la que la columna acepta. Es una
        # consulta, y lo que evita es pagar el embedding de todo el lote para
        # que después el insert lo rechace. Con 200.000 respuestas esa
        # factura no es teórica.
        desajuste = esquema.desajuste_de_dimension(
            conn_semantica, getattr(proveedor, "dims", None))
        if desajuste:
            raise DatosInvalidos(desajuste, {"motivo": "dimension_de_embeddings"})

    # Primero lo que no hay que embeber: filas cuyo texto no cambió desde la
    # última ingesta. Son un `update` sin vector y no cuestan nada.
    #
    # La comparación es por **identidad** y no por valor: `a_embeber` guarda
    # los mismos objetos que `respuestas`, y un `f not in a_embeber` sería
    # cuadrático —con 200.000 filas, cuarenta mil millones de comparaciones
    # de diccionarios— además de comparar por contenido algo que ya se sabe
    # por referencia.
    hay_que_embeber = {id(f) for f in a_embeber}
    sin_vector = [f for f in respuestas if id(f) not in hay_que_embeber]
    if sin_vector:
        escritas += semantica.upsert_respuestas(conn_semantica, sin_vector)

    if a_embeber:
        textos = [f["texto_embebido"] for f in a_embeber]
        for inicio, vectores in proveedor.embeber_por_lotes(textos):
            sublote = a_embeber[inicio : inicio + len(vectores)]
            for fila, vector in zip(sublote, vectores):
                fila["embedding"] = vector
            escritas += semantica.upsert_respuestas(conn_semantica, sublote)
            # Y se sueltan. El upsert ya los escribió; conservarlos solo
            # haría que el pico de memoria creciera con el tamaño del lote,
            # que es exactamente lo que este requisito evita.
            for fila in sublote:
                fila["embedding"] = None
            del vectores

    return {
        **_descartadas(descartes),
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
