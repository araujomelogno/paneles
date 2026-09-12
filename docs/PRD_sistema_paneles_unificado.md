# PRD — Sistema de gestión de paneles y consulta semántica

**Contexto:** Equipos Consultores · investigación de mercado
**Estado:** Borrador vivo · documento unificado (reemplaza a `PRD_gestion_de_paneles_detallado.md` y `PRD_consulta_semantica_cuestionarios.md`)
**Última actualización:** 2026-09-10
**Estado de implementación:** Fase 1 desplegada en producción (GCP). Fases 2–4 pendientes.

---

## 1. Marco

### Problem Statement

Equipos administra paneles de investigación de mercado —conjuntos estables de personas a las que se fieldan encuestas de forma repetida— y necesita dos cosas que hoy no tiene.

**Mantener el panel sano.** Un panel es un activo que se degrada solo: pierde representatividad, sus panelistas se fatigan y abandonan, se sobre-encuesta a los mismos, y entra dato de baja calidad. Sin un sistema que mida y accione sobre eso, el panel se pudre y los datos se sesgan.

**Consultar por concepto, no por columna.** Los microdatos son heterogéneos: cada cuestionario tiene sus propias variables. Cuando un analista quiere encontrar individuos que responden a un criterio conceptual —"consume tal bebida", "está descontento con el gobierno"—, hoy necesita saber de antemano qué preguntas y qué columnas lo contienen, algo que cambia estudio a estudio.

A esto se suma que retener PII identificada de forma permanente, para contactar a las mismas personas una y otra vez, es una actividad de tratamiento regulada bajo URCDP: sin consentimiento, retención y baja como ciudadanos de primera clase, el sistema es un pasivo legal.

### Goals

- Mantener paneles **sanos**, no solo almacenarlos (representatividad, participación, fatiga, calidad).
- **Un solo registro de personas**: el enrolamiento emite la identidad estable (`id_persona`) de toda la plataforma.
- **La persona es la unidad, y cruza estudios**: un mismo individuo puede responder varios cuestionarios y acumula un perfil a través de todos ellos, de modo que criterios provenientes de estudios distintos pueden identificar a una persona real. El cuestionario es procedencia del dato, no frontera.
- **Consulta por concepto sin conocer el esquema**: describir un criterio en lenguaje natural y obtener un ranking de individuos que se le aproximan.
- **Criterios combinados a nivel persona**, incluso cuando cada criterio proviene de un estudio distinto.
- **Muestreo que equilibra cuota y fatiga**.
- **Cumplimiento como columna vertebral** (consentimiento, retención, baja en cascada, minimización).
- **Participación como palanca**, no solo reporte.

### Non-Goals

- **No es segmentación exacta ni exhaustiva.** El resultado es un ranking por aproximación, no "todos los que cumplen"; no reemplaza un filtro booleano preciso.
- **No hay capa de conceptos canónicos ni pre-clasificación de respuestas.** Descartado deliberadamente a favor de interpretación en tiempo de consulta (*schema-on-read*), para no congelar errores de canonización en el dato.
- **No reemplaza la tabulación cuantitativa.** Ponderación, representatividad y significancia estadística siguen en las herramientas actuales.
- **No espeja atributos demográficos al store semántico** (por ahora): los segmentadores quedan autoritativos en la bóveda y el store semántico se mantiene como contenido puro. Además de simplificar, reduce el **riesgo mosaico**: cuantos menos cuasi-identificadores hay del lado semántico, menos reidentificable es ese dataset por combinación de respuestas.
- **No declara anonimización.** El dataset seudonimizado sigue siendo dato personal bajo URCDP; es una salvaguarda, no una exención.
- **No es tiempo real.** La ingesta es por lotes, por estudio.
- **No endurece el auto-registro en v1**: la landing se construye en Fase 3 en su forma simple; la verificación de contacto y el anti-fraude son Fase 4.
- **No automatiza el tratamiento fiscal de premios.**
- **Sin caché de interpretaciones recurrentes en v1.**

