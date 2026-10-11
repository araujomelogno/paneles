# Despliegue — Unidades de evidencia: un vector por texto distinto (R-UE, D73)

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Pedido que cubre:** `specs/SPEC_unidades_de_evidencia.md`, **en su
totalidad** (es el cambio 3 de la solicitud de consultas semánticas, ahora con
evidencia medida):

| § del spec | Qué pide | Dónde quedó |
|---|---|---|
| R-UE.1 | Una unidad por `texto_embebido` (clave: el `hash_texto`, que incluye la pregunta); la respuesta referencia su unidad; el índice vectorial sobre las unidades | `semantica/0010`: tabla `unidad_evidencia`, `respuesta.unidad_id` con FK compuesta, HNSW `unidad_evidencia_embedding_idx` |
| R-UE.2 | Recall entre unidades, resuelto a personas; el gate al resolver personas | `semantica.recuperar` / `_vecinos*` · §9.1 |
| R-UE.3 | `top_k` cuenta unidades distintas; el veredicto llega a todas sus personas; la agregación por persona no cambia | `consultas._seleccionar_y_verificar` · `hallazgo_de` sin cambios · §9.2 |
| R-UE.4 | Ingesta: texto existente ⇒ sin embedding; reproceso e ingesta diferida siguen | `semantica.escribir_respuestas` (camino único de `ingesta` y `reproceso`) · §9.4 |
| R-UE.5 | Migración a US$ 0, verificando antes que mismo texto ⇒ mismo vector; `respuesta.embedding` se conserva y se quita después | `semantica/0010` (se detiene si hay inconsistencias) · `diagnosticar_consulta.py unidades` · `semantica/posteriores/0011` · Pasos 2, 5 y 12 |
| §4 | Corrige el costo de la completa (A2) y el plan «volver al índice» (D72) | `docs/COSTOS.md` 4.4 y §5 · manual 6.11 · D73 |
| §5 | Pruebas de aceptación (9) | `functions/tests/test_unidades_de_evidencia.py` (17) · Paso 3 |
| §6 | Costos | §1 de este documento · `docs/COSTOS.md` |
| §7 [datos] | Entender textos con más de un vector antes de migrar | Paso 2.2 y Paso 5 |
| §7 [producto] | El diagnóstico muestra cuántas personas hay detrás de cada unidad | etapa *Unidades* (`personas_por_unidad_*`) y `personas_en_la_unidad` en el detalle · §9.3 |
| §7 [ingeniería] | Reidentificación, ficha y exportación no asumen un vector por respuesta | Revisado: ninguna leía el vector · §1 |
| §7 [alcance] | Después de que el recall exacto esté estable, con respaldo | Paso 1 y Paso 4 |

**Duración estimada:** 90 a 120 minutos, más el período de observación antes
del Paso 12 (al menos una semana de uso normal, con una ingesta y un
reproceso de por medio). 20 de diagnóstico previo, 10 de pruebas locales, 10
de respaldo y migración, 10 de deploy, 30 de prueba en la aplicación, 15 de
medición posterior.

---

## 1 · Qué trae, en una página

### La causa

Medido sobre el corpus real (2026-10-11): **21.340 respuestas, 861 textos
distintos**. El 96 % son copias: en una pregunta cerrada, todas las personas
que eligen la misma opción producen el mismo texto embebido —«¿De qué marca
es tu celular? → Xiaomi»— y el mismo vector. Dos síntomas, una causa:

1. **El índice HNSW no alcanza el bloque.** Cientos de puntos idénticos que
   la búsqueda no encuentra desde afuera: `recall-indice` con
   `ef_search=200` dio **0 %** para «celular xiaomi» y «fernet» (`query`).
   Hoy no se nota porque el recall es exacto bajo el umbral (D72), pero era
   la salida prevista para cuando el corpus creciera, y con duplicados no
   funciona.
2. **La verificación se gasta en copias.** `top_k = 25` contaba personas, y
   25 personas podían ser 25 veces la misma idea.

### Qué cambia

- **Base semántica (`semantica/0010`):** tabla `unidad_evidencia`
  (`hash_texto` único, `texto_embebido`, `embedding vector(512)`), con su
  índice HNSW. `respuesta.unidad_id` (not null) con FK compuesta
  `(unidad_id, hash_texto)`. `respuesta.hash_texto` pasa a `not null`. Dos
  triggers: uno borra la unidad que se queda sin respuestas (la baja se lleva
  el vector), otro mantiene `respuesta.embedding` desde la unidad durante la
  transición. `v_unidad_evidencia` se rehace sobre la tabla (respuestas,
  personas y preguntas por unidad). `veredicto_unidad` pasa a ser único por
  `(ejecución, criterio, hash_texto)`.
- **Recall:** busca entre **unidades**; `top_n` (*Pool de recuperación*)
  cuenta unidades y cada una vuelve con todas sus respuestas. Con filtro
  demográfico, solo las respuestas de personas habilitadas.
- **Verificación:** `top_k` (*A verificar*) cuenta **unidades distintas**. El
  veredicto de una unidad llega a **todas** las personas que la dieron. Sus
  otras respuestas (hasta 3 por persona, por la contradicción) se verifican
  dentro del tope de siempre (`top_k × 3`); la que no entra queda
  `sin_verificar` con `fallo = tope_de_unidades` y la consulta lo avisa.
- **Ingesta y reproceso:** un solo camino de escritura
  (`semantica.escribir_respuestas`). **Un embedding por texto nuevo**, no por
  respuesta: una carga con 400 «→ Xiaomi» embebe uno, y si el texto ya
  existía, ninguno. `embebidas` en el resumen cuenta textos; `reutilizadas`,
  respuestas que apuntaron a una unidad existente. El reproceso suma
  `textos_embebidos`.
- **Consulta completa:** recorre unidades. Sobre el corpus actual, un
  criterio sin filtros son ~861 unidades, **~35 llamadas** en lotes de 25.
