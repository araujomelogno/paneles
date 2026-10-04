-- ============================================================
--  0020 · Fase 7 — para qué se cruzó la seudonimización
-- ============================================================
--
-- `reidentificacion.motivo` es texto libre desde la 0004. La lista vive en
-- `auditoria.MOTIVOS_REIDENTIFICACION` y en ningún lado más: no hay cómo
-- preguntarle a la base qué motivos existen ni qué significa cada uno, y el
-- registro que sostiene todo el diseño de dos stores se lee con un glosario
-- que está en el código.
--
-- La Fase 7 agrega un motivo nuevo —ver las respuestas procesadas de un
-- panelista (R7.6)— y es buen momento para catalogarlos.
--
-- ── Por qué catálogo y no `check` ──
--
-- La misma razón que `accion_usuario` en la 0015 y `motivo_acceso_portal` en
-- la 0018: una migración que solo cambia una restricción no crea ningún
-- objeto, y `verificar_esquema.py` no tiene qué buscar. La daría por
-- aplicada sin haberla mirado.
--
-- ── Por qué catálogo y NO clave foránea ──
--
-- Esto es lo que distingue esta migración de las otras dos, y conviene no
-- "corregirlo" después. `auditoria.registrar_reidentificacion` dice:
--
--     «No falla nunca por el contenido: si el motivo no está en la lista se
--      guarda igual, porque perder el rastro es peor que guardarlo con una
--      etiqueta rara.»
--
-- Una FK invierte esa decisión: un motivo nuevo que alguien olvidó
-- catalogar deja de escribir la fila, y **se pierde el registro de que
-- alguien reidentificó a alguien**. Para un registro que existe para
-- demostrar quién vio los datos de quién, ese intercambio está al revés.
--
-- El catálogo queda entonces como documentación consultable y como fuente
-- de la pantalla, no como restricción. Que no se desincronice del código lo
-- cuida una prueba espejo —`test_fase7_auditoria.py`—, igual que
-- `pii.CAMPOS_PII` con `campo_pii`.

create table motivo_reidentificacion (
  codigo      text primary key,
  etiqueta    text not null,
  descripcion text not null,
  -- Si el motivo expone datos de contacto (nombre, documento, correo,
  -- celular) o solo vincula la identidad con su contenido. No es lo mismo
  -- y la pantalla de auditoría los muestra distinto.
  expone_pii  boolean not null default true,
  orden       int not null default 100
);

comment on table motivo_reidentificacion is
  'Fase 7 — para qué se cruzó la seudonimización en cada fila de '
  '`reidentificacion`. Es un catálogo sin clave foránea a propósito: '
  'ver el encabezado de la 0020.';

insert into motivo_reidentificacion (codigo, etiqueta, descripcion, expone_pii, orden) values
  ('ficha',        'Se abrió la ficha de un panelista',
   'Alguien pasó de un id_persona a la ficha con sus datos de contacto.',
   true, 10),
  ('consulta',     'Se resolvió un resultado de consulta',
   'Un ranking de identificadores se tradujo a personas con nombre.',
   true, 20),
  ('convocatoria', 'Se armó una convocatoria',
   'La muestra se resolvió a datos de contacto para poder invitar.',
   true, 30),
  ('exportacion',  'Se bajó un archivo con datos personales',
   'El CSV reidentificado de R3.10. El archivo sale del sistema, así que '
   'es el motivo con la huella más larga.', true, 40),
  ('cumplimiento', 'Atención de un pedido de baja o de acceso',
   'El DPO resolvió un derecho de la persona y necesitó sus datos.',
   true, 50),
  -- R7.6 — el motivo nuevo, y el único que no expone PII.
  ('respuestas_panelista', 'Se vieron las respuestas de un panelista',
   'La ficha mostró qué respondió esa persona y en qué estudio. No revela '
   'nombre ni contacto, pero une identidad y contenido —las dos cosas que '
   'los dos stores mantienen separadas—, así que se registra igual.',
   false, 60);

-- ============================================================
--  La vista, que es además lo que hace detectable la migración
-- ============================================================
-- `left join` y no `join`: una fila con un motivo que no está en el
-- catálogo **tiene que seguir apareciendo**. Si la vista la escondiera,
-- catalogar mal volvería invisible una reidentificación, que es
-- exactamente lo que el diseño no puede permitirse. `coalesce` deja ver
-- que el motivo es desconocido en vez de callarlo.
create view v_reidentificacion as
select
  r.id,
  r.id_persona,
  r.actor_uid,
  r.actor_email,
  r.motivo,
  coalesce(m.etiqueta, '(motivo sin catalogar: ' || r.motivo || ')')
                                     as motivo_etiqueta,
  coalesce(m.expone_pii, true)       as expone_pii,
  m.codigo is null                   as motivo_desconocido,
  r.contexto,
  r.creado_en
  from reidentificacion r
  left join motivo_reidentificacion m on m.codigo = r.motivo;

comment on view v_reidentificacion is
  'El registro de reidentificación con la etiqueta de su motivo. Un motivo '
  'sin catalogar se marca como tal y NO se esconde.';

-- ============================================================
--  Lo que Postgres deja abierto
-- ============================================================
-- `public` recibe permisos sobre todo objeto nuevo, y la lista blanca de
-- privilegios efectivos de `coloquio_app` vive en el repo justamente para
-- que una migración que abre un acceso de más rompa la build. Sin estos
-- `revoke`, `scripts/verificar_coloquio.py` falla — y hace bien: el
-- registro de quién reidentificó a quién no es asunto de otro consumidor.
revoke all on motivo_reidentificacion from public;
revoke all on v_reidentificacion from public;

alter table motivo_reidentificacion enable row level security;
