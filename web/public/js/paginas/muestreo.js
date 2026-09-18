/* Muestreo: reglas (R3.1) y optimizador (R4.2).

   Esta pantalla es la que convierte la composición —que solo mira— en una
   decisión. Por eso tiene dos partes que pesan lo mismo:

     la propuesta      a quién invitar, y por qué está cada uno
     lo que quedó afuera   y por qué, que es la mitad que se suele esconder

   Un panel que propone diez nombres sin decir por qué faltan los otros
   doscientos no sirve para decidir. Y los avisos —«este segmento tiene
   brecha y no hay a quién ofrecer»— van arriba de todo, porque son
   justamente lo que una lista más corta taparía.

   Desde R4.2 hay dos métodos y **conviven a propósito**. Las reglas alcanzan
   cuando hay holgura; el optimizador negocia cuando cerrar la cuota y cuidar
   a la gente se contradicen. Los dos botones están uno al lado del otro, y
   «Comparar» muestra la diferencia: es lo que permite justificar ante alguien
   por qué esta vez se usó el otro método.

   Cuando el optimizador no puede, la pantalla no muestra una lista más corta
   y ya: muestra las tres salidas con su número —reducir, aflojar la fatiga,
   aceptar la brecha— y no elige ninguna.
*/

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, alerta, token, toast, modal,
  cerrarModal, leerFormulario, fechaCorta,
} from '../ui.js';

let contexto = {};
let panelActual = null;
let encuestaActual = null;
let ultimaPropuesta = null;

const DIMENSIONES = { sexo: 'Sexo', tramo_etario: 'Tramo etario', localidad: 'Localidad' };

export async function render(main, ctx) {
  contexto = ctx;
  const { items: paneles } = await api.paneles.listar();
  if (!paneles.length) {
    main.innerHTML = encabezado('Muestreo', 'por reglas', '')
      + vacio('No hay paneles todavía.', '📋');
    return;
  }
  panelActual = paneles.some((p) => p.id === panelActual) ? panelActual : paneles[0].id;

  main.innerHTML = encabezado('Muestreo', 'por reglas',
    'A quién invitar para cerrar la brecha de cuota, sin volver a convocar a los mismos.') + `
    <div class="card"><div class="card-body">
      <div class="grid-3">
        <div class="form-group"><label>Panel</label>
          <select class="fselect" id="panel">${paneles.map((p) =>
            `<option value="${p.id}" ${p.id === panelActual ? 'selected' : ''}>${esc(p.nombre)}</option>`
          ).join('')}</select></div>
        <div class="form-group"><label>Encuesta</label>
          <select class="fselect" id="encuesta"></select></div>
        <div class="form-group"><label>Equilibrar por</label>
          <select class="fselect" id="dimension">${Object.entries(DIMENSIONES).map(([k, v]) =>
            `<option value="${k}">${v}</option>`).join('')}</select></div>
      </div>
      <div class="grid-3">
        <div class="form-group"><label>Cuántos</label>
          <input class="finput" id="cantidad" type="number" min="1" value="100"></div>
        <div class="form-group" style="align-self:end">
          <button class="btn btn-primary" id="proponer">Proponer</button></div>
        <div class="form-group" style="align-self:end">
          <button class="btn" id="optimizar">Optimizar (R4.2)</button></div>
        <div class="form-group" style="align-self:end">
          <button class="btn" id="umbrales">Umbrales de fatiga…</button></div>
        <div class="form-group" style="align-self:end">
          <button class="btn" id="pesos">Pesos del optimizador…</button></div>
      </div>
    </div></div>
    <div id="cuerpo"></div>`;

  $('#panel').onchange = (e) => { panelActual = Number(e.target.value); cargarEncuestas(); };
  $('#proponer').onclick = proponer;
  $('#optimizar').onclick = optimizar;
  $('#umbrales').onclick = abrirUmbrales;
  $('#pesos').onclick = abrirPesos;
  await cargarEncuestas();
}

async function cargarEncuestas() {
  const { items } = await api.encuestas.listar(panelActual);
  const abiertas = items.filter((e) => e.estado !== 'cerrada');
  encuestaActual = abiertas[0]?.id ?? null;
  $('#encuesta').innerHTML = abiertas.length
    ? abiertas.map((e) => `<option value="${e.id}">${esc(e.nombre)}</option>`).join('')
    : '<option value="">— sin encuestas abiertas —</option>';
  $('#encuesta').onchange = (e) => { encuestaActual = Number(e.target.value); };
  $('#cuerpo').innerHTML = '';
}