- **Diagnóstico:** *Recuperación (ANN)* — `pedidos` y `crudos` cuentan
  unidades; `respuestas` nuevo. *Unidades* — `personas_por_unidad_max`,
  `personas_por_unidad_mediana`, `unidades_de_una_persona`. *Verificación* —
  `unidades_principales`, `individuos` (personas alcanzadas),
  `sin_verificar_por_tope`. El detalle de una persona: «el mismo texto en N
  respuestas de M personas».
- **`scripts/diagnosticar_consulta.py`:** subcomando nuevo **`unidades`**
  (antes y después de migrar: textos con más de un vector, unidades,
  respuestas sin unidad, huérfanas, personas por unidad). `recall-indice` y
  `a1-distancia` miden unidades.
- **Estadísticas (14.2):** el índice vectorial que se informa son los HNSW de
  `unidad_evidencia` y, mientras exista, de `respuesta`; suma el dato
  **Respuestas distintas** (`unidades_de_evidencia`).
- **Frontend:** una línea en el detalle de la persona (`consultas.js`) y una
  tarjeta en Estadísticas (`estadisticas.js`).

### Qué NO cambia

Ninguna migración de la **bóveda**, ningún `grant`, ninguna vista de la
superficie externa, ningún secreto, ninguna variable obligatoria, ninguna
cola. El contrato con COLOQUIO (`version_contrato: 2`) es el mismo; solo
suman campos (`personas_en_la_unidad` en cada evidencia evaluada), y la
ausencia de un campo nuevo se lee como «no se sabe». La regla de agregación
(`hallazgo_de`) es la misma. La ficha (R7.6), la reidentificación y la
exportación trabajan por respuesta y persona y **ninguna leía el vector**
(revisado: `ficha.py`, `auditoria.py`, `consultas.a_csv*`). El criterio se
sigue embebiendo como `query`. **No se re-embebe nada: la migración cuesta
US$ 0.**

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Cloud Tasks | Código |
|---|---|---|---|---|---|---|
| R-UE | — | `0010` (ahora) · `posteriores/0011` (después, Paso 12) | — | — (el comentario de `RECALL_EXACTO_HASTA` ahora dice «unidades») | — | backend, frontend (una línea), script, pruebas |

### Cambio de comportamiento visible

- **Consultas exploratorias:** con el mismo *A verificar* el ranking puede
  traer **muchas más personas**: si «→ Xiaomi» cumple, entran todas las que
  la eligieron. El costo de verificación no sube (son las mismas 25
  lecturas, o menos).
- **Personas «pendientes» por el tope:** pueden aparecer personas con estado
  *pendiente* y la razón «el tope de unidades del criterio se agotó». Entraron
  por una respuesta verificada; otra suya no se llegó a leer. Es honesto, no
  un error (§13).
- **Consulta completa:** estimaciones mucho más bajas que antes.
- **Ingesta:** los resúmenes de carga muestran menos `embebidas` (textos, no
  respuestas).

### Costos (§6 del spec)

| | Antes | Con unidades |
|---|---:|---:|
| Vectores a embeber e indexar | 21.340 | **861** |
| Índice vectorial | ~12 MB | ~0,5 MB (más el de `respuesta` hasta el Paso 12) |
| Embeddings de una carga nueva | uno por respuesta | **uno por texto nuevo** |
| Verificación con `top_k = 25` | hasta 25 copias de una idea | **25 ideas distintas** |
| Completa de un criterio sobre todo el corpus | «cientos» de llamadas | **~35 llamadas** |
| Migración | — | **US$ 0** |

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
git fetch origin claude/magical-franklin-e5yx9o
git checkout claude/magical-franklin-e5yx9o
git log --oneline -1
```

La clave de Voyage, para lo que embebe un criterio (no se imprime ni se
guarda; se borra al final, Paso 14):

```bash
export EMBEDDINGS_API_KEY="$(gcloud secrets versions access latest --secret=EMBEDDINGS_API_KEY --project="$PROYECTO")"
```

> **Ojo con la versión del script.** En los Pasos 1 y 2 el script que se
> corre es el de **esta rama**: su `unidades` funciona con la `0010` sin
> aplicar (informa «SIN APLICAR» y mide lo de antes). Pero `recall-indice` y
> `a1-distancia` de esta rama **necesitan** la `0010` (leen
> `unidad_evidencia`). Para medir el «antes» con ellos, usar el script de
> `main`, como indica el Paso 2.3.

---

## Paso 1 · Punto de partida

### 1.1 · D72 estable en producción (§7 [alcance])

Este cambio va **encima de D72** (recall exacto bajo el umbral, PR #61,
`85ef760`). El spec pide desplegarlo **después** de que ese recall esté
estable. Confirmar:

```bash
psql "$DSN_BOVEDA" <<'SQL'
select c.creado_en, e->>'plan' as plan, e->>'motivo_plan' as motivo, e->>'ms' as ms
  from consulta_ejecucion c, jsonb_array_elements(c.diagnostico->'etapas') e
 where e->>'etapa' = 'recall'
 order by c.creado_en desc limit 10;
SQL
```

Esperado: `plan=exacto`, `motivo_plan` en `corpus_bajo_umbral` o
`filtro_por_personas`, y `ms` estable. Si `motivo_plan` sale vacío, D72 no
está desplegado: hacerlo primero (`docs/DESPLIEGUE - bug recall HNSW completo
no correcto.md`).

Y que no haya líneas `[recall]` recientes en el log:

```bash
gcloud functions logs read api --gen2 --region="$REGION" --project="$PROYECTO" \
  --limit=1000 | grep "\[recall\]" | tail -5
```

### 1.2 · El PR anterior

La rama se reutiliza entre entregas (CLAUDE.md, «Antes de pushear»).
Confirmar que el PR de esta rama es el **nuevo**, abierto contra `main` con
D72 ya mergeado, y que no apila commits sobre historia mergeada:

```bash
git log --oneline origin/main..claude/magical-franklin-e5yx9o
```

Tiene que listar **solo** los commits de R-UE (el cambio y el ajuste del despliegue).

### 1.3 · Nada en curso del lado semántico

La migración toma un bloqueo exclusivo sobre `respuesta` durante segundos
(agrega una columna, la llena, la vuelve `not null` y valida una FK). No
correrla con una ingesta, un reproceso o una consulta completa a mitad de
camino:

```bash
psql "$DSN_BOVEDA" <<'SQL'
select 'ingesta' as que, id::text, estado from ingesta_trabajo
 where estado in ('encolada', 'procesando')
