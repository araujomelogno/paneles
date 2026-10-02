# Despliegue — Fase 6 · El portal del panelista

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Cubre:** los nueve requisitos de la Fase 6 (`specs/SPEC_fase6.md`)
**Migración:** `db/boveda/0017_fase6_portal_panelista.sql` · **una sola base**
**Duración estimada:** 45 a 60 minutos, casi todo en la consola de Firebase
**Precondición:** Fases 1 a 4 desplegadas y la base al día hasta la `0016`

---

> ### ⚠ El paso 5 de este documento quedó superado por R6.1.a
>
> Este manual hace entrar al panelista con un **enlace de un solo uso por
> visita**. R6.1.a lo reemplazó por **usuario y contraseña**, y el enlace
> quedó solo para crearla y recuperarla.
>
> * **Si todavía no desplegaste la Fase 6**, seguí este documento igual
>   —la migración `0017` y los pasos 7 a 10 siguen valiendo— pero **saltéate
>   el paso 5** y, en su lugar, hacé el despliegue de
>   **[`DESPLIEGUE - R6.1.a acceso con contraseña.md`](DESPLIEGUE%20-%20R6.1.a%20acceso%20con%20contrase%C3%B1a.md)**
>   a continuación, que habilita el proveedor que corresponde.
> * **Si ya la desplegaste**, lo que te toca es solo ese otro documento.

---

## Lo primero que hay que entender de este despliegue

**Esta fase da vuelta la postura de seguridad del sistema.** Hasta ahora la
bóveda la tocaban una decena de empleados de Equipos y un consumidor
registrado. Desde que el portal esté arriba, autentica a **miles de externos**
contra el store que tiene toda la PII.

Eso cambia qué significa «salió bien». Una migración aplicada y un sitio que
carga no alcanzan: los pasos 7 y 8 verifican que la superficie nueva **no
revele quién integra el panel** y que un panelista **no obtenga nada de la
administración**. Si alguno de esos dos falla, el despliegue no está hecho,
por más que el portal muestre puntos.

### El camino, en una mirada

| # | Paso | Dónde |
|---|---|---|
| 1 | Túnel y DSN | Terminal |
| 2 | Confirmar que falta solo la `0017` | Terminal |
| 3 | Aplicar `boveda/0017` | Bóveda |
| 4 | Verificar el esquema y el guardia de los derivados | Bóveda |
| 5 | Habilitar el ingreso por enlace en Firebase Auth | Consola de Firebase |
| 6 | Declarar `PORTAL_URL` y desplegar | Terminal |
| 7 | **Probar que no se filtra quién es panelista** | Navegador |
| 8 | **Probar que un panelista no entra a la administración** | Navegador |
| 9 | Marcar qué atributos puede editar la gente | App de administración |
| 10 | Avisar a los panelistas | — |

**Los pasos 1 a 4 y el rollback están ensayados** contra un Postgres levantado
al estado que deja la `0016`; las salidas de abajo son las de ese ensayo. Del
5 al 10 son de Firebase, del navegador y de operación.

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

## Paso 2 · Confirmar que falta solo la `0017`

```bash
python3 scripts/verificar_esquema.py
```

**Esperado:**

```
Faltan 1 migración(es). Para aplicarlas, con el Auth Proxy abierto:

  psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda \
       -v ON_ERROR_STOP=1 -f db/boveda/0017_fase6_portal_panelista.sql
```

Si falta alguna otra, andá primero a su manual: las migraciones se aplican en
orden y la `0017` modifica objetos que crean las anteriores.

---

## Paso 3 · Aplicar `boveda/0017`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/boveda/0017_fase6_portal_panelista.sql
```

**Esperado**, en este orden y sin ningún `ERROR`:

```
ALTER TABLE
COMMENT
CREATE FUNCTION
CREATE TRIGGER
CREATE TABLE
COMMENT
CREATE TABLE
CREATE INDEX
CREATE INDEX
CREATE INDEX
COMMENT
ALTER TABLE
ALTER TABLE
ALTER TABLE
ALTER TABLE
COMMENT
CREATE VIEW
COMMENT
```

> **`--single-transaction` no es opcional.** La migración cambia la
> restricción de estados de `canje` entre dos `alter table`: a mitad de
> camino quedaría una tabla viva con una restricción que no corresponde.

---

## Paso 4 · Verificar

```bash
python3 scripts/verificar_esquema.py      # → «Las dos bases están al día.»

# Por defecto no hay nada editable: habilitarlo es una decisión (paso 9).
psql "$DSN_BOVEDA" -tAc \
  "select count(*) from atributo_demografico where editable_por_panelista"
# esperado: 0
```

Y el guardia que hace que la promesa se sostenga sola:

```bash
psql "$DSN_BOVEDA" -c \
  "update atributo_demografico set editable_por_panelista = true where clave = 'edad'"
