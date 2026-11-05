-- 0033 · De dónde viene cada encuesta (PEDIDO encuestas sin convocatoria).
--
-- Desde la 0032 una encuesta independiente puede venir de dos lados que la
-- pantalla no distinguía: de una consulta («Crear encuesta con estos
-- panelistas»), con audiencia, o de una carga (un archivo de respuestas, o
-- una carga migrada), sin audiencia. Una encuesta de una carga se mostraba
-- con una sección «Convocatoria» que listaba como convocadas a personas que
-- nadie convocó. El origen se guarda explícito y se fija al crearla:
--
--   panel    — de un panel (`tipo = 'panel'`)
--   consulta — independiente, su audiencia es la foto de una consulta
--   carga    — independiente, llegó como archivo de respuestas
--
-- Aditiva: no borra ni reescribe datos, solo completa la columna nueva.

begin;

alter table encuesta add column if not exists origen text;

-- Las que ya existen. Las de panel son de panel. Una independiente con
-- alguien convocado vino de una consulta (su audiencia es eso). Las demás
-- llegaron como archivo: entre ellas, todas las que creó la 0032 a partir
-- de una carga existente, que no tienen ningún convocado.
update encuesta e
   set origen = case
         when e.tipo = 'panel' then 'panel'
         when exists (select 1 from participacion pa
                       where pa.encuesta_id = e.id and pa.convocado) then 'consulta'
         else 'carga'
       end
 where e.origen is null;

-- Una inserción que no lo dice (SQL a mano, código anterior a esta
-- migración) se completa sola: de panel si es de panel, y si no de carga,
-- que es lo que crea el código anterior sin decirlo. La encuesta desde una
-- consulta lo declara siempre (`encuestas.crear_desde_consulta`).
create or replace function encuesta_origen_por_defecto()
returns trigger language plpgsql as $$
begin
  if new.origen is null then
    new.origen := case when new.tipo = 'panel' then 'panel' else 'carga' end;
  end if;
  return new;
end;
$$;

drop trigger if exists encuesta_origen_por_defecto on encuesta;
create trigger encuesta_origen_por_defecto
  before insert on encuesta
  for each row execute function encuesta_origen_por_defecto();

revoke execute on function encuesta_origen_por_defecto() from public;

alter table encuesta alter column origen set not null;

alter table encuesta drop constraint if exists encuesta_origen_check;
alter table encuesta add constraint encuesta_origen_check
  check (origen in ('panel', 'consulta', 'carga'));
-- El origen y el tipo dicen lo mismo de la parte «panel»: no pueden
-- contradecirse.
alter table encuesta drop constraint if exists encuesta_origen_y_tipo;
alter table encuesta add constraint encuesta_origen_y_tipo
  check ((tipo = 'panel') = (origen = 'panel'));

comment on column encuesta.origen is
  'De dónde viene: panel · consulta (independiente, audiencia = la foto de '
  'una consulta) · carga (independiente, llegó como archivo de respuestas, '
  'sin audiencia). Se fija al crearla y decide si la pantalla muestra '
  '«Convocatoria» o «Respondentes».';

commit;
