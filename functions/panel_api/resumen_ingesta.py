"""El resumen que se revisa antes de importar (R7.2).

Una importación es **la operación menos reversible del sistema**: crea
personas en la bóveda, escribe embeddings y da de alta membresías.
Deshacerla es borrar gente. Y hasta ahora se confirmaba a ciegas: se
definían decenas de cosas —qué columna identifica, qué va a cada store, qué
evidencia el consentimiento— y se apretaba ingestar sin ver nada junto.

── Por qué este módulo no reconstruye nada ──

El resumen **solo vale si refleja lo que de verdad se va a ejecutar**. Por
eso no se arma de una copia paralela del estado de la pantalla: se arma del
mismo `plan` y las mismas `filas` que recibe la ingesta, por la misma ruta,
con la bandera `solo_revisar`. Una reconstrucción aparte empieza igual y
diverge con el primer cambio que alguien haga de un solo lado — y lo que
diverge es justamente la pantalla que dice «esto es lo que va a pasar».

── El conteo de claves de dedup es el corazón de todo esto ──

El resto del resumen ayuda a leer. Este detecta el error que destruye datos.

Si alguien mapea por error una variable de sí/no a `documento`, el resumen
lo grita: «documento: 1131 filas, **2 valores distintos**, 1129 colisionan →
se crearían **2 personas**». Sin ese número el error pasa, y como el dedup
resuelve primero por documento, la base entera se fusiona en dos registros.
Con él es imposible no verlo.
"""

from . import calidad_dato, ingesta, sav

# Las claves con las que `dedup.resolver` decide, **en su orden**. La
# compuesta va última porque es la que el dedup usa solo cuando no hay
# ninguna clave fuerte, y el resumen tiene que contar lo mismo que va a
# pasar, no lo que sería razonable contar.
CLAVES_SIMPLES = ("documento", "email")
CLAVE_COMPUESTA = ("nombre", "fecha_nacimiento")

# Cuándo una clave deja de ser un número y pasa a ser una advertencia.
# Deliberadamente generoso: un archivo donde la mitad de las filas colisiona
# por documento no es un padrón con repetidos, es un marcado equivocado.
PROPORCION_SOSPECHOSA = 0.5
FILAS_PARA_SOSPECHAR = 10
EJEMPLOS_POR_CLAVE = 5

# ── Qué frena y qué solo avisa ──────────────────────────────────────
# El inventario de lo que el paso de revisión puede decir, cada cosa con su
# clase y su motivo (BUG_validacion_dedup_bloquea §6). Una detección que
# bloquea sin salida convierte una ayuda en un obstáculo, así que la regla es:
#
#   · **Bloquea** solo lo que falta y el sistema no puede suplir con una
#     decisión del analista: la base legal del alta, una variable que no está
#     en el archivo. Esos no son advertencias del resumen: los rechaza la
#     ruta (`DatosInvalidos`) y la pantalla muestra el motivo junto al botón.
#   · **Advierte** todo lo demás: lo que tiene un costo que el analista puede
#     asumir a conciencia. Se muestra, se explica la consecuencia, y se puede
#     continuar.
#
# `grave` es cómo se muestra (destacada, en rojo); `bloquea` es si frena. Que
# sean dos campos es el punto: antes una sola bandera hacía las dos cosas, y
# «importante» terminaba leyéndose como «prohibido».
BLOQUEA, ADVIERTE = "bloquea", "advierte"

VALIDACIONES = {
    # — Las que rechaza la ruta antes de escribir nada —
    "sin_evidencia_de_consentimiento": (
        BLOQUEA, "crear personas sin evidencia de consentimiento no tiene base "
                 "legal: falta algo que ninguna decisión del analista suple"),
    "variable_de_consentimiento_ausente": (
        BLOQUEA, "la variable declarada no está en el archivo: ninguna fila "
                 "evidenciaría consentimiento y el alta daría cero sin explicar"),
    "sin_campo_de_identidad": (
        BLOQUEA, "para crear personas hace falta al menos documento, correo o "
                 "nombre: sin eso no hay a quién dar de alta"),
    "documento_implausible": (
        BLOQUEA, "una variable de sí/no marcada como documento fusiona la base "
                 "entera en dos personas: es un error de marcado, no un costo"),
    # — Las del resumen: se muestran y se puede seguir —
    "sin_clave_de_dedup": (
        ADVIERTE, "una carga futura con la misma gente va a crear duplicados; "
                  "hay casos legítimos —una base que se carga una vez, una "
                  "identidad que resuelve el alias— y el costo se puede asumir"),
    "clave_de_dedup_sospechosa": (
        ADVIERTE, "muchas filas comparten la clave: suele ser un marcado "
                  "equivocado, pero un padrón con repetidos existe"),
    "pregunta_sin_texto": (
        ADVIERTE, "la respuesta queda fuera de las búsquedas, no rompe nada"),
    "valores_sin_mapear": (
        ADVIERTE, "esas personas quedan sin el atributo; un 99 de «no "
                  "contesta» sin categoría puede estar bien"),
    # Los hallazgos de calidad del dato que rompen el texto embebido (Fase 8)
    # entran con su propio tipo, todos como advertencia: la PII en texto libre
    # es un indicio, no una certeza.
    "calidad_del_dato": (
        ADVIERTE, "degrada el texto que se embebe; el analista decide"),
}


