/* Fase 8 — calidad del dato semántico, del lado de la pantalla.

   Dos piezas que usan la importación (paginas/encuestas.js) y el reproceso
   (paginas/reproceso.js):

   * **El panel de hallazgos.** Lo que el servidor detectó, en el orden en
     que lo devuelve: primero lo que rompe, después lo que mejora, al final
     lo informativo. Cada hallazgo trae sus acciones; ninguna está aplicada
     hasta que el analista la elige. Mostrarlo jerarquizado no es estética:
     más detección es más decisiones, y un paso de revisión que se vuelve
     largo termina confirmándose sin mirar.

   * **La vista previa.** Cómo va a quedar el texto embebido de cada
     variable, con valores reales. La calcula el servidor con la misma
     función que la ingesta; acá solo se pinta. Una copia en JavaScript de
     «qué se embebe» divergiría, y lo que divergiría es justamente la vista
     que dice «esto es lo que se va a escribir». */

import { esc } from './ui.js';

const SEVERIDADES = {
  rompe: { etiqueta: 'Rompe', clase: 'sev-rompe' },
  mejora: { etiqueta: 'Mejora', clase: 'sev-mejora' },
  info: { etiqueta: 'Información', clase: 'sev-info' },
};

const NOMBRES_DE_PII = {
  correo: 'correo', telefono: 'teléfono', cedula: 'cédula', url: 'dirección web',
};

export const MOTIVOS = {
  no_marcada: 'no marcada: no se ingesta',
  no_respuesta: 'no respuesta: no se ingesta',
};

/* Las decisiones de normalización que viajan en cada pregunta. Coinciden con
   `semantica.CLAVES_DE_NORMALIZACION`. */
export const CLAVES = ['solo_marcadas', 'valores_marcados', 'excluir_valores',
  'prefijo_respuesta', 'fusionada_con', 'bateria', 'pii_aceptada'];

/* Cuántos valores por variable viajan para la vista previa: los más
   frecuentes y los menos. Coincide con `sav.MUESTRA_*`. */
const FRECUENTES = 30;
const RAROS = 10;

/* La clave de un hallazgo, estable entre dos diagnósticos del mismo
   archivo: así lo aplicado sigue marcado aunque se vuelva a detectar. */
export const claveDe = (h) => `${h.tipo}|${(h.variables || []).join(',')}`;

/* `[{valor, filas}]` de una columna, para los archivos que se leen en el
   navegador (.csv, .xlsx). Es contar, no componer texto: componerlo es del
   servidor. */
export function muestraDeFilas(filas, codigo) {
  const conteo = new Map();
  (filas || []).forEach((fila) => {
    const crudo = fila[codigo];
    if (crudo === null || crudo === undefined) return;
    const valor = String(crudo).trim();
    if (!valor) return;
    conteo.set(valor, (conteo.get(valor) || 0) + 1);
  });
  let lista = [...conteo.entries()]
    .sort((a, b) => (b[1] - a[1]) || (a[0] < b[0] ? -1 : 1));
  if (lista.length > FRECUENTES + RAROS) {
    lista = [...lista.slice(0, FRECUENTES), ...lista.slice(-RAROS)];
  }
  return lista.map(([valor, n]) => ({ valor, filas: n }));
}

/* ── El panel ─────────────────────────────────────────────────────── */

export function panelHtml(diagnostico, aplicados = new Set()) {
  const hallazgos = diagnostico?.hallazgos || [];
  if (!hallazgos.length) {
    return `<div class="calidad"><p class="small">No se detectó nada que
      degrade el texto que se embebe. Igual conviene mirar la vista previa de
      cada variable.</p></div>`;
  }
  const r = diagnostico.resumen || {};
  const tarjeta = (h, i) => {
    const sev = SEVERIDADES[h.severidad] || SEVERIDADES.info;
    const aplicado = h.acciones.some((_a, j) => aplicados.has(`${claveDe(h)}#${j}`));
    return `<div class="hallazgo ${esc(h.severidad)}${aplicado ? ' aplicado' : ''}">
      <h5><span class="sev ${sev.clase}">${esc(sev.etiqueta)}</span> ${esc(h.titulo)}</h5>
      <p>${esc(h.mensaje)}</p>
      ${h.detalle?.ejemplos?.length ? `<div class="ejemplos-pii">${h.detalle.ejemplos
        .map((e) => `${esc(NOMBRES_DE_PII[e.tipo] || e.tipo)}: ${esc(e.texto)}`)
        .join('<br>')}</div>` : ''}
      ${h.detalle?.items?.length ? `<ul class="items">${h.detalle.items.map((it, k) => {
        const hecho = aplicado || aplicados.has(`${claveDe(h)}@${it.codigo}`);
        return `<li><code>${esc(it.codigo)}</code> — ${esc(it.texto)}
          <button class="btn-link" data-hallazgo="${i}" data-item="${k}"
            ${hecho ? 'disabled' : ''}>${hecho ? '✓ aplicado' : 'usar en esta'}</button></li>`;
      }).join('')}</ul>` : ''}
      ${h.acciones.length ? `<div class="acciones">${h.acciones.map((a, j) => {
        const hecho = aplicados.has(`${claveDe(h)}#${j}`);
        return `<button class="btn btn-outline btn-sm" data-hallazgo="${i}"
          data-accion="${j}" ${hecho ? 'disabled' : ''}>${hecho ? '✓ ' : ''}${esc(a.etiqueta)}</button>`;
      }).join('')}</div>` : ''}
    </div>`;
  };
  const de = (sev) => hallazgos.map((h, i) => [h, i]).filter(([h]) => h.severidad === sev);
  const info = de('info');
  return `<div class="calidad">
    <div class="calidad-resumen">
      <span class="sev sev-rompe">${r.rompe || 0} rompe</span>
      <span class="sev sev-mejora">${r.mejora || 0} mejora</span>
      <span class="sev sev-info">${r.info || 0} información</span>
    </div>
    ${[...de('rompe'), ...de('mejora')].map(([h, i]) => tarjeta(h, i)).join('')}
    ${info.length ? `<details><summary class="small">Información (${info.length})</summary>
      ${info.map(([h, i]) => tarjeta(h, i)).join('')}</details>` : ''}
  </div>`;
}

