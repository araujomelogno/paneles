/* Composición y universo de referencia (R2.2, R2.3 + P1, R-ORG.2/3).

   Dos cosas en la misma pantalla, porque no se entienden por separado: la
   composición observada del panel y el universo con el que se la compara.

   Lo que la pantalla tiene que dejar claro es la diferencia entre «no hay
   brecha» y «no se puede calcular la brecha». Sin objetivo cargado, la
   composición es descriptiva y la brecha aparece explícitamente como no
   disponible: una columna vacía se leería como «está todo bien».

   R-ORG.2 — el conjunto ya no es siempre un panel: se elige el ámbito
   (todos los panelistas, un panel o una carga) y se muestra junto al
   resultado. «Todos» admite su propio universo de referencia; una carga no.
*/

import * as api from '../api.js';
import * as catalogo from '../catalogo.js';
import {
  $, $$, esc, encabezado, vacio, cargando, toast, modal, cerrarModal,
  leerFormulario, alerta, confirmar, fechaCorta,
} from '../ui.js';

let contexto = {};
let panelActual = null;
let cruceActual = null;

/* R-ORG.2 — sobre quién se calcula: «todos» (la bóveda entera, con y sin
   panel), un panel (lo de siempre) o una carga. Se elige antes de calcular y
   vuelve en la respuesta, que es lo que se muestra junto al resultado. */
let ambitoActual = null;
let cargaActual = null;
const AMBITOS = {
  todos: 'Todos los panelistas',
  panel: 'Un panel',
  carga: 'Una carga',
};

/* R3.14 — las dimensiones de cuota salen del catálogo de atributos, no de
   una lista escrita acá. Definir «nivel socioeconómico» en Configuración
   alcanza para poder fijarle un objetivo y ver su brecha. */
let catalogoDeAtributos = [];
const dimensiones = () => catalogo.categoricos(catalogoDeAtributos);
const etiqueta = (clave) => catalogo.etiquetaDe(catalogoDeAtributos, clave);

const pct = (n) => (n == null ? '—' : `${(n * 100).toFixed(1)} %`);
const rotuloCarga = (c) => [c.nombre, c.fecha_estudio ? fechaCorta(c.fecha_estudio) : null]
  .filter(Boolean).join(' · ');

export async function render(main, ctx) {
  contexto = ctx;
  const [{ items: paneles }, delCatalogo, { items: cargas }] = await Promise.all([
    api.paneles.listar(), catalogo.cargar(),
    api.cargas.listar().catch(() => ({ items: [] })),
  ]);
  catalogoDeAtributos = delCatalogo;
  const activos = paneles.filter((p) => p.estado === 'activo');
  panelActual = activos.some((p) => p.id === panelActual) ? panelActual : activos[0]?.id ?? null;
  cargaActual = cargas.some((c) => c.id === cargaActual) ? cargaActual : cargas[0]?.id ?? null;
  // Sin paneles activos ya no hay pantalla vacía: «todos» siempre existe.
  if (!ambitoActual || (ambitoActual === 'panel' && !panelActual)
      || (ambitoActual === 'carga' && !cargaActual)) {
    ambitoActual = panelActual ? 'panel' : 'todos';
  }

  main.innerHTML = encabezado('Composición', 'y brecha',
    'Cuánto se parece un conjunto de personas al universo que pretende representar.') + `
    <div class="card"><div class="card-body">
      <div class="form-row" style="margin:0">
        <div class="form-group" style="margin:0"><label>Ámbito</label>
          <select class="fselect" id="ambito">${Object.entries(AMBITOS).map(([clave, texto]) =>
            `<option value="${clave}" ${clave === ambitoActual ? 'selected' : ''}
              ${(clave === 'panel' && !activos.length) || (clave === 'carga' && !cargas.length)
                ? 'disabled' : ''}>${esc(texto)}</option>`).join('')}
          </select></div>
        <div class="form-group" style="margin:0" id="grupo-panel"><label>Panel</label>
          <select class="fselect" id="panel">${activos.map((p) =>
            `<option value="${p.id}" ${p.id === panelActual ? 'selected' : ''}>${esc(p.nombre)}</option>`
          ).join('')}</select></div>
        <div class="form-group" style="margin:0" id="grupo-carga"><label>Carga</label>
          <select class="fselect" id="carga">${cargas.map((c) =>
            `<option value="${c.id}" ${c.id === cargaActual ? 'selected' : ''}>${esc(rotuloCarga(c))}</option>`
          ).join('')}</select></div>
        <div class="form-group" style="margin:0"><label>Cruce de dos dimensiones</label>
          <select class="fselect" id="cruce">
            <option value="">Sin cruce</option>
            ${dimensiones().flatMap((a, i) => dimensiones().slice(i + 1).map((b) =>
              `<option value="${esc(a.clave)},${esc(b.clave)}">${esc(a.etiqueta)} × ${esc(b.etiqueta)}</option>`
            )).join('')}
          </select></div>
      </div>
    </div></div>
    <div id="cuerpo">${cargando()}</div>`;

  const mostrarSelectores = () => {
    $('#grupo-panel').style.display = ambitoActual === 'panel' ? '' : 'none';
    $('#grupo-carga').style.display = ambitoActual === 'carga' ? '' : 'none';
  };
  mostrarSelectores();
  $('#ambito').onchange = (e) => { ambitoActual = e.target.value; mostrarSelectores(); cargar(); };
  $('#panel').onchange = (e) => { panelActual = Number(e.target.value); cargar(); };
  $('#carga').onchange = (e) => { cargaActual = Number(e.target.value); cargar(); };
  $('#cruce').value = cruceActual ? cruceActual.join(',') : '';
  $('#cruce').onchange = (e) => {
    cruceActual = e.target.value ? e.target.value.split(',') : null;
    cargar();
  };
  await cargar();
}