def _valor(fila, variable):
    if not variable:
        return ""
    bruto = fila.get(variable)
    if bruto is None or bruto != bruto:   # noqa: PLR0124 — NaN
        return ""
    return str(bruto).strip()


def _grupos(filas, variables):
    """`{valor compuesto: cuántas filas}`, sin las filas que no traen la clave.

    Una fila sin el dato no colisiona con nada: no se la cuenta ni como
    repetida ni como distinta. Contarla inflaría «valores distintos» con un
    vacío que el dedup nunca va a comparar.
    """
    conteo = {}
    for fila in filas:
        partes = [_valor(fila, v) for v in variables]
        if not all(partes):
            continue
        conteo["\x1f".join(partes)] = conteo.get("\x1f".join(partes), 0) + 1
    return conteo


def _canonica(marca):
    """Una marca demográfica en la forma `{"campo": …, "mapeo": …}`.

    La ruta de `.sav` manda el marcado ya normalizado; la de filas (`.csv`,
    `.xlsx`) lo manda como viene, y la forma vieja es `{"MAIL": "email"}`.
    Sin esto el resumen de esa ruta reventaba con la primera demográfica.
    """
    if isinstance(marca, dict):
        return marca
    return {"campo": marca, "mapeo": {}}


def _por_campo(demograficas):
    """`{campo de persona: variable del archivo}` del marcado."""
    return {m["campo"]: variable
            for variable, m in ((v, _canonica(m)) for v, m in
                                (demograficas or {}).items())
            if m.get("campo") in sav.CAMPOS_DEMOGRAFICOS}


def claves_de_dedup(demograficas, filas):
    """Las claves con las que `dedup.resolver` va a poder reconocer a alguien.

    **En los mismos términos que el dedup**: documento, correo, y nombre con
    fecha de nacimiento. Una clave presente en parte de las filas **cuenta**
    —300 correos sobre 1131 filas deduplican a esos 300— y se informa con su
    cobertura. Lo que no cuenta es una marcada que no trae ni un valor: el
    dedup no tendría con qué comparar.

    Es la única definición de «hay clave»: la usan el resumen y la ingesta,
    para que lo que la revisión avisa sea lo que la carga registra.
    """
    filas = list(filas or [])
    por_campo = _por_campo(demograficas)
    claves = []
    for campo in CLAVES_SIMPLES:
        if por_campo.get(campo):
            claves.append(_estadistica_de_clave(
                campo, (campo,), (por_campo[campo],), filas))
    if all(por_campo.get(c) for c in CLAVE_COMPUESTA):
        claves.append(_estadistica_de_clave(
            "nombre + fecha de nacimiento", CLAVE_COMPUESTA,
            tuple(por_campo[c] for c in CLAVE_COMPUESTA), filas))
    return claves


def sin_clave_de_dedup(plan, filas):
    """¿La carga crea personas sin ninguna clave de dedup con valores?

    Es lo que la revisión advierte y lo que la ingesta deja registrado cuando
    el analista decide continuar igual."""
    if (plan or {}).get("modo") != "crear_individuos":
        return False
    return not any(c["filas_con_valor"]
                   for c in claves_de_dedup(plan.get("demograficas"), filas))


