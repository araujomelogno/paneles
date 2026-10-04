# SPEC — Fase 8: Calidad del dato semántico

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**PRD de referencia:** `PRD_sistema_paneles_unificado.md`
**Estado:** Borrador para desarrollo · octava fase
**Precondición:** Fase 7 desplegada
**Última actualización:** 2026-10-04

---

## 1. Problem Statement

El motor de búsqueda es tan bueno como el texto que se embebe, y hoy ese texto
sale casi tal cual del archivo. En la primera carga real —1.131 personas,
24.935 respuestas— eso produjo entradas como:

```
"Cigarrillos:Pensando en el ÚLTIMO mes, ¿has consumido alguno de estos
 productos? Seleccione los que correpondan → Checked"
```

Tres problemas en una sola línea.

**`Checked` no significa nada en español.** El modelo capta el tema por el texto
de la pregunta, pero no distingue bien quién respondió que sí de quién respondió
que no: `Checked` y `Unchecked` son dos palabras parecidas entre sí y ninguna
dice lo que representa. Es el problema de polaridad del que el reranker se
ocupa, pero agravado: acá no hay señal semántica que rescatar.

**La pregunta no es una afirmación.** `"Cigarrillos:Pensando en el último mes…"`
arrastra un prefijo de batería y una consigna de instrumento (*«Seleccione los
que correspondan»*) que no aportan significado y diluyen el concepto.

**Se guardan las respuestas que nadie dio.** Cada batería de respuesta múltiple
ingesta todas sus opciones, marcadas y no marcadas. Miles de respuestas que
dicen «no» sobre cosas que la persona no eligió: no aportan, ocupan lugar en el
ranking y se pagan como embeddings.

Y hay un problema que no se ve hasta que es tarde: **las preguntas abiertas de
tipo «Otro: especificar» pueden contener datos personales** —un nombre, un
teléfono, el lugar donde alguien trabaja— y hoy se embeben sin que nadie lo
mire. El guardrail de PII bloquea columnas **declaradas** como PII, no texto
libre que la contenga.

Todo esto es corregible a mano, variable por variable, en la pantalla de carga.
Nadie lo hace porque no se nota hasta que una búsqueda falla.

## 2. Goals

- Que el sistema **detecte** los patrones que degradan el dato y **proponga** la
  corrección, en vez de dejarla a la vista de quien carga.
- Que el analista **vea cómo va a quedar** el texto embebido antes de ingestar.
- Que **no se ingeste ruido**: opciones no marcadas, valores de no respuesta,
  preguntas vacías.
- Que un estudio mal cargado se pueda **corregir sin volver a subir el archivo**.
- Que la **PII en texto libre** se advierta antes de llegar al store semántico.

## 3. Non-Goals

- **El sistema propone, el analista confirma.** Ninguna corrección se aplica
  sola: el texto es lo que se embebe, y una reescritura automática equivocada
  degrada la búsqueda en silencio.
- **No se canonizan las respuestas abiertas** (sigue vigente *schema-on-read*).
- **No se corrige la ortografía ni se reescribe lo que la gente contestó.**
- **No se detecta PII con un modelo**: son patrones (correo, teléfono, cédula),
  con los falsos positivos y negativos que eso implica.
- No cambia el pipeline de consulta ni el modelo de embeddings.

## 4. Composición de la fase

| Bloque | Requisitos | Tema |
|---|---|---|
| **8A — Interpretar el archivo** | R8.1, R8.2, R8.3 | Baterías, textos y tipos |
| **8B — Normalizar valores** | R8.4, R8.5 | Etiquetas y no respuesta |
| **8C — Detectar problemas** | R8.6, R8.7 | PII en texto libre, variables inútiles |
| **8D — Ver y rehacer** | R8.8, R8.9 | Vista previa y reproceso |

**8D es el que más rinde si hay que priorizar:** la vista previa evita los
errores antes de cometerlos, y el reproceso permite arreglar lo ya cargado sin
pedir el archivo de nuevo.

---

## 5. Requirements

### Bloque 8A — Interpretar el archivo

#### R8.1 — Detectar baterías de respuesta múltiple (P0)

