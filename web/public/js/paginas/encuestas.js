/* Encuestas — fielding, convocatoria e ingesta semántica (R1.5).

   Acá se ve el puente entre stores: la encuesta local nace con un
   `ref_estudio`, y la ingesta escribe del lado remoto el cuestionario con
   ese mismo uuid y las respuestas con el `id_persona` de cada panelista.
   Del archivo de campo solo cruza el id de la plataforma traducido a
   `id_persona`: la PII se queda en la bóveda. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, token, vacio, cargando, toast, modal, cerrarModal,
  leerFormulario, activarTokens, fechaCorta, estado, alerta, confirmar,
} from '../ui.js';

let contexto = {};

const ESTADO_ETIQUETA = { borrador: 'Borrador', en_campo: 'En campo', cerrada: 'Cerrada' };

export async function render(main, ctx) {
  contexto = ctx;
  if (ctx.contexto?.encuestaId) return renderDetalle(main, ctx.contexto.encuestaId);

  const panelId = ctx.contexto?.panelId || null;
  const [{ items }, { items: paneles }] = await Promise.all([
    api.encuestas.listar(panelId), api.paneles.listar(),
  ]);

  main.innerHTML = encabezado('Encuestas', 'y olas',
    'Cada encuesta nace con su ref_estudio: es la uuid que la ata a su cuestionario del lado remoto.') + `
    <div class="card">
      <div class="card-header"><span class="card-header-title">
        ${panelId ? esc(paneles.find((p) => p.id === panelId)?.nombre || 'Panel') : 'Todas las encuestas'}
      </span></div>
      <div class="card-body tight">
        ${items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Encuesta</th><th>Panel</th><th>Campo</th><th>Estado</th>
            <th>Convocados</th><th>Respondieron</th><th>ref_estudio</th><th></th></tr></thead>
          <tbody>${items.map((e) => {
            const tasa = e.convocados ? Math.round((e.respondieron / e.convocados) * 100) : 0;
            return `<tr>
              <td class="td-strong">${esc(e.nombre)}</td>
              <td class="small">${esc(e.panel || '—')}</td>
              <td class="small">${e.fecha_campo ? fechaCorta(e.fecha_campo) : '—'}</td>
              <td>${estado(e.estado, ESTADO_ETIQUETA[e.estado])}</td>
              <td class="mono">${e.convocados ?? 0}</td>
              <td><div style="display:flex;align-items:center;gap:0.5rem">
                <span class="mono">${e.respondieron ?? 0}</span>
                <span class="barra ${tasa >= 60 ? 'ok' : ''}"><span style="width:${tasa}%"></span></span>
                <span class="small muted">${tasa}%</span></div></td>
              <td>${token(e.ref_estudio)}</td>
              <td class="right"><button class="btn btn-outline btn-sm" data-encuesta="${e.id}">Abrir</button></td>
            </tr>`;
          }).join('')}</tbody></table></div>`
          : vacio('Todavía no hay encuestas fieldeadas.', '📨')}
      </div>
    </div>`;

  $('#ph-acciones').innerHTML = `
    ${panelId ? `<button class="btn btn-outline" id="todas">Ver todas</button>` : ''}
    <button class="btn btn-orange" id="nueva">+ Nueva encuesta</button>`;
  if (panelId) $('#todas').onclick = () => contexto.irA('encuestas');
  $('#nueva').onclick = () => abrirNueva(paneles, panelId);
  activarTokens(main);
  $$('[data-encuesta]', main).forEach((boton) => {
    boton.onclick = () => contexto.irA('encuestas', { encuestaId: Number(boton.dataset.encuesta) });
  });
}

function abrirNueva(paneles, panelPreseleccionado) {
  const activos = paneles.filter((p) => p.estado === 'activo');
  modal({
    titulo: 'Nueva encuesta',
    cuerpo: `
      <div id="enc-alerta"></div>
      <div class="form-group"><label>Panel</label>
        <select class="fselect" name="panel_id">
          ${activos.map((p) => `<option value="${p.id}" ${p.id === panelPreseleccionado ? 'selected' : ''}>${esc(p.nombre)}</option>`).join('')}
        </select></div>
      <div class="form-group"><label>Nombre de la encuesta</label>
        <input type="text" name="nombre" placeholder="Ola 3 — Consumo de bebidas" /></div>
      <div class="form-group"><label>Fecha de campo</label>
        <input type="date" name="fecha_campo" /></div>
      <div class="field-hint">Al crearla se genera su <code>ref_estudio</code>, que es lo que
      después ata sus respuestas del lado remoto.</div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Crear', clase: 'btn-orange', onClick: async (caja) => {
          const datos = leerFormulario(caja);
          if (!datos.nombre) {
            $('#enc-alerta', caja).innerHTML = alerta('La encuesta necesita un nombre.');
            return;
          }
          try {
            const encuesta = await api.encuestas.crear(
              Number(datos.panel_id), datos.nombre, datos.fecha_campo);
            cerrarModal();
            toast('Encuesta creada.', 'ok');
            contexto.irA('encuestas', { encuestaId: encuesta.id });
          } catch (error) {
            $('#enc-alerta', caja).innerHTML = alerta(error.message);
          }
        } },
    ],
  });
}

/* ── Detalle ────────────────────────────────────────────────────── */

