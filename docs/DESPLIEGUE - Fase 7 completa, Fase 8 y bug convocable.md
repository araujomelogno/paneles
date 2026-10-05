# Despliegue — Fase 7 completa, Fase 8 y bug de `v_persona_convocable`

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0020`** y el store semántico
hasta la **`0006`**. Si la `0020` no está, primero va
`DESPLIEGUE - Fase 7.md` entero.
**Qué cambia:** una migración en la bóveda (`0021`), una en el store
semántico (`0007`), código de backend y de frontend.
**Qué NO cambia:** ningún secreto, ningún rol, ningún permiso de IAM, ninguna
cola de Cloud Tasks, ninguna configuración de función.

**Lo único que hay que coordinar con alguien más: COLOQUIO.** La `0021`
cambia una columna de la vista que COLOQUIO lee. Leer la sección
**«Antes de empezar»**, que dice qué tiene que cambiar del otro lado y en qué
momento.

---

## Qué trae este despliegue

Son tres entregas que salen juntas porque comparten archivos:

| Entrega | Qué es | Dónde se ve |
|---|---|---|
| **Bug de la vista convocable** | `v_persona_convocable` devolvía una fila **por consentimiento** y no por persona: con las dos finalidades vigentes, cada persona aparecía dos veces y todos los conteos de COLOQUIO salían al doble | `boveda/0021`, `verificar_coloquio.py` (chequeo nuevo de cardinalidad) |
| **Lo que faltaba de la Fase 7** | La pantalla de R7.3 (la ficha desde un resultado, ahora **con la evidencia**: la respuesta que hizo aparecer a esa persona), R7.4 (columnas de la lista), R7.6 (las respuestas procesadas de un panelista) y R7.1 completo (versión del consentimiento con desplegable en **todos** los lugares) | Solo código |
| **Fase 8 — calidad del dato** | Al importar, el sistema detecta lo que degrada el texto que se embebe (baterías, consignas, códigos sin etiqueta, `Checked`, no respuesta, datos personales en abiertas, variables vacías) y **propone** la corrección, con vista previa. Y un estudio ya cargado se puede **corregir y reprocesar** sin volver a subir el archivo | `semantica/0007`, código |

---

## Cómo leer este documento

Cada paso trae **el comando** y **la salida que se obtuvo al ensayarlo**. El
ensayo se hizo contra dos bases levantadas a propósito y llevadas al estado
exacto de producción —bóveda hasta la `0020`, semántica hasta la `0006`,
ninguna más— y con **1008 personas con las dos finalidades vigentes**, que es
el caso de la primera carga real donde apareció el bug. Diez de ellas tienen
además un segundo consentimiento de contacto vigente (otra versión del
texto), para que la duplicación se vea también dentro de una misma
finalidad.

Donde dice «se obtuvo», es salida real copiada. Si lo que ves difiere, eso es
el hallazgo, y la tabla de errores del final dice qué hacer.

---

## Antes de empezar · COLOQUIO

La `0021` cambia el **contrato** de lectura de la bóveda:

| | Antes | Después |
|---|---|---|
| `v_persona_convocable` | `(id_persona, finalidad, ref_estudio, sexo, localidad, tramo_etario, edad)`, **una fila por consentimiento vigente** | `(id_persona, finalidades text[], sexo, localidad, tramo_etario, edad)`, **una fila por persona** |
| Finalidades de un estudio puntual | estaban en la misma vista, con `ref_estudio` | `v_persona_finalidad_vigente (id_persona, finalidad, ref_estudio)` — vista nueva, también legible por `coloquio_app` |

`finalidades` reúne solo las finalidades **generales** (las de
`ref_estudio is null`). Una persona aparece en `v_persona_convocable` si
tiene al menos una. Las que están acotadas a un estudio están en la vista
nueva, una fila por cada una.

**Qué tiene que cambiar COLOQUIO** —son reemplazos mecánicos—:

```sql
-- antes
select … from v_persona_convocable where finalidad = 'contacto_participacion';
-- después
select … from v_persona_convocable where 'contacto_participacion' = any(finalidades);

