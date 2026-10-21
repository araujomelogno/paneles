# SPEC — Temáticas incorporadas y sección de cargas

**Sistema:** Gestión de paneles · carga de panelistas
**Se apoya en:** vínculo persona ↔ carga y metadatos del estudio (R-ORG),
paso de revisión de la carga (R7.2)
**Estado:** Para desarrollo

---

## 1. Problem Statement

Hoy una carga sabe **de dónde vino** —nombre, fecha del estudio, público
objetivo— pero no **de qué trata**. Para saber qué temas cubrió un estudio hay
que abrir el SAV o recorrer sus preguntas una por una. Y no hay ninguna pantalla
donde mirar una carga como un todo: sus datos, cuánta gente trajo y quiénes.

Eso importa por una razón que ya apareció antes en este proyecto: para decidir
si un estudio se puede hacer con el panel o hay que salir a campo, la pregunta
es **«¿sobre qué temas tenemos información de esta gente?»**. Las temáticas por
carga son el primer paso para contestarla.

## 2. Goals

- Que cada carga tenga sus **temáticas incorporadas**: los temas que tratan las
  preguntas del archivo.
- Que se **propongan automáticamente** al subir el SAV, para no tipearlas.
- Que el vocabulario de temáticas sea **consistente entre cargas**, para poder
  comparar y filtrar después.
- Una **sección de cargas** para ver cada una con sus datos, sus temáticas y sus
  panelistas.

## 3. Non-Goals

- **No se clasifican las respuestas**, solo las preguntas.
- **Las temáticas no reemplazan la consulta semántica.** Permiten filtrar
  panelistas «sobre los que hay información de un tema», pero para encontrar
  quién opina qué sigue estando la búsqueda por concepto.
- **No hay jerarquía de temáticas** (área → tema) en esta versión.
- **Claude no decide solo**: propone, el analista confirma.

---

## 4. Requirements

### R-TEM.1 — Temáticas de una carga (P0)

- Dada una carga, entonces tiene una lista de **temáticas incorporadas**,
  multivaluada, que se muestra y edita como **etiquetas**: se agregan, se
  quitan, se reutilizan las existentes.
- Dada una temática, entonces es **una entidad propia**, compartida entre
  cargas, no texto suelto en cada una.
- [ ] Cada vínculo carga ↔ temática registra su origen: `propuesta_ia` o
      `manual`.

> **Por qué una entidad y no texto libre.** Si cada carga guarda sus etiquetas
> como texto, en poco tiempo hay «telefonía móvil», «telefonía celular» y
> «celulares» significando lo mismo, y la pregunta «¿qué cargas cubrieron
> telefonía?» deja de tener respuesta. Una temática compartida es lo que permite
> reutilizarla y, más adelante, filtrar por ella.

### R-TEM.2 — Propuesta con Claude al analizar el SAV (P0)

- Dado un SAV analizado, entonces el sistema envía a Claude la **lista de
  preguntas** y recibe una **propuesta de temáticas**.
- Dada la propuesta, entonces aparece en el **paso de revisión** (R7.2), junto
  al resto de la configuración de la carga.
- Dado el envío, entonces incluye **solo los textos de las preguntas** —código y
  texto— y **nunca respuestas** ni datos de personas.
- Dadas las variables marcadas como **demográficas o de identidad**, entonces
  **no se envían**: si se incluyeran, toda carga tendría «datos demográficos»
  como temática y no aportaría nada.
- Dado que la llamada a Claude **falla o tarda**, entonces la carga **no se
  bloquea**: las temáticas quedan vacías, con un aviso, y se pueden cargar a
  mano.
- [ ] La respuesta se pide como **salida estructurada** (herramienta forzada,
      como hace la verificación), no como texto a interpretar.
- [ ] Modelo y tiempo de espera configurables, con los mismos valores que la
      verificación.

### R-TEM.3 — Vocabulario consistente (P0)

- Dado el prompt, entonces incluye la **lista de temáticas ya existentes** en el
  sistema.
- Dada una temática existente que corresponde a las preguntas, entonces Claude
  **la reutiliza tal cual**, en vez de crear una variante.
- Dado que ninguna existente corresponde, entonces propone una **nueva**, y en
  la revisión se ve marcada como nueva.
