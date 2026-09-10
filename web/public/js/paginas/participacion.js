/* Tablero de participación (R2.1, R2.6).

   Tres preguntas, que son las tres que se le hacen a un panel:

     ¿Responde?                tasa de respuesta, global y por ola
     ¿Hace cuánto que no lo molesto?   tiempo desde el último contacto
     ¿Reparto parejo?          distribución de convocatorias

   La tercera es la que suele sorprender: si el decil más convocado se come
   la mayor parte de las convocatorias, la muestra no es del panel, es de ese
   pedacito del panel.
*/

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, alerta, token, fechaCorta, hace,
} from '../ui.js';

let contexto = {};
let panelActual = null;

const TRAMOS_CONTACTO = {
  nunca: 'Nunca contactados',
  hasta_30_dias: 'Hasta 30 días',
  '31_a_90_dias': '31 a 90 días',
  '91_a_180_dias': '91 a 180 días',
  mas_de_180_dias: 'Más de 180 días',
};

const TRAMOS_CONVOCATORIAS = {
  0: 'Ninguna', 1: 'Una', 2: 'Dos', 3: 'Tres', 4: 'Cuatro', '5_o_mas': 'Cinco o más',
};

const pct = (n) => (n == null ? '—' : `${(n * 100).toFixed(1)} %`);

export async function render(main, ctx) {
  contexto = ctx;
  const { items: paneles } = await api.paneles.listar();
  if (!paneles.length) {
    main.innerHTML = encabezado('Participación', 'del panel', '')
      + vacio('No hay paneles todavía.', '📋');
    return;
  }
  panelActual = paneles.some((p) => p.id === panelActual) ? panelActual : paneles[0].id;

  main.innerHTML = encabezado('Participación', 'y fatiga',
    'Quién responde, a quién hace que no se contacta y cómo se reparten las convocatorias.') + `
    <div class="card"><div class="card-body">
      <div class="form-group" style="margin:0; max-width:340px"><label>Panel</label>
        <select class="fselect" id="panel">${paneles.map((p) =>
          `<option value="${p.id}" ${p.id === panelActual ? 'selected' : ''}>${esc(p.nombre)}</option>`
        ).join('')}</select></div>
    </div></div>
    <div id="cuerpo">${cargando()}</div>`;

  $('#panel').onchange = (e) => { panelActual = Number(e.target.value); cargar(); };
  await cargar();
}

async function cargar() {
  const caja = $('#cuerpo');
  caja.innerHTML = cargando();
  try {
    caja.innerHTML = pintar(await api.participacion.tablero(panelActual));
    $$('[data-panelista]', caja).forEach((fila) => {
      fila.onclick = () => contexto.irA('panelistas', { idPersona: fila.dataset.panelista });
    });
  } catch (error) {
    caja.innerHTML = alerta(error.message);
  }
}

