/* R-LN — La lista negra, en las pantallas que la tocan.

   Se usa en tres lugares con el mismo comportamiento: la ficha de un
   panelista (sección Panelistas), la ficha que se abre desde un resultado
   de consulta y la sección «Lista negra». Por eso vive acá y no en cada
   página: el modal que pide el motivo es uno solo.

   Lo que **no** decide esta pantalla:
   * quién se convoca: lo decide la base (`v_persona_convocable`);
   * quién ve el motivo: el servidor lo manda solo a quien tiene permiso, y
     a los demás les llega la marca (`{ en_lista_negra: true }`);
   * las categorías: vienen del servidor con el listado.

   El motivo lo van a leer otras personas del equipo y es un dato sobre
   alguien identificado: el modal pide **hechos**, no juicios. */

import * as api from './api.js';
import {
  $, esc, modal, cerrarModal, toast, alerta, leerFormulario, fechaHora, vacio,
} from './ui.js';

const GESTIONAN = ['admin', 'operaciones'];
const VEN = ['admin', 'operaciones', 'dpo'];
export const puedeGestionar = (actor) => GESTIONAN.includes(actor?.rol);
export const puedeVer = (actor) => VEN.includes(actor?.rol);

/* La etiqueta de una fila de resultados o de la grilla. Con el motivo (si
   llegó) en el `title`: no se oculta a nadie, se marca. */
export function marca(entrada) {
  if (!entrada) return '';
  const titulo = entrada.detalle
    ? `${entrada.categoria_etiqueta}: ${entrada.detalle}`
    : 'Está en la lista negra: no se la convoca.';
  return `<span class="badge badge-lista-negra" title="${esc(titulo)}">lista negra</span>`;
}

let categoriasCache = null;
async function categorias() {
  if (!categoriasCache) {
    const { categorias: lista } = await api.listaNegra.listar({ limite: 1 });
    categoriasCache = lista;
  }
  return categoriasCache;
}

export async function abrirAgregar(idPersona, alListo) {
  const lista = await categorias();
  modal({
    titulo: 'Agregar a la lista negra',
    ancho: '560px',
    cuerpo: `
      <div id="ln-alerta"></div>
      <div class="aviso destacado">
        <h4>Qué cambia</h4>
        <p>No se la convoca a ninguna encuesta ni desde COLOQUIO, y sus
        invitaciones pendientes se cancelan. Sigue apareciendo en los resultados
        de consulta, marcada. <strong>No se le avisa</strong> ni lo ve en su portal.</p>
      </div>
      <div class="form-group"><label>Categoría</label>
        <select class="fselect" name="categoria">
          <option value="">— Elegir —</option>
          ${lista.map((c) => `<option value="${esc(c.clave)}">${esc(c.etiqueta)}</option>`).join('')}
        </select></div>
      <div class="form-group"><label>Qué pasó</label>
        <textarea class="finput" name="detalle" rows="3" maxlength="1000"
          placeholder="Respondió en 40 segundos un cuestionario de 15 minutos."></textarea>
        <div class="field-hint">Describí <strong>hechos</strong>, no juicios sobre la
          persona: lo van a leer otras personas del equipo y, si lo pide, ella
          misma (es parte de su derecho de acceso).</div></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Agregar a la lista negra', clase: 'btn-danger', onClick: async (caja) => {
          const datos = leerFormulario(caja);
          try {
            const entrada = await api.listaNegra.agregar(idPersona, datos.categoria, datos.detalle);
            cerrarModal();
            const n = entrada.invitaciones_canceladas?.length || 0;
            toast(`Agregada a la lista negra.${n ? ` Se cancelaron ${n} invitación(es) pendiente(s).` : ''}`, 'ok');
            alListo?.(entrada);
          } catch (error) {
            $('#ln-alerta', caja).innerHTML = alerta(error.message);
          }
        } },
    ],
  });
}

export function abrirSacar(idPersona, alListo) {
  modal({
    titulo: 'Sacar de la lista negra',
    ancho: '520px',
    cuerpo: `
      <div id="ln-alerta"></div>
      <p class="small muted">Vuelve a poder convocarse. La entrada no se borra:
        queda en el historial quién la sacó, cuándo y por qué.</p>
      <div class="form-group"><label>Motivo</label>
        <textarea class="finput" name="motivo" rows="2" maxlength="1000"
          placeholder="Se revisó el caso con campo: el tiempo era un error de registro."></textarea></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Sacar de la lista negra', clase: 'btn-orange', onClick: async (caja) => {
          try {
            const entrada = await api.listaNegra.sacar(idPersona, leerFormulario(caja).motivo);
            cerrarModal();
            toast('Sacada de la lista negra. La entrada queda en el historial.', 'ok');
            alListo?.(entrada);
          } catch (error) {
            $('#ln-alerta', caja).innerHTML = alerta(error.message);
          }
        } },
    ],
  });
}

