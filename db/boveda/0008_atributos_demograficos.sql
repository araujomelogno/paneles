-- R3.14 — Atributos demográficos configurables.
--
-- La bóveda tenía una lista **fija** de segmentadores: `persona.sexo`,
-- `persona.localidad` y el tramo etario derivado de `persona.fecha_nacimiento`.
-- Eran los únicos por los que se podía filtrar, segmentar y fijar cuotas.
-- Cualquier otro segmentador habitual en investigación de mercado —nivel
-- educativo, nivel socioeconómico, ocupación, composición del hogar— no tenía
-- dónde guardarse: o se perdía, o terminaba embebido como una pregunta más en
-- el store semántico, que es justo lo que el marcado de demográficas evita. Y
-- agregar uno nuevo exigía una migración, o sea un ciclo de desarrollo.
--
-- Esta migración reemplaza esa lista fija por un **catálogo**: un admin define
-- el atributo y sus categorías canónicas desde la app, y de ahí en más ese
-- atributo sirve igual que sexo o localidad en consultas, composición, cuotas
-- y muestreo.
--
-- ---------- Por qué acá sí canonizamos --------------------------------------
-- Es la misma distinción de diseño de todo el sistema: lo estructurado se
-- consulta con SQL exacto y necesita categorías estables; lo semántico se
-- interpreta en cada consulta. Canonizar texto libre congela errores en el
-- dato; canonizar segmentadores es lo que hace que un filtro devuelva siempre
-- lo mismo y que la aritmética de cuotas cierre. La salvaguarda contra el
-- riesgo de congelar un error es `persona_atributo.valor_crudo`: el valor tal
-- como vino del archivo, para poder recalcular el canónico si el mapeo salió
-- mal, sin volver a pedir el archivo original.
--
-- ---------- Qué NO entra al catálogo ----------------------------------------
-- Los datos de identidad y contacto (documento, nombre, email, celular,
-- contacto, observaciones): no son segmentadores. Tampoco `fecha_nacimiento`,
-- que es dato de identidad y llave del dedup (R1.2); de ella se deriva el
-- tramo etario, que sí es un atributo del catálogo.
--
-- ---------- Partes de esta migración ----------------------------------------
-- 1. Aditiva: las tres tablas nuevas. Se aplica con la app andando.
-- 2. De datos: siembra `sexo`, `localidad`, `tramo_etario` y `edad` en el
--    catálogo y copia los valores que hoy viven en `persona`.
-- 3. Reescribe `v_demografia` sobre el catálogo, **conservando su nombre y sus
--    columnas**, para que todo el código que la consulta siga andando.
--
-- `persona.sexo` y `persona.localidad` quedan **obsoletas**: dejan de
-- escribirse y de leerse, pero no se eliminan acá. Se eliminan en una
-- migración posterior, una vez verificado que nada las usa.

-- ============================================================
--  1 · El catálogo
-- ============================================================

create table atributo_demografico (
  id          bigint generated always as identity primary key,
  -- La clave es el identificador estable: es lo que guardan
  -- `objetivo_composicion.dimension` y los criterios de las consultas
  -- guardadas. Por eso no se puede cambiar una vez que hay datos.
  clave       text not null,
  etiqueta    text not null,
  tipo        text not null default 'categorico'
              check (tipo in ('categorico','numerico','fecha','derivado')),
  descripcion text,
  -- R3.14.e — categorías especiales de la Ley 18.331: salud, origen étnico o
  -- racial, convicciones religiosas o morales, afiliación sindical, ideología
  -- política, vida sexual. Un catálogo abierto permite definirlas como si
  -- fueran un segmentador cualquiera, y en investigación de mercado se
  -- preguntan seguido. La marca es lo que hace que su existencia sea visible
  -- en una revisión de cumplimiento en vez de pasar inadvertida.
  es_especial boolean not null default false,
  -- No se borran los atributos con datos: se desactivan. Dejan de ofrecerse
  -- en cargas y filtros nuevos y conservan lo ya cargado.
  activo      boolean not null default true,
  orden       int not null default 100,
  creado_en   timestamptz not null default now(),
  creado_por  text
);
create unique index atributo_demografico_clave_unica
    on atributo_demografico (clave);

