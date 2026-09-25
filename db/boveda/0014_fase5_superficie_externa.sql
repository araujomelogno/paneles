-- Fase 5 — La superficie que la bóveda le expone a un segundo consumidor, y
-- el control de acceso que la hace la única puerta.
--
-- `paneles` fue construido con el supuesto de que hay **un solo** programa
-- hablándole a la bóveda. Bajo ese supuesto era razonable que el gate de
-- consentimiento fuera `consentimiento.exigir()` en Python. Con un segundo
-- consumidor eso pasa a ser una promesa repetida en dos bases de código, y la
-- primera que se rompa lo va a hacer en silencio: una persona sin
-- consentimiento vigente convocada a un grupo, o una baja que deja viva una
-- grabación porque la cascada no sabe que el otro sistema existe.
--
-- Esta migración mueve las invariantes de cumplimiento a la base.
--
-- ============================================================
--  La decisión que ordena todo: gate legal ≠ política de negocio
-- ============================================================
-- El **consentimiento vigente** es un invariante legal: no es negociable, no
-- admite parámetros y no puede quedar del lado del consumidor. Va en una
-- vista de la que es imposible salirse.
--
-- La **fatiga** es política de negocio: sus umbrales son por panel, su
-- ventana es un parámetro, y el cálculo actual excluye la encuesta en curso
-- —que es contexto del llamador y no se puede expresar en una vista sin
-- parámetros—. Además el cualitativo tiene su propia noción de fatiga. Así
-- que la fatiga se expone como **hechos** y cada consumidor aplica su umbral.
--
-- Consecuencia que conviene tener escrita y no descubrir después: **la
-- política de fatiga no queda hecha valer en la base.** Es deliberado. El
-- consentimiento no se negocia; la fatiga admite criterios distintos por
-- consumidor.

-- ============================================================
--  1 · Registro de sistemas consumidores
-- ============================================================
-- Es lo que hace determinista a la cascada de baja: sin esta tabla, «a quién
-- hay que avisarle» sería una lista en la cabeza de alguien.

create table sistema_consumidor (
  codigo             text primary key,
  nombre             text not null,
  -- Desde cuándo es responsable. Una baja anterior a esta fecha no le genera
  -- pendientes: no se le puede exigir retroactivamente algo que no existía.
  alta_en            timestamptz not null default now(),
  activo             boolean not null default true,
  -- De qué finalidades se hace cargo. Decide qué bajas le llegan: un retiro
  -- parcial de `uso_semantico` no tiene por qué molestar a un sistema que no
  -- trata esa finalidad.
  alcance_finalidades text[] not null default '{}',
  contacto_tecnico   text,
  -- El rol de base con el que se conecta. Es lo que permite derivar el
  -- sistema de la conexión en vez de que el llamador lo declare (R5.6).
  rol_bd             text
);

comment on table sistema_consumidor is
  'R5.4 — quién consume la bóveda, desde cuándo y de qué se hace responsable '
  'ante una baja.';

-- `paneles` es el primero, y se registra a sí mismo: la auditoría existente
-- pasa a decir de qué sistema vino, y el default necesita una fila a la que
-- apuntar.
insert into sistema_consumidor
       (codigo, nombre, alcance_finalidades, contacto_tecnico, rol_bd)
values ('paneles', 'Gestión de paneles y consulta semántica',
        array['contacto_participacion','uso_semantico'],
        'equipo de plataforma', 'app_paneles');

-- ============================================================
--  2 · R5.6 · Auditoría con origen
-- ============================================================
-- `actor_uid` y `actor_email` asumen una persona logueada en la app de
-- administración. Con dos sistemas eso ya no identifica al autor.
--
-- El default `'paneles'` es lo que hace que las filas históricas queden
-- correctas sin tocarlas: todas las que existen hoy las escribió `paneles`.

alter table reidentificacion
  add column sistema text not null default 'paneles'
      references sistema_consumidor(codigo);
alter table usuario_auditoria
  add column sistema text not null default 'paneles'
      references sistema_consumidor(codigo);

create index reidentificacion_por_sistema
    on reidentificacion (sistema, creado_en desc);

-- De qué sistema es la conexión actual. **No es un parámetro**: se deriva del
-- rol conectado, para que un consumidor no pueda declararse otro. Con
-- autenticación IAM, `current_user` es el email de la cuenta de servicio.
-- `security definer` porque lee `sistema_consumidor`, y un consumidor no
-- tiene —ni debe tener— privilegio sobre esa tabla.
create function sistema_de_la_conexion() returns text
language plpgsql stable
security definer
set search_path = public
as $$
declare
  el_codigo text;