### Personas

- **Responsable de panel / operaciones** — mantiene la salud del panel, decide reclutamiento e invitaciones.
- **Analista / investigador** — fieldea encuestas y consulta (demográfica / semántica / mixta / longitudinal).
- **Panelista** — se registra, consiente, responde, gana y canjea puntos, ejerce derechos.
- **DPO / cumplimiento** — consentimiento, retención, bajas.

### Arquitectura

Un sistema, dos módulos, dos stores. Ambos en **Cloud SQL for Postgres**, en instancias **separadas** (la separación es parte del diseño de privacidad, no preferencia de infra). App en Firebase (Auth + Cloud Functions Python + Hosting), región `southamerica-east1`.

- **Store local (bóveda + paneles):** PII y atributos demográficos, autoritativos aquí — `persona` con sexo, fecha de nacimiento, localidad, nombre, email, celular, contacto y observaciones, más atributos derivados (tramo etario) —; paneles, membresías, consentimiento, participación, muestreo, gamificación. Sirve la gestión de panel y la **consulta puramente demográfica** sin tocar embeddings.
- **Store semántico:** solo embeddings + `id_persona`. Contenido puro.
- **Regla dura:** la PII nunca se escribe en el store semántico. Al semántico solo viaja `id_persona`.
- **Cruce entre stores:** por conjuntos de `id_persona`, y `ref_estudio` (uuid) para vincular `encuesta` (local) ↔ `cuestionario` (semántico). No hay FK cruzada.
- **Consentimiento por finalidad, con dos finalidades independientes:** `contacto_participacion` (convocar y participar) y `uso_semantico` (perfilado semántico entre estudios). Sin la finalidad vigente correspondiente, la operación se rechaza: no se convoca sin la primera, y no se ingesta ni se devuelve en consultas semánticas sin la segunda.

---

## 2. Módulo de consulta semántica (mecánica interna)

Cómo funciona el motor por dentro. Las tres formas de consulta como feature de producto están en la Fase 2 (§3).

### Principio: todo se evalúa semánticamente

**Toda respuesta se embebe y toda aproximación es por similitud, incluidas las preguntas cerradas.** No hay match exacto sobre códigos ni valores: una cerrada se trata igual que una abierta (su etiqueta se resuelve y se embebe junto al texto de su pregunta). Esto es lo que permite consultar sin saber en qué variable vive un concepto ni cómo se codificó en cada estudio. El tipo de pregunta se conserva como metadato informativo, pero no cambia el camino de consulta.

### Modelo de datos

`cuestionario` (con `ref_estudio`), `individuo` (solo `id_persona` opaco), `pregunta` (con `codigo` externo, tipo, opciones), `respuesta` (formato largo, `embedding` obligatorio, `texto_embebido`, índice HNSW, unicidad por individuo+pregunta).

### Ingesta

Desde archivo de cuestionario + Excel ancho de respuestas (exports de las plataformas de campo, p. ej. Dooblo o Alchemer; el identificador de cada plataforma se guarda como alias de origen para resolver ingestas futuras a la misma persona):
- Despivote ancho → largo; cada columna se une a su pregunta por `codigo` (= encabezado del Excel).
- Resolución código/etiqueta por celda contra el mapa de opciones antes de embeber.
- Composición del texto como "pregunta → respuesta" y vectorización en lotes.
- Inserción idempotente (re-corridas no duplican).
- Embeddings: Voyage `voyage-3.5` (1024 dims) por defecto, detrás de una interfaz para cambiar de proveedor.

### Pipeline de consulta (tres etapas)

