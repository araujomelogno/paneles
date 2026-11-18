# Despliegue — Alchemer, historia de participación y bandeja de WhatsApp

**Spec:** `specs/SPEC_alchemer_participacion_y_bandeja_whatsapp.md` (R-PAR,
R-ALC, R-WA) · **Decisiones:** D96, D97, D98
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0037`** y el store semántico
hasta la **`0013`**, con el código de D95 desplegado.

Son **dos momentos**:

1. **El despliegue** (pasos 1 a 12): migración, secretos, código y la función
   nueva `whatsappwebhook`, probada con avisos firmados a mano. **No cambia
   nada en Meta**: la planilla sigue recibiendo.
2. **El corte** (paso 13, R-WA.7): cambiar la URL de callback de la app de
   Meta de la planilla a paneles. Meta admite **una sola**: desde ese momento
   la planilla deja de recibir. Se hace en un momento acordado, sin Flows en
   campo, y tiene su vuelta atrás.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Meta | Código |
|---|---|---|---|---|---|---|
| | **`0038`** (cambia datos) | nada | **4 nuevos** | 5 opcionales | URL de callback (**solo en el corte**) | backend, frontend, función **`whatsappwebhook`** |

La `boveda/0038`:

1. **B · fecha de campo obligatoria**: completa las encuestas sin fecha con la
   de su **primera carga** (o la de creación), las anota en
   `encuesta_fecha_completada` para revisarlas y vuelve `encuesta.fecha_campo`
   **`not null`**;
2. **A · Alchemer**: `encuesta.alchemer_id` y `alchemer_cuestionario` (el
   cuestionario como referencia, sin datos personales);
3. **C · la bandeja**: `whatsapp_aviso` (el aviso crudo), `whatsapp_entrante`
   (respuestas y comentarios, con teléfonos), `whatsapp_estado` y
   `whatsapp_exportacion`. Todo con RLS y sin `grant` a nadie.

**Secretos nuevos** (declarados en `SECRETOS`: **el deploy falla si falta
alguno**): `ALCHEMER_API_TOKEN`, `ALCHEMER_API_SECRET`,
`WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`.

**Permiso nuevo:** `bandeja_whatsapp` (admin y operaciones), para la sección
«WhatsApp».

**Qué NO cambia:** el store semántico, ningún rol de base ni `grant`, ninguna
cola de Cloud Tasks, la superficie de COLOQUIO. La batería tiene que seguir en
**24 de 24**.

### Orden en una línea

proyecto fijado → sin ingestas en curso → **respaldo de la bóveda** →
**corrida de prueba** → `boveda/0038` → verificación → `verificar_esquema.py`
en verde → **secretos** → `functions/.env` → token de COLOQUIO y batería →
`firebase deploy` → **probar el webhook con avisos firmados** →
comprobaciones → (otro día, acordado) **el corte en Meta**.

La `0038` va **antes** del código: el código nuevo lee `alchemer_id` y las
tablas de la bandeja, y exige la fecha. El código anterior sigue andando con la
`0038` aplicada, **salvo** un detalle: crear una encuesta sin fecha falla por el
`not null` en lugar de guardarla vacía (ver «Volver atrás»).

### Puntos de control

| # | Qué tiene que cumplirse | Paso | Si no se cumple |
|---|---|---|---|
| 1 | Ninguna ingesta en curso | 1 | Se espera |
| 2 | Respaldo de la bóveda `SUCCESSFUL` | 2 | No se empieza |
| 3 | La corrida de prueba lista lo que va a completar y «cierra» | 3 | No se aplica la `0038` |
| 4 | `verificar` cierra: ninguna encuesta sin fecha y la columna obligatoria | 5 | **Se restaura el respaldo**; no se despliega |
| 5 | `verificar_esquema.py` sin faltantes | 6 | No se despliega |
| 6 | Los 4 secretos existen con versión | 7 | No se despliega (el deploy fallaría) |
| 7 | Batería de COLOQUIO en 24 de 24 | 9 | No se despliega |
| 8 | El webhook responde la verificación, acepta un aviso firmado y rechaza uno mal firmado | 11 | **No se hace el corte** |

---

## Paso 1 · Preparar la terminal y fijar el proyecto — punto de control 1

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
gcloud config get-value project
git checkout main && git pull
ls db/boveda/0038_alchemer_participacion_y_bandeja_whatsapp.sql scripts/migrar_fecha_de_campo.py
```

