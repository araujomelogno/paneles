"""Fase 6 — El portal del panelista.

Todo lo que el panelista podía hacer con sus propios datos pasaba por una
persona de Equipos: preguntar cuántos puntos tenía, avisar que cambió de
celular, pedir que dejen de mandarle WhatsApp, irse del panel. Eso tiene tres
costos —el dato de contacto se pudre, los derechos del titular se ejercen a
mano, y la gamificación no puede funcionar sin visibilidad— y esta fase los
ataca con una puerta propia.

**No es «una pantalla de puntos»: es la interfaz de derechos del titular.**
Los puntos son lo que hace que la gente entre.

── Lo que cambia de postura, y por qué está todo en un módulo aparte ──

Hasta la Fase 5 la bóveda la tocaban empleados de Equipos y un segundo
sistema registrado. Desde acá autentica a **miles de externos** contra el
store que tiene toda la PII. Un portal que viviera repartido entre los
módulos existentes heredaría, sin quererlo, las suposiciones de una API
interna: que el llamador es de confianza, que puede nombrar a cualquier
persona, que si pide algo raro es un error y no un ataque.

Por eso todo lo del portal entra por acá, y acá rigen tres reglas que no
rigen en el resto de la API:

1. **El `id_persona` no se recibe nunca.** Sale de `cuenta_panelista`, a
   partir del `uid` del token. Ninguna función de este módulo acepta un
   identificador de persona del cliente.
2. **Lo editable es una lista blanca.** Un atributo se puede tocar solo si
   alguien lo marcó editable; lo demás se rechaza aunque venga forzado.
3. **La respuesta no revela.** Pedir acceso contesta lo mismo exista o no el
   correo, porque la diferencia alcanzaría para averiguar quién integra el
   panel probando direcciones.
"""

import secrets as _secrets

from . import (
    atributos, bajas, consentimiento, db, personas, preferencias, premios,
    puntos, verificacion_contacto as verif,
)
from .errores import Conflicto, DatosInvalidos, NoAutenticado, NoEncontrado, SinPermiso

# ── R6.1 · Parámetros del acceso ─────────────────────────────────────

# Cuánto vive un enlace. Quince minutos: alcanza para ir al correo y volver,
# y no tanto como para que un enlace reenviado por error sirva al otro día.
MINUTOS_DEL_ENLACE = 15

# Cuántos enlaces se pueden pedir para el mismo correo, y desde el mismo
# origen, en una hora. El segundo es el que frena la enumeración: sin él,
# probar direcciones ajenas para ver cuál existe saldría gratis.
MAX_POR_CORREO_POR_HORA = 5
MAX_POR_ORIGEN_POR_HORA = 15

# Lo que se contesta siempre, exista o no el correo. Es una sola constante a
# propósito: dos mensajes parecidos escritos en dos lugares terminan
# divergiendo, y la diferencia **es** la filtración.
RESPUESTA_DE_ACCESO = (
    "Si esa dirección corresponde a un panelista, va a recibir un enlace "
    "para entrar. Revisá tu correo."
)


def _hash(valor):
    """El mismo hashing con sal que la verificación de R4.3."""
    return verif._hash(valor)


def _normalizar_email(crudo):
    texto = str(crudo or "").strip().lower()
    if not texto or "@" not in texto or "." not in texto.split("@")[-1]:
        raise DatosInvalidos("Ese correo no parece válido.")
    return texto


def _contar_accesos(conn, columna, valor, horas=1):
    fila = db.una(
        conn,
        f"""select count(*)::int as n from acceso_portal
             where {columna} = %s and creado_en > now() - make_interval(hours => %s)""",
        (valor, horas))
    return fila["n"] if fila else 0


def _panelista_por_email(conn, email):
    """La persona activa que tiene ese correo, o `None`.

    «Activa» importa: alguien dado de baja no vuelve a entrar, y su fila ya
    no existe —la baja borra la PII—, así que esto no la encuentra. Se filtra
    igual por estado para el caso de una persona que todavía existe pero no
    está activa.
    """
    return db.una(
        conn,
        "select id_persona, email, estado from persona "
        " where lower(email) = %s and estado = 'activa'",
        (email,))