create table atributo_categoria (
  id           bigint generated always as identity primary key,
  atributo_id  bigint not null
               references atributo_demografico(id) on delete cascade,
  clave        text not null,
  etiqueta     text not null,
  orden        int not null default 100,
  activo       boolean not null default true
);
create unique index atributo_categoria_unica
    on atributo_categoria (atributo_id, clave);

-- ============================================================
--  2 · El valor de cada persona
-- ============================================================

create table persona_atributo (
  id              bigint generated always as identity primary key,
  id_persona      uuid   not null references persona(id_persona) on delete cascade,
  atributo_id     bigint not null references atributo_demografico(id) on delete cascade,
  -- Uno de los tres, según el tipo del atributo. Un categórico nunca guarda
  -- texto libre: guarda la categoría canónica.
  categoria_id    bigint references atributo_categoria(id),
  valor_num       numeric,
  valor_fecha     date,
  -- El valor tal como vino del archivo. Es la salvaguarda de R3.14.c: si el
  -- mapeo salió mal se corrige y se recalcula el canónico desde acá.
  valor_crudo     text,
  origen          text,   -- 'alta', 'ingesta', 'carga', 'inscripcion', 'edicion'
  -- Para los valores que envejecen. El caso es la edad declarada: una base
  -- que trae «34 años» sin fecha de nacimiento vale 34 **en la fecha de campo
  -- de esa base**, no para siempre. Con la referencia guardada, el tramo se
  -- calcula envejeciendo; sin ella, quedaría congelado y las cuotas se
  -- calcularían sobre una edad que ya no es.
  fecha_referencia date,
  actualizado_en  timestamptz not null default now()
);
-- Un solo valor vigente por persona y atributo (R3.14.b).
create unique index persona_atributo_unico
    on persona_atributo (id_persona, atributo_id);
create index persona_atributo_por_atributo
    on persona_atributo (atributo_id, categoria_id);

-- R3.14.a — toda creación, edición y desactivación del vocabulario queda con
-- autor y fecha. No se reutiliza `usuario_auditoria`: esa tabla habla de
-- personal de Equipos y su `accion` tiene un check propio. Acá lo que se
-- audita es el vocabulario con el que se segmenta el panel, que es otra cosa
-- y se revisa por otro motivo (un atributo especial que aparece sin que nadie
-- lo haya decidido, por ejemplo).
create table atributo_auditoria (
  id           bigint generated always as identity primary key,
  atributo_id  bigint references atributo_demografico(id) on delete set null,
  clave        text not null,
  accion       text not null
                 check (accion in ('alta','edicion','desactivacion',
                                   'reactivacion','baja','categoria',
                                   'recalculo')),
  detalle      jsonb not null default '{}'::jsonb,
  actor_uid    text,
  actor_email  text,
  creado_en    timestamptz not null default now()
);
create index atributo_auditoria_por_atributo
    on atributo_auditoria (atributo_id, creado_en desc);

comment on table atributo_demografico is
  'R3.14 — vocabulario de segmentadores. Lo define un admin desde la app; '
  'consultas, composición, cuotas y muestreo operan sobre él.';
comment on table persona_atributo is
  'R3.14 — el valor de cada persona para cada atributo, canónico y crudo.';

-- ============================================================
--  3 · Siembra del catálogo con los segmentadores de hoy
-- ============================================================
-- R3.14.f — las claves son **las mismas que se usan hoy**, para que los
-- objetivos de composición ya cargados y las consultas guardadas sigan
-- resolviendo sin tocarlos.

