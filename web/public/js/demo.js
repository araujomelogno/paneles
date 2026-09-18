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

/* Addendum de R3.9 — marcar sin campo: se excluye del store semántico y no
   se guarda. Tiene que coincidir con `sav.SOLO_EXCLUIR` del backend. */
const SOLO_EXCLUIR = '(no guardar)';

/* Un `.sav` guarda `2` y «Femenino» por separado: sin traducir, la ficha se
   llena de códigos y la composición por sexo queda inservible. */
const SEXO_CANONICO = {
  f: 'F', femenino: 'F', femenina: 'F', mujer: 'F', female: 'F',
  m: 'M', masculino: 'M', masculina: 'M', hombre: 'M', varon: 'M',
  'varón': 'M', male: 'M',
  x: 'X', otro: 'X', otra: 'X', 'no binario': 'X', 'no binarie': 'X',
};

function valorDemografico(campo, crudo, opciones) {
  if (crudo === undefined || crudo === null) return null;
  let texto = String(crudo).trim();
  if (!texto) return null;
  const etiqueta = (opciones || {})[texto];
  if (etiqueta) texto = String(etiqueta).trim();
  if (campo === 'sexo') return SEXO_CANONICO[texto.toLowerCase()] || texto;
  return texto;
}
const diasAtras = (n) => new Date(Date.now() - n * 86400000).toISOString();

/* ── Estado ─────────────────────────────────────────────────────── */

const UMBRALES_DEFAULT = {
  max_convocatorias_ventana: 3, ventana_dias: 90,
  max_convocatorias_total: null, dias_minimos_entre: 14,
};

const EXPLICACIONES_MUESTREO = {
  sin_consentimiento: 'no tiene consentimiento vigente de contacto_participacion',
  pendiente_de_consentimiento: 'fue creada por una ingesta sin base legal registrada',
  ya_convocado_a_esta_encuesta: 'ya está convocada a esta encuesta',
  demasiadas_convocatorias_recientes: 'superó el tope de convocatorias de la ventana',
  demasiadas_convocatorias_acumuladas: 'superó el tope de convocatorias acumuladas',
  convocado_hace_muy_poco: 'fue convocada hace menos de los días mínimos entre olas',
  cuota_del_segmento_ya_cubierta: 'su segmento no tiene brecha que cerrar',
};

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
  // Fase 3
  umbralesFatiga: {},      // por panel; vacío = rigen los defaults
  movimientos: [],         // el ledger de puntos: el saldo es su suma
  premios: [],
  canjes: [],
  bonos: [],
  inscripciones: [],       // solicitudes de la landing, todavía no personas
  textosConsentimiento: [],
  cargas: [],              // R3.13 — lotes incorporados sin panel
  atributos: [],           // R3.14 — el catálogo de segmentadores
  canales: [],             // R4.4 — preferencias de canal por persona
  series: [],              // R4.1.b — series comparables entre olas
  auditoriaSeries: [],     // R4.1.b — el rastro va en la bóveda, no del lado semántico
  pesosOptimizador: {},    // R4.2 — por panel; vacío = rigen los defaults
  secuencias: {
    panel: 1, encuesta: 1, revision: 1, consentimiento: 1, semantica: 1,
    guardada: 1, auditoria: 1, reident: 1, carga: 1, atributo: 1,
    serie: 1,
  },
};

const siguiente = (clave) => bd.secuencias[clave]++;

/* R4.5 — las plantillas que «tiene» la cuenta de WhatsApp de la copia demo.

   Cada una trae su idioma y su `flow_id` adentro, que es exactamente la razón
   por la que la pantalla los dejó de pedir por separado. `invitacion_ola`
   está en dos idiomas a propósito: son dos plantillas distintas, se aprueban
   por separado, y elegir una por la otra sería mandarle a la gente en el
   idioma equivocado. */
const PLANTILLAS_DEMO = [
  { nombre: 'invitacion_ola', idioma: 'es', estado: 'APPROVED',
    categoria: 'MARKETING', flow_id: '7412880031',
    texto_boton: 'Responder la encuesta',
    cuerpo: 'Hola, somos Equipos Consultores. Te invitamos a responder una '
          + 'encuesta breve. Gracias por ser parte del panel.', calidad: 'GREEN' },
  { nombre: 'invitacion_ola', idioma: 'pt_BR', estado: 'APPROVED',
    categoria: 'MARKETING', flow_id: '7412880031',
    texto_boton: 'Responder a pesquisa',
    cuerpo: 'Olá, somos a Equipos Consultores. Convidamos você a responder '
          + 'uma pesquisa breve.', calidad: 'GREEN' },
  { nombre: 'recordatorio_ola', idioma: 'es', estado: 'APPROVED',
    categoria: 'UTILITY', flow_id: '7412880032',
    texto_boton: 'Completar ahora',
    cuerpo: 'Te habíamos invitado a una encuesta y todavía está abierta. '
          + 'Si querés, podés completarla acá.', calidad: 'GREEN' },
];

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

/* R3.14 — el catálogo. En la copia demo los valores siguen viviendo en la
   ficha de cada persona (`p.sexo`, `p.localidad`), porque lo que el demo
   imita es la **API**, no el esquema; lo que sí hace falta es que el catálogo
   exista y responda, que es de donde las pantallas arman sus desplegables. */
const DEPARTAMENTOS = [
  'Artigas', 'Canelones', 'Cerro Largo', 'Colonia', 'Durazno', 'Flores',
  'Florida', 'Lavalleja', 'Maldonado', 'Montevideo', 'Paysandú', 'Río Negro',
  'Rivera', 'Rocha', 'Salto', 'San José', 'Soriano', 'Tacuarembó',
  'Treinta y Tres',
];
const TRAMOS = ['<18', '18-24', '25-34', '35-44', '45-54', '55-64', '65+'];

function crearAtributo({ clave, etiqueta, tipo = 'categorico', categorias = [],
                         descripcion = null, es_especial = false }) {
  const atributo = {
    id: siguiente('atributo'), clave, etiqueta, tipo, descripcion,
    es_especial, activo: true, orden: bd.atributos.length * 10 + 10,
    creado_en: ahora(), del_nucleo: ['sexo', 'localidad', 'tramo_etario', 'edad']
      .includes(clave),
    tiene_datos: false,
    categorias: categorias.map((c, i) => ({
      id: bd.atributos.length * 100 + i + 1,
      clave: typeof c === 'string' ? c : c.clave,
      etiqueta: typeof c === 'string' ? c : (c.etiqueta || c.clave),
      orden: (i + 1) * 10, activo: true,
    })),
  };
  bd.atributos.push(atributo);
  return atributo;
}

function sembrarCatalogo() {
  crearAtributo({
    clave: 'sexo', etiqueta: 'Sexo',
    descripcion: 'Segmentador de cuota básico.',
    categorias: [{ clave: 'F', etiqueta: 'Femenino' },
                 { clave: 'M', etiqueta: 'Masculino' },
                 { clave: 'X', etiqueta: 'Otro / no binario' }],
  });
  crearAtributo({
    clave: 'tramo_etario', etiqueta: 'Tramo etario', tipo: 'derivado',
    descripcion: 'Se deriva de la fecha de nacimiento; si no hay, de la edad '
      + 'declarada envejecida hasta hoy.',
    categorias: TRAMOS,
  });
  crearAtributo({
    clave: 'edad', etiqueta: 'Edad', tipo: 'derivado',
    descripcion: 'Edad efectiva, derivada de la fecha de nacimiento.',
  });
  crearAtributo({
    clave: 'localidad', etiqueta: 'Localidad',
    descripcion: 'Segmentador geográfico.', categorias: DEPARTAMENTOS,
  });
  // Y uno definido por un admin, para que se vea el caso de uso de R3.14.
  crearAtributo({
    clave: 'nivel_educativo', etiqueta: 'Nivel educativo',
    descripcion: 'Máximo nivel alcanzado, tal como lo releva el campo.',
    categorias: [{ clave: 'primaria', etiqueta: 'Primaria' },
                 { clave: 'secundaria', etiqueta: 'Secundaria' },
                 { clave: 'terciaria', etiqueta: 'Terciaria o más' }],
  });
}

