# Retomar el despliegue de la ingesta diferida

`DESPLIEGUE - ingesta diferida.md` es el procedimiento **desde cero**. Este
documento es otra cosa: la lista de pasos **desde el estado real** en que
quedó el sistema después de los dos intentos del 3 de octubre, uno que
desplegó una función que no llegaba a la base y otro que falló al crear la
cola.

**Cómo leerlo.** Cada afirmación sobre el estado actual viene con el comando
que la comprueba. No hace falta creerme: correr el comando. Donde el
documento dice «esperado», es lo que el código y la configuración del repo
implican; si la salida real difiere, eso **es** el hallazgo, y la tabla final
dice qué hacer.

---

## Dónde estamos

| Qué | Estado | Con qué se comprueba |
|---|---|---|
| Migración `0019` en la bóveda | **Aplicada.** Una carga llegó a crear su trabajo y a encolar tareas, y eso no pasa sin las tablas | paso 1 |
| Cola `procesaringesta` | **Creada a mano** en el paso 5.4 del runbook general | paso 8 |
| Secretos `TAREAS_URL` y `TAREAS_CUENTA` | Cargados, pero **`TAREAS_URL` apunta al nombre viejo** (`/procesar_ingesta`) | paso 4 |
| Función `api` | Desplegada y funcionando: escribió el trabajo y encoló | paso 7 |
| Función de tareas | **Rota de dos maneras.** La vieja (`procesar_ingesta`) está desplegada y no llega a la base: muere a los 127 s. La nueva (`procesaringesta`) todavía no existe, porque el último deploy abortó al crear la cola | pasos 5 a 7 |
| Permiso de invocación | Otorgado sobre `procesar_ingesta`, el nombre viejo. **No sirve para el nuevo** | paso 9 |
| La carga que se probó | Quedó colgada, con sus lotes sin procesar | paso 10 |

**Las dos causas, que son independientes.** Conviene tenerlas separadas
porque arreglar una no arregla la otra:

1. **El Queue ID no admite guiones bajos.** El SDK de Python deriva el id del
   endpoint del nombre de la función y el CLI crea la cola con ese id, así
   que `procesar_ingesta` desplegaba la función y después rompía el deploy
   entero. Resuelto en el código: la función se llama `procesaringesta`.
2. **La función de tareas no llegaba a la base.** Murió a los 127 segundos
   exactos —1+2+4+8+16+32+64, el timeout de `connect()` del kernel con
   `tcp_syn_retries=6`—, que es la firma de un SYN que no llega a destino.
   La causa más probable es que esa revisión se desplegó sin conector de
   VPC. **Esto todavía no está confirmado**: lo confirma el paso 7.

---

## Paso 1 · Comprobar que la `0019` está aplicada

Con el proxy de la bóveda abierto y `DSN_BOVEDA` definido (pasos 1.1 y 1.2
del runbook general):

```bash
psql "$DSN_BOVEDA" -tAc "
  select string_agg(c.relname, ', ' order by c.relname)
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public'
     and c.relname in ('ingesta_trabajo','ingesta_lote',
                       'estado_ingesta','estado_lote_ingesta',
                       'v_ingesta_progreso')"
```

Esperado: las cinco, separadas por coma. Si falta alguna, **parar acá** y
aplicar la `0019` con el paso 3 del runbook general; lo que sigue supone que
está.

---

## Paso 2 · Preparar la terminal

Se pierden al abrir otra terminal, así que van de nuevo:

```bash
setopt INTERACTIVE_COMMENTS        # solo zsh

export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
export FUNCION="procesaringesta"   # la función y la cola se llaman igual
export CUENTA="${PROYECTO}@appspot.gserviceaccount.com"
export URL="https://${REGION}-${PROYECTO}.cloudfunctions.net/${FUNCION}"
gcloud config set project "$PROYECTO"
printf 'URL: %s\nCuenta OIDC: %s\n' "$URL" "$CUENTA"
```

