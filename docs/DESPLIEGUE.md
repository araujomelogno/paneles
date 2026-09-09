# Manual de despliegue

Cómo poner el sistema de gestión de paneles en producción, de cero, y qué se
configura en cada lugar. Proyecto Firebase: **`gestion-paneles`**.

Los comandos son para macOS/Linux. Cada paso dice **dónde** se configura la
cosa, porque están repartidas en cuatro lugares distintos y esa es la parte
que más confunde.

> **Revisión 2026-09 (comandos gcloud/Cloud SQL).** Corregidos: `create` de
> Cloud SQL (edición Enterprise + `--ssl-mode`, ver 4.2), lectura de IP privada
> (4.4), y **consistencia de región** entre función, conector y bases (4.2).
> Postgres 16 crea por defecto instancias **Enterprise Plus**, que no admite
> tiers de núcleo compartido: por eso va `--edition=ENTERPRISE`. Se agregó el
> toolchain de **macOS/Homebrew** (paso 1: `cloud-sql-proxy`, `libpq`, ADC) que
> `gcloud sql connect` (paso 6) da por sentado.

---

## 0 · El mapa

Cinco piezas. Dos viven en Firebase, dos en GCP, una es un proveedor externo.

| Pieza | Qué hace | Dónde se configura |
|---|---|---|
| **Hosting** | Sirve la SPA de administración | `firebase.json` + `web/public/` |
| **Cloud Functions** (`api`) | Toda la lógica; único que toca las bases | `functions/main.py` + secretos |
| **Auth + Firestore** | Login del personal de Equipos y su rol | Consola Firebase + `firestore.rules` |
| **Cloud SQL × 2** | Los datos: bóveda y semántica | Consola GCP (**no** en el repo) |
| **Voyage** | Embeddings | Secreto `EMBEDDINGS_API_KEY` |

Lo importante de entender: **el repo no sabe nada de Cloud SQL.** El código
recibe dos cadenas de conexión (`DSN_BOVEDA` y `DSN_SEMANTICA`) y le da igual
de dónde salgan. Las instancias se crean en GCP y se enganchan por secretos.

### Las dos bases, y por qué son dos

- **Bóveda** — PII de los panelistas, paneles, membresías, consentimiento,
  participación, gamificación. Postgres común, sin extensiones.
- **Semántica** — embeddings y `id_persona`. Postgres **con pgvector**.

Son **dos instancias de Cloud SQL distintas**, nunca dos bases de la misma
instancia. No es preferencia: es el invariante de privacidad de `CLAUDE.md`.
`config.cargar()` se niega a arrancar si los dos DSN apuntan al mismo lado, y
hay una prueba que lo cubre.

---

## 1 · Requisitos previos

```bash
node --version        # 20 o superior
python3.11 --version  # el runtime de las functions es python311
firebase --version    # npm install -g firebase-tools
gcloud --version      # https://cloud.google.com/sdk/docs/install
```

En **macOS con Homebrew**, la instalación completa (incluye lo que el paso 6
necesita para conectar a las bases):

```bash
brew install python@3.11 node libpq cloud-sql-proxy
brew install --cask google-cloud-sdk
npm install -g firebase-tools
brew link --force libpq          # deja psql en el PATH (libpq es keg-only)
```

> **Notas de macOS/Homebrew** (aprendidas a los tropezones):
> - `gcloud` por Homebrew trae el **gestor de componentes deshabilitado**, así
>   que `gcloud components install ...` NO funciona. Por eso `cloud-sql-proxy` y
>   `psql` se instalan por brew aparte, no como componentes de gcloud.
> - `gcloud sql connect` (paso 6) necesita **`cloud-sql-proxy` (v2)** y **`psql`**
>   en el PATH; sin ellos falla con "Cloud SQL Proxy couldn't be found" o
>   "Psql client not found".
> - Si `gcloud` no aparece tras instalar el cask, reiniciá la terminal o agregá
>   su path al `~/.zshrc`.

