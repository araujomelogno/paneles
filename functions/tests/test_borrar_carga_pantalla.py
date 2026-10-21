"""Borrar una carga, en la pantalla (D78): la app entera, en modo demo, en
Chromium.

Lo que se mira es lo que el pedido exige de la confirmación: la vista previa
con los números, el texto de irreversible, y el botón que se habilita solo
con el nombre exacto de la carga. Que el servidor sea quien decide (permiso,
nombre, ingesta en curso) lo prueba `test_borrar_carga.py`.
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
    pagina.click('[data-pagina="cargas"]')
    pagina.locator("[data-carga]").first.click()
    pagina.wait_for_selector("#borrar-carga")
    yield pagina
    assert not errores, errores
    contexto.close()


def test_el_borrado_muestra_la_vista_previa_y_pide_el_nombre(pagina):
    pagina.click("#borrar-carga")
    pagina.wait_for_selector("#confirmar-nombre")

    cuerpo = pagina.inner_text("#borrado-cuerpo")
    assert "irreversible" in cuerpo.lower()
    assert "Se dan de baja" in cuerpo and "Se conservan" in cuerpo
    assert "respaldo" in cuerpo

    boton = pagina.locator("#confirmar-borrado")
    assert boton.is_disabled()
    etiqueta = pagina.text_content("label[for='confirmar-nombre'] strong")
    assert etiqueta in pagina.text_content(".modal-title")
    # Lo que se ve es lo que hay que escribir: la etiqueta no lo pasa a
    # mayúsculas (las etiquetas de formulario sí, por CSS).
    assert pagina.inner_text("label[for='confirmar-nombre'] strong") == etiqueta
    pagina.fill("#confirmar-nombre", etiqueta[:-1])
    assert boton.is_disabled()
    pagina.fill("#confirmar-nombre", etiqueta.lower() if etiqueta != etiqueta.lower()
                else etiqueta + "x")
    assert boton.is_disabled()
    pagina.fill("#confirmar-nombre", etiqueta)
    assert boton.is_enabled()


def test_un_error_del_servidor_se_muestra_y_devuelve_el_boton(pagina):
    """En la demo borrar responde que no: el motivo aparece junto al botón,
    que vuelve a habilitarse."""
    pagina.click("#borrar-carga")
    pagina.wait_for_selector("#confirmar-nombre")
    etiqueta = pagina.text_content("label[for='confirmar-nombre'] strong")
    pagina.fill("#confirmar-nombre", etiqueta)
    pagina.click("#confirmar-borrado")
    sync_api.expect(pagina.locator("#borrado-alerta")).to_contain_text(
        "no se borran cargas")
    assert pagina.locator("#confirmar-borrado").is_enabled()
    assert pagina.locator("#confirmar-nombre").is_enabled()
