# Despliegue — taxonomía de temáticas, filtro e ingesta

Un solo despliegue para dos entregas que van juntas en el PR #66:

1. **Taxonomía de temáticas y correcciones del filtro** (R-TEM.7 a R-TEM.14).
   Spec: `specs/SPEC_tematicas_y_seccion_cargas.md`, sección «Ampliación».
   Decisión: D76.
2. **Indicador de procesamiento al ingestar, guarda contra el doble envío y
   la fecha del estudio** (R-ING.1 a R-ING.3 y el anexo de la fecha).
   Pedido: `docs/PEDIDO_indicador_ingesta_y_doble_envio.md`. Decisión: D77.

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0027`** (temáticas de la carga y
de cada panelista, PR #65).
**Qué cambia:** dos migraciones aditivas en la bóveda (`0028` y `0029`), una
actualización única del store semántico que hace un script (completar la
fecha de las cargas ya ingestadas), y código de backend y de frontend. El
esquema del store semántico no cambia.
**Qué NO cambia:** ningún secreto (las propuestas usan la `CLAUDE_API_KEY` y la
configuración `TEMATICAS_*` de la versión anterior), rol de base, permiso de
IAM, cola ni configuración de función. Hay un **permiso de aplicación nuevo**,
`gestionar_taxonomia` (admin, operaciones, analista), que vive en `auth.py`.
Nada que coordinar con COLOQUIO: ni las tablas, ni la vista, ni el índice, ni
la columna nuevos tienen `grant` a nadie (Paso 11 lo comprueba).

### Orden en una línea

`0028` → revisar trabajos activos duplicados → `0029` → verificar esquema →
desplegar → completar fechas → arranque de la taxonomía → comprobar.

---

## Qué trae

### 1 · Taxonomía de temáticas y correcciones del filtro (D76)

- **Áreas** (dos niveles: área → temática). Una pantalla nueva, Cargas →
  **Taxonomía de temáticas**:
  - **Proponer la taxonomía** (arranque): una sola llamada a Claude propone
    áreas para las temáticas sin área. Se revisa —renombrar, mover, unir,
    marcar especiales— y recién al confirmar se guarda.
  - **Reorganizar taxonomía**: Claude propone operaciones (fusionar
    temáticas, mover, renombrar, fusionar o dividir áreas) y cada una se
    aprueba o se descarta por separado. Nunca se aplica nada sola.
  - **Mover a…** cada temática a mano, **Especial** por área, el
    **historial** de cambios (quién, cuándo, qué) y las **temáticas sin
    personas**.
- **Crecimiento con cada carga**: en la revisión, la misma llamada que ya
  proponía las temáticas ubica cada una en un área existente, o propone una
  nueva (marcada). Lo ya clasificado no se mueve desde una carga.
- **Área especial**: vuelve especiales a todas sus temáticas (sin columna,
  sin exportación, filtro restringido por rol). La herencia suma.
- **El filtro de Consultas** (+ Temática) agrupado por área, plegable, con
  búsqueda, la opción de elegir un área entera, y **cuántas personas** tienen
  cada temática. Una temática que no tiene nadie **no se ofrece**.
- **Correcciones** de lo que mostró «nivel educativo»:
  - en la revisión, marcar una variable como demográfica **retira en el
    momento** las temáticas propuestas que salían solo de ella, sin volver a
    llamar a Claude; desmarcarla las devuelve;
  - en el detalle de una carga, una temática con cero personas se marca
    «sin personas · probablemente sobrante», con **Quitar**.

### 2 · Ingesta: aviso, guarda y fecha del estudio (D77)

- **El modal dice que trabaja.** Al apretar «Ingestar», en el momento y
  antes de que conteste el servidor, aparece *Preparando la carga… No cierres
  esta ventana*, arriba y junto al botón, con un contador; a los 8 s pasa a
  *Sigue procesando…*. El botón y todo el mapeo quedan deshabilitados, y un
  segundo clic no hace nada. Un error muestra el motivo y devuelve el botón;
  una respuesta exitosa cierra el modal y pasa al seguimiento de siempre.
- **Una sola ingesta en curso por destino**, garantizado por la base (índice
  único parcial de la `0029`). Un segundo pedido —doble clic, dos pestañas,
  otra persona— recibe `409 ingesta_en_curso` con el `trabajo_id` del que ya
  está, y la pantalla ofrece **Ver su avance**. Una ingesta terminada o
  fallida no bloquea. Cubre también el reproceso del mismo estudio.
- **Cuánto tarda cada parte del pedido** (despivotar, crear individuos, armar
  y guardar el plan, persistir lotes, encolar): en
  `ingesta_trabajo.preparacion`, en `GET /ingestas/<id>` y en el log
  (`[ingesta] preparación …`, solo números), con un `[ingesta] aviso:` si
  pasa de 120 s (la API corta a los 300).
- **La fecha del estudio llega al cuestionario.** La ingesta de una carga lo
  creaba sin fecha y la ficha decía *sin fecha de campo*. Ahora pasa la
  `fecha_estudio`; corregirla en *Datos del estudio* la lleva al
  cuestionario; y un script completa una vez lo ya ingestado (Paso 7).

### Rutas nuevas o cambiadas

| Ruta | Permiso | Qué hace |
|---|---|---|
| `GET /tematicas/taxonomia` | `leer` | La taxonomía guardada, con cargas y personas |
| `POST /tematicas/taxonomia/propuesta` | `gestionar_taxonomia` | Arranque: propone áreas; no escribe |
| `PUT /tematicas/taxonomia` | `gestionar_taxonomia` | Guarda el arranque confirmado (solo temáticas sin área) |
| `POST /tematicas/taxonomia/reorganizacion` | `gestionar_taxonomia` | Propone operaciones; no aplica ninguna |
| `POST /tematicas/taxonomia/operaciones` | `gestionar_taxonomia` | Aplica **una** operación aprobada y la registra |
| `GET /tematicas/taxonomia/cambios` | `leer` | El historial |
| `GET /tematicas/filtro` | `leer` | El filtro: por área, con personas, sin las que no tiene nadie |
| `GET /tematicas/sin-personas` | `leer` | Temáticas de cada carga que no tiene nadie |
| `POST /consultas` | `consultar` | El criterio de temática va por id y admite `areas` (y `dimension: area_tematica`); los nombres de consultas guardadas siguen valiendo |
| `POST /cargas/<id>/ingesta` con `solo_revisar` | `ingestar` | Con `proponer_tematicas: false` y `tematicas_editadas`, ajusta la lista a las variables que siguen yendo al semántico, sin llamar a Claude |
| `PUT /cargas/<id>/tematicas` | `ingestar` | Acepta `area` / `area_id` por temática (solo ubica las que no tienen) |
| `POST /cargas/<id>/ingesta`, `POST /encuestas/<id>/ingesta`, `POST /encuestas/<id>/sav/ingesta` | `ingestar` | Con una ingesta en curso del mismo destino, `409` `ingesta_en_curso` con `detalle.trabajo_id` (también con `solo_revisar`). Miden su preparación |
| `PATCH /cargas/<id>` | `ingestar` | Si cambia `fecha_estudio`, la lleva al cuestionario del store semántico (también vaciarla) |
| `GET /ingestas/<id>` | `leer` | Suma `preparacion`: milisegundos de cada parte del pedido que creó el trabajo |

---

## Paso 0 · Rama y PR

El PR anterior (#65) ya está mergeado: este es el PR #66, con la rama rehecha
sobre `main`, y lleva las dos entregas.

## Paso 1 · Túneles y DSN de los dos stores

La bóveda para las migraciones; el semántico, para el script de fechas del
Paso 7.

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
cloud-sql-proxy gestion-paneles:$REGION:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
psql "$DSN_SEMANTICA" -tAc "select current_database()"
```

