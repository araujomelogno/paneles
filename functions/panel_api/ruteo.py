"""Ruteo de la API: Fases 1, 2 y 3.

Las rutas son las del HANDOFF y las del contrato propuesto en
`specs/SPEC_fase2.md` y `specs/SPEC_fase3.md`, con su requisito al lado. El router no sabe nada de
Firebase: recibe método, ruta, cuerpo y actor, y devuelve `(status, dict)`.
Eso lo hace probable sin desplegar nada.
"""

import re

from . import (
    atributos,
    auditoria,
    bajas,
    desafio,
    calidad,
    cargas,
    composicion,
    consentimiento,
    consultas,
    db,
    encuestas,
    esquema,
    inscripciones,
    longitudinal,
    muestreo,
    optimizador,
    paneles,
    participacion,
    personas,
    preferencias,
    premios,
    puntos,
    revision,
    sav,
    semantica,
    series,
    usuarios,
    verificacion_contacto,
    whatsapp,
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
    # R4.3 — pedir y comprobar el código de verificación son parte del mismo
    # formulario público: si necesitaran token, la verificación sería
    # imposible desde la landing. Las dos están protegidas por su propio
    # límite de tasa y por el desafío anti-automatización.
    ("POST", "/inscripciones/verificacion"),
    ("POST", "/inscripciones/verificacion/comprobar"),
})


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
        demograficas=cuerpo.get("demograficas"),
        tipo_identificador=cuerpo.get("tipo_identificador"),
    )
    ctx.semantica.commit()
    ctx.boveda.commit()
    return 200, resultado


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
    origen = (cuerpo.get("origen_ip") or "").strip() or None
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
    return 200, sav.analizar(contenido, _claves_del_catalogo(ctx))


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
        campos_validos=sav.campos_demograficos(ctx.boveda))
    mapeo = sav.mapeo_por_campo(demograficas) or (
        cuerpo.get("mapeo_patronimico") or {})
    # Los value labels de cada variable, para traducir el código del archivo
    # antes de escribirlo en la bóveda. Salen de la misma lista de preguntas:
    # una variable demográfica se configuró como cualquier otra y recién el
    # marcado decide que su valor va a `persona`.
    opciones_por_variable = {
        p.get("codigo"): (p.get("opciones") or {}) for p in preguntas
    }

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

    # Por nombre y no por posición: `ingestar` toma `columna_id` antes que
    # `origen`, y pasarlos al revés no falla —los dos son strings— sino que
    # deja la ingesta sin poder mapear a nadie, en silencio.
    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta_id, preguntas, filas,
        columna_id=columna_id,
        origen=(cuerpo.get("origen") or "sav"),
        proveedor=ctx.embeddings,
        demograficas=demograficas,
        tipo_identificador=cuerpo.get("tipo_identificador"),
    )
    ctx.semantica.commit()
    ctx.boveda.commit()
    resultado["duplicados_en_el_archivo"] = calidad.detectar_duplicados_en_filas(
        filas, columna_id
    )
    if creacion is not None:
        resultado["creacion_de_individuos"] = creacion
    return 200, resultado


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
        ctx.boveda, cuerpo.get("nombre"), cuerpo.get("descripcion"), actor)
    ctx.boveda.commit()
    return 201, carga


@ruta("GET", "/cargas", "leer", requisito="R3.13")
def listar_cargas(ctx, actor, params, cuerpo, consulta):
    return 200, {"items": cargas.listar(ctx.boveda)}


@ruta("POST", "/cargas/<carga_id>/analizar", "ingestar", requisito="R3.13")
def analizar_sav_de_carga(ctx, actor, params, cuerpo, consulta):
    """Mismo contrato que el análisis de R3.9: la pantalla es la misma."""
    cargas.obtener(ctx.boveda, _entero(params["carga_id"]))
    return 200, sav.analizar(_archivo_de(cuerpo), _claves_del_catalogo(ctx))


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
        campos_validos=sav.campos_demograficos(ctx.boveda))
    opciones_por_variable = {
        p.get("codigo"): (p.get("opciones") or {}) for p in preguntas
    }

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
        )

    resultado = cargas.ingestar(
        ctx.boveda, ctx.semantica, carga_id, preguntas, filas,
        columna_id=columna_id,
        origen=(cuerpo.get("origen") or "carga"),
        proveedor=ctx.embeddings,
        demograficas=demograficas,
        tipo_identificador=cuerpo.get("tipo_identificador"),
    )
    ctx.semantica.commit()
    ctx.boveda.commit()
    resultado["duplicados_en_el_archivo"] = calidad.detectar_duplicados_en_filas(
        filas, columna_id
    )
    if creacion is not None:
        resultado["creacion_de_individuos"] = creacion
    return 200, resultado


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
    salida = atributos.recalcular(ctx.boveda, params["atributo_id"], actor=actor)
    ctx.boveda.commit()
    return 200, salida


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
        "verificacion": verificacion_contacto.diagnostico(),
        "desafio": desafio.diagnostico(),
        "whatsapp": whatsapp.diagnostico(),
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
