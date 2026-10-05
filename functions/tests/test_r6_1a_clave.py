"""R6.1.a — El panelista entra con usuario y contraseña.

Reemplaza a R6.1, que hacía entrar con un **enlace de un solo uso por
visita**. El enlace no desapareció: dejó de ser la puerta y pasó a ser la
forma de crear y recuperar la contraseña.

El archivo sigue el orden del Definition of Done y arranca, como el de la
Fase 6, por lo que no se puede averiguar. La razón es la misma y vale
repetirla: un formulario de ingreso que distingue «ese correo no existe» de
«contraseña incorrecta» es un buscador de panelistas, y eso es una filtración
de datos personales aunque nunca muestre un perfil.

Dos cosas de método, porque explican por qué estas pruebas prueban algo:

* **La base es de verdad.** El límite de tasa, el enlace de un solo uso y el
  vencimiento son `acceso_portal` y un `update ... where usado_en is null`:
  si corrieran contra un doble no se estaría probando nada.
* **Firebase es de mentira** (`CredencialesEnMemoria`), y está bien que lo
  sea. Lo que no se prueba acá es que Auth hashee bien una contraseña, que es
  justamente lo que se delega. Lo que sí se prueba es todo lo que decidimos
  nosotros: quién entra, con qué límite, con qué mensaje, y qué pasa después
  de una baja.
"""

import pytest

from panel_api import credenciales as cred, db, personas, portal, ruteo
from panel_api.errores import Conflicto, DatosInvalidos, NoAutenticado, SinPermiso

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")
EMAIL = "panelista@ejemplo.invalid"
CLAVE = "mi-contrasena-1"
OTRA_CLAVE = "otra-contrasena-2"


# ── Escenario ────────────────────────────────────────────────────────

def _panelista(conn, documento="R61A-1", email=EMAIL):
    return personas.alta(
        conn,
        {"persona": {"documento": documento, "nombre": "Panelista de prueba",
                     "email": email, "celular": "+59899000111"},
         "consentimientos": consentimientos(*AMBAS)},
    )["id_persona"]


def _pedir(conn, email, motivo=portal.RECUPERACION, origen=None, actor=None):
    """Pide el enlace en modo desarrollo —sin proveedor de envío— y devuelve
    lo que contestó, incluido el enlace cuando se emitió."""
    return portal.pedir_enlace_de_clave(
        conn, email, motivo=motivo, origen=origen, actor=actor,
        enviar=lambda canal, destino, cuerpo, **_: {"sin_proveedor": True},
        armar_enlace=lambda token: f"https://portal/clave?t={token}")


def _token_de(respuesta):
    enlace = respuesta.get("enlace_sin_enviar")
    return enlace.split("t=")[1] if enlace else None


@pytest.fixture
def con_cuenta(conn_boveda, credenciales):
    """Una persona que ya pasó por el enlace y tiene su contraseña puesta."""
    id_persona = _panelista(conn_boveda)
    portal.fijar_clave(conn_boveda, _token_de(_pedir(conn_boveda, EMAIL)),
                       CLAVE, credenciales)
    return id_persona


# ════════════════════════════════════════════════════════════════════
#  DoD 1-2 · El enlace para establecer la contraseña
# ════════════════════════════════════════════════════════════════════

def test_un_panelista_sin_contrasena_recibe_el_enlace_y_la_establece(
        conn_boveda, credenciales):
    """El problema de arrastre de esta fase: nadie le pidió nunca una
    contraseña a los panelistas ya enrolados."""
    id_persona = _panelista(conn_boveda)
    token = _token_de(_pedir(conn_boveda, EMAIL, motivo=portal.ALTA_CLAVE))
    assert token

    salida = portal.fijar_clave(conn_boveda, token, CLAVE, credenciales)

    assert salida["id_persona"] == str(id_persona)
    # Y queda adentro sin volver a escribirla: el enlace ya probó identidad.
    assert salida["token_de_sesion"]
    assert credenciales.verificar_clave(EMAIL, CLAVE)
    # El vínculo cuenta ↔ persona quedó armado de paso (R6.2).
    assert str(portal.persona_de(conn_boveda, credenciales
                                 .buscar(EMAIL)["uid"])) == str(id_persona)


