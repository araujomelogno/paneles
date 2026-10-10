# Despliegue — Bug del diagnóstico A1.2 y el índice vectorial (D71)

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Pedido que cubre:** `specs/BUG_diagnostico_a1_distancia_y_hallazgo.md`,
**en su totalidad**:

| § del bug | Qué pide | Dónde quedó |
|---|---|---|
| §4.1 | `_distancias` mide filas conocidas sin el índice: filtro materializado primero | `scripts/diagnosticar_consulta.py` (`_distancias`) |
| §4.2 | El conteo de «cuántas están más cerca» sigue igual, con el comentario que explica las dos intenciones | `_delante` · comentario de la sección A1.2 |
| §4.3 | Vacío de verdad ≠ patrón ausente | `medir()`: `AUSENTE` (sale 1) / `NO_MEDIDO` (sale 2) |
| §4.4 | Revisar el resto del script | `--contra` reescrito como conteo exacto; `input-type` arreglado vía `semantica.recuperar` |
| §5 | «El mismo bug explica el problema original» | **`semantica.recuperar` corregido** (ver abajo) · §6 de este documento |
| §5 (A1.3) | Queda pendiente si el reranker corrió | §7 de este documento |
| §6 | Definition of Done (4 puntos) | `functions/tests/test_bug_diagnostico_a1.py` (12 pruebas) · §5 de este documento (corpus real) |
| §7 | Criterio general: vacío/defecto se distingue y se registra | `[recall]` en el log · `plan` / `indice_corto` en el diagnóstico · `NO_MEDIDO` |

**Duración estimada:** 45 a 60 minutos. 15 de diagnóstico contra la base
real (antes y después), 10 de pruebas locales, 5 de deploy y 15 de prueba en
la aplicación.

---

## 1 · Qué trae, en una página

### La causa, en dos líneas

Con un `order by <=> … limit`, Postgres resuelve por el **índice HNSW**, que
devuelve como mucho `hnsw.ef_search` vecinos (40 por defecto) **de todo el
corpus**, y el `where` se aplica **después**. Un filtro selectivo devuelve
vacío y un `top_n` mayor que 40 devuelve 40. **Sin error.**

### Lo que se encontró al corregirlo — y que el bug anticipaba

El bug pedía «revisar el resto por el mismo patrón». El patrón **no estaba
solo en el script**: `semantica.recuperar`, que es el *recall* de toda
consulta semántica (también la de COLOQUIO), tenía las dos variantes:

| Caso | Antes (medido en el corpus de prueba) | Ahora |
|---|---|---|
| Recall sin filtro, `top_n = 200` (lo habitual) | **40** respuestas | 200 |
| Recall con filtro demográfico primero, la mitad del corpus habilitada, `top_n = 200` | **22** respuestas | 200, las exactas |
| `a1-distancia --patron xiaomi` con 30 respuestas de marca y 1.500 repetidas | «no hay respuestas» | la distancia de las 30 |

Es decir: el «pool de recuperación» de 200 que muestra *Parámetros* era, en
la práctica, de 40. Con un filtro demográfico, personas habilitadas quedaban
fuera del pool sin que nada lo dijera. Las dos cosas refuerzan la conclusión
del bug (§5): la evidencia existe y está bien embebida; lo que la dejaba
afuera era **el recorte**.

### Qué cambia en el código

- **`scripts/diagnosticar_consulta.py`**
  - `a1-distancia` materializa el filtro y ordena por distancia sobre ese
    conjunto. Distingue tres salidas: **0** midió · **1** el patrón no está
    en el corpus (ingesta) · **2** `FALLO DE MEDICIÓN` (el patrón está y la
    consulta no lo encontró: no es un resultado).
  - Dice además si la mejor respuesta **entra en el recall real**
    (`top_n = 200`), con el mismo `semantica.recuperar` que usa la consulta.
  - `--contra` cuenta exacto y sin tope (antes salía de las primeras 1.000
    filas de la misma consulta rota).
