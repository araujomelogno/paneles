# Despliegue — envío de correo por Google Workspace y verificación por lotes

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Specs:** `SPEC_envio_correo_workspace.md` (R-MAIL) y
`SPEC_verificacion_por_lotes.md` (R-VER)
**Precondición:** la bóveda al día hasta la **`0022`** y el store semántico
hasta la **`0007`**.
**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Código |
|---|---|---|---|---|---|
| R-MAIL | `0023_envio_correo.sql` | — | `SMTP_PASSWORD` (nuevo) | `VERIFICACION_ENVIO_PROVEEDOR=workspace` + `SMTP_*` | backend y frontend |
| R-VER | — | `0008_captura_verificacion.sql` | — | `VERIFICACION_LOTE`, `…_CONCURRENCIA`, `…_PRESUPUESTO_S`, `…_PROFUNDIDAD_MAX` (opcionales) | backend y frontend |

**Qué NO cambia:** ningún rol, permiso de IAM, cola, conector VPC ni tamaño de
instancia. La superficie externa de COLOQUIO (`coloquio_app`) tampoco: las
dos tablas nuevas no se le otorgan a nadie. El timeout de COLOQUIO **no hay
que tocarlo** (§11), pero sí conviene un arreglo menor allá (§11.3).

Los dos requerimientos van en **un solo deploy**: tocan los mismos archivos
(`ruteo.py`, `main.py`, las pantallas) y desplegarlos por separado obligaría
a desplegar dos veces la misma función.

---

## Qué trae

### R-MAIL · El correo sale de verdad, y el «modo desarrollo» desaparece del sitio público

- **Proveedor `workspace`**: los correos salen por SMTP de Google Workspace
  (`smtp.gmail.com:587`, STARTTLS) como
  `Equipos Consultores <notificaciones@equipos.com.uy>`, con `Reply-To`
  configurable. Cubre los cuatro correos del sistema, cada uno con su
  plantilla: código de verificación (landing y cambio de correo en el
  portal), crear contraseña del portal, recuperar contraseña, y el enlace de
  acceso de los usuarios internos.
- **El atajo se cerró.** Hasta ahora, sin proveedor, el código de la landing
  y el enlace para crear la contraseña del portal **volvían en la respuesta**
  y el sitio público los mostraba como «Modo desarrollo»: cualquiera que
  escribiera el correo de un panelista obtenía el acceso a su cuenta. Ahora:
  - ninguna página pública muestra el enlace ni el código, nunca;
  - sin proveedor, el portal y la landing contestan **«El envío de correos
    no está configurado»** (HTTP 503), igual para cualquier dirección;
  - el modo desarrollo es una señal explícita (`ENVIO_MODO_DESARROLLO`) que
    **en Cloud Run se ignora** aunque alguien la deje en el `.env`.
- **Un fallo no se reporta como éxito**: se reintenta lo transitorio dentro
  de un presupuesto de 15 s, se registra con su motivo, y al usuario se le
  dice «No pudimos enviar el correo en este momento».
- **Cumplimiento → Contacto** muestra el proveedor, el remitente, si la
  contraseña de aplicación está cargada, los envíos de las últimas 24 h
  contra el tope de 2.000 de Workspace, un **correo de prueba** y la lista de
  **envíos fallidos** con destinatario, momento y motivo.

### R-VER · La verificación semántica por lotes

- La verificación ya no manda todas las evidencias en **una** llamada a
  Claude: las parte en **lotes de 25** con hasta **4 en paralelo**.
- Un lote cuya respuesta llega truncada (`stop_reason=max_tokens`) se
  **descarta entero** y se parte en dos, hasta llegar a una evidencia. Nunca
  se aceptan veredictos parciales.
- Lo que no se puede verificar queda **`sin_verificar`**, un estado nuevo y
  distinto de `dudoso`: no excluye ni aprueba, la persona queda marcada
  «verificación incompleta» con confianza baja, y la consulta lo avisa
  arriba del ranking. Los veredictos válidos se siguen aplicando.
- Toda la verificación de una consulta tiene un **presupuesto de 60 s**: con
  eso una consulta de COLOQUIO entra en sus 90 s de timeout.
- El diagnóstico de la consulta muestra lotes, subdivisiones, reintentos,
  tokens y tiempo; y el log de la función, una línea `[verificacion]` sin
  contenido.
- **Modo de depuración** (apagado por defecto): con
  `VERIFICACION_DEPURACION=1`, el cuerpo exacto de cada llamada y su
  respuesta quedan 7 días en el store semántico, y un admin los ve
  enfrentados desde el diagnóstico.

---

## Cómo leer este documento

Cada paso trae el comando y, cuando existe, la salida que se obtuvo al
ensayarlo contra el cluster de pruebas (`scripts/pg_pruebas.sh`) con la
bóveda en la `0022` y el semántico en la `0007`. Donde dice «se obtuvo» es
salida real. Los pasos de Workspace (§0) y los de producción (§8 en
adelante) no se pueden ensayar sin la cuenta real: ahí se dice qué tiene que
aparecer.

El orden importa en dos lugares:

