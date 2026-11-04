# Despliegue — encuestas y cargas unificadas

**Spec:** `specs/SPEC_encuestas_y_cargas_unificadas.md` · **Decisión:** D81
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0031`** (clientes y lista
negra, D80). R-EU.5 y R-EU.2 la usan.

**Qué cambia:**

- Una migración en la bóveda (`0032`) que **cambia datos**.
- Una migración aditiva en el store semántico (`0012`, `respuesta.carga_id`).
- Un script con corrida de prueba y verificación:
  `scripts/migrar_encuestas_y_cargas.py`.
- Código de backend y de frontend.

**Qué NO cambia:** ningún secreto, variable de entorno, rol de base, permiso
de IAM, cola ni configuración de función.

**Permiso de aplicación nuevo** (en `auth.py`):

| Permiso | Roles | Para qué |
|---|---|---|
| `borrar_encuesta` | admin, dpo | Borrar una encuesta entera (R-EU.9): «estos datos no tienen que estar más» |

**COLOQUIO: hay que avisarle.** Dos cosas de su superficie cambian de
contenido, no de forma. Las firmas y las columnas son las mismas, y la
batería sigue en 18 de 18:

- **`contacto_para_convocatoria` exige que la persona haya sido
  convocada** (`participacion.convocado`). Una participación que nació de
  un archivo de respuestas ya no cuenta como convocatoria activa.
- **`v_fatiga_panelista` cuenta también las encuestas
  independientes** (sin panel). Van a ver números de fatiga algo más altos.

---

## La migración es el punto de no retorno

Todo lo demás es código y se revierte desplegando la versión anterior. La
`0032` cambia datos. Por eso tiene:

- dos controles propios: la **corrida de prueba** (punto 2) y la
  **verificación** (punto 3);
- el **respaldo** como red (punto 1).

Y por eso se aplica y se verifica **antes** de desplegar el código nuevo,
no al revés. Es exactamente lo que falló con la `0010` de unidades de
evidencia.

**Ventana de mantenimiento.** Entre el paso 4 (aplicar la `0032`) y el
paso 7 (desplegar), el código viejo sigue en producción y la base ya es la
nueva:

- Las secciones Cargas, temáticas y clientes del código viejo fallan
  (`carga_tematica` y `carga_cliente` ya no existen).
- Una ingesta nueva no anda.

Hacerlo fuera de horario, avisar al equipo y no dejar pasar tiempo entre
esos pasos. La `0032` **no corre si hay una ingesta en curso**: se detiene
con `Hay ingestas en curso…`.

### Puntos de control

| # | Antes de seguir, verificar | Paso | Si falla |
|---|---|---|---|
| 1 | Respaldo de las dos bases hecho y confirmado | 2 | No se empieza |
| 2 | La **corrida de prueba** informa lo que va a crear y los números cierran (una encuesta por carga, una carga por encuesta con respuestas) | 3 | Se corrige la migración antes de escribir |
| 3 | Después de migrar: toda carga tiene encuesta, ninguna respuesta sin cuestionario, y los filtros de temáticas y clientes devuelven las mismas personas que antes | 5 | **Se restaura el respaldo**; no se despliega el código |
| 4 | `verificar_esquema.py` en verde para las dos bases | 6 | No se despliega el código |
| 5 | Una carga de prueba de punta a punta: revisión, temáticas, participación, sin duplicados al cargarla dos veces | 8 | Se revierte el despliegue |
| 6 | Batería de COLOQUIO en verde | 9 | Se revierte el despliegue |

### Orden en una línea

respaldo → corrida de prueba → `0032` + `0012` → `semantico --aplicar` →
verificar → verificar esquema → desplegar → carga de prueba → COLOQUIO.

---

## Qué trae

### La encuesta es el estudio; la carga, un archivo de ese estudio

- **Encuestas** es la casa de los estudios. Cada encuesta tiene:
  - sus **datos del estudio**: nombre, fecha de campo, público objetivo,
    descripción y clientes;
  - su **audiencia**: un panel, o la propia de una encuesta independiente;
  - sus **cargas**: cada archivo que se ingestó, con quién, cuándo, el modo,
    cuántas personas trajo y cuántas creó;
  - sus **temáticas**, que valen para todo el estudio.
- **Ingestar respuestas** se hace siempre desde una encuesta: **+ Ingestar
  respuestas** abre una carga nueva. Solo pide los datos del archivo (modo
  de relevamiento, tipo de datos); los del estudio ya están en la encuesta.
- **Cargar panelistas** (en Panelistas) sigue igual para quien lo usa. Por
  detrás crea una encuesta **independiente y cerrada** con los datos del
  estudio, y la carga es su primera carga.
- **Varias cargas, un estudio, sin duplicados** (R-EU.3):
  - todas las cargas de una encuesta escriben **el mismo cuestionario**;
  - una persona que aparece en dos cargas tiene **una** respuesta por
    pregunta (la de la última carga) y **una** participación;
  - una parcial seguida de la final deja lo de la final.
- **La revisión avisa, antes de procesar**:
  - si el archivo es **idéntico** al de otra carga de la encuesta. Seguir
    exige marcar la casilla;
  - en una encuesta con audiencia, quiénes **no estaban convocados**. Se
    incorporan marcados «no convocado», o se excluyen con la casilla;
  - **por finalidad**, cuántos quedan sin consentimiento y qué implica: sin
    contacto, no se los convoca; sin uso semántico, sus respuestas no
    entran al store semántico. Se registra como vino, y nadie va a la lista
    negra por no consentir.
- **Temáticas** (R-EU.2): la primera carga propone para todas las
  preguntas. Las siguientes proponen solo para las preguntas nuevas, y si no
  hay ninguna, no llaman a Claude.

### Encuestas independientes y composición (R-EU.5, R-EU.11)

- **Consultas → Crear encuesta**, en la barra de acciones de un resultado:
  - crea una encuesta independiente con la **foto** de ese resultado como
    audiencia;
  - deja afuera a la lista negra y a quien no tiene `contacto_participacion`,
    y lo informa;
  - si la consulta era semántica, registra la reidentificación, como «ver
    quiénes son».
- Una encuesta independiente **no tiene muestreo ni optimizador**: eligen
  entre los miembros de un panel.
- **Composición → Una encuesta** (o «Convocados vs respondentes» desde la
  encuesta): la composición de quienes respondieron al lado de la de los
  convocados, y la **tasa de respuesta de cada segmento**. Sin audiencia,
  solo respondentes.

### Archivar paneles, borrar una encuesta, borrar una carga (R-EU.8 a R-EU.10)

- **Paneles → Archivar…**:
  - un panel archivado no recibe encuestas ni convocatorias;
  - se oculta, salvo con **Incluir archivados**;
  - se reactiva con **Reactivar**;
  - no se archiva un panel con encuestas en campo.
- **Borrar la encuesta…** (admin y dpo), desde su detalle:
  - vista previa, y confirmar escribiendo el nombre;
  - borra sus respuestas del store semántico, participaciones, cargas y
    temáticas por panelista;
  - **conserva a las personas**;
  - es reanudable y queda auditado en `encuesta_borrado`;
  - no se puede con una ingesta en curso.
- **Borrar una carga** dentro de una encuesta con varias:
  - borra solo las respuestas que esa carga escribió;
  - quien estaba solo en esa carga pierde la participación, o vuelve a «no
    respondió» si estaba convocado;
  - **dar de baja a las personas que la carga creó es una casilla**. Antes
    era automático. Si se marca, va por `bajas.retirar()` como siempre, con
    aviso a COLOQUIO.

### Rutas nuevas o cambiadas

| Ruta | Permiso | Qué hace |
|---|---|---|
| `GET /encuestas?tipo=` | `leer` | Filtra `panel` o `independiente`. Cada fila trae `tipo`, `panel_estado`, `cargas`, `clientes`, `no_convocados` |
| `GET /encuestas/<id>` | `leer` | La encuesta completa: datos del estudio, `clientes`, `tematicas`, `cargas`, `convocados`, `respondieron`, `no_convocados` |
| `POST /encuestas` | `fieldear` | Acepta `publico_objetivo`, `descripcion`, `clientes`. `409` si el panel está archivado |
| `PATCH /encuestas/<id>` | `fieldear` | Además del `estado`, edita los datos del estudio |
| `POST /encuestas/desde-consulta` | `fieldear` | R-EU.5. Devuelve la encuesta y su `audiencia` (convocados, en lista negra, sin consentimiento, no encontrados) |
| `POST /encuestas/<id>/cargas` | `ingestar` | R-EU.1. Una carga nueva de la encuesta |
| `PUT /encuestas/<id>/tematicas` | `ingestar` | Las temáticas del estudio |
| `POST /encuestas/<id>/borrado` | `borrar_encuesta` | R-EU.9. `solo_revisar` da la vista previa; `confirmacion` = nombre exacto |
| `POST /cargas/<id>/ingesta` | `ingestar` | La revisión trae `encuesta`, `archivo_repetido`, `no_convocados`, `consentimiento_por_finalidad`. Confirmar acepta `confirmar_archivo_repetido` y `excluir_no_convocados` |
| `POST /cargas/<id>/borrado` | `borrar_carga` | Acepta `dar_de_baja_creadas` |
| `GET /paneles?incluir_archivados=1` | `leer` | Los archivados, ocultos por defecto |
| `PATCH /paneles/<id>` | `gestionar_paneles` | `estado: archivado \| activo`. `409` con encuestas en campo |
| `GET /composicion?ambito=encuesta&encuesta_id=` | `leer` | R-EU.11 |
| ~~`POST /encuestas/<id>/ingesta`~~, ~~`/encuestas/<id>/sav/analizar`~~, ~~`/encuestas/<id>/sav/ingesta`~~ | — | **Eliminadas.** La ingesta de una encuesta es la de una de sus cargas |
| `POST /encuestas/<id>/muestreo`, `/optimizar`, `/comparar` | — | `409` para una encuesta independiente |

---

## Paso 1 · Túneles y DSN

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
cloud-sql-proxy gestion-paneles:$REGION:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
psql "$DSN_SEMANTICA" -tAc "select current_database()"
```

