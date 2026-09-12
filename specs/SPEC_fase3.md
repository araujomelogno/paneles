# SPEC — Fase 3: Palancas, crecimiento y operación

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**PRD de referencia:** `PRD_sistema_paneles_unificado.md`
**Estado:** Borrador para desarrollo · tercer sprint
**Precondición:** Fase 2 desplegada y con su DoD cerrado
**Última actualización:** 2026-09-11

---

## 1. Problem Statement

Las Fases 1 y 2 dejaron el sistema **capaz de observar, pero no de actuar**. Se ve la composición del panel contra su objetivo y se ve quién participa, pero nadie decide a quién invitar: esa decisión sigue siendo manual y, por lo tanto, sigue produciendo los dos males que degradan un panel — se convoca siempre a los mismos hasta quemarlos, y los segmentos que faltan no se cubren. Tampoco hay nada que distinga una respuesta trabajada de una completada al azar, ni un incentivo que sostenga la participación en el tiempo.

A eso se suman tres fricciones operativas concretas que aparecieron al usar el sistema: el panel solo crece por alta manual de un operador (no hay vía pública de inscripción), la carga de cuestionarios exige tipear a mano textos y códigos que el archivo de origen ya trae, y el resultado de una consulta muere en la pantalla — no se puede llevar a un archivo ni convertir en un panel de trabajo.

Esta fase cierra las dos brechas: **convertir la visibilidad en acción** sobre la salud del panel, y **sacar la fricción** del trabajo diario.

## 2. Goals

- **Decidir a quién invitar** equilibrando brecha de cuota y fatiga, en vez de a ojo.
- **Distinguir dato bueno de dato malo** y que esa distinción tenga consecuencias.
- **Sostener la participación** con un incentivo que premie calidad, no volumen.
- **Hacer crecer el panel** por una vía pública, con consentimiento dado por el propio titular.
- **Administrar el sistema desde el sistema**, sin línea de comandos.
- **Reducir el tipeo y el error** en la carga de estudios, aprovechando la metadata que el archivo ya trae.
- **Que un resultado de consulta sea accionable**: exportable e instanciable como panel.

## 3. Non-Goals

- **No es optimización de muestreo.** Acá el motor es por **reglas**; la optimización con restricciones (cuota sujeta a fatiga y equidad de rotación) es Fase 4 (R4.2).
- **No hay análisis longitudinal** (Fase 4).
- **No se endurece la landing.** Verificación de contacto y anti-fraude son Fase 4 (R4.4). Esta fase la construye en su forma simple.
- **No incluye SSO, MFA ni permisos por panel** en la gestión de usuarios: son los cuatro roles existentes.
- **No automatiza el tratamiento fiscal** del canje de premios.
- **No convierte el CSV seudonimizado actual en un CSV con PII**: la exportación con datos es una acción nueva y separada.
- **No hay paneles dinámicos**: un panel creado desde una consulta es una foto, no una vista que se actualiza sola.
- **No se modifica el pipeline de consulta** (recuperación → reranker → verificación) ni el modelo de embeddings.

## 4. Composición de la fase

Once requisitos de naturaleza distinta. Se agrupan en tres bloques con dependencias internas; el orden sugerido es A → B → C, pero B y C son independientes entre sí.

| Bloque | Requisitos | Tema |
|---|---|---|
| **3A — Salud del panel accionable** | R3.1, R3.2, R3.3, R3.4, R3.5, R3.6 | Muestreo, calidad y gamificación |
| **3B — Crecimiento y administración** | R3.7, R3.8 | Landing pública y usuarios del sistema |
| **3C — Fricción operativa** | R3.9, R3.10, R3.11 | Ingesta SAV, exportación e instanciación de paneles |

**Dependencias internas:** R3.4 (earn por calidad) depende de R3.2 (chequeos de calidad) — sin calidad medida, no hay con qué condicionar el punto. R3.5 (canje) depende de R3.3 (ledger). R3.6 (bono dirigido) depende de R3.3 y de la composición de Fase 2. R3.1 (muestreo) depende de la composición y la participación de Fase 2.

