"""R-CF.4 a R-CF.8 — Los mensajes de una encuesta: invitar, recordar y
agradecer desde el sistema (`specs/SPEC_convocatoria_y_mensajes.md`, D85).

Hasta acá el sistema **registraba** el campo; esto lo **opera**. Cada encuesta
tiene sus mensajes —un nombre libre y un canal—, y cada mensaje se envía a uno
de cuatro públicos. Lo que no hay que deshacer:

**Nunca el mismo mensaje dos veces a la misma persona por error.** «No enviado
aún» se calcula sobre el registro (`envio`): quien tiene un envío aceptado,
entregado o leído de ese mensaje no lo vuelve a recibir; un fallido cuenta como
no enviado, así que el reintento lo incluye. Un envío en curso no se lanza dos
veces para el mismo mensaje: lo impide el índice de la `0029` (un trabajo
activo por destino, y el destino es el mensaje). Y dentro de un envío, cada
persona se registra y se commitea apenas sale: si Cloud Tasks reintenta un
lote, quien ya tiene su fila en este trabajo no se vuelve a mandar.

**Quién lo recibe lo decide la regla de siempre** (`preferencias.filtrar_
contactables`): lista negra primero, consentimiento de contacto, preferencia
por el canal y un celular (o un correo) válido. Se reaplica en cada lote, no
solo en la vista previa: alguien que entra a la lista negra a mitad del envío
no recibe nada. Es la misma regla de los dos ejes de R4.4, y es lo que hace
que R-CF.8 «respete el canal que aceptó cada panelista» sin una regla nueva.

**El primer mensaje convoca, los siguientes no.** El primer envío aceptado a
una persona en una encuesta marca su participación como convocada, con esa
fecha. Los siguientes no son convocatorias nuevas: no inflan la fatiga. Un
agradecimiento a quien ya respondió tampoco la convoca —ya respondió—.

**«No contestaron» son las respuestas ingestadas**, no las de Alchemer en
tiempo real (non-goal de la spec). Recordar sobre la última carga alcanza.
"""

import json

from . import db, diferida, encuestas, preferencias, whatsapp
from .errores import Conflicto, DatosInvalidos, NoEncontrado

OPERACION = "envio_mensaje"
DESTINO = "mensaje"
MENSAJES_POR_LOTE = 50

WHATSAPP = "whatsapp"
CORREO = "correo"
CANALES = (WHATSAPP, CORREO)
# El canal del mensaje → la preferencia de canal que tiene que tener la
# persona (R4.4).
PREFERENCIA_DE = {WHATSAPP: preferencias.WHATSAPP, CORREO: preferencias.EMAIL}

# Qué puede ir en cada `{{n}}` del texto de una plantilla. Un conjunto fijo:
# lo que no está acá no se manda.
NOMBRE_PANELISTA = "nombre_panelista"
NOMBRE_ENCUESTA = "nombre_encuesta"
FUENTES_DE_PARAMETRO = {
    NOMBRE_PANELISTA: "Nombre del panelista",
    NOMBRE_ENCUESTA: "Nombre de la encuesta",
}
# Meta rechaza un parámetro vacío: sin nombre, se saluda igual.
NOMBRE_POR_DEFECTO = "panelista"

TODOS = "todos"
NO_ENVIADO = "no_enviado"
NO_CONTESTARON = "no_contestaron"
RESPONDIERON = "respondieron"
PUBLICOS = {
    TODOS: "Todos",
    NO_ENVIADO: "No enviado aún",
    NO_CONTESTARON: "No contestaron",
    RESPONDIERON: "Respondieron",
}

ACEPTADO = "aceptado"
FALLIDO = "fallido"
ENTREGADO = "entregado"
LEIDO = "leido"
# Lo que cuenta como «ya lo recibió». Un fallido no. Hoy solo se escriben
# `aceptado` y `fallido`: entregado y leído llegarían por el webhook de Meta
# (R-CF.7), diferido. La base ya los admite y cada envío guarda su
# `id_mensaje_meta`, así que sumarlos no exige migrar ni pierde lo enviado.
RECIBIDOS = (ACEPTADO, ENTREGADO, LEIDO)

