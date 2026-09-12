# Manual de despliegue — Fase 2

Cómo poner en producción la consulta semántica, la composición con brecha, el
tablero de participación y la gestión de usuarios desde la app.

Este manual **no repite** el de la Fase 1. Da por hecho que ya está andando lo
de `DESPLIEGUE -  Fase 1 .md`: proyecto Firebase `gestion-paneles`, las dos
instancias de Cloud SQL, el conector de VPC, Auth, Firestore y Voyage. Si eso
todavía no está, la Fase 2 no tiene dónde apoyarse.

Los comandos son para macOS/Linux, con el mismo toolchain del manual anterior
(`gcloud`, `cloud-sql-proxy`, `psql`, `firebase`; y para las verificaciones
de la sección 8, `curl` y `python3`, que vienen con macOS).

---

## 0 · Qué cambia

La Fase 2 no agrega infraestructura: no hay instancias nuevas, ni conectores
nuevos, ni bases nuevas. Agrega **dos proveedores externos**, **tres tablas**,
**una columna** y **cinco pantallas**.

| Pieza | Estado | Qué hay que hacer |
|---|---|---|
| Cloud SQL × 2 | Ya está | Aplicar 2 migraciones (paso 2) |
| Cloud Functions (`api`) | Ya está | Redesplegar con 2 secretos nuevos (pasos 3 y 7) |
| Hosting | Ya está | Se sube en el mismo deploy |
| Auth + Firestore | Ya está | Permisos a la cuenta de servicio (paso 5) |
| **Voyage rerank** | **Nuevo** | Clave en Secret Manager (paso 3) |
| **API de Claude** | **Nuevo** | Clave en Secret Manager (paso 4) |
| pgvector / índice HNSW | Ya está | Revisar el índice con corpus real (paso 6) |

### Las dos etapas que llaman a un modelo ajeno

La consulta semántica tiene tres llamadas a un proveedor externo, y conviene
tenerlas claras porque son las que cuestan plata y las que pueden fallar:

| Etapa | Proveedor | Qué hace | Si falta la clave |
|---|---|---|---|
| **Embedding del criterio** | Voyage `voyage-3.5` | Vectoriza la frase que se busca | La consulta **falla** |
| **Reranking** | Voyage `rerank-2.5` | Reordena el pool con un cross-encoder | **Degrada** y avisa |
| **Verificación** | API de Claude | Dice cumple / no cumple / dudoso | **Degrada** y avisa |

El embedding es el único que no puede degradar: sin vector no hay búsqueda.
Los otros dos sí, y el diseño es a propósito. **La degradación es explícita:**
la respuesta de la consulta trae un campo `degradaciones` con la etapa, el
proveedor, el motivo y la consecuencia, y la interfaz lo muestra como un aviso
ámbar arriba del ranking. Un ranking peor avisado se puede usar; un ranking
peor callado es un resultado falso.

> **Cuando la verificación está caída, el modo estricto no vacía el ranking.**
> Sin verificación todos los candidatos quedan en `dudoso`, y la regla del
> modo estricto («fuera el que no cumple») dejaría el resultado vacío. Excluir
> a todo el mundo por un juicio que nunca se emitió no es ser estricto: es
> devolver una lista vacía y llamarla resultado. Así que con la verificación
> caída el veredicto deja de excluir, y la respuesta lo dice.

---

## 1 · Antes de empezar

**La Fase 1 tiene que estar cerrada.** No es formalismo: la Fase 2 consulta el
corpus que la Fase 1 ingesta. Sin respuestas ingestadas, las consultas
devuelven vacío y no hay forma de distinguir «anda y no hay datos» de «no
anda».

```bash
# 1. Las pruebas pasan (256, todas verdes).
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest && cd ..

# 2. La región está declarada igual en todos lados.
python3 scripts/verificar_region.py

# 3. Hay corpus real del otro lado.
#    (con el proxy abierto contra la instancia semántica)
psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica -c "
  select count(*) as respuestas,
         count(distinct individuo_id) as individuos,
         count(distinct pregunta_id) as preguntas
    from respuesta;"
```

Si `respuestas` da 0, andá a fieldear e ingestar una ola antes de seguir. Con
menos de unos cuantos miles de respuestas la calibración del paso 8 no se
puede hacer con sentido.

### El consentimiento de uso semántico es bloqueante

Este es el punto que más frena despliegues de la Fase 2, y no es técnico.

Una consulta semántica solo puede ver a las personas con la finalidad
**`uso_semantico` vigente**, que es distinta de `contacto_participacion`. Si el
padrón se armó pidiendo solo el consentimiento de contacto, **las consultas van
a devolver vacío y el sistema va a estar funcionando bien**.

En la **bóveda** (`cloud-sql-proxy --port 5432 …:paneles-boveda` en otra
terminal):

```bash
psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda -c "
select finalidad, count(distinct id_persona) as personas
  from consentimiento
 where estado = 'vigente'
 group by finalidad;"
```

Si `uso_semantico` da mucho menos que `contacto_participacion`, hay que hacer
una campaña de re-consentimiento antes de que la Fase 2 sirva para algo. Es
trabajo legal y de campo, no de despliegue; conviene arrancarlo temprano.

---

