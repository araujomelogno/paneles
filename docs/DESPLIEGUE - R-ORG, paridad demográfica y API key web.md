# Despliegue — API key web fuera de Secret Manager, paridad de la consulta demográfica y R-ORG

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Pedidos que cubre:**

- `PEDIDO_api_key_web_y_log_de_verificar_clave.md` — R1, R2 y R3 (el portal no
  deja entrar a nadie), el anexo A2 (otros `except` que esconden fallas) y la
  solicitud de cambio **SC** (paridad de acciones entre la consulta
  demográfica y la semántica).
- `SPEC_ambitos_composicion_y_metadatos_carga.md` — R-ORG.1 a R-ORG.6
  (vínculo persona ↔ carga, ámbitos de composición, datos del estudio en la
  carga).

**Precondición:** la bóveda al día hasta la **`0023`**. El store semántico no
cambia.

**Duración estimada:** 45 a 60 minutos, de los cuales 20 son pruebas en la
aplicación.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Código |
|---|---|---|---|---|---|
| PEDIDO R1-R3 + A2 | — | — | **se borra** `FIREBASE_WEB_API_KEY` | **`WEB_API_KEY`** (nueva, con el valor real) | backend |
| SC | — | — | — | — | backend y frontend |
| R-ORG | `0024_r_org_origen_y_ambitos.sql` | — | — | — | backend y frontend |

**Qué NO cambia:** ningún rol, permiso de IAM, cola, conector VPC, tamaño de
instancia ni secreto nuevo. La superficie externa de COLOQUIO
(`coloquio_app`) tampoco: la tabla y la vista nuevas se revocan
explícitamente a `public`.

Los tres pedidos van en **un solo deploy**: tocan los mismos archivos
(`ruteo.py`, `main.py`, `consultas.js`, `panelistas.js`) y desplegarlos por
separado obligaría a desplegar tres veces la misma función.

---

## Qué trae

### PEDIDO R1 · La web API key deja de ser un secreto, y cambia de nombre

El síntoma era «correo o contraseña incorrectos» con **cualquier**
contraseña. La causa: el secreto `FIREBASE_WEB_API_KEY` tenía el placeholder
`AIza...`. Y la razón por la que quedó así es doble:

1. **Firebase reserva el prefijo `FIREBASE_`** (también `X_GOOGLE_`, `EXT_`,
   `KIT_`) y rechaza cualquier secreto **o variable de `.env`** con ese
   nombre: `Key FIREBASE_WEB_API_KEY starts with a reserved prefix`. Se
   comprobó en el código de `firebase-tools` (`lib/functions/env.js`,
   `RESERVED_PREFIXES`). El valor real no se podía cargar nunca.
2. El paso 6 de `DESPLIEGUE - R6.1.a acceso con contraseña.md` traía
   `printf '%s' 'AIza...' | gcloud secrets versions add …`: copiado tal cual,
   guarda el placeholder. Ese paso quedó marcado como reemplazado.

Por eso **el nombre cambia** a `WEB_API_KEY`, aunque el pedido proponía
conservarlo: con el prefijo reservado tampoco la acepta el `.env`. Es una
**variable común** de `functions/.env`, visible en un `describe`. La key es
pública por diseño —es la misma `apiKey` de `web/public/index.html` y
`portal.html`, que recibe cada navegador—, así que en Secret Manager solo
agregaba una vía de falla.

El código ya **no lee** `FIREBASE_WEB_API_KEY`: un nombre viejo leído como
alternativa haría parecer vigente un valor muerto. Y una key que no tiene la
forma de una (`AIza` + 35 caracteres) se frena antes de llamar a Identity
Toolkit.

### PEDIDO R2 · El error de Identity Toolkit queda en el log

Al usuario, un solo mensaje; en el log, el motivo exacto. Cada fallo de
Identity Toolkit deja una línea `[credenciales] …` con el **estado HTTP**, el
**cuerpo de la respuesta** y el correo. Nunca la contraseña; la key, si
apareciera en el cuerpo, se reemplaza por `<api-key>`. También se registran
los errores de red y de timeout, que antes ni se capturaban.

### PEDIDO R3 · «No entró» no es lo mismo que «no se pudo comprobar»

| Respuesta de Identity Toolkit | Antes | Ahora |
|---|---|---|
| 400 por credencial (`INVALID_LOGIN_CREDENTIALS`, `INVALID_PASSWORD`, `EMAIL_NOT_FOUND`, `USER_DISABLED`…) | «Correo o contraseña incorrectos», cuenta como intento | **Igual** |
| 400 por **key inválida** (`API key not valid`, `API_KEY_INVALID`) — el caso del incidente | «Correo o contraseña incorrectos», cuenta como intento | **«No pudimos comprobar tu contraseña por un problema técnico…»**, HTTP 503, **no cuenta** |
| 403, 429, 5xx | ídem | ídem |
| Timeout, DNS, red caída, respuesta ilegible, key ausente o con forma de placeholder | error 500 genérico | ídem |