1. **El secreto antes del deploy** (§5 antes de §7). `SMTP_PASSWORD` está
   declarado en `SECRETOS` de `functions/main.py`, y `firebase deploy`
   **falla** si declara un secreto que no existe en Secret Manager.
2. **Las migraciones antes del deploy** (§3 y §4 antes de §7). El código
   nuevo registra cada envío en `envio_correo`; sin la `0023`, el primer
   correo del portal termina en error 500 con «esquema desactualizado».

---

## Paso 0 · Prerrequisito de Workspace: la contraseña de aplicación

> La spec lo dice con todas las letras: **verificar esto primero**. Si la
> organización no permite contraseñas de aplicación, este camino no está
> disponible y hay que ir por OAuth2 o por un servicio transaccional
> (SendGrid, ~US$ 15–20/mes). Nada del resto del documento sirve sin esto.

### 0.1 · Verificación en dos pasos en la cuenta

Las contraseñas de aplicación solo existen para cuentas con **verificación
en dos pasos activada**.

1. **Consola de administración de Google**
   (`admin.google.com`) → **Seguridad → Autenticación → Verificación en dos
   pasos**: tiene que estar **permitida** para la unidad organizativa de
   `notificaciones@equipos.com.uy`.
2. Si la política exige **solo llaves de seguridad**, o la cuenta está en el
   **Programa de Protección Avanzada**, Google no deja crear contraseñas de
   aplicación. En ese caso, parar acá y ver la alternativa.
3. Entrar como `notificaciones@equipos.com.uy` en
   `myaccount.google.com/security` y activar la verificación en dos pasos
   (con un teléfono o una llave de quien vaya a custodiar la cuenta).

### 0.2 · Crear la contraseña de aplicación

Como `notificaciones@equipos.com.uy`, en `myaccount.google.com/apppasswords`:

- Nombre: `paneles-smtp` (es solo una etiqueta).
- Google muestra **16 letras en cuatro grupos** (`abcd efgh ijkl mnop`) una
  sola vez. Copiarla directo al paso 5: no pegarla en un chat, un mail ni un
  documento.

> **No es la contraseña de la cuenta.** Si alguien carga la contraseña normal
> en `SMTP_PASSWORD`, el servidor contesta `535 5.7.8 Username and Password
> not accepted` y la pantalla de Cumplimiento lo muestra como «Workspace
> rechazó la credencial».

> **Uso exclusivo.** La spec lo pide en sus riesgos: esta contraseña da
> permiso de **enviar** como `notificaciones@`. No reutilizar la cuenta para
> otra cosa, y si hay sospecha de filtración, revocarla en la misma página y
> repetir el paso 5 con una nueva.

### 0.3 · Entregabilidad: SPF, DKIM y DMARC

Acá el correo **es** el acceso: uno que cae en spam es igual de inútil que
uno que no sale. Desde cualquier terminal:

```bash
dig +short TXT equipos.com.uy | grep -i spf
dig +short TXT google._domainkey.equipos.com.uy
dig +short TXT _dmarc.equipos.com.uy
```

| Registro | Qué tiene que aparecer | Si falta |
|---|---|---|
| SPF | `"v=spf1 include:_spf.google.com ~all"` (o `-all`) | Agregar el `include` en el DNS del dominio |
| DKIM | `"v=DKIM1; k=rsa; p=MIIB…"` | **Workspace no lo activa solo.** Consola → **Apps → Google Workspace → Gmail → Autenticar correo electrónico** → generar la clave, publicarla en el DNS y **Comenzar autenticación** |
| DMARC | `"v=DMARC1; p=none; …"` como mínimo | Publicar al menos `v=DMARC1; p=none; rua=mailto:<casilla>` |

Sin DKIM, Gmail y Outlook tienden a mandar a spam los correos con enlaces.
Se comprueba de verdad con el correo de prueba del paso 8.

### 0.4 · El Reply-To

Elegir una casilla **que alguien lea** —por ejemplo `panel@equipos.com.uy`—:
los panelistas van a contestar a esos correos. Se usa en el paso 6.

---

## Paso 1 · Preparar la terminal

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
git checkout main && git pull
ls db/boveda/0023_envio_correo.sql db/semantica/0008_captura_verificacion.sql
```

Los dos archivos tienen que existir.

---

## Paso 2 · Túneles, DSN y qué falta

Las dos instancias, cada una por su proxy:

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:$REGION:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA"    -tAc "select current_database()"
psql "$DSN_SEMANTICA" -tAc "select current_database()"
```

Tienen que devolver `paneles_boveda` y `paneles_semantica`, **en ese
orden**. Si salen cruzados, los puertos de los proxies están al revés.

Qué falta, y la precondición:

```bash
psql "$DSN_BOVEDA" -tAc "
  select case when to_regclass('envio_correo') is null
              then 'falta 0023_envio_correo.sql' else 'la 0023 ya está' end,
         to_regclass('persona_celular_idx') is not null as tiene_0022"
psql "$DSN_SEMANTICA" -tAc "
  select case when to_regclass('verificacion_captura') is null
              then 'falta 0008_captura_verificacion.sql' else 'la 0008 ya está' end,
         to_regclass('reproceso') is not null as tiene_0007"
```

