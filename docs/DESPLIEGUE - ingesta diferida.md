# Despliegue — Ingesta diferida con Cloud Tasks

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`  
**Actualización:** 3 de octubre de 2026  
**Migración:** `db/boveda/0019_ingesta_diferida.sql`

Guía corregida para reemplazar `DESPLIEGUE - ingesta diferida.md`.
Ejecutar desde la raíz del repositorio `paneles`, en la misma terminal.
No repetir migraciones ni recrear recursos que ya estén correctamente configurados.

| Recurso | Nombre |
|---|---|
| Función, cola y final de su URL | `procesaringesta` |
| Usuario PostgreSQL de COLOQUIO | `coloquio-app@gestion-paneles.iam` |
| Cuenta IAM de COLOQUIO | `coloquio-app@gestion-paneles.iam.gserviceaccount.com` |
| Cuenta OIDC elegida para las tareas | `gestion-paneles@appspot.gserviceaccount.com` |

**Un solo nombre, y sin guion bajo.** El SDK de Python deriva el id del
endpoint del nombre de la función —no hay opción para fijarlo— y el CLI crea
la cola de Cloud Tasks con ese id. Un Queue ID solo admite letras, números y
guiones, así que una función `procesar_ingesta` **despliega y después rompe el
deploy entero**:

```
Request to …/queues/procesar_ingesta had HTTP Error: 400,
Queue ID "procesar_ingesta" can contain only letters ([A-Za-z]),
numbers ([0-9]), or hyphens (-).
```

Por eso la función se llama `procesaringesta`: el nombre de la función **es**
el nombre de la cola. `TAREAS_COLA` tiene ese mismo valor por defecto. La URL
de destino se configura por separado, en `TAREAS_URL`.

## Paso 0 · Preparar la terminal

En macOS con zsh, permitir comentarios interactivos:

```bash
setopt INTERACTIVE_COMMENTS
```

Definir explícitamente todas las variables. Se pierden al abrir otra terminal:

```bash
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
export FUNCION_INGESTA="procesaringesta"
export COLA_INGESTA="procesaringesta"   # el mismo: ver arriba
export CUENTA="${PROYECTO}@appspot.gserviceaccount.com"
export URL="https://${REGION}-${PROYECTO}.cloudfunctions.net/${FUNCION_INGESTA}"
gcloud config set project "$PROYECTO"
printf 'URL: %s\nCuenta OIDC: %s\n' "$URL" "$CUENTA"
```

Copiar solo el contenido de los bloques. Las barras de continuación deben ser
el último carácter de la línea, sin espacios detrás. No pegar enlaces Markdown
como URL dentro de comandos.

## Paso 1 · Túneles y conexiones

Precondición: bóveda al día hasta `0018` y semántica hasta `0006`.
La migración toca solo la bóveda, pero el diagnóstico consulta ambas bases.

### 1.1 · Proxies

Si ya están abiertos, no iniciar otra copia:

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &
```

Para comprobar los puertos en macOS:

```bash
lsof -nP -iTCP:5432 -sTCP:LISTEN
lsof -nP -iTCP:5433 -sTCP:LISTEN
```

### 1.2 · DSN del dueño

```bash
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Debe devolver `paneles_boveda`. Resolver cualquier error de lectura de secretos
antes de continuar. No imprimir ni compartir los DSN.

### 1.3 · Conexión IAM de COLOQUIO

El usuario observado es `CLOUD_IAM_SERVICE_ACCOUNT`, no un usuario con
contraseña PostgreSQL. Usar un token temporal de esa cuenta como contraseña:

```bash
TOKEN_COLOQUIO="$(gcloud auth print-access-token \
  --impersonate-service-account=coloquio-app@gestion-paneles.iam.gserviceaccount.com \
  --project="$PROYECTO")" && \
