# Despliegue — COLOQUIO Fase 0 · Externalización de las bóvedas

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Cubre:** los ocho requisitos P0 de la Fase 5 (`specs/SPEC_fase5.md`)
**Precondición:** Fase 4 desplegada, con las migraciones `boveda/0001`…`0011` y `semantica/0001`…`0004` aplicadas

---

> **Revisión 2026-09-26.** Agregado el prerrequisito §3.0 (flag
> `cloudsql.iam_authentication=on`, sin el cual la verificación de §7.1 falla) y
> corregido el comando del token en §7.1 (`gcloud sql generate-login-token` en
> lugar de `gcloud auth print-access-token`).
>
> **Revisión 2026-09-25.** Corregidos: orden de la `0014` respecto del rol
> (§2 y §3), chequeo previo de textos de consentimiento movido antes de aplicar
> migraciones (§2), `--single-transaction` en todas las migraciones, creación
> del usuario IAM **sin** el sufijo `.gserviceaccount.com` (§3.1), subpasos
> numerados en §3, token de impersonación de la cuenta de servicio (§7.1),
> estado real de las IP de las instancias (§4) y checklist (§10).

## 0 · Qué cambia, en una frase

Hasta hoy la bóveda tenía **un** consumidor, y por eso era razonable que el
gate de consentimiento viviera en `consentimiento.exigir()`, en Python. Esta
fase mueve las invariantes de cumplimiento **a la base**, para que un segundo
sistema pueda conectarse sin que las tres reglas que importan pasen a ser
promesas repetidas en dos bases de código.

| Qué | Dónde | Riesgo si se saltea |
|---|---|---|
| Tres migraciones en la bóveda y una en la semántica | `boveda/0012`…`0014`, `semantica/0005` | La aplicación falla con `relation v_persona_convocable does not exist` apenas alguien entre a Panelistas |
| **El consentimiento deja de poder otorgarse sin texto publicado** | R5.7.d | El alta empieza a fallar si la versión que manda el formulario no está publicada y activa. La `0013` **se niega a aplicar** si ese es el caso: ver §2.2 |
| Un rol de base nuevo, `coloquio_app`, **creado antes** de la `0014` | §3 | La `0014` falla a propósito, con un mensaje que explica cómo crearlo |
| El origen de cada reidentificación deja de suponerse | R5.6 | Nada se rompe: las filas históricas quedan correctas como `paneles` |
| Un event trigger en el store semántico | R5.5 | Un `alter table` puede meter una columna de PII del lado semántico y nadie se entera |
| Una pantalla de cumplimiento nueva: bajas sin confirmar | R5.3 | La cascada funciona igual, pero nadie ve una baja que quedó abierta |

**Lo que esta fase deliberadamente no hace.** No toca Cloud Run, ni el SFU, ni
el plano de medios, ni crea el proyecto Firebase de COLOQUIO: eso empieza en la
Fase 1 de COLOQUIO. Tampoco activa a COLOQUIO: el registro lo deja **inactivo**
y se enciende el día que el sistema salga a producción (§6).

---

## 1 · La decisión que ordena todo: gate legal ≠ política de negocio

Conviene tenerla presente antes de leer las migraciones, porque explica por qué
dos cosas parecidas se resolvieron distinto.

- El **consentimiento vigente** es un invariante legal: no es negociable, no
  admite parámetros y no puede quedar del lado del consumidor. Va en una vista
  de la que es imposible salirse (`v_persona_convocable`).
- La **fatiga** es política de negocio: sus umbrales son por panel, su ventana
  es un parámetro, y el cálculo excluye la encuesta en curso —que es contexto
  del llamador—. Además el cualitativo tiene su propia noción de fatiga. Así
  que la fatiga se expone como **hechos** (`v_fatiga_panelista`) y cada
  consumidor aplica su umbral.

**Consecuencia que conviene tener escrita y no descubrir después: la política
de fatiga no queda hecha valer en la base.** Es deliberado.

---

