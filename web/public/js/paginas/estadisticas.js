/* R7.5 — Estadísticas de base: los dos stores en una pantalla.

   Para saber cuántos panelistas o cuántas respuestas hay había que
   consultar la base a mano, y la información está repartida entre dos
   instancias de Cloud SQL que nadie mira juntas.

   **La brecha es la razón de esta pantalla**, no un apéndice. Los conteos
   sueltos —«1.008 panelistas», «24.935 respuestas»— se miran una vez. El
   número que se usa todas las semanas es «de mis 1.131 panelistas, ¿sobre
   cuántos puedo realmente consultar?», y por eso va primero y grande.

   Todo sale de **una** llamada: el backend resuelve los conteos en pocas
   consultas agregadas, no en una por tarjeta.
*/

import * as api from '../api.js';
import {
  $, esc, encabezado, cargando, alerta, modal, cerrarModal, fechaCorta,
} from '../ui.js';

const numero = (n) => (n == null ? '—' : Number(n).toLocaleString('es-UY'));

function tamano(bytes) {
  if (!bytes) return '—';
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

const dato = (etiqueta, texto, nota = '') => `
  <div class="tarjeta-dato">
    <div class="valor">${esc(texto)}</div>
    <div class="etiqueta">${esc(etiqueta)}</div>
    ${nota ? `<div class="nota small">${esc(nota)}</div>` : ''}
  </div>`;

const tarjeta = (etiqueta, valor, nota = '') => dato(etiqueta, numero(valor), nota);

export async function render(main) {
  main.innerHTML = encabezado('Estadísticas', 'de la base',
    'Los dos stores en una pantalla, sin pedir parámetros.')
    + cargando();
  try {
    pintar(main, await api.estadisticas.todo());
  } catch (error) {
    main.innerHTML = encabezado('Estadísticas', 'de la base', '')
      + alerta(error.message);
  }
}

function pintar(main, d) {
  const { panelistas: p, consentimiento: c, corpus, salud, brecha, cargas } = d;

  main.innerHTML = encabezado('Estadísticas', 'de la base',
    'Todo sale de una sola consulta. Los indicadores de composición no se '
    + 'repiten acá: están en su propia pantalla.')
  + brechaHtml(brecha, p)
  + `
  <div class="card">
    <h3>Panelistas</h3>
    <div class="grid-datos">
      ${tarjeta('Total', p.total)}
      ${tarjeta('Activos', p.activos)}
      ${tarjeta('Sin panel', p.sin_panel, 'cargados sin asociar a ninguno')}
      ${tarjeta('Con correo', p.con_email)}
      ${tarjeta('Con celular', p.con_celular)}
      ${tarjeta('Altas en 30 días', p.altas_30_dias)}
    </div>
    ${p.por_panel.length ? `
      <table class="tabla" style="margin-top:1rem">
        <thead><tr><th>Panel</th><th class="num">Miembros</th></tr></thead>
        <tbody>${p.por_panel.map((x) => `
          <tr><td>${esc(x.nombre)}</td><td class="num">${numero(x.miembros)}</td></tr>`).join('')}
        </tbody>
      </table>` : ''}
  </div>

  <div class="card">
    <h3>Consentimiento vigente</h3>
    <table class="tabla">
      <thead><tr><th>Finalidad</th><th class="num">Lo tienen</th><th class="num">No lo tienen</th></tr></thead>
      <tbody>${c.por_finalidad.map((f) => `
        <tr><td>${esc(f.finalidad)}</td>
            <td class="num">${numero(f.vigentes)}</td>
            <td class="num">${numero(f.sin_el)}</td></tr>`).join('')}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h3>Corpus semántico</h3>
    <div class="grid-datos">
      ${tarjeta('Respuestas', corpus.respuestas)}
      ${tarjeta('Individuos con respuestas', corpus.individuos_con_respuestas)}
      ${tarjeta('Preguntas distintas', corpus.preguntas)}
      ${tarjeta('Estudios ingestados', corpus.cuestionarios)}
      ${tarjeta('Respuestas por individuo', corpus.respuestas_por_individuo)}
    </div>
    ${corpus.estudios.length ? `
      <table class="tabla" style="margin-top:1rem">
        <thead><tr><th>Estudio</th><th>Campo</th><th class="num">Respuestas</th></tr></thead>
        <tbody>${corpus.estudios.map((e) => `
          <tr><td>${esc(e.nombre)}</td>
              <td>${e.fecha_campo ? esc(fechaCorta(e.fecha_campo)) : '—'}</td>
              <td class="num">${numero(e.respuestas)}</td></tr>`).join('')}
        </tbody>
      </table>` : ''}
  </div>

  <div class="card">
    <h3>Tamaño y salud del corpus</h3>
    <div class="grid-datos">
      ${dato('Tabla de respuestas', tamano(salud.tabla_bytes))}
      ${dato('Índice vectorial', tamano(salud.indice_bytes))}
      ${tarjeta('Dimensión', salud.dimension_embeddings)}
    </div>
    <p class="small" style="margin-top:0.6rem">
      El índice tiene que entrar en la memoria de la instancia. Cuando se
      acerque, conviene subir de tier: el umbral está en
      <code>docs/COSTOS.md</code> §4.
    </p>
  </div>

  ${cargasHtml(cargas)}
  ${estudiosHtml(d.estudios_de_origen)}`;

  $('#ver-sin-respuestas', main)?.addEventListener('click', verSinRespuestas);
  // R8.9 — corregir lo ya cargado de una encuesta o de una carga sin panel.
  // Es el único listado donde aparecen las cargas sin panel, así que el
  // reproceso se ofrece desde acá además de desde la ficha de la encuesta.
  main.querySelectorAll('[data-reprocesar]').forEach((boton) => {
    boton.onclick = async () => {
      const [tipo, id] = boton.dataset.reprocesar.split(':');
      const { abrirReproceso } = await import('./reproceso.js');
      abrirReproceso({ tipo, id: Number(id), nombre: `${tipo} ${id}` },
                     () => render(main));
    };
  });
}

/* La sección que justifica la pantalla. Va arriba y con el número grande
   que contesta «¿sobre cuántos puedo consultar?». */
function brechaHtml(b, p) {
  const huerfanos = b.individuos_sin_panelista;
  return `
  <div class="card destacada">
    <h3>Lo que una búsqueda puede alcanzar</h3>
    <div class="grid-datos">
      ${tarjeta('Consultables', b.consultables,
                'tienen respuestas y uso semántico vigente')}
      ${tarjeta('De un total de', p.total, 'panelistas')}
    </div>
    <table class="tabla" style="margin-top:1rem">
      <tbody>
        <tr>
          <td>Panelistas <strong>sin ninguna respuesta</strong> procesada
              <div class="small">Existen en la bóveda y son invisibles para una
              consulta por concepto. Es el número que explica por qué una
              búsqueda devuelve menos gente de la esperada.</div></td>
          <td class="num">${numero(b.panelistas_sin_respuestas.cuantos)}</td>
          <td><button class="btn btn-outline btn-sm" id="ver-sin-respuestas">Ver quiénes</button></td>
        </tr>
        <tr>
          <td>Panelistas <strong>sin uso semántico vigente</strong>
              <div class="small">Aunque tengan respuestas, no pueden aparecer
              en resultados.</div></td>
          <td class="num">${numero(b.panelistas_sin_uso_semantico.cuantos)}</td>
          <td></td>
        </tr>
        <tr class="${huerfanos.es_problema ? 'fila-problema' : ''}">
          <td>Individuos del store semántico <strong>sin panelista</strong>
              <div class="small">${esc(huerfanos.nota)}</div></td>
          <td class="num">${numero(huerfanos.cuantos)}</td>
          <td>${huerfanos.es_problema
                ? '<span class="chip chip-error">Problema de integridad</span>'
                : '<span class="chip chip-ok">Correcto</span>'}</td>
        </tr>
      </tbody>
    </table>
  </div>`;
}

/* R-ORG.5 — las cargas como estudios: con su nombre, la fecha del estudio y
   cuántas personas creó y reutilizó cada una. Sale del vínculo persona ↔
   carga, así que cuenta a quienes siguen en la bóveda y a lo cargado desde
   que el vínculo existe; la nota lo dice. */
function estudiosHtml(estudios) {
  if (!estudios?.items?.length) return '';
  return `
  <div class="card">
    <h3>Estudios de origen</h3>
    <p class="small muted">${esc(estudios.nota)}</p>
    <table class="tabla">
      <thead><tr><th>Estudio</th><th>Fecha del estudio</th><th>Público objetivo</th>
                 <th class="num">Creadas</th><th class="num">Reutilizadas</th>
                 <th class="num">Total</th></tr></thead>
      <tbody>${estudios.items.map((e) => `
        <tr>
          <td>${esc(e.nombre)}</td>
          <td>${e.fecha_estudio ? esc(fechaCorta(e.fecha_estudio)) : '—'}</td>
          <td class="small">${esc(e.publico_objetivo || '—')}</td>
          <td class="num">${numero(e.personas_creadas)}</td>
          <td class="num">${numero(e.personas_reutilizadas)}</td>
          <td class="num">${numero(e.personas)}</td>
        </tr>`).join('')}
      </tbody>
    </table>
  </div>`;
}

function cargasHtml(cargas) {
  if (!cargas.items.length) return '';
  return `
  <div class="card">
    <h3>Últimas cargas</h3>
    ${cargas.con_problemas.length ? alerta(
      `Hay ${cargas.con_problemas.length} carga(s) con lotes fallidos o sin `
      + `avanzar: ${cargas.con_problemas.join(', ')}. Hasta ahora eso solo se `
      + `veía consultando la base.`, 'warn') : ''}
    <table class="tabla">
      <thead><tr><th>#</th><th>Destino</th><th>Estado</th>
                 <th class="num">Filas</th><th class="num">Lotes ok</th>
                 <th class="num">Fallidos</th><th>Creada</th><th></th></tr></thead>
      <tbody>${cargas.items.map((t) => `
        <tr>
          <td>${t.trabajo_id}</td>
          <td>${esc(t.destino_tipo)} ${t.destino_id}</td>
          <td>${esc(t.estado_etiqueta || t.estado)}</td>
          <td class="num">${numero(t.filas_procesadas)} / ${numero(t.filas_total)}</td>
          <td class="num">${numero(t.lotes_ok)}</td>
          <td class="num">${numero(t.lotes_fallidos)}</td>
          <td>${esc(fechaCorta(t.creado_en))}</td>
          <td><button class="btn btn-outline btn-sm" data-reprocesar="${esc(t.destino_tipo)}:${t.destino_id}"
                title="Corregir textos, etiquetas o normalización sin volver a subir el archivo (R8.9)">
                Reprocesar</button></td>
        </tr>`).join('')}
      </tbody>
    </table>
  </div>`;
}

/* El DoD pide poder **listar** y no solo contar: un número sin desglose no
   se puede accionar. Lo que se lista son `id_persona`, igual que un
   resultado de consulta: ver quién es cada uno sigue siendo reidentificar. */
async function verSinRespuestas() {
  try {
    const d = await api.estadisticas.sinRespuestas(500);
    modal({
      titulo: `Panelistas sin respuestas procesadas (${numero(d.cuantos)})`,
      ancho: '640px',
      cuerpo: `
        <p class="small">Son identificadores, no personas: ver quién es cada
        uno sigue siendo una reidentificación, con su motivo y su registro.</p>
        <div class="lista-ids">${d.ejemplos.map(
          (i) => `<code>${esc(i)}</code>`).join(' ')}</div>
        ${d.cuantos > d.ejemplos.length ? `<p class="small">Se muestran
          ${d.ejemplos.length} de ${numero(d.cuantos)}.</p>` : ''}`,
      acciones: [{ texto: 'Cerrar', clase: 'btn-outline',
                   onClick: cerrarModal }],
    });
  } catch (error) {
    modal({ titulo: 'No se pudo listar', cuerpo: alerta(error.message),
            acciones: [{ texto: 'Cerrar', clase: 'btn-outline',
                         onClick: cerrarModal }] });
  }
}
