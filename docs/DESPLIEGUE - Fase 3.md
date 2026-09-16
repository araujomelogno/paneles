# Despliegue — Fase 3

**Precondición:** Fase 2 desplegada, con su DoD cerrado y funcionando.
**Referencia:** `specs/SPEC_fase3.md`

---

## 0 · Qué cambia

Las Fases 1 y 2 dejaron el sistema **capaz de observar**. Esta lo deja capaz
de **actuar**: proponer a quién invitar, distinguir dato bueno de malo,
premiar la participación, crecer por una vía pública y sacar fricción del
trabajo diario.

En términos de despliegue, eso se traduce en cinco cosas concretas:

| Qué | Dónde | Riesgo si se saltea |
|---|---|---|
| Tres migraciones nuevas en la bóveda | `db/boveda/0005_fase3.sql`, `0006_participacion_por_importacion.sql` y `0007_carga_sin_panel.sql` | Las pantallas nuevas fallan con `relation … does not exist`; sin la 0006, la ingesta falla con `column "origen" does not exist`; sin la 0007, «Cargar panelistas» falla con `relation "carga" does not exist` |
| Una dependencia nueva en la función | `pyreadstat` | La ingesta SAV responde 400 al primer archivo |
| Una página pública nueva | `/inscribirse` | La landing no existe |
| Dos decisiones legales pendientes | ver §1 | Se abren superficies de datos sin base legal |
| Un cambio de conducta de la ingesta | ver §1.4 | Los paneles crecen solos y conviene saberlo antes, no después |
| Otro, en la pantalla de carga | ver §1.5 | Los demográficos dejan de embeberse; sin avisar, alguien va a pensar que se perdieron variables |
| Una forma nueva de identificar al respondente | ver §1.6 | Sin precargar la muestra, las ingestas de estudios nuevos siguen quedando en cero |
| Una forma de cargar gente **sin panel** | ver §1.7 | Nada se rompe: es una capacidad nueva. Pero si nadie sabe que existe, se siguen metiendo bases externas en paneles que no les corresponden |
| Nada en el store semántico | — | — |

**El store semántico no cambia en esta fase.** No hay migración nueva del lado
de la semántica, y el pipeline de consulta (recuperación → reranker →
verificación) queda exactamente igual.

### Se puede desplegar por partes

Los tres bloques son independientes y se pueden soltar por separado. Si el
sprint se corta, esta es la secuencia con menos deuda:

- **3A — salud del panel** (muestreo, calidad, puntos): no toca ninguna
  superficie pública ni crea personas. Es lo más seguro de soltar primero.
- **3C — fricción operativa** (SAV, exportación, panel desde consulta,
  identificador de campo, carga sin panel): la única definición pendiente es
  la de §1.7, y solo aplica al flujo de carga sin panel. El modo «crear
  individuos» del SAV exige que el archivo evidencie el consentimiento
  (§1.1), y eso es un requisito del cuestionario de campo, no del
  despliegue.
- **3B — crecimiento** (landing): es la única superficie pública del sistema
  y **exige el texto de consentimiento revisado** antes de anunciarla.

---

## 1 · Antes de empezar: las definiciones legales

La spec marcaba tres puntos como bloqueantes (§11). **Uno ya está resuelto en
el código** —la base legal del alta por SAV, §1.1— y de los otros dos, uno
frena habilitar la landing. Conviene revisarlos antes de anunciar nada.

### 1.1 · Base legal del alta por SAV — *resuelta: la evidencia viaja en el archivo*

El alta manual (R1.1) rechaza crear una persona sin consentimiento registrado.
El modo «crear los individuos en esta carga» entraba por otra puerta, y la
spec dejaba abierto con qué base legal.

**Ya no está abierto.** Para crear personas desde un `.sav` hay que declarar,
en el momento de importar, **qué variable del archivo evidencia el
consentimiento y qué valor cuenta como afirmativo**, para las dos finalidades:

| Finalidad | Qué habilita |
|---|---|
| `contacto_participacion` | Pertenecer al panel y ser convocado |
| `uso_semantico` | Que sus respuestas se ingesten al store semántico |

**Pueden apuntar a la misma variable.** Un cuestionario con una sola pregunta
de consentimiento es el caso normal; declararla dos veces es decir
explícitamente que esa pregunta cubre las dos finalidades.

Desde la app: Encuestas → Ingestar respuestas → subí el `.sav` → «¿Los
panelistas ya están en el sistema?» → *No: darlos de alta en esta carga*. Los
desplegables se llenan con las variables del archivo. Por API:

```jsonc
{
  "modo": "crear_individuos",
  "columna_id": "ID",
  "mapeo_patronimico": {"nombre": "NOM", "documento": "DOC", "email": "MAIL"},
  "evidencia_consentimiento": {
    "contacto_participacion": {
      "variable": "CONS1",
      "valor_afirmativo": "1",          // acepta una lista: ["1", "Sí"]
      "version_texto": "consentimiento-campo-2026-09"
    },
    "uso_semantico": {
      "variable": "CONS1",              // puede ser la misma
      "valor_afirmativo": "1",
      "version_texto": "consentimiento-campo-2026-09"
    }
  }
}
```

**Qué pasa con cada fila:**

| Situación | Resultado |
|---|---|
| Evidencia contacto y uso semántico | Se crea con los dos consentimientos |
| Evidencia contacto, no uso semántico | Se crea con uno. Está en el panel, sus respuestas **no** se ingestan |
| No evidencia contacto | **No se crea.** Se informa cuántas filas y con qué valor |
| La persona ya existe | No se duplica; el consentimiento del archivo se le registra igual |

**Qué se rechaza antes de leer una fila:** una declaración sin alguna de las
dos finalidades, sin variable, sin valor afirmativo o sin versión de texto; y
una variable declarada que no existe en el archivo —un typo ahí dejaría cero
altas sin explicar por qué—.