-- antes: finalidad acotada a un estudio
select … from v_persona_convocable
 where finalidad = 'x' and ref_estudio = :estudio;
-- después
select … from v_persona_finalidad_vigente
 where finalidad = 'x' and ref_estudio = :estudio;
```

Y cualquier `count(distinct id_persona)` que COLOQUIO haya puesto para
esquivar el duplicado sigue funcionando: ahora `count(*)` da lo mismo.

**Las funciones no cambian de firma.** `declarar_convocatoria()` y
`contacto_para_convocatoria()` siguen recibiendo y devolviendo lo mismo;
solo cambió cómo leen la vista por dentro.

**El momento.** Una consulta que nombre la columna `finalidad` **falla**
después de la `0021` (`column "finalidad" does not exist`), y la versión
nueva falla antes. No hay forma de escribir una consulta que sirva para las
dos, así que el cambio de COLOQUIO se despliega **inmediatamente después del
paso 3**. Mientras tanto, lo que falle del lado de COLOQUIO falla con error,
no en silencio: nadie queda convocado sin gate. Avisarles antes de empezar,
con este documento.

> El código de `paneles` tiene la misma ventana: el backend anterior también
> pregunta `finalidad = %s`. Por eso el deploy del paso 6 va **enseguida**
> de las migraciones, sin pausa en el medio. En la ventana, el muestreo y la
> convocatoria fallan con error —cerrados— y no convocan a nadie de más.

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

Desde la raíz del repositorio, en el commit que se va a desplegar:

```bash
git log -1 --oneline
ls db/boveda/0021_persona_convocable_una_fila.sql db/semantica/0007_fase8_calidad_del_dato.sql
```

Los dos archivos tienen que existir. Si no, estás en otro commit.

---

## Paso 1 · Túneles y DSN de los dos stores

Esta vez se tocan **los dos** stores, así que hacen falta los dos túneles.

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:$REGION:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA"    -tAc "select current_database()"
psql "$DSN_SEMANTICA" -tAc "select current_database()"
```

Tienen que devolver `paneles_boveda` y `paneles_semantica`. Si un proxy ya
estaba abierto, no levantes otro: `lsof -nP -iTCP:5432 -sTCP:LISTEN` (y
`5433`) lo dice.

---

## Paso 2 · Confirmar qué falta

```bash
psql "$DSN_BOVEDA" -tAc "
  select case when to_regclass('v_persona_finalidad_vigente') is null
              then 'falta 0021_persona_convocable_una_fila.sql'
              else 'la 0021 ya está aplicada' end"
psql "$DSN_BOVEDA" -tAc "select to_regclass('motivo_reidentificacion') is not null"

psql "$DSN_SEMANTICA" -tAc "
  select case when to_regclass('reproceso') is null
              then 'falta 0007_fase8_calidad_del_dato.sql'
              else 'la 0007 ya está aplicada' end"
psql "$DSN_SEMANTICA" -tAc "select to_regclass('v_dimension_embeddings') is not null"
```

En el ensayo se obtuvo:

```
falta 0021_persona_convocable_una_fila.sql
t
falta 0007_fase8_calidad_del_dato.sql
t
```

Las dos `t` son las precondiciones: la `0020` de la bóveda y la `0006` del
semántico están puestas. Si alguna dice `f`, **parar**: falta una migración
anterior y hay que aplicarla con su propio documento (`DESPLIEGUE - Fase 7.md`
para la `0020`, `DESPLIEGUE - 512 dimensiones.md` para la `0006`).

Y la foto del bug, **antes** de arreglarlo, que conviene guardar:

```bash
psql "$DSN_BOVEDA" -c "
  select count(*) as filas, count(distinct id_persona) as personas
    from v_persona_convocable"
```

En el ensayo:

```
 filas | personas
-------+----------
  2026 |     1008
```

Más filas que personas: ése es el bug. En producción el número va a ser
otro, pero la relación tiene que ser la misma (`filas` > `personas`) si hay
gente con más de una finalidad vigente.

---

