/* Análisis longitudinal (R4.1).

   Es la pantalla que explota lo único que un panel tiene y una muestra
   fresca no: la misma gente, medida en el tiempo. Tres solapas, y son tres
   porque responden tres preguntas distintas:

     Series        ¿qué preguntas de olas distintas son la misma medición?
     Evolución     ¿cómo se movió la gente entre categorías, de una ola a otra?
     Una persona   ¿qué contestó esta persona a lo largo de los años?

   Dos cosas que la pantalla tiene que dejar claras y no dar por sabidas:

   **Las sugerencias proponen.** El botón «Agregar a la serie» está al lado de
   cada candidata, nunca arriba de la lista entera: agregar es un acto por
   pregunta, de alguien que la leyó.

   **Ver la línea de tiempo de alguien es una reidentificación.** Se avisa
   antes de abrirla, no después, y queda registrada.
*/

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, alerta, toast, modal,
  cerrarModal, leerFormulario, fechaCorta, confirmar,
} from '../ui.js';

let solapa = 'series';
let serieActual = null;

const SOLAPAS = [
  ['series', 'Series'],
  ['evolucion', 'Evolución'],
  ['persona', 'Una persona'],
];

export async function render(main) {
  main.innerHTML = encabezado('Análisis', 'longitudinal',
    'Lo único que un panel tiene y una muestra fresca no: la misma gente, '
    + 'medida en el tiempo.') + `
    <div class="tabs" id="solapas">${SOLAPAS.map(([id, etiqueta]) =>
      `<button class="tab ${id === solapa ? 'active' : ''}" data-solapa="${id}">${esc(etiqueta)}</button>`
    ).join('')}</div>
    <div id="cuerpo">${cargando()}</div>`;

  $$('#solapas .tab').forEach((boton) => {
    boton.onclick = () => { solapa = boton.dataset.solapa; pintar(); };
  });
  await pintar();
}

async function pintar() {
  $$('#solapas .tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.solapa === solapa));
  const cuerpo = $('#cuerpo');
  cuerpo.innerHTML = cargando();
  try {
    if (solapa === 'series') await pintarSeries(cuerpo);
    else if (solapa === 'evolucion') await pintarEvolucion(cuerpo);
    else await pintarPersona(cuerpo);
  } catch (error) {
    cuerpo.innerHTML = alerta(error.message);
  }
}

/* ── Series (R4.1.b) ────────────────────────────────────────────────── */

async function pintarSeries(cuerpo) {
  const { items } = await api.series.listar();
  cuerpo.innerHTML = `
    <div class="card">
      <div class="card-header"><span class="card-header-title">Series declaradas</span>
        <span class="small muted">El sistema sugiere; agregar lo decide una persona.</span></div>
      <div class="card-body">
        <p class="small muted" style="margin-top:0">Una serie declara que
        preguntas de olas distintas son la misma medición. El sistema propone
        candidatas por parecido, pero no agrega ninguna sola: comparar dos
        preguntas parecidas que miden cosas distintas es peor que no
        comparar.</p>
        <button class="btn btn-orange" id="nueva">+ Nueva serie</button>
      </div>
      <div class="card-body tight">
        ${items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Serie</th><th>Preguntas</th><th>Categorías</th><th></th></tr></thead>
          <tbody>${items.map((s) => `<tr>
            <td><div class="td-strong">${esc(s.nombre)}</div>
                <div class="td-muted small">${esc(s.clave)}</div></td>
            <td>${s.preguntas}</td>
            <td>${s.categorias}</td>
            <td style="text-align:right"><button class="btn btn-outline btn-sm"
              data-ver="${esc(s.clave)}">Abrir</button></td>
          </tr>`).join('')}</tbody>
        </table></div>` : vacio('No hay series declaradas todavía.', '🔗')}
      </div>
    </div>
    <div id="detalle"></div>`;

  $('#nueva').onclick = abrirNuevaSerie;
  $$('[data-ver]').forEach((b) => {
    b.onclick = () => { serieActual = b.dataset.ver; verSerie(); };
  });
  if (serieActual && items.some((s) => s.clave === serieActual)) await verSerie();
  else serieActual = null;
}

function abrirNuevaSerie() {
  modal({
    titulo: 'Nueva serie',
    cuerpo: `
      <div class="form-group"><label>Clave</label>
        <input class="finput" name="clave" placeholder="situacion_economica">
        <div class="small muted">Minúsculas y guion bajo. No se cambia
        después: es lo que referencia a la serie desde afuera.</div></div>
      <div class="form-group"><label>Nombre</label>
        <input class="finput" name="nombre" placeholder="Situación económica"></div>
      <div class="form-group"><label>Categorías comunes</label>
        <input class="finput" name="categorias" placeholder="buena, intermedia, mala">
        <div class="small muted">Separadas por coma. Es el vocabulario al que
        se llevan las opciones de cada ola; sin él no hay dos olas
        comparables.</div></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Crear', clase: 'btn-orange', onClick: crearSerie },
    ],
  });
}