Necesitás rol de **Owner** o **Editor** en el proyecto GCP, y permiso de
facturación: Cloud SQL y Cloud Functions no corren en el plan gratuito.

```bash
firebase login
gcloud auth login
gcloud auth application-default login   # ADC: las usa el proxy (paso 6) y el alta de usuarios (paso 9.3)
gcloud config set project gestion-paneles
```

> **`gcloud auth login` vs `application-default login`.** El primero te autentica
> a *vos* para los comandos de gcloud; el segundo deja un archivo de credenciales
> (ADC) que levantan solas las bibliotecas y binarios, como el Cloud SQL Auth
> Proxy. Si falta el segundo, el proxy del paso 6 corta con
> `could not find default credentials`.
>
> Si alguna acción sensible (crear Cloud SQL, tocar facturación/IAM) te pide
> **reautenticar** con un loop de "Please enter your password", no contestes en
> la terminal: corré `gcloud auth login` de nuevo y completá por el navegador.

---

## 2 · Proyecto Firebase y plan de facturación

El proyecto ya está fijado en `.firebaserc`. Si todavía no existe:

```bash
firebase projects:create gestion-paneles --display-name "Gestión de paneles"
firebase use gestion-paneles
```

En la consola, **Configuración del proyecto → Uso y facturación → Plan**:
pasar a **Blaze**. Cloud Functions de 2ª generación no despliega en Spark.

### App web

```bash
firebase apps:create web "Admin de paneles"
firebase apps:sdkconfig web        # imprime la config
```

Esa config va en `web/public/index.html`, en `window.firebaseConfig`. Ya está
puesta; si rehacés la app hay que actualizar `apiKey`, `messagingSenderId` y
`appId`.

> Mientras `apiKey` valga `TU_API_KEY`, la SPA arranca en **modo demo** con
> datos en memoria. Es el comportamiento buscado, no un error.

---

## 3 · Habilitar las APIs

`firebase deploy` habilita algunas solas, pero conviene adelantarlas para que
no falle a mitad de camino:

```bash
gcloud services enable \
  cloudfunctions.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  sqladmin.googleapis.com \
  vpcaccess.googleapis.com \
  servicenetworking.googleapis.com \
  compute.googleapis.com
```

---

## 4 · Las dos instancias de Cloud SQL

### 4.1 · Red privada

La función alcanza las bases por **IP privada**, a través de la VPC. Eso
exige preparar antes el rango que Google usa para el peering de servicios; si
este paso falta, la creación de las instancias con `--network` falla.

```bash
gcloud compute addresses create google-managed-services-default \
  --global --purpose=VPC_PEERING --prefix-length=16 --network=default

gcloud services vpc-peerings connect \
  --service=servicenetworking.googleapis.com \
  --ranges=google-managed-services-default --network=default
```

### 4.2 · Crear las instancias

Elegí la región **una vez** y usá la misma en todo (instancias, conector,
functions). Para Uruguay, `southamerica-east1` (São Paulo) es la más cercana.

> ⚠️ **Consistencia de región (código + infra).** El conector de VPC **tiene
> que estar en la misma región que la función**, o el `firebase deploy` (paso
> 10) falla y la función no llega a las bases. Y el código trae la función en
> `us-central1` por defecto, en **dos** lugares que hay que cambiar a tu región:
>
> - `functions/main.py`: `REGION = "us-central1"` → `REGION = "southamerica-east1"`
> - `firebase.json`: en el rewrite de `/api/**`, `"region": "us-central1"` → `"region": "southamerica-east1"`
>
> Hacé estos dos cambios **antes** de crear el conector (paso 5) y de desplegar.
> Con eso, instancias, conector y función quedan todas en `southamerica-east1`
> —lo mejor para latencia y para URCDP, porque la PII no sale de la región—.
> (Alternativa sin tocar código: dejar todo en `us-central1`, pero ahí la PII
> queda en EE.UU., que es justo lo que URCDP mira con lupa.)

