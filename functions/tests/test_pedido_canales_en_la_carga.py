"""PEDIDO 06/11/26 · Parte 2 (D88) — la evidencia de canal en la carga.

La carga registraba el consentimiento de contacto y nunca el canal: nadie
que hubiera entrado por una carga podía recibir mensajes. El Definition of
Done de la parte 2, contra Postgres de verdad:

* Una carga con canales declarados deja a sus personas con esos canales y con
  el texto de evidencia (R-CAN.1).
* Una persona sin celular válido no recibe WhatsApp y figura en la vista
  previa.
* Una persona con el canal revocado no se reactiva en bloque (R-CAN.3).
* El registro en bloque sobre una carga existente queda auditado y exige
  `gestionar_canales` (R-CAN.2).
* Después de registrar WhatsApp para la carga, la sección Mensajes muestra a
  esas personas como elegibles.
"""

import pytest

from panel_api import (
    canales_carga, cargas, db, diferida, encuestas, mensajes, preferencias, ruteo,
    whatsapp,
)
from panel_api.errores import DatosInvalidos, SinPermiso

VERSION = "campo-2026-09"
PREGUNTAS = [{"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
              "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Whisky"}, "orden": 1}]
DEMOGRAFICAS = {"NOM": "nombre", "DOC": "documento", "CEL": "celular", "MAIL": "email"}
EVIDENCIA = {
    "contacto_participacion": {"variable": "CONS", "valor_afirmativo": "1",
                               "version_texto": VERSION},
    "uso_semantico": {"variable": "CONS", "valor_afirmativo": "1",
                      "version_texto": VERSION},
}
CANALES = {"whatsapp": VERSION, "email": VERSION}


def _fila(i, celular=None, email=None):
    return {"ID": f"c-{i}", "NOM": f"Persona {i}", "DOC": f"31-{i}",
            "CEL": celular if celular is not None else f"09{i:07d}",
            "MAIL": email if email is not None else f"p{i}@ej.uy",
            "CONS": "1", "P1": "1"}


FILAS = [_fila(1), _fila(2), _fila(3, celular="12"), _fila(4, email="")]


def _cuerpo(**extra):
    return {"filas": FILAS, "columna_id": "ID", "modo": "crear_individuos",
            "preguntas": PREGUNTAS, "demograficas": DEMOGRAFICAS,
            "evidencia_consentimiento": EVIDENCIA, "proponer_tematicas": False,
            **extra}


def _procesar(ctx):
    pendientes = list(ctx.encolador.encoladas)
    ctx.encolador.encoladas.clear()
    return [diferida.procesar_lote(ctx.boveda, ctx.semantica, t, i, proveedor=ctx.embeddings)
            for t, i in pendientes]


def _canales_de(conn, documento):
    return {f["canal"]: f for f in db.todas(
        conn, """select pc.canal, pc.estado, pc.version_texto, pc.origen
                   from preferencia_canal pc join persona p using (id_persona)
                  where p.documento = %s""", (documento,))}


@pytest.fixture
def carga(conn_boveda):
    return cargas.crear(conn_boveda, "ANTEL — base de campo", tipo_datos_origen="encuesta",
                        modo_relevamiento="telefonico")


# ── R-CAN.1 · Declarar los canales al cargar ──────────────────────────

