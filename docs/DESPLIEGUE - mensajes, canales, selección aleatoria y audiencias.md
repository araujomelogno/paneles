# Despliegue — mensajes con Flow, canales en la carga, selección aleatoria y audiencias

**Pedido:** `specs/PEDIDO_unificar_mensajes_y_canal_en_carga_06_11_26.md`
(partes 1 a 4) · **Decisiones:** D87, D88, D89 y D90
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0035`** (convocatoria y
mensajes, D85) y su código desplegado.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | Meta | `functions/.env` | Código |
|---|---|---|---|---|---|---|
| | `0036` (**cambia datos**) | nada | nada | nada | opcional: `COMPOSICION_BASE_CHICA` | backend y frontend |

- **La `0036`** (una sola transacción, re-ejecutable):
  - `encuesta_mensaje.boton` y `encuesta_mensaje.flow_id` (D87);
  - **migra lo enviado por la sección «WhatsApp Flow»**: un mensaje
    «WhatsApp Flow» por cada encuesta que la usó, y una fila en `envio` por
    participación enviada o fallida. Es lo que impide que «No enviado aún»
    le repita el Flow a quien ya lo recibió;
  - `carga_canal_registro`, la auditoría de los canales de una carga (D88);
  - `consulta_ejecucion.seleccion` y `.semilla` (D89);
  - `encuesta_audiencia_agregado`, cómo se armó cada audiencia (D90);
  - el origen `manual` para la encuesta independiente creada desde la
    sección Encuestas (D90).

  **No borra** columnas: `participacion.enviado_en`, `envio_estado`,
  `envio_error` y `encuesta.flow_*` quedan, sin uso, hasta una versión
  posterior.
- **Se quitan rutas**: `PUT/GET /encuestas/<id>/flow`,
  `GET/POST /encuestas/<id>/whatsapp` y `GET /whatsapp/plantillas`, y el
  permiso `enviar_whatsapp` (lo reemplaza `enviar_mensajes`, mismos roles).
  Ninguna la usa COLOQUIO.
- **Rutas nuevas**: `GET/POST /cargas/<id>/canales`,
  `GET/POST /encuestas/<id>/audiencia`. Cambian (compatibles):
  `GET /encuestas` (filtros), `POST /encuestas` (`tipo`),
  `GET /encuestas/<id>/participacion` (paginada), `POST /consultas`
  (`seleccion`, `semilla`, `pagina`), `POST /encuestas/desde-consulta`
  (`segmento_completo`, `cantidad`), `GET /composicion` (`base_chica`).

**Qué NO cambia:**
- ningún secreto, rol de base, permiso de IAM, cola ni función;
- **ningún webhook en Meta**: la URL de callback de la app ya la usa otro
  sistema (D86). Este despliegue no da de alta nada en Meta;
- la superficie de COLOQUIO: las tablas nuevas no tienen `grant` y no se
  toca ninguna vista ni función de su superficie. La batería tiene que
  seguir en **24 de 24**.

### Orden en una línea

proyecto fijado → **respaldo** → **corrida de prueba** → `0036` →
verificación de la migración → `verificar_esquema.py` en verde → token de
COLOQUIO y batería → `firebase deploy` → comprobaciones.

La `0036` va **antes** del código: el código nuevo escribe `boton` y
`flow_id`, lee las tablas nuevas y ya no escribe en `participacion.envio_*`.
El código anterior no nota nada de lo que agrega la `0036`.

### Puntos de control

| # | Qué tiene que cumplirse | Paso | Si no se cumple |
|---|---|---|---|
| 1 | Respaldo de la bóveda hecho y `SUCCESSFUL` | 2 | No se empieza |
| 2 | La corrida de prueba informa los mensajes y envíos a crear y «cierra» | 3 | No se aplica la `0036`; se investiga |
| 3 | Después de migrar: `verificar` cierra (ninguna participación enviada sin su fila en `envio`) | 5 | **Se restaura el respaldo**; no se despliega el código |
| 4 | `verificar_esquema.py` sin faltantes | 6 | No se despliega |
| 5 | Batería de COLOQUIO en 24 de 24 | 7 | No se despliega |

---

## Paso 1 · Preparar la terminal y fijar el proyecto

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
gcloud config get-value project
git checkout main && git pull
ls db/boveda/0036_mensajes_canales_seleccion_y_audiencias.sql scripts/migrar_flow_a_mensajes.py
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

Tiene que devolver `paneles_boveda`. La precondición, la `0035`:

```bash
psql "$DSN_BOVEDA" -tAc "select to_regclass('envio') is not null"
```

Tiene que dar `t`. Si da `f`, primero
`docs/DESPLIEGUE - convocatoria y mensajes.md`.

Que no haya un envío ni una ingesta en curso (la migración escribe en
`envio`):

```bash
psql "$DSN_BOVEDA" -tAc "select count(*) from ingesta_trabajo where estado in ('encolada','procesando')"
```

Tiene que dar `0`. Si no, esperar a que terminen.

## Paso 2 · Respaldo — punto de control 1

La `0036` cambia datos. Antes que nada, el respaldo:

```bash
gcloud sql backups create --instance=paneles-boveda --project="$PROYECTO" \
  --description="antes de mensajes, canales y seleccion boveda/0036"
