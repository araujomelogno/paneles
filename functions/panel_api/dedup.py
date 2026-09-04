"""R1.2 — Dedup de identidad en el alta de panelista.

Orden de resolución del HANDOFF de Fase 1; se detiene en el primer match:

1. `documento` exacto            → reutiliza ese `id_persona`.
2. `email` (case-insensitive)    → reutiliza ese `id_persona`.
3. Sin documento ni email, pero `nombre` + `fecha_nacimiento` coinciden
   con una persona existente → NO fusiona: marca el alta en revisión.
4. Sin match                     → crea persona nueva.

Nunca fusiona por parecido: ante duda, decide una persona.
"""

from . import db

REUTILIZA = "reutiliza"
REVISION = "revision"
CREA = "crea"


class Resolucion:
    def __init__(self, accion, id_persona=None, motivo=None, candidatos=None):
        self.accion = accion
        self.id_persona = id_persona
        self.motivo = motivo
        self.candidatos = candidatos or []

    def como_dict(self):
        return {
            "accion": self.accion,
            "id_persona": str(self.id_persona) if self.id_persona else None,
            "motivo": self.motivo,
            "candidatos": self.candidatos,
        }


def _texto(valor):
    valor = (valor or "").strip()
    return valor or None


def resolver(conn, datos):
    """Aplica el orden de resolución sobre los datos de un alta."""
    documento = _texto(datos.get("documento"))
    email = _texto(datos.get("email"))
    nombre = _texto(datos.get("nombre"))
    fecha_nacimiento = _texto(datos.get("fecha_nacimiento"))

    # 1 — documento exacto.
    if documento:
        fila = db.una(
            conn,
            "select id_persona from persona where documento = %s",
            (documento,),
        )
        if fila:
            return Resolucion(REUTILIZA, fila["id_persona"], motivo="documento")

    # 2 — email, case-insensitive.
    if email:
        fila = db.una(
            conn,
            "select id_persona from persona where lower(email) = lower(%s)",
            (email,),
        )
        if fila:
            return Resolucion(REUTILIZA, fila["id_persona"], motivo="email")

    # 3 — sin clave fuerte: nombre + fecha de nacimiento es match ambiguo.
    #     Homónimos con la misma fecha existen; fusionar automáticamente
    #     sería peor que pedir una decisión humana.
    if not documento and not email and nombre and fecha_nacimiento:
        filas = db.todas(
            conn,
            """
            select id_persona, nombre, localidad
              from persona
             where lower(nombre) = lower(%s)
               and fecha_nacimiento = %s
            """,
            (nombre, fecha_nacimiento),
        )
        if filas:
            candidatos = [
                {
                    "id_persona": str(f["id_persona"]),
                    "nombre": f["nombre"],
                    "localidad": f["localidad"],
                }
                for f in filas
            ]
            return Resolucion(
                REVISION, motivo="nombre_fecha_nacimiento", candidatos=candidatos
            )

    # 4 — sin match.
    return Resolucion(CREA)


def buscar_por_alias(conn, origen, id_en_origen):
    """`id_persona` con el que una plataforma externa ya conoce a la persona."""
    fila = db.una(
        conn,
        "select id_persona from alias_origen where origen = %s and id_en_origen = %s",
        (origen, id_en_origen),
    )
    return fila["id_persona"] if fila else None


def registrar_alias(conn, id_persona, origen, id_en_origen):
    """Guarda cómo nombró a la persona la plataforma de origen. Idempotente."""
    db.ejecutar(
        conn,
        """
        insert into alias_origen (id_persona, origen, id_en_origen)
             values (%s, %s, %s)
        on conflict (origen, id_en_origen) do nothing
        """,
        (id_persona, origen, id_en_origen),
    )
