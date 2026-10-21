# SPEC — Clientes por carga, lista negra, y mejoras de panelistas, cargas y auditoría

**Sistema:** Gestión de paneles
**Se apoya en:** vínculo persona ↔ carga (R-ORG), temáticas (R-TEM), reidentificación
(R3.10), superficie externa de COLOQUIO (fase 5)
**Estado:** Para desarrollo
**Fecha:** 2026-10-21

---

## Resumen

| # | Cambio | Tipo |
|---|---|---|
| 1 | Clientes asociados a cada carga, y a sus panelistas | Nuevo |
| 2 | La sección de panelistas no pagina | **Error** (solo interfaz) |
| 3 | Lista negra | Nuevo |
| 4 | Desglose de «reutilizados» en la sección Cargas | Nuevo |
| 5 | Cargas de cada panelista en su ficha | Nuevo |
| 6 | «Ver quiénes son» no aparece en la auditoría | **Error** (solo interfaz) |

---

## 1. Clientes de una carga

### R-CLI.1 — Clientes asociados a la carga (P0)

- Dada una carga, entonces tiene una lista de **clientes** relacionados,
  multivaluada y **abierta**: se agregan como etiquetas, se reutilizan los
  existentes con autocompletado, y se pueden crear nuevos.
- Dado el alta de una carga, entonces los clientes se pueden indicar **en ese
  momento**, junto al nombre, la fecha y el público objetivo.
- Dada una carga existente, entonces sus clientes son **editables** desde la
  sección Cargas.
- [ ] El cliente es **una entidad propia**, compartida entre cargas, con el
      nombre normalizado —igual que las temáticas—, para que «Antel», «ANTEL» y
      «antel» sean el mismo.

### R-CLI.2 — Los clientes del panelista (P0)

- Dado un panelista, entonces tiene como clientes **los de todas las cargas en
  las que aparece**.
- [ ] Se **deriva** del vínculo `persona_carga`, no se copia en cada persona.

> **Por qué acá sí se hereda de la carga, y con las temáticas no.** Una temática
> se asigna a la persona solo si **respondió** sobre ese tema, porque estar en
> una carga no garantiza haber contestado esas preguntas. El cliente es
> distinto: si una persona estuvo en una carga hecha para Antel, **participó en
> un estudio para Antel**, contestara lo que contestara. Heredar es lo correcto.
>
> Y derivarlo en vez de copiarlo evita que, al corregir los clientes de una
> carga, haya que salir a actualizar a cada uno de sus panelistas.

### R-CLI.3 — Consultar por cliente (P0)

- Dada la consulta demográfica, entonces se puede filtrar por cliente en los
  **dos sentidos**:
  - **incluir**: «panelistas que participaron en estudios para *X*»;
  - **excluir**: «panelistas que **no** participaron en estudios para *X*».
- Dados los resultados de una consulta, entonces los clientes se pueden agregar
  como columna (R7.4).

> **El filtro de exclusión es probablemente el más usado.** En la práctica de
> paneles es habitual no convocar, para un estudio de un cliente, a quienes
> participaron recientemente en uno de su competencia o del mismo cliente.
>
> - [ ] Evaluar, como mejora posterior, combinarlo con la **fecha del estudio**:
>       «que no hayan participado para *X* en los últimos seis meses».

### R-CLI.4 — Confidencialidad del cliente (P0)

El nombre del cliente es información comercial confidencial, y en investigación
de mercado es habitual que el respondente **no sepa** para quién es el estudio.

- [ ] El cliente **nunca** se muestra en el **portal del panelista**.
- [ ] El cliente **nunca** forma parte de la **superficie externa** (vistas y
      funciones que consume COLOQUIO).
- [ ] No se incluye por defecto en las **exportaciones** de resultados.

---

## 2. La sección de panelistas no pagina

### Causa (verificada)

El servidor **ya pagina**: `GET /panelistas` acepta `limite` (50 por defecto,
200 como máximo) y `desde`, y la respuesta trae el `total`. La pantalla
**nunca pide la página siguiente**: muestra los primeros 50 y ahí termina.

### R-PAN.1 — Paginar la grilla (P0)

- [ ] Controles de **página anterior y siguiente**, y la indicación «página N de
      M · T panelistas», usando el `total` que ya devuelve el servidor.
- [ ] Los **filtros activos** (búsqueda, panel, sin panel, carga) se mantienen
      al cambiar de página.
- [ ] Cambiar un filtro **vuelve a la primera página**.
- [ ] Opcional: elegir cuántos por página (25 / 50 / 100).

Solo interfaz; el servidor no cambia.

---

## 3. Lista negra

### R-LN.1 — Agregar a la lista negra (P0)

- Dada la **ficha de un panelista** —en la sección de panelistas o abierta desde
  el resultado de una consulta—, entonces hay una acción **«Agregar a la lista
  negra»**.
