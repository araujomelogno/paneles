# SPEC — Encuestas y cargas unificadas

**Sistema:** Gestión de paneles · Equipos Consultores
**Origen:** sesión de brainstorming, 2026-11-04
**Estado:** Para desarrollo
**Se apoya en:** cargas, temáticas y clientes (R-ORG, R-TEM, R-CLI), lista negra
(R-LN), borrado de carga (0030), ingesta diferida, unidades de evidencia (R-UE),
consentimiento por finalidad (fase 5)

---

## 1. Problem Statement

Hoy el sistema tiene **dos caminos paralelos** para ingestar respuestas: la
**carga** (sección Panelistas) y la **ingesta de una encuesta** (sección
Encuestas). Comparten el motor, pero todo lo construido en los últimos meses
—preview, temáticas por IA, clientes, origen creado/reutilizado, sección Cargas,
borrado— quedó **solo del lado de las cargas**. Una respuesta que entró por una
encuesta no aparece en el filtro de temáticas ni en el de clientes, aunque para la
pregunta de factibilidad —*¿sobre qué tenemos información de esta gente?*— vale lo
mismo.

Además, la encuesta está atada a un panel: no hay forma de hacer un estudio a un
**conjunto puntual** de panelistas elegido con una consulta, ni de archivar un
panel sin perder sus encuestas, ni de borrar los datos de un estudio cuando ya no
deben estar.

El costo de no resolverlo es doble: **datos inconsistentes** según por dónde
entraron, y **operación de campo forzada** a usar paneles para cosas que no son
paneles.

## 2. El modelo

La decisión central de la sesión:

| Concepto | Qué es | Qué guarda |
|---|---|---|
| **Encuesta** | Un **estudio**: una operación de campo | Nombre, cliente(s), fecha de campo, público objetivo, temáticas, cuestionario, panel **o** audiencia propia, estado |
| **Carga** | **Una ingesta** de respuestas de esa encuesta (un archivo) | Archivo, cuándo y quién la subió, modo de relevamiento, filas, resultado, origen creado/reutilizado de cada persona |
| **Participación** | Que una persona fue **convocada** a una encuesta y si **respondió** | Una sola por persona y encuesta |

- **Una encuesta tiene una o más cargas.** Lo normal es más de una: un estudio
  mixto telefónico + web trae dos archivos, y es habitual ingestar un parcial con
  el campo abierto y el final al cerrar.
- **Toda carga pertenece a una encuesta.** No hay cargas sueltas.
- **Todas las cargas de una encuesta comparten el cuestionario** de la encuesta.
  Por eso una persona tiene **una sola respuesta por pregunta** dentro de la
  encuesta, aunque aparezca en varios archivos.

## 3. Goals

1. **Una sola forma de ingestar**: el mismo flujo —preview, revisión, temáticas
   por IA, clientes, consentimiento— para cualquier respuesta que entre al
   sistema, venga de donde venga.
2. **Cero duplicados** dentro de una encuesta: ni personas, ni participaciones, ni
   respuestas, aunque se carguen varios archivos con la misma gente.
3. **Encuestas a medida**: poder convocar a un conjunto de panelistas elegido con
   una consulta, sin crear un panel para eso.
4. **Ciclo de vida completo**: archivar paneles y borrar los datos de un estudio,
   sin perder a los panelistas.
5. **Ver el sesgo de no respuesta**: la composición de quienes respondieron frente
   a la de quienes fueron convocados.

## 4. Non-Goals

- **Mapear variables entre archivos con códigos distintos.** Si el archivo
  telefónico y el web nombran distinto la misma pregunta, en esta versión quedan
  como preguntas distintas. Es un problema real pero aparte (§11).
- **Borrar paneles.** Se archivan. Borrar destruiría historia —quién perteneció a
  qué panel y cuándo— sin una necesidad que lo justifique.
- **Convocar desde el sistema por canales nuevos.** La convocatoria sigue usando
  lo que ya existe; este spec define **a quién** se convoca, no **cómo**.
- **Usar la lista negra para el consentimiento.** Quien no consiente no va a la
  lista negra (§7.6).
- **Políticas automáticas de retención.** Se borra una encuesta a pedido; un borrado
  programado por antigüedad es una mejora posterior.

## 5. User Stories

**Analista de investigación**
- Como analista, quiero ingestar las respuestas de una encuesta con el mismo
  preview y las mismas temáticas por IA que una carga, para que todos los datos
  queden igual de descritos.