---

## Paso 3 · Traer el código con el renombre

```bash
cd ~/WorkSpaces/HTML/paneles
git checkout main && git pull
grep -n "^def procesaringesta" functions/main.py
```

Esperado: una línea. Si no aparece, el checkout no tiene el cambio y
desplegar va a volver a fallar con el error del Queue ID.

---

## Paso 4 · Corregir `TAREAS_URL`

Apunta al nombre viejo. Si no se corrige, las tareas salen hacia una URL que
ya no existe y los lotes mueren con 404.

```bash
gcloud secrets versions access latest --secret=TAREAS_URL --project="$PROYECTO"; echo
```

Si no termina en `/procesaringesta`, cargar la versión nueva:

```bash
printf '%s' "$URL" | gcloud secrets versions add TAREAS_URL \
  --project="$PROYECTO" --data-file=-
gcloud secrets versions access latest --secret=TAREAS_URL --project="$PROYECTO"; echo
```

El secreto nuevo **no llega al runtime hasta el próximo deploy**, que es el
paso 5.

---

## Paso 5 · Desplegar, con el conector exportado

Primero recuperar el conector real de la función que sí funciona:

```bash
export VPC_CONNECTOR="$(gcloud functions describe api \
  --gen2 --region="$REGION" --project="$PROYECTO" \
  --format='value(serviceConfig.vpcConnector)')"
printf 'Conector VPC: %s\n' "$VPC_CONNECTOR"
```

Tiene que imprimir una ruta completa
(`projects/…/locations/southamerica-east1/connectors/…`). **Si imprime vacío,
no desplegar**: significa que `api` tampoco lo tiene y hay que elegirlo de
`gcloud compute networks vpc-access connectors list --region="$REGION"`.

`main.py` lee esta variable del entorno **del deploy**, y `None` es un valor
válido: sin ella la función se despliega sin conector y sin un solo error.

```bash
firebase deploy --only functions --project="$PROYECTO"
```

Esperado: `api` y `procesaringesta`, las dos con `Successful update
operation` / `Successful create operation`, y **sin** el error 400 del Queue
ID. El CLI probablemente avise que `procesar_ingesta` ya no está en el
código y ofrezca borrarla: aceptar, y si se saltea el prompt, el paso 6.

---

## Paso 6 · Borrar la función huérfana

```bash
gcloud functions list --project="$PROYECTO" --regions="$REGION" --format='value(name)'
```

Si todavía figura `procesar_ingesta`:

```bash
firebase functions:delete procesar_ingesta --region="$REGION" --project="$PROYECTO"
```

Se borra con su servicio de Cloud Run (`procesar-ingesta`). Dejarla no rompe
nada, pero es una función privada que nadie invoca y que confunde al leer los
logs.

---

## Paso 7 · El conector en las dos funciones

**Este es el paso que explica los 127 segundos.** No saltearlo: si la función
nueva quedó otra vez sin conector, el síntoma no aparece hasta el primer lote
y no se parece a su causa.

```bash
for f in api "$FUNCION"; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)'
done
```

Esperado: dos líneas con **el mismo** conector y `PRIVATE_RANGES_ONLY`.

Una vacía significa que ese deploy corrió sin `VPC_CONNECTOR`: volver al paso
5. Un `yaml(...)` no sirve para esto, porque si la clave falta la omite en
silencio; por eso el comando usa `value(...)`.

Y la URL, que tiene que coincidir con el secreto del paso 4:

```bash
gcloud functions describe "$FUNCION" --gen2 --region="$REGION" --project="$PROYECTO" \
  --format='value(serviceConfig.uri)'
```

---

## Paso 8 · La cola

```bash
gcloud tasks queues describe "$FUNCION" --location="$REGION" --project="$PROYECTO"
```

