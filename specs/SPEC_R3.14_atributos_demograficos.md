# SPEC — R3.14: Atributos demográficos configurables

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Spec de referencia:** `specs/SPEC_fase3.md` · R3.9 y addenda (marcado de demográficas), R2.2–R2.5 (composición y consulta)
**Requisito nuevo:** R3.14
**Estado:** Para desarrollo
**Última actualización:** 2026-09-16

---

## 1. Problem Statement

La bóveda tiene una lista **fija** de campos demográficos: sexo, fecha de nacimiento y localidad. Son los únicos por los que se puede filtrar, segmentar y fijar cuotas. Cualquier otro segmentador habitual en investigación de mercado —nivel educativo, nivel socioeconómico, ocupación, composición del hogar, tenencia de bienes— hoy no tiene dónde guardarse: o se pierde, o termina embebido como una pregunta más en el store semántico, que es justamente lo que el marcado de demográficas evita.

Eso limita las tres cosas que dependen de segmentadores: los filtros demográficos de las consultas, la composición del panel contra su objetivo, y el muestreo por cuotas. Y agregar cada segmentador nuevo requiere hoy una migración de base de datos, o sea que depende de un ciclo de desarrollo.

## 2. Goals

- Que un administrador pueda **definir atributos demográficos nuevos** sin tocar código ni base.
- Que esos atributos tengan **categorías canónicas** estables, para que filtros y cuotas sean consistentes entre estudios.
- Que se puedan **cargar desde los archivos** con el mismo flujo de marcado que ya existe.
- Que sirvan igual que los campos fijos en **consultas demográficas, composición y cuotas**.
- Que un mapeo mal hecho sea **corregible sin recargar** el archivo original.
- Que **todos** los segmentadores vivan en un solo lugar: consultas, composición, cuotas y muestreo operan sobre el catálogo, sin distinguir entre demográficos «de fábrica» y definidos por el usuario.

## 3. Non-Goals

- **No migran al catálogo los datos de identidad y contacto** (documento, nombre, email, celular, contacto, observaciones): no son segmentadores. Tampoco la **fecha de nacimiento**, que es dato de identidad y llave del dedup (R1.2); de ella se deriva el tramo etario, que sí es un atributo del catálogo.
- **No se crean atributos al vuelo durante la carga.** El vocabulario lo define un admin; si alguien pudiera inventarlo al cargar, terminaríamos con veinte variantes de «nivel educativo» escritas distinto y las cuotas no cerrarían.
- **No se canonizan respuestas abiertas.** Esto aplica solo a segmentadores estructurados; el texto libre sigue interpretándose en consulta (*schema-on-read*).
- **No se espejan al store semántico**: como todo demográfico, quedan autoritativos en la bóveda.
- **No se borran atributos con datos cargados**: se desactivan.

> **Por qué acá sí canonizamos.** Es la misma distinción de diseño de todo el sistema: lo estructurado se consulta con SQL exacto y necesita categorías estables; lo semántico se interpreta en cada consulta. Canonizar texto libre congela errores en el dato; canonizar segmentadores es lo que hace que un filtro devuelva siempre lo mismo y que la aritmética de cuotas cierre. La salvaguarda contra el riesgo de congelar un error es guardar el **valor crudo junto al canónico** (R3.14.c).

## 4. User Stories

- Como admin, quiero definir un atributo demográfico nuevo con sus categorías, para poder segmentar por él sin pedir un desarrollo.
- Como analista, quiero mapear una variable del archivo a un atributo existente durante la carga, igual que hago con sexo o localidad.
- Como analista, quiero filtrar una consulta por nivel educativo o nivel socioeconómico.
- Como responsable de panel, quiero fijar cuotas y ver la brecha de composición por esos atributos.
- Como responsable de datos, quiero corregir un mapeo mal hecho sin volver a cargar el archivo.
- Como DPO, quiero que los atributos que son categorías especiales queden identificados y tratados como tales.

