-- ============================================================
--  STORE LOCAL  --  Bóveda de identidad + Módulo de paneles
--  Cloud SQL for Postgres (instancia separada de la remota).
--  Contiene PII y toda la gestión de panel. La PII nunca sale de acá.
--  (Supersede a boveda_identidad.sql: incluye la bóveda ampliada.)
-- ============================================================

create extension if not exists pgcrypto;   -- gen_random_uuid()

-- ---------- Bóveda de identidad (PII) -----------------------
-- id_persona es la clave de la persona en TODA la plataforma.
-- Token opaco y aleatorio; el mismo entre paneles y estudios.
create table persona (
  id_persona       uuid primary key default gen_random_uuid(),
  documento        text,
  nombre           text,
  sexo             text,          -- demográfico
  fecha_nacimiento date,          -- demográfico (identificador fino: solo local)
  localidad        text,          -- demográfico
  email            text,
  celular          text,
  contacto         text,          -- otros datos de contacto
  observaciones    text,          -- texto libre: puede contener dato sensible; tratar con cuidado
  creado_en        timestamptz not null default now()
);
create unique index on persona (documento)    where documento is not null;
create unique index on persona (lower(email))  where email is not null;

-- Cómo nombró cada plataforma/estudio a la persona (dedup en ingesta).
create table alias_origen (
  id           bigint generated always as identity primary key,
  id_persona   uuid not null references persona(id_persona) on delete cascade,
  origen       text not null,        -- 'dooblo', 'alchemer', ...
  id_en_origen text not null,
  unique (origen, id_en_origen)
);

-- Vista demográfica con tramo etario derivado (composición / muestreo).
create view v_demografia as
select
  id_persona, sexo, localidad,
  date_part('year', age(fecha_nacimiento))::int as edad,
  case
    when fecha_nacimiento is null then null
    when date_part('year', age(fecha_nacimiento)) < 18 then '<18'
    when date_part('year', age(fecha_nacimiento)) < 25 then '18-24'
    when date_part('year', age(fecha_nacimiento)) < 35 then '25-34'
    when date_part('year', age(fecha_nacimiento)) < 45 then '35-44'
    when date_part('year', age(fecha_nacimiento)) < 55 then '45-54'
    when date_part('year', age(fecha_nacimiento)) < 65 then '55-64'
    else '65+'
  end as tramo_etario
from persona;

-- ---------- Paneles y membresías (N:M) ----------------------
create table panel (
  id          bigint generated always as identity primary key,
  nombre      text not null,
  descripcion text,
  estado      text not null default 'activo' check (estado in ('activo','archivado')),
  creado_en   timestamptz not null default now()
);

create table membresia (
  id          bigint generated always as identity primary key,
  panel_id    bigint not null references panel(id) on delete cascade,
  id_persona  uuid   not null references persona(id_persona) on delete cascade,
  estado      text not null default 'activo' check (estado in ('activo','baja')),
  fecha_alta  timestamptz not null default now(),
  fecha_baja  timestamptz,
  unique (panel_id, id_persona)
);
create index on membresia (id_persona);

-- ---------- Consentimiento (máquina de estados) -------------
-- Una fila por (persona, finalidad[, panel]). 'uso_semantico' es una
-- finalidad DISTINTA de 'contacto_participacion' (limitación de finalidad).
create table consentimiento (
  id            bigint generated always as identity primary key,
  id_persona    uuid not null references persona(id_persona) on delete cascade,
  finalidad     text not null check (finalidad in ('contacto_participacion','uso_semantico')),
  panel_id      bigint references panel(id) on delete cascade,   -- null = a nivel persona
  estado        text not null default 'vigente' check (estado in ('vigente','retirado')),
  version_texto text not null,          -- versión del texto consentido
  otorgado_en   timestamptz not null default now(),
  retirado_en   timestamptz
);
create index on consentimiento (id_persona, finalidad);

