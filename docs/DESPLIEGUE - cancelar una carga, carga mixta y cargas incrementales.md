# Despliegue — cancelar una carga, carga mixta y cargas incrementales

**Pedidos:** `specs/PEDIDO_cancelar_carga.md` (R-CAN-C.1 a R-CAN-C.5),
`specs/PEDIDO_carga_mixta.md` (R-MIX.1 a R-MIX.5) y
`specs/PEDIDO_cargas_incrementales.md` (R-INC.1 a R-INC.6) ·
**Decisiones:** D92, D93, D94
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0036`** y el código de los
controles de mensajes (D91) desplegado.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | Meta | `functions/.env` | Código |
|---|---|---|---|---|---|---|
| | **`0037`** (cambia datos) | nada | nada | nada | nada | backend y frontend |

La `0037` hace tres cosas:

1. **D92 · lo provisorio.** `carga.provisoria` y `encuesta.provisoria`
   (`boolean not null default false`), y la tabla `limpieza_provisoria`
   (el registro de cada limpieza). Lo que ya existe queda **no provisorio**.
2. **D92 · detener una ingesta.** El estado de trabajo `detenida` y el de
   lote `cancelado`, en sus catálogos; `v_ingesta_progreso` suma
   `lotes_cancelados`, y la purga no se lleva las filas de un lote
   cancelado.
3. **D94 · `repetida`.** El `check` de `persona_carga.origen` admite
   `repetida`, y **lo ya cargado se migra**: en cada encuesta con más de una
   carga, los vínculos posteriores a la primera carga pasan a `repetida`.
   `v_carga_resumen` suma `personas_repetidas` y `provisoria`.

La carga mixta (D93) no necesita esquema: el ID de respuesta se guarda en
`alias_origen`, que ya existe.

**Qué NO cambia:**
- ningún secreto, rol de base, permiso de IAM, cola, función ni variable;
- **ningún webhook en Meta** (D86). Este despliegue no da de alta nada en
  Meta;
- **ningún proceso programado**: la limpieza de lo abandonado corre al
  iniciar cada carga (US$ 0);
- la superficie de COLOQUIO: la tabla nueva no tiene `grant` y no se toca
  ninguna vista ni función de su superficie. La batería tiene que seguir en
  **24 de 24**.

### Orden en una línea

proyecto fijado → sin ingestas en curso → **respaldo** → **lo vacío de antes,
listado** → **corrida de prueba** → `0037` → verificación de la migración →
`verificar_esquema.py` en verde → token de COLOQUIO y batería →
`firebase deploy` → comprobaciones.

La `0037` va **antes** del código: el código nuevo escribe `provisoria` y
`repetida`, y lee `limpieza_provisoria` y `lotes_cancelados`. El código
anterior no nota nada de lo que agrega la `0037` (las columnas tienen
default y el `check` es más amplio).

### Puntos de control

| # | Qué tiene que cumplirse | Paso | Si no se cumple |
|---|---|---|---|
| 1 | Ninguna ingesta encolada o procesando | 1 | Se espera a que terminen |
| 2 | Respaldo de la bóveda hecho y `SUCCESSFUL` | 2 | No se empieza |
| 3 | La corrida de prueba informa los vínculos que pasan a `repetida` y «cierra» | 4 | No se aplica la `0037`; se investiga |
| 4 | Después de migrar: `verificar` cierra (ninguna persona con más de un vínculo no `repetida` en una encuesta) | 6 | **Se restaura el respaldo**; no se despliega el código |
| 5 | `verificar_esquema.py` sin faltantes | 7 | No se despliega |
| 6 | Batería de COLOQUIO en 24 de 24 | 8 | No se despliega |

---

## Paso 1 · Preparar la terminal y fijar el proyecto — punto de control 1

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
gcloud config get-value project
git checkout main && git pull
ls db/boveda/0037_cancelar_carga_mixta_e_incrementales.sql scripts/migrar_cargas_incrementales.py
```

`gcloud config get-value project` tiene que decir **`gestion-paneles`**. Si
dice otra cosa, no seguir: el respaldo, el token y el despliegue irían a otro
proyecto.

