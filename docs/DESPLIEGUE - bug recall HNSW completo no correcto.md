# Despliegue — El recall por índice: completo no es correcto (D72)

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Pedido que cubre:** `specs/BUG_recall_hnsw_completo_no_correcto.md`,
**en su totalidad**:

| § del bug | Qué pide | Dónde quedó |
|---|---|---|
| §4 · recomendado | Sin filtro, camino exacto mientras el corpus esté bajo un umbral configurable (200 mil) | `semantica._vecinos` · `RECALL_EXACTO_HASTA` · §4 de este documento |
| §4 · recomendado | El `informe` sigue diciendo el plan | `plan`, más `motivo_plan` y `ef_search` |
| §4 · cuando crezca | Por encima del umbral, `ef_search = 2 × top_n` con tope | `semantica.ef_search_para` |
| §4 · cuando crezca | Verificar `m` / `ef_construction` del índice | `recall-indice` los informa · §9 |
| §4 · salvaguarda | Chequeo de recall real, índice contra exacto | `semantica.comparar_recall` · `diagnosticar_consulta.py recall-indice` |
| §1 · nota | La comparación «viejo vs. nuevo» de D71 comparaba el script consigo mismo | Despliegue D71 corregido (`e7c0eba`); `scripts/_diagnosticar_viejo.py` borrado e ignorado |
| §5 | Conclusiones excluyentes; etiqueta «FALLO DE MEDICIÓN» corregida | `conclusiones()` · «EL RECALL NO LA TRAJO (plan=…)» |
| §5 | Revisar qué eran las respuestas repetidas | `a1-distancia` lista los textos repetidos del pool · §7.3 |
| §6 | Pruebas de aceptación (6) | `functions/tests/test_bug_recall_hnsw.py` (16) |
| §7 | Costos | §1 y §8.2 de este documento · `docs/COSTOS.md` 4.4 |

**Duración estimada:** 60 a 75 minutos. 20 de diagnóstico contra la base
real (antes y después), 10 de pruebas locales, 5 de deploy, 20 de prueba en
la aplicación y la medición del índice.

---

## 1 · Qué trae, en una página

### La causa

HNSW es **aproximado**. Con `ef_search` igual a `top_n`, el índice puede
devolver las 200 filas pedidas dejando afuera al vecino más cercano. El
control de D71 miraba si el recall venía **corto**; este viene completo, con
el contenido equivocado. En producción: la respuesta de Xiaomi era **la más
cercana de todo el corpus** (0 delante) y no entraba entre las 200.

Pasa solo en el plan `indice`: consultas **semánticas puras**, sin filtro
demográfico. Con filtro, el recall ya era exacto desde D71.

### Qué cambia

- **Recall sin filtro, exacto bajo el umbral.** Mientras el corpus no pase de
  `RECALL_EXACTO_HASTA` (default **200.000**; hoy hay ~21.340), el recall
  compara el criterio contra **todas** las respuestas y devuelve las más
  cercanas de verdad. El tamaño sale del catálogo de Postgres
  (`pg_class.reltuples`), sin contar la tabla.
- **Por encima del umbral, índice con margen:** `ef_search = 2 × top_n`
  (tope 1000). Con `top_n` ≥ 1000 no hay margen posible y va exacto.
- **Diagnóstico:** la etapa *Recuperación (ANN)* dice `plan`, `motivo_plan`
  (`filtro_por_personas`, `corpus_bajo_umbral`, `corpus_sobre_umbral`,
  `top_n_sin_margen_en_el_indice`) y, con índice, `ef_search`.
- **`scripts/diagnosticar_consulta.py`:**
  - `recall-indice` (nuevo): para uno o más criterios, compara índice contra
    exacto y dice cuántas de las `top_n` exactas faltan, cuáles y en qué
    puesto. Informa `m` y `ef_construction` del índice y qué plan usa
    producción. Sale con 1 si falta alguna.
  - `a1-distancia`: conclusiones **excluyentes**. Si la respuesta no entró
    al recall, no dice nada del recorte. La etiqueta es **«EL RECALL NO LA
    TRAJO (plan=…)»**: es el comportamiento del sistema, no una falla de la
    medición. Lista además los textos que más se repiten en el pool.
- **Configuración:** `RECALL_EXACTO_HASTA`, documentada en
  `functions/.env.ejemplo` (comentada: el default alcanza).

