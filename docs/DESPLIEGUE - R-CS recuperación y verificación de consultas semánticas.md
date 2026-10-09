# Despliegue — Recuperación y verificación de consultas semánticas (R-CS)

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Pedido que cubre:** la «Solicitud de cambio: recuperación y verificación de
consultas semánticas» con su addendum,
`specs/ADDENDUM_solicitud_consultas_semanticas.md`, **en su totalidad**:

| | Qué es | Dónde quedó |
|---|---|---|
| Cambio 1 | `input_type=query` para el criterio, reversible sin redesplegar | `embeddings.py` · §8 · §12 |
| Cambio 2 | `alcance` (exploratorio / completo) separado de `modo` | `consultas.py` |
| Cambio 3 | Unidades de evidencia: lo repetido se verifica una vez y no se come los lugares | `consultas.py` · `semantica/0009` |
| Cambio 4 | Veredicto `irrelevante`; estados `confirmada` / `posible` / `pendiente` | `verificacion.py` · `consultas.py` |
| Cambio 5 | Ejecución completa en diferido, con presupuesto | `consulta_completa.py` · `boveda/0025` · `procesarconsulta` |
| A1 | Verificación empírica previa (texto, distancia, reranker) | **§2 — obligatorio antes de habilitar la completa** |
| A2 | Costo estimado en pantalla, presupuesto obligatorio, costo real | `costo_consulta.py` · §3 · `docs/COSTOS.md` 5.1 |
| A3 | Reutilizar `sin_verificar` y el patrón de la ingesta diferida | sin contrato nuevo |
| A4 | COLOQUIO: `detalle` ↔ `criterios`, contrato v2 | §11 |
| A5 | Medición antes/después del `input_type`; fixture de contradicción | §2.4 · `test_r_cs_consultas.py` |
| A6 | `limite` («Personas a mostrar») en Parámetros, recorte visible | `consultas.js` |

> **Sobre el documento original.** En el repositorio está el addendum, no la
> solicitud original. Los cambios 1 a 5 se implementaron como el addendum los
> describe y los referencia (R4.1, R4.2, R5.4, sección 7). Cada decisión que
> hubo que tomar sin el texto original está en `docs/decisiones.md`, **D70**.
> Si la solicitud original pide algo distinto en algún punto, ese es el lugar
> donde contrastarlo.

**Precondición:** bóveda al día hasta la **`0024`**; semántica hasta la
**`0008`**. La API `cloudtasks.googleapis.com` habilitada y `TAREAS_CUENTA`
cargado (los dejó la ingesta diferida).

**Duración estimada:** 90 a 120 minutos, de los cuales 30 son el diagnóstico
A1/A2 y 30 las pruebas en la aplicación.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Cloud Tasks | Código |
|---|---|---|---|---|---|---|
| R-CS | `0025_consulta_ejecucion.sql` | `0009_unidades_de_evidencia.sql` (con backfill de `hash_texto`) | **`TAREAS_CONSULTA_URL`** (nuevo) | `EMBEDDINGS_TIPO_CONSULTA`, `COSTO_*`, `CONSULTA_*` | cola **`procesarconsulta`** (nueva) | backend, frontend, función nueva `procesarconsulta` |

**Qué NO cambia:** ningún rol de base, ningún `grant`, la superficie de
COLOQUIO (`verificar_coloquio.py` da 18/18 con las dos migraciones aplicadas),
el tamaño de las instancias, el conector VPC, el modelo de Claude ni el de
embeddings. **No se re-embebe nada**: las respuestas siguen como `document`.

**Permiso nuevo:** `consulta_completa` (admin y operaciones). Un analista
puede estimar una completa y leer sus resultados, pero no lanzarla.

---

## 1 · Qué trae, en una página

**Para el analista:**

- **Parámetros** tiene cinco campos: *Pool de recuperación*, *A verificar*,
  ***Personas a mostrar*** (nuevo, A6), *Umbral de confianza*, *Estrategia de
  puente* y ***Embedding del criterio*** (nuevo, A5). El formulario avisa
  mientras se escribe si *Personas a mostrar* supera *A verificar*, y no deja
  guardarlo. Bajo *A verificar*, cuántas llamadas a Claude implica.
- El encabezado del ranking dice **cuántas personas se revisaron, cuántas
  tenían evidencia pertinente y cuántas se muestran**.
- Cada persona tiene **estado**: *Confirmada*, *Posible*, *Pendiente*.
- Veredicto nuevo **Irrelevante**: la respuesta no habla del criterio. Cuenta
  como falta de evidencia, no como «no cumple». En laxo, quien no tiene
  ninguna evidencia pertinente **ya no aparece** (antes podía entrar como
  «dudoso» penalizado: era el falso resultado del caso Xiaomi).
