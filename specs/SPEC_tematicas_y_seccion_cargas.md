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


---

# Ampliación — Taxonomía de temáticas y correcciones del filtro

**Estado:** Para desarrollo · sobre lo ya desplegado de este spec
**Fecha:** 2026-10-20

## T1. Por qué

Con tres temáticas, una lista plana alcanza. Con cada carga nueva la lista crece,
y en poco tiempo el filtro de temáticas se vuelve una columna larga, sin orden, y
con casi-duplicados difíciles de ver. Hace falta un nivel arriba que agrupe.

La decisión es que esa taxonomía **no sea una lista fija definida a mano**: la
propone y la hace crecer Claude a partir de las temáticas que van apareciendo.
Pero **se calcula una vez y se guarda**, y solo cambia con aprobación del
analista.

> **Por qué no generarla en cada consulta.** Claude no responde dos veces
> exactamente igual: un día el área se llama «telecomunicaciones» y al otro
> «comunicaciones», o agrupa distinto. El analista encontraría el filtro
> reorganizado cada vez, y cualquier consulta guardada que use un área dejaría de
> coincidir. Además sumaría segundos de espera y un costo por cada vez que alguien
> abre el filtro. **Dinámica en cómo crece; estable en cómo se ve.**

## T2. Requirements

### R-TEM.7 — Dos niveles: área y temática (P0)

- Dada una temática, entonces pertenece a **un área**.
- Dada un área, entonces agrupa varias temáticas; tiene **nombre**, una
  **descripción corta** y la marca **`es_especial`**.
- [ ] Exactamente **dos niveles**. Tres o más parecen más ordenados, pero en la
      práctica nadie sabe en qué rama va cada cosa y la clasificación se vuelve
      inconsistente.
- [ ] Todo se referencia **por id**: renombrar un área no rompe filtros ni
      consultas guardadas.

### R-TEM.8 — Arranque de la taxonomía (P0)

- Dadas las temáticas existentes, entonces **una sola llamada** a Claude propone
  las áreas y ubica cada temática en una.
- Dada la propuesta, entonces el analista la **revisa** en una pantalla —puede
  renombrar áreas, mover temáticas, unir dos áreas— y recién al confirmar se
  guarda.
- [ ] Guía para Claude: **entre 8 y 20 áreas**, nombres de dos a cuatro palabras
      en minúsculas, nivel de sector o ámbito de investigación (*«telecomunicaciones
      y conectividad»*, *«consumo masivo»*, *«finanzas y medios de pago»*).
- [ ] Claude indica qué áreas **podrían ser especiales** (salud, sustancias,
      ideología política, religión, vida sexual); el analista confirma.

### R-TEM.9 — Crecimiento con cada carga (P0)

- Dada una carga nueva, entonces **en la misma llamada que ya propone sus
  temáticas** (R-TEM.2), Claude ubica cada temática en un **área existente**.
- Dado que ninguna área existente corresponde, entonces Claude propone una
  **nueva**, marcada como tal en la revisión.
- [ ] El prompt recibe la lista de áreas existentes **con sus temáticas**, para
      que la ubicación sea por contenido y no solo por nombre.
- [ ] La herramienta `proponer_tematicas` agrega, en cada temática, `area` y
      `area_es_nueva`.
- [ ] **No se regenera nada**: lo ya clasificado no se toca.

> **Clasificar es más estable que generar.** Con las áreas ya existentes, Claude
> **elige** de una lista en vez de inventar un nombre. Eso es lo que hace que el
> primer nivel se mantenga consistente aunque las temáticas de abajo varíen.

### R-TEM.10 — Reorganización a pedido (P1)

- Dado un botón **«Reorganizar taxonomía»**, entonces Claude recibe toda la
  taxonomía y propone **operaciones**: fusionar temáticas, mover una temática de
  área, renombrar un área, fusionar áreas, dividir un área.
- Dada la propuesta, entonces cada operación se **aprueba o descarta por
  separado**.
- Dada una **fusión de temáticas**, entonces se reapuntan todas sus referencias
  —`carga_tematica`, `tematica_pregunta`, `persona_tematica`— a la que queda, sin
  duplicar filas.
- [ ] **Nunca se aplica nada automáticamente.**
- [ ] Cada cambio aplicado queda registrado (quién, cuándo, qué), para poder
      entender después por qué una temática está donde está.

### R-TEM.11 — Sensibilidad en el área (P1)

- Dada un área **especial**, entonces todas sus temáticas **heredan** el
  tratamiento especial de R-TEM.6: sin columna en resultados, sin exportación con
  datos, consulta restringida por rol.
- Dada una temática especial en un área que no lo es, entonces conserva su propia
  marca: la herencia **suma**, no resta.

### R-TEM.12 — El filtro, agrupado por área (P0)

- Dado el filtro de temáticas en la consulta, entonces se muestra **agrupado por
  área**, cada área plegable.
- Dada un área, entonces se puede seleccionar **entera**: «con información sobre
  *telecomunicaciones y conectividad*» equivale a tener cualquiera de sus
  temáticas.
- [ ] Una búsqueda por texto sobre áreas y temáticas, para cuando la lista sea
      larga.

---

## T3. Correcciones del filtro (lo que reveló «nivel educativo»)

### Qué pasó

La temática **«nivel educativo»** aparece en el filtro con «(1 carga)», pero
**ninguna persona la tiene**. La secuencia:

1. Al analizar el SAV, Claude propuso las temáticas viendo **todas** las
   preguntas, incluida la de nivel educativo.
2. Después, en la revisión, esa variable se marcó como **demográfica**.
3. El cálculo por persona (R-TEM.6) ya la excluyó —las demográficas no cuentan—,
   así que nadie quedó con esa temática.
