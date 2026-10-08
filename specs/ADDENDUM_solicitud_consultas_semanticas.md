# Addendum a «Solicitud de cambio: recuperación y verificación de consultas semánticas»

**Complementa, no reemplaza.** El documento original es sólido: el diagnóstico
es honesto sobre lo que no puede probar, la distinción entre `alcance` y `modo`
es la pieza conceptual que faltaba, y el estado `irrelevante` corrige un error
real de agregación.

Lo que sigue son **cinco agregados**. Los dos primeros conviene resolverlos
**antes de escribir código**: pueden reducir el alcance a una fracción.

---

## A1 · Verificación empírica previa — puede invalidar buena parte del trabajo

La solicitud dice, con razón, que la captura *«no determina si faltó en la
ingesta, en el recall o en la selección posterior»*. Y después propone un
catálogo de unidades de evidencia con migraciones, backfill y un camino de
ejecución persistente.

**Esa incertidumbre se resuelve con tres consultas de cinco minutos**, y según
el resultado, los cambios 3 y 5 podrían no hacer falta para este caso.

### A1.1 — ¿La respuesta existe en el store semántico?

```sql
select r.id, p.codigo, p.texto, r.valor_texto, r.texto_embebido
  from respuesta r join pregunta p on p.id = r.pregunta_id
 where r.valor_texto ilike '%xiam%' or r.texto_embebido ilike '%xiam%';
```

- **Si no devuelve nada**, la evidencia **nunca entró**: el problema es de
  ingesta —la variable no se mapeó, o se embebió el código en vez de la
  etiqueta— y ningún cambio de recuperación lo arregla.
- Este proyecto **ya tiene ese problema documentado**: hay respuestas embebidas
  como `→ 11427` y `→ Checked` en vez de sus etiquetas. La pregunta de marca
  puede estar en esa situación.

### A1.2 — ¿A qué distancia quedó del criterio?

Con el vector de `xiaomi`, comparar la distancia de esa respuesta contra la del
contrato que sí entró. Si la respuesta de marca está **más lejos** que 25
repeticiones del contrato, el problema es de **calidad del embedding** —`xiami`
mal escrito contra `xiaomi`— y el catálogo de unidades **no lo resuelve**: la
propia solicitud lo reconoce en su alcance.

Si está **más cerca** pero igual no llegó, entonces sí es el recorte por
repetición, y el cambio 3 está justificado.

### A1.3 — ¿El reranker corrió en esa consulta?

```sql
select diagnostico from consulta_ejecucion  -- o donde se persista
 where ... order by creado_en desc limit 1;
```

Buscar `degradaciones` y el proveedor de rerank efectivo.

> **Esta es la hipótesis más barata de todas y la solicitud no la menciona.**
> Que 25 respuestas idénticas e irrelevantes ocupen el top es exactamente lo
> que un reranker existe para evitar: evalúa consulta y candidato en conjunto y
> habría puesto la titularidad del contrato muy por debajo de la marca. **Si el
> reranker degradó en silencio en esa consulta, eso solo explica el resultado**
> y no hace falta ningún cambio de arquitectura.
>
> El proyecto ya decidió que la degradación del reranker debe ser **explícita**
> —está especificado— pero conviene confirmar que en esa ejecución se cumplió.

- [ ] **Correr A1.1, A1.2 y A1.3 y documentar el resultado antes de empezar.**
      Si la causa es ingesta o degradación, el alcance se reduce drásticamente.

---

## A2 · Costos — ausentes, y «completo» es lo más caro propuesto hasta ahora

La solicitud menciona *«estimar unidades y costo aproximado»* en R5.4, pero no
da ningún número. Para este proyecto rige la regla de **cuantificar al
proponer**, no después.

### El orden de magnitud

| | Hoy (exploratorio) | «Completo» sobre el corpus actual |
|---|---:|---:|
| Evidencias enviadas al verificador | 25 | todas las elegibles |
| Corpus de referencia | — | **24.935 respuestas, 1.008 personas, 43 preguntas** |
| Unidades distintas (estimación, tras agrupar) | — | del orden de **miles** |
| Llamadas a Claude por criterio, en lotes de 25 | **1** | **cientos** |

**La diferencia no es de grado: es de dos órdenes de magnitud.** Y se multiplica
por cada criterio semántico de la consulta, y por cada ejecución.