def _estadistica_de_clave(nombre, campos, variables, filas):
    conteo = _grupos(filas, variables)
    con_valor = sum(conteo.values())
    distintos = len(conteo)
    repetidos = {v: n for v, n in conteo.items() if n > 1}
    colisionan = sum(repetidos.values())
    sospechosa = (
        con_valor >= FILAS_PARA_SOSPECHAR
        and distintos > 0
        and (con_valor - distintos) / con_valor >= PROPORCION_SOSPECHOSA
    )
    return {
        "clave": nombre,
        "campos": list(campos),
        "variables": list(variables),
        "filas_con_valor": con_valor,
        # La cobertura: «email: 300 de 1131 filas». Una clave parcial sirve
        # —deduplica a los que la traen— y el número dice cuánto.
        "filas_total": len(filas),
        "cobertura": f"{con_valor} de {len(filas)} filas",
        "valores_distintos": distintos,
        "filas_que_colisionan": colisionan,
        "grupos_repetidos": len(repetidos),
        "personas_por_esta_clave": distintos,
        "ejemplos": [
            {"valor": v.replace("\x1f", " · "), "filas": n}
            for v, n in sorted(repetidos.items(), key=lambda p: -p[1])[:EJEMPLOS_POR_CLAVE]
        ],
        "sospechosa": sospechosa,
    }


def _personas_estimadas(filas, por_campo):
    """Cuántas personas distintas saldrían, siguiendo el orden del dedup.

    Documento, si no correo, si no nombre + fecha de nacimiento. Una fila sin
    ninguna de las tres no se puede agrupar con nadie: cuenta como propia,
    que es lo que va a pasar al crearla.
    """
    claves, sueltas = set(), 0
    for indice, fila in enumerate(filas):
        documento = _valor(fila, por_campo.get("documento"))
        email = _valor(fila, por_campo.get("email"))
        nombre = _valor(fila, por_campo.get("nombre"))
        nacimiento = _valor(fila, por_campo.get("fecha_nacimiento"))
        if documento:
            claves.add(("documento", documento.lower()))
        elif email:
            claves.add(("email", email.lower()))
        elif nombre and nacimiento:
            claves.add(("nombre_fnac", nombre.lower(), nacimiento))
        else:
            sueltas += 1
            claves.add(("fila", indice))
    return {"personas": len(claves), "sin_clave_de_dedup": sueltas}


