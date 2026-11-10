"""Fase 4 · Bloque 4A — Contacto: R4.3, R4.4 y R4.5.

Los seis puntos del Definition of Done del bloque, más lo que hace falta para
que esos seis signifiquen algo.

Dos cosas que este archivo cuida especialmente:

**La regla de los dos ejes** (R4.4) se prueba por sus cuatro caras: falta el
consentimiento, falta la preferencia, falta el celular, y el celular está pero
es inválido. Son cuatro motivos distintos porque se arreglan distinto, y un
envío que los mezclara en «no se pudo» sería inútil para quien tiene que
resolverlos.

**Las pruebas de no regresión de los tres caminos de alta**: quien no declara
canales tiene que seguir enrolándose exactamente igual que antes. El riesgo
real de R4.4 no es que los canales no se registren, es que agregar la captura
rompa un alta que venía funcionando.
"""

import json

import pytest

from panel_api import (
    consentimiento, db, encuestas, inscripciones as ins, mensajes, paneles,
    personas, preferencias, sav, verificacion_contacto as verificacion, whatsapp,
)
from panel_api.errores import Conflicto, DatosInvalidos

from conftest import VERSION_TEXTO, consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")
VERSION_OPTIN = "optin-whatsapp-2026-09"


# ── Ayudas ───────────────────────────────────────────────────────────

def _persona(conn, documento, celular=None, canales=(), finalidades=AMBAS,
             **datos):
    cuerpo = {
        "persona": {"documento": documento, "nombre": f"Panelista {documento}",
                    "celular": celular, **datos},
        "consentimientos": consentimientos(*finalidades),
    }
    if canales:
        cuerpo["canales"] = list(canales)
        cuerpo["version_texto_canales"] = VERSION_OPTIN
    return personas.alta(conn, cuerpo)


def _verificar(conn, canal, destino):
    pedido = verificacion.pedir_codigo(conn, canal, destino)
    return verificacion.verificar(conn, canal, destino,
                                  pedido["codigo_sin_enviar"])


def _ola_flow(conn, nombre="Ola WhatsApp", plantilla="invitacion_ola",
              pedir=None, convocar=True):
    """Una encuesta en campo con un mensaje de WhatsApp Flow (D87).

    Se elige **solo la plantilla**: el idioma y el `flow_id` salen de ella.
    Por eso guardar el mensaje toca Meta, y por eso hay que inyectarle la
    respuesta. Sin `convocar`, la encuesta queda en borrador y la convoca
    cada prueba.
    """
    panel = paneles.crear(conn, "Panel")
    encuesta = encuestas.crear(conn, panel["id"], nombre)
    mensaje = mensajes.guardar(conn, encuesta["id"],
                               {"nombre": "WhatsApp Flow", "plantilla": plantilla},
                               entorno=ENTORNO_META, pedir=pedir or _meta_ok)
    if convocar:
        encuestas.cambiar_estado(conn, encuesta["id"], "en_campo")
    conn.commit()
    return panel, encuestas.obtener(conn, encuesta["id"]), mensaje


class _MetaQueAnota:
    """Meta de mentira para enviar: anota cada mensaje, da un id distinto a
    cada uno (el índice de `envio.id_mensaje_meta` es único) y rechaza los
    números de `rechazar`."""

    def __init__(self, rechazar=()):
        self.enviados = []
        self.rechazar = set(rechazar)

    def __call__(self, url, datos=None, token=None, metodo=None):
        if "/messages" in url:
            if datos["to"] in self.rechazar:
                raise Conflicto("Número inválido para WhatsApp.", {"status": 400})
            self.enviados.append(datos)
            return {"messages": [{"id": f"wamid.{id(self)}.{len(self.enviados)}"}]}
        return _meta_ok(url, datos, token, metodo)


def _enviar_flow(conn, encuesta, mensaje, meta, publico="no_enviado"):
    """Lo que hace la tarea de Cloud Tasks con un lote: el mismo
    `procesar_lote` que cualquier mensaje."""
    ids = mensajes.publico(conn, encuesta["id"], mensaje["id"], publico)
    return mensajes.procesar_lote(
        conn, {"encuesta_id": encuesta["id"], "mensaje_id": mensaje["id"],
               "publico": publico},
        [{"id_persona": i} for i in ids], entorno=ENTORNO_META, pedir=meta)


def _plantilla(nombre="invitacion_ola", idioma="es", estado="APPROVED",
               flow_id="FLOW-1"):
    """Una plantilla como la devuelve Meta: el idioma y el Flow van adentro."""
    botones = [{"type": "FLOW", "text": "Responder", "flow_id": flow_id}] \
        if flow_id else [{"type": "URL", "text": "Ver", "url": "https://x"}]
    return {
        "name": nombre, "language": idioma, "status": estado,
        "category": "MARKETING",
        "components": [
            {"type": "BODY", "text": "Te invitamos a responder."},
            {"type": "BUTTONS", "buttons": botones},
        ],
    }


