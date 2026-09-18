/* Encuestas — fielding, convocatoria e ingesta semántica (R1.5).

   Acá se ve el puente entre stores: la encuesta de la bóveda nace con un
   `ref_estudio`, y la ingesta escribe del lado semántico el cuestionario con
   ese mismo uuid y las respuestas con el `id_persona` de cada panelista.
   Del archivo de campo solo cruza el id de la plataforma traducido a
   `id_persona`: la PII se queda en la bóveda. */

import * as api from '../api.js';
import * as catalogo from '../catalogo.js';
import {
  $, $$, esc, encabezado, token, vacio, cargando, toast, modal, cerrarModal,
  leerFormulario, activarTokens, fechaCorta, estado, alerta, confirmar,
} from '../ui.js';

let contexto = {};

const ESTADO_ETIQUETA = { borrador: 'Borrador', en_campo: 'En campo', cerrada: 'Cerrada' };

export async function render(main, ctx) {
  contexto = ctx;
  if (ctx.contexto?.encuestaId) return renderDetalle(main, ctx.contexto.encuestaId);

  const panelId = ctx.contexto?.panelId || null;
  const [{ items }, { items: paneles }] = await Promise.all([
    api.encuestas.listar(panelId), api.paneles.listar(),
  ]);

  main.innerHTML = encabezado('Encuestas', 'y olas',
    'Cada encuesta nace con su ref_estudio: es la uuid que la ata a su cuestionario del lado semántico.') + `
    <div class="card">
      <div class="card-header"><span class="card-header-title">
        ${panelId ? esc(paneles.find((p) => p.id === panelId)?.nombre || 'Panel') : 'Todas las encuestas'}
      </span></div>
      <div class="card-body tight">
        ${items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Encuesta</th><th>Panel</th><th>Campo</th><th>Estado</th>
            <th>Convocados</th><th>Respondieron</th><th>ref_estudio</th><th></th></tr></thead>
          <tbody>${items.map((e) => {
            const tasa = e.convocados ? Math.round((e.respondieron / e.convocados) * 100) : 0;
            return `<tr>
              <td class="td-strong">${esc(e.nombre)}</td>
              <td class="small">${esc(e.panel || '—')}</td>
              <td class="small">${e.fecha_campo ? fechaCorta(e.fecha_campo) : '—'}</td>
              <td>${estado(e.estado, ESTADO_ETIQUETA[e.estado])}</td>
              <td class="mono">${e.convocados ?? 0}</td>
              <td><div style="display:flex;align-items:center;gap:0.5rem">
                <span class="mono">${e.respondieron ?? 0}</span>
                <span class="barra ${tasa >= 60 ? 'ok' : ''}"><span style="width:${tasa}%"></span></span>
                <span class="small muted">${tasa}%</span></div></td>
              <td>${token(e.ref_estudio)}</td>
              <td class="right"><button class="btn btn-outline btn-sm" data-encuesta="${e.id}">Abrir</button></td>
            </tr>`;
          }).join('')}</tbody></table></div>`
          : vacio('Todavía no hay encuestas fieldeadas.', '📨')}
      </div>
    </div>`;

  $('#ph-acciones').innerHTML = `
    ${panelId ? `<button class="btn btn-outline" id="todas">Ver todas</button>` : ''}
    <button class="btn btn-orange" id="nueva">+ Nueva encuesta</button>`;
  if (panelId) $('#todas').onclick = () => contexto.irA('encuestas');
  $('#nueva').onclick = () => abrirNueva(paneles, panelId);
  activarTokens(main);
  $$('[data-encuesta]', main).forEach((boton) => {
    boton.onclick = () => contexto.irA('encuestas', { encuestaId: Number(boton.dataset.encuesta) });
  });
}

function abrirNueva(paneles, panelPreseleccionado) {
  const activos = paneles.filter((p) => p.estado === 'activo');
  modal({
    titulo: 'Nueva encuesta',
    cuerpo: `
      <div id="enc-alerta"></div>
      <div class="form-group"><label>Panel</label>
        <select class="fselect" name="panel_id">
          ${activos.map((p) => `<option value="${p.id}" ${p.id === panelPreseleccionado ? 'selected' : ''}>${esc(p.nombre)}</option>`).join('')}
        </select></div>
      <div class="form-group"><label>Nombre de la encuesta</label>
        <input type="text" name="nombre" placeholder="Ola 3 — Consumo de bebidas" /></div>
      <div class="form-group"><label>Fecha de campo</label>
        <input type="date" name="fecha_campo" /></div>
      <div class="field-hint">Al crearla se genera su <code>ref_estudio</code>, que es lo que
      después ata sus respuestas del lado semántico.</div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Crear', clase: 'btn-orange', onClick: async (caja) => {
          const datos = leerFormulario(caja);
          if (!datos.nombre) {
            $('#enc-alerta', caja).innerHTML = alerta('La encuesta necesita un nombre.');
            return;
          }
          try {
            const encuesta = await api.encuestas.crear(
              Number(datos.panel_id), datos.nombre, datos.fecha_campo);
            cerrarModal();
            toast('Encuesta creada.', 'ok');
            contexto.irA('encuestas', { encuestaId: encuesta.id });
          } catch (error) {
            $('#enc-alerta', caja).innerHTML = alerta(error.message);
          }
        } },
    ],
  });
}

/* ── Detalle ────────────────────────────────────────────────────── */

async function renderDetalle(main, encuestaId) {
  main.innerHTML = cargando();
  const encuesta = await api.encuestas.ver(encuestaId);

  main.innerHTML = encabezado(encuesta.nombre, '',
    `Panel #${encuesta.panel_id} · campo ${encuesta.fecha_campo ? fechaCorta(encuesta.fecha_campo) : 'sin fecha'}`) + `
    <div class="card">
      <div class="card-body">
        <dl class="kv">
          <dt>Estado</dt><dd>${estado(encuesta.estado, ESTADO_ETIQUETA[encuesta.estado])}</dd>
          <dt>ref_estudio</dt>
          <dd>${token(encuesta.ref_estudio, encuesta.ref_estudio)}
            <div class="field-hint">La misma uuid identifica al cuestionario del lado semántico.
            No hay clave foránea entre stores: el cruce es por esta uuid y por id_persona.</div></dd>
        </dl>
      </div>
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">Convocatoria y participación</span>
        <div class="toolbar">
          <button class="btn btn-outline btn-sm" id="convocar">Convocar al panel</button>
          <button class="btn btn-outline btn-sm" id="muestra">Exportar muestra</button>
          <button class="btn btn-orange btn-sm" id="ingestar">Ingestar respuestas</button>
        </div>
      </div>
      <div class="card-body tight"><div id="participacion">${cargando('20vh')}</div></div>
    </div>
    <!-- R4.5 — el canal de WhatsApp. La tarjeta existe siempre, porque es
         donde se configura; el envío aparece cuando hay Flow. -->
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">WhatsApp Flow</span>
        <div class="toolbar">
          <button class="btn btn-outline btn-sm" id="configurar-flow">
            ${encuesta.es_flow ? 'Editar configuración' : 'Configurar'}</button>
          ${encuesta.es_flow
            ? '<button class="btn btn-orange btn-sm" id="enviar-wa">Enviar por WhatsApp</button>'
            : ''}
        </div>
      </div>
      <div class="card-body"><div id="flow" class="muted small">
        ${encuesta.es_flow
          ? cargando('12vh')
          : `Esta encuesta no se envía por WhatsApp. Para que lo haga, se
             elige una de las <strong>plantillas aprobadas</strong> de la
             cuenta: cada una lleva adentro su Flow y su idioma. El sistema
             <strong>solo envía</strong>; las respuestas se bajan de Meta y se
             ingestan por el flujo de siempre.`}
      </div></div>
    </div>
    <div class="card">
      <div class="card-header"><span class="card-header-title">Cruce entre stores</span>
        <button class="btn btn-outline btn-sm" id="verificar">Verificar</button></div>
      <div class="card-body"><div id="cruce" class="muted small">
        Verificá que lo que quedó del lado semántico se corresponde con lo convocado acá.
      </div></div>
    </div>`;

  $('#ph-acciones').innerHTML = `
    <button class="btn btn-outline" id="volver">‹ Encuestas</button>
    ${encuesta.estado !== 'cerrada'
      ? `<button class="btn btn-dark" id="cerrar">Cerrar encuesta</button>` : ''}`;
  $('#volver').onclick = () => contexto.irA('encuestas');
  $('#configurar-flow').onclick = () => abrirConfiguracionFlow(encuesta);
  if ($('#enviar-wa')) $('#enviar-wa').onclick = () => abrirEnvioWhatsapp(encuesta);
  if (encuesta.es_flow) cargarEstadoFlow(encuesta.id);
  $('#cerrar')?.addEventListener('click', async () => {
    const ok = await confirmar({
      titulo: 'Cerrar la encuesta',
      cuerpo: '<p>Una encuesta cerrada no admite nuevas convocatorias.</p>',
      textoOk: 'Cerrar', claseOk: 'btn-dark',
    });
    if (!ok) return;
    await api.encuestas.cambiarEstado(encuestaId, 'cerrada');
    toast('Encuesta cerrada.', 'ok');
    contexto.irA('encuestas', { encuestaId });
  });
  activarTokens(main);

  $('#convocar').onclick = () => convocar(encuestaId);
  $('#muestra').onclick = () => exportarMuestra(encuestaId);
  $('#ingestar').onclick = () => abrirIngesta(encuesta);
  $('#verificar').onclick = () => verificarCruce(encuestaId);

  await cargarParticipacion(encuestaId);
}

