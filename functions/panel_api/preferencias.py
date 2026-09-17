"""R4.4 — Preferencias de canal de contacto.

**La regla de los dos ejes.** `contacto_participacion` responde *«¿puedo
contactarla?»*; la preferencia de canal responde *«¿por dónde?»*. Para enviar
por un canal hacen falta **los dos**: consentimiento de finalidad vigente y
preferencia de ese canal activa. Falta cualquiera, no se envía.

No es una distinción de abogado. El consentimiento que ya teníamos autoriza a
contactar, pero no dice por qué medio: alguien pudo aceptar que lo llamen por
teléfono y no querer mensajes en su WhatsApp personal. Y del lado de Meta, la
política de mensajería exige **opt-in previo** para los mensajes que inicia el
negocio; mandar sin él lleva a bloqueos y a la suspensión de la cuenta. El
canal se quema con el primer envío masivo a gente que no lo pidió.

`whatsapp` tiene una exigencia extra: **celular en E.164**. Sin un número en
formato internacional válido no hay a quién mandarle, así que la preferencia
no se puede activar. Eso obliga a normalizar el celular en los tres caminos de
alta, y esa normalización vive acá (`normalizar_celular`) para que los tres
usen exactamente la misma.
"""

import re

from . import consentimiento, db
from .errores import Conflicto, DatosInvalidos, NoEncontrado

WHATSAPP = "whatsapp"
EMAIL = "email"
TELEFONO = "telefono"
SMS = "sms"
CANALES = (WHATSAPP, EMAIL, TELEFONO, SMS)

ACTIVA = "activa"
REVOCADA = "revocada"

# Los canales que necesitan un celular utilizable. `telefono` también lo
# necesita, pero una llamada tolera un número mal escrito —alguien marca y se
# da cuenta—; un envío automático a un número inválido se pierde en silencio y
# cuenta contra la reputación del emisor.
EXIGEN_CELULAR = (WHATSAPP, SMS)

# Por dónde entró la preferencia. Los tres caminos de alta de Fase 3.
ORIGENES = ("alta_manual", "ingesta", "landing", "edicion")

# Uruguay. El país por defecto es una decisión de producto, no una constante
# universal: el panel es uruguayo y la enorme mayoría de los celulares vienen
# escritos en formato local («099 123 456»). Asumirlo acá evita que cada
# camino de alta tenga que pedir el prefijo.
PAIS_POR_DEFECTO = "598"


def normalizar_celular(crudo, pais=PAIS_POR_DEFECTO):
    """Deja el celular en E.164 (`+59899123456`), o `None` si no se puede.

    Devolver `None` en vez de levantar una excepción es deliberado: un celular
    mal escrito no puede voltear un alta —el resto de los datos de esa persona
    sirven igual—, pero sí tiene que impedir activar un canal que depende de
    él. Quien llama decide qué hacer con el `None`.
    """
    if not crudo:
        return None
    texto = str(crudo).strip()
    if not texto:
        return None

    # `00` es el prefijo internacional de marcado en buena parte del mundo y
    # equivale a `+`. Se traduce antes de limpiar, porque después no se
    # distingue de los ceros del número.
    if texto.startswith("00"):
        texto = "+" + texto[2:]
    tenia_mas = texto.startswith("+")
    digitos = re.sub(r"\D", "", texto)
    if not digitos:
        return None

    if tenia_mas:
        numero = digitos
    elif digitos.startswith(pais) and len(digitos) > len(pais):
        # Vino con el código de país pero sin el `+`.
        numero = digitos
    else:
        # Número local: se le saca el cero de larga distancia nacional y se le
        # pone el país. «099 123 456» → «59899123456».
        numero = pais + digitos.lstrip("0")

    # E.164 admite hasta 15 dígitos, y menos de 8 no es un número de nadie.
    if not 8 <= len(numero) <= 15:
        return None
    return "+" + numero


def _validar_canal(canal):
    canal = (canal or "").strip().lower()
    if canal not in CANALES:
        raise DatosInvalidos(
            f"Canal desconocido: {canal!r}.", {"canales_validos": list(CANALES)})
    return canal


def _serializar(fila):
    return {
        "canal": fila["canal"],
        "estado": fila["estado"],
        "activa": fila["estado"] == ACTIVA,
        "version_texto": fila["version_texto"],
        "origen": fila["origen"],
        "otorgado_en": fila["otorgado_en"].isoformat(),
        "revocado_en": fila["revocado_en"].isoformat() if fila["revocado_en"] else None,
    }


# ── Lectura ──────────────────────────────────────────────────────────

def listar(conn, id_persona, solo_activas=False):
    filas = db.todas(
        conn,
        """
        select canal, estado, version_texto, origen, otorgado_en, revocado_en
          from preferencia_canal
         where id_persona = %s and (%s::bool is not true or estado = 'activa')
         order by canal
        """,
        (id_persona, solo_activas),
    )
    return [_serializar(f) for f in filas]


def acepta(conn, id_persona, canal):
    """¿Tiene preferencia activa para ese canal? Uno de los dos ejes."""
    canal = _validar_canal(canal)
    return bool(db.una(
        conn,
        "select 1 from preferencia_canal "
        "where id_persona = %s and canal = %s and estado = 'activa'",
        (id_persona, canal)))


