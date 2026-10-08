"""Ruteo de la API: Fases 1, 2 y 3.

Las rutas son las del HANDOFF y las del contrato propuesto en
`specs/SPEC_fase2.md` y `specs/SPEC_fase3.md`, con su requisito al lado. El router no sabe nada de
Firebase: recibe método, ruta, cuerpo y actor, y devuelve `(status, dict)`.
Eso lo hace probable sin desplegar nada.
"""

import os
import re

from . import (
    atributos,
    auditoria,
    bajas,
    desafio,
    diferida,
    calidad,
    calidad_dato,
    cargas,
    composicion,
    consentimiento,
    consultas,
    correo,
    db,
    encuestas,
    esquema,
    estadisticas,
    inscripciones,
    longitudinal,
    muestreo,
    optimizador,
    paneles,
    participacion,
    personas,
    portal,
    preferencias,
    ficha,
    premios,
    puntos,
    reproceso,
    resumen_ingesta,
    revision,
    sav,
    semantica,
    series,
    usuarios,
    verificacion as mod_verificacion,
    verificacion_contacto,
    whatsapp,
)
from .errores import DatosInvalidos, EnvioNoConfigurado, ErrorApi, NoEncontrado

RUTAS = []


def ruta(metodo, patron, permiso, requisito=None):
    """Registra un handler. `patron` usa <nombre> para los parámetros."""
    expresion = re.compile(
        "^" + re.sub(r"<([a-z_]+)>", r"(?P<\1>[^/]+)", patron) + "$"
    )

    def decorador(funcion):
        funcion.requisito = requisito
        RUTAS.append((metodo.upper(), expresion, permiso, funcion, patron))
        return funcion

    return decorador


def resolver(metodo, camino):
    """Encuentra el handler de una ruta. Devuelve `(handler, permiso, params)`."""
    camino = "/" + camino.strip("/")
    rutas_del_camino = []
    for metodo_ruta, expresion, permiso, funcion, _ in RUTAS:
        match = expresion.match(camino)
        if not match:
            continue
        rutas_del_camino.append(metodo_ruta)
        if metodo_ruta == metodo.upper():
            return funcion, permiso, match.groupdict()
    if rutas_del_camino:
        raise ErrorApi(
            f"Método {metodo} no soportado en {camino}.",
            {"metodos": sorted(set(rutas_del_camino))},
        )
    raise NoEncontrado(f"No existe la ruta {camino}.")


# R3.7 — las únicas rutas sin autenticación. Se enumeran acá, una por una,
# y `main.py` consulta esta lista: así el conjunto de lo público es un dato
# que se puede leer de un vistazo y probar, en vez de una condición
# repartida entre el ruteo y el punto de entrada.
PUBLICAS = frozenset({
    ("GET", "/inscripciones/formulario"),
    ("POST", "/inscripciones"),
    # R4.3 — pedir y comprobar el código de verificación son parte del mismo
    # formulario público: si necesitaran token, la verificación sería
    # imposible desde la landing. Las dos están protegidas por su propio
    # límite de tasa y por el desafío anti-automatización.
    ("POST", "/inscripciones/verificacion"),
    ("POST", "/inscripciones/verificacion/comprobar"),
    # R6.1.a — las tres rutas con las que se entra al portal son públicas
    # por definición: quien las usa todavía no tiene sesión. Su protección
    # no es el token sino el límite de tasa y que la respuesta no revele
    # nada —la misma exista o no el correo, la misma sea cual sea el motivo
    # por el que una credencial no entra—.
    ("POST", "/portal/clave/enlace"),   # «creá» o «recuperá» mi contraseña
    ("POST", "/portal/clave"),          # la fijo con el token del enlace
    ("POST", "/portal/sesion/clave"),   # entro con correo y contraseña
})

# R6.2 — las rutas del portal se autentican contra Firebase Auth pero **no**
# contra el padrón interno: un panelista no es un usuario de la aplicación
# de administración. `main.py` consulta esto para elegir con qué resolución
# de actor entrar, y el prefijo es seguro acá —a diferencia de `PUBLICAS`—
# porque no abre nada: una ruta nueva bajo `/portal/` sigue exigiendo token
# y sigue resolviendo a la persona por su vínculo.
PREFIJO_PORTAL = "/portal/"


def es_del_portal(camino):
    return ("/" + (camino or "").strip("/") + "/").startswith(PREFIJO_PORTAL)


def es_publica(metodo, camino):
    return (metodo.upper(), "/" + (camino or "").strip("/")) in PUBLICAS


def despachar(metodo, camino, cuerpo, consulta, actor, ctx):
    """Ejecuta la ruta. `ctx` trae las conexiones y el proveedor de embeddings."""
    funcion, permiso, params = resolver(metodo, camino)
    if permiso:
        actor.exigir(permiso)
    return funcion(ctx, actor, params, cuerpo or {}, consulta or {})


def _bandera(valor):
    """`?sin_panel=1`, `=true`, `=si`. En la query string todo es texto."""
    return str(valor or "").strip().lower() in ("1", "true", "si", "sí")


def _entero(valor, por_defecto=None):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return por_defecto


def _revision_pedida(cuerpo):
    """¿Esta llamada es la revisión previa (R7.2) y no la ejecución?

    La bandera viaja en el mismo cuerpo y la atiende la misma ruta a
    propósito: el resumen se arma del plan que se va a ejecutar, no de una
    reconstrucción paralela que pueda divergir. Es la única forma de que lo
    que la pantalla muestra sea lo que después pasa.
    """
    return bool((cuerpo or {}).get("solo_revisar"))


def _evidencia_normalizada(cuerpo):
    """La evidencia tal como la va a ver el alta, o `None` si no aplica.

    Se normaliza con la misma función que usa `crear_individuos`, así que el
    resumen muestra los valores afirmativos ya canónicos —los mismos contra
    los que se va a comparar— y no los que se tipearon.
    """
    if (cuerpo or {}).get("modo") != "crear_individuos":
        return None
    declarada = cuerpo.get("evidencia_consentimiento")
    if not declarada:
        return None
    try:
        return sav.normalizar_evidencia(declarada)
    except DatosInvalidos:
        # En la revisión, una evidencia incompleta no es motivo para no
        # mostrar el resto: el resumen existe para ver qué falta.
        return None


def _plan_de(cuerpo, **extra):
    """El mapeo que la pantalla confirmó, listo para congelar (R-ASYNC.1).

    Se arma una sola vez, al confirmar, y las tareas lo usan sin volver a
    interpretarlo. Si cada tarea re-resolviera qué variables son
    demográficas, dos tareas de la misma carga podrían usar mapeos distintos
    y el estudio quedaría con la mitad de las respuestas teniendo una
    pregunta que la otra mitad no.
    """
    cuerpo = cuerpo or {}
    plan = {
        "preguntas": cuerpo.get("preguntas") or [],
        "columna_id": (cuerpo.get("columna_id") or "id_en_origen"),
        "origen": cuerpo.get("origen"),
        "demograficas": cuerpo.get("demograficas"),
        "tipo_identificador": cuerpo.get("tipo_identificador"),
        # El modo y si se declaró la evidencia **no los usa ninguna tarea**:
        # las personas se crean en la ruta, antes de encolar. Se guardan para
        # que después se pueda contestar «¿con qué modo se corrió esta
        # carga?» mirando la carga, y no la memoria de quien la lanzó.
        #
        # Que no estuvieran costó caro: con 1131 filas sin mapear no había
        # forma de distinguir «se eligió el modo equivocado» de «el modo no
        # llegó», y se investigó lo segundo durante un día.
        #
        # De la evidencia va el hecho, no el contenido: qué variable del
        # archivo la lleva es parte del plan de otra cosa, y el plan se
        # devuelve por API.
        "modo": cuerpo.get("modo") or "existen",
        "evidencia_declarada": bool(cuerpo.get("evidencia_consentimiento")),
        # Fase 8 — la configuración de normalización de la carga (la lista de
        # valores de no respuesta con la que se detectó). Las decisiones por
        # pregunta van adentro de cada pregunta.
        "normalizacion": _normalizacion_de(cuerpo),
    }
    plan.update(extra)
    return plan


def _con_constancia_de_dedup(plan, filas):
    """Deja en el plan si la carga crea personas sin clave de dedup.

    La revisión lo advierte y no lo frena (BUG_validacion_dedup_bloquea): hay
    cargas legítimas sin clave. Lo que sí tiene que quedar es la constancia de
    que se continuó igual, y queda en el plan porque el plan se guarda con la
    carga (D56) y es lo que `diferida.estado` devuelve con el resultado. Se
    calcula con la misma función que usa la revisión, así que lo que se
    registra es lo que se advirtió.
    """
    plan["sin_clave_de_dedup"] = resumen_ingesta.sin_clave_de_dedup(plan, filas)
    return plan


def _normalizacion_de(cuerpo):
    """`{valores_no_respuesta: [...]}` si la pantalla la mandó, o `None`."""
    lista = (cuerpo or {}).get("valores_no_respuesta")
    if not lista:
        return None
    return {"valores_no_respuesta": calidad_dato.lista_no_respuesta(lista)}


# ════════════════════════════════════════════════════════════════════
#  Panelistas
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/panelistas", "enrolar", requisito="R1.1, R1.2, R1.3")
def alta_panelista(ctx, actor, params, cuerpo, consulta):
    resultado = personas.alta(ctx.boveda, cuerpo, actor=actor.uid)
    ctx.boveda.commit()
    return (200 if resultado["estado"] != "revision" else 202), resultado


@ruta("GET", "/panelistas", "leer")
def listar_panelistas(ctx, actor, params, cuerpo, consulta):
    return 200, personas.listar(
        ctx.boveda,
        busqueda=consulta.get("q"),
        panel_id=_entero(consulta.get("panel_id")),
        limite=min(_entero(consulta.get("limite"), 50) or 50, 200),
        desplazamiento=_entero(consulta.get("desde"), 0) or 0,
        # R3.13.e — los que entraron por una carga externa y no son miembros
        # de ningún panel. Sin filtro se mezclan con el resto.
        sin_panel=_bandera(consulta.get("sin_panel")),
        # R-ORG.5 — los que provienen de una carga, creados o reutilizados.
        carga_id=_entero(consulta.get("carga_id")),
    )


@ruta("GET", "/panelistas/<id_persona>", "leer")
def ficha_panelista(ctx, actor, params, cuerpo, consulta):
    """Abrir una ficha es ver la PII de una persona identificada: es una
    reidentificación deliberada y queda registrada (P1)."""
    ficha = personas.ficha(ctx.boveda, params["id_persona"])
    auditoria.registrar_reidentificacion(
        ctx.boveda, [params["id_persona"]], actor=actor, motivo="ficha",
        contexto={"ruta": "GET /panelistas/{id_persona}"},
    )
    ctx.boveda.commit()
    return 200, ficha


@ruta("PATCH", "/panelistas/<id_persona>", "enrolar")
def editar_panelista(ctx, actor, params, cuerpo, consulta):
    """Corrige los datos de una persona ya enrolada. Solo los campos que
    vengan en el cuerpo; un valor vacío borra el dato."""
    resultado = personas.editar(
        ctx.boveda, params["id_persona"], cuerpo, actor=actor.uid
    )
    ctx.boveda.commit()
    return 200, resultado


@ruta("POST", "/panelistas/<id_persona>/alias", "enrolar")
def agregar_alias_panelista(ctx, actor, params, cuerpo, consulta):
    resultado = personas.agregar_alias(
        ctx.boveda, params["id_persona"], cuerpo.get("origen"),
        cuerpo.get("id_en_origen"),
    )
    ctx.boveda.commit()
    return 201, resultado


@ruta("DELETE", "/panelistas/<id_persona>/alias/<origen>/<id_en_origen>", "enrolar")
def quitar_alias_panelista(ctx, actor, params, cuerpo, consulta):
    resultado = personas.quitar_alias(
        ctx.boveda, params["id_persona"], params["origen"], params["id_en_origen"]
    )
    ctx.boveda.commit()
    return 200, resultado


# ════════════════════════════════════════════════════════════════════
#  Altas en revisión (dedup ambiguo)
# ════════════════════════════════════════════════════════════════════