async function cargarParticipacion(encuestaId) {
  const contenedor = $('#participacion');
  if (!contenedor) return;
  const { items } = await api.encuestas.participacion(encuestaId);
  if (!items.length) {
    contenedor.innerHTML = vacio('Todavía no se convocó a nadie.', '📭');
    return;
  }
  const respondieron = items.filter((p) => p.respondio).length;
  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Panelista</th><th>Id en el origen</th><th>Convocado</th>
        <th>Respondió</th><th>Uso semántico</th></tr></thead>
      <tbody>${items.map((p) => `
        <tr>
          <td><div class="td-strong">${esc(p.nombre || 'Sin nombre')}</div>
            <div class="td-muted small">${esc(p.email || '—')}</div></td>
          <td class="mono small">${esc(p.id_en_origen || '—')}</td>
          <td class="small">${fechaCorta(p.convocado_en)}</td>
          <td>${p.respondio ? estado('completa', 'Sí') : estado('no_ficho', 'No')}</td>
          <td>${p.consiente_semantico
            ? '<span class="badge badge-on">Sí</span>'
            : '<span class="badge badge-off">No</span>'}</td>
        </tr>`).join('')}</tbody></table></div>
    <div class="paginacion"><span class="p-info">
      ${items.length} convocados · ${respondieron} respondieron</span></div>`;
}

async function convocar(encuestaId) {
  const ok = await confirmar({
    titulo: 'Convocar a todo el panel',
    cuerpo: `<div class="aviso"><h4>Gate de consentimiento</h4>
      <p>Solo entran a la convocatoria quienes tengan <strong>contacto / participación</strong>
      vigente. Quien no lo tenga queda afuera y se informa acá.</p></div>`,
    textoOk: 'Convocar', claseOk: 'btn-orange',
  });
  if (!ok) return;
  try {
    const resultado = await api.encuestas.convocar(encuestaId, { todoElPanel: true });
    mostrarResultado('Convocatoria', [
      ['Convocados nuevos', resultado.convocados_nuevos],
      ['Total habilitados', resultado.convocados_total],
      ['Sin consentimiento', resultado.sin_consentimiento.length, true],
    ], resultado.sin_consentimiento.length
      ? `${resultado.sin_consentimiento.length} persona(s) quedaron fuera por no tener el
         consentimiento de contacto vigente.` : null);
    await cargarParticipacion(encuestaId);
  } catch (error) {
    toast(error.message, 'err');
  }
}

/* R3.12.d — las tres causas se arreglan distinto, así que el informe las
   separa en vez de dar un número suelto. */
const MOTIVOS_SIN_MAPEAR = {
  formato_invalido: 'el valor no es un id_persona válido',
  no_encontrado: 'ese identificador no existe en la bóveda',
  sin_alias_para_ese_origen: 'esa plataforma no tiene registrado ese id',
};

function resumirSinMapear(resultado) {
  const detalle = resultado.sin_mapear_detalle || [];
  if (!detalle.length) return 'sin detalle del motivo';
  const porMotivo = {};
  detalle.forEach((d) => { porMotivo[d.motivo] = (porMotivo[d.motivo] || 0) + 1; });
  return Object.entries(porMotivo)
    .sort((a, b) => b[1] - a[1])
    .map(([motivo, n]) => `${n} porque ${MOTIVOS_SIN_MAPEAR[motivo] || motivo}`)
    .join('; ');
}

/* «localidad (3), sexo (1)»: qué campos discreparon y cuántas veces. La
   lista completa puede ser larga y lo que hace falta para decidir si mirarla
   es saber de qué se trata. */
function resumirDiscrepancias(discrepancias) {
  const porCampo = {};
  discrepancias.forEach((d) => { porCampo[d.campo] = (porCampo[d.campo] || 0) + 1; });
  return Object.entries(porCampo)
    .sort((a, b) => b[1] - a[1])
    .map(([campo, n]) => `${campo} (${n})`)
    .join(', ');
}

function mostrarResultado(titulo, filas, nota) {
  modal({
    titulo,
    cuerpo: `
      <div class="resultado">
        ${filas.map(([etiqueta, valor, alertar]) => `
          <div class="r ${alertar && valor ? 'alerta' : ''}">
            <div class="r-num">${esc(valor)}</div>
            <div class="r-label">${esc(etiqueta)}</div>
          </div>`).join('')}
      </div>
      ${nota ? `<div class="aviso"><p>${esc(nota)}</p></div>` : ''}`,
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
}

/* ── R3.12.a · Exportar la muestra ──────────────────────────────── */

/* Mientras corre algo largo, el pie del modal queda inutilizable.

   Sin esto, el usuario que no ve avance vuelve a apretar Ingestar —es lo
   razonable si parece que no pasó nada— y dispara una segunda carga encima
   de la primera. La barra de avance y esto resuelven el mismo problema desde
   los dos lados. */
function bloquearModal(caja) {
  // El pie y la × del encabezado. No las × de cada fila de variable, que
  // comparten la clase: esas quedan inertes igual porque el modal entero
  // está esperando.
  const botones = $$('.modal-foot .btn, .modal-head .modal-close', caja);
  botones.forEach((b) => { b.disabled = true; });
  return {
    soltar() { botones.forEach((b) => { b.disabled = false; }); },
  };
}

function descargarCsv(nombre, contenido) {
  const blob = new Blob([contenido], { type: 'text/csv;charset=utf-8' });
  const enlace = document.createElement('a');
  enlace.href = URL.createObjectURL(blob);
  enlace.download = nombre || 'muestra.csv';
  enlace.click();
  URL.revokeObjectURL(enlace.href);
}


/* El identificador del sistema viaja al campo en vez de adivinarlo a la
   vuelta: se precarga `id_persona` como variable oculta en el instrumento y
   vuelve en el archivo. El archivo seudónimo es el camino normal; el que
   lleva contacto es una reidentificación y se trata como tal. */
function exportarMuestra(encuestaId) {
  modal({
    titulo: 'Exportar la muestra para el campo',
    cuerpo: `
      <div id="muestra-alerta"></div>
      <div class="aviso">
        <h4>Para qué sirve</h4>
        <p>El archivo trae el <code>id_persona</code> de cada convocado.
        Precargalo como <strong>variable oculta</strong> en el instrumento y
        pedile a la plataforma que lo devuelva en el export: al ingestar,
        declarás que la columna trae el <strong>id_persona</strong> y el
        mapeo es directo.</p>
        <p>Resuelve el problema de fondo: las plataformas suelen generar ids
        nuevos en cada estudio, y entonces ninguna fila mapea.</p>
      </div>
      <div class="form-group">
        <label class="check">
          <input type="checkbox" id="muestra-contacto" />
          Incluir datos de contacto (nombre, documento, correo, celular)
        </label>
        <div class="field-hint">Solo si el equipo de campo necesita llamar o
        mandar el link. Es una <strong>reidentificación</strong>: queda
        registrada con tu usuario, igual que en una consulta.</div>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Exportar', clase: 'btn-orange', onClick: async (caja) => {
          const conContacto = $('#muestra-contacto', caja).checked;
          try {
            const muestra = await api.encuestas.muestra(encuestaId, conContacto);
            if (!muestra.personas) {
              $('#muestra-alerta', caja).innerHTML = alerta(
                'Esta ola todavía no tiene a nadie convocado: no hay muestra que exportar.');
              return;
            }
            descargarCsv(muestra.nombre_archivo, muestra.csv);
            cerrarModal();
            toast(`Muestra de ${muestra.personas} persona(s) exportada.`, 'ok');
          } catch (error) {
            $('#muestra-alerta', caja).innerHTML = alerta(error.message);
          }
        } },
    ],
  });
}

