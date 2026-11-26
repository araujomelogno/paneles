"""SPEC_alchemer_participacion_y_bandeja_whatsapp (D96 a D98), en la pantalla:
la app entera, en modo demo, en Chromium.

* B — el criterio «Historia de participación» en Consultas, y la fecha de
  campo obligatoria al crear una encuesta.
* A — elegir la encuesta de Alchemer en «Nueva encuesta»: se completan el
  nombre y la URL, se elige el enlace si hay más de uno y se avisa si no
  guarda el parámetro; la ficha muestra el cuestionario.
* C — la sección «WhatsApp» (respuestas con una columna por campo,
  comentarios, exportación y reprocesar) y «Obtener respuestas de WhatsApp»
  en la ficha de la encuesta, que abre el modal de ingesta con el archivo ya
  cargado.
"""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_modal_de_ingesta_procesando import PUBLICO, navegador, servidor_web  # noqa: E402,F401

OLA_3 = "Ola 3 — Movilidad urbana"


@pytest.fixture
def pagina(navegador, servidor_web):  # noqa: F811
    contexto = navegador.new_context(accept_downloads=True)
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


def _abrir_encuesta(pagina, nombre):
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("[data-encuesta]")
    pagina.locator("tr", has_text=nombre).first.locator("[data-encuesta]").click()
    pagina.wait_for_selector("#participacion table, #participacion .empty-state")


# ── B · Historia de participación ─────────────────────────────────────

def test_el_criterio_de_participacion_se_agrega_y_corre(pagina):
    pagina.click('[data-pagina="consultas"]')
    pagina.wait_for_selector("#agregar-participacion")
    pagina.click("#agregar-participacion")
    caja = pagina.locator(".modal-box")
    sync_api.expect(caja.locator("[data-ayuda-relacion]")).to_contain_text("carga")
    caja.locator('[name="meses"]').fill("6")
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#criterios")).to_contain_text(
        "No convocado en los últimos 6 meses")
    pagina.click("#correr")
    pagina.wait_for_selector("#tabla-demografica")

    # «Nunca respondió»: la ventana «alguna vez» no pide meses.
    pagina.click("#agregar-participacion")
    pagina.select_option('.modal-box [name="relacion"]', "respondio")
    pagina.select_option('.modal-box [name="ventana"]', "siempre")
    sync_api.expect(pagina.locator('.modal-box [data-ventana="meses"]')).to_be_hidden()
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#criterios")).to_contain_text("Nunca respondió")


def test_nueva_encuesta_exige_la_fecha_de_campo(pagina):
    pagina.click('[data-pagina="encuestas"]')
    pagina.click("#nueva")
    caja = pagina.locator(".modal-box")
    caja.wait_for()
    sync_api.expect(caja.locator('[data-campo-estudio="fecha_campo"] .obligatorio')).to_be_visible()
    pagina.select_option("#tipo-nueva", "independiente")
    caja.locator('[name="nombre"]').fill("Sin fecha")
    caja.locator('[name="fecha_campo"]').fill("")
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#enc-alerta")).to_contain_text("fecha de campo")


# ── A · Alchemer ──────────────────────────────────────────────────────

def test_elegir_la_encuesta_de_alchemer_completa_nombre_y_url(pagina):
    pagina.click('[data-pagina="encuestas"]')
    pagina.click("#nueva")
    caja = pagina.locator(".modal-box")
    caja.wait_for()
    pagina.select_option("#tipo-nueva", "independiente")
    caja.locator("[data-alchemer-buscar]").fill("bebidas")
    caja.locator("[data-alchemer-ir]").click()
    selector = caja.locator("[data-alchemer-encuesta]")
    sync_api.expect(selector).to_be_visible()
    selector.select_option("7001234")
    sync_api.expect(caja.locator("[data-alchemer-ok]")).to_contain_text("pid")
    sync_api.expect(caja.locator('[name="nombre"]')).to_have_value("Ola 4 — Consumo de bebidas")
    sync_api.expect(caja.locator('[name="url_alchemer"]')).to_have_value(
        "https://survey.alchemer.com/s3/7001234/ola-4?pid=")
    # Un solo enlace: el selector se ve igual (D99 · R-ALB.5), con ese enlace.
    sync_api.expect(caja.locator("[data-alchemer-enlace] option")).to_have_count(1)

    # La otra no guarda el parámetro y tiene dos enlaces.
    caja.locator("[data-alchemer-buscar]").fill("")
    caja.locator("[data-alchemer-ir]").click()
    caja.locator("[data-alchemer-encuesta]").select_option("7005678")
    sync_api.expect(caja.locator("[data-alchemer-aviso]")).to_contain_text(
        "no guarda el parámetro")
    enlace = caja.locator("[data-alchemer-enlace]")
    sync_api.expect(enlace).to_be_visible()
    enlace.select_option("2")
    sync_api.expect(caja.locator('[name="url_alchemer"]')).to_have_value(
        "https://survey.alchemer.com/s3/7005678/telefonia-wa?pid=")

    # Se guarda igual, y la ficha muestra el cuestionario con el aviso.
    caja.locator('[name="fecha_campo"]').fill("2026-11-20")
    pagina.click(".modal-foot .btn-orange")
    tarjeta = pagina.locator("#tarjeta-alchemer")
    tarjeta.wait_for()
    sync_api.expect(tarjeta).to_contain_text("no guarda el parámetro")
    sync_api.expect(tarjeta).to_contain_text("1 pregunta(s)")
    sync_api.expect(pagina.locator("#proponer-desde-alchemer")).to_be_visible()


