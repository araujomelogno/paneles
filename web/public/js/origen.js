/* R-ORG.5 — «Origen»: de qué estudios proviene una persona.

   Lo usan la ficha de la bóveda (Panelistas) y la ficha seudónima que se abre
   desde un resultado (Consultas). Un solo lugar para pintarlo, por la misma
   razón que `respuestas.js`: dos copias de la misma sección terminan
   diciendo cosas distintas. Son datos del estudio —nombre, fecha, público
   objetivo—, no de la persona, así que no la identifican. */

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
    <thead><tr><th>Estudio</th><th>Fecha</th><th>Público objetivo</th><th>En esta carga</th></tr></thead>
    <tbody>${estudios.map((e) => `<tr>
      <td class="td-strong">${esc(e.nombre)}</td>
      <td class="small">${e.fecha_estudio ? esc(fechaCorta(e.fecha_estudio)) : '<span class="muted">sin fecha</span>'}</td>
      <td class="small">${esc(e.publico_objetivo || '—')}</td>
      <td><span class="badge ${e.origen === 'creada' ? 'badge-on' : ''}">${
        e.origen === 'creada' ? 'creada' : 'reutilizada'}</span></td>
    </tr>`).join('')}</tbody></table></div>`;
}

