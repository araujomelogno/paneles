# SPEC — Fase 4: Profundización

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**PRD de referencia:** `PRD_sistema_paneles_unificado.md`
**Estado:** Borrador para desarrollo · cuarto sprint
**Precondición:** Fase 3 cerrada y desplegada
**Última actualización:** 2026-09-17

---

## 1. Problem Statement

Las tres fases anteriores construyeron un sistema que **registra, consulta y acciona**, pero que vive en el presente y depende de que alguien opere a mano lo que ya podría decidirse solo.

**El tiempo no se aprovecha.** El sistema acumula olas de la misma gente desde hace meses, pero no hay forma de ver cómo evolucionó una persona: si su postura cambió, si su situación se movió. Peor: los atributos demográficos guardan **un solo valor vigente**, así que cuando alguien pasa de un nivel educativo a otro, el anterior se pierde. El activo más valioso de un panel —seguir a la misma gente en el tiempo— está sin explotar y, en parte, destruyéndose.

**El muestreo decide por reglas simples donde hay una tensión real.** Cerrar una brecha de cuota y no quemar a los panelistas escasos tiran para lados opuestos. Las reglas de Fase 3 resuelven el caso fácil (excluir sobre-convocados, priorizar brechas), pero cuando no hay holgura eligen mal o no eligen: no saben negociar entre dos objetivos que compiten.

**La puerta de entrada está abierta sin control.** La landing pública funciona, pero sin verificación ni anti-fraude: cualquiera puede inscribir datos ajenos o inventados, y esas inscripciones llegan a la cola de aprobación como si fueran legítimas.

**Y convocar termina en una lista.** El contacto efectivo se hace fuera del sistema, aun cuando la encuesta es un Flow de WhatsApp que podría enviarse desde acá.

## 2. Goals

- **Explotar el tiempo**: ver la evolución de una persona a través de olas, y dejar de perder el historial de sus atributos.
- **Decidir el muestreo bajo tensión**: cerrar brecha de cuota respetando fatiga y equidad de rotación, y decir con claridad cuándo no se puede.
- **Cerrar la puerta de entrada**: verificar contacto y detectar fraude antes de que una inscripción llegue a la cola.
- **Contactar desde el sistema**, por el canal que cada persona aceptó.

## 3. Non-Goals

- **No se espejan segmentadores al store semántico.** Descartado en el PRD: el store semántico se mantiene como contenido puro y la consulta mixta se resuelve por puente.
- **No se reciben respuestas de WhatsApp.** El sistema solo envía; la ingesta sigue siendo por archivo.
- **No se canonizan respuestas abiertas** para hacerlas comparables entre olas: la comparabilidad la declara el analista (R4.1.b), no una capa automática.
- **No se rehace ninguna pantalla de fases anteriores**: se les agrega lo necesario conservando su comportamiento.
- **No se construye un motor de campo**: WhatsApp es un canal de envío, no una plataforma de recolección.
- No cambia el pipeline de consulta ni el modelo de embeddings.

## 4. Composición de la fase

Cinco requisitos en dos bloques, independientes entre sí.

| Bloque | Requisitos | Tema |
|---|---|---|
| **4A — Contacto** | R4.3, R4.4, R4.5 | Endurecer la entrada y contactar por el canal aceptado |
| **4B — Inteligencia** | R4.1, R4.2 | Explotar el tiempo y decidir bajo tensión |

**Dependencias internas.** R4.5 (envío) depende de R4.4 (preferencias): sin saber quién aceptó WhatsApp, no se puede enviar. R4.3 (verificación de celular) **fortalece** a R4.5 sin ser obligatorio: un número verificado mejora la entregabilidad y protege la reputación del número emisor. R4.1 necesita olas acumuladas; R4.2 necesita los umbrales de Fase 3 ya calibrados.

**Orden sugerido:** 4A completo (R4.4 → R4.5, con R4.3 en paralelo), después 4B. El bloque 4A tiene dependencias externas con demoras (aprobación de plantillas en Meta, revisión legal), así que conviene arrancar temprano aunque se desarrolle después.

---

## BLOQUE 4A — Contacto

### R4.3 — Endurecimiento de la landing (P0)

La landing de Fase 3 acepta cualquier envío. Esto le agrega verificación y control de fraude, **sin rehacerla**.