- Dado ese pedido, entonces el **motivo es obligatorio**: una categoría (calidad
  de respuestas, fraude o duplicación, comportamiento, otro) y un detalle breve.
- [ ] Queda registrado quién lo agregó y cuándo.
- [ ] Requiere permiso de operaciones o administración.

> **Sobre el texto del motivo.** Lo van a leer otras personas del equipo, y es
> un dato sobre alguien identificado. Conviene que la pantalla pida describir
> **hechos** («respondió en 40 segundos un cuestionario de 15 minutos») y no
> juicios sobre la persona.

### R-LN.2 — Qué efecto tiene (P0)

| Dónde | Efecto |
|---|---|
| **Resultados de consulta** | Aparecen **diferenciados**: marcados como en lista negra, con el motivo a la vista para quien tenga permiso. No se ocultan. |
| **Crear panel desde una consulta** | Se **excluyen por defecto**, y se avisa cuántos quedaron afuera. |
| **Encuestas** | **No se los convoca**: quedan fuera de las invitaciones automáticamente, y el resumen de la convocatoria dice cuántos se excluyeron por estar en lista negra. |
| **COLOQUIO** | **No aparecen** en `v_persona_convocable`. |
| **Landing** | Si alguien en lista negra se vuelve a inscribir, el dedup lo reconoce y la inscripción va a **revisión**, marcada. |

- [ ] Una persona que entra en la lista negra **con invitaciones pendientes** a
      una encuesta: esas invitaciones se **cancelan**.

> **COLOQUIO no estaba en el pedido, y es imprescindible.** El pedido dice que en
> la sección de encuestas no se los convoca. Pero COLOQUIO convoca por su cuenta,
> leyendo `v_persona_convocable`. Si la lista negra no llega a esa vista, una
> persona excluida de las encuestas puede igual ser invitada a un focus group.

### R-LN.3 — Sección «Lista negra» (P0)

- [ ] Lista de quienes están en la lista negra: nombre, motivo, quién lo agregó,
      cuándo. Paginada, con búsqueda.
- [ ] Acción **«Sacar de la lista negra»**, con un motivo de la remoción.
- [ ] Sacar a alguien **no borra el historial**: queda registrado que estuvo, por
      qué, quién lo sacó y por qué. Si vuelve a entrar, se ve la historia
      completa.

### R-LN.4 — Privacidad y relación con la baja (P1)

- [ ] El panelista **no recibe ningún aviso** de que está en la lista negra, ni
      lo ve en su portal.
- [ ] La lista negra es un **dato sobre una persona identificada**: entra en el
      derecho de acceso. Si un panelista pide sus datos, este registro también
      corresponde.

> **Pregunta abierta para resolver con quien asesora en protección de datos.** Si
> alguien en lista negra pide la **baja**, sus datos se borran y con ellos el
> registro de la lista negra. Si después se vuelve a inscribir, el sistema no lo
> reconoce. Impedirlo requeriría conservar algún identificador —por ejemplo, una
> huella del documento o del celular— **después** de la baja, lo cual tiene que
> justificarse legalmente. Hasta decidirlo, la baja borra también la entrada en
> la lista negra.

---

## 4. Desglose de los reutilizados de una carga

### R-CAR.3 — De qué otras cargas vienen (P0)

En la sección Cargas, una fila como:

> Centel · 04/06/2026 · Total país, todas las edades, ambos sexos · **487**
> 339 creados · **148 reutilizados**

- Dado un clic en **«reutilizados»**, entonces se abre un modal con el desglose:
  para cada **otra carga** en la que aparecen esas personas, cuántas son.
- [ ] Ordenado de mayor a menor, con el nombre y la fecha de cada carga.
- [ ] Cada fila del desglose lleva al detalle de esa carga.

**Dos detalles para que los números cierren:**

- **Una persona puede venir de varias cargas.** Si estuvo en dos cargas
  anteriores, cuenta en las dos filas del desglose. Por eso la suma puede
  superar 148, y el modal tiene que decirlo: *«una persona puede aparecer en
  más de una carga»*.
- **No todo reutilizado viene de otra carga.** Una persona pudo haber entrado
  por la **landing**, por un **alta manual** o por una **encuesta**. Esas
  personas van en una fila propia —*«sin carga previa: landing, alta manual o
  encuesta»*—, para que ninguna quede sin explicar.

---

## 5. Las cargas de cada panelista

### R-CAR.4 — Sección «Cargas» en la ficha (P0)

- Dada la ficha de un panelista, entonces muestra **en qué cargas aparece**, con:
  nombre y fecha de la carga, si fue **creado** o **reutilizado** en ella, los
  **clientes** de esa carga y las **temáticas** que respondió en ella.
