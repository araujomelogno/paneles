/* Consultas semánticas (R2.4, R2.5, R2.7 a R2.11 + P1).

   La pantalla se organiza alrededor de la idea de que una consulta es una
   lista de criterios de dos tipos. Los semánticos se escriben en lenguaje
   natural; los demográficos se eligen de una lista. Eso no es un detalle de
   interfaz: es la distinción que decide dónde se resuelve cada cosa.

   Lo que la pantalla tiene que dejar ver, además del ranking:

   * la evidencia de cada persona, con estudio y pregunta (sin eso el
     resultado no se puede verificar);
   * a quién se excluyó y por qué (el que dice lo contrario es información,
     no basura);
   * qué etapas corrieron y cuáles degradaron (un ranking sin reranking es
     utilizable, pero hay que saberlo);
   * que el resultado son tokens opacos, y que ver los nombres es otro paso.
*/

import * as api from '../api.js';
import * as catalogo from '../catalogo.js';
import * as respuestas from '../respuestas.js';
import {
  $, $$, esc, encabezado, token, vacio, cargando, toast, modal, cerrarModal,
  leerFormulario, alerta, activarTokens, fechaCorta, confirmar,
} from '../ui.js';

let contexto = {};

/* La consulta que se está armando. Vive en el módulo para que cambiar de
   pestaña y volver no la pierda. */
let definicion = {
  criterios: [],
  modo: 'estricto',
  panel_id: null,
  top_n: 200,
  top_k: 25,
  umbral_distancia: 0.55,
  estrategia_puente: null,
};

let ultimoResultado = null;
let nombresResueltos = {};   // id_persona → datos de contacto, si se pidieron

/* R3.14 — las dimensiones ya no son una lista escrita acá: salen del
   catálogo de atributos, que administra un admin desde Configuración. Un
   segmentador nuevo aparece en este desplegable sin tocar una línea. */
let catalogoDeAtributos = [];

/* R7.4 — qué columnas demográficas se muestran en el ranking.

   `columnasElegidas` se recuerda por usuario en su ficha, así que
   sobrevive a recargar y a cambiar de consulta. `valoresDeColumnas` es el
   caché de esta tanda de resultados: agregar una columna **no re-ejecuta
   la consulta**, resuelve los atributos sobre los `id_persona` que ya
   están en pantalla, y de a todos en una sola llamada. De a uno, una lista
   de 200 dispara 200 consultas, y con la bóveda en un tier chico eso se
   nota. */
let columnasOfrecidas = [];
let columnasElegidas = [];
let valoresDeColumnas = {};

const OPERADORES = {
  eq: 'es', ne: 'no es', in: 'es alguno de', not_in: 'no es ninguno de',
  contiene: 'contiene', lt: 'menor que', lte: 'menor o igual que',
  gt: 'mayor que', gte: 'mayor o igual que',
};

const VEREDICTOS = {
  cumple: { etiqueta: 'Cumple', clase: 'est-vigente' },
  no_cumple: { etiqueta: 'No cumple', clase: 'est-retirado' },
  dudoso: { etiqueta: 'Dudoso', clase: 'est-pendiente' },
  sin_evidencia: { etiqueta: 'Sin evidencia', clase: 'est-inactivo' },
  /* No es un juicio: el verificador no llegó a leer la evidencia (un lote
     truncado, la API caída, el tiempo agotado). No excluye ni aprueba. */
  sin_verificar: { etiqueta: 'Sin verificar', clase: 'est-inactivo' },
};

/* Por qué una evidencia quedó sin verificar, en palabras. */
const FALLOS = {
  truncamiento: 'la respuesta del verificador se cortó',
  herramienta_ausente: 'el verificador no devolvió veredictos',
  respuesta_invalida: 'la respuesta del verificador no se pudo leer',
  error_http: 'la API del verificador devolvió un error',
  error_red: 'no se pudo llegar a la API del verificador',
  omitido: 'el verificador no se pronunció',
  presupuesto_agotado: 'se terminó el tiempo de verificación',
  sin_proveedor: 'no hay verificador configurado',
};

const ETAPAS = {
  segmento_boveda: 'Segmento en la bóveda',
  ids_del_segmento: 'Ids del segmento',
  embedding: 'Embedding del criterio',
  recall: 'Recuperación (ANN)',
  gate_consentimiento: 'Gate de consentimiento',
  reranking: 'Reranking',
  colapso: 'Colapso a individuo',
  verificacion: 'Verificación',
  combinacion: 'Combinación de criterios',
  consulta_demografica: 'Consulta demográfica',
};

/* ── Render ─────────────────────────────────────────────────────── */

export async function render(main, ctx) {
  contexto = ctx;
  const [{ items: paneles }, guardadas, atributosDelCatalogo] = await Promise.all([
    api.paneles.listar(),
    api.consultas.guardadas().catch(() => ({ items: [] })),
    catalogo.cargar(),
  ]);
  catalogoDeAtributos = atributosDelCatalogo;
  // R7.4 — qué columnas se pueden elegir y cuáles tenía elegidas este
  // usuario la última vez. No bloquea la pantalla: si falla, la lista se
  // muestra como siempre.
  await cargarColumnas();

  main.innerHTML = encabezado('Consulta', 'semántica',
    'Quiénes se aproximan a un criterio, con la respuesta que lo justifica.') + `
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">Criterios</span>
        <span class="small muted">Los demográficos filtran; los semánticos ordenan.</span>
      </div>
      <div class="card-body">
        <div id="criterios"></div>
        <div class="toolbar" style="margin-top:.75rem">
          <button class="btn btn-outline btn-sm" id="agregar-semantico">+ Criterio semántico</button>
          <button class="btn btn-outline btn-sm" id="agregar-demografico">+ Criterio demográfico</button>
          <span class="toolbar-spacer"></span>
          <button class="btn btn-outline btn-sm" id="parametros">Parámetros</button>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-body">
        <div class="form-row">
          <div class="form-group">
            <label>Panel</label>
            <select class="fselect" id="panel">
              <option value="">Todos los paneles</option>
              ${paneles.map((p) => `<option value="${p.id}">${esc(p.nombre)}</option>`).join('')}
            </select>
          </div>
          <div class="form-group">
            <label>Modo</label>
            <select class="fselect" id="modo">
              <option value="estricto">Estricto — deja afuera a quien no cumple</option>
              <option value="laxo">Laxo — incluye penalizado a quien no tiene evidencia</option>
            </select>
            <div class="field-hint">
              En los dos modos, quien dice lo contrario del criterio queda afuera.
            </div>
          </div>
        </div>
        <div class="toolbar">
          <button class="btn btn-orange" id="correr">Consultar</button>
          <button class="btn btn-outline" id="guardar">Guardar consulta</button>
          ${guardadas.items?.length ? `<select class="fselect" id="cargar-guardada" style="max-width:260px">
            <option value="">Cargar una guardada…</option>
            ${guardadas.items.map((g) => `<option value="${g.id}">${esc(g.nombre)}</option>`).join('')}
          </select>` : ''}
        </div>
      </div>
    </div>

    <div id="resultado"></div>`;

  $('#panel').value = definicion.panel_id || '';
  $('#modo').value = definicion.modo;
  $('#panel').onchange = (e) => { definicion.panel_id = e.target.value ? Number(e.target.value) : null; };
  $('#modo').onchange = (e) => { definicion.modo = e.target.value; };
  $('#agregar-semantico').onclick = () => abrirCriterioSemantico();
  $('#agregar-demografico').onclick = () => abrirCriterioDemografico();
  $('#parametros').onclick = abrirParametros;
  $('#correr').onclick = correr;
  $('#guardar').onclick = abrirGuardar;
  if ($('#cargar-guardada')) $('#cargar-guardada').onchange = cargarGuardada;

  pintarCriterios();
  if (ultimoResultado) pintarResultado(ultimoResultado);
}