1. **Recuperación (recall).** Criterio → embedding → ANN sobre `respuesta.embedding` → pool top-N. Rápido y aproximado.
2. **Reranking (precisión).** Un cross-encoder evalúa consulta y candidato *en conjunto* y reordena, recortando a top-k. Mejora la discriminación de polaridad y valor ("a favor" vs "en contra", "fernet" vs "whisky"), donde la distancia coseno es débil, y baja el costo de la etapa siguiente. Reranker de Voyage por defecto, detrás de interfaz.
3. **Verificación con Claude (decisión).** Sobre el top-k, Claude lee las respuestas con procedencia, confirma valor/polaridad, combina criterios y devuelve el ranking con la evidencia de por qué entró cada individuo.

**Criterios combinados:** mejor puntaje por criterio y por individuo → regla de combinación → ranking único a nivel persona. Los criterios duros filtran; los difusos ordenan.

### Mejoras previstas del módulo (no bloquean el P0)

- **Salto de re-embedding por hash:** guardar hash del `texto_embebido` y no re-vectorizar respuestas sin cambios en re-ingestas.
- **Dedup de re-ingesta:** reingestar el mismo estudio sin duplicar `cuestionario` / `pregunta` *(ya implementado en Fase 1)*.
- **Políticas RLS** según el modelo de auth sobre las tablas del store semántico (hoy deny-all de base).
- **Logging de reidentificación:** registrar cada acceso deliberado que traduce `id_persona` a PII en la bóveda.
- **Regla de combinación configurable:** "Y" estricto (exige aproximación en todos los criterios) vs. laxo (suma lo disponible).

### Consideraciones futuras del módulo

Caché derivado de interpretaciones recurrentes (versionado por modelo, invalidable); capa de conceptos canónicos opcional; representación específica de respuesta múltiple y escalas; ponderadores/cuotas por estudio en el ranking.

---

## 3. Fases

### Fase 1 — Núcleo de personas e identidad ✅ *implementada y desplegada*

**Objetivo.** Sistema de registro de personas: enrolar panelistas con PII en la bóveda, emitir `id_persona` estable, gestionar paneles y membresías, registrar consentimiento por finalidad, y enganchar el fielding con la ingesta semántica.

**Requisitos.** R1.1 alta de panelista · R1.2 dedup de identidad (con cola de revisión para casos ambiguos) · R1.3 consentimiento por finalidad y su retiro con cascada · R1.4 paneles y membresía N:M · R1.5 fielding, convocatoria, participación y vínculo de respuestas por `ref_estudio` · R1.6 guardrail de PII (ninguna escritura de PII al store semántico).

**Estado.** Implementada. Del módulo semántico quedaron cubiertas las fundaciones: modelo de datos, bóveda e ingesta (incluida idempotencia y dedup de re-ingesta). **No** la consulta.

**DoD.** Enrolar/deduplicar/consentir; membresías múltiples; ingesta que asocia respuestas al `id_persona` con `ref_estudio` compartido; baja en cascada; auditoría con 0 columnas de PII en el semántico; pruebas de dedup, gate de consentimiento y guardrail de PII.

---

### Fase 2 — Consulta y salud del panel *(siguiente; spec detallado aparte)*

**Objetivo.** Hacer utilizable lo que la Fase 1 acumula: poder **interrogar** la base vectorial (hoy se escriben embeddings que no se pueden preguntar) y medir la salud del panel.

**Alcance.** Motor de consulta de tres etapas (recuperación → reranker → verificación con Claude); criterios combinados; las tres formas de consulta (demográfica local, semántica, mixta por puente de `id_persona`); composición vs. objetivo de universo/cuotas; tablero de participación.

**Requisitos.** R2.1 registro de participación por ola · R2.2 carga de universo de referencia · R2.3 composición descriptiva y brecha · R2.4 consulta demográfica pura (sin tocar el semántico) · R2.5 consulta mixta (puente) · R2.6 tablero de participación · **R2.7 recuperación semántica** · **R2.8 reranking** · **R2.9 verificación con Claude** · **R2.10 criterios combinados** · R2.11 gate de consentimiento en consulta.

*Detalle completo en `SPEC_fase2.md`.*

---