## 2 · Las migraciones

Cada paso es aditivo y deja el sistema funcionando. En ningún momento hay una
ventana en la que `paneles` esté roto.

```bash
# Con el Auth Proxy abierto contra cada instancia
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &

export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
```

| # | Migración | Qué trae | Orden |
|---|---|---|---|
| 1 | `boveda/0012_fase5_catalogo_finalidades.sql` | `finalidad_consentimiento` con las dos finalidades actuales sembradas; FK desde `consentimiento` y `texto_consentimiento`; se retiran los dos `check`. **Sin cambios de comportamiento.** | Puede ir sola y sin riesgo |
| 2 | `boveda/0013_fase5_finalidades_cualitativo.sql` | Las cuatro finalidades del cualitativo; `consentimiento.ref_estudio`; validación de ámbito; exigencia de texto activo | Depende de la 1 |
| 3 | `boveda/0014_fase5_superficie_externa.sql` | La superficie externa completa y los `grant` | **Última.** Requiere el rol de §3 |
| 4 | `semantica/0005_fase5_prohibicion_pii.sql` | Catálogo de PII y event trigger | Independiente; puede ir en paralelo |

**Antes de aplicar nada**, comprobar que la `0013` va a poder pasar (ver 2.2):

```bash
psql "$DSN_BOVEDA" -c "
select distinct c.finalidad, c.version_texto
  from consentimiento c
 where c.estado = 'vigente'
   and not exists (select 1 from texto_consentimiento t
                    where t.finalidad = c.finalidad
                      and t.version = c.version_texto and t.activo);"
```

Sin filas, se puede seguir. **Con filas, publicar esos textos primero** (2.2):
si no, la `0013` aborta a mitad de camino.

```bash
for m in 0012_fase5_catalogo_finalidades 0013_fase5_finalidades_cualitativo; do
  psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction -f "db/boveda/${m}.sql"
done
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/semantica/0005_fase5_prohibicion_pii.sql
```

**La `0014` NO se aplica todavía**: necesita el rol `coloquio_app`, que se crea
en §3. Se aplica en **3.4**.

> **`--single-transaction` no es opcional.** Sin él, una migración que falla a
> mitad deja confirmado lo ya ejecutado y hay que reconstruir el estado a mano.
> Con él, revierte entera y se reintenta limpio.

### 2.1 · La `0014` va última, y el motivo no es estético

Es la que crea acceso externo. Hasta que exista, no hay nada que proteger. Si
fuera antes, habría una ventana en la que el rol existe y las reglas todavía
no.

### 2.2 · La `0013` se puede negar a aplicar, y eso es lo que hay que querer

Desde R5.7.d la base rechaza otorgar una finalidad sin una versión **activa**
en `texto_consentimiento`. Esa regla es la que hace demostrable al
consentimiento, y es también la que puede romper el alta en silencio si la
versión que manda el formulario nunca se publicó.

Por eso la migración empieza con una comprobación que **aborta** si algún
consentimiento vigente apunta a una versión que no está publicada y activa:

```
ERROR:  Hay consentimientos vigentes con versiones sin publicar: «consentimiento-2026-01».
```

No es un obstáculo de la migración: es el hallazgo. Antes de reintentar hay
que publicar esas versiones, con su texto real, desde **Panelistas →
Inscripciones → Textos de consentimiento** o por SQL:

```sql
insert into texto_consentimiento (finalidad, version, cuerpo, activo)
values ('contacto_participacion', 'consentimiento-2026-01', '…el texto real…', true);
```

> **Verificarlo antes de la ventana de despliegue**, no durante:
> ```sql
> select distinct c.finalidad, c.version_texto
>   from consentimiento c
>  where c.estado = 'vigente'
>    and not exists (select 1 from texto_consentimiento t
>                     where t.finalidad = c.finalidad
>                       and t.version = c.version_texto and t.activo);
> ```
> Sin filas, la `0013` aplica limpia.

### 2.3 · Verificar