### Qué NO cambia

Ninguna migración, ningún `grant`, ninguna vista, ningún secreto, ninguna
cola, el frontend (el diagnóstico pinta cualquier clave de la etapa), la
superficie ni el contrato de COLOQUIO (`version_contrato: 2`). **No se
re-embebe nada.** El recall **con** filtro demográfico sigue igual que en D71.

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Cloud Tasks | Código |
|---|---|---|---|---|---|---|
| D72 | — | — (reindex opcional, §9) | — | `RECALL_EXACTO_HASTA` opcional | — | `semantica.py`, script, pruebas |

### Cambio de comportamiento visible

- **Consultas semánticas sin filtro:** el pool son las 200 más cercanas de
  verdad. Pueden entrar personas que antes no entraban —la del caso Xiaomi
  es la primera que tiene que aparecer— y salir otras que entraban por
  aproximación.
- **Latencia del recall sin filtro:** sube. Medido en local, el recorrido
  exacto de 21.340 vectores de 512 dimensiones tarda **~115 ms**. En la
  instancia de la semántica hay que medirlo (§8.2). La verificación con
  Claude, en la misma consulta, tarda segundos.
- **Costo:** US$ 0 de diferencia. Se embebe el mismo criterio y se verifica
  la misma cantidad (*A verificar*).

---

## Paso 0 · Preparar la terminal

```bash
setopt INTERACTIVE_COMMENTS 2>/dev/null || true
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
```

Túneles y DSN (no abrir otra copia si ya están):

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda   --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA"    -tAc "select current_database()"   # paneles_boveda
psql "$DSN_SEMANTICA" -tAc "select current_database()"   # paneles_semantica
```

Dependencias del script y la rama:

```bash
pip install "psycopg[binary]~=3.1" requests
git fetch origin claude/jolly-wright-wzcej1
git checkout claude/jolly-wright-wzcej1
git log --oneline -1
```

La clave de Voyage, para todo lo que embebe un criterio (no se imprime ni
se guarda; se borra en §5.5):

```bash
export EMBEDDINGS_API_KEY="$(gcloud secrets versions access latest --secret=EMBEDDINGS_API_KEY --project="$PROYECTO")"
```

---

## Paso 1 · Punto de partida

### 1.1 · D71 desplegado

Este arreglo va **encima de D71** (`9b24e78`, PR #60). Confirmar que lo que
corre en producción lo tiene: la etapa *Recuperación (ANN)* del diagnóstico
de cualquier consulta reciente muestra `plan=…`. O desde la base:

```bash
psql "$DSN_BOVEDA" -c "select c.creado_en, e->>'plan' as plan
                         from consulta_ejecucion c, jsonb_array_elements(c.diagnostico->'etapas') e
                        where e->>'etapa' = 'recall' order by c.creado_en desc limit 3"
```

Si `plan` sale vacío en todas, D71 no está desplegado: desplegar primero con
`docs/DESPLIEGUE - bug A1.2 distancia e índice vectorial.md` (su paso 2 ya
está corregido para comparar contra `e7c0eba`).

### 1.2 · El corpus, pgvector y el índice

```bash
psql "$DSN_SEMANTICA" <<'SQL'
select extversion from pg_extension where extname = 'vector';
select reltuples::bigint as estimado, (select count(*) from respuesta) as real
  from pg_class where oid = 'respuesta'::regclass;
select c.relname, c.reloptions, pg_size_pretty(pg_relation_size(c.oid)) as tamano
  from pg_index i join pg_class c on c.oid = i.indexrelid join pg_am a on a.oid = c.relam
 where i.indrelid = 'respuesta'::regclass and a.amname = 'hnsw';
SQL
```

Anotar (§11):

- **versión de pgvector**;
- **`estimado` y `real`**: tienen que estar cerca. Si `estimado` es -1 o muy
  distinto, correr `analyze respuesta;` (el plan se decide con el estimado;
  el código cuenta solo si la tabla nunca se analizó);
- **el índice:** `reloptions` vacío quiere decir los defaults (`m=16`,
  `ef_construction=64`). Lo dejó así la `semantica/0006`.

---

## Paso 2 · El «antes», medido (10 min)

Con el script de **esta rama**, que ya trae `recall-indice`. Mide el índice
de producción tal como lo usa hoy el código de D71: `ef_search = top_n`.

### 2.1 · Cuánto pierde el índice con el plan de D71

```bash
cat > /tmp/criterios_recall.json <<'JSON'
[
  {"criterio": "gente que usa un celular xiaomi"},
  {"criterio": "gente a la que le gusta el fernet"},
  {"criterio": "personas que viajan al exterior por trabajo"}
]
JSON
# Sumar criterios reales de los analistas: cuantos más, mejor la medición.