begin
  -- **`session_user`, no `current_user`.** Adentro de una función
  -- `security definer` —y `contacto_para_convocatoria` lo es— `current_user`
  -- es el dueño de la función, no quien la llamó. Derivar de ahí haría que
  -- cualquier rol que pueda ejecutarla quede auditado como la aplicación
  -- dueña del esquema, que es exactamente el lavado de origen que R5.6
  -- existe para impedir. `session_user` es el rol que abrió la conexión y no
  -- cambia con `security definer`.
  select codigo into el_codigo
    from sistema_consumidor
   where rol_bd = session_user
   limit 1;
  if el_codigo is not null then
    return el_codigo;
  end if;

  -- Sin fila en el registro, el rol **no puede actuar**. La tentación era
  -- devolver 'paneles' por defecto, y sería un error grave: haría que las
  -- acciones de un consumidor no registrado quedaran auditadas como si las
  -- hubiera hecho la aplicación de administración. Lavar el origen es peor
  -- que no tener origen.
  --
  -- La única excepción es el **dueño de las tablas**, que por definición es
  -- la aplicación incumbente: se conecta con el rol que creó el esquema, y en
  -- el cluster de pruebas ese rol no se llama igual que en producción.
  if pg_catalog.pg_get_userbyid(
       (select relowner from pg_class where relname = 'persona'
         and relnamespace = 'public'::regnamespace)) = session_user then
    return 'paneles';
  end if;

  raise exception
    'El rol «%» no está en `sistema_consumidor`. Un sistema que no está '
    'registrado no puede operar: no se le pueden generar pendientes de '
    'borrado, y sus acciones quedarían auditadas como si fueran de otro.',
    session_user
    using errcode = 'insufficient_privilege';
end;
$$;

comment on function sistema_de_la_conexion() is
  'R5.6 — el sistema se deriva del rol, nunca del llamador. Un rol sin fila '
  'en el registro no opera: atribuirle sus acciones a `paneles` sería lavar '
  'el origen.';

-- ============================================================
--  3 · R5.1 · La superficie de convocables
-- ============================================================
-- Una fila por (persona, finalidad) de toda persona que **hoy** puede usarse
-- para esa finalidad. El gate deja de ser una función de Python y pasa a ser
-- la única puerta de lectura de candidatos.
--
-- ¡OJO! ── `security_invoker` ──────────────────────────────────────────
-- Esta vista se crea **con el dueño actual y sin `security_invoker`**, y de
-- eso depende todo el control de acceso de esta fase.
--
-- Las tablas base tienen `row level security` habilitada y sin políticas, lo
-- que para un rol que no es el dueño significa cero filas. Una vista en
-- Postgres se ejecuta con los privilegios de **su dueño**, salvo que se cree
-- con `security_invoker = true`. Así que: la vista pasa, la consulta directa
-- a las tablas no.
--
-- Si alguien recreara esta vista con `security_invoker = true`, devolvería
-- cero filas y el requisito quedaría roto de una forma difícil de
-- diagnosticar: no falla, simplemente no hay candidatos.
--
-- ── Y una segunda mitad, que la de arriba no cubre ──────────────────
-- «La vista corre con los privilegios de su dueño» vale para las **tablas**
-- que la vista lee. No vale para las **funciones**: el permiso de *ejecutar*
-- una función se verifica contra quien consulta, aun cuando la llamada esté
-- adentro de una vista ajena.
--
-- Y `v_persona_convocable` necesita demografía, que desde R4.1.a se resuelve
-- en `f_atributo_persona(momento)`. Una vista pelada da «permission denied
-- for function f_atributo_persona», y el motivo no se parece en nada a la
-- causa.
--
-- Las salidas posibles eran tres y dos son malas:
--
--   · Otorgarle `execute` sobre `f_atributo_persona` al consumidor. Andaría,
--     y sería una puerta de atrás: esa función devuelve la demografía de
--     **todo el mundo**, incluida la de quien no consintió. Justo lo que el
--     gate existe para impedir.
--   · Duplicar la resolución de atributos en esta vista, sin pasar por la
--     función. Andaría hoy y se separaría con el tiempo, que es exactamente
--     el problema que R4.1.a resolvió unificándola.
--
-- La que queda: la superficie es una **función `security definer` que aplica
-- el gate adentro**, y la vista es una fachada sobre ella. Al consumidor se
-- le otorga ejecutar esa función, y no es una puerta de atrás porque la
-- función ya filtró: no hay forma de sacarle una fila de alguien que no
-- consintió. La resolución de atributos sigue siendo una sola.
alter function f_atributo_persona(timestamptz) security definer;
revoke execute on function f_atributo_persona(timestamptz) from public;

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

