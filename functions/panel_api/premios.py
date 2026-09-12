"""R3.5 — Catálogo de premios y canje.

El canje es la única operación de la fase donde dos usuarios pueden pelear
por el mismo recurso: el saldo de una persona y el stock de un premio. Todo
lo demás del módulo es administración de catálogo.

La regla que hay que sostener es «dadas dos solicitudes concurrentes con
saldo para una sola, solo una prospera». No alcanza con leer el saldo y
después descontar: entre las dos cosas cabe la otra transacción. El canje
bloquea primero la persona (para serializar el saldo) y después el premio
(para serializar el stock), **siempre en ese orden**, que es lo que evita
que dos canjes cruzados se abracen esperándose.

Una cancelación devuelve los puntos como movimiento nuevo, no borrando el
descuento: el ledger de R3.3 no se reescribe.
"""

from . import db, puntos
from .errores import DatosInvalidos, NoEncontrado

SOLICITADO = "solicitado"
ENTREGADO = "entregado"
CANCELADO = "cancelado"
ESTADOS = (SOLICITADO, ENTREGADO, CANCELADO)
# Un canje ya entregado no se cancela: el premio salió. Corregir eso es un
# ajuste manual con motivo, no una transición de estado.
TRANSICIONES = {SOLICITADO: (ENTREGADO, CANCELADO)}


# ── Catálogo ────────────────────────────────────────────────────────

def _serializar_premio(fila):
    return {
        "id": fila["id"], "nombre": fila["nombre"],
        "descripcion": fila["descripcion"], "costo_puntos": fila["costo_puntos"],
        "stock": fila["stock"], "activo": fila["activo"],
        "disponible": bool(fila["activo"]) and (
            fila["stock"] is None or fila["stock"] > 0
        ),
    }


def listar_premios(conn, solo_disponibles=False):
    filas = db.todas(
        conn,
        "select id, nombre, descripcion, costo_puntos, stock, activo "
        "  from catalogo_premio order by activo desc, costo_puntos, nombre",
    )
    premios = [_serializar_premio(f) for f in filas]
    return [p for p in premios if p["disponible"]] if solo_disponibles else premios


def crear_premio(conn, cuerpo):
    nombre = (cuerpo.get("nombre") or "").strip()
    if not nombre:
        raise DatosInvalidos("El premio necesita un nombre.")
    try:
        costo = int(cuerpo.get("costo_puntos"))
    except (TypeError, ValueError):
        raise DatosInvalidos("«costo_puntos» tiene que ser un entero.")
    if costo <= 0:
        raise DatosInvalidos("«costo_puntos» tiene que ser mayor que cero.")
    stock = cuerpo.get("stock")
    if stock is not None:
        try:
            stock = int(stock)
        except (TypeError, ValueError):
            raise DatosInvalidos("«stock» tiene que ser un entero, o nulo (ilimitado).")
        if stock < 0:
            raise DatosInvalidos("«stock» no puede ser negativo.")

    fila = db.una(
        conn,
        """
        insert into catalogo_premio (nombre, descripcion, costo_puntos, stock, activo)
        values (%s, %s, %s, %s, coalesce(%s, true))
        returning id, nombre, descripcion, costo_puntos, stock, activo
        """,
        (nombre, (cuerpo.get("descripcion") or "").strip() or None, costo, stock,
         cuerpo.get("activo")),
    )
    conn.commit()
    return _serializar_premio(fila)