export DSN_BOVEDA_COLOQUIO="postgresql://coloquio-app%40gestion-paneles.iam:${TOKEN_COLOQUIO}@127.0.0.1:5432/paneles_boveda"
```

Continuar solo si tuvo éxito. Repetir cuando expire el token; no guardarlo en el
repositorio. Si aparece `iam.serviceAccounts.getAccessToken denied`, la cuenta
que ejecuta gcloud necesita permiso para impersonar esta cuenta, por ejemplo
`roles/iam.serviceAccountTokenCreator` sobre esa cuenta de servicio concreta.

Sin `DSN_BOVEDA_COLOQUIO`, el script deriva un usuario `coloquio_app` sin
contraseña, previsto para pruebas locales. En Cloud SQL produjo
`fe_sendauth: no password supplied`.

Para comprobar los usuarios sin mostrar contraseñas:

```bash
gcloud sql users list --instance=paneles-boveda --project="$PROYECTO"
```

## Paso 2 · Confirmar que falta solo la `0019`

```bash
python3 scripts/verificar_esquema.py
```

```
    ✗ 0019_ingesta_diferida.sql
        falta estado_ingesta  (los estados de un trabajo de ingesta, con su etiqueta, para que la pantalla no repita el diccionario)
        falta estado_lote_ingesta  (los estados de un lote de ingesta, con su etiqueta)
        falta ingesta_trabajo  (la carga que se procesa en diferido, con el mapeo congelado; sin ella la ingesta vuelve a correr adentro de una request y corta por timeout)
        falta ingesta_lote  (cada pedazo de una carga con su rebanada de filas; es lo que permite reintentar uno solo en vez de rehacer todo)
        falta v_ingesta_progreso  (el avance de cada carga contado sobre sus lotes; es lo que lee la pantalla para mostrar progreso)
        falta purgar_ingestas_terminadas()  (libera las filas guardadas de las cargas viejas; sin ella el espacio temporal de las cargas grandes no se recupera nunca)
```

---

## Paso 3 · Aplicar `boveda/0019`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction \
     -f db/boveda/0019_ingesta_diferida.sql
```

Salida esperada, completa:

```
CREATE TABLE
COMMENT
INSERT 0 5
CREATE TABLE
INSERT 0 4
CREATE TABLE
ALTER TABLE
CREATE INDEX
CREATE INDEX
COMMENT
CREATE TABLE
CREATE INDEX
COMMENT
COMMENT
CREATE VIEW
COMMENT
CREATE FUNCTION
COMMENT
REVOKE
REVOKE
```

**Es aditiva y sin riesgo de datos.** Crea dos catálogos, dos tablas, una
vista y una función; no toca ninguna fila existente.

> **Los dos `REVOKE` del final no son decorativos.** Postgres le da `execute`
> a `public` sobre toda función nueva, así que sin ellos **COLOQUIO podría
> purgar las filas de nuestras cargas**. No es hipotético: la lista blanca de
> privilegios de `verificar_coloquio.py` rompió por esta función antes de que
> esas dos líneas existieran.

---

## Paso 4 · Verificar esquema y privilegios

```bash
python3 scripts/verificar_esquema.py
python3 scripts/verificar_coloquio.py --solo-lectura
```

El primero debe indicar que las dos bases están al día. En producción usar
`--solo-lectura`: ejecuta siete chequeos, sin crear el escenario de prueba.
**No se esperan 16 chequeos en este modo.**

La batería completa escribe datos de prueba y requiere conexiones adicionales,
incluida una conexión de intruso válida. Reservarla para un entorno de pruebas;
no crear un usuario intruso en producción para completar el contador.

```bash
psql "$DSN_BOVEDA" -c "select codigo, etiqueta, terminal from estado_ingesta order by orden"
```

Deben aparecer `encolada`, `procesando`, `terminada`, `terminada_con_errores` y `fallida`.

## Paso 5 · Cloud Tasks, secretos y cola

### 5.1 · Habilitar la API

```bash
gcloud services enable cloudtasks.googleapis.com --project="$PROYECTO"
```

### 5.2 · Crear los secretos que falten

```bash
gcloud secrets list --project="$PROYECTO" --format='value(name)'
```

Ejecutar solo el comando correspondiente a cada secreto que todavía no exista:

```bash
gcloud secrets create TAREAS_URL --replication-policy=automatic --project="$PROYECTO"
gcloud secrets create TAREAS_CUENTA --replication-policy=automatic --project="$PROYECTO"
```

`ALREADY_EXISTS` significa que ya existe. No ocultar otros errores con un mensaje
«ya existe»; resolverlos antes de seguir.

### 5.3 · Guardar los valores

Si se abrió otra terminal, repetir el paso 0.

