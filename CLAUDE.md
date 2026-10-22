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

Y desde el PEDIDO de la API key web (D68), dos más:

- **La web API key es `WEB_API_KEY`, variable común de `functions/.env`, no
  un secreto.** Firebase rechaza cualquier variable o secreto que empiece
  con `FIREBASE_`, `X_GOOGLE_`, `EXT_` o `KIT_`; ese nombre dejó el
  placeholder en producción y nadie entraba. `test_main.py` lo vigila.
- **«No entró» y «no se pudo comprobar» son dos cosas.** Un 400 de
  credencial es `None` y el mensaje genérico; un 403/5xx/timeout/key
  inválida es `ComprobacionNoDisponible`, con
  `MENSAJE_COMPROBACION_NO_DISPONIBLE`, y **no suma intentos**. El motivo
  exacto va al log (`[credenciales]`) sin contraseña ni key. Un `except` que
  devuelve un valor por defecto sin registrar nada es como se escondió ese
  incidente: no agreguen otro.

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

Desde el PEDIDO del indicador de ingesta (D77, `boveda/0029`), tres más:

- **Una sola ingesta en curso por destino, y lo garantiza la base**: el
  índice único parcial `ingesta_trabajo_uno_activo_por_destino`.
  `diferida.encolar` traduce la violación a `IngestaEnCurso` con el
  `trabajo_id` del que existe, y la pantalla lleva a su seguimiento.
  `exigir_sin_trabajo_activo` en las rutas es una cortesía para rechazar
  antes de leer el archivo; no la conviertan en la guarda ni le saquen el
  índice. Una terminada o fallida no bloquea.
- **El modal dice que trabaja en el momento del clic** y bloquea el botón y
  todo el mapeo (`bloquearModal`, `indicarProcesando` en `encuestas.js`).
  Lo prueba un Chromium de verdad (`test_modal_de_ingesta_procesando.py`):
  un pedido nuevo del modal pasa por ahí.
- **Lo que la ingesta sabe del estudio sale de `ingesta.estudio_del_destino`**,
  para encuesta y carga. Leer el dict de una con los nombres de la otra
  devolvía `None` sin error: así se quedaban sin fecha los cuestionarios de
  las cargas. No armen ese dict a mano. Y cada pedido mide sus partes
  (`diferida.Cronometro` → `ingesta_trabajo.preparacion`): lo que crece con
  la base es `crear_individuos`; pasarlo a lotes paralelos puede duplicar
  personas en el dedup, así que no se hace sin serializarlo.

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

Desde el PEDIDO del celular (D78) también **encuentra** personas: es un tipo
de identificador del modo «ya existen» (`ingesta.POR_CELULAR`), con las
mismas reglas —E.164, una sola titular; si no, `celular_compartido`—. No
registra alias de la plataforma: cada carga vuelve a preguntar si el número
sigue teniendo una sola titular. Y el detector de datos personales mira **lo
que se va a ingestar**: ni la columna que identifica ni las demográficas o de
identidad (`resumen_ingesta.resumir` y `noSeEmbebe` en la pantalla). Un
chequeo que analiza el archivo entero avisa de columnas que el flujo ya
descartó, y una alarma que salta de más deja de leerse.

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

## El correo es el acceso, y no hay atajo

Desde R-MAIL los correos salen por SMTP de Google Workspace
(`correo.py`, proveedor `workspace`, contraseña de aplicación en
`SMTP_PASSWORD`). Lo que no hay que deshacer:

- **Ninguna página pública muestra el código ni el enlace.** Hubo un «modo
  desarrollo» que los devolvía en la respuesta y el portal los mostraba: con
  escribir el correo de otro se entraba a su cuenta. Hay una prueba que falla
  si `sin_enviar` vuelve a aparecer en `portal.html`, `inscribirse.html` o
  `portal.js`.
