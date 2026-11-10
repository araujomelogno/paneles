"""R-CAN — Los canales aceptados, declarados para una carga entera (D88).

Para recibir un mensaje una persona necesita dos cosas distintas (R4.4): el
**consentimiento de contacto** —por finalidad— y la **preferencia de canal**
—WhatsApp, correo—. La carga registraba el consentimiento y nunca el canal: la
única forma de registrarlo era de a una persona, desde su ficha, o que el
panelista lo aceptara en la landing o el portal. Resultado: nadie que hubiera
entrado por una carga podía recibir mensajes.

Este módulo deja declarar los canales para toda la gente de una carga, al
cargarla (R-CAN.1, por la vía diferida, lote por lote) o después, desde el
detalle de una carga ya hecha (R-CAN.2). Lo que no hay que deshacer:

**Sin texto no se registra.** Cada canal declarado lleva la versión del texto
con que lo aceptaron, elegida de los textos publicados (como el
consentimiento, R7.1). «Aceptó recibir WhatsApp» sin respaldo es una
afirmación vacía. Si ese texto alcanza como aceptación del canal es una
lectura de protección de datos, no técnica: por eso se exige elegirlo y queda
auditado (`carga_canal_registro`).

**Un canal revocado no se reactiva en bloque (R-CAN.3).** `preferencias.
otorgar` reactiva un canal revocado —«es la misma persona diciendo que sí de
nuevo»—, y eso es correcto cuando la persona lo acepta en la landing, el
portal o su ficha. En una declaración en bloque no: quien pidió no recibir más
WhatsApp volvería a recibirlo porque una carga vieja decía que sí. Acá se
saltea y se informa como «revocó el canal».

**Quien ya lo tenía aceptado no se toca**: su evidencia (la landing, el
portal, otra carga) se conserva tal cual.

**Se registra por `preferencias.otorgar`**, con `origen = 'carga'`. No hay un
segundo camino de escritura de `preferencia_canal`.
"""

import json

from . import consentimiento, db, preferencias
from .errores import DatosInvalidos

# Los canales que una carga puede declarar. Teléfono y SMS no: ninguno de los
# dos se usa para enviar desde la plataforma.
CANALES = (preferencias.WHATSAPP, preferencias.EMAIL)
ETIQUETAS = {preferencias.WHATSAPP: "WhatsApp", preferencias.EMAIL: "Correo"}

# Los textos que se ofrecen para cada canal son los de la finalidad de
# contacto: es el texto que habla de contactar (el mismo que usan el alta
# manual y la landing para los canales).
FINALIDAD_DE_LOS_TEXTOS = consentimiento.CONTACTO

ORIGEN = "carga"
VIA_INGESTA = "ingesta"
VIA_BLOQUE = "bloque"

# Por qué alguien no queda con el canal.
SIN_CELULAR_VALIDO = "sin_celular_valido"
SIN_CORREO = "sin_correo"
REVOCADO = "revoco_el_canal"
MOTIVOS = {
    SIN_CELULAR_VALIDO: "Sin celular válido",
    SIN_CORREO: "Sin correo",
    REVOCADO: "Revocó el canal",
}


def normalizar(conn, crudo):
    """`{canal: version_texto}` validado. Acepta `{canal: "versión"}` o
    `{canal: {"version_texto": "versión"}}`. Vacío o None: nada declarado.

    Cada versión tiene que ser un texto **publicado y activo** de la
    finalidad de contacto: es lo que el desplegable ofrece, y lo que hace
    demostrable la aceptación."""
    if not crudo:
        return {}
    if not isinstance(crudo, dict):
        raise DatosInvalidos(
            "Los canales aceptados son un objeto «canal → versión del texto».",
            {"ejemplo": {"whatsapp": "contacto-2026-09"}})
    salida = {}
    for canal, regla in crudo.items():
        canal = str(canal or "").strip().lower()
        if canal not in CANALES:
            raise DatosInvalidos(f"Canal desconocido: {canal!r}.",
                                 {"canales": list(CANALES)})
        version = regla.get("version_texto") if isinstance(regla, dict) else regla
        version = str(version or "").strip()
        if not version:
            raise DatosInvalidos(
                f"Falta la versión del texto con que aceptaron {ETIQUETAS[canal]}: sin "
                f"texto no se registra, porque «aceptó» sin respaldo no es demostrable.",
                {"canal": canal, "motivo": "sin_texto"})
        salida[canal] = version
    publicadas = {f["version"] for f in db.todas(
        conn, """select version from texto_consentimiento
                  where finalidad = %s and activo and version = any(%s)""",
        (FINALIDAD_DE_LOS_TEXTOS, list(set(salida.values()))))}
    for canal, version in salida.items():
        if version not in publicadas:
            raise DatosInvalidos(
                f"La versión «{version}» no es un texto publicado y activo de "
                f"{FINALIDAD_DE_LOS_TEXTOS}. Se publica en Inscripciones → Textos de "
                f"consentimiento.", {"canal": canal, "motivo": "texto_no_publicado"})
    return salida


