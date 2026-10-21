-- ============================================================
--  Revertir boveda/0027 · temáticas de cada carga y de cada panelista
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`: nada la aplica
-- solo. Se corre **solo después** de volver a desplegar el código anterior:
-- el código nuevo lee `tematica`, `carga_tematica`, `tematica_pregunta` y
-- `persona_tematica`.
--
-- **Pierde datos, a propósito y sin vuelta:** el vocabulario de temáticas,
-- qué carga cubre cuál, qué preguntas sustentan cada una y las temáticas de
-- cada persona (estas últimas se pueden volver a calcular desde el store
-- semántico guardando de nuevo las temáticas de cada carga). Si hay alguna
-- chance de volver a la 0027, exportar antes lo que no se recalcula:
--
--   \copy (select c.id as carga_id, c.nombre as carga, t.nombre as tematica,
--                 t.es_especial, ct.origen, ct.creado_en,
--                 (select string_agg(tp.codigo_pregunta, ' ')
--                    from tematica_pregunta tp
--                   where tp.carga_id = ct.carga_id
--                     and tp.tematica_id = ct.tematica_id) as preguntas
--            from carga_tematica ct
--            join carga c on c.id = ct.carga_id
--            join tematica t on t.id = ct.tematica_id
--           order by c.id, t.nombre)
--     to 'carga_tematica.csv' csv header
--
-- Va en una transacción: si algo falla, no cambia nada.

begin;

drop table if exists persona_tematica;
drop table if exists tematica_pregunta;
drop table if exists carga_tematica;
drop table if exists tematica;

commit;