> **Pendiente de definir:** dónde vive la PII es tema URCDP. Está anotado como
> decisión abierta en el README y conviene cerrarlo antes de cargar datos
> reales — mover una instancia de región después es un export/import.

Las instancias llevan **IP privada** (por ahí las alcanza la función) y además
**IP pública sin ninguna red autorizada**. La combinación suena rara pero es
deliberada: con la lista de redes autorizadas vacía, Cloud SQL **rechaza
todas** las conexiones públicas; lo único que entra es `gcloud sql connect`,
que agrega tu IP mientras dura la sesión y la saca al salir. Sin eso no hay
forma de aplicar las migraciones desde tu máquina, porque a una instancia con
IP privada **solamente** solo se llega desde adentro de la VPC.

```bash
REGION=southamerica-east1

# Bóveda: PII + paneles.
gcloud sql instances create paneles-boveda \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-g1-small \
  --region=$REGION \
  --storage-auto-increase \
  --network=default \
  --ssl-mode=ENCRYPTED_ONLY \
  --backup-start-time=04:00 \
  --enable-point-in-time-recovery

# Semántica: embeddings.
gcloud sql instances create paneles-semantica \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-g1-small \
  --region=$REGION \
  --storage-auto-increase \
  --network=default \
  --ssl-mode=ENCRYPTED_ONLY \
  --backup-start-time=04:30
```

La bóveda lleva **point-in-time recovery** y la semántica no, a propósito: la
bóveda tiene el dato irrecuperable (PII, consentimiento), la semántica se
puede reconstruir re-ingestando los estudios.

`db-g1-small` alcanza para arrancar. Si la búsqueda vectorial se pone lenta,
lo que hay que subir es la memoria de la **semántica** (el índice HNSW quiere
entrar en RAM), no la de la bóveda.

#### Si querés cerrar del todo la IP pública

Es la postura más estricta, y la que mejor cumple el «acceso bloqueado» de
`CLAUDE.md`. El costo es que las migraciones dejan de correrse desde tu
máquina: hace falta una VM chica en la misma VPC con el Cloud SQL Auth Proxy
y `--private-ip`.

```bash
# Una vez aplicadas las migraciones (paso 6):
gcloud sql instances patch paneles-boveda    --no-assign-ip
gcloud sql instances patch paneles-semantica --no-assign-ip
```

Recomendación práctica: arrancá con IP pública sin redes autorizadas, aplicá
las migraciones, y cerrala cuando el esquema esté estable.

### 4.3 · Bases y usuario

```bash
gcloud sql databases create paneles_boveda    --instance=paneles-boveda
gcloud sql databases create paneles_semantica --instance=paneles-semantica

# Una clave distinta por instancia: si se filtra una, la otra no cae.
gcloud sql users create app_paneles --instance=paneles-boveda    --password='...'
gcloud sql users create app_paneles --instance=paneles-semantica --password='...'
```

### 4.4 · Anotá las IP privadas

Las vas a necesitar para armar los DSN:

Filtrá por tipo: con IP pública y privada a la vez, el orden de la lista no
está garantizado y la que necesitás es la **privada**.

```bash
# Lista las IP de la instancia; la privada es la 10.x.x.x
gcloud sql instances describe paneles-boveda \
  --format="value(ipAddresses[].ipAddress)"
gcloud sql instances describe paneles-semantica \
  --format="value(ipAddresses[].ipAddress)"
```

Para aislar solo la privada (no sirve `--filter`: es un flag de `list`, no de
`describe`), usá JSON + Python:

```bash
gcloud sql instances describe paneles-boveda --format=json | \
  python3 -c "import json,sys; print([i['ipAddress'] for i in json.load(sys.stdin)['ipAddresses'] if i['type']=='PRIVATE'][0])"
```

---

## 5 · El conector de VPC

Es la pieza que le falta a mucha gente y sin la cual **la función no llega a
las bases**. Cloud Functions corre fuera de tu VPC; el conector es el puente.

