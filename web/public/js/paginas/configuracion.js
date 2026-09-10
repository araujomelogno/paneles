/* Configuración — gestión de usuarios de la app (R2.12).

   Esta pantalla es escalada de privilegios por diseño: desde acá se le da a
   una persona acceso a toda la bóveda. La interfaz lo dice en vez de
   esconderlo, y las tres barandas del backend están reflejadas acá para que
   no haya que descubrirlas por el error:

     * solo `admin` entra (la solapa no aparece para los demás);
     * nadie se puede sacar su propio rol de admin ni desactivarse: los
       controles vienen deshabilitados sobre uno mismo;
     * cada alta, cambio de rol y desactivación queda en la auditoría, que se
       ve en la misma pantalla.

   La clave inicial no se muestra de forma persistente: el alta devuelve un
   enlace de restablecimiento que se ve una vez y no se puede volver a
   consultar.
*/

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, toast, modal, cerrarModal,
  leerFormulario, alerta, confirmar, fechaHora,
} from '../ui.js';

let contexto = {};

const ROLES = {
  admin: {
    etiqueta: 'Administración',
    detalle: 'Todo, incluida la gestión de usuarios.',
  },
  operaciones: {
    etiqueta: 'Responsable de panel',
    detalle: 'Enrola, arma paneles, convoca y reidentifica.',
  },
  analista: {
    etiqueta: 'Analista',
    detalle: 'Fieldea, ingesta y consulta. No enrola ni da de baja.',
  },
  dpo: {
    etiqueta: 'Cumplimiento / DPO',
    detalle: 'Retiros de consentimiento, bajas y auditorías.',
  },
};

const ACCIONES = {
  alta: 'Alta', cambio_rol: 'Cambio de rol', desactivacion: 'Desactivación',
  reactivacion: 'Reactivación', actualizacion: 'Actualización',
};

export async function render(main, ctx) {
  contexto = ctx;

  if (ctx.actor?.rol !== 'admin') {
    main.innerHTML = encabezado('Configuración', '', '') + `
      <div class="info-card"><div class="ic-icon">🔒</div>
        <h2>Solo administración</h2>
        <p>La gestión de usuarios da acceso a toda la bóveda, así que es una
        capacidad aparte: la tiene únicamente el rol de administración.</p>
      </div>`;
    return;
  }

  main.innerHTML = encabezado('Configuración', 'usuarios',
    'Quién entra a la app y con qué rol.') + `
    <div class="alert alert-warn">
      Dar de alta a alguien con rol de administración le da acceso a toda la
      bóveda, incluida la PII de los panelistas. Cada alta, cambio de rol y
      desactivación queda registrada con tu usuario y la fecha.
    </div>
    <div id="cuerpo">${cargando()}</div>`;

  await cargar();
}

async function cargar() {
  const caja = $('#cuerpo');
  caja.innerHTML = cargando();
  try {
    const [padron, auditoria] = await Promise.all([
      api.usuarios.listar(),
      api.usuarios.auditoria().catch(() => ({ items: [] })),
    ]);
    caja.innerHTML = pintar(padron, auditoria);
    $('#nuevo').onclick = abrirAlta;
    $$('[data-rol]', caja).forEach((select) => {
      select.onchange = () => cambiarRol(select.dataset.rol, select.value, select);
    });
    $$('[data-estado]', caja).forEach((boton) => {
      boton.onclick = () => cambiarEstado(boton.dataset.estado, boton.dataset.activar === '1');
    });
  } catch (error) {
    caja.innerHTML = alerta(error.message);
  }
}

