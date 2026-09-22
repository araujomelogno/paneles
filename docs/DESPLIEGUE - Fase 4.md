# Despliegue — Fase 4

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Cubre:** los cinco requisitos de la fase, en dos bloques independientes.

| Bloque | Requisitos | Tema |
|---|---|---|
| **4A — Contacto** | R4.3, R4.4, R4.5 | Endurecer la entrada y contactar por el canal aceptado |
| **4B — Inteligencia** | R4.1, R4.2 | Explotar el tiempo y decidir bajo tensión |

**Precondición:** Fase 3 desplegada, con las migraciones 0005 a 0008 aplicadas

> **Se puede desplegar todo junto o por bloque.** Los dos son independientes:
> 4A no toca nada de lo que usa 4B y al revés. Lo que **no** conviene es
> aplicar las migraciones a medias, porque el diagnóstico de esquema informa
> por archivo y una migración a medio aplicar se ve como faltante.

---

## 0 · Qué cambia

La Fase 4 cierra la puerta de entrada, abre un canal de salida y empieza a
explotar el tiempo acumulado.

| Qué | Dónde | Riesgo si se saltea |
|---|---|---|
| Tres migraciones nuevas: dos en la bóveda y una en la semántica | `0009`, `0010`, `0011` y `semantica/0004` | La landing, la ficha, las encuestas y la composición fallan con `relation … does not exist` o `function … does not exist` |
| **La composición retroactiva pasa a estar bien calculada** | R4.1.a | Sin la 0010, recalcular la composición de una ola pasada la sigue calculando con la demografía de hoy: un número incorrecto que no avisa |
| **La landing deja de aceptar envíos sin verificar** | R4.3 | Nada se rompe, pero **nadie más se puede inscribir** hasta que haya proveedor de códigos. Es el cambio con más consecuencia operativa del bloque |
| Un eje de permiso nuevo para contactar | R4.4 | Los panelistas existentes quedan **sin preferencias**, o sea no contactables por ningún canal, hasta que se registren |
| Credenciales de Meta y de un proveedor de códigos | ver §3 | Sin ellas se puede configurar todo pero no enviar nada, y la landing no verifica de verdad |
| Una pantalla nueva, «Longitudinal» | R4.1 | Las series y la evolución entre olas no se pueden usar |
| El optimizador, junto a las reglas en Muestreo | R4.2 | Las reglas de R3.1 siguen funcionando: 4B no las reemplaza |

**El pipeline de consulta no cambia.** La 0004 del store semántico agrega las
tablas de series y una columna de embedding para el texto de las preguntas,
pero la consulta semántica resuelve exactamente igual que antes.

> **Lo que este bloque deliberadamente no hace.** El sistema **solo envía** por
> WhatsApp: no recibe respuestas, no procesa webhooks y no crea Flows ni
> plantillas. El analista baja las respuestas de Meta y las ingesta por el
> flujo de siempre, con el `flow_token` mapeando directo. WhatsApp es un canal
> de envío, no una plataforma de recolección.

---

## 1 · Antes de empezar: las tres definiciones legales

Son del bloque 4A. Ninguna bloquea aplicar las migraciones; las tres bloquean
**usar** el canal de WhatsApp. El bloque 4B no tiene definiciones legales
pendientes: no manda nada afuera ni cambia qué datos se guardan.

### 1.1 · El opt-in de WhatsApp de los panelistas ya enrolados — *bloqueante para enviar*

Los panelistas que ya están en el panel **no tienen opt-in de WhatsApp**:
nadie se lo pidió, porque hasta ahora el canal no existía. El consentimiento
de `contacto_participacion` que sí tienen autoriza a contactarlos, pero no
dice por qué medio.

Hay que obtenerlo antes de poder mandarles. Las vías, en orden de solidez:

1. **La landing**, para los que se inscriban de ahora en más: el opt-in lo da
   el titular en el momento, que es la forma más sólida frente a Meta y frente
   a URCDP.
2. **Una ola de consulta por otro canal** (correo o teléfono) preguntándolo, y
   después registrarlo desde la ficha.
3. **Un archivo de campo** que lo evidencie, declarando la variable al
   ingestar, igual que la evidencia de consentimiento.

> **No hay una cuarta vía.** Activar la preferencia «porque tenemos su
> celular» es exactamente lo que la política de Meta prohíbe, y lo que quema
> el número.

### 1.2 · Transferencia de celulares a Meta — *revisar antes del primer envío*

