# SPEC — Convocatoria desde la encuesta: estado, panelistas a mano y mensajes

**Sistema:** Gestión de paneles · Equipos Consultores
**Origen:** sesión de brainstorming, 2026-11-05
**Estado:** Para desarrollo · **una sola entrega**
**Se apoya en:** encuestas y cargas unificadas (R-EU), integración de WhatsApp
existente (`whatsapp.py`, R4.5), correo por Workspace, lista negra (R-LN),
consentimiento por finalidad

---

## 1. Problem Statement

Hoy el sistema **registra** el trabajo de campo —a quién se convocó, quién
respondió— pero **no lo opera**. Las invitaciones y los recordatorios se mandan
por fuera, a mano, y el equipo tiene que reconstruir en otro lado a quién ya le
escribió y quién todavía no respondió. Además, no hay forma de sumar a una
encuesta a un panelista puntual sin rehacer la audiencia, y las encuestas no se
pueden abrir ni cerrar desde la pantalla: las creadas desde una carga aparecen
cerradas sin manera de cambiarlo.

El costo de no resolverlo: trabajo manual repetido en cada estudio, riesgo de
escribirle dos veces a la misma persona o de no recordarle a quien sí hacía
falta, y un registro de campo que no refleja lo que pasó.

## 2. Goals

1. **Lanzar y seguir el campo desde la encuesta**: invitar, recordar y agradecer
   por WhatsApp sin salir del sistema.
2. **Nunca escribirle dos veces el mismo mensaje a la misma persona** por error, y
   saber siempre a quién se le envió qué.
3. **Recordar solo a quien hace falta**: los que todavía no tienen respuestas
   ingestadas.
4. **Ajustar la audiencia sin rehacerla**: sumar panelistas de a uno o en lista.
5. **Que el estado de la encuesta refleje la realidad**, editable por el equipo.

## 3. Non-Goals

- **Saber en tiempo real quién respondió.** «No contestaron» se basa en las
  respuestas **ingestadas**. Recordar sobre la última carga es suficiente, y
  evita integrarse con la API de Alchemer.
- **Crear plantillas de WhatsApp desde el sistema.** Se crean y aprueban en Meta;
  el sistema las elige.
- **Mensajes de sesión («enviar ahora»).** Existe la opción en la cuenta, pero esta
  versión usa solo plantillas aprobadas.
- **Envíos programados o automáticos** (por ejemplo, un recordatorio a los tres
  días). Cada envío lo dispara una persona.
- **Crear panelistas al agregarlos a mano.** Un panelista nuevo necesita su
  consentimiento; para eso está la carga.

## 4. User Stories

**Coordinador de campo**
- Como coordinador, quiero elegir una plantilla de WhatsApp y enviarla a toda la
  audiencia de una encuesta, para lanzar el campo desde el sistema.
- Como coordinador, quiero enviar un mensaje solo a quienes todavía no lo
  recibieron, para sumar a los que agregué después sin repetirles a los demás.
- Como coordinador, quiero enviar un recordatorio a quienes todavía no tienen
  respuestas, para subir la tasa de respuesta sin molestar a quien ya contestó.
- Como coordinador, quiero enviar un mensaje a quienes respondieron, para
  agradecerles.
- Como coordinador, quiero ver antes de confirmar a cuántos les va a llegar y a
  quiénes se deja afuera y por qué, para no enviar a ciegas.
- Como coordinador, quiero agregar a la encuesta a un panelista por su id, correo
  o celular, o pegando una lista, para sumar gente puntual sin rehacer la
  audiencia.
- Como coordinador, quiero que se me avise si un dato no corresponde a ningún
  panelista, para corregirlo en vez de que se pierda en silencio.

**Analista**
- Como analista, quiero abrir, cerrar o reabrir una encuesta, para que su estado
  refleje cómo está el campo.
- Como analista, quiero ver para cada panelista qué mensajes se le enviaron y
  cuándo, para entender su participación.

**Responsable de datos**
- Como responsable de datos, quiero que nadie en lista negra o sin
  consentimiento de contacto reciba mensajes, aunque esté en la audiencia.

---

## 5. Requirements — P0

### R-CF.1 — Cambiar el estado de la encuesta

