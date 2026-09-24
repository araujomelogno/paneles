# Costos del sistema de paneles

**Proyecto GCP:** `gestion-paneles` · región `southamerica-east1` (São Paulo)
**Última actualización:** 2026-09-24

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

### 2.5 · Lo que NO conviene tocar

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

## 4. Costos variables (por uso)

No entran en la base fija y dependen de la actividad:

- **Embeddings (Voyage):** se paga por token embebido. Se gasta en la **ingesta**
  (una vez por respuesta, salvo re-ingesta) y en **cada consulta** (una vez por
  criterio semántico). El `hash_texto` de Fase 2 evita re-embeber lo que no
  cambió.
- **Verificación con Claude:** una llamada por consulta semántica, sobre el
  top-k. **Es la etapa más cara del pipeline**: bajar `top_k` es la palanca
  directa sobre este costo.
- **Reranker:** una llamada por criterio semántico.
- **WhatsApp (Meta):** por conversación iniciada por el negocio. Escala con el
  tamaño de las convocatorias.

> **Regla práctica para consultas.** Acotar por panel y por segmento
> demográfico antes de consultar reduce el costo *y* mejora el resultado: los
> filtros demográficos se resuelven en la bóveda, sin embeddings, sin reranker
> y sin Claude.

---

## 5. Controles recomendados

- [ ] **Presupuesto con alertas** en Facturación (ej. aviso al 50%, 90% y 100%
      de un tope mensual). Es lo que evita la próxima sorpresa.
- [ ] Revisar el informe por SKU una vez al mes.
- [ ] Definir si el sistema necesita estar encendido 24/7 o puede apagarse
      entre usos.
- [ ] Estimar el costo por consulta semántica una vez calibrado `top_k`.