function sembrar() {
  sembrarCatalogo();
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
    // R4.4 — el celular en E.164, y los canales que aceptó. Los primeros seis
    // aceptaron WhatsApp: alcanza para que el envío tenga a quién mandarle y
    // para que se vea a quién deja afuera.
    bd.personas[bd.personas.length - 1].celular =
      '+598' + String(celular || '').replace(/\D/g, '').replace(/^0/, '');
    ['email', ...(i < 6 ? ['whatsapp'] : [])].forEach((canal) => bd.canales.push({
      id_persona: idPersona, canal, estado: 'activa', activa: true,
      version_texto: 'optin-2026-01', origen: 'alta_manual',
      otorgado_en: diasAtras(120 - i * 7), revocado_en: null,
    }));
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

  /* R4.1 — una segunda ola de la **misma** gente que vuelve a preguntar lo
     mismo, con otra redacción y otras opciones. Es lo que hace demostrable
     todo el bloque longitudinal: sin dos olas comparables, una serie no tiene
     qué comparar y la matriz de transiciones no tiene qué mostrar.

     La redacción distinta no es un detalle de color: es exactamente el
     problema que R4.1.b resuelve. Si las dos olas preguntaran con las mismas
     palabras y las mismas opciones, declarar una serie no haría falta. */
  const olaSeguimiento = crearEncuesta(
    nacional.id, 'Ola 4 — Consumo de bebidas (seguimiento)', '2027-02-20');
  convocar(olaSeguimiento.id, { todo_el_panel: true });
  olaSeguimiento.estado = 'cerrada';
  const respuestasSeguimiento = [
    ['A', 'Sigo con el fernet, no lo cambio'],
    ['C', 'Me pasé a la cerveza este verano'],
    ['C', 'Dejé el fernet, me caía mal'],
    ['B', 'El whisky sigue siendo lo mío'],
    ['C', 'Cerveza, como siempre'],
    ['A', 'Fernet toda la vida'],
    ['B', 'Whisky los fines de semana'],
    ['A', 'Me pasé al fernet, me lo hicieron probar'],
    ['C', 'Ahora tomo cerveza'],
    ['C', 'Cerveza, por el precio'],
  ];
  ingestar(olaSeguimiento.id, {
    preguntas: [
      { codigo: 'B1', texto: '¿Cuál es hoy su bebida de consumo habitual?',
        tipo: 'cerrada',
        opciones: { A: 'Fernet con cola', B: 'Whisky', C: 'Cerveza' }, orden: 1 },
      { codigo: 'B2', texto: '¿Cambió algo respecto del año pasado?',
        tipo: 'abierta', orden: 2 },
    ],
    filas: bd.participaciones
      .filter((p) => p.encuesta_id === olaSeguimiento.id)
      .map((p, i) => ({
        id_en_origen: bd.alias.find((a) => a.id_persona === p.id_persona).id_en_origen,
        B1: respuestasSeguimiento[i % respuestasSeguimiento.length][0],
        B2: respuestasSeguimiento[i % respuestasSeguimiento.length][1],
      })),
  });

  // Una ola en campo, para poder convocar desde la interfaz.
  crearEncuesta(joven.id, 'Ola 2 — Hábitos digitales', '2026-09-20');

  // Y una sin convocar en el panel que sí tiene universo de referencia: es
  // la que deja ver el muestreo de la Fase 3 haciendo lo suyo, priorizando
  // la brecha contra un objetivo cargado. Va configurada como Flow de
  // WhatsApp (R4.5): sin una así, la pantalla de envío no tendría nada que
  // mostrar y el estado del Flow no se podría ver nunca.
  const movilidad = crearEncuesta(nacional.id, 'Ola 3 — Movilidad urbana',
                                  '2026-10-10');
  Object.assign(movilidad, {
    flow_plantilla: 'invitacion_ola', flow_idioma: 'es',
    flow_id: '7412880031', es_flow: true,
  });

  // R3.13 — una carga externa ya hecha: gente incorporada con sus respuestas
  // que **no** es panelista. Sin esto el filtro «sin panel» de la pantalla de
  // panelistas no tendría nada que mostrar, que es justamente el caso que el
  // requisito existe para resolver.
  const omnibus = {
    id: siguiente('carga'), nombre: 'Ómnibus agosto 2026',
    descripcion: 'Estudio de terceros. Esta gente no es panelista.',
    ref_estudio: uuid(), creado_en: diasAtras(25), creado_por: 'demo',
  };
  bd.cargas.push(omnibus);
  const EXTERNOS = [
    ['1.234.567-8', 'Karina Bentancur', 'F', '1983-05-11', 'Durazno', 'k.bentancur@correo.uy', '1', 'La compro por costumbre, siempre la misma'],
    ['2.345.678-9', 'Leonardo Ferreira', 'M', '1977-10-02', 'Montevideo', 'l.ferreira@correo.uy', '3', 'Me la recomendó un amigo y me quedé con esa'],
    ['3.456.789-0', 'Mariana Olivera', 'F', '1996-03-19', 'Florida', 'm.olivera@correo.uy', '2', 'Es la que consigo cerca de casa'],
  ];
  EXTERNOS.forEach(([documento, nombre, sexo, fechaNacimiento, localidad, email], i) => {
    const idPersona = uuid();
    bd.personas.push({
      id_persona: idPersona, documento, nombre, sexo,
      fecha_nacimiento: fechaNacimiento, localidad, email, celular: null,
      contacto: null, observaciones: null, creado_en: diasAtras(25),
    });
    // La finalidad obligatoria de una carga es el uso semántico; el contacto
    // no se pide y por eso no lo tienen: son consultables y no convocables.
    otorgar(idPersona, 'uso_semantico', VERSION, diasAtras(25));
  });
  ingestarCarga(omnibus.id, {
    columna_id: 'documento',
    tipo_identificador: 'documento',
    preguntas: [
      { codigo: 'Q1', texto: '¿Qué marca compra habitualmente?', tipo: 'cerrada', opciones: { 1: 'La de siempre', 2: 'La más barata', 3: 'La que le recomiendan' }, orden: 1 },
      { codigo: 'Q2', texto: '¿Por qué elige esa marca?', tipo: 'abierta', orden: 2 },
    ],
    filas: EXTERNOS.map(([documento, , , , , , q1, q2]) => ({ documento, Q1: q1, Q2: q2 })),
  });

  // La calidad de la ola ya fieldeada: la mayoría bien, un par marcados. Sin
  // esto la liquidación de puntos no tendría nada que mostrar, y la revisión
  // de una marca tampoco.
  bd.participaciones
    .filter((p) => p.encuesta_id === ola.id && p.respondio)
    .forEach((p, i) => {
      p.calidad_estado = i % 7 === 3 ? 'sospechoso' : 'ok';
      p.motivo_calidad = i % 7 === 3 ? 'speeder' : null;
      p.duracion_segundos = i % 7 === 3 ? 41 : 300 + i * 17;
    });

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

  /* ── Fase 3 ─────────────────────────────────────────────────────
     Lo justo para que las pantallas nuevas tengan de qué hablar: un
     catálogo con un premio agotado (para que se vea el estado), saldo
     para que un canje sea posible, un bono vigente y otro vencido, un
     texto de consentimiento publicado —sin él la landing no recibe— y
     dos inscripciones esperando, una de ellas de alguien que ya está
     en el panel. */

  bd.premios.push(
    { id: 1, nombre: 'Orden de compra $1.000', descripcion: 'Canjeable en comercios adheridos.',
      costo_puntos: 500, stock: null, activo: true },
    { id: 2, nombre: 'Auriculares inalámbricos', descripcion: 'Stock limitado.',
      costo_puntos: 1200, stock: 3, activo: true },
    { id: 3, nombre: 'Entradas de cine (par)', descripcion: 'Válidas de lunes a jueves.',
      costo_puntos: 300, stock: 0, activo: true },
  );

  bd.personas.slice(0, 6).forEach((persona, i) => {
    bd.movimientos.push({
      id: bd.movimientos.length + 1, id_persona: persona.id_persona, tipo: 'earn',
      puntos: 100 + i * 50, encuesta_id: bd.encuestas[0]?.id ?? null,
      motivo: `participación de calidad · ${bd.encuestas[0]?.nombre ?? 'Ola 1'}`,
      creado_en: diasAtras(20 - i),
    });
  });

  bd.canjes.push({
    id: 1, id_persona: bd.personas[0].id_persona, premio_id: 3, costo_puntos: 300,
    estado: 'solicitado', creado_en: diasAtras(3),
  });
  bd.movimientos.push({
    id: bd.movimientos.length + 1, id_persona: bd.personas[0].id_persona,
    tipo: 'canje', puntos: -300, motivo: 'canje · Entradas de cine (par)',
    creado_en: diasAtras(3),
  });

  bd.bonos.push(
    { id: 1, panel_id: nacional.id, dimension: 'sexo', categoria: 'M', puntos_extra: 50,
      desde: diasAtras(10), hasta: null },
    { id: 2, panel_id: nacional.id, dimension: 'localidad', categoria: 'Salto',
      puntos_extra: 80, desde: diasAtras(60), hasta: diasAtras(5) },
  );

  bd.textosConsentimiento.push({
    id: 1, finalidad: 'contacto_participacion', version: VERSION,
    cuerpo: 'Texto de ejemplo del modo demo. El consentimiento real lo redacta y '
          + 'revisa el DPO antes de publicar la landing.',
    activo: true, creado_por: 'demo', creado_en: diasAtras(30),
  });

  const yaEsPanelista = bd.personas[1];
  bd.inscripciones.push(
    { id: 1, nombre: 'Lucía Fernández', email: 'lucia.fernandez@ejemplo.uy',
      celular: '099 123 456', documento: '4.567.890-1', fecha_nacimiento: '1994-07-12',
      sexo: 'F', localidad: 'Montevideo', finalidades: ['contacto_participacion'],
      version_texto: VERSION, acepto_en: diasAtras(1), resolucion: 'crea',
      id_persona_previa: null, estado: 'pendiente', id_persona: null, panel_id: null,
      resuelto_por: null, resuelto_en: null, motivo_rechazo: null, origen: 'landing',
      creado_en: diasAtras(1) },
    { id: 2, nombre: yaEsPanelista.nombre, email: yaEsPanelista.email,
      celular: yaEsPanelista.celular, documento: yaEsPanelista.documento,
      fecha_nacimiento: yaEsPanelista.fecha_nacimiento, sexo: yaEsPanelista.sexo,
      localidad: yaEsPanelista.localidad, finalidades: ['contacto_participacion'],
      version_texto: VERSION, acepto_en: diasAtras(2), resolucion: 'reutiliza',
      id_persona_previa: yaEsPanelista.id_persona, estado: 'pendiente', id_persona: null,
      panel_id: null, resuelto_por: null, resuelto_en: null, motivo_rechazo: null,
      origen: 'landing', creado_en: diasAtras(2) },
  );
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
      encuesta_id: encuestaId, id_persona: idPersona, origen: 'convocatoria',
      convocado_en: ahora(), respondio: false, respondio_en: null,
      calidad_estado: 'pendiente',
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
  if (!encuesta) throw new ErrorDemo('No existe la encuesta.', 404);
  return ingestarEn(encuesta, cuerpo, { sinPanel: false });
}

/* R3.13 — la misma ingesta, contra una carga en vez de una encuesta. Del
   lado semántico no cambia nada: una carga tiene su `ref_estudio` igual que
   una encuesta. Lo que cambia es que no hay panel al que incorporar ni ola
   que participar. */
function ingestarCarga(cargaId, cuerpo) {
  const carga = bd.cargas.find((c) => c.id === cargaId);
  if (!carga) throw new ErrorDemo('No existe la carga.', 404);
  const resultado = ingestarEn(carga, cuerpo, { sinPanel: true });
  resultado.carga_id = cargaId;
  resultado.sin_panel = true;
  delete resultado.encuesta_id;
  return resultado;
}

