"""Fase 6 — El portal del panelista, contra su Definition of Done.

Esta fase **da vuelta la postura de seguridad del sistema**: hasta acá la
bóveda la tocaban empleados de Equipos y un consumidor registrado; ahora
autentica a miles de externos contra el store que tiene toda la PII. Por eso
el archivo arranca por lo que no se puede ver y no por lo que sí: una sesión
mirando a otra persona, y un panelista probando si de paso le sirve para
entrar a la administración.

Lo de **entrar** —contraseñas, enlaces de alta, límites de intentos,
reautenticación— se mudó a `test_r6_1a_clave.py` cuando R6.1.a reemplazó el
enlace mágico. Acá quedó lo que esa sustitución no tocó.

Lo demás —puntos, canje, perfil, derechos— viene después, y buena parte se
apoya en mecanismos que ya existían: el saldo bloqueado de R3.5, la
verificación de contacto de R4.3, la cascada de baja de R1.3. Las pruebas de
acá comprueban que el portal los **use**, no que los reimplemente.
"""

import pytest

from panel_api import (
    atributos, auth, consentimiento, db, personas, portal, preferencias,
    premios, puntos, ruteo,
)
from panel_api.errores import Conflicto, DatosInvalidos, NoAutenticado, SinPermiso

from conftest import VERSION_TEXTO, consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")

# La contraseña de las pruebas. Una constante y no un literal suelto: las
# acciones sensibles la piden en tres lugares distintos y tienen que coincidir.
CLAVE = "contrasena-de-prueba"


# ── Escenario ────────────────────────────────────────────────────────

def _panelista(conn, documento="P6-1", email="panelista@ejemplo.invalid",
               celular="+59899000111"):
    return personas.alta(
        conn,
        {"persona": {"documento": documento, "nombre": "Panelista de prueba",
                     "email": email, "celular": celular},
         "consentimientos": consentimientos(*AMBAS)},
    )["id_persona"]


@pytest.fixture
def quien(conn_boveda):
    """Una persona con cuenta del portal ya vinculada."""
    id_persona = _panelista(conn_boveda)
    portal.vincular(conn_boveda, "uid-1", "panelista@ejemplo.invalid")
    return id_persona


@pytest.fixture
def con_clave(conn_boveda, quien, credenciales):
    """La misma persona, con una contraseña que se puede volver a escribir.

    Desde R6.1.d las tres acciones irreversibles la piden, así que probarlas
    exige que exista: sin esto, «la baja dispara la cascada» no se podría
    comprobar sin saltearse la baranda que la protege.
    """
    # Con el mismo `uid` que `quien` dejó en `cuenta_panelista`: la
    # reautenticación comprueba que la cuenta que entra sea **esa**, no
    # cualquiera que tenga el mismo correo.
    credenciales.sembrar("uid-1", "panelista@ejemplo.invalid", CLAVE)
    return quien


# ════════════════════════════════════════════════════════════════════
#  6A · El vínculo entre la cuenta y la persona
# ════════════════════════════════════════════════════════════════════
# El **acceso** —cómo se entra— dejó de vivir acá: R6.1.a lo reemplazó por
# usuario y contraseña, y sus pruebas están en `test_r6_1a_clave.py`. Lo que
# queda en este archivo es lo que esa sustitución no tocó, empezando por la
# regla que sostiene a todo el portal: una cuenta, una persona, y el
# `id_persona` sale del vínculo y nunca del pedido.

def test_el_vinculo_exige_que_el_correo_coincida(conn_boveda):
    _panelista(conn_boveda)
    with pytest.raises(SinPermiso, match="no corresponde a ningún panelista"):
        portal.vincular(conn_boveda, "uid-intruso", "otro@ejemplo.invalid")


def test_una_persona_no_puede_tener_dos_cuentas(conn_boveda, quien):
    """Si pudiera, revocar el acceso dejaría la otra puerta abierta."""
    with pytest.raises(Conflicto, match="ya tiene una cuenta"):
        portal.vincular(conn_boveda, "uid-2", "panelista@ejemplo.invalid")


def test_la_sesion_resuelve_a_su_propia_persona(conn_boveda, quien):
    assert str(portal.persona_de(conn_boveda, "uid-1")) == str(quien)