## 2 · Las migraciones

Dos, una por store. Las dos son **aditivas**: no borran ni cambian nada de lo
que ya está, así que se pueden aplicar con la app andando.

```
db/boveda/0004_fase2.sql        consulta_guardada, usuario_auditoria,
                                reidentificacion
db/semantica/0003_hash_texto.sql  columna hash_texto en respuesta
```

### 2.1 · Bóveda

```bash
# Terminal 1 — el proxy contra la bóveda (dejalo corriendo).
cloud-sql-proxy --port 5432 \
  gestion-paneles:southamerica-east1:paneles-boveda

# Terminal 2
psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda \
  -v ON_ERROR_STOP=1 -f db/boveda/0004_fase2.sql
```

Qué crea y por qué está del lado de la bóveda:

- **`consulta_guardada`** — definiciones de consulta reutilizables. Se guarda
  la **definición**, nunca el resultado. Un resultado cacheado envejece mal —el
  padrón cambia, los consentimientos se retiran— y sería una lista de personas
  persistida sin volver a pasar por el gate.
- **`usuario_auditoria`** — cada alta, cambio de rol y desactivación de un
  usuario de la app, con autor y fecha. Es de solo agregar.
- **`reidentificacion`** — cada traducción deliberada de `id_persona` a datos
  de contacto. **Sin FK a `persona` a propósito:** el registro tiene que
  sobrevivir a la baja de la persona, que es justo el caso en que hace falta
  poder demostrar quién había visto sus datos.

Las tres nombran a personal de Equipos o a un panelista, así que son de bóveda.
Ninguna cruza al store semántico.

### 2.2 · Semántica

```bash
# Terminal 1 — ahora contra la instancia semántica.
cloud-sql-proxy --port 5433 \
  gestion-paneles:southamerica-east1:paneles-semantica

# Terminal 2
psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica \
  -v ON_ERROR_STOP=1 -f db/semantica/0003_hash_texto.sql
```

Agrega `respuesta.hash_texto`: el sha256 del texto que se vectorizó. Sirve para
**saltear el re-embedding** cuando una re-ingesta trae el mismo texto, que es
la parte del pipeline que cuesta plata.

- Es `alter table ... add column if not exists`, sin `not null` ni default, así
  que **no reescribe la tabla**: es instantáneo incluso con millones de filas.
- **No hay backfill.** Las respuestas anteriores quedan con `hash_texto` nulo,
  la ingesta las trata como «no sé qué había» y las re-embebe **una vez**; de
  ahí en adelante quedan con hash. Es el comportamiento anterior, no una
  regresión.
- `hash_texto` no es PII ni deriva de PII: `texto_embebido` es
  «pregunta → respuesta», ya despersonalizado.

### 2.3 · Verificar

```bash
# Bóveda: las tres tablas nuevas.
psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda -c "
  select table_name from information_schema.tables
   where table_schema='public'
     and table_name in ('consulta_guardada','usuario_auditoria','reidentificacion')
   order by 1;"

# Semántica: la columna nueva y, de paso, que el store siga limpio de PII.
psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica -c "
  select column_name from information_schema.columns
   where table_name='respuesta' order by ordinal_position;"
```

Tienen que salir las tres tablas y `hash_texto` al final de `respuesta`.

Para no depender de que uno se acuerde de qué tendría que estar —y para cubrir
de paso las migraciones de la Fase 1—, conviene correr el verificador del
repositorio, que compara las dos bases contra la lista completa de migraciones
que el código espera:

```bash
pip install "psycopg[binary]"   # el driver, si no está
# Los DSN, sin escribir la clave: ver «Armar el DSN sin escribir la clave»
# en el despliegue de la Fase 1.
export DSN_BOVEDA="$(dsn_local BOVEDA 5432)"
export DSN_SEMANTICA="$(dsn_local SEMANTICA 5433)"
python3 scripts/verificar_esquema.py
```

Sale con código 0 si las dos bases están al día. Si falta alguna migración, la
nombra, explica qué depende de ella e imprime el comando que la aplica. Con
`--sql semantica` (o `--sql boveda`) imprime la misma verificación como
consulta suelta, para pegar dentro de una sesión de `psql` ya abierta. Esa vía
no se conecta a ninguna base y no necesita el driver instalado.

Es la misma verificación que hace la aplicación en Cumplimiento → **Esquema de
las dos bases**; la diferencia es que el script sirve **antes** de desplegar,
cuando la función todavía no está arriba.

---

## 3 · La clave de reranking

El reranking usa el cross-encoder de Voyage. Es el mismo proveedor que los
embeddings, así que **la clave puede ser la misma**, pero se guarda como un
secreto aparte para poder rotarla o apagarla sin tocar la ingesta.

```bash
# Sacá (o reusá) la clave en https://dash.voyageai.com
echo -n "pa-tu-api-key-de-voyage" \
  | firebase functions:secrets:set RERANKER_API_KEY --data-file -
```

| Variable | Default | Para qué |
|---|---|---|
| `RERANKER_PROVEEDOR` | `voyage` | `lexico` para probar sin red; `ninguno` para apagarlo |
| `RERANKER_MODELO` | `rerank-2.5` | |
| `RERANKER_API_KEY` | — | Secreto |

