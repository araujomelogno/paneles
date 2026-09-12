# Decisiones de diseño

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Alcance:** Fases 1, 2 y 3
**Última actualización:** 2026-09-12

---

## Qué es este documento y cómo leerlo

Un registro de las decisiones que no son obvias: las que un lector del código
podría querer revertir sin saber qué se rompe si lo hace. No documenta *qué*
hace el sistema —para eso están los PRD y las specs— sino **por qué está
hecho así y qué se descartó en el camino**.

Cada decisión tiene la misma forma:

- **El problema** — qué había que resolver.
- **La decisión** — qué se hizo.
- **Alternativas descartadas** — qué más se consideró, y por qué no.
- **Consecuencias** — qué se gana, qué se paga, qué queda condicionado.
- **Dónde vive** — el archivo donde está, para poder ir a mirarlo.

Algunas decisiones tienen además **Cómo se verifica**: la prueba automatizada
que falla si alguien la revierte sin querer. Cuando existe, es la parte más
importante de la entrada, porque es lo que convierte una decisión en una
restricción real del sistema.

> **Una decisión revertible no es un error.** Varias de las que están acá
> podrían cambiarse con buenos motivos. Lo que este documento evita es que se
> cambien **sin** motivos, por no saber qué sostenían.

### Índice

| | Decisión | Fase |
|---|---|---|
| [D1](#d1) | Dos stores separados, y la PII nunca cruza | 1 |
| [D2](#d2) | El guardrail de PII se aplica en tiempo de ejecución, no solo en el DDL | 1 |
| [D3](#d3) | Ante identidad ambigua, no se fusiona: decide una persona | 1 |
| [D4](#d4) | El consentimiento se valida en el punto de uso, no en el alta | 1 |
| [D5](#d5) | El cruce entre stores es lógico, sin FK | 1 |
| [D6](#d6) | Los proveedores externos van detrás de una interfaz, con un doble sin red | 1-2 |
| [D7](#d7) | Una consulta demográfica no abre el store semántico | 2 |
| [D8](#d8) | El reranking corre antes de colapsar a individuo | 2 |
| [D9](#d9) | La verificación no puede inventar evidencia | 2 |
| [D10](#d10) | La contradicción se busca en todas las evidencias, no en la mejor | 2 |
| [D11](#d11) | Si falta una clave, la consulta degrada y lo dice | 2 |
| [D12](#d12) | Reidentificar queda registrado; exportar es un evento distinto | 2-3 |
| [D13](#d13) | La gestión de usuarios es escalada de privilegios, y se trata como tal | 2 |
| [D14](#d14) | El esquema desplegado se verifica, no se supone | 2-3 |
| [D15](#d15) | El muestreo explica cada exclusión | 3 |
| [D16](#d16) | El muestreo no completa el cupo empeorando la brecha | 3 |
| [D17](#d17) | Silencio no es aprobado | 3 |
| [D18](#d18) | Marcar calidad es reversible, y queda quién lo hizo | 3 |
| [D19](#d19) | El saldo de puntos es la suma de los movimientos, no un campo | 3 |
| [D20](#d20) | El sobregiro se evita bloqueando, no comprobando | 3 |
| [D21](#d21) | Una inscripción pública no es una persona | 3 |
| [D22](#d22) | La landing responde siempre lo mismo | 3 |
| [D23](#d23) | El texto de consentimiento se versiona y no se reescribe | 3 |
| [D24](#d24) | El repositorio no trae ningún texto legal | 3 |
| [D25](#d25) | Las altas por SAV quedan pendientes de consentimiento | 3 |
| [D26](#d26) | El `.sav` se parsea en el backend y su metadata es una propuesta | 3 |
| [D27](#d27) | Exportar con datos exige haber reidentificado | 3 |
| [D28](#d28) | Un panel creado desde una consulta es una foto | 3 |
| [D29](#d29) | Las rutas públicas se enumeran una por una | 3 |
| [D30](#d30) | Las pruebas corren contra un Postgres real | 1-3 |
| [D31](#d31) | Los defaults numéricos están en el código, no en la base | 3 |

---

# Bloque A · Privacidad e identidad

Son las decisiones que sostienen el invariante central de `CLAUDE.md`. Si
alguna se revierte, el diseño de privacidad deja de existir aunque el sistema
siga funcionando —y ese es justamente el problema: no se nota.

<a id="d1"></a>
## D1 · Dos stores separados, y la PII nunca cruza

**El problema.** El sistema necesita, a la vez, saber quién es cada persona
(para convocarla) y buscar por el significado de lo que respondió (para
armar muestras). Las dos cosas juntas, en una sola base, producen el peor
resultado posible: una base donde una consulta semántica mal escrita puede
devolver nombres y teléfonos.

**La decisión.** Dos instancias de Cloud SQL distintas, no dos esquemas ni
dos bases de la misma instancia:

- **Bóveda** — PII, demográficos y el módulo de paneles. Los atributos
  demográficos (sexo, localidad, fecha de nacimiento → tramo etario) son
  autoritativos acá.
- **Semántico** — Postgres + pgvector. **Solo** `id_persona` y contenido:
  embeddings, textos de respuesta ya despersonalizados, metadatos de
  cuestionario y pregunta.

Lo único que viaja al store semántico es `id_persona`, `ref_estudio`, el
texto de respuesta y su vector.

**Alternativas descartadas.**

- *Una sola base con esquemas separados.* Un `GRANT` mal puesto, un rol con
  más permisos de la cuenta, o simplemente un `join` escrito sin pensar, y la
  separación desaparece. La separación tiene que ser algo que no se pueda
  deshacer por error.
- *Firestore para todo.* La búsqueda semántica depende de pgvector y de
  consultas relacionales —joins respuesta↔pregunta↔persona, rollup a
  individuo, distancia a fuerza bruta sobre subconjuntos—. El vector search
  de Firestore es un KNN plano, sin joins ni agregación. Y el módulo de
  paneles necesita integridad relacional. Mover los datos ahí sería deshacer
  el diseño; Firebase se usa para auth, funciones y hosting.

**Consecuencias.** Cruzar información entre los dos lados cuesta: hay que
hacerlo por conjuntos de `id_persona`, en la aplicación. Ese costo es el
precio de la garantía, y es deliberado. Una consecuencia práctica: la
configuración verifica que los dos DSN no apunten a la misma base y **falla
al arrancar** si lo hacen.

**Dónde vive.** `CLAUDE.md`, `db/boveda/`, `db/semantica/`,
`functions/panel_api/config.py`.

---

<a id="d2"></a>
## D2 · El guardrail de PII se aplica en tiempo de ejecución, no solo en el DDL

**El problema.** Que el esquema semántico no tenga columnas de PII no impide
que alguien escriba un nombre adentro de un campo de texto. Y la ingesta es
exactamente el lugar donde eso puede pasar sin mala intención: un analista
marca la variable «NOM» del archivo de campo como si fuera una pregunta.

**La decisión.** Dos controles, no uno:

- **Estático** — `auditar_esquema(sql)` lee el DDL del store semántico y
  devuelve las columnas cuyo nombre delata PII. Cero columnas es un criterio
  del DoD de la Fase 1.
- **En tiempo de ejecución** — `validar_sin_pii(payload)` inspecciona todo
  diccionario que esté por salir hacia el store semántico. Si aparece una
  clave de PII, **la operación aborta**.

La regla es: es preferible romper la ingesta a filtrar un dato.

**Alternativas descartadas.** *Confiar en la revisión de código.* Funciona
hasta que no funciona, y el día que falla nadie se entera: el dato ya está
del otro lado.

**Consecuencias.** Una ingesta mal configurada falla ruidosamente en vez de
contaminar el store. El costo es que agregar un campo legítimo al payload
semántico requiere pensarlo, porque el guardrail se va a quejar.

**Cómo se verifica.** En la Fase 3 se agregó la prueba más directa posible:
se ingesta un `.sav` **entero, incluidas las variables patronímicas**, y
después se busca en todo el contenido del store semántico cada nombre,
documento, correo y fecha del archivo. Si aparece alguno, la prueba falla.

**Dónde vive.** `functions/panel_api/pii.py`, `semantica.py`,
`functions/tests/test_fase3_operacion.py`.

---

<a id="d3"></a>
## D3 · Ante identidad ambigua, no se fusiona: decide una persona

**El problema.** La misma persona entra al sistema por varias puertas —alta
manual, ingesta de campo, landing pública— y con datos distintos cada vez. Si
el sistema no deduplica, el panel se llena de personas repetidas. Si
deduplica mal, fusiona a dos personas distintas, y eso es peor: se pierden
datos de una y la otra queda con respuestas que no dio.

**La decisión.** Un orden de resolución que se detiene en el primer match:

1. `documento` exacto → reutiliza ese `id_persona`.
2. `email`, sin distinguir mayúsculas → reutiliza ese `id_persona`.
3. Sin documento ni email, pero coinciden `nombre` + `fecha_nacimiento` →
   **no fusiona**: manda el alta a una cola de revisión humana.
4. Sin match → crea persona nueva.

**Alternativas descartadas.** *Fusionar por nombre y fecha de nacimiento.*
Homónimos con la misma fecha existen. Fusionarlos automáticamente sería
irreversible y silencioso; pedir una decisión humana es molesto pero
recuperable.

**Consecuencias.** Existe una cola de revisión que alguien tiene que atender.
A cambio, el sistema nunca destruye una identidad por su cuenta.

Esta regla se aplica en **las tres puertas**: el alta manual, la inscripción
pública y la ingesta por SAV usan el mismo `dedup.resolver`. Que la ingesta
pudiera crear duplicados que el alta manual habría evitado sería una
inconsistencia difícil de detectar y fácil de introducir.

**Dónde vive.** `functions/panel_api/dedup.py`, `revision.py`, `personas.py`,
`inscripciones.py`, `sav.py`.

---

<a id="d4"></a>
## D4 · El consentimiento se valida en el punto de uso, no en el alta

**El problema.** «Tiene consentimiento» no es un estado binario de una
persona. Alguien puede haber aceptado que lo contacten para participar y no
haber aceptado que sus respuestas se usen para buscar entre estudios. Son dos
permisos distintos y se retiran por separado.

**La decisión.** Dos finalidades independientes:

- `contacto_participacion` — habilita convocar y muestrear.
- `uso_semantico` — habilita ingestar respuestas al store semántico.

Tener una **no** implica tener la otra, y el gate se aplica cada vez que se
va a usar el dato, no una sola vez al enrolar. Una persona puede estar en un
panel y quedar fuera de una ingesta por no haber consentido el uso semántico.

**Alternativas descartadas.** *Un flag `consiente` en la persona.* No
representa la limitación de finalidad, y el día que alguien retira una sola
de las dos no hay forma de expresarlo.

**Consecuencias.** El retiro de consentimiento tiene efectos distintos según
cuál se retire, y eso está implementado: retirar `uso_semantico` borra los
embeddings pero la persona sigue en el panel; retirar
`contacto_participacion` la saca del muestreo pero los embeddings quedan;
retirar todo dispara la cascada completa y deja una lápida con el token
opaco —que no es PII— para poder probar que el retiro se atendió.

**Dónde vive.** `functions/panel_api/consentimiento.py`, `bajas.py`.

---

<a id="d5"></a>
## D5 · El cruce entre stores es lógico, sin FK

**El problema.** Las respuestas del store semántico pertenecen a un estudio
que vive en la bóveda. La forma natural de expresarlo sería una foreign key,
y no se puede: son dos bases distintas.

**La decisión.** Dos referencias lógicas, y nada más:

- `encuesta.ref_estudio` (bóveda) == `cuestionario.ref_estudio` (semántico),
  una uuid generada al crear la encuesta.
- `id_persona` como clave de la persona en toda la plataforma.

**Consecuencias.** La integridad referencial entre stores no la garantiza la
base: la garantiza el código, y hay una ruta de verificación de cruce
(`GET /encuestas/{id}/cruce`) que existe justamente para poder comprobar que
los dos lados coinciden. Es un costo aceptado a cambio de D1.

**Dónde vive.** `functions/panel_api/encuestas.py`, `semantica.py`.

---

# Bloque B · El motor de consultas

<a id="d6"></a>
## D6 · Los proveedores externos van detrás de una interfaz, con un doble sin red

**El problema.** Tres etapas del sistema llaman a un servicio ajeno:
embeddings (Voyage), reranking (Voyage) y verificación (Claude). Si las
pruebas los llaman de verdad, son lentas, cuestan plata, y —lo peor— no son
determinísticas: el mismo test pasa o falla según lo que conteste el modelo.

**La decisión.** Cada uno detrás de una interfaz mínima, con dos
implementaciones: la real y una sin red.

| Etapa | Proveedor real | Doble sin red |
|---|---|---|
| Embeddings | Voyage `voyage-3.5`, 1024 dims | `BolsaDePalabras` |
| Reranking | Voyage `rerank-2.5` | `Lexico` |
| Verificación | Claude | `Lexico` |
| Padrón de usuarios | Firebase Auth + Firestore | `EnMemoria` |

Los dobles **no son relleno**. El de embeddings le da a cada palabra una
dirección fija y suma, así que dos textos que comparten palabras salen cerca;
con un hash del texto completo —que es lo que hacía la primera versión— «me
encanta el fernet» quedaba tan lejos del criterio como cualquier otra frase,
y las pruebas de recall no probaban el recall. Los léxicos de reranking y
verificación usan un vocabulario de negación en español rioplatense: son
gruesos a propósito, y alcanzan para que el caso de polaridad opuesta dé
siempre el mismo resultado.

**Consecuencias.** Toda la suite corre sin red y sin gastar un centavo.
Cambiar de proveedor de embeddings es cambiar una clase, no tocar la
ingesta. Y el modo demo de la interfaz existe gracias a lo mismo.

**Dónde vive.** `functions/panel_api/embeddings.py`, `reranker.py`,
`verificacion.py`, `polaridad.py`, `usuarios.py`.

---

<a id="d7"></a>
## D7 · Una consulta demográfica no abre el store semántico

**El problema.** Los atributos demográficos son autoritativos en la bóveda.
Una consulta que solo pide «mujeres de Salto» no necesita nada del otro lado,
y abrir esa conexión igual es superficie de exposición gratuita.

**La decisión.** El contexto de la request abre todo en forma **perezosa**.
Una consulta puramente demográfica se resuelve entera en la bóveda y nunca
toca la propiedad `semantica`.

**Consecuencias.** Además del ahorro, la decisión es **verificable**: la
prueba usa un contexto cuyo store semántico lanza una excepción si alguien lo
toca, así que una regresión falla con un error explícito y no con una
aserción sutil.

El mismo criterio se extendió a los otros recursos caros: reranker y
verificador leen su clave de Secret Manager al construirse, y el padrón
levanta el cliente de Firestore. Ninguno se instancia en una request que no
los usa.

**Dónde vive.** `functions/panel_api/contexto.py`, `demografia.py`,
`functions/tests/conftest.py` (fixture `ctx_solo_boveda`).

---

<a id="d8"></a>
## D8 · El reranking corre antes de colapsar a individuo

**El problema.** El pipeline es: recall por vecino aproximado → reranking →
agrupar por persona → top-k → verificación. Hay dos órdenes posibles para los
pasos del medio, y eligen cosas distintas.

**La decisión.** Rerankear **antes** de colapsar a individuo.

**Por qué.** La distancia entre embeddings mide parecido temático, no si la
respuesta cumple el criterio. Si se colapsa primero —quedándose con la
respuesta de menor distancia de cada persona— se elige el representante de
cada persona con la métrica equivocada, y el reranker después solo puede
ordenar lo que ya se eligió mal. Rerankeando primero, cada respuesta compite
con su relevancia real y recién ahí se decide cuál representa a la persona.

**Consecuencias.** El reranker procesa más candidatos, y por lo tanto cuesta
más. Se acota con `FACTOR_SOBREPEDIDO = 4`: se pide al recall cuatro veces lo
que se necesita, para que después de agrupar por persona siga habiendo
suficientes.

**Dónde vive.** `functions/panel_api/consultas.py`.

---

<a id="d9"></a>
## D9 · La verificación no puede inventar evidencia

**El problema.** La etapa de verificación le pide a Claude que diga, para
cada persona del top-k, si cumple el criterio y con qué respuesta concreta.
Un modelo al que se le pide que escriba la cita puede escribir una cita que
suena perfecta y que nadie dijo nunca. En un sistema cuyo producto es «esta
persona dijo esto», eso no es un error tolerable.

**La decisión.** La garantía es **estructural, no está en el prompt**. El
modelo no escribe la cita: devuelve el **número de candidato**, y la cita se
arma del lado del sistema con el `valor_texto` que ya estaba guardado. Si
devuelve un número que no existe, ese veredicto se descarta.

Se usa la API de Claude con `tools` y `tool_choice` forzado a una
herramienta, para que la respuesta venga estructurada y no haya que parsear
prosa.

**Consecuencias.** Es imposible que el sistema muestre una cita que no esté
en el store semántico, independientemente de lo que el modelo alucine. A
cambio, el prompt es más rígido y hay que mantener el contrato de la
herramienta.

**Dónde vive.** `functions/panel_api/verificacion.py`.

---

<a id="d10"></a>
## D10 · La contradicción se busca en todas las evidencias, no en la mejor

**El problema.** Apareció al probar: un candidato con una respuesta neutral
entraba al ranking aunque en **otra** respuesta del mismo estudio decía
exactamente lo contrario de lo que se buscaba. Pasaba porque el colapso a
individuo se quedaba con su mejor evidencia, y la contradictoria se perdía.

**La decisión.** Se agrupan hasta `MAX_EVIDENCIAS_POR_INDIVIDUO = 3` por
persona y se verifican **todas**. Un veredicto `no_cumple` en cualquiera de
ellas excluye a la persona.

**Por qué así.** Buscar la confirmación en la mejor evidencia y la
contradicción en la mejor evidencia no son la misma operación. Para afirmar
que alguien cumple alcanza con una respuesta que lo diga; para descartar que
lo contradiga hay que mirar todo lo que dijo.

**Consecuencias.** `no_cumple` excluye **en los dos modos**, estricto y laxo.
La diferencia entre los modos es qué se hace con la ausencia y la duda: el
modo laxo las tolera, la contradicción no la tolera ninguno.

**Dónde vive.** `functions/panel_api/consultas.py`.

---

<a id="d11"></a>
## D11 · Si falta una clave, la consulta degrada y lo dice

**El problema.** El reranking y la verificación dependen de servicios
externos con su propia clave. Si una falta o el servicio no responde, hay dos
conductas posibles: fallar la consulta entera, o seguir sin esa etapa.

**La decisión.** Seguir, y **decirlo**. La respuesta trae un arreglo
`degradaciones` con qué etapa faltó y por qué, y la interfaz lo muestra como
un aviso ámbar arriba del ranking.

Con la verificación caída hay un ajuste que no es obvio: **la exclusión por
veredicto se suspende**. En modo estricto, exigir un `cumple` que nadie puede
emitir vaciaría el ranking, y un ranking vacío por falta de una clave se lee
como «no hay nadie», que es una respuesta falsa.

**Alternativas descartadas.** *Fallar la consulta.* Deja al analista sin nada
cuando podría haberle dado un resultado peor pero utilizable.
*Degradar en silencio.* Peor que fallar: el analista recibe un ranking sin
rerankear creyendo que está rerankeado.

**Consecuencias.** Hay que mirar la tarjeta de diagnóstico para saber en qué
condiciones corrió una consulta. Por eso la respuesta siempre dice qué
proveedor atendió cada etapa.

**Dónde vive.** `functions/panel_api/consultas.py`, `reranker.py`,
`verificacion.py`, `web/public/js/paginas/consultas.js`.

---

# Bloque C · Auditoría y control de acceso

<a id="d12"></a>
## D12 · Reidentificar queda registrado; exportar es un evento distinto

**El problema.** El puente entre stores existe para poder traducir un
`id_persona` a una persona: sin eso, un ranking de tokens opacos no sirve
para convocar a nadie. Pero esa traducción es la operación que deshace la
seudonimización, o sea lo único que el diseño de dos stores estaba evitando.

**La decisión.** Cada traducción **deliberada** queda anotada con quién, a
quién, cuándo y con qué motivo. Y en la Fase 3, exportar a un archivo se
registra con un motivo **propio** (`exportacion`), distinto del `consulta`
que deja haberla mirado en pantalla.

**Por qué separarlos.** Ver una lista en pantalla y llevarse un CSV con
nombres, documentos y teléfonos no son el mismo riesgo. El archivo sobrevive
a la sesión, viaja por correo y termina en un escritorio compartido. Que los
dos eventos se distingan en el registro es lo que permite responder «¿quién
se llevó datos?», que es una pregunta distinta de «¿quién los vio?».

Lo que **no** se registra: listar personas que ya se venían mostrando en
pantalla, ni el resultado de una consulta (que es de `id_persona`). Registrar
todo sería registrar nada: el volumen taparía los eventos que importan.

**Consecuencias.** Son dos permisos distintos —`reidentificar` y
`exportar_identificado`— aunque hoy los tengan los mismos roles (`admin` y
`operaciones`). Que estén separados no es redundancia: permite endurecer uno
sin tocar el otro ni el código que los exige, y hace que el registro
distinga las dos acciones aunque quien las haga sea la misma persona. El
analista, que sí puede `consultar`, no tiene ninguno de los dos.

**Dónde vive.** `functions/panel_api/auditoria.py`, `consultas.py`,
`ruteo.py`, `auth.py`.

---

<a id="d13"></a>
## D13 · La gestión de usuarios es escalada de privilegios, y se trata como tal

**El problema.** Quien puede dar de alta a alguien con rol `admin` puede
darle acceso a toda la bóveda. No es una tarea administrativa más.

**La decisión.** Cuatro restricciones que no son adornos:

1. **Permiso propio** (`gestionar_usuarios`), solo para `admin`. No alcanza
   con ser de operaciones.
2. **Nadie se puede sacar a sí mismo el rol de admin ni desactivarse.** No es
   paternalismo: evita quedarse sin ningún administrador y tener que volver a
   entrar por el script de emergencia.
3. **Rol desconocido, rechazado.** Un rol inventado no falla al escribirse:
   falla después, cuando la persona entra y ningún permiso le aplica.
4. **Todo queda auditado** con autor y fecha, en la bóveda.

Además, la clave inicial no se muestra de forma persistente: se envía un
enlace de establecer contraseña, visible una sola vez.

**Consecuencias.** El padrón vive en Firestore (`usuarios/{uid}`) y no en
Cloud SQL, porque son empleados de Equipos y no panelistas: ahí no hay PII de
panelista. Y se conserva el script de línea de comandos para el bootstrap del
primer admin, porque un sistema recién desplegado no tiene por dónde entrar.

**Dónde vive.** `functions/panel_api/usuarios.py`, `auth.py`,
`scripts/alta_usuario.js`.

---

<a id="d14"></a>
## D14 · El esquema desplegado se verifica, no se supone

**El problema.** Esta decisión salió de una falla real en producción. Las
migraciones se aplican a mano contra cada instancia de Cloud SQL. Una que no
se aplicó **no impide arrancar**: el sistema funciona, la mayoría de las
pantallas andan, y la que necesitaba el objeto que falta se cae semanas
después con un error de Postgres que solo aparece en los logs. Al usuario le
llega «Error interno del servidor».

La causa más probable de aquel caso: `psql` sigue adelante después de un
error si no se le pasa `ON_ERROR_STOP`, así que un `\i` fallido imprime el
error, ejecuta igual los siguientes y termina normal. La sesión se ve bien y
la base queda a medio migrar.

**La decisión.** Cerrar el hueco por cuatro lados:

1. **Una lista declarada** de qué objetos crea cada migración, en el código.
2. **`verificar()`** compara esa lista con lo que hay en la base, expuesta en
   `GET /api/diagnostico/esquema` y en la pantalla de Cumplimiento.
3. **`explicar_error()`** traduce el error crudo de Postgres a una frase
   accionable, para que el 500 diga qué archivo aplicar.
4. **`scripts/verificar_esquema.py`** hace lo mismo desde la terminal, para
   poder comprobarlo **antes** de desplegar.

**Dos sub-decisiones que costaron caro y conviene no revertir:**

- **Se consulta `pg_catalog`, no `information_schema`.** `information_schema`
  filtra por privilegios: un usuario que llega a la base correcta pero sin
  permisos sobre las tablas ve cero filas, y el verificador concluía que
  faltaban **todas** las migraciones, con los comandos para re-correrlas
  sobre una base que ya las tenía. Un problema de acceso disfrazado de
  diagnóstico es la peor forma de fallar para una herramienta cuyo trabajo es
  decir si la base está bien.
- **Cada informe dice contra qué base y con qué usuario corrió.** Un DSN
  vacío no hace fallar a `psql`: lo manda a sus valores por omisión, o sea a
  la base `postgres`, que no tiene ninguna de estas tablas. La respuesta
  entonces es segura y equivocada.

**Cómo se verifica.** La lista declarada se compara con el DDL real **en las
dos direcciones**: si una migración crea algo que no está declarado, falla; y
si se declara algo que el DDL no crea, también. Además las dos vías —la de
Python y la consulta SQL suelta— tienen que dar el mismo resultado con la
base al día y con objetos faltantes.

**Dónde vive.** `functions/panel_api/esquema.py`,
`scripts/verificar_esquema.py`, `functions/tests/test_esquema.py`, y los manuales de
despliegue de las tres fases.

---

# Bloque D · Muestreo y calidad

<a id="d15"></a>
## D15 · El muestreo explica cada exclusión

**El problema.** Una herramienta que propone diez nombres sin decir por qué
faltan los otros doscientos no es una herramienta de decisión: es una caja
negra que hay que creerle.

**La decisión.** La propuesta devuelve tres cosas con el mismo peso: a quién
invitar, **por qué está cada uno** (qué brecha cierra), y **quiénes quedaron
afuera con su motivo**. Los motivos son concretos y están enumerados: sin
consentimiento, pendiente de consentimiento, ya convocado a esta encuesta,
demasiadas convocatorias recientes, demasiadas acumuladas, convocado hace muy
poco, o cuota del segmento ya cubierta.

Además, **la propuesta no convoca**. Devuelve una sugerencia que el
responsable confirma; convocar sigue siendo un acto explícito. La respuesta
lo dice literalmente (`"convoca": false`) para que no haya duda de qué pasó
al pedirla.

**Consecuencias.** La respuesta es más grande y la pantalla tiene dos mitades
en vez de una. Es el punto: la mitad que se suele esconder es la que permite
decidir con criterio.

**Dónde vive.** `functions/panel_api/muestreo.py`,
`web/public/js/paginas/muestreo.js`.

---

<a id="d16"></a>
## D16 · El muestreo no completa el cupo empeorando la brecha

**El problema.** Si el responsable pide 100 personas y solo hay 40 elegibles
en el segmento que tiene brecha, ¿qué se devuelve? La conducta intuitiva es
completar hasta 100 con quien haya.

**La decisión.** Devolver 40, y explicar por qué. Con objetivo de composición
cargado, el cupo sobrante se completa **solo con segmentos que todavía tienen
brecha**; nunca con los que ya están cubiertos.

**Por qué.** Completar con gente del segmento sobrerrepresentado agregaría
personas del lado que ya sobra, y alejaría al panel de su universo de
referencia: empeoraría exactamente la brecha que la propuesta viene a cerrar.
Una propuesta más corta es la respuesta correcta.

**Consecuencias.** Pedir 100 y recibir 40 se lee como una falla si no se
explica, así que hay dos avisos: uno por segmento que dice que la brecha no
se cierra con esta propuesta y qué la limita, y otro al pie que dice que la
propuesta vino corta a propósito.

**Un caso que hubo que corregir.** Cuando el panel **ya calza** con su
universo, la primera versión avisaba «este segmento tiene brecha (0 personas)
y no alcanza» —una contradicción—. Apareció recorriendo la interfaz en un
navegador, no leyendo el código. Ahora, sin brecha, el sistema dice que el
panel ya calza y reparte parejo. Hay prueba de regresión.

**Dónde vive.** `functions/panel_api/muestreo.py` (`_repartir`),
`functions/tests/test_fase3_salud.py`.

---

<a id="d17"></a>
## D17 · Silencio no es aprobado

**El problema.** El chequeo de speeder necesita la duración de cada
respuesta, y no todos los exports de campo la traen. Si el chequeo
simplemente no corre, el resultado es un informe donde nadie está marcado
como speeder, que es indistinguible de un informe donde nadie lo es.

**La decisión.** Todo chequeo que no se puede correr **se informa
explícitamente**. El informe trae una sección `no_evaluado` con qué chequeo
no corrió y por qué, con el texto que hace falta:

> «Que no haya marcas de speeder NO significa que no los haya: significa que
> no se pudo mirar.»

Lo mismo vale para el straightliner cuando el estudio no tiene ninguna
batería de escalas con suficientes ítems.

**Consecuencias.** Es la regla más transversal de la fase, y la que más
fácilmente se pierde al «simplificar» un informe. Un `None` que significa «no
evaluado» no puede colapsarse a `False` en ningún punto del camino.

**Dónde vive.** `functions/panel_api/calidad.py`,
`functions/tests/test_fase3_salud.py`.

---

<a id="d18"></a>
## D18 · Marcar calidad es reversible, y queda quién lo hizo

**El problema.** Un falso positivo de speeder —alguien que responde rápido
pero bien— le cuesta puntos a una persona real. Y a partir de la liquidación
por calidad, una marca automática tiene consecuencias económicas.

**La decisión.** Tres cosas:

1. La marca se puede revertir, y la reversión guarda **quién** y **con qué
   motivo**.
2. Una corrida automática **nunca pisa una revisión humana**: si alguien ya
   revisó esa participación, el chequeo la respeta y lo informa.
3. Revertir a `ok` **habilita liquidar** el punto que había quedado
   pendiente, y la respuesta lo dice para que la app pueda ofrecerlo.

**Por qué el punto 2.** Quien revisó miró el caso; el chequeo, no. Que una
re-corrida borre el trabajo de una persona convertiría la revisión en algo
que no vale la pena hacer.

**Dónde vive.** `functions/panel_api/calidad.py` (`revisar`), `puntos.py`.

---

# Bloque E · Gamificación

<a id="d19"></a>
## D19 · El saldo de puntos es la suma de los movimientos, no un campo

**El problema.** Lo más simple es una columna `saldo` en la persona que se
suma y se resta. Funciona hasta la primera vez que alguien tiene que
responder «¿de dónde salió este número?».

**La decisión.** No existe ninguna columna `saldo` en ninguna tabla. El saldo
es `sum(puntos)` sobre `puntos_movimiento`, y cada movimiento tiene tipo
(`earn`, `canje`, `ajuste`, `vencimiento`), cantidad, motivo y fecha.

De ahí salen dos reglas más:

- **El vencimiento descuenta, no borra.** Un movimiento de `vencimiento`
  apunta al `earn` que venció. Borrar filas dejaría un saldo correcto y una
  historia falsa.
- **Cancelar un canje devuelve los puntos como movimiento nuevo**, no
  deshaciendo el descuento: el descuento ocurrió, y el ledger no se
  reescribe.

**Alternativas descartadas.** *Campo mutable con historial al lado.* Es lo
peor de los dos mundos: dos fuentes de verdad que se desincronizan, y la
primera corrección manual rompe la correspondencia.

**Consecuencias.** Leer el saldo cuesta una agregación en vez de un `select`
de una columna. Con los volúmenes de un panel eso no es un problema, y a
cambio el saldo siempre es reconstruible.

**Un detalle del vencimiento.** Si la persona ya gastó esos puntos, el lote
se marca vencido pero **no se descuenta de nuevo**: el gasto ya lo sacó del
saldo, y volver a restarlo sería cobrárselo dos veces.

**Dónde vive.** `functions/panel_api/puntos.py`, `db/boveda/0001_init.sql`.

---

<a id="d20"></a>
## D20 · El sobregiro se evita bloqueando, no comprobando

**El problema.** «Leer el saldo, ver si alcanza, descontar» parece correcto y
no lo es: entre leer y descontar cabe otra transacción. Dos canjes
simultáneos con saldo para uno solo leen el mismo saldo, los dos concluyen
que alcanza, y los dos escriben. El saldo termina negativo sin que ninguna de
las dos transacciones haya hecho nada malo por separado.

Y no se puede resolver con un constraint: una suma no se restringe por fila.

**La decisión.** Bloquear la fila de `persona` antes de leer el saldo
(`select … for update`). Eso serializa *la decisión sobre esa persona*. Se
bloquea la persona y no los movimientos porque las filas que habría que
bloquear son justamente las que todavía no existen.

En el canje se toman **dos** bloqueos, y el orden importa: primero la persona
—que serializa el saldo—, después el premio —que serializa el stock—.
**Siempre en ese orden**, porque dos canjes que los tomaran en orden distinto
podrían quedarse esperándose mutuamente.

**Cómo se verifica.** Con dos conexiones reales y una barrera de
sincronización: el bug solo aparece cuando las transacciones se solapan, y
con una sola conexión no se puede reproducir. La prueba exige que uno
prospere, el otro sea rechazado, y el saldo quede en cero.

**Una garantía análoga, por otro mecanismo.** «No se puede liquidar dos
veces» se apoya en un índice único parcial sobre `(id_persona, encuesta_id)`
para los movimientos de tipo `earn`. La comprobación en Python existe para
dar un mensaje decente; **la garantía es del índice**, y hay una prueba que
lo fuerza directamente con un `insert` a mano.

**Dónde vive.** `functions/panel_api/premios.py`, `puntos.py`,
`db/boveda/0005_fase3.sql`, `functions/tests/test_fase3_salud.py`.

---

# Bloque F · La landing pública

Es la única superficie del sistema sin login. Las cuatro decisiones de este
bloque existen por eso.

<a id="d21"></a>
## D21 · Una inscripción pública no es una persona

**El problema.** El requisito pide que una inscripción quede pendiente de
aprobación, que no entre a ningún panel, y que no pueda ser convocada ni
aparecer en consultas semánticas hasta que alguien la apruebe.

La forma directa sería crear la persona con un flag `aprobada = false` y
acordarse de filtrar por ese flag en cada consulta, cada convocatoria y cada
muestreo.

**La decisión.** No crear la persona. La inscripción se guarda en una tabla
propia (`inscripcion`) y **recién al aprobar** se crea el panelista, con el
consentimiento que el titular dio y la versión de texto que aceptó.

**Por qué.** Así, «un pendiente no puede ser convocado ni consultado» no es
una condición que alguien tenga que acordarse de escribir en cada consulta
nueva: **es cierto porque no existe todavía como panelista**. La garantía
sale gratis y no se puede olvidar.

**Consecuencias.** La landing nunca escribe en `persona`, que es la tabla con
toda la PII. Y la aprobación es un acto humano, explícito y auditable, que
además pasa por el dedup de [D3](#d3): si la persona ya existe, se reutiliza
su `id_persona`; si el caso es ambiguo, va a la cola de revisión.

**Dónde vive.** `functions/panel_api/inscripciones.py`,
`db/boveda/0005_fase3.sql`.

---

<a id="d22"></a>
## D22 · La landing responde siempre lo mismo

**El problema.** Es tentador que el formulario sea amable: «ya estás
registrado», «ese documento ya existe». Cada una de esas respuestas convierte
el formulario en un **oráculo**: cualquiera puede averiguar si una persona
pertenece al panel probando documentos, sin autenticarse.

**La decisión.** Todos los envíos válidos reciben el mismo acuse de recibo,
palabra por palabra. Adentro, la resolución de identidad corre igual y su
resultado queda guardado para que lo vea quien aprueba; afuera, no se filtra
nada.

Lo mismo con el reenvío del mismo correo: se resuelve con un
`on conflict do nothing` y se responde igual. Quien reenvía no tiene por qué
enterarse de que ya había una.

**Cómo se verifica.** La prueba inscribe a alguien que **ya es panelista** y
a alguien completamente nuevo, y exige que las dos respuestas sean idénticas
—`conocida == desconocida`—. Además comprueba que la respuesta no traiga
`id_persona`, `resolucion` ni `id`.

**Dónde vive.** `functions/panel_api/inscripciones.py` (constante `ACUSE`),
`functions/tests/test_fase3_crecimiento.py`.

---

<a id="d23"></a>
## D23 · El texto de consentimiento se versiona y no se reescribe

**El problema.** El texto legal va a cambiar. Si se edita en su lugar, el
`version_texto` que guarda cada consentimiento deja de identificar un
documento y pasa a ser una etiqueta sin contenido estable. Eso destruye el
valor probatorio de **todo** el registro de consentimiento, no solo el de la
landing.

**La decisión.** Cada versión es una fila nueva. Publicar una versión que ya
existe se **rechaza** con un error explícito. Los consentimientos anteriores
conservan la suya, y el cuerpo exacto que esa persona aceptó sigue siendo
recuperable.

Hay un detalle más: si el texto cambia mientras alguien está completando el
formulario, el envío se rechaza y se le pide que vuelva a cargarlo. Aceptarlo
registraría un consentimiento a un texto que esa persona no leyó.

**Cómo se verifica.** Se inscribe a alguien, se aprueba, se publica una
versión nueva, y se comprueba que la versión guardada en su consentimiento
**no cambió** y que el cuerpo viejo sigue siendo recuperable.

**Dónde vive.** `functions/panel_api/inscripciones.py`,
`db/boveda/0005_fase3.sql`.

---

<a id="d24"></a>
## D24 · El repositorio no trae ningún texto legal

**El problema.** La spec marca el texto de consentimiento de la landing como
bloqueante: lo tiene que redactar y revisar el DPO. La tentación es sembrar
un texto de ejemplo en la migración para que el formulario «funcione».

**La decisión.** No hay ningún texto legal en el repositorio ni en la
migración. Sin un texto publicado, el formulario público responde que no está
disponible y **rechaza cualquier envío**.

**Por qué.** Si la migración sembrara uno, alguien iba a publicar la landing
creyendo que sirve. Un consentimiento inválido recogido de buena fe es peor
que no tener landing: hay personas reales que creen haber consentido algo.

**Consecuencias.** El estado de fábrica de la landing es «cerrada», y eso es
correcto. La pantalla de administración lo avisa de entrada, y el manual de
despliegue lo documenta como paso obligatorio antes de anunciarla.

**Dónde vive.** `functions/panel_api/inscripciones.py`,
`docs/DESPLIEGUE - Fase 3.md` §1.2.

---

# Bloque G · Fricción operativa

<a id="d25"></a>
## D25 · Las altas por SAV quedan pendientes de consentimiento

**El problema.** El alta manual rechaza crear una persona sin consentimiento
registrado: es la columna vertebral de cumplimiento. La ingesta por SAV en
modo «crear los individuos en esta carga» entra por otra puerta, y la spec
deja **abierto** con qué base legal. Sin una definición, esa puerta permite
poblar la bóveda salteando la regla.

**La decisión.** De las dos opciones que plantea la spec, se implementó la
conservadora: esas personas se crean en estado `pendiente_consentimiento`.
Existen en la bóveda —hacen falta para vincular las respuestas— pero:

- el muestreo las excluye, con su propio motivo;
- no pueden ser convocadas;
- el resultado de la ingesta lo avisa explícitamente, con el conteo.

Hay una operación para regularizarlas: registra el consentimiento y activa a
la persona en un solo paso, porque separarlas dejaría el estado y el
consentimiento en desacuerdo.

**Por qué esta y no la otra.** La alternativa —que el archivo traiga
evidencia de consentimiento en una variable— requiere confiar en la calidad
de un dato que viene de afuera para una decisión legal. Es viable, pero es
una decisión de Equipos y su DPO, no del código. La conservadora no cierra
esa puerta: la deja cerrada hasta que alguien la abra a propósito.

**Cómo se verifica.** Que el estado exista no alcanza: tiene que tener
consecuencias. Hay una prueba que crea individuos por SAV, pide una propuesta
de muestreo y exige que la propuesta venga **vacía** y que todos los
excluidos tengan el motivo `pendiente_de_consentimiento`.

**Dónde vive.** `functions/panel_api/sav.py`, `muestreo.py`,
`db/boveda/0005_fase3.sql`, `docs/DESPLIEGUE - Fase 3.md` §1.1.

---

<a id="d26"></a>
## D26 · El `.sav` se parsea en el backend y su metadata es una propuesta

**El problema.** Un `.sav` de SPSS trae adentro los códigos de las variables,
los textos de las preguntas y los mapeos de etiquetas que hoy se tipean a
mano. Aprovecharlo ahorra trabajo y errores. Pero la metadata de SPSS suele
venir truncada —SPSS corta los variable labels— o críptica.

**La decisión.** Dos partes:

- **El parseo corre en el backend**, no en el navegador. No hay librería
  cliente confiable para `.sav`, los archivos de campo pesan, y subir el
  archivo entero es lo que permite validarlo antes de escribir nada.
- **Lo que devuelve es una propuesta editable**, no un hecho. Ninguna
  variable se incluye hasta que alguien la marca, y los textos dudosos se
  señalan: vacíos, muy cortos (probablemente un código y no una pregunta) o
  exactamente en el límite de 256 caracteres de SPSS (probablemente
  truncados).

**Por qué importa tanto el texto.** Lo que se vectoriza es el texto de la
pregunta. Si queda «P5_1», la consulta semántica sobre ese estudio no va a
servir para nada, y el problema no se nota hasta meses después, cuando
alguien busca algo y no aparece.

**Una consecuencia útil.** El tipo se infiere de `measure` y del tipo de
dato. Las variables con etiquetas y measure ordinal o de escala se marcan
como `escala`, y eso alimenta directamente la detección de straightliners:
las baterías se agrupan por juego de opciones compartido, sin pedirle al
analista que declare nada.

**Dónde vive.** `functions/panel_api/sav.py`, `calidad.py` (`baterias`),
`web/public/js/paginas/encuestas.js`.

---

<a id="d27"></a>
## D27 · Exportar con datos exige haber reidentificado

**El problema.** El CSV con nombres y documentos podría resolver la PII por
su cuenta a partir de la lista de `id_persona`. Sería más cómodo: un botón,
un archivo.

**La decisión.** No. La ruta recibe el resultado **ya reidentificado** y lo
usa tal cual, sin volver a consultar la bóveda. El botón está deshabilitado
hasta que se haya usado «Ver quiénes son».

**Por qué.** Si exportar pudiera resolver la PII por su cuenta, habría **dos
caminos** para sacarla de la bóveda, y mantener los dos auditados es una
tarea que tarde o temprano se descuida. Con uno solo, el registro de
reidentificación es completo por construcción.

**Qué lleva y qué no.** Los mismos campos que devuelve la reidentificación,
**menos** la fecha de nacimiento exacta y las observaciones. La primera es un
identificador fino —el tramo etario da la información demográfica sin él— y
las segundas son texto libre donde suele terminar cayendo dato sensible.

El archivo lleva la marca en el nombre (`consulta-CON-DATOS-PERSONALES-…`) y
en su primera línea. Un CSV con nombres que viaja por correo sin decir lo que
es termina, tarde o temprano, en un escritorio compartido.

**Cómo se verifica.** La prueba más directa: se exporta con personas que **no
existen en esa base**. Si el CSV sale igual, es que no fue a buscarlas.

**Dónde vive.** `functions/panel_api/consultas.py`, `ruteo.py`,
`web/public/js/paginas/consultas.js`.

---

<a id="d28"></a>
## D28 · Un panel creado desde una consulta es una foto

**El problema.** Crear un panel con los individuos que salieron de una
consulta invita a una idea peligrosa: que el panel se mantenga sincronizado
con la consulta.

**La decisión.** Es una foto. Se guarda la definición que lo originó
—`panel.origen_definicion`— pero para poder **rastrear** de dónde salió su
composición, no para recalcularla. Los paneles dinámicos son un no-goal
explícito de la fase.

**Por qué.** Un panel que cambia solo hace que una convocatoria enviada ayer
no se corresponda con el panel de hoy, y que dos análisis del mismo panel en
fechas distintas no sean comparables.

**Una decisión adicional.** Si el resultado venía de una consulta semántica,
la creación deja un registro **equivalente al de reidentificación**.
Materializar un ranking en un panel es, en los hechos, fijar una lista de
personas concretas sobre las que se va a trabajar. No es la misma acción que
ver sus nombres, pero es un momento que merece quedar anotado.

**Consecuencias.** La operación es idempotente en las membresías, informa
quiénes no pueden ser convocados —el gate de consentimiento sigue
aplicando—, y lista los `id_persona` que ya no existen: alguien pudo darse de
baja entre la consulta y la creación, y eso es la baja funcionando bien.

**Dónde vive.** `functions/panel_api/paneles.py` (`desde_consulta`).

---

<a id="d29"></a>
## D29 · Las rutas públicas se enumeran una por una

**El problema.** La landing necesita dos rutas sin token. La forma cómoda de
expresarlo es un prefijo: «todo lo que cuelgue de `/inscripciones/` es
público».

**La decisión.** Una lista de rutas **concretas**, método y camino, en una
constante que el punto de entrada consulta:

```python
PUBLICAS = frozenset({
    ("GET",  "/inscripciones/formulario"),
    ("POST", "/inscripciones"),
})
```

**Por qué.** Un prefijo abierto se convierte, la primera vez que alguien
agrega una ruta debajo, en un agujero que nadie eligió abrir. Con esta forma,
`GET /inscripciones` —la bandeja de aprobación, que lista datos de
personas— **no** es pública, y para que lo fuera habría que escribirlo.

**Cómo se verifica.** Una prueba compara el conjunto completo con el
esperado. Si alguien agrega o saca una ruta pública, la prueba falla y hay
que decidirlo a propósito.

**Dónde vive.** `functions/panel_api/ruteo.py`, `main.py`,
`functions/tests/test_fase3_crecimiento.py`.

---

# Bloque H · Método de trabajo

<a id="d30"></a>
## D30 · Las pruebas corren contra un Postgres real

**El problema.** Un doble de la base es más rápido y no necesita
infraestructura.

**La decisión.** No hay dobles de la base. Las pruebas corren contra un
Postgres real con pgvector, que `scripts/pg_pruebas.sh` levanta.

**Por qué.** Las garantías que hay que probar **son de la base**: el dedup
depende de índices únicos parciales, la cascada de `on delete cascade`, el
sobregiro del bloqueo de fila, la doble liquidación de un índice único, y la
recuperación semántica de pgvector. Probar eso contra un doble no probaría
nada: probaría que el doble se comporta como uno cree que se comporta
Postgres.

**Consecuencias.** La suite tarda unos minutos y necesita el cluster
levantado. A cambio, las pruebas de concurrencia son reales —dos conexiones,
una barrera— y los errores que encuentran son errores que iban a pasar en
producción.

Lo que **sí** tiene dobles son los servicios externos ([D6](#d6)): ahí el
determinismo importa más que la fidelidad.

**Dónde vive.** `functions/tests/conftest.py`, `scripts/pg_pruebas.sh`.

---

<a id="d31"></a>
## D31 · Los defaults numéricos están en el código, no en la base

**El problema.** Umbrales de fatiga, de calidad, puntos por participación,
tamaños de pool. Todos necesitan un valor inicial y todos van a cambiar
cuando se calibren contra datos reales.

**La decisión.** El valor por defecto vive en el código, como constante
nombrada y documentada; la base guarda **solo** las configuraciones que
alguien cambió explícitamente. Una fila ausente significa «rigen los
defaults», y la respuesta de la API lo dice (`son_defaults`).

**Por qué.** Sembrar los defaults en la base los vuelve indistinguibles de un
valor calibrado. Con esta forma, la interfaz puede decir *«con los umbrales
por defecto: todavía nadie los calibró contra los datos de Equipos»*, que es
información que el responsable necesita para saber cuánto confiar en lo que
está viendo.

**Los valores actuales, y que ninguno está medido:**

| Constante | Valor | Qué gobierna |
|---|---|---|
| `max_convocatorias_ventana` / `ventana_dias` | 3 / 90 | Fatiga: tope por ventana |
| `dias_minimos_entre` | 14 | Fatiga: descanso entre olas |
| `SPEEDER_SEGUNDOS` | 120 | Calidad: duración mínima |
| `STRAIGHTLINER_VARIANZA` | 0.25 | Calidad: varianza mínima de una batería |
| `MIN_ITEMS_BATERIA` | 4 | Cuántos ítems hacen significativa una varianza |
| `PUNTOS_POR_PARTICIPACION` | 100 | Gamificación: earn por participación de calidad |
| `MESES_DE_VIGENCIA` | 12 | Gamificación: vencimiento de los puntos |
| `TOP_N_POR_DEFECTO` / `TOP_K_POR_DEFECTO` | 200 / 25 | Consulta: pool y ranking |
| `UMBRAL_DISTANCIA` | 0.55 | Consulta: corte de similitud |
| `UMBRAL_SEGMENTO` | 5000 | Consulta: cuándo conviene cada estrategia de puente |

Los umbrales de calidad son además configurables **por estudio**, no solo por
sistema: lo que es rápido en un cuestionario de cinco minutos no lo es en uno
de treinta, y un umbral global sería falso para casi todos.

**Consecuencias.** La calibración empírica es trabajo pendiente y está
anotada como tal en los manuales de despliegue y en los riesgos de la spec.
Este documento no la reemplaza: la hace visible.

**Dónde vive.** `functions/panel_api/muestreo.py`, `calidad.py`, `puntos.py`,
`consultas.py`.

---

## Anexo · Decisiones que no se tomaron

Cosas que quedaron abiertas a propósito, para que no se confundan con olvidos:

| Tema | Estado | Dónde está anotado |
|---|---|---|
| Base legal del alta por SAV | Implementada la opción conservadora; la definitiva la decide Equipos con su DPO | [D25](#d25), `SPEC_fase3.md` §11 |
| Texto de consentimiento de la landing | Pendiente del DPO; el sistema lo trata como dato | [D24](#d24) |
| Tratamiento fiscal del canje | No-goal explícito de la Fase 3 | `SPEC_fase3.md` §3 |
| Calibración de todos los umbrales | Pendiente, contra datos de Equipos | [D31](#d31) |
| Optimización de muestreo con restricciones | Fase 4 (R4.2); la Fase 3 es por reglas | [D15](#d15) |
| Endurecimiento de la landing (verificación de contacto, anti-fraude) | Fase 4 (R4.4) | `docs/DESPLIEGUE - Fase 3.md` §5.4 |
| Análisis longitudinal | Fase 4 | `SPEC_fase3.md` §3 |
| Alinear el voseo de la interfaz con el registro formal del manual | Sin decidir; requeriría recapturar las 44 pantallas | PR de la Fase 2 |