- **Ausencia de proveedor y modo desarrollo son dos cosas.** Sin proveedor,
  `proveedor_de_envio()` levanta `EnvioNoConfigurado` **antes** de mirar si
  el correo existe. El modo desarrollo es `ENVIO_MODO_DESARROLLO`, explícito,
  y `modo_desarrollo()` lo ignora en Cloud Run (`K_SERVICE`). No lo vuelvan a
  derivar de «no hay proveedor».
- **Un envío fallido no se reporta como éxito**: pasa por
  `enviar_y_registrar()`, que deja la fila en `envio_correo` (sin contenido)
  y levanta `EnvioFallido`. Un correo nuevo del sistema usa ese camino y una
  plantilla de `correo.PLANTILLAS`.

## La verificación: `sin_verificar` no es `dudoso`

La verificación con Claude va por lotes (`verificar_por_lotes`): índices
locales al lote, `stop_reason` mirado **antes** de aceptar nada, un lote
truncado se descarta entero y se parte en dos, y todo dentro de un
presupuesto de tiempo por consulta (60 s, para que COLOQUIO entre en sus
90 s). Lo que no se pudo juzgar es `sin_verificar`, con su `fallo`:

- **No excluye ni aprueba**, en ningún modo, y la persona queda con
  `verificacion_incompleta`. `dudoso` es solo «Claude leyó y no pudo
  decidir», y su regla de estricto no se apaga por una falla técnica.
- **Nunca completen lo que falta con `dudoso`.** Era el bug: un truncamiento
  silencioso se presentaba como un juicio.
- **El modo de depuración no es un log** (`VERIFICACION_DEPURACION`): va al
  store semántico (`verificacion_captura`), apagado por defecto, vence a los
  7 días, solo admin. El payload a Claude no lleva identificadores; no se los
  agreguen «para depurar mejor». El diagnóstico de rutina (`[verificacion]`
  en el log) lleva números, nunca contenido.

## Lo que se verifica son unidades, y «completo» tiene precio

Desde R-CS (D70, `specs/ADDENDUM_solicitud_consultas_semanticas.md`) la
consulta semántica rerankea y verifica **unidades de evidencia** —un texto
embebido distinto; desde D73, la huella sola y no `(pregunta_id,
hash_texto)`— y tiene dos alcances. Lo que no hay que deshacer:

- **Una sola regla de agregación** (`consultas.hallazgo_de`), para la
  exploratoria y la completa: `no_cumple` > `cumple` > `dudoso` >
  `sin_verificar` > `irrelevante`, y un `cumple` con un `no_cumple` marca
  `contradiccion`. No escriban una segunda en `consulta_completa`.
- **`irrelevante` no es `no_cumple` ni `dudoso`**: es ausencia de evidencia.
  En laxo, quien no tiene **ninguna** evidencia pertinente no aparece
  (`sin_evidencia_pertinente`). Y corrige la interpretación, no el recall:
  los lugares los recuperan las unidades.
- **La unidad es física desde D73** (`unidad_evidencia`, ver «Un vector por
  texto distinto»); `v_unidad_evidencia` cuenta respuestas, personas y
  preguntas por unidad. Una baja la achica sola (trigger). La huella SQL de
  las migraciones y `semantica.hash_texto` tienen que coincidir, y hay una
  prueba.
- **La completa no corre en la request y no guarda el resultado.** Plan
  congelado, lotes en Cloud Tasks (`procesarconsulta`), avance y **gasto**
  derivados de los lotes (`v_consulta_progreso`), gate re-evaluado en cada
  lote y **al leer**. Los veredictos van al store semántico
  (`veredicto_unidad`, sin `id_persona`, 30 días). No agreguen un
  acumulador de costo a `consulta_ejecucion`.
