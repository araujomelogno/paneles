/* Backend en memoria para MODO DEMO.

   Se usa solo cuando el proyecto Firebase todavía no está configurado.
   Reproduce las reglas que importan de la Fase 1 —dedup, gate de
   consentimiento, membresías N:M, cascada de baja, puente entre stores por
   `ref_estudio`— para poder recorrer la interfaz de verdad, pero NO es el
   sistema: no persiste nada, no hay Postgres y no hay embeddings.

   El "store semántico" de acá es un objeto aparte a propósito: se ve que solo
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
  // Store de bóveda: PII + paneles.
  personas: [],
  alias: [],
  paneles: [],
  membresias: [],
  consentimientos: [],
  encuestas: [],
  participaciones: [],
  revisiones: [],
  borradas: [],
  // Store semántico: SOLO id_persona, ref_estudio y contenido despersonalizado.
  semantica: { cuestionarios: [], individuos: [], preguntas: [], respuestas: [] },
  // Fase 2
  objetivos: [],          // universo de referencia por panel/dimensión
  guardadas: [],          // definiciones de consulta reutilizables
  usuarios: [],           // padrón de personal de Equipos (nunca panelistas)
  auditoriaUsuarios: [],
  reidentificaciones: [],
  secuencias: {
    panel: 1, encuesta: 1, revision: 1, consentimiento: 1, semantica: 1,
    guardada: 1, auditoria: 1, reident: 1,
  },
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
  /* Las respuestas no son al azar: hay una de cada caso que la consulta
     semántica tiene que resolver. La tercera —«no me gusta el fernet, lo
     detesto»— es la de polaridad opuesta, la que sale primero en el recall y
     tiene que quedar afuera del ranking final. */
  const respuestas = [
    ['1', 'Porque me encanta el fernet, lo tomo siempre'],
    ['1', 'Es lo que tomamos en casa desde siempre'],
    ['1', 'No me gusta el fernet, lo detesto, lo tomo por compromiso'],
    ['2', 'Por la calidad del whisky'],
    ['3', 'Es lo que toman mis amigos'],
    ['1', 'Me encanta, es mi bebida favorita'],
    ['2', ''],
    ['3', 'Prefiero algo liviano'],
    ['1', 'Nunca me gustó mucho, pero es lo que hay'],
    ['3', 'Por el precio'],
  ];
  ingestar(ola.id, {
    preguntas,
    filas: bd.participaciones
      .filter((p) => p.encuesta_id === ola.id)
      .map((p, i) => ({
        id_en_origen: bd.alias.find((a) => a.id_persona === p.id_persona).id_en_origen,
        P1: respuestas[i % respuestas.length][0],
        P2: respuestas[i % respuestas.length][1],
      })),
  });

  // Una ola en campo, para poder convocar desde la interfaz.
  crearEncuesta(joven.id, 'Ola 2 — Hábitos digitales', '2026-09-20');

  // Un universo de referencia cargado en un panel y no en el otro: así se ve
  // la diferencia entre «no hay brecha» y «no se puede calcular la brecha».
  [['F', 0.52], ['M', 0.48]].forEach(([categoria, proporcion]) => {
    bd.objetivos.push({ panel_id: nacional.id, dimension: 'sexo', categoria, proporcion_objetivo: proporcion });
  });

  // Padrón de la app. Personal de Equipos; acá no va ningún panelista.
  [
    ['Modo demo', 'demo@equipos.com.uy', 'admin'],
    ['Ana Operaciones', 'ana.ops@equipos.com.uy', 'operaciones'],
    ['Bruno Analista', 'bruno@equipos.com.uy', 'analista'],
    ['Clara DPO', 'clara.dpo@equipos.com.uy', 'dpo'],
  ].forEach(([nombre, email, rol], i) => {
    bd.usuarios.push({
      uid: i === 0 ? 'demo' : `uid-demo-${i}`, nombre, email, rol, activo: true,
    });
  });

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

/* ── Operaciones del store de bóveda ────────────────────────────────── */

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