function pintar(padron, auditoria) {
  const propio = contexto.actor?.uid;
  return `
    <div class="stat-grid">
      <div class="stat s-total"><label>Usuarios</label><strong>${padron.total}</strong></div>
      <div class="stat s-ok"><label>Activos</label>
        <strong>${padron.items.filter((u) => u.activo).length}</strong></div>
      <div class="stat s-warn"><label>Desactivados</label>
        <strong>${padron.items.filter((u) => !u.activo).length}</strong></div>
    </div>

    <div class="toolbar" style="margin-bottom:1rem">
      <button class="btn btn-orange" id="nuevo">+ Dar de alta un usuario</button>
    </div>

    <div class="card">
      <div class="card-header"><span class="card-header-title">Padrón</span>
        <span class="small muted">Un cambio de rol vale desde la próxima operación.</span></div>
      <div class="card-body tight">
        ${padron.items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Persona</th><th>Rol</th><th>Estado</th><th></th></tr></thead>
          <tbody>${padron.items.map((u) => fila(u, propio)).join('')}</tbody>
        </table></div>` : vacio(
          'El padrón está vacío. El primer administrador se da de alta con '
          + 'scripts/alta_usuario.js.', '👥')}
      </div>
    </div>

    <div class="card">
      <div class="card-header"><span class="card-header-title">Auditoría</span>
        <span class="small muted">Quién hizo qué y cuándo. Solo se agrega.</span></div>
      <div class="card-body tight">
        ${auditoria.items?.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Cuándo</th><th>Acción</th><th>Sobre</th><th>Rol</th><th>Autor</th></tr></thead>
          <tbody>${auditoria.items.map((r) => `<tr>
            <td class="small">${fechaHora(r.creado_en)}</td>
            <td><span class="badge">${esc(ACCIONES[r.accion] || r.accion)}</span></td>
            <td class="small">${esc(r.email_objetivo || r.uid_objetivo)}</td>
            <td class="small">${r.rol_anterior && r.rol_anterior !== r.rol_nuevo
              ? `${esc(r.rol_anterior)} → ${esc(r.rol_nuevo || '')}`
              : esc(r.rol_nuevo || '—')}</td>
            <td class="small">${esc(r.actor_email || r.actor_uid)}</td>
          </tr>`).join('')}</tbody>
        </table></div>` : vacio('Todavía no hay movimientos registrados.', '📜')}
      </div>
    </div>`;
}

function fila(usuario, propio) {
  const esUnoMismo = usuario.uid === propio;
  const bloqueaRol = esUnoMismo && usuario.rol === 'admin';
  return `<tr>
    <td><div class="td-strong">${esc(usuario.nombre || '—')}
          ${esUnoMismo ? '<span class="badge badge-user">vos</span>' : ''}</div>
        <div class="td-muted small">${esc(usuario.email || '')}</div></td>
    <td>
      <select class="fselect" data-rol="${esc(usuario.uid)}" ${bloqueaRol ? 'disabled' : ''}
        title="${bloqueaRol
          ? 'No podés sacarte tu propio rol de administración: si el último admin se degrada, no queda nadie que pueda dar de alta a nadie.'
          : ''}">
        ${Object.entries(ROLES).map(([clave, r]) =>
          `<option value="${clave}" ${clave === usuario.rol ? 'selected' : ''}>${esc(r.etiqueta)}</option>`
        ).join('')}
        ${usuario.rol_valido ? '' :
          `<option value="${esc(usuario.rol)}" selected>${esc(usuario.rol)} (inválido)</option>`}
      </select>
      ${usuario.rol_valido ? `<div class="field-hint">${esc(ROLES[usuario.rol]?.detalle || '')}</div>`
        : `<div class="field-hint brecha-falta">Rol desconocido: esta persona no puede
            operar hasta que se le asigne uno válido.</div>`}
    </td>
    <td><span class="est est-${usuario.activo ? 'activo' : 'inactivo'}">
      ${usuario.activo ? 'activo' : 'desactivado'}</span></td>
    <td class="right">
      <button class="btn btn-outline btn-sm ${usuario.activo ? 'btn-del' : ''}"
        data-estado="${esc(usuario.uid)}" data-activar="${usuario.activo ? '0' : '1'}"
        ${esUnoMismo && usuario.activo ? 'disabled title="No podés desactivar tu propio usuario: te quedarías afuera."' : ''}>
        ${usuario.activo ? 'Desactivar' : 'Reactivar'}
      </button>
    </td>
  </tr>`;
}

/* ── Alta ───────────────────────────────────────────────────────── */

