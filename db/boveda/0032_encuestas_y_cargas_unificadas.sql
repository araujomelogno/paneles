-- ============================================================
--  STORE DE BÓVEDA — Migración 0032
--  Encuestas y cargas unificadas
--  (specs/SPEC_encuestas_y_cargas_unificadas.md, D81).
-- ============================================================
--
-- El modelo nuevo:
--
--   · **Encuesta** = un estudio. Guarda los datos del estudio: nombre,
--     fecha de campo, público objetivo, clientes, temáticas y su audiencia
--     (un panel o, si es independiente, la foto de una consulta).
--   · **Carga** = una ingesta de respuestas de esa encuesta (un archivo).
--     Guarda lo propio del archivo: cuándo, quién, modo de relevamiento,
--     tipo de datos, su huella y el origen creado/reutilizado de cada
--     persona. **Toda carga pertenece a una encuesta.**
--   · **Participación** = la persona fue convocada (`convocado`) y/o
--     respondió. Una sola por persona y encuesta (ya era así).
--
-- **Cambia datos.** Además del esquema, crea una encuesta para cada carga
-- existente (independiente, cerrada, con el mismo `ref_estudio`: el
-- cuestionario semántico queda vinculado sin tocarlo) y una carga para cada
-- encuesta con respuestas, más las participaciones de las personas de las
-- cargas. No re-embebe nada ni llama a ninguna API.
--
-- **Antes de aplicarla:** respaldo de las dos bases y la corrida de prueba
-- (`scripts/migrar_encuestas_y_cargas.py prueba`), que ejecuta este mismo
-- archivo en una transacción que se deshace y dice qué va a crear. Después:
-- `migrar_encuestas_y_cargas.py semantico` (llena `respuesta.carga_id`) y
-- `verificar`. Si la verificación falla, se restaura el respaldo.
--
-- Es una sola transacción: o queda todo o no queda nada. Se niega a correr
-- con una ingesta en curso.
begin;

do $$
begin
  if exists (select 1 from ingesta_trabajo
              where estado in ('encolada', 'procesando')) then
    raise exception
      'Hay ingestas en curso (ingesta_trabajo encolada o procesando). Esperá '
      'a que terminen antes de aplicar la 0032: un lote que corre durante la '
      'migración escribiría en una carga sin encuesta.';
  end if;
end $$;

-- ---------- 1 · La encuesta es el estudio -------------------------------

alter table encuesta alter column panel_id drop not null;

-- La FK sigue como estaba (`on delete cascade`, 0001). Los paneles no se
-- borran desde la aplicación: se archivan (R-EU.8).

alter table encuesta
  add column if not exists tipo text not null default 'panel',
  add column if not exists publico_objetivo text,
  add column if not exists descripcion text;

alter table encuesta drop constraint if exists encuesta_tipo_check;
alter table encuesta add constraint encuesta_tipo_check
  check (tipo in ('panel', 'independiente'));
-- Una encuesta de panel tiene panel; una independiente, no (R-EU.5).
alter table encuesta drop constraint if exists encuesta_tipo_y_panel;
alter table encuesta add constraint encuesta_tipo_y_panel
  check ((tipo = 'panel') = (panel_id is not null));

comment on column encuesta.tipo is
  'R-EU.5 — panel: se convoca a su panel · independiente: su audiencia es '
  'la foto de una consulta, o no tiene (la crea una carga desde Panelistas).';
comment on column encuesta.publico_objetivo is
  'R-EU.2 — a quién estaba dirigido el estudio. Texto libre: describe al '
  'estudio, no a las personas.';

-- Las cargas comparten el `ref_estudio` de su encuesta; el de la encuesta es
-- único (lo era en los hechos: se genera al crearla).
create unique index if not exists encuesta_ref_estudio_unico
  on encuesta (ref_estudio);

-- ---------- 2 · Convocados y respondentes -------------------------------

alter table participacion
  add column if not exists convocado boolean not null default true;

comment on column participacion.convocado is
  'R-EU.4 — estaba en la audiencia de la encuesta (la convocó el sistema). '
  'Falso para quien respondió sin haber sido convocado: en una encuesta con '
  'audiencia, casi siempre un enlace reenviado.';

