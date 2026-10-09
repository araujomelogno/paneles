-- R-CS · Ejecuciones de consulta: el registro de cada una y el trabajo
-- persistido de las de alcance completo.
-- (specs/ADDENDUM_solicitud_consultas_semanticas.md, A1.3, A2, A3, cambio 5)
--
-- ============================================================
--  Por qué existe
-- ============================================================
-- Dos cosas que hasta acá no tenían dónde quedar:
--
-- 1. **El diagnóstico de cada ejecución** (A1.3). El addendum pide confirmar
--    si el reranker corrió en una consulta concreta, y la respuesta vivía
--    solo en el JSON que recibió el navegador. Ahora cada ejecución deja su
--    fila: degradaciones, proveedores efectivos, `input_type` del criterio y
--    el costo real. Sin resultados ni personas: el resultado no se guarda
--    nunca (P1, consultas guardadas), y esta tabla no es la excepción.
--
-- 2. **La ejecución completa** (cambio 5). Verificar todas las unidades
--    elegibles son cientos de llamadas a Claude por criterio: no entra en una
--    request. Se hace con el mismo patrón que la ingesta diferida (0019):
--    el plan se congela al confirmar, se parte en lotes, Cloud Tasks procesa
--    uno por tarea, el avance se deriva de los lotes y no de un contador, y
--    un lote fallido se reintenta solo. Lo único nuevo es el **presupuesto**:
--    cada lote mira cuánto se gastó (sumando los lotes, no un acumulador)
--    antes de llamar a Claude, y si no le alcanza no llama.
--
-- Los veredictos por unidad **no** van acá: son oraciones sobre el contenido
-- de las respuestas y viven en el store semántico (`veredicto_unidad`,
-- semantica/0009). Acá quedan ids de pregunta y huellas de texto, nunca texto.

-- ============================================================
--  1 · Catálogos de estado
-- ============================================================
create table estado_consulta_ejecucion (
  codigo      text primary key,
  etiqueta    text not null,
  descripcion text not null,
  terminal    boolean not null default false,
  orden       int not null default 100
);

comment on table estado_consulta_ejecucion is
  'Los estados de una ejecución de consulta. La pantalla lee la etiqueta de '
  'acá en vez de repetir el diccionario.';

insert into estado_consulta_ejecucion (codigo, etiqueta, descripcion, terminal, orden) values
  ('encolada', 'Encolada',
   'La ejecución completa quedó guardada y sus lotes encolados.', false, 10),
  ('procesando', 'Procesando',
   'Al menos un lote terminó o está en curso, y quedan lotes por hacer.', false, 20),
  ('terminada', 'Terminada',
   'Todas las unidades se verificaron (o, en una exploratoria, la consulta '
   'respondió).', true, 30),
  ('terminada_con_errores', 'Terminada con errores',
   'Todos los lotes se intentaron y alguno quedó fallido. Sus unidades '
   'figuran «sin verificar»: no excluyen ni aprueban.', true, 40),
  ('detenida_por_presupuesto', 'Detenida por presupuesto',
   'Se alcanzó el presupuesto antes de verificar todo. Lo verificado vale; '
   'lo demás queda «sin verificar».', true, 50),
  ('cancelada', 'Cancelada',
   'Alguien la canceló. Los lotes que no habían empezado no se procesan.', true, 60),
  ('fallida', 'Fallida',
   'No se verificó ningún lote.', true, 70);

create table estado_lote_consulta (
  codigo      text primary key,
  etiqueta    text not null,
  descripcion text not null,
  orden       int not null default 100
);

insert into estado_lote_consulta (codigo, etiqueta, descripcion, orden) values
  ('pendiente',  'Pendiente',  'Encolado, todavía sin tomar.', 10),
  ('procesando', 'Procesando', 'Una tarea lo tomó y está verificando.', 20),
  ('ok',         'Terminado',  'Sus unidades tienen veredicto.', 30),
  ('fallido',    'Fallido',    'Agotó los reintentos o falló por un error '
                               'que reintentar no arregla.', 40),
  ('omitido',    'Omitido',    'No se procesó: el presupuesto no alcanzaba, '
                               'o la ejecución se canceló.', 50);

-- ============================================================
--  2 · La ejecución
-- ============================================================
create table consulta_ejecucion (
  id               uuid primary key,
  -- `alcance` y `modo` son dos ejes distintos (cambio 2): el alcance dice
  -- cuánto se verifica; el modo, cómo se interpreta lo que no se pudo
  -- afirmar. Una consulta completa puede ser laxa.
  alcance          text not null check (alcance in ('exploratorio', 'completo')),
  modo             text not null,
  tipo             text not null,                -- semantica | mixta | demografica
  definicion       jsonb not null,               -- la normalizada; sin resultado
  estado           text not null references estado_consulta_ejecucion(codigo),
  -- A2 — obligatorio en las completas, opcional en las exploratorias.
  presupuesto_usd  numeric(12, 4),
  costo_estimado   jsonb,                        -- lo que se mostró al confirmar
  -- En una exploratoria, el costo real se conoce al responder. En una
  -- completa se deriva de los lotes (`v_consulta_progreso`) y esto queda
  -- con el consolidado al cerrar.
  costo_real_usd   numeric(12, 6),
  diagnostico      jsonb,
  lotes_total      int not null default 0,
  unidades_total   int not null default 0,
  creado_por       text,
  creado_en        timestamptz not null default now(),
  terminado_en     timestamptz,
  constraint consulta_completa_con_presupuesto
    check (alcance <> 'completo' or presupuesto_usd > 0)
);