`gcloud config get-value project` tiene que decir **`gestion-paneles`**. Si
dice otra cosa, no seguir.

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
psql "$DSN_BOVEDA" -tAc "select to_regclass('limpieza_provisoria') is not null"
psql "$DSN_BOVEDA" -tAc "select count(*) from ingesta_trabajo where estado in ('encolada','procesando')"
```

`paneles_boveda`, `t` y `0`.

## Paso 2 · Respaldo de la bóveda — punto de control 2

```bash
gcloud sql backups create --instance=paneles-boveda --project="$PROYECTO" \
  --description="antes de Alchemer, participación y bandeja (boveda/0038)"
gcloud sql backups list --instance=paneles-boveda --project="$PROYECTO" --limit=1
```

Tiene que figurar `SUCCESSFUL`. Anotar el id: es el que se restaura si el
punto 4 falla. **Sin respaldo confirmado no se empieza.**

## Paso 3 · Corrida de prueba — punto de control 3

```bash
python3 scripts/migrar_fecha_de_campo.py prueba
```

Ejecuta **el mismo archivo** `0038` en una transacción que se deshace.
Ejemplo:

```
Corrida de prueba de la 0038 (deshecha: no se escribió nada)
  Encuestas sin fecha de campo hoy: 3
  Va a completar: 3
  encuesta 12 «Tracking telefonía» → 2026-08-04 (fecha de su primera carga)
  encuesta 19 «Base Antel» → 2026-09-15 (fecha de su primera carga)
  encuesta 31 «Piloto» → 2026-10-02 (fecha de creación (no tiene cargas))
  Encuestas sin fecha después de aplicar: 0
Mandá esta lista al equipo: cada fecha se corrige desde «Datos del estudio».
```

Sale con `0` si cierra y con `1` si no; con `1`, **no** se aplica.
**Guardar la lista y mandarla al equipo**: son fechas propuestas, no las del
campo real.

## Paso 4 · Aplicar `boveda/0038`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0038_alchemer_participacion_y_bandeja_whatsapp.sql
```

Una transacción. Termina en `COMMIT`. Segundos.

## Paso 5 · Verificar la migración — punto de control 4

```bash
python3 scripts/migrar_fecha_de_campo.py verificar
```

Tiene que decir **«Cierra.»** (ninguna encuesta sin fecha, columna
obligatoria) y volver a listar las completadas. Si no cierra:

```bash
gcloud sql backups restore <ID_DE_LA_BOVEDA> --restore-instance=paneles-boveda \
  --project="$PROYECTO"
```

y no se despliega el código.

## Paso 6 · Verificar el esquema — punto de control 5

```bash
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"   # con su túnel en 5433
python3 scripts/verificar_esquema.py --pii
```

Sin la `0038` nombra como faltantes `encuesta_fecha_completada`,
`encuesta.alchemer_id`, `alchemer_cuestionario` y las cuatro `whatsapp_*`.
Con ella, **nada**. En verde **antes** del `firebase deploy`.

## Paso 7 · Los cuatro secretos — punto de control 6

Ninguno empieza con `FIREBASE_`, `X_GOOGLE_`, `EXT_` ni `KIT_` (Firebase los
rechaza). Se cargan con `printf '%s'` para no meter un salto de línea.

**De dónde sale cada uno:**

| Secreto | Dónde se obtiene |
|---|---|
| `ALCHEMER_API_TOKEN` y `ALCHEMER_API_SECRET` | Alchemer → *Integrations* → *API Access* → crear una clave. Basta con permiso de lectura. |
| `WHATSAPP_APP_SECRET` | Meta for Developers → la app de WhatsApp → *Configuración de la app* → *Básica* → **Clave secreta de la app**. Es la misma con que Meta firma los avisos. |
| `WHATSAPP_VERIFY_TOKEN` | **Se inventa**: una cadena al azar. Se escribe en Meta en el corte. |

```bash
read -rs ALCHEMER_TOKEN && read -rs ALCHEMER_SECRETO && read -rs APP_SECRET
VERIFY_TOKEN="$(openssl rand -hex 24)"
for par in "ALCHEMER_API_TOKEN:$ALCHEMER_TOKEN" "ALCHEMER_API_SECRET:$ALCHEMER_SECRETO" \
           "WHATSAPP_APP_SECRET:$APP_SECRET" "WHATSAPP_VERIFY_TOKEN:$VERIFY_TOKEN"; do
  nombre="${par%%:*}"; valor="${par#*:}"
  gcloud secrets list --project="$PROYECTO" --format='value(name)' | grep -qx "$nombre" \
    || gcloud secrets create "$nombre" --replication-policy=automatic --project="$PROYECTO"
  printf '%s' "$valor" | gcloud secrets versions add "$nombre" --project="$PROYECTO" --data-file=-
done
unset ALCHEMER_TOKEN ALCHEMER_SECRETO APP_SECRET VERIFY_TOKEN
```