## Paso 3 · Aplicar `boveda/0021`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0021_persona_convocable_una_fila.sql
```

Salida completa del ensayo:

```
BEGIN
DROP VIEW
DROP FUNCTION
CREATE FUNCTION
CREATE VIEW
COMMENT
COMMENT
CREATE FUNCTION
CREATE VIEW
COMMENT
CREATE FUNCTION
CREATE FUNCTION
REVOKE
REVOKE
GRANT
GRANT
GRANT
GRANT
COMMIT
```

Qué es cada bloque, para reconocerlo: tira la vista y la función viejas
(cambian de columnas y Postgres no deja reemplazarlas en el lugar), crea las
nuevas, crea `v_persona_finalidad_vigente` con su función, reemplaza
`declarar_convocatoria` y `contacto_para_convocatoria`, y vuelve a dar los
permisos que el `drop` se llevó.

> **Se puede correr dos veces, y conviene saberlo.** Va entera en una
> transacción: si algo falla en el medio, **no cambia nada**. Y si se corre
> de nuevo sobre una base que ya la tiene, la salida es idéntica a la de
> arriba y la base queda exactamente igual —se verificó en el ensayo,
> con `ON_ERROR_STOP`, y COLOQUIO conservó todos sus permisos—.
>
> No fue así en el primer borrador: tiraba la vista, fallaba antes de los
> `grant` y dejaba a COLOQUIO **sin acceso**. Por eso la transacción.

### 3.1 · Una fila por persona

```bash
psql "$DSN_BOVEDA" -c "
  select count(*) as filas, count(distinct id_persona) as personas
    from v_persona_convocable"
psql "$DSN_BOVEDA" -c "
  select finalidades, count(*) from v_persona_convocable group by 1 order by 2 desc"
```

En el ensayo:

```
 filas | personas
-------+----------
  1008 |     1008

              finalidades               | count
----------------------------------------+-------
 {contacto_participacion,uso_semantico} |  1008
```

**`filas` = `personas`**, y es lo que pide el Definition of Done del bug.
Las diez personas con dos versiones del consentimiento de contacto aparecen
una vez, con la finalidad una sola vez en el arreglo.

`personas` tiene que dar **lo mismo que antes** del paso 3. Si da menos, el
gate cambió de criterio, que es justo lo que el arreglo no tenía que hacer:
**parar** y revertir (sección Rollback).

### 3.2 · Los permisos de COLOQUIO quedaron

```bash
psql "$DSN_BOVEDA" -c "
  select table_name, privilege_type
    from information_schema.role_table_grants
   where grantee = 'coloquio_app'
     and table_name in ('v_persona_convocable', 'v_persona_finalidad_vigente')
   order by 1"
psql "$DSN_BOVEDA" -c "
  select p.proname,
         has_function_privilege('coloquio_app', p.oid, 'execute') as coloquio
    from pg_proc p
   where proname in ('f_persona_convocable', 'f_persona_finalidad_vigente',
                     'declarar_convocatoria', 'contacto_para_convocatoria')
   order by 1"
```

En el ensayo:

```
         table_name          | privilege_type
-----------------------------+----------------
 v_persona_convocable        | SELECT
 v_persona_finalidad_vigente | SELECT

           proname           | coloquio
-----------------------------+----------
 contacto_para_convocatoria  | t
 declarar_convocatoria       | t
 f_persona_convocable        | t
 f_persona_finalidad_vigente | t
```

Dos `SELECT` y cuatro `t`. Si falta alguno, COLOQUIO no puede leer: volver a
correr la `0021` entera (es segura de repetir) y mirar de nuevo.

### 3.3 · Avisar a COLOQUIO

Ahora. Ver «Antes de empezar».

---

## Paso 4 · Aplicar `semantica/0007`

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0007_fase8_calidad_del_dato.sql
```

Salida completa del ensayo:

```
BEGIN
ALTER TABLE
COMMENT
COMMENT
COMMENT
UPDATE 0
ALTER TABLE
COMMENT
CREATE TABLE
CREATE INDEX
COMMENT
ALTER TABLE
COMMIT
```

**El `UPDATE` va a decir otro número en producción**, y está bien: es la
cantidad de preguntas ya cargadas, a las que se les copia el texto y las
opciones actuales como «original». En el ensayo no había preguntas. Es lo que
permite que el primer reproceso de un estudio viejo también pueda volver
atrás.