@ruta("GET", "/revisiones", "leer", requisito="R1.2")
def listar_revisiones(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": revision.listar(ctx.boveda, consulta.get("estado", "pendiente"))}


@ruta("POST", "/revisiones/<revision_id>/resolver", "resolver_revision", requisito="R1.2")
def resolver_revision(ctx, actor, params, cuerpo, consulta):
    resultado = revision.resolver(
        ctx.boveda,
        _entero(params["revision_id"]),
        cuerpo.get("decision"),
        cuerpo.get("id_persona"),
        actor=actor.uid,
    )
    ctx.boveda.commit()
    return 200, resultado


# ════════════════════════════════════════════════════════════════════
#  Paneles y membresías
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/paneles", "gestionar_paneles", requisito="R1.4")
def crear_panel(ctx, actor, params, cuerpo, consulta):
    resultado = paneles.crear(ctx.boveda, cuerpo.get("nombre"), cuerpo.get("descripcion"))
    ctx.boveda.commit()
    return 201, resultado


@ruta("GET", "/paneles", "leer")
def listar_paneles(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": paneles.listar(ctx.boveda)}


@ruta("GET", "/paneles/<panel_id>", "leer")
def ver_panel(ctx, actor, params, cuerpo, consulta):
    return 200, paneles.obtener(ctx.boveda, _entero(params["panel_id"]))


@ruta("PATCH", "/paneles/<panel_id>", "gestionar_paneles")
def cambiar_panel(ctx, actor, params, cuerpo, consulta):
    resultado = paneles.archivar(
        ctx.boveda, _entero(params["panel_id"]), cuerpo.get("estado", "archivado")
    )
    ctx.boveda.commit()
    return 200, resultado


@ruta("GET", "/paneles/<panel_id>/miembros", "leer", requisito="R1.4")
def listar_miembros(ctx, actor, params, cuerpo, consulta):
    return 200, {
        "items": paneles.listar_miembros(
            ctx.boveda, _entero(params["panel_id"]), consulta.get("estado", "activo")
        )
    }


@ruta("POST", "/paneles/<panel_id>/miembros", "gestionar_paneles", requisito="R1.4")
def agregar_miembros(ctx, actor, params, cuerpo, consulta):
    panel_id = _entero(params["panel_id"])
    ids = cuerpo.get("ids_persona") or (
        [cuerpo["id_persona"]] if cuerpo.get("id_persona") else []
    )
    agregados = [paneles.agregar_miembro(ctx.boveda, panel_id, i) for i in ids]
    ctx.boveda.commit()
    return 200, {"agregados": agregados}


@ruta("DELETE", "/paneles/<panel_id>/miembros/<id_persona>", "gestionar_paneles")
def quitar_miembro(ctx, actor, params, cuerpo, consulta):
    resultado = paneles.dar_de_baja_miembro(
        ctx.boveda, _entero(params["panel_id"]), params["id_persona"]
    )
    ctx.boveda.commit()
    return 200, resultado


# ════════════════════════════════════════════════════════════════════
#  Consentimiento y cumplimiento
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/consentimientos/<id_persona>", "enrolar", requisito="R1.3")
def otorgar_consentimiento(ctx, actor, params, cuerpo, consulta):
    resultado = consentimiento.otorgar(
        ctx.boveda,
        params["id_persona"],
        cuerpo.get("finalidad"),
        cuerpo.get("version_texto"),
        cuerpo.get("panel_id"),
    )
    ctx.boveda.commit()
    return 201, resultado


@ruta("POST", "/consentimientos/<id_persona>/retiro", "cumplimiento", requisito="R1.3")
def retirar_consentimiento(ctx, actor, params, cuerpo, consulta):
    resultado = bajas.retirar(
        ctx.boveda,
        params["id_persona"],
        cuerpo.get("finalidad", bajas.TODAS),
        actor=actor.uid,
        conn_semantica=ctx.semantica,
    )
    ctx.boveda.commit()
    return 200, resultado


@ruta("GET", "/cumplimiento/pendientes", "cumplimiento")
def pendientes_cumplimiento(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": bajas.pendientes_de_borrado_semantica(ctx.boveda)}


@ruta("GET", "/cumplimiento/borrados", "cumplimiento", requisito="R5.3")
def borrados_sin_confirmar(ctx, actor, params, cuerpo, consulta):
    """Las bajas que algún consumidor todavía no confirmó haber ejecutado.

    Es la vista del DPO, y es distinta de `/cumplimiento/pendientes`: ésa
    mira el store semántico de `paneles`, ésta mira a **todos** los sistemas
    registrados. Una baja abierta hace mucho tiempo no es una tarea atrasada,
    es un incumplimiento.
    """
    items = bajas.sin_confirmar(ctx.boveda)
    return 200, {
        "items": items,
        # El número que el DPO mira primero: hace cuánto está abierta la más
        # vieja. Si sube, hay un consumidor que dejó de confirmar.
        "mas_vieja_dias": max((i["dias_abierto"] for i in items), default=0),
    }


@ruta("POST", "/cumplimiento/reintentar", "cumplimiento")
def reintentar_cumplimiento(ctx, actor, params, cuerpo, consulta):
    resultado = bajas.reintentar_borrado_semantica(ctx.boveda, ctx.semantica)
    ctx.boveda.commit()
    return 200, {"resultados": resultado}


# ════════════════════════════════════════════════════════════════════
#  Encuestas, fielding e ingesta
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/encuestas", "fieldear", requisito="R1.5")
def crear_encuesta(ctx, actor, params, cuerpo, consulta):
    resultado = encuestas.crear(
        ctx.boveda, _entero(cuerpo.get("panel_id")), cuerpo.get("nombre"),
        cuerpo.get("fecha_campo"),
    )
    ctx.boveda.commit()
    return 201, resultado


@ruta("GET", "/encuestas", "leer")
def listar_encuestas(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": encuestas.listar(ctx.boveda, _entero(consulta.get("panel_id")))}


@ruta("GET", "/encuestas/<encuesta_id>", "leer")
def ver_encuesta(ctx, actor, params, cuerpo, consulta):
    return 200, encuestas.obtener(ctx.boveda, _entero(params["encuesta_id"]))


@ruta("PATCH", "/encuestas/<encuesta_id>", "fieldear")
def cambiar_encuesta(ctx, actor, params, cuerpo, consulta):
    resultado = encuestas.cambiar_estado(
        ctx.boveda, _entero(params["encuesta_id"]), cuerpo.get("estado")
    )
    ctx.boveda.commit()
    return 200, resultado


@ruta("POST", "/encuestas/<encuesta_id>/convocatoria", "fieldear", requisito="R1.5")
def convocar(ctx, actor, params, cuerpo, consulta):
    resultado = encuestas.convocar(
        ctx.boveda,
        _entero(params["encuesta_id"]),
        cuerpo.get("ids_persona"),
        todo_el_panel=bool(cuerpo.get("todo_el_panel")),
    )
    ctx.boveda.commit()
    return 200, resultado


@ruta("GET", "/encuestas/<encuesta_id>/participacion", "leer")
def ver_participacion(ctx, actor, params, cuerpo, consulta):
    return 200, {
        "items": encuestas.listar_participacion(ctx.boveda, _entero(params["encuesta_id"]))
    }


@ruta("POST", "/encuestas/<encuesta_id>/ingesta", "ingestar",
      requisito="R1.5, R-ASYNC.1")
def ingestar_encuesta(ctx, actor, params, cuerpo, consulta):
    """Confirma la carga: la guarda, la parte en lotes y los encola.

    **No procesa nada acá.** Responde `202` con el trabajo recién creado y la
    pantalla pasa a mostrar progreso. Antes esto corría la ingesta entera
    adentro de la request y con una base real cortaba por timeout, a mitad de
    camino y sin forma de retomar.
    """
    encuesta_id = _entero(params["encuesta_id"])
    encuesta = encuestas.obtener(ctx.boveda, encuesta_id)   # que exista, antes de guardar nada
    plan = _plan_de(cuerpo)
    filas = cuerpo.get("filas") or []
    if _revision_pedida(cuerpo):
        return 200, resumen_ingesta.resumir(
            plan, filas, panel=encuesta.get("panel_id"),
            evidencia=_evidencia_normalizada(cuerpo),
            destino_tipo="encuesta", nombre_destino=encuesta.get("nombre"))
    salida = diferida.encolar(
        ctx.boveda, "encuesta", encuesta_id, plan, filas,
        actor=actor, encolador=ctx.encolador)
    return 202, salida


@ruta("GET", "/encuestas/<encuesta_id>/muestra", "leer", requisito="R3.12")
def exportar_muestra(ctx, actor, params, cuerpo, consulta):
    """La muestra de la ola para precargar en la plataforma de campo.

    Seudónima por defecto: solo `id_persona`, que es lo que hace falta para
    que el identificador del sistema viaje al campo y vuelva en el archivo.

    `con_contacto=1` la convierte en una reidentificación —nombre, documento,
    correo— y por eso exige el permiso de exportar identificado y queda
    registrada, igual que R3.10. Que el equipo de campo necesite llamar a la
    gente no lo hace un caso distinto: lo que sale es PII.
    """
    encuesta_id = _entero(params["encuesta_id"])
    con_contacto = str(consulta.get("con_contacto") or "").lower() in ("1", "true", "si", "sí")

    if con_contacto:
        actor.exigir("exportar_identificado")

    muestra = encuestas.exportar_muestra(
        ctx.boveda, encuesta_id, con_contacto=con_contacto)

    if con_contacto:
        auditoria.registrar_reidentificacion(
            ctx.boveda, muestra["ids_persona"], actor=actor,
            motivo="exportacion",
            contexto={"ruta": f"GET /encuestas/{encuesta_id}/muestra",
                      "personas": muestra["personas"],
                      "para": "precarga de campo"},
        )
        ctx.boveda.commit()

    # La lista de ids ya va en el CSV; repetirla en el JSON solo hace el
    # cuerpo más grande.
    muestra.pop("ids_persona", None)
    return 200, muestra


@ruta("GET", "/encuestas/<encuesta_id>/cruce", "leer", requisito="R1.5, R1.6")
def verificar_cruce(ctx, actor, params, cuerpo, consulta):
    return 200, encuestas.verificar_cruce(
        ctx.boveda, ctx.semantica, _entero(params["encuesta_id"])
    )


# ════════════════════════════════════════════════════════════════════
#  Auditoría del guardrail de PII
# ════════════════════════════════════════════════════════════════════

@ruta("GET", "/auditoria/pii", "leer", requisito="R1.6")
def auditoria_pii(ctx, actor, params, cuerpo, consulta):
    """Auditoría en vivo: columnas de PII en el store semántico. Debe dar 0."""
    hallazgos = semantica.auditar_columnas(ctx.semantica)
    return (200 if not hallazgos else 500), {
        "limpio": not hallazgos,
        "hallazgos": hallazgos,
    }


# ════════════════════════════════════════════════════════════════════
#  Consultas (R2.4, R2.5, R2.7 a R2.11 + P1)
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/consultas", "consultar",
      requisito="R2.4, R2.5, R2.7, R2.8, R2.9, R2.10, R2.11")
def correr_consulta(ctx, actor, params, cuerpo, consulta):
    """Corre una consulta. El resultado identifica por `id_persona`: no sale
    PII de acá.

    Con `formato=csv` devuelve el mismo ranking serializado en CSV dentro del
    JSON (`{formato, nombre_archivo, csv}`) en vez de un cuerpo `text/csv`.
    Es a propósito: así el contrato de la API sigue siendo «JSON siempre», el
    router sigue devolviendo dicts y se puede probar sin HTTP. El navegador
    arma la descarga con eso.
    """
    resultado = consultas.ejecutar(ctx, cuerpo)
    formato = (cuerpo.get("formato") or consulta.get("formato") or "json").lower()
    if formato == "csv":
        return 200, {
            "formato": "csv",
            "nombre_archivo": "consulta.csv",
            "filas": resultado["total"],
            "csv": consultas.a_csv(resultado),
            "puente": resultado["puente"],
            "degradaciones": resultado["degradaciones"],
            "diagnostico": resultado["diagnostico"],
        }
    return 200, resultado


@ruta("GET", "/consultas/guardadas", "consultar", requisito="P1")
def listar_consultas_guardadas(ctx, actor, params, cuerpo, consulta):
    return 200, {
        "items": consultas.listar_guardadas(
            ctx.boveda, _entero(consulta.get("panel_id"))
        )
    }


@ruta("POST", "/consultas/guardadas", "consultar", requisito="P1")
def guardar_consulta(ctx, actor, params, cuerpo, consulta):
    resultado = consultas.guardar(
        ctx.boveda, cuerpo.get("nombre"), cuerpo.get("definicion") or cuerpo,
        descripcion=cuerpo.get("descripcion"), actor=actor.uid,
    )
    ctx.boveda.commit()
    return 201, resultado


@ruta("GET", "/consultas/guardadas/<consulta_id>", "consultar", requisito="P1")
def ver_consulta_guardada(ctx, actor, params, cuerpo, consulta):
    return 200, consultas.obtener_guardada(ctx.boveda, _entero(params["consulta_id"]))


@ruta("GET", "/consultas/capturas/<ejecucion_id>", "depurar_verificacion",
      requisito="R-VER.10")
def capturas_de_verificacion(ctx, actor, params, cuerpo, consulta):
    """El intercambio con Claude de una consulta ya ejecutada: lo que se
    mandó y lo que volvió, por lote y por intento.

    Solo admin (`depurar_verificacion`): son respuestas de encuesta en
    bruto, y aunque no lleven identificadores no son para cualquiera que
    pueda consultar. Si no hay nada, se dice por qué en vez de devolver una
    lista vacía que parezca «no hubo intercambio»."""
    ejecucion_id = (params.get("ejecucion_id") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", ejecucion_id):
        raise DatosInvalidos("`ejecucion_id` no es un identificador de ejecución.")
    capturas = mod_verificacion.capturas_de(ctx.semantica, ejecucion_id)
    salida = {
        "ejecucion_id": ejecucion_id,
        "modo_activo_ahora": mod_verificacion.depuracion_activa(),
        "dias_de_retencion": mod_verificacion.DIAS_DE_CAPTURA,
        "capturas": capturas,
    }
    if not capturas:
        salida["explicacion"] = (
            "No hay capturas para esta ejecución. O el modo de depuración "
            "(VERIFICACION_DEPURACION) estaba apagado cuando corrió, o ya "
            f"vencieron ({mod_verificacion.DIAS_DE_CAPTURA} días), o la consulta "
            "no llegó a llamar a la API.")
    return 200, salida


@ruta("DELETE", "/consultas/guardadas/<consulta_id>", "consultar", requisito="P1")
def borrar_consulta_guardada(ctx, actor, params, cuerpo, consulta):
    resultado = consultas.borrar_guardada(ctx.boveda, _entero(params["consulta_id"]))
    ctx.boveda.commit()
    return 200, resultado


# ════════════════════════════════════════════════════════════════════
#  Reidentificación (P1) — id_persona → PII, con registro
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/reidentificacion", "reidentificar", requisito="P1")
def reidentificar(ctx, actor, params, cuerpo, consulta):
    """Traduce una lista de `id_persona` a datos de contacto.

    Es el paso que convierte un ranking en una convocatoria, y el único lugar
    de la Fase 2 donde sale PII. Cada traducción queda registrada con autor,
    fecha y motivo antes de devolver la respuesta.
    """
    ids = cuerpo.get("ids_persona") or []
    if not ids:
        raise DatosInvalidos("Hace falta al menos un id_persona.")
    resultado = personas.reidentificar(ctx.boveda, ids)
    auditoria.registrar_reidentificacion(
        ctx.boveda, [i["id_persona"] for i in resultado["items"]], actor=actor,
        motivo=(cuerpo.get("motivo") or "consulta"),
        contexto={"ruta": "POST /reidentificacion", "pedidos": len(ids)},
    )
    ctx.boveda.commit()
    return 200, resultado


@ruta("GET", "/reidentificacion", "cumplimiento", requisito="P1")
def listar_reidentificaciones(ctx, actor, params, cuerpo, consulta):
    """El registro de reidentificaciones. Lo lee cumplimiento (admin / dpo):
    es la evidencia de que el puente entre stores se usa y se controla."""
    return 200, {
        "items": auditoria.listar_reidentificaciones(
            ctx.boveda,
            id_persona=consulta.get("id_persona"),
            actor_uid=consulta.get("actor_uid"),
            limite=min(_entero(consulta.get("limite"), 200) or 200, 1000),
        )
    }


# ════════════════════════════════════════════════════════════════════
#  Composición y universo de referencia (R2.2, R2.3 + P1)
# ════════════════════════════════════════════════════════════════════

def _momento(ctx, consulta):
    """R4.1.a — a qué fecha se pide la composición.

    Se puede pedir por fecha (`?momento=2025-03-01`) o por ola
    (`?encuesta=12`), que es como lo piensa un analista: no «al 1 de marzo»
    sino «como estaba cuando salimos a campo con esa encuesta». La fecha de
    campo de la encuesta es la que manda; si no la tiene cargada se usa cuándo
    se creó, que es lo más cerca que hay.
    """
    if consulta.get("momento"):
        return str(consulta["momento"])
    if not consulta.get("encuesta"):
        return None
    fila = db.una(
        ctx.boveda,
        "select nombre, fecha_campo, creado_en from encuesta where id = %s",
        (_entero(consulta["encuesta"]),))
    if not fila:
        raise NoEncontrado(f"No existe la encuesta {consulta['encuesta']}.")
    return str(fila["fecha_campo"] or fila["creado_en"])


def _dimensiones_y_cruce(consulta):
    dimensiones = consulta.get("dimensiones")
    if isinstance(dimensiones, str):
        dimensiones = [d for d in dimensiones.split(",") if d.strip()]
    cruce_de = consulta.get("cruce")
    if isinstance(cruce_de, str):
        cruce_de = [d.strip() for d in cruce_de.split(",") if d.strip()]
    if cruce_de and len(cruce_de) != 2:
        raise DatosInvalidos(
            "El cruce necesita exactamente dos dimensiones, separadas por coma "
            "(por ejemplo `cruce=sexo,tramo_etario`)."
        )
    return dimensiones, cruce_de


@ruta("GET", "/composicion", "leer", requisito="R-ORG.2, R-ORG.3")
def ver_composicion_de_ambito(ctx, actor, params, cuerpo, consulta):
    """La composición de un ámbito: `ambito=todos`, `ambito=panel&panel_id=…`
    o `ambito=carga&carga_id=…`. El ámbito vuelve en la respuesta, junto al
    resultado, para que la pantalla diga qué se está mirando."""
    dimensiones, cruce_de = _dimensiones_y_cruce(consulta)
    ambito = (consulta.get("ambito") or "panel").strip().lower()
    referencia = _entero(consulta.get("carga_id" if ambito == "carga"
                                      else "panel_id"))
    return 200, composicion.composicion_de_ambito(
        ctx.boveda, ambito, referencia,
        dimensiones=dimensiones or None,
        cruce_de=cruce_de or None,
        estado=consulta.get("estado", "activo"),
        momento=_momento(ctx, consulta),
    )


@ruta("GET", "/composicion/todos/objetivo", "leer", requisito="R-ORG.3")
def ver_objetivo_de_todos(ctx, actor, params, cuerpo, consulta):
    return 200, composicion.obtener_objetivo_de_todos(ctx.boveda)


@ruta("PUT", "/composicion/todos/objetivo", "gestionar_paneles", requisito="R-ORG.3")
def cargar_objetivo_de_todos(ctx, actor, params, cuerpo, consulta):
    """El universo de referencia de toda la bóveda. Mismo permiso que el de
    un panel: es la misma decisión, sobre otro conjunto."""
    resultado = composicion.guardar_objetivo_de_todos(
        ctx.boveda, cuerpo.get("objetivos") or cuerpo.get("items") or [])
    ctx.boveda.commit()
    return 200, resultado


@ruta("DELETE", "/composicion/todos/objetivo", "gestionar_paneles", requisito="R-ORG.3")
def borrar_objetivo_de_todos(ctx, actor, params, cuerpo, consulta):
    resultado = composicion.borrar_objetivo_de_todos(
        ctx.boveda, consulta.get("dimension"))
    ctx.boveda.commit()
    return 200, resultado


@ruta("GET", "/paneles/<panel_id>/composicion", "leer", requisito="R2.3")
def ver_composicion(ctx, actor, params, cuerpo, consulta):
    dimensiones, cruce_de = _dimensiones_y_cruce(consulta)
    return 200, composicion.composicion(
        ctx.boveda, _entero(params["panel_id"]),
        dimensiones=dimensiones or None,
        estado=consulta.get("estado", "activo"),
        cruce_de=cruce_de or None,
        momento=_momento(ctx, consulta),
    )


@ruta("GET", "/paneles/<panel_id>/objetivo", "leer", requisito="R2.2")
def ver_objetivo(ctx, actor, params, cuerpo, consulta):
    return 200, composicion.obtener_objetivo(ctx.boveda, _entero(params["panel_id"]))


@ruta("PUT", "/paneles/<panel_id>/objetivo", "gestionar_paneles", requisito="R2.2")
def cargar_objetivo(ctx, actor, params, cuerpo, consulta):
    resultado = composicion.guardar_objetivo(
        ctx.boveda, _entero(params["panel_id"]),
        cuerpo.get("objetivos") or cuerpo.get("items") or [],
    )
    ctx.boveda.commit()
    return 200, resultado


@ruta("DELETE", "/paneles/<panel_id>/objetivo", "gestionar_paneles", requisito="R2.2")
def borrar_objetivo(ctx, actor, params, cuerpo, consulta):
    resultado = composicion.borrar_objetivo(
        ctx.boveda, _entero(params["panel_id"]), consulta.get("dimension")
    )
    ctx.boveda.commit()
    return 200, resultado


# ════════════════════════════════════════════════════════════════════
#  Participación (R2.1, R2.6)
# ════════════════════════════════════════════════════════════════════

@ruta("GET", "/paneles/<panel_id>/participacion", "leer", requisito="R2.1, R2.6")
def tablero_participacion(ctx, actor, params, cuerpo, consulta):
    return 200, participacion.tablero(
        ctx.boveda, _entero(params["panel_id"]),
        estado=consulta.get("estado", "activo"),
    )


@ruta("GET", "/participacion/olas", "leer", requisito="R2.1")
def participacion_por_ola(ctx, actor, params, cuerpo, consulta):
    return 200, {
        "items": participacion.por_ola(ctx.boveda, _entero(consulta.get("panel_id")))
    }


# ════════════════════════════════════════════════════════════════════
#  R2.12 — Gestión de usuarios de la app (solapa Configuración)
#  Todas exigen `gestionar_usuarios`, que solo tiene `admin`.
# ════════════════════════════════════════════════════════════════════

@ruta("GET", "/usuarios", "gestionar_usuarios", requisito="R2.12")
def listar_usuarios(ctx, actor, params, cuerpo, consulta):
    return 200, usuarios.listar(ctx.padron)


@ruta("POST", "/usuarios", "gestionar_usuarios", requisito="R2.12")
def alta_usuario(ctx, actor, params, cuerpo, consulta):
    resultado = usuarios.alta(ctx.boveda, ctx.padron, cuerpo, actor)
    ctx.boveda.commit()
    return (201 if resultado["estado"] == "creado" else 200), resultado


@ruta("PATCH", "/usuarios/<uid>", "gestionar_usuarios", requisito="R2.12")
def cambiar_usuario(ctx, actor, params, cuerpo, consulta):
    resultado = usuarios.cambiar(ctx.boveda, ctx.padron, params["uid"], cuerpo, actor)
    ctx.boveda.commit()
    return 200, resultado


@ruta("POST", "/usuarios/<uid>/acceso", "gestionar_usuarios", requisito="R2.12")
def acceso_usuario(ctx, actor, params, cuerpo, consulta):
    """Un enlace nuevo para que la persona fije su clave.

    Existe porque el del alta se muestra una vez y no se guarda: si se
    perdió, no se recupera, se genera otro. `POST` y no `GET` porque tiene
    efecto —emite una credencial de un solo uso y escribe auditoría—, y un
    `GET` con efectos es algo que un prefetch o un bot de enlaces puede
    disparar solo.
    """
    resultado = usuarios.generar_acceso(ctx.boveda, ctx.padron, params["uid"], actor)
    ctx.boveda.commit()
    return 200, resultado


@ruta("GET", "/usuarios/auditoria", "gestionar_usuarios", requisito="R2.12")
def auditoria_usuarios(ctx, actor, params, cuerpo, consulta):
    return 200, {
        **usuarios.historial(
            ctx.boveda, consulta.get("uid"),
            min(_entero(consulta.get("limite"), 200) or 200, 1000),
        ),
        # El catálogo viaja con la auditoría para que la pantalla pueda
        # explicar qué significa cada acción sin repetirlo del lado del
        # navegador.
        "acciones": usuarios.acciones_auditables(ctx.boveda),
    }


# ════════════════════════════════════════════════════════════════════
#  Ingesta diferida — ver el avance y recuperar lo que falló
# ════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
#  Fase 7 — estadísticas, columnas y la ficha desde un resultado
# ════════════════════════════════════════════════════════════════════

@ruta("GET", "/estadisticas", "leer", requisito="R7.5")
def ver_estadisticas(ctx, actor, params, cuerpo, consulta):
    """Los números de los dos stores, sin pedir parámetros.

    Son agregados: esta pantalla no muestra datos de ninguna persona en
    particular. Ver el detalle de alguien sigue siendo la ficha, que es lo
    que queda registrado.
    """
    return 200, estadisticas.todo(ctx.boveda, ctx.semantica)


@ruta("GET", "/estadisticas/sin-respuestas", "leer", requisito="R7.5")
def panelistas_sin_respuestas(ctx, actor, params, cuerpo, consulta):
    """El desglose del número que más se mira: quiénes son.

    Devuelve `id_persona`, no personas: es la misma lista seudónima que
    devuelve una consulta, y reidentificarla sigue siendo otra acción.
    """
    detalle = estadisticas.brecha(
        ctx.boveda, ctx.semantica,
        ejemplos=_entero(consulta.get("limite"), 500))
    return 200, detalle["panelistas_sin_respuestas"]


@ruta("GET", "/atributos/columnas", "leer", requisito="R7.4")
def columnas_disponibles(ctx, actor, params, cuerpo, consulta):
    """Qué atributos se pueden poner como columna de la lista de resultados.

    Los de categoría especial no están, y es a propósito: ver uno en una
    ficha no es lo mismo que verlos todos en una planilla.
    """
    return 200, {"items": atributos.columnas_ofrecibles(ctx.boveda) + [
        # R-ORG.5 — de qué estudio salió cada persona. No es un atributo:
        # sale del vínculo persona ↔ carga y de los datos de la carga, y por
        # eso no se puede filtrar ni fijar cuota con ella. Solo se mira.
        {"clave": cargas.COLUMNA_ESTUDIO_DE_ORIGEN,
         "etiqueta": "Estudio de origen", "tipo": "procedencia"},
    ]}


@ruta("POST", "/resultados/atributos", "leer", requisito="R7.4")
def atributos_de_resultados(ctx, actor, params, cuerpo, consulta):
    """Los atributos de un conjunto de resultados, **en una sola consulta**.

    Agregar una columna no re-ejecuta la consulta semántica: se resuelve
    sobre los `id_persona` que ya están en pantalla. Y se resuelve de a
    todos: de a uno, una lista de 200 dispara 200 consultas.
    """
    ids = (cuerpo or {}).get("ids_persona") or []
    valores = atributos.valores_de_varias(ctx.boveda, ids)
    # R-ORG.5 — el estudio de origen como una columna más. Va en la misma
    # llamada: sigue siendo una sola ida a la base por tanda de resultados.
    for id_persona, origen in cargas.origen_de_varias(ctx.boveda, ids).items():
        valores.setdefault(id_persona, {})[cargas.COLUMNA_ESTUDIO_DE_ORIGEN] = origen
    return 200, {"items": valores}


@ruta("GET", "/mi/preferencias", "leer", requisito="R7.4")
def mis_preferencias(ctx, actor, params, cuerpo, consulta):
    ficha_usuario = ctx.padron.leer_ficha(actor.uid) or {}
    return 200, {"columnas_resultado": ficha_usuario.get("columnas_resultado") or []}


@ruta("PUT", "/mi/preferencias", "leer", requisito="R7.4")
def guardar_mis_preferencias(ctx, actor, params, cuerpo, consulta):
    """La selección de columnas, que se recuerda de una consulta a la otra.

    Va en la ficha del usuario y se escribe con `merge`: la preferencia de
    una pantalla no puede pisar el rol de nadie.
    """
    columnas = [str(c) for c in ((cuerpo or {}).get("columnas_resultado") or [])]
    ctx.padron.escribir_ficha(actor.uid, {"columnas_resultado": columnas})
    return 200, {"columnas_resultado": columnas}


# ════════════════════════════════════════════════════════════════════
#  Fase 7 — la ficha del panelista desde un resultado
# ════════════════════════════════════════════════════════════════════

@ruta("GET", "/panelistas/<id_persona>/ficha", "leer", requisito="R7.3")
def ficha_seudonima(ctx, actor, params, cuerpo, consulta):
    """Los atributos de la persona, **sin un solo dato identificatorio**.

    Permiso `leer` y no uno nuevo: es la misma información demográfica que
    ya devuelve una consulta con filtros, vista de a una persona. Lo que
    sigue necesitando una acción deliberada —y queda registrado— es ver
    quién es: eso es reidentificar, y no pasa por acá.
    """
    salida = ficha.seudonima(
        ctx.boveda, params["id_persona"], momento=consulta.get("momento"))
    # R7.3 — la evidencia del resultado que se está mirando. Llega como los
    # `respuesta_id` que ya trae el ranking (`?respuestas=12,34`); sin ellos
    # la ficha es solo la de la persona y no abre el store semántico.
    ids = ficha.ids_de_evidencia(consulta.get("respuestas"))
    if ids:
        salida["evidencia"] = ficha.evidencia(
            ctx.semantica, params["id_persona"], ids)
    return 200, salida


@ruta("GET", "/panelistas/<id_persona>/respuestas", "leer", requisito="R7.6")
def respuestas_del_panelista(ctx, actor, params, cuerpo, consulta):
    """Qué respondió esta persona, de qué estudio y cuándo.

    **Esto cruza los dos stores a propósito** y por eso deja rastro: la
    arquitectura los mantiene separados para que nadie vea identidad y
    contenido juntos por accidente, y esta pantalla los junta porque operar
    lo necesita. El registro usa su motivo propio
    (`respuestas_panelista`), que lo distingue de reidentificar un contacto.
    """
    return 200, ficha.respuestas_con_registro(
        ctx.boveda, ctx.semantica, params["id_persona"], actor=actor,
        ref_estudio=(consulta.get("ref_estudio") or None),
        busqueda=consulta.get("q"),
        pagina=_entero(consulta.get("pagina"), 1),
        tamano=_entero(consulta.get("tamano"), ficha.PAGINA_POR_DEFECTO))


@ruta("GET", "/panelistas/<id_persona>/respuestas/estudios", "leer",
      requisito="R7.6")
def estudios_del_panelista(ctx, actor, params, cuerpo, consulta):
    """Los estudios donde esta persona tiene respuestas, para el filtro.

    Sin registro: es un conteo por estudio, no el contenido. Lo que se
    audita es ver qué respondió, no saber que respondió algo.
    """
    return 200, {"items": ficha.estudios_con_respuestas(
        ctx.semantica, params["id_persona"])}


@ruta("GET", "/ingestas", "leer", requisito="R-ASYNC.3")
def listar_ingestas(ctx, actor, params, cuerpo, consulta):
    """Las cargas recientes, opcionalmente las de un destino.

    Es lo que deja volver a la pantalla y encontrar la carga de ayer: el
    estado vive en la base, no en la pestaña que la lanzó.
    """
    return 200, {"items": diferida.listar(
        ctx.boveda,
        destino_tipo=consulta.get("destino_tipo"),
        destino_id=_entero(consulta.get("destino_id")),
        limite=_entero(consulta.get("limite"), 20))}


@ruta("GET", "/ingestas/<trabajo_id>", "leer", requisito="R-ASYNC.3")
def ver_ingesta(ctx, actor, params, cuerpo, consulta):
    """El avance de una carga, con sus lotes si se piden.

    Sale de la base, así que sobrevive a un refresco del navegador y a que
    la pestaña se haya cerrado hace una hora.
    """
    return 200, diferida.estado(
        ctx.boveda, _entero(params["trabajo_id"]),
        con_lotes=_bandera(consulta.get("lotes")))


@ruta("POST", "/ingestas/<trabajo_id>/reintentar", "ingestar",
      requisito="R-ASYNC.4")
def reintentar_ingesta(ctx, actor, params, cuerpo, consulta):
    """Vuelve a encolar los lotes fallidos. Sin rehacer la carga completa.

    Reprocesar un lote es seguro porque la ingesta es idempotente: el upsert
    por `(individuo, pregunta)`, la membresía por `(panel, persona)` y la
    participación por `(encuesta, persona)`. Lo que entró no se duplica.
    """
    return 200, diferida.reintentar(
        ctx.boveda, _entero(params["trabajo_id"]),
        indice=_entero((cuerpo or {}).get("indice")),
        encolador=ctx.encolador)


@ruta("GET", "/diagnostico/esquema", "leer")
def diagnostico_esquema(ctx, actor, params, cuerpo, consulta):
    """Qué migraciones están aplicadas en cada store y cuáles faltan.

    Las migraciones se aplican a mano contra cada instancia de Cloud SQL, así
    que una que no se aplicó no se nota hasta que alguien usa la pantalla que
    la necesitaba. Esta ruta lo hace visible antes de eso.
    """
    estado = esquema.revisar_stores(
        ctx.boveda, ctx.semantica,
        dims_proveedor=getattr(ctx.embeddings, "dims", None))
    return (200 if estado["completo"] else 500), estado


@ruta("GET", "/diagnostico/sav", "leer", requisito="R3.9")
def diagnostico_sav(ctx, actor, params, cuerpo, consulta):
    """Si la función puede leer `.sav`, y si no, qué paquete le falta.

    Mismo problema que el diagnóstico de esquema, en otra capa: la ingesta
    por SAV depende de dos paquetes que el despliegue puede no haber
    instalado, y eso no se nota hasta que alguien sube un archivo.
    """
    estado = sav.diagnostico()
    return (200 if estado["puede_leer_sav"] else 500), estado


# ════════════════════════════════════════════════════════════════════
#  Fase 3 · 3A — Salud del panel accionable
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/encuestas/<encuesta_id>/muestreo", "muestrear", requisito="R3.1")
def proponer_muestreo(ctx, actor, params, cuerpo, consulta):
    """Propone a quién invitar. No convoca: la propuesta se confirma aparte."""
    return 200, muestreo.proponer(
        ctx.boveda, int(params["encuesta_id"]),
        dimension=(cuerpo.get("dimension") or "sexo"),
        cantidad=cuerpo.get("cantidad", 100),
        estado=cuerpo.get("estado_membresia", "activo"),
    )


@ruta("GET", "/paneles/<panel_id>/umbrales-fatiga", "leer", requisito="R3.1")
def ver_umbrales(ctx, actor, params, cuerpo, consulta):
    return 200, muestreo.obtener_umbrales(ctx.boveda, int(params["panel_id"]))


@ruta("PUT", "/paneles/<panel_id>/umbrales-fatiga", "muestrear", requisito="R3.1")
def guardar_umbrales(ctx, actor, params, cuerpo, consulta):
    return 200, muestreo.guardar_umbrales(
        ctx.boveda, int(params["panel_id"]), cuerpo, actor
    )


@ruta("POST", "/encuestas/<encuesta_id>/calidad", "ingestar", requisito="R3.2")
def correr_calidad(ctx, actor, params, cuerpo, consulta):
    return 200, calidad.correr(
        ctx.boveda, ctx.semantica, int(params["encuesta_id"]), actor=actor
    )


@ruta("PATCH", "/participacion/<participacion_id>/calidad", "revisar_calidad",
      requisito="R3.2")
def revisar_calidad(ctx, actor, params, cuerpo, consulta):
    """Revierte o confirma una marca automática, con registro de quién."""
    return 200, calidad.revisar(
        ctx.boveda, int(params["participacion_id"]),
        (cuerpo.get("calidad_estado") or "").strip(), actor,
        motivo=cuerpo.get("motivo"),
    )


@ruta("GET", "/panelistas/<id_persona>/puntos", "leer", requisito="R3.3")
def ver_puntos(ctx, actor, params, cuerpo, consulta):
    return 200, puntos.estado_de_cuenta(ctx.boveda, params["id_persona"])


@ruta("POST", "/puntos/liquidar", "gamificacion", requisito="R3.4")
def liquidar_puntos(ctx, actor, params, cuerpo, consulta):
    encuesta_id = cuerpo.get("encuesta_id")
    if not encuesta_id:
        raise DatosInvalidos("Hace falta el id de la encuesta a liquidar.")
    return 200, puntos.liquidar(
        ctx.boveda, int(encuesta_id), cuerpo.get("ids_persona"), actor
    )


@ruta("POST", "/puntos/ajustar", "gamificacion", requisito="R3.3")
def ajustar_puntos(ctx, actor, params, cuerpo, consulta):
    return 200, puntos.ajustar(
        ctx.boveda, cuerpo.get("id_persona"), cuerpo.get("puntos"),
        cuerpo.get("motivo"), actor,
    )


@ruta("POST", "/puntos/vencer", "gamificacion", requisito="R3.3")
def vencer_puntos(ctx, actor, params, cuerpo, consulta):
    """Descuenta los lotes vencidos. Es idempotente: correrlo dos veces no
    descuenta dos veces, porque el lote queda marcado."""
    return 200, puntos.vencer(ctx.boveda, cuerpo.get("id_persona"))


@ruta("GET", "/premios", "leer", requisito="R3.5")
def listar_premios(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": premios.listar_premios(
        ctx.boveda, consulta.get("disponibles") in ("1", "true", "si")
    )}


@ruta("POST", "/premios", "gamificacion", requisito="R3.5")
def crear_premio(ctx, actor, params, cuerpo, consulta):
    return 201, premios.crear_premio(ctx.boveda, cuerpo)


@ruta("PATCH", "/premios/<premio_id>", "gamificacion", requisito="R3.5")
def editar_premio(ctx, actor, params, cuerpo, consulta):
    return 200, premios.editar_premio(ctx.boveda, int(params["premio_id"]), cuerpo)


@ruta("GET", "/canjes", "leer", requisito="R3.5")
def listar_canjes(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": premios.listar_canjes(
        ctx.boveda, consulta.get("id_persona"), consulta.get("estado")
    )}


@ruta("POST", "/canjes", "gamificacion", requisito="R3.5")
def crear_canje(ctx, actor, params, cuerpo, consulta):
    id_persona = cuerpo.get("id_persona")
    premio_id = cuerpo.get("premio_id")
    if not id_persona or not premio_id:
        raise DatosInvalidos("El canje necesita id_persona y premio_id.")
    return 201, premios.canjear(ctx.boveda, id_persona, int(premio_id), actor)


@ruta("PATCH", "/canjes/<canje_id>", "gamificacion", requisito="R3.5")
def resolver_canje(ctx, actor, params, cuerpo, consulta):
    return 200, premios.resolver(
        ctx.boveda, int(params["canje_id"]), (cuerpo.get("estado") or "").strip(),
        actor, nota=cuerpo.get("nota"),
    )


@ruta("GET", "/paneles/<panel_id>/bonos", "leer", requisito="R3.6")
def listar_bonos(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": puntos.listar_bonos(
        ctx.boveda, int(params["panel_id"]),
        consulta.get("vigentes") in ("1", "true", "si"),
    )}


@ruta("POST", "/paneles/<panel_id>/bonos", "gamificacion", requisito="R3.6")
def crear_bono(ctx, actor, params, cuerpo, consulta):
    return 201, puntos.crear_bono(
        ctx.boveda, int(params["panel_id"]), (cuerpo.get("dimension") or "").strip(),
        cuerpo.get("categoria"), cuerpo.get("puntos_extra"),
        hasta=cuerpo.get("hasta"), actor=actor,
    )


# ════════════════════════════════════════════════════════════════════
#  Fase 3 · 3B — Crecimiento
# ════════════════════════════════════════════════════════════════════

# `permiso=None` significa que la ruta no exige rol. En estas dos es
# deliberado y es lo que pide R3.7: la landing es pública. `main.py` las
# deja pasar sin token; ninguna de las dos lee ni devuelve datos de otros
# panelistas.
@ruta("GET", "/inscripciones/formulario", None, requisito="R3.7")
def formulario_inscripcion(ctx, actor, params, cuerpo, consulta):
    return 200, inscripciones.formulario(ctx.boveda)


@ruta("POST", "/inscripciones", None, requisito="R3.7")
def inscribirse(ctx, actor, params, cuerpo, consulta):
    """Recibe una inscripción del formulario público.

    Responde siempre lo mismo ante un envío válido: decir «ya estás
    inscripto» convertiría el formulario en un oráculo para averiguar quién
    es panelista probando documentos.
    """
    return 201, inscripciones.inscribir(ctx.boveda, cuerpo)


@ruta("POST", "/inscripciones/verificacion", None, requisito="R4.3")
def pedir_verificacion(ctx, actor, params, cuerpo, consulta):
    """R4.3 — emite el código de un solo uso que verifica un contacto.

    El desafío anti-automatización se valida **acá** y no al inscribir: el
    envío de códigos es lo que cuesta plata y lo que puede molestar a un
    tercero, así que es lo que hay que proteger.
    """
    # El cuerpo puede declarar un origen —lo usan las pruebas y el
    # emulador—, pero lo que vale en producción es el que `main.py` derivó
    # de los headers: si el límite se contara por un campo que manda el
    # cliente, bastaría con cambiarlo en cada intento.
    origen = ctx.origen or (cuerpo.get("origen_ip") or "").strip() or None
    desafio.validar(cuerpo.get("desafio_token"), origen=origen)
    return 200, verificacion_contacto.pedir_codigo(
        ctx.boveda, cuerpo.get("canal"), cuerpo.get("destino"), origen=origen)


@ruta("POST", "/inscripciones/verificacion/comprobar", None, requisito="R4.3")
def comprobar_verificacion(ctx, actor, params, cuerpo, consulta):
    return 200, verificacion_contacto.verificar(
        ctx.boveda, cuerpo.get("canal"), cuerpo.get("destino"),
        cuerpo.get("codigo"))


@ruta("GET", "/inscripciones", "aprobar_inscripciones", requisito="R3.7")
def listar_inscripciones(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": inscripciones.listar(
        ctx.boveda, consulta.get("estado", "pendiente")
    )}