insert into atributo_demografico (clave, etiqueta, tipo, descripcion, orden, creado_por)
values
  ('sexo', 'Sexo', 'categorico',
   'Segmentador de cuota básico. Antes vivía en persona.sexo.', 10, 'migracion'),
  ('tramo_etario', 'Tramo etario', 'derivado',
   'Se deriva de la fecha de nacimiento; si no hay, de la edad declarada '
   'envejecida hasta hoy; en última instancia, del tramo cargado.', 20, 'migracion'),
  ('edad', 'Edad', 'derivado',
   'Edad efectiva. Se deriva de la fecha de nacimiento; si no hay, de la edad '
   'declarada en el archivo, envejecida desde su fecha de referencia.', 30,
   'migracion'),
  ('localidad', 'Localidad', 'categorico',
   'Segmentador geográfico. Antes vivía en persona.localidad.', 40, 'migracion');

-- Las categorías de sexo son las tres que ya escribía `sav.SEXO_CANONICO` y
-- ofrecía el alta manual.
insert into atributo_categoria (atributo_id, clave, etiqueta, orden)
select a.id, v.clave, v.etiqueta, v.orden
  from atributo_demografico a,
       (values ('F', 'Femenino', 10),
               ('M', 'Masculino', 20),
               ('X', 'Otro / no binario', 30)) as v(clave, etiqueta, orden)
 where a.clave = 'sexo';

-- Los tramos son exactamente los que calculaba la `v_demografia` anterior.
-- Existen como categorías aunque el atributo sea derivado: son el vocabulario
-- de la cuota etaria, y hacen falta para cargar un objetivo de composición y
-- para el caso 3 de R3.14.g (tramo cargado directamente).
insert into atributo_categoria (atributo_id, clave, etiqueta, orden)
select a.id, v.clave, v.clave, v.orden
  from atributo_demografico a,
       (values ('<18', 10), ('18-24', 20), ('25-34', 30), ('35-44', 40),
               ('45-54', 50), ('55-64', 60), ('65+', 70)) as v(clave, orden)
 where a.clave = 'tramo_etario';

-- Localidad arranca con los diecinueve departamentos, que es el vocabulario
-- que efectivamente se usa para cuotas geográficas en Uruguay...
insert into atributo_categoria (atributo_id, clave, etiqueta, orden)
select a.id, v.clave, v.clave, v.orden
  from atributo_demografico a,
       (values ('Artigas', 10), ('Canelones', 20), ('Cerro Largo', 30),
               ('Colonia', 40), ('Durazno', 50), ('Flores', 60),
               ('Florida', 70), ('Lavalleja', 80), ('Maldonado', 90),
               ('Montevideo', 100), ('Paysandú', 110), ('Río Negro', 120),
               ('Rivera', 130), ('Rocha', 140), ('Salto', 150),
               ('San José', 160), ('Soriano', 170), ('Tacuarembó', 180),
               ('Treinta y Tres', 190)) as v(clave, orden)
 where a.clave = 'localidad';

-- ...más todo valor distinto que ya esté cargado y no sea uno de ellos. Sin
-- esto la migración de datos perdería filas, y perder un dato cargado no es
-- una opción aceptable para una unificación.
insert into atributo_categoria (atributo_id, clave, etiqueta, orden)
select a.id, t.localidad, t.localidad, 900
  from atributo_demografico a
  join (select distinct trim(localidad) as localidad
          from persona
         where localidad is not null and trim(localidad) <> '') t on true
 where a.clave = 'localidad'
   and not exists (
         select 1 from atributo_categoria c
          where c.atributo_id = a.id and c.clave = t.localidad);

-- ============================================================
--  4 · Migración de los valores existentes
-- ============================================================
-- Se conserva el valor crudo: es lo que permite recalcular si más adelante se
-- corrige el vocabulario.

insert into persona_atributo
       (id_persona, atributo_id, categoria_id, valor_crudo, origen)
select p.id_persona, a.id, c.id, p.sexo, 'migracion'
  from persona p
  join atributo_demografico a on a.clave = 'sexo'
  join atributo_categoria c on c.atributo_id = a.id and c.clave = upper(trim(p.sexo))
 where p.sexo is not null and trim(p.sexo) <> ''
    on conflict (id_persona, atributo_id) do nothing;