- **`functions/panel_api/semantica.py`** — `recuperar` pasa por `_vecinos`:
  - **con filtro por personas** (el gate): recorrido exacto sobre las
    respuestas de las personas habilitadas;
  - **sin filtro**: índice, con `hnsw.ef_search` subido a `top_n` para esa
    transacción; por encima de 1.000 (el tope de pgvector), exacto;
  - si el índice igual devuelve menos de lo pedido habiendo más, escribe
    `[recall] el índice devolvió X de Y…` en el log y **recalcula exacto**.
- **`functions/panel_api/consultas.py`** — la etapa `recall` del diagnóstico
  suma `plan=indice|exacto` y, si pasó, `indice_corto=N`. Queda en
  `consulta_ejecucion.diagnostico`.

### Qué NO cambia

Ninguna migración, ningún `grant`, ninguna vista, ningún secreto, ninguna
variable de `functions/.env`, ninguna cola, el frontend (el diagnóstico ya
pinta cualquier clave de la etapa) ni la superficie de COLOQUIO. **No se
re-embebe nada.** El contrato con COLOQUIO (`version_contrato: 2`) es el
mismo: COLOQUIO va a recibir más candidatos correctos, no campos nuevos.

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Cloud Tasks | Código |
|---|---|---|---|---|---|---|
| D71 | — | — | — | — | — | `semantica.py`, `consultas.py`, script, pruebas |

### Cambio de comportamiento visible

- **Consultas mixtas** (con filtro demográfico): pueden traer **más
  personas** que antes. Es el arreglo.
- **Consultas sin filtro**: el pool es de verdad de 200 respuestas. Puede
  cambiar el orden y entrar gente nueva; el reranker y la verificación
  trabajan sobre más candidatos (el número de personas verificadas lo sigue
  mandando *A verificar*, así que **el costo de Claude no cambia**).
- **Latencia del recall**: sube algunos milisegundos (más vecinos en el
  índice, o recorrido exacto con filtro). Se mide en §6.3.

---

## Paso 0 · Preparar la terminal

```bash
setopt INTERACTIVE_COMMENTS 2>/dev/null || true
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
```

Túneles y DSN (no abrir otra copia si ya están abiertos):

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda   --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA"    -tAc "select current_database()"   # paneles_boveda
psql "$DSN_SEMANTICA" -tAc "select current_database()"   # paneles_semantica
```

Dependencias del script (en la máquina que lo corre):

```bash
pip install "psycopg[binary]~=3.1" requests
```

Traer la rama y quedarse en ella:

```bash
git fetch origin claude/jolly-wright-wzcej1
git checkout claude/jolly-wright-wzcej1
git log --oneline -1
```

---

## Paso 1 · Punto de partida

### 1.1 · R-CS

Este arreglo va **encima de R-CS**. Dos situaciones posibles:

- **R-CS ya desplegado** (bóveda hasta la `0025`, semántica hasta la `0009`):
  seguir con este documento entero.
- **R-CS a medio desplegar, detenido en su §2.2** (que es donde se detectó el
  bug): seguir este documento hasta el **paso 5** (diagnóstico con el script
  arreglado), volver al documento de R-CS para terminarlo, y desplegar el
  código de esta rama **en su paso 8** en lugar del de R-CS. Este documento
  retoma en el paso 6 para las comprobaciones en la aplicación.

```bash
python3 scripts/verificar_esquema.py
```

Con R-CS desplegado, no falta nada. Si falta `0025` / `0009`, es la segunda
situación.

### 1.2 · pgvector y el índice

```bash
psql "$DSN_SEMANTICA" -c "select extversion from pg_extension where extname = 'vector'"
psql "$DSN_SEMANTICA" -c "show hnsw.ef_search"
psql "$DSN_SEMANTICA" -c "select indexname, indexdef from pg_indexes
                           where tablename = 'respuesta' and indexdef ilike '%hnsw%'"