def test_una_cuenta_sin_vinculo_no_resuelve_a_nadie(conn_boveda):
    with pytest.raises(SinPermiso, match="no está vinculada"):
        portal.persona_de(conn_boveda, "uid-desconocido")


def test_ninguna_ruta_del_portal_recibe_un_id_persona(conn_boveda):
    """La garantía de R6.2 —«ninguna ruta acepta un `id_persona` del
    cliente»— no se puede probar caso por caso sin olvidarse de uno: se
    prueba sobre la lista entera de rutas."""
    del conn_boveda
    for metodo, expresion, _permiso, funcion, _req in ruteo.RUTAS:
        if "/portal/" not in expresion.pattern:
            continue
        assert "id_persona" not in expresion.pattern, (
            f"{metodo} {expresion.pattern} toma un id_persona de la URL")
        codigo = funcion.__code__
        fuente_de_la_persona = "_yo" in codigo.co_names or "persona_de" in codigo.co_names
        # Las excepciones son las tres rutas de entrada (R6.1.a), que corren
        # **antes** de que haya sesión: ahí la persona se resuelve desde el
        # token del enlace o desde la credencial, nunca desde un parámetro.
        de_entrada = any(x in expresion.pattern
                         for x in ("/clave", "/sesion"))
        assert fuente_de_la_persona or de_entrada, (
            f"{metodo} {expresion.pattern} no resuelve la persona desde la sesión")


def test_un_panelista_no_obtiene_permisos_de_la_administracion():
    """Las dos superficies comparten Firebase Auth y nada más."""
    actor = auth.actor_de_portal(
        {"Authorization": "Bearer x"},
        verificar_token=lambda _: {"uid": "uid-1", "email": "p@ejemplo.invalid"})
    assert actor.es_panelista is True
    assert actor.rol is None
    for permiso in auth.PERMISOS:
        assert not actor.puede(permiso), f"un panelista no puede «{permiso}»"


def test_un_panelista_dado_de_baja_no_entra(conn_boveda, quien, conn_semantica):
    from panel_api import bajas

    bajas.retirar(conn_boveda, quien, actor="dpo", conn_semantica=conn_semantica)
    with pytest.raises(SinPermiso):
        portal.persona_de(conn_boveda, "uid-1")


# ════════════════════════════════════════════════════════════════════
#  6B · Puntos y canje
# ════════════════════════════════════════════════════════════════════

def test_el_saldo_coincide_con_la_suma_del_ledger(conn_boveda, quien):
    puntos.registrar(conn_boveda, quien, puntos.EARN, 50,
                     motivo="participacion")
    puntos.registrar(conn_boveda, quien, puntos.AJUSTE, -20, motivo="ajuste")
    resumen = portal.resumen_de_puntos(conn_boveda, quien)
    assert resumen["saldo"] == 30 == puntos.saldo(conn_boveda, quien)
    assert len(resumen["movimientos"]) == 2


def test_el_motivo_se_explica_en_castellano(conn_boveda, quien):
    puntos.registrar(conn_boveda, quien, puntos.EARN, 50,
                     motivo="participacion")
    movimiento = portal.resumen_de_puntos(conn_boveda, quien)["movimientos"][0]
    assert movimiento["motivo"].startswith("Por responder")
    assert "participacion" not in movimiento["motivo"]


def _premio(conn, costo=100, stock=None):
    return premios.crear_premio(
        conn, {"nombre": "Voucher", "costo_puntos": costo, "stock": stock})


def test_un_canje_queda_solicitado_y_reserva_los_puntos(conn_boveda, quien):
    puntos.registrar(conn_boveda, quien, puntos.EARN, 150, motivo="x")
    premio = _premio(conn_boveda)

    canje = portal.solicitar_canje(conn_boveda, quien, premio["id"])
    assert canje["estado"] == premios.SOLICITADO
    assert puntos.saldo(conn_boveda, quien) == 50


def test_sin_saldo_el_canje_se_rechaza_sin_tocar_nada(conn_boveda, quien):
    puntos.registrar(conn_boveda, quien, puntos.EARN, 10, motivo="x")
    premio = _premio(conn_boveda)
    with pytest.raises(DatosInvalidos, match="Saldo insuficiente"):
        portal.solicitar_canje(conn_boveda, quien, premio["id"])
    assert puntos.saldo(conn_boveda, quien) == 10


