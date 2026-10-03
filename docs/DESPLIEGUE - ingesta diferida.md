# Despliegue — La ingesta pasa a diferido con Cloud Tasks

**Sistema:** Gestión de paneles · ingesta
**Cubre:** `specs/SPEC_ingesta_diferida_cloud_tasks.md`
**Migración:** `db/boveda/0019_ingesta_diferida.sql` · **una sola base**
**Duración estimada:** 50 a 70 minutos, la mitad en `gcloud`
**Precondición:** la bóveda al día hasta la `0018` y el store semántico hasta
la `0006`

---

## Lo primero que hay que entender de este despliegue

**Hay infraestructura nueva, y es la primera vez.** Hasta ahora todo el
backend era una función HTTP. Desde acá hay **una segunda función** —la que
procesa un lote— y **una cola** que la invoca. Las dos cosas se despliegan con
`firebase deploy`, pero la cola solo existe después del primer deploy, y la
función que encola necesita saber **a qué URL** pegarle. Ése es el nudo de
este despliegue y el paso 5 existe para desatarlo.

**Y el orden importa más que de costumbre.** Si se despliega el código antes
de aplicar la migración, la primera carga que alguien confirme falla al
escribir en una tabla que no existe. Si se aplica la migración y no se
despliega, no cambia nada: la ingesta sigue sincrónica. El orden es
migración → deploy → configurar la URL → deploy otra vez.

### El camino, en una mirada

| # | Paso | Dónde |
|---|---|---|
| 1 | Túnel y DSN | Terminal |
| 2 | Confirmar que falta solo la `0019` | Terminal |
| 3 | Aplicar `boveda/0019` | Bóveda |
| 4 | Verificar el esquema y los privilegios | Bóveda |
| 5 | **Habilitar Cloud Tasks y resolver la URL** | gcloud |
| 6 | Permisos de la cuenta de servicio | gcloud |
| 7 | Desplegar las dos funciones | Terminal |
| 8 | **Probar una carga chica de punta a punta** | Navegador |
| 9 | **Probar que un lote fallido se reintenta solo** | Navegador + gcloud |
| 10 | Enganchar la purga del espacio temporal | gcloud |

**Los pasos 1 a 4 están ensayados** contra un Postgres levantado al estado
que deja la `0018`; las salidas de abajo son las de ese ensayo. Del 5 al 10
son de GCP y del navegador.

---

## Paso 1 · Túnel y DSN

Esta migración toca **solo la bóveda**, pero el diagnóstico mira las dos.

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &

export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"

psql "$DSN_BOVEDA" -tAc "select current_database()"   # → paneles_boveda
```

---

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

## Paso 4 · Verificar el esquema y los privilegios

```bash
python3 scripts/verificar_esquema.py      # → Las dos bases están al día.
python3 scripts/verificar_coloquio.py     # → Pasaron los 16 chequeos.
```

**Los dos, no solo el primero.** El segundo es el que comprueba que la
superficie externa no se abrió de más, y esta migración agrega una función
que por omisión habría quedado abierta.

Y los catálogos:

```bash
psql "$DSN_BOVEDA" -c "select codigo, etiqueta, terminal from estado_ingesta order by orden"
```

```
        codigo         |       etiqueta        | terminal
-----------------------+-----------------------+----------
 encolada              | Encolada              | f
 procesando            | Procesando            | f
 terminada             | Terminada             | t
 terminada_con_errores | Terminada con errores | t
 fallida               | Fallida               | t
```

Un estado o un destino inventados los rechaza la base, no el código:

```
ERROR:  insert or update on table "ingesta_trabajo" violates foreign key constraint "ingesta_trabajo_estado_fkey"
DETAIL:  Key (estado)=(inventado) is not present in table "estado_ingesta".

ERROR:  new row for relation "ingesta_trabajo" violates check constraint "ingesta_trabajo_destino_check"
```

---

## Paso 5 · Habilitar Cloud Tasks y resolver la URL

### 5.1 · La API

```bash
gcloud services enable cloudtasks.googleapis.com --project=gestion-paneles
```

### 5.2 · El nudo: la URL que todavía no existe

La función que encola necesita saber a qué URL pegarle, y esa URL es la de
la función que procesa, **que todavía no está desplegada**. Hay que romper el
círculo en dos pasos, y por eso el paso 7 despliega dos veces.

La forma de la URL es predecible, así que se puede escribir antes:

```
https://<REGION>-<PROYECTO>.cloudfunctions.net/procesaringesta
```

Para este proyecto:

```bash
PROYECTO=gestion-paneles
REGION=southamerica-east1
URL="https://$REGION-$PROYECTO.cloudfunctions.net/procesaringesta"
echo "$URL"
```

> **El nombre de la función es `procesaringesta`, todo junto y en
> minúsculas.** En Python el decorador la declara como `procesar_ingesta` y
> Firebase le saca el guión bajo al desplegarla. Si la URL lleva el guión, la
> tarea sale, Cloud Tasks recibe un 404 y lo reintenta cinco veces antes de
> darse por vencida: el síntoma es una carga que se queda en «procesando»
> para siempre sin un error visible en ningún lado. **Verificar el nombre
> real después del primer deploy** (paso 7.2) y no darlo por sentado.

### 5.3 · Cargar la URL y la cuenta

```bash
CUENTA="$PROYECTO@appspot.gserviceaccount.com"   # la cuenta por defecto