Las variables dicotómicas de una misma pregunta comparten tres señales: el
**prefijo del código** (`var138O1320`, `var138O1321`, `var138O1322`), el **texto
después de los dos puntos** y el **mismo par de opciones** (`0: Unchecked`,
`1: Checked`).

- Dado un archivo con ese patrón, entonces el sistema **agrupa** esas variables
  y las presenta como una batería, no como preguntas sueltas.
- Dada una batería detectada, entonces se ofrece **ingestar solo las opciones
  marcadas**, descartando las no marcadas.
- Dada esa opción activada, entonces una persona que no marcó ninguna opción de
  la batería **no genera respuestas** para ella.
- [ ] La opción viene **sugerida pero no aplicada**: el analista decide.
- [ ] El resultado informa cuántas respuestas se descartaron por no estar
      marcadas.

> **Por qué descartar lo no marcado.** Una respuesta que dice «no consume
> vaporizador» sobre alguien que simplemente no lo eligió no es información: es
> el reverso de una ausencia. Compite por lugar en el ranking con respuestas que
> sí dicen algo, y se paga como embedding. En la primera carga real, las
> baterías representan una porción importante de las 24.935 respuestas.

#### R8.2 — Proponer un texto de pregunta autocontenido (P0)

- Dado un label con el formato `"Opción:Pregunta"`, entonces el sistema separa
  las dos partes y **propone** un texto que integre ambas como afirmación:
  `"Cigarrillos:Pensando en el último mes, ¿has consumido…?"` →
  *«Pensando en el último mes, ¿consumió cigarrillos?»*
- Dado un texto con consignas de instrumento —«Seleccione los que
  correspondan», «Marque una opción»—, entonces se propone quitarlas.
- Dado un label **truncado** por SPSS (cortado a mitad de palabra), entonces se
  marca como tal para que el analista lo complete.
- [ ] Toda propuesta es **editable** y ninguna se aplica sin confirmación.
- [ ] El texto original queda guardado junto al editado, para poder volver.

> **Por qué esto importa más que el resto.** El texto de la pregunta es la mitad
> de lo que se embebe. Una pregunta bien redactada hace que la búsqueda la
> encuentre; una con prefijos y consignas la diluye. Y es lo único de esta fase
> que no se puede arreglar después sin reprocesar.

#### R8.3 — Inferir mejor el tipo de variable (P1)

- Dada una variable cuyas etiquetas son **el mismo valor que el código** (`16:
  "16"`, `17: "17"`… como en `EDAD` o «cigarrillos por día»), entonces se
  propone tratarla como **numérica**, no como cerrada: ese mapeo no aporta nada.
- Dada una variable con opciones ordinales (escalas de acuerdo, de frecuencia),
  entonces se propone el tipo `escala`.
- Dada una variable abierta con sufijo de «otro» (`…Othr`) **asociada a una
  cerrada**, entonces se ofrece **fusionarla con su cerrada** en vez de
  ingestarla suelta: *«¿Qué marca fumás? → Otra: Nevada Blue»* dice más que una
  respuesta abierta sin contexto.

---

### Bloque 8B — Normalizar valores

#### R8.4 — Pares conocidos y etiquetas sin sentido (P0)

- Dado un par `Checked` / `Unchecked` (o `Yes` / `No`, o `1` / `0` con
  etiquetas vacías), entonces se propone normalizarlo a **`Sí` / `No`**.
- Dada una etiqueta que es solo el código (`11427`), entonces se marca como
  **sin traducir** y se advierte: esa respuesta se va a embeber como un número
  sin significado.
- Dado un valor con espacios o mayúsculas inconsistentes, entonces se normaliza
  sin cambiar su contenido.

> **El caso que esto evita.** Una pregunta cerrada cuyos códigos no se
> tradujeron produce *«¿Qué marca fumás? → 11427»*. Está en la base, cuenta como
> respuesta, y es inútil para la búsqueda. Hoy solo se descubre mirando el texto
> embebido a mano.

#### R8.5 — Valores de no respuesta (P0)

- Dados los valores especiales habituales —`98`, `99`, `No sabe`, `No contesta`,
  `No aplica`— entonces se detectan y se ofrece **no ingestarlos**.
