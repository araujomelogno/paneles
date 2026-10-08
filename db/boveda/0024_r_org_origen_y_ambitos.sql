-- ============================================================
--  STORE DE BÓVEDA — Migración 0024
--  R-ORG — De qué carga vino cada persona, los datos del estudio en la
--  carga y la composición por ámbito (todos · panel · carga).
-- ============================================================
--
-- Hasta acá `carga` tenía nombre, descripción y `ref_estudio`, pero **nada
-- relacionaba a una persona con la carga de la que salió**. Una persona
-- creada en una carga no dejaba constancia de cuál fue, y eso dejaba dos
-- preguntas sin respuesta: la composición de una carga y el estudio del que
-- proviene un panelista. El cruce por `ref_estudio` no alcanza: lleva de una
-- carga a sus respuestas en el store semántico, no a sus **personas** —del
-- lado semántico solo hay `id_persona`, sin saber si nació en esa carga o ya
-- existía—, y la composición es una pregunta de la bóveda.
--
-- Cuatro piezas:
--
--   1 · `persona_carga` — el vínculo, de muchos a muchos (R-ORG.1). Un campo
--       en `persona` obligaría a elegir entre pisar el origen anterior o
--       ignorar las cargas siguientes. `origen` distingue a quien la carga
--       **creó** de quien ya existía y el dedup **reutilizó**.
--   2 · `carga.fecha_estudio` y `carga.publico_objetivo` (R-ORG.4). Viven en
--       la carga y no copiados en cada persona: corregir la fecha del estudio
--       es una fila, no 1.131.
--   3 · `objetivo_composicion.ambito` (R-ORG.3). El universo de referencia
--       se puede cargar también para «todos los panelistas». Para una carga
--       no: una carga es un hecho del pasado y no se corrige reclutando (D67).
--   4 · `v_carga_resumen` — cada carga con cuántas personas creó y
--       reutilizó, para Estadísticas (R-ORG.5).
--
-- **No hay reconstrucción retroactiva.** Las personas cargadas antes de esta
-- migración no tienen vínculo y no se les inventa uno: `alias_origen` guarda
-- la plataforma (`origen`), no la carga, y dos cargas del mismo origen son
-- indistinguibles. La ficha lo dice en vez de mostrar «sin origen» (D67).
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

-- ---------- 1 · El vínculo persona ↔ carga ------------------------------

create table if not exists persona_carga (
  id_persona uuid        not null references persona(id_persona) on delete cascade,
  carga_id   bigint      not null references carga(id) on delete cascade,
  origen     text        not null check (origen in ('creada', 'reutilizada')),
  creado_en  timestamptz not null default now(),
  primary key (id_persona, carga_id)
);

comment on table persona_carga is
  'R-ORG.1 — de qué cargas proviene cada persona. Muchos a muchos: una '
  'persona puede aparecer en varias cargas y se registran todas. Una baja '
  'lo arrastra (on delete cascade desde persona).';
comment on column persona_carga.origen is
  'creada: la carga dio de alta a la persona · reutilizada: ya existía y el '
  'dedup (o el alias del archivo) la encontró. Se fija la primera vez y no '
  'se pisa: si la carga la creó, un lote posterior que la encuentra no la '
  'convierte en reutilizada.';

-- La composición de una carga y el filtro del listado van por `carga_id`;
-- la ficha, por `id_persona`, que ya cubre la clave primaria.
create index if not exists persona_carga_carga_idx on persona_carga (carga_id);

alter table persona_carga enable row level security;

-- ---------- 2 · Los datos del estudio, en la carga ----------------------

alter table carga add column if not exists fecha_estudio date;
alter table carga add column if not exists publico_objetivo text;

comment on column carga.fecha_estudio is
  'R-ORG.4 — cuándo se hizo el estudio del que salen estos datos (no cuándo '
  'se cargó: eso es creado_en).';
comment on column carga.publico_objetivo is
  'R-ORG.6 — texto libre que describe la población del estudio. Documenta la '
  'procedencia; no es un criterio de filtro ni un atributo del catálogo.';

-- ---------- 3 · Objetivo de composición por ámbito ----------------------
--
-- `panel` es lo de siempre. `todos` es el universo de referencia de la
-- bóveda entera: comparar el conjunto contra la población es una pregunta
-- legítima y distinta de la del panel nacional, y por eso no comparten fila.

alter table objetivo_composicion
  add column if not exists ambito text not null default 'panel';

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'objetivo_composicion_ambito_check') then
    alter table objetivo_composicion
      add constraint objetivo_composicion_ambito_check
      check (ambito in ('panel', 'todos'));
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'objetivo_composicion_ambito_panel') then
    -- Un objetivo de panel tiene panel; uno de «todos», no. Sin esto una
    -- fila con ámbito «todos» y un panel_id quedaría en los dos lados.
    alter table objetivo_composicion
      add constraint objetivo_composicion_ambito_panel
      check ((ambito = 'panel') = (panel_id is not null));
  end if;
end $$;

alter table objetivo_composicion alter column panel_id drop not null;

-- El `unique (panel_id, dimension, categoria)` de la 0001 no alcanza para
-- «todos»: con panel_id nulo, Postgres considera distintas a dos filas
-- iguales. Este índice cierra ese hueco.
create unique index if not exists objetivo_composicion_todos_unico
  on objetivo_composicion (dimension, categoria)
  where ambito = 'todos';

comment on column objetivo_composicion.ambito is
  'R-ORG.3 — panel: universo del panel (panel_id obligatorio) · todos: '
  'universo de la bóveda entera (panel_id nulo). Una carga no tiene objetivo.';

-- ---------- 4 · Cada carga con su resultado ------------------------------

create or replace view v_carga_resumen as
select c.id, c.nombre, c.descripcion, c.ref_estudio, c.fecha_estudio,
       c.publico_objetivo, c.creado_en, c.creado_por,
       count(pc.id_persona) filter (where pc.origen = 'creada')::int
                                                        as personas_creadas,
       count(pc.id_persona) filter (where pc.origen = 'reutilizada')::int
                                                        as personas_reutilizadas,
       count(pc.id_persona)::int                        as personas
  from carga c
  left join persona_carga pc on pc.carga_id = c.id
 group by c.id;

comment on view v_carga_resumen is
  'R-ORG.5 — cada carga con los datos del estudio y cuántas personas creó y '
  'reutilizó. Cuenta a las que siguen en la bóveda: quien se dio de baja ya '
  'no figura.';

-- Igual que las tablas de la 0019 y la 0023: no son de ningún consumidor
-- externo. La 0014 ya hizo `revoke all on schema public from coloquio_app`;
-- esto es explícito para que se lea en la migración que las crea.
revoke all on persona_carga, v_carga_resumen from public;

commit;