function ingestarEn(encuesta, cuerpo, { sinPanel }) {
  const encuestaId = encuesta.id;
  const columnaId = cuerpo.columna_id || 'id_en_origen';
  const filas = cuerpo.filas || [];

  // Addendum de R3.9: lo marcado como demográfico no se ingesta al store
  // semántico. Su valor va a la ficha, y solo si el campo estaba vacío.
  const demograficas = cuerpo.demograficas || {};
  const excluidas = [];
  const preguntas = (cuerpo.preguntas || []).filter((p) => {
    if (!demograficas[p.codigo]) return true;
    excluidas.push(p.codigo);
    return false;
  });
  const opcionesDemograficas = {};
  (cuerpo.preguntas || []).forEach((p) => {
    if (demograficas[p.codigo]) opcionesDemograficas[p.codigo] = p.opciones || {};
  });

  // R3.12 — qué trae la columna de identidad. El default es `alias`.
  const tipo = cuerpo.tipo_identificador || 'alias';
  const mapa = {};
  let aliasRegistrados = 0;
  if (tipo === 'alias') {
    // La unión de los convocados y de los alias de campo: quien respondió sin
    // haber sido convocado también tiene que resolver a su id_persona, que es
    // de lo que depende el addendum de R3.9.
    bd.alias.forEach((a) => { mapa[a.id_en_origen] = a.id_persona; });
    bd.participaciones.filter((p) => p.encuesta_id === encuestaId).forEach((p) => {
      const alias = bd.alias.find((a) => a.id_persona === p.id_persona);
      if (alias) mapa[alias.id_en_origen] = p.id_persona;
    });
  } else {
    filas.forEach((fila) => {
      const valor = String(fila[columnaId] || '').trim();
      if (!valor) return;
      const persona = tipo === 'id_persona'
        ? bd.personas.find((p) => p.id_persona === valor)
        : tipo === 'documento'
          ? bd.personas.find((p) => p.documento === valor)
          : bd.personas.find(
              (p) => (p.email || '').toLowerCase() === valor.toLowerCase());
      if (!persona) return;
      mapa[valor] = persona.id_persona;
      // R3.12.c — la carga por llave natural deja sembrado el alias.
      if ((tipo === 'documento' || tipo === 'email') && cuerpo.origen
          && !bd.alias.some((a) => a.origen === cuerpo.origen && a.id_en_origen === valor)) {
        bd.alias.push({
          id_persona: persona.id_persona, origen: cuerpo.origen, id_en_origen: valor,
        });
        aliasRegistrados++;
      }
    });
  }

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
  const motivos = {};
  const sinConsentimiento = new Set();
  const ingestados = new Set();
  let escritas = 0;

  filas.forEach((fila) => {
    const idOrigen = String(fila[columnaId] || '').trim();
    const idPersona = mapa[idOrigen];
    if (!idPersona) {
      if (idOrigen) {
        sinMapear.push(idOrigen);
        motivos[idOrigen] = tipo === 'alias' ? 'sin_alias_para_ese_origen'
          : tipo === 'id_persona' && !/^[0-9a-f-]{36}$/i.test(idOrigen)
            ? 'formato_invalido' : 'no_encontrado';
      }
      return;
    }
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
    ingestados.add(idPersona);
  });

  // ── Addendum de R3.9: membresía y participación ──
  // Quien respondió pertenece al panel de la encuesta, y su respuesta queda
  // registrada aunque nadie lo haya convocado. Una baja no se reactiva.
  let membresiasNuevas = 0;
  let membresiasExistentes = 0;
  const membresiasEnBaja = [];
  let participacionesNuevas = 0;
  let participacionesActualizadas = 0;

  // R3.13 — en una carga sin panel esto no corre: no hay panel al que
  // incorporar y no hubo ola que participar. Es exactamente lo que ese flujo
  // existe para no hacer.
  if (!sinPanel) ingestados.forEach((idPersona) => {
    const membresia = bd.membresias.find(
      (m) => m.panel_id === encuesta.panel_id && m.id_persona === idPersona);
    if (!membresia) {
      bd.membresias.push({
        panel_id: encuesta.panel_id, id_persona: idPersona,
        estado: 'activo', fecha_alta: ahora(), fecha_baja: null,
      });
      membresiasNuevas++;
    } else if (membresia.estado === 'baja') {
      membresiasEnBaja.push(idPersona);
    } else {
      membresiasExistentes++;
    }

    const participacion = bd.participaciones.find(
      (p) => p.encuesta_id === encuestaId && p.id_persona === idPersona);
    if (participacion) {
      participacion.respondio = true;
      participacion.respondio_en = participacion.respondio_en || ahora();
      participacionesActualizadas++;
    } else {
      bd.participaciones.push({
        encuesta_id: encuestaId, id_persona: idPersona, origen: 'importacion',
        convocado_en: ahora(), respondio: true, respondio_en: ahora(),
        calidad_estado: 'pendiente',
      });
      participacionesNuevas++;
    }
  });

  // ── Addendum de R3.9: los demográficos a la bóveda ──
  let completados = 0;
  const discrepancias = [];
  const yaVistos = new Set();
  filas.forEach((fila) => {
    const idPersona = mapa[String(fila[columnaId] || '').trim()];
    if (!idPersona || yaVistos.has(idPersona)) return;
    yaVistos.add(idPersona);
    const persona = bd.personas.find((p) => p.id_persona === idPersona);
    if (!persona) return;
    Object.entries(demograficas).forEach(([variable, campo]) => {
      if (campo === SOLO_EXCLUIR) return;
      const valor = valorDemografico(
        campo, fila[variable], opcionesDemograficas[variable]);
      if (!valor) return;
      if (!persona[campo]) { persona[campo] = valor; completados++; return; }
      if (String(persona[campo]).trim().toLowerCase() !== valor.toLowerCase()) {
        discrepancias.push({
          id_persona: idPersona, campo,
          en_boveda: String(persona[campo]).toLowerCase(),
          en_el_archivo: valor.toLowerCase(),
        });
      }
    });
  });

  return {
    encuesta_id: encuestaId, ref_estudio: encuesta.ref_estudio,
    respuestas_escritas: escritas,
    excluidas_por_demografica: excluidas.sort(),
    demograficos_completados: completados,
    discrepancias_demograficas: discrepancias,
    personas: bd.semantica.individuos.length,
    preguntas: preguntas.length,
    tipo_identificador: tipo,
    alias_registrados: aliasRegistrados,
    sin_mapear: [...new Set(sinMapear)],
    sin_mapear_detalle: [...new Set(sinMapear)].map(
      (i) => ({ id_en_origen: i, motivo: motivos[i] })),
    sin_consentimiento: [...sinConsentimiento],
    ...(sinPanel ? {} : {
      membresias_nuevas: membresiasNuevas,
      membresias_existentes: membresiasExistentes,
      membresias_en_baja: membresiasEnBaja,
      participaciones_nuevas: participacionesNuevas,
      participaciones_actualizadas: participacionesActualizadas,
    }),
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

/* R3.14 — los atributos efectivos de una persona, con su procedencia. En la
   copia demo se derivan de la ficha; contra el servidor salen de
   `v_atributo_persona`, que es donde vive la precedencia de R3.14.g. */
function atributosDe(persona) {
  const salida = [];
  const agregar = (clave, valor, procedencia = 'cargado') => {
    if (valor === null || valor === undefined || valor === '') return;
    const atributo = bd.atributos.find((a) => a.clave === clave);
    if (!atributo) return;
    const categoria = (atributo.categorias || []).find((c) => c.clave === String(valor));
    salida.push({
      clave, etiqueta: atributo.etiqueta, tipo: atributo.tipo,
      es_especial: atributo.es_especial,
      valor: String(valor),
      etiqueta_valor: categoria ? categoria.etiqueta : String(valor),
      valor_num: clave === 'edad' ? Number(valor) : null,
      valor_fecha: null,
      valor_crudo: persona.crudos?.[clave] ?? null,
      origen: persona.origen_atributos?.[clave] || 'alta',
      procedencia, fecha_referencia: null,
    });
  };
  agregar('sexo', persona.sexo);
  agregar('localidad', persona.localidad);
  if (persona.fecha_nacimiento) {
    const edad = Math.floor(
      (Date.now() - new Date(persona.fecha_nacimiento).getTime()) / 31557600000);
    agregar('edad', edad, 'derivado');
    agregar('tramo_etario', tramoEtario(persona.fecha_nacimiento), 'derivado');
  }
  Object.entries(persona.atributos || {}).forEach(([clave, valor]) =>
    agregar(clave, valor));
  return salida;
}

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
    // R3.13.e — los que entraron por una carga y no son miembros de ningún
    // panel. Sin este filtro se mezclan con los panelistas y no hay forma de
    // ver a quiénes falta incorporar.
    if (['1', 'true'].includes(String(consulta.sin_panel || ''))) {
      items = items.filter((p) => !p.paneles);
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
        // R3.14.g — de dónde sale el tramo. En la copia demo solo existe el
        // caso derivado, porque no hay cargas con edad declarada.
        procedencia_tramo: persona.fecha_nacimiento ? 'derivado' : null,
      },
      atributos: atributosDe(persona),
      // R4.4 — por dónde acepta que la contacten.
      canales: bd.canales.filter((c) => c.id_persona === persona.id_persona),
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

  if (metodo === 'GET' && partes[0] === 'encuestas' && partes[2] === 'muestra') {
    const encuestaId = Number(partes[1]);
    const conContacto = ['1', 'true'].includes(String(consulta.con_contacto || ''));
    const campos = conContacto
      ? ['id_persona', 'nombre', 'documento', 'email', 'celular', 'contacto',
         'sexo', 'localidad', 'tramo_etario']
      : ['id_persona'];
    const gente = bd.participaciones
      .filter((p) => p.encuesta_id === encuestaId)
      .map((p) => bd.personas.find((x) => x.id_persona === p.id_persona))
      .filter(Boolean);
    const lineas = [campos.join(',')];
    gente.forEach((p) => lineas.push(campos.map((c) => (
      c === 'tramo_etario' ? tramoEtario(p.fecha_nacimiento) : (p[c] ?? '')
    )).join(',')));
    const aviso = conContacto
      ? '# ATENCIÓN: este archivo contiene datos personales de panelistas.\n' : '';
    return {
      encuesta_id: encuestaId, personas: gente.length,
      csv: aviso + lineas.join('\n') + '\n',
      nombre_archivo: `muestra-${encuestaId}${conContacto ? '-CON-DATOS-PERSONALES' : ''}.csv`,
      contiene_datos_personales: conContacto,
      columnas: campos,
    };
  }

  if (metodo === 'POST' && partes[0] === 'encuestas' && partes[2] === 'ingesta') {
    return ingestar(Number(partes[1]), cuerpo);
  }

  /* R4.4 — preferencias de canal. */
  if (metodo === 'GET' && partes[0] === 'panelistas' && partes[2] === 'canales') {
    return { items: bd.canales.filter((c) => c.id_persona === partes[1]) };
  }
  if (partes[0] === 'panelistas' && partes[2] === 'canales' && partes.length === 4) {
    const idPersona = partes[1];
    const canal = partes[3];
    const persona = bd.personas.find((p) => p.id_persona === idPersona);
    if (!persona) throw new ErrorDemo('No existe la persona.', 404);
    const existente = bd.canales.find(
      (c) => c.id_persona === idPersona && c.canal === canal);

    if (metodo === 'PUT') {
      // R4.4 — WhatsApp exige celular en formato internacional válido: sin
      // número no hay a quién mandarle.
      if ((canal === 'whatsapp' || canal === 'sms')
          && !/^\+\d{8,15}$/.test(persona.celular || '')) {
        throw new ErrorDemo(
          `Para activar «${canal}» hace falta un celular en formato `
          + `internacional válido. El que tiene cargado es ${persona.celular}.`,
          409);
      }
      const preferencia = existente || {
        id_persona: idPersona, canal, otorgado_en: ahora(),
      };
      Object.assign(preferencia, {
        estado: 'activa', activa: true,
        version_texto: cuerpo.version_texto || null,
        origen: cuerpo.origen || 'edicion',
        otorgado_en: ahora(), revocado_en: null,
      });
      if (!existente) bd.canales.push(preferencia);
      return preferencia;
    }

    if (metodo === 'DELETE') {
      if (!existente) {
        throw new ErrorDemo(
          `La persona no tiene preferencia registrada para «${canal}».`, 404);
      }
      Object.assign(existente,
                    { estado: 'revocada', activa: false, revocado_en: ahora() });
      return existente;
    }
  }

  /* R4.5 — las plantillas de la cuenta de WhatsApp.

     La copia demo no llama a Meta: devuelve un catálogo fijo con la forma
     que devuelve la API. Las tres están para mostrar los tres casos que la
     pantalla tiene que distinguir: la que sirve, la que existe en dos
     idiomas —y por eso son dos plantillas distintas— y ninguna sin botón de
     Flow, porque el backend las filtra antes de que lleguen acá. */
  if (clave === 'GET /whatsapp/plantillas') {
    return { plantillas: PLANTILLAS_DEMO, configurado: true, faltan: [],
             sin_flow: 1, avisos: [] };
  }

  /* R4.5 — el canal de WhatsApp de una encuesta. */
  if (partes[0] === 'encuestas' && partes[2] === 'flow') {
    const encuesta = bd.encuestas.find((e) => e.id === Number(partes[1]));
    if (!encuesta) throw new ErrorDemo('No existe la encuesta.', 404);
    if (metodo === 'PUT') {
      const nombre = (cuerpo.plantilla || '').trim() || null;
      const idioma = (cuerpo.idioma || '').trim() || null;
      // El idioma y el `flow_id` salen de la plantilla, no del formulario.
      const candidatas = PLANTILLAS_DEMO.filter((p) =>
        p.nombre === nombre && (!idioma || p.idioma === idioma));
      const elegida = candidatas.length === 1 ? candidatas[0] : null;
      Object.assign(encuesta, {
        flow_plantilla: nombre,
        flow_idioma: elegida ? elegida.idioma : idioma,
        flow_id: elegida ? elegida.flow_id : null,
      });
      encuesta.es_flow = !!encuesta.flow_plantilla;
      return encuesta;
    }
    // La copia demo no llama a Meta: responde como si el Flow estuviera
    // publicado y la plantilla aprobada, que es el caso que interesa recorrer.
    const resuelta = !!(encuesta.es_flow && encuesta.flow_id);
    const ambigua = encuesta.es_flow && !encuesta.flow_id
      && PLANTILLAS_DEMO.filter((p) => p.nombre === encuesta.flow_plantilla);
    return {
      encuesta_id: encuesta.id, es_flow: !!encuesta.es_flow,
      flow_id: encuesta.flow_id, plantilla: encuesta.flow_plantilla,
      idioma: encuesta.flow_idioma,
      puede_enviar: resuelta,
      motivos: resuelta ? [] : (
        !encuesta.es_flow
          ? ['La encuesta no está configurada como Flow.']
          : [`Hay ${(ambigua || []).length} plantillas «${encuesta.flow_plantilla}», `
             + `una por idioma. Hay que elegir cuál: son plantillas distintas y `
             + `se aprueban por separado.`]),
      idiomas_disponibles: resuelta ? undefined
        : (ambigua || []).map((p) => p.idioma).sort(),
      flow: resuelta ? { nombre: 'Invitación a la ola', estado: 'PUBLISHED' } : undefined,
      plantilla_estado: resuelta ? 'APPROVED' : undefined,
      texto_boton: resuelta ? 'Responder la encuesta' : undefined,
    };
  }

  if (partes[0] === 'encuestas' && partes[2] === 'whatsapp') {
    const encuestaId = Number(partes[1]);
    const convocados = bd.participaciones.filter((p) => p.encuesta_id === encuestaId);
    const excluidos = {};
    const destinatarios = [];
    const yaEnviados = [];
    convocados.forEach((p) => {
      const persona = bd.personas.find((x) => x.id_persona === p.id_persona);
      const excluir = (motivo) => {
        (excluidos[motivo] = excluidos[motivo] || []).push(p.id_persona);
      };
      if (!vigente(p.id_persona, 'contacto_participacion')) return excluir('sin_consentimiento');
      const preferencia = bd.canales.find(
        (c) => c.id_persona === p.id_persona && c.canal === 'whatsapp' && c.activa);
      if (!preferencia) return excluir('sin_preferencia_whatsapp');
      if (!persona?.celular) return excluir('sin_celular');
      if (!/^\+\d{8,15}$/.test(persona.celular)) return excluir('celular_invalido');
      if (p.envio_estado === 'enviado') { yaEnviados.push(p.id_persona); return; }
      destinatarios.push(p.id_persona);
    });
    const seleccion = {
      encuesta_id: encuestaId, convocados: convocados.length,
      destinatarios, ya_enviados: yaEnviados, excluidos,
      excluidos_total: Object.values(excluidos).reduce((n, v) => n + v.length, 0),
    };
    if (metodo === 'GET') return seleccion;

    // El envío. Reintentar no le vuelve a mandar a quien ya recibió.
    destinatarios.forEach((idPersona) => {
      const participacion = convocados.find((p) => p.id_persona === idPersona);
      Object.assign(participacion,
                    { enviado_en: ahora(), envio_estado: 'enviado' });
    });
    return {
      ...seleccion, enviados: destinatarios.length, fallidos: 0,
      detalle: destinatarios.map((i) => ({ id_persona: i, estado: 'enviado' })),
    };
  }

  if (clave === 'GET /diagnostico/contacto') {
    return {
      verificacion: { proveedor_envio: 'ninguno', envia_de_verdad: false,
                      sal_configurada: false,
                      avisos: ['Sin proveedor de envío: el código vuelve en la '
                               + 'respuesta y la verificación no prueba nada. '
                               + 'La landing no se puede anunciar así.'] },
      desafio: { proveedor: 'ninguno', activo: false,
                 avisos: ['Sin desafío configurado: la landing no distingue un '
                          + 'envío automatizado de una persona.'] },
      whatsapp: { configurado: false, faltan: ['token', 'phone_number_id'],
                  avisos: ['Sin credenciales de WhatsApp: una encuesta se puede '
                           + 'configurar como Flow, pero el envío queda '
                           + 'deshabilitado.'] },
    };
  }

  /* R3.14 — el catálogo de atributos demográficos. */
  if (clave === 'GET /atributos') {
    const conDatos = new Set();
    bd.personas.forEach((p) => {
      if (p.sexo) conDatos.add('sexo');
      if (p.localidad) conDatos.add('localidad');
      if (p.fecha_nacimiento) { conDatos.add('tramo_etario'); conDatos.add('edad'); }
      Object.keys(p.atributos || {}).forEach((k) => conDatos.add(k));
    });
    return {
      items: bd.atributos
        .filter((a) => (!['1', 'true'].includes(String(consulta.activos || '')) || a.activo))
        .map((a) => ({ ...a, tiene_datos: conDatos.has(a.clave) })),
    };
  }

  if (metodo === 'POST' && clave === 'POST /atributos') {
    const claveNueva = (cuerpo.clave || '').trim().toLowerCase();
    if (!claveNueva) throw new ErrorDemo('Falta la clave del atributo.', 400);
    if (bd.atributos.some((a) => a.clave === claveNueva)) {
      throw new ErrorDemo(`Ya existe un atributo con la clave «${claveNueva}».`, 409);
    }
    if (cuerpo.tipo === 'derivado') {
      throw new ErrorDemo(
        'Un atributo derivado se calcula a partir de otro dato de la persona, '
        + 'y ese cálculo vive en el esquema: no se puede definir desde la app.',
        400);
    }
    return crearAtributo({
      clave: claveNueva, etiqueta: cuerpo.etiqueta || claveNueva,
      tipo: cuerpo.tipo || 'categorico', descripcion: cuerpo.descripcion || null,
      es_especial: !!cuerpo.es_especial, categorias: cuerpo.categorias || [],
    });
  }

  if (partes[0] === 'atributos' && partes.length >= 2 && partes[1] !== 'auditoria') {
    const atributo = bd.atributos.find((a) => String(a.id) === partes[1]);
    if (!atributo) throw new ErrorDemo('No existe el atributo.', 404);

    if (metodo === 'PATCH' && partes.length === 2) {
      if ('activo' in cuerpo && Object.keys(cuerpo).length === 1) {
        if (!cuerpo.activo && atributo.del_nucleo) {
          throw new ErrorDemo(
            `«${atributo.clave}» es uno de los segmentadores sobre los que `
            + 'funcionan la composición, las cuotas y el muestreo: no se puede '
            + 'desactivar.', 409);
        }
        atributo.activo = !!cuerpo.activo;
        return atributo;
      }
      if (cuerpo.etiqueta) atributo.etiqueta = cuerpo.etiqueta;
      if ('descripcion' in cuerpo) atributo.descripcion = cuerpo.descripcion || null;
      if ('es_especial' in cuerpo) atributo.es_especial = !!cuerpo.es_especial;
      return atributo;
    }

    if (metodo === 'DELETE' && partes.length === 2) {
      if (atributo.del_nucleo) {
        throw new ErrorDemo(
          `«${atributo.clave}» es uno de los segmentadores del núcleo: no se `
          + 'elimina.', 409);
      }
      bd.atributos.splice(bd.atributos.indexOf(atributo), 1);
      return { id: atributo.id, clave: atributo.clave, estado: 'eliminado' };
    }

    if (metodo === 'POST' && partes[2] === 'categorias') {
      const claveCat = (cuerpo.clave || '').trim();
      if (atributo.categorias.some((c) => c.clave === claveCat)) {
        throw new ErrorDemo(`La categoría «${claveCat}» ya existe.`, 409);
      }
      const categoria = {
        id: siguiente('atributo') * 1000 + atributo.categorias.length,
        clave: claveCat, etiqueta: cuerpo.etiqueta || claveCat,
        orden: (atributo.categorias.length + 1) * 10, activo: true,
      };
      atributo.categorias.push(categoria);
      return categoria;
    }

    if (metodo === 'PATCH' && partes[2] === 'categorias') {
      const categoria = atributo.categorias.find((c) => String(c.id) === partes[3]);
      if (!categoria) throw new ErrorDemo('No existe la categoría.', 404);
      if (cuerpo.etiqueta) categoria.etiqueta = cuerpo.etiqueta;
      return categoria;
    }

    if (metodo === 'POST' && partes[2] === 'recalcular') {
      return { clave: atributo.clave, revisados: 0, recalculados: 0,
               sin_categoria: [] };
    }
  }

  if (metodo === 'GET' && partes[0] === 'panelistas' && partes[2] === 'atributos') {
    const persona = bd.personas.find((p) => p.id_persona === partes[1]);
    if (!persona) throw new ErrorDemo('No existe la persona.', 404);
    return { items: atributosDe(persona) };
  }

  /* R3.13 — cargas: incorporar individuos con sus respuestas, sin panel. */
  if (metodo === 'POST' && clave === 'POST /cargas') {
    const nombre = (cuerpo.nombre || '').trim();
    if (!nombre) throw new ErrorDemo('La carga necesita un nombre.', 400);
    const carga = {
      id: siguiente('carga'), nombre,
      descripcion: (cuerpo.descripcion || '').trim() || null,
      ref_estudio: uuid(), creado_en: ahora(), creado_por: 'demo',
    };
    bd.cargas.push(carga);
    return carga;
  }

  if (clave === 'GET /cargas') return { items: [...bd.cargas].reverse() };

  if (metodo === 'POST' && partes[0] === 'cargas' && partes[2] === 'ingesta') {
    return ingestarCarga(Number(partes[1]), cuerpo);
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

  /* El demo no tiene esquema que verificar: se responde «al día» para que la
     pantalla de Cumplimiento se pueda recorrer entera. */
  if (clave === 'GET /diagnostico/esquema') {
    const migraciones = {
      boveda: ['0001_init.sql', '0002_revision_alta.sql', '0003_baja_persona.sql', '0004_fase2.sql'],
      semantica: ['0001_init.sql', '0002_vista_procedencia.sql', '0003_hash_texto.sql'],
    };
    const armar = (archivos) => ({
      completo: true, faltantes: [],
      migraciones: archivos.map((m) => ({
        migracion: m, aplicada: true, objetos: [], faltantes: [],
      })),
    });
    return {
      completo: true, como_aplicar: [],
      boveda: armar(migraciones.boveda),
      semantica: armar(migraciones.semantica),
    };
  }

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

  /* ══════════════════════════════════════════════════════════════
     Fase 3
     ══════════════════════════════════════════════════════════════ */

  // ── R3.1 · Muestreo ──────────────────────────────────────────
  if (metodo === 'POST' && partes[0] === 'encuestas' && partes[2] === 'muestreo') {
    const encuesta = bd.encuestas.find((e) => e.id === Number(partes[1]));
    if (!encuesta) throw new ErrorDemo('No existe la encuesta.', 404);
    const dimension = cuerpo.dimension || 'sexo';
    const cantidad = Number(cuerpo.cantidad) || 100;
    const umbrales = bd.umbralesFatiga[encuesta.panel_id] || { ...UMBRALES_DEFAULT };

    const categoriaDe = (persona) => (dimension === 'sexo' ? persona.sexo
      : dimension === 'tramo_etario' ? tramoEtario(persona.fecha_nacimiento)
      : persona.localidad) || '(sin dato)';

    const miembros = bd.membresias
      .filter((m) => m.panel_id === encuesta.panel_id && m.estado === 'activo')
      .map((m) => bd.personas.find((p) => p.id_persona === m.id_persona))
      .filter(Boolean);

    const objetivo = bd.objetivos.filter((o) => o.panel_id === encuesta.panel_id
      && o.dimension === dimension);
    const hayObjetivo = objetivo.length > 0;
    const total = miembros.length;
    const faltan = {};
    for (const o of objetivo) {
      const observados = miembros.filter((p) => categoriaDe(p) === o.categoria).length;
      faltan[o.categoria] = Math.max(0, Math.round(o.proporcion_objetivo * total) - observados);
    }

    const propuesta = [];
    const excluidos = [];
    const elegibles = {};
    for (const persona of miembros) {
      const convocatorias = bd.participaciones.filter((pa) =>
        pa.id_persona === persona.id_persona && pa.encuesta_id !== encuesta.id);
      const yaEnEsta = bd.participaciones.some((pa) =>
        pa.id_persona === persona.id_persona && pa.encuesta_id === encuesta.id);
      const consiente = bd.consentimientos.some((c) =>
        c.id_persona === persona.id_persona
        && c.finalidad === 'contacto_participacion' && c.estado === 'vigente');

      let motivo = null;
      if (!consiente) motivo = 'sin_consentimiento';
      else if (yaEnEsta) motivo = 'ya_convocado_a_esta_encuesta';
      else if (convocatorias.length >= umbrales.max_convocatorias_ventana) {
        motivo = 'demasiadas_convocatorias_recientes';
      }
      const fila = {
        id_persona: persona.id_persona,
        categoria: categoriaDe(persona),
        convocatorias_recientes: convocatorias.length,
        convocatorias_totales: convocatorias.length,
        respondidas: convocatorias.filter((c) => c.respondio).length,
        ultima_convocatoria: convocatorias.at(-1)?.convocado_en ?? null,
      };
      if (motivo) {
        excluidos.push({ ...fila, motivo, explicacion: EXPLICACIONES_MUESTREO[motivo] });
      } else {
        (elegibles[fila.categoria] ||= []).push(fila);
      }
    }

    const orden = Object.keys(hayObjetivo ? faltan : elegibles)
      .sort((a, b) => (faltan[b] || 0) - (faltan[a] || 0) || a.localeCompare(b));
    const avisos = [];
    if (!hayObjetivo) {
      avisos.push({
        tipo: 'sin_objetivo',
        mensaje: `El panel no tiene universo de referencia cargado para «${dimension}», `
          + 'así que no hay brecha que priorizar. La propuesta reparte parejo entre las '
          + 'categorías; para que priorice, cargá el objetivo de composición.',
      });
    }
    const totalFaltante = Object.values(faltan).reduce((a, b) => a + b, 0);
    if (hayObjetivo && !totalFaltante) {
      avisos.push({
        tipo: 'sin_brecha',
        mensaje: `El panel ya calza con su universo de referencia en «${dimension}»: `
          + 'no hay brecha que priorizar. La propuesta reparte parejo entre las '
          + 'categorías para no desbalancearlo.',
      });
    }
    for (const categoria of orden) {
      const cupo = hayObjetivo && totalFaltante
        ? Math.round((cantidad * (faltan[categoria] || 0)) / totalFaltante)
        : Math.floor(cantidad / Math.max(1, orden.length));
      const disponibles = (elegibles[categoria] || [])
        .sort((a, b) => a.convocatorias_recientes - b.convocatorias_recientes);
      const tomados = disponibles.slice(0, cupo);
      for (const c of tomados) {
        propuesta.push({ ...c, motivo_prioridad: hayObjetivo
          ? `faltan ${faltan[categoria] || 0} en «${categoria}»`
          : 'reparto parejo (sin objetivo cargado)' });
      }
      if (cupo > 0 && tomados.length < cupo && (faltan[categoria] || 0) > 0) {
        avisos.push({
          tipo: tomados.length ? 'segmento_con_elegibles_insuficientes' : 'segmento_sin_elegibles',
          dimension, categoria,
          faltan_en_el_panel: faltan[categoria] || 0,
          cupo_pedido: cupo, elegibles_encontrados: tomados.length,
          mensaje: `«${categoria}» tiene brecha (${faltan[categoria] || 0} personas) y `
            + (tomados.length
              ? `solo hay ${tomados.length} elegibles para los ${cupo} que harían falta.`
              : 'no hay ningún miembro elegible: todos están excluidos por fatiga, '
                + 'consentimiento o ya convocados.')
            + ' La brecha no se cierra con esta propuesta.',
        });
      }
      for (const sobrante of disponibles.slice(cupo)) {
        excluidos.push({ ...sobrante, motivo: 'cuota_del_segmento_ya_cubierta',
          explicacion: EXPLICACIONES_MUESTREO.cuota_del_segmento_ya_cubierta });
      }
    }
    if (propuesta.length < cantidad) {
      avisos.push({
        tipo: 'propuesta_mas_corta_que_lo_pedido',
        pedidas: cantidad, propuestas: propuesta.length,
        mensaje: `Se pidieron ${cantidad} y la propuesta trae ${propuesta.length}. `
          + (hayObjetivo && totalFaltante
            ? 'No se completó con gente de segmentos sin brecha a propósito: sumarlos '
              + 'alejaría al panel de su universo de referencia.'
            : 'No hay más miembros elegibles: mirá las exclusiones.'),
      });
    }

    const conteo = {};
    for (const e of excluidos) conteo[e.motivo] = (conteo[e.motivo] || 0) + 1;
    return {
      encuesta: { id: encuesta.id, nombre: encuesta.nombre, panel_id: encuesta.panel_id,
                  estado: encuesta.estado },
      dimension, cantidad_pedida: cantidad,
      umbrales: { ...umbrales, son_defaults: !bd.umbralesFatiga[encuesta.panel_id] },
      brecha_disponible: hayObjetivo,
      propuesta, avisos, excluidos,
      resumen_exclusiones: Object.entries(conteo)
        .sort((a, b) => b[1] - a[1])
        .map(([motivo, personas]) => ({ motivo, personas,
          explicacion: EXPLICACIONES_MUESTREO[motivo] })),
      convoca: false,
      nota: 'Es una sugerencia: nadie fue convocado.',
    };
  }

  if (metodo === 'GET' && partes[0] === 'paneles' && partes[2] === 'umbrales-fatiga') {
    const guardados = bd.umbralesFatiga[Number(partes[1])];
    return { ...(guardados || UMBRALES_DEFAULT), son_defaults: !guardados };
  }

  if (metodo === 'PUT' && partes[0] === 'paneles' && partes[2] === 'umbrales-fatiga') {
    const panelId = Number(partes[1]);
    const previos = bd.umbralesFatiga[panelId] || { ...UMBRALES_DEFAULT };
    bd.umbralesFatiga[panelId] = {
      max_convocatorias_ventana: Number(cuerpo.max_convocatorias_ventana ?? previos.max_convocatorias_ventana),
      ventana_dias: Number(cuerpo.ventana_dias ?? previos.ventana_dias),
      dias_minimos_entre: Number(cuerpo.dias_minimos_entre ?? previos.dias_minimos_entre),
      max_convocatorias_total: cuerpo.max_convocatorias_total == null || cuerpo.max_convocatorias_total === ''
        ? null : Number(cuerpo.max_convocatorias_total),
    };
    return { ...bd.umbralesFatiga[panelId], son_defaults: false };
  }

  // ── R3.3-R3.6 · Puntos, premios, canjes, bonos ───────────────
  if (metodo === 'GET' && partes[0] === 'panelistas' && partes[2] === 'puntos') {
    const idPersona = partes[1];
    const movimientos = bd.movimientos.filter((m) => m.id_persona === idPersona);
    return {
      id_persona: idPersona,
      saldo: movimientos.reduce((a, m) => a + m.puntos, 0),
      movimientos: [...movimientos].reverse(),
      por_vencer: [],
    };
  }

  if (clave === 'GET /premios') {
    const items = bd.premios.map((p) => ({
      ...p, disponible: p.activo && (p.stock === null || p.stock > 0),
    }));
    return { items: consulta.disponibles ? items.filter((p) => p.disponible) : items };
  }

  if (clave === 'POST /premios') {
    const premio = {
      id: bd.premios.length + 1,
      nombre: cuerpo.nombre, descripcion: cuerpo.descripcion || null,
      costo_puntos: Number(cuerpo.costo_puntos),
      stock: cuerpo.stock === null || cuerpo.stock === '' ? null : Number(cuerpo.stock),
      activo: true,
    };
    if (!premio.nombre) throw new ErrorDemo('El premio necesita un nombre.', 400);
    bd.premios.push(premio);
    return { ...premio, disponible: true };
  }

  if (metodo === 'PATCH' && partes[0] === 'premios') {
    const premio = bd.premios.find((p) => p.id === Number(partes[1]));
    if (!premio) throw new ErrorDemo('No existe el premio.', 404);
    for (const campo of ['nombre', 'descripcion', 'costo_puntos', 'stock', 'activo']) {
      if (campo in cuerpo) premio[campo] = cuerpo[campo];
    }
    if (premio.costo_puntos != null) premio.costo_puntos = Number(premio.costo_puntos);
    if (premio.stock === '' ) premio.stock = null;
    if (premio.stock !== null) premio.stock = Number(premio.stock);
    return { ...premio, disponible: premio.activo && (premio.stock === null || premio.stock > 0) };
  }

  if (clave === 'GET /canjes') {
    return { items: [...bd.canjes].reverse().map((c) => ({
      ...c, premio: bd.premios.find((p) => p.id === c.premio_id)?.nombre,
    })) };
  }

  if (clave === 'POST /canjes') {
    const premio = bd.premios.find((p) => p.id === Number(cuerpo.premio_id));
    if (!premio) throw new ErrorDemo('No existe el premio.', 404);
    const saldo = bd.movimientos
      .filter((m) => m.id_persona === cuerpo.id_persona)
      .reduce((a, m) => a + m.puntos, 0);
    if (saldo < premio.costo_puntos) {
      throw new ErrorDemo(
        `Saldo insuficiente: hay ${saldo} puntos y «${premio.nombre}» cuesta ${premio.costo_puntos}.`,
        400, { saldo, costo: premio.costo_puntos });
    }
    bd.movimientos.push({ id: bd.movimientos.length + 1, id_persona: cuerpo.id_persona,
      tipo: 'canje', puntos: -premio.costo_puntos, motivo: `canje · ${premio.nombre}`,
      creado_en: new Date().toISOString() });
    if (premio.stock !== null) premio.stock -= 1;
    const canje = { id: bd.canjes.length + 1, id_persona: cuerpo.id_persona,
      premio_id: premio.id, costo_puntos: premio.costo_puntos, estado: 'solicitado',
      creado_en: new Date().toISOString() };
    bd.canjes.push(canje);
    return { ...canje, premio: premio.nombre, saldo_restante: saldo - premio.costo_puntos };
  }

  if (metodo === 'PATCH' && partes[0] === 'canjes') {
    const canje = bd.canjes.find((c) => c.id === Number(partes[1]));
    if (!canje) throw new ErrorDemo('No existe el canje.', 404);
    if (canje.estado !== 'solicitado') {
      throw new ErrorDemo(`Un canje «${canje.estado}» no puede pasar a «${cuerpo.estado}».`, 400);
    }
    canje.estado = cuerpo.estado;
    canje.resuelto_en = new Date().toISOString();
    if (cuerpo.estado === 'cancelado') {
      bd.movimientos.push({ id: bd.movimientos.length + 1, id_persona: canje.id_persona,
        tipo: 'ajuste', puntos: canje.costo_puntos,
        motivo: `devolución por cancelación del canje ${canje.id}`,
        creado_en: new Date().toISOString() });
      const premio = bd.premios.find((p) => p.id === canje.premio_id);
      if (premio && premio.stock !== null) premio.stock += 1;
    }
    return { ...canje, premio: bd.premios.find((p) => p.id === canje.premio_id)?.nombre };
  }

  if (clave === 'POST /puntos/liquidar') {
    const encuestaId = Number(cuerpo.encuesta_id);
    const encuesta = bd.encuestas.find((e) => e.id === encuestaId);
    if (!encuesta) throw new ErrorDemo('No existe la encuesta.', 404);
    const base = 100;
    const bonos = bd.bonos.filter((b) => b.panel_id === encuesta.panel_id
      && (!b.hasta || new Date(b.hasta) > new Date()));
    const liquidados = [];
    for (const pa of bd.participaciones.filter((p) => p.encuesta_id === encuestaId)) {
      if (!pa.respondio || pa.calidad_estado !== 'ok') continue;
      if (bd.movimientos.some((m) => m.id_persona === pa.id_persona
          && m.encuesta_id === encuestaId && m.tipo === 'earn')) continue;
      const persona = bd.personas.find((p) => p.id_persona === pa.id_persona);
      const extra = bonos.filter((b) =>
        (b.dimension === 'sexo' && persona?.sexo === b.categoria)
        || (b.dimension === 'localidad' && persona?.localidad === b.categoria)
        || (b.dimension === 'tramo_etario' && tramoEtario(persona?.fecha_nacimiento) === b.categoria)
      ).reduce((a, b) => a + b.puntos_extra, 0);
      bd.movimientos.push({ id: bd.movimientos.length + 1, id_persona: pa.id_persona,
        tipo: 'earn', puntos: base + extra, encuesta_id: encuestaId,
        motivo: `participación de calidad · ${encuesta.nombre}`,
        creado_en: new Date().toISOString() });
      liquidados.push({ id_persona: pa.id_persona, puntos: base + extra, base, bono: extra });
    }
    return { encuesta: { id: encuesta.id, nombre: encuesta.nombre }, puntos_base: base,
      liquidados, saltados: [], total_puntos: liquidados.reduce((a, l) => a + l.puntos, 0) };
  }

  if (metodo === 'GET' && partes[0] === 'paneles' && partes[2] === 'bonos') {
    const panelId = Number(partes[1]);
    return { items: bd.bonos.filter((b) => b.panel_id === panelId).map((b) => ({
      ...b, vigente: !b.hasta || new Date(b.hasta) > new Date(),
    })) };
  }

  if (metodo === 'POST' && partes[0] === 'paneles' && partes[2] === 'bonos') {
    const bono = { id: bd.bonos.length + 1, panel_id: Number(partes[1]),
      dimension: cuerpo.dimension, categoria: cuerpo.categoria,
      puntos_extra: Number(cuerpo.puntos_extra),
      desde: new Date().toISOString(), hasta: cuerpo.hasta || null };
    if (!bono.categoria) throw new ErrorDemo('El bono necesita una categoría.', 400);
    bd.bonos.push(bono);
    return { ...bono, vigente: true };
  }

  // ── R3.7 · Inscripciones ─────────────────────────────────────
  if (clave === 'GET /inscripciones/formulario') {
    const textos = bd.textosConsentimiento.filter((t) => t.activo
      && t.finalidad === 'contacto_participacion').slice(-1);
    return { campos: ['nombre', 'documento', 'email', 'celular', 'fecha_nacimiento',
                      'sexo', 'localidad'],
             obligatorios: ['nombre', 'email'], textos, puede_recibir: textos.length > 0 };
  }

  if (clave === 'POST /inscripciones') {
    if (cuerpo.acepto_consentimiento !== true) {
      throw new ErrorDemo('Para inscribirte necesitás aceptar el consentimiento.', 400);
    }
    const datos = cuerpo.persona || {};
    const previa = bd.personas.find((p) =>
      (datos.documento && p.documento === datos.documento)
      || (datos.email && (p.email || '').toLowerCase() === datos.email.toLowerCase()));
    bd.inscripciones.push({
      id: bd.inscripciones.length + 1, ...datos,
      finalidades: ['contacto_participacion'],
      version_texto: bd.textosConsentimiento.at(-1)?.version || 'demo',
      acepto_en: new Date().toISOString(),
      resolucion: previa ? 'reutiliza' : 'crea',
      id_persona_previa: previa?.id_persona ?? null,
      estado: 'pendiente', id_persona: null, panel_id: null,
      resuelto_por: null, resuelto_en: null, motivo_rechazo: null,
      origen: 'landing', creado_en: new Date().toISOString(),
    });
    return { estado: 'recibida',
      mensaje: 'Recibimos tu inscripción. Vamos a revisarla y te vamos a contactar. '
             + 'No hace falta que la envíes de nuevo.' };
  }

  if (clave === 'GET /inscripciones') {
    const pedido = consulta.estado || 'pendiente';
    return { items: bd.inscripciones.filter((i) => i.estado === pedido).reverse() };
  }

  if (metodo === 'POST' && partes[0] === 'inscripciones' && partes[2] === 'aprobar') {
    const inscripcion = bd.inscripciones.find((i) => i.id === Number(partes[1]));
    if (!inscripcion) throw new ErrorDemo('No existe la inscripción.', 404);
    if (inscripcion.estado !== 'pendiente') {
      throw new ErrorDemo(`La inscripción ya está «${inscripcion.estado}».`, 400);
    }
    let idPersona = inscripcion.id_persona_previa;
    if (!idPersona) {
      idPersona = `demo-${bd.personas.length + 1}-${Date.now().toString(36)}`;
      bd.personas.push({ id_persona: idPersona, nombre: inscripcion.nombre,
        documento: inscripcion.documento, email: inscripcion.email,
        celular: inscripcion.celular, fecha_nacimiento: inscripcion.fecha_nacimiento,
        sexo: inscripcion.sexo, localidad: inscripcion.localidad,
        creado_en: new Date().toISOString() });
    }
    bd.consentimientos.push({ id_persona: idPersona, finalidad: 'contacto_participacion',
      estado: 'vigente', version_texto: inscripcion.version_texto,
      otorgado_en: new Date().toISOString() });
    if (cuerpo.panel_id) {
      bd.membresias.push({ panel_id: Number(cuerpo.panel_id), id_persona: idPersona,
        estado: 'activo', fecha_alta: new Date().toISOString() });
    }
    inscripcion.estado = 'aprobada';
    inscripcion.id_persona = idPersona;
    inscripcion.resuelto_en = new Date().toISOString();
    return { estado: 'aprobada', inscripcion_id: inscripcion.id, id_persona: idPersona,
      persona: inscripcion.id_persona_previa ? 'reutilizada' : 'creada',
      panel_id: cuerpo.panel_id ?? null };
  }

  if (metodo === 'POST' && partes[0] === 'inscripciones' && partes[2] === 'rechazar') {
    const inscripcion = bd.inscripciones.find((i) => i.id === Number(partes[1]));
    if (!inscripcion) throw new ErrorDemo('No existe la inscripción.', 404);
    inscripcion.estado = 'rechazada';
    inscripcion.motivo_rechazo = cuerpo.motivo || null;
    inscripcion.resuelto_en = new Date().toISOString();
    return { estado: 'rechazada', inscripcion_id: inscripcion.id };
  }

  if (clave === 'GET /textos-consentimiento') {
    return { items: [...bd.textosConsentimiento].reverse() };
  }

  if (clave === 'POST /textos-consentimiento') {
    const version = (cuerpo.version || '').trim();
    const finalidad = cuerpo.finalidad || 'contacto_participacion';
    if (!version) throw new ErrorDemo('La versión necesita un identificador.', 400);
    if (!(cuerpo.cuerpo || '').trim()) {
      throw new ErrorDemo('El texto de consentimiento no puede estar vacío.', 400);
    }
    if (bd.textosConsentimiento.some((t) => t.finalidad === finalidad && t.version === version)) {
      throw new ErrorDemo(
        `La versión «${version}» de ${finalidad} ya existe y no se puede reescribir: `
        + 'quien la consintió aceptó ese texto. Publicá una versión nueva.', 400);
    }
    const texto = { id: bd.textosConsentimiento.length + 1, finalidad, version,
      cuerpo: cuerpo.cuerpo.trim(), activo: true, creado_por: 'demo',
      creado_en: new Date().toISOString() };
    bd.textosConsentimiento.push(texto);
    return texto;
  }

  // ── R3.11 · Panel desde una consulta ─────────────────────────
  if (clave === 'POST /paneles/desde-consulta') {
    const ids = (cuerpo.resultado?.items || []).map((i) => i.id_persona);
    if (!ids.length) throw new ErrorDemo('El resultado no tiene ningún individuo.', 400);
    const panel = { id: bd.paneles.length + 1, nombre: cuerpo.nombre,
      descripcion: cuerpo.descripcion || null, estado: 'activo',
      origen: 'consulta', creado_en: new Date().toISOString() };
    bd.paneles.push(panel);
    let altas = 0;
    for (const idPersona of ids) {
      if (bd.membresias.some((m) => m.panel_id === panel.id && m.id_persona === idPersona)) continue;
      bd.membresias.push({ panel_id: panel.id, id_persona: idPersona, estado: 'activo',
        fecha_alta: new Date().toISOString() });
      altas += 1;
    }
    const noConvocables = ids.filter((idPersona) => !bd.consentimientos.some((c) =>
      c.id_persona === idPersona && c.finalidad === 'contacto_participacion'
      && c.estado === 'vigente'));
    return { ...panel, miembros: ids.length, altas, ya_eran_miembros: ids.length - altas,
      no_encontrados: [], no_convocables: noConvocables,
      aviso: noConvocables.length ? { personas: noConvocables.length,
        mensaje: `${noConvocables.length} de los ${ids.length} integrantes no tienen `
               + 'consentimiento vigente de contacto. Son miembros del panel, pero no '
               + 'pueden ser convocados hasta regularizarlo.' } : null,
      registrado_como_reidentificacion: true };
  }

  // ── R3.10 · Exportación identificada ─────────────────────────
  if (clave === 'POST /consultas/csv-identificado') {
    const items = cuerpo.reidentificacion?.items || [];
    if (!items.length) {
      throw new ErrorDemo(
        'La exportación con datos necesita un resultado ya reidentificado. '
        + 'Pedí primero la reidentificación y mandá su respuesta acá.', 400);
    }
    const campos = ['id_persona', 'nombre', 'documento', 'email', 'celular', 'contacto',
                    'sexo', 'localidad', 'tramo_etario'];
    const puntajes = Object.fromEntries(
      (cuerpo.resultado?.items || []).map((i) => [i.id_persona, i.puntaje]));
    const filas = items.map((p) => [...campos.map((c) => p[c] ?? ''),
                                    puntajes[p.id_persona] ?? '', ''].join(','));
    const ahora = new Date();
    const sello = `${ahora.getFullYear()}${String(ahora.getMonth() + 1).padStart(2, '0')}`
      + `${String(ahora.getDate()).padStart(2, '0')}-`
      + `${String(ahora.getHours()).padStart(2, '0')}${String(ahora.getMinutes()).padStart(2, '0')}`;
    return {
      csv: ['# ATENCIÓN: este archivo contiene datos personales de panelistas.',
            [...campos, 'puntaje', 'evidencia'].join(','), ...filas].join('\n'),
      nombre_archivo: `consulta-CON-DATOS-PERSONALES-${sello}.csv`,
      personas: items.length, contiene_datos_personales: true,
    };
  }

  // ══ Fase 4 · 4B — Inteligencia ═══════════════════════════════

  /* R4.1.a — el historial de atributos. En la demo los valores nunca
     cambiaron, así que cada uno tiene una sola fila abierta con `desde`
     vacío: es el primer valor conocido, que es exactamente lo que el backend
     devuelve para lo que venía de antes del historial. */
  if (metodo === 'GET' && partes[0] === 'panelistas'
      && partes[2] === 'atributos' && partes[3] === 'historial') {
    const persona = bd.personas.find((p) => p.id_persona === partes[1]);
    if (!persona) throw new ErrorDemo('No existe la persona.', 404);
    return {
      items: atributosDe(persona)
        .filter((a) => a.tipo !== 'derivado')
        .filter((a) => !consulta.clave || a.clave === consulta.clave)
        .map((a) => ({ ...a, desde: null, hasta: null, vigente: true })),
    };
  }

  /* R4.1.b — las preguntas del corpus, para elegir la primera de una serie. */
  if (clave === 'GET /preguntas') {
    const serie = consulta.serie
      ? bd.series.find((s) => s.clave === consulta.serie) : null;
    const yaEstan = new Set((serie?.preguntas || []).map((p) => p.pregunta_id));
    return {
      items: bd.semantica.preguntas
        .filter((q) => !consulta.cuestionario
          || q.cuestionario_id === Number(consulta.cuestionario))
        .map((q) => {
          const cuestionario = bd.semantica.cuestionarios
            .find((c) => c.id === q.cuestionario_id);
          return {
            pregunta_id: q.id, codigo: q.codigo, texto: q.texto, tipo: q.tipo,
            opciones: q.opciones, cuestionario_id: q.cuestionario_id,
            ola: cuestionario?.nombre, fecha_campo: cuestionario?.fecha_campo,
            ya_en_la_serie: yaEstan.has(q.id),
          };
        }),
    };
  }

  /* R4.1.b — series. */
  if (clave === 'GET /series') {
    return {
      items: bd.series
        .filter((s) => s.activa || consulta.inactivas)
        .map((s) => ({
          id: s.id, clave: s.clave, nombre: s.nombre,
          descripcion: s.descripcion, activa: s.activa,
          preguntas: s.preguntas.length, categorias: s.categorias.length,
          creada_en: s.creada_en,
        })),
    };
  }

  if (metodo === 'POST' && clave === 'POST /series') {
    const claveNueva = (cuerpo.clave || '').trim().toLowerCase();
    if (!/^[a-z][a-z0-9_]{1,48}$/.test(claveNueva)) {
      throw new ErrorDemo(
        'La clave de una serie va en minúsculas, empieza con letra y admite '
        + 'letras, números y guion bajo.', 400);
    }
    if (bd.series.some((s) => s.clave === claveNueva)) {
      throw new ErrorDemo(`Ya existe una serie con la clave «${claveNueva}».`, 409);
    }
    const serie = {
      id: siguiente('serie'), clave: claveNueva,
      nombre: (cuerpo.nombre || '').trim(), descripcion: null, activa: true,
      creada_en: ahora(),
      categorias: (cuerpo.categorias || []).map((c, i) => ({
        id: i + 1, clave: c.clave, etiqueta: c.etiqueta || c.clave,
        orden: (i + 1) * 10,
      })),
      preguntas: [],
    };
    bd.series.push(serie);
    auditarSerie(serie.clave, 'alta', { nombre: serie.nombre });
    return serieCompleta(serie);
  }

  if (partes[0] === 'series' && partes.length >= 2) {
    const serie = bd.series.find((s) => s.clave === partes[1]);
    if (!serie) throw new ErrorDemo(`No existe la serie «${partes[1]}».`, 404);

    if (metodo === 'GET' && partes.length === 2) return serieCompleta(serie);

    if (metodo === 'PATCH' && partes.length === 2) {
      ['nombre', 'descripcion'].forEach((campo) => {
        if (campo in cuerpo) serie[campo] = (cuerpo[campo] || '').trim() || null;
      });
      if ('activa' in cuerpo) serie.activa = Boolean(cuerpo.activa);
      auditarSerie(serie.clave, serie.activa === false ? 'baja' : 'edicion', cuerpo);
      return serieCompleta(serie);
    }

    if (metodo === 'POST' && partes[2] === 'categorias') {
      serie.categorias.push({
        id: serie.categorias.length + 1, clave: cuerpo.clave,
        etiqueta: cuerpo.etiqueta || cuerpo.clave,
        orden: (serie.categorias.length + 1) * 10,
      });
      auditarSerie(serie.clave, 'categoria', cuerpo);
      return serieCompleta(serie);
    }

    if (metodo === 'POST' && partes[2] === 'preguntas') {
      const preguntaId = Number(cuerpo.pregunta_id);
      const pregunta = bd.semantica.preguntas.find((q) => q.id === preguntaId);
      if (!pregunta) throw new ErrorDemo('No existe la pregunta.', 404);
      const cuestionario = bd.semantica.cuestionarios
        .find((c) => c.id === pregunta.cuestionario_id);
      const yaDeEsaOla = serie.preguntas
        .filter((p) => p.cuestionario_id === pregunta.cuestionario_id);
      let fila = serie.preguntas.find((p) => p.pregunta_id === preguntaId);
      if (!fila) {
        fila = { pregunta_id: preguntaId, cuestionario_id: pregunta.cuestionario_id,
                 mapeo: {}, origen: cuerpo.origen || 'declarada' };
        serie.preguntas.push(fila);
      } else { fila.origen = cuerpo.origen || fila.origen; }
      if (cuerpo.mapeo) fila.mapeo = { ...fila.mapeo, ...cuerpo.mapeo };
      auditarSerie(serie.clave, 'pregunta_agregada',
                   { pregunta_id: preguntaId, ola: cuestionario?.nombre });
      const salida = serieCompleta(serie);
      if (yaDeEsaOla.length) {
        salida.aviso = `La serie ya tenía ${yaDeEsaOla.length} pregunta(s) de la `
          + `ola «${cuestionario?.nombre}». Al comparar entre olas, esa gente `
          + 'se contaría dos veces.';
      }
      return salida;
    }

    if (metodo === 'DELETE' && partes[2] === 'preguntas') {
      const preguntaId = Number(partes[3]);
      serie.preguntas = serie.preguntas.filter((p) => p.pregunta_id !== preguntaId);
      auditarSerie(serie.clave, 'pregunta_quitada', { pregunta_id: preguntaId });
      return serieCompleta(serie);
    }

    if (metodo === 'PUT' && partes[2] === 'preguntas' && partes[4] === 'mapeo') {
      const preguntaId = Number(partes[3]);
      const fila = serie.preguntas.find((p) => p.pregunta_id === preguntaId);
      if (!fila) throw new ErrorDemo('Esa pregunta no está en la serie.', 404);
      const validas = new Set(serie.categorias.map((c) => c.clave));
      const desconocidas = Object.values(cuerpo.mapeo || {})
        .filter((c) => c && !validas.has(c));
      if (desconocidas.length) {
        throw new ErrorDemo(
          `La serie no tiene las categorías ${desconocidas.join(', ')}.`, 400);
      }
      fila.mapeo = { ...(cuerpo.mapeo || {}) };
      auditarSerie(serie.clave, 'mapeo', { pregunta_id: preguntaId });
      return serieCompleta(serie);
    }

    /* Sugerencias. En la demo el parecido se calcula con palabras
       compartidas en vez de embeddings: alcanza para que la pantalla se
       comporte igual, que es lo que la demo tiene que mostrar. */
    if (metodo === 'GET' && partes[2] === 'sugerencias') {
      const cubiertas = new Set(serie.preguntas.map((p) => p.cuestionario_id));
      const referencia = serie.preguntas
        .map((p) => bd.semantica.preguntas.find((q) => q.id === p.pregunta_id))
        .filter(Boolean);
      if (!referencia.length) {
        return { serie: serie.clave, propone_no_agrega: true, sugerencias: [],
                 motivo: 'La serie no tiene todavía ninguna pregunta: sin una '
                   + 'de referencia no hay contra qué comparar. Agregá la '
                   + 'primera a mano y de ahí en más el sistema propone las '
                   + 'de las otras olas.' };
      }
      const sugerencias = bd.semantica.preguntas
        .filter((q) => !cubiertas.has(q.cuestionario_id))
        .map((q) => {
          const distancia = Math.min(...referencia.map((r) => 1 - parecido(r.texto, q.texto)));
          const cuestionario = bd.semantica.cuestionarios
            .find((c) => c.id === q.cuestionario_id);
          return {
            pregunta_id: q.id, codigo: q.codigo, texto: q.texto, tipo: q.tipo,
            opciones: q.opciones, cuestionario_id: q.cuestionario_id,
            ola: cuestionario?.nombre, fecha_campo: cuestionario?.fecha_campo,
            distancia: Number(distancia.toFixed(4)),
          };
        })
        /* El umbral de la demo es más flojo que el del backend a propósito:
           acá el parecido es Jaccard de palabras y allá es coseno sobre
           embeddings, que reconoce «bebida de consumo habitual» y «bebida que
           consume habitualmente» como casi lo mismo y Jaccard no. Con el
           umbral del backend la demo no propondría nada y la pantalla
           parecería rota. */
        .filter((s) => s.distancia <= 0.9)
        .sort((a, b) => a.distancia - b.distancia)
        .slice(0, 20);
      return { serie: serie.clave, propone_no_agrega: true, sugerencias };
    }

    if (metodo === 'GET' && partes[2] === 'auditoria') {
      return { items: bd.auditoriaSeries
        .filter((r) => r.serie === serie.clave).slice().reverse() };
    }

    if (metodo === 'GET' && partes[2] === 'transiciones') {
      return transicionesDemo(serie);
    }
  }

  /* R4.1.c — la línea de tiempo de una persona. */
  if (metodo === 'GET' && partes[0] === 'panelistas' && partes[2] === 'longitudinal') {
    const persona = bd.personas.find((p) => p.id_persona === partes[1]);
    if (!persona) throw new ErrorDemo('No existe la persona.', 404);
    const olas = bd.participaciones
      .filter((p) => p.id_persona === persona.id_persona)
      .map((p) => {
        const encuesta = bd.encuestas.find((e) => e.id === p.encuesta_id);
        const panel = bd.paneles.find((x) => x.id === encuesta?.panel_id);
        return { participacion: p, encuesta, panel };
      })
      .filter((o) => o.encuesta)
      .sort((a, b) => String(a.encuesta.fecha_campo || '')
        .localeCompare(String(b.encuesta.fecha_campo || '')));
    bd.reidentificaciones.push({
      id: siguiente('reident'), id_persona: persona.id_persona,
      motivo: 'ficha', contexto: { vista: 'longitudinal', olas: olas.length },
      actor_email: 'demo@equipos.com.uy', creado_en: ahora(),
    });
    return {
      id_persona: persona.id_persona, nombre: persona.nombre,
      estado: persona.estado,
      olas: olas.map(({ participacion, encuesta, panel }) => ({
        encuesta_id: encuesta.id, encuesta: encuesta.nombre,
        panel: panel?.nombre, panel_id: panel?.id,
        fecha_campo: encuesta.fecha_campo,
        convocado_en: participacion.convocado_en,
        respondio: participacion.respondio,
        respondio_en: participacion.respondio_en || null,
        calidad_estado: participacion.calidad_estado || 'pendiente',
        origen: participacion.origen, ref_estudio: encuesta.ref_estudio,
        respuestas: respuestasDe(persona.id_persona, encuesta.ref_estudio),
      })),
      aviso: olas.length === 1
        ? 'Esta persona participó en una sola ola: no hay evolución que '
          + 'mostrar todavía.' : null,
    };
  }

  /* R4.2 — pesos del optimizador. */
  if (partes[0] === 'paneles' && partes[2] === 'pesos-optimizador') {
    const panelId = Number(partes[1]);
    if (metodo === 'PUT') {
      const nuevos = { ...PESOS_DEMO, ...(bd.pesosOptimizador[panelId] || {}) };
      ['peso_brecha', 'peso_fatiga', 'peso_equidad'].forEach((k) => {
        if (k in cuerpo) {
          const valor = Number(cuerpo[k]);
          if (Number.isNaN(valor)) throw new ErrorDemo(`«${k}» tiene que ser un número.`, 400);
          if (valor < 0) {
            throw new ErrorDemo(
              `«${k}» no puede ser negativo: un peso negativo invertiría el `
              + 'sentido de la restricción.', 400);
          }
          nuevos[k] = valor;
        }
      });
      bd.pesosOptimizador[panelId] = nuevos;
      return { ...nuevos, configurados: true,
               actualizado_por: 'demo@equipos.com.uy', actualizado_en: ahora() };
    }
    const guardados = bd.pesosOptimizador[panelId];
    return guardados
      ? { ...guardados, configurados: true,
          actualizado_por: 'demo@equipos.com.uy', actualizado_en: ahora() }
      : { ...PESOS_DEMO, configurados: false };
  }

  if (metodo === 'GET' && partes[0] === 'encuestas' && partes[2] === 'optimizar') {
    return partes[3] === 'comparar'
      ? compararDemo(Number(partes[1]), consulta)
      : optimizarDemo(Number(partes[1]), consulta);
  }

  throw new ErrorDemo(`El modo demo no implementa ${clave}.`, 404);
}

/* ── Fase 4 · 4B · ayudantes de la demo ─────────────────────────── */

const PESOS_DEMO = { peso_brecha: 10.0, peso_fatiga: 1.0, peso_equidad: 0.5 };

/* La categoría de una persona en la dimensión de cuota. Misma resolución que
   usa el muestreo por reglas, para que los dos métodos vean lo mismo. */
function categoriaEnDimension(persona, dimension) {
  if (dimension === 'sexo') return persona.sexo || '(sin dato)';
  if (dimension === 'tramo_etario') {
    return tramoEtario(persona.fecha_nacimiento) || '(sin dato)';
  }
  if (dimension === 'localidad') return persona.localidad || '(sin dato)';
  return (persona.atributos || {})[dimension] || '(sin dato)';
}

/* Los candidatos que pasan todas las restricciones duras. Las blandas no
   filtran acá: se penalizan después, que es justamente la diferencia entre
   el optimizador y las reglas. */
function elegiblesDemo(encuesta, dimension, umbrales) {
  const elegibles = [];
  const excluidos = [];
  const conteo = {};
  bd.membresias
    .filter((m) => m.panel_id === encuesta.panel_id && m.estado === 'activo')
    .map((m) => bd.personas.find((p) => p.id_persona === m.id_persona))
    .filter(Boolean)
    .forEach((persona) => {
      const convocatorias = bd.participaciones.filter((pa) =>
        pa.id_persona === persona.id_persona && pa.encuesta_id !== encuesta.id);
      const yaEnEsta = bd.participaciones.some((pa) =>
        pa.id_persona === persona.id_persona && pa.encuesta_id === encuesta.id);
      const consiente = bd.consentimientos.some((c) =>
        c.id_persona === persona.id_persona
        && c.finalidad === 'contacto_participacion' && c.estado === 'vigente');

      let motivo = null;
      if (!consiente) motivo = 'sin_consentimiento';
      else if (yaEnEsta) motivo = 'ya_convocado_a_esta_encuesta';
      else if (convocatorias.length >= umbrales.max_convocatorias_ventana) {
        motivo = 'demasiadas_convocatorias_recientes';
      }

      const fila = {
        id_persona: persona.id_persona,
        categoria: categoriaEnDimension(persona, dimension),
        recientes: convocatorias.length,
        totales: convocatorias.length,
        respondidas: convocatorias.filter((c) => c.respondio).length,
      };
      if (motivo) {
        conteo[motivo] = (conteo[motivo] || 0) + 1;
        excluidos.push({
          id_persona: fila.id_persona, categoria: fila.categoria, motivo,
          explicacion: EXPLICACIONES_MUESTREO[motivo] || motivo,
          convocatorias_recientes: fila.recientes,
          convocatorias_totales: fila.totales,
        });
      } else { elegibles.push(fila); }
    });
  return { elegibles, excluidos, conteo };
}

/* Cuántas personas de cada categoría querría tener la muestra. Con universo
   cargado, su proporción por el tamaño; sin él, el reparto proporcional a los
   elegibles, que es lo más neutro que se puede hacer sin inventar un
   universo. */
function objetivosDemo(panelId, dimension, cantidad, elegibles) {
  const cargados = bd.objetivos.filter((o) =>
    o.panel_id === panelId && o.dimension === dimension);
  if (cargados.length) {
    const objetivos = {};
    cargados.forEach((o) => {
      objetivos[o.categoria] = Math.round(o.proporcion_objetivo * cantidad);
    });
    return { objetivos, hay_objetivo: true };
  }
  const porCategoria = {};
  elegibles.forEach((c) => {
    porCategoria[c.categoria] = (porCategoria[c.categoria] || 0) + 1;
  });
  const total = Object.values(porCategoria).reduce((s, n) => s + n, 0) || 1;
  const objetivos = {};
  Object.entries(porCategoria).forEach(([categoria, n]) => {
    objetivos[categoria] = Math.round((n / total) * cantidad);
  });
  return { objetivos, hay_objetivo: false };
}

function auditarSerie(serie, accion, detalle) {
  bd.auditoriaSeries.push({
    serie, accion, detalle: detalle || {},
    actor: 'demo@equipos.com.uy', creado_en: ahora(),
  });
}

function serieCompleta(serie) {
  return {
    id: serie.id, clave: serie.clave, nombre: serie.nombre,
    descripcion: serie.descripcion, activa: serie.activa,
    creada_en: serie.creada_en, categorias: serie.categorias,
    preguntas: serie.preguntas.map((fila) => {
      const pregunta = bd.semantica.preguntas.find((q) => q.id === fila.pregunta_id);
      const cuestionario = bd.semantica.cuestionarios
        .find((c) => c.id === fila.cuestionario_id);
      const opciones = pregunta?.opciones || {};
      return {
        id: fila.pregunta_id, pregunta_id: fila.pregunta_id,
        codigo: pregunta?.codigo, texto: pregunta?.texto, tipo: pregunta?.tipo,
        opciones, origen: fila.origen, ola: cuestionario?.nombre,
        cuestionario_id: fila.cuestionario_id,
        ref_estudio: cuestionario?.ref_estudio,
        fecha_campo: cuestionario?.fecha_campo, mapeo: fila.mapeo,
        sin_mapear: Object.keys(opciones).filter((o) => !fila.mapeo[o]),
      };
    }),
  };
}

/* Parecido por palabras compartidas (Jaccard). En el backend esto es
   similitud de embeddings; acá alcanza con que ordene igual para que la
   pantalla se comporte igual. */
function parecido(a, b) {
  const palabras = (texto) => new Set(String(texto || '').toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zñ\s]/g, ' ').split(/\s+/)
    .filter((p) => p.length > 3)
    // Una raíz corta, para que «consume» y «consumo» cuenten como la misma
    // palabra. Es la aproximación más barata a lo que el coseno sobre
    // embeddings hace de verdad.
    .map((p) => p.slice(0, 5)));
  const unos = palabras(a);
  const otros = palabras(b);
  if (!unos.size || !otros.size) return 0;
  const comunes = [...unos].filter((p) => otros.has(p)).length;
  return comunes / (unos.size + otros.size - comunes);
}