```bash
printf 'URL: %s\nCUENTA: %s\n' "$URL" "$CUENTA"

if [ -n "$URL" ] && [ -n "$CUENTA" ]; then
  printf '%s' "$URL" | gcloud secrets versions add TAREAS_URL --project="$PROYECTO" --data-file=-
  printf '%s' "$CUENTA" | gcloud secrets versions add TAREAS_CUENTA --project="$PROYECTO" --data-file=-
else
  printf '%s\n' "Faltan URL o CUENTA. Ejecutar el paso 0."
fi
```

Ambas operaciones deben confirmar `Created version [...]`.
`Secret Payload cannot be empty` indica una variable vacía.

### 5.4 · Crear o verificar la cola explícitamente

Durante este despliegue la función existía, pero el listado de colas devolvió
cero resultados. No asumir que Firebase creó la cola automáticamente.

```bash
gcloud tasks queues list --location="$REGION" --project="$PROYECTO"
```

Si falta `procesaringesta`, crearla:

```bash
gcloud tasks queues create "$COLA_INGESTA" \
  --location="$REGION" \
  --project="$PROYECTO" \
  --max-concurrent-dispatches=3 \
  --max-attempts=5 \
  --min-backoff=10s \
  --max-backoff=300s
```

Si ya existe y hay que corregir los límites, usar:

```bash
gcloud tasks queues update "$COLA_INGESTA" \
  --location="$REGION" \
  --project="$PROYECTO" \
  --max-concurrent-dispatches=3 \
  --max-attempts=5 \
  --min-backoff=10s \
  --max-backoff=300s
```

```bash
gcloud tasks queues describe "$COLA_INGESTA" --location="$REGION" --project="$PROYECTO"
```

Se espera `state: RUNNING`. Cinco intentos incluye el inicial. Si el runtime
sobrescribe `TAREAS_COLA`, debe coincidir con esta cola; el valor por defecto
actual del código ya coincide. Cada tarea contiene por separado la URL de la función.

## Paso 6 · Permisos de la API que encola

### 6.1 · Obtener la cuenta real de ejecución

La cuenta OIDC guardada en `TAREAS_CUENTA` puede ser distinta de la cuenta
que ejecuta `api`. No asumir que una función Gen 2 usa la cuenta App Engine.

```bash
CUENTA_API="$(gcloud functions describe api \
  --gen2 \
  --region="$REGION" \
  --project="$PROYECTO" \
  --format='value(serviceConfig.serviceAccountEmail)')"
printf 'Cuenta API: %s\nCuenta OIDC: %s\n' "$CUENTA_API" "$CUENTA"
```

Si `CUENTA_API` está vacía o el comando falló, resolverlo antes de asignar permisos.

### 6.2 · Autorizar encolado y uso de la cuenta OIDC

```bash
gcloud projects add-iam-policy-binding "$PROYECTO" \
  --member="serviceAccount:$CUENTA_API" \
  --role="roles/cloudtasks.enqueuer" \
  --condition=None

gcloud iam service-accounts add-iam-policy-binding "$CUENTA" \
  --project="$PROYECTO" \
  --member="serviceAccount:$CUENTA_API" \
  --role="roles/iam.serviceAccountUser" \
  --condition=None
```

El segundo permiso incluye `iam.serviceAccounts.actAs`, necesario para crear
una tarea que usa esa cuenta OIDC. `TokenCreator` a nivel proyecto, indicado
por la guía anterior, no sustituye ese permiso ni hace falta otorgarlo de
forma general para este flujo. Esta guía no revoca permisos anteriores.

`--condition=None` evita la pregunta interactiva que apareció porque existen
otros bindings condicionales. Si aparece el selector, elegir `None`; no usar
las condiciones de las bases Firestore para estos permisos.

El permiso de invocación se aplica en el paso 7.3, cuando la función ya existe.

## Paso 7 · Desplegar y verificar

### 7.1 · Recuperar el conector VPC y desplegar

«El de siempre» era un marcador de la guía, no un valor para copiar.
Recuperar el conector de la API existente:

```bash
export VPC_CONNECTOR="$(gcloud functions describe api \
  --gen2 \
  --region="$REGION" \
  --project="$PROYECTO" \
  --format='value(serviceConfig.vpcConnector)')"
printf 'Conector VPC: %s\n' "$VPC_CONNECTOR"
```

