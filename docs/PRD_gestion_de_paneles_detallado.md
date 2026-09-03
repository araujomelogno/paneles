# PRD detallado — Sistema de gestión de paneles de investigación de mercado

**Contexto:** Equipos Consultores · investigación de mercado
**Estado:** Borrador para revisión · versión detallada
**Última actualización:** 2026-08-27
**Incorpora el módulo:** Consulta semántica — su mecánica interna (modelo vectorial, ingesta, embeddings, ranking, verificación) está especificada en «PRD — Módulo de consulta semántica». La consulta como feature de producto se especifica acá (Fase 2).

---

## 1. Marco

### Problem Statement
Equipos administra paneles de investigación de mercado —conjuntos estables de personas a las que se fieldan encuestas de forma repetida—. Un panel es un activo que se degrada solo si no se cuida: pierde representatividad, sus panelistas se fatigan y abandonan, se sobre-encuesta a los mismos, y entra dato de baja calidad. No existe hoy un sistema que mantenga ese activo sano, que decida a quién invitar equilibrando representatividad y fatiga, ni que sostenga el ciclo de cumplimiento que exige retener PII identificada de forma permanente. El costo de no resolverlo es representatividad perdida, dato sesgado y exposición legal bajo URCDP.

### Goals
- Mantener paneles **sanos**, no solo almacenarlos (representatividad, participación, fatiga, calidad).
- **Un solo registro de personas**: el enrolamiento emite la identidad estable (`id_persona`) de toda la plataforma.
- **Muestreo que equilibra cuota y fatiga**.
- **Cumplimiento como columna vertebral** (consentimiento, retención, baja en cascada, minimización).
- **Participación como palanca**, no solo reporte.
- Habilitar consulta **demográfica, semántica y mixta**.

### Non-Goals
- No re-especifica la **mecánica interna** del motor semántico (modelo vectorial, ingesta, embeddings, algoritmo de ranking, verificación): eso vive en el spec del Módulo de consulta semántica. La **consulta como feature** (formas demográfica/semántica/mixta) sí se especifica acá (Fase 2).
- No es un CRUD de personas: almacenar sin accionar sobre salud del panel queda fuera del valor.
- No espeja atributos demográficos al store remoto por defecto (se mantiene como contenido puro por riesgo mosaico).
- No declara anonimización (siguen siendo datos personales bajo URCDP).
- No automatiza el tratamiento fiscal de premios.

### Arquitectura
Un sistema, dos módulos, dos stores:
- **Local:** bóveda de identidad (PII + atributos demográficos autoritativos), paneles y membresías, consentimiento, muestreo, gamificación. Sirve la gestión de panel y la **consulta puramente demográfica** sin tocar embeddings.
- **Remoto:** base vectorial (embeddings + `id_persona`), para la **consulta semántica**.
- **Consulta mixta:** puente por conjuntos de `id_persona` entre stores, sin duplicar columnas.

### Personas
- **Responsable de panel / operaciones** — mantiene la salud del panel y decide reclutamiento/invitaciones.
- **Analista / investigador** — fieldea encuestas y consulta (demográfica/semántica/mixta/longitudinal).
- **Panelista** — se registra, consiente, responde, gana y canjea puntos, ejerce derechos.
- **DPO / cumplimiento** — consentimiento, retención, bajas.

---

## 2. Fases

Cada fase se especifica con: objetivo, alcance, historias, requisitos con criterios de aceptación, dependencias, criterios de salida (DoD), métricas y riesgos.

---

### Fase 1 — Núcleo de personas e identidad

**Objetivo.** Tener operativo el sistema de registro de personas: enrolar panelistas con su PII en la bóveda, emitir un `id_persona` estable, gestionar paneles y membresías, registrar consentimiento por finalidad, y enganchar el fielding de encuestas con la ingesta semántica. Al cierre, se puede enrolar gente, armar paneles, fieldear una encuesta y que sus respuestas caigan en el store semántico asociadas al `id_persona` correcto.

**Alcance — dentro**
- Bóveda: entidad `persona` con PII (sexo, fecha de nacimiento, localidad, nombre, email, celular, contacto, observaciones) y atributos derivados (tramo etario).
- Emisión de `id_persona` (uuid aleatorio, opaco) y **dedup en el alta**.
- Entidad `panel` y membresía N:M panelista↔panel.
- Consentimiento por finalidad (contacto/participación; y, separado, uso semántico entre estudios), con estado, timestamp y versión del texto consentido.
- Fielding: entidad encuesta/ola, asociación encuesta↔panel, y el gancho que vincula respuestas ingestadas al `id_persona`.

**Alcance — fuera** (fases posteriores): participación como métrica, composición, consulta, muestreo, gamificación.

