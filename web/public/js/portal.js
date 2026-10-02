/* Fase 6 — El portal del panelista.

   Una página aparte de la aplicación de administración, y conviene que se
   note en el código: no importa nada de `js/paginas/`, no conoce el padrón
   de usuarios y no sabe qué es un permiso. Lo único que comparte con la app
   interna es Firebase Auth, y aun eso con otro método de ingreso —enlace al
   correo, sin contraseña—.

   Ninguna llamada manda un `id_persona`. El backend lo resuelve desde el
   `uid` del token contra `cuenta_panelista`; si esta página pudiera nombrar
   a una persona, la autorización dependería de que el servidor se acuerde de
   comprobarlo en cada ruta. */

const CDN = 'https://www.gstatic.com/firebasejs/10.7.0';
const API = window.API_BASE || '/api';
const cfg = window.firebaseConfig || {};

const $ = (sel) => document.querySelector(sel);
const esc = (texto) => String(texto ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let auth = null;
let token = null;

/* ── Avisos ──────────────────────────────────────────────────────── */

function avisar(mensaje, tipo = 'error') {
  $('#alerta').innerHTML = mensaje
    ? `<div class="aviso ${tipo === 'error' ? 'warn' : 'info'}">${esc(mensaje)}</div>`
    : '';
  if (mensaje) window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ── La API ──────────────────────────────────────────────────────── */

async function pedir(ruta, opciones = {}) {
  const cabeceras = { 'Content-Type': 'application/json' };
  if (token) cabeceras.Authorization = `Bearer ${token}`;
  const respuesta = await fetch(`${API}${ruta}`, { ...opciones, headers: cabeceras });
  let cuerpo = {};
  try { cuerpo = await respuesta.json(); } catch { /* sin cuerpo */ }
  if (!respuesta.ok) {
    throw new Error(cuerpo.mensaje || cuerpo.error || 'No se pudo completar la operación.');
  }
  return cuerpo;
}

const GET = (ruta) => pedir(ruta);
const POST = (ruta, cuerpo) => pedir(ruta, { method: 'POST', body: JSON.stringify(cuerpo || {}) });
const PATCH = (ruta, cuerpo) => pedir(ruta, { method: 'PATCH', body: JSON.stringify(cuerpo || {}) });
const PUT = (ruta, cuerpo) => pedir(ruta, { method: 'PUT', body: JSON.stringify(cuerpo || {}) });
const BORRAR = (ruta) => pedir(ruta, { method: 'DELETE' });

/* ── R6.1 · Pedir el enlace ──────────────────────────────────────── */

$('#form-acceso').onsubmit = async (evento) => {
  evento.preventDefault();
  avisar('');
  const boton = evento.target.querySelector('button');
  boton.disabled = true;
  try {
    const salida = await POST('/portal/acceso', { email: $('#email').value.trim() });
    // El mensaje lo escribe el servidor y es siempre el mismo: si esta
    // página lo cambiara según la respuesta, volvería a filtrar quién
    // pertenece al panel.
    avisar(salida.mensaje, 'info');
    if (salida.enlace_sin_enviar) {
      $('#alerta').insertAdjacentHTML('beforeend',
        `<div class="aviso warn">Modo desarrollo: <a href="${esc(salida.enlace_sin_enviar)}">entrar</a></div>`);
    }
  } catch (error) {
    avisar(error.message);
  } finally {
    boton.disabled = false;
  }
};

/* ── Sesión ──────────────────────────────────────────────────────── */

async function iniciarFirebase() {
  if (!cfg.apiKey) return null;
  const [{ initializeApp }, mod] = await Promise.all([
    import(`${CDN}/firebase-app.js`),
    import(`${CDN}/firebase-auth.js`),
  ]);
  const app = initializeApp(cfg);
  return { ...mod, instancia: mod.getAuth(app) };
}

async function entrarConEnlace() {
  const params = new URLSearchParams(location.search);
  const nuestro = params.get('t');
  if (!auth) return false;
  if (!auth.isSignInWithEmailLink(auth.instancia, location.href)) return false;

  // Firebase pide el correo para completar el ingreso: si el enlace se abre
  // en otro dispositivo no lo tiene guardado y hay que preguntarlo.
  const email = window.localStorage.getItem('portal:email')
    || window.prompt('Confirmá tu correo para entrar:');
  if (!email) return false;

  await auth.signInWithEmailLink(auth.instancia, email, location.href);
  window.localStorage.removeItem('portal:email');
  token = await auth.instancia.currentUser.getIdToken();

  // El token nuestro es lo que hace al enlace de un solo uso. Va una vez, al
  // vincular; después la sesión se sostiene sola.
  await POST('/portal/sesion', nuestro ? { token: nuestro } : {});
  history.replaceState({}, '', '/portal');
  return true;
}

/* ── Las cuatro pestañas ─────────────────────────────────────────── */

document.querySelectorAll('nav.pestanas button').forEach((boton) => {
  boton.onclick = () => {
    document.querySelectorAll('nav.pestanas button').forEach((b) => {
      b.setAttribute('aria-selected', String(b === boton));
    });
    ['puntos', 'premios', 'perfil', 'derechos'].forEach((nombre) => {
      $(`#panel-${nombre}`).classList.toggle('oculto', nombre !== boton.dataset.panel);
    });
    pintar(boton.dataset.panel);
  };
});

const fecha = (iso) => (iso ? new Date(iso).toLocaleDateString('es-UY') : '');

async function pintarPuntos() {
  const datos = await GET('/portal/puntos');
  $('#saldo').textContent = datos.saldo;
  const canjes = (await GET('/portal/canjes')).items || [];
  $('#panel-puntos').innerHTML = `
    <div class="tarjeta">
      <h3>Tus movimientos</h3>
      <p>De dónde salieron tus puntos.</p>
      ${datos.movimientos.length ? datos.movimientos.map((m) => `
        <div class="movimiento">
          <div>
            <div>${esc(m.motivo)}</div>
            <div class="cuando">${fecha(m.fecha)}${
              m.vence_en ? ` · vencen el ${fecha(m.vence_en)}` : ''}</div>
          </div>
          <div class="puntos ${m.puntos >= 0 ? 'suma' : 'resta'}">${
            m.puntos >= 0 ? '+' : ''}${m.puntos}</div>
        </div>`).join('')
        : '<p>Todavía no tenés movimientos.</p>'}
    </div>
    ${canjes.length ? `
    <div class="tarjeta">
      <h3>Tus pedidos de premios</h3>
      ${canjes.map((c) => `
        <div class="movimiento">
          <div>
            <div>${esc(c.premio)}</div>
            <div class="cuando">${esc(c.estado_texto)}</div>
          </div>
          <span class="estado-canje">${esc(c.estado)}</span>
        </div>`).join('')}
    </div>` : ''}`;
}

async function pintarPremios() {
  const datos = await GET('/portal/premios');
  $('#saldo').textContent = datos.saldo;
  $('#panel-premios').innerHTML = datos.premios.length
    ? datos.premios.map((p) => `
      <div class="tarjeta fila-entre">
        <div>
          <h3>${esc(p.nombre)}</h3>
          <p>${esc(p.descripcion || '')} · ${p.costo_puntos} puntos${
            p.alcanza ? '' : ` · te faltan ${p.faltan}`}</p>
        </div>
        <button class="btn-orange" data-premio="${p.id}" ${p.alcanza ? '' : 'disabled'}>
          Pedirlo</button>
      </div>`).join('')
    : '<div class="tarjeta"><p>Por ahora no hay premios disponibles.</p></div>';

  $('#panel-premios').querySelectorAll('[data-premio]').forEach((boton) => {
    boton.onclick = async () => {
      boton.disabled = true;
      try {
        await POST('/portal/canjes', { premio_id: Number(boton.dataset.premio) });
        avisar('Pedido registrado. Lo revisamos y te avisamos.', 'info');
        await pintarPremios();
      } catch (error) {
        avisar(error.message);
        boton.disabled = false;
      }
    };
  });
}

async function pintarPerfil() {
  const perfil = await GET('/portal/perfil');
  $('#panel-perfil').innerHTML = `
    <div class="tarjeta">
      <h3>Cómo te contactamos</h3>
      <p>Si cambiás tu correo o tu celular, te mandamos un código para
         confirmarlo antes de reemplazar el anterior.</p>
      <div class="fila-entre" style="margin-bottom:8px">
        <input class="finput" id="nuevo-email" type="email"
               value="${esc(perfil.email || '')}" style="flex:1 1 200px" />
        <button class="btn-outline" data-contacto="email">Cambiar correo</button>
      </div>
      <div class="fila-entre">
        <input class="finput" id="nuevo-celular" type="tel"
               value="${esc(perfil.celular || '')}" style="flex:1 1 200px" />
        <button class="btn-outline" data-contacto="celular">Cambiar celular</button>
      </div>
      <div id="confirmar-contacto" class="oculto" style="margin-top:12px">
        <div class="fila-entre">
          <input class="finput" id="codigo" placeholder="Código de 6 dígitos"
                 style="flex:1 1 160px" />
          <button class="btn-orange" id="confirmar">Confirmar</button>
        </div>
      </div>
    </div>
    ${perfil.atributos.length ? `
    <div class="tarjeta">
      <h3>Tus datos</h3>
      <p>Mantenerlos al día nos ayuda a invitarte a los estudios que te
         corresponden.</p>
      ${perfil.atributos.map((a) => `
        <div class="fila-entre" style="margin-bottom:8px">
          <label for="attr-${esc(a.clave)}">${esc(a.etiqueta)}</label>
          <select class="fselect" id="attr-${esc(a.clave)}" data-attr="${esc(a.clave)}">
            <option value="">— sin responder —</option>
            ${a.categorias.map((c) => `<option value="${esc(c.clave)}" ${
              c.clave === a.valor ? 'selected' : ''}>${esc(c.etiqueta || c.clave)}</option>`).join('')}
          </select>
        </div>`).join('')}
      <button class="btn-orange" id="guardar-datos">Guardar</button>
    </div>` : ''}`;

  const guardar = $('#guardar-datos');
  if (guardar) {
    guardar.onclick = async () => {
      const atributos = {};
      $('#panel-perfil').querySelectorAll('[data-attr]').forEach((sel) => {
        if (sel.value) atributos[sel.dataset.attr] = sel.value;
      });
      try {
        await PATCH('/portal/perfil', { atributos });
        avisar('Listo, guardamos tus datos.', 'info');
      } catch (error) { avisar(error.message); }
    };
  }

  let pendiente = null;
  $('#panel-perfil').querySelectorAll('[data-contacto]').forEach((boton) => {
    boton.onclick = async () => {
      const canal = boton.dataset.contacto;
      const destino = $(canal === 'email' ? '#nuevo-email' : '#nuevo-celular').value.trim();
      try {
        const salida = await POST('/portal/contacto/verificacion', { canal, destino });
        pendiente = { canal, destino };
        $('#confirmar-contacto').classList.remove('oculto');
        avisar(salida.codigo_sin_enviar
          ? `Modo desarrollo: tu código es ${salida.codigo_sin_enviar}`
          : 'Te mandamos un código para confirmar.', 'info');
      } catch (error) { avisar(error.message); }
    };
  });
  const confirmar = $('#confirmar');
  if (confirmar) {
    confirmar.onclick = async () => {
      if (!pendiente) return;
      try {
        await POST('/portal/contacto', { ...pendiente, codigo: $('#codigo').value.trim() });
        avisar('Confirmado.', 'info');
        await pintarPerfil();
      } catch (error) { avisar(error.message); }
    };
  }
}

const NOMBRE_DE_FINALIDAD = {
  contacto_participacion: 'Que te invitemos a participar en estudios',
  uso_semantico: 'Que analicemos tus respuestas junto con las de otros estudios',
};

async function pintarDerechos() {
  const canales = (await GET('/portal/canales')).items || [];
  const finalidades = (await GET('/portal/finalidades')).items || [];
  const vigentes = finalidades.filter((f) => f.estado === 'vigente');

  $('#panel-derechos').innerHTML = `
    <div class="tarjeta">
      <h3>Por dónde te contactamos</h3>
      <p>Podés apagar un canal sin dejar de ser panelista.</p>
      ${canales.map((c) => `
        <div class="fila-entre" style="margin-bottom:8px">
          <div>
            <div>${esc(c.canal)}</div>
            ${c.motivo ? `<div class="cuando">${esc(c.motivo)}</div>` : ''}
          </div>
          <button class="btn-outline" data-canal="${esc(c.canal)}"
                  data-activo="${c.activo ? '1' : ''}"
                  ${c.activo || c.puede_activar ? '' : 'disabled'}>
            ${c.activo ? 'Desactivar' : 'Activar'}</button>
        </div>`).join('')}
    </div>

    <div class="tarjeta">
      <h3>Para qué nos diste permiso</h3>
      <p>Podés retirar cada permiso por separado. Retirar uno no te da de baja.</p>
      ${vigentes.length ? vigentes.map((f) => `
        <div class="fila-entre" style="margin-bottom:8px">
          <div>
            <div>${esc(NOMBRE_DE_FINALIDAD[f.finalidad] || f.finalidad)}</div>
            <div class="cuando">Desde el ${fecha(f.otorgado_en)} · texto ${esc(f.version_texto || '')}</div>
          </div>
          <button class="btn-outline" data-finalidad="${esc(f.finalidad)}">Retirar</button>
        </div>`).join('') : '<p>No tenés permisos vigentes.</p>'}
    </div>

    <div class="tarjeta peligro">
      <h3>Darte de baja</h3>
      <p>Eliminamos tus datos de nuestra base. Antes de confirmar te mostramos
         exactamente qué pasa.</p>
      <button class="btn-outline" id="ver-baja">Quiero darme de baja</button>
      <div id="detalle-baja"></div>
    </div>`;

  $('#panel-derechos').querySelectorAll('[data-canal]').forEach((boton) => {
    boton.onclick = async () => {
      try {
        await PUT(`/portal/canales/${encodeURIComponent(boton.dataset.canal)}`,
                  { activo: !boton.dataset.activo });
        await pintarDerechos();
      } catch (error) { avisar(error.message); }
    };
  });

  $('#panel-derechos').querySelectorAll('[data-finalidad]').forEach((boton) => {
    boton.onclick = async () => {
      if (!window.confirm('¿Retirar este permiso? Seguís siendo panelista.')) return;
      try {
        await BORRAR(`/portal/finalidades/${encodeURIComponent(boton.dataset.finalidad)}`);
        avisar('Listo, retiramos ese permiso.', 'info');
        await pintarDerechos();
      } catch (error) { avisar(error.message); }
    };
  });

  $('#ver-baja').onclick = async () => {
    const previo = await GET('/portal/baja');
    // R6.9 — el saldo que se pierde va **antes** de confirmar y a la vista,
    // no en una letra chica: es el tipo de cosa que genera un reclamo si
    // aparece después.
    $('#detalle-baja').innerHTML = `
      <ul>${previo.advertencias.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>
      <button class="btn-orange" id="confirmar-baja">Confirmar la baja</button>`;
    $('#confirmar-baja').onclick = async () => {
      if (!window.confirm('Esta acción no se puede deshacer. ¿Confirmás?')) return;
      try {
        const salida = await POST('/portal/baja', {});
        document.querySelector('.portal').innerHTML =
          `<div class="aviso info">${esc(salida.mensaje)}</div>`;
      } catch (error) { avisar(error.message); }
    };
  };
}

const PINTORES = {
  puntos: pintarPuntos, premios: pintarPremios,
  perfil: pintarPerfil, derechos: pintarDerechos,
};

async function pintar(cual) {
  try { await PINTORES[cual](); } catch (error) { avisar(error.message); }
}

/* ── Arranque ────────────────────────────────────────────────────── */

$('#salir').onclick = async () => {
  if (auth) await auth.signOut(auth.instancia);
  location.href = '/portal';
};

(async () => {
  try {
    auth = await iniciarFirebase();
    if (!auth) {
      avisar('Falta configurar Firebase en esta página.', 'info');
      return;
    }
    // Guardar el correo antes de irse al correo es lo que permite completar
    // el ingreso sin volver a preguntarlo cuando el enlace se abre en el
    // mismo dispositivo.
    $('#email').addEventListener('change', (e) => {
      window.localStorage.setItem('portal:email', e.target.value.trim());
    });

    const entro = await entrarConEnlace();
    auth.onAuthStateChanged(auth.instancia, async (usuario) => {
      if (!usuario) return;
      token = await usuario.getIdToken();
      if (!entro) {
        try { await POST('/portal/sesion', {}); } catch (error) {
          avisar(error.message);
          return;
        }
      }
      $('#acceso').classList.add('oculto');
      $('#adentro').classList.remove('oculto');
      avisar('');
      await pintar('puntos');
    });
  } catch (error) {
    avisar(error.message);
  }
})();