def _meta_ok(url, datos=None, token=None, metodo=None):
    """Una Meta que dice que sí a todo. Las llamadas a la API se inyectan
    para no salir a la red: lo que se prueba es nuestra lógica, no la de
    Meta."""
    if "message_templates" in url:
        return {"data": [_plantilla()]}
    if "/messages" in url:
        return {"messages": [{"id": "wamid.TEST"}]}
    return {"id": "FLOW-1", "name": "Invitación", "status": "PUBLISHED"}


ENTORNO_META = {"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "p",
                "WHATSAPP_WABA_ID": "w"}


# ── R4.4 · La regla de los dos ejes ──────────────────────────────────

def test_para_enviar_hacen_falta_los_dos_ejes(conn_boveda):
    """Consentimiento de finalidad y preferencia de canal responden preguntas
    distintas: «¿puedo contactarla?» y «¿por dónde?». Tener una sola no
    alcanza."""
    completa = _persona(conn_boveda, "1-1", celular="099111111",
                        canales=["whatsapp"])["id_persona"]
    sin_preferencia = _persona(conn_boveda, "1-2", celular="099222222")["id_persona"]
    sin_consentimiento = _persona(
        conn_boveda, "1-3", celular="099333333", canales=["whatsapp"],
        finalidades=("uso_semantico",))["id_persona"]

    contactables, excluidos = preferencias.filtrar_contactables(
        conn_boveda, [completa, sin_preferencia, sin_consentimiento],
        preferencias.WHATSAPP)

    assert contactables == [completa]
    assert excluidos["sin_preferencia_whatsapp"] == [sin_preferencia]
    assert excluidos["sin_consentimiento"] == [sin_consentimiento]


def test_los_cuatro_motivos_de_exclusion_se_informan_por_separado(conn_boveda):
    """Se arreglan distinto: a uno hay que pedirle el consentimiento, a otro
    ofrecerle el canal, a otro cargarle el celular. Un «no se pudo» agrupado
    no le sirve a nadie.

    Los dos motivos de celular solo aparecen **después** de activar la
    preferencia: sin un número válido no se puede activar, así que quien nunca
    tuvo uno cae en `sin_preferencia`. Lo que se prueba acá es el caso real:
    alguien que aceptó WhatsApp y a quien después le borraron o le rompieron
    el celular en la ficha.
    """
    sin_celular = _persona(conn_boveda, "2-1", celular="099444444",
                           canales=["whatsapp"])["id_persona"]
    invalido = _persona(conn_boveda, "2-2", celular="099555444",
                        canales=["whatsapp"])["id_persona"]
    db.ejecutar(conn_boveda, "update persona set celular = null where id_persona = %s",
                (sin_celular,))
    db.ejecutar(conn_boveda, "update persona set celular = %s where id_persona = %s",
                ("no es un teléfono", invalido))

    _, excluidos = preferencias.filtrar_contactables(
        conn_boveda, [sin_celular, invalido], preferencias.WHATSAPP)

    assert excluidos["sin_celular"] == [sin_celular]
    assert excluidos["celular_invalido"] == [invalido]


def test_whatsapp_no_se_activa_sin_celular_valido(conn_boveda):
    id_persona = _persona(conn_boveda, "3-1")["id_persona"]
    with pytest.raises(Conflicto, match="formato internacional"):
        preferencias.otorgar(conn_boveda, id_persona, preferencias.WHATSAPP)


def test_revocar_un_canal_no_toca_los_otros(conn_boveda):
    """Es la mitigación mínima del riesgo de cumplimiento: sin canal de
    entrada, un «STOP» en WhatsApp no llega al sistema, así que la revocación
    tiene que poder hacerse a mano y ser precisa."""
    id_persona = _persona(conn_boveda, "4-1", celular="099555555",
                          canales=["whatsapp", "email"])["id_persona"]

    preferencias.revocar(conn_boveda, id_persona, preferencias.WHATSAPP)

    assert preferencias.acepta(conn_boveda, id_persona, preferencias.WHATSAPP) is False
    assert preferencias.acepta(conn_boveda, id_persona, preferencias.EMAIL) is True


def test_el_optin_de_whatsapp_guarda_con_que_texto_se_obtuvo(conn_boveda):
    """Sin la versión del texto, «aceptó recibir WhatsApp» es una afirmación
    sin respaldo, y es justo lo que hay que poder demostrar ante Meta."""
    id_persona = _persona(conn_boveda, "5-1", celular="099666666",
                          canales=["whatsapp"])["id_persona"]
    canal = next(c for c in preferencias.listar(conn_boveda, id_persona)
                 if c["canal"] == "whatsapp")
    assert canal["version_texto"] == VERSION_OPTIN
    assert canal["origen"] == "alta_manual"


# ── R4.4 · E.164 y no regresión de los tres caminos de alta ──────────

@pytest.mark.parametrize("crudo,esperado", [
    ("099 123 456", "+59899123456"),
    ("+598 99 123 456", "+59899123456"),
    ("00598 99123456", "+59899123456"),
    ("59899123456", "+59899123456"),
    ("no es un teléfono", None),
    ("", None),
])
def test_el_celular_se_normaliza_a_e164(crudo, esperado):
    assert preferencias.normalizar_celular(crudo) == esperado