### Fase 3 — Palancas: muestreo, calidad y gamificación

**Objetivo.** Pasar de observar a accionar.

**Alcance.** Motor de muestreo por reglas (propone a quién invitar priorizando brechas de cuota y excluyendo sobre-convocados); chequeos de calidad (speeders, straightliners, duplicados); gamificación con ledger de puntos como moneda (auditable, saldo ≥ 0, vencimiento), catálogo y canje, ganando puntos **solo por participación de calidad**; bonos dirigidos a segmentos de cuota difíciles; **landing pública de auto-registro** de panelistas; **ingesta desde archivos SAV de SPSS** (con alta opcional de individuos en la misma carga); y dos mejoras operativas sobre la consulta: **exportar el resultado reidentificado** y **crear un panel a partir de un resultado**.

**Requisitos.** R3.1 muestreo por reglas · R3.2 chequeos de calidad · R3.3 ledger de puntos · R3.4 earn por calidad · R3.5 catálogo y canje · R3.6 bono dirigido · **R3.7 landing de auto-registro** · **R3.8 gestión de usuarios del sistema** · **R3.9 ingesta desde archivo SAV (SPSS)** · **R3.10 exportar el resultado reidentificado** · **R3.11 crear panel desde el resultado de una consulta**.

**R3.7 — Landing de auto-registro.** Formulario público donde una persona se inscribe al panel y **da su propio consentimiento** (más fuerte legalmente que un operador registrándolo por ella). Incluye: captura de datos patronímicos y de contacto, texto de consentimiento versionado por finalidad, dedup contra panelistas existentes al enviar, y estado de alta pendiente de aprobación por Equipos antes de entrar al panel. Emite `id_persona` por la misma vía que el alta interna (R1.1–R1.3), reutilizando identidad si la persona ya existe.

> **Nota de secuencia.** La Fase 1 implementó el alta **interna** (un operador enrola desde la app admin). El auto-registro es la vía pública y se construye acá; su **endurecimiento** (verificación de email/celular, anti-fraude) es R4.4 en la Fase 4, que asume esta landing ya existente.

**R3.8 — Gestión de usuarios del sistema (solapa Configuración).** Padrón de **personal de Equipos** (Firebase Auth + ficha en Firestore `usuarios/{uid}`), **no** panelistas: alta desde la app con email y rol, cambio de rol y desactivación, sin depender de `scripts/alta_usuario.js`. Alta idempotente por email (si la cuenta ya existe en Auth, solo actualiza ficha y rol). Permiso propio (`gestionar_usuarios`, solo `admin`); un admin no puede quitarse su propio rol ni desactivarse; toda alta, cambio y desactivación queda auditada. La clave inicial no se muestra de forma persistente (correo de establecer contraseña). El script de línea de comandos se conserva para el **bootstrap del primer admin**, que no puede crearse desde la app.

> **Alcance.** Son los cuatro roles ya existentes (`admin`, `operaciones`, `analista`, `dpo`) aplicados a todo el sistema: no incluye SSO, MFA ni permisos por panel. Es escalada de privilegios por diseño (un admin puede crear otros admins), de ahí el permiso acotado y la auditoría.

**R3.9 — Ingesta desde archivo SAV (SPSS).** Alternativa al Excel ancho: se sube un `.sav` y el sistema lo analiza para precargar solo, sin tipeo manual, las **variables** (código), el **texto de la pregunta** (variable labels), el **tipo** (inferido de measure/tipo de dato) y las **etiquetas de las cerradas** (value labels). El usuario revisa y corrige antes de confirmar; el resto del flujo de ingesta no cambia.

En la misma carga se elige cómo se resuelven los individuos, con dos modos:

- **Los panelistas ya existen:** se indica qué variable vincula cada fila con el individuo del sistema (el id de la plataforma de campo guardado en `alias_origen`). Es el comportamiento actual.
- **Crear los individuos en esta carga:** se indica qué variables contienen los datos patronímicos y cuál se toma como identificador del individuo. El sistema los da de alta en la bóveda durante la ingesta.

