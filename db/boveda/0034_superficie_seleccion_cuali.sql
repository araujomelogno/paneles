-- 0034 · La superficie de selección cualitativa (R5.9 a R5.13, D84).
--
-- `specs/ADDENDUM_paneles_superficie_cuali.md`. La pantalla de selección de
-- COLOQUIO necesita cuatro cosas que la superficie de la Fase 5 no da:
-- filtrar por cualquier atributo del catálogo dinámico, mostrar atributos
-- como columnas, abrir una ficha y ver de quién se trata antes de decidir.
--
-- Todo es aditivo. No se tocan `f_persona_convocable()`,
-- `v_persona_convocable`, `declarar_convocatoria()` ni
-- `contacto_para_convocatoria()`: lo que ya anda sigue andando.
--
-- Cinco piezas, todas detrás del gate de consentimiento:
--
--   v_atributo_catalogo            — el vocabulario (R5.9)
--   atributos_de_personas()        — los valores de un conjunto (R5.10)
--   f_persona_convocable_filtrada()— convocables por atributo (R5.11)
--   ficha_candidato()              — la ficha seudónima (R5.12)
--   identificar_candidatos()       — nombre y contacto, auditado (R5.13)
--
-- Una transacción, y re-ejecutable: `create or replace`, `on conflict do
-- nothing`, y `revoke` antes de cada `grant`. Es la lección de la 0021: una
-- segunda corrida que falla a mitad de camino dejó a COLOQUIO sin superficie.

begin;

-- ---------- 0 · El motivo de auditoría de la selección ------------------
-- §2 del addendum: «lo miró para elegir» tiene que poder separarse de «lo
-- miró para llamar». Por eso un motivo propio. El catálogo no tiene FK
-- (0020) y su espejo en Python es `auditoria.MOTIVOS_REIDENTIFICACION`.

insert into motivo_reidentificacion (codigo, etiqueta, descripcion, expone_pii, orden)
values ('seleccion_cuali', 'Selección de candidatos para un estudio cualitativo',
        'COLOQUIO vio nombre y contacto de los candidatos tildados de una '
        'sesión, antes de convocarlos, para decidir a quién llamar. La '
        'referencia de la sesión va en el contexto.',
        true, 70)
on conflict (codigo) do nothing;

-- ---------- 1 · R5.9 · El catálogo de atributos ------------------------
-- Vocabulario, no dato de persona: sin PII. `es_especial` se expone para que
-- el consumidor pueda respetar la marca (Ley 18.331); si la bóveda no la
-- manda, no tiene cómo.

create or replace view v_atributo_catalogo as
select a.clave,
       a.etiqueta,
       a.tipo,
       a.orden,
       a.es_especial,
       coalesce(
         (select jsonb_agg(jsonb_build_object('clave', c.clave,
                                              'etiqueta', c.etiqueta,
                                              'orden', c.orden)
                           order by c.orden, c.clave)
            from atributo_categoria c
           where c.atributo_id = a.id and c.activo),
         '[]'::jsonb) as categorias
  from atributo_demografico a
 where a.activo;

comment on view v_atributo_catalogo is
  'R5.9 — los atributos activos del catálogo, con sus categorías activas en '
  'orden. Vocabulario, no dato de persona. Un atributo nuevo aparece sin '
  'desplegar nada; uno desactivado desaparece.';

-- ---------- 2 · R5.10 · Atributos de un conjunto de personas ------------
-- `f_atributo_persona(momento)` acotada a un conjunto de ids y al gate. El
-- `momento` se conserva: los valores envejecen (0010) y una sesión vieja
-- tiene que poder mostrar los de entonces.
--
-- Devuelve solo las filas que existen: un atributo ausente no viene con
-- valor nulo, no viene. No saber el nivel educativo de alguien es
-- información; mezclarlo con un «sin dato» declarado es peor que no
-- mostrarlo.

create or replace function atributos_de_personas(
    p_ids     uuid[],
    p_momento timestamptz default now())
