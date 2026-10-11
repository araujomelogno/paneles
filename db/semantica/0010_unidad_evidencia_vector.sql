-- ============================================================
--  STORE SEMÁNTICO — Migración 0010
--  R-UE · Unidades de evidencia: un vector por texto distinto
--  (specs/SPEC_unidades_de_evidencia.md, D73)
-- ============================================================
--
-- ── El problema ──
-- Medido sobre el corpus real (2026-10-11): 21.340 respuestas, 861 textos
-- distintos. **El 96 % del corpus son copias**: en una pregunta cerrada,
-- todas las personas que eligen la misma opción producen el mismo
-- `texto_embebido` y, por lo tanto, el mismo vector. Eso tiene dos síntomas
-- con una sola causa:
--
--   1 · El índice HNSW no alcanza un bloque de cientos de puntos idénticos
--       desde afuera: «celular xiaomi» da **0 %** de recall con
--       `ef_search = 200`. No es un ajuste: los duplicados crecen con el
--       corpus.
--   2 · La verificación se gasta en copias.
--
-- ── La solución ──
-- Cada texto distinto se embebe **una vez**, en `unidad_evidencia`, y cada
-- respuesta referencia su unidad (`respuesta.unidad_id`). El índice vectorial
-- pasa a esta tabla: 861 vectores distintos, sin bloques idénticos.
--
-- La clave es `hash_texto` —sha256 del texto embebido completo, que ya
-- incluye la pregunta—: «→ Xiaomi» en dos preguntas distintas son dos
-- unidades; la misma pregunta con el mismo texto en dos olas es **una**,
-- porque el verificador lee exactamente el mismo texto.
--
-- ── Lo que no se pierde ──
-- No hace falta re-embeber nada: textos idénticos ya tienen vectores
-- idénticos, y se toma el de la respuesta de menor id. **Cuesta US$ 0.** Pero
-- eso se **comprueba** antes de migrar (bloque 2): si dos respuestas con el
-- mismo texto tienen vectores distintos —restos de otra dimensión u otro
-- `input_type`—, la migración se detiene y lo dice. Es una inconsistencia que
-- conviene conocer, no esconder.
--
-- `respuesta.embedding` **se conserva** hasta verificar que todo funciona
-- sobre unidades: la quita una migración aparte
-- (`db/semantica/posteriores/0011_quitar_embedding_de_respuesta.sql`), que no
-- se aplica sola. Mientras tanto, un trigger completa esa columna desde la
-- unidad, así que el código nuevo no la escribe y el anterior sigue pudiendo
-- insertar: volver atrás el código no exige volver atrás la base.
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

-- 1 · `hash_texto` completo. La 0009 ya lo completó; esto cubre una base
--     donde alguien insertó a mano después. Mismo algoritmo que
--     `semantica.hash_texto()` (hay una prueba que los compara).
update respuesta
   set hash_texto = encode(sha256(convert_to(texto_embebido, 'UTF8')), 'hex')
 where hash_texto is null;

-- 2 · La comprobación de R-UE.5: mismo texto ⇒ mismo vector.
--     Solo la primera vez (si la tabla ya existe, la migración ya corrió y
--     lo que hay en `respuesta` lo mantienen el código y el trigger).
do $$
declare
  inconsistentes bigint;
  forzado boolean := coalesce(
      current_setting('paneles.unidades_inconsistentes', true), '') = 'tomar_la_primera';
begin
  if to_regclass('unidad_evidencia') is not null then
    return;
  end if;
  select count(*) into inconsistentes
    from (select hash_texto
            from respuesta
           group by hash_texto
          having count(distinct embedding::text) > 1
              or count(distinct texto_embebido) > 1) x;
  if inconsistentes > 0 and not forzado then
    raise exception
      E'Hay % texto(s) con más de un vector distinto entre sus respuestas.\n'
       'Esta migración toma un vector por texto; con vectores distintos, '
       'elegir uno escondería la inconsistencia.\n\n'
       'Para verlos:\n'
       '  select hash_texto, count(*) as respuestas,\n'
       '         count(distinct embedding::text) as vectores\n'
       '    from respuesta group by hash_texto\n'
       '   having count(distinct embedding::text) > 1;\n\n'
       'Si ya se entendió por qué (por ejemplo, restos de una carga anterior '
       'al cambio de input_type) y se decide tomar el vector de la respuesta '
       'de menor id, volvé a correrla con:\n'
       '  psql ... -c "set paneles.unidades_inconsistentes = ''tomar_la_primera''" '
       '-f db/semantica/0010_unidad_evidencia_vector.sql',
      inconsistentes
      using errcode = 'raise_exception';
  end if;
  if inconsistentes > 0 then
    raise notice 'Se migran % texto(s) inconsistentes tomando el vector de la '
                 'respuesta de menor id (paneles.unidades_inconsistentes).',
                 inconsistentes;
  end if;
end $$;

