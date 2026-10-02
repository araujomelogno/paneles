# SPEC — Ingesta en diferido con Cloud Tasks

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Corrige:** la ingesta sincrónica, que corta por timeout en cargas grandes
**Alcance:** backend de ingesta (encuesta y carga de panelistas) y su pantalla
**Estado:** Para desarrollo
**Última actualización:** 2026-10-02

---

## 1. Problem Statement

La ingesta corre entera dentro de una sola request HTTP. Con pocos cientos de
respuestas funciona; con una base real **no llega**: una carga de 200.000
respuestas implica ~1.560 llamadas a Voyage en serie, entre 30 y 60 minutos
solo de embeddings, más los inserts. Cloud Run corta la request al llegar al
timeout —hoy configurado en 300 segundos— y la carga falla con un error
genérico, después de haber procesado una parte.

Tres problemas distintos en uno:

**Hay un techo duro.** Aunque se suba el timeout, el máximo de una función de
2ª gen es 60 minutos. Una base grande no entra, y subir el límite solo corre la
pared de lugar.

**El usuario queda a ciegas.** Mira una pantalla colgada sin saber si avanza, si
va por la mitad o si ya falló. No hay progreso ni estimación.

**Un fallo tardío obliga a rehacer todo.** Si la llamada 1.400 falla, no hay
forma de retomar: se vuelve a empezar, repitiendo el trabajo y el gasto.

## 2. Goals

- Que una carga de **cualquier tamaño** termine, sin techo de tiempo.
- Que el usuario vea **progreso real** y pueda irse de la pantalla.
- Que un fallo parcial **se reintente solo**, sin rehacer lo ya procesado.
- Sin cambiar la lógica de ingesta ya probada: despivote, resolución de códigos,
  gate de consentimiento, membresía, participación y guardrail de PII.

## 3. Non-Goals

- **No cambia qué hace la ingesta**, solo cuándo y en qué pedazos.
- **No cambia la pantalla de mapeo** (variables, demográficos, consentimiento):
  el cambio arranca al confirmar.
- **No se paraleliza dentro de un lote** en esta versión (ver §9: es una mejora
  complementaria, no un reemplazo).
- **No se migra a Cloud Run Jobs**: Cloud Tasks resuelve el caso sin salir del
  flujo de despliegue de Firebase.
- No cambia el pipeline de consulta ni el modelo de embeddings.

## 4. User Stories

- Como analista, quiero confirmar una carga grande y que el sistema me diga que
  quedó en proceso, en vez de esperar con la pantalla colgada.
- Como analista, quiero ver cuánto lleva procesado y cuánto falta.
- Como analista, quiero cerrar la pestaña y volver después a ver cómo terminó.
- Como analista, quiero que si algo falla a mitad de camino, se recupere solo o
  me diga exactamente qué lote falló y por qué.
- Como responsable de datos, quiero el mismo resumen final que hoy —creados,
  reutilizados, sin mapear, sin consentimiento, discrepancias—, sin importar que
  se haya procesado en partes.

**Casos borde**
- Carga chica (unas pocas filas): no debería sentirse más lenta que antes.
- Un lote falla por un error transitorio de Voyage.
- Un lote falla por un error de datos (no se arregla reintentando).
- La misma carga se confirma dos veces.
- Se cierra la pestaña y se vuelve más tarde.
- Dos cargas grandes en simultáneo.
- Retiro de consentimiento mientras la carga está en proceso.

## 5. Requirements

### R-ASYNC.1 — Confirmar encola, no procesa (P0)

- Dada una carga confirmada, entonces el sistema **persiste el trabajo**, lo
  divide en lotes, encola una tarea por lote y **responde de inmediato** con el
  identificador de la carga y su estado inicial.
- Dada esa respuesta, entonces la pantalla pasa a mostrar progreso: no espera el
  resultado final.
- [ ] El tamaño de lote es configurable, con un default documentado (sugerido:
      **2.000 respuestas**, que deja cada tarea muy por debajo del techo).
- [ ] El archivo ya procesado (filas despivotadas y mapeo resuelto) se guarda
      junto con la carga: cada tarea no vuelve a leerlo ni a reinterpretarlo.

