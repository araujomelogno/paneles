# Despliegue — convocatoria desde la encuesta: estado, panelistas a mano y mensajes

**Spec:** `specs/SPEC_convocatoria_y_mensajes.md` (R-CF.1 a R-CF.8, P0 y P1) ·
**Decisión:** D85
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0034`** (superficie de
selección cualitativa, D84), y encuestas y cargas unificadas (D81)
desplegado.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | Meta | Código |
|---|---|---|---|---|---|
| | `0035` (aditiva) | nada | 2 secretos nuevos | alta del webhook | backend, frontend, una función nueva |

- **La `0035`**:
  - `encuesta.url_alchemer`;
  - tres tablas nuevas: `encuesta_estado_cambio`, `encuesta_mensaje` y
    `envio`;
  - `mensaje` como destino de la vía diferida.

  No borra ni reescribe datos.
- **Dos secretos nuevos**, declarados en `SECRETOS` de `functions/main.py`:
  - `WHATSAPP_VERIFY_TOKEN`: el que se escribe en Meta al dar de alta el
    webhook;
  - `WHATSAPP_APP_SECRET`: con él se comprueba la firma de cada aviso.

  **Tienen que existir antes del `firebase deploy`**: un secreto declarado
  que no existe en Secret Manager hace fallar el despliegue.
- **Una función nueva**, `whatsappwebhook`: la URL pública a la que Meta
  manda los estados de entrega.
- **Un permiso de aplicación nuevo**, `enviar_mensajes` (admin y
  operaciones). No hay que dar de alta a nadie: sale del rol.

**Qué NO cambia:** ningún rol de base, permiso de IAM, cola ni variable de
`functions/.env`. El envío usa la cola de siempre (`procesaringesta`), las
credenciales de WhatsApp de R4.5 (`WHATSAPP_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WABA_ID`) y el correo de R-MAIL
(`SMTP_PASSWORD`).

**COLOQUIO:** nada que avisar. Las tablas nuevas no tienen `grant` y no se
toca ninguna vista ni función de su superficie. La batería tiene que seguir
en **24 de 24**.

### Orden en una línea

proyecto fijado → `0035` → verificar esquema → token de COLOQUIO y batería
→ secretos → desplegar → webhook en Meta → envío de prueba → primer envío
real.

La `0035` va **antes** del código: el código nuevo lee `url_alchemer` y las
tablas nuevas, y el anterior no las nota.

---

## Paso 1 · Preparar la terminal y fijar el proyecto

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
gcloud config get-value project
git checkout main && git pull
ls db/boveda/0035_convocatoria_y_mensajes.sql
```

`gcloud config get-value project` tiene que decir **`gestion-paneles`**. Si
dice otra cosa, no seguir: todo lo que viene (Secret Manager, tokens,
despliegue) iría a otro proyecto.

## Paso 2 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`.

La precondición, la `0034`:

```bash
psql "$DSN_BOVEDA" -tAc "select to_regclass('v_atributo_catalogo') is not null"
```

Tiene que dar `t`. Si da `f`, primero
`docs/DESPLIEGUE - superficie de selección cualitativa.md`.

## Paso 3 · Aplicar `boveda/0035`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0035_convocatoria_y_mensajes.sql
```

Una sola transacción, re-ejecutable. La primera vez avisa con algunos
`NOTICE … does not exist, skipping` (los `drop constraint if exists`).
Termina en `COMMIT`.

Qué hace:

- agrega `encuesta.url_alchemer`;
- crea `encuesta_estado_cambio`, `encuesta_mensaje` y `envio`, con sus
  índices y `check`. `envio` tiene un índice único parcial sobre
  `id_mensaje_meta`, que es por donde el webhook busca;
- amplía `ingesta_trabajo_destino_check` a `('encuesta', 'carga',
  'mensaje')`;
- activa RLS en las tres tablas y hace `revoke all … from public`.

**Comprobar:**

```sql
select to_regclass('encuesta_estado_cambio'), to_regclass('encuesta_mensaje'),
       to_regclass('envio');
select pg_get_constraintdef(oid) from pg_constraint
 where conname = 'ingesta_trabajo_destino_check';
