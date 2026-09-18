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
- **No espeja atributos demográficos al store semántico.** Decisión firme, no diferida: los segmentadores quedan autoritativos en la bóveda y el store semántico se mantiene como contenido puro. Además de simplificar, reduce el **riesgo mosaico**: cuantos menos cuasi-identificadores hay del lado semántico, menos reidentificable es ese dataset por combinación de respuestas. La consulta mixta se resuelve puenteando por `id_persona` (R2.5).
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

> **Nota de secuencia.** La Fase 1 implementó el alta **interna** (un operador enrola desde la app admin). El auto-registro es la vía pública y se construye acá; su **endurecimiento** (verificación de email/celular, anti-fraude) es R4.3 en la Fase 4, que asume esta landing ya existente.

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

**Alcance.** Dos bloques independientes. **4A — Contacto:** endurecimiento del auto-registro (verificación y anti-fraude/dedup en la landing), preferencias de canal y envío por WhatsApp Flow. **4B — Inteligencia:** análisis longitudinal por `id_persona` a través de olas (con historial de atributos, que es a la vez una corrección) y muestreo como optimización con restricciones (cuota sujeta a fatiga y equidad de rotación).

**Requisitos.** R4.1 vista longitudinal · R4.2 optimizador de muestreo · R4.3 landing endurecida · **R4.4 preferencias de canal de contacto** · **R4.5 envío de encuestas por WhatsApp Flow**.

**R4.4 — Preferencias de canal de contacto.** Por persona y canal (`whatsapp`, `email`, `telefono`, `sms`), con evidencia de cuándo y cómo se obtuvo. Es un eje **distinto** del consentimiento por finalidad: `contacto_participacion` responde «¿puedo contactarla?», la preferencia responde «¿por dónde?». Para contactar por un canal hacen falta los dos. Se captura en los tres caminos de alta ya construidos en Fase 3 (alta manual, ingesta con creación de individuos, landing), agregando la captura sin rehacerlos.

**R4.5 — Envío de encuestas por WhatsApp Flow.** Una encuesta puede configurarse con un Flow de WhatsApp publicado y su plantilla aprobada; al convocar, se ofrece enviarlo por WhatsApp a los convocados que cumplan **los dos ejes** y tengan celular válido. El sistema **solo envía**: las respuestas se bajan de Meta y se ingestan por el flujo de siempre. Cada envío lleva el `id_persona` como `flow_token`, para que esa ingesta mapee directo.

Criterios de aceptación del bloque 4A (R4.3, R4.4, R4.5):
- Dada una inscripción de la landing, entonces el correo —y el celular si lo declara— se verifican con un **código de un solo uso** antes de que la inscripción exista. Sin verificar, **no llega a la cola de aprobación**.
- Dados los códigos, entonces vencen, cuentan los intentos y están limitados por tasa **por origen y por destino**; se guardan hasheados, igual que el origen.
- Dado un envío automatizado, entonces se bloquea con un desafío, que se valida **antes de emitir el código** —que es lo que cuesta plata y lo que puede molestar a un tercero—.
- Dada una inscripción en la cola, entonces quien aprueba ve los **candidatos parecidos** (por documento, correo, celular o nombre y fecha). El documento exacto se resuelve solo; el resto **se propone y no se fusiona**.
- Dada una persona y un canal, entonces la preferencia se registra con **el texto con que se obtuvo**, su origen y su fecha; revocarla no afecta a los otros canales.
- Dado el canal `whatsapp`, entonces exige **celular en E.164**; el celular se normaliza en los tres caminos de alta, y uno que no se puede normalizar **no voltea el alta**.
- Dado un envío, entonces salen solo quienes cumplen los dos ejes y tienen celular válido, y los excluidos se informan **discriminados por motivo**: sin consentimiento, sin preferencia, sin celular, celular inválido.
- Dada una encuesta de Flow, entonces el sistema valida contra Meta que el Flow esté **publicado** y la plantilla **aprobada** antes de permitir convocar, en vez de fallar al enviar.
- Dado un envío fallido, entonces se puede reintentar **sin reenviar a quien ya recibió**.

> **Por qué la preferencia de canal es un eje aparte.** El consentimiento autoriza a contactar pero no dice por qué medio: alguien pudo aceptar que lo llamen y no querer mensajes en su WhatsApp personal. Y del lado de Meta, la política de mensajería exige opt-in previo para los mensajes que inicia el negocio; mandar sin él lleva a bloqueos, baja el *quality rating* y termina en la restricción de la cuenta. El canal se quema con el primer envío masivo a gente que no lo pidió.

> **Por qué la verificación va antes de que exista la inscripción.** Un contacto sin verificar cubre dos casos y los dos son malos: un dato inventado, que ensucia la cola de aprobación, y —peor— el dato de otra persona, que es inscribir a alguien sin que se entere. Por eso es una precondición de escribir, no una casilla más del formulario.

