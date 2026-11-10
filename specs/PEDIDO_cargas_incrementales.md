# PEDIDO — Cargas incrementales dentro de una encuesta

**Sistema:** Gestión de paneles · cargas, ficha del panelista y composición
**Severidad:** Media — los datos son correctos, pero la ficha y las estadísticas
de cada carga los presentan de forma engañosa
**Detectado:** 2026-11-06
**Entrega:** una sola versión

---

## 1. Qué pasa

Una encuesta de Alchemer se carga en varias veces mientras el campo está abierto,
y **cada SAV trae el acumulado**: quien respondió en la primera carga vuelve a
aparecer en la segunda, la tercera y las siguientes.

Hoy, por cada carga, el sistema registra el vínculo persona ↔ carga
(`persona_carga`). Una persona que respondió al principio queda vinculada a
**todas** las cargas de esa encuesta, y en la segunda y siguientes figura como
**«reutilizada»**, igual que alguien que viene de **otro estudio**. Resultado:

- La **ficha del panelista** muestra la misma encuesta tantas veces como cargas
  tuvo.
- Las **estadísticas de cada carga** inflan los «reutilizados» con gente que solo
  está repetida por el acumulado.
- El **desglose de reutilizados** se llena con la propia encuesta.

## 2. Lo que no hay que hacer: dejar de registrar el vínculo

Lo primero que se ocurre es no guardar el vínculo con las cargas posteriores.
**No**: `persona_carga` lo usan seis módulos —`cargas`, `cargas_borrado`,
`encuestas_borrado`, `composicion`, `canales_carga` y `personas`—, y el borrado
de cargas depende de él. Para decidir qué hacer con cada persona, el borrado se
fija si estaba **solo** en esa carga. Si una persona quedara vinculada solo a la
primera, borrar esa primera haría creer que no está en ninguna otra —aunque sus
respuestas más recientes vengan de la tercera (`respuesta.carga_id`)— y le
quitaría la participación o hasta ofrecería darla de baja.

**La decisión:** se sigue registrando cada aparición, se **distingue** la
repetición dentro de la encuesta, y se ajusta **cómo se muestra**.

## 3. Qué se pide

### R-INC.1 — Un tercer origen: «repetida» (P0)

- Dado un vínculo persona ↔ carga, entonces su origen es uno de:

| Origen | Cuándo |
|---|---|
| **creada** | La persona se creó con esta carga |
| **reutilizada** | La persona ya existía, por **otro** estudio o alta |
| **repetida** | La persona ya estaba en una carga **anterior de esta misma encuesta** |

- Dada la ingesta de una carga, entonces cada persona recibe el origen
  **repetida** si ya tiene un vínculo con otra carga de la misma encuesta creada
  antes; si no, **creada** o **reutilizada** como hasta hoy.
- [ ] Ampliar el `check` de `persona_carga.origen` para admitir `repetida`.

### R-INC.2 — Migración de lo ya cargado (P0)

- Dadas las encuestas con más de una carga, entonces para cada persona su vínculo
  con la **primera** carga de la encuesta conserva su origen, y los vínculos con
  las **siguientes** pasan a `repetida`.
- [ ] Corrida de prueba que informe, por encuesta, cuántos vínculos cambian,
      antes de escribir.
- [ ] Respaldo de la bóveda antes de aplicarla.

### R-INC.3 — Al borrar una carga, reasignar el origen (P0)

- Dada una carga que se borra, entonces cada persona que tenía en ella su
  **primera** aparición en la encuesta, y sigue en una carga **posterior** de la
  misma encuesta, pasa a tener esa siguiente como primera: su origen ahí cambia
  de `repetida` a `reutilizada` —la persona ya existía cuando entró esa carga—.
- [ ] El resto del borrado (R-EU.10) no cambia: sigue mirando **todos** los
      vínculos, incluidos los `repetida`, para decidir si una persona estaba solo
      en esa carga.

### R-INC.4 — La ficha agrupa por encuesta (P0)

- Dada la sección de cargas en la ficha del panelista, entonces cada encuesta
  aparece **una sola vez**, con la **primera carga** en que apareció la persona y
  su origen en esa carga.
- Dada una persona que también aparece en cargas posteriores de esa encuesta,
  entonces debajo se ve una nota: *«también en 2 cargas posteriores de esta
  encuesta»*, desplegable si se quiere ver cuáles.

### R-INC.5 — Estadísticas de cada carga (P0)

- Dado el detalle de una carga y el listado de cargas, entonces se informa:

> 487 en el archivo · **120 nuevos** · **15 reutilizados** de otros estudios ·
> **352 ya estaban** en cargas anteriores de esta encuesta

- Dado el **desglose de reutilizados** (R-CAR.3), entonces cuenta solo los
  `reutilizada`: los `repetida` no vienen de otro estudio y no van ahí.

### R-INC.6 — Composición por carga (P0)

- Dado el ámbito **una carga** en Composición, entonces muestra a las personas
  que esa carga **agregó** a la encuesta —origen `creada` o `reutilizada`—, sin
  las `repetida`.
- [ ] Una nota lo aclara: *«Muestra quiénes sumó esta carga. Para la encuesta
      completa, elegí el ámbito "una encuesta".»*
- [ ] La composición por **encuesta** no cambia.

### Lo que no cambia

- **`canales_carga`**: registrar canales en bloque para una carga se aplica a
  **todas** las personas del archivo, incluidas las repetidas. El texto de
  evidencia es el del estudio, y esas personas lo aceptaron igual.
- **`respuesta.carga_id`**, la participación y el borrado de encuestas.

## 4. Definition of Done

- [ ] Tres cargas incrementales de una encuesta: una persona presente en las tres
      queda `creada` o `reutilizada` en la primera y `repetida` en las otras dos
      (test).
- [ ] Después de migrar, ninguna persona tiene más de un vínculo no `repetida`
      dentro de una misma encuesta (verificable con una consulta).
- [ ] La ficha muestra cada encuesta una sola vez, con la primera carga y la nota
      de las posteriores (test).
- [ ] El detalle de una carga separa nuevos, reutilizados y ya estaban (test).
- [ ] El desglose de reutilizados no incluye a los repetidos (test).
- [ ] La composición de una carga excluye a los repetidos (test).
- [ ] Borrar la primera carga de una encuesta deja como primera a la siguiente,
      con origen `reutilizada`, y no ofrece dar de baja a quien sigue en otras
      cargas (test).

## 5. Costos

**US$ 0.** Una migración pequeña sobre `persona_carga` y cambios de consulta e
interfaz.

## 6. Despliegue

Sigue las reglas del `CLAUDE.md` del repositorio: proyecto de gcloud fijado y
verificado al empezar; respaldo de la bóveda; migración con corrida de prueba,
aplicada y `verificar_esquema.py` en verde **antes** del `firebase deploy`; token
de COLOQUIO regenerado inmediatamente antes de la batería.
