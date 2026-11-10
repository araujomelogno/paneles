"""D93 · R-MIX — toda carga es mixta: el sistema decide fila por fila.

Un archivo de Alchemer trae dos tipos de filas: quienes llegaron por el enlace
de la convocatoria, con su `id_persona` en la columna identificadora, y
quienes llegaron sin ese parámetro —personas nuevas—, con la columna vacía.
La pregunta «¿Los panelistas ya están en el sistema?» obligaba a elegir entre
dos modos y ninguno servía para un archivo mixto: «ya existen» no creaba a las
nuevas, y «crear» descartaba toda fila con la columna vacía. La pregunta se
eliminó: cada fila se resuelve en este orden, y la primera capa que encuentra
a alguien decide.

1. **La columna identificadora**, según su tipo, si la fila trae valor.
   Un `id_persona` que no existe es un **enlace roto** (un id mal copiado, o
   el de alguien que se dio de baja) y no una persona nueva: la fila no entra
   y se informa como «id desconocido». Un id de la plataforma, un documento,
   un correo o un celular que no se encuentra sigue a la capa siguiente.
2. **El ID de respuesta de la plataforma**, si se declaró esa columna
   (opcional). Se guarda en `alias_origen` con un origen **de la encuesta**
   (`respuesta:<ref_estudio>`): el ID de respuesta de Alchemer es único dentro
   de una encuesta, no entre encuestas, y con un origen común la respuesta 12
   de un estudio «encontraría» a la persona de la respuesta 12 de otro.
3. **El dedup** (`dedup.resolver`, con sus cautelas: un celular compartido va
   a revisión), con los datos que la fila trae marcados.
4. **Persona nueva**, si ninguna capa la encontró y la fila trae con qué
   crearla. Se crea con `sav.crear_individuos` —la evidencia de
   consentimiento, la finalidad obligatoria y la revisión de siempre—, y
   **solo** las nuevas reciben esa evidencia.

`reconocer` no escribe nada: lo usan la revisión (la vista previa con los
conteos) y la confirmación, que llama a `aplicar` sobre el mismo resultado. La
regla está una sola vez.

Lo que sale de `aplicar` son las filas con su `id_persona` resuelto en una
columna propia (`COLUMNA_PERSONA`): el plan congelado identifica por esa
columna y los lotes siguen llamando a `cargas.ingestar`, la de siempre.
"""

from . import db, dedup, ingesta, sav
from .errores import DatosInvalidos
from .preferencias import normalizar_celular

# La columna que la confirmación agrega a cada fila con el `id_persona`
# resuelto. Es lo que identifica a la fila en los lotes.
COLUMNA_PERSONA = "__id_persona"
# La clave de una fila para el alta: el alta necesita una por fila y una fila
# nueva puede no traer ningún identificador.
CLAVE_DE_FILA = "__fila"

# Cómo terminó cada fila.
EXISTENTE = "existente"          # la encontró la capa 1 o la 2
RECONOCIDA = "reconocida"        # nueva para la columna, la reconoció el dedup
A_CREAR = "a_crear"              # nueva de verdad
EN_REVISION = "en_revision"      # el dedup no pudo decidir: va a revisión
ID_DESCONOCIDO = "id_desconocido"  # un id_persona que no existe: no entra
SIN_DATOS = "sin_datos"          # nueva sin documento, correo ni nombre

CAPA_IDENTIFICADOR = "identificador"
CAPA_ID_RESPUESTA = "id_respuesta"
CAPA_DEDUP = "dedup"

# Qué campo de la persona trae la columna identificadora según su tipo. Si
# no se encuentra por la columna, ese valor es también un dato para el dedup
# y para el alta.
CAMPO_DEL_TIPO = {
    ingesta.POR_DOCUMENTO: "documento",
    ingesta.POR_EMAIL: "email",
    ingesta.POR_CELULAR: "celular",
}

# R-MIX.3 — por encima de esta proporción de nuevas, en una carga que
# esperaba encontrar a su gente, lo probable es una columna o un tipo mal
# elegidos. Es lo que protegía el modo «ya existen».
PROPORCION_INESPERADA = 0.5

AVISO_SIN_CLAVE = ("{n} persona(s) nueva(s) no tienen correo ni celular: si "
                   "aparecen en una carga posterior, se van a crear de nuevo.")