```

Las tres tablas existen, y el `check` nombra `mensaje`.

## Paso 4 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

Sin la `0035` nombra como faltantes `encuesta.url_alchemer`,
`encuesta_estado_cambio`, `encuesta_mensaje` y `envio`. Con ella, nada.

## Paso 5 · Token de COLOQUIO y su batería

El token dura una hora: se genera **ahora**, justo antes de la batería, no
al principio de la sesión.

```bash
source scripts/token_coloquio.sh
python3 scripts/verificar_coloquio.py
```

Tiene que terminar en **24 de 24**, sin fallidos. Ninguna tabla nueva tiene
`grant`, así que la lista blanca no cambia. Si algún chequeo de privilegios
falla, la `0035` abrió algo de más: no seguir.

## Paso 6 · Los dos secretos

**`WHATSAPP_VERIFY_TOKEN`.** Lo elegimos nosotros: una cadena al azar que
después se pega en Meta (paso 8).

```bash
openssl rand -hex 24 | tr -d '\n' \
  | firebase functions:secrets:set WHATSAPP_VERIFY_TOKEN --data-file - --project="$PROYECTO"
```

**`WHATSAPP_APP_SECRET`.** Es la clave secreta de la app de Meta:
**developers.facebook.com → la app → Configuración → Básica → Clave secreta
de la app → Mostrar**.

```bash
printf '%s' "<clave secreta de la app>" \
  | firebase functions:secrets:set WHATSAPP_APP_SECRET --data-file - --project="$PROYECTO"
```

Comprobar que quedaron, y sin salto de línea (muestra el valor: hacerlo en
una terminal propia):

```bash
firebase functions:secrets:access WHATSAPP_VERIFY_TOKEN --project="$PROYECTO" | wc -c   # 48
firebase functions:secrets:access WHATSAPP_APP_SECRET   --project="$PROYECTO" | wc -c   # 32
```

Si da uno más, se guardó con salto de línea: repetir. Y que la línea con la
clave no quede en el historial de la terminal.

Los dos ya están declarados en `SECRETOS` (`test_main.py` falla si falta o
sobra uno). No hay que tocar código.

## Paso 7 · Desplegar

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
```

Despliega también la función nueva, `whatsappwebhook`. Su URL:

```bash
gcloud functions describe whatsappwebhook --region="$REGION" --gen2 \
  --format='value(serviceConfig.uri)'
```

Anotarla para el paso 8.

**Comprobar la verificación de suscripción** (lo mismo que va a hacer Meta):

```bash
URL_WEBHOOK="<la URL de arriba>"
TOKEN="$(firebase functions:secrets:access WHATSAPP_VERIFY_TOKEN --project="$PROYECTO")"
curl -s "$URL_WEBHOOK?hub.mode=subscribe&hub.verify_token=$TOKEN&hub.challenge=hola"; echo
curl -s -o /dev/null -w '%{http_code}\n' "$URL_WEBHOOK?hub.mode=subscribe&hub.verify_token=otro&hub.challenge=hola"
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$URL_WEBHOOK" -d '{}'
```

- el primero devuelve `hola`;
- el segundo, `403`: el token no coincide;
- el tercero, `403`: un aviso sin firma no entra.

## Paso 8 · El webhook en Meta (R-CF.7)

**Antes de tocar nada, ver si la app ya tiene un webhook** (es una pregunta
abierta de la spec). Una app de Meta tiene **una sola** URL de callback: si
ya apunta a otro sistema, cambiarla lo deja sin avisos. En ese caso, no
seguir y decidir con quien lo use.

**developers.facebook.com → la app → WhatsApp → Configuración → Webhook →
Editar:**

- **URL de devolución de llamada:** la URL de `whatsappwebhook`.
- **Token de verificación:** el valor de `WHATSAPP_VERIFY_TOKEN`.
- **Verificar y guardar.** Meta hace el GET del paso 7. Si falla, mirar el
  log de la función.

Después, en **Campos del webhook**, suscribir **`messages`**: los estados de
entrega (`sent`, `delivered`, `read`, `failed`) llegan por ese campo.

Sin el webhook todo funciona igual, salvo R-CF.7: los envíos quedan en
«Enviado» (aceptado por Meta) y nunca pasan a «Entregado» o «Leído».