/* El historial completo: activas y removidas, la más reciente primero. */
export function historialHtml(items = []) {
  if (!items.length) return vacio('Nunca estuvo en la lista negra.', '✅');
  return `<div class="table-wrap"><table>
    <thead><tr><th>Estado</th><th>Motivo</th><th>Agregada</th><th>Sacada</th></tr></thead>
    <tbody>${items.map((e) => `<tr>
      <td>${e.activa ? '<span class="badge badge-lista-negra">activa</span>'
        : '<span class="badge">removida</span>'}</td>
      <td><strong>${esc(e.categoria_etiqueta)}</strong><div class="small">${esc(e.detalle)}</div>
        ${e.invitaciones_canceladas?.length ? `<div class="small muted">Se cancelaron
          ${e.invitaciones_canceladas.length} invitación(es):
          ${esc(e.invitaciones_canceladas.map((i) => i.encuesta).join(', '))}</div>` : ''}</td>
      <td class="small">${esc(fechaHora(e.agregado_en))}<div class="muted">${esc(e.agregado_por || '—')}</div></td>
      <td class="small">${e.removido_en ? `${esc(fechaHora(e.removido_en))}
        <div class="muted">${esc(e.removido_por || '—')}</div>
        <div>${esc(e.motivo_remocion)}</div>` : '—'}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

export async function abrirHistorial(idPersona, nombre) {
  const { items } = await api.listaNegra.historial(idPersona);
  modal({
    titulo: `Lista negra · ${nombre || 'historial'}`,
    ancho: '760px',
    cuerpo: historialHtml(items),
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
}

/* El bloque de la ficha: el estado, el motivo (si se puede ver) y las
   acciones. `alCambiar` vuelve a pintar la ficha. */
export function bloqueDeFicha({ idPersona, nombre, entrada, actor, alCambiar }) {
  const gestiona = puedeGestionar(actor);
  const ve = puedeVer(actor);
  if (!entrada && !gestiona) return '';
  const id = `ln-${idPersona}`;
  setTimeout(() => {
    const caja = document.getElementById(id);
    if (!caja) return;
    $('[data-ln-agregar]', caja)?.addEventListener('click', () => abrirAgregar(idPersona, alCambiar));
    $('[data-ln-sacar]', caja)?.addEventListener('click', () => abrirSacar(idPersona, alCambiar));
    $('[data-ln-historial]', caja)?.addEventListener('click', () => abrirHistorial(idPersona, nombre));
  });
  return `<div class="bloque-lista-negra ${entrada ? 'activa' : ''}" id="${id}">
    ${entrada ? `<div><span class="badge badge-lista-negra">En la lista negra</span>
        ${entrada.detalle ? `<strong>${esc(entrada.categoria_etiqueta)}</strong> —
          ${esc(entrada.detalle)}
          <div class="small muted">Agregada ${esc(fechaHora(entrada.agregado_en))}
            por ${esc(entrada.agregado_por || '—')}. No se la convoca.</div>`
          : '<span class="small muted">No se la convoca.</span>'}</div>`
      : '<span class="small muted">No está en la lista negra.</span>'}
    <div class="toolbar">
      ${gestiona && !entrada ? '<button class="btn btn-outline btn-sm texto-peligro" data-ln-agregar>Agregar a la lista negra…</button>' : ''}
      ${gestiona && entrada ? '<button class="btn btn-outline btn-sm" data-ln-sacar>Sacar de la lista negra…</button>' : ''}
      ${ve ? '<button class="btn btn-outline btn-sm" data-ln-historial>Historial</button>' : ''}
    </div>
  </div>`;
}
