"""Ruteo de la API: Fases 1, 2 y 3.

Las rutas son las del HANDOFF y las del contrato propuesto en
`specs/SPEC_fase2.md` y `specs/SPEC_fase3.md`, con su requisito al lado. El router no sabe nada de
Firebase: recibe método, ruta, cuerpo y actor, y devuelve `(status, dict)`.
Eso lo hace probable sin desplegar nada.
"""

import re

from . import (
    auditoria,
    bajas,
    calidad,
    composicion,
    consentimiento,
    consultas,
    encuestas,
    esquema,
    inscripciones,
    muestreo,
    paneles,
    participacion,
    personas,
    premios,
    puntos,
    revision,
    sav,
    semantica,
    usuarios,
)
from .errores import DatosInvalidos, ErrorApi, NoEncontrado

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
})


def es_publica(metodo, camino):
    return (metodo.upper(), "/" + (camino or "").strip("/")) in PUBLICAS


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

@ruta("GET", "/paneles/<panel_id>/composicion", "leer", requisito="R2.3")
def ver_composicion(ctx, actor, params, cuerpo, consulta):
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
    return 200, composicion.composicion(
        ctx.boveda, _entero(params["panel_id"]),
        dimensiones=dimensiones or None,
        estado=consulta.get("estado", "activo"),
        cruce_de=cruce_de or None,
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


@ruta("GET", "/usuarios/auditoria", "gestionar_usuarios", requisito="R2.12")
def auditoria_usuarios(ctx, actor, params, cuerpo, consulta):
    return 200, usuarios.historial(
        ctx.boveda, consulta.get("uid"),
        min(_entero(consulta.get("limite"), 200) or 200, 1000),
    )


@ruta("GET", "/diagnostico/esquema", "leer")
def diagnostico_esquema(ctx, actor, params, cuerpo, consulta):
    """Qué migraciones están aplicadas en cada store y cuáles faltan.

    Las migraciones se aplican a mano contra cada instancia de Cloud SQL, así
    que una que no se aplicó no se nota hasta que alguien usa la pantalla que
    la necesitaba. Esta ruta lo hace visible antes de eso.
    """
    estado = esquema.revisar_stores(ctx.boveda, ctx.semantica)
    return (200 if estado["completo"] else 500), estado


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


@ruta("GET", "/inscripciones", "aprobar_inscripciones", requisito="R3.7")
def listar_inscripciones(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": inscripciones.listar(
        ctx.boveda, consulta.get("estado", "pendiente")
    )}


@ruta("POST", "/inscripciones/<inscripcion_id>/aprobar", "aprobar_inscripciones",
      requisito="R3.7")
def aprobar_inscripcion(ctx, actor, params, cuerpo, consulta):
    return 200, inscripciones.aprobar(
        ctx.boveda, int(params["inscripcion_id"]), actor,
        panel_id=cuerpo.get("panel_id"),
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

@ruta("POST", "/encuestas/<encuesta_id>/sav/analizar", "ingestar", requisito="R3.9")
def analizar_sav(ctx, actor, params, cuerpo, consulta):
    """Devuelve la metadata precargada del `.sav`. No ingesta nada."""
    contenido = _archivo_de(cuerpo)
    return 200, sav.analizar(contenido)


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
    creacion = None
    if cuerpo.get("modo") == "crear_individuos":
        creacion = sav.crear_individuos(
            ctx.boveda, filas, cuerpo.get("mapeo_patronimico") or {},
            origen=(cuerpo.get("origen") or "sav"), columna_id=columna_id,
            actor=actor, panel_id=cuerpo.get("panel_id"),
        )

    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta_id, preguntas, filas,
        (cuerpo.get("origen") or "sav"), columna_id, ctx.embeddings,
    )
    resultado["duplicados_en_el_archivo"] = calidad.detectar_duplicados_en_filas(
        filas, columna_id
    )
    if creacion is not None:
        resultado["creacion_de_individuos"] = creacion
    return 200, resultado


def _archivo_de(cuerpo):
    """El `.sav` llega en base64 dentro del JSON. Es un archivo binario y la
    API es JSON: subirlo aparte pediría multipart en la Cloud Function, que
    complica más de lo que ahorra para los tamaños de un export de campo."""
    import base64

    crudo = cuerpo.get("archivo_base64") or cuerpo.get("archivo")
    if not crudo:
        raise DatosInvalidos("Falta el archivo .sav (campo «archivo_base64»).")
    try:
        return base64.b64decode(crudo, validate=True)
    except Exception:
        raise DatosInvalidos("El archivo no viene en base64 válido.")


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


@ruta("GET", "/yo", None)
def quien_soy(ctx, actor, params, cuerpo, consulta):
    return 200, actor.como_dict()