def test_el_enlace_usado_dos_veces_falla_la_segunda(conn_boveda, credenciales):
    """Un enlace que sirve dos veces es una credencial permanente mandada
    por correo."""
    _panelista(conn_boveda)
    token = _token_de(_pedir(conn_boveda, EMAIL))

    portal.fijar_clave(conn_boveda, token, CLAVE, credenciales)
    with pytest.raises(NoAutenticado, match="ya haber sido usado|no sirve"):
        portal.fijar_clave(conn_boveda, token, OTRA_CLAVE, credenciales)
    # Y la contraseña quedó siendo la primera: el segundo intento no cambió
    # nada, que es lo que «falla» tiene que querer decir acá.
    assert credenciales.verificar_clave(EMAIL, CLAVE)
    assert credenciales.verificar_clave(EMAIL, OTRA_CLAVE) is None


def test_un_enlace_vencido_no_sirve(conn_boveda, credenciales):
    _panelista(conn_boveda)
    token = _token_de(_pedir(conn_boveda, EMAIL))
    db.ejecutar(conn_boveda,
                "update acceso_portal set vence_en = now() - interval '1 minute'")

    with pytest.raises(NoAutenticado, match="vencido"):
        portal.fijar_clave(conn_boveda, token, CLAVE, credenciales)


def test_el_vencimiento_lo_pone_la_base_y_no_una_consola(conn_boveda):
    """Las veinticuatro horas están en `acceso_portal.vence_en`, así que se
    pueden ver. Si las pusiera Firebase habría que creerle a una casilla."""
    _panelista(conn_boveda)
    _pedir(conn_boveda, EMAIL)
    fila = db.una(
        conn_boveda,
        """select extract(epoch from (vence_en - creado_en)) / 3600 as horas
             from acceso_portal where token_hash is not null""")
    assert round(fila["horas"]) == portal.HORAS_DEL_ENLACE == 24


# ════════════════════════════════════════════════════════════════════
#  DoD 3 · El responsable reenvía, y no ve ni define la contraseña
# ════════════════════════════════════════════════════════════════════

def test_un_responsable_reenvia_el_enlace_sin_ver_ni_definir_la_contrasena(
        conn_boveda, actor):
    id_persona = _panelista(conn_boveda)
    quien_lo_pide = actor("operaciones")

    salida = portal.emitir_para_panelista(
        conn_boveda, id_persona, actor=quien_lo_pide,
        enviar=lambda canal, destino, cuerpo, **_: {"enviado": True})

    # Lo único que le vuelve al responsable es que salió el correo. Ni la
    # contraseña —no existe todavía— ni el enlace, que se fue al correo de
    # la persona.
    assert set(salida) == {"mensaje", "horas", "id_persona", "email"}
    assert "clave" not in str(salida).lower()


def test_la_emision_de_un_responsable_queda_auditada(conn_boveda, actor):
    id_persona = _panelista(conn_boveda)
    quien_lo_pide = actor("operaciones", uid="uid-ope", email="ope@equipos.com.uy")

    portal.emitir_para_panelista(
        conn_boveda, id_persona, actor=quien_lo_pide,
        enviar=lambda canal, destino, cuerpo, **_: {"enviado": True})

    emisiones = portal.emisiones_de(conn_boveda, id_persona)
    assert len(emisiones) == 1
    assert emisiones[0]["emitido_por"] == "uid-ope"
    assert emisiones[0]["pedido_por"] == "ope@equipos.com.uy"
    assert emisiones[0]["motivo"] == portal.ALTA_CLAVE
    assert emisiones[0]["sigue_sirviendo"] is True


def test_lo_que_pide_el_propio_panelista_se_distingue_de_lo_que_pide_equipos(
        conn_boveda):
    """«Nulo» no es una explicación: el rastro tiene que decir quién lo
    pidió, y que lo haya pedido el titular es un dato, no la falta de uno."""
    id_persona = _panelista(conn_boveda)
    _pedir(conn_boveda, EMAIL)

    emision = portal.emisiones_de(conn_boveda, id_persona)[0]
    assert emision["emitido_por"] is None
    assert emision["pedido_por"] == "el propio panelista"


def test_no_se_le_emite_enlace_a_quien_no_tiene_correo(conn_boveda, actor):
    """Mandar a ningún lado y decir «listo» deja al responsable creyendo que
    resolvió algo. Es el mismo error que evita `usuarios.generar_acceso()`
    con los usuarios desactivados."""
    id_persona = personas.alta(
        conn_boveda,
        {"persona": {"documento": "R61A-SIN-MAIL", "nombre": "Sin correo"},
         "consentimientos": consentimientos(*AMBAS)})["id_persona"]

    with pytest.raises(Conflicto, match="no tiene correo"):
        portal.emitir_para_panelista(conn_boveda, id_persona, actor=actor())


