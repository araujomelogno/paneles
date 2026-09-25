"""R2.12 — Gestión de usuarios de la app desde la solapa Configuración.

Esto es escalada de privilegios por diseño: quien puede dar de alta a alguien
con rol `admin` puede darle acceso a toda la bóveda. De ahí las restricciones,
que no son adornos:

* **Solo `admin`**, con un permiso propio (`gestionar_usuarios`). No alcanza
  con «ser de operaciones»: es una capacidad aparte de administrar paneles.
* **Nadie se puede sacar a sí mismo el rol de admin ni desactivarse.** No es
  paternalismo: es lo que evita quedarse sin ningún administrador y tener
  que volver a entrar por el script de emergencia.
* **Rol desconocido, rechazado.** Los roles son cuatro y están en `auth.py`.
  Un rol inventado no falla al escribirse: falla después, cuando la persona
  entra y ningún permiso le aplica, y eso es peor.
* **Desactivar no borra.** Se apaga el acceso; la ficha, la auditoría y todo
  lo que la persona hizo queda. Borrar al usuario borraría el rastro de sus
  operaciones, que es justo lo que hay que conservar.
* **Todo queda auditado**, con autor y fecha, en `usuario_auditoria`
  (bóveda). El registro es de solo agregar.
* **El enlace de acceso no se guarda.** El alta devuelve un enlace de
  restablecimiento para mostrar una sola vez; la clave con la que se creó la
  cuenta es aleatoria y no se guarda en ningún lado. Un enlace de
  restablecimiento guardado sería una credencial guardada.

  «No se guarda» no es lo mismo que «no se puede volver a pedir», y confundir
  las dos cosas dejaba sin entrada a quien cerraba el modal antes de copiar.
  `generar_acceso()` emite uno nuevo cuando haga falta: es exactamente lo que
  hace «¿Olvidaste tu contraseña?» en el login, así que no agrega riesgo, y
  **queda auditado** porque pedir el enlace de otra persona es una operación
  sensible aunque no cambie nada.

El padrón vive en Firestore (`usuarios/{uid}`) y las cuentas en Firebase
Auth; la auditoría, en la bóveda. El acceso a Firebase está detrás de la
interfaz `Padron`, con una implementación en memoria, para poder probar todas
estas reglas sin desplegar nada.

El script `scripts/alta_usuario.js` sigue existiendo: es la vía de
emergencia y de arranque (el primer admin no puede darse de alta a sí mismo
desde una app a la que no puede entrar).
"""

import secrets

from . import auditoria, auth, db
from .errores import Conflicto, DatosInvalidos, NoEncontrado, SinPermiso

ROLES = auth.ROLES

CAMPOS_FICHA = ("nombre", "email", "rol", "activo")


def _normalizar_email(email):
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise DatosInvalidos(f"Email inválido: {email!r}.")
    return email


def _validar_rol(rol):
    if rol not in ROLES:
        raise DatosInvalidos(
            f"Rol desconocido: {rol!r}. Un rol que no existe no da error al "
            f"guardarse: da error cuando la persona entra y ningún permiso le "
            f"aplica.",
            {"roles_validos": list(ROLES)},
        )
    return rol


ADVERTENCIA_ACCESO = (
    "Este enlace no queda guardado en ningún lado. Pasáselo a la persona por "
    "un canal privado; si se pierde, se genera otro desde el padrón."
)


def _acceso(padron, email, motivo):
    """El bloque que la interfaz muestra con el enlace para fijar la clave.

    `link` puede venir en `None`: Firebase puede fallar al generarlo y eso no
    invalida el alta —la cuenta quedó creada—, así que se informa y se ofrece
    la salida de siempre, que es «¿Olvidaste tu contraseña?» en el login.
    """
    return {
        "metodo": "restablecimiento",
        "motivo": motivo,
        "link": padron.link_de_reseteo(email),
        "mostrar_una_vez": True,
        "advertencia": ADVERTENCIA_ACCESO,
    }


def clave_al_azar(largo=16):
    """Clave inicial de una cuenta nueva. No se guarda ni se devuelve: la
    persona entra por el enlace de restablecimiento."""
    return secrets.token_urlsafe(largo)


# ════════════════════════════════════════════════════════════════════
#  El padrón, detrás de una interfaz
# ════════════════════════════════════════════════════════════════════

