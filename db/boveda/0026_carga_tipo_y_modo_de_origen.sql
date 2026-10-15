-- ============================================================
--  STORE DE BÓVEDA — Migración 0026
--  Tipo de datos y modo de relevamiento de cada carga.
-- ============================================================
--
-- Dos datos más del estudio del que sale una carga, con vocabulario cerrado
-- y una salida «otro» que obliga a especificar:
--
--   · tipo_datos_origen   — encuesta · reclutamiento · otro
--   · modo_relevamiento   — cara_a_cara · telefonico · whatsapp · web · otro
--
-- Igual que `fecha_estudio` y `publico_objetivo` (0024), viven **en la
-- carga** y llegan a cada panelista por el vínculo `persona_carga`: no se
-- copian en `persona`. Corregir el modo de una carga es una fila y se ve en
-- todas las fichas que provienen de ella.
--
-- Son nulables: las cargas anteriores no los declararon y no se les inventa
-- un valor. Lo que exige declararlos es la ruta que abre una carga nueva.
--
-- La especificación de «otro» va en su propia columna, y un check impide las
-- dos incoherencias: «otro» sin especificar, y una especificación colgada de
-- un valor que no es «otro» (quedaría invisible y mentiría al leerse).
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

alter table carga add column if not exists tipo_datos_origen text;
alter table carga add column if not exists tipo_datos_origen_otro text;
alter table carga add column if not exists modo_relevamiento text;
alter table carga add column if not exists modo_relevamiento_otro text;

do $$
begin
  if not exists (select 1 from pg_constraint
                  where conname = 'carga_tipo_datos_origen_check') then
    alter table carga add constraint carga_tipo_datos_origen_check
      check (tipo_datos_origen in ('encuesta', 'reclutamiento', 'otro'));
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'carga_tipo_datos_origen_otro_check') then
    alter table carga add constraint carga_tipo_datos_origen_otro_check
      check (case when tipo_datos_origen = 'otro'
                  then nullif(btrim(tipo_datos_origen_otro), '') is not null
                  else tipo_datos_origen_otro is null end);
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'carga_modo_relevamiento_check') then
    alter table carga add constraint carga_modo_relevamiento_check
      check (modo_relevamiento in
             ('cara_a_cara', 'telefonico', 'whatsapp', 'web', 'otro'));
  end if;
  if not exists (select 1 from pg_constraint
                  where conname = 'carga_modo_relevamiento_otro_check') then
    alter table carga add constraint carga_modo_relevamiento_otro_check
      check (case when modo_relevamiento = 'otro'
                  then nullif(btrim(modo_relevamiento_otro), '') is not null
                  else modo_relevamiento_otro is null end);
  end if;
end $$;

comment on column carga.tipo_datos_origen is
  'Qué tipo de datos dio origen a la carga: encuesta · reclutamiento · otro '
  '(con tipo_datos_origen_otro). Nulo en las cargas anteriores a la 0026.';
comment on column carga.tipo_datos_origen_otro is
  'La especificación cuando tipo_datos_origen = otro. Nula en otro caso.';
comment on column carga.modo_relevamiento is
  'Cómo se relevaron los datos de origen: cara_a_cara · telefonico · '
  'whatsapp · web · otro (con modo_relevamiento_otro). Nulo en las cargas '
  'anteriores a la 0026.';
comment on column carga.modo_relevamiento_otro is
  'La especificación cuando modo_relevamiento = otro. Nula en otro caso.';

-- `create or replace view` solo admite columnas nuevas **al final**: por eso
-- las cuatro van después de `personas` y no junto a los otros datos del
-- estudio.
create or replace view v_carga_resumen as
select c.id, c.nombre, c.descripcion, c.ref_estudio, c.fecha_estudio,
       c.publico_objetivo, c.creado_en, c.creado_por,
       count(pc.id_persona) filter (where pc.origen = 'creada')::int
                                                        as personas_creadas,
       count(pc.id_persona) filter (where pc.origen = 'reutilizada')::int
                                                        as personas_reutilizadas,
       count(pc.id_persona)::int                        as personas,
       c.tipo_datos_origen, c.tipo_datos_origen_otro,
       c.modo_relevamiento, c.modo_relevamiento_otro
  from carga c
  left join persona_carga pc on pc.carga_id = c.id
 group by c.id;

revoke all on v_carga_resumen from public;

commit;