def _estado_de(conn, ids):
    """Lo que hace falta para decidir, por persona: su celular, su correo y
    el estado de cada preferencia de canal que tenga."""
    if not ids:
        return {}
    filas = db.todas(
        conn,
        """select p.id_persona::text as id_persona, p.celular, p.email,
                  coalesce(jsonb_object_agg(pc.canal, pc.estado)
                           filter (where pc.canal is not null), '{}'::jsonb) as canales
             from persona p
             left join preferencia_canal pc on pc.id_persona = p.id_persona
            where p.id_persona = any(%s::uuid[])
            group by p.id_persona, p.celular, p.email""",
        ([str(i) for i in ids],))
    return {f["id_persona"]: f for f in filas}


def _tiene_dato(canal, celular, email):
    if canal == preferencias.WHATSAPP:
        return bool(preferencias.normalizar_celular(celular))
    return "@" in (email or "")


def clasificar(conn, ids, declaracion):
    """`{canal: {"registrar": [...], "ya_lo_tenia": [...], "no": {motivo:
    [...]}}}` para las personas `ids`. No escribe nada: es lo que comparten
    la vista previa y el registro."""
    estado = _estado_de(conn, list(dict.fromkeys(str(i) for i in ids)))
    salida = {}
    for canal in declaracion:
        registrar, ya, no = [], [], {}
        for id_persona, fila in estado.items():
            preferencia = (fila["canales"] or {}).get(canal)
            if preferencia == preferencias.ACTIVA:
                ya.append(id_persona)
            elif preferencia == preferencias.REVOCADA:
                # R-CAN.3 — no se reactiva en bloque.
                no.setdefault(REVOCADO, []).append(id_persona)
            elif not _tiene_dato(canal, fila["celular"], fila["email"]):
                no.setdefault(SIN_CELULAR_VALIDO if canal == preferencias.WHATSAPP
                              else SIN_CORREO, []).append(id_persona)
            else:
                registrar.append(id_persona)
        salida[canal] = {"registrar": registrar, "ya_lo_tenia": ya, "no": no}
    return salida


def _conteos(clasificacion, declaracion):
    """La forma que ven la pantalla y la auditoría: números, sin personas."""
    return {canal: {
        "etiqueta": ETIQUETAS[canal],
        "version_texto": declaracion[canal],
        "quedaran": len(c["registrar"]) + len(c["ya_lo_tenia"]),
        "registrados": len(c["registrar"]),
        "ya_lo_tenian": len(c["ya_lo_tenia"]),
        "no": {motivo: len(v) for motivo, v in c["no"].items()},
        "no_total": sum(len(v) for v in c["no"].values()),
    } for canal, c in clasificacion.items()}


def personas_de_la_carga(conn, carga_id):
    """Todas las personas de la carga, creadas o reutilizadas (R-ORG.1)."""
    return [str(f["id_persona"]) for f in db.todas(
        conn, "select id_persona from persona_carga where carga_id = %s", (carga_id,))]


def aplicar(conn, carga_id, ids, declaracion, via, actor=None, trabajo_id=None):
    """Registra los canales declarados para esas personas y deja la fila de
    auditoría. Devuelve los conteos por canal. `declaracion` ya normalizada.

    Por `preferencias.otorgar`, con `origen = 'carga'` y la versión del texto.
    Quien ya lo tenía no se toca; quien lo revocó, tampoco (R-CAN.3)."""
    if not declaracion:
        return {}
    clasificacion = clasificar(conn, ids, declaracion)
    for canal, grupo in clasificacion.items():
        for id_persona in grupo["registrar"]:
            preferencias.otorgar(conn, id_persona, canal,
                                 version_texto=declaracion[canal], origen=ORIGEN)
    conteos = _conteos(clasificacion, declaracion)
    db.ejecutar(
        conn,
        """insert into carga_canal_registro
                  (carga_id, via, canales, personas, resultado, trabajo_id,
                   actor_uid, actor_email)
           values (%s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s)""",
        (carga_id, via, json.dumps(declaracion), len(set(ids)),
         json.dumps(conteos, ensure_ascii=False), trabajo_id,
         getattr(actor, "uid", None) if not isinstance(actor, str) else None,
         actor if isinstance(actor, str) else getattr(actor, "email", None)))
    return conteos