El proveedor está detrás de una interfaz (`panel_api/reranker.py`), igual que
los embeddings: cambiar a otro es implementar
`reordenar(criterio, textos) -> [(indice, puntaje)]` y registrarlo en `crear()`.

### Para qué sirve, en una línea

El recall por vecino más cercano trae lo que **habla de** lo que se buscó. El
caso que lo muestra: «no me gusta el fernet» está tan cerca del criterio «gente
a la que le gusta el fernet» como «me encanta el fernet», porque comparten casi
todas las palabras. La distancia mide parecido temático, no si la respuesta
cumple. El reranker mira criterio y respuesta juntos y hunde al que dice lo
contrario.

---

## 4 · La clave de Claude

La verificación usa la API de Claude. Es la etapa que **se compromete**: sobre
el top-k dice, para cada individuo, si cumple, no cumple o es dudoso, y señala
la respuesta concreta que lo justifica.

```bash
# Sacá la clave en https://console.anthropic.com → API Keys
echo -n "sk-ant-..." \
  | firebase functions:secrets:set CLAUDE_API_KEY --data-file -
```

| Variable | Default | Para qué |
|---|---|---|
| `VERIFICACION_PROVEEDOR` | `claude` | `lexico` para probar sin red; `ninguno` para apagarla |
| `CLAUDE_MODELO` | `claude-sonnet-5` | |
| `CLAUDE_API_KEY` | — | Secreto |

### Qué se le manda y qué no

Esto conviene poder responderlo sin dudar, porque es la pregunta que va a
hacer cualquiera que mire el diseño:

- **Va:** el texto del criterio, y el texto de pregunta y respuesta de cada
  candidato. Todo eso sale del **store semántico**, que por el invariante
  central de `CLAUDE.md` no tiene PII.
- **No va:** ni `id_persona`. Los candidatos se numeran (`[0]`, `[1]`, …) y la
  correspondencia número → persona se queda en la función.

Y dos garantías que están en el **diseño**, no en el prompt:

1. **No puede inventar evidencia.** El modelo devuelve el *número* del
   candidato, no la cita. La cita se arma del `valor_texto` que ya estaba en la
   base. Si devuelve un número que no existe, ese veredicto se descarta y queda
   en `dudoso`. Un modelo que alucine una frase no tiene por dónde meterla en
   el resultado.
2. **La longitud y el orden los fija la lista de candidatos**, no la respuesta
   del modelo. Un candidato sobre el que el modelo no se pronuncia queda en
   `dudoso` con esa razón, no desaparece.

Hay una prueba automatizada de las dos cosas
(`test_el_verificador_no_puede_inventar_la_evidencia_que_cita`).

---

## 5 · Permisos de la cuenta de servicio

La solapa **Configuración** da de alta usuarios: crea la cuenta en Firebase
Auth y escribe su ficha en Firestore. Eso lo hace la función con su **cuenta de
servicio**, no con credenciales de nadie, así que la cuenta necesita permiso.

```bash
# La cuenta de servicio del runtime de la función (gen2 = Cloud Run).
gcloud run services describe api --region=southamerica-east1 \
  --format='value(spec.template.spec.serviceAccountName)'
```

Con ese valor (llamémoslo `$SA`):

```bash
PROYECTO=gestion-paneles
SA=$(gcloud run services describe api --region=southamerica-east1 \
      --format='value(spec.template.spec.serviceAccountName)')

# Crear y modificar usuarios de Firebase Auth.
gcloud projects add-iam-policy-binding "$PROYECTO" \
  --member="serviceAccount:$SA" --role="roles/firebaseauth.admin"

# Leer y escribir el padrón en Firestore.
gcloud projects add-iam-policy-binding "$PROYECTO" \
  --member="serviceAccount:$SA" --role="roles/datastore.user"
```

Si la cuenta ya tenía `roles/editor` (el default de proyectos viejos) esto es
redundante pero inofensivo. En proyectos con los grants automáticos
deshabilitados es **obligatorio**: sin esto, dar de alta un usuario falla con
`PERMISSION_DENIED` y el error no dice cuál permiso falta.

### Las reglas de Firestore no cambian

`firestore.rules` sigue como está, y está bien:

```
match /usuarios/{uid} {
  allow read: if request.auth != null && request.auth.uid == uid;
  allow write: if false;
}
```

El Admin SDK **no pasa por las reglas**, así que la función escribe igual. El
`write: if false` es para el cliente: la SPA nunca escribe el padrón, lo pide
por la API. Si alguien «arregla» esa regla para permitir escritura desde el
cliente, cualquier usuario autenticado podría darse rol `admin`.

### El primer admin sigue siendo por script

Un administrador no puede darse de alta a sí mismo desde una app a la que no
puede entrar. `scripts/alta_usuario.js` sigue siendo la vía de arranque y de
emergencia:

```bash
gcloud auth application-default login
node scripts/alta_usuario.js jefa@equipos.com.uy admin "Nombre Apellido"
```

---

## 6 · El índice vectorial con corpus real

En la Fase 1 el índice HNSW se creó con la tabla vacía, y con la tabla vacía
cualquier índice anda. Con corpus real hay dos cosas para mirar.

