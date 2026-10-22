"""Las partes de pantalla de SPEC_clientes_lista_negra_y_mejoras, en Chromium.

Lo que solo existe en la pantalla —la paginación de Panelistas es una de
ellas— no lo puede probar el backend. Cada prueba abre el módulo de verdad
(`paginas/*.js`) en modo demo y reemplaza las llamadas que importan por
dobles que registran qué se pidió:

* R-PAN.1 — la grilla de Panelistas pagina, conserva los filtros al pasar
  de página y vuelve a la primera al cambiar uno;
* R-LN.1 — agregar a la lista negra pide el motivo y no cierra sin él;
* R-CAR.3 — «reutilizados» abre el desglose, con la fila de «sin carga
  previa» y la nota de que una persona puede estar en varias;
* R-CLI.3 — «+ Cliente» agrega el criterio de incluir o excluir;
* R-AUD.1 — Cumplimiento muestra las reidentificaciones por evento y manda
  los filtros.

Necesita Playwright para Python y un Chromium; sin ellos se omite.
"""

import pytest

from test_modal_de_ingesta_procesando import (  # noqa: F401  (fixtures)
    navegador, servidor_web, sync_api,
)

ARNES = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<link rel="stylesheet" href="/css/estilo.css"></head>
<body><div id="toast-wrap"></div><div id="modal-wrap"></div><main></main>
<script type="module">
  import * as api from '/js/api.js';
  api.estado.demo = true;
  window.pedidos = [];
  window.irA = [];
  const ctx = (rol) => ({ actor: { rol, uid: 'u', email: rol + '@equipos.com.uy' },
                          irA: (id, c) => window.irA.push([id, c || null]) });
  SCRIPT
  window.listo = true;
</script></body></html>"""


@pytest.fixture
def abrir(navegador, servidor_web):
    contextos = []

    def _abrir(script):
        contexto = navegador.new_context()
        contextos.append(contexto)
        pagina = contexto.new_page()
        errores = []
        pagina.on("pageerror", lambda e: errores.append(str(e)))
        pagina.errores = errores
        pagina.route(f"{servidor_web}/prueba.html", lambda ruta: ruta.fulfill(
            content_type="text/html", body=ARNES.replace("SCRIPT", script)))
        pagina.goto(f"{servidor_web}/prueba.html")
        pagina.wait_for_function("window.listo === true", timeout=15000)
        return pagina

    yield _abrir
    for contexto in contextos:
        contexto.close()


# ════════════════════════════════════════════════════════════════════
#  R-PAN.1 · La grilla pagina
# ════════════════════════════════════════════════════════════════════

PANELISTAS = """
  const gente = Array.from({ length: 120 }, (_, i) => ({
    id_persona: '00000000-0000-0000-0000-' + String(i).padStart(12, '0'),
    nombre: 'Persona ' + i, documento: String(i), paneles: 0,
    consiente_contacto: true, consiente_semantico: true,
    en_lista_negra: i === 0,
  }));
  api.panelistas.listar = async (consulta) => {
    window.pedidos.push(consulta);
    const q = (consulta.q || '').toLowerCase();
    const items = gente.filter((p) => !q || p.nombre.toLowerCase().includes(q));
    const desde = Number(consulta.desde || 0);
    return { total: items.length, items: items.slice(desde, desde + Number(consulta.limite)) };
  };
  const pagina = await import('/js/paginas/panelistas.js');
  await pagina.render(document.querySelector('main'), ctx('admin'));