```bash
gcloud compute networks vpc-access connectors create paneles-conn \
  --region=$REGION --network=default --range=10.8.0.0/28
```

El `--range` es un /28 libre, que no se pise con nada de tu VPC.

> **Por qué este camino y no el socket `/cloudsql/...`:** la librería
> `firebase-functions` para Python **no expone la opción
> `cloud_sql_instances`**. Sus opciones son `region, memory, timeout_sec,
> min_instances, max_instances, concurrency, cpu, vpc_connector,
> vpc_connector_egress_settings, service_account, ingress, labels, secrets,
> enforce_app_check, preserve_external_changes, invoker, cors`. Para usar el
> socket habría que atarlo por fuera con `gcloud run services update
> --add-cloudsql-instances`, y el siguiente `firebase deploy` te lo puede
> pisar. El conector de VPC sí se declara desde el código, así que el deploy
> es reproducible.

### Cómo lo toma el código

`functions/main.py` lo lee del entorno **del deploy**, no del runtime:

```python
VPC_CONNECTOR = os.environ.get("VPC_CONNECTOR") or None
```

Es decir: hay que exportarlo en la terminal desde donde corrés
`firebase deploy` (paso 10). Si no está, la función se despliega **sin**
conector y no va a poder abrir ninguna conexión.

---

## 6 · Aplicar las migraciones

`gcloud sql connect` abre una sesión de `psql` agregando tu IP a las redes
autorizadas mientras dura, y sacándola al salir. Por eso las instancias del
paso 4.2 tienen IP pública con la lista vacía.

> **Requisitos de este paso (macOS):** `gcloud sql connect` lanza el Cloud SQL
> Auth Proxy y un cliente `psql`, y ambos tienen que estar en el PATH —los
> instalaste en el paso 1 (`cloud-sql-proxy`, `libpq`)—. El proxy usa las ADC,
> así que `gcloud auth application-default login` (paso 1) tiene que estar hecho,
> o corta con `could not find default credentials`.

```bash
gcloud sql connect paneles-boveda --user=app_paneles --database=paneles_boveda
```
```
\i db/boveda/0001_init.sql
\i db/boveda/0002_revision_alta.sql
\i db/boveda/0003_baja_persona.sql
\q
```

```bash
gcloud sql connect paneles-semantica --user=app_paneles --database=paneles_semantica
```
```
\i db/semantica/0001_init.sql
\q
```

El orden importa: `0002` y `0003` referencian tablas que crea `0001`.

**Si `gcloud sql connect` sigue fallando en macOS** (a veces no encuentra el
proxy pese al PATH), levantá el Auth Proxy a mano y conectá con `psql` directo
—autentica por ADC, no por IP, así que funciona con las redes autorizadas
vacías—:

```bash
gcloud sql instances describe paneles-boveda --format='value(connectionName)'
# → gestion-paneles:southamerica-east1:paneles-boveda

# Terminal 1 (dejalo corriendo):
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda --port 5432
# Terminal 2:
psql "host=127.0.0.1 port=5432 user=app_paneles dbname=paneles_boveda"
```

Si ya cerraste la IP pública, `gcloud sql connect` no entra. En ese caso las
migraciones se corren desde una VM en la misma VPC:

```bash
gcloud compute instances create migrador --zone=$REGION-a --machine-type=e2-micro
gcloud compute scp --recurse db/ migrador:~/ --zone=$REGION-a
gcloud compute ssh migrador --zone=$REGION-a
#   en la VM: psql contra la IP privada, o el Auth Proxy con --private-ip
```

### Dónde se define que la semántica es vectorial

En la primera línea de `db/semantica/0001_init.sql` y en el tipo de la
columna. No hay ningún flag de instancia que habilitar: en Cloud SQL,
`CREATE EXTENSION vector` funciona directo desde PostgreSQL 12.