async function renderDetalle(main, encuestaId) {
  main.innerHTML = cargando();
  const encuesta = await api.encuestas.ver(encuestaId);

  main.innerHTML = encabezado(encuesta.nombre, '',
    `Panel #${encuesta.panel_id} · campo ${encuesta.fecha_campo ? fechaCorta(encuesta.fecha_campo) : 'sin fecha'}`) + `
    <div class="card">
      <div class="card-body">
        <dl class="kv">
          <dt>Estado</dt><dd>${estado(encuesta.estado, ESTADO_ETIQUETA[encuesta.estado])}</dd>
          <dt>ref_estudio</dt>
          <dd>${token(encuesta.ref_estudio, encuesta.ref_estudio)}
            <div class="field-hint">La misma uuid identifica al cuestionario del lado remoto.
            No hay clave foránea entre stores: el cruce es por esta uuid y por id_persona.</div></dd>
        </dl>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">Convocatoria y participación</span>
        <div class="toolbar">
          <button class="btn btn-outline btn-sm" id="convocar">Convocar al panel</button>
          <button class="btn btn-orange btn-sm" id="ingestar">Ingestar respuestas</button>
        </div>
      </div>
      <div class="card-body tight"><div id="participacion">${cargando('20vh')}</div></div>
    </div>
    <div class="card">
      <div class="card-header"><span class="card-header-title">Cruce entre stores</span>
        <button class="btn btn-outline btn-sm" id="verificar">Verificar</button></div>
      <div class="card-body"><div id="cruce" class="muted small">
        Verificá que lo que quedó del lado remoto se corresponde con lo convocado acá.
      </div></div>
    </div>`;

  $('#ph-acciones').innerHTML = `
    <button class="btn btn-outline" id="volver">‹ Encuestas</button>
    ${encuesta.estado !== 'cerrada'
      ? `<button class="btn btn-dark" id="cerrar">Cerrar encuesta</button>` : ''}`;
  $('#volver').onclick = () => contexto.irA('encuestas');
  $('#cerrar')?.addEventListener('click', async () => {
    const ok = await confirmar({
      titulo: 'Cerrar la encuesta',
      cuerpo: '<p>Una encuesta cerrada no admite nuevas convocatorias.</p>',
      textoOk: 'Cerrar', claseOk: 'btn-dark',
    });
    if (!ok) return;
    await api.encuestas.cambiarEstado(encuestaId, 'cerrada');
    toast('Encuesta cerrada.', 'ok');
    contexto.irA('encuestas', { encuestaId });
  });
  activarTokens(main);

  $('#convocar').onclick = () => convocar(encuestaId);
  $('#ingestar').onclick = () => abrirIngesta(encuesta);
  $('#verificar').onclick = () => verificarCruce(encuestaId);

  await cargarParticipacion(encuestaId);
}