-- 3 · La unidad: un texto, su huella y su vector.
create table if not exists unidad_evidencia (
  id              bigint generated always as identity primary key,
  hash_texto      text        not null unique,
  texto_embebido  text        not null,
  embedding       vector(512) not null,
  creado_en       timestamptz not null default now(),
  -- Para la FK compuesta de `respuesta`: garantiza que la huella de una
  -- respuesta y la de su unidad sean la misma.
  unique (id, hash_texto)
);

comment on table unidad_evidencia is
  'R-UE · Un texto embebido distinto y su vector, una sola vez. Las '
  'respuestas idénticas apuntan a la misma unidad (respuesta.unidad_id). '
  'Una unidad que se queda sin respuestas se borra sola (trigger), así que '
  'una baja se lleva el vector junto con la última respuesta que lo usaba.';
comment on column unidad_evidencia.hash_texto is
  'sha256 hex del texto_embebido completo (incluye la pregunta). Es la '
  'clave de la unidad: la misma que semantica.hash_texto().';
comment on column unidad_evidencia.embedding is
  'El vector de texto_embebido, en 512 dimensiones (EMBEDDINGS_DIMS).';

alter table unidad_evidencia enable row level security;

-- 4 · El corpus actual: una unidad por texto, con el vector de la respuesta
--     de menor id.
insert into unidad_evidencia (hash_texto, texto_embebido, embedding)
select distinct on (r.hash_texto) r.hash_texto, r.texto_embebido, r.embedding
  from respuesta r
 order by r.hash_texto, r.id
on conflict (hash_texto) do nothing;

-- 5 · La referencia. FK compuesta: una respuesta no puede apuntar a una
--     unidad con otro texto.
alter table respuesta add column if not exists unidad_id bigint;

update respuesta r
   set unidad_id = u.id
  from unidad_evidencia u
 where u.hash_texto = r.hash_texto
   and r.unidad_id is null;

alter table respuesta alter column hash_texto set not null;
alter table respuesta alter column unidad_id  set not null;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'respuesta_unidad_fk'
                    and conrelid = 'respuesta'::regclass) then
    alter table respuesta
      add constraint respuesta_unidad_fk
      foreign key (unidad_id, hash_texto)
      references unidad_evidencia (id, hash_texto);
  end if;
end $$;

comment on column respuesta.unidad_id is
  'R-UE · La unidad de evidencia de esta respuesta: dónde vive su vector. '
  'Respuestas con el mismo texto comparten unidad.';
comment on column respuesta.hash_texto is
  'sha256 hex del texto_embebido. Es la clave de su unidad de evidencia '
  '(FK compuesta con unidad_id).';

create index if not exists respuesta_unidad_id_idx on respuesta (unidad_id);
-- La agrupación de la 0009 era por (pregunta, huella); ahora la unidad es la
-- huella sola y se llega por `unidad_id`.
drop index if exists respuesta_unidad_idx;

-- 6 · El índice vectorial, sobre las unidades.
create index if not exists unidad_evidencia_embedding_idx
    on unidad_evidencia using hnsw (embedding vector_cosine_ops);

-- 7 · Transición: el código nuevo no escribe `respuesta.embedding`. Este
--     trigger lo completa desde la unidad (la columna sigue siendo `not
--     null`, y el chequeo corre después de los triggers BEFORE), y si el
--     que inserta es el código anterior —sin `unidad_id`—, resuelve o crea
--     la unidad con el vector que trae. Lo quita la 0011 junto con la
--     columna.
create or replace function respuesta_asegurar_unidad() returns trigger
language plpgsql as $$
begin
  if new.hash_texto is null then
    new.hash_texto := encode(sha256(convert_to(new.texto_embebido, 'UTF8')), 'hex');
  end if;
  if new.unidad_id is null
     or (tg_op = 'UPDATE' and new.hash_texto is distinct from old.hash_texto
         and new.unidad_id = old.unidad_id) then
    if new.embedding is not null then
      insert into unidad_evidencia (hash_texto, texto_embebido, embedding)
           values (new.hash_texto, new.texto_embebido, new.embedding)
      on conflict (hash_texto) do nothing;
    end if;
    select id into new.unidad_id
      from unidad_evidencia where hash_texto = new.hash_texto;
  end if;
  if new.embedding is null
     or (tg_op = 'UPDATE' and new.unidad_id is distinct from old.unidad_id
         and new.embedding is not distinct from old.embedding) then
    select embedding into new.embedding
      from unidad_evidencia where id = new.unidad_id;
  end if;
  return new;
end $$;

comment on function respuesta_asegurar_unidad() is
  'R-UE · Transición: completa respuesta.embedding desde la unidad y '
  'resuelve la unidad para el código anterior. Se va con la 0011.';

drop trigger if exists respuesta_asegurar_unidad on respuesta;
create trigger respuesta_asegurar_unidad
  before insert or update on respuesta
  for each row execute function respuesta_asegurar_unidad();

