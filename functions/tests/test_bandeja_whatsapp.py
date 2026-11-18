"""SPEC_alchemer_participacion_y_bandeja_whatsapp — parte C (D98): la bandeja
de WhatsApp, contra Postgres de verdad y con un Meta de mentira.

* R-WA.1 — el webhook: verificación de Meta, firma, aviso crudo guardado
  antes de procesar, idempotencia, nada se pierde (fallido → reprocesar).
* R-WA.2 / R-WA.3 — respuestas de Flow, comentarios y estados; la identidad
  sale del envío; el `flow_token` distinto es una inconsistencia; lo de
  afuera se agrupa como en la planilla.
* R-WA.4 — la bandeja: grupos, columnas por campo, exportación auditada,
  acceso por rol.
* R-WA.5 — conservación (lo de afuera y el crudo vencen) y baja (por
  `id_persona` y por teléfono).
* R-WA.6 — las respuestas de una encuesta como un SAV, con el diccionario
  del Flow: `screen_0_Radio_1: "0_Si"` se embebe «texto → Sí», y una segunda
  carga deja a los anteriores como «repetida».
"""

import base64
import hashlib
import hmac
import json

import pytest

from panel_api import (
    bajas, bandeja_whatsapp, db, diferida, encuestas, ingesta, mensajes, paneles, personas,
    preferencias, ruteo, sav, whatsapp,
)
from panel_api.errores import SinPermiso

from conftest import consentimientos

SECRETO = "secreto-de-la-app"
ENTORNO = {"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "111",
           "WHATSAPP_WABA_ID": "222", "WHATSAPP_VERIFY_TOKEN": "verificame",
           "WHATSAPP_APP_SECRET": SECRETO}

FLOW_JSON = {
    "version": "6.0",
    "screens": [
        {"id": "QUESTION_ONE", "title": "Pregunta 1", "layout": {
            "type": "SingleColumnLayout", "children": [{"type": "Form", "name": "flow_path", "children": [
                {"type": "TextSubheading", "text": "Contanos"},
                {"type": "RadioButtonsGroup", "label": "¿Le gustó el producto?", "name": "Radio_1",
                 "data-source": [{"id": "0_Si", "title": "Sí"}, {"id": "1_No", "title": "No"}]},
                {"type": "Footer", "label": "Siguiente", "on-click-action": {
                    "name": "navigate", "next": {"type": "screen", "name": "QUESTION_TWO"},
                    "payload": {"screen_0_Radio_1": "${form.Radio_1}"}}}]}]}},
        {"id": "QUESTION_TWO", "title": "Pregunta 2", "terminal": True,
         "data": {"screen_0_Radio_1": {"type": "string", "__example__": "x"}},
         "layout": {"type": "SingleColumnLayout", "children": [{"type": "Form", "name": "flow_path", "children": [
             {"type": "CheckboxGroup", "label": "¿Qué marcas conoce?", "name": "Checkbox_2",
              "data-source": [{"id": "0_Fernet", "title": "Fernet"},
                              {"id": "1_Whisky", "title": "Whisky"}]},
             {"type": "TextArea", "label": "¿Por qué?", "name": "TextArea_3"},
             {"type": "TextInput", "label": "${data.etiqueta}", "name": "Text_4"},
             {"type": "Footer", "label": "Enviar", "on-click-action": {
                 "name": "complete", "payload": {
                     "screen_0_Radio_1": "${data.screen_0_Radio_1}",
                     "screen_1_Checkbox_2": "${form.Checkbox_2}",
                     "screen_1_TextArea_3": "${form.TextArea_3}",
                     "screen_1_Text_4": "${form.Text_4}"}}}]}]}},
    ],
}