- Como analista, quiero cargar el parcial y después el archivo final de una misma
  encuesta, para trabajar con datos parciales sin que se dupliquen al cerrar.
- Como analista, quiero ver la composición de quienes respondieron frente a la de
  quienes convoqué, para saber qué segmentos responden menos.

**Coordinador de campo**
- Como coordinador, quiero crear una encuesta para un conjunto de panelistas
  elegidos con una consulta, para hacer un estudio a medida sin armar un panel.
- Como coordinador, quiero que se me avise si aparece en el archivo alguien que no
  fue convocado, para detectar un enlace reenviado.
- Como coordinador, quiero archivar un panel que ya no uso, para que no aparezca al
  crear encuestas, sin perder su historia.
- Como coordinador, quiero que me avise si subo dos veces el mismo archivo, para no
  procesarlo de nuevo por error.

**Responsable de datos / protección de datos**
- Como responsable de datos, quiero borrar las respuestas de un estudio cuando un
  cliente lo pide, conservando a los panelistas y sus datos demográficos.
- Como responsable de datos, quiero ver antes de confirmar una ingesta quiénes
  quedan sin consentimiento y qué implica, para no incorporar datos que no
  corresponden.

---

## 6. Requirements — P0

### R-EU.1 — Toda ingesta es una carga de una encuesta

- Dada la ingesta de respuestas de una encuesta, entonces se crea **una carga
  vinculada a esa encuesta** y se sigue **el mismo flujo de la sección Panelistas**:
  análisis, revisión, calidad del dato, preview, temáticas, consentimiento,
  procesamiento diferido.
- Dada una carga desde la sección **Panelistas**, entonces se **crea una encuesta
  automáticamente** —independiente, sin convocatoria, en estado cerrada— con los
  datos del estudio, y la carga queda dentro.
- Dada una carga, entonces registra la **participación** de cada persona en su
  encuesta, marcada como `respondio = true`.
- [ ] **Un solo flujo en el código**: la pantalla y la lógica de la sección
      Panelistas se reutilizan; no se mantiene una segunda versión en Encuestas.

### R-EU.2 — Los datos del estudio viven en la encuesta

- Dado un estudio, entonces **nombre, clientes, fecha de campo, público objetivo y
  temáticas** son de la **encuesta**.
- Dada una carga, entonces guarda **solo lo propio del archivo**: cuándo y quién la
  subió, el **modo de relevamiento** (telefónico, web…), el tipo de datos de
  origen, cantidad de filas, el resultado y el origen creado/reutilizado de cada
  persona.
- Dada una segunda carga de la misma encuesta, entonces **no vuelve a pedir** los
  datos del estudio: ya están.
- [ ] Las temáticas se proponen con IA **una vez por encuesta**, en su primera
      carga. Una carga posterior que trae preguntas nuevas propone temáticas solo
      para esas preguntas.

### R-EU.3 — Sin duplicados dentro de una encuesta

- Dadas varias cargas de una encuesta, entonces **todas escriben en el mismo
  cuestionario** del store semántico (el de la encuesta, por su `ref_estudio`).
- Dada una persona en más de una carga, entonces tiene **una sola respuesta por
  pregunta**: si dos archivos responden lo mismo, **gana la última carga**.
- Dada una persona en más de una carga, entonces tiene **una sola participación**
  en la encuesta (lo garantiza `unique (encuesta_id, id_persona)`).
- Dada una persona en más de una carga, entonces **no se crea dos veces**: la
  segunda carga la reconoce por el dedup, igual que hoy.
- Dado un archivo **idéntico** a uno ya cargado en esa encuesta, entonces se
  **avisa antes de procesar**, con la opción de cancelar.
- [ ] Cada respuesta del store semántico registra **qué carga la escribió por
      última vez** (`carga_id`), necesario para borrar una carga sin afectar a las
      otras (R-EU.10).

**Criterios de aceptación**
- [ ] Tres cargas de la misma encuesta con las mismas 100 personas dejan 100
      personas, 100 participaciones y una respuesta por persona y pregunta.
- [ ] Un parcial y después el final: las respuestas finales reemplazan a las
      parciales.
- [ ] Subir el mismo archivo dos veces avisa antes de procesar.

### R-EU.4 — Crear a quienes no existen, y marcar a los no convocados

- Dado un archivo con personas que no existen, entonces se **crean** (modo «crear
  los individuos», que ya existe) y se registra su participación.