function pintarCriterios() {
  const caja = $('#criterios');
  if (!caja) return;
  if (!definicion.criterios.length) {
    caja.innerHTML = vacio(
      'Sin criterios todavía. Escribí una frase («gente a la que le gusta el fernet») '
      + 'o agregá un filtro demográfico.', '🔎');
    return;
  }
  caja.innerHTML = definicion.criterios.map((c, i) => {
    const cuerpo = c.tipo === 'semantico'
      ? `<span class="badge badge-on">semántico</span> <strong>${esc(c.texto)}</strong>
         ${c.duro ? '<span class="badge">duro</span>' : ''}
         <span class="small muted">peso ${c.peso ?? 1}</span>`
      : `<span class="badge badge-user">demográfico</span>
         <strong>${esc(catalogo.etiquetaDe(catalogoDeAtributos, c.dimension))}</strong>
         ${esc(OPERADORES[c.operador] || c.operador)}
         <strong>${esc(Array.isArray(c.valor) ? c.valor.join(', ') : c.valor)}</strong>`;
    return `<div class="fila-criterio">
      <div>${cuerpo}</div>
      <button class="btn btn-outline btn-sm btn-del" data-quitar="${i}">Quitar</button>
    </div>`;
  }).join('');
  $$('[data-quitar]', caja).forEach((b) => {
    b.onclick = () => { definicion.criterios.splice(Number(b.dataset.quitar), 1); pintarCriterios(); };
  });
}

/* ── Alta de criterios ──────────────────────────────────────────── */

function abrirCriterioSemantico() {
  modal({
    titulo: 'Criterio semántico',
    cuerpo: `
      <div class="form-group">
        <label>Escribilo como se lo contarías a alguien</label>
        <textarea class="finput" name="texto" rows="3"
          placeholder="gente a la que le gusta el fernet"></textarea>
        <div class="field-hint">
          Se vectoriza con el mismo modelo que la ingesta y se compara contra
          las respuestas de las encuestas.
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Peso</label>
          <input type="number" name="peso" value="1" min="0.1" step="0.1" />
          <div class="field-hint">Cuánto pesa en el puntaje combinado.</div>
        </div>
        <div class="form-group">
          <label>&nbsp;</label>
          <label style="font-weight:500; display:flex; gap:.5rem; align-items:center">
            <input type="checkbox" name="duro" /> Duro (filtra en vez de ordenar)
          </label>
          <div class="field-hint">
            Un criterio duro deja afuera a quien no lo cumple, incluso en modo laxo.
          </div>
        </div>
      </div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Agregar', clase: 'btn-orange', onClick: (caja) => {
          const datos = leerFormulario(caja);
          if (!datos.texto) { toast('Escribí el criterio.', 'err'); return; }
          definicion.criterios.push({
            tipo: 'semantico', texto: datos.texto,
            peso: Number(datos.peso) || 1, duro: !!datos.duro,
          });
          cerrarModal();
          pintarCriterios();
        } },
    ],
  });
}

function abrirCriterioDemografico() {
  const caja = modal({
    titulo: 'Criterio demográfico',
    cuerpo: `
      <div class="alert alert-info">
        Los atributos demográficos viven en la bóveda. Este filtro se resuelve
        ahí y nunca se copia al store semántico.
      </div>
      <div class="form-row">
        <div class="form-group"><label>Dimensión</label>
          <select class="fselect" name="dimension">
            ${catalogo.filtrables(catalogoDeAtributos).map((a) =>
              `<option value="${esc(a.clave)}">${esc(a.etiqueta)}${a.es_especial ? ' · especial' : ''}</option>`).join('')}
          </select></div>
        <div class="form-group"><label>Operador</label>
          <select class="fselect" name="operador">
            ${Object.entries(OPERADORES).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}
          </select></div>
      </div>
      <div class="form-group"><label>Valor</label>
        <input type="text" name="valor" placeholder="F" list="valores-dimension" />
        <datalist id="valores-dimension"></datalist>
        <div class="field-hint" id="hint-valor">
          Para «es alguno de», separá los valores con coma.
        </div></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Agregar', clase: 'btn-orange', onClick: (c) => {
          const datos = leerFormulario(c);
          if (!datos.valor) { toast('Falta el valor.', 'err'); return; }
          const lista = ['in', 'not_in'].includes(datos.operador);
          definicion.criterios.push({
            tipo: 'demografico', dimension: datos.dimension,
            operador: datos.operador,
            valor: lista ? datos.valor.split(',').map((v) => v.trim()).filter(Boolean)
                         : datos.valor,
          });
          cerrarModal();
          pintarCriterios();
        } },
    ],
  });
  /* R3.14 — las categorías admitidas salen del catálogo, así que el campo
     de valor se autocompleta con ellas. Con vocabulario canónico, tipear
     «Medio alto» donde la categoría es `medio_alto` deja el filtro en cero y
     nadie entiende por qué. */
  const hint = $('#hint-valor', caja);
  const lista$ = $('#valores-dimension', caja);
  const actualizarValores = (clave) => {
    const categorias = catalogo.categoriasDe(catalogoDeAtributos, clave);
    lista$.innerHTML = categorias.map(
      (c) => `<option value="${esc(c.clave)}">${esc(c.etiqueta)}</option>`).join('');
    hint.textContent = categorias.length
      ? `Categorías: ${categorias.map((c) => c.clave).join(', ')}. `
        + 'Para «es alguno de», separalas con coma.'
      : clave === 'edad'
        ? 'Un número de años.'
        : 'Para «es alguno de», separá los valores con coma.';
  };
  const dimension$ = $('[name="dimension"]', caja);
  dimension$.onchange = (e) => actualizarValores(e.target.value);
  actualizarValores(dimension$.value);
}