**Casos borde**
- Dos estudios que codifican el mismo atributo distinto (`1=Primaria…` vs `1=Bajo…`).
- Valor del archivo que no corresponde a ninguna categoría del atributo.
- Atributo que ya tiene datos cargados y se le quiere agregar o renombrar una categoría.
- Atributo definido por error, sin uso.
- Persona con un valor previo distinto al que trae un archivo nuevo.
- Atributo que corresponde a una categoría especial (religión, salud, ideología política, origen étnico).

## 5. Requirements

### R3.14.a — Catálogo de atributos (P0)

Pantalla de administración donde se definen los atributos demográficos.

- Dado un admin, cuando define un atributo, entonces indica: **clave** (identificador estable), **etiqueta** (nombre visible), **tipo** (`categorico` | `numerico` | `fecha` | `derivado`), descripción opcional, y si es **categoría especial** (ver R3.14.e).
- Dado un atributo `derivado`, entonces su valor se calcula a partir de otro dato de la persona y siempre está actualizado (el caso actual es **tramo etario**, calculado desde la fecha de nacimiento).
- Dado un atributo de tipo `categorico`, entonces se definen sus **categorías canónicas** (clave y etiqueta), que son las únicas admitidas como valor.
- Dada una clave de atributo o de categoría **ya usada por algún dato cargado**, entonces no se puede cambiar; sí se puede editar su etiqueta visible.
- Dado un atributo con datos cargados, cuando se lo quiere eliminar, entonces **no se borra**: se desactiva y deja de ofrecerse en cargas y filtros nuevos, conservando lo ya cargado.
- Dado un atributo sin datos, entonces sí puede eliminarse.
- [ ] Solo el rol `admin` administra el catálogo.
- [ ] Toda creación, edición y desactivación queda auditada con autor y fecha.

### R3.14.b — Valores por persona (P0)

- Dada una persona y un atributo, entonces puede tener **un solo valor vigente** para ese atributo.
- Dado un atributo `categorico`, entonces el valor es una de sus categorías canónicas; nunca texto libre.
- Dado un atributo `numerico` o `fecha`, entonces el valor se guarda con su tipo, para permitir rangos en los filtros.
- Dado cualquier valor, entonces se registra su **origen** (carga, ingesta desde encuesta, alta manual, inscripción) y la fecha de actualización.

### R3.14.c — Carga desde archivos (P0)

Extiende el marcado de demográficas del addendum R3.9.d, sin cambiar su lógica.

- Dada la pantalla de carga (tanto ingesta desde encuesta como «Cargar panelistas»), entonces el desplegable de campo demográfico lista los **campos fijos** más los **atributos activos** del catálogo.
- Dada una variable mapeada a un atributo `categorico`, entonces se mapean los valores del archivo a las categorías canónicas del atributo, igual que se hace con las etiquetas de una pregunta cerrada.
- Dado un valor del archivo que no corresponde a ninguna categoría, entonces **no se inventa una**: la fila queda sin ese atributo y se informa en el resultado, con el listado de valores no mapeados.
- Dado un valor cargado, entonces se guarda también el **valor crudo tal como vino del archivo**, junto al canónico.
- Dadas las reglas de escritura, entonces son las mismas del addendum R3.9.d: si el atributo está vacío se completa; si ya tiene un valor distinto **no se sobrescribe** y se informa la discrepancia.
- Dada una variable marcada como demográfica, entonces **no se ingesta como pregunta** en el store semántico (comportamiento ya existente).

> **Por qué se guarda el valor crudo.** Es la salvaguarda contra el riesgo que hizo descartar la canonización para las respuestas: si un mapeo salió mal, se corrige y se recalcula el valor canónico desde lo crudo, sin volver a pedir ni recargar el archivo original.

### R3.14.d — Uso en consultas, composición y cuotas (P0)