-- Los **hechos** de fatiga. El umbral lo aplica cada consumidor.
--
-- Cuenta solo `origen = 'convocatoria'`, con el mismo criterio que usa
-- `muestreo._candidatos()`: la ingesta también crea participaciones, pero de
-- gente que respondió en campo sin que nadie la contactara desde acá.
-- Contarlas la sacaría del muestreo por una fatiga que no existe. La fatiga
-- mide cuánto se molestó a alguien, no cuántas veces respondió.
create view v_fatiga_panelista as
select
  m.id_persona,
  m.panel_id,
  count(*) filter (
    where pa.convocado_en >= now() - interval '90 days'
      and pa.origen = 'convocatoria')::int      as recientes,
  count(pa.id) filter (where pa.origen = 'convocatoria')::int as totales,
  max(pa.convocado_en) filter (where pa.origen = 'convocatoria')
                                                as ultima_convocatoria,
  count(*) filter (where pa.respondio)::int     as respondidas
  from membresia m
  left join encuesta e on e.panel_id = m.panel_id
  left join participacion pa on pa.id_persona = m.id_persona
                            and pa.encuesta_id = e.id
 where m.estado = 'activo'
 group by m.id_persona, m.panel_id;

comment on view v_fatiga_panelista is
  'R5.1 — los hechos de fatiga. La ventana de 90 días es la de referencia; '
  'el umbral por panel vive en `umbral_fatiga` y lo aplica cada consumidor, '
  'porque el cualitativo tiene su propia noción de fatiga.';

-- ============================================================
--  4 · R5.2 · Contacto acotado y auditado
-- ============================================================
-- Convocar exige leer un canal de contacto, que es PII. Y esa lectura tiene
-- que quedar auditada, cosa que **una vista no puede hacer** porque no tiene
-- efectos. Por eso el contacto se sirve por función y no por vista.