const referenciaActual = () => (ambitoActual === 'panel' ? panelActual
  : ambitoActual === 'carga' ? cargaActual : null);

async function cargar() {
  const caja = $('#cuerpo');
  caja.innerHTML = cargando();
  try {
    const salida = await api.composicion.verAmbito(
      ambitoActual, referenciaActual(), { cruce: cruceActual });
    caja.innerHTML = pintar(salida);
    const boton = $('#cargar-objetivo');
    if (boton) boton.onclick = () => abrirObjetivo(salida);
    $$('[data-borrar-objetivo]', caja).forEach((b) => {
      b.onclick = () => borrarObjetivo(b.dataset.borrarObjetivo);
    });
  } catch (error) {
    caja.innerHTML = alerta(error.message);
  }
}

/* El ámbito, junto al resultado: qué conjunto se está mirando. Para una
   carga, con los datos del estudio. */
function pintarAmbito(ambito) {
  if (!ambito) return '';
  const detalle = ambito.tipo === 'carga'
    ? [ambito.fecha_estudio ? `estudio del ${fechaCorta(ambito.fecha_estudio)}` : null,
       ambito.publico_objetivo].filter(Boolean).join(' · ')
    : ambito.descripcion || '';
  return `<div class="ambito-elegido small" style="margin:0 0 1rem">
      <span class="badge badge-user">${esc(AMBITOS[ambito.tipo] || ambito.tipo)}</span>
      <strong>${esc(ambito.nombre || '')}</strong>
      ${detalle ? `<span class="muted"> — ${esc(detalle)}</span>` : ''}
    </div>`;
}

