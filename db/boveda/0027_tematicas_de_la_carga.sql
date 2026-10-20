-- ============================================================
--  STORE DE BÓVEDA — Migración 0027
--  Temáticas incorporadas de cada carga y de cada panelista (R-TEM).
-- ============================================================
--
-- Una carga sabe de dónde vino (0024, 0026) pero no de qué trata. Las
-- temáticas son los temas que cubren las preguntas del archivo: «telefonía
-- móvil», «consumo de tabaco».
--
-- Hay dos niveles y no hay que confundirlos: las temáticas de **la carga**
-- (qué cubrió el estudio) y las de **cada panelista** (sobre qué respondió).
-- Que una carga cubra un tema no dice que todos sus individuos lo tengan:
-- una persona tiene la temática Y solo si respondió al menos una de las
-- preguntas relacionadas con Y (R-TEM.6).
--
-- Cinco piezas:
--
--   1 · `tematica` — una entidad propia, compartida entre cargas. Si cada
--       carga guardara sus etiquetas como texto, en pocos meses habría
--       «telefonía móvil», «telefonía celular» y «celulares», y la pregunta
--       «¿qué cargas cubrieron telefonía?» dejaría de tener respuesta.
--       El nombre se guarda **normalizado** (minúsculas, espacios simples,
--       sin espacios en los bordes) y es único: «Telefonía móvil» y
--       «telefonía  móvil» son la misma fila. La normalización la hace
--       `tematicas.normalizar()` y la hace valer un `check`: un `insert`
--       que no pase por Python no puede crear la variante.
--   2 · `carga_tematica` — el vínculo, con su origen: `propuesta_ia` (la
--       propuso Claude y el analista la aceptó) o `manual`. Único por
--       `(carga_id, tematica_id)`. Borrar la carga se lleva sus vínculos;
--       la temática queda, porque puede estar usada por otras cargas.
--   3 · `tematica.es_especial` — salud, consumo de sustancias, ideología,
--       religión, vida sexual: como los atributos de categoría especial
--       (R3.14.e). Una especial no se ofrece como columna ni sale en
--       exportaciones, y consultarla queda restringido por rol.
--   4 · `tematica_pregunta` — qué preguntas (por código) sustentan cada
--       temática en cada carga, tal como se confirmó en la revisión.
--   5 · `persona_tematica` — qué temáticas tiene cada persona y de qué
--       carga, con cuántas de las preguntas relacionadas respondió. Se
--       calcula al ingestar con la fila del archivo en la mano y se recalcula
--       cuando cambian las temáticas de la carga. Se va con la persona (baja)
--       y con la carga.
--
-- **Privacidad.** `persona_tematica` trae a la bóveda, junto a la identidad,
-- un dato derivado del contenido de las respuestas. Es grueso —el tema, no
-- la respuesta: «respondió sobre salud», no qué respondió— y por eso
-- aceptable; para las especiales hay control de rol.
--
-- No toca el store semántico.
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

-- ---------- 1 · La temática, compartida entre cargas --------------------

create table if not exists tematica (
  id        bigint generated always as identity primary key,
  nombre    text        not null,
  creado_en timestamptz not null default now(),
  -- R-TEM.6 — categoría especial (se agrega abajo con `add column if not
  -- exists`, para que correr esto sobre una 0027 parcial también la cree).
  constraint tematica_nombre_unico unique (nombre),
  -- La misma regla que `tematicas.normalizar()`: minúsculas, un espacio
  -- entre palabras y ninguno en los bordes. Si alguien inserta a mano
  -- «Telefonía Móvil», la base lo rechaza en vez de crear la variante.
  constraint tematica_nombre_normalizado check (
    nombre = lower(regexp_replace(btrim(nombre), '\s+', ' ', 'g'))
    and char_length(nombre) between 2 and 80
  )
);

comment on table tematica is
  'R-TEM.1 — un tema de investigación que cubren las preguntas de una o más '
  'cargas. Compartida: es lo que permite reutilizarla y, más adelante, '
  'filtrar por ella. Describe cargas, no personas.';
