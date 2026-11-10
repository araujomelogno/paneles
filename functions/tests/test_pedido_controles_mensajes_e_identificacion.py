"""PEDIDO — Controles de los mensajes y de la identificación al cargar (D91).

Cuatro huecos del mismo tipo: algo que se valida en un momento y después
puede dejar de ser cierto, o que falla sin decir por qué. Contra Postgres de
verdad y con el Meta de mentira de `test_convocatoria_y_mensajes`:

* R-CTL.1 — la URL del botón vigente se vuelve a comparar con la URL de
  Alchemer vigente en cada envío; si no coinciden, se bloquea y se muestran
  las dos. Cambiar la URL de la encuesta avisa qué mensajes quedan
  bloqueados.
* R-CTL.2 — una plantilla con encabezado variable o de archivo no se guarda
  como mensaje, ni se envía si cambió en Meta.
* R-CTL.3 — la fuente «texto fijo» completa un parámetro del cuerpo; el
  cliente no es una fuente.
* R-CTL.4 — una columna con UUID propone el tipo id_persona, y los avisos
  dicen con qué tipo se intentó identificar.
"""

import pytest

from panel_api import (
    cargas, db, encuestas, ingesta, mensajes, paneles, ruteo, sav,
)
from panel_api.errores import Conflicto, DatosInvalidos

from test_convocatoria_y_mensajes import (  # noqa: F401
    URL, _envio, _mensaje, _plantilla, _procesar, meta, ola,
)
from test_encuestas_y_cargas_unificadas import _panelista

OTRA_URL = "https://survey.alchemer.com/s3/1234567/estudio-v2?pid="


def _encabezado(plantilla, componente):
    plantilla["components"].insert(0, {"type": "HEADER", **componente})
    return plantilla


# ════════════════════════════════════════════════════════════════════
#  R-CTL.1 · La URL, otra vez en cada envío
# ════════════════════════════════════════════════════════════════════

def test_cambiar_la_url_de_la_encuesta_bloquea_el_envio_y_muestra_las_dos(
        ctx, actor, meta, ola):
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id)
    # Alguien corrige la URL de la encuesta después de guardar el mensaje.
    _, editada = ruteo.despachar("PATCH", f"/encuestas/{encuesta_id}",
                                 {"url_alchemer": OTRA_URL}, {}, actor("operaciones"), ctx)
    [aviso] = editada["avisos"]
    assert aviso["motivo"] == "mensajes_bloqueados_por_url"
    assert "«Invitación»" in aviso["mensaje"]
    assert aviso["mensajes"] == [{"nombre": "Invitación", "url_boton": URL,
                                  "url_alchemer": OTRA_URL}]

    _, previa = _envio(ctx, actor, encuesta_id, mensaje["id"], "todos", solo_revisar=True)
    assert previa["puede_enviar"] is False
    assert any("no lleva a la URL de Alchemer" in m for m in previa["motivos"])
    assert previa["urls"] == {"url_boton": URL, "url_alchemer": OTRA_URL,
                              "coinciden": False}
    with pytest.raises(Conflicto):
        _envio(ctx, actor, encuesta_id, mensaje["id"], "todos")
    assert not meta.enviados


def test_una_diferencia_de_barra_o_de_https_tambien_bloquea(ctx, actor, meta, ola):
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id)
    encuestas.editar(ctx.boveda, encuesta_id,
                     {"url_alchemer": URL.replace("https://", "http://")})
    ctx.boveda.commit()
    _, previa = _envio(ctx, actor, encuesta_id, mensaje["id"], "todos", solo_revisar=True)
    assert not previa["puede_enviar"]
    assert previa["urls"]["url_alchemer"].startswith("http://")


def test_si_la_plantilla_cambia_en_meta_el_envio_se_bloquea(ctx, actor, meta, ola):
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id)
    meta.plantillas[0] = _plantilla("invitacion_estudio", url=OTRA_URL + "{{1}}")
    _, previa = _envio(ctx, actor, encuesta_id, mensaje["id"], "todos", solo_revisar=True)
    assert not previa["puede_enviar"]
    assert previa["urls"] == {"url_boton": OTRA_URL, "url_alchemer": URL,
                              "coinciden": False}


