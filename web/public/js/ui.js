/* Utilidades de interfaz: selección, escapado, toasts, modales y los
   fragmentos de HTML que se repiten en todas las páginas. */

export const $ = (sel, ctx = document) => ctx.querySelector(sel);
export const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));
export const app$ = () => document.getElementById('app');

/* Todo dato que viene de la base se escapa antes de entrar al DOM: en la
   bóveda hay texto libre cargado por operadores (nombre, observaciones). */
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

export const iniciales = (nombre, email) => {
  const base = (nombre || email || '?').trim();
  const partes = base.split(/\s+/);
  if (partes.length >= 2) return (partes[0][0] + partes[1][0]).toUpperCase();
  return base.slice(0, 2).toUpperCase();
};

export function toast(mensaje, tipo = '') {
  const contenedor = document.getElementById('toast-wrap');
  const nodo = document.createElement('div');
  nodo.className = 'toast ' + (tipo || '');
  const icono = tipo === 'ok' ? '✅' : tipo === 'err' ? '⚠️' : 'ℹ️';
  nodo.innerHTML = `<span>${icono}</span><span>${esc(mensaje)}</span>`;
  contenedor.appendChild(nodo);
  setTimeout(() => {
    nodo.style.opacity = '0';
    nodo.style.transform = 'translateY(8px)';
    nodo.style.transition = 'all .25s';
    setTimeout(() => nodo.remove(), 260);
  }, 3800);
}

/* ── Fechas ─────────────────────────────────────────────────────── */

const TZ = window.APP_TZ || 'America/Montevideo';

export function fechaCorta(valor) {
  if (!valor) return '—';
  /* Una fecha sin hora (fecha de nacimiento, fecha de campo) es un día del
     calendario, no un instante: convertirla de zona la correría un día. */
  const soloFecha = typeof valor === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(valor);
  if (soloFecha) {
    const [anio, mes, dia] = valor.split('-');
    return `${dia}/${mes}/${anio}`;
  }
  const d = valor instanceof Date ? valor : new Date(valor);
  if (Number.isNaN(d.getTime())) return String(valor);
  return new Intl.DateTimeFormat('es-UY', {
    timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric',
  }).format(d);
}

