-- ============================================================
--  0021 · `v_persona_convocable` devuelve una fila por persona
-- ============================================================
--
-- Corrige specs/BUG_v_persona_convocable_duplica.md.
--
-- ── Qué pasaba ──
--
-- La `0014` armó la superficie con un `join` contra `consentimiento`, así que
-- la vista devolvía **una fila por consentimiento vigente**, no por persona.
-- Con dos finalidades vigentes —`contacto_participacion` y `uso_semantico`,
-- que es el caso normal desde que el alta pide las dos— cada persona aparecía
-- dos veces. Y con un re-otorgamiento (una versión nueva del texto, que
-- agrega fila sin pisar el historial) aparecía tres.
--
-- En la primera carga real: 1008 personas, 2016 filas. COLOQUIO contaba la
-- muestra disponible al doble, calculaba cuotas sobre un universo inflado y
-- un listado para invitar traía a cada persona repetida.
--
-- ── Por qué `exists` y no `distinct` ──
--
-- El `distinct` tapa el síntoma: el día que la vista sume una columna que
-- varíe entre las filas duplicadas, deja de colapsarlas y el bug vuelve, otra
-- vez en silencio. El `exists` dice lo que el gate quiere decir —«existe un
-- consentimiento vigente»— y no multiplica nunca.
--
-- ── Qué cambia en la forma, y por qué ──
--
-- La vista vieja tenía las columnas `finalidad` y `ref_estudio`, que son
-- justamente las que variaban entre las filas repetidas. Una fila por persona
-- no puede llevarlas sueltas, así que:
--
--   · `v_persona_convocable` queda en **una fila por persona**, con
--     `finalidades text[]`: las finalidades de ámbito persona o panel que esa
--     persona tiene vigentes. El gate se expresa igual que antes, con otra
--     sintaxis:  `where 'contacto_participacion' = any(finalidades)`.
--     El criterio no cambia: aparece quien está activa, sin lápida, y con al
--     menos un consentimiento vigente; filtra por la finalidad quien consume.
--
--   · Las finalidades de **ámbito estudio** (`grabacion_av`,
--     `moderacion_automatizada`, `difusion_verbatim`) no entran en ese
--     arreglo. Un `grabacion_av` en la lista daría a entender que esa
--     persona se puede grabar en cualquier estudio, que es exactamente el
--     error que el trigger de la `0013` existe para impedir. Esas se consultan
--     en `v_persona_finalidad_vigente`, que tiene una fila por
--     `(id_persona, finalidad, ref_estudio)` —esa es su clave, y es la forma
--     honesta de una relación que es por consentimiento y no por persona—.
--
-- El cambio de columnas es deliberadamente **ruidoso** para un consumidor
-- que siga filtrando `where finalidad = …`: la consulta falla con «column
-- finalidad does not exist» en vez de devolver algo distinto sin avisar. La
-- receta para COLOQUIO está en el documento de despliegue.

-- ── Se puede correr dos veces ──
--
-- El ensayo del despliegue lo mostró: con la primera versión de esta
-- migración, una segunda corrida con `ON_ERROR_STOP` tiraba la vista, la
-- recreaba y fallaba antes de los `grant` del final, y **COLOQUIO perdía el
-- acceso a la superficie**. Ahora va entera en una transacción —falla toda o
-- no falla— y cada sentencia es re-ejecutable: los objetos nuevos se crean con
-- `create or replace`, los que cambian de forma se tiran y se rehacen, y los
-- `grant` se repiten. Correrla de nuevo deja todo exactamente igual.
begin;

-- ============================================================
--  1 · Lo que se reemplaza
-- ============================================================
-- La vista depende de la función, y la función cambia de forma (otro
-- `returns table`): `create or replace` no puede cambiar el tipo de retorno,
-- así que se tiran las dos y se rehacen.
--
-- Las funciones de la `0016` que consultan la vista (`declarar_convocatoria`,
-- `contacto_para_convocatoria`) no dependen de ella para Postgres —plpgsql
-- resuelve sus consultas al ejecutar—, así que el `drop` no las arrastra.
-- Se reescriben más abajo, porque las dos filtraban por la columna
-- `finalidad` que deja de existir.
drop view v_persona_convocable;
drop function f_persona_convocable();