Se obtuvo:

```
falta 0023_envio_correo.sql|t
falta 0008_captura_verificacion.sql|t
```

Las `t` son la precondición. Si alguna dice `f`, **parar** y aplicar antes
`DESPLIEGUE - celular como clave de dedup.md` (bóveda `0022`) o
`DESPLIEGUE - Fase 7 completa, Fase 8 y bug convocable.md` (semántico
`0007`).

---

## Paso 3 · Bóveda: `0023_envio_correo.sql`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0023_envio_correo.sql
```

Salida completa del ensayo:

```
BEGIN
CREATE TABLE
COMMENT
COMMENT
COMMENT
CREATE INDEX
CREATE INDEX
CREATE VIEW
COMMENT
REVOKE
COMMIT
```

Crea `envio_correo` (un registro por correo intentado, **sin** el contenido:
ni el código ni el enlace) y la vista `v_envio_correo_por_dia`. El `REVOKE`
es explícito para que se lea que COLOQUIO no las ve.

> **Se puede correr dos veces.** La segunda imprime tres
> `NOTICE: relation … already exists, skipping` y termina en `COMMIT` sin
> cambiar nada. Se verificó en el ensayo.

---

## Paso 4 · Semántico: `0008_captura_verificacion.sql`

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0008_captura_verificacion.sql
```

Salida completa del ensayo:

```
BEGIN
CREATE TABLE
COMMENT
COMMENT
COMMENT
COMMENT
COMMENT
COMMENT
COMMENT
COMMENT
CREATE INDEX
CREATE INDEX
CREATE INDEX
COMMIT
```

Crea `verificacion_captura`, la tabla del modo de depuración. Pasa por el
guardia de PII de la `0005` (el event trigger de `ddl_command_end`) como
cualquier otra: ningún nombre de columna está en `campo_pii`. Si el guardia
la rechazara, el error diría `columna con nombre de PII` y **no** hay que
apagarlo: la migración está mal.

> **Idempotente**: la segunda corrida da cuatro `NOTICE … skipping` y
> `COMMIT`. Se verificó.

> **Por qué en el store semántico.** La captura guarda las mismas respuestas
> de encuesta que ya viven ahí, sin identificadores (el payload que va a
> Claude es `[n] pregunta → respuesta`). Guardarlas en Cloud Logging o en un
> bucket sería una tercera copia con reglas que nadie definió. Al costado del
> payload guarda `respuesta_ids` para que una **baja** borre las capturas que
> llevan respuestas de esa persona (`semantica.borrar_persona`).

---

## Paso 5 · Verificar esquema y la superficie de COLOQUIO

```bash
PYTHONPATH=functions python3 -c "
from panel_api import esquema, db, config
cfg = config.cargar()
with db.boveda(cfg) as b, db.semantica(cfg) as s:
    d = esquema.revisar_stores(b, s)
for store in ('boveda', 'semantica'):
    r = d[store]
    print(store, 'completo' if r['completo'] else 'FALTAN: %s' % r['faltantes'])"
```

Se obtuvo:

```
boveda completo
semantica completo
```

La batería de COLOQUIO, porque la `0023` agrega una tabla y una vista a la
bóveda y la regla del repo es correrla después de tocar cualquier cosa ahí:

```bash
python3 scripts/verificar_coloquio.py
```

Contra el cluster de ensayo dio `18 chequeos · 18 pasados · 0 fallidos · 0
omitidos`. Contra Cloud SQL lo esperado es `18 · 17 · 0 · 1 omitido` (el
intruso, ver `DESPLIEGUE - COLOQUIO Fase 0.md` §7.1.1). Un **fallido** en
«privilegios efectivos» significaría que `coloquio_app` ve `envio_correo`:
no debería pasar (la `0014` le quitó el esquema y la `0023` hace `revoke`).

---

## Paso 6 · El secreto: `SMTP_PASSWORD`

Con la contraseña de aplicación del paso 0.2. Los espacios se sacan; el `-n`
y el `tr` evitan guardar un salto de línea, que Workspace rechaza como
credencial inválida:

```bash
printf '%s' "abcd efgh ijkl mnop" | tr -d ' ' \
  | firebase functions:secrets:set SMTP_PASSWORD --data-file - --project="$PROYECTO"
```

Verificar que quedó (muestra el valor: hacerlo en una terminal propia):

```bash
firebase functions:secrets:access SMTP_PASSWORD --project="$PROYECTO" | wc -c
```

Tiene que dar `16`. Si da `17`, se guardó con salto de línea: repetir.

> **Que no quede en el historial.** Si la terminal guarda historial, borrar
> la línea (`history -d` en bash, o `fc -W` después de editar en zsh). La
> prueba `test_la_contrasena_de_aplicacion_no_esta_en_el_repositorio` cuida
> el repo, no la terminal.

`SMTP_PASSWORD` ya está declarado en `SECRETOS` de `functions/main.py` (la
prueba `test_main.py` falla si falta o sobra). No hay que tocar código.

---