async function proponer() {
  if (!encuestaActual) { toast('Elegí una encuesta abierta.', 'error'); return; }
  $('#cuerpo').innerHTML = cargando('20vh');
  try {
    ultimaPropuesta = await api.muestreo.proponer(encuestaActual, {
      dimension: $('#dimension').value,
      cantidad: Number($('#cantidad').value) || 100,
    });
    pintar(ultimaPropuesta);
  } catch (error) {
    $('#cuerpo').innerHTML = alerta(error.message);
  }
}

function pintar(propuesta) {
  // `alerta()` escapa su contenido, así que el mensaje va en texto plano.
  const avisos = propuesta.avisos.map((a) => alerta(
    a.categoria ? `${a.categoria}: ${a.mensaje}` : a.mensaje,
    a.tipo === 'segmento_sin_elegibles' ? 'error' : 'warn',
  )).join('');

  const umbrales = propuesta.umbrales;
  const nota = umbrales.son_defaults
    ? 'Con los umbrales por defecto: todavía nadie los calibró contra los datos de Equipos.'
    : 'Con los umbrales configurados para este panel.';

  $('#cuerpo').innerHTML = `
    ${avisos}
    ${alerta(`Es una sugerencia: nadie fue convocado. ${esc(nota)}`, 'info')}
    <div class="card"><div class="card-head">
        <h3>Propuesta · ${propuesta.propuesta.length} de ${propuesta.cantidad_pedida} pedidos</h3>
        <button class="btn btn-primary" id="convocar" ${propuesta.propuesta.length ? '' : 'disabled'}>
          Convocar a estos ${propuesta.propuesta.length}</button>
      </div>
      <div class="card-body" style="padding:0">${tabla(propuesta)}</div>
    </div>
    ${exclusiones(propuesta)}`;
  if (propuesta.propuesta.length) $('#convocar').onclick = () => confirmarConvocatoria(propuesta);
}

function tabla(propuesta) {
  if (!propuesta.propuesta.length) {
    return vacio('Ninguna persona quedó elegible. Mirá los avisos y las exclusiones.', '🚫');
  }
  return `<table class="tabla"><thead><tr>
      <th>Persona</th><th>${esc(DIMENSIONES[propuesta.dimension])}</th>
      <th>Convocatorias</th><th>Respondió</th><th>Última</th><th>Por qué está</th>
    </tr></thead><tbody>
    ${propuesta.propuesta.map((p) => `<tr>
      <td class="mono">${esc(p.id_persona.slice(0, 8))}…</td>
      <td>${token(p.categoria)}</td>
      <td>${p.convocatorias_recientes} recientes · ${p.convocatorias_totales} en total</td>
      <td>${p.respondidas}</td>
      <td>${p.ultima_convocatoria ? fechaCorta(p.ultima_convocatoria) : '—'}</td>
      <td class="tenue">${esc(p.motivo_prioridad)}</td>
    </tr>`).join('')}
    </tbody></table>`;
}

function exclusiones(propuesta) {
  if (!propuesta.resumen_exclusiones.length) return '';
  return `<div class="card"><div class="card-head">
      <h3>Quiénes quedaron afuera</h3>
      <button class="btn btn-sm" id="ver-excluidos">Ver el detalle</button>
    </div><div class="card-body">
      <table class="tabla"><thead><tr><th>Motivo</th><th>Personas</th></tr></thead><tbody>
        ${propuesta.resumen_exclusiones.map((e) => `<tr>
          <td>${esc(e.explicacion)}</td><td><strong>${e.personas}</strong></td>
        </tr>`).join('')}
      </tbody></table>
    </div></div>`;
}