def origen_de_respuesta(encuesta):
    """El origen con el que se guarda el ID de respuesta: uno por encuesta."""
    return f"respuesta:{encuesta['ref_estudio']}"


def _valor(fila, columna):
    if not columna:
        return ""
    crudo = fila.get(columna)
    if crudo is None or crudo != crudo:  # noqa: PLR0124 — NaN
        return ""
    return str(crudo).strip()


def _mapeo(demograficas, columna_id, tipo):
    """`{campo: columna}` para el dedup y el alta, con la columna
    identificadora sumada cuando trae un dato de la persona."""
    mapeo = dict(sav.mapeo_por_campo(demograficas))
    campo = CAMPO_DEL_TIPO.get(tipo)
    if campo and campo not in mapeo:
        mapeo[campo] = columna_id
    return mapeo


def _datos(fila, mapeo, opciones):
    datos = {}
    for campo, columna in mapeo.items():
        if campo not in sav.CAMPOS_PATRONIMICOS:
            continue
        valor = sav.valor_demografico(campo, fila.get(columna),
                                      (opciones or {}).get(columna))
        if valor:
            datos[campo] = valor
    return datos


def _tiene_clave_persistente(datos, respuesta):
    """¿La fila trae algo con que reconocerla en la carga siguiente? El ID de
    respuesta, un correo o un celular válidos (R-MIX.3)."""
    if respuesta:
        return True
    if "@" in (datos.get("email") or ""):
        return True
    return bool(normalizar_celular(datos.get("celular")))


def reconocer(conn, encuesta, filas, columna_id, tipo=None, origen=None,
              columna_respuesta=None, demograficas=None,
              opciones_por_variable=None):
    """Resuelve cada fila por capas. **No escribe nada.**

    Devuelve `{"filas": [...], "conteos": {...}}`; cada fila es `{indice,
    clase, capa, id_persona, valor, respuesta, datos, sin_clave}`.
    """
    tipo = tipo or ingesta.POR_ALIAS
    if tipo not in ingesta.TIPOS_DE_IDENTIFICADOR:
        raise DatosInvalidos(
            f"«{tipo}» no es un tipo de identificador conocido.",
            {"tipos_validos": list(ingesta.TIPOS_DE_IDENTIFICADOR)})
    from . import encuestas

    filas = list(filas or [])
    mapeo = _mapeo(demograficas, columna_id, tipo)
    valores = [_valor(f, columna_id) for f in filas]
    respuestas = [_valor(f, columna_respuesta) for f in filas]

    # Capa 1 — la columna identificadora, de una consulta para todo el archivo.
    con_valor = [v for v in dict.fromkeys(valores) if v and ingesta.tiene_contenido(v)]
    mapa1 = {}
    if con_valor:
        mapa1, _ = encuestas.resolver_identidades(
            conn, encuesta["id"], tipo, con_valor, origen)
    # Capa 2 — el ID de respuesta, si se declaró la columna.
    mapa2 = {}
    if columna_respuesta:
        pedidos = [r for r in dict.fromkeys(respuestas) if r and ingesta.tiene_contenido(r)]
        if pedidos:
            mapa2, _ = ingesta.mapear_a_id_persona(
                conn, origen_de_respuesta(encuesta), pedidos)

    resultado, invalidos = [], 0
    for indice, (fila, valor, respuesta) in enumerate(zip(filas, valores, respuestas)):
        item = {"indice": indice, "valor": valor, "respuesta": respuesta,
                "clase": None, "capa": None, "id_persona": None,
                "sin_clave": False, "motivo": None}
        if valor and not ingesta.tiene_contenido(valor):
            # «...........» no identifica a nadie: es como la columna vacía.
            invalidos += 1
            valor = item["valor"] = ""
        if valor:
            if valor in mapa1:
                item.update(clase=EXISTENTE, capa=CAPA_IDENTIFICADOR,
                            id_persona=str(mapa1[valor]))
                resultado.append(item)
                continue
            if tipo == ingesta.POR_ID_PERSONA:
                # Un enlace roto, no una persona nueva: no se crea a nadie.
                item.update(clase=ID_DESCONOCIDO)
                resultado.append(item)
                continue
        if respuesta and respuesta in mapa2:
            item.update(clase=EXISTENTE, capa=CAPA_ID_RESPUESTA,
                        id_persona=str(mapa2[respuesta]))
            resultado.append(item)
            continue

        datos = _datos(fila, mapeo, opciones_por_variable)
        creable = any(datos.get(c) for c in ("documento", "email", "nombre"))
        resolucion = dedup.resolver(conn, datos) if datos else None
        if resolucion is not None and resolucion.accion == dedup.REUTILIZA:
            item.update(clase=RECONOCIDA, capa=CAPA_DEDUP,
                        id_persona=str(resolucion.id_persona),
                        motivo=resolucion.motivo)
        elif not creable:
            item.update(clase=SIN_DATOS)
        elif resolucion is not None and resolucion.accion == dedup.REVISION:
            item.update(clase=EN_REVISION, motivo=resolucion.motivo)
        else:
            item.update(clase=A_CREAR,
                        sin_clave=not _tiene_clave_persistente(datos, respuesta))
        resultado.append(item)

    return {"filas": resultado,
            "conteos": _conteos(resultado, invalidos),
            "tipo_identificador": tipo,
            "columna_id": columna_id,
            "columna_respuesta": columna_respuesta or None}