def _inscribir(conn, actor, email, documento, version):
    """Una inscripción aprobable, por el mismo camino que la persona: texto
    publicado, contacto verificado, formulario enviado."""
    from panel_api import inscripciones as ins, verificacion_contacto as verif

    ins.publicar_texto(conn, "contacto_participacion", version,
                       "Texto de consentimiento.", actor=actor("dpo"))
    pedido = verif.pedir_codigo(conn, verif.EMAIL, email)
    verif.verificar(conn, verif.EMAIL, email, pedido["codigo_sin_enviar"])
    ins.inscribir(conn, {
        "persona": {"nombre": "Recién inscripta", "email": email,
                    "documento": documento},
        "acepto_consentimiento": True, "version_texto": version})
    return ins.listar(conn)[0]


def test_una_inscripcion_aprobada_sale_con_su_enlace(conn_boveda, actor):
    """R6.1.a — si no saliera solo, cada aprobación quedaría esperando que
    alguien se acuerde de mandarlo."""
    from panel_api import inscripciones as ins

    pendiente = _inscribir(conn_boveda, actor, "nueva@ejemplo.invalid",
                           "R61A-NUEVA", "v-r61a")

    salida = ins.aprobar(conn_boveda, pendiente["id"], actor("operaciones"),
                         enviar_acceso=lambda c, d, cuerpo, **_: {"enviado": True})

    assert salida["acceso_al_portal"]["estado"] == "emitido"
    emisiones = portal.emisiones_de(conn_boveda, salida["id_persona"])
    assert [e["motivo"] for e in emisiones] == [portal.ALTA_CLAVE]


def test_un_envio_que_falla_no_deshace_la_aprobacion(conn_boveda, actor):
    """Con alta y sin enlace se arregla reenviándolo desde la ficha; sin alta
    y sin enlace hay que volver a aprobar la inscripción."""
    from panel_api import inscripciones as ins

    pendiente = _inscribir(conn_boveda, actor, "otra@ejemplo.invalid",
                           "R61A-OTRA", "v-r61a-2")

    def explota(canal, destino, cuerpo, **_):
        raise RuntimeError("el proveedor de correo está caído")

    salida = ins.aprobar(conn_boveda, pendiente["id"], actor("operaciones"),
                         enviar_acceso=explota)

    assert salida["estado"] == "aprobada"
    assert salida["acceso_al_portal"]["estado"] == "error"
    assert "caído" in salida["acceso_al_portal"]["detalle"]


# ════════════════════════════════════════════════════════════════════
#  DoD 4 · Entrar, y el mensaje que no revela
# ════════════════════════════════════════════════════════════════════

def test_la_credencial_correcta_entra(conn_boveda, con_cuenta, credenciales):
    salida = portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE, credenciales)

    assert salida["id_persona"] == str(con_cuenta)
    assert salida["token_de_sesion"]
    assert salida["nueva"] is False   # el vínculo ya existía


def test_el_mensaje_no_distingue_un_correo_que_existe_de_uno_que_no(
        conn_boveda, con_cuenta, credenciales):
    """El vector más probable de esta fase, y el que la spec nombra: probar
    direcciones contra el formulario de ingreso para averiguar quién integra
    el panel."""
    errores = []
    for email, clave in [
            (EMAIL, "la-que-no-es"),             # existe, clave mal
            ("nadie@ejemplo.invalid", CLAVE),    # no existe
            ("nadie@ejemplo.invalid", "x" * 12)]:  # no existe, clave mal
        with pytest.raises(NoAutenticado) as capturado:
            portal.iniciar_sesion(conn_boveda, email, clave, credenciales)
        errores.append(capturado.value)

    textos = {str(e) for e in errores}
    assert textos == {portal.MENSAJE_CREDENCIAL_INVALIDA}
    # Ni el texto ni el detalle ni el status: si difirieran en cualquiera de
    # los tres, la diferencia alcanzaría.
    assert {e.status for e in errores} == {errores[0].status}
    assert {str(e.detalle) for e in errores} == {str(errores[0].detalle)}


def test_una_persona_que_no_es_panelista_no_entra_aunque_su_clave_sea_buena(
        conn_boveda, credenciales):
    """Alguien de Equipos con cuenta de Auth, por ejemplo. Autentica bien y
    no entra, y el mensaje es el mismo: decirle «tu contraseña está bien
    pero no sos panelista» le confirmaría a cualquiera quién no lo es."""
    credenciales.crear("empleado@equipos.com.uy", CLAVE)

    with pytest.raises(NoAutenticado) as capturado:
        portal.iniciar_sesion(conn_boveda, "empleado@equipos.com.uy", CLAVE,
                              credenciales)
    assert str(capturado.value) == portal.MENSAJE_CREDENCIAL_INVALIDA


