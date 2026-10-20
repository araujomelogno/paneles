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
- **No se buscan panelistas por temática** en esta versión: eso es la consulta
  semántica. Las temáticas describen la carga, no reemplazan la búsqueda.
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

## 8. Costos

| Concepto | Costo |
|---|---|
| Una propuesta de temáticas por carga | **~US$ 0,02**: una llamada a Claude con unas decenas de preguntas (unos pocos miles de tokens de entrada y unos cientos de salida). |
| Proponer temáticas para cargas existentes | Lo mismo por carga, una sola vez. |
| Esquema e interfaz | **US$ 0** de infraestructura. |

> Es el componente de IA más barato del sistema: una llamada por carga, contra
> la verificación, que hace una o varias por consulta.

## 9. Riesgos y preguntas abiertas

- **[producto]** El vocabulario va a crecer. Sin un dueño que lo revise de vez en
  cuando —fusionar sinónimos, renombrar—, la reutilización de R-TEM.3 lo frena
  pero no lo evita. Conviene prever, más adelante, una forma de **fusionar dos
  temáticas**.
- **[producto]** Las temáticas describen **la carga**, no a cada persona. Que
  una carga cubra «consumo de tabaco» no significa que todas sus personas hayan
  respondido esas preguntas. No usarlas como si fueran un atributo del
  panelista.
- **[datos]** Los textos de las preguntas tienen que estar bien escritos para
  que la propuesta sirva. Las mejoras de interpretación del SAV (Fase 8: separar
  `"Opción:Pregunta"`, sacar consignas) mejoran también esto.
- **[alcance]** Este es el primer paso hacia el «descriptor semántico» de qué
  información hay en el panel. Si después se quiere saber **cuántas personas**
  tienen datos sobre cada temática, la base ya va a estar: temáticas y
  preguntas relacionadas.
