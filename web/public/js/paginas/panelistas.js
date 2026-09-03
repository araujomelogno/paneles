/* Panelistas — listado, alta con dedup y ficha de la bóveda.
   Cubre R1.1, R1.2 y R1.3 desde la interfaz. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, consentimientos, token, vacio, cargando, toast,
  modal, cerrarModal, leerFormulario, confirmar, activarTokens, fechaCorta,
  fechaHora, hace, estado, alerta,
} from '../ui.js';

const VERSION = () => window.VERSION_CONSENTIMIENTO || 'consentimiento-2026-01';

let filtro = { q: '', panel_id: '' };
let contexto = {};

export async function render(main, ctx) {
  contexto = ctx;
  if (ctx.contexto?.idPersona) return renderFicha(main, ctx.contexto.idPersona);

  const { items: paneles } = await api.paneles.listar();

  main.innerHTML = encabezado('Panelistas', 'de la bóveda',
    'Alta con deduplicación, consentimiento por finalidad y ficha de cada persona.') + `
    <div class="card">
      <div class="card-header">
        <div class="toolbar" style="flex:1">
          <div class="buscador">
            <input type="text" id="q" placeholder="Buscar por nombre, email o documento…"
                   value="${esc(filtro.q)}" />
          </div>
          <select class="fselect" id="f-panel" style="width:auto">
            <option value="">Todos los paneles</option>
            ${paneles.map((p) => `<option value="${p.id}" ${String(filtro.panel_id) === String(p.id) ? 'selected' : ''}>${esc(p.nombre)}</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="card-body tight"><div id="tabla">${cargando()}</div></div>
    </div>`;

  $('#ph-acciones').innerHTML =
    `<button class="btn btn-orange" id="nuevo">+ Enrolar panelista</button>`;
  $('#nuevo').onclick = () => abrirAlta(paneles);

  let temporizador;
  $('#q').oninput = (e) => {
    clearTimeout(temporizador);
    filtro.q = e.target.value;
    temporizador = setTimeout(cargarTabla, 280);
  };
  $('#f-panel').onchange = (e) => { filtro.panel_id = e.target.value; cargarTabla(); };

  await cargarTabla();
}

async function cargarTabla() {
  const contenedor = $('#tabla');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('20vh');
  const { items, total } = await api.panelistas.listar({
    q: filtro.q, panel_id: filtro.panel_id, limite: 100,
  });

  if (!items.length) {
    contenedor.innerHTML = vacio(
      filtro.q ? 'Ningún panelista coincide con la búsqueda.' : 'Todavía no hay panelistas enrolados.',
      '👤');
    return;
  }

  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Panelista</th><th>Documento</th><th>Demografía</th>
        <th>Consentimiento</th><th>Paneles</th><th>Identificador</th><th></th>
      </tr></thead>
      <tbody>${items.map((p) => `
        <tr>
          <td>
            <div class="td-strong">${esc(p.nombre || 'Sin nombre')}</div>
            <div class="td-muted small">${esc(p.email || '—')}</div>
          </td>
          <td class="mono">${esc(p.documento || '—')}</td>
          <td class="small">${esc([p.sexo, p.tramo_etario, p.localidad].filter(Boolean).join(' · ') || '—')}</td>
          <td>${consentimientos(p)}</td>
          <td class="mono">${p.paneles}</td>
          <td>${token(p.id_persona)}</td>
          <td class="right"><button class="btn btn-outline btn-sm" data-ficha="${esc(p.id_persona)}">Ver ficha</button></td>
        </tr>`).join('')}
      </tbody>
    </table></div>
    <div class="paginacion"><span class="p-info">${items.length} de ${total} panelistas</span></div>`;

  activarTokens(contenedor);
  $$('[data-ficha]', contenedor).forEach((boton) => {
    boton.onclick = () => contexto.irA('panelistas', { idPersona: boton.dataset.ficha });
  });
}

/* ── Alta ───────────────────────────────────────────────────────── */

