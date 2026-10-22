/* R-CLI — Los clientes de una carga: para quién se hizo el estudio.

   Se usa en dos lugares con el mismo comportamiento: el alta de una carga
   y el detalle de una carga ya creada (sección Cargas). Son etiquetas, como
   las temáticas: se agregan escribiendo (con autocompletado sobre los que ya
   existen) y se quitan con ×. Varios por carga.

   Lo que **no** decide esta pantalla: si un cliente es nuevo ni cómo se
   escribe. El servidor normaliza al guardar («Antel» = «ANTEL» = «antel»,
   y una variante que difiere solo en tildes reutiliza la existente) y
   devuelve la lista como quedó. `comparable` existe solo para no mostrar dos
   veces la misma etiqueta mientras se edita.

   Confidencial (R-CLI.4): nada de esto llega al portal del panelista. */

import { $, $$, esc } from './ui.js';
import * as api from './api.js';

const comparable = (texto) => String(texto || '')
  .normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/\s+/g, ' ').trim().toLowerCase();

let secuencia = 0;

/* `contenedor` recibe el editor; `iniciales`, los que ya tiene la carga
   (`[{id, nombre}]` o nombres). Devuelve `{ valor() }`: la lista de nombres
   para `POST /cargas` o `PATCH /cargas/<id>`. */
export function editorDeClientes(contenedor, { iniciales = [], alCambiar } = {}) {
  const id = `cli-${++secuencia}`;
  const elegidos = new Map();
  const agregar = (nombre) => {
    const limpio = String(nombre || '').replace(/\s+/g, ' ').trim();
    const clave = comparable(limpio);
    if (!clave || elegidos.has(clave)) return false;
    elegidos.set(clave, limpio);
    return true;
  };
  iniciales.forEach((c) => agregar(typeof c === 'string' ? c : c.nombre));

  contenedor.innerHTML = `
    <div class="editor-clientes">
      <div class="tematicas-chips" data-chips></div>
      <div class="toolbar" style="margin-top:.4rem">
        <input type="text" class="finput" data-nuevo list="${id}-lista"
               placeholder="Escribí un cliente y Enter (ej.: Antel)" maxlength="120"
               style="max-width:320px" />
        <datalist id="${id}-lista"></datalist>
        <button type="button" class="btn btn-outline btn-sm" data-agregar>+ Agregar</button>
      </div>
    </div>`;
  const chips$ = $('[data-chips]', contenedor);
  const nuevo$ = $('[data-nuevo]', contenedor);
  const lista$ = $(`#${id}-lista`, contenedor);

  const pintar = () => {
    chips$.innerHTML = elegidos.size
      ? [...elegidos.entries()].map(([clave, nombre]) => `
        <span class="chip chip-cliente">${esc(nombre)}
          <button type="button" data-quitar="${esc(clave)}" aria-label="Quitar">×</button>
        </span>`).join('')
      : '<span class="small muted">Sin clientes.</span>';
    $$('[data-quitar]', chips$).forEach((boton) => {
      boton.onclick = () => { elegidos.delete(boton.dataset.quitar); pintar(); alCambiar?.(); };
    });
  };
  const sumar = () => {
    if (agregar(nuevo$.value)) { pintar(); alCambiar?.(); }
    nuevo$.value = '';
  };
  let pedido = 0;
  nuevo$.oninput = async () => {
    const yo = ++pedido;
    try {
      const { items } = await api.clientes.listar(nuevo$.value.trim() || undefined);
      if (yo !== pedido) return;
      lista$.innerHTML = items.map((c) =>
        `<option value="${esc(c.nombre)}">${c.cargas} carga(s)</option>`).join('');
    } catch { /* el autocompletado es una ayuda: sin él se escribe igual */ }
  };
  nuevo$.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); sumar(); }
  };
  $('[data-agregar]', contenedor).onclick = sumar;
  pintar();
  nuevo$.oninput();

  return {
    // Lo que quedó escrito sin Enter también cuenta: es lo que se ve.
    valor: () => {
      if (nuevo$.value.trim()) sumar();
      return [...elegidos.values()];
    },
  };
}

/* Las etiquetas, solo para mirar: el listado y el detalle de cargas. */
export function chipsDeClientes(lista = []) {
  if (!lista.length) return '<span class="small muted">—</span>';
  return `<span class="tematicas-chips">${lista.map((c) =>
    `<span class="chip chip-cliente">${esc(c.nombre ?? c)}</span>`).join('')}</span>`;
}