def pedir_acceso(conn, email, origen=None, enviar=None, generar_enlace=None):
    """R6.1 — emite un enlace de acceso y lo manda. Contesta siempre igual.

    `generar_enlace` existe para poder probar esto sin Firebase y para que el
    día que se cambie de proveedor de enlaces no haya que tocar la regla de
    no-revelación, que es la parte delicada.
    """
    email = _normalizar_email(email)
    origen_hash = verif.hash_origen(origen)

    # Los dos límites se aplican **antes** de mirar si el correo existe: si
    # se aplicaran después, el tiempo de respuesta ya diría algo.
    if origen_hash and _contar_accesos(conn, "origen_hash", origen_hash) >= MAX_POR_ORIGEN_POR_HORA:
        raise Conflicto(
            "Se pidieron demasiados enlaces desde este dispositivo. Esperá un "
            "rato y volvé a intentar.", {"motivo": "tasa_por_origen"})
    if _contar_accesos(conn, "email_hash", _hash(email)) >= MAX_POR_CORREO_POR_HORA:
        # Ojo: este rechazo sí distingue un correo de otro, pero solo entre
        # correos que **alguien ya pidió cinco veces en una hora**, que es
        # información sobre el pedido y no sobre el padrón.
        raise Conflicto(
            "Se pidieron demasiados enlaces para esa dirección. Esperá un "
            "rato y volvé a intentar.", {"motivo": "tasa_por_correo"})

    persona = _panelista_por_email(conn, email)
    token = _secrets.token_urlsafe(32) if persona else None

    db.ejecutar(
        conn,
        """insert into acceso_portal
                  (email_hash, id_persona, token_hash, vence_en, origen_hash)
           values (%s, %s, %s,
                   case when %s::text is null then null
                        else now() + make_interval(mins => %s) end,
                   %s)""",
        (_hash(email), persona["id_persona"] if persona else None,
         _hash(token) if token else None, token, MINUTOS_DEL_ENLACE,
         origen_hash))

    salida = {"mensaje": RESPUESTA_DE_ACCESO, "minutos": MINUTOS_DEL_ENLACE}
    if not persona:
        # Nada que mandar. Y la respuesta es la misma: ésa es la regla.
        return salida

    enlace = (generar_enlace or _enlace_de_firebase)(email, token)
    resultado = (enviar or verif.proveedor_de_envio())("email", email, enlace)
    if resultado.get("sin_proveedor"):
        # Modo desarrollo, dicho con todas las letras igual que en R4.3: sin
        # proveedor el enlace vuelve en la respuesta y entonces no prueba que
        # quien pide tenga acceso al correo.
        salida["enlace_sin_enviar"] = enlace
        salida["aviso"] = (
            "No hay proveedor de envío configurado: el enlace vuelve en esta "
            "respuesta y el acceso no prueba nada. No usar así en producción.")
    return salida


def _enlace_de_firebase(email, token):
    """El enlace de ingreso, emitido por Firebase Auth.

    Se usa Firebase y no una sesión propia por una razón de superficie: una
    sesión propia significaría emitir, guardar y revocar credenciales de
    miles de externos, que es exactamente el tipo de cosa que conviene no
    escribir cuando el proyecto ya tiene un proveedor de identidad.

    El `token` nuestro viaja como parámetro y es lo que hace al enlace de un
    solo uso: Firebase no lo garantiza por sí mismo.
    """
    import os

    base = os.environ.get("PORTAL_URL", "").rstrip("/")
    if not base:
        raise DatosInvalidos(
            "Falta `PORTAL_URL`: sin la dirección del portal no se puede "
            "armar el enlace de acceso.")
    try:
        from firebase_admin import auth as fb_auth

        destino = f"{base}/entrar?t={token}"
        ajustes = fb_auth.ActionCodeSettings(url=destino, handle_code_in_app=True)
        return fb_auth.generate_sign_in_with_email_link(email, ajustes)
    except ImportError:
        return f"{base}/entrar?t={token}"


def canjear_enlace(conn, token):
    """Consume el enlace y devuelve a quién corresponde.

    Un enlace sirve **una sola vez**: se marca usado en la misma sentencia
    que lo busca, así que dos pedidos simultáneos con el mismo token no
    pueden ganar los dos.
    """
    if not token:
        raise NoAutenticado("Falta el enlace de acceso.")
    fila = db.una(
        conn,
        """update acceso_portal
              set usado_en = now()
            where token_hash = %s and usado_en is null and vence_en > now()
        returning id_persona""",
        (_hash(token),))
    if not fila or not fila["id_persona"]:
        raise NoAutenticado(
            "Ese enlace no sirve: puede estar vencido o ya haber sido usado. "
            "Pedí uno nuevo.")
    return fila["id_persona"]