def test_dos_canjes_con_saldo_para_uno_solo_dejan_uno(dsn_boveda, conn_boveda,
                                                      quien):
    """El sobregiro por concurrencia es el modo de falla que importa: dos
    pedidos leen el mismo saldo, los dos concluyen que alcanza, y el saldo
    termina negativo sin que ninguna transacción haya hecho nada malo."""
    puntos.registrar(conn_boveda, quien, puntos.EARN, 100, motivo="x")
    premio = _premio(conn_boveda)
    conn_boveda.commit()

    import psycopg
    from psycopg.rows import dict_row

    otra = psycopg.connect(dsn_boveda, row_factory=dict_row)
    try:
        portal.solicitar_canje(conn_boveda, quien, premio["id"])
        conn_boveda.commit()
        with pytest.raises(DatosInvalidos, match="Saldo insuficiente"):
            portal.solicitar_canje(otra, quien, premio["id"])
    finally:
        otra.rollback()
        otra.close()
    assert puntos.saldo(conn_boveda, quien) == 0


def test_el_portal_no_entrega_nada_sin_aprobacion(conn_boveda, quien, actor):
    """El portal **solicita**; aprobar y entregar los hace Equipos."""
    puntos.registrar(conn_boveda, quien, puntos.EARN, 150, motivo="x")
    premio = _premio(conn_boveda)
    canje = portal.solicitar_canje(conn_boveda, quien, premio["id"])

    visto = portal.mis_canjes(conn_boveda, quien)[0]
    assert visto["estado"] == "solicitado"
    assert "esperando revisión" in visto["estado_texto"]

    aprobado = premios.resolver(conn_boveda, canje["id"], premios.APROBADO,
                                actor=actor("operaciones"))
    assert aprobado["estado"] == "aprobado"
    visto = portal.mis_canjes(conn_boveda, quien)[0]
    assert visto["aprobado_en"] and visto["resuelto_en"] is None
    assert "falta entregártelo" in visto["estado_texto"]

    entregado = premios.resolver(conn_boveda, canje["id"], premios.ENTREGADO,
                                 actor=actor("operaciones"))
    assert entregado["estado"] == "entregado"


def test_cancelar_devuelve_los_puntos_como_movimiento_nuevo(conn_boveda, quien,
                                                            actor):
    puntos.registrar(conn_boveda, quien, puntos.EARN, 150, motivo="x")
    premio = _premio(conn_boveda)
    canje = portal.solicitar_canje(conn_boveda, quien, premio["id"])
    antes = len(puntos.movimientos(conn_boveda, quien))

    premios.resolver(conn_boveda, canje["id"], premios.CANCELADO,
                     actor=actor("operaciones"))
    assert puntos.saldo(conn_boveda, quien) == 150
    # El descuento original sigue en el ledger: ocurrió.
    assert len(puntos.movimientos(conn_boveda, quien)) == antes + 1


# ════════════════════════════════════════════════════════════════════
#  6C · Perfil
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def editable(conn_boveda, actor):
    atributo = atributos.crear(
        conn_boveda,
        {"clave": "ocupacion", "etiqueta": "Ocupación", "tipo": "categorico",
         "categorias": [{"clave": "empleado", "etiqueta": "Empleado"},
                        {"clave": "estudiante", "etiqueta": "Estudiante"}]},
        actor=actor("admin"))
    return atributos.editar(conn_boveda, atributo["id"],
                            {"editable_por_panelista": True},
                            actor=actor("admin"))


def test_solo_se_muestran_los_atributos_editables(conn_boveda, quien, editable):
    claves = {a["clave"] for a in portal.perfil(conn_boveda, quien)["atributos"]}
    assert claves == {"ocupacion"}
    assert "sexo" not in claves, "sexo no está marcado editable"


def test_un_atributo_no_editable_se_rechaza_aunque_venga_forzado(conn_boveda,
                                                                 quien, editable):
    """La pantalla no es la autoridad: el pedido puede venir de cualquier
    lado y lo que decide es la lista blanca."""
    with pytest.raises(SinPermiso, match="no se pueden editar"):
        portal.editar_atributos(conn_boveda, quien, {"sexo": "F"})