class MetaFalsa:
    def __init__(self):
        self.enviados = []
        self.plantilla = {
            "name": "encuesta_flow", "language": "es", "status": "APPROVED",
            "category": "MARKETING",
            "components": [{"type": "BODY", "text": "Hola {{1}}."},
                           {"type": "BUTTONS", "buttons": [
                               {"type": "FLOW", "text": "Responder", "flow_id": "FLOW-9"}]}]}

    def __call__(self, url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": [self.plantilla]}
        if url.endswith("/messages"):
            self.enviados.append(datos)
            return {"messages": [{"id": f"wamid.envio.{len(self.enviados)}"}]}
        if url.endswith("/FLOW-9/assets"):
            return {"data": [{"name": "flow.json", "asset_type": "FLOW_JSON",
                              "download_url": "https://cdn.meta/flow-9.json"}]}
        if url == "https://cdn.meta/flow-9.json":
            return FLOW_JSON
        return {"id": "FLOW-9", "name": "Encuesta", "status": "PUBLISHED"}


@pytest.fixture
def meta(monkeypatch):
    falsa = MetaFalsa()
    for clave, valor in ENTORNO.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setattr(whatsapp, "_pedir", falsa)
    return falsa


def _panelista(conn, n):
    id_persona = str(personas.alta(conn, {
        "persona": {"documento": f"8-{n}", "nombre": f"Persona{n}", "celular": f"09{n:07d}"},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico")})
        ["id_persona"])
    preferencias.otorgar(conn, id_persona, preferencias.WHATSAPP, "v1")
    return id_persona


def _de(n):
    """El `from` con que Meta manda un mensaje del panelista n."""
    return preferencias.normalizar_celular(f"09{n:07d}").lstrip("+")


@pytest.fixture
def ola(conn_boveda, meta):
    """Una encuesta con un mensaje de Flow enviado a cinco panelistas."""
    panel = paneles.crear(conn_boveda, "Panel")
    ids = [_panelista(conn_boveda, i) for i in range(1, 6)]
    for i in ids:
        paneles.agregar_miembro(conn_boveda, panel["id"], i)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola Flow", fecha_campo="2026-11-01")
    encuestas.convocar(conn_boveda, encuesta["id"], ids_persona=ids)
    mensaje = mensajes.guardar(conn_boveda, encuesta["id"], {
        "nombre": "Encuesta por WhatsApp", "plantilla": "encuesta_flow",
        "parametros": ["nombre_panelista"]})
    publico = mensajes.publico(conn_boveda, encuesta["id"], mensaje["id"], mensajes.TODOS)
    mensajes.procesar_lote(conn_boveda, {"encuesta_id": encuesta["id"], "mensaje_id": mensaje["id"],
                                         "publico": mensajes.TODOS},
                           [{"id_persona": i} for i in publico])
    conn_boveda.commit()
    envios = {str(f["id_persona"]): f["id_mensaje_meta"] for f in db.todas(
        conn_boveda, "select id_persona, id_mensaje_meta from envio")}
    return {"encuesta": encuesta, "mensaje": mensaje, "ids": ids,
            "wamid": [envios[i] for i in ids]}


# ── Avisos de Meta ────────────────────────────────────────────────────

def _aviso(mensajes_=(), estados=()):
    return {"object": "whatsapp_business_account", "entry": [{"id": "222", "changes": [{
        "field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "111"},
            "contacts": [{"profile": {"name": "Perfil"}, "wa_id": "000"}],
            "messages": list(mensajes_), "statuses": list(estados)}}]}]}


def _flow(id_mensaje, de, contexto=None, token=None, **respuesta):
    cuerpo = {**({"flow_token": token} if token is not None else {}), **respuesta}
    return {"from": de, "id": id_mensaje, "timestamp": "1763400000", "type": "interactive",
            **({"context": {"from": "111", "id": contexto}} if contexto else {}),
            "interactive": {"type": "nfm_reply", "nfm_reply": {
                "name": "flow", "body": "Sent", "response_json": json.dumps(cuerpo)}}}


def _texto(id_mensaje, de, texto, contexto=None):
    return {"from": de, "id": id_mensaje, "timestamp": "1763400100", "type": "text",
            "text": {"body": texto},
            **({"context": {"from": "111", "id": contexto}} if contexto else {})}


def _estado(id_meta, estado, **extra):
    return {"id": id_meta, "status": estado, "timestamp": "1763400200",
            "recipient_id": "000", **extra}


def _firmar(cuerpo, secreto=SECRETO):
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _post(conn, aviso, firma=None):
    cuerpo = json.dumps(aviso).encode()
    return bandeja_whatsapp.atender(
        conn, "POST", {}, cuerpo,
        {"X-Hub-Signature-256": firma if firma is not None else _firmar(cuerpo)})


def _entrantes(conn):
    return db.todas(conn, "select * from whatsapp_entrante order by id")


def _cuantos(conn, tabla):
    return db.una(conn, f"select count(*)::int as n from {tabla}")["n"]


# ════════════════════════════════════════════════════════════════════
#  R-WA.1 · El webhook
# ════════════════════════════════════════════════════════════════════

def test_la_verificacion_de_meta_devuelve_el_challenge(conn_boveda, meta, monkeypatch):
    consulta = {"hub.mode": "subscribe", "hub.verify_token": "verificame",
                "hub.challenge": "12345"}
    assert bandeja_whatsapp.atender(conn_boveda, "GET", consulta, b"", {}) == (200, "12345")
    otra = {**consulta, "hub.verify_token": "otro"}
    assert bandeja_whatsapp.atender(conn_boveda, "GET", otra, b"", {})[0] == 403
    # Sin token configurado no se verifica nada.
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN")
    assert bandeja_whatsapp.atender(conn_boveda, "GET", consulta, b"", {})[0] == 403


def test_una_firma_invalida_se_rechaza_y_no_se_guarda(conn_boveda, ola, monkeypatch):
    aviso = _aviso([_flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][0])])
    assert _post(conn_boveda, aviso, firma="sha256=" + "0" * 64)[0] == 403
    assert _post(conn_boveda, aviso, firma=_firmar(json.dumps(aviso).encode(), "otra"))[0] == 403
    # Sin la clave secreta configurada falla cerrado.
    monkeypatch.delenv("WHATSAPP_APP_SECRET")
    assert _post(conn_boveda, aviso)[0] == 403
    assert _cuantos(conn_boveda, "whatsapp_aviso") == 0


