-- 0037 · Cargas provisorias, detener una ingesta y cargas incrementales
-- (D92 a D94: `specs/PEDIDO_cancelar_carga.md`,
-- `specs/PEDIDO_carga_mixta.md`, `specs/PEDIDO_cargas_incrementales.md`).
--
-- Tres cosas, en una sola versión:
--
--   1. La carga nace **provisoria** (D92, R-CAN-C.1): se crea en el primer
--      paso del flujo —antes de subir el archivo— y deja de serlo en la misma
--      transacción que crea el trabajo de ingesta. La encuesta automática que
--      crea una carga desde Panelistas también. Lo provisorio no aparece en
--      ningún listado; cancelar lo borra y lo abandonado se limpia a las 24 h
--      (`limpieza_provisoria`, el registro de cada limpieza).
--   2. Una ingesta se puede **detener** (D92, R-CAN-C.4): el trabajo pasa a
--      `detenida` y sus lotes pendientes a `cancelado`. Una detenida no es
--      activa: el índice único de la `0029` mira solo `encolada` y
--      `procesando`, así que no bloquea una ingesta nueva del mismo destino.
--   3. Un tercer origen del vínculo persona ↔ carga, **`repetida`** (D94,
--      R-INC.1): la persona ya estaba en una carga anterior de la misma
--      encuesta. Una encuesta de Alchemer se carga varias veces y cada SAV
--      trae el acumulado.
--
-- La carga mixta (D93) no necesita esquema: el ID de respuesta se registra en
-- `alias_origen`, con un origen propio de la encuesta.
--
-- **Cambia datos** (el punto 3). La corrida de prueba es
-- `scripts/migrar_cargas_incrementales.py prueba`: ejecuta este mismo archivo
-- en una transacción que se deshace y dice, por encuesta, cuántos vínculos
-- pasan a `repetida`. Las cargas y encuestas que ya existen quedan con
-- `provisoria = false`: no hay forma de saber cuáles se abandonaron y cuáles
-- se crearon a propósito (el despliegue las lista para que el equipo decida).
-- Re-ejecutable.

begin;

-- ---------- 1 · Lo provisorio -----------------------------------------

alter table carga    add column if not exists provisoria boolean not null default false;
alter table encuesta add column if not exists provisoria boolean not null default false;

comment on column carga.provisoria is
  'D92 · R-CAN-C.1 — la carga se creó en el primer paso del flujo y todavía '
  'no se confirmó su ingesta. No aparece en ningún listado; cancelar la '
  'borra, y abandonada más de 24 h sin trabajo la borra la limpieza. Deja de '
  'serlo en la misma transacción que crea el trabajo de ingesta.';
comment on column encuesta.provisoria is
  'D92 · R-CAN-C.1 — la encuesta automática de una carga provisoria creada '
  'desde Panelistas. Una encuesta existente a la que se le suma una carga '
  'nunca se vuelve provisoria.';

-- La limpieza busca las provisorias viejas; son pocas, y el índice parcial
-- no pesa nada sobre las demás.
create index if not exists carga_provisoria_idx
    on carga (creado_en) where provisoria;
create index if not exists encuesta_provisoria_idx
    on encuesta (creado_en) where provisoria;

-- El registro de cada limpieza (R-CAN-C.3): cuándo corrió, quién o qué la
-- disparó y cuántas borró. Una fila por corrida, también cuando no borró
-- nada: «corrió y no había» es un dato distinto de «no corrió».
create table if not exists limpieza_provisoria (
  id                   bigint generated always as identity primary key,
  ejecutada_en         timestamptz not null default now(),
  disparada_por        text not null
                         check (disparada_por in ('inicio_de_carga', 'manual')),
  actor                text,
  cargas_eliminadas    int not null default 0,
  encuestas_eliminadas int not null default 0
);

comment on table limpieza_provisoria is
  'D92 · R-CAN-C.3 — cada corrida de la limpieza de cargas provisorias '
  'abandonadas (más de 24 h y sin trabajo de ingesta). Corre al iniciar cada '
  'carga y a mano desde Cumplimiento; sin un proceso programado.';

alter table limpieza_provisoria enable row level security;

-- ---------- 2 · Detener una ingesta ------------------------------------

insert into estado_ingesta (codigo, etiqueta, descripcion, terminal, orden) values
  ('detenida', 'Detenida',
   'Alguien la detuvo: los lotes pendientes quedaron cancelados y no se '
   'procesan. Lo que entró, entró. Se puede reanudar o borrar lo cargado. No '
   'bloquea una ingesta nueva del mismo destino.', true, 45)