/* ── Ingesta ────────────────────────────────────────────────────── */

const MB = 1024 * 1024;
const enMb = (bytes) => `${(bytes / MB).toFixed(1)} MB`;

/* Pasa el archivo a base64 sin congelar la pantalla.

   El bucle manual sobre el `Uint8Array` corría entero en el hilo principal:
   con un export de varios MB la interfaz quedaba trabada justo mientras se
   suponía que mostraba avance. `readAsDataURL` lo hace el navegador, fuera
   del hilo, y además informa progreso. */
function leerBase64(archivo, alLeer) {
  return new Promise((resolver, rechazar) => {
    const lector = new FileReader();
    lector.onprogress = (evento) => {
      if (evento.lengthComputable) alLeer?.(evento.loaded / evento.total);
    };
    lector.onload = () => {
      alLeer?.(1);
      // data:<tipo>;base64,XXXX — el base64 arranca después de la coma.
      const texto = String(lector.result);
      resolver(texto.slice(texto.indexOf(',') + 1));
    };
    lector.onerror = () => rechazar(new Error('no se pudo leer el archivo del disco'));
    lector.readAsDataURL(archivo);
  });
}

/* Panel de avance de una subida.

   Tres fases, y solo dos tienen porcentaje real: leer el archivo y subirlo.
   Cuánto le falta al servidor para terminar de parsear el `.sav` no hay
   forma de saberlo, así que esa fase muestra el tiempo transcurrido. Es la
   cifra que importa: lo que el usuario necesita distinguir es «tarda» de
   «se colgó». */
function panelDeAvance(destino, archivo) {
  destino.innerHTML = `
    <div class="avance">
      <div class="avance-cab">
        <span class="avance-texto">Preparando…</span>
        <span class="avance-cifra"></span>
      </div>
      <div class="barra"><span style="width:0%"></span></div>
      <div class="avance-pie">${esc(archivo.name)} · ${enMb(archivo.size)}</div>
    </div>`;
  const texto = $('.avance-texto', destino);
  const cifra = $('.avance-cifra', destino);
  const barra = $('.barra span', destino);
  const caja = $('.avance', destino);
  let reloj = null;

  const detener = () => { clearInterval(reloj); reloj = null; };

  return {
    /* Fase con porcentaje: la barra avanza y la cifra lo dice. */
    medido(nombre, fraccion) {
      detener();
      caja.classList.remove('indeterminado');
      const pct = Math.max(0, Math.min(100, Math.round(fraccion * 100)));
      texto.textContent = nombre;
      cifra.textContent = `${pct}%`;
      barra.style.width = `${pct}%`;
    },
    /* Fase sin porcentaje: barra en movimiento y cronómetro. */
    abierto(nombre, nota) {
      if (reloj) return;
      caja.classList.add('indeterminado');
      barra.style.width = '100%';
      texto.textContent = nombre;
      const desde = Date.now();
      const tic = () => {
        const s = Math.round((Date.now() - desde) / 1000);
        cifra.textContent = s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
      };
      tic();
      reloj = setInterval(tic, 1000);
      if (nota) $('.avance-pie', destino).textContent = nota;
    },
    cerrar() { detener(); },
  };
}


/* Parser de CSV mínimo, con comillas dobles. El export de campo suele ser
   .xlsx; para eso se carga SheetJS bajo demanda. */
function parsearCSV(texto) {
  const filas = [];
  let campo = '';
  let fila = [];
  let enComillas = false;
  for (let i = 0; i < texto.length; i++) {
    const c = texto[i];
    if (enComillas) {
      if (c === '"') {
        if (texto[i + 1] === '"') { campo += '"'; i++; } else enComillas = false;
      } else campo += c;
    } else if (c === '"') enComillas = true;
    else if (c === ',' || c === ';' || c === '\t') { fila.push(campo); campo = ''; }
    else if (c === '\n') { fila.push(campo); filas.push(fila); fila = []; campo = ''; }
    else if (c !== '\r') campo += c;
  }
  if (campo || fila.length) { fila.push(campo); filas.push(fila); }
  return filas.filter((f) => f.some((v) => String(v).trim() !== ''));
}

function aObjetos(matriz) {
  if (!matriz.length) return { encabezados: [], filas: [] };
  const encabezados = matriz[0].map((h) => String(h).trim());
  const filas = matriz.slice(1).map((valores) => {
    const objeto = {};
    encabezados.forEach((h, i) => { if (h) objeto[h] = valores[i]; });
    return objeto;
  });
  return { encabezados: encabezados.filter(Boolean), filas };
}

async function leerArchivo(archivo) {
  if (/\.(xlsx|xls)$/i.test(archivo.name)) {
    const { read, utils } = await import('https://cdn.jsdelivr.net/npm/xlsx@0.18.5/+esm');
    const libro = read(await archivo.arrayBuffer(), { type: 'array' });
    const hoja = libro.Sheets[libro.SheetNames[0]];
    return aObjetos(utils.sheet_to_json(hoja, { header: 1, raw: false, defval: '' }));
  }
  return aObjetos(parsearCSV(await archivo.text()));
}

/* Qué datos patronímicos puede traer un .sav, con el patrón que se usa para
   sugerir la variable. Van a la bóveda y solo a la bóveda. */
/* Addendum de R3.9 — a qué campo de la bóveda puede apuntar una variable
   marcada como demográfica. Es la misma lista del backend
   (`sav.CAMPOS_DEMOGRAFICOS`); el orden es el de la ficha del panelista.

   Los patronímicos dejaron de ser un bloque aparte del modo «crear
   individuos»: son un subconjunto de este marcado, y el marcado vale para
   los dos modos. */
/* Los campos de `persona`: patronímicos y de contacto. Coincide con
   `sav.CAMPOS_DEMOGRAFICOS`. */
const CAMPOS_DE_PERSONA = [
  ['nombre', 'Nombre'],
  ['documento', 'Documento'],
  ['email', 'Correo'],
  ['celular', 'Celular'],
  ['fecha_nacimiento', 'Fecha de nacimiento'],
  ['contacto', 'Contacto preferido'],
];

/* R3.14 — y los atributos del catálogo, que es donde viven ahora los
   segmentadores: sexo, localidad, tramo etario, edad y cualquiera que un
   admin haya definido. La lista la trae el servidor: si estuviera escrita
   acá, definir un atributo nuevo no alcanzaría para poder cargarlo, que es
   justamente lo que este requisito resuelve. */
let catalogoDeAtributos = [];
const DEMOGRAFICOS = () => [
  ...CAMPOS_DE_PERSONA,
  ...catalogo.activos(catalogoDeAtributos).map((a) => [
    a.clave, a.es_especial ? `${a.etiqueta} · especial` : a.etiqueta,
  ]),
];

/* Marcar sin campo: se excluye del store semántico y no se guarda. Es para
   las demográficas que la bóveda no modela —`EDAD` es el caso: la bóveda
   guarda fecha de nacimiento y deriva el tramo—. Tiene que coincidir con
   `sav.SOLO_EXCLUIR`. */
const SOLO_EXCLUIR = '(no guardar)';

/* Las opciones de una cerrada se escriben "1=Fernet; 2=Whisky". */
function parsearOpciones(texto) {
  if (!texto) return null;
  const opciones = {};
  texto.split(/[;\n]/).forEach((par) => {
    const [codigo, ...resto] = par.split('=');
    if (codigo && resto.length) opciones[codigo.trim()] = resto.join('=').trim();
  });
  return Object.keys(opciones).length ? opciones : null;
}

/* R3.13 — la misma pantalla sirve para los dos destinos.

   `destino` es `{tipo, id, nombre, panel_id?}`:

   - `encuesta`: la ingesta de siempre. Incorpora al panel de la encuesta y
     registra la participación (R3.9.a/b), y la finalidad obligatoria del
     alta es el contacto.
   - `carga`: R3.13. **No** crea membresía ni participación —esa gente no es
     panelista— y la finalidad obligatoria es el uso semántico, que es la
     base de lo único que se va a hacer con esos datos.

   Todo lo demás —mapeo de variables, marcado de demográficos, tipo de
   identificador, dedup, guardrail de PII— es idéntico, y por eso es la misma
   pantalla y no una nueva que haya que aprender. */
export function abrirCargaDePanelistas(carga, alTerminar) {
  return abrirIngesta(
    { tipo: 'carga', id: carga.id, nombre: carga.nombre }, alTerminar);
}