# ── R6.2 · El vínculo entre la cuenta y la persona ───────────────────

def vincular(conn, uid, email, token=None):
    """Ata la cuenta autenticada a su persona, y devuelve el vínculo.

    En el primer acceso el vínculo se establece **solo si el correo
    autenticado coincide** con el de la bóveda. Después, el `uid` manda: si
    la persona cambia de correo desde el portal, la cuenta sigue siendo la
    misma.
    """
    if not uid:
        raise NoAutenticado("Falta la cuenta autenticada.")
    email = _normalizar_email(email)

    ya = db.una(conn,
                "select uid, id_persona from cuenta_panelista where uid = %s",
                (uid,))
    if ya:
        db.ejecutar(conn,
                    "update cuenta_panelista set ultimo_acceso_en = now() "
                    " where uid = %s", (uid,))
        return {"uid": uid, "id_persona": ya["id_persona"], "nueva": False}

    # Primer acceso. El token del enlace, si vino, es lo que prueba que quien
    # entra recibió el correo; el correo autenticado tiene que coincidir con
    # el de la persona a la que ese enlace correspondía.
    id_persona = canjear_enlace(conn, token) if token else None
    persona = _panelista_por_email(conn, email)
    if not persona or (id_persona and persona["id_persona"] != id_persona):
        raise SinPermiso(
            "Esa dirección no corresponde a ningún panelista activo. Si "
            "querés sumarte al panel, podés inscribirte desde el formulario "
            "público.")

    existente = db.una(
        conn, "select uid from cuenta_panelista where id_persona = %s",
        (persona["id_persona"],))
    if existente:
        # Una persona, una cuenta. Si pudiera tener dos, revocar el acceso
        # dejaría la otra puerta abierta.
        raise Conflicto(
            "Esa persona ya tiene una cuenta del portal vinculada. Si "
            "perdiste el acceso, escribinos.",
            {"motivo": "persona_ya_vinculada"})

    db.ejecutar(
        conn,
        """insert into cuenta_panelista (uid, id_persona, email, ultimo_acceso_en)
           values (%s, %s, %s, now())""",
        (uid, persona["id_persona"], email))
    return {"uid": uid, "id_persona": persona["id_persona"], "nueva": True}


def persona_de(conn, uid):
    """El `id_persona` de la sesión. **La única fuente.**

    Ninguna ruta del portal acepta un `id_persona` del cliente: si lo
    aceptara, la autorización dependería de que todas las rutas se acuerden
    de comprobarlo, y alcanza con que una se olvide.
    """
    fila = db.una(
        conn,
        """select c.id_persona, p.estado
             from cuenta_panelista c
             join persona p on p.id_persona = c.id_persona
            where c.uid = %s""",
        (uid,))
    if not fila:
        raise SinPermiso(
            "Esta cuenta no está vinculada a ningún panelista.",
            {"motivo": "sin_vinculo"})
    if fila["estado"] != "activa":
        raise SinPermiso(
            "Esta cuenta ya no tiene acceso al portal.",
            {"motivo": "no_activa"})
    return fila["id_persona"]


# ── R6.3 · Saldo y movimientos ───────────────────────────────────────

# Cómo se le explica a una persona de dónde salieron sus puntos. El ledger
# guarda códigos; «participacion» no es una explicación.
MOTIVOS = {
    puntos.EARN: "Por responder una encuesta",
    puntos.CANJE: "Canje de un premio",
    puntos.AJUSTE: "Ajuste",
    puntos.VENCIMIENTO: "Puntos vencidos",
}