## Paso 7 · `functions/.env`

El `.env` de producción **no está en el repo** (`.gitignore`): está en la
máquina desde la que se despliega. Abrirlo y dejar, además de lo que ya
tenga:

```bash
cat >> functions/.env <<'EOF'

# ── R-MAIL · correo por Google Workspace ──
VERIFICACION_ENVIO_PROVEEDOR=workspace
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USUARIO=notificaciones@equipos.com.uy
SMTP_REMITENTE=Equipos Consultores <notificaciones@equipos.com.uy>
SMTP_REPLY_TO=panel@equipos.com.uy

# ── R-VER · verificación por lotes (son los defaults; se escriben para que
# quien abra este archivo sepa que existen) ──
VERIFICACION_LOTE=25
VERIFICACION_CONCURRENCIA=4
VERIFICACION_PRESUPUESTO_S=60
VERIFICACION_PROFUNDIDAD_MAX=5
EOF
```

`SMTP_REPLY_TO` es la casilla del paso 0.4. **Si ya había una línea
`VERIFICACION_ENVIO_PROVEEDOR=ninguno`, borrarla**: con dos líneas gana la
última, pero dejar la vieja es invitar a la confusión.

Tres comprobaciones antes de seguir:

```bash
grep -c '^VERIFICACION_ENVIO_PROVEEDOR=' functions/.env   # tiene que dar 1
grep -n 'ENVIO_MODO_DESARROLLO' functions/.env            # no tiene que imprimir nada
grep -n 'SMTP_PASSWORD' functions/.env                    # no tiene que imprimir nada
```

- `ENVIO_MODO_DESARROLLO` **nunca** va en el `.env` que se despliega. En
  Cloud Run se ignora igual (la función ve `K_SERVICE`), pero si aparece,
  Cumplimiento → Contacto lo marca con un aviso.
- `SMTP_PASSWORD` **nunca** va en el `.env`: es un secreto (paso 6). Si
  estuviera en los dos lados, `firebase deploy` falla con «secret
  environment variable overlaps non secret environment variable».
- `VERIFICACION_DEPURACION` tampoco va, salvo para diagnosticar (§10).

Un ejemplo comentado de todo esto está en `functions/.env.ejemplo`.

---

## Paso 8 · Desplegar

**Después** de los pasos 3, 4 y 6.

```bash
export VPC_CONNECTOR="$(gcloud functions describe api \
  --gen2 --region="$REGION" --project="$PROYECTO" \
  --format='value(serviceConfig.vpcConnector)')"
printf 'Conector: %s\n' "$VPC_CONNECTOR"
```

**Si imprime vacío, parar** (la función se desplegaría sin conector y moriría
a los 127 segundos sin llegar a las bases).

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"

for f in api procesaringesta; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)'
done
```

Las dos con el mismo conector y `PRIVATE_RANGES_ONLY`. Eso es lo que deja
salir el SMTP directo a internet: con `PRIVATE_RANGES_ONLY` solo el tráfico a
IP privadas va por la VPC, y `smtp.gmail.com` es pública. Con `ALL_TRAFFIC`
el SMTP necesitaría Cloud NAT, y el síntoma sería un correo de prueba que
falla con «No se pudo llegar al servidor de correo».

Que el secreto y las variables llegaron al runtime:

```bash
gcloud run services describe api --region="$REGION" --project="$PROYECTO" \
  --format=json | python3 -c "
import json, sys
env = json.load(sys.stdin)['spec']['template']['spec']['containers'][0]['env']
nombres = {e['name']: ('secreto' if 'valueFrom' in e else e.get('value')) for e in env}
for n in ('SMTP_PASSWORD', 'VERIFICACION_ENVIO_PROVEEDOR', 'SMTP_USUARIO',
          'SMTP_REPLY_TO', 'VERIFICACION_LOTE', 'ENVIO_MODO_DESARROLLO'):
    print(f'{n:30} {nombres.get(n, \"(no está)\")}')"
