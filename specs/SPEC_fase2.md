# SPEC — Fase 2: Consulta y salud del panel

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**PRD de referencia:** `PRD_sistema_paneles_unificado.md`
**Estado:** Borrador para desarrollo · segundo sprint
**Precondición:** Fase 1 desplegada y con su DoD cerrado
**Última actualización:** 2026-09-10

---

## 1. Problem Statement

La Fase 1 dejó el sistema **acumulando sin poder responder**. Hoy se enrolan panelistas, se fieldan encuestas y se escriben embeddings en el store semántico, pero no existe nada que los interrogue: la base vectorial se llena con dato que no se puede preguntar. En paralelo, se registra quién fue convocado y quién respondió, pero ese dato crudo no se convierte en visibilidad: nadie puede ver si el panel está representativo ni cómo viene la participación.

Esta fase cierra las dos brechas: **poder consultar** (por concepto, por demografía, o combinando ambas) y **poder ver la salud del panel**. Es la fase que convierte la infraestructura de la Fase 1 en algo que un analista usa.

## 2. Goals

- **Consultar por concepto sin conocer el esquema:** describir un criterio en lenguaje natural y recibir un ranking de individuos, con evidencia de por qué entró cada uno.
- **Discriminar polaridad y valor**, donde la similitud vectorial sola falla, mediante reranking + verificación.
- **Resolver las tres formas de consulta** con el camino correcto en cada caso: demográfica pura sin tocar el store semántico, semántica pura, y mixta por puente de `id_persona`.
- **Combinar criterios a nivel persona**, incluso si provienen de estudios distintos.
- **Ver composición contra un objetivo** (universo de referencia o cuotas del cliente), no solo descriptivo.
- **Ver participación** por panelista y por ola.
- **Administrar los usuarios del sistema desde la app**, sin depender de la línea de comandos.

## 3. Non-Goals

- **No acciona sobre el panel.** El muestreo que *propone a quién invitar*, los chequeos de calidad y la gamificación son Fase 3. Acá se observa y se consulta.
- **No es segmentación exacta.** El resultado es ranking por aproximación; no reemplaza un filtro booleano.
- **No hay capa de conceptos canónicos** ni pre-clasificación de respuestas (se mantiene *schema-on-read*).
- **No espeja segmentadores al store semántico.** La consulta mixta se resuelve por puente; el espejo es Fase 4 y condicional.
- **Sin caché de interpretaciones recurrentes.**
- **No hay análisis longitudinal** (Fase 4: necesita olas acumuladas).
- **No se modifican los esquemas de la Fase 1**, salvo lo estrictamente necesario para composición (objetivo ya existe en el DDL).
- **La gestión de usuarios no incluye SSO, MFA ni permisos granulares por panel**: son los cuatro roles ya existentes, aplicados a todo el sistema.

## 4. User Stories

**Analista / investigador**
- Como analista, quiero describir un criterio en lenguaje natural y recibir individuos rankeados, para no depender de conocer la estructura del cuestionario.
- Como analista, quiero ver *por qué* entró cada individuo (su respuesta y de qué estudio salió), para confiar en el resultado y defenderlo ante un cliente.
- Como analista, quiero combinar criterios ("toma fernet" + "descontento con el gobierno") y obtener un ranking único a nivel persona.
- Como analista, quiero filtrar por criterios demográficos sin pasar por el proceso semántico, porque muchas consultas son solo demográficas.
- Como analista, quiero combinar demografía y concepto en una sola consulta.

**Responsable de panel / operaciones**
- Como responsable de panel, quiero ver la composición del panel contra un universo/cuotas, para saber qué segmentos me faltan.
- Como responsable de panel, quiero ver tasa de respuesta, tiempo desde el último contacto y distribución de convocatorias, para detectar fatiga.

**Administrador del sistema**
- Como admin, quiero dar de alta usuarios desde una solapa de Configuración, para no depender de que alguien corra un script en su máquina.
- Como admin, quiero cambiar el rol de un usuario o desactivarlo, para gestionar altas y bajas de personal sin tocar la consola de Firebase.

**Casos borde**
- Admin que intenta quitarse a sí mismo el rol admin o desactivarse → se rechaza (no quedarse sin administradores).
- Alta con un email que ya existe en Auth → no recrea la cuenta, solo actualiza su ficha y rol.
- Criterio sin ninguna respuesta relevante en el corpus → resultado vacío o de baja confianza, explicitado (no un ranking de ruido).
- Criterio de polaridad opuesta cercano en el espacio vectorial ("a favor" vs "en contra").
- Individuo sin consentimiento `uso_semantico` vigente → excluido de resultados semánticos.
- Consulta mixta donde el segmento demográfico es enorme (el puente no debe degradarse).
- Individuo presente en varios estudios: sus respuestas se colapsan a una sola entrada de ranking.
- Panel sin objetivo de composición cargado → se muestra descriptivo, y la brecha se indica como no disponible.

