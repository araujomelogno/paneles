# Decisiones de diseño

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Alcance:** Fases 1, 2 y 3
**Última actualización:** 2026-09-14

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
| [D25](#d25) | La base legal del alta por SAV viaja en el archivo | 3 |
| [D26](#d26) | El `.sav` se parsea en el backend y su metadata es una propuesta | 3 |
| [D27](#d27) | Exportar con datos exige haber reidentificado | 3 |
| [D28](#d28) | Un panel creado desde una consulta es una foto | 3 |
| [D29](#d29) | Las rutas públicas se enumeran una por una | 3 |
| [D30](#d30) | Las pruebas corren contra un Postgres real | 1-3 |
| [D31](#d31) | Los defaults numéricos están en el código, no en la base | 3 |
| [D32](#d32) | La ingesta incorpora al panel y registra la participación | 3 |
| [D33](#d33) | Los demográficos del archivo van a la bóveda y no al store semántico | 3 |
| [D34](#d34) | El identificador viaja al campo en vez de adivinarlo a la vuelta | 3 |
| [D35](#d35) | Incorporar individuos y hacerlos panelistas son dos cosas distintas | 3 |
| [D36](#d36) | Los segmentadores son un catálogo, no una lista en el código | 3 |
| [D37](#d37) | Poder contactar y poder contactar por un canal son dos permisos | 4 |
| [D38](#d38) | La landing verifica el contacto antes de existir la inscripción | 4 |
| [D39](#d39) | Guardar un solo valor vigente por atributo era un número mal calculado | 4 |
| [D40](#d40) | La comparabilidad entre olas la declara el analista, no el sistema | 4 |
| [D41](#d41) | El optimizador propone y explica; no es un solver | 4 |
| [D42](#d42) | De WhatsApp se elige la plantilla, y nada más | 4 |

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
## D25 · La base legal del alta por SAV viaja en el archivo

> **Reemplaza a la versión anterior de esta decisión**, que creaba esas
> personas en estado `pendiente_consentimiento`. Aquella era la opción
> conservadora mientras la definición legal estuviera abierta; ya no lo está.

**El problema.** El alta manual (R1.1) rechaza crear una persona sin
consentimiento registrado: es la columna vertebral de cumplimiento. La
ingesta por SAV en modo «crear los individuos en esta carga» entra por otra
puerta, y sin una definición esa puerta permite poblar la bóveda salteando la
regla.

La spec planteaba dos salidas: que el archivo traiga la evidencia de
consentimiento, o que las personas queden en un estado pendiente hasta
regularizarse.

**La decisión.** La primera. Al importar hay que declarar, de forma
obligatoria y para **cada** finalidad, tres cosas:

| Qué se declara | Por qué hace falta |
|---|---|
| **Variable** del archivo que contiene la respuesta de consentimiento | Es dónde está la evidencia |
| **Valor** que cuenta como afirmativo | Un `1`, un `Sí`: sin esto no se sabe qué respuesta es un sí |
| **Versión del texto** consentido en campo | Es lo que hace demostrable *qué* aceptó la persona |

Las dos finalidades —`contacto_participacion` y `uso_semantico`— pueden
apuntar a la **misma variable**: un cuestionario con una sola pregunta de
consentimiento es el caso normal, y declararla dos veces es decir
explícitamente que esa pregunta cubre las dos cosas.

De ahí salen tres reglas:

1. **Sin declaración, no se importa.** La importación se rechaza antes de
   leer una sola fila. No es un default que se pueda omitir.
2. **Sin evidencia en la fila, no se crea la persona.** Quien no consintió el
   contacto no entra a la bóveda: no queda pendiente, no queda a medias, no
   entra. Se informa cuántas filas quedaron afuera y con qué valor.
3. **Cada finalidad se evalúa por separado.** Quien consiente el contacto y
   no el uso semántico entra al panel con un solo consentimiento, y el gate
   de R1.3 deja sus respuestas fuera del store semántico.

**Por qué esta y no la otra.** La opción del estado pendiente funciona, pero
deja una deuda que alguien tiene que acordarse de pagar: personas en la
bóveda esperando una regularización que nadie tiene agendada. La evidencia en
el archivo, en cambio, hace que la puerta del SAV exija **lo mismo** que la
del alta manual, y no deja ningún estado intermedio.

El costo es real y hay que decirlo: **si el cuestionario de campo no incluye
la pregunta de consentimiento, no se puede dar de alta a nadie desde ese
archivo.** Eso mueve un requisito del software al diseño del cuestionario, que
es donde corresponde: el consentimiento se pide a la persona, no se deduce
después.

**Consecuencias.**

- El estado `pendiente_consentimiento` y la operación de regularizar quedan
  como **transitorios**, para las personas creadas con la versión anterior.
  Cuando no quede ninguna, se pueden retirar.
- El consentimiento evidenciado **también se registra a quien ya existe**: es
  evidencia nueva sobre una persona conocida.
- Re-importar el mismo archivo no duplica consentimientos idénticos.
  `consentimiento.otorgar` agrega una fila cada vez a propósito —el historial
  no se pisa— pero el mismo dato dos veces no es un consentimiento nuevo, y
  llenar la tabla de filas idénticas haría ilegible el registro que tiene que
  servir de prueba.
- El caso ambiguo del dedup se lleva el consentimiento a la cola de revisión,
  para que quien la resuelva no tenga que volver al archivo.
- La comparación no distingue mayúsculas ni espacios sobrantes. En un export
  de campo conviven «Si», «SI » y «sí»; rechazar a alguien por eso sería un
  error de importación disfrazado de falta de consentimiento.

**Cómo se verifica.** Ocho pruebas, entre ellas las tres que sostienen las
reglas de arriba: que sin declaración la importación se rechaza y no crea
nada; que quien no evidencia el contacto no se crea *ni se le registra
alias*; y que quien evidencia el contacto pero no el uso semántico entra al
panel **y sus respuestas no llegan al store semántico** —esa última no se
conforma con el aviso: comprueba la consecuencia real—.

**Dónde vive.** `functions/panel_api/sav.py` (`normalizar_evidencia`,
`crear_individuos`), `functions/panel_api/ruteo.py`,
`web/public/js/paginas/encuestas.js`,
`functions/tests/test_fase3_operacion.py`,
`docs/DESPLIEGUE - Fase 3.md` §1.1.

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

<a id="d32"></a>
## D32 · La ingesta incorpora al panel y registra la participación

**El problema.** Convocatoria e ingesta estaban desacopladas: convocar creaba
participaciones, ingestar escribía respuestas mapeando por `alias_origen`, y
nada las unía. Dos agujeros, los dos silenciosos:

- Una persona dada de alta desde un `.sav` no era miembro de ningún panel.
  Existía en la bóveda, pero no entraba en `todo_el_panel` al convocar, no
  contaba para la composición ni para la brecha de cuota, y el muestreo no la
  veía. Invisible para todo lo que se hace con un panel.
- Alguien que respondió en campo sin haber sido convocado desde el sistema no
  tenía fila en `participacion`. La ola mostraba menos respuestas de las que
  realmente hubo.

**La decisión.** La ingesta hace las dos cosas, para los dos modos de R3.9 y
también para los archivos que no son `.sav`:

1. **Alta automática en el panel de la encuesta**, sin selector. Cada encuesta
   pertenece a exactamente un panel (`encuesta.panel_id` es FK obligatorio), así
   que no hay ambigüedad sobre cuál es: si alguien respondió esa encuesta,
   pertenece a ese panel.
2. **Registro de la participación** con `respondio = true`, creándola si no
   existe y actualizándola si la persona ya había sido convocada.

**Una baja no se revierte de costado.** `paneles.agregar_miembro` reactiva una
membresía en `baja`; esta vía **no**. Una baja fue una decisión explícita de
alguien y una ingesta no es el lugar para deshacerla. Se informa en el
resultado y decide un responsable.

**El gate de consentimiento, y por qué no es el mismo que en `convocar()`.**
Esta es la parte que hay que entender para no «arreglarla» después:

`convocar()` exige `contacto_participacion` porque emite una **invitación
futura**: no se puede contactar a quien no consintió ser contactado. La
participación que crea la ingesta es otra cosa —**registra un hecho ya
ocurrido**, la persona respondió en terreno—. Bloquear ese registro no protege
a nadie y sí distorsiona la tasa de respuesta de la ola. Así que la
participación importada no pasa por ese gate, y el gate sigue intacto donde
corresponde: un miembro sin consentimiento vigente no entra en ninguna
convocatoria futura por más participaciones que tenga registradas.

El gate de `uso_semantico` tampoco cambia, y acota el alcance de todo esto: a
quien no lo tenga vigente no se le ingesta nada, y por lo tanto tampoco se le
crea membresía ni participación.

**`participacion.origen`, y la trampa que evita.** La columna nueva
(`convocatoria` | `importacion`) no es documentación: sin ella, las
participaciones importadas se contarían como convocatorias en el motor de
fatiga, y **sacarían a esa gente del muestreo por contactos que nunca
ocurrieron**. La fatiga mide cuánto se molestó a alguien. Por eso:

| Métrica | Cuenta importadas | Por qué |
|---|---|---|
| Fatiga del muestreo (`recientes`, `totales`, `ultima_convocatoria`) | **No** | Mide contactos emitidos, y nadie contactó a esta persona |
| `convocatorias` y `ultimo_contacto` del tablero y de la ficha | **No** | Misma pregunta |
| `respuestas`, `respondidas` | Sí | Respondió |
| `ya_en_esta` (no re-convocar) | Sí | Ya tenemos sus respuestas |
| Denominador de la tasa de respuesta de la ola | Sí | Si no, la tasa pasaría de uno |

**Lo que hubo que arreglar para que funcionara.** `encuestas.ingestar` armaba
el mapa de ids con las participaciones de la ola y, si salía no vacío, la
ingesta ya no miraba `alias_origen`. Con un solo convocado con alias —o sea,
siempre— todo el que respondió sin haber sido convocado caía en `sin_mapear`.
El mapa ahora es la unión de los dos, con la participación mandando si
difieren.

**Consecuencias.** Un panel crece solo con cada ingesta, que es lo buscado
pero conviene saberlo: la composición se mueve sin que nadie agregue gente a
mano. El resultado de la ingesta informa las cinco cifras
(`membresias_nuevas`, `membresias_existentes`, `membresias_en_baja`,
`participaciones_nuevas`, `participaciones_actualizadas`) para que el
movimiento sea visible y no un efecto de costado.

**Dónde vive.** `db/boveda/0006_participacion_por_importacion.sql`,
`functions/panel_api/encuestas.py` (`incorporar_al_panel`,
`registrar_participacion_importada`), y los filtros por origen en
`muestreo.py`, `participacion.py` y `personas.py`.

---

<a id="d33"></a>
## D33 · Los demográficos del archivo van a la bóveda y no al store semántico

**El problema.** Si el `.sav` traía `SEXO`, `EDAD` o `LOCALIDAD`, se
precargaban como variables cualquiera y terminaban embebidas: *«Sexo →
Femenino»*, *«Localidad → Montevideo»*. Eso espeja los segmentadores al store
semántico **por la puerta de atrás**, que es exactamente lo que el diseño
descarta —quedan autoritativos en la bóveda para no sumar cuasi-identificadores
del lado que se quiere mantener limpio—. Y no se ganaba nada a cambio: el
filtro demográfico ya se resuelve en la bóveda (R2.4 y el puente de R2.5), así
que tenerlos también como vectores no mejora ninguna consulta.

**La decisión.** Cada variable se marca con qué es: pregunta del estudio, o
demográfica con su campo de la bóveda. Lo marcado como demográfico **no genera
`pregunta`, ni `respuesta`, ni embedding**; su valor va a la ficha del
panelista.

**El filtro corre en el backend, no en la pantalla.** Es lo que hace que sea
una regla y no una convención: una regla de privacidad que solo vive en el
navegador se saltea con una llamada a la API.

**Por qué la marca es del analista y no automática.** Una variable puede ser
segmentador en un estudio y objeto de análisis en otro: «¿en qué barrio vivís?»
es demográfico en un estudio de consumo y es *el* dato en uno sobre barrios.
Ninguna heurística resuelve eso; quien carga el estudio, sí. Por eso el sistema
**sugiere** —por nombre y por variable label— y nunca aplica solo.

**Tres cosas que se decidieron en el camino:**

| | |
|---|---|
| **Existe «demográfica sin campo»** | `EDAD` no tiene dónde ir: la bóveda guarda fecha de nacimiento y deriva el tramo. Sin esta opción, una columna de edad solo podría quedar como pregunta, que es lo que hay que evitar |
| **El valor se traduce antes de guardarlo** | Un `.sav` guarda `2` y «Femenino» por separado. Escribir el `2` en `persona.sexo` deja la composición por sexo llena de `1` y `2` y el muestreo por cuota inservible, **sin que nada falle**. El sexo se lleva a `F`/`M`/`X` con una tabla explícita: adivinar por la primera letra manda a todas las mujeres a «M» |
| **El archivo no pisa la ficha** | Un campo vacío se completa; uno ya cargado con otro valor se informa y se deja. El archivo de un estudio puede traer un dato viejo, mal tipeado o de otra persona, y una ingesta no es el lugar para cambiar la identidad de un panelista |

**El campo de códigos, de paso.** Estaba habilitado para todos los tipos,
incluso `abierta`, donde no hay códigos posibles: solo invitaba a cargar un
mapeo que nunca se iba a aplicar. Ahora sigue al tipo. **Las numéricas lo
conservan** a propósito: las variables numéricas de SPSS suelen traer value
labels solo para los valores especiales, y esa traducción importa —«¿Cuántos
años tenés? → 99» es ruido, «→ No contesta» es información—.

**Lo que quedó sin resolver, a propósito.** No hay override para embeber un
demográfico cuando *es* el objeto del estudio. Habilitarlo reintroduce el
espejado que el diseño descarta, así que si se decide, tiene que ser explícito,
advertido y registrado. Hoy la marca excluye sin excepción, y la salida es no
marcarla.

**Dónde vive.** `functions/panel_api/sav.py`
(`normalizar_demograficas`, `valor_demografico`, `SUGERENCIAS_DEMOGRAFICAS`),
`encuestas.py` (`completar_demograficos`), `personas.py`
(`completar_desde_archivo`) y la fila de variables de
`web/public/js/paginas/encuestas.js`.

---

<a id="d34"></a>
## D34 · El identificador viaja al campo en vez de adivinarlo a la vuelta

**El problema.** El mapeo de respuestas a personas se apoyaba siempre en
`alias_origen`: el id que la plataforma de campo le puso al respondente,
guardado al enrolar. Eso asume que **ese id es estable por persona entre
estudios**, y no se cumple: cada encuesta genera ids nuevos para el mismo
individuo. Al ingestar un estudio nuevo no matcheaba ninguna fila, todas
caían en `sin_mapear`, y la ingesta quedaba en cero **sin que nada estuviera
roto**. Es el peor tipo de falla: no hay excepción, no hay log, hay un número
en cero que alguien tiene que notar.

**La decisión.** Dar vuelta la dirección. El sistema tiene una ventaja que no
estaba usando —**la muestra sale de él**—, así que el identificador correcto
puede viajar *hacia* el campo en vez de intentar adivinarlo *a la vuelta*: se
exporta la muestra con `id_persona`, se precarga como variable oculta en el
instrumento, y vuelve en el archivo. Al ingestar se **declara qué tipo de
identificador** trae la columna, en vez de asumir siempre alias.

| Tipo | Contra qué resuelve | Cuándo |
|---|---|---|
| `id_persona` | `persona.id_persona`, directo | **Preferido**: se precargó la muestra |
| `alias` | `alias_origen` (origen + id) | **Default**, por compatibilidad: las cargas existentes siguen andando sin tocar nada |
| `documento` | `persona.documento` | Respaldo |
| `email` | `persona.email`, sin distinguir mayúsculas | Respaldo |

**Y de paso, el archivo de campo queda seudónimo.** La alternativa a
precargar era usar documento o email como llave, o sea meter PII en un export
que circula por la plataforma, por la computadora de quien lo baja y por
correo. La exportación con contacto existe —el equipo de campo a veces
necesita llamar— pero es una **reidentificación**: exige el permiso y queda
registrada, igual que R3.10.

**El alias se siembra solo.** Una carga por documento o email registra el
alias de esa plataforma, así que la segunda vuelta del mismo estudio ya no
depende de la llave natural. Es lo que hace que el respaldo no sea una
condena: se usa PII una vez y después se sale de ahí.

**Tres cosas que aparecieron al construirlo:**

| | |
|---|---|
| **Un uuid mal formado voltea la carga entera** | Postgres aborta la transacción completa ante un `invalid input syntax for type uuid`. Si el filtro por formato no corriera **antes** de consultar, una fila con un typo se llevaría puesta toda la ingesta. Hay prueba, y falla sin el filtro |
| **`formato_invalido` y `no_encontrado` son cosas distintas** | Un typo y una persona borrada por baja se arreglan distinto. Por eso `sin_mapear` pasó a traer el motivo por fila (R3.12.d), en vez de un número suelto |
| **El alias sembrado guarda el documento como `id_en_origen`** | Es lo que la plataforma usó para identificar al respondente, así que es lo correcto, y queda del lado de la bóveda —donde ese dato ya vive—. El guardrail de PII del store semántico no se toca |

**La pregunta que quedó sin decidir, y que conviene decidir.** Precargar el
`id_persona` significa que la plataforma de campo pasa a tener ese token. Es
opaco y no reidentifica por sí solo, pero **es la misma clave con la que está
indexado el store semántico**. La alternativa más conservadora es emitir un
**código por ola** y traducirlo en la ingesta: cuesta una tabla de mapeo más y
no cambia el flujo del equipo de campo. Se implementó `id_persona` directo
porque es lo que pide la spec en su cuerpo, pero la puerta al código por ola
sigue abierta y el cambio sería acotado —el tipo de identificador ya es un
parámetro declarado—.

**Lo otro que hay que verificar fuera del código:** que Dooblo y Alchemer
permitan precargar una variable oculta por respondente en el flujo que usa hoy
el equipo. Si no lo permiten, R3.12.a pierde sentido y el peso cae en los
respaldos —que por eso están, y por eso siembran el alias—.

**Dónde vive.** `functions/panel_api/ingesta.py`
(`resolver_identificadores`, los tipos y los motivos), `encuestas.py`
(`exportar_muestra`, `_resolver_identidades`, `_sembrar_alias`) y la ruta
`GET /encuestas/{id}/muestra` en `ruteo.py`.

---

<a id="d35"></a>
## D35 · Incorporar individuos y hacerlos panelistas son dos cosas distintas

**El problema.** La única forma de cargar individuos con sus respuestas era
desde una encuesta, y toda encuesta pertenece a un panel. Así, dar de alta
gente implicaba **necesariamente** meterla en un panel: aparecía en las
convocatorias, en la composición y en el muestreo. Eso bloquea un caso real y
frecuente —un ómnibus, un estudio de terceros, una base histórica— donde esa
gente no es panelista: no fue reclutada, no va a ser convocada, y meterla en
un panel distorsiona todos los indicadores de ese panel. Pero sus respuestas
sí interesa poder consultarlas por concepto.

**La decisión.** Separar las dos operaciones que venían pegadas:
**incorporar individuos y sus respuestas** por un lado, **hacerlos miembros de
un panel** por otro. Una **carga** hace lo primero y nada más.

**Una tabla nueva y no `encuesta.panel_id` nullable.** Fue la alternativa
obvia y se descartó. Una encuesta es, por definición, algo que se fieldea a un
panel: `convocar()`, la composición y el muestreo lo dan por sentado en todo
su código. Hacer el panel opcional obligaría a revisar cada uno de esos
caminos y dejaría un estado nuevo —la encuesta que no se puede fieldear— que
no significa nada para nadie. `carga` es una entidad aparte que mantiene esa
semántica intacta y **no toca ninguna línea de código existente**. Frente al
store semántico las dos son lo mismo: un `ref_estudio` que agrupa un
cuestionario. El store semántico no sabe ni le importa de cuál vino.

**La finalidad obligatoria se invierte, y es lo menos obvio del cambio.** En
la ingesta desde encuesta, una fila sin evidencia de `contacto_participacion`
no crea la persona: esa persona es un panelista al que se va a seguir
convocando, y sin base legal para contactarla el alta no tiene sentido. En una
carga sin panel el propósito es exactamente el inverso —gente que **no** se va
a contactar y cuyos datos se incorporan para análisis—, así que:

| | En una encuesta | En una carga |
|---|---|---|
| `contacto_participacion` | **Obligatoria** | Opcional |
| `uso_semantico` | Opcional | **Obligatoria** |

Exigir consentimiento de contacto en una carga sería pedir base legal para
algo que no se va a hacer, y no exigir el de uso semántico dejaría sin base lo
único que sí se va a hacer. Quien evidencia uso semántico y no contacto se
crea igual, y el gate de R1.3 —intacto— le impide ser convocado para siempre.
Por eso la finalidad obligatoria es **un parámetro** de
`sav.crear_individuos`, no una constante.

**El camino previsto es cargar → consultar → crear panel.** Un individuo sin
panel es consultable desde el primer momento, y si después se decide sumarlo,
se lo suma desde el resultado de una consulta (R3.11). Se incorpora solo a
quien corresponde, en vez de meter la base entera y depurar después. Es la
razón por la que este requisito no necesitó ninguna forma nueva de dar
membresías: R3.11 ya era el camino.

**Lo que este flujo deliberadamente no hace.** Después de escribir las
respuestas, `cargas.ingestar` **no** llama a `incorporar_al_panel` ni a
`registrar_participacion_importada`. Son las dos líneas que la ingesta desde
encuesta sí ejecuta (D32) y son exactamente lo que este flujo existe para no
hacer. Hay pruebas que fallan si alguna vuelve, y el resultado tampoco informa
esos contadores: mostrarlos en cero sugeriría que algo falló.

**Lo que sí se reutiliza, tal cual:** despivote, resolución de códigos a
etiquetas, composición del texto a embeber, embeddings en lote, upsert
idempotente, dedup de identidad (R1.2), tipo de identificador (R3.12.b),
marcado de demográficos (D33) y guardrail de PII (R1.6). Cambia el contexto,
no el pipeline. Del lado de la interfaz eso se traduce en **la misma pantalla**
—la de ingesta— parametrizada por destino, y no en una pantalla nueva que haya
que aprender.

**La pregunta abierta, y es legal.** ¿Alcanza el consentimiento de uso
semántico para conservar datos patronímicos —nombre, documento— de alguien que
no es panelista y no será contactado? Si la respuesta es que no, habría que
cargar estos individuos con demográficos pero sin patronímicos, o no
cargarlos. **Conviene definirlo antes de usar el flujo con bases reales**: el
sistema hoy permite las dos cosas, porque los patronímicos son opcionales, y
la decisión no es de software.

**Dónde vive.** `db/boveda/0007_carga_sin_panel.sql`,
`functions/panel_api/cargas.py`, el parámetro `finalidad_obligatoria` de
`sav.crear_individuos`, `personas.listar(sin_panel=…)`, las rutas `/cargas` de
`ruteo.py`, y del lado de la interfaz `web/public/js/paginas/panelistas.js`
(el botón y el filtro) con `web/public/js/paginas/encuestas.js` (la pantalla
parametrizada por destino).

---

<a id="d36"></a>
## D36 · Los segmentadores son un catálogo, no una lista en el código

**El problema.** La bóveda tenía tres segmentadores y punto: `persona.sexo`,
`persona.localidad` y el tramo derivado de `persona.fecha_nacimiento`. Eran
los únicos por los que se podía filtrar una consulta, fijar una cuota, ver una
brecha y equilibrar un muestreo. Nivel educativo, nivel socioeconómico,
ocupación, composición del hogar, tenencia de bienes —todo lo que una
consultora de mercado pregunta en cada estudio— no tenía dónde guardarse: o se
perdía, o terminaba embebido como una pregunta más del lado semántico, que es
justamente lo que el marcado de demográficas (D33) existe para evitar. Y
agregar uno costaba una migración, o sea un ciclo de desarrollo para algo que
es vocabulario, no software.

**La decisión.** Un **catálogo** que administra un admin desde la app. Define
el atributo y sus categorías, y de ahí en más ese atributo sirve exactamente
igual que sexo o localidad, en los cuatro lugares que dependen de
segmentadores.

**Acá sí canonizamos, y es la inversa de lo que hacemos con las respuestas.**
Es la misma distinción de diseño de todo el sistema: lo estructurado se
consulta con SQL exacto y necesita categorías estables; lo semántico se
interpreta en cada consulta (*schema-on-read*). Canonizar texto libre congela
errores en el dato —por eso las respuestas no se canonizan—, pero canonizar
segmentadores es lo que hace que un filtro devuelva siempre lo mismo y que la
aritmética de las cuotas cierre. Sin vocabulario cerrado aparecen veinte
variantes de «nivel educativo» escritas distinto y ninguna cuota cierra.

**La salvaguarda contra congelar un error es el valor crudo.** Cada valor
guarda además **lo que decía el archivo**. Si el mapeo salió mal, se corrige el
catálogo y se recalcula desde ahí, sin volver a pedir ni recargar el archivo
original (R3.14.h). Es lo que hace que canonizar sea reversible, y es la razón
por la que se pudo canonizar sin repetir el problema que hizo descartarlo para
las respuestas.

**Se unificó ahora, no después, y esa fue la parte cara.** `sexo`,
`localidad`, `tramo_etario` y `edad` pasaron al mismo catálogo, con las mismas
claves de siempre. Eso obligó a tocar código que ya funcionaba —consultas
demográficas, composición, muestreo, bonos, la ficha, el alta— a cambio de no
quedar con dos mecanismos en paralelo para siempre. El argumento decisivo fue
el muestreo: **todavía no existía**, así que construirlo contra el catálogo no
costó nada, mientras que construirlo contra las columnas fijas habría creado
la deuda en el momento mismo de nacer. Lo que separa una unificación limpia de
una que rompe en silencio es el test de no regresión sobre sexo y localidad, y
se escribió antes de migrar.

**Dos cosas que hicieron que la unificación no rompiera nada:**

| | |
|---|---|
| **`v_demografia` conserva nombre y columnas** | Se reescribió sobre el catálogo pero sigue devolviendo `id_persona, sexo, localidad, edad, tramo_etario`. Todo el código que la consulta —participación, exportaciones, ficha, bonos— siguió andando sin tocarse. Las 519 pruebas que ya existían pasaron sin cambios de comportamiento |
| **Un solo lugar resuelve el valor efectivo** | `v_atributo_persona`. Los filtros, la composición, las cuotas y el muestreo leen de ahí, y `v_demografia` también. No hay un camino para los segmentadores «de fábrica» y otro para los definidos por el usuario |

**«Sin dato» no es una categoría.** Antes, quien no tenía sexo cargado
aparecía como una categoría `(sin dato)` en la composición, con su proporción
calculada sobre el panel entero. Eso hacía dos cosas malas a la vez: mostraba
una categoría de cuota que nadie cargó, y su peso en el denominador bajaba la
proporción observada de todas las demás, con lo cual la brecha marcaba un
déficit que no existía. Ahora se informa aparte, y las proporciones se
calculan sobre quienes tienen el dato —que es la única base sobre la que suman
1—; el faltante en personas, en cambio, se sigue contando contra el panel
entero, porque el panel es del tamaño que es.

**La edad se envejece, no se congela.** Es habitual que una base traiga «34
años» y no la fecha de nacimiento. Guardarla como tramo lo congela: alguien
cargado como «25-34» en 2019 seguiría contando ahí hoy, y las cuotas se
calcularían sobre una edad que ya no es. La precedencia es fecha de nacimiento
→ edad declarada **con su fecha de referencia**, envejecida hasta hoy → tramo
cargado tal cual, que es el último recurso y el único que queda congelado. La
fecha de nacimiento gana siempre que exista, y cuando aparece después, el
tramo pasa a derivarse de ella sin recargar nada. La ficha dice cuál de los
tres casos es, porque los tres valen pero no valen lo mismo.

**Las categorías especiales tienen freno propio.** Un catálogo abierto permite
definir «religión» o «afiliación política» como si fueran un segmentador
cualquiera, y en investigación de mercado se preguntan seguido. Bajo la Ley
18.331 son categorías especiales con exigencias propias. Sin un control, el
sistema facilitaría almacenarlas sin que nadie lo note: por eso se declaran al
definirlas, se advierte ahí mismo que exigen consentimiento específico, se
listan aparte para que una revisión de cumplimiento las vea, y quedan fuera de
las exportaciones con datos. La lista de campos de esas exportaciones es
**fija** a propósito: que el catálogo crezca no puede hacer crecer solo lo que
sale del sistema en un archivo.

**El costo que sí se paga.** Con vocabulario cerrado, dar de alta a alguien de
una localidad que no está en el catálogo ya no se puede hacer tipeándola: hay
que agregarla primero. La migración siembra los diecinueve departamentos más
todo valor distinto que ya estuviera cargado, así que en la práctica el hueco
aparece poco; pero cuando aparece, el alta lo informa en el momento en vez de
guardar una variante nueva en silencio. Es deliberado: la alternativa es que
las cuotas geográficas no cierren nunca.

**Lo que quedó afuera a propósito.** Los datos de identidad y contacto no
entran al catálogo: no son segmentadores. Tampoco `fecha_nacimiento`, que es
dato de identidad y llave del dedup (R1.2); de ella se deriva el tramo, que sí
es un atributo. Y no se crean atributos al vuelo durante una carga: el
vocabulario lo define un admin, porque si cualquiera pudiera inventarlo al
cargar volveríamos exactamente al problema que esto resuelve.

**Dónde vive.** `db/boveda/0008_atributos_demograficos.sql` (las tres tablas,
la siembra, la migración de datos y las dos vistas),
`functions/panel_api/atributos.py`, y el catálogo enhebrado en
`demografia.py`, `composicion.py`, `muestreo.py`, `puntos.py`, `personas.py`,
`sav.py` y `encuestas.py`. Del lado de la interfaz, `web/public/js/catalogo.js`
y la solapa de atributos en `web/public/js/paginas/configuracion.js`.

---

<a id="d37"></a>
## D37 · Poder contactar y poder contactar por un canal son dos permisos

**El problema.** El sistema tenía un solo eje de permiso para contactar:
`contacto_participacion`. Eso responde *«¿puedo contactarla?»* y no dice nada
de *«¿por dónde?»*. Alguien pudo aceptar que lo llamen por teléfono y no
querer mensajes en su WhatsApp personal, y con un solo eje esa distinción no
existe.

Del lado de afuera el problema es más duro. La política de mensajería de
WhatsApp exige **opt-in previo** para los mensajes que inicia el negocio.
Mandar sin él no es una infracción abstracta: la gente bloquea, el *quality
rating* del número baja y Meta termina restringiendo la cuenta. El canal se
quema con el primer envío masivo a gente que no lo pidió, y no se recupera
fácil.

**La decisión.** `preferencia_canal`, un eje aparte. Para enviar por un canal
hacen falta **los dos**: consentimiento de finalidad vigente y preferencia de
ese canal activa. Falta cualquiera, no se envía.

**Los cuatro motivos de exclusión se informan por separado**, y eso no es
prolijidad: se arreglan distinto. A quien le falta el consentimiento hay que
pedírselo; a quien le falta la preferencia hay que ofrecerle el canal; a quien
le falta el celular hay que cargárselo; y a quien lo tiene inválido hay que
corregírselo. Un «no se pudo enviar a 340 personas» agrupado no le sirve a
nadie.

**El opt-in guarda con qué texto se obtuvo**, igual que el consentimiento. Sin
eso, «aceptó recibir WhatsApp» es una afirmación sin respaldo, y es
exactamente lo que hay que poder mostrar si Meta o la propia persona lo
reclaman.

**E.164 en los tres caminos de alta, y la normalización en un solo lugar.**
Un celular guardado como «099 123 456» no se puede usar para enviar y no es
comparable entre archivos. La conversión vive en `preferencias.normalizar_celular`
y la usan el alta manual, la ingesta y la landing: tres implementaciones
distintas del mismo formato es cómo se terminan teniendo tres formatos.

**Un celular que no se puede normalizar no voltea el alta.** Se guarda como
vino. El resto de los datos de esa persona sirven igual, y lo único que no va
a poder es activar WhatsApp —que es precisamente lo correcto—. Al revés, un
número mal tipeado impediría enrolar a alguien, y eso es peor que quedarse sin
un canal.

**Lo que no se hizo, y hay que saberlo.** No hay canal de entrada: si alguien
bloquea el número o responde «STOP», el sistema no se entera. La mitigación es
la revocación manual desde la ficha y la revisión periódica de los reportes de
Meta. Si el volumen crece, recibir webhooks deja de ser opcional.

**Dónde vive.** `db/boveda/0009_fase4_contacto.sql`,
`functions/panel_api/preferencias.py`, y la captura enhebrada en
`personas.py` (alta manual), `sav.py` (ingesta) e `inscripciones.py`
(landing).

---

<a id="d38"></a>
## D38 · La landing verifica el contacto antes de que exista la inscripción

**El problema.** La landing de Fase 3 aceptaba cualquier envío. Nadie
comprobaba que el correo o el celular fueran de quien se estaba inscribiendo,
y eso deja entrar dos cosas distintas y las dos malas: datos inventados, que
ensucian la cola de aprobación, y **datos ajenos**, que es inscribir a alguien
sin que se entere.

**La decisión.** Un código de un solo uso, y la inscripción **no existe** hasta
que se verifica. No es una casilla más del formulario: es una precondición de
escribir, igual que el consentimiento.

**Cuatro cosas que valen la pena:**

| | |
|---|---|
| **El código se guarda hasheado** | Un código en claro en la base es una credencial de un solo uso al alcance de cualquiera que lea la tabla, y alcanza para inscribir a nombre de otro |
| **La IP también** | Para limitar la tasa por origen alcanza con saber que dos pedidos vinieron del mismo lado; de cuál no hace falta, y guardarlo sería juntar un dato personal más en una superficie pública |
| **Los intentos se agotan** | Sin eso, seis dígitos se adivinan por fuerza bruta en minutos |
| **Hay dos límites de tasa, no uno** | Por origen, que frena el uso de la landing como oráculo; y **por destino**, que es el que protege a la persona del otro lado: sin él, la landing sirve para bombardear a un número ajeno |

**El desafío anti-automatización va antes de emitir el código, no antes de
inscribir.** El envío de códigos es lo que cuesta plata y lo que puede
molestar a un tercero, así que es lo que hay que proteger. Dejarlo para el
final protegería la tabla y no el bolsillo ni al vecino.

**Sin proveedor configurado, el sistema no finge.** El código vuelve en la
respuesta y lo dice con todas las letras; el desafío no bloquea y el
diagnóstico de Cumplimiento lo informa. Una landing sin verificación real es
una decisión que alguien tiene que tomar a sabiendas, no un olvido silencioso.
Es el mismo patrón de degradación visible del reranker y de la verificación
con Claude.

**El dedup se refuerza acá y no en el alta.** La landing es el único camino
donde la persona se inscribe sola, sin que nadie del equipo controle qué
escribe: es donde más probable es que la misma persona se anote dos veces, con
el correo del trabajo una vez y el personal la otra. Por eso quien aprueba ve
los candidatos parecidos —por documento, correo, celular o nombre y fecha— y
por eso **se proponen y no se fusionan**: una coincidencia de correo o de
nombre puede ser un homónimo, y fusionar a dos personas distintas no se
deshace. La única que se resuelve sola sigue siendo el documento exacto, que
es R1.2 sin cambios.

**Dónde vive.** `functions/panel_api/verificacion_contacto.py`,
`functions/panel_api/desafio.py`, `inscripciones.candidatos_parecidos`, y del
lado público `web/public/inscribirse.html`.

---

<a id="d39"></a>
## D39 · Guardar un solo valor vigente por atributo era un número mal calculado

**El problema.** `persona_atributo` guardaba un valor por persona y atributo, y
al cambiarlo lo pisaba. La lectura fácil es que faltaba una feature
longitudinal. La lectura correcta es peor: **el sistema ya mostraba números
incorrectos y no avisaba**. Recalcular la composición de una ola de hace un año
la calculaba con la demografía de hoy. Una cuota que cerró con 30 % de menores
de 35 puede mostrar 22 % un año después sin que nadie se haya ido del panel,
solo porque esa gente cumplió años. El dato no estaba incompleto: estaba mal, y
se veía bien.

Por eso R4.1.a va primero en el bloque, antes que las series y que el
optimizador. No es la parte más vistosa; es la que arregla algo roto.

**La decisión.** Cada valor vale en un intervalo semiabierto `[desde, hasta)`.
El vigente es el de `hasta is null`. Cambiar un valor no lo actualiza: le cierra
la vigencia e inserta uno nuevo.

**Tres cosas de la implementación que no son obvias:**

**El primer valor vale desde `-infinity`.** Sabemos cuándo un valor *cambió*;
no sabemos cuándo el primero *empezó*. Si al primero le pusiéramos la fecha de
carga, toda composición anterior a esta migración devolvería cero personas con
dato, y la corrección habría quedado peor que el problema. Es una suposición
declarada, no un dato, y está escrita como tal en la migración. No se usa
`persona.creado_en` en su lugar porque una persona puede ingresarse hoy desde
un archivo de campo de hace dos años: su fecha de alta no acota hacia atrás la
validez de sus atributos.

**Dos cambios en la misma transacción se colapsan.** Una carga que corrige un
valor que acaba de escribir produciría, si se registrara como un cambio, un
intervalo que afirma que ese valor rigió *desde siempre hasta ahora*, cuando
nunca existió fuera de la transacción. Una transacción es atómica: desde
afuera, el único valor que existió es el último. Ese caso pisa en vez de
cerrar, y el historial no registra un cambio que nadie pudo ver.

**Una sola implementación de la resolución.** La precedencia de R3.14.g —fecha
de nacimiento > edad declarada envejecida > tramo cargado— vivía en
`v_atributo_persona`. Duplicarla en una versión «a fecha» garantizaba que las
dos se separaran con el tiempo. En vez de eso pasó a
`f_atributo_persona(momento)` y la vista quedó como esa función en `now()`.
Todo el código que consultaba la vista siguió andando sin cambios, y de yapa
los derivados se calculan a la fecha pedida: la edad de una persona en una ola
de 2024 es la que tenía en 2024, que era el otro lado del mismo error.

**Lo que no se historiza: el objetivo de composición.** `objetivo_composicion`
guarda el universo de referencia vigente y cargar uno nuevo pisa al anterior.
Así que la brecha de una composición retroactiva compara la foto de entonces
contra el universo de hoy. No se resolvió —historizar el universo es otro
requisito— pero la respuesta lo dice con todas las letras, que es la diferencia
entre un número que se entiende y uno que engaña.

**Dónde vive.** `db/boveda/0010_fase4_historial_atributos.sql`,
`functions/panel_api/atributos.py`, `functions/panel_api/composicion.py`,
`functions/panel_api/demografia.py`.

---

<a id="d40"></a>
## D40 · La comparabilidad entre olas la declara el analista, no el sistema

**El problema.** Comparar «la misma pregunta» entre dos olas es difícil porque
cada cuestionario la redacta distinto: «¿Qué bebida consume habitualmente?» en
una y «¿Cuál es hoy su bebida de consumo habitual?» en la siguiente, con
opciones que tampoco coinciden. Un sistema que quisiera resolverlo solo tendría
que canonizar respuestas, que es justamente lo que este diseño descarta desde
la Fase 1.

**La decisión.** Una `serie` agrupa preguntas de distintas olas y mapea sus
opciones a un vocabulario común. La declara el analista. El sistema **sugiere**
candidatas por similitud semántica y no agrega ninguna sola.

**Por qué declarado y no automático.** Es la misma distinción de siempre, con
una vuelta de tuerca. Canonizar automáticamente congelaría una equivalencia que
puede ser falsa —dos preguntas parecidas que miden cosas distintas— y lo haría
*en el dato*, donde ya no se ve. Declararla la hace explícita, revisable y
responsabilidad de quien sabe qué se preguntó y para qué. Las que entran por
sugerencia quedan marcadas como tales: si más adelante una serie resulta mal
armada, saber cuáles entraron así dice si el problema fue el criterio o la
herramienta.

**Hubo que embeber algo nuevo.** Hasta acá solo se embebían las *respuestas*
(`pregunta → respuesta`), que sirve para buscar qué contestó la gente pero no
para preguntarse qué preguntas se parecen entre sí: dos olas pueden preguntar
lo mismo y recibir respuestas opuestas. Así que `pregunta.embedding_texto`, que
se llena en forma perezosa la primera vez que se piden sugerencias.

**Una opción sin mapear no se cuenta.** Es la misma regla que «(sin dato)» en
la composición: meterla en una categoría real haría que el movimiento entre
olas mienta.

**Dónde vive la serie, y dónde no.** La serie vive en el **store semántico**,
porque agrupar preguntas y mapear opciones es contenido. Quién la creó o la
editó, **no**: eso es una persona, y la regla dura del sistema es que ninguna
persona se escribe de ese lado. Así que el rastro va a `serie_auditoria`, en la
bóveda, junto a los otros dos rastros de acciones de personal. Es una tabla más
y una escritura cruzada, y vale la pena: la alternativa era una columna con un
correo del lado equivocado.

**Dónde vive.** `db/semantica/0004_series.sql`,
`functions/panel_api/series.py`, `functions/panel_api/longitudinal.py`,
`web/public/js/paginas/longitudinal.js`.

---

<a id="d41"></a>
## D41 · El optimizador propone y explica; no es un solver

**El problema.** Las reglas de R3.1 priorizan brechas y excluyen
sobre-convocados, y eso alcanza cuando hay holgura. Cuando no la hay, cerrar la
cuota y cuidar a la gente tiran para lados opuestos y las reglas no saben
negociar: eligen mal o no eligen.

**La decisión: un voraz, no programación entera.** La spec pide que cada
individuo incluido sea *explicable*: por qué segmento entró y qué peso tuvo. Un
óptimo de programación entera da una asignación mejor en el margen y **ninguna
explicación por persona**, además de una dependencia nueva. Con una función
objetivo convexa y una sola dimensión de cuota, el voraz llega al mismo lugar
en la enorme mayoría de los casos, y cada elegido sale con el déficit que tenía
su segmento cuando entró, lo que aportó a la brecha y lo que costó en fatiga y
en equidad. Una selección que nadie puede defender ante un investigador no
sirve para decidir.

El aporte usa el error cuadrático sobre el conteo del segmento: la derivada de
`(s − objetivo)²` al sumar uno es `2·déficit − 1`, fuertemente positiva
mientras falte gente y negativa apenas la categoría se pasa. Eso da el
comportamiento que se quiere sin ningún caso especial.

**Duro y blando son cosas distintas.** Consentimiento, preferencia de canal,
pertenencia al panel y tamaño pedido son restricciones **duras**: no se compran
con ningún peso. Fatiga y equidad de rotación son **blandas**: se penalizan.
Meterlas en la misma bolsa sería o convocar a quien no consintió, o no poder
cerrar nunca una cuota. Y la equidad no es lo mismo que la fatiga: alguien
puede estar lejos del umbral de la ventana y aun así ser siempre el elegido de
su celda, porque su celda tiene poca gente.

**Cuando no se puede, lo dice con números y no elige.** Una cuota infactible no
se cierra violando una restricción ni devolviendo menos gente en silencio: se
informan las tres salidas con su costo medido —reducir el tamaño, aflojar la
fatiga, aceptar la brecha— y decide una persona. La alternativa de aflojar la
fatiga se cuantifica **corriendo el mismo motor con el tope movido**, que es la
única forma honesta de decir cuántos más entran; y si no entra nadie, se dice
una vez que el cuello de botella no es la fatiga en vez de repetir pasos que
dicen «0 personas más».

**Las reglas de R3.1 se mantienen, no se reemplazan.** Con holgura los dos
métodos coinciden; la diferencia aparece justo donde duele. `comparar()` corre
los dos sobre la misma pregunta y muestra la diferencia, que es lo que permite
justificar el cambio de método ante quien pregunte. Las reglas quedan además
como respaldo.

**Los pesos son por panel y no globales.** La tensión es distinta en cada uno:
un panel chico y muy usado necesita pesar la fatiga mucho más que uno grande.
Un peso negativo se rechaza: invertiría el sentido de la restricción y premiaría
convocar a los más convocados.

**Dónde vive.** `functions/panel_api/optimizador.py`,
`db/boveda/0011_fase4_inteligencia.sql`,
`web/public/js/paginas/muestreo.js`.

---

<a id="d42"></a>
## D42 · De WhatsApp se elige la plantilla, y nada más

**El problema.** La pantalla pedía tres datos para configurar una encuesta
como Flow: el id del Flow, el nombre de la plantilla y el idioma. Los tres son
**el mismo dato**. Una plantilla de Meta se identifica por el par
`(nombre, idioma)` y lleva el `flow_id` adentro, en su botón de Flow.

Pedirlos por separado tenía dos consecuencias, y la segunda es la grave:

1. Había que ir a Meta, buscar el id del Flow y copiarlo a mano. Un número de
   diez dígitos transcrito entre dos pantallas.
2. **Nada impedía que se contradijeran.** Se podía configurar la plantilla `X`
   con el `flow_id` de la `Y`, o la plantilla en español con el idioma `pt_BR`.
   El sistema lo guardaba sin chistar y eso recién se descubría al validar
   contra Meta —o, si nadie validaba, al enviar.

Un formulario que permite escribir una combinación imposible no es un
formulario incompleto: es uno que delega en el usuario una verificación que la
fuente de verdad ya podía hacer.

**La decisión.** Se lista `GET /{waba_id}/message_templates`, se filtran las
aprobadas **que tienen un botón de Flow**, y se elige una. El idioma y el
`flow_id` salen de ella.

**Las que no tienen botón de Flow no se ofrecen.** Están aprobadas y no sirven
para convocar a un cuestionario: mandan un mensaje y nada más. Ofrecerlas
sería ofrecer un callejón sin salida, así que se filtran y se dice cuántas
había, para que «la lista está vacía» tenga una explicación.

**Una plantilla en dos idiomas no se elige sola.** El mismo nombre en `es` y
en `pt_BR` son dos plantillas distintas, se aprueban por separado y pueden
estar en estados distintos. Cuando la configuración guardada no alcanza para
desambiguar, el sistema lo informa con los idiomas disponibles en vez de tomar
la primera: elegir por su cuenta sería elegir en qué idioma se le habla a la
gente.

**El `flow_id` se sigue guardando, pero ya no se escribe.** Se resuelve de la
plantilla y se persiste por dos motivos: queda registrado qué Flow usó esa ola
aunque la plantilla cambie después, y el envío no depende de que Meta conteste
para saber qué se configuró. El envío en sí **no lo usa**: el mensaje
referencia la plantilla y el Flow viene adentro de su botón, que es la razón
de fondo por la que configurarlo aparte nunca tuvo sentido.

**Lo que esto vuelve obligatorio.** `WHATSAPP_WABA_ID` pasa de «hace falta
para validar» a «hace falta para configurar»: sin él no hay lista y la
encuesta no se puede marcar como Flow. El token necesita además el permiso
`whatsapp_business_management`, no solo `whatsapp_business_messaging`.

> **Por qué esto no cambia el alcance.** El sistema **sigue solo enviando**.
> No crea Flows ni plantillas —se siguen armando en Meta— ni recibe
> respuestas. Lo único que cambió es que, en vez de pedir que le transcriban
> lo que Meta ya sabe, lo va a buscar.

**Dónde vive.** `whatsapp.listar_plantillas`, `whatsapp.candidatas`,
`encuestas.configurar_flow`, `web/public/js/paginas/encuestas.js`.

---

## Anexo · Decisiones que no se tomaron

Cosas que quedaron abiertas a propósito, para que no se confundan con olvidos:

| Tema | Estado | Dónde está anotado |
|---|---|---|
| `id_persona` directo al campo, o código por ola | Sin decidir: se implementó el directo, que es lo que pide la spec. El código por ola es defensa en profundidad y el cambio sería acotado | [D34](#d34) |
| Que Dooblo y Alchemer permitan precargar una variable oculta | **A verificar fuera del código.** Si no se puede, el peso cae en los respaldos por documento o correo | [D34](#d34) |
| Si el consentimiento de uso semántico alcanza para conservar patronímicos de un no-panelista | **Abierta, y es legal.** El sistema permite cargar con o sin patronímicos; hay que definirlo antes de usar R3.13 con bases reales | [D35](#d35) |
| Qué hacer con las personas sin panel acumuladas | Sin política: nadie las revisa ni las depura por ahora | [D35](#d35) |
| Si se van a definir atributos que sean categorías especiales | **Abierta, y es legal.** El sistema los permite y los advierte; el consentimiento actual probablemente no alcance para tratarlos | [D36](#d36) |
| Quién es dueño del vocabulario de segmentadores | Sin definir. Sin un responsable, el catálogo se llena de atributos parecidos | [D36](#d36) |
| Versionar el conjunto de categorías de un atributo | No se hizo: alcanza con no permitir cambiar claves. Cambiar categorías usadas en cuotas históricas rompe la comparabilidad entre olas, y eso queda como riesgo anotado | [D36](#d36) |
| Historial de un atributo que cambia con el tiempo (ocupación, ingresos) | **Resuelto en R4.1.a:** cada valor vale en un intervalo y el anterior se conserva | [D39](#d39) |
| Eliminar `persona.sexo` y `persona.localidad` | Pendiente de una migración posterior: quedaron obsoletas, no se escriben ni se leen, y se borran una vez verificado que nada las usa | [D36](#d36) |
| Crear Flows o plantillas desde el sistema | **No se hace, y no cambió.** Se arman en Meta; el sistema los lista y elige, nunca los crea | [D42](#d42) |
| Recibir webhooks de WhatsApp | **No se hizo.** Sin canal de entrada, un bloqueo o un «STOP» no llega al sistema. Mitigación: revocación manual y revisión de los reportes de Meta. Si el volumen crece, deja de ser opcional | [D37](#d37) |
| Opt-in de WhatsApp de los panelistas ya enrolados | **Pendiente, y es legal.** Nadie se lo pidió: hay que obtenerlo antes de poder mandarles | [D37](#d37) |
| Transferencia de celulares a Meta | **A revisar antes del primer envío real.** Es compartir datos personales con un tercero fuera del país | [D37](#d37) |
| Embeber un demográfico cuando es el objeto del estudio | Sin override: la marca excluye sin excepción. Habilitarlo reintroduce el espejado que el diseño descarta | [D33](#d33) |
| Base legal del alta por SAV | **Resuelta:** la evidencia viaja en el archivo y declararla es obligatorio. Lo que queda es de campo: que el cuestionario incluya la pregunta | [D25](#d25) |
| Texto de consentimiento de la landing | Pendiente del DPO; el sistema lo trata como dato | [D24](#d24) |
| Tratamiento fiscal del canje | No-goal explícito de la Fase 3 | `SPEC_fase3.md` §3 |
| Calibración de todos los umbrales | Pendiente, contra datos de Equipos | [D31](#d31) |
| Optimización de muestreo con restricciones | **Resuelta en R4.2:** un voraz explicable, con las reglas de R3.1 como referencia y respaldo | [D41](#d41) |
| Endurecimiento de la landing (verificación de contacto, anti-fraude) | Fase 4 (R4.4) | `docs/DESPLIEGUE - Fase 3.md` §5.4 |
| Análisis longitudinal | **Resuelto en R4.1:** historial de atributos, series declaradas y vista longitudinal | [D39](#d39), [D40](#d40) |
| Historizar el universo de referencia (`objetivo_composicion`) | **No se hizo.** Una composición retroactiva compara la foto de entonces contra el universo de hoy; la respuesta lo avisa | [D39](#d39) |
| Quién es dueño del vocabulario de series | Sin definir, y es el mismo riesgo que con los segmentadores: sin un responsable, cada equipo arma las suyas y las comparaciones dejan de ser comparables entre equipos | [D40](#d40) |
| Un solver de programación entera para el muestreo | **Descartado a propósito:** da una asignación mejor en el margen y ninguna explicación por persona | [D41](#d41) |
| Calibrar los pesos del optimizador contra datos de Equipos | Pendiente, igual que los umbrales de fatiga. Los defaults están documentados pero no medidos | [D41](#d41) |
| Alinear el voseo de la interfaz con el registro formal del manual | Sin decidir; requeriría recapturar las 44 pantallas | PR de la Fase 2 |
