/* Backend en memoria para MODO DEMO.

   Se usa solo cuando el proyecto Firebase todavía no está configurado.
   Reproduce las reglas que importan de la Fase 1 —dedup, gate de
   consentimiento, membresías N:M, cascada de baja, puente entre stores por
   `ref_estudio`— para poder recorrer la interfaz de verdad, pero NO es el
   sistema: no persiste nada, no hay Postgres y no hay embeddings.

   El "store remoto" de acá es un objeto aparte a propósito: se ve que solo
   recibe `id_persona` y `ref_estudio`, nunca PII. */

const VERSION = window.VERSION_CONSENTIMIENTO || 'consentimiento-2026-01';

const uuid = () => (crypto.randomUUID ? crypto.randomUUID()
  : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    }));

const ahora = () => new Date().toISOString();
const diasAtras = (n) => new Date(Date.now() - n * 86400000).toISOString();

/* ── Estado ─────────────────────────────────────────────────────── */

const bd = {
  // Store local: bóveda de PII + paneles.
  personas: [],
  alias: [],
  paneles: [],
  membresias: [],
  consentimientos: [],
  encuestas: [],
  participaciones: [],
  revisiones: [],
  borradas: [],
  // Store remoto: SOLO id_persona, ref_estudio y contenido despersonalizado.
  remoto: { cuestionarios: [], individuos: [], preguntas: [], respuestas: [] },
  secuencias: { panel: 1, encuesta: 1, revision: 1, consentimiento: 1, remoto: 1 },
};

const siguiente = (clave) => bd.secuencias[clave]++;

/* ── Semilla ────────────────────────────────────────────────────── */

const SEMILLA = [
  ['4.123.456-7', 'Ana Pérez Bentancor', 'F', '1988-04-12', 'Montevideo', 'ana.perez@correo.uy', '099 123 456', ['contacto_participacion', 'uso_semantico']],
  ['3.987.654-2', 'Beatriz Silva', 'F', '1974-11-30', 'Salto', 'b.silva@correo.uy', '099 234 567', ['contacto_participacion']],
  ['5.222.111-9', 'Carlos Méndez', 'M', '1995-07-08', 'Canelones', 'c.mendez@correo.uy', '099 345 678', ['contacto_participacion', 'uso_semantico']],
  ['2.777.333-1', 'Diego Sosa Fernández', 'M', '1962-02-19', 'Maldonado', 'd.sosa@correo.uy', '099 456 789', ['contacto_participacion', 'uso_semantico']],
  ['4.888.222-5', 'Elena Rodríguez', 'F', '1991-09-25', 'Montevideo', 'e.rodriguez@correo.uy', '099 567 890', ['contacto_participacion']],
  ['1.555.999-3', 'Fernando Castro', 'M', '1958-12-03', 'Rivera', 'f.castro@correo.uy', '099 678 901', ['uso_semantico']],
  ['5.111.777-8', 'Gabriela Núñez', 'F', '2001-06-14', 'Montevideo', 'g.nunez@correo.uy', '099 789 012', ['contacto_participacion', 'uso_semantico']],
  ['3.444.666-0', 'Hugo Ramírez', 'M', '1980-03-27', 'Paysandú', 'h.ramirez@correo.uy', '099 890 123', ['contacto_participacion', 'uso_semantico']],
  ['4.999.111-6', 'Irene Píriz', 'F', '1969-08-05', 'Colonia', 'i.piriz@correo.uy', '099 901 234', ['contacto_participacion']],
  ['5.333.888-4', 'Joaquín Lema', 'M', '1998-01-22', 'Tacuarembó', 'j.lema@correo.uy', '099 012 345', ['contacto_participacion', 'uso_semantico']],
];

