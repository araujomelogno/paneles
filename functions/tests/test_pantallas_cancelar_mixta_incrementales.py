"""Los PEDIDOS de cancelar una carga, carga mixta y cargas incrementales
(D92 a D94), en la pantalla, en Chromium.

* R-CAN-C.2 — cancelar o cerrar el modal antes de confirmar borra la carga
  provisoria; confirmar no.
* R-CAN-C.4/5 — el seguimiento dice que cerrar no detiene, ofrece «Detener
  ingesta» con confirmación y, detenida, «Reanudar» y «Borrar lo cargado». La
  revisión, al apretar «Ingestar», dice lo mismo.
* R-MIX — sin la pregunta 4; el ID de respuesta opcional; la revisión con
  existentes y nuevas, la proporción inesperada destacada, y la evidencia de
  consentimiento pedida solo cuando hay a quién crear.
* R-INC.4/5 — la ficha agrupa por encuesta con la nota de las posteriores; el
  detalle de una carga separa nuevos, reutilizados y ya estaban.
* R-CAN-C.3 — Cumplimiento dispara la limpieza a mano.
"""

import json

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from panel_api import personas, ruteo  # noqa: E402
from conftest import consentimientos  # noqa: E402
from test_modal_de_ingesta_procesando import (  # noqa: E402,F401
    ARNES, PUBLICO, carga, navegador, servidor_web,
)

# El arnés de siempre, con la carga provisoria y un espía de «cancelar».
ARNES_PROVISORIA = ARNES.replace(
    "abrirCargaDePanelistas({ id: CARGA_ID, nombre: 'Ómnibus agosto 2026' }, () => {});",
    """window.canceladas = [];
  api.cargas.cancelar = async (id) => (window.canceladas.push(id), { borrada: true });
  window.detenidas = [];
  api.ingestas.detener = async (id) => (window.detenidas.push(id), {});
  abrirCargaDePanelistas({ id: CARGA_ID, nombre: 'Ómnibus agosto 2026', provisoria: true },
                         () => {});""")
assert ARNES_PROVISORIA != ARNES

PREGUNTAS = [{"codigo": "P1", "texto": "¿Qué bebida consume?", "tipo": "abierta", "orden": 1}]
AVISO = ("El procesamiento sigue en el servidor aunque cierres esta ventana. "
         "Para frenarlo, usá «Detener ingesta».")