> **Por qué se persiste el trabajo y no solo el archivo.** Si cada tarea
> re-despivotara el archivo, un cambio en el mapeo entre tareas produciría lotes
> inconsistentes. Resolver el mapeo una sola vez, al confirmar, hace que las
> tareas sean puramente mecánicas.

### R-ASYNC.2 — Cada lote es una tarea (P0)

- Dada una tarea, entonces procesa **solo su lote**: embeddings, inserts,
  membresías y participaciones de esas filas.
- Dada una tarea terminada, entonces registra su resultado parcial (procesadas,
  sin mapear, sin consentimiento, discrepancias) asociado a la carga.
- Dada la última tarea de una carga, entonces la carga pasa a **terminada** y se
  consolida el resumen.
- [ ] Las tareas son **idempotentes**: reprocesar un lote no duplica respuestas,
      membresías ni participaciones. El upsert existente ya lo garantiza para
      respuestas; verificar que también para membresía y participación.
- [ ] La concurrencia de la cola es configurable, con un default conservador
      (sugerido: **3 tareas en paralelo**), para no saturar Voyage ni la
      instancia de base.

### R-ASYNC.3 — Estado y progreso visibles (P0)

- Dada una carga, entonces tiene estado: `encolada`, `procesando`, `terminada`,
  `terminada_con_errores`, `fallida`.
- Dada una carga en proceso, entonces se puede consultar: lotes totales, lotes
  terminados, filas procesadas y, si se puede estimar, tiempo restante.
- Dada la pantalla, entonces refresca el progreso mientras la carga avanza y
  **sobrevive a un refresco del navegador**: el estado vive en la base, no en el
  cliente.
- Dada una carga terminada, entonces muestra el mismo resumen consolidado que
  hoy entrega la ingesta sincrónica.

### R-ASYNC.4 — Reintentos y fallos (P0)

- Dado un lote que falla por un error **transitorio** (timeout de red, 429 de
  Voyage, caída momentánea de la base), entonces Cloud Tasks lo reintenta con
  espera creciente.
- Dado un lote que agota los reintentos, entonces la carga queda
  `terminada_con_errores` y **se informa qué lote falló y por qué**, sin tirar
  abajo los lotes que sí entraron.
- Dado un lote fallido, entonces se puede **reintentar solo ese lote** desde la
  pantalla, sin rehacer la carga completa.
- [ ] Límite de reintentos configurable (sugerido: 5), con espera creciente.
- [ ] Un error de datos (una fila inválida) **no** se reintenta indefinidamente:
      se distingue de un error transitorio y se informa.

> **Por qué importa la distinción.** Reintentar cinco veces un lote que falla
> por un dato mal formado gasta tiempo y dinero en embeddings que van a fallar
> igual. El reintento sirve para lo transitorio.

### R-ASYNC.5 — Cargas chicas no se degradan (P1)

- Dada una carga por debajo del umbral de un lote (p. ej. menos de 2.000
  respuestas), entonces se procesa igual por la vía asincrónica, pero la
  pantalla muestra el resultado apenas termina.
- [ ] No debería sentirse más lenta que la ingesta actual para cargas chicas.

> Alternativa a evaluar: mantener la vía sincrónica para cargas chicas. Agrega
> dos caminos que mantener; **se prefiere un solo camino** salvo que la latencia
> percibida empeore de forma notoria.

### R-ASYNC.6 — Consentimiento revocado durante la carga (P0)

- Dado que una persona retira `uso_semantico` mientras la carga está en
  proceso, entonces los lotes aún no procesados **no ingestan** sus respuestas.
- Dado que ya se habían ingestado en un lote anterior, entonces la cascada de
  baja existente las elimina: no hace falta nada nuevo.
- [ ] El gate se evalúa **en cada lote**, no una sola vez al confirmar.

## 6. Cambios de esquema

En la bóveda (o donde hoy viva el registro de cargas):

- **`carga`** (o la entidad equivalente): agregar `estado`, `lotes_total`,
  `lotes_terminados`, `filas_procesadas`, `creado_en`, `terminado_en`.