Esperado: `state: RUNNING`, `maxConcurrentDispatches: 3`, `maxAttempts: 5`.
Esos tres valores salen de `main.py` (`TAREAS_EN_PARALELO`,
`TAREAS_REINTENTOS`) y el deploy los aplica.

Si quedaron en otra cosa porque la cola se creó a mano antes, el deploy del
paso 5 debería haberlos alineado; si no, corregirlos:

```bash
gcloud tasks queues update "$FUNCION" --location="$REGION" --project="$PROYECTO" \
  --max-concurrent-dispatches=3 --max-attempts=5
```

---

## Paso 9 · El permiso de invocación, sobre el nombre nuevo

El binding anterior era sobre `procesar_ingesta` y **no vale** para la
función nueva. Sin esto, las tareas salen y la función contesta 403: Cloud
Tasks reintenta cinco veces y el lote queda fallido con «403» en el error.

```bash
gcloud functions add-invoker-policy-binding "$FUNCION" \
  --region="$REGION" --member="serviceAccount:$CUENTA" --project="$PROYECTO"
```

Los otros dos permisos **no** cambian con el renombre, porque son sobre la
cuenta de servicio y no sobre la función: `roles/cloudtasks.enqueuer` y
`roles/iam.serviceAccountTokenCreator`. Si se otorgaron en el paso 6 del
runbook general, siguen valiendo.

---

## Paso 10 · Destrabar la carga que quedó colgada

Primero, ver qué hay:

```bash
psql "$DSN_BOVEDA" -c "
  select t.id, t.destino_tipo, t.destino_id, t.estado, t.lotes_total,
         count(l.id) filter (where l.estado = 'pendiente')  as pendientes,
         count(l.id) filter (where l.estado = 'procesando') as en_curso,
         count(l.id) filter (where l.estado = 'fallido')    as fallidos,
         count(l.id) filter (where l.estado = 'ok')         as ok
    from ingesta_trabajo t
    left join ingesta_lote l on l.trabajo_id = t.id
   where t.terminado_en is null
   group by t.id
   order by t.id"
```

**El botón de la pantalla no alcanza para esto, y conviene entender por qué.**
`reintentar()` solo reencola lotes en estado `fallido`. Los de esta carga
quedaron en `pendiente`: la función murió **antes** de poder conectarse a la
base, así que nunca llegó a marcar nada. Pedir reintentar devuelve «No hay
lotes fallidos para reintentar en esta carga».

Hay dos salidas. La primera conserva el archivo ya despivotado:

```sql
-- Reemplazar <ID> por el trabajo_id de la consulta de arriba.
update ingesta_lote
   set estado = 'fallido',
       error = 'la tarea no pudo conectar a la base',
       reintentable = true,
       actualizado_en = now()
 where trabajo_id = <ID>
   and estado in ('pendiente', 'procesando');
```

Y después, desde la pantalla de la encuesta (o de Panelistas, si era una
carga sin panel), **Reintentar los lotes fallidos**. Es seguro: la ingesta es
idempotente y lo que hubiera entrado no se duplica.

La segunda es descartar y volver a subir el archivo:

```sql
delete from ingesta_trabajo where id = <ID>;   -- los lotes se van en cascada
```

Conviene la primera si el archivo era grande, y la segunda si era la prueba
de dos filas.

---

## Paso 11 · Prueba de punta a punta

Con un archivo chico de verdad, no con el que quedó colgado:

1. Abrir una encuesta → **Ingestar respuestas**, subir el archivo y confirmar.
2. La respuesta tiene que ser inmediata y la pantalla mostrar la barra de
   avance con `N de M fila(s)`. Si en vez de eso aparece un aviso de
   `sin_encolar`, falló el **encolado**, que son otros permisos que el del
   paso 9: `cloudtasks.enqueuer` y `serviceAccountTokenCreator`, paso 6 del
   runbook general. El invoker del paso 9 no se nota acá: se nota después,
   con el lote fallido y «403» en el error.
