/* Inscripciones de la landing y textos de consentimiento (R3.7).

   Dos cosas conviven acá porque son dos mitades de lo mismo: sin un texto
   publicado la landing no puede recibir a nadie, y cada inscripción queda
   atada a la versión que esa persona leyó.

   La bandeja muestra qué dijo la resolución de identidad —si la persona ya
   existe, si el caso es ambiguo— porque quien aprueba necesita saberlo. Eso
   es adentro; la landing, afuera, no dice nada de eso.

   Arriba de todo va **el enlace del formulario**, que es lo que hay que
   repartir y no estaba en ninguna pantalla: había que saberlo de memoria o
   ir a buscarlo al documento de despliegue. Va con su estado, porque el
   enlace solo sirve si las dos condiciones se cumplen, y las dos fallan en
   silencio:

     sin texto de consentimiento publicado   el formulario rechaza a todos
     sin proveedor de envío de códigos       el código se muestra en pantalla,
                                             así que la verificación no verifica
                                             nada y el enlace no se puede repartir
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
    <div id="enlace">${cargando('12vh')}</div>
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
    // Una sola lectura de los textos para las dos cosas que los necesitan:
    // la tarjeta del enlace y la solapa. Antes se pedían dos veces en la
    // misma carga de pantalla.
    const { items: textos } = await api.inscripciones.textos();
    pintarEnlace(textos);
    if (solapa === 'textos') return pintarTextos(cuerpo, textos);
    return pintarInscripciones(
      cuerpo, solapa === 'pendientes' ? 'pendiente' : 'aprobada', textos);
  } catch (error) {
    cuerpo.innerHTML = alerta(error.message);
  }
}

/* El enlace del formulario público, con su estado.

   La URL se arma con el origen de la propia app y no con un dominio
   configurado: las dos cosas se sirven del mismo Hosting, así que el origen
   **es** el dato correcto en producción, en la copia demo y en desarrollo, y
   no hay una variable más que se pueda quedar vieja. */
async function pintarEnlace(textos) {
  const caja = $('#enlace');
  if (!caja) return;
  const url = `${window.location.origin}/inscribirse`;

  const hayTexto = textos.some(
    (t) => t.finalidad === 'contacto_participacion' && t.activo);

  // El diagnóstico puede no estar disponible —una versión vieja del backend,
  // por ejemplo—; si no está, se informa lo que sí se sabe en vez de no
  // mostrar nada.
  let verifica = null;
  try {
    const diagnostico = await api.cumplimiento.contacto();
    // `envia_de_verdad` es lo que decide si el código sale hacia el titular
    // o vuelve en la respuesta. Lo segundo es lo que vuelve teatro a la
    // verificación, y por eso es la condición que importa acá.
    verifica = Boolean(diagnostico?.verificacion?.envia_de_verdad);
  } catch { /* se informa sin esta parte */ }

  const problemas = [];
  if (!hayTexto) {
    problemas.push('No hay un texto de consentimiento publicado: el '
      + 'formulario rechaza a todo el mundo. Se publica en la solapa «Textos '
      + 'de consentimiento».');
  }
  if (verifica === false) {
    problemas.push('No hay proveedor de envío de códigos: el formulario '
      + 'muestra el código en pantalla en vez de mandarlo, así que la '
      + 'verificación no comprueba nada. El enlace no se puede repartir así.');
  }

  caja.innerHTML = `
    <div class="card"><div class="card-body">
      <div class="card-header-title" style="margin-bottom:.6rem">
        Enlace del formulario público</div>
      <div class="toolbar" style="gap:.5rem;flex-wrap:wrap">
        <code class="token" id="url-landing" data-copiar="${esc(url)}"
              style="font-size:.8rem;padding:.45rem .7rem">${esc(url)}</code>
        <button class="btn btn-outline btn-sm" id="copiar-landing">Copiar</button>
        <a class="btn btn-outline btn-sm" href="${esc(url)}" target="_blank"
           rel="noopener">Abrir</a>
      </div>
      ${problemas.length
        ? problemas.map((p) => alerta(p, 'warn')).join('')
        : `<div class="alert alert-success" style="margin-top:.75rem">${
             verifica === null
               ? 'Hay un texto de consentimiento publicado: el formulario '
                 + 'recibe inscripciones.'
               : 'Hay texto publicado y los códigos se envían de verdad: el '
                 + 'enlace se puede repartir.'}</div>`}
    </div></div>`;

  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast('Enlace copiado.', 'ok');
    } catch {
      toast('No se pudo copiar. El enlace está a la vista para copiarlo a mano.',
            'err');
    }
  };
  $('#copiar-landing').onclick = copiar;
  $('#url-landing').onclick = copiar;
}