@ruta("GET", "/inscripciones/<inscripcion_id>", "aprobar_inscripciones",
      requisito="R4.3")
def ver_inscripcion(ctx, actor, params, cuerpo, consulta):
    """La inscripción con sus **candidatos parecidos** (R4.3): quien aprueba
    los ve sin tener que buscarlos a mano, que es la única forma de que
    efectivamente los mire."""
    return 200, inscripciones.obtener(ctx.boveda, _entero(params["inscripcion_id"]))


@ruta("POST", "/inscripciones/<inscripcion_id>/aprobar", "aprobar_inscripciones",
      requisito="R3.7")
def aprobar_inscripcion(ctx, actor, params, cuerpo, consulta):
    return 200, inscripciones.aprobar(
        ctx.boveda, int(params["inscripcion_id"]), actor,
        panel_id=cuerpo.get("panel_id"),
        # R4.3 — la fusión con un candidato parecido la declara quien aprueba.
        # El sistema no la decide solo: un homónimo fusionado no se deshace.
        id_persona=cuerpo.get("id_persona"),
    )


@ruta("POST", "/inscripciones/<inscripcion_id>/rechazar", "aprobar_inscripciones",
      requisito="R3.7")
def rechazar_inscripcion(ctx, actor, params, cuerpo, consulta):
    return 200, inscripciones.rechazar(
        ctx.boveda, int(params["inscripcion_id"]), actor, cuerpo.get("motivo")
    )