- Las **respuestas repetidas se verifican una vez** y la selección va a
  buscar a las siguientes personas si las primeras solo tenían evidencia
  irrelevante.
- **Alcance completo**: estimar costo → fijar presupuesto → lanzar → avance
  y gasto → resultado paginado. Se puede cerrar la pantalla.
- El diagnóstico muestra **cuánto costó cada consulta** y con qué
  `input_type` se embebió el criterio.

**Para quien opera:**

- Cada ejecución deja una fila en `consulta_ejecucion` con su diagnóstico
  (degradaciones, reranker y verificador efectivos, `input_type`, costo).
  Responde A1.3 después del hecho.
- Un reranker que contesta **sin puntajes para todas las unidades** ahora es
  una **degradación declarada**. Antes se descartaban los `None` en silencio
  y la consulta seguía por distancia sin avisar: es exactamente el escenario
  que A1.3 pedía descartar.
- `scripts/diagnosticar_consulta.py` corre A1.1, A1.2, A1.3, A2 y A5 contra
  la base, sin escribir nada.

> **Cambio de comportamiento visible.** Con el mismo corpus, una consulta
> puede devolver **menos personas en modo laxo** que antes: las que solo
> tenían evidencia sobre otra cosa ahora quedan como «sin evidencia
> pertinente». Es el arreglo, no una regresión. Conviene avisarlo a los
> analistas antes del deploy (§13).

---

## Paso 0 · Preparar la terminal

```bash
setopt INTERACTIVE_COMMENTS 2>/dev/null || true
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
export FUNCION_CONSULTA="procesarconsulta"
export COLA_CONSULTA="procesarconsulta"   # el CLI crea la cola con el id de la función
export URL_CONSULTA="https://${REGION}-${PROYECTO}.cloudfunctions.net/${FUNCION_CONSULTA}"
gcloud config set project "$PROYECTO"
printf 'URL: %s\n' "$URL_CONSULTA"
```

La cuenta OIDC con la que Cloud Tasks firma es **la misma** de la ingesta
diferida, `TAREAS_CUENTA`. Recuperarla:

```bash
export CUENTA="$(gcloud secrets versions access latest --secret=TAREAS_CUENTA --project="$PROYECTO")"
printf 'Cuenta OIDC: %s\n' "$CUENTA"
```

Túneles y DSN, como siempre (no abrir otra copia si ya están):

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda   --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA"    -tAc "select current_database()"   # paneles_boveda
psql "$DSN_SEMANTICA" -tAc "select current_database()"   # paneles_semantica
```

Dependencias del script de diagnóstico (en la máquina que lo corre):

```bash
pip install "psycopg[binary]~=3.1" requests
```

---

## Paso 1 · Confirmar el punto de partida

```bash
python3 scripts/verificar_esquema.py
```

Con el código de esta rama, lo único que tiene que faltar es:

```
    ✗ 0025_consulta_ejecucion.sql
        falta consulta_ejecucion …
        falta consulta_lote …
        falta estado_consulta_ejecucion …
        falta estado_lote_consulta …
        falta v_consulta_progreso …
    ✗ 0009_unidades_de_evidencia.sql
        falta v_unidad_evidencia …
        falta veredicto_unidad …
```

Si falta cualquier otra cosa, **no seguir**: aplicar antes la migración que
corresponda con su propio documento de despliegue.

---

## Paso 2 · A1 — la verificación empírica previa (obligatoria)

El addendum pide correr esto **antes de decidir el alcance**. El código ya
trae todo, pero la consulta **completa no se habilita para nadie** hasta que
estos resultados estén anotados en §2.5: son los que dicen si el caso que
motivó la solicitud era ingesta, embedding, recorte o reranker.

Usar el **criterio y el texto reales** del caso (por ejemplo, criterio «gente
que usa un celular Xiaomi», texto buscado `xiam`, texto que ocupó el top
`titular`).

### 2.1 · A1.1 — ¿la respuesta está en el store semántico?

Se puede correr **antes** de migrar y de desplegar: solo lee.

```bash
python3 scripts/diagnosticar_consulta.py a1-texto --patron xiam
```

- *«Ninguna respuesta contiene…»* → **la evidencia nunca entró**. Es un
  problema de ingesta (variable sin mapear, o se embebió el código y no la
  etiqueta). Ningún cambio de recuperación lo arregla: corregir el estudio
  con **Reprocesar** (manual 5.9) y volver a correr esto.
- Filas marcadas `⚠ código sin traducir` (`→ 11427`, `→ Checked`) → mismo
  diagnóstico: el texto embebido es inútil para la búsqueda.

El SQL equivalente, si se prefiere `psql`:

```sql
select r.id, p.codigo, p.texto, r.valor_texto, r.texto_embebido
  from respuesta r join pregunta p on p.id = r.pregunta_id
 where r.valor_texto ilike '%xiam%' or r.texto_embebido ilike '%xiam%';
