/* R-AU.3 (D90) — los datos del estudio: **un solo formulario**.

   La carga (sección Panelistas), «Nueva encuesta» (sección Encuestas), la
   encuesta creada desde una consulta y «Datos del estudio» piden lo mismo:
   nombre, fecha de campo, público objetivo, clientes, URL de Alchemer y
   descripción. Es el mismo componente, no uno parecido: si se suma un dato
   al estudio, aparece en todos lados. Cada campo lleva `data-campo-estudio`,
   y una prueba compara los de cada pantalla. */

import * as api from './api.js';
import { editorDeClientes } from './clientes.js';
import { $, $$, esc, alerta } from './ui.js';

export const CAMPOS_DEL_ESTUDIO = [
  'nombre', 'fecha_campo', 'publico_objetivo', 'clientes', 'alchemer', 'url_alchemer',
  'descripcion',
];

/* El HTML. `valores` son los de un estudio que ya existe (para editar).
   `conNombre: false` lo omite cuando el nombre lo pone otro campo (la carga
   tiene el suyo y su encuesta lo toma de ahí). */
export function campos(valores = {}, {
  conNombre = true, etiquetaNombre = 'Nombre del estudio',
  ejemploNombre = 'Ola 3 — Consumo de bebidas',
} = {}) {
  return `<div class="campos-estudio" data-campos-estudio>
    ${conNombre ? `<div class="form-group" data-campo-estudio="nombre">
      <label>${esc(etiquetaNombre)}</label>
      <input type="text" name="nombre" value="${esc(valores.nombre || '')}"
             placeholder="${esc(ejemploNombre)}" /></div>` : ''}
    <div class="form-row">
      <div class="form-group" data-campo-estudio="fecha_campo"><label>Fecha de campo
        <span class="obligatorio" title="Obligatoria">*</span></label>
        <input type="date" name="fecha_campo" required
               value="${esc(valores.fecha_campo || '')}" />
        <div class="field-hint">Cuándo se hizo el campo, no cuándo se carga. Es
          obligatoria: la consulta por historia de participación la usa.</div></div>
      <div class="form-group" data-campo-estudio="publico_objetivo"><label>Público objetivo</label>
        <input type="text" name="publico_objetivo" value="${esc(valores.publico_objetivo || '')}"
               placeholder="Población adulta de Montevideo" /></div>
    </div>
    <div class="form-group" data-campo-estudio="clientes"><label>Clientes</label>
      <div data-clientes-estudio></div>
      <div class="field-hint">Para quién es el estudio. Confidencial: el panelista no lo ve.</div></div>
    <div class="form-group" data-campo-estudio="alchemer"><label>Encuesta de Alchemer</label>
      <div class="toolbar">
        <input type="search" class="finput" data-alchemer-buscar style="flex:1 1 auto"
               placeholder="Buscar por título, o pegar la URL o el id de la encuesta" />
        <button type="button" class="btn btn-outline btn-sm" data-alchemer-ir>Buscar</button>
      </div>
      <div class="small muted" data-alchemer-catalogo style="margin-top:.3rem"></div>
      <label class="check small hidden" data-alchemer-todas-caja style="margin-top:.3rem">
        <input type="checkbox" data-alchemer-todas /> Incluir las cerradas</label>
      <select class="fselect hidden" data-alchemer-encuesta style="margin-top:.4rem"></select>
      <div class="hidden" data-alchemer-enlaces style="margin-top:.5rem">
        <label class="small">Enlace</label>
        <select class="fselect" data-alchemer-enlace></select>
        <div class="small" data-alchemer-enlace-detalle style="margin-top:.2rem"></div>
        <label class="check small"><input type="checkbox" data-alchemer-cerrados />
          Mostrar también los enlaces cerrados</label>
      </div>
      <div data-alchemer-aviso></div>
      <input type="hidden" name="alchemer_id" value="${esc(valores.alchemer_id || '')}" />
      <input type="hidden" name="alchemer_enlace_id" value="${esc(valores.alchemer_enlace_id || '')}" />
      <div class="field-hint">Buscala por título, o pegá la URL del enlace o el id: se
        completan el nombre y la URL, y el sistema verifica que guarde el parámetro del
        panelista.${valores.alchemer_id
          ? ` Vinculada a la encuesta <code>${esc(valores.alchemer_id)}</code>.` : ''}</div></div>
    <div class="form-group" data-campo-estudio="url_alchemer"><label>URL de la encuesta en Alchemer</label>
      <input type="url" name="url_alchemer" value="${esc(valores.url_alchemer || '')}"
             placeholder="https://survey.alchemer.com/s3/…?pid=" />
      <div class="field-hint">Terminada en el parámetro que recibe al panelista
        (<code>…?pid=</code>): a cada uno se le agrega su id_persona.</div></div>
    <div class="form-group" data-campo-estudio="descripcion"><label>Descripción (opcional)</label>
      <textarea class="finput" name="descripcion">${esc(valores.descripcion || '')}</textarea></div>
  </div>`;
}