returns table (id_persona uuid, atributo text, valor text,
               etiqueta_valor text, valor_num numeric,
               valor_fecha date, procedencia text)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if p_ids is null then
    raise exception 'Hace falta la lista de personas.'
      using errcode = 'invalid_parameter_value';
  end if;
  -- Para que no sirva de volcado del padrón.
  if cardinality(p_ids) > 500 then
    raise exception 'Se piden % personas y el tope es 500 por llamada.',
                    cardinality(p_ids)
      using errcode = 'program_limit_exceeded';
  end if;

  return query
    with pedidas as materialized (
      -- El gate, una vez: solo quien está en `v_persona_convocable`.
      select v.id_persona
        from v_persona_convocable v
       where v.id_persona = any(p_ids))
    select f.id_persona, f.atributo, f.valor, f.etiqueta_valor,
           f.valor_num, f.valor_fecha, f.procedencia
      from f_atributo_persona(coalesce(p_momento, now())) f
      join pedidas p on p.id_persona = f.id_persona
      join atributo_demografico a on a.id = f.atributo_id and a.activo
     order by f.id_persona, a.orden, a.clave;
end;
$$;

comment on function atributos_de_personas(uuid[], timestamptz) is
  'R5.10 — los atributos de hasta 500 personas, a un momento. Solo de '
  'quienes están en v_persona_convocable, y solo las filas que existen: un '
  'atributo sin valor no aparece.';

-- ---------- 3 · R5.11 · Convocables filtrados por atributo ---------------
-- La pieza que no es mecánica. El filtro ocurre en la base: el panel tiene
-- decenas de miles de personas.
--
--   {"sexo": {"en": ["F"]},
--    "edad": {"min": 35, "max": 44},
--    "fecha_alta": {"desde": "2024-01-01"}}
--
-- · `en` para categóricos y derivados; `min`/`max` para numéricos, y para
--   los derivados con valor numérico (la edad); `desde`/`hasta` para fechas.
-- · Varias categorías del mismo atributo: o. Atributos distintos: y.
-- · Una clave fuera del catálogo, un atributo inactivo, un operador que no
--   corresponde al tipo o una categoría que no existe **son error**. Un
--   filtro que no se aplicó y nadie avisó devuelve más gente de la pedida,
--   que es la peor falla posible en un muestreo.
-- · Devuelve `(id_persona, finalidades)` y nada más. Los valores, R5.10:
--   así agregar una columna en COLOQUIO no vuelve a ejecutar la búsqueda.
--
-- Cómo se filtra. Se parte del gate (`v_persona_convocable` con la
-- finalidad) y se achica el conjunto de a un atributo:
--   · un atributo cargado (categórico, numérico, fecha) se pregunta con un
--     `exists` correlacionado sobre `persona_atributo`, con la vigencia de
--     la 0010, que usa `persona_atributo_por_atributo`. Un `exists` y no un
--     `join`: un `join` que multiplica filas es la lección de la 0021.
--   · un derivado (edad, tramo etario) no está en `persona_atributo`: sale
--     de la fecha de nacimiento o de una edad declarada envejecida, y esa
--     precedencia tiene una sola implementación, `f_atributo_persona`. Se
--     pregunta ahí, una vez por atributo.

create or replace function f_persona_convocable_filtrada(
    p_finalidad text,
    p_filtros   jsonb,
    p_limite    int default 500)
returns table (id_persona uuid, finalidades text[])
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  base       jsonb;      -- id_persona → finalidades, del gate
  ids        uuid[];
  filtro     record;
  atr        record;
  cond       jsonb;
  operadores text[];
  permitidos text[];
  en_        text[];
  minimo     numeric;
  maximo     numeric;
  desde_     date;
  hasta_     date;
  categorias text[];
  ajenas     text[];
