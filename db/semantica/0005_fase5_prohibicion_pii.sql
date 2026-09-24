-- Fase 5 · R5.5 — La regla dura #1, con dientes.
--
-- «La PII nunca se escribe en el store semántico» es la invariante central de
-- la plataforma, y hasta ahora vivía en dos lugares de Python:
-- `pii.validar_sin_pii()` para los payloads y `pii.auditar_esquema()` para el
-- DDL. Las dos son correctas y las dos **solo detectan cuando alguien las
-- ejecuta**. Con un segundo sistema escribiendo de este lado, eso deja de
-- alcanzar: basta un `alter table` por `psql` para que la invariante se rompa
-- sin que ningún control se entere.
--
-- Un `check` no sirve: la regla es sobre **nombres de columna**, no sobre
-- valores. El mecanismo que corresponde es un event trigger.
--
-- Tres niveles, que protegen cosas distintas y por eso ninguno sobra:
--
--   1 · Este event trigger. Un `alter table` que agregue una columna con
--       nombre de PII falla **en el momento**, lo intente quien lo intente y
--       por el camino que sea.
--   2 · `scripts/verificar_esquema.py --pii`, que lee los archivos de
--       migración. El event trigger protege la base, pero no atrapa una
--       migración mal escrita que todavía no se aplicó, y ésa es la que llega
--       a un pull request.
--   3 · `pii.validar_sin_pii()`, que sigue cubriendo lo que el DDL no puede
--       ver: una clave de PII adentro de un `jsonb`.

-- ============================================================
--  1 · El catálogo, en la base
-- ============================================================
-- Hasta ahora la lista era un `frozenset` en `panel_api/pii.py`. El día que
-- COLOQUIO escriba embeddings va a necesitar la misma lista sin importar ese
-- módulo, así que la fuente pasa a ser la base y Python la espeja. Hay una
-- prueba que falla si divergen.

create table campo_pii (
  campo text primary key,
  nota  text
);

comment on table campo_pii is
  'R5.5 — nombres de columna que delatan PII. El event trigger de esta '
  'migración los rechaza, y `panel_api.pii.CAMPOS_PII` es su espejo.';

insert into campo_pii (campo, nota) values
  ('documento',        'identificador de la bóveda'),
  ('cedula',           'sinónimo de documento'),
  ('ci',               'sinónimo de documento'),
  ('dni',              'sinónimo de documento'),
  ('pasaporte',        'sinónimo de documento'),
  ('rut',              'sinónimo de documento'),
  ('nombre',           'con excepciones: ver `excepcion_pii`'),
  ('nombres',          'sinónimo de nombre'),
  ('apellido',         'sinónimo de nombre'),
  ('apellidos',        'sinónimo de nombre'),
  ('nombre_completo',  'sinónimo de nombre'),
  ('email',            'canal de contacto'),
  ('correo',           'sinónimo de email'),
  ('mail',             'sinónimo de email'),
  ('e_mail',           'sinónimo de email'),
  ('celular',          'canal de contacto'),
  ('telefono',         'sinónimo de celular'),
  ('movil',            'sinónimo de celular'),
  ('whatsapp',         'sinónimo de celular'),
  ('direccion',        'ubicación exacta'),
  ('domicilio',        'sinónimo de dirección'),
  ('fecha_nacimiento', 'fecha exacta; del lado semántico solo va el tramo'),
  ('fecha_nac',        'sinónimo de fecha_nacimiento'),
  ('nacimiento',       'sinónimo de fecha_nacimiento'),
  ('fnac',             'sinónimo de fecha_nacimiento'),
  ('contacto',         'texto libre de contacto'),
  ('observaciones',    'texto libre: es donde termina la PII que no tiene campo');

-- Las excepciones legítimas. `nombre` es el nombre del **cuestionario**, de la
-- **pregunta** o de la **serie**: metadatos del estudio, no de la persona.
--
-- Quién creó o editó una serie sí es una persona, y ese rastro no va de este
-- lado: vive en `serie_auditoria`, en la bóveda.
create table excepcion_pii (
  relacion text not null,
  campo    text not null references campo_pii(campo),
  motivo   text not null,
  primary key (relacion, campo)
);