def _motivo_legible(movimiento):
    base = MOTIVOS.get(movimiento.get("tipo"), movimiento.get("tipo") or "Movimiento")
    encuesta = movimiento.get("encuesta")
    if movimiento.get("tipo") == puntos.EARN and encuesta:
        return f"{base}: {encuesta}"
    motivo = (movimiento.get("motivo") or "").strip()
    # El motivo interno se muestra solo cuando agrega algo que el tipo no
    # dice. «canje · Voucher» sí; «participacion» otra vez, no.
    #
    # El criterio es la forma, no una lista: un motivo de una sola palabra
    # en minúsculas y sin espacios es un código que alguien escribió para
    # otro programa. Una lista de códigos a excluir se desactualizaría con
    # el primero que se agregue, y volvería a mostrarle «participacion» a
    # una persona.
    import re as _re

    es_codigo = bool(_re.fullmatch(r"[a-z0-9_]+", motivo))
    if motivo and not es_codigo and motivo.lower() != base.lower():
        return f"{base} · {motivo}"
    return base


def resumen_de_puntos(conn, id_persona):
    """R6.3 — el saldo y de dónde salió, en términos de quien lo lee."""
    movimientos = puntos.movimientos(conn, id_persona)
    return {
        # Siempre desde el ledger: no hay un campo acumulado que pueda
        # desincronizarse, y ésa es la razón de que no exista.
        "saldo": puntos.saldo(conn, id_persona),
        "movimientos": [
            {
                "fecha": m.get("creado_en"),
                "puntos": m.get("puntos"),
                "motivo": _motivo_legible(m),
                "vence_en": m.get("vence_en"),
                "vencido": m.get("vencido"),
            }
            for m in movimientos
        ],
    }


# ── R6.4 · Canje ─────────────────────────────────────────────────────

def catalogo(conn, id_persona):
    """Los premios activos, con si están al alcance del saldo."""
    saldo = puntos.saldo(conn, id_persona)
    disponibles = []
    for premio in premios.listar_premios(conn, solo_disponibles=True):
        disponibles.append({
            **premio,
            "alcanza": saldo >= premio["costo_puntos"],
            "faltan": max(0, premio["costo_puntos"] - saldo),
        })
    return {"saldo": saldo, "premios": disponibles}


def solicitar_canje(conn, id_persona, premio_id):
    """R6.4 — el portal **solicita**; aprobar y entregar los hace Equipos.

    La reserva de puntos y el bloqueo contra el sobregiro concurrente son
    los de R3.5: `premios.canjear()` ya bloquea la persona y el premio en ese
    orden. No se reimplementa acá —dos implementaciones del mismo saldo es
    cómo se llega a un saldo negativo—.
    """
    return premios.canjear(conn, id_persona, premio_id)


def mis_canjes(conn, id_persona):
    filas = db.todas(
        conn,
        """select id, premio, costo_puntos, estado, estado_texto,
                  creado_en, aprobado_en, resuelto_en, nota
             from v_canje_panelista
            where id_persona = %s
            order by creado_en desc, id desc""",
        (str(id_persona),))
    return [
        {**f,
         "creado_en": f["creado_en"].isoformat() if f["creado_en"] else None,
         "aprobado_en": f["aprobado_en"].isoformat() if f["aprobado_en"] else None,
         "resuelto_en": f["resuelto_en"].isoformat() if f["resuelto_en"] else None}
        for f in filas
    ]


# ── R6.5 · Atributos editables ───────────────────────────────────────

def atributos_editables(conn):
    """Los que el administrador marcó editables. **Lista blanca.**"""
    return [
        a for a in atributos.listar(conn, solo_activos=True)
        if a.get("editable_por_panelista") and a["tipo"] != "derivado"
    ]


def perfil(conn, id_persona):
    """Lo que el panelista ve de sí mismo.

    Deliberadamente **no** incluye a qué estudios fue convocado ni qué
    respondió: lo primero porque no es asunto suyo saber la muestra, y lo
    segundo porque ver lo que respondió antes condiciona lo que responde
    ahora.
    """
    persona = db.una(
        conn,
        "select nombre, email, celular from persona where id_persona = %s",
        (str(id_persona),))
    if not persona:
        raise NoEncontrado("No se encontró a esa persona.")
    editables = atributos_editables(conn)
    valores = {v["clave"]: v for v in atributos.valores_de(conn, id_persona)}
    return {
        "nombre": persona["nombre"],
        "email": persona["email"],
        "celular": persona["celular"],
        "atributos": [
            {
                "clave": a["clave"],
                "etiqueta": a["etiqueta"],
                "valor": (valores.get(a["clave"]) or {}).get("valor"),
                "categorias": [
                    {"clave": c["clave"], "etiqueta": c.get("etiqueta")}
                    for c in (a.get("categorias") or [])
                    if c.get("activo") is not False
                ],
            }
            for a in editables
        ],
    }


