/* Cliente de la API.

   En producción pega contra las Cloud Functions, mandando el ID token de
   Firebase Auth en el header Authorization; el backend resuelve el rol
   contra el padrón de usuarios.

   Si el proyecto todavía no está configurado, `estado.demo` queda en true
   y las llamadas se resuelven contra el backend en memoria de `demo.js`.
   Es para recorrer la interfaz: no guarda nada y no toca ninguna base. */

import * as demo from './demo.js';

const BASE = (window.API_BASE || '/api').replace(/\/$/, '');

export const estado = {
  demo: false,
  obtenerToken: async () => null,
};

export class ErrorApi extends Error {
  constructor(mensaje, status, cuerpo) {
    super(mensaje);
    this.status = status;
    this.cuerpo = cuerpo || {};
    this.codigo = this.cuerpo.error || 'error';
    this.detalle = this.cuerpo.detalle;
  }
}

async function pedir(metodo, camino, { cuerpo, consulta } = {}) {
  if (estado.demo) return demo.responder(metodo, camino, cuerpo, consulta);

  const url = new URL(BASE + camino, window.location.origin);
  Object.entries(consulta || {}).forEach(([clave, valor]) => {
    if (valor !== undefined && valor !== null && valor !== '') {
      url.searchParams.set(clave, valor);
    }
  });

  const token = await estado.obtenerToken();
  const respuesta = await fetch(url, {
    method: metodo,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: cuerpo ? JSON.stringify(cuerpo) : undefined,
  });

  let datos = {};
  try { datos = await respuesta.json(); } catch { /* respuesta sin cuerpo */ }

  if (!respuesta.ok) {
    throw new ErrorApi(datos.mensaje || `Error ${respuesta.status}`, respuesta.status, datos);
  }
  return datos;
}

const GET = (camino, consulta) => pedir('GET', camino, { consulta });
const POST = (camino, cuerpo) => pedir('POST', camino, { cuerpo });
const PATCH = (camino, cuerpo) => pedir('PATCH', camino, { cuerpo });
const DELETE = (camino) => pedir('DELETE', camino);

/* ── Superficie de la Fase 1 (misma que el HANDOFF) ─────────────── */

export const yo = () => GET('/yo');

export const panelistas = {
  listar: (consulta) => GET('/panelistas', consulta),
  ficha: (idPersona) => GET(`/panelistas/${idPersona}`),
  alta: (cuerpo) => POST('/panelistas', cuerpo),
};

export const revisiones = {
  listar: (estadoRevision = 'pendiente') => GET('/revisiones', { estado: estadoRevision }),
  resolver: (id, decision, idPersona) =>
    POST(`/revisiones/${id}/resolver`, { decision, id_persona: idPersona }),
};

export const paneles = {
  listar: () => GET('/paneles'),
  ver: (id) => GET(`/paneles/${id}`),
  crear: (nombre, descripcion) => POST('/paneles', { nombre, descripcion }),
  cambiarEstado: (id, estadoPanel) => PATCH(`/paneles/${id}`, { estado: estadoPanel }),
  miembros: (id, estadoMembresia = 'activo') =>
    GET(`/paneles/${id}/miembros`, { estado: estadoMembresia }),
  agregarMiembros: (id, idsPersona) => POST(`/paneles/${id}/miembros`, { ids_persona: idsPersona }),
  quitarMiembro: (id, idPersona) => DELETE(`/paneles/${id}/miembros/${idPersona}`),
};

export const consentimientos = {
  otorgar: (idPersona, finalidad, versionTexto) =>
    POST(`/consentimientos/${idPersona}`, { finalidad, version_texto: versionTexto }),
  retirar: (idPersona, finalidad) =>
    POST(`/consentimientos/${idPersona}/retiro`, { finalidad }),
};

export const cumplimiento = {
  pendientes: () => GET('/cumplimiento/pendientes'),
  reintentar: () => POST('/cumplimiento/reintentar', {}),
  auditoriaPii: () => GET('/auditoria/pii'),
};

export const encuestas = {
  listar: (panelId) => GET('/encuestas', panelId ? { panel_id: panelId } : {}),
  ver: (id) => GET(`/encuestas/${id}`),
  crear: (panelId, nombre, fechaCampo) =>
    POST('/encuestas', { panel_id: panelId, nombre, fecha_campo: fechaCampo }),
  cambiarEstado: (id, estadoEncuesta) => PATCH(`/encuestas/${id}`, { estado: estadoEncuesta }),
  convocar: (id, { idsPersona, todoElPanel } = {}) =>
    POST(`/encuestas/${id}/convocatoria`, {
      ids_persona: idsPersona, todo_el_panel: !!todoElPanel,
    }),
  participacion: (id) => GET(`/encuestas/${id}/participacion`),
  ingestar: (id, { preguntas, filas, columnaId, origen }) =>
    POST(`/encuestas/${id}/ingesta`, {
      preguntas, filas, columna_id: columnaId || 'id_en_origen', origen,
    }),
  cruce: (id) => GET(`/encuestas/${id}/cruce`),
};