function pintar(salida) {
  const puedeGestionar = ['admin', 'operaciones'].includes(contexto.actor?.rol);
  const ambito = salida.ambito || { tipo: 'panel' };
  const esPanel = ambito.tipo === 'panel';
  const admite = salida.objetivo_admitido !== false;
  return `
    ${pintarAmbito(salida.ambito)}
    <div class="stat-grid">
      <div class="stat s-total"><label>${esPanel ? 'Miembros activos' : 'Personas'}</label>
        <strong>${salida.miembros}</strong></div>
      ${ambito.tipo === 'todos' ? `<div class="stat"><label>Sin ningún panel</label>
        <strong>${salida.sin_panel ?? '—'}</strong></div>` : ''}
      <div class="stat ${salida.objetivo_cargado ? 's-ok' : 's-warn'}">
        <label>Universo de referencia</label>
        <strong>${!admite ? 'no aplica' : salida.objetivo_cargado ? 'cargado' : 'sin cargar'}</strong></div>
    </div>

    ${(salida.avisos || []).map((a) => `<div class="alert alert-info">${esc(a)}</div>`).join('')}

    ${!admite ? `<div class="alert alert-warn">
      Una carga <strong>no tiene universo de referencia</strong>: es un hecho
      del pasado —lo que entró, entró— y no algo que se corrija reclutando. La
      composición es descriptiva y la brecha figura como no disponible, no
      como cero.</div>`
    : salida.objetivo_cargado ? '' : `<div class="alert alert-warn">
      Sin universo de referencia cargado la composición es <strong>solo
      descriptiva</strong>: la brecha no se puede calcular, no es que sea
      cero. ${puedeGestionar ? 'Cargá el objetivo para poder compararla.' : ''}
    </div>`}

    ${admite ? `<div class="toolbar" style="margin-bottom:1rem">
      <button class="btn btn-orange" id="cargar-objetivo"
        ${puedeGestionar ? '' : 'disabled title="Hace falta rol de operaciones o admin"'}>
        ${salida.objetivo_cargado ? 'Editar universo de referencia' : 'Cargar universo de referencia'}
        ${ambito.tipo === 'todos' ? ' de toda la bóveda' : ''}
      </button>
    </div>` : ''}

    ${salida.dimensiones.map((d) => pintarDimension(d, salida.miembros, puedeGestionar && admite)).join('')}
    ${salida.cruce ? pintarCruce(salida.cruce) : ''}`;
}

function pintarDimension(dimension, miembros, puedeGestionar) {
  const hay = dimension.brecha_disponible;
  return `<div class="card">
    <div class="card-header">
      <span class="card-header-title">${esc(etiqueta(dimension.dimension))}</span>
      <div class="toolbar">
        ${hay ? `<span class="small muted">disimilitud ${pct(dimension.disimilitud)}</span>
          ${puedeGestionar ? `<button class="btn btn-outline btn-sm btn-del"
            data-borrar-objetivo="${dimension.dimension}">Quitar objetivo</button>` : ''}`
        : '<span class="badge badge-off">brecha no disponible</span>'}
      </div>
    </div>
    <div class="card-body tight">
      ${hay ? '' : `<div class="small muted" style="padding:.5rem 1.5rem 0">
        ${esc(dimension.motivo_sin_brecha)}</div>`}
      <div class="table-wrap"><table>
        <thead><tr>
          <th>Categoría</th><th>Observados</th><th>% observado</th>
          <th>% objetivo</th><th>Brecha</th><th>Faltan / sobran</th>
        </tr></thead>
        <tbody>${dimension.categorias.map((c) => `<tr>
          <td class="td-strong">${esc(c.categoria)}</td>
          <td class="mono">${c.observados}</td>
          <td><div class="barra ${hay && Math.abs(c.brecha ?? 0) < 0.03 ? 'ok' : ''}">
                <span style="width:${Math.round((c.proporcion_observada || 0) * 100)}%"></span></div>
              <div class="small mono">${pct(c.proporcion_observada)}</div></td>
          <td class="mono">${pct(c.proporcion_objetivo)}</td>
          <td class="mono ${c.brecha == null ? 'muted'
            : (c.brecha < 0 ? 'brecha-falta' : c.brecha > 0 ? 'brecha-sobra' : '')}">
            ${c.brecha == null ? '—' : `${c.brecha > 0 ? '+' : ''}${(c.brecha * 100).toFixed(1)} pp`}</td>
          <td class="small">${c.faltan ? `faltan <strong>${c.faltan}</strong>`
            : c.sobran ? `sobran ${c.sobran}` : (hay ? 'calza' : '—')}</td>
        </tr>`).join('')}</tbody>
      </table></div>
      ${dimension.sin_dato ? `<div class="alert alert-info" style="margin:1rem 1.5rem 0">
        <strong>${dimension.sin_dato}</strong> persona(s) no tienen esta
        variable cargada y no se cuentan en ninguna categoría: los porcentajes
        de arriba son sobre los <strong>${dimension.con_dato}</strong> que sí
        la tienen. Contarlos como una categoría más haría que la brecha de las
        demás mienta.</div>` : ''}
    </div>
  </div>`;
}