**Verificación de contacto**
- Dada una inscripción con celular, cuando se envía, entonces se verifica mediante un código de un solo uso antes de crear la inscripción.
- Dada una inscripción con email, entonces se verifica por enlace o código de un solo uso.
- Dado un contacto no verificado, entonces la inscripción **no llega a la cola de aprobación**.
- [ ] Los códigos vencen y el reintento está limitado.
- [ ] Un celular verificado queda marcado como tal: es lo que R4.5 necesita para enviar con confianza.

**Anti-fraude**
- Dada una tasa inusual de envíos desde el mismo origen, entonces se limita y se registra.
- Dado un envío automatizado, entonces se bloquea con un mecanismo de desafío.
- Dado un celular o email ya usado en otra inscripción, entonces se detecta antes de crear una nueva.

**Dedup en la aprobación**
- Dada una inscripción en la cola, entonces quien evalúa ve los **candidatos parecidos** que ya existen en la bóveda (por documento, email, celular, o nombre + fecha de nacimiento), para poder resolver sin buscar a mano.
- Dado un candidato con documento coincidente exacto, entonces se resuelve automáticamente reutilizando su `id_persona` (comportamiento actual de R1.2, sin cambios).
- Dada una coincidencia por email, celular o nombre+fecha, entonces **se propone, no se fusiona**: la decisión es de quien aprueba.

> **Por qué el dedup se refuerza acá y no en el alta.** La landing es el único camino donde la persona se inscribe sola, sin que nadie del equipo controle qué escribe. Es donde más probable es que alguien se anote dos veces, con datos levemente distintos.

### R4.4 — Preferencias de canal de contacto (P0)

Detalle completo en `SPEC_R4.4_R4.5_canal_whatsapp_flow.md`. Resumen:

Por persona y canal (`whatsapp`, `email`, `telefono`, `sms`) se registra si lo acepta, con evidencia de cuándo y cómo. Es un eje **distinto** del consentimiento por finalidad: `contacto_participacion` responde «¿puedo contactarla?», la preferencia responde «¿por dónde?». **Para contactar por un canal hacen falta los dos.**

Se captura en los tres caminos de alta ya construidos en Fase 3 —alta manual, ingesta con creación de individuos, landing— agregando la captura sin rehacerlos, con test de no regresión en cada uno.

- [ ] El celular se normaliza a E.164 en todos los caminos de alta.

### R4.5 — Envío de encuestas por WhatsApp Flow (P0)

Detalle completo en `SPEC_R4.4_R4.5_canal_whatsapp_flow.md`. Resumen:

Una encuesta puede configurarse con un Flow publicado y su plantilla aprobada; al convocar se ofrece enviarlo por WhatsApp a quienes cumplan los dos ejes y tengan celular válido. El sistema **solo envía**. Cada envío lleva el `id_persona` como `flow_token`, para que la ingesta posterior mapee directo.

---

## BLOQUE 4B — Inteligencia

### R4.1 — Análisis longitudinal (P0)

#### R4.1.a — Historial de atributos demográficos

Hoy `persona_atributo` guarda **un solo valor vigente** por atributo: cuando alguien cambia de nivel educativo o de ocupación, el valor anterior se sobrescribe y se pierde. Sin historial no hay longitudinal posible sobre segmentadores.

- Dado un cambio de valor en un atributo, entonces el valor anterior **se conserva** con su período de vigencia, en vez de perderse.
- Dada una consulta por un atributo **sin referencia temporal**, entonces usa el valor vigente (comportamiento actual, sin cambios).
- Dada una consulta con referencia a una fecha o a una ola, entonces usa el valor que estaba vigente en ese momento.
- Dada la composición de una ola pasada, entonces puede calcularse con los valores de entonces, no con los de hoy.
- [ ] Test de no regresión: las consultas y composiciones existentes devuelven lo mismo que antes.

> **Por qué importa más de lo que parece.** Sin historial, recalcular la composición de una ola de hace un año la calcula con la demografía de hoy, y el resultado es sencillamente incorrecto. Esto no es solo una feature longitudinal: es una corrección.

#### R4.1.b — Series comparables entre olas

Comparar «la misma pregunta» entre olas es difícil porque cada cuestionario la redacta distinto, y el sistema **no canoniza respuestas** por decisión de diseño. La salida es que la comparabilidad la declare el analista, explícitamente y por caso.

