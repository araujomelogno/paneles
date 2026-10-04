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

from . import sav

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
    demograficas = plan.get("demograficas") or {}
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
    por_campo = {m["campo"]: variable for variable, m in demograficas.items()
                 if m.get("campo") in (*sav.CAMPOS_DEMOGRAFICOS,)}
    dedup = []
    for campo in CLAVES_SIMPLES:
        if por_campo.get(campo):
            dedup.append(_estadistica_de_clave(
                campo, (campo,), (por_campo[campo],), filas))
    if all(por_campo.get(c) for c in CLAVE_COMPUESTA):
        dedup.append(_estadistica_de_clave(
            "nombre + fecha de nacimiento", CLAVE_COMPUESTA,
            tuple(por_campo[c] for c in CLAVE_COMPUESTA), filas))

    estimado = _personas_estimadas(filas, por_campo)

    # ── Volumen ──────────────────────────────────────────────────────
    codigos_semanticos = [v["codigo"] for v in al_semantico]
    respuestas = sum(
        1 for fila in filas for codigo in codigos_semanticos
        if _valor(fila, codigo)
    )

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
        },
        "dedup": dedup,
    }
    resumen["advertencias"] = _advertencias(resumen, preguntas, demograficas,
                                            tipo_de, filas)
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

    if resumen["identidad"]["crea_personas"] and not resumen["dedup"]:
        avisos.append({
            "tipo": "sin_clave_de_dedup", "grave": True,
            "mensaje": (
                "No hay ninguna variable marcada como documento, correo, ni "
                "nombre con fecha de nacimiento. Sin clave de deduplicación "
                "no hay forma de reconocer a alguien que ya esté en el "
                "sistema, y cada carga vuelve a crear a las mismas personas."),
        })

    return avisos
