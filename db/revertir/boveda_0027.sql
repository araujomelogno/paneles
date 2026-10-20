-- ============================================================
--  Revertir boveda/0027 · temáticas incorporadas de cada carga
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`: nada la aplica
-- solo. Se corre **solo después** de volver a desplegar el código anterior:
-- el código nuevo lee `tematica` y `carga_tematica`.
--
-- **Pierde datos, a propósito y sin vuelta:** el vocabulario de temáticas y
-- qué carga cubre cuál. Si hay alguna chance de volver a la 0027,
-- exportarlos antes:
--
--   \copy (select c.id as carga_id, c.nombre as carga, t.nombre as tematica,
--                 ct.origen, ct.creado_en
--            from carga_tematica ct
--            join carga c on c.id = ct.carga_id
--            join tematica t on t.id = ct.tematica_id
--           order by c.id, t.nombre)
--     to 'carga_tematica.csv' csv header
--
-- Va en una transacción: si algo falla, no cambia nada.

begin;

drop table if exists carga_tematica;
drop table if exists tematica;

commit;
