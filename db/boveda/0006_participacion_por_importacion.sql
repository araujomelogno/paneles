-- Addendum a R3.9 — Membresía y participación al ingestar.
--
-- Hasta acá, convocatoria e ingesta estaban desacopladas: convocar creaba
-- participaciones, ingestar escribía respuestas mapeando por `alias_origen`,
-- y nada las unía. Consecuencia: quien respondió en campo sin haber sido
-- convocado desde el sistema no tenía fila en `participacion` —la ola
-- mostraba menos respuestas de las que hubo— ni membresía en el panel, así
-- que era invisible para el muestreo, la composición y la cuota.
--
-- La ingesta ahora crea las dos cosas. Para poder distinguir una
-- participación que nació de una convocatoria emitida por el sistema de una
-- que se dedujo de un archivo de campo, hace falta decir de dónde salió.
--
-- Aditiva: el default deja intactas las filas existentes, que son todas
-- convocatorias, y se puede aplicar con la app andando.
alter table participacion
  add column origen text not null default 'convocatoria'
    check (origen in ('convocatoria','importacion'));

comment on column participacion.origen is
  'convocatoria: la emitió el sistema y hubo gate de contacto_participacion. '
  'importacion: se dedujo del archivo de campo al ingestar, registra un hecho '
  'ya ocurrido y por eso no pasa por ese gate.';