4. Pero la temática **siguió asociada a la carga**, y el filtro cuenta **cargas**,
   no personas: la muestra igual.

Hay dos defectos: uno de orden (la propuesta no se actualiza al marcar
demográficas) y uno del filtro (cuenta lo que no corresponde).

### R-TEM.13 — La propuesta sigue a la marca de demográficas (P0)

- Dada una variable que se marca como **demográfica o de identidad** en la
  revisión, entonces las temáticas propuestas cuyas **preguntas relacionadas
  quedaron todas excluidas** se **retiran de la propuesta**, en el momento.
- Dada una temática que todavía tiene otras preguntas relacionadas no excluidas,
  entonces se mantiene.
- [ ] Se resuelve con los datos que ya trae la propuesta (`preguntas_relacionadas`),
      **sin volver a llamar a Claude**.
- [ ] Desmarcar la variable vuelve a mostrar la temática.

### R-TEM.14 — El filtro cuenta personas (P0)

- Dado el filtro, entonces cada temática y cada área muestran **cuántas
  personas** tienen información sobre ellas (desde `persona_tematica`), no
  cuántas cargas.
- Dada una temática con **cero personas**, entonces **no se ofrece** en el filtro
  de consulta. Sigue visible en el detalle de la carga, marcada «sin personas»,
  para poder quitarla.
- [ ] En el detalle de una carga, una temática con cero personas se señala como
      **probablemente sobrante**, con la acción de quitarla.

> **Por qué personas y no cargas.** El filtro sirve para encontrar **gente**. Que
> una temática esté en una carga dice qué cubrió el estudio; cuántas personas la
> tienen dice si vale la pena filtrar por ella.

### Limpieza de lo ya cargado

«Nivel educativo» se puede quitar ya, sin esperar el arreglo:

```sql
-- Bóveda. Quita la temática de las cargas y, si no la usa nadie más, la borra.
delete from carga_tematica
 where tematica_id = (select id from tematica where nombre = 'nivel educativo');
delete from tematica_pregunta
 where tematica_id = (select id from tematica where nombre = 'nivel educativo');
delete from tematica t
 where t.nombre = 'nivel educativo'
   and not exists (select 1 from persona_tematica pt where pt.tematica_id = t.id);
```

- [ ] Después del arreglo, correr una revisión general: listar las temáticas con
      **cero personas** en todas las cargas, que son las candidatas a quitar.

---

## T4. Cambios de esquema

- **`area_tematica`**: `id`, `nombre` (normalizado, único), `descripcion`,
  `es_especial boolean`, `origen` (`propuesta_ia` | `manual`), `creado_en`.
- **`tematica`**: agregar `area_id` → `area_tematica(id)`. Nulo permitido solo
  hasta el arranque (R-TEM.8); después, toda temática tiene área.
- **`taxonomia_cambio`**: registro de las operaciones aplicadas (R-TEM.10):
  tipo de operación, objetos afectados, actor, fecha.

## T5. Definition of Done

- [ ] El arranque propone áreas para las temáticas existentes y se guarda solo al
      confirmar (test con respuesta simulada).
- [ ] Una carga nueva ubica sus temáticas en áreas existentes, en la misma
      llamada que ya se hacía (test).
- [ ] Si ninguna área corresponde, se propone una nueva marcada como tal (test).
- [ ] La taxonomía **no cambia** al abrir el filtro ni al consultar (test).
- [ ] La reorganización propone operaciones y no aplica ninguna sin aprobación
      (test).
- [ ] Fusionar dos temáticas reapunta cargas, preguntas y personas sin duplicar
      (test).
- [ ] Un área especial vuelve especiales a sus temáticas (test).
- [ ] El filtro agrupa por área, permite seleccionar un área entera y muestra
      cantidad de **personas** (test).
- [ ] Una temática con cero personas no aparece en el filtro de consulta (test).
- [ ] Marcar una variable como demográfica retira de la propuesta las temáticas
      que salían solo de ella, sin llamar a Claude (test).
- [ ] «Nivel educativo» ya no aparece en el filtro.

## T6. Costos

| Concepto | Costo |
|---|---|
| Arranque de la taxonomía | **Centavos**, una vez: una llamada con la lista de temáticas (frases cortas; aun con 500, unos pocos miles de tokens). |
| Crecimiento por carga | **Sin llamadas adicionales**: va en la llamada que ya propone las temáticas; el prompt crece un poco con la lista de áreas. |
| Reorganización a pedido | **Centavos** cada vez que se pide. |
| Abrir el filtro, consultar | **US$ 0**: la taxonomía está guardada. |
| Esquema e interfaz | **US$ 0** de infraestructura. |

> **Lo que se evita:** generar la taxonomía en cada consulta habría multiplicado
> esos centavos por cada apertura del filtro, sumado segundos de espera y
> producido una agrupación distinta cada vez.

## T7. Riesgos y preguntas abiertas

- **[producto]** La primera taxonomía condiciona las siguientes: R-TEM.9 clasifica
  contra lo que ya existe. Conviene que el arranque se revise con cuidado, y
  hacerlo cuando haya **varias cargas** cargadas —con tres temáticas, Claude no
  tiene material para proponer áreas con sentido—.
- **[producto]** Las áreas nuevas propuestas en el crecimiento tienden a
  multiplicarse si cada carga trae un tema distinto. La reorganización periódica
  es lo que lo contiene; conviene pedirla cada tanto, no solo cuando la lista ya
  se volvió inmanejable.
- **[datos]** Fusionar temáticas es irreversible en la práctica: las referencias
  se reapuntan. El registro de cambios permite saber qué pasó, no deshacerlo.