---

## 5. User Stories

**Responsable de panel / operaciones**
- Como responsable de panel, quiero que el sistema me proponga a quién invitar para cerrar una brecha de cuota, sin sobre-convocar a los segmentos escasos.
- Como responsable de panel, quiero ver por qué alguien quedó excluido de una propuesta (fatiga, consentimiento, cuota ya cubierta), para poder decidir con criterio.
- Como responsable de panel, quiero dar un bono de puntos a un segmento difícil de alcanzar, para incentivar su participación.
- Como responsable de panel, quiero crear un panel con los individuos que salieron de una consulta, para trabajar con ese grupo sin armarlo a mano.

**Analista / investigador**
- Como analista, quiero cargar un `.sav` y que el sistema tome solo las variables, los textos de las preguntas y las etiquetas, para no tipearlos ni equivocarme.
- Como analista, quiero, si la base trae gente que no está en el sistema, darla de alta en la misma carga.
- Como analista, quiero llevarme la lista reidentificada a un archivo para pasarla al equipo de campo.

**Panelista**
- Como persona interesada, quiero inscribirme al panel desde un formulario público y dar mi consentimiento yo mismo.
- Como panelista, quiero ganar puntos por participar bien y canjearlos por premios de un catálogo.
- Como panelista, quiero ver mi saldo de puntos y qué puedo canjear.

**Administrador del sistema**
- Como admin, quiero dar de alta usuarios y cambiar roles desde la app, sin depender de que alguien corra un script.

**DPO / cumplimiento**
- Como DPO, quiero que las inscripciones públicas registren consentimiento con su versión de texto, igual que las altas internas.
- Como DPO, quiero que exportar una lista con datos personales quede registrado como evento distinto de verla en pantalla.

**Casos borde**
- Segmento con brecha grande pero cuyos miembros están todos sobre-convocados (tensión cuota vs. fatiga sin salida limpia).
- Panelista que responde rápido pero bien (falso positivo de speeder).
- Export de campo **sin datos de tiempo**: no se puede detectar speeders.
- Canje con saldo justo y dos solicitudes concurrentes (no puede quedar saldo negativo).
- Inscripción pública de alguien que ya es panelista.
- `.sav` con variable labels vacías o truncadas.
- `.sav` en modo «crear individuos» con filas que ya existen en la bóveda.
- Crear un panel desde un resultado donde algunos individuos ya son miembros.

---

## 6. Requirements

### Bloque 3A — Salud del panel accionable

#### R3.1 — Motor de muestreo por reglas (P0)
Propone a quién invitar para una encuesta, priorizando segmentos con brecha de cuota y excluyendo a quienes superan los umbrales de fatiga.

- Dado un panel con objetivo de composición cargado y una encuesta, cuando se pide una propuesta, entonces se devuelve una lista de individuos priorizada por brecha de segmento.
- Dado un individuo por encima del umbral de convocatorias recientes o acumuladas, entonces no aparece en la propuesta.
- Dado un individuo sin `contacto_participacion` vigente, entonces nunca aparece en la propuesta.
- Dada una propuesta, entonces cada exclusión relevante es explicable (fatiga, consentimiento, cuota cubierta): el responsable tiene que poder entender por qué falta alguien.
- Dado que un segmento con brecha no tiene individuos elegibles, entonces se informa explícitamente en vez de devolver la lista más corta sin aviso.
- [ ] Los umbrales de fatiga son configurables (no hardcodeados) y tienen default documentado.
- [ ] La propuesta no convoca por sí sola: es una sugerencia que el responsable confirma.

#### R3.2 — Chequeos de calidad (P0)
Marca respuestas sospechosas al ingestar.