class Padron:
    """Interfaz sobre Firebase Auth + Firestore.

    Existe para que las reglas de R2.12 —quién puede qué, qué no se puede
    hacer sobre uno mismo, qué queda auditado— se puedan probar sin Firebase.
    """

    def buscar_por_email(self, email):
        """`{uid, email, nombre, desactivado}` o None."""
        raise NotImplementedError

    def crear_cuenta(self, email, nombre, clave):
        """Crea la cuenta en Auth y devuelve `{uid}`."""
        raise NotImplementedError

    def leer_ficha(self, uid):
        raise NotImplementedError

    def escribir_ficha(self, uid, datos):
        raise NotImplementedError

    def listar_fichas(self):
        raise NotImplementedError

    def cambiar_estado_cuenta(self, uid, desactivada):
        raise NotImplementedError

    def link_de_reseteo(self, email):
        """Enlace para que la persona fije su clave. None si no se pudo."""
        raise NotImplementedError


class PadronFirebase(Padron):
    """El padrón real. La función corre con su cuenta de servicio, así que no
    hace falta configurar credenciales: el SDK de Admin las toma del entorno
    de Cloud Functions."""

    COLECCION = "usuarios"

    def __init__(self, auth_fb=None, firestore_fb=None):
        if auth_fb is None or firestore_fb is None:
            from firebase_admin import auth as auth_modulo, firestore as firestore_modulo

            auth_fb = auth_fb or auth_modulo
            firestore_fb = firestore_fb or firestore_modulo.client()
        self.auth = auth_fb
        self.bd = firestore_fb

    def _coleccion(self):
        return self.bd.collection(self.COLECCION)

    def buscar_por_email(self, email):
        try:
            usuario = self.auth.get_user_by_email(email)
        except Exception:  # noqa: BLE001 — el SDK usa UserNotFoundError
            return None
        return {
            "uid": usuario.uid,
            "email": usuario.email,
            "nombre": usuario.display_name,
            "desactivado": bool(getattr(usuario, "disabled", False)),
        }

    def crear_cuenta(self, email, nombre, clave):
        usuario = self.auth.create_user(
            email=email, password=clave, display_name=nombre or email,
            email_verified=False,
        )
        return {"uid": usuario.uid}

    def leer_ficha(self, uid):
        doc = self._coleccion().document(uid).get()
        return doc.to_dict() if doc.exists else None

    def escribir_ficha(self, uid, datos):
        self._coleccion().document(uid).set(datos, merge=True)

    def listar_fichas(self):
        return [{"uid": doc.id, **(doc.to_dict() or {})} for doc in self._coleccion().stream()]

    def cambiar_estado_cuenta(self, uid, desactivada):
        self.auth.update_user(uid, disabled=bool(desactivada))

    def link_de_reseteo(self, email):
        try:
            return self.auth.generate_password_reset_link(email)
        except Exception:  # noqa: BLE001 — sin dominio configurado, por ejemplo
            return None


class PadronEnMemoria(Padron):
    """Padrón de mentira, para las pruebas y el emulador."""

    def __init__(self):
        self.cuentas = {}   # uid → {email, nombre, desactivado}
        self.fichas = {}    # uid → ficha
        self._siguiente = 0

    def buscar_por_email(self, email):
        for uid, cuenta in self.cuentas.items():
            if cuenta["email"] == email:
                return {"uid": uid, **cuenta}
        return None

    def crear_cuenta(self, email, nombre, clave):
        self._siguiente += 1
        uid = f"uid-{self._siguiente}"
        self.cuentas[uid] = {
            "email": email, "nombre": nombre or email, "desactivado": False,
        }
        return {"uid": uid}

    def leer_ficha(self, uid):
        return dict(self.fichas[uid]) if uid in self.fichas else None

    def escribir_ficha(self, uid, datos):
        self.fichas.setdefault(uid, {}).update(datos)

    def listar_fichas(self):
        return [{"uid": uid, **ficha} for uid, ficha in self.fichas.items()]

    def cambiar_estado_cuenta(self, uid, desactivada):
        if uid in self.cuentas:
            self.cuentas[uid]["desactivado"] = bool(desactivada)

    def link_de_reseteo(self, email):
        return f"https://ejemplo.invalido/reset?email={email}"


def crear_padron(cfg=None):
    """El padrón que corresponde al entorno."""
    import os

    if (os.environ.get("PADRON_USUARIOS") or "").lower() in ("memoria", "test"):
        return PadronEnMemoria()
    return PadronFirebase()


# ════════════════════════════════════════════════════════════════════
#  Operaciones
# ════════════════════════════════════════════════════════════════════

def _serializar(ficha, cuenta=None):
    activo = ficha.get("activo")
    return {
        "uid": ficha.get("uid"),
        "nombre": ficha.get("nombre"),
        "email": ficha.get("email"),
        "rol": ficha.get("rol"),
        "activo": True if activo is None else bool(activo),
        "estado": "activo" if (activo is None or activo) else "desactivado",
        "rol_valido": ficha.get("rol") in ROLES,
        "cuenta_desactivada": bool(cuenta.get("desactivado")) if cuenta else None,
        "actualizado_en": ficha.get("actualizado_en"),
    }