- **El costo se muestra antes y el presupuesto es obligatorio.** La
  estimación sale de la misma ruta que lanza (`solo_estimar`); el
  presupuesto se controla antes de cada lote; lanzar es el permiso
  `consulta_completa`. Una completa sin presupuesto o con uno por encima de
  `CONSULTA_PRESUPUESTO_MAXIMO_USD` no se lanza.
- **Cada ejecución deja su fila** en `consulta_ejecucion` (diagnóstico,
  proveedores efectivos, `input_type`, costo), nunca personas ni evidencias.
  Un reranker que contesta sin puntajes para todo es una **degradación
  declarada**: no vuelvan a descartar los `None` en silencio.
- **El criterio se embebe como `query`** y se vuelve atrás sin redesplegar
  (`EMBEDDINGS_TIPO_CONSULTA=document`). Las respuestas siguen siendo
  `document`.
- **El contrato con COLOQUIO es `version_contrato: 2`**: `item.detalle` lleva
  lo mismo que `item.criterios` (COLOQUIO lee `detalle`), y la ausencia de un
  campo nuevo se lee como «no se sabe», nunca como favorable.

## El índice vectorial no sabe de filtros

Desde el bug de A1.2 (D71): con un `order by <=> … limit`, Postgres resuelve
por el índice HNSW, que devuelve como mucho `hnsw.ef_search` vecinos (40 por
defecto) **de todo el corpus**, y el `where` se aplica después. Un filtro
selectivo da vacío y un `top_n` mayor que `ef_search` da 40, **sin error**.

- **El recall pasa por `semantica._vecinos`**, sobre unidades de evidencia
  (D73): con filtro por personas (el gate) es exacto; sin filtro, **también
  exacto** mientras el corpus no pase de `RECALL_EXACTO_HASTA` unidades (200
  mil, D72), y por encima índice con `ef_search = 2 × top_n`. Un recall corto se registra (`[recall]`) y se
  recalcula. No escriban otro `order by <=> … limit` con un `where` al lado.
- **Completo no es correcto** (D72): el índice puede devolver las 200 pedidas
  sin la más cercana, y ninguna cuenta lo ve. No agreguen un control que mire
  solo cuántas filas volvieron; lo que mide el índice es
  `semantica.comparar_recall` (`diagnosticar_consulta.py recall-indice`),
  contra el exacto. Correrlo antes de bajar el umbral o al cruzarlo.
- **Medir filas conocidas no usa el índice**: el filtro en una CTE
  `materialized` y la distancia sobre ese conjunto
  (`diagnosticar_consulta._distancias`). Una prueba falla si el script vuelve
  a combinar `where`, `order by <=>` y `limit` sin materializar.
- **Vacío no es ausencia.** Si una consulta devuelve vacío o un valor por
  defecto, el código distingue y registra si es un resultado o algo que no
  se pudo hacer (`a1-distancia`: `AUSENTE` sale con 1, `NO_MEDIDO` con 2).
  Y las conclusiones del diagnóstico son excluyentes: si la evidencia no
  entró al recall, no se concluye nada sobre lo que pasa después (el
  recorte).

## Un vector por texto distinto

Desde R-UE (D73, `specs/SPEC_unidades_de_evidencia.md`) el vector vive en
`unidad_evidencia` (semantica/0010), **una vez por texto embebido distinto**,
y cada respuesta lo referencia (`respuesta.unidad_id`). El 96 % del corpus
eran copias, y las copias rompían el índice (bloques de vectores idénticos:
0 % de recall para «xiaomi») y gastaban la verificación. Lo que no hay que
deshacer:

- **La unidad es la huella del texto embebido completo** (`hash_texto`, que
  ya incluye la pregunta), no `(pregunta_id, hash_texto)`: la misma pregunta
  con el mismo texto en dos olas es una unidad. `veredicto_unidad` es único
  por `(ejecución, criterio, hash_texto)`; su `pregunta_id` es informativo.
