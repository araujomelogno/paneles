"""Borrar una carga, con lo que trajo (PEDIDO del celular, parte 3; D78).

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
OTRO_ESTUDIO = "respuestas_de_otro_estudio"
MOTIVOS = {
    REUTILIZADA: "ya existía: la carga la reutilizó",
    OTRA_CARGA: "también vino en otra carga",
    PANEL: "está o estuvo en un panel",
    PARTICIPACION: "participó en una encuesta",
    PUNTOS: "tiene movimientos de puntos",
    CANJE: "tiene canjes",
    OTRO_ESTUDIO: "tiene respuestas de otro estudio",
}


# ── Quién se va y quién se queda ─────────────────────────────────────

def _clasificar(conn_boveda, conn_semantica, carga_id, ref_estudio, ids=None):
    """`{id_persona: None | motivo}` de las personas vinculadas a la carga
    (o de esas `ids`). `None` es «se da de baja»; un motivo, «se conserva y
    por qué». Una persona que ya no está vinculada no figura."""
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
                        where pa.id_persona = pc.id_persona)       as participacion,
               exists (select 1 from puntos_movimiento pm
                        where pm.id_persona = pc.id_persona)       as puntos,
               exists (select 1 from canje c
                        where c.id_persona = pc.id_persona)        as canje
          from persona_carga pc
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
        motivo = next((m for m in (OTRA_CARGA, PANEL, PARTICIPACION, PUNTOS, CANJE)
                       if f[m]), None)
        clasificacion[id_persona] = motivo
        if motivo is None:
            candidatas.append(id_persona)

    # Lo único que vive del otro lado: respuestas de otro estudio.
    for id_persona in semantica.con_respuestas_de_otro_estudio(
            conn_semantica, candidatas, ref_estudio):
        clasificacion[id_persona] = OTRO_ESTUDIO
    return clasificacion


def _resumen_de(clasificacion):
    por_motivo = {}
    for motivo in clasificacion.values():
        if motivo:
            por_motivo[motivo] = por_motivo.get(motivo, 0) + 1
    return {
        "personas": len(clasificacion),
        "se_dan_de_baja": sum(1 for m in clasificacion.values() if m is None),
        "se_conservan": sum(1 for m in clasificacion.values() if m),
        "conservadas_por_motivo": [
            {"motivo": m, "etiqueta": MOTIVOS[m], "personas": por_motivo[m]}
            for m in MOTIVOS if m in por_motivo],
    }


def previa(conn_boveda, conn_semantica, carga_id):
    """Lo que el borrado va a hacer, sin hacer nada."""
    carga = cargas.obtener(conn_boveda, carga_id)
    clasificacion = _clasificar(conn_boveda, conn_semantica, carga_id,
                                carga["ref_estudio"])
    estudio = semantica.contar_estudio(conn_semantica, carga["ref_estudio"])
    return {
        "carga": {"id": carga["id"], "nombre": carga["nombre"],
                  "ref_estudio": str(carga["ref_estudio"])},
        **_resumen_de(clasificacion),
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
           encolador=None, solo_revisar=False, tamano_lote=None):
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
        salida = previa(conn_boveda, conn_semantica, carga_id)
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

    vista = previa(conn_boveda, conn_semantica, carga_id)
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

    respuestas = semantica.borrar_respuestas_de_estudio_de(
        conn_semantica, ids, ref_estudio)
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
        if clasificacion[id_persona] is None:
            bajas.retirar(conn_boveda, id_persona, bajas.TODAS,
                          actor=plan.get("actor"), conn_semantica=conn_semantica,
                          motivo=MOTIVO_LAPIDA)
            dadas_de_baja += 1
            continue
        db.ejecutar(conn_boveda,
                    "delete from persona_tematica where id_persona = %s and carga_id = %s",
                    (id_persona, carga_id))
        db.ejecutar(conn_boveda,
                    "delete from persona_carga where id_persona = %s and carga_id = %s",
                    (id_persona, carga_id))
        conservadas += 1

    return {"operacion": OPERACION, "respuestas_borradas": respuestas,
            "personas_dadas_de_baja": dadas_de_baja,
            "personas_conservadas": conservadas,
            "personas_ya_procesadas": ya_procesadas}


# ── El final ─────────────────────────────────────────────────────────

def finalizar(conn_boveda, conn_semantica, borrado_id):
    """Cuando todos los lotes terminaron: el cuestionario (semántico,
    primero), después la carga, y el registro con los conteos. Idempotente:
    se puede llamar de nuevo si se cortó."""
    borrado = db.una(conn_boveda, "select * from carga_borrado where id = %s",
                     (borrado_id,))
    if borrado["estado"] == TERMINADO:
        return _final(borrado)

    semantica.borrar_estudio(conn_semantica, borrado["ref_estudio"])
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
    if borrado["carga_id"] is not None:
        db.ejecutar(conn_boveda, "delete from carga where id = %s",
                    (borrado["carga_id"],))
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
