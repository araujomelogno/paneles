"""R4.3 — El desafío anti-automatización de la landing.

Verificar el contacto (`verificacion_contacto`) frena a quien inscribe datos
ajenos, pero no a un bot que tiene sus propios números y correos. Para eso va
un desafío: la landing incluye un token que el backend valida contra el
proveedor antes de emitir ningún código.

Va **antes de pedir el código**, no antes de inscribir, y esa es la decisión
que importa: el envío de códigos es lo que cuesta plata y lo que puede
molestar a terceros, así que es lo que hay que proteger. Dejar el desafío para
el final protegería la tabla y no el bolsillo ni al vecino.

Detrás de una interfaz, igual que todo lo que llama a un servicio ajeno en
este sistema. Sin proveedor configurado **no bloquea**, y eso se informa en el
diagnóstico y está en el checklist de despliegue: una landing sin desafío es
una decisión que alguien tiene que tomar a sabiendas, no un olvido silencioso.
"""

import json
import os
import urllib.parse
import urllib.request

from .errores import Conflicto, DatosInvalidos

# Los dos que se usan en la práctica. El contrato es el mismo: se manda el
# token del cliente y el servicio responde si es humano.
VERIFICADORES = {
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    "recaptcha": "https://www.google.com/recaptcha/api/siteverify",
}


def configurado(entorno=None):
    entorno = os.environ if entorno is None else entorno
    nombre = (entorno.get("DESAFIO_PROVEEDOR") or "").strip().lower()
    return nombre in VERIFICADORES and bool(entorno.get("DESAFIO_SECRETO"))


def validar(token, origen=None, entorno=None, pedir=None):
    """Valida el token del desafío. Levanta `Conflicto` si no pasa.

    `pedir` existe para las pruebas: inyecta la llamada al servicio en vez de
    salir a la red.
    """
    entorno = os.environ if entorno is None else entorno
    nombre = (entorno.get("DESAFIO_PROVEEDOR") or "").strip().lower()
    if nombre in ("", "ninguno"):
        return {"validado": False, "proveedor": "ninguno",
                "aviso": "Sin proveedor de desafío: no se verificó que el "
                         "envío no sea automatizado."}
    if nombre not in VERIFICADORES:
        raise DatosInvalidos(
            f"Proveedor de desafío desconocido: {nombre!r}.",
            {"proveedores": sorted(VERIFICADORES) + ["ninguno"]})

    secreto = entorno.get("DESAFIO_SECRETO", "")
    if not secreto:
        raise DatosInvalidos(
            f"Falta `DESAFIO_SECRETO` para el proveedor {nombre!r}. Sin él, "
            f"el desafío no se puede validar y la landing quedaría abierta.")
    if not token:
        raise Conflicto(
            "Falta resolver el desafío de seguridad del formulario.",
            {"motivo": "sin_token"})

    respuesta = (pedir or _pedir)(
        VERIFICADORES[nombre], {"secret": secreto, "response": token,
                                "remoteip": origen or ""})
    if not respuesta.get("success"):
        raise Conflicto(
            "El desafío de seguridad no se pudo validar. Recargá el "
            "formulario y volvé a intentar.",
            {"motivo": "desafio_rechazado",
             "detalle": respuesta.get("error-codes")})
    return {"validado": True, "proveedor": nombre}


def _pedir(url, datos):
    cuerpo = urllib.parse.urlencode(datos).encode()
    pedido = urllib.request.Request(
        url, data=cuerpo,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(pedido, timeout=10) as respuesta:
        return json.loads(respuesta.read().decode())


def diagnostico(entorno=None):
    entorno = os.environ if entorno is None else entorno
    nombre = (entorno.get("DESAFIO_PROVEEDOR") or "ninguno").strip().lower()
    activo = configurado(entorno)
    return {
        "proveedor": nombre,
        "activo": activo,
        "avisos": [] if activo else [
            "Sin desafío configurado: la landing no distingue un envío "
            "automatizado de una persona. Configurar `DESAFIO_PROVEEDOR` y "
            "`DESAFIO_SECRETO` antes de anunciarla."
        ],
    }
