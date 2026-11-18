# SPEC — Alchemer, consulta por participación y bandeja de WhatsApp

**Sistema:** Gestión de paneles · Equipos Consultores
**Origen:** sesión de brainstorming, 2026-11-17
**Estado:** Para desarrollo · **una sola entrega**
**Se apoya en:** encuestas y cargas unificadas (R-EU), mensajes (R-CF), registro de
envíos (`envio.id_mensaje_meta`), carga mixta (R-MIX), historial por carga
(R-HIS), calidad del dato (Fase 8), temáticas (R-TEM)

---

## Contexto verificado en el código

- `encuesta.fecha_campo` **admite nulo** hoy.
- `envio` guarda `id_mensaje_meta` de cada mensaje enviado.
- Paneles envía los Flows con `flow_token = id_persona`.
- La planilla actual (Apps Script) **deduce** la encuesta del `flow_token`
  (`tpl=NOMBRE|…`, o lo anterior a `|`, o el token entero). Meta no envía el
  nombre de la plantilla en la respuesta. Por eso las respuestas a Flows enviados
  desde paneles —cuyo token es solo el `id_persona`— caen en una hoja por persona.

---

## Parte A · Alchemer al crear una encuesta

### Problem Statement

Hoy la URL de Alchemer se escribe a mano al crear una encuesta, sin forma de saber
si está bien ni si la encuesta de Alchemer captura el parámetro con el
`id_persona` —si no lo captura, las respuestas vuelven sin identificar y la carga
no reconoce a nadie—. Y las temáticas de un estudio recién se conocen con la
primera carga, cuando el campo ya terminó.

### Goals

1. Elegir la encuesta de Alchemer de una lista, sin copiar enlaces.
2. Saber **antes del campo** si la encuesta captura el `id_persona`.
3. Tener el cuestionario y sus temáticas **antes** de la primera carga.

### Non-Goals

- **Ingestar respuestas por la API.** A veces se trata la información a mano antes
  de cargarla; el SAV sigue siendo la entrada.
- **Crear o editar encuestas en Alchemer** desde paneles.
- **Escribir el cuestionario en el store semántico.** Los códigos de pregunta de la
  API pueden no coincidir con los nombres de variable del SAV; escribirlo ahí
  duplicaría preguntas en la primera carga.

### User Stories

- Como coordinador, quiero buscar y elegir la encuesta de Alchemer al crear una
  encuesta en paneles, para no copiar enlaces a mano.
- Como coordinador, quiero que el sistema me avise si la encuesta de Alchemer no
  captura el `id_persona`, para corregirla antes de convocar.
- Como analista, quiero ver el cuestionario y sus temáticas propuestas antes del
  campo, para planificar la convocatoria.

### Requirements — P0

#### R-ALC.1 — Credenciales

- [ ] Secretos `ALCHEMER_API_TOKEN` y `ALCHEMER_API_SECRET` en Secret Manager.
      Son secretos reales. **Nombres sin prefijos reservados** (`FIREBASE_`,
      `X_GOOGLE_`, `EXT_`, `KIT_`): con esos prefijos Firebase no los acepta.
- [ ] La región de la cuenta de Alchemer (endpoint de la API) es configurable.

#### R-ALC.2 — Elegir la encuesta

- Dado el formulario de una encuesta (crear o editar), entonces se puede **buscar**
  entre las encuestas de Alchemer por título y elegir una.
- Dada la elección, entonces se completan el **nombre** (editable) y la **URL**:
  el enlace de la encuesta más el parámetro del panelista (por defecto `?pid=`,
  configurable).
- Dada una encuesta con **más de un enlace** (campañas), entonces se elige cuál.
- [ ] La URL sigue validándose como hoy (https, sin espacios, termina en `=`).
- [ ] Se guarda el id de la encuesta de Alchemer junto a la encuesta.

#### R-ALC.3 — Verificar el parámetro

