# Despliegue — historial de respuestas por carga

**Pedido:** `specs/PEDIDO_historial_respuestas_por_carga.md` (R-HIS.1 a
R-HIS.5, R-ALT.1) · **Decisión:** D95
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0037`** y el store semántico
hasta la **`0012`**, con el código de D92 a D94 desplegado.

> **Hasta que esto esté desplegado: no borres cargas de encuestas
> incrementales.** Si hace falta deshacer una, volvé a subir el último archivo
> correcto como carga nueva en lugar de borrar.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | Meta | `functions/.env` | Código |
|---|---|---|---|---|---|---|
| | nada | **`0013`** (cambia datos) | nada | nada | nada | backend y frontend |

La `semantica/0013`:

1. crea **`respuesta_carga`**: qué traía cada carga, una fila por persona,
   pregunta y carga, sin vectores propios (apunta a la unidad de evidencia);
2. hace que `borrar_unidades_huerfanas()` mire también el historial (una
   unidad que solo usa el historial es el valor al que se vuelve sin pagar
   otro embedding), con dos triggers nuevos sobre `respuesta_carga`;
3. **crea el historial de lo ya cargado**: una fila por respuesta vigente, en
   la carga que la escribió por última vez. Lo anterior a esa carga nunca se
   guardó y no se inventa.

**Qué NO cambia:** la bóveda (ninguna migración), ningún secreto, rol,
permiso, cola, función ni variable; **ningún webhook en Meta** (D86); la
superficie de COLOQUIO. La batería tiene que seguir en **24 de 24**.

### Orden en una línea

proyecto fijado → sin ingestas ni borrados en curso → **respaldo de las dos
bases** → **corrida de prueba** → `semantica/0013` → verificación →
`verificar_esquema.py` en verde → token de COLOQUIO y batería →
`firebase deploy` → comprobaciones.

La `0013` va **antes** del código: el código nuevo escribe y lee
`respuesta_carga`. El código anterior no la nota (escribe respuestas sin
historial, como siempre).

### Puntos de control

| # | Qué tiene que cumplirse | Paso | Si no se cumple |
|---|---|---|---|
| 1 | Ninguna ingesta ni borrado de carga en curso | 1 | Se espera a que terminen |
| 2 | Respaldo de **las dos** bases hecho y `SUCCESSFUL` | 2 | No se empieza |
| 3 | La corrida de prueba informa las filas a crear y «cierra» | 3 | No se aplica la `0013`; se investiga |
| 4 | `verificar` cierra: ninguna respuesta con carga sin su historial | 5 | **Se restaura el respaldo del semántico**; no se despliega el código |
| 5 | `verificar_esquema.py` sin faltantes | 6 | No se despliega |
| 6 | Batería de COLOQUIO en 24 de 24 | 7 | No se despliega |

---

## Paso 1 · Preparar la terminal y fijar el proyecto — punto de control 1

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
gcloud config get-value project
git checkout main && git pull
ls db/semantica/0013_historial_de_respuestas.sql scripts/migrar_historial_de_respuestas.py
```

`gcloud config get-value project` tiene que decir **`gestion-paneles`**. Si
dice otra cosa, no seguir.

Túneles y DSN de las dos bases:

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
cloud-sql-proxy gestion-paneles:$REGION:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
psql "$DSN_SEMANTICA" -tAc "select current_database()"
```

Tienen que devolver `paneles_boveda` y `paneles_semantica`. Las
precondiciones:

```bash
psql "$DSN_BOVEDA" -tAc "select to_regclass('limpieza_provisoria') is not null"
psql "$DSN_SEMANTICA" -tAc "select count(*) from information_schema.columns where table_name = 'respuesta' and column_name = 'carga_id'"
```

`t` y `1`. Que no haya nada escribiendo respuestas:

```bash
psql "$DSN_BOVEDA" -tAc "select count(*) from ingesta_trabajo where estado in ('encolada','procesando')"
psql "$DSN_BOVEDA" -tAc "select count(*) from carga_borrado where estado = 'en_curso'"
```

Los dos en `0`. Si no, esperar a que terminen.

## Paso 2 · Respaldo de las dos bases — punto de control 2

```bash
gcloud sql backups create --instance=paneles-semantica --project="$PROYECTO" \
  --description="antes del historial de respuestas semantica/0013"
gcloud sql backups create --instance=paneles-boveda --project="$PROYECTO" \
  --description="antes del historial de respuestas (semantica/0013)"
gcloud sql backups list --instance=paneles-semantica --project="$PROYECTO" --limit=1
gcloud sql backups list --instance=paneles-boveda --project="$PROYECTO" --limit=1
```

Los dos tienen que figurar `SUCCESSFUL`. Anotar el id del semántico: es el
que se restaura si el punto 4 falla. El de la bóveda es por si un borrado de
carga posterior al despliegue hiciera falta deshacerlo junto con el semántico.
**Sin respaldo confirmado no se empieza.**

## Paso 3 · Corrida de prueba — punto de control 3

```bash
python3 scripts/migrar_historial_de_respuestas.py prueba
```

Ejecuta **el mismo archivo** `0013` en una transacción que se deshace.
Salida esperada, por ejemplo:

```
Corrida de prueba de la 0013 (deshecha: no se escribió nada)
  Respuestas vigentes con carga: 24850 · sin carga (anteriores a la 0012): 0
  Va a crear: 24850 fila(s) de historial
  Respuestas con carga sin su fila de historial después de aplicar: 0