begin
  if p_finalidad is null or not exists (
       select 1 from finalidad_consentimiento where codigo = p_finalidad) then
    raise exception 'Finalidad desconocida: %.', p_finalidad
      using errcode = 'invalid_parameter_value';
  end if;
  if p_limite is null or p_limite < 1 or p_limite > 5000 then
    raise exception 'El límite tiene que estar entre 1 y 5000 (vino %).', p_limite
      using errcode = 'invalid_parameter_value';
  end if;
  p_filtros := coalesce(p_filtros, '{}'::jsonb);
  if jsonb_typeof(p_filtros) <> 'object' then
    raise exception 'Los filtros van en un objeto {clave: condición}.'
      using errcode = 'invalid_parameter_value';
  end if;

  -- El gate, una sola vez.
  select coalesce(jsonb_object_agg(v.id_persona::text, to_jsonb(v.finalidades)),
                  '{}'::jsonb),
         coalesce(array_agg(v.id_persona), '{}')
    into base, ids
    from v_persona_convocable v
   where p_finalidad = any(v.finalidades);

  for filtro in select f.key as clave, f.value as condicion
                  from jsonb_each(p_filtros) f
                 order by f.key loop
    select a.id, a.clave, a.tipo, a.activo into atr
      from atributo_demografico a where a.clave = filtro.clave;
    if not found then
      raise exception 'El atributo «%» no está en el catálogo.', filtro.clave
        using errcode = 'invalid_parameter_value';
    end if;
    if not atr.activo then
      raise exception 'El atributo «%» está dado de baja: no se puede filtrar por él.',
                      filtro.clave
        using errcode = 'invalid_parameter_value';
    end if;

    cond := filtro.condicion;
    if jsonb_typeof(cond) <> 'object' or cond = '{}'::jsonb then
      raise exception 'La condición de «%» tiene que ser un objeto con un operador.',
                      filtro.clave
        using errcode = 'invalid_parameter_value';
    end if;
    operadores := array(select jsonb_object_keys(cond));
    permitidos := case atr.tipo
                    when 'categorico' then array['en']
                    when 'numerico'   then array['min', 'max']
                    when 'fecha'      then array['desde', 'hasta']
                    when 'derivado'   then array['en', 'min', 'max']
                  end;
    if not operadores <@ permitidos then
      raise exception 'El atributo «%» es %: admite %, y vino %.',
                      filtro.clave, atr.tipo, permitidos, operadores
        using errcode = 'invalid_parameter_value';
    end if;

    -- Los valores de cada operador.
    en_ := null; minimo := null; maximo := null; desde_ := null; hasta_ := null;
    begin
      if cond ? 'en' then
        if jsonb_typeof(cond->'en') <> 'array' or jsonb_array_length(cond->'en') = 0 then
          raise exception using message = 'en';
        end if;
        en_ := array(select jsonb_array_elements_text(cond->'en'));
      end if;
      minimo := (cond->>'min')::numeric;
      maximo := (cond->>'max')::numeric;
      desde_ := (cond->>'desde')::date;
      hasta_ := (cond->>'hasta')::date;
    exception when others then
      raise exception 'La condición de «%» no se puede leer: %.', filtro.clave, cond
        using errcode = 'invalid_parameter_value';
    end;

    -- Una categoría que no existe tampoco se ignora: «terciaria» por
    -- «terciario» no puede dar cero personas sin que nadie lo diga.
    if en_ is not null then
      categorias := array(select c.clave from atributo_categoria c
                           where c.atributo_id = atr.id and c.activo);
      if cardinality(categorias) > 0 then
        ajenas := array(select x from unnest(en_) x where not x = any(categorias));
        if cardinality(ajenas) > 0 then
          raise exception 'El atributo «%» no tiene la(s) categoría(s) %.',
                          filtro.clave, ajenas
            using errcode = 'invalid_parameter_value';
        end if;
      end if;
    end if;

    if atr.tipo = 'derivado' then
      ids := array(
        select distinct f.id_persona
          from f_atributo_persona(now()) f
         where f.atributo_id = atr.id
           and f.id_persona = any(ids)
           and (en_ is null or f.valor = any(en_))
           and (minimo is null or f.valor_num >= minimo)
           and (maximo is null or f.valor_num <= maximo));
    else
      ids := array(
        select x from unnest(ids) x
         where exists (
           select 1
             from persona_atributo pa
             left join atributo_categoria c on c.id = pa.categoria_id
            where pa.id_persona = x
              and pa.atributo_id = atr.id
              and pa.desde <= now() and (pa.hasta is null or now() < pa.hasta)
              and (en_ is null or c.clave = any(en_))
              and (minimo is null or pa.valor_num >= minimo)
              and (maximo is null or pa.valor_num <= maximo)
              and (desde_ is null or pa.valor_fecha >= desde_)
              and (hasta_ is null or pa.valor_fecha <= hasta_)));
    end if;

    exit when cardinality(ids) = 0;
  end loop;

  return query
    select x, array(select jsonb_array_elements_text(base -> x::text))
      from unnest(ids) x
     order by x
     limit p_limite;
