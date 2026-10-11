# SPEC — Unidades de evidencia: un vector por texto distinto

**Sistema:** Gestión de paneles · store semántico, recall y verificación
**Relación:** es el **cambio 3** de la solicitud de cambio de consultas
semánticas, ahora **con evidencia medida**
**Complementa:** el recall exacto por debajo de umbral (ya desplegado)
**Estado:** Para desarrollo

---

## 1. La evidencia

Medido sobre el corpus real, 2026-10-11:

```sql
select count(*) as respuestas, count(distinct texto_embebido) as textos_distintos
  from respuesta;

 respuestas | textos_distintos
------------+------------------
      21340 |              861
```

**El 96 % del corpus son copias.** Cada texto distinto aparece, en promedio,
unas 25 veces. La causa es estructural: en una pregunta cerrada, todas las
personas que eligen la misma opción producen el mismo texto
—`"¿De qué marca es tu celular? → Xiaomi"`— y por lo tanto el mismo vector.

Y el efecto sobre el índice, medido con `recall-indice` a `ef_search=200`:

| Criterio | `input_type` | Recall del índice |
|---|---|---:|
| gente que usa un celular xiaomi | query | **0,0 %** |
| gente que usa un celular xiaomi | document | **0,0 %** |
| gente a la que le gusta el fernet | query | **0,0 %** |
| gente a la que le gusta el fernet | document | 59,5 % |
| personas que viajan al exterior por trabajo | query | 87,5 % |
| personas que viajan al exterior por trabajo | document | 71,5 % |

Los casos con **0 %** son exactamente los que tienen **bloques de vectores
idénticos** en la cima del resultado exacto: los cinco primeros puestos de
Xiaomi están todos a 0,3712; los de fernet, todos a 0,5797. La pregunta de
viajes, con respuestas más variadas, rinde razonablemente.

## 2. Por qué es un solo problema con dos síntomas

**Síntoma 1 — el índice no encuentra el bloque.** HNSW conecta cada punto con
sus vecinos; cientos de puntos idénticos forman un bloque que la búsqueda no
alcanza desde afuera. **No es un problema de ajuste**: con `ef_search=200` ya
da 0 %, y los duplicados aumentan al crecer el corpus, no disminuyen.

**Síntoma 2 — la verificación se gasta en copias.** Aun con recall correcto,
los primeros puestos son copias del mismo texto, y `top_k = 25` termina
verificando **25 veces la misma idea**. Es lo que se observó en el problema
original: 25 respuestas repetidas ocupando el top.

> **La hipótesis del «recorte por repetición» no estaba mal en el fondo.** La
> repetición **es** el problema; lo que no se veía era que también rompe el
> índice. Los dos síntomas tienen una sola causa y una sola solución.

## 3. La solución

**Embeber cada texto distinto una sola vez**, y que todas las respuestas
idénticas apunten a ese mismo vector.

### R-UE.1 — La unidad de evidencia (P0)

- Dado un `texto_embebido`, entonces existe **una sola unidad** con ese texto y
  su vector.
- Dada una respuesta, entonces **referencia a su unidad**, en vez de llevar su
  propio vector.
- [ ] La clave de la unidad es el **texto embebido completo**, que ya incluye la
      pregunta: `"→ Xiaomi"` en dos preguntas distintas son **dos unidades**.
      Los 861 de la medición ya tienen esto en cuenta.
- [ ] El `hash_texto` que ya existe (Fase 2) sirve como clave de la unidad.

**Esquema:** una tabla `unidad_evidencia` (`id`, `hash_texto` único,
`texto_embebido`, `embedding vector(512)`), y en `respuesta` una referencia
`unidad_id`. El índice vectorial pasa a estar sobre `unidad_evidencia`.

### R-UE.2 — Recall sobre unidades (P0)

- Dado un criterio, entonces el recall busca entre **unidades**, no entre
  respuestas.
- Dadas las unidades recuperadas, entonces se resuelven a las **personas** que
  las tienen, a través de sus respuestas.
- [ ] El gate de consentimiento se aplica **al resolver personas**, igual que
      hoy: una unidad puede estar en el recall aunque alguna de sus personas no
      tenga `uso_semantico` vigente; esa persona simplemente no aparece.

### R-UE.3 — Verificación sobre unidades (P0)

- Dado `top_k`, entonces cuenta **unidades distintas**, no respuestas: 25
  unidades son 25 ideas distintas.
- Dado un veredicto sobre una unidad, entonces **se aplica a todas las personas**
  que tienen esa respuesta.
- [ ] La agregación por persona sigue igual: una persona con varias unidades
      combina sus veredictos con las reglas actuales.

> **Este es el ahorro grande.** La verificación es la etapa más cara del
> pipeline. Hoy, una unidad repetida 200 veces puede verificarse muchas veces;
> con unidades, **se verifica una vez** y el veredicto llega a las 200 personas.

### R-UE.4 — Ingesta (P0)

