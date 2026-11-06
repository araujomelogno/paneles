-- 0035 · Convocatoria desde la encuesta: estado, panelistas a mano y
-- mensajes (D85, `specs/SPEC_convocatoria_y_mensajes.md`).
--
-- El sistema registraba el campo —a quién se convocó, quién respondió— pero
-- no lo operaba: las invitaciones y los recordatorios salían por fuera. Esto
-- agrega lo que hace falta para lanzarlo y seguirlo desde la encuesta:
--
--   encuesta.url_alchemer    — a dónde lleva el botón, con el parámetro
--   encuesta_estado_cambio   — quién cambió el estado, cuándo, de qué a qué
--   encuesta_mensaje         — los mensajes de la encuesta (WhatsApp o correo)
--   envio                    — un registro por persona y por mensaje enviado
--
-- y suma `mensaje` a los destinos de la vía diferida: un envío corre en lotes
-- como una ingesta, y el índice de la 0029 —un trabajo activo por destino—
-- es lo que impide lanzar dos veces el mismo mensaje a la vez.
--
-- Aditiva y re-ejecutable. Nada de esto toca la superficie de COLOQUIO: las
-- tablas nuevas no tienen `grant`.

begin;

-- ---------- 1 · R-CF.3 · La URL de Alchemer ----------------------------

alter table encuesta add column if not exists url_alchemer text;

comment on column encuesta.url_alchemer is
  'R-CF.3 — la URL de la encuesta en Alchemer, terminada en el parámetro '
  'que recibe el panelista (…?pid=). Se le agrega el id_persona.';

-- ---------- 2 · R-CF.1 · El registro de los cambios de estado -----------

create table if not exists encuesta_estado_cambio (
  id              bigint generated always as identity primary key,
  encuesta_id     bigint not null references encuesta(id) on delete cascade,
  estado_anterior text,
  estado_nuevo    text not null,
  actor_uid       text,
  actor_email     text,
  creado_en       timestamptz not null default now()
);

create index if not exists encuesta_estado_cambio_por_encuesta
    on encuesta_estado_cambio (encuesta_id, creado_en desc);

comment on table encuesta_estado_cambio is
  'R-CF.1 — cada cambio de estado de una encuesta: quién, cuándo, de qué a '
  'qué. Lo escribe encuestas.cambiar_estado, que es el único camino.';

-- ---------- 3 · R-CF.4 · Los mensajes de la encuesta --------------------
-- Cada uno tiene un nombre libre y un canal. WhatsApp: una plantilla
-- aprobada por Meta (nombre e idioma), qué va en cada parámetro del texto, y
-- la base de su botón de URL tal como Meta la aprobó. Correo: asunto y
-- cuerpo, escritos acá.

create table if not exists encuesta_mensaje (
  id               bigint generated always as identity primary key,
  encuesta_id      bigint not null references encuesta(id) on delete cascade,
  nombre           text not null,
  canal            text not null default 'whatsapp',
  plantilla        text,
  plantilla_idioma text,
  -- Qué va en {{1}}, {{2}}… del cuerpo de la plantilla, en orden:
  -- 'nombre_panelista' | 'nombre_encuesta'.
  parametros       jsonb not null default '[]'::jsonb,
  -- La base del botón de URL de la plantilla, como la aprobó Meta. El
  -- id_persona va como sufijo.
  url_boton        text,
  asunto           text,
  cuerpo           text,
  creado_por       text,
  creado_en        timestamptz not null default now(),
  actualizado_en   timestamptz not null default now()
);

alter table encuesta_mensaje drop constraint if exists encuesta_mensaje_canal_check;
alter table encuesta_mensaje add constraint encuesta_mensaje_canal_check
  check (canal in ('whatsapp', 'correo'));
alter table encuesta_mensaje drop constraint if exists encuesta_mensaje_segun_canal;
alter table encuesta_mensaje add constraint encuesta_mensaje_segun_canal
  check ((canal = 'whatsapp' and plantilla is not null)
      or (canal = 'correo' and asunto is not null and cuerpo is not null));

create unique index if not exists encuesta_mensaje_nombre_unico
    on encuesta_mensaje (encuesta_id, lower(nombre));

comment on table encuesta_mensaje is
  'R-CF.4 — los mensajes de una encuesta: invitación, recordatorios, '
  'agradecimiento. Canal WhatsApp (plantilla aprobada) o correo (asunto y '
  'cuerpo).';

-- ---------- 4 · R-CF.6 / R-CF.7 · El registro de envíos -----------------
-- Una fila por persona y por envío. «No enviado aún» se calcula acá: un
-- envío fallido cuenta como no enviado, así que el reintento lo incluye.
-- Sin PII: la persona va por id, y la baja se lleva sus filas (cascade).

create table if not exists envio (
  id                  bigint generated always as identity primary key,
  encuesta_mensaje_id bigint not null references encuesta_mensaje(id) on delete cascade,
  id_persona          uuid not null references persona(id_persona) on delete cascade,
  canal               text not null,
  trabajo_id          bigint,
  lanzado_por         text,
  enviado_en          timestamptz not null default now(),
  estado              text not null,
  id_mensaje_meta     text,
  error               text,
  actualizado_en      timestamptz not null default now()
);

alter table envio drop constraint if exists envio_estado_check;
alter table envio add constraint envio_estado_check
  check (estado in ('aceptado', 'fallido', 'entregado', 'leido'));

create index if not exists envio_por_mensaje_y_persona
    on envio (encuesta_mensaje_id, id_persona);
create index if not exists envio_por_persona on envio (id_persona);
-- El webhook de Meta busca por este id.
create unique index if not exists envio_id_mensaje_meta_unico
    on envio (id_mensaje_meta) where id_mensaje_meta is not null;

comment on table envio is
  'R-CF.6 — un registro por persona y por mensaje enviado: cuándo, quién lo '
  'lanzó y qué dijo el proveedor (aceptado con el id de Meta, o fallido con '
  'el motivo). R-CF.7: el webhook de Meta lo lleva a entregado o leído.';

-- ---------- 5 · La vía diferida admite envíos ---------------------------

alter table ingesta_trabajo drop constraint if exists ingesta_trabajo_destino_check;
alter table ingesta_trabajo add constraint ingesta_trabajo_destino_check
  check (destino_tipo in ('encuesta', 'carga', 'mensaje'));

-- ---------- 6 · Seguridad ------------------------------------------------

alter table encuesta_estado_cambio enable row level security;
alter table encuesta_mensaje       enable row level security;
alter table envio                  enable row level security;

revoke all on encuesta_estado_cambio, encuesta_mensaje, envio from public;

commit;