- **Se escribe por un solo camino**: `semantica.escribir_respuestas`, que usan
  la ingesta y el reproceso. Reutiliza la unidad que existe y embebe una vez
  por texto nuevo. No escriban `respuesta.embedding` ni inserten respuestas
  con un vector propio: `upsert_respuestas` levanta `SinUnidad` si el texto
  no tiene unidad, y la FK compuesta `(unidad_id, hash_texto)` rechaza una
  respuesta apuntando a otro texto.
- **El recall busca unidades** y `top_n` las cuenta; cada una vuelve con
  todas sus respuestas, y el gate se aplica al resolver personas.
  `RECALL_EXACTO_HASTA` cuenta unidades.
- **Las respuestas de un lote se escriben en una sola sentencia** (D79,
  `upsert_respuestas`). El trigger de huérfanas corre al final de cada
  sentencia: escribiendo fila por fila, un `UPDATE` borraba a mitad del lote
  la unidad que una respuesta posterior iba a usar, y su vector quedaba nulo
  (la carga 28). No vuelvan al loop. Las repetidas (misma persona y
  pregunta) dejan la última, y una unidad sin vector es `RespuestaSinVector`
  con huella y pregunta, no un `not null` mudo.
- **`top_k` cuenta unidades distintas y el veredicto llega a todas sus
  personas** (`consultas._seleccionar_y_verificar`). Las otras evidencias de
  esas personas se verifican dentro del tope de siempre; la que no entra es
  `sin_verificar` con `fallo = tope_de_unidades`, **nunca** `dudoso`.
- **La unidad que se queda sin respuestas la borra la base** (trigger
  `borrar_unidades_huerfanas()`), por cualquier camino que borre
  respuestas. Así la baja se lleva el vector de un texto que solo esa
  persona dio. No lo muevan a Python. Un `truncate` no dispara el trigger:
  por eso las pruebas truncan también `unidad_evidencia`.
- **`respuesta.embedding` está en transición**: la completa el trigger
  `respuesta_asegurar_unidad()` desde la unidad, para que volver atrás el
  código no exija volver atrás la base. **Ningún código nuevo la lee.** La
  quita `db/semantica/posteriores/0011_…`, que se aplica a mano después de
  verificar en producción; nunca la muevan a `db/semantica/`.

## De qué carga viene cada persona, y sobre quién se calcula la composición

Desde R-ORG (D67) cada persona queda vinculada a las cargas de las que
proviene (`persona_carga`, `boveda/0024`), y la composición se calcula por
ámbito: `todos`, `panel` o `carga`.

- **El vínculo es de muchos a muchos y no se pisa.** `creada` o
  `reutilizada`, fijado la primera vez (`cargas.registrar_vinculos`, `on
  conflict do nothing`). No le agreguen un `carga_id` a `persona`.
- **Los datos del estudio viven en la carga** (`fecha_estudio`,
  `publico_objetivo`) y se leen por join. No los copien en la persona: la
  corrección se tiene que ver en todas las fichas sin tocar ninguna.
- **No se reconstruye el pasado.** Sin vínculo, la ficha lo explica
  (`cargas.SIN_VINCULO_REGISTRADO`); no se infiere de `alias_origen`.
- **Una carga no tiene objetivo de composición; «todos» sí**, y es otro
  universo que el de cualquier panel. El ámbito `panel` es el SQL de
  siempre: el muestreo y el optimizador dependen de él.
- `estudio_de_origen` es una columna de resultados, no un atributo:
  `atributos.CLAVES_RESERVADAS` impide crear un atributo con esa clave.
- Desde `boveda/0026` la carga declara también **tipo de datos de origen** y
  **modo de relevamiento** (vocabulario cerrado + «otro» con especificación,
  lo hace valer un `check`). Mismo patrón: viven en la carga, llegan a cada
  panelista por el vínculo y son columnas de resultados reservadas
  (`cargas.COLUMNAS_DE_PROCEDENCIA`). La pantalla recibe los vocabularios de
  `GET /cargas` (`opciones`); no los repitan en JavaScript fuera de `demo.js`.