/* ── Ingesta: lo único que cruza al "store semántico" es el id_persona ── */

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

  let cuestionario = bd.semantica.cuestionarios.find((c) => c.ref_estudio === encuesta.ref_estudio);
  if (!cuestionario) {
    cuestionario = {
      id: siguiente('semantica'), nombre: encuesta.nombre,
      fecha_campo: encuesta.fecha_campo, ref_estudio: encuesta.ref_estudio,
    };
    bd.semantica.cuestionarios.push(cuestionario);
  }
  preguntas.forEach((p) => {
    if (!bd.semantica.preguntas.some((q) => q.cuestionario_id === cuestionario.id && q.codigo === p.codigo)) {
      bd.semantica.preguntas.push({ id: siguiente('semantica'), cuestionario_id: cuestionario.id, ...p });
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

    let individuo = bd.semantica.individuos.find((i) => i.id_persona === idPersona);
    if (!individuo) {
      individuo = { id: siguiente('semantica'), id_persona: idPersona };
      bd.semantica.individuos.push(individuo);
    }
    preguntas.forEach((pregunta) => {
      const crudo = fila[pregunta.codigo];
      if (crudo === undefined || crudo === null || String(crudo).trim() === '') return;
      const etiqueta = pregunta.opciones?.[String(crudo).trim()] ?? String(crudo).trim();
      const preguntaSemantica = bd.semantica.preguntas.find(
        (q) => q.cuestionario_id === cuestionario.id && q.codigo === pregunta.codigo);
      const existente = bd.semantica.respuestas.find(
        (r) => r.individuo_id === individuo.id && r.pregunta_id === preguntaSemantica.id);
      const texto = `${pregunta.texto} → ${etiqueta}`;
      if (existente) {
        existente.valor_texto = etiqueta;
        existente.texto_embebido = texto;
      } else {
        bd.semantica.respuestas.push({
          individuo_id: individuo.id, pregunta_id: preguntaSemantica.id,
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
    personas: bd.semantica.individuos.length,
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
    // Las páginas leen `error.detalle` (lo pone `ErrorApi` en api.js). En modo
    // demo el error viaja tal cual, así que se expone en el mismo lugar.
    this.detalle = detalle;
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
      alias: bd.alias
        .filter((a) => a.id_persona === persona.id_persona)
        .map((a) => ({ origen: a.origen, id_en_origen: a.id_en_origen })),
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

  if (metodo === 'PATCH' && partes[0] === 'panelistas' && partes.length === 2) {
    const persona = bd.personas.find((p) => p.id_persona === partes[1]);
    if (!persona) throw new ErrorDemo('No existe la persona.', 404);
    // Las claves de dedup solo se completan cuando están vacías.
    for (const campo of ['documento', 'email']) {
      if (!(campo in cuerpo)) continue;
      const nuevo = typeof cuerpo[campo] === 'string'
        ? (cuerpo[campo].trim() || null) : cuerpo[campo];
      const antes = persona[campo] ?? null;
      if (antes === null || nuevo === antes) continue;
      if (nuevo === null) {
        throw new ErrorDemo(
          `No se puede borrar el ${campo}: es una de las claves con las que el ` +
          'sistema reconoce a la persona.', 400);
      }
      throw new ErrorDemo(
        `El ${campo} ya está cargado y no se puede cambiar: es una de las claves ` +
        'con las que el sistema reconoce a la persona. Se puede completar cuando ' +
        'está vacío, no reemplazar.', 400);
    }
    // Y completar con un valor que ya es de otro choca.
    for (const campo of ['documento', 'email']) {
      const nuevo = (cuerpo[campo] || '').trim();
      if (!nuevo || (persona[campo] ?? null) !== null) continue;
      const otro = bd.personas.find((x) => x.id_persona !== persona.id_persona
        && (campo === 'email'
            ? (x.email || '').toLowerCase() === nuevo.toLowerCase()
            : x[campo] === nuevo));
      if (otro) {
        throw new ErrorDemo(
          `Ya hay otro panelista con ese ${campo}. Si es la misma persona, hay ` +
          'que unificar los dos registros.', 409);
      }
    }
    const modificados = [];
    Object.entries(cuerpo).forEach(([campo, valor]) => {
      const limpio = typeof valor === 'string' ? (valor.trim() || null) : valor;
      if (limpio !== (persona[campo] ?? null)) { persona[campo] = limpio; modificados.push(campo); }
    });
    return { id_persona: persona.id_persona, campos_modificados: modificados.sort() };
  }

  if (metodo === 'POST' && partes[0] === 'panelistas' && partes[2] === 'alias') {
    const origen = (cuerpo.origen || '').trim();
    const idEnOrigen = (cuerpo.id_en_origen || '').trim();
    if (!origen || !idEnOrigen) throw new ErrorDemo('Hacen falta el origen y el id.', 400);
    const duenio = bd.alias.find((a) => a.origen === origen && a.id_en_origen === idEnOrigen);
    if (duenio && duenio.id_persona !== partes[1]) {
      throw new ErrorDemo(
        `El id «${idEnOrigen}» de ${origen} ya está asignado a otro panelista.`, 409);
    }
    if (!duenio) bd.alias.push({ id_persona: partes[1], origen, id_en_origen: idEnOrigen });
    return { origen, id_en_origen: idEnOrigen };
  }

  if (metodo === 'DELETE' && partes[0] === 'panelistas' && partes[2] === 'alias') {
    const origen = decodeURIComponent(partes[3] || '');
    const idEnOrigen = decodeURIComponent(partes[4] || '');
    const antes = bd.alias.length;
    bd.alias = bd.alias.filter((a) => !(
      a.id_persona === partes[1] && a.origen === origen && a.id_en_origen === idEnOrigen));
    if (bd.alias.length === antes) throw new ErrorDemo('La persona no tiene ese alias.', 404);
    return { origen, id_en_origen: idEnOrigen, estado: 'borrado' };
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

  if (metodo === 'GET' && partes[0] === 'paneles' && partes.length === 2) {
    const panel = bd.paneles.find((p) => p.id === Number(partes[1]));
    if (!panel) throw new ErrorDemo('No existe el panel.', 404);
    return panel;
  }

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
    if (!panel) throw new ErrorDemo('No existe el panel.', 404);
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
    const encuesta = bd.encuestas.find((e) => e.id === Number(partes[1]));
    if (!encuesta) throw new ErrorDemo('No existe la encuesta.', 404);
    return encuesta;
  }

  if (metodo === 'PATCH' && partes[0] === 'encuestas') {
    const encuesta = bd.encuestas.find((e) => e.id === Number(partes[1]));
    if (!encuesta) throw new ErrorDemo('No existe la encuesta.', 404);
    encuesta.estado = cuerpo.estado;
    return encuesta;
  }

  if (metodo === 'POST' && partes[0] === 'encuestas' && partes[2] === 'convocatoria') {
    return convocar(Number(partes[1]), cuerpo);
  }

  if (metodo === 'GET' && partes[0] === 'encuestas' && partes[2] === 'participacion') {
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

  if (metodo === 'POST' && partes[0] === 'encuestas' && partes[2] === 'ingesta') {
    return ingestar(Number(partes[1]), cuerpo);
  }

  if (metodo === 'GET' && partes[0] === 'encuestas' && partes[2] === 'cruce') {
    const encuesta = bd.encuestas.find((e) => e.id === Number(partes[1]));
    const cuestionario = bd.semantica.cuestionarios.find((c) => c.ref_estudio === encuesta.ref_estudio);
    const preguntasDe = bd.semantica.preguntas.filter((q) => q.cuestionario_id === cuestionario?.id);
    const idsPregunta = new Set(preguntasDe.map((q) => q.id));
    const respuestas = bd.semantica.respuestas.filter((r) => idsPregunta.has(r.pregunta_id));
    const convocados = bd.participaciones.filter((p) => p.encuesta_id === encuesta.id).length;
    return {
      encuesta_id: encuesta.id, ref_estudio: encuesta.ref_estudio,
      boveda: { convocados },
      semantica: {
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
    let borradasSemantica = 0;
    if (finalidad === 'todas' || finalidad === 'uso_semantico') {
      const individuo = bd.semantica.individuos.find((i) => i.id_persona === idPersona);
      if (individuo) {
        borradasSemantica = bd.semantica.respuestas.filter((r) => r.individuo_id === individuo.id).length;
        bd.semantica.respuestas = bd.semantica.respuestas.filter((r) => r.individuo_id !== individuo.id);
        bd.semantica.individuos = bd.semantica.individuos.filter((i) => i.id_persona !== idPersona);
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
        borrado_local_en: ahora(), borrado_semantica_en: ahora(), semantica_error: null,
      });
      piiBorrada = true;
    }
    return {
      id_persona: idPersona, finalidad_retirada: finalidad,
      membresias_dadas_de_baja: bajas, pii_borrada: piiBorrada,
      semantica: { estado: 'ok', respuestas_borradas: borradasSemantica },
    };
  }

  if (metodo === 'POST' && partes[0] === 'consentimientos' && partes.length === 2) {
    return otorgar(partes[1], cuerpo.finalidad, cuerpo.version_texto);
  }

  if (clave === 'GET /cumplimiento/pendientes') {
    return { items: bd.borradas.filter((b) => !b.borrado_semantica_en) };
  }
  if (clave === 'POST /cumplimiento/reintentar') return { resultados: [] };
  if (clave === 'GET /auditoria/pii') return { limpio: true, hallazgos: [] };

  /* ══════════════════════════════════════════════════════════════
     Fase 2
     ══════════════════════════════════════════════════════════════ */

  if (clave === 'POST /consultas') return correrConsulta(cuerpo);

  if (clave === 'GET /consultas/guardadas') {
    const panelId = consulta.panel_id ? Number(consulta.panel_id) : null;
    return { items: bd.guardadas.filter((g) => !panelId || g.panel_id === panelId) };
  }

  if (clave === 'POST /consultas/guardadas') {
    const nombre = (cuerpo.nombre || '').trim();
    if (!nombre) throw new ErrorDemo('La consulta guardada necesita un nombre.', 400);
    const definicion = cuerpo.definicion || cuerpo;
    if (!(definicion.criterios || []).length) {
      throw new ErrorDemo('La consulta necesita al menos un criterio.', 400);
    }
    const existente = bd.guardadas.find((g) => g.nombre === nombre);
    if (existente) {
      Object.assign(existente, {
        definicion, descripcion: cuerpo.descripcion || null,
        actualizado_por: 'demo', actualizado_en: ahora(),
      });
      return existente;
    }
    const fila = {
      id: siguiente('guardada'), nombre, descripcion: cuerpo.descripcion || null,
      definicion, panel_id: definicion.panel_id || null,
      creado_por: 'demo', creado_en: ahora(),
      actualizado_por: null, actualizado_en: null,
    };
    bd.guardadas.push(fila);
    return fila;
  }

  if (metodo === 'GET' && partes[0] === 'consultas' && partes[1] === 'guardadas') {
    const fila = bd.guardadas.find((g) => g.id === Number(partes[2]));
    if (!fila) throw new ErrorDemo('No existe la consulta guardada.', 404);
    return fila;
  }

  if (metodo === 'DELETE' && partes[0] === 'consultas' && partes[1] === 'guardadas') {
    const antes = bd.guardadas.length;
    bd.guardadas = bd.guardadas.filter((g) => g.id !== Number(partes[2]));
    if (bd.guardadas.length === antes) {
      throw new ErrorDemo('No existe la consulta guardada.', 404);
    }
    return { id: Number(partes[2]), estado: 'borrada' };
  }

  if (clave === 'POST /reidentificacion') {
    const ids = cuerpo.ids_persona || [];
    if (!ids.length) throw new ErrorDemo('Hace falta al menos un id_persona.', 400);
    const items = ids
      .map((id) => bd.personas.find((p) => p.id_persona === id))
      .filter(Boolean)
      .map((p) => ({
        id_persona: p.id_persona, nombre: p.nombre, documento: p.documento,
        email: p.email, celular: p.celular, contacto: p.contacto,
        sexo: p.sexo, localidad: p.localidad,
        tramo_etario: tramoEtario(p.fecha_nacimiento),
        consiente_contacto: vigente(p.id_persona, 'contacto_participacion'),
        consiente_semantico: vigente(p.id_persona, 'uso_semantico'),
      }));
    items.forEach((p) => bd.reidentificaciones.push({
      id: siguiente('reident'), id_persona: p.id_persona,
      actor_uid: 'demo', actor_email: 'demo@equipos.com.uy',
      motivo: cuerpo.motivo || 'consulta', contexto: { ruta: 'POST /reidentificacion' },
      creado_en: ahora(),
    }));
    return {
      total: items.length, items,
      no_encontrados: ids.filter((id) => !items.some((p) => p.id_persona === id)),
    };
  }

  if (clave === 'GET /reidentificacion') {
    return { items: [...bd.reidentificaciones].reverse() };
  }

  if (metodo === 'GET' && partes[0] === 'paneles' && partes[2] === 'composicion') {
    return composicionDemo(Number(partes[1]), consulta);
  }

  if (metodo === 'GET' && partes[0] === 'paneles' && partes[2] === 'objetivo') {
    return objetivoDemo(Number(partes[1]));
  }

  if (metodo === 'PUT' && partes[0] === 'paneles' && partes[2] === 'objetivo') {
    const panelId = Number(partes[1]);
    const objetivos = cuerpo.objetivos || cuerpo.items || [];
    if (!objetivos.length) throw new ErrorDemo('No hay objetivos para cargar.', 400);
    const porDimension = {};
    objetivos.forEach((o) => {
      const proporcion = Number(o.proporcion ?? o.proporcion_objetivo);
      if (!(proporcion >= 0 && proporcion <= 1)) {
        throw new ErrorDemo(
          `La proporción de ${o.dimension}/${o.categoria} tiene que estar entre 0 y 1. `
          + 'Si viene en porcentaje, dividila por 100.', 400);
      }
      porDimension[o.dimension] = (porDimension[o.dimension] || 0) + proporcion;
    });
    for (const [dimension, suma] of Object.entries(porDimension)) {
      if (Math.abs(suma - 1) > 0.005) {
        throw new ErrorDemo(
          `Las proporciones de «${dimension}» suman ${suma.toFixed(4)} y tienen que `
          + 'sumar 1. Un universo de referencia incompleto haría que todas las brechas '
          + 'de esa dimensión estén mal.', 400,
          { dimension, suma: Number(suma.toFixed(6)) });
      }
    }
    const dimensiones = new Set(objetivos.map((o) => o.dimension));
    bd.objetivos = bd.objetivos.filter(
      (o) => o.panel_id !== panelId || !dimensiones.has(o.dimension));
    objetivos.forEach((o) => bd.objetivos.push({
      panel_id: panelId, dimension: o.dimension, categoria: String(o.categoria).trim(),
      proporcion_objetivo: Number(o.proporcion ?? o.proporcion_objetivo),
    }));
    return objetivoDemo(panelId);
  }

  if (metodo === 'DELETE' && partes[0] === 'paneles' && partes[2] === 'objetivo') {
    const panelId = Number(partes[1]);
    const dimension = consulta.dimension;
    const antes = bd.objetivos.length;
    bd.objetivos = bd.objetivos.filter(
      (o) => o.panel_id !== panelId || (dimension && o.dimension !== dimension));
    return { panel_id: panelId, dimension: dimension || null, borradas: antes - bd.objetivos.length };
  }

  if (metodo === 'GET' && partes[0] === 'paneles' && partes[2] === 'participacion') {
    return tableroDemo(Number(partes[1]));
  }

  if (clave === 'GET /participacion/olas') {
    const panelId = consulta.panel_id ? Number(consulta.panel_id) : null;
    return { items: olasDemo(panelId) };
  }

  if (clave === 'GET /usuarios') {
    return {
      total: bd.usuarios.length,
      roles: ROLES_APP,
      items: [...bd.usuarios]
        .sort((a, b) => (a.nombre || '').localeCompare(b.nombre || ''))
        .map((u) => ({
          ...u, estado: u.activo ? 'activo' : 'desactivado',
          rol_valido: ROLES_APP.includes(u.rol),
        })),
    };
  }

  if (clave === 'POST /usuarios') {
    const email = (cuerpo.email || '').trim().toLowerCase();
    if (!email || !email.includes('@')) throw new ErrorDemo(`Email inválido: ${email}`, 400);
    const rol = (cuerpo.rol || '').trim().toLowerCase();
    if (!ROLES_APP.includes(rol)) {
      throw new ErrorDemo(`Rol desconocido: ${rol}.`, 400, { roles_validos: ROLES_APP });
    }
    const existente = bd.usuarios.find((u) => u.email === email);
    const rolAnterior = existente?.rol || null;
    let usuario = existente;
    let acceso = null;
    if (!usuario) {
      usuario = { uid: `uid-demo-${bd.usuarios.length + 1}`, email, activo: true };
      bd.usuarios.push(usuario);
      acceso = {
        metodo: 'restablecimiento',
        link: `https://gestion-paneles.firebaseapp.com/__/auth/action?modo=demo&email=${encodeURIComponent(email)}`,
        mostrar_una_vez: true,
        advertencia: 'Este enlace se muestra una sola vez y no se vuelve a poder '
          + 'consultar. Pasáselo a la persona por un canal privado, o pedile que '
          + 'entre con «¿Olvidaste tu contraseña?» en el login.',
      };
    }
    usuario.nombre = (cuerpo.nombre || '').trim() || usuario.nombre || email;
    usuario.rol = rol;
    usuario.activo = true;
    const accion = acceso ? 'alta' : (rolAnterior !== rol ? 'cambio_rol' : 'actualizacion');
    auditarUsuario(accion, usuario, rolAnterior, rol);
    return {
      uid: usuario.uid, estado: acceso ? 'creado' : 'existente',
      usuario: { ...usuario, estado: 'activo', rol_valido: true },
      acceso,
    };
  }

  if (metodo === 'PATCH' && partes[0] === 'usuarios') {
    const usuario = bd.usuarios.find((u) => u.uid === partes[1]);
    if (!usuario) throw new ErrorDemo(`No hay ficha de usuario para ${partes[1]}.`, 404);
    const esUnoMismo = usuario.uid === 'demo';
    const rolAnterior = usuario.rol;
    const cambios = {};

    if (cuerpo.rol != null && cuerpo.rol !== usuario.rol) {
      if (!ROLES_APP.includes(cuerpo.rol)) {
        throw new ErrorDemo(`Rol desconocido: ${cuerpo.rol}.`, 400, { roles_validos: ROLES_APP });
      }
      if (esUnoMismo && usuario.rol === 'admin') {
        throw new ErrorDemo(
          'No podés sacarte tu propio rol de administrador. Si el último admin se '
          + 'degrada, no queda nadie que pueda dar de alta a nadie.', 403);
      }
      cambios.rol = cuerpo.rol;
    }
    if (cuerpo.activo != null && !!cuerpo.activo !== !!usuario.activo) {
      if (esUnoMismo && !cuerpo.activo) {
        throw new ErrorDemo(
          'No podés desactivar tu propio usuario: te quedarías afuera del sistema.', 403);
      }
      cambios.activo = !!cuerpo.activo;
    }
    if (cuerpo.nombre && cuerpo.nombre !== usuario.nombre) cambios.nombre = cuerpo.nombre;
    if (!Object.keys(cambios).length) {
      throw new ErrorDemo('No hay nada que cambiar en este usuario.', 409);
    }
    Object.assign(usuario, cambios);
    if ('rol' in cambios) auditarUsuario('cambio_rol', usuario, rolAnterior, usuario.rol);
    if ('activo' in cambios) {
      auditarUsuario(cambios.activo ? 'reactivacion' : 'desactivacion', usuario, rolAnterior, usuario.rol);
    }
    return {
      uid: usuario.uid, cambios,
      usuario: { ...usuario, estado: usuario.activo ? 'activo' : 'desactivado', rol_valido: true },
      vigencia: 'El cambio aplica desde la próxima operación de esa persona.',
    };
  }

  if (clave === 'GET /usuarios/auditoria') {
    return { items: [...bd.auditoriaUsuarios].reverse() };
  }

  throw new ErrorDemo(`El modo demo no implementa ${clave}.`, 404);
}

/* ══════════════════════════════════════════════════════════════════
   Fase 2 — implementaciones del demo

   El motor de verdad hace embeddings, un cross-encoder y una llamada a
   Claude. Acá no hay red: la "recuperación" es solapamiento de palabras y la
   "verificación" es un léxico de negación. La FORMA de la respuesta es la
   misma —ranking, evidencia con procedencia, excluidos, degradaciones,
   diagnóstico y estrategia de puente— porque eso es lo que la pantalla tiene
   que poder mostrar. Los números no significan nada.
   ══════════════════════════════════════════════════════════════════ */

const ROLES_APP = ['admin', 'operaciones', 'analista', 'dpo'];

const NEGADORES = ['no', 'nunca', 'jamas', 'jamás', 'tampoco', 'ni', 'nada'];
const RECHAZO = ['odio', 'detesto', 'evito', 'disgusta', 'aburre', 'horrible', 'pesimo', 'pésimo'];
const VACIAS = ['de', 'del', 'la', 'las', 'el', 'los', 'un', 'una', 'que', 'quien', 'quienes',
  'a', 'al', 'y', 'o', 'en', 'con', 'por', 'para', 'se', 'su', 'sus', 'lo', 'le', 'les',
  'me', 'mi', 'gente', 'personas', 'persona', 'es', 'son', 'esta', 'estan', 'hay'];

const sinTildes = (s) => String(s || '').normalize('NFD')
  .replace(/[\u0300-\u036f]/g, '').toLowerCase();
const fichas = (texto) => sinTildes(texto).match(/[a-z0-9ñ]+/g) || [];
const fichasUtiles = (texto) => new Set(fichas(texto).filter((p) => p.length > 2 && !VACIAS.includes(p)));
const esNegativo = (texto) => {
  const palabras = fichas(texto);
  return palabras.some((p) => NEGADORES.includes(p) || RECHAZO.includes(p));
};

function auditarUsuario(accion, usuario, rolAnterior, rolNuevo) {
  bd.auditoriaUsuarios.push({
    id: siguiente('auditoria'), accion, uid_objetivo: usuario.uid,
    email_objetivo: usuario.email, rol_anterior: rolAnterior, rol_nuevo: rolNuevo,
    actor_uid: 'demo', actor_email: 'demo@equipos.com.uy',
    detalle: {}, creado_en: ahora(),
  });
}

/* Los criterios demográficos se resuelven sobre la bóveda del demo. */
function cumpleDemografico(persona, criterio) {
  const valores = {
    sexo: persona.sexo, localidad: persona.localidad,
    tramo_etario: tramoEtario(persona.fecha_nacimiento),
    edad: persona.fecha_nacimiento
      ? Math.floor((Date.now() - new Date(persona.fecha_nacimiento).getTime()) / 31557600000)
      : null,
  };
  const actual = valores[criterio.dimension];
  const esperado = criterio.valor;
  switch (criterio.operador || 'eq') {
    case 'ne': return actual !== esperado;
    case 'in': return (esperado || []).includes(actual);
    case 'not_in': return !(esperado || []).includes(actual);
    case 'contiene': return String(actual || '').toLowerCase().includes(String(esperado).toLowerCase());
    case 'lt': return Number(actual) < Number(esperado);
    case 'lte': return Number(actual) <= Number(esperado);
    case 'gt': return Number(actual) > Number(esperado);
    case 'gte': return Number(actual) >= Number(esperado);
    default: return String(actual) === String(esperado);
  }
}

function correrConsulta(cuerpo) {
  const arranque = performance.now();
  const criterios = (cuerpo.criterios || []).map((c, orden) => (
    typeof c === 'string'
      ? { tipo: 'semantico', texto: c, peso: 1, duro: false, etiqueta: c, orden }
      : {
          ...c, orden, peso: Number(c.peso) || 1,
          tipo: c.tipo || (c.dimension ? 'demografico' : 'semantico'),
          etiqueta: c.etiqueta || c.texto || `${c.dimension} ${c.operador || 'eq'} ${c.valor}`,
          duro: c.tipo === 'demografico' || !!c.dimension ? true : !!c.duro,
        }
  ));
  if (!criterios.length) throw new ErrorDemo('La consulta necesita al menos un criterio.', 400);

  const modo = cuerpo.modo === 'laxo' ? 'laxo' : 'estricto';
  const umbral = cuerpo.umbral_distancia == null ? 0.55 : Number(cuerpo.umbral_distancia);
  const demograficos = criterios.filter((c) => c.tipo === 'demografico');
  const semanticos = criterios.filter((c) => c.tipo === 'semantico');
  const panelId = cuerpo.panel_id ? Number(cuerpo.panel_id) : null;
  const etapas = [];
  const marca = (etapa, desde, datos) => etapas.push({
    etapa, ms: Number((performance.now() - desde).toFixed(1)), ...datos,
  });

  /* Segmento: membresía + criterios demográficos. Todo del lado bóveda. */
  const enSegmento = (persona) => (
    (!panelId || bd.membresias.some((m) => m.panel_id === panelId
      && m.id_persona === persona.id_persona && m.estado === 'activo'))
    && demograficos.every((c) => cumpleDemografico(persona, c))
  );

  /* ── Consulta puramente demográfica: no se toca el store semántico ── */
  if (!semanticos.length) {
    const desde = performance.now();
    const items = bd.personas.filter((p) => enSegmento(p)
      && (!cuerpo.finalidad || vigente(p.id_persona, cuerpo.finalidad)))
      .map((p) => ({
        id_persona: p.id_persona, nombre: p.nombre, email: p.email,
        sexo: p.sexo, localidad: p.localidad,
        tramo_etario: tramoEtario(p.fecha_nacimiento),
      }));
    marca('consulta_demografica', desde, { personas: items.length });
    return {
      tipo: 'demografica', store: 'boveda', abrio_semantica: false, modo,
      criterios, panel_id: panelId, finalidad_exigida: cuerpo.finalidad || null,
      total: items.length, items, excluidos: [], degradaciones: [],
      puente: {
        estrategia: null,
        motivo: 'La consulta no tiene criterios semánticos: se resuelve entera en '
          + 'la bóveda y no se abre conexión al store semántico.',
      },
      diagnostico: {
        ms_total: Number((performance.now() - arranque).toFixed(1)),
        etapas, abrio_semantica: false,
      },
    };
  }

  /* ── Con parte semántica ── */
  const habilitadas = new Set(bd.personas
    .filter((p) => enSegmento(p) && vigente(p.id_persona, 'uso_semantico'))
    .map((p) => p.id_persona));
  const desdeSegmento = performance.now();
  marca('segmento_boveda', desdeSegmento, {
    personas: habilitadas.size, finalidad: 'uso_semantico',
  });

  const estrategia = cuerpo.estrategia_puente
    || (demograficos.length ? 'demografico_primero' : 'semantico_primero');
  const motivo = cuerpo.estrategia_puente
    ? 'Estrategia forzada en la consulta.'
    : (demograficos.length
      ? `El segmento demográfico tiene ${habilitadas.size} personas: es más selectivo `
        + 'que el corpus, así que se filtra en la bóveda y se le pasan los id_persona '
        + 'al store semántico.'
      : 'No hay criterios demográficos: no hay segmento local con el que recortar '
        + 'antes del recall.');

  const porCriterio = {};
  semanticos.forEach((criterio) => {
    const delCriterio = fichasUtiles(criterio.texto);
    const desdeRecall = performance.now();
    const pool = bd.semantica.respuestas.map((r) => {
      const individuo = bd.semantica.individuos.find((i) => i.id === r.individuo_id);
      const pregunta = bd.semantica.preguntas.find((q) => q.id === r.pregunta_id);
      const cuestionario = bd.semantica.cuestionarios.find((c) => c.id === pregunta?.cuestionario_id);
      const propias = fichasUtiles(r.texto_embebido);
      const comunes = [...delCriterio].filter((p) => propias.has(p)).length;
      const solapamiento = delCriterio.size ? comunes / delCriterio.size : 0;
      return {
        id_persona: individuo?.id_persona,
        respuesta_id: r.individuo_id * 1000 + r.pregunta_id,
        valor_texto: r.valor_texto, texto_embebido: r.texto_embebido,
        estudio: cuestionario?.nombre || null, ref_estudio: cuestionario?.ref_estudio || null,
        fecha_campo: cuestionario?.fecha_campo || null,
        pregunta_codigo: pregunta?.codigo || null, pregunta_texto: pregunta?.texto || null,
        solapamiento,
        distancia: Number((1 - solapamiento).toFixed(4)),
      };
    }).filter((c) => c.solapamiento > 0);
    marca('recall', desdeRecall, { criterio: criterio.etiqueta, crudos: pool.length });

    /* Gate: quien no tiene uso_semantico vigente no llega a ser candidato. */
    const desdeGate = performance.now();
    const delRecall = [...new Set(pool.map((c) => c.id_persona))];
    const habilitado = pool.filter((c) => habilitadas.has(c.id_persona));
    marca('gate_consentimiento', desdeGate, {
      criterio: criterio.etiqueta, personas_evaluadas: delRecall.length,
      personas_descartadas: delRecall.filter((id) => !habilitadas.has(id)).length,
    });

    /* Reranking: solapamiento con castigo de polaridad. */
    const desdeRerank = performance.now();
    habilitado.forEach((c) => {
      c.relevancia = Math.max(0, Math.min(1,
        c.solapamiento - (esNegativo(c.texto_embebido) && !esNegativo(criterio.texto) ? 2 : 0)));
    });
    marca('reranking', desdeRerank, {
      criterio: criterio.etiqueta, aplicado: true, proveedor: 'lexico (demo)',
      entrada: habilitado.length,
    });

    /* Agrupación por individuo: hasta tres evidencias, para poder ver la
       contradicción aunque no sea la mejor evidencia de la persona. */
    const desdeColapso = performance.now();
    const grupos = {};
    habilitado.sort((a, b) => b.relevancia - a.relevancia || a.respuesta_id - b.respuesta_id);
    habilitado.forEach((c) => {
      grupos[c.id_persona] = grupos[c.id_persona] || [];
      if (grupos[c.id_persona].length < 3) grupos[c.id_persona].push(c);
    });
    const ordenados = Object.entries(grupos)
      .sort((a, b) => b[1][0].relevancia - a[1][0].relevancia)
      .slice(0, Number(cuerpo.top_k) || 25);
    marca('colapso', desdeColapso, {
      criterio: criterio.etiqueta, individuos: Object.keys(grupos).length,
      top_k: ordenados.length,
    });

    /* Verificación. */
    const desdeVerificacion = performance.now();
    const hallazgos = {};
    ordenados.forEach(([idPersona, evidencias]) => {
      const juicios = evidencias.map((c) => {
        if (esNegativo(c.texto_embebido) && !esNegativo(criterio.texto)) {
          return { veredicto: 'no_cumple', razon: 'La respuesta niega o rechaza lo que pide el criterio.' };
        }
        if (c.solapamiento >= 0.34) {
          return { veredicto: 'cumple', razon: 'La respuesta afirma el tema que pide el criterio.' };
        }
        return { veredicto: 'dudoso', razon: 'La respuesta toca el tema pero no alcanza para decidir.' };
      });
      let indice = juicios.findIndex((j) => j.veredicto === 'no_cumple');
      if (indice < 0) indice = juicios.findIndex((j) => j.veredicto === 'cumple');
      if (indice < 0) indice = 0;
      hallazgos[idPersona] = {
        criterio: criterio.etiqueta, orden: criterio.orden,
        puntaje: Number(evidencias[indice].relevancia.toFixed(4)),
        distancia: evidencias[indice].distancia,
        relevancia: Number(evidencias[indice].relevancia.toFixed(4)),
        veredicto: juicios[indice].veredicto, razon: juicios[indice].razon,
        aviso_polaridad: false,
        evidencia: evidencias[indice],
      };
    });
    marca('verificacion', desdeVerificacion, {
      criterio: criterio.etiqueta, aplicada: true, proveedor: 'lexico (demo)',
      evidencias_verificadas: ordenados.reduce((s, [, e]) => s + e.length, 0),
      individuos: ordenados.length,
      cumple: Object.values(hallazgos).filter((h) => h.veredicto === 'cumple').length,
      no_cumple: Object.values(hallazgos).filter((h) => h.veredicto === 'no_cumple').length,
      dudoso: Object.values(hallazgos).filter((h) => h.veredicto === 'dudoso').length,
    });
    porCriterio[criterio.orden] = hallazgos;
  });

  /* Combinación. */
  const desdeCombinar = performance.now();
  const universo = new Set(Object.values(porCriterio).flatMap((h) => Object.keys(h)));
  const items = [];
  const excluidos = [];
  universo.forEach((idPersona) => {
    const detalle = demograficos.map((c) => ({
      criterio: c.etiqueta, tipo: 'demografico', puntaje: 1, peso: c.peso,
      veredicto: 'cumple', razon: 'Filtro demográfico aplicado en la bóveda.',
      evidencia: null,
    }));
    let excluir = null;
    semanticos.forEach((c) => {
      const hallazgo = porCriterio[c.orden]?.[idPersona];
      if (!hallazgo) {
        detalle.push({
          criterio: c.etiqueta, tipo: 'semantico', puntaje: 0, peso: c.peso,
          veredicto: 'sin_evidencia',
          razon: 'No hay ninguna respuesta suya cerca de este criterio.',
          evidencia: null,
        });
        if (c.duro || modo === 'estricto') {
          excluir = excluir || { criterio: c.etiqueta, motivo: 'sin_evidencia' };
        }
        return;
      }
      let puntaje = hallazgo.puntaje;
      if (hallazgo.veredicto === 'no_cumple') {
        puntaje = 0;
        excluir = excluir || {
          criterio: c.etiqueta, motivo: 'no_cumple',
          evidencia: hallazgo.evidencia.valor_texto,
        };
      } else if (hallazgo.veredicto === 'dudoso' && (c.duro || modo === 'estricto')) {
        excluir = excluir || { criterio: c.etiqueta, motivo: 'dudoso' };
      }
      detalle.push({ ...hallazgo, tipo: 'semantico', peso: c.peso, puntaje });
    });

    if (excluir) { excluidos.push({ id_persona: idPersona, ...excluir }); return; }

    const pesos = detalle.reduce((s, d) => s + d.peso, 0) || 1;
    const combinado = detalle.reduce((s, d) => s + d.puntaje * d.peso, 0) / pesos;
    const distancias = detalle.map((d) => d.distancia).filter((d) => d != null);
    const mejorDistancia = distancias.length ? Math.min(...distancias) : null;
    const penalizado = detalle.some((d) => d.tipo === 'semantico'
      && ['sin_evidencia', 'dudoso'].includes(d.veredicto));
    items.push({
      id_persona: idPersona,
      puntaje: Number(combinado.toFixed(4)),
      confianza: (penalizado || (mejorDistancia != null && mejorDistancia > umbral))
        ? 'baja' : 'alta',
      mejor_distancia: mejorDistancia,
      penalizado,
      criterios: detalle,
      evidencias: detalle.map((d) => d.evidencia).filter(Boolean),
    });
  });
  items.sort((a, b) => b.puntaje - a.puntaje || a.id_persona.localeCompare(b.id_persona));
  marca('combinacion', desdeCombinar, {
    modo, candidatos: items.length + excluidos.length, excluidos: excluidos.length,
  });

  return {
    tipo: demograficos.length ? 'mixta' : 'semantica',
    modo, panel_id: panelId, criterios,
    parametros: {
      top_n: Number(cuerpo.top_n) || 200, top_k: Number(cuerpo.top_k) || 25,
      umbral_distancia: umbral,
    },
    puente: {
      estrategia, motivo, personas_en_segmento: habilitadas.size, gate: 'uso_semantico',
    },
    total: items.length,
    items: items.slice(0, Number(cuerpo.limite) || 50),
    excluidos,
    degradaciones: [{
      etapa: 'modo_demo',
      proveedor: 'demo',
      motivo: 'El modo demo no tiene embeddings, cross-encoder ni API de Claude.',
      consecuencia: 'La recuperación es por solapamiento de palabras y la '
        + 'verificación por un léxico de negación. La forma del resultado es la '
        + 'misma; los números no significan nada.',
    }],
    diagnostico: {
      ms_total: Number((performance.now() - arranque).toFixed(1)),
      etapas, abrio_semantica: true,
      reranker: 'lexico (demo)', verificador: 'lexico (demo)',
    },
  };
}

function objetivoDemo(panelId) {
  const propios = bd.objetivos.filter((o) => o.panel_id === panelId);
  const dimensiones = {};
  propios.forEach((o) => {
    dimensiones[o.dimension] = dimensiones[o.dimension] || {};
    dimensiones[o.dimension][o.categoria] = o.proporcion_objetivo;
  });
  return { panel_id: panelId, dimensiones, items: propios };
}

const DIMENSIONES_DEMO = {
  sexo: (p) => p.sexo,
  tramo_etario: (p) => tramoEtario(p.fecha_nacimiento),
  localidad: (p) => p.localidad,
};

function miembrosDe(panelId, estado = 'activo') {
  return bd.membresias
    .filter((m) => m.panel_id === panelId && (!estado || m.estado === estado))
    .map((m) => bd.personas.find((p) => p.id_persona === m.id_persona))
    .filter(Boolean);
}

function composicionDemo(panelId, consulta) {
  const panel = bd.paneles.find((p) => p.id === panelId);
  if (!panel) throw new ErrorDemo('No existe el panel.', 404);
  const estado = consulta.estado || 'activo';
  const miembros = miembrosDe(panelId, estado);
  const objetivos = objetivoDemo(panelId).dimensiones;
  const pedidas = consulta.dimensiones
    ? consulta.dimensiones.split(',').map((d) => d.trim()).filter(Boolean)
    : Object.keys(DIMENSIONES_DEMO);

  const salida = {
    panel_id: panelId, estado_membresia: estado, miembros: miembros.length,
    objetivo_cargado: Object.keys(objetivos).length > 0,
    dimensiones: pedidas.map((dimension) => {
      const leer = DIMENSIONES_DEMO[dimension];
      if (!leer) throw new ErrorDemo(`Dimensión desconocida: ${dimension}.`, 400);
      const observados = {};
      miembros.forEach((p) => {
        const categoria = leer(p) || '(sin dato)';
        observados[categoria] = (observados[categoria] || 0) + 1;
      });
      const delObjetivo = objetivos[dimension] || {};
      const hay = Object.keys(delObjetivo).length > 0;
      const categorias = [...new Set([...Object.keys(observados), ...Object.keys(delObjetivo)])]
        .sort()
        .map((categoria) => {
          const n = observados[categoria] || 0;
          const proporcion = miembros.length ? n / miembros.length : 0;
          const fila = {
            categoria, observados: n,
            proporcion_observada: Number(proporcion.toFixed(4)),
            proporcion_objetivo: null, brecha: null, faltan: null,
          };
          if (hay && categoria in delObjetivo) {
            const objetivo = delObjetivo[categoria];
            const esperados = Math.round(objetivo * miembros.length);
            Object.assign(fila, {
              proporcion_objetivo: Number(objetivo.toFixed(4)),
              brecha: Number((proporcion - objetivo).toFixed(4)),
              faltan: Math.max(0, esperados - n),
              sobran: Math.max(0, n - esperados),
              esperados,
            });
          }
          return fila;
        });
      return {
        dimension, brecha_disponible: hay,
        motivo_sin_brecha: hay ? null
          : `No hay universo de referencia cargado para «${dimension}»: la composición `
            + 'es descriptiva y la brecha no se puede calcular.',
        categorias,
        disimilitud: hay
          ? Number((categorias.reduce((s, c) => s + Math.abs(c.brecha || 0), 0) / 2).toFixed(4))
          : null,
      };
    }),
  };

  if (consulta.cruce) {
    const [a, b] = consulta.cruce.split(',').map((d) => d.trim());
    if (!DIMENSIONES_DEMO[a] || !DIMENSIONES_DEMO[b] || a === b) {
      throw new ErrorDemo('El cruce necesita dos dimensiones distintas y conocidas.', 400);
    }
    const celdas = {};
    miembros.forEach((p) => {
      const clave = `${DIMENSIONES_DEMO[a](p) || '(sin dato)'}|${DIMENSIONES_DEMO[b](p) || '(sin dato)'}`;
      celdas[clave] = (celdas[clave] || 0) + 1;
    });
    salida.cruce = {
      panel_id: panelId, dimensiones: [a, b], miembros: miembros.length,
      brecha_disponible: false,
      motivo_sin_brecha: 'El universo de referencia se carga por dimensión '
        + '(marginales), no por celda cruzada: el cruce es descriptivo.',
      celdas: Object.entries(celdas).map(([clave, n]) => {
        const [va, vb] = clave.split('|');
        return {
          [a]: va, [b]: vb, observados: n,
          proporcion_observada: miembros.length ? Number((n / miembros.length).toFixed(4)) : 0,
        };
      }),
    };
  }
  return salida;
}

function olasDemo(panelId) {
  return bd.encuestas
    .filter((e) => !panelId || e.panel_id === panelId)
    .map((e) => {
      const propias = bd.participaciones.filter((p) => p.encuesta_id === e.id);
      const respondieron = propias.filter((p) => p.respondio).length;
      return {
        encuesta_id: e.id, panel_id: e.panel_id,
        panel: bd.paneles.find((p) => p.id === e.panel_id)?.nombre,
        nombre: e.nombre, fecha_campo: e.fecha_campo, estado: e.estado,
        ref_estudio: e.ref_estudio,
        convocados: propias.length, respondieron,
        tasa_respuesta: propias.length ? Number((respondieron / propias.length).toFixed(4)) : null,
        calidad_ok: propias.filter((p) => p.calidad_estado === 'ok').length,
        calidad_sospechosa: propias.filter((p) => p.calidad_estado === 'sospechoso').length,
        calidad_pendiente: propias.filter((p) => p.calidad_estado === 'pendiente').length,
        primera_convocatoria: propias.map((p) => p.convocado_en).sort()[0] || null,
        ultima_respuesta: propias.map((p) => p.respondio_en).filter(Boolean).sort().pop() || null,
      };
    });
}

function tableroDemo(panelId) {
  const panel = bd.paneles.find((p) => p.id === panelId);
  if (!panel) throw new ErrorDemo('No existe el panel.', 404);
  const olasDelPanel = new Set(bd.encuestas.filter((e) => e.panel_id === panelId).map((e) => e.id));
  const miembros = miembrosDe(panelId);

  const personas = miembros.map((p) => {
    const propias = bd.participaciones.filter(
      (x) => x.id_persona === p.id_persona && olasDelPanel.has(x.encuesta_id));
    const respuestas = propias.filter((x) => x.respondio).length;
    const ultimo = propias.map((x) => x.convocado_en).sort().pop() || null;
    const dias = ultimo ? Math.floor((Date.now() - new Date(ultimo).getTime()) / 86400000) : null;
    return {
      id_persona: p.id_persona, nombre: p.nombre, email: p.email, sexo: p.sexo,
      localidad: p.localidad, tramo_etario: tramoEtario(p.fecha_nacimiento),
      convocatorias: propias.length, respuestas,
      tasa_respuesta: propias.length ? Number((respuestas / propias.length).toFixed(4)) : null,
      calidad_sospechosa: propias.filter((x) => x.calidad_estado === 'sospechoso').length,
      ultimo_contacto: ultimo, dias_sin_contacto: dias,
      tramo_contacto: dias == null ? 'nunca'
        : dias <= 30 ? 'hasta_30_dias'
        : dias <= 90 ? '31_a_90_dias'
        : dias <= 180 ? '91_a_180_dias' : 'mas_de_180_dias',
    };
  });

  const olas = olasDemo(panelId);
  const convocados = olas.reduce((s, o) => s + o.convocados, 0);
  const respondieron = olas.reduce((s, o) => s + o.respondieron, 0);

  const distribucionContacto = {
    nunca: 0, hasta_30_dias: 0, '31_a_90_dias': 0, '91_a_180_dias': 0, mas_de_180_dias: 0,
  };
  personas.forEach((p) => { distribucionContacto[p.tramo_contacto] += 1; });

  const distribucionConvocatorias = { 0: 0, 1: 0, 2: 0, 3: 0, 4: 0, '5_o_mas': 0 };
  personas.forEach((p) => {
    distribucionConvocatorias[p.convocatorias < 5 ? String(p.convocatorias) : '5_o_mas'] += 1;
  });

  const porConvocatorias = [...personas].sort((a, b) => b.convocatorias - a.convocatorias);
  const cabeza = personas.length ? Math.max(1, Math.round(personas.length * 0.1)) : 0;
  const totalConvocatorias = personas.reduce((s, p) => s + p.convocatorias, 0);
  const deLaCabeza = porConvocatorias.slice(0, cabeza).reduce((s, p) => s + p.convocatorias, 0);
  const diasConocidos = personas.map((p) => p.dias_sin_contacto).filter((d) => d != null).sort((a, b) => a - b);

  return {
    panel_id: panelId, panel: panel.nombre, estado_membresia: 'activo',
    miembros: personas.length, olas,
    respuesta: {
      convocatorias_emitidas: convocados, respuestas: respondieron,
      tasa_respuesta: convocados ? Number((respondieron / convocados).toFixed(4)) : null,
      miembros_que_respondieron_alguna: personas.filter((p) => p.respuestas).length,
      miembros_nunca_convocados: distribucionContacto.nunca,
    },
    ultimo_contacto: {
      distribucion: distribucionContacto,
      mediana_dias: diasConocidos.length ? diasConocidos[Math.floor(diasConocidos.length / 2)] : null,
      umbral_dormido_dias: 30,
      mas_dormidos: personas.filter((p) => p.dias_sin_contacto != null && p.dias_sin_contacto >= 30)
        .sort((a, b) => b.dias_sin_contacto - a.dias_sin_contacto).slice(0, 15),
      nunca_contactados: personas.filter((p) => p.dias_sin_contacto == null).slice(0, 15),
    },
    convocatorias: {
      distribucion: distribucionConvocatorias,
      promedio_por_miembro: personas.length
        ? Number((totalConvocatorias / personas.length).toFixed(2)) : null,
      maximo: personas.reduce((m, p) => Math.max(m, p.convocatorias), 0),
      concentracion_decil_superior: totalConvocatorias
        ? Number((deLaCabeza / totalConvocatorias).toFixed(4)) : null,
      mas_convocados: porConvocatorias.slice(0, 15),
    },
  };
}

/* Lo que el "store semántico" tiene guardado. La página de cumplimiento lo
   muestra para hacer visible el invariante: solo id_persona, nunca PII. */
export function espiarSemantica() {
  return {
    cuestionarios: bd.semantica.cuestionarios.length,
    individuos: bd.semantica.individuos.map((i) => i.id_persona),
    respuestas: bd.semantica.respuestas.length,
    muestra: bd.semantica.respuestas.slice(0, 5).map((r) => ({
      id_persona: bd.semantica.individuos.find((i) => i.id === r.individuo_id)?.id_persona,
      texto_embebido: r.texto_embebido,
      embedding: r.embedding,
    })),
  };
}
