-- ============================================================
--  STORE DE BÓVEDA — Migración 0002
--  Altas en revisión (dedup ambiguo) + índice de apoyo al dedup.
--
--  El HANDOFF de Fase 1 define un caso de dedup que no fusiona:
--  cuando no hay documento ni email, pero nombre + fecha_nacimiento
--  coinciden con una persona existente, el alta se marca como
--  'revisión' para resolución manual. Ese estado necesita dónde vivir.
-- ============================================================

-- Alta que quedó pendiente de decisión humana: no creó persona todavía.
create table alta_en_revision (
  id                bigint generated always as identity primary key,
  -- Payload del alta tal como llegó (PII: vive solo en el store de bóveda).
  datos             jsonb  not null,
  -- Candidatas con las que hubo match ambiguo: [{"id_persona": "...", "motivo": "..."}]
  candidatos        jsonb  not null default '[]'::jsonb,
  motivo            text   not null,   -- 'nombre_fecha_nacimiento' | ...
  estado            text   not null default 'pendiente'
                      check (estado in ('pendiente','fusionada','creada','descartada')),
  -- Resultado de la resolución manual, cuando la hay.
  id_persona        uuid   references persona(id_persona) on delete set null,
  resuelto_por      text,              -- uid de Firebase Auth de quien resolvió
  resuelto_en       timestamptz,
  creado_en         timestamptz not null default now()
);
create index on alta_en_revision (estado);

alter table alta_en_revision enable row level security;

-- Dedup por nombre + fecha de nacimiento (caso 3): búsqueda case-insensitive.
create index persona_nombre_fnac_idx
  on persona (lower(nombre), fecha_nacimiento)
  where nombre is not null and fecha_nacimiento is not null;