**Historias de usuario**
- Como responsable de panel, quiero enrolar un panelista con sus datos y su consentimiento, para sumarlo al panel en regla.
- Como responsable de panel, quiero que al enrolar a alguien que ya es panelista se detecte el duplicado, para no tener dos registros de la misma persona.
- Como responsable de panel, quiero que un panelista pertenezca a varios paneles sin duplicarlo.
- Como analista, quiero fieldear una encuesta a un panel y que las respuestas queden asociadas al `id_persona`, para poder consultarlas después.

**Requisitos y criterios de aceptación**

R1.1 — Alta de panelista en la bóveda.
- Dado un formulario de alta completo, cuando se confirma, entonces se crea una `persona` con su PII y se emite un `id_persona`.
- Dado que falta el consentimiento, cuando se intenta confirmar, entonces el alta se rechaza.

R1.2 — Dedup de identidad.
- Dado un alta cuyos datos coinciden con una `persona` existente (por documento o email), cuando se procesa, entonces se reutiliza su `id_persona` y no se crea un duplicado.
- Dado un match parcial/ambiguo, cuando se procesa, entonces se marca para revisión en vez de fusionar automáticamente.

R1.3 — Consentimiento por finalidad.
- Dado el alta, cuando se registra el consentimiento, entonces queda guardada la finalidad, el estado (vigente), el timestamp y la versión del texto.
- El consentimiento de "uso semántico entre estudios" se registra como una finalidad separada de la de "contacto/participación".

R1.4 — Paneles y membresía N:M.
- Dado un panelista, cuando se lo agrega a un segundo panel, entonces su pertenencia al primero no se altera.

R1.5 — Fielding y vínculo de respuestas.
- Dada una encuesta fieldeada a un panel, cuando se ingestan sus respuestas, entonces cada respuesta queda asociada al `id_persona` del panelista que la respondió.

R1.6 — Separación de stores.
- En ningún caso se escribe PII en el store remoto; solo viaja el `id_persona`.

**Dependencias:** esquema de bóveda y de store remoto (diseñados); capacidad de ingesta (PRD previo) en su forma básica para el gancho de fielding.

**Criterios de salida (DoD).** Se puede enrolar, deduplicar, consentir, armar paneles con membresías múltiples, fieldear una encuesta a un panel y ver sus respuestas ingestadas asociadas al `id_persona` correcto; cero PII en el store remoto.

**Métricas de la fase**
- 0 duplicados no detectados en el set de prueba de dedup.
- 100% de altas con consentimiento registrado por finalidad.
- 0 fugas de PII al store remoto (auditoría de esquema).

**Riesgos y preguntas de la fase**
- **[datos]** Calidad de la clave de dedup: si no hay documento ni email confiable, el dedup se degrada.
- **[legal]** Alcance del consentimiento del alta frente al uso semántico entre estudios *(bloqueante para habilitar lo semántico)*.

---

### Fase 2 — Salud del panel y consulta

**Objetivo.** Convertir los datos en visibilidad y respuestas: registrar participación por panelista/encuesta, medir composición contra un objetivo, y habilitar consulta demográfica (local), semántica (remoto, ya existente) y mixta (puente por `id_persona`).

**Alcance — dentro**
- Registro de participación: convocado sí/no, respondió sí/no, nº acumulado de convocatorias, fecha de último contacto.
- Carga del universo de referencia / cuotas del cliente.
- Composición: descriptivo **y** brecha contra el objetivo.
- Reporte de participación (tablero).
- Consulta demográfica (local, sobre la bóveda).
- Consulta mixta (puente por `id_persona`, con estrategia según selectividad).

**Alcance — fuera:** el muestreo que *acciona* (fase 3), gamificación.

**Historias de usuario**
- Como responsable de panel, quiero ver la composición del panel contra un universo/cuotas, para saber qué segmentos me faltan.
- Como responsable de panel, quiero ver cuántas veces se convocó a cada panelista y su historial de respuesta, para gestionar fatiga.
- Como analista, quiero filtrar panelistas por criterios demográficos sin pasar por el proceso semántico.
- Como analista, quiero combinar filtro demográfico con criterio semántico y obtener un ranking.

**Requisitos y criterios de aceptación**

R2.1 — Registro de participación por ola.
- Dada una encuesta fieldeada, cuando concluye la convocatoria, entonces por cada panelista queda registrado si fue convocado, si respondió, y se incrementa su nº de convocatorias.

R2.2 — Carga de universo de referencia.
- Dado un objetivo de composición (censo o cuotas del cliente), cuando se carga, entonces queda disponible como referencia para el cálculo de brecha.

