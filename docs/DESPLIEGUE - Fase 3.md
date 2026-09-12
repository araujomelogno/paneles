# Despliegue — Fase 3

**Precondición:** Fase 2 desplegada, con su DoD cerrado y funcionando.
**Referencia:** `specs/SPEC_fase3.md`

---

## 0 · Qué cambia

Las Fases 1 y 2 dejaron el sistema **capaz de observar**. Esta lo deja capaz
de **actuar**: proponer a quién invitar, distinguir dato bueno de malo,
premiar la participación, crecer por una vía pública y sacar fricción del
trabajo diario.

En términos de despliegue, eso se traduce en cinco cosas concretas:

| Qué | Dónde | Riesgo si se saltea |
|---|---|---|
| Una migración nueva en la bóveda | `db/boveda/0005_fase3.sql` | Las pantallas nuevas fallan con `relation … does not exist` |
| Una dependencia nueva en la función | `pyreadstat` | La ingesta SAV responde 400 al primer archivo |
| Una página pública nueva | `/inscribirse` | La landing no existe |
| Tres decisiones legales pendientes | ver §1 | Se abren superficies de datos sin base legal |
| Nada en el store semántico | — | — |

**El store semántico no cambia en esta fase.** No hay migración nueva del lado
de la semántica, y el pipeline de consulta (recuperación → reranker →
verificación) queda exactamente igual.

### Se puede desplegar por partes

Los tres bloques son independientes y se pueden soltar por separado. Si el
sprint se corta, esta es la secuencia con menos deuda:

- **3A — salud del panel** (muestreo, calidad, puntos): no toca ninguna
  superficie pública ni crea personas. Es lo más seguro de soltar primero.
- **3C — fricción operativa** (SAV, exportación, panel desde consulta): el
  modo «ya existen» del SAV y las dos últimas se pueden habilitar sin
  ninguna definición legal pendiente.
- **3B — crecimiento** (landing): es la única superficie pública del sistema
  y **exige el texto de consentimiento revisado** antes de anunciarla.

---

## 1 · Antes de empezar: las tres definiciones pendientes

La spec marca tres puntos como bloqueantes (§11). Ninguno frena el despliegue
del código —está todo implementado— pero dos de ellos sí frenan **habilitar**
la funcionalidad. Conviene resolverlos antes de anunciar nada.

### 1.1 · Base legal del alta por SAV — *implementado con la opción conservadora*

El alta manual (R1.1) rechaza crear una persona sin consentimiento. El modo
«crear los individuos en esta carga» entra por otra puerta, y la spec deja
abierto con qué base legal.

**Lo que hace el sistema hoy:** esas personas se crean en estado
`pendiente_consentimiento`. Existen en la bóveda, pero el muestreo las excluye
y no pueden ser convocadas. Es una de las dos opciones que plantea la spec, y
es la conservadora.

No hay que hacer nada para que esto funcione. Lo que hay que decidir es si se
queda así o si el archivo puede traer evidencia de consentimiento; si se
decide lo segundo, el cambio es en `sav.crear_individuos` y en `personas.alta`.

Para regularizar a quien ya tenga base legal:

```bash
curl -s -X POST https://gestion-paneles.web.app/api/panelistas/regularizar \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"ids_persona": ["…"], "finalidad": "contacto_participacion",
       "version_texto": "consentimiento-2026-09"}'
```

### 1.2 · Texto de consentimiento de la landing — *bloqueante para 3B*

**El repositorio no trae ningún texto legal**, y es a propósito: si la
migración sembrara uno, alguien iba a publicar la landing creyendo que
sirve. Sin un texto publicado, el formulario público responde que no está
disponible y rechaza cualquier envío.

Cuando el DPO tenga el texto, se publica desde la app —Inscripciones →
Textos de consentimiento— o por API:

```bash
curl -s -X POST https://gestion-paneles.web.app/api/textos-consentimiento \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"finalidad": "contacto_participacion", "version": "2026-09",
       "cuerpo": "…el texto revisado…"}'
```

> **Una versión publicada no se puede reescribir.** Es una restricción del
> sistema, no un olvido: lo que alguien consintió tiene que seguir siendo
> recuperable tal cual. Para cambiar el texto se publica una versión nueva, y
> los consentimientos anteriores conservan la suya.

