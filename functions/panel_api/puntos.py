"""R3.3, R3.4, R3.6 — Ledger de puntos, liquidación y bonos.

El saldo se maneja **como moneda, no como un campo** (R3.3). No hay ninguna
columna `saldo` en ninguna tabla: el saldo es la suma de los movimientos, y
eso no es purismo contable sino la única forma de responder «¿de dónde salió
este número?» un año después. Un campo mutable pierde esa respuesta la
primera vez que alguien lo corrige a mano.

De ahí salen las tres reglas que el módulo sostiene:

- **El saldo nunca es negativo.** No hay constraint que lo garantice —una
  suma no se puede restringir por fila—, así que se garantiza bloqueando a la
  persona antes de leer su saldo. Dos canjes simultáneos con plata para uno
  solo se serializan: uno cobra, el otro rebota.
- **El vencimiento descuenta, no borra.** Un movimiento de `vencimiento`
  apunta al `earn` que venció. Borrar filas dejaría un saldo correcto y una
  historia falsa.
- **Solo se gana por calidad** (R3.4). `respondio` no alcanza: hace falta
  `calidad_estado = 'ok'`. Premiar volumen es la forma más rápida de llenar
  un panel de gente que completa sin leer.
"""

from . import db
from .errores import DatosInvalidos, NoEncontrado

EARN = "earn"
CANJE = "canje"
AJUSTE = "ajuste"
VENCIMIENTO = "vencimiento"
TIPOS = (EARN, CANJE, AJUSTE, VENCIMIENTO)

# Puntos por participación de calidad, cuando el estudio no configura los
# suyos. Como todos los defaults de esta fase, es un punto de partida.
PUNTOS_POR_PARTICIPACION = 100
# Vigencia de los puntos ganados. Nulo sería «no vencen nunca»; R3.3 pide
# vencimiento, así que hay un default y se puede cambiar por liquidación.
MESES_DE_VIGENCIA = 12


# ── Saldo ───────────────────────────────────────────────────────────

def saldo(conn, id_persona):
    """Suma de los movimientos. Es la única definición de saldo que existe."""
    fila = db.una(
        conn,
        "select coalesce(sum(puntos), 0)::int as saldo "
        "  from puntos_movimiento where id_persona = %s",
        (str(id_persona),),
    )
    return fila["saldo"]


def _saldo_bloqueando(conn, id_persona):
    """El saldo, con la persona bloqueada hasta el fin de la transacción.

    Es lo que hace imposible el sobregiro por concurrencia. Sin este bloqueo,
    dos canjes simultáneos leen el mismo saldo, los dos concluyen que alcanza
    y los dos escriben: el saldo termina negativo y ninguna de las dos
    transacciones hizo nada malo por separado.

    Se bloquea la fila de `persona` y no los movimientos porque lo que hay
    que serializar es *la decisión sobre esta persona*, y las filas que
    habría que bloquear son justamente las que todavía no existen.
    """
    fila = db.una(
        conn,
        "select id_persona from persona where id_persona = %s for update",
        (str(id_persona),),
    )
    if not fila:
        raise NoEncontrado(f"No existe la persona {id_persona}.")
    return saldo(conn, id_persona)