- Dada una respuesta nueva, entonces se calcula su `hash_texto`; si la unidad
  **ya existe**, se reutiliza **sin volver a embeber**; si no, se crea y se
  embebe.
- [ ] El reproceso (Fase 8) y la ingesta diferida siguen funcionando: el
      cambio es dónde vive el vector, no cómo se calcula.

### R-UE.5 — Migración del corpus existente (P0)

- Dado el corpus actual, entonces se crean las 861 unidades a partir de los
  textos distintos.
- [ ] **No hace falta re-embeber nada**: textos idénticos ya tienen vectores
      idénticos. Se toma el vector de cualquiera de las respuestas de cada
      texto. **La migración cuesta US$ 0 en embeddings.**
- [ ] Verificar antes de migrar que, efectivamente, todas las respuestas con el
      mismo `hash_texto` tienen el mismo vector. Si alguna no lo tiene —por
      ejemplo, porque se embebió con otro `input_type` o antes del cambio a 512
      dimensiones—, esa es una inconsistencia que conviene conocer, no
      ocultar con la migración.
- [ ] La columna `respuesta.embedding` se conserva hasta verificar que todo
      funciona sobre unidades, y recién después se elimina.

## 4. Lo que esto corrige de documentos anteriores

**El addendum de la solicitud de cambio (A2)** estimó que una evaluación
**completa** costaría *«cientos de llamadas a Claude por criterio»*, calculado
sobre las ~25.000 respuestas. Con unidades, el universo es de **861 textos
distintos**: una evaluación completa de un criterio sobre **todo el corpus**
son **unas 35 llamadas** en lotes de 25. El modo completo, que parecía caro,
pasa a ser perfectamente viable.

**El reporte del recall con índice** sugería volver al índice, con un
`ef_search` más alto, cuando el corpus superara el umbral. **Con duplicados eso
no funciona.** Con unidades sí: el índice sobre 861 vectores distintos no tiene
bloques idénticos, y el problema de fondo desaparece.

## 5. Pruebas de aceptación

- [ ] El corpus migrado tiene una unidad por `hash_texto` distinto (test:
      861 unidades sobre el corpus actual).
- [ ] Ninguna respuesta queda sin unidad (test).
- [ ] *«gente que usa un celular xiaomi»* recupera la unidad de Xiaomi y la
      resuelve a **todas** las personas que la eligieron (test).
- [ ] `top_k = 25` verifica **25 unidades distintas** (test).
- [ ] Un veredicto sobre una unidad llega a todas sus personas (test).
- [ ] Una persona sin `uso_semantico` vigente no aparece, aunque su unidad esté
      en el recall (test).
- [ ] Una respuesta nueva con un texto ya existente **no dispara un embedding**
      (test).
- [ ] Con unidades, el recall del índice sobre los criterios de prueba sube muy
      por encima del 0 % actual (medición con `recall-indice`).
- [ ] Los resultados de las consultas existentes no cambian en personas
      encontradas, salvo para mejor (test de no regresión).

## 6. Costos

| | Hoy | Con unidades |
|---|---:|---:|
| Vectores a embeber e indexar | 21.340 | **861** |
| Tamaño del índice vectorial | 12 MB | ~0,5 MB |
| Embeddings de una carga nueva | uno por respuesta | **uno por texto nuevo** |
| Verificación con `top_k = 25` | hasta 25 copias de una idea | **25 ideas distintas** |
| Evaluación completa de un criterio | cientos de llamadas (estimado) | **~35 llamadas** |
| Migración del corpus actual | — | **US$ 0** (no se re-embebe) |

> **Dónde se nota el ahorro.** En embeddings, poco en dinero: ya eran baratos.
> El ahorro real está en la **verificación**, la etapa cara, porque cada idea se
> verifica una vez. Y en infraestructura: con un índice de medio megabyte, la
> instancia semántica en `db-f1-micro` deja de tener fecha de vencimiento por
> tamaño.

## 7. Riesgos y preguntas abiertas

- **[datos]** Si se encuentran respuestas con el **mismo texto y distinto
  vector** (R-UE.5), conviene entender por qué antes de migrar: pueden ser
  restos de cargas anteriores al cambio de dimensiones o de `input_type`.
- **[producto]** Con unidades, el diagnóstico de una consulta debería mostrar
  **cuántas personas** hay detrás de cada unidad. Una unidad que representa a
  200 personas y otra que representa a una no tienen el mismo peso para el
  analista, aunque sean una fila cada una.
- **[ingeniería]** La reidentificación, la ficha del panelista (R7.6) y la
  exportación siguen trabajando a nivel **respuesta y persona**, no unidad.
  Verificar que ninguna asuma que cada respuesta tiene su propio vector.
- **[alcance]** Este cambio toca el corazón del store semántico. Conviene
  desplegarlo **después** de que el recall exacto esté estable en producción, y
  con el respaldo de la base hecho.