end;
$$;

comment on function f_persona_convocable_filtrada(text, jsonb, int) is
  'R5.11 — convocables con la finalidad, filtrados por cualquier atributo '
  'activo del catálogo. Categorías del mismo atributo: o; atributos '
  'distintos: y. Una clave, un operador o una categoría desconocidos son '
  'error. Devuelve (id_persona, finalidades) y nada más.';

-- ---------- 4 · R5.12 · La ficha seudónima del candidato ----------------
-- El equivalente de `ficha.seudonima()`: estado, enrolamiento, atributos con
-- procedencia y paneles activos. Sin un solo dato identificatorio y sin
-- registro de reidentificación: es lo que la búsqueda ya devolvió, de a una
-- persona.

create or replace function ficha_candidato(
    p_id_persona uuid,
    p_momento    timestamptz default now())
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  la_ficha jsonb;
begin
  if not exists (select 1 from v_persona_convocable v
                  where v.id_persona = p_id_persona) then
    raise exception 'No hay un candidato %.', p_id_persona
      using errcode = 'no_data_found';
  end if;

  select jsonb_build_object(
           'id_persona', p.id_persona,
           'estado', p.estado,
           'enrolado_en', p.creado_en,
           'atributos', coalesce((
             select jsonb_agg(jsonb_build_object(
                      'atributo', f.atributo,
                      'etiqueta', a.etiqueta,
                      'valor', f.valor,
                      'etiqueta_valor', f.etiqueta_valor,
                      'procedencia', f.procedencia,
                      'es_especial', a.es_especial)
                    order by a.orden, a.clave)
               from f_atributo_persona(coalesce(p_momento, now())) f
               join atributo_demografico a on a.id = f.atributo_id and a.activo
              where f.id_persona = p.id_persona), '[]'::jsonb),
           'paneles', coalesce((
             select jsonb_agg(jsonb_build_object('id', pn.id, 'nombre', pn.nombre)
                              order by pn.nombre)
               from membresia m join panel pn on pn.id = m.panel_id
              where m.id_persona = p.id_persona and m.estado = 'activo'),
             '[]'::jsonb))
    into la_ficha
    from persona p
   where p.id_persona = p_id_persona;

  return la_ficha;
end;
$$;

comment on function ficha_candidato(uuid, timestamptz) is
  'R5.12 — la ficha seudónima de un candidato: estado, enrolamiento, '
  'atributos con procedencia y paneles activos. Sin PII y sin registro de '
  'reidentificación. Fuera de v_persona_convocable: no encontrado.';

-- ---------- 5 · R5.13 · Identificación de candidatos --------------------
-- La decisión de §2 (D84): en la etapa de selección COLOQUIO ve nombre,
-- celular y correo, antes de declarar la convocatoria. Con tres límites que
-- viven acá y no en la interfaz:
--   1. Por sesión: `p_referencia` (el id de la sesión de COLOQUIO) es
--      obligatoria y queda en la auditoría. No es «traducime estos uuid».
--   2. Auditado: una fila de `reidentificacion` por persona devuelta, antes
--      de devolverla, con motivo propio (`seleccion_cuali`) y el sistema de
--      la conexión.
--   3. Una acción sobre un conjunto tildado: tope de 200 ids por llamada.
-- El gate no se toca: solo quien está en `v_persona_convocable` con
-- `contacto_participacion` vigente. Quien no, no viene, y no se dice por qué.