Qué agrega: tres columnas en `pregunta` (`texto_original`,
`opciones_originales`, `normalizacion`), una en `cuestionario`
(`normalizacion`) y la tabla `reproceso`. **No toca `respuesta`** ni ningún
embedding: no hay nada que re-embeber por aplicarla.

El guardia de PII de la `0005` revisa estas columnas como cualquier otra;
ninguna está en `campo_pii`, así que no hay nada que declarar en
`excepcion_pii`. Si el `alter table` fuera rechazado por el event trigger,
la transacción no deja nada a medias.

> **También se puede correr dos veces.** La segunda corrida imprime
> `NOTICE: … already exists, skipping` por cada columna, la tabla y el
> índice, `UPDATE 0`, y termina en `COMMIT`. No cambia nada. Se verificó en
> el ensayo.

---

## Paso 5 · Verificar esquema y privilegios

Desde la raíz del repositorio:

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

En el ensayo, con las dos migraciones aplicadas:

```
boveda completo
semantica completo
```

La `0021` se reconoce por `f_persona_finalidad_vigente()` y
`v_persona_finalidad_vigente`; la `0007`, por sus cuatro columnas y la tabla
`reproceso`.

Y la batería de COLOQUIO, que **es la prueba que importa** después de tocar
una vista y cuatro funciones de la superficie externa:

```bash
python3 scripts/verificar_coloquio.py
```

(con `DSN_BOVEDA_COLOQUIO` exportado como en `DESPLIEGUE - COLOQUIO Fase 0.md`:
contra Cloud SQL el DSN del consumidor se pasa entero.)

En el ensayo:

```
  ✓ privilegios efectivos sobre relaciones  5 relaciones legibles, todas en la lista
  ✓ privilegios efectivos sobre funciones  8 funciones ejecutables, todas en la lista
  ✓ tablas sensibles negadas  11 tablas sensibles, todas negadas
  ✓ la conexión se identifica sola  coloquio_app → sistema «coloquio»
  ✓ lee la superficie del contrato  v_persona_convocable: 1008, v_persona_finalidad_vigente: 2016, v_fatiga_panelista: 0, v_finalidad: 6, v_texto_consentimiento_activo: 2
  ✓ una fila por persona en cada vista  v_fatiga_panelista: 0 · v_persona_convocable: 1008 · v_persona_finalidad_vigente: 2016 — ninguna clave repetida
  ✓ no lee las tablas  persona, consentimiento y participacion: permission denied
  ✓ no resuelve atributos por su cuenta  f_atributo_persona: permission denied
  ✓ solo ve a quien consintió  consintió: visible una vez · no consintió: invisible
  ✓ el contacto legítimo queda auditado  +598099000111 · auditado como coloquio/coloquio_app
  ✓ el contacto sin consentimiento se rechaza  rechazado por el gate, y sin fila de auditoría
  ✓ un canal por vez  solo `email` o `celular`
  ✓ la convocatoria se verifica por sistema  sin declarar: rechazado · declarado: entrega el contacto
  ✓ la declaración tiene tope y gate  tope de 60 días y gate de consentimiento, los dos al declarar
  ✓ la cascada de baja llega y se cierra  pendiente recibido, confirmado, y el de `paneles` sigue abierto
  ✓ no confirma el borrado de otro  un consumidor no cierra el pendiente de otro
  ✓ un rol sin registrar no consigue nada  rechazado: permission denied for function contacto_para_convocatoria

Pasaron los 17 chequeos. La bóveda se defiende sola.
```

**Son 17, uno más que antes.** El nuevo es «una fila por persona en cada
vista»: la batería no miraba la cardinalidad y por eso no vio el bug. Ahora
compara `count(*)` con la cantidad de claves distintas en cada vista que
expone `id_persona`, y falla si aparece una vista nueva con `id_persona`
sin clave declarada en el script.

`v_persona_finalidad_vigente: 2016` es correcto: es una fila por
**finalidad** de cada persona (1008 × 2), y su clave es
`(id_persona, finalidad, ref_estudio)`. Las diez personas con dos versiones
del texto de contacto no la duplican.