python3 scripts/diagnosticar_consulta.py recall-indice \
  --consultas /tmp/criterios_recall.json --ef-search 200 | tee /tmp/recall_d71.txt
echo "salida: $?"
```

`--ef-search 200` reproduce el plan de D71 (`ef_search = top_n`). Esperado,
por lo que mostró el bug: en el criterio Xiaomi, **`faltan ≥ 1`** con un
`puesto 1 del exacto`, y `salida: 1`. Anotar el `recall@200` de cada línea.

### 2.2 · Y con el margen que usaría producción por encima del umbral

```bash
python3 scripts/diagnosticar_consulta.py recall-indice \
  --consultas /tmp/criterios_recall.json | tee /tmp/recall_margen.txt
```

Sin `--ef-search` usa `2 × top_n` (400). Anotar el `recall@200`. No es el
plan que va a usar producción hoy (hoy va exacto), pero dice cuánto rinde el
índice el día que se cruce el umbral, y alimenta la decisión de §9.

### 2.3 · El caso del bug, con el script de D71

```bash
git show 9b24e78:scripts/diagnosticar_consulta.py > scripts/_diagnosticar_viejo.py   # está en .gitignore
python3 scripts/_diagnosticar_viejo.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron xiaomi --contra titular \
  | tee /tmp/a12_d71.txt
echo "salida: $?"
rm -f scripts/_diagnosticar_viejo.py
```

> **Ojo: el script viejo importa `semantica.recuperar` de la rama actual.**
> Por eso, para el «antes» del recall vale §2.1 y no esta salida: acá el
> recall ya va exacto y la línea `recall real` puede decir `entra`. Lo que
> esta salida sirve para ver es la **contradicción** de las conclusiones
> («FALLO DE MEDICIÓN» y «fue el RECORTE» a la vez) cuando el recall no la
> trae. Si se quiere el antes completo, correrlo con la rama en `9b24e78`
> (`git checkout 9b24e78 -- functions/panel_api/semantica.py`, correr, y
> `git checkout HEAD -- functions/panel_api/semantica.py`).

---

## Paso 3 · Pruebas antes de subir (10 min)

Con el Postgres de pruebas **con pgvector** (desde D71):

```bash
# Debian/Ubuntu, una sola vez:  sudo apt-get install -y postgresql-16-pgvector
source scripts/pg_pruebas.sh
cd functions
python3 -m pytest -q tests/test_bug_recall_hnsw.py tests/test_bug_diagnostico_a1.py   # 16 + 12
python3 -m pytest -q                                                                  # todas
cd ..
```

Todo verde. Qué cubre `test_bug_recall_hnsw.py`:

| Prueba | §6 del bug |
|---|---|
| `test_el_corpus_reproduce_el_caso` | El fixture: la respuesta de Xiaomi es la más cercana (0 delante) |
| `test_sin_filtro_la_mejor_respuesta_entra_aunque_el_indice_se_equivoque` | 1 |
| `test_con_el_indice_equivocado_la_mejor_no_entraba` | El defecto reproducido: 200 de 200, sin la mejor, sin `indice_corto` |
| `test_bajo_el_umbral_…_coincide_con_el_exhaustivo` · `test_el_umbral_se_lee_del_entorno…` · `test_el_tamano_del_corpus…` | 2 |
| `test_sobre_el_umbral_…_ef_search_mayor_que_top_n` · `test_el_margen_respeta_el_tope…` · `test_sin_margen_posible…` | 3 |
| `test_el_chequeo_de_recall_real_reporta…` · `…_contra_el_indice_de_verdad` · `test_recall_indice_del_script_sale_con_1…` | 4 |
| `test_las_conclusiones_son_excluyentes` · `test_a1_distancia_con_el_indice_equivocado_no_dice_recorte` · `test_a1_distancia_con_el_plan_de_produccion_la_trae` | 5 |
| `test_con_filtro_sigue_el_camino_exacto` | 6 |

> **Sobre el «índice equivocado» de las pruebas.** Un HNSW que pierda al
> vecino más cercano no se puede provocar de forma determinística. Las
> pruebas usan un doble explícito que devuelve las `top_n` pedidas **sin**
> la más cercana —exactamente lo que se vio en producción— y una prueba
> aparte usa el índice de verdad con un `ef_search` mínimo, para que el
> chequeo de recall real se pruebe también sin dobles. La comprobación con el
> índice real y el corpus real es el §2.1 y el §5.

Con el código de `main`, todas fallan o no pueden correr (no existen
`_vecinos_indice`, `comparar_recall` ni `conclusiones`), salvo la que solo
verifica el fixture.

---

## Paso 4 · Configuración (`functions/.env`)

**Por defecto no hay nada que hacer:** sin la variable, el umbral es
200.000.

Solo si se decide otro umbral (por ejemplo, porque la latencia de §8.2 sale
alta en el tier actual), agregar a `functions/.env`:

```bash
# D72 — hasta cuántas respuestas el recall sin filtro es exacto
RECALL_EXACTO_HASTA=200000
```

Valores: un entero ≥ 0; `0` = siempre el índice (con `ef_search = 2 × top_n`).
Un valor ilegible no apaga nada: se usa el default y queda
`[recall] RECALL_EXACTO_HASTA=… no es un entero ≥ 0` en el log.

No es un secreto: **no** va a Secret Manager ni a `SECRETOS`.

---

## Paso 5 · Diagnóstico con el script corregido, contra la base real (5 min)

El script corre local con el `recuperar` de esta rama, así que mide el plan
**nuevo** antes del deploy.

### 5.1 · §6.1 — la mejor respuesta entra

```bash
python3 scripts/diagnosticar_consulta.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron xiaomi --contra titular \
  | tee /tmp/a12_d72.txt
