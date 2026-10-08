"""R6.1.a — La credencial del panelista, detrás de una interfaz.

Hasta R6.1.a el portal no tenía credenciales: cada visita empezaba con un
enlace al correo. Ahora hay una contraseña por panelista, y eso trae una
pregunta que conviene contestar de entrada: **dónde vive.**

Vive en **Firebase Auth**, y este módulo es lo único que la toca. Acá no se
hashea nada, no se guarda nada y no se compara nada: se le pide a Auth que
lo haga. La razón no es pereza —es que un esquema de contraseñas propio es
exactamente el tipo de cosa que no hay que escribir cuando el proyecto ya
tiene un proveedor de identidad, y escribirlo mal no se nota hasta que se
filtra—.

── Por qué el login pasa por el backend y no por el SDK del navegador ──

Lo natural en Firebase sería que la página llame a `signInWithEmailAndPassword`
y el servidor solo verifique el token. No alcanza, por tres cosas que R6.1.b
pide y que desde el navegador no se pueden hacer valer:

1. **El límite de intentos fallidos por correo y por origen.** Un límite que
   se aplica en el cliente no es un límite.
2. **«Un panelista dado de baja no entra.»** Con login en el cliente la
   persona obtiene un token válido y recién se la rechaza adentro; acá se la
   rechaza en la puerta.
3. **El mensaje genérico.** Firebase distingue «ese correo no existe» de
   «contraseña incorrecta» salvo que esté activada la protección contra
   enumeración, que es una casilla de la consola y no una línea del repo. La
   constante del módulo no depende de una casilla.

El costo, dicho sin vueltas: **la contraseña pasa por nuestra función.** En
memoria, nunca a un log ni a una tabla —acá no hay un solo `print` ni un solo
`insert` con la clave—, pero pasa. Es el precio de los tres puntos de arriba,
y se paga una vez: por eso `fijar_clave()` también entra por acá en vez de
mandar a la persona a la pantalla de Firebase. Un sistema con dos caminos
para las contraseñas tiene dos conjuntos de reglas, y el segundo envejece.

── La interfaz ──

Igual que el `Padron` de R2.12, y por el mismo motivo: las reglas de quién
entra, con qué límite y qué pasa tras una baja se tienen que poder probar sin
desplegar nada. `CredencialesEnMemoria` es lo que hace que las pruebas del
Definition of Done corran contra Postgres de verdad y Firebase de mentira.
"""

import json
import os
import re
import urllib.error
import urllib.request

from .errores import ComprobacionNoDisponible, DatosInvalidos

# Mínimo de Firebase Auth. La política la fija Auth y es la autoridad: esto
# es su piso repetido acá para que el doble en memoria rechace lo mismo que
# rechazaría Firebase, y para dar un mensaje en castellano en vez de dejar
# salir el error del SDK en inglés.
LARGO_MINIMO = 6


def validar_clave(clave):
    clave = str(clave or "")
    if len(clave) < LARGO_MINIMO:
        raise DatosInvalidos(
            f"La contraseña tiene que tener al menos {LARGO_MINIMO} "
            f"caracteres.", {"largo_minimo": LARGO_MINIMO})
    return clave