### 1.3 · Tratamiento fiscal del canje — *no bloquea el código*

La spec lo marca bloqueante para R3.5, pero también declara como no-goal
automatizar el tratamiento fiscal. El sistema registra el canje y su ciclo de
estados; la parte fiscal es un proceso de administración, fuera del software.
Lo que sí conviene es **no abrir el canje al panelista** hasta que finanzas
haya definido el tratamiento y exista logística de entrega.

Mientras tanto, el catálogo puede quedar cargado con los premios en `activo:
false`: se ven en la administración y no se pueden canjear.

---

## 2 · La migración

Una sola, y solo en la bóveda.

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0005_fase3.sql
```

> El `-v ON_ERROR_STOP=1` no es decorativo. Ya pasó una vez: sin él, `psql`
> sigue después de un error y la base queda a medio migrar sin que se note
> hasta semanas después.

### 2.1 · Qué crea

| Objeto | Para qué |
|---|---|
| `umbral_fatiga` | Los topes de convocatoria por panel (R3.1) |
| `bono_puntos` | Bonos dirigidos a un segmento (R3.6) |
| `texto_consentimiento` | Los textos versionados de la landing (R3.7) |
| `inscripcion` | Las solicitudes públicas, que **no** son personas todavía (R3.7) |
| `persona.estado` | `pendiente_consentimiento` para las altas por SAV (R3.9) |
| `participacion.duracion_segundos` y la revisión de calidad | Detectar speeders y poder revertir la marca (R3.2) |
| `encuesta.umbral_*` y `puntos_participacion` | Umbrales y puntos propios de cada estudio |
| `puntos_earn_unico_por_encuesta` | Que no se pueda liquidar dos veces, ni con concurrencia (R3.4) |
| `panel.origen` y `origen_definicion` | De dónde salió la composición de un panel (R3.11) |

### 2.2 · Verificar

```bash
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
python3 scripts/verificar_esquema.py
```

Tienen que salir las cinco migraciones de la bóveda y las tres de la
semántica, todas con tilde. Es la misma comprobación que hace la app en
Cumplimiento → Esquema de las dos bases.

---

## 3 · La dependencia nueva

`pyreadstat` lee los `.sav`. Ya está en `functions/requirements.txt`, así que
el `predeploy` de `firebase.json` la instala sola. Vale saber dos cosas:

- **Trae `numpy` y `pandas` con ella.** El paquete de la función crece unos
  60 MB. Está dentro del límite de Cloud Functions gen2, pero el primer
  deploy después de esto tarda más.
- **El parseo corre en el backend a propósito** (R3.9): no hay librería
  cliente confiable para `.sav`, y subir el archivo entero es lo que permite
  validarlo antes de escribir nada.

Si el deploy falla instalando la rueda, el síntoma es un error de compilación
de `pyreadstat` en el log del predeploy. La causa casi siempre es un runtime
distinto de `python311`; verificar el `runtime` en `firebase.json`.

---

## 4 · Permisos nuevos

Seis, y ninguno se le agrega a un rol existente por comodidad. Están en
`panel_api/auth.py` y el frontend tiene una copia para decidir qué solapas
mostrar —copia que **no** es el control de acceso: la autoridad es el
backend—.

| Permiso | Roles | Por qué así |
|---|---|---|
| `muestrear` | admin, operaciones | Decidir a quién invitar es del responsable de panel. El analista consulta, no decide la muestra. |
| `revisar_calidad` | admin, operaciones, analista | El chequeo lo corre la ingesta; la revisión la hace quien mira el caso. |
| `gamificacion` | admin, operaciones | Es dinero para el panelista. |
| `aprobar_inscripciones` | admin, operaciones | Es un alta de persona: va con quien ya podía enrolar. |
| `publicar_consentimiento` | admin, dpo | Es una decisión de cumplimiento antes que de operación. |
| `exportar_identificado` | admin, operaciones | **Separado de `reidentificar` a propósito** (ver §6). |

No hay que configurar nada: los permisos se derivan del rol que cada usuario
ya tiene en Firestore.

---

## 5 · La landing pública

### 5.1 · Cómo queda publicada

`web/public/inscribirse.html` es una página aparte que **no carga Firebase
Auth**. El `firebase.json` ya tiene el rewrite:

```
https://gestion-paneles.web.app/inscribirse
```

Va antes del catch-all de la SPA; si se reordena, la landing deja de existir.

### 5.2 · Las dos rutas sin token

Son las únicas de todo el sistema, y están enumeradas una por una en
`ruteo.PUBLICAS`:

```
GET  /api/inscripciones/formulario
POST /api/inscripciones
```

La lista es de rutas concretas y no de un prefijo, justamente para que
agregar una ruta debajo de `/inscripciones/` no abra un agujero que nadie
eligió abrir. Hay una prueba que falla si el conjunto cambia.

### 5.3 · Qué NO hace la landing

Vale enumerarlo porque es lo que se suele romper al «mejorarla»:

- **No crea personas.** Crea una solicitud en `inscripcion`. Recién al
  aprobar se crea el panelista. De ahí sale gratis que un pendiente no pueda
  ser convocado ni aparecer en una consulta semántica: todavía no existe.
- **No dice si la persona ya estaba.** Todos los envíos válidos reciben el
  mismo acuse. Un formulario que contesta «ya estás registrado» es un oráculo
  para averiguar quién es panelista probando documentos.
- **No expone ningún dato de otros panelistas.** El endpoint del formulario
  devuelve el texto de consentimiento y nada más.

### 5.4 · Antes de anunciarla

La landing es superficie pública sin endurecer: verificación de contacto y
anti-fraude son Fase 4 (R4.4). La spec lo marca como riesgo abierto. Dos
recomendaciones concretas:

1. **Abrirla con volumen limitado** —un canal, una campaña— y mirar la
   bandeja de pendientes antes de difundirla ampliamente.
2. **Poner Cloud Armor o rate limiting** en `/api/inscripciones` si se va a
   difundir en redes. Sin eso, un script llena la tabla de solicitudes basura
   en minutos. No degrada nada del resto del sistema —las inscripciones no
   son personas— pero le arruina la bandeja a quien tenga que revisarlas.

---

## 6 · La exportación con datos personales

Es la superficie de PII más nueva y conviene entender la regla antes de
habilitarla.

**Exportar con datos exige haber reidentificado primero.** No es una
comodidad de la interfaz: si la exportación pudiera resolver la PII por su
cuenta, habría dos caminos para sacarla de la bóveda y solo uno quedaría
auditado. La ruta recibe el resultado ya resuelto y lo usa tal cual.

Se registra en `reidentificacion` con motivo `exportacion`, distinto del
`consulta` que deja haberla mirado en pantalla. Para auditar:

```sql
select motivo, count(*), max(creado_en)
  from reidentificacion group by 1 order by 2 desc;