MOTIVOS_DE_EXCLUSION = {
    "lista_negra": "En lista negra",
    "sin_consentimiento": "Sin consentimiento de contacto",
    "sin_preferencia_whatsapp": "No aceptó WhatsApp como canal",
    "sin_preferencia_email": "No aceptó el correo como canal",
    "sin_celular": "Sin celular",
    "celular_invalido": "Celular inválido",
    "sin_email": "Sin correo",
    "no_existe": "Ya no existe (se dio de baja)",
}

LIMITE_DIARIO_CORREO = 2000


# ════════════════════════════════════════════════════════════════════
#  R-CF.4 · Los mensajes
# ════════════════════════════════════════════════════════════════════

def _serializar(fila, conteos=None):
    conteos = conteos or {}
    return {
        "id": fila["id"],
        "encuesta_id": fila["encuesta_id"],
        "nombre": fila["nombre"],
        "canal": fila["canal"],
        "plantilla": fila["plantilla"],
        "plantilla_idioma": fila["plantilla_idioma"],
        "parametros": fila["parametros"] or [],
        "url_boton": fila["url_boton"],
        "asunto": fila["asunto"],
        "cuerpo": fila["cuerpo"],
        "creado_en": fila["creado_en"].isoformat(),
        "recibieron": conteos.get("recibieron", 0),
        "fallidos": conteos.get("fallidos", 0),
        "ultimo_envio": conteos.get("ultimo_envio"),
    }


def _conteos(conn, ids_mensaje):
    if not ids_mensaje:
        return {}
    return {f["encuesta_mensaje_id"]: {
                "recibieron": f["recibieron"], "fallidos": f["fallidos"],
                "ultimo_envio": f["ultimo"].isoformat() if f["ultimo"] else None}
            for f in db.todas(
                conn,
                """select encuesta_mensaje_id,
                          count(distinct id_persona) filter (
                              where estado = any(%s))::int        as recibieron,
                          count(*) filter (where estado = 'fallido')::int as fallidos,
                          max(enviado_en)                          as ultimo
                     from envio where encuesta_mensaje_id = any(%s)
                    group by 1""",
                (list(RECIBIDOS), list(ids_mensaje)))}


def listar(conn, encuesta_id):
    encuestas.obtener(conn, encuesta_id)
    filas = db.todas(conn, "select * from encuesta_mensaje where encuesta_id = %s "
                           "order by creado_en, id", (encuesta_id,))
    conteos = _conteos(conn, [f["id"] for f in filas])
    return [_serializar(f, conteos.get(f["id"])) for f in filas]


def obtener(conn, encuesta_id, mensaje_id):
    fila = db.una(conn, "select * from encuesta_mensaje where id = %s and encuesta_id = %s",
                  (mensaje_id, encuesta_id))
    if not fila:
        raise NoEncontrado(f"No existe el mensaje {mensaje_id} de esa encuesta.")
    return _serializar(fila, _conteos(conn, [fila["id"]]).get(fila["id"]))


def plantillas_para_mensajes(entorno=None, pedir=None):
    """R-CF.4 — las plantillas aprobadas que sirven para un mensaje: las de
    botón de URL (la invitación y el recordatorio), las de Flow (R4.5) y las
    de solo texto (un agradecimiento). Hoy `listar_plantillas` filtraba por
    Flow; para los mensajes no."""
    salida = whatsapp.listar_plantillas(entorno=entorno, pedir=pedir,
                                        solo_con_flow=False)
    salida["fuentes_de_parametro"] = FUENTES_DE_PARAMETRO
    return salida