> **Lo que queda es una decisión de campo, no de software.** El cuestionario
> tiene que **incluir la pregunta de consentimiento**. Si un `.sav` viene sin
> ella, no se puede dar de alta a nadie desde ese archivo, y hay que
> enrolarlos por la pantalla de Panelistas o por la landing. Conviene
> acordarlo con el equipo de campo y con el proveedor de la plataforma
> (Dooblo, Alchemer) antes de la primera ola que use esta vía.

La comparación del valor afirmativo no distingue mayúsculas ni espacios: «Sí»,
«SI » y «sí» son la misma respuesta. Rechazar a alguien por eso sería un error
de importación disfrazado de falta de consentimiento.

#### Las personas creadas con la versión anterior

Antes de este cambio, la ingesta creaba a esas personas en estado
`pendiente_consentimiento`. Ya no lo hace: o el archivo prueba la base legal y
la persona nace activa, o no se crea. Si quedaron altas de la versión
anterior, se regularizan una vez y el estado deja de usarse:

```bash
# ¿Queda alguna?
psql "$DSN_BOVEDA" -c "select count(*) from persona
                        where estado = 'pendiente_consentimiento';"
```

```bash
curl -s -X POST https://gestion-paneles.web.app/api/panelistas/regularizar \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"ids_persona": ["…"], "finalidad": "contacto_participacion",
       "version_texto": "consentimiento-2026-09"}'
```

### 1.2 · Texto de consentimiento de la landing — *bloqueante para 3B*

**El repositorio no trae ningún texto legal**, y es a propósito: si la
migración sembrara uno, alguien iba a publicar la landing creyendo que
sirve. Sin un texto publicado, el formulario público responde que no está
disponible y rechaza cualquier envío.

Cuando el DPO tenga el texto, se publica desde la app —Inscripciones →
Textos de consentimiento— o por API:

```bash
curl -s -X POST https://gestion-paneles.web.app/api/textos-consentimiento \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"finalidad": "contacto_participacion", "version": "2026-09",
       "cuerpo": "…el texto revisado…"}'
```

> **Una versión publicada no se puede reescribir.** Es una restricción del
> sistema, no un olvido: lo que alguien consintió tiene que seguir siendo
> recuperable tal cual. Para cambiar el texto se publica una versión nueva, y
> los consentimientos anteriores conservan la suya.

### 1.3 · Tratamiento fiscal del canje — *no bloquea el código*

La spec lo marca bloqueante para R3.5, pero también declara como no-goal
automatizar el tratamiento fiscal. El sistema registra el canje y su ciclo de
estados; la parte fiscal es un proceso de administración, fuera del software.
Lo que sí conviene es **no abrir el canje al panelista** hasta que finanzas
haya definido el tratamiento y exista logística de entrega.

Mientras tanto, el catálogo puede quedar cargado con los premios en `activo:
false`: se ven en la administración y no se pueden canjear.


### 1.4 · La ingesta ahora hace crecer el panel (addendum de R3.9)

No es una definición pendiente: es un cambio de conducta que conviene avisar
antes de desplegarlo, porque mueve números que la gente mira.

**Qué hace ahora.** Al ingestar —cualquier archivo, no solo `.sav`—, todo
individuo cuyas respuestas se escriben queda:

- **dado de alta como miembro del panel de esa encuesta**, si no lo era, y
- **con su participación registrada** (`respondio = true`), creándola si no
  existía.

**Por qué.** Sin eso, quien respondía en campo sin haber sido convocado desde
el sistema quedaba invisible: sin membresía no entraba en «convocar al panel»,
no contaba para la composición ni para la brecha de cuota, y el muestreo no lo
veía; sin participación, la ola mostraba menos respuestas de las que hubo.

**Qué mirar la primera vez.** El resultado de la ingesta informa cinco cifras
nuevas: membresías nuevas, existentes y en baja, y participaciones nuevas y
actualizadas. Si la primera ingesta después de desplegar reporta muchas
membresías nuevas, no es un error: es el atraso que se estaba acumulando.

**Tres cosas que no cambian, y son a propósito:**

| | |
|---|---|
| Una membresía en `baja` **no se reactiva** | Fue una decisión explícita de alguien. Se informa y decide un responsable desde la pantalla del panel |
| `convocar()` **sigue exigiendo** `contacto_participacion` | La membresía no habilita a contactar a nadie. Registrar que alguien respondió es otra cosa que invitarlo |
| Quien no tiene `uso_semantico` vigente **no entra por esta vía** | No se le ingesta nada, así que no hay hecho que registrar |

**Y una que sí cambia sin que se vea:** una participación importada **no
cuenta como convocatoria** para la fatiga. Si contara, esa gente saldría del
muestreo por contactos que nunca ocurrieron. Por eso la columna
`participacion.origen` de la 0006 no es documentación: es lo que hace posible
esa distinción.


### 1.5 · Las variables demográficas ya no se embeben (addendum de R3.9)

Tampoco es una definición pendiente: es lo segundo que cambia de conducta, y
no necesita migración.

**Qué hace ahora.** En la pantalla de carga, cada variable del archivo tiene
una columna nueva que dice **qué es**: pregunta del estudio, o demográfica con
su campo de la bóveda (nombre, documento, correo, celular, sexo, fecha de
nacimiento, localidad, contacto), o demográfica **sin campo** para las que la
bóveda no modela —`EDAD` es el caso: la bóveda guarda fecha de nacimiento y
deriva el tramo—.

Lo marcado como demográfico **no se ingesta al store semántico** —no genera
pregunta, ni respuesta, ni embedding— y su valor va a la ficha del panelista.