```

Lo esperado:

```
SMTP_PASSWORD                  secreto
VERIFICACION_ENVIO_PROVEEDOR   workspace
SMTP_USUARIO                   notificaciones@equipos.com.uy
SMTP_REPLY_TO                  panel@equipos.com.uy
VERIFICACION_LOTE              25
ENVIO_MODO_DESARROLLO          (no está)
```

Recargá la app con `Cmd+Shift+R` (o `Ctrl+Shift+R`).

---

## Paso 9 · Probar R-MAIL

### 9.1 · El diagnóstico

App de administración → **Cumplimiento** → tarjeta **Contacto** →
**Revisar**. Tiene que mostrar:

| Fila | Lo esperado |
|---|---|
| Envío de correos | «sí, por `workspace`» |
| Remitente | `Equipos Consultores <notificaciones@…>` · `smtp.gmail.com:587` · contraseña de aplicación **cargada** · responde a la casilla del 0.4 |
| Últimas 24 h | `0 enviado(s) de un tope de 2000 por día · 0 fallido(s)` |
| Modo desarrollo | **apagado** |

Y en los avisos de arriba, solo «Workspace solo envía correo: la
verificación por celular no tiene proveedor» (ver §12). Si dice «Falta
aplicar la migración boveda/0023», volver al paso 3.

### 9.2 · El correo de prueba (y la salida de red)

En la misma tarjeta, **Correo de prueba** → una casilla propia →
**Enviar prueba**. Necesita rol admin o dpo.

- **«✓ Enviado a … en N ms»** y el correo en la bandeja **de entrada** (no en
  spam), de `Equipos Consultores`, con asunto «Correo de prueba del sistema
  de gestión de paneles». Contestarlo: la respuesta tiene que ir al
  Reply-To.
- Mirar los encabezados del correo recibido (en Gmail: ⋮ → **Mostrar
  original**): `SPF: PASS`, `DKIM: PASS`, `DMARC: PASS`. Si DKIM no dice
  PASS, volver al 0.3.

Si no sale, la pantalla dice por qué (ver «Errores y correcciones»). Este es
el paso que la spec pide **antes de dar por hecha la salida de red**: un SMTP
bloqueado por egress falla de forma poco clara, y acá se ve.

### 9.3 · El DoD con un panelista de prueba

Con una persona de prueba **activa** del panel cuyo correo sea una casilla
propia:

1. **Crear contraseña.** En `https://<dominio>/portal`: escribir el correo →
   **«Todavía no tengo contraseña»**. La pantalla dice el mensaje de siempre
   («Si esa dirección corresponde a un panelista…») y **no** muestra ningún
   enlace. El correo llega desde `notificaciones@` con asunto «Creá tu
   contraseña del portal del panelista». El enlace es `PORTAL_URL` seguido
   de `/clave?t=…`: tiene que apuntar al dominio público del portal.
   Abrirlo, elegir la contraseña: queda adentro.
2. **El enlace vence y sirve una vez.** Volver a abrir el mismo enlace: tiene
   que decir que no sirve («puede estar vencido o ya haber sido usado»).
3. **Olvidé mi contraseña.** Salir, **«Olvidé mi contraseña»** con el mismo
   correo. Llega «Recuperá tu contraseña…»; el enlace funciona igual.
4. **La landing.** En `https://<dominio>/inscribirse.html`, completar el
   correo y **Verificar**: llega «Tu código de verificación: NNNNNN», la
   página dice «Te mandamos un código. Vence en 10 minutos.» y **no** muestra
   el código. Ingresarlo: «✓ Verificado».
5. **Usuario interno.** Configuración → alta de un usuario de prueba: el modal
   muestra el enlace y además «También se lo mandamos a …», y el correo llega
   con asunto «Tu acceso al sistema de gestión de paneles».

Después, en Cumplimiento → Contacto → **Envíos** → «Todos»: tienen que
figurar los cinco envíos (más el de prueba) como `enviado`, con su tipo.

### 9.4 · Que el atajo no exista en el sitio público

Desde cualquier terminal, contra lo que sirve Hosting:

```bash
for f in portal.html inscribirse.html js/portal.js; do
  printf '%-18s ' "$f"
  curl -s "https://<dominio>/$f" | grep -ciE 'sin_enviar|modo (desarrollo|prueba)'
done
```

Las tres líneas tienen que dar `0`. Es lo mismo que comprueba
`test_el_modo_desarrollo_no_aparece_en_el_sitio_publico`, pero contra lo
desplegado: si da distinto de cero, Hosting está sirviendo una versión vieja
(caché del CDN; repetir el deploy de `hosting`).

---

## Paso 10 · Probar R-VER

### 10.1 · Una consulta grande

En **Consultas**, un criterio semántico con **A verificar (top-k) = 100**
—son hasta 300 evidencias, lo que manda COLOQUIO— y **Ejecutar**.

- El ranking no tiene que mostrar el aviso rojo «Verificación incompleta».
  Si lo muestra, el aviso dice el motivo; ver «Errores y correcciones».
- Al pie, **Diagnóstico → Verificación por lotes**: para 300 evidencias,
  `Lotes 12 × 25`, `Llamadas 12` (más si hubo subdivisiones o reintentos),
  `Sin verificar 0`, y los `ms` por debajo de 60.000.

### 10.2 · La línea de diagnóstico en el log

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="api"
   AND textPayload:"[verificacion]"' \
  --project="$PROYECTO" --limit=3 --format='value(textPayload)'