El servidor ya permite cualquier transición (`encuestas.cambiar_estado`); falta
el control en la pantalla.

- Dado el detalle de una encuesta, entonces hay un control para pasarla a
  **borrador**, **en campo** o **cerrada**, en cualquier sentido —incluido
  reabrir una cerrada—.
- Dado el listado, entonces el estado se puede cambiar también desde ahí.
- Se **mantienen** los efectos que el estado ya tiene, y se suma uno:

| Estado | Convocar / agregar panelistas | Enviar mensajes | Ingestar cargas |
|---|:--:|:--:|:--:|
| Borrador | Sí | **No** | Sí |
| En campo | Sí | Sí | Sí |
| Cerrada | **No** | **No** | Sí |

- [ ] Las encuestas creadas desde una carga siguen naciendo cerradas, pero ahora
      se pueden reabrir.
- [ ] El cambio de estado queda registrado (quién, cuándo, de qué a qué).

**Criterios de aceptación**
- Given una encuesta cerrada creada desde una carga, When el analista la pasa a
  en campo, Then queda en campo y admite convocatorias y envíos.
- Given una encuesta cerrada, When alguien intenta enviar un mensaje, Then el
  envío se rechaza indicando que la encuesta está cerrada.

### R-CF.2 — Agregar panelistas a mano

- Dada una encuesta de panel o independiente, entonces se puede agregar a la
  audiencia **un panelista** indicando su **id_persona**, **correo** o
  **celular**.
- Dada la misma pantalla, entonces se puede **pegar una lista** —uno por
  línea, con identificadores mezclados—.
- Dado cada dato, entonces se normaliza antes de buscar: correo en minúsculas,
  celular a formato E.164.
- Dado el resultado, entonces se informa **línea por línea**:

| Resultado | Qué pasa |
|---|---|
| **Agregado** | Entra a la audiencia |
| **Ya estaba** | No cambia nada |
| **No corresponde a ningún panelista** | **No se crea**; se avisa |
| **Ambiguo** | El celular corresponde a más de una persona; no se agrega |
| **En lista negra** | No se agrega |
| **Sin consentimiento de contacto** | No se agrega |
| **Formato inválido** | No se agrega |

- [ ] Agregar usa **el mismo camino que convocar** (`encuestas.convocar`), con
      sus mismos controles. No es un camino paralelo.
- [ ] Una encuesta cerrada no admite agregar panelistas.

### R-CF.3 — URL de la encuesta en Alchemer

- Dada una encuesta, entonces tiene su **URL de Alchemer**, incluido el nombre
  del parámetro que recibe el panelista —por ejemplo
  `https://survey.alchemer.com/s3/1234567/estudio?pid=`—.
- Dado que el parámetro es el **`id_persona`**, entonces al ingestar el SAV se
  usa el tipo de identificador **«Trae el id_persona del sistema»**, que ya
  existe.

> **Una verificación del lado de Alchemer.** El parámetro tiene que **llegar como
> columna al SAV exportado**. Según cómo esté armada la encuesta, Alchemer lo
> registra como variable de URL o hace falta una pregunta oculta que lo tome.
> Conviene probarlo con una respuesta de prueba antes de lanzar el primer campo.

### R-CF.4 — Mensajes de la encuesta

- Dada una encuesta, entonces se le pueden asociar **varios mensajes**. Cada uno
  tiene un **nombre libre** —«Invitación», «Recordatorio 2», lo que el equipo
  quiera— y una **plantilla de WhatsApp aprobada**, elegida de la lista de Meta.
- [ ] La lista de plantillas incluye las de **botón de URL**, no solo las de
      Flow (hoy `listar_plantillas` filtra por Flow por defecto).
- Dados los parámetros de la plantilla, entonces:
  - el **parámetro del botón de URL** recibe el **`id_persona`**;
  - los **parámetros del texto** se asignan desde un conjunto fijo: nombre del
    panelista, nombre de la encuesta.
- Dada una plantilla cuyo **botón de URL no coincide** con la URL de Alchemer de
  la encuesta, entonces se **avisa** antes de guardar: es la forma más fácil de
  mandar a la gente a la encuesta equivocada.