# ════════════════════════════════════════════════════════════════════
#  DoD 5 · Los intentos fallidos se limitan
# ════════════════════════════════════════════════════════════════════

def test_los_intentos_fallidos_repetidos_se_limitan_por_correo(
        conn_boveda, con_cuenta, credenciales):
    for _ in range(portal.MAX_FALLOS_POR_CORREO_POR_HORA):
        with pytest.raises(NoAutenticado):
            portal.iniciar_sesion(conn_boveda, EMAIL, "mal", credenciales)

    with pytest.raises(Conflicto, match="dirección"):
        portal.iniciar_sesion(conn_boveda, EMAIL, "mal", credenciales)
    # Y el límite frena incluso con la contraseña buena: si no, probar hasta
    # acertar seguiría siendo posible, que es lo que el límite evita.
    with pytest.raises(Conflicto):
        portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE, credenciales)


def test_los_intentos_fallidos_repetidos_se_limitan_por_origen(
        conn_boveda, credenciales):
    """El de por correo no frena a quien prueba una dirección distinta cada
    vez, que es exactamente como se recorre un diccionario."""
    for numero in range(portal.MAX_FALLOS_POR_ORIGEN_POR_HORA):
        with pytest.raises(NoAutenticado):
            portal.iniciar_sesion(conn_boveda, f"p{numero}@ejemplo.invalid",
                                  "mal", credenciales, origen="1.2.3.4")

    with pytest.raises(Conflicto, match="dispositivo"):
        portal.iniciar_sesion(conn_boveda, "uno-mas@ejemplo.invalid", "mal",
                              credenciales, origen="1.2.3.4")


def test_adivinar_la_contrasena_no_consume_los_pedidos_de_recuperacion(
        conn_boveda, con_cuenta, credenciales):
    """Si compartieran contador, el ataque le cerraría a la víctima justo la
    puerta de salida: no podría pedir el enlace para recuperarla."""
    for _ in range(portal.MAX_FALLOS_POR_CORREO_POR_HORA):
        with pytest.raises(NoAutenticado):
            portal.iniciar_sesion(conn_boveda, EMAIL, "mal", credenciales)

    assert _pedir(conn_boveda, EMAIL)["mensaje"] == portal.RESPUESTA_DE_ENLACE


def test_el_correo_no_viaja_en_claro_al_registro_de_intentos(conn_boveda,
                                                             credenciales):
    """Un intento puede ser de alguien que no es panelista: guardar su
    dirección sería juntar datos de quien no aceptó nada."""
    with pytest.raises(NoAutenticado):
        portal.iniciar_sesion(conn_boveda, "ajeno@ejemplo.invalid", "x" * 8,
                              credenciales)

    fila = db.una(conn_boveda, "select email_hash, motivo from acceso_portal")
    assert "ajeno@ejemplo.invalid" not in fila["email_hash"]
    assert fila["motivo"] == portal.LOGIN_FALLIDO


def test_el_pedido_de_enlace_tambien_se_limita(conn_boveda):
    _panelista(conn_boveda)
    for _ in range(portal.MAX_ENLACES_POR_CORREO_POR_HORA):
        _pedir(conn_boveda, EMAIL)
    with pytest.raises(Conflicto, match="dirección"):
        _pedir(conn_boveda, EMAIL)


# ════════════════════════════════════════════════════════════════════
#  DoD 6 · Dado de baja: ni entra ni recupera
# ════════════════════════════════════════════════════════════════════

def test_un_panelista_dado_de_baja_no_entra(conn_boveda, con_cuenta,
                                            conn_semantica, credenciales):
    from panel_api import bajas

    bajas.retirar(conn_boveda, con_cuenta, actor="dpo",
                  conn_semantica=conn_semantica, credenciales=credenciales)

    with pytest.raises(NoAutenticado) as capturado:
        portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE, credenciales)
    assert str(capturado.value) == portal.MENSAJE_CREDENCIAL_INVALIDA


def test_la_credencial_de_quien_se_dio_de_baja_queda_inutilizable(
        conn_boveda, con_cuenta, conn_semantica, credenciales):
    """No alcanza con que la bóveda lo olvide: mientras la cuenta de Auth
    siga viva, la credencial sirve para cualquier cosa que acepte ese token."""
    from panel_api import bajas

    salida = bajas.retirar(conn_boveda, con_cuenta, actor="dpo",
                           conn_semantica=conn_semantica,
                           credenciales=credenciales)

    assert salida["acceso_al_portal"]["estado"] == "cortado"
    assert credenciales.buscar(EMAIL)["deshabilitada"] is True
    assert credenciales.verificar_clave(EMAIL, CLAVE) is None


