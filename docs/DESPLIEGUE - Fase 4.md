# Despliegue — Fase 4 · Bloque 4A (Contacto)

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Cubre:** R4.3 (endurecimiento de la landing), R4.4 (preferencias de canal),
R4.5 (envío por WhatsApp Flow)
**Precondición:** Fase 3 desplegada, con las migraciones 0005 a 0008 aplicadas

---

## 0 · Qué cambia

Este bloque cierra la puerta de entrada y abre un canal de salida.

| Qué | Dónde | Riesgo si se saltea |
|---|---|---|
| Una migración nueva en la bóveda | `db/boveda/0009_fase4_contacto.sql` | La landing, la ficha y la pantalla de encuestas fallan con `relation … does not exist` |
| **La landing deja de aceptar envíos sin verificar** | R4.3 | Nada se rompe, pero **nadie más se puede inscribir** hasta que haya proveedor de códigos. Es el cambio con más consecuencia operativa del bloque |
| Un eje de permiso nuevo para contactar | R4.4 | Los panelistas existentes quedan **sin preferencias**, o sea no contactables por ningún canal, hasta que se registren |
| Credenciales de Meta y de un proveedor de códigos | ver §3 | Sin ellas se puede configurar todo pero no enviar nada, y la landing no verifica de verdad |
| Nada en el store semántico | — | — |

**El store semántico no cambia.** Ninguna migración del lado semántico y el
pipeline de consulta queda exactamente igual.

> **Lo que este bloque deliberadamente no hace.** El sistema **solo envía** por
> WhatsApp: no recibe respuestas, no procesa webhooks y no crea Flows ni
> plantillas. El analista baja las respuestas de Meta y las ingesta por el
> flujo de siempre, con el `flow_token` mapeando directo. WhatsApp es un canal
> de envío, no una plataforma de recolección.

---

## 1 · Antes de empezar: las tres definiciones legales

Ninguna bloquea aplicar la migración. Las tres bloquean **usar** lo que
habilita.

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

## 2 · La migración

Una sola, y solo en la bóveda.

```bash
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0009_fase4_contacto.sql
```

**Es aditiva y se puede aplicar con la app andando:** crea dos tablas y agrega
columnas nullables (o con default) a tres. No reescribe ninguna fila
existente ni toma locks largos.

### 2.1 · Qué crea

| Objeto | Para qué |
|---|---|
| `preferencia_canal` | Por qué canal acepta cada persona que la contacten, con el texto con que lo aceptó y de qué camino de alta vino (R4.4) |
| `verificacion_contacto` | Los códigos de un solo uso que endurecen la landing. El código va **hasheado** y la IP también (R4.3) |
| `inscripcion.celular_verificado` / `email_verificado` | Qué verificó cada inscripción. `false` en las anteriores a esta migración, que no pasaron por verificación |
| `inscripcion.canales` | Los canales que el titular aceptó de primera mano en el formulario. Se convierten en preferencias al aprobar |
| `encuesta.flow_id` / `flow_plantilla` / `flow_idioma` | Configurar una encuesta como WhatsApp Flow (R4.5) |
| `participacion.enviado_en` / `envio_estado` / `envio_error` | El estado del envío por persona, para reintentar solo los fallidos |

### 2.2 · Verificar

```bash
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
python3 scripts/verificar_esquema.py
```

Tienen que salir las ocho migraciones de la bóveda y las tres de la semántica,
todas con tilde.

---

## 3 · Las credenciales

Tres bloques, y **ninguno va al repositorio**. En producción, Secret Manager.

### 3.1 · WhatsApp Business (R4.5)

```
WHATSAPP_TOKEN            token de acceso de la app de Meta
WHATSAPP_PHONE_NUMBER_ID  el número emisor
WHATSAPP_WABA_ID          la cuenta de WhatsApp Business
```

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

> **Sin proveedor la landing no verifica nada.** El código vuelve en la
> respuesta del propio pedido, o sea que quien lo pide lo recibe sin
> necesidad de tener acceso al contacto. El sistema lo dice con todas las
> letras —en la respuesta y en el diagnóstico de Cumplimiento— pero **la
> landing no se puede anunciar así**.

`VERIFICACION_SAL` no es opcional en producción: sin ella el hash de un código
de seis dígitos se revierte con una tabla de un millón de entradas, y el de
una IP también.

Los proveedores reales de SMS y de correo se agregan en
`verificacion_contacto.proveedor_de_envio`, que es una función que devuelve
`enviar(canal, destino, codigo)`. El patrón es el mismo de los embeddings y el
reranker.

### 3.3 · Desafío anti-automatización (R4.3)

```
DESAFIO_PROVEEDOR   'ninguno' (default) | 'turnstile' | 'recaptcha'
DESAFIO_SECRETO     el secreto del lado servidor
```

Sin configurar, la landing no distingue un envío automatizado de una persona.
No bloquea el despliegue, pero está en el checklist: es una decisión que
alguien tiene que tomar a sabiendas.

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

- [ ] `db/boveda/0009_fase4_contacto.sql` aplicada.
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

- [ ] Que la landing ahora pide verificación, y qué pasa si el proveedor no
      está configurado.
- [ ] Que los panelistas existentes no son contactables por WhatsApp hasta
      que den su opt-in.
- [ ] Que la plantilla de Meta demora en aprobarse: no se configura la
      encuesta y se convoca el mismo día.