union all
select 'consulta completa', id::text, estado from consulta_ejecucion
 where estado in ('encolada', 'procesando');
SQL
```

Vacío = se puede seguir. Si hay algo, esperar a que termine o cancelarlo.
Avisar a los analistas que durante ~15 minutos no lancen cargas ni consultas
completas (las exploratorias pueden seguir: solo esperan el bloqueo).

---

## Paso 2 · El «antes», medido (20 min)

### 2.1 · El corpus

```bash
psql "$DSN_SEMANTICA" <<'SQL'
select count(*) as respuestas,
       count(distinct texto_embebido) as textos_distintos,
       count(distinct hash_texto) as huellas,
       count(*) filter (where hash_texto is null) as sin_huella
  from respuesta;
select indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) as tamano
  from pg_stat_user_indexes where relname = 'respuesta';
SQL
```

Anotar (Checklist): **respuestas**, **textos distintos** (861 el 2026-10-11),
y el tamaño de `respuesta_embedding_idx` (~12 MB). `huellas` tiene que ser
igual a `textos_distintos` y `sin_huella` 0 (lo dejó así la `0009`).

### 2.2 · ¿Mismo texto, mismo vector? (R-UE.5, §7 [datos])

Es la comprobación que el spec pide **antes** de migrar. Con el script de
esta rama:

```bash
python3 scripts/diagnosticar_consulta.py unidades
```

Salida esperada:

```
R-UE · semantica/0010 SIN APLICAR
  21340 respuestas · 861 textos distintos · 96.0% son copias
  textos con más de un vector: 0
  → sin inconsistencias
```

**Si `textos con más de un vector` es mayor que 0**, la migración se va a
detener (Paso 5) y eso es lo correcto: hay que entender por qué antes de
elegir un vector. Para cada huella listada:

```bash
psql "$DSN_SEMANTICA" <<'SQL'
select r.hash_texto, c.nombre as estudio, c.creado_en, count(*) as respuestas,
       left(r.embedding::text, 40) as vector
  from respuesta r
  join pregunta p on p.id = r.pregunta_id
  join cuestionario c on c.id = p.cuestionario_id
 where r.hash_texto in (
   select hash_texto from respuesta group by hash_texto
   having count(distinct embedding::text) > 1)
 group by 1, 2, 3, 5
 order by 1, 3;
SQL
```

Causas posibles, de la más a la menos probable:

| Patrón | Causa | Qué hacer |
|---|---|---|
| Dos grupos de vectores, separados por la fecha de un estudio | Una carga con otro `input_type` u otro modelo antes de un cambio de configuración | Re-ingestar el estudio viejo (un reproceso «forzado» lo re-embebe) **antes** de migrar, o aceptar el vector de la primera respuesta (Paso 5.2) |
| Diferencias en los últimos decimales | Precisión de la API entre llamadas | Aceptar el de la primera (Paso 5.2): el coseno es el mismo a efectos prácticos |
| Vectores muy distintos para el mismo texto | Un bug | **No migrar.** Guardar la salida y abrir un bug |

Anotar la decisión en el Checklist.

### 2.3 · Recall del índice, antes (con el script de `main`)

Es el número que el spec usó como evidencia (§1). Con el script de `main`
—que mide sobre `respuesta`—, sin cambiar de rama:

```bash
git show origin/main:scripts/diagnosticar_consulta.py > /tmp/diagnosticar_main.py
cp /tmp/diagnosticar_main.py scripts/_diagnosticar_main.py
python3 scripts/_diagnosticar_main.py recall-indice --ef-search 200 \
  --criterio "gente que usa un celular xiaomi" \
  --criterio "gente a la que le gusta el fernet" \
  --criterio "personas que viajan al exterior por trabajo"
rm scripts/_diagnosticar_main.py /tmp/diagnosticar_main.py
```

(La copia va dentro de `scripts/` porque el script importa `functions/` por
ruta relativa. Borrarla después: no se commitea.)

Anotar el recall de cada criterio y `input_type` (el spec: 0,0 / 0,0 / 0,0 /
59,5 / 87,5 / 71,5 %). Sale con 1 si falta alguna: es lo esperado.

### 2.4 · Línea de base de la aplicación (no regresión, §5)

En la aplicación, **con el código todavía de D72**, correr y **exportar a
CSV** (botón de la barra de acciones) estas consultas, todas exploratorias,
resto de parámetros por defecto, modo **laxo**:

1. «gente que usa un celular xiaomi», sin filtro.
2. «gente a la que le gusta el fernet», sin filtro.
3. «personas que viajan al exterior por trabajo», sin filtro.
4. La 1 con un filtro demográfico cualquiera (por ejemplo, sexo).
5. Una o dos consultas guardadas reales de los analistas.

Guardar los CSV como `antes_1.csv` … `antes_5.csv` y anotar de cada una: total
de personas, cuántas confirmadas, y del diagnóstico la etapa *Verificación*
(`evidencias_verificadas`, `llamadas`) y el costo.

O desde la base, lo registrado:

```bash
psql "$DSN_BOVEDA" <<'SQL'
select c.id, c.creado_en, c.definicion->'criterios'->0->>'texto' as criterio,
       c.diagnostico->>'total' as total, c.costo_real_usd,
       (select e->>'evidencias_verificadas' from jsonb_array_elements(c.diagnostico->'etapas') e
         where e->>'etapa' = 'verificacion' limit 1) as verificadas
  from consulta_ejecucion c
 where c.alcance = 'exploratorio'
 order by c.creado_en desc limit 10;