- Dado que se ingestan igual, entonces se embeben con su etiqueta en texto
  («No contesta»), nunca con el código.
- [ ] La lista de valores considerados no respuesta es configurable.

> Una respuesta «no sabe» embebida compite en el ranking con respuestas reales
> sobre el mismo tema, y no dice nada sobre la persona. Pero a veces sí
> interesa saber quién no contestó: de ahí que sea una opción y no una regla.

---

### Bloque 8C — Detectar problemas

#### R8.6 — PII en texto libre — **advertir antes de embeber** (P0)

El guardrail actual bloquea las columnas **declaradas** como PII. No mira el
**contenido** de las abiertas, y ahí puede haber de todo: un nombre, un
teléfono, un correo, el lugar donde alguien trabaja.

- Dada una variable abierta, entonces se analizan sus valores buscando patrones
  de **correo, teléfono, cédula y URL**.
- Dado que se detectan coincidencias, entonces se **advierte antes de ingestar**,
  con cuántos casos y ejemplos, y se ofrece: excluir la variable, o ingestarla
  igual de forma consciente.
- [ ] La advertencia no bloquea: es una decisión informada, no una prohibición.
- [ ] La detección es por patrones, no por modelo: va a tener falsos positivos
      (un número largo que no es un teléfono) y falsos negativos (un nombre
      suelto es indistinguible de una palabra cualquiera). **Se informa como
      indicio, no como certeza.**

> **Por qué importa.** El store semántico está diseñado para no contener datos
> identificatorios: es lo que permite que un dataset de embeddings sea menos
> riesgoso que uno de microdatos. Una abierta con teléfonos adentro rompe esa
> propiedad sin que ninguna salvaguarda actual se entere.

#### R8.7 — Variables que no aportan (P1)

- Dada una variable **sin ninguna respuesta** en el archivo, entonces se propone
  excluirla.
- Dada una variable donde **casi todas las respuestas son iguales**, entonces se
  informa: aporta poco a una búsqueda y mucho volumen.
- Dado un conjunto de variables, entonces el resumen muestra **cuántas
  respuestas va a generar cada una**, para que el peso de cada decisión sea
  visible.

---

### Bloque 8D — Ver y rehacer

#### R8.8 — Vista previa del texto embebido (P0)

- Dado el paso de revisión (R7.2), entonces se muestran **ejemplos reales** de
  cómo va a quedar el texto embebido de cada variable, con filas del archivo.
- Dada una corrección (texto, mapeo, normalización), entonces la vista previa se
  actualiza **en el momento**.
- [ ] Se muestran al menos dos ejemplos por variable, incluyendo un valor
      frecuente y uno poco frecuente.

> **Es el requisito que más errores evita de toda la fase.** Ver
> *«Cigarrillos:Pensando en el ÚLTIMO mes… → Checked»* escrito tal cual, antes
> de ingestar, hace evidente lo que ninguna lista de variables deja ver.

#### R8.9 — Reprocesar un estudio sin recargar el archivo (P0)

- Dado un estudio ya ingestado, entonces se pueden **corregir los textos de
  pregunta y los mapeos** y volver a generar los embeddings, sin subir el
  archivo de nuevo.
- Dado un reproceso, entonces solo se re-embeben las respuestas cuyo texto
  cambió: el `hash_texto` (Fase 2) ya permite saber cuáles.
- Dado un reproceso, entonces las respuestas que no cambian **no se tocan**: ni
  se re-embeben ni se re-escriben.
- Dada una variable que pasa a excluirse, entonces sus respuestas se eliminan
  del store semántico.
- [ ] El reproceso corre por la vía diferida (Cloud Tasks), como cualquier
      ingesta grande.
- [ ] Queda registrado qué se cambió y cuándo.

> **Sin esto, un error de texto es permanente o carísimo.** Hoy, descubrir
> después de cargar que las preguntas quedaron mal redactadas obliga a pedir el
> archivo otra vez y rehacer todo. Con el reproceso, se corrige el texto y se
> re-embebe solo lo afectado.

---

