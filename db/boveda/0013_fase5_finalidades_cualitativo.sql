-- R5.7.b/c/d — Las finalidades del cualitativo, el alcance por estudio y la
-- exigencia de texto activo.
--
-- Es la mitad legal de la fase: lo que le da base jurídica a COLOQUIO antes
-- de que encienda una cámara. Depende de la 0012.

-- ============================================================
--  b · Las cuatro finalidades nuevas
-- ============================================================
-- `uso_semantico_cuali` es **distinta** de `uso_semantico`, y la distinción
-- no es burocrática: una autoriza indexar lo que alguien marcó en una
-- encuesta, la otra lo que dijo hablando. Mezclarlas sería exactamente el
-- error de limitación de finalidad que el diseño original evitó cuando
-- separó `contacto_participacion` de `uso_semantico`.
--
-- `difusion_verbatim` es la más delicada de las cuatro y por eso tiene fila
-- propia: **es la única en la que el dato sale de Equipos**. Grabar es
-- tratamiento interno; entregarle al cliente el video de la cara de alguien
-- diciendo una frase es otra cosa, y quien la retire tiene que poder
-- retirarla sin retirar las otras tres.

insert into finalidad_consentimiento
       (codigo, descripcion, requiere_texto, ambito, orden)
values
  ('grabacion_av',
   'Grabar audio y video de la sesión en la que participa.',
   true, 'estudio', 30),
  ('moderacion_automatizada',
   'Que la sesión la conduzca un sistema automatizado en lugar de una '
   'persona.',
   true, 'estudio', 40),
  ('uso_semantico_cuali',
   'Indexar su verbatim despersonalizado en el corpus cualitativo '
   'consultable entre estudios. Distinta de `uso_semantico`, que es sobre '
   'respuestas de encuesta.',
   true, 'persona', 50),
  ('difusion_verbatim',
   'Entregar al cliente del estudio un clip o una cita identificable. Es la '
   'única finalidad en la que el dato sale de Equipos.',
   true, 'estudio', 60);

-- ============================================================
--  c · Consentimiento con alcance de estudio
-- ============================================================
-- Que alguien haya aceptado que lo graben en un grupo de marzo no autoriza a
-- grabarlo en uno de agosto. `consentimiento` ya tenía `panel_id` nullable
-- —persona o panel—; el cualitativo necesita el tercer alcance.
--
-- `ref_estudio` es la misma uuid que ya comparten `encuesta` (bóveda) y
-- `cuestionario` (semántico). No lleva FK a `encuesta`: un estudio
-- cualitativo no es una encuesta, y el día que exista su propia tabla esta
-- columna la referencia igual. Es la misma referencia lógica de siempre.

alter table consentimiento add column ref_estudio uuid;

create index consentimiento_por_estudio
    on consentimiento (id_persona, finalidad, ref_estudio)
 where ref_estudio is not null;

comment on column consentimiento.ref_estudio is
  'R5.7.c — el estudio para el que vale este consentimiento. Obligatorio '
  'cuando la finalidad es de ámbito estudio, prohibido cuando es de ámbito '
  'persona.';

-- El catálogo declara el ámbito; acá se hace valer. Un `check` no alcanza
-- porque la regla depende de otra tabla, así que va como trigger.
create function consentimiento_valida_ambito() returns trigger
language plpgsql as $$
declare
  el_ambito text;
begin
  select ambito into el_ambito
    from finalidad_consentimiento where codigo = new.finalidad;

  if el_ambito = 'estudio' and new.ref_estudio is null then
    raise exception
      'La finalidad «%» es de ámbito estudio: hace falta `ref_estudio`. '
      'Un consentimiento de grabación sin estudio no dice a qué sesión '
      'aplica, y entonces no autoriza ninguna.', new.finalidad
      using errcode = 'check_violation';
  end if;

  if el_ambito <> 'estudio' and new.ref_estudio is not null then
    raise exception
      'La finalidad «%» es de ámbito %: no lleva `ref_estudio`. Aceptarlo '
      'daría a entender que el permiso está acotado a ese estudio cuando en '
      'realidad vale para toda la persona.', new.finalidad, el_ambito
      using errcode = 'check_violation';
  end if;

  if el_ambito = 'panel' and new.panel_id is null then
    raise exception 'La finalidad «%» es de ámbito panel: hace falta '
                    '`panel_id`.', new.finalidad
      using errcode = 'check_violation';
  end if;

  return new;
end;
$$;

