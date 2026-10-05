/* R7.1 — la versión del consentimiento se elige, no se escribe.

   Era una cadena exacta (`consentimiento-2026-01`) que tenía que coincidir
   con una versión publicada y activa, o la operación fallaba. El dato ya
   existe en el sistema: pedirlo tipeado es pedirle a alguien que recuerde
   de memoria algo que el sistema conoce.

   El desplegable **no agrega una validación** —el trigger de la base ya
   rechaza una versión no publicada— sino que elimina la posibilidad del
   error. Y de paso hace visible qué textos hay publicados, que hoy es
   información escondida en otra pantalla.

   Lo que sale al backend sigue siendo la misma cadena: el contrato no
   cambia, solo cómo se llega a ella.
*/

import * as api from './api.js';
import { esc, alerta } from './ui.js';

/* `{finalidad: [{version, cuerpo, creado_en}, …]}`, solo las activas y
   ordenadas como las devuelve el servidor (la más reciente primero). */
let activos = {};
let cargado = false;

export async function cargar() {
  try {
    const { items } = await api.inscripciones.textos();
    activos = {};
    items.filter((t) => t.activo).forEach((t) => {
      (activos[t.finalidad] = activos[t.finalidad] || []).push(t);
    });
    cargado = true;
  } catch {
    activos = {};
    cargado = false;
  }
  return activos;
}

export const versionesDe = (finalidad) => activos[finalidad] || [];

/* La que se manda si nadie elige: la más reciente. Mantiene el
   comportamiento de antes para quien no quiera tocar nada. */
export const versionPorDefecto = (finalidad) =>
  (versionesDe(finalidad)[0] || {}).version || null;

export const hayVersiones = (finalidad) => versionesDe(finalidad).length > 0;

const DONDE_PUBLICAR = 'Inscripciones → Textos de consentimiento';

/* El aviso de la finalidad sin ninguna versión activa. Dice **dónde**
   publicarla: un desplegable vacío sin explicación manda a adivinar. */
export function avisoSinVersiones(finalidad) {
  return alerta(
    `No hay ningún texto de consentimiento publicado y activo para `
    + `«${finalidad}». Sin él la base rechaza el otorgamiento, y con razón: `
    + `un consentimiento sin texto recuperable no es demostrable. Se publica `
    + `en ${DONDE_PUBLICAR}.`, 'warn');
}

/* El desplegable. `id` es el del `<select>`; el valor es la versión, que es
   exactamente lo que viaja al backend. */
export function selector(finalidad, { id, etiqueta } = {}) {
  const versiones = versionesDe(finalidad);
  if (!versiones.length) return avisoSinVersiones(finalidad);

  const unica = versiones.length === 1;
  return `
    <div class="form-group">
      <label for="${esc(id)}">${esc(etiqueta || 'Versión del texto consentido')}</label>
      <div class="fila-version">
        <select class="fselect" id="${esc(id)}" data-finalidad="${esc(finalidad)}">
          ${versiones.map((t, i) => `
            <option value="${esc(t.version)}"${i === 0 ? ' selected' : ''}>
              ${esc(t.version)}</option>`).join('')}
        </select>
        <button type="button" class="btn btn-outline btn-sm" data-ver-texto="${esc(id)}">
          Ver texto</button>
      </div>
      <div class="field-hint">${unica
        ? 'Es la única versión activa de esta finalidad, así que viene elegida.'
        : `${versiones.length} versiones activas. Se manda la elegida, tal cual.`}</div>
      <pre class="texto-consentido" id="${esc(id)}-texto" hidden></pre>
    </div>`;
}

/* Engancha los botones «Ver texto» de un contenedor. Es lo que convierte al
   desplegable en algo más que un código: lo que la persona aceptó es el
   texto, no la etiqueta.

   El texto se despliega **en el lugar**, debajo del desplegable, y no en un
   modal: los desplegables viven adentro del formulario de alta o de la
   importación, que ya son un modal, y abrir otro encima cerraba el primero
   y se llevaba todo lo cargado. Ver el texto antes de confirmar no puede
   costar tener que llenar el formulario de nuevo. */
export function activarVerTexto(contenedor) {
  contenedor.querySelectorAll('[data-ver-texto]').forEach((boton) => {
    const select = contenedor.querySelector(`#${boton.dataset.verTexto}`);
    const destino = contenedor.querySelector(`#${boton.dataset.verTexto}-texto`);
    if (!select || !destino) return;
    const pintar = () => {
      const texto = versionesDe(select.dataset.finalidad).find(
        (t) => t.version === select.value);
      destino.textContent = texto?.cuerpo
        || 'Esta versión no tiene texto cargado.';
    };
    boton.onclick = () => {
      destino.hidden = !destino.hidden;
      boton.textContent = destino.hidden ? 'Ver texto' : 'Ocultar texto';
      if (!destino.hidden) pintar();
    };
    // Si se cambia de versión con el texto abierto, se ve el de la nueva:
    // mostrar el de la anterior al lado de la elegida sería peor que nada.
    select.addEventListener('change', () => { if (!destino.hidden) pintar(); });
  });
}

export const estaCargado = () => cargado;