SQL
```

---

## Paso 3 · Pruebas antes de subir (10 min)

Con el Postgres de pruebas **con pgvector**:

```bash
# Debian/Ubuntu, una sola vez:  sudo apt-get install -y postgresql-16-pgvector
source scripts/pg_pruebas.sh
cd functions
python3 -m pytest -q tests/test_unidades_de_evidencia.py      # 17
python3 -m pytest -q                                            # todas
cd ..
```

Todo verde. Qué cubre `test_unidades_de_evidencia.py`:

| Prueba | §5 del spec / diseño |
|---|---|
| `test_la_migracion_deja_una_unidad_por_texto_y_ninguna_respuesta_sin_unidad` | «Una unidad por `hash_texto`» y «ninguna respuesta sin unidad», con la `0010` de verdad sobre una base nueva (y dos veces, sin efecto) |
| `test_la_migracion_no_esconde_un_texto_con_dos_vectores` | R-UE.5: se detiene; con la decisión explícita, toma el de menor id |
| `test_el_diagnostico_de_unidades_ve_la_inconsistencia` | El subcomando `unidades` |
| `test_mil_copias_del_mismo_texto_se_embeben_una_vez` · `test_una_respuesta_nueva_con_un_texto_existente_no_dispara_un_embedding` | «Una respuesta nueva con un texto ya existente no dispara un embedding» |
| `test_una_respuesta_sin_unidad_no_se_escribe` · `test_la_base_no_deja_una_respuesta_apuntando_a_otro_texto` | Integridad: `SinUnidad` y la FK compuesta |
| `test_el_codigo_anterior_sigue_pudiendo_insertar_durante_la_transicion` | La vuelta atrás del código (Paso 13) |
| `test_xiaomi_recupera_su_unidad_y_la_resuelve_a_todas_sus_personas` | «Xiaomi recupera la unidad y la resuelve a todas las personas» |
| `test_el_indice_sobre_unidades_trae_al_bloque_que_antes_no_alcanzaba` | El recall del índice sobre unidades (la medición real es el Paso 10) |
| `test_un_veredicto_sobre_una_unidad_llega_a_todas_sus_personas` | «Un veredicto llega a todas sus personas» (una lectura, 40 personas) |
| `test_top_k_25_verifica_25_unidades_distintas` | «`top_k = 25` verifica 25 unidades distintas» |
| `test_quien_no_tiene_uso_semantico_no_aparece_aunque_su_unidad_si` | «Sin `uso_semantico` no aparece, aunque su unidad esté en el recall» |
| `test_no_regresion_el_pool_contiene_a_las_personas_del_recall_por_respuestas` | No regresión: el pool por unidades contiene a las personas del recall por respuestas |
| `test_la_baja_se_lleva_la_unidad_que_solo_era_suya` · `test_el_retiro_por_estudio_tambien_se_lleva_la_unidad` | La cascada de baja: el vector se va con la última respuesta |
| `test_sin_respuesta_embedding_la_ingesta_y_el_recall_siguen_andando` | La `0011` no rompe el código nuevo (corrida en una transacción que se deshace) |

Pruebas existentes que **cambiaron a propósito** (el comportamiento nuevo es
el pedido):

| Prueba | Antes | Ahora |
|---|---|---|
| `test_r_cs_consultas.py::test_las_respuestas_repetidas_se_verifican_una_vez…` | hacían falta ≥ 2 rondas | **1**: el contrato ocupa un lugar de 3 |
| `test_ingesta.py::test_una_fila_sin_hash_…` | una fila sin huella se re-embebía | la base completa la huella y no se embebe nada |
| `test_fase8_calidad_del_dato.py` (3 de reproceso) | un embedding por respuesta | uno por texto distinto |
| `test_dimension_embeddings.py::test_la_columna_quedo_en_512` | dos columnas vectoriales | tres (`unidad_evidencia.embedding`) |
| `test_bug_recall_hnsw.py`, `test_bug_diagnostico_a1.py` | 1.500 respuestas con el mismo texto y vectores distintos | cada una con su texto: miden el recall sobre muchos vectores, no la deduplicación; el plan se compara sin las cuentas nuevas |

> **Las pruebas truncan `unidad_evidencia`** (`conftest.py`). Un `truncate`
> no dispara el trigger que borra unidades huérfanas: sin eso, cada prueba
> vería los vectores de las anteriores.

---

## Paso 4 · Respaldo (obligatorio, §7 [alcance])

```bash
gcloud sql backups create --instance=paneles-semantica --project="$PROYECTO" \
  --description="antes de R-UE semantica/0010"
gcloud sql backups list --instance=paneles-semantica --project="$PROYECTO" --limit=1
```

Anotar el **ID** del respaldo (Checklist): es la vuelta atrás de último
recurso (Paso 13.3). La bóveda no se toca.

---

## Paso 5 · Migración `semantica/0010` (5 min)

Va en **una transacción**: si algo falla, no queda nada a medias. Se puede
correr dos veces sin efecto.

### 5.1 · Sin inconsistencias (lo esperado)

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0010_unidad_evidencia_vector.sql
```

Con ~21 mil respuestas son segundos; sobre `db-f1-micro`, el índice HNSW de
861 vectores es instantáneo. No interrumpir.

### 5.2 · Con inconsistencias decididas en el Paso 2.2

Si la migración se detuvo con «Hay N texto(s) con más de un vector distinto»
y en el Paso 2.2 se decidió **tomar el vector de la respuesta de menor id**:

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 \
  -c "set paneles.unidades_inconsistentes = 'tomar_la_primera'" \
  -f db/semantica/0010_unidad_evidencia_vector.sql
```

Deja un `NOTICE` con cuántos textos tomó así. Anotarlo.

> `-c` y `-f` en el mismo `psql` comparten sesión, que es lo que hace falta
> para que el `set` llegue a la migración.

### 5.3 · Si falla por el guardia de PII

El event trigger de la `0005` revisa los nombres de columna nuevos: ninguno
de la `0010` es PII (`hash_texto`, `texto_embebido`, `embedding`,
`unidad_id`). Si igual falla ahí, alguien cambió el catálogo `campo_pii`:
revisar antes de tocar nada.

---

## Paso 6 · Verificar la migración (5 min)

### 6.1 · Unidades

```bash
python3 scripts/diagnosticar_consulta.py unidades
```

Esperado:

```
R-UE · semantica/0010 APLICADA
  21340 respuestas · 861 textos distintos · 96.0% son copias
  textos con más de un vector: 0
  861 unidades · 0 respuestas sin unidad · 0 con otro texto · 0 huérfanas
  unidades con más personas detrás:
    unidad #…: … personas, … respuestas, … pregunta(s)
  → sin inconsistencias