function abrirAlta(paneles) {
  const caja = modal({
    titulo: 'Enrolar panelista',
    ancho: '620px',
    cuerpo: `
      <div id="alta-alerta"></div>
      <div class="form-row">
        <div class="form-group"><label>Nombre completo</label>
          <input type="text" name="nombre" placeholder="Ana Pérez Bentancor" /></div>
        <div class="form-group"><label>Documento</label>
          <input type="text" name="documento" placeholder="4.123.456-7" />
          <div class="field-hint">Clave principal de deduplicación.</div></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Email</label>
          <input type="text" name="email" placeholder="ana@correo.uy" />
          <div class="field-hint">Segunda clave de deduplicación.</div></div>
        <div class="form-group"><label>Celular</label>
          <input type="text" name="celular" placeholder="099 123 456" /></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Sexo</label>
          <select class="fselect" name="sexo">
            <option value="">—</option><option value="F">F</option>
            <option value="M">M</option><option value="X">X</option>
          </select></div>
        <div class="form-group"><label>Fecha de nacimiento</label>
          <input type="date" name="fecha_nacimiento" />
          <div class="field-hint">De acá sale el tramo etario. No sale de la bóveda.</div></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Localidad</label>
          <input type="text" name="localidad" placeholder="Montevideo" /></div>
        <div class="form-group"><label>Sumar al panel</label>
          <select class="fselect" name="panel_id">
            <option value="">No sumar a ningún panel todavía</option>
            ${paneles.filter((p) => p.estado === 'activo')
              .map((p) => `<option value="${p.id}">${esc(p.nombre)}</option>`).join('')}
          </select></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Origen</label>
          <input type="text" name="origen" placeholder="dooblo / alchemer" />
          <div class="field-hint">Plataforma de la que viene el alta.</div></div>
        <div class="form-group"><label>Id en el origen</label>
          <input type="text" name="id_en_origen" placeholder="R-0042" />
          <div class="field-hint">Con esto se enganchan después sus respuestas.</div></div>
      </div>
      <div class="form-group"><label>Observaciones</label>
        <textarea class="finput" name="observaciones" placeholder="Texto libre; puede contener dato sensible."></textarea></div>

      <div class="form-group">
        <label>Consentimiento — versión ${esc(VERSION())}</label>
        <div class="finalidades">
          <label class="finalidad">
            <input type="checkbox" name="finalidades" data-lista="1" value="contacto_participacion" checked />
            <span>
              <span class="f-titulo">Contacto y participación</span>
              <span class="f-desc">Habilita convocarla a encuestas y entrar en el muestreo.</span>
            </span>
          </label>
          <label class="finalidad">
            <input type="checkbox" name="finalidades" data-lista="1" value="uso_semantico" />
            <span>
              <span class="f-titulo">Uso semántico entre estudios</span>
              <span class="f-desc">Finalidad distinta y separada: habilita ingestar sus respuestas
              al store de embeddings para consultarlas entre estudios.</span>
            </span>
          </label>
        </div>
        <div class="field-hint">Sin al menos una finalidad, el alta se rechaza.</div>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Enrolar', clase: 'btn-orange', onClick: (c) => guardarAlta(c, paneles) },
    ],
  });
  return caja;
}

async function guardarAlta(caja, paneles) {
  const datos = leerFormulario(caja);
  const finalidades = datos.finalidades || [];
  const alerta$ = $('#alta-alerta', caja);
  alerta$.innerHTML = '';

  if (!finalidades.length) {
    alerta$.innerHTML = alerta('Marcá al menos una finalidad de consentimiento.');
    return;
  }
  if (!datos.documento && !datos.email && !datos.nombre) {
    alerta$.innerHTML = alerta('Hace falta documento, email o nombre para identificar a la persona.');
    return;
  }

  const cuerpo = {
    persona: {
      documento: datos.documento, nombre: datos.nombre, sexo: datos.sexo,
      fecha_nacimiento: datos.fecha_nacimiento, localidad: datos.localidad,
      email: datos.email, celular: datos.celular, observaciones: datos.observaciones,
    },
    consentimientos: finalidades.map((finalidad) => ({
      finalidad, version_texto: VERSION(),
    })),
    origen: datos.origen, id_en_origen: datos.id_en_origen,
    panel_id: datos.panel_id ? Number(datos.panel_id) : null,
  };

  try {
    const resultado = await api.panelistas.alta(cuerpo);
    cerrarModal();
    if (resultado.estado === 'revision') {
      avisarRevision(resultado);
    } else if (resultado.estado === 'reutilizada') {
      toast(`Ya estaba enrolada (match por ${resultado.motivo_dedup}): se reutilizó su identificador.`, 'ok');
    } else {
      toast('Panelista enrolado.', 'ok');
    }
    await cargarTabla();
  } catch (error) {
    alerta$.innerHTML = alerta(error.message);
  }
}

/* El dedup ambiguo no se resuelve solo: se avisa y se manda a revisión. */
function avisarRevision(resultado) {
  modal({
    titulo: 'El alta quedó en revisión',
    cuerpo: `
      <div class="aviso">
        <h4>Coincidencia ambigua</h4>
        <p>Los datos coinciden por <strong>nombre y fecha de nacimiento</strong> con alguien
        que ya está enrolado, pero el alta no trae documento ni email, que son las claves
        fuertes. El sistema no fusiona por parecido: homónimos con la misma fecha existen.</p>
        <p>El alta quedó pendiente en <strong>Revisión de altas</strong> para que una persona
        decida si es la misma o es otra. Todavía no se creó ninguna persona.</p>
      </div>
      <div style="margin-top:1rem">
        <label>Candidatos</label>
        ${resultado.candidatos.map((c) => `
          <div class="pick">
            <div class="pick-avatar">${esc((c.nombre || '?').slice(0, 2).toUpperCase())}</div>
            <div><div class="pick-name">${esc(c.nombre)}</div>
            <div class="pick-email">${esc(c.localidad || '—')}</div></div>
          </div>`).join('')}
      </div>`,
    acciones: [
      { texto: 'Entendido', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Ir a revisión', clase: 'btn-orange', onClick: () => {
          cerrarModal(); contexto.irA('revisiones');
        } },
    ],
  });
}

/* ── Ficha ──────────────────────────────────────────────────────── */

async function renderFicha(main, idPersona) {
  main.innerHTML = cargando();
  const ficha = await api.panelistas.ficha(idPersona);
  const p = ficha.persona;

  const dato = (etiqueta, valor) =>
    `<dt>${esc(etiqueta)}</dt><dd class="${valor ? '' : 'vacio'}">${esc(valor || '—')}</dd>`;

  main.innerHTML = encabezado(p.nombre || 'Panelista', '', 'Ficha de la bóveda de identidad.') + `
    <div class="grid-2">
      <div class="stack">
        <div class="card">
          <div class="card-header"><span class="card-header-title">Datos de la persona</span></div>
          <div class="card-body">
            <dl class="kv">
              ${dato('Documento', p.documento)}
              ${dato('Email', p.email)}
              ${dato('Celular', p.celular)}
              ${dato('Sexo', p.sexo)}
              ${dato('Fecha de nacimiento', p.fecha_nacimiento ? fechaCorta(p.fecha_nacimiento) : null)}
              ${dato('Tramo etario', ficha.demografia.tramo_etario)}
              ${dato('Localidad', p.localidad)}
              ${dato('Observaciones', p.observaciones)}
              <dt>Identificador</dt>
              <dd>${token(ficha.id_persona, ficha.id_persona.slice(0, 13))}
                <div class="field-hint">Token opaco. Es lo único que viaja al store de embeddings.</div></dd>
              ${dato('Enrolado', fechaHora(ficha.creado_en))}
            </dl>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><span class="card-header-title">Consentimiento</span>
            <button class="btn btn-outline btn-sm" id="otorgar">Otorgar finalidad</button></div>
          <div class="card-body tight">
            ${ficha.consentimientos.length ? `<div class="table-wrap"><table>
              <thead><tr><th>Finalidad</th><th>Estado</th><th>Versión</th><th>Otorgado</th><th>Retirado</th></tr></thead>
              <tbody>${ficha.consentimientos.map((c) => `
                <tr>
                  <td class="td-strong">${esc(c.finalidad)}</td>
                  <td>${estado(c.estado)}</td>
                  <td class="mono small">${esc(c.version_texto)}</td>
                  <td class="small">${fechaCorta(c.otorgado_en)}</td>
                  <td class="small">${c.retirado_en ? fechaCorta(c.retirado_en) : '—'}</td>
                </tr>`).join('')}</tbody></table></div>`
              : vacio('Sin consentimientos registrados.', '📄')}
          </div>
        </div>
      </div>

      <div class="stack">
        <div class="stat-grid" style="margin-bottom:0">
          <div class="stat s-total"><div class="stat-num">${ficha.participacion.convocatorias}</div>
            <div class="stat-label">Convocatorias</div></div>
          <div class="stat s-ok"><div class="stat-num">${ficha.participacion.respondidas}</div>
            <div class="stat-label">Respondidas</div></div>
          <div class="stat s-warn"><div class="stat-num" style="font-size:1rem;padding-top:0.5rem">
            ${esc(hace(ficha.participacion.ultimo_contacto))}</div>
            <div class="stat-label">Último contacto</div></div>
        </div>

        <div class="card">
          <div class="card-header"><span class="card-header-title">Paneles</span></div>
          <div class="card-body tight">
            ${ficha.paneles.length ? `<div class="table-wrap"><table>
              <thead><tr><th>Panel</th><th>Estado</th><th>Alta</th></tr></thead>
              <tbody>${ficha.paneles.map((panel) => `
                <tr><td class="td-strong">${esc(panel.nombre)}</td>
                  <td>${estado(panel.estado)}</td>
                  <td class="small">${fechaCorta(panel.fecha_alta)}</td></tr>`).join('')}
              </tbody></table></div>`
              : vacio('No pertenece a ningún panel.', '📋')}
          </div>
        </div>

        <div class="card">
          <div class="card-header"><span class="card-header-title">Derechos del titular</span></div>
          <div class="card-body">
            <p class="small muted" style="margin-bottom:1rem">
              El retiro de consentimiento dispara el borrado en cascada. Es irreversible.
            </p>
            <div class="toolbar">
              <button class="btn btn-outline btn-del btn-sm" data-retiro="uso_semantico">Retirar uso semántico</button>
              <button class="btn btn-outline btn-del btn-sm" data-retiro="contacto_participacion">Retirar contacto</button>
              <button class="btn btn-danger btn-sm" data-retiro="todas">Baja total</button>
            </div>
          </div>
        </div>
      </div>
    </div>`;

  $('#ph-acciones').innerHTML = `<button class="btn btn-outline" id="volver">‹ Volver al listado</button>`;
  $('#volver').onclick = () => contexto.irA('panelistas');
  activarTokens(main);

  $('#otorgar').onclick = () => abrirOtorgar(idPersona);
  $$('[data-retiro]', main).forEach((boton) => {
    boton.onclick = () => retirar(idPersona, boton.dataset.retiro, p.nombre);
  });
}

function abrirOtorgar(idPersona) {
  modal({
    titulo: 'Otorgar consentimiento',
    cuerpo: `
      <div class="form-group"><label>Finalidad</label>
        <select class="fselect" name="finalidad">
          <option value="contacto_participacion">Contacto y participación</option>
          <option value="uso_semantico">Uso semántico entre estudios</option>
        </select></div>
      <div class="form-group"><label>Versión del texto</label>
        <input type="text" name="version_texto" value="${esc(VERSION())}" />
        <div class="field-hint">Queda guardada: es lo que hace demostrable qué consintió.</div></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Otorgar', clase: 'btn-orange', onClick: async (caja) => {
          const datos = leerFormulario(caja);
          try {
            await api.consentimientos.otorgar(idPersona, datos.finalidad, datos.version_texto);
            cerrarModal();
            toast('Consentimiento registrado.', 'ok');
            contexto.irA('panelistas', { idPersona });
          } catch (error) { toast(error.message, 'err'); }
        } },
    ],
  });
}

