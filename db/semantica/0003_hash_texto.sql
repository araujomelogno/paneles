-- ============================================================
--  STORE SEMÁNTICO — Migración 0003 (Fase 2)
--  Hash del texto embebido, para no re-embeber lo que no cambió.
--
--  Re-ingestar una ola es normal: llega una corrección de campo, se
--  agrega una pregunta, se vuelve a subir el archivo. Hoy eso
--  re-embebe todo, y embeber es la parte que cuesta plata y tiempo.
--
--  Con el hash guardado, la ingesta compara antes de llamar al
--  proveedor: si el `texto_embebido` de una (individuo, pregunta) es
--  idéntico al que ya está, se saltea. El vector no se recalcula
--  porque el mismo texto con el mismo modelo da el mismo vector.
--
--  Es sha256 del texto exacto que se vectorizó. No es PII ni deriva de
--  PII: `texto_embebido` es «pregunta → respuesta», ya
--  despersonalizado. Y el nombre de la columna tampoco es un campo
--  identificatorio, así que la auditoría de PII lo deja pasar.
--
--  Las filas anteriores a esta migración quedan con `hash_texto` nulo:
--  la ingesta las trata como «no sé qué había» y las re-embebe una vez,
--  que es el comportamiento anterior. No hay backfill porque el hash
--  de una fila vieja se calcula solo, en la próxima ingesta.
-- ============================================================

alter table respuesta add column if not exists hash_texto text;

comment on column respuesta.hash_texto is
  'sha256 hex del texto_embebido. Permite saltear el re-embedding cuando '
  'la re-ingesta trae el mismo texto. Nulo = desconocido, se re-embebe.';