```

**`unidades` = `textos distintos`** del Paso 2.1 (861 el día de la
medición) es la prueba de aceptación «una unidad por `hash_texto`» sobre el
corpus real. Si se usó 5.2, `textos con más de un vector` sigue mostrando los
de antes (es `respuesta.embedding`, que no se toca): es esperado, y desaparece
con la `0011`.

### 6.2 · Objetos y tamaños

```bash
psql "$DSN_SEMANTICA" <<'SQL'
select count(*) as unidades from unidad_evidencia;
select count(*) filter (where unidad_id is null) as sin_unidad from respuesta;
select relname, indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) as tamano
  from pg_stat_user_indexes where relname in ('respuesta', 'unidad_evidencia') order by 1, 2;
select tgname from pg_trigger where tgrelid = 'respuesta'::regclass and not tgisinternal order by 1;
analyze unidad_evidencia;
SQL
```

Tres triggers: `respuesta_asegurar_unidad`,
`respuesta_borrar_unidades_huerfanas_del`,
`respuesta_borrar_unidades_huerfanas_upd`. El `analyze` deja el
`reltuples` que usa el plan del recall.

### 6.3 · Esquema y COLOQUIO

```bash
python3 scripts/verificar_esquema.py           # las dos bases al día; la 0010 completa
python3 scripts/verificar_coloquio.py          # sin fallidos (no cambió la superficie)
```

`verificar_esquema.py` declara para la `0010`: `unidad_evidencia`,
`respuesta.unidad_id`, `borrar_unidades_huerfanas()` y
`respuesta_asegurar_unidad()`.

> **Hasta acá, el código en producción sigue siendo el de D72 y funciona**:
> el trigger de transición resuelve la unidad de lo que inserte y mantiene
> `respuesta.embedding`. Si hubiera que parar, se puede parar acá.

---

## Paso 7 · Configuración (`functions/.env`)

**Nada que hacer.** `RECALL_EXACTO_HASTA` (D72) sigue valiendo, y ahora cuenta
**unidades**: con 861 y un umbral de 200 mil, el recall sin filtro es exacto
por mucho tiempo. Si estaba fijado explícitamente en `functions/.env`, el
valor sigue siendo correcto (lo que cambia es que tarda mucho más en
cruzarse).

---

## Paso 8 · Desplegar (10 min)

Funciones **y hosting** (cambiaron `web/public/js/paginas/consultas.js` y
`estadisticas.js`):

```bash
export VPC_CONNECTOR="$(gcloud functions describe api --gen2 --region="$REGION" \
  --project="$PROYECTO" --format='value(serviceConfig.vpcConnector)')"
printf 'Conector VPC: %s\n' "$VPC_CONNECTOR"     # no seguir si está vacío
firebase deploy --only functions,hosting --project="$PROYECTO"
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

Las tres `ACTIVE` y con el mismo conector. Las tres cambian de verdad:
`api` (recall y verificación), `procesaringesta` (ingesta y reproceso por
unidades) y `procesarconsulta` (la completa por huella).

---

## Paso 9 · Probar en la aplicación (30 min)

Recargar la aplicación con caché limpia (el `consultas.js` nuevo).

### 9.1 · Recall sobre unidades (R-UE.2)

Correr la consulta 1 del Paso 2.4 («celular xiaomi», sin filtro, laxo).
Diagnóstico:

- *Recuperación (ANN)*: `pedidos=200 · crudos=≤200 · respuestas=…` (cientos o
  miles: todas las respuestas de esas unidades) · `plan=exacto ·
  motivo_plan=corpus_bajo_umbral`.
- *Unidades*: `unidades=crudos`, `repetidas=0` (cada unidad ya es un texto
  distinto; lo repetido son las **personas** detrás),
  `personas_por_unidad_max`, `…_mediana`, `unidades_de_una_persona`.

La consulta 4 (con filtro demográfico): `plan=exacto ·
motivo_plan=filtro_por_personas`, y en el ranking **ninguna** persona fuera
del filtro.

### 9.2 · Verificación sobre unidades (R-UE.3)

En la misma consulta 1:

- *Verificación*: `unidades_principales ≤ 25`, `evidencias_verificadas ≤ 75`,
  `individuos` (personas alcanzadas) **mayor** que en la línea de base del
  Paso 2.4. `llamadas` del orden de antes o menos.
- El ranking: las personas que eligieron Xiaomi aparecen **todas**,
  confirmadas, con la misma razón. Abrir **Ver** de una: «el mismo texto en
  N respuestas de M personas: se verificó una vez».
- Si hay `sin_verificar_por_tope > 0`: aparece la degradación «N unidad(es)
  de evidencia de personas del resultado no se verificaron: el tope … se
  agotó», y esas personas figuran *pendientes*. Es el comportamiento
  diseñado (§13).

### 9.3 · Personas por unidad (§7 [producto])

En el detalle de una evidencia evaluada, `personas_en_la_unidad`. En el
diagnóstico, las tres cuentas de *Unidades*. Desde la base, lo registrado
(solo números, como siempre):

```bash
psql "$DSN_BOVEDA" <<'SQL'
select c.creado_en, e->>'unidades' as unidades, e->>'personas_por_unidad_max' as max,
       e->>'personas_por_unidad_mediana' as mediana, e->>'unidades_de_una_persona' as de_una
  from consulta_ejecucion c, jsonb_array_elements(c.diagnostico->'etapas') e
 where e->>'etapa' = 'unidades' order by c.creado_en desc limit 5;
SQL
```

### 9.4 · Ingesta y reproceso (R-UE.4)

Con un estudio **de prueba** (no uno real):

1. **Carga nueva** con respuestas que ya existen en el corpus (por ejemplo,
   una ola que repite preguntas cerradas). Al terminar, el resumen dice
   `embebidas` = cuántos **textos nuevos** había (0 si todos existían) y
   `reutilizadas` = el resto de las respuestas.