def _plantilla_aprobada(nombre, idioma, entorno=None, pedir=None):
    """La plantilla `(nombre, idioma)` si existe y está aprobada. Si no, el
    motivo, como `DatosInvalidos`: una plantilla no aprobada o pausada no se
    puede asociar ni enviar (R-CF.4)."""
    if not whatsapp.configurado(entorno) or not whatsapp.config(entorno)["waba_id"]:
        raise DatosInvalidos(
            "No hay credenciales de WhatsApp configuradas: no se pueden leer ni "
            "enviar plantillas.", {"motivo": "whatsapp_sin_configurar"})
    encontradas = whatsapp.candidatas(nombre, idioma, entorno=entorno, pedir=pedir)
    if len(encontradas) > 1:
        raise DatosInvalidos(
            f"Hay {len(encontradas)} plantillas «{nombre}», una por idioma: hay "
            f"que elegir el idioma.",
            {"idiomas": sorted(p["idioma"] for p in encontradas if p["idioma"])})
    if not encontradas:
        raise DatosInvalidos(f"No existe la plantilla «{nombre}» en la cuenta de Meta.",
                             {"motivo": "plantilla_inexistente"})
    plantilla = encontradas[0]
    if plantilla["estado"] != whatsapp.PLANTILLA_APROBADA:
        raise DatosInvalidos(
            f"La plantilla «{nombre}» está en «{plantilla['estado']}»: Meta tiene "
            f"que aprobarla antes de usarla.",
            {"motivo": "plantilla_no_aprobada", "estado": plantilla["estado"]})
    return plantilla