Si devuelve vacío, listar los conectores y elegir el conectado a la red de Cloud SQL:

```bash
gcloud compute networks vpc-access connectors list \
  --project="$PROYECTO" \
  --region="$REGION"
```

Solo si es necesario asignarlo manualmente, este bloque de zsh solicita el
nombre completo, evitando un marcador que se pueda copiar por error:

```bash
read "VPC_CONNECTOR?Pegá el nombre completo del conector VPC: "
export VPC_CONNECTOR
```

No desplegar con la variable vacía si las bases usan IP privada.
Con el conector exportado:

```bash
firebase deploy --only functions --project="$PROYECTO"
```

Comprobar que el deploy termine correctamente. No aceptar borrados de funciones
ajenas al cambio si Firebase los propone sin haberlos previsto.

### 7.2 · Verificar el nombre real, la URL y la cola

```bash
gcloud functions list --project="$PROYECTO" --regions="$REGION" --format='value(name)'

gcloud functions describe "$FUNCION_INGESTA" \
  --gen2 \
  --region="$REGION" \
  --project="$PROYECTO" \
  --format='yaml(name,state,serviceConfig.uri,serviceConfig.vpcConnector,serviceConfig.serviceAccountEmail)'
```

El nombre observado es `procesaringesta`. La URL debe terminar en
`/procesaringesta`. El deploy debe conservar el conector VPC.

Que lo conserve hay que **verlo**: si la clave falta, el `yaml` la omite sin
decir nada. Las dos funciones tienen que devolver el mismo conector.

```bash
for f in api "$FUNCION_INGESTA"; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)'
done
```

Una vacía significa que ese deploy corrió sin `VPC_CONNECTOR` exportado.
`main.py` lo lee del entorno **del deploy**, y `None` es un valor válido: la
función sale sin conector y sin un solo error. Reexportar y volver a
desplegar; el síntoma, si no, aparece recién en el paso 8 y no se parece a
esto.

Si el secreto se había guardado con el nombre incorrecto, corregirlo y
redesplegar la API para que tome la nueva versión:

```bash
URL="https://${REGION}-${PROYECTO}.cloudfunctions.net/${FUNCION_INGESTA}"
printf '%s' "$URL" | gcloud secrets versions add TAREAS_URL --project="$PROYECTO" --data-file=-
firebase deploy --only functions:api --project="$PROYECTO"
```

Mantener `VPC_CONNECTOR` exportado también en este deploy. No repetirlo si
el secreto ya era correcto al desplegar.

Si una versión anterior se desplegó con la función llamada
`procesar_ingesta`, queda huérfana: el CLI la ofrece para borrar en el
siguiente deploy, y si se saltea el prompt se borra a mano.

```bash
gcloud functions list --project="$PROYECTO" --regions="$REGION" --format='value(name)'
firebase functions:delete procesar_ingesta --region="$REGION" --project="$PROYECTO"
```

La cola se verifica con su propio nombre:

```bash
gcloud tasks queues describe "$COLA_INGESTA" --location="$REGION" --project="$PROYECTO"
```

### 7.3 · Otorgar el permiso de invocación

```bash
gcloud functions add-invoker-policy-binding "$FUNCION_INGESTA" \
  --region="$REGION" \
  --member="serviceAccount:$CUENTA" \
  --project="$PROYECTO"
```

La función y la cola se llaman igual, así que acá no hay nada que
confundir. Un 404 significa que la función no llegó a desplegarse.

## Paso 8 · Probar una carga chica de punta a punta

**Este paso y el 9 son los que deciden si el despliegue está hecho.** Que las
funciones estén arriba no alcanza: lo que hay que ver es una carga que
termina sola.

Desde la app, en una encuesta de prueba, ingestar un archivo de **unas pocas
decenas de filas**:

| # | Qué tiene que pasar |
|---|---|
| 8.1 | La confirmación vuelve **en segundos**, no después de minutos |
| 8.2 | La pantalla muestra una barra que avanza, con «N de M fila(s)» |
| 8.3 | Al terminar, el resumen es el de siempre: escritas, personas, sin mapear, sin consentimiento, demográficos |
| 8.4 | **Recargar la página a mitad de carga** y que el progreso siga ahí |