- Dado un conjunto de preguntas de distintas olas, cuando el analista las agrupa en una **serie**, entonces quedan declaradas como la misma medición a lo largo del tiempo.
- Dada una serie sobre preguntas cerradas, entonces se mapean sus opciones a un conjunto común de categorías, igual que en el catálogo de atributos.
- Dada una serie, entonces el sistema **sugiere** preguntas candidatas de otras olas por similitud semántica, pero **no las agrega sola**.
- [ ] Una serie es editable y auditable: se puede corregir sin rehacer las olas.

> **Por qué declarado y no automático.** Es la misma distinción de siempre: canonizar automáticamente congelaría una equivalencia que puede ser falsa (dos preguntas parecidas que miden cosas distintas). Declararla la hace explícita, revisable y responsabilidad de quien sabe.

#### R4.1.c — Vista longitudinal

- Dada una persona, entonces se ve su línea de tiempo: en qué olas participó, cuándo, y sus respuestas por ola.
- Dada una serie y un conjunto de personas, entonces se ve cómo se movieron entre categorías de una ola a la siguiente.
- Dada una persona presente en una sola ola, entonces se muestra como tal, sin inventar continuidad.
- [ ] Ver la línea de tiempo de una persona identificada es una **reidentificación**: se registra como tal (R3.10).

### R4.2 — Optimizador de muestreo (P0)

Las reglas de R3.1 priorizan brechas y excluyen sobre-convocados. Eso alcanza cuando hay holgura; cuando no la hay, hay que **negociar** entre cerrar cuota y cuidar a la gente.

**Qué optimiza**
- **Objetivo:** minimizar la distancia entre la composición de la muestra seleccionada y el objetivo de cuotas.
- **Restricciones duras:** consentimiento de finalidad vigente; preferencia del canal a usar; pertenencia al panel; tamaño de muestra pedido.
- **Restricciones blandas (penalizadas):** convocatorias recientes y acumuladas por persona; equidad de rotación (repartir entre elegibles en vez de recaer siempre en los mismos).

**Requisitos**
- Dado un objetivo de cuotas, un tamaño de muestra y los umbrales de fatiga, cuando se pide una selección, entonces se devuelve el conjunto que mejor cierra la brecha respetando las restricciones duras.
- Dada una selección, entonces cada individuo incluido es **explicable**: por qué segmento entró y qué peso tuvo.
- Dada una situación **infactible** (no se puede llenar la cuota sin violar fatiga), entonces el sistema **lo dice explícitamente** y ofrece las alternativas cuantificadas: reducir el tamaño, aflojar el umbral de fatiga, o aceptar la brecha. No elige por su cuenta.
- Dada una comparación con las reglas de R3.1, entonces se puede ver la diferencia entre ambas selecciones, para poder justificar el cambio de método.
- [ ] El optimizador **propone**; convocar sigue siendo una acción explícita de un responsable.
- [ ] Los pesos de las restricciones blandas son configurables, con default documentado.

> **Cuándo vale.** Con holgura, el optimizador y las reglas coinciden. La diferencia aparece justo donde duele: segmentos escasos y muy convocados. Por eso conviene mantener las reglas de R3.1 y no reemplazarlas: sirven de referencia y de respaldo.

---

## 5. Cambios de esquema

- **`preferencia_canal`** (R4.4): por persona y canal, con estado, evidencia y origen.
- **`encuesta`** (R4.5): configuración de Flow, nullable.
- **`participacion`** (R4.5): estado de envío por WhatsApp, nullable.
- **`persona_atributo`** (R4.1.a): historial. Agregar vigencia (`desde`, `hasta`) y conservar los valores anteriores, en vez de sobrescribir. La unicidad pasa a ser por persona, atributo **y** vigencia; el valor actual es el de vigencia abierta.
- **`serie`** y **`serie_pregunta`** (R4.1.b): agrupación declarada de preguntas de distintas olas, con su mapeo de categorías.
- **Verificaciones de contacto** (R4.3): registro de códigos emitidos, su estado y vencimiento.

Todas aditivas salvo el historial de atributos, que requiere migrar los valores existentes a una vigencia abierta. Sin cambios en el store semántico.

## 6. Dependencias

