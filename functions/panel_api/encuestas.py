"""R1.5 — Fielding de encuestas y vínculo de respuestas con `id_persona`.

`encuesta.ref_estudio` (bóveda) == `cuestionario.ref_estudio` (semántico): la
misma uuid, generada al crear la encuesta. No hay FK entre stores; el cruce
se resuelve por `ref_estudio` y por `id_persona`, y nada más.
"""

from . import (
    consentimiento, db, dedup, ingesta as mod_ingesta, personas, sav,
    semantica)
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
          returning id, panel_id, nombre, fecha_campo, estado, ref_estudio,
                    creado_en, flow_id, flow_plantilla, flow_idioma
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
        # R4.5 — la configuración de WhatsApp Flow. `None` en las encuestas
        # que no son de Flow, que son todas las anteriores a Fase 4.
        "flow_id": fila.get("flow_id"),
        "flow_plantilla": fila.get("flow_plantilla"),
        "flow_idioma": fila.get("flow_idioma"),
        "es_flow": bool(fila.get("flow_id") and fila.get("flow_plantilla")),
    }


def obtener(conn, encuesta_id):
    fila = db.una(
        conn,
        "select id, panel_id, nombre, fecha_campo, estado, ref_estudio, creado_en, "
        "       flow_id, flow_plantilla, flow_idioma "
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
               e.creado_en, e.flow_id, e.flow_plantilla, e.flow_idioma,
               p.nombre as panel,
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


def completar_demograficos(conn, filas, demograficas, columna_id, mapa,
                           opciones_por_variable=None, origen="ingesta",
                           fecha_referencia=None):
    """Vuelca a la bóveda los demográficos que trae el archivo.

    Addendum de R3.9 · R3.9.d. Solo completa lo que está vacío; un dato ya
    cargado con otro valor se informa y no se toca (ver
    `personas.completar_desde_archivo`).

    `demograficas` es `{variable: campo}`. Las marcadas «no guardar» no
    llegan acá: ya se filtraron.

    `opciones_por_variable` trae los value labels de cada variable, y no es
    opcional en la práctica: un `.sav` guarda `2` y «Femenino» por separado.
    Sin traducir, `persona.sexo` se llena de `1` y `2` y la composición por
    sexo queda inservible sin que nada falle.
    """
    campos = sav.mapeo_por_campo(demograficas)
    if not campos:
        return {"demograficos_completados": 0, "discrepancias_demograficas": [],
                "valores_sin_categoria": []}

    por_variable = {variable: campo for campo, variable in campos.items()}
    opciones_por_variable = opciones_por_variable or {}
    completados, discrepancias = 0, []
    # R3.14.c — los valores del archivo que no corresponden a ninguna
    # categoría del atributo. No se inventa la categoría: la fila queda sin
    # ese atributo y se informa, con el listado para que se pueda corregir el
    # vocabulario y después recalcular.
    sin_categoria = {}
    vistos = set()
    for fila in filas:
        id_en_origen = str(fila.get(columna_id) or "").strip()
        id_persona = mapa.get(id_en_origen)
        if not id_persona or id_persona in vistos:
            continue
        vistos.add(id_persona)

        datos = {}
        for variable, campo in por_variable.items():
            valor = sav.valor_demografico(
                campo, fila.get(variable), opciones_por_variable.get(variable))
            if valor:
                datos[campo] = valor
        if not datos:
            continue

        resultado = personas.completar_desde_archivo(
            conn, id_persona, datos, origen=origen,
            fecha_referencia=fecha_referencia)
        completados += len(resultado["completados"])
        for discrepancia in resultado["discrepancias"]:
            discrepancias.append({"id_persona": id_persona, **discrepancia})
        for caso in resultado.get("sin_categoria") or []:
            sin_categoria.setdefault(
                (caso["clave"], caso["motivo"]), set()).add(str(caso.get("valor")))

    return {
        "demograficos_completados": completados,
        "discrepancias_demograficas": discrepancias,
        "valores_sin_categoria": [
            {"atributo": clave, "motivo": motivo, "valores": sorted(valores)}
            for (clave, motivo), valores in sorted(sin_categoria.items())
        ],
    }


# ── R3.12.a · Exportar la muestra para precargar el instrumento ──────
#
# El sistema tiene una ventaja que no estaba usando: **la muestra sale de
# él**. La convocatoria se decide acá, así que el identificador correcto puede
# viajar *hacia* el campo en vez de intentar adivinarlo *a la vuelta*. Y de
# paso el archivo que va a campo queda seudónimo: la alternativa era usar
# documento o email como llave, o sea meter PII en un export que circula por
# la plataforma, por la computadora de quien lo baja y por correo.

# El archivo seudónimo lleva `id_persona` y nada más: es lo necesario para
# precargar el instrumento.
CAMPOS_MUESTRA = ("id_persona",)

# El de contacto lleva lo mismo que devuelve la reidentificación, **sin**
# fecha de nacimiento exacta ni observaciones.
CAMPOS_MUESTRA_CON_CONTACTO = (
    "id_persona", "nombre", "documento", "email", "celular", "contacto",
    "sexo", "localidad", "tramo_etario",
)

# R3.14.e — un atributo marcado como categoría especial **no** se agrega a
# esta lista ni a ninguna exportación con datos. La lista es fija a propósito:
# que el catálogo crezca no puede hacer crecer solo lo que sale del sistema en
# un archivo.
AVISO_MUESTRA_CON_PII = (
    "# ATENCIÓN: este archivo contiene datos personales de panelistas. "
    "Tratarlo según la política de protección de datos: no reenviarlo fuera "
    "del equipo de campo, no subirlo a servicios de terceros y borrarlo "
    "cuando termine el trabajo para el que se pidió."
)


def exportar_muestra(conn, encuesta_id, con_contacto=False):
    """La muestra de la ola, para precargar en la plataforma de campo.

    Seudónimo por defecto. `con_contacto=True` es una reidentificación en los
    hechos —devuelve nombre, documento, correo— y quien llama tiene que
    exigir el permiso y registrarla, igual que en R3.10. La ruta lo hace.
    """
    import csv
    import io

    encuesta = obtener(conn, encuesta_id)
    filas = db.todas(
        conn,
        """
        select pa.id_persona, p.nombre, p.documento, p.email, p.celular,
               p.contacto, d.sexo, d.localidad, d.tramo_etario
          from participacion pa
          join persona p on p.id_persona = pa.id_persona
          left join v_demografia d on d.id_persona = pa.id_persona
         where pa.encuesta_id = %s
         order by p.nombre nulls last
        """,
        (encuesta_id,),
    )

    campos = CAMPOS_MUESTRA_CON_CONTACTO if con_contacto else CAMPOS_MUESTRA
    salida = io.StringIO()
    if con_contacto:
        salida.write(AVISO_MUESTRA_CON_PII + "\n")
    escritor = csv.writer(salida, lineterminator="\n")
    escritor.writerow(campos)
    for fila in filas:
        escritor.writerow([
            str(fila[c]) if c == "id_persona" else fila[c] for c in campos
        ])

    marca = "-CON-DATOS-PERSONALES" if con_contacto else ""
    return {
        "encuesta_id": encuesta_id,
        "encuesta": encuesta["nombre"],
        "personas": len(filas),
        "ids_persona": [str(f["id_persona"]) for f in filas],
        "csv": salida.getvalue(),
        "nombre_archivo": f"muestra-{encuesta_id}{marca}.csv",
        "contiene_datos_personales": con_contacto,
        "columnas": list(campos),
        "nota": (
            "Precargá la columna `id_persona` como variable oculta en el "
            "instrumento y pedile a la plataforma que la devuelva en el "
            "export. Al ingestar, declará el tipo de identificador "
            "«id_persona»: el mapeo es directo y no depende de que la "
            "plataforma repita sus ids entre estudios."
        ),
    }


def _resolver_identidades(conn, encuesta_id, tipo, valores, origen):
    """Traduce la columna de identidad a `id_persona`, según el tipo declarado.

    R3.12.b. El tipo por defecto es `alias` —la conducta de siempre— para que
    las cargas existentes sigan andando sin tocar nada.
    """
    if tipo != mod_ingesta.POR_ALIAS:
        return mod_ingesta.resolver_identificadores(conn, tipo, valores, origen)

    # Alias: las participaciones de esta ola **y** `alias_origen`, unidos.
    # Antes era uno u otro, y con un solo convocado con alias —o sea,
    # siempre— la ingesta dejaba de mirar `alias_origen`: todo el que
    # respondió sin haber sido convocado caía en `sin_mapear`. La
    # participación manda sobre el alias si difieren.
    mapa = {
        p["id_en_origen"]: p["id_persona"]
        for p in listar_participacion(conn, encuesta_id)
        if p["id_en_origen"]
    }
    if origen:
        por_alias, _ = mod_ingesta.mapear_a_id_persona(
            conn, origen, [v for v in valores if v not in mapa])
        mapa = {**por_alias, **mapa}
    return mapa, {v: mod_ingesta.SIN_ALIAS for v in valores if v not in mapa}


def _sembrar_alias(conn, tipo, mapa, origen):
    """Registra el alias de plataforma de lo que se resolvió por llave natural.

    R3.12.c. El valor que se guarda como `id_en_origen` es el que traía la
    columna —el documento o el correo—, porque es con eso que esa plataforma
    identificó al respondente: la próxima carga del mismo estudio lo encuentra
    por alias y ya no necesita la llave natural. Queda del lado de la bóveda,
    que es donde ese dato ya vive; el guardrail de PII del store semántico no
    se toca.

    Sin origen declarado no hay alias que registrar: el par
    `(origen, id_en_origen)` es lo que identifica al alias, y la mitad sola no
    sirve para nada.
    """
    if tipo not in (mod_ingesta.POR_DOCUMENTO, mod_ingesta.POR_EMAIL) or not origen:
        return 0
    if not mapa:
        return 0

    # De una consulta y no de una por fila: un export de campo trae miles.
    ya_estan = {
        f["id_en_origen"]
        for f in db.todas(
            conn,
            "select id_en_origen from alias_origen "
            " where origen = %s and id_en_origen = any(%s)",
            (origen, list(mapa)),
        )
    }
    nuevos = 0
    for id_en_origen, id_persona in mapa.items():
        if id_en_origen in ya_estan:
            continue
        dedup.registrar_alias(conn, id_persona, origen, id_en_origen)
        nuevos += 1
    return nuevos


def ingestar(conn_boveda, conn_semantica, encuesta_id, preguntas, filas,
             columna_id="id_en_origen", origen=None, proveedor=None,
             demograficas=None, tipo_identificador=None):
    """Gancho de fielding → ingesta semántica (R1.5).

    Construye el mapa id de campo → `id_persona` a partir de las
    participaciones de esta ola y de `alias_origen`, y se lo pasa a la
    ingesta. La PII no entra en el mapa: solo el par de ids.

    Terminada la escritura, incorpora al panel y registra la participación de
    todos los ingestados (addendum de R3.9).

    `demograficas` (`{variable: campo}`) marca qué variables del archivo son
    segmentadores: **no se ingestan como preguntas** y sus valores van a la
    bóveda. El filtro se aplica acá y no solo en la pantalla, porque es una
    regla de privacidad —los demográficos no se espejan al store semántico— y
    una regla de privacidad que solo vive en el navegador no es una regla.
    """
    encuesta = obtener(conn_boveda, encuesta_id)
    demograficas = sav.normalizar_demograficas(
        demograficas, campos_validos=sav.campos_demograficos(conn_boveda))

    marcadas = set(demograficas)
    excluidas = sorted(
        {p.get("codigo") for p in preguntas if p.get("codigo") in marcadas})
    # Las opciones de la variable demográfica salen de la misma lista de
    # preguntas: se la configuró como cualquier otra y recién acá se decide
    # que su valor va a la bóveda y no al store semántico.
    opciones_demograficas = {
        p.get("codigo"): (p.get("opciones") or {})
        for p in preguntas if p.get("codigo") in marcadas
    }
    preguntas = [p for p in preguntas if p.get("codigo") not in marcadas]

    ids_del_archivo = [
        i for i in dict.fromkeys(
            str(f.get(columna_id) or "").strip() for f in filas)
        if i
    ]
    tipo = tipo_identificador or mod_ingesta.POR_ALIAS
    mapa, motivos = _resolver_identidades(
        conn_boveda, encuesta_id, tipo, ids_del_archivo, origen)

    if not mapa and tipo == mod_ingesta.POR_ALIAS and not origen:
        raise DatosInvalidos(
            "No hay forma de saber a qué panelista corresponde cada fila del "
            "archivo: registrá el `alias_origen` de los convocados, indicá "
            "el `origen` de la plataforma de campo, o declará otro tipo de "
            "identificador.",
            {"tipos_validos": list(mod_ingesta.TIPOS_DE_IDENTIFICADOR)},
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
        motivos_sin_mapear=motivos,
    )
    resultado["tipo_identificador"] = tipo

    # R3.12.c — que una carga por llave natural deje sembrado el alias: la
    # próxima vuelta del mismo estudio ya no depende de documento ni email, y
    # el archivo de campo deja de necesitar PII.
    resultado["alias_registrados"] = _sembrar_alias(
        conn_boveda, tipo, mapa, origen)

    # ── Addendum de R3.9: demográficos, membresía y participación ──
    resultado["excluidas_por_demografica"] = excluidas
    resultado.update(completar_demograficos(
        conn_boveda, filas, demograficas, columna_id, mapa,
        opciones_por_variable=opciones_demograficas))

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


# ── R4.5 · Envío por WhatsApp Flow ──────────────────────────────────
#
# El envío es una acción **sobre** una convocatoria que ya existe: la
# convocatoria se registra igual que siempre (`participacion`) y esto no la
# reemplaza. Por eso el estado del envío vive en `participacion` y no en una
# tabla aparte, y por eso se puede convocar sin enviar y enviar después.

def configurar_flow(conn, encuesta_id, flow_id=None, plantilla=None,
                    idioma=None):
    """Marca la encuesta como de WhatsApp Flow. No valida contra Meta: eso lo
    hace `estado_flow`, porque una plantilla recién mandada a aprobar puede
    tardar días y la configuración tiene que poder guardarse igual."""
    obtener(conn, encuesta_id)
    db.ejecutar(
        conn,
        "update encuesta set flow_id = %s, flow_plantilla = %s, flow_idioma = %s "
        "where id = %s",
        ((flow_id or "").strip() or None, (plantilla or "").strip() or None,
         (idioma or "").strip() or None, encuesta_id),
    )
    return obtener(conn, encuesta_id)


def estado_flow(conn, encuesta_id, entorno=None, pedir=None):
    """¿Se puede convocar por WhatsApp? Valida contra Meta **antes** de enviar.

    Las plantillas requieren aprobación y la revisión demora; un Flow válido
    no hace enviable una plantilla rechazada. Descubrirlo recién al enviar
    significa haber convocado a gente a la que no se le puede mandar nada.
    """
    from . import whatsapp

    encuesta = obtener(conn, encuesta_id)
    if not encuesta["es_flow"]:
        return {"encuesta_id": encuesta_id, "es_flow": False,
                "puede_enviar": False,
                "motivos": ["La encuesta no está configurada como Flow."]}
    salida = whatsapp.validar_configuracion(
        encuesta["flow_id"], encuesta["flow_plantilla"], encuesta["flow_idioma"],
        entorno=entorno, pedir=pedir)
    salida.update({"encuesta_id": encuesta_id, "es_flow": True})
    return salida


def destinatarios_whatsapp(conn, encuesta_id, solo_pendientes=True):
    """Quiénes de los convocados pueden recibir el Flow, y quiénes no y por qué.

    **La regla de los dos ejes** (R4.4): consentimiento de finalidad vigente
    *y* preferencia de WhatsApp activa, más celular válido. Los excluidos van
    discriminados por motivo porque cada uno se arregla distinto.
    """
    from . import preferencias

    obtener(conn, encuesta_id)
    filas = db.todas(
        conn,
        """
        select id_persona, envio_estado from participacion
         where encuesta_id = %s
        """,
        (encuesta_id,),
    )
    convocados = [str(f["id_persona"]) for f in filas]
    ya_enviados = {str(f["id_persona"]) for f in filas
                   if f["envio_estado"] == "enviado"}

    contactables, excluidos = preferencias.filtrar_contactables(
        conn, convocados, preferencias.WHATSAPP)

    # Reintentar no puede volver a enviarle a quien ya recibió: sería un
    # mensaje duplicado, y por ese camino se queman el canal y la paciencia.
    pendientes = [i for i in contactables if i not in ya_enviados]
    return {
        "encuesta_id": encuesta_id,
        "convocados": len(convocados),
        "destinatarios": pendientes if solo_pendientes else contactables,
        "ya_enviados": sorted(ya_enviados),
        "excluidos": excluidos,
        "excluidos_total": sum(len(v) for v in excluidos.values()),
    }


def enviar_por_whatsapp(conn, encuesta_id, ids_persona=None, entorno=None,
                        pedir=None, enviar=None):
    """Manda el Flow a los convocados que cumplen. Devuelve el parte por persona.

    No se puede enviar si la configuración no está validada: es la baranda de
    R4.5, y evita el caso feo de convocar y descubrir después que la plantilla
    estaba rechazada.
    """
    from . import whatsapp

    encuesta = obtener(conn, encuesta_id)
    estado = estado_flow(conn, encuesta_id, entorno=entorno, pedir=pedir)
    if not estado["puede_enviar"]:
        raise Conflicto(
            "No se puede enviar por WhatsApp todavía.",
            {"motivos": estado["motivos"], "encuesta_id": encuesta_id})

    seleccion = destinatarios_whatsapp(conn, encuesta_id)
    destinatarios = seleccion["destinatarios"]
    if ids_persona:
        pedidos = {str(i) for i in ids_persona}
        destinatarios = [i for i in destinatarios if i in pedidos]
    if not destinatarios:
        return {**seleccion, "enviados": 0, "fallidos": 0, "detalle": []}

    celulares = {
        str(f["id_persona"]): f["celular"]
        for f in db.todas(
            conn,
            "select id_persona, celular from persona where id_persona = any(%s::uuid[])",
            (destinatarios,))
    }

    enviados, fallidos, detalle = 0, 0, []
    for id_persona in destinatarios:
        try:
            resultado = (enviar or whatsapp.enviar_flow)(
                celulares.get(id_persona),
                encuesta["flow_id"], encuesta["flow_plantilla"],
                encuesta["flow_idioma"],
                # R4.5 — el `flow_token` **es** el `id_persona`: es lo que
                # vuelve con las respuestas y lo que hace que la ingesta
                # mapee directo, sin PII y sin adivinar.
                flow_token=id_persona,
                entorno=entorno, pedir=pedir)
            db.ejecutar(
                conn,
                "update participacion set enviado_en = now(), "
                "envio_estado = 'enviado', envio_error = null "
                "where encuesta_id = %s and id_persona = %s",
                (encuesta_id, id_persona))
            enviados += 1
            detalle.append({"id_persona": id_persona, "estado": "enviado",
                            "message_id": resultado.get("message_id")})
        except Exception as error:      # noqa: BLE001 — se informa, no se propaga
            # Un fallo por persona no puede voltear el envío entero: el resto
            # de la ola tiene que salir igual, y el fallido se reintenta.
            mensaje = getattr(error, "mensaje", None) or str(error)
            db.ejecutar(
                conn,
                "update participacion set envio_estado = 'fallido', "
                "envio_error = %s where encuesta_id = %s and id_persona = %s",
                (mensaje[:500], encuesta_id, id_persona))
            fallidos += 1
            detalle.append({"id_persona": id_persona, "estado": "fallido",
                            "error": mensaje})
    conn.commit()
    return {**seleccion, "enviados": enviados, "fallidos": fallidos,
            "detalle": detalle}