def test_la_casilla_de_guardar_igual_no_alcanza_para_enviar(ctx, actor, meta, ola):
    """Guardar con una URL distinta es una decisión vista; enviar con ella,
    no: si difieren al enviar, algo cambió después (o nunca se arregló)."""
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id, nombre="Recordatorio",
                       plantilla="otra_encuesta", confirmar_url_distinta=True)
    _, previa = _envio(ctx, actor, encuesta_id, mensaje["id"], "todos", solo_revisar=True)
    assert not previa["puede_enviar"]
    assert previa["urls"]["coinciden"] is False


def test_un_lote_vuelve_a_comparar_y_no_manda_nada_si_cambio(ctx, actor, meta, ola):
    """Entre la vista previa y el lote la URL pudo cambiar."""
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id)
    status, salida = _envio(ctx, actor, encuesta_id, mensaje["id"], "todos")
    assert status == 202
    encuestas.editar(ctx.boveda, encuesta_id, {"url_alchemer": OTRA_URL})
    ctx.boveda.commit()
    resultados = _procesar(ctx)
    assert all(r["estado"] == "fallido" for r in resultados)
    assert not meta.enviados
    assert not db.todas(ctx.boveda, "select 1 from envio where encuesta_mensaje_id = %s",
                        (mensaje["id"],))


def test_con_las_url_iguales_se_envia_como_siempre(ctx, actor, meta, ola):
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id)
    _, previa = _envio(ctx, actor, encuesta_id, mensaje["id"], "todos", solo_revisar=True)
    assert previa["puede_enviar"] and previa["urls"]["coinciden"] is True


# ════════════════════════════════════════════════════════════════════
#  R-CTL.2 · El encabezado de la plantilla
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("componente,que", [
    ({"format": "TEXT", "text": "Hola {{1}}"}, "texto variable"),
    ({"format": "IMAGE"}, "imagen"),
    ({"format": "VIDEO"}, "video"),
    ({"format": "DOCUMENT"}, "documento"),
])
def test_un_encabezado_variable_o_de_archivo_no_se_guarda(ctx, actor, meta, ola,
                                                        componente, que):
    _encabezado(meta.plantillas[0], componente)
    with pytest.raises(DatosInvalidos) as error:
        _mensaje(ctx, actor, ola["encuesta"]["id"])
    assert error.value.detalle["motivo"] == "encabezado_no_soportado"
    assert f"encabezado con {que}" in error.value.mensaje
    assert "todavía no se puede completar desde el sistema" in error.value.mensaje
    # La lista la muestra igual, con el motivo.
    listada = next(p for p in mensajes.plantillas_para_mensajes()["plantillas"]
                   if p["nombre"] == "invitacion_estudio")
    assert listada["no_soportada"]


def test_un_encabezado_de_texto_fijo_se_puede_usar(ctx, actor, meta, ola):
    _encabezado(meta.plantillas[0], {"format": "TEXT", "text": "Equipos Consultores"})
    assert _mensaje(ctx, actor, ola["encuesta"]["id"])["id"]


def test_si_la_plantilla_suma_un_encabezado_en_meta_el_envio_se_bloquea(
        ctx, actor, meta, ola):
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id)
    _encabezado(meta.plantillas[0], {"format": "IMAGE"})
    _, previa = _envio(ctx, actor, encuesta_id, mensaje["id"], "todos", solo_revisar=True)
    assert not previa["puede_enviar"]
    assert any("encabezado con imagen" in m for m in previa["motivos"])


# ════════════════════════════════════════════════════════════════════
#  R-CTL.3 · Texto fijo
# ════════════════════════════════════════════════════════════════════

def test_el_texto_fijo_completa_un_parametro_del_cuerpo(ctx, actor, meta, ola):
    encuesta_id = ola["encuesta"]["id"]
    mensaje = _mensaje(ctx, actor, encuesta_id, parametros=[
        "nombre_panelista", {"fuente": "texto_fijo", "valor": "hasta el viernes 14"}])
    assert mensaje["parametros"][1] == {"fuente": "texto_fijo",
                                        "valor": "hasta el viernes 14"}
    ruteo.despachar("POST", f"/encuestas/{encuesta_id}/mensajes/{mensaje['id']}/envio",
                    {"publico": "todos"}, {}, actor("operaciones"), ctx)
    _procesar(ctx)
    textos = {tuple(p["text"] for p in e["template"]["components"][0]["parameters"])
              for e in meta.enviados}
    assert all(t[1] == "hasta el viernes 14" for t in textos)
    assert len({t[0] for t in textos}) > 1, "el nombre sigue siendo de cada uno"