**Por qué.** Si el archivo traía `SEXO` o `LOCALIDAD`, hasta acá terminaban
embebidos como «Sexo → Femenino». Eso espeja los segmentadores al store
semántico por la puerta de atrás, que es lo que el diseño descarta: quedan
autoritativos en la bóveda para no sumar cuasi-identificadores del lado que se
quiere mantener limpio. Y no se ganaba nada: el filtro demográfico ya se
resuelve en la bóveda.

**Qué mirar la primera vez.** El sistema **sugiere** el marcado por nombre y
por variable label, pero no lo aplica solo: las filas quedan marcadas a la
vista para confirmar o corregir antes de ingestar. Vale revisarlas: una
variable puede ser segmentador en un estudio y ser el objeto de análisis en
otro, y ninguna heurística resuelve eso.

El resultado de la carga suma tres cifras: variables excluidas por
demográficas, datos completados en la bóveda y discrepancias con la ficha.

**Dos reglas que conviene conocer:**

| | |
|---|---|
| **El archivo no pisa la ficha** | Un campo vacío del panelista se completa con el valor del archivo; uno ya cargado con otro valor se informa y **no se toca**. El archivo de un estudio puede traer un dato viejo o mal tipeado, y una ingesta no es el lugar para cambiar la ficha de alguien |
| **El valor se traduce antes de guardarse** | Un `.sav` guarda `2` y «Femenino» por separado. Se guarda «F», no «2» — si se guardara el código, la composición por sexo quedaría inservible sin que nada falle |

**Y el campo de códigos sigue al tipo.** Deshabilitado en las abiertas —no hay
códigos que traducir—, completo en cerradas y escalas, y en las numéricas
acotado a los valores especiales (`98=No sabe`, `99=No contesta`), que es la
traducción que hace la diferencia entre embeber «→ 99» y «→ No contesta».


### 1.6 · El identificador ahora viaja al campo (R3.12)

Tampoco lleva migración, pero **sí necesita un cambio en cómo trabaja el
equipo de campo**, y es el único de los tres que no funciona solo.

**El problema que resuelve.** El mapeo se apoyaba siempre en `alias_origen`:
el id que la plataforma de campo le puso al respondente. Eso asume que ese id
es estable por persona entre estudios, y no lo es —cada encuesta genera ids
nuevos—. Al ingestar un estudio nuevo no matcheaba ninguna fila, todas caían
en `sin_mapear` y **la ingesta quedaba en cero sin que nada fallara**.

**El flujo nuevo, en tres pasos:**

1. Después de convocar, en la pantalla de la encuesta: **Exportar muestra**.
   Baja un CSV con una sola columna, `id_persona`. Sin datos personales.
2. El equipo de campo **precarga esa columna como variable oculta** en el
   instrumento (Dooblo, Alchemer) y configura que vuelva en el export.
3. Al ingestar, en el paso 2, se declara que la columna trae el
   **`id_persona` del sistema**. El mapeo es directo.

> **Verificar antes de prometerlo:** que Dooblo y Alchemer permitan precargar
> una variable oculta por respondente en el flujo que usa hoy el equipo. Si no
> se puede, el paso 2 no existe y hay que ir por los respaldos.

**Los respaldos, cuando no se pudo precargar.** La misma pantalla deja
declarar que la columna trae el **documento** o el **correo**. Funciona, con
dos advertencias: el archivo de campo contuvo PII —conviene tener una política
de borrado de esos archivos—, y a cambio el sistema **registra el alias de esa
plataforma**, así que la próxima carga del mismo estudio ya anda por alias y
no necesita la llave natural.

**El default no cambia.** Una carga que no declara nada se comporta como
`alias`, igual que hoy: las cargas existentes siguen andando sin tocar nada.

**Exportar con contacto es una reidentificación.** La casilla «incluir datos
de contacto» existe porque el equipo a veces necesita llamar, pero ese archivo
lleva nombre, documento y correo: exige el permiso `exportar_identificado`,
queda registrado en `reidentificacion` con motivo `exportacion`, y el archivo
se marca en el nombre y en su primera línea. El analista puede bajar la
muestra seudónima y **no** la que lleva contacto.

**Y el informe ahora dice por qué no mapeó.** `sin_mapear` trae el motivo por
fila —el valor no es un `id_persona` válido, ese identificador no existe, o
esa plataforma no tiene registrado ese id—, porque las tres se arreglan
distinto.

### 1.7 · Ahora se puede cargar gente sin meterla en un panel (R3.13)

Esta **sí lleva migración** (la 0007, ver §2) y trae una definición legal
abierta. Es lo único de la Fase 3 que agrega una tabla nueva después del
primer despliegue.

**El problema que resuelve.** La única forma de cargar individuos con sus
respuestas era desde una encuesta, y toda encuesta pertenece a un panel. O sea
que dar de alta gente implicaba **necesariamente** meterla en un panel:
aparecía en las convocatorias, en la composición y en el muestreo. Con una
base que llega de afuera —un ómnibus, un estudio de terceros, una base
histórica— eso distorsiona los indicadores de un panel con gente que no es
panelista.

**Dónde está.** Pantalla de **Panelistas** → botón **«Cargar panelistas»**,
al lado de «Enrolar panelista». Pide un nombre para la carga («Ómnibus agosto
2026») y de ahí en adelante es **la misma pantalla de ingesta** de siempre.
Rige el mismo permiso que la ingesta desde encuesta (`ingestar`).

**Qué hace distinto, y es todo lo que hace distinto:**

- **No crea membresía ni participación.** Esa gente no entra en la
  composición, la brecha, el muestreo ni las convocatorias de ningún panel.
- **La finalidad obligatoria se invierte.** En la ingesta desde encuesta, una
  fila sin `contacto_participacion` no crea la persona. Acá la obligatoria es
  **`uso_semantico`**, y el contacto es opcional: quien no lo evidencie se
  crea igual y nunca va a poder ser convocado (el gate de R1.3 sigue
  intacto). El motivo es que son personas que no se van a contactar; exigir
  base legal para el contacto sería pedirla para algo que no se va a hacer.