function pintar(t) {
  const r = t.respuesta;
  const concentracion = t.convocatorias.concentracion_decil_superior;
  return `
    <div class="stat-grid">
      <div class="stat s-total"><label>Miembros activos</label><strong>${t.miembros}</strong></div>
      <div class="stat ${r.tasa_respuesta != null && r.tasa_respuesta >= 0.4 ? 's-ok' : 's-warn'}">
        <label>Tasa de respuesta</label><strong>${pct(r.tasa_respuesta)}</strong></div>
      <div class="stat s-free"><label>Convocatorias emitidas</label>
        <strong>${r.convocatorias_emitidas}</strong></div>
      <div class="stat ${r.miembros_nunca_convocados ? 's-warn' : 's-ok'}">
        <label>Nunca convocados</label><strong>${r.miembros_nunca_convocados}</strong></div>
    </div>

    ${concentracion != null && concentracion > 0.4 ? `<div class="alert alert-warn">
      El 10 % más convocado del panel se lleva el <strong>${pct(concentracion)}</strong>
      de las convocatorias. Cuando esa cifra sube, la muestra deja de ser del
      panel y pasa a ser de ese grupo.
    </div>` : ''}

    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="card-header-title">Último contacto</span>
          <span class="small muted">mediana ${t.ultimo_contacto.mediana_dias ?? '—'} día(s)</span></div>
        <div class="card-body">${barras(
          t.ultimo_contacto.distribucion, TRAMOS_CONTACTO, t.miembros)}</div>
      </div>
      <div class="card">
        <div class="card-header"><span class="card-header-title">Convocatorias por miembro</span>
          <span class="small muted">promedio ${t.convocatorias.promedio_por_miembro ?? '—'}
            · máximo ${t.convocatorias.maximo}</span></div>
        <div class="card-body">${barras(
          t.convocatorias.distribucion, TRAMOS_CONVOCATORIAS, t.miembros)}</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><span class="card-header-title">Olas</span></div>
      <div class="card-body tight">
        ${t.olas.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Ola</th><th>Campo</th><th>Estado</th><th>Convocados</th>
            <th>Respondieron</th><th>Tasa</th><th>Calidad</th></tr></thead>
          <tbody>${t.olas.map((o) => `<tr>
            <td class="td-strong">${esc(o.nombre)}</td>
            <td class="small">${fechaCorta(o.fecha_campo)}</td>
            <td><span class="est est-${esc(o.estado)}">${esc(o.estado.replace('_', ' '))}</span></td>
            <td class="mono">${o.convocados}</td>
            <td class="mono">${o.respondieron}</td>
            <td><div class="barra ${o.tasa_respuesta >= 0.4 ? 'ok' : ''}">
                  <span style="width:${Math.round((o.tasa_respuesta || 0) * 100)}%"></span></div>
                <div class="small mono">${pct(o.tasa_respuesta)}</div></td>
            <td class="small">${o.calidad_ok} ok · ${o.calidad_sospechosa} sosp. ·
              ${o.calidad_pendiente} pend.</td>
          </tr>`).join('')}</tbody></table></div>`
          : vacio('Este panel todavía no tuvo olas.', '📨')}
      </div>
    </div>

    ${listaPersonas('Sin contacto hace más de 30 días', t.ultimo_contacto.mas_dormidos,
      'Son los que primero se pierden: cuando vuelve la convocatoria, ya no contestan.')}
    ${listaPersonas('Nunca contactados', t.ultimo_contacto.nunca_contactados,
      'Están en el panel y nunca se los convocó.')}
    ${listaPersonas('Más convocados', t.convocatorias.mas_convocados,
      'Los candidatos a fatiga: revisá antes de sumarlos a otra ola.')}`;
}

function barras(distribucion, etiquetas, total) {
  return `<div class="barras">${Object.entries(etiquetas).map(([clave, etiqueta]) => {
    const n = distribucion[clave] || 0;
    const proporcion = total ? n / total : 0;
    return `<div class="fila-barra">
      <span>${esc(etiqueta)}</span>
      <span class="barra"><span style="width:${Math.round(proporcion * 100)}%"></span></span>
      <span class="cifra">${n} · ${pct(proporcion)}</span>
    </div>`;
  }).join('')}</div>`;
}

function listaPersonas(titulo, personas, bajada) {
  if (!personas?.length) return '';
  return `<div class="card">
    <div class="card-header"><span class="card-header-title">${esc(titulo)}</span>
      <span class="small muted">${esc(bajada)}</span></div>
    <div class="card-body tight"><div class="table-wrap"><table>
      <thead><tr><th>Persona</th><th>Convocatorias</th><th>Respuestas</th>
        <th>Tasa</th><th>Último contacto</th></tr></thead>
      <tbody>${personas.map((p) => `<tr data-panelista="${esc(p.id_persona)}"
            style="cursor:pointer">
        <td><div class="td-strong">${esc(p.nombre || '—')}</div>
            <div class="td-muted small">${esc(p.localidad || '')}
              ${p.tramo_etario ? `· ${esc(p.tramo_etario)}` : ''}</div></td>
        <td class="mono">${p.convocatorias}</td>
        <td class="mono">${p.respuestas}</td>
        <td class="mono">${pct(p.tasa_respuesta)}</td>
        <td class="small">${p.ultimo_contacto ? hace(p.ultimo_contacto) : 'nunca'}</td>
      </tr>`).join('')}</tbody>
    </table></div></div>
  </div>`;
}
