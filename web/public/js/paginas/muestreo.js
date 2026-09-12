/* Muestreo por reglas (R3.1).

   Esta pantalla es la que convierte la composición —que solo mira— en una
   decisión. Por eso tiene dos partes que pesan lo mismo:

     la propuesta      a quién invitar, y por qué está cada uno
     lo que quedó afuera   y por qué, que es la mitad que se suele esconder

   Un panel que propone diez nombres sin decir por qué faltan los otros
   doscientos no sirve para decidir. Y los avisos —«este segmento tiene
   brecha y no hay a quién ofrecer»— van arriba de todo, porque son
   justamente lo que una lista más corta taparía.
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
          <button class="btn" id="umbrales">Umbrales de fatiga…</button></div>
      </div>
    </div></div>
    <div id="cuerpo"></div>`;

  $('#panel').onchange = (e) => { panelActual = Number(e.target.value); cargarEncuestas(); };
  $('#proponer').onclick = proponer;
  $('#umbrales').onclick = abrirUmbrales;
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
