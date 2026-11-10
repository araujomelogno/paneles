"""PEDIDO 06/11/26 (D87 a D90), en la pantalla: la app entera, en modo demo,
en Chromium.

* Parte 1 — ya no existe la sección «WhatsApp Flow»: el Flow es un tipo de
  botón de la plantilla de un mensaje.
* Parte 2 — «Registrar canales aceptados» en el detalle de una carga, con
  vista previa y registro.
* Parte 3 — la consulta demográfica sortea con semilla, «Volver a sortear»
  cambia la semilla, la paginación es del mismo sorteo y ordenar la tabla no
  cambia quiénes son.
* Parte 4 — el listado de encuestas filtra por estado; «Nueva encuesta»
  tiene selector de tipo y el mismo formulario que la carga; la lista de la
  encuesta se busca y se pagina; agregar audiencia desde una consulta; los
  cruces en porcentaje con sus bases.
"""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_modal_de_ingesta_procesando import PUBLICO, navegador, servidor_web  # noqa: E402,F401

OLA_1 = "Ola 1 — Consumo de bebidas"
OLA_2 = "Ola 2 — Hábitos digitales"
OLA_3 = "Ola 3 — Movilidad urbana"


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


def _abrir_encuesta(pagina, nombre):
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("[data-encuesta]")
    pagina.locator("tr", has_text=nombre).first.locator("[data-encuesta]").click()
    pagina.wait_for_selector("#participacion table, #participacion .empty-state")
    pagina.wait_for_selector("#mensajes table, #mensajes p")


# ── Parte 1 · El Flow es un botón del mensaje ─────────────────────────

def test_no_hay_seccion_whatsapp_flow_y_el_flow_es_un_mensaje(pagina):
    _abrir_encuesta(pagina, OLA_3)
    titulos = [t.strip() for t in pagina.locator(".card-header-title").all_text_contents()]
    assert "WhatsApp Flow" not in titulos
    assert pagina.locator("#configurar-flow, #enviar-wa").count() == 0
    fila = pagina.locator("#tabla-mensajes tbody tr", has_text="WhatsApp Flow")
    sync_api.expect(fila.locator("[data-mensaje-flow]")).to_have_text("Flow")
    # Las plantillas de un mensaje dicen qué botón llevan.
    pagina.click("#nuevo-mensaje")
    pagina.wait_for_selector("#mensaje-plantilla")
    opciones = pagina.locator("#mensaje-plantilla option").all_text_contents()
    assert any("Botón de Flow" in o for o in opciones)
    assert any("Botón de URL" in o for o in opciones)
    pagina.select_option("#mensaje-plantilla", label="invitacion_ola · es · Botón de Flow")
    sync_api.expect(pagina.locator("[data-tipo-boton]")).to_have_attribute(
        "data-tipo-boton", "flow")


# ── Parte 2 · Registrar canales aceptados ─────────────────────────────

def test_registrar_canales_de_una_carga_con_vista_previa(pagina):
    pagina.click('[data-pagina="cargas"]')
    pagina.wait_for_selector("[data-carga]")
    pagina.locator("tr", has_text="Ómnibus agosto 2026").first.locator("[data-carga]").click()
    pagina.wait_for_selector("#canales-sin-registro")
    pagina.click("#registrar-canales")
    # Los textos publicados se cargan al abrir: el desplegable tiene que estar.
    pagina.wait_for_selector("#canal-whatsapp-version", state="attached")
    pagina.check('[data-canal="whatsapp"]')
    sync_api.expect(pagina.locator("#confirmar-canales")).to_be_disabled()
    pagina.click(".modal-foot .btn-outline >> text=Ver vista previa")
    pagina.wait_for_selector("[data-previa-canales]")
    sync_api.expect(pagina.locator("#confirmar-canales")).to_be_enabled()
    pagina.click("#confirmar-canales")
    pagina.wait_for_selector("#tabla-canales-carga")
    fila = pagina.locator("#tabla-canales-carga tbody tr").first
    assert "WhatsApp" in fila.text_content() and "En bloque" in fila.text_content()


# ── Parte 3 · El sorteo de la consulta demográfica ────────────────────

def _consulta_demografica(pagina, limite=None):
    pagina.click('[data-pagina="consultas"]')
    pagina.wait_for_selector("#agregar-demografico")
    pagina.click("#agregar-demografico")
    pagina.select_option('.modal-box [name="dimension"]', "edad")
    pagina.select_option('.modal-box [name="operador"]', "gte")
    pagina.fill('.modal-box [name="valor"]', "1")
    pagina.click(".modal-foot .btn-orange")
    if limite:
        pagina.click("#parametros")
        pagina.fill('.modal-box [name="limite"]', str(limite))
        pagina.fill('.modal-box [name="top_k"]', str(max(limite, 25)))
        pagina.click(".modal-foot .btn-dark")
    pagina.click("#correr")
    pagina.wait_for_selector("#tabla-demografica")


def _filas(pagina):
    return [f.get_attribute("data-fila-persona")
            for f in pagina.locator("#tabla-demografica tbody tr").all()]


def test_la_demografica_sortea_y_ordenar_no_cambia_quienes(pagina):
    _consulta_demografica(pagina, limite=4)
    semilla = pagina.text_content("[data-semilla]").strip()
    assert semilla
    antes = _filas(pagina)
    assert len(antes) == 4
    # Ordenar por una columna es solo de lectura.
    pagina.locator('#tabla-demografica th[data-ordenar="localidad"]').click()
    assert sorted(_filas(pagina)) == sorted(antes)
    # Otra página del mismo sorteo: la misma semilla y nadie repetido.
    pagina.locator("[data-pagina-resultado='2']").click()
    pagina.wait_for_function(
        "(viejas) => ![...document.querySelectorAll('#tabla-demografica tbody tr')]"
        ".some((f) => viejas.includes(f.dataset.filaPersona))", arg=antes)
    assert pagina.text_content("[data-semilla]").strip() == semilla
    # Volver a sortear: otra semilla.
    pagina.click("#volver-a-sortear")
    pagina.wait_for_function(
        "(s) => document.querySelector('[data-semilla]')?.textContent.trim() !== s",
        arg=semilla)


