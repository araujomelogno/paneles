# Despliegue — R6.1.a · Acceso del panelista con usuario y contraseña

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Cubre:** `specs/SPEC_R6.1a_acceso_con_contrasena.md` (reemplaza a R6.1)
**Migración:** `db/boveda/0018_r6_1a_acceso_con_contrasena.sql` · **una sola base**
**Duración estimada:** 40 a 55 minutos, la mitad en la consola de Firebase
**Precondición:** Fase 6 desplegada y la bóveda al día hasta la `0017`

---

## Qué cambia, en una frase

El panelista dejaba de entrar al portal si no le llegaba un correo. Desde
este despliegue entra **con su contraseña**, cuando quiera, y el correo
queda para crearla y recuperarla.

### Lo que hay que mirar de este despliegue en particular

Hay un paso que no es técnico y que decide si la fase sirve: **los
panelistas ya enrolados no tienen contraseña.** Nadie se la pidió nunca.
Hasta que no hagan el camino del enlace, el portal les está cerrado, y los
que tengan el correo desactualizado no van a poder hacerlo solos.

Por eso el paso 9 no es «avisar»: es una campaña con un plan para los que no
respondan. Si el despliegue se da por terminado en el paso 8, lo que queda
arriba es un portal que nadie puede usar.

### El camino, en una mirada

| # | Paso | Dónde |
|---|---|---|
| 1 | Túnel y DSN | Terminal |
| 2 | Confirmar que falta solo la `0018` | Terminal |
| 3 | Aplicar `boveda/0018` | Bóveda |
| 4 | Verificar el esquema | Bóveda |
| 5 | Protección contra enumeración y política de contraseñas | Consola de Firebase |
| 6 | Cargar `FIREBASE_WEB_API_KEY` y desplegar | Terminal |
| 7 | **Probar que el mensaje de error no revela nada** | Navegador |
| 8 | **Probar las tres acciones que piden la contraseña** | Navegador |
| 9 | **Hacer que los panelistas creen su contraseña** | Operación |

**Los pasos 1 a 4 y el rollback están ensayados** contra un Postgres
levantado al estado que deja la `0017`; las salidas de abajo son las de ese
ensayo. Del 5 al 9 son de Firebase, del navegador y de operación.

---

## Paso 1 · Túnel y DSN

Esta migración toca **solo la bóveda**, pero el diagnóstico mira las dos.

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &

export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"

psql "$DSN_BOVEDA" -tAc "select current_database()"   # → paneles_boveda
```

---

## Paso 2 · Confirmar que falta solo la `0018`

```bash
python3 scripts/verificar_esquema.py
```

Con la Fase 6 aplicada y ésta no, la salida termina así:

```
        falta motivo_acceso_portal  (el catálogo de motivos de acceso al portal, que separa los enlaces emitidos de los intentos fallidos)
        falta v_acceso_portal  (los pedidos de acceso al portal con su motivo resuelto: qué enlaces siguen sirviendo y qué intentos fallaron)

Faltan 1 migración(es). Para aplicarlas, con el Auth Proxy abierto:

  psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda \
       -v ON_ERROR_STOP=1 -f db/boveda/0018_r6_1a_acceso_con_contrasena.sql

El orden importa: se aplican de menor a mayor.
```

> **Si falta alguna anterior, pará.** Aplicá primero la que falte, en orden.
> Este documento asume la `0017` aplicada.

---

## Paso 3 · Aplicar `boveda/0018`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 \
     -f db/boveda/0018_r6_1a_acceso_con_contrasena.sql
```

Salida esperada, completa:

```
ALTER TABLE
CREATE TABLE
COMMENT
INSERT 0 4
ALTER TABLE
COMMENT
ALTER TABLE
ALTER TABLE
COMMENT
DROP INDEX
DROP INDEX
CREATE INDEX
CREATE INDEX
CREATE VIEW
COMMENT
```