-- ---------- Encuestas / olas y participación ----------------
-- ref_estudio: uuid compartido con el store remoto (cuestionario.ref_estudio)
-- para cruzar las respuestas semánticas de esta encuesta.
create table encuesta (
  id           bigint generated always as identity primary key,
  panel_id     bigint not null references panel(id) on delete cascade,
  nombre       text not null,
  fecha_campo  date,
  estado       text not null default 'borrador' check (estado in ('borrador','en_campo','cerrada')),
  ref_estudio  uuid not null default gen_random_uuid(),
  creado_en    timestamptz not null default now()
);
create index on encuesta (panel_id);

-- Una fila por (encuesta, persona) convocada.
-- El nº de convocatorias de una persona = count(*) de sus participaciones.
create table participacion (
  id             bigint generated always as identity primary key,
  encuesta_id    bigint not null references encuesta(id) on delete cascade,
  id_persona     uuid   not null references persona(id_persona) on delete cascade,
  convocado_en   timestamptz not null default now(),
  respondio      boolean not null default false,
  respondio_en   timestamptz,
  calidad_estado text not null default 'pendiente' check (calidad_estado in ('pendiente','ok','sospechoso')),
  motivo_calidad text,          -- 'speeder' | 'straightliner' | 'duplicado' | ...
  unique (encuesta_id, id_persona)
);
create index on participacion (id_persona);

-- ---------- Composición: objetivo / universo de referencia --
create table objetivo_composicion (
  id                  bigint generated always as identity primary key,
  panel_id            bigint not null references panel(id) on delete cascade,
  dimension           text not null,     -- 'sexo' | 'tramo_etario' | 'localidad'
  categoria           text not null,     -- 'F' | '18-24' | 'Montevideo' ...
  proporcion_objetivo numeric not null check (proporcion_objetivo between 0 and 1),
  unique (panel_id, dimension, categoria)
);

-- ---------- Gamificación (Fase 3; DDL incluido por consistencia)
-- Saldo de puntos = suma de 'puntos'. Saldo >= 0, sin sobregiro en canje,
-- y vencimiento: se garantizan en la capa de aplicación (ver HANDOFF).
create table puntos_movimiento (
  id          bigint generated always as identity primary key,
  id_persona  uuid not null references persona(id_persona) on delete cascade,
  tipo        text not null check (tipo in ('earn','canje','ajuste','vencimiento')),
  puntos      int  not null,             -- + earn/ajuste ; - canje/vencimiento
  motivo      text,
  encuesta_id bigint references encuesta(id) on delete set null,
  vence_en    timestamptz,
  creado_en   timestamptz not null default now()
);
create index on puntos_movimiento (id_persona);

create table catalogo_premio (
  id           bigint generated always as identity primary key,
  nombre       text not null,
  descripcion  text,
  costo_puntos int not null check (costo_puntos > 0),
  stock        int,                       -- null = ilimitado
  activo       boolean not null default true
);

create table canje (
  id           bigint generated always as identity primary key,
  id_persona   uuid not null references persona(id_persona) on delete cascade,
  premio_id    bigint not null references catalogo_premio(id),
  costo_puntos int not null,
  estado       text not null default 'solicitado' check (estado in ('solicitado','entregado','cancelado')),
  creado_en    timestamptz not null default now()
);
create index on canje (id_persona);

-- ---------- RLS (baseline) ----------------------------------
-- Store local: acceso vía el servicio de la app con un rol dedicado.
-- Definir políticas según el modelo de auth.
alter table persona              enable row level security;
alter table alias_origen         enable row level security;
alter table panel                enable row level security;
alter table membresia            enable row level security;
alter table consentimiento       enable row level security;
alter table encuesta             enable row level security;
alter table participacion        enable row level security;
alter table objetivo_composicion enable row level security;
alter table puntos_movimiento    enable row level security;
alter table catalogo_premio      enable row level security;
alter table canje                enable row level security;