gcloud sql backups list --instance=paneles-boveda --project="$PROYECTO" --limit=1
```

Tiene que figurar con estado `SUCCESSFUL`. Anotar su id: es el que se
restaura si el punto 3 falla. **Sin respaldo confirmado no se empieza.** El
store semántico no se toca en este despliegue.

## Paso 3 · Corrida de prueba — punto de control 2

```bash
python3 scripts/migrar_flow_a_mensajes.py prueba
```

- Ejecuta **el mismo archivo** `0036` que se aplica en el paso 4, en una
  transacción que se deshace: no se escribe nada.
- Informa cuántas encuestas tienen envíos de la sección vieja, cuántos
  mensajes «WhatsApp Flow» y cuántos envíos (aceptados y fallidos) va a
  crear.
- Termina con «Participaciones enviadas sin su fila en `envio` después de
  aplicar: 0».

Salida esperada, por ejemplo:

```
Corrida de prueba de la 0036 (deshecha: no se escribió nada)
  En participacion: 3 encuesta(s) con envíos de la sección vieja · 1840 enviados · 12 fallidos
  Va a crear: 3 mensaje(s) «WhatsApp Flow» y 1852 envío(s) (1840 aceptados, 12 fallidos)
  Participaciones enviadas sin su fila en `envio` después de aplicar: 0
```

Sale con `0` si cierra y con `1` si no. Con `1`, **no** se aplica la `0036`:
se investiga. Anotar los números: el paso 5 los compara.

## Paso 4 · Aplicar `boveda/0036`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0036_mensajes_canales_seleccion_y_audiencias.sql
```

Una sola transacción. La primera vez puede avisar con algunos
`NOTICE … does not exist, skipping` (los `drop constraint if exists`).
Termina en `COMMIT`.

## Paso 5 · Verificar la migración — punto de control 3

```bash
python3 scripts/migrar_flow_a_mensajes.py verificar
```

Tiene que decir **«Cierra.»**: tantos aceptados y fallidos como la corrida de
prueba, y ninguna participación enviada sin su fila en `envio`.

Por SQL, los mensajes migrados:

```sql
select m.encuesta_id, m.nombre, m.plantilla, m.flow_id,
       count(e.*) filter (where e.estado = 'aceptado') as aceptados,
       count(e.*) filter (where e.estado = 'fallido')  as fallidos
  from encuesta_mensaje m left join envio e on e.encuesta_mensaje_id = m.id
 where m.creado_por = 'migracion_0036'
 group by 1, 2, 3, 4 order by 1;
```

**Si no cierra: se restaura el respaldo del paso 2 y no se despliega el
código.**

```bash
gcloud sql backups restore <ID_DEL_PASO_2> --restore-instance=paneles-boveda \
  --project="$PROYECTO"
```

## Paso 6 · Verificar el esquema — punto de control 4

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0036` nombra como faltantes `encuesta_mensaje.boton`,
`encuesta_mensaje.flow_id`, `carga_canal_registro`,
`consulta_ejecucion.seleccion`, `consulta_ejecucion.semilla` y
`encuesta_audiencia_agregado`. Con ella, **nada**. Tiene que estar en verde
**antes** del `firebase deploy`.

## Paso 7 · Token de COLOQUIO y su batería — punto de control 5

El token dura una hora: se genera **ahora**, inmediatamente antes de la
batería, no al principio de la sesión.

```bash
source scripts/token_coloquio.sh
python3 scripts/verificar_coloquio.py
```

Tiene que terminar en **24 de 24**, sin fallidos. Las dos tablas nuevas no
tienen `grant` y tienen `revoke all … from public`, así que la lista blanca
no cambia. Si algún chequeo de privilegios falla, la `0036` abrió algo de
más: no seguir.

## Paso 8 · Desplegar

Opcional, antes del despliegue: si el umbral de **base chica** de los cruces
no es 30, agregarlo a `functions/.env` (no es un secreto):

```
COMPOSICION_BASE_CHICA=30
```

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
```

No se crea ninguna función ni se pide ningún secreto nuevo. Que no haya
quedado ninguna función de webhook:

```bash
gcloud functions list --regions="$REGION" --format='value(name)' | grep -c whatsappwebhook
```

Tiene que dar **`0`**.

## Paso 9 · Comprobaciones en la aplicación

**Parte 1 — el Flow en Mensajes.**
- [ ] El detalle de una encuesta **no** tiene la sección «WhatsApp Flow».
- [ ] En las encuestas que la usaban hay un mensaje **«WhatsApp Flow»** con
      la marca *Flow*, y sus *Lo recibieron* / *Fallidos* coinciden con el
      paso 5.
- [ ] **Enviar…** sobre ese mensaje con **«No enviado aún»**: la vista
      previa **no** incluye a quienes ya lo habían recibido (sí a los
      fallidos). Cancelar sin enviar.
- [ ] **+ Nuevo mensaje** → WhatsApp: la lista dice *Botón de URL*, *Botón
      de Flow* o *Solo texto* en cada plantilla.
- [ ] El conteo de convocados es el mismo en el listado y en el detalle.

**Parte 2 — canales en la carga.** Antes de usarlo sobre una carga real
(ANTEL), hace falta la **validación de quien asesora en protección de
datos** sobre si su texto alcanza como aceptación del canal (ver
«Preguntas abiertas»).
- [ ] En el detalle de una carga de prueba, **Registrar canales aceptados**
      → WhatsApp con un texto publicado → **Ver vista previa**: cuántas
      quedan y cuántas no, por motivo → **Registrar**. La tarjeta muestra la
      fila con quién, cuándo, el texto y cuántas personas.
- [ ] En una encuesta de esa carga, la vista previa de un mensaje de
      WhatsApp ya no las deja afuera por «No aceptó WhatsApp como canal».

**Parte 3 — selección aleatoria.**
- [ ] Una consulta demográfica muestra *Selección aleatoria · semilla …*;
      **Volver a sortear** cambia la semilla y las personas; la página 2 no
      repite a nadie de la 1.
- [ ] Una consulta semántica con **Selección: Aleatorio entre los que
      cumplen** informa pool, verificadas, reemplazadas y quedaron.
- [ ] La ejecución guardó modo y semilla:

```sql
select alcance, tipo, seleccion, semilla, creado_en
  from consulta_ejecucion order by creado_en desc limit 5;
```

**Parte 4 — audiencias, listas y composición.**
- [ ] El listado de encuestas filtra por estado y por cliente.
- [ ] **+ Nueva encuesta** → *Independiente* crea una encuesta sin
      audiencia; **Agregar audiencia desde una consulta** abre Consultas con
      el aviso y el criterio de exclusión; **Agregar a la encuesta** suma
      gente y la tabla *Cómo se armó la audiencia* muestra el paso con su
      semilla.
- [ ] En una encuesta grande, la lista dice «Página 1 de N» y la búsqueda
      por nombre encuentra a una persona.
- [ ] En Composición, un cruce en **% de columna** muestra la base de cada
      columna y marca ⚠ las de menos de 30.

---

## Qué mirar después

**Que no se repitan envíos del Flow migrado** (tiene que dar cero filas):

```sql
select e.encuesta_mensaje_id, e.id_persona, count(*)
  from envio e join encuesta_mensaje m on m.id = e.encuesta_mensaje_id
 where m.boton = 'flow' and e.estado = 'aceptado'
 group by 1, 2 having count(*) > 1;
```

**Que nada escriba las columnas deprecadas** (tiene que dar cero después del
despliegue):

```sql
select count(*) from participacion
 where enviado_en > (select min(creado_en) from encuesta_mensaje
                      where creado_por = 'migracion_0036');
```

Cuando esto se mantenga en cero un tiempo, una versión posterior quita
`participacion.enviado_en`, `envio_estado`, `envio_error` y `encuesta.flow_*`.

**Los canales registrados en bloque**:

```sql
select c.nombre, r.via, r.canales, r.personas, r.actor_email, r.creado_en
  from carga_canal_registro r join carga c on c.id = r.carga_id
 order by r.creado_en desc limit 20;
```

**Audiencias armadas sin modo ni semilla** (métrica del pedido: cero después
del despliegue; toda participación agregada desde una consulta tiene su
paso registrado):

```sql
select via, modo, count(*), count(*) filter (where semilla is null) as sin_semilla
  from encuesta_audiencia_agregado group by 1, 2;
```

`sin_semilla` es esperable solo en modo `ranking`.

---

## Costos

