-- La ingesta deja de correr dentro de una request.
--
-- ============================================================
--  El problema que resuelve
-- ============================================================
-- La ingesta corría entera adentro de un `POST`. Con unos cientos de
-- respuestas anda; con una base real no llega: 200.000 respuestas son ~1.560
-- llamadas al proveedor de embeddings en serie, media hora larga solo de eso,
-- y Cloud Run corta la request a los 300 segundos. La carga falla con un
-- error genérico **después de haber procesado una parte**, y no hay forma de
-- retomar.
--
-- Subir el timeout corre la pared de lugar: el máximo de una función de 2ª
-- gen son 60 minutos. Lo que hace falta es que el trabajo deje de vivir en
-- una request.
--
-- Desde acá, confirmar una carga **persiste el trabajo** y encola una tarea
-- por lote. Estas dos tablas son ese trabajo persistido.
--
-- ============================================================
--  Por qué el plan se congela al confirmar
-- ============================================================
-- `ingesta_trabajo.plan` guarda lo que la pantalla resolvió: qué preguntas,
-- qué variables son demográficas, qué columna identifica a la persona, de qué
-- plataforma viene. Las tareas **no vuelven a interpretarlo**.
--
-- Si cada tarea re-resolviera el mapeo, dos tareas de la misma carga podrían
-- usar mapeos distintos —alguien marca una variable como demográfica mientras
-- la carga avanza— y el resultado sería un estudio donde la mitad de las
-- respuestas tiene una pregunta que la otra mitad no. Congelarlo hace que las
-- tareas sean mecánicas.
--
-- Lo que **sí** se re-evalúa en cada lote, y tiene que ser así, es el gate de
-- consentimiento: si alguien retira `uso_semantico` mientras la carga avanza,
-- los lotes que faltan no lo ingestan. Eso no está acá: lo hace la ingesta de
-- siempre, que corre igual dentro de cada tarea.

-- ============================================================
--  1 · Los catálogos de estado
-- ============================================================
-- Catálogos y no `check`, por lo mismo que `accion_usuario` en la 0015 y
-- `motivo_acceso_portal` en la 0018: una migración que solo cambia una
-- restricción es invisible para `verificar_esquema.py`, y un catálogo tiene
-- dónde guardar la etiqueta que si no se duplica en el frontend.
create table estado_ingesta (
  codigo      text primary key,
  etiqueta    text not null,
  descripcion text not null,
  -- Si desde este estado ya no se espera que el trabajo avance solo.
  terminal    boolean not null default false,
  orden       int not null default 100
);

comment on table estado_ingesta is
  'Los estados de un trabajo de ingesta diferida. La pantalla lee la '
  'etiqueta de acá en vez de repetir el diccionario.';

insert into estado_ingesta (codigo, etiqueta, descripcion, terminal, orden) values
  ('encolada', 'Encolada',
   'El trabajo quedó guardado y sus lotes encolados. Todavía no empezó a '
   'procesarse ninguno.', false, 10),
  ('procesando', 'Procesando',
   'Al menos un lote terminó o está en curso, y quedan lotes por hacer.',
   false, 20),
  ('terminada', 'Terminada',
   'Todos los lotes entraron bien y el resumen está consolidado.', true, 30),
  ('terminada_con_errores', 'Terminada con errores',
   'Todos los lotes se intentaron y al menos uno quedó fallido. Lo que entró, '
   'entró: los lotes buenos no se deshacen.', true, 40),
  ('fallida', 'Fallida',
   'No entró ningún lote. Se distingue de la anterior porque no hay nada que '
   'conservar y conviene rehacer la carga entera.', true, 50);

create table estado_lote_ingesta (
  codigo      text primary key,
  etiqueta    text not null,
  descripcion text not null,
  orden       int not null default 100
);

insert into estado_lote_ingesta (codigo, etiqueta, descripcion, orden) values
  ('pendiente',  'Pendiente',  'Encolado, todavía sin tomar.', 10),
  ('procesando', 'Procesando', 'Una tarea lo tomó y está trabajando.', 20),
  ('ok',         'Terminado',  'Entró completo.', 30),
  ('fallido',    'Fallido',
   'Agotó los reintentos o falló por un error que reintentar no arregla.', 40);

