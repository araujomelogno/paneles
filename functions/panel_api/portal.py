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

Por eso todo lo del portal entra por acá, y acá rigen cuatro reglas que no
rigen en el resto de la API:

1. **El `id_persona` no se recibe nunca.** Sale de `cuenta_panelista`, a
   partir del `uid` del token. Ninguna función de este módulo acepta un
   identificador de persona del cliente.
2. **Lo editable es una lista blanca.** Un atributo se puede tocar solo si
   alguien lo marcó editable; lo demás se rechaza aunque venga forzado.
3. **La respuesta no revela.** Pedir un enlace, o errar una credencial,
   contesta lo mismo exista o no el correo, porque la diferencia alcanzaría
   para averiguar quién integra el panel probando direcciones.
4. **Lo irreversible pide la contraseña de nuevo** (R6.1.d), aunque la
   sesión esté vigente.

── R6.1.a · Del enlace mágico a la contraseña ──

La primera versión de esta fase hacía entrar con un **enlace de un solo uso
por visita**: pedir el correo, esperarlo, hacer clic. Para un portal al que
se vuelve cada varios meses eso es la diferencia entre un portal que se usa y
uno al que nadie vuelve, y además deja el acceso a merced de la
entregabilidad del correo.

Ahora se entra con contraseña, y el enlace queda para **crear** y
**recuperar** esa contraseña. El cambio no es solo de pantalla:

* El login **pasa por el backend** (`iniciar_sesion`) y no por el SDK del
  navegador, porque el límite de intentos de R6.1.b no se puede hacer valer
  desde el cliente y porque así una persona dada de baja se frena en la
  puerta. El porqué largo está en `credenciales.py`.
* El token del enlace sigue siendo **nuestro**: `acceso_portal` es lo que
  hace que sirva una sola vez y venza, con un `update ... where usado_en is
  null`, que se puede probar contra una base de verdad.
* La contraseña **no se guarda acá**. Va a Firebase Auth y nunca a una tabla,
  un log ni una respuesta.