- [ ] Las temáticas se normalizan al guardar (mayúsculas, espacios) para que
      «Telefonía móvil» y «telefonía móvil» sean la misma.

> **Este es el punto que más importa del requerimiento.** Sin él, la extracción
> funciona bien en cada carga y el conjunto se vuelve inutilizable en pocos
> meses.

### R-TEM.4 — Revisión y edición (P0)

- Dada la propuesta, entonces el analista puede **aceptar, quitar o agregar**
  temáticas antes de confirmar la carga.
- Dada una carga ya confirmada, entonces sus temáticas siguen siendo
  **editables** desde la sección de cargas.
- [ ] Una temática se puede agregar a mano, reutilizando las existentes con
      autocompletado.

### R-TEM.5 — Cargas existentes (P1)

- Dada una carga anterior a este cambio, entonces desde su detalle se puede
  pedir **«proponer temáticas»**.
- Dado ese pedido, entonces se usan las **preguntas ya guardadas** de esa carga
  (por su `ref_estudio`), sin volver a subir el archivo.
- [ ] La propuesta pasa por la misma revisión: no se aplica sola.

### R-TEM.6 — Temáticas de cada panelista (P0)

Las temáticas de la carga se trasladan **a los panelistas**, almacenadas en la
**bóveda** y consultables junto con los atributos demográficos.

**Qué significa que un panelista tenga una temática.** Un panelista tiene la
temática Y si **respondió al menos una de las preguntas relacionadas con Y**, en
cualquiera de sus cargas. No alcanza con haber estado en una carga que cubría Y.

> **Por qué así y no heredando las de la carga.** Una carga que cubre «consumo de
> tabaco» no garantiza que cada persona haya respondido esas preguntas: pueden
> haberlas salteado, o haber estado en una rama del cuestionario que no las
> incluía. Heredar la temática de la carga diría «tenemos información de esta
> persona sobre tabaco» cuando no la hay. Basarla en las respuestas dice lo que
> realmente se sabe.
>
> La relación pregunta ↔ temática **ya existe**: la propuesta de R-TEM.2
> devuelve, para cada temática, sus preguntas relacionadas. Hay que guardarla.

**Cálculo al ingestar**
- Dada la confirmación de la carga, entonces se guarda la relación **pregunta ↔
  temática** confirmada en la revisión.
- Dada la ingesta, entonces, para cada persona, se registra cada temática para la
  que respondió **al menos una pregunta relacionada**.
- Dada una respuesta **vacía** o con un valor de **no respuesta** (`No sabe`,
  `No contesta`, los códigos marcados como tales), entonces **no cuenta** como
  haber respondido.
- [ ] El cálculo se hace con los datos que la ingesta ya tiene en mano —la fila
      del archivo y su `id_persona`—, **sin leer el store semántico**.
- [ ] Una persona en varias cargas acumula las temáticas de todas.

**Sincronización**
- Dado que se editan las temáticas o la relación pregunta ↔ temática de una
  carga, entonces se **recalculan** las temáticas de los panelistas de esa carga.
- Dada una baja, entonces sus temáticas se eliminan con la persona.
- [ ] Para las cargas anteriores a este cambio (R-TEM.5), el cálculo inicial lee
      qué preguntas respondió cada individuo desde el store semántico, por
      `ref_estudio`. Es una operación única.

**Consulta**
- Dada la **consulta demográfica**, entonces se puede filtrar por temática:
  «panelistas con información sobre *telefonía móvil*».
- Dados los **resultados de una consulta**, entonces la temática se puede agregar
  como columna (R7.4).
- Dada la **ficha del panelista**, entonces muestra sus temáticas y de qué carga
  viene cada una.
- Dada la **composición**, entonces se puede ver cuántos panelistas de un ámbito
  tienen cada temática.

> **Esto es la respuesta a «¿podemos hacer este estudio con el panel?».** Contar
> cuántos panelistas de un segmento tienen información sobre un tema, sin tocar
> el store semántico ni pagar una consulta, es exactamente la pregunta de
> factibilidad que motivó todo esto.

**Temáticas sensibles**
- Dada una temática, entonces puede marcarse como **especial** (salud, consumo de
  sustancias, ideología política, religión, vida sexual), igual que los
  atributos demográficos de categoría especial (R3.14.e).
