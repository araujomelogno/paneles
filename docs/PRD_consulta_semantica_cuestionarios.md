# PRD — Módulo de consulta semántica

**Contexto:** Equipos Consultores · investigación de mercado
**Estado:** Borrador para revisión
**Última actualización:** 2026-08-27
**Rol en el sistema:** módulo del Sistema de gestión de paneles (no un producto aparte). Este documento especifica la **mecánica interna** del motor: modelo de datos vectorial, ingesta y embeddings, algoritmo de recuperación/ranking y verificación con Claude. La **consulta como feature de producto** (formas demográfica, semántica y mixta, y el puente por `id_persona`) se especifica en el PRD de gestión de paneles (Fase 2).

---

## Problem Statement

Equipos corre muchos estudios de investigación de mercado cuyos microdatos son heterogéneos: cada cuestionario tiene sus propias variables y llega como export ancho de Excel (una fila por individuo, una columna por pregunta). Cuando un analista quiere encontrar individuos que responden a un criterio conceptual —"consume tal bebida", "está descontento con el gobierno"—, hoy necesita saber de antemano qué preguntas y qué columnas corresponden a ese criterio, algo que cambia estudio a estudio y que las herramientas actuales (tabulados, Power BI) no resuelven de forma semántica. El costo de no resolverlo es tiempo de analista en cruces manuales, imposibilidad de agrupar individuos por concepto a través de estudios, y criterios que quedan sin explorar porque nadie sabe en qué variable viven.

## Goals

- **Consulta por concepto sin conocer el esquema.** Un analista describe un criterio en lenguaje natural y obtiene un ranking de individuos que se le aproximan, sin saber qué pregunta ni qué columna lo contiene.
- **Criterios combinados a nivel persona.** Permitir combinar varios criterios y devolver individuos que mejor aproximan al conjunto, incluso cuando cada criterio proviene de un estudio distinto.
- **Tratamiento uniforme de cerradas y abiertas.** Toda respuesta se evalúa por similitud semántica; no se depende de que el analista sepa el tipo de pregunta ni sus códigos.
- **Identidad de persona estable entre estudios.** Reconocer al mismo individuo a través de estudios distintos para poder cruzar sus respuestas.
- **Separación de PII por diseño.** Mantener los datos identificatorios fuera del store analítico (seudonimización) para reducir riesgo de reidentificación y alcance de cumplimiento.

## Non-Goals

- **No es segmentación exacta ni exhaustiva.** El resultado es un ranking por aproximación, no "todos los que cumplen exactamente"; no reemplaza un filtro booleano preciso.
- **No hay capa de conceptos canónicos ni pre-clasificación de respuestas.** Se descartó deliberadamente a favor de interpretación en tiempo de consulta (*schema-on-read*), para no congelar errores de canonización en el dato.
- **No reemplaza la tabulación cuantitativa.** Ponderación, representatividad y significancia estadística siguen en las herramientas actuales.
- **No se declara anonimización.** El dataset seudonimizado sigue siendo dato personal bajo URCDP/GDPR; es una salvaguarda, no una exención.
- **No es tiempo real.** La ingesta es por lotes, por estudio.
- **Sin caché de interpretaciones recurrentes en v1.** Se difiere hasta validar el flujo base.

## User Stories

**Analista de investigación**
- Como analista, quiero describir un criterio en lenguaje natural y recibir un ranking de individuos que se le aproximan, para no depender de conocer la estructura del cuestionario.
- Como analista, quiero combinar varios criterios y obtener las personas que mejor aproximan a todos, para armar segmentos conceptuales.
- Como analista, quiero ver las respuestas relevantes de cada individuo del ranking, con su procedencia (estudio y pregunta), para confiar en por qué entró.

**Responsable de datos / operaciones**
- Como responsable de datos, quiero ingestar un estudio nuevo a partir de su archivo de cuestionario y su Excel de respuestas, para incorporarlo sin trabajo manual de mapeo.
- Como responsable de datos, quiero que el mismo individuo sea reconocido entre estudios mediante un identificador estable, para habilitar el cruce.

**Responsable de cumplimiento (DPO)**
- Como DPO, quiero que la PII quede en una bóveda separada y que el store analítico solo maneje tokens opacos, para minimizar el riesgo de reidentificación.
- Como DPO, quiero poder atender derechos del titular operando sobre la bóveda, para cumplir con URCDP.

**Casos borde**
- Individuo presente en un solo estudio (no todos los criterios son evaluables sobre él).
- Criterio sin preguntas relevantes en ningún estudio (resultado vacío o de baja confianza).
- Polaridad opuesta cercana en el espacio vectorial ("a favor" vs "en contra").
- Celdas vacías, y mezcla de códigos y etiquetas según la pregunta.

## Requirements

### Must-Have (P0)

**Modelo de datos seudonimizado.** `cuestionario`, `individuo` (con `id_persona` opaco), `pregunta` (con `codigo` externo, tipo y opciones), `respuesta` (formato largo, `embedding` obligatorio, `texto_embebido`).
- [ ] `individuo` no guarda PII, solo el token opaco.
- [ ] `respuesta` tiene índice vectorial HNSW y unicidad por (individuo, pregunta).