## Paso 9 · Envío de prueba (antes del primer envío real)

Lo pide la spec (§11): dos o tres números del equipo, **antes** de mandarle
nada a un panelista.

1. **La plantilla.** En Meta, una plantilla con botón de URL **dinámico**
   cuya base sea la URL de la encuesta de prueba en Alchemer, terminada en
   el parámetro: `https://survey.alchemer.com/s3/<id>/<nombre>?pid={{1}}`.
   Esperar a que esté **aprobada**: la revisión demora.
2. **La encuesta.** Una encuesta de prueba en el sistema, y en **Datos del
   estudio** su URL de Alchemer, **igual** a la base del botón
   (`…?pid=`).
3. **La audiencia.** **Agregar panelistas** con los celulares de las 2–3
   personas del equipo. Tienen que estar en la bóveda con consentimiento de
   contacto y WhatsApp aceptado: si alguna sale como «no corresponde» o
   «sin consentimiento», eso es lo que hay que arreglar primero.
4. **El mensaje.** Pasar la encuesta a **En campo**. **+ Nuevo mensaje** →
   WhatsApp → la plantilla. Si avisa que el botón lleva a otra URL, **no**
   tildar «guardar igual»: corregir la URL de la encuesta o la plantilla.
5. **Enviar** → «Todos». La vista previa tiene que decir 2–3 «lo van a
   recibir» y nadie afuera. Enviar y esperar el resumen.

**Qué verificar:**

- [ ] Llega el WhatsApp, con el nombre en el texto si la plantilla lo usa.
- [ ] El botón abre **la encuesta correcta**, con el `id_persona` de esa
      persona al final de la URL. Se ve en la barra del navegador:
      `?pid=<uuid>`.
- [ ] En la sección Convocatoria, cada persona muestra el mensaje en
      **Enviado** y, al rato, en **Entregado** o **Leído** (el webhook).
- [ ] Responder la encuesta, bajar el SAV de Alchemer y comprobar que el
      `id_persona` **vuelve en una columna**. Es la pregunta abierta que
      bloquea el primer campo real: si no vuelve solo, hace falta una
      pregunta oculta en Alchemer que tome el parámetro.

Por SQL, lo mismo:

```sql
select m.nombre, e.estado, e.id_mensaje_meta is not null as con_id_meta,
       e.error, e.enviado_en, e.actualizado_en
  from envio e join encuesta_mensaje m on m.id = e.encuesta_mensaje_id
 order by e.id desc limit 10;
```

Al terminar, la encuesta de prueba se puede borrar (**Borrar la
encuesta…**): sus mensajes y su registro de envíos se van con ella.

## Paso 10 · El correo (R-CF.8)

Usa el SMTP de Workspace de R-MAIL, sin configuración nueva. Para probarlo,
un segundo mensaje en la encuesta de prueba, canal **Correo**, con
`{nombre}` y `{enlace}` en el cuerpo, enviado a «Todos». Verificar que llega
y que el enlace es la URL de Alchemer con el `id_persona`.

Solo lo reciben quienes aceptaron el correo como canal: quien aceptó solo
WhatsApp queda afuera, con ese motivo en la vista previa.

---

## Qué mirar después

**Que nadie reciba dos veces el mismo mensaje** (métrica de la spec: cero):

```sql
select encuesta_mensaje_id, id_persona, count(*)
  from envio
 where estado in ('aceptado', 'entregado', 'leido')
 group by 1, 2 having count(*) > 1;
```

Con «Todos» un reenvío es una decisión que la vista previa avisa. Con «No
enviado aún» no tiene que aparecer nadie.

**La proporción de aceptados** (objetivo ≥ 95 %) y **los motivos de los
fallidos**:

```sql
select estado, count(*) from envio group by 1;
select error, count(*) from envio where estado = 'fallido'
 group by 1 order by 2 desc limit 20;
```

**El webhook**, en el log de `whatsappwebhook`: líneas
`[webhook] estados=… actualizados=…`, y ninguna `aviso rechazado`. Si
aparecen rechazados, el `WHATSAPP_APP_SECRET` no es el de la app.

**El límite diario.** La spec deja abierto cuál es el límite de
destinatarios del número de WhatsApp. Con un envío de más de mil personas,
mirar en el resumen cuántos fallaron y por qué antes de lanzar otro.

