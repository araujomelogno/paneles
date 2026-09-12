/* Puntos, premios y canjes (R3.3 a R3.6).

   El saldo de un panelista es la suma de sus movimientos y nada más: no hay
   ningún campo «saldo» que esta pantalla pueda editar. Por eso lo que se ve
   acá es un extracto —movimientos, uno debajo del otro— y no un número
   suelto con un lápiz al lado.
*/

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, alerta, token, toast, modal,
  cerrarModal, leerFormulario, confirmar, fechaCorta, fechaHora,
} from '../ui.js';

let contexto = {};
let solapa = 'premios';
let panelActual = null;

const SOLAPAS = {
  premios: 'Catálogo de premios',
  canjes: 'Canjes',
  liquidacion: 'Liquidación de puntos',
  bonos: 'Bonos dirigidos',
};

const ESTADO_CANJE = {
  solicitado: ['aviso', 'Solicitado'],
  entregado: ['ok', 'Entregado'],
  cancelado: ['off', 'Cancelado'],
};

export async function render(main, ctx) {
  contexto = ctx;
  main.innerHTML = encabezado('Gamificación', 'puntos y premios',
    'Los puntos se ganan por participación de calidad. El saldo es la suma de los movimientos.') + `
    <div class="tabs" id="solapas">
      ${Object.entries(SOLAPAS).map(([id, etiqueta]) =>
        `<button class="tab ${id === solapa ? 'active' : ''}" data-solapa="${id}">${etiqueta}</button>`
      ).join('')}
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
    if (solapa === 'premios') return pintarPremios(cuerpo);
    if (solapa === 'canjes') return pintarCanjes(cuerpo);
    if (solapa === 'liquidacion') return pintarLiquidacion(cuerpo);
    return pintarBonos(cuerpo);
  } catch (error) {
    cuerpo.innerHTML = alerta(error.message);
  }
}

/* ── Catálogo ───────────────────────────────────────────────────── */

async function pintarPremios(cuerpo) {
  const { items } = await api.premios.listar();
  cuerpo.innerHTML = `
    <div class="card"><div class="card-head">
      <h3>Premios</h3><button class="btn btn-primary" id="nuevo">+ Premio</button>
    </div><div class="card-body" style="padding:0">
      ${items.length ? `<table class="tabla"><thead><tr>
        <th>Premio</th><th>Costo</th><th>Stock</th><th>Estado</th><th></th>
      </tr></thead><tbody>${items.map((p) => `<tr>
        <td><strong>${esc(p.nombre)}</strong>${p.descripcion ? `<div class="tenue">${esc(p.descripcion)}</div>` : ''}</td>
        <td>${p.costo_puntos} pts</td>
        <td>${p.stock === null ? '<span class="tenue">ilimitado</span>' : p.stock}</td>
        <td>${token(p.disponible ? 'disponible' : (p.activo ? 'sin stock' : 'inactivo'))}</td>
        <td><button class="btn btn-sm" data-editar="${p.id}">Editar</button></td>
      </tr>`).join('')}</tbody></table>` : vacio('Todavía no hay premios en el catálogo.', '🎁')}
    </div></div>`;
  $('#nuevo').onclick = () => formularioPremio();
  $$('[data-editar]').forEach((b) => {
    b.onclick = () => formularioPremio(items.find((p) => p.id === Number(b.dataset.editar)));
  });
}

function formularioPremio(premio) {
  modal({
    titulo: premio ? `Editar «${premio.nombre}»` : 'Premio nuevo',
    cuerpo: `
      <div class="form-group"><label>Nombre</label>
        <input class="finput" name="nombre" value="${esc(premio?.nombre ?? '')}"></div>
      <div class="form-group"><label>Descripción</label>
        <textarea class="finput" name="descripcion" rows="2">${esc(premio?.descripcion ?? '')}</textarea></div>
      <div class="grid-2">
        <div class="form-group"><label>Costo en puntos</label>
          <input class="finput" name="costo_puntos" type="number" min="1"
                 value="${premio?.costo_puntos ?? 100}"></div>
        <div class="form-group"><label>Stock (vacío = ilimitado)</label>
          <input class="finput" name="stock" type="number" min="0"
                 value="${premio?.stock ?? ''}"></div>
      </div>
      ${premio ? `<label class="check"><input type="checkbox" name="activo"
          ${premio.activo ? 'checked' : ''}> Activo</label>` : ''}`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Guardar', clase: 'btn-primary',
        onClick: async (contenedor) => {
          const datos = leerFormulario(contenedor);
          if (datos.stock === '') datos.stock = null;
          try {
            if (premio) await api.premios.editar(premio.id, datos);
            else await api.premios.crear(datos);
            cerrarModal(); toast('Guardado.', 'ok'); cargar();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}

/* ── Canjes ─────────────────────────────────────────────────────── */

async function pintarCanjes(cuerpo) {
  const { items } = await api.canjes.listar({});
  cuerpo.innerHTML = `
    <div class="card"><div class="card-head"><h3>Canjes</h3></div>
    <div class="card-body" style="padding:0">
      ${items.length ? `<table class="tabla"><thead><tr>
        <th>Persona</th><th>Premio</th><th>Puntos</th><th>Estado</th><th>Pedido</th><th></th>
      </tr></thead><tbody>${items.map((c) => `<tr>
        <td class="mono">${esc(c.id_persona.slice(0, 8))}…</td>
        <td>${esc(c.premio)}</td><td>${c.costo_puntos}</td>
        <td>${token(ESTADO_CANJE[c.estado][1])}</td>
        <td>${fechaCorta(c.creado_en)}</td>
        <td>${c.estado === 'solicitado' ? `
          <button class="btn btn-sm" data-entregar="${c.id}">Entregado</button>
          <button class="btn btn-sm btn-danger" data-cancelar="${c.id}">Cancelar</button>` : ''}</td>
      </tr>`).join('')}</tbody></table>` : vacio('Todavía no hay canjes.', '🎟️')}
    </div></div>`;

  $$('[data-entregar]').forEach((b) => {
    b.onclick = () => resolverCanje(Number(b.dataset.entregar), 'entregado');
  });
  $$('[data-cancelar]').forEach((b) => {
    b.onclick = async () => {
      const ok = await confirmar({
        titulo: 'Cancelar el canje',
        cuerpo: '<p>Los puntos se devuelven como un movimiento nuevo: el descuento original queda en el extracto, porque ocurrió.</p>',
        textoOk: 'Cancelar el canje',
      });
      if (ok) resolverCanje(Number(b.dataset.cancelar), 'cancelado');
    };
  });
}

async function resolverCanje(id, estadoNuevo) {
  try {
    await api.canjes.resolver(id, estadoNuevo);
    toast(estadoNuevo === 'entregado' ? 'Marcado como entregado.' : 'Cancelado y puntos devueltos.', 'ok');
    cargar();
  } catch (error) { toast(error.message, 'error'); }
}

/* ── Liquidación ────────────────────────────────────────────────── */

async function pintarLiquidacion(cuerpo) {
  const { items: paneles } = await api.paneles.listar();
  if (!paneles.length) { cuerpo.innerHTML = vacio('No hay paneles.', '📋'); return; }
  panelActual = paneles.some((p) => p.id === panelActual) ? panelActual : paneles[0].id;
  const { items: encuestas } = await api.encuestas.listar(panelActual);

  cuerpo.innerHTML = `
    ${alerta('Solo se liquida la participación de calidad: respondió y quedó en «ok». Una encuesta ya liquidada no vuelve a pagar.', 'info')}
    <div class="card"><div class="card-body">
      <div class="grid-3">
        <div class="form-group"><label>Panel</label>
          <select class="fselect" id="panel">${paneles.map((p) =>
            `<option value="${p.id}" ${p.id === panelActual ? 'selected' : ''}>${esc(p.nombre)}</option>`
          ).join('')}</select></div>
        <div class="form-group"><label>Encuesta</label>
          <select class="fselect" id="encuesta">${encuestas.map((e) =>
            `<option value="${e.id}">${esc(e.nombre)}</option>`).join('')}</select></div>
        <div class="form-group" style="align-self:end">
          <button class="btn btn-primary" id="liquidar" ${encuestas.length ? '' : 'disabled'}>Liquidar</button></div>
      </div>
    </div></div>
    <div id="resultado"></div>`;

  $('#panel').onchange = (e) => { panelActual = Number(e.target.value); cargar(); };
  if (encuestas.length) $('#liquidar').onclick = liquidar;
}

async function liquidar() {
  const encuestaId = Number($('#encuesta').value);
  $('#resultado').innerHTML = cargando('15vh');
  try {
    const r = await api.puntos.liquidar(encuestaId);
    $('#resultado').innerHTML = `
      <div class="card"><div class="card-head">
        <h3>${r.liquidados.length} liquidaciones · ${r.total_puntos} puntos</h3></div>
      <div class="card-body" style="padding:0">
        ${r.liquidados.length ? `<table class="tabla"><thead><tr>
          <th>Persona</th><th>Base</th><th>Bono</th><th>Total</th>
        </tr></thead><tbody>${r.liquidados.map((l) => `<tr>
          <td class="mono">${esc(l.id_persona.slice(0, 8))}…</td>
          <td>${l.base}</td><td>${l.bono || '—'}</td><td><strong>${l.puntos}</strong></td>
        </tr>`).join('')}</tbody></table>`
        : vacio('No quedaba nada por liquidar en esta encuesta.', '✅')}
      </div></div>`;
    toast(`${r.liquidados.length} liquidaciones.`, 'ok');
  } catch (error) {
    $('#resultado').innerHTML = alerta(error.message);
  }
}

/* ── Bonos ──────────────────────────────────────────────────────── */

async function pintarBonos(cuerpo) {
  const { items: paneles } = await api.paneles.listar();
  if (!paneles.length) { cuerpo.innerHTML = vacio('No hay paneles.', '📋'); return; }
  panelActual = paneles.some((p) => p.id === panelActual) ? panelActual : paneles[0].id;
  const { items } = await api.puntos.bonos(panelActual);

  cuerpo.innerHTML = `
    ${alerta('Un bono suma puntos extra a la participación de calidad de un segmento mientras está vigente. Vencido, deja de aplicarse y no toca lo ya otorgado.', 'info')}
    <div class="card"><div class="card-head">
      <div class="form-group" style="margin:0;min-width:260px"><label>Panel</label>
        <select class="fselect" id="panel">${paneles.map((p) =>
          `<option value="${p.id}" ${p.id === panelActual ? 'selected' : ''}>${esc(p.nombre)}</option>`
        ).join('')}</select></div>
      <button class="btn btn-primary" id="nuevo">+ Bono</button>
    </div><div class="card-body" style="padding:0">
      ${items.length ? `<table class="tabla"><thead><tr>
        <th>Segmento</th><th>Puntos extra</th><th>Desde</th><th>Hasta</th><th>Estado</th>
      </tr></thead><tbody>${items.map((b) => `<tr>
        <td>${token(`${b.dimension} = ${b.categoria}`)}</td>
        <td>+${b.puntos_extra}</td>
        <td>${fechaCorta(b.desde)}</td>
        <td>${b.hasta ? fechaCorta(b.hasta) : '<span class="tenue">sin fin</span>'}</td>
        <td>${token(b.vigente ? 'vigente' : 'vencido')}</td>
      </tr>`).join('')}</tbody></table>` : vacio('No hay bonos configurados.', '⭐')}
    </div></div>`;

  $('#panel').onchange = (e) => { panelActual = Number(e.target.value); cargar(); };
  $('#nuevo').onclick = formularioBono;
}

function formularioBono() {
  modal({
    titulo: 'Bono dirigido',
    cuerpo: `
      <div class="grid-2">
        <div class="form-group"><label>Dimensión</label>
          <select class="fselect" name="dimension">
            <option value="sexo">Sexo</option>
            <option value="tramo_etario">Tramo etario</option>
            <option value="localidad">Localidad</option>
          </select></div>
        <div class="form-group"><label>Categoría</label>
          <input class="finput" name="categoria" placeholder="M, 18-24, Salto…"></div>
        <div class="form-group"><label>Puntos extra</label>
          <input class="finput" name="puntos_extra" type="number" min="1" value="50"></div>
        <div class="form-group"><label>Vence (opcional)</label>
          <input class="finput" name="hasta" type="date"></div>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Crear', clase: 'btn-primary',
        onClick: async (contenedor) => {
          const datos = leerFormulario(contenedor);
          if (!datos.hasta) delete datos.hasta;
          try {
            await api.puntos.crearBono(panelActual, datos);
            cerrarModal(); toast('Bono creado.', 'ok'); cargar();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}