def test_una_baja_hecha_desde_la_administracion_tambien_corta_el_acceso(
        conn_boveda, con_cuenta, conn_semantica, credenciales):
    """La baja la puede ejecutar el titular, el DPO o un job. Si el corte
    viviera en el camino del portal, los otros dos dejarían la credencial
    viva: por eso está en `bajas.retirar` y no en `portal.darse_de_baja`."""
    from panel_api import bajas

    uid = credenciales.buscar(EMAIL)["uid"]
    bajas.retirar(conn_boveda, con_cuenta, actor="dpo",
                  conn_semantica=conn_semantica, credenciales=credenciales)

    assert uid in credenciales.revocaciones


def test_la_baja_no_se_cae_si_firebase_esta_caido(conn_boveda, con_cuenta,
                                                  conn_semantica):
    """La bóveda no espera a ningún sistema de afuera para borrar. Lo que sí
    hace es dejar anotado qué quedó por apagar."""
    class Rota(cred.CredencialesEnMemoria):
        def deshabilitar(self, uid):
            raise RuntimeError("Firebase no responde")

    from panel_api import bajas

    salida = bajas.retirar(conn_boveda, con_cuenta, actor="dpo",
                           conn_semantica=conn_semantica, credenciales=Rota())

    assert salida["pii_borrada"] is True
    assert salida["acceso_al_portal"]["estado"] == "error"
    assert "no responde" in salida["acceso_al_portal"]["detalle"]


def test_quien_se_dio_de_baja_no_recupera_la_contrasena(
        conn_boveda, con_cuenta, conn_semantica, credenciales):
    from panel_api import bajas

    bajas.retirar(conn_boveda, con_cuenta, actor="dpo",
                  conn_semantica=conn_semantica, credenciales=credenciales)

    respuesta = _pedir(conn_boveda, EMAIL)
    # La misma respuesta de siempre —no se le dice que ya no está— y ningún
    # enlace emitido.
    assert respuesta["mensaje"] == portal.RESPUESTA_DE_ENLACE
    assert _token_de(respuesta) is None


# ════════════════════════════════════════════════════════════════════
#  DoD 7 · «Olvidé mi contraseña» responde igual
# ════════════════════════════════════════════════════════════════════

def test_la_recuperacion_responde_igual_exista_o_no_el_correo(conn_boveda):
    _panelista(conn_boveda)

    existe = _pedir(conn_boveda, EMAIL)
    no_existe = _pedir(conn_boveda, "cualquiera@ejemplo.invalid")

    assert existe["mensaje"] == no_existe["mensaje"]
    assert existe["horas"] == no_existe["horas"]
    # Lo único que difiere es el enlace de desarrollo, que en producción no
    # existe: con proveedor de envío configurado las dos respuestas son
    # idénticas campo por campo.
    assert set(no_existe) <= {"mensaje", "horas"}


def test_a_quien_no_es_panelista_no_se_le_emite_nada(conn_boveda):
    respuesta = _pedir(conn_boveda, "nadie@ejemplo.invalid")

    assert _token_de(respuesta) is None
    # Pero el intento queda registrado: es lo que hace que probar direcciones
    # consuma el límite en vez de salir gratis.
    assert db.una(conn_boveda,
                  "select count(*)::int as n from acceso_portal")["n"] == 1


def test_crear_y_recuperar_dan_exactamente_la_misma_respuesta(conn_boveda):
    """Dos textos distintos para los dos botones serían una segunda forma de
    preguntar lo mismo: «¿este correo ya tiene contraseña?»."""
    _panelista(conn_boveda)

    alta = _pedir(conn_boveda, EMAIL, motivo=portal.ALTA_CLAVE)
    recuperacion = _pedir(conn_boveda, EMAIL, motivo=portal.RECUPERACION)

    assert alta["mensaje"] == recuperacion["mensaje"]


# ════════════════════════════════════════════════════════════════════
#  DoD 8 · Cambiar la contraseña cierra las otras sesiones
# ════════════════════════════════════════════════════════════════════

def test_cambiar_la_contrasena_exige_la_actual(conn_boveda, con_cuenta,
                                               credenciales):
    """Sin esto, una sesión abierta en una máquina prestada alcanza para
    quedarse con la cuenta."""
    with pytest.raises(NoAutenticado, match="no coincide"):
        portal.cambiar_clave(conn_boveda, con_cuenta, "la-que-no-es",
                             OTRA_CLAVE, credenciales)
    assert credenciales.verificar_clave(EMAIL, CLAVE)


