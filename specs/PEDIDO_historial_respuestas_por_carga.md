# PEDIDO — Historial de respuestas por carga, y los pasos de alta que no aparecen

**Sistema:** Gestión de paneles · cargas, borrado de cargas y store semántico
**Severidad:** **Crítica** la parte 1 —hoy, borrar la última carga de una encuesta
incremental **borra las respuestas de todo el estudio**—; media la parte 2
**Detectado:** 2026-11-10
**Entrega:** una sola versión

---

## Parte 1 · Borrar una carga no puede borrar respuestas que otras cargas contienen

### Qué pasó (caso real, verificado)

En la encuesta «Encuesta Prueba - 10-11-26»:

1. Las cargas **39, 41 y 42** —incrementales, mismo cuestionario— escribieron las
   respuestas de sus personas (2, 3 y 4 respuestas, incluida la persona
   `33cdda6e-…`).
2. Se cargó la **45**, con las mismas personas. Como todas las cargas de una
   encuesta comparten el cuestionario y hay una sola respuesta por persona y
   pregunta, la 45 pasó a ser **la última que escribió** las 4 respuestas
   (`respuesta.carga_id = 45`).
3. Se borró la carga 45 (`carga_borrado` id 5: `respuestas_borradas: 4`).
4. Resultado: el cuestionario de la encuesta quedó con **4 preguntas y 0
   respuestas**, aunque **las cargas 39, 41 y 42 siguen existiendo** y contenían
   esas respuestas. La bóveda dice que esas personas respondieron; el store
   semántico no tiene nada.

### La causa: una regla mal definida en el spec

R-EU.10 dice: *«al borrarla se eliminan las respuestas que esa carga escribió
por última vez»*. Es incorrecta para encuestas incrementales: la última carga
escribe **todo**, porque el archivo es acumulado. Borrarla borra el estudio
entero.

La regla correcta: **si la respuesta también está en una carga anterior que sigue
existiendo, se vuelve a ese valor; se borra solo lo que ninguna otra carga
contiene.** Para eso el sistema tiene que saber qué traía cada carga, y hoy solo
guarda la última.

### R-HIS.1 — Registro de cada respuesta por carga (P0)

- Dada la ingesta de una carga, entonces además de actualizar la respuesta vigente
  se guarda, **en el store semántico**, una fila por **persona, pregunta y carga**:
  `respuesta_carga (individuo_id, pregunta_id, carga_id, valor_texto, hash_texto,
  unidad_id, escrito_en)`, única por `(individuo_id, pregunta_id, carga_id)`.
- [ ] Va en el store **semántico** y no en la bóveda: es contenido de respuestas.
      La separación entre identidad y contenido se mantiene.
- [ ] No genera vectores nuevos: apunta a la unidad de evidencia que ya existe.

### R-HIS.2 — Borrar una carga revierte en lugar de borrar (P0)

- Dada una respuesta cuya última carga es la que se borra, entonces:
  - si existe su registro en **otra carga que sigue existiendo** de la misma
    encuesta, la respuesta vigente **vuelve al valor de la más reciente** de
    ellas, y su `carga_id` pasa a esa carga;
  - si no existe en ninguna otra carga, **recién ahí se borra**.
- Dada la carga borrada, entonces se eliminan sus filas de `respuesta_carga`.
- [ ] Una persona que sigue en otras cargas de la encuesta conserva su
      participación como respondió.
- [ ] Las unidades de evidencia que quedan sin ninguna respuesta se borran, como
      hasta ahora.

### R-HIS.3 — La vista previa del borrado dice qué se pierde (P0)

- Dada la vista previa de borrar una carga, entonces informa por separado:

> **4 respuestas vuelven** al valor de una carga anterior · **0 se eliminan**
> porque solo estaban en esta carga

- [ ] Si una carga es la única que contiene ciertas respuestas, la vista previa lo
      dice explícitamente antes de confirmar.

### R-HIS.4 — Borrar una encuesta (P0)

- [ ] Borrar una encuesta elimina también sus filas de `respuesta_carga`.