"""


def _info(pagina):
    return pagina.locator("[data-pagina-info]").inner_text()


def _ultimo_pedido(pagina):
    return pagina.evaluate("window.pedidos[window.pedidos.length - 1]")


def test_la_grilla_dice_en_que_pagina_esta(abrir):
    pagina = abrir(PANELISTAS)
    pagina.wait_for_selector("[data-pagina-info]")
    assert _info(pagina) == "Página 1 de 3 · 120 panelistas"
    assert pagina.locator("[data-anterior]").is_disabled()
    assert _ultimo_pedido(pagina)["desde"] == 0
    # La de la lista negra, marcada en la grilla.
    assert pagina.locator("tr.en-lista-negra").count() == 1
    assert not pagina.errores, pagina.errores


def test_pasar_de_pagina_conserva_los_filtros_y_cambiar_uno_vuelve_a_la_primera(abrir):
    pagina = abrir(PANELISTAS)
    pagina.wait_for_selector("[data-pagina-info]")
    pagina.fill("#q", "persona 1")          # 1, 10-19, 100-119: 31 personas
    pagina.wait_for_function("window.pedidos.at(-1).q === 'persona 1'")
    pagina.wait_for_function(
        "document.querySelector('[data-pagina-info]')?.innerText.includes('31 panelistas')")
    assert _info(pagina) == "Página 1 de 1 · 31 panelistas"

    pagina.fill("#q", "")
    pagina.wait_for_function(
        "document.querySelector('[data-pagina-info]')?.innerText.includes('120')")
    pagina.click("[data-siguiente]")
    pagina.wait_for_function(
        "document.querySelector('[data-pagina-info]')?.innerText.startsWith('Página 2')")
    assert _ultimo_pedido(pagina)["desde"] == 50

    # Un filtro nuevo vuelve a la primera página, con el filtro puesto.
    pagina.fill("#q", "persona 11")
    pagina.wait_for_function("window.pedidos.at(-1).q === 'persona 11'")
    assert _ultimo_pedido(pagina)["desde"] == 0
    pagina.fill("#q", "")
    pagina.wait_for_function("window.pedidos.at(-1).q === ''")

    # El tamaño de página: 25, y otra vez desde el principio.
    pagina.wait_for_selector("[data-tamano]")
    pagina.select_option("[data-tamano]", "25")
    pagina.wait_for_function("window.pedidos.at(-1).limite === 25")
    assert _ultimo_pedido(pagina)["desde"] == 0
    pagina.wait_for_function(
        "document.querySelector('[data-pagina-info]')?.innerText === 'Página 1 de 5 · 120 panelistas'")
    assert not pagina.errores, pagina.errores


# ════════════════════════════════════════════════════════════════════
#  R-LN.1 · El modal pide el motivo
# ════════════════════════════════════════════════════════════════════

LISTA_NEGRA = """
  window.agregadas = [];
  api.listaNegra.listar = async () => ({ total: 0, items: [], categorias: [
    { clave: 'calidad_respuestas', etiqueta: 'Calidad de respuestas' },
    { clave: 'otro', etiqueta: 'Otro' }] });
  api.listaNegra.agregar = async (id, categoria, detalle) => {
    window.agregadas.push({ id, categoria, detalle });
    if (!categoria) throw new api.ErrorApi('Elegí la categoría del motivo.', 400, {});
    return { id: 1, categoria, detalle, invitaciones_canceladas: [{ encuesta: 'Ola 3' }] };
  };
  const ln = await import('/js/lista_negra.js');
  window.listos = 0;
  await ln.abrirAgregar('abc', () => { window.listos += 1; });
"""


def test_agregar_a_la_lista_negra_pide_hechos_y_no_cierra_sin_motivo(abrir):
    pagina = abrir(LISTA_NEGRA)
    modal = pagina.locator(".modal-box")
    sync_api.expect(modal).to_contain_text("Describí hechos")
    sync_api.expect(modal).to_contain_text("No se le avisa")
    boton = pagina.locator(".modal-foot .btn", has_text="Agregar a la lista negra")

    boton.click()
    sync_api.expect(pagina.locator("#ln-alerta")).to_contain_text("Elegí la categoría")
    assert pagina.evaluate("window.listos") == 0          # sigue abierto

    pagina.select_option("[name='categoria']", "calidad_respuestas")
    pagina.fill("[name='detalle']", "Respondió en 40 segundos.")
    boton.click()
    pagina.wait_for_function("window.listos === 1")
    assert pagina.evaluate("window.agregadas.at(-1)") == {
        "id": "abc", "categoria": "calidad_respuestas", "detalle": "Respondió en 40 segundos."}
    sync_api.expect(pagina.locator("#toast-wrap")).to_contain_text("1 invitación")
    assert not pagina.errores, pagina.errores


# ════════════════════════════════════════════════════════════════════
#  R-CAR.3 · El desglose de los reutilizados
# ════════════════════════════════════════════════════════════════════

CARGAS = """
  api.tematicas.listar = async () => ({ items: [] });
  api.cargas.listar = async () => ({ items: [{
    id: 7, nombre: 'Centel', fecha_estudio: '2026-06-04', publico_objetivo: 'Total país',
    personas: 487, personas_creadas: 339, personas_reutilizadas: 148,
    tematicas: [], clientes: [{ id: 1, nombre: 'antel' }] }] });
  api.cargas.reutilizados = async (id) => (window.pedidos.push(id), {
    carga_id: id, reutilizados: 148,
    cargas: [{ carga_id: 3, nombre: 'Ómnibus mayo', fecha_estudio: '2026-05-01', personas: 120 },
             { carga_id: 2, nombre: 'Tracking abril', fecha_estudio: '2026-04-01', personas: 40 }],
    sin_carga_previa: 12,
    sin_carga_previa_etiqueta: 'sin carga previa: landing, alta manual o encuesta',
    nota: 'Una persona puede aparecer en más de una carga.' });
  const pagina = await import('/js/paginas/cargas.js');
  await pagina.render(document.querySelector('main'), ctx('analista'));
