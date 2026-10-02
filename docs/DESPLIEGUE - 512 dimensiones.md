# Despliegue — Los embeddings bajan a 512 dimensiones

**Sistema:** Gestión de paneles · store semántico
**Cubre:** `docs/PLAN_bajar_a_512_dims.md`
**Migración:** `db/semantica/0006_embeddings_512.sql` · **una sola base**
**Duración estimada:** 30 a 40 minutos de pasos técnicos, más la validación de
calidad del paso 7, que lleva días y no se saltea
**Precondición:** el store semántico al día hasta la `0005`

---

## Lo primero que hay que entender de este despliegue

**Hay una ventana y se cierra.** El corpus tiene hoy una respuesta. Re-embeber
hoy cuesta una llamada a Voyage; después de cargar las 200.000, cuesta
re-procesar el corpus entero. Esta migración **borra los embeddings** —un
vector de 1024 no se convierte a 512 desde SQL— y eso es intrascendente ahora
y caro después.

**Y hay un paso que no es técnico.** Lo que se gana son ~US$ 20–35 por mes de
instancia, para siempre. Lo que se resigna son uno o dos puntos de calidad de
recuperación según los benchmarks de Voyage —que **no son sobre respuestas de
encuesta en español rioplatense**—. El paso 7 es el que convierte esa apuesta
en una decisión informada, y es el único que no se puede apurar.

| | 1024 dims | 512 dims |
|---|---:|---:|
| Vectores de 200.000 respuestas | ~820 MB | ~410 MB |
| Con el índice HNSW | ~1,5–2 GB | ~0,8–1 GB |
| Instancia necesaria | ~4 GB RAM (custom) | `db-g1-small` (1,7 GB) |
| Costo mensual | ~US$ 50–70 | ~US$ 34 |

### El camino, en una mirada

| # | Paso | Dónde |
|---|---|---|
| 1 | Túnel y DSN | Terminal |
| 2 | Confirmar que falta solo la `0006` y contar el corpus | Terminal |
| 3 | **Confirmar que Voyage devuelve 512** | Terminal |
| 4 | Aplicar `semantica/0006` | Semántica |
| 5 | Verificar columnas, índice y vista | Semántica |
| 6 | Desplegar y re-ingestar lo que haya | Terminal + app |
| 7 | **Validar la calidad con un subconjunto** | Protocolo de calibración |
| 8 | Dimensionar la instancia antes de la carga masiva | gcloud |

**Los pasos 1, 2, 4, 5 y el rollback están ensayados** contra un Postgres
levantado al estado que deja la `0005`; las salidas de abajo son las de ese
ensayo. El 3 necesita la clave real, y del 6 al 8 son de despliegue, de
método y de infraestructura.

---

## Paso 1 · Túnel y DSN

Esta migración toca **solo la semántica**, pero el diagnóstico mira las dos.

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &

export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"

psql "$DSN_SEMANTICA" -tAc "select current_database()"   # → paneles_semantica
```

---

## Paso 2 · Confirmar que falta solo la `0006`, y contar el corpus

```bash
python3 scripts/verificar_esquema.py
```

Con la `0005` aplicada y ésta no:

```
    ✗ 0006_embeddings_512.sql
        falta v_dimension_embeddings  (en cuántas dimensiones está cada columna vectorial; es con lo que se comprueba que el proveedor y la base estén de acuerdo antes de pagar un embedding)

Faltan 1 migración(es). Para aplicarlas, con el Auth Proxy abierto:

  psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica \
       -v ON_ERROR_STOP=1 -f db/semantica/0006_embeddings_512.sql
```

> **Por qué la migración crea una vista que no parece hacer falta.** Esta
> migración solo **cambia el tipo** de dos columnas que ya existen, y una
> migración así es invisible para el diagnóstico: preguntarle a la base si
> `respuesta.embedding` está diría que sí con la `0006` sin aplicar. Es la
> misma lección que la `0015` en la bóveda ([D45](decisiones.md#d45)).
> `v_dimension_embeddings` es lo que la hace detectable, y de paso es lo que
> leen el diagnóstico y el paso 5.

Y lo que el plan pide confirmar antes de nada:

```bash
psql "$DSN_SEMANTICA" -tAc "select count(*) from respuesta;"
```

**La migración borra esas filas.** Con una, intrascendente. Si fueran muchas,
la migración se frena sola —ver el recuadro del paso 4— pero conviene saber el
número antes y no enterarse por el error.

```bash
# Y qué estudios habría que re-ingestar después, si hubiera más de uno.
psql "$DSN_SEMANTICA" -c "
select ref_estudio, estudio, count(*) as respuestas
  from v_respuesta_estudio group by 1, 2 order by 3 desc;"