- Dada una encuesta **con audiencia** —de panel o independiente—, entonces quien
  responde **sin haber sido convocado** se crea igual, pero su participación queda
  marcada **«no convocado»**, y el preview lo muestra antes de confirmar.
- [ ] El analista puede **excluir de la carga** a los no convocados desde el preview.

> **Por qué marcar y no rechazar.** En una encuesta de panel, un respondente
> desconocido casi siempre es un **enlace reenviado**. Crearlo sin aviso mete en el
> sistema a alguien que nadie eligió; rechazarlo pierde datos que pueden ser
> válidos. Marcarlo deja la decisión a quien conoce el estudio.

### R-EU.5 — Encuestas independientes

- Dada una encuesta nueva, entonces se elige si es **de panel** o **independiente**.
- Dada una encuesta independiente, entonces su audiencia se arma desde el
  **resultado de una consulta**: «crear encuesta con estos panelistas».
- Dada esa creación, entonces la audiencia es una **foto**: se registran como
  participaciones convocadas las personas del resultado **en ese momento**; la
  consulta no se vuelve a ejecutar.
- Dada una encuesta independiente, entonces **no se asocia a ningún panel** y **no
  se convoca a ningún panel**: solo a su audiencia.
- Dada la audiencia, entonces quedan **fuera automáticamente** quienes están en
  **lista negra** o **sin consentimiento** de `contacto_participacion`, y el
  resumen dice cuántos y por qué.
- [ ] `encuesta.panel_id` pasa a admitir nulo.

### R-EU.6 — Consentimiento al ingestar, sin lista negra

- Dado el preview de una carga, entonces muestra **quiénes quedan sin
  consentimiento** y **qué implica**, por finalidad:
  - sin `contacto_participacion` → no se los convocará en el futuro;
  - sin `uso_semantico` → **sus respuestas no entran al store semántico**.
- Dada la confirmación, entonces se registra el consentimiento **tal como vino**,
  por finalidad.
- [ ] Quien no consiente **no va a la lista negra**. La lista negra es para fraude,
      calidad o comportamiento.

> **Por qué así.** El efecto buscado —no volver a convocar a quien no quiere— ya lo
> produce no tener `contacto_participacion`. Y las respuestas de quien no dio
> `uso_semantico` **no pueden** entrar al store semántico para consultarlas a
> futuro: es lo que el consentimiento por finalidad existe para impedir.

### R-EU.7 — Migración de lo existente

- Dada cada **carga existente** (Centel y las demás), entonces se crea **su
  encuesta**: independiente, cerrada, con el **mismo `ref_estudio`** —así el
  cuestionario del store semántico queda vinculado sin tocarlo—, y se le pasan
  nombre, fecha, público objetivo, clientes y temáticas.
- Dada cada **encuesta existente con respuestas** ingestadas por el flujo viejo,
  entonces se crea **una carga** que las representa, con sus personas.
- Dada cada persona de una carga migrada, entonces se crea su **participación**
  (`respondio = true`).
- [ ] La migración **no re-embebe nada** ni llama a ninguna API.
- [ ] Antes de migrar: respaldo de las dos bases y una **corrida de prueba** que
      informe qué va a crear, sin escribir.

**Criterios de aceptación**
- [ ] Después de migrar, toda carga tiene encuesta.
- [ ] Ninguna respuesta del store semántico queda sin cuestionario de una encuesta.
- [ ] Los filtros de temáticas y clientes devuelven las mismas personas que antes
      para las cargas migradas.

---

## 7. Requirements — P1 (entran en esta misma versión)

### R-EU.8 — Archivar paneles

- Dado un panel, entonces se puede **archivar** (el estado ya existe en la tabla).
- Dado un panel archivado, entonces:
  - **no** se pueden crear encuestas ni convocatorias en él;
  - **no** aparece en los listados ni selectores por defecto (sí con un filtro
    «incluir archivados»);
  - **conserva** sus membresías, sus encuestas y su composición, que se pueden
    consultar;
  - se puede **reactivar**.
- [ ] Un panel con encuestas **en campo** no se puede archivar hasta cerrarlas.
- [ ] Archivar no toca a los panelistas: siguen existiendo y pueden estar en otros
      paneles.

### R-EU.9 — Borrar una encuesta

- Dada una encuesta, entonces se puede **borrar**, con vista previa y confirmación
  fuerte (escribir su nombre).
