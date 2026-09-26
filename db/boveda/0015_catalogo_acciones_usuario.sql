-- R2.12 — El enlace de acceso se puede volver a generar, y eso hay que auditarlo.
--
-- El alta de un usuario devuelve un enlace para que la persona fije su clave,
-- y ese enlace se muestra **una sola vez**: no se guarda en ningún lado, que
-- es lo correcto —un enlace de restablecimiento guardado es una credencial
-- guardada—. Pero «no se guarda» se había implementado como «no se puede
-- volver a obtener», y son dos cosas distintas: si quien da el alta cierra el
-- modal antes de copiarlo, el usuario nuevo queda sin forma de entrar salvo
-- que sepa usar «¿Olvidaste tu contraseña?» en una pantalla que todavía no
-- conoce.
--
-- Generar uno nuevo es exactamente lo mismo que esa opción del login, así que
-- no agrega riesgo. Lo que sí hace falta es que **quede auditado**: pedir el
-- enlace de acceso de otra persona es una operación sensible aunque no cambie
-- nada, y sin rastro no se puede distinguir de un abuso.
--
-- ============================================================
--  Por qué un catálogo y no un `check` más largo
-- ============================================================
-- `usuario_auditoria.accion` tenía un `check` con las cinco acciones que
-- existían. Agregar la sexta a mano sería un `alter table` por acción, cada
-- vez, y —más importante para este repo— **una migración que solo cambia una
-- restricción es invisible para el diagnóstico de esquema**: no crea ningún
-- objeto que `verificar_esquema.py` pueda buscar, así que la daría por
-- aplicada sin haberla mirado.
--
-- Es el mismo razonamiento de la Fase 5 con las finalidades (D45): lo que
-- parecía una lista de valores permitidos es en realidad un catálogo, y como
-- catálogo tiene lugar donde guardar la etiqueta que hoy está duplicada en el
-- frontend.

create table accion_usuario (
  codigo      text primary key,
  etiqueta    text not null,
  descripcion text not null,
  orden       int  not null default 100
);

comment on table accion_usuario is
  'R2.12 — las acciones de gestión de usuarios que se auditan. Reemplaza al '
  '`check` de `usuario_auditoria.accion`.';

insert into accion_usuario (codigo, etiqueta, descripcion, orden) values
  ('alta',           'Alta',
   'Se creó la cuenta y se le asignó un rol.', 10),
  ('cambio_rol',     'Cambio de rol',
   'Cambió lo que la persona puede hacer.', 20),
  ('actualizacion',  'Actualización',
   'Cambió el nombre o se puso al día la ficha.', 30),
  ('desactivacion',  'Desactivación',
   'Se apagó el acceso. No se borró nada.', 40),
  ('reactivacion',   'Reactivación',
   'Se volvió a habilitar el acceso.', 50),
  -- La nueva.
  ('enlace_acceso',  'Enlace de acceso',
   'Se generó un enlace nuevo para que la persona fije su clave. '
   'Equivale a «¿Olvidaste tu contraseña?», pedido por un administrador.', 60);

-- El `check` sale y entra la FK. Las filas históricas ya usan los cinco
-- códigos sembrados arriba, así que la FK valida sin tocar nada; si alguna
-- fila tuviera un valor fuera del catálogo, la migración fallaría acá, que es
-- lo que hay que querer.
alter table usuario_auditoria drop constraint if exists usuario_auditoria_accion_check;
alter table usuario_auditoria
  add constraint usuario_auditoria_accion_fk
      foreign key (accion) references accion_usuario(codigo);

-- La auditoría con su etiqueta resuelta, para que el frontend no tenga que
-- repetir el diccionario y quedarse viejo cuando aparezca una acción nueva.
create view v_usuario_auditoria as
select a.id, a.accion, c.etiqueta as accion_etiqueta, c.descripcion as accion_descripcion,
       a.uid_objetivo, a.email_objetivo, a.rol_anterior, a.rol_nuevo,
       a.actor_uid, a.actor_email, a.detalle, a.sistema, a.creado_en
  from usuario_auditoria a
  join accion_usuario c on c.codigo = a.accion;