| Concepto | Costo |
|---|---|
| Partes 1 y 2 | **US$ 0**: interfaz, una migración de datos y reglas sobre tablas existentes |
| Lo que habilita la parte 2 | **Poder enviar** a esas personas: cada mensaje de WhatsApp se paga según la tarifa de Meta (del orden de US$ 0,07 en marketing para Uruguay; confirmar con la vigente) |
| Selección aleatoria demográfica | **US$ 0**: es una cláusula de ordenamiento |
| Semántica aleatoria | **Parecido al ranking**: se verifica la cantidad pedida más los reemplazos, con un tope de 4 verificaciones por persona pedida. Cada lote extra de 25 cuesta del orden de US$ 0,03 |
| Parte 4 | **US$ 0**. Agregar audiencia con una consulta semántica cuesta lo mismo que esa consulta |
| Infraestructura | **US$ 0** |

---

## Preguntas abiertas

| Pregunta | Quién | ¿Bloquea? |
|---|---|---|
| ¿El texto de la carga ANTEL («el teléfono se usará exclusivamente para contactarnos contigo…») alcanza como aceptación de WhatsApp como canal? | Protección de datos | **Sí, para usar R-CAN sobre esa carga.** La funcionalidad se despliega igual |
| ¿El umbral de base chica en 30 sirve para todos los estudios? | Investigación | No: es configurable (`COMPOSICION_BASE_CHICA`) |
| ¿El desempate final del Muestreo debería ser aleatorio en vez de por `id_persona`? | Investigación | No: fuera de este pedido |

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_pedido_flow_en_mensajes.py` — un mensaje de Flow se crea, se
  valida y se envía con los cuatro públicos; la corrida de prueba no
  escribe; después de migrar, «No enviado aún» no repite; el conteo de
  convocados es el mismo; las respuestas de un Flow mapean por el
  `flow_token` (no regresión).
- `tests/test_fase4_contacto.py` — las pruebas de R4.5, reescritas sobre
  los mensajes: idioma y Flow resueltos de la plantilla, Flow publicado al
  guardar y al enviar, motivos de exclusión, `flow_token`, reintento, y que
  ya no existen las rutas viejas ni `enviar_whatsapp`.
- `tests/test_pedido_canales_en_la_carga.py` — canales al cargar con su
  texto, sin celular válido no hay WhatsApp, un revocado no se reactiva,
  el registro en bloque queda auditado y exige `gestionar_canales`, y los
  mensajes los ven elegibles.
- `tests/test_pedido_seleccion_aleatoria.py` — 50 de 1.000 sorteadas, misma
  semilla misma selección, páginas sin repetir, el sorteo en Python igual al
  de SQL, el CSV respeta selección y orden, la encuesta guarda modo y
  semilla, segmento completo, y el aleatorio semántico con verificación
  simulada.
- `tests/test_pedido_audiencias_listados_y_composicion.py` — la lista
  paginada de a 50 con búsqueda y filtros, el listado por estado y cliente,
  las cuatro vistas del cruce con bases, el criterio de encuesta, agregar
  audiencia y la independiente vacía.
- `tests/test_pantallas_pedido_mensajes_canales_seleccion.py` (8, Chromium).
- `tests/test_rutas_con_pantalla.py` — las rutas `R-CAN.`, `R-ALE.`,
  `R-ENC.`, `R-COMP.` y `R-AU.` se usan desde una pantalla.
- `tests/test_esquema.py` — la `0036` está declarada.

---

## Volver atrás

**El código:** desplegar la versión anterior. La `0036` puede quedar: el
código anterior no lee nada de lo que agrega y sigue escribiendo
`participacion.envio_*` desde la sección vieja. Ojo: lo que se envíe con la
sección vieja después de volver atrás **no** estará en `envio`, y «No
enviado aún» del mensaje migrado no lo verá.

**La migración**, si hiciera falta sacarla: restaurar el respaldo del paso 2
es lo más seguro. A mano (borra los mensajes migrados y sus envíos, y las
tablas nuevas):

```sql
begin;
delete from encuesta_mensaje where creado_por = 'migracion_0036';
drop table if exists encuesta_audiencia_agregado;
drop table if exists carga_canal_registro;
alter table consulta_ejecucion drop column if exists seleccion;
alter table consulta_ejecucion drop column if exists semilla;
alter table encuesta_mensaje drop constraint if exists encuesta_mensaje_boton_check;
alter table encuesta_mensaje drop constraint if exists encuesta_mensaje_flow_con_id;
alter table encuesta_mensaje drop column if exists boton;
alter table encuesta_mensaje drop column if exists flow_id;
update encuesta set origen = 'consulta' where origen = 'manual';
alter table encuesta drop constraint if exists encuesta_origen_check;
alter table encuesta add constraint encuesta_origen_check
  check (origen in ('panel', 'consulta', 'carga'));
commit;
```
