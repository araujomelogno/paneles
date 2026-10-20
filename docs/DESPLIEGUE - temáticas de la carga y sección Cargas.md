# Despliegue — temáticas de la carga y sección Cargas (R-TEM, R-CAR)

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Spec:** `specs/SPEC_tematicas_y_seccion_cargas.md` · **Decisión:** D74
**Precondición:** la bóveda al día hasta la **`0026`** (tipo de datos y modo
de relevamiento de la carga). El store semántico no se toca.
**Qué cambia:** una migración aditiva en la bóveda (`0027`) y código de
backend y de frontend.
**Qué NO cambia:** ningún secreto (la propuesta usa la `CLAUDE_API_KEY` que
ya monta la verificación), rol, permiso de IAM, cola ni configuración de
función. Nada que coordinar con COLOQUIO: `tematica` y `carga_tematica` no
tienen `grant` a nadie y `verificar_coloquio.py` pasa igual (18/18 en el
ensayo).

---

## Qué trae

- **Temáticas incorporadas por carga.** Los temas que tratan las preguntas
  del archivo («telefonía móvil», «consumo de bebidas alcohólicas»), como
  etiquetas. Son una entidad **compartida** entre cargas, con el nombre
  normalizado (minúsculas, espacios simples; la base lo hace valer con un
  `check`), y lo que difiere solo en tildes reutiliza la existente.
- **Propuesta con Claude en la revisión de la carga.** Al revisar una carga
  nueva (Panelistas → Cargar panelistas → Ingestar), el resumen trae un bloque
  **Temáticas incorporadas** con lo que propuso Claude a partir de **los
  textos de las preguntas que van al store semántico** —nunca respuestas, ni
  demográficas, ni identidad—. El analista acepta, quita o agrega; se guardan
  al confirmar. Si Claude falla o tarda, la revisión avisa, la lista queda
  vacía y **la carga se confirma igual**.
- **Sección «Cargas»** en el menú, para todos los roles: listado por fecha
  del estudio con sus temáticas, filtro por temática y búsqueda por nombre;
  detalle con atributos, creados y reutilizados, temáticas editables
  (permiso `ingestar`), lista paginada de panelistas (la misma ruta y permiso
  que Panelistas) con enlace a la ficha, y acceso a su composición.
- **Cargas anteriores.** En su detalle, **Proponer temáticas** usa las
  preguntas ya guardadas, sin volver a subir el archivo. La propuesta no se
  aplica sola.

### Rutas nuevas o cambiadas

| Ruta | Permiso | Qué hace |
|---|---|---|
| `GET /cargas?q=&tematica_id=` | `leer` | Ahora con `tematicas` por carga, filtros y orden por fecha del estudio |
| `GET /cargas/<id>` | `leer` | Ahora con `tematicas` |
| `GET /tematicas` | `leer` | El vocabulario, con cuántas cargas usa cada una |
| `PUT /cargas/<id>/tematicas` | `ingestar` | Deja a la carga con esa lista (único camino de escritura) |
| `POST /cargas/<id>/tematicas/propuesta` | `ingestar` | Propone con las preguntas guardadas; no escribe |
| `POST /cargas/<id>/ingesta` con `solo_revisar` | `ingestar` | El resumen trae `tematicas` (`proponer_tematicas: false` la omite) |
| `GET /panelistas?carga_id=` | `leer` | Cada fila trae `origen_en_carga` (`creada` / `reutilizada`) |

### Configuración (opcional)

Por defecto la propuesta usa los valores de la verificación: `CLAUDE_API_KEY`,
`CLAUDE_MODELO` y `VERIFICACION_PRESUPUESTO_S` como espera. Se pueden separar
sin tocar código, en `functions/.env` (no son secretos; `test_main.py` los
lista en `NO_SON_SECRETOS`):

```bash
TEMATICAS_PROVEEDOR=claude    # o «ninguno»: la revisión avisa y se cargan a mano
TEMATICAS_MODELO=             # vacío = CLAUDE_MODELO
TEMATICAS_TIEMPO_S=60         # vacío = VERIFICACION_PRESUPUESTO_S
```

No hace falta agregar ninguna para desplegar.

---