create function contacto_para_convocatoria(
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
  select exists (
    select 1 from participacion pa
      join encuesta e on e.id = pa.encuesta_id
     where pa.id_persona = p_id_persona
       and e.estado <> 'cerrada')
    into tiene_convocatoria;

  if not tiene_convocatoria then
    raise exception
      'La persona % no tiene una convocatoria activa: no hay motivo para '
      'leer su contacto.', p_id_persona
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

comment on function contacto_para_convocatoria(uuid, text, text, text) is
  'R5.2 — un solo canal, con el gate aplicado y la lectura auditada en la '
  'misma transacción. Es la única vía: los consumidores no leen `persona`.';

-- ============================================================
--  5 · R5.3 · Cascada de baja extensible
-- ============================================================
-- `bajas.py` ya resolvió bien el borrado semántico: **no es transaccional, no
-- bloquea la baja, y deja rastro para reintentar**. Esta migración generaliza
-- ese patrón a N consumidores en vez de inventar uno nuevo. `persona_borrada`
-- no se toca: sigue siendo la prueba de que el retiro se atendió.

create table borrado_pendiente (
  id_persona    uuid not null,
  sistema       text not null references sistema_consumidor(codigo),
  -- Qué hay que borrar. En una baja total es todo; en un retiro parcial, lo
  -- que corresponde a esa finalidad.
  alcance       text not null default 'total',
  finalidad     text references finalidad_consentimiento(codigo),
  solicitado_en timestamptz not null default now(),
  confirmado_en timestamptz,
  intentos      int not null default 0,
  ultimo_error  text,
  primary key (id_persona, sistema, alcance)
);

create index borrado_pendiente_abiertos
    on borrado_pendiente (sistema, solicitado_en)
 where confirmado_en is null;

comment on table borrado_pendiente is
  'R5.3 — un pendiente por (persona, sistema). La baja en la bóveda nunca '
  'espera al consumidor: se completa igual y esto queda abierto.';

-- El tablero del DPO: qué está abierto y desde hace cuánto.
create view v_borrados_sin_confirmar as
select
  b.id_persona, b.sistema, b.alcance, b.finalidad,
  b.solicitado_en, b.intentos, b.ultimo_error,
  s.nombre as sistema_nombre, s.contacto_tecnico,
  (now() - b.solicitado_en)                as antiguedad,
  extract(day from now() - b.solicitado_en)::int as dias_abierto
  from borrado_pendiente b
  join sistema_consumidor s on s.codigo = b.sistema
 where b.confirmado_en is null
 order by b.solicitado_en;

-- Genera los pendientes de una baja. La llama `bajas.py`; existe como función
-- de base y no solo en Python para que un consumidor futuro que borre por su
-- cuenta dispare la misma cascada.
--
-- `p_finalidad` null = baja total. Con finalidad, solo reciben pendiente los
-- sistemas cuyo alcance declarado la incluye: un retiro de `uso_semantico` no
-- tiene por qué molestar a un sistema que no trata esa finalidad.
create function generar_borrados_pendientes(
    p_id_persona uuid,
    p_finalidad  text default null)
returns int
language plpgsql as $$
declare
  cuantos int;
begin
  insert into borrado_pendiente (id_persona, sistema, alcance, finalidad)
  select p_id_persona, s.codigo,
         coalesce(p_finalidad, 'total'), p_finalidad
    from sistema_consumidor s
   where s.activo
     -- Una baja anterior al alta del consumidor no es responsabilidad suya, y
     -- su fecha de alta es lo que lo justifica.
     and s.alta_en <= now()
     and (p_finalidad is null or p_finalidad = any(s.alcance_finalidades))
  on conflict (id_persona, sistema, alcance) do nothing;
  get diagnostics cuantos = row_count;
  return cuantos;
end;
$$;

-- Lo que un consumidor puede hacer con sus pendientes. Las tres funciones
-- derivan el sistema de la conexión: un consumidor no puede confirmar por
-- otro.
create function mis_borrados_pendientes()
returns table (id_persona uuid, alcance text, finalidad text,
               solicitado_en timestamptz, intentos int)
language sql stable
security definer
set search_path = public
as $$
  select b.id_persona, b.alcance, b.finalidad, b.solicitado_en, b.intentos
    from borrado_pendiente b
   where b.sistema = sistema_de_la_conexion()
     and b.confirmado_en is null
   order by b.solicitado_en;
$$;

create function confirmar_borrado(p_id_persona uuid,
                                  p_alcance text default 'total')
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update borrado_pendiente
     set confirmado_en = now(), ultimo_error = null
   where id_persona = p_id_persona
     and alcance = p_alcance
     and sistema = sistema_de_la_conexion()
     and confirmado_en is null;
  if not found then
    raise exception
      'No hay un borrado pendiente de % para el sistema %.',
      p_id_persona, sistema_de_la_conexion()
      using errcode = 'no_data_found';
  end if;
end;
$$;

create function reportar_error_de_borrado(p_id_persona uuid,
                                          p_error text,
                                          p_alcance text default 'total')
returns void
language sql
security definer
set search_path = public
as $$
  update borrado_pendiente
     set intentos = intentos + 1, ultimo_error = p_error
   where id_persona = p_id_persona
     and alcance = p_alcance
     and sistema = sistema_de_la_conexion()
     and confirmado_en is null;
$$;

-- ============================================================
--  6 · R5.4 · Roles con privilegio mínimo
-- ============================================================
-- En Cloud SQL, todo usuario creado con `gcloud sql users create` recibe
-- `cloudsqlsuperuser`, con `CREATEROLE`: podría otorgarse de vuelta todo lo
-- que se le revoque, y esta lista no valdría nada. Por eso **`coloquio_app`
-- se crea como usuario IAM de cuenta de servicio**, que no recibe ningún rol
-- automáticamente. Ver el manual de despliegue de la fase.
--
-- La migración no crea el rol: lo espera. Si no existe, falla acá y el
-- mensaje dice qué hacer, que es mejor que otorgar privilegios a la nada.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'coloquio_app') then
    raise exception
      E'No existe el rol `coloquio_app`.\\n\\n'
      'Se crea **antes** de esta migración, y como usuario IAM de cuenta de '
      'servicio, no con `gcloud sql users create` tradicional: ese camino '
      'otorga `cloudsqlsuperuser` y con él `CREATEROLE`, o sea que el rol '
      'podría devolverse a sí mismo todo lo que acá se le niega.\\n\\n'
      'En el cluster local de pruebas alcanza con `create role coloquio_app '
      'login`. Ver «DESPLIEGUE - COLOQUIO Fase 0.md».';
  end if;
end;
$$;

