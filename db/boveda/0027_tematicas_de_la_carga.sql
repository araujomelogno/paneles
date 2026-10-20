-- ============================================================
--  STORE DE BÓVEDA — Migración 0027
--  Temáticas incorporadas de cada carga (R-TEM).
-- ============================================================
--
-- Una carga sabe de dónde vino (0024, 0026) pero no de qué trata. Las
-- temáticas son los temas que cubren las preguntas del archivo: «telefonía
-- móvil», «consumo de tabaco». Describen **la carga**, no a cada persona:
-- que una carga cubra un tema no dice que todos sus individuos hayan
-- respondido esas preguntas, y por eso no se copian en `persona` ni se
-- ofrecen como atributo.
--
-- Dos piezas:
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
--
-- No toca el store semántico: las temáticas se proponen leyendo las
-- preguntas de allá, pero se guardan acá, junto a la carga.
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

-- ---------- 1 · La temática, compartida entre cargas --------------------

create table if not exists tematica (
  id        bigint generated always as identity primary key,
  nombre    text        not null,
  creado_en timestamptz not null default now(),
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

commit;