def filtrar_contactables(conn, ids_persona, canal,
                         finalidad=consentimiento.CONTACTO):
    """**La regla de los dos ejes**, aplicada a un conjunto.

    Devuelve `(contactables, excluidos_por_motivo)`. Los motivos van
    discriminados porque se arreglan distinto: a quien le falta el
    consentimiento hay que pedírselo, a quien le falta la preferencia hay que
    ofrecerle el canal, y a quien le falta el celular hay que cargárselo.
    """
    canal = _validar_canal(canal)
    ids = [str(i) for i in dict.fromkeys(ids_persona or [])]
    if not ids:
        return [], {}

    filas = db.todas(
        conn,
        """
        select p.id_persona,
               p.celular,
               exists (select 1 from consentimiento c
                        where c.id_persona = p.id_persona
                          and c.finalidad = %s and c.estado = 'vigente')
                                                        as tiene_finalidad,
               exists (select 1 from preferencia_canal pc
                        where pc.id_persona = p.id_persona
                          and pc.canal = %s and pc.estado = 'activa')
                                                        as tiene_preferencia
          from persona p
         where p.id_persona = any(%s::uuid[])
        """,
        (finalidad, canal, ids),
    )

    contactables, excluidos = [], {}
    def excluir(id_persona, motivo):
        excluidos.setdefault(motivo, []).append(str(id_persona))

    encontrados = set()
    for fila in filas:
        id_persona = str(fila["id_persona"])
        encontrados.add(id_persona)
        if not fila["tiene_finalidad"]:
            excluir(id_persona, "sin_consentimiento")
            continue
        if not fila["tiene_preferencia"]:
            excluir(id_persona, f"sin_preferencia_{canal}")
            continue
        if canal in EXIGEN_CELULAR:
            if not fila["celular"]:
                excluir(id_persona, "sin_celular")
                continue
            if not normalizar_celular(fila["celular"]):
                excluir(id_persona, "celular_invalido")
                continue
        contactables.append(id_persona)

    for id_persona in ids:
        if id_persona not in encontrados:
            excluir(id_persona, "no_existe")
    return contactables, excluidos


# ── Escritura ────────────────────────────────────────────────────────

def otorgar(conn, id_persona, canal, version_texto=None, origen="edicion"):
    """Registra que esta persona acepta ese canal.

    Si ya existía revocada, se reactiva con la fecha y el texto nuevos: es la
    misma persona diciendo que sí de nuevo, y lo que importa conservar es con
    qué texto lo dijo esta vez.
    """
    canal = _validar_canal(canal)
    if not db.una(conn, "select 1 from persona where id_persona = %s", (id_persona,)):
        raise NoEncontrado(f"No existe la persona {id_persona}.")

    if canal in EXIGEN_CELULAR:
        fila = db.una(
            conn, "select celular from persona where id_persona = %s", (id_persona,))
        if not normalizar_celular(fila["celular"]):
            raise Conflicto(
                f"Para activar «{canal}» hace falta un celular en formato "
                f"internacional válido. El que tiene cargado es "
                f"{fila['celular']!r}.",
                {"canal": canal, "celular": fila["celular"]})

    db.ejecutar(
        conn,
        """
        insert into preferencia_canal
               (id_persona, canal, estado, version_texto, origen)
             values (%s, %s, 'activa', %s, %s)
        on conflict (id_persona, canal) do update
               set estado = 'activa',
                   version_texto = excluded.version_texto,
                   origen = excluded.origen,
                   otorgado_en = now(),
                   revocado_en = null
        """,
        (id_persona, canal, version_texto, origen),
    )
    return _una(conn, id_persona, canal)


def revocar(conn, id_persona, canal):
    """Deja de ser elegible para ese canal, **sin afectar los otros**."""
    canal = _validar_canal(canal)
    n = db.ejecutar(
        conn,
        "update preferencia_canal set estado = 'revocada', revocado_en = now() "
        "where id_persona = %s and canal = %s and estado = 'activa'",
        (id_persona, canal))
    if not n and not _una(conn, id_persona, canal):
        raise NoEncontrado(
            f"La persona no tiene preferencia registrada para «{canal}».")
    return _una(conn, id_persona, canal)


def _una(conn, id_persona, canal):
    fila = db.una(
        conn,
        "select canal, estado, version_texto, origen, otorgado_en, revocado_en "
        "from preferencia_canal where id_persona = %s and canal = %s",
        (id_persona, canal))
    return _serializar(fila) if fila else None


def registrar_varias(conn, id_persona, canales, version_texto=None,
                     origen="alta_manual"):
    """Los canales que trae un alta, en cualquiera de los tres caminos.

    Devuelve `{"activadas": [...], "rechazadas": [{canal, motivo}]}`. Un canal
    que no se pudo activar —típicamente WhatsApp sin celular válido— **no
    voltea el alta**: la persona se crea igual y el resultado lo informa. Al
    revés, un celular mal tipeado impediría enrolar a alguien, que es peor.
    """
    activadas, rechazadas = [], []
    for canal in dict.fromkeys(canales or []):
        try:
            otorgar(conn, id_persona, canal, version_texto=version_texto,
                    origen=origen)
            activadas.append(_validar_canal(canal))
        except (Conflicto, DatosInvalidos) as error:
            rechazadas.append({"canal": str(canal), "motivo": error.mensaje})
    return {"activadas": activadas, "rechazadas": rechazadas}