function abrirParametros() {
  modal({
    titulo: 'Parámetros de la consulta',
    ancho: '620px',
    cuerpo: `
      <div class="form-row">
        <div class="form-group"><label>Pool de recuperación (top_n)</label>
          <input type="number" name="top_n" value="${definicion.top_n}" min="1" max="2000" />
          <div class="field-hint">
            Cuántas <em>respuestas</em> trae el vecino más cercano antes de
            colapsar a personas. Más pool = más recall y más costo.
          </div></div>
        <div class="form-group"><label>A verificar (top_k)</label>
          <input type="number" name="top_k" value="${definicion.top_k}" min="1" max="200" />
          <div class="field-hint">
            Cuántas personas se mandan a verificar. Es el parámetro caro.
          </div></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Umbral de confianza (distancia)</label>
          <input type="number" name="umbral_distancia" step="0.05" min="0" max="2"
                 value="${definicion.umbral_distancia}" />
          <div class="field-hint">
            Por encima de esta distancia el resultado se marca de confianza
            baja. No excluye a nadie: lo señala.
          </div></div>
        <div class="form-group"><label>Estrategia de puente</label>
          <select class="fselect" name="estrategia_puente">
            <option value="">Automática (por selectividad)</option>
            <option value="demografico_primero">Demográfico primero</option>
            <option value="semantico_primero">Semántico primero</option>
          </select>
          <div class="field-hint">
            Cuál de los dos stores filtra primero. En automático lo decide el
            tamaño del segmento.
          </div></div>
      </div>`,
    acciones: [
      { texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Guardar', clase: 'btn-dark', onClick: (caja) => {
          const datos = leerFormulario(caja);
          definicion.top_n = Number(datos.top_n) || 200;
          definicion.top_k = Number(datos.top_k) || 25;
          definicion.umbral_distancia = datos.umbral_distancia === null
            ? 0.55 : Number(datos.umbral_distancia);
          definicion.estrategia_puente = datos.estrategia_puente || null;
          cerrarModal();
          toast('Parámetros actualizados.', 'ok');
        } },
    ],
  });
  const caja = $('.modal-box');
  $('[name="estrategia_puente"]', caja).value = definicion.estrategia_puente || '';
}

/* ── Correr ─────────────────────────────────────────────────────── */

async function correr() {
  if (!definicion.criterios.length) {
    toast('Agregá al menos un criterio.', 'err');
    return;
  }
  const boton = $('#correr');
  boton.disabled = true;
  boton.textContent = 'Consultando…';
  $('#resultado').innerHTML = cargando();
  nombresResueltos = {};
  try {
    ultimoResultado = await api.consultas.correr(definicion);
    // R7.4 — los atributos de las columnas elegidas se resuelven sobre el
    // conjunto recién obtenido, en una sola llamada y antes de pintar.
    await resolverColumnas(ultimoResultado);
    pintarResultado(ultimoResultado);
  } catch (error) {
    $('#resultado').innerHTML = alerta(error.message);
  } finally {
    boton.disabled = false;
    boton.textContent = 'Consultar';
  }
}

/* ── Resultado ──────────────────────────────────────────────────── */

function pintarResultado(resultado) {
  const caja = $('#resultado');
  if (!caja) return;

  if (resultado.tipo === 'demografica') {
    caja.innerHTML = pintarDemografica(resultado);
    activarTokens(caja);
    return;
  }

  caja.innerHTML = `
    ${pintarVerificacionIncompleta(resultado)}
    ${pintarDegradaciones(resultado)}
    ${pintarPuente(resultado)}
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">Ranking — ${resultado.total} persona(s)</span>
        <div class="toolbar">
          <button class="btn btn-outline btn-sm" id="ver-nombres">Ver quiénes son</button>
          <button class="btn btn-outline btn-sm" id="bajar-csv">Descargar CSV</button>
          <button class="btn btn-outline btn-sm" id="bajar-csv-pii"
            ${hayReidentificacion(resultado) ? '' : 'disabled'}
            title="${hayReidentificacion(resultado)
              ? 'CSV con nombre, documento y contacto. Queda registrado.'
              : 'Primero hay que reidentificar: exportar con datos no puede ser un segundo camino para sacar PII.'}"
            >CSV con datos</button>
          <button class="btn btn-outline btn-sm" id="elegir-columnas">Columnas</button>
          <button class="btn btn-outline btn-sm" id="crear-panel">Crear panel</button>
        </div>
      </div>
      <div class="card-body tight">
        ${resultado.items.length ? `<div class="table-wrap"><table>
          <thead><tr>
            <th>#</th><th>Persona</th>
            ${columnasElegidas.map((c) => `<th>${esc(etiquetaDeColumna(c))}</th>`).join('')}
            <th>Puntaje</th><th>Confianza</th>
            <th>Criterios</th><th>Evidencia</th>
          </tr></thead>
          <tbody>${resultado.items.map((item, i) => filaItem(item, i)).join('')}</tbody>
        </table></div>` : vacio(
          'Nadie quedó en el ranking. Probá el modo laxo, subí el pool o '
          + 'revisá los criterios.', '🕳️')}
      </div>
    </div>
    ${pintarExcluidos(resultado)}
    ${pintarDiagnostico(resultado)}`;

  activarTokens(caja);
  $('#ver-nombres').onclick = verNombres;
  $('#bajar-csv').onclick = bajarCsv;
  $('#bajar-csv-pii').onclick = bajarCsvIdentificado;
  $('#crear-panel').onclick = crearPanelDesdeConsulta;
  $('#elegir-columnas').onclick = abrirElegirColumnas;
  $$('[data-detalle]', caja).forEach((b) => {
    b.onclick = () => abrirDetalle(resultado.items[Number(b.dataset.detalle)]);
  });
  // R7.3 — la ficha se abre sin perder el resultado: es un modal encima de
  // la lista, y cerrarlo no vuelve a consultar nada.
  $$('[data-ficha]', caja).forEach((b) => {
    b.onclick = () => abrirFicha(resultado.items[Number(b.dataset.ficha)]);
  });
  const intercambio = $('#ver-intercambio');
  if (intercambio) intercambio.onclick = () => verIntercambio(intercambio.dataset.ejecucion);
}