# ── C · La bandeja de WhatsApp ────────────────────────────────────────

def test_la_bandeja_muestra_respuestas_por_campo_comentarios_y_exporta(pagina):
    pagina.click('[data-pagina="whatsapp"]')
    # Los grupos van del más reciente al más viejo; se elige la Ola 3.
    pagina.wait_for_selector("#wa-grupo option[value^='encuesta:']", state="attached")
    valor = pagina.locator("#wa-grupo option", has_text=OLA_3).get_attribute("value")
    pagina.select_option("#wa-grupo", valor)
    tabla = pagina.locator("#wa-respuestas")
    tabla.wait_for()
    encabezados = [t.strip() for t in tabla.locator("thead th").all_text_contents()]
    assert encabezados[:4] == ["Fecha", "Teléfono", "Encuesta o plantilla", "Token"]
    assert encabezados[4:] == ["screen_0_Radio_1", "screen_1_Checkbox_2",
                               "screen_1_TextArea_3", "screen_1_Text_4"]
    assert tabla.locator("tbody tr").count() == 3

    # La encuesta de afuera, agrupada por el nombre del token.
    pagina.select_option("#wa-grupo", "grupo:SONDEO_CLIENTE")
    sync_api.expect(pagina.locator("#wa-respuestas tbody tr")).to_have_count(2)
    sync_api.expect(pagina.locator("#wa-respuestas thead")).to_contain_text("pregunta_1")

    pagina.click('#wa-solapas [data-solapa="comentario"]')
    pagina.select_option("#wa-grupo", "grupo:GENERAL")
    sync_api.expect(pagina.locator("#wa-comentarios")).to_contain_text("¿esto de qué es?")

    with pagina.expect_download() as descarga:
        pagina.click("#wa-exportar")
    assert descarga.value.suggested_filename.endswith(".csv")

    # Los avisos que fallaron se reprocesan.
    pagina.click("#wa-reprocesar")
    sync_api.expect(pagina.locator("#wa-fallidos")).to_have_count(0)


def test_obtener_respuestas_de_whatsapp_abre_la_ingesta_con_el_archivo(pagina):
    _abrir_encuesta(pagina, OLA_3)
    boton = pagina.locator("#obtener-whatsapp")
    sync_api.expect(boton).to_have_text("Obtener respuestas de WhatsApp (3 nuevas)")
    boton.click()
    sync_api.expect(pagina.locator("#wa-resumen [data-nuevas]")).to_have_text("3")
    pagina.click(".modal-foot .btn-orange")
    # El modal de ingesta, con el archivo ya analizado y el aviso fijo.
    pagina.wait_for_selector("#ing-aviso-inicial")
    sync_api.expect(pagina.locator("#ing-aviso-inicial")).to_contain_text("sin etiqueta")
    sync_api.expect(pagina.locator("#columna-id")).to_have_value("id_persona")
    sync_api.expect(pagina.locator("#resumen-archivo")).to_contain_text("whatsapp_encuesta_")
    codigos = pagina.locator('#preguntas .p-codigo').evaluate_all(
        "(xs) => xs.map((x) => x.value)")
    assert "screen_0_Radio_1" in codigos
    assert "screen_1_Checkbox_2_0_Demoras" in codigos
