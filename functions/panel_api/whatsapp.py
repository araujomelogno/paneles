"""R4.5 — Envío de encuestas por WhatsApp Flow; D85 — mensajes de una
encuesta; D87 — el Flow es un tipo de botón de la plantilla de un mensaje.

**El sistema solo envía.** No recibe respuestas ni estados de entrega, no
crea ni edita Flows ni plantillas. (Los estados de entrega por webhook,
R-CF.7, quedaron para una versión posterior: el envío guarda el id que
devuelve Meta para poder sumarlos.) El Flow y la plantilla se arman en Meta y acá se
referencian por id; el analista baja las respuestas de Meta y las ingesta por
el flujo de siempre. Eso deja el modelo de ingesta sin tocar, que es
deliberado: WhatsApp es un canal de envío, no una plataforma de recolección.

Tres cosas que este módulo garantiza:

**Se valida antes de convocar, no al enviar.** Una plantilla necesita
aprobación de Meta y la revisión demora; un Flow válido no hace enviable una
plantilla rechazada. Descubrirlo recién cuando falla el envío significa haber
convocado a gente a la que no se le puede mandar nada.

**El `flow_token` es el `id_persona`.** El envío admite un token por
destinatario que vuelve con los datos del Flow. Poniendo ahí el `id_persona`,
cuando el analista baje las respuestas ese token viene como una columna más y
la ingesta mapea directo, sin PII y sin adivinar. Es la misma idea de la
precarga de R3.12, por otro canal, y cuesta cero ahora.

**Las credenciales no viven acá.** Token, `phone_number_id` y `WABA_ID` salen
del entorno (Secret Manager en producción). Sin configurar, el módulo se
degrada de forma visible —igual que el reranker y la verificación— en vez de
fingir que envió.

**Se elige la plantilla, y nada más.** Una plantilla de Meta ya trae adentro
su idioma y, en su botón de Flow, el `flow_id`. Pedir los tres por separado
era pedir tres veces el mismo dato y dejar que se contradigan.
`listar_plantillas()` devuelve las plantillas con su idioma, su botón (de URL
o de Flow) y su `flow_id` ya resueltos; el mensaje elige una de esa lista
(`mensajes.guardar`). Desde D87 no hay una sección «WhatsApp Flow» aparte:
un mensaje con plantilla de Flow tiene los mismos públicos, la misma regla de
elegibilidad y el mismo registro (`envio`) que uno con botón de URL.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .errores import Conflicto, DatosInvalidos

API = "https://graph.facebook.com/v21.0"

# Estados que Meta devuelve y que nos importan. El resto se informa tal cual:
# si Meta agrega uno nuevo, mostrarlo es mejor que traducirlo mal.
FLOW_PUBLICADO = "PUBLISHED"
PLANTILLA_APROBADA = "APPROVED"


class SinConfigurar(RuntimeError):
    """No hay credenciales de Meta. No es un error del usuario."""


def config(entorno=None):
    entorno = os.environ if entorno is None else entorno
    return {
        "token": entorno.get("WHATSAPP_TOKEN", "").strip(),
        "phone_number_id": entorno.get("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
        "waba_id": entorno.get("WHATSAPP_WABA_ID", "").strip(),
    }


def configurado(entorno=None):
    datos = config(entorno)
    return bool(datos["token"] and datos["phone_number_id"])


def diagnostico(entorno=None):
    datos = config(entorno)
    faltan = [c for c in ("token", "phone_number_id", "waba_id") if not datos[c]]
    return {
        "configurado": not faltan,
        "faltan": faltan,
        "avisos": [] if not faltan else [
            "Sin credenciales de WhatsApp: una encuesta se puede configurar "
            "como Flow, pero el envío queda deshabilitado. Las credenciales "
            "van en Secret Manager, nunca en el repositorio."
        ],
    }


# ── Llamadas a la API ────────────────────────────────────────────────

def _pedir(url, datos=None, token=None, metodo=None):
    pedido = urllib.request.Request(
        url,
        data=json.dumps(datos).encode() if datos is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method=metodo or ("POST" if datos is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(pedido, timeout=20) as respuesta:
            return json.loads(respuesta.read().decode())
    except urllib.error.HTTPError as error:
        cuerpo = error.read().decode(errors="replace")
        try:
            detalle = json.loads(cuerpo).get("error", {})
        except ValueError:
            detalle = {"message": cuerpo[:400]}
        raise Conflicto(
            detalle.get("message") or f"Meta respondió {error.code}.",
            {"status": error.code, "meta": detalle})


def _boton_de_flow(componentes):
    """El botón de Flow de una plantilla, si lo tiene.

    Meta devuelve los botones dentro del componente `BUTTONS`. Una plantilla
    puede tener varios botones y solo uno de Flow; y puede no tener ninguno,
    en cuyo caso no sirve para convocar por Flow por más aprobada que esté.
    """
    for componente in componentes or []:
        if (componente.get("type") or "").upper() != "BUTTONS":
            continue
        for indice, boton in enumerate(componente.get("buttons") or []):
            if (boton.get("type") or "").upper() == "FLOW":
                return {"indice": indice,
                        "flow_id": str(boton.get("flow_id") or "") or None,
                        "texto": boton.get("text")}
    return None


def _boton_de_url(componentes):
    """El botón de URL de una plantilla, si lo tiene (R-CF.4).

    Meta fija la base de la URL al aprobar la plantilla y solo deja variar el
    final (`…?pid={{1}}`). Ese final es lo que recibe el `id_persona`. Una URL
    sin `{{1}}` es fija: no puede llevar a nadie a su propia encuesta.
    """
    for componente in componentes or []:
        if (componente.get("type") or "").upper() != "BUTTONS":
            continue
        for indice, boton in enumerate(componente.get("buttons") or []):
            if (boton.get("type") or "").upper() == "URL":
                url = boton.get("url") or ""
                return {"indice": indice, "url": url, "texto": boton.get("text"),
                        "dinamico": "{{1}}" in url,
                        "base": url.split("{{")[0] if "{{1}}" in url else url}
    return None


def parametros_del_cuerpo(texto):
    """Cuántos parámetros (`{{1}}`, `{{2}}`…) tiene el texto de una plantilla."""
    import re

    return len({m for m in re.findall(r"\{\{(\d+)\}\}", texto or "")})


def _encabezado_de(componentes):
    """R-CTL.2 — el encabezado de una plantilla, si lo tiene:
    `{formato, variable, texto}`. `formato` es el de Meta (`TEXT`, `IMAGE`,
    `VIDEO`, `DOCUMENT`, `LOCATION`); `variable`, si un encabezado de texto
    lleva un `{{n}}`."""
    for componente in componentes or []:
        if (componente.get("type") or "").upper() == "HEADER":
            formato = (componente.get("format") or "TEXT").upper()
            texto = componente.get("text")
            return {"formato": formato, "texto": texto,
                    "variable": formato == "TEXT" and "{{" in (texto or "")}
    return None


def _cuerpo_de(componentes):
    """El texto del cuerpo, para que la lista se pueda leer sin ir a Meta."""
    for componente in componentes or []:
        if (componente.get("type") or "").upper() == "BODY":
            return componente.get("text")
    return None


def listar_plantillas(entorno=None, pedir=None, solo_con_flow=True,
                      solo_aprobadas=True):
    """Las plantillas de la cuenta, con su idioma y su Flow ya resueltos.

    Es lo que alimenta el selector de la pantalla de encuestas. Devuelve
    `{"plantillas": [...], "avisos": [...]}` y **no levanta** cuando no hay
    credenciales: la pantalla tiene que poder decir «no hay nada configurado»
    en vez de romperse.

    Las que no tienen botón de Flow se filtran por defecto: están aprobadas y
    no sirven para convocar por Flow, y ofrecerlas sería ofrecer un callejón
    sin salida. Se pueden pedir igual con `solo_con_flow=False`, que es lo que
    hace el diagnóstico para poder explicar por qué la lista está vacía.
    """
    datos = config(entorno)
    pedir = pedir or _pedir
    if not datos["token"] or not datos["waba_id"]:
        faltan = [c for c in ("token", "waba_id") if not datos[c]]
        return {
            "plantillas": [], "configurado": False, "faltan": faltan,
            "avisos": ["Sin `WHATSAPP_TOKEN` y `WHATSAPP_WABA_ID` no se pueden "
                       "listar las plantillas de la cuenta."],
        }

    campos = "name,language,status,category,components,quality_score"
    url = f"{API}/{datos['waba_id']}/message_templates?fields={campos}&limit=100"
    crudas, avisos = [], []
    # Meta pagina. Sin seguir el cursor, una cuenta con muchas plantillas
    # mostraría solo las primeras cien y las que faltan parecerían no existir.
    for _ in range(10):
        try:
            respuesta = pedir(url, token=datos["token"])
        except Conflicto as error:
            avisos.append(f"No se pudo leer el listado de plantillas: "
                          f"{error.mensaje}")
            break
        crudas.extend(respuesta.get("data") or [])
        url = ((respuesta.get("paging") or {}).get("next")) or None
        if not url:
            break

    plantillas, sin_flow = [], 0
    for cruda in crudas:
        estado = (cruda.get("status") or "").upper()
        if solo_aprobadas and estado != PLANTILLA_APROBADA:
            continue
        boton = _boton_de_flow(cruda.get("components"))
        if not boton or not boton["flow_id"]:
            sin_flow += 1
            if solo_con_flow:
                continue
        boton_url = _boton_de_url(cruda.get("components"))
        cuerpo = _cuerpo_de(cruda.get("components"))
        plantillas.append({
            "nombre": cruda.get("name"),
            "idioma": cruda.get("language"),
            "estado": estado,
            "categoria": cruda.get("category"),
            "flow_id": boton["flow_id"] if boton else None,
            # D87 — la posición del botón de Flow, que Meta pide al enviar.
            "indice_flow": boton["indice"] if boton else None,
            "texto_boton": boton["texto"] if boton else None,
            "cuerpo": cuerpo,
            "calidad": (cruda.get("quality_score") or {}).get("score"),
            # R-CF.4 — para los mensajes de una encuesta: el botón de URL
            # (cuya base fija Meta) y cuántos parámetros lleva el texto.
            "boton_url": boton_url,
            "parametros_cuerpo": parametros_del_cuerpo(cuerpo),
            # R-CTL.2 — el sistema solo completa el cuerpo y el botón: un
            # encabezado con variable o con archivo haría fallar cada envío.
            "encabezado": _encabezado_de(cruda.get("components")),
        })

    # El mismo nombre puede existir en varios idiomas, y son plantillas
    # distintas: se ordenan juntas para que se vean como lo que son.
    plantillas.sort(key=lambda p: ((p["nombre"] or ""), (p["idioma"] or "")))

    if not plantillas and sin_flow:
        avisos.append(
            f"La cuenta tiene {sin_flow} plantilla(s) aprobada(s), pero "
            f"ninguna con un botón de Flow. Una plantilla sin ese botón no "
            f"sirve para convocar por Flow por más aprobada que esté.")
    elif not plantillas and not avisos:
        avisos.append("La cuenta no tiene plantillas aprobadas todavía. La "
                      "revisión de Meta demora.")

    return {"plantillas": plantillas, "configurado": True, "faltan": [],
            "sin_flow": sin_flow, "avisos": avisos}


def candidatas(nombre, idioma=None, entorno=None, pedir=None):
    """Las plantillas que coinciden con ese nombre, y con ese idioma si se da.

    La clave de una plantilla en Meta es el par `(nombre, idioma)`: el mismo
    nombre puede existir en varios idiomas y cada uno se aprueba por separado.
    Por eso puede haber más de una candidata, y **no se elige una sola**:
    mandar en el idioma equivocado es peor que no mandar.
    """
    todas = listar_plantillas(entorno=entorno, pedir=pedir,
                              solo_con_flow=False, solo_aprobadas=False)
    return [p for p in todas["plantillas"]
            if p["nombre"] == nombre and (idioma is None
                                          or p["idioma"] == idioma)]


def estado_del_flow(flow_id, entorno=None, pedir=None):
    """`(publicado, motivo)` del Flow que una plantilla lleva en su botón.

    Una plantilla aprobada con un Flow en borrador se envía y no abre nada:
    por eso, para un mensaje con botón de Flow, «Flow publicado» es parte de
    guardarlo y de enviarlo (D87), igual que «plantilla aprobada».
    """
    datos = config(entorno)
    if not flow_id:
        return False, ("La plantilla no dice qué Flow lleva en su botón: no se "
                       "puede comprobar que esté publicado.")
    if not datos["token"]:
        return False, "No hay credenciales de WhatsApp configuradas en el sistema."
    try:
        flow = (pedir or _pedir)(f"{API}/{flow_id}?fields=id,name,status",
                                 token=datos["token"])
    except Conflicto as error:
        return False, f"No se pudo leer el Flow: {error.mensaje}"
    if (flow.get("status") or "").upper() != FLOW_PUBLICADO:
        return False, (f"El Flow «{flow.get('name') or flow_id}» está en "
                       f"«{flow.get('status')}» y tiene que estar publicado para "
                       f"poder enviarlo.")
    return True, None


def enviar_flow(destino, plantilla, idioma, flow_token, textos=None, indice=0,
                entorno=None, pedir=None):
    """Manda la plantilla con el Flow a un número. Devuelve el id del mensaje.

    `flow_token` es el `id_persona`: es lo que vuelve con las respuestas y lo
    que permite que la ingesta mapee directo.

    El `flow_id` no entra acá: el envío referencia la plantilla, y el Flow
    viene adentro de su botón. `textos` va a los `{{n}}` del cuerpo, como en
    cualquier mensaje (D87), e `indice` es la posición del botón de Flow.
    """
    datos = config(entorno)
    if not datos["token"] or not datos["phone_number_id"]:
        raise SinConfigurar("Sin credenciales de WhatsApp.")
    if not flow_token:
        raise DatosInvalidos(
            "Falta el `flow_token`. Sin él, las respuestas vuelven sin forma "
            "de saber de quién son.")

    componentes = []
    if textos:
        componentes.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(t)} for t in textos]})
    componentes.append({
        "type": "button",
        "sub_type": "flow",
        "index": str(indice or 0),
        "parameters": [{
            "type": "action",
            "action": {"flow_token": str(flow_token)},
        }],
    })
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "template",
        "template": {
            "name": plantilla,
            "language": {"code": idioma or "es"},
            "components": componentes,
        },
    }
    respuesta = (pedir or _pedir)(
        f"{API}/{datos['phone_number_id']}/messages", cuerpo,
        token=datos["token"])
    mensajes = respuesta.get("messages") or [{}]
    return {"message_id": mensajes[0].get("id"), "respuesta": respuesta}


# ── R-CF.4 / R-CF.5 · Mensajes de una encuesta ───────────────────────

def enviar_plantilla(destino, plantilla, idioma, textos=None, id_persona=None,
                     boton_url=None, flow_id=None, entorno=None, pedir=None):
    """Manda una plantilla aprobada a un número. Devuelve el id del mensaje.

    `textos` va a los `{{1}}`, `{{2}}`… del cuerpo, en orden. Si la plantilla
    tiene un botón de URL dinámico, su parámetro es el `id_persona`: es lo que
    Alchemer recibe y lo que vuelve como columna en el SAV (R-CF.3). Si tiene
    un botón de Flow, el `id_persona` va como `flow_token`, igual que R4.5.
    """
    datos = config(entorno)
    if not datos["token"] or not datos["phone_number_id"]:
        raise SinConfigurar("Sin credenciales de WhatsApp.")
    componentes = []
    if textos:
        componentes.append({
            "type": "body",
            "parameters": [{"type": "text", "text": str(t)} for t in textos]})
    if boton_url and boton_url.get("dinamico"):
        componentes.append({
            "type": "button", "sub_type": "url",
            "index": str(boton_url.get("indice", 0)),
            "parameters": [{"type": "text", "text": str(id_persona)}]})
    elif flow_id:
        componentes.append({
            "type": "button", "sub_type": "flow", "index": "0",
            "parameters": [{"type": "action",
                            "action": {"flow_token": str(id_persona)}}]})
    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "template",
        "template": {"name": plantilla, "language": {"code": idioma or "es"},
                     **({"components": componentes} if componentes else {})},
    }
    respuesta = (pedir or _pedir)(
        f"{API}/{datos['phone_number_id']}/messages", cuerpo, token=datos["token"])
    mensajes = respuesta.get("messages") or [{}]
    return {"message_id": mensajes[0].get("id"), "respuesta": respuesta}

