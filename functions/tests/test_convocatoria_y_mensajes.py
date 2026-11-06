"""SPEC_convocatoria_y_mensajes (D85): el campo se opera desde la encuesta.

Contra Postgres de verdad y con un Meta de mentira (`MetaFalsa`) que anota
cada envío y puede rechazar números puntuales. Sigue los criterios de
aceptación de la spec, requisito por requisito:

* R-CF.1 — el estado se cambia en cualquier sentido y queda registrado; una
  encuesta cerrada o en borrador no envía.
* R-CF.2 — agregar panelistas de a uno o en lista, por el camino de
  `convocar`, con el resultado línea por línea.
* R-CF.3 — la URL de Alchemer.
* R-CF.4 — mensajes: plantilla aprobada, parámetros, aviso de URL distinta.
* R-CF.5 / R-CF.6 — los cuatro públicos, la vista previa, el envío en
  lotes, el registro, nunca dos veces, y el primero convoca.
* R-CF.7 — el webhook: firma, estados, sin retroceder.
* R-CF.8 — el canal correo.
"""

import hashlib
import hmac
import json

import pytest

from panel_api import (
    cargas, db, diferida, encuestas, lista_negra, mensajes, paneles, personas,
    preferencias, ruteo, whatsapp,
)
from panel_api.errores import Conflicto, DatosInvalidos, IngestaEnCurso, SinPermiso

from conftest import consentimientos

URL = "https://survey.alchemer.com/s3/1234567/estudio?pid="
ENTORNO = {"WHATSAPP_TOKEN": "t", "WHATSAPP_PHONE_NUMBER_ID": "111",
           "WHATSAPP_WABA_ID": "222", "WHATSAPP_APP_SECRET": "secreto-de-la-app",
           "WHATSAPP_VERIFY_TOKEN": "token-de-alta"}


def _plantilla(nombre, url=URL + "{{1}}", estado="APPROVED", cuerpo="Hola {{1}}, "
               "te invitamos a {{2}}."):
    componentes = [{"type": "BODY", "text": cuerpo}]
    if url:
        componentes.append({"type": "BUTTONS", "buttons": [
            {"type": "URL", "text": "Responder", "url": url}]})
    return {"name": nombre, "language": "es", "status": estado,
            "category": "MARKETING", "components": componentes}


class MetaFalsa:
    def __init__(self):
        self.plantillas = [_plantilla("invitacion_estudio"),
                           _plantilla("otra_encuesta",
                                      url="https://survey.alchemer.com/s3/999/otra?pid={{1}}"),
                           _plantilla("en_revision", estado="PENDING"),
                           _plantilla("gracias", url=None, cuerpo="Gracias {{1}}.")]
        self.enviados = []
        self.rechazar = set()

    def __call__(self, url, datos=None, token=None, metodo=None):
        if "message_templates" in url:
            return {"data": self.plantillas}
        if url.endswith("/messages"):
            if datos["to"] in self.rechazar:
                raise Conflicto("Número inválido para WhatsApp.", {"status": 400})
            self.enviados.append(datos)
            return {"messages": [{"id": f"wamid.{len(self.enviados)}"}]}
        raise AssertionError(f"pedido inesperado: {url}")


@pytest.fixture
def meta(monkeypatch):
    falsa = MetaFalsa()
    for clave, valor in ENTORNO.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setattr(whatsapp, "_pedir", falsa)
    return falsa


