# El diagnóstico A1.2 mide mal — y lo que eso confirma sobre la causa raíz

**Sistema:** Gestión de paneles · `scripts/diagnosticar_consulta.py`
**Severidad:** Alta para el diagnóstico — **da un falso negativo** que haría
descartar la hipótesis correcta
**Detectado:** 2026-10-09, corriendo A1 del despliegue R-CS
**Estado:** Para corregir

---

## 1. El síntoma

Con el mismo corpus y la misma condición, dos subcomandos del mismo script se
contradicen:

```
$ python3 scripts/diagnosticar_consulta.py a1-texto --patron xiaomi
A1.1 · 50 respuesta(s) con «xiaomi» (máx. 50):
  #27909 [var273] '¿De qué marca es tu celular?'
      texto_embebido = '¿De qué marca es tu celular? → Xiaomi'

$ python3 scripts/diagnosticar_consulta.py a1-distancia \
    --criterio "gente que usa un celular xiaomi" --patron xiaomi --contra titular
  [query]    no hay respuestas con «xiaomi»: correr a1-texto.
  [document] no hay respuestas con «xiaomi»: correr a1-texto.
```

El mensaje manda a correr `a1-texto`, que es precisamente el que **sí las
encuentra**.

## 2. Qué se descartó primero

| Hipótesis | Resultado |
|---|---|
| Mayúsculas (`like` vs `ilike`) | Descartada: los dos usan `ilike` |
| Dimensión del vector (512 vs 1024) | Descartada: columna **512**, proveedor **512** |
| La clave de embeddings no llega | Descartada: el proveedor responde |
| Excepción tragada | Descartada: `db.todas` propaga, el script no tiene `try` |
| Embeddings nulos | Descartada: **0 nulos** |
| Base equivocada | Descartada: `paneles_semantica`, 21.340 respuestas |

Reproduciendo `_distancias` paso a paso:

```
== 2. Solo el WHERE, sin vector ==        {'n': 218}
== 4. La consulta completa (vector ceros) == filas: 0
== 5. Lo mismo, con el vector real ==       filas: 0
```

**218 filas cumplen el `where`. La misma consulta con el `order by` de
distancia devuelve 0.** Sin error.

## 3. La causa: el índice HNSW gana al `where`

```sql
select r.id, r.valor_texto, r.embedding <=> %s::vector as distancia
  from respuesta r
 where r.valor_texto ilike %s or r.texto_embebido ilike %s
 order by 3 limit %s
```

Con un `order by` de distancia y un `limit`, Postgres resuelve por el **índice
vectorial**: el índice HNSW devuelve sus vecinos más cercanos **de todo el
corpus** —un conjunto acotado, del orden de `ef_search`— y **recién después**
se aplica el `ilike`. Si entre esos vecinos no hay ninguna fila con «xiaomi»,
el resultado es **cero filas, sin error**.

No es un problema de los datos ni del filtro: es el **plan de ejecución**.

**Comprobación:**

```sql
set enable_indexscan = off;
set enable_bitmapscan = off;
-- la misma consulta ahora devuelve filas
```

> **Es la trampa clásica de una búsqueda ANN con filtro.** El índice es
> aproximado y se aplica **antes** del `where`, así que un filtro selectivo
> sobre un vecindario chico devuelve vacío. Lo engañoso es que **no falla**:
> devuelve un resultado perfectamente válido —el conjunto vacío— y el script lo
> interpreta como «no existe esa respuesta».

## 4. Qué hay que corregir en el script

- [ ] En `_distancias`, **forzar el recorrido secuencial** sobre el conjunto
      filtrado: materializar el filtro primero (una CTE `materialized` con el
      `ilike`) y **recién sobre ese conjunto** calcular y ordenar por distancia.
      El objetivo de A1.2 es medir la distancia de **filas conocidas**, no
      buscar vecinos: el índice no corresponde acá.
- [ ] El conteo de «cuántas están más cerca» (`delante`) **sí** debe usar el
      índice: ahí la pregunta es sobre el corpus entero. Conviene que el
      comentario lo diga, porque son dos consultas con intenciones opuestas.
- [ ] Si el conjunto filtrado viene **vacío de verdad**, el mensaje debe
      distinguirlo de «el patrón no está en el corpus»: hoy dice lo segundo
      cuando pasa lo primero.
- [ ] Revisar el resto de `diagnosticar_consulta.py` por el mismo patrón:
      cualquier consulta que combine `where` selectivo con `order by <=>` y
      `limit` tiene el mismo problema.

## 5. Lo que esto confirma sobre la causa raíz — **lo más importante**

El addendum pedía correr A1 **antes de implementar**, porque el resultado podía
reducir el alcance. Ya está corrido, y lo que muestra:

**La evidencia existe y está bien.** `'¿De qué marca es tu celular? → Xiaomi'`,
con la etiqueta resuelta —no un código—. **Descartada la hipótesis de ingesta**,
que era la que habría invalidado los cambios 3 y 5.

**Y el mismo bug explica el problema original.** Que el índice HNSW devuelva
vecinos del corpus entero **antes** de cualquier filtro es exactamente lo que
pasa en la consulta real: el `top_n` trae los 200 más cercanos, y si 25 lugares
se los llevan respuestas repetidas sobre titularidad del contrato, las de marca
**quedan fuera del pool**. No es que estén mal, es que no entran.

> **Conclusión para la solicitud de cambio:** la hipótesis correcta es la del
> **recorte por repetición** (cambio 3, unidades de evidencia). El cambio 1
> (`input_type`) y el 4 (`irrelevante`) siguen siendo correctos y baratos. La
> pregunta sobre ingesta queda **cerrada**: no era eso.

- [ ] Queda pendiente la tercera verificación del addendum (**A1.3**): si el
      reranker corrió en esa consulta. Sigue valiendo: un reranker degradado
      explicaría por qué 25 respuestas idénticas ocuparon el top aun estando
      dentro del pool.

## 6. Definition of Done

- [ ] `a1-distancia` con `--patron xiaomi` devuelve la distancia de esas
      respuestas (test con el corpus real).
- [ ] El conteo de «cuántas están más cerca» sigue usando el índice y da el
      mismo número que antes (test de no regresión).
- [ ] Un patrón que **de verdad** no existe en el corpus da un mensaje distinto
      del de un conjunto filtrado vacío (test).
- [ ] Ninguna otra consulta del script combina filtro selectivo con `order by
      <=>` y `limit` sin materializar primero.

## 7. Nota de método

Tres diagnósticos de este proyecto terminaron en lo mismo: **un fallo técnico
presentándose como un resultado de negocio**. La API key sin cargar se veía como
«contraseña incorrecta»; el truncamiento de Claude se veía como veredictos
`dudoso`; y ahora un plan de ejecución se ve como «esa respuesta no existe».

Las tres veces, el costo fue descartar hipótesis a mano durante horas.

- [ ] Como criterio general: cuando una función devuelve **vacío o un valor por
      defecto**, que el código distinga —y registre— si eso es un resultado
      legítimo o la consecuencia de algo que no se pudo hacer.