function respuestasDe(idPersona, refEstudio) {
  const cuestionario = bd.semantica.cuestionarios
    .find((c) => c.ref_estudio === refEstudio);
  const individuo = bd.semantica.individuos.find((i) => i.id_persona === idPersona);
  if (!cuestionario || !individuo) return [];
  return bd.semantica.preguntas
    .filter((q) => q.cuestionario_id === cuestionario.id)
    .map((q) => {
      const respuesta = bd.semantica.respuestas
        .find((r) => r.pregunta_id === q.id && r.individuo_id === individuo.id);
      return respuesta
        ? { codigo: q.codigo, texto: q.texto, tipo: q.tipo,
            respuesta: respuesta.valor_texto }
        : null;
    })
    .filter(Boolean);
}

const SIN_MAPEAR_DEMO = '(sin mapear)';
const SIN_RESPUESTA_DEMO = '(no respondió)';

function categoriaDemo(valor, opciones, mapeo) {
  if (valor == null || String(valor).trim() === '') return SIN_RESPUESTA_DEMO;
  const texto = String(valor).trim();
  let clave = texto;
  Object.entries(opciones || {}).forEach(([opcion, etiqueta]) => {
    if (String(etiqueta).trim().toLowerCase() === texto.toLowerCase()) clave = opcion;
  });
  return mapeo[clave] || mapeo[texto] || SIN_MAPEAR_DEMO;
}

