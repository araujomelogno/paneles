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
import { pintarOrigen } from '../origen.js';
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
  /* R-CS · cambio 2 — cuánto se verifica, aparte del modo. */
  alcance: 'exploratorio',
  panel_id: null,
  top_n: 200,
  top_k: 25,
  /* A6 — «Personas a mostrar». Antes no se mandaba y siempre era 50. */
  limite: 50,
  umbral_distancia: 0.55,
  estrategia_puente: null,
  /* A5 — null = lo que diga el entorno (por defecto `query`). */
  tipo_embedding_criterio: null,
};

/* R-CS · cambio 5 — la ejecución completa que se está mirando, si hay. */
let ejecucionAbierta = null;
let sondeo = null;
const TAM_LOTE_VERIFICACION = 25;

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
  /* R-CS · cambio 4 — se leyó y no habla del criterio: ni afirma ni
     contradice. Cuenta como ausencia de evidencia. */
  irrelevante: { etiqueta: 'Irrelevante', clase: 'est-inactivo' },
  sin_evidencia_pertinente: { etiqueta: 'Sin evidencia pertinente', clase: 'est-inactivo' },
};

/* R-CS — el estado de una persona en el resultado. Si el backend no lo
   manda (una versión vieja), se muestra «sin dato»: la ausencia del campo no
   es «confirmada» (A4). */
