/* Inscripciones de la landing y textos de consentimiento (R3.7).

   Dos cosas conviven acá porque son dos mitades de lo mismo: sin un texto
   publicado la landing no puede recibir a nadie, y cada inscripción queda
   atada a la versión que esa persona leyó.

   La bandeja muestra qué dijo la resolución de identidad —si la persona ya
   existe, si el caso es ambiguo— porque quien aprueba necesita saberlo. Eso
   es adentro; la landing, afuera, no dice nada de eso.
*/

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, alerta, token, toast, modal,
  cerrarModal, leerFormulario, fechaHora,
} from '../ui.js';

let contexto = {};
let solapa = 'pendientes';

const RESOLUCION = {
  crea: ['nueva', 'No coincide con nadie: se va a crear.'],
  reutiliza: ['ya existe', 'Coincide con alguien del panel: se reutiliza su id_persona.'],
  revision: ['ambigua', 'Coincidencia ambigua: al aprobar va a la cola de revisión.'],
};

export async function render(main, ctx) {
  contexto = ctx;
  main.innerHTML = encabezado('Inscripciones', 'de la landing',
    'Quién se inscribió por el formulario público y con qué versión del consentimiento.') + `
    <div class="tabs" id="solapas">
      <button class="tab ${solapa === 'pendientes' ? 'active' : ''}" data-solapa="pendientes">Pendientes</button>
      <button class="tab ${solapa === 'resueltas' ? 'active' : ''}" data-solapa="resueltas">Resueltas</button>
      <button class="tab ${solapa === 'textos' ? 'active' : ''}" data-solapa="textos">Textos de consentimiento</button>
    </div>
    <div id="cuerpo">${cargando('20vh')}</div>`;

  $$('#solapas .tab').forEach((boton) => {
    boton.onclick = () => {
      solapa = boton.dataset.solapa;
      $$('#solapas .tab').forEach((b) => b.classList.toggle('active', b.dataset.solapa === solapa));
      cargar();
    };
  });
  await cargar();
}

async function cargar() {
  const cuerpo = $('#cuerpo');
  cuerpo.innerHTML = cargando('20vh');
  try {
    if (solapa === 'textos') return pintarTextos(cuerpo);
    return pintarInscripciones(cuerpo, solapa === 'pendientes' ? 'pendiente' : 'aprobada');
  } catch (error) {
    cuerpo.innerHTML = alerta(error.message);
  }
}

async function pintarInscripciones(cuerpo, estadoPedido) {
  const [{ items }, { items: paneles }, { items: textos }] = await Promise.all([
    api.inscripciones.listar(estadoPedido),
    api.paneles.listar(),
    api.inscripciones.textos('contacto_participacion'),
  ]);

  const sinTexto = !textos.some((t) => t.activo);
  cuerpo.innerHTML = `
    ${sinTexto ? alerta('No hay ningún texto de consentimiento publicado, así que la landing no puede recibir inscripciones. Publicá uno en la solapa «Textos de consentimiento».', 'warn') : ''}
    <div class="card"><div class="card-head">
      <h3>${items.length} ${estadoPedido === 'pendiente' ? 'pendientes' : 'resueltas'}</h3></div>
    <div class="card-body" style="padding:0">
      ${items.length ? `<table class="tabla"><thead><tr>
        <th>Persona</th><th>Contacto</th><th>Consintió</th><th>Identidad</th><th>Enviada</th><th></th>
      </tr></thead><tbody>${items.map((i) => `<tr>
        <td><strong>${esc(i.nombre)}</strong>
            ${i.documento ? `<div class="tenue">doc. ${esc(i.documento)}</div>` : ''}</td>
        <td>${esc(i.email ?? '—')}${i.celular ? `<div class="tenue">${esc(i.celular)}</div>` : ''}</td>
        <td>${token(i.version_texto)}<div class="tenue">${fechaHora(i.acepto_en)}</div></td>
        <td title="${esc(RESOLUCION[i.resolucion]?.[1] ?? '')}">
            ${token(RESOLUCION[i.resolucion]?.[0] ?? i.resolucion)}</td>
        <td>${fechaHora(i.creado_en)}</td>
        <td>${i.estado === 'pendiente' ? `
          <button class="btn btn-sm btn-primary" data-aprobar="${i.id}">Aprobar</button>
          <button class="btn btn-sm btn-danger" data-rechazar="${i.id}">Rechazar</button>`
          : token(i.estado)}</td>
      </tr>`).join('')}</tbody></table>`
      : vacio(estadoPedido === 'pendiente' ? 'No hay inscripciones esperando.' : 'Todavía no se resolvió ninguna.', '📥')}
    </div></div>`;

  $$('[data-aprobar]').forEach((b) => {
    b.onclick = () => aprobar(Number(b.dataset.aprobar), paneles);
  });
  $$('[data-rechazar]').forEach((b) => {
    b.onclick = () => rechazar(Number(b.dataset.rechazar));
  });
}