create trigger consentimiento_ambito
  before insert or update of finalidad, ref_estudio, panel_id
  on consentimiento
  for each row execute function consentimiento_valida_ambito();

-- ============================================================
--  d · Sin texto activo no hay otorgamiento
-- ============================================================
-- `texto_consentimiento` existía y nadie la obligaba a nada: se podía otorgar
-- una finalidad con una `version_texto` escrita a mano que no correspondiera
-- a ningún texto publicado. Eso convierte al consentimiento en una cadena de
-- caracteres en vez de en algo demostrable: si mañana alguien pregunta «¿qué
-- aceptó exactamente esta persona?», la respuesta tiene que ser un texto
-- recuperable, no una etiqueta.
--
-- Se valida sobre `insert` únicamente. Un `update` que retira el
-- consentimiento no puede quedar bloqueado porque el texto se haya
-- desactivado en el medio: **retirar siempre tiene que poder hacerse**, y esa
-- es la razón por la que el trigger no mira los updates.

create function consentimiento_exige_texto() returns trigger
language plpgsql as $$
declare
  exige boolean;
  hay_version boolean;
begin
  select requiere_texto into exige
    from finalidad_consentimiento where codigo = new.finalidad;
  if not exige then
    return new;
  end if;

  select exists (
    select 1 from texto_consentimiento
     where finalidad = new.finalidad
       and version = new.version_texto
       and activo
  ) into hay_version;

  if not hay_version then
    raise exception
      'No hay un texto activo «%» para la finalidad «%». Un consentimiento '
      'sin texto publicado no es demostrable: hay que publicar la versión '
      'antes de otorgarla.', new.version_texto, new.finalidad
      using errcode = 'foreign_key_violation';
  end if;
  return new;
end;
$$;

create trigger consentimiento_texto
  before insert on consentimiento
  for each row execute function consentimiento_exige_texto();

-- ── Chequeo previo: que esta migración no rompa el alta ──────────────
-- La regla de arriba es correcta y es la que pide R5.7.d, pero si se aplica
-- sobre una base donde las versiones que la aplicación viene usando **no**
-- están publicadas, el efecto no es «ahora el consentimiento es demostrable»:
-- es «a partir de este momento no se puede dar de alta a nadie», y el error
-- aparece recién en el primer alta, lejos de acá.
--
-- Así que la migración se niega a aplicarse en ese estado. Publicar los
-- textos es un paso previo, no una consecuencia a descubrir.
do $$
declare
  faltantes text;
begin
  select string_agg(distinct format('%L de la finalidad %L',
                                    c.version_texto, c.finalidad), ', ')
    into faltantes
    from consentimiento c
    join finalidad_consentimiento f
      on f.codigo = c.finalidad and f.requiere_texto
   where c.estado = 'vigente'
     and not exists (
           select 1 from texto_consentimiento t
            where t.finalidad = c.finalidad
              and t.version = c.version_texto
              and t.activo);

  if faltantes is not null then
    raise exception
      E'Hay consentimientos vigentes cuya versión de texto no está publicada '
      'y activa: %.\n\n'
      'Desde esta migración, otorgar exige una versión activa en '
      '`texto_consentimiento`. Si se aplica así, el alta de panelistas deja '
      'de funcionar.\n\n'
      'Publicá esos textos primero (pantalla Inscripciones → Textos de '
      'consentimiento, o un insert en `texto_consentimiento`) y volvé a '
      'correr esta migración.', faltantes;
  end if;
end;
$$;

-- ============================================================
--  Las finalidades de una inscripción
-- ============================================================
-- `inscripcion.finalidades` es un `text[]` y un array no admite FK. Es el
-- punto más frágil del catálogo, porque es la única validación del dominio
-- que no la hace una clave foránea: sin esto, la landing podría guardar una
-- finalidad inventada y el error aparecería recién al aprobar.

create function inscripcion_valida_finalidades() returns trigger
language plpgsql as $$
declare
  desconocidas text[];
begin
  select array_agg(f) into desconocidas
    from unnest(new.finalidades) as f
   where not exists (select 1 from finalidad_consentimiento
                      where codigo = f and activa);
  if desconocidas is not null then
    raise exception
      'Finalidades desconocidas o inactivas en la inscripción: %. El '
      'dominio lo define `finalidad_consentimiento`.', desconocidas
      using errcode = 'foreign_key_violation';
  end if;
  return new;
end;
$$;

create trigger inscripcion_finalidades
  before insert or update of finalidades on inscripcion
  for each row execute function inscripcion_valida_finalidades();