- Dada una respuesta con duración menor al mínimo configurado, entonces se marca `sospechoso` con motivo `speeder`.
- Dada una respuesta con varianza por debajo del umbral en una batería de escalas, entonces se marca `sospechoso` con motivo `straightliner`.
- Dada una respuesta duplicada para el mismo individuo y encuesta, entonces se marca con motivo `duplicado`.
- Dada una respuesta que pasa todos los chequeos, entonces queda `ok`.
- Dado un export **sin datos de tiempo**, entonces el chequeo de speeder no se aplica y se informa que no se pudo evaluar, en vez de marcar todo como `ok` (silencio ≠ aprobado).
- [ ] Los umbrales son configurables por estudio: lo que es rápido en un cuestionario de 5 minutos no lo es en uno de 30.
- [ ] Marcar es reversible: un responsable puede revisar y revertir un `sospechoso` (con registro de quién).

#### R3.3 — Ledger de puntos (P0)
El saldo se maneja como moneda, no como un campo.

- Dado un movimiento de puntos, entonces queda registrado con tipo (`earn`, `canje`, `ajuste`, `vencimiento`), cantidad, motivo y fecha.
- Dado un saldo, entonces se calcula como suma de movimientos y **nunca es negativo**.
- Dados puntos con fecha de vencimiento cumplida, entonces se registra un movimiento de `vencimiento` que los descuenta (no se borran movimientos).
- [ ] El saldo es auditable: siempre se puede reconstruir de dónde salió.

#### R3.4 — Ganar puntos por participación de calidad (P0)
- Dado un panelista que respondió una encuesta **y** su participación quedó `calidad_estado = 'ok'`, cuando se liquida, entonces gana los puntos configurados.
- Dado un panelista cuya participación quedó `sospechoso`, entonces **no** gana puntos.
- Dada una participación que pasa de `sospechoso` a `ok` tras revisión, entonces se puede liquidar el punto pendiente.
- Dado un mismo par (panelista, encuesta), entonces no se puede liquidar dos veces.

#### R3.5 — Catálogo de premios y canje (P0)
- Dado un premio activo con stock y un panelista con saldo suficiente, cuando canjea, entonces se descuenta el saldo y se registra el canje.
- Dado un saldo insuficiente, entonces el canje se rechaza y el saldo no cambia.
- Dadas dos solicitudes concurrentes con saldo para una sola, entonces solo una prospera (sin saldo negativo).
- Dado un canje, entonces su estado sigue el ciclo `solicitado → entregado | cancelado`; una cancelación devuelve los puntos como movimiento nuevo.

#### R3.6 — Bono de puntos dirigido (P1)
- Dado un segmento con brecha de cuota, cuando se configura un bono, entonces la participación de calidad de ese segmento otorga puntos extra durante su vigencia.
- Dado un bono vencido, entonces deja de aplicarse sin afectar los puntos ya otorgados.

---

### Bloque 3B — Crecimiento y administración

#### R3.7 — Landing de auto-registro (P0)
Formulario público donde una persona se inscribe y **da su propio consentimiento**.

- Dado el formulario público, cuando alguien lo envía completo, entonces se registra su consentimiento con finalidad y **versión del texto**, igual que un alta interna.
- Dado un envío sin aceptar el consentimiento, entonces la inscripción se rechaza.
- Dada una inscripción, entonces pasa por la **misma resolución de identidad que R1.2**: reutiliza `id_persona` si la persona ya existe, y manda a revisión los casos ambiguos.
- Dada una inscripción válida, entonces queda **pendiente de aprobación** y no entra a ningún panel hasta que alguien de Equipos la aprueba.
- Dada una inscripción pendiente, entonces no puede ser convocada ni aparecer en consultas semánticas.
- [ ] El texto de consentimiento es editable y versionado: cambiarlo no altera lo que ya consintieron los inscriptos anteriores.
- [ ] La landing es pública (sin login) y no expone ningún dato de otros panelistas.