create index consulta_ejecucion_reciente on consulta_ejecucion (creado_en desc);
create index consulta_ejecucion_abiertas on consulta_ejecucion (creado_en desc)
 where estado in ('encolada', 'procesando');

comment on table consulta_ejecucion is
  'R-CS · Una fila por ejecución de consulta: definición, alcance, '
  'diagnóstico (degradaciones, proveedores efectivos) y costo. Nunca el '
  'resultado: las personas se recalculan al leer, con el gate de hoy.';
comment on column consulta_ejecucion.diagnostico is
  'A1.3 — el diagnóstico de la ejecución: `degradaciones`, `reranker`, '
  '`verificador`, `input_type` del criterio, etapas. Es lo que permite '
  'contestar «¿el reranker corrió en esa consulta?» después del hecho.';

-- ============================================================
--  3 · Los lotes de una ejecución completa
-- ============================================================
create table consulta_lote (
  id              bigint generated always as identity primary key,
  ejecucion_id    uuid not null references consulta_ejecucion(id) on delete cascade,
  indice          int not null,
  criterio_orden  int not null,
  -- El plan congelado: [[pregunta_id, hash_texto, distancia], …]. Ids y
  -- huellas, no texto. Las tareas no vuelven a elegir qué verificar.
  unidades        jsonb not null,
  unidades_total  int not null,
  estado          text not null default 'pendiente'
                    references estado_lote_consulta(codigo),
  intentos        int not null default 0,
  llamadas        int not null default 0,
  tokens_entrada  int not null default 0,
  tokens_salida   int not null default 0,
  tokens_rerank   int not null default 0,
  costo_usd       numeric(12, 6) not null default 0,
  resultado       jsonb,
  error           text,
  reintentable    boolean,
  actualizado_en  timestamptz not null default now(),
  unique (ejecucion_id, indice)
);

create index consulta_lote_por_estado on consulta_lote (ejecucion_id, estado);

comment on table consulta_lote is
  'R-CS · Un pedazo de una ejecución completa: qué unidades verifica, en qué '
  'estado está y cuánto costó. Reprocesarlo es seguro: los veredictos se '
  'escriben con upsert por (ejecución, criterio, unidad).';
comment on column consulta_lote.costo_usd is
  'A2 — lo que costó de verdad, con los tokens que informó cada proveedor. '
  'La suma sobre los lotes es el costo real de la ejecución y lo que se '
  'compara con el presupuesto antes de procesar el siguiente.';

-- ============================================================
--  4 · El progreso y el gasto, derivados y no acumulados
-- ============================================================
create view v_consulta_progreso as
select
  e.id                     as ejecucion_id,
  e.alcance, e.modo, e.tipo,
  e.estado,
  s.etiqueta               as estado_etiqueta,
  s.terminal,
  e.presupuesto_usd,
  e.lotes_total,
  e.unidades_total,
  count(l.id) filter (where l.estado = 'ok')         as lotes_ok,
  count(l.id) filter (where l.estado = 'fallido')    as lotes_fallidos,
  count(l.id) filter (where l.estado = 'omitido')    as lotes_omitidos,
  count(l.id) filter (where l.estado = 'procesando') as lotes_en_curso,
  count(l.id) filter (where l.estado = 'pendiente')  as lotes_pendientes,
  coalesce(sum(l.unidades_total) filter (where l.estado = 'ok'), 0)::bigint
                                                     as unidades_verificadas,
  coalesce(sum(l.costo_usd), 0)::numeric(12, 6)      as costo_lotes_usd,
  coalesce(sum(l.tokens_entrada), 0)::bigint         as tokens_entrada,
  coalesce(sum(l.tokens_salida), 0)::bigint          as tokens_salida,
  coalesce(sum(l.llamadas), 0)::bigint               as llamadas,
  e.creado_por, e.creado_en, e.terminado_en,
  extract(epoch from (coalesce(e.terminado_en, now()) - e.creado_en))::int
                                                     as segundos_transcurridos
  from consulta_ejecucion e
  join estado_consulta_ejecucion s on s.codigo = e.estado
  left join consulta_lote l on l.ejecucion_id = e.id
 group by e.id, s.etiqueta, s.terminal;

comment on view v_consulta_progreso is
  'R-CS · Avance y gasto de cada ejecución, contados sobre los lotes: con '
  'varias tareas en paralelo un contador a mano se desincroniza, y el '
  'presupuesto se controla contra esta suma.';

-- ============================================================
--  5 · Nadie más
-- ============================================================
-- Ni funciones nuevas ni grants: la superficie de COLOQUIO no cambia. Las
-- tablas se revocan explícitamente, como en la 0019, para que se lea acá.
revoke all on consulta_ejecucion, consulta_lote, estado_consulta_ejecucion,
              estado_lote_consulta from public;
revoke all on v_consulta_progreso from public;