def editar_atributos(conn, id_persona, valores):
    """R6.5 — guarda lo que el panelista cambió de sí mismo.

    Con `origen = 'panelista'`, que es lo que lo hace **autoritativo**: una
    ingesta posterior que traiga otro valor lo informa como discrepancia y no
    lo pisa. La jerarquía es panelista > operador > archivo, y la razón es
    obvia escrita: nadie sabe mejor que la persona en qué barrio vive.
    """
    permitidos = {a["clave"] for a in atributos_editables(conn)}
    pedidos = {str(k): v for k, v in (valores or {}).items()}
    rechazados = sorted(set(pedidos) - permitidos)
    if rechazados:
        raise SinPermiso(
            f"Estos datos no se pueden editar desde el portal: "
            f"{', '.join(rechazados)}.",
            {"editables": sorted(permitidos)})

    cambiados, sin_categoria = [], []
    for clave, valor in pedidos.items():
        resultado = atributos.fijar(
            conn, id_persona, clave, valor, origen=ORIGEN_PANELISTA,
            # El panelista gana: su valor pisa lo que hubiera.
            pisar=True)
        if resultado["estado"] in ("completado", "sin_cambio"):
            cambiados.append(clave)
        elif resultado["estado"] == "sin_categoria":
            sin_categoria.append(clave)
    return {"actualizados": cambiados, "sin_categoria": sin_categoria}


ORIGEN_PANELISTA = "panelista"


# ── R6.6 · Datos de contacto ─────────────────────────────────────────

def pedir_verificacion_de_contacto(conn, id_persona, canal, destino,
                                   origen=None, enviar=None):
    """Manda un código al contacto **nuevo**, antes de reemplazar nada.

    Es lo que evita el modo de falla que arruinaría el acceso: si el correo
    nuevo se guardara sin verificar y estuviera mal tipeado, la persona
    perdería la puerta de entrada al portal.
    """
    canal, destino = verif.normalizar_destino(canal, destino)
    return verif.pedir_codigo(conn, canal, destino, origen=origen, enviar=enviar)


def confirmar_contacto(conn, id_persona, canal, destino, codigo):
    """Comprueba el código y recién ahí reemplaza el dato."""
    canal, destino = verif.normalizar_destino(canal, destino)
    verif.verificar(conn, canal, destino, codigo)
    verif.consumir(conn, canal, destino)

    if canal == verif.EMAIL:
        otro = db.una(
            conn,
            "select id_persona from persona where lower(email) = %s "
            "  and id_persona <> %s",
            (destino, str(id_persona)))
        if otro:
            raise Conflicto(
                "Ese correo ya está registrado para otra persona.",
                {"motivo": "email_duplicado"})
        db.ejecutar(conn,
                    "update persona set email = %s where id_persona = %s",
                    (destino, str(id_persona)))
        # La cuenta sigue siendo la misma —el vínculo es por `uid`— pero se
        # deja anotado con qué correo quedó, para poder diagnosticar.
        db.ejecutar(conn,
                    "update cuenta_panelista set email = %s where id_persona = %s",
                    (destino, str(id_persona)))
        return {"canal": canal, "email": destino}

    db.ejecutar(conn,
                "update persona set celular = %s where id_persona = %s",
                (destino, str(id_persona)))
    return {"canal": canal, "celular": destino}


# ── R6.7 · Canales ───────────────────────────────────────────────────

def canales(conn, id_persona):
    """Por qué canales aceptó ser contactado, y cuáles puede activar."""
    actuales = {c["canal"]: c for c in preferencias.listar(conn, id_persona)}
    persona = db.una(conn, "select celular from persona where id_persona = %s",
                     (str(id_persona),))
    hay_celular = bool((persona or {}).get("celular"))
    salida = []
    for canal in preferencias.CANALES:
        registro = actuales.get(canal) or {}
        # R6.6 — un celular sin verificar no habilita WhatsApp. Acá se
        # expresa como «no se puede activar y se dice por qué»: un control
        # deshabilitado sin motivo es peor que no tenerlo.
        requiere_celular = canal in (preferencias.WHATSAPP, preferencias.SMS,
                                     preferencias.TELEFONO)
        verificado = (hay_celular and verif.esta_verificado(
            conn, verif.CELULAR, persona["celular"])) if requiere_celular else True
        salida.append({
            "canal": canal,
            "activo": bool(registro.get("activa")),
            "desde": registro.get("otorgado_en"),
            "puede_activar": verificado,
            "motivo": None if verificado else (
                "Para activarlo hace falta un celular verificado."),
        })
    return salida