Cuatro `Created version [...]`. Comprobación:

```bash
for s in ALCHEMER_API_TOKEN ALCHEMER_API_SECRET WHATSAPP_APP_SECRET WHATSAPP_VERIFY_TOKEN; do
  printf '%s ' "$s"; gcloud secrets versions list "$s" --project="$PROYECTO" --filter=state=enabled --format='value(name)' | head -1
done
```

Cada uno con un número de versión.

> **Si todavía no hay clave de Alchemer:** crear los dos secretos igual (el
> deploy los exige) con un valor provisorio, por ejemplo `pendiente`. La
> búsqueda va a mostrar el error de Alchemer y la URL se sigue escribiendo a
> mano. Cargar la clave real después es agregar una versión: no hace falta
> redesplegar el código, pero sí reiniciar las instancias (un deploy nuevo
> o esperar a que se reciclen).

## Paso 8 · `functions/.env`

Todas son **opcionales**: sin ellas valen los valores por defecto.

```bash
# Alchemer: us (default) | eu | ca, o la URL completa de la API.
ALCHEMER_REGION=us
# El parámetro de la URL que lleva el id_persona (default pid).
ALCHEMER_PARAMETRO=pid
# Cuánto se conserva lo de afuera y el aviso crudo (180 y 30 por defecto).
WHATSAPP_CONSERVAR_AFUERA_DIAS=180
WHATSAPP_CONSERVAR_AVISO_DIAS=30
```

Si la cuenta de Alchemer está en Europa, `ALCHEMER_REGION=eu` **es
obligatorio**: con la región equivocada la API contesta 401 aunque la clave
esté bien.

## Paso 9 · Token de COLOQUIO y su batería — punto de control 7

```bash
source scripts/token_coloquio.sh
python3 scripts/verificar_coloquio.py
```

**24 de 24**, sin fallidos. Las tablas nuevas no tienen `grant` a
`coloquio_app`, y la lista blanca lo comprueba.

## Paso 10 · Desplegar

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
gcloud functions list --regions="$REGION" --format='value(name)' | grep -c whatsappwebhook
```

El segundo comando ahora tiene que dar **`1`**: la función existe, pero Meta
todavía no la conoce. La URL:

```bash
export WEBHOOK_URL="$(gcloud functions describe whatsappwebhook --region="$REGION" \
  --project="$PROYECTO" --format='value(serviceConfig.uri)')"
echo "$WEBHOOK_URL"
```

Algo como `https://whatsappwebhook-xxxxx-rj.a.run.app`. Anotarla: es la que va
en Meta en el corte.

## Paso 11 · Probar el webhook sin Meta — punto de control 8

**11.1 · La verificación** (lo que Meta hace al dar de alta la URL):

```bash
VERIFY_TOKEN="$(gcloud secrets versions access latest --secret=WHATSAPP_VERIFY_TOKEN --project="$PROYECTO")"
curl -s "$WEBHOOK_URL?hub.mode=subscribe&hub.verify_token=$VERIFY_TOKEN&hub.challenge=4242"; echo
curl -s -o /dev/null -w '%{http_code}\n' "$WEBHOOK_URL?hub.mode=subscribe&hub.verify_token=otro&hub.challenge=4242"
```

`4242` y `403`.

**11.2 · Un aviso firmado** (un estado de un mensaje que no existe: no toca
ningún envío):

```bash
APP_SECRET="$(gcloud secrets versions access latest --secret=WHATSAPP_APP_SECRET --project="$PROYECTO")"
CUERPO='{"object":"whatsapp_business_account","entry":[{"id":"prueba","changes":[{"field":"messages","value":{"statuses":[{"id":"wamid.prueba-despliegue","status":"delivered","timestamp":"1763400000"}]}}]}]}'
FIRMA="sha256=$(printf '%s' "$CUERPO" | openssl dgst -sha256 -hmac "$APP_SECRET" | sed 's/^.* //')"
curl -s -H "Content-Type: application/json" -H "X-Hub-Signature-256: $FIRMA" \
  --data-binary "$CUERPO" "$WEBHOOK_URL"; echo
curl -s -o /dev/null -w '%{http_code}\n' -H "Content-Type: application/json" \
  -H "X-Hub-Signature-256: sha256=0000" --data-binary "$CUERPO" "$WEBHOOK_URL"
unset APP_SECRET
```

