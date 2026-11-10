# Despliegue — controles de los mensajes y de la identificación al cargar

**Pedido:** `specs/PEDIDO_controles_mensajes_e_identificacion.md` (R-CTL.1 a
R-CTL.4) · **Decisión:** D91
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0036`** (mensajes, canales,
selección y audiencias, D87 a D90) y su código desplegado.

**Qué cambia:**

| | Bóveda | Semántico | Secret Manager | Meta | `functions/.env` | Código |
|---|---|---|---|---|---|---|
| | **nada** | nada | nada | nada | nada | backend y frontend |

**No hay migración.** Los parámetros con texto fijo se guardan en el mismo
`encuesta_mensaje.parametros` (`jsonb`) que ya existe.

- **R-CTL.1:** la vista previa de cada envío, y cada lote, comparan el botón
  de la plantilla vigente con la URL de Alchemer vigente y bloquean si no
  coinciden. Las dos URL se muestran una debajo de la otra.
- **R-CTL.2:** una plantilla con encabezado de texto variable o de archivo no
  se guarda como mensaje ni se envía.
- **R-CTL.3:** la fuente «texto fijo» para los parámetros del cuerpo.
- **R-CTL.4:** se propone el tipo id_persona cuando la columna trae UUID, los
  avisos dicen con qué tipo se intentó identificar, y cambiar el tipo deja la
  revisión desactualizada.

**Efecto sobre lo que ya está armado.** Un mensaje de WhatsApp que se guardó
con «guardar igual» (botón que no coincide con la URL de la encuesta), o cuya
plantilla tiene un encabezado no soportado, **queda bloqueado al enviar**
desde este despliegue. Es el comportamiento buscado; el paso 6 los lista
antes de desplegar para que nadie se sorprenda.

**Qué NO cambia:**
- ningún secreto, rol de base, permiso, cola, función ni variable;
- **ningún webhook en Meta**: la URL de callback de la app ya la usa otro
  sistema (D86). Este despliegue no da de alta nada en Meta;
- la superficie de COLOQUIO. La batería tiene que seguir en **24 de 24**.

### Orden en una línea

proyecto fijado → `verificar_esquema.py` en verde → mensajes que van a quedar
bloqueados → token de COLOQUIO y batería → `firebase deploy` →
comprobaciones.

---

## Paso 1 · Preparar la terminal y fijar el proyecto

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
gcloud config get-value project
git checkout main && git pull
```

`gcloud config get-value project` tiene que decir **`gestion-paneles`**. Si
dice otra cosa, no seguir.

## Paso 2 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`.

## Paso 3 · La precondición: la `0036`

```bash
psql "$DSN_BOVEDA" -tAc "select to_regclass('encuesta_audiencia_agregado') is not null"
```

Tiene que dar `t`. Si da `f`, primero
`docs/DESPLIEGUE - mensajes, canales, selección aleatoria y audiencias.md`.

## Paso 4 · Verificar el esquema

Este despliegue no migra, pero la regla es la misma: el esquema en verde
**antes** del `firebase deploy`.

```bash
python3 scripts/verificar_esquema.py
```

Sin faltantes.

## Paso 5 · Qué mensajes van a quedar bloqueados

Los mensajes de WhatsApp con botón de URL cuya base no coincide con la URL de
Alchemer de su encuesta. Desde este despliegue no se pueden enviar hasta
corregir una de las dos:

```sql
select e.id as encuesta_id, e.nombre as encuesta, e.estado, m.nombre as mensaje,
       m.url_boton, e.url_alchemer
  from encuesta_mensaje m join encuesta e on e.id = m.encuesta_id
 where m.canal = 'whatsapp' and m.boton = 'url'
   and m.url_boton is distinct from e.url_alchemer
 order by e.estado, e.id;
```

Avisarle a operaciones de los que estén **en campo**: tienen que revisar la
URL de la encuesta o la plantilla antes del próximo envío.

Las plantillas con encabezado no soportado no se ven por SQL (viven en Meta):
aparecen en **+ Nuevo mensaje** marcadas *encabezado no soportado*, y la vista
previa de un mensaje que use una de ellas lo dice.

## Paso 6 · Token de COLOQUIO y su batería

El token dura una hora: se genera **ahora**, inmediatamente antes de la
batería.

```bash
source scripts/token_coloquio.sh
python3 scripts/verificar_coloquio.py
```

