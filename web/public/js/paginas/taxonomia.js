/* R-TEM.7 a R-TEM.11, R-TEM.14 — La taxonomía de temáticas.

   Dos niveles, área → temática, y nada más. La propone y la hace crecer
   Claude, pero **se guarda**: esta pantalla muestra la taxonomía guardada y
   todo cambio pasa por una aprobación explícita.

   - **Arranque** (R-TEM.8): una sola llamada propone áreas para las
     temáticas sin área. Se revisa acá —renombrar, mover, unir— y recién al
     confirmar se guarda.
   - **Reorganización** (R-TEM.10): Claude propone operaciones; cada una se
     aprueba o se descarta por separado. Nunca se aplica nada sola.
   - **Mover a mano**: la misma operación, sin pedirle nada a Claude.
   - **Temáticas sin personas** (R-TEM.14): las candidatas a quitar.
   - **Historial**: quién cambió qué y cuándo.

   Se abre desde la sección Cargas. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, cargando, toast, alerta, vacio, confirmar, fechaHora,
} from '../ui.js';

let contexto = {};
const puedeGestionar = () => ['admin', 'operaciones', 'analista'].includes(contexto.actor?.rol);
const puedeDesmarcar = () => contexto.actor?.rol === 'admin';
const marca = (x) => (x.es_especial ? ' <span class="chip-marca especial">especial</span>' : '');

export async function renderTaxonomia(main, ctx) {
  contexto = ctx;
  main.innerHTML = encabezado('Taxonomía', 'de temáticas',
    'Áreas que agrupan las temáticas. La propone Claude; se guarda solo lo que se aprueba.') + `
    <div class="toolbar" style="margin-bottom:1rem">
      <button class="btn btn-outline btn-sm" id="volver">← Cargas</button>
      <span class="toolbar-spacer"></span>
      ${puedeGestionar() ? `
        <button class="btn btn-outline btn-sm" id="proponer-arranque" style="display:none"></button>
        <button class="btn btn-outline btn-sm" id="reorganizar" style="display:none">Reorganizar taxonomía</button>` : ''}
    </div>
    <div id="tax-aviso"></div>
    <div id="tax-propuesta"></div>
    <div id="tax-actual">${cargando()}</div>
    <div id="tax-sin-personas"></div>
    <div id="tax-historial"></div>`;
  $('#volver').onclick = () => contexto.irA('cargas');
  if (puedeGestionar()) {
    $('#proponer-arranque').onclick = proponerArranque;
    $('#reorganizar').onclick = proponerReorganizacion;
  }
  await refrescar();
}

async function refrescar() {
  const [taxonomia, { items: sinPersonas }, { items: cambios }] = await Promise.all([
    api.tematicas.taxonomia(),
    api.tematicas.sinPersonas().catch(() => ({ items: [] })),
    api.tematicas.cambios().catch(() => ({ items: [] })),
  ]);
  if (puedeGestionar()) {
    const arranque = $('#proponer-arranque');
    arranque.style.display = taxonomia.sin_area.length ? '' : 'none';
    arranque.textContent = taxonomia.arrancada
      ? `Proponer área para ${taxonomia.sin_area.length} temática(s) sin área`
      : 'Proponer la taxonomía';
    $('#reorganizar').style.display = taxonomia.arrancada ? '' : 'none';
  }
  pintarActual(taxonomia);
  pintarSinPersonas(sinPersonas);
  pintarHistorial(cambios);
}

/* ── La taxonomía guardada ─────────────────────────────────────── */