```bash
python3 scripts/verificar_esquema.py
```

En este punto lo correcto es:

- `0012`, `0013` y toda la semántica en **✓**.
- **`0014` en ✗**, con su lista de objetos faltantes. **Es lo esperado**: se
  aplica en 3.4, después de crear el rol. No es un error.
- Ninguna migración del store semántico declara una columna de PII.

---

## 3 · El rol `coloquio_app`, y la trampa de `cloudsqlsuperuser`

Este es el paso que puede convertir todo el control de acceso de la fase en
decoración, así que va con su explicación y no solo con su comando.

En Cloud SQL, **todo usuario creado con `gcloud sql users create`, la consola o
la API recibe automáticamente el rol `cloudsqlsuperuser`**, con `CREATEROLE`.
Es así como se creó `app_paneles`. Si `coloquio_app` se creara del mismo modo
tendría `CREATEROLE`, es decir **podría otorgarse a sí mismo cualquier
privilegio que le revoquemos**, y la lista blanca de R5.4 no valdría nada.

**Los usuarios de autenticación IAM no reciben ningún rol de base
automáticamente.** Por eso:

### 3.0 · Habilitar autenticación IAM en la instancia — **prerrequisito**

Postgres no acepta autenticación IAM salvo que la instancia tenga el flag
habilitado. Crear el usuario con `--type=cloud_iam_service_account` **no** lo
activa: son dos cosas distintas. Sin esto, la conexión de §7.1 falla con
`Cloud SQL IAM service account authentication failed`.

**Verificar primero qué flags tiene:**

```bash
gcloud sql instances describe paneles-boveda \
  --format="value(settings.databaseFlags)"
```

Si **no** aparece `cloudsql.iam_authentication=on`, habilitarlo:

```bash
gcloud sql instances patch paneles-boveda \
  --database-flags=cloudsql.iam_authentication=on
```

> ⚠ **Dos avisos.**
>
> **Reinicia la instancia** (un par de minutos sin servicio). Hacerlo fuera de
> horario de uso.
>
> **`--database-flags` reemplaza la lista completa, no agrega.** Si el
> `describe` devolvió otros flags, hay que repetirlos todos en el mismo
> comando, separados por coma, o se pierden.

**Confirmar** (tiene que incluir el flag, y la instancia volver a `RUNNABLE`):

```bash
gcloud sql instances describe paneles-boveda \
  --format="value(settings.databaseFlags,state)"
```

### 3.1 · Crear la cuenta de servicio y el usuario IAM

```bash
# 1 · La cuenta de servicio de COLOQUIO
gcloud iam service-accounts create coloquio-app \
    --project=gestion-paneles \
    --display-name="COLOQUIO · acceso a la bóveda"

# 2 · Los permisos de plataforma para llegar a la instancia
for rol in roles/cloudsql.client roles/cloudsql.instanceUser; do
  gcloud projects add-iam-policy-binding gestion-paneles \
      --member="serviceAccount:coloquio-app@gestion-paneles.iam.gserviceaccount.com" \
      --role="$rol"
done

# 3 · El usuario de base, como usuario IAM. NO con `gcloud sql users create`
#     tradicional: ese camino otorga `cloudsqlsuperuser`.
# OJO: va SIN el sufijo `.gserviceaccount.com`. Con el sufijo completo,
#      Cloud SQL rechaza el pedido con HTTPError 400.
gcloud sql users create \
    coloquio-app@gestion-paneles.iam \
    --instance=paneles-boveda \
    --type=cloud_iam_service_account
```

### 3.2 · Verificar cómo quedó el nombre del rol

**Recién ahora**, con el usuario IAM ya creado, se consulta el nombre real:

```bash
psql "$DSN_BOVEDA" -c "select rolname from pg_roles where rolname like 'coloquio%';"
```

Cloud SQL **trunca el email en el `@`**, así que lo esperable es
`coloquio-app@gestion-paneles.iam`, **no** `coloquio_app`, que es el nombre que
espera la migración.