def test_cambiar_la_contrasena_cierra_las_otras_sesiones(conn_boveda,
                                                         con_cuenta,
                                                         credenciales):
    uid = credenciales.buscar(EMAIL)["uid"]
    credenciales.revocaciones.clear()

    salida = portal.cambiar_clave(conn_boveda, con_cuenta, CLAVE, OTRA_CLAVE,
                                  credenciales)

    assert uid in credenciales.revocaciones
    assert credenciales.verificar_clave(EMAIL, OTRA_CLAVE)
    assert credenciales.verificar_clave(EMAIL, CLAVE) is None
    # Y quien la cambió **no** se queda afuera: la revocación se lleva
    # también su sesión, así que se le devuelve una nueva. Si no, hacer lo
    # correcto se sentiría como un error.
    assert salida["token_de_sesion"]


def test_una_contrasena_demasiado_corta_se_rechaza(conn_boveda, con_cuenta,
                                                   credenciales):
    with pytest.raises(DatosInvalidos, match="al menos"):
        portal.cambiar_clave(conn_boveda, con_cuenta, CLAVE, "abc",
                             credenciales)
    assert credenciales.verificar_clave(EMAIL, CLAVE)


# ════════════════════════════════════════════════════════════════════
#  DoD 9-10 · Reautenticación para lo irreversible
# ════════════════════════════════════════════════════════════════════

ACCIONES = ("baja", "retiro de finalidad", "cambio de correo")


def _sin_clave(conn, id_persona, credenciales, conn_semantica, accion):
    """La misma acción, sin contraseña. Sirve para las tres."""
    if accion == "baja":
        return portal.darse_de_baja(conn, id_persona,
                                    conn_semantica=conn_semantica,
                                    credenciales=credenciales)
    if accion == "retiro de finalidad":
        return portal.retirar_finalidad(conn, id_persona, "uso_semantico",
                                        conn_semantica=conn_semantica,
                                        credenciales=credenciales)
    pedido = portal.pedir_verificacion_de_contacto(
        conn, id_persona, "email", "nuevo@ejemplo.invalid",
        enviar=lambda c, d, codigo, **_: {"sin_proveedor": True, "codigo": codigo})
    return portal.confirmar_contacto(conn, id_persona, "email",
                                     "nuevo@ejemplo.invalid",
                                     pedido["codigo_sin_enviar"],
                                     credenciales=credenciales)


@pytest.mark.parametrize("accion", ACCIONES)
def test_las_tres_acciones_irreversibles_piden_la_contrasena(
        conn_boveda, con_cuenta, conn_semantica, credenciales, accion):
    with pytest.raises(NoAutenticado) as capturado:
        _sin_clave(conn_boveda, con_cuenta, credenciales, conn_semantica, accion)

    assert capturado.value.detalle["motivo"] == "requiere_reautenticacion"
    # Y no pasó nada: la persona sigue entera, con su finalidad y su correo.
    fila = db.una(conn_boveda,
                  "select email from persona where id_persona = %s",
                  (str(con_cuenta),))
    assert fila and fila["email"] == EMAIL
    vigentes = {c["finalidad"] for c in portal.finalidades(conn_boveda, con_cuenta)
                if c["estado"] == "vigente"}
    assert vigentes == set(AMBAS)


@pytest.mark.parametrize("accion", ACCIONES)
def test_con_la_contrasena_mal_tampoco_pasan(conn_boveda, con_cuenta,
                                             conn_semantica, credenciales,
                                             accion):
    comunes = {"conn_semantica": conn_semantica, "credenciales": credenciales,
               "clave": "la-que-no-es"}
    with pytest.raises(NoAutenticado, match="no coincide"):
        if accion == "baja":
            portal.darse_de_baja(conn_boveda, con_cuenta, **comunes)
        elif accion == "retiro de finalidad":
            portal.retirar_finalidad(conn_boveda, con_cuenta, "uso_semantico",
                                     **comunes)
        else:
            # El código es cualquiera a propósito: la contraseña se comprueba
            # **antes**, así que un código que ni siquiera se pidió no llega
            # a mirarse. Es lo que evita quemar el código en un intento sin
            # contraseña.
            portal.confirmar_contacto(conn_boveda, con_cuenta, "email",
                                      "nuevo@ejemplo.invalid", "000000",
                                      clave="la-que-no-es",
                                      credenciales=credenciales)