def test_el_aviso_crudo_se_guarda_y_el_reenvio_no_duplica(conn_boveda, ola):
    aviso = _aviso([_flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][0],
                          screen_0_Radio_1="0_Si")])
    assert _post(conn_boveda, aviso) == (200, "ok")
    assert _post(conn_boveda, aviso) == (200, "ok")            # Meta reenvía
    # El mismo mensaje dentro de otro aviso (con un estado al lado) tampoco.
    assert _post(conn_boveda, {**aviso, "entry": aviso["entry"] + _aviso(
        estados=[_estado(ola["wamid"][1], "delivered")])["entry"]})[0] == 200
    assert _cuantos(conn_boveda, "whatsapp_aviso") == 2
    assert _cuantos(conn_boveda, "whatsapp_entrante") == 1
    assert {f["estado"] for f in db.todas(conn_boveda, "select estado from whatsapp_aviso")} \
        == {"procesado"}


def test_si_el_procesamiento_falla_el_aviso_queda_y_se_reprocesa(
        ctx, actor, ola, monkeypatch):
    original = bandeja_whatsapp._guardar_mensaje

    def roto(*args):
        raise RuntimeError("se cayó a mitad")

    monkeypatch.setattr(bandeja_whatsapp, "_guardar_mensaje", roto)
    aviso = _aviso([_flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][0])])
    assert _post(ctx.boveda, aviso) == (200, "ok")        # a Meta, 200 igual
    fila = db.una(ctx.boveda, "select estado, error, cuerpo from whatsapp_aviso")
    assert fila["estado"] == "fallido" and "RuntimeError" in fila["error"]
    assert fila["cuerpo"]["entry"]                        # el crudo, entero
    assert _cuantos(ctx.boveda, "whatsapp_entrante") == 0

    monkeypatch.setattr(bandeja_whatsapp, "_guardar_mensaje", original)
    status, salida = ruteo.despachar("POST", "/whatsapp/avisos/reprocesar", {}, {},
                                     actor("operaciones"), ctx)
    assert status == 200 and salida == {"reprocesados": 1, "procesados": 1, "fallidos": 0}
    assert _cuantos(ctx.boveda, "whatsapp_entrante") == 1