function abrirIngesta(destino, alTerminar) {
  const esCarga = destino.tipo === 'carga';
  const encuesta = destino;
  let datosArchivo = { encabezados: [], filas: [] };

  const caja = modal({
    titulo: `${esCarga ? 'Cargar panelistas' : 'Ingestar respuestas'} — ${encuesta.nombre}`,
    ancho: '720px',
    cuerpo: `
      <div id="ing-alerta"></div>
      ${esCarga ? `
      <div class="aviso destacado">
        <h4>Esta gente no queda en ningún panel</h4>
        <p>Los individuos de esta carga <strong>no se incorporan a ningún
        panel</strong>: no generan membresía ni participación, no se los puede
        convocar y no entran en la composición, la brecha ni el muestreo de
        ningún panel.</p>
        <p>Sus respuestas sí quedan consultables por concepto, y sus
        demográficos permiten filtrarlos. Si después se decide sumar a alguno
        de ellos a un panel, se hace desde el resultado de una consulta, sin
        volver a cargar nada.</p>
      </div>` : ''}
      <div class="aviso">
        <h4>Qué cruza al store semántico</h4>
        <p>Solo el <code>id_persona</code>, el <code>ref_estudio</code>, el texto de la
        respuesta y su embedding. El id de la plataforma de campo se traduce a
        <code>id_persona</code> <strong>en la bóveda</strong>; ninguna PII sale de acá.</p>
        <p>Se ingesta únicamente a quien tenga <strong>uso semántico</strong> vigente.</p>
      </div>

      <div class="form-group" style="margin-top:1.25rem">
        <label>1 · Archivo de respuestas (formato ancho)</label>
        <div class="dropzone" id="dz">
          <span class="dz-icon">📄</span>
          <span class="dz-texto">Elegí el .sav, .xlsx o .csv del export de campo</span>
          <span class="dz-hint">Una fila por individuo, una columna por pregunta.
            Con un <strong>.sav</strong> de SPSS, los textos de las preguntas y las
            etiquetas de respuesta se precargan solas desde el archivo.</span>
        </div>
        <input type="file" id="archivo" accept=".sav,.xlsx,.xls,.csv,.tsv,.txt" class="hidden" />
        <div id="resumen-archivo" class="field-hint"></div>
      </div>

      <div class="form-group">
        <label>2 · Columna que identifica al respondente</label>
        <div class="grid-2">
          <select class="fselect" name="columna_id" id="columna-id" disabled>
            <option value="">Cargá primero el archivo</option>
          </select>
          <select class="fselect" id="tipo-identificador">
            <option value="alias">Trae el id de la plataforma de campo</option>
            <option value="id_persona">Trae el id_persona del sistema (precargado)</option>
            <option value="documento">Trae el documento</option>
            <option value="email">Trae el correo</option>
          </select>
        </div>
        <div class="field-hint" id="hint-identificador"></div>
      </div>

      <div class="form-group">
        <label>3 · Variables del archivo</label>
        <div class="field-hint" style="margin:-0.3rem 0 0.6rem">
          La última columna dice qué es cada variable. Las marcadas como
          <strong>demográficas no se ingestan al store semántico</strong>: su
          valor va a la ficha del panelista, en la bóveda. Es la decisión que
          evita duplicar segmentadores del lado que se quiere mantener limpio.
        </div>
        <div id="preguntas"></div>
        <button class="btn btn-outline btn-sm" id="add-pregunta" style="margin-top:0.6rem">+ Agregar pregunta</button>
      </div>

      <!-- R3.9 — Solo para .sav: dar de alta a la gente en la misma carga.
           Se muestra recién cuando el archivo es un .sav porque es el único
           formato del que el backend puede leer la metadata; con un Excel
           el alta sigue siendo por la pantalla de Panelistas. -->
      <div id="bloque-sav" class="hidden">
        <div class="form-group">
          <label>4 · ¿Los panelistas ya están en el sistema?</label>
          <select class="fselect" id="modo-sav">
            <option value="existen">Sí, ya existen: solo vincular las respuestas</option>
            <option value="crear_individuos">No: darlos de alta en esta carga</option>
          </select>
        </div>

        <div id="alta-sav" class="hidden">
          <div class="aviso">
            <h4>Base legal del alta</h4>
            <p>Para crear personas desde el archivo hay que indicar
            <strong>qué variable evidencia el consentimiento y qué valor cuenta
            como afirmativo</strong>, para las dos finalidades. Puede ser la
            misma variable.</p>
            ${esCarga ? `
            <p>En una carga sin panel la finalidad obligatoria es el
            <strong>uso semántico</strong>: es la base de lo único que se va a
            hacer con estos datos. Quien no la evidencie <strong>no se
            crea</strong>. El consentimiento de contacto es opcional acá; quien
            no lo evidencie se crea igual, pero nunca va a poder ser
            convocado.</p>` : `
            <p>Quien no evidencie el consentimiento de contacto
            <strong>no se crea</strong>: sus respuestas no se van a poder
            vincular a nadie.</p>`}
          </div>

          <div class="field-hint" style="margin-bottom:1rem">
            Los datos patronímicos salen del marcado de la lista de variables
            de arriba: la que esté marcada como <strong>Nombre</strong>,
            <strong>Documento</strong> o <strong>Correo</strong> es la que
            identifica a la persona. Hace falta al menos una de las tres.
          </div>

          <div class="form-group">
            <label>Consentimiento de contacto y participación</label>
            <div class="grid-3">
              <select class="fselect" id="cons-contacto-var"></select>
              <input class="finput" id="cons-contacto-valor" placeholder="Valor afirmativo (1, Sí…)" />
              <input class="finput" id="cons-contacto-version" placeholder="Versión del texto consentido" />
            </div>
          </div>

          <label class="check" style="margin:0.2rem 0 0.8rem">
            <input type="checkbox" id="cons-misma" checked />
            La misma variable y el mismo valor cubren el uso semántico
          </label>

          <div class="form-group hidden" id="grupo-cons-semantico">
            <label>Consentimiento de uso semántico</label>
            <div class="grid-3">
              <select class="fselect" id="cons-semantico-var"></select>
              <input class="finput" id="cons-semantico-valor" placeholder="Valor afirmativo" />
              <input class="finput" id="cons-semantico-version" placeholder="Versión del texto consentido" />
            </div>
          </div>
        </div>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Ingestar', clase: 'btn-orange', onClick: () => correr() },
    ],
  });

  /* Editor de preguntas. */
  const preguntas$ = $('#preguntas', caja);
  const TIPOS = ['cerrada', 'abierta', 'escala', 'numerica'];

  const opcionesDeRol = (rol) => [
    ['', 'Pregunta del estudio'],
    ...DEMOGRAFICOS().map(([campo, etiqueta]) => [campo, `Demográfica · ${etiqueta}`]),
    [SOLO_EXCLUIR, 'Demográfica · no guardar'],
  ].map(([valor, etiqueta]) =>
    `<option value="${esc(valor)}" ${valor === (rol || '') ? 'selected' : ''}>${esc(etiqueta)}</option>`
  ).join('');

  /* El catálogo llega del servidor, así que el modal se pinta primero y los
     desplegables de rol se repintan cuando llega. Es lo único que depende de
     él, y esperarlo antes de abrir dejaría la pantalla en blanco por una
     lista de opciones. */
  catalogo.cargar().then((items) => {
    catalogoDeAtributos = items;
    $$('.pregunta-fila .p-rol', caja).forEach((select) => {
      const elegido = select.value;
      select.innerHTML = opcionesDeRol(elegido);
      select.value = elegido;
    });
  });

  const textoDeOpciones = (opciones) => (opciones
    ? Object.entries(opciones).map(([c, e]) => `${c}=${e}`).join('; ') : '');

  const filaPregunta = (codigo = '', texto = '', tipo = 'cerrada',
                        opciones = null, rol = '') => `
    <div class="pregunta-fila" data-tenia-etiquetas="${opciones ? '1' : '0'}">
      <input type="text" class="p-codigo" placeholder="P1" value="${esc(codigo)}" />
      <input type="text" class="p-texto" placeholder="Texto de la pregunta" value="${esc(texto)}" />
      <select class="fselect p-tipo">
        ${TIPOS.map((valor) => `<option value="${valor}" ${valor === tipo ? 'selected' : ''}>
          ${valor === 'numerica' ? 'numérica' : valor}</option>`).join('')}
      </select>
      <input type="text" class="p-opciones" placeholder="1=Fernet; 2=Whisky"
             value="${esc(textoDeOpciones(opciones))}" />
      <select class="fselect p-rol" title="Qué es esta variable">${opcionesDeRol(rol)}</select>
      <button class="modal-close p-quitar" title="Quitar">×</button>
    </div>`;

  /* R3.9.e — el campo de códigos según el tipo, y los avisos que van con él.

     El campo estaba habilitado para todos los tipos, incluso `abierta`,
     donde no hay códigos posibles: solo invitaba a cargar un mapeo que
     nunca se iba a aplicar. Y una variable marcada como demográfica no va
     al store semántico, así que su texto y su tipo dejan de importar. */
  function ajustarFila(fila) {
    const tipo = fila.querySelector('.p-tipo').value;
    const rol = fila.querySelector('.p-rol').value;
    const opciones$ = fila.querySelector('.p-opciones');
    const texto$ = fila.querySelector('.p-texto');
    const tipo$ = fila.querySelector('.p-tipo');
    const esDemografica = rol !== '';

    // El texto y el tipo solo gobiernan lo que se embebe.
    texto$.disabled = esDemografica;
    tipo$.disabled = esDemografica;
    texto$.classList.toggle('inerte', esDemografica);
    tipo$.classList.toggle('inerte', esDemografica);

    // Una abierta no tiene códigos. Lo cargado se guarda y vuelve si el
    // tipo cambia a uno que sí los admite: perderlo castigaría un clic.
    const admiteCodigos = esDemografica || tipo !== 'abierta';
    if (!admiteCodigos && opciones$.value) {
      fila.dataset.opcionesGuardadas = opciones$.value;
      opciones$.value = '';
    } else if (admiteCodigos && !opciones$.value && fila.dataset.opcionesGuardadas) {
      opciones$.value = fila.dataset.opcionesGuardadas;
      delete fila.dataset.opcionesGuardadas;
    }
    opciones$.disabled = !admiteCodigos;
    opciones$.classList.toggle('inerte', !admiteCodigos);

    opciones$.placeholder = !admiteCodigos ? 'Una abierta no tiene códigos'
      : esDemografica ? '1=Femenino; 2=Masculino'
      // Las numéricas de SPSS suelen traer etiquetas solo para los valores
      // especiales, y esa traducción importa: «→ 99» es ruido, «→ No
      // contesta» es información.
      : tipo === 'numerica' ? 'Solo valores especiales: 98=No sabe; 99=No contesta'
      : '1=Fernet; 2=Whisky';

    avisarDeLaFila(fila, tipo, rol, admiteCodigos);
  }

  function avisarDeLaFila(fila, tipo, rol, admiteCodigos) {
    const teniaEtiquetas = fila.dataset.teniaEtiquetas === '1';
    const hayCodigos = Boolean(
      fila.querySelector('.p-opciones').value || fila.dataset.opcionesGuardadas);
    let aviso = '';
    if (rol === '') {
      // Da igual si las etiquetas venían del archivo o las escribió alguien
      // recién: en los dos casos se pierden, y en los dos hay que avisar
      // antes de confirmar.
      if (tipo === 'abierta' && (teniaEtiquetas || hayCodigos)) {
        aviso = 'Esta variable tiene etiquetas de respuesta: como abierta se descartan.';
      } else if (tipo === 'cerrada' && !hayCodigos) {
        aviso = 'Sin etiquetas, sus valores se embeben crudos (el código, no la respuesta).';
      }
    }
    let nota = fila.querySelector('.p-aviso');
    if (!aviso) { nota?.remove(); return; }
    if (!nota) {
      nota = document.createElement('div');
      nota.className = 'p-aviso';
      fila.appendChild(nota);
    }
    nota.textContent = aviso;
  }

  const agregarFila = (codigo, texto, tipo, opciones, rol) => {
    preguntas$.insertAdjacentHTML(
      'beforeend', filaPregunta(codigo, texto, tipo, opciones, rol));
    const fila = preguntas$.lastElementChild;
    fila.querySelector('.p-quitar').onclick = (e) => {
      e.preventDefault();
      e.target.closest('.pregunta-fila').remove();
    };
    fila.querySelector('.p-tipo').onchange = () => ajustarFila(fila);
    fila.querySelector('.p-rol').onchange = () => ajustarFila(fila);
    fila.querySelector('.p-opciones').oninput = () => ajustarFila(fila);
    ajustarFila(fila);
  };
  agregarFila();
  $('#add-pregunta', caja).onclick = (e) => { e.preventDefault(); agregarFila(); };

  /* R3.12 — qué trae la columna de identidad. El default es `alias`, la
     conducta de siempre, para que nadie tenga que cambiar nada. */
  const AYUDA_IDENTIFICADOR = {
    alias: 'El id que la plataforma le puso al respondente, el mismo que se '
      + 'guardó al enrolar. Ojo: muchas plataformas generan ids nuevos en cada '
      + 'estudio, y entonces ninguna fila va a mapear.',
    id_persona: 'El identificador del sistema, precargado en el instrumento '
      + 'desde «Exportar muestra». Es el mapeo directo y el único que no '
      + 'depende de lo que haga la plataforma.',
    documento: 'Respaldo, cuando no se pudo precargar. Al terminar queda '
      + 'registrado el alias de esta plataforma, así la próxima carga ya no '
      + 'necesita el documento.',
    email: 'Respaldo, cuando no se pudo precargar. Al terminar queda '
      + 'registrado el alias de esta plataforma, así la próxima carga ya no '
      + 'necesita el correo.',
  };
  const tipoId$ = $('#tipo-identificador', caja);
  const refrescarAyudaIdentificador = () => {
    $('#hint-identificador', caja).textContent = AYUDA_IDENTIFICADOR[tipoId$.value];
  };
  tipoId$.onchange = refrescarAyudaIdentificador;
  refrescarAyudaIdentificador();

  $('#modo-sav', caja).onchange = (e) => {
    $('#alta-sav', caja).classList.toggle('hidden', e.target.value !== 'crear_individuos');
  };
  $('#cons-misma', caja).onchange = (e) => {
    $('#grupo-cons-semantico', caja).classList.toggle('hidden', e.target.checked);
  };

  /* Carga del archivo. */
  const zona = $('#dz', caja);
  const entrada = $('#archivo', caja);
  zona.onclick = () => entrada.click();
  zona.ondragover = (e) => { e.preventDefault(); zona.classList.add('activa'); };
  zona.ondragleave = () => zona.classList.remove('activa');
  zona.ondrop = (e) => {
    e.preventDefault();
    zona.classList.remove('activa');
    if (e.dataTransfer.files[0]) cargarArchivo(e.dataTransfer.files[0]);
  };
  entrada.onchange = (e) => { if (e.target.files[0]) cargarArchivo(e.target.files[0]); };

  async function cargarArchivo(archivo) {
    try {
      // `await`, no `return` pelado: sin él la promesa se va sin pasar por
      // el catch de acá y el error queda como «uncaught in promise», con la
      // pantalla congelada en el cartel de «analizando».
      if (/\.sav$/i.test(archivo.name)) return await cargarSav(archivo);
      datosArchivo.savBase64 = null;
      datosArchivo = await leerArchivo(archivo);
      $('#resumen-archivo', caja).textContent =
        `${archivo.name} — ${datosArchivo.filas.length} filas, ${datosArchivo.encabezados.length} columnas.`;
      const select = $('#columna-id', caja);
      select.disabled = false;
      const probable = datosArchivo.encabezados.find(
        (h) => /^(id|id_en_origen|respondent|resp_id|codigo_resp)/i.test(h));
      select.innerHTML = datosArchivo.encabezados
        .map((h) => `<option value="${esc(h)}" ${h === probable ? 'selected' : ''}>${esc(h)}</option>`).join('');

      // Precarga las preguntas con las columnas que no son la de identidad.
      preguntas$.innerHTML = '';
      datosArchivo.encabezados
        .filter((h) => h !== (probable || ''))
        .forEach((h) => agregarFila(h, ''));
      if (!preguntas$.children.length) agregarFila();
    } catch (error) {
      // Los errores del backend ya vienen redactados para que se entiendan
      // —archivo demasiado grande, .sav ilegible, sin permiso—: envolverlos
      // en «no se pudo leer el archivo» los empeora.
      $('#ing-alerta', caja).innerHTML = alerta(
        error instanceof api.ErrorApi
          ? error.message
          : `No se pudo leer el archivo: ${error.message}`);
    }
  }

  /* R3.9 — el .sav lo parsea el backend: no hay librería cliente confiable
     para SPSS y los exports de campo pesan. Lo que vuelve es una propuesta
     editable, no un hecho: la metadata de SPSS suele venir truncada o
     críptica, y el texto de la pregunta es justamente lo que se vectoriza. */
  async function cargarSav(archivo) {
    const alerta$ = $('#ing-alerta', caja);
    const avance = panelDeAvance(alerta$, archivo);
    let analisis;
    let base64;
    try {
      avance.medido('Leyendo el archivo', 0);
      base64 = await leerBase64(archivo, (f) => avance.medido('Leyendo el archivo', f));

      avance.medido('Subiendo al servidor', 0);
      analisis = await (esCarga ? api.cargas : api.sav).analizar(encuesta.id, base64, {
        alSubir: (f) => (f < 1
          ? avance.medido('Subiendo al servidor', f)
          : avance.abierto('Analizando el archivo en el servidor…',
              'SPSS se lee entero antes de contestar: con archivos grandes '
              + 'puede tardar unos minutos.')),
      });
    } finally {
      avance.cerrar();
    }
    datosArchivo = { encabezados: analisis.variables.map((v) => v.codigo),
                     filas: [], savBase64: base64 };

    const select = $('#columna-id', caja);
    select.disabled = false;
    const probable = analisis.candidatas_a_id[0] || analisis.variables[0]?.codigo;
    select.innerHTML = analisis.variables
      .map((v) => `<option value="${esc(v.codigo)}" ${v.codigo === probable ? 'selected' : ''}>
        ${esc(v.codigo)}${analisis.candidatas_a_id.includes(v.codigo) ? ' (valores únicos)' : ''}
      </option>`).join('');

    preguntas$.innerHTML = '';
    analisis.variables
      .filter((v) => v.codigo !== probable)
      // El rol viene presugerido, no aplicado: la fila queda a la vista con
      // su marca para confirmar o corregir. Una variable puede ser
      // segmentador en un estudio y ser el objeto de análisis en otro.
      .forEach((v) => agregarFila(
        v.codigo, v.texto, v.tipo, v.opciones, v.demografica_sugerida || ''));
    if (!preguntas$.children.length) agregarFila();

    $('#resumen-archivo', caja).textContent =
      `${archivo.name} — ${analisis.filas} filas, ${analisis.variables.length} variables. `
      + 'Revisá los textos antes de confirmar: es lo que se vectoriza.';
    alerta$.innerHTML = analisis.avisos.length
      ? alerta(analisis.avisos.map((a) => a.mensaje).join(' '), 'warn')
      : '';

    prepararAltaSav(analisis.variables.map((v) => v.codigo));
  }

  /* R3.9 — el modo «crear los individuos en esta carga». Solo aparece con un
     .sav, y solo deja confirmar si se declaró de dónde sale la evidencia de
     consentimiento: es la base legal del alta, no un campo más. */
  function prepararAltaSav(codigos) {
    $('#bloque-sav', caja).classList.remove('hidden');

    const opciones = (vacia) =>
      (vacia ? '<option value="">— ninguna —</option>' : '')
      + codigos.map((c) => `<option value="${esc(c)}">${esc(c)}</option>`).join('');

    $('#cons-contacto-var', caja).innerHTML = opciones(true);
    $('#cons-semantico-var', caja).innerHTML = opciones(true);

    // Precarga: las variables que parecen de consentimiento suelen llamarse
    // así. Es una sugerencia y se cambia con el desplegable.
    const probable = codigos.find((c) => /^(cons|consent|autoriz|acepta)/i.test(c));
    if (probable) {
      $('#cons-contacto-var', caja).value = probable;
      $('#cons-semantico-var', caja).value = probable;
    }
  }

  /* El marcado demográfico de la lista de variables: `{variable: campo}`.
     Vale para los dos modos, y es de donde sale el mapeo patronímico que el
     alta necesita. */
  function marcadoDemografico() {
    const marcado = {};
    $$('.pregunta-fila', preguntas$).forEach((fila) => {
      const codigo = fila.querySelector('.p-codigo').value.trim();
      const rol = fila.querySelector('.p-rol').value;
      if (codigo && rol) marcado[codigo] = rol;
    });
    return marcado;
  }

  /* Arma el cuerpo del modo «crear individuos». Valida acá lo que el
     backend también valida: no para reemplazarlo —la autoridad es el
     backend— sino para no hacerle subir el archivo entero a alguien que se
     olvidó de completar un campo. */
  function cuerpoDeAltaSav() {
    const marcado = marcadoDemografico();
    const campos = new Set(Object.values(marcado));
    if (!['documento', 'email', 'nombre'].some((c) => campos.has(c))) {
      throw new Error('Para crear personas hace falta marcar al menos una '
        + 'variable como documento, correo o nombre: sin eso no hay con qué '
        + 'identificarlas.');
    }

    const contacto = {
      variable: $('#cons-contacto-var', caja).value,
      valor_afirmativo: $('#cons-contacto-valor', caja).value.trim(),
      version_texto: $('#cons-contacto-version', caja).value.trim(),
    };
    const misma = $('#cons-misma', caja).checked;
    const semantico = misma ? { ...contacto } : {
      variable: $('#cons-semantico-var', caja).value,
      valor_afirmativo: $('#cons-semantico-valor', caja).value.trim(),
      version_texto: $('#cons-semantico-version', caja).value.trim(),
    };

    for (const [etiqueta, regla] of [['contacto', contacto], ['uso semántico', semantico]]) {
      if (!regla.variable || !regla.valor_afirmativo || !regla.version_texto) {
        throw new Error(`Falta declarar la evidencia de consentimiento de `
          + `${etiqueta}: variable, valor afirmativo y versión del texto.`);
      }
    }

    return {
      modo: 'crear_individuos',
      // En una carga no hay panel al que incorporar: es el punto.
      ...(esCarga ? {} : { panel_id: encuesta.panel_id }),
      evidencia_consentimiento: {
        contacto_participacion: contacto,
        uso_semantico: semantico,
      },
    };
  }

  /* El aviso del modal vive arriba de todo y el botón de Ingestar, abajo
     del todo: con una lista larga de variables, al apretar el botón el
     usuario está mirando el pie y no ve nada de lo que pasa. Ni la barra de
     avance ni, peor, el error que le dice qué le falta completar. Así que
     todo lo que escribe ahí sube la vista primero. */
  function avisarEnIngesta(html) {
    const alerta$ = $('#ing-alerta', caja);
    alerta$.innerHTML = html;
    // De golpe y no con `smooth`: el desplazamiento suave tarda unos 300 ms
    // y una respuesta rápida termina antes, así que el usuario alcanza a ver
    // la vista moviéndose hacia algo que ya no está. Acá lo que importa es
    // que el cartel esté a la vista en el momento, no que el viaje sea lindo.
    caja.scrollTop = 0;
    return alerta$;
  }

  async function correr() {
    const alerta$ = avisarEnIngesta('');
    const columnaId = $('#columna-id', caja).value;
    if (!datosArchivo.filas.length && !datosArchivo.savBase64) {
      avisarEnIngesta(alerta('Cargá primero el archivo de respuestas.'));
      return;
    }
    if (!columnaId) {
      avisarEnIngesta(alerta('Elegí la columna que identifica al respondente.'));
      return;
    }
    const preguntas = $$('.pregunta-fila', preguntas$).map((fila, i) => ({
      codigo: fila.querySelector('.p-codigo').value.trim(),
      texto: fila.querySelector('.p-texto').value.trim(),
      tipo: fila.querySelector('.p-tipo').value,
      opciones: parsearOpciones(fila.querySelector('.p-opciones').value.trim()),
      orden: i + 1,
    })).filter((p) => p.codigo && p.texto);

    if (!preguntas.length) {
      avisarEnIngesta(alerta(
        'Cada pregunta necesita su código y su texto: el texto es lo que se embebe.'));
      return;
    }

    let extraSav = {};
    if (datosArchivo.savBase64 && $('#modo-sav', caja).value === 'crear_individuos') {
      try {
        extraSav = cuerpoDeAltaSav();
      } catch (error) {
        avisarEnIngesta(alerta(error.message));
        return;
      }
    }

    // Avance para los dos caminos, no solo para el `.sav`. La espera larga
    // es la del servidor —resolver cada individuo y calcular embeddings— y
    // esa la tiene igual un `.csv`: sin panel, el modal se quedaba quieto y
    // no había forma de distinguir «está trabajando» de «no responde».
    const avance = panelDeAvance(
      alerta$,
      datosArchivo.savBase64
        ? { name: 'archivo .sav', size: datosArchivo.savBase64.length * 0.75 }
        : { name: `${datosArchivo.filas.length} fila(s)`, size: 0 });
    caja.scrollTop = 0;
    const enCurso = bloquearModal(caja);
    try {
      const resultado = datosArchivo.savBase64
        ? await (esCarga ? api.cargas : api.sav).ingestar(encuesta.id, {
            archivo_base64: datosArchivo.savBase64,
            preguntas, columna_id: columnaId, origen: 'sav',
            // El marcado y el tipo de identificador valen para los dos modos
            // y para cualquier formato: van siempre, no solo al crear gente.
            demograficas: marcadoDemografico(),
            tipo_identificador: tipoId$.value,
            ...extraSav,
          }, {
            alSubir: (f) => (f < 1
              ? avance.medido('Subiendo al servidor', f)
              : avance.abierto('Ingestando en el servidor…',
                  'Se leen las respuestas, se resuelve cada individuo y se '
                  + 'calculan los embeddings. Con archivos grandes tarda.')),
          })
        : await (() => {
            avance.abierto('Ingestando en el servidor…',
              'Se resuelve cada individuo y se calculan los embeddings. '
              + 'Con archivos grandes tarda.');
            return (esCarga ? api.cargas.ingestarFilas : api.encuestas.ingestar)(encuesta.id, {
              preguntas, filas: datosArchivo.filas, columnaId,
              origen: datosArchivo.origen || undefined,
              demograficas: marcadoDemografico(),
              tipoIdentificador: tipoId$.value,
            });
          })();
      avance.cerrar();
      enCurso.soltar();
      cerrarModal();
      mostrarResultado('Ingesta terminada', [
        ['Respuestas escritas', resultado.respuestas_escritas],
        ['Personas', resultado.personas],
        ['Sin mapear', (resultado.sin_mapear || []).length, true],
        ['Sin consentimiento', (resultado.sin_consentimiento || []).length, true],
        ...(resultado.creacion_de_individuos ? [
          ['Personas creadas', resultado.creacion_de_individuos.resumen.creados],
          ['Sin consentimiento',
           resultado.creacion_de_individuos.resumen.sin_consentimiento, true],
        ] : []),
        // Addendum de R3.9: la ingesta ahora incorpora al panel y registra
        // la participación. Son cifras que cambian la composición y la tasa
        // de respuesta de la ola, así que se muestran siempre.
        // R3.13.f — en una carga sin panel no se informan, porque no se
        // crean: mostrarlas en cero sugeriría que algo falló.
        ...(esCarga ? [] : [
          ['Nuevos miembros del panel', resultado.membresias_nuevas || 0],
          ['Ya eran miembros', resultado.membresias_existentes || 0],
          ['Con baja en el panel', (resultado.membresias_en_baja || []).length, true],
          ['Participaciones nuevas', resultado.participaciones_nuevas || 0],
          ['Participaciones actualizadas', resultado.participaciones_actualizadas || 0],
        ]),
        // Addendum de R3.9: qué quedó fuera del store semántico por ser
        // demográfico, y qué no se pudo volcar a la bóveda por discrepar.
        ['Variables demográficas excluidas', (resultado.excluidas_por_demografica || []).length],
        ['Datos completados en la bóveda', resultado.demograficos_completados || 0],
        ['Discrepancias con la ficha', (resultado.discrepancias_demograficas || []).length, true],
      ], [
        resultado.creacion_de_individuos?.aviso_sin_consentimiento?.mensaje,
        resultado.creacion_de_individuos?.aviso_sin_la_otra_finalidad?.mensaje,
        (resultado.sin_mapear || []).length
          ? `${resultado.sin_mapear.length} fila(s) no se pudieron vincular a ningún panelista: ${resumirSinMapear(resultado)}.`
          : null,
        resultado.alias_registrados
          ? `Se registró el id de esta plataforma para ${resultado.alias_registrados} persona(s): la próxima carga de este estudio ya no va a necesitar el documento ni el correo.`
          : null,
        (resultado.sin_consentimiento || []).length
          ? `${resultado.sin_consentimiento.length} panelista(s) quedaron fuera por no tener uso semántico vigente.`
          : null,
        resultado.membresias_nuevas
          ? `${resultado.membresias_nuevas} persona(s) respondieron sin ser miembros del panel y quedaron incorporadas: ahora entran en el muestreo y en la composición.`
          : null,
        (resultado.membresias_en_baja || []).length
          ? `${resultado.membresias_en_baja.length} persona(s) respondieron pero tienen la membresía dada de baja en este panel. No se reactivó sola: si corresponde reincorporarlas, hay que hacerlo desde el panel.`
          : null,
        (resultado.excluidas_por_demografica || []).length
          ? `No se ingestaron al store semántico, por estar marcadas como demográficas: ${resultado.excluidas_por_demografica.join(', ')}.`
          : null,
        (resultado.discrepancias_demograficas || []).length
          ? `${resultado.discrepancias_demograficas.length} dato(s) del archivo difieren de lo que ya estaba en la ficha del panelista y no se pisaron: ${resumirDiscrepancias(resultado.discrepancias_demograficas)}. El archivo de un estudio no es autoridad sobre la ficha; revisalo desde Panelistas.`
          : null,
      ].filter(Boolean).join(' '));
      if (esCarga) {
        // La pantalla que abrió la carga es la de panelistas, y la gente
        // recién incorporada tiene que aparecer ahí sin recargar a mano.
        await alTerminar?.();
      } else {
        await cargarParticipacion(encuesta.id);
        await verificarCruce(encuesta.id);
      }
    } catch (error) {
      avance.cerrar();
      enCurso.soltar();
      avisarEnIngesta(alerta(error.message));
    }
  }
}

/* ── R4.5 · WhatsApp Flow ───────────────────────────────────────── */

const MOTIVOS_EXCLUSION = {
  sin_consentimiento: 'sin consentimiento de contacto vigente',
  sin_preferencia_whatsapp: 'no aceptó recibir WhatsApp',
  sin_celular: 'sin celular cargado',
  celular_invalido: 'el celular cargado no es un número válido',
  no_existe: 'la persona ya no existe',
};

async function cargarEstadoFlow(encuestaId) {
  const caja = $('#flow');
  if (!caja) return;
  try {
    const [estado, destinatarios] = await Promise.all([
      api.flow.estado(encuestaId),
      api.flow.destinatarios(encuestaId).catch(() => null),
    ]);
    caja.innerHTML = `
      <div class="alert ${estado.puede_enviar ? 'alert-success' : 'alert-warn'}">
        ${estado.puede_enviar
          ? 'El Flow está publicado y la plantilla aprobada: se puede enviar.'
          : `No se puede enviar todavía:<ul>${
              estado.motivos.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>`}
      </div>
      <dl class="kv">
        <!-- El idioma va pegado al nombre y no en una fila aparte: dos
             plantillas con el mismo nombre en distintos idiomas son dos
             plantillas distintas, y sin el idioma la tarjeta no las
             distingue. -->
        <dt>Plantilla</dt><dd><span class="mono">${esc(estado.plantilla || '—')}</span>
          ${estado.idioma ? ` · <span class="mono">${esc(estado.idioma)}</span>` : ''}
          ${estado.plantilla_estado
            ? `<span class="badge ${estado.plantilla_estado === 'APPROVED'
                 ? 'badge-on' : 'badge-off'}">${esc(estado.plantilla_estado)}</span>` : ''}</dd>
        <dt>Flow</dt><dd>${esc(estado.flow?.nombre || '—')}
          ${estado.flow ? `<span class="badge ${estado.flow.estado === 'PUBLISHED'
             ? 'badge-on' : 'badge-off'}">${esc(estado.flow.estado)}</span>` : ''}
          ${estado.flow_id
            ? `<div class="td-muted small mono">${esc(estado.flow_id)}</div>` : ''}</dd>
        ${estado.texto_boton
          ? `<dt>Botón</dt><dd>${esc(estado.texto_boton)}</dd>` : ''}
      </dl>
      ${destinatarios ? pintarDestinatarios(destinatarios) : ''}`;
  } catch (error) {
    caja.innerHTML = `<div class="alert alert-error">${esc(error.message)}</div>`;
  }
}

function pintarDestinatarios(destinatarios) {
  const excluidos = Object.entries(destinatarios.excluidos || {});
  return `
    <div class="resultado" style="margin-top:1rem">
      <div class="r"><div class="r-num">${destinatarios.convocados}</div>
        <div class="r-label">Convocados</div></div>
      <div class="r"><div class="r-num">${destinatarios.destinatarios.length}</div>
        <div class="r-label">Se les puede enviar</div></div>
      <div class="r"><div class="r-num">${destinatarios.ya_enviados.length}</div>
        <div class="r-label">Ya recibieron</div></div>
      <div class="r"><div class="r-num">${destinatarios.excluidos_total}</div>
        <div class="r-label">Quedan fuera</div></div>
    </div>
    ${excluidos.length ? `
      <div class="small" style="margin-top:.75rem">
        <strong>Por qué quedan fuera.</strong> Cada motivo se arregla distinto:
        <ul>${excluidos.map(([motivo, ids]) =>
          `<li>${ids.length} — ${esc(MOTIVOS_EXCLUSION[motivo] || motivo)}</li>`
        ).join('')}</ul>
      </div>` : ''}`;
}

/* R4.5 — elegir la plantilla, y nada más.

   Antes esta pantalla pedía tres cosas: el id del Flow, el nombre de la
   plantilla y el idioma. Eran tres veces el mismo dato: una plantilla de Meta
   ya trae adentro su idioma y, en su botón de Flow, el `flow_id`. Pedirlos
   por separado no solo era tedioso —había que ir a buscar el id a Meta y
   copiarlo a mano— sino que dejaba que se contradijeran: nada impedía
   configurar la plantilla `X` con el Flow de la `Y`, y eso recién se
   descubría al validar, o peor, al enviar.

   Ahora se elige de una lista de las plantillas aprobadas de la cuenta que
   tienen botón de Flow. El resto se resuelve solo. */
async function abrirConfiguracionFlow(encuesta) {
  const caja = modal({
    titulo: 'Enviar esta encuesta por WhatsApp',
    ancho: '640px',
    cuerpo: `
      <div id="flow-alerta"></div>
      <div class="aviso">
        <h4>El Flow se crea en Meta, no acá</h4>
        <p>El sistema <strong>solo envía</strong>. El Flow y la plantilla se
        arman y se publican en Meta; acá se elige cuál usar. Las respuestas se
        bajan de Meta y se ingestan por el flujo de siempre.</p>
        <p>La plantilla necesita <strong>aprobación de Meta</strong> y la
        revisión demora: no se puede crear la plantilla y convocar el mismo
        día.</p>
      </div>
      <div class="form-group" style="margin-top:1.25rem">
        <label>Plantilla de mensaje</label>
        <select class="fselect" name="elegida" id="plantilla-elegida" disabled>
          <option>Cargando las plantillas de la cuenta…</option>
        </select>
        <div class="field-hint" id="plantilla-hint">Solo se listan las
        aprobadas que llevan un botón de Flow. El idioma y el Flow vienen
        adentro de la plantilla.</div>
      </div>
      <div id="plantilla-detalle"></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Guardar', clase: 'btn-orange', onClick: async (c) => {
          const elegida = $('#plantilla-elegida', c).value;
          if (!elegida) {
            $('#flow-alerta', c).innerHTML = alerta(
              'Elegí una plantilla de la lista.');
            return;
          }
          const [plantilla, idioma] = JSON.parse(elegida);
          try {
            await api.flow.configurar(encuesta.id, { plantilla, idioma });
            cerrarModal();
            toast('Configuración guardada.', 'ok');
            contexto.irA('encuestas', { encuestaId: encuesta.id });
          } catch (error) {
            $('#flow-alerta', c).innerHTML = alerta(error.message);
          }
        } },
    ],
  });

  await cargarPlantillas(caja, encuesta);
  return caja;
}