```

El archivo lleva la marca en el nombre —`consulta-CON-DATOS-PERSONALES-…`— y
en su primera línea. No incluye fecha de nacimiento exacta ni observaciones:
la primera es un identificador fino y las segundas son texto libre donde
suele terminar cayendo dato sensible.

El CSV seudonimizado de la Fase 2 **no cambió** y sigue siendo el botón por
defecto.

---

## 7 · Redesplegar

```bash
export VPC_CONNECTOR=paneles-conn
firebase deploy
```

No hay secretos nuevos en esta fase. Los cinco de siempre —`DSN_BOVEDA`,
`DSN_SEMANTICA`, `EMBEDDINGS_API_KEY`, `RERANKER_API_KEY`, `CLAUDE_API_KEY`—
siguen siendo los mismos.

---

## 8 · Verificación

En orden, porque cada paso depende del anterior. Todo desde la app, con un
usuario `admin`.

0. **El esquema.** Cumplimiento → Esquema de las dos bases tiene que decir que
   están las ocho migraciones. Si falta alguna, no tiene sentido seguir.

1. **Muestreo.** Muestreo → elegí un panel **con objetivo de composición
   cargado** y una encuesta abierta → Proponer.
   - Con brecha: la propuesta tiene que priorizar el segmento que falta y
     decirlo en la columna «Por qué está».
   - Sin brecha: tiene que avisar que el panel ya calza, y **no** decir que
     un segmento «tiene brecha (0 personas)».
   - Sin objetivo cargado: tiene que avisar que reparte parejo.
   - En todos los casos: «Es una sugerencia: nadie fue convocado», y la tabla
     de exclusiones con sus motivos.

2. **Umbrales de fatiga.** Muestreo → Umbrales de fatiga → bajá el máximo de
   convocatorias de la ventana a 1 y volvé a proponer: tiene que caer gente
   al motivo «superó el tope de convocatorias de la ventana».

3. **Calidad.** Corré los chequeos sobre una encuesta ya ingestada. Si el
   export no traía tiempos, el informe **tiene que decir** que el chequeo de
   speeder no se aplicó. Que no haya marcas de speeder no significa que no
   los haya: significa que no se pudo mirar.

4. **Revisión de una marca.** Revertí un `sospechoso` a `ok`. Tiene que
   quedar registrado quién lo hizo, y habilitar la liquidación de ese punto.

5. **Liquidación.** Puntos y premios → Liquidación → liquidá una encuesta.
   Solo tienen que cobrar quienes respondieron **y** quedaron en `ok`.
   Liquidá de nuevo: no puede pagar dos veces.

6. **Canje.** Cargá un premio, dale saldo a alguien con un ajuste y canjealo.
   Cancelá el canje: los puntos vuelven **como movimiento nuevo** y el stock
   se repone. El extracto tiene que mostrar el descuento original y la
   devolución, las dos cosas.

7. **Bono dirigido.** Creá un bono para un segmento y liquidá otra encuesta:
   solo ese segmento tiene que recibir el extra.

8. **Landing.** Publicá un texto de consentimiento y abrí `/inscribirse` en
   una ventana privada (sin sesión).
   - Enviar sin tildar el consentimiento tiene que rechazar **y no guardar
     nada**.
   - Enviar completo tiene que dar el acuse.
   - Inscripciones → Pendientes tiene que mostrarla, con la versión del texto
     y qué dijo la resolución de identidad.
   - Aprobar tiene que crear la persona con ese consentimiento.

9. **SAV.** Encuestas → Ingestar respuestas → subí un `.sav`. Los textos de
   las preguntas y las etiquetas de respuesta tienen que venir precargados, y
   las variables con label vacío o truncado, marcadas. Corregí un texto antes
   de confirmar: es lo que se vectoriza.

10. **Exportación con datos.** Consultas → correr una → «CSV con datos» tiene
    que estar **deshabilitado**. Usá «Ver quiénes son» y recién ahí se
    habilita. Después de descargar, verificá el registro con motivo
    `exportacion`.

11. **Panel desde consulta.** Consultas → Crear panel. Tiene que crear las
    membresías, avisar cuántos no son convocables, y quedar con
    `origen = 'consulta'` en la base.

---

## 9 · Problemas frecuentes

**«La pantalla de Muestreo dice que no hay encuestas abiertas.»**
Solo ofrece encuestas en `borrador` o `en_campo`. Una cerrada no admite
convocatorias nuevas, así que proponer para ella no tendría sentido.

**«La propuesta viene más corta de lo que pedí.»**
Es la conducta correcta cuando hay objetivo cargado: completar con gente de
segmentos que ya están cubiertos alejaría al panel de su universo de
referencia. El aviso al pie lo explica, y las exclusiones dicen qué frena a
cada uno.

**«El chequeo de calidad no marca ningún speeder.»**
Mirá la sección «no evaluado» del informe. Si dice que ninguna participación
trae duración, el chequeo no corrió: el export de campo tiene que traer el
tiempo de respuesta. Verificar con Dooblo/Alchemer qué trae cada export.

**«La liquidación no paga a nadie.»**
Solo paga a quien respondió **y** tiene `calidad_estado = 'ok'`. Si nunca se
corrieron los chequeos, todos quedan en `pendiente` y no cobran. Corré la
calidad primero.

**«El canje falla con saldo insuficiente y el saldo parece alcanzar.»**
El saldo es la suma de los movimientos en el momento del canje, con la
persona bloqueada. Si dos canjes salieron a la vez, uno cobró primero. Mirá
el extracto: el movimiento que ganó está ahí.

**«La landing dice que no está disponible.»**
No hay ningún texto de consentimiento publicado. Es el estado de fábrica y es
deliberado (§1.2).

**«Subir un `.sav` da error 400 diciendo que falta pyreadstat.»**
La función se desplegó sin instalar la dependencia. Revisar el log del
predeploy y volver a desplegar.

**«Un `.sav` precarga los textos como “P1”, “P2”…»**
El archivo no tiene variable labels. La precarga cae al código y lo marca con
un aviso. Hay que escribir los textos a mano antes de confirmar: si se
ingesta así, la consulta semántica sobre ese estudio no va a servir, porque
lo que se vectoriza es el texto.

**«Alguien creado por SAV no aparece en el muestreo.»**
Está en `pendiente_consentimiento` (§1.1). Es lo esperado hasta que se
registre su base legal.

---

## 10 · Volver atrás

**El código** se revierte redesplegando la versión anterior de la función; la
migración puede quedar aplicada sin molestar, porque todo lo que agrega es
aditivo (tablas nuevas y columnas con default).

**Apagar solo la landing** sin tocar nada más: sacar el rewrite de
`/inscribirse` de `firebase.json` y redesplegar hosting. Las rutas de API
siguen existiendo pero no hay página que las use. Para cerrarla del todo,
desactivar el texto de consentimiento:

```sql
update texto_consentimiento set activo = false
 where finalidad = 'contacto_participacion';