## Paso 2 · Aplicar `boveda/0028` (antes de desplegar el código)

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0028_taxonomia_de_tematicas.sql
```

Crea `area_tematica`, `tematica.area_id`, `taxonomia_cambio` y la vista
`v_tematica`. Salida en el ensayo (bóveda llevada a la `0027`, con temáticas
existentes):

```
BEGIN
CREATE TABLE
COMMENT
COMMENT
ALTER TABLE
ALTER TABLE
COMMENT
CREATE INDEX
CREATE TABLE
COMMENT
CREATE INDEX
ALTER TABLE
CREATE VIEW
COMMENT
REVOKE
COMMIT
```

Se puede correr dos veces (la segunda avisa `already exists, skipping` y
termina en `COMMIT`). Las temáticas existentes quedan **sin área** hasta el
arranque (Paso 6); mientras tanto el filtro las muestra en el grupo «Sin
área».

## Paso 3 · Antes de la `0029`: que no haya dos trabajos activos por destino

El índice único no se puede crear si ya hay dos trabajos `encolada` o
`procesando` para el mismo destino, y la migración fallaría entera (sin
cambiar nada). Mirarlo primero:

```bash
psql "$DSN_BOVEDA" -c "
  select destino_tipo, destino_id, count(*)
    from ingesta_trabajo
   where estado in ('encolada', 'procesando')
   group by 1, 2 having count(*) > 1;"