-- Lo que entró por un archivo de campo no fue una convocatoria del sistema.
update participacion set convocado = (origen = 'convocatoria');

-- ---------- 3 · La carga es una ingesta de una encuesta ------------------

alter table carga
  add column if not exists encuesta_id bigint
    references encuesta(id) on delete cascade,
  add column if not exists archivo_huella text;

comment on column carga.encuesta_id is
  'R-EU.1 — la encuesta a la que pertenece. Toda carga tiene una.';
comment on column carga.archivo_huella is
  'R-EU.3 — sha256 del archivo (o de sus filas): avisa antes de procesar un '
  'archivo idéntico a uno ya cargado en esa encuesta.';
comment on column carga.fecha_estudio is
  'Retirada por la 0032: la fecha del estudio es `encuesta.fecha_campo`. Se '
  'quita en `db/boveda/posteriores/`.';
comment on column carga.publico_objetivo is
  'Retirada por la 0032: el público objetivo es de la encuesta. Se quita en '
  '`db/boveda/posteriores/`.';

-- Las cargas de una encuesta comparten su `ref_estudio`: ya no es único.
drop index if exists carga_ref_estudio_unico;
create index if not exists carga_por_ref_estudio on carga (ref_estudio);
create index if not exists carga_por_encuesta on carga (encuesta_id);
create index if not exists carga_por_huella
  on carga (encuesta_id, archivo_huella) where archivo_huella is not null;

-- ---------- 4 · Migración: una encuesta por carga existente (R-EU.7) -----
-- Independiente, cerrada, con el mismo `ref_estudio`: su cuestionario del
-- store semántico queda vinculado sin tocarlo. Se reconoce la carga por ese
-- `ref_estudio`, que era único en `carga`.

insert into encuesta (panel_id, tipo, nombre, fecha_campo, publico_objetivo,
                      descripcion, estado, ref_estudio, creado_en)
select null, 'independiente', c.nombre, c.fecha_estudio, c.publico_objetivo,
       c.descripcion, 'cerrada', c.ref_estudio, c.creado_en
  from carga c
 where c.encuesta_id is null
   and not exists (select 1 from encuesta e where e.ref_estudio = c.ref_estudio);

update carga c
   set encuesta_id = e.id
  from encuesta e
 where c.encuesta_id is null and e.ref_estudio = c.ref_estudio;

-- ---------- 5 · Migración: una carga por encuesta con respuestas ---------
-- Las respuestas que entraron por el flujo viejo de Encuestas no tenían
-- carga. Una encuesta «con respuestas» es una con alguien que respondió.
-- Sus personas quedan como `reutilizada`: no se sabe si las creó ese
-- archivo, y así el borrado de la carga nunca ofrece darlas de baja.

insert into carga (nombre, descripcion, ref_estudio, encuesta_id, creado_en)
select e.nombre,
       'Respuestas ingestadas antes de unificar encuestas y cargas (0032).',
       e.ref_estudio, e.id,
       coalesce((select min(pa.respondio_en) from participacion pa
                  where pa.encuesta_id = e.id and pa.respondio), e.creado_en)
  from encuesta e
 where exists (select 1 from participacion pa
                where pa.encuesta_id = e.id and pa.respondio)
   and not exists (select 1 from carga c where c.encuesta_id = e.id);

insert into persona_carga (id_persona, carga_id, origen, creado_en)
select pa.id_persona, c.id, 'reutilizada', coalesce(pa.respondio_en, now())
  from carga c
  join participacion pa on pa.encuesta_id = c.encuesta_id and pa.respondio
 where c.descripcion = 'Respuestas ingestadas antes de unificar encuestas y cargas (0032).'
on conflict (id_persona, carga_id) do nothing;

-- ---------- 6 · Migración: la participación de quien vino en una carga ---
-- Respondió: es un hecho. No fue convocado desde el sistema.

insert into participacion (encuesta_id, id_persona, origen, respondio,
                           respondio_en, convocado, convocado_en)
select c.encuesta_id, pc.id_persona, 'importacion', true, pc.creado_en, false,
       pc.creado_en
  from persona_carga pc
  join carga c on c.id = pc.carga_id
on conflict (encuesta_id, id_persona) do nothing;