def resumir(plan, filas, *, panel=None, evidencia=None, destino_tipo="encuesta",
            nombre_destino=None):
    """Las siete secciones del resumen, del mismo plan que se va a ejecutar.

    `evidencia` es la declaración **ya normalizada** por
    `sav.normalizar_evidencia`, o `None` cuando el modo no crea personas.
    """
    filas = list(filas or [])
    preguntas = list(plan.get("preguntas") or [])
    demograficas = {v: _canonica(m)
                    for v, m in (plan.get("demograficas") or {}).items()}
    columna_id = plan.get("columna_id")

    texto_de = {p.get("codigo"): (p.get("texto") or "") for p in preguntas}
    tipo_de = {p.get("codigo"): p.get("tipo") for p in preguntas}
    del_archivo = {c for fila in filas for c in fila}

    # ── Adónde va cada variable ──────────────────────────────────────
    al_semantico, a_boveda_demo, a_boveda_identidad, excluidas = [], [], [], []
    for pregunta in preguntas:
        codigo = pregunta.get("codigo")
        marca = demograficas.get(codigo)
        if marca is None:
            al_semantico.append({
                "codigo": codigo,
                "texto": pregunta.get("texto"),
                "tipo": pregunta.get("tipo"),
            })
            continue
        campo = marca.get("campo")
        if campo == sav.SOLO_EXCLUIR:
            excluidas.append({"codigo": codigo, "texto": texto_de.get(codigo),
                              "motivo": "marcada para no guardar"})
            continue
        entrada = {"codigo": codigo, "texto": texto_de.get(codigo),
                   "campo": campo}
        if campo in sav.CAMPOS_DEMOGRAFICOS:
            a_boveda_identidad.append(entrada)
        else:
            entrada["mapeo"] = marca.get("mapeo") or {}
            a_boveda_demo.append(entrada)

    declaradas = {p.get("codigo") for p in preguntas}
    for codigo in sorted(del_archivo - declaradas):
        excluidas.append({"codigo": codigo, "texto": None,
                          "motivo": "no se declaró en la lista de variables"})

    # ── Las claves de dedup ──────────────────────────────────────────
    por_campo = _por_campo(demograficas)
    dedup = claves_de_dedup(demograficas, filas)

    estimado = _personas_estimadas(filas, por_campo)

    # ── Volumen ──────────────────────────────────────────────────────
    # Fase 8 — las respuestas se cuentan con `ingesta.despivotar`, que es lo
    # que corre al ingestar: con las decisiones de normalización (solo lo
    # marcado de una batería, sin los valores de no respuesta) el número que
    # se muestra es el que se va a escribir, no el de celdas con algo.
    semanticas = [p for p in preguntas
                  if demograficas.get(p.get("codigo")) is None and p.get("codigo")]
    descartes = {}
    respuestas = len(ingesta.despivotar(
        filas, [dict(p, texto=p.get("texto") or p.get("codigo"))
                for p in semanticas],
        columna_id or "id_en_origen", descartes))

    # ── Fase 8 · Calidad del dato y vista previa ─────────────────────
    # Del mismo plan y las mismas filas: lo que el resumen dice que se va a
    # embeber es lo que se embebe.
    distribucion = calidad_dato.distribucion_de_filas(
        filas, {p["codigo"] for p in semanticas})
    calidad = calidad_dato.diagnosticar(
        semanticas, distribucion, filas_total=len(filas),
        valores_no_respuesta=(plan.get("normalizacion") or {}).get(
            "valores_no_respuesta"))
    previas = calidad_dato.vistas_previas(semanticas, distribucion)
    for variable in al_semantico:
        previa = previas.get(variable["codigo"]) or {}
        variable["respuestas_que_genera"] = previa.get("genera", 0)
        variable["normalizacion"] = {
            clave: valor for clave, valor in (
                (c, next((p.get(c) for p in semanticas
                          if p.get("codigo") == variable["codigo"]), None))
                for c in ("solo_marcadas", "excluir_valores",
                          "prefijo_respuesta", "fusionada_con", "pii_aceptada"))
            if valor}

    resumen = {
        "identidad": {
            "modo": plan.get("modo") or "existen",
            "crea_personas": (plan.get("modo") == "crear_individuos"),
            "columna_id": columna_id,
            "columna_id_texto": texto_de.get(columna_id),
            "tipo_identificador": plan.get("tipo_identificador") or "alias",
            "origen": plan.get("origen"),
        },
        "consentimiento": _seccion_consentimiento(evidencia, texto_de),
        "al_store_semantico": {
            "variables": al_semantico, "cuantas": len(al_semantico)},
        "a_la_boveda": {
            "demograficas": a_boveda_demo,
            "identidad_y_contacto": a_boveda_identidad,
        },
        "excluidas": excluidas,
        "panel": {
            "destino_tipo": destino_tipo,
            "nombre": nombre_destino,
            "panel_id": panel,
            "sin_panel": destino_tipo == "carga" or panel is None,
        },
        "volumen": {
            "filas_del_archivo": len(filas),
            "respuestas_a_escribir": respuestas,
            "personas_estimadas": estimado["personas"],
            "filas_sin_clave_de_dedup": estimado["sin_clave_de_dedup"],
            # Fase 8 — lo que la normalización deja afuera, por motivo.
            "descartadas_no_marcadas": descartes.get(ingesta.NO_MARCADA, 0),
            "descartadas_no_respuesta": descartes.get(ingesta.NO_RESPUESTA, 0),
        },
        "dedup": dedup,
        # El mismo cálculo que va a registrar la ingesta si se continúa.
        "sin_clave_de_dedup": sin_clave_de_dedup(
            {"modo": plan.get("modo"), "demograficas": demograficas}, filas),
        "calidad": calidad,
        "vista_previa": previas,
    }
    resumen["advertencias"] = _advertencias(resumen, preguntas, demograficas,
                                            tipo_de, filas)
    # Ninguna advertencia del resumen frena la carga: lo que frena lo rechaza
    # la ruta. Se dice explícito en cada una para que la pantalla no tenga
    # que deducirlo de `grave`.
    for aviso in resumen["advertencias"]:
        aviso["bloquea"] = False
    resumen["bloquea"] = False
    return resumen


def _seccion_consentimiento(evidencia, texto_de):
    if not evidencia:
        return {"aplica": False,
                "nota": ("El modo elegido no crea personas, así que no hace "
                         "falta declarar la base legal del alta.")}
    return {
        "aplica": True,
        "finalidades": [
            {
                "finalidad": finalidad,
                "variable": regla["variable"],
                "variable_texto": texto_de.get(regla["variable"]),
                "valores_afirmativos": regla["valores_afirmativos"],
                "version_texto": regla["version_texto"],
            }
            for finalidad, regla in sorted(evidencia.items())
        ],
    }


