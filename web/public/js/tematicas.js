/* R-TEM — El editor de temáticas incorporadas de una carga.

   Se usa en dos lugares con el mismo comportamiento: el paso de revisión de
   una carga nueva (la propuesta de Claude aparece ahí) y el detalle de una
   carga ya confirmada (sección Cargas). Las temáticas se muestran como
   etiquetas: se aceptan las propuestas, se quitan con ×, y se agregan a
   mano reutilizando las existentes con autocompletado.

   R-TEM.6 — debajo de las etiquetas, cada temática dice **qué preguntas la
   sustentan** y si es de **categoría especial**. Las preguntas no son un
   detalle: deciden qué panelistas la tienen (quien respondió al menos una).
   Una temática sin preguntas describe la carga y ningún panelista la tiene.

   Lo que **no** decide esta pantalla: si una temática es nueva o existente,
   ni su nombre definitivo. El servidor normaliza al guardar (mayúsculas,
   espacios, tildes de menos contra lo que ya existe) y devuelve la lista
   como quedó. `comparable` existe solo para no mostrar dos veces la misma
   etiqueta mientras se edita; el que manda es `tematicas.normalizar`. */

import { $, $$, esc } from './ui.js';

const comparable = (texto) => String(texto || '')
  .normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/\s+/g, ' ').trim().toLowerCase();

let secuencia = 0;

/* `contenedor` recibe el editor. `vocabulario` es `GET /tematicas`;
   `iniciales`, las que ya tiene la carga (`{nombre, origen, preguntas,
   es_especial, personas}`); `propuesta`, lo que devolvió Claude (`{nombre,
   es_nueva, preguntas_relacionadas, podria_ser_especial}`).
   `preguntasDisponibles` son las que se pueden asociar (`{codigo, texto}`);
   `puedeDesmarcar`, si el rol puede quitar la marca de especial a una
   temática que ya la tenía (el servidor lo vuelve a controlar). `areas`,
   las de la taxonomía (`{id, nombre, es_especial}`), para ubicar las
   temáticas que todavía no tienen área (R-TEM.9): lo ya clasificado no se
   mueve desde una carga, se mueve en la taxonomía.

   Devuelve `{ valor() }`: la lista para `PUT /cargas/<id>/tematicas`. */