const ESTADOS = {
  confirmada: { etiqueta: 'Confirmada', clase: 'est-vigente',
    ayuda: 'Todos los criterios semánticos tienen evidencia que los cumple.' },
  posible: { etiqueta: 'Posible', clase: 'est-pendiente',
    ayuda: 'Entró por la tolerancia del modo laxo: duda o falta de evidencia en algún criterio.' },
  pendiente: { etiqueta: 'Pendiente', clase: 'est-inactivo',
    ayuda: 'Alguna de sus evidencias quedó sin verificar: no la des por validada.' },
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
  unidades: 'Unidades de evidencia',
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
          <div class="form-group">
            <label>Alcance</label>
            <select class="fselect" id="alcance">
              <option value="exploratorio">Exploratorio — verifica las mejores (en el momento)</option>
              <option value="completo">Completo — verifica todo lo elegible (con presupuesto)</option>
            </select>
            <div class="field-hint" id="ayuda-alcance"></div>
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

    <div id="ejecuciones"></div>
    <div id="resultado"></div>`;

  $('#panel').value = definicion.panel_id || '';
  $('#modo').value = definicion.modo;
  $('#alcance').value = definicion.alcance;
  $('#panel').onchange = (e) => { definicion.panel_id = e.target.value ? Number(e.target.value) : null; };
  $('#modo').onchange = (e) => { definicion.modo = e.target.value; };
  $('#alcance').onchange = (e) => { definicion.alcance = e.target.value; ayudaAlcance(); };
  ayudaAlcance();
  $('#agregar-semantico').onclick = () => abrirCriterioSemantico();
  $('#agregar-demografico').onclick = () => abrirCriterioDemografico();
  $('#parametros').onclick = abrirParametros;
  $('#correr').onclick = correr;
  $('#guardar').onclick = abrirGuardar;
  if ($('#cargar-guardada')) $('#cargar-guardada').onchange = cargarGuardada;

  pintarCriterios();
  pintarEjecuciones();
  if (ultimoResultado) pintarResultado(ultimoResultado);
  else if (ejecucionAbierta) seguirEjecucion(ejecucionAbierta);
}

/* R-CS · cambio 2 — qué implica cada alcance, en la pantalla. */
function ayudaAlcance() {
  const caja = $('#ayuda-alcance');
  const boton = $('#correr');
  if (!caja) return;
  if (definicion.alcance === 'completo') {
    caja.innerHTML = 'Verifica <strong>todas</strong> las respuestas elegibles, en '
      + 'segundo plano. Antes de lanzarla se muestra el costo estimado y se fija un '
      + 'presupuesto que la frena sola.';
    if (boton) boton.textContent = 'Estimar costo…';
  } else {
    caja.innerHTML = `Verifica las ${definicion.top_k} personas mejor rankeadas. Rápido y `
      + 'barato; no garantiza encontrar a todas.';
    if (boton) boton.textContent = 'Consultar';
  }
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

/* A6 — los cuatro parámetros, nombrados por lo que hacen. `limite` estaba
   en la API y no en el formulario: subir `top_k` verificaba (y pagaba) más
   personas y la pantalla seguía mostrando 50, así que parecía que el
   parámetro no hacía nada. */
const llamadasPorCriterio = (topK) => Math.max(1, Math.ceil(topK / TAM_LOTE_VERIFICACION));

function abrirParametros() {
  modal({
    titulo: 'Parámetros de la consulta',
    ancho: '680px',
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
            Cuántas personas se mandan a verificar. <strong>Es la palanca
            directa sobre el gasto</strong>: la verificación es la etapa más
            cara. De 25 a 200 la multiplica por ocho; con lotes de
            ${TAM_LOTE_VERIFICACION}, pasa de una llamada a Claude a ocho
            <em>por criterio</em>.
            <div id="costo-top-k" class="small" style="margin-top:.25rem"></div>
          </div></div>
      </div>
      <div class="form-row">
        <div class="form-group"><label>Personas a mostrar (limite)</label>
          <input type="number" name="limite" value="${definicion.limite}" min="1" max="200" />
          <div class="field-hint">
            Cuántas personas del ranking se muestran. En el alcance completo es
            el tamaño de página.
            <div id="aviso-limite" class="small" style="margin-top:.25rem;color:var(--warn,#b45309)"></div>
          </div></div>
        <div class="form-group"><label>Umbral de confianza (distancia)</label>
          <input type="number" name="umbral_distancia" step="0.05" min="0" max="2"
                 value="${definicion.umbral_distancia}" />
          <div class="field-hint">
            Por encima de esta distancia el resultado se marca de confianza
            baja. No excluye a nadie: lo señala.
          </div></div>
      </div>
      <div class="form-row">
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
        <div class="form-group"><label>Embedding del criterio</label>
          <select class="fselect" name="tipo_embedding_criterio">
            <option value="">Según la configuración (por defecto: consulta)</option>
            <option value="query">Como consulta (query)</option>
            <option value="document">Como documento (comportamiento anterior)</option>
          </select>
          <div class="field-hint">
            Cómo se vectoriza la frase del criterio. Para comparar con cómo
            daba antes; el diagnóstico dice cuál se usó.
          </div></div>
      </div>`,
    acciones: [
      { texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal },
      { texto: 'Guardar', clase: 'btn-dark', onClick: (caja) => {
          const datos = leerFormulario(caja);
          const topK = Number(datos.top_k) || 25;
          const limite = Number(datos.limite) || 50;
          if (definicion.alcance !== 'completo' && limite > topK) {
            toast(`No se pueden mostrar ${limite} personas si se verifican ${topK}: `
              + 'bajá «Personas a mostrar» o subí «A verificar».', 'err');
            return;
          }
          definicion.top_n = Number(datos.top_n) || 200;
          definicion.top_k = topK;
          definicion.limite = limite;
          definicion.umbral_distancia = datos.umbral_distancia === null
            ? 0.55 : Number(datos.umbral_distancia);
          definicion.estrategia_puente = datos.estrategia_puente || null;
          definicion.tipo_embedding_criterio = datos.tipo_embedding_criterio || null;
          cerrarModal();
          ayudaAlcance();
          toast('Parámetros actualizados.', 'ok');
        } },
    ],
  });
  const caja = $('.modal-box');
  $('[name="estrategia_puente"]', caja).value = definicion.estrategia_puente || '';
  $('[name="tipo_embedding_criterio"]', caja).value = definicion.tipo_embedding_criterio || '';
  /* A6 — el aviso va en el formulario, mientras se escribe, y no al recibir
     un resultado con menos personas de las pedidas. */
  const revisar = () => {
    const topK = Number($('[name="top_k"]', caja).value) || 0;
    const limite = Number($('[name="limite"]', caja).value) || 0;
    const llamadas = llamadasPorCriterio(topK);
    $('#costo-top-k', caja).textContent = topK
      ? `≈ ${llamadas} llamada(s) a Claude por criterio (hasta ${llamadas * 3} si cada `
        + 'persona aporta tres evidencias distintas).' : '';
    $('#aviso-limite', caja).textContent = definicion.alcance !== 'completo' && limite > topK
      ? `Se verifican ${topK} personas: como mucho vas a ver ${topK}. Pedir ${limite} `
        + 'no trae más gente, la recorta igual.' : '';
  };
  ['top_k', 'limite'].forEach((n) => { $(`[name="${n}"]`, caja).oninput = revisar; });
  revisar();
}

/* ── Correr ─────────────────────────────────────────────────────── */

async function correr() {
  if (!definicion.criterios.length) {
    toast('Agregá al menos un criterio.', 'err');
    return;
  }
  if (definicion.alcance === 'completo') {
    await abrirEstimacionCompleta();
    return;
  }
  const boton = $('#correr');
  boton.disabled = true;
  boton.textContent = 'Consultando…';
  $('#resultado').innerHTML = cargando();
  nombresResueltos = {};
  detenerSondeo();
  ejecucionAbierta = null;
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
    ayudaAlcance();
  }
}