```

### 2.2 · A1.2 — ¿a qué distancia quedó del criterio?

Necesita la clave de Voyage en el entorno de la terminal (no se imprime ni se
guarda):

```bash
export EMBEDDINGS_API_KEY="$(gcloud secrets versions access latest --secret=EMBEDDINGS_API_KEY --project="$PROYECTO")"
python3 scripts/diagnosticar_consulta.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron xiam --contra titular
unset EMBEDDINGS_API_KEY
```

Lo mide con los **dos** `input_type` (cambio 1). Lectura:

- *«Más lejos que 25 repeticiones…»* → **calidad del embedding** (`xiami`
  mal escrito contra `xiaomi`). Las unidades de evidencia no lo resuelven;
  sí puede ayudar el `input_type=query` (comparar las dos líneas).
- *«Más cerca que las repeticiones»* → era el **recorte**: el cambio 3 está
  justificado y es el que lo arregla.

### 2.3 · A1.3 — ¿corrió el reranker?

Las ejecuciones anteriores a la `0025` **no dejaron registro**: esa pregunta
no se puede contestar para la consulta original. Lo que sí se puede es
reproducirla con el código nuevo y mirar. Después del paso 9:

```bash
python3 scripts/diagnosticar_consulta.py a1-reranker --ultimas 5
```

- *«EL RERANKER NO CORRIÓ (o corrió a medias)»* con su motivo → la
  degradación **sola** explica el resultado: revisar `RERANKER_API_KEY`
  (Secret Manager y declaración en `SECRETOS`) antes que nada.
- *«el reranker corrió sin degradaciones»* → la causa está en 2.1 o 2.2.

Mientras tanto, en los logs de la consulta original puede haber pistas
indirectas (`[verificacion]` trae los números de la verificación, no del
reranker):

```bash
gcloud functions logs read api --gen2 --region="$REGION" --project="$PROYECTO" \
  --limit=200 | grep -E "\[verificacion\]|rerank" | tail -20
```

### 2.4 · A5 — medir el `input_type` antes y después

Armar un archivo con consultas conocidas y las respuestas que **deberían**
aparecer (los `respuesta_id` salen de `a1-texto`):

```json
[
  {"criterio": "gente que usa un celular xiaomi", "esperadas": [12345, 23456]},
  {"criterio": "gente a la que le gusta el fernet", "esperadas": [34567]}
]
```

```bash
export EMBEDDINGS_API_KEY="$(gcloud secrets versions access latest --secret=EMBEDDINGS_API_KEY --project="$PROYECTO")"
python3 scripts/diagnosticar_consulta.py input-type --consultas consultas_conocidas.json
unset EMBEDDINGS_API_KEY
```

Devuelve, por consulta, cuántas esperadas trae el recall con `query` y con
`document`, y cuánto se parecen las dos listas. **Si `query` no mejora o
empeora**, desplegar igual y dejar `EMBEDDINGS_TIPO_CONSULTA=document` en el
paso 6: el resto de R-CS no depende de esto. No guardar el archivo con
resultados en el repositorio.

### 2.5 · Anotar los resultados

Completar esta tabla en el PR o en el ticket del despliegue **antes** de
habilitar la completa (paso 10):

| Verificación | Resultado | Conclusión |
|---|---|---|
| A1.1 — ¿la respuesta está? | | ingesta / está |
| A1.2 — distancia `document` | | embedding / recorte |
| A1.2 — distancia `query` | | |
| A1.3 — reranker (reproducción) | | degradó / corrió |
| A5 — recall@200 `document` vs `query` | | dejar `query` / volver a `document` |

> **Si la causa es ingesta o degradación del reranker**, el addendum tiene
> razón: el alcance real del problema se reduce a eso, y los cambios 1 y 4
> (baratos y correctos por sí mismos) más la corrección de ingesta o de clave
> alcanzan. La completa queda desplegada pero **sin habilitar** (presupuesto
> máximo en cero, paso 6) hasta que haga falta.

---

## Paso 3 · A2 — el costo real sobre el corpus

Después de aplicar la `semantica/0009` (paso 5), porque cuenta unidades:

```bash
export EMBEDDINGS_API_KEY="$(gcloud secrets versions access latest --secret=EMBEDDINGS_API_KEY --project="$PROYECTO")"
python3 scripts/diagnosticar_consulta.py estimar --criterio "gente que usa un celular xiaomi"
python3 scripts/diagnosticar_consulta.py estimar --criterio "gente que usa un celular xiaomi" --panel-id 1
unset EMBEDDINGS_API_KEY
```

No crea nada ni llama a Claude. Imprime personas habilitadas, unidades,
lotes, llamadas, tokens y US$ de la completa, y la cota de la exploratoria.
El orden de magnitud esperado (`docs/COSTOS.md` 5.1) es **≈ US$ 0,05 por
criterio en la exploratoria y US$ 2 a 11 por criterio en la completa** sobre
el corpus de 24.935 respuestas.

Con ese número, decidir y anotar:

- [ ] `CONSULTA_PRESUPUESTO_MAXIMO_USD` (paso 6). Por omisión **25**. Si una
      completa de un criterio sobre todo el padrón cuesta ~US$ 6, un techo de
      15–20 deja hacer dos criterios y frena un error.
- [ ] Quién lanza: por omisión **admin y operaciones**
      (`auth.PERMISOS["consulta_completa"]`). Cambiarlo es un cambio de código.
- [ ] Las tarifas: si `CLAUDE_MODELO` no es `claude-sonnet-5`, ajustar
      `COSTO_CLAUDE_*` en el paso 6 o la estimación miente.

---

## Paso 4 · Respaldo

Las dos migraciones son aditivas, pero la `0009` hace un `update` masivo
(`hash_texto`). Respaldo a demanda de las dos instancias:

```bash
gcloud sql backups create --instance=paneles-semantica --project="$PROYECTO" \
  --description="antes de R-CS semantica/0009"
