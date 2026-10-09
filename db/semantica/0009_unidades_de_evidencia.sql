-- ============================================================
--  STORE SEMÁNTICO — Migración 0009
--  R-CS · Unidades de evidencia y veredictos persistidos
--  (specs/ADDENDUM_solicitud_consultas_semanticas.md, cambios 3 y 5)
-- ============================================================
--
-- ── El problema ──
-- Una consulta exploratoria verifica las `top_k` personas mejor rankeadas. Si
-- 25 personas dieron **la misma respuesta** a la misma pregunta —«soy el
-- titular del contrato», copiada por el campo en todas las filas—, esas 25
-- ocupan los 25 lugares, Claude lee 25 veces el mismo texto y la respuesta
-- que importaba (la marca, más abajo) nunca llega a verificarse.
--
-- ── La unidad de evidencia ──
-- Una unidad es **un texto distinto dentro de una pregunta**:
-- `(pregunta_id, hash_texto)`. Se verifica una vez y el veredicto vale para
-- todas las respuestas que la comparten. No se guarda como tabla con su
-- propio contenido: es una vista sobre `respuesta`, así que una baja que
-- borra respuestas achica o hace desaparecer la unidad sin ningún paso
-- extra, y no hay dos copias del texto que sincronizar.
--
-- `hash_texto` existe desde la 0003, pero las filas anteriores quedaron en
-- nulo («no sé qué había»). Para agrupar hace falta en todas: esta migración
-- lo completa con el mismo algoritmo que `semantica.hash_texto()` (sha256
-- hex del texto exacto, UTF-8). Hay una prueba que compara los dos.
--
-- ── Los veredictos de una ejecución completa ──
-- Una consulta de alcance `completo` verifica todas las unidades elegibles
-- en lotes diferidos (bóveda/0025). Lo que Claude dijo de cada unidad se
-- guarda **acá** y no en la bóveda: la razón es una oración sobre el
-- contenido de una respuesta de encuesta, y el contenido vive en este store.
-- Sin `id_persona`: el veredicto es de la unidad; a quién alcanza se resuelve
-- al leer, sobre las respuestas que existen en ese momento.
--
-- Va en una transacción y se puede correr dos veces sin efecto.
begin;

-- 1 · `hash_texto` completo. Idempotente: solo toca los nulos.
update respuesta
   set hash_texto = encode(sha256(convert_to(texto_embebido, 'UTF8')), 'hex')
 where hash_texto is null;

-- 2 · El índice de la agrupación.
create index if not exists respuesta_unidad_idx
    on respuesta (pregunta_id, hash_texto);

-- 3 · El catálogo de unidades, derivado.
create or replace view v_unidad_evidencia as
select r.pregunta_id,
       r.hash_texto,
       count(*)                      as respuestas,
       count(distinct r.individuo_id) as individuos,
       min(r.id)                     as respuesta_representante
  from respuesta r
 group by r.pregunta_id, r.hash_texto;

comment on view v_unidad_evidencia is
  'R-CS · Una fila por texto distinto dentro de una pregunta. Es la unidad '
  'que se verifica: el veredicto vale para todas las respuestas que la '
  'comparten. Derivada de `respuesta`, así que una baja la achica sola.';

-- 4 · Los veredictos de las ejecuciones completas.
create table if not exists veredicto_unidad (
  id              bigint generated always as identity primary key,
  ejecucion_id    uuid        not null,   -- consulta_ejecucion.id (bóveda), sin FK entre stores
  criterio_orden  int         not null,
  pregunta_id     bigint      not null references pregunta(id) on delete cascade,
  hash_texto      text        not null,
  veredicto       text        not null
                    check (veredicto in ('cumple', 'no_cumple', 'dudoso',
                                         'irrelevante', 'sin_verificar')),
  razon           text,
  fallo           text,
  aviso_polaridad boolean     not null default false,
  relevancia      real,
  distancia       real,
  creado_en       timestamptz not null default now(),
  vence_en        timestamptz not null default now() + interval '30 days',
  unique (ejecucion_id, criterio_orden, pregunta_id, hash_texto)
);

comment on table veredicto_unidad is
  'R-CS · Lo que el verificador dijo de cada unidad en una consulta de '
  'alcance completo. Sin id_persona: a quién alcanza se resuelve al leer. '
  'Vence a los 30 días; una baja borra los de las unidades que se quedaron '
  'sin respuestas (semantica.borrar_persona).';
comment on column veredicto_unidad.ejecucion_id is
  'El id de consulta_ejecucion en la bóveda. Referencia lógica, sin FK: '
  'son dos instancias distintas.';

create index if not exists veredicto_unidad_ejecucion_idx
    on veredicto_unidad (ejecucion_id, criterio_orden);
create index if not exists veredicto_unidad_vence_idx
    on veredicto_unidad (vence_en);

commit;