def _panelista(conn, n, whatsapp_ok=True, email=True):
    id_persona = str(personas.alta(conn, {
        "persona": {"documento": f"9-{n}", "nombre": f"Persona{n} Apellido",
                    "celular": f"09{n:07d}",
                    **({"email": f"p{n}@ej.uy"} if email else {})},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico")})
        ["id_persona"])
    if whatsapp_ok:
        preferencias.otorgar(conn, id_persona, preferencias.WHATSAPP, "v1")
    preferencias.otorgar(conn, id_persona, preferencias.EMAIL, "v1")
    return id_persona


@pytest.fixture
def ola(conn_boveda):
    """Una ola de panel en campo, con diez convocados y su URL de Alchemer."""
    panel = paneles.crear(conn_boveda, "Panel general")
    ids = [_panelista(conn_boveda, i) for i in range(1, 11)]
    for i in ids:
        paneles.agregar_miembro(conn_boveda, panel["id"], i)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola de prueba")
    encuestas.editar(conn_boveda, encuesta["id"], {"url_alchemer": URL})
    encuestas.convocar(conn_boveda, encuesta["id"], ids_persona=ids)
    conn_boveda.commit()
    return {"encuesta": encuestas.obtener(conn_boveda, encuesta["id"]), "ids": ids,
            "panel": panel}


def _mensaje(ctx, actor, encuesta_id, **datos):
    cuerpo = {"nombre": "Invitación", "canal": "whatsapp",
              "plantilla": "invitacion_estudio",
              "parametros": ["nombre_panelista", "nombre_encuesta"], **datos}
    _, salida = ruteo.despachar("POST", f"/encuestas/{encuesta_id}/mensajes", cuerpo,
                                {}, actor("operaciones"), ctx)
    return salida


def _envio(ctx, actor, encuesta_id, mensaje_id, publico, solo_revisar=False, rol="operaciones"):
    return ruteo.despachar(
        "POST", f"/encuestas/{encuesta_id}/mensajes/{mensaje_id}/envio",
        {"publico": publico, **({"solo_revisar": True} if solo_revisar else {})}, {},
        actor(rol), ctx)


def _procesar(ctx):
    pendientes = list(ctx.encolador.encoladas)
    ctx.encolador.encoladas.clear()
    return [diferida.procesar_lote(ctx.boveda, ctx.semantica, t, i, proveedor=ctx.embeddings)
            for t, i in pendientes]


def _enviar(ctx, actor, encuesta_id, mensaje_id, publico):
    status, salida = _envio(ctx, actor, encuesta_id, mensaje_id, publico)
    assert status == 202
    _procesar(ctx)
    return salida


def _recibieron(conn, mensaje_id):
    return {str(f["id_persona"]) for f in db.todas(
        conn, "select id_persona from envio where encuesta_mensaje_id = %s "
              "and estado <> 'fallido'", (mensaje_id,))}


# ════════════════════════════════════════════════════════════════════
#  R-CF.1 · El estado
# ════════════════════════════════════════════════════════════════════

def test_una_encuesta_de_carga_se_reabre_y_queda_registrado(ctx, actor, meta):
    carga = cargas.crear(ctx.boveda, "AVANTI")
    encuesta_id = carga["encuesta_id"]
    assert encuestas.obtener(ctx.boveda, encuesta_id)["estado"] == "cerrada"
    _, salida = ruteo.despachar("PATCH", f"/encuestas/{encuesta_id}",
                                {"estado": "en_campo"}, {}, actor("analista"), ctx)
    assert salida["estado"] == "en_campo"
    _, historial = ruteo.despachar("GET", f"/encuestas/{encuesta_id}/estados", {}, {},
                                   actor("analista"), ctx)
    assert [(h["estado_anterior"], h["estado_nuevo"], h["actor"])
            for h in historial["items"]] == [("cerrada", "en_campo",
                                              "analista@equipos.com.uy")]
    # Volver a pedir el mismo estado no deja una fila de más.
    encuestas.cambiar_estado(ctx.boveda, encuesta_id, "en_campo")
    assert len(encuestas.historial_de_estado(ctx.boveda, encuesta_id)) == 1


@pytest.mark.parametrize("estado, texto", [("cerrada", "cerrada"), ("borrador", "borrador")])
def test_cerrada_o_en_borrador_no_envia(ctx, actor, meta, ola, estado, texto):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    encuestas.cambiar_estado(ctx.boveda, ola["encuesta"]["id"], estado)
    ctx.boveda.commit()
    _, previa = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos", True)
    assert previa["puede_enviar"] is False
    assert any(texto in m for m in previa["motivos"])
    with pytest.raises(Conflicto, match=texto):
        _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    assert not meta.enviados


# ════════════════════════════════════════════════════════════════════
#  R-CF.2 · Agregar panelistas a mano
# ════════════════════════════════════════════════════════════════════

def test_agregar_una_lista_mezclada_informa_linea_por_linea(ctx, actor, conn_boveda, ola):
    nueva = _panelista(conn_boveda, 50)
    por_correo = _panelista(conn_boveda, 51)
    por_celular = _panelista(conn_boveda, 52)
    vetada = _panelista(conn_boveda, 53)
    lista_negra.agregar(conn_boveda, vetada, "calidad_respuestas", "al azar")
    sin_contacto = str(personas.alta(conn_boveda, {
        "persona": {"documento": "9-54", "nombre": "Sin contacto"},
        "consentimientos": consentimientos("uso_semantico")})["id_persona"])
    # Dos personas con el mismo celular: ambiguo.
    for n in (55, 56):
        personas.alta(conn_boveda, {
            "persona": {"documento": f"9-{n}", "nombre": f"Hogar {n}",
                        "celular": "098 765 432"},
            "consentimientos": consentimientos("contacto_participacion")})
    conn_boveda.commit()

    lineas = "\n".join([nueva, "P51@EJ.UY", "090000052", ola["ids"][0], vetada,
                        sin_contacto, "noexiste@ej.uy", "098765432", ".....", ""])
    _, salida = ruteo.despachar("POST", f"/encuestas/{ola['encuesta']['id']}/panelistas",
                                {"lineas": lineas}, {}, actor("analista"), ctx)
    resultado = {l["linea"]: l["resultado"] for l in salida["lineas"]}
    assert resultado == {
        nueva: "agregado", "P51@EJ.UY": "agregado", "090000052": "agregado",
        ola["ids"][0]: "ya_estaba", vetada: "en_lista_negra",
        sin_contacto: "sin_consentimiento", "noexiste@ej.uy": "no_corresponde",
        "098765432": "ambiguo", ".....": "formato_invalido"}
    convocados = {str(f["id_persona"]) for f in db.todas(
        conn_boveda, "select id_persona from participacion where encuesta_id = %s "
                     "and convocado", (ola["encuesta"]["id"],))}
    assert {nueva, por_correo, por_celular} <= convocados
    assert vetada not in convocados and sin_contacto not in convocados
    # No se crea a nadie.
    assert not db.una(conn_boveda, "select 1 as hay from persona where email = 'noexiste@ej.uy'")


def test_una_encuesta_cerrada_no_admite_agregar(ctx, actor, conn_boveda, ola):
    encuestas.cambiar_estado(conn_boveda, ola["encuesta"]["id"], "cerrada")
    conn_boveda.commit()
    with pytest.raises(Conflicto, match="cerrada"):
        ruteo.despachar("POST", f"/encuestas/{ola['encuesta']['id']}/panelistas",
                        {"lineas": ola["ids"][0]}, {}, actor("analista"), ctx)


def test_a_una_independiente_tambien_se_le_agregan_panelistas(ctx, actor, conn_boveda):
    alguien = _panelista(conn_boveda, 60)
    otra = _panelista(conn_boveda, 61)
    encuesta = encuestas.crear_desde_consulta(
        conn_boveda, "Seguimiento", {"items": [{"id_persona": alguien}]})
    conn_boveda.commit()
    _, salida = ruteo.despachar("POST", f"/encuestas/{encuesta['id']}/panelistas",
                                {"lineas": otra}, {}, actor("analista"), ctx)
    assert salida["lineas"][0]["resultado"] == "agregado"


# ════════════════════════════════════════════════════════════════════
#  R-CF.3 · La URL de Alchemer
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("url, mensaje", [
    ("survey.alchemer.com/s3/1/x?pid=", "https://"),
    ("https://survey.alchemer.com/s3/1/x", "terminar en el parámetro"),
    ("https://survey.alchemer.com/s3 /1/x?pid=", "espacios"),
])
def test_la_url_de_alchemer_se_valida(conn_boveda, ola, url, mensaje):
    with pytest.raises(DatosInvalidos, match=mensaje):
        encuestas.editar(conn_boveda, ola["encuesta"]["id"], {"url_alchemer": url})


def test_el_enlace_es_la_url_mas_el_id_persona(ola):
    assert encuestas.enlace_de(ola["encuesta"], ola["ids"][0]) == URL + ola["ids"][0]


# ════════════════════════════════════════════════════════════════════
#  R-CF.4 · Los mensajes
# ════════════════════════════════════════════════════════════════════

def test_las_plantillas_incluyen_las_de_boton_de_url(ctx, actor, meta):
    _, salida = ruteo.despachar("GET", "/mensajes/plantillas", {}, {}, actor("analista"), ctx)
    por = {p["nombre"]: p for p in salida["plantillas"]}
    assert por["invitacion_estudio"]["boton_url"]["base"] == URL
    assert por["invitacion_estudio"]["parametros_cuerpo"] == 2
    assert por["gracias"]["boton_url"] is None
    assert "en_revision" not in por        # solo las aprobadas


def test_una_plantilla_no_aprobada_no_se_asocia(ctx, actor, meta, ola):
    with pytest.raises(DatosInvalidos, match="PENDING"):
        _mensaje(ctx, actor, ola["encuesta"]["id"], plantilla="en_revision")


def test_cada_parametro_tiene_que_tener_de_donde_sale(ctx, actor, meta, ola):
    with pytest.raises(DatosInvalidos, match="parámetro"):
        _mensaje(ctx, actor, ola["encuesta"]["id"], parametros=["nombre_panelista"])
    with pytest.raises(DatosInvalidos, match="desconocido"):
        _mensaje(ctx, actor, ola["encuesta"]["id"],
                 parametros=["nombre_panelista", "documento"])


def test_un_boton_que_lleva_a_otra_encuesta_se_avisa_antes_de_guardar(ctx, actor, meta, ola):
    with pytest.raises(Conflicto) as error:
        _mensaje(ctx, actor, ola["encuesta"]["id"], plantilla="otra_encuesta")
    assert error.value.detalle["motivo"] == "url_distinta"
    salida = _mensaje(ctx, actor, ola["encuesta"]["id"], plantilla="otra_encuesta",
                      confirmar_url_distinta=True)
    assert salida["avisos"]


def test_un_mensaje_enviado_no_se_borra(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    with pytest.raises(Conflicto, match="ya se envió"):
        ruteo.despachar("DELETE", f"/encuestas/{ola['encuesta']['id']}/mensajes/{mensaje['id']}",
                        {}, {}, actor("analista"), ctx)


# ════════════════════════════════════════════════════════════════════
#  R-CF.5 / R-CF.6 · Envío y registro
# ════════════════════════════════════════════════════════════════════

def test_no_enviado_aun_no_le_repite_a_nadie(ctx, actor, meta, ola):
    """Given 10 en la audiencia y el mensaje enviado a 6, When «No enviado
    aún», Then lo reciben los 4 restantes y ninguno de los 6."""
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id)
    primeros = ola["ids"][:6]
    # El primer envío fue a seis: los demás todavía no estaban.
    db.ejecutar(ctx.boveda, "delete from participacion where encuesta_id = %s "
                            "and not (id_persona = any(%s::uuid[]))", (encuesta_id, primeros))
    ctx.boveda.commit()
    _enviar(ctx, actor, encuesta_id, mensaje["id"], "todos")
    assert _recibieron(ctx.boveda, mensaje["id"]) == set(primeros)
    # Se suman los otros cuatro (R-CF.2) y se manda «No enviado aún».
    ruteo.despachar("POST", f"/encuestas/{encuesta_id}/panelistas",
                    {"lineas": "\n".join(ola["ids"][6:])}, {}, actor("analista"), ctx)
    antes = len(meta.enviados)
    _, previa = _envio(ctx, actor, encuesta_id, mensaje["id"], "no_enviado", True)
    assert previa["recibiran"] == 4 and previa["ya_recibieron"] == 0
    _enviar(ctx, actor, encuesta_id, mensaje["id"], "no_enviado")
    nuevos = [e["to"] for e in meta.enviados[antes:]]
    assert len(nuevos) == 4
    por_persona = db.todas(ctx.boveda, """select id_persona, count(*)::int as n from envio
                                           where encuesta_mensaje_id = %s group by 1""",
                           (mensaje["id"],))
    assert len(por_persona) == 10 and all(f["n"] == 1 for f in por_persona)


def test_no_contestaron_deja_afuera_a_quien_tiene_respuestas(ctx, actor, meta, ola):
    """Given 3 respondentes ingestados, When «No contestaron», Then ninguno
    de los 3 lo recibe; «Respondieron» es exactamente ellos."""
    encuesta_id = ola["encuesta"]["id"]
    respondieron = ola["ids"][:3]
    encuestas.marcar_respuesta(ctx.boveda, encuesta_id, respondieron)
    ctx.boveda.commit()
    recordatorio = _mensaje(ctx, actor, encuesta_id, nombre="Recordatorio")
    _enviar(ctx, actor, encuesta_id, recordatorio["id"], "no_contestaron")
    assert _recibieron(ctx.boveda, recordatorio["id"]) == set(ola["ids"][3:])
    gracias = _mensaje(ctx, actor, encuesta_id, nombre="Gracias", plantilla="gracias",
                       parametros=["nombre_panelista"])
    _enviar(ctx, actor, encuesta_id, gracias["id"], "respondieron")
    assert _recibieron(ctx.boveda, gracias["id"]) == set(respondieron)


def test_la_lista_negra_queda_afuera_de_cualquier_publico(ctx, actor, meta, ola):
    vetada = ola["ids"][0]
    # Respondió antes de entrar a la lista negra: su participación queda
    # (la lista negra solo cancela las invitaciones pendientes), así que
    # sigue en el público y es la exclusión la que la deja afuera.
    encuestas.marcar_respuesta(ctx.boveda, ola["encuesta"]["id"], [vetada])
    lista_negra.agregar(ctx.boveda, vetada, "calidad_respuestas", "al azar")
    ctx.boveda.commit()
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    for publico in ("todos", "no_enviado", "respondieron"):
        _, previa = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], publico, True)
        assert previa["excluidos"].get("lista_negra") == 1, publico
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    assert vetada not in _recibieron(ctx.boveda, mensaje["id"])


