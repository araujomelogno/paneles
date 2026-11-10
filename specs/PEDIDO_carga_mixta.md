# PEDIDO — Carga siempre mixta: sin la pregunta «¿Los panelistas ya están en el sistema?»

**Sistema:** Gestión de paneles · carga de respuestas y de panelistas
**Severidad:** Alta — hoy las personas nuevas de un archivo mixto **no se cargan**
**Detectado:** 2026-11-06
**Entrega:** una sola versión

---

## 1. Qué pasa

Un archivo de Alchemer trae dos tipos de filas:

- quienes llegaron por el enlace de la convocatoria, con su **`id_persona`** en la
  columna identificadora;
- quienes llegaron sin ese parámetro —personas nuevas—, con la columna **vacía**.

La carga obliga a elegir, en el paso 4, entre «los panelistas ya existen» y
«crear los individuos». Ninguna opción sirve para un archivo mixto:

- La creación (`sav.crear_individuos`) **descarta toda fila con la columna
  identificadora vacía** como `identificador_invalido`, y no la crea.
- Aunque la creara, la vinculación de sus respuestas también depende de esa
  columna.
- Con el tipo `id_persona` hay además una imposibilidad de fondo: una persona
  **nueva** no puede traer su `id_persona` en el archivo, porque lo genera el
  sistema al crearla.

Caso real: una carga con personas existentes y una nueva. Las existentes se
cargaron; la nueva quedó afuera con «el valor no tiene el formato del tipo
elegido».

## 2. La decisión

**Se elimina la pregunta 4.** Toda carga se trata como **mixta**, y el sistema
decide fila por fila si la persona existe o es nueva.

## 3. Qué se pide

### R-MIX.1 — Reconocimiento por capas (P0)

Cada fila se resuelve en este orden; la primera capa que encuentra a alguien
decide:

| # | Capa | Cuándo aplica |
|---|---|---|
| 1 | **Columna identificadora** (paso 2), según su tipo | Si la fila trae valor |
| 2 | **ID de respuesta de la plataforma** | Solo si se declaró esa columna (opcional) |
| 3 | **Dedup por correo y celular** | Siempre que la fila los traiga válidos |
| 4 | **Persona nueva** | Si ninguna capa la encontró |

**La capa 1 depende del tipo de identificador:**

| Tipo | Valor encontrado | Valor no encontrado | Sin valor |
|---|---|---|---|
| `id_persona` | Esa persona | **Error: «id desconocido»** — no se crea | Siguiente capa |
| Id de la plataforma (`alias`) | Esa persona | Siguiente capa; si termina nueva, se le **registra ese id** | Siguiente capa |
| Documento / correo / celular | Esa persona | Siguiente capa | Siguiente capa |

> **Por qué un `id_persona` desconocido no se crea.** No es una persona nueva: es
> un **enlace roto** —un id mal copiado, o el de alguien que se dio de baja—.
> Crear a alguien a partir de él generaría una persona falsa. Se informa y la
> fila no entra.

**Criterios de aceptación**
- Given un archivo con 50 filas con `id_persona` existente y 3 con la columna
  vacía y correo válido, When se carga, Then las 50 se vinculan a sus personas y
  las 3 se crean (o se reutilizan si el correo ya existe).
- Given una fila con un `id_persona` que no existe, When se carga, Then no se
  crea nadie y la fila figura como «id desconocido».

### R-MIX.2 — ID de respuesta como capa opcional (P1)

- Dado el paso 2, entonces se puede declarar **opcionalmente** una segunda
  columna: el **ID de respuesta** de la plataforma —en Alchemer, el
  identificador de cada respuesta, si el export lo incluye—.
- Dada una fila nueva creada con esa columna declarada, entonces su ID de
  respuesta se registra como su identificador en la plataforma (`alias_origen`).
- Dada la carga siguiente de la misma encuesta, entonces esa fila se reconoce por
  su ID de respuesta antes de llegar al dedup.