def _conteos(filas, invalidos):
    def cuantas(clase, capa=None):
        return sum(1 for f in filas
                   if f["clase"] == clase and (capa is None or f["capa"] == capa))

    existentes = cuantas(EXISTENTE)
    reconocidas, a_crear = cuantas(RECONOCIDA), cuantas(A_CREAR)
    en_revision, sin_datos = cuantas(EN_REVISION), cuantas(SIN_DATOS)
    return {
        "filas": len(filas),
        "existentes": existentes,
        "por_identificador": cuantas(EXISTENTE, CAPA_IDENTIFICADOR),
        "por_id_respuesta": cuantas(EXISTENTE, CAPA_ID_RESPUESTA),
        # Las nuevas: las que la columna (y el ID de respuesta) no
        # encontraron. Algunas ya existían de otro estudio (el dedup).
        "nuevas": reconocidas + a_crear + en_revision + sin_datos,
        "reconocidas_por_dedup": reconocidas,
        "a_crear": a_crear,
        "en_revision": en_revision,
        "sin_datos": sin_datos,
        "id_desconocido": cuantas(ID_DESCONOCIDO),
        "sin_clave": sum(1 for f in filas if f["sin_clave"]),
        "identificadores_invalidos": invalidos,
    }


def requiere_consentimiento(reconocimiento):
    """R-MIX.4 — la evidencia de consentimiento se pide solo si hay personas
    a crear (o a revisión, que se crean al resolverla)."""
    c = reconocimiento["conteos"]
    return bool(c["a_crear"] or c["en_revision"])


def resumen(reconocimiento, esperaba_existentes=False):
    """Lo que la revisión muestra (R-MIX.3): los conteos, la frase, y las
    advertencias. Ninguna bloquea (D63)."""
    c = reconocimiento["conteos"]
    texto = f"{c['filas']} filas · {c['existentes']} existentes · {c['nuevas']} nuevas"
    if c["id_desconocido"]:
        texto += f" · {c['id_desconocido']} con id desconocido"
    detalle = (f"De las nuevas, {c['reconocidas_por_dedup']} ya existían de otro "
               f"estudio (reconocidas por correo, celular o documento) y "
               f"{c['a_crear']} se van a crear.")
    extra = []
    if c["en_revision"]:
        extra.append(f"{c['en_revision']} van a revisión (el dedup no pudo decidir)")
    if c["sin_datos"]:
        extra.append(f"{c['sin_datos']} no traen documento, correo ni nombre: no se "
                     f"pueden crear y no entran")
    if extra:
        detalle += " " + "; ".join(extra) + "."

    advertencias = []
    if c["id_desconocido"]:
        advertencias.append({
            "tipo": "id_desconocido", "grave": True, "bloquea": False,
            "mensaje": (f"{c['id_desconocido']} fila(s) traen un id_persona que no "
                        f"existe: es un enlace roto (un id mal copiado, o el de "
                        f"alguien que se dio de baja). No se crea a nadie y esas "
                        f"filas no entran."),
            "ejemplos": [f["valor"] for f in reconocimiento["filas"]
                         if f["clase"] == ID_DESCONOCIDO][:10]})
    if c["sin_clave"]:
        advertencias.append({
            "tipo": "nuevas_sin_clave", "grave": False, "bloquea": False,
            "mensaje": AVISO_SIN_CLAVE.format(n=c["sin_clave"])
                       + ("" if reconocimiento.get("columna_respuesta") else
                          " Declarar la columna del ID de respuesta lo evita.")})
    inesperada = (esperaba_existentes and c["filas"]
                  and c["nuevas"] / c["filas"] > PROPORCION_INESPERADA)
    if inesperada:
        advertencias.append({
            "tipo": "proporcion_inesperada", "grave": True, "bloquea": False,
            "destacada": True,
            "mensaje": (f"{c['nuevas']} de {c['filas']} filas serían personas nuevas: "
                        f"más de la mitad del archivo, en una carga que esperaba "
                        f"encontrar a su gente. Probablemente la columna "
                        f"identificadora o su tipo están mal elegidos: revisalos "
                        f"antes de confirmar.")})
    return {
        **c,
        "texto": texto,
        "detalle_nuevas": detalle,
        "requiere_consentimiento": requiere_consentimiento(reconocimiento),
        "tipo_identificador": reconocimiento["tipo_identificador"],
        "tipo_etiqueta": ingesta.ETIQUETAS_DE_IDENTIFICADOR.get(
            reconocimiento["tipo_identificador"]),
        "columna_respuesta": reconocimiento.get("columna_respuesta"),
        "proporcion_inesperada": bool(inesperada),
        "advertencias": advertencias,
    }