- Dada una consulta demográfica (R2.4), entonces se puede filtrar por atributos del catálogo, además de los campos fijos.
- Dada una consulta mixta (R2.5), entonces los atributos participan del filtro demográfico igual que los campos fijos, sin cambiar la estrategia del puente.
- Dado un objetivo de composición (R2.2), entonces se puede usar un atributo como **dimensión**; sus categorías canónicas son las categorías de la cuota.
- Dada la composición (R2.3), entonces muestra descriptivo y brecha por esas dimensiones igual que por las fijas.
- Dado el muestreo por reglas (R3.1), entonces puede priorizar brechas definidas sobre atributos del catálogo.
- [ ] Una persona sin valor para un atributo no cuenta como una categoría más: se informa aparte como «sin dato», para no inflar ninguna categoría ni distorsionar la brecha.

### R3.14.e — Categorías especiales (P0)

- Dada la definición de un atributo, entonces se declara si corresponde a una **categoría especial** de datos (salud, origen étnico o racial, convicciones religiosas o morales, afiliación sindical, ideología política, vida sexual).
- Dado un atributo marcado como especial, entonces al definirlo se advierte que su tratamiento exige consentimiento específico y protección reforzada bajo la Ley 18.331.
- Dado un atributo especial, entonces su valor **no se incluye** en exportaciones con datos (R3.10, R3.12.a) salvo decisión explícita y registrada.
- [ ] El acceso a atributos especiales se restringe por rol.
- [ ] Los atributos especiales se listan aparte en el catálogo, para que su existencia sea visible en una revisión de cumplimiento.

> **Por qué este requisito existe.** Un catálogo abierto permite definir «religión» o «afiliación política» como si fueran un segmentador cualquiera, y en investigación de mercado se preguntan seguido. Bajo URCDP son categorías especiales con exigencias propias. Sin este control, el sistema facilitaría almacenarlas sin que nadie lo note.

### R3.14.f — Unificación de los segmentadores existentes (P0)

Los segmentadores que hoy viven como columnas fijas de `persona` pasan al catálogo, de modo que consultas, composición, cuotas y muestreo operen sobre un único mecanismo.

- Dada la migración, entonces se crean en el catálogo los atributos **`sexo`** (categórico, con sus categorías actuales) y **`localidad`** (categórico), conservando las mismas claves que se usan hoy, para que los objetivos de composición ya cargados sigan resolviendo.
- Dada la migración, entonces se crea **`tramo_etario`** como atributo `derivado` de `persona.fecha_nacimiento`, reemplazando el cálculo de `v_demografia`.
- Dados los valores existentes en `persona.sexo` y `persona.localidad`, entonces se migran a `persona_atributo` conservando su valor crudo, sin pérdida.
- Dada la migración, entonces `persona.sexo` y `persona.localidad` quedan **obsoletas**: dejan de escribirse y de leerse. Se eliminan en una migración posterior, una vez verificado que nada las usa.
- Dada la vista `v_demografia`, entonces se reescribe sobre el catálogo **conservando su nombre y sus columnas**, para que el código que la consulta siga funcionando sin cambios durante la transición.
- Dado el muestreo (R3.1), entonces **se construye directamente sobre el catálogo**: al no estar implementado todavía, no hay reescritura que pagar.
- Dadas las consultas demográficas, la composición y las pantallas de alta, reidentificación y carga, entonces pasan a leer los segmentadores del catálogo.
- [ ] Test de no regresión: una consulta demográfica por sexo o localidad devuelve exactamente los mismos individuos antes y después de la migración.

> **Por qué unificar ahora y no después.** El muestreo (R3.1) todavía no existe: construirlo contra el catálogo no cuesta nada, mientras que construirlo contra los campos fijos crearía la deuda en el mismo momento de nacer. La composición y las consultas demográficas sí hay que tocarlas, pero es una vez y con test de no regresión, en vez de dos mecanismos que hay que recordar mantener en paralelo para siempre.

### R3.14.g — Edad sin fecha de nacimiento (P0)

Es habitual que una base traiga la **edad en años** y no la fecha de nacimiento. El tramo etario no puede depender solo de la fecha, o esas cargas quedarían sin dato de edad.

