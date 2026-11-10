"""R1.5 — Fielding de encuestas y vínculo de respuestas con `id_persona`.

`encuesta.ref_estudio` (bóveda) == `cuestionario.ref_estudio` (semántico): la
misma uuid, generada al crear la encuesta. No hay FK entre stores; el cruce
se resuelve por `ref_estudio` y por `id_persona`, y nada más.

Desde D81 (`boveda/0032`, SPEC_encuestas_y_cargas_unificadas) **una encuesta
es un estudio**: guarda sus datos —nombre, fecha de campo, público objetivo,
clientes, temáticas— y su audiencia, que es un panel (`tipo = panel`) o la
foto de una consulta (`independiente`). Las respuestas entran siempre por una
**carga** de la encuesta (`cargas.ingestar`): una por archivo, todas sobre el
mismo cuestionario.
"""

from . import (
    consentimiento, db, dedup, ingesta as mod_ingesta, lista_negra, personas,
    sav, semantica)
from .errores import Conflicto, DatosInvalidos, NoEncontrado

ESTADOS = ("borrador", "en_campo", "cerrada")

# R-EU.5 — de qué es la audiencia de una encuesta.
DE_PANEL = "panel"
INDEPENDIENTE = "independiente"
TIPOS = (DE_PANEL, INDEPENDIENTE)

# De dónde viene (boveda/0033). Una independiente puede venir de una consulta
# —tiene audiencia— o de una carga —un archivo de respuestas, sin
# audiencia—, y la pantalla las muestra distinto: «Convocatoria» o
# «Respondentes». Se fija al crear.
ORIGEN_PANEL = "panel"
ORIGEN_CONSULTA = "consulta"
ORIGEN_CARGA = "carga"
# R-AU.3 (D90, boveda/0036) — una independiente creada vacía desde la sección
# Encuestas: su audiencia se arma a mano o desde consultas, en pasos.
ORIGEN_MANUAL = "manual"
ORIGENES = (ORIGEN_PANEL, ORIGEN_CONSULTA, ORIGEN_CARGA, ORIGEN_MANUAL)

_COLUMNAS = ("id, panel_id, tipo, origen, nombre, fecha_campo, publico_objetivo, "
             "url_alchemer, descripcion, estado, ref_estudio, creado_en")


def _fecha(crudo):
    """La fecha de campo como `date`, o None. Texto ISO (`2026-08-15`)."""
    import datetime

    if crudo in (None, ""):
        return None
    if isinstance(crudo, datetime.date):
        return crudo
    try:
        return datetime.date.fromisoformat(str(crudo).strip()[:10])
    except ValueError:
        raise DatosInvalidos(
            f"La fecha del estudio no es una fecha válida: {crudo!r}. Usá el "
            f"formato AAAA-MM-DD.") from None


def _texto(crudo):
    return (str(crudo).strip() or None) if crudo is not None else None


def _exigir_panel_activo(conn, panel_id):
    """R-EU.8 — un panel archivado no recibe encuestas ni convocatorias."""
    fila = db.una(conn, "select estado from panel where id = %s", (panel_id,))
    if not fila:
        raise NoEncontrado(f"No existe el panel {panel_id}.")
    if fila["estado"] == "archivado":
        raise Conflicto(
            "El panel está archivado: no se le pueden crear encuestas ni "
            "convocatorias. Reactivalo primero si hace falta.",
            {"motivo": "panel_archivado", "panel_id": panel_id})


