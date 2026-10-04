# SPEC — Fase 7: Usabilidad de alta y de resultados

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**PRD de referencia:** `PRD_sistema_paneles_unificado.md`
**Estado:** Borrador para desarrollo · séptima fase
**Precondición:** Fases 1 a 4 desplegadas
**Última actualización:** 2026-10-02

---

## 1. Problem Statement

El sistema hace lo que tiene que hacer, pero en cuatro puntos obliga al usuario
a trabajar de más o a confiar sin ver.

**Hay que tipear la versión del consentimiento.** Es una cadena exacta
(`consentimiento-2026-01`) que debe coincidir con una versión publicada y
activa, o la operación falla. El dato ya existe en el sistema: pedirlo escrito
es pedirle a alguien que recuerde de memoria algo que el sistema conoce.

**La importación de un `.sav` se confirma a ciegas.** Se definen decenas de
cosas —qué variable identifica a la persona, qué va al store semántico, qué se
marca como demográfico y a qué atributo, qué evidencia el consentimiento— y se
aprieta ingestar sin ver nada junto. Es la operación **menos reversible** del
sistema: crea personas en la bóveda. Un error se descubre después, con los
datos ya adentro.

**Los resultados de una consulta son una lista de identificadores.** Para saber
quién es cada uno hay que salir a la sección de panelistas y buscarlo. El
analista pierde el hilo justo cuando está evaluando si el resultado sirve.

**Y esa lista muestra siempre lo mismo.** Si alguien busca por concepto pero
necesita ver la edad o la localidad para juzgar el resultado, no puede: tiene
que abrir cada uno.

## 2. Goals

- Que **no haya que tipear** un dato que el sistema ya tiene.
- Que una importación se **revise entera antes de ejecutarse**.
- Que el analista pueda **ver quién es cada resultado** sin salir de la pantalla.
- Que pueda **elegir qué mirar** en la lista de resultados.

## 3. Non-Goals

- **No cambia la lógica de ingesta** ni el pipeline de consulta.
- **No cambia el modelo de permisos**: lo que un rol podía ver, sigue viendo.
- **No convierte la lista de resultados en una planilla editable**: se elige qué
  columnas mirar, no se modifica nada desde ahí.
- **No agrega atributos nuevos**: las columnas disponibles son las del catálogo
  existente (R3.14).
- No cambia el contrato de la exportación seudonimizada (ver R7.4, decisión
  abierta).

---

## 4. Requirements

### R7.1 — Elegir la versión del consentimiento, no escribirla (P0)

**Dónde aplica:** alta manual de panelista, importación con creación de
individuos (evidencia de consentimiento por finalidad) y cualquier otro punto
donde hoy se pida la versión escrita.

- Dado un punto donde se declara la versión del consentimiento, entonces se
  presenta un **desplegable** con las versiones **publicadas y activas** de esa
  finalidad, en vez de un campo de texto.
- Dado que el desplegable es **por finalidad**, entonces muestra solo las
  versiones de esa finalidad: una versión de `contacto_participacion` no es
  elegible para `uso_semantico`.
- Dada una finalidad **sin ninguna versión activa**, entonces el desplegable lo
  dice con claridad y **apunta a dónde publicarla** (Inscripciones → Textos de
  consentimiento), en vez de quedar vacío sin explicación.
- Dada una versión elegida, entonces se puede **ver el texto completo** antes de
  confirmar: es lo que la persona aceptó, no un código.
- [ ] Si hay una sola versión activa para esa finalidad, viene preseleccionada.
- [ ] El valor enviado al backend sigue siendo la misma cadena de versión: no
      cambia el contrato.

> **Por qué importa más de lo que parece.** El trigger `consentimiento_texto`
> ya rechaza una versión no publicada, así que tipear mal hoy produce un error.
> El desplegable no agrega una validación: **elimina la posibilidad del error**,
> y de paso hace visible qué textos hay publicados, que es información que hoy
> está escondida en otra pantalla.

### R7.2 — Paso de revisión antes de importar (P0)

Un paso nuevo entre «definir» y «ejecutar», en la importación de `.sav` (y en
la carga de panelistas sin panel).

- Dado que se terminó de definir el mapeo, entonces se muestra un **resumen
  completo** antes de ingestar, con:
  - **Identidad:** modo (los panelistas ya existen / crear los individuos), qué
    columna identifica a la persona y de qué tipo es.
  - **Consentimiento:** qué variable evidencia cada finalidad, qué valor cuenta
    como afirmativo y qué versión de texto se declaró.
  - **Al store semántico:** qué variables se van a ingestar como preguntas, con
    su texto y su tipo, y cuántas son.
  - **A la bóveda:** qué variables se marcaron como demográficas y a qué
    atributo van; para las categóricas, el mapeo de valores a categorías.
  - **Excluidas:** qué variables del archivo no se van a ingestar y por qué
    (demográficas, sin declarar, descartadas a mano).
  - **Panel:** a qué panel quedan asociados los individuos (o que no quedan
    asociados a ninguno).
  - **Volumen:** cuántas filas trae el archivo y cuántas respuestas se van a
    escribir.