for SECRETO in TAREAS_URL TAREAS_CUENTA; do
  gcloud secrets create "$SECRETO" --replication-policy=automatic 2>/dev/null \
    || echo "$SECRETO ya existe"
done
printf '%s' "$URL"    | gcloud secrets versions add TAREAS_URL    --data-file=-
printf '%s' "$CUENTA" | gcloud secrets versions add TAREAS_CUENTA --data-file=-
```

> **Si `TAREAS_URL` queda vacía, nadie toma los lotes.** El sistema no se
> rompe de forma visible: la carga se guarda, los lotes quedan en
> `pendiente`, y la pantalla muestra una barra que no avanza. El código lo
> dice con todas las letras cuando no puede encolar —*«Falta `TAREAS_URL`…»*—
> y la respuesta de la confirmación trae el aviso, pero hay que mirarlo.

---

## Paso 6 · Permisos de la cuenta de servicio

La cuenta necesita tres cosas, y las tres fallan distinto si faltan:

```bash
# 1 · Poder encolar tareas.
gcloud projects add-iam-policy-binding "$PROYECTO" \
  --member="serviceAccount:$CUENTA" --role="roles/cloudtasks.enqueuer"

# 2 · Poder firmar el token OIDC con el que la tarea se autentica.
gcloud projects add-iam-policy-binding "$PROYECTO" \
  --member="serviceAccount:$CUENTA" --role="roles/iam.serviceAccountTokenCreator"

# 3 · Poder invocar la función que procesa.
gcloud functions add-invoker-policy-binding procesaringesta \
  --region="$REGION" --member="serviceAccount:$CUENTA" --project="$PROYECTO"
```

El tercero **se corre después del paso 7**, porque la función tiene que
existir. Los dos primeros, antes.

| Si falta | Qué se ve |
|---|---|
| `cloudtasks.enqueuer` | La confirmación responde igual, con `sin_encolar` y el aviso; los lotes quedan `pendiente` |
| `serviceAccountTokenCreator` | Idem: el `create_task` falla al armar el token |
| El invoker | La tarea sale y la función devuelve 403. Cloud Tasks reintenta cinco veces y el lote queda fallido con «403» en el error |

---

## Paso 7 · Desplegar

### 7.1 · Primer deploy, que crea la función y la cola

```bash
export VPC_CONNECTOR=<el de siempre>
firebase deploy --only functions
```

Despliega las dos: `api` (la de siempre) y `procesaringesta` (nueva).
**Firebase crea la cola de Cloud Tasks sola**, con el nombre de la función y
la concurrencia y los reintentos que están declarados en `main.py`:

```python
retry_config=options.RetryConfig(max_attempts=5, min_backoff_seconds=10, ...)
rate_limits=options.RateLimits(max_concurrent_dispatches=3)
```

> **Tres tareas en paralelo es conservador a propósito.** El techo real no es
> la función sino los límites de tasa de Voyage y la instancia de la base.
> Con `paneles-semantica` en un tier chico, subirlo degrada o fuerza a
> agrandarla (~US$ 24/mes de `db-f1-micro` a `db-g1-small`).

### 7.2 · Verificar el nombre real y corregir la URL si hace falta

```bash
gcloud functions list --project="$PROYECTO" --regions="$REGION" \
  --format='value(name)'
```

Si el nombre no es `procesaringesta`, hay que cargar la URL correcta y volver
a desplegar:

```bash
printf '%s' "https://$REGION-$PROYECTO.cloudfunctions.net/<nombre real>" \
  | gcloud secrets versions add TAREAS_URL --data-file=-
firebase deploy --only functions:api
```

Y la cola, que ya tiene que existir:

```bash
gcloud tasks queues describe procesaringesta --location="$REGION"
```

### 7.3 · El invoker, ahora que la función existe

El comando 3 del paso 6.

---

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
> gcloud tasks queues describe procesaringesta --location="$REGION"
> gcloud logging read \
>   'resource.labels.function_name="procesaringesta"' --limit=20
> ```

---

## Paso 9 · Probar que un lote fallido se reintenta