El mensaje técnico es el mismo exista o no el correo: no revela quién integra
el panel. Lo irreversible (baja, retiro de finalidad, cambio de correo) sigue
**fallando cerrado**: si no se puede comprobar la contraseña, no ocurre; solo
deja de sumar intentos.

> **Por qué el 400 de la key cuenta como técnico** aunque R3 diga «un 400 es
> no entró»: es exactamente el caso del incidente. Tratarlo como credencial
> es el error que el pedido corrige. Un 400 de motivo desconocido sigue siendo
> «no entró». Decisión anotada en `docs/decisiones.md` (D68).

### PEDIDO A2 · Revisión de lo que se esconde

- **`except` que devuelven un valor por defecto sin registrar.** Se
  encontraron tres del mismo tipo que el del incidente y se corrigieron:
  `credenciales.buscar` y `usuarios.buscar_por_email` devolvían `None`
  —«la cuenta no existe»— ante **cualquier** excepción, así que Firebase
  caído se leía como cuenta inexistente; ahora solo `UserNotFoundError` es
  `None` y lo demás se registra y sube. `usuarios.link_de_reseteo` conserva
  su `None` pero deja el motivo en el log. Los demás `except` revisados
  (conversión de números, cerrar una conexión SMTP, una tabla que puede no
  existir) son deliberados y están comentados.
- **Valores en Secret Manager que no son secretos.** Ver §12: se listan y se
  deja una recomendación; **no se mueven en este despliegue**.

### SC · La consulta demográfica tiene la misma barra de acciones

`pintarResultado` sacaba la consulta demográfica por un `return` temprano y
nunca llegaba a la barra de acciones. Ahora la barra es un bloque común y los
dos tipos de resultado tienen: **Ficha**, **Columnas**, **Descargar CSV**,
**Ver quiénes son**, **CSV con datos** y **Crear panel**.

> **Cambio visible para los analistas:** el resultado demográfico **ya no
> muestra nombre ni correo**. Hasta ahora los mostraba sin registrar ninguna
> reidentificación; ahora es seudónimo como el semántico, y **Ver quiénes
> son** los muestra y queda registrado. Conviene avisarlo antes del deploy
> (§11.4). Detalle en D69.

Las diferencias legítimas se conservan: sin puntaje, evidencia, veredicto,
degradaciones ni diagnóstico del puente; el orden es el del listado. Los
endpoints ya aceptaban cualquier conjunto de `id_persona`; el CSV deja vacía
la columna `puntaje` en vez de fallar.

### R-ORG · De qué carga viene cada persona, y sobre quién se calcula la composición

- **`persona_carga`** (bóveda `0024`): muchos a muchos, con `origen`
  `creada` o `reutilizada`. Se registra al dar de alta (antes de encolar) y en
  cada lote; se fija la primera vez y no se pisa. Quien pasa por revisión se
  vincula al resolverla. Una baja lo arrastra (`on delete cascade`).
- **Datos del estudio en la carga:** `carga.fecha_estudio` y
  `carga.publico_objetivo` (texto libre). Se piden al cargar, se corrigen con
  **Datos del estudio** y se ven en todas las fichas por join.
- **Dónde se ven:** tarjeta **Origen** en la ficha (y en la ficha desde un
  resultado), **filtro por carga** en Panelistas, columna **Estudio de
  origen** en los resultados, bloque **Estudios de origen** en Estadísticas.
- **Ámbitos de composición:** **Todos los panelistas** (la bóveda entera,
  incluidas las personas sin panel), **Un panel** (lo de siempre, mismo SQL)
  y **Una carga**. «Todos» admite su **propio universo de referencia**; una
  carga **no** (es un hecho del pasado). La composición a una fecha sigue
  siendo solo por panel.
- **Sin reconstrucción retroactiva:** las personas cargadas antes de la
  `0024` no tienen vínculo y no se les inventa uno; la ficha lo explica
  (§12, decisión D67).

---

## Cómo leer este documento

Cada paso trae el comando y, cuando existe, la salida que se obtuvo al
ensayarlo contra el cluster de pruebas (`scripts/pg_pruebas.sh`) con la
bóveda llevada a la `0023`. Donde dice «se obtuvo» es salida real. Las dos
llamadas a Identity Toolkit del paso 0 también son reales: se hicieron con la
key de `index.html` y un correo inexistente. Los pasos de producción (§6 en
adelante) no se pueden ensayar sin el proyecto: ahí se dice qué tiene que
aparecer.

El orden importa en tres lugares:

1. **`WEB_API_KEY` en el `.env` antes del deploy** (§5 antes de §6). Sin ella,
   el código nuevo contesta «problema técnico» en todo intento de ingreso
   —ya no «contraseña incorrecta», pero igual no entra nadie—.