def guardar(conn, encuesta_id, datos, actor=None, mensaje_id=None,
            entorno=None, pedir=None):
    """Crea (o edita) un mensaje de la encuesta.

    WhatsApp: la plantilla tiene que estar aprobada, y cada `{{n}}` de su
    texto tiene que tener de dónde sale (`parametros`). Si su botón de URL
    no coincide con la URL de Alchemer de la encuesta, **se avisa antes de
    guardar** (`409 url_distinta`): es la forma más fácil de mandar a la gente
    a la encuesta equivocada. Guardar igual exige `confirmar_url_distinta`.

    Correo: asunto y cuerpo. `{nombre}`, `{encuesta}` y `{enlace}` se
    reemplazan al enviar; si el cuerpo no trae `{enlace}`, se agrega al final.
    """
    datos = datos or {}
    encuesta = encuestas.obtener(conn, encuesta_id)
    nombre = (datos.get("nombre") or "").strip()
    if not nombre:
        raise DatosInvalidos("El mensaje necesita un nombre: «Invitación», "
                             "«Recordatorio 2»…", {"campo": "nombre"})
    canal = (datos.get("canal") or WHATSAPP).strip().lower()
    if canal not in CANALES:
        raise DatosInvalidos(f"Canal desconocido: {canal!r}.", {"canales": list(CANALES)})

    valores = {"nombre": nombre, "canal": canal, "plantilla": None,
               "plantilla_idioma": None, "parametros": [], "url_boton": None,
               "asunto": None, "cuerpo": None}
    avisos = []
    if canal == WHATSAPP:
        plantilla = _plantilla_aprobada((datos.get("plantilla") or "").strip(),
                                        (datos.get("plantilla_idioma") or "").strip() or None,
                                        entorno=entorno, pedir=pedir)
        parametros = list(datos.get("parametros") or [])
        if len(parametros) != plantilla["parametros_cuerpo"]:
            raise DatosInvalidos(
                f"La plantilla tiene {plantilla['parametros_cuerpo']} parámetro(s) "
                f"en el texto y se indicaron {len(parametros)}: cada uno tiene que "
                f"tener de dónde sale.", {"campo": "parametros"})
        ajenos = [p for p in parametros if p not in FUENTES_DE_PARAMETRO]
        if ajenos:
            raise DatosInvalidos(f"Parámetro desconocido: {ajenos}.",
                                 {"fuentes": list(FUENTES_DE_PARAMETRO)})
        boton = plantilla.get("boton_url")
        valores.update(plantilla=plantilla["nombre"], plantilla_idioma=plantilla["idioma"],
                       parametros=parametros,
                       url_boton=boton["base"] if boton else None)
        if boton:
            distinta = None
            if not boton["dinamico"]:
                distinta = ("El botón de la plantilla tiene una URL fija: no puede "
                            "llevar el id_persona, y las respuestas volverían sin "
                            "saber de quién son.")
            elif not encuesta.get("url_alchemer"):
                distinta = ("La encuesta todavía no tiene su URL de Alchemer: no se "
                            "puede comprobar que el botón lleve a esta encuesta.")
            elif boton["base"] != encuesta["url_alchemer"]:
                distinta = (f"El botón de la plantilla lleva a {boton['base']} y la "
                            f"encuesta está en {encuesta['url_alchemer']}: la gente "
                            f"iría a otra encuesta.")
            if distinta:
                if not datos.get("confirmar_url_distinta"):
                    raise Conflicto(distinta, {
                        "motivo": "url_distinta", "url_boton": boton["base"],
                        "url_alchemer": encuesta.get("url_alchemer")})
                avisos.append(distinta)
    else:
        asunto = (datos.get("asunto") or "").strip()
        cuerpo = (datos.get("cuerpo") or "").strip()
        if not asunto or not cuerpo:
            raise DatosInvalidos("Un mensaje por correo necesita asunto y cuerpo.",
                                 {"campo": "asunto" if not asunto else "cuerpo"})
        valores.update(asunto=asunto, cuerpo=cuerpo)
        if not encuesta.get("url_alchemer"):
            avisos.append("La encuesta no tiene URL de Alchemer: el correo va a "
                          "salir sin enlace a la encuesta.")

    columnas = list(valores)
    try:
        if mensaje_id is None:
            fila = db.una(
                conn,
                f"""insert into encuesta_mensaje (encuesta_id, creado_por, {', '.join(columnas)})
                    values (%s, %s, {', '.join(['%s'] * len(columnas))})
                    returning id""",
                (encuesta_id, getattr(actor, "email", None) or getattr(actor, "uid", None),
                 *[json.dumps(v) if c == "parametros" else v for c, v in valores.items()]))
            mensaje_id = fila["id"]
        else:
            obtener(conn, encuesta_id, mensaje_id)
            db.ejecutar(
                conn,
                f"""update encuesta_mensaje
                       set {', '.join(c + ' = %s' for c in columnas)}, actualizado_en = now()
                     where id = %s""",
                (*[json.dumps(v) if c == "parametros" else v for c, v in valores.items()],
                 mensaje_id))
    except Exception as error:  # noqa: BLE001
        if "encuesta_mensaje_nombre_unico" in str(error):
            conn.rollback()
            raise Conflicto(f"La encuesta ya tiene un mensaje «{nombre}».",
                            {"campo": "nombre"}) from None
        raise
    salida = obtener(conn, encuesta_id, mensaje_id)
    salida["avisos"] = avisos
    return salida


def borrar(conn, encuesta_id, mensaje_id):
    """Un mensaje que ya se envió no se borra: su registro es lo que dice a
    quién se le escribió, y «no enviado aún» se calcula sobre él."""
    mensaje = obtener(conn, encuesta_id, mensaje_id)
    if db.una(conn, "select 1 as hay from envio where encuesta_mensaje_id = %s limit 1",
              (mensaje_id,)):
        raise Conflicto(f"El mensaje «{mensaje['nombre']}» ya se envió: no se borra, "
                        f"porque su registro es lo que dice a quién se le escribió.",
                        {"motivo": "mensaje_enviado"})
    db.ejecutar(conn, "delete from encuesta_mensaje where id = %s", (mensaje_id,))
    return {"borrado": mensaje_id}


# ════════════════════════════════════════════════════════════════════
#  R-CF.5 · Públicos, vista previa y envío
# ════════════════════════════════════════════════════════════════════