echo "salida: $?"
```

Esperado, para `[query]` y `[document]`:

```
  [query] 218 respuesta(s) con «xiaomi»; la mejor (#41197, 'Xiaomi') está a 0.3712;
          0 respuesta(s) del corpus están más cerca del criterio (top_n por defecto: 200).
          recall real (top_n=200, plan=exacto, corpus_bajo_umbral): 200 respuesta(s); entra la mejor con «xiaomi».
          textos más repetidos en el pool:
            N× [varXXX] '…'
          «titular»: 1 respuesta(s), K más cerca que la buscada.
          → …
salida: 0
```

- **`entra`** en las dos líneas, con `plan=exacto, corpus_bajo_umbral`.
- **`salida: 0`**. Si sale `2` con «EL RECALL NO LA TRAJO (plan=exacto)»,
  **no seguir**: con el plan exacto eso no puede pasar. Es un bug nuevo;
  guardar la salida.
- Nunca deben aparecer en la misma línea «NO entra» y «fue el RECORTE».

### 5.2 · §6.4 — el chequeo de recall real con el plan de producción

```bash
python3 scripts/diagnosticar_consulta.py recall-indice --consultas /tmp/criterios_recall.json | head -3
```

La primera línea tiene que decir `producción usa EXACTO sin filtro`. El resto
repite §2.2 (el índice no se usa hoy).

### 5.3 · Un patrón ausente sigue dando «NUNCA ENTRÓ»

```bash
python3 scripts/diagnosticar_consulta.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron "zzzz-no-existe"
echo "salida: $?"   # 1
```

### 5.4 · Dejar anotado

Guardar `/tmp/recall_d71.txt`, `/tmp/recall_margen.txt` y `/tmp/a12_d72.txt`
junto al ticket del despliegue. **No** commitearlos: tienen contenido de
encuesta.

### 5.5 · Limpiar

```bash
unset EMBEDDINGS_API_KEY
git status --short    # no tiene que quedar nada sin trackear
```

---

## Paso 6 · Línea de base de la aplicación (antes del deploy)

En la aplicación, con el código todavía de D71, correr **sin filtro
demográfico** el criterio «gente que usa un celular xiaomi», resto por
defecto. Anotar:

- total de personas y si aparece la de la respuesta #41197 / #51159;
- de la etapa *Recuperación (ANN)*: `pedidos`, `crudos`, `plan`, `ms`.

O desde la base:

```bash
psql "$DSN_BOVEDA" <<'SQL'
select c.id, c.creado_en, e->>'plan' as plan, e->>'motivo_plan' as motivo,
       e->>'ef_search' as ef_search, e->>'pedidos' as pedidos, e->>'crudos' as crudos,
       e->>'ms' as ms_recall, c.diagnostico->>'total' as total
  from consulta_ejecucion c, jsonb_array_elements(c.diagnostico->'etapas') e
 where e->>'etapa' = 'recall'
 order by c.creado_en desc limit 5;
SQL
```

Esperado con D71: `plan=indice`, sin `motivo_plan`, `crudos=200`.

---

## Paso 7 · Desplegar (5 min)

Solo funciones: el frontend no cambió.

```bash
export VPC_CONNECTOR="$(gcloud functions describe api --gen2 --region="$REGION" \
  --project="$PROYECTO" --format='value(serviceConfig.vpcConnector)')"
printf 'Conector VPC: %s\n' "$VPC_CONNECTOR"     # no seguir si está vacío
firebase deploy --only functions --project="$PROYECTO"
```

El CLI **actualiza** `api`, `procesaringesta` y `procesarconsulta` (las tres
importan `panel_api`); no crea ni borra ninguna. No aceptar borrados.

```bash
for f in api procesaringesta procesarconsulta; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(state,serviceConfig.vpcConnector)'
done
```

Las tres `ACTIVE` y con el mismo conector.

> `procesarconsulta` (la completa) no usa `recuperar`: recorre las unidades
> a fuerza bruta desde R-CS. Se redespliega solo porque comparte el paquete.

---

## Paso 8 · Probar en la aplicación (20 min)

### 8.1 · El caso del bug

1. **Xiaomi, sin filtro.** La misma consulta del paso 6. Diagnóstico →
   *Recuperación (ANN)*: `plan=exacto · motivo_plan=corpus_bajo_umbral ·
   pedidos=200 · crudos=200`. La respuesta de la marca tiene que llegar al
   ranking (o a la verificación): es la más cercana de todo el corpus.
2. **Mixta (con filtro demográfico).** `plan=exacto ·
   motivo_plan=filtro_por_personas`, como en D71.
3. **El registro.** El SQL del paso 6: las filas nuevas traen `motivo_plan`.
4. **El log.** Ninguna línea `[recall]`:

   ```bash
   gcloud functions logs read api --gen2 --region="$REGION" --project="$PROYECTO" \
     --limit=500 | grep "\[recall\]" | tail -5
   ```

   Si aparece `RECALL_EXACTO_HASTA=… no es un entero`, corregir el valor de
   `functions/.env` (§4).

5. **COLOQUIO.** `python3 scripts/verificar_coloquio.py` sin fallidos (no
   cambió la superficie; es la comprobación de rutina).

### 8.2 · Latencia (§7 del bug)

Comparar el `ms` de la etapa `recall` del paso 6 contra el de 8.1.

| Caso | Esperado |
|---|---|
| Sin filtro (`plan=exacto`, corpus_bajo_umbral) | del orden de 100–300 ms con ~21 mil respuestas (115 ms medidos en local; la instancia de la semántica puede ser más lenta) |
| Mixta (`plan=exacto`, filtro_por_personas) | igual que con D71 |

- **Por debajo de 1 s**: dejar el default.
- **Más de 1 s sostenido** en la sin filtro: anotarlo con el tier de
  `paneles-semantica`. Opciones, en este orden: (a) aceptar, si el total de
  la consulta no cambia de forma perceptible (la verificación tarda
  segundos); (b) subir el tier de la semántica (`docs/COSTOS.md` 3–4);
  (c) bajar `RECALL_EXACTO_HASTA` por debajo del corpus, **solo después** de
  medir el recall del índice con §9 y aceptar lo que pierde.

---

## Paso 9 · El índice: medir y decidir si se reconstruye (opcional, 10 min)

Bajo el umbral el índice **no se usa**, así que nada de este paso cambia
ninguna consulta hoy. Sirve para el día que se cruce el umbral.

1. **El número:** `recall@200` de §2.2 (con `ef_search` 400).
2. **Si es 100 % en todos los criterios:** no hacer nada.
3. **Si no:** la `0006` dejó el índice con los defaults (`m=16`,
   `ef_construction=64`). Probar una reconstrucción con más calidad, en una
   ventana de baja carga (no bloquea lecturas ni escrituras):

   ```bash
   psql "$DSN_SEMANTICA" <<'SQL'
   set maintenance_work_mem = '256MB';
   create index concurrently respuesta_embedding_idx_v2
       on respuesta using hnsw (embedding vector_cosine_ops)
       with (m = 16, ef_construction = 128);
   SQL
   python3 scripts/diagnosticar_consulta.py recall-indice --consultas /tmp/criterios_recall.json
   ```

   Si el `recall@200` mejora, quedarse con el nuevo:

   ```bash
   psql "$DSN_SEMANTICA" -c "drop index concurrently respuesta_embedding_idx" \
                         -c "alter index respuesta_embedding_idx_v2 rename to respuesta_embedding_idx"
   ```

   Si no mejora, `drop index concurrently respuesta_embedding_idx_v2`.

> **Esto no es una migración** a propósito: es una decisión de operación con
> un número detrás, y la estructura (un índice HNSW coseno sobre
> `respuesta.embedding`) no cambia. Si se adopta, anotarlo en D72 y, en la
> próxima migración de la semántica, recrearlo con esos parámetros para que
> un entorno nuevo nazca igual. `verificar_esquema.py` controla que el índice
> exista, no sus parámetros.

---

## Paso 10 · Corregir la conclusión de A1 (R-CS §2.5)

D71 había concluido que la causa del caso Xiaomi era **el recorte por
repetición** (cambio 3). Con este bug, para ese caso, **no se sostiene**: la
evidencia no entraba al recall, que es anterior al recorte. Actualizar la
tabla de R-CS §2.5:

| Verificación | Resultado | Conclusión |
|---|---|---|
| A1.1 — ¿la respuesta está? | Sí, con la etiqueta resuelta | Ingesta descartada |
| A1.2 — distancia | 0 delante, con los dos `input_type` | El embedding está bien: es la más cercana |
| A1.2 — ¿entraba al recall? | **No** con el plan de D71 (§2.1); **sí** con D72 (§5.1) | **Causa: el recall por índice** |
| Recorte por repetición (cambio 3) | `--contra titular` encontró 1, no 25 | **No demostrado por este caso** |
| Textos repetidos en el pool | (lista de §5.1) | Revisar abajo |
| A1.3 — reranker | `a1-reranker --ultimas 5` | |

### 10.1 · Qué eran las respuestas repetidas

Con la lista `textos más repetidos en el pool` de §5.1 (y la de una consulta
del resultado original, si se tiene el criterio exacto):

- Si un mismo texto ocupa decenas de lugares del pool, el cambio 3 (unidades
  de evidencia) **sigue valiendo** por esa razón: anotar el texto y cuántas
  veces aparece.
- Si no hay repeticiones relevantes, anotar que el cambio 3 queda como
  mejora general, **sin** el caso Xiaomi como justificación.

En los dos casos el cambio 3 ya está desplegado (R-CS): esto corrige el
registro de **por qué**, no el código.

### 10.2 · A1.3

```bash
python3 scripts/diagnosticar_consulta.py a1-reranker --ultimas 5
```

Anotar si el reranker corrió en las consultas del paso 8.

---

## Paso 11 · Volver atrás

**Solo el plan, sin redesplegar código** (por ejemplo, si la latencia de
§8.2 es inaceptable y ya se midió el índice con §9):

```bash
gcloud run services update api --region="$REGION" --project="$PROYECTO" \
  --update-env-vars=RECALL_EXACTO_HASTA=0
```

y dejar el mismo valor en `functions/.env` para que el próximo `firebase
deploy` no lo pise. Con `0` el recall sin filtro va por el índice con
`ef_search = 2 × top_n`: **mejor que D71, pero aproximado**. El diagnóstico
lo muestra como `motivo_plan=corpus_sobre_umbral`.

**Todo el código:** desplegar el commit anterior.

```bash
git checkout b56a910        # main con D71, antes de D72
firebase deploy --only functions --project="$PROYECTO"
```

Sin migraciones ni secretos que revertir. Si en §9 se reconstruyó el índice,
puede quedar: es el mismo índice con otros parámetros.

---

## Paso 12 · Aviso a los analistas

Texto sugerido:

> Corregimos otro problema de la búsqueda semántica: en las consultas
> **sin filtro demográfico**, la búsqueda rápida podía traer las 200
> respuestas candidatas **dejando afuera a la más parecida de todas**, sin
> ningún aviso. Pasaba, por ejemplo, con «gente que usa un celular Xiaomi».
> Desde hoy la búsqueda compara todas las respuestas y trae las más
> parecidas de verdad. Con la misma consulta pueden aparecer personas que
> antes no aparecían (y salir otras). La consulta puede tardar unas décimas
> de segundo más; el costo no cambia. En el diagnóstico, la etapa
> *Recuperación* dice `plan=exacto`. Detalle en el manual, 6.9.

---

## Errores y correcciones

| Síntoma | Causa | Qué hacer |
|---|---|---|
| `a1-distancia` dice «EL RECALL NO LA TRAJO (plan=exacto)» | No debería poder pasar | Guardar la salida y abrir un bug; no interpretarlo como resultado |
| `a1-distancia` dice «EL RECALL NO LA TRAJO (plan=indice)» | El corpus está sobre el umbral (o `RECALL_EXACTO_HASTA` bajo) y el índice perdió la respuesta | §2.2 / §9; subir el umbral o reconstruir el índice |
| `motivo_plan=corpus_sobre_umbral` con ~21 mil respuestas | `RECALL_EXACTO_HASTA` mal puesto, o `reltuples` desactualizado | `printenv` de la función; `analyze respuesta` |
| `[recall] RECALL_EXACTO_HASTA=… no es un entero ≥ 0` | Valor ilegible en `functions/.env` | Corregirlo (§4); mientras, corre con 200.000 |
| `recall-indice` sale con 1 | Al índice le faltan filas frente al exacto | Esperado con `ef_search` = `top_n`; con el plan de producción bajo el umbral no afecta. §9 |
| El recall sin filtro tarda > 1 s | Recorrido exacto en un tier chico | §8.2 |
| `test_el_chequeo_de_recall_real_contra_el_indice_de_verdad` falla en una máquina | Postgres de pruebas sin pgvector, o el planificador no usó el índice | `apt-get install postgresql-16-pgvector` |
| Una consulta sin filtro trae gente distinta que ayer | El arreglo (§1) | Esperado; mirar `plan` en el diagnóstico |

---

## Checklist

- [ ] Paso 1: D71 en producción; versión de pgvector, `reltuples` vs real y parámetros del índice anotados.
- [ ] §2.1: recall@200 con `ef_search` 200 anotado; el criterio Xiaomi muestra `faltan ≥ 1`.
- [ ] §2.2: recall@200 con `ef_search` 400 anotado.
- [ ] §2.3: contradicción de conclusiones del script de D71 vista; copia vieja borrada.
- [ ] Paso 3: `test_bug_recall_hnsw.py` (16), `test_bug_diagnostico_a1.py` (12) y la batería completa en verde.
- [ ] Paso 4: umbral decidido (default o explícito en `functions/.env`).
- [ ] §5.1: Xiaomi **entra**, `plan=exacto`, salida 0, sin «RECORTE» junto a «NO entra».
- [ ] §5.2: `producción usa EXACTO sin filtro`.
- [ ] §5.3: patrón inexistente → salida 1.
- [ ] Paso 6: línea de base en la aplicación.
- [ ] Paso 7: deploy de funciones; las tres `ACTIVE` con el mismo conector.
- [ ] §8.1: Xiaomi en el ranking; `motivo_plan` en las filas nuevas; sin `[recall]`; `verificar_coloquio.py` sin fallidos.
- [ ] §8.2: latencia antes/después anotada y decisión tomada.
- [ ] Paso 9: decidido si se reconstruye el índice (con el número de §2.2).
- [ ] Paso 10: tabla de R-CS §2.5 corregida; repetidas revisadas; A1.3 corrido.
- [ ] Paso 12: aviso a analistas.

## Referencias

- `specs/BUG_recall_hnsw_completo_no_correcto.md` — el bug
- `docs/decisiones.md` — **D72** (y D71, cuya conclusión sobre R-CS corrige)
- `docs/DESPLIEGUE - bug A1.2 distancia e índice vectorial.md` — el antecedente; su paso 2 ya compara contra `e7c0eba`
- `docs/DESPLIEGUE - R-CS recuperación y verificación de consultas semánticas.md` — §2.5
- `docs/COSTOS.md` — 4.4
- `CLAUDE.md` — «El índice vectorial no sabe de filtros»
- Manual de usuario — 6.9, preguntas frecuentes, glosario
