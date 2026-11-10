"""Borrar una carga, con lo que trajo (PEDIDO del celular, parte 3; D78).

**Desde D81 (R-EU.10) una carga es un archivo de una encuesta**, y borrarla
responde a «esto lo cargué mal»:

* se borran **las respuestas que esa carga escribió por última vez**
  (`respuesta.carga_id`); las que una carga posterior reemplazó se quedan,
  porque son de la otra. Si era la única carga de la encuesta, se va el
  cuestionario entero, como antes;
* la participación de quien **solo** estaba en esta carga: si fue
  convocada, vuelve a `respondio = false`; si no, se borra;
* las personas que la carga **creó** y no tienen otra vida se **ofrecen**
  para dar de baja (`dar_de_baja_creadas`). Es una opción, no un efecto: sin
  ella se conservan, sin el vínculo ni las respuestas de esta carga;
* las temáticas de cada persona se recalculan para el estudio con lo que
  queda;
* una encuesta independiente que se queda sin cargas ni participaciones
  —la que se creó para una carga desde Panelistas— se borra con ella.

Borrar **la encuesta** entera es otra cosa (`encuestas_borrado`, R-EU.9):
ahí las personas siempre quedan.

El caso: una carga hecha con un mapeo equivocado, o de prueba, que no
debería existir. Lo que se borra es **lo que la carga trajo**, no todo lo que
aparece en ella:

* **Las respuestas de ese estudio**, de todas las personas, y su
  cuestionario. Las unidades de evidencia que quedan sin respuestas las borra
  el trigger de la `semantica/0010`; las compartidas con otro estudio se
  conservan solas.
* **Las personas que la carga creó y no tienen otra vida en el sistema** se
  dan de baja por `bajas.retirar()` —el mismo camino que la baja de un
  panelista—, que genera el `borrado_pendiente` para COLOQUIO. Un `delete`
  directo dejaría en COLOQUIO el contacto de gente que ya no existe.
* **Las demás se conservan**: las reutilizadas, y las creadas acá que
  después entraron en otra carga o en un panel, participaron en una
  encuesta, tienen puntos o canjes, o respuestas de otro estudio. Pierden el
  vínculo con esta carga y sus respuestas de este estudio, nada más.

«Sin otra vida» se decide en `_clasificar`, y es la misma función para la
vista previa y para cada lote: así lo que se muestra antes de confirmar es lo
que pasa. Se re-evalúa en cada lote, como el gate de consentimiento en la
ingesta: si alguien se sumó a un panel entre la confirmación y su lote, se
conserva.

── Cómo corre ──

Toca dos bases sin una transacción común, así que es un **trabajo
reanudable** por la vía diferida: un `ingesta_trabajo` sobre la carga con
`plan.operacion = 'borrado_carga'` y un lote por grupo de personas. Por
reutilizar esa vía, la guarda de la `0029` vale sola: no se borra una carga
con una ingesta en curso, ni se ingesta en una que se está borrando.

En cada lote va **primero el store semántico** (sus respuestas de este
estudio, y se commitea) y **después la bóveda** (bajas y vínculos). Si algo
falla en el medio quedan personas sin respuestas de esta carga —se arregla
reintentando—, nunca respuestas que apuntan a personas que ya no existen.
Cuando terminan todos los lotes, `finalizar` borra el cuestionario (el
semántico, otra vez primero) y recién entonces la carga.

Volver a pedir el borrado de una carga con un borrado en curso **lo
reanuda**: finaliza si los lotes ya terminaron, reintenta los que fallaron,
o devuelve el trabajo para seguirlo si sigue corriendo.

`carga_borrado` (boveda/0030) es la auditoría: quién, cuándo, la vista
previa confirmada y los conteos finales. Sobrevive a la carga.
"""

import json
import os

import psycopg

from . import bajas, cargas, db, diferida, semantica
from .errores import Conflicto, DatosInvalidos, IngestaEnCurso

OPERACION = "borrado_carga"
MOTIVO_LAPIDA = "borrado_de_carga"
PERSONAS_POR_LOTE = int(os.environ.get("BORRADO_PERSONAS_POR_LOTE", "500"))

EN_CURSO = "en_curso"
TERMINADO = "terminado"