def test_el_alta_manual_guarda_el_celular_en_e164(conn_boveda):
    id_persona = _persona(conn_boveda, "6-1", celular="099 777 888")["id_persona"]
    fila = db.una(conn_boveda, "select celular from persona where id_persona = %s",
                  (id_persona,))
    assert fila["celular"] == "+59899777888"


def test_un_celular_que_no_se_puede_normalizar_no_voltea_el_alta(conn_boveda):
    """El resto de los datos de esa persona sirven igual. Lo único que no va a
    poder es activar WhatsApp, que es exactamente lo correcto."""
    resultado = _persona(conn_boveda, "6-2", celular="123")
    assert resultado["estado"] == "creada"
    fila = db.una(conn_boveda, "select celular from persona where id_persona = %s",
                  (resultado["id_persona"],))
    assert fila["celular"] == "123", "se guarda como vino"


def test_quien_no_declara_canales_se_enrola_igual_que_antes(conn_boveda):
    """No regresión del alta manual: el riesgo de R4.4 no es que los canales
    no se registren, es que agregar la captura rompa lo que funcionaba."""
    resultado = _persona(conn_boveda, "7-1", celular="099888999")
    assert resultado["estado"] == "creada"
    assert "canales" not in resultado
    assert preferencias.listar(conn_boveda, resultado["id_persona"]) == []


def test_un_canal_que_no_se_puede_activar_no_voltea_el_alta(conn_boveda):
    """Al revés, un celular mal tipeado impediría enrolar a alguien, que es
    peor que quedarse sin un canal."""
    resultado = _persona(conn_boveda, "7-2", canales=["whatsapp", "email"])
    assert resultado["estado"] == "creada"
    assert resultado["canales"]["activadas"] == ["email"]
    assert resultado["canales"]["rechazadas"][0]["canal"] == "whatsapp"


def test_la_ingesta_registra_los_canales_que_evidencia_el_archivo(conn_boveda):
    """Mismo mecanismo que la evidencia de consentimiento, y a propósito: es
    la misma pregunta —«¿qué respondió en campo?»— sobre otro eje."""
    resultado = sav.crear_individuos(
        conn_boveda,
        [{"ID": "R-1", "NOM": "Ana Prueba", "DOC": "8-1", "CEL": "099121212",
          "CONS": "1", "OPTIN": "1"}],
        {"nombre": "NOM", "documento": "DOC", "celular": "CEL"},
        origen="dooblo", columna_id="ID",
        evidencia_consentimiento={
            "contacto_participacion": {"variable": "CONS", "valor_afirmativo": "1",
                                       "version_texto": "campo-2026-09"}},
        evidencia_canales={
            "whatsapp": {"variable": "OPTIN", "valor_afirmativo": "1",
                         "version_texto": VERSION_OPTIN}},
    )
    id_persona = resultado["creados"][0]["id_persona"]
    assert resultado["preferencias_registradas"]["whatsapp"] == 1
    assert preferencias.acepta(conn_boveda, id_persona, preferencias.WHATSAPP)


def test_sin_declarar_canales_la_ingesta_crea_gente_no_contactable(conn_boveda):
    """Un opt-in que nadie evidenció no se inventa. La gente se crea y
    simplemente no es contactable hasta que alguien lo registre."""
    resultado = sav.crear_individuos(
        conn_boveda,
        [{"ID": "R-1", "NOM": "Ana Prueba", "DOC": "9-1", "CONS": "1"}],
        {"nombre": "NOM", "documento": "DOC"},
        origen="dooblo", columna_id="ID",
        evidencia_consentimiento={
            "contacto_participacion": {"variable": "CONS", "valor_afirmativo": "1",
                                       "version_texto": "campo-2026-09"}},
    )
    id_persona = resultado["creados"][0]["id_persona"]
    assert resultado["preferencias_registradas"] == {}
    assert preferencias.listar(conn_boveda, id_persona) == []


# ── R4.3 · Verificación de contacto ──────────────────────────────────

@pytest.fixture
def con_texto(conn_boveda, actor):
    ins.publicar_texto(conn_boveda, "contacto_participacion", "2026-09",
                       "Texto de prueba.", actor("admin"))
    return actor("admin")


def _envio(**extra):
    cuerpo = {"persona": {"nombre": "Ana Pérez", "email": "ana@ejemplo.uy",
                          "documento": "1111111"},
              "acepto_consentimiento": True}
    cuerpo.update(extra)
    return cuerpo


def test_una_inscripcion_sin_verificar_no_llega_a_la_cola(conn_boveda, con_texto):
    """El punto del DoD. Un contacto sin verificar significa que quien completó
    el formulario no probó tener acceso a ese correo: puede ser inventado o
    —peor— de otra persona."""
    with pytest.raises(Conflicto, match="verificamos tu correo"):
        ins.inscribir(conn_boveda, _envio())
    assert ins.listar(conn_boveda) == []