A esto se suman el embedding del criterio (despreciable) y **el reranking de
todas las unidades**, que hoy se hace sobre 200 y pasaría a hacerse sobre el
universo.

- [ ] **Estimar el costo real de una ejecución completa sobre el corpus actual
      antes de implementar**, con el precio vigente de Claude y del reranker.
      Si una ejecución completa cuesta varios dólares, es una decisión de
      producto —quién puede lanzarla, cuántas veces— y no solo técnica.
- [ ] **Presupuesto obligatorio por ejecución**, no opcional: una evaluación
      completa lanzada por error sobre el panel entero debería frenarse sola.
- [ ] **Mostrar el costo estimado antes de confirmar**, como la solicitud ya
      insinúa. Para que sirva, tiene que estar en la pantalla, no en el log.
- [ ] Registrar el costo **real** consumido por ejecución, para calibrar la
      estimación.

> **Y una consecuencia de infraestructura:** recorrer todo el universo cambia el
> patrón de acceso al store semántico. Hoy corre en `db-f1-micro` (0,6 GB) y
> alcanza porque las consultas son top-k. Un recorrido completo con reranking
> puede requerir subir a `db-g1-small` (~US$ 24/mes más). Conviene medirlo en la
> prueba de carga, no descubrirlo en producción.

---

## A3 · Solapamiento con especificaciones ya existentes

El documento re-especifica cosas que **ya están especificadas o implementadas**
en este proyecto, sin referenciarlas. Si se implementa de cero, hay riesgo de
dos contratos distintos para lo mismo.

| Punto de la solicitud | Ya existe |
|---|---|
| `sin_verificar` como estado (R4.1) | Especificado en la spec de verificación por lotes, con su distinción de `dudoso` y su tratamiento en `consultas.py` |
| Verificación en lotes, truncamiento, subdivisión (implícito en R5.4) | Especificado por completo, con recuperación ante `stop_reason=max_tokens` |
| Diagnóstico del intercambio con Claude (sección 7) | Especificado como modo de depuración, con la decisión de guardarlo en el store semántico y no en logs |
| Trabajos persistentes, lotes, reanudación (R5.4) | Implementado para la ingesta diferida: mismo patrón, reutilizable |

- [ ] **Contrastar con las specs existentes antes de implementar** y reutilizar
      el contrato ya definido de `sin_verificar` en vez de introducir otro.
- [ ] Reutilizar el patrón de `ingesta_trabajo` / `ingesta_lote` para la
      ejecución completa: estados, cursores, idempotencia y reanudación ya están
      resueltos ahí.

---

## A4 · Los consumidores: COLOQUIO tiene un desajuste previo

La solicitud pide *«actualizar clientes que consuman los veredictos»*. Conviene
ser específico, porque **COLOQUIO hoy no muestra ninguna evidencia**, por un
desajuste anterior a este cambio:

`motor._evidencias()` lee `item["detalle"]` y `paneles` devuelve los criterios
en `item["criterios"]`. El resultado es que los veredictos nunca se ven del lado
de COLOQUIO.

- [ ] Arreglar ese desajuste **antes** de introducir estados nuevos: si no, los
      estados `irrelevante`, `posible` y `pendiente` se agregan a algo que no se
      muestra, y no hay forma de verificar que funcionen.
- [ ] La ausencia de un campo nuevo en una versión vieja de `paneles` debe
      leerse como **«no se sabe»**, no como un valor por defecto favorable. Un
      cliente que interpreta «sin dato» como «confirmada» convierte un cambio de
      contrato en un error silencioso.

---

## A5 · Precisiones menores

**El cambio de `input_type` altera el comportamiento de todas las consultas.**
Es correcto y está bien fundado, pero no es neutral: los vectores de consulta
van a caer en otro lugar del espacio. Conviene
- [ ] medir un conjunto de consultas conocidas **antes y después**, y
- [ ] poder volver atrás con configuración, sin redesplegar.

**«Irrelevante» corrige la agregación, no el recall.** Que 25 respuestas sobre
titularidad del contrato se juzguen irrelevantes evita el falso resultado, pero
**esos 25 lugares ya se gastaron**. El cambio 3 es el que ataca eso; conviene
que quede explícito que el 4 **solo** arregla la interpretación, para que
implementarlo no dé la sensación de que el problema quedó resuelto.