```

---

## Paso 3 · Confirmar que Voyage devuelve 512 — **antes de tocar la base**

El bug que esta entrega arregla es que `EMBEDDINGS_DIMS` se leía y **no se le
mandaba a Voyage**: el cuerpo del pedido no llevaba el parámetro, así que la
API devolvía su default de 1024 pase lo que pase.

El nombre correcto es **`output_dimension`**, verificado contra la
documentación de Voyage: acepta 256, 512, 1024 (el default) y 2048 en los
modelos entrenados con Matryoshka, `voyage-3.5` entre ellos. Aun así, el
parámetro se comprueba con una llamada real, porque un nombre que la API no
reconoce **lo ignora en silencio**:

```bash
VOYAGE_API_KEY="$(gcloud secrets versions access latest --secret=EMBEDDINGS_API_KEY)" \
python3 - <<'EOF'
import os, requests
r = requests.post(
    "https://api.voyageai.com/v1/embeddings",
    headers={"Authorization": "Bearer " + os.environ["VOYAGE_API_KEY"]},
    json={"input": ["prueba"], "model": "voyage-3.5",
          "input_type": "document", "output_dimension": 512},
    timeout=60,
)
print(r.status_code, len(r.json()["data"][0]["embedding"]))
EOF
```

Tiene que imprimir `200 512`.

> **Si imprime `200 1024`**, el parámetro no se está aplicando y hay que
> averiguar por qué **antes de seguir**: migrar la columna a 512 con un
> proveedor que devuelve 1024 deja el sistema sin poder ingestar nada.
>
> Lo que sí cambió respecto del plan original: ahora el código **no depende
> de que alguien corra esta prueba**. `embeddings._controlar_largo()` rechaza
> cualquier vector que no venga en la dimensión pedida, así que el caso malo
> falla con un mensaje claro en vez de llegar al insert. Esta llamada sigue
> valiendo la pena porque detecta el problema antes del despliegue y no
> durante.

---

## Paso 4 · Aplicar `semantica/0006`

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 --single-transaction \
     -f db/semantica/0006_embeddings_512.sql
```

Salida esperada, completa, con un corpus de una respuesta:

```
psql:db/semantica/0006_embeddings_512.sql:90: NOTICE:  Se borran 1 respuestas para re-embeberlas en 512 dimensiones.
DO
DROP INDEX
DROP VIEW
DELETE 1
ALTER TABLE
ALTER TABLE
COMMENT
ALTER TABLE
ALTER TABLE
COMMENT
CREATE INDEX
CREATE VIEW
COMMENT
CREATE VIEW
COMMENT
```

`--single-transaction` no es decorativo: si algo falla a la mitad, la tabla
quedaría sin índice HNSW y sin la vista de procedencia, y ninguna de las dos
cosas se nota hasta que una consulta tarda un minuto o una pantalla se rompe.

> **Hay un `DROP VIEW` que el plan original no previó.** `v_respuesta_estudio`
> —la vista de procedencia de la `0002`— selecciona `r.embedding`, así que un
> `drop column` suelto falla con *«cannot drop column embedding of table
> respuesta because other objects depend on it»*. La migración la tira y la
> vuelve a crear **idéntica**.

### Las dos guardas, y cuándo saltan

**Si el corpus ya creció** (más de 1.000 respuestas), la migración aborta sin
tocar nada:

```
ERROR:  Hay 1500 respuestas embebidas y esta migración las borra para poder recrear la columna en 512 dimensiones.
Re-embeberlas cuesta una llamada al proveedor por respuesta.

Si de verdad querés seguir, volvé a correrla con:
  psql ... -c "set paneles.rehacer_embeddings = 'si'" -f db/semantica/0006_embeddings_512.sql
y anotá qué estudios hay que re-ingestar (select distinct ref_estudio from v_respuesta_estudio).
```

Esa salida de escape **está probada** y funciona tal como la escribe el
mensaje.

**Si ya está aplicada**, también aborta, y antes de borrar:

```
ERROR:  `respuesta.embedding` ya está en 512 dimensiones: esta migración ya se aplicó.
No se tocó nada. Si lo que querés es re-embeber el corpus, eso es una re-ingesta, no una migración.
```

Es deliberado: una migración que destruye datos en una segunda corrida
accidental es una trampa, y acá la segunda corrida es probable —
`scripts/pg_pruebas.sh` reaplica todas las migraciones cada vez que se lo
invoca.

---

## Paso 5 · Verificar

```bash
python3 scripts/verificar_esquema.py      # → Las dos bases están al día.

psql "$DSN_SEMANTICA" -c "select * from v_dimension_embeddings order by columna"
```