Los números de producción van a ser otros; lo que tiene que coincidir es
**17 de 17**.

---

## Paso 6 · Desplegar

**Las dos partes, y enseguida del paso 3.** El backend trae el gate nuevo
(que lee `finalidades`), las rutas de la Fase 8 y la evidencia de la ficha;
el frontend trae las pantallas. Con solo `hosting`, las pantallas nuevas
llaman a rutas que devuelven 404; con solo `functions`, nadie las ve.

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
`procesaringesta` importa: **el reproceso corre por ahí**, igual que la
ingesta diferida, y sin conector no llega a ninguna de las dos bases.

> **Las ingestas que estaban en curso siguen.** Una tarea encolada antes del
> deploy trae un plan sin `normalizacion` ni `operacion`; el código nuevo lo
> lee como «ingesta sin decisiones de normalización», que es exactamente lo
> que era. No hace falta esperar a que la cola se vacíe.

> **Después del deploy, recargá con caché limpia** (`Cmd+Shift+R`). Casi
> todo lo de esta entrega es JavaScript, y el navegador se lo queda.

---

## Paso 7 · Probar las pantallas

### 7.1 · La ficha desde un resultado, con evidencia (R7.3)

Correr una consulta en **Consultas** y apretar **Ficha** en una fila.

* Se abre **encima del resultado**; cerrarla lo deja como estaba.
* El bloque **«Por qué aparece en este resultado»**: la respuesta de esa
  persona que la hizo aparecer **en esta consulta**, con su pregunta, su
  estudio y la fecha de campo. Correr otra
  consulta y abrir la ficha de la misma persona: la evidencia cambia, porque
  depende de lo que se está buscando.
* **No muestra nombre, documento, correo ni celular.** Si los muestra, es un
  problema serio, no cosmético: no seguir y reportarlo.
* El botón **Ver quién es** lleva a la reidentificación de siempre, con su
  motivo y su registro.

### 7.2 · Las respuestas procesadas de un panelista (R7.6)

En la misma ficha, y también en la ficha completa de **Panelistas**:

* De entrada se ve **solo el conteo** por estudio. La tabla aparece al
  apretar **Ver las respuestas**, y el aviso dice que queda registrado.
* La tabla pagina, filtra por estudio y busca por texto. La casilla **Ver el
  texto embebido** muestra lo que realmente se vectorizó.
* Un panelista sin respuestas dice que **no tiene respuestas procesadas**, no
  una tabla vacía.

Y en la base, que cada apertura de la tabla dejó su fila:

```bash
psql "$DSN_BOVEDA" -c "
  select motivo, actor_uid, creado_en from v_reidentificacion
   where motivo = 'respuestas_panelista' order by creado_en desc limit 5"
```

Abrir una ficha **sin** apretar «Ver las respuestas» **no** tiene que dejar
fila: el registro significa «miró el contenido», no «abrió una ficha».

### 7.3 · Las columnas de la lista (R7.4)

En el ranking, botón **Columnas**: marcar un par de atributos y aplicar.
Aparecen sin volver a correr la consulta; un individuo sin el dato dice
**«sin dato»**; los atributos de categoría especial no están en la lista; y
después de recargar la página, las columnas siguen elegidas.

### 7.4 · La versión del consentimiento (R7.1)

En el alta manual, en el bloque de alta de la importación y en **Otorgar
finalidad** desde la ficha de un panelista: un **desplegable** de
versiones publicadas, con **Ver texto** que lo muestra sin cerrar el
formulario. Con una sola versión activa, viene elegida.

### 7.5 · La calidad del dato al importar (Fase 8, R8.1–R8.8)

Importar respuestas con un `.sav` real —el de la primera carga sirve, porque
tiene de todo— y llegar al paso de variables.

* Aparece el bloque **«Calidad del dato · lo que se detectó en el archivo»**,
  con un contador `rompe / mejora / información` y las tarjetas ordenadas en
  ese orden. Lo informativo va plegado.