Criterios de aceptación:
- Dado un `.sav`, cuando se carga, entonces se precargan códigos, textos, tipos y mapeos de etiquetas, y quedan editables antes de confirmar.
- Dada una variable con value labels, entonces se trata como cerrada y sus códigos se resuelven a etiqueta antes de embeber.
- Dado el modo «crear individuos», entonces cada alta pasa por la **misma resolución de identidad que R1.2** (documento → email → nombre+fecha de nacimiento → nuevo), reutilizando `id_persona` si la persona ya existe y mandando a revisión los casos ambiguos: la ingesta no puede crear duplicados que el alta manual evitaría.
- Dado el modo «crear individuos», entonces los datos patronímicos se escriben **solo en la bóveda**; el guardrail de PII (R1.6) sigue aplicando sobre el store semántico.
- Dado que el archivo trae variables no declaradas o sin mapear, entonces el resultado de la ingesta las informa explícitamente (no se descartan en silencio).

> **Tensión a resolver: consentimiento.** El alta manual (R1.1) **rechaza** crear una persona sin consentimiento registrado. Crear individuos desde un SAV entra por otra puerta, así que hay que decidir con qué base legal se dan de alta: que el archivo traiga la evidencia de consentimiento (variable con fecha/versión), o que queden en un estado **pendiente de consentimiento** —contables y consultables solo cuando corresponda, y excluidos de convocatoria y de uso semántico hasta regularizarse—. Sin esta definición, R3.9 abre un camino para poblar la bóveda salteando la columna vertebral de cumplimiento. *(Decisión requerida antes de implementar el modo «crear individuos»; el modo «ya existen» no está afectado.)*

**R3.10 — Exportar el resultado reidentificado.** Hoy «Descargar CSV» re-ejecuta la consulta y exporta el ranking **seudonimizado** (`id_persona`, puntaje, evidencia), y la interfaz lo aclara. Reidentificar muestra los datos en pantalla y queda registrado. Falta el caso operativo intermedio: quien ya reidentificó necesita esa lista como archivo (para convocar, para pasarla al equipo de campo) y hoy la transcribe a mano. Se agrega una **acción distinta**, sin modificar el CSV actual: un botón «Descargar CSV con datos», visible solo después de reidentificar, que exporta el resultado ya resuelto en pantalla con los datos de bóveda de esos individuos.

Criterios de aceptación:
- Dado un ranking sin reidentificar, entonces la opción de exportar con datos no está disponible.
- Dado un resultado reidentificado, cuando se exporta con datos, entonces el CSV trae los mismos campos que la reidentificación devuelve (nombre, documento, email, celular, contacto, sexo, localidad, tramo etario), **sin** fecha de nacimiento exacta ni observaciones.
- Dada la exportación, entonces se registra en `reidentificacion` con motivo propio (`exportacion`), con actor, fecha y cantidad de personas: exportar es un evento auditable distinto de ver en pantalla.
- Dada la exportación, entonces se usa el resultado ya resuelto, sin volver a consultar ni re-reidentificar.
- El CSV seudonimizado actual se mantiene sin cambios y sigue siendo la opción por defecto.

> **Por qué dos botones y no uno.** Un archivo con PII sale del sistema y deja de estar bajo control: se copia, se reenvía, queda en una carpeta de descargas. Mantenerlo como acción separada y explícita evita que el botón de uso cotidiano se vuelva una fuga por defecto. Se excluyen los mismos dos campos que la reidentificación: la fecha exacta es un identificador fino que el tramo etario reemplaza para uso operativo, y `observaciones` es texto libre que puede contener datos sensibles con exigencias propias bajo URCDP. Conviene que el archivo lleve una marca visible de que contiene datos personales.