El 8.4 es el que prueba que el estado vive en la base y no en la pestaña.

Y desde la terminal, que los lotes hayan entrado:

```bash
psql "$DSN_BOVEDA" -c "
select trabajo_id, estado, lotes_total, lotes_ok, lotes_fallidos,
       filas_procesadas, segundos_transcurridos
  from v_ingesta_progreso order by creado_en desc limit 5;"
```

> **Si la barra no avanza nunca**, el problema casi siempre es el paso 5 o el
> 6. Mirar la cola, que dice si las tareas están saliendo:
>
> ```bash
> gcloud tasks queues describe "$COLA_INGESTA" --location="$REGION" --project="$PROYECTO"
> gcloud functions logs read "$FUNCION_INGESTA" --gen2 \
>   --region="$REGION" --project="$PROYECTO" --limit=30
> ```

---

## Paso 9 · Validar recuperación en un entorno de pruebas

Con una carga pequeña y un entorno aislado, provocar un fallo transitorio
controlado del proveedor, observar los reintentos y restaurar la configuración
válida. Usar un mecanismo de inyección de fallos apropiado para ese entorno.

La guía anterior proponía publicar una clave de embeddings vacía. No hacerlo
sobre el secreto compartido de producción: afecta a las funciones que adopten
esa versión y puede ser rechazado por contenido vacío.

Verificar que los errores transitorios se reintenten, los errores de datos
muestren su detalle, y «Reintentar los lotes fallidos» permita completar la
carga sin duplicar lo ingresado. La política de la cola y el estado de la
aplicación son distintos: no asumir que la pantalla espera cinco intentos
para mostrar un fallo. Un 403 anterior a la función tampoco actualiza por sí
solo el estado del lote.

```bash
psql "$DSN_SEMANTICA" -c "
select count(*) as respuestas, count(distinct (individuo_id, pregunta_id)) as pares
  from respuesta;"
```

Los conteos deben coincidir. Revisar también los registros de la encuesta de
prueba; la igualdad global no demuestra toda la correctitud de la carga.

## Paso 10 · Enganchar la purga del espacio temporal

Las filas de una carga terminada ocupan lugar y no sirven para nada: lo
ingestado está en los dos stores y el resumen quedó en el trabajo. Con
200.000 respuestas eso no es despreciable.

```sql
-- Cuánto ocupan hoy las filas guardadas.
select pg_size_pretty(sum(pg_column_size(filas))) as guardado,
       count(*) filter (where filas is not null)  as lotes_con_filas
  from ingesta_lote;

-- Liberar las de las cargas terminadas hace más de siete días.
select purgar_ingestas_terminadas();
```

La función es idempotente, no toma parámetros obligatorios y **conserva las
filas de los lotes fallidos** —son las que harían falta para reintentarlos—.
Conviene engancharla a una rutina semanal.

> **Queda pendiente y vale decirlo:** igual que
> `purgar_convocatorias_externas()` de la Fase 5, esta función existe y está
> probada pero **no está enganchada a ninguna rutina**. Hoy hay que correrla a
> mano.

---

## Rollback

Coordinarlo sin cargas activas. Mantener VPC_CONNECTOR exportado durante el
deploy del código anterior. No aceptar borrados de funciones ajenas al cambio.


El código y la base vuelven atrás juntos, y **en este orden**:

```bash
# 1 · Primero el código: con la 0019 revertida y el código nuevo arriba,
#     confirmar una carga falla al escribir en una tabla que no existe.
firebase deploy --only functions --project="$PROYECTO"   # desde el commit anterior

# 2 · Y la función de tareas, que ya no tiene razón de existir.
firebase functions:delete procesaringesta --region="$REGION" --project="$PROYECTO"
```

```sql
-- 3 · Y recién entonces la base.
begin;
drop view     if exists v_ingesta_progreso;
drop function if exists purgar_ingestas_terminadas(int);
drop table    if exists ingesta_lote;
drop table    if exists ingesta_trabajo;
drop table    if exists estado_lote_ingesta;
drop table    if exists estado_ingesta;
commit;
```