* **Nada está aplicado de entrada.** Cada tarjeta tiene sus acciones
  («usar en esta», o la de aplicar a todas); al apretarla queda marcada con ✓
  y la variable cambia en la tabla.
* Debajo de cada variable, **«Se embebe así»**: el texto embebido real con
  valores del archivo, cuántas respuestas genera y cuántas descarta. Corregir
  el texto de una pregunta actualiza su vista previa.
* Las decisiones aplicadas aparecen como etiquetas con **×**: quitarla la
  deshace. Si el texto se editó, debajo dice el original y **volver al
  original**.

Lo que conviene probar a propósito, porque es lo que la fase existe para
evitar:

| En el archivo | Lo que tiene que proponer |
|---|---|
| Una batería `Checked`/`Unchecked` | Agruparla, **solo lo marcado**, `Checked` → `Sí` |
| Un label «Opción:Pregunta…» | Un texto autocontenido, sin la opción pegada adelante |
| Una cerrada con un código sin etiqueta (`11427` en vez de la marca) | Tarjeta **Rompe**: etiqueta sin traducir, antes de ingestar |
| Valores 99 / «NS/NC» | Detectarlos como no respuesta y ofrecer excluirlos |
| Un «Otro: especificar» con un teléfono o un correo | Tarjeta **Rompe** de datos personales, con conteo y ejemplos **enmascarados**. **No bloquea**: se puede ingestar a conciencia |
| Una variable sin ninguna respuesta | Proponer excluirla |
| Una variable casi constante (fuera de una batería) | Solo informarlo, en «Información»: aporta poco a la búsqueda, pero decidir es del analista |

Y en la revisión antes de confirmar: la columna **Genera** por variable, los
descartes en el volumen («no marcadas», «no respuesta») y, si quedó algo que
rompe sin decidir, el aviso en la lista de advertencias.

### 7.6 · Corregir y reprocesar un estudio ya cargado (R8.9)

Desde la ficha de una encuesta, **Corregir y reprocesar**; o desde
**Estadísticas**, **Reprocesar** en la fila de una carga.

* Se abre con las preguntas tal como quedaron cargadas y el bloque
  **«Calidad del dato de lo cargado»**: el mismo diagnóstico, hecho sobre lo
  que está en la base.
* Cambiar el texto de **una** pregunta y apretar **Revisar los cambios**. La
  revisión dice cuántas respuestas se van a **re-embeber** (solo las de esa
  pregunta), cuántas quedan **sin cambios** y cuántas se **borran**. **No
  escribe nada todavía.**
* **Confirmar y reprocesar**: se encola por la vía diferida y la pantalla
  sigue el avance, como una importación.

Y en la base, el registro:

```bash
psql "$DSN_SEMANTICA" -c "
  select id, cuestionario_id, creado_en, respuestas_a_procesar, trabajo_id,
         jsonb_array_length(cambios) as cambios
    from reproceso order by creado_en desc limit 5"
```

Y el detalle de uno —qué campo de qué pregunta, antes y después—:

```bash
psql "$DSN_SEMANTICA" -c "
  select c->>'codigo' as pregunta, c->>'campo' as campo,
         c->'antes' as antes, c->'despues' as despues
    from reproceso r, jsonb_array_elements(r.cambios) c
   where r.id = (select max(id) from reproceso)"
```

`trabajo_id` es el `ingesta_trabajo` de la bóveda que lo procesó (nulo si no
había respuestas que tocar). Quién lo pidió está del lado de la identidad:

```bash
psql "$DSN_BOVEDA" -c "
  select id, creado_por, estado, creado_en from ingesta_trabajo
   where plan->>'operacion' = 'reproceso' order by creado_en desc limit 5"
```

> **El reproceso re-aplica el gate de consentimiento**, igual que la
> ingesta: una respuesta de alguien que retiró `uso_semantico` desde que se
> cargó el estudio **no** se re-embebe, se borra. El resumen del trabajo lo
> cuenta aparte (`sin_consentimiento_reproceso`).

> **Cuesta lo que se re-embebe.** «Reprocesar todo de nuevo» re-embebe el
> estudio entero, y con Voyage eso tiene costo. Lo normal es corregir
> preguntas puntuales: el `hash_texto` acota el trabajo a lo que cambió.