function transicionesDemo(serie) {
  const completa = serieCompleta(serie);
  if (completa.preguntas.length < 2) {
    throw new ErrorDemo(
      `La serie «${serie.clave}» tiene ${completa.preguntas.length} pregunta(s): `
      + 'para ver un movimiento hacen falta dos olas.', 400);
  }
  /* Mismo orden que el backend: por fecha de campo, y las que no la tienen
     al final. Ponerlas primero haría que «la primera ola» de una serie fuera
     la que menos se sabe cuándo pasó. */
  const ordenadas = completa.preguntas.slice().sort((a, b) =>
    String(a.fecha_campo || '9999').localeCompare(String(b.fecha_campo || '9999')));
  const origen = ordenadas[0];
  const destino = ordenadas[ordenadas.length - 1];

  const leer = (pregunta) => {
    const salida = {};
    bd.semantica.respuestas
      .filter((r) => r.pregunta_id === pregunta.pregunta_id)
      .forEach((r) => {
        const individuo = bd.semantica.individuos.find((i) => i.id === r.individuo_id);
        if (individuo) {
          salida[individuo.id_persona] =
            categoriaDemo(r.valor_texto, pregunta.opciones, pregunta.mapeo);
        }
      });
    return salida;
  };

  const antes = leer(origen);
  const despues = leer(destino);
  const enLasDos = Object.keys(antes).filter((id) => id in despues);
  const matriz = {};
  enLasDos.forEach((id) => {
    const cl = `${antes[id]}→${despues[id]}`;
    matriz[cl] = (matriz[cl] || 0) + 1;
  });
  const categorias = completa.categorias.map((c) => c.clave);
  const estables = categorias.reduce(
    (suma, c) => suma + (matriz[`${c}→${c}`] || 0), 0);

  return {
    serie: serie.clave,
    desde: { cuestionario_id: origen.cuestionario_id, ola: origen.ola,
             fecha_campo: origen.fecha_campo, codigo: origen.codigo },
    hasta: { cuestionario_id: destino.cuestionario_id, ola: destino.ola,
             fecha_campo: destino.fecha_campo, codigo: destino.codigo },
    categorias,
    en_las_dos_olas: enLasDos.length,
    celdas: Object.entries(matriz).map(([cl, personas]) => {
      const [desde, hasta] = cl.split('→');
      return { desde, hasta, personas };
    }),
    estables,
    se_movieron: enLasDos.length - estables,
    solo_en_una: {
      solo_al_inicio: Object.keys(antes).filter((id) => !(id in despues)).length,
      solo_al_final: Object.keys(despues).filter((id) => !(id in antes)).length,
      aviso: 'No entran en la matriz: no hay un «de dónde» o un «a dónde» para '
        + 'ellas, y ponerlas en la diagonal diría que no cambiaron.',
    },
    sin_mapear: enLasDos.filter((id) =>
      antes[id] === SIN_MAPEAR_DEMO || despues[id] === SIN_MAPEAR_DEMO).length,
  };
}

