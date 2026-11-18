/* R-WA.4 (D98) — Sección «WhatsApp»: la bandeja.

   Lo que la gente contesta por WhatsApp —respuestas de Flow y comentarios—,
   de las encuestas de paneles y de las de afuera, en lugar de la planilla.
   Una tabla por encuesta o plantilla, con una columna por campo de la
   respuesta (las de pantalla en orden), filtros por fecha y exportación a
   CSV.

   Tiene teléfonos, también de gente que no es panelista: la ven
   administración y operaciones (`bandeja_whatsapp`) y cada exportación queda
   registrada. Lo de afuera se borra solo al cumplirse el plazo (R-WA.5).

   Ingestar las respuestas de una encuesta no se hace acá sino desde su
   ficha («Obtener respuestas de WhatsApp», R-WA.6): sigue el proceso de un
   SAV. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, alerta, toast, fechaHora, hace,
} from '../ui.js';

const filtro = { clave: '', desde: '', hasta: '', solapa: 'flow' };
let grupos = [];

export async function render(main) {
  main.innerHTML = encabezado('Bandeja de', 'WhatsApp',
    'Las respuestas de los Flows y los comentarios que llegan por WhatsApp, de las '
    + 'encuestas de paneles y de las de afuera.') + `
    <div id="wa-estado"></div>
    <div class="card">
      <div class="card-header">
        <div class="toolbar" style="flex:1;flex-wrap:wrap">
          <select class="fselect" id="wa-grupo" style="min-width:16rem"></select>
          <label class="small">Desde <input type="date" class="finput" id="wa-desde"
            value="${esc(filtro.desde)}" /></label>
          <label class="small">Hasta <input type="date" class="finput" id="wa-hasta"
            value="${esc(filtro.hasta)}" /></label>
        </div>
        <button class="btn btn-outline btn-sm" id="wa-exportar">Exportar CSV</button>
      </div>
      <div class="tabs" id="wa-solapas">
        <button class="tab ${filtro.solapa === 'flow' ? 'active' : ''}" data-solapa="flow">Respuestas</button>
        <button class="tab ${filtro.solapa === 'comentario' ? 'active' : ''}"
                data-solapa="comentario">Comentarios</button>
      </div>
      <div class="card-body tight"><div id="wa-tabla">${cargando('20vh')}</div></div>
    </div>
    <p class="small muted" id="wa-conservacion"></p>`;

  $('#wa-grupo').onchange = (e) => { filtro.clave = e.target.value; cargarTabla(); };
  $('#wa-desde').onchange = (e) => { filtro.desde = e.target.value; cargarTabla(); };
  $('#wa-hasta').onchange = (e) => { filtro.hasta = e.target.value; cargarTabla(); };
  $$('#wa-solapas .tab').forEach((boton) => {
    boton.onclick = () => {
      filtro.solapa = boton.dataset.solapa;
      $$('#wa-solapas .tab').forEach((b) => b.classList.toggle('active', b === boton));
      cargarTabla();
    };
  });
  $('#wa-exportar').onclick = exportar;
  await cargarGrupos();
}

function consulta() {
  const [tipo, ...resto] = (filtro.clave || '').split(':');
  const valor = resto.join(':');
  return {
    ...(tipo === 'encuesta' ? { encuesta_id: valor } : {}),
    ...(tipo === 'grupo' ? { grupo: valor } : {}),
    ...(filtro.desde ? { desde: filtro.desde } : {}),
    ...(filtro.hasta ? { hasta: filtro.hasta } : {}),
  };
}

async function cargarGrupos() {
  let salida;
  try {
    salida = await api.bandejaWhatsapp.grupos();
  } catch (error) {
    $('#wa-tabla').innerHTML = alerta(error.message);
    return;
  }
  grupos = salida.items || [];
  if (filtro.clave && !grupos.some((g) => g.clave === filtro.clave)) filtro.clave = '';
  if (!filtro.clave && grupos.length) filtro.clave = grupos[0].clave;
  $('#wa-grupo').innerHTML = grupos.length
    ? grupos.map((g) => `<option value="${esc(g.clave)}" ${g.clave === filtro.clave ? 'selected' : ''}>
        ${g.tipo === 'encuesta' ? 'Encuesta' : 'Plantilla'}: ${esc(g.nombre)}
        · ${g.respuestas} resp. · ${g.comentarios} coment.</option>`).join('')
    : '<option value="">— todavía no llegó nada —</option>';

  const avisos = salida.avisos || {};
  $('#wa-estado').innerHTML = [
    salida.configurado ? '' : alerta('El webhook no está configurado: faltan '
      + '`WHATSAPP_VERIFY_TOKEN` o `WHATSAPP_APP_SECRET` en Secret Manager. Sin ellos '
      + 'no llega nada.', 'warn'),
    avisos.fallidos ? `<div class="alert alert-warn" id="wa-fallidos">${avisos.fallidos}
      aviso(s) de Meta no se pudieron procesar. Están guardados tal como llegaron:
      no se perdió nada. <button class="btn btn-outline btn-sm" id="wa-reprocesar"
      >Reprocesar</button></div>` : '',
    `<p class="small muted">Último aviso: ${avisos.ultimo ? esc(hace(avisos.ultimo)) : 'ninguno'}${
      avisos.pendientes ? ` · ${avisos.pendientes} pendiente(s)` : ''}</p>`,
  ].join('');
  $('#wa-reprocesar')?.addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    try {
      const r = await api.bandejaWhatsapp.reprocesar();
      toast(`${r.procesados} aviso(s) procesado(s)${r.fallidos ? `, ${r.fallidos} siguen fallando` : ''}.`,
        r.fallidos ? 'err' : 'ok');
      await cargarGrupos();
    } catch (error) {
      toast(error.message, 'err');
    }
  });
  const c = salida.conservacion || {};
  $('#wa-conservacion').textContent = `Las respuestas de encuestas de afuera se borran a los `
    + `${c.afuera_dias} días; el aviso tal como llegó de Meta, a los ${c.aviso_dias}. La baja `
    + 'de un panelista borra lo suyo, también lo que mandó desde su número.';
  await cargarTabla();
}

async function cargarTabla() {
  const contenedor = $('#wa-tabla');
  if (!contenedor) return;
  if (!grupos.length) {
    contenedor.innerHTML = vacio('Todavía no llegó ninguna respuesta ni comentario por WhatsApp.');
    return;
  }
  contenedor.innerHTML = cargando('20vh');
  try {
    contenedor.innerHTML = filtro.solapa === 'flow'
      ? tablaDeRespuestas(await api.bandejaWhatsapp.respuestas(consulta()))
      : tablaDeComentarios(await api.bandejaWhatsapp.comentarios(consulta()));
  } catch (error) {
    contenedor.innerHTML = alerta(error.message);
  }
}

function tablaDeRespuestas(datos) {
  if (!datos.filas.length) return vacio('No hay respuestas con este filtro.');
  return `<div class="table-wrap"><table id="wa-respuestas">
    <thead><tr><th>Fecha</th><th>Teléfono</th><th>Encuesta o plantilla</th><th>Token</th>
      ${datos.columnas.map((c) => `<th><code>${esc(c)}</code></th>`).join('')}</tr></thead>
    <tbody>${datos.filas.map((f) => `<tr>
      <td class="small">${esc(fechaHora(f.recibido_en))}</td>
      <td class="small">${esc(f.telefono)}</td>
      <td class="small">${esc(f.origen || '')}${f.mensaje ? ` · ${esc(f.mensaje)}` : ''}
        ${f.inconsistencia ? `<span class="badge badge-off" title="${esc(f.inconsistencia)}"
          >inconsistencia</span>` : ''}</td>
      <td class="small"><code>${esc(f.token || '')}</code></td>
      ${datos.columnas.map((c) => `<td>${esc(f.campos[c])}</td>`).join('')}</tr>`).join('')}
    </tbody></table></div>
    ${datos.recortado ? '<p class="small muted">Se muestran las más recientes: exportá para '
      + 'tenerlas todas.</p>' : ''}`;
}

function tablaDeComentarios(datos) {
  if (!datos.filas.length) return vacio('No hay comentarios con este filtro.');
  return `<div class="table-wrap"><table id="wa-comentarios">
    <thead><tr><th>Fecha</th><th>Teléfono</th><th>Encuesta o plantilla</th><th>Contesta a</th>
      <th>Comentario</th></tr></thead>
    <tbody>${datos.filas.map((f) => `<tr>
      <td class="small">${esc(fechaHora(f.recibido_en))}</td>
      <td class="small">${esc(f.telefono)}</td>
      <td class="small">${esc(f.origen || '')}</td>
      <td class="small">${esc(f.mensaje || '—')}</td>
      <td>${esc(f.texto || '')}</td></tr>`).join('')}</tbody></table></div>`;
}

async function exportar(e) {
  const boton = e.currentTarget;
  boton.disabled = true;
  try {
    const salida = await api.bandejaWhatsapp.exportar({ ...consulta(), tipo: filtro.solapa });
    const blob = new Blob([salida.csv], { type: 'text/csv;charset=utf-8' });
    const enlace = document.createElement('a');
    enlace.href = URL.createObjectURL(blob);
    enlace.download = salida.nombre;
    enlace.click();
    URL.revokeObjectURL(enlace.href);
    toast(`Exportadas ${salida.filas} fila(s). La exportación quedó registrada.`, 'ok');
  } catch (error) {
    toast(error.message, 'err');
  } finally {
    boton.disabled = false;
  }
}