# Por qué una persona de la carga se conserva. El orden es el de la
# explicación: la primera razón que aplica es la que se informa.
REUTILIZADA = "reutilizada"
OTRA_CARGA = "otra_carga"
PANEL = "panel"
PARTICIPACION = "participacion"
PUNTOS = "puntos"
CANJE = "canje"
# D85 — se le escribió desde una encuesta: el registro de a quién se le
# escribió es una vida propia, y la baja se lo llevaría.
MENSAJES = "mensajes"
OTRO_ESTUDIO = "respuestas_de_otro_estudio"
MOTIVOS = {
    REUTILIZADA: "ya existía: la carga la reutilizó",
    OTRA_CARGA: "también vino en otra carga",
    PANEL: "está o estuvo en un panel",
    PARTICIPACION: "participó en una encuesta",
    PUNTOS: "tiene movimientos de puntos",
    CANJE: "tiene canjes",
    MENSAJES: "recibió mensajes de una encuesta",
    OTRO_ESTUDIO: "tiene respuestas de otro estudio",
}


# ── Quién se va y quién se queda ─────────────────────────────────────

def _clasificar(conn_boveda, conn_semantica, carga_id, ref_estudio, ids=None):
    """`{id_persona: None | motivo}` de las personas vinculadas a la carga
    (o de esas `ids`). `None` es «se puede dar de baja»; un motivo, «se
    conserva y por qué». Una persona que ya no está vinculada no figura.

    La participación en **la encuesta de esta carga** no cuenta como otra
    vida: la registró esta misma carga (R-EU.1)."""
    filas = db.todas(
        conn_boveda,
        """
        select pc.id_persona, pc.origen,
               exists (select 1 from persona_carga o
                        where o.id_persona = pc.id_persona
                          and o.carga_id <> pc.carga_id)            as otra_carga,
               exists (select 1 from membresia m
                        where m.id_persona = pc.id_persona)        as panel,
               exists (select 1 from participacion pa
                        where pa.id_persona = pc.id_persona
                          and (pa.encuesta_id <> c.encuesta_id
                               or pa.convocado))                   as participacion,
               exists (select 1 from puntos_movimiento pm
                        where pm.id_persona = pc.id_persona)       as puntos,
               exists (select 1 from canje c
                        where c.id_persona = pc.id_persona)        as canje,
               exists (select 1 from envio e
                        where e.id_persona = pc.id_persona)        as mensajes
          from persona_carga pc
          join carga c on c.id = pc.carga_id
         where pc.carga_id = %s
           and (%s::uuid[] is null or pc.id_persona = any(%s::uuid[]))
        """,
        (carga_id, ids, ids))

    clasificacion = {}
    candidatas = []
    for f in filas:
        id_persona = str(f["id_persona"])
        if f["origen"] != "creada":
            clasificacion[id_persona] = REUTILIZADA
            continue
        motivo = next((m for m in (OTRA_CARGA, PANEL, PARTICIPACION, PUNTOS, CANJE,
                                    MENSAJES)
                       if f[m]), None)
        clasificacion[id_persona] = motivo
        if motivo is None:
            candidatas.append(id_persona)

    # Lo único que vive del otro lado: respuestas de otro estudio.
    for id_persona in semantica.con_respuestas_de_otro_estudio(
            conn_semantica, candidatas, ref_estudio):
        clasificacion[id_persona] = OTRO_ESTUDIO
    return clasificacion


def _resumen_de(clasificacion, dar_de_baja_creadas=True):
    por_motivo = {}
    for motivo in clasificacion.values():
        if motivo:
            por_motivo[motivo] = por_motivo.get(motivo, 0) + 1
    candidatas = sum(1 for m in clasificacion.values() if m is None)
    return {
        "personas": len(clasificacion),
        # R-EU.10 — las que se **pueden** dar de baja: las creó esta carga y
        # no tienen otra vida. Se dan de baja solo si se elige.
        "se_pueden_dar_de_baja": candidatas,
        "se_dan_de_baja": candidatas if dar_de_baja_creadas else 0,
        "se_conservan": len(clasificacion) - (candidatas if dar_de_baja_creadas else 0),
        "conservadas_por_motivo": [
            {"motivo": m, "etiqueta": MOTIVOS[m], "personas": por_motivo[m]}
            for m in MOTIVOS if m in por_motivo],
    }