> **Dónde se corre todo lo de esta sección.** Dentro de una sesión de `psql`
> contra la **instancia semántica** (es donde vive `respuesta`):
>
> ```bash
> # Terminal 1 — el proxy contra la semántica (dejalo corriendo).
> cloud-sql-proxy --port 5433 \
>   gestion-paneles:southamerica-east1:paneles-semantica
>
> # Terminal 2 — la sesión interactiva.
> psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica
> ```
>
> En el prompt `paneles_semantica=>` pegás el SQL de abajo, terminado en `;`.
> Antes de sacar conclusiones, mirá cuántas filas tenés: `select count(*) from
> respuesta;`. Con un corpus chico, varios de estos chequeos dan «mal» sin que
> haya nada roto (ver la causa 2 de 6.1).

### 6.1 · ¿Está usando el índice?

```sql
-- Un vector cualquiera de la propia tabla sirve de sonda.
explain (analyze, buffers)
select id from respuesta
 order by embedding <=> (select embedding from respuesta limit 1)
 limit 200;
```

En el plan tiene que aparecer **`Index Scan using respuesta_embedding_idx`**.
Si aparece `Seq Scan` seguido de un `Sort`, el índice no se está usando y cada
consulta lee la tabla entera.

Causas habituales, en orden de frecuencia:

1. **El índice no se construyó** (la migración `0001` falló en silencio).
   `\d respuesta` lo muestra.
2. **La tabla es chica** y el planificador tiene razón: con pocos miles de
   filas el seq scan es más rápido. No es un problema.
3. `enable_indexscan` apagado en la sesión, o estadísticas viejas
   (`analyze respuesta`).

### 6.2 · Recall del índice

HNSW es **aproximado**: puede no devolver el vecino más cercano. Cuánto se
esfuerza lo controla `hnsw.ef_search` (default 40).

```sql
-- Por sesión, para probar.
set hnsw.ef_search = 100;

-- Fijo, si la prueba dice que hace falta. Va con ALTER DATABASE y no con las
-- marcas de Cloud SQL: `hnsw.ef_search` es un GUC de la extensión, y la
-- consola solo deja tocar los flags de Postgres que GCP tiene en su lista.
alter database paneles_semantica set hnsw.ef_search = 100;
```

Cómo decidir: corré la misma consulta con el índice y sin él
(`set enable_indexscan = off`) y comparé los conjuntos de ids. Si el índice se
pierde candidatos que después importaban, subí `ef_search`. Va contra latencia,
así que el valor sale de medir, no de elegirlo lindo.

Con el `top_n` por defecto (200) y un corpus de decenas de miles de respuestas,
el default de 40 suele alcanzar. Con `top_n` grande conviene
`ef_search >= top_n`.

### 6.3 · Si hay que reconstruir el índice

```sql
-- Construir HNSW es intensivo en memoria; con poca, tarda muchísimo.
set maintenance_work_mem = '2GB';
reindex index concurrently respuesta_embedding_idx;
```

`concurrently` no bloquea las escrituras, así que se puede hacer con la app
andando. Tarda.

---

## 7 · Redesplegar

Los secretos nuevos están **declarados en el manifiesto** de la función
(`functions/main.py`), así que **tienen que existir antes del deploy** o el
deploy falla. Si todavía no tenés una de las claves, creá el secreto con un
valor cualquiera —incluso `pendiente`— y la etapa correspondiente va a
degradar avisando en vez de romper.

```bash
# Si falta alguno, que exista aunque sea vacío.
firebase functions:secrets:set RERANKER_API_KEY   # o --data-file - con "pendiente"
firebase functions:secrets:set CLAUDE_API_KEY

export VPC_CONNECTOR=paneles-conn     # ⚠️ sin esto la función no llega a las bases
firebase deploy
```

Un cambio de secreto **no aplica solo**: hay que volver a desplegar la función
para que tome la versión nueva.

### 7.1 · Variables de runtime vs. de deploy

Es la distinción que más confunde, y la Fase 2 agrega cuatro variables nuevas,
así que conviene dejarla dicha:

| Dónde va | Qué es | Cuáles |
|---|---|---|
| **Secret Manager** | Secretos. Nunca en el repo. | `DSN_*`, `EMBEDDINGS_API_KEY`, `RERANKER_API_KEY`, `CLAUDE_API_KEY` |
| **`functions/.env`** | Variables de **runtime**: las lee el código al atender una request | `*_PROVEEDOR`, `*_MODELO`, `EMBEDDINGS_DIMS`, `PADRON_USUARIOS` |
| **La shell del deploy** | Variables de **deploy**: se leen al construir el manifiesto | `VPC_CONNECTOR` |

Un `export RERANKER_PROVEEDOR=lexico` en la terminal **no hace nada**: esa
variable la lee la función en producción, no el proceso que despliega.

Hay un ejemplo comentado en `functions/.env.ejemplo`. Para usarlo:

```bash
cp functions/.env.ejemplo functions/.env
# editá functions/.env y desplegá
```

Si no existe `functions/.env`, todo toma su default —Voyage para embeddings y
reranking, Claude para verificación— que es lo que se quiere en producción. El
archivo hace falta solo para apartarse de eso.

`.env` y `.env.*` están en `.gitignore`; el `.ejemplo` es la excepción.