- Dada una temática especial, entonces **no se ofrece como columna** en los
  resultados, **no se incluye en exportaciones** con datos y su consulta queda
  **restringida por rol**.
- [ ] Al proponer temáticas, Claude indica cuáles podrían ser especiales, para
      que el analista lo confirme.

> **Por qué importa.** Las temáticas viven en la bóveda, junto a la identidad.
> Una temática no es la respuesta —dice «respondió sobre salud», no qué
> respondió— pero asociar una persona identificada con un tema sensible ya es
> información sobre ella. La arquitectura separa identidad y contenido
> justamente para que esto no ocurra por accidente; acá ocurre a propósito, y
> conviene que sea con control.

---

### R-CAR.1 — Sección «Cargas»: listado (P0)

Una sección nueva en el menú.

- Dada la sección, entonces lista las cargas con: **nombre**, **fecha del
  estudio**, **público objetivo**, **cantidad de individuos** y sus
  **temáticas**.
- Dada la lista, entonces se puede **filtrar por temática** y **buscar por
  nombre**.
- [ ] Ordenada por fecha, la más reciente primero.

### R-CAR.2 — Detalle de una carga (P0)

- Dada una carga, entonces su detalle muestra:
  - **Atributos**: nombre, descripción, fecha del estudio, público objetivo,
    fecha de carga.
  - **Individuos**: cuántos, separados en **creados** y **reutilizados** (el
    vínculo `persona_carga` ya lo distingue).
  - **Temáticas incorporadas**, editables (R-TEM.4).
  - **Panelistas cargados**: la lista de las personas vinculadas a esa carga.
- Dada la lista de panelistas, entonces está **paginada** y cada fila lleva a la
  ficha del panelista.
- Dada una carga, entonces se puede ir a su **composición** (el ámbito «una
  carga» de R-ORG ya existe).
- [ ] La lista de panelistas respeta **los mismos permisos** que la sección de
      panelistas: lo que un rol no puede ver ahí, tampoco lo ve acá.

---

## 5. El prompt

Sugerencia de estructura, para que Claude Code no tenga que inventarla:

**Instrucción:** se le explica que va a recibir las preguntas de un cuestionario
de investigación de mercado, y que tiene que identificar **los temas que el
cuestionario cubre**, no clasificar cada pregunta.

**Contexto que recibe:**
- La **lista de temáticas existentes**, con la indicación de reutilizarlas
  textualmente cuando correspondan.
- Las **preguntas**, como `código: texto`, sin las demográficas ni las de
  identidad.

**Reglas:**
- Entre **3 y 10 temáticas** por cuestionario.
- Cada temática es una **frase nominal corta en español**, de dos a cuatro
  palabras (*«telefonía móvil»*, *«consumo de tabaco»*, *«hábitos de compra de
  ropa»*).
- **No** proponer temáticas demográficas ni de datos de contacto.
- Preferir el nivel de **tema de investigación**, no el de pregunta: *«servicios
  de streaming»* y no *«Netflix»*.

**Salida:** una herramienta con una lista de objetos
`{ temática, es_nueva, preguntas_relacionadas }`. Las preguntas relacionadas
sirven para mostrarle al analista **de dónde salió** cada temática.

---

## 6. Cambios de esquema

En la bóveda:

- **`tematica`**: `id`, `nombre` (normalizado, único), `creado_en`.
- **`carga_tematica`**: `carga_id`, `tematica_id`, `origen`
  (`propuesta_ia` | `manual`), `creado_en`. Única por `(carga_id,
  tematica_id)`. Cascada al borrar la carga.
- **`tematica`**: agregar `es_especial boolean not null default false`.
- **`tematica_pregunta`**: `carga_id`, `tematica_id`, `codigo_pregunta`. Guarda
  qué preguntas sustentan cada temática en cada carga. Cascada al borrar la
  carga.
- **`persona_tematica`**: `id_persona`, `tematica_id`, `carga_id`,
  `preguntas_respondidas` (cuántas de las relacionadas), `creado_en`. Única por
  `(id_persona, tematica_id, carga_id)`. **Cascada al borrar la persona** y la
  carga.

Migración aditiva. Sin cambios en el store semántico.

