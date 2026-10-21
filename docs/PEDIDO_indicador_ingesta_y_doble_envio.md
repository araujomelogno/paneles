# PEDIDO — Indicador de procesamiento al ingestar, guarda contra el doble envío, y la fecha del estudio

**Sistema:** Gestión de paneles · carga de panelistas · paso de revisión
**Severidad:** Media — la parte visible es de experiencia de uso; la de fondo
puede **duplicar una ingesta**
**Detectado:** 2026-10-20
**Estado:** Para corregir

---

## 1. Qué pasa

Después de mapear las variables, al apretar **«Ingestar»**:

- El **modal de revisión queda abierto**, sin ningún cambio visible.
- El sistema **sí está trabajando** —preparando la carga antes de mostrar el
  resumen—, pero nada lo indica.
- Quien lo usa no puede distinguir «está procesando» de «no hizo nada», y lo
  natural es **volver a apretar el botón**.

## 2. Por qué no es solo estético

Verificado en el esquema: el índice sobre trabajos abiertos

```sql
create index ingesta_trabajo_abiertos on ingesta_trabajo (creado_en desc)
 where estado in ('encolada', 'procesando');
```

**no es único**: sirve para *listar* trabajos en curso, no para *impedir* dos.
**No hay nada que evite encolar una segunda ingesta para la misma carga**
mientras la primera está en curso.

Así que un segundo clic, en el estado actual de la pantalla, puede:

- crear **dos trabajos** para la misma carga, que procesan lo mismo en paralelo;
- duplicar trabajo y consumo (embeddings de textos nuevos, cálculo de temáticas
  por panelista);
- dejar un historial de cargas confuso, con dos ejecuciones casi simultáneas.

La ingesta es idempotente —no duplica respuestas ni personas—, así que **el
resultado final no se corrompe**. Pero se paga y se procesa dos veces, y nadie
se entera.

> **El modal sin respuesta es la causa; la ingesta duplicada es la
> consecuencia.** Arreglar solo el aviso deja el riesgo abierto ante un doble
> clic rápido o dos pestañas; arreglar solo la guarda deja al usuario sin saber
> qué pasa. Hacen falta los dos.

---

## 3. Qué se pide

### R-ING.1 — Aviso de procesamiento (P0)

- Dado el clic en «Ingestar», entonces **en el momento** —antes de que responda
  el servidor— el modal pasa a un estado de **procesando**: un indicador visible
  y un mensaje del estilo *«Preparando la carga… puede tardar unos segundos con
  bases grandes. No cierres esta ventana.»*
- Dado ese estado, entonces el botón **«Ingestar» queda deshabilitado**, y
  también los controles de mapeo: no se puede modificar la configuración
  mientras se envía.
- Dada la respuesta exitosa, entonces el modal **se cierra** y la pantalla pasa
  al **seguimiento de la ingesta** que ya existe (progreso por lotes, R-ASYNC.3).
- Dado un **error**, entonces el modal sale del estado de procesando, **muestra
  el motivo** y vuelve a habilitar el botón, para poder corregir y reintentar.
- [ ] Si la preparación tarda más de unos segundos, el mensaje lo dice —*«sigue
      procesando»*— en vez de quedar igual, para que no parezca colgado.

### R-ING.2 — Una sola ingesta en curso por carga (P0)

La guarda tiene que estar **en la base**, no solo en la pantalla: un doble clic
muy rápido o dos pestañas abiertas pasan por encima de cualquier control del
navegador.

- Dada una carga con una ingesta **encolada o procesando**, entonces un segundo
  pedido de ingesta para esa misma carga **se rechaza**.
- Dado el rechazo, entonces la respuesta **dice que ya hay una en curso** y
  **lleva a su seguimiento**, en vez de mostrar un error genérico.
- [ ] Implementarlo como un **índice único parcial**:

  ```sql
  create unique index ingesta_trabajo_uno_activo_por_destino
      on ingesta_trabajo (destino_tipo, destino_id)
   where estado in ('encolada', 'procesando');
  ```

  Así la base lo garantiza aunque dos pedidos lleguen al mismo tiempo.
- [ ] Una ingesta **terminada o fallida** no bloquea una nueva: reintentar
      después de un fallo tiene que seguir siendo posible.

> **Antes de aplicar el índice**, verificar que no haya ya dos trabajos activos
> para el mismo destino, porque el índice no se podría crear:
>
> ```sql
> select destino_tipo, destino_id, count(*)
>   from ingesta_trabajo
>  where estado in ('encolada', 'procesando')
>  group by 1, 2 having count(*) > 1;
> ```
>
> Si aparece alguno —por ejemplo, un trabajo colgado de los episodios de cola
> pausada—, resolverlo antes.

### R-ING.3 — Medir por qué tarda la preparación (P1)

La ingesta procesa en diferido (Cloud Tasks), así que el pedido de «Ingestar»
debería responder rápido: crear el trabajo, partir en lotes, encolar. Si tarda
lo suficiente como para que se note, algo de la preparación sigue siendo
sincrónico y pesado.

- [ ] Registrar en el diagnóstico cuánto tarda cada parte del pedido: guardar el
      plan, despivotar, persistir lotes, encolar.
- [ ] Si alguna parte crece con el tamaño de la base y se acerca al tiempo
      límite de la función, **pasarla también a la vía diferida**. Un aviso de
      «procesando» no arregla un pedido que puede cortarse por timeout.

---

## 4. Definition of Done