/* Engancha el editor de clientes. Devuelve `{ valor }`: los datos del
   estudio como los recibe el servidor. */
export function activar(caja, valores = {}) {
  const contenedor = $('[data-campos-estudio]', caja);
  const clientes = editorDeClientes($('[data-clientes-estudio]', contenedor),
                                    { iniciales: valores.clientes || [] });
  const campo = (nombre) => $(`[name="${nombre}"]`, contenedor);
  activarAlchemer(contenedor, campo);
  return {
    valor() {
      return {
        ...(campo('nombre') ? { nombre: campo('nombre').value.trim() } : {}),
        fecha_campo: campo('fecha_campo').value || null,
        publico_objetivo: campo('publico_objetivo').value.trim() || null,
        clientes: clientes.valor(),
        url_alchemer: campo('url_alchemer').value.trim() || null,
        // R-ALC.2 — la encuesta de Alchemer elegida (solo si se eligió).
        ...(campo('alchemer_id').value ? { alchemer_id: campo('alchemer_id').value } : {}),
        // R-ALB.5 (D99) — qué enlace dio la URL.
        ...(campo('alchemer_enlace_id').value
          ? { alchemer_enlace_id: campo('alchemer_enlace_id').value } : {}),
        descripcion: campo('descripcion').value.trim() || null,
      };
    },
    campos: () => $$('[data-campo-estudio]', contenedor).map((c) => c.dataset.campoEstudio),
  };
}

/* R-ALC.2/3 (D97) y D99 — la encuesta de Alchemer, sin pegar la URL a mano.

   - Lo que se escribe se **busca en el catálogo** (R-ALB.2), al instante y
     sin llamar a Alchemer; si es una URL o un id, se **resuelve esa sola
     encuesta** (R-ALB.1, una llamada).
   - Elegida, se completa el nombre y se listan sus **enlaces**, siempre a la
     vista aunque sea uno, con nombre, estado y URL (R-ALB.5). La URL del
     estudio es la del enlace elegido, con el parámetro.
   - Todo pedido a Alchemer tiene tiempo máximo (R-ALB.3): vencido, se dice
     y se ofrece reintentar o pegar la URL. Nunca queda girando.
   - «Actualizar» lanza la sincronización del catálogo, que corre en segundo
     plano: se muestra su avance. */
const PARECE_URL_O_ID = /^\s*\d{4,}\s*$|\/|alchemer|\.com/i;

function hace(iso) {
  if (!iso) return '';
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 2) return 'recién';
  if (min < 60) return `hace ${min} min`;
  const horas = Math.round(min / 60);
  return horas < 48 ? `hace ${horas} h` : `hace ${Math.round(horas / 24)} días`;
}

