# Despliegue — taxonomía de temáticas y correcciones del filtro (R-TEM.7 a R-TEM.14)

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Spec:** `specs/SPEC_tematicas_y_seccion_cargas.md`, sección «Ampliación» ·
**Decisión:** D76
**Precondición:** la bóveda al día hasta la **`0027`** (temáticas de la carga y
de cada panelista, PR #65). El store semántico no se toca.
**Qué cambia:** una migración aditiva en la bóveda (`0028`) y código de
backend y de frontend.
**Qué NO cambia:** ningún secreto (las propuestas usan la `CLAUDE_API_KEY` y la
configuración `TEMATICAS_*` de la versión anterior), rol de base, permiso de
IAM, cola ni configuración de función. Hay un **permiso de aplicación nuevo**,
`gestionar_taxonomia` (admin, operaciones, analista), que vive en `auth.py`.
Nada que coordinar con COLOQUIO: las tablas y la vista nuevas no tienen
`grant` a nadie.

---

## Qué trae

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

---

## Paso 0 · Rama y PR

El PR anterior (#65) ya está mergeado: este es un PR nuevo, con la rama
rehecha sobre `main`.

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
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

## Paso 3 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0028`, nombra como faltantes `area_tematica`, `tematica.area_id`,
`taxonomia_cambio` y `v_tematica`.

## Paso 4 · Limpiar «nivel educativo» (se puede hacer antes o después)

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

## Paso 5 · Desplegar

```bash
firebase deploy --only functions,hosting
```

## Paso 6 · Arranque de la taxonomía y revisión general

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

## Paso 7 · Comprobar en la pantalla

1. Consultas → **+ Temática**: agrupado por área, con personas, búsqueda; un
   área elegida entera trae a quien tenga cualquiera de sus temáticas.
   «Nivel educativo» no aparece.
2. Una carga nueva: en la revisión, la columna **Área** de cada temática
   propuesta; marcar como demográfica la variable de la que sale una temática
   y volver a revisar: se retira, con un aviso, y vuelve al desmarcar.
3. Taxonomía → marcar un área como especial: sus temáticas dejan de aparecer
   como columna y, para el analista, en el filtro.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

`tests/test_taxonomia_de_tematicas.py` (17 pruebas) cubre el Definition of
Done de la ampliación: arranque que se guarda solo al confirmar, crecimiento
en la misma llamada con área existente o nueva, taxonomía que no cambia al
abrir el filtro ni al consultar, reorganización que no aplica nada sola,
fusión que reapunta cargas, preguntas y personas sin duplicar (con recálculo),
operaciones sobre áreas y su registro, área especial que vuelve especiales a
sus temáticas, filtro por área con personas, área entera, una especial que no
entra por un área común, temática sin personas fuera del filtro, y la
propuesta que sigue a la marca de demográficas sin llamar a Claude.

---

## Volver atrás

Primero volver a desplegar el código anterior y **después**:

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0028.sql
```

Pierde las áreas, la ubicación de cada temática y el historial (el archivo dice
cómo exportarlos). **Las fusiones de temáticas aplicadas no se deshacen**:
reapuntaron referencias en tablas de la `0027`. Se ensayó revertir y volver a
aplicar la `0028`.