- Dada una encuesta de Alchemer elegida, entonces el sistema verifica que **capture
  la variable de URL** del parámetro (por ejemplo, `pid`), de modo que llegue a las
  respuestas exportadas.
- Dado que no la captura, entonces avisa: *«Esta encuesta no guarda el parámetro
  `pid`: las respuestas no van a traer el id_persona.»*
- [ ] La verificación se repite al pasar la encuesta a «en campo».

#### R-ALC.4 — Cuestionario y temáticas antes del campo

- Dada una encuesta vinculada a Alchemer, entonces se traen sus **preguntas y
  opciones** y se muestran en la ficha de la encuesta.
- Dado el cuestionario, entonces se pueden **proponer temáticas** con el mismo
  proceso de R-TEM (propuesta de IA, revisión del analista).
- [ ] El cuestionario se guarda como **referencia** de la encuesta, en la bóveda (no
      tiene datos personales), no como preguntas del store semántico.
- [ ] Botón **«Actualizar desde Alchemer»** por si la encuesta cambió.

**Criterios de aceptación**
- Given una encuesta de Alchemer sin la variable `pid`, When se vincula, Then se ve
  el aviso y la encuesta igual se guarda.
- Given una encuesta vinculada, When se pide proponer temáticas, Then la propuesta
  usa las preguntas traídas de Alchemer.

---

## Parte B · Consulta por historia de participación

### Problem Statement

No hay forma de elegir panelistas según su historia: quiénes no fueron convocados
hace tiempo (para cuidar la fatiga), quiénes fueron convocados y nunca responden
(panelistas inactivos), quiénes respondieron recientemente.

### Goals

1. Filtrar por convocatoria y respuesta en una ventana de tiempo, **sin tocar el
   store semántico**.
2. Combinarlo con cualquier otro criterio y con la selección aleatoria.

### Non-Goals

- Ponderar o puntuar a los panelistas por su historia (eso es Muestreo).

### User Stories

- Como coordinador, quiero los panelistas **no convocados** en los últimos 6
  meses, para no saturar siempre a los mismos.
- Como coordinador, quiero los que **respondieron** en el último año, para
  priorizar a los activos.
- Como responsable del panel, quiero los convocados que **nunca respondieron**,
  para depurar el panel.

### Requirements — P0

#### R-PAR.1 — Los criterios

- Dada una consulta, entonces se pueden agregar dos criterios, cada uno con **sí**
  o **no**:

| Criterio | Qué mira |
|---|---|
| **Convocado** en la ventana | Alguna participación con `convocado_en` dentro de la ventana. Incluye las personas que entraron por carga, aunque no hayan sido convocadas formalmente (decisión tomada). |
| **Respondió** en la ventana | Alguna participación con `respondio = true` en una encuesta cuya **fecha de campo** cae dentro de la ventana |

- Dada la ventana, entonces puede ser: **últimos N meses**, **entre dos fechas**, o
  **alguna vez** («nunca convocado», «nunca respondió»).
- [ ] Se resuelve solo en la bóveda.
- [ ] Combinable con todos los demás criterios y con la selección aleatoria
      (R-ALE).

#### R-PAR.2 — Fecha de campo obligatoria

- [ ] Los formularios de encuesta y de carga **exigen** la fecha de campo.
- [ ] Migración: las encuestas sin fecha de campo se completan con la fecha de su
      **primera carga** (o la de creación, si no tienen cargas), y se **listan** para
      que el equipo las revise.
- [ ] Después de completar, `encuesta.fecha_campo` pasa a `not null`.

**Criterios de aceptación**
- Given un panelista convocado hace 8 meses y no después, When se consulta
  «no convocado en los últimos 6 meses», Then aparece.
- Given una encuesta con fecha de campo hace 3 meses donde el panelista respondió,
  When se consulta «respondió en los últimos 6 meses», Then aparece.

### Requirements — P1

- **Contar veces**: «convocado **al menos N veces** en la ventana», «respondió
  menos de N veces». Permite «convocado 3 o más veces y nunca respondió».

