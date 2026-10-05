-- ============================================================
--  STORE DE BÓVEDA — Migración 0023
--  R-MAIL — Registro de los correos que envía el sistema.
-- ============================================================
--
-- Desde R-MAIL el portal y la landing mandan correo de verdad, por SMTP de
-- Google Workspace (`panel_api/correo.py`). Este registro existe por tres
-- cosas que la spec pide y que sin él no se pueden ver:
--
--   1 · Los envíos **fallidos**, con destinatario, momento y motivo
--       (R-MAIL.4). Un correo que no sale es un panelista sin acceso; sin
--       el motivo a mano, diagnosticarlo es adivinar.
--   2 · **Cuántos correos salen por día** (§7). Workspace corta en 2.000
--       destinatarios diarios por cuenta: mejor ver que el tope se acerca
--       que descubrirlo con envíos rechazados.
--   3 · Que un fallo **no se reporte como éxito**: la fila del fallido se
--       confirma aunque la request termine en error.
--
-- Vive en la bóveda y no en el store semántico porque el destinatario es un
-- dato personal. Por lo mismo:
--
--   · una baja lo borra (`bajas.retirar` → `correo.borrar_de`);
--   · se purga solo a los 90 días (`correo.registrar` borra lo viejo en cada
--     envío): es para diagnosticar, no un archivo de direcciones;
--   · no guarda **nunca** el contenido —ni el código ni el enlace—: un
--     enlace guardado es una credencial guardada.
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

create table if not exists envio_correo (
  id           bigserial   primary key,
  tipo         text        not null,
  destinatario text        not null,
  proveedor    text        not null,
  estado       text        not null check (estado in ('enviado', 'fallido')),
  motivo       text,
  intentos     int         not null default 1,
  duracion_ms  int,
  creado_en    timestamptz not null default now()
);

comment on table envio_correo is
  'R-MAIL — un registro por correo que el sistema intentó mandar, salga o '
  'no. Sin contenido: ni el código ni el enlace. Se purga a los 90 días y '
  'una baja borra las filas de su dirección.';
comment on column envio_correo.tipo is
  'codigo_verificacion · alta_clave · recuperacion_clave · acceso_usuario · prueba';
comment on column envio_correo.motivo is
  'Por qué falló, tal como lo reportó el servidor SMTP. Nunca credenciales.';

create index if not exists envio_correo_creado_idx
  on envio_correo (creado_en desc);
create index if not exists envio_correo_destinatario_idx
  on envio_correo (lower(destinatario));

-- Cuántos salieron por día, en hora de Montevideo: es el número que se
-- compara contra el tope de Workspace.
create or replace view v_envio_correo_por_dia as
select (creado_en at time zone 'America/Montevideo')::date as dia,
       count(*) filter (where estado = 'enviado')::int    as enviados,
       count(*) filter (where estado = 'fallido')::int    as fallidos
  from envio_correo
 group by 1;

comment on view v_envio_correo_por_dia is
  'R-MAIL §7 — envíos por día (hora de Montevideo), para ver cuándo se '
  'acerca el tope de 2.000 destinatarios diarios de Workspace.';

-- Las tablas nuevas no son de nadie más. La 0014 ya hizo
-- `revoke all on schema public from coloquio_app`; esto es explícito para que
-- se lea en la migración que las crea.
revoke all on envio_correo, v_envio_correo_por_dia from public;

commit;