Enviar por WhatsApp implica mandarle el número de la persona a Meta: es
compartir datos personales con un tercero fuera del país. Bajo URCDP eso es
una **transferencia internacional** y una relación con un **encargado de
tratamiento**, y las dos tienen requisitos propios.

*Revisarlo con el DPO antes del primer envío real.* El sistema no lo bloquea
porque no es una condición técnica, pero es una condición.

### 1.3 · El texto del opt-in de WhatsApp

Cada preferencia guarda **con qué texto se obtuvo**, igual que el
consentimiento. Ese texto tiene que decir con claridad que la persona va a
recibir mensajes de WhatsApp de Equipos Consultores y para qué. El de la
landing está en el formulario; el de las otras dos vías lo define quien las
opere.

---

## 2 · Las migraciones

Tres en la bóveda y una en el store semántico. **En este orden.**

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

# Bloque 4A
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0009_fase4_contacto.sql
# Bloque 4B
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0010_fase4_historial_atributos.sql
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0011_fase4_inteligencia.sql
```

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"

psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 -f db/semantica/0004_series.sql
```

**Tres de las cuatro son aditivas** y se pueden aplicar con la app andando:
crean tablas y agregan columnas nullables o con default.

> ### La 0010 es la excepción: leerla antes de aplicarla
>
> Es la única migración **no aditiva** de la fase. Hace tres cosas que
> conviene entender antes de correrla:
>
> 1. **Agrega `desde`/`hasta` a `persona_atributo` y reescribe todas sus
>    filas** para ponerles `desde = '-infinity'`. En una tabla de cientos de
>    miles de filas eso es un `UPDATE` completo: **aplicarla en ventana**, no
>    en el pico de uso.
> 2. **Reemplaza el índice único** `persona_atributo_unico` por uno parcial
>    sobre la vigencia abierta, y agrega una restricción de exclusión que
>    necesita la extensión **`btree_gist`**. La migración la crea con
>    `create extension if not exists`; en Cloud SQL está disponible, pero el
>    usuario que aplica la migración tiene que poder crear extensiones.
> 3. **Recrea `v_atributo_persona` y `v_demografia`** sobre una función nueva,
>    `f_atributo_persona(momento)`. Los dos `drop view` van antes de los
>    `create`, así que **entre esas dos sentencias las vistas no existen**:
>    toda la app que segmenta falla durante ese instante. Es corto, pero es
>    otra razón para aplicarla en ventana.
>
> **Lo que la 0010 no rompe:** las vistas conservan su nombre y sus columnas,
> así que composición, consultas, muestreo, cuotas, bonos, ficha y
> exportaciones siguen andando sin cambios. Eso está cubierto por pruebas de
> no regresión.
>
> **Revertirla no es trivial** (habría que volver a un solo valor por
> atributo, y eso pierde el historial que se haya acumulado). Tomar un backup
> de `persona_atributo` antes:
>
> ```bash
> pg_dump "$DSN_BOVEDA" -t persona_atributo > persona_atributo_pre_0010.sql
> ```

### 2.1 · Qué crea

| Objeto | Para qué |
|---|---|
| `preferencia_canal` | Por qué canal acepta cada persona que la contacten, con el texto con que lo aceptó y de qué camino de alta vino (R4.4) |
| `verificacion_contacto` | Los códigos de un solo uso que endurecen la landing. El código va **hasheado** y la IP también (R4.3) |
| `inscripcion.celular_verificado` / `email_verificado` | Qué verificó cada inscripción. `false` en las anteriores a esta migración, que no pasaron por verificación |
| `inscripcion.canales` | Los canales que el titular aceptó de primera mano en el formulario. Se convierten en preferencias al aprobar |
| `encuesta.flow_id` / `flow_plantilla` / `flow_idioma` | Configurar una encuesta como WhatsApp Flow (R4.5) |
| `participacion.enviado_en` / `envio_estado` / `envio_error` | El estado del envío por persona, para reintentar solo los fallidos |
| `persona_atributo.desde` / `hasta` | El historial: desde y hasta cuándo valió cada valor (R4.1.a) |
| `f_atributo_persona(momento)` | La única implementación de la resolución de atributos, ahora a una fecha. `v_atributo_persona` es esta función en `now()` |
| `serie_auditoria` | Quién tocó una serie y cuándo. La serie vive del lado semántico; el nombre de quien la editó, nunca (R4.1.b) |
| `peso_optimizador` | Cuánto pesa la fatiga frente a la cuota, por panel (R4.2) |
| `serie`, `serie_categoria`, `serie_pregunta`, `serie_mapeo` *(semántica)* | Las series comparables entre olas (R4.1.b) |
| `pregunta.embedding_texto` *(semántica)* | El texto de la pregunta embebido, para sugerir candidatas. Se llena solo, la primera vez que se piden sugerencias |

