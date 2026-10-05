# Sistema de gestión de paneles + consulta semántica

Plataforma para administrar paneles de investigación de mercado y consultar el contenido de las encuestas por significado.
Referencia de producto: `PRD_gestion_de_paneles_detallado.md` y `PRD_consulta_semantica_cuestionarios.md`.

## Invariante central: dos stores

- **Bóveda** (`db/boveda/`): Cloud SQL for Postgres, instancia separada de la semántica. PII + módulo de paneles (paneles, membresías, consentimiento, participación, muestreo, gamificación). Los atributos demográficos (sexo, localidad, fecha de nacimiento → tramo etario) son **autoritativos acá**.
- **Semántico** (`db/semantica/`): Cloud SQL for Postgres + pgvector. **Solo** embeddings + `id_persona`. Contenido puro.

**Regla dura #1: la PII nunca se escribe en el store semántico.** Al store semántico solo viaja `id_persona`. Cualquier código que intente persistir nombre, email, celular, documento, fecha exacta u observaciones del lado semántico está mal.

Desde la Fase 5 esa regla **la hace valer la base**: un event trigger en `ddl_command_end` rechaza el `alter table` en el momento, y el catálogo de campos prohibidos vive en `campo_pii` (store semántico). `pii.CAMPOS_PII` es su espejo y hay una prueba que falla si divergen. Las excepciones legítimas (`nombre` en `cuestionario`, `pregunta` y `serie`) se declaran en `excepcion_pii`, con su motivo, en una migración: nunca apagando el guardia.

Cruce entre stores: por conjuntos de `id_persona`. Los dos lados comparten `ref_estudio` (uuid) para vincular `encuesta` (bóveda) ↔ `cuestionario` (semántico). No hay FK entre stores; es una referencia lógica.

## Identidad

- `id_persona` (uuid) es la clave de la persona en **toda** la plataforma. La emite el **enrolamiento** (bóveda), no la ingesta.
- El panelista **es** la `persona` de la bóveda. La ingesta semántica referencia ese `id_persona`; no crea personas.

## La bóveda tiene más de un consumidor

Desde la Fase 5, `paneles` **no es el único** programa que le habla a la bóveda: COLOQUIO (investigación cualitativa) es el segundo, y el registro `sistema_consumidor` está hecho para que sumar un tercero cueste una fila y un `grant`.

La consecuencia para quien escribe código acá: **una invariante de cumplimiento escrita en Python es una promesa repetida en dos bases de código**, y la primera que se rompa lo va a hacer en silencio. Por eso:

- El gate de consentimiento es `v_persona_convocable`, **no** un `select` en Python. `consentimiento.esta_vigente()` y `filtrar_con_consentimiento()` leen esa vista; no vuelvan a calcular la regla.
- Desde la `boveda/0021` esa vista tiene **una fila por persona**: las finalidades vigentes van en `finalidades text[]` y se pregunta `'x' = any(finalidades)`. Las de ámbito estudio están en `v_persona_finalidad_vigente`. El consentimiento se evalúa con `exists`, nunca con un `join` que multiplique filas, y `verificar_coloquio.py` comprueba la cardinalidad de cada vista de la superficie contra la clave declarada en `CLAVE_POR_RELACION`: una vista nueva con `id_persona` tiene que declarar la suya.
- Leer un dato de contacto es `contacto_para_convocatoria()`: un canal, con el gate reaplicado y la auditoría en la misma transacción. No hay un segundo camino.
- Y exige una convocatoria activa **en el sistema que llama**: `paneles` la tiene en sus tablas, un consumidor externo la declara con `declarar_convocatoria()` —su única escritura sobre la bóveda—. No agreguen excepciones por sistema al chequeo: confiar en el consumidor deja el gate en nada.
- Una baja genera pendientes para **todos** los consumidores activos (`generar_borrados_pendientes()`), y la bóveda nunca espera a ninguno.
- La fatiga es la excepción deliberada: se expone como **hechos** (`v_fatiga_panelista`) y cada consumidor pone su umbral. El consentimiento es legal y no se negocia; la fatiga es negocio.
- El origen de cada fila de auditoría se deriva de la conexión (`sistema_de_la_conexion()`, sobre `session_user`), nunca de un parámetro del llamador.

`scripts/verificar_coloquio.py` se conecta **como `coloquio_app`** y comprueba todo eso, incluida una lista blanca de privilegios efectivos que vive en el repo. Si una migración abre un acceso de más, esa prueba rompe. Correrlo después de tocar cualquier `grant`, vista o función de la superficie externa.

