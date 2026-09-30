-- R5.2.a — La convocatoria activa se verifica **por sistema**, y un consumidor
--           externo puede declarar la suya.
--
-- ============================================================
--  El hueco que esta migración cierra
-- ============================================================
-- R5.2 pide que `contacto_para_convocatoria()` exija «una convocatoria activa
-- **en el sistema que llama**», y remite a R5.4: «el registro de consumidores
-- declara cómo se verifica eso para cada uno».
--
-- La `0014` implementó el caso de `paneles` —una `participacion` en una
-- `encuesta` que no esté cerrada— y lo aplicó a todos. Para el segundo
-- consumidor eso no es un gate: es un muro. COLOQUIO convoca a grupos, sus
-- sesiones viven en su propio store, y la bóveda no las ve; así que pide el
-- contacto de alguien que efectivamente convocó y la bóveda le contesta que
-- no tiene convocatoria activa, salvo que esa persona esté por casualidad en
-- una encuesta abierta de `paneles`.
--
-- Y no lo puede resolver de su lado: escribir en `participacion` le está
-- negado (R5.4), y debe estarlo —fabricar participaciones en encuestas
-- ajenas para poder leer un contacto es justo lo que la superficie externa
-- existe para impedir—.
--
-- ============================================================
--  Por qué una declaración y no una excepción
-- ============================================================
-- La alternativa corta era relajar el chequeo cuando el sistema es
-- `coloquio`: confiar en el consumidor. Es más simple y es peor: deja a
-- COLOQUIO barrer la agenda entera de a una persona por vez, que es
-- exactamente lo que el chequeo existe para impedir. El gate dejaría de ser
-- un gate para el único rol al que se le aplica.
--
-- Acá el consumidor **declara** a quién convocó, con una referencia opaca a
-- su sesión y un vencimiento. Sigue siendo un gate —sin declaración vigente
-- no hay contacto—, la declaración queda escrita con su sistema y su fecha,
-- y el costo de barrer la agenda es dejar una fila por persona barrida: la
-- bóveda no lo impide, lo deja a la vista.
--
-- La declaración **no se escribe por tabla sino por función**, como todo lo
-- demás de esta superficie: es la única forma de reaplicar el gate de
-- consentimiento y de derivar el sistema de la conexión en vez de creerle al
-- llamador.

-- ============================================================
--  1 · La tabla
-- ============================================================
create table convocatoria_externa (
  id_persona   uuid not null references persona(id_persona) on delete cascade,
  sistema      text not null references sistema_consumidor(codigo),
  -- El identificador de la sesión del lado del consumidor. Para la bóveda es
  -- opaco a propósito: no es una FK a nada de acá, y no se interpreta. Sirve
  -- para que dos convocatorias distintas a la misma persona no se pisen, y
  -- para que la auditoría diga a cuál corresponde cada lectura.
  referencia   text not null check (length(trim(referencia)) > 0),
  vence_en     timestamptz not null,
  declarada_en timestamptz not null default now(),
  primary key (id_persona, sistema, referencia)
);

-- La FK a `persona` con `on delete cascade` es la que hace que el borrado de
-- PII se lleve las declaraciones sin que nadie se acuerde de borrarlas. La
-- baja parcial —retiro de `contacto_participacion` sin borrar la persona— la
-- atiende `generar_borrados_pendientes()`, más abajo.
--
-- `borrado_pendiente` justamente **no** tiene esta FK, y la diferencia es
-- deliberada: un pendiente tiene que sobrevivir a la persona (es la prueba de
-- que hay que avisarle a alguien), y una declaración de convocatoria no tiene
-- ningún sentido sin ella.

comment on table convocatoria_externa is
  'R5.2.a — la convocatoria que un consumidor declara en su propio sistema. '
  'Es lo que le da motivo para leer un contacto: sin una vigente, '
  '`contacto_para_convocatoria()` lo rechaza igual que a `paneles` sin '
  'participación abierta.';

-- Para la purga. Las lecturas van por la PK, que empieza en `id_persona`.
create index convocatoria_externa_vencimiento on convocatoria_externa (vence_en);

-- Igual que el resto de las tablas: habilitada y sin políticas, o sea cero
-- filas para todo rol que no sea el dueño. Las funciones de abajo son
-- `security definer` y corren como el dueño, así que pasan; una consulta
-- directa de `coloquio_app` no vería nada aunque alguien le otorgara
-- `select` por error.
alter table convocatoria_externa enable row level security;

