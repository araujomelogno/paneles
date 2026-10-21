-- ============================================================
--  Revertir boveda/0028 · taxonomía de temáticas (áreas)
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`: nada la aplica
-- solo. Se corre **solo después** de volver a desplegar el código anterior:
-- el código nuevo lee `area_tematica`, `tematica.area_id`,
-- `taxonomia_cambio` y `v_tematica`.
--
-- **Pierde datos, a propósito y sin vuelta:** las áreas, en qué área está
-- cada temática y el registro de cambios. Las fusiones de temáticas ya
-- aplicadas **no** se deshacen: reapuntaron referencias en tablas de la 0027.
-- Si hay alguna chance de volver a la 0028, exportar antes:
--
--   \copy (select a.nombre as area, a.descripcion, a.es_especial,
--                 t.nombre as tematica
--            from tematica t left join area_tematica a on a.id = t.area_id
--           order by a.nombre nulls last, t.nombre)
--     to 'taxonomia.csv' csv header
--   \copy (select * from taxonomia_cambio order by id) to 'taxonomia_cambio.csv' csv header
--
-- Va en una transacción: si algo falla, no cambia nada.

begin;

drop view if exists v_tematica;
drop table if exists taxonomia_cambio;
drop index if exists tematica_area_idx;
alter table tematica drop column if exists area_id;
drop table if exists area_tematica;

commit;