```

Las filas a crear tienen que ser tantas como las respuestas vigentes con
carga. Sale con `0` si cierra y con `1` si no; con `1`, **no** se aplica.

## Paso 4 · Aplicar `semantica/0013`

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0013_historial_de_respuestas.sql
```

Una sola transacción. Termina en `COMMIT`. Con ~25.000 respuestas son pocos
MB y segundos.

## Paso 5 · Verificar la migración — punto de control 4

```bash
python3 scripts/migrar_historial_de_respuestas.py verificar
```

Tiene que decir **«Cierra.»**. **Si no cierra: se restaura el respaldo del
semántico y no se despliega el código.**

```bash
gcloud sql backups restore <ID_DEL_SEMANTICO> --restore-instance=paneles-semantica \
  --project="$PROYECTO"
```

## Paso 6 · Verificar el esquema — punto de control 5

```bash
python3 scripts/verificar_esquema.py --pii
```

Sin la `0013` nombra como faltante `respuesta_carga`. Con ella, **nada**, y
ninguna columna de PII del lado semántico. En verde **antes** del
`firebase deploy`.

## Paso 7 · Token de COLOQUIO y su batería — punto de control 6

```bash
source scripts/token_coloquio.sh
python3 scripts/verificar_coloquio.py
```

**24 de 24**, sin fallidos. La `0013` no toca la bóveda ni su superficie.

## Paso 8 · Desplegar

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
gcloud functions list --regions="$REGION" --format='value(name)' | grep -c whatsappwebhook
```

El segundo comando tiene que dar **`0`**.

## Paso 9 · Comprobaciones en la aplicación

- [ ] En una encuesta incremental con dos o más cargas, abrir **Borrar** de
      la **última** carga y quedarse en la vista previa: dice *«N respuestas
      vuelven al valor de una carga anterior · M se eliminan porque solo
      estaban en esta carga»*. **No confirmar.** (Lo cargado antes del
      despliegue tiene historial solo de su última carga, así que acá todo
      «se elimina»: el historial completo empieza con las cargas nuevas.)
- [ ] Subir una carga de prueba a una encuesta de prueba, después otra con
      las mismas personas, y borrar la segunda: las respuestas vuelven a las
      de la primera (ficha → **Respuestas procesadas**).
- [ ] En esa ficha, la respuesta dice en qué cargas apareció.
- [ ] Una carga con personas nuevas sin base legal: después de la revisión,
      la pantalla vuelve sola al paso 4 con el aviso arriba.

---

## La recuperación del caso real (carga 45)

No requiere desarrollo: volver a subir como carga nueva de «Encuesta Prueba -
10-11-26» el archivo de la **carga 42** —el último acumulado—. Las personas
quedan como «ya estaba» y sus respuestas vuelven al store semántico. Se puede
hacer antes o después de este despliegue; después, esa carga ya deja su
historial.

---

## Qué mirar después

**Borrados de carga que eliminaron respuestas** (las que no estaban en ninguna
otra carga):

```sql
select id, carga_nombre, terminado_en, respuestas_borradas,
       previa->>'respuestas_que_vuelven' as vuelven
  from carga_borrado
 where terminado_en > now() - interval '30 days'
 order by terminado_en desc;
```

**Unidades que solo usa el historial** (los valores que cambiaron entre
cargas; tienen que ser pocas):

```sql
select count(*) from unidad_evidencia u
 where not exists (select 1 from respuesta r where r.unidad_id = u.id);
```

---

## Costos

| Concepto | Costo |
|---|---|
| `respuesta_carga` | **Solo espacio**: una fila liviana por respuesta y carga, sin vectores. Con ~25.000 respuestas y dos o tres cargas por estudio, pocos MB. |
| Revertir al borrar | **US$ 0** en embeddings: las unidades ya existen. |
| Interfaz | **US$ 0** |

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_pedido_historial_respuestas_por_carga.py`: cada carga deja su
  historial sin vectores propios; borrar la cuarta de cuatro cargas
  incrementales deja el valor de la tercera; el caso de la carga 45 ya no
  vacía el cuestionario; lo que solo estaba en la carga borrada se elimina;
  borrar una carga anterior no toca la vigente; el valor anterior conserva su
  unidad y se va con su carga; la vista previa separa las que vuelven de las
  que se eliminan; borrar la encuesta y el retiro de un estudio se llevan el
  historial; el recall exacto no devuelve unidades solo del historial; la
  ficha dice en qué cargas apareció y si cambió; la migración es
  re-ejecutable.
- `tests/test_pantallas_cancelar_mixta_incrementales.py` (Chromium): con
  personas nuevas sin base legal, la pantalla vuelve sola al paso 4 y
  «Siguiente» revisa de nuevo con la evidencia.

---

## Volver atrás

**El código:** desplegar la versión anterior. Escribe respuestas sin
historial y borra como antes (la regla con el bug): mientras tanto, **no
borres cargas de encuestas incrementales**. La tabla queda y no molesta.

**La migración:** restaurar el respaldo del semántico del paso 2, o
`drop table respuesta_carga` y volver la función de huérfanas a la versión de
la `0010` (sin el `not exists` sobre el historial). Antes de borrar la tabla,
las unidades que solo usa el historial quedarían huérfanas sin que nadie las
borre: no rompen nada, ocupan un lugar en el recall por índice.
