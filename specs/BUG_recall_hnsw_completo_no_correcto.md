# BUG — El recall por índice devuelve la cantidad pedida, pero no las filas correctas

**Sistema:** Gestión de paneles · `functions/panel_api/semantica.py` · `_vecinos`
**Severidad:** Alta — **las consultas semánticas puras pueden perder la mejor
evidencia del corpus**, sin ningún aviso
**Detectado:** 2026-10-10, desplegando el arreglo de A1.2
**Antecedente:** `specs/BUG_diagnostico_a1_distancia_y_hallazgo.md`
**Estado:** Para corregir

---

## 1. El hallazgo

Con el script de diagnóstico ya corregido, el criterio *«gente que usa un
celular xiaomi»* devuelve:

```
[query] 218 respuesta(s) con «xiaomi»; la mejor (#41197, 'Xiaomi') está a 0.3712;
        0 respuesta(s) del corpus están más cerca del criterio
        recall real (top_n=200): 200 respuesta(s); NO entra la mejor con «xiaomi».

[document] 218 respuesta(s) con «xiaomi»; la mejor (#51159, 'Xiaomi') está a 0.1384;
        0 respuesta(s) del corpus están más cerca del criterio
        recall real (top_n=200): 200 respuesta(s); NO entra la mejor con «xiaomi».
```

**La respuesta de Xiaomi es la más cercana de todo el corpus** —ninguna otra
está más cerca del criterio— **y no entra entre las 200 que trae el recall.**
Con los dos `input_type`.

> **Confirmado con el script corregido.** La salida corresponde a
> `scripts/diagnosticar_consulta.py` en el commit `9b24e78` —*«Bug A1.2: el
> índice vectorial no sabe de filtros (D71)»*—, es decir, **con el arreglo de
> A1.2 ya aplicado**, y termina con `salida: 2`. El documento de despliegue de
> D71 indica, para ese caso: *«si es el de esta rama, abrir un bug con la salida
> completa: **no** interpretarlo como "no está"»*. **Este es ese bug.**
>
> Nota: el paso del despliegue que compara el script «viejo» contra el nuevo
> extrae el viejo de `origin/main`, que **ya contiene D71**. Las dos salidas
> salieron idénticas por eso: la comparación no estaba comparando nada. Conviene
> que ese paso tome el script de un commit anterior a D71.

## 2. Qué ya está bien, y qué no cubre

El arreglo de A1.2 ya está en `_vecinos`, y resuelve dos trampas reales:

- **Con filtro de personas** (el gate de consentimiento, la estrategia
  «demográfico primero»): recorrido exacto sobre el conjunto filtrado. ✅
- **Sin filtro, con `top_n` mayor que `ef_search`**: sube `ef_search` a
  `top_n` para la transacción. ✅
- **Si el índice devuelve menos filas de las pedidas**: lo registra y
  recalcula exacto. ✅

Lo que **no** cubre:

```python
if len(cercanas) < top_n:
    …                       # se recalcula exacto
return cercanas             # ← 200 de 200: se acepta tal cual
```

El control detecta un recall **corto**. Este recall **no es corto**: devolvió
exactamente las 200 filas pedidas. El problema es que **no eran las 200 más
cercanas**: HNSW es aproximado, y puede devolver la cantidad justa dejando afuera
al vecino más cercano. Ninguna cuenta lo detecta.

> **Completo no es lo mismo que correcto.** El arreglo anterior trató el
> síntoma visible —menos filas de las esperadas—. Este es el mismo defecto de
> fondo sin síntoma visible: la cantidad está bien, el contenido no.

## 3. Cuándo pasa

Solo en la rama **`plan = "indice"`**: consultas **semánticas puras**, sin
filtro de personas. Con filtro demográfico la consulta ya va por el camino
exacto y encuentra bien.

Es decir: el caso más afectado es justamente el más simple —«buscame gente que
X»—, que es el primero que prueba cualquier analista.

## 4. Qué hay que corregir

### Recomendado: camino exacto mientras el corpus no justifique el índice

Con **21.340 respuestas de 512 dimensiones**, una búsqueda exacta recorre del
orden de diez millones de operaciones en coma flotante: **decenas de
milisegundos** en Postgres. A esa escala, HNSW no ahorra nada perceptible y sí
introduce resultados equivocados.

- [ ] En `_vecinos`, usar el **camino exacto también sin filtro** mientras el
      corpus esté por debajo de un umbral configurable.
- [ ] El umbral, por variable de entorno, con un valor inicial alto (sugerido:
      **200.000 respuestas**), y documentado.
- [ ] Por encima del umbral, volver al índice — ver la segunda parte.
- [ ] El `informe` sigue indicando qué plan se usó, como hoy.

> **El umbral no es arbitrario.** Por debajo de unos cientos de miles de filas,
> el recorrido exacto es rápido y da recall perfecto. El índice existe para
> cuando ya no lo es. Hoy el corpus está dos órdenes de magnitud por debajo de
> ese punto.