gcloud sql backups create --instance=paneles-boveda --project="$PROYECTO" \
  --description="antes de R-CS boveda/0025"
gcloud sql backups list --instance=paneles-semantica --project="$PROYECTO" --limit=1
```

---

## Paso 5 · Migraciones

### 5.1 · Semántica `0009` — unidades y veredictos

Antes, cuántas filas va a tocar el backfill:

```bash
psql "$DSN_SEMANTICA" -tAc "select count(*) filter (where hash_texto is null) as sin_huella, count(*) as total from respuesta"
```

Aplicar (va en una transacción; se puede correr dos veces):

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0009_unidades_de_evidencia.sql
```

El `update` recorre las filas sin huella: con ~25.000 respuestas son
segundos. Sobre `db-f1-micro` el índice nuevo `respuesta_unidad_idx` tarda
algo más; no interrumpir. Después:

```bash
psql "$DSN_SEMANTICA" -tAc "select count(*) from respuesta where hash_texto is null"      # 0
psql "$DSN_SEMANTICA" -tAc "select count(*) as unidades, sum(respuestas) as respuestas from v_unidad_evidencia"
```

El cociente respuestas/unidades dice **cuánto se repite el corpus**: es la
proporción de verificación que el cambio 3 ahorra. Anotarlo con A2.

Si la migración falla con el event trigger de PII (`ddl_command_end`), es que
alguien cambió un nombre de columna: ninguna de las de la `0009` es de PII
(`hash_texto`, `veredicto`, `razon`, `fallo`, `relevancia`, `distancia`…).

### 5.2 · Bóveda `0025` — ejecuciones y lotes

**No** es idempotente (crea tablas): correrla una sola vez.

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0025_consulta_ejecucion.sql
psql "$DSN_BOVEDA" -tAc "select codigo from estado_consulta_ejecucion order by orden"
```

Siete estados: `encolada`, `procesando`, `terminada`, `terminada_con_errores`,
`detenida_por_presupuesto`, `cancelada`, `fallida`.

### 5.3 · Esquema y COLOQUIO

```bash
python3 scripts/verificar_esquema.py                 # las dos bases al día
```

Y la superficie externa, **como `coloquio_app`** (las tablas nuevas se
revocan a `public`; no hay funciones nuevas):

```bash
TOKEN_COLOQUIO="$(gcloud auth print-access-token \
  --impersonate-service-account=coloquio-app@gestion-paneles.iam.gserviceaccount.com \
  --project="$PROYECTO")" && \
export DSN_BOVEDA_COLOQUIO="postgresql://coloquio-app%40gestion-paneles.iam:${TOKEN_COLOQUIO}@127.0.0.1:5432/paneles_boveda"
python3 scripts/verificar_coloquio.py
```

Tiene que terminar en «La bóveda está lista para COLOQUIO», sin fallidos.
Si la lista blanca de privilegios rompe, **no** ampliar la lista: revocar lo
que sobra.

---

## Paso 6 · Configuración (`functions/.env`)

Agregar al `functions/.env` del proyecto (no es secreto; se ve en un
`describe`):

```bash
# ── R-CS ──
# Cómo se embebe el criterio de una consulta. `query` es lo nuevo (cambio 1);
# `document` vuelve al comportamiento anterior SIN redesplegar código (A5):
# alcanza con una revisión nueva de la función con el valor cambiado.
EMBEDDINGS_TIPO_CONSULTA=query