# ── Parte 4 · Encuestas: filtros, nueva encuesta, lista, audiencia ────

def test_el_listado_filtra_por_estado(pagina):
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("[data-encuesta]")
    pagina.select_option("#f-estado", "cerrada")
    pagina.wait_for_function(
        "() => document.querySelector('#f-estado')?.value === 'cerrada'"
        " && [...document.querySelectorAll('[data-estado-encuesta]')]"
        ".every((s) => s.value === 'cerrada')")
    nombres = " ".join(pagina.locator("td.td-strong").all_text_contents())
    assert OLA_1 in nombres and OLA_2 not in nombres


def test_nueva_encuesta_independiente_con_el_mismo_formulario_que_la_carga(pagina):
    # El formulario de la carga (Panelistas → Cargar panelistas).
    pagina.click('[data-pagina="panelistas"]')
    pagina.wait_for_selector("#cargar")
    pagina.click("#cargar")
    pagina.wait_for_selector("[data-campos-estudio]")
    de_la_carga = [c.get_attribute("data-campo-estudio")
                   for c in pagina.locator("[data-campo-estudio]").all()]
    pagina.click(".modal-foot .btn-outline")
    # El de «Nueva encuesta».
    pagina.click('[data-pagina="encuestas"]')
    pagina.wait_for_selector("#nueva")
    pagina.click("#nueva")
    pagina.wait_for_selector("[data-campos-estudio]")
    de_la_encuesta = [c.get_attribute("data-campo-estudio")
                      for c in pagina.locator("[data-campo-estudio]").all()]
    assert de_la_encuesta == de_la_carga == [
        "nombre", "fecha_campo", "publico_objetivo", "clientes", "url_alchemer", "descripcion"]
    pagina.select_option("#tipo-nueva", "independiente")
    assert not pagina.is_visible("#grupo-panel")
    pagina.fill('.modal-box [name="nombre"]', "Estudio en pasos")
    pagina.click(".modal-foot .btn-orange")
    pagina.wait_for_selector("#titulo-participacion")
    assert pagina.text_content("#titulo-participacion").strip() == "Convocatoria"
    assert "se arma a mano o desde consultas" in pagina.text_content("#texto-participacion")
    assert pagina.locator("#agregar-desde-consulta").count() == 1


def test_la_lista_de_la_encuesta_se_busca_y_se_filtra(pagina):
    _abrir_encuesta(pagina, OLA_1)
    pagina.wait_for_selector("#buscar-participacion")
    assert "Página 1 de 1" in pagina.text_content("[data-pagina-de]")
    total = pagina.locator("[data-fila-participacion]").count()
    pagina.fill("#buscar-participacion", "Ana")
    pagina.wait_for_function(
        "(n) => document.querySelectorAll('[data-fila-participacion]').length < n", arg=total)
    assert all("Ana" in f.text_content()
               for f in pagina.locator("[data-fila-participacion]").all())


def test_agregar_audiencia_desde_una_consulta(pagina):
    _abrir_encuesta(pagina, OLA_2)
    pagina.select_option("#estado-encuesta", "en_campo")
    pagina.wait_for_selector("#agregar-desde-consulta")
    pagina.click("#agregar-desde-consulta")
    pagina.wait_for_selector("#aviso-agregando")
    assert OLA_2 in pagina.text_content("#aviso-agregando")
    assert "No está en la audiencia" in pagina.text_content("#criterios")
    pagina.click("#agregar-demografico")
    pagina.select_option('.modal-box [name="dimension"]', "edad")
    pagina.select_option('.modal-box [name="operador"]', "gte")
    pagina.fill('.modal-box [name="valor"]', "1")
    pagina.click(".modal-foot .btn-orange")
    pagina.click("#correr")
    pagina.wait_for_selector("#agregar-a-encuesta")
    pagina.click("#agregar-a-encuesta")
    pagina.fill('.modal-box [name="cantidad"]', "2")
    pagina.click(".modal-foot .btn-orange")
    pagina.wait_for_selector("[data-agregado]")
    assert int(pagina.text_content("[data-agregadas]")) <= 2
    pagina.click(".modal-foot .btn-orange")
    pagina.wait_for_selector("#tabla-pasos-audiencia")
    assert "Aleatorio" in pagina.text_content("#tabla-pasos-audiencia")


def test_los_cruces_en_porcentaje_con_sus_bases(pagina):
    pagina.click('[data-pagina="composicion"]')
    pagina.wait_for_selector("#cuerpo")
    pagina.evaluate("""async () => {
        sessionStorage.removeItem('composicion.vistaDeCruce');
    }""")
    pagina.select_option("#cruce", "sexo,tramo_etario")
    pagina.wait_for_selector("[data-vista-cruce]")
    pagina.select_option("[data-vista-cruce]", "columna")
    sync_api.expect(pagina.locator("[data-tabla-cruce]")).to_have_attribute(
        "data-vista", "columna")
    assert "base" in pagina.text_content("[data-tabla-cruce] tfoot")
    assert "%" in pagina.text_content("[data-tabla-cruce] tbody")
    # La elección se mantiene en la sesión.
    assert pagina.evaluate("sessionStorage.getItem('composicion.vistaDeCruce')") == "columna"