function aprobar(id, paneles) {
  modal({
    titulo: 'Aprobar la inscripción',
    cuerpo: `
      <p>Recién al aprobar se crea la persona en la bóveda, con el
      consentimiento que dio en su momento y la versión de texto que aceptó.</p>
      <div class="form-group"><label>Sumar al panel</label>
        <select class="fselect" name="panel_id">
          <option value="">— ninguno por ahora —</option>
          ${paneles.map((p) => `<option value="${p.id}">${esc(p.nombre)}</option>`).join('')}
        </select></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Aprobar', clase: 'btn-primary',
        onClick: async (contenedor) => {
          const { panel_id: panelId } = leerFormulario(contenedor);
          try {
            const r = await api.inscripciones.aprobar(id, panelId ? Number(panelId) : null);
            cerrarModal();
            if (r.estado === 'revision') {
              toast('Quedó en la cola de revisión de altas: la coincidencia es ambigua.', 'aviso');
            } else {
              toast(r.persona === 'reutilizada'
                ? 'Aprobada. Ya existía: se reutilizó su id_persona.'
                : 'Aprobada y dada de alta.', 'ok');
            }
            cargar();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}

function rechazar(id) {
  modal({
    titulo: 'Rechazar la inscripción',
    cuerpo: `<div class="form-group"><label>Motivo (queda registrado)</label>
      <textarea class="finput" name="motivo" rows="2"></textarea></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Rechazar', clase: 'btn-danger',
        onClick: async (contenedor) => {
          try {
            await api.inscripciones.rechazar(id, leerFormulario(contenedor).motivo);
            cerrarModal(); toast('Rechazada.', 'ok'); cargar();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}

/* ── Textos ─────────────────────────────────────────────────────── */

async function pintarTextos(cuerpo) {
  const { items } = await api.inscripciones.textos();
  cuerpo.innerHTML = `
    ${alerta('Cada versión es una fila nueva y no se puede reescribir: lo que alguien consintió tiene que seguir siendo recuperable tal cual. Para cambiar el texto, se publica una versión nueva.', 'info')}
    <div class="card"><div class="card-head">
      <h3>Textos publicados</h3>
      <button class="btn btn-primary" id="nuevo">+ Versión</button>
    </div><div class="card-body" style="padding:0">
      ${items.length ? `<table class="tabla"><thead><tr>
        <th>Finalidad</th><th>Versión</th><th>Texto</th><th>Publicada</th>
      </tr></thead><tbody>${items.map((t, i) => `<tr>
        <td>${token(t.finalidad)}</td>
        <td><strong>${esc(t.version)}</strong>
            ${i === 0 || items[i - 1].finalidad !== t.finalidad ? ' ' + token('vigente') : ''}</td>
        <td class="tenue">${esc(t.cuerpo.slice(0, 120))}${t.cuerpo.length > 120 ? '…' : ''}</td>
        <td>${fechaHora(t.creado_en)}</td>
      </tr>`).join('')}</tbody></table>`
      : vacio('No hay ningún texto publicado. Hasta que haya uno, la landing no recibe inscripciones.', '📜')}
    </div></div>`;
  $('#nuevo').onclick = formularioTexto;
}

function formularioTexto() {
  const hoy = new Date().toISOString().slice(0, 7);
  modal({
    titulo: 'Publicar una versión del consentimiento',
    ancho: 640,
    cuerpo: `
      <div class="grid-2">
        <div class="form-group"><label>Finalidad</label>
          <select class="fselect" name="finalidad">
            <option value="contacto_participacion">Contacto y participación</option>
            <option value="uso_semantico">Uso semántico</option>
          </select></div>
        <div class="form-group"><label>Versión</label>
          <input class="finput" name="version" value="${hoy}"></div>
      </div>
      <div class="form-group"><label>Texto que va a leer y aceptar la persona</label>
        <textarea class="finput" name="cuerpo" rows="10"
          placeholder="El texto legal lo redacta y revisa el DPO."></textarea></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Publicar', clase: 'btn-primary',
        onClick: async (contenedor) => {
          const d = leerFormulario(contenedor);
          try {
            await api.inscripciones.publicarTexto(d.finalidad, d.version, d.cuerpo);
            cerrarModal(); toast('Versión publicada.', 'ok'); cargar();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}