def test_tras_reautenticar_la_accion_sigue_sin_volver_a_empezar(
        conn_boveda, con_cuenta, credenciales):
    """El código al correo nuevo se pide **una vez**: si reautenticar lo
    invalidara, la persona tendría que arrancar el trámite de nuevo y la
    reautenticación se sentiría un castigo."""
    pedido = portal.pedir_verificacion_de_contacto(
        conn_boveda, con_cuenta, "email", "nuevo@ejemplo.invalid",
        enviar=lambda c, d, codigo, **_: {"sin_proveedor": True, "codigo": codigo})

    # Primero sin contraseña: es lo que hace la pantalla antes de abrir el
    # modal. Falla, y el código tiene que seguir sirviendo.
    with pytest.raises(NoAutenticado):
        portal.confirmar_contacto(conn_boveda, con_cuenta, "email",
                                  "nuevo@ejemplo.invalid",
                                  pedido["codigo_sin_enviar"],
                                  credenciales=credenciales)

    salida = portal.confirmar_contacto(
        conn_boveda, con_cuenta, "email", "nuevo@ejemplo.invalid",
        pedido["codigo_sin_enviar"], clave=CLAVE, credenciales=credenciales)
    assert salida["email"] == "nuevo@ejemplo.invalid"


def test_lo_reversible_no_pide_nada(conn_boveda, con_cuenta, credenciales):
    """La sesión sirve para mirar y para lo que se deshace. Pedir la
    contraseña en cada clic la convertiría en un trámite que la gente
    aprende a saltear sin leer."""
    del credenciales
    portal.resumen_de_puntos(conn_boveda, con_cuenta)
    # Prender y apagar un canal: lo más parecido a «ejercer un derecho» que
    # hay entre lo reversible, y se deshace volviéndolo a prender.
    portal.cambiar_canal(conn_boveda, con_cuenta, "email", True)
    portal.cambiar_canal(conn_boveda, con_cuenta, "email", False)


def test_sin_proveedor_de_credenciales_la_accion_no_ocurre(conn_boveda,
                                                           con_cuenta,
                                                           conn_semantica):
    """Falla cerrado. Una baja que se ejecuta porque la comprobación no
    estaba disponible es peor que una que no se ejecuta."""
    with pytest.raises(SinPermiso, match="no se puede comprobar|no ejecuta|"
                                         "No se puede comprobar"):
        portal.darse_de_baja(conn_boveda, con_cuenta,
                             conn_semantica=conn_semantica, clave=CLAVE,
                             credenciales=None)
    assert db.una(conn_boveda,
                  "select count(*)::int as n from persona where id_persona = %s",
                  (str(con_cuenta),))["n"] == 1


def test_la_reautenticacion_tambien_cuenta_para_el_limite(conn_boveda,
                                                          con_cuenta,
                                                          credenciales):
    """Si no contara, una sesión robada sería un oráculo de contraseñas con
    intentos infinitos."""
    for _ in range(portal.MAX_FALLOS_POR_CORREO_POR_HORA):
        with pytest.raises(NoAutenticado):
            portal.reautenticar(conn_boveda, con_cuenta, "mal", credenciales)

    with pytest.raises(Conflicto, match="dirección"):
        portal.reautenticar(conn_boveda, con_cuenta, CLAVE, credenciales)


# ════════════════════════════════════════════════════════════════════
#  DoD 11 · R6.1.f · El correo nuevo pasa a ser el usuario
# ════════════════════════════════════════════════════════════════════

def test_el_correo_nuevo_pasa_a_ser_el_usuario_con_la_misma_contrasena(
        conn_boveda, con_cuenta, credenciales):
    pedido = portal.pedir_verificacion_de_contacto(
        conn_boveda, con_cuenta, "email", "nuevo@ejemplo.invalid",
        enviar=lambda c, d, codigo, **_: {"sin_proveedor": True, "codigo": codigo})
    portal.confirmar_contacto(conn_boveda, con_cuenta, "email",
                              "nuevo@ejemplo.invalid",
                              pedido["codigo_sin_enviar"], clave=CLAVE,
                              credenciales=credenciales)

    assert credenciales.verificar_clave("nuevo@ejemplo.invalid", CLAVE)
    assert credenciales.verificar_clave(EMAIL, CLAVE) is None
    # Y entra con la dirección nueva, sin volver a vincular nada: el vínculo
    # es por `uid` y el `uid` no cambió.
    assert portal.iniciar_sesion(conn_boveda, "nuevo@ejemplo.invalid", CLAVE,
                                 credenciales)["id_persona"] == str(con_cuenta)


