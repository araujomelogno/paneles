"""PEDIDO 06/11/26 · Parte 1 (D87) — «WhatsApp Flow» se unifica en «Mensajes».

Contra Postgres de verdad y con un Meta de mentira. Lo que se prueba es el
Definition of Done de la parte 1:

* Un mensaje con plantilla de Flow se crea, se valida (Flow publicado,
  plantilla aprobada) y se envía con los cuatro públicos.
* Después de migrar (`boveda/0036`), «No enviado aún» sobre el mensaje de Flow
  no incluye a quienes ya lo habían recibido por la sección vieja.
* El conteo de convocados es el mismo en el listado y en el detalle.
* Las respuestas de un Flow se siguen ingestando igual: el `flow_token` es el
  `id_persona` y mapea directo (no regresión).
"""

import importlib.util
import pathlib

import pytest

from panel_api import db, encuestas, mensajes, paneles, personas, preferencias, whatsapp
from panel_api.errores import DatosInvalidos

from conftest import consentimientos

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ENTORNO = {"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "111",
           "WHATSAPP_WABA_ID": "222"}


def _plantilla(nombre, boton="flow", estado="APPROVED", cuerpo="Hola {{1}}."):
    botones = {"flow": [{"type": "FLOW", "text": "Responder", "flow_id": "FLOW-9"}],
               "url": [{"type": "URL", "text": "Responder",
                        "url": "https://survey.alchemer.com/s3/1/x?pid={{1}}"}]}[boton]
    return {"name": nombre, "language": "es", "status": estado, "category": "MARKETING",
            "components": [{"type": "BODY", "text": cuerpo},
                           {"type": "BUTTONS", "buttons": botones}]}