### Qué se sube

- La función `api`, con la superficie nueva: `/consultas`,
  `/consultas/guardadas`, `/reidentificacion`,
  `/paneles/{id}/composicion`, `/paneles/{id}/objetivo`,
  `/paneles/{id}/participacion`, `/participacion/olas`, `/usuarios`.
- El Hosting, con las cuatro pantallas nuevas (Consultas, Composición,
  Participación, Configuración).

> **`PUT` en CORS.** La ruta del universo de referencia es `PUT`, y ese método
> se agregó a la lista de `cors_methods` en `main.py`. En producción no hace
> falta —Hosting reescribe `/api/**` y comparte origen— pero sin él el
> emulador y cualquier prueba de origen cruzado fallan con un preflight
> rechazado.

---

## 8 · Verificación y calibración

### 8.1 · Que responda

En orden, porque cada paso depende del anterior. Todo desde la app, con un
usuario de rol `admin`.

0. **El esquema, antes que nada.** Cumplimiento → **Esquema de las dos bases**
   tiene que decir que están aplicadas todas las migraciones. Si falta alguna,
   la nombra, y no tiene sentido seguir: las pantallas que dependan de lo que
   falta van a fallar. Lo mismo por API:

   ```bash
   curl -s https://gestion-paneles.web.app/api/diagnostico/esquema \
     -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
   ```

   Devuelve `200` con `"completo": true` cuando está todo, y `500` con la lista
   de lo que falta y los comandos para aplicarlo cuando no. Sin desplegar nada,
   la misma comprobación es `python3 scripts/verificar_esquema.py` (§2.3).

1. **Consulta demográfica.** Consultas → *+ Criterio demográfico* → `Sexo es F`
   → Consultar. Tiene que aparecer el aviso celeste *«se resolvió entera en la
   bóveda y no se abrió conexión al store semántico»*. Eso prueba que R2.4
   funciona y que la bóveda responde.
2. **Consulta semántica.** Quitá el criterio demográfico, agregá uno semántico
   con una frase que tenga sentido para tu corpus, y consultá. Tiene que salir
   un ranking con evidencia y procedencia (estudio y pregunta) por persona.
3. **El diagnóstico.** Abajo del ranking, la tarjeta *Diagnóstico* tiene que
   decir `Reranker: voyage` y `Verificador: claude`. Si dice `ninguno`, la
   clave correspondiente no llegó: mirá los avisos ámbar arriba del ranking,
   que traen el motivo.
4. **Composición.** Composición → cargá un universo de referencia de `sexo` que
   sume 1. Tiene que aparecer la brecha. Quitalo y tiene que volver a decir
   *brecha no disponible* (que no es lo mismo que brecha cero).
5. **Participación.** Tiene que mostrar tasa de respuesta, distribución de
   último contacto y distribución de convocatorias.
6. **Usuarios.** Configuración → dá de alta a alguien con rol `analista`.
   Tiene que aparecer el enlace de restablecimiento **una sola vez**. Después
   probá sacarte tu propio rol de admin: el selector tiene que estar
   deshabilitado.

### 8.2 · Que las claves estén enganchadas

Sin abrir la app, con un ID token de Firebase Auth.

> **Cómo se saca el token.** Entrá a `https://gestion-paneles.web.app`,
> logueate, abrí la consola del navegador (F12 → Console) y pegá:
> `await firebase.auth().currentUser.getIdToken()` —o, según cómo esté
> inicializado el SDK, `await getAuth().currentUser.getIdToken()`—. Copiá la
> cadena que devuelve. Dura una hora; pasado ese rato, repetí.

```bash
TOKEN="...el ID token..."
curl -s https://gestion-paneles.web.app/api/consultas \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"criterios":["una frase de prueba"],"top_k":5}' \
  | python3 -m json.tool | head -40
```

Lo que hay que mirar en la respuesta:

```json
"degradaciones": [],                    ← vacío = las dos claves llegaron
"diagnostico": {
  "reranker": "voyage",                 ← no "ninguno"
  "verificador": "claude",              ← no "ninguno"
  "ms_total": 1840.2,
  "etapas": [ ... ]                     ← tamaños y tiempos por etapa
},
"puente": { "estrategia": "...", "motivo": "..." }
```

### 8.3 · Calibrar contra el corpus real

Los defaults son un punto de partida razonable, no una verdad. Los tres que
importan:

| Parámetro | Default | Qué controla | Cómo se calibra |
|---|---|---|---|
| `top_n` | 200 | Tamaño del pool de recall, en **respuestas** | Subilo hasta que dejen de aparecer candidatos nuevos en el ranking |
| `top_k` | 25 | Cuántas personas se verifican | Es el parámetro caro: bajalo si la latencia molesta |
| `umbral_distancia` | 0.55 | Por encima, confianza **baja** | Mirá la distancia de los que el analista confirma como correctos |

Se pueden cambiar por consulta desde el botón **Parámetros** de la pantalla, y
así se calibran sin desplegar.

El `umbral_distancia` es el que más depende del corpus y del modelo. La forma
de fijarlo: corré diez consultas reales, pedile al analista que marque cuáles
resultados son correctos, y mirá la distancia del peor de los correctos. Ese es
tu umbral. **El default de 0.55 no está medido contra el corpus de Equipos**;
está puesto para que exista un valor y para que la señal de confianza aparezca
desde el primer día.