def test_un_aviso_pendiente_de_antes_se_procesa_con_el_siguiente(conn_boveda, ola):
    """Un proceso que murió entre guardar y procesar no deja el aviso
    olvidado: el próximo aviso lo levanta."""
    cuerpo = json.dumps(_aviso([_flow("wamid.viejo", _de(2), ola["wamid"][1],
                                      ola["ids"][1])])).encode()
    bandeja_whatsapp.guardar_aviso(conn_boveda, cuerpo)
    conn_boveda.commit()
    _post(conn_boveda, _aviso([_texto("wamid.t1", _de(3), "hola")]))
    assert {f["id_mensaje"] for f in _entrantes(conn_boveda)} == {"wamid.viejo", "wamid.t1"}


# ════════════════════════════════════════════════════════════════════
#  R-WA.2 / R-WA.3 · Tres tipos, y a quién pertenecen
# ════════════════════════════════════════════════════════════════════

def test_una_respuesta_de_flow_se_asocia_por_el_envio(conn_boveda, ola):
    _post(conn_boveda, _aviso([_flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][0],
                                     screen_0_Radio_1="0_Si")]))
    fila = _entrantes(conn_boveda)[0]
    assert fila["tipo"] == "flow" and str(fila["id_persona"]) == ola["ids"][0]
    assert fila["encuesta_id"] == ola["encuesta"]["id"]
    assert fila["encuesta_mensaje_id"] == ola["mensaje"]["id"]
    assert fila["respuesta"] == {"screen_0_Radio_1": "0_Si"}     # sin el token
    assert fila["flow_token"] == ola["ids"][0] and fila["inconsistencia"] is None
    assert fila["telefono"] == "+" + _de(1) and fila["grupo"] is None


def test_un_flow_token_distinto_es_una_inconsistencia_y_manda_el_envio(conn_boveda, ola):
    _post(conn_boveda, _aviso([_flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][3])]))
    fila = _entrantes(conn_boveda)[0]
    assert str(fila["id_persona"]) == ola["ids"][0]
    assert fila["inconsistencia"] == bandeja_whatsapp.INCONSISTENCIA_TOKEN


@pytest.mark.parametrize("token, grupo", [
    ("tpl=SONDEO_CLIENTE|598991234", "SONDEO_CLIENTE"),
    ("ENCUESTA_B|x|y", "ENCUESTA_B"),
    ("abc|tpl=OTRA", "OTRA"),
    ("solo-un-token", "GENERAL"),
    (None, "GENERAL"),
])
def test_lo_de_afuera_se_agrupa_como_en_la_planilla(conn_boveda, ola, token, grupo):
    _post(conn_boveda, _aviso([_flow("wamid.x", "59891111111", None, token, campo="1")]))
    fila = _entrantes(conn_boveda)[0]
    assert fila["id_persona"] is None and fila["encuesta_id"] is None
    assert fila["grupo"] == grupo


def test_un_comentario_se_asocia_al_mensaje_que_contesta(conn_boveda, ola):
    _post(conn_boveda, _aviso([
        _texto("wamid.c1", _de(2), "No me abre el formulario", contexto=ola["wamid"][1]),
        {"from": _de(3), "id": "wamid.c2", "timestamp": "1763400100", "type": "button",
         "button": {"text": "No me interesa", "payload": "no"},
         "context": {"id": ola["wamid"][2]}},
        {"from": _de(4), "id": "wamid.c3", "timestamp": "1763400100", "type": "image",
         "image": {"id": "img"}},
    ]))
    por_id = {f["id_mensaje"]: f for f in _entrantes(conn_boveda)}
    assert por_id["wamid.c1"]["tipo"] == "comentario"
    assert por_id["wamid.c1"]["texto"] == "No me abre el formulario"
    assert str(por_id["wamid.c1"]["id_persona"]) == ola["ids"][1]
    assert por_id["wamid.c2"]["texto"] == "No me interesa"
    assert por_id["wamid.c3"]["texto"] == "[image]" and por_id["wamid.c3"]["grupo"] == "GENERAL"