```

Una línea por criterio, con `lotes_iniciales`, `verificadas`,
`sin_verificar`, `subdivisiones`, `reintentos`, `stop_reasons`, `tokens` y
`duracion_ms`. **No** tiene que aparecer el texto del criterio ni ninguna
respuesta: es la regla de R-VER.9, y la prueba
`test_el_diagnostico_no_lleva_contenido` la cuida.

### 10.3 · El tiempo, contra el de COLOQUIO

Con la consulta del 10.1, el tiempo total del diagnóstico (`ms_total`)
tiene que quedar **por debajo de 90.000**. La cuenta que lo respalda:

| | Valor |
|---|---|
| Evidencias de una consulta de COLOQUIO | `top_k` ≤ 100 personas × 3 evidencias = **300** |
| Lotes de 25 | **12** |
| En paralelo de a 4 | **3 rondas** |
| Una llamada de 25 evidencias | ~10–20 s |
| Verificación | ~30–60 s, **cortada a los 60 s** por el presupuesto |
| Embedding + recall + reranking | ~3–8 s por criterio |
| **Total** | **≈ 40–70 s, siempre < 90 s** |

Si un día el proveedor está lento, lo que no entra en los 60 s queda
`sin_verificar` y la consulta **termina igual** dentro del timeout, avisando.
Antes, la misma situación era un error o, peor, veredictos parciales.

### 10.4 · El modo de depuración (opcional, y apagarlo después)

Solo si hay un veredicto raro que diagnosticar.

1. Agregar `VERIFICACION_DEPURACION=1` a `functions/.env` y desplegar solo
   funciones:
   ```bash
   firebase deploy --only functions --project="$PROYECTO"
   ```
2. Correr la consulta. En el diagnóstico, como **admin**, aparece «El modo de
   depuración estaba encendido en esta consulta» y el botón **Ver el
   intercambio con Claude**: solicitud y respuesta enfrentadas, por lote y
   por intento. Un rol que no es admin no ve el botón, y la ruta le contesta
   403.
3. **Apagarlo**: sacar la línea del `.env` y volver a desplegar funciones.
   Cada consulta con el modo encendido deja varios cientos de kB en el store
   semántico, que es lo que define cuándo hay que subirlo de tier.

Lo capturado vence a los 7 días y se purga solo en cada lectura y escritura.
Si el modo quedó apagado mucho tiempo, las filas vencidas esperan a la
próxima consulta capturada; se pueden borrar a mano:

```bash
psql "$DSN_SEMANTICA" -c "delete from verificacion_captura where vence_en <= now()"
psql "$DSN_SEMANTICA" -c "
  select count(*) as capturas, pg_size_pretty(pg_total_relation_size('verificacion_captura'))
    from verificacion_captura"
```

---

## 11 · COLOQUIO

### 11.1 · El timeout, verificado

R-VER.8 pide verificar el valor actual en COLOQUIO. Revisado en
`araujomelogno/coloquio`, rama `main`:

| Dónde | Valor |
|---|---|
| `functions/coloquio/motor.py` · `MotorPanelesHttp(timeout=90)` | **90 s** hacia `POST /api/consultas` |
| Lo que manda | `top_k = min(limite, 100)` → hasta 300 evidencias |
| `functions/main.py` de COLOQUIO · `timeout_sec=120` | la función que llama |
| `functions/main.py` de `paneles` · `api` · `timeout_sec=300` | la función que contesta |

Con el presupuesto de verificación de 60 s (§10.3), una consulta de COLOQUIO
entra en los 90 s. **No hace falta cambiar el timeout en ningún proyecto.**
La decisión y la cuenta quedan documentadas acá y en `docs/decisiones.md`
(D66); el día que alguien suba `VERIFICACION_PRESUPUESTO_S` por encima de
~75, este documento es el que dice que hay que subir también los 90 s de
COLOQUIO y los 120 de su función.

### 11.2 · El estado nuevo, del lado de COLOQUIO

COLOQUIO no interpreta los veredictos: los copia y los muestra como texto
(`seleccion.js` pinta `e.veredicto`). Un `sin_verificar` se va a ver como
«sin_verificar», que es correcto. Y `degradaciones` se reenvía entero
(`seleccion.py`), así que la degradación `parcial` de una verificación
incompleta le llega a la persona que selecciona. **Nada se rompe.**

### 11.3 · Un arreglo pendiente en COLOQUIO (no bloquea este despliegue)

Al revisarlo apareció un desajuste **anterior** a este cambio:
`motor._evidencias()` lee `item["detalle"]`, y `paneles` devuelve los
criterios de cada persona en `item["criterios"]`. El resultado es que
COLOQUIO **nunca muestra evidencia ni veredicto** de las consultas
semánticas. Con este cambio importa más, porque es donde se vería
`sin_verificar`. El arreglo, en COLOQUIO:

```python
# functions/coloquio/motor.py
def _evidencias(item):
    salida = []
    for d in item.get("criterios") or item.get("detalle") or []:
        ...