function pintarCruce(cruce) {
  const [a, b] = cruce.dimensiones;
  const filas = [...new Set(cruce.celdas.map((c) => c[a]))].sort();
  const columnas = [...new Set(cruce.celdas.map((c) => c[b]))].sort();
  const valor = {};
  cruce.celdas.forEach((c) => { valor[`${c[a]}|${c[b]}`] = c.observados; });

  return `<div class="card">
    <div class="card-header">
      <span class="card-header-title">
        ${esc(etiqueta(a))} × ${esc(etiqueta(b))}</span>
      <span class="badge badge-off">descriptivo</span>
    </div>
    <div class="card-body tight">
      <div class="small muted" style="padding:.5rem 1.5rem 0">
        ${esc(cruce.motivo_sin_brecha)} Las celdas vacías son los huecos que
        las marginales esconden.
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>${esc(etiqueta(a))}</th>
          ${columnas.map((c) => `<th>${esc(c)}</th>`).join('')}<th>Total</th></tr></thead>
        <tbody>${filas.map((f) => {
          const total = columnas.reduce((s, c) => s + (valor[`${f}|${c}`] || 0), 0);
          return `<tr><td class="td-strong">${esc(f)}</td>
            ${columnas.map((c) => {
              const n = valor[`${f}|${c}`] || 0;
              return `<td class="mono ${n ? '' : 'td-muted'}">${n || '·'}</td>`;
            }).join('')}
            <td class="mono td-strong">${total}</td></tr>`;
        }).join('')}</tbody>
      </table></div>
    </div>
  </div>`;
}

/* ── Carga del universo de referencia ───────────────────────────── */