/* ── R-CS · cambio 5 · La consulta completa ─────────────────────── */

const fmtUsd = (n) => (n == null ? '—' : `US$ ${Number(n).toFixed(n < 1 ? 4 : 2)}`);
const puedeLanzarCompleta = () => ['admin', 'operaciones'].includes(contexto.actor?.rol);

/* A2 — el costo **en la pantalla, antes de confirmar**. La estimación sale
   de la misma ruta que lanza, con `solo_estimar`. */
async function abrirEstimacionCompleta() {
  const caja = modal({
    titulo: 'Consulta completa — costo estimado',
    ancho: '720px',
    cuerpo: cargando('16vh'),
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
  let e;
  try {
    e = await api.consultas.estimar({ ...definicion, alcance: 'completo' });
  } catch (error) {
    $('.modal-body', caja).innerHTML = alerta(error.message);
    return;
  }
  const est = e.estimacion;
  const veces = e.exploratoria.usd ? Math.round(est.usd / e.exploratoria.usd) : null;
  $('.modal-body', caja).innerHTML = `
    <p>Se van a verificar <strong>${est.unidades.toLocaleString('es-UY')}</strong>
      unidad(es) de evidencia (textos distintos) de
      <strong>${e.personas_habilitadas.toLocaleString('es-UY')}</strong> persona(s)
      habilitadas, en ${e.lotes} lote(s) y ~${est.llamadas} llamada(s) a Claude.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Criterio</th><th>Unidades</th><th>Llamadas</th><th>Estimado</th></tr></thead>
      <tbody>${est.por_criterio.map((c) => `<tr>
        <td class="small">${esc(c.criterio)}</td><td class="mono">${c.unidades}</td>
        <td class="mono">${c.llamadas}</td><td class="mono">${fmtUsd(c.usd)}</td></tr>`).join('')}
      </tbody></table></div>
    <dl class="kv" style="margin-top:.75rem">
      <dt>Costo estimado</dt><dd><strong>${fmtUsd(est.usd)}</strong>
        <span class="small muted">(incluye ${Math.round((est.margen - 1) * 100)}% de margen
        por reintentos y subdivisiones)</span></dd>
      <dt>La exploratoria</dt><dd>${fmtUsd(e.exploratoria.usd)} como mucho${veces && veces > 1
        ? ` · la completa cuesta ~${veces} veces más` : ''}</dd>
      <dt>Supuestos</dt><dd class="small muted">${esc(est.supuestos)}</dd>
    </dl>
    ${e.excede_el_maximo ? `<div class="alert alert-error">La estimación supera el
      máximo por ejecución (${fmtUsd(e.presupuesto_maximo_usd)}). Acotá la consulta
      —panel, criterios demográficos— o usá la exploratoria.</div>` : ''}
    ${puedeLanzarCompleta() ? `
      <div class="form-row" style="margin-top:.5rem">
        <div class="form-group"><label>Presupuesto (US$)</label>
          <input type="number" name="presupuesto" step="0.01" min="0.01"
                 max="${e.presupuesto_maximo_usd}" value="${e.presupuesto_sugerido_usd}" />
          <div class="field-hint">Obligatorio. Si se alcanza, la ejecución se
            detiene sola y lo verificado se conserva. Máximo
            ${fmtUsd(e.presupuesto_maximo_usd)}.</div></div>
      </div>
      <label class="finalidad"><input type="checkbox" name="confirmo" />
        <span><span class="f-titulo">Entiendo el costo y quiero lanzarla.</span></span></label>`
    : `<div class="alert alert-info">Lanzar una consulta completa lo hace un
        responsable de operaciones o un admin: es un gasto que alguien tiene que
        aprobar. Podés pasarle esta estimación.</div>`}`;
  if (!puedeLanzarCompleta() || e.excede_el_maximo) return;
  const pie = $('.modal-foot', caja) || $('.modal-body', caja);
  const lanzar = document.createElement('button');
  lanzar.className = 'btn btn-orange';
  lanzar.textContent = 'Lanzar la consulta completa';
  lanzar.onclick = async () => {
    const presupuesto = Number($('[name="presupuesto"]', caja).value);
    if (!$('[name="confirmo"]', caja).checked) { toast('Confirmá el costo.', 'err'); return; }
    if (!(presupuesto > 0)) { toast('Fijá un presupuesto.', 'err'); return; }
    lanzar.disabled = true;
    try {
      const estado = await api.consultas.lanzarCompleta(definicion, presupuesto);
      cerrarModal();
      toast('Consulta completa encolada. Podés cerrar la pantalla: sigue sola.', 'ok');
      ultimoResultado = null;
      seguirEjecucion(estado.ejecucion_id);
      pintarEjecuciones();
    } catch (error) {
      lanzar.disabled = false;
      toast(error.message, 'err');
    }
  };
  pie.appendChild(lanzar);
}

function detenerSondeo() {
  if (sondeo) clearTimeout(sondeo);
  sondeo = null;
}

/* El avance sale de la base: sobrevive a recargar y a cambiar de pestaña. */
async function seguirEjecucion(id) {
  detenerSondeo();
  ejecucionAbierta = id;
  let estado;
  try {
    estado = await api.consultas.ejecucion(id, { solo_estado: 1 });
  } catch (error) {
    $('#resultado').innerHTML = alerta(error.message);
    return;
  }
  if (ejecucionAbierta !== id || !$('#resultado')) return;
  $('#resultado').innerHTML = progresoHtml(estado);
  engancharProgreso(estado);
  if (!estado.terminal) {
    sondeo = setTimeout(() => seguirEjecucion(id), 4000);
  } else {
    await abrirResultadoCompleto(id, 1, estado);
  }
}

function progresoHtml(e) {
  const gastado = e.presupuesto_usd
    ? Math.min(100, Math.round(100 * (e.costo_real_usd || 0) / e.presupuesto_usd)) : 0;
  return `<div class="card" id="progreso-completa"><div class="card-body">
    <div class="fila-criterio">
      <div><strong>Consulta completa</strong> <span class="small muted">${esc((e.criterios || []).join(' · '))}</span></div>
      <span class="est ${e.terminal ? (e.estado === 'terminada' ? 'est-vigente' : 'est-pendiente') : 'est-inactivo'}">${esc(e.estado_etiqueta)}</span>
    </div>
    <div class="small" style="margin:.5rem 0 .2rem">Avance: ${e.lotes_ok + e.lotes_fallidos + e.lotes_omitidos}
      de ${e.lotes_total} lote(s) · ${e.unidades_verificadas} de ${e.unidades_total} unidad(es)
      ${e.segundos_restantes != null ? ` · faltan ~${Math.ceil(e.segundos_restantes / 60)} min` : ''}</div>
    <div class="barra"><span style="width:${e.porcentaje}%"></span></div>
    <div class="small" style="margin:.5rem 0 .2rem">Gasto: ${fmtUsd(e.costo_real_usd)} de
      ${fmtUsd(e.presupuesto_usd)} (estimado ${fmtUsd(e.costo_estimado?.usd)})</div>
    <div class="barra ${gastado >= 90 ? '' : 'ok'}"><span style="width:${gastado}%"></span></div>
    ${e.problemas?.length ? `<div class="alert alert-warn" style="margin-top:.6rem">
      ${e.problemas.length} lote(s) con problemas: ${esc(e.problemas.slice(0, 3)
        .map((p) => `#${p.indice} ${p.estado}${p.error ? ` — ${p.error}` : ''}`).join(' · '))}</div>` : ''}
    <div class="toolbar" style="margin-top:.6rem">
      ${!e.terminal ? '<button class="btn btn-outline btn-sm" id="ver-parcial">Ver lo verificado hasta ahora</button>' : ''}
      ${!e.terminal && puedeLanzarCompleta() ? '<button class="btn btn-outline btn-sm" id="cancelar-completa">Cancelar</button>' : ''}
      ${e.terminal && puedeLanzarCompleta() && (e.lotes_fallidos || e.lotes_omitidos) ? `
        <button class="btn btn-outline btn-sm" id="reintentar-completa">${e.estado === 'detenida_por_presupuesto'
          ? 'Ampliar presupuesto y seguir' : 'Reintentar lotes fallidos'}</button>` : ''}
    </div>
  </div></div>`;
}

function engancharProgreso(e) {
  const parcial = $('#ver-parcial');
  if (parcial) parcial.onclick = () => abrirResultadoCompleto(e.ejecucion_id, 1, e);
  const cancelar = $('#cancelar-completa');
  if (cancelar) {
    cancelar.onclick = async () => {
      if (!window.confirm('¿Cancelar? Lo verificado se conserva; los lotes que no empezaron no se procesan.')) return;
      try { await api.consultas.cancelarEjecucion(e.ejecucion_id); seguirEjecucion(e.ejecucion_id); }
      catch (error) { toast(error.message, 'err'); }
    };
  }
  const reintentar = $('#reintentar-completa');
  if (reintentar) {
    reintentar.onclick = async () => {
      let presupuesto = null;
      if (e.estado === 'detenida_por_presupuesto') {
        const texto = window.prompt(`Nuevo presupuesto en US$ (hoy ${fmtUsd(e.presupuesto_usd)}):`);
        if (!texto) return;
        presupuesto = Number(texto.replace(',', '.'));
      }
      try {
        await api.consultas.reintentarEjecucion(e.ejecucion_id, presupuesto);
        toast('Lotes reencolados.', 'ok');
        seguirEjecucion(e.ejecucion_id);
      } catch (error) { toast(error.message, 'err'); }
    };
  }
}

async function abrirResultadoCompleto(id, pagina, estado) {
  detenerSondeo();
  ejecucionAbierta = id;
  try {
    ultimoResultado = await api.consultas.ejecucion(id, { pagina, por_pagina: definicion.limite });
    await resolverColumnas(ultimoResultado);
    pintarResultado(ultimoResultado);
    if (estado && !estado.terminal) {
      $('#resultado').insertAdjacentHTML('afterbegin', progresoHtml(estado));
      engancharProgreso(estado);
      sondeo = setTimeout(() => seguirEjecucion(id), 8000);
    }
  } catch (error) {
    $('#resultado').innerHTML = alerta(error.message);
  }
}

/* Las completas recientes: volver a la de ayer sin tener que guardar nada. */
async function pintarEjecuciones() {
  const caja = $('#ejecuciones');
  if (!caja) return;
  let items = [];
  try { ({ items } = await api.consultas.ejecuciones()); } catch { items = []; }
  if (!items.length) { caja.innerHTML = ''; return; }
  caja.innerHTML = `<div class="card">
    <div class="card-header"><span class="card-header-title">Consultas completas recientes</span>
      <span class="small muted">Corren en segundo plano; el resultado se arma al abrirlas, con el consentimiento de hoy.</span></div>
    <div class="card-body tight"><div class="table-wrap"><table>
      <thead><tr><th>Criterios</th><th>Estado</th><th>Avance</th><th>Gasto</th><th>Lanzada</th><th></th></tr></thead>
      <tbody>${items.slice(0, 8).map((e) => `<tr>
        <td class="small">${esc((e.criterios || []).join(' · '))}</td>
        <td><span class="est ${e.estado === 'terminada' ? 'est-vigente' : e.terminal ? 'est-pendiente' : 'est-inactivo'}">${esc(e.estado_etiqueta)}</span></td>
        <td class="mono">${e.porcentaje}%</td>
        <td class="mono small">${fmtUsd(e.costo_real_usd)} / ${fmtUsd(e.presupuesto_usd)}</td>
        <td class="small">${esc(fechaCorta(e.creado_en))}</td>
        <td><button class="btn btn-outline btn-sm" data-ejecucion="${esc(e.ejecucion_id)}">Abrir</button></td>
      </tr>`).join('')}</tbody></table></div></div></div>`;
  $$('[data-ejecucion]', caja).forEach((b) => {
    b.onclick = () => { ultimoResultado = null; seguirEjecucion(b.dataset.ejecucion); };
  });
}

/* ── Resultado ──────────────────────────────────────────────────── */

function pintarResultado(resultado) {
  const caja = $('#resultado');
  if (!caja) return;

  /* SC1 — la consulta demográfica salía acá por un `return` temprano y nunca
     llegaba a la barra de acciones: ficha, columnas, CSV, reidentificar y
     crear panel existían solo para la semántica, que es el camino menos
     transitado. Ahora los dos tipos pasan por la misma barra
     (`barraDeAcciones` + `engancharAcciones`); lo que cambia es la tabla. */
  const esDemografica = resultado.tipo === 'demografica';
  const esCompleta = resultado.alcance === 'completo';

  caja.innerHTML = `
    ${esDemografica ? avisoDemografica(resultado) : `
      ${pintarVerificacionIncompleta(resultado)}
      ${pintarDegradaciones(resultado)}
      ${esCompleta ? pintarResumenCompleta(resultado) : pintarPuente(resultado)}`}
    <div class="card">
      <div class="card-header">
        <span class="card-header-title">${esDemografica
          ? `${resultado.total} persona(s)${resultado.items.length < resultado.total
            ? ` <span class="small muted">· se muestran ${resultado.items.length}</span>` : ''}`
          : `Ranking — ${resultado.total} persona(s)${pintarRecorte(resultado)}`}</span>
        ${barraDeAcciones(resultado)}
      </div>
      <div class="card-body tight">
        ${esDemografica ? tablaDemografica(resultado) : tablaRanking(resultado)}
        ${esCompleta ? paginacionHtml(resultado) : ''}
      </div>
    </div>
    ${esDemografica ? '' : `${pintarExcluidos(resultado)}${esCompleta ? '' : pintarDiagnostico(resultado)}`}`;

  activarTokens(caja);
  engancharAcciones(caja, resultado);
  $$('[data-pagina-resultado]', caja).forEach((b) => {
    b.onclick = () => abrirResultadoCompleto(resultado.ejecucion.ejecucion_id,
      Number(b.dataset.paginaResultado), resultado.ejecucion);
  });
}

/* A6 — el recorte, a la vista: cuántas personas se verificaron frente a
   cuántas se muestran. Sin esto, «50 resultados» con `top_k` 150 se lee como
   «no hay más gente que cumpla». */
function pintarRecorte(resultado) {
  const r = resultado.recorte;
  if (!r) return '';
  const partes = [`se revisaron ${r.personas_verificadas}`
    + (r.personas_pertinentes != null && r.personas_pertinentes < r.personas_verificadas
      ? ` (${r.personas_pertinentes} con evidencia pertinente)` : '')];
  if (r.recortado) partes.push(`se muestran ${r.mostradas} (Personas a mostrar = ${r.limite})`);
  return ` <span class="small muted">· ${partes.join(' · ')}</span>`;
}

function paginacionHtml(resultado) {
  const p = resultado.paginacion;
  if (!p || p.paginas <= 1) return '';
  // Primera, última y las vecinas de la actual: con 137 personas y páginas
  // chicas serían treinta botones.
  const visibles = [...new Set([1, p.pagina - 1, p.pagina, p.pagina + 1, p.paginas])]
    .filter((i) => i >= 1 && i <= p.paginas).sort((x, y) => x - y);
  const botones = [];
  visibles.forEach((i, k) => {
    if (k && i - visibles[k - 1] > 1) botones.push('<span class="muted">…</span>');
    botones.push(`<button class="btn btn-sm ${i === p.pagina ? 'btn-dark' : 'btn-outline'}"
      data-pagina-resultado="${i}">${i}</button>`);
  });
  return `<div class="toolbar" style="padding:.75rem 1.5rem">
      <span class="small muted">Página ${p.pagina} de ${p.paginas} · ${p.total} persona(s)
        · ${p.por_pagina} por página</span>
      <span class="toolbar-spacer"></span>${botones.join('')}</div>`;
}

/* R-CS · cambio 5 — qué se verificó en una completa, por criterio, y cuánto
   costó. Ocupa el lugar del puente: en la completa no hay pool ni top-k. */
function pintarResumenCompleta(resultado) {
  const v = resultado.verificacion || {};
  const c = resultado.costo || {};
  return `<div class="card"><div class="card-body">
    <dl class="kv">
      <dt>Alcance</dt><dd><strong>Completo</strong> — ${esc(resultado.ejecucion?.estado_etiqueta || '')}
        <div class="small muted" style="font-weight:500">Las personas se arman al abrir el
          resultado, con el consentimiento de hoy: quien retiró el uso semántico ya no está.</div></dd>
      <dt>Verificación</dt><dd>${(v.por_criterio || []).map((x) => `<div class="small">
        <strong>${esc(x.criterio)}</strong>: ${x.verificadas} de ${x.unidades} unidad(es)
        verificadas · ${x.irrelevantes} irrelevante(s)${x.sin_verificar
          ? ` · <span style="color:var(--warn,#b45309)">${x.sin_verificar} sin verificar</span>` : ''}</div>`).join('')}</dd>
      <dt>Costo</dt><dd>${fmtUsd(c.usd)} de ${fmtUsd(c.presupuesto_usd)} presupuestados
        <span class="small muted">(estimado ${fmtUsd(c.estimado?.usd)})</span></dd>
    </dl>
  </div></div>`;
}

/* SC3 — la barra de acciones, una sola vez para los dos tipos de
   resultado. La próxima acción que se agregue se agrega acá y aparece en
   los dos: duplicarla en cada tabla es lo que dejó a la demográfica sin
   ninguna. */
function barraDeAcciones(resultado) {
  const reidentificado = hayReidentificacion(resultado);
  return `<div class="toolbar" id="acciones-resultado">
      <button class="btn btn-outline btn-sm" id="ver-nombres">Ver quiénes son</button>
      <button class="btn btn-outline btn-sm" id="bajar-csv">Descargar CSV</button>
      <button class="btn btn-outline btn-sm" id="bajar-csv-pii"
        ${reidentificado ? '' : 'disabled'}
        title="${reidentificado
          ? 'CSV con nombre, documento y contacto. Queda registrado.'
          : 'Primero hay que reidentificar: exportar con datos no puede ser un segundo camino para sacar PII.'}"
        >CSV con datos</button>
      <button class="btn btn-outline btn-sm" id="elegir-columnas">Columnas</button>
      <button class="btn btn-outline btn-sm" id="crear-panel">Crear panel</button>
    </div>`;
}

function engancharAcciones(caja, resultado) {
  $('#ver-nombres', caja).onclick = verNombres;
  $('#bajar-csv', caja).onclick = bajarCsv;
  $('#bajar-csv-pii', caja).onclick = bajarCsvIdentificado;
  $('#crear-panel', caja).onclick = crearPanelDesdeConsulta;
  $('#elegir-columnas', caja).onclick = abrirElegirColumnas;
  $$('[data-detalle]', caja).forEach((b) => {
    b.onclick = () => abrirDetalle(resultado.items[Number(b.dataset.detalle)]);
  });
  // R7.3 — la ficha se abre sin perder el resultado: es un modal encima de
  // la lista, y cerrarlo no vuelve a consultar nada.
  $$('[data-ficha]', caja).forEach((b) => {
    b.onclick = () => abrirFicha(resultado.items[Number(b.dataset.ficha)]);
  });
  const intercambio = $('#ver-intercambio', caja);
  if (intercambio) intercambio.onclick = () => verIntercambio(intercambio.dataset.ejecucion);
}

function tablaRanking(resultado) {
  return resultado.items.length ? `<div class="table-wrap"><table>
      <thead><tr>
        <th>#</th><th>Persona</th>
        ${columnasElegidas.map((c) => `<th>${esc(etiquetaDeColumna(c))}</th>`).join('')}
        <th>Puntaje</th><th>Confianza</th>
        <th>Criterios</th><th>Evidencia</th>
      </tr></thead>
      <tbody>${resultado.items.map((item, i) => filaItem(item, i)).join('')}</tbody>
    </table></div>` : vacio(
      'Nadie quedó en el ranking. Probá el modo laxo, subí el pool o '
      + 'revisá los criterios.', '🕳️');
}

/* SC2 — la lista demográfica. Las diferencias legítimas se conservan: no
   tiene puntaje, ni confianza, ni criterios, ni evidencia, y no se inventan
   columnas vacías para que se parezca al ranking. El orden es el del
   listado, no un ranking. Y es seudónima, como la semántica: el nombre
   aparece solo después de «Ver quiénes son», que queda registrado. */
const COLUMNAS_DEMOGRAFICAS_POR_DEFECTO = ['sexo', 'tramo_etario', 'localidad'];

function columnasDeLaDemografica() {
  return columnasElegidas.length ? columnasElegidas : COLUMNAS_DEMOGRAFICAS_POR_DEFECTO;
}

function tablaDemografica(resultado) {
  if (!resultado.items.length) return vacio('El segmento está vacío.', '🕳️');
  const columnas = columnasDeLaDemografica();
  return `<div class="table-wrap"><table>
      <thead><tr>
        <th>#</th><th>Persona</th>
        ${columnas.map((c) => `<th>${esc(etiquetaDeColumna(c))}</th>`).join('')}
      </tr></thead>
      <tbody>${resultado.items.map((item, i) => filaDemografica(item, i, columnas)).join('')}</tbody>
    </table></div>
    ${columnasElegidas.length ? '' : `<p class="field-hint" style="padding:.5rem 1.5rem">
      Con «Columnas» elegís qué atributos ver; la elección se recuerda y no
      vuelve a correr la consulta.</p>`}`;
}

function filaDemografica(item, indice, columnas) {
  const nombre = nombresResueltos[item.id_persona]?.nombre;
  return `<tr>
    <td class="mono">${indice + 1}</td>
    <td>${nombre ? `<div class="td-strong">${esc(nombre)}</div>` : ''}
        ${token(item.id_persona)}
        <button class="btn btn-outline btn-sm" data-ficha="${indice}"
                style="margin-top:.35rem">Ficha</button></td>
    ${columnas.map((c) => `<td>${celdaDemografica(item, c)}</td>`).join('')}
  </tr>`;
}

/* Las columnas por defecto vienen en el propio resultado; las elegidas, del
   mismo `/resultados/atributos` que usa el ranking. */
function celdaDemografica(item, clave) {
  if (!columnasElegidas.length) {
    return item[clave] ? esc(item[clave]) : '<span class="muted small">sin dato</span>';
  }
  return celdaDeColumna(item.id_persona, clave);
}

function avisoDemografica(resultado) {
  return `<div class="alert alert-info">
      Consulta puramente demográfica: se resolvió entera en la bóveda y no se
      abrió conexión al store semántico. No hay puntaje ni evidencia: el orden
      es el del listado. ${resultado.items.length ? 'Ver quiénes son, exportar'
        + ' y crear un panel funcionan igual que con un resultado semántico.' : ''}
    </div>`;
}

/* R-CS · A4 — sin `estado` (un backend viejo) no se asume nada favorable. */
function estadoHtml(item) {
  const e = ESTADOS[item.estado];
  if (!e) return '<div class="small muted" title="El servidor no informó el estado.">estado sin dato</div>';
  return `<div style="margin-bottom:.25rem"><span class="est ${e.clase}" title="${esc(e.ayuda)}">${esc(e.etiqueta)}</span></div>`;
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
    <td>${estadoHtml(item)}
        <span class="est est-${item.confianza === 'alta' ? 'vigente' : 'pendiente'}">
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
  (columnasOfrecidas.find((c) => c.clave === clave) || {}).etiqueta
  || catalogo.etiquetaDe(catalogoDeAtributos, clave) || clave;

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
  // SC2 — un resultado demográfico no tiene evidencia: la sección no se
  // muestra en vez de quedar vacía o de decir que «no se pudo leer».
  const conEvidencia = ultimoResultado?.tipo !== 'demografica';
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

    ${conEvidencia ? `<h4 class="ficha-titulo">Por qué aparece en este resultado</h4>
    ${evidenciaHtml(ficha.evidencia, item)}` : ''}

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

    <h4 class="ficha-titulo">Origen</h4>
    ${pintarOrigen(ficha.origen, { compacto: true })}

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
        <dt>Estado</dt><dd>${estadoHtml(item)}</dd>
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
          ${(c.evidencias_evaluadas || []).length > 1 || c.contradiccion ? `<div class="small" style="margin:.2rem 0">
            ${(c.evidencias_evaluadas || []).map((ev) => {
              const vv = VEREDICTOS[ev.veredicto] || { etiqueta: ev.veredicto, clase: '' };
              return `<div><span class="est ${vv.clase}">${esc(vv.etiqueta)}</span>
                ${esc((ev.valor_texto || '').slice(0, 80))}${ev.repeticiones > 1
                  ? ` <span class="muted">(el mismo texto en ${ev.repeticiones} respuestas: se verificó una vez)</span>` : ''}</div>`;
            }).join('')}</div>` : ''}
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
  /* R-CS — quien solo tenía evidencia irrelevante no fue juzgado contra el
     criterio: no habló del tema. Listarlos uno por uno tapa a los que sí
     importan (los que contradicen), así que van en una línea. */
  const sinPertinente = resultado.excluidos.filter((e) => e.motivo === 'sin_evidencia_pertinente');
  const resto = resultado.excluidos.filter((e) => e.motivo !== 'sin_evidencia_pertinente');
  return `<div class="card">
    <div class="card-header">
      <span class="card-header-title">Excluidos — ${resultado.excluidos.length}</span>
      <span class="small muted">Por qué no están en el ranking</span>
    </div>
    <div class="card-body tight">
    ${sinPertinente.length ? `<p class="small" style="padding:.75rem 1.5rem 0">
      <span class="est est-inactivo">Sin evidencia pertinente</span>
      ${sinPertinente.length} persona(s): sus respuestas se revisaron y no hablan del
      criterio. No contradicen nada; simplemente no aportan.</p>` : ''}
    ${resto.length ? `<div class="table-wrap"><table>
      <thead><tr><th>Persona</th><th>Motivo</th><th>Criterio</th><th>Evidencia</th></tr></thead>
      <tbody>${resto.map((e) => {
        const v = VEREDICTOS[e.motivo] || { etiqueta: e.motivo, clase: '' };
        return `<tr>
          <td>${token(e.id_persona)}</td>
          <td><span class="est ${v.clase}">${esc(v.etiqueta)}</span>${e.contradiccion
            ? '<div class="small" style="color:var(--warn,#b45309)" title="Tiene evidencia que cumple y evidencia que contradice: manda la contradicción.">se contradice</div>' : ''}</td>
          <td class="small">${esc(e.criterio || '')}</td>
          <td class="small">${esc(e.evidencia || '—')}</td>
        </tr>`;
      }).join('')}</tbody></table></div>` : ''}</div>
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
        Criterio embebido como <code>${esc(d.tipo_embedding_criterio || '—')}</code> ·
        Store semántico abierto: ${d.abrio_semantica ? 'sí' : 'no'}
        ${resultado.costo ? ` · Costo de esta consulta: <strong>${fmtUsd(resultado.costo.usd)}</strong>` : ''}
        ${d.ejecucion_id ? ` · Ejecución <code>${esc(d.ejecucion_id)}</code>` : ''}
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
        <thead><tr><th>Criterio</th><th>Unidades</th><th>Sin verificar</th>
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
    // La completa no se vuelve a correr para exportar: el CSV sale de la
    // ejecución, con el ranking entero y no solo la página en pantalla.
    const salida = ultimoResultado?.alcance === 'completo'
      ? await api.consultas.ejecucion(ultimoResultado.ejecucion.ejecucion_id, { formato: 'csv' })
      : await api.consultas.csv(definicion);
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