```sql
create extension if not exists vector;
...
embedding vector(1024) not null,
create index on respuesta using hnsw (embedding vector_cosine_ops);
```

**El `1024` tiene que coincidir en tres lugares** o la ingesta rompe al
escribir: la columna, la variable `EMBEDDINGS_DIMS`, y lo que devuelva el
modelo. Cambiar de modelo a otra dimensión no es un `ALTER`: es una migración
con re-embedding de todo el histórico.

### Verificar

```sql
-- en la semántica
select extversion from pg_extension where extname = 'vector';
\d respuesta
-- en la bóveda
\dt
```

La bóveda tiene que mostrar 13 tablas; la semántica, 4.

---

## 7 · Los secretos

Van a **Secret Manager**, nunca al repo. Son tres y la función los declara en
su manifiesto, así que **tienen que existir antes del primer deploy** o el
deploy falla.

```bash
# Ojo con el -n: sin él te llevás un salto de línea adentro del DSN.
echo -n "postgresql://app_paneles:CLAVE@10.x.x.x:5432/paneles_boveda" \
  | firebase functions:secrets:set DSN_BOVEDA --data-file -

echo -n "postgresql://app_paneles:CLAVE@10.y.y.y:5432/paneles_semantica" \
  | firebase functions:secrets:set DSN_SEMANTICA --data-file -

echo -n "pa-tu-api-key-de-voyage" \
  | firebase functions:secrets:set EMBEDDINGS_API_KEY --data-file -
```

Si todavía no tenés Cloud SQL y querés que el deploy pase igual, creálos con
`pendiente` como valor. La función despliega y el Hosting queda andando; la
API va a devolver error de config hasta que pongas los valores reales.

```bash
firebase functions:secrets:access DSN_BOVEDA   # ver el valor actual
firebase functions:secrets:set DSN_BOVEDA      # cambiarlo (crea versión nueva)
```

Un cambio de secreto **no aplica solo**: hay que volver a desplegar la función
para que tome la versión nueva.

---

## 8 · Voyage