async function cargarPlantillas(caja, encuesta) {
  const select = $('#plantilla-elegida', caja);
  let salida;
  try {
    salida = await api.flow.plantillas();
  } catch (error) {
    select.innerHTML = '<option value="">— no se pudieron cargar —</option>';
    $('#flow-alerta', caja).innerHTML = alerta(error.message);
    return;
  }

  const plantillas = salida.plantillas || [];
  if (!plantillas.length) {
    select.innerHTML = '<option value="">— no hay ninguna disponible —</option>';
    // Los avisos explican **por qué** está vacía, que es lo que hace falta
    // para saber a quién reclamarle: no hay credenciales, no hay plantillas
    // aprobadas, o las que hay no tienen botón de Flow.
    $('#flow-alerta', caja).innerHTML = (salida.avisos || [])
      .map((a) => alerta(a, 'warn')).join('')
      || alerta('La cuenta de WhatsApp no tiene plantillas con Flow.', 'warn');
    return;
  }

  const actual = JSON.stringify([encuesta.flow_plantilla, encuesta.flow_idioma]);
  select.disabled = false;
  select.innerHTML = '<option value="">— elegir —</option>'
    + plantillas.map((p) => {
      const valor = JSON.stringify([p.nombre, p.idioma]);
      return `<option value="${esc(valor)}" ${valor === actual ? 'selected' : ''}>`
        + `${esc(p.nombre)} · ${esc(p.idioma)}</option>`;
    }).join('');

  const pintarDetalle = () => {
    const elegida = plantillas.find((p) =>
      JSON.stringify([p.nombre, p.idioma]) === select.value);
    $('#plantilla-detalle', caja).innerHTML = elegida ? `
      <dl class="kv">
        <dt>Idioma</dt><dd>${esc(elegida.idioma)}</dd>
        <dt>Flow</dt><dd class="mono">${esc(elegida.flow_id)}</dd>
        <dt>Botón</dt><dd>${esc(elegida.texto_boton || '—')}</dd>
        ${elegida.cuerpo
          ? `<dt>Mensaje</dt><dd class="small">${esc(elegida.cuerpo)}</dd>` : ''}
      </dl>` : '';
  };
  select.onchange = pintarDetalle;
  pintarDetalle();
}

