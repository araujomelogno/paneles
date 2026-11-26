# Despliegue — búsqueda y enlaces de Alchemer

**Pedido:** `specs/PEDIDO_busqueda_alchemer.md` (R-ALB.1 a R-ALB.5) ·
**Decisión:** D99
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0038`**, con el código de D96 a
D98 desplegado y los secretos `ALCHEMER_API_TOKEN` y `ALCHEMER_API_SECRET`
cargados.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | `functions/.env` | Cola | Código |
|---|---|---|---|---|---|---|
| | **`0039`** (no cambia datos) | nada | nada | `ALCHEMER_TIEMPO_S` (opcional) | ninguna nueva | backend y frontend |

La `boveda/0039` crea:

1. **`alchemer_encuesta`**: el catálogo de encuestas de la cuenta (id,
   título, estado, fechas). Ni cuestionario ni respuestas.
2. **`alchemer_sincronizacion`** y **`alchemer_sincronizacion_pagina`**: cada
   sincronización y sus páginas. Su avance se deriva de las páginas. Un índice
   único parcial impide dos en curso.
3. **`encuesta.alchemer_enlace_id`**: qué enlace de Alchemer dio la URL.

Todo con RLS y sin `grant` a nadie.

**La sincronización usa la cola de siempre** (`procesaringesta`, con
`TAREAS_URL` y `TAREAS_CUENTA`): la función despacha por `tipo`. No hay cola,
función ni secreto nuevos.

**Qué NO cambia:** el store semántico, ningún rol ni `grant`, ningún permiso
(buscar y sincronizar es `fieldear`), la superficie de COLOQUIO. La batería
tiene que seguir en **24 de 24**.

### Orden en una línea

proyecto fijado → **respaldo de la bóveda** → `boveda/0039` →
`verificar_esquema.py` en verde → token de COLOQUIO y batería →
`firebase deploy` → **primera sincronización, y esperar a que termine** →
comprobaciones.

La `0039` va **antes** del código: el código nuevo lee y escribe el catálogo y
`alchemer_enlace_id`. El código anterior no la nota.

### Puntos de control

| # | Qué tiene que cumplirse | Paso | Si no se cumple |
|---|---|---|---|
| 1 | Respaldo de la bóveda `SUCCESSFUL` | 2 | No se empieza |
| 2 | `verificar_esquema.py` sin faltantes | 4 | No se despliega |
| 3 | Batería de COLOQUIO en 24 de 24 | 5 | No se despliega |
| 4 | La primera sincronización termina con las ~22 páginas procesadas | 7 | Se mira el error de la página; ver «Errores» |

---

## Paso 1 · Preparar la terminal y fijar el proyecto

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
gcloud config get-value project
git checkout main && git pull
ls db/boveda/0039_catalogo_y_enlaces_de_alchemer.sql
```

`gcloud config get-value project` tiene que decir **`gestion-paneles`**. Si
dice otra cosa, no seguir.

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
cloud-sql-proxy gestion-paneles:$REGION:paneles-semantica --port 5433 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
psql "$DSN_BOVEDA" -tAc "select to_regclass('alchemer_cuestionario') is not null"
```

`paneles_boveda` y `t` (la `0038` está).

## Paso 2 · Respaldo de la bóveda — punto de control 1

```bash
gcloud sql backups create --instance=paneles-boveda --project="$PROYECTO" \
  --description="antes del catálogo de Alchemer (boveda/0039)"
gcloud sql backups list --instance=paneles-boveda --project="$PROYECTO" --limit=1
```

Tiene que figurar `SUCCESSFUL`. La `0039` no cambia datos, pero **sin
respaldo confirmado no se empieza**.

## Paso 3 · Aplicar `boveda/0039`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0039_catalogo_y_enlaces_de_alchemer.sql
```

Una transacción; termina en `COMMIT`. Es re-ejecutable.

## Paso 4 · Verificar el esquema — punto de control 2

```bash
python3 scripts/verificar_esquema.py --pii
```

Sin la `0039` nombra como faltantes `encuesta.alchemer_enlace_id`,
`alchemer_encuesta`, `alchemer_sincronizacion` y
`alchemer_sincronizacion_pagina`. Con ella, **nada**. En verde **antes** del
`firebase deploy`.

## Paso 5 · Token de COLOQUIO y su batería — punto de control 3

```bash
source scripts/token_coloquio.sh
python3 scripts/verificar_coloquio.py
```

**24 de 24**, sin fallidos. Las tablas nuevas no tienen `grant` a
`coloquio_app`.

## Paso 6 · Desplegar

Opcional, en `functions/.env`: cuánto se espera a Alchemer en una llamada.
El valor por defecto es 20 s, y tiene que quedar bien por debajo de los 60 s
de Hosting.

```bash
# ALCHEMER_TIEMPO_S=20
```

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
```

## Paso 7 · La primera sincronización — punto de control 4

Desde la aplicación: **Encuestas → + Nueva encuesta → Encuesta de Alchemer**,
abajo del buscador, **Actualizar**. Se ve *«Actualizando el catálogo… N de 22
página(s)»*. Con la cuenta actual son ~22 llamadas y **unos 3 minutos**. No
hace falta dejar la pantalla abierta: corre en Cloud Tasks.

Seguirla desde la terminal:

```bash
psql "$DSN_BOVEDA" -c "
  select s.id, s.modo, s.estado, s.paginas_total,
         count(*) filter (where p.estado = 'procesada') as procesadas,
         count(*) filter (where p.estado = 'fallida')   as fallidas
    from alchemer_sincronizacion s
    left join alchemer_sincronizacion_pagina p on p.sincronizacion_id = s.id
   group by s.id order by s.id desc limit 3"