#### R3.8 — Gestión de usuarios del sistema (P0)
Padrón de **personal de Equipos** (Firebase Auth + ficha en Firestore), **no** panelistas.

- Dado un actor con `gestionar_usuarios`, cuando abre Configuración → Usuarios, entonces ve el padrón con nombre, email, rol y estado.
- Dado un alta con email y rol válido, entonces se crea la cuenta en Auth y su ficha con ese rol.
- Dado un email que ya existe en Auth, entonces no se recrea la cuenta: solo se actualiza ficha y rol (idempotente).
- Dado un rol fuera de los cuatro válidos, entonces el alta se rechaza.
- Dado un usuario desactivado, cuando intenta operar, entonces se le rechaza el acceso sin borrar su histórico.
- [ ] Solo `admin` administra usuarios.
- [ ] Un admin no puede quitarse su propio rol ni desactivarse.
- [ ] Toda alta, cambio de rol y desactivación queda auditada con autor y fecha.
- [ ] La clave inicial no se muestra de forma persistente (correo de establecer contraseña).
- [ ] El script de línea de comandos se conserva para el bootstrap del primer admin.

---

### Bloque 3C — Fricción operativa

#### R3.9 — Ingesta desde archivo SAV (SPSS) (P0)
Alternativa al Excel ancho, con precarga automática de la metadata.

**Análisis del archivo:**
- Dado un `.sav`, cuando se carga, entonces se precargan códigos (variables), textos de pregunta (variable labels), tipo (inferido de measure/tipo de dato) y mapeos de etiquetas (value labels).
- Dada la precarga, entonces todo queda **editable** antes de confirmar: la metadata de SPSS suele venir truncada o críptica, y el texto es lo que se embebe.
- Dada una variable con value labels, entonces se trata como cerrada y sus códigos se resuelven a etiqueta antes de embeber.
- Dadas variables no declaradas o sin mapear, entonces el resultado las informa explícitamente (no se descartan en silencio).
- [ ] El parseo corre en el **backend** (no en el navegador): no hay librería cliente confiable para `.sav` y los archivos pueden ser grandes.

**Modo «los panelistas ya existen»:**
- Dado ese modo, entonces se indica qué variable vincula cada fila con el individuo del sistema (el id de plataforma guardado en `alias_origen`), y el comportamiento es el actual.
- Dada una fila cuyo id no resuelve a ningún individuo, entonces se informa como no mapeada.

**Modo «crear los individuos en esta carga»:**
- Dado ese modo, entonces se indica qué variables contienen los datos patronímicos y cuál es el identificador del individuo.
- Dada cada alta, entonces pasa por la **misma resolución de identidad que R1.2** (documento → email → nombre+fecha de nacimiento → nuevo): la ingesta no puede crear duplicados que el alta manual habría evitado.
- Dado un caso ambiguo, entonces va a la cola de revisión, no se fusiona.
- Dados los datos patronímicos, entonces se escriben **solo en la bóveda**; el guardrail de PII (R1.6) sigue aplicando sobre el store semántico.
- [ ] El resultado informa cuántos individuos se crearon, cuántos se reutilizaron y cuántos quedaron en revisión.

> ⚠ **Bloqueante — consentimiento.** El alta manual (R1.1) rechaza crear una persona sin consentimiento registrado. Este modo entra por otra puerta: hay que decidir con qué base legal se dan de alta — que el archivo traiga evidencia de consentimiento (variable con fecha/versión), o que queden en estado **pendiente de consentimiento**, excluidos de convocatoria y de uso semántico hasta regularizarse. Sin esta definición, R3.9 permite poblar la bóveda salteando la columna vertebral de cumplimiento. *El modo «ya existen» no está afectado y puede implementarse antes.*