@ruta("GET", "/textos-consentimiento", "leer", requisito="R3.7")
def listar_textos(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": inscripciones.listar_textos(
        ctx.boveda, consulta.get("finalidad")
    )}


@ruta("POST", "/textos-consentimiento", "publicar_consentimiento", requisito="R3.7")
def publicar_texto(ctx, actor, params, cuerpo, consulta):
    return 201, inscripciones.publicar_texto(
        ctx.boveda, (cuerpo.get("finalidad") or "contacto_participacion"),
        cuerpo.get("version"), cuerpo.get("cuerpo"), actor,
    )


# ════════════════════════════════════════════════════════════════════
#  Fase 3 · 3C — Fricción operativa
# ════════════════════════════════════════════════════════════════════


def _claves_del_catalogo(ctx):
    """Las claves de los atributos activos (R3.14), para sugerir el marcado."""
    return [a["clave"] for a in atributos.listar(
        ctx.boveda, solo_activos=True, con_categorias=False)]


@ruta("POST", "/encuestas/<encuesta_id>/sav/analizar", "ingestar", requisito="R3.9")
def analizar_sav(ctx, actor, params, cuerpo, consulta):
    """Devuelve la metadata precargada del `.sav`. No ingesta nada."""
    contenido = _archivo_de(cuerpo)
    return 200, sav.analizar(contenido, _claves_del_catalogo(ctx),
                             cuerpo.get("valores_no_respuesta"))