> Si se borra una carga, sus vínculos con temáticas se van con ella, pero la
> temática queda: puede estar usada por otras cargas.

## 7. Definition of Done

- [ ] Al analizar un SAV, la revisión muestra temáticas propuestas (test con
      respuesta de Claude simulada).
- [ ] El envío a Claude contiene solo textos de preguntas, **sin respuestas** y
      **sin** variables demográficas ni de identidad (test).
- [ ] Una temática existente se reutiliza en vez de duplicarse (test).
- [ ] «Telefonía móvil» y «telefonía móvil» quedan como una sola (test).
- [ ] Si Claude falla, la carga se confirma igual y avisa (test).
- [ ] El analista puede quitar, agregar y editar temáticas antes y después de
      confirmar (test).
- [ ] La sección «Cargas» lista las cargas con sus atributos y temáticas, y
      filtra por temática.
- [ ] El detalle muestra creados y reutilizados por separado, y la lista
      paginada de panelistas con enlace a la ficha.
- [ ] Una carga anterior puede recibir temáticas propuestas sin volver a subir
      el archivo (test).
- [ ] La lista de panelistas respeta los permisos de la sección de panelistas
      (test).
- [ ] Un panelista que **respondió** una pregunta relacionada tiene la temática;
      uno que estuvo en la carga pero **no la respondió**, no (test).
- [ ] Una respuesta «No sabe» o vacía no cuenta como haber respondido (test).
- [ ] Editar las temáticas de una carga recalcula las de sus panelistas (test).
- [ ] La consulta demográfica filtra por temática (test).
- [ ] Una temática especial no aparece como columna ni en exportaciones (test).
- [ ] Una baja elimina las temáticas de la persona (test).

## 8. Costos

| Concepto | Costo |
|---|---|
| Una propuesta de temáticas por carga | **~US$ 0,02**: una llamada a Claude con unas decenas de preguntas (unos pocos miles de tokens de entrada y unos cientos de salida). |
| Proponer temáticas para cargas existentes | Lo mismo por carga, una sola vez. |
| Temáticas de cada panelista (R-TEM.6) | **US$ 0**: se calculan en la ingesta con datos que ya están en memoria, sin llamadas a ninguna API. |
| Volumen en la bóveda | Una fila por persona y temática. Con 1.000 panelistas y unas 8 temáticas por carga, unas 8.000 filas pequeñas: despreciable. |
| Esquema e interfaz | **US$ 0** de infraestructura. |

> Es el componente de IA más barato del sistema: una llamada por carga, contra
> la verificación, que hace una o varias por consulta.

## 9. Riesgos y preguntas abiertas

- **[producto]** El vocabulario va a crecer. Sin un dueño que lo revise de vez en
  cuando —fusionar sinónimos, renombrar—, la reutilización de R-TEM.3 lo frena
  pero no lo evita. Conviene prever, más adelante, una forma de **fusionar dos
  temáticas**.
- **[producto]** Hay dos niveles y conviene no confundirlos en la interfaz: las
  temáticas de **la carga** (qué cubrió el estudio) y las de **cada panelista**
  (sobre qué respondió). Que una carga cubra un tema no implica que todos sus
  panelistas lo tengan.
- **[privacidad]** R-TEM.6 lleva a la bóveda, junto a la identidad, un dato
  derivado del contenido de las respuestas. Es grueso —el tema, no la respuesta—
  y por eso aceptable, pero para temáticas especiales conviene revisarlo con
  quien asesora en protección de datos antes de habilitarlo.
- **[datos]** En las baterías de respuesta múltiple, una opción no marcada
  (`Unchecked`) **es** una respuesta: la persona vio la pregunta. Decidir si eso
  cuenta para la temática. La propuesta: sí cuenta, porque la persona respondió
  sobre el tema, aunque sea para decir que no.
- **[datos]** Los textos de las preguntas tienen que estar bien escritos para
  que la propuesta sirva. Las mejoras de interpretación del SAV (Fase 8: separar
  `"Opción:Pregunta"`, sacar consignas) mejoran también esto.
- **[alcance]** Este es el primer paso hacia el «descriptor semántico» de qué
  información hay en el panel. Si después se quiere saber **cuántas personas**
  tienen datos sobre cada temática, la base ya va a estar: temáticas y
  preguntas relacionadas.