- Dado el resumen, entonces se destacan las **advertencias** que hoy aparecen
  recién al final: valores sin mapear a una categoría, variables marcadas como
  pregunta sin texto, variables del archivo que no matchean ninguna declaración.

**Cada variable, agrupada por destino.** El resumen no es una lista plana: las
variables se muestran separadas visualmente según adónde van, de modo que se
vea de un golpe si algo está en el grupo equivocado.

| Grupo | Qué incluye | Qué se muestra de cada una |
|---|---|---|
| **Al store semántico** | Las que se ingestan como preguntas | código, texto de la pregunta, tipo |
| **A la bóveda · demográficas** | Las mapeadas a un atributo del catálogo | código, **texto de la pregunta**, atributo destino, y el mapeo de valores a categorías |
| **A la bóveda · identidad y contacto** | Nombre, documento, email, celular, contacto | código, **texto de la pregunta**, campo destino |
| **Claves de deduplicación** | Las que participan del dedup (R1.2) | ver abajo |
| **Excluidas** | Las que no se ingestan | código y motivo |

- Dada una variable mapeada a un campo de bóveda, entonces el resumen muestra
  **el texto de la pregunta junto al código**, no solo el código.

> **Por qué el texto y no solo el código.** `var138O1320 → documento` no dice
> nada. *«Cigarrillos: ¿has consumido en el último mes?» → documento* salta a la
> vista. Es un error real que ocurrió en producción y que el código solo no
> deja ver.

**Las claves de dedup, con su conteo de repetidos.** El dedup (R1.2) resuelve
por documento, por email y por nombre + fecha de nacimiento. Para cada una de
esas claves presentes en el archivo, el resumen muestra:

| | |
|---|---|
| **Valores no vacíos** | cuántas filas traen esa clave |
| **Valores distintos** | cuántos valores únicos hay |
| **Filas que colisionan** | cuántas comparten su valor con otra |
| **Grupos repetidos** | cuántos valores aparecen más de una vez, y los primeros ejemplos |

- Dada una clave de dedup, entonces se informa cuántas filas se van a **fusionar
  en una misma persona** por esa clave.
- Dado el total, entonces el resumen dice **cuántas personas se van a crear**
  frente a cuántas filas trae el archivo, y la diferencia explicada por el
  dedup.
- Dada una clave donde **la mayoría de las filas colisiona** —pocos valores
  distintos sobre muchas filas—, entonces se muestra como **advertencia
  destacada**, no como un número más.

> **Este conteo es el mejor detector de un mapeo equivocado.** Si alguien mapea
> por error una variable de sí/no a `documento`, el resumen lo grita:
> «documento: 1131 filas, **2 valores distintos**, 1129 colisionan → se crearían
> **2 personas**». Sin ese número, el error pasa y el dedup fusiona la base
> entera en dos registros. Con él, es imposible no verlo.
- Dado el resumen, entonces se puede **volver atrás a corregir** sin perder lo
  ya definido.
- Dada la confirmación en el resumen, entonces recién ahí se ejecuta.
- [ ] El resumen es **exportable o copiable**: sirve como registro de qué se
      definió en esa carga.

> **Por qué una importación merece un paso que otras operaciones no tienen.**
> Es la operación menos reversible del sistema: crea personas en la bóveda,
> escribe embeddings y da de alta membresías. Deshacerla es borrar gente. Un
> paso de revisión cuesta diez segundos y es la última oportunidad de ver que la
> columna de identidad está mal elegida antes de crear 5.000 duplicados.

### R7.3 — Ficha del panelista desde los resultados (P0)

- Dado un resultado de consulta, entonces se puede hacer clic en un individuo y
  se abre una **ficha emergente** sin salir de la pantalla ni perder el
  resultado.
- Dada la ficha, entonces muestra sus **atributos demográficos** —los del
  catálogo, con sus valores actuales— y la procedencia de cada uno cuando
  aplique (por ejemplo, si la edad es derivada o envejecida).
- Dada la ficha, entonces muestra también **la evidencia del resultado**: qué
  respondió y de qué estudio, que es lo que ya trae el ranking.
- Dada la ficha, entonces **no muestra datos identificatorios** (nombre,
  documento, correo, celular) por defecto.
- Dado que se quiere ver quién es, entonces se usa la acción de
  **reidentificación que ya existe**, que pide motivo y queda registrada.
- [ ] Cerrar la ficha devuelve al resultado en el mismo estado, sin re-consultar.