### 8.4 · Latencia

El DoD pide «latencia de una consulta típica dentro del objetivo acordado», y
el objetivo se acuerda contra el corpus de producción. Lo que el sistema ya
hace es **medirla y publicarla**: cada respuesta trae `diagnostico.ms_total` y
el tiempo de cada etapa.

Para sacar el p50/p95 por forma de consulta, que es la métrica de §9 del spec:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api"' \
  --limit=500 --format='value(textPayload)' --freshness=7d
```

Referencia de dónde se va el tiempo, para saber qué mirar:

| Etapa | Peso típico | Si se dispara |
|---|---|---|
| `embedding` | ~100–300 ms | Una llamada a Voyage; si tarda, es la red |
| `recall` | ~10–100 ms | Si se dispara, mirá el paso 6.1 (índice) |
| `gate_consentimiento` | ~10–50 ms | Una consulta a la bóveda |
| `reranking` | ~200–600 ms | Proporcional a `top_n` |
| `verificacion` | ~1–4 s | **La etapa dominante.** Proporcional a `top_k` |

Si hay que bajar la latencia, el orden es: bajar `top_k`, después `top_n`, y
solo al final tocar el modelo de verificación.

### 8.5 · Costo por consulta

Vale la pena estimarlo antes de abrirlo a todo el equipo, porque escala con el
uso y no con el tamaño del panel.

```
1 consulta con un criterio semántico
  = 1 embedding del criterio           (unas decenas de tokens)
  + 1 rerank de top_n documentos       (200 respuestas cortas por defecto)
  + 1 llamada a Claude con top_k × ≤3 evidencias
```

Dos criterios semánticos duplican las tres cosas: cada criterio corre su propio
oleoducto. Los criterios **demográficos son gratis** —se resuelven en la bóveda—
así que acotar por panel y por segmento antes de consultar baja el costo además
de mejorar el resultado.

---

## 9 · La gestión de usuarios, y por qué tiene barandas

La solapa Configuración es **escalada de privilegios por diseño**: quien puede
dar de alta a alguien con rol `admin` puede darle acceso a toda la bóveda,
incluida la PII de los panelistas. Las restricciones no son adornos:

| Regla | Por qué |
|---|---|
| Solo `admin`, con permiso propio (`gestionar_usuarios`) | Es una capacidad aparte de administrar paneles |
| Nadie se saca su propio rol de admin | Si el último admin se degrada, no queda nadie que pueda dar de alta a nadie |
| Nadie se desactiva a sí mismo | Se queda afuera, y hay que volver por el script de emergencia |
| Rol fuera de la lista, rechazado | Un rol inventado no falla al guardarse: falla cuando la persona entra y ningún permiso le aplica |
| Desactivar no borra | Borrar al usuario borraría el rastro de sus operaciones |
| Todo queda auditado, con autor y fecha | `usuario_auditoria`, solo de agregar |

**La clave inicial no se muestra de forma persistente.** El alta crea la cuenta
con una clave aleatoria que no se guarda en ningún lado, y devuelve un enlace de
restablecimiento marcado para mostrar **una sola vez**. Si se cierra ese modal
sin copiarlo, no se recupera: la persona entra con «¿Olvidaste tu contraseña?»
en el login.

**Un cambio de rol vale desde la próxima operación.** El rol se resuelve contra
la ficha de Firestore en cada request, así que no hace falta que la persona
vuelva a entrar. La operación que ya está corriendo no cambia de permisos en el
medio.

### Los permisos de la Fase 2

Se agregaron tres a `panel_api/auth.py`:

| Permiso | Roles | Para qué |
|---|---|---|
| `consultar` | admin, operaciones, analista | Correr consultas semánticas |
| `reidentificar` | admin, operaciones | Traducir `id_persona` a datos de contacto |
| `gestionar_usuarios` | **admin** | La solapa Configuración |

El `dpo` **no** consulta: consultar mueve el corpus por el reranker y por la
API de Claude, y no es su trabajo. Sí lee el registro de reidentificación, que
es la evidencia de que el puente entre stores se usa y se controla.

> La navegación de la SPA también filtra por permiso, pero eso es una
> conveniencia de interfaz: la autoridad es el backend, que exige el permiso en
> cada ruta. Si las dos listas se desincronizan, el síntoma es una solapa que
> da 403, nunca un acceso indebido.

---

## 10 · El registro de reidentificación

El resultado de una consulta son `id_persona`: tokens opacos. Traducirlos a
personas es lo que deshace la seudonimización, y **cada traducción queda
anotada** con autor, fecha y motivo.

Se registra en dos lugares:

- **Abrir la ficha de un panelista** (`GET /panelistas/{id}`), motivo `ficha`.
- **Resolver un resultado de consulta** (`POST /reidentificacion`), motivo
  `consulta` o el que se le pase. Es lo que hace el botón *Ver quiénes son*.

No se registra listar el padrón: eso ya se venía mostrando en pantalla, y
anotarlo llenaría el registro de ruido hasta volverlo inútil para auditar.

En la **bóveda** (o desde la app, ver abajo):

```bash
psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda -c "
-- Quién reidentificó, a cuánta gente, en la última semana.
select actor_email, motivo, count(*) as veces,
       count(distinct id_persona) as personas
  from reidentificacion
 where creado_en > now() - interval '7 days'
 group by 1, 2
 order by veces desc;"
