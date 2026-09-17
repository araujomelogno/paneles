"""R4.3 — Verificación de contacto de la landing.

La landing de Fase 3 aceptaba cualquier envío: nadie comprobaba que el celular
o el correo fueran de quien se estaba inscribiendo. Eso deja entrar dos cosas
distintas y las dos malas —datos inventados, que ensucian la cola de
aprobación, y **datos ajenos**, que es inscribir a alguien sin que se entere—.

El mecanismo es un código de un solo uso: se pide, se manda al contacto, y la
inscripción **no existe** hasta que se verifica. No es una casilla más del
formulario: es una precondición de escribir.

Cuatro decisiones que vale la pena explicar:

**El código se guarda hasheado.** Un código en claro en la base es una
credencial de un solo uso al alcance de cualquiera que lea la tabla, y alcanza
para inscribir a nombre de otro. Se guarda el hash y se compara el hash.

**La IP también se hashea.** Para limitar la tasa por origen alcanza con saber
que dos pedidos vinieron del mismo lado; de cuál no hace falta, y guardarlo
sería juntar un dato personal más en una superficie pública.

**Los intentos se cuentan y el código se agota.** Sin eso, seis dígitos se
adivinan por fuerza bruta en minutos.

**El envío va detrás de una interfaz.** Igual que los embeddings y el
reranker: en producción un proveedor real de SMS o de correo; sin configurar,
uno que registra el código en el log y lo dice. Así la landing se puede
recorrer entera en desarrollo, y en producción la ausencia de proveedor es
visible en el diagnóstico en vez de silenciosa.
"""

import hashlib
import hmac
import os
import secrets

from . import db
from .errores import Conflicto, DatosInvalidos, NoEncontrado

CELULAR = "celular"
EMAIL = "email"
CANALES = (CELULAR, EMAIL)

# Cuánto vive un código. Diez minutos es el equilibrio habitual: alcanza para
# ir a buscar el teléfono y no tanto como para que un código filtrado sirva al
# día siguiente.
MINUTOS_DE_VIDA = 10

# Cuántas veces se puede errar antes de quemar el código.
MAX_INTENTOS = 5

# Cuántos códigos se pueden pedir desde el mismo origen en una hora. Es el
# freno a la tasa inusual de envíos: no bloquea a nadie legítimo —nadie se
# inscribe diez veces por hora— y corta el uso de la landing como oráculo o
# como forma de mandarle mensajes a terceros.
MAX_POR_ORIGEN_POR_HORA = 10

# Y cuántos al mismo destino, que es el freno que protege a la persona del
# otro lado: sin él, la landing sirve para bombardear a un número ajeno.
MAX_POR_DESTINO_POR_HORA = 5

DIGITOS = 6


def _sal():
    """La sal con la que se hashean códigos y orígenes.

    Sale del entorno. Sin ella el hash de un código de seis dígitos es
    trivialmente reversible con una tabla de un millón de entradas, y el
    hash de una IP también.
    """
    return os.environ.get("VERIFICACION_SAL", "").encode() or b"paneles-dev"


def _hash(valor):
    return hmac.new(_sal(), str(valor).encode(), hashlib.sha256).hexdigest()


def hash_origen(origen):
    """El origen del pedido (IP, por ejemplo), hasheado."""
    return _hash(f"origen:{origen}") if origen else None


def normalizar_destino(canal, destino):
    canal = (canal or "").strip().lower()
    if canal not in CANALES:
        raise DatosInvalidos(
            f"Canal de verificación desconocido: {canal!r}.",
            {"canales_validos": list(CANALES)})
    crudo = str(destino or "").strip()
    if not crudo:
        raise DatosInvalidos("Falta el contacto a verificar.")
    if canal == CELULAR:
        from . import preferencias

        normalizado = preferencias.normalizar_celular(crudo)
        if not normalizado:
            raise DatosInvalidos(
                "Ese celular no parece válido. Escribilo con el código de "
                "país o en formato local (por ejemplo, 099 123 456).")
        return canal, normalizado
    if "@" not in crudo or "." not in crudo.split("@")[-1]:
        raise DatosInvalidos("Ese correo no parece válido.")
    return canal, crudo.lower()


