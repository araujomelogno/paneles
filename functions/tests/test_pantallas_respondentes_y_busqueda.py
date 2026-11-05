"""La lista de respondentes y la búsqueda por id de origen, en la pantalla:
la app entera, en modo demo, en Chromium.

* En la lista de una encuesta, cada fila dice si la persona es panelista o
  independiente y si fue convocada, y «Ver ficha» abre su ficha.
* En Panelistas, buscar por el id de una plataforma de campo encuentra a la
  persona y la fila muestra el id que coincidió.
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
    html = (PUBLICO / "index.html").read_text(encoding="utf-8").replace(
        "window.firebaseConfig = {", "window.firebaseConfigDeOtroLado = {")
    pagina.route(f"{servidor_web}/index.html", lambda ruta: ruta.fulfill(
        content_type="text/html", body=html))
    pagina.goto(f"{servidor_web}/index.html")
    pagina.wait_for_selector('[data-pagina="encuestas"]')
    yield pagina
    assert not errores, errores
    contexto.close()


def _abrir(pagina, nombre):
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("[data-encuesta]")
    pagina.locator("tr", has_text=nombre).first.locator("[data-encuesta]").click()
    pagina.wait_for_selector("#participacion table")


def _columna(pagina, selector):
    return [c.strip() for c in pagina.locator(f"#participacion {selector}").all_text_contents()]


def test_los_respondentes_de_una_carga_son_independientes_y_no_convocados(pagina):
    """El Ómnibus de la demo: tres personas que entraron solo por el
    archivo, sin panel, y nadie convocado."""
    _abrir(pagina, "Ómnibus agosto 2026")
    encabezados = pagina.locator("#participacion th").all_text_contents()
    assert "Panel" in encabezados and "Convocatoria" in encabezados
    assert _columna(pagina, "[data-panel-de-persona]") == ["independiente"] * 3
    assert _columna(pagina, "[data-convocado]") == ["no convocado"] * 3
    resumen = " ".join(pagina.text_content("#participacion .p-info").split())
    assert "3 respondentes" in resumen and "0 panelistas" in resumen \
        and "3 independientes" in resumen


def test_los_de_una_ola_de_panel_son_panelistas(pagina):
    _abrir(pagina, "Ola 1 — Consumo de bebidas")
    panel = _columna(pagina, "[data-panel-de-persona]")
    assert panel and set(panel) == {"panelista"}


def test_ver_ficha_abre_la_ficha_del_respondente(pagina):
    _abrir(pagina, "Ómnibus agosto 2026")
    fila = pagina.locator("#participacion tbody tr", has_text="Karina Bentancur")
    fila.locator("[data-ficha]").click()
    sync_api.expect(pagina.locator("main h1, main .page-title, main")).to_contain_text(
        "Ficha de la bóveda")
    assert "Karina Bentancur" in pagina.text_content("main")


def test_en_una_ola_con_convocatoria_tambien_se_abre_la_ficha(pagina):
    _abrir(pagina, "Ola 1 — Consumo de bebidas")
    assert "Panel" in pagina.locator("#participacion th").all_text_contents()
    pagina.locator("#participacion [data-ficha]").first.click()
    sync_api.expect(pagina.locator("main")).to_contain_text("Ficha de la bóveda")


def test_panelistas_busca_por_id_de_origen(pagina):
    pagina.click('[data-pagina="panelistas"]')
    pagina.wait_for_selector("#q")
    pagina.fill("#q", "R-001")
    fila = pagina.locator("#tabla tbody tr")
    sync_api.expect(fila).to_have_count(1)
    assert "Ana Pérez Bentancor" in fila.text_content()
    assert fila.locator("[data-ids-en-origen]").text_content().strip() == "dooblo: R-001"