```

Desde la app lo lee cumplimiento (`admin` o `dpo`) en `GET /reidentificacion`.

---

## 11 · Volver atrás

La Fase 2 se puede apagar por partes, de menos a más invasivo. Ninguna opción
toca los datos.

### Apagar solo las etapas caras

Las dos etapas que llaman a un modelo se apagan por variable de runtime, en
`functions/.env` (ver el paso 7.1):

```bash
cat >> functions/.env <<'EOF'
RERANKER_PROVEEDOR=ninguno
VERIFICACION_PROVEEDOR=ninguno
EOF

export VPC_CONNECTOR=paneles-conn
firebase deploy --only functions
```

Las consultas siguen andando con el orden del recall, y cada respuesta dice que
está degradada y por qué. Sirve para cortar costo de un día para otro sin
sacar la funcionalidad.

### Volver a la versión anterior de la función

```bash
git checkout <commit-de-fase-1>
export VPC_CONNECTOR=paneles-conn
firebase deploy --only functions,hosting
```

Las migraciones **no hace falta revertirlas**: son aditivas y la Fase 1 no mira
esas tablas ni esa columna. Quedan ahí sin molestar, y si se vuelve a
desplegar la Fase 2 están puestas.

### Si igual hay que revertir el esquema

**Son dos stores distintos: cada bloque va a su instancia.** Correr el de
bóveda contra la semántica (o al revés) falla a mitad de camino.

```bash
# Bóveda (puerto 5432). Ojo: se lleva la auditoría de usuarios y el registro
# de reidentificación, que son justamente lo que hay que conservar.
psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda -v ON_ERROR_STOP=1 -c "
drop table if exists consulta_guardada;
drop table if exists usuario_auditoria;
drop table if exists reidentificacion;"

# Semántica (puerto 5433). Perder el hash solo cuesta un re-embedding en la
# próxima ingesta.
psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica -v ON_ERROR_STOP=1 -c "
alter table respuesta drop column if exists hash_texto;"
```

---

## 12 · Problemas frecuentes

**«La consulta devuelve vacío y no hay ningún error.»**
Casi siempre es el gate de consentimiento. Mirá
`puente.personas_en_segmento` en la respuesta: si da 0, nadie del segmento
tiene `uso_semantico` vigente (ver paso 1). Si da un número razonable y el
ranking igual está vacío, probá el **modo laxo**: en estricto queda afuera
quien no tiene evidencia del criterio.

**«Una pantalla falla con “Error interno del servidor” y en los logs aparece
`relation "…" does not exist`.»**
Es una migración sin aplicar. A partir de la Fase 2 la aplicación lo traduce y
la respuesta trae el nombre del archivo que falta en lugar del error genérico;
si el mensaje que se ve es el genérico, la función es anterior a ese cambio y
hay que redesplegar. El estado completo está en Cumplimiento → Esquema de las
dos bases, o en `GET /api/diagnostico/esquema`.

El caso que ya ocurrió: `v_respuesta_estudio` no existía porque la semántica
tenía aplicada la migración `0001` pero no la `0002`. El síntoma fue que toda
la sección de Consultas fallaba mientras el resto del sistema funcionaba
normalmente. Se corrige aplicando la migración que falte:

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433
psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica \
  -v ON_ERROR_STOP=1 -f db/semantica/0002_vista_procedencia.sql
```

No hace falta redesplegar la función: la vista se crea y la consulta siguiente
ya funciona. Después de aplicarla conviene correr
`python3 scripts/verificar_esquema.py`, que revisa las dos bases enteras: si
una migración se salteó, es probable que se haya salteado más de una.

**«El diagnóstico dice `reranker: ninguno`.»**
El secreto `RERANKER_API_KEY` está vacío o no llegó. El aviso ámbar arriba del
ranking trae el motivo exacto. Acordate de que un cambio de secreto necesita un
deploy nuevo.

**«Aparece gente que dice justamente lo contrario de lo que busqué.»**
Es el caso que el reranking y la verificación están para filtrar. Si pasa con
las dos etapas activas, mirá el detalle del candidato (*Ver*): si el veredicto
es `cumple` pero la evidencia es negativa, la fila trae un aviso
`aviso_polaridad`. Si pasa seguido, es señal de que el criterio está escrito de
forma ambigua —«fernet» en vez de «gente a la que le gusta el fernet»— más que
de que el modelo se equivoque.

**«Dar de alta un usuario falla con `PERMISSION_DENIED`.»**
Le faltan permisos a la cuenta de servicio de la función (paso 5).

**«El alta de usuario anda pero no devuelve enlace.»**
`generate_password_reset_link` necesita un dominio autorizado en Auth. Consola
Firebase → Authentication → Settings → Authorized domains. Sin el enlace el
alta funciona igual: la persona entra con «¿Olvidaste tu contraseña?».

