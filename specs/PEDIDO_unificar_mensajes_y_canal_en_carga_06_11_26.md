# PEDIDO — Encuestas: mensajes, canales, selección aleatoria, audiencias y composición

**Sistema:** Gestión de paneles · encuestas y cargas
**Severidad:** **Alta** las partes 2 y 3 —sin la 2, la sección Mensajes no le
puede escribir a nadie que haya entrado por una carga; sin la 3, las audiencias
creadas desde una consulta demográfica salen por orden alfabético—; media la
parte 1
**Detectado:** 2026-11-06, al probar la sección Mensajes (R-CF)
**Entrega:** una sola versión

---

## Parte 1 · Unificar «WhatsApp Flow» en «Mensajes»

### Qué pasa

En el detalle de una encuesta conviven dos secciones que hacen lo mismo
—mandarle una plantilla de WhatsApp a la audiencia—:

| | WhatsApp Flow (R4.5) | Mensajes (R-CF) |
|---|---|---|
| Plantilla | Con botón de **Flow** | Con botón de **URL** |
| Mensajes por encuesta | Uno (la configuración) | Varios |
| Públicos | No tiene | Todos · No enviado aún · No contestaron · Respondieron |
| Registro de envíos | En `participacion` (`enviado_en`, `envio_estado`, `envio_error`): **uno por persona** | Tabla `envio`: **uno por persona y mensaje** |
| Conteo de convocados | Propio | El del modelo de encuestas |

La diferencia de conteo ya se ve: para la encuesta ANTEL, la sección Flow dice
**1.002 convocados** y el listado de encuestas dice **0**. Con cada cambio las dos
secciones se van a separar más.

### Qué se pide

**Una sola sección, «Mensajes»**, donde el tipo de botón es un atributo de la
plantilla y no una sección aparte.

- [ ] La lista de plantillas para un mensaje incluye las de **botón de URL** y las
      de **botón de Flow**, indicando cuál es cuál.
- [ ] Un mensaje con plantilla de **Flow** guarda su `flow_id` y su botón, y al
      enviarse usa `whatsapp.enviar_flow` con el `id_persona` como `flow_token`,
      igual que hoy.
- [ ] Un mensaje con plantilla de **URL** sigue funcionando como hasta ahora.
- [ ] La validación de la sección vieja —**Flow publicado y plantilla
      aprobada**— pasa a ser parte de editar y de enviar un mensaje de Flow.
- [ ] Los mensajes de Flow tienen **los mismos cuatro públicos**, la misma regla
      de elegibilidad (`preferencias.filtrar_contactables`) y el mismo registro en
      `envio`.
- [ ] Se quita la sección **«WhatsApp Flow»** del detalle de la encuesta, y sus
      rutas (`PUT/GET /encuestas/<id>/flow`, `POST /encuestas/<id>/whatsapp`)
      dejan de usarse desde la pantalla.
- [ ] **No se toca** lo que hoy recibe y procesa las respuestas de los Flows: el
      cambio es solo del lado del envío.

### Migración de lo ya enviado

Sin esto, «No enviado aún» le repetiría el Flow a quien ya lo recibió, porque esos
envíos están en `participacion` y no en `envio`.

- [ ] Por cada encuesta con un Flow configurado, crear un **mensaje** con esa
      plantilla y ese Flow, con un nombre que lo identifique (por ejemplo
      «WhatsApp Flow»).
- [ ] Por cada participación con `envio_estado = 'enviado'`, crear su fila en
      `envio` para ese mensaje, con la fecha de `enviado_en`; y con
      `envio_estado = 'fallido'`, una fila fallida con su `envio_error`.
- [ ] Dejar de escribir `participacion.enviado_en`, `envio_estado` y
      `envio_error`. **No borrar las columnas todavía**: se quitan en una versión
      posterior, una vez verificado que nada las lee.
- [ ] Corrida de prueba de la migración, que informe cuántos mensajes y envíos va
      a crear, antes de escribir.

---

## Parte 2 · La evidencia de canal en la carga

### Qué pasa

Para recibir un mensaje, una persona necesita dos cosas distintas (R4.4): el
**consentimiento de contacto** —por finalidad— y la **preferencia de canal**
—WhatsApp, correo—. La carga registra el consentimiento, pero **nunca registra el
canal**. La única forma de registrarlo hoy es de a una persona, desde su ficha, o
que el panelista lo acepte en la landing o el portal.