Túnel y DSN de la bóveda:

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`. La precondición, la `0036`:

```bash
psql "$DSN_BOVEDA" -tAc "select to_regclass('encuesta_audiencia_agregado') is not null"
```

Tiene que dar `t`. Si da `f`, primero
`docs/DESPLIEGUE - mensajes, canales, selección aleatoria y audiencias.md`.

Que no haya una ingesta en curso (la migración reescribe `v_ingesta_progreso`
y marca vínculos de cargas):

```bash
psql "$DSN_BOVEDA" -tAc "select count(*) from ingesta_trabajo where estado in ('encolada','procesando')"
```

Tiene que dar `0`. Si no, esperar a que terminen.

## Paso 2 · Respaldo — punto de control 2

La `0037` cambia datos (los vínculos que pasan a `repetida`). Antes que nada,
el respaldo:

```bash
gcloud sql backups create --instance=paneles-boveda --project="$PROYECTO" \
  --description="antes de cargas provisorias, mixtas e incrementales boveda/0037"
gcloud sql backups list --instance=paneles-boveda --project="$PROYECTO" --limit=1
```

Tiene que figurar con estado `SUCCESSFUL`. Anotar su id: es el que se
restaura si el punto 4 falla. **Sin respaldo confirmado no se empieza.** El
store semántico no se toca en este despliegue.

## Paso 3 · Lo vacío que dejaron las cancelaciones de antes

Hasta este despliegue, cancelar una carga dejaba la carga —y, desde
Panelistas, su encuesta automática— creadas y vacías. La `0037` **no las
borra**: las deja como no provisorias, porque no hay forma de saber cuáles se
abandonaron y cuáles se crearon a propósito. Se listan para que el equipo
decida:

```bash
python3 scripts/migrar_cargas_incrementales.py vacias
```

Lista las cargas sin personas ni trabajo de ingesta y las encuestas
independientes sin respuestas. **No borra nada.** Mandarle la lista a
operaciones; lo que decidan borrar se borra después del despliegue, desde la
sección Cargas (Borrar) y desde Encuestas (Borrar).

## Paso 4 · Corrida de prueba — punto de control 3

```bash
python3 scripts/migrar_cargas_incrementales.py prueba
```

- Ejecuta **el mismo archivo** `0037` que se aplica en el paso 5, en una
  transacción que se deshace: no se escribe nada.
- Informa, por encuesta, cuántos vínculos pasan a `repetida` (solo las
  encuestas con más de una carga).
- Termina con «Personas con más de un vínculo no repetida en una encuesta
  después de aplicar: 0».

Salida esperada, por ejemplo:

```
Corrida de prueba de la 0037 (deshecha: no se escribió nada)
  encuesta 41 «Ómnibus octubre» · 3 cargas · 1460 vínculos · pasan a repetida: 812
  Total de vínculos que pasan a `repetida`: 812
  Personas con más de un vínculo no repetida en una encuesta después de aplicar: 0
```

Sale con `0` si cierra y con `1` si no. Con `1`, **no** se aplica la `0037`:
se investiga. Anotar el total: el paso 6 lo compara.

## Paso 5 · Aplicar `boveda/0037`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0037_cancelar_carga_mixta_e_incrementales.sql
```

Una sola transacción. Puede avisar con algunos `NOTICE … skipping` (los
`if not exists` y `drop … if exists`). Termina en `COMMIT`.

## Paso 6 · Verificar la migración — punto de control 4

```bash
python3 scripts/migrar_cargas_incrementales.py verificar
```

Tiene que decir **«Cierra.»**, con tantos vínculos `repetida` como el total
de la corrida de prueba. La consulta del Definition of Done, por SQL:

```sql
select c.encuesta_id, pc.id_persona, count(*) as vinculos
  from persona_carga pc join carga c on c.id = pc.carga_id
 where pc.origen <> 'repetida'
 group by 1, 2 having count(*) > 1;
```

Cero filas. Y lo existente quedó no provisorio:

```sql
select (select count(*) from carga where provisoria) as cargas,
       (select count(*) from encuesta where provisoria) as encuestas;
```

`0 | 0`.