@ruta("POST", "/encuestas/<encuesta_id>/sav/ingesta", "ingestar", requisito="R3.9")
def ingestar_sav(ctx, actor, params, cuerpo, consulta):
    """Ingesta el `.sav` con la metadata ya confirmada por el analista."""
    encuesta_id = int(params["encuesta_id"])
    contenido = _archivo_de(cuerpo)
    preguntas = cuerpo.get("preguntas") or []
    if not preguntas:
        raise DatosInvalidos(
            "Hace falta la lista de preguntas confirmada. Pedila primero a "
            "POST /encuestas/{id}/sav/analizar y mandala editada."
        )
    columna_id = (cuerpo.get("columna_id") or "").strip()
    if not columna_id:
        raise DatosInvalidos("Falta indicar qué variable identifica al individuo.")

    filas = sav.filas_de(contenido)
    # El marcado demográfico vale para los dos modos y es la única fuente del
    # mapeo a campos de `persona`: `mapeo_patronimico` quedó como alias de
    # compatibilidad para las llamadas viejas, no como un mecanismo aparte.
    demograficas = sav.normalizar_demograficas(
        cuerpo.get("demograficas"), {c for fila in filas for c in fila},
        campos_validos=sav.campos_demograficos(ctx.boveda), conn=ctx.boveda)
    mapeo = sav.mapeo_por_campo(demograficas) or (
        cuerpo.get("mapeo_patronimico") or {})
    # Los value labels de cada variable, para traducir el código del archivo
    # antes de escribirlo en la bóveda. Salen de la misma lista de preguntas:
    # una variable demográfica se configuró como cualquier otra y recién el
    # marcado decide que su valor va a `persona`.
    opciones_por_variable = {
        p.get("codigo"): (p.get("opciones") or {}) for p in preguntas
    }

    # R7.2 — la revisión va **antes** de `crear_individuos`: en el modo que
    # crea personas, confirmar es lo irreversible, y el resumen existe para
    # verlo antes. Si esto estuviera más abajo, revisar ya habría dado de
    # alta a la gente.
    if _revision_pedida(cuerpo):
        encuesta = encuestas.obtener(ctx.boveda, encuesta_id)
        return 200, resumen_ingesta.resumir(
            _plan_de(cuerpo, columna_id=columna_id,
                     origen=(cuerpo.get("origen") or "sav"),
                     preguntas=preguntas, demograficas=demograficas),
            # El panel de la encuesta, y no solo el que viene en el cuerpo:
            # en el modo «ya existen» la pantalla no lo manda, y la ingesta
            # igual incorpora a todos al panel de la encuesta (R3.9.a). El
            # resumen decía «no quedan asociados a ningún panel», que era
            # falso.
            filas, panel=cuerpo.get("panel_id") or encuesta.get("panel_id"),
            evidencia=_evidencia_normalizada(cuerpo),
            destino_tipo="encuesta",
            nombre_destino=encuesta.get("nombre"))

    creacion = None
    if cuerpo.get("modo") == "crear_individuos":
        # La evidencia de consentimiento es obligatoria en este modo y se
        # valida **antes** de ingestar nada: si falta o está mal declarada,
        # la importación entera se rechaza sin haber escrito una respuesta.
        # Al revés —ingestar primero y fallar al crear— dejaría el store
        # semántico con respuestas de gente que no existe en la bóveda.
        creacion = sav.crear_individuos(
            ctx.boveda, filas, mapeo,
            origen=(cuerpo.get("origen") or "sav"), columna_id=columna_id,
            evidencia_consentimiento=cuerpo.get("evidencia_consentimiento"),
            actor=actor, panel_id=cuerpo.get("panel_id"),
            opciones_por_variable=opciones_por_variable,
        )
        # Antes de encolar: las personas tienen que existir cuando la primera
        # tarea busque a quién corresponde cada fila, y las tareas corren en
        # otro proceso que no ve esta transacción.
        ctx.boveda.commit()

    # R-ASYNC.1 — se encola, no se procesa. El `.sav` ya se leyó y se
    # despivotó acá: lo que viaja a los lotes son las filas, no el archivo,
    # así que ninguna tarea vuelve a parsearlo.
    #
    # El plan lleva las demográficas **ya normalizadas** y no las crudas del
    # cuerpo: normalizarlas consulta el catálogo de atributos, y hacerlo una
    # vez por lote sería re-resolver el mapeo, que es justo lo que R-ASYNC.1
    # prohíbe.
    plan = _con_constancia_de_dedup(
        _plan_de(cuerpo, columna_id=columna_id,
                 origen=(cuerpo.get("origen") or "sav"),
                 preguntas=preguntas, demograficas=demograficas), filas)
    salida = diferida.encolar(
        ctx.boveda, "encuesta", encuesta_id, plan,
        filas, actor=actor, encolador=ctx.encolador)
    salida["sin_clave_de_dedup"] = plan["sin_clave_de_dedup"]

    # Lo que se puede decir sin procesar nada se dice ya: son chequeos sobre
    # el archivo, no sobre lo ingestado, y esperarlos al final no aportaría.
    salida["duplicados_en_el_archivo"] = calidad.detectar_duplicados_en_filas(
        filas, columna_id
    )
    if creacion is not None:
        salida["creacion_de_individuos"] = creacion
    return 202, salida


# ════════════════════════════════════════════════════════════════════
#  R3.13 — Cargar panelistas sin asociarlos a un panel
# ════════════════════════════════════════════════════════════════════
#
# Mismo permiso que la ingesta desde encuesta: es la misma operación —
# incorporar individuos y respuestas—, solo que sin panel.

@ruta("POST", "/cargas", "ingestar", requisito="R3.13")
def crear_carga(ctx, actor, params, cuerpo, consulta):
    """Abre una carga y devuelve su `ref_estudio`, que es lo que la ata a su
    cuestionario del lado semántico."""
    carga = cargas.crear(
        ctx.boveda, cuerpo.get("nombre"), cuerpo.get("descripcion"), actor,
        # R-ORG.4 — los datos del estudio, en la carga y no en cada persona.
        fecha_estudio=cuerpo.get("fecha_estudio"),
        publico_objetivo=cuerpo.get("publico_objetivo"))
    ctx.boveda.commit()
    return 201, carga


@ruta("GET", "/cargas", "leer", requisito="R3.13, R-ORG.5")
def listar_cargas(ctx, actor, params, cuerpo, consulta):
    """Las cargas con los datos del estudio y cuántas personas creó y
    reutilizó cada una. Es lo que arma los selectores por nombre y fecha."""
    return 200, {"items": cargas.listar(ctx.boveda)}


@ruta("GET", "/cargas/<carga_id>", "leer", requisito="R-ORG.4")
def ver_carga(ctx, actor, params, cuerpo, consulta):
    return 200, cargas.obtener(ctx.boveda, _entero(params["carga_id"]))


@ruta("PATCH", "/cargas/<carga_id>", "ingestar", requisito="R-ORG.4")
def editar_carga(ctx, actor, params, cuerpo, consulta):
    """Corrige los datos del estudio. Se refleja en todas las fichas que
    provienen de esa carga sin tocar ninguna persona."""
    salida = cargas.editar(ctx.boveda, _entero(params["carga_id"]), cuerpo)
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/cargas/<carga_id>/analizar", "ingestar", requisito="R3.13")
def analizar_sav_de_carga(ctx, actor, params, cuerpo, consulta):
    """Mismo contrato que el análisis de R3.9: la pantalla es la misma."""
    cargas.obtener(ctx.boveda, _entero(params["carga_id"]))
    return 200, sav.analizar(_archivo_de(cuerpo), _claves_del_catalogo(ctx),
                             cuerpo.get("valores_no_respuesta"))


@ruta("POST", "/cargas/<carga_id>/ingesta", "ingestar", requisito="R3.13")
def ingestar_carga(ctx, actor, params, cuerpo, consulta):
    """Incorpora los individuos del archivo y sus respuestas. Sin panel.

    Lo que **no** hace es tan importante como lo que hace: no crea membresías
    ni participaciones. Esa gente no es panelista y no tiene por qué aparecer
    en la composición, la brecha ni el muestreo de ningún panel.
    """
    carga_id = _entero(params["carga_id"])
    preguntas = cuerpo.get("preguntas") or []
    columna_id = (cuerpo.get("columna_id") or "").strip()
    if not columna_id:
        raise DatosInvalidos("Falta indicar qué variable identifica al individuo.")

    # Las filas llegan del `.sav` o ya despivotadas, igual que en R3.9.
    filas = (sav.filas_de(_archivo_de(cuerpo))
             if cuerpo.get("archivo_base64") or cuerpo.get("archivo")
             else (cuerpo.get("filas") or []))

    demograficas = sav.normalizar_demograficas(
        cuerpo.get("demograficas"), {c for fila in filas for c in fila},
        campos_validos=sav.campos_demograficos(ctx.boveda), conn=ctx.boveda)
    opciones_por_variable = {
        p.get("codigo"): (p.get("opciones") or {}) for p in preguntas
    }

    if _revision_pedida(cuerpo):
        return 200, resumen_ingesta.resumir(
            _plan_de(cuerpo, columna_id=columna_id,
                     origen=(cuerpo.get("origen") or "carga"),
                     preguntas=preguntas, demograficas=demograficas),
            filas, panel=None, evidencia=_evidencia_normalizada(cuerpo),
            destino_tipo="carga",
            nombre_destino=cargas.obtener(ctx.boveda, carga_id).get("nombre"))

    creacion = None
    if cuerpo.get("modo") == "crear_individuos":
        # Igual que en R3.9: la evidencia se valida **antes** de ingestar
        # nada. Al revés dejaría el store semántico con respuestas de gente
        # que no existe en la bóveda.
        creacion = cargas.crear_individuos(
            ctx.boveda, filas, sav.mapeo_por_campo(demograficas),
            origen=(cuerpo.get("origen") or "carga"), columna_id=columna_id,
            evidencia_consentimiento=cuerpo.get("evidencia_consentimiento"),
            actor=actor, opciones_por_variable=opciones_por_variable,
            carga_id=carga_id,
        )
        # Igual que en R3.9: las personas tienen que estar commiteadas antes
        # de que arranque la primera tarea.
        ctx.boveda.commit()

    cargas.obtener(ctx.boveda, carga_id)   # que exista, antes de guardar nada
    plan = _con_constancia_de_dedup(
        _plan_de(cuerpo, columna_id=columna_id,
                 origen=(cuerpo.get("origen") or "carga"),
                 preguntas=preguntas, demograficas=demograficas), filas)
    salida = diferida.encolar(
        ctx.boveda, "carga", carga_id, plan,
        filas, actor=actor, encolador=ctx.encolador)
    salida["sin_clave_de_dedup"] = plan["sin_clave_de_dedup"]

    salida["duplicados_en_el_archivo"] = calidad.detectar_duplicados_en_filas(
        filas, columna_id
    )
    if creacion is not None:
        salida["creacion_de_individuos"] = creacion
    return 202, salida