def publico(conn, encuesta_id, mensaje_id, cual):
    """Los `id_persona` de un público. La audiencia es la participación de la
    encuesta: convocados y quienes respondieron.

    - `todos`: toda la audiencia.
    - `no_enviado`: quienes no tienen un envío aceptado, entregado o leído
      **de este mensaje** (un fallido no cuenta), sin importar los demás.
    - `no_contestaron` / `respondieron`: según las respuestas **ingestadas**.
    """
    if cual not in PUBLICOS:
        raise DatosInvalidos(f"Público desconocido: {cual!r}.", {"publicos": list(PUBLICOS)})
    condicion = {
        TODOS: "true",
        NO_ENVIADO: """not exists (select 1 from envio e
                                    where e.encuesta_mensaje_id = %(mensaje)s
                                      and e.id_persona = pa.id_persona
                                      and e.estado = any(%(recibidos)s))""",
        NO_CONTESTARON: "not pa.respondio",
        RESPONDIERON: "pa.respondio",
    }[cual]
    return [str(f["id_persona"]) for f in db.todas(
        conn,
        f"""select pa.id_persona from participacion pa
             where pa.encuesta_id = %(encuesta)s and {condicion}
             order by pa.id_persona""",
        {"encuesta": encuesta_id, "mensaje": mensaje_id, "recibidos": list(RECIBIDOS)})]


def _contactables(conn, ids, canal):
    """La regla de siempre (R4.4, R-LN.2), más el correo presente para el
    canal correo: `filtrar_contactables` no lo exige."""
    contactables, excluidos = preferencias.filtrar_contactables(
        conn, ids, PREFERENCIA_DE[canal])
    if canal == CORREO and contactables:
        con_correo = {str(f["id_persona"]) for f in db.todas(
            conn, """select id_persona from persona
                      where id_persona = any(%s::uuid[])
                        and coalesce(trim(email), '') like '%%@%%'""", (contactables,))}
        sin = [i for i in contactables if i not in con_correo]
        if sin:
            excluidos.setdefault("sin_email", []).extend(sin)
            contactables = [i for i in contactables if i in con_correo]
    return contactables, excluidos


def _motivos_para_no_enviar(conn, encuesta, mensaje, entorno=None, pedir=None):
    """Por qué no se puede enviar este mensaje ahora. Vacío si se puede."""
    motivos = []
    if encuesta["estado"] != "en_campo":
        motivos.append(
            "La encuesta está cerrada: no se envían mensajes. Reabrila para enviar."
            if encuesta["estado"] == "cerrada" else
            "La encuesta está en borrador: los mensajes se envían con la encuesta "
            "en campo.")
    if mensaje["canal"] == WHATSAPP:
        try:
            _plantilla_aprobada(mensaje["plantilla"], mensaje["plantilla_idioma"],
                                entorno=entorno, pedir=pedir)
        except DatosInvalidos as error:
            motivos.append(error.mensaje)
    else:
        from . import verificacion_contacto as vc

        try:
            vc.proveedor_de_envio(entorno, canal=vc.EMAIL)
        except Exception as error:  # noqa: BLE001
            motivos.append(getattr(error, "mensaje", None) or str(error))
    return motivos