def test_los_estados_actualizan_el_envio_y_nunca_retroceden(conn_boveda, ola):
    """R-CF.7 queda cerrado: entregado, leído y fallido llegan al envío."""
    w = ola["wamid"]
    _post(conn_boveda, _aviso(estados=[_estado(w[0], "sent"), _estado(w[0], "read")]))
    _post(conn_boveda, _aviso(estados=[_estado(w[0], "delivered")]))     # llega tarde
    _post(conn_boveda, _aviso(estados=[_estado(w[1], "delivered")]))
    _post(conn_boveda, _aviso(estados=[_estado(w[2], "failed", errors=[
        {"code": 131026, "title": "Message undeliverable"}])]))
    estado = {f["id_mensaje_meta"]: f for f in db.todas(
        conn_boveda, "select id_mensaje_meta, estado, error from envio")}
    assert estado[w[0]]["estado"] == "leido"
    assert estado[w[1]]["estado"] == "entregado"
    assert estado[w[2]]["estado"] == "fallido"
    assert estado[w[2]]["error"] == "Message undeliverable"
    assert estado[w[3]]["estado"] == "aceptado"
    assert _cuantos(conn_boveda, "whatsapp_estado") == 5


# ════════════════════════════════════════════════════════════════════
#  R-WA.4 · La bandeja
# ════════════════════════════════════════════════════════════════════

def test_la_bandeja_lista_grupos_columnas_y_exporta_auditado(ctx, actor, ola):
    _post(ctx.boveda, _aviso([
        _flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][0],
              screen_1_TextArea_3="Rico", screen_0_Radio_1="0_Si"),
        _flow("wamid.r2", _de(2), ola["wamid"][1], ola["ids"][1],
              screen_0_Radio_1="1_No", screen_1_Checkbox_2=["0_Fernet", "1_Whisky"]),
        _flow("wamid.x", "59891111111", None, "tpl=AFUERA|1", pregunta="sí"),
        _texto("wamid.c1", _de(3), "Gracias!", contexto=ola["wamid"][2]),
    ]))
    status, grupos = ruteo.despachar("GET", "/whatsapp/flows", None, {}, actor("operaciones"), ctx)
    assert status == 200 and grupos["configurado"] is True
    por_clave = {g["clave"]: g for g in grupos["items"]}
    assert por_clave[f"encuesta:{ola['encuesta']['id']}"]["respuestas"] == 2
    assert por_clave[f"encuesta:{ola['encuesta']['id']}"]["comentarios"] == 1
    assert por_clave["grupo:AFUERA"]["respuestas"] == 1

    status, tabla = ruteo.despachar("GET", "/whatsapp/respuestas", None,
                                    {"encuesta_id": str(ola["encuesta"]["id"])},
                                    actor("operaciones"), ctx)
    # Las de pantalla, ordenadas por pantalla aunque lleguen desordenadas.
    assert tabla["columnas"] == ["screen_0_Radio_1", "screen_1_Checkbox_2", "screen_1_TextArea_3"]
    assert {f["campos"]["screen_1_Checkbox_2"] for f in tabla["filas"]} == {"", "0_Fernet, 1_Whisky"}

    status, salida = ruteo.despachar("POST", "/whatsapp/exportacion",
                                     {"encuesta_id": ola["encuesta"]["id"]}, {},
                                     actor("operaciones", email="ops@equipos.com.uy"), ctx)
    lineas = salida["csv"].strip().splitlines()
    assert lineas[0].startswith("fecha,telefono,encuesta_o_plantilla,token")
    assert len(lineas) == 3 and salida["filas"] == 2
    registro = db.una(ctx.boveda, "select actor_email, filtro, filas from whatsapp_exportacion")
    assert registro["actor_email"] == "ops@equipos.com.uy" and registro["filas"] == 2
    assert registro["filtro"] == {"encuesta_id": ola["encuesta"]["id"]}

    status, coment = ruteo.despachar("GET", "/whatsapp/comentarios", None, {}, actor("admin"), ctx)
    assert [c["texto"] for c in coment["filas"]] == ["Gracias!"]


@pytest.mark.parametrize("rol", ["analista", "dpo"])
def test_la_bandeja_es_de_acceso_restringido(ctx, actor, rol):
    for metodo, camino in [("GET", "/whatsapp/flows"), ("GET", "/whatsapp/respuestas"),
                           ("POST", "/whatsapp/exportacion")]:
        with pytest.raises(SinPermiso):
            ruteo.despachar(metodo, camino, {}, {}, actor(rol), ctx)


# ════════════════════════════════════════════════════════════════════
#  R-WA.5 · Conservación y baja
# ════════════════════════════════════════════════════════════════════