Todo lo demás es idéntico: dedup de identidad, tipo de identificador, marcado
de demográficos, guardrail de PII, idempotencia.

**Cómo se ve después.** Los individuos cargados aparecen en Panelistas con
**0 paneles**, y el desplegable de la pantalla tiene una opción **«— Sin panel
—»** para aislarlos. Son consultables desde el primer momento. Si después se
decide sumar a alguno a un panel, se hace **desde el resultado de una
consulta** (R3.11), sin volver a cargar nada: ese es el camino previsto
—cargar, consultar, crear el panel con los que interesan— en vez de meter la
base entera y depurar después.

> **La definición pendiente, y es legal.** ¿Alcanza el consentimiento de uso
> semántico para conservar datos patronímicos —nombre, documento— de alguien
> que **no es panelista y no será contactado**? Si la respuesta es que no, hay
> que cargar estos individuos con demográficos pero sin patronímicos (se puede:
> los patronímicos son opcionales en el mapeo) o no cargarlos. **Definirlo
> antes de usar el flujo con bases reales.** No bloquea el despliegue —la
> capacidad puede estar sin usarse— pero sí el primer uso.

> **Y una de producto, más tibia.** Sin una política de revisión periódica, la
> bóveda acumula gente sin panel que nadie mira. Conviene decidir quién la
> revisa y cada cuánto.

---

## 2 · Las migraciones

