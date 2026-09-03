"""Resolución manual de las altas ambiguas del dedup (caso 3 de R1.2).

El sistema no fusiona por parecido. Cuando el alta coincidió por nombre +
fecha de nacimiento con alguien ya enrolado, queda acá esperando que una
persona decida: es la misma (fusionar) o es otra (crear).
"""

import json

from . import consentimiento, db, dedup, personas
from .errores import Conflicto, DatosInvalidos, NoEncontrado


def listar(conn, estado="pendiente"):
    filas = db.todas(
        conn,
        """
        select id, datos, candidatos, motivo, estado, id_persona, creado_en
          from alta_en_revision
         where (%s::text is null or estado = %s::text)
         order by creado_en asc
        """,
        (estado, estado),
    )
    return [
        {
            "id": f["id"],
            "datos": f["datos"],
            "candidatos": f["candidatos"],
            "motivo": f["motivo"],
            "estado": f["estado"],
            "id_persona": str(f["id_persona"]) if f["id_persona"] else None,
            "creado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]


def _tomar_pendiente(conn, revision_id):
    fila = db.una(
        conn,
        "select id, datos, candidatos, estado from alta_en_revision where id = %s",
        (revision_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe el alta en revisión {revision_id}.")
    if fila["estado"] != "pendiente":
        raise Conflicto(
            f"El alta en revisión {revision_id} ya fue resuelta como "
            f"«{fila['estado']}»."
        )
    return fila


def _cerrar(conn, revision_id, estado, id_persona, actor):
    db.ejecutar(
        conn,
        """
        update alta_en_revision
           set estado = %s, id_persona = %s, resuelto_por = %s, resuelto_en = now()
         where id = %s
        """,
        (estado, id_persona, actor, revision_id),
    )


def resolver(conn, revision_id, decision, id_persona=None, actor=None):
    """`decision`: 'fusionar' (con `id_persona`), 'crear' o 'descartar'."""
    fila = _tomar_pendiente(conn, revision_id)
    datos = fila["datos"] if isinstance(fila["datos"], dict) else json.loads(fila["datos"])
    persona_datos = datos.get("persona") or {}
    consentimientos = datos.get("consentimientos") or []
    panel_id = datos.get("panel_id")
    origen = datos.get("origen")
    id_en_origen = datos.get("id_en_origen")

    if decision == "descartar":
        _cerrar(conn, revision_id, "descartada", None, actor)
        return {"estado": "descartada", "revision_id": revision_id}

    if decision == "fusionar":
        candidatos = {c["id_persona"] for c in (fila["candidatos"] or [])}
        if not id_persona:
            raise DatosInvalidos("Para fusionar hay que indicar el `id_persona`.")
        if str(id_persona) not in candidatos:
            raise DatosInvalidos(
                "El `id_persona` indicado no es uno de los candidatos del match.",
                {"candidatos": sorted(candidatos)},
            )
        personas._completar_faltantes(conn, id_persona, persona_datos)
        destino = id_persona
        estado_final = "fusionada"
    elif decision == "crear":
        destino = personas._crear(conn, persona_datos)
        estado_final = "creada"
    else:
        raise DatosInvalidos(
            f"Decisión desconocida: {decision!r}.",
            {"decisiones_validas": ["fusionar", "crear", "descartar"]},
        )

    if origen and id_en_origen:
        dedup.registrar_alias(conn, destino, origen, id_en_origen)
    for c in consentimientos:
        consentimiento.otorgar(
            conn, destino, c["finalidad"], c["version_texto"], c.get("panel_id")
        )
    if panel_id:
        from . import paneles

        paneles.agregar_miembro(conn, panel_id, destino)

    _cerrar(conn, revision_id, estado_final, destino, actor)
    return {
        "estado": estado_final,
        "revision_id": revision_id,
        "id_persona": str(destino),
    }