def movimientos(conn, id_persona, limite=200):
    filas = db.todas(
        conn,
        """
        select m.id, m.tipo, m.puntos, m.motivo, m.encuesta_id, m.vence_en,
               m.vencido, m.origen_movimiento_id, m.creado_en, e.nombre as encuesta
          from puntos_movimiento m
          left join encuesta e on e.id = m.encuesta_id
         where m.id_persona = %s
         order by m.creado_en desc, m.id desc
         limit %s
        """,
        (str(id_persona), int(limite)),
    )
    return [
        {
            "id": f["id"], "tipo": f["tipo"], "puntos": f["puntos"],
            "motivo": f["motivo"], "encuesta_id": f["encuesta_id"],
            "encuesta": f["encuesta"],
            "vence_en": f["vence_en"].isoformat() if f["vence_en"] else None,
            "vencido": f["vencido"],
            "origen_movimiento_id": f["origen_movimiento_id"],
            "creado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]


def estado_de_cuenta(conn, id_persona):
    """Saldo, movimientos y qué está por vencer. Es lo que ve el panelista."""
    persona = db.una(
        conn,
        "select id_persona, nombre from persona where id_persona = %s",
        (str(id_persona),),
    )
    if not persona:
        raise NoEncontrado(f"No existe la persona {id_persona}.")
    por_vencer = db.todas(
        conn,
        """
        select id, puntos, vence_en
          from puntos_movimiento
         where id_persona = %s and tipo in ('earn','ajuste')
           and vence_en is not null and not vencido and vence_en > now()
         order by vence_en
        """,
        (str(id_persona),),
    )
    return {
        "id_persona": str(id_persona),
        "saldo": saldo(conn, id_persona),
        "movimientos": movimientos(conn, id_persona),
        "por_vencer": [
            {"id": f["id"], "puntos": f["puntos"],
             "vence_en": f["vence_en"].isoformat()}
            for f in por_vencer
        ],
    }


# ── Movimientos ─────────────────────────────────────────────────────

def registrar(conn, id_persona, tipo, puntos, motivo=None, encuesta_id=None,
              vence_en=None, origen_movimiento_id=None, comprobar_saldo=True):
    """Escribe un movimiento. Los negativos no pueden dejar el saldo bajo cero."""
    if tipo not in TIPOS:
        raise DatosInvalidos(
            f"«{tipo}» no es un tipo de movimiento.", {"tipos_validos": list(TIPOS)}
        )
    try:
        puntos = int(puntos)
    except (TypeError, ValueError):
        raise DatosInvalidos(f"«puntos» tiene que ser un entero, no {puntos!r}.")
    if puntos == 0:
        raise DatosInvalidos("Un movimiento de cero puntos no registra nada.")
    if tipo in (EARN,) and puntos < 0:
        raise DatosInvalidos("Un «earn» suma puntos: no puede ser negativo.")
    if tipo in (CANJE, VENCIMIENTO) and puntos > 0:
        raise DatosInvalidos(f"Un «{tipo}» descuenta puntos: tiene que ser negativo.")

    if puntos < 0 and comprobar_saldo:
        actual = _saldo_bloqueando(conn, id_persona)
        if actual + puntos < 0:
            raise DatosInvalidos(
                f"Saldo insuficiente: hay {actual} puntos y el movimiento "
                f"descuenta {abs(puntos)}.",
                {"saldo": actual, "pedido": abs(puntos)},
            )

    fila = db.una(
        conn,
        """
        insert into puntos_movimiento
               (id_persona, tipo, puntos, motivo, encuesta_id, vence_en,
                origen_movimiento_id)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning id, creado_en
        """,
        (str(id_persona), tipo, puntos, motivo, encuesta_id, vence_en,
         origen_movimiento_id),
    )
    return {
        "id": fila["id"], "tipo": tipo, "puntos": puntos, "motivo": motivo,
        "encuesta_id": encuesta_id, "creado_en": fila["creado_en"].isoformat(),
    }


def ajustar(conn, id_persona, puntos, motivo, actor=None):
    """Corrección manual. Exige motivo: un ajuste sin explicación es
    indistinguible de un error."""
    if not (motivo or "").strip():
        raise DatosInvalidos("Un ajuste manual necesita un motivo.")
    texto = motivo.strip()
    if actor is not None and getattr(actor, "uid", None):
        texto = f"{texto} · {actor.uid}"
    movimiento = registrar(conn, id_persona, AJUSTE, puntos, motivo=texto)
    conn.commit()
    return movimiento


# ── R3.6 · Bonos dirigidos ──────────────────────────────────────────

def crear_bono(conn, panel_id, dimension, categoria, puntos_extra,
               hasta=None, actor=None):
    from . import demografia

    if dimension not in demografia.DIMENSIONES_CATEGORICAS:
        raise DatosInvalidos(
            f"«{dimension}» no es una dimensión de segmento.",
            {"dimensiones_validas": list(demografia.DIMENSIONES_CATEGORICAS)},
        )
    if not (categoria or "").strip():
        raise DatosInvalidos("El bono necesita una categoría.")
    try:
        puntos_extra = int(puntos_extra)
    except (TypeError, ValueError):
        raise DatosInvalidos(f"«puntos_extra» tiene que ser un entero.")
    if puntos_extra <= 0:
        raise DatosInvalidos("Un bono suma puntos: tiene que ser mayor que cero.")
    if not db.una(conn, "select 1 from panel where id = %s", (panel_id,)):
        raise NoEncontrado(f"No existe el panel {panel_id}.")

    fila = db.una(
        conn,
        """
        insert into bono_puntos
               (panel_id, dimension, categoria, puntos_extra, hasta, creado_por)
        values (%s, %s, %s, %s, %s, %s)
        returning id, desde, hasta, creado_en
        """,
        (panel_id, dimension, categoria.strip(), puntos_extra, hasta,
         getattr(actor, "uid", None)),
    )
    conn.commit()
    return {
        "id": fila["id"], "panel_id": panel_id, "dimension": dimension,
        "categoria": categoria.strip(), "puntos_extra": puntos_extra,
        "desde": fila["desde"].isoformat(),
        "hasta": fila["hasta"].isoformat() if fila["hasta"] else None,
    }


def listar_bonos(conn, panel_id, solo_vigentes=False):
    filas = db.todas(
        conn,
        """
        select id, panel_id, dimension, categoria, puntos_extra, desde, hasta,
               creado_por, creado_en,
               (desde <= now() and (hasta is null or hasta > now())) as vigente
          from bono_puntos
         where panel_id = %s
         order by desde desc, id desc
        """,
        (panel_id,),
    )
    return [
        {
            "id": f["id"], "dimension": f["dimension"], "categoria": f["categoria"],
            "puntos_extra": f["puntos_extra"], "vigente": f["vigente"],
            "desde": f["desde"].isoformat(),
            "hasta": f["hasta"].isoformat() if f["hasta"] else None,
            "creado_por": f["creado_por"],
        }
        for f in filas if f["vigente"] or not solo_vigentes
    ]


def _bono_para(conn, panel_id, id_persona):
    """Los puntos extra que le tocan a esta persona, hoy.

    Un bono vencido deja de aplicar y no toca los puntos ya otorgados
    (R3.6): esto se evalúa al liquidar, y lo liquidado es un movimiento
    inmutable.
    """
    fila = db.una(
        conn,
        """
        select coalesce(sum(b.puntos_extra), 0)::int as extra,
               string_agg(b.dimension || '=' || b.categoria, ', ') as detalle
          from bono_puntos b
          join v_demografia d on d.id_persona = %s
         where b.panel_id = %s
           and b.desde <= now() and (b.hasta is null or b.hasta > now())
           and ((b.dimension = 'sexo'         and d.sexo = b.categoria)
             or (b.dimension = 'localidad'    and d.localidad = b.categoria)
             or (b.dimension = 'tramo_etario' and d.tramo_etario = b.categoria))
        """,
        (str(id_persona), panel_id),
    )
    return (fila["extra"] or 0), fila["detalle"]


# ── R3.4 · Liquidación ──────────────────────────────────────────────

def liquidables(conn, encuesta_id):
    """Quiénes tienen un punto por cobrar en esta encuesta.

    Respondió y quedó en `ok`, y todavía no se le liquidó. La consulta es la
    definición operativa de R3.4, y por eso vive acá y no repartida en la
    ruta.
    """
    return db.todas(
        conn,
        """
        select p.id_persona, p.calidad_estado
          from participacion p
         where p.encuesta_id = %s
           and p.respondio
           and p.calidad_estado = 'ok'
           and not exists (
                 select 1 from puntos_movimiento m
                  where m.id_persona = p.id_persona
                    and m.encuesta_id = p.encuesta_id
                    and m.tipo = 'earn')
         order by p.id
        """,
        (encuesta_id,),
    )


def liquidar(conn, encuesta_id, ids_persona=None, actor=None):
    """Otorga los puntos de una encuesta a quienes participaron con calidad.

    Idempotente por diseño: el índice único `(id_persona, encuesta_id)` sobre
    los `earn` hace que una segunda corrida no pueda pagar de nuevo, ni
    siquiera si dos personas la disparan a la vez. La comprobación en Python
    es para dar un mensaje decente; la garantía es del índice.
    """
    encuesta = db.una(
        conn,
        "select id, panel_id, nombre, puntos_participacion from encuesta where id = %s",
        (encuesta_id,),
    )
    if not encuesta:
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    base = encuesta["puntos_participacion"] or PUNTOS_POR_PARTICIPACION

    pendientes = liquidables(conn, encuesta_id)
    if ids_persona is not None:
        pedidos = {str(i) for i in ids_persona}
        pendientes = [p for p in pendientes if str(p["id_persona"]) in pedidos]

    liquidados, saltados = [], []
    for fila in pendientes:
        id_persona = str(fila["id_persona"])
        extra, detalle_bono = _bono_para(conn, encuesta["panel_id"], id_persona)
        total = base + extra
        motivo = f"participación de calidad · {encuesta['nombre']}"
        if extra:
            motivo += f" · bono {detalle_bono} (+{extra})"
        try:
            with conn.transaction():
                movimiento = registrar(
                    conn, id_persona, EARN, total, motivo=motivo,
                    encuesta_id=encuesta_id,
                    vence_en=_vencimiento(conn),
                )
        except Exception as error:  # noqa: BLE001
            # El índice único es la última palabra: si alguien liquidó en el
            # medio, este intento no paga y se informa.
            if "puntos_earn_unico_por_encuesta" not in str(error):
                raise
            saltados.append({
                "id_persona": id_persona, "motivo": "ya estaba liquidado",
            })
            continue
        liquidados.append({
            "id_persona": id_persona, "puntos": total, "base": base,
            "bono": extra, "movimiento_id": movimiento["id"],
        })

    conn.commit()
    return {
        "encuesta": {"id": encuesta["id"], "nombre": encuesta["nombre"]},
        "puntos_base": base,
        "liquidados": liquidados,
        "saltados": saltados,
        "total_puntos": sum(l["puntos"] for l in liquidados),
    }


def _vencimiento(conn):
    fila = db.una(
        conn,
        "select (now() + make_interval(months => %s)) as vence",
        (MESES_DE_VIGENCIA,),
    )
    return fila["vence"]


# ── R3.3 · Vencimiento ──────────────────────────────────────────────

def vencer(conn, id_persona=None):
    """Descuenta los lotes vencidos con un movimiento nuevo, sin borrar nada.

    Nunca deja el saldo negativo: si la persona ya gastó esos puntos, el
    lote se marca vencido pero no se descuenta de nuevo —el gasto ya lo
    sacó del saldo, y volver a restarlo sería cobrárselo dos veces—.
    """
    filas = db.todas(
        conn,
        """
        select id, id_persona, puntos, vence_en
          from puntos_movimiento
         where tipo in ('earn','ajuste') and puntos > 0
           and vence_en is not null and not vencido and vence_en <= now()
           and (%s::uuid is null or id_persona = %s::uuid)
         order by id_persona, vence_en, id
        """,
        (str(id_persona) if id_persona else None,
         str(id_persona) if id_persona else None),
    )
    vencidos = []
    for fila in filas:
        persona = str(fila["id_persona"])
        disponible = _saldo_bloqueando(conn, persona)
        a_descontar = min(fila["puntos"], max(0, disponible))
        if a_descontar > 0:
            registrar(
                conn, persona, VENCIMIENTO, -a_descontar,
                motivo=f"vencimiento del movimiento {fila['id']}",
                origen_movimiento_id=fila["id"],
                comprobar_saldo=False,
            )
        db.ejecutar(
            conn, "update puntos_movimiento set vencido = true where id = %s",
            (fila["id"],),
        )
        vencidos.append({
            "id_persona": persona, "movimiento_id": fila["id"],
            "puntos_del_lote": fila["puntos"], "descontados": a_descontar,
            "nota": None if a_descontar == fila["puntos"] else (
                "El lote venció pero esos puntos ya estaban gastados: se "
                "marca vencido y se descuenta solo lo que quedaba."
            ),
        })
    conn.commit()
    return {"vencidos": vencidos, "total_descontado": sum(
        v["descontados"] for v in vencidos)}
