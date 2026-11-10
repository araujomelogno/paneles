/* R-AU.3 (D90) — los datos del estudio: **un solo formulario**.

   La carga (sección Panelistas), «Nueva encuesta» (sección Encuestas), la
   encuesta creada desde una consulta y «Datos del estudio» piden lo mismo:
   nombre, fecha de campo, público objetivo, clientes, URL de Alchemer y
   descripción. Es el mismo componente, no uno parecido: si se suma un dato
   al estudio, aparece en todos lados. Cada campo lleva `data-campo-estudio`,
   y una prueba compara los de cada pantalla. */

import { editorDeClientes } from './clientes.js';
import { $, $$, esc } from './ui.js';

export const CAMPOS_DEL_ESTUDIO = [
  'nombre', 'fecha_campo', 'publico_objetivo', 'clientes', 'url_alchemer', 'descripcion',
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
      <div class="form-group" data-campo-estudio="fecha_campo"><label>Fecha de campo</label>
        <input type="date" name="fecha_campo" value="${esc(valores.fecha_campo || '')}" />
        <div class="field-hint">Cuándo se hizo el campo, no cuándo se carga.</div></div>
      <div class="form-group" data-campo-estudio="publico_objetivo"><label>Público objetivo</label>
        <input type="text" name="publico_objetivo" value="${esc(valores.publico_objetivo || '')}"
               placeholder="Población adulta de Montevideo" /></div>
    </div>
    <div class="form-group" data-campo-estudio="clientes"><label>Clientes</label>
      <div data-clientes-estudio></div>
      <div class="field-hint">Para quién es el estudio. Confidencial: el panelista no lo ve.</div></div>
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
  return {
    valor() {
      return {
        ...(campo('nombre') ? { nombre: campo('nombre').value.trim() } : {}),
        fecha_campo: campo('fecha_campo').value || null,
        publico_objetivo: campo('publico_objetivo').value.trim() || null,
        clientes: clientes.valor(),
        url_alchemer: campo('url_alchemer').value.trim() || null,
        descripcion: campo('descripcion').value.trim() || null,
      };
    },
    campos: () => $$('[data-campo-estudio]', contenedor).map((c) => c.dataset.campoEstudio),
  };
}