export function editorDeTematicas(contenedor, {
  vocabulario = [], iniciales = [], propuesta = [], aviso = null,
  preguntasDisponibles = [], puedeDesmarcar = false, alCambiar, areas = [],
} = {}) {
  const id = `tem-${++secuencia}`;
  const existentes = new Map(vocabulario.map((t) => [comparable(t.nombre), t]));
  const textoDe = new Map(preguntasDisponibles.map((p) => [p.codigo, p.texto]));
  // nombre comparable → { nombre, origen, nueva, preguntas, especial, ... }
  const elegidas = new Map();

  const agregar = (nombre, origen, extra = {}) => {
    const clave = comparable(nombre);
    if (!clave || clave.length < 2 || elegidas.has(clave)) return false;
    const existente = existentes.get(clave);
    const areaExistente = existente?.area_id
      ? { id: existente.area_id, nombre: existente.area } : null;
    // R-TEM.11 — la marca propia es la que se edita acá; la del área se
    // hereda y se muestra, pero no se guarda en la temática.
    const propia = existente
      ? Boolean(existente.es_especial_propia ?? existente.es_especial) : false;
    const heredada = Boolean(existente?.es_especial) && !propia;
    elegidas.set(clave, {
      nombre: existente?.nombre || String(nombre).replace(/\s+/g, ' ').trim().toLowerCase(),
      origen,
      nueva: extra.nueva ?? !existente,
      preguntas: [...new Set(extra.preguntas || [])],
      especial: Boolean(extra.especialPropia ?? (propia || (!existente && extra.especial))),
      heredada,
      // Una especial que ya existía: quitarle la marca es de quien
      // administra los atributos.
      especialFija: propia && !puedeDesmarcar,
      personas: extra.personas,
      // El área: la que ya tiene (fija) o la propuesta / elegida.
      area: areaExistente || extra.area || null,
      areaFija: Boolean(areaExistente),
    });
    return true;
  };

  iniciales.forEach((t) => agregar(t.nombre, t.origen || 'manual', {
    nueva: Boolean(t.nueva), preguntas: t.preguntas, personas: t.personas,
    especialPropia: t.es_especial_propia ?? (t.es_especial && !t.heredada),
    especial: t.es_especial,
    area: t.area_id || t.area ? { id: t.area_id || null, nombre: t.area } : null,
  }));
  propuesta.forEach((t) => agregar(t.nombre, 'propuesta_ia', {
    nueva: t.es_nueva,
    preguntas: (t.preguntas_relacionadas || []).map((p) => p.codigo || p),
    especial: t.podria_ser_especial,
    area: t.area ? { id: t.area_id || null, nombre: t.area, nueva: t.area_es_nueva } : null,
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
        Las que ya existen se reutilizan; las <b>nueva</b> se crean al guardar.</div>
      <div data-detalle></div>
    </div>`;

  const chips$ = $('[data-chips]', contenedor);
  const detalle$ = $('[data-detalle]', contenedor);
  const entrada$ = $('[data-nueva]', contenedor);

  const cambio = () => { pintar(); alCambiar?.(); };

  function pintar() {
    chips$.innerHTML = elegidas.size ? [...elegidas.entries()].map(([clave, t]) => `
      <span class="chip chip-tematica ${t.origen === 'propuesta_ia' ? 'chip-ia' : ''}"
            title="${esc(t.origen === 'propuesta_ia' ? 'Propuesta por Claude' : 'Agregada a mano')}">
        ${esc(t.nombre)}
        ${t.nueva ? '<span class="chip-marca">nueva</span>' : ''}
        ${t.especial || t.heredada ? '<span class="chip-marca especial">especial</span>' : ''}
        ${t.origen === 'propuesta_ia' ? '<span class="chip-marca ia">IA</span>' : ''}
        <button type="button" data-quitar="${esc(clave)}" aria-label="Quitar">×</button>
      </span>`).join('')
      : '<span class="small muted">Sin temáticas. Agregalas a mano.</span>';

    detalle$.innerHTML = elegidas.size ? `
      <table class="tabla tematicas-detalle">
        <thead><tr><th>Temática</th><th>Área</th><th>Preguntas que la sustentan</th>
          <th title="Salud, sustancias, ideología política, religión, vida sexual">Especial</th>
          ${iniciales.some((t) => t.personas != null) ? '<th class="num">Panelistas</th>' : ''}
        </tr></thead>
        <tbody>${[...elegidas.entries()].map(([clave, t]) => `
          <tr>
            <td><strong>${esc(t.nombre)}</strong></td>
            <td>${celdaDeArea(clave, t)}</td>
            <td>
              <div class="tematicas-chips">${t.preguntas.map((codigo) => `
                <span class="chip" title="${esc(textoDe.get(codigo) || '')}"><code>${esc(codigo)}</code>
                  ${textoDe.get(codigo) ? `<span class="small">${esc(recortar(textoDe.get(codigo)))}</span>` : ''}
                  <button type="button" data-sacar="${esc(clave)}" data-codigo="${esc(codigo)}"
                          aria-label="Quitar pregunta">×</button></span>`).join('')
                || '<span class="small aviso-sin-preguntas">Sin preguntas: describe la carga, pero ningún panelista la va a tener.</span>'}
              </div>
              ${preguntasDisponibles.length ? `
                <select class="fselect fselect-sm" data-sumar="${esc(clave)}">
                  <option value="">+ Pregunta…</option>
                  ${preguntasDisponibles.filter((p) => !t.preguntas.includes(p.codigo)).map((p) =>
                    `<option value="${esc(p.codigo)}">${esc(p.codigo)} — ${esc(recortar(p.texto, 70))}</option>`).join('')}
                </select>` : ''}
            </td>
            <td class="centro">${t.heredada
              ? '<input type="checkbox" checked disabled title="Especial por su área" /><div class="small muted">por su área</div>'
              : `<input type="checkbox" data-especial="${esc(clave)}"
                  ${t.especial ? 'checked' : ''} ${t.especial && t.especialFija ? 'disabled title="Quitar la marca de especial lo hace quien administra los atributos"' : ''} />`}</td>
            ${iniciales.some((x) => x.personas != null) ? `<td class="num">${t.personas === 0
              ? `<span class="sobrante">0 · sin personas</span>
                 <div class="small muted">probablemente sobrante</div>
                 <button type="button" class="btn btn-outline btn-sm" data-quitar-fila="${esc(clave)}">Quitar</button>`
              : (t.personas ?? '—')}</td>` : ''}
          </tr>`).join('')}
        </tbody>
      </table>
      <p class="small muted">Un panelista tiene la temática si respondió al menos una de
        sus preguntas («No sabe» y vacío no cuentan). Marcar una temática como
        <b>especial</b> vale para todas las cargas: no sale como columna ni en
        exportaciones, y filtrar por ella queda restringido por rol.</p>` : '';

    $$('[data-quitar]', chips$).forEach((boton) => {
      boton.onclick = () => { elegidas.delete(boton.dataset.quitar); cambio(); };
    });
    $$('[data-sacar]', detalle$).forEach((boton) => {
      boton.onclick = () => {
        const t = elegidas.get(boton.dataset.sacar);
        t.preguntas = t.preguntas.filter((c) => c !== boton.dataset.codigo);
        cambio();
      };
    });
    $$('[data-sumar]', detalle$).forEach((select) => {
      select.onchange = () => {
        if (!select.value) return;
        elegidas.get(select.dataset.sumar).preguntas.push(select.value);
        cambio();
      };
    });
    $$('[data-especial]', detalle$).forEach((caja) => {
      caja.onchange = () => { elegidas.get(caja.dataset.especial).especial = caja.checked; cambio(); };
    });
    $$('[data-quitar-fila]', detalle$).forEach((boton) => {
      boton.onclick = () => { elegidas.delete(boton.dataset.quitarFila); cambio(); };
    });
    $$('[data-area]', detalle$).forEach((select) => {
      select.onchange = () => {
        const t = elegidas.get(select.dataset.area);
        if (select.value === '__nueva__') {
          const nombre = (window.prompt('Nombre del área nueva (dos a cuatro palabras):') || '').trim();
          t.area = nombre ? { id: null, nombre: nombre.toLowerCase(), nueva: true } : t.area;
        } else if (select.value !== '__propuesta__') {
          const area = areas.find((a) => String(a.id) === select.value);
          t.area = area ? { id: area.id, nombre: area.nombre } : null;
        }
        cambio();
      };
    });
  }

  /* R-TEM.9 — el área: la que ya tiene (fija: se mueve en la taxonomía) o,
     para una temática que todavía no tiene, la que propuso Claude —una
     existente o una nueva, marcada— o la que elija el analista. */
  function celdaDeArea(clave, t) {
    if (t.areaFija) {
      const area = areas.find((a) => a.id === t.area.id);
      return `<span class="small">${esc(t.area.nombre)}</span>${area?.es_especial
        ? ' <span class="chip-marca especial">especial</span>' : ''}`;
    }
    const propuestaNueva = t.area && !t.area.id;
    if (!areas.length && !propuestaNueva) {
      return '<span class="small muted">Sin área todavía</span>';
    }
    return `<select class="fselect fselect-sm" data-area="${esc(clave)}">
      <option value="">— Sin área —</option>
      ${propuestaNueva ? `<option value="__propuesta__" selected>nueva: ${esc(t.area.nombre)}</option>` : ''}
      ${areas.map((a) => `<option value="${a.id}" ${t.area?.id === a.id ? 'selected' : ''}>${esc(a.nombre)}</option>`).join('')}
      <option value="__nueva__">+ Área nueva…</option>
    </select>${propuestaNueva ? '<div class="small"><span class="chip-marca">área nueva</span></div>' : ''}`;
  }

  const agregarDeLaEntrada = () => {
    if (agregar(entrada$.value, 'manual')) {
      entrada$.value = '';
      cambio();
    }
  };
  $('[data-agregar]', contenedor).onclick = agregarDeLaEntrada;
  entrada$.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); agregarDeLaEntrada(); }
  });
  pintar();

  return {
    valor: () => [...elegidas.values()].map((t) => ({
      nombre: t.nombre, origen: t.origen, preguntas: t.preguntas, es_especial: t.especial,
      ...(t.area ? { area: t.area.nombre, area_id: t.area.id || null } : {}),
    })),
  };
}

function recortar(texto, largo = 48) {
  const limpio = String(texto || '').replace(/\s+/g, ' ').trim();
  return limpio.length > largo ? `${limpio.slice(0, largo - 1)}…` : limpio;
}

/* Las etiquetas, solo para mirar: el listado de cargas. */
export function chipsDeTematicas(lista = []) {
  if (!lista.length) return '<span class="small muted">—</span>';
  return `<span class="tematicas-chips">${lista.map((t) =>
    `<span class="chip chip-tematica">${esc(t.nombre)}${t.es_especial
      ? ' <span class="chip-marca especial">especial</span>' : ''}</span>`).join('')}</span>`;
}