function activarAlchemer(contenedor, campo) {
  const buscar$ = $('[data-alchemer-buscar]', contenedor);
  if (!buscar$) return;
  const encuesta$ = $('[data-alchemer-encuesta]', contenedor);
  const enlaces$ = $('[data-alchemer-enlaces]', contenedor);
  const enlace$ = $('[data-alchemer-enlace]', contenedor);
  const detalle$ = $('[data-alchemer-enlace-detalle]', contenedor);
  const cerrados$ = $('[data-alchemer-cerrados]', contenedor);
  const todas$ = $('[data-alchemer-todas]', contenedor);
  const aviso$ = $('[data-alchemer-aviso]', contenedor);
  const catalogo$ = $('[data-alchemer-catalogo]', contenedor);
  let enlaces = [];
  let ultimoPedido = null;
  let sondeo = null;

  /* R-ALB.3 — un error con sus opciones: reintentar lo último o pegar la URL. */
  const mostrarError = (error, reintentar) => {
    aviso$.innerHTML = `<div class="alert alert-warn" data-alchemer-error>
      ${esc(error?.message || 'No se pudo hablar con Alchemer.')}
      <div class="toolbar" style="margin-top:.4rem">
        <button type="button" class="btn btn-outline btn-sm" data-alchemer-reintentar>Reintentar</button>
        <button type="button" class="btn btn-outline btn-sm" data-alchemer-pegar
          >Pegar la URL</button></div></div>`;
    $('[data-alchemer-reintentar]', aviso$).onclick = () => reintentar?.();
    $('[data-alchemer-pegar]', aviso$).onclick = () => {
      aviso$.innerHTML = '<p class="small muted">Pegá la URL del enlace o el id de la '
        + 'encuesta en el buscador y apretá «Buscar».</p>';
      buscar$.value = '';
      buscar$.focus();
    };
  };

  const pintarEnlaces = () => {
    const visibles = enlaces.filter((e) => e.activo || cerrados$.checked
      || e.id === campo('alchemer_enlace_id').value);
    enlace$.innerHTML = visibles.map((e) => `<option value="${esc(e.id)}">${esc(e.nombre)} · ${
      esc(e.estado || '—')} · ${esc(e.url)}</option>`).join('');
    const elegido = visibles.find((e) => e.id === campo('alchemer_enlace_id').value) || visibles[0];
    if (elegido) enlace$.value = elegido.id;
    usarEnlace();
  };
  const usarEnlace = () => {
    const elegido = enlaces.find((e) => e.id === enlace$.value);
    if (!elegido) {
      detalle$.innerHTML = enlaces.length ? '' : '<span class="muted">La encuesta no tiene '
        + 'enlaces publicados: completá la URL a mano.</span>';
      return;
    }
    campo('alchemer_enlace_id').value = elegido.id;
    campo('url_alchemer').value = elegido.url_con_parametro;
    detalle$.innerHTML = `<span data-alchemer-url>${esc(elegido.url_con_parametro)}</span>${
      elegido.seguro ? '' : ' <span class="muted">· sin https: paneles no la acepta</span>'}`;
  };

  const traerEnlaces = async (id) => {
    detalle$.innerHTML = '<span class="muted">Trayendo los enlaces…</span>';
    enlaces$.classList.remove('hidden');
    try {
      enlaces = (await api.alchemer.enlaces(id)).items || [];
      pintarEnlaces();
    } catch (error) {
      enlaces = [];
      enlace$.innerHTML = '';
      detalle$.innerHTML = '';
      mostrarError(error, () => traerEnlaces(id));
    }
  };

  /* R-ALB.1 — una encuesta, por lo que se pegó o lo que se eligió. */
  const resolver = async (texto) => {
    ultimoPedido = () => resolver(texto);
    aviso$.innerHTML = '<p class="small muted" data-alchemer-consultando>Consultando Alchemer…</p>';
    let d;
    try {
      d = await api.alchemer.resolver(texto);
    } catch (error) {
      mostrarError(error, ultimoPedido);
      return;
    }
    if (campo('alchemer_id').value !== d.id) campo('alchemer_enlace_id').value = '';
    campo('alchemer_id').value = d.id;
    if (campo('nombre')) campo('nombre').value = d.titulo || campo('nombre').value;
    aviso$.innerHTML = d.aviso ? alerta(d.aviso, 'warn')
      : `<p class="small" data-alchemer-ok>«${esc(d.titulo)}» · guarda el parámetro
          <code>${esc(d.parametro)}</code> · ${d.preguntas} pregunta(s).</p>`;
    await traerEnlaces(d.id);
  };

  const pintarCatalogo = (s) => {
    const ultima = s?.ultima;
    const terminada = s?.ultima_terminada;
    const enCurso = ultima?.estado === 'en_curso';
    catalogo$.innerHTML = `${enCurso
      ? `<span data-alchemer-progreso>Actualizando el catálogo… ${ultima.paginas_procesadas}${
        ultima.paginas_total ? ` de ${ultima.paginas_total}` : ''} página(s)${
        ultima.estancada ? ' · parece detenida: «Actualizar» la retoma' : ''}</span>`
      : terminada ? `Catálogo: ${s.en_catalogo} encuestas · actualizado ${esc(hace(terminada.terminada_en))}`
        : 'El catálogo todavía no se sincronizó.'}${
      ultima?.estado === 'fallida' ? ' · la última actualización falló en alguna página' : ''}
      · <a href="#" data-alchemer-actualizar>Actualizar</a>`;
    $('[data-alchemer-actualizar]', catalogo$).onclick = async (e) => {
      e.preventDefault();
      try {
        pintarCatalogo({ ...s, ultima: await api.alchemer.sincronizar() });
        seguir();
      } catch (error) {
        aviso$.innerHTML = alerta(error.message, 'warn');
      }
    };
    return enCurso;
  };
  /* El avance se consulta mientras el formulario esté abierto. */
  const seguir = () => {
    clearTimeout(sondeo);
    sondeo = setTimeout(async () => {
      if (!contenedor.isConnected) return;
      try {
        const s = await api.alchemer.sincronizacion();
        if (pintarCatalogo(s)) seguir();
        else if (buscar$.value.trim() && !PARECE_URL_O_ID.test(buscar$.value)) ir();
      } catch { /* se reintenta con el próximo «Actualizar» */ }
    }, 2000);
  };

  /* R-ALB.2 — buscar en el catálogo: no llama a Alchemer. */
  const ir = async () => {
    const texto = buscar$.value.trim();
    if (texto && PARECE_URL_O_ID.test(texto)) {
      encuesta$.classList.add('hidden');
      await resolver(texto);
      return;
    }
    ultimoPedido = ir;
    try {
      const d = await api.alchemer.buscar(texto, todas$.checked);
      $('[data-alchemer-todas-caja]', contenedor).classList.toggle('hidden', !d.configurado);
      if (!d.configurado) {
        aviso$.innerHTML = alerta((d.avisos || []).join(' '), 'info');
        return;
      }
      if (pintarCatalogo(d.sincronizacion)) seguir();
      encuesta$.innerHTML = '<option value="">— elegí una encuesta —</option>'
        + d.items.map((s) => `<option value="${esc(s.id)}">${esc(s.titulo)}${
          s.estado ? ` · ${esc(s.estado)}` : ''} · ${esc(s.id)}</option>`).join('');
      encuesta$.classList.toggle('hidden', !d.items.length);
      aviso$.innerHTML = d.items.length
        ? (d.hay_mas ? '<p class="small muted">Hay más: escribí más del título.</p>' : '')
        : (d.avisos || []).length ? alerta(d.avisos.join(' '), 'info')
          : '<p class="small muted">Ninguna encuesta del catálogo coincide. Si es nueva, '
            + 'pegá su URL o su id, o actualizá el catálogo.</p>';
    } catch (error) {
      mostrarError(error, ir);
    }
  };

  $('[data-alchemer-ir]', contenedor).onclick = ir;
  buscar$.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); ir(); } };
  todas$.onchange = ir;
  encuesta$.onchange = () => { if (encuesta$.value) resolver(encuesta$.value); };
  enlace$.onchange = usarEnlace;
  cerrados$.onchange = pintarEnlaces;
}