Cada chequeo termina en **pasado, fallido u omitido** (D62). Un chequeo que en ese entorno no puede probar lo que quiere —el intruso contra Cloud SQL, la cardinalidad sobre vistas vacías— levanta `Omitido` con su motivo: no lo conviertan en pasado ni lo dejen fallando. Una batería que falla siempre deja de leerse. Y el chequeo de auditoría manda como `p_actor` un email de usuario, que es el contrato: probar con el rol de la conexión verifica el nombre del rol, no la auditoría.

## El portal del panelista es otra superficie

Desde la Fase 6 la bóveda autentica también a **miles de externos**: cada
panelista entra a `/portal` a ver sus puntos, corregir sus datos y ejercer sus
derechos. Comparte Firebase Auth con la app de administración y **nada más**.

Lo que hay que respetar al tocar `panel_api/portal.py` o sus rutas:

- **El `id_persona` no se recibe nunca.** Sale de `cuenta_panelista` a partir
  del `uid` del token (`portal.persona_de()`). Hay una prueba que recorre
  todas las rutas de `/portal/` y falla si alguna lo toma de la URL.
- **Una sesión del portal no tiene rol.** `auth.actor_de_portal()` devuelve
  `rol = None` a propósito: así no puede ejecutar ningún permiso interno
  aunque una ruta quede mal registrada. No le agreguen un rol «panelista».
- **Los textos que no revelan son constantes** del módulo
  (`RESPUESTA_DE_ENLACE`, `MENSAJE_CREDENCIAL_INVALIDA`), y son los mismos
  exista o no el correo. Dos textos parecidos en dos lugares terminan
  divergiendo, y la diferencia *es* la filtración.
- **Lo editable es una lista blanca** (`editable_por_panelista`), y un valor
  con `origen = 'panelista'` es autoritativo: una ingesta posterior lo informa
  como discrepancia y no lo pisa.

Desde **R6.1.a** se entra con contraseña y no con un enlace por visita, y eso
agrega tres reglas más:

- **El login pasa por el backend** (`portal.iniciar_sesion`), no por
  `signInWithEmailAndPassword` en la página. El límite de intentos y el
  rechazo de quien se dio de baja se deciden del lado del servidor o no se
  deciden. La contraseña nunca se guarda, se loguea ni se devuelve: va a
  Firebase Auth detrás de `credenciales.py`.
- **Lo irreversible pide la contraseña de nuevo** —baja, retiro de finalidad,
  cambio de correo— y `_exigir_reautenticacion()` **falla cerrado**: sin
  proveedor de credenciales la acción no ocurre. No le agreguen un camino
  alternativo «por si acaso».
- **El corte de acceso tras una baja vive en `bajas.retirar()`**, no en el
  portal: la baja también la ejecutan el DPO y los jobs, y si el corte
  viviera en un solo camino los otros dejarían la credencial viva. Misma
  razón por la que el gate de consentimiento es una vista y no Python.

## La ingesta no corre adentro de una request

Desde la ingesta diferida, confirmar una carga **la encola**: el trabajo se
persiste, se parte en lotes y Cloud Tasks procesa uno por tarea
(`panel_api/diferida.py`, `boveda/0019`). Tres cosas que no hay que deshacer:

- **La tarea llama a `encuestas.ingestar()` / `cargas.ingestar()`, las de
  siempre.** No hay una segunda implementación de la ingesta, y por eso el
  gate de consentimiento se re-evalúa en cada lote, el guardia de PII sigue
  corriendo y los upserts idempotentes cubren el reintento. Una ingesta
  paralela escrita aparte tendría que volver a demostrar las tres, y
  divergiría con el primer arreglo que se haga de un solo lado.
- **El plan se congela al confirmar.** Las filas van despivotadas y con el
  mapeo resuelto; las tareas no reinterpretan el archivo. Si lo hicieran, dos
  lotes de la misma carga podrían usar mapeos distintos.
- **El avance se deriva de los lotes (`v_ingesta_progreso`), no de un
  contador.** Con tareas en paralelo un acumulador se desincroniza y el
  síntoma es una barra que miente. No agreguen `lotes_terminados` a
  `ingesta_trabajo`.

Y lo de siempre con las funciones nuevas: Postgres le da `execute` a `public`,
así que cada una necesita su `revoke` o `scripts/verificar_coloquio.py` rompe.

## La ficha desde un resultado, y lo que cuesta cruzar los stores

La Fase 7 pone en una pantalla cosas que el diseño mantiene separadas. Tres
reglas que no son estéticas:

- **La ficha seudónima no devuelve nombre, documento, correo ni celular.**
  Hay una prueba que recorre su salida y falla si aparece cualquiera de los
  cuatro. No es pudor: si la ficha mostrara el nombre con un clic, la
  auditoría de reidentificación dejaría de reflejar quién vio los datos de
  quién, que es lo único que esa auditoría existe para demostrar.