def crear(conn, panel_id, nombre, fecha_campo=None, tipo=None,
          publico_objetivo=None, descripcion=None, clientes=None,
          estado="borrador", origen=None, url_alchemer=None):
    """Abre un estudio. De panel (`panel_id`) o independiente (sin panel).

    Los datos del estudio viven acá (R-EU.2): las cargas que se le hagan no
    los vuelven a pedir. `clientes` son nombres; los que no existen se crean.

    `origen` dice de dónde viene una independiente: `consulta` (con
    audiencia) o `carga` (un archivo de respuestas, sin audiencia). Una de
    panel es siempre `panel`. Una independiente sin origen declarado es de
    una carga, que es como la crean las cargas desde Panelistas.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosInvalidos("La encuesta necesita un nombre.")
    tipo = (tipo or (DE_PANEL if panel_id else INDEPENDIENTE)).strip().lower()
    if tipo not in TIPOS:
        raise DatosInvalidos(f"Tipo de encuesta desconocido: {tipo!r}.",
                             {"tipos": list(TIPOS)})
    if tipo == DE_PANEL:
        if not panel_id:
            raise DatosInvalidos("Una encuesta de panel necesita el panel.",
                                 {"campo": "panel_id"})
        _exigir_panel_activo(conn, panel_id)
    elif panel_id:
        raise DatosInvalidos(
            "Una encuesta independiente no tiene panel: su audiencia es la de "
            "una consulta.", {"campo": "panel_id"})
    if estado not in ESTADOS:
        raise DatosInvalidos(f"Estado desconocido: {estado!r}.")
    origen = ORIGEN_PANEL if tipo == DE_PANEL else (origen or ORIGEN_CARGA)
    if origen not in ORIGENES or (origen == ORIGEN_PANEL) != (tipo == DE_PANEL):
        raise DatosInvalidos(f"Origen de encuesta inválido: {origen!r}.",
                             {"origenes": list(ORIGENES)})
    fila = db.una(
        conn,
        f"""
        insert into encuesta (panel_id, tipo, origen, nombre, fecha_campo,
                              publico_objetivo, descripcion, estado, url_alchemer)
             values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
          returning {_COLUMNAS}
        """,
        (panel_id if tipo == DE_PANEL else None, tipo, origen, nombre,
         _fecha(fecha_campo), _texto(publico_objetivo), _texto(descripcion), estado,
         url_alchemer_valida(url_alchemer) if url_alchemer else None),
    )
    from . import clientes as mod_clientes

    salida = _serializar(fila)
    salida["clientes"] = mod_clientes.guardar_de_encuesta(
        conn, fila["id"], clientes or [])
    return salida


def _serializar(fila):
    return {
        "id": fila["id"],
        "panel_id": fila["panel_id"],
        "tipo": fila.get("tipo") or DE_PANEL,
        "origen": fila.get("origen") or ORIGEN_PANEL,
        "nombre": fila["nombre"],
        "fecha_campo": fila["fecha_campo"].isoformat() if fila["fecha_campo"] else None,
        "publico_objetivo": fila.get("publico_objetivo"),
        "descripcion": fila.get("descripcion"),
        # R-CF.3 — a dónde lleva el botón del mensaje, con el parámetro.
        "url_alchemer": fila.get("url_alchemer"),
        "estado": fila["estado"],
        "ref_estudio": str(fila["ref_estudio"]),
        "creado_en": fila["creado_en"].isoformat(),
    }


def obtener(conn, encuesta_id, completa=False):
    """La encuesta. Con `completa`, además sus clientes, temáticas y cargas:
    es lo que muestra el detalle del estudio."""
    fila = db.una(
        conn, f"select {_COLUMNAS} from encuesta where id = %s", (encuesta_id,))
    if not fila:
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    salida = _serializar(fila)
    if completa:
        from . import cargas, clientes as mod_clientes, tematicas

        salida["clientes"] = mod_clientes.de_encuesta(conn, encuesta_id)
        salida["tematicas"] = tematicas.de_encuesta(conn, encuesta_id)
        salida["cargas"] = cargas.listar(conn, encuesta_id=encuesta_id)
        salida.update(conteos(conn, encuesta_id))
        salida["con_convocatoria"] = salida["convocados"] > 0
        if fila["panel_id"]:
            panel = db.una(conn, "select nombre, estado from panel where id = %s",
                           (fila["panel_id"],))
            salida["panel"] = panel["nombre"]
            salida["panel_estado"] = panel["estado"]
    return salida


def exigir_de_panel(conn, encuesta_id):
    """El muestreo y el optimizador eligen entre los miembros de un panel:
    una encuesta independiente no tiene de dónde elegir (R-EU.5)."""
    encuesta = obtener(conn, encuesta_id)
    if encuesta["tipo"] != DE_PANEL:
        raise Conflicto(
            "Una encuesta independiente no tiene panel del cual muestrear: su "
            "audiencia es la de la consulta con que se creó.",
            {"motivo": "encuesta_independiente"})
    return encuesta


def conteos(conn, encuesta_id):
    """Convocados (la audiencia), respondentes y no convocados que respondieron."""
    fila = db.una(
        conn,
        """select count(*) filter (where convocado)::int as convocados,
                  count(*) filter (where respondio)::int as respondieron,
                  count(*) filter (where respondio and not convocado)::int
                                                         as no_convocados
             from participacion where encuesta_id = %s""",
        (encuesta_id,))
    return dict(fila)


def tiene_audiencia(conn, encuesta_id):
    """R-EU.4 — ¿alguien fue convocado? Solo entonces tiene sentido marcar
    a quien responde sin estarlo."""
    return bool(db.una(
        conn, "select 1 from participacion where encuesta_id = %s and convocado limit 1",
        (encuesta_id,)))


def listar(conn, panel_id=None, tipo=None, estado=None, cliente_id=None):
    """El listado de encuestas. R-ENC.2 (D90) — además del panel y el tipo,
    por estado y por cliente; los filtros se combinan."""
    if estado is not None and estado not in ESTADOS:
        raise DatosInvalidos(f"Estado desconocido: {estado!r}.",
                             {"estados_validos": list(ESTADOS)})
    filas = db.todas(
        conn,
        f"""
        select {', '.join('e.' + c.strip() for c in _COLUMNAS.split(','))},
               p.nombre as panel, p.estado as panel_estado,
               (select count(*) from participacion pa
                 where pa.encuesta_id = e.id and pa.convocado)::int as convocados,
               (select count(*) from participacion pa
                 where pa.encuesta_id = e.id and pa.respondio)::int as respondieron,
               (select count(*) from participacion pa
                 where pa.encuesta_id = e.id and pa.respondio
                   and not pa.convocado)::int as no_convocados,
               (select count(*) from carga c
                 where c.encuesta_id = e.id and not c.provisoria)::int as cargas
          from encuesta e left join panel p on p.id = e.panel_id
         -- D92 · R-CAN-C.1 — la encuesta automática de una carga que todavía
         -- no se confirmó no aparece en ningún listado.
         where not e.provisoria
           and (%s::bigint is null or e.panel_id = %s::bigint)
           and (%s::text is null or e.tipo = %s::text)
           and (%s::text is null or e.estado = %s::text)
           and (%s::bigint is null or exists (
                 select 1 from encuesta_cliente ec
                  where ec.encuesta_id = e.id and ec.cliente_id = %s::bigint))
         order by e.creado_en desc
        """,
        (panel_id, panel_id, tipo, tipo, estado, estado, cliente_id, cliente_id),
    )
    from . import clientes as mod_clientes

    clientes_de = mod_clientes.de_encuestas(conn, [f["id"] for f in filas])
    salida = []
    for f in filas:
        item = _serializar(f)
        item.update(
            {
                "panel": f["panel"],
                "panel_estado": f["panel_estado"],
                "convocados": f["convocados"],
                # PEDIDO encuestas sin convocatoria — «0 convocados» no es lo
                # mismo que «nadie convocó desde el sistema»: la pantalla
                # muestra «—» cuando no hay convocatoria.
                "con_convocatoria": f["convocados"] > 0,
                "respondieron": f["respondieron"],
                "no_convocados": f["no_convocados"],
                "cargas": f["cargas"],
                "clientes": clientes_de.get(f["id"], []),
            }
        )
        salida.append(item)
    return salida


def editar(conn, encuesta_id, cambios, conn_semantica=None):
    """R-EU.2 — corrige los datos del estudio: nombre, fecha de campo,
    público objetivo, descripción y clientes. Se ven en todas sus cargas y
    en las fichas de sus panelistas sin tocar a nadie.

    La fecha, además, se lleva al cuestionario del store semántico: es la
    que muestran las respuestas en la ficha."""
    cambios = cambios or {}
    obtener(conn, encuesta_id)
    columnas, valores = [], []
    if "nombre" in cambios:
        nombre = (cambios.get("nombre") or "").strip()
        if not nombre:
            raise DatosInvalidos("La encuesta necesita un nombre.")
        columnas.append("nombre")
        valores.append(nombre)
    for campo in ("publico_objetivo", "descripcion"):
        if campo in cambios:
            columnas.append(campo)
            valores.append(_texto(cambios.get(campo)))
    if "url_alchemer" in cambios:
        columnas.append("url_alchemer")
        valores.append(url_alchemer_valida(cambios.get("url_alchemer")))
    # `fecha_estudio` es el nombre con que la carga la mostraba: se acepta
    # igual, es la misma fecha.
    fecha = "fecha_campo" if "fecha_campo" in cambios else (
        "fecha_estudio" if "fecha_estudio" in cambios else None)
    if fecha:
        columnas.append("fecha_campo")
        valores.append(_fecha(cambios.get(fecha)))
    if columnas:
        db.ejecutar(
            conn,
            f"update encuesta set {', '.join(c + ' = %s' for c in columnas)} "
            f"where id = %s",
            tuple(valores) + (encuesta_id,))
    if "clientes" in cambios:
        from . import clientes as mod_clientes

        mod_clientes.guardar_de_encuesta(conn, encuesta_id, cambios.get("clientes") or [])
    if fecha and conn_semantica is not None:
        sincronizar_fecha(conn, conn_semantica, encuesta_id)
    salida = obtener(conn, encuesta_id, completa=True)
    if "url_alchemer" in cambios:
        salida["avisos"] = _avisos_de_url(conn, encuesta_id, salida.get("url_alchemer"))
    return salida


def _avisos_de_url(conn, encuesta_id, url):
    """R-CTL.1 — al cambiar la URL de Alchemer, los mensajes de WhatsApp con
    botón de URL que ya no coinciden: van a quedar bloqueados al enviar hasta
    revisar sus plantillas. Se comparan con la base del botón con que se
    guardó cada uno; al enviar se vuelve a comparar con la plantilla vigente."""
    filas = db.todas(
        conn,
        """select nombre, url_boton from encuesta_mensaje
            where encuesta_id = %s and canal = 'whatsapp' and boton = 'url'
              and url_boton is distinct from %s
            order by creado_en, id""", (encuesta_id, url))
    if not filas:
        return []
    nombres = ", ".join(f"«{f['nombre']}»" for f in filas)
    return [{
        "motivo": "mensajes_bloqueados_por_url",
        "mensaje": (f"{len(filas)} mensaje(s) de WhatsApp —{nombres}— llevan a otra "
                    f"URL: van a quedar bloqueados al enviar hasta revisar sus "
                    f"plantillas."),
        "mensajes": [{"nombre": f["nombre"], "url_boton": f["url_boton"],
                      "url_alchemer": url} for f in filas],
    }]


def sincronizar_fecha(conn_boveda, conn_semantica, encuesta_id, solo_si_vacia=False):
    """Lleva la fecha de campo del estudio a su cuestionario semántico. No
    es PII: es una fecha del estudio. Sin cuestionario (todavía no se
    ingestó nada) no hace nada."""
    fila = db.una(conn_boveda,
                  "select ref_estudio, fecha_campo from encuesta where id = %s",
                  (encuesta_id,))
    return semantica.fechar_cuestionario(
        conn_semantica, str(fila["ref_estudio"]), fila["fecha_campo"],
        solo_si_vacia=solo_si_vacia)


def url_alchemer_valida(crudo):
    """R-CF.3 — la URL de Alchemer, terminada en el parámetro que recibe el
    `id_persona` (`…?pid=`). Vacía es `None`: la encuesta todavía no tiene."""
    url = (crudo or "").strip()
    if not url:
        return None
    if not url.lower().startswith(("https://", "http://")) or any(c.isspace() for c in url):
        raise DatosInvalidos(
            "La URL de Alchemer tiene que empezar con https:// y no tener espacios.",
            {"campo": "url_alchemer"})
    if not url.endswith("="):
        raise DatosInvalidos(
            "La URL de Alchemer tiene que terminar en el parámetro que recibe al "
            "panelista, por ejemplo `…/estudio?pid=`: el sistema le agrega el "
            "id_persona al final.", {"campo": "url_alchemer"})
    return url


def enlace_de(encuesta, id_persona):
    """El enlace a la encuesta de una persona: la URL de Alchemer más su
    `id_persona`, que vuelve como columna en el SAV (R-CF.3)."""
    return f"{encuesta['url_alchemer']}{id_persona}" if encuesta.get("url_alchemer") else None


def cambiar_estado(conn, encuesta_id, estado, actor=None):
    """R-CF.1 — cualquier transición, en cualquier sentido (reabrir una
    cerrada incluido), y registrada: quién, cuándo, de qué a qué."""
    if estado not in ESTADOS:
        raise DatosInvalidos(
            f"Estado desconocido: {estado!r}.", {"estados_validos": list(ESTADOS)}
        )
    anterior = db.una(conn, "select estado from encuesta where id = %s for update",
                      (encuesta_id,))
    if not anterior:
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    if anterior["estado"] != estado:
        db.ejecutar(conn, "update encuesta set estado = %s where id = %s",
                    (estado, encuesta_id))
        db.ejecutar(
            conn,
            """insert into encuesta_estado_cambio
                      (encuesta_id, estado_anterior, estado_nuevo, actor_uid, actor_email)
               values (%s, %s, %s, %s, %s)""",
            (encuesta_id, anterior["estado"], estado, getattr(actor, "uid", None),
             getattr(actor, "email", None)))
    return obtener(conn, encuesta_id)


def historial_de_estado(conn, encuesta_id):
    """R-CF.1 — los cambios de estado, el más reciente primero."""
    obtener(conn, encuesta_id)
    return [{"estado_anterior": f["estado_anterior"], "estado_nuevo": f["estado_nuevo"],
             "actor": f["actor_email"] or f["actor_uid"] or "sistema",
             "creado_en": f["creado_en"].isoformat()}
            for f in db.todas(
                conn,
                """select * from encuesta_estado_cambio where encuesta_id = %s
                    order by creado_en desc, id desc""", (encuesta_id,))]


def convocar(conn, encuesta_id, ids_persona=None, todo_el_panel=False,
             _desde_consulta=False, actor=None, a_mano=False):
    """Crea las participaciones de la ola.

    Gate: nadie sin `contacto_participacion` vigente entra en la convocatoria
    (regla de negocio de CLAUDE.md). Los bloqueados se informan; el resto de
    la convocatoria sigue. Idempotente por (encuesta, persona).
    """
    encuesta = obtener(conn, encuesta_id)
    if encuesta["estado"] == "cerrada":
        raise Conflicto("La encuesta está cerrada: no se puede convocar más gente.")
    # R-EU.5 — la audiencia de una independiente es la foto de la consulta
    # con que se creó: no se le convoca a ningún panel ni se le suma gente.
    # R-CF.2 — sumar panelistas de a uno o en lista sí vale para una
    # independiente: es ajustar la audiencia sin rehacerla.
    if encuesta["tipo"] == INDEPENDIENTE and not (_desde_consulta or a_mano):
        raise Conflicto(
            "Una encuesta independiente no se convoca: su audiencia se fija al "
            "crearla desde el resultado de una consulta.",
            {"motivo": "encuesta_independiente"})
    if encuesta["panel_id"]:
        _exigir_panel_activo(conn, encuesta["panel_id"])

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

    # R-LN.2 — quien está en la lista negra no se convoca. El gate ya lo deja
    # afuera (`v_persona_convocable`, boveda/0031); se separa antes para que
    # el resumen diga por qué, y no lo cuente como falta de consentimiento.
    en_lista_negra = sorted(lista_negra.activas_de(conn, ids_persona))
    excluidas = set(en_lista_negra)
    ids_persona = [i for i in ids_persona if i not in excluidas]

    habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        conn, ids_persona, consentimiento.CONTACTO
    )

    nuevas = 0
    for id_persona in habilitadas:
        nuevas += db.ejecutar(
            conn,
            """
            insert into participacion (encuesta_id, id_persona, convocado)
                 values (%s, %s, true)
            on conflict (encuesta_id, id_persona) do update
                  set convocado = true, convocado_en = now()
                where not participacion.convocado
            """,
            (encuesta_id, id_persona),
        )
    if encuesta["estado"] == "borrador" and habilitadas:
        cambiar_estado(conn, encuesta_id, "en_campo", actor=actor)

    return {
        "encuesta_id": encuesta_id,
        "convocados_nuevos": nuevas,
        "convocados_total": len(habilitadas),
        "sin_consentimiento": bloqueadas,
        "en_lista_negra": len(en_lista_negra),
    }



# ── R-CF.2 · Agregar panelistas a mano ───────────────────────────────

AGREGADO = "agregado"
YA_ESTABA = "ya_estaba"
NO_CORRESPONDE = "no_corresponde"
AMBIGUO = "ambiguo"
EN_LISTA_NEGRA = "en_lista_negra"
SIN_CONSENTIMIENTO = "sin_consentimiento"
FORMATO_INVALIDO = "formato_invalido"

RESULTADOS_DE_AGREGAR = {
    AGREGADO: "Agregado",
    YA_ESTABA: "Ya estaba",
    NO_CORRESPONDE: "No corresponde a ningún panelista",
    AMBIGUO: "Ambiguo: el celular es de más de una persona",
    EN_LISTA_NEGRA: "En lista negra",
    SIN_CONSENTIMIENTO: "Sin consentimiento de contacto",
    FORMATO_INVALIDO: "Formato inválido",
}

TOPE_DE_LINEAS = 500


def _tipo_de_linea(valor):
    """Qué identificador parece: un uuid es un id_persona, una arroba es un
    correo, y lo demás se prueba como celular."""
    if mod_ingesta._UUID.match(valor):
        return mod_ingesta.POR_ID_PERSONA
    if "@" in valor:
        return mod_ingesta.POR_EMAIL
    return mod_ingesta.POR_CELULAR


def agregar_panelistas(conn, encuesta_id, texto, actor=None):
    """R-CF.2 — suma a la audiencia panelistas de a uno o en lista, uno por
    línea y con identificadores mezclados (id_persona, correo o celular).

    **No crea a nadie**: lo que no corresponde a un panelista se informa. Cada
    identificador se resuelve con `ingesta.resolver_identificadores`, la misma
    que usa la ingesta: el correo en minúsculas, el celular en E.164 y con una
    sola titular (si no, `ambiguo`). Lo que se encuentra entra por
    `convocar()`, **el mismo camino**, con sus controles: encuesta cerrada,
    lista negra y consentimiento de contacto. El resultado va línea por línea.
    """
    lineas = [l.strip() for l in str(texto or "").splitlines()]
    lineas = [l for l in lineas if l]
    if not lineas:
        raise DatosInvalidos("No hay ningún panelista para agregar.")
    if len(lineas) > TOPE_DE_LINEAS:
        raise DatosInvalidos(f"Se pegaron {len(lineas)} líneas y el tope es "
                             f"{TOPE_DE_LINEAS} por vez.")
    encuesta = obtener(conn, encuesta_id)
    if encuesta["estado"] == "cerrada":
        raise Conflicto("La encuesta está cerrada: no admite agregar panelistas. "
                        "Reabrila primero.", {"motivo": "encuesta_cerrada"})

    por_tipo = {}
    for linea in dict.fromkeys(lineas):
        por_tipo.setdefault(_tipo_de_linea(linea), []).append(linea)
    resueltos, motivos = {}, {}
    for tipo, valores in por_tipo.items():
        mapa, no = mod_ingesta.resolver_identificadores(conn, tipo, valores)
        resueltos.update(mapa)
        motivos.update(no)

    resultado = {}
    for linea, motivo in motivos.items():
        resultado[linea] = (AMBIGUO if motivo == mod_ingesta.CELULAR_COMPARTIDO
                            else NO_CORRESPONDE if motivo == mod_ingesta.NO_ENCONTRADO
                            else FORMATO_INVALIDO)

    ids = list(dict.fromkeys(resueltos.values()))
    ya = {str(f["id_persona"]) for f in db.todas(
        conn, """select id_persona from participacion
                  where encuesta_id = %s and convocado and id_persona = any(%s::uuid[])""",
        (encuesta_id, ids))} if ids else set()
    vetados = lista_negra.activas_de(conn, ids) if ids else set()
    nuevos = [i for i in ids if i not in ya and i not in vetados]
    sin_consentimiento = set()
    if nuevos:
        salida = convocar(conn, encuesta_id, ids_persona=nuevos, actor=actor,
                          a_mano=True)
        sin_consentimiento = {str(i) for i in salida["sin_consentimiento"]}
    for linea, id_persona in resueltos.items():
        resultado[linea] = (YA_ESTABA if id_persona in ya
                            else EN_LISTA_NEGRA if id_persona in vetados
                            else SIN_CONSENTIMIENTO if id_persona in sin_consentimiento
                            else AGREGADO)

    detalle = [{"linea": l, "resultado": resultado[l],
                "etiqueta": RESULTADOS_DE_AGREGAR[resultado[l]],
                "id_persona": resueltos.get(l)}
               for l in dict.fromkeys(lineas)]
    conteo = {clave: 0 for clave in RESULTADOS_DE_AGREGAR}
    for fila in detalle:
        conteo[fila["resultado"]] += 1
    return {"encuesta_id": encuesta_id, "lineas": detalle, "conteo": conteo,
            "etiquetas": RESULTADOS_DE_AGREGAR}

def _seleccion_de_la_consulta(conn, resultado, definicion, cantidad=None,
                              segmento_completo=False):
    """R-ALE.3 / R-AU.2 — a quiénes toma una encuesta de una consulta.

    Devuelve `(ids, modo, semilla)`, en el orden de la selección:

    * `segmento_completo` (solo sin criterios semánticos): todo el segmento,
      no solo lo que se mostró, en el orden del sorteo.
    * una demográfica con `cantidad`: las primeras `cantidad` **del sorteo**
      con la semilla del resultado —la misma selección que se vio, extendida
      si se piden más de las que entraban en pantalla—, nunca las primeras por
      nombre.
    * una semántica: la selección vigente, la sorteada o la del ranking según
      el modo elegido; con `cantidad`, sus primeras `cantidad`.
    """
    from . import consultas, demografia

    resultado = resultado or {}
    seleccion = resultado.get("seleccion") or {}
    definicion = definicion or resultado.get("definicion") or {}
    semilla = consultas.normalizar_semilla(seleccion.get("semilla")
                                           or definicion.get("semilla"))
    criterios = definicion.get("criterios") or resultado.get("criterios") or []
    semanticos = [c for c in criterios
                  if isinstance(c, str) or (c or {}).get("tipo") == "semantico"]
    if cantidad is not None:
        try:
            cantidad = int(cantidad)
        except (TypeError, ValueError):
            raise DatosInvalidos("La cantidad tiene que ser un número.") from None
        if cantidad < 1:
            raise DatosInvalidos("La cantidad tiene que ser al menos 1.")

    if segmento_completo or (cantidad is not None and not semanticos and criterios):
        if semanticos:
            raise DatosInvalidos(
                "Todo el segmento solo se puede tomar de una consulta sin criterios "
                "semánticos: en una semántica la selección es la que se verificó.",
                {"motivo": "segmento_con_semanticos"})
        normalizada = consultas.normalizar_definicion(
            {**definicion, "criterios": criterios}, conn)
        semilla = semilla or consultas.nueva_semilla()
        ids = demografia.ids_del_segmento(
            conn, [c for c in normalizada["criterios"] if c["tipo"] == "demografico"],
            panel_id=normalizada["panel_id"], semilla=semilla,
            limite=None if segmento_completo else cantidad)
        return (ids, consultas.SEGMENTO_COMPLETO if segmento_completo
                else consultas.ALEATORIO, semilla)

    ids = list(dict.fromkeys(str(item["id_persona"]) for item in resultado.get("items") or []
                             if item.get("id_persona")))
    if cantidad is not None:
        ids = ids[:cantidad]
    modo = seleccion.get("modo") or (consultas.ALEATORIO if not semanticos
                                     else consultas.RANKING)
    return ids, modo, semilla


def _sumar_audiencia(conn, encuesta, ids, modo, semilla, definicion, via, actor=None,
                     pedidas=None, fue_semantica=False):
    """Suma a la audiencia, por **el mismo camino que convocar** (lista negra
    y consentimiento de contacto), a quienes no estaban, y deja el paso
    registrado en `encuesta_audiencia_agregado`: quién, cuándo, la definición
    de la consulta, el modo, la semilla y cuántas personas. La audiencia se
    reconstruye paso a paso."""
    import json

    from . import auditoria

    ya = {str(f["id_persona"]) for f in db.todas(
        conn, """select id_persona from participacion
                  where encuesta_id = %s and convocado and id_persona = any(%s::uuid[])""",
        (encuesta["id"], ids))} if ids else set()
    existentes = {str(f["id_persona"]) for f in db.todas(
        conn, "select id_persona from persona where id_persona = any(%s::uuid[])",
        (ids,))} if ids else set()
    nuevos = [i for i in ids if i in existentes and i not in ya]
    convocatoria = {"convocados_nuevos": 0, "convocados_total": 0,
                    "sin_consentimiento": [], "en_lista_negra": 0}
    if nuevos:
        convocatoria = convocar(conn, encuesta["id"], nuevos, _desde_consulta=True,
                                actor=actor)
    excluidas = {
        "ya_estaban": len(ya),
        "en_lista_negra": convocatoria["en_lista_negra"],
        "sin_consentimiento": len(convocatoria["sin_consentimiento"]),
        "no_encontrados": len(ids) - len(existentes),
    }
    agregadas = convocatoria["convocados_total"]
    db.ejecutar(
        conn,
        """insert into encuesta_audiencia_agregado
                  (encuesta_id, via, modo, semilla, definicion, pedidas,
                   en_la_seleccion, agregadas, excluidas, actor_uid, actor_email)
           values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s)""",
        (encuesta["id"], via, modo, semilla,
         json.dumps(definicion or {}, ensure_ascii=False, default=str), pedidas,
         len(ids), agregadas, json.dumps(excluidas), getattr(actor, "uid", None),
         getattr(actor, "email", None)))
    if fue_semantica and ids:
        # Fijar una lista de personas a partir de una consulta semántica es,
        # como crear un panel desde ella (R3.11), el momento en que la
        # consulta deja de ser una pregunta: queda como reidentificación.
        auditoria.registrar_reidentificacion(
            conn, ids, actor=actor, motivo="convocatoria",
            contexto={"encuesta_id": encuesta["id"], "nombre": encuesta["nombre"],
                      "personas": len(ids), "via": via})
    return {"en_la_seleccion": len(ids), "agregadas": agregadas,
            "excluidas": excluidas, "modo": modo, "semilla": semilla}


def _fue_semantica(resultado, definicion):
    return bool([c for c in ((definicion or (resultado or {}).get("definicion") or {})
                             .get("criterios") or [])
                 if isinstance(c, str) or (c or {}).get("tipo") == "semantico"]) \
        or bool((resultado or {}).get("diagnostico", {}).get("abrio_semantica"))


def crear_desde_consulta(conn, nombre, resultado, actor=None, fecha_campo=None,
                         publico_objetivo=None, descripcion=None, clientes=None,
                         definicion=None, segmento_completo=False, cantidad=None,
                         url_alchemer=None):
    """R-EU.5 — «crear encuesta con estos panelistas».

    Una encuesta **independiente** cuya audiencia es la **foto** de la
    selección: las personas de ese momento quedan convocadas
    (`participacion.convocado`) y la consulta no se vuelve a ejecutar. No se
    asocia a ningún panel. Quien está en la lista negra o no tiene
    `contacto_participacion` vigente queda afuera, y el resumen dice cuántos y
    por qué (la regla es la de siempre: `convocar`).

    R-ALE.3 (D89) — la audiencia es **la selección vigente** (la sorteada, o
    la del ranking si se eligió ranking), no las primeras por nombre; de una
    demográfica se puede tomar además **todo el segmento**. La encuesta
    guarda cómo se armó: modo y semilla (`encuesta_audiencia_agregado`).

    Fijar una lista de personas a partir de una consulta semántica es, como
    crear un panel desde ella (R3.11), un momento en que la consulta deja de
    ser una pregunta: queda registrado como reidentificación.
    """
    ids, modo, semilla = _seleccion_de_la_consulta(
        conn, resultado, definicion, cantidad=cantidad,
        segmento_completo=segmento_completo)
    if not ids:
        raise DatosInvalidos(
            "El resultado no tiene ningún individuo, así que no hay audiencia "
            "para la encuesta.")
    encuesta = crear(conn, None, nombre, fecha_campo=fecha_campo,
                     tipo=INDEPENDIENTE, origen=ORIGEN_CONSULTA,
                     publico_objetivo=publico_objetivo,
                     descripcion=descripcion, clientes=clientes,
                     url_alchemer=url_alchemer)
    fue_semantica = _fue_semantica(resultado, definicion)
    paso = _sumar_audiencia(
        conn, encuesta, ids, modo, semilla,
        definicion or (resultado or {}).get("definicion"), "crear_desde_consulta",
        actor=actor, pedidas=cantidad, fue_semantica=fue_semantica)
    return {
        **obtener(conn, encuesta["id"], completa=True),
        "audiencia": {
            "en_el_resultado": len(ids),
            "convocados": paso["agregadas"],
            "sin_consentimiento": paso["excluidas"]["sin_consentimiento"],
            "en_lista_negra": paso["excluidas"]["en_lista_negra"],
            "no_encontrados": paso["excluidas"]["no_encontrados"],
            "modo": modo, "semilla": semilla,
        },
        "registrado_como_reidentificacion": fue_semantica,
    }


def agregar_audiencia_desde_consulta(conn, encuesta_id, resultado, definicion=None,
                                     actor=None, cantidad=None, segmento_completo=False):
    """R-AU.2 (D90) — «Agregar a la encuesta X» desde el resultado de una
    consulta: una cantidad (sorteada con semilla, o según el modo elegido en
    una semántica) o todo el segmento (sin criterios semánticos).

    Entra por el mismo camino que convocar —lista negra, consentimiento de
    contacto— y quien ya estaba en la audiencia no cuenta dos veces. Una
    encuesta cerrada no admite agregar. Queda registrado el paso.
    """
    encuesta = obtener(conn, encuesta_id)
    if encuesta["estado"] == "cerrada":
        raise Conflicto("La encuesta está cerrada: no admite agregar audiencia. "
                        "Reabrila primero.", {"motivo": "encuesta_cerrada"})
    if encuesta["panel_id"]:
        _exigir_panel_activo(conn, encuesta["panel_id"])
    ids, modo, semilla = _seleccion_de_la_consulta(
        conn, resultado, definicion, cantidad=cantidad,
        segmento_completo=segmento_completo)
    if not ids:
        raise DatosInvalidos("La selección no tiene ninguna persona para agregar.")
    paso = _sumar_audiencia(
        conn, encuesta, ids, modo, semilla,
        definicion or (resultado or {}).get("definicion"), "agregar_desde_consulta",
        actor=actor, pedidas=cantidad, fue_semantica=_fue_semantica(resultado, definicion))
    return {"encuesta_id": encuesta_id, **paso,
            "etiquetas": MOTIVOS_DE_AUDIENCIA,
            **conteos(conn, encuesta_id)}


MOTIVOS_DE_AUDIENCIA = {
    "ya_estaban": "Ya estaban en la audiencia",
    "en_lista_negra": "En lista negra",
    "sin_consentimiento": "Sin consentimiento de contacto",
    "no_encontrados": "Ya no existen (se dieron de baja)",
}


def pasos_de_audiencia(conn, encuesta_id):
    """R-AU.2 — cómo se armó la audiencia desde consultas, paso a paso: el
    más viejo primero, para leerla en el orden en que se hizo."""
    obtener(conn, encuesta_id)
    return [{
        "via": f["via"], "modo": f["modo"], "semilla": f["semilla"],
        "definicion": f["definicion"], "pedidas": f["pedidas"],
        "en_la_seleccion": f["en_la_seleccion"], "agregadas": f["agregadas"],
        "excluidas": f["excluidas"],
        "actor": f["actor_email"] or f["actor_uid"] or "sistema",
        "creado_en": f["creado_en"].isoformat(),
    } for f in db.todas(
        conn, """select * from encuesta_audiencia_agregado where encuesta_id = %s
                  order by creado_en, id""", (encuesta_id,))]


def listar_participacion(conn, encuesta_id):
    obtener(conn, encuesta_id)
    filas = db.todas(
        conn,
        """
        select pa.id_persona, pa.convocado, pa.convocado_en, pa.respondio,
               pa.respondio_en, pa.calidad_estado, p.nombre, p.email,
               exists (select 1 from consentimiento c
                        where c.id_persona = pa.id_persona
                          and c.finalidad = 'uso_semantico'
                          and c.estado = 'vigente') as consiente_semantico,
               (select id_en_origen from alias_origen a
                 where a.id_persona = pa.id_persona limit 1) as id_en_origen,
               -- Panelista es quien tiene una membresía activa en algún
               -- panel; independiente, quien no (llegó solo por archivos).
               exists (select 1 from membresia m
                        where m.id_persona = pa.id_persona
                          and m.estado = 'activo') as es_panelista
          from participacion pa join persona p on p.id_persona = pa.id_persona
         where pa.encuesta_id = %s
         order by p.nombre nulls last
        """,
        (encuesta_id,),
    )
    return [_fila_de_participacion(f) for f in filas]


POR_PAGINA_PARTICIPACION = 50


def _si_no(valor):
    """`sí`/`no` (o `true`/`false`, `1`/`0`) → bool; vacío → None."""
    if valor in (None, ""):
        return None
    texto = str(valor).strip().lower()
    if texto in ("si", "sí", "true", "1", "s"):
        return True
    if texto in ("no", "false", "0", "n"):
        return False
    raise DatosInvalidos(f"Filtro inválido: {valor!r} (sí o no).")


def pagina_de_participacion(conn, encuesta_id, pagina=1, por_pagina=None, buscar=None,
                            respondio=None, convocado=None, mensaje_id=None,
                            recibio=None):
    """R-ENC.1 (D90) — la lista de respondentes o de convocatoria, **paginada
    en el servidor**, con búsqueda y filtros.

    Una encuesta como la de ANTEL tiene más de mil personas: traerlas todas
    de una vez dejaba la pantalla sin forma de encontrar a nadie. Se busca
    por nombre o identificador (id_persona, correo o id en el origen) y se
    filtra por respondió, convocado y por un mensaje de la encuesta
    (`recibio`: lo recibió o no; por defecto, lo recibió). Los conteos de
    arriba son de toda la encuesta, no de la página.
    """
    obtener(conn, encuesta_id)
    por_pagina = max(1, min(int(por_pagina or POR_PAGINA_PARTICIPACION), 200))
    pagina = max(1, int(pagina or 1))
    respondio, convocado = _si_no(respondio), _si_no(convocado)
    recibio = True if recibio in (None, "") else _si_no(recibio)
    texto = (buscar or "").strip()
    donde = ["pa.encuesta_id = %(encuesta)s"]
    if texto:
        donde.append("""(p.nombre ilike %(patron)s or p.email ilike %(patron)s
                          or pa.id_persona::text ilike %(prefijo)s
                          or exists (select 1 from alias_origen a
                                      where a.id_persona = pa.id_persona
                                        and a.id_en_origen ilike %(patron)s))""")
    if respondio is not None:
        donde.append("pa.respondio = %(respondio)s")
    if convocado is not None:
        donde.append("pa.convocado = %(convocado)s")
    if mensaje_id:
        from . import mensajes

        mensajes.obtener(conn, encuesta_id, int(mensaje_id))
        negado = "" if recibio else "not "
        donde.append(f"""{negado}exists (select 1 from envio e
                                  where e.encuesta_mensaje_id = %(mensaje)s
                                    and e.id_persona = pa.id_persona
                                    and e.estado = any(%(recibidos)s))""")
    from . import mensajes as mod_mensajes

    parametros = {"encuesta": encuesta_id, "patron": f"%{texto}%", "prefijo": f"{texto}%",
                  "respondio": respondio, "convocado": convocado,
                  "mensaje": int(mensaje_id) if mensaje_id else None,
                  "recibidos": list(mod_mensajes.RECIBIDOS),
                  "limite": por_pagina, "desde": (pagina - 1) * por_pagina}
    condicion = " and ".join(donde)
    total = db.una(
        conn,
        f"""select count(*)::int as n from participacion pa
              join persona p on p.id_persona = pa.id_persona where {condicion}""",
        parametros)["n"]
    filas = db.todas(
        conn,
        f"""
        select pa.id_persona, pa.convocado, pa.convocado_en, pa.respondio,
               pa.respondio_en, pa.calidad_estado, p.nombre, p.email,
               exists (select 1 from consentimiento c
                        where c.id_persona = pa.id_persona
                          and c.finalidad = 'uso_semantico'
                          and c.estado = 'vigente') as consiente_semantico,
               (select id_en_origen from alias_origen a
                 where a.id_persona = pa.id_persona limit 1) as id_en_origen,
               exists (select 1 from membresia m
                        where m.id_persona = pa.id_persona
                          and m.estado = 'activo') as es_panelista
          from participacion pa join persona p on p.id_persona = pa.id_persona
         where {condicion}
         order by p.nombre nulls last, pa.id_persona
         limit %(limite)s offset %(desde)s
        """, parametros)
    items = [_fila_de_participacion(f) for f in filas]
    envios = mod_mensajes.envios_por_persona(conn, encuesta_id,
                                             ids_persona=[i["id_persona"] for i in items])
    for item in items:
        item["mensajes"] = envios.get(item["id_persona"], [])
    return {
        "items": items, "total": total, "pagina": pagina, "por_pagina": por_pagina,
        "paginas": max(1, -(-total // por_pagina)),
        "filtros": {"buscar": texto or None, "respondio": respondio,
                    "convocado": convocado, "mensaje_id": parametros["mensaje"],
                    "recibio": recibio if mensaje_id else None},
        "conteos": conteos(conn, encuesta_id),
    }


def _fila_de_participacion(f):
    return {
        "id_persona": str(f["id_persona"]),
        "nombre": f["nombre"],
        "email": f["email"],
        "id_en_origen": f["id_en_origen"],
        # `convocado_en` no es una fecha de convocatoria si `convocado`
        # es falso: es cuándo se creó la fila (la carga que lo trajo).
        # La pantalla mira `convocado` antes de mostrarla.
        "convocado": f["convocado"],
        "convocado_en": f["convocado_en"].isoformat() if f["convocado"] else None,
        "registrado_en": f["convocado_en"].isoformat(),
        "respondio": f["respondio"],
        "respondio_en": f["respondio_en"].isoformat() if f["respondio_en"] else None,
        "calidad_estado": f["calidad_estado"],
        "consiente_semantico": f["consiente_semantico"],
        "es_panelista": f["es_panelista"],
    }


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
                   (encuesta_id, id_persona, origen, respondio, respondio_en,
                    convocado)
                 values (%s, %s, 'importacion', true, now(), false)
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
                "valores_sin_categoria": [], "mapeo_de_categorias": []}

    por_variable = {variable: campo for campo, variable in campos.items()}
    # R-MAP.1 — el mapeo declarado en la pantalla, por variable.
    mapeos = sav.mapeos_por_variable(demograficas)
    opciones_por_variable = opciones_por_variable or {}
    completados, discrepancias = 0, []
    # R3.14.c — los valores del archivo que no corresponden a ninguna
    # categoría del atributo. No se inventa la categoría: la fila queda sin
    # ese atributo y se informa, con el listado para que se pueda corregir el
    # vocabulario y después recalcular.
    sin_categoria = {}
    # R-MAP.3 — el informe por atributo: qué valor se mapeó y a qué, cuál
    # quedó sin mapear, y cuántas personas afecta cada uno. Sin esto, un
    # código que nadie mapeó desaparece en silencio y se descubre semanas
    # después en una cuota que no cierra.
    conteo = {}
    vistos = set()
    for fila in filas:
        id_en_origen = str(fila.get(columna_id) or "").strip()
        id_persona = mapa.get(id_en_origen)
        if not id_persona or id_persona in vistos:
            continue
        vistos.add(id_persona)

        datos, crudos = {}, {}
        for variable, campo in por_variable.items():
            bruto = fila.get(variable)
            texto = "" if bruto is None else str(bruto).strip()
            mapeo = mapeos.get(variable)
            if mapeo:
                # Declarado: el mapeo manda, y lo que no está en él es un
                # valor **sin mapear**, no un valor para adivinar por
                # etiqueta. Adivinarlo sería desoír lo que dijo quien carga.
                if not texto:
                    continue
                canonico = mapeo.get(texto)
                registro = conteo.setdefault(
                    (campo, texto), {"categoria": canonico, "personas": 0})
                registro["personas"] += 1
                if canonico is None:
                    # R-MAP.4 — la persona queda sin el atributo, pero el
                    # crudo se guarda para poder remapear.
                    datos[campo] = None
                    crudos[campo] = texto
                    continue
                datos[campo] = canonico
                crudos[campo] = texto
                continue
            # Sin mapeo declarado, la resolución es la de siempre: el
            # código contra las etiquetas del archivo y la etiqueta contra el
            # catálogo. El crudo que se guarda es esa etiqueta y **no** el
            # código, porque es contra ella que `recalcular()` vuelve a
            # resolver (R3.14.h). Guardar el código dejaría esos valores
            # irrecuperables sin un mapeo explícito.
            valor = sav.valor_demografico(
                campo, bruto, opciones_por_variable.get(variable))
            if valor:
                datos[campo] = valor
        # Los que quedaron en `None` no viajan como valor: se escriben
        # aparte, como crudo pendiente.
        pendientes = {c: crudos[c] for c, v in datos.items() if v is None}
        datos = {c: v for c, v in datos.items() if v is not None}
        if pendientes:
            personas.guardar_crudos_pendientes(
                conn, id_persona, pendientes, origen=origen)
        if not datos:
            continue

        resultado = personas.completar_desde_archivo(
            conn, id_persona, datos, origen=origen,
            fecha_referencia=fecha_referencia, crudos=crudos,
            guardar_crudo=True)
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
        "mapeo_de_categorias": _informe_de_mapeo(conteo),
    }


def _informe_de_mapeo(conteo):
    """R-MAP.3 — por atributo: qué se mapeó, qué no, y a cuántos afecta."""
    por_atributo = {}
    for (campo, valor), registro in conteo.items():
        entrada = por_atributo.setdefault(
            campo, {"atributo": campo, "mapeados": [], "sin_mapear": []})
        destino = ("mapeados" if registro["categoria"] is not None
                   else "sin_mapear")
        entrada[destino].append({
            "valor": valor,
            **({"categoria": registro["categoria"]}
               if registro["categoria"] is not None else {}),
            "personas": registro["personas"],
        })
    for entrada in por_atributo.values():
        entrada["mapeados"].sort(key=lambda v: v["valor"])
        entrada["sin_mapear"].sort(key=lambda v: v["valor"])
    return [por_atributo[c] for c in sorted(por_atributo)]


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

    # Un id sin letras ni dígitos no se busca, ni en las participaciones ni
    # en los alias: «...........» pudo haber quedado registrado antes.
    valores, invalidos = mod_ingesta.separar_invalidos(valores)

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
    mapa = {k: v for k, v in mapa.items() if mod_ingesta.tiene_contenido(k)}
    return mapa, {**invalidos,
                  **{v: mod_ingesta.SIN_ALIAS for v in valores if v not in mapa}}


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


def carga_por_defecto(conn, encuesta_id, actor=None):
    """La carga a la que va una ingesta que no nombró ninguna: la más
    reciente de la encuesta, o una nueva si no tiene.

    Existe para lo que llega por `ingestar(encuesta_id, …)` —las pruebas y
    un trabajo diferido encolado antes de la 0032—: desde D81 toda respuesta
    entra por una carga, y no hay una segunda implementación de la ingesta.
    """
    from . import cargas

    fila = db.una(
        conn, "select id from carga where encuesta_id = %s "
              "order by creado_en desc, id desc limit 1", (encuesta_id,))
    if fila:
        return fila["id"]
    encuesta = obtener(conn, encuesta_id)
    return cargas.crear(conn, encuesta["nombre"], actor=actor,
                        encuesta_id=encuesta_id)["id"]


def ingestar(conn_boveda, conn_semantica, encuesta_id, preguntas, filas,
             columna_id="id_en_origen", origen=None, proveedor=None,
             demograficas=None, tipo_identificador=None, normalizacion=None):
    """Ingesta en la encuesta por su carga más reciente (D81).

    Desde la unificación **toda ingesta es una carga** (`cargas.ingestar`):
    esto solo elige la carga (`carga_por_defecto`) y delega. La pantalla y
    las rutas nombran la carga; esta firma queda para quien ingesta «en la
    encuesta» sin haberla creado.
    """
    from . import cargas

    carga_id = carga_por_defecto(conn_boveda, encuesta_id)
    resultado = cargas.ingestar(
        conn_boveda, conn_semantica, carga_id, preguntas, filas,
        columna_id=columna_id, origen=origen, proveedor=proveedor,
        demograficas=demograficas, tipo_identificador=tipo_identificador,
        normalizacion=normalizacion)
    resultado["encuesta_id"] = encuesta_id
    return resultado


def resolver_identidades(conn, encuesta_id, tipo, valores, origen):
    """La resolución de la columna de identidad para una carga de esta
    encuesta: la de `_resolver_identidades` (participaciones y alias)."""
    return _resolver_identidades(conn, encuesta_id, tipo, valores, origen)


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


# ── R4.5 · El envío por WhatsApp Flow ────────────────────────────────
#
# D87 — la sección «WhatsApp Flow» se unificó en los mensajes de la encuesta
# (`mensajes.py`): el Flow es un tipo de botón de la plantilla de un mensaje,
# con los mismos cuatro públicos, la misma regla de elegibilidad y el mismo
# registro en `envio`. Lo que la sección vieja envió se migró a `envio`
# (boveda/0036). Las columnas `encuesta.flow_*` y `participacion.envio_*`
# quedan sin uso hasta que una versión posterior las quite: nada las escribe.
