# SPEC — Fase 1: Núcleo de personas e identidad

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**PRD de referencia:** `PRD_sistema_paneles_unificado.md`
**Estado:** ✅ Implementada y desplegada en producción (GCP, `southamerica-east1`)
**Última actualización:** 2026-09-10

> **Encuadre.** Esta es la especificación **de referencia** de lo que quedó
> construido, no un work order (ese fue `HANDOFF_fase1.md`). Sirve para
> documentar el sistema, verificar el DoD y ser consultada desde las fases
> siguientes. Está escrita contra el código implementado.

---

## 1. Problem Statement

Antes de poder consultar nada, el sistema necesita saber **quién es quién** y bajo qué condiciones puede tratar sus datos. Sin un registro único de personas con identidad estable, cada estudio queda aislado y no se puede cruzar a un individuo entre olas. Y sin consentimiento, retención y baja como piezas de primera clase, retener PII identificada de forma permanente —el corazón de un panel— es un pasivo legal bajo URCDP.

Esta fase construye ese núcleo: el registro de personas, la identidad que usa toda la plataforma, el consentimiento con dientes, los paneles y sus membresías, y el enganche que hace que las respuestas de una encuesta lleguen al store semántico asociadas a la persona correcta, sin que su PII salga nunca de la bóveda.

## 2. Goals

- **Un único registro de personas** con `id_persona` opaco y estable, emitido en el enrolamiento (no en la ingesta).
- **Deduplicar identidad** en el alta, sin fusionar por parecido cuando la evidencia es ambigua.
- **Consentimiento por finalidad**, con retiro y borrado en cascada entre los dos stores.
- **Paneles y membresías N:M**: una persona en varios paneles.
- **Fielding con trazabilidad**: encuestas por panel, convocatoria, registro de participación, y respuestas ingestadas vinculadas por `ref_estudio`.
- **Separación de PII por diseño**, verificable: nada de PII del lado semántico.

## 3. Non-Goals

- **No consulta nada.** Ni composición, ni tablero, ni consulta demográfica/semántica/mixta (Fase 2).
- **No acciona sobre el panel.** Sin muestreo, chequeos de calidad ni gamificación (Fase 3) — aunque sus tablas existen en el DDL.
- **No hay análisis longitudinal** ni landing pública endurecida (Fase 4).
- **No declara anonimización**: el store semántico es seudonimizado, sigue siendo dato personal.

## 4. Personas y roles implementados

Cuatro roles, con permisos verificados en cada ruta:

| Rol | Alcance |
|---|---|
| `admin` | Todo. |
| `operaciones` | Responsable de panel: enrola, arma paneles, convoca, resuelve revisiones. |
| `analista` | Fieldea, ingesta y lee; **no** enrola ni da de baja. |
| `dpo` | Cumplimiento: retiros de consentimiento y bajas. |

El padrón de usuarios (personal de Equipos) vive en Firebase Auth + Firestore (`usuarios/{uid}`). **No contiene datos de panelistas.**

## 5. User Stories

**Responsable de panel / operaciones**
- Como responsable de panel, quiero enrolar un panelista con sus datos y su consentimiento, para sumarlo en regla.
- Como responsable de panel, quiero que al enrolar a alguien que ya existe se reutilice su identidad, para no tener registros duplicados.
- Como responsable de panel, quiero decidir a mano los casos de duplicado dudoso, en vez de que el sistema fusione por parecido.
- Como responsable de panel, quiero que un panelista pertenezca a varios paneles sin duplicarlo.

**Analista**
- Como analista, quiero fieldear una encuesta a un panel, convocar panelistas e ingestar sus respuestas, para que queden disponibles para análisis.
- Como analista, quiero verificar que la ingesta cerró bien (cuántas respuestas entraron y de qué estudio).

**DPO / cumplimiento**
- Como DPO, quiero que cada consentimiento quede registrado por finalidad y versión de texto, para demostrar base legal.
- Como DPO, quiero que un retiro de consentimiento borre la PII, los embeddings y saque del muestreo, y que si algo falla quede pendiente y reintentable.
- Como DPO, quiero poder auditar que no hay PII en el store semántico.

**Casos borde cubiertos**
- Alta con documento/email coincidente → reutiliza identidad.
- Alta con nombre + fecha de nacimiento coincidente (homónimo) → **cola de revisión**, no fusión automática.
- Retiro de consentimiento cuando el borrado en el otro store falla → baja local completa + pendiente reintentable.
- Ingesta con celdas vacías y mezcla de códigos/etiquetas.
- Re-ingesta del mismo estudio → no duplica.