def listar(padron):
    """El padrón completo: nombre, email, rol y estado.

    Se marca `rol_valido` en vez de esconder al usuario con un rol que no
    existe: si quedó uno mal cargado (por el script de emergencia, por
    ejemplo), hay que poder verlo y arreglarlo desde acá.
    """
    fichas = sorted(
        padron.listar_fichas(),
        key=lambda f: (f.get("nombre") or f.get("email") or ""),
    )
    return {
        "total": len(fichas),
        "roles": list(ROLES),
        "items": [_serializar(f) for f in fichas],
    }


def obtener(padron, uid):
    ficha = padron.leer_ficha(uid)
    if not ficha:
        raise NoEncontrado(f"No hay ficha de usuario para {uid}.")
    return _serializar({"uid": uid, **ficha})


def alta(conn, padron, cuerpo, actor):
    """Da de alta (o pone al día) un usuario de la app.

    **Idempotente por email.** Si ya hay una cuenta en Auth con ese correo,
    no se crea otra ni se toca la clave: se actualiza la ficha y el rol. Es
    el caso normal, no un error: alguien que ya tenía cuenta en otro producto
    de Equipos, o un alta que se hizo dos veces. Devolver un conflicto acá
    obligaría a borrar la cuenta para poder darla de alta, que es exactamente
    lo que no hay que hacer.
    """
    email = _normalizar_email(cuerpo.get("email"))
    rol = _validar_rol((cuerpo.get("rol") or "").strip().lower())
    nombre = (cuerpo.get("nombre") or "").strip() or None

    existente = padron.buscar_por_email(email)
    acceso = None

    if existente:
        uid = existente["uid"]
        ficha_previa = padron.leer_ficha(uid) or {}
        rol_anterior = ficha_previa.get("rol")
        estado = "existente"
    else:
        uid = padron.crear_cuenta(email, nombre, clave_al_azar())["uid"]
        ficha_previa, rol_anterior, estado = {}, None, "creado"
        # La clave con la que se creó la cuenta es aleatoria y no se guarda:
        # la persona entra por este enlace y fija la suya. No queda en ninguna
        # pantalla ni en ningún log; si se pierde, `generar_acceso()` emite
        # otro.
        acceso = _acceso(padron, email, "alta")

    padron.escribir_ficha(uid, {
        "nombre": nombre or ficha_previa.get("nombre") or email,
        "email": email,
        "rol": rol,
        "activo": True,
    })
    # Un alta sobre alguien que estaba desactivado lo reactiva: es lo que
    # quiere quien la hace, y queda auditado como tal.
    if existente and existente.get("desactivado"):
        padron.cambiar_estado_cuenta(uid, False)

    if estado == "creado":
        accion = "alta"
    elif rol_anterior != rol:
        accion = "cambio_rol"
    else:
        accion = "actualizacion"

    registro = auditoria.registrar_usuario(
        conn, accion, uid, actor=actor, email_objetivo=email,
        rol_anterior=rol_anterior, rol_nuevo=rol,
        detalle={"nombre": nombre, "cuenta": estado},
    )

    return {
        "uid": uid,
        "estado": estado,
        "usuario": obtener(padron, uid),
        "acceso": acceso,
        "auditoria": registro,
    }