Tres, y las tres solo en la bóveda. En este orden.

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0005_fase3.sql
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0006_participacion_por_importacion.sql
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0007_carga_sin_panel.sql
```

La 0006 y la 0007 son **aditivas y se pueden aplicar con la app andando**: la
0006 agrega una columna con default —no reescribe las filas existentes ni toma
locks largos— y la 0007 crea una tabla sin tocar ninguna.

> **Orden importa entre el `psql` y el `firebase deploy`.** La 0007 hay que
> aplicarla **antes** de desplegar la función. Al revés, el botón «Cargar
> panelistas» ya está en la pantalla y falla con `relation "carga" does not
> exist` a quien lo apriete. Nada se corrompe, pero es un error feo y evitable.

> El `-v ON_ERROR_STOP=1` no es decorativo. Ya pasó una vez: sin él, `psql`
> sigue después de un error y la base queda a medio migrar sin que se note
> hasta semanas después.

### 2.1 · Qué crea

| Objeto | Para qué |
|---|---|
| `umbral_fatiga` | Los topes de convocatoria por panel (R3.1) |
| `bono_puntos` | Bonos dirigidos a un segmento (R3.6) |
| `texto_consentimiento` | Los textos versionados de la landing (R3.7) |
| `inscripcion` | Las solicitudes públicas, que **no** son personas todavía (R3.7) |
| `persona.estado` | El valor `pendiente_consentimiento` existe en el enum, pero ya no se usa en altas nuevas: solo puede haberlo en altas por SAV de la versión anterior de R3.9 (ver §1.1) |
| `participacion.duracion_segundos` y la revisión de calidad | Detectar speeders y poder revertir la marca (R3.2) |
| `encuesta.umbral_*` y `puntos_participacion` | Umbrales y puntos propios de cada estudio |
| `puntos_earn_unico_por_encuesta` | Que no se pueda liquidar dos veces, ni con concurrencia (R3.4) |
| `panel.origen` y `origen_definicion` | De dónde salió la composición de un panel (R3.11) |
| `participacion.origen` (0006) | Distinguir a quien convocó el sistema de quien respondió en campo y se incorporó al ingestar (addendum de R3.9) |
| `carga` (0007) | Un lote de individuos incorporados con sus respuestas **sin panel**: cumple frente al store semántico el mismo papel que `encuesta`, con su propio `ref_estudio`, y no genera membresías ni participaciones (R3.13) |

### 2.2 · Verificar

```bash
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
python3 scripts/verificar_esquema.py
```

Tienen que salir las seis migraciones de la bóveda y las tres de la
semántica, todas con tilde. Es la misma comprobación que hace la app en
Cumplimiento → Esquema de las dos bases.

---

## 3 · La dependencia nueva

`pyreadstat` lee los `.sav`. Ya está en `functions/requirements.txt`, así que
el `predeploy` de `firebase.json` la instala sola. Vale saber tres cosas:

- **`pandas` va declarado aparte, y es obligatorio.** `pyreadstat` devuelve un
  `DataFrame`, pero desde la versión 1.3 **no declara pandas entre sus
  dependencias**: instala `numpy` y `narwhals` y nada más. Sin la línea
  explícita de `pandas` en `requirements.txt` la función despliega bien,
  importa bien, y falla recién al leer el primer archivo con
  `PyreadstatError('You requested pandas as output_format but cannot import
  pandas')`. Pasó en el primer intento de ingesta real.
- **`numpy` y `pandas` engordan el paquete** unos 60 MB. Está dentro del
  límite de Cloud Functions gen2, pero el primer deploy después de esto tarda
  más.
- **El parseo corre en el backend a propósito** (R3.9): no hay librería
  cliente confiable para `.sav`, y subir el archivo entero es lo que permite
  validarlo antes de escribir nada.

Después de desplegar, verificar que la función quedó en condiciones de leer
`.sav` **sin subir un archivo**:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  https://<tu-hosting>/api/diagnostico/sav | python3 -m json.tool
```

Devuelve 200 con `"puede_leer_sav": true` y las versiones instaladas, o 500
diciendo qué paquete falta. Es la comprobación que hubiera evitado el primer
500 en producción.

Si el deploy falla instalando la rueda, el síntoma es un error de compilación
de `pyreadstat` en el log del predeploy. La causa casi siempre es un runtime
distinto de `python311`; verificar el `runtime` en `firebase.json`.

### 3.1 · Memoria de la función

`main.py` declara **1 GiB** (`MemoryOption.GB_1`), no los 512 MB de las fases
anteriores. Leer un `.sav` levanta `pandas` y `pyreadstat`, y el archivo pasa
por memoria tres veces: el base64 del cuerpo, los bytes decodificados y el
`DataFrame`. Con 512 MB el proceso moría sin dejar log y el navegador veía un
500 sin explicación.

Solo pagan la diferencia las instancias que sirvieron una ingesta —el resto
de las rutas no importa `pandas`—, pero la facturación de Cloud Functions es
por GB-segundo: si el gasto importa, se puede volver a 512 MB **a condición de
no usar la ingesta por `.sav`**.

### 3.2 · Tamaño máximo del archivo

El `.sav` viaja en base64 adentro del JSON, así que ocupa un tercio más que en
disco y todo el cuerpo tiene que entrar en el límite de request del hosting.
`ruteo.LIMITE_SAV_BYTES` lo fija en **22 MB de archivo** (unos 30 MB de
cuerpo). Por encima de eso la subida se corta en la red, antes de llegar a la
función: el chequeo del servidor está para que, cuando el corte no ocurra, el
mensaje diga qué pasó en vez de fallar de manera opaca.

Un export que no entra se parte por olas, o se le sacan del `.sav` las
variables que no se van a ingestar.

---

## 4 · Permisos nuevos

Seis, y ninguno se le agrega a un rol existente por comodidad. Están en
`panel_api/auth.py` y el frontend tiene una copia para decidir qué solapas
mostrar —copia que **no** es el control de acceso: la autoridad es el
backend—.

| Permiso | Roles | Por qué así |
|---|---|---|
| `muestrear` | admin, operaciones | Decidir a quién invitar es del responsable de panel. El analista consulta, no decide la muestra. |
| `revisar_calidad` | admin, operaciones, analista | El chequeo lo corre la ingesta; la revisión la hace quien mira el caso. |
| `gamificacion` | admin, operaciones | Es dinero para el panelista. |
| `aprobar_inscripciones` | admin, operaciones | Es un alta de persona: va con quien ya podía enrolar. |
| `publicar_consentimiento` | admin, dpo | Es una decisión de cumplimiento antes que de operación. |
| `exportar_identificado` | admin, operaciones | **Separado de `reidentificar` a propósito** (ver §6). |

No hay que configurar nada: los permisos se derivan del rol que cada usuario
ya tiene en Firestore.

---

## 5 · La landing pública

### 5.1 · Cómo queda publicada

`web/public/inscribirse.html` es una página aparte que **no carga Firebase
Auth**. El `firebase.json` ya tiene el rewrite:

```
https://gestion-paneles.web.app/inscribirse
```

Va antes del catch-all de la SPA; si se reordena, la landing deja de existir.

### 5.2 · Las dos rutas sin token

Son las únicas de todo el sistema, y están enumeradas una por una en
`ruteo.PUBLICAS`:

```
GET  /api/inscripciones/formulario
POST /api/inscripciones
```

La lista es de rutas concretas y no de un prefijo, justamente para que
agregar una ruta debajo de `/inscripciones/` no abra un agujero que nadie
eligió abrir. Hay una prueba que falla si el conjunto cambia.

### 5.3 · Qué NO hace la landing

Vale enumerarlo porque es lo que se suele romper al «mejorarla»:

- **No crea personas.** Crea una solicitud en `inscripcion`. Recién al
  aprobar se crea el panelista. De ahí sale gratis que un pendiente no pueda
  ser convocado ni aparecer en una consulta semántica: todavía no existe.
- **No dice si la persona ya estaba.** Todos los envíos válidos reciben el
  mismo acuse. Un formulario que contesta «ya estás registrado» es un oráculo
  para averiguar quién es panelista probando documentos.
- **No expone ningún dato de otros panelistas.** El endpoint del formulario
  devuelve el texto de consentimiento y nada más.

### 5.4 · Antes de anunciarla

La landing es superficie pública sin endurecer: verificación de contacto y
anti-fraude son Fase 4 (R4.4). La spec lo marca como riesgo abierto. Dos
recomendaciones concretas:

1. **Abrirla con volumen limitado** —un canal, una campaña— y mirar la
   bandeja de pendientes antes de difundirla ampliamente.
2. **Poner Cloud Armor o rate limiting** en `/api/inscripciones` si se va a
   difundir en redes. Sin eso, un script llena la tabla de solicitudes basura
   en minutos. No degrada nada del resto del sistema —las inscripciones no
   son personas— pero le arruina la bandeja a quien tenga que revisarlas.

---

## 6 · La exportación con datos personales

Es la superficie de PII más nueva y conviene entender la regla antes de
habilitarla.

**Exportar con datos exige haber reidentificado primero.** No es una
comodidad de la interfaz: si la exportación pudiera resolver la PII por su
cuenta, habría dos caminos para sacarla de la bóveda y solo uno quedaría
auditado. La ruta recibe el resultado ya resuelto y lo usa tal cual.

Se registra en `reidentificacion` con motivo `exportacion`, distinto del
`consulta` que deja haberla mirado en pantalla. Para auditar:

```sql
select motivo, count(*), max(creado_en)
  from reidentificacion group by 1 order by 2 desc;
```

El archivo lleva la marca en el nombre —`consulta-CON-DATOS-PERSONALES-…`— y
en su primera línea. No incluye fecha de nacimiento exacta ni observaciones:
la primera es un identificador fino y las segundas son texto libre donde
suele terminar cayendo dato sensible.

El CSV seudonimizado de la Fase 2 **no cambió** y sigue siendo el botón por
defecto.

---

## 7 · Redesplegar

```bash
export VPC_CONNECTOR=paneles-conn
firebase deploy
```

No hay secretos nuevos en esta fase. Los cinco de siempre —`DSN_BOVEDA`,
`DSN_SEMANTICA`, `EMBEDDINGS_API_KEY`, `RERANKER_API_KEY`, `CLAUDE_API_KEY`—
siguen siendo los mismos.

---

## 8 · Verificación

En orden, porque cada paso depende del anterior. Todo desde la app, con un
usuario `admin`.

0. **El esquema.** Cumplimiento → Esquema de las dos bases tiene que decir que
   están las ocho migraciones. Si falta alguna, no tiene sentido seguir.

1. **Muestreo.** Muestreo → elegí un panel **con objetivo de composición
   cargado** y una encuesta abierta → Proponer.
   - Con brecha: la propuesta tiene que priorizar el segmento que falta y
     decirlo en la columna «Por qué está».
   - Sin brecha: tiene que avisar que el panel ya calza, y **no** decir que
     un segmento «tiene brecha (0 personas)».
   - Sin objetivo cargado: tiene que avisar que reparte parejo.
   - En todos los casos: «Es una sugerencia: nadie fue convocado», y la tabla
     de exclusiones con sus motivos.

2. **Umbrales de fatiga.** Muestreo → Umbrales de fatiga → bajá el máximo de
   convocatorias de la ventana a 1 y volvé a proponer: tiene que caer gente
   al motivo «superó el tope de convocatorias de la ventana».

3. **Calidad.** Corré los chequeos sobre una encuesta ya ingestada. Si el
   export no traía tiempos, el informe **tiene que decir** que el chequeo de
   speeder no se aplicó. Que no haya marcas de speeder no significa que no
   los haya: significa que no se pudo mirar.

4. **Revisión de una marca.** Revertí un `sospechoso` a `ok`. Tiene que
   quedar registrado quién lo hizo, y habilitar la liquidación de ese punto.

5. **Liquidación.** Puntos y premios → Liquidación → liquidá una encuesta.
   Solo tienen que cobrar quienes respondieron **y** quedaron en `ok`.
   Liquidá de nuevo: no puede pagar dos veces.

6. **Canje.** Cargá un premio, dale saldo a alguien con un ajuste y canjealo.
   Cancelá el canje: los puntos vuelven **como movimiento nuevo** y el stock
   se repone. El extracto tiene que mostrar el descuento original y la
   devolución, las dos cosas.

7. **Bono dirigido.** Creá un bono para un segmento y liquidá otra encuesta:
   solo ese segmento tiene que recibir el extra.

8. **Landing.** Publicá un texto de consentimiento y abrí `/inscribirse` en
   una ventana privada (sin sesión).
   - Enviar sin tildar el consentimiento tiene que rechazar **y no guardar
     nada**.
   - Enviar completo tiene que dar el acuse.
   - Inscripciones → Pendientes tiene que mostrarla, con la versión del texto
     y qué dijo la resolución de identidad.
   - Aprobar tiene que crear la persona con ese consentimiento.

9. **SAV.** Encuestas → Ingestar respuestas → subí un `.sav`. Los textos de
   las preguntas y las etiquetas de respuesta tienen que venir precargados, y
   las variables con label vacío o truncado, marcadas. Corregí un texto antes
   de confirmar: es lo que se vectoriza.

9.1. **SAV con alta de individuos.** Elegí *No: darlos de alta en esta carga*.
   - Sin completar la evidencia de consentimiento, **no tiene que dejar
     confirmar**.
   - Con una variable que no existe en el archivo, tiene que rechazarlo
     diciendo cuál.
   - Con todo completo, el resumen tiene que decir cuántas personas se
     crearon y cuántas quedaron afuera por no consentir.
   - Verificá en la base que las creadas quedaron `activa` y con su
     consentimiento:
     ```sql
     select p.estado, c.finalidad, c.version_texto
       from persona p join consentimiento c using (id_persona)
      order by p.creado_en desc limit 10;
     ```

10. **Exportación con datos.** Consultas → correr una → «CSV con datos» tiene
    que estar **deshabilitado**. Usá «Ver quiénes son» y recién ahí se
    habilita. Después de descargar, verificá el registro con motivo
    `exportacion`.

11. **Panel desde consulta.** Consultas → Crear panel. Tiene que crear las
    membresías, avisar cuántos no son convocables, y quedar con
    `origen = 'consulta'` en la base.

---

## 9 · Problemas frecuentes

**«Al subir el `.sav` la pantalla queda en "Analizando el archivo en el
servidor…" y después tira 500.»**
Cuatro causas. (1) **Falta `pandas` en la función** —la que efectivamente
pasó la primera vez—: el log dice `You requested pandas as output_format but
cannot import pandas`. Se arregla redesplegando con el `requirements.txt`
actual (§3); desde esta versión el mensaje que llega a la pantalla nombra el
despliegue y no manda a tocar el archivo. (2) La función se quedó sin
memoria: verificar que esté desplegada con 1 GiB (§3.1) —en Cloud Logging la
instancia muere sin dejar traza del error—. (3) El archivo no se puede
parsear: eso vuelve como **400 con el motivo**, no como 500; si el mensaje
habla de codificación, reexportarlo desde SPSS en UTF-8. (4) Cualquier otra
cosa: el handler imprime el traceback completo, así que el motivo está en el
log.

```bash
gcloud functions logs read api --region=southamerica-east1 --limit=80 \
  --gen2 | grep -A 20 "error no manejado"
```

**«La subida no muestra ningún avance.»**
Debería mostrar tres fases: leer el archivo y subirlo con porcentaje, y el
análisis en el servidor con el tiempo transcurrido. Si no aparece nada, el
navegador está sirviendo el JS viejo de caché: `firebase.json` manda
`Cache-Control: no-cache` para `.js`, así que alcanza con recargar.

**«Después de ingestar, el panel tiene más miembros de los que yo agregué.»**
Es la conducta nueva (§1.4): quien respondió esa encuesta queda incorporado
al panel de la encuesta. El resultado de la ingesta dice cuántos fueron. Si
no era lo que se quería, la vía es dar de baja la membresía desde la pantalla
del panel —y esa baja no se revierte en la próxima ingesta—.

**«Ingestar falla con `column "origen" does not exist`.»**
Falta la migración `0006_participacion_por_importacion.sql` (§2).
`python3 scripts/verificar_esquema.py` lo dice y da el comando.

**«Alguien que respondió no aparece como convocado en el tablero.»**
Correcto: no se lo convocó. Aparece como miembro y como respuesta, pero
`convocatorias` y «último contacto» cuentan solo lo que emitió el sistema. Es
lo que evita que la fatiga lo saque del muestreo por contactos que nunca
ocurrieron (§1.4).

**«Ingesté un estudio nuevo y no mapeó ninguna fila.»**
Es el problema que R3.12 resuelve (§1.6): la plataforma generó ids nuevos y
ninguno coincide con los alias guardados. El informe ahora lo dice —«esa
plataforma no tiene registrado ese id»—. La salida de fondo es precargar la
muestra; la de este archivo, declarar que la columna trae el documento o el
correo, que además deja sembrado el alias para la próxima.

**«Una fila con un id mal escrito hizo fallar toda la carga.»**
Ya no. Los valores que no son uuid se descartan antes de consultar y se
informan aparte: Postgres aborta la transacción entera ante un uuid inválido,
así que sin ese filtro un typo se llevaba puesta la ingesta completa.

**«Faltan variables en el store semántico después de ingestar.»**
Fijate si están marcadas como demográficas (§1.5): en ese caso es correcto y
el resultado de la carga las enumera. Si alguna no debería estarlo —porque es
el objeto del estudio y no un segmentador—, se le cambia la marca a «pregunta
del estudio» y se vuelve a ingestar.

**«La composición por sexo muestra 1 y 2 en vez de F y M.»**
Son altas anteriores a este cambio, cuando el código del `.sav` se guardaba
crudo. Las nuevas se traducen. Las viejas se corrigen desde la ficha del
panelista; no hay migración porque no hay forma de saber, mirando la base, qué
codificación usaba cada archivo.

**«La pantalla de Muestreo dice que no hay encuestas abiertas.»**
Solo ofrece encuestas en `borrador` o `en_campo`. Una cerrada no admite
convocatorias nuevas, así que proponer para ella no tendría sentido.

**«La propuesta viene más corta de lo que pedí.»**
Es la conducta correcta cuando hay objetivo cargado: completar con gente de
segmentos que ya están cubiertos alejaría al panel de su universo de
referencia. El aviso al pie lo explica, y las exclusiones dicen qué frena a
cada uno.

**«El chequeo de calidad no marca ningún speeder.»**
Mirá la sección «no evaluado» del informe. Si dice que ninguna participación
trae duración, el chequeo no corrió: el export de campo tiene que traer el
tiempo de respuesta. Verificar con Dooblo/Alchemer qué trae cada export.

**«La liquidación no paga a nadie.»**
Solo paga a quien respondió **y** tiene `calidad_estado = 'ok'`. Si nunca se
corrieron los chequeos, todos quedan en `pendiente` y no cobran. Corré la
calidad primero.

**«El canje falla con saldo insuficiente y el saldo parece alcanzar.»**
El saldo es la suma de los movimientos en el momento del canje, con la
persona bloqueada. Si dos canjes salieron a la vez, uno cobró primero. Mirá
el extracto: el movimiento que ganó está ahí.

**«La landing dice que no está disponible.»**
No hay ningún texto de consentimiento publicado. Es el estado de fábrica y es
deliberado (§1.2).

**«Subir un `.sav` da error 400 diciendo que falta pyreadstat.»**
La función se desplegó sin instalar la dependencia. Revisar el log del
predeploy y volver a desplegar.

**«Un `.sav` precarga los textos como “P1”, “P2”…»**
El archivo no tiene variable labels. La precarga cae al código y lo marca con
un aviso. Hay que escribir los textos a mano antes de confirmar: si se
ingesta así, la consulta semántica sobre ese estudio no va a servir, porque
lo que se vectoriza es el texto.

**«La importación con alta de individuos se rechaza y dice que falta la
evidencia de consentimiento.»**
Es lo esperado: declararla es obligatorio (§1.1). Hay que indicar variable,
valor afirmativo y versión del texto para las dos finalidades. Si el
cuestionario tiene una sola pregunta de consentimiento, se declara la misma
variable en las dos.

**«Importé y no se creó nadie.»**
Mirá el aviso del resultado. Lo más probable es que el valor afirmativo
declarado no coincida con el que trae el archivo —declaraste `1` y el archivo
codifica `Sí`, o al revés—. El aviso dice qué valor tenía cada fila que quedó
afuera. Corregí el valor declarado y volvé a importar: reimportar no duplica
nada.

**«Alguien creado por SAV no aparece en el muestreo.»**
Si quedó en `pendiente_consentimiento`, es un alta de la versión anterior del
módulo: regularizala (§1.1). Con la versión actual eso no puede pasar, porque
quien no evidencia el consentimiento no se crea.

**«Las respuestas de alguien no llegaron al store semántico.»**
Consintió el contacto y no el uso semántico. Es el gate de R1.3 funcionando:
está en el panel y se lo puede convocar, pero sus respuestas no se ingestan.

---

## 10 · Volver atrás

**El código** se revierte redesplegando la versión anterior de la función; la
migración puede quedar aplicada sin molestar, porque todo lo que agrega es
aditivo (tablas nuevas y columnas con default).

**Apagar solo la landing** sin tocar nada más: sacar el rewrite de
`/inscribirse` de `firebase.json` y redesplegar hosting. Las rutas de API
siguen existiendo pero no hay página que las use. Para cerrarla del todo,
desactivar el texto de consentimiento:

```sql
update texto_consentimiento set activo = false
 where finalidad = 'contacto_participacion';
```

El formulario pasa a responder que no está disponible, sin borrar nada de lo
ya consentido.

**Si igual hay que revertir el esquema**, el orden es el inverso al de
creación y hay que asumir la pérdida de lo que esas tablas guardan
(inscripciones, movimientos de puntos, bonos, umbrales). No es una operación
de rutina: preferir dejar la migración aplicada.

---

## 11 · Checklist

Infraestructura:

- [ ] `db/boveda/0005_fase3.sql`, `db/boveda/0006_participacion_por_importacion.sql`
      y `db/boveda/0007_carga_sin_panel.sql` aplicadas, en ese orden, y las tres
      **antes** del `firebase deploy`.
- [ ] `python3 scripts/verificar_esquema.py` sale con código 0.
- [ ] `firebase deploy` completo, con `pyreadstat` instalado en el predeploy.
- [ ] `GET /api/diagnostico/sav` devuelve 200 con `puede_leer_sav: true`.
      Es lo que detecta que la función quedó sin `pandas`, que despliega bien
      y recién falla al leer el primer archivo (§3).
- [ ] La función quedó desplegada con **1 GiB** de memoria (§3.1).
- [ ] El rewrite de `/inscribirse` está **antes** del catch-all.

Definiciones pendientes:

- [ ] El cuestionario de campo **incluye la pregunta de consentimiento**, y se
      sabe qué variable es y qué valor cuenta como afirmativo. Sin eso, el
      modo «crear individuos» del SAV no puede dar de alta a nadie.
- [ ] Si quedaron personas en `pendiente_consentimiento` de la versión
      anterior, regularizadas.
- [ ] Texto de consentimiento revisado por el DPO y publicado — **sin esto la
      landing no recibe a nadie**.
- [ ] Tratamiento fiscal del canje definido, o catálogo cargado con los
      premios inactivos.
- [ ] Revisión con el DPO del conjunto de las tres superficies nuevas de datos
      personales (landing, alta por SAV, exportación con PII), no de a una.
- [ ] **Si el consentimiento de uso semántico alcanza para conservar
      patronímicos de un no-panelista** (§1.7). Sin esta definición no se debe
      usar «Cargar panelistas» con bases reales.
- [ ] Política de revisión de las personas sin panel acumuladas (§1.7).

Verificación funcional:

- [ ] Ingestar un archivo con gente no convocada la incorpora al panel y la
      cuenta en la tasa de respuesta de la ola.
- [ ] Una membresía dada de baja no se reactiva al ingestar, y el resultado
      la informa.
- [ ] Convocar sigue dejando afuera a quien no tiene consentimiento de
      contacto, aunque tenga membresía y participaciones.
- [ ] Una variable marcada como demográfica no aparece del lado semántico, y
      el resultado de la carga la enumera entre las excluidas.
- [ ] Un `SEXO` codificado 1/2 queda como F/M en la ficha, no como 1/2.
- [ ] Un dato del archivo que difiere del de la ficha no la pisa y se informa.
- [ ] «Exportar muestra» baja un CSV con `id_persona` y sin datos personales.
- [ ] La exportación con contacto queda en la auditoría de reidentificación, y
      un analista no puede bajarla.
- [ ] Una carga declarando `id_persona` mapea sin depender de `alias_origen`.
- [ ] Una carga sin declarar tipo se comporta igual que antes.
- [ ] «Cargar panelistas» incorpora la gente del archivo y **ninguno queda
      como miembro de un panel**, ni aparece en la participación de ninguna ola.
- [ ] Una persona del archivo que ya era panelista conserva sus paneles.
- [ ] En esa carga, una fila sin evidencia de `uso_semantico` no crea la
      persona y el resumen lo informa; una con uso semántico y sin contacto se
      crea, y convocarla sigue estando bloqueado.
- [ ] El filtro «— Sin panel —» de Panelistas lista a los cargados así, y
      aparecen en las consultas semánticas.
- [ ] Crear un panel desde una consulta que los incluye les da membresía.
- [ ] Muestreo prioriza la brecha, explica las exclusiones y no convoca.
- [ ] Un panel sin brecha no reporta «brecha (0 personas)».
- [ ] Un export sin tiempos informa que no se pudo evaluar el speeder.
- [ ] Revertir un `sospechoso` habilita su liquidación.
- [ ] Liquidar dos veces no paga dos veces.
- [ ] Cancelar un canje devuelve los puntos como movimiento nuevo.
- [ ] Un bono alcanza solo a su segmento.
- [ ] La landing rechaza sin consentimiento y no guarda nada en ese caso.
- [ ] Aprobar una inscripción crea la persona con la versión que aceptó.
- [ ] Un `.sav` precarga textos y etiquetas, y marca los dudosos.
- [ ] Sin evidencia de consentimiento declarada, el alta por SAV se rechaza.
- [ ] Quien no consiente el contacto no se crea, y el resumen lo dice.
- [ ] «CSV con datos» está deshabilitado hasta reidentificar.
- [ ] Crear un panel desde una consulta registra su origen.

Calibración (después, pero no olvidarla):

- [ ] Umbrales de fatiga medidos contra los datos de Equipos. Los defaults
      —3 convocatorias en 90 días, 14 días entre olas— **no están medidos**.
- [ ] Umbral de speeder por estudio, medido contra la duración real de cada
      cuestionario. El default de 120 segundos es un punto de partida.
- [ ] Puntos por participación y costos del catálogo, contra lo que Equipos
      quiera gastar por ola.
