/* R8.9 — corregir un estudio ya ingestado, sin volver a subir el archivo.

   Se abre desde la ficha de una encuesta («Corregir y reprocesar») y desde
   Estadísticas → Últimas cargas, que es donde aparecen también las cargas
   sin panel. Muestra las preguntas tal como quedaron en el store semántico,
   el diagnóstico de calidad sobre lo ya cargado y, para cada variable, la
   vista previa del texto con la corrección aplicada.

   Tres cosas que la pantalla dice antes de que nadie confirme:

   * **Qué cambia y a cuántas respuestas afecta.** Sale de la misma ruta que
     ejecuta, con `solo_revisar`: lo que la revisión dice es lo que pasa.
   * **Que lo que no cambió no se toca.** El `hash_texto` hace que solo se
     re-embeba lo que de verdad cambió de texto.
   * **Lo que no se puede recuperar.** Lo que la ingesta dejó afuera —lo no
     marcado de una batería, los valores de no respuesta excluidos— no está
     en la base: para tenerlo hay que volver a cargar el archivo. */

import * as api from '../api.js';
import * as calidad from '../calidad.js';
import {
  $, $$, esc, cargando, toast, modal, cerrarModal, alerta, fechaHora,
} from '../ui.js';

const TIPOS = ['cerrada', 'abierta', 'escala', 'numerica'];

const textoDeOpciones = (opciones) => (opciones
  ? Object.entries(opciones).map(([c, e]) => `${c}=${e}`).join('; ') : '');

function parsearOpciones(texto) {
  if (!texto) return null;
  const opciones = {};
  texto.split(/[;\n]/).forEach((par) => {
    const [codigo, ...resto] = par.split('=');
    if (codigo && resto.length) opciones[codigo.trim()] = resto.join('=').trim();
  });
  return Object.keys(opciones).length ? opciones : null;
}

const lista = (texto) => String(texto || '').split(',').map((v) => v.trim()).filter(Boolean);