> Si se corre esta consulta antes del paso 3.1 devuelve cero filas: todavía no
> hay nada creado.

### 3.3 · Crear el rol de grupo con el nombre canónico

Si el nombre no es `coloquio_app` —el caso normal—, la forma correcta **no** es
editar la migración a mano en la base (`CLAUDE.md`: las migraciones son
versionadas). Es crear un rol de grupo con el nombre canónico y hacer al
usuario IAM miembro suyo, que además deja el nombre estable si la cuenta de
servicio cambia algún día:

```bash
psql "$DSN_BOVEDA" -c "
create role coloquio_app;
grant coloquio_app to \"coloquio-app@gestion-paneles.iam\";"
```

(`coloquio_app` va **sin `login`**: es un grupo, no un usuario que se conecte.)

### 3.4 · Aplicar la migración

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/boveda/0014_fase5_superficie_externa.sql
```

> **`--single-transaction` no es opcional.** Sin él, una migración que falla a
> mitad deja lo ya ejecutado confirmado, y hay que reconstruir el estado a
> mano. Con él, revierte entera y se puede reintentar limpio.

### 3.5 · Registrar con qué nombre se conecta COLOQUIO

En `sistema_consumidor.rol_bd` va **el nombre con el que se conecta**, que es el
del usuario IAM y no el del grupo, porque `sistema_de_la_conexion()` mira
`session_user`:

```bash
psql "$DSN_BOVEDA" -c "
update sistema_consumidor
   set rol_bd = 'coloquio-app@gestion-paneles.iam'
 where codigo = 'coloquio';"