def previa(conn, encuesta_id, mensaje_id, cual, entorno=None, pedir=None):
    """R-CF.5 — a cuántos les va a llegar y quiénes quedan afuera, por
    motivo, antes de confirmar. Sale de la misma ruta que envía."""
    encuesta = encuestas.obtener(conn, encuesta_id)
    mensaje = obtener(conn, encuesta_id, mensaje_id)
    ids = publico(conn, encuesta_id, mensaje_id, cual)
    contactables, excluidos = _contactables(conn, ids, mensaje["canal"])
    ya = {str(f["id_persona"]) for f in db.todas(
        conn, """select distinct id_persona from envio
                  where encuesta_mensaje_id = %s and estado = any(%s)
                    and id_persona = any(%s::uuid[])""",
        (mensaje_id, list(RECIBIDOS), contactables))} if contactables else set()
    avisos = []
    if ya:
        # Con «Todos» se puede querer reenviar; que sea una decisión vista.
        avisos.append(f"{len(ya)} persona(s) ya recibieron este mensaje y lo van a "
                      f"recibir de nuevo. Para no repetírselo, elegí «No enviado aún».")
    if mensaje["canal"] == CORREO:
        from . import correo

        hoy = correo.enviados_ultimas_24h(conn)
        if hoy + len(contactables) > LIMITE_DIARIO_CORREO:
            avisos.append(f"Google Workspace admite {LIMITE_DIARIO_CORREO} destinatarios "
                          f"por día y en las últimas 24 horas ya salieron {hoy}: parte "
                          f"del envío puede ser rechazada.")
    motivos = _motivos_para_no_enviar(conn, encuesta, mensaje, entorno, pedir)
    if not contactables:
        motivos.append("Nadie del público elegido puede recibir este mensaje.")
    return {
        "encuesta_id": encuesta_id, "mensaje": mensaje, "publico": cual,
        "publico_etiqueta": PUBLICOS[cual],
        "en_el_publico": len(ids),
        "recibiran": len(contactables),
        "ya_recibieron": len(ya),
        "excluidos": {m: len(v) for m, v in excluidos.items()},
        "excluidos_total": sum(len(v) for v in excluidos.values()),
        "etiquetas": MOTIVOS_DE_EXCLUSION,
        "avisos": avisos,
        "motivos": motivos,
        "puede_enviar": not motivos,
        "_destinatarios": contactables,
    }


def lanzar(conn, encuesta_id, mensaje_id, cual, actor=None, solo_revisar=False,
           encolador=None, entorno=None, pedir=None, tamano_lote=None):
    """`(status, cuerpo)`. Con `solo_revisar`, la vista previa —de la misma
    ruta que envía—. Sin él, encola el envío en lotes (Cloud Tasks).

    Un envío en curso del mismo mensaje no se lanza dos veces: el destino de
    la vía diferida es el mensaje, y el índice de la 0029 admite uno activo.
    """
    vista = previa(conn, encuesta_id, mensaje_id, cual, entorno, pedir)
    destinatarios = vista.pop("_destinatarios")
    if solo_revisar:
        vista["envio_en_curso"] = _en_curso(conn, mensaje_id)
        return 200, vista
    if not vista["puede_enviar"]:
        raise Conflicto("No se puede enviar este mensaje: " + " ".join(vista["motivos"]),
                        {"motivos": vista["motivos"]})
    plan = {
        "operacion": OPERACION, "encuesta_id": encuesta_id, "mensaje_id": mensaje_id,
        "canal": vista["mensaje"]["canal"], "publico": cual,
        "lanzado_por": getattr(actor, "email", None) or getattr(actor, "uid", None),
    }
    salida = diferida.encolar(
        conn, DESTINO, mensaje_id, plan, [{"id_persona": i} for i in destinatarios],
        actor=actor, tamano_lote=tamano_lote or MENSAJES_POR_LOTE, encolador=encolador)
    salida["recibiran"] = vista["recibiran"]
    print(f"[mensajes] encuesta={encuesta_id} mensaje={mensaje_id} publico={cual} "
          f"destinatarios={len(destinatarios)} trabajo={salida.get('trabajo_id')}", flush=True)
    return 202, salida


def _en_curso(conn, mensaje_id):
    activo = diferida.trabajo_activo(conn, DESTINO, mensaje_id)
    return {"trabajo_id": activo["id"]} if activo else None


# ════════════════════════════════════════════════════════════════════
#  El lote: lo que corre la tarea de Cloud Tasks
# ════════════════════════════════════════════════════════════════════

def _textos(mensaje, encuesta, persona):
    valores = {NOMBRE_PANELISTA: (persona.get("nombre") or "").split(" ")[0]
               or NOMBRE_POR_DEFECTO,
               NOMBRE_ENCUESTA: encuesta["nombre"]}
    return [valores[p] for p in mensaje["parametros"]]