2. **La migración antes del deploy** (§3 antes de §6). El código nuevo lee
   `persona_carga` en cada ficha; sin la `0024` la ficha de un panelista da
   «esquema desactualizado».
3. **El secreto viejo se borra después del deploy** (§8 después de §6). La
   revisión que está corriendo hoy monta `FIREBASE_WEB_API_KEY`; borrarlo
   antes haría que una instancia nueva de esa revisión no arranque mientras
   el deploy no termina.

---

## Paso 0 · Conseguir la key real y comprobar que sirve desde un servidor

La key es la `apiKey` de la configuración web del proyecto. Dos lugares
donde está, y tienen que coincidir:

- Consola de Firebase → ⚙ **Configuración del proyecto** → **General** →
  «Clave de API web».
- El repo: `web/public/index.html` y `web/public/portal.html`.

```bash
KEY="$(grep -o 'apiKey: "[^"]*"' web/public/index.html | cut -d'"' -f2)"
printf 'largo: %s · empieza con: %s\n' "${#KEY}" "${KEY:0:4}"
```

Tiene que dar `largo: 39 · empieza con: AIza`. Si `index.html` todavía dice
`TU_API_KEY`, tomarla de la consola.

Ahora la prueba que habría ahorrado la hora de diagnóstico: llamar a
Identity Toolkit **sin `Referer`**, como lo hace la función, con un correo
que no existe:

```bash
curl -s -X POST \
  "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=$KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"nadie-prueba-despliegue@ejemplo.invalid","password":"x","returnSecureToken":false}'
```

Se obtuvo (con la key de `index.html`, el 2026-10-08):

```
{ "error": { "code": 400, "message": "INVALID_LOGIN_CREDENTIALS", … } }
```

**Eso es lo correcto**: la key es válida y la protección contra enumeración
está activa (dice `INVALID_LOGIN_CREDENTIALS` y no `EMAIL_NOT_FOUND`). Lo que
**no** tiene que aparecer, y qué significa:

| Respuesta | Qué pasa | Qué hacer |
|---|---|---|
| `"API key not valid. Please pass a valid API key."` (`API_KEY_INVALID`) | La key está mal copiada o es el placeholder. Es la respuesta que se obtuvo con `key=AIza...` | Volver a copiarla de la consola |
| 403 `API_KEY_HTTP_REFERRER_BLOCKED` | La key tiene **restricción por referente HTTP**: desde el navegador funciona, desde la función no (no manda `Referer`) | En Google Cloud → APIs y servicios → Credenciales, quitar la restricción de referente o crear una key para el servidor restringida **por API** a *Identity Toolkit API* y usar esa en `WEB_API_KEY` |
| 403 `API_KEY_SERVICE_BLOCKED` | La key está restringida a APIs que no incluyen Identity Toolkit | Agregar *Identity Toolkit API* a las APIs permitidas de la key |
| `EMAIL_NOT_FOUND` en vez de `INVALID_LOGIN_CREDENTIALS` | La protección contra enumeración está apagada | No bloquea este despliegue, pero conviene activarla (`DESPLIEGUE - R6.1.a…`, paso 4) |

> Las tres filas de 403 y la de la key inválida son, desde este despliegue,
> «no se pudo comprobar» para el portal: el panelista ve el mensaje técnico y
> no se le cuenta el intento. Pero mejor descubrirlas acá que en el portal.

---

## Paso 1 · Preparar la terminal

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
git checkout main && git pull
ls db/boveda/0024_r_org_origen_y_ambitos.sql db/revertir/boveda_0024.sql
```

Los dos archivos tienen que existir.

---

## Paso 2 · Túnel, DSN y qué falta

Solo la bóveda; el store semántico no cambia en este despliegue.

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`.

```bash
psql "$DSN_BOVEDA" -tAc "
  select case when to_regclass('persona_carga') is null
              then 'falta 0024_r_org_origen_y_ambitos.sql' else 'la 0024 ya está' end,
         to_regclass('envio_correo') is not null as tiene_0023"
```

Se obtuvo:

```
falta 0024_r_org_origen_y_ambitos.sql|t
```

La `t` es la precondición. Si dice `f`, **parar** y aplicar antes
`DESPLIEGUE - correo Workspace y verificación por lotes.md` (bóveda `0023`).

Una foto de lo que hay, para comparar después:

```bash
psql "$DSN_BOVEDA" -c "
  select (select count(*) from carga) as cargas,
         (select count(*) from objetivo_composicion) as objetivos,
         (select count(distinct panel_id) from objetivo_composicion) as paneles_con_objetivo"
```

Anotar los tres números: la migración no tiene que cambiar ninguno.

---