```

Verificar que la fila quedó bien:

```bash
psql "$DSN_BOVEDA" -c "select codigo, rol_bd from sistema_consumidor;"
```

Si el rol no existe, la migración falla con un mensaje que dice exactamente
esto. Es deliberado: otorgarle privilegios a la nada sería peor que fallar.

### 3.6 · `app_paneles` no se toca

Migrarlo a IAM sería un cambio de riesgo innecesario en esta fase. Queda
anotado como candidato futuro, junto con la asimetría de propiedad: las tablas
las posee `app_paneles`, así que el mecanismo de «la vista es la única puerta»
protege la bóveda **del consumidor nuevo, no de `paneles`**. Es deliberado y
está en P2 con nombre y motivo, no como olvido.

---

## 4 · Conectividad: la decisión de infraestructura de la fase

Las dos instancias viven en la VPC de `gestion-paneles`.

> **Verificar antes de decidir.** Según cómo se crearon, pueden tener además
> IP pública (es el caso si se siguió «DESPLIEGUE - Fase 1»). Comprobarlo:
>
> ```bash
> gcloud sql instances describe paneles-boveda \
>   --format="value(ipAddresses[].ipAddress)"
> ```
>
> Si aparecen direcciones fuera del rango privado (`10.x`, `172.16-31.x`,
> `192.168.x`), la instancia tiene IP pública y el Auth Proxy llega por ahí.
> Eso **no** cambia la decisión de esta sección —la conexión de COLOQUIO va por
> IP privada con IAM— pero sí explica por qué el proxy funciona hoy sin estar
> dentro de la VPC. El plano de control de COLOQUIO va en un proyecto Firebase
propio. Un servicio en otro proyecto no alcanza una IP privada de otra VPC
porque sí.

| Opción | Qué implica | Recomendación |
|---|---|---|
| **A · Shared VPC** | COLOQUIO en su proyecto, adjuntado a la VPC de `gestion-paneles` como proyecto de servicio, más `roles/cloudsql.client` cruzado | **Sí.** Conserva la autonomía de deploy que motivó separar los sistemas |
| **B · VPC peering** | Peering entre las dos VPC | Más piezas, rangos que no se pueden solapar, y el peering no es transitivo |
| **C · Mismo proyecto** | El plano de control de COLOQUIO vive en `gestion-paneles` | Regresión aceptable y reversible si el tiempo aprieta |

La separación que importa, y la que esta fase garantiza, es la de
**privilegios**. La de proyectos es comodidad operativa.

En cualquiera de las tres, la conexión usa el conector de Cloud SQL con
autenticación IAM.

---

## 5 · El contrato: lo que COLOQUIO puede tocar

**Todo lo que no está acá no existe para COLOQUIO**, y la base lo hace cumplir.

**Lectura**

```
v_persona_convocable          (id_persona, finalidad, ref_estudio, sexo, localidad, tramo_etario, edad)
v_fatiga_panelista            (id_persona, panel_id, recientes, totales, ultima_convocatoria, respondidas)
v_finalidad                   (codigo, descripcion, requiere_texto, ambito, activa)
v_texto_consentimiento_activo (finalidad, version, cuerpo, creado_en)
```

**Ejecución**

```
contacto_para_convocatoria(id_persona, canal, motivo, actor) → text
mis_borrados_pendientes()                                    → tabla
confirmar_borrado(id_persona, alcance)                       → void
reportar_error_de_borrado(id_persona, error, alcance)        → void
sistema_de_la_conexion()                                     → text
```

**Nada más.** En particular, **cero** sobre `persona`, `consentimiento`,
`participacion`, `membresia` y `encuesta`.

### 5.1 · Tres cosas que sorprenden, y que conviene saber de antemano

**Una vista no le presta a la vista su permiso para ejecutar una función.** El
permiso de *ejecutar* una función se chequea contra quien invoca, aun cuando la
llamada venga de adentro de una vista. Por eso `v_persona_convocable` es una
fachada delgada sobre `f_persona_convocable()`, y ese `execute` está otorgado.
No es una puerta de atrás: el gate está adentro de la función.

**Una función nace con `execute` otorgado a `public`.** «No le otorgamos nada»
y «no puede ejecutarla» son dos cosas distintas. La `0014` revoca
explícitamente el `execute` de sus siete funciones antes de otorgar ninguno.

**Adentro de un `security definer`, `current_user` es el dueño de la función.**
Derivar el origen de `current_user` dejaría a cualquier llamador firmándose
como el dueño. Todo lo que deriva identidad usa `session_user`.

Las tres se encontraron **conectándose de verdad** con el rol del consumidor, no
leyendo la migración. Por eso la fase entrega un cliente de verificación (§7) y
no un checklist.

---

## 6 · COLOQUIO entra inactivo

La `0014` registra a COLOQUIO en `sistema_consumidor` con `activo = false`, y
esto es a propósito: `activo` es lo que decide si una baja le genera un
pendiente de borrado. Mientras COLOQUIO no esté en producción no hay nada del
otro lado que pueda confirmarlo, así que cada baja dejaría una fila abierta
para siempre y el tablero del DPO nacería lleno de ruido.

**El día que COLOQUIO sale a producción**, y no antes:

```sql
update sistema_consumidor
   set activo = true, alta_en = now()
 where codigo = 'coloquio';
```

`alta_en` se mueve junto con el `activo` por la misma razón: la fecha de alta
es la que justifica que una baja anterior no sea responsabilidad suya.

---

## 7 · Verificación

### 7.1 · La batería, conectada como el consumidor

Es el sustituto honesto de «lo revisamos»: si el script pasa, la bóveda está
lista para COLOQUIO; si no pasa, no lo está.

```bash
# Contra el cluster de pruebas
source scripts/pg_pruebas.sh
python3 scripts/verificar_coloquio.py
```

```bash
# Contra la instancia real, con el Auth Proxy abierto
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
# El token TIENE que ser el de la cuenta de servicio, no el tuyo:
# Usar `gcloud sql generate-login-token`, NO `gcloud auth print-access-token`:
# el segundo emite un token genérico, sin el scope de login de base.
# Y con `--impersonate-service-account`, porque el usuario de base es
# `coloquio-app@…`, no vos.
# Requiere roles/iam.serviceAccountTokenCreator sobre la cuenta (ver abajo).
TOKEN_COLOQUIO="$(gcloud sql generate-login-token \
  --impersonate-service-account=coloquio-app@gestion-paneles.iam.gserviceaccount.com)"