def test_lo_de_afuera_y_el_crudo_vencen(conn_boveda, ola):
    _post(conn_boveda, _aviso([
        _flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][0]),
        _flow("wamid.x", "59891111111", None, "tpl=AFUERA|1"),
    ]))
    db.ejecutar(conn_boveda, "update whatsapp_entrante set recibido_en = now() - interval '200 days'")
    db.ejecutar(conn_boveda, "update whatsapp_aviso set recibido_en = now() - interval '40 days'")
    salida = bandeja_whatsapp.purgar(conn_boveda)
    assert salida == {"entrantes": 1, "avisos": 1}
    # Lo del panelista se queda (lo borra la baja); el crudo procesado, no.
    assert [f["id_mensaje"] for f in _entrantes(conn_boveda)] == ["wamid.r1"]


def test_la_baja_borra_lo_suyo_por_id_y_por_telefono(conn_boveda, ola):
    _post(conn_boveda, _aviso([
        _flow("wamid.r1", _de(1), ola["wamid"][0], ola["ids"][0]),
        # Desde su número, a una encuesta de afuera: sin id_persona.
        _flow("wamid.x1", _de(1), None, "tpl=AFUERA|1"),
        _flow("wamid.r2", _de(2), ola["wamid"][1], ola["ids"][1]),
    ]))
    _post(conn_boveda, _aviso([_texto("wamid.otro", _de(2), "hola")]))
    bajas.retirar(conn_boveda, ola["ids"][0])
    conn_boveda.commit()
    assert {f["id_mensaje"] for f in _entrantes(conn_boveda)} == {"wamid.r2", "wamid.otro"}
    # El aviso crudo que nombraba su teléfono tampoco queda.
    for f in db.todas(conn_boveda, "select cuerpo::text as c from whatsapp_aviso"):
        assert _de(1) not in f["c"]
    assert _cuantos(conn_boveda, "whatsapp_aviso") == 1


# ════════════════════════════════════════════════════════════════════
#  R-WA.6 · Ingestar las respuestas de una encuesta como un SAV
# ════════════════════════════════════════════════════════════════════

def test_el_diccionario_sale_de_la_definicion_del_flow():
    dicc = bandeja_whatsapp.diccionario(FLOW_JSON)
    assert dicc["screen_0_Radio_1"]["texto"] == "¿Le gustó el producto?"
    assert dicc["screen_0_Radio_1"]["opciones"] == {"0_Si": "Sí", "1_No": "No"}
    assert dicc["screen_1_Checkbox_2"]["tipo"] == "multiple"
    assert dicc["screen_1_TextArea_3"]["tipo"] == "abierta"
    assert dicc["screen_1_Text_4"]["texto"] is None         # etiqueta dinámica


def _responder(conn, ola, quienes, desde=0):
    _post(conn, _aviso([
        _flow(f"wamid.r{desde + n}", _de(n + 1), ola["wamid"][n], ola["ids"][n],
              screen_0_Radio_1="0_Si" if n % 2 == 0 else "1_No",
              screen_1_Checkbox_2=["0_Fernet"], screen_1_TextArea_3=f"Porque sí {n}",
              screen_1_Text_4="algo", screen_2_Extra="z")
        for n in quienes]))


def test_el_sav_traduce_como_un_sav_de_alchemer(conn_boveda, ola):
    """Criterio de aceptación: `screen_0_Radio_1: "0_Si"` → «texto → Sí»."""
    _responder(conn_boveda, ola, [0, 1])
    salida = bandeja_whatsapp.conjunto_de_encuesta(conn_boveda, ola["encuesta"]["id"])
    assert salida["filas"] == 2 and salida["flows"] == ["FLOW-9"]
    analisis = sav.analizar(base64.b64decode(salida["archivo_base64"]))
    variables = {v["codigo"]: v for v in analisis["variables"]}
    assert "id_persona" in analisis["candidatas_a_id"]
    radio = variables["screen_0_Radio_1"]
    assert radio["texto"] == "¿Le gustó el producto?"
    assert radio["opciones"] == {"1": "Sí", "2": "No"}
    _, texto, _ = ingesta.respuesta_de(radio, "1")
    assert texto == "¿Le gustó el producto? → Sí"
    # Selección múltiple → batería, una variable por opción.
    assert variables["screen_1_Checkbox_2_0_Fernet"]["texto"] == "¿Qué marcas conoce?:Fernet"
    assert variables["screen_1_TextArea_3"]["tipo"] == "abierta"
    # Lo que no se pudo traducir, marcado «sin etiqueta» para corregirlo.
    sin = {s["codigo"] for s in salida["sin_etiqueta"]}
    assert {"screen_1_Text_4", "screen_2_Extra"} <= sin
    assert any("sin etiqueta" in a for a in salida["avisos"])
    assert any(a["tipo"] == "textos_a_revisar" for a in analisis["avisos"])