async function abrirEnvioWhatsapp(encuesta) {
  const destinatarios = await api.flow.destinatarios(encuesta.id);
  const caja = modal({
    titulo: 'Enviar por WhatsApp',
    ancho: '620px',
    cuerpo: `
      <div id="envio-alerta"></div>
      <div class="aviso destacado">
        <h4>Se envía a quienes cumplen los dos ejes</h4>
        <p>Consentimiento de contacto vigente <strong>y</strong> preferencia
        de WhatsApp activa, más un celular válido. Falta cualquiera de los
        tres y esa persona queda fuera.</p>
        <p>Reintentar <strong>no reenvía</strong> a quien ya recibió: un
        mensaje duplicado quema el canal y la paciencia.</p>
      </div>
      ${pintarDestinatarios(destinatarios)}`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: `Enviar a ${destinatarios.destinatarios.length}`,
        clase: 'btn-orange', onClick: async (c) => {
          const enCurso = bloquearModal(c);
          try {
            const salida = await api.flow.enviar(encuesta.id);
            enCurso.soltar();
            cerrarModal();
            mostrarResultado('Envío terminado', [
              ['Enviados', salida.enviados],
              ['Fallidos', salida.fallidos, true],
              ['Quedaron fuera', salida.excluidos_total, true],
            ], salida.fallidos
              ? `${salida.fallidos} envío(s) fallaron. Se pueden reintentar: `
                + 'el reintento no le vuelve a mandar a quien ya recibió.'
              : '');
            await cargarEstadoFlow(encuesta.id);
          } catch (error) {
            enCurso.soltar();
            $('#envio-alerta', c).innerHTML = alerta(error.message);
          }
        } },
    ],
  });
  return caja;
}


