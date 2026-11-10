"""PEDIDO de controles (D91), en la pantalla, en Chromium.

* R-CTL.1 — cambiar la URL de la encuesta avisa, y la vista previa del envío
  muestra las dos URL completas, una debajo de la otra, y no deja enviar.
* R-CTL.2 — una plantilla con encabezado de imagen se ve con su motivo.
* R-CTL.3 — un parámetro del cuerpo con «texto fijo» se guarda con su valor.
* R-CTL.4 — una columna con UUID propone el tipo id_persona; cambiar el tipo
  en la revisión la deja desactualizada y no se puede confirmar.
"""

import json

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_modal_de_ingesta_procesando import (  # noqa: E402,F401
    ARNES, PUBLICO, carga, navegador, resumen, servidor_web,
)

OLA_1 = "Ola 1 — Consumo de bebidas"
OTRA = "https://survey.alchemer.com/s3/7001234/ola-1-v2?pid="


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
    pagina.wait_for_selector("#mensajes table, #mensajes p")


def test_la_url_cambiada_bloquea_el_envio_con_las_dos_url(pagina):
    _abrir(pagina, OLA_1)
    pagina.select_option("#estado-encuesta", "en_campo")
    pagina.wait_for_selector("#editar-estudio")
    pagina.click("#editar-estudio")
    pagina.fill('.modal-box [name="url_alchemer"]', OTRA)
    pagina.click(".modal-foot .btn-orange")
    sync_api.expect(pagina.locator("#toast-wrap")).to_contain_text("van a quedar bloqueados")
    pagina.wait_for_selector("[data-enviar-mensaje]:not([disabled])")
    pagina.locator("#tabla-mensajes tbody tr", has_text="Invitación").locator(
        "[data-enviar-mensaje]").click()
    pagina.wait_for_selector("[data-previa]")
    sync_api.expect(pagina.locator("[data-motivos]")).to_contain_text(
        "no lleva a la URL de Alchemer")
    sync_api.expect(pagina.locator("[data-url-encuesta]")).to_have_text(OTRA)
    assert "ola-1?pid=" in pagina.text_content("[data-url-boton]")
    assert pagina.locator(".modal-foot .btn-orange").is_disabled()


def test_una_plantilla_con_encabezado_de_imagen_dice_por_que_no_se_usa(pagina):
    _abrir(pagina, OLA_1)
    pagina.click("#nuevo-mensaje")
    pagina.wait_for_selector("#mensaje-plantilla")
    pagina.select_option("#mensaje-plantilla",
                         label="promo_con_imagen · es · Solo texto · encabezado no soportado")
    sync_api.expect(pagina.locator("[data-encabezado-no-soportado]")).to_contain_text(
        "encabezado con imagen")


def test_el_texto_fijo_se_guarda_con_su_valor(pagina):
    _abrir(pagina, OLA_1)
    pagina.click("#nuevo-mensaje")
    pagina.wait_for_selector("#mensaje-plantilla")
    pagina.fill('.modal-box input[name="nombre"]', "Gracias")
    pagina.select_option("#mensaje-plantilla", label="gracias · es · Solo texto")
    pagina.select_option('[data-parametro="0"]', "texto_fijo")
    pagina.fill('[data-texto-fijo="0"]', "ganás 200 puntos")
    pagina.click(".modal-foot .btn-orange")
    pagina.wait_for_selector("#tabla-mensajes tbody tr >> text=Gracias")
    parametros = pagina.evaluate("""async () => {
        const api = await import('/js/api.js');
        const { items } = await api.mensajes.listar(1);
        return items.find((m) => m.nombre === 'Gracias').parametros;
    }""")
    assert parametros == [{"fuente": "texto_fijo", "valor": "ganás 200 puntos"}]


# ── R-CTL.4 · el modal de ingesta ─────────────────────────────────────

UUIDS = ["3f2b8c1e-9a4d-4c7e-8b21-0d6f5a9e1c33", "7a1d2e3f-4b5c-4d6e-8f90-a1b2c3d4e5f6"]


@pytest.fixture
def modal(navegador, servidor_web, carga, tmp_path):  # noqa: F811
    contexto = navegador.new_context()
    pagina = contexto.new_page()
    errores = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    pagina.route(f"{servidor_web}/prueba-ingesta.html", lambda ruta: ruta.fulfill(
        content_type="text/html", body=ARNES.replace("CARGA_ID", str(carga["id"]))))
    pagina.goto(f"{servidor_web}/prueba-ingesta.html")
    pagina.wait_for_function("window.listo === true")
    # El editor de temáticas de la revisión pide el vocabulario: acá, vacío.
    pagina.evaluate("""async () => {
        const api = await import('/js/api.js');
        api.tematicas.listar = async () => ({ items: [] });
        api.tematicas.taxonomia = async () => ({ areas: [] });
    }""")
    archivo = tmp_path / "alchemer.csv"
    archivo.write_text("pid,P1\n" + "\n".join(f"{u},Fernet" for u in UUIDS), encoding="utf-8")
    pagina.set_input_files("#archivo", str(archivo))
    pagina.wait_for_selector("#columna-id:not([disabled])")
    yield pagina
    assert not errores, errores
    contexto.close()


def test_una_columna_con_uuid_propone_el_tipo_id_persona(modal):
    modal.select_option("#columna-id", "pid")
    sync_api.expect(modal.locator("#tipo-identificador")).to_have_value("id_persona")
    sync_api.expect(modal.locator("[data-tipo-propuesto]")).to_be_visible()


def test_cambiar_el_tipo_deja_el_resumen_desactualizado(modal, resumen):  # noqa: F811
    modal.select_option("#columna-id", "pid")
    for texto in modal.locator(".pregunta-fila .p-texto").all():
        if texto.is_enabled() and not texto.input_value():
            texto.fill("¿Qué bebida consume?")
    modal.locator(".modal-foot .btn", has_text="Ingestar").click()
    modal.wait_for_function("window.pedidos.length >= 1")
    assert modal.evaluate("window.pedidos[0].tipo_identificador") == "id_persona"
    modal.evaluate(f"window.responder({json.dumps(resumen)})")
    modal.wait_for_selector("#tipo-en-revision")
    sync_api.expect(modal.locator("#tipo-en-revision")).to_have_value("id_persona")
    assert modal.locator("#resumen-desactualizado").is_hidden()
    modal.select_option("#tipo-en-revision", "alias")
    sync_api.expect(modal.locator("#resumen-desactualizado")).to_be_visible()
    assert modal.locator("#confirmar-importar").is_disabled()
    # Revisar de nuevo pide el resumen otra vez, con el tipo nuevo.
    modal.click("#revisar-de-nuevo")
    modal.wait_for_function("window.pedidos.length >= 2")
    assert modal.evaluate("window.pedidos[1].tipo_identificador") == "alias"
