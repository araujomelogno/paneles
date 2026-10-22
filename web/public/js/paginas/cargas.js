/* R-CAR — La sección «Cargas».

   Hasta acá una carga solo se veía de costado: como opción de un filtro en
   Panelistas, en Composición y en Estadísticas. Esta sección la mira como
   un todo: sus datos, cuánta gente trajo —creada y reutilizada—, de qué
   trata (sus temáticas, R-TEM) y quiénes son.

   Dos decisiones que no son de diseño:

   - **La lista de panelistas de una carga es la de la sección Panelistas**:
     `api.panelistas.listar({ carga_id })`, la misma ruta y el mismo permiso.
     No hay una ruta propia que liste personas de una carga, así que lo que
     un rol no ve allá tampoco lo ve acá, sin una segunda regla que mantener.
   - **Las temáticas describen la carga, no a cada persona.** Que una carga
     cubra «consumo de tabaco» no dice que todos sus individuos hayan
     respondido esas preguntas; por eso viven acá y no en la ficha.

   Una carga nueva se sigue abriendo desde Panelistas → «Cargar panelistas»:
   es el mismo flujo de ingesta, y duplicarlo acá sería tener dos. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, consentimientos, vacio, cargando, toast, alerta,
  fechaCorta, fechaHora, modal, cerrarModal,
} from '../ui.js';
import { editorDeTematicas, chipsDeTematicas } from '../tematicas.js';
import { editorDeClientes, chipsDeClientes } from '../clientes.js';

let contexto = {};
let filtro = { q: '', tematica_id: '' };
const POR_PAGINA = 25;

// Quien puede corregir los datos de una carga puede editar sus temáticas
// (permiso `ingestar`). Es una copia para no ofrecer un botón que da 403;
// la autoridad es el servidor.
const puedeEditar = () => ['admin', 'operaciones', 'analista'].includes(contexto.actor?.rol);
// Quitarle la marca de especial a una temática es de quien administra los
// atributos (`gestionar_atributos`). Misma salvedad: decide el servidor.
const puedeDesmarcarEspecial = () => contexto.actor?.rol === 'admin';
// Borrar una carga es de administración (`borrar_carga`). Misma salvedad.
const puedeBorrar = () => contexto.actor?.rol === 'admin';

const ORIGEN_EN_CARGA = {
  creada: { texto: 'creada', clase: 'est-cerrada', titulo: 'Esta carga la dio de alta.' },
  reutilizada: {
    texto: 'reutilizada', clase: 'est-borrador',
    titulo: 'Ya existía: el dedup (o el identificador del archivo) la encontró.',
  },
};

export async function render(main, ctx) {
  contexto = ctx;
  if (ctx.contexto?.cargaId) return renderDetalle(main, ctx.contexto.cargaId);
  // R-TEM.7 — la taxonomía de temáticas se abre desde acá.
  if (ctx.contexto?.vista === 'taxonomia') {
    const { renderTaxonomia } = await import('./taxonomia.js');
    return renderTaxonomia(main, ctx);
  }
  return renderListado(main);
}

/* ── R-CAR.1 · El listado ────────────────────────────────────────── */

