-- 0036 · Mensajes con Flow, canales en la carga, selección aleatoria y
-- audiencias en pasos (D87 a D90,
-- `specs/PEDIDO_unificar_mensajes_y_canal_en_carga_06_11_26.md`).
--
-- Cuatro cosas, en una sola versión:
--
--   1. «WhatsApp Flow» deja de ser una sección aparte: es un tipo de botón de
--      la plantilla de un mensaje (`encuesta_mensaje.boton`, `flow_id`). Lo
--      ya enviado por la sección vieja —que se registraba en `participacion`
--      (`enviado_en`, `envio_estado`, `envio_error`), uno por persona— pasa a
--      `envio`, uno por persona y mensaje. Sin esto, «No enviado aún» le
--      repetiría el Flow a quien ya lo recibió.
--   2. La carga registra los canales aceptados en bloque, con su texto, y
--      cada registro queda auditado (`carga_canal_registro`).
--   3. La selección de una consulta guarda su modo y su semilla
--      (`consulta_ejecucion.seleccion`, `semilla`).
--   4. Cómo se armó la audiencia de una encuesta, paso a paso
--      (`encuesta_audiencia_agregado`), y un origen nuevo para la encuesta
--      independiente creada desde la sección Encuestas (`manual`).
--
-- **Cambia datos** (el punto 1). La corrida de prueba es
-- `scripts/migrar_flow_a_mensajes.py prueba`: ejecuta este mismo archivo en
-- una transacción que se deshace y dice cuántos mensajes y envíos va a crear.
-- Re-ejecutable: lo migrado no se vuelve a crear. **No borra** las columnas
-- viejas de `participacion` ni las de Flow de `encuesta`: se quitan en una
-- versión posterior, una vez verificado que nada las lee.

begin;

-- ---------- 1 · El Flow es un botón de la plantilla del mensaje --------

alter table encuesta_mensaje add column if not exists boton   text;
alter table encuesta_mensaje add column if not exists flow_id text;

alter table encuesta_mensaje drop constraint if exists encuesta_mensaje_boton_check;
alter table encuesta_mensaje add constraint encuesta_mensaje_boton_check
  check (boton is null or boton in ('url', 'flow'));
alter table encuesta_mensaje drop constraint if exists encuesta_mensaje_flow_con_id;
alter table encuesta_mensaje add constraint encuesta_mensaje_flow_con_id
  check (boton is distinct from 'flow' or canal = 'whatsapp');

comment on column encuesta_mensaje.boton is
  'D87 — qué botón lleva la plantilla: url (la encuesta en Alchemer, con el '
  'id_persona al final) o flow (un WhatsApp Flow, con el id_persona como '
  'flow_token). Null: sin botón, o un correo.';
comment on column encuesta_mensaje.flow_id is
  'D87 — el Flow que la plantilla lleva en su botón, como estaba al guardar '
  'el mensaje: queda registrado qué Flow usó la ola aunque la plantilla cambie.';

-- 1.a · Un mensaje por cada encuesta que usó la sección vieja: la que tiene
-- un Flow configurado o algún envío registrado en `participacion`. Se llama
-- «WhatsApp Flow»; si ya hay un mensaje con ese nombre, «WhatsApp Flow
-- (R4.5)». `creado_por = 'migracion_0036'` es lo que lo identifica.
insert into encuesta_mensaje
       (encuesta_id, nombre, canal, plantilla, plantilla_idioma, parametros,
        boton, flow_id, creado_por)
select e.id,
       case when exists (select 1 from encuesta_mensaje m
                          where m.encuesta_id = e.id
                            and lower(m.nombre) = 'whatsapp flow')
            then 'WhatsApp Flow (R4.5)' else 'WhatsApp Flow' end,
       'whatsapp',
       coalesce(e.flow_plantilla, '(sin plantilla registrada)'),
       e.flow_idioma, '[]'::jsonb, 'flow', e.flow_id, 'migracion_0036'
  from encuesta e
 where (e.flow_plantilla is not null
        or exists (select 1 from participacion pa
                    where pa.encuesta_id = e.id and pa.envio_estado is not null))
   and not exists (select 1 from encuesta_mensaje m
                    where m.encuesta_id = e.id and m.creado_por = 'migracion_0036');

-- 1.b · Lo enviado: una fila en `envio` por participación enviada (aceptado,
-- con la fecha de `enviado_en`) o fallida (con su `envio_error`).
insert into envio (encuesta_mensaje_id, id_persona, canal, lanzado_por,
                   enviado_en, estado, error)
select m.id, pa.id_persona, 'whatsapp', 'migracion_0036',
       coalesce(pa.enviado_en, pa.convocado_en, now()),
       case pa.envio_estado when 'enviado' then 'aceptado' else 'fallido' end,
       case when pa.envio_estado = 'fallido'
            then coalesce(pa.envio_error, 'fallido (sección WhatsApp Flow)') end
  from participacion pa
  join encuesta_mensaje m on m.encuesta_id = pa.encuesta_id
                         and m.creado_por = 'migracion_0036'
 where pa.envio_estado in ('enviado', 'fallido')
   and not exists (select 1 from envio v
                    where v.encuesta_mensaje_id = m.id
                      and v.id_persona = pa.id_persona);

comment on column participacion.envio_estado is
  'R4.5 — DEPRECADA desde la 0036 (D87): el registro de envíos es `envio`. '
  'Nada la escribe; se quita en una versión posterior.';