- Dada una plantilla **no aprobada** o pausada por Meta, entonces no se puede
  asociar ni enviar (se reutiliza la validación de `estado_flow`).

> **Una plantilla por encuesta.** Meta fija la base de la URL del botón al aprobar
> la plantilla; solo varía el final. Por eso cada encuesta de Alchemer necesita su
> propia plantilla. Es el diseño elegido: simple y sin intermediarios.

### R-CF.5 — Enviar un mensaje

- Dado un mensaje de una encuesta **en campo**, entonces se puede enviar a uno de
  cuatro públicos:

| Público | A quién |
|---|---|
| **Todos** | Toda la audiencia de la encuesta |
| **No enviado aún** | Quienes **todavía no recibieron este mensaje** —no se les envió—, sin importar los demás mensajes |
| **No contestaron** | Quienes **no tienen respuestas ingestadas** en la encuesta |
| **Respondieron** | Quienes **sí tienen respuestas ingestadas** |

- Dada la elección, entonces una **vista previa** muestra cuántos lo recibirán y
  cuántos quedan afuera, **por motivo**: en lista negra, sin consentimiento de
  contacto, sin celular válido.
- Dada la confirmación, entonces el envío corre **en diferido** (Cloud Tasks),
  con progreso visible, respetando el ritmo que admite la API de Meta.
- Dado el público **«Todos»** con personas que **ya recibieron** ese mensaje,
  entonces la vista previa lo advierte explícitamente: se lo van a recibir de
  nuevo.
- [ ] Un envío en curso no se puede lanzar dos veces para el mismo mensaje.

**Criterios de aceptación**
- Given 100 panelistas en la audiencia y un mensaje enviado a 60, When se envía
  con «No enviado aún», Then lo reciben los 40 restantes y ninguno de los 60.
- Given una carga ingestada con 30 respondentes, When se envía con «No
  contestaron», Then no lo recibe ninguno de esos 30.
- Given un panelista en lista negra dentro de la audiencia, When se envía con
  cualquier público, Then no lo recibe y figura excluido en la vista previa.

### R-CF.6 — Registro de envíos

- Dado cada envío, entonces se registra **por persona y por mensaje**: cuándo,
  quién lo lanzó, y el resultado de la API de Meta —**aceptado** (con el id del
  mensaje) o **fallido** (con el motivo)—.
- Dada la sección de convocatoria, entonces para cada panelista se ven **los
  mensajes que se le enviaron**.
- Dado el **primer mensaje** que recibe una persona en una encuesta, entonces su
  participación queda marcada como **convocada** con esa fecha. Los mensajes
  siguientes **no** cuentan como nuevas convocatorias, para no inflar la fatiga.

> «No enviado aún» se calcula sobre este registro. Un envío **fallido** cuenta
> como **no enviado**: el reintento lo vuelve a incluir.

---

## 6. Requirements — P1 (entran en esta misma versión)

### R-CF.7 — Estados de entrega

- Dado el webhook de estados de Meta, entonces cada envío se actualiza a
  **entregado**, **leído** o **fallido**.
- Dada la sección de convocatoria, entonces se ve ese estado por panelista.

### R-CF.8 — Correo como canal alternativo

- Dado un mensaje, entonces su canal puede ser **WhatsApp** o **correo**.
- Dado el canal correo, entonces el mensaje tiene **asunto y cuerpo** definidos en
  el sistema, con el enlace armado desde la URL de Alchemer más el `id_persona`.
- Se envía por la cuenta de **Google Workspace** que ya se usa, con los mismos
  cuatro públicos y el mismo registro.
- [ ] Respeta el canal de contacto que aceptó cada panelista.

---

## 7. Requirements — P2 (para no cerrarles la puerta)

- **Saber quién abrió el enlace**, con un enlace propio que redirija a Alchemer.
- **Código opaco por participación** en vez de `id_persona`, para que Alchemer no
  pueda vincular a la misma persona entre estudios.
- **Envíos programados** y recordatorios automáticos.
- **Mensajes de sesión** («enviar ahora»).
- **Saber en tiempo real quién respondió**, integrándose con Alchemer.

## 8. Cambios de esquema

**Bóveda**