### 2.2 · Verificar

```bash
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
python3 scripts/verificar_esquema.py
```

Tienen que salir las **once** migraciones de la bóveda y las **cuatro** de la
semántica, todas con tilde. El diagnóstico ahora también verifica funciones y
no solo tablas y vistas: sin eso la 0010 se daría por aplicada con las
columnas puestas y `f_atributo_persona` ausente, que es justo de lo que cuelga
todo lo que segmenta.

Y una comprobación que vale la pena hacer a mano, porque es la que prueba que
la corrección de R4.1.a quedó bien:

En la **bóveda** (con `cloud-sql-proxy --port 5432 …:paneles-boveda` corriendo
en otra terminal):

```bash
psql -h 127.0.0.1 -p 5432 -U app_paneles -d paneles_boveda -c "
-- Toda persona con algún atributo cargado tiene que tener exactamente un
-- valor vigente por atributo, y ninguna vigencia solapada. Si esto devuelve
-- filas, la migración no terminó bien.
select id_persona, atributo_id, count(*)
  from persona_atributo where hasta is null
 group by 1, 2 having count(*) > 1;"
```

---

## 3 · Las credenciales

Tres bloques, y **ninguno va al repositorio**. En producción, Secret Manager.

### 3.1 · WhatsApp Business (R4.5)

```
WHATSAPP_TOKEN            token de acceso de la app de Meta
WHATSAPP_PHONE_NUMBER_ID  el número emisor
WHATSAPP_WABA_ID          la cuenta de WhatsApp Business
```

Los tres valores salen de **Meta for Developers → tu app → WhatsApp → API
Setup**. Se cargan como los de las fases anteriores (ojo con el `-n`: sin él
se guarda un salto de línea y Meta rechaza el token con un error poco claro):

```bash
echo -n "EL_TOKEN" \
  | firebase functions:secrets:set WHATSAPP_TOKEN --data-file -

echo -n "EL_PHONE_NUMBER_ID" \
  | firebase functions:secrets:set WHATSAPP_PHONE_NUMBER_ID --data-file -

echo -n "EL_WABA_ID" \
  | firebase functions:secrets:set WHATSAPP_WABA_ID --data-file -
```

Verificar: `firebase functions:secrets:access WHATSAPP_TOKEN`.

> **El token de la consola es temporal.** El que Meta muestra por defecto en
> API Setup dura 24 horas: sirve para probar, no para producción. Para que el
> envío funcione de forma estable hace falta un token permanente de usuario
> del sistema. Si el primer envío anda y al día siguiente falla con error de
> autenticación, es esto.

La configuración es **a nivel sistema, no por encuesta**: hay un número
emisor y es el mismo para todas.

Sin ellas, una encuesta se puede configurar como Flow pero el envío queda
deshabilitado y la pantalla lo dice. Sin `WHATSAPP_WABA_ID` en particular no
se puede comprobar que la plantilla esté aprobada, y por eso el envío también
se bloquea: enviar con una plantilla rechazada falla persona por persona.

**Antes del primer envío hacen falta, además del token:**

- Cuenta de WhatsApp Business **verificada**.
- El **Flow publicado** en Meta (no en borrador).
- La **plantilla aprobada** que lo contiene. La revisión de Meta demora, así
  que conviene mandarla a aprobar apenas se sepa el texto: **no se puede
  configurar la encuesta y convocar el mismo día**.

El sistema valida las dos cosas contra la API antes de dejar convocar, y si
alguna falla lo dice con su motivo en vez de fallar al enviar.

### 3.2 · Envío de códigos de verificación (R4.3)

```
VERIFICACION_ENVIO_PROVEEDOR   'ninguno' (default) | 'log'
VERIFICACION_SAL               la sal con que se hashean códigos y orígenes
```

```bash
# Generar una sal larga y aleatoria, y cargarla.
openssl rand -base64 32 \
  | tr -d '\n' \
  | firebase functions:secrets:set VERIFICACION_SAL --data-file -

# El proveedor es configuración, no secreto: va como variable de entorno
# de la función (en su configuración de despliegue), no en Secret Manager.
```