"""


def test_reutilizados_abre_el_desglose_y_lleva_a_cada_carga(abrir):
    pagina = abrir(CARGAS)
    # El listado muestra también los clientes de la carga.
    sync_api.expect(pagina.locator("main")).to_contain_text("antel")
    pagina.click("[data-reutilizados='7']")
    modal = pagina.locator(".modal-box")
    sync_api.expect(modal).to_contain_text("Ómnibus mayo")
    sync_api.expect(modal).to_contain_text("Una persona puede aparecer en más de una carga")
    sync_api.expect(modal).to_contain_text("sin carga previa: landing, alta manual o encuesta")
    filas = modal.locator("tbody tr")
    assert filas.count() == 3
    assert filas.nth(0).inner_text().endswith("120")
    assert filas.nth(2).inner_text().endswith("12")

    modal.locator("[data-ir-carga='2']").click()
    assert pagina.evaluate("window.irA.at(-1)") == ["cargas", {"cargaId": 2}]
    assert not pagina.errores, pagina.errores


# ════════════════════════════════════════════════════════════════════
#  R-CLI.3 · El criterio de cliente
# ════════════════════════════════════════════════════════════════════

CONSULTAS = """
  api.clientes.listar = async () => ({ items: [
    { id: 1, nombre: 'antel', cargas: 2 }, { id: 2, nombre: 'movistar', cargas: 1 }] });
  const pagina = await import('/js/paginas/consultas.js');
  await pagina.render(document.querySelector('main'), ctx('analista'));
"""


def test_mas_cliente_agrega_el_criterio_de_excluir(abrir):
    pagina = abrir(CONSULTAS)
    pagina.click("#agregar-cliente")
    modal = pagina.locator(".modal-box")
    sync_api.expect(modal).to_contain_text("antel")
    modal.locator("[name='operador']").select_option("not_in")
    modal.locator("[data-cliente='1']").check()
    modal.locator(".modal-foot .btn", has_text="Agregar").click()
    sync_api.expect(pagina.locator("#criterios")).to_contain_text(
        "No participó en estudios para antel")
    assert not pagina.errores, pagina.errores


# ════════════════════════════════════════════════════════════════════
#  R-AUD.1 · Las reidentificaciones en Cumplimiento
# ════════════════════════════════════════════════════════════════════

CUMPLIMIENTO = """
  api.reidentificacion.registro = async (consulta) => (window.pedidos.push(consulta), {
    total: 1, items: [{ evento_id: 9, actor_uid: 'u', actor_email: 'ana@equipos.com.uy',
      motivo: 'consulta', motivo_etiqueta: 'Resultado de consulta resuelto a contacto',
      contexto: { ruta: 'POST /reidentificacion' }, creado_en: '2026-10-20T15:00:00Z',
      personas: 2, ids_persona: ['11111111-1111-1111-1111-111111111111',
                                 '22222222-2222-2222-2222-222222222222'] }] });
  const pagina = await import('/js/paginas/cumplimiento.js');
  await pagina.render(document.querySelector('main'), ctx(ROL));
"""


def test_cumplimiento_muestra_las_reidentificaciones_por_evento(abrir):
    pagina = abrir(CUMPLIMIENTO.replace("ROL", "'dpo'"))
    tabla = pagina.locator("#reidentificaciones")
    sync_api.expect(tabla).to_contain_text("ana@equipos.com.uy")
    sync_api.expect(tabla).to_contain_text("Resultado de consulta resuelto a contacto")
    assert pagina.evaluate("window.pedidos[0].eventos") == 1

    tabla.locator("[data-cuales='0']").click()
    sync_api.expect(tabla.locator("[data-detalle='0']")).to_be_visible()
    tabla.locator("[data-ficha='22222222-2222-2222-2222-222222222222']").click()
    assert pagina.evaluate("window.irA.at(-1)") == [
        "panelistas", {"idPersona": "22222222-2222-2222-2222-222222222222"}]

    pagina.fill("#reid-filtros [name='panelista']", "4.123.456-7")
    pagina.fill("#reid-filtros [name='desde']", "2026-10-01")
    pagina.click("#reid-filtrar")
    pagina.wait_for_function("window.pedidos.length === 2")
    pedido = pagina.evaluate("window.pedidos[1]")
    assert pedido["panelista"] == "4.123.456-7" and pedido["desde"] == "2026-10-01"
    assert not pagina.errores, pagina.errores


def test_sin_permiso_de_cumplimiento_no_aparece(abrir):
    pagina = abrir(CUMPLIMIENTO.replace("ROL", "'analista'"))
    pagina.wait_for_selector("#auditoria")
    assert pagina.locator("#reidentificaciones").count() == 0
    assert pagina.evaluate("window.pedidos.length") == 0