-- Ahora sí: toda carga tiene encuesta.
alter table carga alter column encuesta_id set not null;

-- El `ref_estudio` de una carga es el de su encuesta, y lo garantiza la
-- base: así ninguna ingesta puede escribir en un cuestionario que no es el
-- de su encuesta (R-EU.3).
create or replace function carga_ref_estudio_de_la_encuesta()
returns trigger
language plpgsql
as $$
begin
  select e.ref_estudio into new.ref_estudio
    from encuesta e where e.id = new.encuesta_id;
  return new;
end;
$$;

drop trigger if exists carga_ref_estudio_de_la_encuesta on carga;
create trigger carga_ref_estudio_de_la_encuesta
  before insert or update of encuesta_id, ref_estudio on carga
  for each row execute function carga_ref_estudio_de_la_encuesta();

revoke execute on function carga_ref_estudio_de_la_encuesta() from public;

-- ---------- 7 · Los clientes son del estudio -----------------------------

drop view if exists v_persona_cliente;

create table if not exists encuesta_cliente (
  encuesta_id bigint not null references encuesta(id) on delete cascade,
  cliente_id  bigint not null references cliente(id) on delete restrict,
  creado_en   timestamptz not null default now(),
  primary key (encuesta_id, cliente_id)
);
create index if not exists encuesta_cliente_por_cliente
  on encuesta_cliente (cliente_id);

comment on table encuesta_cliente is
  'R-EU.2 — para quién se hizo cada estudio. Reemplaza a `carga_cliente`.';

insert into encuesta_cliente (encuesta_id, cliente_id, creado_en)
select c.encuesta_id, cc.cliente_id, min(cc.creado_en)
  from carga_cliente cc join carga c on c.id = cc.carga_id
 group by c.encuesta_id, cc.cliente_id
on conflict do nothing;

drop table carga_cliente;

-- Los clientes de una persona: los de las encuestas que respondió. Sigue
-- sin tabla por persona: corregir los de una encuesta se ve en el acto.
create view v_persona_cliente as
select pa.id_persona, ec.cliente_id, cl.nombre as cliente, pa.encuesta_id
  from participacion pa
  join encuesta_cliente ec on ec.encuesta_id = pa.encuesta_id
  join cliente cl on cl.id = ec.cliente_id
 where pa.respondio;

comment on view v_persona_cliente is
  'R-CLI.2 / R-EU.2 — los clientes de cada persona: los de las encuestas que '
  'respondió. Confidencial: sin grant a COLOQUIO.';

-- ---------- 8 · Las temáticas son del estudio ----------------------------

create table if not exists encuesta_tematica (
  encuesta_id bigint      not null references encuesta(id) on delete cascade,
  tematica_id bigint      not null references tematica(id) on delete restrict,
  origen      text        not null check (origen in ('propuesta_ia', 'manual')),
  creado_en   timestamptz not null default now(),
  primary key (encuesta_id, tematica_id)
);
create index if not exists encuesta_tematica_tematica_idx
  on encuesta_tematica (tematica_id);

comment on table encuesta_tematica is
  'R-TEM.1 / R-EU.2 — las temáticas de cada estudio. Reemplaza a '
  '`carga_tematica`. El origen se fija la primera vez.';

insert into encuesta_tematica (encuesta_id, tematica_id, origen, creado_en)
select distinct on (c.encuesta_id, ct.tematica_id)
       c.encuesta_id, ct.tematica_id, ct.origen, ct.creado_en
  from carga_tematica ct join carga c on c.id = ct.carga_id
 order by c.encuesta_id, ct.tematica_id, ct.creado_en
on conflict do nothing;

-- `tematica_pregunta` y `persona_tematica` se re-llavean por encuesta. Una
-- columna nueva que se llena y después reemplaza a `carga_id`: actualizar la
-- clave en el lugar podría chocar con otra fila a mitad del `update`.
alter table tematica_pregunta
  drop constraint if exists tematica_pregunta_carga_id_tematica_id_fkey;
alter table persona_tematica
  drop constraint if exists persona_tematica_carga_id_tematica_id_fkey;

alter table tematica_pregunta add column if not exists encuesta_id bigint;
update tematica_pregunta tp
   set encuesta_id = c.encuesta_id
  from carga c where c.id = tp.carga_id;

