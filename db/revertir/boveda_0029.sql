-- ============================================================
--  Revertir boveda/0029 · una sola ingesta activa por destino
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`. Quita el
-- índice —y con él la guarda contra el doble envío— y la columna
-- `ingesta_trabajo.preparacion`, que es diagnóstico: se pierden los tiempos
-- registrados y nada más. Se corre **después** de volver a desplegar el
-- código anterior: el nuevo escribe esa columna al encolar.

begin;

drop index if exists ingesta_trabajo_uno_activo_por_destino;
alter table ingesta_trabajo drop column if exists preparacion;

commit;