function sembrar() {
  const nacional = crearPanel('Panel Nacional', 'Panel general de hogares, cobertura país.');
  const joven = crearPanel('Panel Joven 18-29', 'Submuestra de jóvenes urbanos.');

  SEMILLA.forEach(([documento, nombre, sexo, fechaNacimiento, localidad, email, celular, finalidades], i) => {
    const idPersona = uuid();
    bd.personas.push({
      id_persona: idPersona, documento, nombre, sexo,
      fecha_nacimiento: fechaNacimiento, localidad, email, celular,
      contacto: null, observaciones: null, creado_en: diasAtras(120 - i * 7),
    });
    bd.alias.push({ id_persona: idPersona, origen: 'dooblo', id_en_origen: `R-${String(i + 1).padStart(3, '0')}` });
    finalidades.forEach((finalidad) => otorgar(idPersona, finalidad, VERSION, diasAtras(120 - i * 7)));
    agregarMiembro(nacional.id, idPersona);
    const edad = new Date().getFullYear() - Number(fechaNacimiento.slice(0, 4));
    if (edad <= 29) agregarMiembro(joven.id, idPersona);
  });

  // Una ola ya fieldeada e ingestada, para que el cruce entre stores se vea.
  const ola = crearEncuesta(nacional.id, 'Ola 1 — Consumo de bebidas', '2026-07-15');
  convocar(ola.id, { todo_el_panel: true });
  ola.estado = 'cerrada';
  const preguntas = [
    { codigo: 'P1', texto: '¿Qué bebida consume habitualmente?', tipo: 'cerrada', opciones: { 1: 'Fernet', 2: 'Whisky', 3: 'Cerveza' }, orden: 1 },
    { codigo: 'P2', texto: '¿Por qué la elige?', tipo: 'abierta', orden: 2 },
  ];
  const respuestasDemo = ['Fernet', 'Cerveza', 'Whisky'];
  const razones = ['Porque es amargo y me gusta', 'Es lo que toman mis amigos', 'Por la calidad'];
  ingestar(ola.id, {
    preguntas,
    filas: bd.participaciones
      .filter((p) => p.encuesta_id === ola.id)
      .map((p, i) => ({
        id_en_origen: bd.alias.find((a) => a.id_persona === p.id_persona).id_en_origen,
        P1: String((i % 3) + 1),
        P2: i % 2 === 0 ? razones[i % 3] : '',
      })),
  });

  // Una ola en campo, para poder convocar desde la interfaz.
  crearEncuesta(joven.id, 'Ola 2 — Hábitos digitales', '2026-09-20');

  // Un alta ambigua esperando decisión humana (caso 3 del dedup).
  const homonimo = bd.personas[3];
  bd.revisiones.push({
    id: siguiente('revision'),
    motivo: 'nombre_fecha_nacimiento',
    estado: 'pendiente',
    creado_en: diasAtras(2),
    datos: {
      persona: {
        nombre: homonimo.nombre, fecha_nacimiento: homonimo.fecha_nacimiento,
        sexo: 'M', localidad: 'Rocha',
      },
      consentimientos: [{ finalidad: 'contacto_participacion', version_texto: VERSION }],
      panel_id: nacional.id,
    },
    candidatos: [{ id_persona: homonimo.id_persona, nombre: homonimo.nombre, localidad: homonimo.localidad }],
  });
}

/* ── Operaciones del store local ────────────────────────────────── */

function crearPanel(nombre, descripcion) {
  const panel = {
    id: siguiente('panel'), nombre, descripcion,
    estado: 'activo', creado_en: ahora(),
  };
  bd.paneles.push(panel);
  return panel;
}

function otorgar(idPersona, finalidad, versionTexto, cuando) {
  const fila = {
    id: siguiente('consentimiento'), id_persona: idPersona, finalidad,
    panel_id: null, estado: 'vigente', version_texto: versionTexto,
    otorgado_en: cuando || ahora(), retirado_en: null,
  };
  bd.consentimientos.push(fila);
  return fila;
}

const vigente = (idPersona, finalidad) => bd.consentimientos.some(
  (c) => c.id_persona === idPersona && c.finalidad === finalidad && c.estado === 'vigente');

function agregarMiembro(panelId, idPersona) {
  const existente = bd.membresias.find((m) => m.panel_id === panelId && m.id_persona === idPersona);
  if (existente) {
    existente.estado = 'activo';
    existente.fecha_baja = null;
    return existente;
  }
  const fila = {
    panel_id: panelId, id_persona: idPersona, estado: 'activo',
    fecha_alta: ahora(), fecha_baja: null,
  };
  bd.membresias.push(fila);
  return fila;
}

function crearEncuesta(panelId, nombre, fechaCampo) {
  const fila = {
    id: siguiente('encuesta'), panel_id: panelId, nombre,
    fecha_campo: fechaCampo || null, estado: 'borrador',
    ref_estudio: uuid(), creado_en: ahora(),
  };
  bd.encuestas.push(fila);
  return fila;
}