Tiene que terminar en **24 de 24**, sin fallidos. Nada de este despliegue toca
grants, vistas ni funciones de la superficie.

## Paso 7 · Desplegar

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"
```

No se crea ninguna función ni se pide ningún secreto nuevo.

```bash
gcloud functions list --regions="$REGION" --format='value(name)' | grep -c whatsappwebhook
```

Tiene que dar **`0`**.

## Paso 8 · Comprobaciones en la aplicación

**R-CTL.1 — la URL en cada envío.** En una encuesta de prueba en campo, con un
mensaje de WhatsApp de botón de URL:
- [ ] En **Datos del estudio**, cambiar la URL de Alchemer (por ejemplo,
      agregarle una barra al final): al guardar, el aviso dice que el mensaje
      va a quedar bloqueado.
- [ ] **Enviar…** sobre ese mensaje: la vista previa no deja enviar y muestra
      las dos URL completas, una debajo de la otra.
- [ ] Volver la URL a la original: la vista previa vuelve a dejar enviar.
      Cancelar sin enviar.

**R-CTL.2 — el encabezado.**
- [ ] En **+ Nuevo mensaje**, una plantilla con encabezado de imagen o con
      variable aparece marcada *encabezado no soportado*; elegirla muestra el
      motivo y guardar se rechaza. Si la cuenta no tiene ninguna, no hace falta
      crear una para probar: lo cubren las pruebas automáticas.

**R-CTL.3 — texto fijo.**
- [ ] En un mensaje nuevo, un parámetro del texto con **Texto fijo** pide el
      valor y se guarda.

**R-CTL.4 — el tipo de identificador.**
- [ ] Cargar en una encuesta de prueba un archivo de Alchemer cuya columna
      trae el `id_persona`: al elegir esa columna, el tipo pasa a *Trae el
      id_persona del sistema* con la ayuda «Propuesto…».
- [ ] En la revisión, cambiar el tipo: aparece *El resumen está
      desactualizado* y *Confirmar e importar* queda deshabilitado hasta
      *Revisar de nuevo*. Volver atrás sin importar.

---

## Qué mirar después

**Lotes de envío que fallaron por la URL o el encabezado** (un cambio entre la
vista previa y el lote):

```sql
select t.id, t.creado_en, l.indice, l.error
  from ingesta_trabajo t join ingesta_lote l on l.trabajo_id = t.id
 where t.destino_tipo = 'mensaje' and l.estado = 'fallido'
   and l.error ilike 'No se envía:%'
 order by t.creado_en desc limit 20;
```

Ninguno de esos lotes mandó mensajes: se corrige la URL o la plantilla y se
vuelve a enviar con «No enviado aún».

---

## Costos

**US$ 0.** Son validaciones y ajustes de interfaz. Evitan costo: un
encabezado no soportado hacía fallar cada envío, y una URL distinta mandaba a
la gente a otra encuesta.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q
```

- `tests/test_pedido_controles_mensajes_e_identificacion.py`:
  - la URL cambiada en la encuesta o en Meta bloquea el envío y muestra las
    dos; una barra o un `http` de diferencia también; la casilla de guardar
    no alcanza para enviar; un lote vuelve a comparar y no manda nada;
  - un encabezado de texto variable, imagen, video o documento no se guarda,
    uno de texto fijo sí, y uno sumado en Meta bloquea el envío;
  - el texto fijo completa un parámetro; vacío, con saltos o largo no; el
    cliente no es una fuente;
  - la columna con UUID propone id_persona, la revisión avisa si se eligió
    otro tipo, y el aviso de no convocados dice con qué tipo se intentó.
- `tests/test_pantallas_controles_mensajes_e_identificacion.py` (5,
  Chromium): las dos URL en la vista previa, el encabezado no soportado, el
  texto fijo, la propuesta del tipo y la revisión desactualizada.

---

## Volver atrás

Desplegar la versión anterior del código. No hay nada que deshacer en la
base. Los mensajes con texto fijo que se hayan creado mientras tanto quedan
con un parámetro que el código anterior no sabe leer: borrarlos (si no se
enviaron) o editarlos antes de volver atrás.

```sql
select m.encuesta_id, m.nombre from encuesta_mensaje m
 where exists (select 1 from jsonb_array_elements(m.parametros) p
                where jsonb_typeof(p) = 'object');
```