Resultado: **nadie que haya entrado por una carga puede recibir mensajes**, ni por
WhatsApp ni por correo. En la encuesta ANTEL, las 1.002 personas quedan fuera
con el motivo «no aceptó recibir WhatsApp».

### R-CAN.1 — Declarar los canales al cargar

- Dada una carga, entonces junto a la evidencia de consentimiento se declaran los
  **canales aceptados**: WhatsApp, correo, o ninguno.
- Dado cada canal declarado, entonces se elige **la versión del texto** con que
  lo aceptaron, de un desplegable con los textos publicados —como el
  consentimiento (R7.1)—. Sin texto, no se registra: «aceptó recibir WhatsApp» sin
  respaldo es una afirmación vacía.
- Dada la confirmación, entonces cada persona de la carga recibe el canal con
  `preferencias.otorgar(..., version_texto=…, origen='carga')`.
- Dada la **vista previa**, entonces muestra cuántas personas van a quedar con
  cada canal y cuántas no, **por motivo**: sin celular válido (WhatsApp), sin
  correo (correo), canal revocado antes (ver abajo).

### R-CAN.2 — Registrar los canales en bloque para una carga existente

- Dado el detalle de una carga ya hecha, entonces hay una acción **«Registrar
  canales aceptados»**: se eligen los canales y la versión del texto, se ve la
  vista previa y se confirma.
- Dada la confirmación, entonces se aplica a **todas las personas de esa carga**
  (`persona_carga`), creadas o reutilizadas.
- [ ] Queda **auditado**: quién lo hizo, cuándo, para qué carga, qué canales, con
      qué texto y a cuántas personas.
- [ ] Requiere el mismo permiso que gestionar canales (`gestionar_canales`).

### R-CAN.3 — Un canal revocado no se reactiva en bloque

`preferencias.otorgar` **reactiva** un canal que la persona había revocado («es la
misma persona diciendo que sí de nuevo»). Eso es correcto cuando la persona lo
acepta de nuevo en la landing o el portal. **No lo es en una declaración en
bloque**: alguien que pidió no recibir más WhatsApp volvería a recibirlo porque
una carga vieja decía que sí.

- [ ] En R-CAN.1 y R-CAN.2, una persona con el canal **revocado** no se toca: se
      informa en la vista previa y en el resultado como «revocó el canal».
- [ ] La reactivación sigue siendo posible de a una persona, desde su ficha, como
      hoy.

### Una pregunta que no es técnica

El texto de la carga ANTEL decía que el teléfono se usaría *«exclusivamente para
contactarnos contigo en caso que clasifiques para participar en este estudio o en
futuros estudios»*. Es claramente consentimiento de contacto. **Si alcanza como
aceptación de WhatsApp como canal** es una lectura que tiene que validar quien
asesora en protección de datos.

- [ ] La funcionalidad se construye igual; **usarla sobre cargas reales** queda
      sujeto a esa validación. Por eso exige elegir un texto: la decisión queda
      respaldada y auditada.

---

## Parte 3 · Selección aleatoria en las consultas

### Qué pasa hoy (verificado en el código)

**Consulta demográfica** —incluidos los filtros por temática y por cliente—: el
total del segmento se cuenta bien, pero las personas que se **muestran** salen de

```sql
... order by p.nombre nulls last limit %s offset %s
```

(`demografia.consultar`). Si se piden 50, se ven **las primeras 50 por orden
alfabético**.

**Crear una encuesta desde el resultado** toma **exactamente las personas
mostradas**: la pantalla envía `ultimoResultado.items` y
`encuestas.crear_desde_consulta` arma la audiencia con esos `id_persona`. Lo mismo
vale para crear un panel desde el resultado.

**Por qué es un problema de investigación, no de presentación.** El nombre está
asociado al **sexo** y a la **generación** —hay nombres típicos de cada década—.
Las primeras 50 personas por orden alfabético no representan al segmento, y ese
sesgo pasa directo a la audiencia de la encuesta.

**Consulta semántica:** las personas salen por **relevancia** —recuperación,
reordenamiento y verificación—. Es correcto cuando se busca a quienes mejor
cumplen el criterio, pero no hay forma de pedir una selección aleatoria entre
quienes lo cumplen.

### Qué se pide

#### R-ALE.1 — Consulta demográfica: selección aleatoria con semilla (P0)

- Dada una consulta **sin criterios semánticos** —demográfica, por temática, por
  cliente o combinadas—, entonces las personas que se muestran son una **selección
  aleatoria** del segmento, no las primeras por nombre.
