-- ============================================================
--  STORE SEMÁNTICO — Migración 0013
--  El historial de cada respuesta por carga
--  (specs/PEDIDO_historial_respuestas_por_carga.md, R-HIS.1, D95).
-- ============================================================
--
-- Todas las cargas de una encuesta escriben el mismo cuestionario: una
-- respuesta por persona y pregunta, y gana la última carga
-- (`respuesta.carga_id`, 0012). En una encuesta incremental el último archivo
-- trae **todo** el acumulado, así que borrar la última carga borraba las
-- respuestas del estudio entero aunque las cargas anteriores siguieran
-- existiendo (el caso de la carga 45). Para volver al valor de una carga
-- anterior hay que saber qué traía cada una, y hasta ahora solo se guardaba
-- la última.
--
-- `respuesta_carga` guarda una fila por persona, pregunta y carga. Va acá y
-- no en la bóveda porque es **contenido** de respuestas: la separación entre
-- identidad y contenido se mantiene. No tiene PII ni vectores propios: apunta
-- a la unidad de evidencia que ya existe (FK compuesta, como `respuesta`).
--
-- Por eso una unidad que solo usa el historial **no** es huérfana: hace falta
-- para volver a ese valor sin pagar otro embedding. La función de huérfanas
-- de la 0010 pasa a mirar también esta tabla, y un trigger nuevo la corre
-- cuando se borran o cambian filas del historial.
--
-- **Cambia datos**: crea el historial de las respuestas vigentes, con su
-- `carga_id` actual. Las cargas anteriores a la última no tienen registro
-- —ese dato nunca se guardó—: el historial empieza completo solo para las
-- cargas nuevas. La corrida de prueba es
-- `scripts/migrar_historial_de_respuestas.py prueba`. Re-ejecutable.
begin;

create table if not exists respuesta_carga (
  individuo_id bigint      not null references individuo(id) on delete cascade,
  pregunta_id  bigint      not null references pregunta(id) on delete cascade,
  -- La carga de la bóveda. Sin FK: los stores no se referencian.
  carga_id     bigint      not null,
  valor_texto  text,
  hash_texto   text        not null,
  unidad_id    bigint      not null,
  escrito_en   timestamptz not null default now(),
  primary key (individuo_id, pregunta_id, carga_id),
  foreign key (unidad_id, hash_texto) references unidad_evidencia (id, hash_texto)
);

comment on table respuesta_carga is
  'R-HIS.1 (D95) — qué traía cada carga: una fila por persona, pregunta y '
  'carga. Borrar una carga vuelve la respuesta vigente al valor de la carga '
  'más reciente que sigue existiendo; se borra solo lo que ninguna otra '
  'carga contiene. Sin vectores: apunta a la unidad de evidencia.';

create index if not exists respuesta_carga_por_carga on respuesta_carga (carga_id);
create index if not exists respuesta_carga_por_unidad on respuesta_carga (unidad_id);

alter table respuesta_carga enable row level security;
revoke all on respuesta_carga from public;

-- Una unidad es huérfana si no la usa ninguna respuesta **ni el historial**.
create or replace function borrar_unidades_huerfanas() returns trigger
language plpgsql as $$
begin
  delete from unidad_evidencia u
   where u.id in (select distinct v.unidad_id from viejas v)
     and not exists (select 1 from respuesta r where r.unidad_id = u.id)
     and not exists (select 1 from respuesta_carga h where h.unidad_id = u.id);
  return null;
end $$;

comment on function borrar_unidades_huerfanas() is
  'R-UE / R-HIS · Borra las unidades de evidencia que se quedaron sin '
  'respuestas ni filas de historial después de un delete o un update.';

revoke execute on function borrar_unidades_huerfanas() from public;

drop trigger if exists respuesta_carga_borrar_unidades_huerfanas_del on respuesta_carga;
create trigger respuesta_carga_borrar_unidades_huerfanas_del
  after delete on respuesta_carga
  referencing old table as viejas
  for each statement execute function borrar_unidades_huerfanas();

drop trigger if exists respuesta_carga_borrar_unidades_huerfanas_upd on respuesta_carga;
create trigger respuesta_carga_borrar_unidades_huerfanas_upd
  after update on respuesta_carga
  referencing old table as viejas
  for each statement execute function borrar_unidades_huerfanas();

-- El historial de lo que ya está: cada respuesta vigente, en la carga que la
-- escribió por última vez. Lo anterior no se guardó y no se inventa.
insert into respuesta_carga
       (individuo_id, pregunta_id, carga_id, valor_texto, hash_texto, unidad_id)
select r.individuo_id, r.pregunta_id, r.carga_id, r.valor_texto, r.hash_texto,
       r.unidad_id
  from respuesta r
 where r.carga_id is not null
on conflict (individuo_id, pregunta_id, carga_id) do nothing;

commit;
