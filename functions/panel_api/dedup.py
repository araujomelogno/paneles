"""R1.2 — Dedup de identidad en el alta de panelista.

Orden de resolución del HANDOFF de Fase 1; se detiene en el primer match:

1. `documento` exacto            → reutiliza ese `id_persona`.
2. `email` (case-insensitive)    → reutiliza ese `id_persona`.
3. `celular` en E.164            → reutiliza si hay **una sola** titular y
   nada la contradice; si lo comparten varias, revisión.
4. Sin documento ni email, pero `nombre` + `fecha_nacimiento` coinciden
   con una persona existente → NO fusiona: marca el alta en revisión.
5. Sin match                     → crea persona nueva.

Nunca fusiona por parecido: ante duda, decide una persona.

── Por qué el celular es más débil que el correo ──

El documento y el correo son de una persona, y la base lo garantiza con un
índice único (0001). El celular no siempre: el de un hogar, el que usa un
padre mayor y es del hijo, el que la compañía reasignó. Por eso no tiene
índice único (0022) y el paso 3 es más cauto que el 2:

* Reutiliza solo si la persona con ese celular es **la única** que lo tiene.
  Si lo tienen dos, fusionar con cualquiera sería elegir al azar: va a
  revisión, con las dos como candidatas.
* Y solo si nada la contradice: si el alta trae un documento o un correo y
  la titular tiene **otro**, son dos personas que comparten el número, y la
  evidencia fuerte gana. No se fusiona y el alta sigue su camino.
* Y si el alta trae un nombre y no es el de la titular —el padre que se
  anota con el celular del hijo—, tampoco se reutiliza: va a revisión. Un
  nombre no alcanza para fusionar, pero sí para dudar.
* Compara en E.164, que es como se guarda desde R4.4. Lo que no se puede
  normalizar no se compara: «099 123 456» y «+59899123456» son el mismo
  número, y «llamar a la oficina» no es ninguno.
"""

import unicodedata

from . import db
from .preferencias import normalizar_celular

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
    celular = normalizar_celular(_texto(datos.get("celular")))
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

    # 3 — celular, con las cautelas del docstring.
    if celular:
        resolucion = _por_celular(conn, celular, documento, email, nombre)
        if resolucion:
            return resolucion

    # 4 — sin clave fuerte: nombre + fecha de nacimiento es match ambiguo.
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

    # 5 — sin match.
    return Resolucion(CREA)


def _contradice(titular, documento, email):
    """¿La persona que tiene el celular es, por evidencia fuerte, otra?"""
    if documento and titular["documento"] and titular["documento"] != documento:
        return True
    if email and titular["email"] and titular["email"].lower() != email.lower():
        return True
    return False


def _palabras(nombre):
    """Las palabras de un nombre, sin tildes ni mayúsculas."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", nombre or "")
        if not unicodedata.combining(c))
    return set(sin_tildes.casefold().split())


def _mismo_nombre(titular, nombre):
    """¿El nombre del alta es compatible con el de la titular?

    Sin nombre de alguno de los dos no hay con qué dudar. Con los dos, alcanza
    con que uno contenga las palabras del otro: «Ana Pérez» y «Ana Pérez
    Silva» son la misma persona escrita en dos archivos."""
    de_la_titular, del_alta = _palabras(titular["nombre"]), _palabras(nombre)
    if not de_la_titular or not del_alta:
        return True
    return de_la_titular <= del_alta or del_alta <= de_la_titular


def _por_celular(conn, celular, documento, email, nombre):
    """El paso 3: reutiliza, manda a revisión, o `None` para seguir."""
    titulares = db.todas(
        conn,
        """
        select id_persona, nombre, localidad, documento, email
          from persona
         where celular = %s
         order by creado_en
        """,
        (celular,),
    )
    compatibles = [t for t in titulares if not _contradice(t, documento, email)]
    if not compatibles:
        # Nadie lo tiene, o quien lo tiene es otra persona: el celular no
        # decide nada y el alta sigue al paso siguiente.
        return None
    if len(titulares) == 1 and _mismo_nombre(compatibles[0], nombre):
        return Resolucion(REUTILIZA, compatibles[0]["id_persona"], motivo="celular")
    # Lo comparten varias, o la única que lo tiene se llama de otra forma:
    # decide una persona.
    return Resolucion(
        REVISION,
        motivo="celular_compartido" if len(titulares) > 1 else "celular_otro_nombre",
        candidatos=[
            {"id_persona": str(t["id_persona"]), "nombre": t["nombre"],
             "localidad": t["localidad"]}
            for t in compatibles
        ],
    )


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