function pintarActual(taxonomia) {
  const destino = $('#tax-actual');
  const areas = taxonomia.areas;
  if (!areas.length && !taxonomia.sin_area.length) {
    destino.innerHTML = vacio('Todavía no hay temáticas. Se agregan a las cargas.', '🗂️');
    return;
  }
  const selectorDeArea = (t, actual) => (puedeGestionar() ? `
    <select class="fselect fselect-sm" data-mover="${t.id}" title="Mover a otra área">
      <option value="">Mover a…</option>
      ${areas.filter((a) => a.id !== actual).map((a) =>
        `<option value="${a.id}">${esc(a.nombre)}</option>`).join('')}
      <option value="__nueva__">+ Área nueva…</option>
    </select>` : '');
  const filaTematica = (t, areaId) => `<tr>
      <td><span class="chip chip-tematica">${esc(t.nombre)}${marca(t)}</span>
        ${t.es_especial && !t.es_especial_propia ? '<span class="small muted">por su área</span>' : ''}</td>
      <td class="num">${t.cargas}</td>
      <td class="num">${t.personas ?? '<span class="muted">—</span>'}</td>
      <td class="right">${selectorDeArea(t, areaId)}</td></tr>`;
  const tabla = (tematicas, areaId) => `<div class="table-wrap"><table>
      <thead><tr><th>Temática</th><th class="num">Cargas</th><th class="num">Personas</th><th></th></tr></thead>
      <tbody>${tematicas.map((t) => filaTematica(t, areaId)).join('')
        || '<tr><td colspan="4" class="small muted">Sin temáticas.</td></tr>'}</tbody></table></div>`;

  destino.innerHTML = `
    ${!taxonomia.arrancada ? alerta('Todavía no hay áreas: las temáticas están en una '
      + 'lista plana. «Proponer la taxonomía» le pide a Claude una agrupación, que se '
      + 'revisa antes de guardar. Conviene hacerlo con varias cargas ya cargadas.', 'info') : ''}
    ${areas.map((a) => `<div class="card">
      <div class="card-header">
        <span class="card-header-title">${esc(a.nombre)}${marca(a)}</span>
        <span class="small muted">${esc(a.descripcion || '')}</span>
        ${puedeGestionar() ? `<label class="check small" title="Un área especial vuelve especiales a todas sus temáticas">
          <input type="checkbox" data-especial-area="${a.id}" ${a.es_especial ? 'checked' : ''}
            ${a.es_especial && !puedeDesmarcar() ? 'disabled' : ''} /> Especial</label>` : ''}
      </div>
      <div class="card-body tight">${tabla(a.tematicas, a.id)}</div>
    </div>`).join('')}
    ${taxonomia.sin_area.length ? `<div class="card">
      <div class="card-header"><span class="card-header-title">Sin área</span>
        <span class="small muted">temáticas que todavía no tienen área</span></div>
      <div class="card-body tight">${tabla(taxonomia.sin_area, null)}</div></div>` : ''}`;

  $$('[data-mover]', destino).forEach((select) => {
    select.onchange = async () => {
      if (!select.value) return;
      let operacion = { tipo: 'mover_tematica', tematica_id: Number(select.dataset.mover) };
      if (select.value === '__nueva__') {
        const nombre = (window.prompt('Nombre del área nueva (dos a cuatro palabras):') || '').trim();
        if (!nombre) { select.value = ''; return; }
        operacion.area_nueva = { nombre };
      } else {
        operacion.area_id = Number(select.value);
      }
      await aplicar(operacion);
    };
  });
  $$('[data-especial-area]', destino).forEach((caja) => {
    caja.onchange = async () => {
      const especial = caja.checked;
      const ok = await confirmar({
        titulo: especial ? 'Marcar el área como especial' : 'Quitar la marca de especial',
        cuerpo: `<p>${especial
          ? 'Todas sus temáticas pasan a ser especiales: dejan de ofrecerse como columna, '
            + 'no salen en exportaciones y filtrarlas queda restringido por rol.'
          : 'Sus temáticas vuelven a ofrecerse como columna (salvo las que son especiales '
            + 'por sí mismas).'}</p>`,
        textoOk: especial ? 'Marcar' : 'Quitar la marca', claseOk: 'btn-orange',
      });
      if (!ok) { caja.checked = !especial; return; }
      await aplicar({ tipo: 'marcar_area_especial', area_id: Number(caja.dataset.especialArea),
                      es_especial: especial });
    };
  });
}

async function aplicar(operacion, { silencioso = false } = {}) {
  try {
    const salida = await api.tematicas.aplicarOperacion(operacion);
    if (!silencioso) toast(salida.descripcion || 'Cambio aplicado.', 'ok');
    await refrescar();
    return true;
  } catch (error) {
    $('#tax-aviso').innerHTML = alerta(error.message);
    await refrescar();
    return false;
  }
}

/* ── R-TEM.8 · El arranque ─────────────────────────────────────── */

