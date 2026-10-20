/* R-TEM — El editor de temáticas incorporadas de una carga.

   Se usa en dos lugares con el mismo comportamiento: el paso de revisión de
   una carga nueva (la propuesta de Claude aparece ahí) y el detalle de una
   carga ya confirmada (sección Cargas). Las temáticas se muestran como
   etiquetas: se aceptan las propuestas, se quitan con ×, y se agregan a
   mano reutilizando las existentes con autocompletado.

   Lo que **no** decide esta pantalla: si una temática es nueva o existente,
   ni su nombre definitivo. El servidor normaliza al guardar (mayúsculas,
   espacios, tildes de menos contra lo que ya existe) y devuelve la lista
   como quedó. `comparable` existe solo para no mostrar dos veces la misma
   etiqueta mientras se edita; el que manda es `tematicas.normalizar`. */

import { $, esc } from './ui.js';

const comparable = (texto) => String(texto || '')
  .normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/\s+/g, ' ').trim().toLowerCase();

let secuencia = 0;

/* `contenedor` recibe el editor. `vocabulario` es `GET /tematicas`;
   `iniciales`, las que ya tiene (`{nombre, origen}`); `propuesta`, lo que
   devolvió Claude (`{nombre, es_nueva, preguntas_relacionadas}`), que entra
   marcado como propuesto y con su procedencia a la vista.

   Devuelve `{ valor() }`: la lista para `PUT /cargas/<id>/tematicas`. */
export function editorDeTematicas(contenedor, {
  vocabulario = [], iniciales = [], propuesta = [], aviso = null, alCambiar,
} = {}) {
  const id = `tem-${++secuencia}`;
  const existentes = new Map(vocabulario.map((t) => [comparable(t.nombre), t.nombre]));
  // nombre comparable → { nombre, origen, nueva, relacionadas }
  const elegidas = new Map();

  const agregar = (nombre, origen, extra = {}) => {
    const clave = comparable(nombre);
    if (!clave || clave.length < 2 || elegidas.has(clave)) return false;
    const existente = existentes.get(clave);
    elegidas.set(clave, {
      nombre: existente || String(nombre).replace(/\s+/g, ' ').trim().toLowerCase(),
      origen,
      nueva: extra.nueva ?? !existente,
      relacionadas: extra.relacionadas || [],
    });
    return true;
  };

  iniciales.forEach((t) => agregar(t.nombre, t.origen || 'manual', { nueva: false }));
  propuesta.forEach((t) => agregar(t.nombre, 'propuesta_ia', {
    nueva: t.es_nueva, relacionadas: t.preguntas_relacionadas,
  }));

  contenedor.innerHTML = `
    <div class="tematicas-editor">
      ${aviso ? `<div class="alert alert-warn">${esc(aviso)}</div>` : ''}
      <div class="tematicas-chips" data-chips></div>
      <div class="tematicas-agregar">
        <input type="text" list="${id}-lista" data-nueva maxlength="80"
               placeholder="Agregar una temática (p. ej. «telefonía móvil»)" />
        <button class="btn btn-outline btn-sm" type="button" data-agregar>Agregar</button>
      </div>
      <datalist id="${id}-lista">${vocabulario.map((t) =>
        `<option value="${esc(t.nombre)}"></option>`).join('')}</datalist>
      <div class="field-hint">Frases cortas, de dos a cuatro palabras, al nivel
        del tema de investigación («servicios de streaming», no «Netflix»).
        Las que ya existen se reutilizan; las <span class="chip chip-nueva">nueva</span>
        se crean al guardar.</div>
    </div>`;

  const chips$ = $('[data-chips]', contenedor);
  const entrada$ = $('[data-nueva]', contenedor);

  function pintar() {
    chips$.innerHTML = elegidas.size ? [...elegidas.entries()].map(([clave, t]) => `
      <span class="chip chip-tematica ${t.origen === 'propuesta_ia' ? 'chip-ia' : ''}"
            title="${esc(t.relacionadas.length
              ? `Sale de: ${t.relacionadas.map((p) => `${p.codigo} — ${p.texto}`).join(' · ')}`
              : t.origen === 'propuesta_ia' ? 'Propuesta por Claude' : 'Agregada a mano')}">
        ${esc(t.nombre)}
        ${t.nueva ? '<span class="chip-marca">nueva</span>' : ''}
        ${t.origen === 'propuesta_ia' ? '<span class="chip-marca ia">IA</span>' : ''}
        <button type="button" data-quitar="${esc(clave)}" aria-label="Quitar">×</button>
      </span>`).join('')
      : '<span class="small muted">Sin temáticas. Agregalas a mano.</span>';
    chips$.querySelectorAll('[data-quitar]').forEach((boton) => {
      boton.onclick = () => {
        elegidas.delete(boton.dataset.quitar);
        pintar();
        alCambiar?.();
      };
    });
    const conProcedencia = [...elegidas.values()].filter((t) => t.relacionadas.length);
    const detalle$ = contenedor.querySelector('[data-procedencia]');
    if (detalle$) detalle$.remove();
    if (conProcedencia.length) {
      chips$.insertAdjacentHTML('afterend', `
        <details class="small" data-procedencia style="margin-top:0.4rem">
          <summary>De qué preguntas sale cada temática propuesta</summary>
          <ul style="margin:0.3rem 0 0 1rem">${conProcedencia.map((t) => `
            <li><strong>${esc(t.nombre)}</strong>: ${t.relacionadas.map((p) =>
              `<code>${esc(p.codigo)}</code> ${esc(p.texto)}`).join(' · ')}</li>`).join('')}
          </ul>
        </details>`);
    }
  }

  const agregarDeLaEntrada = () => {
    if (agregar(entrada$.value, 'manual')) {
      entrada$.value = '';
      pintar();
      alCambiar?.();
    }
  };
  $('[data-agregar]', contenedor).onclick = agregarDeLaEntrada;
  entrada$.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); agregarDeLaEntrada(); }
  });
  pintar();

  return {
    valor: () => [...elegidas.values()].map((t) => ({ nombre: t.nombre, origen: t.origen })),
  };
}

/* Las etiquetas, solo para mirar: el listado de cargas. */
export function chipsDeTematicas(lista = []) {
  if (!lista.length) return '<span class="small muted">—</span>';
  return `<span class="tematicas-chips">${lista.map((t) =>
    `<span class="chip chip-tematica">${esc(t.nombre)}</span>`).join('')}</span>`;
}