- **`carga_lote`** (nueva): `id`, `carga_id`, `indice`, `estado`
  (`pendiente`|`procesando`|`ok`|`fallido`), `intentos`, `resultado jsonb`,
  `error text`, `actualizado_en`. Única por `(carga_id, indice)`.
- **Trabajo persistido**: las filas ya despivotadas y con el mapeo resuelto,
  asociadas a su lote. Puede ser una tabla auxiliar o un blob por lote; decidir
  según el tamaño típico.

Migración aditiva. Sin cambios en el store semántico.

## 7. Infraestructura

- **Cloud Tasks**: el SDK de Python de `firebase-functions` expone
  `tasks_fn.on_task_dispatched` (verificado en la versión actual del paquete).
- La función de tarea necesita el mismo acceso a bases y secretos que la
  función HTTP: **mismo conector VPC, misma región `southamerica-east1`**.
- [ ] Habilitar la API de Cloud Tasks en el proyecto si no lo está.
- [ ] La cola se configura con concurrencia y política de reintentos (§5).

## 8. Definition of Done

- [ ] Confirmar una carga responde en segundos, con la carga en estado
      `encolada` (test).
- [ ] Una carga de varios lotes termina completa y el resumen consolidado
      coincide con el de una ingesta sincrónica equivalente (test).
- [ ] Reprocesar un lote no duplica respuestas, membresías ni participaciones
      (test).
- [ ] Un lote que falla de forma transitoria se reintenta y termina bien (test).
- [ ] Un lote que agota reintentos deja la carga `terminada_con_errores`, con el
      motivo, y no afecta a los demás lotes (test).
- [ ] Un lote fallido se puede reintentar solo desde la pantalla (test).
- [ ] El progreso sobrevive a un refresco del navegador (test).
- [ ] Una persona que retira `uso_semantico` a mitad de carga no entra en los
      lotes siguientes (test).
- [ ] Una carga chica no tarda más que antes (medición, no test).
- [ ] El guardrail de PII sigue activo en la vía asincrónica (test de no
      regresión).

## 9. Costos

| Concepto | Costo |
|---|---|
| Cloud Tasks | Prácticamente **US$ 0**: se cobra por operación y la capa gratuita cubre un millón mensual. Una carga de 200.000 respuestas en lotes de 2.000 son 100 tareas. |
| Tiempo de ejecución de las funciones | **El mismo que hoy**: se paga el trabajo, no la espera. Partirlo en lotes no lo aumenta. |
| Embeddings (Voyage) | **Sin cambios**: se cobra por token, no por cómo se agrupan las llamadas. |
| Infraestructura nueva | **Ninguna**: mismo proyecto, mismo conector, mismas bases. |

> **Dónde sí puede aumentar:** si se sube mucho la concurrencia, varias tareas
> golpean la base a la vez. Con `paneles-semantica` en un tier chico eso puede
> degradar o forzar a subirla (~US$ 24/mes de `db-f1-micro` a `db-g1-small`). De
> ahí el default conservador de 3 tareas en paralelo.

## 10. Riesgos y preguntas abiertas

- **[ingeniería]** Los límites de tasa de Voyage son el techo real de la
  concurrencia. Con varias tareas en paralelo mandando lotes, un 429 es
  esperable: el manejo de reintentos tiene que contemplarlo explícitamente.
- **[ingeniería]** Persistir el trabajo despivotado de 200.000 respuestas ocupa
  espacio temporal en la base. Conviene limpiarlo al terminar la carga y decidir
  qué pasa si una carga queda a medias para siempre.
- **[producto]** Con el proceso en diferido, el analista puede irse y no
  enterarse de que falló. Vale evaluar un aviso (en la app o por correo) cuando
  una carga termina con errores.
- **[producto]** Dos cargas grandes en simultáneo compiten por Voyage y por la
  base. Decidir si se limita a una carga activa por vez.
- **[complementario]** **Paralelizar las llamadas dentro de cada lote** (un pool
  de hilos) es una mejora aparte y compatible: reduce el tiempo de cada tarea
  además de repartirlas. Es más barata de implementar y, para volúmenes
  moderados, puede alcanzar por sí sola. Conviene medir con Cloud Tasks andando
  antes de decidir si hace falta.