@pytest.fixture
def revision_mixta(ctx, actor, carga):  # noqa: F811
    """La revisión de un archivo mixto, de la ruta real: una existente por
    id_persona y tres nuevas, sin evidencia declarada."""
    ana = str(personas.alta(ctx.boveda, {
        "persona": {"nombre": "Ana", "email": "ana@ej.uy"},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico")})
        ["id_persona"])
    ctx.boveda.commit()
    filas = [{"ID": ana, "NOM": "Ana", "MAIL": "ana@ej.uy", "P1": "Fernet"}] + [
        {"ID": "", "NOM": f"Nueva {i}", "MAIL": f"n{i}@ej.uy", "P1": "Whisky"} for i in range(3)]
    _, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta",
        {"filas": filas, "columna_id": "ID", "tipo_identificador": "id_persona",
         "preguntas": PREGUNTAS, "demograficas": {"NOM": "nombre", "MAIL": "email"},
         "solo_revisar": True, "proponer_tematicas": False},
        {}, actor("analista"), ctx)
    return json.loads(json.dumps(salida, default=str))


@pytest.fixture
def modal(navegador, servidor_web, carga, tmp_path):  # noqa: F811
    contexto = navegador.new_context()
    pagina = contexto.new_page()
    errores = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    pagina.route(f"{servidor_web}/prueba-ingesta.html", lambda ruta: ruta.fulfill(
        content_type="text/html", body=ARNES_PROVISORIA.replace("CARGA_ID", str(carga["id"]))))
    pagina.goto(f"{servidor_web}/prueba-ingesta.html")
    pagina.wait_for_function("window.listo === true")
    pagina.evaluate("""async () => {
        const api = await import('/js/api.js');
        api.tematicas.listar = async () => ({ items: [] });
        api.tematicas.taxonomia = async () => ({ areas: [] });
    }""")
    archivo = tmp_path / "alchemer.csv"
    archivo.write_text("ID,NOM,MAIL,RID,P1\n,Nueva,n@ej.uy,R-1,Fernet\n", encoding="utf-8")
    pagina.set_input_files("#archivo", str(archivo))
    pagina.wait_for_selector("#columna-id:not([disabled])")
    yield pagina
    assert not errores, errores
    contexto.close()


def _a_la_revision(modal, resumen):
    for texto in modal.locator(".pregunta-fila .p-texto").all():
        if texto.is_enabled() and not texto.input_value():
            texto.fill("¿Qué bebida consume?")
    modal.locator(".modal-foot .btn", has_text="Ingestar").click()
    modal.wait_for_function("window.pedidos.length >= 1")
    modal.evaluate(f"window.responder({json.dumps(resumen)})")
    modal.wait_for_selector("#reconocimiento")


def test_no_hay_pregunta_4_y_se_puede_declarar_el_id_de_respuesta(modal):
    assert modal.locator("#modo-sav").count() == 0
    assert "¿Los panelistas ya están en el sistema?" not in modal.content()
    assert modal.locator("#bloque-alta").is_hidden()        # sin nuevas a la vista, no se pide
    sync_api.expect(modal.locator("#columna-respuesta")).to_be_enabled()
    modal.select_option("#columna-respuesta", "RID")
    modal.select_option("#columna-id", "ID")
    for texto in modal.locator(".pregunta-fila .p-texto").all():
        if texto.is_enabled() and not texto.input_value():
            texto.fill("Texto")
    modal.locator(".modal-foot .btn", has_text="Ingestar").click()
    modal.wait_for_function("window.pedidos.length >= 1")
    pedido = modal.evaluate("window.pedidos[0]")
    assert pedido["columna_respuesta"] == "RID"
    assert "modo" not in pedido and "evidencia_consentimiento" not in pedido


def test_cancelar_antes_de_confirmar_borra_la_carga_provisoria(modal, carga):  # noqa: F811
    modal.locator(".modal-foot .btn", has_text="Cancelar").click()
    modal.wait_for_function("window.canceladas.length === 1")
    assert modal.evaluate("window.canceladas") == [carga["id"]]


def test_cerrar_con_la_cruz_tambien_cancela(modal, carga):  # noqa: F811
    modal.click("[data-cerrar]")
    modal.wait_for_function("window.canceladas.length === 1")


def test_la_revision_muestra_existentes_nuevas_y_pide_la_evidencia(modal, revision_mixta):
    _a_la_revision(modal, revision_mixta)
    sync_api.expect(modal.locator("#reconocimiento-texto")).to_have_text(
        "4 filas · 1 existentes · 3 nuevas")
    # La proporción inesperada, destacada (id_persona y 3 de 4 nuevas).
    sync_api.expect(modal.locator("#aviso-proporcion")).to_contain_text(
        "columna identificadora o su tipo")
    # Hay a quién crear y no se declaró la evidencia: no se confirma.
    sync_api.expect(modal.locator("#falta-alta")).to_be_visible()
    assert modal.locator("#confirmar-importar").is_disabled()
    # R-CAN-C.5 — el mismo aviso que el seguimiento.
    sync_api.expect(modal.locator("#aviso-cerrar-revision")).to_have_text(AVISO)
    modal.click("#volver-a-corregir")
    sync_api.expect(modal.locator("#bloque-alta")).to_be_visible()


def test_confirmar_no_cancela_y_el_seguimiento_ofrece_detener(modal, revision_mixta):
    resumen = {**revision_mixta,
               "reconocimiento": {**revision_mixta["reconocimiento"],
                                  "requiere_consentimiento": False, "advertencias": []}}
    modal.evaluate("""async () => {
        const api = await import('/js/api.js');
        window.consultas = 0;
        api.ingestas.ver = async (id) => {
          window.consultas += 1;
          const detenida = window.detenidas.length > 0;
          return { trabajo_id: id, destino: { tipo: 'carga', id: 1 },
                   estado: detenida ? 'detenida' : 'procesando',
                   estado_etiqueta: detenida ? 'Detenida' : 'Procesando',
                   terminal: detenida, detenible: !detenida, lotes_en_curso: 0,
                   lotes_total: 4, lotes_ok: 1, filas_procesadas: 1, filas_total: 4,
                   porcentaje: 25, resumen: {}, fallidos: [],
                   aviso_cerrar: 'El procesamiento sigue en el servidor aunque cierres esta ventana. Para frenarlo, usá «Detener ingesta».',
                   detencion: detenida ? { lotes_procesados: 1, lotes_sin_procesar: 3,
                                           filas_procesadas: 1, filas_total: 4 } : undefined };
        };
    }""")
    _a_la_revision(modal, resumen)
    modal.click("#confirmar-importar")
    modal.wait_for_function("window.pedidos.length >= 2")
    modal.evaluate("window.responder({ trabajo_id: 77, lotes_total: 4, filas_total: 4 })")
    modal.wait_for_selector("#detener-ingesta")
    assert modal.evaluate("window.canceladas") == []          # confirmada: no se borra
    sync_api.expect(modal.locator("#aviso-cerrar")).to_have_text(AVISO)

    modal.click("#detener-ingesta")
    modal.locator(".modal-foot .btn", has_text="Detener ingesta").click()
    modal.wait_for_function("window.detenidas.length === 1")
    modal.wait_for_selector("#ingesta-detenida")
    sync_api.expect(modal.locator("#ingesta-detenida")).to_contain_text("3")
    sync_api.expect(modal.locator("#reanudar-ingesta")).to_be_visible()
    sync_api.expect(modal.locator("#borrar-lo-cargado")).to_be_visible()


# ── En la aplicación demo ─────────────────────────────────────────────

@pytest.fixture
def demo(navegador, servidor_web):  # noqa: F811
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


def _ir(pagina, destino, esperar):
    """El menú lateral puede quedar fuera de la ventana: el clic va por el
    DOM. Y la página de inicio puede terminar de pintarse después del clic y
    taparlo: si no aparece lo esperado, se vuelve a pedir."""
    for _ in range(4):
        pagina.locator(f'[data-pagina="{destino}"]').evaluate("(e) => e.click()")
        try:
            pagina.wait_for_selector(esperar, timeout=5000)
            return
        except sync_api.TimeoutError:
            continue
    pagina.wait_for_selector(esperar)


def test_la_ficha_agrupa_por_encuesta_con_la_nota_de_las_posteriores(demo):
    html = demo.evaluate("""async () => {
        const { pintarCargas } = await import('/js/origen.js');
        return pintarCargas({ cargas: [{
          carga_id: 1, nombre: 'Ola · carga 1', encuesta_id: 9, origen: 'creada',
          clientes: [], tematicas: [],
          posteriores: [{ carga_id: 2, nombre: 'Ola · carga 2' },
                        { carga_id: 3, nombre: 'Ola · carga 3' }],
          nota_posteriores: 'también en 2 cargas posteriores de esta encuesta' }],
          otros_ingresos: [] });
    }""")
    # En un contenedor propio: la página demo repinta `main` cuando quiere.
    demo.evaluate("(html) => document.body.insertAdjacentHTML("
                  "'afterbegin', `<div id=\"prueba-ficha\">${html}</div>`)", html)
    ficha = demo.locator("#prueba-ficha")
    assert ficha.locator("[data-ir-carga]", has_text="Ola · carga 1").count() == 1
    nota = ficha.locator("[data-posteriores] summary")
    sync_api.expect(nota).to_have_text("también en 2 cargas posteriores de esta encuesta")
    assert ficha.locator("[data-posteriores] a", has_text="Ola · carga 2").is_hidden()
    nota.click()
    sync_api.expect(ficha.locator("[data-posteriores] a", has_text="Ola · carga 2")).to_be_visible()


def test_el_detalle_de_una_carga_separa_nuevos_reutilizados_y_ya_estaban(demo):
    _ir(demo, "cargas", "[data-carga]")
    demo.locator("[data-carga]").first.click()
    sync_api.expect(demo.locator("#resumen-personas")).to_contain_text("en el archivo")
    sync_api.expect(demo.locator("#dato-repetidas")).to_contain_text(
        "ya estaban en cargas anteriores de esta encuesta")


def test_cumplimiento_dispara_la_limpieza_de_provisorias(demo):
    _ir(demo, "cumplimiento", "#provisorias p")
    demo.locator("#limpiar-provisorias").evaluate("(e) => e.click()")
    sync_api.expect(demo.locator("#toast-wrap")).to_contain_text("Limpieza:")
    sync_api.expect(demo.locator("#provisorias table")).to_contain_text("a mano")