**«Las proporciones del universo de referencia no se guardan.»**
Tienen que sumar 1 **por dimensión**, con medio punto porcentual de tolerancia.
El error dice cuánto suman. Si vienen en porcentaje (52 en vez de 0.52), hay
que dividirlas por 100.

**«La consulta demográfica tarda igual que la semántica.»**
No debería: no abre el store semántico. Verificá en la respuesta que
`abrio_semantica` sea `false` y que `tipo` sea `demografica`. Si dice `mixta`,
quedó un criterio semántico cargado en la pantalla.

**«El re-embedding no se saltea.»**
Mirá `embebidas` y `reutilizadas` en el resultado de la ingesta. Si
`reutilizadas` es 0 en una re-ingesta idéntica, o falta la migración `0003`, o
el texto cambió de verdad (un espacio distinto en el enunciado de la pregunta
cambia el `texto_embebido` y con razón: es otro texto).

---

## 13 · Desarrollo sin desplegar

La Fase 2 entera se puede probar sin red y sin GCP. Es lo que corre en las
pruebas:

```bash
source scripts/pg_pruebas.sh          # levanta Postgres y aplica TODAS las migraciones
export EMBEDDINGS_PROVEEDOR=bolsa     # embeddings de bolsa de palabras
export RERANKER_PROVEEDOR=lexico      # reranker léxico
export VERIFICACION_PROVEEDOR=lexico  # verificador léxico
export PADRON_USUARIOS=memoria        # padrón en memoria, sin Firebase

cd functions && python3 -m pytest -q
```

Acá el `export` **sí** sirve: las pruebas corren en este proceso, y leen
`os.environ`. Es lo mismo que en producción va en `functions/.env` (paso 7.1).

Los tres proveedores sin red no son juguetes de relleno: son lo que hace que
las pruebas del DoD sean determinísticas.

- **`bolsa`** le da a cada palabra una dirección fija y suma: dos textos que
  comparten palabras salen cerca. Con un hash del texto completo —lo que hacía
  la Fase 1— «me encanta el fernet» quedaba tan lejos del criterio como
  cualquier otra frase, y las pruebas de recall no probaban el recall.
- **`lexico`** (reranker y verificador) usa un léxico de negación en español
  rioplatense. Es grueso a propósito y no reemplaza al modelo; alcanza para que
  el caso de polaridad opuesta dé el mismo resultado siempre.

Y la interfaz se recorre entera en **modo demo**: dejá la `apiKey` de
`web/public/index.html` en `TU_API_KEY` y la app arranca contra el backend en
memoria de `demo.js`, que implementa la superficie de la Fase 2 con la misma
forma de respuesta. La semilla incluye a propósito una respuesta de polaridad
opuesta, para que la exclusión se vea.

---

## 14 · Checklist

Infraestructura y datos:

- [ ] Fase 1 andando, y `python3 scripts/verificar_region.py` en verde.
- [ ] Corpus real ingestado (`select count(*) from respuesta` > 0).
- [ ] Cuánta gente tiene `uso_semantico` vigente, y si alcanza.
- [ ] `db/boveda/0004_fase2.sql` aplicada; las tres tablas existen.
- [ ] `db/semantica/0003_hash_texto.sql` aplicada; `hash_texto` existe.
- [ ] El `explain` de la consulta vectorial usa el índice HNSW.
- [ ] `python3 scripts/verificar_esquema.py` sale con código 0. Es la
      comprobación que cubre de una sola vez las migraciones de las dos bases,
      incluidas las de la Fase 1, y se puede correr **antes** del deploy.
- [ ] `GET /api/diagnostico/esquema` responde `200` con `"completo": true`
      (lo mismo, ya con la función arriba).

Secretos y permisos:

- [ ] `RERANKER_API_KEY` en Secret Manager.
- [ ] `CLAUDE_API_KEY` en Secret Manager.
- [ ] La cuenta de servicio de la función tiene `firebaseauth.admin` y
      `datastore.user`.
- [ ] `firestore.rules` sigue con `allow write: if false` en `/usuarios/{uid}`.
- [ ] Si hace falta apartarse de los defaults, `functions/.env` copiado de
      `functions/.env.ejemplo` (y `VPC_CONNECTOR` exportado en la shell, que
      es lo otro).

Deploy y verificación:

- [ ] `export VPC_CONNECTOR=paneles-conn && firebase deploy`.
- [ ] Consulta demográfica: dice que no abrió el store semántico.
- [ ] Consulta semántica: ranking con evidencia y procedencia.
- [ ] Diagnóstico: `reranker: voyage` y `verificador: claude`, sin
      degradaciones.
- [ ] Composición: con objetivo aparece la brecha; sin objetivo dice
      *no disponible*.
- [ ] Participación: tasa de respuesta, último contacto y convocatorias.
- [ ] Configuración: alta de usuario, enlace mostrado una vez, y el propio
      rol de admin deshabilitado.
- [ ] `select count(*) from reidentificacion` crece al usar *Ver quiénes son*.

Calibración (se puede hacer después, pero no olvidarla):

- [ ] `umbral_distancia` medido contra resultados que el analista confirmó.
- [ ] `top_n` y `top_k` ajustados a la latencia que el equipo tolera.
- [ ] Costo por consulta estimado antes de abrirlo a todo el equipo.