- [ ] Ordenada por fecha de la carga, la más reciente primero.
- [ ] Cada carga lleva a su detalle.
- [ ] Si la persona entró por la landing, un alta manual o una encuesta, eso se
      indica también, aunque no sea una carga.

---

## 6. «Ver quiénes son» no aparece en la auditoría

### Causa (verificada)

La reidentificación **sí se registra**, desde siempre: `POST /reidentificacion`
llama a `auditoria.registrar_reidentificacion`, que escribe en la tabla
`reidentificacion`. Pero la pantalla de auditoría lee **`usuario_auditoria`**,
que es otra tabla.

Existe una ruta para leer ese registro —`GET /reidentificacion`, con permiso
de cumplimiento— y está declarada en `api.js` como `reidentificacion.registro`,
pero **ninguna pantalla la usa**.

> **La evidencia legal no se perdió.** Cada «ver quiénes son» quedó guardado con
> su actor, motivo y personas. Lo que falta es verlo. Es el mismo patrón ya visto
> en la Fase 7: el servidor listo, la pantalla sin conectar.

### R-AUD.1 — Mostrar las reidentificaciones (P0)

- [ ] En la pantalla de auditoría, las reidentificaciones aparecen **junto con**
      el resto de las acciones —en la misma línea de tiempo o en una pestaña
      propia—, usando `GET /reidentificacion`.
- [ ] Cada entrada muestra: **quién**, **cuándo**, **motivo** (consulta, ficha,
      exportación, convocatoria…) y **a cuántas personas** alcanzó, con la
      posibilidad de ver cuáles.
- [ ] Filtros por usuario, por fecha y por panelista: *«¿quién vio los datos de
      esta persona?»* es la pregunta que un pedido de acceso obliga a contestar.
- [ ] Respeta el permiso de la ruta: solo administración y protección de datos.

---

## Cambios de esquema

En la bóveda:

- **`cliente`**: `id`, `nombre` (normalizado, único), `creado_en`.
- **`carga_cliente`**: `carga_id`, `cliente_id`, `creado_en`. Única por el par.
  Cascada al borrar la carga.
- **Vista `v_persona_cliente`**: `persona_carga` ⨝ `carga_cliente`. Sin tabla
  por persona.
- **`lista_negra`**: `id`, `id_persona`, `categoria`, `detalle`, `agregado_por`,
  `agregado_en`, `removido_por`, `removido_en`, `motivo_remocion`. Una fila por
  cada **entrada**; estar en la lista negra es tener una fila sin `removido_en`.
  Cascada al borrar la persona.
- **`v_persona_convocable`**: excluir a quienes tienen una entrada activa en
  `lista_negra`. Es un cambio en la superficie externa: correr la batería de
  COLOQUIO después.

## Definition of Done

**Clientes**
- [ ] Una carga admite varios clientes, al cargarla y después (test).
- [ ] «Antel» y «antel» son el mismo cliente (test).
- [ ] Un panelista tiene los clientes de todas sus cargas, sin copias (test).
- [ ] La consulta filtra por cliente, incluyendo y excluyendo (test).
- [ ] El cliente no aparece en el portal, ni en la superficie de COLOQUIO, ni en
      las exportaciones por defecto (test).

**Panelistas**
- [ ] La grilla pagina, muestra el total y conserva los filtros (test).

**Lista negra**
- [ ] Agregar exige motivo y queda registrado (test).
- [ ] En los resultados aparecen marcados, no ocultos (test).
- [ ] No se los convoca en encuestas, y sus invitaciones pendientes se cancelan
      (test).
- [ ] No aparecen en `v_persona_convocable` (test, y la batería de COLOQUIO en
      verde).
- [ ] Sacar a alguien conserva el historial (test).
- [ ] El panelista no ve nada en su portal (test).

**Cargas**
- [ ] El desglose de reutilizados lista las otras cargas con sus cantidades, e
      incluye la fila «sin carga previa» (test).
- [ ] La ficha muestra las cargas de la persona, con creado/reutilizado,
      clientes y temáticas (test).

**Auditoría**
- [ ] Un «ver quiénes son» aparece en la pantalla de auditoría con actor, motivo
      y cantidad de personas (test).
- [ ] Se puede filtrar por panelista (test).

## Costos

**US$ 0.** Son tablas chicas, una vista y cambios de interfaz, sin llamadas a
APIs ni infraestructura nueva.

## Riesgos y preguntas abiertas

- **[privacidad]** La lista negra y la baja (R-LN.4): decidir con asesoría si se
  conserva alguna huella para impedir la reinscripción.
- **[producto]** La lista negra concentra poder sobre personas concretas.
  Conviene revisarla periódicamente —que nadie quede en ella por un motivo que
  ya no aplica— y que el permiso para agregar sea acotado.
- **[comercial]** El cliente es confidencial. Además de las restricciones de
  R-CLI.4, conviene evaluar si todos los roles internos deben verlo.
