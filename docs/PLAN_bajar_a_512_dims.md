# Plan — Bajar los embeddings de 1024 a 512 dimensiones

**Sistema:** Gestión de paneles · store semántico
**Cuándo:** **antes** de cargar las 200.000 respuestas
**Última actualización:** 2026-10-02

---

## 0 · Por qué ahora y no después

| | 1024 dims | 512 dims |
|---|---:|---:|
| Vectores de 200.000 respuestas | ~820 MB | ~410 MB |
| Con el índice HNSW | ~1,5–2 GB | ~0,8–1 GB |
| Instancia necesaria | ~4 GB RAM (custom) | `db-g1-small` (1,7 GB) |
| Costo mensual de esa instancia | ~US$ 50–70 | ~US$ 34 |

**El corpus actual tiene una sola respuesta.** Re-embeber hoy cuesta una llamada
a Voyage; después de cargar 200.000, cuesta re-procesar todo el corpus. Esta es
la ventana.

**Costo del cambio:** US$ 0 en embeddings (una respuesta). El costo real de la
decisión es la instancia, y es lo que se ahorra: ~US$ 20–35/mes para siempre.

> **Lo que se resigna:** entre uno y dos puntos porcentuales de calidad de
> recuperación según los benchmarks de Voyage. Voyage 3.5 usa *Matryoshka*: las
> primeras 512 dimensiones concentran la mayor parte de la información, así que
> truncar no es cortar al azar. **Pero esos benchmarks no son sobre respuestas
> de encuesta en español rioplatense**, así que el paso 6 (validación) no es
> opcional.

---

## 1 · Arreglar un bug que hay que resolver igual

`functions/panel_api/embeddings.py` guarda `self.dims` pero **nunca se lo manda
a Voyage**: el cuerpo de la llamada no lleva el parámetro de dimensión, así que
la API devuelve el default de 1024 pase lo que pase. Cambiar `EMBEDDINGS_DIMS`
hoy no tendría ningún efecto.

En el método `embeber` del proveedor Voyage, agregar `output_dimension` al
cuerpo del pedido:

```python
json={
    "input": list(textos),
    "model": self.modelo,
    "input_type": "document",
    "output_dimension": self.dims,
},
```

> **Verificar el nombre exacto del parámetro** contra la documentación de Voyage
> antes de desplegar: si el nombre no coincide, la API lo ignora en silencio y
> seguís con 1024 sin enterarte. El paso 3 lo detecta.

Y que el proveedor **determinístico** (el de pruebas) use la misma dimensión,
para que los tests no mientan.

## 2 · Cambiar el default a 512

Tres lugares:

- `functions/panel_api/embeddings.py`: los `dims=1024` de las firmas y el
  `entorno.get("EMBEDDINGS_DIMS", "1024")` → `512`.
- `.env.ejemplo`: `EMBEDDINGS_DIMS=512`.
- La variable de entorno de la función desplegada, si está seteada en 1024.

## 3 · Confirmar que Voyage devuelve 512 — **antes de tocar la base**

Con la clave real, una llamada suelta:

```bash
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

Tiene que imprimir `200 512`. **Si imprime `200 1024`, el parámetro está mal
escrito** y hay que corregirlo antes de seguir: migrar la columna a 512 con un
proveedor que devuelve 1024 deja el sistema roto.

## 4 · Migración de la base

Nueva migración en `db/semantica/`. Dos columnas cambian: `respuesta.embedding`
y `pregunta.embedding_texto` (la de la `0004`).

Como el corpus es de **una fila**, lo más simple es recrear las columnas en vez
de convertirlas:

```sql
-- db/semantica/0006_embeddings_512.sql

-- El índice depende de la columna: se cae primero.
drop index if exists respuesta_embedding_idx;

alter table respuesta  drop column embedding;
alter table respuesta  add  column embedding vector(512) not null;

alter table pregunta   drop column if exists embedding_texto;
alter table pregunta   add  column embedding_texto vector(512);

create index respuesta_embedding_idx
    on respuesta using hnsw (embedding vector_cosine_ops);
```

> ⚠ **Esto borra los embeddings existentes.** Con una sola respuesta es
> intrascendente; si al momento de aplicarlo hubiera más, hay que re-ingestar
> esos estudios después (la ingesta es idempotente).
>
> Confirmar antes: `select count(*) from respuesta;`

Aplicar:

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/semantica/0006_embeddings_512.sql
```

Verificar:

```bash
psql "$DSN_SEMANTICA" -c "
select column_name, udt_name,
       (select atttypmod from pg_attribute
         where attrelid = 'respuesta'::regclass and attname = 'embedding') as dims_mas_4
  from information_schema.columns
 where table_name = 'respuesta' and column_name = 'embedding';"
```

## 5 · Desplegar y re-ingestar lo que haya

```bash
firebase deploy --only functions
```

Y re-ingestar el estudio existente, para que su respuesta quede en 512. El
`hash_texto` no ayuda acá: el texto no cambió, pero la dimensión sí, así que
conviene forzar el re-embedding de ese estudio.

## 6 · Validar la calidad antes de cargar 200.000 — **no saltear**

Cargar un subconjunto de **5.000 a 10.000 respuestas** y correr el protocolo de
calibración (`MANUAL_calibracion_analistas.md`), con foco en el **grupo 2**:
polaridad opuesta, su inversa, discriminación de valor y negación. Ahí es donde
una pérdida de calidad se notaría primero.

- **Si los resultados se sostienen** → cargar los 200.000 con 512.
- **Si hay degradación clara** → subir `top_n` (de 200 a 300) y volver a probar;
  el reranker y la verificación absorben bastante pérdida de recall.
- **Si sigue mal** → volver a 1024 habiendo re-procesado 10.000 y no 200.000.

> Volver atrás es el mismo plan al revés: cambiar el default a 1024, una
> migración que recrea las columnas, y re-ingestar. Por eso conviene hacer la
> prueba con un subconjunto chico.

## 7 · Dimensionar la instancia antes de la carga masiva

Con 512 dims y 200.000 respuestas, `db-f1-micro` (0,6 GB) queda corto. Subir la
**semántica** antes de cargar:

```bash
gcloud sql instances patch paneles-semantica --tier=db-g1-small
```

Reinicia la instancia (un par de minutos) y pasa de ~US$ 10 a ~US$ 34/mes. La
bóveda puede quedarse en micro.

> Si la carga masiva es puntual y después el uso es liviano, se puede volver a
> `db-f1-micro` al terminar — aunque con 200.000 vectores lo más probable es que
> convenga dejarla en `g1-small` para que el índice entre en memoria.

## 8 · Checklist

- [ ] `output_dimension` se envía en la llamada a Voyage (paso 1).
- [ ] El proveedor determinístico usa la misma dimensión.
- [ ] Default cambiado a 512 en código, `.env.ejemplo` y entorno desplegado.
- [ ] **Confirmado que Voyage devuelve 512** con una llamada real (paso 3).
- [ ] `select count(*) from respuesta` revisado antes de migrar.
- [ ] Migración `0006` aplicada con `--single-transaction`.
- [ ] Columnas verificadas en 512 y el índice HNSW recreado.
- [ ] Funciones redesplegadas y el estudio existente re-ingestado.
- [ ] Validación de calidad con subconjunto (paso 6) hecha y documentada.
- [ ] `paneles-semantica` en `db-g1-small` antes de la carga masiva.
- [ ] Línea de base anotada: tiempos por etapa de una consulta típica.