def test_una_carga_con_canales_declarados_deja_a_su_gente_con_esos_canales(
        ctx, actor, carga):
    ruta = f"/cargas/{carga['id']}/ingesta"
    _, revision = ruteo.despachar("POST", ruta,
                                  _cuerpo(canales_aceptados=CANALES, solo_revisar=True),
                                  {}, actor("operaciones"), ctx)
    previa = revision["canales"]["canales"]
    # La vista previa: cuántas quedan con cada canal y cuántas no, por motivo.
    assert previa["whatsapp"]["quedaran"] == 3
    assert previa["whatsapp"]["no"] == {"sin_celular_valido": 1}
    assert previa["email"]["quedaran"] == 3
    assert previa["email"]["no"] == {"sin_correo": 1}

    status, _ = ruteo.despachar("POST", ruta, _cuerpo(canales_aceptados=CANALES), {},
                                actor("operaciones"), ctx)
    assert status == 202
    _procesar(ctx)

    con_todo = _canales_de(ctx.boveda, "31-1")
    assert set(con_todo) == {"whatsapp", "email"}
    assert {c["version_texto"] for c in con_todo.values()} == {VERSION}
    assert {c["origen"] for c in con_todo.values()} == {"carga"}
    # Sin celular válido no recibe WhatsApp; sin correo, no el correo.
    assert set(_canales_de(ctx.boveda, "31-3")) == {"email"}
    assert set(_canales_de(ctx.boveda, "31-4")) == {"whatsapp"}
    # Y queda auditado: quién lo declaró, por qué vía y a cuántas personas.
    [registro] = canales_carga.historial(ctx.boveda, carga["id"])
    assert (registro["via"], registro["personas"]) == ("ingesta", 4)
    assert registro["canales"] == CANALES
    assert registro["actor"] == "operaciones@equipos.com.uy"


def test_sin_canales_la_carga_hace_lo_de_siempre(ctx, actor, carga):
    ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta", _cuerpo(), {},
                    actor("analista"), ctx)
    _procesar(ctx)
    assert not _canales_de(ctx.boveda, "31-1")
    assert not canales_carga.historial(ctx.boveda, carga["id"])


def test_sin_texto_no_se_registra(ctx, actor, carga):
    for declaracion, motivo in (({"whatsapp": ""}, "sin_texto"),
                                ({"whatsapp": "no-publicado"}, "texto_no_publicado")):
        with pytest.raises(DatosInvalidos) as error:
            ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta",
                            _cuerpo(canales_aceptados=declaracion), {},
                            actor("operaciones"), ctx)
        assert error.value.detalle["motivo"] == motivo
    assert not ctx.encolador.encoladas


def test_declarar_canales_al_cargar_exige_gestionar_canales(ctx, actor, carga):
    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta",
                        _cuerpo(canales_aceptados=CANALES), {}, actor("analista"), ctx)


# ── R-CAN.2 / R-CAN.3 · En bloque, para una carga existente ───────────

@pytest.fixture
def cargada(ctx, actor, carga):
    """Una carga ya hecha, sin canales: como la de ANTEL."""
    ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta", _cuerpo(), {},
                    actor("analista"), ctx)
    _procesar(ctx)
    ctx.boveda.commit()
    return carga


def _id(conn, documento):
    return str(db.una(conn, "select id_persona from persona where documento = %s",
                      (documento,))["id_persona"])


def test_un_canal_revocado_no_se_reactiva_en_bloque(ctx, actor, cargada):
    revoco = _id(ctx.boveda, "31-2")
    preferencias.otorgar(ctx.boveda, revoco, "whatsapp", "v-landing", origen="landing")
    preferencias.revocar(ctx.boveda, revoco, "whatsapp")
    ctx.boveda.commit()

    ruta = f"/cargas/{cargada['id']}/canales"
    _, previa = ruteo.despachar("POST", ruta, {"canales": {"whatsapp": VERSION},
                                               "solo_revisar": True}, {},
                                actor("operaciones"), ctx)
    assert previa["canales"]["whatsapp"]["no"] == {"revoco_el_canal": 1,
                                                    "sin_celular_valido": 1}
    _, salida = ruteo.despachar("POST", ruta, {"canales": {"whatsapp": VERSION}}, {},
                                actor("operaciones"), ctx)
    assert salida["canales"]["whatsapp"]["registrados"] == 2
    assert salida["canales"]["whatsapp"]["no"]["revoco_el_canal"] == 1
    assert _canales_de(ctx.boveda, "31-2")["whatsapp"]["estado"] == "revocada"

    # De a una persona, desde su ficha, sigue siendo posible.
    preferencias.otorgar(ctx.boveda, revoco, "whatsapp", VERSION, origen="edicion")
    assert _canales_de(ctx.boveda, "31-2")["whatsapp"]["estado"] == "activa"