```
         columna          | dimension |    tipo
--------------------------+-----------+-------------
 pregunta.embedding_texto |       512 | vector(512)
 respuesta.embedding      |       512 | vector(512)
```

> **Una corrección al plan original.** Su consulta de verificación llamaba al
> resultado `dims_mas_4`, asumiendo el «+4» que llevan los tipos de largo
> variable como `varchar`. Para `vector`, `atttypmod` **es** la dimensión, sin
> sumar nada. Comprobado: una columna `vector(1024)` da `atttypmod = 1024`.

Y que el índice haya vuelto, que es la mitad que no se nota si falta:

```bash
psql "$DSN_SEMANTICA" -tAc \
  "select indexdef from pg_indexes where indexname = 'respuesta_embedding_idx'"
```

```
CREATE INDEX respuesta_embedding_idx ON public.respuesta USING hnsw (embedding vector_cosine_ops)
```

---

## Paso 6 · Desplegar y re-ingestar

El código ya tiene el default en 512, así que **no hace falta setear
`EMBEDDINGS_DIMS`**. Si está seteada en el entorno de la función con el valor
viejo, hay que cambiarla o sacarla: lo que vale es la variable, no el default.

```bash
# ¿Hay una EMBEDDINGS_DIMS vieja dando vueltas?
gcloud functions describe api --region=southamerica-east1 \
  --format='value(serviceConfig.environmentVariables)' | tr ',' '\n' | grep -i dims

export VPC_CONNECTOR=<el de siempre>
firebase deploy --only functions
```

Después, **re-ingestar el estudio que había**. La migración borró sus
respuestas, así que el salteo por `hash_texto` no se activa y la ingesta
embebe todo de nuevo —que es exactamente lo que se quiere—. Se hace desde la
pantalla de la encuesta, con el mismo archivo.

### La guarda que hace que esto no salga caro si algo quedó mal

Si `EMBEDDINGS_DIMS` quedara en 1024 con la columna en 512, antes **cada
ingesta fallaba al insertar**, es decir después de haber mandado el lote
entero a embeber y haberlo pagado. Ahora la ingesta lo comprueba antes de
mandar nada:

```
El proveedor de embeddings está configurado en 1024 dimensiones y
`respuesta.embedding` es vector(512). Con esa diferencia, embeber sale caro y
después el insert falla igual. Alineá `EMBEDDINGS_DIMS` con la columna, o
aplicá la migración que cambia la columna.
```

Lo mismo se ve sin ingestar nada, en `GET /api/diagnostico/esquema`, bajo
`embeddings.desajuste`.

---

## Paso 7 · Validar la calidad — **no saltear**

Acá es donde se decide si el cambio se queda. Todo lo anterior es mecánica;
esto es la pregunta de fondo, y **no hay forma de contestarla sin corpus
propio**: los benchmarks de Voyage no son sobre respuestas de encuesta en
español rioplatense.

1. Cargar un subconjunto de **5.000 a 10.000 respuestas**.
2. Correr el protocolo de `docs/manual/MANUAL_calibracion_analistas.md`, con
   foco en el **grupo 2**: polaridad opuesta, su inversa, discriminación de
   valor y negación. Ahí es donde una pérdida de recall se nota primero.
3. Anotar los resultados al lado de los de 1024, si los hay.

| Resultado | Qué hacer |
|---|---|
| Se sostienen | Cargar los 200.000 con 512 |
| Degradación clara | Subir `top_n` de 200 a 300 y volver a probar: el reranker y la verificación absorben bastante pérdida de recall |
| Sigue mal con `top_n` más alto | Volver a 1024 (ver **Rollback**), habiendo re-procesado 10.000 y no 200.000 |

**La razón de probar con un subconjunto es exactamente ésa**: que volver atrás
cueste poco. Cargar los 200.000 primero y validar después convierte una
decisión reversible en una irreversible.

También conviene dejar anotada una **línea de base de tiempos** por etapa de
una consulta típica (recuperación, reranking, verificación). Sin ella, dentro
de seis meses no se va a poder decir si el sistema se puso lento.

---

## Paso 8 · Dimensionar la instancia antes de la carga masiva

Con 512 dims y 200.000 respuestas, `db-f1-micro` (0,6 GB) queda corto: el
índice no entra en memoria y cada consulta lo lee de disco.

```bash
gcloud sql instances patch paneles-semantica --tier=db-g1-small
```

Reinicia la instancia —un par de minutos— y pasa de ~US$ 10 a ~US$ 34/mes. **La
bóveda puede quedarse en micro**: no tiene vectores.