**Lo que el rollback pierde**, y conviene saberlo antes: el registro de todas
las cargas en diferido —qué se cargó, cuándo, qué lote falló y por qué—. Lo
**ingestado** no se toca: está en los dos stores y no depende de estas
tablas.

> **Si hay una carga en curso cuando se hace el rollback**, sus lotes
> pendientes quedan encolados contra una función que ya no existe. Cloud
> Tasks los reintenta y los descarta. Lo que entró, entró; lo que faltaba hay
> que volver a cargarlo, y ahora por la vía sincrónica, con el límite de
> tamaño que esa vía tiene.

---

## Errores y correcciones

| Error | Corrección |
|---|---|
| `fe_sendauth: no password supplied` | DSN IAM de COLOQUIO, paso 1.3. |
| `getAccessToken denied` | Autorizar la impersonación de COLOQUIO. |
| `zsh: command not found: #` | `setopt INTERACTIVE_COMMENTS`. |
| `Secret Payload cannot be empty` | Definir URL y CUENTA, paso 0. |
| Selector de condiciones IAM | `--condition=None` para estos bindings. |
| «el de siempre» | Recuperar el conector VPC real, paso 7.1. |
| `Queue ID "procesar_ingesta" can contain only letters…`, con `api` ya actualizada | Quedó una versión del código con la función nombrada con guion bajo. La cola la crea el CLI con el id de la función: renombrarla a `procesaringesta` y volver a desplegar. |
| `Listed 0 items` en colas | Crear explícitamente la cola, paso 5.4. |
| `iam.serviceAccounts.actAs` denegado | Dar a la cuenta real de api Service Account User sobre la cuenta OIDC. |
| Tarea recibe 403 | Revisar invoker y cuenta OIDC. |
| Tarea recibe 404 | Corregir URL y redesplegar api. |
| `Falta TAREAS_URL` | Revisar secreto, declaración en SECRETOS y deploy. |
| `no se pudo conectar al store «…»`, la tarea muere a los **127 s** | La función quedó sin conector VPC: el SYN no llega a la IP privada (127 s = `tcp_syn_retries=6`). Verificar las dos funciones, paso 7.2. |
| Timeout de Cloud SQL | Revisar conector VPC y DSN. |
| Función fuera de lista: `purgar_ingestas_terminadas` | Revisar los dos REVOKE de la migración 0019. |
| Carga guardada sin tareas | Revisar `sin_encolar`, corregir la causa y comprobar qué lotes permite reintentar la aplicación. |

## Checklist

- [ ] Variables definidas en la terminal actual.
- [ ] Proxies y DSN de ambas bases correctos.
- [ ] Token IAM de COLOQUIO vigente.
- [ ] Migración 0019 aplicada, incluidos REVOKE.
- [ ] Esquema actualizado y siete chequeos de solo lectura aprobados.
- [ ] TAREAS_URL termina en /procesaringesta.
- [ ] TAREAS_CUENTA contiene la cuenta OIDC elegida.
- [ ] Cola procesaringesta en RUNNING, tres tareas simultáneas y cinco intentos.
- [ ] Cuenta real de api con permiso de encolado y actAs sobre la cuenta OIDC.
- [ ] Conector VPC exportado antes de cada deploy.
- [ ] **Verificado** que api y procesaringesta devuelven el mismo conector.
- [ ] Función procesaringesta desplegada y permiso invoker aplicado.
- [ ] API redesplegada si cambió el secreto.
- [ ] Carga pequeña finaliza incluso tras recargar la página.
- [ ] Recuperación validada en pruebas o anotada como pendiente.
- [ ] Purga realizada cuando corresponde; automatización semanal pendiente si no existe.

## Referencias

- [Encolador](https://github.com/araujomelogno/paneles/blob/main/functions/panel_api/diferida.py)
- [Funciones](https://github.com/araujomelogno/paneles/blob/main/functions/main.py)
- [Verificación COLOQUIO](https://github.com/araujomelogno/paneles/blob/main/scripts/verificar_coloquio.py)
- [Cloud SQL IAM](https://docs.cloud.google.com/sql/docs/postgres/iam-logins)
- [Crear colas](https://docs.cloud.google.com/sdk/gcloud/reference/tasks/queues/create)
- [Crear tareas y permisos](https://docs.cloud.google.com/tasks/docs/create-tasks)