def test_con_el_contacto_verificado_la_inscripcion_entra(conn_boveda, con_texto):
    _verificar(conn_boveda, verificacion.EMAIL, "ana@ejemplo.uy")
    ins.inscribir(conn_boveda, _envio())

    pendiente = ins.listar(conn_boveda)[0]
    assert pendiente["email_verificado"] is True


def test_el_celular_declarado_tambien_se_verifica(conn_boveda, con_texto):
    """Un celular verificado es lo que permite enviar por WhatsApp sin quemar
    la reputación del número emisor."""
    _verificar(conn_boveda, verificacion.EMAIL, "ana@ejemplo.uy")
    cuerpo = _envio(persona={"nombre": "Ana Pérez", "email": "ana@ejemplo.uy",
                             "celular": "099131313"})
    with pytest.raises(Conflicto, match="verificamos tu celular"):
        ins.inscribir(conn_boveda, cuerpo)

    _verificar(conn_boveda, verificacion.CELULAR, "099131313")
    ins.inscribir(conn_boveda, cuerpo)
    assert ins.listar(conn_boveda)[0]["celular_verificado"] is True


def test_un_codigo_equivocado_no_verifica_y_cuenta_el_intento(conn_boveda):
    verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL, "x@ejemplo.uy")
    with pytest.raises(DatosInvalidos, match="no coincide"):
        verificacion.verificar(conn_boveda, verificacion.EMAIL, "x@ejemplo.uy",
                               "000000")
    assert not verificacion.esta_verificado(
        conn_boveda, verificacion.EMAIL, "x@ejemplo.uy")


def test_los_intentos_se_agotan(conn_boveda):
    """Sin esto, seis dígitos se adivinan por fuerza bruta en minutos."""
    verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL, "y@ejemplo.uy")
    for _ in range(verificacion.MAX_INTENTOS - 1):
        with pytest.raises(DatosInvalidos):
            verificacion.verificar(conn_boveda, verificacion.EMAIL,
                                   "y@ejemplo.uy", "000000")
    with pytest.raises(Conflicto, match="Se agotaron los intentos"):
        verificacion.verificar(conn_boveda, verificacion.EMAIL, "y@ejemplo.uy",
                               "000000")


def test_un_codigo_vencido_no_sirve(conn_boveda):
    verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL, "z@ejemplo.uy")
    db.ejecutar(conn_boveda,
                "update verificacion_contacto set vence_en = now() - interval '1 minute'")
    conn_boveda.commit()
    with pytest.raises(Conflicto, match="venció"):
        verificacion.verificar(conn_boveda, verificacion.EMAIL, "z@ejemplo.uy",
                               "123456")


def test_el_codigo_no_se_guarda_en_claro(conn_boveda):
    """Un código en claro en la base es una credencial de un solo uso al
    alcance de cualquiera que lea la tabla."""
    pedido = verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL,
                                       "w@ejemplo.uy")
    fila = db.una(conn_boveda, "select codigo_hash from verificacion_contacto "
                               "order by id desc limit 1")
    assert pedido["codigo_sin_enviar"] not in fila["codigo_hash"]
    assert len(fila["codigo_hash"]) == 64


def test_una_verificacion_sirve_para_una_sola_inscripcion(conn_boveda, con_texto):
    """Sin consumirla, el mismo código serviría para inscribir a diez personas
    distintas con el mismo contacto."""
    _verificar(conn_boveda, verificacion.EMAIL, "ana@ejemplo.uy")
    ins.inscribir(conn_boveda, _envio())
    with pytest.raises(Conflicto, match="verificamos tu correo"):
        ins.inscribir(conn_boveda, _envio(
            persona={"nombre": "Otra", "email": "ana@ejemplo.uy",
                     "documento": "2222222"}))


def test_se_limita_la_tasa_de_codigos_por_destino(conn_boveda):
    """El freno que protege a la persona del otro lado: sin él, la landing
    sirve para bombardear a un número ajeno."""
    for _ in range(verificacion.MAX_POR_DESTINO_POR_HORA):
        verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL, "spam@ejemplo.uy")
    with pytest.raises(Conflicto, match="demasiados códigos para este contacto"):
        verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL, "spam@ejemplo.uy")


def test_se_limita_la_tasa_de_codigos_por_origen(conn_boveda):
    for i in range(verificacion.MAX_POR_ORIGEN_POR_HORA):
        verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL,
                                  f"a{i}@ejemplo.uy", origen="1.2.3.4")
    with pytest.raises(Conflicto, match="demasiados códigos desde este dispositivo"):
        verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL,
                                  "otro@ejemplo.uy", origen="1.2.3.4")


def test_la_ip_no_se_guarda_en_claro(conn_boveda):
    """Para contar envíos alcanza con saber que dos vinieron del mismo lado."""
    verificacion.pedir_codigo(conn_boveda, verificacion.EMAIL, "ip@ejemplo.uy",
                              origen="200.40.1.2")
    fila = db.una(conn_boveda, "select origen_hash from verificacion_contacto "
                               "order by id desc limit 1")
    assert "200.40" not in (fila["origen_hash"] or "")


# ── R4.3 · Desafío anti-automatización ───────────────────────────────