async function proponerArranque() {
  const destino = $('#tax-propuesta');
  destino.innerHTML = cargando('12vh');
  let propuesta;
  try {
    propuesta = await api.tematicas.proponerTaxonomia();
  } catch (error) {
    destino.innerHTML = alerta(error.message);
    return;
  }
  if (!propuesta.areas.length) {
    destino.innerHTML = alerta(propuesta.aviso || 'Claude no propuso áreas.', 'warn');
    return;
  }
  // El estado editable: las áreas propuestas y las temáticas que quedaron
  // sin ubicar. Nada de esto está guardado hasta «Confirmar».
  const estado = {
    areas: propuesta.areas.map((a, i) => ({
      clave: `a${i}`, id: a.id, nombre: a.nombre, descripcion: a.descripcion || '',
      es_especial: Boolean(a.podria_ser_especial), es_nueva: a.es_nueva,
      tematicas: [...a.tematicas],
    })),
    sinUbicar: [...propuesta.sin_ubicar],
  };
  const pintar = () => {
    const opciones = (actual) => estado.areas.filter((a) => a.clave !== actual)
      .map((a) => `<option value="${a.clave}">${esc(a.nombre)}</option>`).join('');
    destino.innerHTML = `<div class="card">
      <div class="card-header"><span class="card-header-title">Propuesta de taxonomía</span>
        <span class="small muted">${estado.areas.length} áreas · guía: entre
          ${propuesta.guia?.min_areas ?? 8} y ${propuesta.guia?.max_areas ?? 20}</span></div>
      <div class="card-body">
        <p class="small">Revisala antes de guardar: renombrá áreas, mové temáticas, uní
          dos áreas. La primera taxonomía condiciona las siguientes —las cargas nuevas se
          clasifican contra lo que exista—, así que vale la pena mirarla con cuidado.
          <strong>Nada se guardó todavía.</strong></p>
        ${estado.areas.map((a) => `<div class="area-propuesta">
          <div class="form-row">
            <div class="form-group"><label>Área ${a.es_nueva ? '<span class="chip-marca">nueva</span>' : ''}</label>
              <input type="text" data-nombre="${a.clave}" value="${esc(a.nombre)}" /></div>
            <div class="form-group"><label>Descripción</label>
              <input type="text" data-descripcion="${a.clave}" value="${esc(a.descripcion)}" /></div>
          </div>
          <div class="toolbar">
            <label class="check small"><input type="checkbox" data-especial="${a.clave}"
              ${a.es_especial ? 'checked' : ''} /> Especial
              <span class="muted">(salud, sustancias, ideología, religión, vida sexual)</span></label>
            <span class="toolbar-spacer"></span>
            <select class="fselect fselect-sm" data-unir="${a.clave}">
              <option value="">Unir con…</option>${opciones(a.clave)}</select>
          </div>
          <div class="tematicas-chips">${a.tematicas.map((t) => `
            <span class="chip chip-tematica">${esc(t.nombre)}
              <select class="fselect fselect-sm" data-mover-propuesta="${a.clave}:${t.id}">
                <option value="">mover…</option>${opciones(a.clave)}</select></span>`).join('')}</div>
        </div>`).join('')}
        ${estado.sinUbicar.length ? `<h4>Sin ubicar</h4>
          <div class="tematicas-chips">${estado.sinUbicar.map((t) => `
            <span class="chip chip-tematica">${esc(t.nombre)}
              <select class="fselect fselect-sm" data-ubicar="${t.id}">
                <option value="">ubicar en…</option>${opciones(null)}</select></span>`).join('')}</div>
          <p class="small muted">Las que queden sin ubicar siguen sin área.</p>` : ''}
        <div class="toolbar" style="margin-top:1rem">
          <button class="btn btn-outline" id="descartar-arranque">Descartar</button>
          <button class="btn btn-orange" id="confirmar-arranque">Confirmar y guardar</button>
        </div>
      </div></div>`;
    const leer = () => estado.areas.forEach((a) => {
      a.nombre = $(`[data-nombre="${a.clave}"]`, destino).value.trim() || a.nombre;
      a.descripcion = $(`[data-descripcion="${a.clave}"]`, destino).value.trim();
      a.es_especial = $(`[data-especial="${a.clave}"]`, destino).checked;
    });
    const area = (clave) => estado.areas.find((a) => a.clave === clave);
    $$('[data-unir]', destino).forEach((s) => {
      s.onchange = () => {
        if (!s.value) return;
        leer();
        const origen = area(s.dataset.unir);
        const destinoArea = area(s.value);
        destinoArea.tematicas.push(...origen.tematicas);
        destinoArea.es_especial = destinoArea.es_especial || origen.es_especial;
        estado.areas = estado.areas.filter((a) => a !== origen);
        pintar();
      };
    });
    $$('[data-mover-propuesta]', destino).forEach((s) => {
      s.onchange = () => {
        if (!s.value) return;
        leer();
        const [desde, id] = s.dataset.moverPropuesta.split(':');
        const t = area(desde).tematicas.find((x) => String(x.id) === id);
        area(desde).tematicas = area(desde).tematicas.filter((x) => x !== t);
        area(s.value).tematicas.push(t);
        pintar();
      };
    });
    $$('[data-ubicar]', destino).forEach((s) => {
      s.onchange = () => {
        if (!s.value) return;
        leer();
        const t = estado.sinUbicar.find((x) => String(x.id) === s.dataset.ubicar);
        estado.sinUbicar = estado.sinUbicar.filter((x) => x !== t);
        area(s.value).tematicas.push(t);
        pintar();
      };
    });
    $('#descartar-arranque', destino).onclick = () => { destino.innerHTML = ''; };
    $('#confirmar-arranque', destino).onclick = async () => {
      leer();
      try {
        await api.tematicas.guardarTaxonomia(estado.areas
          .filter((a) => a.tematicas.length)
          .map((a) => ({
            id: a.id || undefined, nombre: a.nombre, descripcion: a.descripcion,
            es_especial: a.es_especial, origen: 'propuesta_ia',
            tematicas: a.tematicas.map((t) => t.id),
          })));
        destino.innerHTML = '';
        toast('Taxonomía guardada.', 'ok');
        await refrescar();
      } catch (error) {
        $('#tax-aviso').innerHTML = alerta(error.message);
      }
    };
  };
  pintar();
}