def registrar_en_bloque(conn, carga_id, crudo, actor=None, solo_revisar=False):
    """R-CAN.2 — los canales aceptados para todas las personas de una carga
    ya hecha. Con `solo_revisar`, la vista previa —de la misma función que
    registra—: cuántas quedan con cada canal y cuántas no, por motivo."""
    from . import cargas

    cargas.obtener(conn, carga_id)
    declaracion = normalizar(conn, crudo)
    if not declaracion:
        raise DatosInvalidos("Elegí al menos un canal, con la versión del texto con "
                             "que lo aceptaron.")
    ids = personas_de_la_carga(conn, carga_id)
    salida = {"carga_id": carga_id, "personas": len(ids), "motivos": MOTIVOS,
              "solo_revisar": bool(solo_revisar)}
    if solo_revisar:
        salida["canales"] = _conteos(clasificar(conn, ids, declaracion), declaracion)
        return salida
    salida["canales"] = aplicar(conn, carga_id, ids, declaracion, VIA_BLOQUE, actor=actor)
    return salida


def previa_de_archivo(conn, encuesta_id, filas, columna_id, tipo, origen,
                      declaracion, demograficas=None, crea_personas=False):
    """R-CAN.1 — la vista previa de la revisión de una carga: con los canales
    declarados, cuántas personas del archivo quedarían con cada uno y cuántas
    no, por motivo. Las que ya existen se miran en la base (su celular, su
    correo, si revocaron); las que se van a crear, en las columnas del archivo
    marcadas como celular y correo."""
    from . import cargas, encuestas, ingesta as mod_ingesta, sav

    if not declaracion:
        return None
    ids = cargas._ids_de(filas, columna_id)
    mapa, _ = encuestas.resolver_identidades(
        conn, encuesta_id, tipo or mod_ingesta.POR_ALIAS, ids, origen)
    existentes = list(dict.fromkeys(str(v) for v in mapa.values()))
    conteos = _conteos(clasificar(conn, existentes, declaracion), declaracion)
    if crea_personas:
        por_campo = sav.mapeo_por_campo(demograficas or {})
        nuevas = [f for f in filas or []
                  if str(f.get(columna_id) or "").strip() not in mapa]
        for canal, c in conteos.items():
            columna = por_campo.get("celular" if canal == preferencias.WHATSAPP else "email")
            con_dato = sum(1 for f in nuevas
                           if columna and _tiene_dato(canal, f.get(columna), f.get(columna)))
            sin_dato = len(nuevas) - con_dato
            c["registrados"] += con_dato
            c["quedaran"] += con_dato
            if sin_dato:
                motivo = SIN_CELULAR_VALIDO if canal == preferencias.WHATSAPP else SIN_CORREO
                c["no"][motivo] = c["no"].get(motivo, 0) + sin_dato
                c["no_total"] += sin_dato
    return {"canales": conteos, "motivos": MOTIVOS,
            "personas": len(existentes) + (len([f for f in filas or []
                                                 if str(f.get(columna_id) or "").strip()
                                                 not in mapa]) if crea_personas else 0)}


def historial(conn, carga_id):
    """Las declaraciones de canales de la carga, la más reciente primero:
    quién, cuándo, por qué vía, qué canales con qué texto y a cuántas."""
    return [{
        "id": f["id"], "via": f["via"], "canales": f["canales"],
        "personas": f["personas"], "resultado": f["resultado"],
        "actor": f["actor_email"] or f["actor_uid"] or "sistema",
        "creado_en": f["creado_en"].isoformat(),
    } for f in db.todas(
        conn, """select * from carga_canal_registro where carga_id = %s
                  order by creado_en desc, id desc""", (carga_id,))]


def textos_disponibles(conn):
    """Los textos publicados y activos que se ofrecen para declarar un canal."""
    return [{"version": f["version"], "cuerpo": f["cuerpo"],
             "creado_en": f["creado_en"].isoformat()}
            for f in db.todas(
                conn, """select version, cuerpo, creado_en from texto_consentimiento
                          where finalidad = %s and activo
                          order by creado_en desc, id desc""",
                (FINALIDAD_DE_LOS_TEXTOS,))]
