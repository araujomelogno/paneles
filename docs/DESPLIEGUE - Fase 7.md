# Despliegue — Fase 7: usabilidad de alta y de resultados

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0019`** y el store semántico
hasta la **`0006`**.
**Qué cambia:** una migración en la bóveda, código de backend y de frontend.
**Qué NO cambia:** ningún secreto, ningún permiso de IAM, ninguna cola,
ninguna configuración de función. El store semántico no se toca.

---

## Cómo leer este documento

Cada paso trae **el comando** y **la salida que se obtuvo al ensayarlo**. El
ensayo se hizo contra un Postgres levantado a propósito y llevado al estado
exacto de producción —todas las migraciones hasta la `0019`, ninguna más—,
no contra la base de pruebas del repo.

Donde dice «se obtuvo», es salida real copiada. Si lo que ves difiere, eso
es el hallazgo, y la tabla final dice qué hacer.

**Lo que este despliegue no necesita**, dicho por adelantado para que nadie
lo busque: no hay secretos nuevos, no hay roles nuevos, no hay que tocar la
cola de Cloud Tasks ni los permisos de la cuenta de servicio. Es la fase más
chica de desplegar de las últimas.

---

## Paso 0 · Preparar la terminal

En macOS con zsh, para que los comentarios no se ejecuten:

```bash
setopt INTERACTIVE_COMMENTS
```

```bash
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
```

---

## Paso 1 · Túnel y DSN de la bóveda

La migración toca **solo la bóveda**. El store semántico no se abre.

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`. Si el proxy ya estaba abierto, no
levantes otro: `lsof -nP -iTCP:5432 -sTCP:LISTEN` lo dice.

---

## Paso 2 · Confirmar que falta solo la `0020`

```bash
psql "$DSN_BOVEDA" -tAc "
  select case when to_regclass('motivo_reidentificacion') is null
              then 'falta 0020_motivos_reidentificacion.sql'
              else 'la 0020 ya está aplicada' end"
```

En el ensayo se obtuvo:

```
falta 0020_motivos_reidentificacion.sql
```

Si dice que ya está aplicada, saltear al paso 4.

Y que la base esté donde se la espera —la `0019` puesta—:

```bash
psql "$DSN_BOVEDA" -tAc "select to_regclass('ingesta_trabajo') is not null"
```

Tiene que decir `t`. Si dice `f`, falta la `0019` y hay que aplicarla antes
con `DESPLIEGUE - ingesta diferida.md`.

---

## Paso 3 · Aplicar `boveda/0020`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0020_motivos_reidentificacion.sql
```

Salida completa del ensayo, que son nueve líneas y ninguna sorpresa:

```
CREATE TABLE
COMMENT
INSERT 0 6
CREATE VIEW
COMMENT
REVOKE
REVOKE
ALTER TABLE
```

> **La migración no es idempotente, y eso está bien.** Correrla dos veces
> falla con `relation "motivo_reidentificacion" already exists` y **no
> cambia nada**: se verificó en el ensayo corriéndola de nuevo, con y sin
> `ON_ERROR_STOP`, y el catálogo quedó con sus seis filas, sin duplicados.
> No es como la `0006` del store semántico, que sí borraba datos antes de
> fallar: acá una segunda corrida es ruido en la pantalla y nada más.

### 3.1 · Que el catálogo quedó completo

```bash
psql "$DSN_BOVEDA" -c "select codigo, expone_pii, orden from motivo_reidentificacion order by orden"
```

```
        codigo        | expone_pii | orden
----------------------+------------+-------
 ficha                | t          |    10
 consulta             | t          |    20
 convocatoria         | t          |    30
 exportacion          | t          |    40
 cumplimiento         | t          |    50
 respuestas_panelista | f          |    60