Precedencia, de más a menos preciso:

1. **Fecha de nacimiento** (en `persona`): el tramo se deriva de ella. Gana siempre que exista.
2. **Edad declarada con fecha de referencia**: si no hay fecha de nacimiento, se puede cargar la edad del archivo junto con la **fecha de referencia del trabajo de campo**; el tramo se calcula envejeciendo esa edad hasta hoy.
3. **Tramo cargado directamente**, sin referencia: último recurso, queda congelado.

- Dado un individuo con fecha de nacimiento, entonces el tramo se deriva de ella y **ningún valor cargado la reemplaza**.
- Dada una carga con una variable de edad mapeada, y sin fecha de nacimiento en la bóveda, entonces se guarda la edad declarada junto a la fecha de referencia de la carga.
- Dada una edad declarada con fecha de referencia, entonces el tramo se calcula envejeciéndola hasta la fecha de consulta, no se congela.
- Dado que después se obtiene la fecha de nacimiento de esa persona, entonces el tramo pasa a derivarse de ella automáticamente, sin recargar nada.
- Dado un individuo sin ninguno de los tres, entonces el tramo queda **sin dato**: no se cuenta en ninguna categoría de edad, no infla cuotas y el muestreo no puede usarlo para cerrar una brecha etaria.
- [ ] La ficha del individuo muestra de dónde sale su tramo (derivado, envejecido o cargado), para que la precisión del dato sea visible.

> **Por qué envejecer y no congelar.** Un panel vive años. Alguien cargado como «25-34» en 2024 puede estar hoy en otro tramo, y con el valor congelado nadie se entera: las cuotas se calculan sobre una edad que ya no es. Guardar la edad con su fecha de referencia cuesta una columna y mantiene el dato vivo.

### R3.14.h — Corrección de mapeos (P1)

- Dado un atributo con valores cargados desde un mapeo erróneo, cuando se corrige el mapeo, entonces se pueden recalcular los valores canónicos a partir de los valores crudos guardados.
- Dada esa corrección, entonces queda registrada con autor, fecha y cantidad de valores afectados.

## 6. Cambios de esquema

En la bóveda, tres tablas nuevas:

- **`atributo_demografico`**: `id`, `clave` (única), `etiqueta`, `tipo` (`categorico`|`numerico`|`fecha`), `descripcion`, `es_especial boolean not null default false`, `activo boolean not null default true`, `orden`, `creado_en`.
- **`atributo_categoria`**: `id`, `atributo_id`, `clave`, `etiqueta`, `orden`; única por `(atributo_id, clave)`. Solo para atributos `categorico`.
- **`persona_atributo`**: `id`, `id_persona`, `atributo_id`, `categoria_id` (nullable), `valor_num` (nullable), `valor_fecha` (nullable), `valor_crudo text`, `origen text`, `fecha_referencia date` (nullable, para valores que envejecen como la edad declarada), `actualizado_en`; única por `(id_persona, atributo_id)`.

En `persona`: `sexo` y `localidad` quedan obsoletas tras la migración de datos (se eliminan en una migración posterior, no en esta). `fecha_nacimiento` **se conserva**: es dato de identidad y llave del dedup.

`v_demografia` se reescribe sobre el catálogo conservando nombre y columnas.

Sin cambios en `objetivo_composicion` (su `dimension` ya es texto libre y admite la clave de un atributo) ni en el store semántico.

La migración tiene dos partes: **aditiva** (tablas nuevas, aplicable con la app andando) y **de datos** (sembrar `sexo`, `localidad` y `tramo_etario` en el catálogo, y copiar los valores existentes). La eliminación de las columnas obsoletas queda para una migración posterior, una vez verificado que nada las usa.

## 7. Contratos de API (propuestos)