/* ── R-TEM.10 · Reorganización ─────────────────────────────────── */

async function proponerReorganizacion() {
  const destino = $('#tax-propuesta');
  destino.innerHTML = cargando('12vh');
  let propuesta;
  try {
    propuesta = await api.tematicas.proponerReorganizacion();
  } catch (error) {
    destino.innerHTML = alerta(error.message);
    return;
  }
  if (propuesta.aviso) {
    destino.innerHTML = alerta(propuesta.aviso, 'warn');
    return;
  }
  const pendientes = propuesta.operaciones.map((o, i) => ({ ...o, clave: i, estado: 'pendiente' }));
  const pintar = () => {
    destino.innerHTML = `<div class="card">
      <div class="card-header"><span class="card-header-title">Reorganización propuesta</span>
        <span class="small muted">cada operación se aprueba o se descarta por separado</span></div>
      <div class="card-body tight">${pendientes.length ? `<div class="table-wrap"><table>
        <thead><tr><th>Operación</th><th>Por qué</th><th></th></tr></thead>
        <tbody>${pendientes.map((o) => `<tr class="${o.estado !== 'pendiente' ? 'muted' : ''}">
          <td><strong>${esc(o.descripcion)}</strong></td>
          <td class="small">${esc(o.motivo || '—')}</td>
          <td class="right" style="white-space:nowrap">${o.estado === 'pendiente' ? `
            <button class="btn btn-outline btn-sm" data-descartar="${o.clave}">Descartar</button>
            <button class="btn btn-orange btn-sm" data-aprobar="${o.clave}">Aprobar</button>`
            : `<span class="small">${o.estado === 'aplicada' ? '✓ aplicada' : o.estado}</span>`}</td>
        </tr>`).join('')}</tbody></table></div>`
        : '<p class="small muted" style="padding:1rem 1.5rem">Claude no propuso cambios: la taxonomía está bien como está.</p>'}
        ${propuesta.descartadas.length ? `<p class="small muted" style="padding:0.6rem 1.5rem">
          ${propuesta.descartadas.length} propuesta(s) nombraban cosas que no existen y se
          descartaron solas.</p>` : ''}
        <p class="small muted" style="padding:0.6rem 1.5rem">Fusionar temáticas es
          irreversible en la práctica: sus cargas, preguntas y panelistas pasan a la que
          queda. Todo cambio aplicado queda en el historial.</p>
      </div></div>`;
    $$('[data-descartar]', destino).forEach((b) => {
      b.onclick = () => { pendientes[b.dataset.descartar].estado = 'descartada'; pintar(); };
    });
    $$('[data-aprobar]', destino).forEach((b) => {
      b.onclick = async () => {
        const o = pendientes[b.dataset.aprobar];
        b.disabled = true;
        const { clave, estado, descripcion, ...operacion } = o;
        const ok = await aplicar(operacion);
        o.estado = ok ? 'aplicada' : 'no se pudo aplicar';
        pintar();
      };
    });
  };
  pintar();
}