function convocar(encuestaId, cuerpo) {
  const encuesta = bd.encuestas.find((e) => e.id === encuestaId);
  let ids = cuerpo.ids_persona || [];
  if (cuerpo.todo_el_panel) {
    ids = bd.membresias
      .filter((m) => m.panel_id === encuesta.panel_id && m.estado === 'activo')
      .map((m) => m.id_persona);
  }
  const habilitadas = ids.filter((id) => vigente(id, 'contacto_participacion'));
  const bloqueadas = ids.filter((id) => !vigente(id, 'contacto_participacion'));
  let nuevas = 0;
  habilitadas.forEach((idPersona) => {
    if (bd.participaciones.some((p) => p.encuesta_id === encuestaId && p.id_persona === idPersona)) return;
    bd.participaciones.push({
      encuesta_id: encuestaId, id_persona: idPersona, convocado_en: ahora(),
      respondio: false, respondio_en: null, calidad_estado: 'pendiente',
    });
    nuevas++;
  });
  if (encuesta.estado === 'borrador' && habilitadas.length) encuesta.estado = 'en_campo';
  return {
    encuesta_id: encuestaId, convocados_nuevos: nuevas,
    convocados_total: habilitadas.length, sin_consentimiento: bloqueadas,
  };
}

/* ── Ingesta: lo único que cruza al "store remoto" es el id_persona ── */

function ingestar(encuestaId, cuerpo) {
  const encuesta = bd.encuestas.find((e) => e.id === encuestaId);
  const preguntas = cuerpo.preguntas || [];
  const filas = cuerpo.filas || [];
  const columnaId = cuerpo.columna_id || 'id_en_origen';

  const mapa = {};
  bd.participaciones.filter((p) => p.encuesta_id === encuestaId).forEach((p) => {
    const alias = bd.alias.find((a) => a.id_persona === p.id_persona);
    if (alias) mapa[alias.id_en_origen] = p.id_persona;
  });

  let cuestionario = bd.remoto.cuestionarios.find((c) => c.ref_estudio === encuesta.ref_estudio);
  if (!cuestionario) {
    cuestionario = {
      id: siguiente('remoto'), nombre: encuesta.nombre,
      fecha_campo: encuesta.fecha_campo, ref_estudio: encuesta.ref_estudio,
    };
    bd.remoto.cuestionarios.push(cuestionario);
  }
  preguntas.forEach((p) => {
    if (!bd.remoto.preguntas.some((q) => q.cuestionario_id === cuestionario.id && q.codigo === p.codigo)) {
      bd.remoto.preguntas.push({ id: siguiente('remoto'), cuestionario_id: cuestionario.id, ...p });
    }
  });

  const sinMapear = [];
  const sinConsentimiento = new Set();
  let escritas = 0;

  filas.forEach((fila) => {
    const idOrigen = String(fila[columnaId] || '').trim();
    const idPersona = mapa[idOrigen];
    if (!idPersona) { if (idOrigen) sinMapear.push(idOrigen); return; }
    if (!vigente(idPersona, 'uso_semantico')) { sinConsentimiento.add(idPersona); return; }

    let individuo = bd.remoto.individuos.find((i) => i.id_persona === idPersona);
    if (!individuo) {
      individuo = { id: siguiente('remoto'), id_persona: idPersona };
      bd.remoto.individuos.push(individuo);
    }
    preguntas.forEach((pregunta) => {
      const crudo = fila[pregunta.codigo];
      if (crudo === undefined || crudo === null || String(crudo).trim() === '') return;
      const etiqueta = pregunta.opciones?.[String(crudo).trim()] ?? String(crudo).trim();
      const preguntaRemota = bd.remoto.preguntas.find(
        (q) => q.cuestionario_id === cuestionario.id && q.codigo === pregunta.codigo);
      const existente = bd.remoto.respuestas.find(
        (r) => r.individuo_id === individuo.id && r.pregunta_id === preguntaRemota.id);
      const texto = `${pregunta.texto} → ${etiqueta}`;
      if (existente) {
        existente.valor_texto = etiqueta;
        existente.texto_embebido = texto;
      } else {
        bd.remoto.respuestas.push({
          individuo_id: individuo.id, pregunta_id: preguntaRemota.id,
          valor_texto: etiqueta, texto_embebido: texto, embedding: '[…1024 dims…]',
        });
      }
      escritas++;
    });
    const participacion = bd.participaciones.find(
      (p) => p.encuesta_id === encuestaId && p.id_persona === idPersona);
    if (participacion) { participacion.respondio = true; participacion.respondio_en = ahora(); }
  });

  return {
    encuesta_id: encuestaId, ref_estudio: encuesta.ref_estudio,
    respuestas_escritas: escritas,
    personas: bd.remoto.individuos.length,
    preguntas: preguntas.length,
    sin_mapear: [...new Set(sinMapear)],
    sin_consentimiento: [...sinConsentimiento],
  };
}