---

## Costos (§12 de la spec)

| Concepto | Costo |
|---|---|
| WhatsApp | Por mensaje, según la categoría que Meta le asigne a la plantilla. Uruguay: del orden de US$ 0,07 en marketing y US$ 0,01 en utilidad (confirmar con la tarifa vigente) |
| Ejemplo | 1.000 panelistas × invitación + recordatorio a la mitad + agradecimiento a 300 ≈ 1.800 mensajes ≈ US$ 125 por estudio en marketing |
| Correo (Workspace) | US$ 0, hasta 2.000 destinatarios por día |
| Cloud Tasks, esquema, pantalla | US$ 0 |

Invitar por correo y recordar por WhatsApp solo a quienes no contestaron es
el patrón más barato, y el sistema ya lo permite.

---

## Preguntas abiertas que siguen abiertas

| Pregunta | Quién | ¿Bloquea? |
|---|---|---|
| ¿La app de Meta ya tiene un webhook dado de alta? | Ingeniería | Paso 8 |
| ¿Cuál es el límite diario de destinatarios del número de WhatsApp? | Operaciones | No |
| ¿El `id_persona` vuelve como columna en el SAV de Alchemer, o hace falta una pregunta oculta? | Campo | **Sí, para el primer campo real** (paso 9) |
| ¿Hacen falta más variables de texto que el nombre del panelista y el de la encuesta? | Campo | No |

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_convocatoria_y_mensajes.py` (29 pruebas):
  - el estado: una encuesta de carga se reabre, queda registrado y se lee
    por la ruta del historial; cerrada o en borrador no envía;
  - agregar panelistas: una lista mezclada, línea por línea; cerrada
    rechaza; también en una independiente;
  - la URL de Alchemer y el enlace;
  - plantillas: las de botón de URL, la no aprobada, los parámetros, el
    aviso de URL distinta;
  - un mensaje enviado no se borra;
  - los cuatro públicos, la lista negra en cualquiera, la vista previa con
    excluidos y reenvío, el fallido que cuenta como no enviado;
  - el botón lleva el `id_persona` y el texto sus parámetros;
  - el reintento de un lote no reenvía, y no se lanzan dos envíos a la vez;
  - enviar es de operaciones y admin;
  - el primer mensaje convoca y los siguientes no;
  - el webhook: firma, verificación y estados que no retroceden;
  - el correo, con el canal que cada uno aceptó.
- `tests/test_pantallas_convocatoria_y_mensajes.py` (5, Chromium): el
  estado desde el listado y su historial, la URL y los mensajes en el
  detalle, agregar panelistas, la casilla de URL distinta y el envío con su
  vista previa.
- `tests/test_borrar_carga.py`: quien recibió un mensaje se conserva al
  borrar la carga.
- `tests/test_rutas_con_pantalla.py`: las rutas `R-CF.` se usan desde una
  pantalla.

---

## Volver atrás

**El código:** desplegar la versión anterior. La `0035` puede quedar: el
código anterior no lee nada de lo que agrega. El webhook en Meta, en ese
caso, apunta a una función que ya no existe: quitarlo o dejarlo (Meta
reintenta y desiste).

**La migración**, si hiciera falta sacarla (borra los mensajes y el
registro de envíos):

```sql
begin;
alter table ingesta_trabajo drop constraint if exists ingesta_trabajo_destino_check;
delete from ingesta_trabajo where destino_tipo = 'mensaje';
alter table ingesta_trabajo add constraint ingesta_trabajo_destino_check
  check (destino_tipo in ('encuesta', 'carga'));
drop table if exists envio;
drop table if exists encuesta_mensaje;
drop table if exists encuesta_estado_cambio;
alter table encuesta drop column if exists url_alchemer;
commit;
```

Y los secretos, si no se van a usar:

```bash
firebase functions:secrets:destroy WHATSAPP_VERIFY_TOKEN --project="$PROYECTO"
firebase functions:secrets:destroy WHATSAPP_APP_SECRET   --project="$PROYECTO"
```

Solo después de sacarlos de `SECRETOS`: un secreto declarado que no existe
hace fallar el despliegue.