| Cambio | Detalle |
|---|---|
| `encuesta.url_alchemer` | URL base con el nombre del parámetro |
| `encuesta_mensaje` | `id`, `encuesta_id`, `nombre`, `canal`, `plantilla` (nombre, idioma), mapeo de parámetros, `asunto` y `cuerpo` para correo |
| `envio` | `id`, `encuesta_mensaje_id`, `id_persona`, `lanzado_por`, `enviado_en`, `estado` (`aceptado`, `fallido`, `entregado`, `leido`), `id_mensaje_meta`, `error` |
| `encuesta_estado_cambio` | Registro de cambios de estado |

Ningún cambio en el store semántico.

## 9. Success Metrics

**Inmediatos** (a las dos semanas del primer campo lanzado desde el sistema)
- **0** casos de un mismo mensaje enviado dos veces a la misma persona con «No
  enviado aún».
- **≥ 95 %** de los envíos aceptados por Meta; los fallidos, con motivo legible.
- El primer campo completo —invitación, al menos un recordatorio, agradecimiento—
  lanzado **sin envíos por fuera** del sistema.

**De uso** (a los tres meses)
- Proporción de encuestas en campo cuyos mensajes salen del sistema: objetivo,
  la mayoría.
- **Tasa de respuesta tras un recordatorio**, comparada con la de la invitación:
  sirve para saber si los recordatorios rinden y cuándo mandarlos.

## 10. Open Questions

| Pregunta | Quién | ¿Bloquea? |
|---|---|---|
| ¿La integración actual ya tiene configurado el **webhook de estados** en Meta, o hay que darlo de alta? | Ingeniería | No (afecta R-CF.7) |
| ¿Cuál es el **límite diario de destinatarios** del número de WhatsApp hoy? Define si un envío a más de mil personas entra en un día. | Operaciones | No |
| ¿El `id_persona` llega como columna al SAV de Alchemer sin configuración extra, o hace falta una pregunta oculta? | Campo | Sí, para el primer campo real |
| ¿Qué variables de texto necesitan las plantillas, además del nombre del panelista y el de la encuesta? | Campo | No |

## 11. Entrega

**Todo en una sola versión**: P0 y P1. P2 queda fuera.

**Orden de implementación:**
1. Esquema nuevo.
2. Estado editable (R-CF.1).
3. URL de Alchemer y agregar panelistas a mano (R-CF.2, R-CF.3).
4. Mensajes, envío y registro (R-CF.4 a R-CF.6).
5. Estados de entrega y canal correo (R-CF.7, R-CF.8).

**Antes del primer envío real:** un envío de prueba a dos o tres números del
equipo, verificando que el botón abre la encuesta correcta con el `id_persona`, y
que una respuesta de prueba vuelve en el SAV con esa columna.

**Dependencias:** encuestas y cargas unificadas desplegado; y el documento de
despliegue sigue las reglas del `CLAUDE.md` del repositorio —proyecto de gcloud
fijado y verificado al empezar, token de COLOQUIO regenerado antes de la batería,
migraciones aplicadas y verificadas antes del `firebase deploy`—.

## 12. Costos

| Concepto | Costo |
|---|---|
| Mensajes de WhatsApp | **Por mensaje**, según la categoría que Meta asigne a la plantilla. Para Uruguay, del orden de **US$ 0,07 en marketing** y **US$ 0,01 en utilidad** (confirmar con la tarifa vigente). Una invitación suele quedar en marketing. |
| Ejemplo | 1.000 panelistas × invitación + recordatorio a la mitad + agradecimiento a 300 ≈ 1.800 mensajes ≈ **US$ 125 por estudio** en marketing |
| Correo por Workspace (R-CF.8) | **US$ 0**, hasta 2.000 destinatarios por día por cuenta |
| Envío diferido (Cloud Tasks) | **US$ 0** dentro de la capa gratuita |
| Esquema e interfaz | **US$ 0** de infraestructura |

> **Dónde está el ahorro posible.** Si en algún momento el costo de WhatsApp
> importa, el patrón más eficiente es **invitar por correo** y **recordar por
> WhatsApp** solo a quienes no respondieron: el grupo es mucho más chico y es
> donde WhatsApp rinde más. El diseño ya lo permite con R-CF.8.