class MetaFalsa:
    def __init__(self):
        self.plantillas = [_plantilla("encuesta_flow"), _plantilla("invitacion_url", "url")]
        self.flow = "PUBLISHED"
        self.enviados = []

    def __call__(self, url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": self.plantillas}
        if url.endswith("/messages"):
            self.enviados.append(datos)
            return {"messages": [{"id": f"wamid.flow.{len(self.enviados)}"}]}
        return {"id": "FLOW-9", "name": "Encuesta en WhatsApp", "status": self.flow}


@pytest.fixture
def meta(monkeypatch):
    falsa = MetaFalsa()
    for clave, valor in ENTORNO.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setattr(whatsapp, "_pedir", falsa)
    return falsa


def _panelista(conn, n):
    id_persona = str(personas.alta(conn, {
        "persona": {"documento": f"7-{n}", "nombre": f"Persona{n}",
                    "celular": f"09{n:07d}"},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico")})
        ["id_persona"])
    preferencias.otorgar(conn, id_persona, preferencias.WHATSAPP, "v1")
    return id_persona


@pytest.fixture
def ola(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    ids = [_panelista(conn_boveda, i) for i in range(1, 7)]
    for i in ids:
        paneles.agregar_miembro(conn_boveda, panel["id"], i)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola Flow")
    encuestas.convocar(conn_boveda, encuesta["id"], ids_persona=ids)
    conn_boveda.commit()
    return {"encuesta": encuestas.obtener(conn_boveda, encuesta["id"]), "ids": ids}


def _enviar(conn, encuesta_id, mensaje_id, publico):
    ids = mensajes.publico(conn, encuesta_id, mensaje_id, publico)
    return mensajes.procesar_lote(
        conn, {"encuesta_id": encuesta_id, "mensaje_id": mensaje_id, "publico": publico},
        [{"id_persona": i} for i in ids])


# ── Un mensaje de Flow ────────────────────────────────────────────────

def test_las_plantillas_de_un_mensaje_dicen_que_boton_llevan(meta):
    salida = mensajes.plantillas_para_mensajes()
    por_nombre = {p["nombre"]: p for p in salida["plantillas"]}
    assert por_nombre["encuesta_flow"]["boton"] == "flow"
    assert por_nombre["encuesta_flow"]["boton_etiqueta"] == "Botón de Flow"
    assert por_nombre["invitacion_url"]["boton"] == "url"
    assert por_nombre["invitacion_url"]["boton_etiqueta"] == "Botón de URL"


def test_un_mensaje_de_flow_se_crea_se_valida_y_se_envia_a_los_cuatro_publicos(
        conn_boveda, meta, ola):
    encuesta = ola["encuesta"]
    mensaje = mensajes.guardar(conn_boveda, encuesta["id"],
                               {"nombre": "Encuesta por WhatsApp",
                                "plantilla": "encuesta_flow",
                                "parametros": ["nombre_panelista"]})
    assert (mensaje["boton"], mensaje["flow_id"]) == ("flow", "FLOW-9")

    # «Respondieron»: los que tienen respuestas ingestadas.
    encuestas.marcar_respuesta(conn_boveda, encuesta["id"], ola["ids"][:2])
    # Los cuatro públicos, con la misma regla de siempre.
    for publico in mensajes.PUBLICOS:
        previa = mensajes.previa(conn_boveda, encuesta["id"], mensaje["id"], publico)
        assert previa["puede_enviar"], previa["motivos"]

    salida = _enviar(conn_boveda, encuesta["id"], mensaje["id"], mensajes.RESPONDIERON)
    assert salida["enviados"] == 2
    # «No contestaron» y «No enviado aún» no le repiten a esos dos.
    assert set(mensajes.publico(conn_boveda, encuesta["id"], mensaje["id"],
                                mensajes.NO_ENVIADO)) == set(ola["ids"][2:])
    salida = _enviar(conn_boveda, encuesta["id"], mensaje["id"], mensajes.NO_CONTESTARON)
    assert salida["enviados"] == 4
    # Cada envío es un Flow con el id_persona como flow_token, y queda en `envio`.
    tokens = {e["template"]["components"][-1]["parameters"][0]["action"]["flow_token"]
              for e in meta.enviados}
    assert tokens == set(ola["ids"])
    assert db.una(conn_boveda, "select count(*)::int as n from envio "
                               "where encuesta_mensaje_id = %s and estado = 'aceptado'",
                  (mensaje["id"],))["n"] == 6
    # «Todos» avisa del reenvío.
    previa = mensajes.previa(conn_boveda, encuesta["id"], mensaje["id"], mensajes.TODOS)
    assert previa["ya_recibieron"] == 6 and previa["avisos"]


def test_un_flow_sin_publicar_no_se_guarda_ni_se_envia(conn_boveda, meta, ola):
    encuesta = ola["encuesta"]
    mensaje = mensajes.guardar(conn_boveda, encuesta["id"],
                               {"nombre": "Flow", "plantilla": "encuesta_flow",
                                "parametros": ["nombre_panelista"]})
    meta.flow = "DRAFT"
    previa = mensajes.previa(conn_boveda, encuesta["id"], mensaje["id"], mensajes.TODOS)
    assert not previa["puede_enviar"]
    assert any("publicado" in m for m in previa["motivos"])
    with pytest.raises(DatosInvalidos):
        mensajes.guardar(conn_boveda, encuesta["id"],
                         {"nombre": "Otro Flow", "plantilla": "encuesta_flow",
                          "parametros": ["nombre_panelista"]})


def test_un_mensaje_de_url_sigue_igual(conn_boveda, meta, ola):
    encuesta = ola["encuesta"]
    encuestas.editar(conn_boveda, encuesta["id"], {
        "url_alchemer": "https://survey.alchemer.com/s3/1/x?pid="})
    mensaje = mensajes.guardar(conn_boveda, encuesta["id"],
                               {"nombre": "Invitación", "plantilla": "invitacion_url",
                                "parametros": ["nombre_panelista"]})
    assert mensaje["boton"] == "url" and mensaje["flow_id"] is None
    _enviar(conn_boveda, encuesta["id"], mensaje["id"], mensajes.TODOS)
    boton = meta.enviados[0]["template"]["components"][-1]
    assert boton["sub_type"] == "url"


# ── La migración de lo ya enviado ─────────────────────────────────────

def _script():
    spec = importlib.util.spec_from_file_location(
        "migrar_flow_a_mensajes", RAIZ / "scripts" / "migrar_flow_a_mensajes.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _como_la_seccion_vieja(conn, encuesta_id, enviados, fallidos):
    """Lo que dejaba `enviar_por_whatsapp`: la configuración en la encuesta y
    el envío en `participacion`."""
    db.ejecutar(conn, """update encuesta set flow_plantilla = 'encuesta_flow',
                                flow_idioma = 'es', flow_id = 'FLOW-9' where id = %s""",
                (encuesta_id,))
    db.ejecutar(conn, """update participacion set envio_estado = 'enviado',
                                enviado_en = now() - interval '3 days'
                          where encuesta_id = %s and id_persona = any(%s::uuid[])""",
                (encuesta_id, enviados))
    db.ejecutar(conn, """update participacion set envio_estado = 'fallido',
                                envio_error = 'Número inválido'
                          where encuesta_id = %s and id_persona = any(%s::uuid[])""",
                (encuesta_id, fallidos))
    conn.commit()


def test_la_corrida_de_prueba_informa_sin_escribir(conn_boveda, ola, capsys):
    encuesta = ola["encuesta"]
    _como_la_seccion_vieja(conn_boveda, encuesta["id"], ola["ids"][:3], ola["ids"][3:4])
    assert _script().prueba(conn_boveda) == 0
    salida = capsys.readouterr().out
    assert "1 mensaje(s) «WhatsApp Flow» y 4 envío(s) (3 aceptados, 1 fallidos)" in salida
    assert not mensajes.listar(conn_boveda, encuesta["id"])


def test_despues_de_migrar_no_enviado_aun_no_repite_el_flow(conn_boveda, meta, ola):
    encuesta = ola["encuesta"]
    ya_recibieron, fallo = ola["ids"][:3], ola["ids"][3]
    _como_la_seccion_vieja(conn_boveda, encuesta["id"], ya_recibieron, [fallo])

    script = _script()
    db.ejecutar(conn_boveda, script._sin_transaccion(script.MIGRACION.read_text()))
    conn_boveda.commit()
    # Re-ejecutable: aplicarla de nuevo no duplica nada.
    db.ejecutar(conn_boveda, script._sin_transaccion(script.MIGRACION.read_text()))
    conn_boveda.commit()
    assert script.verificar(conn_boveda) == 0

    [migrado] = mensajes.listar(conn_boveda, encuesta["id"])
    assert (migrado["nombre"], migrado["boton"], migrado["flow_id"]) == (
        "WhatsApp Flow", "flow", "FLOW-9")
    assert (migrado["recibieron"], migrado["fallidos"]) == (3, 1)
    fila = db.una(conn_boveda, "select error from envio where id_persona = %s", (fallo,))
    assert fila["error"] == "Número inválido"

    pendientes = set(mensajes.publico(conn_boveda, encuesta["id"], migrado["id"],
                                      mensajes.NO_ENVIADO))
    assert pendientes == set(ola["ids"]) - set(ya_recibieron)
    assert fallo in pendientes, "un fallido cuenta como no enviado"


# ── El conteo de convocados ───────────────────────────────────────────

def test_el_conteo_de_convocados_es_el_mismo_en_el_listado_y_en_el_detalle(conn_boveda, ola):
    encuesta = ola["encuesta"]
    # Respondió sin estar convocado: está en la participación y no es convocado.
    intruso = _panelista(conn_boveda, 99)
    encuestas.registrar_participacion_importada(conn_boveda, encuesta["id"], [intruso])
    conn_boveda.commit()
    detalle = encuestas.obtener(conn_boveda, encuesta["id"], completa=True)
    [en_el_listado] = [e for e in encuestas.listar(conn_boveda)
                       if e["id"] == encuesta["id"]]
    assert detalle["convocados"] == en_el_listado["convocados"] == len(ola["ids"])
    assert "es_flow" not in detalle


# ── No regresión: las respuestas de un Flow ───────────────────────────

def test_las_respuestas_de_un_flow_mapean_por_el_flow_token(ctx, ola):
    """El sistema solo envía: las respuestas se bajan de Meta y se ingestan
    por el flujo de siempre. El `flow_token` es el `id_persona`, así que la
    columna mapea directo, sin PII y sin adivinar."""
    encuesta = ola["encuesta"]
    preguntas = [{"codigo": "P1", "texto": "¿Qué bebida consume?", "tipo": "abierta",
                  "orden": 1}]
    filas = [{"flow_token": i, "P1": f"respuesta {n}"} for n, i in enumerate(ola["ids"][:2])]
    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"], preguntas, filas,
        columna_id="flow_token", proveedor=ctx.embeddings,
        tipo_identificador="id_persona")
    assert resultado["tipo_identificador"] == "id_persona"
    respondieron = {str(f["id_persona"]) for f in db.todas(
        ctx.boveda, "select id_persona from participacion where encuesta_id = %s "
                    "and respondio", (encuesta["id"],))}
    assert respondieron == set(ola["ids"][:2])