-- 8 · Una unidad sin respuestas se borra. Es lo que hace que una baja se
--     lleve el vector: si la persona era la única que dio ese texto, su
--     unidad se va con su última respuesta. Vive en la base y no en Python
--     porque las respuestas se borran por varios caminos (baja, retiro por
--     estudio, reproceso, `on delete cascade` desde individuo, pregunta o
--     cuestionario), y uno que se olvidara dejaría el texto vivo.
create or replace function borrar_unidades_huerfanas() returns trigger
language plpgsql as $$
begin
  delete from unidad_evidencia u
   where u.id in (select distinct v.unidad_id from viejas v)
     and not exists (select 1 from respuesta r where r.unidad_id = u.id);
  return null;
end $$;

comment on function borrar_unidades_huerfanas() is
  'R-UE · Borra las unidades de evidencia que se quedaron sin respuestas '
  'después de un delete o un update de respuesta.';

drop trigger if exists respuesta_borrar_unidades_huerfanas_del on respuesta;
create trigger respuesta_borrar_unidades_huerfanas_del
  after delete on respuesta
  referencing old table as viejas
  for each statement execute function borrar_unidades_huerfanas();

drop trigger if exists respuesta_borrar_unidades_huerfanas_upd on respuesta;
create trigger respuesta_borrar_unidades_huerfanas_upd
  after update on respuesta
  referencing old table as viejas
  for each statement execute function borrar_unidades_huerfanas();

-- 9 · El catálogo de la 0009, ahora sobre la unidad física.
drop view if exists v_unidad_evidencia;
create view v_unidad_evidencia as
select u.id                           as unidad_id,
       u.hash_texto,
       count(r.id)                    as respuestas,
       count(distinct r.individuo_id) as individuos,
       count(distinct r.pregunta_id)  as preguntas,
       min(r.id)                      as respuesta_representante
  from unidad_evidencia u
  join respuesta r on r.unidad_id = u.id
 group by u.id, u.hash_texto;

comment on view v_unidad_evidencia is
  'R-UE · Una fila por unidad de evidencia (texto distinto), con cuántas '
  'respuestas y personas la comparten. El veredicto de una unidad vale para '
  'todas.';

-- 10 · La procedencia suma la unidad, al final (una vista solo admite
--      columnas nuevas al final con `create or replace`).
create or replace view v_respuesta_estudio as
select r.id             as respuesta_id,
       i.id_persona,
       c.ref_estudio,
       c.nombre         as estudio,
       c.fecha_campo,
       p.codigo         as pregunta_codigo,
       p.texto          as pregunta_texto,
       p.tipo           as pregunta_tipo,
       r.valor_texto,
       r.texto_embebido,
       r.embedding,
       r.unidad_id
  from respuesta r
  join individuo i    on i.id = r.individuo_id
  join pregunta p     on p.id = r.pregunta_id
  join cuestionario c on c.id = p.cuestionario_id;

-- 11 · Los veredictos de la completa, por unidad (la huella sola). Una
--      ejecución anterior pudo juzgar el mismo texto en dos preguntas: se
--      conserva el más severo con la regla de `consultas.hallazgo_de`
--      (no_cumple > cumple > dudoso > sin_verificar > irrelevante).
delete from veredicto_unidad v
 using (select id,
               row_number() over (
                 partition by ejecucion_id, criterio_orden, hash_texto
                 order by case veredicto when 'no_cumple' then 0
                                         when 'cumple' then 1
                                         when 'dudoso' then 2
                                         when 'sin_verificar' then 3
                                         else 4 end, id) as n
          from veredicto_unidad) d
 where d.id = v.id and d.n > 1;

do $$
declare
  restriccion record;
begin
  for restriccion in
    select conname from pg_constraint
     where conrelid = 'veredicto_unidad'::regclass and contype in ('u', 'f')
  loop
    execute format('alter table veredicto_unidad drop constraint %I',
                   restriccion.conname);
  end loop;
end $$;

alter table veredicto_unidad alter column pregunta_id drop not null;
alter table veredicto_unidad
  add constraint veredicto_unidad_pregunta_fk
  foreign key (pregunta_id) references pregunta(id) on delete set null;
create unique index if not exists veredicto_unidad_clave
    on veredicto_unidad (ejecucion_id, criterio_orden, hash_texto);
-- Compatibilidad para volver atrás el código: el anterior hace
-- `on conflict (ejecucion_id, criterio_orden, pregunta_id, hash_texto)`, y ese
-- `on conflict` necesita un índice único con exactamente esas columnas. Lo
-- implica el de arriba, así que nunca rechaza nada. Lo quita la 0011.
create unique index if not exists veredicto_unidad_clave_r_cs
    on veredicto_unidad (ejecucion_id, criterio_orden, pregunta_id, hash_texto);

comment on column veredicto_unidad.pregunta_id is
  'R-UE · La pregunta de la respuesta que se le mostró al verificador. '
  'Informativa: la unidad es hash_texto, y el mismo texto en dos olas es una '
  'sola unidad.';

commit;