function abrirAlta() {
  modal({
    titulo: 'Dar de alta un usuario',
    ancho: '620px',
    cuerpo: `
      <div class="form-group"><label>Email de Equipos</label>
        <input type="email" name="email" placeholder="nombre@equipos.com.uy" />
        <div class="field-hint">
          Si ya existe una cuenta con ese correo, no se crea otra ni se toca su
          clave: se actualiza la ficha y el rol.
        </div></div>
      <div class="form-group"><label>Nombre y apellido</label>
        <input type="text" name="nombre" placeholder="Ana Pérez" /></div>
      <div class="form-group"><label>Rol</label>
        <select class="fselect" name="rol">
          ${Object.entries(ROLES).map(([clave, r]) =>
            `<option value="${clave}" ${clave === 'analista' ? 'selected' : ''}>${esc(r.etiqueta)}</option>`
          ).join('')}
        </select>
        <div class="field-hint" id="hint-rol">${esc(ROLES.analista.detalle)}</div></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Dar de alta', clase: 'btn-orange', onClick: async (caja) => {
          const datos = leerFormulario(caja);
          if (!datos.email) { toast('Falta el email.', 'err'); return; }
          try {
            const resultado = await api.usuarios.alta(datos.email, datos.rol, datos.nombre);
            cerrarModal();
            if (resultado.acceso) mostrarAcceso(resultado);
            else toast(`Ya existía: se actualizó la ficha con rol ${datos.rol}.`, 'ok');
            await cargar();
          } catch (error) {
            toast(error.detalle?.roles_validos
              ? `Rol inválido. Válidos: ${error.detalle.roles_validos.join(', ')}.`
              : error.message, 'err');
          }
        } },
    ],
  });
  const caja = $('.modal-box');
  $('[name="rol"]', caja).onchange = (e) => {
    $('#hint-rol', caja).textContent = ROLES[e.target.value]?.detalle || '';
  };
}

function mostrarAcceso(resultado) {
  const acceso = resultado.acceso;
  modal({
    titulo: 'Usuario creado',
    ancho: '640px',
    cuerpo: `
      <p><strong>${esc(resultado.usuario.nombre || resultado.usuario.email)}</strong>
        quedó con rol <strong>${esc(ROLES[resultado.usuario.rol]?.etiqueta
          || resultado.usuario.rol)}</strong>.</p>
      <div class="alert alert-warn">${esc(acceso.advertencia)}</div>
      ${acceso.link ? `<div class="form-group"><label>Enlace para fijar la contraseña</label>
        <input type="text" id="acceso-link" readonly value="${esc(acceso.link)}" /></div>`
        : `<div class="alert alert-info">
            No se pudo generar el enlace desde el servidor. La persona puede
            entrar con «¿Olvidaste tu contraseña?» en el login.
          </div>`}
      <p class="small muted">La clave con la que se creó la cuenta es aleatoria
        y no se guarda en ningún lado.</p>`,
    acciones: [
      ...(acceso.link ? [{ texto: 'Copiar enlace', clase: 'btn-dark', onClick: async () => {
          try {
            await navigator.clipboard.writeText(acceso.link);
            toast('Enlace copiado. No se vuelve a mostrar.', 'ok');
          } catch { toast('No se pudo copiar.', 'err'); }
        } }] : []),
      { texto: 'Listo', clase: 'btn-outline', onClick: cerrarModal },
    ],
  });
}

/* ── Cambios ────────────────────────────────────────────────────── */

async function cambiarRol(uid, rol, select) {
  const anterior = select.dataset.anterior || '';
  try {
    const resultado = await api.usuarios.cambiar(uid, { rol });
    toast(`Rol cambiado a ${ROLES[rol]?.etiqueta || rol}. ${resultado.vigencia}`, 'ok');
    await cargar();
  } catch (error) {
    toast(error.message, 'err');
    if (anterior) select.value = anterior;
    await cargar();
  }
}

async function cambiarEstado(uid, activar) {
  if (!activar) {
    const ok = await confirmar({
      titulo: 'Desactivar el usuario',
      cuerpo: `<p>Deja de poder entrar a la app. <strong>No se borra nada</strong>:
        la ficha, la auditoría y todo lo que hizo quedan, porque borrarlo
        borraría el rastro de sus operaciones.</p>`,
      textoOk: 'Desactivar',
    });
    if (!ok) return;
  }
  try {
    await api.usuarios.cambiar(uid, { activo: activar });
    toast(activar ? 'Usuario reactivado.' : 'Usuario desactivado.', 'ok');
    await cargar();
  } catch (error) {
    toast(error.message, 'err');
  }
}