/* `destino`: `{tipo: 'encuesta'|'carga', id, nombre}`. */
export async function abrirReproceso(destino, alLanzar) {
  const caja = modal({
    titulo: `Corregir y reprocesar — ${destino.nombre || `${destino.tipo} ${destino.id}`}`,
    ancho: '920px',
    cuerpo: cargando('30vh'),
    acciones: [{ texto: 'Cerrar', clase: 'btn-outline', onClick: cerrarModal }],
  });
  const cuerpo$ = $('.modal-body', caja);
  let estado;
  try {
    estado = await (destino.tipo === 'carga'
      ? api.reproceso.preguntasDeCarga(destino.id)
      : api.reproceso.preguntasDeEncuesta(destino.id));
  } catch (error) {
    cuerpo$.innerHTML = alerta(error.message, error.status === 404 ? 'warn' : 'error');
    return;
  }

  /* El estado deseado de cada pregunta, que arranca como está. Lo que se
     manda al servidor sale de acá. */
  const preguntas = estado.preguntas.map((p) => ({ ...p }));
  const porCodigo = Object.fromEntries(preguntas.map((p) => [p.codigo, p]));
  const aplicados = new Set();

  cuerpo$.innerHTML = `
    ${estado.trabajo_abierto ? alerta(
      `Hay una carga o un reproceso de este estudio en curso (trabajo `
      + `${estado.trabajo_abierto.trabajo_id}). Se puede preparar la corrección, `
      + `pero no lanzarla hasta que termine.`, 'warn') : ''}
    <div class="aviso">
      <h4>Qué hace un reproceso</h4>
      <p>Corrige los textos, las etiquetas y las decisiones de normalización
      de lo ya cargado y <strong>re-embebe solo las respuestas cuyo texto
      cambia</strong>: las demás no se tocan. Si se excluye una variable, sus
      respuestas se borran del store semántico. Corre en diferido, como una
      carga grande, y queda registrado qué se cambió y cuándo.</p>
      <p>Lo que la ingesta dejó afuera (lo no marcado de una batería, los
      valores de no respuesta excluidos) no está en la base: para recuperarlo
      hay que volver a cargar el archivo.</p>
    </div>
    <h4 class="ficha-titulo">Calidad del dato de lo cargado</h4>
    <div id="rep-calidad"></div>
    <h4 class="ficha-titulo">Preguntas · ${preguntas.length}</h4>
    <div id="rep-preguntas"></div>
    <div id="rep-revision"></div>
    <div class="toolbar" style="margin-top:0.8rem">
      <button class="btn btn-orange" id="rep-revisar">Revisar los cambios</button>
      <span class="toolbar-spacer"></span>
      <button class="btn btn-outline btn-sm" id="rep-forzar"
        title="Vuelve a pasar todas las respuestas por la configuración actual. Solo re-embebe las que quedaron desactualizadas.">
        Reprocesar todo de nuevo</button>
    </div>
    ${historialHtml(estado.reprocesos)}`;

  const preguntas$ = $('#rep-preguntas', cuerpo$);
  preguntas$.innerHTML = preguntas.map((p, i) => tarjetaHtml(p, i)).join('');

  const tarjetaDe = (codigo) => preguntas$.querySelector(
    `.rep-variable[data-codigo="${CSS.escape(codigo)}"]`);

  /* Lo que la tarjeta muestra, de vuelta al estado deseado. */
  function leerTarjeta(tarjeta) {
    const p = porCodigo[tarjeta.dataset.codigo];
    p.texto = $('.r-texto', tarjeta).value.trim();
    p.tipo = $('.r-tipo', tarjeta).value;
    p.opciones = parsearOpciones($('.r-opciones', tarjeta).value.trim());
    p.solo_marcadas = $('.r-solo', tarjeta).checked;
    p.valores_marcados = p.solo_marcadas ? lista($('.r-marcados', tarjeta).value) : [];
    p.excluir_valores = lista($('.r-excluir-valores', tarjeta).value);
    p.excluir = $('.r-excluir', tarjeta).checked;
    tarjeta.classList.toggle('excluida', p.excluir);
    return p;
  }

  /* Y al revés, cuando una acción del panel cambia el estado. */
  function escribirTarjeta(p) {
    const tarjeta = tarjetaDe(p.codigo);
    if (!tarjeta) return;
    $('.r-texto', tarjeta).value = p.texto || '';
    $('.r-tipo', tarjeta).value = p.tipo || 'cerrada';
    $('.r-opciones', tarjeta).value = textoDeOpciones(p.opciones);
    $('.r-solo', tarjeta).checked = Boolean(p.solo_marcadas);
    $('.r-marcados', tarjeta).value = (p.valores_marcados || []).join(', ');
    $('.r-excluir-valores', tarjeta).value = (p.excluir_valores || []).join(', ');
    $('.r-excluir', tarjeta).checked = Boolean(p.excluir);
    tarjeta.classList.toggle('excluida', Boolean(p.excluir));
  }

  /* La vista previa de una variable, con la corrección aplicada. Los
     valores de la muestra son los códigos recuperados del store semántico,
     así que se ven pasar por la configuración nueva igual que en la carga. */
  async function previa(tarjeta, espera = 350) {
    clearTimeout(tarjeta._reloj);
    tarjeta._reloj = setTimeout(async () => {
      const p = leerTarjeta(tarjeta);
      const destino$ = $('.p-previa', tarjeta);
      if (p.excluir) {
        destino$.innerHTML = `<div class="small"><strong>Se excluye</strong>:
          sus ${p.respuestas} respuesta(s) se borran del store semántico.</div>`;
        return;
      }
      try {
        const { items } = await api.calidadDato.vistaPrevia(
          [cuerpoDe(p)], { [p.codigo]: p.muestra || [] });
        destino$.innerHTML = calidad.previaHtml(items[p.codigo], p, p.texto_original);
        const volver = destino$.querySelector('[data-volver]');
        if (volver) {
          volver.onclick = () => {
            p.texto = p.texto_original;
            escribirTarjeta(p);
            previa(tarjeta, 0);
          };
        }
        destino$.querySelectorAll('[data-quitar]').forEach((b) => {
          b.onclick = () => {
            p[b.dataset.quitar] = Array.isArray(p[b.dataset.quitar]) ? [] : false;
            if (b.dataset.quitar === 'solo_marcadas') p.valores_marcados = [];
            escribirTarjeta(p);
            previa(tarjeta, 0);
          };
        });
      } catch (error) {
        destino$.innerHTML = `<span class="small muted">${esc(error.message)}</span>`;
      }
    }, espera);
  }

  $$('.rep-variable', preguntas$).forEach((tarjeta) => {
    tarjeta.querySelectorAll('input, select').forEach((campo) => {
      campo.addEventListener(campo.tagName === 'SELECT' || campo.type === 'checkbox'
        ? 'change' : 'input', () => previa(tarjeta));
    });
    previa(tarjeta, 0);
  });

  /* El panel de hallazgos sobre lo ya cargado. Las acciones cambian el
     estado deseado, no la base: nada se escribe hasta confirmar. */
  const calidad$ = $('#rep-calidad', cuerpo$);
  const pintarCalidad = () => {
    calidad$.innerHTML = calidad.panelHtml(estado.calidad, aplicados);
    calidad.activarPanel(calidad$, estado.calidad, aplicados, (_h, accion) => {
      Object.entries(accion.propuesta || {}).forEach(([codigo, campos]) => {
        const p = porCodigo[codigo];
        if (!p) return;
        if (campos.incluir === false) p.excluir = true;
        ['texto', 'tipo', 'opciones', ...calidad.CLAVES].forEach((clave) => {
          if (clave in campos) p[clave] = campos[clave];
        });
        escribirTarjeta(p);
        previa(tarjetaDe(codigo), 0);
      });
    }, pintarCalidad);
  };
  pintarCalidad();

  const lanzar = destino.tipo === 'carga'
    ? (cuerpo) => api.reproceso.deCarga(destino.id, cuerpo)
    : (cuerpo) => api.reproceso.deEncuesta(destino.id, cuerpo);

  const pedido = () => ({
    preguntas: preguntas.map((p) => (p.excluir
      ? { codigo: p.codigo, excluir: true } : cuerpoDe(p))),
  });

  /* Revisar y confirmar: la misma ruta con y sin `solo_revisar`, y el
     mismo cuerpo armado una sola vez. */
  async function revisar(cuerpo) {
    const revision$ = $('#rep-revision', cuerpo$);
    revision$.innerHTML = cargando('10vh');
    let r;
    try {
      r = await lanzar({ ...cuerpo, solo_revisar: true });
    } catch (error) {
      revision$.innerHTML = alerta(error.message);
      return;
    }
    if (r.sin_cambios) {
      revision$.innerHTML = alerta(r.mensaje || 'No hay cambios.', 'info');
      return;
    }
    revision$.innerHTML = `
      <h4 class="ficha-titulo">Revisión del reproceso</h4>
      ${(r.avisos || []).map((a) => alerta(a, 'warn')).join('')}
      <div class="grid-datos">
        <div class="tarjeta-dato"><div class="valor">${r.reembeben}</div>
          <div class="etiqueta">se re-embeben</div></div>
        <div class="tarjeta-dato"><div class="valor">${r.sin_cambios}</div>
          <div class="etiqueta">quedan igual, no se tocan</div></div>
        <div class="tarjeta-dato"><div class="valor">${r.se_borran}</div>
          <div class="etiqueta">se borran del store semántico</div></div>
      </div>
      ${r.cambios.length ? `<table class="tabla">
        <thead><tr><th>Variable</th><th>Campo</th><th>Antes</th><th>Después</th></tr></thead>
        <tbody>${r.cambios.map((c) => `<tr><td><code>${esc(c.codigo)}</code></td>
          <td>${esc(c.campo)}</td><td class="small">${esc(mostrar(c.antes))}</td>
          <td class="small">${esc(mostrar(c.despues))}</td></tr>`).join('')}</tbody>
      </table>` : ''}
      ${(r.ejemplos || []).length ? `<h4 class="ficha-titulo">Ejemplos</h4>
        ${r.ejemplos.map((e) => `<div class="evidencia" style="margin-bottom:.4rem">
          <div class="procedencia">${esc(e.codigo)} · ${e.accion === 'borra'
            ? 'se borra' : 'se re-embebe'}</div>
          <div class="small mono"><s>${esc(e.antes)}</s></div>
          ${e.despues ? `<div class="small mono">${esc(e.despues)}</div>` : ''}
        </div>`).join('')}` : ''}
      <div class="toolbar" style="margin-top:.6rem">
        <button class="btn btn-orange" id="rep-confirmar"
          ${estado.trabajo_abierto ? 'disabled' : ''}>Confirmar y reprocesar</button>
      </div>`;
    $('#rep-confirmar', revision$).onclick = () => confirmarLanzamiento(cuerpo);
    revision$.scrollIntoView({ block: 'start' });
  }

  async function confirmarLanzamiento(cuerpo) {
    try {
      const salida = await lanzar(cuerpo);
      cerrarModal();
      if (salida.trabajo_id) {
        toast(`Reproceso encolado: ${salida.lotes_total} lote(s). `
              + 'Queda registrado qué se cambió.', 'ok');
      } else {
        toast(salida.mensaje || 'Preguntas actualizadas.', 'ok');
      }
      await alLanzar?.(salida);
    } catch (error) {
      toast(error.message, 'err');
    }
  }

  $('#rep-revisar', cuerpo$).onclick = () => revisar(pedido());
  $('#rep-forzar', cuerpo$).onclick = () => revisar({ preguntas: [], forzar: true });
}

