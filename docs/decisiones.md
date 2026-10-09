# Decisiones de diseño

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Alcance:** Fases 1 a 8, y la superficie externa de COLOQUIO (incluida su batería de verificación)
**Última actualización:** 2026-10-05 (R-MAIL y verificación por lotes)

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
| [D43](#d43) | El gate de consentimiento es una vista; la fatiga son hechos | 5 |
| [D44](#d44) | El contacto se sirve por función, no por vista | 5 |
| [D45](#d45) | Las finalidades son un catálogo, no un `check` | 5 |
| [D46](#d46) | Las vistas del contrato no llevan `security_invoker` | 5 |
| [D47](#d47) | El rol del consumidor es IAM, no un usuario de Cloud SQL | 5 |
| [D48](#d48) | La baja avisa a todos y no espera a ninguno | 5 |
| [D49](#d49) | La regla dura #1 la hace valer la base, no un chequeo que hay que correr | 5 |
| [D50](#d50) | La convocatoria activa se verifica por sistema, y el consumidor declara la suya | 5 |
| [D51](#d51) | El mapeo a categorías se declara valor por valor, y lo que no se mapea se guarda igual | 3 |
| [D52](#d52) | El portal es una superficie separada, aunque comparta el proveedor de identidad | 6 |
| [D53](#d53) | El login del portal pasa por el backend, no por el SDK del navegador | 6 |
| [D54](#d54) | Los embeddings van en 512 dimensiones, y la dimensión es un contrato con la base | — |
| [D55](#d55) | La ingesta deja de vivir en una request, y el estado vive en la base | — |
| [D56](#d56) | Lo que decidió una carga se guarda con la carga | — |
| [D57](#d57) | Lo que se revisa es lo que se ejecuta, y lo que se cruza se registra | 7 |
| [D58](#d58) | Una fila por persona: el gate se pregunta con `exists`, y la cardinalidad se verifica | 5 (bug) |
| [D59](#d59) | La ficha explica el resultado que se está mirando, y una ruta sin pantalla no existe | 7 |
| [D60](#d60) | El texto que se embebe: el sistema propone, el analista confirma | 8 |
| [D61](#d61) | Un estudio se corrige desde lo que quedó cargado, no desde el archivo | 8 |
| [D62](#d62) | Un chequeo que no puede probar se omite, no falla; y la auditoría se prueba con el actor del contrato | 5 |
| [D63](#d63) | El paso de revisión advierte; solo frena lo que una decisión no puede suplir | 8 (bug) |
| [D64](#d64) | El celular es clave de dedup, pero más cauta que el correo | 1 (dedup) |
| [D65](#d65) | El correo sale por Workspace, y sin proveedor no hay atajo | 6 (portal), 4 (landing) |
| [D66](#d66) | La verificación va por lotes, y lo que no se juzgó queda «sin verificar» | 2 (consultas) |

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

<a id="d43"></a>
## D43 · El gate de consentimiento es una vista; la fatiga son hechos

**El problema.** `paneles` fue construido con el supuesto de que hay **un
solo** programa hablándole a la bóveda. Bajo ese supuesto era razonable que el
gate de consentimiento fuera `consentimiento.exigir()`, en Python. Con un
segundo consumidor eso pasa a ser una promesa repetida en dos bases de código,
y la primera que se rompa lo va a hacer en silencio: una persona sin
consentimiento vigente convocada a un grupo, o una baja que deja viva una
grabación porque la cascada no sabe que el otro sistema existe.

La reacción obvia —«que COLOQUIO llame a una API de `paneles`»— resuelve el
gate y crea una dependencia de disponibilidad entre dos sistemas que no la
tenían. La bóveda ya es un punto de encuentro; el control tiene que estar
donde están los datos.

**La decisión.** Las invariantes de cumplimiento se mueven a la base. Pero no
todas las reglas son iguales, y tratarlas igual habría sido el error:

- El **consentimiento vigente** es un invariante **legal**. No es negociable,
  no admite parámetros y no puede quedar del lado del consumidor. Va en
  `v_persona_convocable`, una vista de la que es imposible salirse: quien no
  aparece ahí, no existe para el consumidor.
- La **fatiga** es política de **negocio**. Sus umbrales son por panel, su
  ventana es un parámetro, y el cálculo actual excluye la encuesta en curso
  —que es contexto del llamador y no se puede expresar en una vista sin
  parámetros—. Además el cualitativo tiene su propia noción de fatiga: ocho
  personas en un grupo no se cansan como mil en una encuesta. Así que la
  fatiga se expone como **hechos** en `v_fatiga_panelista` (cuántas
  convocatorias, cuándo la última, cuántas respondió) y cada consumidor aplica
  su umbral.

**La consecuencia, escrita y no descubierta después: la política de fatiga no
queda hecha valer en la base.** Es deliberado, y es la diferencia entre las dos
reglas: el consentimiento no se negocia; la fatiga admite criterios distintos
por consumidor. Si mañana la fatiga tuviera que ser obligatoria, el camino no
es agregarle parámetros a una vista: es otra función, como la del contacto.

**Dónde vive.** `db/boveda/0014_fase5_superficie_externa.sql` §3,
`functions/panel_api/consentimiento.py`.

---

<a id="d44"></a>
## D44 · El contacto se sirve por función, no por vista

**El problema.** Convocar exige leer un canal de contacto, que es PII, y esa
lectura tiene que quedar auditada. Una vista no puede auditar: no tiene
efectos. Y un `grant select` sobre `persona` limitado a dos columnas dejaría
al consumidor barriendo la agenda entera a voluntad, sin motivo y sin rastro.

**La decisión.** `contacto_para_convocatoria(id_persona, canal, motivo,
actor)` devuelve **un** canal de **una** persona, y en la misma transacción
escribe la fila de `reidentificacion`. Si la auditoría no se puede escribir, el
dato no se entrega: no son dos operaciones que casi siempre pasan juntas, es
una sola.

Tres detalles que parecen menores y no lo son:

**El gate se vuelve a aplicar adentro.** No se confía en que el llamador haya
consultado la vista antes. Consultarla y llamar son dos momentos distintos, y
entre los dos el consentimiento se puede haber retirado.

**Hay que tener a quién convocar.** La función exige una convocatoria activa
—una `participacion` en una encuesta no cerrada—. Sin eso, un consumidor podría
reconstruir la agenda completa de a una persona por vez, con el gate intacto y
todo perfectamente auditado. El gate dice *a quién se puede contactar*; esto
dice *por qué ahora*.

**Un canal vacío es información, no un error.** Que la persona no tenga celular
cargado se devuelve como vacío y se audita igual: el intento existió. Un
rechazo del gate, en cambio, **no** deja fila: no se entregó ningún dato, y una
auditoría de reidentificaciones que incluya lecturas que no ocurrieron deja de
servir para contar.

**Dónde vive.** `db/boveda/0014_fase5_superficie_externa.sql` §4.

---

<a id="d45"></a>
## D45 · Las finalidades son un catálogo, no un `check`

**El problema.** `consentimiento.finalidad` y `texto_consentimiento.finalidad`
tenían cada una un `check` con las dos finalidades existentes. El cualitativo
suma cuatro —`grabacion_av`, `moderacion_automatizada`, `uso_semantico_cuali`,
`difusion_verbatim`— y con un `check` eso es un `alter table` por finalidad, en
dos tablas, cada vez.

Pero el motivo de fondo no es la comodidad. Un `check` puede decir *qué valores
se aceptan* y nada más. No puede decir que `grabacion_av` se consiente **por
estudio** y `uso_semantico_cuali` **por persona**; ni que una finalidad exige
un texto publicado y otra no; ni en qué orden se muestran en un formulario.
Esos son atributos de la finalidad, y un `check` no tiene dónde guardarlos.

**La decisión.** `finalidad_consentimiento` es una tabla de catálogo con
`ambito`, `requiere_texto`, `activa` y `orden`, y las dos columnas pasan a ser
FK. Los dos `check` se retiran.

De ahí salen tres reglas que antes no existían y que ahora hace valer la base:

1. **Una finalidad de ámbito `estudio` exige `ref_estudio`, y una de ámbito
   `persona` lo prohíbe.** Consentir que te graben «en general» no es
   consentimiento; consentir que te graben en *este* grupo, sí. Y al revés:
   un `uso_semantico_cuali` atado a un estudio se retiraría por estudio, que
   no es lo que significa.
2. **No se otorga una finalidad sin una versión activa de su texto.** Es lo
   que hace demostrable al consentimiento: sin el texto publicado, «consintió
   la versión 2026-01» no es verificable por nadie.
3. **El retiro nunca se bloquea.** La regla anterior vale solo al **insertar**.
   Si el texto se desactivó, retirar el consentimiento tiene que seguir siendo
   posible: una regla de cumplimiento que impida cumplir está mal escrita.

**Lo que esto rompe, a propósito.** Si hay consentimientos vigentes que apuntan
a versiones que nunca se publicaron, la migración **se niega a aplicar** y los
nombra. Es el hallazgo, no el obstáculo: aplicarla igual dejaría el alta
fallando en producción por un motivo que nadie relacionaría con la migración.

**Dónde vive.** `db/boveda/0012_fase5_catalogo_finalidades.sql`,
`db/boveda/0013_fase5_finalidades_cualitativo.sql`.

---

<a id="d46"></a>
## D46 · Las vistas del contrato no llevan `security_invoker`

**El problema.** Desde PG15 una vista puede declararse `security_invoker`, y
entonces los permisos sobre las tablas de abajo se chequean contra quien
consulta. Es la opción que más suena a «lo correcto»: privilegio mínimo,
nada de heredar los del dueño.

Acá sería exactamente al revés. Si `v_persona_convocable` fuera
`security_invoker`, para leerla COLOQUIO necesitaría `select` sobre `persona`
y sobre `consentimiento` — es decir, sobre **todas** las filas, con gate o sin
él. La vista dejaría de ser una puerta y pasaría a ser una sugerencia.

**La decisión.** Las vistas del contrato se quedan con el comportamiento por
defecto: corren con los privilegios de su dueño. Es ese comportamiento el que
convierte a la vista en la única puerta, y por eso PostgreSQL 16 no es un
detalle del stack sino un supuesto del diseño.

**Lo que costó descubrir, y vale escribir.** Una vista le presta al consumidor
sus privilegios **sobre tablas**, no su permiso para **ejecutar funciones**: el
`execute` se chequea siempre contra quien invoca, aun cuando la llamada venga
de adentro de una vista. Como `v_demografia` cuelga de `f_atributo_persona()`
—la única resolución de la precedencia de atributos, [D39](#d39)—, la primera
versión de la vista era ilegible para COLOQUIO.

Las dos salidas fáciles eran malas: otorgar `execute` sobre
`f_atributo_persona()` habría expuesto la demografía de **cualquiera**, con
gate o sin él; y reescribir la resolución adentro de la vista habría creado la
segunda implementación que [D39](#d39) existe para evitar. La salida buena fue
`f_persona_convocable()`: un `security definer` con el gate adentro, del que la
vista es una fachada delgada. Otorgar su `execute` no abre nada porque no hay
nada que ver fuera del gate.

**Y algo que Postgres regala y hay que devolver:** una función nace con
`execute` otorgado a `public`. «No le otorgamos nada» y «no puede ejecutarla»
son dos afirmaciones distintas, y sin revocarlo explícitamente la lista blanca
de privilegios no describe nada.

**Dónde vive.** `db/boveda/0014_fase5_superficie_externa.sql` §3 y §6,
`scripts/verificar_coloquio.py`.

---

<a id="d47"></a>
## D47 · El rol del consumidor es IAM, no un usuario de Cloud SQL

**El problema.** En Cloud SQL, **todo usuario creado con `gcloud sql users
create`, la consola o la API recibe automáticamente `cloudsqlsuperuser`**, y
con él `CREATEROLE`. Así se creó `app_paneles`. Si `coloquio_app` se creara
igual, podría otorgarse a sí mismo cualquier privilegio que se le revoque, y
todo el control de acceso de esta fase sería decoración.

**La decisión.** `coloquio_app` se crea como **usuario IAM de cuenta de
servicio**, que no recibe ningún rol de base automáticamente. Tres cosas se
cobran de una vez: privilegio realmente mínimo sin herencias que revocar; no
hay contraseña, así que desaparece un DSN con clave en Secret Manager; y el
origen deja de ser declarable, porque la identidad de la conexión **es** la
cuenta de servicio.

`app_paneles` no se toca: migrarlo sería riesgo innecesario en esta fase.

**Y la red de seguridad, que es lo que hace que esto no dependa de la memoria
de nadie sobre cómo se comporta Cloud SQL:** `scripts/verificar_coloquio.py`
enumera los privilegios **efectivos** del rol —lo que puede hacer hoy, no los
`grant` que alguien escribió— contra una lista blanca que vive en el repo, y
falla si sobra uno. Si un default de la plataforma cambia, o si el usuario se
crea por el camino equivocado, la build rompe.

**Lo que esa verificación encontró, y que leer la migración no habría
mostrado.** Tres defectos, los tres de seguridad, aparecieron al conectarse de
verdad con el rol del consumidor: la vista que el consumidor no podía usar
([D46](#d46)); el `execute` regalado a `public`; y —el peor— que un rol **sin
registrar** conseguía un contacto y quedaba auditado como `paneles`, porque
`sistema_de_la_conexion()` caía en un valor por omisión y porque adentro de un
`security definer` `current_user` es el dueño de la función, no quien llama.
Era lavado de origen, no una fuga menor. Por eso todo lo que deriva identidad
usa `session_user`, y por eso la función es estricta: un rol que no está en
`sistema_consumidor` no obtiene nada.

**Dónde vive.** `docs/DESPLIEGUE - COLOQUIO Fase 0.md` §3,
`scripts/verificar_coloquio.py`, `functions/tests/test_fase5_superficie.py`.

---

<a id="d48"></a>
## D48 · La baja avisa a todos y no espera a ninguno

**El problema.** `bajas.py` ya resolvía bien el borrado semántico: no es
transaccional, no bloquea la baja en la bóveda, y deja rastro para reintentar.
Estaba escrito para **un** consumidor. Con dos, «a quién hay que avisarle»
sería una lista en la cabeza de alguien.

**La decisión.** Se generaliza el patrón que ya existía, en vez de inventar
uno nuevo. `sistema_consumidor` dice quién consume la bóveda, desde cuándo y de
qué finalidades se hace responsable; cada retiro genera una fila en
`borrado_pendiente` por cada sistema activo al que le corresponda. La baja en la
bóveda se completa igual, siempre, y los pendientes quedan abiertos hasta que
cada consumidor confirme el suyo.

Cuatro reglas que salen de ahí:

- **Un retiro parcial no molesta a quien no trata esa finalidad.** Por eso
  `alcance_finalidades`.
- **Una baja anterior al alta de un consumidor no es responsabilidad suya.**
  Por eso `alta_en`.
- **Un consumidor no confirma por otro.** `confirmar_borrado()` deriva el
  sistema de la conexión; no lo recibe como parámetro.
- **`paneles` usa la misma función que usa COLOQUIO.** Si se cerrara el
  pendiente por un camino propio, la cascada tendría dos mecanismos y uno de
  los dos envejecería.

**Sumar un tercer consumidor tiene que costar una fila y un `grant`.** Es la
métrica de esta decisión: si sumar el tercero obliga a una migración, el
diseño salió mal.

**Y lo que hace que esto no sea un buzón que nadie lee:**
`v_borrados_sin_confirmar` y la pantalla de Cumplimiento. Una baja abierta hace
mucho tiempo no es una tarea atrasada: es un incumplimiento.

**Dónde vive.** `db/boveda/0014_fase5_superficie_externa.sql` §5,
`functions/panel_api/bajas.py`, `GET /cumplimiento/borrados`.

---

<a id="d49"></a>
## D49 · La regla dura #1 la hace valer la base, no un chequeo que hay que correr

**El problema.** «La PII nunca se escribe en el store semántico» es la
invariante central de la plataforma, y vivía en dos funciones de Python:
`validar_sin_pii()` para los payloads y `auditar_esquema()` para el DDL. Las
dos son correctas y las dos **solo detectan cuando alguien las ejecuta**. Con
un segundo sistema escribiendo de ese lado, basta un `alter table` por `psql`
para romperla sin que ningún control se entere.

Un `check` no sirve: la regla es sobre **nombres de columna**, no sobre
valores.

**La decisión.** Un event trigger en `ddl_command_end` que rechaza la columna
en el momento, lo intente quien lo intente y por el camino que sea. Y la lista
de PII deja de ser un `frozenset` en el repo: se materializa como catálogo en
la base (`campo_pii`, `excepcion_pii`), que es de donde la lee el trigger.
`pii.CAMPOS_PII` pasa a ser su espejo, y hay una prueba que falla si divergen.

**Tres niveles, y ninguno sobra:**

1. El event trigger protege **la base**.
2. `verificar_esquema.py --pii` lee los **archivos** de migración. El trigger
   no ve una migración que todavía no se aplicó, y ésa es justamente la que
   llega a un pull request.
3. `validar_sin_pii()` cubre lo que el DDL no puede ver: una clave de PII
   adentro de un `jsonb`.

**La salida de emergencia es una fila, no un `disable`.** `nombre` es legítimo
en `cuestionario`, `pregunta` y `serie` —es el nombre del estudio, no el de una
persona—, y eso está declarado en `excepcion_pii` con su motivo escrito. Si
mañana hace falta otra excepción, el camino es una migración que la declare y
la explique, no apagar el guardia.

**Y la migración se niega a instalarse sobre un esquema sucio.** Si el store
semántico ya tuviera una columna prohibida, el trigger no la vería —solo mira
lo que se crea de acá en adelante— y habríamos instalado un guardia que mira
para otro lado.

**Dónde vive.** `db/semantica/0005_fase5_prohibicion_pii.sql`,
`functions/panel_api/pii.py`, `scripts/verificar_esquema.py`.

---

<a id="d50"></a>
## D50 · La convocatoria activa se verifica por sistema, y el consumidor declara la suya

**El problema.** R5.2 pide que `contacto_para_convocatoria()` exija «una
convocatoria activa **en el sistema que llama**». La `0014` implementó el caso
de `paneles` —una `participacion` en una `encuesta` abierta— y se lo aplicó a
todos. Con un solo consumidor eso no se nota: el que llama es el dueño de las
tablas donde está la convocatoria.

Con el segundo apareció, y de la peor forma. COLOQUIO convoca a grupos, sus
sesiones viven en Firestore, y la bóveda no las ve; así que pedía el contacto
de alguien que efectivamente había convocado y la bóveda le contestaba que esa
persona **no tiene convocatoria activa**. No es un mensaje de «esto no está
implementado»: suena a dato sobre la persona, y es un defecto nuestro.

Y COLOQUIO no lo podía resolver de su lado sin romper otra cosa: escribir en
`participacion` le está negado por R5.4, y tiene que estarlo —fabricar
participaciones en encuestas ajenas para poder leer un contacto es justo lo
que la superficie externa existe para impedir—.

**La alternativa corta, y por qué se descartó.** Relajar el chequeo cuando el
sistema es `coloquio`: confiar en el consumidor. Es una línea de SQL y deja al
único rol sobre el que el gate se aplica pudiendo barrer la agenda entera de a
una persona por vez, que es exactamente lo que el chequeo existe para impedir.
El gate dejaría de ser un gate para el único que lo necesita.

**La decisión.** El consumidor **declara** su convocatoria en la bóveda, y el
chequeo se ramifica por sistema: `paneles` la tiene en sus tablas, un
consumidor externo la declara. Las dos ramas son el mismo gate —hace falta un
motivo, y el motivo queda escrito—.

**Cuatro cosas que hacen que declarar no sea lo mismo que tener acceso:**

1. **Se declara por función, no por tabla.** `declarar_convocatoria()` es la
   única escritura de un consumidor sobre la bóveda. Por tabla no habría
   forma de reaplicar el gate ni de derivar el sistema de la conexión.
2. **Reaplica el consentimiento.** No se declara una convocatoria de quien no
   consintió; si no, la bóveda guardaría «tal sistema convocó a esta persona»
   de alguien que nunca aceptó que lo contacten.
3. **Vence, y como mucho a 60 días.** Sin tope, una declaración a cien años
   sacaría a esa persona del gate para siempre.
4. **Queda escrita.** Barrer la agenda sigue siendo posible y ahora cuesta
   dejar una fila por persona barrida, con sistema y fecha. La bóveda no lo
   impide: lo deja a la vista, que para un consumidor auditado es suficiente.

**Retención.** Una declaración vencida no habilita nada, así que conservarla
es guardar un dato sin finalidad: se purgan a los 30 días del vencimiento
(`purgar_convocatorias_externas()`, que llama la aplicación, como el
vencimiento de puntos). La baja se las lleva antes: la FK a `persona` cubre el
borrado total y `generar_borrados_pendientes()` cubre el retiro parcial de
`contacto_participacion`. Va en la función de base y no en `bajas.py` por lo
de siempre: una invariante de cumplimiento escrita en Python es una promesa
repetida en dos bases de código.

**Lo que esto le cuesta a la bóveda.** Una tabla más de la que es responsable,
que dice quién convocó a quién en otro sistema. Es un dato nuevo sobre las
personas, y por eso vence y se purga en vez de acumularse. La alternativa
—que no exista— significaba o bien que el segundo consumidor no pudiera
operar, o bien confiar en él sin registro, y la segunda es peor que guardar
la fila.

**Dónde vive.** `db/boveda/0016_convocatoria_externa.sql`,
`scripts/verificar_coloquio.py`,
`functions/tests/test_fase5_convocatoria_externa.py`.

---

<a id="d51"></a>
## D51 · El mapeo a categorías se declara valor por valor, y lo que no se mapea se guarda igual

**El problema.** R3.14.c pedía que los valores de una variable marcada como
atributo categórico se mapearan a las categorías canónicas «igual que se hace
con las etiquetas de una pregunta cerrada». Quedó a medias: el marcado que
armaba la pantalla era `{variable: campo}` y decía *«esta variable es nivel
educativo»*, nada más.

Con eso, un archivo con códigos `1, 2, 3` no tenía cómo conectarse con
`primaria`, `secundaria`, `terciaria`. Funcionaba de casualidad —cuando las
etiquetas del `.sav` coincidían con el catálogo— y fallaba en silencio el
resto de las veces: los filtros demográficos, la composición contra objetivo
y las cuotas del muestreo quedaban trabajando sobre un atributo vacío, y nadie
se enteraba en el momento de cargar.

**Por qué un desplegable por valor y no un campo de texto.** Las opciones de
una pregunta cerrada se cargan como texto (`1=Fernet; 2=Whisky`) y está bien:
su destino es un embedding, y no hay vocabulario que respetar. Acá el destino
es una categoría de un catálogo cerrado. Un texto libre dejaría escribir
`primarai`, que no es ninguna categoría, y el error aparecería recién al
ingestar. El desplegable hace dos cosas que el texto no: impide inventar una
categoría, y **muestra cuáles quedaron sin asignar**.

**Tres decisiones sobre qué pasa con lo que no se mapea:**

1. **No entra como categoría.** Guardar el `99` como si fuera un valor del
   atributo es lo que rompe la aritmética de las cuotas sin que falle nada.
2. **Pero el crudo se guarda.** La fila queda con `valor_crudo` y sin
   `categoria_id`. No es un valor —`f_atributo_persona` la filtra por su
   `coalesce(...) is not null`, así que la persona no tiene ese atributo en
   ninguna vista, consulta ni cuota— y es lo que permite corregir el mapeo
   más adelante sin volver a pedir el archivo.
3. **Se avisa antes, con el conteo.** «El 99 quedó sin mapear» no alcanza
   para decidir; «el 99 quedó sin mapear, son 412 personas» sí. Se puede
   seguir igual: un código de no respuesta no corresponde a ninguna
   categoría y está bien que así sea.

**La sugerencia propone y no aplica.** Cuando el `.sav` trae value labels que
coinciden con el catálogo —sin distinguir mayúsculas ni acentos— el mapeo
llega precargado. Nunca aplicado: una misma etiqueta puede significar cosas
distintas en dos estudios, y aplicar sola una coincidencia de texto es
exactamente el tipo de decisión que después nadie recuerda haber tomado.

**Y el mapeo declarado gana sobre las etiquetas del archivo.** Si quien carga
dijo que el `1` es primaria, el `1=Bajo` del `.sav` no lo contradice: para eso
se le preguntó.

**Una asimetría deliberada en qué se guarda como crudo.** Con mapeo declarado
se guarda **el valor del archivo** (`1`), que es contra lo que se remapea. Sin
mapeo declarado se sigue guardando **la etiqueta** (`Bajo`), que es contra lo
que `recalcular()` resolvía desde R3.14.h. Cambiar eso habría dejado
irrecuperables, sin un mapeo explícito, todos los valores cargados por el
camino viejo.

**Dónde vive.** `functions/panel_api/sav.py` (contrato, sugerencia y valores
del archivo), `functions/panel_api/encuestas.py` (aplicación e informe),
`functions/panel_api/atributos.py` (la fila pendiente y el recálculo con
mapeo), `web/public/js/paginas/encuestas.js`,
`functions/tests/test_mapeo_categorias.py`.

---

<a id="d52"></a>
## D52 · El portal es una superficie separada, aunque comparta el proveedor de identidad

**El problema.** Hasta la Fase 5 la bóveda la tocaban empleados de Equipos
—una decena de cuentas, todas con un rol del padrón interno— y un segundo
sistema registrado. El portal del panelista **da vuelta esa postura**:
autentica a miles de externos contra el store que tiene toda la PII.

La tentación era reusar lo que ya existía: la misma resolución de actor, las
mismas rutas con un permiso nuevo, el mismo sitio. Habría funcionado el
primer día y habría sido un error, porque la API interna está escrita sobre
suposiciones que dejan de valer: que el llamador es de confianza, que puede
nombrar a cualquier persona, y que un pedido raro es un error y no un ataque.

**La decisión: una superficie aparte que comparte Firebase Auth y nada más.**

| | |
|---|---|
| **Resolución de actor propia** | `auth.actor_de_portal()` no consulta el padrón interno y devuelve `rol = None`. Un panelista no puede ejecutar ningún permiso de la administración, y no porque alguien se acuerde de comprobarlo: porque no tiene con qué |
| **El `id_persona` nunca se recibe** | Sale de `cuenta_panelista`, a partir del `uid` del token. Hay una prueba que recorre **todas** las rutas del portal y falla si alguna lo toma de la URL o no lo resuelve desde la sesión |
| **Lista blanca, no excepciones** | Un atributo se edita solo si alguien lo marcó editable. Por omisión, nada |
| **Página y módulo propios** | `web/public/portal.html` + `web/public/js/portal.js` + `functions/panel_api/portal.py`. El código del portal no importa nada de la app interna |

**Por qué la respuesta de «pedir acceso» es una constante del módulo.** Si
el sistema contesta distinto ante un correo que existe y uno que no,
cualquiera averigua quién integra el panel probando direcciones. Eso es una
filtración de datos personales aunque nunca se muestre un perfil. El mensaje
vive en **una sola constante** porque dos textos parecidos escritos en dos
lugares terminan divergiendo, y la diferencia *es* la filtración. Los
intentos fallidos se registran igual —hasheando el correo— porque si solo
contaran los de panelistas reales, probar direcciones ajenas no tendría
límite.

**Lo que no se guarda en claro.** El registro de accesos hashea el correo: un
intento puede ser de alguien que no es panelista, y juntar su dirección sería
recolectar datos de quien no aceptó nada, en la tabla que menos lo justifica.

**Precedencia: la persona gana.** Un valor con `origen = 'panelista'` es
autoritativo sobre sí mismo. La jerarquía es **panelista > operador >
archivo**, y una ingesta que traiga otro valor lo informa como discrepancia
igual que hoy hace con un campo ya cargado. Nadie sabe mejor que la persona
en qué barrio vive. Efecto secundario valioso: como el historial de R4.1.a
guarda cada vigencia, se aprende **cuándo** cambió su situación, no solo cuál
es hoy.

**Granular, no todo o nada.** El sistema ya modela finalidades y canales por
separado, y el portal lo respeta: se puede dejar WhatsApp sin dejar el panel,
y salir del análisis entre estudios sin dejar de participar. Colapsar todo en
«darse de baja» perdería una distinción que la arquitectura ya sostiene.

**La pérdida de puntos se dice antes.** Está decidido que la baja los pierde.
La decisión no es el problema; el problema sería que apareciera después de
confirmar. Por eso el saldo va a la vista, con los canjes pendientes nombrados
uno por uno, **antes** del botón.

**Y la baja se confirma como «en curso», no como «hecha».** La cascada
incluye consumidores externos que confirman de forma asincrónica (R5.3):
decir «listo, ya borramos todo» sería afirmar algo verificable y falso.

**Dónde vive.** `db/boveda/0017_fase6_portal_panelista.sql`,
`functions/panel_api/portal.py`, `functions/panel_api/auth.py`,
`web/public/portal.html`, `functions/tests/test_fase6_portal.py`.

<a id="d53"></a>
## D53 · El login del portal pasa por el backend, no por el SDK del navegador

**El cambio.** R6.1.a reemplaza el acceso por enlace mágico —un correo por
visita— por usuario y contraseña. Para un portal al que se vuelve cada
varios meses, pedir un correo cada vez es la diferencia entre un portal que
se usa y uno al que nadie vuelve, y además deja el acceso a merced de la
entregabilidad del correo.

Lo natural en Firebase habría sido que la página llame a
`signInWithEmailAndPassword` y que el servidor se limite a verificar el
token. **No se hizo, y el porqué es lo que vale documentar**, porque la
decisión tiene un costo visible.

### Tres cosas que R6.1.b pide y que desde el navegador no se pueden hacer valer

| Lo que pide la spec | Por qué no alcanza con el SDK |
|---|---|
| «Intentos fallidos repetidos se limitan por correo y por origen» | Un límite que se aplica en el cliente no es un límite. El contador tiene que vivir del lado del servidor y la decisión también |
| «Un panelista dado de baja no puede entrar» | Con login en el cliente la persona obtiene un token válido y recién se la rechaza adentro. Acá se la rechaza en la puerta |
| «El mensaje es genérico, sin distinguir cuál de los dos falló» | Firebase distingue «ese correo no existe» de «contraseña incorrecta» salvo que esté activada la protección contra enumeración, que es **una casilla de la consola**. Una constante del módulo no depende de una casilla |

El tercero es el que no se negocia: un formulario de ingreso que distingue
los dos casos es un buscador de panelistas, y eso es una filtración de datos
personales aunque nunca muestre un perfil.

### El costo, dicho sin vueltas

**La contraseña pasa por nuestra Cloud Function.** En memoria, nunca a un
log ni a una tabla —no hay un solo `print` ni un solo `insert` con la clave—,
pero pasa. Con el SDK del navegador iría directo de la página a Firebase y
nuestro código no la vería nunca.

Se paga una vez y se aprovecha: por eso **fijar** la contraseña también
entra por el backend (`POST /portal/clave`) en vez de mandar a la persona a
la pantalla de Firebase. Un sistema con dos caminos para las contraseñas
tiene dos conjuntos de reglas, y el segundo envejece.

Una aclaración honesta sobre el alcance: la *web API key* de Firebase es
pública por diseño, así que alguien decidido puede llamar a Identity Toolkit
directamente y saltearse nuestro límite. Lo que nuestro límite protege es el
camino del portal; lo que **no** se puede saltear es la no-revelación, porque
la respuesta que ve quien usa el portal la escribe el servidor. Conviene,
además, dejar activada la protección contra enumeración en la consola: es
defensa en profundidad, no la defensa.

### La contraseña la guarda Firebase, el enlace lo controlamos nosotros

Lo que **no** se escribió a mano: hash, almacenamiento y política de
longitud. Eso es Firebase Auth, detrás de `credenciales.py`, con un doble en
memoria para las pruebas.

Lo que **sí** es nuestro: el token del enlace con el que se crea o recupera
la contraseña. Vive en `acceso_portal` y su «una sola vez» es un
`update ... where usado_en is null and vence_en > now()`, en una sola
sentencia, así que dos pedidos simultáneos con el mismo token no ganan los
dos. La alternativa era usar el enlace de restablecimiento de Firebase —el
mismo mecanismo que R2.12 usa con los usuarios internos—, y se descartó por
una razón concreta: con ese enlace, «sirve una sola vez» y «vence a las 24
horas» son promesas de una consola, y la prueba del Definition of Done que
las comprueba estaría probando un doble. Con el nuestro, las comprueba
Postgres.

### Qué pide reautenticar, y qué no

| | |
|---|---|
| **Pide la contraseña de nuevo** | Darse de baja, retirar una finalidad, cambiar el correo |
| **Alcanza con la sesión** | Ver puntos, pedir un canje, editar atributos, prender o apagar un canal |

El criterio es **lo que no se deshace**, no «lo importante». Revocar WhatsApp
se deshace volviéndolo a activar; retirar `uso_semantico` borra embeddings y
volver a consentir no los devuelve. Con contraseña esto es barato —un campo
más, no un correo que esperar—, que es parte de por qué el enlace mágico
hacía inviable este control.

Dos detalles de implementación que son la decisión y no un accidente:

* **Falla cerrado.** Sin proveedor de credenciales la acción no ocurre. Una
  baja que se ejecuta porque la comprobación no estaba disponible es peor
  que una que no se ejecuta.
* **La contraseña se comprueba antes que el código de verificación** al
  cambiar el correo. Al revés, el intento sin contraseña que hace la
  pantalla antes de abrir el modal quemaría el código —`verificar()` lo
  consume al acertarlo— y la persona tendría que pedir otro. La spec pide
  justo lo contrario: que tras reautenticar la acción siga sin volver a
  empezar.

### El corte tras la baja vive en `bajas.retirar`, no en el portal

R6.1.e pide que una baja invalide **todas** las sesiones de inmediato. La
baja la puede ejecutar el titular desde `/portal`, el DPO desde la
administración o un job de cumplimiento: si el corte viviera en el camino
del portal, los otros dos dejarían la credencial viva y las sesiones
abiertas. Es la misma razón por la que el gate de consentimiento es una
vista y no un `select` en Python.

Y «de inmediato» exige una cosa más: `auth.actor_de_portal()` verifica el
token con `check_revoked=True`. Sin eso, revocar sería efectivo recién
cuando venciera el id token, hasta una hora después. Cuesta una consulta a
Firebase por request del portal y se paga: es lo que convierte dos promesas
del spec en comportamiento.

Como la bóveda nunca espera a un sistema de afuera, un Firebase caído **no**
frena la baja: el resultado trae `acceso_al_portal: {estado: "error"}` para
que el DPO pueda ver qué cuenta quedó por apagar. La persona pierde el
acceso igual en cuanto `cuenta_panelista` desaparece con la cascada, porque
sin esa fila la sesión no resuelve a nadie.

### Por qué hay una migración si la spec dice que no hace falta

La spec (§6) dice que no hay cambios de esquema, y para la credencial es
cierto. Lo que no mira es el límite de intentos fallidos: ese contador no
puede compartir fila con el de los enlaces, porque si la compartiera,
**cinco intentos de adivinar una contraseña dejarían a la víctima sin poder
pedir el enlace para recuperarla** —el ataque le cerraría justo la puerta de
salida—. `0018` agrega `motivo` a `acceso_portal`, con su catálogo, y
`emitido_por` para el rastro de quién disparó un envío.

Ese rastro no va a `usuario_auditoria`, que es donde está el de los usuarios
internos, y la razón es de lectura: esa tabla es el registro de escalada de
privilegios entre empleados de Equipos, y es lo que se mira para contestar
«quién puede ver la bóveda». Mezclarle miles de panelistas —que no son
usuarios de la aplicación y no tienen rol— la volvería ilegible justo para
la pregunta que existe para contestar.

### La inactividad la mide la bóveda

«La sesión persiste hasta que el panelista cierre sesión o venza por
inactividad prolongada» (R6.1.b). Firebase no vence sesiones por
inactividad, así que la mide `cuenta_panelista.ultimo_acceso_en`: noventa
días sin entrar y hay que volver a poner la contraseña. La marca se refresca
con una hora de gracia —es un reloj que se lee en meses, no un contador de
pantallas—, así que no cuesta un `update` por cada cosa que el portal pinta.

**Dónde vive.** `db/boveda/0018_r6_1a_acceso_con_contrasena.sql`,
`functions/panel_api/credenciales.py`, `functions/panel_api/portal.py`,
`functions/panel_api/bajas.py`, `functions/panel_api/auth.py`,
`functions/panel_api/inscripciones.py`, `web/public/portal.html`,
`functions/tests/test_r6_1a_clave.py`.

---

<a id="d54"></a>
## D54 · Los embeddings van en 512 dimensiones, y la dimensión es un contrato con la base

**La decisión.** `voyage-3.5` devuelve 1024 dimensiones por omisión. El
sistema le pide **512**.

### Por qué, con números

| | 1024 dims | 512 dims |
|---|---:|---:|
| Vectores de 200.000 respuestas | ~820 MB | ~410 MB |
| Con el índice HNSW | ~1,5–2 GB | ~0,8–1 GB |
| Instancia necesaria | ~4 GB RAM (custom) | `db-g1-small` (1,7 GB) |
| Costo mensual de esa instancia | ~US$ 50–70 | ~US$ 34 |

Son unos US$ 20–35 por mes, para siempre, y el cambio en sí cuesta cero: el
corpus era de una respuesta cuando se tomó la decisión.

**Lo que se resigna**, dicho sin maquillaje: entre uno y dos puntos
porcentuales de calidad de recuperación según los benchmarks de Voyage.
Truncar no es cortar al azar —`voyage-3.5` está entrenado con *Matryoshka*,
así que las primeras dimensiones concentran la mayor parte de la
información— pero esos benchmarks **no son sobre respuestas de encuesta en
español rioplatense**. Por eso la decisión no se da por buena hasta la
validación con corpus propio que documenta el paso 7 de
`DESPLIEGUE - 512 dimensiones.md`, y por eso esa validación se hace con 5.000
y no con 200.000: para que volver atrás siga siendo barato.

### El bug que había que arreglar igual

`embeddings.py` guardaba `self.dims` y **nunca se lo mandaba a Voyage**. El
cuerpo del pedido no llevaba el parámetro, así que la API devolvía su default
pase lo que pase: `EMBEDDINGS_DIMS` se leía, se propagaba por tres módulos y
no hacía nada.

Es el tipo de falla que no da error. El valor estaba ahí, el código lo movía
de un lado a otro, y la única forma de notarlo era contar las dimensiones de
un vector devuelto. Arreglarlo es una línea —`output_dimension`, nombre
verificado contra la documentación de Voyage—, y lo que sigue es lo que
importa de esta entrada.

### La dimensión es un contrato entre tres cosas

```
EMBEDDINGS_DIMS   →  lo que el proveedor le pide a la API
output_dimension  →  lo que la API devuelve
vector(512)       →  lo que la columna acepta
```

Si las tres no dicen lo mismo, **el síntoma no es un error claro: es una
factura.** La ingesta embebe el lote entero —ésa es la parte que cuesta
plata— y recién al insertar Postgres rechaza el vector por largo. Con 200.000
respuestas, eso es pagar por nada y encima quedarse sin ingesta.

Tres controles, en tres lugares distintos porque fallan distinto:

| Dónde | Qué atrapa |
|---|---|
| `Voyage.__init__` | Una dimensión que el modelo no genera (777). Falla al construir, no cuando la API conteste un 400 a mitad de una ingesta larga |
| `_controlar_largo()` | Que lo devuelto tenga el largo pedido. Cubre el caso en que el parámetro cambie de nombre y la API lo **ignore en silencio** |
| `esquema.desajuste_de_dimension()` | Que el proveedor y la columna estén de acuerdo. Lo llama la ingesta **antes de mandar nada a embeber**, que es el único momento en que sirve |

El tercero es el que ahorra dinero, y por eso corre antes del `embeber` y no
antes del `insert`.

### Dos cosas que salieron al implementarlo

**El plan no preveía una dependencia.** `v_respuesta_estudio` —la vista de
procedencia de la `0002`— selecciona `r.embedding`, así que el `drop column`
del plan fallaba con *«other objects depend on it»*. La migración la tira y
la recrea idéntica. Se descubrió ensayando contra un Postgres de verdad, que
es la única forma en que se descubren estas cosas.

**Y una migración que borra datos necesita dos guardas, no una.** La primera
es la del plan: abortar si el corpus ya creció, con una salida de escape
explícita. La segunda no estaba y hace falta igual: abortar si **ya está
aplicada**. Sin ella, una segunda corrida accidental borra el corpus antes de
fallar — y acá la segunda corrida es probable, porque `scripts/pg_pruebas.sh`
reaplica todas las migraciones cada vez que se lo invoca.

### Por qué la migración crea una vista que parece decorativa

`v_dimension_embeddings` existe por dos razones y la segunda es la que la
hace obligatoria:

* El diagnóstico la lee para comparar contra `EMBEDDINGS_DIMS`.
* **Una migración que solo cambia un tipo es invisible para
  `verificar_esquema.py`.** `respuesta.embedding` existe desde la `0001`, así
  que preguntarle a la base «¿está?» diría que sí con la `0006` sin aplicar.
  Es la misma lección de la `0015` en la bóveda ([D45](#d45)): lo que no crea
  ningún objeto nuevo no se puede detectar, y entonces hay que crear uno que
  valga la pena.

Como efecto lateral, la baranda que compara `esquema.py` con el DDL aprendió
a reconocer `drop` + `create` como un **reemplazo** y no como una creación.
Antes solo entendía `create or replace`, y eso obligaba a declarar objetos
que ya existían: `v_demografia` figuraba en la `0008` y la `0010`, y
`v_atributo_persona` en la `0010`, cuando esas migraciones solo las
reescriben. Declararlas daba esas migraciones por aplicadas sin haber
corrido, que es exactamente el estado peligroso —en la `0010`, las vistas
andando y la función que resuelve los atributos sin existir—. Quedaron
sacadas.

**Dónde vive.** `db/semantica/0006_embeddings_512.sql`,
`functions/panel_api/embeddings.py`, `functions/panel_api/esquema.py`,
`functions/panel_api/ingesta.py`, `functions/panel_api/config.py`,
`functions/tests/test_dimension_embeddings.py`.

---

<a id="d55"></a>
## D55 · La ingesta deja de vivir en una request, y el estado vive en la base

**La decisión.** Confirmar una carga ya no la procesa: la **persiste, la parte
en lotes y encola una tarea por lote**. La pantalla pasa a mirar el avance.

### El techo que había

La ingesta corría entera dentro de una sola request HTTP. Con unos cientos de
respuestas andaba; con una base real no llega. 200.000 respuestas son ~1.560
llamadas al proveedor de embeddings en serie —media hora larga solo de eso,
más los inserts— y Cloud Run corta a los 300 segundos. La carga fallaba con un
error genérico **después** de haber procesado una parte, y no había forma de
retomar.

Subir el timeout corre la pared de lugar: el máximo de una función de 2ª
generación son 60 minutos. Son tres problemas distintos en uno —hay un techo
duro, el analista queda a ciegas, y un fallo tardío obliga a rehacer todo— y
los tres se arreglan sacando el trabajo de la request.

### Lo que no cambió, a propósito

**La tarea llama a `encuestas.ingestar()` y `cargas.ingestar()`, las de
siempre.** No hay una segunda implementación de la ingesta, y eso es el motivo
de la decisión y no un atajo:

* El **gate de consentimiento se re-evalúa en cada lote**, porque está adentro
  de la función que cada lote llama. Quien retira `uso_semantico` a mitad de
  una carga no entra en los lotes que faltan (R-ASYNC.6) sin que haya que
  escribir nada nuevo. Es la misma razón por la que el gate es una vista y no
  Python ([D43](#d43)): la regla se cumple donde pasa el dato, no donde alguien
  se acordó de chequearla.
* El **guardrail de PII sigue corriendo** en la vía nueva, por el mismo motivo.
* La **idempotencia de los upserts** vale igual para un reintento de Cloud
  Tasks: reprocesar un lote no duplica respuestas, ni membresías, ni
  participaciones.

Una ingesta «asincrónica» escrita aparte habría tenido que volver a demostrar
esas tres cosas, y habría empezado a divergir con el primer arreglo que se
hiciera de un solo lado.

### El plan se congela al confirmar

Las filas ya despivotadas y con el mapeo resuelto se guardan junto con el
trabajo. Las tareas **no vuelven a interpretar el archivo**.

No es una optimización. Si cada tarea re-resolviera qué variables son
demográficas y cómo se mapea cada código, dos tareas de la misma carga podrían
usar criterios distintos —alcanza con que alguien edite el catálogo de
atributos mientras la carga avanza— y el estudio quedaría con la mitad de las
respuestas teniendo una pregunta que la otra mitad no tiene. Resolver el mapeo
una sola vez hace que las tareas sean puramente mecánicas.

### El progreso se cuenta sobre los lotes, no sobre un contador

La spec (§6) pedía guardar `lotes_terminados` y `filas_procesadas` en la fila
del trabajo. No se hizo: el avance se **deriva** en `v_ingesta_progreso`,
contando los lotes por estado.

El motivo es la concurrencia, que es justamente lo que esta entrega introduce.
Con tres tareas escribiendo a la vez, un contador incrementado a mano se
desincroniza —dos `update` que se pisan, un reintento que suma dos veces, una
tarea que muere después de incrementar y antes de marcar su lote— y **el
síntoma es una barra de progreso que miente**, que es peor que no tenerla. El
lote tiene un estado y es el único lugar donde está escrito; contar es gratis
y no puede desincronizarse de sí mismo.

El tiempo restante se estima de lo mismo: `segundos_transcurridos` dividido
los lotes hechos, por los que faltan. Es grosero y alcanza, porque los lotes
son del mismo tamaño.

### Reintentar lo transitorio, no lo roto

Cloud Tasks reintenta con espera creciente, cinco veces. Pero reintentar cinco
veces un lote que falla por una fila mal formada gasta tiempo y **embeddings
que se pagan** en algo que va a fallar igual. Por eso el procesador distingue:

| Qué pasó | Qué hace la tarea |
|---|---|
| Timeout de red, 429 del proveedor, caída momentánea de la base | Propaga el error: Cloud Tasks reintenta |
| `ErrorDeDatos` —una fila que ningún reintento arregla— | Marca el lote fallido y **devuelve bien**: la cola no insiste |

Un lote fallido no tira abajo los que entraron. La carga queda
`terminada_con_errores`, la pantalla dice cuál falló y por qué, y el botón
reintenta **ese lote**: lo que ya se procesó está pago y no se rehace.

**El número de intento va en el nombre de la tarea.** Cloud Tasks deduplica por
nombre durante aproximadamente una hora después de completada una tarea; sin el
intento adentro, el segundo encolado del mismo lote se descartaría en silencio
y el botón de reintentar no haría nada visible.

### Encolar después de confirmar, y tolerar que el encolado falle

El trabajo se **commitea antes** de encolar las tareas. El orden importa: si se
encolara primero, una tarea podría empezar a procesar un lote que todavía no
está en la base.

Y si el encolado falla —la API de Cloud Tasks caída, un permiso que falta— la
respuesta igual devuelve el trabajo, marcando los lotes `sin_encolar`. Quedó
guardado y se puede reencolar con el mismo botón de reintentar. La alternativa
—abortar y perder el archivo ya despivotado— castiga al analista por un
problema de infraestructura.

### El patrón de memoria, que era un bug aparte

`embeddings.embeber_en_lotes()` hacía `vectores = []` y acumulaba con
`extend()` cada lote de 128, y la ingesta lo llamaba con **la lista completa de
textos**. Con 200.000 respuestas eso son ~100 millones de números como objetos
de Python: varios GB, contra 1 GiB configurado en la función.

Con lotes de 2.000 el problema no aparece. Pero no aparece **por el tamaño
elegido**, no porque el patrón esté bien: el día que alguien suba el lote
buscando velocidad, la función se queda sin memoria y el síntoma es un error
genérico difícil de atribuir.

Por eso se **reemplazó la función en vez de arreglarla por dentro**.
`embeber_por_lotes()` es un generador que entrega `(inicio, vectores)` y la
ingesta guarda cada sub-lote antes de pedir el siguiente. Dejar la firma vieja
andando habría dejado disponible la forma que acumula; sacarla hace que el
llamador no pueda acumular sin darse cuenta. El tamaño de lote vuelve a ser lo
que debería haber sido siempre: **cuánto trabajo se rehace cuando un lote
falla**, no una condición de supervivencia.

Que no se confunda con [D54](#d54): bajar a 512 dimensiones reduce la memoria
de **la base** —que el índice HNSW entre en RAM— y el costo de la instancia. La
memoria de la función es otra cosa; 512 solo divide por dos lo que se acumula y
deja el orden de magnitud igual.

### Un solo camino, también para las cargas chicas

Una carga de diez filas pasa por la misma vía: un lote, una tarea, el resumen
apenas termina. Se evaluó mantener la vía sincrónica para cargas chicas y se
descartó: son dos caminos que mantener y dos lugares donde arreglar el próximo
bug de ingesta, a cambio de uno o dos segundos.

### La pantalla no guarda el progreso

El estado vive en la base y la pantalla solo pregunta. Cerrar la pestaña no
pierde nada: al volver, la pantalla busca el trabajo **por su destino** —esta
encuesta, esta carga— y retoma el seguimiento. Guardar el progreso en
`localStorage` habría sido más simple y habría perdido exactamente lo que el
requisito pide, porque el progreso que importa es el del servidor.

### Lo que la migración agrega además de las tablas

Dos cosas que no son obvias y que el repo ya había aprendido antes:

* **`v_ingesta_progreso` también existe para ser detectable.** Es la lección de
  la `0015` ([D45](#d45)) y de la `0006` del store semántico ([D54](#d54)): lo
  que `verificar_esquema.py` no puede ver, no puede avisar que falta.
* **`revoke execute … from public` sobre `purgar_ingestas_terminadas()`.**
  Postgres le da `execute` a `public` en toda función nueva, así que sin ese
  `revoke` **COLOQUIO podría purgar las filas de nuestras cargas**. No lo
  encontró una revisión: lo encontró `verificar_coloquio.py`, cuya lista blanca
  de privilegios efectivos falló en cuanto apareció la función. Es la tercera
  vez que esa prueba atrapa algo que leer el diff no atrapaba.

**Dónde vive.** `db/boveda/0019_ingesta_diferida.sql`,
`functions/panel_api/diferida.py`, `functions/panel_api/embeddings.py`,
`functions/panel_api/ingesta.py`, `functions/panel_api/ruteo.py`,
`functions/main.py`, `web/public/js/paginas/encuestas.js`,
`functions/tests/test_ingesta_diferida.py`.

---

<a id="d56"></a>
## D56 · Lo que decidió una carga se guarda con la carga

**La decisión.** El plan persistido registra **con qué modo** se corrió la
carga y **si se declaró la evidencia** de consentimiento, aunque ninguna
tarea los use.

### De dónde salió

Una carga de 1131 filas en modo «crear los individuos» terminó en `ok`, con
cero personas creadas y las 1131 filas en `sin_mapear` por falta de alias. La
pregunta obvia —¿llegó el modo al backend?— **no se podía contestar**: el
plan guardaba las preguntas, las demográficas, la columna identificadora y el
tipo de identificador, y nada sobre el modo. Se investigó durante un día la
hipótesis de que el front no lo mandaba.

Y no lo mandaba o sí: eso sigue sin saberse para *esa* carga, porque el dato
no se guardó. Es exactamente el costo de no guardarlo.

**Lo que sí quedó probado** es que el backend lo honra, también con
`tipo_identificador: "alias"`, que era la única diferencia entre el caso real
y la prueba de ruta que ya existía. Esa prueba estaba, pasaba, y no cubría la
combinación que fallaba.

### Por qué en el plan, si ninguna tarea lo lee

Las personas se crean **en la ruta**, antes de encolar: cuando la primera
tarea corre, ya existen. El modo no cumple ninguna función en el
procesamiento de un lote.

Se guarda igual, y la distinción importa: el plan dejó de ser solo *lo que
las tareas necesitan* para ser *lo que decidió esta carga*. Una carga que
salió mal se diagnostica mirándola, no reconstruyendo de memoria qué eligió
quien la lanzó tres semanas antes.

De la evidencia va **el hecho y no el contenido** —`evidencia_declarada:
true`—, porque el plan se devuelve por API y qué variable del archivo lleva
el consentimiento no tiene por qué viajar con él.

### El aviso era verdad y no servía

«Ninguna respuesta quedó habilitada para ingestar» no distingue **no matcheó
nadie** de **nadie consintió**, que se arreglan de forma opuesta: el primero
cambiando el modo o la columna, el segundo no se arregla —esas respuestas no
se conservan, y está bien—.

Cuando el 100% de las filas cae con motivo `sin_alias_para_ese_origen`, el
sistema tiene toda la información para decir qué hacer, y ahora la dice. Un
aviso que obliga a abrir la base para entenderlo es medio aviso.

### La guarda del documento, que es parte de este arreglo y no un extra

En la misma carga, la variable marcada como `documento` era *«¿has consumido
alguno de estos productos?»*, con valores `0` y `1`. El dedup de R1.2
resuelve **primero por documento**: crear esas 1131 personas las habría
fusionado en dos.

No pasó porque el bug del modo lo impidió. **Arreglar el modo quita la
casualidad**, así que la guarda va en el mismo cambio: `crear_individuos`
rechaza un archivo de diez filas o más cuya columna de documento traiga menos
de tres valores distintos, y nombra las etiquetas de esa variable —ver
«Unchecked / Checked» al lado de «documento» no deja lugar a dudas—.

Es un error de configuración y no del software, pero el daño es irreversible
y el software lo puede ver venir. Va **antes de escribir nada**, en el mismo
lugar donde ya se controla que la variable de consentimiento exista en el
archivo, y por la misma razón.

El umbral es deliberadamente bajo: atrapa lo que destruye datos —`0`/`1`,
sí/no, una constante— y no se mete con un padrón chico legítimo.

**Dónde vive.** `functions/panel_api/ruteo.py` (`_plan_de`),
`functions/panel_api/ingesta.py` (`_por_que_no_entro_nada`),
`functions/panel_api/sav.py` (`_controlar_documento_plausible`),
`web/public/js/paginas/encuestas.js`,
`functions/tests/test_modo_crear_individuos.py`.

---

<a id="d57"></a>
## D57 · Lo que se revisa es lo que se ejecuta, y lo que se cruza se registra

La Fase 7 no agrega capacidades: saca trabajo manual y hace visible lo que
el sistema ya sabía. Tres decisiones de las seis la definen.

### La revisión se pide por la misma ruta que ejecuta

El resumen previo a importar (R7.2) **no** se arma en la pantalla ni en un
endpoint aparte. Va por la misma ruta de ingesta, con el mismo cuerpo, y lo
único que cambia es una bandera: `solo_revisar`.

La alternativa obvia —un `POST /…/revision` que recibiera lo mismo y
devolviera el resumen— empieza igual y diverge con el primer cambio que
alguien haga de un solo lado. Y lo que divergiría es exactamente la pantalla
que dice «esto es lo que va a pasar». Un resumen que no refleja la
ejecución es peor que no tener resumen: da confianza sin fundamento.

Del lado de la pantalla, la misma idea: el cuerpo se arma **una vez**
(`cuerpoDeIngesta`) y se manda dos veces. Revisar y confirmar no pueden
construir objetos distintos porque hay un solo objeto.

> Esto obligó a un arreglo colateral que valía la pena igual.
> `api.encuestas.ingestar` desarmaba un objeto con nombres propios y
> rearmaba el del servidor; esa lista blanca descartaba en silencio
> cualquier campo que no enumerara, y ya había costado un bug —`modo` se
> mandaba y nunca llegaba ([D56](#d56))—. Ahora el cuerpo viaja tal cual.
> Un traductor que pierde lo que no conoce es peor que no tener traductor.

### El conteo de claves de dedup es el requisito, no el adorno

De las siete secciones del resumen, seis ayudan a leer. Una detecta el error
que destruye datos.

Si alguien marca como `documento` una variable de sí/no, el resumen lo
grita: «documento: 1131 filas, **2 valores distintos**, 1129 colisionan →
se crearían **2 personas**». Sin ese número el error pasa, y como el dedup
resuelve primero por documento, la base entera se fusiona en dos registros.

No es hipotético: pasó el 3 de octubre y lo evitó un bug distinto. Por eso
el umbral de «sospechosa» es generoso —la mitad de las filas colisionando ya
es advertencia grave— y por eso las personas estimadas se calculan
siguiendo **el orden real del dedup** (documento, si no correo, si no nombre
+ fecha de nacimiento) y no un criterio propio que sería más prolijo y menos
cierto.

### Ver las respuestas de alguien es cruzar los dos stores, y se registra

R7.6 es el punto donde la separación entre bóveda y semántico **se cruza a
propósito**: la pantalla muestra qué opinó una persona identificada. Es
necesario para operar —es la única forma de entender por qué alguien aparece
o no en una consulta— y es, exactamente, lo que la arquitectura evita que
pase por accidente.

Entonces se registra igual que una reidentificación, con un motivo propio:
`respuestas_panelista`. Y el registro **no es opcional ni depende de que lo
recuerde quien escriba la próxima ruta**: la consulta sin registro
(`ficha.respuestas`) existe solo para poder probarla sola, y la ruta llama
siempre a `respuestas_con_registro`.

**La ficha no muestra nombre, documento, correo ni celular**, y eso no es
pudor. Si la ficha mostrara el nombre con un clic, la auditoría de
reidentificación dejaría de reflejar quién vio los datos de quién — que es
lo único que esa auditoría existe para demostrar. Hay una prueba que recorre
la salida y falla si aparece cualquiera de los cuatro, por si alguien
agrega uno «porque es cómodo».

Lo que sí hay que tener presente, y no lo resuelve ningún código: **los
demográficos combinados son cuasi-identificadores**. Sexo, localidad y tramo
etario juntos pueden señalar a una persona en un panel chico, y R7.4 los
pone en una lista de doscientas filas. No lo bloquea, pero por eso los
atributos de categoría especial no se ofrecen como columna: verlos de a uno
en una ficha no es lo mismo que verlos todos juntos.

### El catálogo de motivos no tiene clave foránea, y es deliberado

La `0020` cataloga los motivos de reidentificación en una tabla, como la
`0015` con `accion_usuario` y la `0018` con `motivo_acceso_portal`. Pero a
diferencia de esas dos, **no agrega la FK**.

`auditoria.registrar_reidentificacion` dice, desde la Fase 2:

> «No falla nunca por el contenido: si el motivo no está en la lista se
> guarda igual, porque perder el rastro es peor que guardarlo con una
> etiqueta rara.»

Una FK invierte ese intercambio: un motivo nuevo que alguien olvidó
catalogar deja de escribir la fila, y se pierde el registro de que alguien
reidentificó a alguien. Para el registro que sostiene el diseño de dos
stores, eso está al revés.

La vista lleva la misma lógica un nivel más abajo: `left join` y no `join`,
con `coalesce` que marca el motivo como «sin catalogar» en vez de
esconderlo. Si la vista filtrara, catalogar mal volvería **invisible** una
reidentificación.

Lo que la FK habría dado gratis —que el código y el catálogo no diverjan—
lo da una prueba espejo, igual que `pii.CAMPOS_PII` con `campo_pii`.

### La brecha entre stores es la razón de la pantalla de estadísticas

Los conteos sueltos —«1.008 panelistas», «24.935 respuestas»— se miran una
vez. El número que se usa todas las semanas es *«de mis 1.131 panelistas,
¿sobre cuántos puedo realmente consultar?»*: eso define si una búsqueda
sirve, y hoy no se puede saber sin cruzar las dos bases a mano.

Por eso la brecha va primero y no al final, y por eso uno de sus tres
números —individuos del store semántico sin panelista en la bóveda— no es
una estadística sino **un chequeo de integridad que tiene que dar cero**. Si
no da cero, hay respuestas de gente que ya no existe y la cascada de baja no
las alcanzó.

El cruce se resuelve por conjuntos de `id_persona` y en Python, porque son
dos instancias distintas y no hay FK entre ellas. Es la misma forma en que
se cruza todo en esta plataforma.

**Dónde vive.** `db/boveda/0020_motivos_reidentificacion.sql`,
`functions/panel_api/resumen_ingesta.py`, `functions/panel_api/ficha.py`,
`functions/panel_api/estadisticas.py`, `functions/panel_api/atributos.py`,
`web/public/js/consentimiento.js`, `web/public/js/paginas/estadisticas.js`,
`web/public/js/paginas/consultas.js`, `web/public/js/paginas/encuestas.js`.

<a id="d58"></a>
## D58 · Una fila por persona: el gate se pregunta con `exists`, y la cardinalidad se verifica

**El problema.** `v_persona_convocable` —la única superficie desde la que un
consumidor externo ve personas— se armó en la `0014` con un `join` contra
`consentimiento`. El gate funcionaba (solo aparecía quien consintió), pero la
cardinalidad no: **una fila por consentimiento vigente**. Con las dos
finalidades que pide el alta, cada persona aparecía dos veces; con un
re-otorgamiento (versión nueva del texto), tres. En la primera carga real,
1008 personas daban 2016 filas, y COLOQUIO contaba la muestra al doble,
calculaba cuotas sobre un universo inflado e invitaba a cada persona dos
veces si no deduplicaba por su cuenta. Nada fallaba: los valores eran
correctos, solo que repetidos (`specs/BUG_v_persona_convocable_duplica.md`).

**La decisión.** La `boveda/0021` rehace la vista sobre una función nueva:

- **Una fila por persona**, con el consentimiento evaluado con `exists` y
  las finalidades vigentes en un arreglo, `finalidades text[]`. El gate se
  pregunta igual que antes con otra sintaxis:
  `where 'contacto_participacion' = any(finalidades)`. El criterio no cambia
  —persona activa, sin lápida, con consentimiento vigente—; cambia la forma.
- Las finalidades de **ámbito estudio** (`grabacion_av`,
  `moderacion_automatizada`, `difusion_verbatim`) **no** entran al arreglo:
  `grabacion_av` en la lista de una persona daría a entender que se la puede
  grabar en cualquier estudio, que es lo que el trigger de la `0013` existe
  para impedir. Se consultan en `v_persona_finalidad_vigente`, una fila por
  `(id_persona, finalidad, ref_estudio)`. Ahí el `distinct` no es un parche:
  las columnas **son** la clave, así que no hay ninguna que pueda variar y
  volver a abrir las filas.
- `declarar_convocatoria` y `contacto_para_convocatoria` se reescriben para
  preguntar sobre el arreglo. La entrega y su fila de auditoría siguen siendo
  una por lectura: el `insert ... values` nunca dependió de la vista.

**Por qué `exists` y no `distinct`.** Un `distinct` sobre la vista vieja
habría dado el número correcto hoy y lo habría vuelto a romper el día que
alguien sumara una columna que variara entre las filas repetidas. El `exists`
dice lo que el gate quiere decir —«existe un consentimiento vigente»— y no
multiplica nunca.

**Alternativas descartadas.**
- *Dejar la vista en `contacto_participacion` solamente y sacar la columna.*
  «Convocable» se lee como «contactable», pero COLOQUIO también necesita
  saber quién tiene `uso_semantico_cuali`, y una persona con solo una de las
  finalidades de persona dejaría de verse.
- *Mantener la columna `finalidad` con un valor fijo.* Las consultas viejas
  de un consumidor seguirían corriendo y devolverían otra cosa sin avisar. El
  cambio es deliberadamente **ruidoso**: `where finalidad = …` falla con
  «column finalidad does not exist», que es mejor que un número distinto en
  silencio.

**Lo que se revisó con el mismo patrón.** `v_fatiga_panelista` es una fila
por `(persona, panel)` **a propósito** —el umbral de fatiga es por panel— y
agrega con `group by`, así que no multiplica; con participaciones en dos
encuestas del mismo panel y una persona en dos paneles cuenta bien (hay
prueba). `f_persona_convocable()` devolvía filas y estaba afectada: es la que
se corrigió. `contacto_para_convocatoria()` devuelve un escalar y escribe una
fila de auditoría por entrega. Y de paso apareció la misma clase de error del
lado de la pantalla: `estadisticas.consentimiento` contaba filas de
`consentimiento`, así que un re-otorgamiento contaba dos veces a una persona y
«sin el» podía dar negativo. Ahora cuenta personas, desde la vista.

**Cómo se verifica.** `scripts/verificar_coloquio.py` suma el chequeo «una
fila por persona en cada vista»: cada relación de la superficie con
`id_persona` tiene que declarar su clave en `CLAVE_POR_RELACION`, y el chequeo
compara filas contra claves distintas. Falla también si aparece una vista con
`id_persona` sin clave declarada: la próxima no se puede saltear la pregunta.
El escenario usa una persona con las dos finalidades, que es el caso que la
batería no tenía y por eso no vio el bug. Pruebas:
`test_bug_convocable_una_fila.py`.

**Consecuencias.** Es un cambio de contrato para COLOQUIO: su código tiene que
pasar de `finalidad = 'x'` a `'x' = any(finalidades)`, y de `ref_estudio` en
la vista a `v_persona_finalidad_vigente`. La receta está en el documento de
despliegue. Del lado de `paneles`, `consentimiento.esta_vigente()` y
`filtrar_con_consentimiento()` ya preguntan sobre el arreglo.

Y una que apareció al ensayar el despliegue: la `0021` **tira y rehace** la
vista, y el primer borrador, corrido dos veces, fallaba después del `drop` y
antes de los `grant` —COLOQUIO quedaba sin acceso—. Ahora va entera en una
transacción y se puede repetir sin efecto; y como el código anterior y la
vista nueva no se entienden, el rollback no puede ser «volver el código»: es
`db/revertir/boveda_0021.sql`, que deja un esquema idéntico al de la `0020`,
permisos incluidos (comprobado con `pg_dump`).

**Dónde vive.** `db/boveda/0021_persona_convocable_una_fila.sql`,
`db/revertir/boveda_0021.sql`, `functions/panel_api/consentimiento.py`, `functions/panel_api/estadisticas.py`,
`scripts/verificar_coloquio.py`.

<a id="d59"></a>
## D59 · La ficha explica el resultado que se está mirando, y una ruta sin pantalla no existe

**El problema.** El informe de la Fase 7
(`specs/INFORME_fase7_que_falta.md`) encontró dos cosas. Una ruta declarada
con `requisito="R7.3"` que no devolvía la mitad del requisito —la evidencia
del resultado—. Y requisitos con el backend terminado y ninguna pantalla que
lo usara o con la pantalla en un solo lugar: para quien usa el sistema, eso
es indistinguible de no tenerlo.

**La decisión.**

- **La evidencia llega desde el resultado y el servidor la relee.** El
  ranking ya trae, por individuo, los `respuesta_id` que lo justificaron. La
  ficha los manda (`?respuestas=12,34`) y el servidor devuelve el texto
  guardado de esas respuestas, **solo si son de esa persona**: un id ajeno se
  descarta y se informa cuántos. Recalcularla con el criterio como parámetro
  podía dar otra evidencia que la que el analista tiene en pantalla, y la
  ficha existe para explicar *este* resultado. Tope de 20 ids: la ficha no es
  una vía para bajar el contenido de alguien sin pasar por R7.6. No registra
  reidentificación: es contenido atado a un id opaco, lo mismo que ya
  mostraba la lista.
- **Las respuestas procesadas se cargan a pedido.** R7.6 registra cada
  lectura como cruce de stores. Si la tabla se cargara sola al abrir la
  ficha, abrirla para corregir un correo dejaría dicho que alguien leyó las
  opiniones de esa persona, y el registro dejaría de significar algo. De
  entrada se ve el conteo por estudio (que no se registra: saber *que*
  respondió no es ver *qué*); la tabla aparece con un botón que dice que
  queda registrado. La sección es un solo módulo (`respuestas.js`) para las
  dos fichas: dos copias de la misma tabla terminan divergiendo, y lo que
  divergiría es el aviso.
- **«Ver quién es» en la ficha** es la reidentificación de siempre
  (`POST /reidentificacion`, motivo `consulta`), para una persona. No es un
  camino nuevo.
- **Una ruta de las fases 7 y 8 tiene que usarse desde alguna pantalla.**
  `test_rutas_con_pantalla.py` recorre las rutas declaradas con esos
  requisitos, busca la función de `api.js` que las llama y exige que esa
  función se use fuera de `api.js`. Es la recomendación del informe hecha
  regla. No puede saber si la pantalla *muestra* lo que la ruta devuelve —eso
  lo cuidan las pruebas de cada requisito—, pero sí que el cable esté
  conectado.

**Lo que salió al recorrerlo.** Tres defectos que ninguna prueba de backend
podía ver: «Otorgar finalidad» en la ficha referenciaba una variable
inexistente (`textosActivos`) y no abría; «Ver texto» del desplegable de
versión abría un modal que **cerraba el alta** —ver el texto costaba
volver a llenar el formulario—; y la Fase 7 usaba clases de CSS que no estaban
definidas. Los tres se corrigieron. El primero lo encontró un `eslint` con
`no-undef` sobre el frontend, que conviene correr antes de cada entrega.

**Dónde vive.** `functions/panel_api/ficha.py` (`evidencia`),
`functions/panel_api/ruteo.py` (`ficha_seudonima`),
`web/public/js/respuestas.js`, `web/public/js/paginas/consultas.js`,
`web/public/js/paginas/panelistas.js`, `web/public/js/consentimiento.js`,
`functions/tests/test_rutas_con_pantalla.py`.

<a id="d60"></a>
## D60 · El texto que se embebe: el sistema propone, el analista confirma

**El problema.** El motor de búsqueda es tan bueno como el texto que se
embebe, y ese texto salía casi tal cual del archivo:
«Cigarrillos:Pensando en el ÚLTIMO mes, ¿has consumido alguno de estos
productos? Seleccione los que correpondan → Checked». `Checked` no significa
nada en español, la pregunta arrastra un prefijo y una consigna, se guardan
las opciones que nadie marcó, y una abierta de «Otro» puede traer un
teléfono. Todo corregible a mano, variable por variable; nadie lo hacía
porque no se notaba hasta que una búsqueda fallaba.

**La decisión.**

1. **Una sola implementación de «qué se embebe».** `ingesta.respuesta_de`
   decide, para una celda, si genera respuesta y con qué texto. La usan la
   ingesta, la vista previa y el reproceso. La vista previa la calcula el
   servidor aunque la pantalla la pida en cada tecla: una copia en
   JavaScript divergiría, y lo que divergiría es la vista que dice «esto es lo
   que se va a escribir».
2. **Las decisiones viajan en la pregunta.** `solo_marcadas` y
   `valores_marcados` (batería), `excluir_valores` (no respuesta),
   `prefijo_respuesta` y `fusionada_con` (el «Otro» de una cerrada),
   `pii_aceptada`. Sin ninguna, la ingesta hace exactamente lo de antes. Se
   guardan con la pregunta (`pregunta.normalizacion`) y la lista de no
   respuesta con el cuestionario: un reproceso tiene que saber qué se decidió.
3. **Nada se aplica solo, y el original se conserva.** El diagnóstico
   devuelve hallazgos con **acciones**; la pantalla las aplica cuando el
   analista las elige, las muestra como etiquetas que se pueden quitar, y
   guarda el texto y las etiquetas del archivo (`texto_original`,
   `opciones_originales`). El riesgo central de la fase es una reescritura
   que cambia el sentido de la pregunta y nadie lee: de ahí la propuesta, el
   original y la vista previa.
4. **Jerarquizado y agrupado.** Primero lo que rompe (PII, códigos sin
   traducir, textos truncados), después lo que mejora, al final lo
   informativo. Y lo que se repite variable por variable —textos, pares
   Sí/No, no respuesta, etiquetas, tipos— va en un solo hallazgo por tipo,
   con «aplicar a todas» y «usar en esta». Treinta tarjetas de «texto más
   claro» son el paso de revisión que se confirma sin leer, que es peor que
   no detectar nada.
5. **La PII en texto libre es un indicio y no bloquea.** Patrones de correo,
   teléfono uruguayo, cédula (con dígito verificador, que baja los falsos
   positivos) y URL. Los ejemplos se muestran **enmascarados**: alcanza con
   ver la forma para decidir, y el aviso no tiene por qué repetir el dato que
   advierte. Se ofrece excluir la variable o ingestarla a conciencia; lo
   segundo queda guardado.
6. **La detección corre sobre distribuciones** (`{código: {valor: filas}}`),
   que se arman igual desde un `.sav`, desde las filas de un `.csv` y desde un
   estudio ya cargado. Una sola detección para las tres puertas.

**Alternativas descartadas.**
- *Aplicar las correcciones evidentes de forma automática* (Checked → Sí).
  La spec lo descarta y con razón: el texto es lo que se embebe, y una regla
  automática equivocada degrada la búsqueda sin que nadie se entere.
- *Detectar PII con un modelo.* Fuera de alcance (spec §3): son patrones, y se
  dice que son patrones.
- *Reescribir la pregunta en tercera persona* («¿has consumido?» →
  «¿consumió?»). La propuesta integra la opción, quita consignas y baja
  mayúsculas de énfasis; no conjuga. Es editable y el analista termina el
  trabajo; una conjugación mala es exactamente la reescritura que cambia el
  sentido.

**Un hallazgo de paso.** El archivo de prueba, armado con la estructura del
real, reveló que la sugerencia de marcado demográfico buscaba `^ci` y
proponía «Cigarrillos:…» como **documento** —el marcado que casi fusiona 1131
personas en dos—. Los patrones ahora piden palabra entera (`CI_NUM` sí,
«Cigarrillos», «Televisión» y «Agenda» no).

**Consecuencias.** Descartar lo no marcado cambia qué se puede consultar:
deja de poder buscarse «quiénes **no** consumen» por la vía semántica. El
hallazgo lo dice; para eso está el filtro demográfico o una cerrada normal.
Y la detección de patrones tiene falsos positivos y negativos que se informan
como tales.

**Dónde vive.** `functions/panel_api/calidad_dato.py`,
`functions/panel_api/ingesta.py` (`respuesta_de`, `despivotar`),
`functions/panel_api/semantica.py`, `functions/panel_api/sav.py`,
`functions/panel_api/resumen_ingesta.py`, `db/semantica/0007_fase8_calidad_del_dato.sql`,
`web/public/js/calidad.js`, `web/public/js/paginas/encuestas.js`.

<a id="d61"></a>
## D61 · Un estudio se corrige desde lo que quedó cargado, no desde el archivo

**El problema.** Descubrir después de cargar que los textos quedaron mal
obligaba a pedir el archivo otra vez y rehacer todo. Y el archivo no está: las
filas de la ingesta diferida se purgan a los siete días.

**La decisión.**

- **El dato sale del store semántico.** Cada respuesta guarda su
  `valor_texto` —la etiqueta con la que se embebió— y cada pregunta sus
  opciones de ahora y las originales del archivo. Invirtiendo las etiquetas se
  recupera el código, y con el código y la configuración nueva se recompone
  el texto con `ingesta.respuesta_de`. Un código que no se había traducido
  (`11427`) está guardado como `11427`, así que darle etiqueta lo traduce.
- **Solo se re-embebe lo que cambió.** Si el texto nuevo tiene el mismo
  `hash_texto`, la respuesta no se toca —ni se re-embebe ni se re-escribe;
  hay una prueba que mira el `xmin`—. Si cambió, se re-embebe. Si con la
  configuración nueva ya no genera respuesta (variable excluida, valor de no
  respuesta, opción no marcada), se borra.
- **Por la vía diferida, con el plan congelado.** El reproceso es un
  `ingesta_trabajo` como cualquier carga, con `plan.operacion = 'reproceso'`:
  la pregunta *antes* (para invertir las etiquetas viejas) y *después*. Cada
  lote son ids de respuestas. No es una segunda ingesta: no resuelve
  identidades, no crea individuos ni toca la bóveda; recompone el texto con
  la función de siempre, escribe por `semantica.upsert_respuestas` (con el
  guardia de PII) y **reaplica el gate de `uso_semantico`** en cada lote. Dos
  reprocesos —o un reproceso y una carga— del mismo estudio no corren a la vez.
- **La misma ruta revisa y ejecuta** (`solo_revisar`), como la importación:
  cuántas respuestas se re-embeben, quedan igual o se borran, con ejemplos
  de antes y después.
- **Queda registrado qué se cambió y cuándo** en `reproceso` (store
  semántico), campo por campo. Quién lo pidió queda en el trabajo diferido,
  del lado de la bóveda: el uid de un usuario interno no tiene por qué viajar
  al store semántico.
- **Una variable excluida conserva su pregunta**, marcada `excluida`: una
  serie de la Fase 4 puede apuntarle, y que existió y se excluyó es parte de
  la historia del estudio.

**Alternativas descartadas.**
- *Guardar el valor crudo en `respuesta`.* Habría hecho la inversión
  innecesaria, pero la spec pide no tocar `respuesta`, y con las etiquetas
  originales conservadas la inversión alcanza. El caso en que no alcanza —dos
  códigos con la misma etiqueta— es un archivo mal etiquetado que el
  diagnóstico ya señala.
- *Reprocesar llamando a la ingesta con filas reconstruidas.* Habría
  reincorporado al panel y re-registrado participaciones: efectos sobre la
  bóveda que un cambio de texto no tiene por qué tener.
- *Un destino nuevo en `ingesta_trabajo`.* Habría pedido migrar la bóveda
  para nada: el destino de un reproceso **es** la encuesta o la carga, y la
  operación va en el plan.

**Consecuencias.** Lo que la ingesta dejó afuera no se puede recuperar: lo no
marcado de una batería o los valores de no respuesta excluidos no están en
ninguna parte. La revisión lo avisa cuando se intenta. Y si un reproceso queda
a medias (se actualizaron las preguntas y no se encoló), «Reprocesar todo de
nuevo» pasa todas las respuestas por la configuración actual; el hash hace
que solo se re-embeba lo desactualizado.

**Dónde vive.** `functions/panel_api/reproceso.py`,
`functions/panel_api/diferida.py` (`_ingestar_el_lote`),
`db/semantica/0007_fase8_calidad_del_dato.sql`,
`web/public/js/paginas/reproceso.js`.

---

<a id="d62"></a>
## D62 · Un chequeo que no puede probar se omite, no falla; y la auditoría se prueba con el actor del contrato

**El problema.** Contra Cloud SQL, `verificar_coloquio.py` daba **siempre**
dos fallos que no eran de la bóveda, y cerraba con «la bóveda no está lista
para COLOQUIO». En un despliegue se ignoraron por rutina. Una batería que
falla siempre deja de leerse, y la vez que un fallo sea real nadie lo va a
ver: el costo no era técnico, era de confianza. Los dos eran:

* **«el contacto legítimo queda auditado»** llamaba sin `p_actor` y esperaba
  `coloquio_app` como actor. Pasaba en el cluster local porque ése es el
  `session_user`; contra Cloud SQL el rol es la cuenta IAM y fallaba. Probaba
  el nombre del rol, no la auditoría.
* **«un rol sin registrar no consigue nada»** se conectaba sin credenciales.
  En Cloud SQL eso muere en la autenticación y nunca llega a la superficie.

**Lo que se decidió.** Tres cosas, y ninguna toca la base:

1. **El chequeo de auditoría prueba el contrato.** Manda como `p_actor` un
   email de usuario —lo que COLOQUIO tiene que mandar según
   `HANDOFF_coloquio_fase1.md`— y verifica que **ese** quede escrito. Y uno
   nuevo prueba el caso sin actor: la entrega queda registrada con el rol de
   la conexión y `actor_email` nulo, que es lo que permite encontrar después
   las llamadas que incumplieron.
2. **Un tercer estado: omitido.** Un chequeo que en este entorno no puede
   probar lo que quiere probar levanta `Omitido` con el motivo, y el resumen
   lo informa aparte. **No cuenta como fallo**, y «no está lista» aparece
   solo con fallos reales. Se omiten el del intruso contra Cloud SQL (se le
   pregunta al servidor, no al DSN: el Auth Proxy y el cluster local se ven
   iguales), los que necesitan escenario en `--solo-lectura` y la
   cardinalidad cuando todas las vistas están vacías.
3. **La cardinalidad corre sobre el escenario.** Sobre una bóveda vacía
   «ninguna clave repetida» es cierto sin probar nada; el escenario tiene la
   persona con dos finalidades que hacía aparecer el bug de la `0021`.

**Por qué omitido y no «pasado con aviso».** Porque pasado es una
afirmación: «verifiqué esto». Un chequeo que no corrió no puede afirmarlo, y
mezclarlo con los que sí sumaría verificaciones que no ocurrieron. Y por qué
no fallido: porque es lo que había, y ya se vio que termina ignorado.

**Lo que se descartó.**

- *Cambiar la función para que use `session_user` siempre.* Habría perdido
  quién fue la persona, que es lo que una reidentificación necesita
  demostrar. La función estaba bien; el test, no.
- *Detectar Cloud SQL por el DSN* (`.iam` en el usuario, `127.0.0.1`).
  Contra el Auth Proxy el host es el mismo que el del cluster local, y el
  usuario del dueño no tiene por qué ser IAM. El rol `cloudsqlsuperuser` lo
  tiene toda instancia de Cloud SQL y ningún Postgres común.
- *Omitir el intruso en todo entorno que no sea local.* Si alguien pasa
  `DSN_BOVEDA_INTRUSO` con credenciales de un rol sin registrar, el chequeo
  tiene sentido también contra Cloud SQL, y corre.

**Consecuencias.** La batería pasa de 17 a 18 chequeos. Contra Cloud SQL,
sana, da `18 · 17 pasados · 0 fallidos · 1 omitido` y sale con 0. Sigue
abierto lo de fondo: `p_actor` es nullable, y el chequeo nuevo hace visible
la obligación del cliente sin obligarlo. Rechazar la llamada sin actor sería
un cambio de contrato y de migración.

**Dónde vive.** `scripts/verificar_coloquio.py` (`Omitido`, `es_cloud_sql`,
`veredicto`), `functions/tests/test_bateria_coloquio_estados.py`,
`docs/DESPLIEGUE - COLOQUIO Fase 0.md` §7.1.1.

---

<a id="d63"></a>
## D63 · El paso de revisión advierte; solo frena lo que una decisión no puede suplir

**El problema.** Con la Fase 8 desplegada, una carga con el correo marcado
como `email` mostraba «no hay ninguna variable marcada como documento,
correo…» y no se podía ejecutar (`specs/BUG_validacion_dedup_bloquea.md`).
Eran tres defectos encadenados:

1. **El correo no llegaba.** El diagnóstico de calidad del dato corría sobre
   todas las variables del `.sav`, también las marcadas como demográficas.
   La columna de correo es una abierta llena de correos, así que salía como
   «posibles datos personales» —en rojo— con la acción «Excluir la
   variable», y esa acción **borraba la fila**, marcado `email` incluido.
   Quien seguía el consejo perdía la clave de dedup sin enterarse, y la
   revisión decía la verdad sobre lo que había recibido.
2. **Advertir con una bandera que se leía como prohibir.** `grave` hacía dos
   cosas a la vez —mostrar en rojo y, en la lectura de todos, frenar— y el
   botón seguía diciendo «Confirmar e importar» debajo de un cartel rojo.
3. **Un botón que no respondía.** Con la revisión abierta, el «Ingestar» del
   pie del modal seguía a la vista. Apretarlo volvía a pedir la revisión, la
   pintaba igual encima y dejaba colgada la anterior: nada se movía y no
   llegaba ninguna ingesta.

**Lo que se decidió.**

- **La calidad del dato es sobre lo que se embebe.** El panel filtra al
  pintar (`calidad.soloSemanticas`) lo que habla de variables con rol
  demográfico —se filtra al pintar y no al recibir porque el rol se puede
  cambiar después—, y `aplicarPropuesta` no toca nunca una fila demográfica.
- **«Hay clave» tiene una sola definición**, `resumen_ingesta.claves_de_dedup`,
  en los términos de `dedup.resolver`: documento, correo, nombre con fecha de
  nacimiento. Una clave parcial cuenta y se informa con su cobertura («300 de
  1131 filas»); una marcada sin ningún valor no, y el mensaje la nombra. La
  misma función decide el aviso de la revisión y la constancia de la ingesta.
- **Dos campos y no uno:** `grave` es cómo se muestra, `bloquea` es si frena.
  Ninguna advertencia del resumen frena. Lo que frena lo rechaza la ruta
  (`DatosInvalidos`) y su motivo se escribe **junto al botón**.
- **Sin clave se puede continuar**, con dos salidas que dicen su nombre
  —«Volver a corregir el mapeo» y «Continuar igual, sin clave de dedup»— y la
  decisión queda en el plan del trabajo (`plan.sin_clave_de_dedup`), que se
  guarda con la carga (D56) y vuelve con el estado: al retomar la carga otro
  día, la constancia sigue ahí.
- **El pie del modal se esconde mientras se revisa**, y toda salida temprana
  de «Ingestar» escribe su motivo. Lo que revienta sin estar previsto también
  se muestra.

**El inventario.** `resumen_ingesta.VALIDACIONES` lista cada validación del
paso con su clase y su motivo, y una prueba falla si el resumen emite una que
no está:

| Bloquea | Por qué |
|---|---|
| Crear personas sin evidencia de consentimiento | No hay base legal para el alta |
| La variable de consentimiento no está en el archivo | Ninguna fila evidenciaría nada |
| Ningún campo de identidad (documento, correo, nombre) | No hay a quién dar de alta |
| Un sí/no marcado como documento | Fusiona la base entera: es un error de marcado |

| Advierte | Por qué |
|---|---|
| Sin clave de dedup | Duplicados futuros; hay cargas legítimas sin clave |
| Clave sospechosa (muchas filas, pocos valores) | Suele ser un marcado equivocado, pero un padrón con repetidos existe |
| Pregunta sin texto | Queda fuera de las búsquedas, no rompe nada |
| Valores sin mapear | Un 99 de «no contesta» sin categoría puede estar bien |
| Lo que rompe el texto embebido (Fase 8) | Degrada la búsqueda; la PII en texto libre es un indicio |

**La regla para lo que venga.** Bloquear solo cuando falta algo que el
sistema no puede suplir con una decisión del usuario; advertir cuando falta
algo cuyo costo el usuario puede asumir a conciencia. Y una advertencia
siempre dice la consecuencia y deja seguir.

**Lo que se descartó.**

- *No diagnosticar las demográficas en el servidor.* El análisis del `.sav`
  sugiere el rol, pero el analista lo cambia después; filtrar en el servidor
  dejaría sin hallazgos a una variable que se desmarca.
- *Un botón «Ignorar» en el aviso.* Habría sido una tercera salida para lo
  mismo que «Continuar igual», y sin dejar constancia.

**Dónde vive.** `functions/panel_api/resumen_ingesta.py` (`claves_de_dedup`,
`sin_clave_de_dedup`, `VALIDACIONES`), `functions/panel_api/ruteo.py`
(`_con_constancia_de_dedup`), `functions/panel_api/diferida.py` (`estado`),
`web/public/js/calidad.js` (`soloSemanticas`),
`web/public/js/paginas/encuestas.js`,
`functions/tests/test_bug_dedup_no_bloquea.py`.

---

<a id="d64"></a>
## D64 · El celular es clave de dedup, pero más cauta que el correo

**El problema.** El dedup de R1.2 reconocía a una persona por documento,
correo o —en revisión— nombre con fecha de nacimiento. Muchas bases de campo
traen solo nombre y celular, y cada carga de esa gente volvía a crearla.

**Lo que se decidió.** El celular entra como tercera clave, después del
documento y del correo y antes de nombre + fecha de nacimiento. Pero **no**
como el correo: el documento y el correo son de una persona, y la base lo
garantiza con un índice único; un celular no siempre —el de un hogar, el que
usa un padre mayor y es del hijo, el que la compañía reasignó—. Por eso el
paso tiene tres cautelas, y ante la duda decide una persona:

| Situación | Qué hace |
|---|---|
| Una sola persona tiene ese celular, nada la contradice y el nombre es compatible | **Reutiliza** (`motivo = celular`) |
| La titular tiene otro documento u otro correo que el alta | Son dos personas que comparten el número: el celular **no decide** y el dedup sigue |
| La única titular se llama de otra forma | **Revisión** (`celular_otro_nombre`) |
| Varias personas lo tienen | **Revisión** (`celular_compartido`), con todas las compatibles como candidatas |

- **Se compara en E.164**, con `preferencias.normalizar_celular`, que es como
  se guarda desde R4.4. «099 123 456» y «+59899123456» son el mismo número;
  lo que no se puede normalizar no se compara con nada.
- **«Nombre compatible»** es que las palabras de uno —sin tildes ni
  mayúsculas— estén en el otro: «Ana Pérez» y «Ana Pérez Silva» son la
  misma persona escrita en dos archivos. Sin nombre de alguno de los dos no
  hay con qué dudar.
- **Un celular de relleno se frena**, como un documento implausible: si la
  columna marcada trae menos de tres números distintos en diez filas o más,
  crear esas personas las fusionaría (la primera fila crea, las demás la
  encuentran). Es `celular_implausible` en `VALIDACIONES`: bloquea.
- **La revisión de la importación lo cuenta igual que el dedup**:
  `CLAVES_SIMPLES` suma el celular, y los valores se comparan normalizados.
- **Índice no único** en `persona (celular)` (`boveda/0022`). El alta por
  archivo resuelve el dedup fila por fila dentro de la request, y sin índice
  cada fila recorrería `persona` entera. Único habría convertido un celular
  compartido en un error de inserción.

**Lo que se descartó.**

- *Celular igual que el correo* (reutilizar ante cualquier coincidencia).
  Fusionar a dos personas mezcla datos y consentimientos, y deshacerlo es
  mucho más difícil que resolver una revisión.
- *Celular solo como pista*, como en la aprobación de inscripciones. No
  resolvía el problema: la gente que solo trae celular se seguiría
  duplicando en cada carga.
- *Normalizar en SQL los celulares viejos.* La normalización vive en Python
  (país por defecto, prefijo `00`); duplicarla en una migración sería un
  segundo criterio. Los celulares guardados antes de R4.4 en otro formato no
  coinciden con nada —lo seguro— y el despliegue trae una consulta para
  contarlos.

**Lo que no cambia.** Para **crear** una persona sigue haciendo falta
documento, correo o nombre: el celular reconoce a alguien que ya está, pero
no alcanza solo para dar de alta a un desconocido. Y el correo sigue
reutilizando sin mirar el nombre, como siempre.

**Dónde vive.** `functions/panel_api/dedup.py` (`_por_celular`),
`functions/panel_api/resumen_ingesta.py`, `functions/panel_api/sav.py`
(`_controlar_celular_plausible`), `db/boveda/0022_celular_clave_de_dedup.sql`,
`web/public/js/paginas/revisiones.js`,
`functions/tests/test_celular_clave_de_dedup.py`.

---

<a id="d65"></a>
## D65 · El correo sale por Workspace, y sin proveedor no hay atajo

**El problema.** `VERIFICACION_ENVIO_PROVEEDOR` admitía `ninguno` y `log`, y
ninguno de los dos mandaba nada: el portal decía «vas a recibir un enlace» y
no salía. Peor: sin proveedor, el código de la landing y el enlace para crear
la contraseña **volvían en la respuesta** y el sitio público los mostraba
como «Modo desarrollo». Cualquiera que escribiera el correo de un panelista
obtenía el enlace para crear su contraseña y entraba a su cuenta.

**La decisión.**

- **Proveedor `workspace`**: SMTP de Google Workspace (`smtp.gmail.com`, 587
  con STARTTLS o 465 con SSL) autenticando como
  `notificaciones@equipos.com.uy` con una **contraseña de aplicación** en
  Secret Manager (`SMTP_PASSWORD`). Remitente visible
  `Equipos Consultores <notificaciones@…>`, `Reply-To` configurable. Una
  plantilla por tipo de correo, en un solo lugar.
- **Dos condiciones que no se colapsan.** La *ausencia de proveedor* es un
  error de configuración (`EnvioNoConfigurado`, 503) que se decide **antes**
  de mirar si el correo existe, así que contesta igual para cualquier
  dirección. El *modo desarrollo* es una señal explícita
  (`ENVIO_MODO_DESARROLLO`) que además **se ignora en el entorno desplegado**
  (`K_SERVICE` presente, salvo el emulador). Y ninguna página pública lee
  `enlace_sin_enviar` ni `codigo_sin_enviar`: aunque el servidor los
  devolviera, no hay dónde mostrarlos.
- **Un fallo no se reporta como éxito.** Reintento acotado por tiempo
  (`SMTP_PRESUPUESTO_S`, 15 s) solo para lo transitorio; credencial o
  destinatario rechazados no se reintentan. El fallo queda en `envio_correo`
  con su motivo —confirmado aunque la request termine en error— y al usuario
  se le dice que vuelva a intentar (`EnvioFallido`, 503), sin el motivo
  técnico.
- **Registro de envíos en la bóveda** (`envio_correo`, `boveda/0023`): tipo,
  destinatario, estado, motivo, intentos. Nunca el contenido —un enlace
  guardado es una credencial guardada—. Cuenta los envíos contra el tope de
  2.000 diarios de Workspace. Se purga a los 90 días y una baja lo borra.
- **Cumplimiento → Contacto** muestra el proveedor, el remitente, si la
  contraseña está cargada (no cuál es), los envíos de 24 h contra el tope, un
  **correo de prueba** (permiso `cumplimiento`) y los envíos fallidos.
- **Los usuarios internos** (R2.12) siguen recibiendo su enlace en el modal
  —es de Firebase, no de este mecanismo—, y con proveedor configurado además
  les llega por correo. Ese envío no puede hacer fallar el alta.

**El intercambio que se aceptó.** Si el SMTP falla al pedir el enlace del
portal, la respuesta es un error para un correo del panel y el mensaje de
siempre para uno que no lo es. Mientras dura la caída, eso distingue
direcciones. Se aceptó porque la alternativa —contestar «revisá tu correo»
cuando el correo no salió— le miente justo a quien sí lo espera, y el acceso
del panelista depende de ese correo. La exposición la acotan los mismos
límites de tasa por origen y por correo de R6.1.a, y una caída se ve en
Cumplimiento.

**Lo que se descartó.**

- *OAuth2 contra Gmail API.* Más trabajo y una credencial que rota sola; la
  contraseña de aplicación alcanza mientras la organización la permita.
- *Un servicio transaccional* (SendGrid, Mailgun). ~US$ 15–20/mes; se evalúa
  solo si el volumen pasa el tope de Workspace.
- *Dejar el modo desarrollo gobernado por la ausencia de proveedor.* Es
  justamente el agujero: un despliegue sin el secreto cargado lo reabría.
- *SMS por el mismo proveedor.* Workspace solo manda correo: la verificación
  de celular sigue sin proveedor y lo dice (`EnvioNoConfigurado`).

**Dónde vive.** `functions/panel_api/correo.py`,
`functions/panel_api/verificacion_contacto.py` (`proveedor_de_envio`,
`modo_desarrollo`, `enviar_y_registrar`), `functions/panel_api/portal.py`,
`functions/panel_api/usuarios.py` (`_mandar_acceso`),
`db/boveda/0023_envio_correo.sql`, `web/public/js/paginas/cumplimiento.js`,
`functions/tests/test_envio_correo.py`.

**Cómo se verifica.** `test_envio_correo.py`: el modo desarrollo no aparece
en las páginas públicas; sin proveedor el portal informa y no ofrece el
atajo, para un correo que existe y para uno que no; en el entorno desplegado
la señal se ignora; un fallo no reporta éxito y queda registrado; la
contraseña no está en el repositorio.

---

<a id="d66"></a>
## D66 · La verificación va por lotes, y lo que no se juzgó queda «sin verificar»

**El problema.** La verificación mandaba todos los candidatos en **una**
llamada. Con 100 evidencias Claude agotaba la salida
(`stop_reason=max_tokens`) antes de cerrar la herramienta, y subir
`max_tokens` solo corre la pared. El modo de falla peor era silencioso: una
respuesta truncada con **algunos** veredictos se aceptaba, el resto se
completaba como `dudoso`, y un candidato sin verificar era indistinguible de
uno que Claude evaluó y no pudo decidir.

**La decisión.**

- **Lotes de tamaño configurable** (`VERIFICACION_LOTE`, 25), con índices
  locales al lote en el prompt; `verificar_por_lotes` traduce a índices
  globales al combinar y garantiza longitud y orden de la entrada.
- **`stop_reason` se mira antes de aceptar nada.** Un lote truncado se
  descarta entero y se parte en dos; lo mismo sin herramienta o con una
  respuesta ilegible. Un error transitorio (429, 5xx, red) se reintenta una
  vez. Profundidad máxima de subdivisión y un **presupuesto de tiempo por
  consulta** (`VERIFICACION_PRESUPUESTO_S`, 60 s, compartido entre
  criterios) acotan todo; lo que no entra queda sin verificar.
- **`sin_verificar` es un estado propio**, con su modo de falla (`fallo`:
  truncamiento, herramienta ausente, respuesta inválida, error HTTP, error de
  red, omitido, presupuesto agotado, sin proveedor). `dudoso` vuelve a
  significar solo «evaluó y no pudo decidir», y por eso su regla en modo
  estricto **ya no se apaga** cuando la verificación degrada: la
  `verificacion_aplicada` que lo hacía desapareció.
- **En `consultas.py`**, `sin_verificar` no excluye ni aprueba; un
  `no_cumple` válido sigue excluyendo aunque otras evidencias de la persona
  hayan fallado; la persona con pendientes queda con
  `verificacion_incompleta` y confianza baja; la respuesta trae
  `verificacion.completa` y una degradación `parcial`. La pantalla lo pone
  arriba del ranking.
- **Concurrencia acotada** entre lotes (`VERIFICACION_CONCURRENCIA`, 4): con
  `top_k` 100 de COLOQUIO pueden ser 300 evidencias y doce lotes, y en serie
  no entran en sus 90 segundos.
- **Diagnóstico sin contenido** (R-VER.9): una línea por verificación en el
  log con lotes, verificadas, pendientes, subdivisiones, duración,
  `stop_reason` y tokens.
- **Modo de depuración** (R-VER.10, `VERIFICACION_DEPURACION`): la
  solicitud exacta y la respuesta tal cual, por llamada, en
  `verificacion_captura` del **store semántico** (`semantica/0008`). Apagado
  por defecto, vence a los 7 días, topes por entrada y por ejecución con
  constancia del recorte, lectura solo admin (`depurar_verificacion`). Al
  costado del payload guarda `respuesta_ids` —no adentro— para que una baja
  borre las capturas que llevan respuestas de esa persona.

**La contradicción con R-VER.9, resuelta.** El diagnóstico de rutina no
lleva contenido; la captura lleva exactamente las respuestas de encuesta. No
se mezclan: la captura no es un log —no va a Cloud Logging, que sería una
tercera copia con reglas que nadie definió— sino una tabla en el store donde
esos datos ya viven, con su alcance de baja.

**Lo que se descartó.**

- *Subir `max_tokens` otra vez.* Posterga, no resuelve.
- *Reducir `top_k` o las evidencias por persona para que entre.* Cambia el
  resultado de la consulta en silencio.
- *Aceptar los veredictos parciales de un lote truncado.* Es el bug.
- *Reintentar solo los omitidos de un lote exitoso.* Se marcan `omitido` y
  se informan; si fueran frecuentes, es un problema del prompt.

**Dónde vive.** `functions/panel_api/verificacion.py`
(`verificar_por_lotes`, `Claude._interpretar`, `Captura`),
`functions/panel_api/consultas.py` (`_combinar`, `_resumen_verificacion`),
`db/semantica/0008_captura_verificacion.sql`,
`web/public/js/paginas/consultas.js`,
`functions/tests/test_verificacion_por_lotes.py`.

---

## Anexo · Decisiones que no se tomaron

Cosas que quedaron abiertas a propósito, para que no se confundan con olvidos:

| Tema | Estado | Dónde está anotado |
|---|---|---|
| Rechazar `contacto_para_convocatoria` sin `p_actor` | **Abierta (P2).** Hoy el actor es opcional y la fila sin él se distingue por `actor_email` nulo; el chequeo «el contacto sin actor queda marcado» lo hace visible. Rechazarla es un cambio de contrato con COLOQUIO y de migración | [D62](#d62) |
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
| Revocar una convocatoria declarada antes de que venza | **No se hizo.** Una sesión que se cancela deja la declaración viva hasta su vencimiento. Se puede agregar como función de la superficie si aparece el caso; hoy el tope de 60 días y la purga acotan la exposición | [D50](#d50) |
| Quién llama a `purgar_convocatorias_externas()` y cada cuánto | Pendiente: la función existe y es idempotente, pero todavía no está enganchada a ninguna rutina ni pantalla | [D50](#d50) |
| Mapear a categorías desde la pantalla de atributos, no solo al cargar | **No se hizo.** Corregir un mapeo ya cargado se hace por API (`POST /atributos/{id}/recalcular` con `mapeo`); no hay pantalla para eso todavía | [D51](#d51) |
| Mapear una variable con más de 60 valores distintos | **Deliberado:** no se ofrece. Una variable así no es un segmentador, y media lista de desplegables invita a mapear la mitad y creer que está completo | [D51](#d51) |
| Verificación del celular por SMS en el portal | **Pendiente.** El mecanismo de R4.3 ya existe y el portal lo usa; falta contratar el proveedor de SMS. Hasta entonces, un celular nuevo no habilita WhatsApp | [D52](#d52) |
| Que el panelista vea a qué estudios fue convocado | **No se hace, y no es un olvido.** Ver la muestra es información sobre el diseño del estudio, no sobre la persona | `SPEC_fase6.md` §3 |
| Que el panelista vea sus respuestas anteriores | **Descartado:** contamina la investigación —ver lo que respondió antes condiciona lo que responde ahora— y complica lo prometido sobre confidencialidad | `SPEC_fase6.md` §3 |
| Auditar los cambios de atributos editables | **Anotado, no hecho.** Se evaluó el gameo y se consideró poco probable; si algún atributo pasa a definir cuotas o premios, conviene auditarlo o limitar su frecuencia | [D52](#d52) |
| Segundo factor en el portal | **Descartado para esta versión, y anotado.** Las contraseñas traen lo que el enlace evitaba: gente que reutiliza la misma de otros servicios, y un objetivo que robar. Si el portal llega a exponer más datos, vale reevaluarlo | [D53](#d53) |
| Protección contra enumeración de Firebase Auth | **Pendiente de activar en la consola.** No es la defensa —el mensaje genérico lo escribe el servidor— pero sí defensa en profundidad para quien llame a Identity Toolkit por fuera del portal | [D53](#d53), `DESPLIEGUE - R6.1.a` §3 |
| Panelistas cuyo correo registrado está desactualizado | **Abierto, y es operativo.** No van a poder recibir el enlace y quedan sin acceso. Conviene detectarlos **antes** de anunciar el portal; hoy la salida es que un responsable los contacte por otra vía y les corrija el correo desde la ficha | `SPEC_R6.1a` §9 |
| Pantalla para que un responsable vea quién no activó nunca su contraseña | **No se hizo.** El dato está (`v_acceso_portal` y `cuenta_panelista`), pero no hay una lista de «invitados que no entraron»; hoy se ve persona por persona desde la ficha | [D53](#d53) |
| Si 512 dimensiones alcanzan para este corpus | **Abierta, y es la pregunta de fondo.** Los benchmarks de Voyage dicen que se pierde poco, pero no son sobre respuestas de encuesta en español rioplatense. Se decide con la validación del paso 7, sobre 5.000–10.000 respuestas y antes de cargar 200.000 | [D54](#d54) |
| Cuantización int8 o binaria de los vectores | **No se evaluó.** `voyage-3.5` la soporta y bajaría otro tanto la memoria, pero son dos variables a la vez: primero hay que saber qué cuesta bajar la dimensión | [D54](#d54) |
| Línea de base de tiempos por etapa de una consulta | **Pendiente, y conviene antes de la carga masiva.** Sin ella no se va a poder decir dentro de seis meses si el sistema se puso lento ni por qué | `DESPLIEGUE - 512 dimensiones` §7 |
| Quién llama a `purgar_ingestas_terminadas()` y cada cuánto | **Pendiente, y es el mismo pendiente que el de las convocatorias externas.** La función existe, es idempotente y borra solo las filas despivotadas de trabajos terminados hace más de N días —conservando las de los lotes fallidos, que son las que harían falta para reintentarlos—; todavía no está enganchada a ninguna rutina. Mientras tanto, el archivo de cada carga queda en la bóveda | [D55](#d55) |
| Avisar cuando una carga termina con errores | **No se hizo.** Con el proceso en diferido el analista puede irse y no enterarse. Hoy se entera al volver a la pantalla; un correo o un aviso en la app queda anotado | [D55](#d55) |
| Limitar a una carga grande por vez | **No se limita.** Dos cargas simultáneas compiten por el proveedor de embeddings y por la base. El default conservador de 3 tareas en paralelo acota el daño, pero nada impide que sean seis | [D55](#d55) |
| Paralelizar las llamadas al proveedor dentro de un lote | **Postergado a propósito.** Es complementario, no alternativo: reduce el tiempo de cada tarea además de repartirlas. Conviene medir con Cloud Tasks andando antes de decidir si hace falta | [D55](#d55) |
| Un trabajo de ingesta que queda a medias para siempre | **Sin política.** Si una tarea nunca llega a correr, el trabajo queda `procesando` indefinidamente: nadie lo marca fallido ni lo limpia. La purga solo toca los terminados | [D55](#d55) |
| Que el resumen consolidado incluya el alta de personas | **No se hizo así.** El alta pasa en la ruta, antes de encolar, y su resultado viaja en la respuesta inmediata; la pantalla lo arrastra hasta el resumen final. Al retomar una carga desde otra pestaña no se tiene, y entonces no se muestra: ya pasó, y las personas están en Panelistas | [D56](#d56) |
| Marcar los valores sin mapear a ninguna categoría antes de ejecutar | **Pendiente.** Es el otro hallazgo del mismo plan: mapeos escritos con la etiqueta completa en vez de la clave, que no van a corresponder a ninguna categoría. Se ve recién en el informe posterior | [D56](#d56), `BUG_modo_crear_individuos_no_se_envia.md` §6.2 |
| Mostrar el texto de la pregunta junto al código en la revisión previa | **Pendiente, y es lo que habría evitado el marcado de «documento».** `var138O1320 → documento` no dice nada; «¿has consumido…» → documento salta a la vista | [D56](#d56) |
| Si las columnas elegidas entran en el CSV seudonimizado | **No entran, y es la propuesta de la spec.** Un CSV con sexo, localidad y tramo etario de 200 personas es bastante más identificable que uno con tokens. La selección afecta solo la vista | [D57](#d57), `SPEC_fase7` §R7.4 |
| Qué atributos se ofrecen por defecto como columna | **Sin decidir.** Hoy se ofrecen todos los no especiales y ninguno viene elegido. Conviene mirarlo cuando el panel crezca: la combinación de tres demográficos ya señala a una persona en un panel chico | [D57](#d57) |
| Saltear el paso de revisión en cargas chicas | **No se hace.** Agrega un clic a una operación que ya tiene varios, y si molesta se puede evaluar un umbral de filas — pero **nunca** en el modo «crear los individuos», que es el irreversible | [D57](#d57) |
| Cachear los conteos de la pantalla de estadísticas | **No hizo falta todavía.** Se resuelven en pocas consultas agregadas, pero un `count(*)` sobre `respuesta` crece con el corpus. Conviene medirlo cuando haya 200.000 respuestas | [D57](#d57), `SPEC_fase7` §7 |
| Acotar por rol quién puede ver las respuestas de un panelista | **Abierto.** Hoy alcanza el permiso `leer`, el mismo de la lista de resultados, y queda registrado. Si se decide acotarlo, el registro ya permite ver quién lo usaba | [D57](#d57) |
| Que COLOQUIO adapte sus consultas a `v_persona_convocable` de una fila por persona | **Pendiente, y es de otro repositorio.** `finalidad = 'x'` pasa a `'x' = any(finalidades)` y lo de ámbito estudio va a `v_persona_finalidad_vigente`. Falla ruidosamente hasta que se haga | [D58](#d58) |
| Que `v_fatiga_panelista` aplique el gate de consentimiento | **Abierto.** Expone hechos de fatiga de todos los miembros activos, también de quien no consintió el contacto. No es PII, pero sí un `id_persona` fuera del gate; conviene filtrarla o documentar por qué no | [D58](#d58) |
| Pantalla de configuración global de los valores de no respuesta | **No se hizo.** La lista es editable por carga y se guarda con el cuestionario; el default vive en `calidad_dato.VALORES_NO_RESPUESTA` | [D60](#d60) |
| Conjugar la pregunta propuesta («¿has consumido?» → «¿consumió?») | **Descartado a propósito.** La propuesta integra, limpia y baja énfasis; no reescribe | [D60](#d60) |
| Recuperar en un reproceso lo que la ingesta descartó | **Imposible sin el archivo, y deliberado.** Lo no marcado y lo excluido no se guardan; la revisión lo avisa | [D61](#d61) |
| Que el modo demo cubra las pantallas de las fases 7 y 8 | **Pendiente.** `demo.js` no simula la ficha seudónima, las respuestas, las estadísticas, la calidad del dato ni el reproceso; por eso las capturas de esas secciones del manual siguen pendientes | `docs/manual/README.md` |
| Alinear el voseo de la interfaz con el registro formal del manual | Sin decidir; requeriría recapturar las 44 pantallas | PR de la Fase 2 |
| Verificación del celular cuando el proveedor es Workspace | **Pendiente.** Workspace solo envía correo; un celular en la landing o en el portal no se puede verificar y el sistema lo dice. Falta un proveedor de SMS (o una plantilla de autenticación de WhatsApp) | [D65](#d65) |
| OAuth2 o un servicio transaccional para el correo | **Solo si hace falta.** Si la organización deja de permitir contraseñas de aplicación, o el volumen pasa los 2.000 destinatarios por día | [D65](#d65) |
| Una rutina programada que purgue `verificacion_captura` | **No se hizo.** La purga corre en cada escritura y cada lectura; si el modo queda apagado meses, las filas vencidas esperan a la próxima consulta con el modo encendido o a la purga manual del manual de despliegue | [D66](#d66) |
| Calibrar `VERIFICACION_LOTE` con datos reales | **Pendiente.** 25 es un punto de partida: más chico trunca menos y repite más prompt; más grande ahorra prompt y arriesga el doble pago de la subdivisión | [D66](#d66) |

## D67 · De qué carga viene cada persona: un vínculo, no un campo; y una carga no tiene objetivo

**El problema.** `carga` tenía nombre y `ref_estudio`, pero nada la
relacionaba con sus personas. Sin eso no se podía calcular la composición de
una carga ni decirle a un panelista de qué estudio salió. El cruce por
`ref_estudio` no alcanza: lleva a las respuestas del lado semántico, no a las
personas, y no distingue a quien nació en la carga de quien ya existía.

**La decisión.**

- **`persona_carga`, de muchos a muchos** (`boveda/0024`), con `origen`
  `creada` | `reutilizada` y clave `(id_persona, carga_id)`. Un campo en
  `persona` obligaría a elegir entre pisar el primer origen o ignorar los
  siguientes. `on delete cascade` desde `persona`: la baja lo arrastra como
  al resto.
- **El vínculo se fija la primera vez y no se pisa** (`on conflict do
  nothing`). La ruta registra `creada`/`reutilizada` al dar de alta (antes de
  encolar) y cada lote registra `reutilizada` sobre todo lo que resolvió; así
  el orden y los reintentos no cambian el resultado. Quien pasa por revisión
  se vincula al resolverla (`carga_id` viaja en `alta_en_revision.datos`).
- **Los datos del estudio viven en la carga** (`fecha_estudio`,
  `publico_objetivo`) y la ficha los lee por join. Copiarlos en cada persona
  haría que corregir una fecha toque 1.131 filas y que una persona de tres
  estudios muestre uno solo.
- **El público objetivo es texto libre** (R-ORG.6): documenta procedencia,
  no segmenta. Si un día hace falta filtrar por él, se evalúa un vocabulario
  con el criterio del catálogo de atributos.
- **La encuesta no lleva vínculo propio**: `participacion` ya cumple ese
  papel para la ingesta desde encuesta.
- **No hay reconstrucción retroactiva.** `alias_origen` guarda la plataforma,
  no la carga, y dos cargas de la misma plataforma son indistinguibles. Las
  personas anteriores quedan sin vínculo, y la ficha lo dice en vez de
  mostrar «sin origen».
- **Ámbitos de composición:** `todos` (la bóveda entera, también lo cargado
  sin panel), `panel` (el de siempre, mismo SQL) y `carga`. **Objetivo para
  `todos` sí, para una carga no**: la representatividad del conjunto es una
  pregunta legítima y recurrente; una carga es un hecho del pasado que no se
  corrige reclutando. `objetivo_composicion` gana `ambito` y `panel_id`
  nulo para `todos`, con un índice único parcial (con `panel_id` nulo el
  `unique` de la 0001 no protege). La pantalla advierte que el objetivo de
  «todos» es otro universo que el de un panel, y que la composición de una
  carga mira el presente (quien se dio de baja ya no aparece).
- **La composición a fecha (R4.1.a) queda solo por panel**: sin membresía
  que fechar, mezclar atributos de entonces con personas de hoy no
  corresponde a ningún momento.

## D68 · La web API key no es un secreto, y «no se pudo comprobar» no es «no entró»

**El problema.** El portal rechazaba toda contraseña. El secreto
`FIREBASE_WEB_API_KEY` tenía el placeholder `AIza...` porque Firebase
**reserva el prefijo `FIREBASE_`** —para secretos y también para variables de
`.env` (en `firebase-tools`, archivo lib/functions/env.js, `RESERVED_PREFIXES`)—, así que el
valor real no se pudo cargar nunca. Identity Toolkit contestaba 400 «API key
not valid», el código lo convertía en `None` sin log y el panelista leía
«contraseña incorrecta». Diagnosticarlo llevó más de una hora.

**La decisión.**

- **Variable de entorno común, `WEB_API_KEY`**, en `functions/.env`. Es
  pública por diseño (está en `index.html`); en Secret Manager solo agregaba
  un valor que nadie podía ver en un `describe`. **El nombre cambia** aunque
  el pedido proponía conservarlo: con el prefijo `FIREBASE_` tampoco la
  acepta el `.env`. No se lee el nombre viejo como alternativa: haría
  parecer vigente un valor muerto. `test_main.py` falla si cualquier
  variable usa un prefijo reservado.
- **Un placeholder se frena antes de llamar**: la key tiene que tener la
  forma `AIza` + 35 caracteres.
- **Al usuario un mensaje, al log el motivo.** `verificar_clave` registra el
  estado y el cuerpo de Identity Toolkit (con la key reemplazada) y el
  correo; nunca la contraseña ni la key. También los errores de red, que antes
  ni se capturaban.
- **Dos resultados distintos.** Un 400 por motivo de credencial
  (`INVALID_LOGIN_CREDENTIALS`, `INVALID_PASSWORD`, `EMAIL_NOT_FOUND`,
  `USER_DISABLED`…) es «no entró» → `None` y el mensaje genérico de siempre.
  Un 403, 429, 5xx, timeout, error de red, respuesta ilegible, key ausente o
  un **400 por key inválida** es «no se pudo comprobar» →
  `ComprobacionNoDisponible` (503), con `MENSAJE_COMPROBACION_NO_DISPONIBLE`,
  y **no cuenta para el límite de intentos**: una caída no bloquea a nadie una
  hora. El 400 de la key se clasifica como técnico a propósito: es el caso del
  incidente, y tratarlo como credencial es exactamente el error que el pedido
  corrige. Un 400 de motivo desconocido sigue siendo «no entró».
- **Lo irreversible sigue fallando cerrado**: si no se puede comprobar, la
  baja o el cambio de correo no ocurren; solo cambia que no suman intento.
- **A2 — el mismo patrón en otros `except`.** `credenciales.buscar` y
  `usuarios.buscar_por_email` devolvían `None` ante **cualquier** excepción:
  Firebase caído se leía como «la cuenta no existe». Ahora solo
  `UserNotFoundError` es `None`; lo demás se loguea y sube.
  `link_de_reseteo` conserva el `None` pero loguea el motivo.

## D69 · El resultado demográfico es seudónimo y tiene las mismas acciones

**El problema.** `pintarResultado` salía por un `return` temprano para la
consulta demográfica, así que ficha, columnas, CSV, reidentificar, CSV con
datos y crear panel solo existían para la semántica, que es el camino menos
transitado. Además el resultado demográfico traía **nombre y correo** y la
pantalla los mostraba sin registrar ninguna reidentificación.

**La decisión.** La barra de acciones es un bloque común
(`barraDeAcciones` + `engancharAcciones`) que usan los dos tipos; cambia la
tabla. El resultado demográfico se **seudonimiza en `consultas.ejecutar`**
(sin nombre, correo, documento ni celular), y ver quiénes son pasa por la
misma reidentificación registrada. `a_csv` deja vacío el puntaje en vez de
fallar o inventar un cero. Las diferencias legítimas se conservan: sin
puntaje, evidencia, veredicto, degradaciones ni diagnóstico del puente, y el
orden es el del listado. Los endpoints ya aceptaban cualquier conjunto de
`id_persona`; no hizo falta tocarlos.

---

## D70 · Lo que se verifica son unidades de evidencia, «irrelevante» no es «no cumple», y «completo» tiene precio y presupuesto

**El problema.** Una consulta por «usa un celular Xiaomi» devolvió como
resultado a personas cuya única evidencia era «soy el titular del
contrato»: 25 personas con la misma respuesta ocuparon los 25 lugares de
`top_k`, Claude leyó 25 veces el mismo texto, y la respuesta que importaba
—la marca, mal escrita— nunca llegó a verificarse. La solicitud de cambio
proponía cinco cambios; el addendum
(`specs/ADDENDUM_solicitud_consultas_semanticas.md`) pidió antes medir (A1),
cuantificar el costo (A2), no duplicar contratos existentes (A3), arreglar
el desajuste con COLOQUIO (A4) y exponer `limite` (A6).

**La decisión.**

- **Cambio 1 · el criterio se embebe como consulta** (`input_type=query`);
  las respuestas siguen como documento y no se re-embebe nada. No es neutral
  (A5): se vuelve atrás sin redesplegar con `EMBEDDINGS_TIPO_CONSULTA=document`
  (una revisión nueva de la función, no un deploy) o por consulta con
  `tipo_embedding_criterio`, y el tipo efectivo queda en el diagnóstico.
  `scripts/diagnosticar_consulta.py input-type` mide antes y después.
- **Cambio 2 · `alcance` es otro eje que `modo`.** `exploratorio` (las
  `top_k` mejores, en la request) o `completo` (todas las unidades
  elegibles, en diferido). Una completa puede ser laxa.
- **Cambio 3 · unidades de evidencia.** Una unidad es un texto distinto
  dentro de una pregunta, `(pregunta_id, hash_texto)`. Se rerankea y se
  verifica **una vez** y el veredicto vale para todas las respuestas que la
  comparten. La exploratoria **rellena**: si las personas verificadas solo
  tienen evidencia irrelevante, va a buscar a las siguientes (hasta 3 rondas
  que llamen al verificador, sin pasar de `top_k × 3` unidades, el mismo tope
  de evidencias que había). Las tandas cuyas unidades ya se juzgaron son
  gratis y no cuentan como ronda. El catálogo es una **vista**
  (`v_unidad_evidencia`, semantica/0009), no una tabla con texto: una baja
  la achica sola. La 0009 completa `hash_texto` con el mismo sha256 que
  Python, y una prueba los compara.
- **Cambio 4 · `irrelevante`.** Un veredicto nuevo del verificador: la
  respuesta no habla del criterio. Para la combinación es ausencia de
  evidencia (afuera en estricto o duro, penalizada en laxo), y en laxo una
  persona **sin ninguna** evidencia pertinente no aparece
  (`sin_evidencia_pertinente`): antes no podía pasar —sin hallazgo no se
  entraba al universo— y mostrarla sería el falso resultado. Irrelevante
  **corrige la interpretación, no el recall**: los lugares los recupera el
  cambio 3.
- **R4.2 · una sola regla de agregación** (`consultas.hallazgo_de`), que
  usan la exploratoria y la completa: `no_cumple` > `cumple` > `dudoso` >
  `sin_verificar` > `irrelevante`. Un `cumple` y un `no_cumple` de la misma
  persona: manda el `no_cumple` y se marca `contradiccion` (A5).
- **Estado por persona** (`confirmada`, `posible`, `pendiente`;
  `descartada` en excluidos) y `version_contrato: 2`. **A4**: `item.detalle`
  va con el mismo contenido que `item.criterios`, porque COLOQUIO lee
  `detalle`; y un cliente que no encuentre un campo nuevo lo lee como «no se
  sabe», nunca como un valor favorable.
- **Cambio 5 · la completa reutiliza la ingesta diferida** (A3): plan
  congelado al confirmar, lotes en Cloud Tasks (`procesarconsulta`), avance
  derivado de los lotes (`v_consulta_progreso`), reintento idempotente
  (upsert por ejecución, criterio y unidad), gate re-evaluado en cada lote
  **y al leer**. El resultado no se guarda: se arma al leer, paginado.
  Los veredictos viven en el store semántico (`veredicto_unidad`, 30 días),
  porque la razón describe contenido; una baja borra los de las unidades
  que se quedan sin respuestas.
- **A2 · el costo es requisito, no detalle.** Estimación en la pantalla
  antes de confirmar (misma ruta, `solo_estimar`), **presupuesto
  obligatorio** con techo por configuración (`CONSULTA_PRESUPUESTO_MAXIMO_USD`),
  control antes de cada lote sobre la suma de los lotes (no un acumulador),
  y costo real registrado por lote y por ejecución. La estimación se calibra
  sola con los tokens observados. Lanzar una completa es un permiso aparte,
  `consulta_completa` (admin y operaciones): quién gasta es una decisión de
  producto.
- **A1.3 · cada ejecución deja su fila** en `consulta_ejecucion`
  (bóveda/0025): degradaciones, reranker y verificador efectivos,
  `input_type`, etapas y costo; nunca personas ni evidencias. Y un reranker
  que contesta sin puntajes (o con puntajes para una parte) ahora es una
  **degradación declarada**: antes se descartaban los `None` en silencio.
- **A6 · `limite` en Parámetros** («Personas a mostrar»), validado contra
  `top_k` en el formulario, y `recorte` en el resultado: cuántas se
  verificaron frente a cuántas se muestran.

**Lo que se descartó.**

- *Una tabla de unidades con su propio embedding e índice.* Duplicaba los
  vectores (la instancia es chica, D54) y obligaba a sincronizar bajas. La
  vista sobre `respuesta` alcanza: el recall sigue con el índice HNSW y el
  colapso a unidades es en memoria.
- *Guardar el resultado de la completa.* Persistiría una lista de personas
  sin volver a pasar por el gate (P1).
- *Un endpoint aparte para estimar.* Divergiría del que lanza, y lo que
  divergiría es el número que se muestra antes de gastar.
- *Esperar a A1 para implementar.* El addendum lo recomienda para decidir el
  alcance; acá se pidió la solicitud completa. A1 queda como **paso
  obligatorio del despliegue**, antes de habilitar la completa, y con el
  script para correrlo.

**Dónde vive.** `functions/panel_api/consultas.py` (`_resolver_criterio_semantico`,
`hallazgo_de`, `_combinar`, `registrar_ejecucion`),
`functions/panel_api/consulta_completa.py`, `functions/panel_api/costo_consulta.py`,
`functions/panel_api/embeddings.py` (`tipo_de_criterio`, `embeber_criterio`),
`functions/panel_api/verificacion.py` (`IRRELEVANTE`),
`functions/panel_api/semantica.py` (unidades y veredictos),
`db/boveda/0025_consulta_ejecucion.sql`, `db/semantica/0009_unidades_de_evidencia.sql`,
`functions/main.py` (`procesarconsulta`), `web/public/js/paginas/consultas.js`,
`scripts/diagnosticar_consulta.py`, `functions/tests/test_r_cs_consultas.py`.
