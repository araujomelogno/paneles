# Despliegue — bug del embedding nulo (carga 28)

**Bug:** `docs/BUG_ingesta_embedding_nulo_un_texto.md` · **Decisión:** D79
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Qué cambia:** solo código del backend (`semantica.upsert_respuestas`).
**Qué NO cambia:** ninguna migración, secreto, permiso ni pantalla.

---

## La causa, en corto

Hay tres hechos que se combinan:

1. **El lote traía dos respuestas de la misma persona a la misma pregunta**,
   con textos distintos. Son dos filas del archivo que el dedup fusionó en
   una persona.
2. **Las respuestas se escribían de a una.** La segunda de esas dos era un
   `UPDATE` que dejaba sin respuestas la unidad de evidencia del primer
   texto.
3. **El trigger de la `semantica/0010` borra las unidades huérfanas al final
   de cada sentencia.** Por eso esa unidad se borraba **a mitad del lote**.

Más adelante en el mismo lote, otra persona tenía ese mismo texto
(«… → Educación»). Su respuesta apuntaba a la unidad ya borrada y el vector
quedaba nulo. Pasaba en los cinco intentos, siempre con la misma huella.

Se reprodujo en pruebas con la misma huella, `0df92b48…`.

**La corrección** tiene tres partes:

- Las respuestas de un lote se escriben **en una sola sentencia**. Así el
  trigger corre cuando todas ya apuntan a su unidad.
- Si llegan dos respuestas de la misma persona a la misma pregunta, **queda
  la última**, como antes. La otra se descarta antes de embeber.
- **Salvaguarda:** si una respuesta va a apuntar a una unidad sin vector, el
  error nombra la huella y el código de pregunta, en el lote y en el log.

## Paso 1 · Desplegar

```bash
firebase deploy --only functions
```

No hay migraciones. El hosting no cambia.

## Paso 2 · Reintentar el trabajo 11 (carga 28)

El lote 0 quedó `fallido`. Un lote fallido **conserva sus filas**: la purga
no lo toca, así que no hace falta volver a subir el archivo.

Antes de reintentar, conviene confirmar el estado:

```sql
-- En la bóveda.
select t.id, t.estado, l.indice, l.estado as lote, l.intentos,
       l.filas is not null as tiene_filas, left(l.error, 120) as error
  from ingesta_trabajo t join ingesta_lote l on l.trabajo_id = t.id
 where t.id = 11 order by l.indice;
```

Tiene que figurar `tiene_filas = true` en el lote fallido. Después hay que
reintentar, desde la app, logueado con un rol que pueda ingestar:

1. Abrir la consola del navegador: F12 → Console.
2. Pegar:

   ```js
   (await import('/js/api.js')).ingestas.reintentar(11)
   ```

3. Tiene que devolver `reencolados: [0]`. El avance se ve con:

   ```js
   (await import('/js/api.js')).ingestas.ver(11)
   ```

   Esperar a que `estado` sea `terminada`.

Si `tiene_filas` diera `false`, no se puede reintentar. En ese caso hay que
volver a cargar el archivo de la carga 28 desde Panelistas → *Cargar
panelistas*, con el mismo mapeo del plan del trabajo 11
(`select plan from ingesta_trabajo where id = 11`). Las 910 personas ya
existen: el dedup las reutiliza y no se duplican.

## Paso 3 · Comprobar

En la bóveda:

```sql
select count(*) as personas from persona_carga where carga_id = 28;   -- 910
```

En el semántico, el `ref_estudio` sale de
`select ref_estudio from carga where id = 28`:

```sql
select count(*) as respuestas,
       count(*) filter (where r.embedding is null) as sin_vector   -- 0
  from respuesta r
  join pregunta p on p.id = r.pregunta_id
  join cuestionario c on c.id = p.cuestionario_id
 where c.ref_estudio = '<ref_estudio>';
```

Después, en la ficha de un panelista de la carga 28, *Respuestas
procesadas* tiene que mostrar sus respuestas.

En el log de la función de tareas:

```bash
gcloud logging read 'textPayload:"[ingesta]"' --limit 20 \
  --project gestion-paneles --format 'value(textPayload)'
```

Lo esperable es una línea `… respuesta(s) repetidas para la misma persona y
pregunta: queda la última`. Esa línea nombra lo que causó el bug: filas del
archivo que el dedup juntó. No es un error.

Si en cambio aparece `respuesta sin vector: huella=… pregunta=…`, es la
salvaguarda. El error del lote dice la huella y la pregunta. Con eso se
encuentra el texto:

```sql
select texto_embebido from respuesta where hash_texto = '<huella>' limit 1;
```

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q tests/test_bug_embedding_nulo_un_texto.py
```

Son 6 pruebas. Las cuatro primeras de esta lista fallaban antes de la
corrección con el mismo error de producción, sobre la misma huella:

- dos respuestas de una persona y el mismo texto en otra;
- la re-ingesta en la que otra persona da el texto que alguien dejó;
- una carga de punta a punta con una persona repetida en el archivo;
- el error explícito con huella y pregunta.

Las otras dos:

- la unidad que de verdad queda huérfana se sigue borrando;
- si Postgres rechaza igual, el error se traduce y la transacción queda
  usable.

## Volver atrás

Volver a desplegar el código anterior. No hay nada en la base que revertir.
Con el código anterior, una carga con personas repetidas en el archivo
vuelve a fallar.