2. **Reproceso** de una pregunta de ese estudio (cambiar una etiqueta). El
   resumen del trabajo trae `reembebidas` (respuestas reescritas) y
   `textos_embebidos` (textos nuevos mandados a Voyage).
3. Después:

   ```bash
   python3 scripts/diagnosticar_consulta.py unidades     # 0 sin unidad, 0 huérfanas
   ```

### 9.5 · Consulta completa

**Estimar** (no lanzar) la consulta 1 con *Alcance: Completo*. La
estimación cuenta unidades: del orden de 861 sin filtros, **~35 lotes**. Si
hay presupuesto y permiso, lanzarla con un presupuesto chico y comprobar que
termina y que el resultado incluye a todas las personas de Xiaomi.

> Una completa lanzada **antes** del deploy se sigue leyendo: los planes
> congelados conservan su forma y se leen por la huella. Si el mismo texto
> estaba en dos preguntas del plan viejo, se lo juzga una vez.

### 9.6 · La baja (cascada)

Con una persona **de prueba** que tenga una respuesta abierta única (un
texto que solo ella dio):

```bash
psql "$DSN_SEMANTICA" -tAc "select count(*) from unidad_evidencia"     # N
```

Darla de baja desde la aplicación (o con `bajas.retirar` desde el job). Después:

```bash
psql "$DSN_SEMANTICA" -tAc "select count(*) from unidad_evidencia"     # N - (sus textos únicos)
python3 scripts/diagnosticar_consulta.py unidades                       # 0 huérfanas
```

Su texto único ya no está en el store; los textos que compartía con otras
personas, sí.

### 9.7 · Estadísticas

Pantalla **Estadísticas → Tamaño y salud del corpus**: *Respuestas
distintas* = las unidades del Paso 6.1; *Índice vectorial* = la suma de los
dos HNSW del Paso 6.2 (baja a ~0,5 MB después del Paso 12).

### 9.8 · Log y COLOQUIO

```bash
gcloud functions logs read api --gen2 --region="$REGION" --project="$PROYECTO" \
  --limit=500 | grep -E "\[recall\]|SinUnidad|unidad_evidencia" | tail -10
gcloud functions logs read procesaringesta --gen2 --region="$REGION" --project="$PROYECTO" \
  --limit=500 | grep -E "SinUnidad|ForeignKey|unidad" | tail -10
python3 scripts/verificar_coloquio.py
```

Ninguna línea de `SinUnidad` ni de FK. `verificar_coloquio.py` sin fallidos.
Si COLOQUIO hace una consulta, debe seguir leyendo `detalle` y `estado` como
antes.

---

## Paso 10 · El «después» medido: recall del índice (15 min)

El spec (§5) pide que el recall del índice sobre los criterios de prueba
«suba muy por encima del 0 %». Con el script de **esta rama** (mide
unidades):

```bash
python3 scripts/diagnosticar_consulta.py recall-indice --ef-search 200 \
  --criterio "gente que usa un celular xiaomi" \
  --criterio "gente a la que le gusta el fernet" \
  --criterio "personas que viajan al exterior por trabajo"
python3 scripts/diagnosticar_consulta.py recall-indice \
  --criterio "gente que usa un celular xiaomi" \
  --criterio "gente a la que le gusta el fernet" \
  --criterio "personas que viajan al exterior por trabajo"
```

La primera línea dice `corpus ~861 unidades de evidencia` y el índice
`unidad_evidencia_embedding_idx`. Anotar el recall de cada criterio y tipo
junto al del Paso 2.3. Con 861 vectores distintos y `top_n` 200, lo
esperable es cercano a 100 %.

| Resultado | Lectura |
|---|---|
| ≥ 95 % en todos | El índice vuelve a ser una opción para cuando el corpus crezca. Anotarlo en D73 |
| Entre 50 y 95 % en alguno | Mejor que antes; anotar. Bajo el umbral no afecta (el recall es exacto) |
| Cerca de 0 % en alguno | No esperado. Guardar la salida y revisar con `unidades` si hay unidades casi idénticas (textos que difieren en un espacio o un signo) |

Y la medición de A1 que motivó todo:

```bash
python3 scripts/diagnosticar_consulta.py a1-distancia \
  --criterio "gente que usa un celular xiaomi" --patron xiaomi --contra titular
```

Ahora dice «N unidad(es) de evidencia del corpus están más cerca» y «recall
real … X unidad(es), Y respuesta(s); entra la mejor con «xiaomi»».

---

## Paso 11 · No regresión (§5)

Volver a correr las consultas del Paso 2.4, igual que entonces, exportar a
CSV como `despues_1.csv` … y comparar las personas:

```bash
for n in 1 2 3 4 5; do
  [ -f "antes_$n.csv" ] || continue
  cut -d, -f1 "antes_$n.csv"   | tail -n +2 | sort -u > /tmp/a
  cut -d, -f1 "despues_$n.csv" | tail -n +2 | sort -u > /tmp/d
  printf 'consulta %s: antes %s · después %s · se fueron %s · llegaron %s\n' "$n" \
    "$(wc -l < /tmp/a)" "$(wc -l < /tmp/d)" \
    "$(comm -23 /tmp/a /tmp/d | wc -l)" "$(comm -13 /tmp/a /tmp/d | wc -l)"
done
rm -f /tmp/a /tmp/d
```

(La primera columna del CSV es el `id_persona`.)

**Lo esperado: «se fueron» en 0 o casi**, y «llegaron» > 0: el veredicto de
cada unidad llega ahora a todas sus personas. Si alguien **se fue**, revisar
su caso antes de dar por cerrado el despliegue:

| Por qué se fue | Es… |
|---|---|
| Ahora figura en *Quiénes quedaron fuera* con `no_cumple` o «sin evidencia pertinente» | **Mejor**: antes entraba con una evidencia sin verificar o sin que se leyera su contradicción |
| Entraba por estar entre las 25 personas verificadas, y su respuesta ahora cae fuera de las 25 **unidades** principales | Efecto de contar unidades: subir *A verificar* la trae de vuelta. Anotarlo |
| Ninguna de las dos | **Regresión**: guardar los dos CSV y el diagnóstico, y abrir un bug |