> **Por qué la ficha no trae nombre ni contacto.** La consulta devuelve un
> conjunto seudonimizado a propósito; reidentificar es un acto deliberado y
> auditado (R3.10). Si la ficha mostrara el nombre con un clic, la auditoría de
> reidentificación dejaría de reflejar quién vio los datos de quién — que es
> exactamente lo que existe para demostrar.
>
> **Aun así, los demográficos combinados son cuasi-identificadores.** Sexo,
> localidad y tramo etario juntos pueden señalar a una persona en un panel
> chico. No convierte la ficha en reidentificación, pero conviene tenerlo
> presente al decidir qué atributos se muestran por defecto.

### R7.4 — Elegir las columnas de la lista de resultados (P0)

- Dada la lista de resultados, entonces se puede elegir **qué columnas mostrar**,
  entre los atributos demográficos del catálogo.
- Dadas las columnas elegidas, entonces la selección **se recuerda** para ese
  usuario, de una consulta a la siguiente.
- Dado un individuo **sin valor** para un atributo mostrado, entonces la celda
  lo dice («sin dato»), y **no se confunde con una categoría**.
- Dado un atributo marcado como **categoría especial** (R3.14.e), entonces no se
  ofrece como columna sin una decisión explícita: su visualización masiva en una
  lista es otra cosa que verlo en una ficha.
- [ ] Las columnas por defecto se mantienen como hoy, para no cambiarle la
      pantalla a quien no quiera tocar nada.
- [ ] Agregar columnas no debe obligar a re-ejecutar la consulta: los atributos
      se resuelven sobre el conjunto de resultados ya obtenido.

> **Decisión abierta: ¿afecta la exportación?** Hoy el CSV seudonimizado exporta
> `id_persona`, puntaje y evidencia (R3.10). Si las columnas elegidas se
> incluyeran, ese archivo pasaría a llevar demografía, y un CSV con sexo,
> localidad y tramo etario de 200 personas es bastante más identificable que uno
> con tokens. **Propuesta: el CSV mantiene su contrato actual y la selección de
> columnas afecta solo la vista.** Si se decide lo contrario, conviene que el
> archivo lo advierta como hace el de exportación con datos.

### R7.5 — Sección de estadísticas de base (P0)

Hoy, para saber cuántos panelistas o cuántas respuestas hay, se consulta la base
a mano. La información está repartida entre los dos stores y nadie la ve junta.

**Panelistas (bóveda)**
- Total de panelistas, y cuántos están **sin panel**.
- Panelistas por panel.
- Con consentimiento vigente, **por finalidad** (`contacto_participacion`,
  `uso_semantico`): cuántos lo tienen y cuántos no.
- Con celular válido y con correo, y por canal de contacto aceptado.
- Altas por mes, para ver cómo crece el panel.

**Corpus semántico**
- Respuestas totales, individuos con respuestas, preguntas distintas.
- Estudios y cargas ingestadas, con su fecha.
- Promedio de respuestas por individuo.

**La brecha entre los dos stores** — lo más útil de toda la sección:
- **Panelistas sin ninguna respuesta semántica**: existen en la bóveda pero son
  invisibles para una consulta por concepto. Es el número que explica por qué
  una búsqueda devuelve menos gente de la esperada.
- **Panelistas sin `uso_semantico` vigente**: aunque tengan respuestas, no
  pueden aparecer en resultados.
- **Individuos en el store semántico sin panelista en la bóveda**: debería ser
  **cero**; si no lo es, hay datos huérfanos y es un problema de integridad.

**Tamaño y salud del corpus**
- Tamaño de la tabla de respuestas y del **índice vectorial**.
- Dimensión de los embeddings en uso.
- Una señal de **cuándo conviene subir de tier**: comparar el tamaño del índice
  con la memoria de la instancia, con el umbral documentado en `COSTOS.md` §4.

**Cargas e ingestas**
- Últimas cargas con su estado, filas procesadas y descartes por motivo.
- Trabajos **fallidos o colgados**, que hoy solo se ven consultando la base.

Criterios:
- Dada la sección, entonces se muestra en una sola pantalla, sin pedir
  parámetros.
- Dado un número, entonces se puede ver **de qué está compuesto** (por ejemplo,
  los panelistas sin respuestas, listados).
- Dada la carga de la pantalla, entonces no re-ejecuta consultas pesadas por
  cada tarjeta: los conteos se resuelven en pocas consultas.
- [ ] Los indicadores que ya existen en composición (R2.3) **no se duplican**:
      esta sección enlaza a esa, no la repite.
- [ ] La sección no muestra datos de ninguna persona en particular: son
      agregados. Ver el detalle de un panelista sigue siendo la ficha.