## 5. Requirements

### Must-Have (P0)

#### Motor de consulta semántica

**R2.7 — Recuperación semántica (recall).**
Criterio en lenguaje natural → embedding → ANN sobre `respuesta.embedding` → pool de candidatos top-N.
- Dado un criterio en lenguaje natural, cuando se ejecuta la consulta, entonces se vectoriza con el mismo proveedor/modelo/dimensión usado en la ingesta.
- Dado el pool recuperado, entonces cada candidato trae `id_persona`, la respuesta, su procedencia (estudio + pregunta) y su distancia.
- Dado un individuo con varias respuestas cercanas, entonces se colapsa a una entrada por individuo (mejor puntaje).
- [ ] `top_n` es configurable, con default documentado.

**R2.8 — Reranking (precisión).**
Un cross-encoder reordena el pool y lo recorta a top-k.
- Dado el pool top-N, cuando se rerankea, entonces se devuelve un top-k reordenado por relevancia consulta↔candidato.
- Dados dos candidatos de polaridad opuesta sobre el mismo tema, entonces el que coincide con la polaridad del criterio queda mejor rankeado que el opuesto.
- Dado que el reranker no está disponible (error o desactivado), entonces la consulta continúa con el orden de la etapa de recall y lo indica en la respuesta (degradación explícita, no silenciosa).
- [ ] El proveedor está detrás de una interfaz (como el de embeddings), con `top_k` configurable.

**R2.9 — Verificación con Claude (decisión).**
Sobre el top-k, Claude confirma valor y polaridad y arma la evidencia.
- Dado el top-k, cuando se verifica, entonces cada individuo devuelto trae un veredicto (cumple / no cumple / dudoso) y la respuesta concreta que lo justifica, con su procedencia.
- Dado un candidato cuya respuesta contradice el criterio (polaridad opuesta o valor distinto), entonces se marca como no cumple y no aparece en el ranking final.
- [ ] La verificación nunca inventa evidencia: cita texto de `valor_texto` existente.

**R2.10 — Criterios combinados.**
Varios criterios → puntaje por criterio y por individuo → regla de combinación → ranking único.
- Dados dos o más criterios, cuando se ejecuta la consulta, entonces cada individuo recibe un puntaje por criterio y un puntaje combinado.
- Dado un individuo con datos para un criterio y no para otro, entonces la regla de combinación lo trata según el modo elegido (estricto lo excluye; laxo lo incluye penalizado).
- Dado un criterio duro y uno difuso, entonces el duro filtra y el difuso ordena.
- [ ] El modo (estricto/laxo) es un parámetro de la consulta.

**R2.11 — Gate de consentimiento en consulta.**
- Dado un individuo sin `uso_semantico` vigente, cuando se ejecuta cualquier consulta semántica, entonces no aparece en el pool ni en el resultado.

#### Formas de consulta

**R2.4 — Consulta demográfica pura (local).**
- Dada una consulta solo con factores demográficos (sexo, localidad, tramo etario), cuando se ejecuta, entonces se resuelve enteramente en el store local y **no** accede al store semántico.
- [ ] Verificable: la consulta no abre conexión al semántico (test).

**R2.5 — Consulta mixta (puente por `id_persona`).**
- Dada una consulta demográfica + semántica, cuando el segmento demográfico es más selectivo, entonces se filtra primero en local y se puntúa sobre ese subconjunto de `id_persona`.
- Dada una consulta mixta donde el criterio semántico es más selectivo, entonces se recupera y rerankea primero en semántico y se filtra demografía en local.
- [ ] La estrategia elegida queda registrada en la respuesta (para diagnóstico).
- [ ] No se duplican columnas demográficas en el store semántico.

#### Salud del panel

**R2.1 — Registro de participación por ola.**
- Dada una encuesta fieldeada, cuando concluye la convocatoria, entonces por cada panelista queda registrado si fue convocado, si respondió, y su nº acumulado de convocatorias. *(Base capturada en Fase 1; acá se consolida y expone.)*

**R2.2 — Carga de universo de referencia.**
- Dado un objetivo de composición (censo o cuotas del cliente) por dimensión y categoría, cuando se carga, entonces queda disponible como referencia del panel.
- [ ] Las proporciones de una dimensión se validan (suman ~1).