alter table persona_tematica add column if not exists encuesta_id bigint;
update persona_tematica pt
   set encuesta_id = c.encuesta_id
  from carga c where c.id = pt.carga_id;

-- Antes de la 0032 cada carga es su propia encuesta, así que no debería
-- haber repetidas por encuesta; por si las hubiera, queda la de la carga más
-- vieja en vez de fallar la clave primaria.
delete from tematica_pregunta a
 using tematica_pregunta b
 where a.encuesta_id = b.encuesta_id and a.tematica_id = b.tematica_id
   and a.codigo_pregunta = b.codigo_pregunta and a.carga_id > b.carga_id;
delete from persona_tematica a
 using persona_tematica b
 where a.encuesta_id = b.encuesta_id and a.tematica_id = b.tematica_id
   and a.id_persona = b.id_persona and a.carga_id > b.carga_id;

alter table tematica_pregunta drop constraint if exists tematica_pregunta_pkey;
alter table tematica_pregunta drop column carga_id;
alter table tematica_pregunta alter column encuesta_id set not null;
alter table tematica_pregunta
  add primary key (encuesta_id, tematica_id, codigo_pregunta),
  add constraint tematica_pregunta_encuesta_tematica_fkey
    foreign key (encuesta_id, tematica_id)
    references encuesta_tematica (encuesta_id, tematica_id) on delete cascade;

drop index if exists persona_tematica_carga_idx;
alter table persona_tematica drop constraint if exists persona_tematica_pkey;
alter table persona_tematica drop column carga_id;
alter table persona_tematica alter column encuesta_id set not null;
alter table persona_tematica
  add primary key (id_persona, tematica_id, encuesta_id),
  add constraint persona_tematica_encuesta_tematica_fkey
    foreign key (encuesta_id, tematica_id)
    references encuesta_tematica (encuesta_id, tematica_id) on delete cascade;
create index if not exists persona_tematica_encuesta_idx
  on persona_tematica (encuesta_id);

drop table carga_tematica;

comment on table persona_tematica is
  'R-TEM.6 / R-EU.2 — la persona respondió al menos una pregunta de la '
  'temática en ese estudio. Se va con la persona, con el estudio y con la '
  'temática del estudio.';

-- ---------- 9 · Cada carga con su encuesta -------------------------------
-- Los datos del estudio salen de la encuesta: la fecha y el público objetivo
-- de la carga quedaron retirados.

drop view if exists v_carga_resumen;
create view v_carga_resumen as
select c.id, c.nombre, c.descripcion, c.ref_estudio, c.encuesta_id,
       e.nombre as encuesta, e.tipo as encuesta_tipo,
       e.fecha_campo as fecha_estudio, e.publico_objetivo,
       c.creado_en, c.creado_por, c.archivo_huella,
       c.tipo_datos_origen, c.tipo_datos_origen_otro,
       c.modo_relevamiento, c.modo_relevamiento_otro,
       count(pc.id_persona) filter (where pc.origen = 'creada')::int
                                                        as personas_creadas,
       count(pc.id_persona) filter (where pc.origen = 'reutilizada')::int
                                                        as personas_reutilizadas,
       count(pc.id_persona)::int                        as personas
  from carga c
  join encuesta e on e.id = c.encuesta_id
  left join persona_carga pc on pc.carga_id = c.id
 group by c.id, e.id;

comment on view v_carga_resumen is
  'R-ORG.5 / R-EU.2 — cada carga con los datos de su estudio (de la '
  'encuesta) y cuántas personas creó y reutilizó.';

-- ---------- 10 · Borrar una encuesta (R-EU.9), con su auditoría ----------

create table if not exists encuesta_borrado (
  id                         bigint generated always as identity primary key,
  encuesta_id                bigint references encuesta(id) on delete set null,
  encuesta_nombre            text not null,
  ref_estudio                uuid not null,
  estado                     text not null default 'en_curso'
                               check (estado in ('en_curso', 'terminado')),
  vista_previa               jsonb not null default '{}'::jsonb,
  respuestas_borradas        int not null default 0,
  participaciones_borradas   int not null default 0,
  cargas_borradas            int not null default 0,
  actor_uid                  text,
  actor_email                text,
  creado_en                  timestamptz not null default now(),
  terminado_en               timestamptz
);