/* El cuerpo de una pregunta para el servidor: el estado deseado entero. Lo
   falso viaja explícito para poder **apagar** una decisión que estaba. */
function cuerpoDe(p) {
  return {
    codigo: p.codigo,
    texto: p.texto,
    tipo: p.tipo,
    opciones: p.opciones || null,
    solo_marcadas: Boolean(p.solo_marcadas),
    valores_marcados: p.solo_marcadas ? (p.valores_marcados?.length
      ? p.valores_marcados : ['1']) : [],
    excluir_valores: p.excluir_valores || [],
    prefijo_respuesta: p.prefijo_respuesta || null,
    fusionada_con: p.fusionada_con || null,
    bateria: p.bateria || null,
    pii_aceptada: Boolean(p.pii_aceptada),
  };
}

const mostrar = (v) => {
  if (v === null || v === undefined || v === false) return '—';
  if (v === true) return 'sí';
  if (Array.isArray(v)) return v.join(', ');
  if (typeof v === 'object') return textoDeOpciones(v);
  return String(v);
};

function tarjetaHtml(p, i) {
  const excluida = Boolean(p.excluida);
  return `<div class="rep-variable${excluida ? ' excluida' : ''}" data-codigo="${esc(p.codigo)}">
    <div class="rep-cab">
      <div><code>${esc(p.codigo)}</code>
        <span class="small muted">· ${p.respuestas} respuesta(s)</span>
        ${excluida ? '<span class="badge">excluida en un reproceso anterior</span>' : ''}</div>
      <label class="check"><input type="checkbox" class="r-excluir" ${excluida ? 'checked disabled' : ''} />
        Excluir la variable</label>
    </div>
    <div class="form-group">
      <input type="text" class="finput r-texto" value="${esc(p.texto || '')}"
             aria-label="Texto de la pregunta ${i + 1}" />
    </div>
    <div class="grid-2">
      <select class="fselect r-tipo">${TIPOS.map((t) => `<option value="${t}"
        ${t === p.tipo ? 'selected' : ''}>${t === 'numerica' ? 'numérica' : t}</option>`).join('')}</select>
      <input type="text" class="finput r-opciones" placeholder="1=Fernet; 2=Whisky"
             value="${esc(textoDeOpciones(p.opciones))}" />
    </div>
    <div class="grid-2" style="margin-top:.4rem">
      <div>
        <label class="check"><input type="checkbox" class="r-solo"
          ${p.solo_marcadas ? 'checked' : ''} /> Solo lo marcado (batería)</label>
        <input type="text" class="finput r-marcados" placeholder="Valores que cuentan como marcado: 1"
               value="${esc((p.valores_marcados || []).join(', '))}" />
      </div>
      <div>
        <label class="small">Valores que no se ingestan (no respuesta)</label>
        <input type="text" class="finput r-excluir-valores" placeholder="98, 99"
               value="${esc((p.excluir_valores || []).join(', '))}" />
      </div>
    </div>
    <div class="p-previa">${cargando('4vh')}</div>
  </div>`;
}

function historialHtml(reprocesos) {
  if (!reprocesos?.length) return '';
  return `
    <h4 class="ficha-titulo">Reprocesos anteriores</h4>
    <table class="tabla">
      <thead><tr><th>Cuándo</th><th>Qué se cambió</th><th class="num">Respuestas</th>
                 <th>Estado</th></tr></thead>
      <tbody>${reprocesos.map((r) => `
        <tr><td class="small">${esc(fechaHora(r.creado_en))}</td>
            <td class="small">${(r.cambios || []).map((c) => (c.forzado
              ? 'reproceso completo' : `${esc(c.codigo)} · ${esc(c.campo)}`)).join('<br>')}</td>
            <td class="num">${r.respuestas_a_procesar}</td>
            <td class="small">${esc(r.estado || '—')}</td></tr>`).join('')}
      </tbody>
    </table>`;
}