### R-HIS.5 — Ver en qué cargas apareció cada respuesta (P1)

- Dada la sección «Respuestas procesadas» de la ficha, entonces cada respuesta
  puede mostrar **en qué cargas apareció**, y si **cambió** entre una y otra
  —por ejemplo, entre el parcial y el final—.

### Migración

- [ ] Crear `respuesta_carga` a partir de las respuestas vigentes, con su
      `carga_id` actual. **Las cargas anteriores a la última no tienen registro**
      —ese dato nunca se guardó—: el historial empieza completo solo para las
      cargas nuevas. Mientras tanto, borrar una carga vieja sigue usando la regla
      nueva con lo que haya.
- [ ] Corrida de prueba que informe cuántas filas se crean, antes de escribir.

### Recuperación del caso real

No requiere desarrollo: volver a subir como carga nueva de «Encuesta Prueba -
10-11-26» el archivo de la **carga 42** —el último acumulado—. Las personas quedan
como «repetida» y sus respuestas vuelven al store semántico.

---

## Parte 2 · Los pasos 4 y 5 aparecen en un formulario oculto

### Qué pasa (verificado en `encuestas.js`)

Los bloques **«4 · Personas nuevas: base legal del alta»** y **«5 · Canales
aceptados por las personas nuevas»** arrancan ocultos y se activan cuando la
revisión detecta personas nuevas (`revisarAntesDeImportar`, ~línea 2430:
`$('#bloque-alta').classList.remove('hidden')`).

Pero en ese momento lo visible es el **panel de revisión** (`#ing-revision`); el
formulario (`#ing-formulario`), donde viven esos bloques, está **oculto debajo**.
Los pasos se activan en un formulario que no se ve, y aparecen recién al volver
atrás.

### R-ALT.1 — Llevar al usuario a los pasos que faltan (P0)

- Dada una revisión que detecta personas nuevas **sin** base legal declarada,
  entonces el sistema **vuelve automáticamente al formulario**, con los pasos 4 y
  5 visibles, se desplaza hasta el paso 4 y muestra arriba: *«La revisión encontró
  N personas nuevas. Completá su base legal para poder confirmar.»*
- Dado el paso 4 completo, entonces «Siguiente» vuelve a la revisión, ya sin el
  faltante.
- [ ] Alternativa aceptable: mostrar los pasos 4 y 5 **dentro** del panel de
      revisión. Lo que no puede pasar es que el usuario tenga que adivinar que debe
      volver atrás.

---

## Definition of Done

**Parte 1**
- [ ] Tres cargas incrementales y una cuarta con las mismas personas: borrar la
      cuarta deja las respuestas con el valor de la tercera (test).
- [ ] Una respuesta presente solo en la carga borrada se elimina (test).
- [ ] La vista previa separa respuestas que vuelven de respuestas que se eliminan
      (test).
- [ ] Borrar una encuesta elimina su historial (test).
- [ ] El caso real (carga 45) reproducido en pruebas ya no deja el cuestionario
      vacío.

**Parte 2**
- [ ] Una revisión con personas nuevas sin base legal muestra los pasos 4 y 5 sin
      que el usuario tenga que volver atrás (test).

## Costos

| Concepto | Costo |
|---|---|
| `respuesta_carga` | **Solo espacio**: una fila liviana por respuesta y carga, sin vectores. Con ~25.000 respuestas y dos o tres cargas por estudio, del orden de **pocos MB**. |
| Revertir al borrar | **US$ 0** en embeddings: las unidades ya existen |
| Interfaz | **US$ 0** |

## Despliegue

Sigue las reglas del `CLAUDE.md` del repositorio: proyecto de gcloud fijado y
verificado al empezar; respaldo de las **dos** bases; migración con corrida de
prueba, aplicada y `verificar_esquema.py` en verde **antes** del
`firebase deploy`; token de COLOQUIO regenerado inmediatamente antes de la
batería.

> **Hasta que esto esté desplegado: no borres cargas de encuestas incrementales.**
> Si hace falta deshacer una, conviene volver a subir el último archivo correcto en
> lugar de borrar.