def _unica(conn, carga):
    """¿Es la única carga de su encuesta? Entonces el estudio es suyo."""
    return db.una(conn, "select count(*)::int as n from carga where encuesta_id = %s",
                  (carga["encuesta_id"],))["n"] == 1


def _participaciones(conn, carga_id):
    """R-EU.10 — qué pasa con la participación de quien solo estaba en esta
    carga dentro de su encuesta: `{vuelven_a_no_respondio, se_borran}`."""
    fila = db.una(
        conn,
        """
        select count(*) filter (where pa.convocado)::int     as vuelven,
               count(*) filter (where not pa.convocado)::int as se_borran
          from persona_carga pc
          join carga c on c.id = pc.carga_id
          join participacion pa on pa.encuesta_id = c.encuesta_id
                               and pa.id_persona = pc.id_persona
         where pc.carga_id = %s
           and not exists (select 1 from persona_carga o
                             join carga oc on oc.id = o.carga_id
                            where o.id_persona = pc.id_persona
                              and oc.encuesta_id = c.encuesta_id
                              and o.carga_id <> c.id)
        """,
        (carga_id,))
    return {"vuelven_a_no_respondio": fila["vuelven"], "se_borran": fila["se_borran"]}


def previa(conn_boveda, conn_semantica, carga_id, dar_de_baja_creadas=False):
    """Lo que el borrado va a hacer, sin hacer nada."""
    carga = cargas.obtener(conn_boveda, carga_id)
    clasificacion = _clasificar(conn_boveda, conn_semantica, carga_id,
                                carga["ref_estudio"])
    unica = _unica(conn_boveda, carga)
    if unica:
        estudio = semantica.contar_estudio(conn_semantica, carga["ref_estudio"])
    else:
        estudio = {"respuestas": semantica.contar_carga(
            conn_semantica, carga["ref_estudio"], carga_id),
                   "unidades_que_se_borran": None}
    return {
        "carga": {"id": carga["id"], "nombre": carga["nombre"],
                  "ref_estudio": str(carga["ref_estudio"]),
                  "encuesta_id": carga["encuesta_id"],
                  "encuesta": carga["encuesta"]},
        "unica_de_su_encuesta": unica,
        **_resumen_de(clasificacion, dar_de_baja_creadas),
        "dar_de_baja_creadas": bool(dar_de_baja_creadas),
        "participaciones": _participaciones(conn_boveda, carga_id),
        "respuestas_a_borrar": estudio["respuestas"],
        "unidades_que_se_borran": estudio["unidades_que_se_borran"],
        "irreversible": True,
    }


# ── Lanzar (o reanudar) ──────────────────────────────────────────────

def _borrado_en_curso(conn, carga_id):
    return db.una(
        conn,
        "select * from carga_borrado where carga_id = %s and estado = %s",
        (carga_id, EN_CURSO))


