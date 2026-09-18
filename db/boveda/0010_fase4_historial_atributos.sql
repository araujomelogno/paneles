-- R4.1.a — Historial de atributos demográficos.
--
-- `persona_atributo` guardaba **un solo valor vigente** por persona y
-- atributo: un índice único sobre `(id_persona, atributo_id)` y un
-- `on conflict do update` que pisaba el valor anterior. Cuando alguien pasaba
-- de un nivel educativo a otro, el anterior no quedaba en ningún lado.
--
-- ---------- Esto es una corrección, no solo una feature -----------------
-- Sin historial, recalcular la composición de una ola de hace un año la
-- calcula con la demografía de **hoy**. No es que falte una vista
-- longitudinal: es que el número que el sistema ya muestra está mal, y no
-- avisa. Una ola cuya cuota cerró con 30 % de jóvenes puede mostrar hoy 22 %
-- sin que nadie haya cambiado de panel, solo porque esa gente cumplió años.
-- Por eso este requisito va primero en el bloque.
--
-- ---------- El modelo: intervalos semiabiertos --------------------------
-- Cada fila de `persona_atributo` pasa a valer en `[desde, hasta)`. El valor
-- actual es el de `hasta is null`. Cambiar un valor **no** actualiza la fila:
-- le pone `hasta = now()` e inserta una nueva con `desde = now()`. Que el
-- intervalo sea semiabierto es lo que garantiza que en el instante del cambio
-- haya exactamente un valor vigente y no dos.
--
-- ---------- Por qué el primer valor vale desde `-infinity` --------------
-- Sabemos cuándo un valor **cambió**; no sabemos cuándo el primero *empezó*.
-- Si al primer valor le pusiéramos `desde = now()`, toda composición
-- retroactiva anterior a esta migración devolvería cero personas con dato:
-- la corrección que este requisito viene a hacer quedaría peor que el
-- problema. Así que el primer valor registrado de un atributo vale «desde
-- siempre», y eso es una suposición declarada, no un dato. Los cambios
-- posteriores sí llevan su fecha real.
--
-- No usamos `persona.creado_en` en su lugar porque una persona puede
-- ingresarse hoy desde un archivo de campo de hace dos años: su fecha de alta
-- en el sistema no acota hacia atrás la validez de sus atributos.
--
-- ---------- Una sola implementación de la resolución --------------------
-- `v_atributo_persona` era el único lugar donde vivía la precedencia de
-- R3.14.g (fecha de nacimiento > edad declarada envejecida > tramo cargado).
-- Duplicarla en una versión «a fecha» garantizaría que las dos se separen con
-- el tiempo. En vez de eso, la lógica pasa a `f_atributo_persona(momento)` y
-- la vista queda como `select * from f_atributo_persona(now())`. Todo el
-- código que consulta la vista sigue andando sin cambios, y el cálculo
-- retroactivo es la misma función con otro argumento.
--
-- Al parametrizarse, los derivados también se calculan **a esa fecha**: la
-- edad de una persona en una ola de 2024 es la que tenía en 2024. Era el otro
-- lado del mismo error.

-- ============================================================
--  1 · Vigencia
-- ============================================================

-- `btree_gist` es lo que permite combinar la igualdad de `id_persona` y
-- `atributo_id` con el solapamiento del rango en una sola restricción de
-- exclusión. Está disponible en Cloud SQL.
create extension if not exists btree_gist;

alter table persona_atributo
  add column desde timestamptz not null default now(),
  add column hasta timestamptz;

-- Todo lo que ya existe es el primer valor conocido de su atributo: vale
-- desde siempre y sigue vigente.
update persona_atributo set desde = '-infinity'::timestamptz;

comment on column persona_atributo.desde is
  'R4.1.a — inicio de vigencia. `-infinity` = primer valor conocido: sabemos '
  'cuándo cambió, no cuándo empezó.';
comment on column persona_atributo.hasta is
  'R4.1.a — fin de vigencia, exclusivo. NULL = vigente. El intervalo es '
  '[desde, hasta).';

-- La unicidad pasa a ser sobre la vigencia abierta: una persona tiene un
-- solo valor actual por atributo, y cuantos históricos haga falta.
drop index persona_atributo_unico;
create unique index persona_atributo_vigente
    on persona_atributo (id_persona, atributo_id) where hasta is null;

-- Nada de esto sirve si una consulta a fecha tiene que recorrer la tabla.
create index persona_atributo_vigencia
    on persona_atributo (id_persona, atributo_id, desde desc);

-- Dos intervalos del mismo par no se pueden pisar. Sin esto, un error de
-- código produciría dos valores vigentes a la misma fecha y la composición
-- retroactiva contaría a la persona dos veces, en dos categorías distintas.
alter table persona_atributo
  add constraint persona_atributo_vigencia_coherente
      check (hasta is null or hasta >= desde);

alter table persona_atributo
  add constraint persona_atributo_sin_solapamiento
      exclude using gist (
        id_persona with =,
        atributo_id with =,
        tstzrange(desde, hasta, '[)') with &&
      );

-- ============================================================
--  2 · La resolución, ahora parametrizada por fecha
-- ============================================================
-- Es el cuerpo de la `v_atributo_persona` de R3.14, con tres cambios:
--   · las filas de `persona_atributo` se filtran por vigencia a `momento`;
--   · `age(...)` se calcula contra `momento` y no contra `current_date`;
--   · el envejecimiento de la edad declarada cuenta hasta `momento`.
-- Es una función SQL `stable` de una sola sentencia, así que el planificador
-- la puede expandir en línea: consultar la vista cuesta lo mismo que antes.

