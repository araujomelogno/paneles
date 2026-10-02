-- Los embeddings pasan de 1024 a 512 dimensiones.
--
-- ============================================================
--  Por qué, y por qué ahora
-- ============================================================
-- La mitad de vector es la mitad de instancia. Con las 200.000 respuestas que
-- se van a cargar:
--
--   · 1024 dims → ~820 MB de vectores, ~1,5–2 GB con el índice HNSW. No entra
--     en la instancia chica; hace falta una custom de ~4 GB (~US$ 50–70/mes).
--   · 512 dims  → ~410 MB, ~0,8–1 GB con el índice. Entra en `db-g1-small`
--     (~US$ 34/mes).
--
-- Son ~US$ 20–35 por mes, para siempre. Y lo que se resigna son uno o dos
-- puntos de calidad de recuperación según los benchmarks de Voyage: truncar
-- no es cortar al azar, porque `voyage-3.5` está entrenado con *Matryoshka* y
-- las primeras dimensiones concentran la mayor parte de la información.
--
-- **Ahora y no después** porque el corpus todavía es de una respuesta.
-- Re-embeber hoy cuesta una llamada; después de cargar 200.000, cuesta
-- re-procesar el corpus entero. Ésta es la ventana.
--
-- ============================================================
--  Esta migración destruye embeddings. Es su naturaleza.
-- ============================================================
-- Un vector de 1024 no se convierte a 512 desde SQL: habría que truncarlo y
-- renormalizarlo, y el resultado no sería el que devuelve el modelo. Lo
-- correcto es re-embeber, que es trabajo de la ingesta y no de una migración.
--
-- Por eso acá las filas de `respuesta` **se borran**, y no se les deja el
-- vector en null. Dos razones:
--
--   1 · `respuesta.embedding` es `not null`, y agregar una columna `not null`
--       a una tabla con filas no se puede.
--   2 · Una respuesta sin embedding es invisible para la búsqueda pero cuenta
--       en los totales: es exactamente el tipo de estado a medias que después
--       nadie nota. Borrarla hace que `count(*)` diga la verdad y que la
--       re-ingesta sea la única salida.
--
-- Borrar también resuelve, de paso, el problema que el salteo por hash
-- traería: `hashes_de_estudio()` no encuentra nada, así que la re-ingesta
-- embebe todo en vez de reutilizar vectores de la dimensión vieja.

-- ============================================================
--  1 · La guarda
-- ============================================================
-- Si esta migración llega tarde —con el corpus ya cargado— borrar en silencio
-- sería destruir trabajo de verdad. Aborta, y dice cómo seguir.
do $$
declare
  cuantas  bigint;
  actual   int;
  -- Hasta acá es «el corpus de prueba». Más que esto ya es una carga real.
  tope     constant bigint := 1000;
  forzado  boolean := coalesce(
              current_setting('paneles.rehacer_embeddings', true), '') = 'si';
begin
  -- Primero: ¿ya está aplicada? Esta pregunta va **antes** que ninguna otra
  -- porque lo que sigue borra. Una migración que destruye datos en una
  -- segunda corrida accidental es una trampa, y acá la segunda corrida es
  -- probable: `scripts/pg_pruebas.sh` reaplica todas las migraciones cada
  -- vez que se lo invoca, ignorando los errores.
  select atttypmod into actual
    from pg_attribute
   where attrelid = 'respuesta'::regclass and attname = 'embedding';
  if actual = 512 then
    raise exception
      E'`respuesta.embedding` ya está en 512 dimensiones: esta migración ya '
       'se aplicó.\nNo se tocó nada. Si lo que querés es re-embeber el '
       'corpus, eso es una re-ingesta, no una migración.'
      using errcode = 'raise_exception';
  end if;

  select count(*) into cuantas from respuesta;
  if cuantas > tope and not forzado then
    raise exception
      E'Hay % respuestas embebidas y esta migración las borra para poder '
       'recrear la columna en 512 dimensiones.\n'
       'Re-embeberlas cuesta una llamada al proveedor por respuesta.\n\n'
       'Si de verdad querés seguir, volvé a correrla con:\n'
       '  psql ... -c "set paneles.rehacer_embeddings = ''si''" '
       '-f db/semantica/0006_embeddings_512.sql\n'
       'y anotá qué estudios hay que re-ingestar '
       '(select distinct ref_estudio from v_respuesta_estudio).',
      cuantas
      using errcode = 'raise_exception';
  end if;
  raise notice 'Se borran % respuestas para re-embeberlas en 512 dimensiones.',
    cuantas;
end $$;

