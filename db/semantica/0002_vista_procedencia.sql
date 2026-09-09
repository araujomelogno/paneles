-- ============================================================
--  STORE SEMÁNTICO — Migración 0002
--  Vista de procedencia: qué estudio y qué pregunta originaron
--  cada respuesta.
--
--  `respuesta` no lleva una columna de estudio a propósito: la
--  procedencia ya está determinada por `pregunta_id`, porque cada
--  `pregunta` pertenece a un solo `cuestionario`. Guardarla también en
--  `respuesta` sería redundante y abriría la puerta a que las dos
--  copias no coincidan.
--
--  Pero el camino son dos saltos (respuesta → pregunta → cuestionario)
--  y hace falta en dos lugares del producto:
--
--  * P0 del módulo semántico: «ver las respuestas relevantes de cada
--    individuo del ranking, con su procedencia (estudio y pregunta)».
--  * R4.1: seguir al mismo `id_persona` a través de olas.
--
--  Esta vista deja ese join escrito una vez.
--
--  Nota sobre nombres: la columna del nombre del estudio se llama
--  `estudio`, no `nombre`. La auditoría de PII revisa los nombres de
--  columna del store semántico contra una lista de campos
--  identificatorios, y `nombre` solo está permitido en las tablas
--  `cuestionario` y `pregunta`. En una vista daría un falso positivo.
-- ============================================================

create view v_respuesta_estudio as
select r.id             as respuesta_id,
       i.id_persona,                      -- token opaco; no es PII
       c.ref_estudio,                     -- puente con `encuesta` de la bóveda
       c.nombre         as estudio,
       c.fecha_campo,
       p.codigo         as pregunta_codigo,
       p.texto          as pregunta_texto,
       p.tipo           as pregunta_tipo,
       r.valor_texto,
       r.texto_embebido,
       r.embedding
  from respuesta r
  join individuo i    on i.id = r.individuo_id
  join pregunta p     on p.id = r.pregunta_id
  join cuestionario c on c.id = p.cuestionario_id;

comment on view v_respuesta_estudio is
  'Respuestas con su procedencia (estudio y pregunta) resuelta. Sin PII: la '
  'persona figura solo por su id_persona opaco.';
