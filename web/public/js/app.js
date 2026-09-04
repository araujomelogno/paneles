/* Arranque, autenticación y navegación.

   Frontend sin build: HTML + módulos ES nativos, servidos tal cual por
   Firebase Hosting. Misma identidad visual que la app de fichaje de
   jornadas de Equipos. */

import * as api from './api.js';
import { $, $$, app$, esc, iniciales, toast, cargando, alerta } from './ui.js';

import * as pagPanelistas from './paginas/panelistas.js';
import * as pagPaneles from './paginas/paneles.js';
import * as pagEncuestas from './paginas/encuestas.js';
import * as pagRevisiones from './paginas/revisiones.js';
import * as pagCumplimiento from './paginas/cumplimiento.js';

const CDN = 'https://www.gstatic.com/firebasejs/10.7.0';
const cfg = window.firebaseConfig || {};
const CONFIGURADO = cfg.apiKey && !String(cfg.apiKey).startsWith('TU_')
  && cfg.projectId && !String(cfg.projectId).startsWith('TU_');

export const sesion = { usuario: null, actor: null, auth: null };

const PAGINAS = [
  { id: 'panelistas',   icono: '👤', etiqueta: 'Panelistas',   modulo: pagPanelistas,   permiso: 'leer' },
  { id: 'paneles',      icono: '📋', etiqueta: 'Paneles',      modulo: pagPaneles,      permiso: 'leer' },
  { id: 'encuestas',    icono: '📨', etiqueta: 'Encuestas',    modulo: pagEncuestas,    permiso: 'leer' },
  { id: 'revisiones',   icono: '🔀', etiqueta: 'Revisión de altas', modulo: pagRevisiones, permiso: 'leer' },
  { id: 'cumplimiento', icono: '🛡️', etiqueta: 'Cumplimiento', modulo: pagCumplimiento, permiso: 'leer' },
];

const ROL_ETIQUETA = {
  admin: 'Administración', operaciones: 'Responsable de panel',
  analista: 'Analista', dpo: 'Cumplimiento / DPO',
};

let paginaActual = 'panelistas';

/* ── Arranque ───────────────────────────────────────────────────── */

async function arrancar() {
  if (!CONFIGURADO) {
    // Sin proyecto Firebase: se entra directo al modo demo.
    api.estado.demo = true;
    sesion.actor = await api.yo();
    renderApp();
    return;
  }

  try {
    const [{ initializeApp }, authMod] = await Promise.all([
      import(`${CDN}/firebase-app.js`),
      import(`${CDN}/firebase-auth.js`),
    ]);
    const app = initializeApp(cfg);
    const auth = authMod.getAuth(app);
    sesion.auth = { ...authMod, instancia: auth };
    api.estado.obtenerToken = async () => auth.currentUser?.getIdToken();

    authMod.onAuthStateChanged(auth, async (usuario) => {
      sesion.usuario = usuario;
      if (!usuario) { sesion.actor = null; renderLogin(); return; }
      app$().innerHTML = cargando('100vh');
      try {
        sesion.actor = await api.yo();
        renderApp();
      } catch (error) {
        renderSinAcceso(error);
      }
    });
  } catch (error) {
    renderError(`No se pudo iniciar Firebase: ${error.message}`);
  }
}

/* ── Pantallas de estado y login ────────────────────────────────── */

const LOGO_VERTICAL = 'assets/logo-equipos-vertical.png';
const LOGO_HORIZONTAL = 'assets/logo-equipos-horizontal.png';

function renderError(mensaje) {
  app$().innerHTML = `<div class="center-screen"><div class="info-card">
    <div class="ic-icon">⚠️</div><h2>Algo salió mal</h2><p>${esc(mensaje)}</p>
  </div></div>`;
}

function renderSinAcceso(error) {
  const detalle = error?.status === 403
    ? 'Tu usuario no está habilitado en el sistema de paneles. Pedile el alta a un administrador.'
    : (error?.message || 'No se pudieron cargar tus datos.');
  app$().innerHTML = `<div class="center-screen"><div class="info-card">
    <div class="ic-icon">🔒</div><h2>Sin acceso</h2><p>${esc(detalle)}</p>
    <button class="btn btn-dark btn-full" id="salir">Cerrar sesión</button>
  </div></div>`;
  $('#salir').onclick = cerrarSesion;
}

function renderLogin() {
  app$().innerHTML = `
  <div class="login-wrap">
    <div class="login-brand">
      <div class="login-brand-inner">
        <div class="login-brand-logo"><img src="${LOGO_VERTICAL}" alt="Equipos Consultores" /></div>
        <div class="tagline"><em>Gestión</em><em>de paneles</em></div>
        <div class="tagline-sub">Panelistas, consentimiento y fielding</div>
      </div>
    </div>
    <div class="login-form-panel">
      <div class="login-form-inner">
        <div class="accent-bar"></div>
        <h1>Bienvenido/a</h1>
        <p class="login-sub">Ingresá con tu cuenta de Equipos</p>
        <div id="login-alerta" class="hidden"></div>
        <div class="form-group">
          <label>Email</label>
          <input type="email" id="login-email" placeholder="nombre@equipos.com.uy" autocomplete="username" />
        </div>
        <div class="form-group">
          <label>Clave</label>
          <input type="password" id="login-clave" placeholder="••••••••" autocomplete="current-password" />
        </div>
        <button class="btn btn-orange btn-full" id="login-btn">Ingresar</button>
        <p class="login-forgot"><a id="login-olvide">¿Olvidaste tu contraseña?</a></p>
      </div>
    </div>
  </div>`;

  $('#login-btn').onclick = ingresar;
  $('#login-olvide').onclick = (e) => { e.preventDefault(); recuperarClave(); };
  $('#login-clave').addEventListener('keydown', (e) => { if (e.key === 'Enter') ingresar(); });
  $('#login-email').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('#login-clave').focus(); });
}