Que no haya ingestas en curso (la `0032` lo vuelve a comprobar):

```bash
psql "$DSN_BOVEDA" -tAc "select count(*) from ingesta_trabajo where estado in ('encolada','procesando')"
```

## Paso 2 · Respaldo — punto de control 1

```bash
gcloud sql backups create --instance=paneles-boveda --project="$PROYECTO" \
  --description="antes de encuestas y cargas unificadas boveda/0032"
gcloud sql backups create --instance=paneles-semantica --project="$PROYECTO" \
  --description="antes de encuestas y cargas unificadas semantica/0012"
gcloud sql backups list --instance=paneles-boveda --project="$PROYECTO" --limit=1
gcloud sql backups list --instance=paneles-semantica --project="$PROYECTO" --limit=1
```

Los dos tienen que figurar con estado `SUCCESSFUL`. Anotar sus ids: son los
que se restauran si el punto 3 falla. **Sin respaldo confirmado no se
empieza.**

## Paso 3 · Corrida de prueba — punto de control 2

```bash
python3 scripts/migrar_encuestas_y_cargas.py prueba --guardar antes.json
```

- Ejecuta **el mismo archivo** `0032` que se aplica en el paso 4, dentro de
  una transacción que se deshace. No hay una segunda versión de la
  migración que pueda decir otra cosa.
