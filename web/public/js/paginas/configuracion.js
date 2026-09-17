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
import * as catalogo from '../catalogo.js';
import {
  $, $$, esc, encabezado, vacio, cargando, toast, modal, cerrarModal,
  leerFormulario, alerta, confirmar, fechaHora,
} from '../ui.js';

let contexto = {};
let solapa = 'usuarios';

const SOLAPAS = {
  usuarios: 'Usuarios',
  atributos: 'Atributos demográficos',
};

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

  main.innerHTML = encabezado('Configuración', '',
    'Quién entra a la app, y con qué segmentadores se describe al panel.') + `
    <div class="tabs" id="solapas">
      ${Object.entries(SOLAPAS).map(([id, etiqueta]) =>
        `<button class="tab ${id === solapa ? 'active' : ''}" data-solapa="${id}">${etiqueta}</button>`
      ).join('')}
    </div>
    <div id="cuerpo">${cargando()}</div>`;

  $$('#solapas .tab').forEach((boton) => {
    boton.onclick = () => {
      solapa = boton.dataset.solapa;
      $$('#solapas .tab').forEach(
        (b) => b.classList.toggle('active', b.dataset.solapa === solapa));
      cargar();
    };
  });
  await cargar();
}

async function cargar() {
  const caja = $('#cuerpo');
  caja.innerHTML = cargando();
  if (solapa === 'atributos') return cargarAtributos(caja);
  try {
    const [padron, auditoria] = await Promise.all([
      api.usuarios.listar(),
      api.usuarios.auditoria().catch(() => ({ items: [] })),
    ]);
    caja.innerHTML = `
      <div class="alert alert-warn">
        Dar de alta a alguien con rol de administración le da acceso a toda la
        bóveda, incluida la PII de los panelistas. Cada alta, cambio de rol y
        desactivación queda registrada con tu usuario y la fecha.
      </div>` + pintar(padron, auditoria);
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

/* ── R3.14 · El catálogo de atributos demográficos ──────────────────

   La lista de segmentadores dejó de estar escrita en el código. Acá se
   define, y de ahí en más ese atributo sirve igual que sexo o localidad: en
   los filtros de las consultas, en la composición, en las cuotas y en el
   muestreo.

   Dos reglas que la pantalla refleja en vez de dejar que se descubran por el
   error: la **clave no se cambia** una vez que hay datos cargados —es lo que
   guardan los objetivos de composición y los valores de cada persona—, y un
   atributo con datos **no se borra, se desactiva**. */

const TIPOS_DE_ATRIBUTO = {
  categorico: 'Categórico (con categorías fijas)',
  numerico: 'Numérico',
  fecha: 'Fecha',
};

async function cargarAtributos(caja) {
  caja.innerHTML = cargando();
  try {
    const { items } = await api.atributos.listar();
    caja.innerHTML = pintarAtributos(items);
    $('#nuevo-atributo').onclick = () => abrirAtributo();
    $$('[data-editar-atributo]', caja).forEach((boton) => {
      boton.onclick = () => abrirAtributo(
        items.find((a) => String(a.id) === boton.dataset.editarAtributo));
    });
    $$('[data-activar]', caja).forEach((boton) => {
      boton.onclick = () => activarAtributo(
        boton.dataset.activar, boton.dataset.valor === '1');
    });
    $$('[data-borrar-atributo]', caja).forEach((boton) => {
      boton.onclick = () => borrarAtributo(
        items.find((a) => String(a.id) === boton.dataset.borrarAtributo));
    });
    $$('[data-recalcular]', caja).forEach((boton) => {
      boton.onclick = () => recalcular(
        items.find((a) => String(a.id) === boton.dataset.recalcular));
    });
  } catch (error) {
    caja.innerHTML = `<div class="alert alert-error">${esc(error.message)}</div>`;
  }
}

function pintarAtributos(items) {
  const especiales = items.filter((a) => a.es_especial);
  const comunes = items.filter((a) => !a.es_especial);
  return `
    <div class="alert alert-info">
      Un atributo define <strong>con qué se puede segmentar al panel</strong>:
      filtrar una consulta, fijar una cuota, ver una brecha de composición y
      equilibrar un muestreo. Sus categorías son canónicas, y eso es lo que
      hace que un filtro devuelva siempre lo mismo y que la aritmética de las
      cuotas cierre.
    </div>
    <div class="toolbar" style="justify-content:flex-end;margin-bottom:1rem">
      <button class="btn btn-orange" id="nuevo-atributo">+ Definir atributo</button>
    </div>
    ${tablaDeAtributos(comunes)}
    ${especiales.length ? `
      <div class="card" style="margin-top:1.5rem">
        <div class="card-header">
          <span class="card-header-title">Categorías especiales</span>
          <span class="badge badge-off">Ley 18.331</span>
        </div>
        <div class="card-body tight">
          <div class="alert alert-warn" style="margin:1rem 1.5rem">
            Salud, origen étnico o racial, convicciones religiosas o morales,
            afiliación sindical, ideología política y vida sexual son
            categorías especiales: su tratamiento exige <strong>consentimiento
            específico</strong> y protección reforzada. No alcanza con el
            consentimiento del alta ni con el de la landing. Quedan excluidas
            por defecto de las exportaciones con datos.
          </div>
          ${tablaDeAtributos(especiales, true)}
        </div>
      </div>` : ''}`;
}

function tablaDeAtributos(items, anidada = false) {
  if (!items.length) {
    return anidada ? '' : vacio('Todavía no hay atributos definidos.', '🏷️');
  }
  const cuerpo = `
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Atributo</th><th>Clave</th><th>Tipo</th><th>Categorías</th>
        <th>Estado</th><th></th>
      </tr></thead>
      <tbody>${items.map((a) => `<tr>
        <td class="td-strong">${esc(a.etiqueta)}
          ${a.descripcion ? `<div class="small muted">${esc(a.descripcion)}</div>` : ''}</td>
        <td><code class="token">${esc(a.clave)}</code></td>
        <td class="small">${esc(TIPOS_DE_ATRIBUTO[a.tipo] || a.tipo)}
          ${a.tipo === 'derivado'
            ? '<div class="small muted">Lo calcula el sistema</div>' : ''}</td>
        <td class="small">${(a.categorias || []).length
          ? (a.categorias || []).slice(0, 6).map((c) => esc(c.clave)).join(', ')
            + ((a.categorias || []).length > 6 ? ` y ${a.categorias.length - 6} más` : '')
          : '—'}</td>
        <td><span class="badge ${a.activo ? 'est-vigente' : 'badge-off'}">
          ${a.activo ? 'activo' : 'desactivado'}</span>
          ${a.tiene_datos ? '<div class="small muted">con datos</div>' : ''}</td>
        <td class="td-acciones">
          <button class="btn btn-outline btn-sm"
            data-editar-atributo="${a.id}">Editar</button>
          ${a.tipo === 'categorico' && a.tiene_datos ? `
            <button class="btn btn-outline btn-sm" data-recalcular="${a.id}"
              title="Vuelve a resolver los valores crudos contra las categorías de hoy"
              >Recalcular</button>` : ''}
          ${a.del_nucleo ? '' : (a.tiene_datos
            ? `<button class="btn btn-outline btn-sm" data-activar="${a.id}"
                 data-valor="${a.activo ? '0' : '1'}">
                 ${a.activo ? 'Desactivar' : 'Reactivar'}</button>`
            : `<button class="btn btn-outline btn-sm btn-del"
                 data-borrar-atributo="${a.id}">Eliminar</button>`)}
        </td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  return anidada ? cuerpo : `<div class="card"><div class="card-body tight">${cuerpo}</div></div>`;
}

function abrirAtributo(atributo) {
  const esNuevo = !atributo;
  const bloqueada = !esNuevo && (atributo.tiene_datos || atributo.del_nucleo);
  const categorias = (atributo?.categorias || []);

  const caja = modal({
    titulo: esNuevo ? 'Definir un atributo demográfico' : `Editar «${atributo.etiqueta}»`,
    ancho: '680px',
    cuerpo: `
      <div id="atr-alerta"></div>
      <div class="form-row">
        <div class="form-group"><label>Nombre visible</label>
          <input type="text" name="etiqueta" placeholder="Nivel socioeconómico"
                 value="${esc(atributo?.etiqueta || '')}" /></div>
        <div class="form-group"><label>Clave</label>
          <input type="text" name="clave" placeholder="nse"
                 value="${esc(atributo?.clave || '')}" ${bloqueada ? 'disabled' : ''} />
          <div class="field-hint">${bloqueada
            ? 'No se puede cambiar: es lo que guardan los objetivos de composición y los valores ya cargados.'
            : 'Identificador estable. Letras, números y guión bajo.'}</div></div>
      </div>
      ${esNuevo ? `
      <div class="form-group"><label>Tipo</label>
        <select class="fselect" name="tipo">
          ${Object.entries(TIPOS_DE_ATRIBUTO).map(([k, v]) =>
            `<option value="${k}">${esc(v)}</option>`).join('')}
        </select>
        <div class="field-hint">El tipo no se cambia después: define cómo se
          guarda y cómo se compara el valor.</div></div>` : ''}
      <div class="form-group"><label>Descripción <span class="muted">(opcional)</span></label>
        <input type="text" name="descripcion" placeholder="Escala AMAI, tal como la releva el campo"
               value="${esc(atributo?.descripcion || '')}" /></div>

      <div class="form-group" id="bloque-categorias">
        <label>Categorías canónicas</label>
        <div class="field-hint" style="margin:-0.3rem 0 0.6rem">
          Son los únicos valores admitidos. Un valor del archivo que no
          corresponda a ninguna <strong>no se inventa</strong>: la fila queda
          sin el atributo y la carga lo informa.
        </div>
        <div id="atr-categorias">${categorias.map((c) => filaCategoria(c)).join('')}</div>
        <button class="btn btn-outline btn-sm" id="atr-agregar-cat"
          style="margin-top:0.6rem">+ Categoría</button>
      </div>

      <label class="finalidad" style="margin-top:0.5rem">
        <input type="checkbox" name="es_especial" ${atributo?.es_especial ? 'checked' : ''} />
        <span>
          <span class="f-titulo">Es una categoría especial de datos</span>
          <span class="f-desc">Salud, origen étnico o racial, convicciones
          religiosas o morales, afiliación sindical, ideología política o vida
          sexual. Exige consentimiento específico bajo la Ley 18.331 y queda
          fuera de las exportaciones con datos.</span>
        </span>
      </label>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: esNuevo ? 'Definir' : 'Guardar', clase: 'btn-orange',
        onClick: (c) => guardarAtributo(c, atributo) },
    ],
  });

  const pintarBloque = () => {
    const tipo = $('[name="tipo"]', caja)?.value || atributo?.tipo;
    $('#bloque-categorias', caja).style.display =
      (tipo === 'categorico' || tipo === 'derivado') ? '' : 'none';
  };
  $('[name="tipo"]', caja)?.addEventListener('change', pintarBloque);
  pintarBloque();

  const lista$ = $('#atr-categorias', caja);
  $('#atr-agregar-cat', caja).onclick = () => {
    lista$.insertAdjacentHTML('beforeend', filaCategoria());
  };
  // Una categoría **ya guardada** no se saca desde acá: si hay personas
  // cargadas con ella, borrarla las dejaría apuntando a nada. Por eso solo
  // las recién agregadas traen la cruz.
  lista$.onclick = (evento) => {
    const boton = evento.target.closest('[data-quitar-cat]');
    if (boton) boton.closest('[data-categoria]').remove();
  };
}

function filaCategoria(categoria) {
  const conDatos = categoria?.id;
  return `
    <div class="pregunta-fila" data-categoria data-id="${categoria?.id || ''}">
      <input type="text" class="cat-clave" placeholder="alto"
             value="${esc(categoria?.clave || '')}" ${conDatos ? 'disabled' : ''} />
      <input type="text" class="cat-etiqueta" placeholder="Alto"
             value="${esc(categoria?.etiqueta || '')}" />
      ${conDatos ? '' : '<button class="btn btn-outline btn-sm btn-del" data-quitar-cat>×</button>'}
    </div>`;
}

async function guardarAtributo(caja, atributo) {
  const datos = leerFormulario(caja);
  const alerta$ = $('#atr-alerta', caja);
  alerta$.innerHTML = '';

  const categorias = $$('[data-categoria]', caja).map((fila) => ({
    id: fila.dataset.id || null,
    clave: fila.querySelector('.cat-clave').value.trim(),
    etiqueta: fila.querySelector('.cat-etiqueta').value.trim(),
  })).filter((c) => c.clave);

  try {
    if (!atributo) {
      await api.atributos.crear({
        clave: datos.clave, etiqueta: datos.etiqueta, tipo: datos.tipo,
        descripcion: datos.descripcion, es_especial: !!datos.es_especial,
        categorias: categorias.map(({ clave, etiqueta }) => ({ clave, etiqueta })),
      });
    } else {
      await api.atributos.editar(atributo.id, {
        etiqueta: datos.etiqueta, descripcion: datos.descripcion,
        es_especial: !!datos.es_especial,
      });
      // Las categorías nuevas se agregan de a una: cada una es su propia
      // decisión y el servidor rechaza las repetidas por separado.
      for (const categoria of categorias.filter((c) => !c.id)) {
        await api.atributos.agregarCategoria(atributo.id, categoria);
      }
      for (const categoria of categorias.filter((c) => c.id)) {
        await api.atributos.editarCategoria(
          atributo.id, categoria.id, { etiqueta: categoria.etiqueta });
      }
    }
    cerrarModal();
    catalogo.invalidar();
    toast(atributo ? 'Atributo actualizado.' : 'Atributo definido.', 'ok');
    await cargar();
  } catch (error) {
    alerta$.innerHTML = alerta(error.message);
  }
}

async function activarAtributo(id, activo) {
  if (!activo && !await confirmar({
    titulo: 'Desactivar el atributo',
    cuerpo: 'Deja de ofrecerse en cargas y filtros nuevos. Lo ya cargado se '
      + 'conserva: por eso se desactiva en vez de borrarse.',
    textoOk: 'Desactivar',
  })) return;
  await api.atributos.activar(id, activo);
  catalogo.invalidar();
  toast(activo ? 'Atributo reactivado.' : 'Atributo desactivado.', 'ok');
  await cargar();
}

async function borrarAtributo(atributo) {
  if (!await confirmar({
    titulo: `Eliminar «${atributo.etiqueta}»`,
    cuerpo: 'No tiene ningún valor cargado, así que se puede eliminar sin '
      + 'perder datos. Si después vuelve a hacer falta, hay que definirlo de nuevo.',
    textoOk: 'Eliminar',
  })) return;
  try {
    await api.atributos.eliminar(atributo.id);
    catalogo.invalidar();
    toast('Atributo eliminado.', 'ok');
    await cargar();
  } catch (error) {
    toast(error.message, 'err');
  }
}

async function recalcular(atributo) {
  if (!await confirmar({
    titulo: `Recalcular «${atributo.etiqueta}»`,
    cuerpo: 'Vuelve a resolver el valor de cada persona a partir de lo que '
      + 'decía el archivo, contra las categorías de hoy. Es lo que se hace '
      + 'después de corregir el vocabulario: no hace falta volver a cargar nada.',
    textoOk: 'Recalcular',
  })) return;
  try {
    const salida = await api.atributos.recalcular(atributo.id);
    toast(`${salida.recalculados} de ${salida.revisados} valor(es) cambiaron.`
      + (salida.sin_categoria.length
        ? ` Quedan sin categoría: ${salida.sin_categoria.join(', ')}.` : ''), 'ok');
    await cargar();
  } catch (error) {
    toast(error.message, 'err');
  }
}
