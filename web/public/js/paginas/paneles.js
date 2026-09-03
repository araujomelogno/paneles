/* Paneles — listado, creación y membresías N:M (R1.4). */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, consentimientos, token, vacio, cargando, toast,
  modal, cerrarModal, leerFormulario, confirmar, activarTokens, fechaCorta,
  estado, alerta,
} from '../ui.js';

let contexto = {};
let panelAbierto = null;

export async function render(main, ctx) {
  contexto = ctx;
  if (ctx.contexto?.panelId) return renderDetalle(main, ctx.contexto.panelId);
  panelAbierto = null;

  const { items } = await api.paneles.listar();

  main.innerHTML = encabezado('Paneles', 'y membresías',
    'Una persona puede pertenecer a varios paneles sin duplicarse.') + `
    <div class="card">
      <div class="card-header"><span class="card-header-title">Paneles</span></div>
      <div class="card-body tight">
        ${items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Panel</th><th>Miembros</th><th>Encuestas</th><th>Estado</th><th>Creado</th><th></th></tr></thead>
          <tbody>${items.map((p) => `
            <tr>
              <td><div class="td-strong">${esc(p.nombre)}</div>
                <div class="td-muted small">${esc(p.descripcion || '—')}</div></td>
              <td class="mono">${p.miembros}</td>
              <td class="mono">${p.encuestas ?? 0}</td>
              <td>${estado(p.estado)}</td>
              <td class="small">${fechaCorta(p.creado_en)}</td>
              <td class="right"><button class="btn btn-outline btn-sm" data-panel="${p.id}">Abrir</button></td>
            </tr>`).join('')}</tbody></table></div>`
          : vacio('Todavía no hay paneles. Creá el primero.', '📋')}
      </div>
    </div>`;

  $('#ph-acciones').innerHTML = `<button class="btn btn-orange" id="nuevo">+ Nuevo panel</button>`;
  $('#nuevo').onclick = abrirNuevo;
  $$('[data-panel]', main).forEach((boton) => {
    boton.onclick = () => contexto.irA('paneles', { panelId: Number(boton.dataset.panel) });
  });
}

function abrirNuevo() {
  modal({
    titulo: 'Nuevo panel',
    cuerpo: `
      <div id="panel-alerta"></div>
      <div class="form-group"><label>Nombre</label>
        <input type="text" name="nombre" placeholder="Panel Nacional" /></div>
      <div class="form-group"><label>Descripción</label>
        <textarea class="finput" name="descripcion" placeholder="Para qué es este panel."></textarea></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Crear', clase: 'btn-orange', onClick: async (caja) => {
          const datos = leerFormulario(caja);
          if (!datos.nombre) {
            $('#panel-alerta', caja).innerHTML = alerta('El panel necesita un nombre.');
            return;
          }
          try {
            await api.paneles.crear(datos.nombre, datos.descripcion);
            cerrarModal();
            toast('Panel creado.', 'ok');
            contexto.irA('paneles');
          } catch (error) {
            $('#panel-alerta', caja).innerHTML = alerta(error.message);
          }
        } },
    ],
  });
}

/* ── Detalle: miembros ──────────────────────────────────────────── */

async function renderDetalle(main, panelId) {
  panelAbierto = panelId;
  main.innerHTML = cargando();
  const panel = await api.paneles.ver(panelId);

  main.innerHTML = encabezado(panel.nombre, '', panel.descripcion || 'Miembros del panel.') + `
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">Miembros</span>
        <div class="toolbar">
          <select class="fselect" id="f-estado" style="width:auto">
            <option value="activo">Activos</option>
            <option value="baja">De baja</option>
          </select>
          <button class="btn btn-orange btn-sm" id="agregar">+ Agregar panelistas</button>
        </div>
      </div>
      <div class="card-body tight"><div id="miembros">${cargando('20vh')}</div></div>
    </div>`;

  $('#ph-acciones').innerHTML = `
    <button class="btn btn-outline" id="volver">‹ Todos los paneles</button>
    <button class="btn btn-dark" id="encuestas">Ver encuestas</button>`;
  $('#volver').onclick = () => contexto.irA('paneles');
  $('#encuestas').onclick = () => contexto.irA('encuestas', { panelId });
  $('#agregar').onclick = () => abrirAgregar(panelId);
  $('#f-estado').onchange = (e) => cargarMiembros(panelId, e.target.value);

  await cargarMiembros(panelId, 'activo');
}