```

y en `normalizar_resultado`, pasar también
`"verificacionIncompleta": item.get("verificacion_incompleta")` para que la
pantalla de selección pueda marcar a esas personas. Va en un PR de
COLOQUIO, con su propia prueba.

---

## 12 · Lo que queda abierto

- **El celular no se puede verificar.** Workspace solo envía correo. En la
  landing, quien complete el celular ve «Por ahora no podemos enviar códigos
  por SMS…» y tiene que dejarlo vacío para inscribirse; en el portal, el
  cambio de celular tampoco se puede confirmar. Hace falta un proveedor de
  SMS (o una plantilla de autenticación de WhatsApp). Está en el anexo de
  `docs/decisiones.md`.
- **Panelistas con correo desactualizado** (riesgo de la spec): no reciben el
  enlace y no tienen forma de recuperarlo solos. Antes de anunciar el portal,
  conviene una revisión de los correos que rebotan: los envíos fallidos de
  Cumplimiento → Contacto con motivo «rechazó el destinatario» son el punto
  de partida.
- **Rebotes asincrónicos.** Un correo que Gmail acepta y después rebota (la
  casilla no existe en el dominio de destino) figura como `enviado`: el
  rebote llega como correo a `notificaciones@`. Conviene que alguien mire esa
  casilla las primeras semanas.

---

## Rollback

El código primero, las bases después (o nunca):

```bash
git checkout <commit-anterior>
firebase deploy --only functions,hosting --project="$PROYECTO"
```

- El código anterior **no usa** `envio_correo` ni `verificacion_captura`:
  dejarlas no molesta. Si igual se quieren sacar:
  ```bash
  psql "$DSN_BOVEDA"    -c "drop view if exists v_envio_correo_por_dia; drop table if exists envio_correo"
  psql "$DSN_SEMANTICA" -c "drop table if exists verificacion_captura"
  ```
  y entonces `esquema.revisar_stores` va a decir que falta la `0023` / `0008`
  hasta que se vuelvan a aplicar.
- **Ojo con el `.env` en el rollback.** El código anterior solo conoce
  `ninguno` y `log`: con `VERIFICACION_ENVIO_PROVEEDOR=workspace` levanta
  «Proveedor de envío de códigos desconocido» en el portal y la landing.
  Volver a `ninguno` en el `.env` **reabre el atajo del modo desarrollo** en
  el sitio público (era el comportamiento anterior). Si hay que volver atrás,
  es preferible `log` —no devuelve nada en la respuesta— y dejar el portal
  sin anunciar mientras tanto.
- El secreto `SMTP_PASSWORD` puede quedarse: el código anterior no lo
  declara y entonces no lo monta. Si se borra
  (`firebase functions:secrets:destroy SMTP_PASSWORD`), revocar también la
  contraseña de aplicación en `myaccount.google.com/apppasswords`.

---

## Errores y correcciones

| Lo que se ve | Qué pasó | Qué hacer |
|---|---|---|
| `firebase deploy` falla con «secret SMTP_PASSWORD … not found» | El secreto no existe y `main.py` lo declara | Paso 6, y volver a desplegar |
| `firebase deploy` falla con «overlaps non secret environment variable» | `SMTP_PASSWORD` está también en `functions/.env` | Sacarlo del `.env` (paso 7) |
| Correo de prueba: «Workspace rechazó la credencial (535)…» | Contraseña normal en vez de la de aplicación, revocada, o con salto de línea | Paso 0.2 y paso 6; `wc -c` tiene que dar 16 |
| Correo de prueba: «No se pudo llegar al servidor de correo: … Network is unreachable» o timeout | La salida a internet está cerrada: el conector quedó en `ALL_TRAFFIC` sin Cloud NAT, o un firewall de egress bloquea el 587 | Paso 8: verificar `PRIVATE_RANGES_ONLY`. Si la política exige `ALL_TRAFFIC`, configurar Cloud NAT en la VPC del conector |
| Correo de prueba: «El servidor rechazó el remitente (553)…» | `SMTP_REMITENTE` no es la cuenta ni un alias suyo | Usar `notificaciones@` o agregar el alias en la cuenta de Workspace |
| El correo llega a spam | Falta DKIM (Workspace no lo activa solo) o DMARC | Paso 0.3; comprobar en «Mostrar original» |
| El portal dice «El envío de correos no está configurado» | `VERIFICACION_ENVIO_PROVEEDOR` no es `workspace` en el runtime | Paso 7 y redeploy; verificar con el comando del paso 8 |
| El portal dice «No pudimos enviar el correo en este momento» | El SMTP falló en ese intento (el motivo exacto está en Envíos fallidos) | Mirar Cumplimiento → Contacto → Envíos; según el motivo, una de las filas de arriba |
| La landing con celular dice «Por ahora no podemos enviar códigos por SMS» | Workspace no cubre celular | Es lo esperado (§12). La persona deja el celular vacío |
| Cumplimiento: «Se enviaron N correos… cerca del tope» | Más de 1.600 en 24 h | Repartir los envíos masivos; si es la norma, evaluar un servicio transaccional (§Costos) |
| Cumplimiento: «Falta aplicar la migración boveda/0023» | No se aplicó | Paso 3 |
| Error 500 «esquema_desactualizado» al pedir un enlace | Idem | Paso 3 |
| Consulta: aviso rojo «Verificación incompleta… porque se terminó el tiempo de verificación» | La verificación no entró en `VERIFICACION_PRESUPUESTO_S` | Bajar el top-k de esa consulta. Si pasa siempre, subir `VERIFICACION_CONCURRENCIA` a 6 (vigilar el 429) antes que el presupuesto (§11.1) |
| «… porque la API del verificador devolvió un error» con muchos `error_http` | 429 (límite de tasa) o 529 (sobrecarga) que persistieron tras el reintento | Bajar `VERIFICACION_CONCURRENCIA`; ver el detalle con el modo de depuración (§10.4) |
| Todo `sin_verificar` con «La API del verificador devolvió un error» y HTTP 401 | `CLAUDE_API_KEY` inválida o vencida | Recargar el secreto como en `DESPLIEGUE - Fase 2.md` |
| Muchas `subdivisiones` en el diagnóstico | Respuestas que truncan con lotes de 25 | Bajar `VERIFICACION_LOTE` a 15 y medir: cada subdivisión paga el lote truncado dos veces |
| «Ver el intercambio con Claude» dice que el modo estaba apagado | Es así: solo se captura con `VERIFICACION_DEPURACION=1` | §10.4 |

---

## Costos

| Concepto | Costo |
|---|---|
| Envío por Workspace SMTP con la cuenta existente | **US$ 0** |
| Tope de Workspace | 2.000 destinatarios por día por cuenta. Con 1.000 panelistas, una invitación a todo el panel entra cómoda; el riesgo es combinar varias olas el mismo día. El conteo está en Cumplimiento → Contacto |
| Servicio transaccional, solo si se supera el tope | ~US$ 15–20/mes |
| Verificación por lotes | **Neutro o levemente mayor**: el prompt de sistema se repite en cada lote (12 veces para 300 evidencias en vez de 1). Una subdivisión paga el lote truncado y las dos mitades |
| Concurrencia | No cambia el costo, solo la latencia |
| Modo de depuración | US$ 0 de servicios; varios cientos de kB por consulta en el store semántico mientras esté encendido. **No dejarlo encendido** |
| Infraestructura nueva | **Ninguna** |

---

## Checklist

- [ ] 0.1 · Verificación en dos pasos activa en `notificaciones@`, y la política permite contraseñas de aplicación
- [ ] 0.2 · Contraseña de aplicación creada, sin pasar por chats ni documentos
- [ ] 0.3 · SPF con `_spf.google.com`, **DKIM activado** en la consola, DMARC publicado
- [ ] 0.4 · Casilla de Reply-To elegida, y alguien que la lea
- [ ] 1 · En `main`, existen `boveda/0023` y `semantica/0008`
- [ ] 2 · Dos proxies; `DSN_BOVEDA` → `paneles_boveda`, `DSN_SEMANTICA` → `paneles_semantica`; precondiciones en `t`
- [ ] 3 · `0023` aplicada (`BEGIN … REVOKE … COMMIT`)
- [ ] 4 · `0008` aplicada (`BEGIN … COMMIT`), sin rechazo del guardia de PII
- [ ] 5 · Los dos stores `completo`; `verificar_coloquio.py` sin fallidos
- [ ] 6 · `SMTP_PASSWORD` en Secret Manager, 16 caracteres
- [ ] 7 · `.env` con `workspace` y `SMTP_*`; **sin** `ENVIO_MODO_DESARROLLO`, **sin** `SMTP_PASSWORD`
- [ ] 8 · `VPC_CONNECTOR` antes del deploy; `functions` y `hosting`; `PRIVATE_RANGES_ONLY`; variables y secreto en el runtime
- [ ] 9.1 · Cumplimiento → Contacto: workspace, contraseña cargada, modo desarrollo apagado
- [ ] 9.2 · Correo de prueba en la bandeja de entrada, SPF/DKIM/DMARC `PASS`, Reply-To correcto
- [ ] 9.3 · Crear contraseña, enlace de un solo uso, olvidé mi contraseña, landing y usuario interno: los cinco correos llegan
- [ ] 9.4 · `sin_enviar` / «modo desarrollo» en `0` en las tres páginas públicas servidas
- [ ] 10.1 · Consulta con top-k 100: sin aviso de incompleta, lotes de 25, < 60 s de verificación
- [ ] 10.2 · Línea `[verificacion]` en el log, sin contenido
- [ ] 10.3 · `ms_total` < 90.000
- [ ] 10.4 · Modo de depuración **apagado** al terminar
- [ ] 11 · COLOQUIO: timeout de 90 s confirmado; arreglo de `_evidencias` anotado para su repo

---

## Referencias

* `docs/decisiones.md` — D65 (correo) y D66 (verificación por lotes).
* `docs/manual/manual.html` — §6.3, §6.4 «Si la verificación quedó
  incompleta», §6.9, §11.4 «El envío de correos», §3.8, §12.1 y §13;
  `docs/manual/MANUAL_panelista.md`.
* `functions/panel_api/correo.py` — el proveedor SMTP y las plantillas.
* `functions/panel_api/verificacion_contacto.py` — `proveedor_de_envio`,
  `modo_desarrollo`, `enviar_y_registrar`, `diagnostico`.
* `functions/panel_api/verificacion.py` — `verificar_por_lotes`, `Claude`,
  `Captura`.
* `functions/panel_api/consultas.py` — `_combinar` y `_resumen_verificacion`.
* `functions/tests/test_envio_correo.py` y
  `functions/tests/test_verificacion_por_lotes.py` — las pruebas de
  aceptación de las dos specs, con SMTP y API simulados.
* `DESPLIEGUE - Fase 4.md` §3.2 — lo que este documento reemplaza sobre
  `VERIFICACION_ENVIO_PROVEEDOR`.
