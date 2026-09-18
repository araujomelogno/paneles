-- R4.1.b — Series comparables entre olas.
--
-- Comparar «la misma pregunta» entre dos olas es difícil porque cada
-- cuestionario la redacta distinto, y este sistema **no canoniza respuestas**
-- por decisión de diseño: canonizar automáticamente congelaría una
-- equivalencia que puede ser falsa —dos preguntas parecidas que miden cosas
-- distintas— y lo haría en el dato, donde ya no se ve.
--
-- La salida es que la comparabilidad la **declare el analista**, por caso y
-- explícitamente. Eso la hace revisable y la pone donde corresponde: en quien
-- sabe qué se preguntó y para qué. El sistema sugiere candidatas por
-- similitud, pero no agrega ninguna solo.
--
-- ---------- Por qué las series viven de este lado ------------------------
-- Una serie agrupa preguntas y mapea opciones: es contenido, y el contenido
-- vive en el store semántico. Lo que **no** vive acá es quién la creó o la
-- editó: eso es una persona, y la regla dura del sistema es que ninguna
-- persona se escribe de este lado. La auditoría de quién tocó una serie va a
-- `serie_auditoria`, en la bóveda, junto a los otros dos rastros de acciones
-- de personal.

create table serie (
  id          bigint generated always as identity primary key,
  -- La clave es el identificador estable con el que se la referencia desde
  -- afuera; el nombre es lo que se muestra y se puede corregir.
  clave       text not null,
  nombre      text not null,
  descripcion text,
  activa      boolean not null default true,
  creada_en   timestamptz not null default now()
);
create unique index serie_clave_unica on serie (clave);

-- Las categorías comunes a las que se mapean las opciones de cada pregunta.
-- Es el mismo mecanismo que el catálogo de atributos de la bóveda, por el
-- mismo motivo: sin un vocabulario común no hay dos olas que se puedan
-- comparar.
create table serie_categoria (
  id       bigint generated always as identity primary key,
  serie_id bigint not null references serie(id) on delete cascade,
  clave    text not null,
  etiqueta text not null,
  orden    int not null default 100
);
create unique index serie_categoria_unica on serie_categoria (serie_id, clave);

create table serie_pregunta (
  id          bigint generated always as identity primary key,
  serie_id    bigint not null references serie(id) on delete cascade,
  pregunta_id bigint not null references pregunta(id) on delete cascade,
  -- `declarada` = la agregó el analista. `sugerida_aceptada` = el sistema la
  -- propuso y el analista la aceptó. Distinguirlas importa: si más adelante
  -- una serie resulta estar mal armada, saber cuáles entraron por sugerencia
  -- dice si el problema fue el criterio o la herramienta.
  origen      text not null default 'declarada'
              check (origen in ('declarada','sugerida_aceptada')),
  agregada_en timestamptz not null default now()
);
create unique index serie_pregunta_unica
    on serie_pregunta (serie_id, pregunta_id);
-- Una pregunta puede estar en varias series (una pregunta de confianza en
-- «confianza institucional» y en «clima de opinión»), pero no dos veces en la
-- misma. El índice de arriba ya lo garantiza.
create index serie_pregunta_por_pregunta on serie_pregunta (pregunta_id);

-- El mapeo de las opciones de una pregunta cerrada a las categorías comunes
-- de la serie. Una opción sin mapear no se cuenta en la comparación: igual
-- que «sin dato» en la composición, meterla en una categoría real haría que
-- el movimiento entre olas mienta.
create table serie_mapeo (
  id                bigint generated always as identity primary key,
  serie_pregunta_id bigint not null
                    references serie_pregunta(id) on delete cascade,
  -- La clave de la opción tal como está en `pregunta.opciones`.
  opcion            text not null,
  categoria_id      bigint references serie_categoria(id) on delete cascade
);
create unique index serie_mapeo_unico on serie_mapeo (serie_pregunta_id, opcion);

-- El texto de la pregunta, embebido. Hasta ahora solo se embebían las
-- respuestas (`pregunta -> respuesta`), que sirve para buscar qué contestó la
-- gente pero no para preguntarse qué preguntas se parecen entre sí: dos olas
-- pueden preguntar lo mismo y recibir respuestas opuestas. Se llena en forma
-- perezosa, la primera vez que se piden sugerencias, y queda cacheado.
alter table pregunta add column embedding_texto vector(1024);

comment on table serie is
  'R4.1.b — agrupación declarada de preguntas de distintas olas como la misma '
  'medición. La declara el analista; el sistema sugiere pero no agrega solo.';
comment on column pregunta.embedding_texto is
  'R4.1.b — el texto de la pregunta embebido, para sugerir candidatas de otras '
  'olas. Se llena la primera vez que se piden sugerencias.';