def test_hasta_que_el_correo_nuevo_no_se_verifica_entra_con_el_anterior(
        conn_boveda, con_cuenta, credenciales):
    """Es lo que evita el modo de falla que arruinaría el acceso: un correo
    nuevo mal tipeado guardado sin verificar deja a la persona sin puerta."""
    portal.pedir_verificacion_de_contacto(
        conn_boveda, con_cuenta, "email", "con-error-de-tipeo@ejemplo.invalid",
        enviar=lambda c, d, codigo, **_: {"sin_proveedor": True, "codigo": codigo})

    assert portal.iniciar_sesion(conn_boveda, EMAIL, CLAVE,
                                 credenciales)["id_persona"] == str(con_cuenta)


# ════════════════════════════════════════════════════════════════════
#  DoD 12 · No regresión: un panelista no es un usuario de la app
# ════════════════════════════════════════════════════════════════════

def test_un_panelista_no_obtiene_permisos_de_la_administracion():
    """Lo que no cambió al cambiar la forma de entrar, y es lo que importa
    que no cambie: la contraseña abre el portal, no la administración."""
    from panel_api import auth

    actor = auth.actor_de_portal(
        {"Authorization": "Bearer x"},
        verificar_token=lambda _: {"uid": "uid-1", "email": EMAIL})

    assert actor.es_panelista is True
    assert actor.rol is None
    for permiso in auth.PERMISOS:
        assert not actor.puede(permiso), f"un panelista no puede «{permiso}»"


def test_la_sesion_del_portal_comprueba_que_el_token_no_este_revocado():
    """Sin esto, «el cambio de contraseña cierra las otras sesiones» y «la
    baja invalida las de otros dispositivos» serían ciertas recién cuando
    venciera el id token, hasta una hora después."""
    import inspect

    from panel_api import auth

    fuente = inspect.getsource(auth.actor_de_portal)
    assert "check_revoked=True" in fuente


def test_ninguna_ruta_de_clave_recibe_un_id_persona():
    """Las tres rutas de entrada corren antes de que haya sesión, así que no
    pueden resolver la persona desde el vínculo. Lo que no pueden es
    aceptarla del cliente: la sacan del token del enlace o de la credencial."""
    for metodo, expresion, _permiso, _funcion, patron in ruteo.RUTAS:
        if "/portal/" not in patron:
            continue
        assert "id_persona" not in patron, f"{metodo} {patron}"


def test_el_origen_del_limite_sale_de_la_request_y_no_del_cuerpo():
    """Un límite cuyo identificador lo elige quien lo sufre no limita nada:
    alcanza con mandar un `origen` distinto en cada intento.

    Se comprueba sobre el texto de `main.py` porque importar ese módulo
    arrastra `firebase_functions`, que no hace falta para verificar de dónde
    sale un valor. Es la misma técnica que usa `test_main.py` con la lista
    de secretos, y por el mismo motivo.
    """
    import pathlib
    import re

    fuente = (pathlib.Path(__file__).resolve().parents[2]
              / "functions" / "main.py").read_text(encoding="utf-8")
    bloque = re.search(r"def _origen_de\(req\):(.*?)\ndef ", fuente, re.S)
    assert bloque, "main.py ya no deriva el origen de la request"
    assert "X-Forwarded-For" in bloque.group(1)
    # Y se le pasa al contexto, que es lo que lo pone al alcance de las rutas.
    assert "origen=_origen_de(req)" in fuente


def test_las_rutas_del_portal_prefieren_el_origen_de_la_request():
    """Las rutas aceptan un `origen` del cuerpo —lo usan las pruebas y el
    emulador— pero el de la request gana. Si fuera al revés, declarar uno
    propio desactivaría el límite."""
    import inspect

    fuente = inspect.getsource(ruteo)
    for ruta in ("portal_pedir_enlace", "portal_entrar"):
        cuerpo = fuente.split(f"def {ruta}(")[1].split("\n@ruta")[0]
        assert "ctx.origen or" in cuerpo, ruta


def test_las_rutas_de_entrada_son_publicas_y_las_demas_no():
    publicas = {ruta for ruta in ruteo.PUBLICAS if ruta[1].startswith("/portal/")}
    assert publicas == {
        ("POST", "/portal/clave/enlace"),
        ("POST", "/portal/clave"),
        ("POST", "/portal/sesion/clave"),
    }
    assert ruteo.es_publica("POST", "/portal/clave/cambio") is False
    assert ruteo.es_publica("POST", "/portal/baja") is False