/* El optimizador de la demo: el mismo voraz del backend, sobre los datos de
   memoria. Se replica en vez de simplificarse porque lo que la pantalla
   tiene que mostrar —el «por qué entró» de cada uno— sale justamente de la
   cuenta. */
function optimizarDemo(encuestaId, consulta, topeVentana) {
  const encuesta = bd.encuestas.find((e) => e.id === encuestaId);
  if (!encuesta) throw new ErrorDemo(`No existe la encuesta ${encuestaId}.`, 404);
  const dimension = consulta.dimension || 'sexo';
  const cantidad = Number(consulta.cantidad) || 100;
  if (cantidad <= 0) throw new ErrorDemo('«cantidad» tiene que ser mayor que cero.', 400);

  const umbrales = { ...UMBRALES_DEFAULT, ...(bd.umbralesFatiga[encuesta.panel_id] || {}) };
  if (topeVentana != null) umbrales.max_convocatorias_ventana = topeVentana;
  const pesos = { ...PESOS_DEMO, ...(bd.pesosOptimizador[encuesta.panel_id] || {}) };

  const { elegibles, excluidos, conteo } = elegiblesDemo(encuesta, dimension, umbrales);
  const objetivos = objetivosDemo(encuesta.panel_id, dimension, cantidad, elegibles);
  const hayObjetivo = objetivos.hay_objetivo;

  const promedio = elegibles.length
    ? elegibles.reduce((s, c) => s + c.totales, 0) / elegibles.length : 0;
  const restantes = [...elegibles];
  const seleccion = [];
  const contados = {};
  const escala = Math.max(1, cantidad);

  while (restantes.length && seleccion.length < cantidad) {
    let mejor = null;
    let mejorClave = null;
    let detalle = null;
    restantes.forEach((c) => {
      const deficit = (objetivos.objetivos[c.categoria] || 0) - (contados[c.categoria] || 0);
      const aporte = pesos.peso_brecha * (2 * deficit - 1) / escala;
      const fatiga = pesos.peso_fatiga
        * Math.min(1, c.recientes / Math.max(1, umbrales.max_convocatorias_ventana));
      const equidad = pesos.peso_equidad * (c.totales / (1 + promedio));
      const puntaje = aporte - fatiga - equidad;
      const cl = [puntaje, -c.recientes, -c.totales, c.respondidas, c.id_persona];
      if (!mejorClave || comparaClaves(cl, mejorClave) > 0) {
        mejor = c; mejorClave = cl;
        detalle = { deficit, aporte, fatiga, equidad, puntaje };
      }
    });
    if (hayObjetivo && detalle.deficit <= 0) break;
    restantes.splice(restantes.indexOf(mejor), 1);
    contados[mejor.categoria] = (contados[mejor.categoria] || 0) + 1;
    seleccion.push({
      id_persona: mejor.id_persona, categoria: mejor.categoria,
      orden: seleccion.length + 1,
      convocatorias_recientes: mejor.recientes,
      convocatorias_totales: mejor.totales,
      respuestas_previas: mejor.respondidas,
      porque: {
        deficit_del_segmento_al_entrar: detalle.deficit,
        aporte_a_la_brecha: Number(detalle.aporte.toFixed(4)),
        costo_fatiga: Number(detalle.fatiga.toFixed(4)),
        costo_equidad: Number(detalle.equidad.toFixed(4)),
        puntaje: Number(detalle.puntaje.toFixed(4)),
      },
    });
  }

  const elegidos = new Set(seleccion.map((p) => p.id_persona));
  elegibles.filter((c) => !elegidos.has(c.id_persona)).forEach((c) => {
    conteo.cuota_del_segmento_ya_cubierta =
      (conteo.cuota_del_segmento_ya_cubierta || 0) + 1;
    excluidos.push({
      id_persona: c.id_persona, categoria: c.categoria,
      motivo: 'cuota_del_segmento_ya_cubierta',
      explicacion: 'su segmento ya alcanzó el objetivo',
      convocatorias_recientes: c.recientes, convocatorias_totales: c.totales,
    });
  });

  const sinCubrir = {};
  Object.entries(objetivos.objetivos).forEach(([categoria, objetivo]) => {
    const falta = objetivo - (contados[categoria] || 0);
    if (falta > 0) sinCubrir[categoria] = falta;
  });

  const totalObjetivo = Object.values(objetivos.objetivos).reduce((s, n) => s + n, 0);
  const alcanzable = hayObjetivo
    ? Math.min(cantidad, totalObjetivo || cantidad) : cantidad;
  const factible = seleccion.length >= alcanzable && !Object.keys(sinCubrir).length;

  const salida = {
    encuesta: { id: encuesta.id, nombre: encuesta.nombre,
                panel_id: encuesta.panel_id, estado: encuesta.estado },
    metodo: 'optimizador', dimension, canal: consulta.canal || null,
    cantidad_pedida: cantidad, umbrales, pesos,
    brecha_disponible: hayObjetivo, objetivos: objetivos.objetivos,
    propuesta: seleccion, elegibles: elegibles.length, excluidos,
    resumen_exclusiones: Object.entries(conteo)
      .sort((a, b) => b[1] - a[1])
      .map(([motivo, personas]) => ({
        motivo, personas,
        explicacion: EXPLICACIONES_MUESTREO[motivo] || motivo,
      })),
    factible, sin_cubrir: factible ? {} : sinCubrir, convoca: false,
  };
  if (factible) { salida.alternativas = []; return salida; }
  if (topeVentana != null) { salida.alternativas = []; return salida; }

  salida.motivo = seleccion.length < cantidad
    ? `No se puede llenar la cuota pedida sin violar una restricción: hay `
      + `${elegibles.length} persona(s) elegibles y se pidieron ${cantidad}.`
    : `Se llegó al tamaño pedido pero quedaron categorías sin cubrir: no hay `
      + `elegibles suficientes en ${Object.keys(sinCubrir).sort().join(', ')}.`;
  salida.elige_el_sistema = false;
  salida.alternativas = alternativasDemo(
    encuestaId, consulta, umbrales, elegibles, seleccion, cantidad,
    objetivos, sinCubrir, hayObjetivo);
  return salida;
}