async function renderListado(main) {
  const { items: vocabulario } = await api.tematicas.listar().catch(() => ({ items: [] }));
  const usadas = vocabulario.filter((t) => t.cargas > 0);
  main.innerHTML = encabezado('Cargas', 'y sus temáticas',
    'Cada estudio incorporado: de dónde viene, cuánta gente trajo y de qué trata.') + `
    <div class="card">
      <div class="card-header">
        <div class="toolbar" style="flex:1">
          <div class="buscador">
            <input type="text" id="buscar" placeholder="Buscar por nombre…"
                   value="${esc(filtro.q)}" />
          </div>
          <select class="fselect" id="f-tematica" style="width:auto">
            <option value="">Todas las temáticas</option>
            ${usadas.map((t) => `<option value="${t.id}"
              ${String(filtro.tematica_id) === String(t.id) ? 'selected' : ''}>
              ${esc(t.nombre)} (${t.cargas})</option>`).join('')}
          </select>
        </div>
      </div>
      <div class="card-body tight"><div id="tabla">${cargando()}</div></div>
    </div>
    <p class="small muted">Una carga nueva se abre desde <strong>Panelistas →
      Cargar panelistas</strong>; al revisarla, el sistema propone sus temáticas.</p>`;
  // R-TEM.7 — la taxonomía (áreas → temáticas), en su propia vista.
  $('.cargas-filtros, .card-header .toolbar', main)?.insertAdjacentHTML('beforeend',
    '<span class="toolbar-spacer"></span><button class="btn btn-outline btn-sm" '
    + 'id="ver-taxonomia">Taxonomía de temáticas</button>');
  $('#ver-taxonomia').onclick = () => contexto.irA('cargas', { vista: 'taxonomia' });

  let temporizador;
  $('#buscar').oninput = (e) => {
    filtro.q = e.target.value;
    clearTimeout(temporizador);
    temporizador = setTimeout(cargarTabla, 280);
  };
  $('#f-tematica').onchange = (e) => { filtro.tematica_id = e.target.value; cargarTabla(); };
  await cargarTabla();
}

