-- 0038 · Alchemer, consulta por participación y bandeja de WhatsApp
-- (D96 a D98, `specs/SPEC_alchemer_participacion_y_bandeja_whatsapp.md`).
--
-- Tres partes, en una sola versión:
--
--   B. **La fecha de campo es obligatoria** (R-PAR.2, D96). La consulta por
--      historia de participación pregunta «respondió una encuesta cuya fecha
--      de campo cae en la ventana», y una encuesta sin fecha no entraría en
--      ninguna ventana sin que nadie se entere. Las que no la tienen se
--      completan con la fecha de su **primera carga** (o la de creación, si
--      no tienen cargas) y quedan **listadas** en `encuesta_fecha_completada`
--      para que el equipo las revise. Después, `not null`.
--   A. **Alchemer** (R-ALC, D97): el id de la encuesta de Alchemer vinculada
--      y su cuestionario como **referencia**, en la bóveda (no tiene datos
--      personales). No es el store semántico: los códigos de la API pueden no
--      coincidir con los del SAV, y escribirlo ahí duplicaría preguntas.
--   C. **La bandeja de WhatsApp** (R-WA, D98): lo que manda el webhook de Meta.
--      En la bóveda y en tablas propias, porque tiene teléfonos.
--
-- **Cambia datos** (la parte B). La corrida de prueba es
-- `scripts/migrar_fecha_de_campo.py prueba`: ejecuta este mismo archivo en una
-- transacción que se deshace y lista las encuestas que va a completar.
-- Re-ejecutable.

begin;

-- ---------- B · La fecha de campo, obligatoria -------------------------

create table if not exists encuesta_fecha_completada (
  encuesta_id   bigint primary key references encuesta(id) on delete cascade,
  fecha_campo   date        not null,
  fuente        text        not null check (fuente in ('primera_carga', 'creacion')),
  completada_en timestamptz not null default now()
);

comment on table encuesta_fecha_completada is
  'R-PAR.2 (D96) — las encuestas que no tenían fecha de campo y la 0038 '
  'completó (con la de su primera carga o la de creación). Es la lista que '
  'el equipo revisa; corregir la fecha se hace desde «Datos del estudio».';

alter table encuesta_fecha_completada enable row level security;

with sin_fecha as (
  select e.id,
         (select min(c.creado_en)::date from carga c where c.encuesta_id = e.id) as primera,
         e.creado_en::date as creacion
    from encuesta e
   where e.fecha_campo is null
),
anotadas as (
  insert into encuesta_fecha_completada (encuesta_id, fecha_campo, fuente)
  select id, coalesce(primera, creacion),
         case when primera is not null then 'primera_carga' else 'creacion' end
    from sin_fecha
  on conflict (encuesta_id) do nothing
  returning encuesta_id, fecha_campo
)
update encuesta e set fecha_campo = a.fecha_campo
  from anotadas a
 where e.id = a.encuesta_id and e.fecha_campo is null;

-- Lo que quede sin fecha (una encuesta creada entre la corrida de arriba y
-- esta línea no existe dentro de la transacción, pero por las dudas).
update encuesta set fecha_campo = creado_en::date where fecha_campo is null;

-- El código exige la fecha en los formularios; el default es para que un
-- camino interno que no la pase no rompa (la de hoy).
alter table encuesta alter column fecha_campo set default current_date;
alter table encuesta alter column fecha_campo set not null;

-- ---------- A · Alchemer -----------------------------------------------

alter table encuesta add column if not exists alchemer_id text;

comment on column encuesta.alchemer_id is
  'R-ALC.2 (D97) — el id de la encuesta de Alchemer vinculada. La URL '
  '(`url_alchemer`) se arma con su enlace y el parámetro del panelista.';

create table if not exists alchemer_cuestionario (
  encuesta_id       bigint primary key references encuesta(id) on delete cascade,
  alchemer_id       text        not null,
  titulo            text,
  -- `[{id, codigo, texto, tipo, opciones: {valor: etiqueta}}]`, como lo
  -- devuelve la API ya normalizado. Sin respuestas: solo el cuestionario.
  preguntas         jsonb       not null default '[]'::jsonb,
  -- R-ALC.3 — si la encuesta guarda la variable de URL del panelista. Null:
  -- no se pudo verificar.
  parametro         text,
  captura_parametro boolean,
  verificado_en     timestamptz,
  actualizado_en    timestamptz not null default now()
);

comment on table alchemer_cuestionario is
  'R-ALC.4 (D97) — el cuestionario de la encuesta de Alchemer, como '
  'referencia: para verlo y proponer temáticas antes del campo. No son las '
  'preguntas del store semántico, que siguen saliendo del SAV.';

alter table alchemer_cuestionario enable row level security;

-- ---------- C · La bandeja de WhatsApp ---------------------------------