---

## Parte C · La bandeja de WhatsApp en paneles

### Problem Statement

El webhook de Meta lo atiende hoy una planilla de Google con Apps Script. Además de
estar fuera del sistema, el código tiene problemas concretos:

- Las respuestas a Flows enviados desde paneles caen en **una hoja por persona**
  (el token es solo el `id_persona`).
- **No verifica la firma** de Meta: cualquiera que conozca la URL puede cargar
  respuestas falsas.
- **Responde «OK» aunque falle**: un error no se reintenta y la respuesta se pierde.
- Asocia los comentarios con una memoria de 7 días en las propiedades del script,
  que tienen un **tope de tamaño chico**: con volumen, deja de asociarlos sin avisar.
- **Ignora los estados de entrega**, por eso no se pudo implementar R-CF.7.

Y las respuestas de un Flow no se pueden ingestar: hay que pasarlas a mano a un
SAV.

### Goals

1. **No perder ninguna respuesta**: todo lo que manda Meta queda guardado, una sola
   vez, y verificado.
2. Ver las respuestas de cada Flow **tabuladas**, como hoy en la planilla, para las
   encuestas de paneles y las de afuera.
3. **Ingestar** las respuestas de un Flow con el mismo proceso que un SAV, con las
   mismas tuplas pregunta → respuesta.
4. Ver los **estados de entrega** de los mensajes.

### Non-Goals

- **Responder mensajes** desde paneles (no es una bandeja de atención).
- **Ingestar automáticamente**: cada ingesta la dispara una persona, como con los
  SAV.
- **Importar el histórico de la planilla** en esta versión (P2).

### User Stories

- Como coordinador, quiero ver las respuestas de un Flow en una tabla, como en la
  planilla, para seguir el campo.
- Como coordinador, quiero, desde la ficha de una encuesta, **obtener las
  respuestas de WhatsApp** y cargarlas, para no pasarlas a mano a un SAV.
- Como analista, quiero que las respuestas de WhatsApp queden con el texto de la
  pregunta y de la opción, igual que las de un SAV, para que la búsqueda semántica
  funcione.
- Como coordinador, quiero ver los comentarios que la gente deja después de un
  mensaje, asociados a su encuesta.
- Como coordinador, quiero saber si un mensaje se entregó o se leyó.
- Como responsable de datos, quiero que los teléfonos y respuestas de personas que
  no son panelistas tengan acceso restringido y un plazo de conservación.

### Requirements — P0

#### R-WA.1 — El webhook

- Dada la verificación de Meta (`GET` con `hub.verify_token`), entonces responde el
  `hub.challenge`.
- Dado un aviso (`POST`), entonces:
  - **verifica la firma** `X-Hub-Signature-256` con la clave secreta de la app; si no
    coincide, lo rechaza;
  - **guarda el aviso crudo** y recién entonces responde 200;
  - lo procesa en diferido.
- [ ] **Idempotente**: un mensaje o estado que Meta reenvía (por id) no se guarda dos
      veces.
- [ ] Si falla el procesamiento, el aviso crudo queda guardado y se puede
      reprocesar: **nunca se pierde**.
- [ ] Secretos `WHATSAPP_VERIFY_TOKEN` y `WHATSAPP_APP_SECRET`.

#### R-WA.2 — Tres tipos de aviso

| Tipo | Qué es | Qué hace paneles |
|---|---|---|
| **Respuesta de Flow** | `interactive.nfm_reply` | Guarda las respuestas y las asocia (R-WA.3) |
| **Comentario** | Texto o botón libre | Lo guarda y lo asocia al mensaje que contesta |
| **Estado** | Enviado, entregado, leído, fallido | Actualiza el envío (`envio.estado`): **cierra R-CF.7** |

#### R-WA.3 — A qué encuesta y persona pertenece

- Dada una respuesta o comentario con `context.id`, entonces se busca ese id en
  `envio.id_mensaje_meta`: si existe, es de **esa encuesta y esa persona**.