# ════════════════════════════════════════════════════════════════════
#  Fase 8 — Calidad del dato semántico
# ════════════════════════════════════════════════════════════════════
#
# Todo lo de acá **propone**: ninguna ruta aplica una corrección. El texto es
# lo que se embebe, y una reescritura automática equivocada degrada la
# búsqueda en silencio. Las decisiones viajan después, en las preguntas de la
# ingesta o del reproceso, y solo si el analista las eligió.

def _distribucion_del_cuerpo(cuerpo, codigos):
    """La distribución de valores que mandó la pantalla, en cualquiera de
    sus tres formas: las filas del archivo (`.csv`/`.xlsx`, que viven en el
    navegador), `{codigo: {valor: filas}}`, o las muestras `{codigo:
    [{valor, filas}]}` que devuelve el análisis de un `.sav`."""
    if cuerpo.get("filas") is not None:
        return calidad_dato.distribucion_de_filas(cuerpo.get("filas"), codigos)
    if isinstance(cuerpo.get("distribucion"), dict):
        return {str(c): {str(v): int(n) for v, n in (pv or {}).items()}
                for c, pv in cuerpo["distribucion"].items()}
    muestras = cuerpo.get("muestras") or {}
    if not isinstance(muestras, dict):
        raise DatosInvalidos("`muestras` es un objeto «código → [{valor, filas}]».")
    return {
        str(codigo): {str(m.get("valor")): int(m.get("filas") or 0)
                      for m in (lista or []) if m.get("valor") not in (None, "")}
        for codigo, lista in muestras.items()
    }


def _preguntas_del_cuerpo(cuerpo):
    preguntas = [p for p in (cuerpo.get("preguntas") or [])
                 if isinstance(p, dict) and p.get("codigo")]
    if not preguntas:
        raise DatosInvalidos("Hace falta la lista de preguntas a analizar.")
    return preguntas


@ruta("POST", "/calidad/diagnostico", "ingestar",
      requisito="R8.1, R8.2, R8.3, R8.4, R8.5, R8.6, R8.7")
def diagnosticar_calidad(ctx, actor, params, cuerpo, consulta):
    """El diagnóstico de calidad de un archivo que no es `.sav`.

    Con un `.sav` llega en la respuesta de `/sav/analizar`; un `.csv` o un
    `.xlsx` se leen en el navegador, así que la pantalla manda las filas acá.
    No escribe nada.
    """
    preguntas = _preguntas_del_cuerpo(cuerpo)
    distribucion = _distribucion_del_cuerpo(
        cuerpo, {p["codigo"] for p in preguntas})
    filas = cuerpo.get("filas")
    return 200, calidad_dato.diagnosticar(
        preguntas, distribucion,
        filas_total=len(filas) if isinstance(filas, list) else None,
        valores_no_respuesta=cuerpo.get("valores_no_respuesta"))


@ruta("POST", "/calidad/vista-previa", "ingestar", requisito="R8.8")
def vista_previa_calidad(ctx, actor, params, cuerpo, consulta):
    """Cómo va a quedar el texto embebido de cada variable, con valores
    reales del archivo.

    Lo calcula el servidor con `ingesta.respuesta_de` —lo mismo que corre al
    ingestar— y no la pantalla con una copia en JavaScript: dos
    implementaciones de «qué se embebe» divergirían, y lo que divergiría es
    justamente la vista que dice «esto es lo que se va a escribir». La
    pantalla la pide cada vez que se corrige algo.
    """
    preguntas = _preguntas_del_cuerpo(cuerpo)
    distribucion = _distribucion_del_cuerpo(
        cuerpo, {p["codigo"] for p in preguntas})
    return 200, {"items": calidad_dato.vistas_previas(preguntas, distribucion)}


@ruta("GET", "/encuestas/<encuesta_id>/preguntas", "leer", requisito="R8.9")
def preguntas_de_encuesta(ctx, actor, params, cuerpo, consulta):
    """Las preguntas de un estudio ya ingestado, como están, con lo que hace
    falta para corregirlas y reprocesar."""
    return 200, reproceso.estado(ctx.boveda, ctx.semantica, "encuesta",
                                 _entero(params["encuesta_id"]))


@ruta("GET", "/cargas/<carga_id>/preguntas", "leer", requisito="R8.9")
def preguntas_de_carga(ctx, actor, params, cuerpo, consulta):
    return 200, reproceso.estado(ctx.boveda, ctx.semantica, "carga",
                                 _entero(params["carga_id"]))


def _reprocesar(ctx, actor, destino_tipo, destino_id, cuerpo):
    """Misma ruta para revisar y para ejecutar, con `solo_revisar`: como en
    la importación (R7.2), lo que la revisión dice que va a pasar sale del
    mismo plan que después se encola."""
    return reproceso.lanzar(
        ctx.boveda, ctx.semantica, destino_tipo, destino_id,
        cuerpo.get("preguntas") or [], actor=actor, encolador=ctx.encolador,
        solo_revisar=_revision_pedida(cuerpo),
        forzar=bool(cuerpo.get("forzar")))


@ruta("POST", "/encuestas/<encuesta_id>/reproceso", "ingestar", requisito="R8.9")
def reprocesar_encuesta(ctx, actor, params, cuerpo, consulta):
    """Corrige textos, mapeos o normalización de un estudio ya ingestado y
    re-embebe solo lo que cambió, por la vía diferida. Sin volver a subir el
    archivo."""
    return _reprocesar(ctx, actor, "encuesta", _entero(params["encuesta_id"]),
                       cuerpo)


@ruta("POST", "/cargas/<carga_id>/reproceso", "ingestar", requisito="R8.9")
def reprocesar_carga(ctx, actor, params, cuerpo, consulta):
    return _reprocesar(ctx, actor, "carga", _entero(params["carga_id"]), cuerpo)


# Tope del archivo subido. El `.sav` viaja en base64 adentro del JSON, así
# que ocupa un tercio más que en disco, y todo el cuerpo tiene que entrar en
# el límite de request del hosting. 22 MiB de `.sav` dan ~30 MiB de cuerpo,
# que es lo último que pasa con margen. Por encima de eso la subida falla en
# la red, sin llegar acá: mejor decirlo con un mensaje que se entienda.
LIMITE_SAV_BYTES = 22 * 1024 * 1024


def _archivo_de(cuerpo):
    """El `.sav` llega en base64 dentro del JSON. Es un archivo binario y la
    API es JSON: subirlo aparte pediría multipart en la Cloud Function, que
    complica más de lo que ahorra para los tamaños de un export de campo."""
    import base64

    crudo = cuerpo.get("archivo_base64") or cuerpo.get("archivo")
    if not crudo:
        raise DatosInvalidos("Falta el archivo .sav (campo «archivo_base64»).")
    try:
        contenido = base64.b64decode(crudo, validate=True)
    except Exception:
        raise DatosInvalidos("El archivo no viene en base64 válido.")
    if len(contenido) > LIMITE_SAV_BYTES:
        raise DatosInvalidos(
            f"El archivo pesa {len(contenido) / 1048576:.1f} MB y el máximo "
            f"que admite la subida es {LIMITE_SAV_BYTES // 1048576} MB. "
            f"Partilo por olas o quitale del export las variables que no se "
            f"van a ingestar.",
            {"bytes": len(contenido), "limite_bytes": LIMITE_SAV_BYTES},
        )
    return contenido


@ruta("POST", "/panelistas/regularizar", "enrolar", requisito="R3.9")
def regularizar_consentimiento(ctx, actor, params, cuerpo, consulta):
    """Saca de `pendiente_consentimiento` a quien ya tiene base legal.

    Es la contraparte del alta por SAV: esas personas se crean sin
    consentimiento registrado y no se las puede convocar hasta pasar por acá.
    """
    ids = cuerpo.get("ids_persona") or []
    if not ids:
        raise DatosInvalidos("Hace falta al menos un id_persona.")
    return 200, sav.regularizar(
        ctx.boveda, ids,
        (cuerpo.get("finalidad") or "contacto_participacion"),
        cuerpo.get("version_texto"), actor,
    )


@ruta("POST", "/consultas/csv-identificado", "exportar_identificado",
      requisito="R3.10")
def exportar_identificado(ctx, actor, params, cuerpo, consulta):
    """CSV con datos de contacto, a partir de un resultado ya reidentificado.

    Exige la reidentificación hecha: exportar no puede ser un segundo camino
    para sacar PII, porque entonces habría uno auditado y otro no. Y se
    registra con motivo propio, distinto de haberla visto en pantalla.
    """
    reidentificacion = cuerpo.get("reidentificacion")
    if not reidentificacion or not reidentificacion.get("items"):
        raise DatosInvalidos(
            "La exportación con datos necesita un resultado ya reidentificado. "
            "Pedí primero POST /reidentificacion y mandá su respuesta acá.",
            {"ruta_previa": "POST /reidentificacion"},
        )
    ids = [i["id_persona"] for i in reidentificacion["items"]]
    auditoria.registrar_reidentificacion(
        ctx.boveda, ids, actor=actor, motivo="exportacion",
        contexto={"ruta": "POST /consultas/csv-identificado",
                  "personas": len(ids)},
    )
    ctx.boveda.commit()
    return 200, {
        "csv": consultas.a_csv_identificado(reidentificacion, cuerpo.get("resultado")),
        "nombre_archivo": consultas.nombre_archivo_identificado(),
        "personas": len(ids),
        "contiene_datos_personales": True,
    }


@ruta("POST", "/paneles/desde-consulta", "gestionar_paneles", requisito="R3.11")
def panel_desde_consulta(ctx, actor, params, cuerpo, consulta):
    return 201, paneles.desde_consulta(
        ctx.boveda, cuerpo.get("nombre"), cuerpo.get("resultado") or {},
        definicion=cuerpo.get("definicion"),
        descripcion=cuerpo.get("descripcion"),
        actor=actor, consulta_id=cuerpo.get("consulta_id"),
    )


# ════════════════════════════════════════════════════════════════════
#  R3.14 — Catálogo de atributos demográficos
# ════════════════════════════════════════════════════════════════════
#
# Leer el catálogo lo puede hacer cualquiera que pueda leer: la pantalla de
# carga, la de consultas y la de composición lo necesitan para armar sus
# desplegables. **Escribirlo es solo de admin** (`gestionar_atributos`):
# define con qué se puede segmentar al panel entero y, cuando marca una
# categoría especial, toca una obligación legal.

@ruta("GET", "/atributos", "leer", requisito="R3.14")
def listar_atributos(ctx, actor, params, cuerpo, consulta):
    return 200, {
        "items": atributos.listar(
            ctx.boveda,
            solo_activos=_bandera(consulta.get("activos")),
            incluir_especiales=not _bandera(consulta.get("sin_especiales")),
        )
    }


@ruta("GET", "/atributos/<atributo_id>", "leer", requisito="R3.14")
def ver_atributo(ctx, actor, params, cuerpo, consulta):
    return 200, atributos.obtener(ctx.boveda, params["atributo_id"])


@ruta("POST", "/atributos", "gestionar_atributos", requisito="R3.14")
def crear_atributo(ctx, actor, params, cuerpo, consulta):
    salida = atributos.crear(ctx.boveda, cuerpo or {}, actor=actor)
    ctx.boveda.commit()
    return 201, salida


@ruta("PATCH", "/atributos/<atributo_id>", "gestionar_atributos", requisito="R3.14")
def editar_atributo(ctx, actor, params, cuerpo, consulta):
    cuerpo = cuerpo or {}
    # `activo` se maneja por su propio camino: desactivar no es editar, y
    # tiene una regla propia (los del núcleo no se desactivan).
    if "activo" in cuerpo and len(cuerpo) == 1:
        salida = atributos.desactivar(
            ctx.boveda, params["atributo_id"], bool(cuerpo["activo"]), actor=actor)
    else:
        salida = atributos.editar(
            ctx.boveda, params["atributo_id"], cuerpo, actor=actor)
    ctx.boveda.commit()
    return 200, salida


@ruta("DELETE", "/atributos/<atributo_id>", "gestionar_atributos", requisito="R3.14")
def eliminar_atributo(ctx, actor, params, cuerpo, consulta):
    salida = atributos.eliminar(ctx.boveda, params["atributo_id"], actor=actor)
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/atributos/<atributo_id>/categorias", "gestionar_atributos",
      requisito="R3.14")
def agregar_categoria(ctx, actor, params, cuerpo, consulta):
    salida = atributos.agregar_categoria(
        ctx.boveda, params["atributo_id"], cuerpo or {}, actor=actor)
    ctx.boveda.commit()
    return 201, salida


@ruta("PATCH", "/atributos/<atributo_id>/categorias/<categoria_id>",
      "gestionar_atributos", requisito="R3.14")
def editar_categoria(ctx, actor, params, cuerpo, consulta):
    salida = atributos.editar_categoria(
        ctx.boveda, params["atributo_id"], _entero(params["categoria_id"]),
        cuerpo or {}, actor=actor)
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/atributos/<atributo_id>/recalcular", "gestionar_atributos",
      requisito="R3.14")
def recalcular_atributo(ctx, actor, params, cuerpo, consulta):
    """R3.14.h — corregido el vocabulario, se recalculan los canónicos desde
    los valores crudos guardados. Sin volver a pedir el archivo original."""
    salida = atributos.recalcular(
        ctx.boveda, params["atributo_id"], actor=actor,
        # R-MAP.5 — corregir el mapeo y recalcular desde los crudos, sin
        # volver a subir el archivo.
        mapeo=(cuerpo or {}).get("mapeo"))
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/atributos/<atributo_id>/sugerir-mapeo", "ingestar",
      requisito="R-MAP.2")