**Si no cierra: se restaura el respaldo del paso 2 y no se despliega el
código.**

```bash
gcloud sql backups restore <ID_DEL_PASO_2> --restore-instance=paneles-boveda \
  --project="$PROYECTO"
```

## Paso 7 · Verificar el esquema — punto de control 5

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0037` nombra como faltantes `carga.provisoria`,
`encuesta.provisoria` y `limpieza_provisoria`. Con ella, **nada**. Tiene que
estar en verde **antes** del `firebase deploy`.

## Paso 8 · Token de COLOQUIO y su batería — punto de control 6

El token dura una hora: se genera **ahora**, inmediatamente antes de la
batería.

```bash
source scripts/token_coloquio.sh
python3 scripts/verificar_coloquio.py
```

Tiene que terminar en **24 de 24**, sin fallidos. La `0037` no toca grants,
vistas ni funciones de la superficie (la tabla nueva no tiene `grant`, y
`purgar_ingestas_terminadas` queda revocada de `public`).

## Paso 9 · Desplegar

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
```

No se crea ninguna función ni se pide ningún secreto nuevo.

```bash
gcloud functions list --regions="$REGION" --format='value(name)' | grep -c whatsappwebhook
```

Tiene que dar **`0`**.

## Paso 10 · Comprobaciones en la aplicación

**D92 · cancelar y detener.**
- [ ] **Panelistas → Cargar panelistas**: poner un nombre, **Continuar**, y en
      el modal del archivo apretar **Cancelar**. La carga no aparece en
      **Cargas** ni su encuesta en **Encuestas**.
- [ ] En una encuesta de prueba, **Ingestar respuestas** → **Continuar** y
      cerrar el modal con la ×: la encuesta sigue igual, sin una carga nueva.
- [ ] Confirmar una carga chica de prueba: en el seguimiento se lee
      «El procesamiento sigue en el servidor aunque cierres esta ventana…» y
      el botón **Detener ingesta** (con una carga de un solo lote puede
      terminar antes de poder apretarlo; no hace falta forzarlo, lo cubren
      las pruebas automáticas).
- [ ] **Cumplimiento → Cargas provisorias abandonadas**: aparece la limpieza
      que corrió al iniciar las cargas de arriba; **Limpiar ahora** suma una
      fila «a mano».

**D93 · carga mixta.**
- [ ] El modal de la carga ya no tiene «4 · ¿Los panelistas ya están en el
      sistema?». En el paso 2 aparece **ID de respuesta de la plataforma
      (opcional)**.
- [ ] Un archivo de Alchemer con filas con `id_persona` y filas vacías: la
      revisión dice «N filas · X existentes · Y nuevas», y de las nuevas
      cuántas se crean. Con nuevas para crear, pide el paso 4 (base legal del
      alta) y no deja confirmar hasta declararlo. **Volver atrás sin
      importar** y cancelar.

**D94 · cargas incrementales.** En una encuesta que la migración marcó (paso
4), y en la ficha de alguien que estaba en más de una de sus cargas:
- [ ] La ficha muestra esa encuesta **una vez**, con la primera carga y
      «también en N cargas posteriores de esta encuesta».
- [ ] El detalle de la segunda carga separa **nuevos**, **reutilizados de
      otros estudios** y **ya estaban en cargas anteriores de esta encuesta**.
- [ ] **Composición → una carga**: la nota «Muestra quiénes sumó esta
      carga…» y menos personas que antes.

---

## Qué mirar después

**Cargas provisorias que no se limpian** (más de 24 h y todavía ahí: tienen
un trabajo de ingesta, o nadie inició una carga desde entonces):

```sql
select c.id, c.nombre, c.creado_en,
       exists (select 1 from ingesta_trabajo t
                where t.destino_tipo = 'carga' and t.destino_id = c.id) as con_trabajo
  from carga c where c.provisoria and c.creado_en < now() - interval '24 hours';
```

**Ingestas detenidas que nadie reanudó ni borró:**

```sql
select t.id, t.destino_id as carga_id, t.terminado_en,
       count(*) filter (where l.estado = 'cancelado') as lotes_cancelados
  from ingesta_trabajo t join ingesta_lote l on l.trabajo_id = t.id
 where t.estado = 'detenida' group by 1, 2, 3 order by 3;
```

