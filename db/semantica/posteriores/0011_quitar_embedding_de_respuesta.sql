-- ============================================================
--  STORE SEMÁNTICO — Migración 0011 (POSTERIOR: no se aplica sola)
--  R-UE.5 · Se quita `respuesta.embedding`
--  (specs/SPEC_unidades_de_evidencia.md, D73)
-- ============================================================
--
-- La 0010 movió el vector a `unidad_evidencia` y **conservó**
-- `respuesta.embedding` hasta verificar que todo funciona sobre unidades
-- (R-UE.5). Esta migración es ese «recién después»: vive fuera de
-- `db/semantica/` a propósito, para que ni `scripts/pg_pruebas.sh` ni un
-- `for f in db/semantica/*.sql` la apliquen sin que alguien lo decida.
--
-- Aplicarla cuando, en producción y con el código de R-UE desplegado:
--   · las consultas exploratorias y completas devolvieron lo esperado,
--   · `diagnosticar_consulta.py recall-indice` midió recall sobre unidades,
--   · y hubo al menos una ingesta y un reproceso sin errores.
-- (ver «DESPLIEGUE - R-UE unidades de evidencia.md», paso 9).
--
-- Después de esta migración, **volver atrás el código ya no es posible** sin
-- re-embeber: el código anterior lee `respuesta.embedding`.
--
-- Libera el índice HNSW de respuestas (~12 MB con el corpus de hoy) y el
-- espacio de 21.340 vectores.
begin;

do $$
begin
  if to_regclass('unidad_evidencia') is null then
    raise exception 'Falta la semantica/0010: sin unidades, quitar el vector '
                    'de respuesta borraría el corpus semántico.';
  end if;
  if exists (select 1 from respuesta where unidad_id is null) then
    raise exception 'Hay respuestas sin unidad: no se quita nada.';
  end if;
end $$;

-- El trigger de transición lee y escribe la columna. Se reemplaza **antes**
-- de quitarla por una versión que solo completa la huella y resuelve la
-- unidad por huella (sin crearla: sin vector no hay con qué), así la función
-- que declara `esquema.py` para la 0010 sigue existiendo.
create or replace function respuesta_asegurar_unidad() returns trigger
language plpgsql as $$
begin
  if new.hash_texto is null then
    new.hash_texto := encode(sha256(convert_to(new.texto_embebido, 'UTF8')), 'hex');
  end if;
  if new.unidad_id is null then
    select id into new.unidad_id
      from unidad_evidencia where hash_texto = new.hash_texto;
  end if;
  return new;
end $$;

comment on function respuesta_asegurar_unidad() is
  'R-UE · Completa la huella y resuelve la unidad por huella. Desde la 0011 '
  'no crea unidades: el vector lo pone semantica.escribir_respuestas.';

-- La compatibilidad de la 0010 con el código anterior ya no sirve: después
-- de esta migración ese código no puede volver.
drop index if exists veredicto_unidad_clave_r_cs;

-- La vista de procedencia selecciona la columna: se recrea con el vector de
-- la unidad, para que su contrato (incluida la columna `embedding`) no
-- cambie para quien la lea.
drop view if exists v_respuesta_estudio;
drop index if exists respuesta_embedding_idx;
alter table respuesta drop column if exists embedding;

create view v_respuesta_estudio as
select r.id             as respuesta_id,
       i.id_persona,
       c.ref_estudio,
       c.nombre         as estudio,
       c.fecha_campo,
       p.codigo         as pregunta_codigo,
       p.texto          as pregunta_texto,
       p.tipo           as pregunta_tipo,
       r.valor_texto,
       r.texto_embebido,
       u.embedding,
       r.unidad_id
  from respuesta r
  join unidad_evidencia u on u.id = r.unidad_id
  join individuo i    on i.id = r.individuo_id
  join pregunta p     on p.id = r.pregunta_id
  join cuestionario c on c.id = p.cuestionario_id;

comment on view v_respuesta_estudio is
  'Respuestas con su procedencia (estudio y pregunta) resuelta. Sin PII: la '
  'persona figura solo por su id_persona opaco. El vector es el de su unidad '
  'de evidencia (R-UE).';

commit;
