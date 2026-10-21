"""PEDIDO — El detector de datos personales revisa la columna de identidad
(parte 2 de `docs/PEDIDO_celular_identificador_y_falso_positivo_pii.md`).

Con el celular elegido como columna que identifica al respondente, la
revisión decía «Posibles datos personales en var7O11», con nivel «rompe» y
la acción «Ingestarla igual, a conciencia». Falso: esa columna nunca va al
store semántico —el despivote la saltea— y tampoco las marcadas como
demográficas o de identidad y contacto (R3.9.d). Una alarma que salta sobre
lo obvio enseña a no leerla, y el día que sea real pasa de largo.

Lo que se prueba: el chequeo mira **lo que se va a ingestar**. En el
servidor (el resumen de la revisión, que es el que manda) y en la pantalla
(el panel de calidad del modal, en Chromium).
"""

import json

import pytest

from panel_api import calidad_dato, resumen_ingesta

FILAS = [
    {"ID": f"r-{i}", "CEL": f"099 123 4{i:02d}", "MAIL": f"persona{i}@ejemplo.com",
     # Una cédula válida (el detector verifica el dígito).
     "DOC": "1.234.567-2",
     "P9": f"Otro: llamame al 098 555 1{i:02d}", "P1": "Antel"}
    for i in range(1, 6)
]


def _pregunta(codigo, texto, tipo="abierta"):
    return {"codigo": codigo, "texto": texto, "tipo": tipo, "opciones": {}}


PREGUNTAS = [
    _pregunta("CEL", "Celular"),
    _pregunta("MAIL", "Correo"),
    _pregunta("DOC", "Cédula"),
    _pregunta("P9", "Otra compañía, especificar"),
    _pregunta("P1", "¿Qué compañía usa?"),
]


def _resumen(columna_id="ID", demograficas=None):
    return resumen_ingesta.resumir(
        {"preguntas": PREGUNTAS, "columna_id": columna_id,
         "demograficas": demograficas or {}, "modo": "existen"},
        FILAS, destino_tipo="carga", nombre_destino="Panel telefónico")


def _con_pii(resumen):
    """Las variables con la advertencia, en el diagnóstico y en el resumen."""
    hallazgos = {v for h in resumen["calidad"]["hallazgos"]
                 if h["tipo"] == "pii_en_texto_libre" for v in h["variables"]}
    avisos = {v for a in resumen["advertencias"]
              if a["tipo"] == "pii_en_texto_libre" for v in a["variables"]}
    assert hallazgos == avisos
    return hallazgos


def test_la_columna_de_identidad_no_genera_la_advertencia():
    """El caso del pedido: el celular como identificador, sin marcar."""
    assert "CEL" not in _con_pii(_resumen(columna_id="CEL"))


def test_la_columna_de_identidad_no_figura_como_que_va_al_semantico():
    resumen = _resumen(columna_id="CEL")
    assert "CEL" not in {v["codigo"] for v in resumen["al_store_semantico"]["variables"]}
    excluida = next(e for e in resumen["excluidas"] if e["codigo"] == "CEL")
    assert "identifica al respondente" in excluida["motivo"]


@pytest.mark.parametrize("variable,campo", [
    ("CEL", "celular"), ("MAIL", "email"), ("DOC", "documento"),
])
def test_una_variable_de_identidad_y_contacto_no_la_genera(variable, campo):
    assert variable in _con_pii(_resumen())          # sin marcar, la genera
    assert variable not in _con_pii(_resumen(demograficas={variable: campo}))


def test_una_abierta_que_va_al_semantico_con_telefonos_sigue_generandola():
    """No regresión: la advertencia existe para esto, una «Otro,
    especificar» donde alguien escribió su número."""
    identidad = {"MAIL": "email", "DOC": "documento"}
    for columna_id, demograficas in (("CEL", identidad),
                                     ("ID", {**identidad, "CEL": "celular"})):
        assert _con_pii(_resumen(columna_id, demograficas)) == {"P9"}
    # Y una de identidad sin marcar, que sí iría al semántico, también avisa.
    assert _con_pii(_resumen("CEL", {"DOC": "documento"})) == {"MAIL", "P9"}


