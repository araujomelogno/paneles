# Despliegue — celular como identificador, falso positivo de datos personales y borrar una carga

**Pedido:** `docs/PEDIDO_celular_identificador_y_falso_positivo_pii.md` ·
**Decisión:** D78
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0029`** (una ingesta activa
por destino, PR #66). El borrado de una carga se apoya en esa guarda.
**Qué cambia:** una migración aditiva en la bóveda (`0030`) y código de
backend y de frontend. El store semántico no cambia de esquema.
**Qué NO cambia:** ningún secreto, rol de base, permiso de IAM, cola ni
configuración de función. El borrado usa la cola de la ingesta
(`procesaringesta`) que ya existe.
**Permiso de aplicación nuevo:** `borrar_carga`, **solo admin**, en `auth.py`.
**Variable nueva, opcional:** `BORRADO_PERSONAS_POR_LOTE` (cuántas personas
procesa cada lote del borrado; por defecto 500). No es un secreto.
**COLOQUIO:** nada que coordinar. La tabla nueva no tiene `grant`; las bajas
del borrado le llegan como `borrado_pendiente`, igual que cualquier baja.

### Orden en una línea

`0030` → verificar esquema → desplegar → comprobar.

---

## Qué trae

### 1 · «Trae el celular» como identificador

En el paso 2 de una carga o ingesta, el selector ofrece **Trae el celular**.
En modo «los panelistas ya existen», cada fila se busca por
`persona.celular` en formato internacional, así que da igual cómo se haya
escrito el número. Se aplican las mismas cautelas que en el dedup:

| Caso | Motivo en el resultado |
|---|---|
| El número lo tiene una sola persona | Se asigna a esa persona |
| Lo tiene más de una persona | No se asigna a ninguna: `celular_compartido` |
| Nadie lo tiene | `no_encontrado` |
| La celda no es un número («solo WhatsApp 095…», «....», «1») | `formato_invalido` |

A diferencia del documento y el correo, el celular **no** registra el alias
de la plataforma. Así cada carga vuelve a comprobar que el número siga
teniendo una sola titular.

### 2 · El detector de datos personales mira solo lo que se ingesta

La columna que identifica al respondente y las variables marcadas como
demográficas o de identidad y contacto ya no generan «Posibles datos
personales», porque nunca llegan al store semántico. Esto vale en la
revisión, que además lista esa columna entre las excluidas con su motivo, y
en el panel de calidad del modal. Cambiar la columna o la marca vuelve a
filtrar el panel. Las preguntas abiertas que sí van al store semántico se
siguen revisando.

### 3 · Borrar una carga

Desde el detalle de la carga, **Borrar la carga…**, solo para admin:

- **Vista previa** con los números: cuántas personas se dan de baja, cuántas
  se conservan y por qué, y cuántas respuestas y textos embebidos se borran.
- **Para confirmar** hay que escribir el nombre exacto de la carga (lo
  comprueba el servidor). El texto dice que es irreversible y recomienda
  tener un respaldo.
- **Qué se borra:**
  - Todas las respuestas del estudio y su cuestionario.
  - Las unidades de evidencia que quedan sin respuestas. Las compartidas con
    otro estudio se conservan.
  - Las personas **creadas** por la carga **sin otra vida** en el sistema:
    sin otra carga, panel, participación, puntos, canjes ni respuestas de
    otro estudio. Se dan de baja por `bajas.retirar`, así que generan el
    `borrado_pendiente` para COLOQUIO, y su lápida dice `borrado_de_carga`.
- **Qué se conserva:** las demás personas, sin el vínculo con la carga ni sus
  respuestas de ella.
- **Cómo corre:** por la vía diferida, con avance visible. No se puede borrar
  mientras hay una ingesta en curso, ni ingestar mientras se borra. Si se
  corta, volver a pedir el borrado lo reanuda.
- **Auditoría:** cada borrado queda en `carga_borrado`, con quién, cuándo, la
  vista previa y los conteos. Sobrevive a la carga.

### Rutas nuevas o cambiadas

| Ruta | Permiso | Qué hace |
|---|---|---|
| `POST /cargas/<id>/borrado` | `borrar_carga` | Con `solo_revisar: true`, la vista previa. Con `confirmacion` (el nombre exacto), lanza el borrado: `202` con el trabajo, o `200` si no había personas. Con un borrado a medias, lo reanuda. Con una ingesta en curso, `409 ingesta_en_curso` |
| `POST /cargas/<id>/ingesta`, `POST /encuestas/<id>/ingesta` | `ingestar` | Aceptan `tipo_identificador: "celular"`. La revisión excluye del diagnóstico la columna de identidad |
| `GET /ingestas/<id>` | `leer` | Un trabajo de borrado se informa con `operacion: "borrado_carga"` |

---

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

## Paso 2 · Aplicar `boveda/0030` (antes de desplegar el código)

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0030_borrado_de_carga.sql
```

