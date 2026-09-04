"""Ruteo de la API de Fase 1.

Las rutas son exactamente las del HANDOFF, con su requisito al lado. El
router no sabe nada de Firebase: recibe método, ruta, cuerpo y actor, y
devuelve `(status, dict)`. Eso lo hace probable sin desplegar nada.
"""

import re

from . import bajas, consentimiento, encuestas, paneles, personas, revision, semantica
from .errores import ErrorApi, NoEncontrado

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


def despachar(metodo, camino, cuerpo, consulta, actor, ctx):
    """Ejecuta la ruta. `ctx` trae las conexiones y el proveedor de embeddings."""
    funcion, permiso, params = resolver(metodo, camino)
    if permiso:
        actor.exigir(permiso)
    return funcion(ctx, actor, params, cuerpo or {}, consulta or {})


def _entero(valor, por_defecto=None):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return por_defecto


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
    )


@ruta("GET", "/panelistas/<id_persona>", "leer")
def ficha_panelista(ctx, actor, params, cuerpo, consulta):
    return 200, personas.ficha(ctx.boveda, params["id_persona"])


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


@ruta("POST", "/encuestas/<encuesta_id>/ingesta", "ingestar", requisito="R1.5")
def ingestar_encuesta(ctx, actor, params, cuerpo, consulta):
    resultado = encuestas.ingestar(
        ctx.boveda,
        ctx.semantica,
        _entero(params["encuesta_id"]),
        cuerpo.get("preguntas") or [],
        cuerpo.get("filas") or [],
        columna_id=cuerpo.get("columna_id", "id_en_origen"),
        origen=cuerpo.get("origen"),
        proveedor=ctx.embeddings,
    )
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 200, resultado


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


@ruta("GET", "/yo", None)
def quien_soy(ctx, actor, params, cuerpo, consulta):
    return 200, actor.como_dict()