**El fixture de aceptación de 137 personas no prueba lo más difícil.** Prueba
exhaustividad y paginación, que son mecánicos. Conviene agregar un caso con
**evidencias contradictorias dentro de la misma persona** —un `cumple` y un
`no_cumple` pertinentes— porque ahí es donde las reglas de agregación de R4.2
pueden fallar sin que ningún conteo lo delate.

---

## A6 · Exponer `limite` en Parámetros — el trabajo se hace y se tira

**Esto es independiente de todo lo anterior y se puede hacer ya.** No espera a
ninguna decisión de alcance.

### Qué pasa hoy

El formulario de **Parámetros** de la pantalla de consultas ya deja ajustar
`top_n`, `top_k` y `umbral_distancia` por consulta. Falta el cuarto:

| Parámetro | En la API | En la pantalla | Qué controla |
|---|---|---|---|
| `top_n` | sí | **sí** (máx. 2000) | Respuestas recuperadas del pool |
| `top_k` | sí | **sí** (máx. 200) | **Personas que se verifican** |
| `umbral_distancia` | sí | **sí** | Confianza mínima |
| `limite` | sí (acepta hasta `TOP_K_MAXIMO`) | **no** | **Personas que se muestran** |

`consultas.py` lee `limite` y lo acota a `TOP_K_MAXIMO`; el recorte final está
en `items[: definicion["limite"]]`. Pero `consultas.js` **nunca lo envía**, así
que siempre se aplica el valor por defecto: **50**.

### Por qué importa

**El trabajo se hace y se descarta.** Si alguien sube `top_k` a 150 buscando más
resultados, el sistema recupera, rerankea y **verifica 150 personas** —con el
costo de Claude que eso implica, ahora en seis lotes— y después muestra 50. Las
100 restantes se pagaron y se tiraron.

Y desde la pantalla el efecto es invisible: subir `top_k` no cambia la cantidad
de resultados visibles, así que parece que el parámetro no hace nada.

### Qué se pide

- [ ] Agregar **`limite`** al formulario de Parámetros, junto a los otros tres.
- [ ] Etiquetarlo por lo que hace —**«Personas a mostrar»**—, igual que «Pool de
      recuperación» y «A verificar», que están bien nombrados.
- [ ] **Validar que `limite` no supere a `top_k`**: pedir 100 resultados con
      `top_k = 25` es pedir algo imposible, y hoy devolvería 25 sin explicar por
      qué. Avisar en el formulario, no al recibir el resultado.
- [ ] Mostrar, cuando el resultado quedó recortado, **cuántas personas se
      verificaron** frente a cuántas se muestran. Es la forma de que el recorte
      sea visible y no se confunda con «no hay más gente que cumpla».

### La señal de costo

- [ ] Una línea de ayuda bajo **«A verificar» (`top_k`)**: es la palanca directa
      sobre el gasto de la consulta. De 25 a 200 multiplica por ocho la
      verificación, que es la etapa más cara del pipeline; con lotes de 25, pasa
      de una llamada a Claude a ocho **por criterio**.

> **Por qué ahora y no junto al resto.** La solicitud propone renombrar estos
> parámetros como *presupuestos* separados de `page_size`, y es una mejora
> conceptual real. Pero exponer `limite` **no depende de eso**: el backend ya lo
> acepta, el formulario ya existe, y mientras tanto hay consultas pagando
> verificación que nadie ve. Si después se renombran, se renombran los cuatro
> juntos.

---

## Qué haría yo, en orden

1. **A1 completo** (tres consultas, una tarde). Documentar el resultado.
2. **A2**: estimar el costo de una ejecución completa con números reales.
3. Con eso en la mano, **decidir el alcance**. Es posible que la causa sea
   ingesta o degradación del reranker, y que el trabajo se reduzca a los
   cambios 1 y 4 —que son baratos, correctos y valen por sí mismos—.
4. Si se confirma que hace falta, **cambios 3 y 5**, con el presupuesto y la
   estimación de costo como requisitos de primera clase y no como detalle de
   R5.4.

**A6 va aparte de esa secuencia:** es media hora de trabajo, no depende de
ninguna decisión y evita que se siga pagando verificación que no se muestra.