## 6. Requirements implementados

### R1.1 — Alta de panelista (bóveda)
Datos patronímicos y de contacto: sexo, fecha de nacimiento, localidad, nombre, email, celular, contacto, observaciones. El alta emite el `id_persona`.
- Dado un alta completa, cuando se confirma, entonces se crea la persona y se emite un `id_persona` (uuid aleatorio, no derivado de la PII).
- Dado que falta el consentimiento, cuando se intenta confirmar, entonces el alta se rechaza.
- [x] `id_persona` opaco: no se puede recomputar desde la PII.

### R1.2 — Dedup de identidad
Orden de resolución, se detiene en el primer match: documento → email (case-insensitive) → nombre + fecha de nacimiento → nueva persona.
- Dado un alta con documento coincidente, entonces se reutiliza ese `id_persona` sin crear duplicado.
- Dado un alta con email coincidente (distinta capitalización), entonces se reutiliza ese `id_persona`.
- Dado un match ambiguo (nombre + fecha de nacimiento, sin documento ni email), entonces **no fusiona**: crea un alta en revisión con sus candidatos.
- Dado un alta en revisión, cuando quien decide la resuelve, entonces se aplica `fusionar` (con el `id_persona` elegido), `crear` (nueva identidad) o `descartar`.
- Dada una revisión ya resuelta, cuando se intenta resolver otra vez, entonces se rechaza indicando su estado.
- [x] Alias de plataforma origen (Dooblo/Alchemer) registrados para resolver ingestas futuras a la misma persona.

### R1.3 — Consentimiento por finalidad
Finalidades independientes: `contacto_participacion` y `uso_semantico`.
- Dado el otorgamiento, entonces queda registrada la finalidad, el estado vigente, el timestamp y la **versión del texto** consentido.
- Dado un retiro, entonces el consentimiento pasa a retirado con su fecha y dispara la cascada.
- Dado un re-otorgamiento, entonces se crea un registro vigente nuevo y se conserva el historial.
- **Cascada de baja:** retirar `contacto_participacion` (o todas) borra PII de la bóveda, pide el borrado de embeddings en el store semántico, y da de baja las membresías (sale del muestreo).
- Dada una baja, entonces queda una constancia (lápida) con el token opaco que prueba que se atendió el pedido, **sin conservar PII**.
- Dado que el borrado en el store semántico falla, entonces la baja local se completa igual y el borrado semántico queda **pendiente y reintentable** (no se cuelga la operación).

### R1.4 — Paneles y membresías (N:M)
- Dado un panelista, cuando se lo agrega a un segundo panel, entonces su pertenencia al primero no se altera.
- Dado un panelista ya miembro, cuando se lo agrega de nuevo, entonces la operación es idempotente.
- Dada una baja de membresía, entonces se marca como baja con su fecha (no se borra el histórico).

### R1.5 — Fielding, convocatoria, participación e ingesta
- Dada una encuesta creada en un panel, entonces se genera su `ref_estudio` (uuid) que comparte con el `cuestionario` del store semántico.
- Dada una convocatoria, entonces por cada panelista convocado queda una participación registrada, y su nº acumulado de convocatorias es contable.
- Dada la ingesta de un Excel ancho (encabezados = `codigo` de pregunta), entonces se despivota a formato largo, cada celda se une a su pregunta por código, los códigos de cerradas se resuelven a etiqueta, el texto se compone como "pregunta → respuesta" y se vectoriza en lotes.
- Dada una re-ingesta del mismo estudio, entonces no se duplican cuestionario, preguntas ni respuestas (upsert idempotente).
- Dado un estudio ingestado, cuando se pide su cruce/resumen, entonces se puede verificar que las respuestas del store semántico corresponden a los panelistas convocados.

### R1.6 — Guardrail de PII (invariante)
- **Estático:** se audita el DDL del store semántico; no debe existir ninguna columna de PII.
- **Runtime:** cualquier escritura al store semántico que traiga una clave de PII (incluidos sinónimos como cédula, dni, email, teléfono) se **aborta**.
- Dada una consulta de auditoría, entonces se puede demostrar 0 columnas de PII del lado semántico.

### R1.7 — Autorización por rol
- Dada una ruta con permiso requerido, cuando el actor no lo tiene, entonces la operación se rechaza.
- Dado un usuario sin ficha en el padrón, entonces no puede operar (se le indica pedir el alta a un administrador).

## 7. Modelo de datos