def test_un_derivado_no_puede_marcarse_editable(conn_boveda, actor):
    """Su valor se calcula: ofrecer editarlo prometería algo que el esquema
    no va a cumplir."""
    tramo = atributos.obtener(conn_boveda, "tramo_etario")
    with pytest.raises(Exception, match="derivado"):
        atributos.editar(conn_boveda, tramo["id"],
                         {"editable_por_panelista": True}, actor=actor("admin"))
    conn_boveda.rollback()


def test_un_cambio_del_panelista_queda_con_su_origen(conn_boveda, quien,
                                                     editable):
    portal.editar_atributos(conn_boveda, quien, {"ocupacion": "empleado"})
    fila = db.una(
        conn_boveda,
        """select pa.origen, c.clave as valor
             from persona_atributo pa
             join atributo_demografico a on a.id = pa.atributo_id
             left join atributo_categoria c on c.id = pa.categoria_id
            where pa.id_persona = %s and a.clave = 'ocupacion'""",
        (str(quien),))
    assert fila["origen"] == "panelista"
    assert fila["valor"] == "empleado"


def test_una_ingesta_posterior_no_pisa_lo_que_dijo_la_persona(conn_boveda,
                                                              quien, editable):
    """La jerarquía es panelista > operador > archivo. Nadie sabe mejor que
    la persona en qué trabaja."""
    portal.editar_atributos(conn_boveda, quien, {"ocupacion": "empleado"})

    resultado = personas.completar_desde_archivo(
        conn_boveda, quien, {"ocupacion": "estudiante"}, origen="ingesta")
    assert [d["campo"] for d in resultado["discrepancias"]] == ["ocupacion"]

    valores = {v["clave"]: v["valor"]
               for v in atributos.valores_de(conn_boveda, quien)}
    assert valores["ocupacion"] == "empleado"


def test_el_cambio_crea_vigencia_nueva_y_conserva_la_anterior(conn_boveda,
                                                              quien, editable):
    portal.editar_atributos(conn_boveda, quien, {"ocupacion": "estudiante"})
    conn_boveda.commit()
    portal.editar_atributos(conn_boveda, quien, {"ocupacion": "empleado"})

    historial = atributos.historial_de(conn_boveda, quien, clave="ocupacion")
    assert len(historial) == 2, "el valor anterior se conserva, no se pisa"


# ── R6.6 · Contacto ─────────────────────────────────────────────────

def test_el_correo_nuevo_se_verifica_antes_de_reemplazar(conn_boveda, con_clave,
                                                        credenciales):
    quien = con_clave
    pedido = portal.pedir_verificacion_de_contacto(
        conn_boveda, quien, "email", "nuevo@ejemplo.invalid",
        enviar=lambda c, d, codigo, **_: {"sin_proveedor": True, "codigo": codigo})
    # Hasta acá el correo viejo sigue siendo el de la ficha: si el nuevo
    # estuviera mal tipeado, la persona perdería la puerta de entrada.
    assert db.una(conn_boveda, "select email from persona where id_persona = %s",
                  (str(quien),))["email"] == "panelista@ejemplo.invalid"

    portal.confirmar_contacto(conn_boveda, quien, "email",
                              "nuevo@ejemplo.invalid",
                              pedido["codigo_sin_enviar"],
                              clave=CLAVE, credenciales=credenciales)
    assert db.una(conn_boveda, "select email from persona where id_persona = %s",
                  (str(quien),))["email"] == "nuevo@ejemplo.invalid"


def test_un_celular_sin_verificar_no_habilita_whatsapp(conn_boveda, quien):
    estado = {c["canal"]: c for c in portal.canales(conn_boveda, quien)}
    assert estado[preferencias.WHATSAPP]["puede_activar"] is False
    with pytest.raises(Conflicto, match="celular verificado"):
        portal.cambiar_canal(conn_boveda, quien, preferencias.WHATSAPP, True)


# ════════════════════════════════════════════════════════════════════
#  6D · Derechos
# ════════════════════════════════════════════════════════════════════

