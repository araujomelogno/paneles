-- ============================================================
--  Revertir boveda/0031 · clientes y lista negra
-- ============================================================
--
-- **No es una migración** y por eso no está en `db/boveda/`. Se corre
-- **después** de volver a desplegar el código anterior, que no conoce ni
-- clientes ni lista negra.
--
-- **Pierde datos, a propósito:** los clientes de cada carga y toda la
-- historia de la lista negra. Exportar antes si hay alguna chance de volver:
--
--   \copy (select c.nombre as carga, cl.nombre as cliente from carga_cliente cc
--            join carga c on c.id = cc.carga_id join cliente cl on cl.id = cc.cliente_id)
--     to 'carga_cliente.csv' csv header
--   \copy (select * from lista_negra order by id) to 'lista_negra.csv' csv header
--
-- Y devuelve `f_persona_convocable()` a la definición de la 0021: quien
-- estaba en la lista negra vuelve a ser convocable, también para COLOQUIO.
-- Correr `scripts/verificar_coloquio.py` después.

begin;

create or replace function f_persona_convocable()
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
    left join v_demografia d on d.id_persona = p.id_persona
   where p.estado = 'activa'
     and exists (select 1 from consentimiento c
                  where c.id_persona = p.id_persona
                    and c.estado = 'vigente'
                    and c.ref_estudio is null)
     and not exists (select 1 from persona_borrada b
                      where b.id_persona = p.id_persona);
$$;

drop view if exists v_persona_cliente;
drop table if exists lista_negra;
drop table if exists carga_cliente;
drop table if exists cliente;

commit;
