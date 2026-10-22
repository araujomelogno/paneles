-- ============================================================
--  STORE DE BÓVEDA — Migración 0031
--  Clientes de cada carga y lista negra
--  (specs/SPEC_clientes_lista_negra_y_mejoras.md, D80).
-- ============================================================
--
-- Tres piezas:
--
--   1 · `cliente` y `carga_cliente` (R-CLI.1) — el cliente es una entidad
--       compartida entre cargas, con el nombre normalizado (la misma regla
--       que `tematica`): «Antel», «ANTEL» y «antel» son uno.
--   2 · `v_persona_cliente` (R-CLI.2) — los clientes de cada persona se
--       **derivan** del vínculo `persona_carga`: quien estuvo en una carga
--       para Antel participó en un estudio para Antel. No hay tabla por
--       persona: corregir los clientes de una carga se ve en todos sus
--       panelistas sin tocar ninguno.
--   3 · `lista_negra` (R-LN) — una fila por **entrada**; estar en la lista
--       es tener una sin `removido_en`. Sacar a alguien no borra nada: la
--       historia completa queda. Cascada desde `persona`: hasta que se
--       decida otra cosa con asesoría (R-LN.4), la baja se la lleva.
--
-- Y un cambio en la **superficie de COLOQUIO**: `f_persona_convocable()`
-- deja afuera a quien tiene una entrada activa en la lista negra. COLOQUIO
-- convoca por su cuenta leyendo esa vista; si la lista no llegara ahí, una
-- persona excluida de las encuestas podría igual ser invitada a un focus
-- group. Se reemplaza con la misma firma (`create or replace`), así que la
-- vista y los `grant` de la 0021 quedan como están.
--
-- El cliente es información comercial confidencial (R-CLI.4): ninguna de
-- estas tablas ni la vista tiene `grant` a COLOQUIO.
--
-- Aditiva salvo el reemplazo de `f_persona_convocable()`. Se puede correr
-- dos veces. Correr `scripts/verificar_coloquio.py` después.
begin;

-- ---------- 1 · Clientes ------------------------------------------------

create table if not exists cliente (
  id         bigint generated always as identity primary key,
  nombre     text not null,
  creado_en  timestamptz not null default now(),
  constraint cliente_nombre_unico unique (nombre),
  constraint cliente_nombre_normalizado check (
    nombre = lower(regexp_replace(btrim(nombre), '\s+', ' ', 'g'))
    and char_length(nombre) between 1 and 120)
);

comment on table cliente is
  'R-CLI.1: para quién se hizo un estudio. Compartido entre cargas, con el '
  'nombre normalizado (minúsculas, espacios simples). Confidencial: nunca en '
  'el portal ni en la superficie de COLOQUIO.';

create table if not exists carga_cliente (
  carga_id    bigint not null references carga(id) on delete cascade,
  cliente_id  bigint not null references cliente(id) on delete restrict,
  creado_en   timestamptz not null default now(),
  primary key (carga_id, cliente_id)
);

create index if not exists carga_cliente_por_cliente on carga_cliente (cliente_id);

comment on table carga_cliente is
  'R-CLI.1: los clientes de cada carga. Se borra con la carga; el cliente '
  'queda en el vocabulario.';

create or replace view v_persona_cliente as
select pc.id_persona, cc.cliente_id, c.nombre as cliente, pc.carga_id
  from persona_carga pc
  join carga_cliente cc on cc.carga_id = pc.carga_id
  join cliente c on c.id = cc.cliente_id;

comment on view v_persona_cliente is
  'R-CLI.2: los clientes de cada persona, derivados de sus cargas. Una fila '
  'por (persona, cliente, carga): una persona con el mismo cliente en dos '
  'cargas aparece dos veces, y quien cuente personas cuenta `distinct`.';

-- ---------- 2 · Lista negra ---------------------------------------------

create table if not exists lista_negra (
  id               bigint generated always as identity primary key,
  id_persona       uuid not null references persona(id_persona) on delete cascade,
  categoria        text not null check (categoria in
                     ('calidad_respuestas', 'fraude_duplicacion',
                      'comportamiento', 'otro')),
  detalle          text not null check (char_length(btrim(detalle)) >= 5),
  agregado_por     text,
  agregado_en      timestamptz not null default now(),
  -- Las invitaciones pendientes que se cancelaron al agregarla (R-LN.2):
  -- cuáles encuestas y cuántas, para que la cancelación quede explicada.
  invitaciones_canceladas jsonb not null default '[]'::jsonb,
  removido_por     text,
  removido_en      timestamptz,
  motivo_remocion  text,
  constraint lista_negra_remocion_completa check (
    (removido_en is null and removido_por is null and motivo_remocion is null)
    or (removido_en is not null and char_length(btrim(coalesce(motivo_remocion, ''))) >= 5))
);

-- A lo sumo una entrada activa por persona; las removidas, las que haya.
create unique index if not exists lista_negra_una_activa
    on lista_negra (id_persona) where removido_en is null;

comment on table lista_negra is
  'R-LN: una fila por cada entrada en la lista negra. Activa = sin '
  'removido_en. Sacar a alguien completa la remoción y no borra la fila. '
  'Cascada desde persona: la baja se la lleva (R-LN.4, pendiente de '
  'asesoría).';

alter table cliente       enable row level security;
alter table carga_cliente enable row level security;
alter table lista_negra   enable row level security;

revoke all on cliente, carga_cliente, lista_negra, v_persona_cliente from public;

-- ---------- 3 · La superficie de COLOQUIO --------------------------------
-- Misma definición que la 0021, con una condición más al final.
create or replace function f_persona_convocable()
returns table (
  id_persona   uuid,
  finalidades  text[],
  sexo         text,
  localidad    text,
  tramo_etario text,
  edad         int)
language sql
stable
security definer
set search_path = public
as $$
  select
    p.id_persona,
    array(select distinct c.finalidad
            from consentimiento c
           where c.id_persona = p.id_persona
             and c.estado = 'vigente'
             and c.ref_estudio is null
           order by c.finalidad) as finalidades,
    d.sexo,
    d.localidad,
    d.tramo_etario,
    d.edad
    from persona p
    left join v_demografia d on d.id_persona = p.id_persona
   where p.estado = 'activa'
     and exists (select 1 from consentimiento c
                  where c.id_persona = p.id_persona
                    and c.estado = 'vigente'
                    and c.ref_estudio is null)
     and not exists (select 1 from persona_borrada b
                      where b.id_persona = p.id_persona)
     -- 0031 · R-LN.2 — quien está en la lista negra no se convoca, ni desde
     -- `paneles` ni desde COLOQUIO.
     and not exists (select 1 from lista_negra ln
                      where ln.id_persona = p.id_persona
                        and ln.removido_en is null);
$$;

comment on function f_persona_convocable() is
  'La superficie de convocables. Una fila por persona: el consentimiento se '
  'evalúa con `exists`, nunca con un `join` que multiplique (0021). Sin '
  'quienes están en la lista negra (0031).';

commit;