---

## Paso 8 · Lo que conviene hacer una vez

**Revisar los estudios ya cargados.** Todo lo que se ingestó antes de la
Fase 8 entró con el texto del archivo tal cual: baterías con sus `Unchecked`,
códigos sin etiqueta, consignas. No hay que hacer nada para que la búsqueda
siga funcionando como hasta hoy, pero **cada estudio viejo mejora con un
reproceso**, y la pantalla de 7.6 dice qué tiene para corregir. Conviene
empezar por los que más se consultan.

**Guardar la foto del gate.** El número de personas convocables del paso 3.1
es el que COLOQUIO va a ver a partir de ahora. Si su equipo tenía cuotas
calculadas sobre el número viejo, estaban al doble.

---

## Rollback

El orden es el de siempre: **código primero, base después**. Y acá importa
más que otras veces, porque el código anterior y la vista nueva no se
entienden (paso 6).

```bash
# 1 · El código anterior, desde el commit previo.
firebase deploy --only functions,hosting --project="$PROYECTO"
```

```bash
# 2 · La bóveda: deshacer la 0021.
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0021.sql
```

`db/revertir/boveda_0021.sql` **no es una migración** —está fuera de
`db/boveda/` para que nada la aplique sola—. Vuelve a crear la vista y la
función de la `0014` y las dos funciones del contrato con el texto de la
`0016`, y repone los permisos. En el ensayo, su salida fue:

```
BEGIN
DROP VIEW
DROP FUNCTION
DROP VIEW
DROP FUNCTION
CREATE FUNCTION
CREATE VIEW
COMMENT
CREATE FUNCTION
CREATE FUNCTION
REVOKE
GRANT
GRANT
COMMIT
```

y el esquema que dejó, comparado con `pg_dump --schema-only` contra una base
que nunca tuvo la `0021`, fue **idéntico, permisos incluidos**. Con él
vuelve el bug (2026 filas para 1008 personas): es lo que significa volver
atrás. COLOQUIO también tiene que volver a su consulta anterior.

**El store semántico no se revierte.** Las columnas y la tabla de la `0007`
no molestan al código anterior —tienen valores por omisión y nadie más las
lee— y tirarlas perdería el texto original de las preguntas que se hayan
corregido. Si de todos modos hiciera falta:

```sql
begin;
drop table if exists reproceso;
alter table cuestionario drop column if exists normalizacion;
alter table pregunta
  drop column if exists normalizacion,
  drop column if exists opciones_originales,
  drop column if exists texto_original;
commit;
```

Lo que **no** se deshace: los embeddings que un reproceso ya reescribió
quedan con el texto corregido, y las filas de `reidentificacion` con motivo
`respuestas_panelista` quedan, porque registran accesos que ocurrieron.

---

## Errores y correcciones

| Lo que se ve | Qué pasó | Qué hacer |
|---|---|---|
| `column "finalidad" does not exist` en COLOQUIO | La `0021` está aplicada y COLOQUIO todavía consulta la columna vieja | Es la ventana prevista: desplegar el cambio de COLOQUIO («Antes de empezar») |
| `column "finalidad" does not exist` en el log de `api` | Se aplicó la `0021` y todavía corre el backend anterior | Paso 6, ya |
| `column "finalidades" does not exist` | Se desplegó el código nuevo **antes** de la `0021` | Paso 3 |
| `column "texto_original" … does not exist` al importar | Se desplegó el código nuevo sin la `0007` | Paso 4 |
| Paso 3.1: `personas` dio menos que antes | El gate cambió de criterio | No debería pasar —se probó—; revertir (Rollback) y reportar con las dos fotos |
| Paso 3.2: falta un `SELECT` o una función da `f` | Una corrida a medias de un borrador viejo de la `0021` | Volver a correr la `0021`: es una transacción y se puede repetir |
| `verificar_coloquio.py` dice 16 chequeos | Se está corriendo una copia vieja del script | Correr el del commit desplegado: son 17 |
| «una fila por persona en cada vista» falla con «sin clave declarada» | Una vista nueva de la superficie expone `id_persona` y el script no sabe su clave | Declararla en `CLAVE_POR_RELACION` de `verificar_coloquio.py` |
| `NOTICE: … already exists, skipping` | Se corrió una migración por segunda vez | Nada: es la salida esperada de una segunda corrida |
| No aparece el bloque de calidad del dato | Caché del navegador, o no se desplegó `hosting` | `Cmd+Shift+R`; si sigue, paso 6 |
| El reproceso queda en «encolado» y no avanza | `procesaringesta` sin conector o la cola pausada | Paso 6 (conector); `DESPLIEGUE - ingesta diferida.md` (cola) |
| «Hay una carga o un reproceso de este estudio en curso» | Hay una ingesta o un reproceso abierto sobre el mismo estudio | Esperar a que termine: dos procesos sobre las mismas respuestas se pisarían |
| La ficha muestra nombre, documento, correo o celular | **Problema serio** | No seguir. Reportar |