- `GET /atributos` · `POST /atributos` · `PATCH /atributos/{id}` · `DELETE /atributos/{id}` (solo sin datos)
- `POST /atributos/{id}/categorias` · `PATCH /atributos/{id}/categorias/{cat_id}`
- `GET /panelistas/{id_persona}/atributos` · `PUT /panelistas/{id_persona}/atributos/{atributo_id}`
- `POST /atributos/{id}/recalcular` (R3.14.f)
- Los contratos de carga y de consulta **no cambian de forma**: los atributos entran como campos demográficos más.

## 8. Definition of Done

- [ ] Un admin define un atributo categórico con sus categorías y aparece disponible en la carga.
- [ ] Un no-admin no puede administrar el catálogo (test).
- [ ] Una carga mapea una variable a un atributo y guarda el valor canónico **y** el crudo (test).
- [ ] Un valor del archivo sin categoría correspondiente no se inventa y se informa (test).
- [ ] Un valor existente distinto no se sobrescribe y se informa la discrepancia (test).
- [ ] Una consulta demográfica filtra por un atributo del catálogo (test).
- [ ] Se puede fijar una cuota por un atributo y ver su brecha en la composición.
- [ ] Las personas sin valor se informan como «sin dato» y no se cuentan dentro de ninguna categoría (test).
- [ ] Un atributo con datos no se puede eliminar, solo desactivar (test).
- [ ] Un atributo marcado como especial queda excluido de las exportaciones con datos por defecto (test).
- [ ] Ningún valor de atributo llega al store semántico (test del guardrail).
- [ ] Un individuo con fecha de nacimiento deriva su tramo de ella, aunque el archivo traiga una edad distinta (test).
- [ ] Una edad declarada con fecha de referencia produce el tramo correcto al envejecerla (test con fecha de referencia vieja).
- [ ] Un individuo sin fecha, sin edad y sin tramo queda «sin dato» y no se cuenta en ninguna categoría etaria (test).
- [ ] Tras la migración, `sexo`, `localidad` y `tramo_etario` existen en el catálogo y sus valores están completos (test).
- [ ] Una consulta demográfica por sexo o localidad devuelve exactamente los mismos individuos antes y después de la migración (test de no regresión).
- [ ] Los objetivos de composición ya cargados siguen resolviendo contra las mismas claves (test).
- [ ] `v_demografia` conserva nombre y columnas, ahora alimentada por el catálogo (test).
- [ ] El muestreo (R3.1) opera sobre el catálogo, sin referencias a columnas fijas.
- [ ] Las cargas y consultas existentes siguen funcionando sin cambios (test de no regresión).

## 9. Riesgos y preguntas abiertas

- **[legal]** ¿Se van a definir atributos que sean categorías especiales? Si la respuesta es sí, el consentimiento actual —el del alta y el de la landing— probablemente no alcance: esas categorías requieren consentimiento específico. *Definir antes de habilitar el marcado de «especial».*
- **[privacidad]** Cada atributo nuevo es un cuasi-identificador más: aumenta el riesgo de reidentificación por combinación, aun quedando del lado bóveda. Conviene un criterio sobre cuántos y cuáles, no agregar por si acaso.
- **[producto]** Cambiar categorías de un atributo ya usado en cuotas históricas rompe la comparabilidad entre olas. ¿Hace falta versionar el conjunto de categorías, o alcanza con no permitir cambios de clave?
- **[ingeniería]** La migración toca código que ya funciona (consultas demográficas, composición, reidentificación, pantallas de alta). El test de no regresión sobre sexo y localidad es lo que separa una unificación limpia de una que rompe en silencio; conviene escribirlo **antes** de migrar.
- **[producto]** ¿Quién es responsable de mantener el vocabulario? Sin un dueño claro, el catálogo se llena de atributos parecidos y vuelve el problema que este diseño evita.
- **[datos]** Atributos que cambian con el tiempo (nivel educativo, ocupación, ingresos): hoy se guarda un solo valor vigente. Si interesa la evolución, hace falta historial, y eso se cruza con el análisis longitudinal de Fase 4.
