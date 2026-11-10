# PEDIDO — Cancelar una carga sin dejar rastros, y detener una ingesta en curso

**Sistema:** Gestión de paneles · carga de respuestas y de panelistas
**Severidad:** Media — quedan cargas y encuestas vacías en los listados, y
cerrar la ventana no detiene una ingesta
**Detectado:** 2026-11-06
**Entrega:** una sola versión

---

## 1. Qué pasa (verificado en el código)

**Antes de confirmar.** La carga se crea **en el primer paso** del flujo, al poner
el nombre y apretar «Continuar» —antes de subir el archivo, mapear y revisar—:

- Desde una encuesta: `api.encuestas.nuevaCarga` → `POST /encuestas/<id>/cargas`.
- Desde la sección Panelistas: `api.cargas.crear` → `POST /cargas`, que además
  **crea la encuesta automática** (`cargas.crear`).

Si después se cancela en cualquier momento —mapeo, revisión—, la carga y, en el
segundo caso, la encuesta **quedan creadas y vacías**, y aparecen en los
listados.

**Después de confirmar.** Al apretar «Ingestar», el procesamiento corre en
diferido, en el servidor. **Cerrar la ventana no lo detiene**: la carga termina
igual, con sus respuestas. Y no existe ninguna acción para frenar una ingesta en
curso: los estados de un trabajo son encolada, procesando, terminada, terminada
con errores y fallida.

## 2. Qué se pide

### R-CAN-C.1 — La carga nace provisoria (P0)

- Dada una carga creada en el primer paso del flujo, entonces queda
  **provisoria** hasta que se confirma la ingesta.
- Dada la encuesta automática que crea una carga desde la sección Panelistas,
  entonces también queda **provisoria**, con la misma regla.
- Dada la confirmación de la ingesta, entonces carga y encuesta dejan de ser
  provisorias, en la misma transacción que crea el trabajo de ingesta.
- Dado un listado —encuestas, cargas, composición, filtros de consultas—,
  entonces las provisorias **no aparecen**.
- [ ] Una encuesta **existente** a la que se le agrega una carga no se vuelve
      provisoria: solo la carga.

### R-CAN-C.2 — Cancelar borra lo provisorio (P0)

- Dado «Cancelar» o cerrar el modal en cualquier paso **antes** de confirmar,
  entonces se borra la carga provisoria y, si la hubo, su encuesta automática
  provisoria.
- [ ] El borrado es seguro de repetir: si ya no existe, no falla.

### R-CAN-C.3 — Limpieza de lo abandonado (P0)

Cerrar la pestaña del navegador o perder la conexión no avisa al servidor, así que
el cancelar explícito no alcanza.

- Dada una carga provisoria con **más de 24 horas** y **sin trabajo de
  ingesta**, entonces se elimina, junto con su encuesta automática provisoria.
- [ ] La limpieza corre **al iniciar cada carga nueva** —sin un proceso
      programado—, y además se puede disparar a mano desde Cumplimiento.
- [ ] Queda registrado cuántas se limpiaron y cuándo.

### R-CAN-C.4 — Detener una ingesta en curso (P0)

- Dado el seguimiento de una ingesta **encolada o procesando**, entonces hay una
  acción **«Detener ingesta»**, con confirmación.
- Dada esa acción, entonces:
  - los lotes **pendientes** pasan a un estado nuevo, **cancelado**, y no se
    procesan;
  - un lote que ya estaba **procesando** termina su transacción —no se corta a
    mitad de un lote—;
  - el trabajo pasa a un estado nuevo, **detenida**.
- Dada una tarea de Cloud Tasks ya encolada para un lote cancelado, entonces el
  procesador **verifica el estado al empezar** y la descarta sin procesar.
- Dado un trabajo detenido, entonces la pantalla dice cuántos lotes se procesaron
  y cuántos no, y ofrece:
  - **Reanudar**: vuelve a encolar los lotes cancelados.
  - **Borrar lo cargado**: lleva al borrado de carga que ya existe (R-EU.10).
- [ ] Un trabajo detenido **no** cuenta como activo para la guarda de una ingesta
      por destino.

### R-CAN-C.5 — Cerrar no es cancelar (P0)

- Dado el seguimiento de una ingesta, entonces dice claramente: *«El
  procesamiento sigue en el servidor aunque cierres esta ventana. Para frenarlo,
  usá "Detener ingesta".»*
- [ ] El modal de revisión, al apretar «Ingestar», muestra el mismo aviso.

## 3. Cambios de esquema

| Cambio | Detalle |
|---|---|
| `carga.provisoria` | `boolean not null default false` |
| `encuesta.provisoria` | `boolean not null default false` |
| Estado de trabajo `detenida` | Nuevo valor |
| Estado de lote `cancelado` | Nuevo valor |

Las cargas y encuestas ya existentes quedan con `provisoria = false`.

## 4. Definition of Done

- [ ] Cancelar en el mapeo o la revisión no deja carga ni encuesta en los
      listados, por los dos caminos (test).
- [ ] Una carga agregada a una encuesta existente, cancelada, no afecta a la
      encuesta (test).
- [ ] Una carga provisoria abandonada más de 24 horas se elimina al iniciar otra
      carga (test).
- [ ] «Detener ingesta» deja sin procesar los lotes pendientes y el trabajo en
      «detenida» (test).
- [ ] Una tarea de Cloud Tasks de un lote cancelado no procesa nada (test).
- [ ] «Reanudar» completa los lotes cancelados (test).
- [ ] Un trabajo detenido no bloquea una ingesta nueva para el mismo destino
      (test).
- [ ] El seguimiento avisa que cerrar la ventana no detiene el proceso.

## 5. Costos

**US$ 0.** La limpieza corre al iniciar cada carga, sin un proceso programado. Si
más adelante se prefiere un barrido programado, Cloud Scheduler cuesta del orden
de US$ 0,10 por tarea al mes fuera de la capa gratuita.

## 6. Despliegue

Sigue las reglas del `CLAUDE.md` del repositorio: proyecto de gcloud fijado y
verificado al empezar; respaldo de la bóveda; migración aplicada y
`verificar_esquema.py` en verde **antes** del `firebase deploy`; token de
COLOQUIO regenerado inmediatamente antes de la batería.

> **Antes de aplicar:** si hoy hay cargas o encuestas vacías que quedaron de
> cancelaciones anteriores, listarlas para que el equipo decida si borrarlas. No
> se borran automáticamente: la migración las deja como no provisorias, porque no
> hay forma de saber cuáles fueron abandonadas y cuáles se crearon a propósito.