- Dado un `flow_token` que no coincide con el `id_persona` del envío, entonces se
  marca como inconsistencia.
- Dada una respuesta **sin** envío en paneles (encuesta de afuera), entonces se
  agrupa como hoy en la planilla: por el nombre que trae el token (`tpl=NOMBRE|…` o
  lo anterior a `|`), o en «GENERAL».

#### R-WA.4 — Sección «WhatsApp»

- Dada la sección, entonces lista los Flows con respuestas —de paneles y de
  afuera—, y para cada uno una **tabla**: fecha, teléfono, encuesta o plantilla,
  token, y **una columna por campo de la respuesta** (las de pantalla ordenadas
  como en la planilla).
- [ ] Filtros por encuesta o plantilla y por fecha; exportar a CSV.
- [ ] Una pestaña con los **comentarios** de cada encuesta o plantilla.
- [ ] Acceso restringido por rol; las exportaciones quedan auditadas.

#### R-WA.5 — Privacidad y conservación

- [ ] Todo lo de la bandeja vive en la **bóveda**, en tablas propias: tiene teléfonos.
- [ ] Las respuestas de **encuestas de afuera** —personas que no son panelistas— se
      conservan un plazo configurable y después se borran.
- [ ] El aviso crudo se conserva un plazo más corto que lo ya procesado.
- [ ] La **baja** de un panelista borra sus filas de la bandeja (por `id_persona` y
      por su teléfono).

#### R-WA.6 — Ingestar desde la ficha de la encuesta

- Dada una encuesta con respuestas de WhatsApp, entonces su ficha ofrece **«Obtener
  respuestas de WhatsApp»**, que muestra cuántas hay **nuevas desde la última
  carga**.
- Dada esa acción, entonces se arma el conjunto de datos y **sigue el mismo proceso
  que un SAV**: análisis, revisión, calidad del dato, temáticas, consentimiento y
  canales si hay personas nuevas, carga incremental e historial por carga.
- **Las tuplas pregunta → respuesta tienen que quedar como las de un SAV.** Para
  eso se baja de Meta la **definición del Flow** y se usa como diccionario:
  - el nombre de cada campo → el **texto de la pregunta** (con el contexto de su
    pantalla si hace falta);
  - el id de cada opción → su **etiqueta**;
  - una selección múltiple → una batería, tratada como hoy las del SAV;
  - un campo de texto → una pregunta abierta.
- Dado un campo que no se puede traducir (sin etiqueta en la definición), entonces
  se marca en la revisión como **sin etiqueta**, para corregirlo antes de ingestar.
- [ ] La identidad sale del **envío**: el `id_persona` de quien recibió el mensaje.

**Criterios de aceptación**
- Given una respuesta `screen_0_Radio_1: "0_Si"`, When se ingesta, Then el texto
  embebido es *«[texto de la pregunta] → Sí»*, como desde un SAV.
- Given 120 respuestas y una carga anterior con 80, When se obtienen, Then se
  informan 40 nuevas y las repetidas quedan como «repetida».

#### R-WA.7 — El cambio de la planilla a paneles

Meta admite **una sola** URL de callback por app: al cambiarla, la planilla deja de
recibir.

- [ ] Antes del cambio: webhook desplegado y probado con el envío de prueba de Meta.
- [ ] El cambio se hace en un momento **sin Flows en campo**, o aceptando que lo que
      llegue después va a paneles.
- [ ] Después: verificar que llegan los primeros avisos reales de los tres tipos.
- [ ] La planilla queda como **archivo histórico**, sin recibir más.
- [ ] Las encuestas de afuera mantienen su convención de token (`tpl=NOMBRE|…`)
      para seguir agrupándose.

### Requirements — P1

- **Ingestar una encuesta de afuera**: desde la bandeja, crear una encuesta
  independiente y cargar sus respuestas por el mismo proceso. La identidad es el
  **teléfono** (`from`), con las reglas de la carga mixta (R-MIX) y la evidencia de
  consentimiento para las personas nuevas.