Y desde la paridad de consultas (D69): **el resultado demográfico es
seudónimo** —sin nombre, correo, documento ni celular— y usa la misma barra
de acciones que el semántico (`barraDeAcciones`). Una acción nueva sobre
resultados se agrega ahí, una vez.

## De qué trata una carga: temáticas, y la sección Cargas

Desde R-TEM / R-CAR (D74, D75, D76, `boveda/0027` y `0028`) cada carga tiene sus
**temáticas incorporadas**, cada panelista las suyas, y hay una sección
«Cargas» que la mira entera. Lo que no hay que deshacer:

- **Una temática es una entidad compartida** (`tematica`), no texto en la
  carga. El nombre se guarda normalizado (minúsculas, espacios simples) y lo
  hace valer un `check`; antes de crear una se busca la existente que difiera
  solo en tildes (`tematicas._resolver_existente`). Sin esto el vocabulario se
  llena de variantes y «¿qué cargas cubren telefonía?» deja de tener
  respuesta.
- **Claude propone, el analista confirma.** `tematicas.proponer` no escribe
  nada; lo único que escribe `carga_tematica` es `tematicas.guardar`
  (`PUT /cargas/<id>/tematicas`), antes y después de confirmar. El origen
  (`propuesta_ia` | `manual`) se fija la primera vez. Si Claude falla o
  tarda, la revisión llega con aviso y la lista vacía: **la carga no se
  frena por las temáticas**, y confirmar no llama a Claude.
- **Lo que viaja a Claude sale del resumen de revisión**
  (`al_store_semantico.variables`) por `tematicas.preguntas_para_proponer`:
  solo código y texto, nunca respuestas, ni demográficas, ni identidad, ni la
  columna que identifica. No armen un segundo criterio de qué se manda.
  `es_nueva` lo decide la base, no el modelo.
- **Hay dos niveles y no se mezclan** (R-TEM.6, D75): las temáticas de **la
  carga** (qué cubrió el estudio) y las de **cada panelista**
  (`persona_tematica`: sobre qué respondió). Una persona tiene la temática si
  respondió **al menos una** de las preguntas que la sustentan en esa carga
  (`tematica_pregunta`); haber estado en la carga no alcanza. No hereden la
  temática de la carga.
- **«Respondió» tiene una sola definición**: `tematicas.respondida` (lo
  embebido que no es no respuesta, con `calidad_dato.es_no_respuesta`). La
  usan los dos caminos: la ingesta (`registrar_de_lote`, con la fila en la
  mano, **sin leer el store semántico**) y el recálculo (`recalcular_carga`,
  desde el semántico por `ref_estudio`, al guardar las temáticas de una
  carga). No escriban una tercera.
- **Las especiales** (`tematica.es_especial`) no son columna
  (`columna_de_varias` las excluye), no salen en exportaciones (sus columnas
  son fijas) y filtrar, contar o verlas en la ficha exige
  `tematicas_especiales`. Marcar es libre; **desmarcar** exige
  `gestionar_atributos`. Un retiro de `uso_semantico` borra las temáticas de
  la persona en el acto (`bajas.retirar`): se derivaron de ese contenido.
- `tematica` y `tematicas` son claves reservadas (`atributos.CLAVES_RESERVADAS`):
  la primera es la dimensión del filtro demográfico, la segunda la columna.
- **La lista de panelistas de una carga es `GET /panelistas?carga_id=…`**, la
  misma ruta y permiso que la sección Panelistas. No agreguen una ruta bajo
  `/cargas/` que liste personas; hay una prueba que lo vigila.
