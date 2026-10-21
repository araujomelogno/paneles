# BUG — La ingesta de una carga falla siempre en el mismo texto: `respuesta.embedding` nulo

**Sistema:** Gestión de paneles · ingesta diferida · R-UE (unidades de evidencia)
**Severidad:** Alta — la carga 28 tiene **910 personas creadas y ninguna respuesta**
**Detectado:** 2026-10-21
**Estado:** Para reproducir y corregir

---

## 1. El síntoma

Trabajo de ingesta **11**, destino `carga` **28**: el lote 0 falla en sus **5
intentos** y el trabajo queda `fallida`. Las 910 personas se crearon bien, con
`contacto_participacion` y `uso_semantico` vigentes, pero **no se escribió ninguna
respuesta** en el store semántico.

```
Unhandled error: null value in column "embedding" of relation "respuesta"
violates not-null constraint
```

## 2. La evidencia

### 2.1 Los tres últimos intentos

| Hora (UTC) | `respuesta.id` | `individuo_id` | `pregunta_id` | `unidad_id` | huella |
|---|---:|---:|---:|---:|---|
| 15:56:34 | 58081 | 6403 | 361 | 3352 | `0df92b48…` |
| 15:57:33 | 58802 | 7313 | 400 | 4443 | `0df92b48…` |
| 15:59:12 | 59523 | 8223 | 439 | 5534 | `0df92b48…` |

Huella completa: `0df92b48744c69039279b347bec6d63445f7865886db8977969232e4a9bcb974`.
Texto: *«¿Cuál es tu área de formación o carrera universitaria? → Educación»*
(el `→ E...` del mensaje es el recorte que hace Postgres al mostrar el error, no
el texto guardado).

**Lo que dicen estos números:**

- **Siempre el mismo texto.** No es una falla general: es algo de esta huella.
- **El `respuesta.id` salta 721 entre intentos.** En cada intento se insertaron
  unas **720 respuestas sin problema** antes de llegar a esta —el trigger de la
  0010 les completó el vector—, y todo se deshizo al fallar.
- **El `unidad_id` salta ~1.091 entre intentos.** Cada intento crea las unidades
  de los ~1.091 textos nuevos de la carga y las pierde con el rollback. La unidad
  5534 **no existe hoy**: se creó dentro de la operación fallida.

### 2.2 Lo que se descartó

| Hipótesis | Cómo se descartó |
|---|---|
| Falta la migración 0010 | `verificar_esquema.py`: las dos bases al día, `0010_unidad_evidencia_vector.sql` ✓ |
| El trigger instalado es otro | `pg_get_functiondef('respuesta_asegurar_unidad')` es **idéntico** al de la 0010 |
| La 0010 se aplicó después de los intentos | Cae por §2.1: ~720 respuestas por intento pasaron por el trigger y se insertaron bien |
| Quedaron unidades sin vector de antes | `select count(*) from unidad_evidencia where embedding is null` → **0** |
| Consentimiento | 910 personas con las dos finalidades vigentes |
| La cola no despacha | Hubo 5 intentos; la cola quedó vacía porque se agotó `maxAttempts` |
| Unidades y respuestas por conexiones distintas | `escribir_respuestas` usa la misma `conn` para `crear_unidades` y `upsert_respuestas` |
| `_vector` produce un nulo | No puede: con `None` falla con `TypeError` |

### 2.3 Lo que el código dice que **no** debería pasar

- `escribir_respuestas`: los textos nuevos se embeben por sub-lotes y
  `crear_unidades` los inserta **con** vector.
- `upsert_respuestas`: inserta la respuesta con `unidad_id` y **sin** `embedding`;
  el trigger BEFORE de la 0010 hace `select embedding into new.embedding from
  unidad_evidencia where id = new.unidad_id`.
- El camino `on conflict (individuo_id, pregunta_id) do update` también deja el
  vector completo, según el trigger.

**Y sin embargo, para esta huella, el vector llega vacío.** Las explicaciones que
quedan requieren ejecutar el código para verificarlas. Candidatas, sin orden de
probabilidad:

1. **`select … into` sin filas.** Si `unidad_de[hash]` apunta a una unidad que la
   respuesta no ve, `new.embedding` queda nulo sin error. Verificar el `unidad_id`
   que efectivamente se pasa y si esa unidad existe en ese instante.
2. **Dos filas para la misma `(individuo_id, pregunta_id)`** en el lote —por dedup
   que fusionó dos filas del archivo en una persona, o por la fusión de una
   cerrada con su «Otro, especificar» (Fase 8)—, combinadas con algún caso del
   trigger en la rama `UPDATE` que no esté contemplado.
3. **El proveedor devolvió un vector incompleto o vacío** para esta entrada y algo
   en `zip(sublote, vectores)` o en `crear_unidades` lo absorbió.

## 3. Qué se pide

- [ ] **Reproducirlo** con el SAV de la carga 28 y el mismo mapeo (está en el plan
      del trabajo 11), en un entorno de pruebas.
- [ ] **Encontrar la causa** y corregirla.
- [ ] **Salvaguarda**, aparte de la causa: antes de insertar cada respuesta,
      verificar que su unidad **existe y tiene vector**, y si no, fallar con un
      mensaje que nombre la huella y la pregunta. Hoy el error es una violación de
      `not null` sin contexto, que obligó a reconstruirlo a mano.
- [ ] **Registrar en el log del lote**, al fallar, la huella y el código de
      pregunta del texto problemático, además del error de Postgres.
- [ ] Una vez corregido, **reintentar el trabajo 11** y verificar que la carga 28
      queda con sus respuestas.

## 4. Definition of Done

- [ ] El SAV de la carga 28 ingesta completo en el entorno de pruebas (test de
      regresión con ese caso).
- [ ] Una respuesta cuya unidad no tenga vector produce un error **explícito**, con
      huella y pregunta, no una violación de `not null` (test).
- [ ] El trabajo 11 reintentado en producción termina `ok`, y las personas de la
      carga 28 tienen respuestas procesadas.

## 5. Costos

**US$ 0** de infraestructura. Reintentar la carga 28 embebe sus ~1.091 textos
nuevos: **fracciones de dólar**.

## 6. Nota

Se tardó en llegar acá porque el error no decía **qué** texto ni **por qué**: solo
«violates not-null constraint». La salvaguarda de §3 es la que más rinde para la
próxima vez, más allá de este caso.