### Requirements — P2

- Importar el histórico de la planilla a la bandeja.

---

## Success Metrics

**Inmediatos** (primer mes)
- **0 respuestas perdidas**: cada aviso que Meta entrega queda guardado
  (verificable comparando los avisos recibidos con los procesados).
- **100 %** de las respuestas a Flows enviados desde paneles asociadas a su
  encuesta y persona.
- **≥ 95 %** de los campos de cada Flow traducidos a pregunta y etiqueta.
- **100 %** de las encuestas con fecha de campo.

**De uso** (tres meses)
- Ingestas de WhatsApp hechas sin pasar por un SAV armado a mano: la mayoría.
- Encuestas creadas eligiendo la encuesta de Alchemer en lugar de pegar la URL.

## Open Questions

| Pregunta | Quién | ¿Bloquea? |
|---|---|---|
| ¿Cuánto tiempo se conservan las respuestas de **encuestas de afuera** y el aviso crudo? | Protección de datos | Bloquea el **valor por defecto** del plazo, no el desarrollo |
| ¿Cómo expone la API de Alchemer las variables de URL de una encuesta (R-ALC.3)? Puede requerir revisar sus acciones o preguntas ocultas. | Ingeniería | No |
| ¿Todos los Flows tienen las etiquetas en su definición, o alguno las trae de un servidor (pantallas con datos dinámicos)? Esos no se podrían traducir solos. | Ingeniería | No |
| ¿Qué plazo y qué límite de uso tiene el plan de Alchemer para la API? | Operaciones | No |

## Entrega

**Una sola versión.** Orden de implementación, de menor a mayor riesgo:

1. **Parte B**: criterios y fecha de campo obligatoria (con su migración).
2. **Parte A**: credenciales, elegir encuesta, verificar parámetro, cuestionario.
3. **Parte C**: webhook y bandeja, después la ingesta, y **al final el cambio de la
   URL en Meta**, que es el único paso que afecta al campo en curso.

**Puntos de control:**

| # | Verificar | Si falla |
|---|---|---|
| 1 | Respaldo de la bóveda; migración de fecha de campo con la lista de encuestas completadas | No se aplica |
| 2 | `verificar_esquema.py` en verde | No se despliega |
| 3 | Webhook desplegado responde la verificación de Meta y guarda un aviso de prueba firmado | No se cambia la URL en Meta |
| 4 | Un aviso con firma inválida se rechaza | No se cambia la URL en Meta |
| 5 | Después del cambio, llegan y se procesan una respuesta, un comentario y un estado reales | Se vuelve la URL a la planilla |
| 6 | Batería de COLOQUIO en verde | Se revierte el despliegue |

**Dependencias:** carga mixta (R-MIX) para la ingesta de encuestas de afuera;
historial por carga (R-HIS) para las cargas incrementales de WhatsApp. El documento
de despliegue sigue las reglas del `CLAUDE.md` del repositorio —proyecto de gcloud
fijado y verificado al empezar, migración antes del `firebase deploy`, token de
COLOQUIO regenerado inmediatamente antes de la batería—.

## Costos

| Concepto | Costo |
|---|---|
| API de Alchemer | **Incluida** en el plan que ya tienen |
| Temáticas propuestas desde el cuestionario de Alchemer | **~US$ 0,02 por encuesta**, una vez |
| Webhook de WhatsApp | **US$ 0**: entra en la capa gratuita de Cloud Functions (2 millones de invocaciones al mes) |
| Definición de los Flows (Graph API) | **US$ 0** |
| Secretos nuevos (4) | **Centavos por mes**: Secret Manager cobra unos US$ 0,06 por secreto activo fuera de los gratuitos |
| Almacenamiento de la bandeja | **Despreciable**: texto y JSON, con plazo de conservación |
| Consulta por participación | **US$ 0** |