async function cargarMiembros(panelId, filtroEstado) {
  const contenedor = $('#miembros');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('20vh');
  const { items } = await api.paneles.miembros(panelId, filtroEstado);

  if (!items.length) {
    contenedor.innerHTML = vacio(
      filtroEstado === 'activo' ? 'El panel no tiene miembros activos.' : 'Nadie de baja.', '👥');
    return;
  }

  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Panelista</th><th>Demografía</th><th>Consentimiento</th>
        <th>Alta</th><th>Identificador</th><th></th></tr></thead>
      <tbody>${items.map((m) => `
        <tr>
          <td><div class="td-strong">${esc(m.nombre || 'Sin nombre')}</div>
            <div class="td-muted small">${esc(m.email || '—')}</div></td>
          <td class="small">${esc([m.sexo, m.tramo_etario, m.localidad].filter(Boolean).join(' · ') || '—')}</td>
          <td>${consentimientos(m)}</td>
          <td class="small">${fechaCorta(m.fecha_alta)}</td>
          <td>${token(m.id_persona)}</td>
          <td class="right td-actions" style="justify-content:flex-end">
            <button class="btn btn-outline btn-sm" data-ficha="${esc(m.id_persona)}">Ficha</button>
            ${m.estado === 'activo'
              ? `<button class="btn btn-outline btn-del btn-sm" data-quitar="${esc(m.id_persona)}"
                   data-nombre="${esc(m.nombre || '')}">Quitar</button>` : ''}
          </td>
        </tr>`).join('')}</tbody></table></div>
    <div class="paginacion"><span class="p-info">${items.length} ${filtroEstado === 'activo' ? 'miembros activos' : 'de baja'}</span></div>`;

  activarTokens(contenedor);
  $$('[data-ficha]', contenedor).forEach((boton) => {
    boton.onclick = () => contexto.irA('panelistas', { idPersona: boton.dataset.ficha });
  });
  $$('[data-quitar]', contenedor).forEach((boton) => {
    boton.onclick = async () => {
      const ok = await confirmar({
        titulo: 'Quitar del panel',
        cuerpo: `<p>Se marca la membresía de <strong>${esc(boton.dataset.nombre || 'esta persona')}</strong>
          como <em>baja</em>. La persona no se borra, y sus otras membresías no se tocan.</p>`,
        textoOk: 'Quitar', claseOk: 'btn-danger',
      });
      if (!ok) return;
      try {
        await api.paneles.quitarMiembro(panelId, boton.dataset.quitar);
        toast('Membresía dada de baja.', 'ok');
        cargarMiembros(panelId, $('#f-estado').value);
      } catch (error) { toast(error.message, 'err'); }
    };
  });
}

/* ── Agregar miembros ───────────────────────────────────────────── */

async function abrirAgregar(panelId) {
  const caja = modal({
    titulo: 'Agregar panelistas al panel',
    ancho: '620px',
    cuerpo: `
      <div class="form-group">
        <label>Buscar panelistas</label>
        <div class="buscador" style="max-width:none"><input type="text" id="buscar" placeholder="Nombre, email o documento…" /></div>
      </div>
      <div id="lista" class="pick-list">${cargando('12vh')}</div>
      <div class="field-hint" style="margin-top:0.6rem">
        Agregar a alguien que ya es miembro no lo duplica: la operación es idempotente.
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Agregar seleccionados', clase: 'btn-orange', onClick: async (c) => {
          const ids = $$('#lista input:checked', c).map((i) => i.value);
          if (!ids.length) { toast('No seleccionaste a nadie.', 'err'); return; }
          try {
            await api.paneles.agregarMiembros(panelId, ids);
            cerrarModal();
            toast(`${ids.length} panelista(s) agregados.`, 'ok');
            cargarMiembros(panelId, $('#f-estado').value);
          } catch (error) { toast(error.message, 'err'); }
        } },
    ],
  });

  const cargar = async (q) => {
    const lista = $('#lista', caja);
    lista.innerHTML = cargando('12vh');
    const { items } = await api.panelistas.listar({ q, limite: 60 });
    const miembros = new Set((await api.paneles.miembros(panelId)).items.map((m) => m.id_persona));
    const disponibles = items.filter((p) => !miembros.has(p.id_persona));
    lista.innerHTML = disponibles.length ? disponibles.map((p) => `
      <label class="pick">
        <input type="checkbox" value="${esc(p.id_persona)}" />
        <div class="pick-avatar">${esc((p.nombre || '?').slice(0, 2).toUpperCase())}</div>
        <div style="flex:1">
          <div class="pick-name">${esc(p.nombre || 'Sin nombre')}</div>
          <div class="pick-email">${esc(p.email || p.documento || '—')}</div>
        </div>
        ${consentimientos(p)}
      </label>`).join('')
      : `<div class="empty-state">Ningún panelista disponible para agregar.</div>`;
  };

  let temporizador;
  $('#buscar', caja).oninput = (e) => {
    clearTimeout(temporizador);
    temporizador = setTimeout(() => cargar(e.target.value), 280);
  };
  await cargar('');
}