-- ============================================================
--  2 · Las columnas
-- ============================================================
-- Dos cosas dependen de `respuesta.embedding` y hay que soltarlas antes:
--
--   · El índice HNSW, que se rehace al final. Va en la misma transacción
--     que el resto: una migración que deja la tabla sin índice no se nota
--     hasta que una consulta tarda un minuto.
--   · **`v_respuesta_estudio`** (la 0002), que selecciona la columna. Es la
--     que hace fallar un `drop column` suelto con «other objects depend on
--     it», y por eso se recrea abajo **idéntica**: si cambiara, cambiaría el
--     contrato de la procedencia, que no es lo que esta migración viene a
--     hacer.
drop index if exists respuesta_embedding_idx;
drop view  if exists v_respuesta_estudio;

delete from respuesta;

alter table respuesta drop column embedding;
alter table respuesta add  column embedding vector(512) not null;

comment on column respuesta.embedding is
  'El vector de `texto_embebido`, en 512 dimensiones. Tiene que coincidir con '
  '`EMBEDDINGS_DIMS`: con otra dimensión el insert falla, y la ingesta lo '
  'comprueba antes de mandar nada a embeber.';

-- La de R4.1.b, que se llena en forma perezosa la primera vez que se piden
-- sugerencias de serie. Es nullable, así que alcanza con recrearla: lo que
-- hubiera cacheado se vuelve a calcular solo.
alter table pregunta drop column if exists embedding_texto;
alter table pregunta add  column embedding_texto vector(512);

comment on column pregunta.embedding_texto is
  'R4.1.b — el texto de la pregunta embebido, para sugerir candidatas de '
  'otras olas. 512 dimensiones, igual que `respuesta.embedding`. Se llena la '
  'primera vez que se piden sugerencias.';

-- El nombre del índice no es el que Postgres le habría puesto solo
-- (`respuesta_embedding_idx`), es el mismo: la 0001 lo creó sin nombrarlo y
-- Postgres lo llamó así. Se nombra explícito para que el `drop` de arriba
-- siga encontrándolo si alguna vez cambia la convención.
create index respuesta_embedding_idx
    on respuesta using hnsw (embedding vector_cosine_ops);

-- Y la vista de procedencia, tal cual estaba en la 0002. Se repite entera y
-- no se «parcha» porque una vista no se altera: se reemplaza, y la copia
-- tiene que ser la misma o el cambio sería silencioso.
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

-- ============================================================
--  3 · Qué dimensión tiene cada columna, consultable
-- ============================================================
-- Hace falta por dos razones, y la segunda es la que obliga a que sea un
-- objeto y no una consulta suelta:
--
--   · El diagnóstico compara la dimensión del proveedor con la de la columna.
--     Si no coinciden, cada ingesta falla al insertar **después** de haber
--     pagado el embedding del lote. Con esta vista, se detecta antes.
--   · **Una migración que solo cambia un tipo es invisible para
--     `verificar_esquema.py`**: la columna `respuesta.embedding` existe desde
--     la 0001, así que preguntarle a la base «¿está?» diría que sí con la
--     0006 sin aplicar. Es la misma lección de la 0015 en la bóveda (D45).
--     Esta vista es lo que hace detectable a esta migración.
-- Sin `drop ... if exists` delante, a propósito: esta vista es **nueva**, y
-- un drop defensivo la haría parecer un reemplazo. La guarda de arriba ya
-- impide la segunda corrida, que es de lo único que ese drop protegería.
create view v_dimension_embeddings as
select
  c.relname::text || '.' || a.attname::text as columna,
  -- Para `vector`, `atttypmod` **es** la dimensión. No lleva el «+4» que sí
  -- llevan los tipos de largo variable como `varchar`.
  a.atttypmod                                as dimension,
  format_type(a.atttypid, a.atttypmod)       as tipo
  from pg_attribute a
  join pg_class     c on c.oid = a.attrelid
  join pg_namespace n on n.oid = c.relnamespace
  join pg_type      t on t.oid = a.atttypid
 where n.nspname = 'public'
   and t.typname = 'vector'
   -- Solo tablas de verdad. Sin esto aparecen también la columna del índice
   -- HNSW y la de `v_respuesta_estudio`, que son la misma dimensión vista
   -- tres veces y convierten una respuesta de dos filas en una de cuatro.
   and c.relkind = 'r'
   and a.attnum > 0
   and not a.attisdropped;

comment on view v_dimension_embeddings is
  'En cuántas dimensiones está cada columna vectorial del store. La lee el '
  'diagnóstico para comparar contra `EMBEDDINGS_DIMS`: si no coinciden, la '
  'ingesta falla al insertar y el embedding ya se pagó.';