```

**Esperado — este error es el resultado correcto:**

```
ERROR:  El atributo «edad» es derivado: su valor se calcula, así que no puede
        ser editable por el panelista.
```

Un derivado editable mostraría en el portal un control que no hace nada.

---

## Paso 5 · Habilitar el ingreso por enlace en Firebase Auth

En la consola de Firebase, proyecto `gestion-paneles`:

1. **Authentication → Sign-in method → Email/Password**: habilitarlo, y dentro
   marcar **«Email link (passwordless sign-in)»**. El portal no crea
   contraseñas de panelistas: la casilla de clave puede quedar deshabilitada,
   la del enlace no.
2. **Authentication → Settings → Authorized domains**: agregar el dominio
   desde el que se sirve el portal, si no está.
3. **Authentication → Templates → Email address sign-in**: revisar el texto.
   Es lo que va a leer el panelista, y hoy dice lo que Firebase trae de
   fábrica, en inglés.

> **Sobre el remitente.** Con el dominio de Firebase el envío es gratis y
> resuelve entregabilidad, que es por donde conviene empezar. Pasar a
> `@equipos.com.uy` mejora la confianza y **tiene un tope de 2.000 envíos
> diarios** por cuenta de Workspace; un servicio transaccional
> (SendGrid, Mailgun) cuesta ~US$ 15–20/mes y recién hace falta si el volumen
> supera ese tope. Ver `SPEC_fase6.md` §10.

---

## Paso 6 · Declarar `PORTAL_URL` y desplegar

El enlace de acceso tiene que volver a alguna dirección, y esa dirección no
puede vivir en el código: cambia entre ambientes, y apuntarla mal manda a los
panelistas a otro lado.

```bash
printf 'https://TU-DOMINIO' | gcloud secrets create PORTAL_URL --data-file=-
# o, si ya existe:
printf 'https://TU-DOMINIO' | gcloud secrets versions add PORTAL_URL --data-file=-

firebase deploy --only functions,hosting
```

> **Un secreto que está en Secret Manager pero no en la lista `SECRETOS` de
> `functions/main.py` no llega al runtime.** `PORTAL_URL` ya está declarado
> ahí; si alguna vez el enlace sale vacío, es lo primero que hay que mirar.

El `hosting` del mismo comando publica el portal: `firebase.json` ya tiene los
dos *rewrites* que hacen falta, `/portal` y `/entrar`.

---

## Paso 7 · Probar que no se filtra quién es panelista

**Este paso es el que decide si la fase está bien desplegada.** Es el vector
más probable de toda la fase: probar direcciones para averiguar quién integra
el panel.

Abrí `https://TU-DOMINIO/portal` y pedí el enlace **dos veces**: una con el
correo de un panelista real y otra con uno inventado.

| Lo que tiene que pasar | Lo que sería un problema |
|---|---|
| El mismo mensaje, palabra por palabra | Que uno diga «te mandamos el enlace» y el otro «no encontramos esa dirección» |
| El mismo tiempo de respuesta, a ojo | Que el del panelista real tarde notoriamente más |
| Ningún enlace al correo inventado | Que llegue un correo a una dirección que no es de nadie |

Y el límite de tasa, que es lo que hace que probar direcciones no salga
gratis:

```bash
# Dieciséis pedidos seguidos desde el mismo lugar: el último tiene que fallar.
for n in $(seq 1 16); do
  curl -s -X POST https://TU-DOMINIO/api/portal/acceso \
    -H 'Content-Type: application/json' \
    -d "{\\"email\\":\\"prueba$n@ejemplo.invalid\\"}" | tail -c 80; echo
done
```

El último tiene que contestar que se pidieron demasiados enlaces desde ese
dispositivo.

---

## Paso 8 · Probar que un panelista no entra a la administración

Las dos superficies comparten Firebase Auth y nada más. Con la sesión del
portal abierta, en la consola del navegador:

```js
const t = await firebase.auth().currentUser.getIdToken();
await fetch('/api/panelistas', { headers: { Authorization: `Bearer ${t}` } });
```

**Esperado: 403.** Un panelista no tiene rol, así que no puede ejecutar
ningún permiso interno. Si eso devolviera datos, hay que parar el despliegue.

Al revés también conviene mirarlo: una cuenta interna que entre a `/portal`
tiene que quedar sin vínculo —«esta cuenta no está vinculada a ningún
panelista»— salvo que esa persona además sea panelista, que es un caso
legítimo y distinto.

---

## Paso 9 · Marcar qué atributos puede editar la gente

Por defecto **nada** es editable. En **Configuración → Atributos
demográficos**, editar cada atributo que corresponda y marcar «El panelista
puede cambiarlo desde el portal».

Dos cosas que conviene pensar antes de marcar:

- **El valor pasa a ser autoritativo.** Una ingesta posterior que traiga otro
  valor lo informa como discrepancia y **no lo pisa**. La jerarquía es
  panelista > operador > archivo.
- **Una categoría especial editable** (salud, ideología, etc.) es una decisión
  aparte: la app lo marca con una advertencia y no lo impide. Son datos con
  exigencias propias.

Los atributos derivados no aparecen como marcables: su valor se calcula.

---

## Paso 10 · Avisar a los panelistas

El portal no sirve de nada si nadie sabe que existe. Lo que conviene incluir
en el aviso, y que el portal cumple:

- Que pueden **ver y canjear** sus puntos.
- Que pueden **corregir su celular** cuando cambian de número.
- Que pueden **dejar WhatsApp sin dejar el panel** — son cosas distintas.
- Que pueden **irse** y pedir que borren sus datos.

> **Antes de avisarle a todo el padrón, conviene tener el circuito de canje
> andando.** Mostrar los puntos crea expectativa: un premio que nunca llega es
> peor que no tener premios.

---

## Si algo sale mal

| Lo que ves | Qué pasó | Qué hacer |
|---|---|---|
| El enlace del correo lleva a una página en blanco | `PORTAL_URL` apunta a otro lado, o falta el *rewrite* | Paso 6. Comprobar con `gcloud secrets versions access latest --secret=PORTAL_URL` |
| «Falta `PORTAL_URL`» al pedir acceso | El secreto no llegó al runtime | Está en la lista `SECRETOS`; hay que **redesplegar** después de crearlo |
| El correo nunca llega | El método de ingreso por enlace no está habilitado, o el dominio no está autorizado | Paso 5, puntos 1 y 2 |
| «Esta cuenta no está vinculada a ningún panelista» | El correo con el que se autenticó no es el que la bóveda tiene para esa persona | Es el comportamiento correcto. Si la persona cambió de correo por fuera del portal, hay que actualizarlo en su ficha |
| «Esa persona ya tiene una cuenta del portal vinculada» | Se intentó vincular una segunda cuenta a la misma persona | Deliberado: una persona, una cuenta. Si perdió el acceso, hay que borrar su fila de `cuenta_panelista` y que vuelva a entrar |
| Un panelista no puede activar WhatsApp | Su celular no está verificado | Es la regla (R6.6). Tiene que confirmarlo desde «Mis datos» |
| El portal muestra `0` puntos y debería mostrar más | El saldo sale del ledger, así que es lo que dice el ledger | Revisar `puntos_movimiento` de esa persona desde la administración |

---

## Volver atrás

**Lo normal es apagar la puerta, no revertir el esquema.** Deshabilitar el
ingreso por enlace en Firebase Auth (paso 5.1) deja el portal inutilizable en
el acto, sin tocar la base y sin afectar nada de la administración.

Si hace falta revertir el esquema —ensayado, en este orden—:

```sql
begin;
  drop view if exists v_canje_panelista;
  drop table if exists acceso_portal;
  drop table if exists cuenta_panelista;
  alter table canje drop constraint if exists canje_estado_check;
  -- El `check` vuelve CON `aprobado` adentro: si algún canje ya pasó por ese
  -- estado, sacarlo de la lista haría fallar la restricción sobre datos que
  -- ya existen.
  alter table canje add constraint canje_estado_check
      check (estado in ('solicitado','aprobado','entregado','cancelado'));
  drop trigger if exists atributo_editable_coherente on atributo_demografico;
  drop function if exists atributo_editable_coherente();
  alter table atributo_demografico drop column if exists editable_por_panelista;
commit;
```

> **Lo que esto borra de verdad:** los vínculos cuenta ↔ persona y el registro
> de accesos. Las personas, sus puntos y sus canjes **no se tocan** —viven en
> tablas anteriores— y los cambios que los panelistas hayan hecho sobre sus
> propios datos tampoco: quedan en `persona_atributo` con
> `origen = 'panelista'`.

---

## Checklist

- [ ] **1** · Túnel abierto y `current_database()` comprobado
- [ ] **2** · `verificar_esquema.py` dice que falta **solo** la `0017`
- [ ] **3** · `boveda/0017` aplicada con `--single-transaction`
- [ ] **4** · Esquema al día; `editable_por_panelista` en 0; el derivado se rechaza
- [ ] **5** · Ingreso por enlace habilitado, dominio autorizado, plantilla revisada
- [ ] **6** · `PORTAL_URL` creado **y** redesplegado
- [ ] **7** · Dos correos —uno real, uno inventado— dan la **misma** respuesta
- [ ] **7** · El pedido número 16 desde el mismo origen se rechaza
- [ ] **8** · Una sesión del portal contra `/api/panelistas` da **403**
- [ ] **9** · Marcados los atributos que corresponda; ninguno especial sin decidirlo
- [ ] **10** · Circuito de canje andando antes de avisarle al padrón