def _contar(conn, columna, valor, horas=1):
    fila = db.una(
        conn,
        f"""
        select count(*)::int as n from verificacion_contacto
         where {columna} = %s and creado_en > now() - make_interval(hours => %s)
        """,
        (valor, horas),
    )
    return fila["n"]


def pedir_codigo(conn, canal, destino, origen=None, enviar=None):
    """Emite un código y lo manda. Devuelve qué mostrar en la pantalla.

    **Nunca devuelve el código** cuando hay un proveedor de envío real: si lo
    devolviera, la verificación no verificaría nada —quien pide el código lo
    recibe en la misma respuesta, sin necesidad de tener acceso al contacto—.
    Sin proveedor configurado sí lo devuelve, y lo dice: es modo desarrollo.
    """
    canal, destino = normalizar_destino(canal, destino)
    origen_hash = hash_origen(origen)

    if origen_hash and _contar(conn, "origen_hash", origen_hash) >= MAX_POR_ORIGEN_POR_HORA:
        raise Conflicto(
            "Se pidieron demasiados códigos desde este dispositivo. Esperá un "
            "rato y volvé a intentar.",
            {"motivo": "tasa_por_origen"})
    if _contar(conn, "destino", destino) >= MAX_POR_DESTINO_POR_HORA:
        raise Conflicto(
            "Se pidieron demasiados códigos para este contacto. Esperá un "
            "rato y volvé a intentar.",
            {"motivo": "tasa_por_destino"})

    codigo = f"{secrets.randbelow(10 ** DIGITOS):0{DIGITOS}d}"
    # Los pendientes anteriores del mismo destino se queman: si no, quedan
    # varios códigos vivos a la vez y el más viejo sigue sirviendo.
    db.ejecutar(
        conn,
        "update verificacion_contacto set estado = 'vencido' "
        "where destino = %s and canal = %s and estado = 'pendiente'",
        (destino, canal))
    fila = db.una(
        conn,
        """
        insert into verificacion_contacto
               (canal, destino, codigo_hash, vence_en, origen_hash)
             values (%s, %s, %s, now() + make_interval(mins => %s), %s)
          returning id, vence_en
        """,
        (canal, destino, _hash(codigo), MINUTOS_DE_VIDA, origen_hash),
    )
    conn.commit()

    enviador = enviar or proveedor_de_envio()
    resultado = enviador(canal, destino, codigo)
    salida = {
        "verificacion_id": fila["id"],
        "canal": canal,
        "vence_en": fila["vence_en"].isoformat(),
        "minutos": MINUTOS_DE_VIDA,
        "enviado_por": resultado.get("proveedor"),
    }
    if resultado.get("sin_proveedor"):
        # Modo desarrollo: no hay a dónde mandarlo, así que vuelve en la
        # respuesta y se dice con todas las letras que eso no es verificar.
        salida["codigo_sin_enviar"] = codigo
        salida["aviso"] = (
            "No hay proveedor de envío configurado: el código vuelve en esta "
            "respuesta y la verificación no prueba nada. No usar así en "
            "producción.")
    return salida


def verificar(conn, canal, destino, codigo):
    """Comprueba el código. Devuelve el registro verificado.

    Un código verificado queda `verificado` y sirve una sola vez: lo consume
    la inscripción que lo usa (`consumir`).
    """
    canal, destino = normalizar_destino(canal, destino)
    fila = db.una(
        conn,
        """
        select id, codigo_hash, estado, intentos, vence_en
          from verificacion_contacto
         where canal = %s and destino = %s and estado = 'pendiente'
         order by creado_en desc
         limit 1
        """,
        (canal, destino),
    )
    if not fila:
        raise NoEncontrado(
            "No hay un código pendiente para ese contacto. Pedí uno nuevo.")

    if fila["vence_en"] <= _ahora(conn):
        db.ejecutar(conn, "update verificacion_contacto set estado = 'vencido' "
                          "where id = %s", (fila["id"],))
        conn.commit()
        raise Conflicto("El código venció. Pedí uno nuevo.",
                        {"motivo": "vencido"})

    if fila["intentos"] + 1 >= MAX_INTENTOS and not hmac.compare_digest(
            fila["codigo_hash"], _hash(str(codigo or "").strip())):
        db.ejecutar(conn, "update verificacion_contacto "
                          "set estado = 'vencido', intentos = intentos + 1 "
                          "where id = %s", (fila["id"],))
        conn.commit()
        raise Conflicto(
            "Se agotaron los intentos de ese código. Pedí uno nuevo.",
            {"motivo": "intentos_agotados"})

    if not hmac.compare_digest(fila["codigo_hash"], _hash(str(codigo or "").strip())):
        db.ejecutar(conn, "update verificacion_contacto set intentos = intentos + 1 "
                          "where id = %s", (fila["id"],))
        conn.commit()
        raise DatosInvalidos(
            "El código no coincide.",
            {"intentos_restantes": MAX_INTENTOS - fila["intentos"] - 1})

    db.ejecutar(
        conn,
        "update verificacion_contacto "
        "set estado = 'verificado', verificado_en = now() where id = %s",
        (fila["id"],))
    conn.commit()
    return {"verificacion_id": fila["id"], "canal": canal, "destino": destino,
            "estado": "verificado"}