> **Por qué la brecha entre stores es lo que más importa.** Los conteos sueltos
> —«1.008 panelistas», «24.935 respuestas»— se miran una vez. El número que se
> usa todas las semanas es *«de mis 1.131 panelistas, ¿sobre cuántos puedo
> realmente consultar?»*: eso es lo que define si una búsqueda sirve, y hoy no
> se puede saber sin cruzar las dos bases a mano.

---

## 5. Cambios de esquema

- **Preferencia de columnas por usuario** (R7.4): guardar la selección. Puede ir
  en la ficha del usuario en Firestore o en una tabla de preferencias; no
  requiere nada en la bóveda.
- Sin otros cambios: las versiones de consentimiento, los atributos y la
  reidentificación ya existen.

## 6. Definition of Done

**R7.1**
- [ ] La versión del consentimiento se elige de un desplegable en el alta manual
      y en la importación (test de cada punto).
- [ ] El desplegable muestra solo versiones activas de esa finalidad (test).
- [ ] Una finalidad sin versión activa lo informa y apunta a dónde publicarla
      (test).
- [ ] Se puede ver el texto completo antes de confirmar.
- [ ] El valor enviado al backend no cambia (test de no regresión).

**R7.2**
- [ ] La importación muestra el resumen antes de ejecutar, con las siete
      secciones (test).
- [ ] Las advertencias de valores sin mapear aparecen en el resumen, no recién
      al final (test).
- [ ] Volver atrás desde el resumen conserva lo definido (test).
- [ ] Nada se escribe hasta confirmar en el resumen (test).

**R7.3**
- [ ] Un clic en un resultado abre la ficha sin perder el resultado (test).
- [ ] La ficha muestra atributos demográficos y la evidencia, y **no** muestra
      nombre, documento, correo ni celular (test).
- [ ] Reidentificar sigue siendo una acción aparte y queda registrada (test de
      no regresión).

**R7.2 (ampliación)**
- [ ] El resumen agrupa las variables por destino y muestra el texto de la
      pregunta junto al código en los grupos de bóveda (test).
- [ ] Para cada clave de dedup se muestran valores distintos, filas que
      colisionan y personas a crear (test).
- [ ] Una clave donde la mayoría de las filas colisiona aparece como
      advertencia destacada (test con un archivo preparado).

**R7.5**
- [ ] La sección muestra los conteos de ambos stores en una sola pantalla.
- [ ] Los panelistas sin respuestas semánticas se pueden listar, no solo contar.
- [ ] Individuos semánticos sin panelista se informan como problema de
      integridad si son más de cero (test).
- [ ] La pantalla se resuelve en pocas consultas, sin una por tarjeta
      (medición).

**R7.4**
- [ ] Se pueden agregar y quitar columnas demográficas de la lista (test).
- [ ] La selección se recuerda entre consultas (test).
- [ ] Un individuo sin valor muestra «sin dato» y no una categoría (test).
- [ ] Un atributo de categoría especial no se ofrece como columna (test).
- [ ] Agregar una columna no re-ejecuta la consulta (test).

## 7. Costos

| Concepto | Costo |
|---|---|
| Las cuatro mejoras | **US$ 0**: son cambios de interfaz y de backend, sin infraestructura nueva. |
| Carga adicional sobre la bóveda (R7.3 y R7.4) | Marginal: resolver atributos de hasta unas decenas de resultados es una consulta relacional chica. |
| R7.5 — estadísticas | Marginal si se resuelve en pocas consultas agregadas. **Con cuidado**: un `count(*)` sobre la tabla de respuestas crece con el corpus; conviene medirlo y, si pesa, cachear los conteos con un refresco periódico. |

> **Lo único a vigilar:** si R7.4 se implementa resolviendo atributos fila por
> fila en vez de en una sola consulta, una lista de 200 resultados dispara 200
> consultas. Con `paneles-boveda` en `db-f1-micro` eso se nota. Resolverlos en
> una sola pasada sobre el conjunto.

## 8. Riesgos y preguntas abiertas

- **[privacidad]** R7.3 y R7.4 juntas hacen la demografía mucho más visible: una
  lista con sexo, localidad y tramo etario de 200 personas es bastante más
  identificable que una lista de tokens, aunque ningún dato sea un identificador
  directo. No lo bloquea, pero conviene decidir qué atributos se ofrecen por
  defecto.
- **[producto]** Decisión abierta de R7.4: si las columnas elegidas entran o no
  en el CSV seudonimizado.
- **[producto]** El paso de revisión (R7.2) agrega un clic a una operación que
  ya tiene varios. Si resulta molesto en cargas chicas, evaluar saltearlo por
  debajo de un umbral de filas — pero **nunca** en el modo «crear los
  individuos», que es el irreversible.
- **[datos]** El resumen de R7.2 solo vale si refleja lo que de verdad se va a
  ejecutar. Conviene que se arme del mismo objeto que se envía al backend, y no
  de una reconstrucción paralela que pueda divergir.