```

El formulario pasa a responder que no está disponible, sin borrar nada de lo
ya consentido.

**Si igual hay que revertir el esquema**, el orden es el inverso al de
creación y hay que asumir la pérdida de lo que esas tablas guardan
(inscripciones, movimientos de puntos, bonos, umbrales). No es una operación
de rutina: preferir dejar la migración aplicada.

---

## 11 · Checklist

Infraestructura:

- [ ] `db/boveda/0005_fase3.sql` aplicada.
- [ ] `python3 scripts/verificar_esquema.py` sale con código 0.
- [ ] `firebase deploy` completo, con `pyreadstat` instalado en el predeploy.
- [ ] El rewrite de `/inscribirse` está **antes** del catch-all.

Definiciones pendientes:

- [ ] Decidido qué pasa con las altas por SAV, o asumido el default
      conservador (`pendiente_consentimiento`).
- [ ] Texto de consentimiento revisado por el DPO y publicado — **sin esto la
      landing no recibe a nadie**.
- [ ] Tratamiento fiscal del canje definido, o catálogo cargado con los
      premios inactivos.
- [ ] Revisión con el DPO del conjunto de las tres superficies nuevas de datos
      personales (landing, alta por SAV, exportación con PII), no de a una.

Verificación funcional:

- [ ] Muestreo prioriza la brecha, explica las exclusiones y no convoca.
- [ ] Un panel sin brecha no reporta «brecha (0 personas)».
- [ ] Un export sin tiempos informa que no se pudo evaluar el speeder.
- [ ] Revertir un `sospechoso` habilita su liquidación.
- [ ] Liquidar dos veces no paga dos veces.
- [ ] Cancelar un canje devuelve los puntos como movimiento nuevo.
- [ ] Un bono alcanza solo a su segmento.
- [ ] La landing rechaza sin consentimiento y no guarda nada en ese caso.
- [ ] Aprobar una inscripción crea la persona con la versión que aceptó.
- [ ] Un `.sav` precarga textos y etiquetas, y marca los dudosos.
- [ ] «CSV con datos» está deshabilitado hasta reidentificar.
- [ ] Crear un panel desde una consulta registra su origen.

Calibración (después, pero no olvidarla):

- [ ] Umbrales de fatiga medidos contra los datos de Equipos. Los defaults
      —3 convocatorias en 90 días, 14 días entre olas— **no están medidos**.
- [ ] Umbral de speeder por estudio, medido contra la duración real de cada
      cuestionario. El default de 120 segundos es un punto de partida.
- [ ] Puntos por participación y costos del catálogo, contra lo que Equipos
      quiera gastar por ola.