def esta_verificado(conn, canal, destino):
    """¿Hay una verificación vigente para ese contacto?

    Vigente = verificada y todavía no consumida por otra inscripción.
    """
    try:
        canal, destino = normalizar_destino(canal, destino)
    except DatosInvalidos:
        return False
    return bool(db.una(
        conn,
        "select 1 from verificacion_contacto "
        "where canal = %s and destino = %s and estado = 'verificado'",
        (canal, destino)))


def consumir(conn, canal, destino):
    """Marca la verificación como usada por una inscripción.

    Un código verificado sirve para **una** inscripción. Sin consumirlo, el
    mismo código serviría para inscribir a diez personas distintas con el
    mismo contacto.
    """
    try:
        canal, destino = normalizar_destino(canal, destino)
    except DatosInvalidos:
        return False
    return bool(db.ejecutar(
        conn,
        "update verificacion_contacto set estado = 'usado' "
        "where id = (select id from verificacion_contacto "
        "             where canal = %s and destino = %s and estado = 'verificado' "
        "             order by verificado_en desc limit 1)",
        (canal, destino)))


def _ahora(conn):
    return db.una(conn, "select now() as ahora")["ahora"]


# ── El envío, detrás de una interfaz ─────────────────────────────────

def proveedor_de_envio(entorno=None):
    """Devuelve `enviar(canal, destino, codigo) -> {...}`.

    Mismo patrón que `embeddings` y `reranker`: el proveedor se elige por
    variable de entorno y, si no hay ninguno configurado, se degrada de forma
    **visible** en vez de fallar o de fingir que envió.
    """
    entorno = os.environ if entorno is None else entorno
    nombre = (entorno.get("VERIFICACION_ENVIO_PROVEEDOR") or "").strip().lower()

    if nombre in ("", "ninguno"):
        def sin_proveedor(canal, destino, codigo):
            return {"proveedor": "ninguno", "sin_proveedor": True}
        return sin_proveedor

    if nombre == "log":
        # Para desarrollo: queda en los logs del servidor, no en la respuesta.
        def por_log(canal, destino, codigo):
            print(f"[verificacion] código para {canal} {destino}: {codigo}")
            return {"proveedor": "log"}
        return por_log

    raise DatosInvalidos(
        f"Proveedor de envío de códigos desconocido: {nombre!r}.",
        {"proveedores": ["ninguno", "log"]})


def diagnostico(entorno=None):
    """Qué tan endurecida está la landing hoy. Va a la pantalla de
    Cumplimiento, para que la falta de proveedor no sea una sorpresa."""
    entorno = os.environ if entorno is None else entorno
    nombre = (entorno.get("VERIFICACION_ENVIO_PROVEEDOR") or "ninguno").strip().lower()
    hay_sal = bool(entorno.get("VERIFICACION_SAL"))
    return {
        "proveedor_envio": nombre,
        "envia_de_verdad": nombre not in ("", "ninguno"),
        "sal_configurada": hay_sal,
        "avisos": [a for a in [
            ("Sin proveedor de envío: el código vuelve en la respuesta y la "
             "verificación no prueba nada. La landing no se puede anunciar así.")
            if nombre in ("", "ninguno") else None,
            ("Sin `VERIFICACION_SAL`: los códigos y los orígenes se hashean con "
             "una sal de desarrollo, que es pública.") if not hay_sal else None,
        ] if a],
    }
