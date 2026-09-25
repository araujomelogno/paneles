/* Cumplimiento — el invariante de privacidad, visible.

   Cuatro cosas: la auditoría de que el store semántico no tiene columnas de
   PII, el estado de las migraciones de las dos bases, las bajas cuyo borrado
   semántico quedó pendiente de confirmar, y el detalle de qué implica cada
   retiro de consentimiento.

   El estado del esquema está acá y no en Configuración porque es la pantalla
   que ya se usa para verificar que el sistema está sano, y porque una
   migración sin aplicar es exactamente eso: una parte del sistema que no está
   donde se cree que está. */

import * as api from '../api.js';
import {
  $, $$, esc, encabezado, vacio, cargando, toast, activarTokens,
  fechaHora, token, alerta,
} from '../ui.js';

let contexto = {};

export async function render(main, ctx) {
  contexto = ctx;
  main.innerHTML = encabezado('Cumplimiento', 'y privacidad',
    'Separación de stores, retiros de consentimiento y bajas pendientes.') + `
    <div class="grid-2">
      <div class="stack">
        <div class="card">
          <div class="card-header"><span class="card-header-title">Auditoría del store semántico</span>
            <button class="btn btn-outline btn-sm" id="auditar">Auditar ahora</button></div>
          <div class="card-body"><div id="auditoria">${cargando('12vh')}</div></div>
        </div>

        <div class="card">
          <div class="card-header"><span class="card-header-title">Esquema de las dos bases</span>
            <button class="btn btn-outline btn-sm" id="ver-esquema">Revisar</button></div>
          <div class="card-body"><div id="esquema">${cargando('12vh')}</div></div>
        </div>

        <!-- R4.3/R4.5 — las tres condiciones externas del bloque de
             contacto. Van acá porque su ausencia no es un detalle técnico:
             sin proveedor de códigos la landing no se puede anunciar, y sin
             credenciales de Meta no se puede enviar. -->
        <div class="card">
          <div class="card-header">
            <span class="card-header-title">Contacto: landing y WhatsApp</span>
            <button class="btn btn-outline btn-sm" id="ver-contacto">Revisar</button></div>
          <div class="card-body"><div id="contacto">${cargando('12vh')}</div></div>
        </div>

        <div class="card">
          <div class="card-header"><span class="card-header-title">Bajas con borrado semántico pendiente</span>
            <button class="btn btn-outline btn-sm" id="reintentar">Reintentar</button></div>
          <div class="card-body tight"><div id="pendientes">${cargando('12vh')}</div></div>
        </div>

        <!-- R5.3 — la bóveda tiene más de un consumidor. Una baja le genera
             un pendiente a cada uno, y la baja en la bóveda no espera a
             ninguno: se completa igual y el pendiente queda abierto hasta que
             ese sistema confirme. Este panel es el tablero del DPO. -->
        <div class="card">
          <div class="card-header"><span class="card-header-title">Bajas sin confirmar por consumidor</span>
            <button class="btn btn-outline btn-sm" id="ver-borrados">Revisar</button></div>
          <div class="card-body tight"><div id="borrados">${cargando('12vh')}</div></div>
        </div>
      </div>

      <div class="stack">
        <div class="card">
          <div class="card-header"><span class="card-header-title">Qué implica cada retiro</span></div>
          <div class="card-body stack">
            <div class="aviso">
              <h4>Uso semántico</h4>
              <p>Se borran los embeddings del store semántico. La persona sigue en el panel y
              puede seguir siendo convocada: son finalidades separadas.</p>
            </div>
            <div class="aviso">
              <h4>Contacto y participación</h4>
              <p>Sale del muestreo: se dan de baja sus membresías. Sus embeddings quedan,
              porque los consintió por separado.</p>
            </div>
            <div class="aviso grave">
              <h4>Baja total</h4>
              <p>Cascada completa: se borra la PII de la bóveda, se borran los
              embeddings del store semántico y sale de todos los paneles.</p>
              <p>Queda una lápida con el token opaco —que no es PII— para poder probar
              que el retiro se atendió. El borrado en la bóveda no espera al store semántico: si el semántico
              falla, la baja se completa igual y queda listada acá para reintentar.</p>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header"><span class="card-header-title">La regla que no se negocia</span></div>
          <div class="card-body">
            <p class="small">La PII vive solo en el store de bóveda. Al store semántico viaja únicamente
            el <code>id_persona</code>, el <code>ref_estudio</code>, el texto de la respuesta
            ya despersonalizado y su embedding.</p>
            <p class="small" style="margin-top:0.7rem">El control es triple. La base
            <strong>rechaza en el momento</strong> cualquier columna con nombre de PII que se
            intente crear del lado semántico, venga por donde venga. Antes de eso, el chequeo
            previo al despliegue lee los archivos de migración y no deja pasar una que la
            declare. Y cada escritura hacia el store semántico pasa por una validación que
            aborta la operación si detecta una clave de PII, que es lo único que ve la PII
            escondida adentro de un <code>jsonb</code>.</p>
            <p class="small" style="margin-top:0.7rem">Las excepciones legítimas
            —<code>nombre</code> en cuestionario, pregunta y serie, que son metadatos del
            estudio y no de la persona— están declaradas en la base con su motivo. La salida
            no es apagar el guardia.</p>
          </div>
        </div>
      </div>
    </div>`;

  $('#auditar').onclick = cargarAuditoria;
  $('#ver-esquema').onclick = cargarEsquema;
  $('#ver-contacto').onclick = cargarContacto;
  $('#reintentar').onclick = reintentar;
  $('#ver-borrados').onclick = cargarBorrados;
  await Promise.all([cargarAuditoria(), cargarEsquema(), cargarContacto(),
                     cargarPendientes(), cargarBorrados()]);
}