def test_un_envio_automatizado_se_bloquea():
    """El punto del DoD. Con proveedor configurado, un token que el servicio
    rechaza no deja emitir el código."""
    from panel_api import desafio

    entorno = {"DESAFIO_PROVEEDOR": "turnstile", "DESAFIO_SECRETO": "s"}
    with pytest.raises(Conflicto, match="desafío"):
        desafio.validar("token-de-bot", entorno=entorno,
                        pedir=lambda url, datos: {"success": False,
                                                  "error-codes": ["invalid-input"]})
    # Y un token válido pasa.
    assert desafio.validar("token-humano", entorno=entorno,
                           pedir=lambda url, datos: {"success": True})["validado"]


def test_sin_token_el_desafio_rechaza():
    from panel_api import desafio

    with pytest.raises(Conflicto, match="desafío"):
        desafio.validar(None, entorno={"DESAFIO_PROVEEDOR": "turnstile",
                                       "DESAFIO_SECRETO": "s"})


def test_sin_proveedor_el_desafio_no_bloquea_pero_lo_dice():
    """Una landing sin desafío es una decisión que alguien tiene que tomar a
    sabiendas, no un olvido silencioso."""
    from panel_api import desafio

    salida = desafio.validar("lo que sea", entorno={})
    assert salida["validado"] is False
    assert "Sin proveedor" in salida["aviso"]
    assert desafio.diagnostico({})["avisos"]


# ── R4.3 · Dedup en la aprobación ────────────────────────────────────

def test_quien_aprueba_ve_los_candidatos_parecidos(conn_boveda, con_texto):
    """La landing es el único camino donde alguien se inscribe solo: es donde
    más probable es que la misma persona se anote dos veces con datos
    levemente distintos."""
    existente = _persona(conn_boveda, "10-1", celular="099141414",
                         email="conocida@ejemplo.uy",
                         nombre="Ana Pérez")["id_persona"]

    _verificar(conn_boveda, verificacion.EMAIL, "otra@ejemplo.uy")
    _verificar(conn_boveda, verificacion.CELULAR, "099141414")
    ins.inscribir(conn_boveda, _envio(persona={
        "nombre": "Ana Pérez", "email": "otra@ejemplo.uy",
        "celular": "099141414"}))

    pendiente = ins.obtener(conn_boveda, ins.listar(conn_boveda)[0]["id"])
    candidato = pendiente["candidatos"][0]
    assert candidato["id_persona"] == existente
    assert "celular" in candidato["coincide_por"]
    # Se propone, no se fusiona: la coincidencia por celular no es documento.
    assert candidato["resuelve_solo"] is False


def test_el_documento_exacto_se_resuelve_solo(conn_boveda, con_texto):
    """Comportamiento actual de R1.2, sin cambios."""
    existente = _persona(conn_boveda, "11-1", nombre="Ana Pérez")["id_persona"]
    _verificar(conn_boveda, verificacion.EMAIL, "ana@ejemplo.uy")
    ins.inscribir(conn_boveda, _envio(persona={
        "nombre": "Ana Pérez", "email": "ana@ejemplo.uy", "documento": "11-1"}))

    pendiente = ins.obtener(conn_boveda, ins.listar(conn_boveda)[0]["id"])
    assert pendiente["candidatos"][0]["resuelve_solo"] is True
    assert pendiente["id_persona_previa"] == existente


def test_la_fusion_la_declara_quien_aprueba(conn_boveda, con_texto, actor):
    """Un homónimo fusionado no se deshace, así que la decisión es humana."""
    existente = _persona(conn_boveda, "12-1", celular="099151515",
                         nombre="Ana Pérez")["id_persona"]
    _verificar(conn_boveda, verificacion.EMAIL, "ana@ejemplo.uy")
    ins.inscribir(conn_boveda, _envio(persona={
        "nombre": "Ana Pérez", "email": "ana@ejemplo.uy"}))
    inscripcion = ins.listar(conn_boveda)[0]

    antes = db.una(conn_boveda, "select count(*)::int as n from persona")["n"]
    salida = ins.aprobar(conn_boveda, inscripcion["id"], actor("operaciones"),
                         id_persona=existente)
    despues = db.una(conn_boveda, "select count(*)::int as n from persona")["n"]

    assert salida["persona"] == "fusionada"
    assert salida["id_persona"] == existente
    assert despues == antes, "no se crea ninguna persona nueva"


def test_la_landing_registra_los_canales_de_primera_mano(conn_boveda, con_texto,
                                                         actor):
    """Es el único lugar donde el opt-in lo da el titular en el momento, que
    es la forma más sólida frente a Meta y frente a URCDP."""
    _verificar(conn_boveda, verificacion.EMAIL, "ana@ejemplo.uy")
    _verificar(conn_boveda, verificacion.CELULAR, "099161616")
    ins.inscribir(conn_boveda, _envio(
        persona={"nombre": "Ana Pérez", "email": "ana@ejemplo.uy",
                 "celular": "099161616"},
        canales=["whatsapp", "email"]))

    inscripcion = ins.listar(conn_boveda)[0]
    assert set(inscripcion["canales"]) == {"whatsapp", "email"}

    salida = ins.aprobar(conn_boveda, inscripcion["id"], actor("operaciones"))
    activos = {c["canal"] for c in preferencias.listar(
        conn_boveda, salida["id_persona"], solo_activas=True)}
    assert activos == {"whatsapp", "email"}


