-- ============================================================
--  STORE DE BÓVEDA — Migración 0004 (Fase 2)
--
--  Tres cosas que la Fase 2 necesita guardar y que, por definición,
--  van del lado de la bóveda:
--
--  * `consulta_guardada` — definiciones de consulta reutilizables
--    (P1). Se guarda la DEFINICIÓN, nunca el resultado: un resultado
--    cacheado envejece mal (el padrón cambia, los consentimientos se
--    retiran) y sería una lista de personas persistida sin gate.
--  * `usuario_auditoria` — rastro de la gestión de usuarios de la app
--    (R2.12). Es escalada de privilegios por diseño: cada alta, cambio
--    de rol y desactivación queda con autor y fecha.
--  * `reidentificacion` — rastro de cada traducción deliberada de
--    `id_persona` → PII (P1). El puente entre stores existe para poder
--    hacerla; que quede registrado es lo que la vuelve auditable.
--
--  Las tres son de bóveda porque las dos primeras hablan de personal
--  de Equipos y la tercera nombra a un panelista. Ninguna cruza al
--  store semántico.
-- ============================================================

-- ---------- Consultas guardadas (P1) ------------------------
-- `definicion` es el cuerpo de POST /consultas tal como se manda:
-- criterios, modo, top_n, top_k y segmento demográfico. Guardar el
-- cuerpo y no columnas sueltas evita migrar la tabla cada vez que la
-- consulta gana un parámetro.
create table consulta_guardada (
  id            bigint generated always as identity primary key,
  nombre        text not null,
  descripcion   text,
  definicion    jsonb not null,
  panel_id      bigint references panel(id) on delete set null,
  creado_por    text not null,            -- uid de Firebase Auth
  creado_en     timestamptz not null default now(),
  actualizado_por text,
  actualizado_en  timestamptz,
  unique (nombre)
);
create index on consulta_guardada (panel_id);

-- ---------- Auditoría de la gestión de usuarios (R2.12) -----
-- `email_objetivo` es el correo corporativo de un empleado de Equipos,
-- no de un panelista: es el identificador con el que la app resuelve
-- la idempotencia del alta, y sin él la auditoría no se puede leer.
create table usuario_auditoria (
  id             bigint generated always as identity primary key,
  accion         text not null
                   check (accion in ('alta','cambio_rol','desactivacion',
                                     'reactivacion','actualizacion')),
  uid_objetivo   text not null,           -- uid de Firebase Auth afectado
  email_objetivo text,
  rol_anterior   text,
  rol_nuevo      text,
  actor_uid      text not null,           -- quién lo hizo
  actor_email    text,
  detalle        jsonb not null default '{}'::jsonb,
  creado_en      timestamptz not null default now()
);
create index on usuario_auditoria (uid_objetivo, creado_en desc);
create index on usuario_auditoria (creado_en desc);

-- ---------- Registro de reidentificación (P1) ---------------
-- Sin FK a `persona` a propósito: el registro tiene que sobrevivir a la
-- baja de la persona, que es justo el caso en que hace falta poder
-- demostrar quién había visto sus datos y cuándo.
create table reidentificacion (
  id          bigint generated always as identity primary key,
  id_persona  uuid not null,
  actor_uid   text not null,
  actor_email text,
  motivo      text not null,              -- 'ficha' | 'consulta' | 'convocatoria' | ...
  contexto    jsonb not null default '{}'::jsonb,
  creado_en   timestamptz not null default now()
);
create index on reidentificacion (id_persona, creado_en desc);
create index on reidentificacion (creado_en desc);

alter table consulta_guardada  enable row level security;
alter table usuario_auditoria  enable row level security;
alter table reidentificacion   enable row level security;
