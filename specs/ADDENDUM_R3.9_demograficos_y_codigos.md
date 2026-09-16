# Addendum a R3.9 — Variables demográficas y códigos por tipo

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Spec de referencia:** `specs/SPEC_fase3.md` · R3.9 (ingesta desde archivo SAV)
**Estado:** Para desarrollo
**Última actualización:** 2026-09-15

---

## 1. Problem Statement

Dos problemas distintos en la pantalla de carga, uno de privacidad y uno de usabilidad.

**Los demográficos se están espejando al store semántico sin que nadie lo decida.** Si el archivo trae `SEXO`, `EDAD` o `LOCALIDAD`, hoy se precargan como variables cualquiera y terminan embebidas como `"Sexo → Femenino"`. Eso contradice una decisión explícita del PRD: los segmentadores quedan autoritativos en la bóveda y **no se espejan** al store semántico, para minimizar el riesgo mosaico. Ingestarlos como preguntas los espeja por la puerta de atrás. Y no se gana nada a cambio: el filtro demográfico ya se resuelve en la bóveda (R2.4 y el puente de R2.5), así que tenerlos también como vectores no mejora ninguna consulta — solo agrega cuasi-identificadores del lado que se quería mantener limpio.

Además, el mapeo de patronímicos hoy solo existe en modo «crear los individuos». Si el archivo trae demográficos de panelistas que ya están en el sistema, no hay forma de aprovecharlos ni de excluirlos.

**El campo de códigos se ofrece donde no corresponde.** Está habilitado para todos los tipos, incluso `abierta`, donde no hay códigos posibles: solo invita a cargar un mapeo que nunca se va a aplicar.

## 2. Goals

- Que las variables demográficas se declaren explícitamente y **no lleguen al store semántico**.
- Que esa declaración sirva además para poblar o completar la bóveda, en los dos modos de carga.
- Que el campo de códigos esté habilitado solo donde tiene sentido, sin perder las etiquetas de no respuesta de las variables numéricas.

## 3. Non-Goals

- **No se espeja ningún demográfico al store semántico**, ni con override en esta versión (ver Preguntas abiertas).
- **No se infiere automáticamente** qué variable es demográfica: hay sugerencia, la decisión es del analista.
- **No se sobrescriben datos de bóveda existentes** con los del archivo (ver R3.9.d).
- No cambia el pipeline de consulta ni el modelo de embeddings.

## 4. User Stories

- Como analista, quiero marcar qué variables del archivo son demográficas, para que no se mezclen con las preguntas del estudio.
- Como analista, quiero que al marcarlas se usen para completar los datos del panelista en la bóveda, sin tener que cargarlos aparte.
- Como DPO, quiero que los datos demográficos no se dupliquen en el store semántico, para no aumentar el riesgo de reidentificación.
- Como analista, quiero que la pantalla no me ofrezca cargar códigos en una pregunta abierta, porque no existen.
- Como analista, quiero poder declarar que en una variable numérica el `99` significa «No contesta», para que no se embeba un número sin sentido.

**Casos borde**
- Variable que es demográfica en el cuestionario pero es objeto del estudio (ej. «¿en qué barrio vivís?» en un estudio sobre barrios).
- Archivo con demográficos que **difieren** de lo que ya está en la bóveda para esa persona.
- Variable marcada como `cerrada` pero sin value labels en el archivo.
- Variable con value labels que el analista marca como `abierta`.

## 5. Requirements

### R3.9.d — Marcado de variables demográficas (P0)

En la pantalla de carga, cada variable puede marcarse como **demográfica**, indicando a qué campo de la bóveda corresponde (`sexo`, `fecha_nacimiento`, `localidad`, y los patronímicos `nombre`, `documento`, `email`, `celular`, `contacto`). El marcado aplica a **los dos modos** de R3.9; el mapeo de patronímicos que el spec ya exige en modo «crear los individuos» pasa a ser un subconjunto de este marcado, no un mecanismo aparte.

**Efecto sobre el store semántico (en ambos modos):**
- Dada una variable marcada como demográfica, entonces **no se ingesta como pregunta**: no genera `pregunta` ni `respuesta` ni embedding.
- Dado el resultado de la carga, entonces informa qué variables quedaron excluidas por demográficas, para que la exclusión sea visible y no silenciosa.