def filas_para_previa(filas, reconocimiento):
    """Las filas con `COLUMNA_PERSONA` puesta para las que ya tienen persona
    (existentes y reconocidas) y vacía para las nuevas, sin las que no van a
    entrar. Es lo que las previas por persona (consentimiento por finalidad,
    canales) necesitan para contar existentes y nuevas con la misma regla."""
    salida = []
    for fila, item in zip(filas, reconocimiento["filas"]):
        if item["clase"] in (EXISTENTE, RECONOCIDA):
            salida.append({**fila, COLUMNA_PERSONA: item["id_persona"]})
        elif item["clase"] in (A_CREAR, EN_REVISION):
            salida.append({**fila, COLUMNA_PERSONA: ""})
    return salida


def no_convocados(conn, encuesta_id, reconocimiento, excluir=False):
    """R-EU.4 / R-MIX.5 — en una encuesta con audiencia, quiénes del archivo
    no estaban convocados: los existentes fuera de la audiencia **y todas las
    nuevas** —quien no vino con un `id_persona` de la convocatoria no fue
    convocado, aunque el dedup lo reconozca—. Sin audiencia, None."""
    from . import encuestas

    if not encuestas.tiene_audiencia(conn, encuesta_id):
        return None
    convocadas = _convocadas(conn, encuesta_id)
    fuera = [f for f in reconocimiento["filas"] if _fuera_de_la_audiencia(f, convocadas)]
    existentes = sum(1 for f in fuera if f["clase"] == EXISTENTE)
    sin_identificar = len(fuera) - existentes
    tipo = reconocimiento["tipo_identificador"]
    etiqueta = ingesta.ETIQUETAS_DE_IDENTIFICADOR.get(tipo, tipo)
    ids = [f["valor"] or f["respuesta"] or f"fila {f['indice'] + 1}" for f in fuera]
    return {
        "cuantos": len(fuera),
        "existentes": existentes,
        "sin_identificar": sin_identificar,
        "tipo_identificador": tipo,
        "tipo_etiqueta": etiqueta,
        "ids_en_origen": ids[:200],
        "mas": max(0, len(ids) - 200),
        "mensaje": (
            f"{len(fuera)} persona(s) del archivo no estaban convocadas a esta "
            f"encuesta. Se incorporan marcadas «no convocado»; en una encuesta "
            f"con audiencia, casi siempre es un enlace reenviado. Podés "
            f"excluirlas de la carga."
            + (f" {sin_identificar} no vinieron con un {etiqueta} de la "
               f"convocatoria." if sin_identificar else "")) if fuera else None,
    }


def _convocadas(conn, encuesta_id):
    return {str(f["id_persona"]) for f in db.todas(
        conn, "select id_persona from participacion where encuesta_id = %s and convocado",
        (encuesta_id,))}


def _fuera_de_la_audiencia(item, convocadas):
    if item["clase"] in (ID_DESCONOCIDO, SIN_DATOS):
        return False  # no entran igual
    # Las nuevas no vinieron con el enlace de la convocatoria. Quien ya
    # existía y está en la audiencia sigue convocado: no se le borra la
    # convocatoria por cómo llegó la fila.
    return not item["id_persona"] or item["id_persona"] not in convocadas