```

Vacío: seguir al Paso 4. Si aparece alguno —típicamente un trabajo colgado
de los episodios de cola pausada—, ver sus lotes:

```bash
psql "$DSN_BOVEDA" -c "
  select t.id, t.destino_tipo, t.destino_id, t.estado, t.creado_en,
         count(l.id) filter (where l.estado = 'pendiente')  as pendientes,
         count(l.id) filter (where l.estado = 'procesando') as en_curso,
         count(l.id) filter (where l.estado = 'fallido')    as fallidos,
         count(l.id) filter (where l.estado = 'ok')         as ok
    from ingesta_trabajo t
    left join ingesta_lote l on l.trabajo_id = t.id
   where t.estado in ('encolada', 'procesando')
   group by t.id order by t.destino_tipo, t.destino_id, t.id;"
```

y resolver **el más viejo** de cada destino. Si nada lo está procesando (sin
lotes `procesando` recientes, sin tareas en la cola), se cierra como fallido;
lo que haya entrado queda, porque la ingesta es idempotente:

```sql
-- Reemplazar <ID>.
update ingesta_lote set estado = 'fallido', reintentable = true,
       error = 'cerrado antes de la 0029: había otro trabajo activo',
       actualizado_en = now()
 where trabajo_id = <ID> and estado in ('pendiente', 'procesando');
update ingesta_trabajo set estado = 'fallida', terminado_en = now()
 where id = <ID>;
```

Ensayado: con dos trabajos activos para el mismo destino la `0029` falla así,
y aplicada después de resolverlos, pasa:

```
BEGIN
psql:db/boveda/0029_una_ingesta_activa_por_destino.sql:43: ERROR:  could not create unique index "ingesta_trabajo_uno_activo_por_destino"
DETAIL:  Key (destino_tipo, destino_id)=(carga, 999) is duplicated.
```

> **Desde la `0029`, un trabajo colgado bloquea su destino** hasta que se
> resuelve: es el precio de la guarda. La pantalla lleva a su seguimiento;
> si sus lotes están `fallido`, *Reintentar los lotes fallidos* lo destraba,
> y si quedaron `pendiente` (la tarea murió antes de marcarlos), es la receta
> de arriba.

## Paso 4 · Aplicar `boveda/0029`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0029_una_ingesta_activa_por_destino.sql
```

Crea el índice único parcial `ingesta_trabajo_uno_activo_por_destino` y la
columna `ingesta_trabajo.preparacion`. Salida en el ensayo:

```
BEGIN
CREATE INDEX
COMMENT
ALTER TABLE
COMMENT
COMMIT
```

Se puede correr dos veces (la segunda avisa `already exists, skipping`). Se
ensayó revertirla y volver a aplicarla.

## Paso 5 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0028`, nombra como faltantes `area_tematica`, `tematica.area_id`,
`taxonomia_cambio` y `v_tematica`; sin la `0029`,
`ingesta_trabajo_uno_activo_por_destino` e `ingesta_trabajo.preparacion`.