-- ============================================================
--  2 · El trabajo
-- ============================================================
create table ingesta_trabajo (
  id            bigint generated always as identity primary key,
  -- A dónde va: una encuesta fieldeada (R1.5/R3.9) o una carga sin panel
  -- (R3.13). Son los dos destinos que hoy tiene la ingesta, y el procesador
  -- despacha a la función de siempre según esto. Sin FK porque son dos
  -- tablas distintas; el destino se valida al encolar.
  destino_tipo  text not null,
  destino_id    bigint not null,
  -- El mapeo resuelto, congelado. Ver el encabezado.
  plan          jsonb not null,
  estado        text not null default 'encolada' references estado_ingesta(codigo),
  lotes_total   int not null,
  filas_total   int not null,
  -- El resumen consolidado, que es lo que la ingesta sincrónica devolvía de
  -- una sola vez. Se arma al terminar el último lote.
  resumen       jsonb,
  creado_en     timestamptz not null default now(),
  creado_por    text,
  terminado_en  timestamptz
);

alter table ingesta_trabajo
  add constraint ingesta_trabajo_destino_check
      check (destino_tipo in ('encuesta', 'carga'));

create index ingesta_trabajo_por_destino
    on ingesta_trabajo (destino_tipo, destino_id, creado_en desc);
create index ingesta_trabajo_abiertos on ingesta_trabajo (creado_en desc)
 where estado in ('encolada', 'procesando');

comment on table ingesta_trabajo is
  'Una carga de respuestas que se procesa en diferido. Guarda el mapeo ya '
  'resuelto para que las tareas no vuelvan a interpretarlo, y el resumen '
  'consolidado cuando termina.';

-- ============================================================
--  3 · Los lotes
-- ============================================================
-- Las filas del archivo viven acá, repartidas: cada lote lleva su rebanada y
-- ninguna tarea necesita el archivo original. Es espacio temporal en la base
-- —200.000 respuestas ocupan lo suyo— y por eso `purgar_ingestas_terminadas()`
-- lo libera cuando ya no hace falta.
create table ingesta_lote (
  id            bigint generated always as identity primary key,
  trabajo_id    bigint not null references ingesta_trabajo(id) on delete cascade,
  indice        int not null,
  -- Null después de la purga: el lote queda como registro de lo que pasó,
  -- sin el peso de los datos.
  filas         jsonb,
  filas_total   int not null,
  estado        text not null default 'pendiente'
                  references estado_lote_ingesta(codigo),
  intentos      int not null default 0,
  resultado     jsonb,
  error         text,
  -- Si el error vale la pena reintentarlo. Un timeout del proveedor sí; una
  -- fila mal formada no, y reintentarla cinco veces gasta tiempo y
  -- embeddings en algo que va a fallar igual.
  reintentable  boolean,
  actualizado_en timestamptz not null default now(),
  unique (trabajo_id, indice)
);

create index ingesta_lote_pendientes on ingesta_lote (trabajo_id, estado);

comment on table ingesta_lote is
  'Un pedazo de una carga: su rebanada de filas, en qué estado está y qué '
  'dejó. Reprocesarlo no duplica nada —la ingesta es idempotente por '
  '(individuo, pregunta) y por (panel, persona)—, así que un reintento es '
  'seguro.';

comment on column ingesta_lote.reintentable is
  'R-ASYNC.4 — si el error es transitorio. Lo decide quien procesa, no quien '
  'reintenta: el que sabe si fue un 429 del proveedor o un dato inválido es '
  'el que lo vio fallar.';

