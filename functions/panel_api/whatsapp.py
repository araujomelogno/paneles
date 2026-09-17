"""R4.5 — Envío de encuestas por WhatsApp Flow.

**El sistema solo envía.** No recibe respuestas, no procesa webhooks, no crea
ni edita Flows ni plantillas. El Flow y la plantilla se arman en Meta y acá se
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


def validar_configuracion(flow_id, plantilla, idioma=None, entorno=None,
                          pedir=None):
    """¿Se puede convocar por WhatsApp con esta configuración?

    Devuelve `{"puede_enviar": bool, "motivos": [...], ...}`. **No levanta**
    cuando algo está mal: el motivo es información que la pantalla tiene que
    mostrar, no un error de programa.
    """
    datos = config(entorno)
    pedir = pedir or _pedir
    salida = {"flow_id": flow_id, "plantilla": plantilla, "idioma": idioma,
              "puede_enviar": False, "motivos": []}

    if not flow_id or not plantilla:
        salida["motivos"].append(
            "Falta el Flow o la plantilla: los dos se configuran en la encuesta.")
        return salida
    if not datos["token"] or not datos["phone_number_id"]:
        salida["motivos"].append(
            "No hay credenciales de WhatsApp configuradas en el sistema.")
        return salida

    try:
        flow = pedir(f"{API}/{flow_id}?fields=id,name,status",
                     token=datos["token"])
        salida["flow"] = {"nombre": flow.get("name"), "estado": flow.get("status")}
        if (flow.get("status") or "").upper() != FLOW_PUBLICADO:
            salida["motivos"].append(
                f"El Flow está en «{flow.get('status')}» y tiene que estar "
                f"publicado para poder enviarlo.")
    except Conflicto as error:
        salida["motivos"].append(f"No se pudo leer el Flow: {error.mensaje}")

    if datos["waba_id"]:
        try:
            listado = pedir(
                f"{API}/{datos['waba_id']}/message_templates"
                f"?name={urllib.parse.quote(plantilla)}&limit=20",
                token=datos["token"])
            candidatas = [
                p for p in listado.get("data", [])
                if p.get("name") == plantilla
                and (not idioma or p.get("language") == idioma)
            ]
            if not candidatas:
                salida["motivos"].append(
                    f"No existe una plantilla «{plantilla}»"
                    + (f" en «{idioma}»" if idioma else "") + ".")
            else:
                estado = (candidatas[0].get("status") or "").upper()
                salida["plantilla_estado"] = estado
                if estado != PLANTILLA_APROBADA:
                    salida["motivos"].append(
                        f"La plantilla está en «{estado}». Meta tiene que "
                        f"aprobarla antes de que se pueda enviar.")
        except Conflicto as error:
            salida["motivos"].append(
                f"No se pudo leer la plantilla: {error.mensaje}")
    else:
        salida["motivos"].append(
            "Sin `WHATSAPP_WABA_ID` no se puede comprobar que la plantilla "
            "esté aprobada.")

    salida["puede_enviar"] = not salida["motivos"]
    return salida


def enviar_flow(destino, flow_id, plantilla, idioma, flow_token,
                entorno=None, pedir=None):
    """Manda la plantilla con el Flow a un número. Devuelve el id del mensaje.

    `flow_token` es el `id_persona`: es lo que vuelve con las respuestas y lo
    que permite que la ingesta mapee directo.
    """
    datos = config(entorno)
    if not datos["token"] or not datos["phone_number_id"]:
        raise SinConfigurar("Sin credenciales de WhatsApp.")
    if not flow_token:
        raise DatosInvalidos(
            "Falta el `flow_token`. Sin él, las respuestas vuelven sin forma "
            "de saber de quién son.")

    cuerpo = {
        "messaging_product": "whatsapp",
        "to": destino,
        "type": "template",
        "template": {
            "name": plantilla,
            "language": {"code": idioma or "es"},
            "components": [{
                "type": "button",
                "sub_type": "flow",
                "index": "0",
                "parameters": [{
                    "type": "action",
                    "action": {"flow_token": str(flow_token)},
                }],
            }],
        },
    }
    respuesta = (pedir or _pedir)(
        f"{API}/{datos['phone_number_id']}/messages", cuerpo,
        token=datos["token"])
    mensajes = respuesta.get("messages") or [{}]
    return {"message_id": mensajes[0].get("id"), "respuesta": respuesta}