3. **Recargar la página a mitad de camino.** La barra tiene que reaparecer
   sola y seguir donde estaba: ésa es la prueba de que el estado vive en la
   base y no en la pestaña.
4. Al terminar, el resumen consolidado —respuestas escritas, personas, sin
   mapear, sin consentimiento—.

Y el avance, visto desde la base mientras corre:

```bash
psql "$DSN_BOVEDA" -c "select * from v_ingesta_progreso order by trabajo_id desc limit 3"
```

---

## Paso 12 · Confirmar que los 127 segundos no vuelven

```bash
gcloud logging read \
  "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$FUNCION\"" \
  --project="$PROYECTO" --limit=20 --order=desc \
  --format='value(timestamp,severity,httpRequest.latency,textPayload)'
```

Lo que **no** tiene que aparecer:

* latencias de ~127 s con `status: 500`;
* `server closed the connection unexpectedly`;
* `no se pudo conectar al store «bóveda»` o `«semántico»`.

Ese último mensaje es nuevo: desde el PR #47 el error dice **a cuál de los
dos stores** no se pudo conectar, que antes había que deducir buscando la IP
privada de cada instancia.

---

## Si algo no coincide

| Lo que se ve | Qué pasó | Qué hacer |
|---|---|---|
| `Queue ID "procesar_ingesta" can contain only letters…` | El checkout no tiene el renombre | Paso 3 |
| El deploy termina bien pero el paso 7 da una línea vacía | Ese deploy corrió sin `VPC_CONNECTOR` | Paso 5, con el `export` |
| La tarea muere a los ~127 s | Sin ruta a la IP privada: casi siempre el conector | Paso 7 |
| `no se pudo conectar al store «semántico»` y la bóveda anda | El conector está, pero no alcanza esa instancia | Revisar que las dos instancias estén en la red del conector |
| La tarea recibe 403 | El invoker quedó sobre el nombre viejo | Paso 9 |
| La tarea recibe 404 | `TAREAS_URL` apunta al nombre viejo, o `api` no se redesplegó | Pasos 4 y 5 |
| La confirmación responde con `sin_encolar` | La cola rechazó las tareas. **El motivo viene en el texto de cada entrada**, y hay que leerlo: no es siempre el mismo | Casi siempre permisos: paso 6 del runbook general. La carga quedó guardada y se puede reintentar |
| «No hay lotes fallidos para reintentar en esta carga» | Los lotes están en `pendiente`, no en `fallido` | Paso 10 |
| `Las filas de este lote ya se purgaron` | Se reintenta un lote de una carga vieja ya purgada | Volver a subir el archivo |
| Dentro de `sin_encolar`, ``Falta `TAREAS_URL` `` | El secreto no llegó al runtime | Está declarado en `SECRETOS`, así que lo que falta es el deploy que lo monte: paso 5 |

---

## Checklist

- [ ] 1 · Las cinco relaciones de la `0019` existen en la bóveda
- [ ] 2 · Variables exportadas en **esta** terminal
- [ ] 3 · El checkout tiene `def procesaringesta`
- [ ] 4 · `TAREAS_URL` termina en `/procesaringesta`
- [ ] 5 · `VPC_CONNECTOR` exportado **antes** del deploy, y el deploy sin errores
- [ ] 6 · `procesar_ingesta` borrada
- [ ] 7 · **Las dos funciones devuelven el mismo conector** y `PRIVATE_RANGES_ONLY`
- [ ] 8 · Cola `procesaringesta` en `RUNNING`, 3 en paralelo, 5 intentos
- [ ] 9 · Invoker otorgado sobre `procesaringesta`
- [ ] 10 · La carga colgada, destrabada o descartada
- [ ] 11 · Carga chica completa, y sobrevive a recargar la página
- [ ] 12 · Sin latencias de 127 s ni errores de conexión en los logs