- Informa lo que va a crear.
- Compara, para cada temática y cada cliente, las personas que devuelve el
  filtro antes y después.
- Guarda la foto de «antes» para el paso 5.
- No escribe nada.

Salida del ensayo (bóveda con una carga con cliente, temática y tres
personas, una encuesta de panel con dos respuestas por el flujo viejo y
otra sin respuestas):

```
Foto de «antes» guardada en antes.json.
Corrida de prueba de la 0032 (no se escribió nada):
  cargas existentes ............ 1
  encuestas a crear ............ 1 (una por carga: tiene que ser 1)
  encuestas con respuestas ..... 1
  cargas a crear ............... 1 (una por encuesta con respuestas: tiene que ser 1)
  participaciones a crear ...... 3
  vínculos persona-carga a crear 2
  cargas sin encuesta después .. 0
  cuestionarios sin encuesta ... 0
  filtros de temáticas y clientes con otras personas: 0
Los números cierran.
```

Tiene que terminar en **«Los números cierran.»** y salir con `0`:

- **encuestas a crear** = cargas existentes (una encuesta por carga);
- **cargas a crear** = encuestas con respuestas que no tenían carga;
- **cargas sin encuesta después** = 0;
- **cuestionarios sin encuesta** = 0;
- **filtros con otras personas** = 0.

Si no cierra, **no se sigue**: se corrige la migración. `antes.json` no
lleva datos personales: son ids de temáticas, clientes y personas.