async function crearSerie(contenedor) {
  const datos = leerFormulario(contenedor);
  try {
    const serie = await api.series.crear({
      clave: datos.clave,
      nombre: datos.nombre,
      categorias: (datos.categorias || '').split(',')
        .map((c) => c.trim()).filter(Boolean)
        .map((c) => ({ clave: c, etiqueta: c })),
    });
    cerrarModal();
    serieActual = serie.clave;
    toast('Serie creada', 'ok');
    await pintar();
  } catch (error) { toast(error.message, 'error'); }
}

async function verSerie() {
  const detalle = $('#detalle');
  if (!detalle) return;
  detalle.innerHTML = cargando('20vh');
  const serie = await api.series.ver(serieActual);
  const sinMapear = serie.preguntas.filter((p) => p.sin_mapear.length);

  detalle.innerHTML = `
    <div class="card">
      <div class="card-header"><span class="card-header-title">${esc(serie.nombre)}</span>
        <span class="small muted">${serie.categorias.map((c) =>
          esc(c.etiqueta)).join(' · ') || 'sin categorías comunes'}</span></div>
      <div class="card-body">
        ${sinMapear.length ? alerta(
          `${sinMapear.length} pregunta(s) tienen opciones sin mapear. Una `
          + 'opción sin mapear no se cuenta en la comparación: no engrosa '
          + 'ninguna categoría, porque si lo hiciera el movimiento entre olas '
          + 'mentiría.', 'warn') : ''}
      </div>
      <div class="card-body tight">
        ${serie.preguntas.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Ola</th><th>Pregunta</th><th>Mapeo</th><th>Origen</th><th></th></tr></thead>
          <tbody>${serie.preguntas.map((p) => `<tr>
            <td><div class="td-strong">${esc(p.ola)}</div>
                <div class="td-muted small">${esc(fechaCorta(p.fecha_campo) || 'sin fecha')}</div></td>
            <td><div class="td-strong">${esc(p.codigo)}</div>
                <div class="td-muted small">${esc(p.texto)}</div></td>
            <td class="small">${Object.entries(p.mapeo).map(([opcion, categoria]) =>
                  `<div>${esc((p.opciones || {})[opcion] || opcion)} → <strong>${esc(categoria)}</strong></div>`
                ).join('') || '<span class="muted">— sin mapear —</span>'}
              ${p.sin_mapear.length ? `<div class="muted">sin mapear:
                ${p.sin_mapear.map((o) => esc((p.opciones || {})[o] || o)).join(', ')}</div>` : ''}</td>
            <td><span class="badge ${p.origen === 'declarada' ? 'badge-on' : 'badge-user'}">${
              p.origen === 'declarada' ? 'declarada' : 'sugerida'}</span></td>
            <td style="text-align:right">
              <button class="btn btn-outline btn-sm" data-mapear="${p.pregunta_id}">Mapear…</button>
              <button class="btn btn-outline btn-sm" data-quitar="${p.pregunta_id}">Quitar</button>
            </td></tr>`).join('')}</tbody>
        </table></div>` : vacio(
          'La serie no tiene preguntas todavía. Agregá la primera a mano y de '
          + 'ahí en más el sistema propone las de las otras olas.', '❓')}
      </div>
    </div>
    <div class="card">
      <div class="card-header"><span class="card-header-title">Preguntas parecidas en otras olas</span>
        <span class="small muted">Propone. No agrega ninguna.</span></div>
      <div class="card-body">
        <button class="btn btn-orange" id="agregar-mano">+ Agregar una pregunta</button>
        <button class="btn btn-outline" id="sugerir">Buscar candidatas</button>
        <div id="sugerencias" style="margin-top:1rem"></div>
      </div>
    </div>`;

  $('#sugerir').onclick = buscarSugerencias;
  $('#agregar-mano').onclick = () => abrirElegirPregunta(serie);
  $$('[data-mapear]').forEach((b) => {
    b.onclick = () => abrirMapeo(serie, Number(b.dataset.mapear));
  });
  $$('[data-quitar]').forEach((b) => {
    b.onclick = async () => {
      const ok = await confirmar({
        titulo: 'Quitar de la serie',
        cuerpo: 'La pregunta sale de la serie. La ola y sus respuestas no se '
                + 'tocan: lo que se deshace es la declaración de que mide lo mismo.',
        textoOk: 'Quitar',
      });
      if (!ok) return;
      await api.series.quitarPregunta(serieActual, Number(b.dataset.quitar));
      toast('Pregunta quitada', 'ok');
      await verSerie();
    };
  });
}

function abrirMapeo(serie, preguntaId) {
  const pregunta = serie.preguntas.find((p) => p.pregunta_id === preguntaId);
  const opciones = Object.entries(pregunta.opciones || {});
  if (!opciones.length) {
    toast('Esa pregunta no es cerrada: no tiene opciones que mapear', 'error');
    return;
  }
  if (!serie.categorias.length) {
    toast('La serie no tiene categorías comunes todavía', 'error');
    return;
  }
  modal({
    titulo: `Mapear «${pregunta.codigo}»`,
    cuerpo: `
      <p class="small muted">Cada opción de esta ola va a una categoría común
      de la serie. Lo que quede sin mapear no se cuenta en la comparación.</p>
      ${opciones.map(([clave, etiqueta]) => `
        <div class="form-group"><label>${esc(etiqueta)}</label>
          <select class="fselect" name="${esc(clave)}">
            <option value="">— sin mapear —</option>
            ${serie.categorias.map((c) =>
              `<option value="${esc(c.clave)}" ${pregunta.mapeo[clave] === c.clave ? 'selected' : ''}>${esc(c.etiqueta)}</option>`
            ).join('')}
          </select></div>`).join('')}`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      {
        texto: 'Guardar',
        clase: 'btn-orange',
        onClick: async (contenedor) => {
          const datos = leerFormulario(contenedor);
          const mapeo = {};
          opciones.forEach(([clave]) => { if (datos[clave]) mapeo[clave] = datos[clave]; });
          try {
            await api.series.mapear(serieActual, preguntaId, mapeo);
            cerrarModal();
            toast('Mapeo guardado', 'ok');
            await verSerie();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}

/* La primera pregunta de una serie no se puede sugerir: sin una de
   referencia no hay contra qué comparar. Así que tiene que poder elegirse de
   una lista, o la serie recién creada queda sin forma de arrancar. */
async function abrirElegirPregunta(serie) {
  const { items } = await api.series.preguntasDisponibles(serieActual);
  const disponibles = items.filter((p) => !p.ya_en_la_serie);
  if (!disponibles.length) {
    toast('No hay más preguntas en el corpus para agregar', 'error');
    return;
  }
  modal({
    titulo: 'Agregar una pregunta a la serie',
    ancho: 640,
    cuerpo: `
      <p class="small muted">Elegí la pregunta de la ola que corresponda. Una
      vez que la serie tenga una, el sistema puede proponer las equivalentes
      de las otras olas.</p>
      <div class="form-group"><label>Pregunta</label>
        <select class="fselect" name="pregunta_id" size="10"
                style="height:auto">${disponibles.map((p) =>
          `<option value="${p.pregunta_id}">${esc(p.ola)} · ${esc(p.codigo)} — ${esc(p.texto)}</option>`
        ).join('')}</select></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      {
        texto: 'Agregar',
        clase: 'btn-orange',
        onClick: async (contenedor) => {
          const elegida = leerFormulario(contenedor).pregunta_id;
          if (!elegida) { toast('Elegí una pregunta', 'error'); return; }
          try {
            const salida = await api.series.agregarPregunta(
              serieActual, Number(elegida));
            cerrarModal();
            toast(salida.aviso || 'Agregada. Falta mapear sus opciones.',
                  salida.aviso ? 'error' : 'ok');
            await verSerie();
          } catch (error) { toast(error.message, 'error'); }
        },
      },
    ],
  });
}