- Al borrarla **se eliminan**: sus **respuestas del store semántico** (cuestionario,
  preguntas, respuestas, y las unidades de evidencia que queden sin respuestas),
  sus **participaciones**, sus **cargas**, y sus temáticas por panelista.
- **Se conservan**: las **personas** y sus **atributos demográficos**, aunque hayan
  llegado en ese archivo.
- [ ] Trabajo reanudable por pasos, primero el store semántico y después la bóveda,
      como el borrado de carga.
- [ ] Auditado: quién, cuándo, cuántas respuestas y participaciones.
- [ ] Bloqueado con una ingesta en curso.

### R-EU.10 — Borrar una carga dentro de una encuesta

- Dada una carga, entonces al borrarla se eliminan **las respuestas que esa carga
  escribió por última vez** (`respuesta.carga_id`). Las que una carga posterior
  reemplazó **se quedan**: pertenecen a la otra carga.
- Dada una persona que **solo** estaba en esa carga, entonces su participación:
  - si fue **convocada**, vuelve a `respondio = false`;
  - si **no** fue convocada, se elimina.
- Dadas las personas que **esa carga creó** y no tienen ningún otro vínculo,
  entonces la vista previa **ofrece** darlas de baja. Es una **opción**, no un
  efecto automático.

> **Por qué dos reglas de borrado.** Borrar una **encuesta** responde a «estos datos
> no tienen que estar más»: las personas quedan. Borrar una **carga** responde a
> «esto lo cargué mal»: ahí sí tiene sentido ofrecer deshacer también a las personas
> que ese error creó.

### R-EU.11 — Composición por encuesta, con convocados frente a respondentes

- Dada la sección Composición, entonces se puede elegir el ámbito **una encuesta**.
- Dado ese ámbito, entonces muestra **lado a lado**:
  - la composición de los **convocados**;
  - la composición de los que **respondieron**;
  - y la **diferencia** por segmento, como **tasa de respuesta** de cada uno.
- [ ] Una encuesta sin audiencia (independiente de una carga suelta) muestra solo
      la de respondentes.

> Esa diferencia es el **sesgo de no respuesta** por segmento: dice a quién hay que
> sobreconvocar en el próximo estudio. Es probablemente lo más valioso de este
> spec para la operación de campo.

---

## 8. Requirements — P2 (para no cerrarles la puerta)

- **Mapeo de variables entre cargas** de una misma encuesta con códigos distintos
  (telefónico vs web). Que el diseño de R-EU.3 lo permita después.
- **Combinar varias consultas** o editar a mano la audiencia de una encuesta
  independiente.
- **Retención programada**: borrar automáticamente las respuestas de encuestas de
  cierto cliente después de un plazo.
- **Audiencia de una encuesta → panel**: convertir la audiencia de una encuesta
  independiente en un panel, si el grupo resultó valioso.

---

## 9. Cambios de esquema

**Bóveda**

| Cambio | Detalle |
|---|---|
| `encuesta.panel_id` | Admite nulo (encuestas independientes) |
| `encuesta.publico_objetivo` | Nueva |
| `encuesta.tipo` | `panel` \| `independiente` |
| `carga.encuesta_id` | Nueva, obligatoria después de migrar |
| `carga.archivo_huella` | Huella del archivo, para detectar duplicados |
| `carga.ref_estudio` | Deja de ser única: las cargas usan el de su encuesta |
| `carga.fecha_estudio`, `carga.publico_objetivo` | Pasan a la encuesta; se retiran después de migrar |
| `carga_cliente` → `encuesta_cliente` | Los clientes son del estudio |
| `carga_tematica`, `tematica_pregunta`, `persona_tematica` | Re-llaveadas por `encuesta_id` |
| `participacion.convocado` | `boolean`: falso para quien respondió sin estar en la audiencia |
| `persona_carga` | Sin cambios: el origen creado/reutilizado es de cada archivo |

> **Atención con una cascada existente:** hoy `encuesta.panel_id` es
> `on delete cascade` sobre `panel`. Con archivado en lugar de borrado no se
> dispara, pero conviene cambiarla a `on delete restrict` para que borrar un panel
> nunca se lleve encuestas por accidente.

**Store semántico**

| Cambio | Detalle |
|---|---|
| `respuesta.carga_id` | La última carga que escribió la respuesta (R-EU.10). Un entero, sin PII |

## 10. Success Metrics

**Indicadores inmediatos** (verificables con consultas, a la semana del despliegue)