async function cargarTabla() {
  const contenedor = $('#tabla');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('20vh');
  const { items } = await api.cargas.listar({ q: filtro.q, tematica_id: filtro.tematica_id });
  if (!items.length) {
    contenedor.innerHTML = vacio(filtro.q || filtro.tematica_id
      ? 'Ninguna carga coincide con el filtro.'
      : 'Todavía no hay cargas. Se abren desde Panelistas → Cargar panelistas.', '🗂️');
    return;
  }
  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Carga</th><th>Fecha del estudio</th><th>Público objetivo</th>
        <th class="num">Individuos</th><th>Clientes</th><th>Temáticas</th><th></th>
      </tr></thead>
      <tbody>${items.map((c) => `
        <tr>
          <td><div class="td-strong">${esc(c.nombre)}</div>
              ${c.descripcion ? `<div class="td-muted small">${esc(c.descripcion)}</div>` : ''}</td>
          <td class="small">${c.fecha_estudio ? esc(fechaCorta(c.fecha_estudio))
            : '<span class="muted">sin dato</span>'}</td>
          <td class="small">${esc(c.publico_objetivo || '—')}</td>
          <td class="num"><strong>${c.personas ?? 0}</strong>
              <div class="td-muted small">${c.personas_creadas ?? 0} creados ·
                ${c.personas_reutilizadas
                  ? `<a href="#" data-reutilizados="${c.id}" title="De qué otras cargas vienen">${c.personas_reutilizadas} reutilizados</a>`
                  : '0 reutilizados'}</div></td>
          <td>${chipsDeClientes(c.clientes || [])}</td>
          <td>${chipsDeTematicas(c.tematicas)}</td>
          <td class="right"><button class="btn btn-outline btn-sm" data-carga="${c.id}">Ver</button></td>
        </tr>`).join('')}
      </tbody>
    </table></div>
    <div class="paginacion"><span class="p-info">${items.length} carga(s),
      la más reciente primero (por fecha del estudio)</span></div>`;
  $$('[data-carga]', contenedor).forEach((boton) => {
    boton.onclick = () => contexto.irA('cargas', { cargaId: Number(boton.dataset.carga) });
  });
  activarReutilizados(contenedor);
}

/* ── R-CAR.3 · De dónde vienen los reutilizados ─────────────────── */

function activarReutilizados(contenedor) {
  $$('[data-reutilizados]', contenedor).forEach((enlace) => {
    enlace.onclick = (e) => {
      e.preventDefault();
      abrirReutilizados(Number(enlace.dataset.reutilizados));
    };
  });
}

/* Para cada otra carga en la que ya estaban, cuántos. Una persona puede
   haber estado en varias: la suma puede superar el total, y el modal lo
   dice. Quien no venía de ninguna carga tiene su fila, para que los
   números cierren. */
async function abrirReutilizados(cargaId) {
  const caja = modal({
    titulo: 'De dónde vienen los reutilizados',
    ancho: '640px',
    cuerpo: cargando('15vh'),
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
  try {
    const d = await api.cargas.reutilizados(cargaId);
    $('.modal-body', caja).innerHTML = `
      <p><strong>${d.reutilizados}</strong> persona(s) ya existían cuando entró esta
        carga. ${esc(d.nota)} Por eso la suma puede ser mayor.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Carga anterior</th><th>Fecha del estudio</th><th class="num">Personas</th></tr></thead>
        <tbody>${d.cargas.map((f) => `<tr>
          <td><a href="#" data-ir-carga="${f.carga_id}" class="td-strong">${esc(f.nombre)}</a></td>
          <td class="small">${f.fecha_estudio ? esc(fechaCorta(f.fecha_estudio)) : '<span class="muted">sin fecha</span>'}</td>
          <td class="num">${f.personas}</td></tr>`).join('')}
          <tr><td colspan="2" class="small"><em>${esc(d.sin_carga_previa_etiqueta)}</em></td>
            <td class="num">${d.sin_carga_previa}</td></tr>
        </tbody></table></div>
      <p class="small muted">Lo cargado antes de que existiera el vínculo con la
        carga (R-ORG) no dejó constancia: esas personas cuentan como «sin carga
        previa».</p>`;
    $$('[data-ir-carga]', caja).forEach((enlace) => {
      enlace.onclick = (e) => {
        e.preventDefault();
        cerrarModal();
        contexto.irA('cargas', { cargaId: Number(enlace.dataset.irCarga) });
      };
    });
  } catch (error) {
    $('.modal-body', caja).innerHTML = alerta(error.message);
  }
}

/* ── R-CAR.2 · El detalle ────────────────────────────────────────── */

async function renderDetalle(main, cargaId) {
  const [carga, { items: vocabulario }, { areas }] = await Promise.all([
    api.cargas.ver(cargaId),
    api.tematicas.listar().catch(() => ({ items: [] })),
    // R-TEM.9 — las áreas, para ubicar las temáticas que no tienen.
    api.tematicas.taxonomia().catch(() => ({ areas: [] })),
  ]);
  // R-TEM.6 — las preguntas guardadas de la carga, para asociarlas a cada
  // temática. Si el store semántico no responde, el editor sigue andando
  // con las que ya están asociadas.
  const { preguntas: preguntasDisponibles } = puedeEditar()
    ? await api.cargas.tematicas(cargaId).catch(() => ({ preguntas: [] }))
    : { preguntas: [] };
  const dato = (valor) => (valor ? esc(valor) : '<span class="muted">sin dato</span>');

  main.innerHTML = encabezado(carga.nombre, '', 'Detalle de la carga') + `
    <div class="toolbar" style="margin-bottom:1rem">
      <button class="btn btn-outline btn-sm" id="volver">← Todas las cargas</button>
      <button class="btn btn-outline btn-sm" id="composicion">Ver su composición</button>
      ${puedeBorrar() ? `<span class="toolbar-spacer"></span>
        <button class="btn btn-outline btn-sm texto-peligro" id="borrar-carga">Borrar la carga…</button>` : ''}
    </div>

    <div class="grid-2" style="margin-bottom:1.25rem">
      <div class="card"><div class="card-header"><span class="card-header-title">Atributos</span></div>
        <div class="card-body">
          <dl class="carga-atributos">
            <dt>Nombre</dt><dd>${esc(carga.nombre)}</dd>
            <dt>Descripción</dt><dd>${dato(carga.descripcion)}</dd>
            <dt>Fecha del estudio</dt><dd>${carga.fecha_estudio
              ? esc(fechaCorta(carga.fecha_estudio)) : dato(null)}</dd>
            <dt>Público objetivo</dt><dd>${dato(carga.publico_objetivo)}</dd>
            <dt>Tipo de datos</dt><dd>${dato(carga.tipo_datos_origen_etiqueta)}</dd>
            <dt>Modo de relevamiento</dt><dd>${dato(carga.modo_relevamiento_etiqueta)}</dd>
            <dt>Clientes</dt><dd id="clientes-de-la-carga">${chipsDeClientes(carga.clientes || [])}</dd>
            <dt>Fecha de carga</dt><dd>${esc(fechaHora(carga.creado_en))}</dd>
          </dl>
        </div></div>

      <div class="card" style="margin-top:0"><div class="card-header"><span class="card-header-title">Individuos</span></div>
        <div class="card-body">
          <div class="grid-datos">
            <div class="tarjeta-dato"><div class="valor">${carga.personas ?? 0}</div>
              <div class="etiqueta">en total</div></div>
            <div class="tarjeta-dato"><div class="valor">${carga.personas_creadas ?? 0}</div>
              <div class="etiqueta">creados por esta carga</div></div>
            <div class="tarjeta-dato"><div class="valor">${carga.personas_reutilizadas ?? 0}</div>
              <div class="etiqueta">${carga.personas_reutilizadas
                ? `<a href="#" data-reutilizados="${carga.id}">reutilizados (ya existían) · ver de dónde</a>`
                : 'reutilizados (ya existían)'}</div></div>
          </div>
          <p class="small muted" style="margin-top:0.6rem">Ninguno queda en un panel
            por venir de una carga. Quien se cargó antes de la versión R-ORG no
            dejó constancia de su carga y no figura acá.</p>
        </div></div>
    </div>

    ${puedeEditar() ? `<div class="card">
      <div class="card-header"><span class="card-header-title">Clientes</span>
        <div class="toolbar">
          <button class="btn btn-orange btn-sm" id="guardar-clientes">Guardar clientes</button>
        </div></div>
      <div class="card-body">
        <div id="clientes-alerta"></div>
        <div id="clientes"></div>
        <p class="small muted" style="margin-top:0.6rem">Para quién se hizo el estudio.
          Cada panelista de la carga lo hereda sin copiarse: corregirlo acá se ve
          en todas sus fichas. Es confidencial: no llega al portal ni a las
          exportaciones.</p>
      </div>
    </div>` : ''}

    <div class="card">
      <div class="card-header"><span class="card-header-title">Temáticas incorporadas</span>
        ${puedeEditar() ? `<div class="toolbar">
          <button class="btn btn-outline btn-sm" id="proponer">Proponer temáticas</button>
          <button class="btn btn-orange btn-sm" id="guardar-tematicas">Guardar temáticas</button>
        </div>` : ''}</div>
      <div class="card-body">
        <div id="tematicas-alerta"></div>
        <div id="tematicas">${puedeEditar() ? '' : chipsDeTematicas(carga.tematicas)}</div>
        <p class="small muted" style="margin-top:0.6rem">Dos niveles distintos: la
          temática de <strong>la carga</strong> dice qué cubrió el estudio; la de
          <strong>cada panelista</strong>, sobre qué respondió. Que la carga cubra un
          tema no quiere decir que todos sus individuos lo tengan: lo tiene quien
          respondió al menos una de sus preguntas. Guardar recalcula a todos.</p>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><span class="card-header-title">Panelistas cargados</span></div>
      <div class="card-body tight"><div id="panelistas">${cargando('15vh')}</div></div>
    </div>`;

  $('#volver').onclick = () => contexto.irA('cargas');
  activarReutilizados(main);
  if (puedeBorrar()) $('#borrar-carga').onclick = () => abrirBorrado(carga);
  // El ámbito «una carga» de la composición (R-ORG) ya existe: se abre ahí.
  $('#composicion').onclick = () => contexto.irA('composicion', { cargaId: carga.id });

  if (puedeEditar()) {
    // R-CLI.2 — los clientes se editan después de creada la carga.
    let clientes = editorDeClientes($('#clientes'), { iniciales: carga.clientes || [] });
    $('#guardar-clientes').onclick = async (e) => {
      const boton = e.currentTarget;
      boton.disabled = true;
      try {
        const nueva = await api.cargas.editar(carga.id, { clientes: clientes.valor() });
        clientes = editorDeClientes($('#clientes'), { iniciales: nueva.clientes || [] });
        $('#clientes-de-la-carga').innerHTML = chipsDeClientes(nueva.clientes || []);
        $('#clientes-alerta').innerHTML = '';
        toast('Clientes guardados. Se ven en las fichas de sus panelistas.', 'ok');
      } catch (error) {
        $('#clientes-alerta').innerHTML = alerta(error.message);
      } finally {
        boton.disabled = false;
      }
    };
    const opciones = { preguntasDisponibles, areas, puedeDesmarcar: puedeDesmarcarEspecial() };
    let editor = editorDeTematicas($('#tematicas'), {
      ...opciones, vocabulario, iniciales: carga.tematicas,
    });
    $('#guardar-tematicas').onclick = async (e) => {
      const boton = e.currentTarget;
      boton.disabled = true;
      try {
        const { tematicas } = await api.cargas.guardarTematicas(carga.id, editor.valor());
        const { items } = await api.tematicas.listar().catch(() => ({ items: vocabulario }));
        editor = editorDeTematicas($('#tematicas'), {
          ...opciones, vocabulario: items, iniciales: tematicas,
        });
        $('#tematicas-alerta').innerHTML = '';
        toast('Temáticas guardadas.', 'ok');
      } catch (error) {
        $('#tematicas-alerta').innerHTML = alerta(error.message);
      } finally {
        boton.disabled = false;
      }
    };
    /* R-TEM.5 — para una carga anterior a las temáticas, o para volver a
       mirar. Usa las preguntas ya guardadas; la propuesta se suma a lo que
       hay en el editor y **no se guarda** hasta «Guardar temáticas». */
    $('#proponer').onclick = async (e) => {
      const boton = e.currentTarget;
      boton.disabled = true;
      boton.textContent = 'Proponiendo…';
      try {
        const salida = await api.cargas.proponerTematicas(carga.id);
        editor = editorDeTematicas($('#tematicas'), {
          ...opciones, vocabulario,
          iniciales: editor.valor().map((t) => ({ ...t, es_especial: t.es_especial })),
          propuesta: salida.propuesta, aviso: salida.aviso,
        });
        $('#tematicas-alerta').innerHTML = salida.propuesta.length ? alerta(
          'Claude propuso temáticas a partir de las preguntas guardadas. Revisalas: '
          + 'quitá las que no correspondan y apretá «Guardar temáticas». Nada se '
          + 'guardó todavía.', 'info') : '';
      } catch (error) {
        $('#tematicas-alerta').innerHTML = alerta(error.message);
      } finally {
        boton.disabled = false;
        boton.textContent = 'Proponer temáticas';
      }
    };
  }

  await cargarPanelistas(carga.id, 0);
}

/* La lista paginada. Sale de `GET /panelistas?carga_id=…`: la misma ruta y
   el mismo permiso que la sección Panelistas, y cada fila lleva a la ficha. */
async function cargarPanelistas(cargaId, desde) {
  const contenedor = $('#panelistas');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('15vh');
  const { items, total } = await api.panelistas.listar({
    carga_id: cargaId, limite: POR_PAGINA, desde,
  });
  if (!items.length) {
    contenedor.innerHTML = vacio('No hay personas vinculadas a esta carga. El vínculo '
      + 'se registra desde la versión R-ORG: lo cargado antes no dejó constancia.', '👤');
    return;
  }
  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Panelista</th><th>Documento</th><th>Demografía</th>
        <th>Consentimiento</th><th>En esta carga</th><th></th></tr></thead>
      <tbody>${items.map((p) => {
        const origen = ORIGEN_EN_CARGA[p.origen_en_carga] || {};
        return `
        <tr>
          <td><div class="td-strong">${esc(p.nombre || 'Sin nombre')}</div>
              <div class="td-muted small">${esc(p.email || '—')}</div></td>
          <td class="mono">${esc(p.documento || '—')}</td>
          <td class="small">${esc([p.sexo, p.tramo_etario, p.localidad].filter(Boolean).join(' · ') || '—')}</td>
          <td>${consentimientos(p)}</td>
          <td><span class="est ${origen.clase || ''}" title="${esc(origen.titulo || '')}">
            ${esc(origen.texto || '—')}</span></td>
          <td class="right"><button class="btn btn-outline btn-sm"
              data-ficha="${esc(p.id_persona)}">Ver ficha</button></td>
        </tr>`;
      }).join('')}
      </tbody>
    </table></div>
    <div class="paginacion">
      <span class="p-info">${desde + 1}–${desde + items.length} de ${total} panelistas</span>
      <div class="toolbar">
        <button class="btn btn-outline btn-sm" id="anterior" ${desde === 0 ? 'disabled' : ''}>← Anterior</button>
        <button class="btn btn-outline btn-sm" id="siguiente"
                ${desde + items.length >= total ? 'disabled' : ''}>Siguiente →</button>
      </div>
    </div>`;
  $$('[data-ficha]', contenedor).forEach((boton) => {
    boton.onclick = () => contexto.irA('panelistas', { idPersona: boton.dataset.ficha });
  });
  $('#anterior', contenedor).onclick = () => cargarPanelistas(cargaId, Math.max(0, desde - POR_PAGINA));
  $('#siguiente', contenedor).onclick = () => cargarPanelistas(cargaId, desde + POR_PAGINA);
}


