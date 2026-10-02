/* Fase 6 — El portal del panelista.

   Una página aparte de la aplicación de administración, y conviene que se
   note en el código: no importa nada de `js/paginas/`, no conoce el padrón
   de usuarios y no sabe qué es un permiso. Lo único que comparte con la app
   interna es Firebase Auth.

   Ninguna llamada manda un `id_persona`. El backend lo resuelve desde el
   `uid` del token contra `cuenta_panelista`; si esta página pudiera nombrar
   a una persona, la autorización dependería de que el servidor se acuerde de
   comprobarlo en cada ruta.

   ── R6.1.a · Dónde ocurre el login ──

   **No acá.** El formulario no llama a `signInWithEmailAndPassword`: manda
   correo y contraseña a `/portal/sesion/clave` y recibe un token custom con
   el que recién entonces abre la sesión de Firebase. Parece una vuelta de
   más y no lo es: el límite de intentos fallidos y el rechazo de quien se
   dio de baja tienen que decidirse en el servidor, y un control que se
   aplica en el navegador no es un control.

   Consecuencia para quien toque este archivo: **los mensajes de error de
   acceso vienen del servidor y se muestran tal cual.** Si esta página
   inventara un texto propio según el código de error, volvería a distinguir
   «ese correo no existe» de «contraseña incorrecta», que es justamente la
   filtración que el servidor evita. */

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

/* ── R6.1.b · Entrar con correo y contraseña ─────────────────────── */

async function abrirSesionCon(tokenCustom) {
  // El token custom lo emitió el backend después de comprobar la credencial
  // y de resolver que esa cuenta es de un panelista activo. Firebase se
  // encarga desde acá del refresco y de la persistencia.
  await auth.signInWithCustomToken(auth.instancia, tokenCustom);
  token = await auth.instancia.currentUser.getIdToken(true);
}

$('#form-acceso').onsubmit = async (evento) => {
  evento.preventDefault();
  avisar('');
  const boton = evento.target.querySelector('button[type="submit"]');
  boton.disabled = true;
  try {
    const salida = await POST('/portal/sesion/clave', {
      email: $('#email').value.trim(), clave: $('#clave').value,
    });
    $('#clave').value = '';
    await abrirSesionCon(salida.token_de_sesion);
  } catch (error) {
    // Tal cual lo dijo el servidor. Ver el encabezado.
    avisar(error.message);
  } finally {
    boton.disabled = false;
  }
};

/* ── R6.1.a/c · Pedir el enlace para crear o recuperar la clave ──── */

async function pedirEnlace(motivo) {
  const email = $('#email').value.trim();
  if (!email) { avisar('Escribí tu correo primero.'); return; }
  avisar('');
  try {
    const salida = await POST('/portal/clave/enlace', { email, motivo });
    // El mensaje lo escribe el servidor y es siempre el mismo: si esta
    // página lo cambiara según la respuesta, volvería a filtrar quién
    // pertenece al panel.
    avisar(salida.mensaje, 'info');
    if (salida.enlace_sin_enviar) {
      $('#alerta').insertAdjacentHTML('beforeend',
        `<div class="aviso warn">Modo desarrollo: <a href="${esc(salida.enlace_sin_enviar)}">crear la contraseña</a></div>`);
    }
  } catch (error) { avisar(error.message); }
}

$('#crear-clave').onclick = () => pedirEnlace('alta_clave');
$('#olvide-clave').onclick = () => pedirEnlace('recuperacion');

/* ── R6.1.a · Fijar la contraseña con el enlace ──────────────────── */

function tokenDelEnlace() {
  return new URLSearchParams(location.search).get('t');
}

$('#form-fijar').onsubmit = async (evento) => {
  evento.preventDefault();
  avisar('');
  const nueva = $('#clave-nueva').value;
  if (nueva !== $('#clave-repetida').value) {
    avisar('Las dos contraseñas no coinciden.');
    return;
  }
  const boton = evento.target.querySelector('button[type="submit"]');
  boton.disabled = true;
  try {
    const salida = await POST('/portal/clave', { token: tokenDelEnlace(), clave: nueva });
    history.replaceState({}, '', '/portal');
    await abrirSesionCon(salida.token_de_sesion);
  } catch (error) {
    avisar(error.message);
    boton.disabled = false;
  }
};

/* ── R6.1.d · El modal de reautenticación ────────────────────────── */

/* Devuelve la contraseña que la persona escribió, o null si cerró el modal.
   No la guarda en ningún lado: viaja en el cuerpo del pedido que la pidió y
   se descarta. Quien exige la contraseña es el servidor —estas tres
   acciones fallan sin ella aunque esta página no abriera nada—; el modal
   solo evita que el error tenga que verlo la persona. */