create unique index if not exists encuesta_borrado_uno_en_curso
  on encuesta_borrado (ref_estudio) where estado = 'en_curso';

comment on table encuesta_borrado is
  'R-EU.9 — quién borró qué estudio, cuándo, y cuántas respuestas, '
  'participaciones y cargas se fueron. Sobrevive a la encuesta. Las '
  'personas y sus datos demográficos se conservan.';

-- ---------- 11 · La superficie de COLOQUIO --------------------------------
-- Fatiga: las convocatorias de una encuesta independiente cuentan para
-- todos los paneles de la persona —la convocó el sistema igual—. Mismas
-- columnas y misma clave (una fila por persona y panel).

create or replace view v_fatiga_panelista as
select
  m.id_persona,
  m.panel_id,
  count(*) filter (
    where pa.convocado_en >= now() - interval '90 days'
      and pa.origen = 'convocatoria')::int      as recientes,
  count(pa.id) filter (where pa.origen = 'convocatoria')::int as totales,
  max(pa.convocado_en) filter (where pa.origen = 'convocatoria')
                                                as ultima_convocatoria,
  count(*) filter (where pa.respondio)::int     as respondidas
  from membresia m
  left join participacion pa
         on pa.id_persona = m.id_persona
        and exists (select 1 from encuesta e
                     where e.id = pa.encuesta_id
                       and (e.panel_id = m.panel_id or e.panel_id is null))
 where m.estado = 'activo'
 group by m.id_persona, m.panel_id;

-- Una convocatoria activa es estar **convocado** a una encuesta abierta. Una
-- participación que registra solo que alguien respondió (una carga) no es
-- motivo para leer su contacto.
create or replace function contacto_para_convocatoria(
    p_id_persona uuid,
    p_canal      text,
    p_motivo     text default 'convocatoria',
    p_actor      text default null)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  el_sistema text := sistema_de_la_conexion();
  el_dato    text;
  tiene_convocatoria boolean;
begin
  if p_canal not in ('email', 'celular') then
    raise exception 'Canal desconocido: %. Solo `email` o `celular`.', p_canal
      using errcode = 'invalid_parameter_value';
  end if;

  if not exists (
       select 1 from v_persona_convocable
        where id_persona = p_id_persona
          and 'contacto_participacion' = any(finalidades)) then
    raise exception
      'La persona % no tiene consentimiento vigente de '
      'contacto_participacion, o ya no está activa.', p_id_persona
      using errcode = 'insufficient_privilege';
  end if;

  select exists (
    select 1 from participacion pa
      join encuesta e on e.id = pa.encuesta_id
     where el_sistema = 'paneles'
       and pa.id_persona = p_id_persona
       and pa.convocado
       and e.estado <> 'cerrada'
     union all
    select 1 from convocatoria_externa c
     where c.sistema = el_sistema
       and c.id_persona = p_id_persona
       and c.vence_en > now())
    into tiene_convocatoria;

  if not tiene_convocatoria then
    raise exception
      'La persona % no tiene una convocatoria activa en el sistema «%»: no '
      'hay motivo para leer su contacto.', p_id_persona, el_sistema
      using errcode = 'insufficient_privilege';
  end if;

  execute format('select %I from persona where id_persona = $1', p_canal)
     into el_dato using p_id_persona;

  insert into reidentificacion (id_persona, actor_uid, actor_email, motivo,
                                contexto, sistema)
  values (p_id_persona, coalesce(p_actor, session_user), p_actor,
          coalesce(p_motivo, 'convocatoria'),
          jsonb_build_object('canal', p_canal,
                             'vacio', el_dato is null,
                             'via', 'contacto_para_convocatoria'),
          el_sistema);

  return coalesce(el_dato, '');
end;
$$;

-- ---------- 12 · Seguridad ------------------------------------------------

alter table encuesta_cliente  enable row level security;
alter table encuesta_tematica enable row level security;
alter table encuesta_borrado  enable row level security;

revoke all on encuesta_cliente, encuesta_tematica, encuesta_borrado,
              v_persona_cliente, v_carga_resumen from public;

commit;
