# Costos del sistema de paneles

**Proyecto GCP:** `gestion-paneles` · región `southamerica-east1` (São Paulo)
**Última actualización:** 2026-10-09

> **Cómo leer esto.** Las cifras son estimaciones a partir de las tarifas
> públicas de Google. La fuente de verdad es **Facturación → Informes** en la
> consola, agrupando por SKU. Revisar ahí antes de tomar cualquier decisión.

---

## 1. Resumen: qué se paga por mes

| Componente | Estimado/mes | Tipo |
|---|---:|---|
| Cloud SQL `paneles-boveda` (db-g1-small) | ~US$ 34 | Fijo, 24/7 |
| Cloud SQL `paneles-semantica` (db-g1-small) | ~US$ 34 | Fijo, 24/7 |
| Almacenamiento SSD (10 GB × 2) | ~US$ 3–4 | Fijo |
| Backups automáticos + PITR (bóveda) | ~US$ 2–5 | Fijo |
| Conector VPC (`paneles-conn`) | ~US$ 10–15 | Fijo, 24/7 |
| Cloud Functions, Hosting, Firestore, Secret Manager | ~US$ 0–2 | Casi todo en capa gratuita |
| Embeddings (Voyage) | por uso | Variable |
| Verificación con Claude | por uso | Variable |
| WhatsApp (Meta) | por conversación | Variable |
| **Base fija** | **~US$ 85–90** | |

**El grueso son las dos instancias de Cloud SQL: cerca del 80% del gasto fijo.**

### Por qué São Paulo cuesta más
`southamerica-east1` corre entre 20% y 40% por encima de `us-central1`. Un
`db-g1-small` que en Iowa sale ~US$ 26/mes, acá sale ~US$ 34. Se eligió la
región para que la PII no salga del continente y por latencia; el sobrecosto
es el precio de esa decisión.

### Por qué son dos instancias y no una
La separación bóveda / semántica es el invariante de privacidad del sistema:
la PII nunca convive con el store de embeddings. Juntarlas ahorraría ~US$ 34
al mes y rompería la columna vertebral del diseño. **No se recomienda.**

---

## 2. Cómo bajarlo

Ordenado por impacto y por cuánto compromete el diseño.

### 2.1 · Apagar las instancias cuando no se usan (ahorro: hasta el 100% del cómputo)

Cloud SQL cobra por tiempo encendido, no por uso. Mientras el sistema no tenga
uso continuo, **detener las instancias entre sesiones de trabajo** es el ahorro
más grande y no compromete nada.

```bash
# Apagar (deja de cobrar cómputo; el almacenamiento se sigue pagando)
gcloud sql instances patch paneles-boveda    --activation-policy=NEVER
gcloud sql instances patch paneles-semantica --activation-policy=NEVER

# Encender
gcloud sql instances patch paneles-boveda    --activation-policy=ALWAYS
gcloud sql instances patch paneles-semantica --activation-policy=ALWAYS
```

Con las instancias apagadas **la aplicación no funciona**: sirve para períodos
sin uso, no para producción con usuarios.

### 2.2 · Bajar a `db-f1-micro` (ahorro: ~US$ 48/mes)

`db-f1-micro` cuesta aproximadamente un tercio de `db-g1-small`
(~US$ 10 vs ~US$ 34 mensuales en São Paulo). Para el volumen actual alcanza.

Ver instrucciones completas en la sección 3.

### 2.3 · Sacar el PITR de la bóveda (ahorro: ~US$ 2–4/mes)

El *point-in-time recovery* guarda logs continuos. Si alcanza con los backups
diarios:

```bash
gcloud sql instances patch paneles-boveda --no-enable-point-in-time-recovery
```

> **Pensarlo dos veces.** La bóveda tiene el dato irrecuperable: PII y
> consentimientos. Con PITR se restaura a un punto exacto; sin él, se pierde
> todo lo ocurrido desde el último backup diario. Por pocos dólares, es el
> ahorro que menos conviene.

### 2.4 · Revisar el conector VPC

El conector de acceso serverless factura por las VMs que lo sostienen, estén
o no en uso. Verificar cuántas instancias tiene asignadas:

```bash
gcloud compute networks vpc-access connectors describe paneles-conn \
  --region=southamerica-east1
```

Si tiene más del mínimo, bajarlo con `--min-instances` / `--max-instances`.