insert into excepcion_pii (relacion, campo, motivo) values
  ('cuestionario', 'nombre', 'el nombre del estudio, no el de una persona'),
  ('pregunta',     'nombre', 'el enunciado corto de la pregunta'),
  ('serie',        'nombre', 'el nombre de la medición comparable entre olas');

-- ============================================================
--  2 · El event trigger
-- ============================================================
-- Se dispara en `ddl_command_end` y no en `ddl_command_start` a propósito: al
-- empezar el comando todavía no existe la columna, así que no hay nada que
-- inspeccionar. Al terminar sí, y el `raise` aborta la transacción, con lo
-- que el `alter table` no queda aplicado. El efecto para quien lo ejecuta es
-- el mismo —falla y no hay columna—, y el código es mucho más simple que
-- analizar el texto del comando.

create function prohibir_pii_en_ddl()
returns event_trigger
language plpgsql as $$
declare
  comando record;
  hallazgo record;
begin
  for comando in select * from pg_event_trigger_ddl_commands() loop
    -- Solo relaciones del esquema `public`. Las de los catálogos y las que
    -- crean las extensiones no son nuestras y no se juzgan.
    continue when comando.classid <> 'pg_class'::regclass;
    continue when comando.schema_name is distinct from 'public';

    for hallazgo in
      select c.relname, a.attname
        from pg_class c
        join pg_attribute a on a.attrelid = c.oid
        join campo_pii p on p.campo = a.attname
       where c.oid = comando.objid
         and c.relkind in ('r', 'p', 'm', 'v', 'f')
         and a.attnum > 0
         and not a.attisdropped
         and not exists (select 1 from excepcion_pii e
                          where e.relacion = c.relname
                            and e.campo = a.attname)
    loop
      raise exception
        'Regla dura #1: «%.%» es PII y el store semántico no la guarda.',
        hallazgo.relname, hallazgo.attname
        using errcode = 'insufficient_privilege',
              detail  = 'Al store semántico solo viaja `id_persona`. '
                        'Los atributos de la persona son autoritativos en la '
                        'bóveda.',
              hint    = 'Si el nombre es legítimo —metadatos del estudio y no '
                        'de la persona— la excepción se declara en '
                        '`excepcion_pii`, en una migración, con su motivo.';
    end loop;
  end loop;
end;
$$;

comment on function prohibir_pii_en_ddl() is
  'R5.5 — nivel 1: la base rechaza una columna con nombre de PII en el '
  'momento en que se intenta crear, venga por donde venga.';

create event trigger pii_prohibida
  on ddl_command_end
  when tag in ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO',
               'ALTER TABLE', 'CREATE VIEW', 'CREATE MATERIALIZED VIEW',
               'CREATE FOREIGN TABLE', 'ALTER FOREIGN TABLE')
  execute function prohibir_pii_en_ddl();

-- ============================================================
--  3 · Comprobación de que el esquema actual pasa limpio
-- ============================================================
-- Si el store semántico de hoy ya tuviera una columna prohibida, el event
-- trigger no la vería —solo mira lo que se crea de acá en adelante— y la
-- migración habría instalado un guardia que mira para otro lado. Así que se
-- revisa una vez, acá, y la migración se niega a aplicar si hay algo.
do $$
declare
  sucias text;
begin
  select string_agg(format('%I.%I', c.relname, a.attname), ', ')
    into sucias
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    join pg_attribute a on a.attrelid = c.oid
    join campo_pii p on p.campo = a.attname
   where n.nspname = 'public'
     and c.relkind in ('r', 'p', 'm', 'v', 'f')
     and a.attnum > 0
     and not a.attisdropped
     and not exists (select 1 from excepcion_pii e
                      where e.relacion = c.relname and e.campo = a.attname);
  if sucias is not null then
    raise exception
      'El store semántico ya tiene columnas de PII: %. Hay que resolverlas '
      'antes de instalar el guardia.', sucias;
  end if;
end;
$$;