### Para cuando el corpus crezca: el índice, con más margen

- [ ] Por encima del umbral, subir `ef_search` **por encima de `top_n`**, no
      igualarlo: un `ef_search` igual a `top_n` deja muy poco margen para que el
      vecino real entre. Sugerido: **`2 × top_n`**, con un tope.
- [ ] Verificar los parámetros con que se construyó el índice (`m`,
      `ef_construction`). La migración `0006` lo recreó sobre una tabla con una
      sola fila; vale confirmar que no conviene reconstruirlo ahora que el
      corpus tiene volumen real.

### Una salvaguarda contra la recaída

- [ ] Agregar al script de diagnóstico, o a la suite, un **chequeo de recall
      real**: para un conjunto de criterios de prueba, comparar el resultado del
      plan con índice contra el exacto y reportar cuántos de los primeros N del
      exacto faltan en el índice. Es la única forma de detectar este defecto,
      porque **no se ve en las cuentas**.

## 5. Lo que esto cambia en la conclusión anterior

En `BUG_diagnostico_a1_distancia_y_hallazgo.md` se concluyó que la causa del
problema original era **el recorte por repetición** (cambio 3 de la solicitud).
**Con este dato, esa conclusión no se sostiene para el caso de Xiaomi**: el
recorte ocurre **después** del recall, y acá la evidencia **ni siquiera entra
al recall**.

El propio script reporta las dos cosas a la vez y se contradice:

```
→ FALLO DE MEDICIÓN del recall: por distancia tendría que entrar y el recall
  no la trajo (índice). No es un resultado.
→ Más cerca que las repeticiones: si no llegó, fue el RECORTE (cambio 3
  justificado).
```

La primera es la correcta. La segunda es una regla condicional que **no aplica
cuando la evidencia no entró al recall**.

- [ ] Corregir el script para que **no emita la conclusión del recorte cuando
      la evidencia no entró al recall**: son excluyentes, y hoy saca las dos.
- [ ] Ajustar la etiqueta «FALLO DE MEDICIÓN»: si el recall de producción usa el
      mismo plan, **no es solo una falla de la medición, es el comportamiento
      real del sistema** para las consultas sin filtro.

### Y un dato que no cierra con la hipótesis original

El chequeo `--contra titular` encontró **una sola** respuesta, no 25. La
hipótesis del recorte se apoyaba en que 25 respuestas repetidas sobre la
titularidad del contrato ocupaban el top. Ese patrón no las encuentra.

- [ ] Revisar qué eran realmente las respuestas repetidas del resultado
      original, antes de seguir invirtiendo en el cambio 3. **Puede que el
      cambio 3 siga valiendo por otras razones, pero no está demostrado por
      este caso.**

## 6. Pruebas de aceptación

- [ ] *«gente que usa un celular xiaomi»*, sin filtro: la mejor respuesta de
      Xiaomi **entra en el recall** (test con el corpus real o con un fixture
      que reproduzca el caso).
- [ ] Con el corpus por debajo del umbral, el plan es `exacto` y el recall
      coincide con el de una búsqueda exhaustiva (test).
- [ ] Por encima del umbral, el plan es `indice` con `ef_search > top_n`
      (test).
- [ ] El chequeo de recall real reporta las filas que el índice omite frente
      al exacto (test).
- [ ] El script no emite la conclusión del recorte cuando la evidencia no entró
      al recall (test).
- [ ] Las consultas con filtro siguen por el camino exacto, sin cambios (test de
      no regresión).

## 7. Costos

| Concepto | Efecto |
|---|---|
| Camino exacto sin filtro | **US$ 0**. Más latencia en el recall: del orden de decenas de milisegundos con el corpus actual. Imperceptible frente a la verificación con Claude, que tarda segundos. |
| Instancia | **Sin cambios a este volumen.** El recorrido exacto usa CPU, no memoria del índice. A vigilar si el corpus crece mucho en `db-f1-micro` (ver `COSTOS.md` §4). |
| Embeddings y Claude | **Sin cambios**: se embebe el mismo criterio y se verifica la misma cantidad. |

> **Lo que se gana es calidad, no se paga en dinero.** Hoy el sistema puede
> dejar afuera la mejor evidencia y verificar en su lugar candidatos peores —que
> además se pagan—. Con el camino exacto, lo que llega a Claude es lo que tiene
> que llegar.

## 8. Nota de método

Es la cuarta vez en este proyecto que un defecto **no falla: devuelve algo
plausible**. La API key se veía como contraseña incorrecta; el truncamiento como
veredictos `dudoso`; el plan de ejecución como «esa respuesta no existe». Y ahora
un recall completo pero equivocado como un resultado normal.

La salvaguarda que más rinde contra este tipo de defecto no es revisar el
código: es **comparar contra una referencia conocida**. Por eso el chequeo de
recall real de §4 no es un detalle.