def _advertencias(resumen, preguntas, demograficas, tipo_de, filas):
    """Lo que hoy aparece recién al final, adelantado a la revisión."""
    avisos = []

    for clave in resumen["dedup"]:
        if clave["sospechosa"]:
            avisos.append({
                "tipo": "clave_de_dedup_sospechosa",
                "grave": True,
                "mensaje": (
                    f"La clave «{clave['clave']}» tiene "
                    f"{clave['filas_con_valor']} filas con valor y solo "
                    f"{clave['valores_distintos']} valor(es) distinto(s): "
                    f"{clave['filas_que_colisionan']} filas comparten su "
                    f"valor con otra. Si se confirma, esas filas se fusionan "
                    f"en {clave['valores_distintos']} persona(s). Revisá que "
                    f"la variable marcada sea realmente ese campo."),
            })

    sin_texto = [p.get("codigo") for p in preguntas
                 if p.get("codigo") not in demograficas
                 and not (p.get("texto") or "").strip()]
    if sin_texto:
        avisos.append({
            "tipo": "pregunta_sin_texto", "grave": False,
            "mensaje": (
                f"{len(sin_texto)} variable(s) se ingestan como pregunta sin "
                f"texto: {sin_texto[:10]}. El texto es lo que se vectoriza, "
                f"así que sin él la respuesta queda fuera de las búsquedas."),
        })

    sin_mapear = []
    for variable, marca in demograficas.items():
        if marca.get("campo") in (None, sav.SOLO_EXCLUIR):
            continue
        if marca.get("campo") in sav.CAMPOS_DEMOGRAFICOS:
            continue
        mapeo = marca.get("mapeo") or {}
        if not mapeo:
            continue
        presentes = {_valor(fila, variable) for fila in filas} - {""}
        faltan = sorted(presentes - set(mapeo))
        if faltan:
            sin_mapear.append({"variable": variable, "valores": faltan[:10]})
    if sin_mapear:
        avisos.append({
            "tipo": "valores_sin_mapear", "grave": False,
            "detalle": sin_mapear,
            "mensaje": (
                f"{len(sin_mapear)} variable(s) tienen valores del archivo sin "
                f"mapear a ninguna categoría. Esas personas quedan sin ese "
                f"atributo; puede estar bien —un 99 de «no contesta»— pero "
                f"conviene decidirlo acá y no en una cuota que no cierra."),
        })

    # Fase 8 — lo que rompe el dato semántico, adelantado acá también. No
    # bloquea nada (ni siquiera la PII: es un indicio, no una certeza), y por
    # eso no es `grave`: el resumen lo muestra para que la decisión sea
    # consciente. La PII que se aceptó a conciencia ya no se repite.
    aceptadas = {p.get("codigo") for p in preguntas if p.get("pii_aceptada")}
    for hallazgo in (resumen.get("calidad") or {}).get("hallazgos", []):
        if hallazgo["severidad"] != calidad_dato.ROMPE:
            continue
        if (hallazgo["tipo"] == "pii_en_texto_libre"
                and set(hallazgo["variables"]) <= aceptadas):
            continue
        avisos.append({"tipo": hallazgo["tipo"], "grave": False,
                       "clase": "calidad_del_dato",
                       "variables": hallazgo["variables"],
                       "mensaje": hallazgo["mensaje"]})

    if resumen["sin_clave_de_dedup"]:
        # El mensaje dice exactamente lo que la condición mira: las tres
        # claves del dedup y si traen valores. Si hay alguna marcada pero
        # vacía, lo nombra: «no hay clave» a secas, con el correo marcado,
        # es lo que hizo creer que el sistema no lo veía.
        vacias = [c["clave"] for c in resumen["dedup"] if not c["filas_con_valor"]]
        marcadas = (
            f" {', '.join(f'«{c}»' for c in vacias)} está(n) marcada(s), pero "
            f"ninguna fila trae un valor." if vacias else "")
        avisos.append({
            "tipo": "sin_clave_de_dedup", "grave": True,
            "acciones": ["volver_a_corregir", "continuar_igual"],
            "mensaje": (
                "Ninguna fila trae una clave de deduplicación: ni documento, "
                "ni correo, ni nombre con fecha de nacimiento." + marcadas +
                " Sin clave no hay forma de reconocer a alguien que ya esté "
                "en el sistema: una carga futura con la misma gente va a "
                "crear registros duplicados. Podés volver a corregir el "
                "mapeo o continuar igual —hay casos en que está bien, como "
                "una base que se carga una sola vez—; si continuás, queda "
                "registrado en el resultado de la carga."),
        })

    for aviso in avisos:
        aviso.setdefault("clase", aviso["tipo"])
    return avisos
