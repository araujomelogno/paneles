# BUG — La validación de claves de dedup no reconoce el correo y bloquea la carga

**Sistema:** Gestión de paneles · carga de panelistas · paso de revisión (Fase 8)
**Severidad:** Alta — **impide cargar** una base válida
**Detectado:** 2026-10-05, tras desplegar la Fase 8
**Estado:** Para corregir

---

## 1. Qué pasa

En el paso de revisión, con una variable **mapeada a `email`**, el sistema
muestra:

> *«No hay ninguna variable marcada como documento, correo, ni nombre con fecha
> de nacimiento. Sin clave de deduplicación no hay forma de reconocer a alguien
> que ya esté en el sistema, y cada carga vuelve a crear a las mismas
> personas.»*

Y **la ingesta no se puede ejecutar**. El popup de revisión queda abierto, el
botón no produce efecto, y no se crea ningún trabajo: se verificó que
`ingesta_trabajo` no registró nada nuevo y que la API no recibió ningún pedido
—el último error de servidor es del día anterior—.

Son **dos defectos distintos** en el mismo punto.

---

## 2. Defecto 1 — La validación no reconoce el correo

El archivo tiene una variable de correo **mapeada al campo `email` de la
bóveda**, que es una de las tres claves con las que `dedup.resolver` trabaja
(documento → email → nombre + fecha de nacimiento). La validación igual afirma
que no hay ninguna.

El propio mensaje nombra el correo entre las claves que busca, así que hay una
inconsistencia entre lo que dice y lo que comprueba.

**Dos causas posibles, a verificar en el código:**

- La condición mira solo `documento` y la clave compuesta, y **omite `email`**.
- O exige que la clave tenga **valores no vacíos en todas las filas**, y la
  descarta porque la columna de correo tiene vacíos. Eso sería un criterio
  demasiado estricto: una clave con 300 correos sobre 1131 filas **sirve** —
  deduplica a esos 300— y no debe contarse como inexistente.

**Qué hay que corregir:**

- [ ] La validación reconoce las **tres** claves del dedup, en los mismos
      términos que `dedup.resolver`: documento, email, y nombre + fecha de
      nacimiento.
- [ ] Una clave con valores vacíos en algunas filas **cuenta como presente**.
      Lo que corresponde es informar su **cobertura** —«email: 300 de 1131
      filas»— no descartarla.
- [ ] El mensaje y la condición **dicen lo mismo**: si el texto nombra el
      correo, la comprobación tiene que mirarlo.

---

## 3. Defecto 2 — Advierte bloqueando, cuando debería advertir y dejar decidir

Aun si **no hubiera ninguna clave**, la carga tendría que poder ejecutarse. Hay
casos legítimos: una base que se carga una sola vez, o un archivo donde la
identidad ya se resuelve por el alias de la plataforma de origen.

Esto contradice dos cosas que ya están escritas:

- El criterio de la Fase 8 para la detección de PII: *«la advertencia no
  bloquea: es una decisión informada, no una prohibición»*.
- El principio de toda la fase: **el sistema propone, el analista decide**. El
  paso de revisión existe para que se vea lo que va a pasar, no para vetarlo.

**Qué hay que corregir:**

- [ ] La ausencia de clave de dedup es una **advertencia destacada**, no un
      bloqueo.
- [ ] La advertencia explica la consecuencia concreta —«una carga futura con la
      misma gente va a crear registros duplicados»— y ofrece **dos salidas
      explícitas**: volver a corregir el mapeo, o **continuar igual**.
- [ ] Continuar deja constancia de que se cargó sin clave de dedup, en el
      resultado de la ingesta.

> **Dónde sí corresponde bloquear.** No todo es advertencia: en modo «crear los
> individuos» **sin evidencia de consentimiento**, bloquear es correcto —no hay
> base legal para dar de alta a esa gente—. La diferencia es que ahí falta algo
> que el sistema no puede suplir con una decisión del usuario; acá falta algo
> cuyo costo el usuario puede asumir a conciencia.

---

## 4. Defecto 3 — Bloquear en silencio parece un cuelgue

Al apretar **Ingestar**, el popup queda abierto y nada se mueve: ni mensaje de
error, ni indicación de que algo está frenando. Quien lo usa concluye que el
sistema se colgó, no que lo está deteniendo a propósito —fue lo que pasó al
detectar este bug—.

- [ ] Si una validación impide continuar, **se dice en el momento**, junto al
      botón, con el motivo y qué hacer.
- [ ] Ninguna acción queda sin respuesta visible.

---

## 5. Definition of Done

- [ ] Una carga con una variable mapeada a `email` **no muestra** la advertencia
      de falta de clave (test).
- [ ] Lo mismo con `documento`, y con `nombre` + `fecha_nacimiento` (test de
      cada clave).
- [ ] Una clave presente solo en parte de las filas cuenta como presente, y el
      resumen informa su cobertura (test).
- [ ] Una carga **sin ninguna clave** muestra la advertencia y **se puede
      ejecutar igual** (test).
- [ ] El resultado de esa ingesta deja constancia de que se cargó sin clave de
      dedup (test).
- [ ] Toda validación que impida continuar muestra el motivo junto al botón
      (test).
- [ ] Sigue bloqueando el caso que **sí** debe bloquear: modo «crear los
      individuos» sin evidencia de consentimiento (test de no regresión).

## 6. Nota de proceso

La Fase 8 agregó varias detecciones al paso de revisión, y este es el riesgo que
quedó anotado en su propio spec:

> *«Más detección significa más decisiones por carga. Hay un punto donde el paso
> de revisión se vuelve tan largo que la gente confirma sin mirar — que es peor
> que no detectar nada.»*

Conviene revisar **todas** las detecciones agregadas en esa fase con el mismo
criterio: cuáles bloquean, cuáles advierten, y si esa clasificación es la
correcta. Una detección que bloquea sin salida convierte una ayuda en un
obstáculo.

- [ ] Inventariar las validaciones del paso de revisión y clasificarlas
      explícitamente en **bloqueante** o **advertencia**, con el motivo de cada
      una.
