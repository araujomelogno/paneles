-- ============================================================
--  STORE SEMÁNTICO — Migración 0008
--  R-VER.10 — Modo de depuración de la verificación: el intercambio con
--  Claude, solicitud y respuesta, por llamada.
-- ============================================================
--
-- Cuando la verificación da un resultado raro no había forma de ver qué se le
-- mandó al modelo ni qué contestó. Esta tabla guarda, **solo con el modo de
-- depuración encendido** (`VERIFICACION_DEPURACION`), el cuerpo exacto de cada
-- llamada a la API —lotes, subdivisiones y reintentos— y la respuesta tal cual
-- llegó, aunque esté truncada o no sea JSON.
--
-- Por qué acá y no en los logs ni en un bucket: el contenido son **las mismas
-- respuestas de encuesta** que ya viven en este store, con la misma ausencia
-- de identificadores (el payload es `[n] pregunta → respuesta`, sin
-- `id_persona`). Guardarlas en otro lado sería una tercera copia con reglas
-- que nadie definió; acá heredan clasificación, protecciones y el alcance de
-- una baja.
--
-- Esa última herencia no es automática, porque el payload no lleva a quién
-- pertenece cada evidencia. Por eso se guarda `respuesta_ids` **al costado**
-- del payload —no adentro: la captura refleja lo que se mandó, no lo amplía—
-- y `semantica.borrar_persona` borra las capturas que tocan respuestas de
-- quien se da de baja.
--
-- Reglas que esta tabla no puede hacer valer sola y hace valer el código:
--   · apagado por defecto (`verificacion.depuracion_activa`);
--   · vence a los 7 días (`vence_en`) y se purga en cada escritura y lectura;
--   · tope de tamaño por entrada y por ejecución, con constancia de que se
--     truncó (`*_truncada`, `omitida_por_tope`);
--   · la lee solo el permiso `depurar_verificacion` (admin).
--
-- Los nombres de columna pasan por el event trigger de la 0005 como cualquier
-- otro: ninguno es de PII.
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

create table if not exists verificacion_captura (
  id                   bigint generated always as identity primary key,
  ejecucion_id         uuid        not null,
  criterio             text        not null,
  lote                 text        not null,
  lote_padre           text,
  profundidad          int         not null default 0,
  intento              int         not null default 1,
  primera_evidencia    int         not null,
  evidencias           int         not null,
  respuesta_ids        bigint[]    not null default '{}',
  solicitud            text,
  solicitud_bytes      int         not null default 0,
  solicitud_truncada   boolean     not null default false,
  estado_http          int,
  respuesta_api        text,
  respuesta_bytes      int         not null default 0,
  respuesta_truncada   boolean     not null default false,
  respuesta_es_json    boolean     not null default false,
  omitida_por_tope     boolean     not null default false,
  resultado            text        not null,
  duracion_ms          int,
  creado_en            timestamptz not null default now(),
  vence_en             timestamptz not null default now() + interval '7 days'
);

comment on table verificacion_captura is
  'R-VER.10 — modo de depuración: solicitud y respuesta de cada llamada a la '
  'API de Claude durante la verificación. Apagado por defecto, vence a los 7 '
  'días, sin API key ni headers. Ver panel_api/verificacion.py.';
comment on column verificacion_captura.ejecucion_id is
  'El identificador de la consulta (diagnostico.ejecucion_id): agrupa todas '
  'las llamadas de una misma ejecución.';
comment on column verificacion_captura.lote is
  'Lote dentro del criterio: L1, L2… y L1.1, L1.2 para las mitades de una '
  'subdivisión. `lote_padre` es el lote del que salió.';
comment on column verificacion_captura.primera_evidencia is
  'Índice global de la primera evidencia del lote. Los `n` del payload son '
  'locales al lote: global = primera_evidencia + n.';
comment on column verificacion_captura.respuesta_ids is
  'Las respuestas que viajaron en el lote, en orden. NO van en el payload: '
  'están acá para que una baja pueda borrar la captura.';
comment on column verificacion_captura.solicitud is
  'El cuerpo JSON exacto enviado a la API. Sin headers y sin API key.';
comment on column verificacion_captura.respuesta_api is
  'El cuerpo de la respuesta tal como llegó: JSON o texto, truncado o no.';
comment on column verificacion_captura.resultado is
  'ok · truncamiento · herramienta_ausente · respuesta_invalida · error_http · error_red';

create index if not exists verificacion_captura_ejecucion_idx
  on verificacion_captura (ejecucion_id, id);
create index if not exists verificacion_captura_vence_idx
  on verificacion_captura (vence_en);
create index if not exists verificacion_captura_respuestas_idx
  on verificacion_captura using gin (respuesta_ids);

commit;