/* ── R-TEM.14 · Las que no tiene nadie ─────────────────────────── */

function pintarSinPersonas(items) {
  const destino = $('#tax-sin-personas');
  if (!items.length) { destino.innerHTML = ''; return; }
  destino.innerHTML = `<div class="card">
    <div class="card-header"><span class="card-header-title">Temáticas sin personas</span>
      <span class="small muted">están en una carga y nadie de esa carga las tiene: probablemente sobran</span></div>
    <div class="card-body tight"><div class="table-wrap"><table>
      <thead><tr><th>Carga</th><th>Temática</th><th>Preguntas</th><th></th></tr></thead>
      <tbody>${items.map((i) => `<tr>
        <td>${esc(i.carga)}</td>
        <td><span class="chip chip-tematica">${esc(i.tematica)}</span></td>
        <td class="small">${i.preguntas.map((c) => `<code>${esc(c)}</code>`).join(' ')
          || '<span class="muted">ninguna</span>'}</td>
        <td class="right"><button class="btn btn-outline btn-sm" data-carga="${i.carga_id}">Ir a la carga</button></td>
      </tr>`).join('')}</tbody></table></div>
      <p class="small muted" style="padding:0.6rem 1.5rem">Suele ser una temática que salía
        de una variable que después se marcó como demográfica, o una carga que todavía se
        está procesando. Se quita desde el detalle de la carga.</p></div></div>`;
  $$('[data-carga]', destino).forEach((b) => {
    b.onclick = () => contexto.irA('cargas', { cargaId: Number(b.dataset.carga) });
  });
}

/* ── El historial ──────────────────────────────────────────────── */

const TIPOS = {
  arranque: 'Arranque', mover_tematica: 'Mover temática',
  fusionar_tematicas: 'Fusionar temáticas', renombrar_area: 'Renombrar área',
  fusionar_areas: 'Fusionar áreas', dividir_area: 'Dividir área',
  marcar_area_especial: 'Marca especial',
};

function pintarHistorial(cambios) {
  const destino = $('#tax-historial');
  if (!cambios.length) { destino.innerHTML = ''; return; }
  destino.innerHTML = `<div class="card">
    <div class="card-header"><span class="card-header-title">Historial de cambios</span>
      <span class="small muted">para entender por qué una temática está donde está</span></div>
    <div class="card-body tight"><div class="table-wrap"><table>
      <thead><tr><th>Cuándo</th><th>Quién</th><th>Qué</th></tr></thead>
      <tbody>${cambios.map((c) => `<tr>
        <td class="small">${esc(fechaHora(c.creado_en))}</td>
        <td class="small">${esc(c.actor || '—')}</td>
        <td><strong>${esc(TIPOS[c.tipo] || c.tipo)}</strong>
          <div class="small">${esc(c.detalle?.descripcion
            || (c.tipo === 'arranque'
              ? `${(c.detalle?.areas || []).length} área(s): ${(c.detalle?.areas || []).map((a) => a.area).join(', ')}`
              : ''))}</div>
          ${c.detalle?.motivo ? `<div class="small muted">${esc(c.detalle.motivo)}</div>` : ''}</td>
      </tr>`).join('')}</tbody></table></div></div></div>`;
}
