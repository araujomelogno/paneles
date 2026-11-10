/* R-CAN (D88) — los canales aceptados de una carga, declarados en bloque.

   Lo usan dos pantallas: el modal de ingesta (al cargar, R-CAN.1) y el
   detalle de una carga ya hecha (R-CAN.2). Es el mismo componente a
   propósito: qué se declara y cómo se ve la vista previa no pueden diferir
   entre los dos caminos.

   Cada canal se declara con la versión del texto con que lo aceptaron,
   elegida de los textos publicados (R7.1): sin texto, no se registra. Y la
   vista previa dice cuántas personas quedan con cada canal y cuántas no, por
   motivo —sin celular válido, sin correo, revocó el canal—: quien revocó no
   se reactiva en bloque (R-CAN.3). */

import * as consentimiento from './consentimiento.js';
import { $, $$, esc } from './ui.js';

const CANALES = { whatsapp: 'WhatsApp', email: 'Correo' };
const FINALIDAD = 'contacto_participacion';

export const puedeDeclarar = (actor) => ['admin', 'operaciones'].includes(actor?.rol);

/* El bloque del formulario: una casilla y un desplegable de versión por
   canal. Los desplegables se llenan en `activar`, que carga los textos. */
export function bloque({ titulo = 'Canales aceptados' } = {}) {
  return `<div class="form-group" data-canales-aceptados>
      <label>${esc(titulo)}</label>
      <div class="field-hint" style="margin-bottom:.5rem">Si la gente de esta carga
        aceptó que se la contacte por WhatsApp o por correo, declaralo con el texto con
        que lo aceptó: sin eso nadie de la carga puede recibir mensajes. Quien revocó el
        canal no se reactiva.</div>
      ${Object.entries(CANALES).map(([canal, etiqueta]) => `
        <div class="fila-canal" style="margin-bottom:.4rem">
          <label class="check"><input type="checkbox" data-canal="${canal}" />
            ${esc(etiqueta)}</label>
          <div data-version-de="${canal}" hidden></div>
        </div>`).join('')}
    </div>`;
}

/* Carga los textos publicados y engancha las casillas. Devuelve `{ valor }`:
   `{canal: versión}` de los canales marcados (vacío si ninguno). */
export async function activar(caja) {
  await consentimiento.cargar();
  $$('[data-canal]', caja).forEach((casilla) => {
    const canal = casilla.dataset.canal;
    const destino = $(`[data-version-de="${canal}"]`, caja);
    destino.innerHTML = consentimiento.selector(FINALIDAD, {
      id: `canal-${canal}-version`, etiqueta: `Texto con que aceptaron ${CANALES[canal]}` });
    casilla.onchange = () => { destino.hidden = !casilla.checked; };
  });
  consentimiento.activarVerTexto(caja);
  return {
    valor() {
      const salida = {};
      $$('[data-canal]', caja).forEach((casilla) => {
        if (!casilla.checked) return;
        salida[casilla.dataset.canal] = $(`#canal-${casilla.dataset.canal}-version`, caja)?.value
          || '';
      });
      return salida;
    },
  };
}

/* La vista previa: por canal, cuántos quedan con él y cuántos no, por
   motivo. Es la misma para la revisión de la ingesta y para el registro en
   bloque, y la misma tabla vale para el resultado. */
export function previaHtml(salida, { resultado = false } = {}) {
  const canales = Object.entries(salida?.canales || {});
  if (!canales.length) return '';
  const motivos = salida.motivos || {};
  return `<div class="table-wrap" data-previa-canales><table>
      <thead><tr><th>Canal</th><th>Texto</th><th class="num">${resultado
        ? 'Registrados' : 'Quedan con el canal'}</th><th class="num">Ya lo tenían</th>
        <th>No quedan, por motivo</th></tr></thead>
      <tbody>${canales.map(([canal, c]) => `<tr data-canal-previa="${esc(canal)}">
        <td class="td-strong">${esc(c.etiqueta || CANALES[canal] || canal)}</td>
        <td class="mono small">${esc(c.version_texto || '')}</td>
        <td class="num" data-quedaran>${resultado ? c.registrados : c.quedaran}</td>
        <td class="num">${c.ya_lo_tenian ?? 0}</td>
        <td class="small">${Object.entries(c.no || {}).filter(([, n]) => n).map(([m, n]) =>
          `<div data-motivo="${esc(m)}">${n} — ${esc(motivos[m] || m)}</div>`).join('')
          || '<span class="muted">—</span>'}</td>
      </tr>`).join('')}</tbody></table></div>`;
}

/* El historial de una carga: quién, cuándo, qué canales, con qué texto y a
   cuántas personas. */
export function historialHtml(historial, fechaHora) {
  if (!historial?.length) {
    return '<p class="small muted" style="padding:1rem 1.5rem" id="canales-sin-registro">'
      + 'Todavía no se registró ningún canal para la gente de esta carga.</p>';
  }
  return `<div class="table-wrap"><table id="tabla-canales-carga">
      <thead><tr><th>Cuándo</th><th>Quién</th><th>Cómo</th><th>Canales y texto</th>
        <th class="num">Personas</th></tr></thead>
      <tbody>${historial.map((h) => `<tr>
        <td class="small">${esc(fechaHora(h.creado_en))}</td>
        <td class="small">${esc(h.actor)}</td>
        <td class="small">${h.via === 'ingesta' ? 'Al cargar' : 'En bloque'}</td>
        <td class="small">${Object.entries(h.canales || {}).map(([canal, version]) =>
          `<div>${esc(CANALES[canal] || canal)} · <span class="mono">${esc(version)}</span>
            <span class="muted">(${h.resultado?.[canal]?.registrados ?? 0} registrados)</span></div>`)
          .join('')}</td>
        <td class="num">${h.personas}</td></tr>`).join('')}</tbody></table></div>`;
}