**R3.11 — Crear un panel desde el resultado de una consulta.** Desde un resultado de consulta (semántica, demográfica o mixta) se puede crear un panel nuevo con los individuos de ese resultado ya incorporados como miembros, sin pasar por el alta manual de membresías una por una.

Criterios de aceptación:
- Dado un resultado de consulta con al menos un individuo, cuando se crea un panel desde él, entonces se crea el panel y se da de alta una membresía por cada individuo del resultado.
- Dado un individuo que ya es miembro de ese panel, entonces la operación es idempotente (no duplica membresías).
- Dada la creación, entonces el panel registra que se originó en una consulta y con qué definición, para poder rastrear de dónde salió su composición.
- Dado un individuo sin `contacto_participacion` vigente, entonces no puede ser convocado desde ese panel (el gate de R1.3 sigue aplicando aunque la membresía exista).

> **Es una foto, no una vista viva.** El panel se crea con los individuos que el resultado tenía en ese momento; si después cambia el corpus o los parámetros, el panel no se actualiza solo. Re-ejecutar la consulta y volver a aplicarla sobre el mismo panel es una operación distinta (agregar miembros), y conviene decidir si se ofrece. Vale además que la creación deje registro equivalente al de reidentificación cuando el resultado venía de una consulta semántica: materializar un ranking en un panel es, en los hechos, fijar una lista de personas.

**Bloqueante.** Tratamiento fiscal del canje de premios en Uruguay.

---

### Fase 4 — Profundización

**Objetivo.** Optimizar y extender.

**Alcance.** Análisis longitudinal por `id_persona` a través de olas; muestreo como optimización con restricciones (cuota sujeto a fatiga y equidad de rotación); espejo de segmentadores al store semántico **condicional** a que la consulta mixta se vuelva intensiva; endurecimiento del auto-registro (verificación y anti-fraude/dedup en la landing).

**Requisitos.** R4.1 vista longitudinal · R4.2 optimizador de muestreo · R4.3 espejo de segmentadores (condicional) · R4.4 landing endurecida.

---

## 4. Métricas globales de éxito

**Leading (días a semanas)**
- Tiempo para responder "¿quiénes se aproximan a X?": de horas de cruce manual a minutos.
- Cierre de brecha de representatividad por ola.
- Tasa de respuesta por ola y su tendencia.
- Incidencia de sobre-convocatoria (objetivo: bajar).
- Precisión percibida: proporción de finalistas que la verificación confirma como correctos.

**Lagging (semanas a meses)**
- Reducción de pedidos de cruces manuales al equipo de datos.
- Churn del panel y representatividad sostenida a lo largo de olas.
- Tasa de canje y su relación con la participación.
- Cumplimiento operativo: SLA de bajas y retiros de consentimiento.

## 5. Preguntas abiertas

- **[legal]** ¿El consentimiento del alta cubre el perfilado semántico entre estudios, o requiere base/consentimiento separado? *(bloqueante para el uso semántico)*
- **[legal/finanzas]** Tratamiento fiscal del canje de premios en Uruguay. *(bloqueante — Fase 3)*
- **[legal]** Con qué base legal se dan de alta los individuos creados desde un archivo SAV (R3.9): evidencia de consentimiento en el propio archivo, o alta en estado pendiente de consentimiento. *(bloqueante para el modo «crear individuos»)*
- **[producto/ingeniería]** ¿Hasta dónde llega el motor de muestreo: reglas u optimización? (separa Fase 3 de Fase 4)
- **[datos]** Fuente y vigencia del universo de referencia para composición.
- **[legal/datos]** Plazos de retención por categoría de dato y purga por inactividad.
- **[ingeniería]** Tamaños de las tres etapas (top-N → top-k → finalistas) y proveedor de reranker.
- **[ingeniería]** Umbrales del puente para consulta mixta (qué lado filtra primero).
- **[datos]** Validar que Voyage sigue siendo la mejor opción (calidad en español rioplatense, costo, residencia de datos).
- **[producto]** Momento de invertir en anti-fraude/dedup de la landing.