# ── R4.5 · Elegir la plantilla (y nada más) ──────────────────────────

def test_solo_se_listan_las_plantillas_que_sirven_para_convocar(conn_boveda):
    """Una plantilla aprobada sin botón de Flow no sirve para convocar a un
    cuestionario por más aprobada que esté: ofrecerla sería ofrecer un
    callejón sin salida."""
    def meta(url, datos=None, token=None, metodo=None):
        return {"data": [
            _plantilla("con_flow", "es"),
            _plantilla("sin_flow", "es", flow_id=None),
            _plantilla("rechazada", "es", estado="REJECTED"),
        ]}

    salida = whatsapp.listar_plantillas(entorno=ENTORNO_META, pedir=meta)
    assert [p["nombre"] for p in salida["plantillas"]] == ["con_flow"]
    assert salida["sin_flow"] == 1


def test_cada_plantilla_trae_su_idioma_y_su_flow_adentro(conn_boveda):
    """Es la razón por la que no se piden aparte."""
    def meta(url, datos=None, token=None, metodo=None):
        return {"data": [_plantilla("invitacion", "pt_BR", flow_id="FLOW-9")]}

    plantilla = whatsapp.listar_plantillas(
        entorno=ENTORNO_META, pedir=meta)["plantillas"][0]
    assert plantilla["idioma"] == "pt_BR"
    assert plantilla["flow_id"] == "FLOW-9"
    assert plantilla["texto_boton"] == "Responder"
    assert plantilla["cuerpo"] == "Te invitamos a responder."


def test_el_listado_sigue_la_paginacion_de_meta(conn_boveda):
    """Sin seguir el cursor, una cuenta con muchas plantillas mostraría solo
    las primeras cien y las que faltan parecerían no existir."""
    def meta(url, datos=None, token=None, metodo=None):
        if "cursor" not in url:
            return {"data": [_plantilla("primera")],
                    "paging": {"next": "https://graph.facebook.com/x?cursor=2"}}
        return {"data": [_plantilla("segunda")]}

    salida = whatsapp.listar_plantillas(entorno=ENTORNO_META, pedir=meta)
    assert [p["nombre"] for p in salida["plantillas"]] == ["primera", "segunda"]


def test_sin_credenciales_el_listado_lo_dice_en_vez_de_romperse(conn_boveda):
    salida = whatsapp.listar_plantillas(entorno={}, pedir=_meta_ok)
    assert salida["plantillas"] == []
    assert salida["configurado"] is False
    assert "waba_id" in salida["faltan"]


def test_un_mensaje_de_flow_resuelve_el_idioma_y_el_flow(conn_boveda):
    """Lo único que se elige es la plantilla. Pedir los tres por separado era
    pedir tres veces el mismo dato y dejar que se contradigan. Desde D87 es
    un mensaje de la encuesta con botón de Flow, no una sección aparte."""
    panel = paneles.crear(conn_boveda, "Panel")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")

    def meta(url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": [_plantilla("invitacion", "pt_BR", flow_id="FLOW-7")]}
        return {"id": "FLOW-7", "name": "Invitación", "status": "PUBLISHED"}

    salida = mensajes.guardar(conn_boveda, encuesta["id"],
                              {"nombre": "Invitación", "plantilla": "invitacion"},
                              entorno=ENTORNO_META, pedir=meta)

    assert salida["plantilla"] == "invitacion"
    assert salida["plantilla_idioma"] == "pt_BR"
    assert salida["flow_id"] == "FLOW-7"
    assert salida["boton"] == "flow"
    assert salida["url_boton"] is None


def test_una_plantilla_en_varios_idiomas_no_se_elige_sola(conn_boveda):
    """El mismo nombre en dos idiomas son dos plantillas distintas, aprobadas
    por separado. Adivinar sería elegir en qué idioma se le habla a la gente."""
    def meta(url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": [_plantilla("invitacion", "es"),
                             _plantilla("invitacion", "pt_BR")]}
        return {"id": "FLOW-1", "status": "PUBLISHED"}

    panel = paneles.crear(conn_boveda, "Panel")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    with pytest.raises(DatosInvalidos) as error:
        mensajes.guardar(conn_boveda, encuesta["id"],
                         {"nombre": "Invitación", "plantilla": "invitacion"},
                         entorno=ENTORNO_META, pedir=meta)
    assert "idioma" in error.value.mensaje
    assert error.value.detalle["idiomas"] == ["es", "pt_BR"]

    # Con el idioma elegido, resuelve.
    salida = mensajes.guardar(conn_boveda, encuesta["id"],
                              {"nombre": "Invitación", "plantilla": "invitacion",
                               "plantilla_idioma": "pt_BR"},
                              entorno=ENTORNO_META, pedir=meta)
    assert salida["plantilla_idioma"] == "pt_BR"