**R4.1 — Análisis longitudinal.** Tres partes. **(a) Historial de atributos:** cada valor demográfico pasa a valer en un intervalo de vigencia, y el anterior se conserva en vez de perderse. **(b) Series comparables:** el analista declara que preguntas de olas distintas son la misma medición y mapea sus opciones a un vocabulario común; el sistema sugiere candidatas por similitud semántica pero no agrega ninguna sola. **(c) Vista longitudinal:** la línea de tiempo de una persona a través de las olas, y la matriz de cuántos se movieron entre categorías de una serie de una ola a la siguiente.

**R4.2 — Optimizador de muestreo.** Minimiza la distancia entre la composición de la muestra y el objetivo de cuotas, sujeto a restricciones duras (consentimiento vigente, preferencia del canal a usar, pertenencia al panel, tamaño pedido) y penalizando las blandas (fatiga y equidad de rotación, con pesos configurables por panel). Cada individuo incluido es explicable. Ante una cuota infactible lo informa con las alternativas cuantificadas en vez de violar una restricción, y **no elige por su cuenta**. Las reglas de R3.1 no se reemplazan: se mantienen como referencia y respaldo, y la diferencia entre las dos selecciones se puede ver.

Criterios de aceptación del bloque 4B (R4.1, R4.2):
- Dado un cambio de valor en un atributo, entonces el anterior **se conserva** con su período de vigencia.
- Dada una consulta o una composición **sin** referencia temporal, entonces devuelve exactamente lo que devolvía antes del historial.
- Dada la composición de una ola pasada, entonces se calcula con los valores vigentes entonces —incluidos los derivados: la edad de una persona en 2024 es la que tenía en 2024— y con la membresía que existía en esa fecha.
- Dada una composición retroactiva con objetivo cargado, entonces se **avisa** que la brecha compara la foto de entonces contra el universo de hoy: los objetivos no se historizan.
- Dada una serie declarada, entonces se ve el movimiento entre categorías de una ola a otra; quien está en una sola ola **no entra en la matriz**, porque ponerlo en la diagonal diría que no cambió.
- Dada una serie, entonces el sistema **sugiere** preguntas candidatas de otras olas y no agrega ninguna sola; las que entran por sugerencia quedan marcadas como tales.
- Dada una opción sin mapear a una categoría común, entonces **no se cuenta** en la comparación y se informa aparte, igual que «(sin dato)» en la composición.
- Dada la línea de tiempo de una persona identificada, entonces la consulta queda registrada como **reidentificación**.
- Dada una selección optimizada, entonces cada individuo sale con el déficit que tenía su segmento al entrar, lo que aportó a la brecha y lo que costó en fatiga y en equidad.
- Dada una restricción dura, entonces **ningún peso la compra**: quien no consintió, o no aceptó el canal, no entra por más brecha que haya.
- Dada una cuota infactible, entonces se informan las tres salidas con su costo medido —reducir el tamaño, aflojar la fatiga, aceptar la brecha— y **el sistema no elige**.

> **Por qué el historial de atributos es una corrección y no una feature.** Sin él, recalcular la composición de una ola de hace un año la calculaba con la demografía de hoy. Una cuota que cerró con 30 % de menores de 35 puede mostrar 22 % un año después sin que nadie se haya ido del panel, solo porque esa gente cumplió años. El número no estaba incompleto: estaba mal, y se veía bien. Por eso R4.1.a va primero en el bloque.

> **Por qué la comparabilidad la declara el analista.** Es la misma distinción de siempre: el sistema no canoniza respuestas. Canonizar automáticamente congelaría una equivalencia que puede ser falsa —dos preguntas parecidas que miden cosas distintas— y lo haría en el dato, donde ya no se ve. Declararla la hace explícita, revisable y responsabilidad de quien sabe qué se preguntó y para qué.

> **Por qué el optimizador propone y no convoca, y por qué es un voraz.** Convocar sigue siendo un acto explícito de un responsable, igual que con las reglas. Y la selección es por un voraz y no por programación entera porque el requisito pide que cada individuo sea **explicable**: un óptimo de programación entera da una asignación mejor en el margen y ninguna explicación por persona. Una selección que nadie puede defender ante un investigador no sirve para decidir.

> **Detalle completo en `SPEC_fase4.md`.**

> **Descartado: espejo de segmentadores al store semántico.** Figuraba como requisito condicional (copiar sexo, localidad y tramo etario al store semántico para que la consulta mixta no tuviera que abrir la bóveda). Se descarta por tres razones: la mayoría de las consultas son **puramente demográficas** y ya se resuelven enteras en la bóveda sin tocar embeddings; el puente por conjuntos de `id_persona` (R2.5) cubre el caso mixto sin duplicar nada; y con el catálogo de atributos configurable (R3.14) el conjunto a espejar deja de ser fijo y crece, lo que multiplicaría los cuasi-identificadores del lado semántico — justo el riesgo mosaico que el diseño evita. **El store semántico se mantiene como contenido puro.**

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