drop view if exists v_demografia;
drop view if exists v_atributo_persona;

create function f_atributo_persona(momento timestamptz)
returns table (
  id_persona     uuid,
  atributo_id    bigint,
  atributo       text,
  valor          text,
  etiqueta_valor text,
  valor_num      numeric,
  valor_fecha    date,
  valor_crudo    text,
  origen         text,
  procedencia    text
)
language sql
stable
as $$
-- ── Valores cargados de atributos no derivados ──
select
  pa.id_persona,
  a.id,
  a.clave,
  coalesce(c.clave, pa.valor_num::text, pa.valor_fecha::text),
  coalesce(c.etiqueta, pa.valor_num::text, pa.valor_fecha::text),
  pa.valor_num,
  pa.valor_fecha,
  pa.valor_crudo,
  pa.origen,
  'cargado'::text
  from persona_atributo pa
  join atributo_demografico a on a.id = pa.atributo_id
  left join atributo_categoria c on c.id = pa.categoria_id
 where a.tipo <> 'derivado'
   and pa.desde <= momento and (pa.hasta is null or momento < pa.hasta)
   and coalesce(c.clave, pa.valor_num::text, pa.valor_fecha::text) is not null

union all

-- ── Edad efectiva (R3.14.g), a `momento` ──
-- Precedencia: fecha de nacimiento > edad declarada envejecida. La fecha de
-- nacimiento gana siempre que exista.
select
  e.id_persona, a.id, a.clave,
  e.edad::text, e.edad::text, e.edad, null::date, e.crudo, e.origen, e.procedencia
  from atributo_demografico a
  join (
    select
      p.id_persona,
      case
        when p.fecha_nacimiento is not null
          then date_part('year', age(momento::date, p.fecha_nacimiento))::int
        when pa.valor_num is not null
          then (pa.valor_num + date_part('year', age(
                 momento::date,
                 coalesce(pa.fecha_referencia, pa.actualizado_en::date))))::int
      end as edad,
      case
        when p.fecha_nacimiento is not null then 'derivado'
        when pa.valor_num is not null then 'envejecido'
      end as procedencia,
      pa.valor_crudo as crudo,
      pa.origen
      from persona p
      left join persona_atributo pa
             on pa.id_persona = p.id_persona
            and pa.atributo_id = (select id from atributo_demografico
                                   where clave = 'edad')
            and pa.desde <= momento and (pa.hasta is null or momento < pa.hasta)
  ) e on true
 where a.clave = 'edad' and e.edad is not null

union all

-- ── Tramo etario (R3.14.g), a `momento` ──
select
  t.id_persona, a.id, a.clave, t.tramo, t.tramo,
  null::numeric, null::date, t.crudo, t.origen, t.procedencia
  from atributo_demografico a
  join (
    select
      p.id_persona,
      case
        when ef.edad is not null then
          case
            when ef.edad < 18 then '<18'
            when ef.edad < 25 then '18-24'
            when ef.edad < 35 then '25-34'
            when ef.edad < 45 then '35-44'
            when ef.edad < 55 then '45-54'
            when ef.edad < 65 then '55-64'
            else '65+'
          end
        else ct.clave
      end as tramo,
      case
        when p.fecha_nacimiento is not null then 'derivado'
        when ef.edad is not null then 'envejecido'
        when ct.clave is not null then 'cargado'
      end as procedencia,
      pt.valor_crudo as crudo,
      pt.origen
      from persona p
      left join persona_atributo pe
             on pe.id_persona = p.id_persona
            and pe.atributo_id = (select id from atributo_demografico
                                   where clave = 'edad')
            and pe.desde <= momento and (pe.hasta is null or momento < pe.hasta)
      cross join lateral (
        select case
                 when p.fecha_nacimiento is not null
                   then date_part('year', age(momento::date, p.fecha_nacimiento))::int
                 when pe.valor_num is not null
                   then (pe.valor_num + date_part('year', age(
                          momento::date,
                          coalesce(pe.fecha_referencia,
                                   pe.actualizado_en::date))))::int
               end as edad
      ) ef
      left join persona_atributo pt
             on pt.id_persona = p.id_persona
            and pt.atributo_id = (select id from atributo_demografico
                                   where clave = 'tramo_etario')
            and pt.desde <= momento and (pt.hasta is null or momento < pt.hasta)
      left join atributo_categoria ct on ct.id = pt.categoria_id
  ) t on true
 where a.clave = 'tramo_etario' and t.tramo is not null;
$$;

comment on function f_atributo_persona(timestamptz) is
  'R4.1.a — el valor efectivo de cada atributo de cada persona a una fecha. '
  'Es la única implementación de la precedencia de R3.14.g; '
  '`v_atributo_persona` es esta misma función en `now()`.';

-- ============================================================
--  3 · Las vistas, sin cambios para quien las consulta
-- ============================================================
-- Mismo nombre y mismas columnas que antes: composición, consultas, muestreo,
-- cuotas, bonos, ficha y exportaciones siguen andando sin tocarse.

create view v_atributo_persona as
select * from f_atributo_persona(now());

create view v_demografia as
select
  p.id_persona,
  max(v.valor) filter (where v.atributo = 'sexo')         as sexo,
  max(v.valor) filter (where v.atributo = 'localidad')    as localidad,
  max(v.valor_num) filter (where v.atributo = 'edad')::int as edad,
  max(v.valor) filter (where v.atributo = 'tramo_etario') as tramo_etario
  from persona p
  left join v_atributo_persona v on v.id_persona = p.id_persona
 group by p.id_persona;