#### R3.10 — Exportar el resultado reidentificado (P0)
- Dado un ranking sin reidentificar, entonces la opción de exportar con datos no está disponible.
- Dado un resultado reidentificado, cuando se exporta con datos, entonces el CSV trae los mismos campos que la reidentificación devuelve (nombre, documento, email, celular, contacto, sexo, localidad, tramo etario), **sin** fecha de nacimiento exacta ni observaciones.
- Dada la exportación, entonces se registra en `reidentificacion` con motivo propio (`exportacion`), con actor, fecha y cantidad de personas.
- Dada la exportación, entonces se usa el resultado ya resuelto, sin volver a consultar ni re-reidentificar.
- [ ] El CSV seudonimizado actual se mantiene sin cambios y sigue siendo la opción por defecto.
- [ ] El archivo lleva una marca visible de que contiene datos personales (nombre de archivo y/o encabezado).

#### R3.11 — Crear un panel desde el resultado de una consulta (P0)
- Dado un resultado de consulta (semántica, demográfica o mixta) con al menos un individuo, cuando se crea un panel desde él, entonces se crea el panel y se da de alta una membresía por cada individuo del resultado.
- Dado un individuo que ya es miembro, entonces la operación es idempotente.
- Dada la creación, entonces el panel registra que se originó en una consulta y con qué definición, para poder rastrear de dónde salió su composición.
- Dado un individuo sin `contacto_participacion` vigente, entonces la membresía puede existir pero no puede ser convocado (el gate de R1.3 sigue aplicando).
- [ ] Si el resultado venía de una consulta semántica, la creación deja registro equivalente al de reidentificación: materializar un ranking en un panel es, en los hechos, fijar una lista de personas.

---

## 7. Contratos de API (propuestos)

| Ruta | Requisito |
|---|---|
| `POST /encuestas/{id}/muestreo` — propuesta de invitación con motivos de exclusión | R3.1 |
| `GET/PUT /paneles/{id}/umbrales-fatiga` | R3.1 |
| `POST /encuestas/{id}/calidad` — corre chequeos · `PATCH /participacion/{id}/calidad` — revisión manual | R3.2 |
| `GET /panelistas/{id_persona}/puntos` — saldo y movimientos · `POST /puntos/liquidar` | R3.3, R3.4 |
| `GET/POST/PATCH /premios` · `POST /canjes` · `PATCH /canjes/{id}` | R3.5 |
| `POST /paneles/{id}/bonos` | R3.6 |
| `POST /inscripciones` (**pública**) · `GET /inscripciones` · `POST /inscripciones/{id}/aprobar` | R3.7 |
| `GET /usuarios` · `POST /usuarios` · `PATCH /usuarios/{uid}` | R3.8 |
| `POST /encuestas/{id}/sav/analizar` — devuelve metadata precargada · `POST /encuestas/{id}/sav/ingesta` | R3.9 |
| `POST /consultas/csv-identificado` | R3.10 |
| `POST /paneles/desde-consulta` | R3.11 |

## 8. Dependencias

- **Fase 2 cerrada**: composición y participación (para R3.1 y R3.6), consulta y reidentificación (para R3.10 y R3.11).
- **`pyreadstat`** (o equivalente) en las Cloud Functions para leer `.sav` (R3.9).
- **Datos de tiempo en los exports de campo**, si se quiere detección de speeders (R3.2). Verificar con Dooblo/Alchemer qué trae cada export.
- **[legal]** Base legal del alta por SAV *(bloqueante — R3.9 modo crear)*.
- **[legal/finanzas]** Tratamiento fiscal del canje en Uruguay *(bloqueante — R3.5)*.
- **[legal]** Texto de consentimiento de la landing, revisado y versionado *(bloqueante — R3.7)*.
- **Proveedor de premios / logística de entrega** definida antes de abrir el canje.

## 9. Definition of Done

**Bloque 3A**
- [ ] El muestreo propone una lista que prioriza brechas y excluye sobre-convocados y sin consentimiento, con motivos explicables.
- [ ] Un segmento con brecha sin elegibles se informa explícitamente.
- [ ] Los chequeos marcan speeders, straightliners y duplicados; un export sin tiempos informa que no pudo evaluarse.
- [ ] Una participación `sospechoso` no genera puntos; revertida a `ok`, sí.
- [ ] El saldo nunca queda negativo, ni con canjes concurrentes (test).
- [ ] El saldo es reconstruible desde los movimientos.