(6 rows)
```

Seis, y **`respuestas_panelista` es el único con `expone_pii = f`**: es el
motivo nuevo de R7.6 —ver qué respondió una persona— y no revela contacto.
Si aparecen cinco, la migración corrió sin su `insert`.

### 3.2 · Que la vista no esconde lo que no conoce

Esto es lo menos obvio de la migración y conviene verlo funcionando. La
vista usa `left join` a propósito: una fila con un motivo que nadie
catalogó **tiene que seguir apareciendo**, marcada como desconocida. Si la
escondiera, catalogar mal volvería invisible una reidentificación.

Con dos filas de ensayo —una con motivo conocido y otra inventada— se
obtuvo:

```
        motivo         |                motivo_etiqueta                | expone_pii | motivo_desconocido
-----------------------+-----------------------------------------------+------------+--------------------
 respuestas_panelista  | Se vieron las respuestas de un panelista      | f          | f
 inventado_por_alguien | (motivo sin catalogar: inventado_por_alguien) | t          | t
```

El desconocido aparece, se marca, y se asume lo más conservador
(`expone_pii = t`).

En producción, sobre los datos que ya hay:

```bash
psql "$DSN_BOVEDA" -c "
  select motivo, motivo_desconocido, count(*)
    from v_reidentificacion group by 1,2 order by 3 desc"
```

Si alguna fila histórica sale con `motivo_desconocido = t`, no es un error
del despliegue: es un motivo que se usó alguna vez y que conviene agregar al
catálogo en una migración futura. **No hay que arreglarlo ahora** y no
bloquea nada.

---

## Paso 4 · Verificar esquema y privilegios

Desde la raíz del repositorio, sin cambiar de directorio —el comando que
sigue lo necesita así—:

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

En el ensayo, con la `0020` ya aplicada, se obtuvo:

```
boveda completo
semantica completo
```

No tiene que faltar ninguna migración. `verificar_esquema.py` detecta la
`0020` por sus dos objetos nuevos (`motivo_reidentificacion` y
`v_reidentificacion`): una migración que solo agregara filas sería
invisible, que es la lección de la `0015`.

Y la prueba que importa, porque la `0020` crea objetos y Postgres le da
permisos a `public` sobre todo lo que nace:

```bash
python3 scripts/verificar_coloquio.py
```

Tiene que terminar en `Pasaron los 16 chequeos.` En el ensayo se comprobó
directamente que `coloquio_app` no alcanza ninguno de los tres objetos:

```
motivo_reidentificacion|f
reidentificacion|f
v_reidentificacion|f
```

Si alguno diera `t`, faltan los `revoke` del final de la migración.

---

## Paso 5 · Desplegar

**Las dos partes.** El backend trae las rutas nuevas (estadísticas, ficha,
respuestas, columnas, resumen de revisión) y el frontend trae las pantallas
que las usan. Desplegar una sola deja la aplicación a medias: con solo
`hosting`, las pantallas nuevas llaman a rutas que devuelven 404.

```bash
export VPC_CONNECTOR="$(gcloud functions describe api \
  --gen2 --region="$REGION" --project="$PROYECTO" \
  --format='value(serviceConfig.vpcConnector)')"
printf 'Conector: %s\n' "$VPC_CONNECTOR"
```

**Si esa línea imprime vacío, parar.** `main.py` lee `VPC_CONNECTOR` del
entorno *del deploy* y `None` es un valor válido: la función se despliega
sin conector, sin un solo error, y muere en el primer uso a los 127
segundos exactos. Ya pasó.

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
```

Y la verificación de siempre, que no es opcional:

```bash
for f in api procesaringesta; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)'
done
```

Las dos líneas con el mismo conector y `PRIVATE_RANGES_ONLY`.

> **Después del deploy, recargá con caché limpia** (`Cmd+Shift+R`). Casi
> todo lo de esta fase es JavaScript, y el navegador se lo queda. Si no ves
> la solapa **Estadísticas**, es caché.

---

## Paso 6 · Probar las cinco pantallas

Son cinco cosas y cada una se ve en menos de un minuto.