export DSN_BOVEDA_COLOQUIO="postgresql://coloquio-app%40gestion-paneles.iam:${TOKEN_COLOQUIO}@127.0.0.1:5432/paneles_boveda?sslmode=disable"
python3 scripts/verificar_coloquio.py
```

> **Para poder impersonar la cuenta** hace falta, una sola vez:
>
> ```bash
> gcloud iam service-accounts add-iam-policy-binding \
>     coloquio-app@gestion-paneles.iam.gserviceaccount.com \
>     --member="user:garaujo@equipos.com.uy" \
>     --role="roles/iam.serviceAccountTokenCreator"
> ```
>
> El token dura **una hora**: si la batería falla con error de autenticación
> después de un rato, hay que volver a generarlo.

Catorce chequeos. Los siete primeros no escriben nada y valen contra
producción; los otros siete arman un escenario descartable —dos personas
marcadas `VERIF-COLOQUIO-…`, un panel y una encuesta— y lo borran al terminar.

Para apuntarlo a producción sin que escriba absolutamente nada:

```bash
python3 scripts/verificar_coloquio.py --solo-lectura
```

### 7.1.1 · Los dos chequeos que fallan hoy, y por qué no bloquean

La batería corrida contra Cloud SQL da **12 de 14**. Los dos que fallan no son
fallas de la bóveda:

**«el contacto legítimo queda auditado».** El test pasa como `p_actor` el rol de
base (`coloquio-app@gestion-paneles.iam`) y espera ver otra cosa. La función
está bien: `contacto_para_convocatoria` registra
`coalesce(p_actor, session_user)`, o sea el actor que informa el llamador, y el
sistema solo si no viene ninguno.

> **Contrato definido:** `p_actor` es el **email del usuario humano de COLOQUIO**
> que pidió el contacto, no la cuenta de servicio. De ahí que el test, que pasa
> la cuenta técnica, verifique algo que no corresponde.
>
> **Obligación del cliente:** COLOQUIO **debe** pasar `p_actor` en cada llamada.
> Si lo omite, la auditoría registra «coloquio» y se pierde quién fue la
> persona — que es justamente el dato que una reidentificación necesita.
>
> **A decidir (P2):** hoy `p_actor` es nullable y el `coalesce` permite que el
> cliente se olvide sin que nadie se entere hasta mirar la auditoría. Si el
> dato tiene valor legal, la función podría **rechazar** la llamada sin actor.
> Queda anotado, no resuelto.
>
> **Pendiente:** corregir el test (que pase un email de usuario y verifique que
> ese email quede registrado). **La migración no se toca.**

**«un rol sin registrar no consigue nada».** Falla con `fe_sendauth: no password
supplied`: el chequeo intenta conectarse con un rol no registrado y sin
credenciales. Contra un cluster local funciona; contra Cloud SQL **toda**
conexión necesita credenciales, así que el chequeo no llega a ejecutarse. **No
es verificable en este entorno** — ver §7.2.

---

### 7.2 · Las dos cosas que no se pueden probar en el cluster local

Son de la plataforma, y se verifican **una sola vez**, en la puesta en marcha:

1. **Que el usuario IAM llegue efectivamente a la instancia** con la
   conectividad elegida. Lo prueba el propio §7.1 contra la instancia real: si
   conecta, está.
2. **Que el event trigger se pueda crear.** `cloudsqlsuperuser` puede crear
   event triggers en Cloud SQL, y `app_paneles` es miembro de ese rol, así que
   la `semantica/0005` debería aplicar sin más. Comprobarlo:

   ```sql
   select evtname, evtenabled from pg_event_trigger where evtname = 'pii_prohibida';
   -- y que muerda de verdad, en una transacción que se descarta:
   begin;
     alter table respuesta add column email text;   -- tiene que fallar
   rollback;
   ```

### 7.3 · La suite

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

---

## 8 · El cambio de comportamiento que hay que avisar

### 8.1 · El alta exige una versión de texto publicada

Es el único cambio con consecuencia operativa inmediata. La pantalla de
Panelistas ya no manda una versión fija: lee las versiones activas de
`texto_consentimiento` y ofrece solo las finalidades que tienen una. Si no hay
ninguna publicada, el alta avisa cuál falta en vez de fallar.

**Antes de desplegar, publicar el texto vigente de cada finalidad en uso.** Es
la §2.2.

### 8.2 · El gate es algo más estricto que antes

`v_persona_convocable` pide tres cosas, no una: consentimiento vigente, persona
`activa`, y sin lápida en `persona_borrada`. Las dos últimas no las miraba el
gate de Python. Son las que evitan convocar a alguien cuya alta quedó en
revisión o cuya baja se está ejecutando, y el muestreo ya las aplicaba por su
cuenta, así que el conjunto elegible no cambia en la práctica.

### 8.3 · La auditoría dice de qué sistema vino

`reidentificacion` y `usuario_auditoria` tienen una columna `sistema`. Las
filas históricas quedan como `paneles`, que es correcto: todas las que existen
hoy las escribió `paneles`.

---

## 9 · Riesgos operativos

| Riesgo | Señal | Qué hacer |
|---|---|---|
| El rol IAM quedó con otro nombre y los `grant` fueron a la nada | El verificador falla en «la lista blanca promete lectura que el rol no tiene» | §3, el recuadro del nombre |
| Un consumidor deja de confirmar bajas | `mas_vieja_dias` sube en Cumplimiento → Borrados | Contactar al `contacto_tecnico` de `sistema_consumidor`. Una baja abierta hace mucho no es una tarea atrasada, es un incumplimiento |
| `reidentificacion` crece sin retención | Volumen de la tabla | Miles de filas de texto por mes: despreciable hoy, pero conviene fijarle retención antes de que crezca |
| Alguien agrega una columna de PII del lado semántico | El `alter table` falla en el momento | Ninguna: es el guardia funcionando. Si el nombre es legítimo, la excepción se declara en `excepcion_pii`, en una migración, con su motivo |

---

## 10 · Checklist

- [ ] La consulta de §2.2 no devuelve filas (o se publicaron los textos que faltaban)
- [ ] `boveda/0012` y `boveda/0013` aplicadas
- [ ] `semantica/0005` aplicada, y `pg_event_trigger` tiene `pii_prohibida`
- [ ] Cuenta de servicio `coloquio-app` creada, con `cloudsql.client` e `instanceUser`
- [ ] Usuario IAM creado en `paneles-boveda`, **no** con `gcloud sql users create` tradicional
- [ ] Flag `cloudsql.iam_authentication=on` habilitado en `paneles-boveda` (§3.0)
- [ ] Nombre real del rol confirmado (§3.2) y rol de grupo `coloquio_app` creado (§3.3)
- [ ] `boveda/0014` aplicada **con `--single-transaction`** (§3.4)
- [ ] `sistema_consumidor.rol_bd` ajustado al nombre del usuario IAM (§3.5)
- [ ] `python3 scripts/verificar_esquema.py` en verde **después de §3.4**
      (antes de aplicar la `0014`, esa migración figura en ✗ y es lo esperado)
- [ ] `python3 scripts/verificar_coloquio.py` contra el cluster de pruebas: 14/14
- [ ] `python3 scripts/verificar_coloquio.py` contra la instancia real: 14/14
- [ ] `pytest` en verde
- [ ] Funciones redesplegadas (`firebase deploy --only functions,hosting`)
- [ ] Panelistas → alta con consentimiento: funciona
- [ ] Cumplimiento → Borrados: la pantalla carga
- [ ] `sistema_consumidor.activo` de `coloquio` **sigue en `false`** hasta que COLOQUIO salga a producción