---

## Checklist

- [ ] Avisado COLOQUIO, con este documento, **antes** de empezar
- [ ] 0 · En el commit correcto: existen la `0021` y la `0007`
- [ ] 1 · Los dos proxies abiertos; `DSN_BOVEDA` y `DSN_SEMANTICA` apuntando bien
- [ ] 2 · Faltan la `0021` y la `0007`, y están la `0020` y la `0006`
- [ ] 2 · Foto de `filas` / `personas` antes del arreglo
- [ ] 3 · `0021` aplicada: de `BEGIN` a `COMMIT`
- [ ] 3.1 · `filas` = `personas`, y `personas` igual que antes
- [ ] 3.2 · Dos `SELECT` y cuatro `t` para `coloquio_app`
- [ ] 3.3 · COLOQUIO avisado de que la `0021` está puesta
- [ ] 4 · `0007` aplicada: de `BEGIN` a `COMMIT`
- [ ] 5 · Los dos stores `completo`
- [ ] 5 · `verificar_coloquio.py`: **17** de 17
- [ ] 6 · `VPC_CONNECTOR` exportado **antes** del deploy
- [ ] 6 · `functions` **y** `hosting`, conector verificado en `api` y `procesaringesta`
- [ ] 7.1 · Ficha con evidencia, sin PII, sin perder el resultado
- [ ] 7.2 · Respuestas a pedido; la tabla deja fila `respuestas_panelista`, abrir la ficha no
- [ ] 7.3 · Columnas sin re-consultar, «sin dato», se recuerdan
- [ ] 7.4 · Versión del consentimiento por desplegable en los tres lugares
- [ ] 7.5 · Panel de calidad: nada aplicado de entrada; vista previa real
- [ ] 7.5 · Datos personales en una abierta: advierte y no bloquea
- [ ] 7.6 · Reproceso: revisar no escribe; confirmar encola y re-embebe solo lo cambiado
- [ ] 8 · Lista de estudios viejos para reprocesar, empezando por los más consultados

---

## Referencias

* `specs/BUG_v_persona_convocable_duplica.md` — el bug y su Definition of Done.
* `specs/SPEC_fase7.md` y `specs/INFORME_fase7_que_falta.md` — lo que faltaba de la Fase 7.
* `specs/SPEC_fase8.md` — los nueve requisitos de la Fase 8.
* `docs/decisiones.md` — D58 (una fila por persona: `exists` y no
  `distinct`), D59 (la evidencia en la ficha, y por qué una ruta sin
  pantalla no existe), D60 (el sistema propone, el analista confirma) y D61
  (corregir desde lo cargado, no desde el archivo).
* `docs/manual/manual.html` — §5.8 (calidad del dato), §5.9 (reproceso),
  §6.4 (columnas, y la ficha desde un resultado con su evidencia), §3.5
  (la ficha del panelista y sus respuestas procesadas).
* `docs/DESPLIEGUE - COLOQUIO Fase 0.md` — el contrato, ya actualizado con
  las columnas nuevas.
* `docs/DESPLIEGUE - ingesta diferida.md` — la cola por la que corre el
  reproceso.