### 2.5 · Direct VPC egress: evaluado y descartado (2026-09)

El conector VPC (`paneles-conn`) cuesta ~US$ 10–15/mes y **Direct VPC egress
haría lo mismo sin costo fijo**. Se evaluó migrar y **no es viable hoy**:

- Direct VPC egress para funciones de 2ª gen está **GA desde febrero de 2026**,
  y la CLI de Firebase lo soporta desde la 15.10.0 (marzo de 2026).
- Pero el soporte llegó al **SDK de Node**, no al de Python. Se verificó el
  paquete `firebase-functions` **0.6.0** (la última en PyPI): no expone
  `network_interfaces` ni ninguna opción de VPC directa. Las únicas que hay
  siguen siendo `vpc_connector` y `vpc_connector_egress_settings`.

Alternativas descartadas y por qué:

| Alternativa | Por qué no |
|---|---|
| Configurarlo en el Cloud Run de abajo con `gcloud run services update` | El siguiente `firebase deploy` lo pisa: el manifiesto del SDK no lo incluye. Habría que reaplicarlo en cada deploy. |
| Desplegar esas funciones con `gcloud` en vez de Firebase | Resuelve el problema pero saca al proyecto de su flujo de despliegue, por US$ 10–15/mes. |

**Cuándo revisarlo:** cuando el SDK de Python de `firebase-functions` exponga
la opción. Se verifica sin desplegar nada:

```bash
pip download firebase-functions --no-deps -d /tmp/ff
cd /tmp/ff && unzip -o -q *.whl -d x
grep -rniE "network_interface|direct_vpc" x/firebase_functions/
```

Si devuelve resultados, la migración pasa a ser viable.

> **Consecuencia para COLOQUIO.** Si se despliega con Firebase + Python, tampoco
> va a poder usar Direct VPC egress: **va a necesitar su propio conector VPC**,
> otros ~US$ 10–15/mes. Conviene tenerlo en el presupuesto desde el arranque.

### 2.6 · Lo que NO conviene tocar

- **Juntar las dos bases en una instancia**: rompe el invariante de privacidad.
- **Mover a una región más barata**: la PII saldría del continente, con las
  implicancias de URCDP que eso trae.
- **Apagar los backups**: el ahorro es marginal y el riesgo no.

---

## 3. Pasar a `db-f1-micro`

### Antes de hacerlo

`db-f1-micro` tiene **0.6 GB de RAM** contra 1.7 GB de `db-g1-small`. Para la
bóveda es de sobra: son consultas relacionales sobre tablas chicas. Para la
**semántica** hay que tener presente que el índice HNSW de pgvector rinde
cuando entra en memoria; con el corpus actual no hay problema, pero **si las
consultas semánticas se ponen lentas al crecer el corpus, esa instancia es la
primera candidata a volver a subir**.

Ninguno de los dos tiers de núcleo compartido tiene SLA de Cloud SQL. Ya era
así con `db-g1-small`: no es un cambio.

**Antes de bajar de tier, anotar los valores de referencia de la sección 4**
(memoria, conexiones, tamaño del índice y tiempos de una consulta típica): sin
línea de base no se puede saber después si el cambio afectó algo.

### El cambio

Requiere **reinicio de la instancia** (un par de minutos de indisponibilidad
cada una). Conviene hacerlo fuera de horario de uso.

```bash
# Bóveda
gcloud sql instances patch paneles-boveda \
  --tier=db-f1-micro

# Semántica
gcloud sql instances patch paneles-semantica \
  --tier=db-f1-micro
```

Cada comando pide confirmación y avisa que la instancia se va a reiniciar.

### Verificar

```bash
gcloud sql instances describe paneles-boveda    --format="value(settings.tier)"
gcloud sql instances describe paneles-semantica --format="value(settings.tier)"
```

Tienen que devolver `db-f1-micro`. Después, probar la aplicación: entrar,
listar panelistas y correr una consulta semántica. Si la consulta semántica
anda con tiempos parecidos a antes, el cambio no costó nada.

### Volver atrás

Es reversible con el mismo comando:

```bash
gcloud sql instances patch paneles-semantica --tier=db-g1-small
```

---

## 4. Cómo saber si `db-f1-micro` quedó chico

Cuatro señales. Conviene **anotar los valores antes del cambio** para tener con
qué comparar.