- [ ] Al apretar «Ingestar», el modal muestra el estado de procesando **antes**
      de que responda el servidor (test).
- [ ] Durante el procesamiento, el botón y el mapeo están deshabilitados (test).
- [ ] Una respuesta exitosa cierra el modal y lleva al seguimiento de la
      ingesta (test).
- [ ] Un error muestra el motivo y vuelve a habilitar el botón (test).
- [ ] Un segundo pedido para una carga con ingesta activa se rechaza con un
      mensaje que lleva a la existente (test).
- [ ] Dos pedidos **simultáneos** para la misma carga producen **un solo**
      trabajo (test de concurrencia).
- [ ] Una carga con su ingesta terminada o fallida admite una nueva (test).
- [ ] El diagnóstico registra cuánto tarda cada parte de la preparación.

## 5. Costos

**US$ 0** de infraestructura.

> **Lo que se evita pagar:** una ingesta duplicada procesa todo dos veces. Con
> unidades de evidencia, los textos ya conocidos no se vuelven a embeber, así
> que el costo directo es bajo; pero el procesamiento, el cálculo de temáticas y
> la carga sobre la base se repiten. La guarda lo elimina.


---

# Además — La fecha del estudio no llega al cuestionario

**Severidad:** Media — se pierde un dato que el usuario cargó, y con él el
anclaje temporal de las respuestas
**Detectado:** 2026-10-20

## A1. Qué pasa

Al dar de alta una carga se completa la **fecha del estudio**. Después, en la
ficha del panelista, la sección «Respuestas procesadas» muestra **«sin fecha de
campo»** en todas las respuestas de esa carga.

## A2. La causa

Son dos campos, en dos bases, con nombres distintos:

| Dónde | Campo | Qué tiene |
|---|---|---|
| Bóveda · `carga` | `fecha_estudio` | la fecha que cargó el usuario ✅ |
| Semántica · `cuestionario` | `fecha_campo` | vacía ❌ — es la que muestra la ficha |

La ingesta crea el cuestionario así (`ingesta.py`):

```python
cuestionario_id = semantica.asegurar_cuestionario(
    conn_semantica, ref_estudio, encuesta["nombre"],
    encuesta.get("fecha_campo"),          # ← nombre del campo en las ENCUESTAS
    ...
```

La función se escribió para las **encuestas**, que guardan la fecha como
`fecha_campo`. Cuando el destino es una **carga**, el campo se llama
`fecha_estudio` (R-ORG.4), así que `encuesta.get("fecha_campo")` devuelve
`None` y el cuestionario se crea sin fecha.

> **`asegurar_cuestionario` está bien.** Ante un cuestionario existente ya hace
> `fecha_campo = coalesce(excluded.fecha_campo, cuestionario.fecha_campo)`: si
> recibe una fecha, la completa. El único problema es que, para las cargas,
> nunca la recibe.

## A3. Por qué importa más allá de la pantalla

`cuestionario.fecha_campo` es lo que **ancla las respuestas en el tiempo**. Sin
ella:

- No se pueden mostrar los atributos **al momento de la respuesta** (R4.1.a):
  por ejemplo, qué edad tenía la persona cuando contestó.
- Las **series longitudinales** (R4.1.b) no tienen cómo ordenar las mediciones.

## A4. Qué se pide

- [ ] En la ingesta, cuando el destino es una **carga**, pasar su
      `fecha_estudio` como fecha del cuestionario. Cuando es una encuesta,
      seguir pasando `fecha_campo`. Conviene resolverlo en un solo lugar —una
      función que devuelva la fecha del destino según su tipo— en vez de un
      `if` dentro de la ingesta.
- [ ] Al **editar** la fecha del estudio de una carga ya ingestada, propagarla a
      su cuestionario en el store semántico. Hoy la edición queda solo en la
      bóveda.
- [ ] **Completar las cargas ya ingestadas**: copiar `carga.fecha_estudio` a
      `cuestionario.fecha_campo` por `ref_estudio`, **solo donde esté vacía**.
      Es una operación única, entre las dos bases.

> **Revisar el mismo patrón en el resto de la ingesta.** El problema es que una
> función escrita para encuestas se reutilizó para cargas leyendo campos por
> nombre. Si hay otros `encuesta.get(...)` o `encuesta["..."]` en ese camino,
> cada uno es un candidato a devolver vacío cuando el destino es una carga.

**Mientras tanto**, lo ya cargado se puede completar a mano:

```bash
psql "$DSN_BOVEDA" -At -F' ' -c "
  select ref_estudio, fecha_estudio from carga where fecha_estudio is not null" |
while read ref fecha; do
  psql "$DSN_SEMANTICA" -c "
    update cuestionario set fecha_campo = '$fecha'
     where ref_estudio = '$ref' and fecha_campo is null"
done
```

## A5. Definition of Done

- [ ] Una carga ingestada con fecha del estudio deja esa fecha en su
      cuestionario (test).
- [ ] Una encuesta sigue dejando su `fecha_campo` igual que antes (test de no
      regresión).
- [ ] Editar la fecha del estudio de una carga actualiza su cuestionario (test).
- [ ] La ficha del panelista muestra la fecha en las respuestas de esa carga.
- [ ] Las cargas anteriores quedan con la fecha completada, sin pisar fechas ya
      existentes (test).
- [ ] No queda ningún otro campo de la ingesta que se lea con el nombre de la
      encuesta cuando el destino es una carga.

## A6. Costos

**US$ 0.** Lógica de la ingesta y una actualización puntual.
