# SPEC — R3.13: Cargar panelistas sin asociarlos a un panel

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Spec de referencia:** `specs/SPEC_fase3.md` · R3.9 (ingesta desde archivo SAV) y sus addenda
**Requisito nuevo:** R3.13
**Estado:** Para desarrollo
**Última actualización:** 2026-09-16

---

## 1. Problem Statement

Hoy la única forma de cargar individuos con sus respuestas es **desde una encuesta**, y toda encuesta pertenece a un panel. Como consecuencia, dar de alta un conjunto de personas implica necesariamente incorporarlas a un panel: no hay manera de sumar individuos al sistema, con sus respuestas y sus demográficos, dejándolos fuera de la operación de paneles.

Eso bloquea un caso real: bases que llegan de afuera del panel —un ómnibus, un estudio hecho por terceros, una base histórica— cuyas personas **no son panelistas**. No fueron reclutadas, no van a ser convocadas, y meterlas en un panel las haría aparecer en convocatorias, en la composición y en el muestreo, distorsionando todos los indicadores de ese panel. Pero sí interesa que sus respuestas sean consultables por concepto y que sus demográficos permitan filtrarlas.

Este requisito separa las dos cosas que hoy vienen pegadas: **incorporar individuos y sus respuestas** por un lado, y **hacerlos miembros de un panel** por otro.

## 2. Goals

- Cargar individuos con sus respuestas y demográficos **sin asociarlos a ningún panel**.
- **Reutilizar el flujo de ingesta ya implementado**: mismo mapeo de variables a preguntas, mismo marcado de demográficos, mismo dedup de identidad, mismo guardrail de PII.
- Que esos individuos sean **consultables** (semántica y demográficamente) desde el primer momento.
- Que puedan **incorporarse a un panel más adelante**, sin recargar nada.

## 3. Non-Goals

- **No reemplaza la ingesta desde encuesta.** Esa sigue existiendo tal cual, con su alta automática de membresía y participación (addendum R3.9.a/b).
- **No crea membresías ni participaciones.** Es precisamente lo que este flujo evita.
- **No convoca ni habilita convocar.** Sin membresía no hay convocatoria posible.
- **No cambia el dedup de identidad (R1.2)** ni el pipeline de consulta.
- **No altera los indicadores de panel:** al no pertenecer a ninguno, estos individuos no entran en composición, brecha ni muestreo.
- No es una carga masiva de personas sin respuestas: si no hay preguntas que mapear, el caso es el alta de panelista existente.

## 4. User Stories

- Como analista, quiero cargar una base externa con sus respuestas para poder consultarla por concepto, sin que esa gente entre a ningún panel.
- Como analista, quiero usar la misma pantalla de mapeo que ya conozco (variables, preguntas, demográficos), sin aprender un flujo nuevo.
- Como responsable de panel, quiero que los indicadores de mis paneles no se vean afectados por gente que no es panelista.
- Como responsable de panel, quiero poder incorporar después a un panel a los individuos cargados así, si decidimos sumarlos.
- Como DPO, quiero que estas altas registren consentimiento con la misma exigencia que el resto.

**Casos borde**
- Persona del archivo que **ya es panelista** y miembro de paneles: sus respuestas se suman, sus membresías no se tocan.
- Persona del archivo que ya fue cargada en una carga anterior.
- Archivo sin evidencia de consentimiento.
- Re-carga del mismo archivo.
- Carga sin ninguna variable de pregunta (solo demográficos).

## 5. Requirements

### R3.13.a — Acceso desde la pantalla de panelistas (P0)

- Dada la pantalla de panelistas, entonces hay un botón **«Cargar panelistas»** que abre el flujo de carga.
- Dado el flujo abierto, entonces la pantalla es la misma que la de ingestar respuestas desde una encuesta: archivo, columna que identifica al respondente, mapeo de variables a preguntas, marcado de demográficos y modo de resolución de individuos.
- Dada la pantalla, entonces se indica de forma visible que **los individuos cargados no quedarán asociados a ningún panel**.
- [ ] Se pide un **nombre para la carga** (ej. «Ómnibus agosto 2026»), que identifica el origen de esos datos.
- [ ] Rige el mismo permiso que la ingesta desde encuesta.