- Dada la selección, entonces usa una **semilla**: el orden es
  `md5(id_persona::text || semilla)`, no `random()`.
- [ ] Con la misma semilla y los mismos datos, la selección es **idéntica**: se
      puede rehacer y auditar.
- [ ] La paginación es **estable**: con `random()` cada página saldría de un
      sorteo distinto y se repetirían o perderían personas; con la semilla, no.
- [ ] Cada ejecución genera una semilla nueva, que se **muestra** y se guarda en el
      diagnóstico de la ejecución (`consulta_ejecucion`).
- [ ] Botón **«Volver a sortear»**: misma consulta, semilla nueva.
- [ ] Una vez elegidas, las personas se pueden **ordenar en pantalla** por nombre u
      otra columna para leerlas cómodo. Ordenar la tabla **no cambia quiénes
      son**: el sorteo decide quiénes, el orden de la tabla es solo de lectura.

#### R-ALE.2 — Consulta semántica: ranking o aleatorio (P0)

- Dada una consulta con criterios semánticos, entonces el sistema **pregunta** el
  modo de selección:
  - **Por ranking**: los que mejor cumplen el criterio. Es lo de hoy.
  - **Aleatorio entre los que cumplen**: una selección al azar entre las personas
    que superan el umbral de confianza y la verificación.
- Dado el modo aleatorio, entonces funciona así:
  1. El **pool** son las personas cuya evidencia supera el **umbral de confianza**
     (el `umbral_distancia` de la consulta), recuperadas con el recall exacto.
  2. Se **sortean** con semilla, como en R-ALE.1.
  3. Se **verifican** las sorteadas, en el orden del sorteo.
  4. Quien no queda como `cumple` **se reemplaza** por la siguiente del sorteo,
     hasta llegar a la cantidad pedida o agotar el pool.
- [ ] El resultado informa: tamaño del pool, cuántas se sortearon, cuántas se
      verificaron, cuántas se reemplazaron y cuántas quedaron.
- [ ] Si el pool se agota antes de llegar a la cantidad pedida, se dice: «solo hay
      N personas que cumplen».
- [ ] Una consulta **combinada** —criterios demográficos y semánticos— usa el
      modo semántico elegido.
- [ ] Para consultas guardadas o ejecuciones completas (R-CS), el modo y la
      semilla se guardan con la ejecución.

> **Por qué sortear antes de verificar.** Verificar todo el pool y después sortear
> da el mismo resultado estadístico —una muestra al azar entre quienes cumplen—,
> pero verifica a muchas más personas de las que se van a usar. Sortear y
> verificar en ese orden, reemplazando a quien no cumple, verifica casi solo lo
> necesario.
>
> **El umbral importa más que antes.** Con un umbral muy laxo el pool se llena de
> personas que no van a cumplir y hay muchos reemplazos —más llamadas a Claude—;
> con uno muy estricto, el pool queda chico. Conviene mostrar el tamaño del pool
> antes de verificar.

#### R-ALE.3 — Crear una encuesta o un panel desde el resultado (P0)

- Dado «Crear encuesta con estos panelistas» o «Crear panel desde el resultado»,
  entonces la audiencia es **la selección vigente** —la sorteada, o la del ranking
  si se eligió ranking—, no las primeras por nombre.
- Dada una consulta **demográfica**, entonces se ofrece además crearla con **todo
  el segmento**, no solo con la cantidad mostrada.
- [ ] La encuesta guarda **cómo se armó su audiencia**: el modo (ranking,
      aleatorio, segmento completo) y la semilla. Si alguien pregunta por qué
      estas personas y no otras, la respuesta está registrada.

#### R-ALE.4 — Exportación (P1)

- [ ] Las exportaciones a CSV respetan la misma selección y el mismo orden que la
      pantalla.

### Lo que queda fuera

- **La sección Muestreo** no cambia. Su orden no es aleatorio por diseño: dentro
  de cada cuota prioriza a quien fue menos convocado (la rotación) y después a
  quien más respondió. Evaluar más adelante si el desempate final debería ser
  aleatorio en lugar de por `id_persona`.

---

## Parte 4 · Audiencias, listados y composición

### Problem Statement