## Paso 3 · Bóveda: `0024_r_org_origen_y_ambitos.sql`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0024_r_org_origen_y_ambitos.sql
```

Salida completa del ensayo:

```
BEGIN
CREATE TABLE
COMMENT
COMMENT
CREATE INDEX
ALTER TABLE
ALTER TABLE
ALTER TABLE
COMMENT
COMMENT
ALTER TABLE
DO
ALTER TABLE
CREATE INDEX
COMMENT
CREATE VIEW
COMMENT
REVOKE
COMMIT
```

Qué hizo, en orden: creó `persona_carga` con su índice por carga; agregó
`fecha_estudio` y `publico_objetivo` a `carga`; agregó `ambito` a
`objetivo_composicion` (con `'panel'` por defecto), sus dos controles y el
índice único de «todos», y dejó `panel_id` admitir nulo; creó
`v_carga_resumen`; y revocó las dos cosas nuevas a `public`.

> **Se puede correr dos veces.** La segunda imprime seis `NOTICE: … already
> exists, skipping` y termina en `COMMIT` sin cambiar nada. Se verificó en el
> ensayo.

**Los objetivos de panel que ya existían quedan como estaban.** En el ensayo
se cargó antes un objetivo de sexo para un panel, y después de la migración:

```
 ambito | panel_id | dimension | categoria | proporcion_objetivo
--------+----------+-----------+-----------+---------------------
 panel  |        1 | sexo      | F         |                0.52
 panel  |        1 | sexo      | M         |                0.48
```

Y la base hace valer las dos reglas nuevas (se probó en el ensayo):

```
-- un objetivo de «todos» con panel: rechazado
ERROR:  new row for relation "objetivo_composicion" violates check constraint "objetivo_composicion_ambito_panel"
-- la misma categoría dos veces en «todos»: rechazado
ERROR:  duplicate key value violates unique constraint "objetivo_composicion_todos_unico"
```

Repetir la foto del paso 2: los tres números tienen que ser los mismos.

---

## Paso 4 · Verificar el esquema y la superficie de COLOQUIO

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

(Necesita también el túnel y `DSN_SEMANTICA` del semántico, como en los
despliegues anteriores.) Tiene que dar:

```
boveda completo
semantica completo
```

La `0024` agrega una tabla y una vista, así que corresponde la batería de
COLOQUIO:

```bash
python3 scripts/verificar_coloquio.py
```

Contra el cluster de ensayo, con la `0024` aplicada, dio `18 chequeos · 18
pasados · 0 fallidos · 0 omitidos`. Contra Cloud SQL lo esperado es `18 · 17 ·
0 · 1 omitido` (el intruso). Un **fallido** en «privilegios efectivos»
significaría que `coloquio_app` ve `persona_carga` o `v_carga_resumen`: no
debería pasar.

---

## Paso 5 · `functions/.env`: agregar `WEB_API_KEY`

En `functions/.env` (o `functions/.env.gestion-paneles`, el que se use para
desplegar), **agregar**:

```bash
# ── Portal del panelista (R6.1.a · PEDIDO R1) ──
# La web API key: pública por diseño, la misma de index.html. No es un secreto.
WEB_API_KEY=AIza…………………………………   # el valor real del paso 0, 39 caracteres
```

Y comprobar que no queda ninguna variable con un prefijo reservado:

```bash
grep -nE '^(FIREBASE_|X_GOOGLE_|EXT_|KIT_)' functions/.env* | grep -v ejemplo \
  && echo "HAY UN NOMBRE RESERVADO: firebase deploy lo va a rechazar" \
  || echo "sin prefijos reservados"
grep -c '^WEB_API_KEY=AIza' functions/.env
```

Tiene que decir `sin prefijos reservados` y `1`.

> **No la pongas en Secret Manager.** No hace falta y no agrega seguridad: es
> el valor que ya ve cada navegador. Y `functions/main.py` ya no la declara en
> `SECRETOS`, así que un secreto con ese nombre no llegaría al runtime.

---

## Paso 6 · Desplegar

**Después** de los pasos 3 y 5.

```bash
export VPC_CONNECTOR="$(gcloud functions describe api \
  --gen2 --region="$REGION" --project="$PROYECTO" \
  --format='value(serviceConfig.vpcConnector)')"
printf 'Conector: %s\n' "$VPC_CONNECTOR"
```

**Si imprime vacío, parar** (la función se desplegaría sin conector y no
llegaría a las bases).

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"

for f in api procesaringesta; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)'
done
```

Las dos con el mismo conector y `PRIVATE_RANGES_ONLY`.

Recargar la app con `Cmd+Shift+R` (o `Ctrl+Shift+R`): cambiaron cuatro
pantallas.

---

## Paso 7 · La variable llegó, visible, y el secreto viejo ya no se monta

Es la primera verificación del pedido:

