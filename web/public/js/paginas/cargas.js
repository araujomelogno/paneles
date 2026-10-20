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
  fechaCorta, fechaHora,
} from '../ui.js';
import { editorDeTematicas, chipsDeTematicas } from '../tematicas.js';

let contexto = {};
let filtro = { q: '', tematica_id: '' };
const POR_PAGINA = 25;

// Quien puede corregir los datos de una carga puede editar sus temáticas
// (permiso `ingestar`). Es una copia para no ofrecer un botón que da 403;
// la autoridad es el servidor.
const puedeEditar = () => ['admin', 'operaciones', 'analista'].includes(contexto.actor?.rol);

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
        <th class="num">Individuos</th><th>Temáticas</th><th></th>
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
                ${c.personas_reutilizadas ?? 0} reutilizados</div></td>
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
}

/* ── R-CAR.2 · El detalle ────────────────────────────────────────── */

async function renderDetalle(main, cargaId) {
  const [carga, { items: vocabulario }] = await Promise.all([
    api.cargas.ver(cargaId),
    api.tematicas.listar().catch(() => ({ items: [] })),
  ]);
  const dato = (valor) => (valor ? esc(valor) : '<span class="muted">sin dato</span>');

  main.innerHTML = encabezado(carga.nombre, '', 'Detalle de la carga') + `
    <div class="toolbar" style="margin-bottom:1rem">
      <button class="btn btn-outline btn-sm" id="volver">← Todas las cargas</button>
      <button class="btn btn-outline btn-sm" id="composicion">Ver su composición</button>
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
              <div class="etiqueta">reutilizados (ya existían)</div></div>
          </div>
          <p class="small muted" style="margin-top:0.6rem">Ninguno queda en un panel
            por venir de una carga. Quien se cargó antes de la versión R-ORG no
            dejó constancia de su carga y no figura acá.</p>
        </div></div>
    </div>

    <div class="card">
      <div class="card-header"><span class="card-header-title">Temáticas incorporadas</span>
        ${puedeEditar() ? `<div class="toolbar">
          <button class="btn btn-outline btn-sm" id="proponer">Proponer temáticas</button>
          <button class="btn btn-orange btn-sm" id="guardar-tematicas">Guardar temáticas</button>
        </div>` : ''}</div>
      <div class="card-body">
        <div id="tematicas-alerta"></div>
        <div id="tematicas">${puedeEditar() ? '' : chipsDeTematicas(carga.tematicas)}</div>
        <p class="small muted" style="margin-top:0.6rem">Describen <strong>la
          carga</strong>, no a cada persona: que cubra un tema no quiere decir que
          todos sus individuos hayan respondido esas preguntas.</p>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><span class="card-header-title">Panelistas cargados</span></div>
      <div class="card-body tight"><div id="panelistas">${cargando('15vh')}</div></div>
    </div>`;

  $('#volver').onclick = () => contexto.irA('cargas');
  // El ámbito «una carga» de la composición (R-ORG) ya existe: se abre ahí.
  $('#composicion').onclick = () => contexto.irA('composicion', { cargaId: carga.id });

  if (puedeEditar()) {
    let editor = editorDeTematicas($('#tematicas'), {
      vocabulario, iniciales: carga.tematicas,
    });
    $('#guardar-tematicas').onclick = async (e) => {
      const boton = e.currentTarget;
      boton.disabled = true;
      try {
        const { tematicas } = await api.cargas.guardarTematicas(carga.id, editor.valor());
        const { items } = await api.tematicas.listar().catch(() => ({ items: vocabulario }));
        editor = editorDeTematicas($('#tematicas'), { vocabulario: items, iniciales: tematicas });
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
          vocabulario, iniciales: editor.valor(),
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