-- ============================================================
--  2 · Declarar — la única escritura del consumidor sobre la bóveda
-- ============================================================
create function declarar_convocatoria(
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

comment on function declarar_convocatoria(uuid, text, timestamptz) is
  'R5.2.a — un consumidor declara a quién convocó en su propio sistema. Es '
  'lo único que escribe en la bóveda, y reaplica el gate de consentimiento.';

-- ============================================================
--  3 · Purga — una declaración vencida no se guarda para siempre
-- ============================================================
-- Vencida ya no habilita nada, así que conservarla es guardar el dato de que
-- tal sistema convocó a tal persona sin ninguna finalidad que lo justifique.
-- Se purgan a los 30 días del vencimiento: el margen existe para que la
-- ventana quede auditable un tiempo después de cerrarse.
--
-- No hay cron en la base: la llama la aplicación, como `puntos.vencer()`.
-- Es idempotente y no toca declaraciones vigentes.
create function purgar_convocatorias_externas(p_dias int default 30)
returns int
language plpgsql as $$
declare
  cuantas int;
begin
  delete from convocatoria_externa
   where vence_en < now() - make_interval(days => p_dias);
  get diagnostics cuantas = row_count;
  return cuantas;
end;
$$;

comment on function purgar_convocatorias_externas(int) is
  'R5.2.a — borra las declaraciones vencidas hace más de N días. Vencida no '
  'habilita nada; guardarla sin plazo sería retención sin finalidad.';

-- ============================================================
--  4 · El chequeo de convocatoria, ahora por sistema
-- ============================================================
-- Lo único que cambia respecto de la `0014` es el `select` de
-- `tiene_convocatoria` y el texto del rechazo. El resto —el gate, el canal
-- único, la auditoría en la misma transacción, el vacío explícito— queda
-- igual, y se repite entero porque `create or replace` reemplaza el cuerpo
-- completo.
--
-- `create or replace` **conserva los privilegios** de la función: el
-- `revoke ... from public` y el `grant ... to coloquio_app` de la `0014`
-- siguen valiendo y no hace falta repetirlos. Hay una prueba que lo verifica,
-- porque «siguen valiendo» es exactamente la clase de cosa que uno cree.
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

comment on function contacto_para_convocatoria(uuid, text, text, text) is
  'R5.2 — un solo canal, con el gate aplicado y la lectura auditada en la '
  'misma transacción. Es la única vía: los consumidores no leen `persona`. '
  'Desde R5.2.a la convocatoria activa se verifica por sistema.';

-- ============================================================
--  5 · La cascada de baja se lleva las declaraciones
-- ============================================================
-- La FK de arriba cubre la baja total, que borra la persona. Falta el retiro
-- parcial de `contacto_participacion`: la persona sigue existiendo y sus
-- declaraciones quedarían dando vueltas. No abrirían ninguna puerta —el gate
-- de consentimiento las bloquea igual— pero serían retención sin finalidad.
--
-- Va acá y no en `bajas.py` por lo de siempre: una invariante de cumplimiento
-- escrita en Python es una promesa repetida en dos bases de código, y esta
-- función es el punto por el que pasa toda cascada, venga de donde venga.
create or replace function generar_borrados_pendientes(
    p_id_persona uuid,
    p_finalidad  text default null)
returns int
language plpgsql as $$
declare
  cuantos int;
begin
  -- Si lo que se retira es el contacto —o todo—, las convocatorias
  -- declaradas sobre esta persona dejan de tener sustento.
  if p_finalidad is null or p_finalidad = 'contacto_participacion' then
    delete from convocatoria_externa where id_persona = p_id_persona;
  end if;

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

-- ============================================================
--  6 · Privilegios
-- ============================================================
-- Una función nace con `execute` otorgado a `public`. Se revoca primero y se
-- otorga después, igual que en la `0014`.
revoke execute on function declarar_convocatoria(uuid, text, timestamptz)
    from public;
revoke execute on function purgar_convocatorias_externas(int) from public;

grant execute on function declarar_convocatoria(uuid, text, timestamptz)
    to coloquio_app;

-- La purga **no** se le otorga al consumidor: es mantenimiento de la bóveda,
-- y quien declara no tiene por qué poder borrar declaraciones —ni las suyas
-- ni las de otro—.

-- Cero sobre la tabla, como con todas las demás. La RLS ya lo garantizaría;
-- decirlo explícito es lo que hace legible la prueba de privilegios
-- efectivos.
revoke all on convocatoria_externa from coloquio_app;

-- El DPO y los chequeos automáticos sí la leen: es donde se ve qué sistema
-- declaró qué, que es la contracara de haber abierto esta puerta.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'plataforma_ro') then
    execute 'grant select on convocatoria_externa to plataforma_ro';
  end if;
end;
$$;