## Paso 4 · Aplicar las migraciones

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0032_encuestas_y_cargas_unificadas.sql
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0012_respuesta_carga.sql
python3 scripts/migrar_encuestas_y_cargas.py semantico            # ensayo
python3 scripts/migrar_encuestas_y_cargas.py semantico --aplicar
```

La `0032` termina en `COMMIT`. Avisa con tres `NOTICE … does not exist,
skipping`: son los `drop … if exists` de las restricciones y del trigger
que crea, y es lo esperado.

Qué hace la `0032`, en una sola transacción:

- **Encuesta:**
  - `panel_id` admite nulo;
  - agrega `tipo` (`panel` | `independiente`, con un `check` que lo ata al
    panel), `publico_objetivo` y `descripcion`;
  - `ref_estudio` pasa a ser único.
- **`participacion.convocado`**: `true` para las que nacieron de una
  convocatoria, `false` para las importadas.
- **Carga:**
  - `encuesta_id`, obligatorio al final;
  - `archivo_huella`;
  - `ref_estudio` deja de ser único: lo copia un trigger desde la encuesta.
- **Los datos:**
  - una encuesta independiente y cerrada por cada carga, con el **mismo
    `ref_estudio`**, sus datos, clientes y temáticas. El cuestionario
    semántico queda vinculado sin tocarlo y **no se re-embebe nada**;
  - una carga por cada encuesta con respuestas, que las representa;
  - la participación de cada persona de cada carga.
- **Clientes y temáticas, al estudio:**
  - `encuesta_cliente` y `encuesta_tematica` reemplazan a `carga_cliente` y
    `carga_tematica`;
  - `tematica_pregunta` y `persona_tematica` cambian su clave a
    `encuesta_id`;
  - `v_persona_cliente` se rehace sobre las participaciones con respuesta.
- **`encuesta_borrado`**, la auditoría de R-EU.9.
- **COLOQUIO**: `v_fatiga_panelista` y `contacto_para_convocatoria` con
  `create or replace` y la misma firma. Los `grant` se conservan.
- RLS y `revoke … from public` en lo nuevo. Nada nuevo tiene `grant` a
  COLOQUIO.

`semantica/0012` agrega `respuesta.carga_id` (sin FK: es otro store) y su
índice. Es aditiva.

`semantico --aplicar` completa `respuesta.carga_id` de lo que ya estaba,
**solo para las encuestas con una sola carga**, que después de la `0032`
son todas. Sin `--aplicar` dice cuántas va a asignar y no escribe.

## Paso 5 · Verificar — punto de control 3

```bash
python3 scripts/migrar_encuestas_y_cargas.py verificar --antes antes.json
```

Salida del ensayo:

```
$ python3 scripts/migrar_encuestas_y_cargas.py semantico
5 respuesta(s) a asignar a 2 carga(s). Sin carga si se aplica: 0.
Ensayo: no se escribió nada. Para aplicar: --aplicar
$ python3 scripts/migrar_encuestas_y_cargas.py semantico --aplicar
5 respuesta(s) asignadas a 2 carga(s). Sin carga después: 0.
$ python3 scripts/migrar_encuestas_y_cargas.py verificar --antes antes.json
✓ Toda carga tiene encuesta, ninguna respuesta queda sin cuestionario de una encuesta y los filtros devuelven las mismas personas.
```

Tiene que salir con `0` y la línea **✓**:

- toda carga tiene encuesta;
- ninguna respuesta queda sin cuestionario de una encuesta;
- los filtros de temáticas y clientes devuelven las mismas personas que en
  `antes.json`.

**Si dice «La verificación FALLÓ»: se restauran los dos respaldos del paso
2 y no se despliega el código.**

```bash
gcloud sql backups restore <ID_BOVEDA> --restore-instance=paneles-boveda --project="$PROYECTO"
gcloud sql backups restore <ID_SEMANTICA> --restore-instance=paneles-semantica --project="$PROYECTO"
```

## Paso 6 · Verificar el esquema — punto de control 4

```bash
python3 scripts/verificar_esquema.py
```

Verde en las dos bases. Sin la `0032`, nombra como faltantes `encuesta.tipo`,
`participacion.convocado`, `carga.encuesta_id`, `encuesta_cliente`,
`encuesta_tematica`, `encuesta_borrado` y otros. Sin la `0012`, falta
`respuesta.carga_id`. `carga_cliente` y `carga_tematica` ya no se exigen:
la `0032` las quitó, y quedan anotadas en `esquema.RETIRADOS`.

## Paso 7 · Desplegar

```bash
firebase deploy --only functions,hosting
```

## Paso 8 · Carga de prueba de punta a punta — punto de control 5

Con una encuesta de prueba de un panel de prueba:

1. Convocar a 3 panelistas.
2. **+ Ingestar respuestas** con un archivo de esos 3 más uno que no estaba
   convocado.
   - La revisión dice «1 persona del archivo no estaba convocada» y muestra
     el consentimiento por finalidad.
   - Proponer temáticas trae la propuesta.
3. Confirmar. Al terminar, la encuesta muestra **4 respondieron, 1 sin estar
   convocado**, y una carga.
4. **+ Ingestar respuestas** otra vez con **el mismo archivo**.
   - La revisión avisa que es idéntico al de la carga anterior.
   - Sin marcar la casilla no deja seguir; marcada, procesa.
5. Comprobar que no hay duplicados:

   ```sql
   -- bóveda: una participación por persona
   select count(*), count(distinct id_persona) from participacion where encuesta_id = <ID>;
   ```

   ```sql
   -- semántico: una respuesta por persona y pregunta, de la última carga
   select r.carga_id, count(*), count(distinct (r.individuo_id, r.pregunta_id))
     from respuesta r join pregunta p on p.id = r.pregunta_id
     join cuestionario c on c.id = p.cuestionario_id
    where c.ref_estudio = '<REF_ESTUDIO>' group by r.carga_id;
   ```

   Los dos conteos de cada consulta tienen que ser iguales, y en el
   semántico tiene que haber una sola `carga_id`, la de la segunda carga.
6. **Composición → Una encuesta** → la de prueba: convocados 3, respondentes
   4, y la tasa por segmento.
7. Borrar la encuesta de prueba (admin): la vista previa coincide y las
   personas se conservan.

Si algo de esto falla, **se revierte el despliegue** (paso «Volver atrás»).

## Paso 9 · COLOQUIO — punto de control 6

```bash
python3 scripts/verificar_coloquio.py
```

Tiene que dar **18 de 18**. Después, avisar a COLOQUIO de los dos cambios
de contenido (arriba).

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_encuestas_y_cargas_unificadas.py` (22 pruebas), una o más por
  requisito:
  - R-EU.1: la carga desde Panelistas crea su encuesta, y cada persona queda
    con su participación;
  - R-EU.2 y R-EU.3: tres cargas de la misma gente no duplican personas,
    participaciones ni respuestas; parcial y final; el archivo repetido se
    avisa y exige confirmación;
  - R-EU.4: los no convocados en la revisión, marcados, y su exclusión;
  - R-EU.5: la encuesta desde una consulta sin lista negra ni sin contacto;
    sin muestreo;
  - R-EU.6: el consentimiento por finalidad y que nadie va a la lista negra;
  - R-EU.8: archivar, ocultar, no recibir encuestas ni convocatorias, no con
    encuestas en campo, reactivar;
  - R-EU.9: borrar el estudio conservando a la gente, por la ruta y con
    permiso; reanudar; no con una ingesta en curso;
  - R-EU.10: borrar una carga deshace solo lo suyo; la baja de las creadas,
    como opción;
  - R-EU.11: la tasa de respuesta por segmento, y solo respondentes sin
    audiencia.