**R2.3 — Composición descriptiva y brecha.**
- Dado un panel con objetivo cargado, cuando se pide composición, entonces se devuelve la distribución observada y la brecha por segmento contra el objetivo.
- Dado un panel sin objetivo cargado, entonces se devuelve solo el descriptivo, indicando que la brecha no está disponible.

**R2.6 — Tablero de participación.**
- Dado un panel, cuando se abre el tablero, entonces se ven tasa de respuesta, tiempo desde el último contacto y distribución de convocatorias.

#### Configuración: gestión de usuarios del sistema

**R2.12 — Alta y administración de usuarios desde la app (solapa Configuración).**
Hoy el alta se hace por línea de comandos (`scripts/alta_usuario.js`, con ADC), lo que restringe la operación a quien tenga la máquina configurada. Esta solapa la lleva a la interfaz.

> **Ámbito.** Son los **usuarios del sistema** (personal de Equipos: Firebase Auth + ficha en Firestore `usuarios/{uid}`), **no** panelistas. No se toca la bóveda ni el store semántico: este padrón nunca contiene datos de panelistas.

- Dado un actor con permiso de administración, cuando abre Configuración → Usuarios, entonces ve el padrón con nombre, email, rol y estado (activo/inactivo).
- Dado un alta con email y rol válidos, cuando se confirma, entonces se crea la cuenta en Auth y su ficha en Firestore con ese rol, y la persona puede ingresar.
- Dado un email que ya existe en Auth, cuando se da de alta, entonces no se recrea la cuenta: solo se actualiza/crea su ficha y su rol (idempotente).
- Dado un rol fuera de los cuatro válidos (`admin`, `operaciones`, `analista`, `dpo`), entonces el alta se rechaza.
- Dado un usuario existente, cuando se le cambia el rol, entonces sus permisos cambian en la siguiente operación.
- Dado un usuario que se desactiva, cuando intenta operar, entonces se le rechaza el acceso (sin borrar su histórico de acciones).
- Dado un alta nueva, entonces la clave inicial **no** se muestra de forma persistente: se envía un correo de establecer/restablecer contraseña, o se muestra una sola vez y se recomienda el cambio.
- [ ] Solo `admin` puede administrar usuarios (permiso propio, p. ej. `gestionar_usuarios`).
- [ ] Un actor no puede quitarse a sí mismo el rol `admin` ni desactivarse (evita quedarse sin administradores).
- [ ] Toda alta, cambio de rol y desactivación queda registrada con quién y cuándo.
- [ ] El script de línea de comandos sigue funcionando como vía de emergencia (bootstrap del primer admin).

### Nice-to-Have (P1)

- **Exportar el resultado de una consulta** (CSV con `id_persona`, puntaje y evidencia) para operar con él.
- **Guardar consultas frecuentes** como definición reutilizable (sin cachear resultados).
- **Diagnóstico de consulta**: mostrar tamaños de cada etapa, tiempos y estrategia del puente.
- **Umbral de confianza**: marcar resultados de baja confianza cuando la mejor distancia supera un límite.
- **Composición por cruce de dos dimensiones** (p. ej. sexo × tramo etario).
- **Salto de re-embedding por hash** en re-ingestas (venía de P1 del módulo semántico).
- **Logging de reidentificación**: registrar cada traducción deliberada de `id_persona` a PII.

### Future Considerations (P2)

- Caché derivado de interpretaciones recurrentes (versionado por modelo, invalidable).
- Ponderadores/cuotas por estudio reflejados en el ranking.
- Representación específica de respuesta múltiple y escalas al embeber.
- Espejo de segmentadores al semántico (Fase 4, condicional a uso real de la mixta).

## 6. Contratos de API (propuestos)

- `POST /consultas` — ejecuta una consulta. Cuerpo: lista de criterios (cada uno `{tipo: "semantico"|"demografico", ...}`), `modo` (estricto/laxo), `top_n`, `top_k`, `panel_id` opcional. Devuelve ranking con puntaje, veredicto, evidencia y procedencia por individuo, más metadatos de diagnóstico (etapas, estrategia). → **R2.4, R2.5, R2.7–R2.11**
- `GET /paneles/{id}/composicion` — descriptivo + brecha por dimensión. → **R2.3**
- `PUT /paneles/{id}/objetivo` — carga/actualiza el objetivo de composición. → **R2.2**
- `GET /paneles/{id}/participacion` — tablero: tasa de respuesta, último contacto, distribución de convocatorias. → **R2.1, R2.6**
- `GET /usuarios` — padrón del sistema (nombre, email, rol, estado). → **R2.12**
- `POST /usuarios` — alta: crea cuenta en Auth (si no existe) + ficha con rol. Idempotente por email. → **R2.12**
- `PATCH /usuarios/{uid}` — cambia rol y/o estado activo. → **R2.12**