```

Anotar la versión (§9). Esperado: `ef_search` **40** (el default; el código
nuevo lo sube por transacción, no se toca la configuración de la instancia)
y **un** índice `hnsw (embedding vector_cosine_ops)`.

> Si `show hnsw.ef_search` da error «unrecognized configuration parameter»,
> la librería de pgvector todavía no se cargó en esa sesión: correr antes
> `select '[1]'::vector;` en la misma sesión. No es un problema: el código
> fija el valor con `set_config`, que funciona igual.

---

## Paso 2 · Reproducir el bug con el código viejo (5 min)

Para que el «después» se compare contra algo medido y no recordado. El
script viejo se saca de `main` sin cambiar de rama:

```bash
git show origin/main:scripts/diagnosticar_consulta.py > /tmp/diagnosticar_viejo.py
cp /tmp/diagnosticar_viejo.py scripts/_diagnosticar_viejo.py   # necesita estar en scripts/ por el sys.path
export EMBEDDINGS_API_KEY="$(gcloud secrets versions access latest --secret=EMBEDDINGS_API_KEY --project="$PROYECTO")"

python3 scripts/_diagnosticar_viejo.py a1-texto --patron xiaomi --limite 3
python3 scripts/_diagnosticar_viejo.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron xiaomi --contra titular
```

Esperado (el síntoma del bug): `a1-texto` encuentra las respuestas y
`a1-distancia` dice `no hay respuestas con «xiaomi»: correr a1-texto.` en las
dos líneas.

**Línea de base para la no regresión (DoD 2).** El conteo de «cuántas están
más cerca» solo se imprime cuando la medición funciona, así que se toma con
un patrón que el índice sí alcanza —el de las respuestas repetidas—:

```bash
python3 scripts/_diagnosticar_viejo.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron titular \
  | tee /tmp/a12_antes.txt
```

Anotar, para `query` y `document`, la distancia y el número de «respuesta(s)
del corpus están más cerca».

**El tamaño real del pool, antes.** Con la misma sesión de `psql`:

```bash
psql "$DSN_SEMANTICA" <<'SQL'
-- un vector cualquiera de la tabla como criterio; la subconsulta escalar
-- deja que el planificador use el índice, igual que la consulta real
select count(*) as devueltas
  from (select r.id from respuesta r
         order by r.embedding <=> (select embedding from respuesta order by id limit 1)
         limit 200) t;
SQL
```

Esperado con el default de pgvector: **40** (o cerca), no 200. Es la segunda
variante del bug, la que afecta a la consulta real.

---

## Paso 3 · Pruebas antes de subir (10 min)

En la máquina de quien despliega, con el Postgres de pruebas. Estas pruebas
**necesitan pgvector** en ese Postgres (antes alcanzaba con las que no lo
usan; ahora la prueba del bug depende del índice HNSW):

```bash
# Debian/Ubuntu, una sola vez:
sudo apt-get install -y postgresql-16-pgvector