def lanzar(conn_boveda, conn_semantica, carga_id, confirmacion=None, actor=None,
           encolador=None, solo_revisar=False, tamano_lote=None,
           dar_de_baja_creadas=False):
    """`(status, cuerpo)`. Con `solo_revisar`, la vista previa —de la misma
    ruta que borra, como la revisión de una importación—. Sin él, exige
    `confirmacion` igual al nombre de la carga y lanza o reanuda."""
    carga = cargas.obtener(conn_boveda, carga_id)
    en_curso = _borrado_en_curso(conn_boveda, carga_id)
    activo = diferida.trabajo_activo(conn_boveda, "carga", carga_id)

    if solo_revisar:
        # Una ingesta en curso bloquea desde la vista previa: no hay qué
        # confirmar. Un borrado en curso no bloquea: se ofrece reanudarlo.
        if activo and not en_curso:
            diferida.exigir_sin_trabajo_activo(conn_boveda, "carga", carga_id)
        salida = previa(conn_boveda, conn_semantica, carga_id, dar_de_baja_creadas)
        salida["borrado_en_curso"] = _publico(en_curso) if en_curso else None
        return 200, salida

    if (confirmacion or "").strip() != carga["nombre"].strip():
        raise DatosInvalidos(
            "Para borrar la carga hay que escribir su nombre exacto.",
            {"campo": "confirmacion"})

    if en_curso:
        return _reanudar(conn_boveda, conn_semantica, en_curso, encolador)

    # La guarda de verdad es el índice de la 0029 (al encolar); esto es para
    # no calcular nada si ya se sabe que no se puede.
    diferida.exigir_sin_trabajo_activo(conn_boveda, "carga", carga_id)

    vista = previa(conn_boveda, conn_semantica, carga_id, dar_de_baja_creadas)
    try:
        borrado = db.una(
            conn_boveda,
            """insert into carga_borrado (carga_id, carga_nombre, ref_estudio,
                                          previa, actor, actor_email)
               values (%s, %s, %s, %s::jsonb, %s, %s)
            returning *""",
            (carga_id, carga["nombre"], str(carga["ref_estudio"]),
             json.dumps(vista, default=str), getattr(actor, "uid", None),
             getattr(actor, "email", None)))
    except psycopg.errors.UniqueViolation:
        # Dos pedidos a la vez: el índice `carga_borrado_uno_en_curso` deja
        # pasar uno. El otro se entera, y volver a pedirlo lo reanuda.
        conn_boveda.rollback()
        raise Conflicto("Ya hay un borrado de esta carga en curso. Volvé a "
                        "abrir la carga para seguirlo.") from None

    ids = [str(f["id_persona"]) for f in db.todas(
        conn_boveda,
        "select id_persona from persona_carga where carga_id = %s order by id_persona",
        (carga_id,))]
    if not ids:
        conn_boveda.commit()
        return 200, {**finalizar(conn_boveda, conn_semantica, borrado["id"]),
                     "previa": vista}

    plan = {"operacion": OPERACION, "carga_id": carga_id,
            "ref_estudio": str(carga["ref_estudio"]), "borrado_id": borrado["id"],
            "encuesta_id": carga["encuesta_id"],
            "unica_de_su_encuesta": vista["unica_de_su_encuesta"],
            "dar_de_baja_creadas": bool(dar_de_baja_creadas),
            "actor": getattr(actor, "email", None) or getattr(actor, "uid", None)}
    try:
        salida = diferida.encolar(
            conn_boveda, "carga", carga_id, plan, ids, actor=actor,
            tamano_lote=tamano_lote or PERSONAS_POR_LOTE, encolador=encolador)
    except IngestaEnCurso:
        conn_boveda.rollback()   # sin trabajo no hay borrado que registrar
        raise
    db.ejecutar(conn_boveda, "update carga_borrado set trabajo_id = %s where id = %s",
                (salida["trabajo_id"], borrado["id"]))
    conn_boveda.commit()
    return 202, {**salida, "borrado_id": borrado["id"], "previa": vista}


def _reanudar(conn_boveda, conn_semantica, borrado, encolador):
    """Un borrado que quedó a medias, desde donde quedó."""
    if borrado["trabajo_id"] is None:
        return 200, finalizar(conn_boveda, conn_semantica, borrado["id"])
    estado = diferida.estado(conn_boveda, borrado["trabajo_id"])
    if not estado["terminal"]:
        return 202, {**estado, "borrado_id": borrado["id"], "reanudado": "en_curso"}
    if estado["estado"] == diferida.TERMINADA:
        return 200, finalizar(conn_boveda, conn_semantica, borrado["id"])
    vuelta = diferida.reintentar(conn_boveda, borrado["trabajo_id"],
                                 encolador=encolador)
    return 202, {**vuelta, "borrado_id": borrado["id"], "reanudado": "reintento"}


def _publico(borrado):
    return {"borrado_id": borrado["id"], "trabajo_id": borrado["trabajo_id"],
            "creado_en": borrado["creado_en"].isoformat(),
            "actor": borrado["actor_email"] or borrado["actor"]}


# ── Un lote ──────────────────────────────────────────────────────────