Con el modelo nuevo, una encuesta independiente se puede crear solo desde el
resultado de una consulta, y una vez creada su audiencia no se puede ampliar con
otra consulta: hay que empezar de cero. La sección Encuestas solo ofrece «Nueva
encuesta de panel». Las listas de respondentes y de convocatoria traen a todas
las personas de una vez —más de mil en ANTEL— sin búsqueda ni filtros, y el
listado de encuestas no se puede filtrar por estado. En Composición, los cruces
se ven solo en cantidades, que no sirven para comparar grupos de distinto tamaño.

### Goals

1. **Armar la audiencia de una encuesta en pasos**: crearla vacía y sumar gente
   desde consultas o a mano, sin rehacerla.
2. **Que toda audiencia agregada desde una consulta sea una muestra aleatoria**
   reproducible, nunca un recorte alfabético.
3. **Encontrar a una persona en una encuesta de más de mil** en segundos.
4. **Leer los cruces de composición como se leen en investigación**: con
   porcentajes, su base y aviso de bases chicas.

### Non-Goals

- **Una consulta propia dentro de la encuesta.** Se reutiliza la sección
  Consultas; duplicarla dejaría dos buscadores que se separan con el tiempo.
- **Proponer temáticas con IA al crear la encuesta.** Salen de las preguntas en
  la primera carga; al crearla solo se pueden poner a mano.
- **Excluir por fatiga desde la consulta.** La fatiga ya se aplica al convocar y
  en Muestreo; un criterio de consulta por convocatorias recientes queda para
  después (P2).
- **Gráficos nuevos en Composición.** Solo cambia cómo se expresan los cruces.

### User Stories

**Coordinador de campo**
- Como coordinador, quiero crear una encuesta independiente vacía desde la
  sección Encuestas, para armar su audiencia de a partes.
- Como coordinador, quiero agregarle audiencia a una encuesta desde una consulta,
  sin que entren quienes ya estaban, para ampliar la convocatoria.
- Como coordinador, quiero que la gente que agrego desde una consulta sea una
  selección al azar entre quienes cumplen, para no sesgar la muestra.
- Como coordinador, quiero excluir de una consulta a quienes participaron en otra
  encuesta —por ejemplo, la ola anterior—, para no repetirles el estudio.
- Como coordinador, quiero buscar a un panelista en la lista de una encuesta y
  filtrar por si respondió o qué mensajes recibió, para resolver un caso puntual.
- Como coordinador, quiero filtrar las encuestas por estado y por cliente, para
  ver solo las que están en campo o las de un cliente.

**Analista**
- Como analista, quiero ver los cruces de composición en porcentaje de columna,
  de fila o del total, o en cantidad, para comparar grupos de distinto tamaño.
- Como analista, quiero ver la base de cada porcentaje y que se marquen las bases
  chicas, para no sacar conclusiones de cuatro casos.

### Requirements — P0

#### R-ENC.1 — Listas de respondentes y convocatoria: paginar, buscar, filtrar

- Dada la lista de **respondentes** o de **convocatoria** de una encuesta,
  entonces se muestra **de a 50**, paginada **en el servidor**, con «página N de
  M · T personas».
- Dada la lista, entonces se puede **buscar** por nombre o identificador.
- Dada la lista, entonces se puede **filtrar** por: respondió (sí / no),
  convocado (sí / no) y mensaje recibido (por cada mensaje de la encuesta).
- [ ] Los filtros y la búsqueda se mantienen al cambiar de página; cambiar un
      filtro vuelve a la primera.

**Criterios de aceptación**
- Given la encuesta ANTEL con 1.002 respondentes, When se abre su lista, Then se
  ven 50 y la indicación «página 1 de 21».
- Given un filtro «no respondió» y un mensaje «Invitación», When se combinan,
  Then se ven solo quienes no respondieron, con lo que recibieron de ese mensaje.

#### R-ENC.2 — Filtrar el listado de encuestas

- Dado el listado de encuestas, entonces además del tipo se puede filtrar por
  **estado** (borrador, en campo, cerrada) y por **cliente**.
- [ ] Los filtros se combinan entre sí.

#### R-COMP.1 — Cruces en cantidad o en porcentaje

- Dado un cruce en Composición —para cualquier ámbito: todos, panel, carga o
  encuesta, incluida la comparación de convocados y respondentes—, entonces se
  puede elegir cómo verlo:

| Opción | Qué muestra cada celda |
|---|---|
| **Cantidad (N)** | La cantidad de personas |
| **% de columna** | El porcentaje dentro de su columna |
| **% de fila** | El porcentaje dentro de su fila |
| **% del total** | El porcentaje sobre el total de la tabla |