R2.3 — Composición descriptiva y brecha.
- Dado un panel con objetivo cargado, cuando se pide composición, entonces se muestra la distribución descriptiva y la brecha por segmento contra el objetivo.

R2.4 — Consulta demográfica pura (local).
- Dada una consulta solo con factores demográficos, cuando se ejecuta, entonces se resuelve enteramente en el store local y no accede al store remoto.

R2.5 — Consulta mixta (puente).
- Dada una consulta demográfica + semántica, cuando el segmento demográfico es chico, entonces se filtra primero local y se mide distancia sobre ese subconjunto de `id_persona` en el remoto.
- Dada una consulta mixta donde el criterio semántico es el selectivo, entonces se rankea primero en el remoto y se filtra demografía en el local.

R2.6 — Reporte de participación.
- Dado un panel, cuando se abre el tablero, entonces se ven tasa de respuesta, tiempo desde último contacto y distribución de convocatorias.

**Dependencias:** Fase 1; capacidad de **consulta semántica** (PRD previo).

**Criterios de salida (DoD).** Se puede ver composición vs objetivo, historial de convocatorias por panelista, y correr las tres formas de consulta con resultados correctos; la demográfica pura no accede al remoto.

**Métricas de la fase**
- Exactitud de la brecha de composición contra un objetivo conocido (validación manual).
- Latencia aceptable de la consulta mixta en el peor caso.
- Cobertura: % de encuestas con participación registrada.

**Riesgos y preguntas de la fase**
- **[datos]** Fuente y vigencia del universo de referencia.
- **[ingeniería]** Umbrales del puente (qué lado filtra primero) para la consulta mixta.

---

### Fase 3 — Palancas: muestreo, calidad y gamificación

**Objetivo.** Pasar de observar a accionar: muestreo por reglas que propone a quién invitar (cuota vs fatiga), chequeos de calidad que filtran dato malo, y gamificación que premia **participación de calidad** para sostener engagement.

**Alcance — dentro**
- Motor de muestreo **por reglas**: dado un objetivo de cuota y umbrales de fatiga, propone lista de invitación priorizando brechas y excluyendo sobre-convocados.
- Chequeos de calidad: speeders (tiempo mínimo), straightliners (baja varianza), duplicados.
- Gamificación: ledger de puntos como moneda (auditable, sin saldos negativos, con vencimiento), regla de earn por participación de calidad, catálogo de premios y canje.
- Bonos de puntos dirigidos a segmentos de cuota difíciles (gamificación como palanca de representatividad).

**Alcance — fuera:** optimización con restricciones (fase 4), automatización fiscal del canje.

**Historias de usuario**
- Como responsable de panel, quiero que el sistema me proponga a quién invitar para cerrar una brecha sin sobre-convocar a los escasos.
- Como responsable de panel, quiero que las respuestas de baja calidad se detecten y no cuenten para puntos.
- Como panelista, quiero ganar puntos por participar bien y canjearlos por premios del catálogo.
- Como responsable de panel, quiero dar un bono de puntos a un segmento difícil, para incentivar su participación.

**Requisitos y criterios de aceptación**

R3.1 — Motor de muestreo por reglas.
- Dado un objetivo de cuota y umbrales de fatiga, cuando se pide una propuesta de invitación, entonces se priorizan los segmentos con brecha y se excluye a los panelistas por encima del umbral de convocatoria reciente/acumulada.
- Dado un panelista sin consentimiento vigente, entonces nunca aparece en la propuesta.

R3.2 — Chequeos de calidad.
- Dada una respuesta más rápida que el mínimo, o con varianza por debajo del umbral, o duplicada, cuando se ingesta, entonces se marca con su motivo de calidad.

R3.3 — Ledger de puntos como moneda.
- El saldo de puntos es auditable, nunca negativo, y los puntos tienen política de vencimiento.

R3.4 — Earn por calidad.
- Dado que un panelista completó una encuesta **y** pasó los chequeos de calidad, cuando se liquida, entonces gana puntos; si no pasó calidad, no gana.

R3.5 — Catálogo y canje.
- Dado saldo suficiente, cuando el panelista canjea un premio, entonces se descuenta el saldo y se registra el canje; el canje se rechaza si no hay saldo.

R3.6 — Bono dirigido.
- Dado un segmento con brecha, cuando se configura un bono, entonces la participación de ese segmento otorga puntos extra.

**Dependencias:** Fase 2 (participación + composición); resolución del tratamiento fiscal del canje *(bloqueante para el canje)*.

**Criterios de salida (DoD).** El sistema propone invitaciones respetando cuota y fatiga; las respuestas de baja calidad se marcan y no generan puntos; un panelista puede ganar y canjear puntos con saldo auditable.

