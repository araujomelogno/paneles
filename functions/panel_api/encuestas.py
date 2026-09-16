"""R1.5 — Fielding de encuestas y vínculo de respuestas con `id_persona`.

`encuesta.ref_estudio` (bóveda) == `cuestionario.ref_estudio` (semántico): la
misma uuid, generada al crear la encuesta. No hay FK entre stores; el cruce
se resuelve por `ref_estudio` y por `id_persona`, y nada más.
"""

from . import consentimiento, db, ingesta as mod_ingesta, semantica
from .errores import Conflicto, DatosInvalidos, NoEncontrado

ESTADOS = ("borrador", "en_campo", "cerrada")


def crear(conn, panel_id, nombre, fecha_campo=None):
    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosInvalidos("La encuesta necesita un nombre.")
    if not db.una(conn, "select 1 from panel where id = %s", (panel_id,)):
        raise NoEncontrado(f"No existe el panel {panel_id}.")
    fila = db.una(
        conn,
        """
        insert into encuesta (panel_id, nombre, fecha_campo)
             values (%s, %s, %s)
          returning id, panel_id, nombre, fecha_campo, estado, ref_estudio, creado_en
        """,
        (panel_id, nombre, fecha_campo or None),
    )
    return _serializar(fila)


def _serializar(fila):
    return {
        "id": fila["id"],
        "panel_id": fila["panel_id"],
        "nombre": fila["nombre"],
        "fecha_campo": fila["fecha_campo"].isoformat() if fila["fecha_campo"] else None,
        "estado": fila["estado"],
        "ref_estudio": str(fila["ref_estudio"]),
        "creado_en": fila["creado_en"].isoformat(),
    }