`ok` y `403`. En la base:

```bash
psql "$DSN_BOVEDA" -c "select estado, error from whatsapp_aviso order by id desc limit 2"
psql "$DSN_BOVEDA" -c "select * from whatsapp_estado where id_mensaje_meta = 'wamid.prueba-despliegue'"
```

**Un** aviso `procesado` (el mal firmado no se guardó) y una fila de estado.
Limpiar la prueba:

```bash
psql "$DSN_BOVEDA" -c "delete from whatsapp_estado where id_mensaje_meta = 'wamid.prueba-despliegue'"
psql "$DSN_BOVEDA" -c "delete from whatsapp_aviso where cuerpo::text like '%prueba-despliegue%'"
```

**Si algo de esto no da lo esperado, no se hace el corte.** Los logs:
`gcloud functions logs read whatsappwebhook --region="$REGION" --limit=20`
(sin contenido de los avisos: solo el tipo de error).

## Paso 12 · Comprobaciones en la aplicación

- [ ] **Consultas → + Participación**: *Convocados · No · en los últimos 6
      meses* agrega el criterio con esa etiqueta y la consulta corre.
- [ ] **+ Nueva encuesta**: la fecha de campo lleva asterisco y sin ella no se
      crea. **Buscar en Alchemer** lista encuestas de la cuenta; elegir una
      completa el nombre y la URL con `?pid=`.
- [ ] Una encuesta de Alchemer que **no** guarda `pid` muestra el aviso y se
      guarda igual; su ficha tiene la tarjeta *Cuestionario de Alchemer*.
- [ ] Mandar al equipo la lista del paso 3 para corregir las fechas
      propuestas.
- [ ] La sección **WhatsApp** aparece para admin y operaciones (vacía hasta
      el corte), y no aparece para analista ni DPO.

---

## Paso 13 · El corte: Meta llama a paneles (R-WA.7)

**Cuándo:** en un momento **sin Flows en campo**, o aceptando que lo que llegue
después del cambio va a paneles y no a la planilla. Acordarlo con quien opera
los Flows de afuera.

**13.1 · Antes de cambiar nada, anotar lo de hoy.** En Meta for Developers →
la app → **WhatsApp** → **Configuración** → **Webhook**: copiar la **URL de
devolución de llamada** y el **token de verificación** actuales (los de la
planilla). Son la vuelta atrás.

**13.2 · Cambiar la URL:**

1. *Editar* → **URL de devolución de llamada** = `$WEBHOOK_URL` (paso 10).
2. **Token de verificación** = el valor de `WHATSAPP_VERIFY_TOKEN`:
   `gcloud secrets versions access latest --secret=WHATSAPP_VERIFY_TOKEN --project="$PROYECTO"`.
3. *Verificar y guardar*. Meta hace el `GET` del paso 11.1; si falla, no
   guarda y la planilla sigue recibiendo.
4. En **Campos del webhook**, que **`messages`** esté suscripto (trae las
   respuestas, los comentarios y los estados).

**13.3 · Después del cambio:**

- [ ] Mandar un mensaje con botón de Flow desde una encuesta de prueba a un
      número propio. En **Mensajes**, el envío pasa a *Entregado* y, al
      abrirlo, a *Leído*.
- [ ] Responder el Flow: aparece en **WhatsApp → Respuestas** bajo esa
      encuesta, con una columna por campo.
- [ ] Escribir un comentario contestando el mensaje: aparece en
      **Comentarios**.
- [ ] Si hay una encuesta de afuera en curso, su primera respuesta aparece
      agrupada por el nombre del token (`tpl=NOMBRE|…`).
- [ ] Sin avisos fallidos:

```bash
psql "$DSN_BOVEDA" -c "select estado, count(*) from whatsapp_aviso group by 1"
```

**13.4 · La planilla** queda como **archivo histórico**: ya no recibe. Avisar a
quien la usaba que las respuestas nuevas están en la sección WhatsApp. Las
encuestas de afuera tienen que seguir usando el token `tpl=NOMBRE|…` para
agruparse.

**Volver atrás el corte:** en Meta, volver a poner la URL y el token anotados en
13.1. Lo que haya llegado a paneles mientras tanto queda en la bandeja (no se
mueve a la planilla).