> Si la carga masiva fuera puntual y después el uso liviano, se podría volver
> a `db-f1-micro` al terminar. Con 200.000 vectores lo más probable es que
> convenga dejarla en `g1-small` para que el índice entre en memoria, que es
> de lo que depende que una consulta tarde medio segundo o diez.

---

## Rollback

Ensayado, incluida la vuelta a aplicar la `0006` después. Devuelve el store
exactamente al estado de la `0005`:

```sql
begin;
drop index if exists respuesta_embedding_idx;
drop view  if exists v_dimension_embeddings;
drop view  if exists v_respuesta_estudio;

delete from respuesta;

alter table respuesta drop column embedding;
alter table respuesta add  column embedding vector(1024) not null;
alter table pregunta  drop column if exists embedding_texto;
alter table pregunta  add  column embedding_texto vector(1024);

create index respuesta_embedding_idx
    on respuesta using hnsw (embedding vector_cosine_ops);

create view v_respuesta_estudio as
select r.id             as respuesta_id,
       i.id_persona,
       c.ref_estudio,
       c.nombre         as estudio,
       c.fecha_campo,
       p.codigo         as pregunta_codigo,
       p.texto          as pregunta_texto,
       p.tipo           as pregunta_tipo,
       r.valor_texto,
       r.texto_embebido,
       r.embedding
  from respuesta r
  join individuo i    on i.id = r.individuo_id
  join pregunta p     on p.id = r.pregunta_id
  join cuestionario c on c.id = p.cuestionario_id;
commit;
```

**El rollback borra los embeddings igual que la ida**, y por la misma razón:
un vector de 512 tampoco se convierte a 1024. Hay que re-ingestar después.

Y el orden importa en los dos sentidos:

> **Volver atrás el código va junto con la base.** Con la columna en 1024 y el
> código en 512, la ingesta se frena sola con el mensaje del paso 6 —no rompe
> nada, pero no ingesta—. Hay que poner `EMBEDDINGS_DIMS=1024` en el entorno
> de la función (o volver el commit) y redesplegar.

---

## Errores que se pueden encontrar

| Mensaje | Qué pasó | Qué hacer |
|---|---|---|
| `cannot drop column embedding of table respuesta because other objects depend on it` | Se corrió el SQL del plan original, que no tira `v_respuesta_estudio` | Usar la migración del repo, que sí la contempla |
| `Hay N respuestas embebidas y esta migración las borra…` | El corpus creció más allá del tope de seguridad | Decidir a conciencia y usar la salida de escape del paso 4 |
| `` `respuesta.embedding` ya está en 512 dimensiones `` | La migración ya se aplicó | Nada: no se tocó nada. Si querés re-embeber, es una re-ingesta |
| `El proveedor de embeddings está configurado en N dimensiones y…` | `EMBEDDINGS_DIMS` y la columna no coinciden | Alinearlos. La ingesta se frenó **antes** de pagar ningún embedding |
| `El proveedor devolvió vectores de [1024] dimensiones y se le pidieron 512` | Voyage ignoró `output_dimension` | Paso 3: verificar el nombre del parámetro contra la documentación |
| `` `voyage-3.5` no genera vectores de N dimensiones `` | `EMBEDDINGS_DIMS` con un valor que el modelo no soporta | Usar 256, 512, 1024 o 2048 |
| `expected N dimensions, not M` (pgvector) | Quedó algún vector viejo en un camino que no pasa por la guarda | Re-ingestar ese estudio |

---

## Checklist

- [ ] 1 · Túnel abierto y `DSN_SEMANTICA` apuntando a `paneles_semantica`
- [ ] 2 · El diagnóstico dice que falta solo la `0006`
- [ ] 2 · `select count(*) from respuesta` anotado, y los estudios a re-ingestar
- [ ] 3 · **La llamada real a Voyage imprime `200 512`**
- [ ] 4 · `0006` aplicada con `--single-transaction`, con la salida de 15 líneas
- [ ] 5 · `v_dimension_embeddings` muestra las dos columnas en 512
- [ ] 5 · El índice HNSW existe de nuevo
- [ ] 6 · Sin `EMBEDDINGS_DIMS` vieja en el entorno de la función
- [ ] 6 · Funciones redesplegadas y el estudio existente re-ingestado
- [ ] 7 · **Validación de calidad con 5.000–10.000 respuestas, documentada**
- [ ] 7 · Línea de base de tiempos por etapa anotada
- [ ] 8 · `paneles-semantica` en `db-g1-small` **antes** de la carga masiva
- [ ] Recién entonces: cargar las 200.000

**El paso 7 es el que decide.** Los demás son mecánica; ése es el que dice si
el ahorro valió la pena, y es el único que no se puede hacer después.