function pedirClaveActual(queVasAHacer) {
  return new Promise((resolver) => {
    const velo = document.createElement('div');
    velo.className = 'velo';
    velo.innerHTML = `
      <form class="tarjeta">
        <h3>Confirmá que sos vos</h3>
        <p>${esc(queVasAHacer)} no se puede deshacer, así que te pedimos la
           contraseña otra vez.</p>
        <input class="finput" type="password" autocomplete="current-password"
               placeholder="Tu contraseña" style="width:100%;margin-bottom:12px" />
        <div class="fila-entre">
          <button type="button" class="btn-outline" data-cancelar>Cancelar</button>
          <button type="submit" class="btn-orange">Confirmar</button>
        </div>
      </form>`;
    const campo = velo.querySelector('input');
    const cerrar = (valor) => { velo.remove(); resolver(valor); };
    velo.querySelector('[data-cancelar]').onclick = () => cerrar(null);
    velo.querySelector('form').onsubmit = (e) => { e.preventDefault(); cerrar(campo.value); };
    document.body.appendChild(velo);
    campo.focus();
  });
}

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

    <div class="tarjeta">
      <h3>Tu contraseña</h3>
      <p>Cambiarla cierra las sesiones que tengas abiertas en otros
         dispositivos.</p>
      <input class="finput" id="clave-actual" type="password"
             autocomplete="current-password" placeholder="Tu contraseña actual"
             style="width:100%;margin-bottom:8px" />
      <input class="finput" id="clave-proxima" type="password"
             autocomplete="new-password" placeholder="La nueva"
             style="width:100%;margin-bottom:12px" />
      <button class="btn-orange" id="cambiar-clave">Cambiar la contraseña</button>
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
      // R6.1.d/f — cambiar el correo mueve la puerta de entrada, así que
      // pide la contraseña además del código. El celular no: equivocarse de
      // número se arregla cambiándolo otra vez.
      let clave = null;
      if (pendiente.canal === 'email') {
        clave = await pedirClaveActual('Cambiar tu correo de ingreso');
        if (clave === null) return;
      }
      try {
        const salida = await POST('/portal/contacto', {
          ...pendiente, codigo: $('#codigo').value.trim(), clave,
        });
        avisar(salida.mensaje || 'Confirmado.', 'info');
        await pintarPerfil();
      } catch (error) { avisar(error.message); }
    };
  }

  // R6.1.c — cambiar la contraseña desde adentro. Vive en «Mis datos» y no
  // escondida en un menú: es lo que hace una persona que sospecha que
  // alguien le entró, y en ese momento no está para buscarla.
  $('#cambiar-clave').onclick = async () => {
    const actual = $('#clave-actual').value;
    const nueva = $('#clave-proxima').value;
    if (!actual || !nueva) { avisar('Completá las dos contraseñas.'); return; }
    try {
      const salida = await POST('/portal/clave/cambio', { actual, nueva });
      // El cambio revocó todas las sesiones, incluida ésta: el servidor
      // devuelve una nueva para que la persona no se encuentre afuera justo
      // después de hacer lo correcto.
      await abrirSesionCon(salida.token_de_sesion);
      $('#clave-actual').value = '';
      $('#clave-proxima').value = '';
      avisar(salida.mensaje, 'info');
    } catch (error) { avisar(error.message); }
  };
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
      // R6.1.d — retirar un permiso borra: `uso_semantico` se lleva los
      // embeddings de esa persona, y volver a darlo no los devuelve.
      const clave = await pedirClaveActual('Retirar este permiso');
      if (clave === null) return;
      try {
        await POST(`/portal/finalidades/${encodeURIComponent(boton.dataset.finalidad)}/retiro`,
                   { clave });
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
      const clave = await pedirClaveActual('Darte de baja del panel');
      if (clave === null) return;
      try {
        const salida = await POST('/portal/baja', { clave });
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

    // Con `?t=` en la dirección, la página es la de elegir la contraseña y
    // no la de entrar. El token no se guarda en ningún lado: viaja en el
    // cuerpo del pedido que lo consume y se descarta con la navegación.
    if (tokenDelEnlace()) {
      $('#acceso').classList.add('oculto');
      $('#fijar-clave').classList.remove('oculto');
    }

    auth.onAuthStateChanged(auth.instancia, async (usuario) => {
      if (!usuario) return;
      token = await usuario.getIdToken();
      // Confirma el vínculo con la persona. En el camino normal ya lo armó
      // el backend al dar la credencial por buena; esto cubre la sesión que
      // Firebase recuerda de una visita anterior.
      try { await POST('/portal/sesion', {}); } catch (error) {
        avisar(error.message);
        return;
      }
      $('#acceso').classList.add('oculto');
      $('#fijar-clave').classList.add('oculto');
      $('#adentro').classList.remove('oculto');
      avisar('');
      await pintar('puntos');
    });
  } catch (error) {
    avisar(error.message);
  }
})();