def test_la_previa_dice_quien_queda_afuera_y_avisa_el_reenvio(ctx, actor, meta,
                                                              conn_boveda, ola):
    sin_whatsapp = ola["ids"][1]
    preferencias.revocar(conn_boveda, sin_whatsapp, preferencias.WHATSAPP)
    conn_boveda.commit()
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    _, previa = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos", True)
    assert previa["en_el_publico"] == 10 and previa["recibiran"] == 9
    assert previa["excluidos"] == {"sin_preferencia_whatsapp": 1}
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    _, otra = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos", True)
    assert otra["ya_recibieron"] == 9
    assert any("de nuevo" in a for a in otra["avisos"])


def test_un_fallido_cuenta_como_no_enviado(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    falla = ola["ids"][2]
    celular = db.una(ctx.boveda, "select celular from persona where id_persona = %s",
                     (falla,))["celular"]
    meta.rechazar.add(preferencias.normalizar_celular(celular))
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    fila = db.una(ctx.boveda, "select estado, error from envio where id_persona = %s",
                  (falla,))
    assert fila["estado"] == "fallido" and "inválido" in fila["error"]
    meta.rechazar.clear()
    _, previa = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "no_enviado", True)
    assert previa["recibiran"] == 1


def test_el_envio_lleva_los_parametros_y_el_id_persona_en_el_boton(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    uno = next(e for e in meta.enviados)
    componentes = {c["type"]: c for c in uno["template"]["components"]}
    textos = [p["text"] for p in componentes["body"]["parameters"]]
    assert textos[1] == "Ola de prueba" and textos[0].startswith("Persona")
    boton = componentes["button"]
    assert boton["sub_type"] == "url"
    assert boton["parameters"][0]["text"] in ola["ids"]


def test_un_envio_en_curso_no_se_lanza_dos_veces(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    status, _ = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    assert status == 202
    with pytest.raises(IngestaEnCurso):
        _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")


def test_un_lote_reintentado_no_le_escribe_dos_veces_a_nadie(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    _, salida = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    trabajo = db.una(ctx.boveda, "select plan from ingesta_trabajo where id = %s",
                     (salida["trabajo_id"],))
    filas = [{"id_persona": i} for i in ola["ids"]]
    mensajes.procesar_lote(ctx.boveda, trabajo["plan"], filas, trabajo_id=salida["trabajo_id"])
    segunda = mensajes.procesar_lote(ctx.boveda, trabajo["plan"], filas,
                                     trabajo_id=salida["trabajo_id"])
    assert segunda["enviados"] == 0 and segunda["salteados"] == 10
    assert len(meta.enviados) == 10


def test_el_primer_mensaje_convoca_y_los_siguientes_no(ctx, actor, meta, conn_boveda, ola):
    encuesta_id = ola["encuesta"]["id"]
    db.ejecutar(conn_boveda, "update participacion set convocado_en = '2026-01-01' "
                             "where encuesta_id = %s", (encuesta_id,))
    conn_boveda.commit()
    invitacion = _mensaje(ctx, actor, encuesta_id)
    _enviar(ctx, actor, encuesta_id, invitacion["id"], "todos")
    fechas = {str(f["id_persona"]): f["convocado_en"] for f in db.todas(
        conn_boveda, "select id_persona, convocado_en from participacion "
                     "where encuesta_id = %s", (encuesta_id,))}
    assert all(f.year == 2026 and f.month != 1 or f.day != 1 for f in fechas.values())
    recordatorio = _mensaje(ctx, actor, encuesta_id, nombre="Recordatorio")
    _enviar(ctx, actor, encuesta_id, recordatorio["id"], "todos")
    despues = {str(f["id_persona"]): f["convocado_en"] for f in db.todas(
        conn_boveda, "select id_persona, convocado_en from participacion "
                     "where encuesta_id = %s", (encuesta_id,))}
    assert despues == fechas


def test_la_convocatoria_muestra_los_mensajes_de_cada_panelista(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    _, salida = ruteo.despachar("GET", f"/encuestas/{ola['encuesta']['id']}/participacion",
                                {}, {}, actor("analista"), ctx)
    uno = salida["items"][0]
    assert [(m["mensaje"], m["estado"]) for m in uno["mensajes"]] == [("Invitación", "aceptado")]


def test_enviar_es_de_operaciones_y_admin(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    with pytest.raises(SinPermiso):
        _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos", True, rol="analista")


# ════════════════════════════════════════════════════════════════════
#  R-CF.7 · El webhook de estados
# ════════════════════════════════════════════════════════════════════

def _aviso(*estados):
    return {"entry": [{"changes": [{"value": {"statuses": [
        {"id": i, "status": s, **({"errors": [{"title": "Número no existe"}]}
                                   if s == "failed" else {})}
        for i, s in estados]}}]}]}


def test_el_webhook_verifica_la_firma_y_falla_cerrado(meta, monkeypatch):
    cuerpo = json.dumps(_aviso(("wamid.1", "delivered"))).encode()
    firma = "sha256=" + hmac.new(b"secreto-de-la-app", cuerpo, hashlib.sha256).hexdigest()
    assert whatsapp.firma_valida(cuerpo, firma)
    assert not whatsapp.firma_valida(cuerpo + b" ", firma)
    assert not whatsapp.firma_valida(cuerpo, None)
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "")
    assert not whatsapp.firma_valida(cuerpo, firma)
    # El alta: solo con el token.
    assert whatsapp.verificar_suscripcion("subscribe", "token-de-alta", "42") == "42"
    assert whatsapp.verificar_suscripcion("subscribe", "otro", "42") is None


def test_los_estados_avanzan_y_no_retroceden(ctx, actor, meta, ola):
    mensaje = _mensaje(ctx, actor, ola["encuesta"]["id"])
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    estados = whatsapp.estados_del_webhook(_aviso(
        ("wamid.1", "delivered"), ("wamid.1", "read"), ("wamid.1", "delivered"),
        ("wamid.2", "failed"), ("wamid.3", "sent"), ("wamid.no-es-nuestro", "read")))
    assert mensajes.aplicar_estados(ctx.boveda, estados) == 3
    por_id = {f["id_mensaje_meta"]: f for f in db.todas(
        ctx.boveda, "select id_mensaje_meta, estado, error from envio")}
    assert por_id["wamid.1"]["estado"] == "leido"
    assert por_id["wamid.2"]["estado"] == "fallido"
    assert por_id["wamid.2"]["error"] == "Número no existe"
    assert por_id["wamid.3"]["estado"] == "aceptado"
    # Un fallido vuelve a «No enviado aún».
    _, previa = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "no_enviado", True)
    assert previa["recibiran"] == 1


# ════════════════════════════════════════════════════════════════════
#  R-CF.8 · Correo
# ════════════════════════════════════════════════════════════════════

def test_el_correo_lleva_el_enlace_y_respeta_el_canal(ctx, actor, conn_boveda, ola,
                                                      monkeypatch):
    from panel_api import verificacion_contacto as vc

    sin_correo = ola["ids"][0]
    db.ejecutar(conn_boveda, "update persona set email = null where id_persona = %s",
                (sin_correo,))
    preferencias.revocar(conn_boveda, ola["ids"][1], preferencias.EMAIL)
    conn_boveda.commit()
    enviados = []

    def enviador(canal, destino, contenido, tipo=None, **datos):
        enviados.append({"destino": destino, **datos})
        return {"proveedor": "prueba", "enviado": True}

    monkeypatch.setattr(vc, "proveedor_de_envio", lambda entorno=None, canal=None: enviador)
    _, mensaje = ruteo.despachar(
        "POST", f"/encuestas/{ola['encuesta']['id']}/mensajes",
        {"nombre": "Invitación por correo", "canal": "correo",
         "asunto": "{nombre}, te esperamos en {encuesta}",
         "cuerpo": "Hola {nombre}: tu opinión importa."}, {}, actor("analista"), ctx)
    _, previa = _envio(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos", True)
    assert previa["excluidos"] == {"sin_email": 1, "sin_preferencia_email": 1}
    _enviar(ctx, actor, ola["encuesta"]["id"], mensaje["id"], "todos")
    assert len(enviados) == 8
    uno = enviados[0]
    assert uno["asunto"].endswith("te esperamos en Ola de prueba")
    assert URL in uno["cuerpo"] and "{enlace}" not in uno["cuerpo"]
    assert db.una(conn_boveda, "select count(*)::int as n from envio "
                               "where canal = 'correo' and estado = 'aceptado'")["n"] == 8
