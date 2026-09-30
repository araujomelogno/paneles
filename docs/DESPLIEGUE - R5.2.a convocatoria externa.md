# Despliegue — R5.2.a · La convocatoria activa se verifica por sistema

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Requerimiento:** R5.2.a (`specs/SPEC_fase5.md`) — la mitad de R5.2 que la `0014` no implementó
**Migración que trae:** `db/boveda/0016_convocatoria_externa.sql`
**Duración estimada:** 15 minutos · **sin ventana de caída** · una sola base, la bóveda

---

## Lo primero: por qué existe este despliegue

Sin esto, **COLOQUIO no puede convocar en producción**.

`contacto_para_convocatoria()` exige, además del consentimiento, que la
persona tenga una convocatoria activa. La `0014` verificó eso mirando solo las
tablas de `paneles` —una `participacion` en una `encuesta` abierta— y se lo
aplicó a todos los consumidores. COLOQUIO convoca a grupos, sus sesiones viven
en su propio store y la bóveda no las ve: así que pide el contacto de alguien
que efectivamente convocó, y la bóveda le contesta que esa persona no tiene
convocatoria activa.

Desde la `0016` cada sistema declara la suya: `paneles` la sigue teniendo en
sus tablas, y un consumidor externo la declara con una función. El razonamiento
completo está en [D50](decisiones.md#d50).

**Precondición:** la `boveda/0015` aplicada, y con ella toda la Fase 5. Si
todavía no lo está, primero va
[`DESPLIEGUE - R2.12 enlace de acceso.md`](DESPLIEGUE%20-%20R2.12%20enlace%20de%20acceso.md),
que lleva la base desde su estado actual hasta la `0015`.

### El camino, en una mirada

| # | Paso | Dónde |
|---|---|---|
| 1 | Abrir el túnel de la bóveda y exportar los DSN | Terminal |
| 2 | Confirmar que falta solo la `0016` | Terminal |
| 3 | Aplicar `boveda/0016` | Bóveda |
| 4 | Verificar el esquema | Terminal |
| 5 | Verificar los privilegios, uno por uno | Bóveda |
| 6 | Correr la batería como COLOQUIO | Terminal |
| 7 | Desplegar funciones y hosting | Firebase |
| 8 | Avisarle al equipo de COLOQUIO | — |

**Los pasos 1 a 6 y el rollback están ensayados** contra un Postgres levantado
al estado que deja el despliegue anterior; las salidas de abajo son las de ese
ensayo. Los pasos 7 y 8 no se pueden ensayar desde el repositorio.

---

## Paso 1 · Abrir el túnel y exportar los DSN

Esta migración toca **solo la bóveda**, pero el diagnóstico mira las dos
bases, así que conviene abrir los dos túneles igual.

```bash
cd <el repositorio>

cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &

export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"

psql "$DSN_BOVEDA" -tAc "select current_database()"   # → paneles_boveda
```

Un DSN vacío no da error: `psql` cae en sus valores por omisión y termina
hablándole a otra base. Por eso se comprueba antes de seguir.

---

## Paso 2 · Confirmar que falta solo la `0016`

```bash
python3 scripts/verificar_esquema.py
```

**Esperado:**

```
Faltan 1 migración(es). Para aplicarlas, con el Auth Proxy abierto:

  psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda \
       -v ON_ERROR_STOP=1 -f db/boveda/0016_convocatoria_externa.sql
```

Si falta alguna otra, **no sigas**: andá primero al manual que corresponda.
Las migraciones se aplican en orden y la `0016` reemplaza dos funciones que
crea la `0014`.

---

## Paso 3 · Aplicar `boveda/0016`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/boveda/0016_convocatoria_externa.sql
```

**Esperado**, en este orden y sin ningún `ERROR`:

```
CREATE TABLE
COMMENT
CREATE INDEX
ALTER TABLE
CREATE FUNCTION
COMMENT
CREATE FUNCTION
COMMENT
CREATE FUNCTION
COMMENT
CREATE FUNCTION
REVOKE
REVOKE
GRANT
REVOKE
DO
```

> **`--single-transaction` no es opcional.** Dos de esas cuatro funciones son
> `create or replace` sobre funciones vivas —`contacto_para_convocatoria` y
> `generar_borrados_pendientes`—. Sin la transacción, una falla a mitad
> dejaría la superficie externa a medio reemplazar.

---

## Paso 4 · Verificar el esquema

```bash
python3 scripts/verificar_esquema.py
```

**Esperado, textual:**

```
PII del lado semántico
  ✓ ninguna migración declara una columna de PII

Las dos bases están al día.
```

---

## Paso 5 · Verificar los privilegios, uno por uno

Esta migración **le abre una escritura** al consumidor externo: es la primera.
Así que el paso no es «¿se creó la función?» sino «¿quién puede llamarla?».

```bash
psql "$DSN_BOVEDA" -c "select
  has_function_privilege('coloquio_app','declarar_convocatoria(uuid,text,timestamptz)','EXECUTE') as declara,
  has_function_privilege('public','declarar_convocatoria(uuid,text,timestamptz)','EXECUTE')       as publico,
  has_function_privilege('coloquio_app','purgar_convocatorias_externas(int)','EXECUTE')           as purga,
  has_table_privilege('coloquio_app','convocatoria_externa','SELECT')                             as lee_tabla,
  has_table_privilege('plataforma_ro','convocatoria_externa','SELECT')                            as dpo_lee;"
```

**Esperado, y los cinco valores importan:**

```
 declara | publico | purga | lee_tabla | dpo_lee
---------+---------+-------+-----------+---------
 t       | f       | f     | f         | t
```

| Columna | Por qué tiene que valer eso |
|---|---|
| `declara` = `t` | Es lo que desbloquea a COLOQUIO |
| `publico` = `f` | Una función nace con `execute` para `public`. Si quedó en `t`, cualquier rol conectado puede declarar convocatorias |
| `purga` = `f` | Quien declara no borra declaraciones: ni las suyas —borrar el rastro de lo que pidió— ni las de otro |
| `lee_tabla` = `f` | Se declara por función. Con acceso a la tabla se saltearía el gate de consentimiento y el tope de 60 días |
| `dpo_lee` = `t` | Es donde se ve qué sistema declaró qué: la contracara de haber abierto esta puerta |

---

## Paso 6 · Correr la batería como COLOQUIO

```bash
# El token TIENE que ser el de la cuenta de servicio (ver §7.1 de
# «DESPLIEGUE - COLOQUIO Fase 0.md»).
TOKEN_COLOQUIO="$(gcloud sql generate-login-token \
  --impersonate-service-account=coloquio-app@gestion-paneles.iam.gserviceaccount.com)"
export DSN_BOVEDA_COLOQUIO="postgresql://coloquio-app%40gestion-paneles.iam:${TOKEN_COLOQUIO}@127.0.0.1:5432/paneles_boveda?sslmode=disable"

python3 scripts/verificar_coloquio.py
```

La batería pasa de 14 chequeos a **16**. Los dos nuevos son los que prueban
esta migración, y el primero es el caso exacto que bloqueaba a COLOQUIO:

```
  ✓ la convocatoria se verifica por sistema  sin declarar: rechazado · declarado: entrega el contacto
  ✓ la declaración tiene tope y gate  tope de 60 días y gate de consentimiento, los dos al declarar
```

> **Ojo con los dos que ya fallaban.** Contra Cloud SQL la batería venía dando
> 12 de 14 por dos motivos que no son de la bóveda —el test de `p_actor` y el
> rol sin credenciales—, anotados en §7.1.1 del manual de COLOQUIO. Esos dos
> siguen igual: lo que este paso tiene que mostrar es que **los dos nuevos
> pasan** y que no apareció ninguno rojo más.

---

## Paso 7 · Desplegar funciones y hosting

```bash
firebase deploy --only functions,hosting
```

La aplicación no usa la función nueva, pero `esquema.py` sí cambió: sin
redesplegar, Cumplimiento → Esquema sigue sin conocer la `0016` y no la va a
reportar. No hay secretos nuevos.

---

## Paso 8 · Avisarle al equipo de COLOQUIO

Lo que tienen que hacer de su lado, y que hasta ahora no podían:

```sql
select declarar_convocatoria(:id_persona, :sesion_id, :fecha_sesion + interval '2 days');
```

Al invitar —el paso `candidato → invitado`—, fuera de la transacción de
Firestore. Después de eso, `contacto_para_convocatoria()` les entrega el
contacto de esa persona.

Tres cosas que conviene que sepan antes de escribir el código:

| | |
|---|---|
| **El vencimiento tiene tope de 60 días** | Más que eso se rechaza. Reprogramar una sesión es volver a llamar con la misma referencia y otra fecha: actualiza, no duplica |
| **No se declara a quien no consintió** | La función reaplica el gate. Si la persona retiró el consentimiento, la declaración falla —y si lo retira después, la declaración se borra sola— |
| **La referencia es opaca para la bóveda** | Es su id de sesión. No se interpreta; sirve para distinguir convocatorias y para saber a cuál correspondió cada lectura |

---

## Si algo sale mal

| Lo que ves | Qué pasó | Qué hacer |
|---|---|---|
| `column ... does not exist` o `relation ... does not exist` al aplicar | Falta una migración anterior | Paso 2. La `0016` reemplaza funciones que crea la `0014` |
| `publico` = `t` en el paso 5 | El `revoke` no corrió | `revoke execute on function declarar_convocatoria(uuid, text, timestamptz) from public;` y volvé a verificar |
| COLOQUIO sigue recibiendo «no tiene una convocatoria activa» | No declaró, o declaró con otro vencimiento | El mensaje ahora nombra el sistema. Que verifiquen que la declaración existe y está vigente: `select * from convocatoria_externa where id_persona = …` |
| `permission denied for function declarar_convocatoria` | El `grant` no corrió, o se conectaron con otro rol | Paso 5, columna `declara` |
| La batería del paso 6 da un rojo nuevo | Una migración abrió un privilegio de más | No lo dejes pasar: la lista blanca vive en `scripts/verificar_coloquio.py` y es lo único que vigila esta superficie |

---

## Volver atrás

**Lo normal es cerrar la puerta, no revertir el esquema.** Deja a `paneles`
exactamente como está y devuelve a COLOQUIO al estado anterior —bloqueado—:

```sql
begin;
  revoke execute on function declarar_convocatoria(uuid, text, timestamptz)
      from coloquio_app;
  delete from convocatoria_externa;
commit;
```

Ensayado: después de eso `has_function_privilege` da `f` y no queda ninguna
declaración vigente.

> **No dropees la tabla.** `contacto_para_convocatoria()` la lee, y una
> función de PL/pgSQL no declara esa dependencia: el `drop table` funciona y
> **la función queda rota en tiempo de ejecución**, para todos los sistemas,
> `paneles` incluido. Comprobado:
>
> ```
> ERROR:  relation "convocatoria_externa" does not exist
> LINE 8:     select 1 from convocatoria_externa c
> ```
>
> Si de verdad hay que revertir el esquema, primero hay que restaurar los
> cuerpos de `contacto_para_convocatoria()` y `generar_borrados_pendientes()`
> tal como los define `db/boveda/0014_fase5_superficie_externa.sql`, y recién
> después borrar la tabla. Eso reintroduce el defecto que esta migración
> arregla, así que no es un camino que convenga tomar sin un motivo escrito.

---

## Checklist

- [ ] **1** · Túnel abierto y `current_database()` comprobado
- [ ] **2** · `verificar_esquema.py` dice que falta **solo** la `0016`
- [ ] **3** · `boveda/0016` aplicada con `--single-transaction`
- [ ] **4** · `verificar_esquema.py`: «Las dos bases están al día»
- [ ] **5** · Los cinco privilegios dan `t f f f t`
- [ ] **6** · `verificar_coloquio.py`: los dos chequeos nuevos en verde, ninguno rojo nuevo
- [ ] **7** · `firebase deploy --only functions,hosting`
- [ ] **8** · COLOQUIO avisado, con el tope de 60 días y el gate por escrito