**Es una migración corta y sin riesgo de datos.** Agrega tres columnas con
valor por omisión, un catálogo de cuatro filas, una vista, y reemplaza dos
índices por los mismos más la columna `motivo`. No toca ninguna fila
existente salvo para darles el motivo `enlace`, que es lo que eran.

### Qué hace, y por qué hay una migración si la spec decía que no

La spec (§6) dice que no hacen falta cambios de esquema, y para la
credencial es cierto: la contraseña la administra Firebase Auth. Lo que la
spec no mira es el **límite de intentos fallidos** de R6.1.b.

Ese contador no puede compartir fila con el de los enlaces. Si la
compartiera, cinco intentos de adivinar una contraseña dejarían a la persona
sin poder pedir el enlace para recuperarla: **el ataque le cerraría justo la
puerta de salida.** La columna `motivo` es lo que separa los dos límites.

---

## Paso 4 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

```
    ✓ 0018_r6_1a_acceso_con_contrasena.sql
...
Las dos bases están al día.
```

Y dos comprobaciones directas, que son las que importan de esta migración:

```bash
# El catálogo quedó sembrado, con el histórico de la Fase 6 incluido.
psql "$DSN_BOVEDA" -c "select codigo, etiqueta, es_intento from motivo_acceso_portal order by orden"
```

```
    codigo     |              etiqueta               | es_intento
---------------+-------------------------------------+------------
 alta_clave    | Enlace para crear la contraseña     | f
 recuperacion  | Enlace para recuperar la contraseña | f
 login_fallido | Intento de ingreso fallido          | t
 enlace        | Enlace de ingreso (Fase 6)          | f
```

```bash
# Y un motivo inventado lo rechaza la base, no el código.
psql "$DSN_BOVEDA" -c "insert into acceso_portal (email_hash, motivo) values ('x','inventado')"
```

```
ERROR:  insert or update on table "acceso_portal" violates foreign key constraint "acceso_portal_motivo_fk"
DETAIL:  Key (motivo)=(inventado) is not present in table "motivo_acceso_portal".
```

---

## Paso 5 · Firebase Auth: enumeración y política de contraseñas

En la consola: **Authentication → Settings**.

### 5.1 · Habilitar el proveedor «Correo electrónico/contraseña»

**Authentication → Sign-in method.** Tiene que estar **habilitado**. El
proveedor «Vínculo de correo electrónico (acceso sin contraseña)» que pedía
la Fase 6 **ya no lo usa nadie**: se puede dejar habilitado sin
consecuencias, pero conviene apagarlo para no dejar una puerta que nada usa.

### 5.2 · Activar la protección contra enumeración de correos

**Authentication → Settings → User actions → Email enumeration protection.**

> **Qué protege, y qué no.** El mensaje genérico que ve quien usa el portal
> lo escribe nuestro servidor, así que esto **no** es lo que evita la
> filtración. Lo que agrega es cubrir a quien llame a Identity Toolkit por
> fuera del portal, con la *web API key* que es pública por diseño. Es
> defensa en profundidad y vale los treinta segundos que lleva.

### 5.3 · Mirar la política de contraseñas

**Authentication → Settings → Password policy.** El mínimo que el sistema
repite en castellano es de **6 caracteres**, que es el piso de Firebase. Si
acá se sube, Firebase rechaza y el portal muestra el error: no hay nada que
cambiar en el código, pero conviene saber que el texto del portal dice
«mínimo seis» y habría que actualizarlo.

---

## Paso 6 · `FIREBASE_WEB_API_KEY` y desplegar

