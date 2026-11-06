"""D85 — la convocatoria desde la encuesta, en la pantalla: la app entera, en
modo demo, en Chromium.

* El estado se cambia desde el listado y desde el detalle, en cualquier
  dirección, y el historial dice quién lo cambió (R-CF.1).
* La URL de Alchemer se ve en el detalle (R-CF.3).
* «Agregar panelistas» informa línea por línea, y una encuesta cerrada no lo
  ofrece (R-CF.2).
* Un mensaje cuyo botón lleva a otra encuesta se avisa antes de guardar, y
  guardar igual es una casilla dentro del modal (R-CF.4).
* El envío muestra la vista previa con los excluidos y el reenvío, y termina
  con su resumen (R-CF.5).
* Cada panelista muestra los mensajes que recibió y cómo le fue (R-CF.6/7).
"""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_modal_de_ingesta_procesando import PUBLICO, navegador, servidor_web  # noqa: E402,F401

OLA_1 = "Ola 1 — Consumo de bebidas"
OLA_2 = "Ola 2 — Hábitos digitales"


@pytest.fixture
def pagina(navegador, servidor_web):  # noqa: F811
    contexto = navegador.new_context()
    pagina = contexto.new_page()
    errores = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    html = (PUBLICO / "index.html").read_text(encoding="utf-8").replace(
        "window.firebaseConfig = {", "window.firebaseConfigDeOtroLado = {")
    pagina.route(f"{servidor_web}/index.html", lambda ruta: ruta.fulfill(
        content_type="text/html", body=html))
    pagina.goto(f"{servidor_web}/index.html")
    pagina.wait_for_selector('[data-pagina="encuestas"]')
    yield pagina
    assert not errores, errores
    contexto.close()


def _listado(pagina):
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("[data-encuesta]")


def _abrir(pagina, nombre):
    _listado(pagina)
    pagina.locator("tr", has_text=nombre).first.locator("[data-encuesta]").click()
    pagina.wait_for_selector("#participacion")
    pagina.wait_for_selector("#mensajes table, #mensajes p")


def _poner_en_campo(pagina):
    pagina.select_option("#estado-encuesta", "en_campo")
    sync_api.expect(pagina.locator("#efecto-estado")).to_have_text(
        "Se convoca, se agregan panelistas y se envían mensajes.")
    pagina.wait_for_selector("#mensajes table")


def _cambiar_en_el_listado(pagina, nombre, estado):
    """Cambia el estado desde la fila y espera a que el listado se vuelva a
    pintar: el selector viejo ya tiene el valor elegido, así que mirarlo no
    dice nada."""
    pagina.eval_on_selector_all("[data-estado-encuesta]",
                                "els => els.forEach((e) => { e.dataset.viejo = '1'; })")
    pagina.locator("tr", has_text=nombre).first.locator(
        "[data-estado-encuesta]").select_option(estado)
    pagina.wait_for_selector("[data-estado-encuesta]:not([data-viejo])")
    sync_api.expect(pagina.locator("tr", has_text=nombre).first
                    .locator("[data-estado-encuesta]")).to_have_value(estado)


def test_el_estado_se_cambia_desde_el_listado_y_queda_en_el_historial(pagina):
    _listado(pagina)
    fila = pagina.locator("tr", has_text=OLA_2).first
    assert fila.locator("[data-estado-encuesta]").input_value() == "borrador"
    _cambiar_en_el_listado(pagina, OLA_2, "cerrada")
    # Y de vuelta: en cualquier dirección.
    _cambiar_en_el_listado(pagina, OLA_2, "en_campo")
    pagina.locator("tr", has_text=OLA_2).first.locator("[data-encuesta]").click()
    pagina.click("#historial-estado")
    filas = pagina.locator("#tabla-historial-estado tbody tr")
    sync_api.expect(filas).to_have_count(2)
    textos = [" ".join(t.split()) for t in filas.all_text_contents()]
    assert "Cerrada En campo" in textos[0] and "demo@equipos.com.uy" in textos[0]
    assert "Borrador Cerrada" in textos[1]