def aplicar(conn, encuesta, carga_id, filas, reconocimiento, origen=None,
            evidencia_consentimiento=None, demograficas=None,
            opciones_por_variable=None, actor=None, excluir_no_convocados=False):
    """La confirmación: crea a las nuevas, registra los identificadores de la
    plataforma, y devuelve las filas que entran con su `id_persona` en
    `COLUMNA_PERSONA`.

    R-MIX.4 — sin personas a crear no hace falta evidencia de
    consentimiento; con personas a crear, falta → no se escribe nada
    (`DatosInvalidos`, lo que la revisión llama `sin_evidencia_de_
    consentimiento`). La evidencia se aplica solo a las que se crean.
    """
    from . import cargas, encuestas

    filas = list(filas or [])
    items = reconocimiento["filas"]
    tipo = reconocimiento["tipo_identificador"]
    columna_id = reconocimiento["columna_id"]
    columna_respuesta = reconocimiento.get("columna_respuesta")
    if requiere_consentimiento(reconocimiento) and not evidencia_consentimiento:
        raise DatosInvalidos(
            f"Hay {reconocimiento['conteos']['a_crear'] + reconocimiento['conteos']['en_revision']} "
            f"persona(s) nuevas para crear y falta la evidencia de "
            f"consentimiento: indicá qué variable del archivo la contiene. "
            f"Sin eso no hay base legal para el alta.",
            {"motivo": "sin_evidencia_de_consentimiento"})

    excluidas = set()
    if excluir_no_convocados and encuestas.tiene_audiencia(conn, encuesta["id"]):
        convocadas = _convocadas(conn, encuesta["id"])
        excluidas = {i["indice"] for i in items if _fuera_de_la_audiencia(i, convocadas)}

    origen_respuesta = origen_de_respuesta(encuesta)

    def alias_de(fila):
        pares = []
        valor = _valor(fila, columna_id)
        if tipo == ingesta.POR_ALIAS and origen and valor and ingesta.tiene_contenido(valor):
            pares.append((origen, valor))
        respuesta = _valor(fila, columna_respuesta)
        if respuesta and ingesta.tiene_contenido(respuesta):
            pares.append((origen_respuesta, respuesta))
        return pares

    asignado = {}
    for item in items:
        if item["indice"] in excluidas:
            continue
        if item["clase"] in (EXISTENTE, RECONOCIDA):
            asignado[item["indice"]] = item["id_persona"]
            # El ID de respuesta (y el id de la plataforma, si no estaba) quedan
            # registrados: la carga siguiente la encuentra antes del dedup.
            for alias_origen, alias_valor in alias_de(filas[item["indice"]]):
                dedup.registrar_alias(conn, item["id_persona"], alias_origen, alias_valor)
    # R3.12.c — lo encontrado por documento o correo deja sembrado el alias,
    # como hacía la ingesta cuando identificaba por esa columna.
    encuestas._sembrar_alias(
        conn, tipo, {i["valor"]: i["id_persona"] for i in items
                     if i["capa"] == CAPA_IDENTIFICADOR and i["indice"] not in excluidas},
        origen)

    creacion = None
    a_crear = [i for i in items if i["clase"] in (A_CREAR, EN_REVISION)
               and i["indice"] not in excluidas]
    if a_crear:
        nuevas = [{**filas[i["indice"]], CLAVE_DE_FILA: f"fila-{i['indice']}"}
                  for i in a_crear]
        creacion = cargas.crear_individuos(
            conn, nuevas, _mapeo(demograficas, columna_id, tipo),
            origen=origen, columna_id=CLAVE_DE_FILA,
            evidencia_consentimiento=evidencia_consentimiento, actor=actor,
            opciones_por_variable=opciones_por_variable, carga_id=carga_id,
            alias_de=alias_de)
        for entrada in (creacion.get("creados") or []) + (creacion.get("reutilizados") or []):
            asignado[int(entrada["id_en_origen"].split("-", 1)[1])] = entrada["id_persona"]

    entran = [{**fila, COLUMNA_PERSONA: asignado[indice]}
              for indice, fila in enumerate(filas) if indice in asignado]
    return {"filas": entran, "creacion": creacion,
            "excluidas_no_convocadas": len(excluidas)}