`personas_verificadas` en el recorte («se revisaron N») va a ser mayor; el
costo (`costo_real_usd`) del mismo orden o menor.

Borrar los CSV al terminar: tienen `id_persona` y no deben quedar en una
máquina de trabajo.

---

## Paso 12 · Quitar `respuesta.embedding` (POSTERIOR, R-UE.5)

**No hacerlo el día del despliegue.** El spec pide conservar la columna
«hasta verificar que todo funciona sobre unidades». Hacerlo cuando, en
producción y con este código:

- pasaron al menos **una semana** de uso normal;
- hubo al menos **una ingesta** y **un reproceso** reales sin errores;
- el Paso 11 no dejó regresiones abiertas;
- `diagnosticar_consulta.py unidades` sigue «sin inconsistencias» (salvo las
  heredadas que se decidieron en 5.2).

**Después de este paso, volver atrás el código ya no es posible** sin
restaurar el respaldo (el código anterior lee `respuesta.embedding`).

```bash
gcloud sql backups create --instance=paneles-semantica --project="$PROYECTO" \
  --description="antes de R-UE semantica/0011"
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 \
  -f db/semantica/posteriores/0011_quitar_embedding_de_respuesta.sql
psql "$DSN_SEMANTICA" <<'SQL'
select columna, dimension from v_dimension_embeddings order by 1;   -- sin respuesta.embedding
select indexrelname, pg_size_pretty(pg_relation_size(indexrelid))
  from pg_stat_user_indexes where relname = 'respuesta';            -- sin respuesta_embedding_idx
vacuum (analyze) respuesta;
SQL
python3 scripts/verificar_esquema.py
python3 scripts/diagnosticar_consulta.py unidades
```

Se va el índice HNSW de `respuesta` (~12 MB) y el espacio de 21 mil vectores
(lo devuelve el `vacuum` a la tabla; al disco, recién con un `vacuum full`,
que bloquea: no hace falta). Correr después una consulta exploratoria y una
carga chica de prueba.

La `0011` **no está** en `esquema.MIGRACIONES_SEMANTICA` ni en
`db/semantica/`: vive en `posteriores/` para que ni `pg_pruebas.sh` ni nadie
la aplique en un bucle. Anotar en el Checklist la fecha en que se aplicó.

---

## Paso 13 · Volver atrás

### 13.1 · Solo el código (antes del Paso 12)

La `0010` queda; el código anterior funciona con ella:

- inserta respuestas con su vector y sin `unidad_id`: el trigger
  `respuesta_asegurar_unidad()` resuelve o crea la unidad;
- lee `respuesta.embedding`, que el trigger mantiene;
- la completa hace `on conflict (ejecucion_id, criterio_orden, pregunta_id,
  hash_texto)`: la `0010` dejó el índice único `veredicto_unidad_clave_r_cs`
  para eso.

```bash
git checkout 8dd3a22        # main con D72 (y el spec), antes de R-UE
firebase deploy --only functions,hosting --project="$PROYECTO"
```

Lo que se pierde volviendo: el recall y la verificación por unidades (vuelve
el comportamiento de D72) y el ahorro de embeddings. Las unidades que el
código anterior cree mientras tanto quedan bien formadas.

### 13.2 · Además, la migración (antes del Paso 12)

Solo si hiciera falta quitar la `0010` sin restaurar el respaldo (no debería:
13.1 alcanza):

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 <<'SQL'
begin;
drop trigger if exists respuesta_asegurar_unidad on respuesta;
drop trigger if exists respuesta_borrar_unidades_huerfanas_del on respuesta;
drop trigger if exists respuesta_borrar_unidades_huerfanas_upd on respuesta;
drop function if exists respuesta_asegurar_unidad();
drop function if exists borrar_unidades_huerfanas();
drop view v_unidad_evidencia;
drop view v_respuesta_estudio;
alter table respuesta drop constraint respuesta_unidad_fk;
alter table respuesta drop column unidad_id;
drop table unidad_evidencia;
-- Las dos vistas, como las dejaron la 0006 y la 0009.
create view v_respuesta_estudio as
select r.id as respuesta_id, i.id_persona, c.ref_estudio, c.nombre as estudio, c.fecha_campo,
       p.codigo as pregunta_codigo, p.texto as pregunta_texto, p.tipo as pregunta_tipo,
       r.valor_texto, r.texto_embebido, r.embedding
  from respuesta r join individuo i on i.id = r.individuo_id
  join pregunta p on p.id = r.pregunta_id join cuestionario c on c.id = p.cuestionario_id;
create view v_unidad_evidencia as
select r.pregunta_id, r.hash_texto, count(*) as respuestas,
       count(distinct r.individuo_id) as individuos, min(r.id) as respuesta_representante
  from respuesta r group by r.pregunta_id, r.hash_texto;
create index if not exists respuesta_unidad_idx on respuesta (pregunta_id, hash_texto);
commit;
SQL
python3 scripts/verificar_esquema.py      # la 0010 figura sin aplicar
```

`respuesta.hash_texto` queda `not null` y `veredicto_unidad` con sus índices
nuevos: los dos son compatibles con el código anterior.

### 13.3 · Último recurso

Restaurar el respaldo del Paso 4 (o el del Paso 12, si ya se aplicó la
`0011`). Se pierde lo cargado después del respaldo.

```bash
gcloud sql backups restore <ID_DEL_RESPALDO> --restore-instance=paneles-semantica \
  --project="$PROYECTO"
