-- R3.13 — Cargar panelistas sin asociarlos a un panel.
--
-- Hasta acá, la única forma de incorporar individuos con sus respuestas era
-- desde una encuesta, y toda encuesta pertenece a un panel. O sea que dar de
-- alta gente implicaba necesariamente meterla en un panel, con lo cual
-- aparecía en convocatorias, en la composición y en el muestreo. Eso bloquea
-- un caso real: bases que llegan de afuera —un ómnibus, un estudio de
-- terceros, una base histórica— cuya gente no es panelista, no fue reclutada
-- y no va a ser convocada, pero cuyas respuestas sí interesa poder consultar
-- por concepto.
--
-- Una `carga` cumple frente al store semántico el mismo papel que una
-- `encuesta` —agrupa el cuestionario, sus preguntas y sus respuestas bajo un
-- `ref_estudio`— y no tiene panel.
--
-- ---------- Por qué una tabla nueva y no `encuesta.panel_id` nullable ------
-- Una encuesta es, por definición, algo que se fieldea a un panel:
-- `convocar()`, la composición y el muestreo lo dan por sentado. Hacer el
-- panel opcional obligaría a revisar cada uno de esos caminos y dejaría
-- encuestas que no se pueden fieldear —un estado que no significa nada—. Una
-- entidad aparte mantiene esa semántica intacta y no toca ningún código
-- existente.
--
-- Aditiva: crea una tabla y no toca ninguna. Se aplica con la app andando.
create table carga (
  id          bigint generated always as identity primary key,
  nombre      text not null,
  descripcion text,
  -- La misma uuid identifica al cuestionario del lado semántico, igual que
  -- en `encuesta`. No hay FK entre stores: el cruce es por esta uuid.
  ref_estudio uuid not null default gen_random_uuid(),
  creado_en   timestamptz not null default now(),
  creado_por  text
);
create unique index carga_ref_estudio_unico on carga (ref_estudio);

comment on table carga is
  'R3.13 — un lote de individuos incorporados con sus respuestas, sin panel. '
  'No genera membresías ni participaciones: esa gente no es panelista y no '
  'entra en composición, brecha ni muestreo.';
