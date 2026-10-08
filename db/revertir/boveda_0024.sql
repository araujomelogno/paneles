-- ============================================================
--  Revertir boveda/0024 · R-ORG (origen de cada persona y ámbitos)
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`: nada la aplica
-- solo. Es el rollback de `DESPLIEGUE - R-ORG, paridad demográfica y API key
-- web.md`, y se corre **solo después** de volver a desplegar el código
-- anterior: el código nuevo lee `persona_carga`, `v_carga_resumen` y
-- `objetivo_composicion.ambito`, y sin ellos la ficha y la composición dan
-- «esquema desactualizado».
--
-- **Pierde datos, a propósito y sin vuelta:** los vínculos persona ↔ carga
-- registrados desde la 0024, la fecha y el público objetivo de cada carga y
-- el universo de referencia de «todos los panelistas». Si hay alguna chance
-- de volver a la 0024, exportarlos antes (el documento de despliegue dice
-- cómo). Los objetivos de cada panel no se tocan.
--
-- Va en una transacción: si algo falla, no cambia nada.

begin;

drop view if exists v_carga_resumen;
drop table if exists persona_carga;

-- Los objetivos de «todos» no tienen panel: con `panel_id not null` de
-- vuelta no podrían existir.
delete from objetivo_composicion where ambito = 'todos';
drop index if exists objetivo_composicion_todos_unico;
alter table objetivo_composicion drop constraint if exists objetivo_composicion_ambito_panel;
alter table objetivo_composicion drop constraint if exists objetivo_composicion_ambito_check;
alter table objetivo_composicion alter column panel_id set not null;
alter table objetivo_composicion drop column if exists ambito;

alter table carga drop column if exists publico_objetivo;
alter table carga drop column if exists fecha_estudio;

commit;