def test_el_detalle_muestra_la_url_los_mensajes_y_como_le_fue_a_cada_uno(pagina):
    _abrir(pagina, OLA_1)
    assert "survey.alchemer.com" in pagina.text_content("[data-url-alchemer]")
    fila = pagina.locator("#tabla-mensajes tbody tr", has_text="Invitación")
    celdas = [c.strip() for c in fila.locator("td.num").all_text_contents()]
    assert celdas == ["5", "1"]      # lo recibieron · fallidos
    assert "Mensajes" in pagina.locator("#participacion th").all_text_contents()
    estados = pagina.locator("#participacion [data-estado-envio]")
    vistos = {e.get_attribute("data-estado-envio") for e in estados.all()}
    assert {"leido", "entregado", "fallido", "aceptado"} <= vistos
    # Cerrada: no se agregan panelistas, y el envío no se ofrece.
    assert pagina.locator("#agregar-panelistas").count() == 0
    assert pagina.locator("[data-enviar-mensaje]").first.is_disabled()


def test_agregar_panelistas_informa_linea_por_linea(pagina):
    _abrir(pagina, OLA_2)
    pagina.click("#agregar-panelistas")
    pagina.fill('textarea[name="lineas"]',
                "ana.perez@correo.uy\nnoexiste@ej.uy\n.....")
    pagina.click(".modal-foot .btn-orange")
    pagina.wait_for_selector("#tabla-agregados")
    resultados = {
        f.locator("td").first.text_content().strip():
        f.locator("[data-resultado]").get_attribute("data-resultado")
        for f in pagina.locator("#tabla-agregados tbody tr").all()}
    assert resultados == {"ana.perez@correo.uy": "agregado",
                          "noexiste@ej.uy": "no_corresponde",
                          ".....": "formato_invalido"}
    # Lo agregado aparece en la convocatoria al cerrar.
    pagina.click(".modal-foot .btn-outline")
    sync_api.expect(pagina.locator("#participacion")).to_contain_text("Ana Pérez Bentancor")


def test_un_boton_que_lleva_a_otra_encuesta_se_confirma_con_una_casilla(pagina):
    _abrir(pagina, OLA_1)
    pagina.click("#nuevo-mensaje")
    pagina.fill('.modal-box input[name="nombre"]', "Recordatorio")
    pagina.select_option("#mensaje-plantilla", label="recordatorio_otra · es · botón de URL")
    sync_api.expect(pagina.locator("[data-boton-url]")).to_contain_text("7009999")
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#mensaje-alerta")).to_contain_text("otra encuesta")
    # Sigue en el modal: la confirmación es una casilla, no otro modal.
    sync_api.expect(pagina.locator("#confirmar-url")).to_be_visible()
    pagina.check('[name="confirmar_url_distinta"]')
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#tabla-mensajes tbody tr")).to_have_count(2)


def test_enviar_muestra_la_previa_y_termina_con_su_resumen(pagina):
    _abrir(pagina, OLA_1)
    _poner_en_campo(pagina)
    pagina.wait_for_selector("[data-enviar-mensaje]:not([disabled])")
    pagina.locator("#tabla-mensajes tbody tr", has_text="Invitación").locator(
        "[data-enviar-mensaje]").click()
    pagina.wait_for_selector("[data-previa]")
    pagina.select_option("#envio-publico", "todos")
    sync_api.expect(pagina.locator("#envio-previa")).to_contain_text("lo van a recibir de nuevo")
    recibiran = int(pagina.text_content("[data-recibiran]"))
    assert recibiran > 0
    assert pagina.locator("[data-excluidos] [data-motivo]").count() >= 1
    boton = pagina.locator(".modal-foot .btn-orange")
    sync_api.expect(boton).to_have_text(f"Enviar a {recibiran}")
    boton.click()
    sync_api.expect(pagina.locator(".modal-title")).to_have_text("Envío terminado")
    assert pagina.locator(".resultado .r-num").first.text_content().strip() == str(recibiran)