Sacá la API key en [dash.voyageai.com](https://dash.voyageai.com) y guardala
como `EMBEDDINGS_API_KEY` (paso 7). Con eso alcanza: el modelo y la dimensión
tienen default.

| Variable | Default | Para qué |
|---|---|---|
| `EMBEDDINGS_PROVEEDOR` | `voyage` | `deterministico` para pruebas sin red |
| `EMBEDDINGS_MODELO` | `voyage-3.5` | |
| `EMBEDDINGS_DIMS` | `1024` | Tiene que coincidir con la columna |
| `EMBEDDINGS_API_KEY` | — | Secreto |

El proveedor está detrás de una interfaz (`panel_api/embeddings.py`): cambiar
a otro es implementar `embeber(textos) -> [[float]]` y registrarlo en
`crear()`. El resto del código no se entera.

Para probar la ingesta sin gastar API, `EMBEDDINGS_PROVEEDOR=deterministico`
genera vectores de 1024 dimensiones a partir de un hash. No tienen sentido
semántico, pero sirven para verificar que el cruce entre stores funciona.

---

## 9 · Auth, Firestore y el padrón de usuarios

### 9.1 · Authentication

Consola Firebase → **Authentication** → Comenzar → habilitar **Correo
electrónico/contraseña**. No hace falta nada más.

### 9.2 · Firestore

Consola → **Firestore Database** → Crear base de datos → **modo producción**,
misma región que el resto.

Firestore guarda **una sola cosa**: el padrón de usuarios de la app
(`usuarios/{uid}`), que es personal de Equipos. Ningún dato de panelista.
`firestore.rules` deja todo cerrado salvo que cada quien lea su propia ficha;
las escrituras van por el Admin SDK.

### 9.3 · Dar de alta a las personas

Cada usuario necesita dos cosas: cuenta en Auth y ficha en Firestore con su
rol. El script hace las dos y es idempotente:

```bash
npm install firebase-admin
gcloud auth application-default login

node scripts/alta_usuario.js ana@equipos.com.uy admin "Ana Pérez"
```

| Rol | Puede |
|---|---|
| `admin` | Todo |
| `operaciones` | Enrolar, paneles, membresías, convocar, fieldear |
| `analista` | Fieldear, ingestar, consultar. **No** enrola ni da de baja |
| `dpo` | Cumplimiento: retiros de consentimiento y bajas. **No** gestiona paneles |

La clave inicial se imprime **una sola vez**. Conviene que la persona la
cambie desde «¿Olvidaste tu contraseña?» en el login.

Sin ficha en el padrón, quien entre ve «Sin acceso» aunque su cuenta de Auth
sea válida. Es a propósito.

---

## 10 · El deploy

```bash
export VPC_CONNECTOR=paneles-conn     # ⚠️ sin esto la función no llega a las bases
firebase deploy
```

Qué pasa por dentro:

1. El `predeploy` de `firebase.json` crea `functions/venv` e instala
   `requirements.txt`. `firebase-tools` lo necesita para importar el código
   Python y descubrir las funciones. Sin venv falla con *«Missing virtual
   environment at venv directory»*.
2. Descubre el manifiesto importando `main.py`.
3. Sube reglas de Firestore, la función y el Hosting.

Por partes, si querés ir de a poco:

```bash
firebase deploy --only hosting            # solo la SPA; no toca nada de GCP
firebase deploy --only functions
firebase deploy --only firestore:rules
```

`--only hosting` no necesita ni secretos ni conector: sirve para ver la
interfaz andando antes de tener bases.

### Comprobar que el conector quedó puesto

Antes de desplegar, mirá que el manifiesto lo traiga:

```bash
cd functions && VPC_CONNECTOR=paneles-conn venv/bin/python -c "
import threading, time, urllib.request
from firebase_functions.private import serving
app = serving.serve_admin()
threading.Thread(target=lambda: app.run(port=8099), daemon=True).start()
time.sleep(2)
print(urllib.request.urlopen('http://127.0.0.1:8099/__/functions.yaml').read().decode())
"
```

Tiene que aparecer:

```yaml
    vpc:
      connector: paneles-conn
      egressSettings: PRIVATE_RANGES_ONLY
```

---

## 11 · Verificación

En orden, porque cada paso depende del anterior:

1. **Hosting** — abrí `https://gestion-paneles.web.app`. Tiene que aparecer el
   login de Equipos, **sin** el banner celeste de modo demo. Si aparece el
   banner, la `apiKey` de `index.html` sigue en `TU_API_KEY`.
2. **Auth** — entrá con el usuario del paso 9.3. Si dice «Sin acceso», falta
   la ficha en Firestore.
3. **Bóveda** — la página de Panelistas carga (vacía, pero carga). Eso prueba
   que la función abrió `DSN_BOVEDA` a través del conector.
4. **Semántica** — Cumplimiento → **Auditar ahora**. Tiene que decir
   «0 columnas de PII en el store semántico». Eso prueba `DSN_SEMANTICA`.
5. **El circuito entero** — creá un panel, enrolá un panelista con las dos
   finalidades de consentimiento, creá una encuesta, convocá al panel,
   ingestá un CSV chico y mirá **Verificar** en el cruce entre stores. Si los
   números cierran, Voyage y las dos bases están andando.

Un CSV mínimo para el paso 5, donde `R-001` es el `id_en_origen` que le
pusiste al enrolar:

```csv
id_en_origen,P1
R-001,Fernet
```

### Logs

```bash
firebase functions:log --only api
gcloud run services logs read api --region=southamerica-east1 --limit=50
```

---

## 12 · Actualizar y volver atrás

```bash
git pull && export VPC_CONNECTOR=paneles-conn && firebase deploy
```

- **Hosting:** Consola → Hosting → historial de versiones → *Revertir*.
- **Función:** no tiene rollback de un clic. Se despliega el commit anterior.
- **Migraciones:** son incrementales y no hay `down`. Volver atrás un cambio
  de esquema es escribir la migración inversa. Antes de una migración fea,
  `gcloud sql backups create --instance=paneles-boveda`.

---

## 13 · Problemas frecuentes

**`Missing virtual environment at venv directory`**
El `predeploy` no corrió o `python3.11` no está en el PATH. A mano:
`cd functions && python3.11 -m venv venv && venv/bin/pip install -r requirements.txt`.

**`Secret DSN_BOVEDA not found`**
El secreto no existe. Creálo (paso 7), aunque sea con un valor de relleno.

**La API devuelve 500 y en los logs hay timeout de conexión**
Casi siempre es el conector: desplegaste sin `export VPC_CONNECTOR=...`.
Comprobalo con `gcloud run services describe api --region=southamerica-east1 --format='value(spec.template.metadata.annotations)'` y volvé a desplegar con la variable puesta.

**`DSN_BOVEDA y DSN_SEMANTICA apuntan a la misma base`**
Es un chequeo a propósito. Los dos stores tienen que estar en instancias
distintas.

**`Se intentó escribir PII en el store semántico`**
El guardrail hizo su trabajo: algo del payload de ingesta trae una clave
identificatoria. El detalle del error dice cuál. No lo desactives: sacá el
campo del archivo de origen.

**El login entra pero todo dice «Sin acceso»**
Falta `usuarios/{uid}` en Firestore, o tiene `activo: false`, o el `rol` no es
uno de los cuatro válidos.

**`The default Firebase app already exists`**
No debería pasar; `main.py` ya inicializa de forma idempotente. Si aparece,
hay otro `initialize_app()` dando vueltas.

---

## 14 · Desarrollo sin desplegar

**Solo la interfaz**, con datos de ejemplo en memoria — poné `apiKey` en
`TU_API_KEY` y:

```bash
cd web/public && python3 -m http.server 8099
```

**Las pruebas del backend**, contra un Postgres real con pgvector:

```bash
pip install "psycopg[binary]" pytest
source scripts/pg_pruebas.sh      # levanta el cluster y exporta los DSN
cd functions && python3 -m pytest # 88 pruebas
scripts/pg_pruebas.sh detener
```

**Los emuladores de Firebase:**

```bash
firebase emulators:start
```

Ojo: los emuladores levantan Auth, Firestore, Functions y Hosting, pero **no**
Cloud SQL. Para que la función tenga bases, exportá `DSN_BOVEDA` y
`DSN_SEMANTICA` apuntando a un Postgres local (el de `pg_pruebas.sh` sirve) y
`EMBEDDINGS_PROVEEDOR=deterministico` para no pegarle a Voyage.

---

## 15 · Checklist

```
[ ] Plan Blaze activo
[ ] APIs habilitadas (paso 3)
[ ] Peering de servicios de red hecho
[ ] paneles-boveda creada, con IP privada y sin redes autorizadas
[ ] paneles-semantica creada, con IP privada y sin redes autorizadas
[ ] Bases y usuario app_paneles en las dos
[ ] Región aplicada en código (main.py REGION + firebase.json rewrite) e infra
[ ] Conector paneles-conn creado
[ ] Migraciones aplicadas: 3 en la bóveda, 1 en la semántica
[ ] create extension vector confirmado en la semántica
[ ] DSN_BOVEDA, DSN_SEMANTICA y EMBEDDINGS_API_KEY en Secret Manager
[ ] Authentication con correo/contraseña habilitado
[ ] Firestore creada en modo producción
[ ] Al menos un usuario admin en el padrón
[ ] firebaseConfig real en web/public/index.html
[ ] export VPC_CONNECTOR=paneles-conn antes del deploy
[ ] firebase deploy sin errores
[ ] Los 5 pasos de verificación (paso 11) en verde
[ ] (opcional, cuando el esquema esté estable) IP pública cerrada
```