class Credenciales:
    """Lo que el portal necesita de un proveedor de identidad. Nada más."""

    def buscar(self, email):
        """`{uid, email, deshabilitada}` o None."""
        raise NotImplementedError

    def crear(self, email, clave):
        """Crea la cuenta y devuelve `{uid}`."""
        raise NotImplementedError

    def verificar_clave(self, email, clave):
        """`{uid}` si la credencial entra; **None** si no.

        None y no una excepción a propósito: quien llama tiene que poder
        tratar «no existe la cuenta» y «la contraseña no es ésa» con la misma
        respuesta, y una excepción distinta por caso invita a distinguirlos.

        La única excepción es `ComprobacionNoDisponible` (PEDIDO R3): **no se
        pudo comprobar** —el proveedor está caído, no contesta o está mal
        configurado—. No es un veredicto sobre la credencial y no se cuenta
        como intento fallido.
        """
        raise NotImplementedError

    def fijar_clave(self, uid, clave):
        """Reemplaza la contraseña. La anterior no hace falta: quien llama ya
        probó identidad, con el token del enlace o con la clave actual."""
        raise NotImplementedError

    def cambiar_email(self, uid, email):
        """R6.1.f — el correo nuevo pasa a ser el usuario, misma clave."""
        raise NotImplementedError

    def token_de_sesion(self, uid):
        """Un token custom para que el navegador abra sesión en Firebase."""
        raise NotImplementedError

    def revocar_sesiones(self, uid):
        """Invalida los refresh tokens: las demás sesiones se caen."""
        raise NotImplementedError

    def deshabilitar(self, uid):
        """La cuenta deja de poder autenticarse. Para la baja."""
        raise NotImplementedError

    def habilitar(self, uid):
        """La vuelve a encender.

        Existe por un caso concreto y no por simetría: alguien que se dio de
        baja y después se inscribió de nuevo. La persona de ahora es otra
        fila de la bóveda, pero el correo es el mismo y por lo tanto la
        cuenta de Auth también —Auth indexa por correo—, y quedó apagada por
        R6.1.e. Sin esto, volver al panel sería imposible con la misma
        dirección.
        """
        raise NotImplementedError


# PEDIDO R1 — de dónde sale la *web API key*. **No es un secreto**: está en
# la configuración del frontend y la recibe cada navegador; Google la trata
# como un identificador de proyecto. Va como variable de entorno común en
# `functions/.env`, visible en un `gcloud run services describe`.
#
# Y no se llama `FIREBASE_WEB_API_KEY`, que es como se llamó hasta acá:
# `firebase-tools` rechaza cualquier variable —de `.env` o de Secret
# Manager— cuyo nombre empiece con `FIREBASE_` («starts with a reserved
# prefix»). Ese nombre hizo que el valor real no se pudiera cargar nunca y
# que en el runtime quedara el placeholder `AIza...`: nadie entraba al
# portal y el síntoma era «contraseña incorrecta».
VARIABLE_API_KEY = "WEB_API_KEY"

# Cómo es una web API key de Google: `AIza` y 35 caracteres más. Sirve para
# reconocer un placeholder (`AIza...`) antes de mandarlo, en vez de dejar
# que Identity Toolkit conteste un 400 que se confunde con una contraseña.
_FORMA_API_KEY = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")

# Lo que Identity Toolkit contesta, en un 400, cuando **la credencial** no
# entró. Es lo único que se traduce en «no entró» con certeza. Cualquier otro
# 400 conocido como de configuración (la key) es «no se pudo comprobar».
MOTIVOS_DE_CREDENCIAL = (
    "INVALID_LOGIN_CREDENTIALS", "INVALID_PASSWORD", "EMAIL_NOT_FOUND",
    "USER_DISABLED", "INVALID_EMAIL", "MISSING_PASSWORD",
)
MOTIVOS_DE_CONFIGURACION = ("API_KEY_INVALID", "API KEY NOT VALID",
                            "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED")

# Cuánto del cuerpo de error va al log. Identity Toolkit contesta con un JSON
# corto; el tope es para que una página de error HTML no inunde el log.
MAX_CUERPO_EN_LOG = 2000


def _registrar(evento, **datos):
    """Una línea de log por fallo de Identity Toolkit, con el motivo exacto.

    PEDIDO R2 — **al usuario, un solo mensaje; en el log, el motivo exacto.**
    Hasta acá el código aplicaba la primera regla a las dos superficies, y un
    400 por «API key not valid» fue indistinguible de una contraseña mal
    escrita durante una hora de diagnóstico. El correo va porque ya está en
    los registros de intentos; la contraseña y la key, **nunca**: quien llama
    pasa el cuerpo ya limpio por `_sin_key`.
    """
    partes = " ".join(f"{k}={v}" for k, v in datos.items())
    print(f"[credenciales] {evento} {partes}")