/* ── Vistas ─────────────────────────────────────────────────────── */

const tramoEtario = (fechaNacimiento) => {
  if (!fechaNacimiento) return null;
  const edad = Math.floor((Date.now() - new Date(fechaNacimiento).getTime()) / 31557600000);
  if (edad < 18) return '<18';
  if (edad < 25) return '18-24';
  if (edad < 35) return '25-34';
  if (edad < 45) return '35-44';
  if (edad < 55) return '45-54';
  if (edad < 65) return '55-64';
  return '65+';
};

const resumenPersona = (p) => ({
  id_persona: p.id_persona, nombre: p.nombre, documento: p.documento,
  email: p.email, sexo: p.sexo, localidad: p.localidad,
  tramo_etario: tramoEtario(p.fecha_nacimiento),
  consiente_contacto: vigente(p.id_persona, 'contacto_participacion'),
  consiente_semantico: vigente(p.id_persona, 'uso_semantico'),
  paneles: bd.membresias.filter((m) => m.id_persona === p.id_persona && m.estado === 'activo').length,
});

/* ── Router del demo ────────────────────────────────────────────── */

class ErrorDemo extends Error {
  constructor(mensaje, status = 400, detalle) {
    super(mensaje);
    this.status = status;
    this.cuerpo = { error: 'demo', mensaje, detalle };
  }
}

let sembrado = false;