async function buscarSugerencias() {
  const caja = $('#sugerencias');
  caja.innerHTML = cargando('15vh');
  try {
    const salida = await api.series.sugerencias(serieActual);
    if (!salida.sugerencias.length) {
      caja.innerHTML = `<p class="small muted">${esc(salida.motivo
        || 'No hay preguntas parecidas en olas que la serie no cubra todavía.')}</p>`;
      return;
    }
    caja.innerHTML = `<div class="table-wrap"><table>
      <thead><tr><th>Ola</th><th>Pregunta</th><th>Parecido</th><th></th></tr></thead>
      <tbody>${salida.sugerencias.map((s) => `<tr>
        <td class="small">${esc(s.ola)}</td>
        <td><div class="td-strong">${esc(s.codigo)}</div>
            <div class="td-muted small">${esc(s.texto)}</div></td>
        <td>${((1 - s.distancia) * 100).toFixed(0)} %</td>
        <td style="text-align:right"><button class="btn btn-orange btn-sm"
          data-agregar="${s.pregunta_id}">Agregar a la serie</button></td>
      </tr>`).join('')}</tbody>
    </table></div>`;
    $$('[data-agregar]').forEach((b) => {
      b.onclick = async () => {
        await api.series.agregarPregunta(
          serieActual, Number(b.dataset.agregar), null, 'sugerida_aceptada');
        toast('Agregada. Falta mapear sus opciones.', 'ok');
        await verSerie();
      };
    });
  } catch (error) { caja.innerHTML = alerta(error.message); }
}