def editar_premio(conn, premio_id, cambios):
    fila = db.una(
        conn,
        "select id, nombre, descripcion, costo_puntos, stock, activo "
        "  from catalogo_premio where id = %s",
        (premio_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe el premio {premio_id}.")

    campos, valores = [], []
    if "nombre" in cambios:
        nombre = (cambios["nombre"] or "").strip()
        if not nombre:
            raise DatosInvalidos("El nombre no puede quedar vacío.")
        campos.append("nombre = %s"); valores.append(nombre)
    if "descripcion" in cambios:
        campos.append("descripcion = %s")
        valores.append((cambios["descripcion"] or "").strip() or None)
    if "costo_puntos" in cambios:
        try:
            costo = int(cambios["costo_puntos"])
        except (TypeError, ValueError):
            raise DatosInvalidos("«costo_puntos» tiene que ser un entero.")
        if costo <= 0:
            raise DatosInvalidos("«costo_puntos» tiene que ser mayor que cero.")
        campos.append("costo_puntos = %s"); valores.append(costo)
    if "stock" in cambios:
        stock = cambios["stock"]
        if stock is not None:
            try:
                stock = int(stock)
            except (TypeError, ValueError):
                raise DatosInvalidos("«stock» tiene que ser un entero, o nulo.")
            if stock < 0:
                raise DatosInvalidos("«stock» no puede ser negativo.")
        campos.append("stock = %s"); valores.append(stock)
    if "activo" in cambios:
        campos.append("activo = %s"); valores.append(bool(cambios["activo"]))

    if not campos:
        return _serializar_premio(fila)
    valores.append(premio_id)
    nueva = db.una(
        conn,
        f"update catalogo_premio set {', '.join(campos)} where id = %s "
        f"returning id, nombre, descripcion, costo_puntos, stock, activo",
        tuple(valores),
    )
    conn.commit()
    return _serializar_premio(nueva)


# ── Canje ───────────────────────────────────────────────────────────

def _serializar_canje(fila):
    return {
        "id": fila["id"], "id_persona": str(fila["id_persona"]),
        "premio_id": fila["premio_id"], "premio": fila.get("premio"),
        "costo_puntos": fila["costo_puntos"], "estado": fila["estado"],
        "movimiento_id": fila.get("movimiento_id"),
        "creado_en": fila["creado_en"].isoformat(),
        "resuelto_en": (
            fila["resuelto_en"].isoformat() if fila.get("resuelto_en") else None
        ),
        "resuelto_por": fila.get("resuelto_por"),
        "nota": fila.get("nota"),
    }


def canjear(conn, id_persona, premio_id, actor=None):
    """Canjea un premio. Descuenta el saldo y baja el stock, o no hace nada.

    El orden de los bloqueos —persona y después premio— es deliberado y no
    debe invertirse: dos canjes que tomaran los bloqueos en orden distinto
    podrían quedarse esperándose mutuamente.
    """
    with conn.transaction():
        # 1 · La persona, que es lo que serializa el saldo.
        saldo_actual = puntos._saldo_bloqueando(conn, id_persona)

        # 2 · El premio, que es lo que serializa el stock.
        premio = db.una(
            conn,
            "select id, nombre, costo_puntos, stock, activo "
            "  from catalogo_premio where id = %s for update",
            (premio_id,),
        )
        if not premio:
            raise NoEncontrado(f"No existe el premio {premio_id}.")
        if not premio["activo"]:
            raise DatosInvalidos(f"El premio «{premio['nombre']}» no está activo.")
        if premio["stock"] is not None and premio["stock"] <= 0:
            raise DatosInvalidos(
                f"No queda stock de «{premio['nombre']}».", {"stock": 0}
            )

        costo = premio["costo_puntos"]
        if saldo_actual < costo:
            raise DatosInvalidos(
                f"Saldo insuficiente: hay {saldo_actual} puntos y "
                f"«{premio['nombre']}» cuesta {costo}.",
                {"saldo": saldo_actual, "costo": costo},
            )

        movimiento = puntos.registrar(
            conn, id_persona, puntos.CANJE, -costo,
            motivo=f"canje · {premio['nombre']}",
            comprobar_saldo=False,   # ya se comprobó con la fila bloqueada
        )
        if premio["stock"] is not None:
            db.ejecutar(
                conn,
                "update catalogo_premio set stock = stock - 1 where id = %s",
                (premio_id,),
            )
        fila = db.una(
            conn,
            """
            insert into canje (id_persona, premio_id, costo_puntos, movimiento_id)
            values (%s, %s, %s, %s)
            returning id, id_persona, premio_id, costo_puntos, estado,
                      movimiento_id, creado_en, resuelto_en, resuelto_por, nota
            """,
            (str(id_persona), premio_id, costo, movimiento["id"]),
        )

    resultado = _serializar_canje({**fila, "premio": premio["nombre"]})
    resultado["saldo_restante"] = puntos.saldo(conn, id_persona)
    return resultado


def listar_canjes(conn, id_persona=None, estado=None, limite=200):
    filas = db.todas(
        conn,
        """
        select c.id, c.id_persona, c.premio_id, c.costo_puntos, c.estado,
               c.movimiento_id, c.creado_en, c.resuelto_en, c.resuelto_por,
               c.nota, p.nombre as premio
          from canje c
          join catalogo_premio p on p.id = c.premio_id
         where (%s::uuid is null or c.id_persona = %s::uuid)
           and (%s::text is null or c.estado = %s::text)
         order by c.creado_en desc, c.id desc
         limit %s
        """,
        (str(id_persona) if id_persona else None,
         str(id_persona) if id_persona else None,
         estado, estado, int(limite)),
    )
    return [_serializar_canje(f) for f in filas]


def resolver(conn, canje_id, estado, actor=None, nota=None):
    """`solicitado → entregado | cancelado`. Cancelar devuelve los puntos."""
    if estado not in (ENTREGADO, CANCELADO):
        raise DatosInvalidos(
            f"«{estado}» no es una resolución de canje.",
            {"estados_validos": [ENTREGADO, CANCELADO]},
        )
    with conn.transaction():
        fila = db.una(
            conn,
            """
            select c.id, c.id_persona, c.premio_id, c.costo_puntos, c.estado,
                   c.movimiento_id, c.creado_en, p.nombre as premio
              from canje c
              join catalogo_premio p on p.id = c.premio_id
             where c.id = %s for update of c
            """,
            (canje_id,),
        )
        if not fila:
            raise NoEncontrado(f"No existe el canje {canje_id}.")
        permitidos = TRANSICIONES.get(fila["estado"], ())
        if estado not in permitidos:
            raise DatosInvalidos(
                f"Un canje «{fila['estado']}» no puede pasar a «{estado}».",
                {"transiciones_validas": list(permitidos)},
            )

        if estado == CANCELADO:
            # Devolución como movimiento nuevo: el descuento original queda
            # en el ledger, porque ocurrió (R3.3).
            puntos.registrar(
                conn, fila["id_persona"], puntos.AJUSTE, fila["costo_puntos"],
                motivo=f"devolución por cancelación del canje {canje_id}"
                       f" · {fila['premio']}",
                origen_movimiento_id=fila["movimiento_id"],
            )
            db.ejecutar(
                conn,
                "update catalogo_premio set stock = stock + 1 "
                " where id = %s and stock is not null",
                (fila["premio_id"],),
            )

        nueva = db.una(
            conn,
            """
            update canje set estado = %s, resuelto_en = now(),
                             resuelto_por = %s, nota = %s
             where id = %s
            returning id, id_persona, premio_id, costo_puntos, estado,
                      movimiento_id, creado_en, resuelto_en, resuelto_por, nota
            """,
            (estado, getattr(actor, "uid", None), nota, canje_id),
        )

    resultado = _serializar_canje({**nueva, "premio": fila["premio"]})
    resultado["saldo_restante"] = puntos.saldo(conn, fila["id_persona"])
    return resultado