- **Borrar una carga deshace lo que trajo**, no todo lo que aparece en ella
  (D78, `cargas_borrado.py`, `boveda/0030`): todas las respuestas de su
  estudio, y las personas que **creó y no tienen otra vida** se dan de baja
  **por `bajas.retirar()`** (así COLOQUIO recibe su `borrado_pendiente`); las
  demás se conservan sin el vínculo. No lo reemplacen por un `delete from
  carga`. Corre por la vía diferida (`plan.operacion = 'borrado_carga'`):
  semántico primero en cada lote, la carga al final, y volver a pedirlo lo
  reanuda. «Sin otra vida» lo decide `_clasificar`, una sola vez para la
  vista previa y los lotes; si suman un vínculo nuevo de una persona (una
  tabla nueva que la referencie), agréguenlo ahí.

Desde la ampliación (D76, `boveda/0028`) hay **taxonomía**: área → temática,
exactamente dos niveles.

- **Se propone, se aprueba y se guarda.** Claude propone el arranque, ubica
  las temáticas nuevas en la llamada de siempre y sugiere reorganizaciones;
  `taxonomia.proponer_*` **no escriben**. Se guarda el arranque confirmado y
  cada operación aprobada (`taxonomia.aplicar`), de a una y registrada en
  `taxonomia_cambio`. Abrir el filtro o consultar no llama a nadie: no
  agreguen una taxonomía calculada al vuelo.
- **Lo clasificado no se mueve desde una carga** (`ubicar_si_falta`); se
  mueve con una operación.
- **La marca especial efectiva es `v_tematica`** (propia o del área). Lean
  esa vista, no `tematica.es_especial`, para decidir columna, exportación,
  filtro, ficha o composición. El criterio de temática lleva
  `incluir_especiales`, que fija la ruta según el rol y nunca el cuerpo.
- **El filtro cuenta personas** (`taxonomia.filtro`) y no ofrece las que no
  tiene nadie. Y la propuesta de la revisión sigue a la marca de demográficas
  **sin llamar a Claude** (`tematicas.ajustar_a_preguntas`).

## Clientes y lista negra: lo que corta la base y lo que no

Desde la spec de clientes y lista negra (D80, `boveda/0031`):

- **La lista negra corta el contacto en `v_persona_convocable`, y en ningún
  otro lado.** Encuestas, muestreo, optimizador y COLOQUIO dejan afuera a
  quien está en ella sin una línea propia, porque ya leían la vista. No
  agreguen un `not in lista_negra` en Python. Las excepciones son la
  convocatoria, que la separa antes para contarla aparte, y WhatsApp, que
  lee `consentimiento` directo.
- **No le quita a nadie el consentimiento.** El gate de las finalidades que
  no son de contacto lee `v_persona_finalidad_vigente`
  (`consentimiento._gate`). Si `uso_semantico` volviera a pasar por la
  convocable, la lista negra frenaría la ingesta y el reproceso borraría
  sus respuestas. En los resultados se **marca** (`lista_negra.marcar`),
  no se oculta. El motivo solo va a quien tiene `ver_lista_negra`.
- **Una fila por entrada; sacar no borra.** Activa = sin `removido_en`. El
  motivo es obligatorio (lo pide la base). El panelista no ve nada: hay una
  prueba que falla si `portal.py` nombra la tabla.
- **Los clientes de un panelista se derivan** de sus cargas
  (`v_persona_cliente`); no se copian ni se heredan como las temáticas.
  Son confidenciales: ni portal, ni COLOQUIO, ni exportaciones.
- **Los eventos de reidentificación se arman al leer**
  (`auditoria.eventos_de_reidentificacion`), agrupando las filas de una
  misma llamada. No le agreguen un id de evento a `reidentificacion`.

## Reglas de negocio que el código debe respetar

- No se puede convocar ni incluir en muestreo a una persona sin `consentimiento` **vigente** para la finalidad correspondiente.
- Quien está en la **lista negra** no se convoca, ni desde `paneles` ni desde COLOQUIO; su consentimiento sigue intacto (D80).
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
