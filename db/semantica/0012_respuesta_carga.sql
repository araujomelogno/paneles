-- ============================================================
--  STORE SEMÁNTICO — Migración 0012
--  Qué carga escribió cada respuesta por última vez
--  (specs/SPEC_encuestas_y_cargas_unificadas.md, R-EU.3 y R-EU.10, D81).
-- ============================================================
--
-- Desde la 0032 de la bóveda una encuesta tiene una o más cargas, y todas
-- escriben en el mismo cuestionario: una persona tiene una sola respuesta
-- por pregunta y gana la última carga. Para poder borrar **una** carga sin
-- tocar lo que escribieron las otras hace falta saber cuál la escribió por
-- última vez. Es un entero —el id de la carga en la bóveda—, sin FK (los
-- stores no se referencian) y sin PII.
--
-- El número 0011 lo tiene `posteriores/0011_quitar_embedding_de_respuesta`,
-- que se aplica a mano: por eso esta es la 0012.
--
-- Aditiva. La llena `scripts/migrar_encuestas_y_cargas.py semantico` para
-- lo que ya estaba (cada cuestionario tenía una sola carga); lo nuevo lo
-- escribe la ingesta. Nula = escrita antes de saberlo.
begin;

alter table respuesta add column if not exists carga_id bigint;

comment on column respuesta.carga_id is
  'R-EU.3 — la carga (bóveda) que escribió esta respuesta por última vez. '
  'Borrar esa carga borra solo las respuestas que tienen su id (R-EU.10).';

create index if not exists respuesta_por_carga on respuesta (carga_id)
  where carga_id is not null;

commit;