```

---

## Paso 14 · Limpiar y avisar

```bash
unset EMBEDDINGS_API_KEY
rm -f antes_*.csv despues_*.csv
```

Texto sugerido para los analistas:

> Cambiamos cómo la búsqueda semántica trata las respuestas repetidas. En
> nuestras encuestas, la gran mayoría de las respuestas son copias (todas las
> personas que eligen «Xiaomi» dan exactamente la misma respuesta), y eso
> confundía a la búsqueda y hacía que se verificara muchas veces lo mismo.
> Desde hoy, cada respuesta distinta se busca y se verifica **una vez**, y el
> resultado llega a **todas** las personas que la dieron. Con el mismo
> «A verificar» (25), el ranking puede traer muchas más personas que antes, al
> mismo costo o menor. Si alguna persona aparece «pendiente» con el motivo
> «el tope de unidades se agotó», es porque tenía otra respuesta parecida que
> no se llegó a leer: subir «A verificar» lo resuelve. La consulta completa
> pasa a ser mucho más barata. Detalle en el manual, 6.4, 6.9 y 6.11.

---

## Errores y correcciones

| Síntoma | Causa | Qué hacer |
|---|---|---|
| La `0010` se detiene: «Hay N texto(s) con más de un vector distinto» | R-UE.5: mismo texto, vectores distintos | Paso 2.2; después 5.2 o re-ingestar |
| La `0010` falla en `alter column hash_texto set not null` | Filas sin huella insertadas a mano después de la `0009` | No debería: la `0010` las completa primero. Si pasa, revisar si hay un `texto_embebido` nulo |
| `esquema_desactualizado` nombrando `unidad_evidencia` | Código desplegado sin la `0010` | Paso 5 |
| `SinUnidad: N texto(s) sin unidad de evidencia` en el log | Un camino escribió respuestas sin pasar por `escribir_respuestas` | Bug: guardar el log y abrirlo. No se pierde nada (la escritura aborta) |
| `ForeignKeyViolation … respuesta_unidad_fk` | Una respuesta apuntando a la unidad de otro texto | Bug, mismo tratamiento. La base lo impidió |
| `ForeignKeyViolation` aislado en `procesaringesta`, y la tarea se reintenta sola | Una baja borró la última respuesta de una unidad justo mientras un lote la reutilizaba | Esperado y raro; Cloud Tasks reintenta y el reintento crea la unidad de nuevo |
| El ranking trae muchas más personas que antes | El cambio (§1) | Esperado; Paso 11 |
| Personas *pendientes* con «el tope de unidades del criterio se agotó» | Sus otras respuestas no entraron en `top_k × 3` | Esperado; subir *A verificar* o usar la completa |
| `diagnosticar_consulta.py recall-indice` o `a1-distancia` fallan con `relation "unidad_evidencia" does not exist` | Script de esta rama contra una base sin la `0010` | Paso 2.3 (script de `main`) o aplicar la `0010` |
| `unidades` informa huérfanas > 0 | Alguien truncó o borró por fuera del trigger | `delete from unidad_evidencia u where not exists (select 1 from respuesta r where r.unidad_id = u.id);` |
| `unidades` informa «N unidades para M textos distintos» con N ≠ M | Huérfanas, o textos que difieren solo en espacios/mayúsculas (no: la huella es exacta) | Mirar las huérfanas primero |
| Después del Paso 12, el código anterior no arranca | Esperado | Paso 13.3 |

---

## Checklist

- [ ] Paso 1.1: D72 estable (`motivo_plan` presente, sin `[recall]`).
- [ ] Paso 1.2: la rama solo trae los commits de R-UE sobre `main`.
- [ ] Paso 1.3: sin ingestas, reprocesos ni completas en curso; analistas avisados.
- [ ] §2.1: respuestas ____ · textos distintos ____ · índice de `respuesta` ____ MB.
- [ ] §2.2: textos con más de un vector ____ · decisión: ______________.
- [ ] §2.3: recall del índice antes (xiaomi q/d, fernet q/d, viajes q/d): ____.
- [ ] §2.4: CSV y números de las consultas de línea de base.
- [ ] Paso 3: `test_unidades_de_evidencia.py` (17) y la batería completa en verde.
- [ ] Paso 4: respaldo de `paneles-semantica`, ID ______________.
- [ ] Paso 5: `0010` aplicada (5.1 o 5.2: ____).
- [ ] §6.1: unidades = textos distintos; 0 sin unidad; 0 huérfanas.
- [ ] §6.2: tres triggers; `analyze unidad_evidencia`.
- [ ] §6.3: `verificar_esquema.py` al día; `verificar_coloquio.py` sin fallidos.
- [ ] Paso 8: funciones y hosting; las tres funciones `ACTIVE` con el mismo conector.
- [ ] §9.1: recall por unidades (`pedidos`, `crudos`, `respuestas`); con filtro, nadie fuera del filtro.
- [ ] §9.2: veredicto a todas las personas de la unidad; «de M personas» en el detalle.
- [ ] §9.3: personas por unidad en el diagnóstico.
- [ ] §9.4: carga con `embebidas` = textos nuevos; reproceso con `textos_embebidos`.
- [ ] §9.5: estimación de la completa ~35 lotes.
- [ ] §9.6: la baja de prueba se llevó su texto único; 0 huérfanas.
- [ ] §9.7: Estadísticas muestra «Respuestas distintas».
- [ ] §9.8: sin `SinUnidad` ni FK en el log.
- [ ] Paso 10: recall del índice después (xiaomi q/d, fernet q/d, viajes q/d): ____.
- [ ] Paso 11: «se fueron» revisado persona por persona; CSV borrados.
- [ ] Paso 12 (posterior, fecha ______): `0011` aplicada tras una semana, una ingesta y un reproceso.
- [ ] Paso 14: clave borrada; aviso a analistas.

## Referencias

- `specs/SPEC_unidades_de_evidencia.md` — el pedido
- `docs/decisiones.md` — **D73** (y D70, D72, que corrige)
- `db/semantica/0010_unidad_evidencia_vector.sql` · `db/semantica/posteriores/0011_quitar_embedding_de_respuesta.sql`
- `docs/DESPLIEGUE - bug recall HNSW completo no correcto.md` — el antecedente (D72)
- `docs/DESPLIEGUE - R-CS recuperación y verificación de consultas semánticas.md` — las unidades de R-CS y la completa
- `docs/COSTOS.md` — 4.4 y §5
- `CLAUDE.md` — «Un vector por texto distinto»
- Manual de usuario — 6.4, 6.9, 6.11, 14.2, preguntas frecuentes, glosario