async function pintarInscripciones(cuerpo, estadoPedido, textos) {
  const [{ items }, { items: paneles }] = await Promise.all([
    api.inscripciones.listar(estadoPedido),
    api.paneles.listar(),
  ]);

  // El aviso de «falta el texto» ya lo da la tarjeta del enlace, arriba, que
  // es donde se mira antes de repartirlo. Repetirlo acá era decir dos veces
  // lo mismo en la misma pantalla.
  cuerpo.innerHTML = `
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

const COINCIDENCIAS = {
  documento: 'documento',
  email: 'correo',
  celular: 'celular',
  nombre_fecha_nacimiento: 'nombre y fecha de nacimiento',
};

async function aprobar(id, paneles) {
  // R4.3 — la inscripción con sus candidatos parecidos. La landing es el
  // único camino donde alguien se inscribe solo, sin que nadie del equipo
  // controle qué escribe: es donde más probable es que la misma persona se
  // anote dos veces con datos levemente distintos.
  const inscripcion = await api.inscripciones.ver(id);
  const candidatos = inscripcion.candidatos || [];

  modal({
    titulo: 'Aprobar la inscripción',
    ancho: candidatos.length ? '680px' : undefined,
    cuerpo: `
      <p>Recién al aprobar se crea la persona en la bóveda, con el
      consentimiento que dio en su momento y la versión de texto que aceptó.</p>
      ${inscripcion.canales?.length ? `
        <div class="small muted">Aceptó que la contacten por
        <strong>${inscripcion.canales.map(esc).join(', ')}</strong>, de primera
        mano en el formulario.</div>` : ''}
      ${candidatos.length ? `
        <div class="aviso" style="margin:1rem 0">
          <h4>Hay ${candidatos.length} panelista(s) parecido(s)</h4>
          <p>Se <strong>proponen</strong>, no se fusionan: una coincidencia de
          correo o de nombre y fecha puede ser un homónimo, y fusionar a dos
          personas distintas no se deshace. La única que el sistema resuelve
          sola es el documento exacto.</p>
        </div>
        <div class="form-group">
          <label>¿Es alguna de estas personas?</label>
          <select class="fselect" name="id_persona">
            <option value="">No, es alguien nuevo</option>
            ${candidatos.map((c) => `
              <option value="${esc(c.id_persona)}">
                ${esc(c.nombre || c.id_persona.slice(0, 8))} —
                coincide por ${c.coincide_por.map((m) => COINCIDENCIAS[m] || m).join(', ')}
                ${c.paneles ? ` · ${c.paneles} panel(es)` : ''}
              </option>`).join('')}
          </select>
          <div class="field-hint">Si elegís una, no se crea nadie nuevo: se
            completa esa ficha y se le registran el consentimiento y los
            canales que dio en la landing.</div>
        </div>` : ''}
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
          const { panel_id: panelId, id_persona: idPersona } =
            leerFormulario(contenedor);
          try {
            const r = await api.inscripciones.aprobar(
              id, panelId ? Number(panelId) : null, idPersona || null);
            cerrarModal();
            if (r.estado === 'revision') {
              toast('Quedó en la cola de revisión de altas: la coincidencia es ambigua.', 'aviso');
            } else if (r.persona === 'fusionada') {
              toast('Aprobada y fusionada con el panelista que elegiste.', 'ok');
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

async function pintarTextos(cuerpo, items) {
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