Lo que esta entrega promete no es solo que las cargas grandes terminen: es
que **un fallo parcial no obliga a rehacer todo**. Sin probarlo, eso es una
suposición.

La forma más limpia de provocarlo sin tocar datos es quitarle temporalmente a
la función el acceso al proveedor de embeddings:

```bash
# Una versión vacía de la clave: las llamadas fallan con un error transitorio.
printf '%s' '' | gcloud secrets versions add EMBEDDINGS_API_KEY --data-file=-
firebase deploy --only functions:procesaringesta
```

Ingestar una carga chica y mirar:

| # | Qué tiene que pasar |
|---|---|
| 9.1 | Los lotes quedan fallidos **después de cinco intentos**, no del primero |
| 9.2 | La pantalla dice qué lote falló y con qué error |
| 9.3 | La carga queda `terminada_con_errores` o `fallida`, no colgada |

Restaurar la clave, redesplegar, y entonces:

| # | Qué tiene que pasar |
|---|---|
| 9.4 | El botón **«Reintentar los lotes fallidos»** los reencola |
| 9.5 | Terminan bien y la carga pasa a `terminada` |
| 9.6 | **No se duplicó nada**: las respuestas son las mismas de antes |

```bash
psql "$DSN_SEMANTICA" -c "
select count(*) as respuestas, count(distinct (individuo_id, pregunta_id)) as pares
  from respuesta;"
```

Los dos números tienen que coincidir: la unicidad está en el esquema, así que
si difirieran sería un bug de otra cosa.

---

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

El código y la base vuelven atrás juntos, y **en este orden**:

```bash
# 1 · Primero el código: con la 0019 revertida y el código nuevo arriba,
#     confirmar una carga falla al escribir en una tabla que no existe.
firebase deploy --only functions   # desde el commit anterior

# 2 · Y la función de tareas, que ya no tiene razón de existir.
firebase functions:delete procesaringesta --region="$REGION"
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

## Errores que se pueden encontrar

| Mensaje | Qué pasó | Qué hacer |
|---|---|---|
| `Falta TAREAS_URL: sin la dirección de la función que procesa los lotes…` | El secreto no está cargado o no se montó | Paso 5.3, y redesplegar: `firebase deploy` solo monta los declarados |
| La respuesta trae `sin_encolar` y un aviso | La cola rechazó las tareas | Casi siempre permisos: paso 6. La carga quedó guardada y se puede reintentar |
| La barra no avanza y los lotes siguen `pendiente` | Nadie está tomando las tareas | `gcloud tasks queues describe` y los logs de la función. URL mal, cola inexistente, o invoker faltante |
| El lote falla con `403` | A la cuenta le falta el rol de invoker | Comando 3 del paso 6 |
| `Las filas de este lote ya se purgaron` | Se reintentó un lote de una carga vieja | Volver a cargar el archivo: no hay con qué reprocesarlo |
| `El lote N no está fallido: no hay nada que reintentar` | Se pidió reintentar algo que está bien | Nada |
| `funciones ejecutables fuera de la lista blanca: purgar_ingestas_terminadas` | Se aplicó la `0019` sin sus dos `REVOKE` finales | Correrlos a mano y volver a correr `verificar_coloquio.py` |
| Una carga queda en `procesando` sin lotes en curso | Una tarea murió sin marcar su lote | Reintentar desde la pantalla: `_tomar_lote` la vuelve a tomar |

---

## Checklist

- [ ] 1 · Túnel abierto y `DSN_BOVEDA` apuntando a `paneles_boveda`
- [ ] 2 · El diagnóstico dice que falta solo la `0019`
- [ ] 3 · `0019` aplicada, con los dos `REVOKE` al final
- [ ] 4 · «Las dos bases están al día» **y** `verificar_coloquio.py` 16/16
- [ ] 5 · API de Cloud Tasks habilitada, `TAREAS_URL` y `TAREAS_CUENTA` cargadas
- [ ] 6 · `cloudtasks.enqueuer` y `serviceAccountTokenCreator` otorgados
- [ ] 7 · Las dos funciones desplegadas; el **nombre real** de la función de tareas verificado
- [ ] 7 · La cola existe (`gcloud tasks queues describe`)
- [ ] 7 · El invoker otorgado, después del deploy
- [ ] 8 · **Una carga chica termina sola y el resumen es el de siempre**
- [ ] 8 · **Recargar la página a mitad de carga no pierde el progreso**
- [ ] 9 · **Un lote fallido se reintenta y no duplica nada**
- [ ] 10 · La purga corrida una vez a mano, y anotado que falta engancharla

**El despliegue no está hecho hasta el paso 9.** Los anteriores dejan la
infraestructura arriba; el 8 y el 9 son los que dicen si sirve.