"""

import secrets as _secrets

from . import (
    atributos, bajas, consentimiento, credenciales as credenciales_mod, db,
    preferencias, premios, puntos, verificacion_contacto as verif,
)
from .errores import Conflicto, DatosInvalidos, NoAutenticado, NoEncontrado, SinPermiso

# ── R6.1.a · Parámetros del acceso ───────────────────────────────────

# Cuánto vive el enlace para crear o recuperar la contraseña. Veinticuatro
# horas y no quince minutos como el enlace de ingreso que reemplaza: ya no se
# pide en cada visita sino una vez cada tanto, y quien lo recibe puede estar
# mirando el correo al otro día. El vencimiento **lo hace valer esta base**
# —`vence_en` en `acceso_portal`—, no una casilla de la consola de Firebase.
HORAS_DEL_ENLACE = 24

# Cuántos enlaces se pueden pedir para el mismo correo, y desde el mismo
# origen, en una hora. El segundo es el que frena la enumeración: sin él,
# probar direcciones ajenas para ver cuál existe saldría gratis.
MAX_ENLACES_POR_CORREO_POR_HORA = 5
MAX_ENLACES_POR_ORIGEN_POR_HORA = 15

# R6.1.b — y los intentos de ingreso fallidos, que se cuentan **aparte**.
# Si compartieran contador con los enlaces, cinco intentos de adivinar una
# contraseña dejarían a la persona sin poder pedir el enlace para
# recuperarla: el ataque le cerraría justo la puerta de salida.
#
# Por origen el número es bastante más alto que por correo porque una
# oficina, un hogar o una red móvil comparten salida: ahí el límite bajo
# castiga a vecinos, no a atacantes. El que muerde de verdad es el de por
# correo, que es por cuenta.
MAX_FALLOS_POR_CORREO_POR_HORA = 10
MAX_FALLOS_POR_ORIGEN_POR_HORA = 30

# R6.1.b — «la sesión persiste hasta que el panelista cierre sesión o venza
# por inactividad prolongada». Firebase no vence sesiones por inactividad, así
# que la inactividad la mide la bóveda sobre `cuenta_panelista.ultimo_acceso_en`:
# noventa días sin entrar y hay que volver a poner la contraseña.
DIAS_DE_INACTIVIDAD = 90

# Cada cuánto se refresca esa marca. Escribirla en cada request sería un
# `update` por pantalla pintada; con una hora de gracia el dato sigue
# sirviendo para lo único que se usa, que es medir meses.
MINUTOS_ENTRE_MARCAS_DE_ACCESO = 60

# Los motivos de `acceso_portal` (catálogo `motivo_acceso_portal`).
ALTA_CLAVE = "alta_clave"
RECUPERACION = "recuperacion"
LOGIN_FALLIDO = "login_fallido"
MOTIVOS_DE_ENLACE = (ALTA_CLAVE, RECUPERACION)

# Lo que se contesta al pedir un enlace, exista o no el correo. Es una sola
# constante a propósito: dos mensajes parecidos escritos en dos lugares
# terminan divergiendo, y la diferencia **es** la filtración.
RESPUESTA_DE_ENLACE = (
    "Si esa dirección corresponde a un panelista, va a recibir un enlace "
    "para crear o recuperar su contraseña. Revisá tu correo."
)

# R6.1.b — y lo que se contesta ante cualquier credencial que no entra.
# Misma regla, otro texto: no dice si el correo existe, no dice si la cuenta
# está deshabilitada, no dice si la persona dejó de ser panelista. Si el
# error distinguiera «ese correo no existe» de «contraseña incorrecta»,
# cualquiera podría averiguar quién integra el panel probando direcciones, y
# eso es una filtración de datos personales aunque nunca se muestre un
# perfil.
MENSAJE_CREDENCIAL_INVALIDA = "Correo o contraseña incorrectos."


def _hash(valor):
    """El mismo hashing con sal que la verificación de R4.3."""
    return verif._hash(valor)


def _normalizar_email(crudo):
    texto = str(crudo or "").strip().lower()
    if not texto or "@" not in texto or "." not in texto.split("@")[-1]:
        raise DatosInvalidos("Ese correo no parece válido.")
    return texto


def _contar_accesos(conn, columna, valor, motivos, horas=1):
    """Cuántas filas de esos motivos hay en la última hora.

    El motivo entra en el conteo —y en el índice— porque los dos límites son
    distintos: pedir enlaces y errar contraseñas no se mezclan.
    """
    fila = db.una(
        conn,
        f"""select count(*)::int as n from acceso_portal
             where {columna} = %s and motivo = any(%s)
               and creado_en > now() - make_interval(hours => %s)""",
        (valor, list(motivos), horas))
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


def _registrar_intento(conn, email, origen_hash, id_persona=None):
    """Deja constancia de un ingreso que no entró.

    Se registra **aunque el correo no sea de nadie**, con el correo hasheado:
    si solo contaran los intentos contra panelistas reales, recorrer un
    diccionario de direcciones ajenas no tendría límite, que es exactamente
    el ataque que el límite existe para frenar.
    """
    db.ejecutar(
        conn,
        """insert into acceso_portal (email_hash, id_persona, origen_hash, motivo)
           values (%s, %s, %s, %s)""",
        (_hash(email), str(id_persona) if id_persona else None, origen_hash,
         LOGIN_FALLIDO))


def _frenar_si_hay_demasiados(conn, email, origen_hash, motivos,
                              max_por_origen, max_por_correo, que):
    """Los dos límites, en el orden que no dice nada de más.

    Se aplican **antes** de mirar si el correo existe. Si se aplicaran
    después, el tiempo de respuesta ya diría algo: una consulta a `persona`
    tarda distinto que no hacerla.
    """
    if origen_hash and _contar_accesos(
            conn, "origen_hash", origen_hash, motivos) >= max_por_origen:
        raise Conflicto(
            f"Se registraron demasiados {que} desde este dispositivo. Esperá "
            f"un rato y volvé a intentar.", {"motivo": "tasa_por_origen"})
    if _contar_accesos(conn, "email_hash", _hash(email), motivos) >= max_por_correo:
        # Este rechazo sí distingue un correo de otro, pero solo entre
        # correos que **alguien ya usó muchas veces en una hora**: es
        # información sobre los pedidos, no sobre el padrón.
        raise Conflicto(
            f"Se registraron demasiados {que} para esa dirección. Esperá un "
            f"rato y volvé a intentar.", {"motivo": "tasa_por_correo"})


# ── R6.1.a/c · Crear y recuperar la contraseña ───────────────────────

def pedir_enlace_de_clave(conn, email, motivo=RECUPERACION, origen=None,
                          enviar=None, armar_enlace=None, actor=None):
    """Emite el enlace para fijar la contraseña y lo manda. Contesta igual
    exista o no el correo.

    Es el mismo mecanismo para los tres casos que lo necesitan —la
    inscripción recién aprobada, el panelista enrolado antes de esta fase, y
    el que se la olvidó—, y eso no es ahorro de código: un segundo camino
    para fijar una contraseña es un segundo conjunto de reglas sobre quién
    puede hacerlo, y el segundo envejece.

    `actor` viene con valor solo cuando lo dispara alguien de Equipos. Queda
    guardado para poder auditarlo; **la contraseña no la ve ni la define**:
    lo único que esta función le deja hacer es que salga el correo.
    """
    email = _normalizar_email(email)
    if motivo not in MOTIVOS_DE_ENLACE:
        raise DatosInvalidos(f"Motivo de enlace desconocido: {motivo!r}.",
                             {"motivos": list(MOTIVOS_DE_ENLACE)})
    # R-MAIL.2 — el proveedor se resuelve **antes** de mirar si el correo
    # existe. Sin proveedor la respuesta es un error de configuración, igual
    # para cualquier dirección; nunca el enlace en la respuesta.
    enviador = enviar or verif.proveedor_de_envio(canal=verif.EMAIL)
    origen_hash = verif.hash_origen(origen)
    _frenar_si_hay_demasiados(
        conn, email, origen_hash, MOTIVOS_DE_ENLACE,
        MAX_ENLACES_POR_ORIGEN_POR_HORA, MAX_ENLACES_POR_CORREO_POR_HORA,
        "pedidos de enlace")

    persona = _panelista_por_email(conn, email)
    token = _secrets.token_urlsafe(32) if persona else None

    db.ejecutar(
        conn,
        """insert into acceso_portal
                  (email_hash, id_persona, token_hash, vence_en, origen_hash,
                   motivo, emitido_por, emitido_por_email)
           values (%s, %s, %s,
                   case when %s::text is null then null
                        else now() + make_interval(hours => %s) end,
                   %s, %s, %s, %s)""",
        (_hash(email), persona["id_persona"] if persona else None,
         _hash(token) if token else None, token, HORAS_DEL_ENLACE,
         origen_hash, motivo, getattr(actor, "uid", None),
         getattr(actor, "email", None)))

    salida = {"mensaje": RESPUESTA_DE_ENLACE, "horas": HORAS_DEL_ENLACE}
    if not persona:
        # Nada que mandar. Y la respuesta es la misma: ésa es la regla.
        return salida

    enlace = (armar_enlace or _enlace_del_portal)(token)
    # Un fallo de envío levanta `EnvioFallido` y no se contesta como si el
    # correo hubiera salido (R-MAIL.1). Eso distingue, mientras dure la
    # caída del servidor de correo, una dirección del panel de una que no lo
    # es; es el precio de no mentirle a quien sí espera el correo, y está
    # anotado en docs/decisiones.md (D65).
    resultado = verif.enviar_y_registrar(
        conn, enviador, verif.EMAIL, email, enlace,
        tipo="alta_clave" if motivo == ALTA_CLAVE else "recuperacion_clave",
        horas=HORAS_DEL_ENLACE)
    if resultado.get("sin_proveedor") and verif.modo_desarrollo():
        # Modo desarrollo explícito (R-MAIL.2): sin proveedor el enlace vuelve
        # en la respuesta y entonces no prueba que quien pide tenga acceso al
        # correo. Nunca en el entorno desplegado, y ninguna página pública lo
        # muestra.
        salida["enlace_sin_enviar"] = enlace
        salida["aviso"] = (
            "No hay proveedor de envío configurado: el enlace vuelve en esta "
            "respuesta y no prueba nada. No usar así en producción.")
    return salida


def emitir_para_panelista(conn, id_persona, actor=None, enviar=None,
                          armar_enlace=None):
    """R6.1.a — un responsable le manda el enlace a alguien que ya está en el
    panel.

    Es la salida al problema de arrastre de esta fase: los panelistas
    enrolados antes de R6.1.a **no tienen contraseña** porque nadie se la
    pidió nunca, y el que tenga el correo desactualizado no va a poder
    pedirla solo. Alguien de Equipos dispara el envío.

    Lo que **no** puede hacer quien lo dispara, y es el punto: ver la
    contraseña, definirla, ni enterarse de si la persona ya tenía una. Lo
    único que ocurre es que sale un correo. Y queda registrado quién lo
    pidió, porque pedir el enlace de otra persona es sensible aunque no
    cambie nada: sin rastro no se distingue de un intento de tomar la
    cuenta.

    A diferencia del pedido público, acá **sí** se informa si la dirección no
    sirve. No hay nada que ocultarle a un responsable que ya puede abrir la
    ficha de esa persona, y callárselo lo dejaría creyendo que resolvió algo
    que no resolvió —el mismo error que `usuarios.generar_acceso()` evita con
    los usuarios desactivados—.
    """
    persona = db.una(
        conn,
        "select email, estado from persona where id_persona = %s",
        (str(id_persona),))
    if not persona:
        raise NoEncontrado(f"No existe la persona {id_persona}.")
    if persona["estado"] != "activa":
        raise Conflicto(
            "Esa persona no está activa: el enlace saldría igual y no "
            "serviría para entrar.",
            {"estado": persona["estado"]})
    if not (persona["email"] or "").strip():
        raise Conflicto(
            "Esa persona no tiene correo registrado, así que no hay a dónde "
            "mandarle el enlace. Cargáselo primero.",
            {"motivo": "sin_correo"})

    salida = pedir_enlace_de_clave(
        conn, persona["email"], motivo=ALTA_CLAVE, enviar=enviar,
        armar_enlace=armar_enlace, actor=actor)
    return {**salida, "id_persona": str(id_persona),
            "email": persona["email"]}


def emisiones_de(conn, id_persona, limite=50):
    """El rastro de R6.1.a: qué enlaces se le emitieron a esa persona, quién
    los pidió y cuáles siguen sirviendo."""
    filas = db.todas(
        conn,
        """select id, motivo, motivo_etiqueta, emitido_por, emitido_por_email,
                  vence_en, usado_en, sigue_sirviendo, creado_en
             from v_acceso_portal
            where id_persona = %s and not es_intento
            order by creado_en desc
            limit %s""",
        (str(id_persona), limite))
    return [
        {**f,
         "vence_en": f["vence_en"].isoformat() if f["vence_en"] else None,
         "usado_en": f["usado_en"].isoformat() if f["usado_en"] else None,
         "creado_en": f["creado_en"].isoformat(),
         # Quién lo pidió, en una sola palabra. Sin esto hay que deducirlo de
         # un `emitido_por` nulo, y «nulo» no es una explicación.
         "pedido_por": f["emitido_por_email"] or "el propio panelista"}
        for f in filas
    ]


def _enlace_del_portal(token):
    """A dónde apunta el enlace que recibe el panelista.

    Al portal, no a una pantalla de Firebase. El token es **nuestro** y por
    eso el «una sola vez» y el vencimiento son nuestros: los hace valer un
    `update ... where usado_en is null and vence_en > now()`, que es algo que
    se puede probar contra una base de verdad, y no una casilla de una
    consola que nadie vuelve a mirar.
    """
    import os

    base = os.environ.get("PORTAL_URL", "").rstrip("/")
    if not base:
        raise DatosInvalidos(
            "Falta `PORTAL_URL`: sin la dirección del portal no se puede "
            "armar el enlace para fijar la contraseña.")
    return f"{base}/clave?t={token}"


def _consumir_token(conn, token):
    """Consume el enlace y devuelve a quién corresponde.

    Un enlace sirve **una sola vez**: se marca usado en la misma sentencia
    que lo busca, así que dos pedidos simultáneos con el mismo token no
    pueden ganar los dos.
    """
    if not token:
        raise NoAutenticado("Falta el enlace.")
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
            "Pedí uno nuevo desde «Olvidé mi contraseña».",
            {"motivo": "enlace_invalido"})
    return fila["id_persona"]


def fijar_clave(conn, token, clave, credenciales):
    """R6.1.a — la persona establece su contraseña con el enlace que recibió.

    La contraseña va directo a Firebase Auth: acá no se hashea, no se guarda
    y no se compara nada. Lo que sí es nuestro es la prueba de identidad —el
    token de un solo uso— y es lo que decide **de quién** es la cuenta que se
    está tocando.
    """
    credenciales_mod.validar_clave(clave)
    id_persona = _consumir_token(conn, token)
    persona = db.una(
        conn,
        "select email, estado from persona where id_persona = %s",
        (str(id_persona),))
    if not persona or persona["estado"] != "activa":
        raise SinPermiso("Esa cuenta ya no tiene acceso al portal.",
                         {"motivo": "no_activa"})
    email = _normalizar_email(persona["email"])

    cuenta = credenciales.buscar(email)
    if cuenta:
        uid = cuenta["uid"]
        # Una cuenta de Auth está atada a un correo, y el enlace salió a ese
        # correo. Si pese a eso el `uid` ya apunta a **otra** persona, hay un
        # vínculo mal armado y tocarlo le cambiaría la contraseña a un
        # tercero: se frena antes y se avisa.
        vinculo = db.una(
            conn, "select id_persona from cuenta_panelista where uid = %s", (uid,))
        if vinculo and str(vinculo["id_persona"]) != str(id_persona):
            raise Conflicto(
                "Esa cuenta ya está vinculada a otra persona del panel. "
                "Escribinos para que lo resolvamos.",
                {"motivo": "cuenta_de_otra_persona"})
        if cuenta.get("deshabilitada"):
            # Alguien que se dio de baja y volvió a inscribirse: la cuenta de
            # Auth quedó apagada por R6.1.e y la persona de ahora es otra fila.
            credenciales.habilitar(uid)
        credenciales.fijar_clave(uid, clave)
    else:
        uid = credenciales.crear(email, clave)["uid"]

    vinculo = vincular(conn, uid, email)
    return {
        "id_persona": str(id_persona),
        "nueva": vinculo["nueva"],
        # Para que no haya que escribir la contraseña recién creada en la
        # pantalla siguiente: la sesión se abre acá mismo.
        "token_de_sesion": credenciales.token_de_sesion(uid),
    }


# ── R6.1.b · Entrar ──────────────────────────────────────────────────

def iniciar_sesion(conn, email, clave, credenciales, origen=None):
    """R6.1.b — correo y contraseña. Un solo mensaje para todo lo que falla.

    Los cinco motivos por los que esto puede no entrar —el correo no existe
    en Auth, la contraseña es otra, la cuenta está deshabilitada, la persona
    no es panelista, la persona ya no está activa— devuelven **la misma
    excepción con el mismo texto**. No es prolijidad: cualquier diferencia
    entre dos de esos casos convierte el formulario de ingreso en un
    buscador de panelistas.
    """
    email = _normalizar_email(email)
    origen_hash = verif.hash_origen(origen)
    _frenar_si_hay_demasiados(
        conn, email, origen_hash, (LOGIN_FALLIDO,),
        MAX_FALLOS_POR_ORIGEN_POR_HORA, MAX_FALLOS_POR_CORREO_POR_HORA,
        "intentos fallidos")

    cuenta = credenciales.verificar_clave(email, clave)
    persona = _panelista_por_email(conn, email)
    if not cuenta or not persona:
        _registrar_intento(conn, email, origen_hash,
                           persona["id_persona"] if persona else None)
        raise NoAutenticado(MENSAJE_CREDENCIAL_INVALIDA,
                            {"motivo": "credencial_invalida"})

    vinculo = vincular(conn, cuenta["uid"], email)
    return {
        "id_persona": str(vinculo["id_persona"]),
        "nueva": vinculo["nueva"],
        "token_de_sesion": credenciales.token_de_sesion(cuenta["uid"]),
    }


# ── R6.1.c · Cambiar la contraseña ───────────────────────────────────

def cambiar_clave(conn, id_persona, actual, nueva, credenciales, origen=None):
    """Con la sesión abierta y la contraseña actual.

    Pedir la actual no es un trámite: una sesión abierta en una máquina
    prestada alcanzaría, si no, para quedarse con la cuenta. Y el cambio
    **cierra las demás sesiones** (lo hace `fijar_clave` del proveedor), que
    es lo que vuelve útil cambiarla cuando se sospecha que alguien entró.
    """
    credenciales_mod.validar_clave(nueva)
    uid = reautenticar(conn, id_persona, actual, credenciales, origen=origen)
    credenciales.fijar_clave(uid, nueva)
    return {
        "cambiada": True,
        # La sesión que pidió el cambio también se cayó con la revocación, así
        # que se le devuelve una nueva: si no, cambiar la contraseña echaría
        # a la persona del portal y parecería un error.
        "token_de_sesion": credenciales.token_de_sesion(uid),
        "mensaje": ("Listo. Cerramos las otras sesiones que tuvieras "
                    "abiertas; en este dispositivo seguís adentro."),
    }


# ── R6.1.d · Reautenticación para lo irreversible ────────────────────

# Las tres acciones que la piden, y por qué sólo ésas: la sesión sirve para
# mirar y para lo que se deshace —prender un canal que se apagó, corregir un
# atributo, pedir un canje—; lo que destruye datos pide probar de nuevo que
# sos vos. Darse de baja no se deshace; retirar una finalidad borra
# embeddings; cambiar el correo mueve la puerta de entrada.
ACCIONES_QUE_REAUTENTICAN = ("baja", "retiro_de_finalidad", "cambio_de_correo")


def reautenticar(conn, id_persona, clave, credenciales, origen=None):
    """Comprueba la contraseña de quien ya tiene sesión. Devuelve su `uid`.

    Acá el mensaje **sí** puede ser específico —«la contraseña no coincide»—
    y no hay contradicción con R6.1.b: la sesión ya probó quién es, así que
    no queda nada que enumerar. El límite de intentos sigue aplicando, porque
    si no esto sería un oráculo de contraseñas con sesión robada.
    """
    fila = db.una(
        conn,
        """select c.uid, p.email
             from cuenta_panelista c
             join persona p on p.id_persona = c.id_persona
            where c.id_persona = %s""",
        (str(id_persona),))
    if not fila:
        raise SinPermiso("Esta persona no tiene cuenta del portal.",
                         {"motivo": "sin_vinculo"})
    email = _normalizar_email(fila["email"])
    origen_hash = verif.hash_origen(origen)
    _frenar_si_hay_demasiados(
        conn, email, origen_hash, (LOGIN_FALLIDO,),
        MAX_FALLOS_POR_ORIGEN_POR_HORA, MAX_FALLOS_POR_CORREO_POR_HORA,
        "intentos fallidos")

    cuenta = credenciales.verificar_clave(email, clave)
    if not cuenta or cuenta["uid"] != fila["uid"]:
        _registrar_intento(conn, email, origen_hash, id_persona)
        raise NoAutenticado(
            "La contraseña no coincide. Para esta acción hace falta volver a "
            "escribirla.", {"motivo": "reautenticacion_fallida"})
    return fila["uid"]


def _exigir_reautenticacion(conn, id_persona, clave, credenciales, accion,
                            origen=None):
    """La baranda que usan las tres rutas sensibles.

    Falla **cerrado**: sin proveedor de credenciales no se reautentica y la
    acción no ocurre. Es deliberado —una acción irreversible que se ejecuta
    porque la comprobación no estaba disponible es peor que una que no se
    ejecuta—.
    """
    if credenciales is None:
        raise SinPermiso(
            "No se puede comprobar la contraseña en este momento, así que "
            "esta acción no se ejecuta. Intentá de nuevo en un rato.",
            {"accion": accion})
    if not str(clave or ""):
        raise NoAutenticado(
            "Para esta acción hace falta volver a escribir tu contraseña.",
            {"motivo": "requiere_reautenticacion", "accion": accion})
    return reautenticar(conn, id_persona, clave, credenciales, origen=origen)


# ── R6.1.e · Cortar el acceso ────────────────────────────────────────

def cortar_acceso(conn, id_persona, credenciales=None):
    """Deja a esa persona sin poder entrar al portal, ya.

    Lo llama **la baja**, cualquier baja: la que pide el titular desde el
    portal y la que ejecuta el DPO desde la administración. Si viviera solo
    en el camino del portal, una baja hecha a mano dejaría la credencial viva
    y las sesiones abiertas, que es justo lo que R6.1.e prohíbe.

    Nunca hace fallar a la baja. Firebase es un sistema de afuera y puede
    estar caído; la bóveda no espera a nadie para borrar (misma postura que
    con el store semántico). Lo que pasó se informa en el resultado, y la
    persona igual pierde el acceso en cuanto `cuenta_panelista` desaparece
    con la cascada: sin esa fila la sesión no resuelve a nadie.
    """
    fila = db.una(conn, "select uid from cuenta_panelista where id_persona = %s",
                  (str(id_persona),))
    if not fila:
        return {"estado": "sin_cuenta"}
    if credenciales is None:
        try:
            credenciales = credenciales_mod.crear()
        except Exception as error:  # noqa: BLE001
            return {"estado": "error", "detalle": str(error)}
    try:
        credenciales.deshabilitar(fila["uid"])
    except Exception as error:  # noqa: BLE001
        return {"estado": "error", "uid": fila["uid"], "detalle": str(error)}
    return {"estado": "cortado", "uid": fila["uid"]}


# ── R6.2 · El vínculo entre la cuenta y la persona ───────────────────

def vincular(conn, uid, email):
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
        marcar_acceso(conn, uid)
        return {"uid": uid, "id_persona": ya["id_persona"], "nueva": False}

    persona = _panelista_por_email(conn, email)
    if not persona:
        raise SinPermiso(
            "Esa dirección no corresponde a ningún panelista activo. Si "
            "querés sumarte al panel, podés inscribirte desde el formulario "
            "público.", {"motivo": "sin_panelista"})

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


def marcar_acceso(conn, uid):
    """Deja constancia de que la cuenta sigue en uso.

    Con una hora de gracia: es el reloj de la inactividad, que se mide en
    meses, no un contador de pantallas. Escribirlo en cada request sería un
    `update` por cada cosa que el portal pinta.
    """
    return db.ejecutar(
        conn,
        """update cuenta_panelista
              set ultimo_acceso_en = now()
            where uid = %s
              and (ultimo_acceso_en is null
                   or ultimo_acceso_en < now() - make_interval(mins => %s))""",
        (uid, MINUTOS_ENTRE_MARCAS_DE_ACCESO))


def persona_de(conn, uid):
    """El `id_persona` de la sesión. **La única fuente.**

    Ninguna ruta del portal acepta un `id_persona` del cliente: si lo
    aceptara, la autorización dependería de que todas las rutas se acuerden
    de comprobarlo, y alcanza con que una se olvide.
    """
    fila = db.una(
        conn,
        """select c.id_persona, p.estado,
                  (c.ultimo_acceso_en is not null
                   and c.ultimo_acceso_en < now() - make_interval(days => %s))
                    as inactiva
             from cuenta_panelista c
             join persona p on p.id_persona = c.id_persona
            where c.uid = %s""",
        (DIAS_DE_INACTIVIDAD, uid))
    if not fila:
        raise SinPermiso(
            "Esta cuenta no está vinculada a ningún panelista.",
            {"motivo": "sin_vinculo"})
    if fila["estado"] != "activa":
        raise SinPermiso(
            "Esta cuenta ya no tiene acceso al portal.",
            {"motivo": "no_activa"})
    if fila["inactiva"]:
        # R6.1.b — la sesión venció por no usarse. No se borra el vínculo:
        # la persona vuelve a entrar con su contraseña y sigue siendo la
        # misma cuenta.
        raise NoAutenticado(
            f"Pasaron más de {DIAS_DE_INACTIVIDAD} días desde tu última "
            f"visita, así que cerramos la sesión. Entrá de nuevo con tu "
            f"contraseña.", {"motivo": "sesion_vencida"})
    marcar_acceso(conn, uid)
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


def confirmar_contacto(conn, id_persona, canal, destino, codigo,
                       clave=None, credenciales=None, origen=None):
    """Comprueba el código y recién ahí reemplaza el dato.

    R6.1.d/f — cambiar el correo **pide la contraseña de nuevo**, y con razón:
    el correo es la puerta de entrada, y quien lo cambia se queda con la
    cuenta. El celular no: equivocarse de número se arregla cambiándolo otra
    vez.

    El orden importa y es éste: código al destino nuevo → contraseña actual →
    recién ahí se reemplaza. El código prueba que la dirección nueva es
    alcanzable —si tuviera un error de tipeo y la guardáramos igual, la
    persona perdería el acceso—; la contraseña prueba que quien la cambia es
    el titular y no alguien sentado en su sesión abierta.
    """
    canal, destino = verif.normalizar_destino(canal, destino)

    if canal == verif.EMAIL:
        # La contraseña va **antes** de comprobar el código, y el orden no es
        # cosmético: `verif.verificar()` consume el código al acertarlo, así
        # que si se comprobara primero, un intento sin contraseña —el que
        # hace la pantalla antes de abrir el modal— quemaría el código y la
        # persona tendría que pedir otro. R6.1.d pide exactamente lo
        # contrario: que tras reautenticar la acción siga sin volver a
        # empezar.
        uid = _exigir_reautenticacion(
            conn, id_persona, clave, credenciales, "cambio_de_correo",
            origen=origen)
        otro = db.una(
            conn,
            "select id_persona from persona where lower(email) = %s "
            "  and id_persona <> %s",
            (destino, str(id_persona)))
        if otro:
            raise Conflicto(
                "Ese correo ya está registrado para otra persona.",
                {"motivo": "email_duplicado"})
        verif.verificar(conn, canal, destino, codigo)
        verif.consumir(conn, canal, destino)
        db.ejecutar(conn,
                    "update persona set email = %s where id_persona = %s",
                    (destino, str(id_persona)))
        # El vínculo es por `uid`, así que la cuenta sigue siendo la misma; lo
        # que cambia es con qué dirección se entra.
        db.ejecutar(conn,
                    "update cuenta_panelista set email = %s where id_persona = %s",
                    (destino, str(id_persona)))
        # R6.1.f — y en Auth también, conservando la contraseña. Hasta este
        # momento el acceso seguía funcionando con la dirección anterior, que
        # es lo que la spec pide: el correo sin verificar no mueve nada.
        credenciales.cambiar_email(uid, destino)
        return {"canal": canal, "email": destino,
                "mensaje": ("Listo. A partir de ahora entrás al portal con "
                            "esta dirección y la misma contraseña.")}

    verif.verificar(conn, canal, destino, codigo)
    verif.consumir(conn, canal, destino)

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


def retirar_finalidad(conn, id_persona, finalidad, conn_semantica=None,
                      clave=None, credenciales=None, origen=None):
    """R6.8 — retirar una finalidad **sin** dejar el panel.

    Granular y no todo o nada: alguien puede querer salir del análisis entre
    estudios y seguir participando. La cascada es la que ya existe; acá no se
    inventa una segunda.

    R6.1.d — pide la contraseña de nuevo porque borra: retirar
    `uso_semantico` elimina los embeddings de esa persona del store
    semántico, y eso no se deshace volviendo a consentir. Volver a dar el
    permiso no devuelve lo borrado.
    """
    if finalidad not in consentimiento.FINALIDADES:
        raise DatosInvalidos(
            f"Finalidad desconocida: {finalidad!r}.",
            {"finalidades": list(consentimiento.FINALIDADES)})
    _exigir_reautenticacion(conn, id_persona, clave, credenciales,
                            "retiro_de_finalidad", origen=origen)
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


def darse_de_baja(conn, id_persona, conn_semantica=None, clave=None,
                  credenciales=None, origen=None):
    """R6.9 — la baja completa, con la cascada que ya existe (R1.3).

    Se confirma que **quedó registrada y se está ejecutando**, no que
    terminó: la cascada incluye consumidores externos que confirman de forma
    asincrónica, y prometer que ya está sería mentir sobre algo verificable.

    R6.1.d — es la acción que más obviamente pide volver a probar identidad:
    no se deshace, y una sesión abierta en una máquina prestada alcanzaría
    para borrarle los datos a alguien.
    """
    _exigir_reautenticacion(conn, id_persona, clave, credenciales, "baja",
                            origen=origen)
    resumen = previo_a_la_baja(conn, id_persona)
    resultado = bajas.retirar(conn, id_persona, actor=ORIGEN_PANELISTA,
                              conn_semantica=conn_semantica,
                              credenciales=credenciales)
    return {
        **resultado,
        "puntos_perdidos": resumen["saldo_que_se_pierde"],
        "canjes_cancelados": len(resumen["canjes_que_se_cancelan"]),
        "mensaje": (
            "Tu baja quedó registrada y se está ejecutando. Tus datos se "
            "eliminan de nuestra base; algunos sistemas que los recibieron "
            "confirman el borrado en las próximas horas."),
    }