- **Ver las respuestas de un panelista se registra siempre.** Une identidad
  y contenido —lo que los dos stores mantienen separado— y por eso la ruta
  llama a `ficha.respuestas_con_registro()`, nunca a `ficha.respuestas()`
  directo. La versión sin registro existe solo para poder probar la consulta
  sola. El motivo es `respuestas_panelista` y se distingue de reidentificar
  un contacto.
- **El resumen de revisión de una importación sale de la misma ruta que
  ejecuta**, con la bandera `solo_revisar`. Un endpoint aparte empieza igual
  y diverge, y lo que divergiría es la pantalla que dice «esto es lo que va
  a pasar». La pantalla, por lo mismo, arma el cuerpo una sola vez.

Desde que se completó la fase, dos más:

- **La evidencia de la ficha llega desde el resultado** (los `respuesta_id`
  que ya trae el ranking) y el servidor la relee de la base, **solo si es de
  esa persona**. No se recalcula con el criterio: la ficha explica el
  resultado que se está mirando.
- **Las respuestas procesadas se cargan a pedido** (`respuestas.js`). Si se
  cargaran al abrir la ficha, cada apertura dejaría registrado que alguien
  leyó las opiniones de esa persona y el registro dejaría de significar algo.
  Y toda ruta de las fases 7 y 8 tiene que usarse desde una pantalla:
  `test_rutas_con_pantalla.py` falla si una queda solo en `api.js`.

Y en el paso de revisión, **advertir no es bloquear** (D63). Solo frena lo
que el sistema no puede suplir con una decisión —crear personas sin evidencia
de consentimiento, sin ningún dato de identidad— y eso lo rechaza la ruta, con
el motivo junto al botón. Todo lo demás es advertencia (`bloquea: false`),
dice su consecuencia y deja seguir; cada una figura en
`resumen_ingesta.VALIDACIONES` y una prueba falla si aparece una que no está.
«Hay clave de dedup» se pregunta a `claves_de_dedup`, en los términos de
`dedup.resolver`, y no se vuelve a calcular en otro lado.

El dedup reconoce por documento → correo → **celular** → nombre + fecha de
nacimiento (D64). El celular es más cauto que el correo porque puede ser
compartido: reutiliza solo con **una** titular, sin un documento o correo que
la contradiga y con nombre compatible; si no, revisión. Se compara en E.164
(`preferencias.normalizar_celular`), y su índice (`boveda/0022`) **no es
único** a propósito: no le agreguen `unique`.

Y una que parece un descuido y no lo es: **`motivo_reidentificacion` no
tiene clave foránea**. `registrar_reidentificacion` documenta que nunca
pierde el rastro por una etiqueta desconocida, y una FK invertiría ese
intercambio. El catálogo se mantiene sincronizado con una prueba espejo,
igual que `pii.CAMPOS_PII`.

## El texto que se embebe: el sistema propone, el analista confirma

La Fase 8 (`calidad_dato.py`, `reproceso.py`) detecta lo que degrada el texto
que se embebe —baterías, textos con prefijos y consignas, `Checked`, códigos
sin traducir, no respuesta, PII en texto libre— y **propone** la corrección.
Lo que no hay que deshacer:

- **`ingesta.respuesta_de` es la única implementación de «qué se embebe de
  una celda».** La usan la ingesta, la vista previa y el reproceso. La vista
  previa la calcula el servidor (`/calidad/vista-previa`) aunque se pida en
  cada tecla: una copia en JavaScript divergiría, y lo que divergiría es la
  pantalla que dice «esto es lo que se va a escribir».
- **Nada se aplica solo.** Las decisiones viajan en cada pregunta
  (`solo_marcadas`, `excluir_valores`, `prefijo_respuesta`…) solo si el
  analista las eligió; sin ellas la ingesta hace exactamente lo de antes. El
  texto y las etiquetas originales del archivo se guardan
  (`pregunta.texto_original`, `opciones_originales`).
- **La PII en texto libre es un indicio y no bloquea.** Son patrones, se
  informan como tales y los ejemplos van enmascarados. El guardia duro sigue
  siendo el de nombres de columna.
- **La calidad del dato es sobre lo que se embebe.** Una variable con rol
  demográfico no recibe hallazgos (`calidad.soloSemanticas`) y ninguna
  propuesta toca su fila: «Excluir la variable» sobre la columna de correo
  se llevaba el marcado `email` y con él la clave de dedup.