function abrirObjetivo(salida) {
  const actuales = {};
  salida.dimensiones.forEach((d) => {
    d.categorias.forEach((c) => {
      if (c.proporcion_objetivo != null) {
        actuales[`${d.dimension}|${c.categoria}`] = c.proporcion_objetivo;
      }
    });
  });
  const observadas = {};
  salida.dimensiones.forEach((d) => {
    observadas[d.dimension] = d.categorias.map((c) => c.categoria);
  });

  const dimension = (dimensiones()[0] || {}).clave || 'sexo';
  const caja = modal({
    titulo: 'Universo de referencia',
    ancho: '680px',
    cuerpo: `
      ${ambitoActual === 'todos' ? `<div class="alert alert-warn">
        Este es el universo de referencia de <strong>toda la bóveda</strong>,
        no el de un panel: se compara contra todas las personas, con y sin
        panel. El objetivo de cada panel es otro y no se toca.</div>` : ''}
      <div class="alert alert-info">
        Se carga <strong>una dimensión a la vez</strong>, en proporciones que
        sumen 1. Una dimensión que suma 0,8 no es un universo incompleto:
        haría que todas sus brechas estén mal, así que se rechaza.
      </div>
      <div class="form-group"><label>Dimensión</label>
        <select class="fselect" id="obj-dimension">
          ${dimensiones().map((a) =>
            `<option value="${esc(a.clave)}">${esc(a.etiqueta)}</option>`).join('')}
        </select></div>
      <div id="obj-categorias"></div>
      <div class="fila-criterio"><div>Suma de las proporciones</div>
        <span id="obj-suma" class="mono">0.000</span></div>
      <div class="toolbar">
        <button class="btn btn-outline btn-sm" id="obj-agregar">+ Categoría</button>
        <button class="btn btn-outline btn-sm" id="obj-desde-panel">
          Precargar con lo observado</button>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Guardar', clase: 'btn-orange', onClick: guardarObjetivo },
    ],
  });

  const pintarCategorias = (dim) => {
    const propias = Object.entries(actuales)
      .filter(([clave]) => clave.startsWith(`${dim}|`))
      .map(([clave, valor]) => [clave.split('|')[1], valor]);
    // Las categorías canónicas del atributo. Antes estaban escritas acá para
    // sexo y tramo etario, y para localidad se caía a «lo que haya en el
    // panel»; ahora las tres salen del mismo lugar, que es el catálogo.
    const delCatalogo = catalogo.categoriasDe(catalogoDeAtributos, dim)
      .map((c) => c.clave);
    const sugeridas = delCatalogo.length ? delCatalogo
      : (observadas[dim] || []).filter((c) => c !== '(sin dato)');
    const lista = propias.length ? propias
      : sugeridas.map((c) => [c, 0]);
    $('#obj-categorias', caja).innerHTML = lista.map(([cat, prop]) => fila(cat, prop)).join('');
    enganchar();
  };

  const fila = (categoria = '', proporcion = 0) => `
    <div class="pregunta-fila" data-fila>
      <input type="text" data-cat placeholder="Categoría" value="${esc(categoria)}" />
      <input type="number" data-prop step="0.01" min="0" max="1"
             placeholder="0.50" value="${proporcion || ''}" />
      <button class="btn btn-outline btn-sm btn-del" data-quitar>×</button>
    </div>`;

  const recalcular = () => {
    const suma = $$('[data-prop]', caja)
      .reduce((s, i) => s + (Number(i.value) || 0), 0);
    const nodo = $('#obj-suma', caja);
    nodo.textContent = suma.toFixed(3);
    nodo.className = `mono ${Math.abs(suma - 1) <= 0.005 ? '' : 'brecha-falta'}`;
  };

  const enganchar = () => {
    $$('[data-prop]', caja).forEach((i) => { i.oninput = recalcular; });
    $$('[data-quitar]', caja).forEach((b) => {
      b.onclick = () => { b.closest('[data-fila]').remove(); recalcular(); };
    });
    recalcular();
  };

  $('#obj-dimension', caja).onchange = (e) => pintarCategorias(e.target.value);
  $('#obj-agregar', caja).onclick = () => {
    $('#obj-categorias', caja).insertAdjacentHTML('beforeend', fila());
    enganchar();
  };
  $('#obj-desde-panel', caja).onclick = () => {
    const dim = $('#obj-dimension', caja).value;
    const d = salida.dimensiones.find((x) => x.dimension === dim);
    if (!d || !salida.miembros) { toast('No hay observados para precargar.', 'err'); return; }
    $('#obj-categorias', caja).innerHTML = d.categorias
      .filter((c) => c.categoria !== '(sin dato)')
      .map((c) => fila(c.categoria, Number(c.proporcion_observada.toFixed(2)))).join('');
    enganchar();
    toast('Precargado con la composición actual. Ajustalo al universo real.', 'ok');
  };

  pintarCategorias(dimension);
}

async function guardarObjetivo(caja) {
  const dimension = $('#obj-dimension', caja).value;
  const objetivos = $$('[data-fila]', caja).map((fila) => ({
    dimension,
    categoria: $('[data-cat]', fila).value.trim(),
    proporcion: Number($('[data-prop]', fila).value),
  })).filter((o) => o.categoria);

  if (!objetivos.length) { toast('Cargá al menos una categoría.', 'err'); return; }
  try {
    if (ambitoActual === 'todos') await api.composicion.cargarObjetivoDeTodos(objetivos);
    else await api.composicion.cargarObjetivo(panelActual, objetivos);
    cerrarModal();
    toast(`Universo de referencia de «${etiqueta(dimension)}» guardado.`, 'ok');
    await cargar();
  } catch (error) {
    toast(error.detalle?.suma != null
      ? `Las proporciones suman ${error.detalle.suma} y tienen que sumar 1.`
      : error.message, 'err');
  }
}

async function borrarObjetivo(dimension) {
  const ok = await confirmar({
    titulo: `Quitar el objetivo de ${etiqueta(dimension)}`,
    cuerpo: `<p>La composición de esa dimensión vuelve a ser descriptiva y la
      brecha deja de estar disponible.</p>`,
    textoOk: 'Quitar',
  });
  if (!ok) return;
  try {
    if (ambitoActual === 'todos') await api.composicion.borrarObjetivoDeTodos(dimension);
    else await api.composicion.borrarObjetivo(panelActual, dimension);
    toast('Objetivo quitado.', 'ok');
    await cargar();
  } catch (error) {
    toast(error.message, 'err');
  }
}