### 4.1 · Memoria — el límite real

`db-f1-micro` tiene **0.6 GB** contra 1.7 GB de `db-g1-small`. Es lo primero
que se agota.

**Dónde mirar:** consola → **Cloud SQL → la instancia → Monitoring** →
*Memory usage*.

| Uso sostenido | Lectura |
|---|---|
| < 70% | Cómodo |
| 70–85% | Vigilar |
| > 85% | Quedó chico |

**Síntoma feo:** la instancia se reinicia sola por falta de memoria. Si ves
reinicios no programados en **Operaciones**, es esto.

### 4.2 · CPU — throttling por créditos de ráfaga

Los tiers de núcleo compartido aguantan picos con créditos, pero si el uso
sostenido pasa ~70% empiezan a throttlear y **todo se pone lento de forma
pareja**, sin errores.

**Dónde mirar:** mismo panel de Monitoring → *CPU utilization*.

### 4.3 · Conexiones — la que más probablemente muerda primero

No depende del volumen de datos sino de Cloud Functions: cada instancia de la
función abre su pool, y al escalar las conexiones se multiplican.
`db-f1-micro` admite bastante menos que `db-g1-small`.

**Cómo verificar** (en cualquiera de las dos bases, con el proxy corriendo):

```bash
psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda -c "
show max_connections;
select count(*) as en_uso from pg_stat_activity;"
```

Si `en_uso` se acerca a `max_connections`, ese es el techo. **El síntoma es un
error de «too many connections», no lentitud.**

### 4.4 · Que el índice vectorial no entre en RAM (solo `paneles-semantica`)

El índice HNSW de pgvector rinde cuando entra en memoria. Cada vector son
1024 dimensiones × 4 bytes ≈ **4 KB**, más el grafo del índice.

| Respuestas en el corpus | Tamaño aprox. | Con 0.6 GB |
|---:|---:|---|
| 10.000 | ~40 MB | Cómodo |
| 50.000 | ~200 MB | En el límite |
| 100.000 | ~400 MB | Ya no entra |

**Cómo verificar el tamaño real:**

```bash
psql -h 127.0.0.1 -p 5433 -U app_paneles -d paneles_semantica -c "
select count(*) as respuestas from respuesta;
select pg_size_pretty(pg_total_relation_size('respuesta')) as tabla_total;
select indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) as indice
  from pg_stat_user_indexes where relname = 'respuesta';"
```

Si el índice HNSW se acerca a la memoria disponible, **`paneles-semantica` es
la primera candidata a volver a `db-g1-small`** (la bóveda puede quedarse en
micro).

### 4.5 · La señal más práctica: el diagnóstico de la propia app

Sin entrar a GCP. La pantalla de consulta muestra los tiempos por etapa. Si
**crece el tiempo de la etapa de recall** mientras reranker y verificación se
mantienen, el problema es la base y no los proveedores externos.

- [ ] Anotar los tiempos de una consulta típica **antes** de bajar de tier.

### 4.6 · Alertas (gratis, y avisan antes de que se note)

Configurar en **Cloud Monitoring → Alerting**, sin costo en la capa gratuita:

- Memoria > 85% sostenido 5 minutos.
- CPU > 70% sostenido 10 minutos.
- Conexiones > 80% del máximo.

### 4.7 · Volver atrás

Reversible, con un reinicio de un par de minutos. Cuesta ~US$ 24/mes más por
instancia:

```bash
gcloud sql instances patch paneles-semantica --tier=db-g1-small
```

No hace falta subir las dos: lo habitual es que la semántica necesite volver y
la bóveda se quede en micro.

---

## 5. Costos variables (por uso)

No entran en la base fija y dependen de la actividad:

- **Embeddings (Voyage):** se paga por token embebido. Se gasta en la **ingesta**
  (una vez por respuesta, salvo re-ingesta) y en **cada consulta** (una vez por
  criterio semántico). El `hash_texto` de Fase 2 evita re-embeber lo que no
  cambió.
- **Verificación con Claude:** lotes de 25 unidades de evidencia por
  llamada, sobre el top-k. **Es la etapa más cara del pipeline**: bajar
  `top_k` es la palanca directa sobre este costo. Desde R-CS las respuestas
  repetidas se verifican una vez (unidades de evidencia), y cada consulta
  registra su costo real (sección 5.1).
