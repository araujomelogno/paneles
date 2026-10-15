-- ============================================================
--  Revertir boveda/0026 · tipo de datos y modo de relevamiento de la carga
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`: nada la aplica
-- solo. Se corre **solo después** de volver a desplegar el código anterior:
-- el código nuevo lee estas columnas de `carga` y `v_carga_resumen`.
--
-- **Pierde datos, a propósito y sin vuelta:** el tipo y el modo declarados
-- en cada carga. Si hay alguna chance de volver a la 0026, exportarlos antes:
--
--   \copy (select id, tipo_datos_origen, tipo_datos_origen_otro,
--                 modo_relevamiento, modo_relevamiento_otro from carga)
--     to 'carga_tipo_y_modo.csv' csv header
--
-- Va en una transacción: si algo falla, no cambia nada.

begin;

-- La vista depende de las columnas: se rehace como la dejó la 0024 antes de
-- tirarlas. Un `create or replace` no puede quitar columnas, por eso `drop`.
drop view if exists v_carga_resumen;
create view v_carga_resumen as
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
revoke all on v_carga_resumen from public;

alter table carga drop constraint if exists carga_modo_relevamiento_otro_check;
alter table carga drop constraint if exists carga_modo_relevamiento_check;
alter table carga drop constraint if exists carga_tipo_datos_origen_otro_check;
alter table carga drop constraint if exists carga_tipo_datos_origen_check;
alter table carga drop column if exists modo_relevamiento_otro;
alter table carga drop column if exists modo_relevamiento;
alter table carga drop column if exists tipo_datos_origen_otro;
alter table carga drop column if exists tipo_datos_origen;

commit;