def test_un_mensaje_de_flow_exige_el_flow_publicado_al_guardar(conn_boveda):
    """La validación de la sección vieja —Flow publicado y plantilla
    aprobada— es parte de editar el mensaje (D87). Una plantilla aprobada con
    un Flow en borrador se envía y no abre nada."""
    def meta_borrador(url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": [_plantilla()]}
        return {"id": "FLOW-1", "name": "Invitación", "status": "DRAFT"}

    panel = paneles.crear(conn_boveda, "Panel")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    with pytest.raises(DatosInvalidos) as error:
        mensajes.guardar(conn_boveda, encuesta["id"],
                         {"nombre": "Invitación", "plantilla": "invitacion_ola"},
                         entorno=ENTORNO_META, pedir=meta_borrador)
    assert "publicado" in error.value.mensaje
    assert error.value.detalle["motivo"] == "flow_no_publicado"


def test_sin_credenciales_no_se_arma_un_mensaje_de_whatsapp(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    with pytest.raises(DatosInvalidos) as error:
        mensajes.guardar(conn_boveda, encuesta["id"],
                         {"nombre": "Invitación", "plantilla": "invitacion_ola"},
                         entorno={}, pedir=_meta_ok)
    assert error.value.detalle["motivo"] == "whatsapp_sin_configurar"
    assert whatsapp.diagnostico({})["configurado"] is False


def test_el_envio_no_necesita_el_flow_id(conn_boveda):
    """El mensaje referencia la plantilla; el Flow viene adentro de su botón.
    Es la razón de fondo por la que configurarlo aparte nunca tuvo sentido."""
    cuerpos = []

    def pedir(url, datos=None, token=None, metodo=None):
        if "/messages" in url:
            cuerpos.append(datos)
            return {"messages": [{"id": "wamid.1"}]}
        return _meta_ok(url, datos, token, metodo)

    whatsapp.enviar_flow("+59899111111", "invitacion_ola", "es",
                         flow_token="ID-PERSONA",
                         entorno=ENTORNO_META, pedir=pedir)

    plantilla = cuerpos[0]["template"]
    assert plantilla["name"] == "invitacion_ola"
    assert plantilla["language"]["code"] == "es"
    accion = plantilla["components"][0]["parameters"][0]["action"]
    assert accion["flow_token"] == "ID-PERSONA"
    assert "flow_id" not in json.dumps(cuerpos[0])


# ── R4.5 · Envío por WhatsApp Flow, desde los mensajes (D87) ─────────

def test_no_se_envia_un_flow_con_la_plantilla_sin_aprobar(conn_boveda):
    """Una plantilla rechazada no se arregla sola. Se comprueba de nuevo al
    enviar: entre guardar el mensaje y mandarlo, Meta pudo pausarla."""
    _, encuesta, mensaje = _ola_flow(conn_boveda)

    def meta_pendiente(url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": [_plantilla(estado="PENDING")]}
        return {"id": "FLOW-1", "status": "PUBLISHED"}

    previa = mensajes.previa(conn_boveda, encuesta["id"], mensaje["id"],
                             mensajes.TODOS, entorno=ENTORNO_META, pedir=meta_pendiente)
    assert previa["puede_enviar"] is False
    assert any("PENDING" in m for m in previa["motivos"])


def test_un_flow_despublicado_tambien_bloquea_el_envio(conn_boveda):
    _, encuesta, mensaje = _ola_flow(conn_boveda)

    def meta_borrador(url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": [_plantilla()]}
        return {"id": "FLOW-1", "name": "Invitación", "status": "DRAFT"}

    previa = mensajes.previa(conn_boveda, encuesta["id"], mensaje["id"],
                             mensajes.TODOS, entorno=ENTORNO_META, pedir=meta_borrador)
    assert previa["puede_enviar"] is False
    assert any("publicado" in m for m in previa["motivos"])


def test_el_envio_excluye_a_quien_no_cumple_informando_el_motivo(conn_boveda):
    """El punto del DoD, con un caso de cada motivo. Es la misma regla de
    elegibilidad que un mensaje con botón de URL (`filtrar_contactables`)."""
    panel, encuesta, mensaje = _ola_flow(conn_boveda, convocar=False)
    completa = _persona(conn_boveda, "13-1", celular="099171717",
                        canales=["whatsapp"])["id_persona"]
    sin_preferencia = _persona(conn_boveda, "13-2", celular="099181818")["id_persona"]
    sin_celular = _persona(conn_boveda, "13-3", celular="099191818",
                           canales=["whatsapp"])["id_persona"]
    for id_persona in (completa, sin_preferencia, sin_celular):
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    # Aceptó WhatsApp y después quedó sin celular: es el caso en que el motivo
    # es «falta el número», no «falta el permiso».
    db.ejecutar(conn_boveda, "update persona set celular = null where id_persona = %s",
                (sin_celular,))
    conn_boveda.commit()

    previa = mensajes.previa(conn_boveda, encuesta["id"], mensaje["id"],
                             mensajes.TODOS, entorno=ENTORNO_META, pedir=_meta_ok)
    assert previa["_destinatarios"] == [completa]
    assert previa["excluidos"]["sin_preferencia_whatsapp"] == 1
    assert previa["excluidos"]["sin_celular"] == 1


def test_cada_envio_lleva_el_id_persona_como_flow_token(conn_boveda):
    """Es lo que hace que la ingesta mapee directo cuando el analista baje las
    respuestas de Meta: sin PII y sin adivinar."""
    panel, encuesta, mensaje = _ola_flow(conn_boveda, convocar=False)
    id_persona = _persona(conn_boveda, "14-1", celular="099191919",
                          canales=["whatsapp"])["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    conn_boveda.commit()

    meta = _MetaQueAnota()
    salida = _enviar_flow(conn_boveda, encuesta, mensaje, meta)

    assert salida["enviados"] == 1
    plantilla = meta.enviados[0]["template"]
    assert meta.enviados[0]["to"] == "+59899191919"
    boton = next(c for c in plantilla["components"] if c["type"] == "button")
    assert boton["sub_type"] == "flow"
    assert boton["parameters"][0]["action"]["flow_token"] == id_persona


def test_reintentar_no_reenvia_a_quien_ya_recibio(conn_boveda):
    """Un mensaje duplicado quema el canal y la paciencia. «No enviado aún»
    lo calcula sobre `envio`, igual que con cualquier mensaje."""
    panel, encuesta, mensaje = _ola_flow(conn_boveda, convocar=False)
    uno = _persona(conn_boveda, "15-1", celular="099202020",
                   canales=["whatsapp"])["id_persona"]
    otro = _persona(conn_boveda, "15-2", celular="099212121",
                    canales=["whatsapp"])["id_persona"]
    for id_persona in (uno, otro):
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    conn_boveda.commit()

    meta = _MetaQueAnota(rechazar={"+59899212121"})
    primera = _enviar_flow(conn_boveda, encuesta, mensaje, meta)
    assert (primera["enviados"], primera["fallidos"]) == (1, 1)

    meta = _MetaQueAnota()
    segunda = _enviar_flow(conn_boveda, encuesta, mensaje, meta)
    assert [e["to"] for e in meta.enviados] == ["+59899212121"], "solo el fallido"
    assert segunda["enviados"] == 1


def test_un_fallo_por_persona_no_voltea_el_envio_entero(conn_boveda):
    panel, encuesta, mensaje = _ola_flow(conn_boveda, convocar=False)
    for i in range(3):
        id_persona = _persona(conn_boveda, f"16-{i}", celular=f"09922{i}22{i}",
                              canales=["whatsapp"])["id_persona"]
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    conn_boveda.commit()

    salida = _enviar_flow(conn_boveda, encuesta, mensaje,
                          _MetaQueAnota(rechazar={"+59899221221"}))
    assert (salida["enviados"], salida["fallidos"]) == (2, 1)
    fallido = db.una(conn_boveda, "select error from envio where estado = 'fallido'")
    assert "Número inválido" in fallido["error"]


def test_el_envio_no_escribe_en_participacion(conn_boveda):
    """D87 — el registro es `envio`, uno por persona y mensaje. Las columnas
    viejas de `participacion` quedan sin escribir hasta que se quiten."""
    panel, encuesta, mensaje = _ola_flow(conn_boveda, convocar=False)
    id_persona = _persona(conn_boveda, "17-1", celular="099232323",
                          canales=["whatsapp"])["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    conn_boveda.commit()
    _enviar_flow(conn_boveda, encuesta, mensaje, _MetaQueAnota())

    fila = db.una(conn_boveda,
                  "select envio_estado, enviado_en from participacion where id_persona = %s",
                  (id_persona,))
    assert fila["envio_estado"] is None and fila["enviado_en"] is None
    assert db.una(conn_boveda, "select estado from envio where id_persona = %s",
                  (id_persona,))["estado"] == "aceptado"


def test_ya_no_existen_las_rutas_de_la_seccion_vieja():
    """D87 — la sección «WhatsApp Flow» se unificó en Mensajes."""
    from panel_api import ruteo

    rutas = {(metodo, patron) for metodo, _expresion, _permiso, _funcion, patron
             in ruteo.RUTAS}
    for metodo, patron in (("PUT", "/encuestas/<encuesta_id>/flow"),
                           ("GET", "/encuestas/<encuesta_id>/flow"),
                           ("POST", "/encuestas/<encuesta_id>/whatsapp"),
                           ("GET", "/encuestas/<encuesta_id>/whatsapp")):
        assert (metodo, patron) not in rutas


# ── Permisos ─────────────────────────────────────────────────────────

def test_enviar_mensajes_no_lo_tiene_el_analista(actor):
    """Cada conversación se cobra y un envío mal dirigido quema el canal para
    todos. El analista fieldea e ingesta, pero no manda."""
    from panel_api.auth import PERMISOS

    assert PERMISOS["enviar_mensajes"] == {"admin", "operaciones"}
    assert "enviar_whatsapp" not in PERMISOS
    assert not actor("analista").puede("enviar_mensajes")
    assert actor("analista").puede("ingestar"), "lo que sí podía, lo sigue pudiendo"