def obtener(conn, encuesta_id):
    fila = db.una(
        conn,
        "select id, panel_id, nombre, fecha_campo, estado, ref_estudio, creado_en "
        "from encuesta where id = %s",
        (encuesta_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    return _serializar(fila)


def listar(conn, panel_id=None):
    filas = db.todas(
        conn,
        """
        select e.id, e.panel_id, e.nombre, e.fecha_campo, e.estado, e.ref_estudio,
               e.creado_en, p.nombre as panel,
               (select count(*) from participacion pa
                 where pa.encuesta_id = e.id)::int as convocados,
               (select count(*) from participacion pa
                 where pa.encuesta_id = e.id and pa.respondio)::int as respondieron
          from encuesta e join panel p on p.id = e.panel_id
         where (%s::bigint is null or e.panel_id = %s::bigint)
         order by e.creado_en desc
        """,
        (panel_id, panel_id),
    )
    salida = []
    for f in filas:
        item = _serializar(f)
        item.update(
            {
                "panel": f["panel"],
                "convocados": f["convocados"],
                "respondieron": f["respondieron"],
            }
        )
        salida.append(item)
    return salida


def cambiar_estado(conn, encuesta_id, estado):
    if estado not in ESTADOS:
        raise DatosInvalidos(
            f"Estado desconocido: {estado!r}.", {"estados_validos": list(ESTADOS)}
        )
    if not db.ejecutar(
        conn, "update encuesta set estado = %s where id = %s", (estado, encuesta_id)
    ):
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    return obtener(conn, encuesta_id)


def convocar(conn, encuesta_id, ids_persona=None, todo_el_panel=False):
    """Crea las participaciones de la ola.

    Gate: nadie sin `contacto_participacion` vigente entra en la convocatoria
    (regla de negocio de CLAUDE.md). Los bloqueados se informan; el resto de
    la convocatoria sigue. Idempotente por (encuesta, persona).
    """
    encuesta = obtener(conn, encuesta_id)
    if encuesta["estado"] == "cerrada":
        raise Conflicto("La encuesta está cerrada: no se puede convocar más gente.")

    if todo_el_panel:
        filas = db.todas(
            conn,
            "select id_persona from membresia where panel_id = %s and estado = 'activo'",
            (encuesta["panel_id"],),
        )
        ids_persona = [str(f["id_persona"]) for f in filas]
    ids_persona = [str(i) for i in dict.fromkeys(ids_persona or [])]
    if not ids_persona:
        raise DatosInvalidos("No hay a quién convocar.")

    habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        conn, ids_persona, consentimiento.CONTACTO
    )

    nuevas = 0
    for id_persona in habilitadas:
        nuevas += db.ejecutar(
            conn,
            """
            insert into participacion (encuesta_id, id_persona)
                 values (%s, %s)
            on conflict (encuesta_id, id_persona) do nothing
            """,
            (encuesta_id, id_persona),
        )
    if encuesta["estado"] == "borrador" and habilitadas:
        cambiar_estado(conn, encuesta_id, "en_campo")

    return {
        "encuesta_id": encuesta_id,
        "convocados_nuevos": nuevas,
        "convocados_total": len(habilitadas),
        "sin_consentimiento": bloqueadas,
    }


def listar_participacion(conn, encuesta_id):
    obtener(conn, encuesta_id)
    filas = db.todas(
        conn,
        """
        select pa.id_persona, pa.convocado_en, pa.respondio, pa.respondio_en,
               pa.calidad_estado, p.nombre, p.email,
               exists (select 1 from consentimiento c
                        where c.id_persona = pa.id_persona
                          and c.finalidad = 'uso_semantico'
                          and c.estado = 'vigente') as consiente_semantico,
               (select id_en_origen from alias_origen a
                 where a.id_persona = pa.id_persona limit 1) as id_en_origen
          from participacion pa join persona p on p.id_persona = pa.id_persona
         where pa.encuesta_id = %s
         order by p.nombre nulls last
        """,
        (encuesta_id,),
    )
    return [
        {
            "id_persona": str(f["id_persona"]),
            "nombre": f["nombre"],
            "email": f["email"],
            "id_en_origen": f["id_en_origen"],
            "convocado_en": f["convocado_en"].isoformat(),
            "respondio": f["respondio"],
            "respondio_en": f["respondio_en"].isoformat() if f["respondio_en"] else None,
            "calidad_estado": f["calidad_estado"],
            "consiente_semantico": f["consiente_semantico"],
        }
        for f in filas
    ]


def marcar_respuesta(conn, encuesta_id, ids_persona, respondio=True):
    """Marca quién respondió. La calidad se evalúa en la Fase 3; acá queda
    en `pendiente`."""
    ids = [str(i) for i in ids_persona]
    return db.ejecutar(
        conn,
        """
        update participacion
           set respondio = %s, respondio_en = case when %s then now() else null end
         where encuesta_id = %s and id_persona = any(%s::uuid[])
        """,
        (respondio, respondio, encuesta_id, ids),
    )


def incorporar_al_panel(conn, encuesta, ids_persona):
    """Da de alta en el panel de la encuesta a quienes se acaba de ingestar.

    Addendum de R3.9 · R3.9.a. Una persona sin membresía es invisible para la
    operación: no entra en `todo_el_panel` al convocar, no cuenta para la
    composición ni para la brecha de cuota, y el muestreo no la ve. Si
    respondió una encuesta de un panel, pertenece a ese pool.

    Cada encuesta pertenece a exactamente un panel (`encuesta.panel_id` es FK
    obligatorio), así que no hay que preguntar a cuál.

    **Una baja no se revierte de costado.** `paneles.agregar_miembro`
    reactiva, y acá eso estaría mal: la baja fue una decisión explícita de
    alguien y una ingesta no es el lugar para deshacerla. Se informa y decide
    un responsable.
    """
    if not ids_persona:
        return {"membresias_nuevas": 0, "membresias_existentes": 0,
                "membresias_en_baja": []}

    ids = [str(i) for i in dict.fromkeys(ids_persona)]
    estados = {
        str(f["id_persona"]): f["estado"]
        for f in db.todas(
            conn,
            "select id_persona, estado from membresia "
            " where panel_id = %s and id_persona = any(%s::uuid[])",
            (encuesta["panel_id"], ids),
        )
    }

    nuevas, existentes, en_baja = 0, 0, []
    for id_persona in ids:
        estado = estados.get(id_persona)
        if estado == "activo":
            existentes += 1
        elif estado == "baja":
            en_baja.append(id_persona)
        else:
            db.ejecutar(
                conn,
                "insert into membresia (panel_id, id_persona) values (%s, %s) "
                "on conflict (panel_id, id_persona) do nothing",
                (encuesta["panel_id"], id_persona),
            )
            nuevas += 1
    return {"membresias_nuevas": nuevas, "membresias_existentes": existentes,
            "membresias_en_baja": en_baja}


def registrar_participacion_importada(conn, encuesta_id, ids_persona):
    """Deja constancia de que estas personas respondieron esta ola.

    Addendum de R3.9 · R3.9.b. Sin esto, quien respondió en campo sin haber
    sido convocado desde el sistema no tiene fila en `participacion` y la ola
    muestra menos respuestas de las que hubo.

    **No aplica el gate de `contacto_participacion`, y es a propósito.**
    `convocar()` sí lo aplica porque emite una invitación futura: no se puede
    contactar a quien no consintió ser contactado. Esto es otra cosa —un
    hecho ya ocurrido, la persona respondió en campo—, y bloquear el registro
    no protege a nadie: solo distorsiona la tasa de respuesta. El gate sigue
    intacto donde corresponde: un miembro sin consentimiento vigente no entra
    en ninguna convocatoria futura por más participaciones que tenga.
    """
    if not ids_persona:
        return {"participaciones_nuevas": 0, "participaciones_actualizadas": 0}

    nuevas, actualizadas = 0, 0
    for id_persona in dict.fromkeys(str(i) for i in ids_persona):
        creada = db.ejecutar(
            conn,
            """
            insert into participacion
                   (encuesta_id, id_persona, origen, respondio, respondio_en)
                 values (%s, %s, 'importacion', true, now())
            on conflict (encuesta_id, id_persona) do nothing
            """,
            (encuesta_id, id_persona),
        )
        if creada:
            nuevas += 1
            continue
        # Ya estaba: la convocó el sistema. Se marca que respondió sin tocar
        # su origen, y `respondio_en` se conserva —una re-ingesta no debería
        # mover la fecha de la primera respuesta.
        db.ejecutar(
            conn,
            """
            update participacion
               set respondio = true,
                   respondio_en = coalesce(respondio_en, now())
             where encuesta_id = %s and id_persona = %s
            """,
            (encuesta_id, id_persona),
        )
        actualizadas += 1
    return {"participaciones_nuevas": nuevas,
            "participaciones_actualizadas": actualizadas}


def ingestar(conn_boveda, conn_semantica, encuesta_id, preguntas, filas,
             columna_id="id_en_origen", origen=None, proveedor=None):
    """Gancho de fielding → ingesta semántica (R1.5).

    Construye el mapa id de campo → `id_persona` a partir de las
    participaciones de esta ola y de `alias_origen`, y se lo pasa a la
    ingesta. La PII no entra en el mapa: solo el par de ids.

    Terminada la escritura, incorpora al panel y registra la participación de
    todos los ingestados (addendum de R3.9).
    """
    encuesta = obtener(conn_boveda, encuesta_id)

    participantes = listar_participacion(conn_boveda, encuesta_id)
    mapa = {
        p["id_en_origen"]: p["id_persona"]
        for p in participantes
        if p["id_en_origen"]
    }
    # La unión, y no uno u otro. Antes, con un solo convocado que tuviera
    # alias, `mapa` dejaba de estar vacío y la ingesta ya no miraba
    # `alias_origen`: todo el que respondió sin haber sido convocado caía en
    # `sin_mapear`, que es justamente el caso que este addendum viene a
    # resolver. La participación manda sobre el alias si difieren.
    if origen:
        ids_del_archivo = [
            i for i in dict.fromkeys(
                str(f.get(columna_id) or "").strip() for f in filas)
            if i
        ]
        por_alias, _ = mod_ingesta.mapear_a_id_persona(
            conn_boveda, origen, [i for i in ids_del_archivo if i not in mapa])
        mapa = {**por_alias, **mapa}

    if not mapa and not origen:
        raise DatosInvalidos(
            "No hay forma de saber a qué panelista corresponde cada fila del "
            "archivo: registrá el `alias_origen` de los convocados o indicá "
            "el `origen` de la plataforma de campo."
        )

    resultado = mod_ingesta.ingestar(
        conn_boveda,
        conn_semantica,
        encuesta,
        preguntas,
        filas,
        columna_id=columna_id,
        origen=origen,
        proveedor=proveedor,
        mapa_personas=mapa or None,
    )

    # ── Addendum de R3.9: membresía y participación ──
    ingestados = resultado.get("ids_persona_ingestados") or []
    resultado.update(incorporar_al_panel(conn_boveda, encuesta, ingestados))
    resultado.update(
        registrar_participacion_importada(conn_boveda, encuesta_id, ingestados))

    # Los que respondieron pero no se ingestaron —no tienen `uso_semantico`
    # vigente— igual respondieron. Si ya tenían fila porque el sistema los
    # convocó, se marca; no se les crea una, que es lo que pide el addendum.
    ingestados_set = set(ingestados)
    respondieron_sin_ingestar = [
        mapa[i]
        for i in {str(f.get(columna_id) or "").strip() for f in filas}
        if i in mapa and mapa[i] not in ingestados_set
    ]
    if respondieron_sin_ingestar:
        marcar_respuesta(
            conn_boveda, encuesta_id, respondieron_sin_ingestar, respondio=True)

    resultado["encuesta_id"] = encuesta_id
    return resultado


def verificar_cruce(conn_boveda, conn_semantica, encuesta_id):
    """Comprueba el contrato de cruce entre stores para una encuesta.

    Es la verificación del DoD de R1.5 hecha desde la app: mismo
    `ref_estudio` de los dos lados, y las personas con respuestas del lado
    store semántico son exactamente las que participaron del lado de la bóveda.
    """
    encuesta = obtener(conn_boveda, encuesta_id)
    resumen = semantica.resumen_de_estudio(conn_semantica, encuesta["ref_estudio"])
    participantes = {p["id_persona"] for p in listar_participacion(conn_boveda, encuesta_id)}
    return {
        "encuesta_id": encuesta_id,
        "ref_estudio": encuesta["ref_estudio"],
        "boveda": {"convocados": len(participantes)},
        "semantica": resumen,
        "cruce_ok": resumen["individuos"] <= len(participantes),
    }