def sugerir_mapeo_de_atributo(ctx, actor, params, cuerpo, consulta):
    """R-MAP.2 — qué categoría parece corresponderle a cada valor del archivo.

    Es una propuesta y nada más: la pantalla la precarga en el desplegable y
    quien carga confirma o corrige. No escribe nada.
    """
    cuerpo = cuerpo or {}
    return 200, sav.sugerir_mapeo(
        ctx.boveda, params["atributo_id"],
        cuerpo.get("valores") or [],
        etiquetas=cuerpo.get("etiquetas") or {})


@ruta("GET", "/atributos-auditoria", "cumplimiento", requisito="R3.14")
def auditoria_atributos(ctx, actor, params, cuerpo, consulta):
    """Quién tocó el vocabulario y cuándo. Va con `cumplimiento` porque el
    uso previsto es una revisión: que no aparezca una categoría especial sin
    que nadie la haya decidido."""
    return 200, {"items": atributos.auditoria(
        ctx.boveda, atributo_id=_entero(consulta.get("atributo_id")))}


@ruta("GET", "/panelistas/<id_persona>/atributos", "leer", requisito="R3.14")
def atributos_de_persona(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": atributos.valores_de(
        ctx.boveda, params["id_persona"],
        momento=_momento(ctx, consulta))}


@ruta("GET", "/panelistas/<id_persona>/atributos/historial", "leer",
      requisito="R4.1.a")
def historial_de_atributos(ctx, actor, params, cuerpo, consulta):
    """Todos los valores que tuvo la persona, con su vigencia."""
    return 200, {"items": atributos.historial_de(
        ctx.boveda, params["id_persona"], consulta.get("clave"))}


@ruta("PUT", "/panelistas/<id_persona>/atributos/<clave>", "enrolar",
      requisito="R3.14")
def fijar_atributo_de_persona(ctx, actor, params, cuerpo, consulta):
    salida = atributos.fijar(
        ctx.boveda, params["id_persona"], params["clave"],
        (cuerpo or {}).get("valor"), origen="edicion",
        fecha_referencia=(cuerpo or {}).get("fecha_referencia"),
        # R4.1.a — para cargar un cambio que ya ocurrió. Sin esto, el
        # historial diría que la persona cambió el día que alguien lo editó.
        vigencia_desde=(cuerpo or {}).get("vigencia_desde"))
    ctx.boveda.commit()
    return 200, salida


# ════════════════════════════════════════════════════════════════════
#  Fase 4 · 4A — Contacto
# ════════════════════════════════════════════════════════════════════

@ruta("POST", "/panelistas/<id_persona>/acceso-portal", "enrolar",
      requisito="R6.1.a")
def emitir_acceso_al_portal(ctx, actor, params, cuerpo, consulta):
    """Le manda al panelista el enlace para crear su contraseña.

    Va con `enrolar` y no con `gestionar_usuarios`: no es dar de alta a nadie
    en la aplicación de administración —el panelista no es un usuario del
    sistema y no tiene rol—, es un trámite sobre la ficha de una persona que
    ya está en el panel, igual que corregirle el celular.

    `POST` y no `GET` por lo mismo que la ruta equivalente de R2.12: tiene
    efecto —emite una credencial de un solo uso y escribe el rastro—, y un
    `GET` con efectos lo dispara solo cualquier prefetch.
    """
    salida = portal.emitir_para_panelista(
        ctx.boveda, params["id_persona"], actor=actor)
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/panelistas/<id_persona>/acceso-portal", "leer",
      requisito="R6.1.a")
def historial_de_acceso_al_portal(ctx, actor, params, cuerpo, consulta):
    """Qué enlaces se le emitieron, quién los pidió y cuáles siguen vivos."""
    return 200, {"items": portal.emisiones_de(ctx.boveda, params["id_persona"])}


@ruta("GET", "/panelistas/<id_persona>/canales", "leer", requisito="R4.4")
def canales_de_persona(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": preferencias.listar(ctx.boveda, params["id_persona"])}


@ruta("PUT", "/panelistas/<id_persona>/canales/<canal>", "gestionar_canales",
      requisito="R4.4")
def fijar_canal(ctx, actor, params, cuerpo, consulta):
    """Registra que esta persona acepta ese canal, con el texto con que lo
    aceptó. Sin ese texto, «aceptó recibir WhatsApp» es una afirmación sin
    respaldo."""
    salida = preferencias.otorgar(
        ctx.boveda, params["id_persona"], params["canal"],
        version_texto=(cuerpo or {}).get("version_texto"),
        origen=(cuerpo or {}).get("origen") or "edicion")
    ctx.boveda.commit()
    return 200, salida


@ruta("DELETE", "/panelistas/<id_persona>/canales/<canal>", "gestionar_canales",
      requisito="R4.4")
def revocar_canal(ctx, actor, params, cuerpo, consulta):
    """Deja de ser elegible para ese canal, sin afectar los otros.

    Es la mitigación mínima del riesgo de cumplimiento que anota la spec: sin
    canal de entrada, un «STOP» o un bloqueo en WhatsApp no llega al sistema,
    así que la revocación tiene que poder hacerse a mano."""
    salida = preferencias.revocar(ctx.boveda, params["id_persona"], params["canal"])
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/whatsapp/plantillas", "leer", requisito="R4.5")
def plantillas_de_whatsapp(ctx, actor, params, cuerpo, consulta):
    """Las plantillas aprobadas de la cuenta que tienen un botón de Flow.

    Es lo que alimenta el selector de la encuesta. Cada una trae su idioma y
    su `flow_id` adentro: no hay nada más que elegir."""
    return 200, whatsapp.listar_plantillas(
        solo_con_flow=not _bandera(consulta.get("todas")))


@ruta("PUT", "/encuestas/<encuesta_id>/flow", "fieldear", requisito="R4.5")
def configurar_flow(ctx, actor, params, cuerpo, consulta):
    """Elige la plantilla. El idioma y el Flow salen de ella."""
    salida = encuestas.configurar_flow(
        ctx.boveda, _entero(params["encuesta_id"]),
        plantilla=(cuerpo or {}).get("plantilla"),
        idioma=(cuerpo or {}).get("idioma"))
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/encuestas/<encuesta_id>/flow", "leer", requisito="R4.5")
def estado_flow(ctx, actor, params, cuerpo, consulta):
    """Valida contra Meta que el Flow esté publicado y la plantilla aprobada.

    Se consulta **antes** de convocar: una plantilla rechazada no se arregla
    sola y descubrirlo al enviar significa haber convocado a gente a la que no
    se le puede mandar nada."""
    return 200, encuestas.estado_flow(ctx.boveda, _entero(params["encuesta_id"]))


@ruta("GET", "/encuestas/<encuesta_id>/whatsapp", "leer", requisito="R4.5")
def destinatarios_whatsapp(ctx, actor, params, cuerpo, consulta):
    """A quiénes se les puede enviar y a quiénes no, discriminado por motivo."""
    return 200, encuestas.destinatarios_whatsapp(
        ctx.boveda, _entero(params["encuesta_id"]))


@ruta("POST", "/encuestas/<encuesta_id>/whatsapp", "enviar_whatsapp",
      requisito="R4.5")
def enviar_whatsapp(ctx, actor, params, cuerpo, consulta):
    """Manda el Flow. No es automático al convocar: es una acción explícita.

    Reintentar no reenvía a quien ya recibió."""
    return 200, encuestas.enviar_por_whatsapp(
        ctx.boveda, _entero(params["encuesta_id"]),
        ids_persona=(cuerpo or {}).get("ids_persona"))


@ruta("GET", "/diagnostico/contacto", "leer", requisito="R4.3, R4.5")
def diagnostico_contacto(ctx, actor, params, cuerpo, consulta):
    """Qué tan endurecida está la landing y si el canal de WhatsApp está listo.

    Las tres cosas que informa —proveedor de códigos, desafío y credenciales
    de Meta— son condiciones para poder anunciar la landing y para poder
    enviar, y su ausencia no puede ser una sorpresa.

    Pide `leer` y no `cumplimiento`: quien reparte el enlace del formulario
    público es operaciones, y es justo quien necesita saber si la
    verificación funciona de verdad antes de repartirlo. Lo que devuelve son
    nombres de proveedor y booleanos —**ningún valor de credencial**—, así que
    no hay nada que proteger más allá de la sesión."""
    return 200, {
        "verificacion": verificacion_contacto.diagnostico(conn=ctx.boveda),
        "desafio": desafio.diagnostico(),
        "whatsapp": whatsapp.diagnostico(),
    }


@ruta("POST", "/diagnostico/contacto/correo-prueba", "cumplimiento",
      requisito="R-MAIL.4")
def correo_de_prueba(ctx, actor, params, cuerpo, consulta):
    """Manda un correo de prueba a la dirección indicada, sin crear un
    panelista. Es la forma de comprobar la configuración entera —credencial,
    remitente, salida de red desde Cloud Run, SPF/DKIM— antes de anunciar el
    portal: un SMTP bloqueado por egress falla de forma poco clara, y es
    mejor verlo acá que en el primer panelista sin acceso.

    Pide `cumplimiento` y no `leer`: manda un correo a cualquier dirección
    desde la cuenta de Equipos. A diferencia del portal, el motivo del fallo
    **sí** se devuelve: quien prueba es quien tiene que arreglarlo.

    No pasa por el modo desarrollo: sin proveedor real, la prueba falla."""
    destino = ((cuerpo or {}).get("destino") or "").strip()
    canal, destino = verificacion_contacto.normalizar_destino(
        verificacion_contacto.EMAIL, destino)
    if verificacion_contacto._nombre_proveedor(os.environ) != "workspace":
        raise EnvioNoConfigurado(
            "La prueba manda un correo de verdad, y el proveedor de envío no es "
            "`workspace`: configurá VERIFICACION_ENVIO_PROVEEDOR=workspace.",
            {"proveedor": verificacion_contacto._nombre_proveedor(os.environ)})
    servidor = correo.Workspace.desde_entorno()
    asunto, texto = correo.armar(correo.PRUEBA, None)
    try:
        envio = servidor.enviar(destino, asunto, texto)
    except correo.ErrorEnvio as error:
        correo.registrar(ctx.boveda, correo.PRUEBA, destino, servidor.nombre,
                         "fallido", motivo=error.motivo, intentos=error.intentos)
        ctx.boveda.commit()
        return 200, {"enviado": False, "destino": destino, "motivo": error.motivo,
                     "transitorio": error.transitorio, "intentos": error.intentos}
    correo.registrar(ctx.boveda, correo.PRUEBA, destino, servidor.nombre,
                     "enviado", intentos=envio["intentos"], duracion_ms=envio["ms"])
    ctx.boveda.commit()
    return 200, {"enviado": True, "destino": destino, "remitente": servidor.remitente,
                 **envio}


@ruta("GET", "/diagnostico/contacto/envios", "cumplimiento", requisito="R-MAIL.4")
def envios_de_correo(ctx, actor, params, cuerpo, consulta):
    """Los envíos recientes —por defecto los fallidos—, con destinatario,
    momento y motivo, y cuántos salieron por día contra el tope de
    Workspace. Pide `cumplimiento`: lista direcciones de correo."""
    estado = (consulta.get("estado") or "fallido").strip().lower()
    if estado not in ("fallido", "enviado", "todos"):
        raise DatosInvalidos("`estado` es fallido, enviado o todos.")
    return 200, {
        "items": correo.listar(ctx.boveda, None if estado == "todos" else estado,
                               limite=_entero(consulta.get("limite") or 100)),
        "por_dia": correo.por_dia(ctx.boveda),
        "tope_diario": correo.TOPE_DIARIO,
    }


# ════════════════════════════════════════════════════════════════════
#  Fase 4 · 4B — Inteligencia
# ════════════════════════════════════════════════════════════════════

# ── R4.1.b · Series comparables ──────────────────────────────────────

@ruta("GET", "/series", "leer", requisito="R4.1.b")
def listar_series(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": series.listar(
        ctx.semantica, incluir_inactivas=_bandera(consulta.get("inactivas")))}


@ruta("POST", "/series", "gestionar_series", requisito="R4.1.b")
def crear_serie(ctx, actor, params, cuerpo, consulta):
    salida = series.crear(ctx.semantica, ctx.boveda, cuerpo or {}, actor=actor)
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 201, salida


@ruta("GET", "/series/<clave>", "leer", requisito="R4.1.b")
def ver_serie(ctx, actor, params, cuerpo, consulta):
    return 200, series.obtener(ctx.semantica, params["clave"])


@ruta("PATCH", "/series/<clave>", "gestionar_series", requisito="R4.1.b")
def editar_serie(ctx, actor, params, cuerpo, consulta):
    salida = series.editar(ctx.semantica, ctx.boveda, params["clave"],
                           cuerpo or {}, actor=actor)
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/series/<clave>/categorias", "gestionar_series",
      requisito="R4.1.b")
def agregar_categoria_de_serie(ctx, actor, params, cuerpo, consulta):
    salida = series.agregar_categoria(ctx.semantica, ctx.boveda, params["clave"],
                                      cuerpo or {}, actor=actor)
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 201, salida


@ruta("POST", "/series/<clave>/preguntas", "gestionar_series",
      requisito="R4.1.b")
def agregar_pregunta_a_serie(ctx, actor, params, cuerpo, consulta):
    """Declara que esta pregunta es la misma medición que las otras.

    El sistema puede haberla sugerido, pero agregarla es siempre un acto de
    una persona: por eso esto es un POST y no un efecto de pedir sugerencias.
    """
    cuerpo = cuerpo or {}
    if not cuerpo.get("pregunta_id"):
        raise DatosInvalidos("Falta `pregunta_id`.")
    salida = series.agregar_pregunta(
        ctx.semantica, ctx.boveda, params["clave"],
        _entero(cuerpo["pregunta_id"]), mapeo=cuerpo.get("mapeo"),
        origen=cuerpo.get("origen") or "declarada", actor=actor)
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 201, salida


@ruta("DELETE", "/series/<clave>/preguntas/<pregunta_id>", "gestionar_series",
      requisito="R4.1.b")
