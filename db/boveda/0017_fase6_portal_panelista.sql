-- Fase 6 — El portal del panelista.
--
-- ============================================================
--  Qué cambia de postura con esta migración
-- ============================================================
-- Hasta acá la bóveda la tocaban **empleados de Equipos**: una decena de
-- cuentas, todas con un rol del padrón interno. Desde la Fase 6 autentica a
-- miles de externos contra el store que tiene toda la PII.
--
-- Eso se nota en cada objeto de abajo. Ninguno confía en lo que manda el
-- cliente: el vínculo entre la cuenta y la persona lo resuelve la base a
-- partir del `uid`, nunca de un `id_persona` que venga en el pedido; lo que
-- el panelista puede editar es una lista blanca y no una excepción; y el
-- registro de accesos guarda el correo **hasheado**, porque un intento
-- fallido es de alguien que puede no ser panelista y no hay por qué juntar
-- su dirección.

-- ============================================================
--  1 · R6.5 · Qué puede editar el panelista
-- ============================================================
-- Por defecto **nada**. Habilitar un atributo es una decisión de quien
-- administra el vocabulario, no el estado natural de las cosas: un
-- segmentador que define cuotas y que cada quien puede cambiar a voluntad
-- deja de servir para muestrear.
alter table atributo_demografico
  add column if not exists editable_por_panelista boolean not null default false;

comment on column atributo_demografico.editable_por_panelista is
  'R6.5 — si el panelista puede cambiarlo desde el portal. Por omisión no: '
  'habilitarlo es una decisión. Los `derivado` nunca pueden serlo (lo hace '
  'valer el trigger de abajo).';

-- Un derivado no se edita: su valor **se calcula**. Dejar marcarlo editable
-- prometería algo que el esquema no va a cumplir, y el portal mostraría un
-- control que no hace nada.
create function atributo_editable_coherente() returns trigger
language plpgsql as $$
begin
  if new.editable_por_panelista and new.tipo = 'derivado' then
    raise exception
      'El atributo «%» es derivado: su valor se calcula, así que no puede '
      'ser editable por el panelista.', new.clave
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger atributo_editable_coherente
  before insert or update on atributo_demografico
  for each row execute function atributo_editable_coherente();

-- ============================================================
--  2 · R6.2 · El vínculo entre la cuenta y la persona
-- ============================================================
-- Una cuenta de Firebase Auth ↔ una persona de la bóveda. Las dos
-- direcciones son únicas: un `uid` no puede resolver a dos personas, y una
-- persona no puede tener dos cuentas —si pudiera, revocar el acceso dejaría
-- la otra puerta abierta—.
create table cuenta_panelista (
  uid           text primary key,
  id_persona    uuid not null unique
                references persona(id_persona) on delete cascade,
  -- Con qué correo se vinculó. Se guarda para poder detectar el caso en que
  -- la persona cambia de correo en la bóveda y la cuenta queda apuntando a
  -- la dirección vieja.
  email         text not null,
  vinculada_en  timestamptz not null default now(),
  ultimo_acceso_en timestamptz
);

comment on table cuenta_panelista is
  'R6.2 — qué persona es cada cuenta del portal. El `id_persona` sale de '
  'acá y nunca del pedido: es lo que impide que una sesión lea a otra '
  'persona mandando otro identificador.';

-- `on delete cascade`: la baja borra la persona y con ella su cuenta. Es la
-- mitad de «tras la baja, el acceso se corta»; la otra mitad es que sin fila
-- acá la sesión no resuelve a nadie y el portal responde 403.

-- ============================================================
--  3 · R6.1 · Los enlaces de acceso emitidos
-- ============================================================
-- Hace falta por tres razones distintas, y la tercera es la que obliga a
-- registrar también los intentos que **no** emitieron nada:
--
--   · un enlace tiene que servir una sola vez y vencer;
--   · el DPO tiene que poder ver que hubo un acceso y cuándo;
--   · el límite de tasa necesita contar los intentos por correo y por
--     origen, y si solo se registraran los que corresponden a un panelista,
--     probar direcciones ajenas saldría gratis —que es exactamente el ataque
--     de enumeración que R6.1 existe para frenar—.
--
-- El correo va **hasheado**. Un intento puede ser de alguien que no es
-- panelista: guardar su dirección en claro sería juntar datos personales de
-- gente que no aceptó nada, en la tabla que menos lo justifica.
create table acceso_portal (
  id           bigint generated always as identity primary key,
  email_hash   text not null,
  -- Null cuando el correo no corresponde a ningún panelista activo. La fila
  -- existe igual: es lo que hace que el intento cuente para el límite.
  id_persona   uuid references persona(id_persona) on delete set null,
  -- Null cuando no se emitió enlace. Nunca el token en claro: quien pueda
  -- leer esta tabla no tiene que poder entrar como otro.
  token_hash   text,
  vence_en     timestamptz,
  usado_en     timestamptz,
  origen_hash  text,
  creado_en    timestamptz not null default now()
);

create index acceso_portal_por_email on acceso_portal (email_hash, creado_en desc);
create index acceso_portal_por_origen on acceso_portal (origen_hash, creado_en desc)
  where origen_hash is not null;
-- Buscar el enlace al canjearlo. Parcial: los intentos sin token no se
-- buscan nunca por token.
create unique index acceso_portal_token on acceso_portal (token_hash)
  where token_hash is not null;

comment on table acceso_portal is
  'R6.1 — cada pedido de acceso al portal: a qué correo (hasheado), si '
  'correspondía a un panelista, qué enlace se emitió y si se usó. Los '
  'intentos fallidos se registran a propósito: sin ellos, probar '
  'direcciones ajenas no tendría límite.';

-- ============================================================
--  4 · R6.4 · El canje pasa por una aprobación
-- ============================================================
-- «Lo revisé» y «lo entregué» eran el mismo estado, y no son lo mismo: entre
-- los dos puede pasar una semana, y durante esa semana el panelista
-- necesita ver que su pedido avanzó. Sin el estado del medio, el portal solo
-- puede decir «solicitado» hasta que el premio llega.
alter table canje drop constraint if exists canje_estado_check;
alter table canje
  add constraint canje_estado_check
      check (estado in ('solicitado', 'aprobado', 'entregado', 'cancelado'));

alter table canje add column if not exists aprobado_por text;
alter table canje add column if not exists aprobado_en timestamptz;

comment on column canje.aprobado_en is
  'R6.4 — cuándo se aprobó, que es distinto de cuándo se entregó. El '
  'panelista ve los dos momentos.';

-- La vista que el portal usa para el estado de un canje. Existe para que el
-- portal no arme el texto a partir del código: «solicitado» no le dice nada
-- a quien pidió un premio.
create view v_canje_panelista as
select
  c.id, c.id_persona, c.premio_id, p.nombre as premio, c.costo_puntos,
  c.estado,
  case c.estado
    when 'solicitado' then 'Lo pediste y está esperando revisión.'
    when 'aprobado'   then 'Lo revisamos y está aprobado: falta entregártelo.'
    when 'entregado'  then 'Te lo entregamos.'
    when 'cancelado'  then 'Se canceló y los puntos volvieron a tu saldo.'
  end as estado_texto,
  c.creado_en, c.aprobado_en, c.resuelto_en, c.nota
  from canje c
  join catalogo_premio p on p.id = c.premio_id;

comment on view v_canje_panelista is
  'R6.4 — el canje tal como lo ve quien lo pidió, con el estado en palabras '
  'y sin los campos internos de quién lo resolvió.';