El login pasa por la función —el porqué está en
[D53](decisiones.md#d53)— y para comprobar una contraseña necesita la *web
API key* del proyecto.

```bash
# Es la misma `apiKey` que ya está en la configuración del frontend
# (web/public/js/config.js). Es pública por diseño; se guarda en Secret
# Manager igual, para que el deploy la monte y para que cambiarla no sea
# editar código.
gcloud secrets create FIREBASE_WEB_API_KEY --replication-policy=automatic 2>/dev/null \
  || echo "ya existe"
printf '%s' 'AIza...' | gcloud secrets versions add FIREBASE_WEB_API_KEY --data-file=-
```

> **Si falta, no entra nadie y el síntoma es confuso.** El portal contesta
> «Falta `FIREBASE_WEB_API_KEY`…» en la ruta de ingreso, que es un error
> claro, pero solo lo ve quien intenta entrar. `functions/tests/test_main.py`
> comprueba que esté declarada en `SECRETOS`; lo que ninguna prueba puede
> comprobar es que tenga valor.

`PORTAL_URL` ya está declarada desde la Fase 6. **Verificá que apunte al
dominio correcto**, porque ahora el enlace lleva a `/clave` y no a `/entrar`:

```bash
gcloud secrets versions access latest --secret=PORTAL_URL   # → https://<dominio>
```

Y desplegar:

```bash
export VPC_CONNECTOR=<el de siempre>
firebase deploy --only functions,hosting
```

El `rewrite` de `/entrar` se reemplaza por `/clave` en `firebase.json`: va en
el mismo deploy de hosting.

---

## Paso 7 · Probar que el mensaje de error no revela nada

**Este paso y el 8 son los que deciden si la fase está bien desplegada.** Un
portal que deja entrar no alcanza.

Desde una ventana de incógnito, en `https://<dominio>/portal`:

| # | Qué hacer | Qué tiene que pasar |
|---|---|---|
| 7.1 | Entrar con un correo **que es de un panelista** y una contraseña cualquiera | `Correo o contraseña incorrectos.` |
| 7.2 | Entrar con un correo **inventado** y una contraseña cualquiera | **Exactamente el mismo texto** |
| 7.3 | Tocar «Olvidé mi contraseña» con el correo de un panelista | `Si esa dirección corresponde a un panelista, va a recibir un enlace para crear o recuperar su contraseña. Revisá tu correo.` |
| 7.4 | Lo mismo con un correo inventado | **Exactamente el mismo texto**, y no llega ningún correo |

> **Si 7.1 y 7.2 difieren en algo** —el texto, el tiempo de respuesta
> notoriamente distinto, el código HTTP— el despliegue no está hecho.
> Cualquiera puede averiguar quién integra el panel probando direcciones.

Y el límite de intentos, que se puede ver sin esperar una hora:

```bash
# Diez intentos fallidos contra el mismo correo; el undécimo tiene que
# cambiar de respuesta.
for i in $(seq 1 11); do
  curl -s -o /dev/null -w "%{http_code} " -X POST https://<dominio>/api/portal/sesion/clave \
    -H 'Content-Type: application/json' \
    -d '{"email":"prueba@ejemplo.invalid","clave":"mal"}'
done; echo
```

Los primeros diez dan `401`; a partir del undécimo, `409` («Se registraron
demasiados intentos fallidos para esa dirección»). Después:

```bash
psql "$DSN_BOVEDA" -c \
  "select motivo, count(*) from v_acceso_portal where es_intento group by motivo"
```

Tiene que haber once filas `login_fallido` y **ningún correo en claro**: la
tabla guarda el hash.

---

## Paso 8 · Probar las tres acciones que piden la contraseña

Con un panelista de prueba que ya tenga su contraseña puesta:

| # | Acción en el portal | Qué tiene que pasar |
|---|---|---|
| 8.1 | **Mis derechos → Retirar** un permiso | Se abre el modal de contraseña. Cancelando, no pasa nada |
| 8.2 | **Mis derechos → Darte de baja** | Idem, y con la contraseña mal dice «La contraseña no coincide» |
| 8.3 | **Mis datos → Cambiar correo** | Pide el código **y** la contraseña |
| 8.4 | **Mis datos → Cambiar la contraseña** con la actual mal | «La contraseña no coincide» |
| 8.5 | Pedir un canje, apagar un canal, corregir un atributo | **No piden nada**: la sesión alcanza |

Y la prueba de R6.1.e, que necesita dos navegadores:

1. Entrá con el mismo panelista en dos navegadores distintos.
2. En el primero, **cambiá la contraseña**.
3. Recargá el segundo: tiene que quedar afuera.

Lo mismo con una baja, y ahí además la credencial queda inutilizable: la
cuenta de Auth se deshabilita.

```bash
# Después de la baja, el pedido de recuperación no emite nada.
psql "$DSN_BOVEDA" -c \
  "select motivo, era_panelista from v_acceso_portal order by creado_en desc limit 1"
```

---

## Paso 9 · Hacer que los panelistas creen su contraseña

**Este paso es la fase.** Hasta acá hay un portal que funciona y nadie puede
usar.

### 9.1 · Antes de anunciar nada: ver quién no va a poder

El riesgo que la spec anota (§9) es concreto: un panelista con el correo
desactualizado no recibe el enlace y queda sin acceso, y no se va a enterar
de que el problema es el correo.

```sql
-- Panelistas activos sin correo: no hay a dónde mandarles nada.
select count(*) from persona
 where estado = 'activa' and coalesce(trim(email), '') = '';

-- Y los que tienen correo pero nunca verificaron ninguno: son los
-- candidatos a que la dirección esté vieja.
select count(*)
  from persona p
 where p.estado = 'activa'
   and coalesce(trim(p.email), '') <> ''
   and not exists (
     select 1 from verificacion_contacto v
      where v.canal = 'email' and v.destino = lower(p.email)
        and v.estado in ('verificado', 'usado'));
```

A los del primer grupo hay que contactarlos por otra vía y cargarles el
correo desde la ficha **antes** de la campaña. A los del segundo, esperar un
rebote y tratarlos después.

### 9.2 · La campaña

El enlace se manda de a uno desde **Panelistas → Ver ficha → Acceso al
portal → Mandarle el enlace**, o masivamente por la API:

```bash
# Ejemplo: a todos los activos de un panel. Espaciado a propósito: el
# límite por origen es de 15 por hora y el proveedor de correo tiene el
# suyo.
for id in $(psql "$DSN_BOVEDA" -tAc "
      select p.id_persona from persona p
        join membresia m on m.id_persona = p.id_persona and m.estado = 'activo'
       where p.estado = 'activa' and m.panel_id = 1"); do
  curl -s -X POST "https://<dominio>/api/panelistas/$id/acceso-portal" \
       -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
  sleep 5
done
```

> **El enlace vence a las 24 horas.** Para una campaña grande conviene
> mandarlo en tandas y reenviar a los que no lo usaron, en vez de mandarlo
> todo junto un viernes.

### 9.3 · Seguir quién entró

```sql
-- Cuántos ya tienen cuenta del portal, sobre el total de activos.
select
  (select count(*) from cuenta_panelista) as con_cuenta,
  (select count(*) from persona where estado = 'activa') as activos;

-- Enlaces mandados que nadie usó y ya vencieron: ésos hay que reenviar.
select count(*) from v_acceso_portal
 where not es_intento and usado_en is null and vence_en < now();
```

Las inscripciones nuevas no entran en esta campaña: **aprobar una
inscripción emite el enlace sola** (R3.7 + R6.1.a).

---

## Rollback

Ensayado. Devuelve la bóveda exactamente al estado de la `0017`:

```sql
begin;
drop view if exists v_acceso_portal;
drop index if exists acceso_portal_por_email;
drop index if exists acceso_portal_por_origen;
create index acceso_portal_por_email on acceso_portal (email_hash, creado_en desc);
create index acceso_portal_por_origen on acceso_portal (origen_hash, creado_en desc)
  where origen_hash is not null;
alter table acceso_portal drop constraint if exists acceso_portal_motivo_fk;
alter table acceso_portal drop column if exists motivo;
alter table acceso_portal drop column if exists emitido_por;
alter table acceso_portal drop column if exists emitido_por_email;
drop table if exists motivo_acceso_portal;
commit;
```

La `0018` se puede volver a aplicar después sin tocar nada: comprobado en el
ensayo.

**Lo que el rollback sí pierde**, y conviene saberlo antes de correrlo: el
motivo y el emisor de todas las filas escritas desde que la `0018` está
aplicada. Las filas quedan —los enlaces y los intentos siguen contándose—
pero sin poder distinguir un intento fallido de un enlace emitido, y sin
saber qué responsable disparó cada envío.

> **Antes del rollback de la base, hay que volver atrás el código.** Con la
> `0018` revertida y la función nueva arriba, el portal falla al escribir en
> `acceso_portal` y **nadie entra**. El orden es: `firebase deploy` del
> commit anterior, después el SQL.

### Lo que el rollback no deshace

Las contraseñas que los panelistas ya hayan creado **quedan en Firebase
Auth**. Eso no es un problema: volver al enlace mágico simplemente las
ignora. Si después se vuelve a avanzar, las contraseñas siguen sirviendo.

---

## Errores que se pueden encontrar

| Mensaje | Qué pasó | Qué hacer |
|---|---|---|
| `Falta FIREBASE_WEB_API_KEY: sin ella no se puede comprobar ninguna contraseña y nadie entra al portal.` | El secreto no está cargado, o está pero no declarado en `SECRETOS` | Paso 6, y volver a desplegar: `firebase deploy` solo monta los declarados |
| `Falta PORTAL_URL: sin la dirección del portal no se puede armar el enlace para fijar la contraseña.` | Idem con `PORTAL_URL` | Cargarlo y redesplegar |
| `Ese enlace no sirve: puede estar vencido o ya haber sido usado.` | Lo esperable a las 24 horas, o si la persona lo abrió dos veces | Pedir otro desde «Olvidé mi contraseña» |
| `Se registraron demasiados intentos fallidos para esa dirección.` | Diez fallos en una hora contra ese correo | Esperar. **Pedir el enlace de recuperación sigue funcionando**: es a propósito |
| `Se registraron demasiados intentos fallidos desde este dispositivo.` | Treinta fallos en una hora desde la misma salida a internet | Si es una oficina entera detrás de una IP, subir `MAX_FALLOS_POR_ORIGEN_POR_HORA` |
| `Esa persona ya tiene una cuenta del portal vinculada.` | Dos personas de la bóveda comparten correo, o quedó un vínculo viejo | Mirar `cuenta_panelista` y `persona` con ese correo: casi siempre son dos fichas de la misma persona sin fusionar |
| `insert or update on table "acceso_portal" violates foreign key constraint "acceso_portal_motivo_fk"` | Código viejo o nuevo contra la base del otro lado | Alinear: o aplicar la `0018`, o volver el código atrás |
| `Esa persona no tiene correo registrado, así que no hay a dónde mandarle el enlace.` | Se intentó emitir desde la ficha de alguien sin correo | Cargarle el correo primero (paso 9.1) |

---

## Checklist

- [ ] 1 · Túnel abierto y `DSN_BOVEDA` apuntando a `paneles_boveda`
- [ ] 2 · El diagnóstico dice que falta solo la `0018`
- [ ] 3 · `0018` aplicada, con la salida de quince líneas
- [ ] 4 · «Las dos bases están al día» y el catálogo con sus cuatro motivos
- [ ] 5 · Correo/contraseña habilitado, enumeración protegida, política mirada
- [ ] 6 · `FIREBASE_WEB_API_KEY` cargada, `PORTAL_URL` verificada, deploy hecho
- [ ] 7 · **Dos correos —uno real, uno inventado— dan la misma respuesta**
- [ ] 7 · El undécimo intento fallido da 409 y la tabla no tiene correos en claro
- [ ] 8 · **Las tres acciones irreversibles piden la contraseña; las demás no**
- [ ] 8 · Cambiar la contraseña deja afuera a la otra sesión
- [ ] 9 · Revisados los panelistas sin correo, **antes** de la campaña
- [ ] 9 · Campaña mandada en tandas, con un plan de reenvío
- [ ] 9 · Seguimiento de `con_cuenta` sobre `activos` acordado con alguien

**La fase no está desplegada hasta que el paso 9 esté en marcha.** Lo demás
es infraestructura; esto es lo que hace que el portal tenga gente adentro.
