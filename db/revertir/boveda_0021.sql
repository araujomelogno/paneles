-- ============================================================
--  Revertir boveda/0021 · volver a la `v_persona_convocable` de la 0014
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`: nada la aplica
-- solo. Es el rollback de `DESPLIEGUE - Fase 7 completa, Fase 8 y bug
-- convocable.md`, y se corre **solo después** de volver a desplegar el código
-- anterior: ese código pregunta `finalidad = %s`, y contra la vista nueva
-- (que tiene `finalidades[]`) falla.
--
-- Deja la bóveda exactamente como estaba con la 0020: la vista vuelve a
-- tener una fila por consentimiento —el bug vuelve con ella—, desaparece
-- `v_persona_finalidad_vigente` y las dos funciones del contrato vuelven al
-- texto de la 0016. Los cuerpos están copiados tal cual de la 0014 y la 0016.
--
-- Va en una transacción: si algo falla, no cambia nada y COLOQUIO conserva
-- el acceso que tenía.

begin;

drop view if exists v_persona_finalidad_vigente;
drop function if exists f_persona_finalidad_vigente();
drop view if exists v_persona_convocable;
drop function if exists f_persona_convocable();

-- De boveda/0014.
create function f_persona_convocable()
returns table (
  id_persona   uuid,
  finalidad    text,
  ref_estudio  uuid,
  sexo         text,
  localidad    text,
  tramo_etario text,
  edad         int)
language sql
stable
security definer
set search_path = public
as $$
  select
    p.id_persona,
    c.finalidad,
    c.ref_estudio,
    d.sexo,
    d.localidad,
    d.tramo_etario,
    d.edad
    from persona p
    join consentimiento c on c.id_persona = p.id_persona
                         and c.estado = 'vigente'
    left join v_demografia d on d.id_persona = p.id_persona
   where p.estado = 'activa'
     -- Quien tiene lápida ya no existe, aunque alguien conserve su id.
     and not exists (select 1 from persona_borrada b
                      where b.id_persona = p.id_persona);
$$;

create view v_persona_convocable as
select * from f_persona_convocable();

comment on view v_persona_convocable is
  'R5.1 — el gate de consentimiento, en la base. No expone ningún campo de '
  'PII: solo id_persona, la finalidad y los segmentadores.';

-- De boveda/0016: las dos funciones del contrato, con el criterio de antes.
create or replace function declarar_convocatoria(
    p_id_persona uuid,
    p_referencia text,
    p_vence_en   timestamptz)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  el_sistema text := sistema_de_la_conexion();
begin
  if p_referencia is null or length(trim(p_referencia)) = 0 then
    raise exception
      'Hace falta una referencia: es lo que distingue una convocatoria de '
      'otra y lo que permite auditar a cuál correspondió cada lectura.'
      using errcode = 'invalid_parameter_value';
  end if;

  -- Un vencimiento que ya pasó nace muerto, y lo más probable es que sea un
  -- error de zona horaria del llamador. Mejor que falle acá que quince
  -- minutos después, al pedir el contacto.
  if p_vence_en <= now() then
    raise exception
      'La convocatoria vence en el pasado (%). Una declaración vencida no '
      'habilita nada.', p_vence_en
      using errcode = 'invalid_parameter_value';
  end if;

  -- El tope. Sin él, «declarar» sería «tener acceso permanente»: bastaría
  -- una declaración a cien años para que el gate no volviera a aplicar nunca
  -- más sobre esa persona.
  if p_vence_en > now() + interval '60 days' then
    raise exception
      'Una convocatoria no puede declararse por más de 60 días.'
      using errcode = 'invalid_parameter_value';
  end if;

  -- El gate de consentimiento, también acá y no solo al leer el contacto.
  -- Podría omitirse —`contacto_para_convocatoria()` lo reaplica igual— y
  -- sería un error: dejaría a la bóveda guardando «el sistema X convocó a
  -- esta persona» de alguien que no consintió que lo contacten.
  if not exists (select 1 from v_persona_convocable
                  where id_persona = p_id_persona
                    and finalidad = 'contacto_participacion') then
    raise exception
      'La persona % no tiene consentimiento vigente de '
      'contacto_participacion, o ya no está activa: no se puede declarar '
      'una convocatoria suya.', p_id_persona
      using errcode = 'insufficient_privilege';
  end if;

  insert into convocatoria_externa (id_persona, sistema, referencia, vence_en)
  values (p_id_persona, el_sistema, p_referencia, p_vence_en)
  -- Reprogramar una sesión es volver a declarar la misma referencia con otra
  -- fecha. El tope de arriba ya se aplicó, así que esto no es una vía para
  -- estirar indefinidamente: cada extensión arranca de `now()`.
  on conflict (id_persona, sistema, referencia)
    do update set vence_en = excluded.vence_en,
                  declarada_en = now();
end;
$$;

create or replace function contacto_para_convocatoria(
    p_id_persona uuid,
    p_canal      text,
    p_motivo     text default 'convocatoria',
    p_actor      text default null)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  el_sistema text := sistema_de_la_conexion();
  el_dato    text;
  tiene_convocatoria boolean;
begin
  if p_canal not in ('email', 'celular') then
    raise exception 'Canal desconocido: %. Solo `email` o `celular`.', p_canal
      using errcode = 'invalid_parameter_value';
  end if;

  -- El gate, otra vez y en la misma transacción. No se confía en que el
  -- llamador haya consultado la vista antes.
  if not exists (
       select 1 from v_persona_convocable
        where id_persona = p_id_persona
          and finalidad = 'contacto_participacion') then
    raise exception
      'La persona % no tiene consentimiento vigente de '
      'contacto_participacion, o ya no está activa.', p_id_persona
      using errcode = 'insufficient_privilege';
  end if;

  -- Tener el gate no alcanza: hay que tener a quién convocar. Sin esto, un
  -- consumidor podría barrer la agenda entera de a una persona por vez.
  --
  -- Cada sistema declara su convocatoria a su manera, y por eso el chequeo
  -- se ramifica por sistema en vez de mirar siempre las tablas de `paneles`:
  --
  --   · `paneles` la tiene en la base —una `participacion` en una `encuesta`
  --     abierta—, porque es el dueño de esas tablas;
  --   · un consumidor externo la **declara** con `declarar_convocatoria()`,
  --     porque sus convocatorias viven en su propio store y la bóveda no las
  --     puede ver.
  --
  -- Las dos ramas son el mismo gate: hace falta un motivo, y el motivo queda
  -- escrito en algún lado.
  select exists (
    select 1 from participacion pa
      join encuesta e on e.id = pa.encuesta_id
     where el_sistema = 'paneles'
       and pa.id_persona = p_id_persona
       and e.estado <> 'cerrada'
     union all
    select 1 from convocatoria_externa c
     where c.sistema = el_sistema
       and c.id_persona = p_id_persona
       and c.vence_en > now())
    into tiene_convocatoria;

  if not tiene_convocatoria then
    raise exception
      'La persona % no tiene una convocatoria activa en el sistema «%»: no '
      'hay motivo para leer su contacto.', p_id_persona, el_sistema
      using errcode = 'insufficient_privilege';
  end if;

  execute format('select %I from persona where id_persona = $1', p_canal)
     into el_dato using p_id_persona;

  -- La auditoría va en la **misma transacción** en que se devuelve el dato:
  -- si no se puede escribir, no se entrega. Se registra también el canal que
  -- vino vacío —el intento existió— pero no se registra nada cuando el gate
  -- rechazó, porque ahí no se entregó ningún dato.
  insert into reidentificacion (id_persona, actor_uid, actor_email, motivo,
                                contexto, sistema)
  -- `session_user` y no `current_user`: adentro de un `security definer`
  -- `current_user` es el dueño de la función, con lo que toda lectura
  -- quedaría firmada por el dueño y la auditoría no diría nada.
  values (p_id_persona, coalesce(p_actor, session_user), p_actor,
          coalesce(p_motivo, 'convocatoria'),
          jsonb_build_object('canal', p_canal,
                             'vacio', el_dato is null,
                             'via', 'contacto_para_convocatoria'),
          el_sistema);

  -- Vacío explícito, no error: que la persona no tenga celular cargado es
  -- información, no una falla.
  return coalesce(el_dato, '');
end;
$$;

-- El `drop` se llevó los privilegios de la vista y la función: se vuelven a
-- dar los de la 0014. Las dos funciones de la 0016 conservan los suyos
-- (`create or replace` no los toca).
revoke execute on function f_persona_convocable() from public;
grant select on v_persona_convocable to coloquio_app;
grant execute on function f_persona_convocable() to coloquio_app;

commit;