### 6.1 · Estadísticas (R7.5)

Abrir la solapa **Estadísticas**. Tiene que cargar sin pedir nada.

Lo primero que hay que mirar es **«Individuos del store semántico sin
panelista»**: si no es cero, hay respuestas de gente que ya no existe en la
bóveda y la cascada de baja no las alcanzó. Aparece marcado como problema de
integridad, no como una estadística más.

Y el número que justifica la pantalla: **«Consultables»**, cuánta gente
tiene respuestas *y* uso semántico vigente. Es el que contesta «¿por qué mi
búsqueda devuelve menos de lo que esperaba?».

### 6.2 · La revisión antes de importar (R7.2)

Ingestar respuestas con cualquier archivo y confirmar. **No tiene que
importar nada todavía**: tiene que aparecer el resumen.

Lo que hay que comprobar ahí:

* Las variables agrupadas por destino, y las de bóveda **con el texto de la
  pregunta al lado del código**.
* La tabla de claves de dedup, con valores distintos y filas que colisionan.
* **Volver a corregir** devuelve al formulario con todo lo que estaba. Si
  algo se perdió, es un bug y conviene no seguir.

La prueba que vale la pena hacer a propósito: marcar como `documento` una
variable que no lo sea —una de sí/no— y confirmar. El resumen tiene que
gritarlo en rojo, con el número de personas que quedarían. Es exactamente el
error que llegó a producción el 3 de octubre.

### 6.3 · La versión del consentimiento (R7.1)

En el alta manual de un panelista y en el bloque de alta de la importación,
donde antes había un campo de texto ahora hay un desplegable con las
versiones publicadas y un botón **Ver texto**.

Si una finalidad no tiene ninguna versión activa, en vez del desplegable
aparece un aviso que dice dónde publicarla. Eso es correcto, no un fallo.

### 6.4 · La ficha desde un resultado (R7.3 y R7.6)

Correr una consulta y apretar **Ficha** en una fila.

* Tiene que abrirse **encima del resultado**, sin perderlo.
* **No tiene que mostrar nombre, documento, correo ni celular.** Si los
  muestra, hay un problema serio: la auditoría de reidentificación dejaría
  de reflejar quién vio los datos de quién.
* La tabla de respuestas pagina, filtra por estudio y busca por texto.
* La casilla **Ver el texto embebido** muestra lo que realmente se
  vectorizó. Vale mirarlo una vez: si dice `¿Qué marca fumás? → 11427` en
  vez de `→ Nevada`, esa respuesta está en la base y es inútil para buscar.

Y lo que hay que verificar en la base, porque es el precio de cruzar los dos
stores:

```bash
psql "$DSN_BOVEDA" -c "
  select motivo, motivo_etiqueta, actor_uid, creado_en
    from v_reidentificacion
   where motivo = 'respuestas_panelista'
   order by creado_en desc limit 5"
```

Tiene que haber una fila por cada ficha cuyas respuestas se miraron.

### 6.5 · Las columnas de la lista (R7.4)

En el ranking, botón **Columnas**: marcar un par de atributos y aplicar.

* Las columnas aparecen **sin volver a correr la consulta** (no hay spinner
  de consulta, no cambia el ranking).
* Un individuo sin ese atributo dice **«sin dato»**, no una celda vacía.
* Los atributos de categoría especial **no están en la lista**, a propósito.
* Recargar la página y correr otra consulta: las columnas siguen elegidas.

---

## Paso 7 · Lo que conviene mirar una vez, y no vuelve a hacer falta

```bash
psql "$DSN_BOVEDA" -c "
  select motivo, count(*) from reidentificacion group by 1 order by 2 desc"
```

Es la foto del registro de reidentificación antes de que R7.6 empiece a
escribir. Guardarla sirve para que dentro de un mes se pueda decir cuánto
creció por la ficha nueva y cuánto por el uso de siempre.

---