-- ============================================================
--  2 · Una fila por persona
-- ============================================================
-- `security definer` por lo mismo que en la `0014`: la demografía sale de
-- `f_atributo_persona`, cuyo `execute` no se le presta al consumidor a través
-- de la vista, y otorgárselo directo le daría la demografía de todo el mundo.
create function f_persona_convocable()
returns table (
  id_persona   uuid,
  finalidades  text[],
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
    -- El arreglo es un subselect correlacionado y no un `join` + `group by`:
    -- así ninguna columna que se le agregue a esta vista puede volver a
    -- multiplicar las filas.
    array(select distinct c.finalidad
            from consentimiento c
           where c.id_persona = p.id_persona
             and c.estado = 'vigente'
             and c.ref_estudio is null
           order by c.finalidad) as finalidades,
    d.sexo,
    d.localidad,
    d.tramo_etario,
    d.edad
    from persona p
    -- `v_demografia` agrupa por `id_persona`: una fila por persona, así que
    -- este `left join` no multiplica.
    left join v_demografia d on d.id_persona = p.id_persona
   where p.estado = 'activa'
     and exists (select 1 from consentimiento c
                  where c.id_persona = p.id_persona
                    and c.estado = 'vigente'
                    and c.ref_estudio is null)
     -- Quien tiene lápida ya no existe, aunque alguien conserve su id.
     and not exists (select 1 from persona_borrada b
                      where b.id_persona = p.id_persona);
$$;

create view v_persona_convocable as
select * from f_persona_convocable();

comment on view v_persona_convocable is
  'R5.1 — el gate de consentimiento, en la base. Una fila por persona '
  '(0021): `finalidades` lista las vigentes de ámbito persona o panel, y se '
  'filtra con `''contacto_participacion'' = any(finalidades)`. No expone '
  'ningún campo de PII.';

comment on function f_persona_convocable() is
  'La superficie de convocables. Una fila por persona: el consentimiento se '
  'evalúa con `exists`, nunca con un `join` que multiplique (0021).';

-- ============================================================
--  3 · Una fila por consentimiento vigente, a propósito
-- ============================================================
-- Para las finalidades de ámbito estudio, y para quien necesite preguntar
-- «¿tiene esta finalidad vigente para este estudio?». La clave de esta
-- relación es `(id_persona, finalidad, ref_estudio)`, y el `distinct` acá no
-- es un parche: es la definición. Las columnas son exactamente la clave, así
-- que no hay ninguna que pueda variar entre filas repetidas y romperlo. Lo
-- que colapsa son los re-otorgamientos de la misma finalidad con otra
-- versión del texto, que para el gate son el mismo permiso.
create or replace function f_persona_finalidad_vigente()
returns table (
  id_persona  uuid,
  finalidad   text,
  ref_estudio uuid)
language sql
stable
security definer
set search_path = public
as $$
  select distinct c.id_persona, c.finalidad, c.ref_estudio
    from consentimiento c
    join persona p on p.id_persona = c.id_persona
   where c.estado = 'vigente'
     and p.estado = 'activa'
     and not exists (select 1 from persona_borrada b
                      where b.id_persona = c.id_persona);
$$;

create or replace view v_persona_finalidad_vigente as
select * from f_persona_finalidad_vigente();

comment on view v_persona_finalidad_vigente is
  '0021 — una fila por (id_persona, finalidad, ref_estudio) vigente, con el '
  'mismo gate de persona activa y sin lápida. Es donde se consultan las '
  'finalidades de ámbito estudio, que no entran en `v_persona_convocable`.';

-- ============================================================
--  4 · Las funciones de la 0016 que filtraban por `finalidad`
-- ============================================================
-- Lo único que cambia en cada una es el `exists` del gate. Se repiten
-- enteras porque `create or replace` reemplaza el cuerpo completo, y
-- `create or replace` conserva los privilegios: los `revoke` y `grant` de la
-- `0014` y la `0016` siguen valiendo.

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

  if p_vence_en <= now() then
    raise exception
      'La convocatoria vence en el pasado (%). Una declaración vencida no '
      'habilita nada.', p_vence_en
      using errcode = 'invalid_parameter_value';
  end if;

  if p_vence_en > now() + interval '60 days' then
    raise exception
      'Una convocatoria no puede declararse por más de 60 días.'
      using errcode = 'invalid_parameter_value';
  end if;

  -- El gate de consentimiento. Desde la 0021 la vista tiene una fila por
  -- persona y la finalidad se pregunta sobre el arreglo.
  if not exists (select 1 from v_persona_convocable
                  where id_persona = p_id_persona
                    and 'contacto_participacion' = any(finalidades)) then
    raise exception
      'La persona % no tiene consentimiento vigente de '
      'contacto_participacion, o ya no está activa: no se puede declarar '
      'una convocatoria suya.', p_id_persona
      using errcode = 'insufficient_privilege';
  end if;

  insert into convocatoria_externa (id_persona, sistema, referencia, vence_en)
  values (p_id_persona, el_sistema, p_referencia, p_vence_en)
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

  -- El gate, otra vez y en la misma transacción. Con la vista en una fila
  -- por persona, este `exists` es uno solo aunque la persona tenga varias
  -- finalidades vigentes: no hay forma de que una entrega se cuente doble.
  if not exists (
       select 1 from v_persona_convocable
        where id_persona = p_id_persona
          and 'contacto_participacion' = any(finalidades)) then
    raise exception
      'La persona % no tiene consentimiento vigente de '
      'contacto_participacion, o ya no está activa.', p_id_persona
      using errcode = 'insufficient_privilege';
  end if;

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

  -- Una fila de auditoría por entrega: un `insert ... values`, no un
  -- `insert ... select` sobre la vista, así que no depende de su
  -- cardinalidad.
  insert into reidentificacion (id_persona, actor_uid, actor_email, motivo,
                                contexto, sistema)
  values (p_id_persona, coalesce(p_actor, session_user), p_actor,
          coalesce(p_motivo, 'convocatoria'),
          jsonb_build_object('canal', p_canal,
                             'vacio', el_dato is null,
                             'via', 'contacto_para_convocatoria'),
          el_sistema);

  return coalesce(el_dato, '');
end;
$$;

-- ============================================================
--  5 · Privilegios
-- ============================================================
-- Las funciones nuevas nacen con `execute` a `public`. Se revoca primero y se
-- otorga después, igual que en la `0014`: sin esto cualquier rol que se
-- conecte leería la superficie sin estar registrado como consumidor.
revoke execute on function f_persona_convocable()        from public;
revoke execute on function f_persona_finalidad_vigente() from public;

-- El `drop` se llevó los `grant` de la vista y la función viejas: hay que
-- volver a darlos. `scripts/verificar_coloquio.py` rompe si falta alguno
-- («la lista blanca promete lectura que el rol no tiene»).
grant select on v_persona_convocable          to coloquio_app;
grant select on v_persona_finalidad_vigente   to coloquio_app;
grant execute on function f_persona_convocable()        to coloquio_app;
grant execute on function f_persona_finalidad_vigente() to coloquio_app;

commit;
