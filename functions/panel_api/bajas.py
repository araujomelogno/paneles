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

R5.3 — ese patrón era correcto y estaba escrito para **un** consumidor. Desde
la Fase 5 se generaliza: cada retiro genera una fila en `borrado_pendiente`
por cada sistema registrado y activo cuyo alcance incluya la finalidad. El
store semántico de `paneles` sigue borrándose acá mismo y su pendiente se
cierra en el acto cuando sale bien; el de otro consumidor queda abierto hasta
que ese consumidor lo confirme. La bóveda nunca espera a ninguno de los dos.
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


def _generar_pendientes(conn, id_persona, finalidad):
    """R5.3 — le avisa a cada consumidor registrado que tiene que borrar.

    `finalidad=None` es baja total. La función de base es la que decide a
    quién le toca, mirando `sistema_consumidor.alcance_finalidades` y la fecha
    de alta de cada uno: un retiro parcial no molesta a un sistema que no
    trata esa finalidad, y una baja anterior al alta de un consumidor no es
    responsabilidad suya.
    """
    fila = db.una(
        conn, "select generar_borrados_pendientes(%s, %s) as cuantos",
        (str(id_persona), finalidad))
    return fila["cuantos"] if fila else 0


def _cerrar_pendiente_propio(conn, id_persona, alcance):
    """Cierra el pendiente de `paneles`, que es este mismo código.

    Usa la misma función que usa un consumidor externo —deriva el sistema de
    la conexión— y no un `update` a mano: si `paneles` se cerrara el pendiente
    por otro camino, la cascada tendría dos mecanismos y uno de los dos
    envejecería.

    `confirmar_borrado()` es estricta a propósito: falla si no hay un
    pendiente abierto, para que un consumidor no crea que confirmó algo que no
    existía. Acá eso sí puede pasar sin que nada esté mal —un segundo retiro
    de la misma finalidad, o un alcance que `paneles` no tiene declarado—, así
    que se mira antes en vez de dejar que la excepción aborte la transacción
    del retiro.
    """
    abierto = db.una(
        conn,
        """
        select 1 from borrado_pendiente
         where id_persona = %s and alcance = %s
           and sistema = sistema_de_la_conexion()
           and confirmado_en is null
        """,
        (str(id_persona), alcance))
    if abierto:
        db.ejecutar(conn, "select confirmar_borrado(%s, %s)",
                    (str(id_persona), alcance))


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

    # El aviso a los consumidores va **antes** de borrar la PII: después del
    # `delete` la persona ya no existe y el pendiente tendría que armarse a
    # ciegas. `borrado_pendiente` no tiene FK a `persona` justamente por eso,
    # para poder sobrevivirla.
    alcance = "total" if finalidad == TODAS else finalidad
    pendientes = _generar_pendientes(
        conn_boveda, id_persona, None if finalidad == TODAS else finalidad)

    if borra_pii:
        _lapida(conn_boveda, id_persona, "retiro_consentimiento", finalidad, actor)

    resultado_semantica = {"estado": "no_aplica"}
    if not borra_semantica:
        # Un retiro que no toca el store semántico no le deja nada por hacer a
        # `paneles`: el pendiente se cierra en el acto, porque dejarlo abierto
        # diría que falta un borrado que no existe.
        _cerrar_pendiente_propio(conn_boveda, id_persona, alcance)
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
                _cerrar_pendiente_propio(conn_boveda, id_persona, alcance)
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
        # Cuántos sistemas quedaron notificados, y cuáles siguen sin
        # confirmar. Es lo que convierte «la cascada anda» en algo que se
        # puede mirar.
        "consumidores_notificados": pendientes,
        "borrados_sin_confirmar": sin_confirmar(conn_boveda, id_persona),
    }


def sin_confirmar(conn_boveda, id_persona=None):
    """Bajas que algún consumidor todavía no confirmó haber ejecutado.

    Es el tablero del DPO. Sin `id_persona` devuelve todo lo abierto, que es
    la métrica que importa: una baja abierta por mucho tiempo es un
    incumplimiento real, no una tarea pendiente.
    """
    donde = "where id_persona = %s" if id_persona else ""
    filas = db.todas(
        conn_boveda,
        f"""
        select id_persona, sistema, sistema_nombre, alcance, finalidad,
               solicitado_en, dias_abierto, intentos, ultimo_error,
               contacto_tecnico
          from v_borrados_sin_confirmar
          {donde}
        """,
        (str(id_persona),) if id_persona else (),
    )
    return [
        {
            "id_persona": str(f["id_persona"]),
            "sistema": f["sistema"],
            "sistema_nombre": f["sistema_nombre"],
            "alcance": f["alcance"],
            "finalidad": f["finalidad"],
            "solicitado_en": f["solicitado_en"].isoformat(),
            "dias_abierto": f["dias_abierto"],
            "intentos": f["intentos"],
            "ultimo_error": f["ultimo_error"],
            "contacto_tecnico": f["contacto_tecnico"],
        }
        for f in filas
    ]


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
            _cerrar_pendiente_propio(conn_boveda, id_persona, "total")
            resultados.append({"id_persona": id_persona, "estado": "ok", **borrado})
        except Exception as error:
            conn_semantica.rollback()
            _confirmar_borrado_semantica(conn_boveda, id_persona, str(error))
            resultados.append(
                {"id_persona": id_persona, "estado": "error", "detalle": str(error)}
            )
    return resultados