-- ============================================================
--  4 · El progreso, derivado y no acumulado
-- ============================================================
-- La spec pedía `lotes_terminados` y `filas_procesadas` como columnas del
-- trabajo. Van acá como vista, por la misma razón por la que el saldo de
-- puntos sale siempre del ledger: un contador que se incrementa a mano se
-- desincroniza, y encima acá lo incrementarían varias tareas en paralelo.
-- Derivarlo de `ingesta_lote` no puede mentir.
--
-- Y de paso, es el objeto que hace **detectable** a esta migración.
create view v_ingesta_progreso as
select
  t.id                     as trabajo_id,
  t.destino_tipo, t.destino_id,
  t.estado,
  e.etiqueta               as estado_etiqueta,
  e.terminal,
  t.lotes_total,
  t.filas_total,
  count(l.id) filter (where l.estado = 'ok')         as lotes_ok,
  count(l.id) filter (where l.estado = 'fallido')    as lotes_fallidos,
  count(l.id) filter (where l.estado = 'procesando') as lotes_en_curso,
  count(l.id) filter (where l.estado = 'pendiente')  as lotes_pendientes,
  coalesce(sum(l.filas_total) filter (where l.estado = 'ok'), 0)::bigint
                                                     as filas_procesadas,
  t.creado_en, t.terminado_en,
  -- Cuánto llevó, para poder estimar lo que falta sin que el cliente tenga
  -- que adivinar.
  extract(epoch from (coalesce(t.terminado_en, now()) - t.creado_en))::int
                                                     as segundos_transcurridos
  from ingesta_trabajo t
  join estado_ingesta e on e.codigo = t.estado
  left join ingesta_lote l on l.trabajo_id = t.id
 group by t.id, e.etiqueta, e.terminal;

comment on view v_ingesta_progreso is
  'El avance de cada trabajo de ingesta, contado sobre los lotes y no sobre '
  'un acumulador: con varias tareas escribiendo en paralelo, un contador a '
  'mano se desincroniza y nadie se entera.';

-- ============================================================
--  5 · Liberar el espacio temporal
-- ============================================================
-- Las filas de una carga terminada no sirven para nada: lo ingestado está en
-- los dos stores y el resumen quedó en el trabajo. Lo que sí se conserva es
-- el registro —cuántos lotes, cuáles fallaron, con qué error—, que es lo que
-- alguien va a querer mirar la semana que viene.
--
-- Idempotente y sin parámetros obligatorios, para poder engancharla a una
-- rutina sin pensarlo dos veces.
create function purgar_ingestas_terminadas(dias int default 7)
returns int language plpgsql as $$
declare
  liberados int;
begin
  update ingesta_lote l
     set filas = null
    from ingesta_trabajo t
   where t.id = l.trabajo_id
     and l.filas is not null
     and t.terminado_en is not null
     and t.terminado_en < now() - make_interval(days => dias)
     -- Un lote fallido conserva sus filas aunque el trabajo haya terminado:
     -- son las que harían falta para reintentarlo a mano.
     and l.estado <> 'fallido';
  get diagnostics liberados = row_count;
  return liberados;
end;
$$;

comment on function purgar_ingestas_terminadas(int) is
  'Libera las filas guardadas de las cargas que ya terminaron hace más de N '
  'días. Conserva las de los lotes fallidos, que son las que harían falta '
  'para reintentarlos.';

-- ============================================================
--  6 · Cerrar la puerta que Postgres deja abierta
-- ============================================================
-- Postgres le da `execute` a `public` sobre toda función nueva, así que sin
-- esto **COLOQUIO podría purgar las filas de nuestras cargas**. No es
-- hipotético: `scripts/verificar_coloquio.py` lleva una lista blanca de
-- privilegios efectivos y rompió por esta función antes de que existiera
-- esta línea. Es la misma `revoke` que llevan las funciones de la 0014 y la
-- 0016, y por la misma razón.
--
-- No hay `grant` a nadie después: la purga la corre el dueño del esquema
-- desde una rutina de mantenimiento, no la aplicación ni un consumidor.
revoke execute on function purgar_ingestas_terminadas(int) from public;

-- Y las tablas nuevas tampoco son de nadie más. La 0014 ya hizo
-- `revoke all on schema public from coloquio_app`, así que un consumidor
-- externo no las ve; esto es explícito para que se lea en la migración que
-- las crea y no haya que ir a buscarlo tres archivos atrás.
revoke all on ingesta_trabajo, ingesta_lote from public;