**Bloque 3B**
- [ ] Una inscripción pública registra consentimiento con versión, queda pendiente de aprobación y no es convocable hasta aprobarse.
- [ ] Una inscripción de alguien ya existente reutiliza su `id_persona`; el caso ambiguo va a revisión.
- [ ] Un admin da de alta un usuario desde Configuración y esa persona ingresa con su rol.
- [ ] Un admin no puede quitarse su rol ni desactivarse (test); un no-admin no accede a la gestión (test).

**Bloque 3C**
- [ ] Un `.sav` precarga códigos, textos, tipos y etiquetas, editables antes de confirmar.
- [ ] En modo «crear individuos», el dedup de R1.2 se aplica y los ambiguos van a revisión (test).
- [ ] Los datos patronímicos del `.sav` no llegan al store semántico (test del guardrail).
- [ ] La exportación con datos solo está disponible tras reidentificar y queda registrada con motivo `exportacion`.
- [ ] Crear un panel desde una consulta da de alta las membresías, es idempotente y registra su origen.

## 10. Success Metrics

**Leading**
- Cierre de brecha de representatividad por ola (efecto directo del muestreo).
- Incidencia de sobre-convocatoria: % de invitaciones por encima del umbral (objetivo: bajar).
- Tasa de dato marcado `sospechoso` y su evolución.
- Tiempo de carga de un estudio: SAV vs. Excel manual (objetivo: caída fuerte).
- Inscripciones públicas por semana y % aprobadas.

**Lagging**
- Churn del panel y tasa de respuesta sostenida (efecto de la gamificación).
- Tasa de canje y su correlación con la participación.
- Reducción de duplicados de identidad (efecto del dedup en altas por SAV y landing).
- Proporción del crecimiento del panel que viene de la landing vs. alta manual.

## 11. Riesgos y preguntas abiertas

- **[legal]** Base legal del alta de individuos por SAV *(bloqueante R3.9 modo crear)*.
- **[legal/finanzas]** Tratamiento fiscal del canje *(bloqueante R3.5)*.
- **[datos]** ¿Los exports de campo traen duración por respuesta? Sin eso, el chequeo de speeders no existe y la gamificación premia sobre una noción de calidad más pobre.
- **[producto]** Calibración de umbrales de fatiga y de calidad: los defaults no están medidos contra los datos de Equipos. Requiere el mismo tipo de protocolo empírico que se usó para calibrar la consulta.
- **[producto]** Falsos positivos de calidad: marcar `sospechoso` a alguien que respondió bien y rápido le cuesta puntos. De ahí que la marca sea reversible; hay que decidir quién revisa y con qué frecuencia.
- **[producto]** Tensión cuota vs. fatiga sin salida limpia: qué hace el sistema cuando el único modo de cerrar una brecha es quemar a los pocos elegibles. Hoy: lo informa y decide el humano.
- **[seguridad]** La landing es superficie pública: sin endurecer (Fase 4), es vulnerable a inscripciones basura. Decidir si se lanza con volumen limitado o se adelanta parte de R4.4.
- **[seguridad]** La gestión de usuarios es escalada de privilegios por diseño (un admin crea admins): permiso acotado y auditoría no son opcionales.
- **[privacidad]** Esta fase amplía la superficie de datos personales en tres frentes a la vez (landing, alta por SAV, exportación con PII). Conviene revisar el conjunto con el DPO antes de habilitarlos, no de a uno.
- **[alcance]** Once requisitos es mucho para un sprint. Los bloques A/B/C son separables y pueden desplegarse por partes; la secuencia sugerida es A → B → C, pero B y C no dependen entre sí.
