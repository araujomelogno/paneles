/* Revisión de altas — el caso de dedup que el sistema NO resuelve solo.

   Cuando un alta coincide por nombre y fecha de nacimiento pero no trae
   documento ni email, no se fusiona: homónimos con la misma fecha existen,
   y fusionar mal es peor que preguntar. La decisión la toma una persona. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, toast, modal, cerrarModal,
  activarTokens, fechaCorta, fechaHora, estado, token,
} from '../ui.js';

let contexto = {};
let filtroEstado = 'pendiente';

export async function render(main, ctx) {
  contexto = ctx;
  main.innerHTML = encabezado('Revisión', 'de altas',
    'Altas con coincidencia ambigua, esperando decisión humana.') + `
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">Altas en revisión</span>
        <select class="fselect" id="f-estado" style="width:auto">
          <option value="pendiente">Pendientes</option>
          <option value="fusionada">Fusionadas</option>
          <option value="creada">Creadas como persona nueva</option>
          <option value="descartada">Descartadas</option>
        </select>
      </div>
      <div class="card-body tight"><div id="lista">${cargando('20vh')}</div></div>
    </div>`;

  $('#f-estado').value = filtroEstado;
  $('#f-estado').onchange = (e) => { filtroEstado = e.target.value; cargar(); };
  await cargar();
}

async function cargar() {
  const contenedor = $('#lista');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('20vh');
  const { items } = await api.revisiones.listar(filtroEstado);

  if (!items.length) {
    contenedor.innerHTML = vacio(
      filtroEstado === 'pendiente'
        ? 'No hay altas esperando decisión. El dedup viene resolviendo solo.'
        : 'Nada en este estado.',
      filtroEstado === 'pendiente' ? '✅' : '📭');
    return;
  }

  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Alta que llegó</th><th>Coincide con</th><th>Motivo</th>
        <th>Estado</th><th>Recibida</th><th></th></tr></thead>
      <tbody>${items.map((r) => {
        const p = r.datos?.persona || {};
        return `<tr>
          <td><div class="td-strong">${esc(p.nombre || 'Sin nombre')}</div>
            <div class="td-muted small">${esc([
              p.fecha_nacimiento ? fechaCorta(p.fecha_nacimiento) : null,
              p.sexo, p.localidad,
            ].filter(Boolean).join(' · ') || '—')}</div></td>
          <td>${(r.candidatos || []).map((c) => `
            <div class="small"><span class="td-strong">${esc(c.nombre || '—')}</span>
            <span class="muted"> · ${esc(c.localidad || '—')}</span></div>`).join('') || '—'}</td>
          <td class="small mono">${esc(r.motivo)}</td>
          <td>${estado(r.estado)}</td>
          <td class="small">${fechaCorta(r.creado_en)}</td>
          <td class="right">${r.estado === 'pendiente'
            ? `<button class="btn btn-orange btn-sm" data-resolver="${r.id}">Resolver</button>`
            : (r.id_persona ? token(r.id_persona) : '')}</td>
        </tr>`;
      }).join('')}</tbody></table></div>`;

  activarTokens(contenedor);
  $$('[data-resolver]', contenedor).forEach((boton) => {
    const revision = items.find((r) => String(r.id) === boton.dataset.resolver);
    boton.onclick = () => abrirResolucion(revision);
  });
}

function abrirResolucion(revision) {
  const p = revision.datos?.persona || {};
  const dato = (etiqueta, valor) =>
    `<dt>${esc(etiqueta)}</dt><dd class="${valor ? '' : 'vacio'}">${esc(valor || '—')}</dd>`;

  const caja = modal({
    titulo: 'Resolver alta en revisión',
    ancho: '640px',
    cuerpo: `
      <div class="aviso">
        <h4>Por qué está acá</h4>
        <p>El alta coincide por <strong>nombre y fecha de nacimiento</strong> con alguien ya
        enrolado, pero no trae documento ni email. El sistema no fusiona por parecido.</p>
      </div>

      <div class="card" style="margin-top:1.25rem">
        <div class="card-header"><span class="card-header-title">El alta que llegó</span></div>
        <div class="card-body"><dl class="kv">
          ${dato('Nombre', p.nombre)}
          ${dato('Fecha de nacimiento', p.fecha_nacimiento ? fechaCorta(p.fecha_nacimiento) : null)}
          ${dato('Sexo', p.sexo)}
          ${dato('Localidad', p.localidad)}
          ${dato('Email', p.email)}
          ${dato('Documento', p.documento)}
          ${dato('Recibida', fechaHora(revision.creado_en))}
        </dl></div>
      </div>

      <div class="form-group" style="margin-top:1.25rem">
        <label>Ya enrolados que coinciden</label>
        <div class="pick-list">
          ${(revision.candidatos || []).map((c, i) => `
            <label class="pick">
              <input type="radio" name="candidato" value="${esc(c.id_persona)}" ${i === 0 ? 'checked' : ''} />
              <div class="pick-avatar">${esc((c.nombre || '?').slice(0, 2).toUpperCase())}</div>
              <div style="flex:1">
                <div class="pick-name">${esc(c.nombre || 'Sin nombre')}</div>
                <div class="pick-email">${esc(c.localidad || '—')}</div>
              </div>
              <code class="token">${esc(String(c.id_persona).slice(0, 8))}…</code>
            </label>`).join('')}
        </div>
      </div>

      <div class="aviso" style="margin-top:1rem">
        <p><strong>Es la misma persona</strong> → se fusiona: el alta se suma al
        <code>id_persona</code> que ya existe y se completan los datos que faltaban.</p>
        <p><strong>Es otra persona</strong> → se crea una persona nueva con su propio
        <code>id_persona</code>.</p>
      </div>`,
    acciones: [
      { texto: 'Descartar alta', clase: 'btn-outline btn-del',
        onClick: () => resolver(revision.id, 'descartar') },
      { texto: 'Es otra persona', clase: 'btn-dark',
        onClick: () => resolver(revision.id, 'crear') },
      { texto: 'Es la misma', clase: 'btn-orange',
        onClick: (c) => {
          const elegido = $('input[name="candidato"]:checked', c);
          if (!elegido) { toast('Elegí con quién fusionar.', 'err'); return; }
          resolver(revision.id, 'fusionar', elegido.value);
        } },
    ],
  });
  return caja;
}

async function resolver(revisionId, decision, idPersona) {
  try {
    const resultado = await api.revisiones.resolver(revisionId, decision, idPersona);
    cerrarModal();
    const mensajes = {
      fusionada: 'Alta fusionada con la persona existente.',
      creada: 'Se creó una persona nueva.',
      descartada: 'Alta descartada.',
    };
    toast(mensajes[resultado.estado] || 'Resuelto.', 'ok');
    await cargar();
  } catch (error) {
    toast(error.message, 'err');
  }
}