- Dado un porcentaje, entonces se muestra **la base** sobre la que está
  calculado —el total de la columna, de la fila o de la tabla—.
- Dada una base **menor a 30**, entonces se **marca** como base chica. El umbral
  es configurable.
- [ ] La elección se mantiene al cambiar de cruce o de ámbito, dentro de la sesión.

**Criterios de aceptación**
- Given un cruce sexo × edad, When se elige «% de columna», Then cada columna suma
  100 % y debajo se ve su base.
- Given una columna con 12 personas, When se ve en porcentaje, Then su base
  aparece marcada como chica.

#### R-AU.1 — Criterio de consulta: participación en una encuesta

- Dada una consulta, entonces se puede agregar un criterio sobre una encuesta:
  - **está en la audiencia** de la encuesta X;
  - **respondió** la encuesta X;
  
  cada uno para **incluir** o **excluir**.
- [ ] Se resuelve en la bóveda, sobre `participacion`, como los demás criterios
      demográficos.
- [ ] Combinable con todo lo demás: demográficos, temáticas, clientes y
      semánticos.

#### R-AU.2 — Agregar audiencia desde una consulta

- Dada una encuesta **en borrador o en campo**, entonces hay una acción
  **«Agregar audiencia desde una consulta»**.
- Dada esa acción, entonces abre **la sección Consultas** con:
  - un aviso visible: *«Agregando audiencia a la encuesta X»*;
  - el criterio de R-AU.1 **ya puesto**: excluir a quienes **están en la
    audiencia** de esta encuesta.
- Dado el resultado, entonces el botón es **«Agregar a la encuesta X»**, con dos
  opciones:
  - **una cantidad**: se agregan N personas **sorteadas** con semilla, igual que
    R-ALE —en una consulta semántica, según el modo elegido: ranking o
    aleatorio—;
  - **todo el segmento** (consultas sin criterios semánticos).
- Dada la confirmación, entonces se agregan por **el mismo camino que convocar**
  —lista negra, consentimiento de contacto— y el resultado informa cuántas
  entraron y cuántas quedaron afuera, por motivo.
- [ ] Cada agregado queda **registrado** en la encuesta: quién, cuándo, la
      definición de la consulta, el modo, la semilla y cuántas personas. La
      audiencia final se puede reconstruir paso a paso.
- [ ] Una encuesta cerrada no ofrece la acción.

**Criterios de aceptación**
- Given una encuesta con 200 personas en la audiencia, When se agrega desde una
  consulta, Then ninguna de esas 200 aparece en el resultado.
- Given un segmento de 1.000 personas y una cantidad de 100, When se agrega, Then
  entran 100 sorteadas —no las 100 primeras por nombre— y se guarda la semilla.
- Given una persona en lista negra dentro del segmento, When se agrega, Then no
  entra y figura excluida.

#### R-AU.3 — Crear una encuesta desde la sección Encuestas

- Dada la sección Encuestas, entonces el botón es **«Nueva encuesta»**, con un
  **selector de tipo**: de panel o independiente.
- Dado el formulario, entonces pide **los mismos datos del estudio que la
  carga**: nombre, fecha de campo, público objetivo, clientes y URL de Alchemer;
  y el panel, si es de panel.
- [ ] Es **el mismo componente** de formulario que usa la carga, no uno parecido:
      si se suma un dato al estudio, aparece en los dos lados.
- [ ] Las **temáticas** se pueden agregar a mano al crearla; las de IA llegan con
      la primera carga.
- Dada una encuesta independiente recién creada, entonces nace **sin audiencia**,
  y se completa con R-CF.2 (a mano), R-AU.2 (desde una consulta) o con sus cargas.

### Requirements — P1

- [ ] **Exportar un cruce** de composición tal como se está viendo —cantidad o
      porcentaje, con sus bases—.

### Requirements — P2

- Criterio de consulta por **convocatorias recientes** («no convocado en los
  últimos N días»), para excluir por fatiga antes de agregar audiencia.

### Success Metrics

- **0 audiencias** armadas por orden alfabético después del despliegue
  (verificable: toda participación agregada desde una consulta tiene modo y
  semilla registrados).
- Encontrar a un panelista en una encuesta de más de mil personas en **menos de
  10 segundos** con la búsqueda.
- A los tres meses: las encuestas independientes se arman desde la sección
  Encuestas, sin crear paneles para eso.

### Open Questions