> **Sin proveedor la landing no verifica nada.** El código vuelve en la
> respuesta del propio pedido, o sea que quien lo pide lo recibe sin
> necesidad de tener acceso al contacto. El sistema lo dice con todas las
> letras —en la respuesta y en el diagnóstico de Cumplimiento— pero **la
> landing no se puede anunciar así**.

`VERIFICACION_SAL` no es opcional en producción: sin ella el hash de un código
de seis dígitos se revierte con una tabla de un millón de entradas, y el de
una IP también.

**Los valores.** `VERIFICACION_ENVIO_PROVEEDOR` acepta hoy dos:

- **`ninguno`** (default): no se envía nada y el código vuelve en la respuesta.
  Sirve para probar el circuito; **no es publicable** (ver aviso de arriba).
- **`log`**: no envía tampoco, pero deja el código en los logs de la función.
  Útil para probar el flujo completo sin contratar un proveedor, revisando el
  código con `gcloud run services logs read api --region=southamerica-east1`.

**Para verificar de verdad hace falta un proveedor real**, y todavía no hay
ninguno implementado: es trabajo pendiente, no configuración. Se agrega en
`verificacion_contacto.proveedor_de_envio`, una función que devuelve
`enviar(canal, destino, codigo)` — el mismo patrón de interfaz que ya se usa
para los embeddings y el reranker. Al agregarlo, su credencial va a Secret
Manager como las demás, y el nuevo valor se declara en
`VERIFICACION_ENVIO_PROVEEDOR`.

> **Consecuencia para el despliegue.** Hasta que exista un proveedor real, la
> landing **no puede difundirse públicamente**: R4.3 exige verificar el
> contacto antes de que una inscripción llegue a la cola, y sin envío no hay
> verificación posible. Con `ninguno` o `log` el circuito se prueba, no se
> opera.

### 3.3 · Desafío anti-automatización (R4.3)

```
DESAFIO_PROVEEDOR   'ninguno' (default) | 'turnstile' | 'recaptcha'
DESAFIO_SECRETO     el secreto del lado servidor
```

**Qué es.** «Desafío» es *challenge*: el widget que comprueba que del otro lado
hay una persona y no un script. Sin él, la landing acepta cualquier envío, y
un script puede inscribir miles de personas falsas en minutos — que llegan a
la cola de aprobación como si fueran legítimas.

**Cómo funciona.** El proveedor entrega **dos claves**:

| Clave | Dónde va | Para qué |
|---|---|---|
| **Pública** (site key) | En el HTML de la landing | Muestra el widget al visitante |
| **Privada** (secret key) | En Secret Manager, como `DESAFIO_SECRETO` | El backend la usa para preguntarle al proveedor si el token que recibió es válido |

Cuando alguien envía el formulario, el widget genera un token. El backend se
lo manda al proveedor junto con el secreto, y el proveedor responde si es
legítimo. Sin el secreto, cualquiera podría inventar un token.

**Qué proveedor elegir.** Las dos opciones soportadas:

- **`turnstile`** — Cloudflare Turnstile. Gratis, no requiere cuenta de
  Google, y en la mayoría de los casos no le muestra ningún rompecabezas al
  visitante (resuelve en silencio). **Es la opción recomendada** salvo que ya
  usen reCAPTCHA en otro lado.
- **`recaptcha`** — Google reCAPTCHA. Equivalente en función; conviene si ya
  hay una cuenta y prácticas establecidas.

**Cómo obtener las claves (Turnstile).**

1. Entrar a la cuenta de Cloudflare (crearla si no existe; no hace falta tener
   el dominio en Cloudflare).
2. Ir a **Turnstile → Add site**.
3. Poner el dominio desde donde se sirve la landing (`gestion-paneles.web.app`,
   o el dominio propio si ya se configuró). Para probar en local, agregar
   también `localhost`.
4. Elegir el modo **Managed** (el proveedor decide cuándo desafiar).
5. Copiar las dos claves que quedan a la vista: **site key** y **secret key**.

**Cargar el secreto:**

```bash
echo -n "LA_SECRET_KEY" \
  | firebase functions:secrets:set DESAFIO_SECRETO --data-file -
```

**Configurar el resto:**

- `DESAFIO_PROVEEDOR` es configuración (variable de entorno de la función), no
  secreto: se setea en `turnstile` o `recaptcha`.
- La **site key** va en el front de la landing, junto a la configuración de
  Firebase en `web/public/inscribirse.html`. Es pública por diseño: no es un
  secreto y no pasa nada si se ve en el código de la página.