## Paso 6 · Limpiar «nivel educativo» (se puede hacer antes o después)

Quita la temática de las cargas y, si no la tiene nadie, la borra. Ensayado:

```sql
begin;
delete from carga_tematica
 where tematica_id = (select id from tematica where nombre = 'nivel educativo');
delete from tematica_pregunta
 where tematica_id = (select id from tematica where nombre = 'nivel educativo');
delete from tematica t
 where t.nombre = 'nivel educativo'
   and not exists (select 1 from persona_tematica pt where pt.tematica_id = t.id);
commit;
```

(El primer `delete` ya se lleva en cascada sus preguntas y personas; el
segundo queda por las dudas y no hace nada.)

Con el código nuevo esto no hace falta para el filtro —una temática sin
personas ya no se ofrece—, pero deja limpia la carga.

## Paso 7 · Desplegar

```bash
firebase deploy --only functions,hosting
```

El código nuevo escribe `ingesta_trabajo.preparacion` al encolar: por eso la
`0029` va antes.

## Paso 8 · Completar la fecha de las cargas ya ingestadas

Operación única entre las dos bases: copia `carga.fecha_estudio` (bóveda) a
`cuestionario.fecha_campo` (semántico), por `ref_estudio`, **solo donde está
vacía**. Después del despliegue, para que no quede ninguna carga ingestada
en el medio con el código viejo. Primero el ensayo, que no escribe:

```bash
python3 scripts/completar_fechas_de_cargas.py
python3 scripts/completar_fechas_de_cargas.py --aplicar
```

Salida en el ensayo (tres cargas con fecha; dos con el cuestionario vacío y
una con una fecha propia, que no se toca):

```
  a completar: carga 1 · Ómnibus agosto 2026
  a completar: carga 2 · Ingestada antes
3 carga(s) con fecha del estudio · 2 a completar
Ensayo: no se escribió nada. Para aplicar: --aplicar
```

```
  completada: carga 1 · Ómnibus agosto 2026
  completada: carga 2 · Ingestada antes
3 carga(s) con fecha del estudio · 2 completada(s)
```

Correrlo de nuevo dice `0 a completar`. Una carga **sin** fecha del estudio
queda sin fecha a propósito: no se inventa una. Se completa desde *Datos del
estudio* y la edición ya la lleva al cuestionario.

## Paso 9 · Arranque de la taxonomía y revisión general

1. Cargas → **Taxonomía de temáticas** → **Temáticas sin personas**: es la
   revisión general que pide el spec. Cada fila es una temática de una carga
   que no tiene nadie; desde **Ir a la carga** se quita (en el detalle está
   marcada «probablemente sobrante»). Una carga que todavía se está
   procesando también aparece: esperar a que termine.
   La misma lista por SQL:

   ```sql
   select c.nombre as carga, t.nombre as tematica
     from carga_tematica ct
     join carga c on c.id = ct.carga_id
     join tematica t on t.id = ct.tematica_id
    where not exists (select 1 from persona_tematica pt
                       where pt.carga_id = ct.carga_id
                         and pt.tematica_id = ct.tematica_id)
    order by 1, 2;
   ```
2. **Proponer la taxonomía** cuando haya **varias cargas** con temáticas: con
   tres temáticas Claude no tiene material para áreas con sentido. Revisar con
   cuidado —la primera taxonomía condiciona las siguientes, porque las cargas
   nuevas se clasifican contra ella— y **Confirmar y guardar**.
3. Cada tanto, **Reorganizar taxonomía**: las áreas nuevas que traen las
   cargas tienden a multiplicarse.

## Paso 10 · Comprobar en la pantalla

Taxonomía y filtro:

1. Consultas → **+ Temática**: agrupado por área, con personas, búsqueda; un
   área elegida entera trae a quien tenga cualquiera de sus temáticas.
   «Nivel educativo» no aparece.
2. Una carga nueva: en la revisión, la columna **Área** de cada temática
   propuesta; marcar como demográfica la variable de la que sale una temática
   y volver a revisar: se retira, con un aviso, y vuelve al desmarcar.
