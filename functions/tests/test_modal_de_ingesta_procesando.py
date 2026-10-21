"""R-ING.1 — el modal de ingesta mientras el servidor trabaja, en un navegador.

El bug: después de mapear, «Ingestar» dejaba el modal quieto mientras el
servidor preparaba la revisión (leer el archivo, proponer temáticas). Quien
lo usaba no distinguía «está procesando» de «no hizo nada» y volvía a
apretar. Estas pruebas abren el modal de verdad (`encuestas.js`) en Chromium
y controlan cuándo contesta el servidor, para mirar el modal **antes** de la
respuesta:

* el estado de procesando aparece en el momento del clic;
* el botón y el mapeo quedan deshabilitados;
* pasados unos segundos dice «sigue procesando»;
* una respuesta exitosa cierra el modal y pasa al seguimiento;
* un error muestra el motivo y devuelve el botón;
* «ya hay una en curso» (R-ING.2) lleva a su seguimiento.

El servidor es de mentira solo en el tiempo: el resumen de la revisión sale
de la ruta real (`solo_revisar`) contra Postgres, así la pantalla recibe lo
mismo que en producción.

Necesita Playwright para Python y un Chromium; sin ellos se omite.
"""

import functools
import glob
import http.server
import json
import os
import pathlib
import threading

import pytest

from panel_api import cargas, ruteo

from test_r_org_origen_y_ambitos import DEMOGRAFICAS, EVIDENCIA, PREGUNTAS, _fila

sync_api = pytest.importorskip("playwright.sync_api")

RAIZ = pathlib.Path(__file__).resolve().parents[2]
PUBLICO = RAIZ / "web" / "public"
FILAS = [_fila(i) for i in range(1, 4)]
CSV = "ID,NOM,DOC,SEXO,CONS,P1\n" + "\n".join(
    ",".join(f[c] for c in ("ID", "NOM", "DOC", "SEXO", "CONS", "P1")) for f in FILAS)

# La página de prueba: abre el modal de una carga y reemplaza las dos
# llamadas que van al servidor por promesas que la prueba resuelve cuando
# quiere. Todo lo demás es el modo demo de siempre.
ARNES = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<link rel="stylesheet" href="/css/estilo.css"></head>
<body><div id="toast-wrap"></div><div id="modal-wrap"></div><main></main>
<script type="module">
  import * as api from '/js/api.js';
  import { abrirCargaDePanelistas } from '/js/paginas/encuestas.js';
  api.estado.demo = true;
  window.pedidos = [];
  window.pendientes = [];
  api.cargas.ingestarFilas = (id, cuerpo) => new Promise((ok, mal) => {
    window.pedidos.push(cuerpo);
    window.pendientes.push({ ok, mal });
  });
  api.cargas.guardarTematicas = async () => ({ tematicas: [] });
  window.seguidos = [];
  api.ingestas.ver = async (trabajoId) => (window.seguidos.push(trabajoId), {
    trabajo_id: trabajoId, estado: 'terminada', estado_etiqueta: 'Terminada',
    terminal: true, filas_procesadas: 3, filas_total: 3, porcentaje: 100,
    resumen: {}, fallidos: [] });
  window.responder = (valor) => window.pendientes.shift().ok(valor);
  window.fallar = (mensaje, status, cuerpo) =>
    window.pendientes.shift().mal(new api.ErrorApi(mensaje, status, cuerpo));
  abrirCargaDePanelistas({ id: CARGA_ID, nombre: 'Ómnibus agosto 2026' }, () => {});
  window.listo = true;
