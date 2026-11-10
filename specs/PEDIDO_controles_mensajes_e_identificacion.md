# PEDIDO — Controles de los mensajes y de la identificación al cargar

**Sistema:** Gestión de paneles · encuestas, mensajes y carga de respuestas
**Severidad:** Alta R-CTL.1, R-CTL.2 y R-CTL.4; media R-CTL.3
**Detectado:** 2026-11-06, al revisar el código de la versión desplegada
**Se apoya en:** el pedido «Encuestas: mensajes, canales, selección aleatoria,
audiencias y composición», ya desplegado
**Entrega:** una sola versión

---

Cuatro huecos encontrados al revisar el código, todos del mismo tipo: algo que
se valida en un momento y después puede dejar de ser cierto, o que falla sin
decir por qué.

## R-CTL.1 — Volver a comparar la URL en cada envío (P0)

**Qué pasa.** La URL del botón de la plantilla se compara con la URL de Alchemer
de la encuesta **solo al guardar el mensaje** (`mensajes.py`). Al enviar, el
sistema vuelve a pedir la plantilla aprobada solo para saber la posición del
botón, pero **no repite la comparación**. Si después de guardar el mensaje
alguien cambia la URL de la encuesta o cambia la plantilla en Meta, WhatsApp
manda a donde diga la plantilla, sin aviso. Y el correo, que sí arma el enlace
con la URL de la encuesta, iría a otro lado: **dos personas de la misma encuesta
irían a lugares distintos según el canal**.

- [ ] En la **vista previa de cada envío**, comparar la base del botón de la
      plantilla **vigente** con la URL de Alchemer **vigente** de la encuesta.
- [ ] Si no coinciden, **bloquear** el envío con las dos URL lado a lado. La
      confirmación que hoy permite guardar con una URL distinta no alcanza para
      enviar: si difieren al enviar, algo cambió después.
- [ ] Al editar la URL de Alchemer de una encuesta que ya tiene mensajes de
      WhatsApp, avisar que esos mensajes van a quedar bloqueados hasta revisar sus
      plantillas.
- [ ] En todos los avisos de URL distinta, mostrar **las dos URL completas** una
      debajo de la otra: la comparación es exacta, y una diferencia de `http` /
      `https` o de una barra final tiene que verse a simple vista.

## R-CTL.2 — Encabezado de la plantilla (P0)

**Qué pasa.** El sistema cuenta solo las variables del **cuerpo**. Si la plantilla
tiene una variable en el **encabezado** —un texto con `{{1}}`— o un encabezado de
**imagen, video o documento**, el mensaje se guarda sin problema y después
**todos los envíos fallan** en Meta, porque el sistema no manda ese dato.

- [ ] Al guardar un mensaje, detectar el encabezado de la plantilla. Si tiene
      variable o archivo, **no guardar** y explicar por qué: «esta plantilla tiene
      un encabezado con [texto variable / imagen / …], que todavía no se puede
      completar desde el sistema».
- [ ] Lo mismo en la vista previa de cada envío, por si la plantilla cambió en
      Meta.

## R-CTL.3 — Fuente «texto fijo» para los parámetros del cuerpo (P1)

**Qué pasa.** Cada parámetro del cuerpo tiene que salir de una fuente, y hay solo
dos: el nombre del panelista y el de la encuesta. Una plantilla que necesite otro
dato —una fecha límite, los puntos que se ganan— no se puede usar.

- [ ] Agregar la fuente **«texto fijo»**: el valor se escribe al crear el mensaje
      («hasta el viernes 14», «ganás 200 puntos») y es igual para todos.
- [ ] **No** agregar el nombre del **cliente** como fuente: es confidencial, y el
      respondente normalmente no debe saber para quién es el estudio (R-CLI.4).

## R-CTL.4 — El tipo de identificador al cargar respuestas (P0)

**Qué pasa.** Al cargar respuestas de Alchemer, la columna trae el `id_persona`,
pero el tipo por defecto es «Trae el id de la plataforma de campo». Con ese tipo,
el sistema busca el valor como identificador registrado de Alchemer, no lo
encuentra, y el aviso de no convocados dice que **no pudo identificar** a
personas que existen. Además, cambiar el tipo en el selector **no recalcula** el
aviso: solo cambia el texto de ayuda.

- [ ] Si los valores de la columna elegida tienen **forma de `id_persona`**
      (UUID), **proponer** el tipo «Trae el id_persona del sistema».
- [ ] Al cambiar el tipo, **recalcular** el resumen o marcarlo como
      **desactualizado** hasta volver a revisar, sin dejar confirmar con un
      resumen viejo.
- [ ] Los avisos dicen **con qué tipo** se intentó identificar: «2 no se pudieron
      identificar **como id de la plataforma de campo**».

**Criterios de aceptación**
- Given una columna cuyos valores son UUID, When se elige como identificadora,
  Then el tipo propuesto es «id_persona del sistema».
- Given un resumen calculado con un tipo, When se cambia el tipo, Then el resumen
  queda marcado como desactualizado y no se puede confirmar la carga hasta
  revisarlo de nuevo.

---

## Definition of Done

- [ ] Un envío se bloquea si la URL del botón vigente no coincide con la URL de
      Alchemer vigente, mostrando las dos (test).
- [ ] Una plantilla con encabezado variable o de archivo no se puede guardar como
      mensaje, con el motivo explicado (test).
- [ ] La fuente «texto fijo» completa un parámetro del cuerpo (test).
- [ ] Una columna con UUID propone el tipo id_persona; cambiar el tipo deja el
      resumen desactualizado hasta revisar de nuevo (test).

## Costos

**US$ 0.** Son validaciones y ajustes de interfaz. Evitan costo: hoy un encabezado
no soportado hace fallar cada envío —y cada intento fallido igual se intenta—, y
una URL distinta manda a la gente a otra encuesta.

## Despliegue

Sigue las reglas del `CLAUDE.md` del repositorio: proyecto de gcloud fijado y
verificado al empezar; si hay migración, aplicada y `verificar_esquema.py` en
verde **antes** del `firebase deploy`; token de COLOQUIO regenerado
inmediatamente antes de la batería. No incluye dar de alta ningún webhook en Meta.