# Tarifas para estimar y registrar el costo (US$ por millón de tokens).
# Tienen que corresponder a CLAUDE_MODELO y a los modelos de Voyage en uso.
COSTO_CLAUDE_ENTRADA_USD_MTOK=2
COSTO_CLAUDE_SALIDA_USD_MTOK=10
COSTO_RERANK_USD_MTOK=0.05
COSTO_EMBEDDING_USD_MTOK=0.06

# Techo por ejecución completa (A2). En 0 nadie puede lanzar una completa:
# es la forma de desplegar sin habilitarla mientras se termina §2.
CONSULTA_PRESUPUESTO_MAXIMO_USD=0
# Unidades por tarea de Cloud Tasks (4 llamadas a Claude con lotes de 25).
CONSULTA_UNIDADES_POR_LOTE=100
```

> `CONSULTA_PRESUPUESTO_MAXIMO_USD=0` es deliberado para el primer deploy:
> el código trata 0 como valor válido y **ningún** presupuesto lo cumple
> (tiene que ser > 0 y ≤ máximo). La pantalla muestra la estimación y dice
> que supera el máximo. Se sube en el paso 10, con los números de §2 y §3.

Ninguna variable empieza con `FIREBASE_`, `X_GOOGLE_`, `EXT_` ni `KIT_`
(`test_main.py` lo vigila).

---

## Paso 7 · Secreto y cola de la consulta completa

### 7.1 · Crear y cargar `TAREAS_CONSULTA_URL`

Está declarado en `SECRETOS` de `main.py`: **el deploy falla si no existe**.

```bash
gcloud secrets list --project="$PROYECTO" --format='value(name)' | grep -x TAREAS_CONSULTA_URL \
  || gcloud secrets create TAREAS_CONSULTA_URL --replication-policy=automatic --project="$PROYECTO"
printf '%s' "$URL_CONSULTA" | gcloud secrets versions add TAREAS_CONSULTA_URL --project="$PROYECTO" --data-file=-
```

Tiene que confirmar `Created version [...]`. La URL se puede cargar antes de
que la función exista: es la que va a tener (se verifica en 8.2).

### 7.2 · Crear la cola explícitamente

Como pasó con `procesaringesta`, no asumir que Firebase la crea:

```bash
gcloud tasks queues list --location="$REGION" --project="$PROYECTO"
gcloud tasks queues create "$COLA_CONSULTA" \
  --location="$REGION" --project="$PROYECTO" \
  --max-concurrent-dispatches=2 \
  --max-attempts=5 --min-backoff=30s --max-backoff=600s
gcloud tasks queues describe "$COLA_CONSULTA" --location="$REGION" --project="$PROYECTO"
```

**Concurrencia 2, no más.** El presupuesto se controla antes de cada lote
sobre la suma de los anteriores; con N tareas en paralelo el desvío posible
es de N lotes. Con 2 y lotes de 100 unidades es del orden de centavos. Si ya
existía, corregirla con `gcloud tasks queues update` y los mismos valores.

### 7.3 · Permisos de encolado

La API ya tiene `roles/cloudtasks.enqueuer` a nivel proyecto y
`roles/iam.serviceAccountUser` sobre `TAREAS_CUENTA` (ingesta diferida, paso
6). Confirmarlo, sin volver a otorgar lo que ya está:

```bash
CUENTA_API="$(gcloud functions describe api --gen2 --region="$REGION" --project="$PROYECTO" \
  --format='value(serviceConfig.serviceAccountEmail)')"
gcloud projects get-iam-policy "$PROYECTO" --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:$CUENTA_API AND bindings.role:roles/cloudtasks.enqueuer" \
  --format='value(bindings.role)'
```

Si devuelve vacío, otorgarlo como en `DESPLIEGUE - ingesta diferida.md`, paso
6.2 (con `--condition=None`).

---

## Paso 8 · Desplegar

### 8.1 · Pruebas antes de subir

En la máquina de quien despliega, con el Postgres de pruebas:

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q && cd ..
scripts/chequear_js.sh
```

Todo verde. Las pruebas nuevas son `functions/tests/test_r_cs_consultas.py`
(23), incluido el fixture de 137 personas (exhaustividad y paginación) y el
de evidencias contradictorias dentro de la misma persona (A5).

### 8.2 · Deploy

```bash
export VPC_CONNECTOR="$(gcloud functions describe api --gen2 --region="$REGION" \
  --project="$PROYECTO" --format='value(serviceConfig.vpcConnector)')"
printf 'Conector VPC: %s\n' "$VPC_CONNECTOR"     # no seguir si está vacío
firebase deploy --only functions,hosting --project="$PROYECTO"
```

El CLI tiene que **crear** la función `procesarconsulta` y actualizar `api`
y `procesaringesta`. No aceptar borrados que no estén previstos.