/* Engancha los botones de acción. `alAplicar(hallazgo, accion)` hace el
   cambio en el editor de quien llama; acá solo se marca como aplicado. */
export function activarPanel(contenedor, diagnostico, aplicados, alAplicar, repintar) {
  contenedor.querySelectorAll('[data-hallazgo]').forEach((boton) => {
    boton.onclick = (e) => {
      e.preventDefault();
      const h = diagnostico.hallazgos[Number(boton.dataset.hallazgo)];
      if (boton.dataset.item !== undefined) {
        // Una sola variable de un hallazgo agrupado.
        const item = h.detalle.items[Number(boton.dataset.item)];
        alAplicar(h, { propuesta: { [item.codigo]: item.propuesta } });
        aplicados.add(`${claveDe(h)}@${item.codigo}`);
      } else {
        const j = Number(boton.dataset.accion);
        alAplicar(h, h.acciones[j]);
        aplicados.add(`${claveDe(h)}#${j}`);
      }
      repintar();
    };
  });
}

/* ── La vista previa ──────────────────────────────────────────────── */

const ETIQUETAS_DE_CHIP = {
  solo_marcadas: () => 'solo lo marcado',
  excluir_valores: (v) => `excluye ${v.join(', ')}`,
  prefijo_respuesta: (v) => `prefijo «${v}»`,
  fusionada_con: (v) => `fusionada con ${v}`,
  pii_aceptada: () => 'datos personales: ingestada a conciencia',
};

/* Las decisiones aplicadas a una pregunta, como etiquetas que se pueden
   quitar. Quitar una es deshacerla: nada queda aplicado sin poder volver. */
export function chipsHtml(pregunta) {
  return Object.entries(ETIQUETAS_DE_CHIP)
    .filter(([clave]) => {
      const v = pregunta[clave];
      return Array.isArray(v) ? v.length : Boolean(v);
    })
    .map(([clave, etiqueta]) => `<span class="chip">${esc(etiqueta(pregunta[clave]))}
      <button type="button" data-quitar="${clave}" title="Quitar">×</button></span>`)
    .join('');
}

export function previaHtml(previa, pregunta, textoOriginal) {
  if (!previa) return '<div class="small muted">Calculando la vista previa…</div>';
  const ejemplos = (previa.ejemplos || []).map((e) => (e.texto_embebido
    ? `<div class="ej">${esc(e.texto_embebido)} <span class="n">· ${e.filas} fila(s)</span></div>`
    : `<div class="ej fuera">${esc(e.valor)} <span class="n">· ${e.filas} fila(s) ·
         ${esc(MOTIVOS[e.motivo] || e.motivo)}</span></div>`)).join('');
  const descartes = Object.entries(previa.descartadas || {})
    .map(([motivo, n]) => `${n} ${motivo.replace('_', ' ')}`);
  const chips = chipsHtml(pregunta);
  const cambiado = textoOriginal && textoOriginal !== pregunta.texto;
  return `
    <div class="small"><strong>Se embebe así</strong> · genera
      ${previa.genera} de ${previa.respuestas} respuesta(s)${descartes.length
        ? ` · descarta ${descartes.join(', ')}` : ''}</div>
    ${ejemplos || '<div class="muted">Sin valores en el archivo.</div>'}
    ${chips ? `<div class="chips">${chips}</div>` : ''}
    ${cambiado ? `<div class="original">Original: «${esc(textoOriginal)}»
      <a data-volver>volver al original</a></div>` : ''}`;
}