```

**Esperar a que diga `terminada`**, con `procesadas = paginas_total` (22) y
`fallidas = 0`. Entonces:

```bash
psql "$DSN_BOVEDA" -tAc "select count(*) from alchemer_encuesta"
psql "$DSN_BOVEDA" -tAc "select id, titulo from alchemer_encuesta where id = '9105516'"
```

Unas **2.172** encuestas, y la 9105516 está (es de las últimas páginas).

Si queda en `en_curso` sin avanzar más de 15 minutos, **Actualizar** de nuevo
la retoma (vuelve a encolar las páginas pendientes). Si quedó `fallida`, la
página que falló tiene el motivo:

```bash
psql "$DSN_BOVEDA" -c "select pagina, error from alchemer_sincronizacion_pagina
                         where estado = 'fallida' order by sincronizacion_id desc, pagina"
```

y **Actualizar** lanza otra.

## Paso 8 · Comprobaciones en la aplicación

- [ ] **Buscar** «evaluación de lanzamientos» responde al instante y trae la
      9105516.
- [ ] **Pegar** `https://survey.alchemer.com/s3/9105516/…` y **Buscar**:
      completa el nombre y lista los **7 enlaces activos**, con nombre,
      estado y URL en `https://` (algunos en `encuestas.equipos.com.uy`).
- [ ] Una encuesta con un solo enlace muestra el selector igual.
- [ ] En la ficha de una encuesta vinculada, **Cambiar enlace**: la revisión
      muestra la URL nueva y, si hay mensajes de WhatsApp con botón a la
      anterior, el aviso con la casilla. **No guardar** si es una encuesta
      real en campo.
- [ ] Los logs no muestran llamadas a la lista de encuestas fuera de las
      tareas: `gcloud functions logs read procesaringesta --region="$REGION" --limit=30`.

---

## Qué mirar después

**Las sincronizaciones** (la incremental dice si Alchemer aplicó el filtro):

```sql
select id, modo, filtro, estado, paginas_total, eliminadas, iniciada_en, terminada_en, error
  from alchemer_sincronizacion order by id desc limit 10;
```

- `filtro = 'aplicado'`: la incremental trajo solo lo modificado.
- `filtro = 'ignorado'` o `'rechazado'`: Alchemer no aplicó el filtro por
  fecha y la corrida recorrió todo. Funciona igual, y tarda lo mismo que una
  completa. Si es siempre así, el filtro de la API v5 no admite ese campo: es
  el dato que faltaba para R-ALB.4 (la documentación no se pudo consultar al
  desarrollarlo).

---

## Errores y correcciones

| Síntoma | Causa | Qué hacer |
|---|---|---|
| «Actualizar» dice *No se pudo encolar la sincronización* | Falta `TAREAS_URL` o `TAREAS_CUENTA` | Son los de la ingesta: revisar que estén cargados y declarados |
| La sincronización queda en `en_curso` con 0 páginas | La tarea no llegó a la función (cola pausada, cuenta sin permiso) | `gcloud tasks queues describe procesaringesta --location="$REGION"`; después **Actualizar** la retoma |
| Páginas `fallida` con *Alchemer respondió 401* | Credenciales o región de la cuenta | `ALCHEMER_REGION` (us, eu, ca) en `functions/.env` y los secretos |
| La pantalla dice *Alchemer tardó demasiado en responder.* | Alchemer lento en ese momento | Reintentar, o pegar la URL |

---

## Costos

**US$ 0.** El catálogo son unas 2.000 filas de texto. La sincronización son ~22
llamadas a la API de Alchemer (incluidas en el plan) y ~22 tareas de Cloud
Tasks, dentro de la capa gratuita. Buscar no llama a Alchemer.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_pedido_busqueda_alchemer.py`, con una API simulada de **2.172
  encuestas en 22 páginas**:
  - el id sale de la URL pública, de la de edición o del id solo;
  - pegar la URL resuelve con **una** llamada, y vincular guarda el enlace y
    refresca el catálogo;
  - la primera sincronización recorre las 22 páginas, de a una por tarea;
  - la búsqueda responde desde el catálogo sin llamar a Alchemer e incluye la
    encuesta de la página 22;
  - incremental si el filtro se aplica, completa si se ignora o se rechaza;
  - una completa borra lo que ya no existe;
  - una página lenta se reintenta y una rechazada no borra nada;
  - una sola en curso, y la estancada se reanuda;
  - un corte de tiempo es un error con su motivo;
  - los **7 enlaces reales de la 9105516** con su URL en `https://`;
  - cambiar de enlace avisa por las plantillas de WhatsApp.
- `tests/test_pantallas_busqueda_alchemer.py` (Chromium):
  - pegar la URL lista los enlaces;
  - un solo enlace se ve igual;
  - el catálogo y «Actualizar» con su avance;
  - el corte de tiempo con «Reintentar» y «Pegar la URL»;
  - cambiar el enlace desde la ficha, con la casilla.

---

## Volver atrás

**El código:** antes, que **no haya ninguna sincronización en curso** (la
consulta del paso 7): el código anterior no conoce el `tipo` de la tarea y
leería una página pendiente como un lote de ingesta con ese número. Si hay
una, esperar a que termine o marcarla:
`update alchemer_sincronizacion set estado = 'fallida', terminada_en = now(), error = 'vuelta atrás' where estado = 'en_curso'`.
Después, desplegar la versión anterior. Su búsqueda vuelve a no servir con la
cuenta real (el motivo de este pedido); mientras tanto, la URL se pega a mano en
el campo de la URL. Las tablas nuevas quedan y no molestan.

**La migración:** no hace falta revertirla. Si se quiere:
`drop table alchemer_sincronizacion_pagina, alchemer_sincronizacion, alchemer_encuesta`
y `alter table encuesta drop column alchemer_enlace_id`.
