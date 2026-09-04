"""Retiro de consentimiento y borrado en cascada.

CLAUDE.md: «Baja / retiro de consentimiento ⇒ borrado en cascada: PII
(bóveda) + embeddings (semántico) + salida del muestreo».

Qué implica cada retiro, en concreto:

* `uso_semantico`          → se borran los embeddings del store semántico. La persona
                             sigue en el panel y puede ser convocada.
* `contacto_participacion` → sale del muestreo: se dan de baja sus
                             membresías. Los embeddings quedan (los consintió
                             por separado).
* `todas`                  → cascada completa: lo anterior más el borrado de
                             la PII de la bóveda. Queda una lápida en
                             `persona_borrada` con el token opaco, que no es
                             PII, para poder probar que el retiro se atendió.

El borrado semántico es una llamada a otra instancia y puede fallar. Si falla,
el retiro en la bóveda se completa igual y la lápida queda con `borrado_semantica_en`
en null: `pendientes_de_borrado_semantica()` las lista para reintentar. Nunca
se demora el retiro en la bóveda esperando al store semántico.
"""

from . import consentimiento, db, semantica
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


def _confirmar_borrado_semantica(conn, id_persona, error=None):
    db.ejecutar(
        conn,
        """
        update persona_borrada
           set borrado_semantica_en = case when %s::text is null then now() else null end,
               semantica_error = %s
         where id_persona = %s
        """,
        (error, error, str(id_persona)),
    )


def retirar(conn_boveda, id_persona, finalidad=TODAS, actor=None, conn_semantica=None):
    """Retira el consentimiento y ejecuta la cascada que corresponda."""
    if finalidad != TODAS and finalidad not in consentimiento.FINALIDADES:
        raise DatosInvalidos(
            f"Finalidad desconocida: {finalidad!r}.",
            {"finalidades_validas": list(consentimiento.FINALIDADES) + [TODAS]},
        )
    _existe(conn_boveda, id_persona)

    retirados = consentimiento.marcar_retirado(
        conn_boveda, id_persona, None if finalidad == TODAS else finalidad
    )

    borra_semantica = finalidad in (TODAS, consentimiento.SEMANTICO)
    saca_del_muestreo = finalidad in (TODAS, consentimiento.CONTACTO)
    borra_pii = finalidad == TODAS

    membresias_bajas = _sacar_del_muestreo(conn_boveda, id_persona) if saca_del_muestreo else 0

    if borra_pii:
        _lapida(conn_boveda, id_persona, "retiro_consentimiento", finalidad, actor)

    resultado_semantica = {"estado": "no_aplica"}
    if borra_semantica:
        if conn_semantica is None:
            resultado_semantica = {
                "estado": "pendiente",
                "detalle": "Sin conexión al store semántico; queda para reintentar.",
            }
        else:
            try:
                borrado = semantica.borrar_persona(conn_semantica, id_persona)
                conn_semantica.commit()
                resultado_semantica = {"estado": "ok", **borrado}
                if borra_pii:
                    _confirmar_borrado_semantica(conn_boveda, id_persona)
            except Exception as error:  # el retiro en la bóveda no espera al store semántico
                conn_semantica.rollback()
                resultado_semantica = {"estado": "error", "detalle": str(error)}
                if borra_pii:
                    _confirmar_borrado_semantica(conn_boveda, id_persona, str(error))

    pii_borrada = 0
    if borra_pii:
        # Borra la PII. Las FK con `on delete cascade` se llevan alias,
        # membresías, consentimientos, participaciones y movimientos de puntos.
        pii_borrada = db.ejecutar(
            conn_boveda, "delete from persona where id_persona = %s", (id_persona,)
        )

    return {
        "id_persona": str(id_persona),
        "finalidad_retirada": finalidad,
        "consentimientos_retirados": retirados,
        "membresias_dadas_de_baja": membresias_bajas,
        "pii_borrada": bool(pii_borrada),
        "semantica": resultado_semantica,
    }


def pendientes_de_borrado_semantica(conn_boveda):
    """Bajas cuyo borrado semántico todavía no se pudo confirmar."""
    filas = db.todas(
        conn_boveda,
        """
        select id_persona, motivo, finalidad, borrado_local_en, semantica_error
          from persona_borrada
         where borrado_semantica_en is null
         order by borrado_local_en
        """,
    )
    return [
        {
            "id_persona": str(f["id_persona"]),
            "motivo": f["motivo"],
            "finalidad": f["finalidad"],
            "borrado_local_en": f["borrado_local_en"].isoformat(),
            "semantica_error": f["semantica_error"],
        }
        for f in filas
    ]


def reintentar_borrado_semantica(conn_boveda, conn_semantica):
    """Reintenta las bajas pendientes. Pensado para correr en un job."""
    resultados = []
    for pendiente in pendientes_de_borrado_semantica(conn_boveda):
        id_persona = pendiente["id_persona"]
        try:
            borrado = semantica.borrar_persona(conn_semantica, id_persona)
            conn_semantica.commit()
            _confirmar_borrado_semantica(conn_boveda, id_persona)
            resultados.append({"id_persona": id_persona, "estado": "ok", **borrado})
        except Exception as error:
            conn_semantica.rollback()
            _confirmar_borrado_semantica(conn_boveda, id_persona, str(error))
            resultados.append(
                {"id_persona": id_persona, "estado": "error", "detalle": str(error)}
            )
    return resultados
