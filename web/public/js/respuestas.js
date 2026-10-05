/* R7.6 — las respuestas procesadas de un panelista.

   Una sola implementación para los dos lugares donde aparece: la ficha
   emergente que se abre desde un resultado de consulta y la ficha completa
   de la sección Panelistas. Dos copias de la misma tabla terminan
   divergiendo, y lo que divergiría es justamente el aviso de que ver esto
   queda registrado.

   ── Por qué se carga a pedido ──

   Ver qué respondió una persona **une identidad y contenido**, que es lo
   que los dos stores mantienen separado, y por eso el servidor lo registra
   con su motivo propio (`respuestas_panelista`). Si la tabla se cargara sola
   al abrir la ficha, cada vez que alguien la abriera para mirar otra cosa
   quedaría una fila de auditoría diciendo que vio las respuestas. El
   registro dejaría de significar «miró el contenido» y pasaría a significar
   «abrió una ficha». Así que lo que se muestra de entrada es solo el conteo
   por estudio —que no se registra: saber que respondió no es ver qué— y la
   tabla aparece cuando alguien la pide.

   ── Por qué el texto embebido ──

   Es la única forma de ver que una cerrada quedó sin traducir: el texto
   dice «¿Qué marca fumás? → 11427» en vez de «→ Nevada». Esa respuesta está
   en la base, cuenta como respuesta y es inútil para la búsqueda. */

import * as api from './api.js';
import { $, esc, cargando, alerta, fechaCorta } from './ui.js';

const TAMANO_PAGINA = 25;

/* El bloque de la sección. `estudios` es lo que devuelve
   `/panelistas/<id>/respuestas/estudios`, o `null` si no se pudo leer: un
   error no puede mostrarse como «no tiene respuestas», que es otra cosa. */
export function seccionHtml(estudios, { prefijo = 'resp' } = {}) {
  if (estudios === null) {
    return alerta('No se pudo consultar el store semántico. Las respuestas '
      + 'existen o no independientemente de este error: probá de nuevo.', 'warn');
  }
  if (!estudios.length) {
    return `<p class="small muted">Todavía no tiene respuestas procesadas: no
      va a aparecer en ninguna consulta por concepto.</p>`;
  }
  const total = estudios.reduce((n, e) => n + (e.respuestas || 0), 0);
  return `
    <p class="small">${total} respuesta(s) en ${estudios.length} estudio(s):
      ${estudios.map((e) => `${esc(e.estudio)} (${e.respuestas})`).join(' · ')}</p>
    <div id="${prefijo}-pedir">
      <button class="btn btn-outline btn-sm" id="${prefijo}-ver">Ver las respuestas</button>
      <div class="field-hint">Queda registrado con tu usuario y el motivo
        «respuestas del panelista»: es ver qué opinó una persona identificada.</div>
    </div>
    <div id="${prefijo}-tabla" hidden>
      <div class="toolbar barra-filtros">
        <select class="fselect" id="${prefijo}-estudio">
          <option value="">Todos los estudios</option>
          ${estudios.map((e) => `<option value="${esc(e.ref_estudio)}">
            ${esc(e.estudio)} (${e.respuestas})</option>`).join('')}
        </select>
        <input class="finput" id="${prefijo}-buscar"
               placeholder="Buscar en pregunta o respuesta" />
        <label class="check"><input type="checkbox" id="${prefijo}-embebido" />
          Ver el texto embebido</label>
      </div>
      <div id="${prefijo}-filas"></div>
    </div>`;
}

/* Engancha la sección pintada por `seccionHtml`. La consulta pagina en la
   base: cada página es un pedido, y nunca se trae todo para recortar acá. */
export function activar(caja, idPersona, estudios, { prefijo = 'resp' } = {}) {
  if (!estudios || !estudios.length) return;
  let pagina = 1;
  const nodo = (sufijo) => $(`#${prefijo}-${sufijo}`, caja);

  const pintar = async () => {
    const destino = nodo('filas');
    if (!destino) return;
    destino.innerHTML = cargando('12vh');
    try {
      const d = await api.panelistas.respuestas(idPersona, {
        ref_estudio: nodo('estudio').value || undefined,
        q: nodo('buscar').value.trim() || undefined,
        pagina,
        tamano: TAMANO_PAGINA,
      });
      const conEmbebido = nodo('embebido').checked;
      destino.innerHTML = d.items.length ? `
        <div class="table-wrap"><table class="tabla">
          <thead><tr><th>Código</th><th>Pregunta</th><th>Respuesta</th>
                     <th>Procedencia</th></tr></thead>
          <tbody>${d.items.map((f) => `
            <tr><td><code>${esc(f.codigo)}</code></td>
                <td>${esc(f.pregunta)}</td>
                <td>${esc(f.respuesta || '—')}
                    ${conEmbebido ? `<div class="small muted mono embebido">${esc(f.texto_embebido)}</div>` : ''}</td>
                <td class="small">${esc(f.estudio)}<br>
                    <span class="muted">${f.fecha_campo ? esc(fechaCorta(f.fecha_campo)) : 'sin fecha de campo'}</span></td>
            </tr>`).join('')}</tbody>
        </table></div>
        <div class="toolbar paginado">
          <button class="btn btn-outline btn-sm" data-pagina="-1"
                  ${pagina <= 1 ? 'disabled' : ''}>Anterior</button>
          <span class="small">Página ${d.pagina} de ${d.paginas} · ${d.total} respuesta(s)</span>
          <button class="btn btn-outline btn-sm" data-pagina="1"
                  ${pagina >= d.paginas ? 'disabled' : ''}>Siguiente</button>
        </div>`
        // Con filtro y sin coincidencias no es lo mismo que no tener nada:
        // el mensaje lo dice para que nadie concluya que la persona está
        // vacía porque buscó mal.
        : '<p class="small muted">Ninguna respuesta coincide con ese filtro.</p>';
      destino.querySelectorAll('[data-pagina]').forEach((boton) => {
        boton.onclick = () => { pagina += Number(boton.dataset.pagina); pintar(); };
      });
    } catch (error) {
      destino.innerHTML = alerta(error.message);
    }
  };

  nodo('ver').onclick = () => {
    nodo('pedir').hidden = true;
    nodo('tabla').hidden = false;
    pintar();
  };
  nodo('estudio').onchange = () => { pagina = 1; pintar(); };
  nodo('embebido').onchange = () => pintar();
  let reloj = null;
  nodo('buscar').oninput = () => {
    clearTimeout(reloj);
    reloj = setTimeout(() => { pagina = 1; pintar(); }, 300);
  };
}

/* Lee los estudios con respuestas sin confundir un error con un «no tiene». */
export async function estudiosDe(idPersona) {
  try {
    return (await api.panelistas.estudiosConRespuestas(idPersona)).items || [];
  } catch {
    return null;
  }
}