async function cargarParticipacion(encuestaId) {
  const contenedor = $('#participacion');
  if (!contenedor) return;
  const { items } = await api.encuestas.participacion(encuestaId);
  if (!items.length) {
    contenedor.innerHTML = vacio('Todavía no se convocó a nadie.', '📭');
    return;
  }
  const respondieron = items.filter((p) => p.respondio).length;
  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Panelista</th><th>Id en el origen</th><th>Convocado</th>
        <th>Respondió</th><th>Uso semántico</th></tr></thead>
      <tbody>${items.map((p) => `
        <tr>
          <td><div class="td-strong">${esc(p.nombre || 'Sin nombre')}</div>
            <div class="td-muted small">${esc(p.email || '—')}</div></td>
          <td class="mono small">${esc(p.id_en_origen || '—')}</td>
          <td class="small">${fechaCorta(p.convocado_en)}</td>
          <td>${p.respondio ? estado('completa', 'Sí') : estado('no_ficho', 'No')}</td>
          <td>${p.consiente_semantico
            ? '<span class="badge badge-on">Sí</span>'
            : '<span class="badge badge-off">No</span>'}</td>
        </tr>`).join('')}</tbody></table></div>
    <div class="paginacion"><span class="p-info">
      ${items.length} convocados · ${respondieron} respondieron</span></div>`;
}

async function convocar(encuestaId) {
  const ok = await confirmar({
    titulo: 'Convocar a todo el panel',
    cuerpo: `<div class="aviso"><h4>Gate de consentimiento</h4>
      <p>Solo entran a la convocatoria quienes tengan <strong>contacto / participación</strong>
      vigente. Quien no lo tenga queda afuera y se informa acá.</p></div>`,
    textoOk: 'Convocar', claseOk: 'btn-orange',
  });
  if (!ok) return;
  try {
    const resultado = await api.encuestas.convocar(encuestaId, { todoElPanel: true });
    mostrarResultado('Convocatoria', [
      ['Convocados nuevos', resultado.convocados_nuevos],
      ['Total habilitados', resultado.convocados_total],
      ['Sin consentimiento', resultado.sin_consentimiento.length, true],
    ], resultado.sin_consentimiento.length
      ? `${resultado.sin_consentimiento.length} persona(s) quedaron fuera por no tener el
         consentimiento de contacto vigente.` : null);
    await cargarParticipacion(encuestaId);
  } catch (error) {
    toast(error.message, 'err');
  }
}

function mostrarResultado(titulo, filas, nota) {
  modal({
    titulo,
    cuerpo: `
      <div class="resultado">
        ${filas.map(([etiqueta, valor, alertar]) => `
          <div class="r ${alertar && valor ? 'alerta' : ''}">
            <div class="r-num">${esc(valor)}</div>
            <div class="r-label">${esc(etiqueta)}</div>
          </div>`).join('')}
      </div>
      ${nota ? `<div class="aviso"><p>${esc(nota)}</p></div>` : ''}`,
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
}

/* ── Ingesta ────────────────────────────────────────────────────── */

/* Parser de CSV mínimo, con comillas dobles. El export de campo suele ser
   .xlsx; para eso se carga SheetJS bajo demanda. */
function parsearCSV(texto) {
  const filas = [];
  let campo = '';
  let fila = [];
  let enComillas = false;
  for (let i = 0; i < texto.length; i++) {
    const c = texto[i];
    if (enComillas) {
      if (c === '"') {
        if (texto[i + 1] === '"') { campo += '"'; i++; } else enComillas = false;
      } else campo += c;
    } else if (c === '"') enComillas = true;
    else if (c === ',' || c === ';' || c === '\t') { fila.push(campo); campo = ''; }
    else if (c === '\n') { fila.push(campo); filas.push(fila); fila = []; campo = ''; }
    else if (c !== '\r') campo += c;
  }
  if (campo || fila.length) { fila.push(campo); filas.push(fila); }
  return filas.filter((f) => f.some((v) => String(v).trim() !== ''));
}

function aObjetos(matriz) {
  if (!matriz.length) return { encabezados: [], filas: [] };
  const encabezados = matriz[0].map((h) => String(h).trim());
  const filas = matriz.slice(1).map((valores) => {
    const objeto = {};
    encabezados.forEach((h, i) => { if (h) objeto[h] = valores[i]; });
    return objeto;
  });
  return { encabezados: encabezados.filter(Boolean), filas };
}

async function leerArchivo(archivo) {
  if (/\.(xlsx|xls)$/i.test(archivo.name)) {
    const { read, utils } = await import('https://cdn.jsdelivr.net/npm/xlsx@0.18.5/+esm');
    const libro = read(await archivo.arrayBuffer(), { type: 'array' });
    const hoja = libro.Sheets[libro.SheetNames[0]];
    return aObjetos(utils.sheet_to_json(hoja, { header: 1, raw: false, defval: '' }));
  }
  return aObjetos(parsearCSV(await archivo.text()));
}

/* Las opciones de una cerrada se escriben "1=Fernet; 2=Whisky". */
function parsearOpciones(texto) {
  if (!texto) return null;
  const opciones = {};
  texto.split(/[;\n]/).forEach((par) => {
    const [codigo, ...resto] = par.split('=');
    if (codigo && resto.length) opciones[codigo.trim()] = resto.join('=').trim();
  });
  return Object.keys(opciones).length ? opciones : null;
}

function abrirIngesta(encuesta) {
  let datosArchivo = { encabezados: [], filas: [] };

  const caja = modal({
    titulo: `Ingestar respuestas — ${encuesta.nombre}`,
    ancho: '720px',
    cuerpo: `
      <div id="ing-alerta"></div>
      <div class="aviso">
        <h4>Qué cruza al store remoto</h4>
        <p>Solo el <code>id_persona</code>, el <code>ref_estudio</code>, el texto de la
        respuesta y su embedding. El id de la plataforma de campo se traduce a
        <code>id_persona</code> <strong>en la bóveda</strong>; ninguna PII sale de acá.</p>
        <p>Se ingesta únicamente a quien tenga <strong>uso semántico</strong> vigente.</p>
      </div>

      <div class="form-group" style="margin-top:1.25rem">
        <label>1 · Archivo de respuestas (formato ancho)</label>
        <div class="dropzone" id="dz">
          <span class="dz-icon">📄</span>
          <span class="dz-texto">Elegí el .xlsx o .csv del export de campo</span>
          <span class="dz-hint">Una fila por individuo, una columna por pregunta.
            El encabezado de cada columna es el código de su pregunta.</span>
        </div>
        <input type="file" id="archivo" accept=".xlsx,.xls,.csv,.tsv,.txt" class="hidden" />
        <div id="resumen-archivo" class="field-hint"></div>
      </div>

      <div class="form-group">
        <label>2 · Columna que identifica al respondente</label>
        <select class="fselect" name="columna_id" id="columna-id" disabled>
          <option value="">Cargá primero el archivo</option>
        </select>
        <div class="field-hint">Debe traer el id que usó la plataforma de campo
          (el mismo que se guardó en <code>alias_origen</code> al enrolar).</div>
      </div>

      <div class="form-group">
        <label>3 · Preguntas del cuestionario</label>
        <div id="preguntas"></div>
        <button class="btn btn-outline btn-sm" id="add-pregunta" style="margin-top:0.6rem">+ Agregar pregunta</button>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Ingestar', clase: 'btn-orange', onClick: () => correr() },
    ],
  });

  /* Editor de preguntas. */
  const preguntas$ = $('#preguntas', caja);
  const filaPregunta = (codigo = '', texto = '') => `
    <div class="pregunta-fila">
      <input type="text" class="p-codigo" placeholder="P1" value="${esc(codigo)}" />
      <input type="text" class="p-texto" placeholder="Texto de la pregunta" value="${esc(texto)}" />
      <select class="fselect p-tipo">
        <option value="cerrada">cerrada</option><option value="abierta">abierta</option>
        <option value="escala">escala</option><option value="numerica">numérica</option>
      </select>
      <input type="text" class="p-opciones" placeholder="1=Fernet; 2=Whisky" />
      <button class="modal-close p-quitar" title="Quitar">×</button>
    </div>`;

  const agregarFila = (codigo, texto) => {
    preguntas$.insertAdjacentHTML('beforeend', filaPregunta(codigo, texto));
    preguntas$.lastElementChild.querySelector('.p-quitar').onclick = (e) => {
      e.preventDefault();
      e.target.closest('.pregunta-fila').remove();
    };
  };
  agregarFila();
  $('#add-pregunta', caja).onclick = (e) => { e.preventDefault(); agregarFila(); };

  /* Carga del archivo. */
  const zona = $('#dz', caja);
  const entrada = $('#archivo', caja);
  zona.onclick = () => entrada.click();
  zona.ondragover = (e) => { e.preventDefault(); zona.classList.add('activa'); };
  zona.ondragleave = () => zona.classList.remove('activa');
  zona.ondrop = (e) => {
    e.preventDefault();
    zona.classList.remove('activa');
    if (e.dataTransfer.files[0]) cargarArchivo(e.dataTransfer.files[0]);
  };
  entrada.onchange = (e) => { if (e.target.files[0]) cargarArchivo(e.target.files[0]); };

  async function cargarArchivo(archivo) {
    try {
      datosArchivo = await leerArchivo(archivo);
      $('#resumen-archivo', caja).textContent =
        `${archivo.name} — ${datosArchivo.filas.length} filas, ${datosArchivo.encabezados.length} columnas.`;
      const select = $('#columna-id', caja);
      select.disabled = false;
      const probable = datosArchivo.encabezados.find(
        (h) => /^(id|id_en_origen|respondent|resp_id|codigo_resp)/i.test(h));
      select.innerHTML = datosArchivo.encabezados
        .map((h) => `<option value="${esc(h)}" ${h === probable ? 'selected' : ''}>${esc(h)}</option>`).join('');

      // Precarga las preguntas con las columnas que no son la de identidad.
      preguntas$.innerHTML = '';
      datosArchivo.encabezados
        .filter((h) => h !== (probable || ''))
        .forEach((h) => agregarFila(h, ''));
      if (!preguntas$.children.length) agregarFila();
    } catch (error) {
      $('#ing-alerta', caja).innerHTML = alerta(`No se pudo leer el archivo: ${error.message}`);
    }
  }

  async function correr() {
    const alerta$ = $('#ing-alerta', caja);
    alerta$.innerHTML = '';
    const columnaId = $('#columna-id', caja).value;
    if (!datosArchivo.filas.length) {
      alerta$.innerHTML = alerta('Cargá primero el archivo de respuestas.');
      return;
    }
    if (!columnaId) {
      alerta$.innerHTML = alerta('Elegí la columna que identifica al respondente.');
      return;
    }
    const preguntas = $$('.pregunta-fila', preguntas$).map((fila, i) => ({
      codigo: fila.querySelector('.p-codigo').value.trim(),
      texto: fila.querySelector('.p-texto').value.trim(),
      tipo: fila.querySelector('.p-tipo').value,
      opciones: parsearOpciones(fila.querySelector('.p-opciones').value.trim()),
      orden: i + 1,
    })).filter((p) => p.codigo && p.texto);

    if (!preguntas.length) {
      alerta$.innerHTML = alerta('Cada pregunta necesita su código y su texto: el texto es lo que se embebe.');
      return;
    }

    try {
      const resultado = await api.encuestas.ingestar(encuesta.id, {
        preguntas, filas: datosArchivo.filas, columnaId,
      });
      cerrarModal();
      mostrarResultado('Ingesta terminada', [
        ['Respuestas escritas', resultado.respuestas_escritas],
        ['Personas', resultado.personas],
        ['Sin mapear', (resultado.sin_mapear || []).length, true],
        ['Sin consentimiento', (resultado.sin_consentimiento || []).length, true],
      ], [
        (resultado.sin_mapear || []).length
          ? `${resultado.sin_mapear.length} fila(s) traían un id que no corresponde a ningún panelista convocado.`
          : null,
        (resultado.sin_consentimiento || []).length
          ? `${resultado.sin_consentimiento.length} panelista(s) quedaron fuera por no tener uso semántico vigente.`
          : null,
      ].filter(Boolean).join(' '));
      await cargarParticipacion(encuesta.id);
      await verificarCruce(encuesta.id);
    } catch (error) {
      alerta$.innerHTML = alerta(error.message);
    }
  }
}

