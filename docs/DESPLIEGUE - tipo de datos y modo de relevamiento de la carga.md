# Despliegue — tipo de datos y modo de relevamiento de la carga

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0025`** (en particular la
`0024`, que crea `persona_carga` y `v_carga_resumen`). El store semántico no
se toca.
**Qué cambia:** una migración en la bóveda (`0026`) y código de backend y de
frontend.
**Qué NO cambia:** ningún secreto, rol, permiso de IAM, cola ni
configuración de función. Nada que coordinar con COLOQUIO: `carga` y
`v_carga_resumen` no son parte de la superficie externa.

---

## Qué trae

Cada carga declara dos datos más del estudio del que sale:

| Dato | Opciones |
|---|---|
| Tipo de datos de origen | Encuesta · Reclutamiento · Otro (especificar) |
| Modo de relevamiento | Cara a cara · Telefónico · WhatsApp · Web · Otro (especificar) |

- Se guardan **en la carga**, como la fecha y el público objetivo, y llegan a
  cada panelista por el vínculo `persona_carga`: la ficha («Origen»), la
  ficha seudónima de los resultados, la composición por carga y Estadísticas
  los muestran. Corregirlos en «Datos del estudio» se ve en todas las fichas
  sin tocar a ninguna persona.
- En la lista de resultados hay dos columnas nuevas, **Tipo de datos de
  origen** y **Modo de relevamiento**, junto a «Estudio de origen». Con varias
  cargas muestran los valores distintos («Telefónico | Web»).
- **Una carga nueva los tiene que declarar** (lo exige `POST /cargas`).
  «Otro» exige especificar. Las cargas anteriores quedan **sin dato** y se
  pueden completar desde «Datos del estudio»; no se les asigna un valor.

---

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`.

## Paso 2 · Aplicar `boveda/0026` (antes de desplegar el código)

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0026_carga_tipo_y_modo_de_origen.sql
```

Salida en el ensayo (bóveda llevada a la `0025`, con una carga previa):

```
BEGIN
ALTER TABLE
ALTER TABLE
ALTER TABLE
ALTER TABLE
DO
COMMENT
COMMENT
COMMENT
COMMENT
CREATE VIEW
REVOKE
COMMIT
```

Se puede correr dos veces: la segunda avisa `column ... already exists,
skipping` y termina en `COMMIT` sin cambiar nada. Las cargas que ya existían
quedan con las cuatro columnas en nulo.

## Paso 3 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0026` aplicada, el verificador la nombra como faltante
(`carga.tipo_datos_origen`, …) y la pantalla de Cumplimiento lo muestra.

## Paso 4 · Desplegar

```bash
firebase deploy --only functions,hosting
```

## Paso 5 · Comprobar en la pantalla

1. Panelistas → **Cargar panelistas**: el formulario pide tipo de datos y
   modo de relevamiento; «Otro» abre el campo «Especificar».
2. Elegir una carga en el filtro → **Datos del estudio**: completar los dos
   datos de una carga anterior y guardar.
3. Abrir la ficha de alguien de esa carga: la sección «Origen» los muestra.

---

## Volver atrás

Primero volver a desplegar el código anterior y **después**:

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0026.sql
```

Pierde el tipo y el modo declarados en cada carga; el archivo dice cómo
exportarlos antes. Se ensayó revertir y volver a aplicar la `0026` sobre la
misma base.