def test_marcar_despues_hace_desaparecer_la_advertencia():
    """El resumen se recalcula con la configuración vigente: la misma carga,
    revisada de nuevo con la variable marcada, ya no la tiene."""
    antes = _resumen(columna_id="ID")
    assert "CEL" in _con_pii(antes)
    despues = _resumen(columna_id="ID", demograficas={"CEL": "celular"})
    assert "CEL" not in _con_pii(despues)
    assert "P9" in _con_pii(despues)


# ════════════════════════════════════════════════════════════════════
#  La pantalla: el panel de calidad del modal
# ════════════════════════════════════════════════════════════════════

sync_api = pytest.importorskip("playwright.sync_api")

from test_modal_de_ingesta_procesando import navegador, servidor_web  # noqa: E402,F401

CSV = "ID,CEL,P9,P1\n" + "\n".join(
    f"{f['ID']},{f['CEL']},{f['P9']},{f['P1']}" for f in FILAS)

ARNES = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<link rel="stylesheet" href="/css/estilo.css"></head>
<body><div id="toast-wrap"></div><div id="modal-wrap"></div><main></main>
<script type="module">
  import * as api from '/js/api.js';
  import { abrirCargaDePanelistas } from '/js/paginas/encuestas.js';
  api.estado.demo = true;
  // El diagnóstico es el del servidor de verdad, calculado por la prueba.
  api.calidadDato.diagnostico = async () => (DIAGNOSTICO);
  abrirCargaDePanelistas({ id: 1, nombre: 'Panel telefónico' }, () => {});
  window.listo = true;
</script></body></html>"""


@pytest.fixture
def pagina(navegador, servidor_web, tmp_path):  # noqa: F811
    """El modal con el `.csv` cargado y el diagnóstico de `calidad_dato`
    sobre todas las columnas menos la que la pantalla propone como id."""
    diagnostico = calidad_dato.diagnosticar(
        [p for p in PREGUNTAS if p["codigo"] in ("CEL", "P9", "P1")],
        calidad_dato.distribucion_de_filas(FILAS, {"CEL", "P9", "P1"}),
        filas_total=len(FILAS))
    contexto = navegador.new_context()
    pagina = contexto.new_page()
    errores = []
    pagina.on("pageerror", lambda e: errores.append(str(e)))
    pagina.route(f"{servidor_web}/prueba-pii.html", lambda ruta: ruta.fulfill(
        content_type="text/html",
        body=ARNES.replace("DIAGNOSTICO", json.dumps(diagnostico))))
    pagina.goto(f"{servidor_web}/prueba-pii.html")
    pagina.wait_for_function("window.listo === true")
    archivo = tmp_path / "telefonico.csv"
    archivo.write_text(CSV, encoding="utf-8")
    pagina.set_input_files("#archivo", str(archivo))
    pagina.wait_for_selector("#calidad-panel .calidad")
    yield pagina
    assert not errores, errores
    contexto.close()


def _panel(pagina):
    return pagina.inner_text("#calidad-panel")


def test_en_la_pantalla_la_columna_elegida_como_id_no_tiene_la_advertencia(pagina):
    # Con «ID» como identificador, el celular sí iría al semántico: avisa.
    assert pagina.input_value("#columna-id") == "ID"
    assert "Posibles datos personales en CEL" in _panel(pagina)
    assert "Posibles datos personales en P9" in _panel(pagina)

    pagina.select_option("#columna-id", "CEL")
    assert "Posibles datos personales en CEL" not in _panel(pagina)
    assert "Posibles datos personales en P9" in _panel(pagina)


def test_en_la_pantalla_marcar_el_celular_hace_desaparecer_su_advertencia(pagina):
    fila = pagina.locator(".pregunta-fila", has=pagina.locator(
        ".p-codigo[value='CEL']"))
    rol = fila.locator(".p-rol")
    valor = rol.locator("option", has_text="elular").first.get_attribute("value")
    rol.select_option(valor)
    assert "Posibles datos personales en CEL" not in _panel(pagina)
    assert "Posibles datos personales en P9" in _panel(pagina)
