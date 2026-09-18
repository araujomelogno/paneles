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

**Se elige la plantilla, y nada más.** Una plantilla de Meta ya trae adentro
su idioma y, en su botón de Flow, el `flow_id`. Pedir los tres por separado
era pedir tres veces el mismo dato y dejar que se contradigan: nada impedía
configurar la plantilla `X` en `es` con el Flow de la plantilla `Y`, y eso
recién se descubría al validar, o peor, al enviar. `listar_plantillas()`
devuelve las aprobadas que tienen un botón de Flow, con su idioma y su
`flow_id` ya resueltos; el resto del sistema elige una de esa lista.
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
        plantillas.append({
            "nombre": cruda.get("name"),
            "idioma": cruda.get("language"),
            "estado": estado,
            "categoria": cruda.get("category"),
            "flow_id": boton["flow_id"] if boton else None,
            "texto_boton": boton["texto"] if boton else None,
            "cuerpo": _cuerpo_de(cruda.get("components")),
            "calidad": (cruda.get("quality_score") or {}).get("score"),
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


def buscar_plantilla(nombre, idioma=None, entorno=None, pedir=None):
    """La plantilla, si la coincidencia es **única**; `None` si no.

    Sin `idioma`, una sola candidata se toma tal cual —es el caso normal, una
    plantilla en un solo idioma— y varias devuelven `None`: elegir por el
    sistema sería elegir en qué idioma le habla a la gente.
    """
    encontradas = candidatas(nombre, idioma, entorno=entorno, pedir=pedir)
    return encontradas[0] if len(encontradas) == 1 else None


def validar_configuracion(plantilla, idioma=None, entorno=None, pedir=None):
    """¿Se puede convocar por WhatsApp con esta plantilla?

    Devuelve `{"puede_enviar": bool, "motivos": [...], ...}`. **No levanta**
    cuando algo está mal: el motivo es información que la pantalla tiene que
    mostrar, no un error de programa.

    La clave de una plantilla en Meta es el par `(nombre, idioma)`: el mismo
    nombre puede existir en varios idiomas y cada uno se aprueba por separado.
    De la plantilla salen el idioma y el `flow_id`; no se piden aparte.
    """
    datos = config(entorno)
    pedir = pedir or _pedir
    salida = {"plantilla": plantilla, "idioma": idioma, "flow_id": None,
              "puede_enviar": False, "motivos": []}

    if not plantilla:
        salida["motivos"].append(
            "Falta elegir la plantilla de mensaje en la encuesta.")
        return salida
    if not datos["token"] or not datos["phone_number_id"]:
        salida["motivos"].append(
            "No hay credenciales de WhatsApp configuradas en el sistema.")
        return salida
    if not datos["waba_id"]:
        salida["motivos"].append(
            "Sin `WHATSAPP_WABA_ID` no se puede comprobar que la plantilla "
            "esté aprobada ni saber qué Flow lleva adentro.")
        return salida

    encontradas = candidatas(plantilla, idioma, entorno=entorno, pedir=pedir)
    if len(encontradas) > 1:
        salida["motivos"].append(
            f"Hay {len(encontradas)} plantillas «{plantilla}», una por idioma "
            f"({', '.join(sorted(p['idioma'] or '—' for p in encontradas))}). "
            f"Hay que elegir cuál: son plantillas distintas y se aprueban por "
            f"separado.")
        salida["idiomas_disponibles"] = sorted(
            p["idioma"] for p in encontradas if p["idioma"])
        return salida
    if not encontradas:
        salida["motivos"].append(
            f"No existe una plantilla «{plantilla}»"
            + (f" en «{idioma}»" if idioma else "")
            + " en la cuenta. Puede haberse borrado o renombrado en Meta.")
        return salida
    elegida = encontradas[0]
    salida["idioma"] = elegida["idioma"]

    salida["plantilla_estado"] = elegida["estado"]
    salida["categoria"] = elegida["categoria"]
    salida["flow_id"] = elegida["flow_id"]
    salida["texto_boton"] = elegida["texto_boton"]

    if elegida["estado"] != PLANTILLA_APROBADA:
        salida["motivos"].append(
            f"La plantilla está en «{elegida['estado']}». Meta tiene que "
            f"aprobarla antes de que se pueda enviar.")
    if not elegida["flow_id"]:
        salida["motivos"].append(
            "La plantilla no tiene un botón de Flow: sirve para mandar un "
            "mensaje, no para convocar a un cuestionario.")
        salida["puede_enviar"] = False
        return salida

    # El Flow que la plantilla lleva adentro tiene que estar publicado. Una
    # plantilla aprobada con un Flow en borrador se envía y no abre nada.
    try:
        flow = pedir(f"{API}/{elegida['flow_id']}?fields=id,name,status",
                     token=datos["token"])
        salida["flow"] = {"nombre": flow.get("name"),
                          "estado": flow.get("status")}
        if (flow.get("status") or "").upper() != FLOW_PUBLICADO:
            salida["motivos"].append(
                f"El Flow «{flow.get('name')}» está en «{flow.get('status')}» "
                f"y tiene que estar publicado para poder enviarlo.")
    except Conflicto as error:
        salida["motivos"].append(f"No se pudo leer el Flow: {error.mensaje}")

    salida["puede_enviar"] = not salida["motivos"]
    return salida


def enviar_flow(destino, plantilla, idioma, flow_token,
                entorno=None, pedir=None):
    """Manda la plantilla con el Flow a un número. Devuelve el id del mensaje.

    `flow_token` es el `id_persona`: es lo que vuelve con las respuestas y lo
    que permite que la ingesta mapee directo.

    El `flow_id` no entra acá: el envío referencia la plantilla, y el Flow
    viene adentro de su botón. Es la razón de fondo por la que configurarlo
    aparte nunca tuvo sentido.
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