insert into persona_atributo
       (id_persona, atributo_id, categoria_id, valor_crudo, origen)
select p.id_persona, a.id, c.id, p.localidad, 'migracion'
  from persona p
  join atributo_demografico a on a.clave = 'localidad'
  join atributo_categoria c on c.atributo_id = a.id and c.clave = trim(p.localidad)
 where p.localidad is not null and trim(p.localidad) <> ''
    on conflict (id_persona, atributo_id) do nothing;

-- ============================================================
--  5 · Las vistas
-- ============================================================

-- `v_atributo_persona` es el único lugar donde vive la resolución de un valor
-- efectivo. Todo lo que segmenta —consultas, composición, cuotas, muestreo—
-- lee de acá, sin distinguir entre los atributos «de fábrica» y los que
-- definió un usuario. Una persona sin valor para un atributo **no tiene fila**:
-- así «sin dato» no se cuenta como una categoría más ni infla ninguna cuota.
create view v_atributo_persona as
-- ── Valores cargados de atributos no derivados ──
select
  pa.id_persona,
  a.id                                        as atributo_id,
  a.clave                                     as atributo,
  coalesce(c.clave, pa.valor_num::text, pa.valor_fecha::text)    as valor,
  coalesce(c.etiqueta, pa.valor_num::text, pa.valor_fecha::text) as etiqueta_valor,
  pa.valor_num,
  pa.valor_fecha,
  pa.valor_crudo,
  pa.origen,
  'cargado'::text                             as procedencia
  from persona_atributo pa
  join atributo_demografico a on a.id = pa.atributo_id
  left join atributo_categoria c on c.id = pa.categoria_id
 where a.tipo <> 'derivado'
   and coalesce(c.clave, pa.valor_num::text, pa.valor_fecha::text) is not null

union all

-- ── Edad efectiva (R3.14.g) ──
-- Precedencia: fecha de nacimiento > edad declarada envejecida. La fecha de
-- nacimiento gana siempre que exista: ningún valor cargado la reemplaza.
select
  e.id_persona, a.id, a.clave,
  e.edad::text, e.edad::text, e.edad, null::date, e.crudo, e.origen, e.procedencia
  from atributo_demografico a
  join (
    select
      p.id_persona,
      case
        when p.fecha_nacimiento is not null
          then date_part('year', age(p.fecha_nacimiento))::int
        when pa.valor_num is not null
          then (pa.valor_num + date_part('year', age(
                 current_date,
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
  ) e on true
 where a.clave = 'edad' and e.edad is not null

union all

-- ── Tramo etario (R3.14.g) ──
-- Mismo orden, con un tercer escalón: el tramo cargado directamente, que es
-- el último recurso y el único que queda congelado.
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
      cross join lateral (
        select case
                 when p.fecha_nacimiento is not null
                   then date_part('year', age(p.fecha_nacimiento))::int
                 when pe.valor_num is not null
                   then (pe.valor_num + date_part('year', age(
                          current_date,
                          coalesce(pe.fecha_referencia,
                                   pe.actualizado_en::date))))::int
               end as edad
      ) ef
      left join persona_atributo pt
             on pt.id_persona = p.id_persona
            and pt.atributo_id = (select id from atributo_demografico
                                   where clave = 'tramo_etario')
      left join atributo_categoria ct on ct.id = pt.categoria_id
  ) t on true
 where a.clave = 'tramo_etario' and t.tramo is not null;

-- `v_demografia` conserva su nombre y sus columnas —`id_persona`, `sexo`,
-- `localidad`, `edad`, `tramo_etario`— y pasa a alimentarse del catálogo.
-- Es lo que permite que todo el código que la consulta siga funcionando sin
-- cambios: composición, participación, exportaciones, ficha, bonos.
drop view if exists v_demografia;
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
