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

import os

from .errores import DatosInvalidos

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


class CredencialesFirebase(Credenciales):
    """Firebase Auth de verdad: Admin SDK para todo salvo comprobar la clave.

    Comprobar una contraseña no está en el Admin SDK —a propósito: el Admin
    SDK administra cuentas, no inicia sesiones—, así que esa única operación
    va por la API REST de Identity Toolkit, la misma que usa el SDK del
    navegador. Necesita la *web API key* del proyecto, que es pública por
    diseño (está en la configuración del frontend) pero se lee del entorno
    igual: la que vale es la del ambiente en el que la función corre.
    """

    URL_LOGIN = ("https://identitytoolkit.googleapis.com/v1/"
                 "accounts:signInWithPassword")

    def __init__(self, auth_fb=None, api_key=None):
        if auth_fb is None:
            from firebase_admin import auth as auth_modulo

            auth_fb = auth_modulo
        self.auth = auth_fb
        self._api_key = api_key

    @property
    def api_key(self):
        clave = self._api_key or os.environ.get("FIREBASE_WEB_API_KEY") or ""
        if not clave:
            raise DatosInvalidos(
                "Falta `FIREBASE_WEB_API_KEY`: sin ella no se puede comprobar "
                "ninguna contraseña y nadie entra al portal.")
        return clave

    def buscar(self, email):
        try:
            usuario = self.auth.get_user_by_email(email)
        except Exception:  # noqa: BLE001 — el SDK usa UserNotFoundError
            return None
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
        import json
        import urllib.error
        import urllib.request

        cuerpo = json.dumps({
            "email": email, "password": str(clave or ""),
            # No hace falta el par de tokens de Identity Toolkit: la sesión
            # del navegador se abre después con un token custom del Admin
            # SDK. Pedir menos es tener menos credenciales dando vueltas.
            "returnSecureToken": False,
        }).encode()
        pedido = urllib.request.Request(
            f"{self.URL_LOGIN}?key={self.api_key}", data=cuerpo,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(pedido, timeout=10) as respuesta:
                datos = json.loads(respuesta.read().decode())
        except urllib.error.HTTPError:
            # 400 es «no entró», por cualquiera de sus motivos. No se mira
            # cuál: distinguirlos acá es lo que termina filtrándose en el
            # mensaje de arriba.
            return None
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