function mostrarAlertaLogin(mensaje) {
  const nodo = $('#login-alerta');
  nodo.className = 'alert alert-error';
  nodo.textContent = mensaje;
}

function mensajeAuth(error) {
  const codigo = error?.code || '';
  if (/invalid-credential|wrong-password|user-not-found/.test(codigo)) return 'Email o clave incorrectos.';
  if (codigo.includes('too-many-requests')) return 'Demasiados intentos. Probá de nuevo en unos minutos.';
  if (codigo.includes('invalid-email')) return 'El email no es válido.';
  return error?.message || 'Ocurrió un error.';
}

async function ingresar() {
  const email = ($('#login-email').value || '').trim().toLowerCase();
  const clave = $('#login-clave').value;
  if (!email || !clave) { mostrarAlertaLogin('Ingresá tu email y tu clave.'); return; }
  const boton = $('#login-btn');
  boton.disabled = true;
  boton.textContent = 'Ingresando…';
  try {
    await sesion.auth.signInWithEmailAndPassword(sesion.auth.instancia, email, clave);
  } catch (error) {
    mostrarAlertaLogin(mensajeAuth(error));
    boton.disabled = false;
    boton.textContent = 'Ingresar';
  }
}

async function recuperarClave() {
  const email = ($('#login-email').value || '').trim().toLowerCase();
  if (!email) { mostrarAlertaLogin('Escribí tu email arriba y volvé a tocar el enlace.'); return; }
  try {
    await sesion.auth.sendPasswordResetEmail(sesion.auth.instancia, email);
    toast('Te enviamos un correo para restablecer la contraseña.', 'ok');
  } catch (error) {
    mostrarAlertaLogin(mensajeAuth(error));
  }
}

async function cerrarSesion() {
  if (api.estado.demo) { location.reload(); return; }
  try { await sesion.auth.signOut(sesion.auth.instancia); } catch { /* ya está afuera */ }
}

/* ── Layout de la app ───────────────────────────────────────────── */

function renderApp() {
  const actor = sesion.actor || {};
  const nav = PAGINAS.map((p) => `
    <button class="nav-item ${p.id === paginaActual ? 'active' : ''}" data-pagina="${p.id}">
      <span class="nav-icon">${p.icono}</span>${esc(p.etiqueta)}
    </button>`).join('');

  const banner = api.estado.demo ? `
    <div class="demo-banner">
      <strong>Modo demo</strong> — datos de ejemplo en memoria, sin backend ni base.
      Configurá <code>firebaseConfig</code> en <code>index.html</code> para conectarlo de verdad.
    </div>` : '';

  app$().innerHTML = `
  ${banner}
  <div class="app">
    <aside class="sidebar">
      <div class="sidebar-top">
        <img class="logo-horizontal-white" src="${LOGO_HORIZONTAL}" alt="Equipos Consultores" />
      </div>
      <div class="sidebar-section-label">Gestión de paneles</div>
      <nav id="nav">${nav}</nav>
      <div class="sidebar-footer">
        <div class="user-pill">
          <div class="user-avatar">${esc(iniciales(actor.nombre, actor.email))}</div>
          <div class="user-info-wrap">
            <div class="user-email">${esc(actor.nombre || actor.email || '—')}</div>
            <div class="user-role">${esc(ROL_ETIQUETA[actor.rol] || actor.rol || '')}</div>
          </div>
        </div>
        <button class="btn btn-outline btn-full btn-sm" id="salir"
                style="border-color:rgba(255,255,255,0.2); color:rgba(255,255,255,0.85);">
          Cerrar sesión
        </button>
      </div>
    </aside>
    <main class="main" id="main"></main>
  </div>`;

  $('#salir').onclick = cerrarSesion;
  $$('#nav .nav-item').forEach((boton) => {
    boton.onclick = () => irA(boton.dataset.pagina);
  });
  abrir(paginaActual);
}

export function irA(id, contexto) {
  paginaActual = id;
  $$('#nav .nav-item').forEach((b) => b.classList.toggle('active', b.dataset.pagina === id));
  abrir(id, contexto);
}

async function abrir(id, contexto) {
  const main = $('#main');
  if (!main) return;
  main.innerHTML = cargando();
  const pagina = PAGINAS.find((p) => p.id === id) || PAGINAS[0];
  try {
    await pagina.modulo.render(main, { actor: sesion.actor, irA, contexto });
  } catch (error) {
    console.error(error);
    main.innerHTML = alerta(error.message || 'No se pudo cargar la página.');
  }
}

arrancar();