### R3.13.b — Ingesta sin panel (P0)

- Dada una carga, entonces se crea un registro de carga con su propio `ref_estudio`, que cumple frente al store semántico el mismo rol que una encuesta: agrupa el cuestionario, sus preguntas y sus respuestas.
- Dada la ingesta, entonces **no se crea membresía** en ningún panel.
- Dada la ingesta, entonces **no se crea participación**: no hubo convocatoria ni encuesta fieldeada.
- Dado el resto del flujo, entonces se comporta exactamente como la ingesta desde encuesta: despivote, resolución de códigos a etiquetas, composición del texto a embeber, embeddings en lote, upsert idempotente.
- Dado el marcado de demográficos (addendum R3.9.d), entonces aplica igual: las variables marcadas se escriben en la bóveda y **no** se ingestan como preguntas.
- Dado el guardrail de PII (R1.6), entonces aplica igual: ningún dato patronímico llega al store semántico.
- Dada una re-carga del mismo archivo sobre la misma carga, entonces no se duplican preguntas ni respuestas.

### R3.13.c — Resolución de individuos (P0)

- Dado el modo «crear los individuos», entonces cada alta pasa por la **misma resolución de identidad que R1.2** (documento → email → nombre+fecha de nacimiento → nuevo), y los casos ambiguos van a la cola de revisión.
- Dada una persona que **ya existe** en la bóveda, entonces se reutiliza su `id_persona`, se suman sus respuestas y sus demográficos se completan según las reglas del addendum R3.9.d (completar vacíos, informar discrepancias, no sobrescribir).
- Dada una persona que ya existe y **es miembro de paneles**, entonces sus membresías **no se modifican** en ninguna dirección: ni se agregan ni se quitan.
- Dado el tipo de identificador declarado (R3.12.b), entonces se resuelve según ese tipo; para bases externas lo habitual será `documento` o `email`.

### R3.13.d — Consentimiento (P0)

- Dada una carga en modo «crear los individuos», entonces se exige declarar la evidencia de consentimiento del archivo, con el mismo mecanismo de R3.9: variable, valor afirmativo y versión de texto, por finalidad.
- Dado este flujo, entonces la finalidad **obligatoria es `uso_semantico`**: es la base de lo que se va a hacer con esos datos. Una fila sin esa evidencia **no crea la persona** y se informa.
- Dada la finalidad `contacto_participacion`, entonces es **opcional** en este flujo: quien no la evidencie se crea igual, pero no podrá ser convocado nunca (el gate de R1.3 sigue aplicando intacto).
- Dado el resultado, entonces informa cuántas filas quedaron afuera por falta de evidencia de `uso_semantico`.

> **Por qué se invierte la obligatoriedad respecto de R3.9.** En la ingesta desde encuesta, la finalidad obligatoria es `contacto_participacion`, porque esa persona es un panelista al que se va a seguir convocando. Acá el propósito es el inverso: son personas que **no** se van a contactar y cuyos datos se incorporan para análisis. Exigir consentimiento de contacto sería pedir una base legal para algo que no se va a hacer, y no exigir el de uso semántico dejaría sin base lo único que sí se va a hacer.

### R3.13.e — Visibilidad y camino a panel (P0)

- Dado un individuo cargado por este flujo, entonces aparece en la pantalla de panelistas y es distinguible de quienes pertenecen a algún panel.
- Dado un individuo sin panel, entonces es consultable con normalidad: entra en consultas semánticas (si tiene `uso_semantico` vigente) y en filtros demográficos.
- Dada la pantalla de panelistas, entonces se puede filtrar por «sin panel».
- Dado un resultado de consulta que incluye individuos sin panel, entonces pueden incorporarse a un panel mediante **R3.11** (crear panel desde el resultado de una consulta), sin recargar los datos.

> **El camino previsto.** Cargar → consultar → crear panel con los que interesan (R3.11). Así se incorpora a un panel solo a quien corresponde, en vez de meter la base entera y depurar después.

### R3.13.f — Informe de resultados (P0)