## Paso 0 · Comprobar que el PR no está mergeado

La rama se reutiliza entre entregas (ver `CLAUDE.md`):

```bash
gh pr view --json state,mergedAt
```

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`.

## Paso 2 · Aplicar `boveda/0027` (antes de desplegar el código)

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0027_tematicas_de_la_carga.sql
```

Salida en el ensayo (bóveda llevada a la `0026`, con cargas previas):

```
BEGIN
CREATE TABLE
COMMENT
COMMENT
ALTER TABLE
CREATE TABLE
COMMENT
COMMENT
CREATE INDEX
ALTER TABLE
COMMIT
```

Se puede correr dos veces: la segunda avisa `relation "tematica" already
exists, skipping` (y lo mismo para `carga_tematica` y su índice) y termina en
`COMMIT` sin cambiar nada. Es aditiva: no toca `carga` ni ninguna tabla
existente, y las cargas anteriores quedan sin temáticas.

Comprobación rápida del `check` de normalización:

```bash
psql "$DSN_BOVEDA" -c "insert into tematica (nombre) values ('Telefonía')"
# ERROR:  new row for relation "tematica" violates check constraint "tematica_nombre_normalizado"
```

## Paso 3 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0027` aplicada, el verificador la nombra (y la pantalla de
Cumplimiento lo muestra):

```
✗ 0027_tematicas_de_la_carga.sql
    falta tematica  (el vocabulario compartido de temáticas …)
    falta carga_tematica  (qué temáticas cubre cada carga …)
```

Con la migración aplicada: «Las dos bases están al día.»

## Paso 4 · Desplegar

```bash
firebase deploy --only functions,hosting
```

## Paso 5 · Comprobar en la pantalla

1. Aparece **Cargas** en el menú. El listado muestra las cargas existentes,
   la más reciente primero, sin temáticas.
2. Abrir una carga anterior → **Proponer temáticas**: llegan etiquetas con la
   marca *IA* y, debajo, de qué preguntas sale cada una. **Guardar
   temáticas**. Volver al listado: la carga las muestra y el filtro por
   temática la encuentra.
   - Si en vez de etiquetas aparece «No se pudieron proponer temáticas (…)»,
     el motivo está entre paréntesis y en el log (`[tematicas] sin
     propuesta: …`). Sin `CLAUDE_API_KEY` dice «falta CLAUDE_API_KEY».
3. En el detalle, **Panelistas cargados** lista a la gente con *creada* /
   *reutilizada*; **Ver ficha** abre la ficha y **Ver su composición** abre
   Composición en el ámbito de esa carga.
4. Una carga nueva (Panelistas → Cargar panelistas): en la revisión, el
   bloque **Temáticas incorporadas** trae la propuesta; confirmar guarda la
   lista revisada.

En el log de la función, cada propuesta deja una línea con números y sin
contenido:

```
[tematicas] preguntas=14 existentes=9 propuestas=5 ms=3210 entrada=1830 salida=240
```

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

`tests/test_tematicas_y_seccion_cargas.py` (37 pruebas) cubre el Definition of
Done: propuesta en la revisión con respuesta simulada, herramienta forzada,
qué viaja a Claude (sin respuestas ni demográficas ni identidad), reutilización
de existentes, normalización («Telefonía móvil» = «telefonía móvil»), falla
que no bloquea (timeout, 529, truncada, sin herramienta), edición antes y
después de confirmar, listado y filtros, detalle con creados/reutilizados y
paginación, propuesta para una carga anterior sin subir el archivo, y
permisos de la lista de panelistas. Batería completa en verde;
`scripts/verificar_coloquio.py` 18/18 contra la base de pruebas.

---

## Volver atrás

Primero volver a desplegar el código anterior y **después**:

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0027.sql
```

Pierde el vocabulario de temáticas y qué carga cubre cuál; el archivo dice
cómo exportarlos a CSV antes. Se ensayó revertir y volver a aplicar la `0027`
sobre la misma base.

Para apagar solo la propuesta con Claude, sin volver atrás nada:
`TEMATICAS_PROVEEDOR=ninguno` en `functions/.env` y `firebase deploy --only
functions`. Las temáticas se siguen editando a mano.