```bash
for f in api procesaringesta "$FUNCION_CONSULTA"; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(serviceConfig.uri,serviceConfig.vpcConnector)'
done
```

Las tres con el **mismo** conector. La URI de `procesarconsulta` tiene que
coincidir con `$URL_CONSULTA`; si no, recargar el secreto (7.1) con la real y
`firebase deploy --only functions:api`.

### 8.3 · Permiso de invocación

```bash
gcloud functions add-invoker-policy-binding "$FUNCION_CONSULTA" \
  --region="$REGION" --member="serviceAccount:$CUENTA" --project="$PROYECTO"
```

Sin esto las tareas reciben 403 y los lotes no avanzan nunca.

---

## Paso 9 · Probar en la aplicación (exploratoria)

Con un usuario **analista** y otro **admin**. Cada punto, una casilla del
checklist final.

1. **Parámetros (A6).** Abrir *Parámetros*: aparece *Personas a mostrar*. Con
   *A verificar* en 10, escribir 50 en *Personas a mostrar*: el aviso aparece
   mientras se escribe y *Guardar* no lo acepta. Bajo *A verificar* se ve
   cuántas llamadas implica.
2. **Recorte visible.** *A verificar* 25, *Personas a mostrar* 10, una
   consulta amplia: el encabezado dice «se revisaron N (M con evidencia
   pertinente) · se muestran 10».
3. **Estados e irrelevante.** Reproducir la consulta del caso (Xiaomi): las
   personas con la respuesta del contrato **no** aparecen en el ranking ni
   como *No cumple*; la tabla de excluidos las resume en una línea «Sin
   evidencia pertinente». Si aparece la persona de la marca, el cambio 3 la
   alcanzó.
4. **Diagnóstico.** Etapa *Unidades de evidencia* (respuestas → unidades,
   repetidas), reranker y verificador efectivos, `Criterio embebido como
   query` y el costo de la consulta.
5. **A1.3, ahora sí.** `python3 scripts/diagnosticar_consulta.py a1-reranker
   --ultimas 1` muestra esa ejecución. Anotarlo en §2.5.
6. **Registro.** En la base:

   ```bash
   psql "$DSN_BOVEDA" -c "select id, alcance, estado, costo_real_usd, diagnostico->>'reranker' as reranker,
                                  jsonb_array_length(diagnostico->'degradaciones') as degradaciones
                             from consulta_ejecucion order by creado_en desc limit 5"
   ```

   Sin personas ni evidencias en `diagnostico`.
7. **Completa deshabilitada.** Elegir *Alcance: Completo* → *Estimar costo…*:
   la estimación aparece y dice que **supera el máximo** (está en 0). Un
   analista ve el aviso «la lanza un responsable de operaciones o un admin».

---

## Paso 10 · Habilitar la consulta completa

Solo con §2.5 completo y el número de §3.

### 10.1 · Subir el techo

En `functions/.env`, `CONSULTA_PRESUPUESTO_MAXIMO_USD=<el decidido en §3>` y:

```bash
firebase deploy --only functions --project="$PROYECTO"
```

(Con `VPC_CONNECTOR` exportado. Es un deploy de configuración: el código es
el mismo.)

### 10.2 · Prueba de carga controlada (A2: `db-f1-micro`)

La completa recorre a fuerza bruta todas las respuestas de las personas
habilitadas. Antes de soltarla, medir el impacto en `paneles-semantica`:

1. Abrir **Cloud SQL → paneles-semantica → Supervisión** (CPU y memoria).
2. Como admin, una completa **acotada** a un panel chico y un criterio,
   presupuesto US$ 1. Anotar el tiempo de *Estimar costo…* (la etapa de
   elegibilidad) y los picos de CPU/memoria.
3. Repetir sin panel (todo el padrón) con *Estimar costo…* solamente: no
   cuesta nada y ejerce la misma consulta de elegibilidad.

Si la memoria pasa de 85% sostenido o la estimación tarda más de ~30 s, el
camino es `db-g1-small` (`docs/COSTOS.md` 4.7, ~US$ 24/mes, reversible).
**No descubrirlo en producción.**

### 10.3 · Una completa de punta a punta

1. Admin: criterio del caso, panel chico, *Alcance: Completo*, *Estimar
   costo…*, presupuesto sugerido, confirmar, *Lanzar*.
2. La pantalla muestra avance y gasto; **cerrar la pestaña** y volver:
   aparece en *Consultas completas recientes*.
3. En Cloud Tasks y en los logs:

   ```bash
   gcloud tasks queues describe "$COLA_CONSULTA" --location="$REGION" --project="$PROYECTO"
   gcloud functions logs read "$FUNCION_CONSULTA" --gen2 --region="$REGION" \
     --project="$PROYECTO" --limit=50 | grep "\[consulta_completa\]"
   ```

   Una línea por lote con `estado` y `costo_usd`; ningún contenido.
