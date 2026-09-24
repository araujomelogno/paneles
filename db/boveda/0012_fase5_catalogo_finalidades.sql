-- R5.7.a — El dominio de finalidades pasa de `check` a catálogo.
--
-- `consentimiento.finalidad` y `texto_consentimiento.finalidad` tenían cada
-- una su propio `check (finalidad in (...))`. Con dos finalidades eso era
-- razonable; con seis, y sabiendo que van a ser más —video, paneles de
-- terceros, transferencias—, agregar una significa migrar dos tablas y
-- acordarse de las dos.
--
-- ---------- Lo que NO cambia -----------------------------------------
-- Sigue siendo **la base** la que rechaza una finalidad inventada. Cambia el
-- mecanismo —de `check` a clave foránea— no la garantía. Esta migración no
-- cambia ningún comportamiento observable: las dos finalidades que existen
-- hoy quedan en el catálogo y todo lo que ya estaba escrito sigue válido.
--
-- ---------- Por qué va sola ------------------------------------------
-- Es puro refactor de dominio y se puede aplicar con la app andando, sin
-- coordinar con nada. Las finalidades del cualitativo van en la 0013, que
-- depende de ésta.

create table finalidad_consentimiento (
  codigo        text primary key,
  descripcion   text not null,
  -- Si es `true`, otorgarla exige que haya una versión activa en
  -- `texto_consentimiento`. Lo hace valer la 0013.
  requiere_texto boolean not null default true,
  -- A qué se refiere el permiso. Decide si `consentimiento.ref_estudio` es
  -- obligatorio, prohibido o indistinto; lo hace valer la 0013.
  --   persona  → vale para la persona, en todo contexto
  --   panel    → vale dentro de un panel
  --   estudio  → vale para un estudio concreto y no para otro
  ambito        text not null default 'persona'
                check (ambito in ('persona','panel','estudio')),
  -- Una finalidad no se borra: se desactiva. Lo ya otorgado sigue siendo
  -- válido y demostrable, que es todo el punto de guardar consentimiento.
  activa        boolean not null default true,
  orden         int not null default 100,
  creado_en     timestamptz not null default now()
);

comment on table finalidad_consentimiento is
  'R5.7.a — el dominio de finalidades. Antes era un check duplicado en dos '
  'tablas; agregar una es ahora insertar una fila.';

-- Las dos que existen hoy, con el mismo significado que tenían.
insert into finalidad_consentimiento
       (codigo, descripcion, requiere_texto, ambito, orden)
values
  ('contacto_participacion',
   'Contactar a la persona para invitarla a participar de un estudio.',
   true, 'persona', 10),
  ('uso_semantico',
   'Indexar sus respuestas de encuesta en el corpus consultable entre '
   'estudios.',
   true, 'persona', 20);

-- ============================================================
--  Del check a la clave foránea
-- ============================================================
-- El orden importa: primero se siembra el catálogo (arriba), después se
-- retiran los checks, y recién entonces se agregan las FK. Al revés, una FK
-- sobre un catálogo vacío rechazaría todas las filas que ya existen.

alter table consentimiento
  drop constraint if exists consentimiento_finalidad_check;
alter table texto_consentimiento
  drop constraint if exists texto_consentimiento_finalidad_check;

alter table consentimiento
  add constraint consentimiento_finalidad_fk
      foreign key (finalidad) references finalidad_consentimiento(codigo);
alter table texto_consentimiento
  add constraint texto_consentimiento_finalidad_fk
      foreign key (finalidad) references finalidad_consentimiento(codigo);

-- La superficie de lectura del catálogo, que es parte del contrato con los
-- consumidores externos (§6 de la spec). Se consulta la vista y no la tabla
-- para poder cambiar la tabla sin romperles nada.
create view v_finalidad as
select codigo, descripcion, requiere_texto, ambito, activa
  from finalidad_consentimiento
 order by orden, codigo;

-- El texto vigente de cada finalidad. Un consumidor que va a pedir
-- consentimiento necesita mostrar **el texto exacto** que la persona acepta,
-- y necesita su versión para guardarla junto al otorgamiento.
create view v_texto_consentimiento_activo as
select distinct on (finalidad)
       finalidad, version, cuerpo, creado_en
  from texto_consentimiento
 where activo
 -- `id desc` como desempate y no como adorno: `creado_en` tiene `now()` por
 -- defecto, que es el instante de la **transacción**, así que dos versiones
 -- publicadas en la misma transacción empatan y `distinct on` devolvería
 -- cualquiera de las dos. El id es monótono, así que «la última publicada»
 -- queda bien definida siempre.
 order by finalidad, creado_en desc, id desc;