def cambiar_canal(conn, id_persona, canal, activo):
    """R6.7 — cada canal por separado. Revocar uno no toca los otros, y
    **no** es darse de baja: son cosas distintas."""
    if canal not in preferencias.CANALES:
        raise DatosInvalidos(f"Canal desconocido: {canal!r}.",
                             {"canales": list(preferencias.CANALES)})
    if activo:
        estado = {c["canal"]: c for c in canales(conn, id_persona)}.get(canal) or {}
        if not estado.get("puede_activar"):
            raise Conflicto(estado.get("motivo") or "No se puede activar ese canal.",
                            {"canal": canal})
        preferencias.otorgar(conn, id_persona, canal, origen=ORIGEN_PANELISTA)
    else:
        preferencias.revocar(conn, id_persona, canal)
    return {"canal": canal, "activo": bool(activo)}


# ── R6.8 · Finalidades ───────────────────────────────────────────────

def finalidades(conn, id_persona):
    """Qué consintió, con qué texto y cuándo."""
    return consentimiento.listar(conn, id_persona)


def retirar_finalidad(conn, id_persona, finalidad, conn_semantica=None):
    """R6.8 — retirar una finalidad **sin** dejar el panel.

    Granular y no todo o nada: alguien puede querer salir del análisis entre
    estudios y seguir participando. La cascada es la que ya existe; acá no se
    inventa una segunda.
    """
    if finalidad not in consentimiento.FINALIDADES:
        raise DatosInvalidos(
            f"Finalidad desconocida: {finalidad!r}.",
            {"finalidades": list(consentimiento.FINALIDADES)})
    return bajas.retirar(conn, id_persona, finalidad=finalidad,
                         actor=ORIGEN_PANELISTA, conn_semantica=conn_semantica)


# ── R6.9 · Baja ──────────────────────────────────────────────────────

def previo_a_la_baja(conn, id_persona):
    """Lo que hay que decirle **antes** de confirmar.

    Los puntos se pierden. Está decidido, y la decisión no es el problema: el
    problema sería que apareciera después. Por eso el saldo va a la vista y
    los canjes pendientes se nombran uno por uno.
    """
    saldo = puntos.saldo(conn, id_persona)
    pendientes = [
        c for c in mis_canjes(conn, id_persona)
        if c["estado"] in ("solicitado", "aprobado")
    ]
    return {
        "saldo_que_se_pierde": saldo,
        "canjes_que_se_cancelan": pendientes,
        "advertencias": [
            "Se eliminan tus datos personales de nuestra base.",
            f"Perdés los {saldo} punto(s) que tenés acumulados."
            if saldo else "No tenés puntos acumulados.",
            (f"Se cancelan {len(pendientes)} pedido(s) de premio que todavía "
             f"no te entregamos.") if pendientes else
            "No tenés pedidos de premio pendientes.",
            "Dejás de recibir convocatorias.",
        ],
    }


def darse_de_baja(conn, id_persona, conn_semantica=None):
    """R6.9 — la baja completa, con la cascada que ya existe (R1.3).

    Se confirma que **quedó registrada y se está ejecutando**, no que
    terminó: la cascada incluye consumidores externos que confirman de forma
    asincrónica, y prometer que ya está sería mentir sobre algo verificable.
    """
    resumen = previo_a_la_baja(conn, id_persona)
    resultado = bajas.retirar(conn, id_persona, actor=ORIGEN_PANELISTA,
                              conn_semantica=conn_semantica)
    return {
        **resultado,
        "puntos_perdidos": resumen["saldo_que_se_pierde"],
        "canjes_cancelados": len(resumen["canjes_que_se_cancelan"]),
        "mensaje": (
            "Tu baja quedó registrada y se está ejecutando. Tus datos se "
            "eliminan de nuestra base; algunos sistemas que los recibieron "
            "confirman el borrado en las próximas horas."),
    }