**Verificar que quedó andando.** Abrir la landing y enviar el formulario: el
widget tiene que aparecer (o resolverse solo, en modo Managed) y el envío
completarse. **Cumplimiento → Contacto: landing y WhatsApp** informa si el
desafío está configurado.

> **Si se deja en `ninguno`.** La landing sigue funcionando, pero no distingue
> un envío automatizado de una persona. No bloquea el despliegue y es una
> opción válida mientras la landing no se difunda públicamente — pero conviene
> que sea una decisión tomada, no un olvido. Por eso está en el checklist.

### 3.4 · Comprobarlo todo desde la app

**Cumplimiento → Contacto: landing y WhatsApp** informa las tres cosas y
enumera lo que falta. Es la forma de verificar la configuración sin leer
variables de entorno en la consola de GCP.

---

## 4 · Permisos nuevos

| Permiso | Quién | Por qué |
|---|---|---|
| `gestionar_canales` | admin, operaciones | Registrar y revocar por qué canal se puede contactar a alguien. Va con quien ya podía enrolar: es parte de la ficha, no una capacidad aparte |
| `enviar_whatsapp` | admin, operaciones | **No lo tiene el analista.** Cada conversación se cobra y un envío mal dirigido quema el canal para todos. El analista fieldea, ingesta y consulta, pero no manda |
| `gestionar_series` | admin, operaciones, **analista** | Declarar que dos preguntas de olas distintas son la misma medición es una decisión metodológica: es del analista tanto como de operaciones. El dpo no, y no es por jerarquía: no es su trabajo y una serie mal armada cambia lo que dicen los resultados |
| `configurar_optimizador` | admin, operaciones | Cambiar cuánto pesa la fatiga frente a la cuota. Va con `muestrear`, que es de quien decide a quién invitar: aflojar el peso de la fatiga es decidir quemar un poco más al panel |

**La línea de tiempo de una persona pide `reidentificar`, no `leer`.** Verla es
la operación que deshace la seudonimización, y queda registrada como tal
(R3.10). Que sea más cómoda de mirar no la hace menos reidentificación: hacerla
fácil sin registrarla habría sido aflojar el diseño de dos stores por la puerta
de atrás.

---

## 5 · El cambio de comportamiento que hay que avisar

### 5.1 · La landing deja de aceptar envíos sin verificar

Es el cambio con más consecuencia operativa del bloque. Desde que se despliega:

1. Quien se inscribe tiene que **verificar su correo** con un código.
2. Si además deja celular, **también lo tiene que verificar**.
3. Sin eso, la inscripción **no llega a la cola de aprobación**.

**Consecuencia directa:** si no hay proveedor de envío de códigos configurado,
nadie se puede inscribir de verdad. Si la landing está anunciada y en uso,
configurar el proveedor **antes** de desplegar, o aceptar la ventana.

### 5.2 · Los panelistas existentes quedan sin preferencias de canal

La migración no inventa preferencias: nadie declaró ninguna todavía. Eso
significa que, apenas se despliega, **ningún panelista existente es
contactable por WhatsApp**, y el envío lo va a informar como
`sin_preferencia_whatsapp`.

No es un error: es el estado correcto hasta que se obtenga el opt-in (§1.1).

### 5.3 · Los celulares se guardan en E.164

Los que ya están cargados **no se migran**: se normalizan a medida que se
editan o se vuelven a cargar. Un celular en formato local sigue sirviendo para
llamar, y lo que no va a poder es activar WhatsApp. Si interesa normalizarlos
todos de una, es un script aparte y conviene correrlo revisando los que no se
pueden convertir.

---

## 6 · Redesplegar

```bash
firebase deploy
```

Sin dependencias nuevas de Python: R4.3 y R4.5 usan `urllib` de la biblioteca
estándar. El `requirements.txt` no cambia.

---

## 7 · Verificación

Después de desplegar, en este orden:

- [ ] `python3 scripts/verificar_esquema.py` sale con código 0.
- [ ] **Cumplimiento → Contacto** muestra los tres bloques y enumera lo que
      falta configurar.
- [ ] En la landing, enviar sin verificar el correo **no crea** la inscripción
      y lo dice.
- [ ] Pedir el código, ingresarlo y enviar: la inscripción entra y aparece
      marcada como verificada.
- [ ] Pedir seis códigos seguidos para el mismo correo: el sexto se rechaza
      por tasa.