async function verificarCruce(encuestaId) {
  const contenedor = $('#cruce');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('12vh');
  try {
    const cruce = await api.encuestas.cruce(encuestaId);
    contenedor.innerHTML = `
      <div class="resultado">
        <div class="r"><div class="r-num">${cruce.local.convocados}</div>
          <div class="r-label">Convocados (local)</div></div>
        <div class="r"><div class="r-num">${cruce.remoto.individuos}</div>
          <div class="r-label">Individuos (remoto)</div></div>
        <div class="r"><div class="r-num">${cruce.remoto.preguntas}</div>
          <div class="r-label">Preguntas</div></div>
        <div class="r"><div class="r-num">${cruce.remoto.respuestas}</div>
          <div class="r-label">Respuestas</div></div>
      </div>
      <div class="alert ${cruce.cruce_ok ? 'alert-success' : 'alert-error'}">
        ${cruce.cruce_ok
          ? 'Los dos stores comparten el mismo ref_estudio y las personas del remoto salen de lo convocado acá.'
          : 'El remoto tiene individuos que no fueron convocados en esta encuesta. Revisar.'}
      </div>`;
  } catch (error) {
    contenedor.innerHTML = alerta(
      error.status === 404
        ? 'Esta encuesta todavía no tiene nada del lado remoto: falta ingestar.'
        : error.message,
      error.status === 404 ? 'info' : 'error');
  }
}
