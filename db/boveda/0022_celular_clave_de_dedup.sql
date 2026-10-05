-- ============================================================
--  STORE DE BÓVEDA — Migración 0022
--  El celular pasa a ser clave de dedup (después del documento y del
--  correo, antes de nombre + fecha de nacimiento).
-- ============================================================
--
-- `dedup.resolver` ahora busca por celular en cada alta. El alta por archivo
-- resuelve el dedup **fila por fila dentro de la request** (las personas
-- tienen que existir antes de encolar los lotes), así que sin índice cada
-- fila recorrería `persona` entera.
--
-- El índice **no es único**, y a propósito. El documento y el correo lo son
-- (0001): son de una persona. Un celular, no siempre: el de un hogar, el que
-- usa un padre mayor y es del hijo, el que la compañía reasignó. Por eso el
-- dedup reutiliza por celular solo cuando hay **una** titular y nada la
-- contradice, y manda a revisión cuando lo comparten varias. Un índice
-- único habría convertido esa realidad en un error de inserción.
--
-- Compara en E.164 (`+59899123456`): es como se guarda el celular desde
-- R4.4 en los tres caminos de alta. Un celular viejo guardado en otro
-- formato no coincide con nada, que es lo seguro: no fusiona.
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

create index if not exists persona_celular_idx
  on persona (celular)
  where celular is not null;

comment on index persona_celular_idx is
  'Dedup por celular (dedup.resolver, paso 3). No es único a propósito: un '
  'celular puede ser compartido, y el dedup lo manda a revisión.';

commit;