/* ── Borrar una carga (PEDIDO del celular, parte 3; D78) ─────────────

   Lo que se borra es **lo que la carga trajo**: todas las respuestas de su
   estudio y las personas que creó y no tienen otra vida en el sistema. Las
   demás se conservan, sin el vínculo ni esas respuestas. La vista previa
   lo dice con números y motivos, y sale de la misma ruta que borra.

   Confirmación fuerte: hay que escribir el nombre de la carga. Es
   irreversible, y lo dice. Con una ingesta en curso no se puede; con un
   borrado a medias, confirmar lo reanuda. */
async function abrirBorrado(carga) {
  const caja = modal({
    titulo: `Borrar la carga — ${carga.nombre}`,
    ancho: '640px',
    cuerpo: `<div id="borrado-cuerpo">${cargando('8vh')}</div>`,
  });
  const cuerpo$ = $('#borrado-cuerpo', caja);
  const enCurso = async (error) => {
    const { seguirTrabajo } = await import('./encuestas.js');
    cuerpo$.innerHTML = `<div class="alert alert-info">${esc(error.message)}
      <div style="margin-top:.5rem"><button class="btn btn-orange btn-sm"
        id="ver-en-curso">Ver su avance</button></div></div>`;
    $('#ver-en-curso', caja).onclick = () => {
      cerrarModal();
      seguirTrabajo(error.detalle.trabajo_id, { esCarga: true });
    };
  };

  let previa;
  try {
    previa = await api.cargas.previaDeBorrado(carga.id);
  } catch (error) {
    if (error.codigo === 'ingesta_en_curso' && error.detalle?.trabajo_id) {
      await enCurso(error);
      return;
    }
    cuerpo$.innerHTML = alerta(error.message);
    return;
  }

  const motivos = previa.conservadas_por_motivo || [];
  cuerpo$.innerHTML = `
    <div class="alert alert-error"><strong>Es irreversible.</strong> Lo que se
      borra no se puede recuperar. Antes de borrar una carga real con muchos
      panelistas, conviene tener un respaldo reciente de las dos bases.</div>
    ${previa.borrado_en_curso ? alerta(
      'Esta carga tiene un borrado que quedó a medias. Confirmar lo reanuda '
      + 'desde donde quedó.', 'info') : ''}
    <table class="tabla-borrado">
      <tr><td>Personas vinculadas a la carga</td><td class="num">${previa.personas}</td></tr>
      <tr class="peligro"><td><strong>Se dan de baja</strong> — las creó esta
        carga y no tienen otra vida en el sistema</td>
        <td class="num"><strong>${previa.se_dan_de_baja}</strong></td></tr>
      <tr><td><strong>Se conservan</strong>, sin el vínculo ni sus respuestas
        de esta carga${motivos.length ? `<ul class="small">${motivos.map((m) =>
          `<li>${m.personas} · ${esc(m.etiqueta)}</li>`).join('')}</ul>` : ''}</td>
        <td class="num">${previa.se_conservan}</td></tr>
      <tr class="peligro"><td>Respuestas del estudio que se borran (de todas las
        personas)</td><td class="num">${previa.respuestas_a_borrar}</td></tr>
      <tr><td>Textos embebidos que se van con ellas (los compartidos con otro
        estudio se conservan)</td><td class="num">${previa.unidades_que_se_borran}</td></tr>
    </table>
    <p class="small">Las bajas siguen el camino de siempre: se avisa a los otros
      sistemas que usan la bóveda (COLOQUIO) para que borren lo suyo. El borrado
      queda registrado con quién lo hizo y estos números.</p>
    <div class="form-group">
      <label for="confirmar-nombre" class="etiqueta-literal">Para confirmar, escribí el nombre de la carga:
        <strong>${esc(carga.nombre)}</strong></label>
      <input type="text" class="finput" id="confirmar-nombre" autocomplete="off" />
    </div>
    <div id="borrado-alerta"></div>
    <div class="toolbar">
      <button class="btn btn-outline" id="cancelar-borrado">Cancelar</button>
      <span class="toolbar-spacer"></span>
      <button class="btn btn-danger" id="confirmar-borrado" disabled>Borrar la carga</button>
    </div>`;

  const nombre$ = $('#confirmar-nombre', caja);
  const boton$ = $('#confirmar-borrado', caja);
  nombre$.oninput = () => {
    boton$.disabled = nombre$.value.trim() !== carga.nombre.trim();
  };
  nombre$.focus();
  $('#cancelar-borrado', caja).onclick = cerrarModal;
  boton$.onclick = async () => {
    boton$.disabled = true;
    nombre$.disabled = true;
    $('#borrado-alerta', caja).innerHTML = `<div class="procesando" role="status">
      <span class="spinner spinner-sm"></span><div class="procesando-texto">
      <strong class="procesando-titulo">Borrando…</strong></div></div>`;
    try {
      const salida = await api.cargas.borrar(carga.id, nombre$.value);
      cerrarModal();
      if (salida.estado === 'terminado') {
        toast(`Se borró la carga «${carga.nombre}».`, 'ok');
        contexto.irA('cargas');
        return;
      }
      // Por la vía diferida: el avance por lotes, y al terminar, el listado.
      const { seguirTrabajo } = await import('./encuestas.js');
      contexto.irA('cargas');
      seguirTrabajo(salida.trabajo_id, { esCarga: true });
    } catch (error) {
      if (error.codigo === 'ingesta_en_curso' && error.detalle?.trabajo_id) {
        await enCurso(error);
        return;
      }
      $('#borrado-alerta', caja).innerHTML = alerta(error.message);
      nombre$.disabled = false;
      boton$.disabled = nombre$.value.trim() !== carga.nombre.trim();
    }
  };
}