---

## Qué mirar después

**Avisos que fallaron** (se reprocesan desde la bandeja; el crudo está):

```sql
select id, recibido_en, error from whatsapp_aviso
 where estado <> 'procesado' order by recibido_en desc limit 20;
```

**Respuestas con el `flow_token` distinto del envío:**

```sql
select encuesta_id, count(*) from whatsapp_entrante
 where inconsistencia is not null group by 1;
```

**Estados de los envíos de WhatsApp** (R-CF.7):

```sql
select estado, count(*) from envio where canal = 'whatsapp'
   and enviado_en > now() - interval '30 days' group by 1;
```

**Exportaciones de la bandeja:**

```sql
select creado_en, actor_email, filtro, filas from whatsapp_exportacion
 order by creado_en desc limit 20;
```

---

## Costos

| Concepto | Costo |
|---|---|
| API de Alchemer | **US$ 0** adicional: incluida en el plan; unas pocas llamadas al buscar y al vincular. |
| Webhook de Meta | **US$ 0** de Meta. La función: una invocación corta por aviso, dentro del nivel gratuito para el volumen de un panel. |
| Definición del Flow (R-WA.6) | **US$ 0**: una llamada a Meta por Flow cada vez que se obtienen respuestas. |
| Espacio | Las tablas de la bandeja, con purga automática de lo de afuera (180 días) y del crudo (30). |
| Ingestar las respuestas de WhatsApp | Lo mismo que una carga de un SAV del mismo tamaño (embeddings de los textos nuevos). |

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_spec_participacion_y_alchemer.py`: los dos criterios de
  aceptación de R-PAR.1 (convocado hace 8 meses aparece en «no convocado en
  los últimos 6»; respondió una encuesta con campo hace 3 meses aparece en
  «respondió en los últimos 6»), veces, entre fechas, combinación, lo mal
  formado es error; la fecha exigida al crear y al editar; la migración
  completa con la primera carga o la creación y lista; Alchemer sin
  credenciales, región, búsqueda y enlaces, la encuesta que no guarda el
  parámetro se avisa y se guarda, se re-verifica al salir a campo, «no se
  pudo verificar» no es «sí», temáticas con el cuestionario sin escribir,
  el cuestionario en la bóveda y no en el semántico.
- `tests/test_bandeja_whatsapp.py`: verificación de Meta; firma inválida o
  sin secreto rechazada sin guardar; reenvío sin duplicar; el aviso fallido
  queda y se reprocesa; los pendientes de antes se levantan; la asociación
  por el envío, el token distinto, los grupos de afuera, comentarios; los
  estados sin retroceder; la bandeja con columnas en orden, exportación
  auditada y acceso por rol; la purga y la baja por id y por teléfono; el
  diccionario del Flow; el SAV con «texto → Sí», batería y «sin etiqueta»;
  nuevas y `repetida` al ingestar dos veces.
- `tests/test_pantallas_alchemer_participacion_y_bandeja.py` (Chromium): el
  criterio de participación, la fecha obligatoria, elegir la encuesta de
  Alchemer (nombre, URL, enlace, aviso, ficha), la bandeja (columnas,
  encuestas de afuera, comentarios, exportar, reprocesar) y «Obtener
  respuestas de WhatsApp» hasta el modal de ingesta con el archivo cargado.

---

## Volver atrás

**El corte:** ver 13.4. **Siempre antes que el código**: si se vuelve el código
atrás con Meta apuntando a `whatsappwebhook`, la función desaparece y los
avisos se pierden (Meta reintenta un tiempo y después descarta).

**El código:** desplegar la versión anterior. Antes, como la columna quedó
obligatoria y el código anterior inserta la fecha tal como viene (vacía si no
se completó):

```bash
psql "$DSN_BOVEDA" -c "alter table encuesta alter column fecha_campo drop not null"
```

Las tablas nuevas quedan y no molestan.

**La migración:** restaurar el respaldo del paso 2. O, sin restaurar: las
fechas completadas son las de `encuesta_fecha_completada`, y se pueden volver
a vaciar con
`update encuesta e set fecha_campo = null from encuesta_fecha_completada f where f.encuesta_id = e.id and e.fecha_campo = f.fecha_campo`
(después de sacar el `not null`). Los cuatro secretos se pueden dejar en
Secret Manager: el código anterior no los declara ni los lee, y un secreto que
existe sin declararse no molesta (lo que hace fallar un deploy es lo
contrario).