- **100 %** de las cargas con encuesta, y **100 %** de las respuestas semánticas
  bajo el cuestionario de una encuesta.
- **0** duplicados: ninguna persona con dos respuestas a la misma pregunta dentro de
  una encuesta, ninguna con dos participaciones.
- **100 %** de las encuestas nuevas con temáticas.

**Indicadores de uso** (a los tres meses)

- Encuestas independientes creadas desde consultas: objetivo, que sea la forma
  habitual de hacer estudios a medida en lugar de crear paneles para eso.
- Cantidad de paneles creados «para un solo estudio»: debería bajar.
- Uso de la composición convocados vs respondentes al planificar convocatorias.

## 11. Open Questions

| Pregunta | Quién | ¿Bloquea? |
|---|---|---|
| Las participaciones que crea la migración para las **cargas viejas** van a contar para la **fatiga** de esas personas. ¿Es lo deseado? Lo propuesto es que sí: respondieron una encuesta. | Operaciones | No |
| Las respuestas de quien no dio `uso_semantico`: ¿se conservan para la **exportación del estudio**, fuera del store semántico, o no se guardan? | Protección de datos | Sí, para R-EU.6 |
| Archivos telefónico y web con **códigos distintos** para la misma pregunta: ¿qué tan frecuente es? Define si el mapeo de variables pasa de P2 a P1. | Campo / datos | No |
| ¿Quién puede **borrar una encuesta**? Propuesto: administración y protección de datos. | Stakeholder | No |

## 12. Entrega

**Todo el spec se entrega en una sola versión**: P0 y P1 juntos. P2 queda fuera.

Para que una sola entrega no concentre el riesgo en un único momento, el trabajo
se ordena por dentro y el despliegue tiene **puntos de control**: si uno no pasa,
no se sigue.

**Orden de implementación** (cada paso usa lo anterior):

1. Esquema nuevo y `respuesta.carga_id`.
2. Migración de cargas y encuestas existentes.
3. Flujo único de ingesta (R-EU.1 a R-EU.4, R-EU.6).
4. Encuestas independientes y composición convocados vs respondentes (R-EU.5,
   R-EU.11).
5. Archivado de paneles y borrados (R-EU.8 a R-EU.10).

**Puntos de control en el despliegue:**

| # | Antes de seguir, verificar | Si falla |
|---|---|---|
| 1 | Respaldo de las dos bases hecho y confirmado | No se empieza |
| 2 | La **corrida de prueba** de la migración informa lo que va a crear, y los números cierran (una encuesta por carga, una carga por encuesta con respuestas) | Se corrige la migración antes de escribir |
| 3 | Después de migrar: toda carga tiene encuesta, ninguna respuesta sin cuestionario, y los filtros de temáticas y clientes devuelven las mismas personas que antes | **Se restaura el respaldo**; no se despliega el código |
| 4 | `verificar_esquema.py` en verde para las dos bases | No se despliega el código |
| 5 | Una carga de prueba de punta a punta: preview, temáticas, participación, sin duplicados al cargarla dos veces | Se revierte el despliegue |
| 6 | Batería de COLOQUIO en verde (cambia `v_persona_convocable` y la audiencia de convocatoria) | Se revierte el despliegue |

> **La migración es el punto de no retorno.** Todo lo demás es código y se puede
> revertir con un despliegue anterior; la migración cambia datos. Por eso tiene
> dos controles propios (2 y 3) y el respaldo como red. El orden importante es:
> **la migración se aplica y se verifica antes de desplegar el código nuevo**, no
> al revés —es exactamente lo que falló con la `0010` de unidades de evidencia—.

**Dependencias:** la lista negra (R-LN) y los clientes (R-CLI) tienen que estar
desplegados antes, porque R-EU.5 y R-EU.2 los usan.

## 13. Costos

| Concepto | Costo |
|---|---|
| Esquema, migración e interfaz | **US$ 0** de infraestructura |
| Migración de cargas y encuestas existentes | **US$ 0**: no re-embebe ni llama a APIs |
| Temáticas por IA | **~US$ 0,02 por encuesta**. Baja respecto de hoy: se proponen una vez por encuesta, no una vez por archivo |
| Temáticas de encuestas viejas que nunca las tuvieron | ~US$ 0,02 cada una, a pedido |
| Borrados | Liberan espacio en el store semántico (Postgres lo reutiliza; lo devuelve al disco con `vacuum`) |
