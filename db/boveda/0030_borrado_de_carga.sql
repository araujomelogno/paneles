-- ============================================================
--  STORE DE BÓVEDA — Migración 0030
--  Borrar una carga, con lo que trajo (PEDIDO del celular, parte 3).
-- ============================================================
--
-- Una carga hecha con un mapeo equivocado, o de prueba, no tenía cómo
-- deshacerse. El borrado lo ejecuta `cargas_borrado.py` por la vía diferida
-- (un trabajo de `ingesta_trabajo` con `plan.operacion = 'borrado_carga'`):
-- así la guarda de la `0029` —una sola operación en curso por destino— lo
-- bloquea mientras hay una ingesta, y bloquea una ingesta mientras borra.
--
-- Esta migración agrega una sola cosa: `carga_borrado`, el registro de cada
-- borrado. Es la auditoría que pide el pedido —quién, cuándo, cuántas
-- personas se dieron de baja, cuántas se conservaron, cuántas respuestas se
-- eliminaron— y es también lo que permite reanudarlo: un borrado
-- `en_curso` cuyo trabajo se cortó se retoma desde su fila.
--
-- **La auditoría no se borra con la carga**: `carga_id` es `on delete set
-- null`, y el nombre y el `ref_estudio` se copian, para que la fila siga
-- diciendo qué se borró cuando la carga ya no existe.
--
-- Qué borra cada paso, y qué no (una carga no es dueña de todos sus
-- panelistas), está en `cargas_borrado.py` y en D78. Las personas se dan de
-- baja por `bajas.retirar()`, el mismo camino que la baja de un panelista:
-- generan el `borrado_pendiente` hacia COLOQUIO. Su lápida en
-- `persona_borrada` lleva el motivo `borrado_de_carga`.
--
-- Aditiva. No toca el store semántico. Se puede correr dos veces. Sin
-- `grant` a nadie: COLOQUIO no tiene por qué verla.
begin;

create table if not exists carga_borrado (
  id                      bigint generated always as identity primary key,
  carga_id                bigint references carga(id) on delete set null,
  carga_nombre            text not null,
  ref_estudio             uuid not null,
  estado                  text not null default 'en_curso'
                            check (estado in ('en_curso', 'terminado')),
  -- El trabajo diferido que lo ejecuta. Sin FK, como `reproceso.trabajo_id`:
  -- `ingesta_trabajo` se purga y la auditoría no.
  trabajo_id              bigint,
  -- Lo que mostró la vista previa al confirmar, tal cual.
  previa                  jsonb not null,
  personas_dadas_de_baja  int,
  personas_conservadas    int,
  respuestas_borradas     int,
  actor                   text,
  actor_email             text,
  creado_en               timestamptz not null default now(),
  terminado_en            timestamptz
);

comment on table carga_borrado is
  'Cada borrado de una carga: quién, cuándo, la vista previa que se confirmó '
  'y los conteos finales. Sobrevive a la carga (carga_id on delete set null). '
  'Un borrado en_curso es reanudable.';

-- Un solo borrado en curso por carga; terminados, los que haya.
create unique index if not exists carga_borrado_uno_en_curso
    on carga_borrado (carga_id) where estado = 'en_curso';

alter table carga_borrado enable row level security;

revoke all on carga_borrado from public;

commit;