4. Al terminar: estado *Terminada*, resultado paginado, **Descargar CSV**
   trae el ranking entero (no solo la página).
5. Costo real vs estimado:

   ```bash
   psql "$DSN_BOVEDA" -c "select ejecucion_id, estado, unidades_total, unidades_verificadas, llamadas,
                                  costo_lotes_usd, presupuesto_usd
                             from v_consulta_progreso where alcance = 'completo'
                            order by creado_en desc limit 3"
   ```

   La próxima estimación ya sale **calibrada** con estos tokens («Tokens
   observados en ejecuciones anteriores»).

### 10.4 · El freno

1. Una completa con presupuesto **por debajo** de la estimación: no se lanza
   («La estimación supera el presupuesto»).
2. Una con presupuesto justo y `CONSULTA_UNIDADES_POR_LOTE` chico no hace
   falta reproducirla en producción: está cubierta por
   `test_el_presupuesto_frena_la_ejecucion_y_ampliarlo_la_continua`. Si
   ocurre de verdad, la ejecución termina *Detenida por presupuesto* y
   *Ampliar presupuesto y seguir* la retoma sin volver a pagar lo hecho.

---

## Paso 11 · COLOQUIO (A4)

**Lo que cambió del lado de paneles:** cada ítem trae `detalle` con el mismo
contenido que `criterios`, `estado` por persona y por criterio, y la
respuesta trae `version_contrato: 2`, `alcance`, `recorte` y `costo`.

Con eso, `motor._evidencias()` de COLOQUIO **ya ve los veredictos** sin tocar
COLOQUIO. Lo que conviene pedirle al equipo de COLOQUIO (es otro repositorio;
no se toca desde acá):

1. Leer `item.get("criterios") or item.get("detalle")` — el alias `detalle`
   se va a retirar cuando COLOQUIO lea `criterios`.
2. Mostrar los veredictos nuevos (`irrelevante`) y los estados
   (`confirmada`, `posible`, `pendiente`).
3. **Ausencia = «no se sabe».** Si `version_contrato` falta o es < 2, o un
   ítem no trae `estado`, mostrarlo como desconocido. Nunca suponer
   «confirmada»: un cliente que lee «sin dato» como favorable convierte un
   cambio de contrato en un error silencioso.

Comprobación después del deploy, por la misma API que usa COLOQUIO, con un
ID token de Firebase Auth (cómo sacarlo: `DESPLIEGUE - Fase 2.md`, 8.2):

```bash
export TOKEN="<el ID token, no se guarda en ningún archivo>"
curl -s https://gestion-paneles.web.app/api/consultas \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"criterios":["gente que usa un celular xiaomi"],"top_k":5,"limite":5}' \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['version_contrato'], [(i['estado'], i['detalle']==i['criterios']) for i in r['items']])"
```

Tiene que imprimir `2` y pares `('confirmada'|'posible'|'pendiente', True)`.

---

## Paso 12 · Volver atrás

**Solo el `input_type`** (A5: «poder volver atrás con configuración, sin
redesplegar»): cambiar `EMBEDDINGS_TIPO_CONSULTA=document` en una revisión
nueva de la función, sin tocar código:

```bash
# Una función Gen 2 es un servicio de Cloud Run con el mismo nombre.
gcloud run services update api --region="$REGION" --project="$PROYECTO" \
  --update-env-vars=EMBEDDINGS_TIPO_CONSULTA=document
```

y dejar el mismo valor en `functions/.env` para que el próximo `firebase
deploy` no lo pise. El diagnóstico de cada consulta confirma el tipo
efectivo. Un analista puede, además, comparar una consulta puntual con
*Parámetros → Embedding del criterio*.

**Solo la completa:** `CONSULTA_PRESUPUESTO_MAXIMO_USD=0` y deploy de
funciones. Las ejecuciones en curso terminan detenidas por presupuesto; las
nuevas no se lanzan. Para cortar ya, *Cancelar* desde la pantalla o:

```bash
gcloud tasks queues pause "$COLA_CONSULTA" --location="$REGION" --project="$PROYECTO"
```

**Todo el código:** desplegar el commit anterior (`firebase deploy --only
functions,hosting` desde ese commit). Las migraciones pueden quedar: son
aditivas y el código anterior no las lee. Si igual hay que revertirlas:

```sql
-- bóveda
drop view if exists v_consulta_progreso;
drop table if exists consulta_lote, consulta_ejecucion,
                     estado_lote_consulta, estado_consulta_ejecucion;
-- semántico (el backfill de hash_texto no se revierte: es el mismo valor
-- que la ingesta habría calculado)
drop table if exists veredicto_unidad;
drop view if exists v_unidad_evidencia;
drop index if exists respuesta_unidad_idx;
```