-- COLOQUIO entra al registro acá, junto con sus privilegios: tener el rol y
-- no estar registrado sería lo peor de los dos mundos —se conecta, y
-- `sistema_de_la_conexion()` no sabe quién es, así que todo lo que haga
-- falla—.
--
-- Entra **inactivo**. `activo` es lo que decide si una baja le genera un
-- pendiente, y mientras COLOQUIO no esté en producción no hay nada del otro
-- lado que pueda confirmarlo: cada baja dejaría una fila abierta para
-- siempre y `v_borrados_sin_confirmar` —el tablero del DPO— nacería lleno de
-- ruido. Se activa el día que COLOQUIO sale a producción, que es también el
-- día en que empieza a ser responsable:
--
--   update sistema_consumidor
--      set activo = true, alta_en = now()
--    where codigo = 'coloquio';
--
-- `alta_en` se mueve junto con el activo por la misma razón: la fecha de
-- alta es la que justifica que una baja anterior no sea suya.
insert into sistema_consumidor
       (codigo, nombre, activo, alcance_finalidades, contacto_tecnico, rol_bd)
values ('coloquio', 'COLOQUIO — investigación cualitativa', false,
        array['contacto_participacion', 'grabacion_av',
              'moderacion_automatizada', 'uso_semantico_cuali',
              'difusion_verbatim'],
        'equipo de COLOQUIO', 'coloquio_app')
on conflict (codigo) do nothing;

-- Nada por defecto, ni siquiera lo que Postgres regala.
--
-- Y Postgres regala bastante: **una función nace con `execute` otorgado a
-- `public`**. Sin revocarlo, cualquier rol que pueda conectarse a la base
-- ejecuta `contacto_para_convocatoria` y lee contactos, y la lista blanca de
-- este requisito no describe nada. Se revoca de todas las de esta fase,
-- primero, y después se otorga a quien corresponda.
revoke execute on function contacto_para_convocatoria(uuid, text, text, text)
    from public;
revoke execute on function sistema_de_la_conexion()        from public;
revoke execute on function f_persona_convocable()          from public;
revoke execute on function mis_borrados_pendientes()       from public;
revoke execute on function confirmar_borrado(uuid, text)   from public;
revoke execute on function reportar_error_de_borrado(uuid, text, text)
    from public;
revoke execute on function generar_borrados_pendientes(uuid, text) from public;

revoke all on schema public from coloquio_app;
grant usage on schema public to coloquio_app;

-- Lectura: solo la superficie del contrato.
grant select on v_persona_convocable         to coloquio_app;
grant select on v_fatiga_panelista           to coloquio_app;
grant select on v_finalidad                  to coloquio_app;
grant select on v_texto_consentimiento_activo to coloquio_app;

-- Ejecución: las funciones, que son las que aplican el gate y auditan.
grant execute on function contacto_para_convocatoria(uuid, text, text, text)
    to coloquio_app;
grant execute on function mis_borrados_pendientes()      to coloquio_app;
grant execute on function confirmar_borrado(uuid, text)  to coloquio_app;
grant execute on function reportar_error_de_borrado(uuid, text, text)
    to coloquio_app;
grant execute on function sistema_de_la_conexion()       to coloquio_app;
-- La función detrás de la superficie de convocables. Se otorga porque la
-- vista no puede prestarle su permiso (ver el comentario de arriba), y no es
-- una puerta de atrás porque el gate está adentro de la función.
grant execute on function f_persona_convocable()         to coloquio_app;

-- Y nada más. En particular, **cero** sobre las tablas: `persona`,
-- `consentimiento`, `participacion`, `membresia`, `encuesta`. La RLS sin
-- políticas ya lo garantizaría, pero decirlo explícito es lo que hace que la
-- prueba de privilegios efectivos sea legible.
revoke all on all tables in schema public from coloquio_app;
grant select on v_persona_convocable         to coloquio_app;
grant select on v_fatiga_panelista           to coloquio_app;
grant select on v_finalidad                  to coloquio_app;
grant select on v_texto_consentimiento_activo to coloquio_app;

-- Solo lectura de auditoría y cumplimiento, para el DPO y los chequeos
-- automáticos. No lee PII.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'plataforma_ro') then
    execute 'grant usage on schema public to plataforma_ro';
    execute 'grant select on reidentificacion, usuario_auditoria, '
            'persona_borrada, borrado_pendiente, v_borrados_sin_confirmar, '
            'sistema_consumidor, finalidad_consentimiento to plataforma_ro';
  end if;
end;
$$;