def correo_de(mensaje, encuesta, persona):
    """`(asunto, cuerpo)` del correo de una persona. `{nombre}`, `{encuesta}`
    y `{enlace}` se reemplazan; sin `{enlace}` en el cuerpo, va al final."""
    enlace = encuestas.enlace_de(encuesta, persona["id_persona"]) or ""
    reemplazos = {"{nombre}": (persona.get("nombre") or "").split(" ")[0]
                  or NOMBRE_POR_DEFECTO,
                  "{encuesta}": encuesta["nombre"], "{enlace}": enlace}
    asunto, cuerpo = mensaje["asunto"], mensaje["cuerpo"]
    if enlace and "{enlace}" not in cuerpo:
        cuerpo = f"{cuerpo}\n\n{enlace}"
    for clave, valor in reemplazos.items():
        asunto = asunto.replace(clave, valor)
        cuerpo = cuerpo.replace(clave, valor)
    return asunto, cuerpo


def _enviar_uno(conn, mensaje, encuesta, persona, entorno=None, pedir=None,
                enviador_correo=None):
    """`(estado, id_meta, error)` de un envío. No levanta por un fallo de la
    persona: se registra como fallido y el envío sigue."""
    if mensaje["canal"] == WHATSAPP:
        try:
            resultado = whatsapp.enviar_plantilla(
                preferencias.normalizar_celular(persona["celular"]) or persona["celular"],
                mensaje["plantilla"], mensaje["plantilla_idioma"],
                textos=_textos(mensaje, encuesta, persona),
                id_persona=persona["id_persona"],
                boton_url=({"dinamico": True, "indice": mensaje.get("_indice_boton", 0)}
                           if mensaje["url_boton"] else None),
                entorno=entorno, pedir=pedir)
            return ACEPTADO, resultado.get("message_id"), None
        except whatsapp.SinConfigurar:
            raise
        except Exception as error:  # noqa: BLE001 — se registra, no se propaga
            return FALLIDO, None, (getattr(error, "mensaje", None) or str(error))[:500]

    from . import correo, verificacion_contacto as vc

    asunto, cuerpo = correo_de(mensaje, encuesta, persona)
    try:
        enviador = enviador_correo or vc.proveedor_de_envio(entorno, canal=vc.EMAIL)
        vc.enviar_y_registrar(conn, enviador, vc.EMAIL, persona["email"].strip(),
                              persona["id_persona"], correo.MENSAJE_ENCUESTA,
                              asunto=asunto, cuerpo=cuerpo)
        return ACEPTADO, None, None
    except Exception as error:  # noqa: BLE001 — se registra, no se propaga
        # El motivo técnico queda en `envio_correo` (Cumplimiento → Contacto).
        return FALLIDO, None, (getattr(error, "mensaje", None) or str(error)
                               or "fallido")[:500]