- **Fase 3 cerrada y desplegada.**
- **Olas acumuladas** de la misma gente: sin eso R4.1 no tiene qué mostrar.
- **Umbrales de fatiga y cuotas calibrados** en Fase 3: el optimizador optimiza contra ellos; si están mal, optimiza bien hacia el lugar equivocado.
- **Cuenta de WhatsApp Business verificada**, Flow publicado y plantilla aprobada (con demora de revisión de Meta).
- **Proveedor de envío de códigos** (SMS y/o email) para R4.3.
- **[legal]** Transferencia de celulares a Meta, y opt-in de WhatsApp para los panelistas ya enrolados.

## 7. Definition of Done

**Bloque 4A**
- [ ] Una inscripción sin verificar el contacto no llega a la cola de aprobación (test).
- [ ] Un envío automatizado se bloquea (test).
- [ ] Quien aprueba ve los candidatos parecidos; el documento exacto se resuelve solo y el resto se propone (test).
- [ ] Se registran preferencias de canal en los tres caminos de alta, y los tres siguen funcionando igual para quien no declara canales (test de no regresión).
- [ ] El envío por WhatsApp excluye a quien no cumple los dos ejes, informando por motivo (test de cada motivo).
- [ ] Cada envío lleva el `id_persona` como `flow_token` (test).

**Bloque 4B**
- [ ] Cambiar el valor de un atributo conserva el anterior con su vigencia (test).
- [ ] La composición de una ola pasada se calcula con los valores vigentes en ese momento (test).
- [ ] Las consultas y composiciones existentes devuelven lo mismo que antes del historial (test de no regresión).
- [ ] Una serie declarada permite ver el movimiento entre categorías de una ola a otra (test).
- [ ] El sistema sugiere preguntas candidatas para una serie pero no las agrega solo (test).
- [ ] Ver la línea de tiempo de una persona identificada queda registrado como reidentificación (test).
- [ ] Ante una cuota infactible, el optimizador lo informa con alternativas cuantificadas en vez de violar una restricción (test).
- [ ] Cada individuo seleccionado es explicable.

## 8. Success Metrics

**Leading**
- Proporción de inscripciones que superan la verificación (mide cuánto ruido estaba entrando).
- Duplicados detectados en la aprobación.
- Tasa de entrega y de apertura de los envíos por WhatsApp.
- Cierre de brecha del optimizador frente a las reglas, sobre las mismas olas.
- Incidencia de sobre-convocatoria con el optimizador (debería bajar a igual cierre de brecha).

**Lagging**
- Uso real del análisis longitudinal por parte de los analistas.
- Evolución del *quality rating* del número de WhatsApp (mide si el canal se está quemando).
- Churn del panel tras introducir el contacto por WhatsApp: puede mejorar por conveniencia o empeorar por intrusión. Vale medirlo.

## 9. Riesgos y preguntas abiertas

- **[cumplimiento Meta]** Sin canal de entrada no se procesan opt-outs: si alguien bloquea o responde «STOP», el sistema no se entera. Mitigación mínima: revocación manual de la preferencia y revisión periódica de los reportes de Meta. Si el volumen crece, recibir webhooks deja de ser opcional.
- **[operación]** El canal se quema: sobre-convocar por WhatsApp genera bloqueos, baja el *quality rating* y Meta puede restringir el número. Los umbrales de fatiga pasan a proteger también el canal, no solo al panelista.
- **[legal]** Enviar celulares a Meta es compartir datos personales con un tercero fuera del país: transferencia internacional y relación con encargado de tratamiento bajo URCDP. *Revisar antes del primer envío real.*
- **[legal]** Los panelistas ya enrolados **no tienen opt-in de WhatsApp**: nadie se lo pidió. Hay que obtenerlo antes de poder mandarles.
- **[datos]** El historial de atributos es una corrección, no solo una feature: hasta que exista, toda composición retroactiva está mal calculada. Conviene priorizarlo dentro del bloque.
- **[producto]** Las series comparables dependen del criterio del analista. Sin un dueño del vocabulario, cada uno arma las suyas y las comparaciones dejan de ser comparables entre equipos.
- **[producto]** El optimizador puede producir selecciones «óptimas» que un investigador rechazaría por razones que el modelo no conoce. Por eso propone y no convoca; conviene medir cuántas veces se corrige a mano, como señal de que falta una restricción.
- **[costos]** Los mensajes iniciados por el negocio se cobran por conversación. Estimar el costo por ola antes de abrirlo a todos los estudios.