**Métricas de la fase**
- Incidencia de sobre-convocatoria (objetivo: bajar).
- Tasa de dato de baja calidad detectado.
- Tasa de canje y efecto en la tasa de respuesta.

**Riesgos y preguntas de la fase**
- **[legal/finanzas]** Tratamiento fiscal del canje en Uruguay *(bloqueante)*.
- **[producto]** Calibración de umbrales de calidad y de fatiga (falsos positivos/negativos).

---

### Fase 4 — Profundización

**Objetivo.** Optimizar y extender: análisis longitudinal a nivel individuo, muestreo como optimización con restricciones, espejo de segmentadores si la consulta mixta lo justifica, y endurecimiento de la landing.

**Alcance — dentro**
- Análisis longitudinal: seguir al mismo `id_persona` a través de olas (evolución de postura).
- Muestreo por **optimización con restricciones**: cerrar brecha de cuota sujeto a fatiga y equidad de rotación.
- Espejo de segmentadores gruesos (sexo, localidad, tramo etario) al store remoto, **condicional** a que la consulta mixta se vuelva intensiva.
- Endurecimiento del auto-registro: verificación (email/celular) y anti-fraude/dedup en la landing.

**Alcance — fuera:** automatización fiscal.

**Historias de usuario**
- Como analista, quiero ver cómo evolucionó la postura de una persona a través de olas.
- Como responsable de panel, quiero que el muestreo optimice la cuota respetando fatiga y rotación, no solo por reglas.
- Como responsable de panel, quiero que la landing verifique identidad y evite duplicados/fraude.

**Requisitos y criterios de aceptación**

R4.1 — Vista longitudinal por persona.
- Dado un `id_persona` con respuestas en varias olas, cuando se abre su vista longitudinal, entonces se muestra la evolución de sus respuestas comparables en el tiempo.

R4.2 — Optimizador de muestreo.
- Dado un objetivo de cuota y restricciones de fatiga/rotación, cuando se solicita, entonces se devuelve una selección que maximiza el cierre de brecha sujeto a las restricciones.

R4.3 — Espejo de segmentadores (condicional).
- Dado que la consulta mixta supera un umbral de uso/latencia, cuando se activa el espejo, entonces los segmentadores gruesos quedan disponibles seudonimizados en el remoto y la consulta mixta deja de abrir la bóveda.

R4.4 — Landing endurecida.
- Dado un auto-registro, cuando se envía, entonces se verifica el email/celular y se deduplica contra panelistas existentes antes de confirmar.

**Dependencias:** olas acumuladas (para el longitudinal); métricas de uso de la consulta mixta (para decidir el espejo).

**Criterios de salida (DoD).** Se puede ver la evolución de una persona a través de olas; el muestreo optimiza cuota bajo restricciones; la landing verifica identidad y evita duplicados.

**Métricas de la fase**
- Uso del análisis longitudinal.
- Mejora de representatividad con el optimizador vs reglas.
- Reducción de duplicados/fraude en el alta.

**Riesgos y preguntas de la fase**
- **[producto/datos]** ¿El espejo se justifica? Depende de datos reales de uso de la consulta mixta.
- **[ingeniería]** Complejidad del optimizador vs beneficio marginal sobre las reglas.

---

## 3. Métricas globales de éxito

**Leading:** cierre de brecha de representatividad por ola; tasa de respuesta y su tendencia; incidencia de sobre-convocatoria; tiempo para fieldear una ola con muestra válida.

**Lagging:** churn del panel; representatividad sostenida a lo largo de olas; tasa de canje y su relación con participación; cumplimiento operativo (SLA de bajas/retiros de consentimiento).

## 4. Preguntas abiertas transversales

- **[legal]** Alcance del consentimiento del alta frente al perfilado semántico entre estudios *(bloqueante — Fase 1/2)*.
- **[legal/finanzas]** Tratamiento fiscal del canje de premios en Uruguay *(bloqueante — Fase 3)*.
- **[producto/ingeniería]** Hasta dónde llega el motor de muestreo (reglas vs optimización) — separa Fase 3 de Fase 4.
- **[datos]** Fuente y vigencia del universo de referencia para composición.
- **[legal/datos]** Plazos de retención por categoría de dato y purga por inactividad.
- **[producto]** Momento de invertir en anti-fraude/dedup de la landing.

## 5. Dependencia entre módulos

El **módulo de consulta semántica** (su mecánica interna está en el spec homónimo) debe estar operativo para las partes semánticas y mixtas del producto (Fase 2 y siguientes). Es un módulo de este mismo sistema, no un producto externo. Las preguntas abiertas bloqueantes deben resolverse antes de las fases que las tocan.