def procesar_lote(conn_boveda, conn_semantica, plan, ids):
    """Las personas de un lote: sus respuestas de este estudio (semántico,
    primero y commiteado) y después su baja o su desvinculación (bóveda).
    Idempotente: un reintento no encuentra lo que ya se hizo."""
    ref_estudio, carga_id = plan["ref_estudio"], plan["carga_id"]
    ids = [str(i) for i in ids]
    # Un plan anterior a D81 no tiene estas claves: era la única carga de su
    # estudio, y daba de baja a las creadas.
    unica = plan.get("unica_de_su_encuesta", True)
    dar_de_baja = plan.get("dar_de_baja_creadas", True)

    respuestas = semantica.borrar_respuestas_de_estudio_de(
        conn_semantica, ids, ref_estudio, carga_id=None if unica else carga_id)
    conn_semantica.commit()

    clasificacion = _clasificar(conn_boveda, conn_semantica, carga_id,
                                ref_estudio, ids)
    dadas_de_baja = conservadas = ya_procesadas = 0
    for id_persona in ids:
        if id_persona not in clasificacion:
            # Ya no está vinculada: la dio de baja otro camino, o es un
            # reintento de algo que ya se hizo.
            ya_procesadas += 1
            continue
        if clasificacion[id_persona] is None and dar_de_baja:
            bajas.retirar(conn_boveda, id_persona, bajas.TODAS,
                          actor=plan.get("actor"), conn_semantica=conn_semantica,
                          motivo=MOTIVO_LAPIDA)
            dadas_de_baja += 1
            continue
        _desvincular(conn_boveda, id_persona, carga_id)
        conservadas += 1

    return {"operacion": OPERACION, "respuestas_borradas": respuestas,
            "personas_dadas_de_baja": dadas_de_baja,
            "personas_conservadas": conservadas,
            "personas_ya_procesadas": ya_procesadas}


def _desvincular(conn, id_persona, carga_id):
    """La persona se queda, sin esta carga. Si no la trae otra carga de la
    misma encuesta, su participación vuelve atrás (R-EU.10): convocada,
    a `respondio = false`; no convocada, se borra. Sus temáticas del estudio
    se recalculan al final, con lo que quede."""
    otra = db.una(
        conn,
        """select 1 from persona_carga o
             join carga oc on oc.id = o.carga_id
            where o.id_persona = %s and o.carga_id <> %s
              and oc.encuesta_id = (select encuesta_id from carga where id = %s)""",
        (id_persona, carga_id, carga_id))
    if not otra:
        encuesta = db.una(conn, "select encuesta_id from carga where id = %s",
                          (carga_id,))["encuesta_id"]
        db.ejecutar(
            conn,
            """update participacion set respondio = false, respondio_en = null
                where encuesta_id = %s and id_persona = %s and convocado""",
            (encuesta, id_persona))
        db.ejecutar(
            conn,
            """delete from participacion
                where encuesta_id = %s and id_persona = %s and not convocado""",
            (encuesta, id_persona))
    # D94 · R-INC.3 — si esta era su primera carga en la encuesta y sigue en
    # una posterior, esa pasa a ser la primera (`reutilizada`).
    cargas.reasignar_primera_aparicion(conn, carga_id, [id_persona])
    db.ejecutar(conn,
                "delete from persona_carga where id_persona = %s and carga_id = %s",
                (id_persona, carga_id))


# ── El final ─────────────────────────────────────────────────────────

