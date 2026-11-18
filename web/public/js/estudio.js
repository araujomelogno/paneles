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
               placeholder="Buscar por título" />
        <button type="button" class="btn btn-outline btn-sm" data-alchemer-ir>Buscar</button>
      </div>
      <select class="fselect hidden" data-alchemer-encuesta style="margin-top:.4rem"></select>
      <select class="fselect hidden" data-alchemer-enlace style="margin-top:.4rem"></select>
      <div data-alchemer-aviso></div>
      <input type="hidden" name="alchemer_id" value="${esc(valores.alchemer_id || '')}" />
      <div class="field-hint">Elegila de la lista: se completan el nombre y la URL, y el
        sistema verifica que guarde el parámetro del panelista.${valores.alchemer_id
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
        descripcion: campo('descripcion').value.trim() || null,
      };
    },
    campos: () => $$('[data-campo-estudio]', contenedor).map((c) => c.dataset.campoEstudio),
  };
}

/* R-ALC.2/3 (D97) — buscar la encuesta en Alchemer en lugar de pegar la URL.
   Elegida, se completan el nombre (editable) y la URL —el enlace más el
   parámetro del panelista—; si tiene más de un enlace, se elige cuál; y se
   avisa si no guarda el parámetro (la encuesta se guarda igual). Sin
   credenciales de Alchemer, la URL se sigue escribiendo a mano. */
function activarAlchemer(contenedor, campo) {
  const buscar$ = $('[data-alchemer-buscar]', contenedor);
  const encuesta$ = $('[data-alchemer-encuesta]', contenedor);
  const enlace$ = $('[data-alchemer-enlace]', contenedor);
  const aviso$ = $('[data-alchemer-aviso]', contenedor);
  if (!buscar$) return;
  let enlaces = [];
  const usarEnlace = () => {
    const elegido = enlaces.find((e) => e.id === enlace$.value) || enlaces[0];
    if (elegido?.url_con_parametro) campo('url_alchemer').value = elegido.url_con_parametro;
  };
  const elegir = async () => {
    const id = encuesta$.value;
    if (!id) return;
    aviso$.innerHTML = '<p class="small muted">Consultando Alchemer…</p>';
    try {
      const d = await api.alchemer.ver(id);
      campo('alchemer_id').value = id;
      if (campo('nombre')) campo('nombre').value = d.titulo || campo('nombre').value;
      enlaces = d.enlaces || [];
      enlace$.innerHTML = enlaces.map((e) => `<option value="${esc(e.id)}">${esc(e.nombre)} · ${
        esc(e.url)}</option>`).join('');
      enlace$.classList.toggle('hidden', enlaces.length < 2);
      usarEnlace();
      aviso$.innerHTML = d.aviso ? alerta(d.aviso, 'warn')
        : `<p class="small" data-alchemer-ok>Guarda el parámetro <code>${esc(d.parametro)}</code>
            · ${d.preguntas} pregunta(s).</p>`;
      if (!enlaces.length) {
        aviso$.innerHTML += alerta('La encuesta no tiene enlaces publicados: completá la URL a mano.', 'warn');
      }
    } catch (error) {
      aviso$.innerHTML = alerta(error.message, 'warn');
    }
  };
  const ir = async () => {
    aviso$.innerHTML = '<p class="small muted">Buscando en Alchemer…</p>';
    try {
      const d = await api.alchemer.buscar(buscar$.value.trim());
      if (!d.configurado) {
        aviso$.innerHTML = alerta((d.avisos || []).join(' '), 'info');
        return;
      }
      encuesta$.innerHTML = '<option value="">— elegí una encuesta —</option>'
        + d.items.map((s) => `<option value="${esc(s.id)}">${esc(s.titulo)}${
          s.estado ? ` · ${esc(s.estado)}` : ''}</option>`).join('');
      encuesta$.classList.remove('hidden');
      aviso$.innerHTML = d.items.length ? ''
        : '<p class="small muted">Ninguna encuesta de Alchemer coincide.</p>';
    } catch (error) {
      aviso$.innerHTML = alerta(error.message, 'warn');
    }
  };
  $('[data-alchemer-ir]', contenedor).onclick = ir;
  buscar$.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); ir(); } };
  encuesta$.onchange = elegir;
  enlace$.onchange = usarEnlace;
}