on conflict (codigo) do nothing;

insert into estado_lote_ingesta (codigo, etiqueta, descripcion, orden) values
  ('cancelado', 'Cancelado',
   'La ingesta se detuvo antes de que lo tomara una tarea. No se procesa; '
   'reanudar lo vuelve a encolar.', 50)
on conflict (codigo) do nothing;

-- El avance suma los cancelados, al final (`create or replace` admite
-- columnas nuevas solo al final).
create or replace view v_ingesta_progreso as
select
  t.id                     as trabajo_id,
  t.destino_tipo, t.destino_id,
  t.estado,
  e.etiqueta               as estado_etiqueta,
  e.terminal,
  t.lotes_total,
  t.filas_total,
  count(l.id) filter (where l.estado = 'ok')         as lotes_ok,
  count(l.id) filter (where l.estado = 'fallido')    as lotes_fallidos,
  count(l.id) filter (where l.estado = 'procesando') as lotes_en_curso,
  count(l.id) filter (where l.estado = 'pendiente')  as lotes_pendientes,
  coalesce(sum(l.filas_total) filter (where l.estado = 'ok'), 0)::bigint
                                                     as filas_procesadas,
  t.creado_en, t.terminado_en,
  extract(epoch from (coalesce(t.terminado_en, now()) - t.creado_en))::int
                                                     as segundos_transcurridos,
  count(l.id) filter (where l.estado = 'cancelado')  as lotes_cancelados
  from ingesta_trabajo t
  join estado_ingesta e on e.codigo = t.estado
  left join ingesta_lote l on l.trabajo_id = t.id
 group by t.id, e.etiqueta, e.terminal;

-- La purga no se lleva las filas de un lote cancelado: son las que harían
-- falta para reanudar.
create or replace function purgar_ingestas_terminadas(dias int default 7)
returns int language plpgsql as $$
declare
  liberados int;
begin
  update ingesta_lote l
     set filas = null
    from ingesta_trabajo t
   where t.id = l.trabajo_id
     and l.filas is not null
     and t.terminado_en is not null
     and t.terminado_en < now() - make_interval(days => dias)
     and l.estado not in ('fallido', 'cancelado');
  get diagnostics liberados = row_count;
  return liberados;
end;
$$;

revoke execute on function purgar_ingestas_terminadas(int) from public;

-- ---------- 3 · `repetida`: la misma persona en otra carga de la encuesta -

alter table persona_carga drop constraint if exists persona_carga_origen_check;
alter table persona_carga add constraint persona_carga_origen_check
  check (origen in ('creada', 'reutilizada', 'repetida'));

comment on column persona_carga.origen is
  'creada: la carga dio de alta a la persona · reutilizada: ya existía, por '
  'otro estudio o alta · repetida (D94): ya estaba en una carga anterior de '
  'esta misma encuesta (una encuesta incremental trae el acumulado). Dentro '
  'de una encuesta, cada persona tiene a lo sumo un vínculo que no es '
  'repetida: el de su primera carga.';

-- 3.a · Lo ya cargado (R-INC.2): en cada encuesta, el vínculo con la primera
-- carga —por orden de creación de la carga— conserva su origen; los de las
-- siguientes pasan a `repetida`. Re-ejecutable: lo que ya es repetida no se
-- toca, y la primera nunca cambia.
with ordenados as (
  select pc.id_persona, pc.carga_id,
         row_number() over (partition by pc.id_persona, c.encuesta_id
                            order by c.creado_en, c.id) as n
    from persona_carga pc
    join carga c on c.id = pc.carga_id
)
update persona_carga pc
   set origen = 'repetida'
  from ordenados o
 where o.id_persona = pc.id_persona and o.carga_id = pc.carga_id
   and o.n > 1
   and pc.origen <> 'repetida';

-- ---------- 4 · Cada carga con sus tres orígenes -----------------------

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
       count(pc.id_persona)::int                        as personas,
       count(pc.id_persona) filter (where pc.origen = 'repetida')::int
                                                        as personas_repetidas,
       c.provisoria
  from carga c
  join encuesta e on e.id = c.encuesta_id
  left join persona_carga pc on pc.carga_id = c.id
 group by c.id, e.id;

comment on view v_carga_resumen is
  'R-ORG.5 / R-EU.2 / R-INC.5 — cada carga con los datos de su estudio (de '
  'la encuesta) y cuántas personas creó, reutilizó de otros estudios y '
  'ya estaban en cargas anteriores de la encuesta (repetidas).';

revoke all on v_carga_resumen, limpieza_provisoria from public;

commit;
