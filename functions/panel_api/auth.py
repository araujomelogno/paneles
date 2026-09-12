"""Autenticación con Firebase Auth y roles del personal de Equipos.

El padrón de usuarios de la app vive en Firestore (`usuarios/{uid}`), no en
Cloud SQL: son empleados de Equipos, no panelistas. Ahí no hay PII de
panelista.

Roles:
  admin       — todo.
  operaciones — responsable de panel: enrola, arma paneles, convoca.
  analista    — fieldea y consulta; no enrola ni da de baja.
  dpo         — cumplimiento: retiros de consentimiento y bajas.
"""

from .errores import NoAutenticado, SinPermiso

ROLES = ("admin", "operaciones", "analista", "dpo")

# Permiso → roles que lo tienen.
PERMISOS = {
    "leer": {"admin", "operaciones", "analista", "dpo"},
    "enrolar": {"admin", "operaciones"},
    "gestionar_paneles": {"admin", "operaciones"},
    "fieldear": {"admin", "operaciones", "analista"},
    "ingestar": {"admin", "operaciones", "analista"},
    "resolver_revision": {"admin", "operaciones"},
    "cumplimiento": {"admin", "dpo"},

    # ── Fase 2 ──
    # Correr consultas semánticas. Es el trabajo del analista, y es aparte de
    # `leer`: consultar mueve el corpus entero por el reranker y por la API de
    # Claude, y el dpo no tiene por qué hacerlo.
    "consultar": {"admin", "operaciones", "analista"},
    # Traducir `id_persona` a datos de contacto. Deshace la seudonimización,
    # así que la tiene quien necesita convocar, y queda registrada (P1).
    "reidentificar": {"admin", "operaciones"},
    # R2.12 — gestión de usuarios de la app. Solo admin, y con permiso propio:
    # es escalada de privilegios, no una tarea más de administrar paneles.
    "gestionar_usuarios": {"admin"},

    # ── Fase 3 ──
    # R3.1 — pedir una propuesta de muestreo y configurar los umbrales de
    # fatiga. Es la decisión de a quién invitar: el trabajo del responsable
    # de panel. El analista consulta, pero no decide la muestra.
    "muestrear": {"admin", "operaciones"},
    # R3.2 — revertir una marca de calidad. Aparte de `ingestar` porque el
    # chequeo lo corre la ingesta y la revisión la hace una persona que se
    # hace cargo: un `sospechoso` revertido son puntos que se pagan.
    "revisar_calidad": {"admin", "operaciones", "analista"},
    # R3.3-R3.6 — mover puntos, administrar el catálogo y resolver canjes.
    # Es dinero para el panelista, así que no lo tiene el analista.
    "gamificacion": {"admin", "operaciones"},
    # R3.7 — aprobar o rechazar inscripciones de la landing. Es un alta de
    # persona, así que va con quien ya podía enrolar.
    "aprobar_inscripciones": {"admin", "operaciones"},
    # R3.7 — publicar el texto de consentimiento. Es una decisión de
    # cumplimiento antes que de operación: la comparten admin y dpo.
    "publicar_consentimiento": {"admin", "dpo"},
    # R3.10 — exportar el resultado con datos personales. Se separa de
    # `reidentificar` a propósito: ver una lista en pantalla y llevarse un
    # archivo con nombres y documentos no son el mismo riesgo, y la spec
    # pide registrarlos como eventos distintos.
    "exportar_identificado": {"admin", "operaciones"},
}


class Actor:
    """Quién está haciendo la operación."""

    def __init__(self, uid, email=None, rol=None, nombre=None):
        self.uid = uid
        self.email = email
        self.rol = rol
        self.nombre = nombre

    def puede(self, permiso):
        return self.rol in PERMISOS.get(permiso, set())

    def exigir(self, permiso):
        if not self.puede(permiso):
            raise SinPermiso(
                f"Tu rol ({self.rol or 'sin rol'}) no puede realizar esta operación.",
                {"permiso": permiso},
            )
        return self

    def como_dict(self):
        return {"uid": self.uid, "email": self.email, "rol": self.rol, "nombre": self.nombre}


def token_de(headers):
    """Extrae el ID token del header Authorization."""
    autorizacion = headers.get("Authorization") or headers.get("authorization") or ""
    if not autorizacion.lower().startswith("bearer "):
        raise NoAutenticado("Falta el token de Firebase Auth (header Authorization).")
    return autorizacion.split(" ", 1)[1].strip()


def actor_de_request(headers, verificar_token=None, buscar_usuario=None):
    """Resuelve el actor. Las dependencias se inyectan para poder probarlo
    sin Firebase."""
    if verificar_token is None or buscar_usuario is None:
        from firebase_admin import auth as fb_auth, firestore

        def verificar_token(token):  # noqa: F811
            return fb_auth.verify_id_token(token)

        def buscar_usuario(uid):  # noqa: F811
            doc = firestore.client().collection("usuarios").document(uid).get()
            return doc.to_dict() if doc.exists else None

    token = token_de(headers)
    try:
        decodificado = verificar_token(token)
    except Exception as error:
        raise NoAutenticado(f"Token inválido: {error}")

    uid = decodificado.get("uid") or decodificado.get("user_id")
    usuario = buscar_usuario(uid) or {}
    if not usuario or usuario.get("activo") is False:
        raise SinPermiso(
            "Tu usuario no está habilitado en el sistema de paneles. "
            "Pedile el alta a un administrador."
        )
    rol = usuario.get("rol")
    if rol not in ROLES:
        raise SinPermiso(f"Rol desconocido: {rol!r}.", {"roles_validos": list(ROLES)})

    return Actor(
        uid=uid,
        email=decodificado.get("email") or usuario.get("email"),
        rol=rol,
        nombre=usuario.get("nombre"),
    )