def test_revocar_un_canal_no_afecta_a_los_otros_ni_implica_baja(conn_boveda,
                                                                quien):
    preferencias.otorgar(conn_boveda, quien, preferencias.EMAIL)
    preferencias.otorgar(conn_boveda, quien, preferencias.TELEFONO)

    portal.cambiar_canal(conn_boveda, quien, preferencias.EMAIL, False)

    estado = {c["canal"]: c for c in portal.canales(conn_boveda, quien)}
    assert estado[preferencias.EMAIL]["activo"] is False
    assert estado[preferencias.TELEFONO]["activo"] is True
    # Y sigue siendo panelista: revocar un canal no es darse de baja.
    assert db.una(conn_boveda, "select estado from persona where id_persona = %s",
                  (str(quien),))["estado"] == "activa"


def test_retirar_uso_semantico_deja_el_panel_intacto(conn_boveda, con_clave,
                                                     conn_semantica,
                                                     credenciales):
    quien = con_clave
    portal.retirar_finalidad(conn_boveda, quien, "uso_semantico",
                             conn_semantica=conn_semantica, clave=CLAVE,
                             credenciales=credenciales)

    vigentes = {c["finalidad"] for c in portal.finalidades(conn_boveda, quien)
                if c["estado"] == "vigente"}
    assert "uso_semantico" not in vigentes
    assert "contacto_participacion" in vigentes
    assert db.una(conn_boveda, "select estado from persona where id_persona = %s",
                  (str(quien),))["estado"] == "activa"


def test_retirar_una_finalidad_desconocida_se_rechaza(conn_boveda, con_clave,
                                                      credenciales):
    """Y se rechaza **antes** de pedir la contraseña: una finalidad que no
    existe es un error de programa, no un intento de hacer algo sensible."""
    with pytest.raises(DatosInvalidos, match="Finalidad desconocida"):
        portal.retirar_finalidad(conn_boveda, con_clave, "telepatia",
                                 clave=CLAVE, credenciales=credenciales)


def test_antes_de_la_baja_se_muestra_lo_que_se_pierde(conn_boveda, quien):
    """La pérdida de puntos está decidida; el problema sería que apareciera
    después de confirmar."""
    puntos.registrar(conn_boveda, quien, puntos.EARN, 150, motivo="x")
    premio = _premio(conn_boveda)
    portal.solicitar_canje(conn_boveda, quien, premio["id"])

    previo = portal.previo_a_la_baja(conn_boveda, quien)
    assert previo["saldo_que_se_pierde"] == 50
    assert len(previo["canjes_que_se_cancelan"]) == 1
    assert any("50 punto" in a for a in previo["advertencias"])
    assert any("pedido(s) de premio" in a for a in previo["advertencias"])


def test_la_baja_dispara_la_cascada_y_deja_la_lapida(conn_boveda, con_clave,
                                                     conn_semantica,
                                                     credenciales):
    quien = con_clave
    salida = portal.darse_de_baja(conn_boveda, quien,
                                  conn_semantica=conn_semantica,
                                  clave=CLAVE, credenciales=credenciales)
    assert salida["pii_borrada"] is True
    assert "se está ejecutando" in salida["mensaje"]
    assert db.una(conn_boveda,
                  "select count(*)::int as n from persona_borrada "
                  " where id_persona = %s", (str(quien),))["n"] == 1
    assert db.una(conn_boveda,
                  "select count(*)::int as n from persona where id_persona = %s",
                  (str(quien),))["n"] == 0


def test_tras_la_baja_el_acceso_se_corta(conn_boveda, con_clave, conn_semantica,
                                         credenciales):
    quien = con_clave
    portal.darse_de_baja(conn_boveda, quien, conn_semantica=conn_semantica,
                         clave=CLAVE, credenciales=credenciales)
    with pytest.raises(SinPermiso):
        portal.persona_de(conn_boveda, "uid-1")
    # Y la cuenta se fue con la persona: no queda un vínculo colgando que
    # alguien pueda reutilizar.
    assert db.una(conn_boveda,
                  "select count(*)::int as n from cuenta_panelista "
                  " where uid = 'uid-1'")["n"] == 0