</script></body></html>"""


def _chromium():
    candidatos = [os.environ.get("CHROMIUM_PATH", "")] + sorted(
        glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return next((c for c in candidatos if c and os.path.exists(c)), None)


class _Estaticos(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # sin una línea por archivo en la salida
        pass


@pytest.fixture(scope="module")
def servidor_web():
    manejador = functools.partial(_Estaticos, directory=str(PUBLICO))
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


@pytest.fixture(scope="module")
def navegador():
    try:
        with sync_api.sync_playwright() as p:
            nav = p.chromium.launch(executable_path=_chromium(),
                                    args=["--no-sandbox"])
            yield nav
            nav.close()
    except sync_api.Error as error:  # sin Chromium utilizable
        pytest.skip(f"no hay un Chromium para Playwright: {error}")


@pytest.fixture
def carga(conn_boveda):
    fila = cargas.crear(conn_boveda, "Ómnibus agosto 2026", fecha_estudio="2026-08-15",
                        tipo_datos_origen="encuesta", modo_relevamiento="telefonico")
    conn_boveda.commit()
    return fila


@pytest.fixture
def resumen(ctx, actor, carga):
    """La revisión, de la ruta real."""
    _, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta",
        {"filas": FILAS, "columna_id": "ID", "origen": "omnibus",
         "tipo_identificador": "alias", "modo": "crear_individuos",
         "preguntas": PREGUNTAS, "demograficas": DEMOGRAFICAS,
         "evidencia_consentimiento": EVIDENCIA, "solo_revisar": True,
         "proponer_tematicas": False},
        {}, actor("analista"), ctx)
    return json.loads(json.dumps(salida, default=str))


@pytest.fixture
def pagina(navegador, servidor_web, carga, tmp_path):
    contexto = navegador.new_context()
    pagina = contexto.new_page()
    errores = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    pagina.clock.install()
    pagina.route(f"{servidor_web}/prueba-ingesta.html", lambda ruta: ruta.fulfill(
        content_type="text/html",
        body=ARNES.replace("CARGA_ID", str(carga["id"]))))
    pagina.goto(f"{servidor_web}/prueba-ingesta.html")
    pagina.wait_for_function("window.listo === true")

    archivo = tmp_path / "respuestas.csv"
    archivo.write_text(CSV, encoding="utf-8")
    pagina.set_input_files("#archivo", str(archivo))
    pagina.wait_for_selector("#columna-id:not([disabled])")
    pagina.select_option("#columna-id", "ID")
    pagina.wait_for_selector(".pregunta-fila")
    for texto in pagina.locator(".pregunta-fila .p-texto").all():
        if texto.is_enabled() and not texto.input_value():
            texto.fill("Texto de la pregunta")
    yield pagina
    assert not errores, errores
    contexto.close()


def _ingestar(pagina):
    pagina.locator(".modal-foot .btn", has_text="Ingestar").click()
    pagina.wait_for_function("window.pedidos.length >= 1")


def _paso_al_seguimiento(pagina, trabajo_id):
    """El modal de ingesta se cerró, se siguió ese trabajo y, como el doble
    lo da por terminado, se ve su resumen (que es otro modal)."""
    pagina.wait_for_selector("#ing-formulario", state="detached")
    pagina.wait_for_function(f"window.seguidos.includes({trabajo_id})")
    sync_api.expect(pagina.locator(".modal-box")).to_contain_text("Ingesta terminada")


def _boton_ingestar(pagina):
    return pagina.locator(".modal-foot .btn", has_text="Ingestar")


def test_el_estado_de_procesando_aparece_antes_de_que_conteste_el_servidor(pagina):
    _ingestar(pagina)
    # El servidor todavía no contestó: el pedido está pendiente.
    assert pagina.evaluate("window.pendientes.length") == 1
    assert pagina.evaluate("window.pedidos[0].solo_revisar") is True

    for lugar in ("#ing-alerta", "#ing-alerta-pie"):
        aviso = pagina.locator(f"{lugar} .procesando")
        assert aviso.is_visible(), lugar
        assert "Preparando la carga" in aviso.inner_text()
        assert "No cierres esta ventana" in aviso.inner_text()


def test_mientras_procesa_el_boton_y_el_mapeo_estan_deshabilitados(pagina):
    _ingestar(pagina)
    assert _boton_ingestar(pagina).is_disabled()
    assert pagina.locator(".modal-head .modal-close").is_disabled()
    assert pagina.locator("#columna-id").is_disabled()
    habilitados = pagina.locator(
        "#ing-formulario input:enabled, #ing-formulario select:enabled, "
        "#ing-formulario textarea:enabled, #ing-formulario button:enabled")
    assert habilitados.count() == 0


def test_un_doble_clic_manda_un_solo_pedido(pagina):
    boton = _boton_ingestar(pagina)
    boton.dblclick()
    pagina.wait_for_function("window.pedidos.length >= 1")
    pagina.clock.run_for(500)
    assert pagina.evaluate("window.pedidos.length") == 1


def test_si_tarda_dice_que_sigue_procesando(pagina):
    _ingestar(pagina)
    pagina.clock.run_for(9000)
    aviso = pagina.locator("#ing-alerta .procesando")
    assert "Sigue procesando" in aviso.inner_text()
    assert "9 s" in aviso.inner_text()


def test_un_error_muestra_el_motivo_y_devuelve_el_boton(pagina):
    deshabilitados_antes = pagina.locator("#ing-formulario :disabled").count()
    _ingestar(pagina)
    pagina.evaluate("window.fallar('Falta la evidencia de consentimiento.', 400, "
                    "{ error: 'datos_invalidos' })")

    pagina.wait_for_selector("#ing-alerta .alert")
    assert "Falta la evidencia de consentimiento." in pagina.inner_text("#ing-alerta")
    assert "Falta la evidencia de consentimiento." in pagina.inner_text("#ing-alerta-pie")
    assert pagina.locator(".procesando").count() == 0
    assert _boton_ingestar(pagina).is_enabled()
    assert pagina.locator("#columna-id").is_enabled()
    # Lo que estaba deshabilitado antes sigue así: soltar no habilita de más.
    assert pagina.locator("#ing-formulario :disabled").count() == deshabilitados_antes


def test_la_respuesta_exitosa_cierra_el_modal_y_pasa_al_seguimiento(pagina, resumen):
    _ingestar(pagina)
    pagina.evaluate("r => window.responder(r)", resumen)
    pagina.wait_for_selector("#confirmar-importar")
    assert pagina.locator(".procesando").count() == 0

    pagina.click("#confirmar-importar")
    pagina.wait_for_function("window.pedidos.length >= 2")
    # Confirmado y sin respuesta todavía: procesando y bloqueado.
    assert not pagina.evaluate("window.pedidos[1].solo_revisar")
    assert pagina.locator("#ing-alerta .avance").is_visible()
    assert "Preparando la carga" in pagina.inner_text("#ing-alerta")
    assert pagina.locator("#ing-alerta-pie .procesando").is_visible()
    assert _boton_ingestar(pagina).is_disabled()

    pagina.evaluate("window.responder({ trabajo_id: 77, filas_total: 3, "
                    "lotes_total: 1, estado: 'encolada' })")
    _paso_al_seguimiento(pagina, 77)


def test_con_una_ingesta_en_curso_ofrece_ir_a_su_seguimiento(pagina):
    _ingestar(pagina)
    pagina.evaluate("""window.fallar(
        'Ya hay una ingesta de esta carga en curso. Se muestra su avance: '
        + 'cuando termine se puede cargar otra.', 409,
        { error: 'ingesta_en_curso', detalle: { trabajo_id: 77, estado: 'procesando' } })""")

    boton = pagina.locator("#ing-alerta [data-ver-en-curso]")
    sync_api.expect(boton).to_be_visible()
    assert "en curso" in pagina.inner_text("#ing-alerta")
    boton.click()
    _paso_al_seguimiento(pagina, 77)