async function verificarCruce(encuestaId) {
  const contenedor = $('#cruce');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('12vh');
  try {
    const cruce = await api.encuestas.cruce(encuestaId);
    contenedor.innerHTML = `
      <div class="resultado">
        <div class="r"><div class="r-num">${cruce.boveda.convocados}</div>
          <div class="r-label">Convocados (bóveda)</div></div>
        <div class="r"><div class="r-num">${cruce.semantica.individuos}</div>
          <div class="r-label">Individuos (semántico)</div></div>
        <div class="r"><div class="r-num">${cruce.semantica.preguntas}</div>
          <div class="r-label">Preguntas</div></div>
        <div class="r"><div class="r-num">${cruce.semantica.respuestas}</div>
          <div class="r-label">Respuestas</div></div>
      </div>
      <div class="alert ${cruce.cruce_ok ? 'alert-success' : 'alert-error'}">
        ${cruce.cruce_ok
          ? 'Los dos stores comparten el mismo ref_estudio y las personas del store semántico salen de lo convocado acá.'
          : 'El store semántico tiene individuos que no fueron convocados en esta encuesta. Revisar.'}
      </div>`;
  } catch (error) {
    contenedor.innerHTML = alerta(
      error.status === 404
        ? 'Esta encuesta todavía no tiene nada del lado semántico: falta ingestar.'
        : error.message,
      error.status === 404 ? 'info' : 'error');
  }
}