## 6. Cambios de esquema

- **`pregunta`**: guardar el **texto original** del archivo junto al texto
  editado, para poder volver y para auditar qué se cambió.
- **Configuración de la carga**: persistir las decisiones de normalización
  (batería, valores de no respuesta, pares normalizados), para que un reproceso
  sepa qué se había decidido.
- Sin cambios en `respuesta` (`hash_texto` ya existe) ni en la bóveda.

## 7. Definition of Done

**8A**
- [ ] Una batería de dicotómicas se detecta y se agrupa (test con el archivo real).
- [ ] Ingestar solo lo marcado descarta las no marcadas y lo informa (test).
- [ ] Un label `"Opción:Pregunta"` produce una propuesta de texto autocontenido,
      editable y no aplicada sola (test).
- [ ] Una variable con etiquetas iguales a sus códigos se propone como numérica
      (test).

**8B**
- [ ] `Checked`/`Unchecked` se propone como `Sí`/`No` (test).
- [ ] Una etiqueta sin traducir se advierte antes de ingestar (test).
- [ ] Los valores de no respuesta se detectan y se pueden excluir (test).

**8C**
- [ ] Una abierta con correos o teléfonos dispara la advertencia, con conteo y
      ejemplos (test).
- [ ] La advertencia no bloquea la ingesta (test).
- [ ] Una variable sin respuestas se propone excluir (test).

**8D**
- [ ] La vista previa muestra el texto embebido real y se actualiza al corregir
      (test).
- [ ] Un reproceso re-embebe solo lo que cambió y deja intacto el resto (test
      con `hash_texto`).
- [ ] Excluir una variable en un reproceso borra sus respuestas del store
      semántico (test).
- [ ] El reproceso corre por la vía diferida (test).

## 8. Costos

| Concepto | Efecto |
|---|---|
| Descartar opciones no marcadas (R8.1) | **Ahorro**: menos embeddings al ingestar y menos corpus que indexar. En la primera carga real, las baterías son una porción significativa de las 24.935 respuestas. |
| Descartar no respuestas (R8.5) | **Ahorro** del mismo tipo. |
| Reproceso (R8.9) | **Costo por uso**: re-embeber lo corregido. El `hash_texto` lo acota a lo que cambió; sin él sería el corpus entero. |
| Detección de PII y de patrones | US$ 0: son expresiones regulares sobre datos que ya están en memoria. |
| Infraestructura nueva | **Ninguna**. |

> **El ahorro indirecto es mayor que el directo:** menos corpus significa índice
> más chico, lo que posterga tener que subir de tier la instancia semántica
> (~US$ 24/mes de `db-f1-micro` a `db-g1-small`). Ver `COSTOS.md` §4.

## 9. Riesgos y preguntas abiertas

- **[producto]** El riesgo central de toda la fase es la **reescritura
  automática mal hecha**: si el sistema propone un texto que cambia el sentido
  de la pregunta y el analista confirma sin leer, la búsqueda queda degradada y
  nadie se entera. De ahí que todo sea propuesta, que el original se conserve y
  que exista la vista previa.
- **[producto]** Más detección significa más decisiones por carga. Hay un punto
  donde el paso de revisión se vuelve tan largo que la gente confirma sin mirar
  — que es peor que no detectar nada. Conviene que lo detectado se muestre
  **jerarquizado**: primero lo que rompe, después lo que mejora.
- **[datos]** La detección de PII por patrones **no es garantía**. Un nombre
  suelto en una abierta es indistinguible de cualquier palabra. Sirve para
  atrapar lo evidente, no para certificar que no hay datos personales.
- **[datos]** Descartar las opciones no marcadas cambia qué se puede consultar:
  deja de poder buscarse «quiénes **no** consumen cigarrillos» por la vía
  semántica. Para eso está el filtro demográfico o una pregunta cerrada normal.
  Vale decidirlo a conciencia, no por ahorro.
- **[alcance]** Nueve requisitos es mucho para un sprint. Si hay que recortar,
  **8D primero** (vista previa y reproceso): evita errores nuevos y permite
  arreglar los viejos, que es lo que más vale con un corpus ya cargado.