## Rollback

No hay datos nuevos que perder: la `0020` crea un catálogo y una vista, y
nada depende de ellos para funcionar. El orden es el de siempre, código
primero:

```bash
# 1 · El código anterior.
firebase deploy --only functions,hosting --project="$PROYECTO"   # desde el commit previo
```

```sql
-- 2 · Y recién entonces la base.
begin;
drop view  if exists v_reidentificacion;
drop table if exists motivo_reidentificacion;
commit;
```

Las filas de `reidentificacion` con motivo `respuestas_panelista` **quedan**,
y está bien que queden: son registros de accesos que ocurrieron. Un rollback
no deshace lo que alguien vio.

---

## Errores y correcciones

| Lo que se ve | Qué pasó | Qué hacer |
|---|---|---|
| `relation "motivo_reidentificacion" already exists` | La `0020` ya estaba aplicada | Nada: no cambió nada. Paso 2 |
| `No existe el rol \`coloquio_app\`` al reaplicar migraciones | Se está reconstruyendo una base desde cero sin ese rol | Es de la `0014`, no de esta fase. `create role coloquio_app login` antes de empezar |
| `verificar_coloquio.py` falla con relaciones de más | Faltaron los dos `revoke` del final de la `0020` | Correrlos a mano y repetir |
| Filas con `motivo_desconocido = t` | Un motivo histórico que no está en el catálogo | No es un error del despliegue; se agrega en una migración futura |
| La solapa **Estadísticas** no aparece | Caché del navegador, o no se desplegó `hosting` | `Cmd+Shift+R`; si sigue, paso 5 |
| Las pantallas nuevas dan 404 | Se desplegó `hosting` sin `functions` | Paso 5, las dos |
| La ficha muestra nombre o documento | **Problema serio**, no cosmético | No seguir: la seudonimización del resultado deja de ser cierta. Reportar |
| «Ver quiénes son» dejó de registrar | Se confundió el motivo nuevo con el de siempre | Los dos tienen que escribir: `ficha`/`consulta` para la reidentificación y `respuestas_panelista` para R7.6 |

---

## Checklist

- [ ] 1 · Proxy abierto y `DSN_BOVEDA` apuntando a `paneles_boveda`
- [ ] 2 · El diagnóstico dice que falta solo la `0020`, y la `0019` está
- [ ] 3 · `0020` aplicada: `CREATE TABLE … INSERT 0 6 … CREATE VIEW`
- [ ] 3.1 · Seis motivos, y `respuestas_panelista` con `expone_pii = f`
- [ ] 3.2 · La vista marca un motivo desconocido en vez de esconderlo
- [ ] 4 · `verificar_coloquio.py`: los 16 chequeos
- [ ] 5 · `VPC_CONNECTOR` exportado **antes** del deploy
- [ ] 5 · `functions` **y** `hosting` desplegados, conector verificado en las dos
- [ ] 6.1 · Estadísticas abre; individuos huérfanos en cero
- [ ] 6.2 · La importación muestra el resumen y no escribe hasta confirmar
- [ ] 6.2 · Volver a corregir conserva lo definido
- [ ] 6.3 · La versión del consentimiento es un desplegable, con «Ver texto»
- [ ] 6.4 · La ficha abre sin perder el resultado y **sin PII**
- [ ] 6.4 · Ver respuestas deja fila con motivo `respuestas_panelista`
- [ ] 6.5 · Las columnas se agregan sin re-consultar y se recuerdan
- [ ] 7 · Foto del registro de reidentificación, antes de que R7.6 escriba

---

## Referencias

* `specs/SPEC_fase7.md` — los seis requisitos y su Definition of Done.
* `docs/decisiones.md` — D57, por qué el catálogo de motivos no tiene clave
  foránea y por qué la revisión se pide por la misma ruta que ejecuta.
* `docs/DESPLIEGUE - ingesta diferida.md` — la `0019`, precondición de ésta.