**Store local (bóveda + paneles):** `persona`, `alias_origen`, `alta_en_revision`, `persona_borrada`, `panel`, `membresia`, `consentimiento`, `encuesta`, `participacion`, `objetivo_composicion` *(sin lógica hasta Fase 2)*, `puntos_movimiento` / `catalogo_premio` / `canje` *(sin lógica hasta Fase 3)*. Vista `v_demografia` con tramo etario derivado.

**Store semántico:** `cuestionario` (con `ref_estudio`), `individuo` (solo `id_persona`), `pregunta` (con `codigo`, tipo, opciones), `respuesta` (formato largo, `embedding vector(1024)` obligatorio, `texto_embebido`, índice HNSW, unicidad individuo+pregunta), más vista de procedencia.

Migraciones: `db/boveda/0001–0003`, `db/semantica/0001–0002`.

## 8. Superficie de API implementada

| Método y ruta | Permiso | Requisito |
|---|---|---|
| `POST /panelistas` | enrolar | R1.1, R1.2, R1.3 |
| `GET /panelistas` · `GET /panelistas/{id_persona}` | leer | — |
| `GET /revisiones` · `POST /revisiones/{id}/resolver` | leer / resolver_revision | R1.2 |
| `POST /paneles` · `GET /paneles` · `GET /paneles/{id}` · `PATCH /paneles/{id}` | gestionar_paneles / leer | R1.4 |
| `GET|POST /paneles/{id}/miembros` · `DELETE /paneles/{id}/miembros/{id_persona}` | leer / gestionar_paneles | R1.4 |
| `POST /consentimientos/{id_persona}` · `POST /consentimientos/{id_persona}/retiro` | enrolar / cumplimiento | R1.3 |
| `GET /cumplimiento/pendientes` · `POST /cumplimiento/reintentar` | cumplimiento | R1.3 |
| `POST /encuestas` · `GET /encuestas` · `GET|PATCH /encuestas/{id}` | fieldear / leer | R1.5 |
| `POST /encuestas/{id}/convocatoria` · `GET /encuestas/{id}/participacion` | fieldear / leer | R1.5 |
| `POST /encuestas/{id}/ingesta` · `GET /encuestas/{id}/cruce` | ingestar / leer | R1.5, R1.6 |
| `GET /auditoria/pii` | leer | R1.6 |
| `GET /yo` | — | — |

## 9. Definition of Done

- [ ] Se enrola un panelista con PII y consentimiento; el alta sin consentimiento se rechaza.
- [ ] El dedup reutiliza `id_persona` por documento y por email; el caso ambiguo va a revisión y se resuelve a mano.
- [ ] Un panelista pertenece a varios paneles sin duplicarse.
- [ ] Al fieldear e ingestar, las respuestas quedan asociadas al `id_persona` correcto y comparten `ref_estudio` con la encuesta.
- [ ] Re-ingestar el mismo estudio no duplica.
- [ ] Retirar consentimiento borra PII, pide borrado de embeddings y saca del muestreo; si el semántico falla, queda pendiente reintentable.
- [ ] Auditoría: **0 columnas de PII** en el store semántico.
- [ ] Los permisos por rol se respetan en todas las rutas.
- [ ] Pruebas automatizadas cubren dedup (los cuatro casos), gate de consentimiento y guardrail de PII.

> **Pendiente de verificación en producción.** El código está implementado y
> desplegado, pero el DoD se cierra corriendo la suite de pruebas y validando
> una ingesta real contra las instancias Cloud SQL. Hasta entonces, los
> requisitos están *construidos*, no *verificados end-to-end*.

## 10. Riesgos y deuda conocida

- **[legal]** Alcance del consentimiento del alta frente al uso semántico entre estudios: sigue abierto. *(bloquea el uso semántico de datos reales)*
- **[datos]** Calidad de la clave de dedup: sin documento ni email confiable, el dedup se degrada y crecen las revisiones manuales.
- **[operación]** La bóveda concentra toda la capacidad de reidentificar: su control de acceso y backups pesan más que los del store semántico.
- **[privacidad]** Riesgo mosaico: un perfil rico de respuestas puede reidentificar por combinación, aunque no haya token ni PII.
- **[ingeniería]** Coordinación entre stores en la cascada de baja: resuelto con pendientes reintentables, pero requiere que alguien atienda la cola.
- **[deuda]** Políticas RLS por rol no escritas (hoy deny-all de base). Logging de reidentificación no implementado.
- **[deuda]** `observaciones` es texto libre: puede acumular dato sensible sin control. Conviene una política de uso.
