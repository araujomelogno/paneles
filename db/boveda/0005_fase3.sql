-- ════════════════════════════════════════════════════════════════════
--  Fase 3 · Bóveda — palancas, crecimiento y operación
--  specs/SPEC_fase3.md
--
--  Nada de esto toca el store semántico: la PII de las altas por SAV y de
--  las inscripciones públicas vive únicamente acá (CLAUDE.md, regla dura #1).
-- ════════════════════════════════════════════════════════════════════

-- ---------- R3.9 · Estado de la persona -----------------------------
-- El alta manual (R1.1) rechaza crear una persona sin consentimiento. La
-- ingesta por SAV en modo «crear individuos» entra por otra puerta, y la
-- spec deja abierta la base legal (§11, bloqueante). Hasta que esa
-- definición exista, esas altas quedan en `pendiente_consentimiento`:
-- existen en la bóveda, pero no se las puede convocar ni usar en consultas
-- semánticas. Es la opción conservadora de las dos que plantea la spec.
alter table persona
  add column estado text not null default 'activa'
    check (estado in ('activa','pendiente_consentimiento'));

comment on column persona.estado is
  'pendiente_consentimiento: creada por ingesta sin base legal registrada. '
  'Excluida de convocatoria y de uso semántico hasta regularizarse.';

-- ---------- R3.2 · Calidad de la participación ----------------------
-- `calidad_estado` y `motivo_calidad` ya existen desde 0001. Falta con qué
-- decidirlos y el rastro de quién revirtió una marca.
alter table participacion
  -- Duración de la respuesta. Nulo NO es cero: significa que el export de
  -- campo no trajo tiempos, y en ese caso el chequeo de speeder no corre y
  -- se informa. Silencio no es aprobado (R3.2).
  add column duracion_segundos int check (duracion_segundos >= 0),
  -- Qué se evaluó, qué dio, y qué no se pudo evaluar por falta de datos.
  add column calidad_detalle jsonb not null default '{}'::jsonb,
  add column calidad_evaluada_en timestamptz,
  -- Revisión humana de una marca automática (R3.2: marcar es reversible).
  add column calidad_revisada_por text,
  add column calidad_revisada_en timestamptz,
  add column calidad_motivo_revision text;

-- ---------- R3.2 · Umbrales de calidad, por estudio -----------------
-- Lo que es rápido en un cuestionario de 5 minutos no lo es en uno de 30,
-- así que el umbral no puede ser global. Nulo = usar el default del código,
-- que está documentado en calidad.py.
alter table encuesta
  add column umbral_speeder_segundos int check (umbral_speeder_segundos > 0),
  add column umbral_straightliner numeric check (umbral_straightliner >= 0),
  -- R3.4 — cuántos puntos otorga una participación de calidad en este
  -- estudio. Nulo = el default del código.
  add column puntos_participacion int check (puntos_participacion >= 0);

-- ---------- R3.1 · Umbrales de fatiga, por panel --------------------
-- Configurables y con default documentado (R3.1). Una fila por panel; sin
-- fila, rigen los defaults de muestreo.py.
create table umbral_fatiga (
  panel_id                  bigint primary key references panel(id) on delete cascade,
  -- Convocatorias en la ventana reciente por encima de las cuales no se
  -- vuelve a proponer a alguien.
  max_convocatorias_ventana int not null default 3 check (max_convocatorias_ventana >= 0),
  ventana_dias              int not null default 90 check (ventana_dias > 0),
  -- Tope acumulado histórico, para el panelista veterano sobre-usado.
  max_convocatorias_total   int check (max_convocatorias_total > 0),
  -- Días mínimos desde la última convocatoria.
  dias_minimos_entre        int not null default 14 check (dias_minimos_entre >= 0),
  actualizado_por           text,
  actualizado_en            timestamptz not null default now()
);

-- ---------- R3.4 · Un solo earn por (persona, encuesta) -------------
-- La regla «no se puede liquidar dos veces» se apoya acá y no solo en la
-- aplicación: dos liquidaciones concurrentes pasarían cualquier chequeo
-- previo hecho en Python.
create unique index puntos_earn_unico_por_encuesta
    on puntos_movimiento (id_persona, encuesta_id)
 where tipo = 'earn' and encuesta_id is not null;

-- El vencimiento descuenta pero no borra (R3.3): para saber qué lote venció
-- hace falta poder apuntar al movimiento que lo originó.
alter table puntos_movimiento
  add column origen_movimiento_id bigint references puntos_movimiento(id) on delete set null,
  add column vencido boolean not null default false;

create index on puntos_movimiento (id_persona, vence_en)
 where tipo in ('earn','ajuste') and vence_en is not null and not vencido;

-- ---------- R3.5 · Canje ---------------------------------------------
-- El canje ya existía en 0001. Le falta el vínculo con el movimiento que lo
-- descontó, para poder devolver exactamente lo que se cobró al cancelar.
alter table canje
  add column movimiento_id  bigint references puntos_movimiento(id) on delete set null,
  add column resuelto_en    timestamptz,
  add column resuelto_por   text,
  add column nota           text;

-- ---------- R3.6 · Bono de puntos dirigido ---------------------------
create table bono_puntos (
  id            bigint generated always as identity primary key,
  panel_id      bigint not null references panel(id) on delete cascade,
  -- El segmento al que apunta, en las mismas dimensiones que la
  -- composición: 'sexo' | 'tramo_etario' | 'localidad'.
  dimension     text not null check (dimension in ('sexo','tramo_etario','localidad')),
  categoria     text not null,
  puntos_extra  int  not null check (puntos_extra > 0),
  desde         timestamptz not null default now(),
  hasta         timestamptz,
  creado_por    text,
  creado_en     timestamptz not null default now(),
  check (hasta is null or hasta > desde)
);
create index on bono_puntos (panel_id, dimension, categoria);

-- ---------- R3.7 · Texto de consentimiento, versionado ---------------
-- «Cambiarlo no altera lo que ya consintieron los inscriptos anteriores»
-- (R3.7): por eso una fila por versión y ningún update del cuerpo. La
-- versión vigente es la última activa por finalidad.
create table texto_consentimiento (
  id          bigint generated always as identity primary key,
  finalidad   text not null check (finalidad in ('contacto_participacion','uso_semantico')),
  version     text not null,
  cuerpo      text not null,
  activo      boolean not null default true,
  creado_por  text,
  creado_en   timestamptz not null default now(),
  unique (finalidad, version)
);
create index on texto_consentimiento (finalidad, activo, creado_en desc);

-- ---------- R3.7 · Inscripciones públicas -----------------------------
-- Una inscripción NO es una persona todavía: es una solicitud. Se guarda
-- aparte para que la landing —superficie pública, sin login— no escriba
-- nunca directo en `persona`, y para que la aprobación sea un acto humano
-- explícito y auditable.
create table inscripcion (
  id                bigint generated always as identity primary key,
  nombre            text not null,
  documento         text,
  email             text,
  celular           text,
  fecha_nacimiento  date,
  sexo              text,
  localidad         text,
  -- Consentimiento del titular: qué versión aceptó y cuándo. Sin esto la
  -- inscripción no se acepta (R3.7).
  finalidades       text[] not null default '{contacto_participacion}',
  version_texto     text not null,
  acepto_en         timestamptz not null default now(),
  -- Qué dijo la resolución de identidad de R1.2 en el momento del envío.
  resolucion        text not null default 'crea'
                      check (resolucion in ('crea','reutiliza','revision')),
  id_persona_previa uuid,
  estado            text not null default 'pendiente'
                      check (estado in ('pendiente','aprobada','rechazada')),
  id_persona        uuid references persona(id_persona) on delete set null,
  panel_id          bigint references panel(id) on delete set null,
  resuelto_por      text,
  resuelto_en       timestamptz,
  motivo_rechazo    text,
  origen            text not null default 'landing',
  creado_en         timestamptz not null default now()
);
create index on inscripcion (estado, creado_en desc);
create unique index on inscripcion (lower(email))
 where email is not null and estado = 'pendiente';

-- ---------- R3.11 · De dónde salió la composición de un panel --------
alter table panel
  add column origen text not null default 'manual'
    check (origen in ('manual','consulta')),
  -- La definición de la consulta que lo originó, congelada. Un panel creado
  -- desde una consulta es una foto, no una vista que se actualiza sola
  -- (§3, no-goal), así que se guarda la definición y no un puntero vivo.
  add column origen_definicion jsonb,
  add column origen_consulta_id bigint references consulta_guardada(id) on delete set null,
  add column creado_por text;

alter table umbral_fatiga        enable row level security;
alter table bono_puntos          enable row level security;
alter table texto_consentimiento enable row level security;
alter table inscripcion          enable row level security;