3. Taxonomía → marcar un área como especial: sus temáticas dejan de aparecer
   como columna y, para el analista, en el filtro.

Ingesta:

4. Cargar panelistas en una carga de prueba: al apretar **Ingestar**, el
   aviso *Preparando la carga…* aparece en el momento, y el botón y el mapeo
   quedan grises hasta que llega la revisión.
5. Con esa ingesta corriendo, abrir la misma carga en otra pestaña y
   ingestar: dice que ya hay una en curso y **Ver su avance** lleva al
   seguimiento.
6. La ficha de un panelista de esa carga: en *Respuestas procesadas*, la
   fecha del estudio y no *sin fecha de campo*. Cambiarla en *Datos del
   estudio* y volver a abrir la ficha: la fecha nueva.
7. En los logs de la función `api`, una línea por carga:

   ```bash
   gcloud logging read 'textPayload:"[ingesta] preparación"' --limit 5 \
     --project gestion-paneles --format 'value(textPayload)'
   ```

   Si aparece `[ingesta] aviso:`, la preparación de esa carga pasó de 120 s y
   la parte que nombra es la que crece (D77: hoy, `crear_individuos`).

## Paso 11 · La superficie de COLOQUIO

```bash
python3 scripts/verificar_coloquio.py
```

Nada de esta entrega tiene `grant`; tiene que seguir en 18 de 18.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
pip install playwright        # para las pruebas del modal, en un Chromium
cd functions && python3 -m pytest -q
```

- `tests/test_taxonomia_de_tematicas.py` (17 pruebas) cubre el Definition of
  Done de la taxonomía: arranque que se guarda solo al confirmar, crecimiento
  en la misma llamada con área existente o nueva, taxonomía que no cambia al
  abrir el filtro ni al consultar, reorganización que no aplica nada sola,
  fusión que reapunta cargas, preguntas y personas sin duplicar (con
  recálculo), operaciones sobre áreas y su registro, área especial que vuelve
  especiales a sus temáticas, filtro por área con personas, área entera, una
  especial que no entra por un área común, temática sin personas fuera del
  filtro, y la propuesta que sigue a la marca de demográficas sin llamar a
  Claude.
- `tests/test_pedido_indicador_ingesta_y_doble_envio.py` (23 pruebas): el
  segundo pedido rechazado con el trabajo existente, antes de crear a nadie y
  también en la revisión; terminada, con errores o fallida no bloquean; otra
  carga no queda bloqueada; el índice frena aunque se saltee el chequeo
  previo; **dos pedidos simultáneos en dos conexiones dejan un solo
  trabajo**; el reproceso cuenta; los tiempos de cada parte en el trabajo y
  en el log, y el aviso de preparación larga; la fecha de la carga en el
  cuestionario y en la ficha; una encuesta sigue igual; el estudio de cada
  destino sale de `estudio_del_destino`; editar la fecha (y vaciarla) llega
  al cuestionario; completar las anteriores sin pisar fechas.
- `tests/test_modal_de_ingesta_procesando.py` (7 pruebas, en Chromium): el
  estado de procesando antes de la respuesta, botón y mapeo deshabilitados,
  un doble clic manda un solo pedido, «sigue procesando», el error con su
  motivo y el botón devuelto, el éxito que cierra el modal y pasa al
  seguimiento, y «ya hay una en curso» que lleva al suyo. Sin Playwright se
  omiten; el CI lo instala.

---

## Volver atrás

Primero volver a desplegar el código anterior y **después**, en este orden:

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0029.sql
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0028.sql
```

- La `0029` revertida quita la guarda y los tiempos registrados; no pierde
  otra cosa.
- La `0028` revertida pierde las áreas, la ubicación de cada temática y el
  historial (el archivo dice cómo exportarlos). **Las fusiones de temáticas
  aplicadas no se deshacen**: reapuntaron referencias en tablas de la `0027`.
- Las fechas completadas en los cuestionarios **se quedan**: son correctas, y
  el código anterior las lee igual.

Se ensayó revertir y volver a aplicar la `0028` y la `0029`.