comment on column participacion.enviado_en is
  'R4.5 — DEPRECADA desde la 0036 (D87): ver `envio.enviado_en`.';
comment on column participacion.envio_error is
  'R4.5 — DEPRECADA desde la 0036 (D87): ver `envio.error`.';

-- ---------- 2 · R-CAN · Los canales aceptados, en bloque, auditados -----
-- Una fila por declaración: al confirmar una carga (`ingesta`, una por lote)
-- o desde el detalle de una carga ya hecha (`bloque`). Quién, cuándo, qué
-- canales, con qué texto y a cuántas personas. Sin PII: los conteos.

create table if not exists carga_canal_registro (
  id            bigint generated always as identity primary key,
  carga_id      bigint not null references carga(id) on delete cascade,
  via           text not null,
  -- {"whatsapp": "optin-2026-09", "email": "optin-2026-09"}: canal → versión
  -- del texto con que lo aceptaron.
  canales       jsonb not null,
  personas      int not null default 0,
  -- Por canal: registrados, ya lo tenían, y los que no, por motivo.
  resultado     jsonb not null default '{}'::jsonb,
  trabajo_id    bigint,
  actor_uid     text,
  actor_email   text,
  creado_en     timestamptz not null default now()
);

alter table carga_canal_registro drop constraint if exists carga_canal_registro_via_check;
alter table carga_canal_registro add constraint carga_canal_registro_via_check
  check (via in ('ingesta', 'bloque'));

create index if not exists carga_canal_registro_por_carga
    on carga_canal_registro (carga_id, creado_en desc);

comment on table carga_canal_registro is
  'R-CAN.1/R-CAN.2 — cada vez que se registraron canales aceptados para las '
  'personas de una carga: quién, cuándo, qué canales, con qué texto y a '
  'cuántas. Un canal revocado no se reactiva en bloque (R-CAN.3).';

-- ---------- 3 · R-ALE · El modo de selección y su semilla ---------------

alter table consulta_ejecucion add column if not exists seleccion text;
alter table consulta_ejecucion add column if not exists semilla   text;

comment on column consulta_ejecucion.seleccion is
  'R-ALE — cómo se eligieron las personas mostradas: ranking (los que mejor '
  'cumplen) o aleatorio (un sorteo con semilla entre los que cumplen).';
comment on column consulta_ejecucion.semilla is
  'R-ALE — la semilla del sorteo: md5(id_persona || semilla). Con la misma '
  'semilla y los mismos datos, la misma selección.';

-- ---------- 4 · R-AU · Cómo se armó la audiencia de una encuesta --------

create table if not exists encuesta_audiencia_agregado (
  id                bigint generated always as identity primary key,
  encuesta_id       bigint not null references encuesta(id) on delete cascade,
  -- crear_desde_consulta | agregar_desde_consulta
  via               text not null,
  -- ranking | aleatorio | segmento_completo
  modo              text not null,
  semilla           text,
  -- La definición de la consulta, congelada (como `panel.origen_definicion`).
  definicion        jsonb,
  pedidas           int,
  en_la_seleccion   int not null default 0,
  agregadas         int not null default 0,
  -- Los que no entraron, por motivo: ya_estaban, en_lista_negra,
  -- sin_consentimiento, no_encontrados.
  excluidas         jsonb not null default '{}'::jsonb,
  actor_uid         text,
  actor_email       text,
  creado_en         timestamptz not null default now()
);

alter table encuesta_audiencia_agregado
  drop constraint if exists encuesta_audiencia_agregado_modo_check;
alter table encuesta_audiencia_agregado add constraint encuesta_audiencia_agregado_modo_check
  check (modo in ('ranking', 'aleatorio', 'segmento_completo'));
alter table encuesta_audiencia_agregado
  drop constraint if exists encuesta_audiencia_agregado_via_check;
alter table encuesta_audiencia_agregado add constraint encuesta_audiencia_agregado_via_check
  check (via in ('crear_desde_consulta', 'agregar_desde_consulta'));

create index if not exists encuesta_audiencia_agregado_por_encuesta
    on encuesta_audiencia_agregado (encuesta_id, creado_en);

comment on table encuesta_audiencia_agregado is
  'R-ALE.3 / R-AU.2 — cada vez que se sumó gente a la audiencia de una '
  'encuesta desde una consulta: quién, cuándo, la definición, el modo, la '
  'semilla y cuántas personas. La audiencia se reconstruye paso a paso.';

-- R-AU.3 — una independiente creada vacía desde la sección Encuestas. Tiene
-- convocatoria (se arma a mano o desde consultas), como la de una consulta.
alter table encuesta drop constraint if exists encuesta_origen_check;
alter table encuesta add constraint encuesta_origen_check
  check (origen in ('panel', 'consulta', 'carga', 'manual'));

comment on column encuesta.origen is
  'De dónde viene: panel · consulta (independiente, creada desde el resultado '
  'de una consulta) · manual (independiente, creada vacía en Encuestas; su '
  'audiencia se arma a mano o desde consultas) · carga (independiente, llegó '
  'como archivo de respuestas, sin audiencia).';

-- ---------- 5 · Seguridad ------------------------------------------------

alter table carga_canal_registro        enable row level security;
alter table encuesta_audiencia_agregado enable row level security;

revoke all on carga_canal_registro, encuesta_audiencia_agregado from public;

commit;