def cambiar(conn, padron, uid, cambios, actor):
    """Cambia rol, nombre o estado de un usuario.

    Las dos barandas que el spec pide, y el motivo de cada una:

    * No podés sacarte tu propio rol de admin. Si el único administrador se
      degrada, el sistema queda sin nadie que pueda dar de alta a nadie, y la
      única salida es el script de emergencia con credenciales de GCP.
    * No podés desactivarte. Mismo motivo, más directo: te quedás afuera.

    Un cambio de rol vale para la **próxima operación**: el rol se resuelve
    contra la ficha en cada request (ver `auth.actor_de_request`), así que no
    hace falta que la persona vuelva a entrar, pero la operación que ya está
    corriendo no cambia de permisos en el medio.
    """
    ficha = padron.leer_ficha(uid)
    if not ficha:
        raise NoEncontrado(f"No hay ficha de usuario para {uid}.")

    actor_uid = getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None)
    es_uno_mismo = actor_uid is not None and actor_uid == uid

    rol_anterior = ficha.get("rol")
    activo_anterior = ficha.get("activo")
    activo_anterior = True if activo_anterior is None else bool(activo_anterior)

    nuevos = {}
    acciones = []

    if "rol" in cambios and cambios["rol"] is not None:
        rol = _validar_rol(str(cambios["rol"]).strip().lower())
        if rol != rol_anterior:
            if es_uno_mismo and rol_anterior == "admin":
                raise SinPermiso(
                    "No podés sacarte tu propio rol de administrador. Si el "
                    "último admin se degrada, no queda nadie que pueda dar de "
                    "alta a nadie. Pedile el cambio a otro administrador.",
                    {"uid": uid, "rol_actual": rol_anterior},
                )
            nuevos["rol"] = rol
            acciones.append(("cambio_rol", {"de": rol_anterior, "a": rol}))

    if "activo" in cambios and cambios["activo"] is not None:
        activo = bool(cambios["activo"])
        if activo != activo_anterior:
            if es_uno_mismo and not activo:
                raise SinPermiso(
                    "No podés desactivar tu propio usuario: te quedarías "
                    "afuera del sistema. Pedíselo a otro administrador.",
                    {"uid": uid},
                )
            nuevos["activo"] = activo
            acciones.append((
                "desactivacion" if not activo else "reactivacion",
                {"activo": activo},
            ))

    if "nombre" in cambios and (cambios["nombre"] or "").strip():
        nombre = cambios["nombre"].strip()
        if nombre != ficha.get("nombre"):
            nuevos["nombre"] = nombre
            acciones.append(("actualizacion", {"nombre": nombre}))

    if not nuevos:
        raise Conflicto("No hay nada que cambiar en este usuario.")

    padron.escribir_ficha(uid, nuevos)
    if "activo" in nuevos:
        # Desactivar apaga también la cuenta de Auth: si no, la persona sigue
        # pudiendo pedir un token, y el rechazo depende solo de la ficha.
        # Nada se borra: la ficha y toda su auditoría quedan.
        padron.cambiar_estado_cuenta(uid, not nuevos["activo"])

    registros = [
        auditoria.registrar_usuario(
            conn, accion, uid, actor=actor, email_objetivo=ficha.get("email"),
            rol_anterior=rol_anterior, rol_nuevo=nuevos.get("rol", rol_anterior),
            detalle=detalle,
        )
        for accion, detalle in acciones
    ]

    return {
        "uid": uid,
        "cambios": nuevos,
        "usuario": obtener(padron, uid),
        "vigencia": "El cambio aplica desde la próxima operación de esa persona.",
        "auditoria": registros,
    }


def generar_acceso(conn, padron, uid, actor):
    """Emite un enlace nuevo para que la persona fije su clave.

    Es la salida al problema que tenía el alta: el enlace se mostraba una vez
    y si se cerraba el modal no había forma de recuperarlo. No se recupera
    —no se guardó—, se genera otro.

    **No baja el listón de seguridad.** Es la misma operación que hace
    «¿Olvidaste tu contraseña?» en el login, con la diferencia de que la pide
    un administrador en vez del titular. Por eso queda auditado: es lo único
    que distingue «le pasé el enlace al compañero que no podía entrar» de un
    intento de tomar la cuenta de alguien.

    **Un usuario desactivado no recibe enlace.** El enlace funcionaría —Auth
    lo emite igual— y después la persona no podría entrar, con lo que el
    administrador creería haber resuelto algo que no resolvió. Primero se
    reactiva.
    """
    ficha = padron.leer_ficha(uid)
    if not ficha:
        raise NoEncontrado(f"No hay ficha de usuario para {uid}.")

    activo = ficha.get("activo")
    if activo is not None and not activo:
        raise Conflicto(
            "Ese usuario está desactivado: el enlace se generaría igual y la "
            "persona seguiría sin poder entrar. Reactivalo primero.",
            {"uid": uid, "email": ficha.get("email")},
        )

    email = _normalizar_email(ficha.get("email"))
    acceso = _acceso(padron, email, "regeneracion")

    registro = auditoria.registrar_usuario(
        conn, "enlace_acceso", uid, actor=actor, email_objetivo=email,
        rol_anterior=ficha.get("rol"), rol_nuevo=ficha.get("rol"),
        # Se registra **si se pudo generar**, no el enlace: guardarlo sería
        # guardar una credencial, que es justo lo que este diseño evita.
        detalle={"generado": acceso["link"] is not None},
    )

    return {
        "uid": uid,
        "usuario": obtener(padron, uid),
        "acceso": acceso,
        "auditoria": registro,
    }


def acciones_auditables(conn):
    """El catálogo de acciones, con su etiqueta. La interfaz lo lee de acá en
    vez de repetir el diccionario y quedarse vieja cuando aparezca una nueva."""
    filas = db.todas(
        conn,
        "select codigo, etiqueta, descripcion from accion_usuario order by orden")
    return [dict(f) for f in filas]


def historial(conn, uid=None, limite=200):
    """La auditoría de gestión de usuarios: quién hizo qué y cuándo."""
    return {"items": auditoria.listar_usuario(conn, uid, limite)}