def _sin_key(texto, key):
    texto = str(texto or "")
    if key:
        texto = texto.replace(key, "<api-key>")
    return texto[:MAX_CUERPO_EN_LOG]


def _motivo_de_error(cuerpo):
    """El `error.message` de Identity Toolkit, más los `reason` de detalle."""
    try:
        error = (json.loads(cuerpo) or {}).get("error") or {}
    except (ValueError, AttributeError):
        return ""
    partes = [str(error.get("message") or "")]
    for detalle in error.get("details") or []:
        if isinstance(detalle, dict) and detalle.get("reason"):
            partes.append(str(detalle["reason"]))
    return " ".join(partes).upper()


def es_cuenta_inexistente(auth_fb, error):
    """¿Es el «no existe ese usuario» del Admin SDK y no otra falla?"""
    clase = getattr(auth_fb, "UserNotFoundError", None)
    if isinstance(clase, type) and isinstance(error, clase):
        return True
    return type(error).__name__ == "UserNotFoundError"


class CredencialesFirebase(Credenciales):
    """Firebase Auth de verdad: Admin SDK para todo salvo comprobar la clave.

    Comprobar una contraseña no está en el Admin SDK —a propósito: el Admin
    SDK administra cuentas, no inicia sesiones—, así que esa única operación
    va por la API REST de Identity Toolkit, la misma que usa el SDK del
    navegador. Necesita la *web API key* del proyecto, que es pública por
    diseño y se lee de la variable de entorno `WEB_API_KEY` (PEDIDO R1).
    """

    URL_LOGIN = ("https://identitytoolkit.googleapis.com/v1/"
                 "accounts:signInWithPassword")

    # Cuánto se espera a Identity Toolkit. Si no contesta en este tiempo es
    # «no se pudo comprobar», no «no entró».
    TIEMPO_S = 10

    def __init__(self, auth_fb=None, api_key=None, abrir=None):
        if auth_fb is None:
            from firebase_admin import auth as auth_modulo

            auth_fb = auth_modulo
        self.auth = auth_fb
        self._api_key = api_key
        # `urlopen`, inyectable: las pruebas simulan las respuestas de
        # Identity Toolkit sin red.
        self._abrir = abrir or urllib.request.urlopen

    @property
    def api_key(self):
        clave = (self._api_key or os.environ.get("WEB_API_KEY") or "").strip()
        if not clave:
            mensaje = (
                f"Falta `{VARIABLE_API_KEY}`: sin ella no se puede comprobar "
                f"ninguna contraseña y nadie entra al portal. Va como variable "
                f"de entorno en functions/.env (antes se llamaba "
                f"FIREBASE_WEB_API_KEY, un nombre que Firebase no deja cargar).")
            _registrar("configuracion", problema="sin_api_key")
            raise ComprobacionNoDisponible(mensaje, {"motivo": "sin_api_key"})
        if not _FORMA_API_KEY.match(clave):
            # Un placeholder (`AIza...`) o un valor cortado. Se frena acá: si
            # se mandara, Identity Toolkit contestaría un 400 y el panelista
            # leería «contraseña incorrecta». Se loguea el largo, no el valor.
            _registrar("configuracion", problema="api_key_con_forma_invalida",
                       largo=len(clave))
            raise ComprobacionNoDisponible(
                f"`{VARIABLE_API_KEY}` no tiene la forma de una web API key "
                f"(«AIza» y 35 caracteres): parece un placeholder.",
                {"motivo": "api_key_invalida"})
        return clave

    def buscar(self, email):
        try:
            usuario = self.auth.get_user_by_email(email)
        except Exception as error:  # noqa: BLE001
            # PEDIDO A2 — «no existe la cuenta» es `UserNotFoundError` y nada
            # más. Antes cualquier excepción era None, así que Firebase caído
            # se leía como «no hay cuenta» y el portal intentaba crearla.
            if es_cuenta_inexistente(self.auth, error):
                return None
            _registrar("buscar_cuenta_fallo", tipo=type(error).__name__)
            raise
        return {
            "uid": usuario.uid,
            "email": usuario.email,
            "deshabilitada": bool(getattr(usuario, "disabled", False)),
        }

    def crear(self, email, clave):
        usuario = self.auth.create_user(
            email=email, password=validar_clave(clave), email_verified=False)
        return {"uid": usuario.uid}

    def verificar_clave(self, email, clave):
        key = self.api_key
        cuerpo = json.dumps({
            "email": email, "password": str(clave or ""),
            # No hace falta el par de tokens de Identity Toolkit: la sesión
            # del navegador se abre después con un token custom del Admin
            # SDK. Pedir menos es tener menos credenciales dando vueltas.
            "returnSecureToken": False,
        }).encode()
        pedido = urllib.request.Request(
            f"{self.URL_LOGIN}?key={key}", data=cuerpo,
            headers={"Content-Type": "application/json"})
        try:
            with self._abrir(pedido, timeout=self.TIEMPO_S) as respuesta:
                datos = json.loads(respuesta.read().decode())
        except urllib.error.HTTPError as error:
            try:
                texto = error.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — sin cuerpo, se loguea vacío
                texto = ""
            texto = _sin_key(texto, key)
            motivo = _motivo_de_error(texto)
            # PEDIDO R2 — el estado y el cuerpo, siempre. Ni la URL (lleva la
            # key) ni el pedido (lleva la contraseña).
            _registrar("identity_toolkit_http", estado=error.code,
                       email=email, cuerpo=texto)
            if error.code == 400 and not any(
                    m in motivo for m in MOTIVOS_DE_CONFIGURACION):
                # «No entró», por cualquiera de sus motivos de credencial. Al
                # usuario no se le dice cuál; en el log, de arriba, sí.
                return None
            # PEDIDO R3 — 403, 429, 5xx o una key inválida: no se pudo
            # comprobar. La contraseña podía ser correcta.
            raise ComprobacionNoDisponible(
                f"Identity Toolkit respondió {error.code}: no se pudo comprobar "
                f"la credencial.",
                {"motivo": "proveedor_respondio_error", "estado": error.code}
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            # Red caída, DNS, timeout. Hasta acá ni se capturaban: la request
            # terminaba en un 500 genérico. `reason` no lleva la URL.
            razon = _sin_key(getattr(error, "reason", None) or error, key)
            _registrar("identity_toolkit_red", email=email,
                       tipo=type(error).__name__, razon=razon)
            raise ComprobacionNoDisponible(
                "No se pudo llegar a Identity Toolkit para comprobar la "
                "credencial.", {"motivo": "proveedor_inalcanzable"}) from None
        except ValueError as error:
            # Un 200 que no es JSON no es un veredicto.
            _registrar("identity_toolkit_respuesta_ilegible", email=email,
                       tipo=type(error).__name__)
            raise ComprobacionNoDisponible(
                "La respuesta de Identity Toolkit no se pudo leer.",
                {"motivo": "respuesta_ilegible"}) from None
        uid = datos.get("localId")
        return {"uid": uid} if uid else None

    def fijar_clave(self, uid, clave):
        self.auth.update_user(uid, password=validar_clave(clave))
        # Una clave nueva cierra las demás sesiones. Va acá adentro y no en
        # quien llama para que no exista la combinación «se cambió la clave
        # pero la sesión robada sigue viva».
        self.revocar_sesiones(uid)

    def cambiar_email(self, uid, email):
        self.auth.update_user(uid, email=email)

    def token_de_sesion(self, uid):
        token = self.auth.create_custom_token(uid)
        return token.decode() if isinstance(token, bytes) else token

    def revocar_sesiones(self, uid):
        self.auth.revoke_refresh_tokens(uid)

    def deshabilitar(self, uid):
        self.auth.update_user(uid, disabled=True)
        self.revocar_sesiones(uid)

    def habilitar(self, uid):
        self.auth.update_user(uid, disabled=False)


class CredencialesEnMemoria(Credenciales):
    """Firebase de mentira, para las pruebas y el emulador.

    Modela lo que al portal le importa y nada más: que una clave entre o no,
    que una cuenta deshabilitada no entre, y que revocar sesiones se note.
    """

    def __init__(self):
        self.cuentas = {}   # uid → {email, clave, deshabilitada, revocada_en}
        self._siguiente = 0
        self.revocaciones = []
        # PEDIDO R3 — para probar «no se pudo comprobar»: con un motivo
        # cargado, `verificar_clave` levanta como lo haría Firebase caído.
        self.falla = None

    def sembrar(self, uid, email, clave):
        """Una cuenta con el `uid` que quiera quien prueba.

        `crear()` inventa el `uid`, y a veces hace falta el contrario: que la
        cuenta de Auth tenga el mismo `uid` que ya quedó en
        `cuenta_panelista`. Es lo que pasa en producción —primero existe la
        cuenta, después el vínculo— y en una prueba que arma el vínculo a
        mano hay que poder reproducirlo.
        """
        self.cuentas[uid] = {"email": email, "clave": validar_clave(clave),
                             "deshabilitada": False}
        return {"uid": uid}

    def _por_email(self, email):
        for uid, cuenta in self.cuentas.items():
            if cuenta["email"] == email:
                return uid, cuenta
        return None, None

    def buscar(self, email):
        uid, cuenta = self._por_email(email)
        if not uid:
            return None
        return {"uid": uid, "email": cuenta["email"],
                "deshabilitada": cuenta["deshabilitada"]}

    def crear(self, email, clave):
        self._siguiente += 1
        uid = f"uid-portal-{self._siguiente}"
        self.cuentas[uid] = {"email": email, "clave": validar_clave(clave),
                             "deshabilitada": False}
        return {"uid": uid}

    def verificar_clave(self, email, clave):
        if self.falla:
            raise ComprobacionNoDisponible(
                "Simulación: el proveedor de identidad no contesta.",
                {"motivo": self.falla})
        uid, cuenta = self._por_email(email)
        if not uid or cuenta["deshabilitada"] or cuenta["clave"] != str(clave or ""):
            return None
        return {"uid": uid}

    def fijar_clave(self, uid, clave):
        self.cuentas[uid]["clave"] = validar_clave(clave)
        self.revocar_sesiones(uid)

    def cambiar_email(self, uid, email):
        self.cuentas[uid]["email"] = email

    def token_de_sesion(self, uid):
        return f"token-de-sesion:{uid}"

    def revocar_sesiones(self, uid):
        self.revocaciones.append(uid)

    def deshabilitar(self, uid):
        if uid in self.cuentas:
            self.cuentas[uid]["deshabilitada"] = True
        self.revocar_sesiones(uid)

    def habilitar(self, uid):
        if uid in self.cuentas:
            self.cuentas[uid]["deshabilitada"] = False


def crear(cfg=None):
    """Las credenciales que corresponden al entorno.

    Mismo interruptor que el padrón de R2.12 (`PADRON_USUARIOS`), con su
    propia variable: el portal y la administración pueden querer el doble en
    distintos momentos, y un solo interruptor para los dos obligaría a
    falsear uno para probar el otro.
    """
    if (os.environ.get("CREDENCIALES_PORTAL") or "").lower() in ("memoria", "test"):
        return CredencialesEnMemoria()
    return CredencialesFirebase()