function confirmarConvocatoria(propuesta) {
  modal({
    titulo: `Convocar a ${propuesta.propuesta.length} personas`,
    cuerpo: `<p>Se van a crear las participaciones de esta ola. El gate de
      consentimiento se vuelve a aplicar al convocar, así que si alguien
      retiró el suyo entre la propuesta y ahora, queda afuera igual.</p>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Convocar', clase: 'btn-primary',
        onClick: async () => {
          try {
            const r = await api.encuestas.convocar(encuestaActual, {
              idsPersona: propuesta.propuesta.map((p) => p.id_persona),
            });
            cerrarModal();
            toast(`Convocadas ${r.convocados ?? r.creadas ?? 0} personas.`, 'ok');
            await proponer();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}

async function abrirUmbrales() {
  const actuales = await api.muestreo.umbrales(panelActual);
  modal({
    titulo: 'Umbrales de fatiga',
    ancho: 560,
    cuerpo: `
      <p class="tenue">Deciden a quién considera «quemado» el muestreo.
      ${actuales.son_defaults ? 'Ahora rigen los valores por defecto, que no están medidos contra los datos de Equipos.' : ''}</p>
      <div class="grid-2">
        <div class="form-group"><label>Máximo de convocatorias en la ventana</label>
          <input class="finput" name="max_convocatorias_ventana" type="number" min="0"
                 value="${actuales.max_convocatorias_ventana}"></div>
        <div class="form-group"><label>Ventana (días)</label>
          <input class="finput" name="ventana_dias" type="number" min="1"
                 value="${actuales.ventana_dias}"></div>
        <div class="form-group"><label>Días mínimos entre olas</label>
          <input class="finput" name="dias_minimos_entre" type="number" min="0"
                 value="${actuales.dias_minimos_entre}"></div>
        <div class="form-group"><label>Tope histórico (vacío = sin tope)</label>
          <input class="finput" name="max_convocatorias_total" type="number" min="1"
                 value="${actuales.max_convocatorias_total ?? ''}"></div>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Guardar', clase: 'btn-primary',
        onClick: async (contenedor) => {
          const datos = leerFormulario(contenedor);
          if (datos.max_convocatorias_total === '') datos.max_convocatorias_total = null;
          try {
            await api.muestreo.guardarUmbrales(panelActual, datos);
            cerrarModal();
            toast('Umbrales guardados.', 'ok');
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}

/* ── R4.2 · Optimizador ─────────────────────────────────────────────── */

async function optimizar() {
  if (!encuestaActual) { toast('Elegí una encuesta abierta.', 'error'); return; }
  $('#cuerpo').innerHTML = cargando('20vh');
  try {
    const salida = await api.optimizador.optimizar(encuestaActual, {
      dimension: $('#dimension').value,
      cantidad: Number($('#cantidad').value) || 100,
    });
    ultimaPropuesta = salida;
    pintarOptimizada(salida);
  } catch (error) {
    $('#cuerpo').innerHTML = alerta(error.message);
  }
}

function pintarOptimizada(salida) {
  $('#cuerpo').innerHTML = `
    ${alerta('Es una sugerencia: nadie fue convocado. El optimizador propone; '
             + 'convocar sigue siendo una acción explícita.', 'info')}
    ${salida.factible ? '' : infactible(salida)}
    <div class="card"><div class="card-head">
        <h3>Optimizada · ${salida.propuesta.length} de ${salida.cantidad_pedida} pedidos</h3>
        <button class="btn" id="comparar">Comparar con las reglas</button>
        <button class="btn btn-primary" id="convocar" ${salida.propuesta.length ? '' : 'disabled'}>
          Convocar a estos ${salida.propuesta.length}</button>
      </div>
      <div class="card-body" style="padding:0">${tablaOptimizada(salida)}</div>
    </div>
    <div id="comparacion"></div>
    ${exclusiones(salida)}`;

  $('#comparar').onclick = comparar;
  if (salida.propuesta.length) {
    $('#convocar').onclick = () => confirmarConvocatoria(salida);
  }
}

/* Las tres salidas, con su número. El sistema no elige ninguna: elegir entre
   menos muestra, más fatiga y más brecha es una decisión de negocio. */
function infactible(salida) {
  return `<div class="card"><div class="card-head">
      <h3>No se puede cerrar la cuota</h3></div>
    <div class="card-body">
      <p>${esc(salida.motivo)}</p>
      <table class="tabla"><thead><tr>
        <th>Alternativa</th><th>Qué pasa</th><th>Qué cuesta</th>
      </tr></thead><tbody>
      ${salida.alternativas.map((a) => `<tr>
        <td><strong>${esc(ALTERNATIVAS[a.opcion] || a.opcion)}</strong></td>
        <td class="tenue">${esc(a.descripcion)}</td>
        <td class="tenue">${esc(a.cuesta)}</td>
      </tr>`).join('')}
      </tbody></table>
      <p class="tenue">El sistema no elige entre las tres: menos muestra, más
      fatiga y más brecha son costos distintos y los paga el estudio.</p>
    </div></div>`;
}

const ALTERNATIVAS = {
  reducir_el_tamano: 'Reducir el tamaño',
  aflojar_la_fatiga: 'Aflojar la fatiga',
  aceptar_la_brecha: 'Aceptar la brecha',
};

function tablaOptimizada(salida) {
  if (!salida.propuesta.length) {
    return vacio('Ninguna persona quedó elegible. Mirá las exclusiones.', '🚫');
  }
  return `<table class="tabla"><thead><tr>
      <th>#</th><th>Persona</th><th>${esc(DIMENSIONES[salida.dimension] || salida.dimension)}</th>
      <th>Convocatorias</th><th>Por qué entró</th>
    </tr></thead><tbody>
    ${salida.propuesta.map((p) => `<tr>
      <td>${p.orden}</td>
      <td class="mono">${esc(p.id_persona.slice(0, 8))}…</td>
      <td>${token(p.categoria)}</td>
      <td>${p.convocatorias_recientes} recientes · ${p.convocatorias_totales} en total</td>
      <td class="tenue">Al segmento le faltaban
        ${p.porque.deficit_del_segmento_al_entrar} ·
        aporta ${p.porque.aporte_a_la_brecha} ·
        cuesta ${(p.porque.costo_fatiga + p.porque.costo_equidad).toFixed(2)}
        (${p.porque.costo_fatiga.toFixed(2)} de fatiga,
         ${p.porque.costo_equidad.toFixed(2)} de equidad)</td>
    </tr>`).join('')}
    </tbody></table>`;
}

async function comparar() {
  const caja = $('#comparacion');
  caja.innerHTML = cargando('15vh');
  try {
    const c = await api.optimizador.comparar(encuestaActual, {
      dimension: $('#dimension').value,
      cantidad: Number($('#cantidad').value) || 100,
    });
    const categorias = [...new Set([
      ...Object.keys(c.reglas.por_categoria),
      ...Object.keys(c.optimizador.por_categoria),
    ])].sort();
    caja.innerHTML = `<div class="card"><div class="card-head">
        <h3>Los dos métodos, sobre la misma pregunta</h3></div>
      <div class="card-body">
        ${c.coinciden ? alerta(
          'Las dos selecciones son idénticas: en este caso hay holgura y el '
          + 'optimizador no aporta nada sobre las reglas. La diferencia '
          + 'aparece en segmentos escasos y muy convocados.', 'info') : ''}
        <table class="tabla"><thead><tr>
          <th>Método</th><th>Personas</th>
          ${categorias.map((k) => `<th>${esc(k)}</th>`).join('')}
          <th>Fatiga media</th>
        </tr></thead><tbody>
          <tr><td>Reglas (R3.1)</td><td>${c.reglas.personas}</td>
            ${categorias.map((k) => `<td>${c.reglas.por_categoria[k] || 0}</td>`).join('')}
            <td>${c.reglas.fatiga_media}</td></tr>
          <tr><td>Optimizador (R4.2)</td><td>${c.optimizador.personas}</td>
            ${categorias.map((k) => `<td>${c.optimizador.por_categoria[k] || 0}</td>`).join('')}
            <td>${c.optimizador.fatiga_media}</td></tr>
        </tbody></table>
        <p class="tenue">${c.en_las_dos} en las dos ·
          ${c.solo_en_reglas.length} solo en reglas ·
          ${c.solo_en_optimizador.length} solo en el optimizador.</p>
      </div></div>`;
  } catch (error) { caja.innerHTML = alerta(error.message); }
}

async function abrirPesos() {
  const actuales = await api.optimizador.pesos(panelActual);
  modal({
    titulo: 'Pesos del optimizador',
    ancho: 560,
    cuerpo: `
      <p class="tenue">Cuánto vale cerrar la cuota frente a cuidar a la gente.
      ${actuales.configurados
        ? `Última edición: ${esc(actuales.actualizado_por || '—')}.`
        : 'Ahora rigen los valores por defecto documentados en R4.2.'}</p>
      <div class="form-group"><label>Cerrar la brecha de cuota</label>
        <input class="finput" name="peso_brecha" type="number" min="0" step="0.5"
               value="${actuales.peso_brecha}">
        <span class="tenue">La escala contra la que se leen los otros dos.</span></div>
      <div class="form-group"><label>Costo de la fatiga</label>
        <input class="finput" name="peso_fatiga" type="number" min="0" step="0.5"
               value="${actuales.peso_fatiga}">
        <span class="tenue">Cuánto cuesta volver a convocar a alguien que ya
        viene siendo convocado. Bajarlo es decidir quemar un poco más al
        panel.</span></div>
      <div class="form-group"><label>Costo del desbalance de rotación</label>
        <input class="finput" name="peso_equidad" type="number" min="0" step="0.5"
               value="${actuales.peso_equidad}">
        <span class="tenue">Distinto de la fatiga: alguien puede estar lejos
        del umbral y aun así ser siempre el elegido de su celda.</span></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Guardar', clase: 'btn-primary',
        onClick: async (contenedor) => {
          try {
            await api.optimizador.guardarPesos(
              panelActual, leerFormulario(contenedor));
            cerrarModal();
            toast('Pesos guardados.', 'ok');
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}
