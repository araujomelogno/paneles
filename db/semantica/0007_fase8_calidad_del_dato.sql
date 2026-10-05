-- ============================================================
--  0007 · Fase 8 — calidad del dato semántico
-- ============================================================
--
-- El motor de búsqueda es tan bueno como el texto que se embebe, y hasta acá
-- ese texto salía casi tal cual del archivo: «Cigarrillos:Pensando en el
-- ÚLTIMO mes… → Checked». La Fase 8 hace que el sistema detecte esos
-- patrones y **proponga** la corrección, que el analista confirma (ver
-- specs/SPEC_fase8.md y `panel_api/calidad_dato.py`).
--
-- Lo que esta migración guarda es lo que hace falta para que esas
-- decisiones sean reversibles y auditables, y para que un estudio mal
-- cargado se pueda **reprocesar sin volver a subir el archivo** (R8.9):
--
--   1 · El texto **original** de cada pregunta junto al editado. Una
--       reescritura que cambia el sentido de la pregunta degrada la búsqueda
--       en silencio; conservar el original es lo que permite volver, y ver
--       qué se cambió.
--   2 · Las decisiones de normalización de cada pregunta (batería, valores
--       de no respuesta, fusión con su cerrada) y las de la carga. Un
--       reproceso tiene que saber qué se había decidido.
--   3 · El registro de cada reproceso: qué se cambió y cuándo.
--
-- Nada de esto toca `respuesta`: `hash_texto` (0003) ya alcanza para saber
-- qué respuestas cambiaron de texto. Y nada toca la bóveda: el reproceso
-- corre por la vía diferida que ya existe (`boveda/0019`), y quién lo pidió
-- queda en `ingesta_trabajo.creado_por`, del lado de la identidad.
--
-- Los nombres de columna pasan por el event trigger de la 0005 como
-- cualquier otro: ninguno es de PII.

-- Va entera en una transacción y se puede correr dos veces sin efecto: las
-- columnas y la tabla llevan `if not exists`, y el relleno del original solo
-- toca las filas que no lo tienen.
begin;

-- ============================================================
--  1 · La pregunta: original y normalización
-- ============================================================
alter table pregunta
  add column if not exists texto_original      text,
  add column if not exists opciones_originales jsonb,
  add column if not exists normalizacion       jsonb not null default '{}'::jsonb;

comment on column pregunta.texto_original is
  'Fase 8 — el texto de la pregunta tal como vino en el archivo (el '
  'variable label de SPSS). `texto` es el que se embebe, editado o no.';
comment on column pregunta.opciones_originales is
  'Fase 8 — las etiquetas de respuesta tal como vinieron en el archivo, '
  'antes de normalizar (`Checked` → `Sí`).';
comment on column pregunta.normalizacion is
  'Fase 8 — las decisiones con las que se embebió: `solo_marcadas`, '
  '`valores_marcados`, `excluir_valores`, `prefijo_respuesta`, '
  '`fusionada_con`, `bateria`, `pii_aceptada`, `excluida`.';

-- Lo que ya estaba cargado no tiene otro original que el que tiene: se toma
-- el texto y las opciones actuales. Así el primer reproceso de un estudio
-- viejo también puede volver atrás.
update pregunta
   set texto_original = texto,
       opciones_originales = opciones
 where texto_original is null;

-- ============================================================
--  2 · La configuración de la carga
-- ============================================================
alter table cuestionario
  add column if not exists normalizacion jsonb not null default '{}'::jsonb;

comment on column cuestionario.normalizacion is
  'Fase 8 — la configuración de la carga que no es de ninguna pregunta: '
  'hoy, la lista de valores considerados no respuesta.';

-- ============================================================
--  3 · El registro de cada reproceso
-- ============================================================
-- Una fila por reproceso pedido. `cambios` es el detalle por pregunta —antes
-- y después de cada campo tocado—, que es lo que alguien va a querer leer
-- cuando una búsqueda empiece a devolver otra cosa.
--
-- `trabajo_id` es el `ingesta_trabajo` de la bóveda que lo procesa: la misma
-- clase de referencia lógica que `ref_estudio`, sin FK entre stores. Es nulo
-- cuando no hubo respuestas que re-embeber (un cambio de texto en una
-- pregunta sin respuestas, por ejemplo): el cambio queda registrado igual.
--
-- Quién lo pidió **no** está acá: es un dato de un usuario interno y vive del
-- lado de la identidad (`ingesta_trabajo.creado_por`).
create table if not exists reproceso (
  id                    bigint generated always as identity primary key,
  cuestionario_id       bigint not null references cuestionario(id) on delete cascade,
  creado_en             timestamptz not null default now(),
  cambios               jsonb not null,
  respuestas_a_procesar int not null default 0,
  trabajo_id            bigint
);

create index if not exists reproceso_por_cuestionario on reproceso (cuestionario_id, creado_en desc);

comment on table reproceso is
  'Fase 8 · R8.9 — qué se cambió de un estudio ya ingestado, y cuándo. El '
  'reproceso re-embebe solo las respuestas cuyo texto cambió (por '
  '`hash_texto`) y borra las de lo que se excluyó.';

alter table reproceso enable row level security;

commit;