export function fechaHora(valor) {
  if (!valor) return '—';
  const d = valor instanceof Date ? valor : new Date(valor);
  if (Number.isNaN(d.getTime())) return String(valor);
  return new Intl.DateTimeFormat('es-UY', {
    timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(d);
}

export function hace(valor) {
  if (!valor) return 'nunca';
  const dias = Math.floor((Date.now() - new Date(valor).getTime()) / 86400000);
  if (dias <= 0) return 'hoy';
  if (dias === 1) return 'ayer';
  if (dias < 30) return `hace ${dias} días`;
  if (dias < 365) return `hace ${Math.floor(dias / 30)} meses`;
  return `hace ${Math.floor(dias / 365)} años`;
}

/* ── Fragmentos reutilizables ───────────────────────────────────── */

export function encabezado(titulo, acento, bajada) {
  return `<div class="page-header"><div class="accent-bar"></div>
    <div class="header-row">
      <div>
        <h2>${esc(titulo)} <span class="accent">${esc(acento || '')}</span></h2>
        <p>${esc(bajada || '')}</p>
      </div>
      <div id="ph-acciones" class="toolbar"></div>
    </div></div>`;
}

export const estado = (valor, etiqueta) =>
  `<span class="est est-${esc(valor)}">${esc(etiqueta || valor)}</span>`;

/* Las dos finalidades, siempre las dos, siempre en el mismo orden: que se
   vea de un vistazo cuál falta. */
export function consentimientos({ consiente_contacto, consiente_semantico }) {
  const c = (activo, sigla, titulo) =>
    `<span class="c ${activo ? 'on' : 'off'}" title="${esc(titulo)}">${activo ? '✓' : '·'} ${sigla}</span>`;
  return `<span class="consent">
    ${c(consiente_contacto, 'Contacto', 'Consentimiento de contacto / participación')}
    ${c(consiente_semantico, 'Semántico', 'Consentimiento de uso semántico entre estudios')}
  </span>`;
}

export function token(valor, etiqueta) {
  if (!valor) return '<span class="muted">—</span>';
  const corto = etiqueta || String(valor).slice(0, 8);
  return `<code class="token" data-copiar="${esc(valor)}" title="${esc(valor)} — clic para copiar">${esc(corto)}…</code>`;
}

export function vacio(mensaje, icono = '📭') {
  return `<div class="empty-state"><span class="ei">${icono}</span>${esc(mensaje)}</div>`;
}

export const cargando = (alto = '30vh') =>
  `<div class="center-screen" style="min-height:${alto}"><div class="spinner"></div></div>`;

export function alerta(mensaje, tipo = 'error') {
  return `<div class="alert alert-${tipo}">${esc(mensaje)}</div>`;
}

/* Habilita los <code class="token"> de un contenedor para copiar al portapapeles. */
export function activarTokens(contenedor = document) {
  $$('[data-copiar]', contenedor).forEach((nodo) => {
    nodo.onclick = async () => {
      try {
        await navigator.clipboard.writeText(nodo.dataset.copiar);
        toast('Identificador copiado.', 'ok');
      } catch {
        toast('No se pudo copiar.', 'err');
      }
    };
  });
}

/* ── Modal ──────────────────────────────────────────────────────── */

let cerrarModalActual = null;

export function modal({ titulo, cuerpo, acciones = [], ancho }) {
  cerrarModal();
  const contenedor = document.getElementById('modal-wrap');
  const estiloAncho = ancho ? ` style="max-width:${ancho}"` : '';
  contenedor.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal-box"${estiloAncho}>
        <div class="modal-head">
          <div class="modal-title">${esc(titulo)}</div>
          <button class="modal-close" data-cerrar>×</button>
        </div>
        <div class="modal-body">${cuerpo}</div>
        ${acciones.length ? `<div class="modal-foot">${acciones.map((a, i) =>
          `<button class="btn ${a.clase || 'btn-outline'}" data-accion="${i}">${esc(a.texto)}</button>`
        ).join('')}</div>` : ''}
      </div>
    </div>`;

  const caja = $('.modal-box', contenedor);
  $('[data-cerrar]', contenedor).onclick = cerrarModal;
  $('.modal-backdrop', contenedor).onclick = (e) => {
    if (e.target === e.currentTarget) cerrarModal();
  };
  acciones.forEach((accion, i) => {
    $(`[data-accion="${i}"]`, contenedor).onclick = () => accion.onClick?.(caja);
  });
  const alEscape = (e) => { if (e.key === 'Escape') cerrarModal(); };
  document.addEventListener('keydown', alEscape);
  cerrarModalActual = () => document.removeEventListener('keydown', alEscape);

  activarTokens(caja);
  const primero = $('input, select, textarea', caja);
  if (primero) primero.focus();
  return caja;
}

export function cerrarModal() {
  cerrarModalActual?.();
  cerrarModalActual = null;
  document.getElementById('modal-wrap').innerHTML = '';
}

/* Confirmación para operaciones que no se deshacen (bajas, borrados). */
export function confirmar({ titulo, cuerpo, textoOk = 'Confirmar', claseOk = 'btn-danger' }) {
  return new Promise((resolver) => {
    modal({
      titulo,
      cuerpo,
      acciones: [
        { texto: 'Cancelar', clase: 'btn-outline', onClick: () => { cerrarModal(); resolver(false); } },
        { texto: textoOk, clase: claseOk, onClick: () => { cerrarModal(); resolver(true); } },
      ],
    });
  });
}

/* Lee un formulario del modal como objeto, usando los `name` de los campos. */
export function leerFormulario(contenedor) {
  const datos = {};
  $$('[name]', contenedor).forEach((campo) => {
    if (campo.type === 'checkbox') {
      if (campo.dataset.lista) {
        datos[campo.name] = datos[campo.name] || [];
        if (campo.checked) datos[campo.name].push(campo.value);
      } else {
        datos[campo.name] = campo.checked;
      }
    } else {
      const valor = (campo.value || '').trim();
      datos[campo.name] = valor === '' ? null : valor;
    }
  });
  return datos;
}
