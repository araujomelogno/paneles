# Despliegue — encuestas sin convocatoria e identificadores inválidos

**Pedido:** `docs/PEDIDO_encuestas_sin_convocatoria.md` · **Decisión:** D82
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0032`** (encuestas y cargas
unificadas, D81).

**Qué cambia:**

- Una migración **aditiva** en la bóveda: `0033`, `encuesta.origen`. No
  borra ni reescribe datos: completa una columna nueva.
- Código de backend y de frontend.

El store semántico no cambia.

**Qué NO cambia:** ningún secreto, variable de entorno, rol de base, permiso
de IAM, cola ni configuración de función. Ningún permiso de aplicación
nuevo.

**COLOQUIO:** nada que avisar. No se toca ninguna vista ni función de su
superficie, y `encuesta` no tiene `grant` para `coloquio_app`.

### Orden en una línea

`0033` → verificar esquema → desplegar → comprobar.

Puede aplicarse antes del código: el código anterior no lee `origen`, y una
encuesta que cree sin decirlo lo recibe del trigger.

---

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

## Paso 2 · Aplicar `boveda/0033`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0033_origen_de_la_encuesta.sql
```

Qué hace, en una transacción:

- Agrega `encuesta.origen` y lo completa:
  - `panel` las de panel;
  - `consulta` las independientes con algún convocado;
  - `carga` las demás, entre ellas las que creó la `0032` desde una carga
    existente (no tienen ningún convocado).
- Un trigger completa el origen de una inserción que no lo dice (`panel` si
  es de panel, `carga` si no), y su función tiene el `revoke` de siempre.
- `not null` y dos `check`: los valores permitidos, y que el origen no
  contradiga al tipo.

Termina en `COMMIT`. La primera vez avisa con tres `NOTICE … does not exist,
skipping` (los `drop … if exists`); se puede correr dos veces.

**Comprobar que las encuestas de la `0032` quedaron como `carga`:**

```sql
select origen, tipo, count(*) from encuesta group by 1, 2 order by 1, 2;
```

Las independientes que vinieron de una carga, como AVANTI, tienen que
figurar con `origen = carga`. Según el pedido son ocho, más las que se hayan
cargado desde Panelistas después del despliegue de D81. Una independiente
creada desde una consulta tiene que figurar como `consulta`.

## Paso 3 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
python3 scripts/verificar_coloquio.py
```

- Sin la `0033`, el primero nombra como faltantes `encuesta.origen` y
  `encuesta_origen_por_defecto()`.
- El segundo tiene que seguir en **18 de 18**.

## Paso 4 · Desplegar

```bash
firebase deploy --only functions,hosting
```

## Paso 5 · Comprobar

1. **Encuestas.** En el listado, AVANTI muestra **«—»** en Convocados y
   **«desde una carga»** en Audiencia.
2. **Su detalle:**
   - la sección se llama **Respondentes**;
   - el texto dice que llegó como archivo de respuestas;
   - no hay columna «Convocado» sino «Respondió / cargado», ni botón de
     exportar muestra.
3. **Una ola de panel con convocatoria** sigue mostrando «Convocatoria».
   Quien respondió sin estar convocado figura como **«no convocado»**, sin
   fecha.
4. **Personas con identificadores inválidos.** Panelistas → **Con
   identificadores inválidos**. La persona «......» de AVANTI aparece
   marcada «inválido: nombre, id de plataforma».

   La misma lista por SQL, para pasarla al equipo:

   ```sql
   select p.id_persona, p.nombre, p.documento, p.email, p.celular,
          (select string_agg(a.origen || ':' || a.id_en_origen, ', ')
             from alias_origen a where a.id_persona = p.id_persona) as alias
     from persona p
    where nullif(p.nombre, '') !~ '[A-Za-zÀ-ÖØ-öø-ÿ]'
       or nullif(p.documento, '') !~ '[0-9A-Za-zÀ-ÖØ-öø-ÿ]'
       or nullif(p.email, '') !~ '[0-9A-Za-zÀ-ÖØ-öø-ÿ]'
       or nullif(p.celular, '') !~ '[0-9A-Za-zÀ-ÖØ-öø-ÿ]'
       or exists (select 1 from alias_origen a
                   where a.id_persona = p.id_persona
                     and nullif(a.id_en_origen, '') !~ '[0-9A-Za-zÀ-ÖØ-öø-ÿ]');
   ```

   **No se borran solas.** Pueden tener respuestas válidas y otros
   identificadores buenos. El equipo decide desde cada ficha si darlas de
   baja.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_pedido_encuestas_sin_convocatoria.py` (24 pruebas):
  - el origen de las encuestas de carga, de consulta y de panel;
    `con_convocatoria`; el `check` y el trigger;
  - el no convocado sin fecha de convocatoria;
  - convocar a quien ya respondió le pone la fecha;
  - la longitudinal, la ficha y la lista negra;
  - la `0033` sobre una bóveda migrada por la `0032`: la carga queda
    `carga` y las olas `panel`;
  - un identificador sin letras ni dígitos se rechaza al crear y al
    resolver, en los cinco tipos, también si el alias ya estaba registrado;
  - el nombre sin letras se guarda vacío;
  - el listado de los ya cargados, por la ruta de Panelistas.
- `tests/test_pantallas_encuestas_sin_convocatoria.py` (5 pruebas, en
  Chromium, sobre el modo demo):
  - «—» y el origen en el listado;
  - «Respondentes» sin columna de convocatoria;
  - «Convocatoria» intacta en una desde consulta;
  - «no convocado» sin fecha;
  - el filtro de Panelistas.

---

## Volver atrás

Primero, volver a desplegar el código anterior. La columna puede quedarse:
el código anterior no la lee, y el trigger completa las encuestas que cree.
Si hace falta quitarla:

```sql
begin;
drop trigger if exists encuesta_origen_por_defecto on encuesta;
drop function if exists encuesta_origen_por_defecto();
alter table encuesta drop constraint if exists encuesta_origen_y_tipo;
alter table encuesta drop constraint if exists encuesta_origen_check;
alter table encuesta drop column if exists origen;
commit;
```

No se pierde nada que no se pueda recalcular con la misma regla.