create or replace function identificar_candidatos(
    p_ids        uuid[],
    p_referencia text,
    p_actor      text,
    p_motivo     text default 'seleccion_cuali')
returns table (id_persona uuid, nombre text, email text, celular text)
language plpgsql
security definer
set search_path = public
as $$
declare
  el_sistema text := sistema_de_la_conexion();
begin
  if p_referencia is null or length(trim(p_referencia)) = 0 then
    raise exception
      'Hace falta la referencia de la sesión: identificar es para los '
      'candidatos de una sesión concreta, no para traducir cualquier id.'
      using errcode = 'invalid_parameter_value';
  end if;
  if p_actor is null or length(trim(p_actor)) = 0 then
    raise exception
      'Hace falta el actor: el email de la persona que lo pidió. Si no hay '
      'alguien detrás, no hay motivo para ver a nadie.'
      using errcode = 'invalid_parameter_value';
  end if;
  if p_ids is null then
    raise exception 'Hace falta la lista de candidatos.'
      using errcode = 'invalid_parameter_value';
  end if;
  if cardinality(p_ids) > 200 then
    raise exception 'Se piden % personas y el tope es 200 por llamada: es una '
                    'acción sobre los tildados, no una búsqueda.', cardinality(p_ids)
      using errcode = 'program_limit_exceeded';
  end if;

  -- Primero la auditoría, después la entrega: una fila por persona que
  -- efectivamente se va a devolver.
  insert into reidentificacion (id_persona, actor_uid, actor_email, motivo,
                                contexto, sistema)
  select v.id_persona, p_actor, p_actor,
         coalesce(nullif(trim(p_motivo), ''), 'seleccion_cuali'),
         jsonb_build_object('referencia', p_referencia,
                            'via', 'identificar_candidatos',
                            'pedidos', cardinality(p_ids)),
         el_sistema
    from v_persona_convocable v
   where v.id_persona = any(p_ids)
     and 'contacto_participacion' = any(v.finalidades);

  return query
    select p.id_persona, p.nombre, p.email, p.celular
      from persona p
     where p.id_persona = any(p_ids)
       and exists (select 1 from v_persona_convocable v
                    where v.id_persona = p.id_persona
                      and 'contacto_participacion' = any(v.finalidades))
     order by p.nombre nulls last, p.id_persona;
end;
$$;

comment on function identificar_candidatos(uuid[], text, text, text) is
  'R5.13 (D84) — nombre, email y celular de hasta 200 candidatos de una '
  'sesión, antes de convocarlos. Exige referencia y actor, reaplica el gate '
  '(contacto_participacion) y escribe una fila de reidentificacion por '
  'persona devuelta, con motivo seleccion_cuali.';

-- ---------- 6 · Privilegios -------------------------------------------
-- Las funciones nacen con `execute` a `public`: revoke primero.

revoke all on v_atributo_catalogo from public;
revoke execute on function atributos_de_personas(uuid[], timestamptz)        from public;
revoke execute on function f_persona_convocable_filtrada(text, jsonb, int)   from public;
revoke execute on function ficha_candidato(uuid, timestamptz)                from public;
revoke execute on function identificar_candidatos(uuid[], text, text, text)  from public;

grant select  on v_atributo_catalogo                                         to coloquio_app;
grant execute on function atributos_de_personas(uuid[], timestamptz)        to coloquio_app;
grant execute on function f_persona_convocable_filtrada(text, jsonb, int)   to coloquio_app;
grant execute on function ficha_candidato(uuid, timestamptz)                to coloquio_app;
grant execute on function identificar_candidatos(uuid[], text, text, text)  to coloquio_app;

commit;
