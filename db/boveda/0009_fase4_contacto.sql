-- Fase 4 · Bloque 4A — Contacto.
--
-- Tres cosas que hasta acá no existían, y una regla que las ata:
--
--   * **Preferencias de canal** (R4.4). El consentimiento por finalidad
--     responde «¿puedo contactarla?»; la preferencia responde «¿por dónde?».
--     Son ejes distintos y hacen falta **los dos** para enviar por un canal.
--   * **Verificación de contacto** (R4.3). La landing aceptaba cualquier
--     envío: cualquiera podía inscribir datos ajenos o inventados. Ahora el
--     celular y el correo se verifican con un código de un solo uso **antes**
--     de que exista la inscripción.
--   * **Envío por WhatsApp Flow** (R4.5). Una encuesta puede configurarse con
--     un Flow publicado y su plantilla aprobada, y la convocatoria ofrece
--     enviarlo. El sistema **solo envía**: no recibe respuestas, no crea
--     Flows ni plantillas.
--
-- Todo aditivo: tablas nuevas y columnas nullables. Se aplica con la app
-- andando y no reescribe ninguna fila existente.

-- ============================================================
--  1 · R4.4 · Preferencias de canal
-- ============================================================

create table preferencia_canal (
  id          bigint generated always as identity primary key,
  id_persona  uuid not null references persona(id_persona) on delete cascade,
  canal       text not null
                check (canal in ('whatsapp','email','telefono','sms')),
  estado      text not null default 'activa'
                check (estado in ('activa','revocada')),
  -- Con qué texto se obtuvo el opt-in. Es lo que lo hace demostrable, igual
  -- que `consentimiento.version_texto`: sin esto, «aceptó recibir WhatsApp»
  -- es una afirmación sin respaldo.
  version_texto text,
  -- Por cuál de los tres caminos de alta entró: 'alta_manual', 'ingesta',
  -- 'landing'. Sin check: si mañana aparece un camino nuevo, perder el dato
  -- sería peor que guardarlo con una etiqueta que esta lista no previó.
  origen      text,
  otorgado_en timestamptz not null default now(),
  revocado_en timestamptz
);
-- Una preferencia vigente por persona y canal. Revocar no borra la fila:
-- cambia su estado, para que quede el rastro de que alguna vez aceptó.
create unique index preferencia_canal_unica
    on preferencia_canal (id_persona, canal);
create index preferencia_canal_activas
    on preferencia_canal (canal, estado) where estado = 'activa';

comment on table preferencia_canal is
  'R4.4 — por qué canal acepta que la contacten. Eje distinto del '
  'consentimiento por finalidad: para enviar hacen falta los dos.';

-- ============================================================
--  2 · R4.3 · Verificación de contacto
-- ============================================================

create table verificacion_contacto (
  id          bigint generated always as identity primary key,
  canal       text not null check (canal in ('celular','email')),
  -- El celular en E.164 o el correo en minúsculas. Es a lo que se le mandó
  -- el código, y contra lo que después se compara la inscripción.
  destino     text not null,
  -- **Nunca el código en claro.** Un código guardado sin hashear es una
  -- credencial de un solo uso al alcance de cualquiera que lea la tabla, y
  -- alcanza para inscribir a nombre de otro.
  codigo_hash text not null,
  estado      text not null default 'pendiente'
                check (estado in ('pendiente','verificado','usado','vencido')),
  intentos    int not null default 0,
  vence_en    timestamptz not null,
  verificado_en timestamptz,
  -- De dónde vino el pedido, hasheado. Sirve para limitar la tasa por origen
  -- sin guardar la IP en claro: para contar envíos alcanza con saber que dos
  -- vinieron del mismo lado, no de cuál.
  origen_hash text,
  creado_en   timestamptz not null default now()
);
create index verificacion_contacto_destino
    on verificacion_contacto (canal, destino, creado_en desc);
create index verificacion_contacto_origen
    on verificacion_contacto (origen_hash, creado_en desc);

comment on table verificacion_contacto is
  'R4.3 — códigos de un solo uso emitidos para verificar un contacto antes '
  'de crear la inscripción. El código va hasheado; la IP también.';

-- Qué verificó una inscripción. Nullable porque las inscripciones anteriores
-- a esta migración no pasaron por verificación, y hay que poder distinguir
-- «no verificada» de «verificada hace tiempo».
alter table inscripcion
  add column celular_verificado boolean not null default false,
  add column email_verificado   boolean not null default false,
  -- R4.4 — los canales que la persona aceptó **de primera mano** en el
  -- formulario. Se guardan en la inscripción y no como preferencias porque
  -- todavía no existe la persona: recién al aprobar se convierten.
  add column canales            text[] not null default '{}',
  add column version_texto_canales text;

comment on column inscripcion.celular_verificado is
  'R4.3/R4.5 — un celular verificado es lo que permite enviar por WhatsApp '
  'con confianza y sin quemar la reputación del número emisor.';

-- ============================================================
--  3 · R4.5 · Encuesta de WhatsApp Flow
-- ============================================================
-- El Flow y la plantilla se crean **en Meta** y acá se referencian por id.
-- El sistema no los crea ni los edita: solo verifica que estén publicados y
-- aprobados antes de dejar convocar.

alter table encuesta
  add column flow_id        text,
  add column flow_plantilla text,
  add column flow_idioma    text;

comment on column encuesta.flow_id is
  'R4.5 — id del Flow publicado en Meta. Con esto y la plantilla aprobada, '
  'la convocatoria ofrece enviar por WhatsApp.';

-- El estado del envío por persona. Va en `participacion` y no en una tabla
-- aparte porque el envío es una acción **sobre** una convocatoria que ya
-- existe: la convocatoria se registra igual que siempre y el envío no la
-- reemplaza.
alter table participacion
  add column enviado_en   timestamptz,
  add column envio_estado text check (envio_estado in ('enviado','fallido')),
  add column envio_error  text;

comment on column participacion.envio_estado is
  'R4.5 — si el mensaje de WhatsApp salió o no, para poder reintentar solo '
  'los fallidos sin volver a enviar a quien ya recibió.';
