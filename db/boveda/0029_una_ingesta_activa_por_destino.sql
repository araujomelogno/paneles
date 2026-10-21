-- ============================================================
--  STORE DE BÓVEDA — Migración 0029
--  Una sola ingesta en curso por destino (R-ING.2) y el tiempo de cada
--  parte de la preparación (R-ING.3).
-- ============================================================
--
-- La `0019` creó `ingesta_trabajo_abiertos` para **listar** los trabajos en
-- curso, y no es único: nada impedía encolar una segunda ingesta de la misma
-- carga mientras la primera seguía. Con el modal de revisión sin respuesta
-- visible, el segundo clic era lo natural, y dos tareas procesaban lo mismo
-- en paralelo. La ingesta es idempotente —no duplica personas ni
-- respuestas—, pero se pagaba y se procesaba dos veces, y nadie se enteraba.
--
-- La guarda va **en la base** y no solo en la pantalla: un doble clic muy
-- rápido o dos pestañas pasan por encima de cualquier control del navegador,
-- y un «¿hay uno abierto?» en Python entre dos pedidos simultáneos contesta
-- que no a los dos. El índice único parcial lo garantiza aunque lleguen al
-- mismo tiempo: el segundo `insert` falla y `diferida.encolar` lo traduce a
-- un rechazo que lleva al trabajo que ya está en curso.
--
-- Cubre también el reproceso (Fase 8), que usa el mismo destino: dos
-- procesos sobre las mismas respuestas podían pisarse, y `reproceso.lanzar`
-- ya lo rechazaba con el mismo problema de carrera.
--
-- Un trabajo `terminada`, `terminada_con_errores` o `fallida` no bloquea:
-- reintentar después de un fallo tiene que seguir siendo posible.
--
-- **Antes de aplicarla**: si ya hay dos trabajos activos para el mismo
-- destino el índice no se puede crear, y la migración falla entera sin
-- cambiar nada. Se ven con
--
--   select destino_tipo, destino_id, count(*)
--     from ingesta_trabajo
--    where estado in ('encolada', 'procesando')
--    group by 1, 2 having count(*) > 1;
--
-- y el despliegue dice cómo resolverlos.
--
-- Agrega también `ingesta_trabajo.preparacion` (R-ING.3): cuánto tardó cada
-- parte del pedido que creó el trabajo.
--
-- Aditiva. No toca el store semántico. Se puede correr dos veces.
begin;

create unique index if not exists ingesta_trabajo_uno_activo_por_destino
    on ingesta_trabajo (destino_tipo, destino_id)
 where estado in ('encolada', 'procesando');

comment on index ingesta_trabajo_uno_activo_por_destino is
  'R-ING.2: a lo sumo un trabajo encolado o procesando por destino. Una '
  'ingesta terminada o fallida no bloquea la siguiente.';

-- R-ING.3 — cuánto tardó cada parte de la preparación del pedido (armar y
-- guardar el plan, despivotar, crear individuos, persistir lotes, encolar).
-- Confirmar debería responder en segundos; si una parte crece con el
-- tamaño de la base, acá se ve cuál, carga por carga, sin depender de que
-- alguien haya mirado el log ese día.
alter table ingesta_trabajo add column if not exists preparacion jsonb;

comment on column ingesta_trabajo.preparacion is
  'R-ING.3: milisegundos de cada parte de la preparación del pedido que '
  'creó el trabajo, y el total. Diagnóstico; nada decide con esto.';

commit;
