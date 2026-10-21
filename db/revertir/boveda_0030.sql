-- ============================================================
--  Revertir boveda/0030 · borrar una carga
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`. Se corre
-- **después** de volver a desplegar el código anterior, que no conoce el
-- borrado de cargas.
--
-- **Pierde la auditoría de los borrados hechos**: quién borró qué carga y
-- con qué conteos. Lo borrado no vuelve. Si hay alguna chance de necesitar
-- ese registro, exportarlo antes:
--
--   \copy (select * from carga_borrado order by id) to 'carga_borrado.csv' csv header
--
-- Un borrado en curso al revertir queda a medias: terminarlo antes.

begin;

drop table if exists carga_borrado;

commit;
