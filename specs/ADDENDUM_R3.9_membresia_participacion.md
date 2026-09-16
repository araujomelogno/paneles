# Addendum a R3.9 — Membresía y participación al ingestar

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Spec de referencia:** `specs/SPEC_fase3.md` · R3.9 (ingesta desde archivo SAV)
**Estado:** Para desarrollo
**Última actualización:** 2026-09-15

---

## 1. Qué problema cierra

Hoy la ingesta escribe respuestas y, en modo «crear los individuos», da de alta personas en la bóveda. Pero **no las incorpora a ningún panel ni registra que participaron**. Eso deja dos agujeros:

- **Personas invisibles para la operación.** Una persona sin membresía no entra en `todo_el_panel` al convocar, no cuenta para la composición ni para la brecha de cuota, y no aparece en el muestreo (R3.1). Existe en la bóveda, pero es invisible para todo lo que se hace con un panel.
- **Tasa de respuesta mal calculada.** Alguien respondió la encuesta —está en el archivo— pero si no fue convocado desde el sistema no tiene fila en `participacion`. La ola muestra menos respuestas de las que realmente hubo.

La causa es que convocatoria e ingesta están desacopladas: convocar crea participaciones, ingestar escribe respuestas mapeando por `alias_origen`, y nada las une. Este addendum las une **en el momento de la ingesta**.

## 2. Decisión

**Opción (a): alta automática en el panel de la encuesta**, sin selector. Cada encuesta pertenece a exactamente un panel (`encuesta.panel_id` es FK obligatorio y único), así que no hay ambigüedad sobre cuál es. Si alguien respondió esa encuesta, pertenece a ese panel.

Y **la ingesta registra la participación** de todos los individuos cuyas respuestas se ingestan, creándola si no existe.

Aplica a **los dos modos** de R3.9 (`ya existen` y `crear los individuos`): un panelista preexistente que respondió una encuesta de un panel del que no era miembro tiene exactamente el mismo problema.

## 3. Requisitos

### R3.9.a — Alta automática de membresía

- Dada una ingesta que resuelve un individuo (creado o preexistente) con respuestas a ingestar, entonces se da de alta como miembro del panel de esa encuesta.
- Dado un individuo que ya es miembro activo de ese panel, entonces no se duplica la membresía (unicidad `(panel_id, id_persona)`; `on conflict do nothing`).
- Dado un individuo con membresía en estado `baja` en ese panel, entonces **no se reactiva automáticamente**: se informa en el resultado para que un responsable decida. Una baja fue una decisión explícita y la ingesta no la revierte de costado.
- Dado el resultado de la ingesta, entonces informa cuántas membresías nuevas se crearon y cuántas ya existían.

### R3.9.b — Registro de participación

- Dado un individuo cuyas respuestas se ingestan, cuando no tiene fila en `participacion` para esa encuesta, entonces se crea una con `respondio = true` y `respondio_en` fechada.
- Dado un individuo que **sí** fue convocado desde el sistema y ya tiene fila, entonces se actualiza a `respondio = true` (no se crea otra: unicidad `(encuesta_id, id_persona)`).
- Dada una participación creada por ingesta, entonces queda distinguible de una convocatoria emitida por el sistema, con un campo de origen (`convocatoria` | `importacion`).
- Dada una participación creada por ingesta, entonces `calidad_estado` queda en su default `pendiente`: la evaluación es de R3.2, no de este addendum.
- Dado el resultado de la ingesta, entonces informa cuántas participaciones se crearon y cuántas se actualizaron.

### R3.9.c — Idempotencia

- Dada una re-ingesta del mismo archivo sobre la misma encuesta, entonces no se duplican membresías ni participaciones, y los contadores del resultado reflejan que ya existían.

## 4. El gate de consentimiento en este caso

Acá hay una distinción que el código tiene que respetar y que **no es la misma que en `convocar()`**:

`convocar()` aplica el gate de `contacto_participacion` porque emite una **invitación futura**: no se puede contactar a quien no consintió ser contactado. La participación que crea la ingesta es otra cosa: **registra un hecho ya ocurrido** —la persona respondió, en campo, a través de la plataforma de terreno—. Bloquear ese registro por falta de consentimiento no protege a nadie y sí distorsiona la tasa de respuesta de la ola.

Por lo tanto:

- La **participación por importación** se registra para todo individuo cuyas respuestas se ingestan, sin exigir `contacto_participacion`. Es un registro histórico, no un contacto.
- La **membresía** se da de alta igual, por la misma razón: refleja que la persona pertenece a ese pool.
- El gate **sigue aplicando intacto donde corresponde**: `convocar()` no cambia. Un miembro sin `contacto_participacion` vigente nunca entra en una convocatoria futura, aunque tenga membresía y participaciones registradas.
- El gate de `uso_semantico` tampoco cambia: quien no lo tenga vigente sigue sin que sus respuestas lleguen al store semántico (hoy caen en `sin_consentimiento`). Este addendum **no** le da membresía ni participación a quien quedó fuera por ese filtro, porque no se le ingestó nada.

> En modo «crear los individuos», el spec ya exige evidencia de consentimiento de contacto en el archivo para crear a la persona, así que en ese modo el caso no se presenta. La distinción importa sobre todo en modo «ya existen», con panelistas antiguos cuyo consentimiento pudo haber sido retirado.

## 5. Cambios de esquema

- `participacion`: agregar columna de origen, `text not null default 'convocatoria'`, con check `in ('convocatoria','importacion')`. El default deja intactas las filas existentes.
- Sin otros cambios. `membresia` y `participacion` ya tienen la unicidad necesaria.
- Migración aditiva (nueva, en `db/boveda/`), aplicable con la app andando.

## 6. Qué informa el resultado de la ingesta

Al bloque de resultado actual (`respuestas_escritas`, `sin_mapear`, `sin_consentimiento`, y en modo crear: creados / reutilizados / en revisión) se suman:

- `membresias_nuevas` — altas en el panel de la encuesta.
- `membresias_existentes` — ya eran miembros.
- `membresias_en_baja` — tenían membresía dada de baja; **no se reactivaron**, requieren decisión.
- `participaciones_nuevas` — filas creadas con origen `importacion`.
- `participaciones_actualizadas` — ya convocados, marcados como respondieron.

## 7. Definition of Done

- [ ] Ingestar un archivo con individuos que no eran miembros del panel los da de alta como miembros de ese panel.
- [ ] Ingestar respuestas de alguien nunca convocado crea su participación con `respondio = true` y origen `importacion`.
- [ ] Ingestar respuestas de alguien ya convocado actualiza su participación existente, sin crear una segunda.
- [ ] Re-ingestar el mismo archivo no duplica membresías ni participaciones (test).
- [ ] Una membresía en estado `baja` no se reactiva por ingesta y se informa (test).
- [ ] `convocar()` sigue aplicando el gate de `contacto_participacion` sin cambios (test de no regresión).
- [ ] El resultado de la ingesta informa los cinco contadores nuevos.
- [ ] La tasa de respuesta de una ola refleja a los que respondieron sin haber sido convocados desde el sistema.

## 8. Fuera de alcance

- Selector de panel en la pantalla de carga: se decidió alta automática en el panel de la encuesta, sin opción.
- Reactivar membresías dadas de baja.
- Evaluar calidad de las respuestas importadas (R3.2).
- Convocar automáticamente a los individuos creados a encuestas futuras: la membresía los hace elegibles, pero convocar sigue siendo una acción explícita.
