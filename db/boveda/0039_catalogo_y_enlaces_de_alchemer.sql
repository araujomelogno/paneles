-- 0039 · Búsqueda y enlaces de encuestas de Alchemer (D99,
-- `specs/PEDIDO_busqueda_alchemer.md`).
--
-- La búsqueda de D97 bajaba la lista entera de encuestas de la API en cada
-- pedido (de a 100, hasta 10 páginas, ~8 s cada una) y recién después
-- filtraba: con 2.172 encuestas se pasaba de los 60 s de Hosting y, aunque
-- terminara, no veía las de las últimas páginas. Ahora:
--
--   1. **Un catálogo local** (`alchemer_encuesta`): id, título, estado y
--      fechas de cada encuesta de la cuenta. Ni cuestionario ni respuestas.
--      La búsqueda se resuelve acá, sin llamar a Alchemer.
--   2. **La sincronización** que lo llena corre en segundo plano, **una
--      página de la API por tarea** de Cloud Tasks
--      (`alchemer_sincronizacion` y sus páginas). El avance se deriva de las
--      páginas, no de un contador. Una sola en curso, y lo garantiza la base.
--   3. **El enlace elegido**: `encuesta.alchemer_enlace_id`, la campaña de
--      Alchemer cuya URL quedó en `url_alchemer`. Se puede cambiar después.
--
-- No cambia datos existentes. Re-ejecutable.

begin;

alter table encuesta add column if not exists alchemer_enlace_id text;
comment on column encuesta.alchemer_enlace_id is
  'R-ALB.5 (D99) — la campaña (enlace) de Alchemer cuya URL quedó en '
  '`url_alchemer`. Se cambia desde la ficha de la encuesta.';

-- ---------- 1 · El catálogo ---------------------------------------------

create table if not exists alchemer_encuesta (
  id               text        primary key,
  titulo           text        not null default '',
  -- El título sin tildes ni mayúsculas, como lo normaliza
  -- `alchemer._clave`: la búsqueda compara contra esto.
  titulo_busqueda  text        not null default '',
  estado           text,
  creada           timestamp,
  -- Como la informa Alchemer, sin zona horaria (no la declara).
  modificada       timestamp,
  -- La sincronización que la vio por última vez: una completa borra al
  -- terminar las que no vio (se borraron en Alchemer).
  vista_en         bigint,
  actualizado_en   timestamptz not null default now()
);

create index if not exists alchemer_encuesta_recientes
    on alchemer_encuesta (modificada desc nulls last);

comment on table alchemer_encuesta is
  'R-ALB.2 (D99) — el catálogo de encuestas de la cuenta de Alchemer, para '
  'buscar por título sin recorrer la API. Sin cuestionario ni respuestas.';

-- ---------- 2 · La sincronización -----------------------------------------

create table if not exists alchemer_sincronizacion (
  id             bigint generated always as identity primary key,
  modo           text        not null check (modo in ('completa', 'incremental')),
  -- Incremental: desde qué fecha de modificación se pidió. Si Alchemer
  -- ignora o rechaza el filtro, la corrida pasa a completa y lo dice.
  desde          timestamp,
  filtro         text        check (filtro in ('aplicado', 'ignorado', 'rechazado')),
  estado         text        not null default 'en_curso'
                   check (estado in ('en_curso', 'terminada', 'fallida')),
  paginas_total  int,
  iniciada_en    timestamptz not null default now(),
  iniciada_por   text,
  -- La última vez que se reanudó (páginas que nadie tomó, vueltas a encolar).
  reanudada_en   timestamptz,
  terminada_en   timestamptz,
  eliminadas     int,
  error          text
);

-- Una sola en curso: la guarda es la base, no un `select` previo.
create unique index if not exists alchemer_sincronizacion_una_en_curso
    on alchemer_sincronizacion ((true)) where estado = 'en_curso';

create table if not exists alchemer_sincronizacion_pagina (
  sincronizacion_id bigint      not null
                      references alchemer_sincronizacion(id) on delete cascade,
  pagina            int         not null,
  estado            text        not null default 'pendiente'
                      check (estado in ('pendiente', 'procesada', 'fallida')),
  encuestas         int,
  error             text,
  procesada_en      timestamptz,
  primary key (sincronizacion_id, pagina)
);

comment on table alchemer_sincronizacion is
  'R-ALB.2 (D99) — cada sincronización del catálogo. Una página de la API por '
  'tarea; el avance se deriva de `alchemer_sincronizacion_pagina`.';

-- ---------- 3 · Seguridad ---------------------------------------------------

alter table alchemer_encuesta              enable row level security;
alter table alchemer_sincronizacion        enable row level security;
alter table alchemer_sincronizacion_pagina enable row level security;

revoke all on alchemer_encuesta, alchemer_sincronizacion,
              alchemer_sincronizacion_pagina from public;

commit;