function comparaClaves(a, b) {
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] === b[i]) continue;
    return a[i] > b[i] ? 1 : -1;
  }
  return 0;
}

function alternativasDemo(encuestaId, consulta, umbrales, elegibles, seleccion,
                          cantidad, objetivos, sinCubrir, hayObjetivo) {
  const alternativas = [{
    opcion: 'reducir_el_tamano',
    descripcion: `Armar la muestra con ${seleccion.length} personas en vez de `
      + `${cantidad}, sin tocar los umbrales ni aceptar brecha.`,
    tamano: seleccion.length,
    cuesta: `${cantidad - seleccion.length} persona(s) menos de muestra`,
  }];

  const tope = umbrales.max_convocatorias_ventana;
  for (const paso of [1, 2, 3]) {
    const relajado = optimizarDemo(encuestaId, consulta, tope + paso);
    const suma = relajado.elegibles - elegibles.length;
    // Si mover el tope no hace elegible a nadie, nadie quedaba afuera por
    // fatiga: se dice una vez en vez de repetir tres «0 personas más».
    if (!suma) {
      alternativas.push({
        opcion: 'aflojar_la_fatiga',
        descripcion: 'Subir el tope de la ventana no cambia nada: nadie está '
          + 'quedando afuera por fatiga.',
        max_convocatorias_ventana: tope + paso,
        tamano: relajado.propuesta.length, elegibles: relajado.elegibles,
        sin_cubrir: relajado.sin_cubrir,
        cuesta: 'Nada, y tampoco aporta nada: el cuello de botella no es la fatiga.',
        alcanza: false,
      });
      break;
    }
    const alcanza = relajado.propuesta.length >= cantidad
      && !Object.keys(relajado.sin_cubrir).length;
    alternativas.push({
      opcion: 'aflojar_la_fatiga',
      descripcion: `Subir el tope de la ventana de ${tope} a ${tope + paso} convocatorias.`,
      max_convocatorias_ventana: tope + paso,
      tamano: relajado.propuesta.length, elegibles: relajado.elegibles,
      sin_cubrir: relajado.sin_cubrir,
      cuesta: `${suma} persona(s) más pasan a ser convocables, a costa de `
        + 'molestar más seguido a quienes ya venían siendo convocados.',
      alcanza,
    });
    if (alcanza) break;
  }

  if (hayObjetivo && Object.keys(sinCubrir).length) {
    const total = seleccion.length || 1;
    const desvio = Object.entries(objetivos.objetivos).reduce((suma, [categoria, objetivo]) =>
      suma + Math.abs(
        seleccion.filter((p) => p.categoria === categoria).length / total
        - objetivo / Math.max(1, cantidad)), 0) / 2;
    alternativas.push({
      opcion: 'aceptar_la_brecha',
      descripcion: 'Convocar igual y asumir que la muestra no calza con el '
        + 'universo de referencia.',
      sin_cubrir: sinCubrir, disimilitud: Number(desvio.toFixed(4)),
      cuesta: `La muestra queda a ${(desvio * 100).toFixed(1)} puntos de `
        + 'disimilitud del universo: habría que mover esa fracción de categoría '
        + 'para que calce.',
    });
  }
  return alternativas;
}

