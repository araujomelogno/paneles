-- R6.1.a — El panelista entra con usuario y contraseña.
--
-- ============================================================
--  Qué reemplaza, y por qué queda la tabla
-- ============================================================
-- La Fase 6 resolvió el acceso con un **enlace de un solo uso por visita**:
-- cada vez que el panelista quería ver sus puntos pedía un correo, lo
-- esperaba y hacía clic. Para un portal al que se entra cada varios meses esa
-- fricción es la diferencia entre un portal que se usa y uno al que nadie
-- vuelve, y además deja el acceso a merced de la entregabilidad del correo.
--
-- El enlace **no desaparece**: deja de ser la forma de entrar y pasa a ser la
-- forma de **establecer** y **recuperar** la contraseña. Una vez cada tanto,
-- no una vez por visita. Por eso `acceso_portal` sigue siendo la tabla
-- correcta —token de un solo uso, vencimiento, correo hasheado, límite por
-- origen— y lo único que le falta es poder distinguir para qué se emitió cada
-- fila.
--
-- ============================================================
--  Por qué hay una migración si la spec dice «ningún cambio de esquema»
-- ============================================================
-- La spec (§6) dice que no hace falta tocar la bóveda, y para la credencial
-- es cierto: la contraseña la administra Firebase Auth y el vínculo
-- `uid` ↔ `id_persona` ya existe. Lo que la spec no mira es el límite de
-- tasa de R6.1.b: «intentos **fallidos de login** repetidos se limitan por
-- correo y por origen».
--
-- Ese contador no puede compartir fila con el de los enlaces. Si lo
-- compartiera, cinco intentos de adivinar una contraseña dejarían a la
-- persona sin poder pedir el enlace para recuperarla —el ataque le cerraría
-- justo la puerta de salida—. Son dos límites con dos umbrales y dos
-- propósitos, y para separarlos alcanza con una columna.
alter table acceso_portal
  add column if not exists motivo text not null default 'enlace';

-- El catálogo, y no un `check`, por lo mismo que `accion_usuario` en la 0015:
-- una migración que solo cambia una restricción es invisible para
-- `verificar_esquema.py`, que no tiene ningún objeto que buscar y la daría
-- por aplicada sin haberla mirado.
create table motivo_acceso_portal (
  codigo      text primary key,
  etiqueta    text not null,
  descripcion text not null,
  -- Si la fila es un **intento** (no una emisión). Lo usa la vista del DPO
  -- para no mezclar «se emitió un enlace» con «alguien erró la contraseña».
  es_intento  boolean not null default false,
  orden       int not null default 100
);

comment on table motivo_acceso_portal is
  'R6.1.a — para qué se escribió cada fila de `acceso_portal`. Separa los '
  'enlaces emitidos de los intentos fallidos de login, que tienen umbrales '
  'distintos a propósito.';

insert into motivo_acceso_portal (codigo, etiqueta, descripcion, es_intento, orden) values
  ('alta_clave',    'Enlace para crear la contraseña',
   'El panelista todavía no tenía contraseña: se le emitió un enlace para '
   'que la establezca. Lo dispara el alta de una inscripción aprobada o un '
   'responsable desde la administración.', false, 10),
  ('recuperacion',  'Enlace para recuperar la contraseña',
   'El panelista dijo que la olvidó. Mismo mecanismo que el de alta: el '
   'enlace vence y sirve una sola vez.', false, 20),
  ('login_fallido', 'Intento de ingreso fallido',
   'Alguien probó una credencial que no entró. Se registra con el correo '
   'hasheado, sea o no de un panelista: si solo contaran los de panelistas '
   'reales, probar direcciones ajenas no tendría límite.', true, 30),
  -- El valor con el que nacieron las filas de la Fase 6: enlaces de ingreso
  -- del mecanismo anterior. Se conserva para que la FK valide sin tocar
  -- historia; no lo escribe nadie más.
  ('enlace',        'Enlace de ingreso (Fase 6)',
   'El enlace mágico con el que se entraba antes de R6.1.a. Histórico: el '
   'código ya no emite ninguno.', false, 90);

alter table acceso_portal
  add constraint acceso_portal_motivo_fk
      foreign key (motivo) references motivo_acceso_portal(codigo);

comment on column acceso_portal.motivo is
  'R6.1.a — para qué es esta fila. Los umbrales del límite de tasa se '
  'cuentan por motivo: adivinar una contraseña no tiene que consumir los '
  'pedidos de recuperación de la víctima.';

-- ============================================================
--  Quién pidió la emisión
-- ============================================================
-- R6.1.a pide que la emisión quede auditada «como la de usuarios internos».
-- Va acá y no en `usuario_auditoria` por una razón de lectura: esa tabla es
-- el registro de **escalada de privilegios entre empleados de Equipos**, y es
-- lo que se mira para responder «quién puede ver la bóveda». Mezclarle
-- miles de panelistas —que no son usuarios de la aplicación y no tienen
-- rol— la volvería ilegible justo para la pregunta que existe para
-- contestar.
--
-- Null cuando lo pidió el propio titular desde el portal, que es el caso
-- normal. Con valor cuando lo disparó alguien de Equipos, que es el caso que
-- hay que poder auditar.
alter table acceso_portal add column if not exists emitido_por text;
alter table acceso_portal add column if not exists emitido_por_email text;

comment on column acceso_portal.emitido_por is
  'R6.1.a — uid del responsable que disparó el envío, o null si lo pidió el '
  'propio panelista. Quien lo dispara nunca ve ni define la contraseña: lo '
  'único que puede hacer es que salga el correo.';

-- Los índices de la 0017 cuentan por correo y por origen sin mirar el
-- motivo. Ahora el conteo es por motivo, así que se reemplazan: un índice
-- que no cubre la columna del `where` deja el límite de tasa haciendo
-- `seq scan` sobre una tabla que crece con cada intento fallido del mundo.
drop index if exists acceso_portal_por_email;
drop index if exists acceso_portal_por_origen;
create index acceso_portal_por_email on acceso_portal (email_hash, motivo, creado_en desc);
create index acceso_portal_por_origen on acceso_portal (origen_hash, motivo, creado_en desc)
  where origen_hash is not null;

-- ============================================================
--  Lo que mira el DPO
-- ============================================================
-- Sin esta vista, contestar «¿alguien está probando contraseñas contra el
-- panel?» es escribir la consulta a mano cada vez, y la primera vez que
-- hiciera falta sería tarde. No lleva el correo en claro —no existe en la
-- tabla— ni el token: lo que muestra es forma, no contenido.
create view v_acceso_portal as
select
  a.id, a.motivo, m.etiqueta as motivo_etiqueta, m.es_intento,
  a.id_persona,
  -- Si el pedido correspondía a un panelista. Para un intento fallido es la
  -- diferencia entre «alguien se equivocó la contraseña» y «alguien está
  -- probando direcciones que no existen».
  (a.id_persona is not null) as era_panelista,
  a.emitido_por, a.emitido_por_email,
  a.vence_en, a.usado_en,
  (a.token_hash is not null and a.usado_en is null and a.vence_en > now())
    as sigue_sirviendo,
  a.creado_en
  from acceso_portal a
  join motivo_acceso_portal m on m.codigo = a.motivo;

comment on view v_acceso_portal is
  'R6.1.a — los pedidos de acceso al portal con su motivo resuelto: qué '
  'enlaces se emitieron, quién los disparó, cuáles siguen sirviendo y qué '
  'intentos fallaron. Sin el correo (no está en claro) y sin el token.';