def quitar_pregunta_de_serie(ctx, actor, params, cuerpo, consulta):
    salida = series.quitar_pregunta(
        ctx.semantica, ctx.boveda, params["clave"],
        _entero(params["pregunta_id"]), actor=actor)
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 200, salida


@ruta("PUT", "/series/<clave>/preguntas/<pregunta_id>/mapeo",
      "gestionar_series", requisito="R4.1.b")
def mapear_opciones(ctx, actor, params, cuerpo, consulta):
    salida = series.mapear(
        ctx.semantica, ctx.boveda, params["clave"],
        _entero(params["pregunta_id"]), (cuerpo or {}).get("mapeo") or {},
        actor=actor)
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/series/<clave>/sugerencias", "gestionar_series",
      requisito="R4.1.b")
def sugerencias_de_serie(ctx, actor, params, cuerpo, consulta):
    """Preguntas candidatas de otras olas. **Propone; no agrega ninguna.**"""
    salida = series.sugerir(
        ctx.semantica, params["clave"],
        pregunta_id=consulta.get("pregunta_id"),
        proveedor=ctx.embeddings)
    # El embedding del texto de cada pregunta se cachea la primera vez: vale
    # la pena persistirlo aunque la ruta sea de lectura.
    ctx.semantica.commit()
    return 200, salida


@ruta("GET", "/preguntas", "leer", requisito="R4.1.b")
def preguntas_del_corpus(ctx, actor, params, cuerpo, consulta):
    """Las preguntas de todas las olas, para armar una serie.

    La primera pregunta de una serie no se puede sugerir —sin una de
    referencia no hay contra qué comparar—, así que tiene que poder elegirse
    de una lista."""
    return 200, {"items": series.preguntas_disponibles(
        ctx.semantica, clave_o_id=consulta.get("serie"),
        cuestionario_id=_entero(consulta["cuestionario"])
                        if consulta.get("cuestionario") else None)}


@ruta("GET", "/series/<clave>/auditoria", "leer", requisito="R4.1.b")
def auditoria_de_serie(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": series.auditoria(ctx.boveda, params["clave"])}


# ── R4.1.c · Vista longitudinal ──────────────────────────────────────

@ruta("GET", "/panelistas/<id_persona>/longitudinal", "reidentificar",
      requisito="R4.1.c")
def linea_de_tiempo(ctx, actor, params, cuerpo, consulta):
    """En qué olas participó y qué contestó en cada una.

    Pide `reidentificar` y no `leer`: ver la línea de tiempo de una persona
    identificada es justo la operación que deshace la seudonimización. Queda
    registrada (R3.10), y el módulo lo hace por su cuenta para que ninguna
    ruta se pueda olvidar."""
    salida = longitudinal.de_persona(
        ctx.boveda, ctx.semantica, params["id_persona"], actor=actor)
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/series/<clave>/transiciones", "leer", requisito="R4.1.c")
def transiciones_de_serie(ctx, actor, params, cuerpo, consulta):
    """Cuántas personas pasaron de cada categoría a cada otra entre dos olas.

    No reidentifica: son conteos sobre `id_persona`."""
    return 200, longitudinal.transiciones(
        ctx.semantica, params["clave"],
        desde=consulta.get("desde"), hasta=consulta.get("hasta"))


# ── R4.2 · Optimizador de muestreo ───────────────────────────────────

@ruta("GET", "/encuestas/<encuesta_id>/optimizar", "muestrear",
      requisito="R4.2")
def optimizar_muestra(ctx, actor, params, cuerpo, consulta):
    """La selección que mejor cierra la brecha respetando lo duro.

    **Propone.** Convocar sigue siendo una acción explícita, igual que con las
    reglas de R3.1."""
    return 200, optimizador.optimizar(
        ctx.boveda, _entero(params["encuesta_id"]),
        dimension=consulta.get("dimension", "sexo"),
        cantidad=consulta.get("cantidad", 100),
        estado=consulta.get("estado", "activo"),
        canal=consulta.get("canal"))


@ruta("GET", "/encuestas/<encuesta_id>/optimizar/comparar", "muestrear",
      requisito="R4.2")
def comparar_metodos(ctx, actor, params, cuerpo, consulta):
    """La misma pregunta por los dos métodos, para poder justificar el cambio."""
    return 200, optimizador.comparar(
        ctx.boveda, _entero(params["encuesta_id"]),
        dimension=consulta.get("dimension", "sexo"),
        cantidad=consulta.get("cantidad", 100),
        estado=consulta.get("estado", "activo"),
        canal=consulta.get("canal"))


@ruta("GET", "/paneles/<panel_id>/pesos-optimizador", "leer", requisito="R4.2")
def ver_pesos(ctx, actor, params, cuerpo, consulta):
    return 200, optimizador.obtener_pesos(ctx.boveda, _entero(params["panel_id"]))


@ruta("PUT", "/paneles/<panel_id>/pesos-optimizador", "configurar_optimizador",
      requisito="R4.2")
def guardar_pesos(ctx, actor, params, cuerpo, consulta):
    salida = optimizador.guardar_pesos(
        ctx.boveda, _entero(params["panel_id"]), cuerpo or {}, actor=actor)
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/yo", None)
def quien_soy(ctx, actor, params, cuerpo, consulta):
    return 200, actor.como_dict()


# ════════════════════════════════════════════════════════════════════
#  Fase 6 · El portal del panelista
# ════════════════════════════════════════════════════════════════════
# Ninguna de estas rutas recibe un `id_persona`. Sale de `portal.persona_de`,
# que lo resuelve desde el `uid` del token contra `cuenta_panelista`. Es la
# única forma de que «solo puede ver y modificar su propia persona» no
# dependa de que cada ruta se acuerde de comprobarlo.

def _yo(ctx, actor):
    return portal.persona_de(ctx.boveda, actor.uid)


@ruta("POST", "/portal/clave/enlace", None, requisito="R6.1.a, R6.1.c")
def portal_pedir_enlace(ctx, actor, params, cuerpo, consulta):
    """«Crear mi contraseña» y «me la olvidé» son la misma ruta.

    El cliente dice cuál de los dos textos mostró, y eso va al registro como
    motivo; lo que hace el servidor es idéntico, y la respuesta también.
    Separarlas en dos rutas con dos respuestas sería darle a quien prueba
    direcciones una segunda forma de preguntar lo mismo.
    """
    cuerpo = cuerpo or {}
    motivo = (cuerpo.get("motivo") or portal.RECUPERACION)
    if motivo not in portal.MOTIVOS_DE_ENLACE:
        motivo = portal.RECUPERACION
    salida = portal.pedir_enlace_de_clave(
        ctx.boveda, cuerpo.get("email"), motivo=motivo,
        origen=ctx.origen or cuerpo.get("origen"))
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/portal/clave", None, requisito="R6.1.a")
def portal_fijar_clave(ctx, actor, params, cuerpo, consulta):
    """Fija la contraseña con el token del enlace, y deja la sesión abierta."""
    cuerpo = cuerpo or {}
    salida = portal.fijar_clave(ctx.boveda, cuerpo.get("token"),
                                cuerpo.get("clave"), ctx.credenciales)
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/portal/sesion/clave", None, requisito="R6.1.b")
def portal_entrar(ctx, actor, params, cuerpo, consulta):
    """Correo y contraseña. Todo lo que falla contesta lo mismo.

    El commit va **también cuando falla**: el intento fallido se registra en
    la misma transacción, y si se perdiera con el rollback el límite de tasa
    no contaría nada. Es el detalle que convierte la regla en control.
    """
    cuerpo = cuerpo or {}
    try:
        salida = portal.iniciar_sesion(
            ctx.boveda, cuerpo.get("email"), cuerpo.get("clave"),
            ctx.credenciales, origen=ctx.origen or cuerpo.get("origen"))
    except ErrorApi:
        ctx.boveda.commit()
        raise
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/portal/clave/cambio", None, requisito="R6.1.c")
def portal_cambiar_clave(ctx, actor, params, cuerpo, consulta):
    """Con sesión abierta y la contraseña actual. Cierra las demás sesiones."""
    cuerpo = cuerpo or {}
    try:
        salida = portal.cambiar_clave(
            ctx.boveda, _yo(ctx, actor), cuerpo.get("actual"),
            cuerpo.get("nueva"), ctx.credenciales, origen=ctx.origen or cuerpo.get("origen"))
    except ErrorApi:
        ctx.boveda.commit()
        raise
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/portal/sesion", None, requisito="R6.2")
def portal_sesion(ctx, actor, params, cuerpo, consulta):
    """Confirma el vínculo de la sesión ya abierta con su persona."""
    salida = portal.vincular(ctx.boveda, actor.uid, actor.email)
    ctx.boveda.commit()
    return 200, {**salida, "id_persona": str(salida["id_persona"])}


@ruta("GET", "/portal/perfil", None, requisito="R6.5")
def portal_perfil(ctx, actor, params, cuerpo, consulta):
    return 200, portal.perfil(ctx.boveda, _yo(ctx, actor))


@ruta("PATCH", "/portal/perfil", None, requisito="R6.5")
def portal_editar_perfil(ctx, actor, params, cuerpo, consulta):
    salida = portal.editar_atributos(
        ctx.boveda, _yo(ctx, actor), (cuerpo or {}).get("atributos") or {})
    ctx.boveda.commit()
    return 200, salida


@ruta("POST", "/portal/contacto/verificacion", None, requisito="R6.6")
def portal_pedir_verificacion(ctx, actor, params, cuerpo, consulta):
    cuerpo = cuerpo or {}
    return 200, portal.pedir_verificacion_de_contacto(
        ctx.boveda, _yo(ctx, actor), cuerpo.get("canal"), cuerpo.get("destino"),
        origen=ctx.origen or cuerpo.get("origen"))


@ruta("POST", "/portal/contacto", None, requisito="R6.6, R6.1.d, R6.1.f")
def portal_confirmar_contacto(ctx, actor, params, cuerpo, consulta):
    cuerpo = cuerpo or {}
    try:
        salida = portal.confirmar_contacto(
            ctx.boveda, _yo(ctx, actor), cuerpo.get("canal"),
            cuerpo.get("destino"), cuerpo.get("codigo"),
            clave=cuerpo.get("clave"), credenciales=ctx.credenciales,
            origen=ctx.origen or cuerpo.get("origen"))
    except ErrorApi:
        ctx.boveda.commit()
        raise
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/portal/puntos", None, requisito="R6.3")
def portal_puntos(ctx, actor, params, cuerpo, consulta):
    return 200, portal.resumen_de_puntos(ctx.boveda, _yo(ctx, actor))


@ruta("GET", "/portal/premios", None, requisito="R6.4")
def portal_premios(ctx, actor, params, cuerpo, consulta):
    return 200, portal.catalogo(ctx.boveda, _yo(ctx, actor))


@ruta("GET", "/portal/canjes", None, requisito="R6.4")
def portal_canjes(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": portal.mis_canjes(ctx.boveda, _yo(ctx, actor))}


@ruta("POST", "/portal/canjes", None, requisito="R6.4")
def portal_canjear(ctx, actor, params, cuerpo, consulta):
    salida = portal.solicitar_canje(
        ctx.boveda, _yo(ctx, actor), _entero((cuerpo or {}).get("premio_id")))
    ctx.boveda.commit()
    return 201, salida


@ruta("GET", "/portal/canales", None, requisito="R6.7")
def portal_canales(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": portal.canales(ctx.boveda, _yo(ctx, actor))}


@ruta("PUT", "/portal/canales/<canal>", None, requisito="R6.7")
def portal_cambiar_canal(ctx, actor, params, cuerpo, consulta):
    salida = portal.cambiar_canal(
        ctx.boveda, _yo(ctx, actor), params["canal"],
        bool((cuerpo or {}).get("activo")))
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/portal/finalidades", None, requisito="R6.8")
def portal_finalidades(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": portal.finalidades(ctx.boveda, _yo(ctx, actor))}


@ruta("POST", "/portal/finalidades/<finalidad>/retiro", None,
      requisito="R6.8, R6.1.d")
def portal_retirar_finalidad(ctx, actor, params, cuerpo, consulta):
    """Retirar una finalidad **no** es darse de baja: el panel queda intacto.

    `POST .../retiro` y no `DELETE` desde que R6.1.d le agregó la
    contraseña: un `DELETE` con cuerpo es legal pero hay intermediarios que
    lo descartan, y un cuerpo descartado acá dejaría a la gente sin poder
    retirar nada.
    """
    cuerpo = cuerpo or {}
    try:
        salida = portal.retirar_finalidad(
            ctx.boveda, _yo(ctx, actor), params["finalidad"],
            conn_semantica=ctx.semantica, clave=cuerpo.get("clave"),
            credenciales=ctx.credenciales, origen=ctx.origen or cuerpo.get("origen"))
    except ErrorApi:
        ctx.boveda.commit()
        raise
    ctx.boveda.commit()
    return 200, salida


@ruta("GET", "/portal/baja", None, requisito="R6.9")
def portal_previo_a_la_baja(ctx, actor, params, cuerpo, consulta):
    """Lo que hay que leer antes de confirmar, con el saldo a la vista."""
    return 200, portal.previo_a_la_baja(ctx.boveda, _yo(ctx, actor))


@ruta("POST", "/portal/baja", None, requisito="R6.9, R6.1.d")
def portal_darse_de_baja(ctx, actor, params, cuerpo, consulta):
    cuerpo = cuerpo or {}
    try:
        salida = portal.darse_de_baja(
            ctx.boveda, _yo(ctx, actor), conn_semantica=ctx.semantica,
            clave=cuerpo.get("clave"), credenciales=ctx.credenciales,
            origen=ctx.origen or cuerpo.get("origen"))
    except ErrorApi:
        ctx.boveda.commit()
        raise
    ctx.boveda.commit()
    return 200, salida
