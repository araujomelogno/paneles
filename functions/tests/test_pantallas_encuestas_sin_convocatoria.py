"""PEDIDO encuestas sin convocatoria, en la pantalla: la app entera, en modo
demo, en Chromium.

Lo que el pedido pide ver: «—» y no 0 en una encuesta sin convocatoria, la
sección «Respondentes» sin columna de convocatoria en una encuesta que llegó
como archivo, «Convocatoria» intacta en una desde consulta, «no convocado» en
vez de una fecha, y las personas con identificadores inválidos listadas en
Panelistas.
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


def _fila(pagina, nombre):
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("[data-encuesta]")
    return pagina.locator("tr", has_text=nombre).first


def _abrir(pagina, nombre):
    _fila(pagina, nombre).locator("[data-encuesta]").click()
    pagina.wait_for_selector("#titulo-participacion")
    pagina.wait_for_selector("#participacion table, #participacion .empty-state")


def _texto(pagina, selector):
    """El texto con los espacios normalizados: el HTML corta líneas."""
    return " ".join(pagina.text_content(selector).split())


def test_el_listado_muestra_guion_y_el_origen_sin_convocatoria(pagina):
    fila = _fila(pagina, "Ómnibus agosto 2026")
    assert fila.locator("[data-convocados]").inner_text().strip() == "—"
    assert fila.locator("[data-origen='carga']").text_content().strip() == "desde una carga"
    # Una ola convocada sigue mostrando su número.
    ola = _fila(pagina, "Ola 1 — Consumo de bebidas")
    assert ola.locator("[data-convocados]").inner_text().strip().isdigit()


def test_una_encuesta_desde_una_carga_lista_respondentes_sin_convocatoria(pagina):
    _abrir(pagina, "Ómnibus agosto 2026")
    assert pagina.text_content("#titulo-participacion").strip() == "Respondentes"
    assert "llegó como archivo de respuestas" in _texto(pagina, "#texto-participacion")
    encabezados = pagina.locator("#participacion th").all_text_contents()
    assert "Convocado" not in encabezados and "Respondió / cargado" in encabezados
    assert pagina.locator("#participacion [data-respondio-en]").count() >= 1
    # No hay muestra que exportar ni panel que convocar.
    assert pagina.locator("#muestra").count() == 0
    assert pagina.locator("#convocar").count() == 0


def test_una_encuesta_desde_una_consulta_sigue_mostrando_convocatoria(pagina):
    # La demo no trae ninguna: se crea por la API de la página, como la
    # barra de acciones de una consulta.
    pagina.evaluate("""async () => {
        const api = await import('/js/api.js');
        const { items } = await api.panelistas.listar({ limite: 3 });
        await api.encuestas.desdeConsulta('Seguimiento desde consulta',
          { items: items.map((p) => ({ id_persona: p.id_persona })) }, null, {});
    }""")
    _abrir(pagina, "Seguimiento desde consulta")
    assert pagina.text_content("#titulo-participacion").strip() == "Convocatoria"
    assert "foto de la consulta" in _texto(pagina, "#texto-participacion")
    assert "Convocado" in pagina.locator("#participacion th").all_text_contents()


def test_un_no_convocado_aparece_como_no_convocado_y_sin_fecha(pagina):
    """En una ola con convocatoria, quien respondió por un enlace reenviado."""
    pagina.evaluate("""async () => {
        const api = await import('/js/api.js');
        const original = api.encuestas.participacion;
        api.encuestas.participacion = async (id) => {
          const salida = await original(id);
          salida.items[0] = { ...salida.items[0], convocado: false, convocado_en: null,
                              respondio: true };
          return salida;
        };
    }""")
    _abrir(pagina, "Ola 1 — Consumo de bebidas")
    assert pagina.text_content("#titulo-participacion").strip() == "Convocatoria"
    celdas = pagina.locator("#participacion [data-convocado]").all_text_contents()
    assert celdas[0].strip() == "no convocado"
    assert all("/" in c for c in celdas[1:])          # los demás, con su fecha
    assert "sin estar convocados" in _texto(pagina, "#participacion .p-info")


def test_panelistas_lista_los_identificadores_invalidos(pagina):
    pagina.evaluate("""async () => {
        const api = await import('/js/api.js');
        await api.panelistas.alta({ persona: { nombre: '......', documento: '9.999.999-9' },
          consentimientos: [{ finalidad: 'uso_semantico', version_texto: 'v1' }] });
    }""")
    pagina.click('[data-pagina="panelistas"]')
    pagina.wait_for_selector("#f-invalidos")
    pagina.check("#f-invalidos")
    sync_api.expect(pagina.locator("#tabla [data-invalidos]")).to_have_count(1)
    assert "nombre" in pagina.text_content("#tabla [data-invalidos]")
