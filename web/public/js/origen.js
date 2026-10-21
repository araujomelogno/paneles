/* R-ORG.5 — «Origen»: de qué estudios proviene una persona.

   Lo usan la ficha de la bóveda (Panelistas) y la ficha seudónima que se abre
   desde un resultado (Consultas). Un solo lugar para pintarlo, por la misma
   razón que `respuestas.js`: dos copias de la misma sección terminan
   diciendo cosas distintas. Son datos del estudio —nombre, fecha, público
   objetivo, tipo de datos y modo de relevamiento—, no de la persona, así que
   no la identifican. */

import { esc, fechaCorta } from './ui.js';

/* R-ORG.5 — los estudios de los que proviene, con los datos de la carga tal
   como están hoy. «Creada» es que esa carga la dio de alta; «reutilizada»,
   que ya existía y el dedup la encontró. Sin vínculo no dice «sin origen»:
   explica que lo cargado antes de R-ORG no dejó constancia. */
export function pintarOrigen(origen, { compacto = false } = {}) {
  const estudios = origen?.estudios || [];
  if (!estudios.length) {
    return `<p class="small muted" style="${compacto ? '' : 'padding:1rem 1.5rem'}">${esc(origen?.aviso
      || 'Sin registro de la carga de la que proviene.')}</p>`;
  }
  return `<div class="table-wrap"><table>
    <thead><tr><th>Estudio</th><th>Fecha</th><th>Público objetivo</th><th>Tipo de datos</th><th>Modo de relevamiento</th><th>En esta carga</th></tr></thead>
    <tbody>${estudios.map((e) => `<tr>
      <td class="td-strong">${esc(e.nombre)}</td>
      <td class="small">${e.fecha_estudio ? esc(fechaCorta(e.fecha_estudio)) : '<span class="muted">sin fecha</span>'}</td>
      <td class="small">${esc(e.publico_objetivo || '—')}</td>
      <td class="small">${esc(e.tipo_datos_origen_etiqueta || '—')}</td>
      <td class="small">${esc(e.modo_relevamiento_etiqueta || '—')}</td>
      <td><span class="badge ${e.origen === 'creada' ? 'badge-on' : ''}">${
        e.origen === 'creada' ? 'creada' : 'reutilizada'}</span></td>
    </tr>`).join('')}</tbody></table></div>`;
}


/* R-CAR.4 — la sección «Cargas» de la ficha de la bóveda: en qué cargas
   aparece, la más reciente primero, si la creó o la reutilizó, los clientes
   de cada carga y las temáticas que respondió **en ella**. Cada carga lleva
   a su detalle (`data-ir-carga`). Debajo, si entró por la landing, un alta
   manual o una encuesta. Los clientes son confidenciales: esta sección no
   está en la ficha seudónima ni en el portal. */
export function pintarCargas(datos) {
  const cargas = datos?.cargas || [];
  const ingresos = datos?.otros_ingresos || [];
  const chips = (lista, clase) => (lista.length
    ? `<span class="tematicas-chips">${lista.map((x) =>
      `<span class="chip ${clase}">${esc(x.nombre)}</span>`).join('')}</span>`
    : '<span class="small muted">—</span>');
  return `${cargas.length ? `<div class="table-wrap"><table>
    <thead><tr><th>Carga</th><th>Fecha</th><th>En esta carga</th><th>Clientes</th>
      <th>Temáticas que respondió</th></tr></thead>
    <tbody>${cargas.map((c) => `<tr>
      <td><a class="td-strong" href="#" data-ir-carga="${c.carga_id}">${esc(c.nombre)}</a></td>
      <td class="small">${c.fecha_estudio ? esc(fechaCorta(c.fecha_estudio)) : '<span class="muted">sin fecha</span>'}</td>
      <td><span class="badge ${c.origen === 'creada' ? 'badge-on' : ''}">${
        c.origen === 'creada' ? 'creado' : 'reutilizado'}</span></td>
      <td>${chips(c.clientes || [], 'chip-cliente')}</td>
      <td>${chips(c.tematicas || [], 'chip-tematica')}</td>
    </tr>`).join('')}</tbody></table></div>`
    : `<p class="small muted" style="padding:1rem 1.5rem 0">${esc(datos?.aviso
      || 'No aparece en ninguna carga.')}</p>`}
  ${ingresos.length ? `<div style="padding:.75rem 1.5rem">${ingresos.map((i) => `
    <div class="small"><span class="badge">${esc(i.etiqueta)}</span>
      ${i.detalle ? esc(i.detalle) : ''}
      ${i.fecha ? `<span class="muted">· ${esc(fechaCorta(i.fecha))}</span>` : ''}</div>`).join('')}
  </div>` : ''}`;
}
