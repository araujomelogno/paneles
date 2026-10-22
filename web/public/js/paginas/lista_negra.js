/* R-LN.3 — Sección «Lista negra».

   Quiénes están, con el motivo, quién los agregó y cuándo. Paginada y con
   búsqueda, como Panelistas. Sacar a alguien pide un motivo y no borra la
   entrada: con «Incluir las removidas» se ve la historia completa, y el
   historial de cada persona es lo que corresponde entregar en un pedido de
   acceso (R-LN.4).

   La ven administración, operaciones y cumplimiento; agregar y sacar es de
   administración y operaciones. El servidor lo vuelve a controlar. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, fechaHora,
} from '../ui.js';
import { abrirSacar, abrirHistorial, puedeGestionar } from '../lista_negra.js';

let contexto = {};
const filtro = { q: '', removidas: false, desde: 0, limite: 25 };

export async function render(main, ctx) {
  contexto = ctx;
  main.innerHTML = encabezado('Lista negra', '',
    'Quienes no se convocan, ni desde acá ni desde COLOQUIO. Siguen apareciendo, '
    + 'marcados, en los resultados de consulta.') + `
    <div class="card">
      <div class="card-header">
        <div class="toolbar" style="flex:1">
          <div class="buscador">
            <input type="text" id="q" placeholder="Nombre, email o documento…"
                   value="${esc(filtro.q)}" />
          </div>
          <label class="check"><input type="checkbox" id="removidas"
            ${filtro.removidas ? 'checked' : ''} /> Incluir las removidas</label>
        </div>
      </div>
      <div class="card-body tight"><div id="tabla">${cargando('20vh')}</div></div>
    </div>
    <p class="small muted">Para agregar a alguien, abrí su ficha (en Panelistas o
      desde un resultado de consulta). El panelista no recibe ningún aviso ni lo ve
      en su portal.</p>`;

  let temporizador;
  $('#q').oninput = (e) => {
    filtro.q = e.target.value;
    filtro.desde = 0;
    clearTimeout(temporizador);
    temporizador = setTimeout(cargar, 280);
  };
  $('#removidas').onchange = (e) => { filtro.removidas = e.target.checked; filtro.desde = 0; cargar(); };
  await cargar();
}

async function cargar() {
  const contenedor = $('#tabla');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('20vh');
  const salida = await api.listaNegra.listar({
    q: filtro.q || undefined, desde: filtro.desde, limite: filtro.limite,
    removidas: filtro.removidas ? 1 : undefined,
  });
  if (!salida.items.length) {
    contenedor.innerHTML = vacio(filtro.q
      ? 'Nadie de la lista coincide con la búsqueda.'
      : 'La lista negra está vacía.', '✅');
    return;
  }
  const gestiona = puedeGestionar(contexto.actor);
  const paginas = Math.max(1, Math.ceil(salida.total / filtro.limite));
  const pagina = Math.floor(filtro.desde / filtro.limite) + 1;
  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Panelista</th><th>Motivo</th><th>Agregado</th>
        ${filtro.removidas ? '<th>Removido</th>' : ''}<th></th></tr></thead>
      <tbody>${salida.items.map((e) => `
        <tr class="${e.activa ? 'en-lista-negra' : ''}">
          <td><div class="td-strong">${esc(e.nombre || '—')}</div>
            <div class="td-muted small">${esc(e.documento || '')}</div>
            ${e.entradas > 1 ? `<div class="small muted">${e.entradas} entradas en su historial</div>` : ''}</td>
          <td><strong>${esc(e.categoria_etiqueta)}</strong><div class="small">${esc(e.detalle)}</div></td>
          <td class="small">${esc(fechaHora(e.agregado_en))}<div class="muted">${esc(e.agregado_por || '—')}</div></td>
          ${filtro.removidas ? `<td class="small">${e.removido_en
            ? `${esc(fechaHora(e.removido_en))}<div class="muted">${esc(e.removido_por || '—')}</div>
               <div>${esc(e.motivo_remocion)}</div>` : '<span class="badge badge-lista-negra">activa</span>'}</td>` : ''}
          <td class="right"><div class="toolbar">
            <button class="btn btn-outline btn-sm" data-ficha="${e.id_persona}">Ficha</button>
            <button class="btn btn-outline btn-sm" data-historial="${e.id_persona}"
                    data-nombre="${esc(e.nombre || '')}">Historial</button>
            ${gestiona && e.activa ? `<button class="btn btn-outline btn-sm" data-sacar="${e.id_persona}">Sacar…</button>` : ''}
          </div></td>
        </tr>`).join('')}
      </tbody>
    </table></div>
    <div class="paginacion">
      <span class="p-info">Página ${pagina} de ${paginas} · ${salida.total} entrada(s)</span>
      <div class="toolbar">
        <button class="btn btn-outline btn-sm" id="anterior" ${pagina <= 1 ? 'disabled' : ''}>← Anterior</button>
        <button class="btn btn-outline btn-sm" id="siguiente" ${pagina >= paginas ? 'disabled' : ''}>Siguiente →</button>
      </div>
    </div>`;
  $('#anterior').onclick = () => { filtro.desde = Math.max(0, filtro.desde - filtro.limite); cargar(); };
  $('#siguiente').onclick = () => { filtro.desde += filtro.limite; cargar(); };
  $$('[data-ficha]', contenedor).forEach((b) => {
    b.onclick = () => contexto.irA('panelistas', { idPersona: b.dataset.ficha });
  });
  $$('[data-historial]', contenedor).forEach((b) => {
    b.onclick = () => abrirHistorial(b.dataset.historial, b.dataset.nombre);
  });
  $$('[data-sacar]', contenedor).forEach((b) => {
    b.onclick = () => abrirSacar(b.dataset.sacar, cargar);
  });
}