def _ingestar_sav(ctx, actor, encuesta_id, salida):
    _, carga = ruteo.despachar("POST", f"/encuestas/{encuesta_id}/cargas",
                               {"nombre": "WhatsApp", "tipo_datos_origen": "encuesta",
                                "modo_relevamiento": "otro",
                                "modo_relevamiento_otro": "WhatsApp Flow"}, {},
                               actor("admin"), ctx)
    analisis = sav.analizar(base64.b64decode(salida["archivo_base64"]))
    preguntas = [{"codigo": v["codigo"], "texto": v["texto"] or v["codigo"], "tipo": v["tipo"],
                  "opciones": v["opciones"], "orden": v["orden"]}
                 for v in analisis["variables"] if v["codigo"] == "screen_0_Radio_1"]
    status, resumen = ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta", {
        "archivo_base64": salida["archivo_base64"], "origen": "sav",
        "columna_id": "id_persona", "tipo_identificador": "id_persona",
        "preguntas": preguntas, "proponer_tematicas": False}, {}, actor("admin"), ctx)
    assert status in (200, 202), resumen
    pendientes = list(ctx.encolador.encoladas)
    ctx.encolador.encoladas.clear()
    for t, i in pendientes:
        diferida.procesar_lote(ctx.boveda, ctx.semantica, t, i, proveedor=ctx.embeddings)
    return carga


def test_obtener_respuestas_informa_las_nuevas_y_la_carga_marca_repetidas(ctx, actor, ola):
    """Criterio de aceptación (en chico): 3 respuestas y una carga anterior
    con 2 → 1 nueva; al ingestar, las 2 de antes quedan «repetida»."""
    _responder(ctx.boveda, ola, [0, 1])
    status, resumen = ruteo.despachar("GET", f"/encuestas/{ola['encuesta']['id']}/whatsapp/resumen",
                                      None, {}, actor("analista"), ctx)
    assert (resumen["personas"], resumen["nuevas"]) == (2, 2)
    assert "telefono" not in json.dumps(resumen, default=str)
    status, salida = ruteo.despachar("POST", f"/encuestas/{ola['encuesta']['id']}/whatsapp/archivo",
                                     {}, {}, actor("analista"), ctx)
    primera = _ingestar_sav(ctx, actor, ola["encuesta"]["id"], salida)
    textos = {f["texto_embebido"] for f in db.todas(
        ctx.semantica, "select texto_embebido from unidad_evidencia")}
    assert any(t.endswith("¿Le gustó el producto? → Sí") for t in textos), textos

    _responder(ctx.boveda, ola, [2], desde=10)
    _, resumen = ruteo.despachar("GET", f"/encuestas/{ola['encuesta']['id']}/whatsapp/resumen",
                                 None, {}, actor("analista"), ctx)
    assert (resumen["personas"], resumen["nuevas"], resumen["repetidas"]) == (3, 1, 2)
    _, salida = ruteo.despachar("POST", f"/encuestas/{ola['encuesta']['id']}/whatsapp/archivo",
                                {}, {}, actor("analista"), ctx)
    segunda = _ingestar_sav(ctx, actor, ola["encuesta"]["id"], salida)
    origenes = {(str(f["id_persona"]), f["carga_id"]): f["origen"] for f in db.todas(
        ctx.boveda, "select id_persona, carga_id, origen from persona_carga")}
    assert origenes[(ola["ids"][0], segunda["id"])] == "repetida"
    assert origenes[(ola["ids"][1], segunda["id"])] == "repetida"
    assert origenes[(ola["ids"][2], segunda["id"])] != "repetida"
    assert origenes[(ola["ids"][0], primera["id"])] != "repetida"