function filaItem(item, indice) {
  const nombre = nombresResueltos[item.id_persona]?.nombre;
  const mejor = item.evidencias[0];
  return `<tr>
    <td class="mono">${indice + 1}</td>
    <td>${nombre ? `<div class="td-strong">${esc(nombre)}</div>` : ''}
        ${token(item.id_persona)}
        <button class="btn btn-outline btn-sm" data-ficha="${indice}"
                style="margin-top:.35rem">Ficha</button></td>
    ${columnasElegidas.map((c) => `<td>${celdaDeColumna(item.id_persona, c)}</td>`).join('')}
    <td><div class="barra ${item.puntaje >= 0.7 ? 'ok' : ''}">
          <span style="width:${Math.round(item.puntaje * 100)}%"></span></div>
        <div class="small mono">${item.puntaje.toFixed(3)}</div></td>
    <td><span class="est est-${item.confianza === 'alta' ? 'vigente' : 'pendiente'}">
          ${item.confianza === 'alta' ? 'alta' : 'baja'}</span>
        ${item.penalizado ? '<div class="small muted">penalizado</div>' : ''}
        ${item.verificacion_incompleta
          ? '<div class="small" style="color:var(--warn,#b45309)" title="Alguna de sus evidencias quedó sin verificar: no es un resultado completo.">verificación incompleta</div>'
          : ''}</td>
    <td>${item.criterios.map((c) => {
          const v = VEREDICTOS[c.veredicto] || { etiqueta: c.veredicto, clase: '' };
          return `<div class="small"><span class="est ${v.clase}">${esc(v.etiqueta)}</span>
                  ${esc(c.criterio)}</div>`;
        }).join('')}</td>
    <td>${mejor ? `<div class="small">${esc((mejor.valor_texto || '').slice(0, 90))}</div>
          <div class="small muted">${esc(mejor.estudio || '')} · ${esc(mejor.pregunta_codigo || '')}</div>`
        : '<span class="muted">—</span>'}
        <button class="btn btn-outline btn-sm" data-detalle="${indice}"
                style="margin-top:.35rem">Ver</button></td>
  </tr>`;
}

/* ── R7.4 · Las columnas de la lista ───────────────────────────── */

const etiquetaDeColumna = (clave) =>
  (columnasOfrecidas.find((c) => c.clave === clave) || {}).etiqueta || clave;

/* «Sin dato» y no una celda vacía ni una categoría: que a alguien le falte
   el atributo es información, y confundirlo con un valor es peor que no
   mostrarlo. El backend devuelve el atributo **ausente** del objeto —no una
   cadena vacía— justamente para que acá no se pueda confundir. */
function celdaDeColumna(idPersona, clave) {
  const valor = (valoresDeColumnas[idPersona] || {})[clave];
  const texto = valor && (valor.etiqueta_valor || valor.valor
    || (valor.valor_num != null ? String(valor.valor_num) : null));
  if (!texto) return '<span class="muted small">sin dato</span>';
  return esc(texto);
}

async function cargarColumnas() {
  try {
    const [{ items }, preferencias] = await Promise.all([
      api.atributos.columnas(),
      api.preferenciasDeUsuario.ver().catch(() => ({ columnas_resultado: [] })),
    ]);
    columnasOfrecidas = items;
    // Solo las que siguen existiendo: un atributo que se dio de baja no
    // puede dejar una columna fantasma para siempre.
    const validas = new Set(items.map((c) => c.clave));
    columnasElegidas = (preferencias.columnas_resultado || [])
      .filter((c) => validas.has(c));
  } catch {
    columnasOfrecidas = [];
    columnasElegidas = [];
  }
}

/* Resuelve los atributos del conjunto que ya está en pantalla. No vuelve a
   consultar: los `id_persona` son los del resultado que se está mirando. */
async function resolverColumnas(resultado) {
  if (!columnasElegidas.length || !resultado?.items?.length) {
    valoresDeColumnas = {};
    return;
  }
  try {
    const { items } = await api.atributos.deResultados(
      resultado.items.map((i) => i.id_persona));
    valoresDeColumnas = items;
  } catch {
    valoresDeColumnas = {};
  }
}

