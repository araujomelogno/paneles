-- ============================================================
--  STORE DE BÓVEDA — Migración 0028
--  Taxonomía de temáticas: área → temática (R-TEM.7 a R-TEM.11).
-- ============================================================
--
-- Con cada carga la lista de temáticas crece, y en poco tiempo el filtro se
-- vuelve una columna larga, sin orden y con casi-duplicados. Esta migración
-- agrega **un** nivel arriba —el área— y nada más: con tres o más niveles
-- nadie sabe en qué rama va cada cosa.
--
-- La taxonomía la propone y la hace crecer Claude, pero **se guarda**: solo
-- cambia con la aprobación del analista. Generarla en cada consulta haría
-- que el filtro se reorganizara solo y que una consulta guardada dejara de
-- coincidir. Dinámica en cómo crece, estable en cómo se ve.
--
-- Tres piezas:
--
--   1 · `area_tematica` — el área: nombre normalizado y único (la misma
--       regla que `tematica`), descripción corta, marca de especial y
--       origen. Todo se referencia **por id**: renombrar un área no rompe
--       filtros ni consultas guardadas.
--   2 · `tematica.area_id` — a qué área pertenece cada temática. Nulo hasta
--       el arranque de la taxonomía (R-TEM.8); después, toda temática nueva
--       llega con su área desde la revisión de la carga (R-TEM.9).
--   3 · `taxonomia_cambio` — el registro de lo que se aplicó (arranque,
--       mover, fusionar, renombrar, dividir): quién, cuándo y qué. Permite
--       entender por qué una temática está donde está; no permite
--       deshacerlo.
--
-- Y la vista `v_tematica`, que es **el único lugar** donde se decide si una
-- temática es especial: la propia marca **o** la de su área (R-TEM.11). La
-- herencia suma y no resta: una especial en un área que no lo es sigue
-- siendo especial.
--
-- Aditiva. No toca el store semántico. Va en una transacción y se puede
-- correr dos veces sin efecto.
begin;

-- ---------- 1 · El área ---------------------------------------------------

create table if not exists area_tematica (
  id          bigint generated always as identity primary key,
  nombre      text        not null,
  descripcion text,
  es_especial boolean     not null default false,
  origen      text        not null default 'manual'
                check (origen in ('propuesta_ia', 'manual')),
  creado_en   timestamptz not null default now(),
  constraint area_tematica_nombre_unico unique (nombre),
  -- La misma regla que `tematica_nombre_normalizado` (0027).
  constraint area_tematica_nombre_normalizado check (
    nombre = lower(regexp_replace(btrim(nombre), '\s+', ' ', 'g'))
    and char_length(nombre) between 2 and 80
  )
);

comment on table area_tematica is
  'R-TEM.7 — el nivel de arriba de la taxonomía: agrupa temáticas. Exactamente '
  'dos niveles (área → temática). La propone Claude y la confirma el analista; '
  'se guarda y no se regenera.';
comment on column area_tematica.es_especial is
  'R-TEM.11 — un área especial vuelve especiales a todas sus temáticas '
  '(v_tematica). La herencia suma: no le quita la marca a ninguna.';

alter table area_tematica enable row level security;

-- ---------- 2 · La temática, en su área -----------------------------------

alter table tematica add column if not exists area_id bigint
  references area_tematica (id) on delete restrict;

comment on column tematica.area_id is
  'R-TEM.7 — el área de la temática. Nulo solo hasta el arranque de la '
  'taxonomía; `restrict`: un área con temáticas no se borra, se fusiona.';

create index if not exists tematica_area_idx on tematica (area_id);

-- ---------- 3 · El registro de cambios ------------------------------------

create table if not exists taxonomia_cambio (
  id          bigint generated always as identity primary key,
  tipo        text        not null check (tipo in (
                'arranque', 'mover_tematica', 'fusionar_tematicas',
                'renombrar_area', 'fusionar_areas', 'dividir_area',
                'marcar_area_especial')),
  detalle     jsonb       not null,
  actor       text,
  actor_email text,
  creado_en   timestamptz not null default now()
);

comment on table taxonomia_cambio is
  'R-TEM.10 — cada operación aplicada sobre la taxonomía, con los objetos '
  'afectados (ids y nombres de ese momento) y quién la aprobó. Es para '
  'entender después por qué una temática está donde está.';

create index if not exists taxonomia_cambio_fecha_idx
  on taxonomia_cambio (creado_en desc);

alter table taxonomia_cambio enable row level security;

-- ---------- La marca especial efectiva ------------------------------------

create or replace view v_tematica as
select t.id, t.nombre, t.area_id, a.nombre as area,
       t.es_especial                                    as es_especial_propia,
       coalesce(a.es_especial, false)                    as area_es_especial,
       (t.es_especial or coalesce(a.es_especial, false)) as es_especial,
       t.creado_en
  from tematica t
  left join area_tematica a on a.id = t.area_id;

comment on view v_tematica is
  'R-TEM.11 — la temática con su área y la marca especial **efectiva**: la '
  'propia o la del área. Todo lo que decide si una temática se muestra, se '
  'exporta o se filtra lee esta vista, no `tematica.es_especial`.';

revoke all on v_tematica from public;

commit;