La purga no les borra las filas a los lotes cancelados: se pueden reanudar
cuando sea.

**Filas con id desconocido** en las cargas de la última semana (enlaces
rotos: un id mal copiado o alguien que se dio de baja):

```sql
select t.id, t.destino_id as carga_id, t.creado_en,
       (t.plan->'reconocimiento'->>'id_desconocido')::int as id_desconocido,
       (t.plan->'reconocimiento'->>'a_crear')::int as creadas
  from ingesta_trabajo t
 where t.plan ? 'reconocimiento' and t.creado_en > now() - interval '7 days'
 order by t.creado_en desc;
```

---

## Costos

**US$ 0.** La limpieza corre al iniciar cada carga, sin un proceso
programado. Si más adelante se prefiere un barrido programado, Cloud
Scheduler cuesta del orden de US$ 0,10 por tarea al mes fuera de la capa
gratuita. La carga mixta no agrega llamadas a nadie: el reconocimiento son
consultas a la bóveda.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_pedido_cancelar_mixta_incrementales.py`:
  - **D92:** cancelar desde Panelistas no deja carga ni encuesta; desde una
    encuesta existente no la toca; confirmar deja de ser provisoria; una
    provisoria de más de 24 h se limpia al iniciar otra (y una con trabajo
    no); la limpieza a mano queda registrada; detener deja los pendientes
    sin procesar y el trabajo en «detenida»; una tarea de un lote cancelado
    no procesa nada; un lote en curso termina; reanudar completa los
    cancelados; una detenida no bloquea una ingesta nueva del mismo
    destino;
  - **D93:** un archivo mixto carga a las existentes y crea a las nuevas con
    sus respuestas; un `id_persona` inexistente no crea a nadie; una nueva
    con correo registrado se reutiliza; con ID de respuesta, una nueva se
    reconoce en la carga siguiente (y el de otra encuesta no cruza); sin él,
    la vista previa avisa las nuevas sin clave; sin nuevas no se pide
    evidencia y con nuevas se aplica solo a ellas; la proporción inesperada
    se destaca; en una encuesta con audiencia las nuevas quedan «no
    convocado»; la pantalla ya no tiene la pregunta 4;
  - **D94:** tres cargas incrementales dejan `creada`/`reutilizada` en la
    primera y `repetida` en las otras; la ficha agrupa por encuesta; el
    detalle separa nuevos, reutilizados y ya estaban; el desglose de
    reutilizados y la composición de una carga excluyen a los repetidos;
    borrar la primera carga reasigna la siguiente como `reutilizada` y no da
    de baja a nadie; la migración marca lo ya cargado y es re-ejecutable.
- `tests/test_pantallas_cancelar_mixta_incrementales.py` (Chromium): sin
  pregunta 4 y con el ID de respuesta; cancelar y cerrar con la × borran la
  provisoria; la revisión con existentes y nuevas, la proporción destacada y
  la evidencia pedida; confirmar no cancela, el seguimiento ofrece detener y,
  detenida, reanudar y borrar; la ficha agrupada; el detalle de la carga; la
  limpieza desde Cumplimiento.

---

## Volver atrás

**El código:** desplegar la versión anterior. La `0037` es compatible hacia
atrás: el código anterior no escribe `provisoria` (las cargas nuevas quedan
no provisorias, como antes) ni conoce `repetida` (registra `reutilizada`, que
el `check` sigue admitiendo).

Antes de volver atrás, dos cosas que el código anterior no sabe leer:
- **cargas provisorias**: con el código anterior aparecerían de nuevo en los
  listados. Limpiarlas desde Cumplimiento antes, o:

  ```sql
  select id, nombre, creado_en from carga where provisoria;
  ```

- **ingestas detenidas**: el código anterior no tiene «Reanudar». Reanudarlas
  o borrar lo cargado antes.

**La migración**, si hiciera falta sacarla: restaurar el respaldo del paso 2.
Los vínculos `repetida` se podrían volver atrás a `reutilizada` por SQL, pero
se perdería la distinción que la `0037` vino a dar.
