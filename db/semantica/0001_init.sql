-- ============================================================
--  Modelo de datos para cuestionarios de investigación de mercado
--  Postgres + pgvector (Cloud SQL for Postgres)  --  STORE SEUDONIMIZADO
--
--  TODO es semántico: cada respuesta se embebe (pregunta + respuesta)
--  y la aproximación es por similitud. No hay match exacto ni capa
--  de conceptos. La unidad es el INDIVIDUO, que cruza estudios vía un
--  id_persona OPACO emitido/mantenido por la bóveda de identidad.
-- ============================================================

create extension if not exists vector;

create table cuestionario (
  id           bigint generated always as identity primary key,
  nombre       text not null,
  fecha_campo  date,
  ref_estudio  uuid unique,          -- = encuesta.ref_estudio del store de bóveda (cruce entre stores)
  metadata     jsonb not null default '{}'::jsonb,
  creado_en    timestamptz not null default now()
);

create table individuo (
  id          bigint generated always as identity primary key,
  id_persona  uuid not null unique,   -- token opaco de la bóveda (estable entre estudios)
  metadata    jsonb not null default '{}'::jsonb,
  creado_en   timestamptz not null default now()
);

-- pregunta: 'codigo' es el id_pregunta externo = ENCABEZADO del Excel de
-- respuestas. Permite unir cada columna con su pregunta en la ingesta.
create table pregunta (
  id              bigint generated always as identity primary key,
  cuestionario_id bigint not null references cuestionario(id) on delete cascade,
  codigo          text not null,   -- id_pregunta externo (header del Excel)
  texto           text not null,
  tipo            text check (tipo in ('cerrada','abierta','escala','numerica')),
  opciones        jsonb,           -- cerradas: {"1":"Fernet","2":"Whisky"}
  orden           int,
  unique (cuestionario_id, codigo)
);
create index on pregunta (cuestionario_id);

create table respuesta (
  id             bigint generated always as identity primary key,
  individuo_id   bigint not null references individuo(id) on delete cascade,
  pregunta_id    bigint not null references pregunta(id)  on delete cascade,
  valor_texto    text,
  texto_embebido text not null,       -- string exacto vectorizado (pregunta -> respuesta)
  embedding      vector(1024) not null,
  unique (individuo_id, pregunta_id)
);
create index on respuesta (individuo_id);
create index on respuesta (pregunta_id);
create index on respuesta using hnsw (embedding vector_cosine_ops);

alter table cuestionario enable row level security;
alter table individuo    enable row level security;
alter table pregunta     enable row level security;
alter table respuesta    enable row level security;
