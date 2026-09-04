"""R1.3 — Consentimiento por finalidad, y el gate que lo hace valer.

Dos finalidades independientes (limitación de finalidad):

* `contacto_participacion` — habilita convocar y muestrear.
* `uso_semantico`          — habilita ingestar respuestas al store semántico.

Tener una NO implica tener la otra. El gate se aplica en el punto de uso,
no en el alta: una persona puede estar en el panel y aun así quedar fuera
de una ingesta si no consintió el uso semántico.
"""

from . import db
from .errores import ConsentimientoFaltante, DatosInvalidos

CONTACTO = "contacto_participacion"
SEMANTICO = "uso_semantico"
FINALIDADES = (CONTACTO, SEMANTICO)

VIGENTE = "vigente"
RETIRADO = "retirado"


def _validar_finalidad(finalidad):
    if finalidad not in FINALIDADES:
        raise DatosInvalidos(
            f"Finalidad desconocida: {finalidad!r}.",
            {"finalidades_validas": list(FINALIDADES)},
        )
    return finalidad


def otorgar(conn, id_persona, finalidad, version_texto, panel_id=None):
    """Registra un consentimiento vigente. Re-otorgar agrega una fila nueva:
    el historial no se pisa."""
    _validar_finalidad(finalidad)
    if not (version_texto or "").strip():
        raise DatosInvalidos(
            "Falta la versión del texto consentido: sin ella el consentimiento "
            "no es demostrable."
        )
    fila = db.una(
        conn,
        """
        insert into consentimiento (id_persona, finalidad, panel_id, estado, version_texto)
             values (%s, %s, %s, 'vigente', %s)
          returning id, otorgado_en
        """,
        (id_persona, finalidad, panel_id, version_texto.strip()),
    )
    return {
        "id": fila["id"],
        "finalidad": finalidad,
        "estado": VIGENTE,
        "version_texto": version_texto.strip(),
        "otorgado_en": fila["otorgado_en"].isoformat(),
    }


def esta_vigente(conn, id_persona, finalidad):
    """¿La persona tiene esta finalidad vigente ahora?"""
    _validar_finalidad(finalidad)
    fila = db.una(
        conn,
        """
        select 1
          from consentimiento
         where id_persona = %s and finalidad = %s and estado = 'vigente'
         limit 1
        """,
        (id_persona, finalidad),
    )
    return fila is not None


def exigir(conn, id_persona, finalidad):
    """Gate: aborta la operación si falta la finalidad. Úsese antes de
    convocar (contacto) o de ingestar (semántico)."""
    if not esta_vigente(conn, id_persona, finalidad):
        raise ConsentimientoFaltante(
            f"La persona no tiene consentimiento vigente para «{finalidad}».",
            {"id_persona": str(id_persona), "finalidad": finalidad},
        )


def filtrar_con_consentimiento(conn, ids_persona, finalidad):
    """Parte una lista en (habilitadas, bloqueadas) según la finalidad.

    Sirve para convocar o ingestar en lote sin abortar todo el trabajo por
    una persona: las bloqueadas se informan y quedan afuera.
    """
    _validar_finalidad(finalidad)
    ids = [str(i) for i in ids_persona]
    if not ids:
        return [], []
    filas = db.todas(
        conn,
        """
        select distinct id_persona
          from consentimiento
         where finalidad = %s and estado = 'vigente'
           and id_persona = any(%s::uuid[])
        """,
        (finalidad, ids),
    )
    habilitadas = {str(f["id_persona"]) for f in filas}
    return (
        [i for i in ids if i in habilitadas],
        [i for i in ids if i not in habilitadas],
    )


def listar(conn, id_persona):
    """Historial completo de consentimientos de una persona."""
    filas = db.todas(
        conn,
        """
        select id, finalidad, panel_id, estado, version_texto, otorgado_en, retirado_en
          from consentimiento
         where id_persona = %s
         order by otorgado_en desc, id desc
        """,
        (id_persona,),
    )
    return [
        {
            "id": f["id"],
            "finalidad": f["finalidad"],
            "panel_id": f["panel_id"],
            "estado": f["estado"],
            "version_texto": f["version_texto"],
            "otorgado_en": f["otorgado_en"].isoformat(),
            "retirado_en": f["retirado_en"].isoformat() if f["retirado_en"] else None,
        }
        for f in filas
    ]


def marcar_retirado(conn, id_persona, finalidad=None):
    """Pasa a `retirado` las filas vigentes. `finalidad=None` retira todas.

    Solo cambia el estado del consentimiento: la cascada de borrado la
    orquesta `bajas.retirar()`, que es quien sabe qué implica cada
    finalidad.
    """
    if finalidad is not None:
        _validar_finalidad(finalidad)
        return db.ejecutar(
            conn,
            """
            update consentimiento
               set estado = 'retirado', retirado_en = now()
             where id_persona = %s and finalidad = %s and estado = 'vigente'
            """,
            (id_persona, finalidad),
        )
    return db.ejecutar(
        conn,
        """
        update consentimiento
           set estado = 'retirado', retirado_en = now()
         where id_persona = %s and estado = 'vigente'
        """,
        (id_persona,),
    )