```bash
gcloud run services describe api --region="$REGION" --project="$PROYECTO" \
  --format=json | python3 -c "
import json, sys
env = json.load(sys.stdin)['spec']['template']['spec']['containers'][0]['env']
nombres = {e['name']: ('secreto' if 'valueFrom' in e else e.get('value')) for e in env}
k = nombres.get('WEB_API_KEY') or ''
print('WEB_API_KEY         ', (k[:4] + '…' + k[-4:] + f' ({len(k)} caracteres)') if k else '(no está)')
print('FIREBASE_WEB_API_KEY', nombres.get('FIREBASE_WEB_API_KEY', '(no está)'))"
```

Lo esperado:

```
WEB_API_KEY          AIza…XXXX (39 caracteres)
FIREBASE_WEB_API_KEY (no está)
```

`WEB_API_KEY` aparece **con su valor** (no como `secreto`): eso es lo que
pedía R1, que un placeholder se vea de un vistazo. Si `FIREBASE_WEB_API_KEY`
sigue figurando como `secreto`, el deploy no tomó el `main.py` nuevo:
verificar el commit y repetir el paso 6.

---

## Paso 8 · Borrar el secreto viejo

**Después** del paso 7. Va por `gcloud`, que trata el secreto como uno más
(el CLI de Firebase valida el prefijo reservado al cargar un secreto; para
borrarlo no, pero así no hay dudas):

```bash
gcloud secrets describe FIREBASE_WEB_API_KEY --project="$PROYECTO" \
  --format='value(name,createTime)'
gcloud secrets delete FIREBASE_WEB_API_KEY --project="$PROYECTO"
```

El `describe` muestra que existe (si dice `NOT_FOUND`, ya no está y no hay
nada que hacer). El `delete` pide confirmación. Después:

```bash
gcloud secrets list --project="$PROYECTO" --filter='name~WEB_API_KEY' --format='value(name)'
```

Tiene que quedar vacío. Así nadie confunde el valor muerto con el vigente.

---

## Paso 9 · Probar el PEDIDO (el portal)

### 9.1 · Un panelista entra con su contraseña (DoD, prueba manual completa)

Con una cuenta de prueba (un correo propio enrolado como panelista):

1. Desde su ficha en la administración, **Mandarle el enlace**.
2. Abrir el correo, seguir el enlace, **fijar una contraseña**. Queda adentro.
3. **Cerrar sesión** en el portal.
4. Entrar en `/portal` con el correo y **esa contraseña**. Tiene que entrar.

Este es el paso que antes fallaba siempre.

### 9.2 · Una contraseña incorrecta sigue dando el mensaje genérico

En `/portal`, el mismo correo con otra contraseña: **«Correo o contraseña
incorrectos.»** Igual con un correo que no existe. Los dos textos tienen que
ser idénticos.

### 9.3 · El motivo quedó en el log, sin la contraseña

```bash
gcloud logging read \
  'resource.labels.service_name="api" AND textPayload:"[credenciales]"' \
  --project="$PROYECTO" --freshness=30m --limit=5 --format='value(textPayload)'
```

Lo esperado, por el intento del 9.2:

```
[credenciales] identity_toolkit_http estado=400 email=<el correo> cuerpo={ "error": { "code": 400, "message": "INVALID_LOGIN_CREDENTIALS", …
```

Revisar las líneas: **no** tienen que contener la contraseña escrita en el
9.2 ni la key (`AIza…`).

### 9.4 · (Opcional, en un entorno que no sea producción) el mensaje técnico

Con `WEB_API_KEY=AIza...` en el `.env` de un entorno de prueba, o con el
emulador, el ingreso tiene que contestar **«No pudimos comprobar tu
contraseña por un problema técnico de nuestro lado…»** y el log tiene que
decir `problema=api_key_con_forma_invalida`. Intentarlo once veces seguidas
no bloquea: no cuenta como intento fallido. En producción **no** hace falta
provocarlo; lo cubren las pruebas automáticas
(`functions/tests/test_pedido_api_key_web.py`, con respuestas simuladas de
Identity Toolkit: 400, 403, 429, 500, 503, key inválida, timeout y red caída).

---

## Paso 10 · Probar R-ORG

### 10.1 · Una carga con los datos del estudio

**Panelistas → Cargar panelistas.** Completar nombre, **fecha del estudio** y
**público objetivo**, y cargar un archivo chico de prueba en modo «crear
individuos», con al menos una fila de alguien que ya exista (mismo
documento). Al terminar la carga:

- El filtro **Todas las cargas** del listado muestra la carga por **nombre y
  fecha**. Elegirla: aparecen las personas creadas **y** la reutilizada.
- Abrir la ficha de la reutilizada: tarjeta **Origen** con el estudio, su
  fecha, el público objetivo y **reutilizada**. La de una creada dice
  **creada**.

En la base:

```bash
psql "$DSN_BOVEDA" -c "select nombre, fecha_estudio, publico_objetivo,
       personas_creadas, personas_reutilizadas from v_carga_resumen order by id desc limit 1"
```