**Bóveda de identidad.** Store separado que mapea PII ↔ `id_persona`, con token aleatorio y opaco (no derivado de la PII).
- [ ] La PII nunca se escribe en el store semántico.
- [ ] El mismo individuo obtiene el mismo `id_persona` entre estudios.

**Pipeline de ingesta.** Desde archivo de cuestionario + Excel ancho de respuestas.
- Dado un Excel ancho con encabezados = `codigo` de pregunta, cuando se ingesta, entonces se despivota a formato largo y cada celda se une a su pregunta por código.
- Dada una celda de pregunta cerrada, cuando el valor es un código, entonces se resuelve a su etiqueta usando el mapa de opciones antes de embeber.
- Dado un texto a embeber, entonces se compone como "pregunta → respuesta" y se vectoriza en lotes.
- [ ] La inserción de respuestas es idempotente (re-corridas no duplican).

**Consulta semántica simple.** Criterio en lenguaje natural → embedding → recuperación ANN sobre `respuesta.embedding` → colapso a individuo por mejor distancia → ranking.

**Consulta combinada.** Varios criterios → mejor distancia por criterio y por individuo → regla de combinación → ranking único a nivel persona.

**Verificación con Claude.** Sobre los finalistas, Claude lee las respuestas con procedencia y confirma valor/polaridad antes de devolver el ranking, con la evidencia de por qué entró cada uno.

### Nice-to-Have (P1)

- **Regla de combinación configurable:** "Y" estricto (exige aproximación en todos los criterios) vs. laxo (suma lo disponible).
- **Salto de re-embedding por hash:** guardar hash del `texto_embebido` y no re-vectorizar respuestas sin cambios en re-ingestas.
- **Dedup de re-ingesta:** reingestar el mismo estudio sin duplicar `cuestionario`/`pregunta`.
- **Políticas RLS** según el modelo de auth, sobre las cuatro tablas.
- **Logging de reidentificación:** registrar cada acceso deliberado que traduce `id_persona` a PII en la bóveda.

### Future Considerations (P2)

- **Caché derivado de interpretaciones recurrentes:** vista materializada, versionada por modelo e invalidable, para amortizar segmentos frecuentes sin congelar el dato.
- **Capa de conceptos canónicos opcional:** solo si aparece necesidad de segmentos precisos y repetibles; mantener el diseño sin bloquearla.
- **Preguntas de respuesta múltiple y escalas:** representación específica al embeber.
- **Ponderadores/cuotas por estudio** para reflejar representatividad en el ranking.

## Success Metrics

**Leading (días a semanas)**
- Tiempo para responder una consulta "¿quiénes se aproximan a X?": de horas de cruce manual a minutos.
- Adopción: nº de analistas que corren al menos una consulta por semana.
- Tasa de criterios respondibles sin consulta manual del esquema (objetivo: alta mayoría).
- Precisión percibida: proporción de finalistas que Claude confirma como correctos en la verificación.

**Lagging (semanas a meses)**
- Reducción de pedidos de cruces manuales al equipo de datos.
- Reutilización del sistema entre estudios (nº de estudios ingestados y consultados).
- Satisfacción de los analistas con la calidad de los rankings.

## Open Questions

- **[datos/legal]** ¿El identificador de persona es opaco, o es un valor sensible (p. ej. cédula) que debe tokenizarse en la bóveda antes de llegar al store semántico? *(bloqueante para la ingesta)*
- **[legal]** Base legal y consentimiento para el perfilado entre estudios bajo URCDP; tratamiento de datos sensibles (opinión política, salud) que aparezcan dentro de respuestas abiertas. *(bloqueante)*
- **[datos]** Proveedor y dimensión de embeddings (Voyage 3.5 / OpenAI / self-hosted): costo, calidad en español rioplatense y residencia de datos.
- **[ingeniería]** Dónde vive la orquestación de la consulta: n8n vs. un backend propio.
- **[producto/datos]** Semántica exacta de la regla de combinación multi-criterio (estricto vs. laxo, ponderación); requiere validación con analistas.
- **[ingeniería]** Tamaño del pool de candidatos (top-k) que pasa a la verificación de Claude: balance recall vs. costo.
- **[producto]** ¿Se mantiene el enfoque estrictamente semántico también para cerradas, o se reconsidera una vía de match exacto si la precisión no alcanza?

## Timeline Considerations

- **Fase 1 — Fundaciones (en curso):** esquema del store seudonimizado, bóveda de identidad, pipeline de ingesta.
- **Fase 2 — Consulta:** recuperación semántica, combinación de criterios y verificación con Claude.
- **Fase 3 — Endurecimiento:** RLS, logging de reidentificación, dedup de re-ingesta, salto de re-embedding por hash.
- **Fase 4 — Opcionales:** caché derivado, ponderadores, interfaz de consulta para analistas.

**Dependencias:** resolución de las dos preguntas abiertas bloqueantes (identificador opaco y base legal) antes de poblar el store con datos reales.