Todas las rutas de `/usuarios` requieren el permiso `gestionar_usuarios` (solo `admin`).

## 7. Dependencias

- **Fase 1 cerrada** (identidad, consentimiento, ingesta poblando el store semántico).
- **Datos reales ingestados**: sin corpus, el motor no se puede evaluar.
- **Proveedor de reranker** contratado/configurado (Voyage por defecto) y clave en Secret Manager.
- **API de Claude** disponible desde las Cloud Functions para la etapa de verificación.
- **Firebase Admin SDK** disponible desde las Cloud Functions para crear cuentas en Auth (la función ya corre con service account: no necesita ADC como el script local).
- **[legal]** Consentimiento `uso_semantico` resuelto: es el gate de R2.11 *(bloqueante para consultas semánticas sobre datos reales)*.

## 8. Definition of Done

- [ ] Una consulta de un criterio semántico devuelve individuos rankeados con evidencia y procedencia.
- [ ] Un caso de polaridad opuesta preparado a propósito **no** aparece en el ranking final (lo filtra rerank + verificación).
- [ ] Una consulta combinada devuelve ranking único a nivel persona, y el modo estricto/laxo cambia el resultado como se espera.
- [ ] Una consulta solo demográfica no abre conexión al store semántico (test automatizado).
- [ ] Una consulta mixta devuelve resultados correctos por ambas estrategias del puente, y registra cuál usó.
- [ ] Individuos sin `uso_semantico` vigente nunca aparecen en resultados semánticos (test).
- [ ] Composición muestra descriptivo y brecha contra un objetivo cargado; sin objetivo, solo descriptivo.
- [ ] Tablero de participación muestra tasa de respuesta, último contacto y distribución de convocatorias.
- [ ] Si el reranker falla, la consulta degrada explícitamente y lo informa.
- [ ] Latencia de una consulta típica dentro del objetivo acordado (ver §9).
- [ ] Un admin da de alta un usuario desde Configuración y esa persona puede ingresar con su rol.
- [ ] Dar de alta un email ya existente actualiza su ficha sin recrear la cuenta.
- [ ] Un no-admin no puede acceder ni operar la gestión de usuarios (test).
- [ ] Un admin no puede quitarse su propio rol admin ni desactivarse (test).
- [ ] Los cambios de rol y las desactivaciones quedan registrados con autor y fecha.

## 9. Success Metrics

**Leading**
- Tiempo para responder "¿quiénes se aproximan a X?" (objetivo: minutos, desde horas de cruce manual).
- Precisión percibida: % de individuos del ranking final que el analista confirma como correctos.
- Tasa de descarte en la verificación: % de candidatos del top-k que Claude marca como no cumple (mide cuánto está aportando el filtro).
- Latencia p50/p95 por forma de consulta (demográfica / semántica / mixta).
- Adopción: nº de analistas que ejecutan al menos una consulta por semana.

**Lagging**
- Reducción de pedidos de cruces manuales al equipo de datos.
- Cierre de brecha de representatividad entre olas (habilitado por la visibilidad de composición).
- Reutilización entre estudios: nº de estudios consultados.

## 10. Riesgos y preguntas abiertas

- **[legal]** Alcance del consentimiento para perfilado semántico entre estudios. *(bloqueante)*
- **[ingeniería]** Tamaños de las tres etapas (top-N → top-k → finalistas): calibrar recall vs. costo con datos reales.
- **[ingeniería]** Costo por consulta (embedding + reranker + Claude) y su previsibilidad; definir techo aceptable.
- **[ingeniería]** Recall del índice ANN cuando se aplica filtro previo (caso mixto con segmento chico): evaluar fuerza bruta sobre el subconjunto.
- **[producto]** Semántica exacta de la regla de combinación (estricto vs. laxo, ponderación): requiere validación con analistas.
- **[producto]** Cómo comunicar incertidumbre en la UI para que un ranking aproximado no se lea como una lista exacta.
- **[datos]** Fuente y vigencia del universo de referencia para composición.
- **[datos]** Validar que Voyage sigue siendo la mejor opción (embedding y reranker) al momento de construir.
- **[seguridad]** La gestión de usuarios desde la app es escalada de privilegios por diseño: un admin puede crear otros admins. Requiere que el permiso esté bien acotado y las acciones auditadas.
- **[operación]** Bootstrap: el primer admin no puede crearse desde la app (no hay quién lo autorice). Se mantiene el script de línea de comandos para eso.
- **[riesgo de producto]** Sin corpus suficiente, el motor parecerá malo por falta de datos y no por diseño: conviene ingestar varios estudios reales antes de evaluar calidad.