Los conteos tienen que coincidir con el resumen de la carga.

### 10.2 · Corregir la fecha se ve en todas las fichas

Con la carga elegida en el filtro, **Datos del estudio** → cambiar la fecha →
Guardar. Abrir dos fichas de esa carga: las dos muestran la fecha nueva. No
se tocó ninguna persona.

### 10.3 · Las personas anteriores

Abrir la ficha de alguien cargado **antes** de hoy: la tarjeta Origen dice
«No hay registro de la carga de la que proviene…». Es lo esperado (§12).

### 10.4 · Composición por ámbito

**Composición:**

- **Ámbito → Todos los panelistas.** El total tiene que coincidir con el
  total de panelistas de **Estadísticas**, y la tarjeta *Sin ningún panel*
  con su «Sin panel».
- **Ámbito → Un panel.** Tiene que dar **exactamente lo mismo que antes del
  deploy** para cada panel (miembros, porcentajes, brecha). Conviene sacar una
  captura de un panel con objetivo antes del paso 6 para comparar.
- **Ámbito → Una carga**, la del 10.1: «Universo de referencia: no aplica»,
  la brecha «no disponible» con su motivo, y el aviso de que muestra el
  presente.
- En «Todos», **Cargar universo de referencia de toda la bóveda** con sexo
  0,52 / 0,48: la brecha aparece. Volver a **Un panel**: su objetivo sigue
  siendo el suyo.

### 10.5 · Columna y estadísticas

- **Consultas**, cualquier consulta → **Columnas** → **Estudio de origen**:
  la columna muestra «nombre · fecha» y «sin dato» para quien no tiene
  vínculo.
- **Estadísticas** → bloque **Estudios de origen** con la carga del 10.1 y
  sus creadas / reutilizadas.

---

## Paso 11 · Probar SC (la consulta demográfica)

### 11.1 · La misma barra

**Consultas** → solo un criterio demográfico (por ejemplo, sexo es F) →
**Consultar**. Tiene que aparecer la barra **Ver quiénes son · Descargar CSV ·
CSV con datos (deshabilitado) · Columnas · Crear panel**, y cada fila con
**Ficha**. **No** tienen que aparecer puntaje, confianza, criterios ni
evidencia, ni nombres ni correos.

### 11.2 · Las acciones

1. **Ficha** de una fila: abre encima, sin la sección «Por qué aparece», con
   Origen. Cerrar: el resultado sigue igual.
2. **Columnas** → agregar una: aparece sin que se vuelva a correr la consulta
   (no hay spinner de consulta).
3. **Descargar CSV**: tres columnas, `puntaje` y `evidencia` vacías.
4. **Ver quiénes son** → confirmar: aparecen los nombres y se habilita **CSV
   con datos**. En **Cumplimiento** figura la reidentificación con motivo
   `consulta`, y la exportación con motivo `exportacion`.
5. **Crear panel**: crea el panel con esas personas; en **Paneles** figura
   con origen «consulta».

### 11.3 · La semántica sigue igual

Una consulta con un criterio semántico: ranking, puntaje, evidencia,
diagnóstico y puente como siempre.

### 11.4 · Avisar a los analistas

Un mensaje corto antes o el día del deploy: *«Desde hoy la consulta solo
demográfica no muestra nombres: usen “Ver quiénes son”, como en la semántica.
Queda registrado igual. A cambio tiene ficha, columnas, CSV y crear panel.»*

---

## 12 · Lo que queda abierto

- **Reconstrucción retroactiva del vínculo (spec §7, riesgo de datos).** Se
  decidió **no** intentarla: `alias_origen` guarda la plataforma, no la
  carga, y dos cargas del mismo origen son indistinguibles; un origen
  inferido sería un dato inventado con apariencia de registro. El vínculo
  arranca desde este despliegue y la ficha lo explica (D67). Si para alguna
  carga puntual se puede probar el origen (por ejemplo, un archivo cuyos
  `id_en_origen` son únicos de esa carga), se puede cargar a mano con
  `insert into persona_carga … origen 'reutilizada'`; documentarlo si se
  hace.
- **Objetivo para una carga:** no se habilitó (recomendación de la spec).
  «Todos» sí.
- **Valores en Secret Manager que no son secretos (A2).** Además de la key,
  hay cinco que se pueden ver sin riesgo y que hoy nadie puede verificar de
  un vistazo: `PORTAL_URL`, `TAREAS_URL`, `TAREAS_CUENTA`,
  `WHATSAPP_PHONE_NUMBER_ID` y `WHATSAPP_WABA_ID`. **No se movieron** en este
  despliegue para no tocar configuración que hoy funciona. La recomendación
  es pasarlos a `functions/.env` en un despliegue propio, uno por uno, con el
  mismo procedimiento que la key (agregar al `.env`, sacar de `SECRETOS`,
  desplegar, verificar con `describe`, borrar el secreto). `VERIFICACION_SAL`,
  `DESAFIO_SECRETO`, `WHATSAPP_TOKEN`, las claves de APIs, los DSN y
  `SMTP_PASSWORD` **sí** son secretos y se quedan.