def finalizar(conn_boveda, conn_semantica, borrado_id):
    """Cuando todos los lotes terminaron: el cuestionario (semántico,
    primero), después la carga, y el registro con los conteos. Idempotente:
    se puede llamar de nuevo si se cortó."""
    borrado = db.una(conn_boveda, "select * from carga_borrado where id = %s",
                     (borrado_id,))
    if borrado["estado"] == TERMINADO:
        return _final(borrado)

    plan = {}
    if borrado["trabajo_id"] is not None:
        plan = db.una(conn_boveda, "select plan from ingesta_trabajo where id = %s",
                      (borrado["trabajo_id"],))["plan"] or {}
    carga = db.una(conn_boveda, "select id, encuesta_id from carga where id = %s",
                   (borrado["carga_id"],)) if borrado["carga_id"] else None
    unica = plan.get("unica_de_su_encuesta",
                     (borrado["previa"] or {}).get("unica_de_su_encuesta", True))
    if unica:
        semantica.borrar_estudio(conn_semantica, borrado["ref_estudio"])
    elif carga:
        semantica.borrar_respuestas_de_carga(
            conn_semantica, borrado["ref_estudio"], carga["id"])
    conn_semantica.commit()

    # Las respuestas se cuentan contra el estudio y no por lote: un lote que
    # se cortó después de commitear el semántico ya no las encuentra al
    # reintentarse, y la suma de lotes daría de menos. Al terminar no queda
    # ninguna del estudio, y mientras se borraba no pudo entrar otra (0029):
    # se borraron las que había al confirmar.
    respuestas = (borrado["previa"] or {}).get("respuestas_a_borrar", 0)

    lotes = {}
    if borrado["trabajo_id"] is not None:
        lotes = (diferida.estado(conn_boveda, borrado["trabajo_id"])["resumen"]
                 or {})
    if carga is not None:
        # Las personas que no se procesaron por lote (no tenían vínculo)
        # no hay; las que quedaban, ya se desvincularon.
        # D94 · R-INC.3 — lo mismo para quien todavía tenga el vínculo.
        cargas.reasignar_primera_aparicion(conn_boveda, carga["id"])
        db.ejecutar(conn_boveda, "delete from carga where id = %s", (carga["id"],))
        _cerrar_encuesta(conn_boveda, conn_semantica, carga["encuesta_id"])
    final = db.una(
        conn_boveda,
        """update carga_borrado
              set estado = %s, terminado_en = now(),
                  personas_dadas_de_baja = %s, personas_conservadas = %s,
                  respuestas_borradas = %s
            where id = %s
        returning *""",
        (TERMINADO, lotes.get("personas_dadas_de_baja", 0),
         lotes.get("personas_conservadas", 0),
         respuestas, borrado_id))
    conn_boveda.commit()
    print(f"[borrado] carga={borrado['carga_id']} borrado={borrado_id} "
          f"bajas={final['personas_dadas_de_baja']} "
          f"conservadas={final['personas_conservadas']} "
          f"respuestas={final['respuestas_borradas']}", flush=True)
    return _final(final)


def _cerrar_encuesta(conn_boveda, conn_semantica, encuesta_id):
    """Después de borrar una carga: la encuesta independiente que quedó sin
    cargas ni participaciones era solo su contenedor (la creó una carga desde
    Panelistas) y se va; las demás recalculan las temáticas de su gente con
    lo que quedó en el cuestionario."""
    from . import tematicas

    vacia = db.una(
        conn_boveda,
        """select e.tipo = 'independiente'
                  and not exists (select 1 from carga c where c.encuesta_id = e.id)
                  and not exists (select 1 from participacion pa
                                   where pa.encuesta_id = e.id) as vacia
             from encuesta e where e.id = %s""",
        (encuesta_id,))
    if not vacia:
        return
    if vacia["vacia"]:
        db.ejecutar(conn_boveda, "delete from encuesta where id = %s", (encuesta_id,))
        return
    tematicas.recalcular_encuesta(conn_boveda, conn_semantica, encuesta_id)


def _final(borrado):
    return {"operacion": OPERACION, "borrado_id": borrado["id"],
            "estado": borrado["estado"], "carga": borrado["carga_nombre"],
            "personas_dadas_de_baja": borrado["personas_dadas_de_baja"],
            "personas_conservadas": borrado["personas_conservadas"],
            "respuestas_borradas": borrado["respuestas_borradas"]}


def al_terminar_el_trabajo(conn_boveda, conn_semantica, plan):
    """Lo llama `diferida` cuando el último lote cierra el trabajo como
    terminado. Si falla, no se pierde nada: el borrado queda `en_curso` y
    volver a pedirlo lo finaliza (`_reanudar`)."""
    try:
        return finalizar(conn_boveda, conn_semantica, plan["borrado_id"])
    except Exception as error:  # noqa: BLE001
        conn_semantica.rollback()
        conn_boveda.rollback()
        print(f"[borrado] no se pudo finalizar el borrado {plan['borrado_id']}: "
              f"{type(error).__name__}: {error}. Queda en curso: volver a "
              f"pedirlo lo termina.", flush=True)
        return None