Crea la tabla `carga_borrado` (con RLS y sin `grant`) y el índice que deja un
solo borrado en curso por carga. Salida en el ensayo:

```
BEGIN
CREATE TABLE
COMMENT
CREATE INDEX
ALTER TABLE
REVOKE
COMMIT
```

Se puede correr dos veces. Se ensayó revertirla y volver a aplicarla.

## Paso 3 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0030`, nombra `carga_borrado` como faltante.

## Paso 4 · Desplegar

```bash
firebase deploy --only functions,hosting
```

## Paso 5 · Comprobar

1. **Celular.** Una carga en modo «ya existen» con **Trae el celular**, con
   un archivo de tres filas: una con el celular de un panelista, una con un
   número que comparten dos personas y una con texto. Tiene que entrar la
   primera; las otras dos tienen que informarse como `celular_compartido` y
   `formato_invalido`.
2. **Datos personales.** Con el celular elegido como columna que identifica,
   el panel de calidad y la revisión no lo advierten. Una pregunta «Otro,
   especificar» con un teléfono adentro sí.
3. **Borrado**, sobre una carga de prueba:
   1. La vista previa coincide con lo que se espera.
   2. El botón se habilita solo con el nombre exacto.
   3. Borrar muestra el avance y, al terminar, el resumen.
   4. Después, por SQL:

      ```sql
      select carga_nombre, estado, personas_dadas_de_baja, personas_conservadas,
             respuestas_borradas, actor_email, creado_en, terminado_en
        from carga_borrado order by id desc limit 5;
      ```

   5. En el log de la función:

      ```bash
      gcloud logging read 'textPayload:"[borrado]"' --limit 5 \
        --project gestion-paneles --format 'value(textPayload)'
      ```

4. **COLOQUIO**, con la superficie intacta:

   ```bash
   python3 scripts/verificar_coloquio.py
   ```

   Tiene que seguir en 18 de 18.

## Si un borrado queda a medias

Esto pasa si un lote falló o si se cortó el cierre. En ese caso:

- La carga **sigue apareciendo** en la sección Cargas.
- Su fila en `carga_borrado` está `en_curso`.
- Volver a **Borrar la carga…** lo reanuda: reintenta los lotes que fallaron
  o termina el cierre.

Para ver en qué quedó:

```sql
select b.id, b.carga_nombre, b.estado, b.trabajo_id, t.estado as trabajo
  from carga_borrado b left join ingesta_trabajo t on t.id = b.trabajo_id
 where b.estado = 'en_curso';
```

Mientras el borrado está a medias, la carga **no admite ingestas**, por la
guarda de la `0029`.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
pip install playwright        # para las pruebas de pantalla, en un Chromium
cd functions && python3 -m pytest -q
```

- `tests/test_pedido_celular_identificador.py` (16 pruebas): cubre el
  selector, la asignación, el número escrito de seis formas, el celular
  compartido, el formato inválido, el no encontrado, la carga de punta a
  punta sin alias registrado, y que la pantalla explique cada motivo.
- `tests/test_pedido_pii_columna_de_identidad.py` (9 pruebas, 2 en
  Chromium): la columna de identidad no genera la advertencia ni figura como
  que va al store semántico. Tampoco la generan el celular, el correo ni el
  documento marcados. Una abierta con teléfonos sigue generándola. Marcar
  después hace desaparecer la advertencia, en el servidor y en la pantalla.
- `tests/test_borrar_carga.py` (19 pruebas): cubre las seis personas del
  escenario, una por cada caso, y además:
  - la vista previa, que no toca nada y coincide con el resultado;
  - las bajas por `bajas.retirar` con su lápida;
  - todas las respuestas, el cuestionario y la carga;
  - la unidad compartida que se conserva y la propia que se borra;
  - el `borrado_pendiente` para COLOQUIO;
  - la auditoría que sobrevive a la carga;
  - el nombre exacto, el permiso solo de admin y la ingesta en curso, en las
    dos direcciones;
  - dos borrados a la vez;
  - un lote cortado después de commitear el store semántico que se reanuda
    sin dejar respuestas huérfanas;
  - un cierre cortado que se termina al volver a pedirlo;
  - una carga sin personas.
- `tests/test_borrar_carga_pantalla.py` (2 pruebas, en Chromium): la vista
  previa con el texto de irreversible, el botón que se habilita solo con el
  nombre exacto (que se ve sin mayúsculas forzadas), y el error del servidor
  junto al botón.

---

## Volver atrás

Primero, volver a desplegar el código anterior. **Después:**

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0030.sql
```

- Se pierde la auditoría de los borrados hechos (el archivo dice cómo
  exportarla antes).
- **Lo borrado no vuelve.**
- Terminar cualquier borrado en curso antes de revertir.
- El celular como identificador y el filtro del detector son solo código: se
  van con el despliegue anterior, sin tocar la base.
