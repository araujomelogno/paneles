"""PEDIDO de búsqueda y enlaces de Alchemer (D99), en la pantalla: la app
entera, en modo demo, en Chromium.

* Pegar la URL de la encuesta la resuelve sola (R-ALB.1).
* Buscar por título es contra el catálogo, con su estado y «Actualizar», que
  muestra el avance de la sincronización (R-ALB.2).
* Un corte de tiempo muestra el error con «Reintentar» y «Pegar la URL», nunca
  un indicador girando (R-ALB.3).
* El selector de enlace se ve siempre, aunque haya uno solo, con nombre,
  estado y URL; los cerrados, con un filtro; y el enlace se cambia después
  desde la ficha, con el aviso por las plantillas de WhatsApp (R-ALB.5).
"""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_modal_de_ingesta_procesando import PUBLICO, navegador, servidor_web  # noqa: E402,F401

URL_AVANTI = ("https://survey.alchemer.com/s3/9105516/"
              "AVANTI-2026-S00726-Evaluaci-n-de-lanzamientos")


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


def _nueva_encuesta(pagina):
    pagina.click('[data-pagina="encuestas"]')
    pagina.click("#nueva")
    caja = pagina.locator(".modal-box")
    caja.wait_for()
    pagina.select_option("#tipo-nueva", "independiente")
    return caja


def _buscar(caja, texto):
    caja.locator("[data-alchemer-buscar]").fill(texto)
    caja.locator("[data-alchemer-ir]").click()


def test_pegar_la_url_resuelve_la_encuesta_y_lista_sus_enlaces(pagina):
    caja = _nueva_encuesta(pagina)
    _buscar(caja, URL_AVANTI)
    sync_api.expect(caja.locator("[data-alchemer-ok]")).to_contain_text("AVANTI 2026")
    sync_api.expect(caja.locator('[name="nombre"]')).to_have_value(
        "AVANTI 2026 - S00726 - Evaluación de lanzamientos")
    # Los siete activos, con nombre, estado y URL; el primero, elegido.
    enlace = caja.locator("[data-alchemer-enlace]")
    sync_api.expect(enlace).to_be_visible()
    sync_api.expect(enlace.locator("option")).to_have_count(7)
    assert "Default Link · Active · https://survey.alchemer.com/s3/9105516/" in \
        enlace.locator("option").first.text_content()
    sync_api.expect(caja.locator('[name="url_alchemer"]')).to_have_value(URL_AVANTI + "?pid=")
    # Otro enlace cambia la URL (con & si ya traía consulta).
    enlace.select_option("26582603")
    sync_api.expect(caja.locator('[name="url_alchemer"]')).to_have_value(
        "https://survey.alchemer.com/s3/9105516/avanti-redes?origen=ig&pid=")
    # Los cerrados, con el filtro.
    caja.locator("[data-alchemer-cerrados]").check()
    sync_api.expect(enlace.locator("option")).to_have_count(8)
    # Se guarda con su enlace.
    caja.locator('[name="fecha_campo"]').fill("2026-11-26")
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#enlace-alchemer")).to_contain_text("26582603")


def test_un_solo_enlace_se_ve_igual(pagina):
    caja = _nueva_encuesta(pagina)
    _buscar(caja, "consumo de bebidas")
    caja.locator("[data-alchemer-encuesta]").select_option("7001234")
    enlace = caja.locator("[data-alchemer-enlace]")
    sync_api.expect(enlace).to_be_visible()
    sync_api.expect(enlace.locator("option")).to_have_count(1)
    sync_api.expect(enlace.locator("option")).to_contain_text("Enlace principal · Active")
    sync_api.expect(caja.locator("[data-alchemer-url]")).to_have_text(
        "https://survey.alchemer.com/s3/7001234/ola-4?pid=")


def test_la_busqueda_es_del_catalogo_y_actualizar_muestra_el_avance(pagina):
    caja = _nueva_encuesta(pagina)
    _buscar(caja, "tracking mensual")
    opciones = caja.locator("[data-alchemer-encuesta] option")
    sync_api.expect(opciones).to_have_count(27)          # 26 activas + «elegí una»
    assert "Tracking mensual 39" in opciones.nth(1).text_content()   # la activa más reciente
    catalogo = caja.locator("[data-alchemer-catalogo]")
    sync_api.expect(catalogo).to_contain_text("43 encuestas")
    sync_api.expect(catalogo).to_contain_text("actualizado hace 2 h")
    caja.locator("[data-alchemer-todas]").check()
    sync_api.expect(opciones).to_have_count(41)
    # «Actualizar»: corre en segundo plano y se ve el avance hasta terminar.
    caja.locator("[data-alchemer-actualizar]").click()
    sync_api.expect(catalogo.locator("[data-alchemer-progreso]")).to_contain_text("de 22 página(s)")
    sync_api.expect(catalogo).to_contain_text("actualizado recién", timeout=15000)


def test_un_corte_de_tiempo_ofrece_reintentar_o_pegar_la_url(pagina):
    caja = _nueva_encuesta(pagina)
    pagina.evaluate("""async () => {
        (await import('/js/api.js')).alchemer.tiempoMs = 400;
        (await import('/js/demo.js')).ajustesDemo.alchemerLento = true;
    }""")
    _buscar(caja, "9105516")
    error = caja.locator("[data-alchemer-error]")
    sync_api.expect(error).to_contain_text("Alchemer tardó demasiado en responder.")
    sync_api.expect(caja.locator("[data-alchemer-consultando]")).to_have_count(0)
    sync_api.expect(error.locator("[data-alchemer-pegar]")).to_be_visible()
    pagina.evaluate("""async () => {
        (await import('/js/demo.js')).ajustesDemo.alchemerLento = false; }""")
    error.locator("[data-alchemer-reintentar]").click()
    sync_api.expect(caja.locator("[data-alchemer-ok]")).to_contain_text("AVANTI 2026")


def test_cambiar_de_enlace_desde_la_ficha_avisa_por_las_plantillas(pagina):
    # La Ola 1 tiene un mensaje de WhatsApp con botón a su URL: se la vincula
    # a la 9105516 por la API de la página.
    pagina.evaluate("""async () => {
        const api = await import('/js/api.js');
        const { items } = await api.encuestas.listar({});
        const ola = items.find((e) => e.nombre === 'Ola 1 — Consumo de bebidas');
        await api.encuestas.editar(ola.id, { alchemer_id: '9105516' });
    }""")
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("[data-encuesta]")
    pagina.locator("tr", has_text="Ola 1 — Consumo de bebidas").first \
        .locator("[data-encuesta]").click()
    pagina.click("#cambiar-enlace-alchemer")
    caja = pagina.locator(".modal-box")
    select = caja.locator("#enlace-nuevo")
    sync_api.expect(select.locator("option")).to_have_count(7)
    select.select_option("26582602")
    revision = caja.locator("#enlace-revision")
    sync_api.expect(revision.locator("[data-enlace-nueva]")).to_contain_text(
        "https://encuestas.equipos.com.uy/s3/9105516/avanti-wa?pid=")
    sync_api.expect(revision).to_contain_text("«Invitación»")
    # Sin la casilla no guarda.
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(caja.locator("#enlace-alerta")).to_contain_text("Confirmá")
    caja.locator("#enlace-confirmo").check()
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#enlace-alchemer")).to_contain_text("avanti-wa?pid=")