source scripts/pg_pruebas.sh
cd functions
python3 -m pytest -q tests/test_bug_diagnostico_a1.py     # 12 pruebas
python3 -m pytest -q                                       # todas
cd ..
scripts/chequear_js.sh
```

Todo verde. Qué cubre `test_bug_diagnostico_a1.py`:

| Prueba | DoD |
|---|---|
| `test_el_corpus_reproduce_la_trampa_del_indice` | El corpus sintético (1.500 repetidas cerca del criterio, 30 de marca lejos) reproduce el bug con la consulta vieja. Si no lo reprodujera, las demás no probarían nada. |
| `test_a1_distancia_mide_las_respuestas_con_el_patron` · `…_completo_imprime_la_distancia` | 1 |
| `test_el_conteo_de_delante_es_el_de_siempre_y_es_exacto` | 2 |
| `test_un_patron_ausente_y_una_medicion_vacia_se_distinguen` · `…_con_medicion_vacia_sale_con_error` | 3 |
| `test_ninguna_consulta_filtra_y_ordena_por_distancia_sin_materializar` | 4 |
| `test_el_recall_con_gate_…` (2) · `…_sin_filtro_trae_top_n_…` · `…_mas_grande_que_el_indice_…` · `…_corto_del_indice_se_registra_…` | §5 y §7 del bug, en la consulta real |

> **Por qué la fixture apaga `enable_seqscan`.** Con 1.530 filas el
> planificador local prefiere recorrer la tabla y el bug no aparece; con las
> 21.340 de producción gana el índice. La fixture empuja al planificador al
> plan de producción para esa sesión. Las consultas corregidas tienen que dar
> bien **aun** cuando el planificador prefiere el índice.

Verificado durante el desarrollo: con el código de `main`, **11 de las 12**
fallan. La que pasa es el gate con 30 personas habilitadas, que el
planificador resuelve por el btree de `individuo_id` y no por el índice
vectorial; el caso que sí rompe —la mitad del corpus habilitada— es
`test_el_recall_con_un_gate_amplio_es_el_exacto`: con el código viejo
devuelve **22 de 200**.

---

## Paso 4 · Respaldo

No hay migraciones ni escrituras: **no hace falta respaldo**. El script de
diagnóstico solo lee.

---

## Paso 5 · Diagnóstico con el script corregido, contra la base real (5 min)

El script corre desde la máquina de quien despliega: **no necesita deploy**.
Por eso esta sección va antes del paso 7 y sirve también para retomar R-CS
§2.2.

### 5.1 · DoD 1 — A1.2 devuelve la distancia

```bash
python3 scripts/diagnosticar_consulta.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron xiaomi --contra titular
echo "salida: $?"
```

Esperado, para `[query]` y `[document]`:

```
  [query] N respuesta(s) con «xiaomi»; la mejor (#27909, 'Xiaomi') está a 0.xxxx;
          M respuesta(s) del corpus están más cerca del criterio (top_n por defecto: 200).
          recall real (top_n=200): R respuesta(s); entra | NO entra la mejor con «xiaomi».
          «titular»: T respuesta(s), K más cerca que la buscada.
          → …
salida: 0
```

- `N` tiene que coincidir con lo que cuenta `a1-texto` (con `--limite`
  grande) para el mismo patrón.
- **`recall real` tiene que decir `R = 200`** (con el código de producción
  todavía viejo, el script usa el `recuperar` de esta rama, así que ya mide
  bien).
- Si sale **`FALLO DE MEDICIÓN`** con `salida: 2`: no seguir. El patrón está
  y la consulta no lo encontró; es un problema del diagnóstico y no del
  corpus. Revisar que se esté corriendo el script de esta rama
  (`git log -1 -- scripts/diagnosticar_consulta.py`).

### 5.2 · DoD 2 — el conteo no cambió

```bash
python3 scripts/diagnosticar_consulta.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron titular \
  | tee /tmp/a12_despues.txt
diff <(grep -o "[0-9]* respuesta(s) del corpus" /tmp/a12_antes.txt) \
     <(grep -o "[0-9]* respuesta(s) del corpus" /tmp/a12_despues.txt) && echo "conteo igual"
```

Esperado: `conteo igual` y la misma distancia que en `/tmp/a12_antes.txt`
(las respuestas repetidas están entre los vecinos que el índice sí devolvía,
así que la versión vieja también las medía bien). La consulta del conteo es
byte a byte la misma: si el número difiere, es porque cambió la distancia de
referencia, y eso hay que anotarlo con las dos salidas.

### 5.3 · DoD 3 — ausente ≠ no medido

```bash
python3 scripts/diagnosticar_consulta.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron "zzzz-no-existe"
echo "salida: $?"
```

Esperado: `ninguna respuesta del corpus contiene «zzzz-no-existe»: la
evidencia NUNCA ENTRÓ …` y `salida: 1`. Es un mensaje **distinto** del de
`FALLO DE MEDICIÓN` (salida 2), que no debería verse nunca con el script
corregido.

### 5.4 · DoD 4 — el resto del script

No hay nada que correr: la prueba
`test_ninguna_consulta_filtra_y_ordena_por_distancia_sin_materializar` lo
controla en cada corrida. `a1-texto` ordena por `id` (no por distancia) y no
tiene el problema; `input-type` usa `semantica.recuperar`, que ahora pide
`top_n` de verdad: **si A5 ya se había medido con el código viejo, el
recall@200 era en realidad recall@40** y conviene volver a medirlo (R-CS
§2.4).

### 5.5 · Limpiar

```bash
rm -f scripts/_diagnosticar_viejo.py /tmp/diagnosticar_viejo.py
unset EMBEDDINGS_API_KEY
git status --short   # no tiene que quedar nada sin trackear del paso 2
```

---

## Paso 6 · Línea de base de la aplicación (antes del deploy)

Correr en la aplicación, **con el código todavía viejo**, dos consultas que
después se repiten idénticas:

1. **Sin filtro demográfico:** criterio semántico «gente que usa un celular
   xiaomi», resto por defecto.
2. **Mixta:** el mismo criterio + un criterio demográfico amplio (por ejemplo
   sexo = femenino), para que el sistema elija «demográfico primero».

Anotar de cada una: total de personas, quiénes aparecen arriba, y del
diagnóstico la etapa *Recuperación (ANN)* (`pedidos`, `crudos`, `ms`) y la
estrategia de puente. O, desde la base:

```bash
psql "$DSN_BOVEDA" <<'SQL'
select c.id, c.creado_en, c.diagnostico->'puente'->>'estrategia' as estrategia,
       e->>'pedidos' as pedidos, e->>'crudos' as crudos, e->>'plan' as plan,
       e->>'ms' as ms, c.diagnostico->>'total' as total
  from consulta_ejecucion c, jsonb_array_elements(c.diagnostico->'etapas') e
 where e->>'etapa' = 'recall'
 order by c.creado_en desc limit 4;
SQL
```

Esperado con el código viejo: `crudos` cerca de **40** en la sin filtro
(pidiendo 200) y menor todavía en la mixta; `plan` vacío (no existía).

---

## Paso 7 · Desplegar (5 min)

Solo funciones: el frontend no cambió.

```bash
export VPC_CONNECTOR="$(gcloud functions describe api --gen2 --region="$REGION" \
  --project="$PROYECTO" --format='value(serviceConfig.vpcConnector)')"
printf 'Conector VPC: %s\n' "$VPC_CONNECTOR"     # no seguir si está vacío
firebase deploy --only functions --project="$PROYECTO"
```

El CLI tiene que **actualizar** `api`, `procesaringesta` y
`procesarconsulta` (las tres importan `panel_api`) y **no crear ni borrar**
ninguna. No aceptar borrados.

```bash
for f in api procesaringesta procesarconsulta; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(state,serviceConfig.vpcConnector)'
done
```

Las tres `ACTIVE` y con el **mismo** conector.

> `procesarconsulta` (la consulta completa) no usa `recuperar`: recorre las
> unidades a fuerza bruta desde R-CS. Se redespliega solo porque comparte el
> paquete.

---

## Paso 8 · Probar en la aplicación (15 min)

Repetir las dos consultas del paso 6, idénticas.

1. **Sin filtro.** Diagnóstico → *Recuperación (ANN)*: `pedidos=200 ·
   crudos=200 · plan=indice`. El total de personas puede cambiar respecto
   del paso 6 (más candidatos en el pool).
2. **Mixta.** *Recuperación (ANN)*: `plan=exacto`, `filtrado_en_la_base=true`
   y `crudos` = `pedidos` (o el total de respuestas de las personas
   habilitadas, si son menos). Lo esperable es **más personas** que en el
   paso 6.
3. **El caso Xiaomi.** Con la consulta del caso, ¿aparece la persona de la
   respuesta `#27909`? Si §5.1 dijo `entra`, tiene que estar en el pool y
   llegar al reranking; si dijo `NO entra`, son más de 200 las respuestas
   más cercanas y la que la alcanza es la consulta completa o un *Pool de
   recuperación* mayor (probar con 800 en *Parámetros*).
4. **El registro.** La misma consulta SQL del paso 6: las filas nuevas
   tienen `plan` (`indice` / `exacto`).
5. **El log.** Ningún `[recall]` esperado:

   ```bash
   gcloud functions logs read api --gen2 --region="$REGION" --project="$PROYECTO" \
     --limit=500 | grep "\[recall\]" | tail -5
   ```

   Si aparece `[recall] el índice devolvió X de Y pedidas…`, la consulta
   salió bien (se recalculó exacta), pero el índice está devolviendo menos de
   lo que se le pide: anotar `X`, `Y` y la versión de pgvector (§1.2). Ver
   «Errores y correcciones».
6. **COLOQUIO.** Una consulta desde COLOQUIO (o `scripts/token_coloquio.sh` y
   la ruta de consulta) sigue con `version_contrato: 2` y sin errores:

   ```bash
   python3 scripts/verificar_coloquio.py
   ```

   Sin fallidos (no cambió la superficie; es una comprobación de rutina).

### 8.1 · Latencia

Del SQL del paso 6, comparar el `ms` de la etapa `recall` antes y después.

| Caso | Esperado |
|---|---|
| Sin filtro (`plan=indice`, `ef_search` 200) | del orden de decenas de ms; unos pocos más que antes |
| Mixta (`plan=exacto`) | proporcional a las respuestas de las personas habilitadas; con el corpus actual (~21 mil), < 300 ms en la instancia de la semántica |

Si la mixta supera **1 segundo** de recall de forma sostenida, anotarlo: es
la señal para pasar a `hnsw.iterative_scan` (D71, «Lo que cuesta»). No es un
bloqueante del despliegue: antes era rápido **porque devolvía de menos**.

---

## Paso 9 · Cerrar A1 de R-CS (5 min)

El bug cierra una pregunta del addendum y deja otra abierta. Completar la
tabla de R-CS §2.5 con lo medido en el paso 5:

| Verificación | Resultado | Conclusión |
|---|---|---|
| A1.1 — ¿la respuesta está? | Sí: `'¿De qué marca es tu celular? → Xiaomi'`, con la etiqueta resuelta | **Hipótesis de ingesta cerrada** |
| A1.2 — distancia `document` | (§5.1) distancia, `M` delante, entra / no entra | recorte |
| A1.2 — distancia `query` | (§5.1) | |
| Pool real del recall | 40 de 200 pedidos (paso 2); 200 de 200 (paso 8) | El recorte era doble: repetidas **y** un pool de un quinto |
| A1.3 — reranker | **pendiente** → §9.1 | |
| A5 — recall@200 | re-medir si se midió con el código viejo (§5.4) | |

### 9.1 · A1.3 — si el reranker corrió (sigue pendiente)

```bash
python3 scripts/diagnosticar_consulta.py a1-reranker --ultimas 5
```

- *«EL RERANKER NO CORRIÓ (o corrió a medias)»* → un reranker degradado
  también explica por qué 25 respuestas idénticas ocuparon el top aun dentro
  del pool: revisar `RERANKER_API_KEY` (Secret Manager **y** `SECRETOS` en
  `functions/main.py`).
- *«el reranker corrió sin degradaciones»* → la causa fue el recorte, y lo
  resuelven el cambio 3 de R-CS y este arreglo.

Las ejecuciones previas a la `boveda/0025` no tienen registro: se contesta
con las consultas del paso 8.

---

## Paso 10 · Volver atrás

**Todo el código:** desplegar el commit anterior.

```bash
git checkout <commit-anterior>
firebase deploy --only functions --project="$PROYECTO"
```

No hay migraciones ni configuración que revertir. Las filas de
`consulta_ejecucion` con `plan` en el diagnóstico quedan: el código anterior
no lee esa clave.

**Por qué no hay un interruptor por configuración.** Volver al plan viejo es
volver a un recall que devuelve de menos sin decirlo; no hay un caso en que
eso sea preferible. Si la latencia de la mixta fuera el problema (§8.1), la
salida es `hnsw.iterative_scan`, no el plan viejo.

---

## Paso 11 · Aviso a los analistas

Texto sugerido:

> Corregimos un problema de la consulta semántica: la búsqueda traía como
> mucho unas 40 respuestas candidatas aunque el *Pool de recuperación*
> dijera 200, y con un filtro demográfico podía dejar afuera a personas que
> cumplían el filtro. Desde hoy: (1) el pool trae lo que se pide; (2) las
> consultas con filtro demográfico buscan entre **todas** las personas que lo
> cumplen. Con la misma consulta pueden aparecer **más personas** que antes,
> sobre todo en las consultas mixtas: es el arreglo. El costo de verificación
> no cambia (lo sigue mandando *A verificar*). En el diagnóstico, la etapa
> *Recuperación* dice ahora cómo se buscó (`plan`). Detalle en el manual,
> 6.7 y 6.9.

---

## Errores y correcciones

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `a1-distancia` dice `FALLO DE MEDICIÓN` (salida 2) | Se está corriendo el script viejo, o algo nuevo impide la medición | `git log -1 -- scripts/diagnosticar_consulta.py`; si es el de esta rama, abrir un bug con la salida completa: **no** interpretarlo como «no está» |
| `a1-distancia` dice «NUNCA ENTRÓ» (salida 1) y `a1-texto` sí encuentra | No debería poder pasar: los dos usan el mismo `ilike` | Comparar el patrón exacto en los dos comandos (espacios, comillas) |
| `recall real … NO entra` y `delante < 200` | Recall corto por el índice | Buscar `[recall]` en el log; si no aparece, es un bug del arreglo: abrirlo con la salida |
| `[recall] el índice devolvió X de Y` en el log | El índice HNSW no alcanza `top_n` aun con `ef_search` = `top_n` (tuplas muertas, un índice degradado) | La consulta salió bien (recalculó exacto). `reindex index concurrently` del índice HNSW en una ventana de baja carga y volver a mirar |
| `unrecognized configuration parameter "hnsw.ef_search"` en el log de `api` | pgvector sin cargar en una versión que reserva el prefijo | No debería pasar (`set_config` crea el placeholder); anotar la versión de §1.2 y abrir un bug |
| El recall de la mixta tarda más de 1 s | Recorrido exacto sobre muchas personas | Anotar el `ms` y el número de personas habilitadas; evaluar `hnsw.iterative_scan` (D71) |
| La prueba `test_el_corpus_reproduce_la_trampa_del_indice` falla en una máquina | El Postgres de pruebas no tiene pgvector, o el planificador eligió otro plan | `apt-get install postgresql-16-pgvector`; si persiste, mirar el `explain` de la consulta vieja |
| Una consulta trae más personas que ayer | El arreglo (§1, «Cambio de comportamiento visible») | Esperado; mirar `plan` en el diagnóstico |

---

## Checklist

- [ ] Paso 1: R-CS desplegado (o el camino «a medio desplegar» elegido); versión de pgvector y `ef_search` anotados.
- [ ] Paso 2: bug reproducido con el script de `main`; línea de base del conteo y del pool (≈40) anotadas.
- [ ] Paso 3: `test_bug_diagnostico_a1.py` (12) y la batería completa en verde; `chequear_js.sh` en verde.
- [ ] §5.1 DoD 1: `a1-distancia --patron xiaomi` mide, salida 0, `recall real` 200.
- [ ] §5.2 DoD 2: conteo igual al de la línea de base.
- [ ] §5.3 DoD 3: patrón inexistente → «NUNCA ENTRÓ», salida 1.
- [ ] §5.5: script viejo borrado, clave fuera del entorno.
- [ ] Paso 6: línea de base de las dos consultas en la aplicación.
- [ ] Paso 7: deploy de funciones; las tres `ACTIVE` con el mismo conector; nada creado ni borrado.
- [ ] Paso 8: `plan=indice` 200/200 sin filtro; `plan=exacto` en la mixta; caso Xiaomi revisado; sin `[recall]` en el log; `verificar_coloquio.py` sin fallidos.
- [ ] §8.1: latencia del recall antes/después anotada.
- [ ] Paso 9: tabla de R-CS §2.5 completada; A1.3 corrido.
- [ ] Paso 11: aviso a analistas.

## Referencias

- `specs/BUG_diagnostico_a1_distancia_y_hallazgo.md` — el bug
- `docs/decisiones.md` — **D71** (y D70 para R-CS)
- `docs/DESPLIEGUE - R-CS recuperación y verificación de consultas semánticas.md` — §2 (A1), §2.5
- `CLAUDE.md` — «El índice vectorial no sabe de filtros»
- Manual de usuario — 6.7, 6.9, preguntas frecuentes, glosario