const TEXTO_RETIRO = {
  uso_semantico: {
    titulo: 'Retirar el consentimiento de uso semántico',
    cuerpo: `<p>Se borran del store remoto <strong>todos los embeddings</strong> de esta
      persona. Sigue en el panel y se la puede seguir convocando.</p>`,
  },
  contacto_participacion: {
    titulo: 'Retirar el consentimiento de contacto',
    cuerpo: `<p>Sale del muestreo: se dan de baja sus membresías y deja de poder ser
      convocada. Sus embeddings quedan, porque los consintió por separado.</p>`,
  },
  todas: {
    titulo: 'Baja total del panel',
    cuerpo: `<p>Cascada completa e <strong>irreversible</strong>:</p>
      <ul>
        <li>se borra su PII de la bóveda local;</li>
        <li>se borran sus embeddings del store remoto;</li>
        <li>sale del muestreo y de todos los paneles.</li>
      </ul>
      <p>Queda solo una lápida con el token opaco, para poder probar que el retiro se atendió.</p>`,
  },
};

async function retirar(idPersona, finalidad, nombre) {
  const texto = TEXTO_RETIRO[finalidad];
  const ok = await confirmar({
    titulo: texto.titulo,
    cuerpo: `<div class="aviso ${finalidad === 'todas' ? 'grave' : ''}">
      <h4>${esc(nombre || 'Panelista')}</h4>${texto.cuerpo}</div>`,
    textoOk: finalidad === 'todas' ? 'Dar de baja' : 'Retirar',
  });
  if (!ok) return;
  try {
    const resultado = await api.consentimientos.retirar(idPersona, finalidad);
    if (resultado.pii_borrada) {
      toast('Baja completa: PII borrada y embeddings eliminados.', 'ok');
      contexto.irA('panelistas');
    } else {
      toast('Consentimiento retirado y cascada aplicada.', 'ok');
      contexto.irA('panelistas', { idPersona });
    }
  } catch (error) {
    toast(error.message, 'err');
  }
}