| Pregunta | Quién | ¿Bloquea? |
|---|---|---|
| ¿El umbral de base chica en 30 sirve para todos los estudios, o algunos usan otro (50, 100)? | Investigación | No: es configurable |

---

## Definition of Done

**Parte 1**
- [ ] Un mensaje con plantilla de Flow se crea, se valida (Flow publicado,
      plantilla aprobada) y se envía con los cuatro públicos (test).
- [ ] Ya no existe la sección «WhatsApp Flow» en el detalle de la encuesta.
- [ ] Después de migrar, «No enviado aún» sobre el mensaje de Flow **no incluye**
      a quienes ya lo habían recibido (test).
- [ ] El conteo de convocados es el mismo en el listado y en el detalle.
- [ ] El procesamiento de respuestas de Flow sigue funcionando igual (test de no
      regresión).

**Parte 2**
- [ ] Una carga con canales declarados deja a sus personas con esos canales y con
      el texto de evidencia (test).
- [ ] Una persona sin celular válido no recibe WhatsApp y figura en la vista previa
      (test).
- [ ] Una persona con el canal revocado no se reactiva en bloque (test).
- [ ] El registro en bloque sobre una carga existente queda auditado (test).
- [ ] Después de registrar WhatsApp para la carga de ANTEL, la sección Mensajes
      muestra a esas personas como elegibles.

**Parte 3**
- [ ] Una consulta demográfica con 1.000 personas en el segmento y 50 pedidas
      muestra 50 sorteadas, no las 50 primeras por nombre (test: el nombre de las
      elegidas no está concentrado en las primeras letras).
- [ ] Misma semilla, misma selección; semilla nueva, selección distinta (test).
- [ ] Dos páginas consecutivas no repiten personas (test).
- [ ] Ordenar la tabla por nombre no cambia quiénes están (test).
- [ ] En modo aleatorio semántico, las seleccionadas cumplen el criterio y el
      resultado informa pool, verificadas y reemplazadas (test con verificación
      simulada).
- [ ] Una encuesta creada desde una consulta tiene como audiencia la selección
      vigente, y guarda modo y semilla (test).
- [ ] Desde una consulta demográfica se puede crear la encuesta con el segmento
      completo (test).

**Parte 4**
- [ ] Las listas de respondentes y convocatoria paginan de a 50 en el servidor,
      con búsqueda y filtros (test).
- [ ] El listado de encuestas filtra por estado y por cliente (test).
- [ ] Los cruces se ven en cantidad, % de columna, % de fila y % del total, con
      bases y bases chicas marcadas (test).
- [ ] El criterio «está en la audiencia / respondió la encuesta X» incluye y
      excluye (test).
- [ ] Agregar audiencia desde una consulta excluye a quienes ya estaban, sortea
      con semilla, pasa por el camino de convocar y queda registrado (test).
- [ ] Se crea una encuesta independiente desde la sección Encuestas, con el
      mismo formulario que la carga (test).

## Costos

| Concepto | Costo |
|---|---|
| Partes 1 y 2 | **US$ 0**: interfaz, una migración de datos y reglas sobre tablas existentes |
| Lo que habilita la parte 2 | **Poder enviar** a esas personas: cada mensaje de WhatsApp se paga según la tarifa de Meta (del orden de US$ 0,07 en marketing para Uruguay; confirmar con la vigente) |
| Selección aleatoria demográfica | **US$ 0**: es una cláusula de ordenamiento |
| Semántica aleatoria | **Parecido al ranking**: se verifica la cantidad pedida más los reemplazos. Cada lote extra de 25 cuesta del orden de **US$ 0,03** (medido: ~2.500 tokens de entrada y ~1.200 de salida por lote) |
| Parte 4 (listas, filtros, cruces, audiencias, formulario) | **US$ 0**: interfaz y consultas sobre tablas existentes. Agregar audiencia con una consulta semántica cuesta lo mismo que esa consulta |
| Infraestructura | **US$ 0** |

## Despliegue

El documento de despliegue sigue las reglas del `CLAUDE.md` del repositorio:
proyecto de gcloud fijado y verificado al empezar, respaldo antes de la
migración —incluida la de la parte 3 si guarda modo y semilla en la encuesta—,
migración aplicada y `verificar_esquema.py` en verde **antes** del
`firebase deploy`, y token de COLOQUIO regenerado inmediatamente antes de la
batería.

**No incluye** dar de alta ningún webhook en Meta: la URL de callback de la app
ya la usa otro sistema.