def test_quien_ya_tenia_el_canal_conserva_su_evidencia(ctx, actor, cargada):
    propia = _id(ctx.boveda, "31-1")
    preferencias.otorgar(ctx.boveda, propia, "whatsapp", "v-landing", origen="landing")
    ctx.boveda.commit()
    _, salida = ruteo.despachar("POST", f"/cargas/{cargada['id']}/canales",
                                {"canales": {"whatsapp": VERSION}}, {},
                                actor("operaciones"), ctx)
    assert salida["canales"]["whatsapp"]["ya_lo_tenian"] == 1
    canal = _canales_de(ctx.boveda, "31-1")["whatsapp"]
    assert (canal["version_texto"], canal["origen"]) == ("v-landing", "landing")


def test_el_registro_en_bloque_queda_auditado(ctx, actor, cargada):
    ruteo.despachar("POST", f"/cargas/{cargada['id']}/canales",
                    {"canales": {"whatsapp": VERSION, "email": VERSION}}, {},
                    actor("admin"), ctx)
    _, leido = ruteo.despachar("GET", f"/cargas/{cargada['id']}/canales", {}, {},
                               actor("analista"), ctx)
    [registro] = leido["historial"]
    assert registro["via"] == "bloque"
    assert registro["actor"] == "admin@equipos.com.uy"
    assert registro["canales"] == {"whatsapp": VERSION, "email": VERSION}
    assert registro["personas"] == 4
    assert registro["resultado"]["whatsapp"]["registrados"] == 3
    assert registro["resultado"]["email"]["no"] == {"sin_correo": 1}
    assert VERSION in {t["version"] for t in leido["textos"]}


def test_la_vista_previa_no_escribe(ctx, actor, cargada):
    ruteo.despachar("POST", f"/cargas/{cargada['id']}/canales",
                    {"canales": {"whatsapp": VERSION}, "solo_revisar": True}, {},
                    actor("operaciones"), ctx)
    assert not _canales_de(ctx.boveda, "31-1")
    assert not canales_carga.historial(ctx.boveda, cargada["id"])


def test_registrar_en_bloque_exige_gestionar_canales(ctx, actor, cargada):
    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", f"/cargas/{cargada['id']}/canales",
                        {"canales": {"whatsapp": VERSION}}, {}, actor("analista"), ctx)


# ── Después: la sección Mensajes los ve elegibles ─────────────────────

def test_despues_de_registrar_whatsapp_los_mensajes_los_muestran_elegibles(
        ctx, actor, cargada, monkeypatch):
    for clave, valor in {"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "1",
                         "WHATSAPP_WABA_ID": "2"}.items():
        monkeypatch.setenv(clave, valor)
    plantilla = {"name": "gracias", "language": "es", "status": "APPROVED",
                 "category": "UTILITY", "components": [{"type": "BODY", "text": "Gracias."}]}
    monkeypatch.setattr(whatsapp, "_pedir", lambda url, *a, **k: {"data": [plantilla]})
    encuesta_id = cargada["encuesta_id"]
    encuestas.cambiar_estado(ctx.boveda, encuesta_id, "en_campo")
    mensaje = mensajes.guardar(ctx.boveda, encuesta_id, {"nombre": "Gracias",
                                                         "plantilla": "gracias"})

    antes = mensajes.previa(ctx.boveda, encuesta_id, mensaje["id"], mensajes.TODOS)
    assert antes["recibiran"] == 0
    assert antes["excluidos"]["sin_preferencia_whatsapp"] == 4

    canales_carga.registrar_en_bloque(ctx.boveda, cargada["id"], {"whatsapp": VERSION})
    despues = mensajes.previa(ctx.boveda, encuesta_id, mensaje["id"], mensajes.TODOS)
    assert despues["recibiran"] == 3
    assert despues["excluidos"] == {"sin_preferencia_whatsapp": 1}