**Efecto sobre la bóveda:**
- Dado el modo «crear los individuos», entonces los valores de las variables demográficas se escriben en la bóveda al dar de alta a la persona (comportamiento ya especificado, ahora unificado bajo este marcado).
- Dado el modo «los panelistas ya existen» y un campo **vacío** en la bóveda, entonces el valor del archivo lo completa.
- Dado un campo **ya cargado** en la bóveda con un valor distinto al del archivo, entonces **no se sobrescribe**: se informa la discrepancia para que un responsable decida. El archivo de un estudio no es autoridad sobre la ficha del panelista.
- Dado el resultado, entonces informa cuántos campos se completaron y cuántas discrepancias se detectaron.

**Sugerencia y confirmación:**
- Dada la precarga, entonces el sistema **sugiere** el marcado por nombre y label de la variable (`SEXO`, `EDAD`, `LOCALIDAD`…), y el analista confirma o corrige.
- Dada una sugerencia, entonces nunca se aplica sola: una variable no se excluye del estudio sin que alguien lo haya confirmado.

> **Por qué la marca es del analista y no automática.** Una variable puede ser segmentador en un estudio y objeto de análisis en otro: «¿en qué barrio vivís?» es demográfico en un estudio de consumo y es *el* dato en uno sobre barrios. Ninguna heurística resuelve eso; quien carga el estudio, sí.

### R3.9.e — Campo de códigos según el tipo de pregunta (P0)

- Dado el tipo `abierta`, entonces el campo de códigos/etiquetas está **deshabilitado**: no hay códigos que traducir.
- Dados los tipos `cerrada` y `escala`, entonces el campo está habilitado y es el mapeo completo de opciones.
- Dado el tipo `numerica`, entonces el campo está habilitado pero **acotado a valores especiales** (no respuesta, no sabe, no aplica), con la aclaración en la interfaz de que ahí van esos códigos y no un mapeo completo.
- Dada una variable con value labels en el archivo que el analista marca como `abierta`, entonces se avisa antes de confirmar que ese mapeo se va a descartar.
- Dada una variable marcada como `cerrada` **sin** value labels, entonces se avisa que sus valores se van a embeber crudos (sin traducir).
- Dado un cambio de tipo en la pantalla, entonces el estado del campo de códigos se actualiza en el momento, sin perder lo ya cargado si el tipo vuelve a uno que lo admite.

> **Por qué numérica conserva el campo.** En datos de encuesta, las variables numéricas de SPSS suelen traer value labels **solo para los valores especiales** (`98 = No sabe`, `99 = No contesta`). Esa traducción importa para el embedding: `"¿Cuántos años tenés? → 99"` es ruido, `"→ No contesta"` es información. Deshabilitar el campo para numéricas perdería eso.

## 6. Cambios de esquema

Ninguno. El marcado de demográficas y la habilitación de códigos son de la capa de carga: determinan qué se escribe dónde, no agregan estructuras. Los campos demográficos ya existen en `persona`.

## 7. Definition of Done

- [ ] Una variable marcada como demográfica no genera `pregunta` ni `respuesta` ni embedding en el store semántico (test).
- [ ] El resultado de la carga informa qué variables se excluyeron por demográficas.
- [ ] En modo «crear», los valores demográficos se escriben en la bóveda al dar de alta.
- [ ] En modo «ya existen», un campo vacío de bóveda se completa con el valor del archivo.
- [ ] Un campo ya cargado con valor distinto **no** se sobrescribe y la discrepancia se informa (test).
- [ ] El sistema sugiere el marcado pero no lo aplica sin confirmación.
- [ ] El campo de códigos está deshabilitado para `abierta` y habilitado para `cerrada`, `escala` y `numerica`.
- [ ] Marcar como `abierta` una variable con value labels avisa que el mapeo se descarta.
- [ ] Marcar como `cerrada` una variable sin value labels avisa que los valores se embeben crudos.

## 8. Preguntas abiertas

- **[producto/privacidad]** ¿Se permite alguna vez embeber un demográfico como pregunta —por ejemplo, cuando es objeto del estudio—? Hoy la marca excluye sin excepción; un override reintroduciría el espejado que el PRD descarta. Si se decide habilitarlo, debería ser explícito, advertido y registrado.
- **[datos]** Discrepancias entre archivo y bóveda: además de informarlas, ¿conviene una cola de revisión como la de identidades ambiguas, o alcanza con el reporte de la carga?
- **[producto]** ¿La sugerencia de marcado debería aprenderse de cargas anteriores (mismo nombre de variable ya marcado antes), o se confirma siempre desde cero?