- [ ] Un panelista nuevo enrolado con WhatsApp marcado y sin celular válido
      se crea igual, y la pantalla avisa que ese canal no se activó.
- [ ] En la ficha de un panelista, la tarjeta de canales muestra el texto con
      que aceptó cada uno, y revocar WhatsApp no toca el correo.
- [ ] Configurar una encuesta con un Flow y una plantilla inexistentes: la
      pantalla dice que **no se puede enviar** y por qué, en vez de dejar
      enviar y fallar.
- [ ] Con el Flow publicado y la plantilla aprobada, el envío informa cuántos
      quedan fuera y **por qué motivo** cada uno.
- [ ] Reintentar después de un fallo no le reenvía a quien ya recibió.
- [ ] Un envío real llega con el botón del Flow, y el `flow_token` del export
      de Meta trae el `id_persona`. **Verificar esto en el primer envío**: la
      spec lo anota como pendiente de comprobación.

---

## 8 · Riesgos operativos

- **El canal se quema.** Sobre-convocar por WhatsApp genera bloqueos, baja el
  *quality rating* y Meta puede restringir el número. Los umbrales de fatiga
  pasan a proteger también el canal, no solo al panelista. Conviene mirar el
  *quality rating* después de cada ola.
- **Los mensajes se cobran por conversación.** Estimar el costo por ola antes
  de abrir el canal a todos los estudios.
- **Sin canal de entrada no se procesan opt-outs.** Si alguien bloquea o
  responde «STOP», el sistema no se entera. Mitigación: revocar a mano desde
  la ficha y revisar los reportes de Meta periódicamente. Si el volumen crece,
  recibir webhooks deja de ser opcional.

---

## 9 · Checklist

Infraestructura:

- [ ] `db/boveda/0009_fase4_contacto.sql` aplicada *(4A)*.
- [ ] Backup de `persona_atributo` tomado *(antes de la 0010)*.
- [ ] `db/boveda/0010_fase4_historial_atributos.sql` aplicada **en ventana**,
      y la comprobación de §2.2 sin filas *(4B)*.
- [ ] `db/boveda/0011_fase4_inteligencia.sql` aplicada *(4B)*.
- [ ] `db/semantica/0004_series.sql` aplicada *(4B)*.
- [ ] `firebase deploy` completo.
- [ ] `VERIFICACION_SAL` configurada (no es opcional en producción).
- [ ] `VERIFICACION_ENVIO_PROVEEDOR` apuntando a un proveedor real —**sin
      esto la landing no se puede anunciar**.
- [ ] `DESAFIO_PROVEEDOR` y `DESAFIO_SECRETO`, o la decisión explícita de
      dejar la landing sin desafío.
- [ ] `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` y `WHATSAPP_WABA_ID` en
      Secret Manager.
- [ ] Cuenta de WhatsApp Business verificada, Flow publicado y plantilla
      aprobada.

Definiciones pendientes:

- [ ] **Opt-in de WhatsApp de los panelistas ya enrolados** (§1.1): sin eso no
      se les puede mandar, y activarlo «porque tenemos su celular» es lo que
      quema el número.
- [ ] **Transferencia de celulares a Meta** revisada con el DPO (§1.2).
- [ ] Texto del opt-in de WhatsApp definido para las tres vías (§1.3).
- [ ] Costo por conversación estimado para una ola típica.

Avisos al equipo:

- [ ] **Que la composición de una ola pasada ahora se puede pedir a fecha, y
      que el número que daba antes estaba mal.** Es el aviso más importante
      de 4B: quien haya reportado una composición retroactiva desde que existe
      el sistema la reportó con la demografía de hoy.
- [ ] Que la brecha de una composición retroactiva se calcula contra el
      universo de referencia de **hoy**: los objetivos no se historizan, y la
      respuesta lo dice.
- [ ] Que hace falta un dueño del vocabulario de series, por el mismo motivo
      que con los segmentadores: sin responsable, cada equipo arma las suyas y
      las comparaciones dejan de ser comparables entre equipos.
- [ ] Que los pesos del optimizador tienen defaults **documentados pero no
      medidos** contra los datos de Equipos, igual que los umbrales de fatiga.
- [ ] Que la landing ahora pide verificación, y qué pasa si el proveedor no
      está configurado.
- [ ] Que los panelistas existentes no son contactables por WhatsApp hasta
      que den su opt-in.
- [ ] Que la plantilla de Meta demora en aprobarse: no se configura la
      encuesta y se convoca el mismo día.
