"""SPEC_encuestas_y_cargas_unificadas, en la pantalla (D81): la app entera,
en modo demo, en Chromium.

Lo que se mira es lo que la spec pone en pantalla y no se ve desde el
servidor: el detalle de una encuesta como estudio (datos, cargas,
participación), la composición de convocados frente a respondentes con su
tasa por segmento, y archivar un panel. Que el servidor decida lo que
corresponde lo prueba `test_encuestas_y_cargas_unificadas.py`.
"""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_modal_de_ingesta_procesando import PUBLICO, navegador, servidor_web  # noqa: E402,F401


@pytest.fixture
def pagina(navegador, servidor_web):  # noqa: F811
    contexto = navegador.new_context()
    pagina = contexto.new_page()
    errores = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    # Sin configuración de Firebase la app entra sola al modo demo.
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
    fila = pagina.locator("tr", has_text=nombre).first
    fila.locator("[data-encuesta]").click()
    pagina.wait_for_selector("#editar-estudio")


def test_el_detalle_de_una_encuesta_es_el_estudio_con_sus_cargas(pagina):
    _abrir_encuesta(pagina, "Ola 1 — Consumo de bebidas")
    # `text_content` y no `inner_text`: los títulos van en mayúsculas por CSS.
    texto = pagina.text_content("main")
    assert "Datos del estudio" in texto
    assert "Cargas" in texto and "+ Ingestar respuestas" in texto
    assert "Temáticas del estudio" in texto
    # La participación separa a quien respondió sin estar convocado.
    assert "sin estar" in texto.lower()


def test_una_carga_de_panelistas_es_una_encuesta_independiente(pagina):
    _abrir_encuesta(pagina, "Ómnibus agosto 2026")
    texto = pagina.text_content("main")
    assert "independiente" in texto.lower()
    # Sin panel no hay a quién convocar.
    assert pagina.locator("#convocar").count() == 0


def test_convocados_vs_respondentes_muestra_la_tasa_por_segmento(pagina):
    _abrir_encuesta(pagina, "Ola 1 — Consumo de bebidas")
    pagina.click("#composicion")
    pagina.wait_for_selector("[data-dimension-encuesta]")
    assert pagina.input_value("#ambito") == "encuesta"
    tabla = pagina.inner_text("[data-dimension-encuesta='sexo']")
    assert "Tasa de respuesta" in tabla or "TASA DE RESPUESTA" in tabla
    assert pagina.locator("[data-dimension-encuesta='sexo'] [data-tasa]").count() >= 1
    assert "%" in pagina.inner_text("#tasa-global")


def test_archivar_un_panel_lo_oculta_y_se_reactiva(pagina):
    pagina.click('[data-pagina="paneles"]')
    fila = pagina.locator("tr", has_text="Panel Joven").first
    fila.locator("[data-panel]").click()
    pagina.click("#archivar")
    pagina.locator(".modal-foot .btn", has_text="Archivar").click()
    sync_api.expect(pagina.locator("main")).to_contain_text("archivado")
    sync_api.expect(pagina.locator("#archivar")).to_have_text("Reactivar")

    pagina.click('[data-pagina="paneles"]')
    pagina.wait_for_selector("#con-archivados")
    assert pagina.locator("tr", has_text="Panel Joven").count() == 0
    pagina.check("#con-archivados")
    sync_api.expect(pagina.locator("tr", has_text="Panel Joven")).to_have_count(1)

    pagina.locator("tr", has_text="Panel Joven").locator("[data-panel]").click()
    pagina.click("#archivar")
    sync_api.expect(pagina.locator("#archivar")).to_have_text("Archivar…")
