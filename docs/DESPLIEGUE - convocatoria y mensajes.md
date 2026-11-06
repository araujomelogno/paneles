# Despliegue — convocatoria desde la encuesta: estado, panelistas a mano y mensajes

**Spec:** `specs/SPEC_convocatoria_y_mensajes.md` (R-CF.1 a R-CF.8, P0 y P1,
**menos R-CF.7**) · **Decisiones:** D85 y D86
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0034`** (superficie de
selección cualitativa, D84), y encuestas y cargas unificadas (D81)
desplegado.

> **R-CF.7 (estados de entrega por webhook) no entra en este despliegue**
> (D86). La URL de devolución de llamada de la app de Meta ya la usa otro
> sistema, y Meta admite una sola. **No se toca la configuración del webhook
> en Meta.** Los envíos quedan como *Enviado* (aceptado) o *Falló*. Cada
> envío aceptado guarda el id que devuelve Meta, así que los estados se
> pueden sumar más adelante sin perder lo enviado.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | Meta | Código |
|---|---|---|---|---|---|
| | `0035` (aditiva) | nada | nada | nada | backend y frontend |

- **La `0035`**:
  - `encuesta.url_alchemer`;
  - tres tablas nuevas: `encuesta_estado_cambio`, `encuesta_mensaje` y
    `envio`;
  - `mensaje` como destino de la vía diferida.

  No borra ni reescribe datos.
- **Un permiso de aplicación nuevo**, `enviar_mensajes` (admin y
  operaciones). No hay que dar de alta a nadie: sale del rol.

**Qué NO cambia:**
- ningún secreto, rol de base, permiso de IAM, cola ni variable de
  `functions/.env`;
- ninguna función nueva: no hay `whatsappwebhook`;
- la configuración de la app en Meta.

El envío usa la cola de siempre (`procesaringesta`), las credenciales de
WhatsApp de R4.5 (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_WABA_ID`) y el correo de R-MAIL (`SMTP_PASSWORD`).

**COLOQUIO:** nada que avisar. Las tablas nuevas no tienen `grant` y no se
toca ninguna vista ni función de su superficie. La batería tiene que seguir
en **24 de 24**.

### Orden en una línea

proyecto fijado → `0035` → verificar esquema → token de COLOQUIO y batería
→ desplegar → envío de prueba → primer envío real.

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
dice otra cosa, no seguir: todo lo que viene (tokens, despliegue) iría a
otro proyecto.

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
  índices y `check`. Dos detalles de `envio` son para R-CF.7 más adelante:
  - un índice único parcial sobre `id_mensaje_meta`, que es por donde un
    webhook buscaría cada envío;
  - el `check` de `estado`, que ya admite `entregado` y `leido`. Hoy solo
    se escriben `aceptado` y `fallido`;
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

## Paso 6 · Desplegar

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
```

No se crea ninguna función ni se pide ningún secreto nuevo. Comprobar que
no quedó un webhook desplegado:

```bash
gcloud functions list --regions="$REGION" --format='value(name)' | grep -c whatsappwebhook
```

Tiene que dar **`0`**. Si da `1`, viene de un despliegue de prueba anterior:
`firebase deploy` ofrece borrarla por no estar en el código, y hay que
aceptar. Así se borra solo la función de nuestro proyecto, sin tocar lo que
está configurado en Meta.

## Paso 7 · Envío de prueba (antes del primer envío real)

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
      **Enviado**. No pasa a «Entregado» ni «Leído»: eso es R-CF.7, que
      está diferido. Que llegó se comprueba en el teléfono.
- [ ] Responder la encuesta, bajar el SAV de Alchemer y comprobar que el
      `id_persona` **vuelve en una columna**. Es la pregunta abierta que
      bloquea el primer campo real: si no vuelve solo, hace falta una
      pregunta oculta en Alchemer que tome el parámetro.

Por SQL, cada envío aceptado tiene que tener su id de Meta: es lo que deja
sumar los estados más adelante.

```sql
select m.nombre, e.estado, e.id_mensaje_meta is not null as con_id_meta,
       e.error, e.enviado_en
  from envio e join encuesta_mensaje m on m.id = e.encuesta_mensaje_id
 order by e.id desc limit 10;
```

`estado = 'aceptado'` con `con_id_meta = t`, o `fallido` con su `error`.

Al terminar, la encuesta de prueba se puede borrar (**Borrar la
encuesta…**): sus mensajes y su registro de envíos se van con ella.

## Paso 8 · El correo (R-CF.8)

Usa el SMTP de Workspace de R-MAIL, sin configuración nueva. Para probarlo,
un segundo mensaje en la encuesta de prueba, canal **Correo**, con
`{nombre}` y `{enlace}` en el cuerpo, enviado a «Todos». Verificar que llega
y que el enlace es la URL de Alchemer con el `id_persona`.

Solo lo reciben quienes aceptaron el correo como canal: quien aceptó solo
WhatsApp queda afuera, con ese motivo en la vista previa. Un correo
aceptado no tiene id de Meta (`con_id_meta = f`): es lo esperado.

---

## Qué mirar después

**Que nadie reciba dos veces el mismo mensaje** (métrica de la spec: cero):

```sql
select encuesta_mensaje_id, id_persona, count(*)
  from envio
 where estado = 'aceptado'
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

«Aceptado» es que Meta tomó el mensaje, no que llegó. Sin R-CF.7, un número
que Meta acepta y después no entrega no se ve desde el sistema: para eso
está el panel de la cuenta de WhatsApp en Meta.

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

## Preguntas abiertas

| Pregunta | Quién | ¿Bloquea? |
|---|---|---|
| ¿El `id_persona` vuelve como columna en el SAV de Alchemer, o hace falta una pregunta oculta? | Campo | **Sí, para el primer campo real** (paso 7) |
| ¿Cuál es el límite diario de destinatarios del número de WhatsApp? | Operaciones | No |
| ¿Hacen falta más variables de texto que el nombre del panelista y el de la encuesta? | Campo | No |
| ¿Cómo se reciben los estados de entrega, si la URL de callback de la app la usa otro sistema? | Ingeniería | No: R-CF.7 diferido (D86) |

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_convocatoria_y_mensajes.py`:
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
  - el envío queda aceptado o fallido, y cada aceptado guarda su id de
    Meta;
  - no hay función de webhook ni sus secretos en `main.py`;
  - el correo, con el canal que cada uno aceptó.
- `tests/test_pantallas_convocatoria_y_mensajes.py` (5, Chromium): el
  estado desde el listado y su historial, la URL y los mensajes en el
  detalle, agregar panelistas, la casilla de URL distinta y el envío con su
  vista previa.
- `tests/test_borrar_carga.py`: quien recibió un mensaje se conserva al
  borrar la carga.
- `tests/test_rutas_con_pantalla.py`: las rutas `R-CF.` se usan desde una
  pantalla.
- `tests/test_main.py`: `SECRETOS` coincide con lo que el código lee.

---

## Volver atrás

**El código:** desplegar la versión anterior. La `0035` puede quedar: el
código anterior no lee nada de lo que agrega.

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