export async function responder(metodo, camino, cuerpo = {}, consulta = {}) {
  if (!sembrado) { sembrar(); sembrado = true; }
  await new Promise((r) => setTimeout(r, 120)); // latencia simulada
  const partes = camino.split('/').filter(Boolean);
  const clave = `${metodo} ${camino}`;

  if (clave === 'GET /yo') {
    return { uid: 'demo', email: 'demo@equipos.com.uy', rol: 'admin', nombre: 'Modo demo' };
  }

  if (metodo === 'GET' && partes[0] === 'panelistas' && partes.length === 1) {
    const q = (consulta.q || '').toLowerCase();
    const panelId = consulta.panel_id ? Number(consulta.panel_id) : null;
    let items = bd.personas.map(resumenPersona);
    if (q) items = items.filter((p) => [p.nombre, p.email, p.documento]
      .some((v) => (v || '').toLowerCase().includes(q)));
    if (panelId) {
      const miembros = new Set(bd.membresias
        .filter((m) => m.panel_id === panelId && m.estado === 'activo')
        .map((m) => m.id_persona));
      items = items.filter((p) => miembros.has(p.id_persona));
    }
    return { total: items.length, items };
  }

  if (metodo === 'GET' && partes[0] === 'panelistas' && partes.length === 2) {
    const persona = bd.personas.find((p) => p.id_persona === partes[1]);
    if (!persona) throw new ErrorDemo('No existe la persona.', 404);
    return {
      id_persona: persona.id_persona,
      persona: {
        documento: persona.documento, nombre: persona.nombre, sexo: persona.sexo,
        fecha_nacimiento: persona.fecha_nacimiento, localidad: persona.localidad,
        email: persona.email, celular: persona.celular,
        contacto: persona.contacto, observaciones: persona.observaciones,
      },
      creado_en: persona.creado_en,
      demografia: {
        tramo_etario: tramoEtario(persona.fecha_nacimiento),
        edad: persona.fecha_nacimiento
          ? Math.floor((Date.now() - new Date(persona.fecha_nacimiento).getTime()) / 31557600000)
          : null,
      },
      paneles: bd.membresias.filter((m) => m.id_persona === persona.id_persona).map((m) => ({
        panel_id: m.panel_id,
        nombre: bd.paneles.find((p) => p.id === m.panel_id)?.nombre,
        estado: m.estado, fecha_alta: m.fecha_alta, fecha_baja: m.fecha_baja,
      })),
      consentimientos: bd.consentimientos
        .filter((c) => c.id_persona === persona.id_persona)
        .sort((a, b) => b.otorgado_en.localeCompare(a.otorgado_en)),
      participacion: (() => {
        const suyas = bd.participaciones.filter((p) => p.id_persona === persona.id_persona);
        return {
          convocatorias: suyas.length,
          respondidas: suyas.filter((p) => p.respondio).length,
          ultimo_contacto: suyas.map((p) => p.convocado_en).sort().pop() || null,
        };
      })(),
    };
  }

  if (clave === 'POST /panelistas') {
    const datos = cuerpo.persona || {};
    if (!(cuerpo.consentimientos || []).length) {
      throw new ErrorDemo('El alta exige al menos un consentimiento.', 400);
    }
    // Dedup, mismo orden que el backend.
    let existente = datos.documento
      && bd.personas.find((p) => p.documento === datos.documento);
    let motivo = existente ? 'documento' : null;
    if (!existente && datos.email) {
      existente = bd.personas.find(
        (p) => (p.email || '').toLowerCase() === datos.email.toLowerCase());
      motivo = existente ? 'email' : null;
    }
    if (!existente && !datos.documento && !datos.email && datos.nombre && datos.fecha_nacimiento) {
      const candidatos = bd.personas.filter(
        (p) => (p.nombre || '').toLowerCase() === datos.nombre.toLowerCase()
          && p.fecha_nacimiento === datos.fecha_nacimiento);
      if (candidatos.length) {
        const revision = {
          id: siguiente('revision'), motivo: 'nombre_fecha_nacimiento',
          estado: 'pendiente', creado_en: ahora(),
          datos: { persona: datos, consentimientos: cuerpo.consentimientos, panel_id: cuerpo.panel_id },
          candidatos: candidatos.map((c) => ({
            id_persona: c.id_persona, nombre: c.nombre, localidad: c.localidad,
          })),
        };
        bd.revisiones.push(revision);
        return {
          estado: 'revision', revision_id: revision.id,
          motivo: revision.motivo, candidatos: revision.candidatos,
        };
      }
    }
    let idPersona;
    if (existente) {
      idPersona = existente.id_persona;
      Object.entries(datos).forEach(([campo, valor]) => {
        if (valor && !existente[campo]) existente[campo] = valor;
      });
    } else {
      idPersona = uuid();
      bd.personas.push({ id_persona: idPersona, ...datos, creado_en: ahora() });
    }
    cuerpo.consentimientos.forEach((c) => otorgar(idPersona, c.finalidad, c.version_texto));
    if (cuerpo.panel_id) agregarMiembro(Number(cuerpo.panel_id), idPersona);
    return {
      estado: existente ? 'reutilizada' : 'creada',
      id_persona: idPersona, motivo_dedup: motivo,
      consentimientos: [], campos_completados: [],
    };
  }

  if (clave === 'GET /revisiones') {
    const filtro = consulta.estado || 'pendiente';
    return { items: bd.revisiones.filter((r) => !filtro || r.estado === filtro) };
  }

  if (metodo === 'POST' && partes[0] === 'revisiones' && partes[2] === 'resolver') {
    const revision = bd.revisiones.find((r) => r.id === Number(partes[1]));
    if (!revision) throw new ErrorDemo('No existe el alta en revisión.', 404);
    if (revision.estado !== 'pendiente') throw new ErrorDemo('Ya fue resuelta.', 409);
    if (cuerpo.decision === 'descartar') {
      revision.estado = 'descartada';
      return { estado: 'descartada', revision_id: revision.id };
    }
    let idPersona = cuerpo.id_persona;
    if (cuerpo.decision === 'crear') {
      idPersona = uuid();
      bd.personas.push({ id_persona: idPersona, ...revision.datos.persona, creado_en: ahora() });
      revision.estado = 'creada';
    } else {
      revision.estado = 'fusionada';
    }
    (revision.datos.consentimientos || []).forEach(
      (c) => otorgar(idPersona, c.finalidad, c.version_texto));
    if (revision.datos.panel_id) agregarMiembro(Number(revision.datos.panel_id), idPersona);
    revision.id_persona = idPersona;
    return { estado: revision.estado, revision_id: revision.id, id_persona: idPersona };
  }

  if (clave === 'GET /paneles') {
    return {
      items: bd.paneles.map((p) => ({
        ...p,
        miembros: bd.membresias.filter((m) => m.panel_id === p.id && m.estado === 'activo').length,
        encuestas: bd.encuestas.filter((e) => e.panel_id === p.id).length,
      })),
    };
  }

  if (clave === 'POST /paneles') return { ...crearPanel(cuerpo.nombre, cuerpo.descripcion), miembros: 0 };

  if (metodo === 'GET' && partes[0] === 'paneles' && partes[2] === 'miembros') {
    const panelId = Number(partes[1]);
    const filtro = consulta.estado || 'activo';
    return {
      items: bd.membresias
        .filter((m) => m.panel_id === panelId && (!filtro || m.estado === filtro))
        .map((m) => {
          const persona = bd.personas.find((p) => p.id_persona === m.id_persona);
          return { ...resumenPersona(persona), estado: m.estado, fecha_alta: m.fecha_alta };
        }),
    };
  }

  if (metodo === 'POST' && partes[0] === 'paneles' && partes[2] === 'miembros') {
    const panelId = Number(partes[1]);
    return { agregados: (cuerpo.ids_persona || []).map((id) => agregarMiembro(panelId, id)) };
  }

  if (metodo === 'DELETE' && partes[0] === 'paneles' && partes[2] === 'miembros') {
    const membresia = bd.membresias.find(
      (m) => m.panel_id === Number(partes[1]) && m.id_persona === partes[3]);
    if (!membresia) throw new ErrorDemo('No tiene membresía activa.', 404);
    membresia.estado = 'baja';
    membresia.fecha_baja = ahora();
    return { panel_id: membresia.panel_id, id_persona: membresia.id_persona, estado: 'baja' };
  }

  if (metodo === 'PATCH' && partes[0] === 'paneles') {
    const panel = bd.paneles.find((p) => p.id === Number(partes[1]));
    panel.estado = cuerpo.estado;
    return panel;
  }

  if (clave === 'GET /encuestas') {
    const panelId = consulta.panel_id ? Number(consulta.panel_id) : null;
    return {
      items: bd.encuestas
        .filter((e) => !panelId || e.panel_id === panelId)
        .map((e) => ({
          ...e,
          panel: bd.paneles.find((p) => p.id === e.panel_id)?.nombre,
          convocados: bd.participaciones.filter((p) => p.encuesta_id === e.id).length,
          respondieron: bd.participaciones.filter((p) => p.encuesta_id === e.id && p.respondio).length,
        })),
    };
  }

  if (clave === 'POST /encuestas') {
    return crearEncuesta(Number(cuerpo.panel_id), cuerpo.nombre, cuerpo.fecha_campo);
  }

  if (metodo === 'GET' && partes[0] === 'encuestas' && partes.length === 2) {
    return bd.encuestas.find((e) => e.id === Number(partes[1]));
  }

  if (metodo === 'PATCH' && partes[0] === 'encuestas') {
    const encuesta = bd.encuestas.find((e) => e.id === Number(partes[1]));
    encuesta.estado = cuerpo.estado;
    return encuesta;
  }

  if (metodo === 'POST' && partes[2] === 'convocatoria') {
    return convocar(Number(partes[1]), cuerpo);
  }

  if (metodo === 'GET' && partes[2] === 'participacion') {
    return {
      items: bd.participaciones
        .filter((p) => p.encuesta_id === Number(partes[1]))
        .map((p) => {
          const persona = bd.personas.find((x) => x.id_persona === p.id_persona);
          return {
            ...p,
            nombre: persona?.nombre, email: persona?.email,
            id_en_origen: bd.alias.find((a) => a.id_persona === p.id_persona)?.id_en_origen,
            consiente_semantico: vigente(p.id_persona, 'uso_semantico'),
          };
        }),
    };
  }

  if (metodo === 'POST' && partes[2] === 'ingesta') return ingestar(Number(partes[1]), cuerpo);

  if (metodo === 'GET' && partes[2] === 'cruce') {
    const encuesta = bd.encuestas.find((e) => e.id === Number(partes[1]));
    const cuestionario = bd.remoto.cuestionarios.find((c) => c.ref_estudio === encuesta.ref_estudio);
    const preguntasDe = bd.remoto.preguntas.filter((q) => q.cuestionario_id === cuestionario?.id);
    const idsPregunta = new Set(preguntasDe.map((q) => q.id));
    const respuestas = bd.remoto.respuestas.filter((r) => idsPregunta.has(r.pregunta_id));
    const convocados = bd.participaciones.filter((p) => p.encuesta_id === encuesta.id).length;
    return {
      encuesta_id: encuesta.id, ref_estudio: encuesta.ref_estudio,
      local: { convocados },
      remoto: {
        ref_estudio: encuesta.ref_estudio, nombre: cuestionario?.nombre || null,
        preguntas: preguntasDe.length, respuestas: respuestas.length,
        individuos: new Set(respuestas.map((r) => r.individuo_id)).size,
      },
      cruce_ok: true,
    };
  }

  if (metodo === 'POST' && partes[0] === 'consentimientos' && partes[2] === 'retiro') {
    const idPersona = partes[1];
    const finalidad = cuerpo.finalidad || 'todas';
    bd.consentimientos
      .filter((c) => c.id_persona === idPersona && c.estado === 'vigente'
        && (finalidad === 'todas' || c.finalidad === finalidad))
      .forEach((c) => { c.estado = 'retirado'; c.retirado_en = ahora(); });

    let bajas = 0;
    if (finalidad === 'todas' || finalidad === 'contacto_participacion') {
      bd.membresias.filter((m) => m.id_persona === idPersona && m.estado === 'activo')
        .forEach((m) => { m.estado = 'baja'; m.fecha_baja = ahora(); bajas++; });
    }
    let borradasRemoto = 0;
    if (finalidad === 'todas' || finalidad === 'uso_semantico') {
      const individuo = bd.remoto.individuos.find((i) => i.id_persona === idPersona);
      if (individuo) {
        borradasRemoto = bd.remoto.respuestas.filter((r) => r.individuo_id === individuo.id).length;
        bd.remoto.respuestas = bd.remoto.respuestas.filter((r) => r.individuo_id !== individuo.id);
        bd.remoto.individuos = bd.remoto.individuos.filter((i) => i.id_persona !== idPersona);
      }
    }
    let piiBorrada = false;
    if (finalidad === 'todas') {
      bd.personas = bd.personas.filter((p) => p.id_persona !== idPersona);
      bd.alias = bd.alias.filter((a) => a.id_persona !== idPersona);
      bd.membresias = bd.membresias.filter((m) => m.id_persona !== idPersona);
      bd.consentimientos = bd.consentimientos.filter((c) => c.id_persona !== idPersona);
      bd.participaciones = bd.participaciones.filter((p) => p.id_persona !== idPersona);
      bd.borradas.push({
        id_persona: idPersona, motivo: 'retiro_consentimiento', finalidad,
        borrado_local_en: ahora(), borrado_remoto_en: ahora(), remoto_error: null,
      });
      piiBorrada = true;
    }
    return {
      id_persona: idPersona, finalidad_retirada: finalidad,
      membresias_dadas_de_baja: bajas, pii_borrada: piiBorrada,
      remoto: { estado: 'ok', respuestas_borradas: borradasRemoto },
    };
  }

  if (metodo === 'POST' && partes[0] === 'consentimientos' && partes.length === 2) {
    return otorgar(partes[1], cuerpo.finalidad, cuerpo.version_texto);
  }

  if (clave === 'GET /cumplimiento/pendientes') {
    return { items: bd.borradas.filter((b) => !b.borrado_remoto_en) };
  }
  if (clave === 'POST /cumplimiento/reintentar') return { resultados: [] };
  if (clave === 'GET /auditoria/pii') return { limpio: true, hallazgos: [] };

  throw new ErrorDemo(`El modo demo no implementa ${clave}.`, 404);
}

/* Lo que el "store remoto" tiene guardado. La página de cumplimiento lo
   muestra para hacer visible el invariante: solo id_persona, nunca PII. */
export function espiarRemoto() {
  return {
    cuestionarios: bd.remoto.cuestionarios.length,
    individuos: bd.remoto.individuos.map((i) => i.id_persona),
    respuestas: bd.remoto.respuestas.length,
    muestra: bd.remoto.respuestas.slice(0, 5).map((r) => ({
      id_persona: bd.remoto.individuos.find((i) => i.id === r.individuo_id)?.id_persona,
      texto_embebido: r.texto_embebido,
      embedding: r.embedding,
    })),
  };
}