-- C.1 · El aviso crudo, tal como llegó. Se guarda **antes** de responder 200
-- y antes de procesar: si el procesamiento falla, el aviso queda y se puede
-- reprocesar. La huella es del cuerpo exacto: Meta reenvía el mismo aviso
-- cuando no recibe el 200, y el segundo no se guarda dos veces.
create table if not exists whatsapp_aviso (
  id           bigint generated always as identity primary key,
  huella       text        not null unique,
  cuerpo       jsonb       not null,
  recibido_en  timestamptz not null default now(),
  estado       text        not null default 'pendiente'
                 check (estado in ('pendiente', 'procesado', 'fallido')),
  error        text,
  procesado_en timestamptz
);

create index if not exists whatsapp_aviso_pendientes
    on whatsapp_aviso (recibido_en) where estado <> 'procesado';

comment on table whatsapp_aviso is
  'R-WA.1 (D98) — cada aviso del webhook de Meta, con la firma ya '
  'verificada, antes de procesarlo. Nunca se pierde: lo que falla queda '
  '`fallido` y se reprocesa. Se conserva un plazo corto (R-WA.5).';

-- C.2 · Lo que la gente manda: respuestas de Flow y comentarios. Una fila por
-- mensaje de Meta (`id_mensaje`, único: idempotente aunque llegue en dos
-- avisos distintos).
create table if not exists whatsapp_entrante (
  id                  bigint generated always as identity primary key,
  id_mensaje          text        not null unique,
  tipo                text        not null check (tipo in ('flow', 'comentario')),
  telefono            text        not null,
  enviado_en          timestamptz,
  recibido_en         timestamptz not null default now(),
  -- R-WA.3 — a qué mensaje contesta (`context.id`) y, si es un envío de
  -- paneles, de quién y de qué encuesta.
  contexto_id         text,
  envio_id            bigint references envio(id) on delete set null,
  id_persona          uuid references persona(id_persona) on delete cascade,
  encuesta_id         bigint references encuesta(id) on delete cascade,
  encuesta_mensaje_id bigint references encuesta_mensaje(id) on delete set null,
  -- Sin envío en paneles (una encuesta de afuera): el grupo sale del token
  -- (`tpl=NOMBRE|…` o lo anterior a `|`), o «GENERAL».
  grupo               text,
  flow_token          text,
  respuesta           jsonb,
  texto               text,
  inconsistencia      text,
  aviso_id            bigint references whatsapp_aviso(id) on delete set null
);

create index if not exists whatsapp_entrante_por_encuesta
    on whatsapp_entrante (encuesta_id, tipo, recibido_en desc);
create index if not exists whatsapp_entrante_por_grupo
    on whatsapp_entrante (grupo, tipo, recibido_en desc) where encuesta_id is null;
create index if not exists whatsapp_entrante_por_persona on whatsapp_entrante (id_persona);
create index if not exists whatsapp_entrante_por_telefono on whatsapp_entrante (telefono);

comment on table whatsapp_entrante is
  'R-WA.2/3 (D98) — respuestas de Flow y comentarios que llegan por el '
  'webhook. Con envío en paneles quedan asociados a la encuesta y la persona; '
  'sin él, a un grupo (como la planilla). Lo de afuera se borra a los N días; '
  'la baja de un panelista borra lo suyo por id_persona y por teléfono.';

-- C.3 · Los estados de entrega (cierra R-CF.7). Uno por mensaje y estado.
create table if not exists whatsapp_estado (
  id_mensaje_meta text        not null,
  estado          text        not null,
  ocurrido_en     timestamptz,
  recibido_en     timestamptz not null default now(),
  error           text,
  primary key (id_mensaje_meta, estado)
);

comment on table whatsapp_estado is
  'R-WA.2 (D98) — los estados que Meta informa de cada mensaje enviado: '
  'sent, delivered, read, failed. Actualizan `envio.estado` (R-CF.7).';

-- C.4 · Cada exportación de la bandeja: tiene teléfonos (R-WA.4).
create table if not exists whatsapp_exportacion (
  id          bigint generated always as identity primary key,
  creado_en   timestamptz not null default now(),
  actor       text,
  actor_email text,
  filtro      jsonb       not null default '{}'::jsonb,
  filas       int         not null
);

comment on table whatsapp_exportacion is
  'R-WA.4 (D98) — quién exportó la bandeja de WhatsApp, con qué filtro y '
  'cuántas filas. Una exportación con teléfonos queda registrada.';

alter table whatsapp_aviso enable row level security;
alter table whatsapp_entrante enable row level security;
alter table whatsapp_estado enable row level security;
alter table whatsapp_exportacion enable row level security;

revoke all on encuesta_fecha_completada, alchemer_cuestionario, whatsapp_aviso,
              whatsapp_entrante, whatsapp_estado, whatsapp_exportacion from public;

commit;