/* Las migraciones se aplican a mano contra cada instancia de Cloud SQL. Una
   que no se aplicó no se nota hasta que alguien entra a la pantalla que la
   necesitaba, y ahí falla con un error del servidor que no dice nada. Este
   panel lo adelanta. */
async function cargarContacto() {
  const contenedor = $('#contacto');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('12vh');
  try {
    const estado = await api.cumplimiento.contacto();
    const avisos = [
      ...estado.verificacion.avisos,
      ...estado.desafio.avisos,
      ...estado.whatsapp.avisos,
    ];
    contenedor.innerHTML = `
      ${avisos.length
        ? `<div class="alert alert-warn">
             <strong>Hay cosas sin configurar.</strong>
             <ul style="margin:.4rem 0 0 1.1rem">
               ${avisos.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>
           </div>`
        : `<div class="alert alert-success">La landing está endurecida y el
             canal de WhatsApp está configurado.</div>`}
      <dl class="kv">
        <dt>Envío de códigos</dt>
        <dd>${estado.verificacion.envia_de_verdad
          ? `sí, por <code>${esc(estado.verificacion.proveedor_envio)}</code>`
          : '<span class="muted">sin proveedor: el código vuelve en la respuesta</span>'}</dd>
        <dt>Desafío anti-bot</dt>
        <dd>${estado.desafio.activo
          ? `<code>${esc(estado.desafio.proveedor)}</code>`
          : '<span class="muted">sin configurar</span>'}</dd>
        <dt>WhatsApp</dt>
        <dd>${estado.whatsapp.configurado
          ? 'credenciales presentes'
          : `<span class="muted">faltan ${estado.whatsapp.faltan.join(', ')}</span>`}</dd>
      </dl>`;
  } catch (error) {
    contenedor.innerHTML = alerta(error.message);
  }
}

async function cargarEsquema() {
  const contenedor = $('#esquema');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('12vh');
  let estado;
  try {
    estado = await api.cumplimiento.esquema();
  } catch (error) {
    // La ruta devuelve 500 cuando falta algo: el cuerpo trae el detalle.
    estado = error.cuerpo?.migraciones ? error.cuerpo : null;
    if (!estado) { contenedor.innerHTML = alerta(error.message); return; }
  }

  if (estado.completo) {
    contenedor.innerHTML = `
      <div class="alert alert-success">
        Las dos bases tienen aplicadas todas las migraciones que el sistema espera.
      </div>
      <p class="small muted">${['boveda', 'semantica'].map((s) =>
        `${s}: ${estado[s].migraciones.length} migración(es)`).join(' · ')}</p>`;
    return;
  }

  contenedor.innerHTML = `
    <div class="alert alert-error">
      Faltan migraciones por aplicar. Las pantallas que dependan de lo que falta
      van a fallar hasta que se apliquen.
    </div>
    ${['boveda', 'semantica'].map((store) => {
      const faltan = estado[store].migraciones.filter((m) => !m.aplicada);
      if (!faltan.length) return '';
      return `<div style="margin-bottom:.7rem">
        <div class="small td-strong">Store ${esc(store)}</div>
        <ul style="margin-left:1.1rem;font-size:0.82rem">
          ${faltan.map((m) => `<li><code>${esc(m.migracion)}</code>
            <span class="muted">— falta ${m.faltantes.map((f) => `<code>${esc(f)}</code>`).join(', ')}</span>
          </li>`).join('')}
        </ul>
      </div>`;
    }).join('')}
    <p class="small muted">Se aplican con <code>psql</code> contra la instancia
    correspondiente; los comandos exactos están en el manual de despliegue de
    cada fase.</p>`;
}

async function cargarAuditoria() {
  const contenedor = $('#auditoria');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('12vh');
  try {
    const resultado = await api.cumplimiento.auditoriaPii();
    contenedor.innerHTML = resultado.limpio
      ? `<div class="alert alert-success">
           0 columnas de PII en el store semántico. El esquema está limpio.
         </div>
         <p class="small muted">Se revisan todas las tablas del esquema semántico contra la
         lista de campos identificatorios de la bóveda y sus sinónimos habituales.</p>`
      : `<div class="alert alert-error">
           Se encontraron ${resultado.hallazgos.length} columna(s) de PII en el store semántico.
         </div>
         <ul style="margin-left:1.1rem;font-size:0.82rem">
           ${resultado.hallazgos.map((h) => `<li><code>${esc(h.tabla)}.${esc(h.columna)}</code></li>`).join('')}
         </ul>`;
  } catch (error) {
    contenedor.innerHTML = alerta(error.message);
  }
}

async function cargarPendientes() {
  const contenedor = $('#pendientes');
  if (!contenedor) return;
  const { items } = await api.cumplimiento.pendientes();
  if (!items.length) {
    contenedor.innerHTML = vacio('Ninguna baja quedó a medias.', '✅');
    return;
  }
  contenedor.innerHTML = `
    <div class="table-wrap"><table>
      <thead><tr><th>Identificador</th><th>Motivo</th><th>Baja en la bóveda</th><th>Error</th></tr></thead>
      <tbody>${items.map((b) => `
        <tr>
          <td>${token(b.id_persona)}</td>
          <td class="small">${esc(b.motivo)}${b.finalidad ? ` · ${esc(b.finalidad)}` : ''}</td>
          <td class="small">${fechaHora(b.borrado_local_en)}</td>
          <td class="small td-muted">${esc(b.semantica_error || 'sin confirmar')}</td>
        </tr>`).join('')}</tbody></table></div>`;
  activarTokens(contenedor);
}

/* R5.3 — el número que importa no es cuántas hay sino hace cuánto está
   abierta la más vieja: una baja que lleva semanas sin confirmar no es una
   tarea atrasada, es un incumplimiento. Por eso el aviso mira los días y no
   la cantidad. */
const DIAS_QUE_PREOCUPAN = 7;

async function cargarBorrados() {
  const contenedor = $('#borrados');
  if (!contenedor) return;
  contenedor.innerHTML = cargando('12vh');
  try {
    const { items, mas_vieja_dias: dias } = await api.cumplimiento.borrados();
    if (!items.length) {
      contenedor.innerHTML = vacio(
        'Todos los sistemas confirmaron las bajas que les tocaban.', '✅');
      return;
    }
    contenedor.innerHTML = `
      <div class="alert ${dias >= DIAS_QUE_PREOCUPAN ? 'alert-error' : 'alert-warn'}">
        ${items.length} baja(s) sin confirmar. La más vieja lleva
        ${dias} día(s) abierta.
        ${dias >= DIAS_QUE_PREOCUPAN
          ? ' Conviene contactar al responsable técnico del sistema.'
          : ''}
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Identificador</th><th>Sistema</th><th>Alcance</th>
          <th>Días</th><th>Último error</th></tr></thead>
        <tbody>${items.map((b) => `
          <tr>
            <td>${token(b.id_persona)}</td>
            <td class="small">${esc(b.sistema_nombre || b.sistema)}
              ${b.contacto_tecnico
                ? `<div class="td-muted">${esc(b.contacto_tecnico)}</div>` : ''}</td>
            <td class="small">${esc(b.finalidad || b.alcance)}</td>
            <td class="small">${b.dias_abierto}</td>
            <td class="small td-muted">${esc(b.ultimo_error
              || (b.intentos ? `${b.intentos} intento(s)` : 'sin intentos'))}</td>
          </tr>`).join('')}</tbody></table></div>`;
    activarTokens(contenedor);
  } catch (error) {
    contenedor.innerHTML = alerta(error.message);
  }
}

async function reintentar() {
  try {
    const { resultados } = await api.cumplimiento.reintentar();
    const errores = resultados.filter((r) => r.estado === 'error').length;
    toast(errores
      ? `${resultados.length - errores} confirmadas, ${errores} siguen fallando.`
      : `${resultados.length} baja(s) confirmadas del lado semántico.`,
      errores ? 'err' : 'ok');
    await Promise.all([cargarPendientes(), cargarBorrados()]);
  } catch (error) {
    toast(error.message, 'err');
  }
}
