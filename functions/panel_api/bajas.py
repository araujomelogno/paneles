"""Retiro de consentimiento y borrado en cascada.

CLAUDE.md: «Baja / retiro de consentimiento ⇒ borrado en cascada: PII
(local) + embeddings (remoto) + salida del muestreo».

Qué implica cada retiro, en concreto:

* `uso_semantico`          → se borran los embeddings del remoto. La persona
                             sigue en el panel y puede ser convocada.
* `contacto_participacion` → sale del muestreo: se dan de baja sus
                             membresías. Los embeddings quedan (los consintió
                             por separado).
* `todas`                  → cascada completa: lo anterior más el borrado de
                             la PII local. Queda una lápida en
                             `persona_borrada` con el token opaco, que no es
                             PII, para poder probar que el retiro se atendió.

El borrado remoto es una llamada a otra instancia y puede fallar. Si falla,
el retiro local se completa igual y la lápida queda con `borrado_remoto_en`
en null: `pendientes_de_borrado_remoto()` las lista para reintentar. Nunca
se demora el retiro local esperando al remoto.
"""

from . import consentimiento, db, remoto
from .errores import DatosInvalidos, NoEncontrado

TODAS = "todas"


def _existe(conn, id_persona):
    if not db.una(conn, "select 1 from persona where id_persona = %s", (id_persona,)):
        raise NoEncontrado(f"No existe la persona {id_persona}.")


def _sacar_del_muestreo(conn, id_persona):
    return db.ejecutar(
        conn,
        """
        update membresia
           set estado = 'baja', fecha_baja = now()
         where id_persona = %s and estado = 'activo'
        """,
        (id_persona,),
    )


def _lapida(conn, id_persona, motivo, finalidad, actor):
    db.ejecutar(
        conn,
        """
        insert into persona_borrada (id_persona, motivo, finalidad, solicitado_por)
             values (%s, %s, %s, %s)
        on conflict (id_persona) do nothing
        """,
        (str(id_persona), motivo, finalidad, actor),
    )


def _confirmar_borrado_remoto(conn, id_persona, error=None):
    db.ejecutar(
        conn,
        """
        update persona_borrada
           set borrado_remoto_en = case when %s::text is null then now() else null end,
               remoto_error = %s
         where id_persona = %s
        """,
        (error, error, str(id_persona)),
    )


def retirar(conn_local, id_persona, finalidad=TODAS, actor=None, conn_remoto=None):
    """Retira el consentimiento y ejecuta la cascada que corresponda."""
    if finalidad != TODAS and finalidad not in consentimiento.FINALIDADES:
        raise DatosInvalidos(
            f"Finalidad desconocida: {finalidad!r}.",
            {"finalidades_validas": list(consentimiento.FINALIDADES) + [TODAS]},
        )
    _existe(conn_local, id_persona)

    retirados = consentimiento.marcar_retirado(
        conn_local, id_persona, None if finalidad == TODAS else finalidad
    )

    borra_remoto = finalidad in (TODAS, consentimiento.SEMANTICO)
    saca_del_muestreo = finalidad in (TODAS, consentimiento.CONTACTO)
    borra_pii = finalidad == TODAS

    membresias_bajas = _sacar_del_muestreo(conn_local, id_persona) if saca_del_muestreo else 0

    if borra_pii:
        _lapida(conn_local, id_persona, "retiro_consentimiento", finalidad, actor)

    resultado_remoto = {"estado": "no_aplica"}
    if borra_remoto:
        if conn_remoto is None:
            resultado_remoto = {
                "estado": "pendiente",
                "detalle": "Sin conexión al store remoto; queda para reintentar.",
            }
        else:
            try:
                borrado = remoto.borrar_persona(conn_remoto, id_persona)
                conn_remoto.commit()
                resultado_remoto = {"estado": "ok", **borrado}
                if borra_pii:
                    _confirmar_borrado_remoto(conn_local, id_persona)
            except Exception as error:  # el retiro local no espera al remoto
                conn_remoto.rollback()
                resultado_remoto = {"estado": "error", "detalle": str(error)}
                if borra_pii:
                    _confirmar_borrado_remoto(conn_local, id_persona, str(error))

    pii_borrada = 0
    if borra_pii:
        # Borra la PII. Las FK con `on delete cascade` se llevan alias,
        # membresías, consentimientos, participaciones y movimientos de puntos.
        pii_borrada = db.ejecutar(
            conn_local, "delete from persona where id_persona = %s", (id_persona,)
        )

    return {
        "id_persona": str(id_persona),
        "finalidad_retirada": finalidad,
        "consentimientos_retirados": retirados,
        "membresias_dadas_de_baja": membresias_bajas,
        "pii_borrada": bool(pii_borrada),
        "remoto": resultado_remoto,
    }


def pendientes_de_borrado_remoto(conn_local):
    """Bajas cuyo borrado remoto todavía no se pudo confirmar."""
    filas = db.todas(
        conn_local,
        """
        select id_persona, motivo, finalidad, borrado_local_en, remoto_error
          from persona_borrada
         where borrado_remoto_en is null
         order by borrado_local_en
        """,
    )
    return [
        {
            "id_persona": str(f["id_persona"]),
            "motivo": f["motivo"],
            "finalidad": f["finalidad"],
            "borrado_local_en": f["borrado_local_en"].isoformat(),
            "remoto_error": f["remoto_error"],
        }
        for f in filas
    ]


def reintentar_borrado_remoto(conn_local, conn_remoto):
    """Reintenta las bajas pendientes. Pensado para correr en un job."""
    resultados = []
    for pendiente in pendientes_de_borrado_remoto(conn_local):
        id_persona = pendiente["id_persona"]
        try:
            borrado = remoto.borrar_persona(conn_remoto, id_persona)
            conn_remoto.commit()
            _confirmar_borrado_remoto(conn_local, id_persona)
            resultados.append({"id_persona": id_persona, "estado": "ok", **borrado})
        except Exception as error:
            conn_remoto.rollback()
            _confirmar_borrado_remoto(conn_local, id_persona, str(error))
            resultados.append(
                {"id_persona": id_persona, "estado": "error", "detalle": str(error)}
            )
    return resultados