El resultado de la carga informa: individuos creados, reutilizados, en revisión, filas sin evidencia de consentimiento, respuestas escritas, variables excluidas por demográficas, campos de bóveda completados y discrepancias detectadas. **No** informa membresías ni participaciones, porque no se crean.

## 6. Cambios de esquema

- Nueva tabla de **carga** en la bóveda: `id`, `nombre`, `descripcion`, `ref_estudio uuid not null default gen_random_uuid()`, `creado_en`, `creado_por`. Cumple frente al store semántico el mismo papel que `encuesta`, sin `panel_id`.
- Sin cambios en el store semántico: `cuestionario.ref_estudio` ya existe y no distingue si el estudio vino de una encuesta o de una carga.
- Sin cambios en `membresia` ni `participacion`.
- Migración aditiva, aplicable con la app andando.

> **Por qué una tabla nueva y no `encuesta.panel_id` nullable.** Una encuesta es, por definición, algo que se fieldea a un panel: `convocar()`, la composición y el muestreo lo dan por sentado. Hacer el panel opcional obligaría a revisar cada uno de esos caminos y dejaría encuestas que no se pueden fieldear. Una entidad aparte mantiene esa semántica intacta y no toca ningún código existente.

## 7. Contratos de API (propuestos)

- `POST /cargas` — crea una carga (nombre, descripción) y devuelve su `ref_estudio`.
- `GET /cargas` — lista las cargas realizadas.
- `POST /cargas/{id}/analizar` — analiza el archivo y devuelve la metadata precargada (mismo contrato que el análisis de SAV de R3.9).
- `POST /cargas/{id}/ingesta` — ejecuta la carga.
- `GET /panelistas?sin_panel=true` — filtro de la pantalla de panelistas.

Las rutas de ingesta desde encuesta quedan **sin cambios**.

## 8. Definition of Done

- [ ] El botón «Cargar panelistas» está en la pantalla de panelistas y abre el flujo conocido.
- [ ] Una carga crea individuos y escribe sus respuestas en el store semántico, con su propio `ref_estudio`.
- [ ] Ningún individuo cargado por este flujo queda como miembro de un panel (test).
- [ ] No se crea ninguna participación (test).
- [ ] Una persona que ya era panelista conserva sus membresías intactas tras la carga (test).
- [ ] Una fila sin evidencia de `uso_semantico` no crea la persona y se informa (test).
- [ ] Una fila con `uso_semantico` pero sin `contacto_participacion` crea la persona, y esa persona no puede ser convocada (test).
- [ ] El marcado de demográficos excluye esas variables del store semántico (test).
- [ ] Ningún dato patronímico llega al store semántico (test del guardrail).
- [ ] Re-cargar el mismo archivo no duplica preguntas ni respuestas (test).
- [ ] Los individuos sin panel se pueden filtrar en la pantalla de panelistas y aparecen en consultas.
- [ ] Un panel creado por R3.11 desde un resultado con individuos sin panel les da membresía correctamente.
- [ ] La ingesta desde encuesta sigue creando membresía y participación como antes (test de no regresión).

## 9. Riesgos y preguntas abiertas

- **[legal]** ¿Alcanza el consentimiento de uso semántico para conservar datos patronímicos (nombre, documento) de alguien que no es panelista y no será contactado? Si la respuesta es que no, habría que cargar estos individuos con demográficos pero sin patronímicos, o no cargarlos. *Definir antes de usar el flujo con bases reales.*
- **[producto]** Personas sin panel acumuladas: conviene decidir si se revisan periódicamente para incorporarlas o depurarlas, o quedan indefinidamente. Sin una política, la bóveda crece con gente que nadie mira.
- **[producto]** ¿Deberían las consultas poder acotarse a «solo panelistas» o «solo sin panel»? Hoy el filtro por panel ya lo permite indirectamente, pero conviene confirmarlo con los analistas.
- **[datos]** Las bases externas suelen traer preguntas equivalentes con otra redacción. No es problema del sistema —la búsqueda es semántica, para eso se diseñó así— pero conviene advertir a los analistas que el texto de la pregunta que carguen es lo que va a determinar cómo se encuentra después.