@pytest.mark.parametrize("valor", ["", "  ", "con\nsalto", "cinco     espacios", "x" * 201])
def test_un_texto_fijo_vacio_o_que_meta_rechaza_no_se_guarda(ctx, actor, meta, ola, valor):
    with pytest.raises(DatosInvalidos):
        _mensaje(ctx, actor, ola["encuesta"]["id"], parametros=[
            "nombre_panelista", {"fuente": "texto_fijo", "valor": valor}])


def test_el_cliente_no_es_una_fuente(ctx, actor, meta, ola):
    assert "texto_fijo" in mensajes.FUENTES_DE_PARAMETRO
    assert not any("client" in f for f in mensajes.FUENTES_DE_PARAMETRO)
    with pytest.raises(DatosInvalidos):
        _mensaje(ctx, actor, ola["encuesta"]["id"],
                 parametros=["nombre_panelista", "nombre_cliente"])


# ════════════════════════════════════════════════════════════════════
#  R-CTL.4 · El tipo de identificador al cargar
# ════════════════════════════════════════════════════════════════════

UUIDS = ["3f2b8c1e-9a4d-4c7e-8b21-0d6f5a9e1c33", "7a1d2e3f-4b5c-4d6e-8f90-a1b2c3d4e5f6"]


def test_una_columna_de_uuid_propone_el_tipo_id_persona():
    assert ingesta.tipo_sugerido(UUIDS + ["", None]) == "id_persona"
    assert ingesta.tipo_sugerido(UUIDS + ["R-001"]) is None
    assert ingesta.tipo_sugerido([]) is None
    assert sav._tipo_sugerido({UUIDS[0]: 3, UUIDS[1]: 1}) == "id_persona"


def test_la_revision_propone_el_tipo_y_avisa_si_se_eligio_otro(ctx, actor):
    personas = [_panelista(ctx.boveda, f"40-{i}", f"P {i}") for i in range(2)]
    carga = cargas.crear(ctx.boveda, "Alchemer", tipo_datos_origen="encuesta",
                         modo_relevamiento="web")
    ctx.boveda.commit()
    filas = [{"ID": i, "P1": "1"} for i in personas]
    cuerpo = {"filas": filas, "columna_id": "ID", "solo_revisar": True,
              "proponer_tematicas": False,
              "preguntas": [{"codigo": "P1", "texto": "¿Qué bebida?", "tipo": "abierta"}]}
    _, mal = ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta",
                             {**cuerpo, "tipo_identificador": "alias"}, {},
                             actor("analista"), ctx)
    assert mal["identidad"]["tipo_sugerido"] == "id_persona"
    assert mal["identidad"]["tipo_etiqueta"] == "id de la plataforma de campo"
    assert "tienen forma de id_persona" in mal["identidad"]["aviso_tipo"]
    _, bien = ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta",
                              {**cuerpo, "tipo_identificador": "id_persona"}, {},
                              actor("analista"), ctx)
    assert bien["identidad"]["tipo_etiqueta"] == "id_persona del sistema"
    assert "aviso_tipo" not in bien["identidad"]


def test_el_aviso_de_no_convocados_dice_con_que_tipo_se_intento(ctx):
    panel = paneles.crear(ctx.boveda, "Panel general")
    ids = [_panelista(ctx.boveda, f"20-{i}", f"Panelista {i}") for i in range(1, 4)]
    for i in ids:
        paneles.agregar_miembro(ctx.boveda, panel["id"], i)
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola 1")
    encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=ids[:2])
    ctx.boveda.commit()
    # El archivo trae el id_persona pero se eligió «id de la plataforma».
    filas = [{"ID": i} for i in ids] + [{"ID": "R-999"}]
    salida = cargas.no_convocados(ctx.boveda, encuesta["id"], filas, "ID", "alias",
                                  "alchemer")
    assert salida["tipo_etiqueta"] == "id de la plataforma de campo"
    assert f"{salida['sin_identificar']} no se pudieron identificar como id de la " \
           f"plataforma de campo" in salida["mensaje"]
    bien = cargas.no_convocados(ctx.boveda, encuesta["id"], filas[:3], "ID",
                                "id_persona", "alchemer")
    assert bien["cuantos"] == 1 and bien["sin_identificar"] == 0