- **El portal no tiene capturas en el manual** del mensaje técnico: se
  describe en la sección 13.

---

## Rollback

El código primero, la base después (o nunca):

```bash
git checkout <commit-anterior>
firebase deploy --only functions,hosting --project="$PROYECTO"
```

**Ojo con la key en el rollback.** El código anterior lee
`FIREBASE_WEB_API_KEY`, que no se puede cargar (es el problema original): al
volver atrás el backend **vuelve el incidente** y nadie entra al portal. Si
el problema está en una pantalla, es preferible volver **solo el frontend**
y dejar el backend nuevo:

```bash
git checkout <commit-anterior> -- web/public
firebase deploy --only hosting --project="$PROYECTO"
git checkout main -- web/public
```

El frontend anterior funciona contra el backend nuevo (la composición por
panel y todas las rutas que usa siguen existiendo); lo único que se nota es
que la tabla demográfica vieja muestra «—» donde antes iba el nombre.

La base:

- El código anterior **no usa** `persona_carga`, `v_carga_resumen`,
  `carga.fecha_estudio`, `carga.publico_objetivo` ni
  `objetivo_composicion.ambito`. Dejarlos no molesta (el `insert` del
  objetivo de un panel sigue funcionando: `ambito` tiene default). **Lo
  recomendado es no revertir la base.**
- Si igual hay que hacerlo, **antes** exportar lo que se pierde:
  ```bash
  psql "$DSN_BOVEDA" -c "\copy persona_carga to 'persona_carga.csv' csv header"
  psql "$DSN_BOVEDA" -c "\copy (select id, fecha_estudio, publico_objetivo from carga) to 'carga_estudio.csv' csv header"
  psql "$DSN_BOVEDA" -c "\copy (select * from objetivo_composicion where ambito = 'todos') to 'objetivo_todos.csv' csv header"
  psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0024.sql
  ```
  Salida del ensayo:
  ```
  BEGIN
  DROP VIEW
  DROP TABLE
  DELETE 0
  DROP INDEX
  ALTER TABLE
  ALTER TABLE
  ALTER TABLE
  ALTER TABLE
  ALTER TABLE
  ALTER TABLE
  COMMIT
  ```
  Los objetivos de cada panel quedan intactos (en el ensayo: los dos de
  antes, después del rollback). Volver a aplicar la `0024` después funciona
  (se probó).
- El secreto `FIREBASE_WEB_API_KEY` borrado en el paso 8 **no** se recrea: no
  servía.

---

## Errores y correcciones

| Lo que se ve | Qué pasó | Qué hacer |
|---|---|---|
| `firebase deploy` falla con «Key … starts with a reserved prefix (X_GOOGLE_ FIREBASE_ EXT_ KIT_)» | Quedó una variable con ese prefijo en el `.env` (por ejemplo, `FIREBASE_WEB_API_KEY=` copiado del despliegue anterior) | Paso 5: renombrarla o sacarla |
| Portal: «No pudimos comprobar tu contraseña por un problema técnico» **para todos** | `WEB_API_KEY` falta, tiene la forma de un placeholder o Identity Toolkit la rechaza | Log `[credenciales]`: `sin_api_key` → paso 5; `api_key_con_forma_invalida` → paso 0 y 5; `estado=400 … API key not valid` → key mal copiada; `estado=403 … REFERRER_BLOCKED` o `SERVICE_BLOCKED` → restricciones de la key (paso 0) |
| Portal: «No pudimos comprobar…» de vez en cuando | Identity Toolkit lento o caído (`identity_toolkit_red` o `estado=5xx` en el log) | Nada: el intento no cuenta y la persona reintenta. Si persiste, ver el estado de Google Cloud |
| Portal: «Correo o contraseña incorrectos» con la contraseña correcta | Ya no puede ser la key (eso ahora da el mensaje técnico). Revisar el log: `INVALID_LOGIN_CREDENTIALS` es una credencial mal escrita o una cuenta sin contraseña fijada | Mandarle el enlace desde la ficha (9.1) |
| `describe` del paso 7 muestra `WEB_API_KEY (no está)` | El `.env` usado no es el del deploy (por ejemplo, se editó `.env` y se desplegó con `.env.gestion-paneles`, que tiene prioridad) | Ponerla en el que se usa y repetir el paso 6 |
| La ficha de un panelista da 500 «esquema desactualizado … persona_carga» | Falta la `0024` | Paso 3 |
| Composición: «Ámbito desconocido» | El frontend nuevo contra un backend viejo (deploy a medias) | Repetir el paso 6 con `functions,hosting` |
| Composición de una carga vacía | La carga es anterior a la `0024` | Esperado (§12) |
| Consulta demográfica sin nombres | Es el cambio de SC | «Ver quiénes son» (11.2) |
| Crear un atributo `estudio_de_origen` da «clave reservada» | La usa la columna de resultados | Elegir otra clave |
| `verificar_coloquio.py` con un fallido en privilegios | `coloquio_app` ve algo de la `0024` (no debería) | Revisar los `grant` recientes; la `0024` revoca a `public` |