- **Reranker:** una llamada por criterio semántico.
- **WhatsApp (Meta):** por conversación iniciada por el negocio. Escala con el
  tamaño de las convocatorias.

> **Regla práctica para consultas.** Acotar por panel y por segmento
> demográfico antes de consultar reduce el costo *y* mejora el resultado: los
> filtros demográficos se resuelven en la bóveda, sin embeddings, sin reranker
> y sin Claude.

### 5.1 · La consulta completa (R-CS, addendum A2)

La consulta de alcance **completo** verifica todas las unidades de evidencia
elegibles en vez de las mejores 25 personas. El orden de magnitud, con la
heurística de `costo_consulta.py` y las tarifas por omisión (Claude Sonnet 5
US$ 2 / 10 por millón de tokens de entrada / salida; Voyage `rerank-2.5`
US$ 0,05; `voyage-3.5` US$ 0,06):

| | Exploratoria (`top_k` 25) | Completa, corpus actual |
|---|---:|---:|
| Corpus de referencia | — | 24.935 respuestas · 1.008 personas · 43 preguntas |
| Unidades verificadas por criterio | ≤ 75 (en general bastantes menos) | del orden de miles |
| Llamadas a Claude por criterio (lotes de 25) | 1 a 3 | ~120 (3.000 u.) a ~600 (15.000 u.) |
| Tokens por unidad | ~61 de entrada · ~46 de salida | igual |
| **Costo por criterio, con 25% de margen** | **≈ US$ 0,05** | **≈ US$ 2,20 (3.000 u.) · 5,90 (8.000) · 11 (15.000)** |

- **La diferencia es de dos órdenes de magnitud**, y se multiplica por cada
  criterio semántico y por cada ejecución. El 80% del costo es la **salida**
  de Claude (una razón por veredicto): el reranking es despreciable.
- **El número real del corpus se obtiene antes de habilitar nada** con
  `scripts/diagnosticar_consulta.py estimar --criterio "…"` contra la base
  (no escribe nada ni llama a Claude). Ese número es el que decide quién
  puede lanzarla y cuántas veces.
- **Controles en el producto:** estimación en pantalla antes de confirmar,
  **presupuesto obligatorio** por ejecución que la frena sola, techo por
  configuración (`CONSULTA_PRESUPUESTO_MAXIMO_USD`, US$ 25 por omisión),
  permiso propio (`consulta_completa`: admin y operaciones) y costo real por
  lote y por ejecución en `consulta_lote` / `v_consulta_progreso`. La
  estimación se calibra sola con los tokens observados.
- **Las tarifas son configuración** (`COSTO_CLAUDE_ENTRADA_USD_MTOK`,
  `COSTO_CLAUDE_SALIDA_USD_MTOK`, `COSTO_RERANK_USD_MTOK`,
  `COSTO_EMBEDDING_USD_MTOK` en `functions/.env`). Si cambia el modelo de
  `CLAUDE_MODELO`, hay que cambiarlas también, o la estimación miente.

> **Consecuencia de infraestructura.** La completa recorre todo el corpus de
> las personas habilitadas a fuerza bruta (no por el índice HNSW, que sirve
> para «los N más cercanos»). Con `paneles-semantica` en `db-f1-micro`
> (0,6 GB) conviene medirlo en la prueba de carga del despliegue (sección 4);
> si la etapa de elegibilidad o la memoria se disparan, la salida es
> `db-g1-small` (~US$ 24/mes más), que es reversible (4.7).

---

## 6. Controles recomendados

- [ ] **Presupuesto con alertas** en Facturación (ej. aviso al 50%, 90% y 100%
      de un tope mensual). Es lo que evita la próxima sorpresa.
- [ ] Revisar el informe por SKU una vez al mes.
- [ ] Definir si el sistema necesita estar encendido 24/7 o puede apagarse
      entre usos.
- [ ] Estimar el costo por consulta semántica una vez calibrado `top_k`
      (el diagnóstico de cada consulta ya lo muestra; ver 5.1).
- [ ] Fijar `CONSULTA_PRESUPUESTO_MAXIMO_USD` con el número real de
      `diagnosticar_consulta.py estimar` antes de habilitar la consulta completa.
- [ ] Alertas de memoria, CPU y conexiones configuradas (sección 4.6).
- [ ] Línea de base anotada antes de cualquier cambio de tier (sección 4).