> **Por qué es útil aunque sea opcional.** En una encuesta incremental, la persona
> nueva de hoy vuelve a aparecer mañana, otra vez sin `id_persona`. Si tiene un
> correo o un celular válido, el dedup la reconoce. Si no —dato vacío, sucio, o una
> respuesta parcial que todavía no llegó a la pregunta de contacto—, sin el ID de
> respuesta **se crearía de nuevo** en cada carga. Es probable que el export de
> Alchemer no lo traiga hoy; si se puede agregar, cierra ese hueco.

### R-MIX.3 — Vista previa con los conteos (P0)

La pregunta 4 también protegía de un error grave: si se elige como identificadora
la columna equivocada, el modo «ya existen» evitaba crear cientos de personas
falsas. Esa protección pasa a la vista previa.

- Dada la revisión, entonces la vista previa muestra:

> 487 filas · **450 existentes** · **35 nuevas** · **2 con id desconocido**

  y, de las nuevas, cuántas se reconocieron por dedup (personas que ya existían de
  otro estudio) y cuántas se van a **crear**.
- Dadas personas nuevas **sin ninguna clave** —sin ID de respuesta declarado ni
  correo o celular válidos—, entonces se advierte: *«N personas nuevas no tienen
  correo ni celular: si aparecen en una carga posterior, se van a crear de
  nuevo»*.
- Dada una proporción de nuevas **inesperada** —por ejemplo, más de la mitad del
  archivo en una encuesta con audiencia—, entonces se advierte de forma destacada:
  probablemente la columna identificadora o su tipo están mal elegidos.
- [ ] Cambiar la columna o el tipo **recalcula** la vista previa, o la deja
      marcada como desactualizada hasta volver a revisar (ver también R-CTL.4).

### R-MIX.4 — Consentimiento solo cuando hay personas nuevas (P0)

- Dada una vista previa **sin** personas a crear, entonces **no** se pide la
  evidencia de consentimiento.
- Dada una vista previa **con** personas a crear, entonces se pide la evidencia de
  consentimiento —y la de canales (R-CAN.1)— y se aplica **solo a las nuevas**.
- [ ] Las reglas de siempre se mantienen: quien no evidencia la finalidad
      obligatoria no se crea, y un caso ambiguo del dedup va a revisión.

### R-MIX.5 — Encuestas con audiencia (P0)

- Dada una encuesta con audiencia, entonces las personas que **no** vinieron con un
  `id_persona` de la convocatoria quedan con su participación marcada **«no
  convocado»** (R-EU.4), aunque el dedup las reconozca como existentes.

## 4. Lo que no cambia

- Las cargas que traen **todas** las filas identificadas siguen funcionando igual.
- El dedup (`dedup.resolver`) y sus cautelas: un celular compartido va a revisión.
- La vinculación de las respuestas: las personas creadas en la carga se vinculan
  con las respuestas de su propia fila en el mismo proceso.

## 5. Definition of Done

- [ ] La pantalla de carga ya no tiene la pregunta «¿Los panelistas ya están en el
      sistema?».
- [ ] Un archivo mixto carga a las existentes y crea a las nuevas, con sus
      respuestas (test).
- [ ] Un `id_persona` inexistente no crea a nadie y se informa como «id
      desconocido» (test).
- [ ] Una fila nueva con correo ya registrado se reutiliza, no se duplica (test).
- [ ] Con ID de respuesta declarado, una persona nueva de la primera carga se
      reconoce en la segunda aunque no tenga correo ni celular (test).
- [ ] Sin ID de respuesta, la vista previa advierte cuántas nuevas quedan sin clave
      (test).
- [ ] Un archivo sin personas nuevas no pide evidencia de consentimiento; uno con
      nuevas, sí, y la aplica solo a ellas (test).
- [ ] Una proporción inesperada de nuevas se advierte de forma destacada (test).
- [ ] En una encuesta con audiencia, las nuevas quedan «no convocado» (test).

## 6. Costos

**US$ 0.** Lógica de la carga e interfaz.

## 7. Despliegue

Sigue las reglas del `CLAUDE.md` del repositorio: proyecto de gcloud fijado y
verificado al empezar; si hay migración, aplicada y `verificar_esquema.py` en
verde **antes** del `firebase deploy`; token de COLOQUIO regenerado
inmediatamente antes de la batería.