- `tests/test_migracion_0032.py` (4 pruebas). Arma una bóveda **anterior**
  a la migración (`0001` a `0031`, en una base nueva), la siembra como
  estaba producción y comprueba:
  - que la corrida de prueba informa sin escribir;
  - que después de migrar todo cierra, incluido el semántico;
  - que no corre con una ingesta en curso;
  - que la verificación **puede fallar** si un filtro cambia.
- `tests/test_pantallas_encuestas_unificadas.py` (4 pruebas, en Chromium,
  sobre el modo demo):
  - el detalle de la encuesta como estudio, con sus cargas y temáticas;
  - una carga de Panelistas es una encuesta independiente, sin convocar;
  - «Convocados vs respondentes» abre la composición por encuesta con la
    tasa por segmento;
  - archivar oculta el panel, «Incluir archivados» lo muestra y se
    reactiva.
- Se actualizaron las pruebas que afirmaban el modelo anterior. Las rutas
  `/encuestas/<id>/sav/…` pasaron a la de la carga (`conftest.ingesta_de`).
  Las temáticas y los clientes se leen por encuesta. Una carga sin panel ya
  registra participación (no convocada). Borrar una carga da de baja solo
  con la opción.

---

## Volver atrás

- **El código** se revierte desplegando la versión anterior. Pero el código
  anterior **no anda** con la base migrada: lee `carga_tematica`,
  `carga_cliente` y `carga.fecha_estudio`.
- **La base** no tiene script de reversión: la `0032` crea encuestas,
  cargas y participaciones, y reescribe claves de temáticas. **Se vuelve
  atrás restaurando el respaldo del paso 2.** Todo lo que se haya cargado
  después del respaldo se pierde, y hay que volver a ingestarlo.
- Por eso los puntos de control 2 y 3 son **antes** de desplegar: si algo
  no cierra, se restaura sin haber perdido nada.
- **Después**, cuando el código nuevo lleve un tiempo en producción, una
  migración posterior puede quitar `carga.fecha_estudio` y
  `carga.publico_objetivo`, que quedaron sin uso.
