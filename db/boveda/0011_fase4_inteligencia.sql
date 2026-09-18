-- Fase 4 · Bloque 4B — lo que del análisis longitudinal y del optimizador va
-- en la bóveda. Todo aditivo.
--
-- Son dos cosas que no tienen nada que ver entre sí salvo que las dos son de
-- este lado: la auditoría de las series (R4.1.b) y los pesos del optimizador
-- de muestreo (R4.2).

-- ============================================================
--  1 · R4.1.b — auditoría de las series
-- ============================================================
-- Las series viven en el store semántico, porque agrupar preguntas y mapear
-- opciones es contenido. Quién las creó o las editó, no: eso es una persona,
-- y ninguna persona se escribe del lado semántico. Así que el rastro va acá,
-- junto a los otros dos rastros de acciones de personal (`usuario_auditoria`
-- y `atributo_auditoria`).
--
-- No se reutiliza `atributo_auditoria`: esa tabla tiene una FK a
-- `atributo_demografico` y habla del vocabulario de segmentación. Una serie
-- es otro vocabulario, vive en otra base y se revisa por otro motivo.
create table serie_auditoria (
  id          bigint generated always as identity primary key,
  -- No hay FK: la serie vive en el otro store. Es la misma clase de
  -- referencia lógica que `ref_estudio`.
  serie_clave text not null,
  accion      text not null
                check (accion in ('alta','edicion','baja','pregunta_agregada',
                                  'pregunta_quitada','mapeo','categoria')),
  detalle     jsonb not null default '{}'::jsonb,
  actor_uid   text,
  actor_email text,
  creado_en   timestamptz not null default now()
);
create index serie_auditoria_por_serie
    on serie_auditoria (serie_clave, creado_en desc);

comment on table serie_auditoria is
  'R4.1.b — quién tocó una serie y cuándo. La serie vive en el store '
  'semántico; el nombre de quien la editó, nunca.';

-- ============================================================
--  2 · R4.2 — pesos del optimizador de muestreo
-- ============================================================
-- Las reglas de R3.1 priorizan brechas y excluyen sobre-convocados, y eso
-- alcanza cuando hay holgura. Cuando no la hay, cerrar la cuota y cuidar a la
-- gente tiran para lados opuestos y hay que **negociar**: por eso lo blando
-- se penaliza en vez de prohibirse, y por eso los pesos son configurables.
--
-- Son por panel y no globales porque la tensión es distinta en cada uno: un
-- panel chico y muy usado necesita pesar la fatiga mucho más que uno grande.
-- Un panel sin fila configurada usa los defaults, que son los de la columna.
create table peso_optimizador (
  panel_id            bigint primary key references panel(id) on delete cascade,
  -- Cuánto cuesta convocar a alguien que ya viene siendo convocado en la
  -- ventana reciente. Es lo que evita que la muestra recaiga siempre sobre
  -- los mismos, que es como se queman los paneles.
  peso_fatiga         numeric not null default 1.0 check (peso_fatiga >= 0),
  -- Cuánto cuesta el desbalance en el reparto entre los elegibles de un mismo
  -- segmento. Distinto de la fatiga: alguien puede estar lejos del umbral y
  -- aun así ser siempre el elegido de su celda.
  peso_equidad        numeric not null default 0.5 check (peso_equidad >= 0),
  -- Cuánto vale cerrar la brecha de cuota. Es la escala contra la que se leen
  -- los otros dos: subirlo es decir «cerrá la cuota aunque duela».
  peso_brecha         numeric not null default 10.0 check (peso_brecha >= 0),
  actualizado_por     text,
  actualizado_en      timestamptz not null default now()
);

comment on table peso_optimizador is
  'R4.2 — cuánto pesa cada restricción blanda al optimizar una muestra. Por '
  'panel: la tensión entre cerrar cuota y no quemar gente es distinta en cada uno.';