def procesar_lote(conn, plan, filas, trabajo_id=None, entorno=None, pedir=None,
                  enviador_correo=None):
    """Envía el mensaje a las personas del lote.

    El gate se reaplica acá, en cada lote. Cada persona se registra y se
    commitea apenas sale: si la tarea se corta y Cloud Tasks la reintenta,
    quien ya tiene su fila **en este trabajo** se saltea, así que nadie recibe
    dos veces el mismo envío.
    """
    mensaje = obtener(conn, plan["encuesta_id"], plan["mensaje_id"])
    encuesta = encuestas.obtener(conn, plan["encuesta_id"])
    if mensaje["canal"] == WHATSAPP and mensaje["url_boton"]:
        plantilla = _plantilla_aprobada(mensaje["plantilla"], mensaje["plantilla_idioma"],
                                        entorno=entorno, pedir=pedir)
        mensaje["_indice_boton"] = (plantilla.get("boton_url") or {}).get("indice", 0)

    ids = [str(f["id_persona"]) for f in filas]
    contactables, excluidos = _contactables(conn, ids, mensaje["canal"])
    ya_en_este = {str(f["id_persona"]) for f in db.todas(
        conn, "select id_persona from envio where trabajo_id = %s", (trabajo_id,))} \
        if trabajo_id is not None else set()
    if plan.get("publico") == NO_ENVIADO and contactables:
        # Entre la vista previa y este lote pudo salir otro envío.
        ya_en_este |= {str(f["id_persona"]) for f in db.todas(
            conn, """select distinct id_persona from envio
                      where encuesta_mensaje_id = %s and estado = any(%s)
                        and id_persona = any(%s::uuid[])""",
            (mensaje["id"], list(RECIBIDOS), contactables))}
    personas = {str(f["id_persona"]): {**f, "id_persona": str(f["id_persona"])}
                for f in db.todas(
                    conn, """select id_persona, nombre, email, celular from persona
                              where id_persona = any(%s::uuid[])""", (contactables,))}

    enviados = fallidos = salteados = 0
    for id_persona in contactables:
        if id_persona in ya_en_este:
            salteados += 1
            continue
        estado, id_meta, error = _enviar_uno(conn, mensaje, encuesta, personas[id_persona],
                                             entorno, pedir, enviador_correo)
        db.ejecutar(
            conn,
            """insert into envio (encuesta_mensaje_id, id_persona, canal, trabajo_id,
                                  lanzado_por, estado, id_mensaje_meta, error)
               values (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (mensaje["id"], id_persona, mensaje["canal"], trabajo_id,
             plan.get("lanzado_por"), estado, id_meta, error))
        if estado == ACEPTADO:
            enviados += 1
            _convocar_con_el_primero(conn, encuesta["id"], id_persona, mensaje["id"])
        else:
            fallidos += 1
        conn.commit()
    return {"enviados": enviados, "fallidos": fallidos, "salteados": salteados,
            "excluidos_al_enviar": sum(len(v) for v in excluidos.values()),
            "operacion": OPERACION, "mensaje_id": mensaje["id"]}


def _convocar_con_el_primero(conn, encuesta_id, id_persona, mensaje_id=None):
    """R-CF.6 — el primer mensaje que recibe una persona en una encuesta la
    convoca, con esa fecha. Los siguientes no: no inflan la fatiga. A quien
    ya respondió no se lo convoca con un agradecimiento.

    Se llama con el envío actual ya registrado, así que «el primero» es que
    sea el único recibido de esa persona en toda la encuesta."""
    db.ejecutar(
        conn,
        """update participacion pa
              set convocado = true, convocado_en = now()
            where pa.encuesta_id = %s and pa.id_persona = %s
              and not pa.respondio
              and (select count(*) from envio e
                     join encuesta_mensaje m on m.id = e.encuesta_mensaje_id
                    where m.encuesta_id = pa.encuesta_id
                      and e.id_persona = pa.id_persona
                      and e.estado = any(%s)) = 1""",
        (encuesta_id, id_persona, list(RECIBIDOS)))


# ════════════════════════════════════════════════════════════════════
#  R-CF.6 · El registro, por panelista
# ════════════════════════════════════════════════════════════════════


def envios_por_persona(conn, encuesta_id):
    """`{id_persona: [{mensaje, canal, enviado_en, estado, error}]}` de una
    encuesta: lo que la sección de convocatoria muestra por panelista."""
    salida = {}
    for f in db.todas(
            conn,
            """select e.id_persona, m.nombre as mensaje, e.canal, e.enviado_en,
                      e.estado, e.error
                 from envio e join encuesta_mensaje m on m.id = e.encuesta_mensaje_id
                where m.encuesta_id = %s
                order by e.enviado_en, e.id""", (encuesta_id,)):
        salida.setdefault(str(f["id_persona"]), []).append({
            "mensaje": f["mensaje"], "canal": f["canal"],
            "enviado_en": f["enviado_en"].isoformat(), "estado": f["estado"],
            "error": f["error"]})
    return salida

