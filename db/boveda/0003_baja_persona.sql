-- ============================================================
--  STORE DE BÓVEDA — Migración 0003
--  Lápida de baja (tombstone) para el retiro de consentimiento.
--
--  El retiro total dispara borrado en cascada de la PII (CLAUDE.md).
--  Borrar la fila 'persona' se lleva puesta también la prueba de que
--  el retiro se atendió. Esta tabla guarda el rastro mínimo que hace
--  auditable el cumplimiento: el token opaco 'id_persona' (que NO es
--  PII), cuándo se pidió la baja y si el borrado semántico ya se
--  confirmó. Ninguna columna acá guarda nombre, documento, email,
--  celular, fecha de nacimiento ni observaciones.
-- ============================================================

create table persona_borrada (
  id_persona        uuid primary key,   -- token opaco; sin FK: la persona ya no existe
  motivo            text not null,      -- 'retiro_consentimiento' | 'baja_solicitada' | ...
  finalidad         text,               -- finalidad retirada que originó la baja
  solicitado_por    text,               -- uid de Firebase Auth del operador
  borrado_local_en  timestamptz not null default now(),
  -- El borrado semántico es una llamada a otra instancia: puede fallar y
  -- reintentarse. Null = pendiente de confirmar.
  borrado_semantica_en timestamptz,
  semantica_error      text
);
create index on persona_borrada (borrado_semantica_en) where borrado_semantica_en is null;

alter table persona_borrada enable row level security;