/* ── Evolución (R4.1.c) ─────────────────────────────────────────────── */

async function pintarEvolucion(cuerpo) {
  const { items } = await api.series.listar();
  const conDos = items.filter((s) => s.preguntas >= 2);
  if (!conDos.length) {
    cuerpo.innerHTML = vacio(
      'Para ver una evolución hace falta una serie con al menos dos olas.', '📊');
    return;
  }
  cuerpo.innerHTML = `
    <div class="card"><div class="card-body">
      <div class="form-group" style="margin-bottom:0"><label>Serie</label>
        <select class="fselect" id="serie">${conDos.map((s) =>
          `<option value="${esc(s.clave)}">${esc(s.nombre)}</option>`).join('')}</select>
      </div>
    </div></div>
    <div id="matriz"></div>`;
  $('#serie').onchange = verMatriz;
  await verMatriz();
}

async function verMatriz() {
  const caja = $('#matriz');
  caja.innerHTML = cargando('20vh');
  try {
    const m = await api.series.transiciones($('#serie').value);
    const celdas = {};
    m.celdas.forEach((c) => { celdas[`${c.desde}→${c.hasta}`] = c.personas; });
    const filas = [...new Set(m.celdas.map((c) => c.desde))].sort();
    const columnas = [...new Set(m.celdas.map((c) => c.hasta))].sort();

    caja.innerHTML = `
      <div class="stat-grid">
        <div class="stat s-total"><label>En las dos olas</label>
          <strong>${m.en_las_dos_olas}</strong></div>
        <div class="stat s-ok"><label>Se mantuvieron</label>
          <strong>${m.estables}</strong></div>
        <div class="stat s-warn"><label>Cambiaron</label>
          <strong>${m.se_movieron}</strong></div>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-header-title">${esc(m.desde.ola)} → ${esc(m.hasta.ola)}</span>
          <span class="small muted">${esc(fechaCorta(m.desde.fecha_campo) || '')}
            → ${esc(fechaCorta(m.hasta.fecha_campo) || '')}</span></div>
        <div class="card-body tight">
          <div class="table-wrap"><table>
            <thead><tr><th>De ↓ / A →</th>${columnas.map((c) =>
              `<th>${esc(c)}</th>`).join('')}</tr></thead>
            <tbody>${filas.map((f) => `<tr>
              <td class="td-strong">${esc(f)}</td>
              ${columnas.map((c) => {
                const n = celdas[`${f}→${c}`] || 0;
                return `<td style="${f === c ? 'font-weight:700;' : ''}${
                  n ? '' : 'opacity:.3'}">${n}</td>`;
              }).join('')}</tr>`).join('')}</tbody>
          </table></div>
        </div>
      </div>
      <div class="card"><div class="card-body">
        <p class="small muted" style="margin:0">
          <strong>Quiénes no entran en la matriz.</strong>
          ${m.solo_en_una.solo_al_inicio} estuvieron solo en la primera ola y
          ${m.solo_en_una.solo_al_final} solo en la segunda.
          ${esc(m.solo_en_una.aviso)}
          ${m.sin_mapear ? ` Además, ${m.sin_mapear} tienen alguna respuesta
          sin mapear a una categoría común.` : ''}
        </p>
      </div></div>`;
  } catch (error) { caja.innerHTML = alerta(error.message); }
}