- **El reproceso no es una segunda ingesta.** Recompone el texto desde el
  store semántico (invirtiendo las etiquetas) con `respuesta_de`, re-embebe
  solo lo que cambió por `hash_texto`, corre por la vía diferida con el plan
  congelado (`plan.operacion = 'reproceso'`, antes y después) y reaplica el
  gate de `uso_semantico` en cada lote. No resuelve identidades ni toca la
  bóveda: por eso no llama a `encuestas.ingestar()`, que reincorporaría al
  panel y registraría participaciones.

## Reglas de negocio que el código debe respetar

- No se puede convocar ni incluir en muestreo a una persona sin `consentimiento` **vigente** para la finalidad correspondiente.
- El uso semántico entre estudios exige la finalidad `uso_semantico` vigente, **distinta** de `contacto_participacion`.
- Baja / retiro de consentimiento ⇒ **borrado en cascada**: PII (bóveda) + embeddings (semántico) + salida del muestreo.
- Saldo de puntos siempre **>= 0**; sin sobregiro en canje; puntos con vencimiento. (No hay constraint DDL: se valida en la app / trigger.)
- Se ganan puntos solo por participación de **calidad** (`respondio = true` y `calidad_estado = 'ok'`).

## Stack

Capa de aplicación en **Firebase**; los datos en **Postgres**. Firebase NO reemplaza a los stores (ver "Por qué los datos siguen en Postgres").

- **Auth:** Firebase Auth (app admin y, más adelante, la landing de panelistas). Resuelve el modelo de auth.
- **Backend / lógica:** Cloud Functions for Firebase, runtime **Python** (continuidad con `ingesta.py`); acceso a las bases vía el conector de Cloud SQL; `pgvector-python` para vectores.
- **Frontend admin:** SPA (React) en Firebase Hosting.
- **Store semántico (vector):** Cloud SQL for Postgres + pgvector (full GCP).
- **Bóveda (PII + paneles):** Cloud SQL for Postgres, en una instancia dedicada y separada (proyecto/VPC aparte, acceso bloqueado). "Separado" = control de acceso, misma nube.
- **Ambos stores en Cloud SQL**, en instancias distintas: nunca en la misma instancia (la separación bóveda/semántico es parte del diseño de privacidad).
- **Embeddings:** Voyage `voyage-3.5` a **512 dimensiones**, detrás de una interfaz para cambiar de proveedor. 512 y no las 1024 del default porque la mitad de vector es la mitad de instancia (D54); la dimensión tiene que coincidir con `respuesta.embedding` en el store semántico, y la ingesta lo comprueba antes de mandar nada a embeber.

### Por qué los datos siguen en Postgres (no Firestore)
La búsqueda semántica depende de **pgvector** y de consultas relacionales (joins respuesta↔pregunta↔persona, rollup a individuo, puente bóveda↔semántico por `id_persona`, distancia a fuerza bruta sobre subconjuntos). Firestore no cubre ese patrón: su vector search es un KNN plano, sin joins ni agregación. Y el módulo de paneles necesita integridad relacional (membresías N:M, estados de consentimiento, ledger de puntos con saldo ≥ 0, índices únicos de dedup). Mover los datos a Firestore sería deshacer el diseño. Firebase se usa para auth, funciones y hosting; los datos, en Postgres.

## Convenciones

- Identificadores de esquema y dominio en **español** (ya reflejado en los `.sql`).
- Migraciones versionadas; no editar el esquema a mano en la base.
- Secretos por variables de entorno; nunca en el repo. Además de guardarlos en Secret Manager hay que **declararlos** en la lista `SECRETOS` de `functions/main.py`: `firebase deploy` solo monta los declarados, y uno que falta no rompe nada —hace que el sistema se comporte como si la credencial no existiera—. `functions/tests/test_main.py` lo verifica en las dos direcciones.
- **Trabajar por fases.** Empezar por la Fase 1 (`HANDOFF_fase1.md`). No implementar una fase posterior hasta cerrar el Definition of Done de la anterior.

## Antes de pushear: verificar si el PR ya está mergeado

La rama de trabajo se reutiliza entre entregas, así que **siempre** hay que
comprobar el estado del PR antes de pushear. Un PR mergeado no admite trabajo
nuevo: apilar commits encima de historia ya mergeada deja la rama divergida y
el trabajo invisible.

```bash
gh pr view --json state,mergedAt        # o el equivalente por API
```

Si está mergeado, el trabajo que sigue es un cambio **nuevo**: rebasar los
commits sin mergear sobre el `main` actual, conservando el nombre de la rama, y
abrir **otro PR**.

```bash
git fetch origin main
git checkout -B <rama> origin/main
git cherry-pick <commits-sin-mergear>
git push --force-with-lease -u origin <rama>
```