Y borrar la función y la cola nuevas:

```bash
firebase functions:delete procesarconsulta --region="$REGION" --project="$PROYECTO"
gcloud tasks queues delete "$COLA_CONSULTA" --location="$REGION" --project="$PROYECTO"
```

---

## Paso 13 · Aviso a los analistas

Texto sugerido:

> Desde hoy la consulta semántica: (1) muestra el **estado** de cada persona
> y cuántas se revisaron frente a cuántas se muestran; (2) tiene en
> *Parámetros* el campo **Personas a mostrar** —subir *A verificar* sin
> subirlo no trae más gente—; (3) marca como **Irrelevante** la respuesta que
> no habla del criterio: en modo laxo ya no aparecen personas cuya única
> respuesta era sobre otra cosa, así que algunas consultas van a devolver
> menos gente, y es lo correcto; (4) verifica una sola vez las respuestas
> repetidas. La **consulta completa** (revisar todo, con presupuesto) la lanza
> un responsable de operaciones o un admin; cualquiera puede estimar su
> costo. Detalle en el manual, 6.3, 6.4, 6.9 y 6.11.

---

## Errores y correcciones

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `esquema_desactualizado` nombrando `consulta_ejecucion` o `v_unidad_evidencia` | Falta la `0025` o la `0009` | Paso 5 |
| El deploy falla pidiendo el secreto `TAREAS_CONSULTA_URL` | Declarado en `SECRETOS` y no existe | Paso 7.1 |
| La completa queda *Encolada* para siempre | Lotes sin encolar (`TAREAS_CONSULTA_URL` vacío) o la cola no existe | Ver `aviso` en la respuesta; 7.1, 7.2; luego *Reintentar lotes* |
| Tareas con 403 | Falta el invoker | 8.3 |
| Tareas con timeout opaco | `procesarconsulta` sin conector VPC | 8.2: reexportar `VPC_CONNECTOR` y redesplegar |
| «La estimación supera el presupuesto» | Es el freno | Subir presupuesto (≤ máximo) o acotar la consulta |
| «supera el máximo por ejecución» | `CONSULTA_PRESUPUESTO_MAXIMO_USD` | Paso 10.1 |
| Aviso ámbar «reranking degradada … puntaje para N de M» | El proveedor contestó parcial | Revisar Voyage; la consulta sigue por distancia y lo dice |
| Una consulta devuelve menos gente en laxo | `sin_evidencia_pertinente` | Esperado (§1). Mirar la línea resumen de excluidos |
| `SinPermiso` al lanzar una completa | El rol no tiene `consulta_completa` | Esperado para analista y dpo |
| La estimación no dice «calibrada» | Menos de 3 lotes completos con tokens | Esperado al principio |

---

## Checklist

- [ ] Paso 1: solo faltaban `0025` y `0009`.
- [ ] §2.1 A1.1 corrido y anotado.
- [ ] §2.2 A1.2 corrido con los dos `input_type` y anotado.
- [ ] §2.4 A5 medido; decidido `EMBEDDINGS_TIPO_CONSULTA`.
- [ ] Respaldos a demanda de las dos instancias.
- [ ] `semantica/0009` aplicada; `hash_texto` sin nulos; cociente respuestas/unidades anotado.
- [ ] `boveda/0025` aplicada; siete estados.
- [ ] `verificar_esquema.py` al día y `verificar_coloquio.py` sin fallidos.
- [ ] §3 A2: costo real por criterio anotado; techo y quién lanza decididos.
- [ ] `functions/.env` con las variables R-CS (máximo en 0 para el primer deploy).
- [ ] Secreto `TAREAS_CONSULTA_URL` con la URL de `procesarconsulta`.
- [ ] Cola `procesarconsulta` con concurrencia 2.
- [ ] Pruebas y `chequear_js.sh` en verde.
- [ ] Deploy: `procesarconsulta` creada, las tres funciones con el mismo conector.
- [ ] Invoker otorgado a `TAREAS_CUENTA`.
- [ ] Paso 9: los siete puntos.
- [ ] §2.3 A1.3 con la reproducción; §2.5 completa.
- [ ] Paso 10: techo subido, prueba de carga medida, una completa de punta a punta, costo real vs estimado.
- [ ] Paso 11: comprobación de contrato v2; pedido a COLOQUIO enviado.
- [ ] Paso 13: aviso a analistas.

## Referencias

- `specs/ADDENDUM_solicitud_consultas_semanticas.md`
- `docs/decisiones.md` — D70 (y D66 para `sin_verificar`)
- `docs/COSTOS.md` — 5.1
- `docs/DESPLIEGUE - ingesta diferida.md` — el patrón de colas que se reutiliza
- Manual de usuario — 6.3, 6.4, 6.6, 6.9, 6.11