/* ── Una persona (R4.1.c) ───────────────────────────────────────────── */

async function pintarPersona(cuerpo) {
  cuerpo.innerHTML = `
    <div class="card"><div class="card-body">
      ${alerta('Abrir la línea de tiempo de una persona identificada es una '
               + 'reidentificación: queda registrada con tu usuario, la fecha '
               + 'y el motivo.', 'warn')}
      <div class="form-group" style="margin-bottom:0"><label>Buscar panelista</label>
        <input class="finput" id="busqueda" placeholder="Nombre o documento">
      </div>
      <div id="resultados" style="margin-top:1rem"></div>
    </div></div>
    <div id="linea"></div>`;

  let temporizador;
  $('#busqueda').oninput = (e) => {
    clearTimeout(temporizador);
    const texto = e.target.value;
    temporizador = setTimeout(() => buscar(texto), 300);
  };
}

async function buscar(texto) {
  const caja = $('#resultados');
  if (!caja) return;
  if (!texto || texto.trim().length < 2) { caja.innerHTML = ''; return; }
  try {
    const { items } = await api.panelistas.listar({ q: texto.trim(), limite: 8 });
    caja.innerHTML = items.length ? `<div class="table-wrap"><table>
      <tbody>${items.map((p) => `<tr>
        <td><div class="td-strong">${esc(p.nombre || '—')}</div></td>
        <td class="small muted">${esc(p.documento || '')}</td>
        <td style="text-align:right"><button class="btn btn-outline btn-sm"
          data-linea="${esc(p.id_persona)}">Ver evolución</button></td>
      </tr>`).join('')}</tbody>
    </table></div>` : '<p class="small muted">Sin resultados.</p>';
    $$('[data-linea]').forEach((b) => {
      b.onclick = () => verLinea(b.dataset.linea);
    });
  } catch (error) { caja.innerHTML = alerta(error.message); }
}

async function verLinea(idPersona) {
  const caja = $('#linea');
  caja.innerHTML = cargando('20vh');
  try {
    const linea = await api.longitudinal.dePersona(idPersona);
    caja.innerHTML = `
      <div class="card">
        <div class="card-header"><span class="card-header-title">${esc(linea.nombre || '—')}</span>
          <span class="small muted">${linea.olas.length} ola(s)</span></div>
        ${linea.aviso ? `<div class="card-body">
          <p class="small muted" style="margin:0">${esc(linea.aviso)}</p></div>` : ''}
      </div>
      ${linea.olas.map((o) => `
        <div class="card">
          <div class="card-header">
            <span class="card-header-title">${esc(o.encuesta)}</span>
            <span class="small muted">${esc(fechaCorta(o.fecha_campo) || 'sin fecha de campo')}
              · ${esc(o.panel)} · ${o.respondio ? 'respondió' : 'no respondió'}${
              o.calidad_estado !== 'pendiente' ? ` · calidad ${esc(o.calidad_estado)}` : ''}</span>
          </div>
          <div class="card-body tight">
            ${o.respuestas.length ? `<div class="table-wrap"><table>
              <tbody>${o.respuestas.map((r) => `<tr>
                <td style="width:50%" class="small">${esc(r.texto)}</td>
                <td class="td-strong">${esc(r.respuesta ?? '—')}</td>
              </tr>`).join('')}</tbody>
            </table></div>` : `<div class="card-body">
              <p class="small muted" style="margin:0">Sin respuestas ingestadas.</p>
            </div>`}
          </div>
        </div>`).join('')}`;
  } catch (error) { caja.innerHTML = alerta(error.message); }
}