---

## Costos

| Concepto | Costo |
|---|---|
| Key como variable de entorno | **US$ 0**. Quita una lectura de secreto por arranque de instancia |
| Logs de `[credenciales]` | Una línea por intento fallido de ingreso; despreciable |
| Paridad de la consulta demográfica | **US$ 0**: reutiliza endpoints; las columnas y el panel hacen las mismas consultas que en la semántica |
| `persona_carga` | Marginal: una fila de pocos bytes por persona y carga (1.131 personas → 1.131 filas) |
| Datos del estudio en `carga` | **US$ 0** |
| Composición «todos» | Una agregación sobre toda la bóveda por consulta de la pantalla. Con los volúmenes actuales en `db-f1-micro` es instantánea (en el ensayo, milisegundos); si crece a decenas de miles, medirla antes de pensar en índices o caché |
| Infraestructura nueva | **Ninguna** |

---

## Checklist

- [ ] 0 · Key real de 39 caracteres; `curl` sin `Referer` devuelve `INVALID_LOGIN_CREDENTIALS` (no `API key not valid` ni 403)
- [ ] 1 · En `main`, existen `boveda/0024` y `revertir/boveda_0024.sql`
- [ ] 2 · Túnel a la bóveda; precondición en `t`; foto de cargas y objetivos anotada
- [ ] 3 · `0024` aplicada (`BEGIN … REVOKE … COMMIT`); la foto no cambió
- [ ] 4 · Los dos stores `completo`; `verificar_coloquio.py` sin fallidos
- [ ] 5 · `WEB_API_KEY` en el `.env` que se despliega; ningún prefijo reservado
- [ ] 6 · `VPC_CONNECTOR` antes del deploy; `functions` y `hosting`; `PRIVATE_RANGES_ONLY`
- [ ] 7 · `WEB_API_KEY` visible con 39 caracteres; `FIREBASE_WEB_API_KEY` no está
- [ ] 8 · Secreto `FIREBASE_WEB_API_KEY` borrado
- [ ] 9.1 · Un panelista fija su contraseña, cierra sesión y **entra con ella**
- [ ] 9.2 · Contraseña incorrecta y correo inexistente: el mismo mensaje
- [ ] 9.3 · Línea `[credenciales]` con estado y cuerpo; sin contraseña ni key
- [ ] 10.1 · Carga con fecha y público; filtro por carga; Origen creada / reutilizada
- [ ] 10.2 · Corregir la fecha se ve en las fichas
- [ ] 10.4 · Todos / panel (igual que antes) / carga (no aplica); objetivo de «todos» independiente
- [ ] 10.5 · Columna Estudio de origen; Estadísticas → Estudios de origen
- [ ] 11.1-11.3 · Demográfica con la barra completa y sin nombres; semántica igual
- [ ] 11.4 · Analistas avisados del cambio de nombres

---

## Referencias

* `docs/decisiones.md` — D67 (R-ORG), D68 (API key y «no se pudo comprobar»),
  D69 (paridad demográfica).
* `docs/manual/manual.html` — §3.5 «El origen», §3.6 (datos del estudio,
  filtro por carga), §6.3 (columna Estudio de origen), §6.10, §7 y §7.5, §13
  («No pudimos comprobar…»), §14; `docs/manual/MANUAL_panelista.md`.
* `functions/panel_api/credenciales.py` — `WEB_API_KEY`, `verificar_clave`,
  `_registrar`, `es_cuenta_inexistente`.
* `functions/panel_api/portal.py` — `MENSAJE_COMPROBACION_NO_DISPONIBLE`,
  `_verificar`.
* `functions/panel_api/cargas.py` — `registrar_vinculos`, `origen_de`,
  `editar`. `functions/panel_api/composicion.py` —
  `composicion_de_ambito`.
* `web/public/js/paginas/consultas.js` — `barraDeAcciones`,
  `engancharAcciones`, `tablaDemografica`.
* `functions/tests/test_pedido_api_key_web.py`,
  `functions/tests/test_sc_paridad_demografica.py`,
  `functions/tests/test_r_org_origen_y_ambitos.py` — las pruebas de
  aceptación de los tres pedidos; `functions/tests/test_main.py` vigila los
  prefijos reservados.
* `DESPLIEGUE - R6.1.a acceso con contraseña.md` §6 — lo que este documento
  reemplaza sobre la key.