function abrirElegirColumnas() {
  if (!columnasOfrecidas.length) {
    toast('No hay atributos del catálogo para mostrar como columna.', 'warn');
    return;
  }
  const caja = modal({
    titulo: 'Columnas de la lista',
    cuerpo: `
      <p class="small">Se agregan sobre el resultado que ya está en pantalla:
      elegir columnas <strong>no vuelve a correr la consulta</strong>.</p>
      <div class="finalidades">
        ${columnasOfrecidas.map((c) => `
          <label class="finalidad">
            <input type="checkbox" value="${esc(c.clave)}"
                   ${columnasElegidas.includes(c.clave) ? 'checked' : ''} />
            <span><span class="f-titulo">${esc(c.etiqueta)}</span>
                  <span class="f-desc">${esc(c.clave)}</span></span>
          </label>`).join('')}
      </div>
      <div class="field-hint">Los atributos marcados como categoría especial
      no se ofrecen: verlos de a uno en una ficha no es lo mismo que verlos
      en una planilla de doscientas filas.</div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Aplicar', clase: 'btn-orange', onClick: async (c) => {
        columnasElegidas = $$('input[type=checkbox]', c)
          .filter((i) => i.checked).map((i) => i.value);
        cerrarModal();
        try { await api.preferenciasDeUsuario.guardar(columnasElegidas); }
        catch { toast('No se pudo recordar la selección.', 'warn'); }
        await resolverColumnas(ultimoResultado);
        pintarResultado(ultimoResultado);
      } },
    ],
  });
  return caja;
}

/* ── R7.3 y R7.6 · La ficha desde un resultado ─────────────────── */

/* Sin salir de la pantalla y sin perder el resultado: es un modal encima de
   la lista, y cerrarlo no vuelve a consultar nada —`ultimoResultado` no se
   toca—. Y **sin nombre, documento, correo ni celular**: la consulta
   devuelve un conjunto seudonimizado a propósito, y reidentificar es un
   acto deliberado que queda registrado. Si la ficha mostrara el nombre con
   un clic, esa auditoría dejaría de reflejar quién vio los datos de quién.

   La evidencia viaja **desde el resultado**: cada individuo del ranking ya
   trae los `respuesta_id` que lo justificaron, y la ficha los pasa al
   servidor, que devuelve el texto guardado y solo si es de esta persona.
   Recalcularla con el criterio daría otra cosa que lo que el analista tiene
   en pantalla, y la ficha tiene que explicar *este* resultado. */
async function abrirFicha(item) {
  const idPersona = item.id_persona;
  const idsEvidencia = (item.evidencias || [])
    .map((e) => e.respuesta_id).filter((i) => i != null);
  const caja = modal({
    titulo: 'Ficha del panelista',
    ancho: '820px',
    cuerpo: cargando('20vh'),
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
  try {
    const [ficha, estudios] = await Promise.all([
      api.panelistas.fichaSeudonima(idPersona, idsEvidencia.length
        ? { respuestas: idsEvidencia.join(',') } : {}),
      respuestas.estudiosDe(idPersona),
    ]);
    $('.modal-body', caja).innerHTML = fichaHtml(ficha, item, estudios);
    activarTokens(caja);
    respuestas.activar(caja, idPersona, estudios, { prefijo: 'ficha-resp' });
    const quien = $('#ficha-quien', caja);
    if (quien) quien.onclick = () => verQuienEs(item, caja);
  } catch (error) {
    $('.modal-body', caja).innerHTML = alerta(error.message);
  }
}

/* Para un derivado, de dónde sale el dato; para el resto, quién lo cargó.
   Es lo que dice cuánto confiar en el valor: una edad derivada de la fecha
   de nacimiento no es lo mismo que una envejecida desde lo que la persona
   declaró hace tres años. */
const PROCEDENCIAS = {
  derivado: 'derivado de la fecha de nacimiento',
  envejecido: 'envejecido desde la edad declarada',
  cargado: 'cargado tal cual',
};
const ORIGENES = {
  alta: 'del alta manual', edicion: 'editado a mano',
  ingesta: 'de una importación', carga: 'de una carga sin panel',
  inscripcion: 'de la inscripción', panelista: 'corregido por el panelista',
};

const valorDeAtributo = (a) =>
  a.etiqueta_valor || a.valor || (a.valor_num != null ? String(a.valor_num) : null)
  || (a.valor_fecha ? fechaCorta(a.valor_fecha) : null);

function fichaHtml(ficha, item, estudios) {
  const resuelto = nombresResueltos[ficha.id_persona];
  return `
    <p>${token(ficha.id_persona)}
       <span class="small muted">enrolada el ${esc(fechaCorta(ficha.enrolado_en))}</span></p>
    ${resuelto ? `<div class="alert alert-info">Reidentificada en esta sesión:
        <strong>${esc(resuelto.nombre || '—')}</strong>. Quedó registrado.</div>`
      : `<div class="aviso">
      <p>Esta ficha <strong>no muestra nombre, documento, correo ni
      celular</strong>. Ver quién es es una reidentificación: pide
      confirmación y queda registrada con tu usuario y el motivo.</p>
      <div class="toolbar" style="margin-top:.5rem">
        <button class="btn btn-outline btn-sm" id="ficha-quien">Ver quién es</button>
      </div>
    </div>`}

    <h4 class="ficha-titulo">Por qué aparece en este resultado</h4>
    ${evidenciaHtml(ficha.evidencia, item)}

    <h4 class="ficha-titulo">Atributos demográficos</h4>
    ${ficha.atributos.length ? `<div class="table-wrap"><table class="tabla">
      <thead><tr><th>Atributo</th><th>Valor</th><th>Procedencia</th></tr></thead>
      <tbody>${ficha.atributos.map((a) => `
        <tr><td>${esc(a.etiqueta)}${a.es_especial
              ? ' <span class="badge">especial</span>' : ''}</td>
            <td>${valorDeAtributo(a) ? esc(valorDeAtributo(a))
              : '<span class="muted small">sin dato</span>'}</td>
            <td class="small muted">${esc(PROCEDENCIAS[a.procedencia]
              || ORIGENES[a.origen] || a.procedencia || a.origen || '')}</td>
        </tr>`).join('')}</tbody>
    </table></div>` : '<p class="small muted">Sin atributos cargados.</p>'}
    <p class="field-hint">Sexo, localidad y tramo etario juntos pueden señalar
      a una persona en un panel chico: la ficha no es reidentificación, pero
      tampoco es anónima.</p>

    <h4 class="ficha-titulo">Paneles</h4>
    <p>${ficha.paneles.length
      ? ficha.paneles.map((p) => esc(p.nombre)).join(' · ')
      : '<span class="small muted">No integra ningún panel.</span>'}</p>

    <h4 class="ficha-titulo">Respuestas procesadas</h4>
    ${respuestas.seccionHtml(estudios, { prefijo: 'ficha-resp' })}`;
}

/* La evidencia con el criterio que justificó y su veredicto. El texto sale
   del servidor; el veredicto, del resultado que se está mirando. */
function evidenciaHtml(evidencia, item) {
  const items = evidencia?.items || [];
  if (!items.length) {
    return `<p class="small muted">${(item.evidencias || []).length
      ? 'No se pudo leer la evidencia de este resultado.'
      : 'El resultado no trae evidencia semántica para esta persona (por '
        + 'ejemplo, en modo laxo sin respuesta que cumpla el criterio).'}</p>`;
  }
  const criterioDe = (respuestaId) => (item.criterios || []).find(
    (c) => c.evidencia && c.evidencia.respuesta_id === respuestaId);
  return items.map((e) => {
    const c = criterioDe(e.respuesta_id);
    const v = c ? (VEREDICTOS[c.veredicto] || { etiqueta: c.veredicto, clase: '' }) : null;
    return `<div class="evidencia" style="margin-bottom:.6rem">
      ${c ? `<div class="small" style="margin-bottom:.3rem">
          <span class="est ${v.clase}">${esc(v.etiqueta)}</span>
          criterio <strong>${esc(c.criterio)}</strong></div>` : ''}
      <div class="procedencia">${esc(e.estudio || '')} · ${esc(e.codigo || '')}${
        e.fecha_campo ? ` · ${esc(fechaCorta(e.fecha_campo))}` : ''}</div>
      <div class="small muted" style="margin-bottom:.3rem">${esc(e.pregunta || '')}</div>
      <div>«${esc(e.respuesta || '')}»</div>
      <div class="small muted mono embebido">${esc(e.texto_embebido || '')}</div>
    </div>`;
  }).join('') + (evidencia.descartadas
    ? `<p class="small muted">${evidencia.descartadas} evidencia(s) no
       corresponden a esta persona y no se muestran.</p>` : '');
}

/* La reidentificación de siempre, para una sola persona. No es un camino
   nuevo: es el mismo `POST /reidentificacion`, con el mismo motivo que
   «Ver quiénes son», y queda registrada igual. */
async function verQuienEs(item, caja) {
  const ok = window.confirm(
    'Ver quién es deshace la seudonimización de esta persona. Queda '
    + 'registrado con tu usuario, la fecha y el motivo. ¿Seguir?');
  if (!ok) return;
  try {
    const resuelto = await api.reidentificacion.resolver([item.id_persona], 'consulta');
    resuelto.items.forEach((p) => { nombresResueltos[p.id_persona] = p; });
    const persona = nombresResueltos[item.id_persona];
    const destino = $('#ficha-quien', caja)?.closest('.aviso');
    if (destino && persona) {
      destino.outerHTML = `<div class="alert alert-info">Reidentificada:
        <strong>${esc(persona.nombre || '—')}</strong>. Quedó registrado.</div>`;
    }
    // La lista de abajo también la muestra, como después de «Ver quiénes son».
    pintarResultado(ultimoResultado);
    toast('Reidentificada. Queda registrado.', 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}

function abrirDetalle(item) {
  const nombre = nombresResueltos[item.id_persona]?.nombre;
  modal({
    titulo: nombre || 'Persona del ranking',
    ancho: '760px',
    cuerpo: `
      <dl class="kv">
        <dt>Identificador</dt><dd>${token(item.id_persona)}</dd>
        <dt>Puntaje combinado</dt><dd class="mono">${item.puntaje.toFixed(4)}</dd>
        <dt>Confianza</dt><dd>${item.confianza}${item.mejor_distancia != null
          ? ` <span class="small muted">(mejor distancia ${item.mejor_distancia})</span>` : ''}</dd>
      </dl>
      <h4 style="margin:1.25rem 0 .6rem">Criterio por criterio</h4>
      ${item.criterios.map((c) => {
        const v = VEREDICTOS[c.veredicto] || { etiqueta: c.veredicto, clase: '' };
        return `<div class="card" style="margin-bottom:.7rem"><div class="card-body">
          <div class="fila-criterio">
            <div><strong>${esc(c.criterio)}</strong>
              <span class="badge">${esc(c.tipo)}</span></div>
            <span class="est ${v.clase}">${esc(v.etiqueta)}</span>
          </div>
          <div class="small muted" style="margin:.4rem 0 .2rem">${esc(c.razon || '')}</div>
          <div class="small mono muted">puntaje ${c.puntaje} · peso ${c.peso}${
            c.distancia != null ? ` · distancia ${c.distancia}` : ''}${
            c.relevancia != null ? ` · relevancia ${c.relevancia}` : ''}</div>
          ${c.aviso_polaridad ? `<div class="alert alert-warn" style="margin-top:.6rem">
            El verificador dijo que cumple, pero la evidencia tiene marcas de
            negación. Vale la pena leerla.</div>` : ''}
          ${c.evidencia ? `<div class="evidencia" style="margin-top:.6rem">
            <div class="procedencia">${esc(c.evidencia.estudio || '')} ·
              ${esc(c.evidencia.pregunta_codigo || '')}${
                c.evidencia.fecha_campo ? ` · ${fechaCorta(c.evidencia.fecha_campo)}` : ''}</div>
            <div class="small muted" style="margin-bottom:.3rem">
              ${esc(c.evidencia.pregunta_texto || '')}</div>
            <div>«${esc(c.evidencia.valor_texto || '')}»</div>
          </div>` : ''}
        </div></div>`;
      }).join('')}`,
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
}

function pintarPuente(resultado) {
  const puente = resultado.puente || {};
  return `<div class="card"><div class="card-body">
    <dl class="kv">
      <dt>Puente entre stores</dt>
      <dd><strong>${esc(puente.estrategia || 'no aplica')}</strong>
        <div class="small muted" style="font-weight:500">${esc(puente.motivo || '')}</div>
        <div class="small muted" style="font-weight:500">
          Gate aplicado: <code>${esc(puente.gate || '—')}</code>
          ${puente.personas_en_segmento != null
            ? ` · ${puente.personas_en_segmento} persona(s) habilitadas en el segmento` : ''}
        </div>
      </dd>
    </dl>
  </div></div>`;
}

function pintarDegradaciones(resultado) {
  if (!resultado.degradaciones?.length) return '';
  return resultado.degradaciones.map((d) => `
    <div class="alert alert-warn">
      <strong>${esc(d.etapa)} ${d.parcial ? 'incompleta' : 'degradada'}</strong>
      (${esc(d.proveedor)}${d.criterio ? ` · ${esc(d.criterio)}` : ''}).
      ${esc(d.motivo)} — ${esc(d.consecuencia)}
    </div>`).join('');
}

/* R-VER.6/7 — un ranking parcialmente sin verificar no es un resultado
   completo, y tiene que verse arriba, no solo en el diagnóstico. */
function pintarVerificacionIncompleta(resultado) {
  const v = resultado.verificacion;
  if (!v || v.completa || !v.evidencias) return '';
  const motivos = Object.entries(v.por_fallo || {})
    .map(([fallo, n]) => `${n} porque ${FALLOS[fallo] || fallo}`).join('; ');
  return `<div class="alert alert-error">
      <strong>Verificación incompleta.</strong>
      ${v.sin_verificar} de ${v.evidencias} evidencia(s) quedaron sin verificar
      (${esc(motivos)}). ${v.personas_con_pendientes} persona(s) del ranking
      tienen evidencias pendientes y figuran con confianza baja: no las des por
      validadas. Las evidencias sin verificar no excluyen a nadie ni cuentan
      como cumplimiento.
      ${v.presupuesto_agotado ? ' Se agotó el tiempo de verificación: probá con un top-k más chico.' : ''}
    </div>`;
}

function pintarExcluidos(resultado) {
  if (!resultado.excluidos?.length) return '';
  return `<div class="card">
    <div class="card-header">
      <span class="card-header-title">Excluidos — ${resultado.excluidos.length}</span>
      <span class="small muted">Por qué no están en el ranking</span>
    </div>
    <div class="card-body tight"><div class="table-wrap"><table>
      <thead><tr><th>Persona</th><th>Motivo</th><th>Criterio</th><th>Evidencia</th></tr></thead>
      <tbody>${resultado.excluidos.map((e) => {
        const v = VEREDICTOS[e.motivo] || { etiqueta: e.motivo, clase: '' };
        return `<tr>
          <td>${token(e.id_persona)}</td>
          <td><span class="est ${v.clase}">${esc(v.etiqueta)}</span></td>
          <td class="small">${esc(e.criterio || '')}</td>
          <td class="small">${esc(e.evidencia || '—')}</td>
        </tr>`;
      }).join('')}</tbody></table></div></div>
  </div>`;
}

function pintarDiagnostico(resultado) {
  const d = resultado.diagnostico || {};
  return `<div class="card">
    <div class="card-header">
      <span class="card-header-title">Diagnóstico</span>
      <span class="small muted">${d.ms_total} ms en total</span>
    </div>
    <div class="card-body tight">
      <div class="small muted" style="padding:0 0 .5rem">
        Reranker: <code>${esc(d.reranker || '—')}</code> ·
        Verificador: <code>${esc(d.verificador || '—')}</code> ·
        Store semántico abierto: ${d.abrio_semantica ? 'sí' : 'no'}
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Etapa</th><th>ms</th><th>Detalle</th></tr></thead>
        <tbody>${(d.etapas || []).map((e) => `<tr>
          <td>${esc(ETAPAS[e.etapa] || e.etapa)}</td>
          <td class="mono">${e.ms}</td>
          <td class="small muted">${esc(Object.entries(e)
              .filter(([k]) => !['etapa', 'ms'].includes(k))
              .map(([k, v]) => `${k}=${v}`).join(' · '))}</td>
        </tr>`).join('')}</tbody></table></div>
      ${pintarDiagnosticoVerificacion(d)}
    </div>
  </div>`;
}

/* R-VER.9/10 — cómo corrió la verificación (lotes, subdivisiones, tokens)
   y, para un admin, el acceso al intercambio con Claude. */
function pintarDiagnosticoVerificacion(d) {
  const informes = d.verificacion || [];
  if (!informes.length) return '';
  const esAdmin = contexto.actor?.rol === 'admin';
  return `<div style="padding:.75rem 0 0">
      <div class="small td-strong" style="margin-bottom:.35rem">Verificación por lotes</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Criterio</th><th>Evidencias</th><th>Sin verificar</th>
          <th>Lotes</th><th>Llamadas</th><th>Subdivisiones</th><th>Tokens</th><th>ms</th></tr></thead>
        <tbody>${informes.map((i) => `<tr>
          <td class="small">${esc(i.criterio || '—')}</td>
          <td class="mono">${i.verificadas}/${i.evidencias}</td>
          <td class="mono">${i.sin_verificar}${Object.keys(i.por_fallo || {}).length
            ? ` <span class="small muted">(${esc(Object.entries(i.por_fallo)
              .map(([f, n]) => `${f}: ${n}`).join(', '))})</span>` : ''}</td>
          <td class="mono">${i.lotes_iniciales}${i.tam_lote ? ` × ${i.tam_lote}` : ''}</td>
          <td class="mono">${i.llamadas}${i.reintentos ? ` (${i.reintentos} reintento/s)` : ''}</td>
          <td class="mono">${i.subdivisiones}</td>
          <td class="mono small">${i.tokens ? `${i.tokens.entrada} → ${i.tokens.salida}` : '—'}</td>
          <td class="mono">${i.duracion_ms}</td>
        </tr>`).join('')}</tbody></table></div>
      ${esAdmin ? `<div class="toolbar" style="margin-top:.5rem">
          <button class="btn btn-outline btn-sm" id="ver-intercambio"
                  data-ejecucion="${esc(d.ejecucion_id || '')}">
            Ver el intercambio con Claude</button>
          <span class="small muted">${d.captura_depuracion
            ? 'El modo de depuración estaba encendido en esta consulta.'
            : 'El modo de depuración estaba apagado en esta consulta: no se guardó el intercambio.'}</span>
        </div>` : ''}
    </div>`;
}

/* R-VER.10 — solicitud y respuesta enfrentadas, por lote y por intento. */
async function verIntercambio(ejecucionId) {
  const d = ultimoResultado?.diagnostico || {};
  if (!d.captura_depuracion) {
    modal({
      titulo: 'Intercambio con Claude',
      cuerpo: `<div class="alert alert-info">El modo de depuración
        (<code>VERIFICACION_DEPURACION</code>) estaba <strong>apagado</strong>
        cuando corrió esta consulta, así que no se guardó lo que se mandó ni lo
        que volvió. No es que no haya habido intercambio. Para capturarlo, hay
        que encenderlo y volver a correr la consulta (y apagarlo después).</div>`,
      acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
    });
    return;
  }
  let salida;
  try {
    salida = await api.consultas.capturas(ejecucionId);
  } catch (error) { toast(error.message, 'err'); return; }
  const bonito = (texto, esJson) => {
    if (texto == null) return '(sin contenido: superó el tope de la ejecución)';
    if (!esJson) return texto;
    try { return JSON.stringify(JSON.parse(texto), null, 2); } catch { return texto; }
  };
  const marca = (truncada) => (truncada
    ? '<span class="est est-pendiente" title="Superó el tope de tamaño y se recortó">truncada</span>' : '');
  modal({
    titulo: 'Intercambio con Claude',
    ancho: '1100px',
    cuerpo: salida.capturas.length ? `
      <p class="small muted">Ejecución <code>${esc(salida.ejecucion_id)}</code>.
        Los <code>[n]</code> del mensaje son locales al lote: el índice global es
        «primera evidencia + n». Las capturas vencen a los
        ${salida.dias_de_retencion} días. Sin API key ni headers.</p>
      ${salida.capturas.map((c) => `
        <div class="card" style="margin-bottom:.75rem">
          <div class="card-header">
            <span class="card-header-title">${esc(c.criterio)} · lote ${esc(c.lote)}
              ${c.lote_padre ? `<span class="small muted">(de ${esc(c.lote_padre)})</span>` : ''}
              · intento ${c.intento}</span>
            <span class="small muted">evidencias ${c.primera_evidencia}–${c.primera_evidencia + c.evidencias - 1}
              · HTTP ${c.estado_http ?? '—'} · ${esc(c.resultado)} · ${c.duracion_ms ?? '—'} ms
              ${c.omitida_por_tope ? ' · <strong>omitida por tope</strong>' : ''}</span>
          </div>
          <div class="card-body" style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem">
            <div><div class="small td-strong">Solicitud ${marca(c.solicitud_truncada)}
                <span class="muted">${c.solicitud_bytes} B</span></div>
              <pre class="small" style="max-height:340px;overflow:auto;white-space:pre-wrap">${esc(bonito(c.solicitud, true))}</pre></div>
            <div><div class="small td-strong">Respuesta ${marca(c.respuesta_truncada)}
                <span class="muted">${c.respuesta_bytes} B${c.respuesta_es_json ? '' : ' · no es JSON'}</span></div>
              <pre class="small" style="max-height:340px;overflow:auto;white-space:pre-wrap">${esc(bonito(c.respuesta_api, c.respuesta_es_json))}</pre></div>
          </div>
        </div>`).join('')}`
      : `<div class="alert alert-info">${esc(salida.explicacion)}</div>`,
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
}

function pintarDemografica(resultado) {
  return `<div class="alert alert-info">
      Consulta puramente demográfica: se resolvió entera en la bóveda y no se
      abrió conexión al store semántico.
    </div>
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">${resultado.total} persona(s)</span>
      </div>
      <div class="card-body tight">
        ${resultado.items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>Persona</th><th>Sexo</th><th>Tramo</th><th>Localidad</th><th>Email</th></tr></thead>
          <tbody>${resultado.items.map((p) => `<tr>
            <td><div class="td-strong">${esc(p.nombre || '—')}</div>${token(p.id_persona)}</td>
            <td>${esc(p.sexo || '—')}</td>
            <td>${esc(p.tramo_etario || '—')}</td>
            <td>${esc(p.localidad || '—')}</td>
            <td class="small">${esc(p.email || '—')}</td>
          </tr>`).join('')}</tbody></table></div>` : vacio('El segmento está vacío.', '🕳️')}
      </div>
    </div>`;
}

/* ── Reidentificación y exportación ─────────────────────────────── */

async function verNombres() {
  if (!ultimoResultado?.items?.length) return;
  const ok = await confirmar({
    titulo: 'Ver quiénes son',
    textoOk: 'Ver los nombres',
    claseOk: 'btn-dark',
    cuerpo: `<p>El resultado de una consulta son identificadores opacos. Traducirlos
      a personas es lo que deshace la seudonimización, así que la operación
      <strong>queda registrada</strong> con tu usuario, la fecha y el motivo.</p>
      <p class="small muted">Se van a resolver ${ultimoResultado.items.length}
      identificador(es).</p>`,
  });
  if (!ok) return;
  try {
    const resuelto = await api.reidentificacion.resolver(
      ultimoResultado.items.map((i) => i.id_persona), 'consulta');
    resuelto.items.forEach((p) => { nombresResueltos[p.id_persona] = p; });
    pintarResultado(ultimoResultado);
    toast(`${resuelto.items.length} persona(s) resueltas. Queda registrado.`, 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}

async function bajarCsv() {
  try {
    const salida = await api.consultas.csv(definicion);
    const blob = new Blob([salida.csv], { type: 'text/csv;charset=utf-8' });
    const enlace = document.createElement('a');
    enlace.href = URL.createObjectURL(blob);
    enlace.download = salida.nombre_archivo || 'consulta.csv';
    enlace.click();
    URL.revokeObjectURL(enlace.href);
    toast(`${salida.filas} fila(s). El archivo identifica por id_persona, sin PII.`, 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}

/* R3.10 — el CSV con datos personales.

   Solo está disponible después de reidentificar, y no porque la interfaz sea
   prolija: si exportar pudiera resolver la PII por su cuenta, habría dos
   caminos para sacarla de la bóveda y uno solo quedaría auditado. Acá se
   manda el resultado ya resuelto, y el backend registra la exportación con
   su propio motivo, distinto de haberla mirado en pantalla. */

function hayReidentificacion(resultado) {
  return (resultado?.items || []).some((i) => nombresResueltos[i.id_persona]);
}

async function bajarCsvIdentificado() {
  const items = (ultimoResultado?.items || [])
    .map((i) => nombresResueltos[i.id_persona])
    .filter(Boolean);
  if (!items.length) {
    toast('Primero usá «Ver quiénes son»: la exportación con datos parte de esa resolución.', 'err');
    return;
  }
  const ok = await confirmar({
    titulo: 'Exportar con datos personales',
    textoOk: 'Descargar el archivo',
    claseOk: 'btn-dark',
    cuerpo: `<p>El archivo va a tener <strong>nombre, documento, correo y
      celular</strong> de ${items.length} persona(s). Llevárselo a un archivo
      no es lo mismo que verlo en pantalla, así que se registra como un evento
      aparte, con tu usuario y la fecha.</p>
      <p class="small muted">No incluye fecha de nacimiento exacta ni
      observaciones.</p>`,
  });
  if (!ok) return;
  try {
    const salida = await api.exportacion.csvIdentificado(
      { items, total: items.length, no_encontrados: [] }, ultimoResultado);
    const blob = new Blob([salida.csv], { type: 'text/csv;charset=utf-8' });
    const enlace = document.createElement('a');
    enlace.href = URL.createObjectURL(blob);
    enlace.download = salida.nombre_archivo;
    enlace.click();
    URL.revokeObjectURL(enlace.href);
    toast(`${salida.personas} persona(s). La exportación quedó registrada.`, 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}

/* R3.11 — el resultado como panel de trabajo.

   El panel es una foto: guarda la definición que lo originó para poder
   rastrear de dónde salió su composición, pero no se actualiza solo si la
   consulta cambia. */

async function crearPanelDesdeConsulta() {
  if (!ultimoResultado?.items?.length) {
    toast('El resultado está vacío: no hay panel que crear.', 'err');
    return;
  }
  modal({
    titulo: `Crear un panel con estas ${ultimoResultado.items.length} personas`,
    cuerpo: `
      <p class="small muted">Se va a dar de alta una membresía por cada
      individuo del resultado. Quien ya sea miembro no se duplica. El panel
      queda con la definición de esta consulta anotada, como una foto: no se
      actualiza solo.</p>
      <div class="form-group"><label>Nombre del panel</label>
        <input class="finput" name="nombre" placeholder="Tomadores de fernet — set A"></div>
      <div class="form-group"><label>Descripción</label>
        <textarea class="finput" name="descripcion" rows="2"></textarea></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn', onClick: cerrarModal },
      {
        texto: 'Crear el panel', clase: 'btn-primary',
        onClick: async (contenedor) => {
          const datos = leerFormulario(contenedor);
          if (!datos.nombre?.trim()) { toast('El panel necesita un nombre.', 'err'); return; }
          try {
            const panel = await api.panelDesdeConsulta(
              datos.nombre.trim(), ultimoResultado, definicion, datos.descripcion);
            cerrarModal();
            toast(`Panel «${panel.nombre}» creado con ${panel.miembros} miembros.`, 'ok');
            if (panel.aviso) toast(panel.aviso.mensaje, 'aviso');
          } catch (error) { toast(error.message, 'err'); }
        },
      },
    ],
  });
}

/* ── Consultas guardadas ────────────────────────────────────────── */

function abrirGuardar() {
  if (!definicion.criterios.length) {
    toast('Armá la consulta antes de guardarla.', 'err');
    return;
  }
  modal({
    titulo: 'Guardar consulta',
    cuerpo: `
      <div class="alert alert-info">
        Se guarda la <strong>definición</strong>, no el resultado: volver a
        correrla vuelve a pasar por el gate de consentimiento con el padrón de
        hoy.
      </div>
      <div class="form-group"><label>Nombre</label>
        <input type="text" name="nombre" placeholder="Fernet en Montevideo" /></div>
      <div class="form-group"><label>Para qué es</label>
        <textarea class="finput" name="descripcion" rows="2"></textarea></div>`,
    acciones: [
      { texto: 'Cancelar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Guardar', clase: 'btn-orange', onClick: async (caja) => {
          const datos = leerFormulario(caja);
          if (!datos.nombre) { toast('Ponele un nombre.', 'err'); return; }
          try {
            await api.consultas.guardar(datos.nombre, definicion, datos.descripcion);
            cerrarModal();
            toast('Consulta guardada.', 'ok');
            contexto.irA('consultas');
          } catch (error) {
            toast(error.message, 'err');
          }
        } },
    ],
  });
}

async function cargarGuardada(evento) {
  const id = evento.target.value;
  if (!id) return;
  try {
    const guardada = await api.consultas.verGuardada(id);
    definicion = { ...definicion, ...guardada.definicion };
    ultimoResultado = null;
    contexto.irA('consultas');
    toast(`Cargada «${guardada.nombre}».`, 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
}