comment on column tematica.nombre is
  'Normalizado (minúsculas, espacios simples) y único. La normalización la '
  'hace valer el check tematica_nombre_normalizado.';

alter table tematica add column if not exists
  es_especial boolean not null default false;

comment on column tematica.es_especial is
  'R-TEM.6 — categoría especial (salud, sustancias, ideología, religión, vida '
  'sexual). No se ofrece como columna de resultados ni sale en exportaciones, '
  'y filtrar o ver quién la tiene queda restringido por rol.';

alter table tematica enable row level security;

-- ---------- 2 · El vínculo carga ↔ temática ----------------------------

create table if not exists carga_tematica (
  carga_id    bigint      not null references carga(id) on delete cascade,
  -- `restrict`: una temática usada no se borra por accidente. Fusionar dos
  -- (riesgo abierto del SPEC) es mover los vínculos y después borrar.
  tematica_id bigint      not null references tematica(id) on delete restrict,
  origen      text        not null check (origen in ('propuesta_ia', 'manual')),
  creado_en   timestamptz not null default now(),
  primary key (carga_id, tematica_id)
);

comment on table carga_tematica is
  'R-TEM.1 — las temáticas incorporadas de cada carga. Se van con la carga '
  '(on delete cascade); la temática queda.';
comment on column carga_tematica.origen is
  'propuesta_ia: la propuso Claude y el analista la aceptó · manual: la '
  'agregó el analista. Se fija al vincular: volver a guardar la lista no '
  'convierte una propuesta aceptada en manual.';

-- El filtro del listado de cargas va por temática.
create index if not exists carga_tematica_tematica_idx
  on carga_tematica (tematica_id);

alter table carga_tematica enable row level security;

-- ---------- 4 · Qué preguntas sustentan cada temática en cada carga -----

create table if not exists tematica_pregunta (
  carga_id        bigint not null,
  tematica_id     bigint not null,
  codigo_pregunta text   not null check (btrim(codigo_pregunta) <> ''),
  primary key (carga_id, tematica_id, codigo_pregunta),
  -- Contra el vínculo y no contra la carga suelta: quitar la temática de la
  -- carga se lleva sus preguntas, y borrar la carga, todo.
  foreign key (carga_id, tematica_id)
    references carga_tematica (carga_id, tematica_id) on delete cascade
);

comment on table tematica_pregunta is
  'R-TEM.6 — las preguntas (por código, las del cuestionario de la carga) que '
  'sustentan cada temática en cada carga. Es lo que decide qué panelistas '
  'tienen la temática: quien respondió al menos una.';

alter table tematica_pregunta enable row level security;

-- ---------- 5 · Las temáticas de cada persona --------------------------

create table if not exists persona_tematica (
  id_persona            uuid        not null
                          references persona (id_persona) on delete cascade,
  tematica_id           bigint      not null,
  carga_id              bigint      not null,
  preguntas_respondidas int         not null check (preguntas_respondidas > 0),
  creado_en             timestamptz not null default now(),
  primary key (id_persona, tematica_id, carga_id),
  foreign key (carga_id, tematica_id)
    references carga_tematica (carga_id, tematica_id) on delete cascade
);

comment on table persona_tematica is
  'R-TEM.6 — la persona respondió al menos una pregunta relacionada con la '
  'temática en esa carga (preguntas_respondidas cuenta cuántas). Una '
  'respuesta vacía o de no respuesta no cuenta. Se va con la persona (baja), '
  'con la carga y con la temática de la carga.';

-- El filtro de la consulta demográfica y la composición van por temática;
-- la ficha, por persona (la clave primaria ya la cubre).
create index if not exists persona_tematica_tematica_idx
  on persona_tematica (tematica_id);
create index if not exists persona_tematica_carga_idx
  on persona_tematica (carga_id);

alter table persona_tematica enable row level security;

commit;