async function compararDemo(encuestaId, consulta) {
  // Por la misma puerta que usa la pantalla: así los dos métodos se comparan
  // sobre exactamente lo mismo, sin una segunda implementación de las reglas
  // que se pueda separar de la primera.
  const porReglas = await responder('POST', `/encuestas/${encuestaId}/muestreo`, {
    dimension: consulta.dimension || 'sexo',
    cantidad: Number(consulta.cantidad) || 100,
  });
  const optimizada = optimizarDemo(encuestaId, consulta);
  const idsReglas = new Set(porReglas.propuesta.map((p) => p.id_persona));
  const idsOptimo = new Set(optimizada.propuesta.map((p) => p.id_persona));

  const porCategoria = (propuesta) => {
    const salida = {};
    propuesta.forEach((p) => { salida[p.categoria] = (salida[p.categoria] || 0) + 1; });
    return salida;
  };
  const fatigaMedia = (propuesta) => (propuesta.length
    ? Number((propuesta.reduce((s, p) => s + p.convocatorias_recientes, 0)
      / propuesta.length).toFixed(3)) : 0);

  return {
    encuesta_id: encuestaId, dimension: consulta.dimension || 'sexo',
    cantidad_pedida: Number(consulta.cantidad) || 100,
    reglas: { metodo: 'reglas (R3.1)', personas: idsReglas.size,
              por_categoria: porCategoria(porReglas.propuesta),
              fatiga_media: fatigaMedia(porReglas.propuesta) },
    optimizador: { metodo: 'optimizador (R4.2)', personas: idsOptimo.size,
                   por_categoria: porCategoria(optimizada.propuesta),
                   fatiga_media: fatigaMedia(optimizada.propuesta),
                   factible: optimizada.factible },
    en_las_dos: [...idsReglas].filter((i) => idsOptimo.has(i)).length,
    solo_en_reglas: [...idsReglas].filter((i) => !idsOptimo.has(i)).sort(),
    solo_en_optimizador: [...idsOptimo].filter((i) => !idsReglas.has(i)).sort(),
    coinciden: idsReglas.size === idsOptimo.size
      && [...idsReglas].every((i) => idsOptimo.has(i)),
  };
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
