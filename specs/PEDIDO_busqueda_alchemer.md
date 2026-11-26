# PEDIDO — Búsqueda y enlaces de encuestas de Alchemer

**Sistema:** Gestión de paneles · vincular una encuesta de Alchemer (R-ALC.2)
**Severidad:** Alta — la funcionalidad **no se puede usar** con la cuenta real
**Detectado:** 2026-11-25, al terminar el despliegue
**Entrega:** una sola versión

---

## 1. Qué pasa (medido)

Al apretar «Buscar encuesta de Alchemer», la pantalla queda girando y nunca
muestra resultados.

Prueba directa contra la API con las credenciales de producción:

```
GET /v5/survey?resultsperpage=100&page=1
HTTP 200 · 7,8 s
2.172 encuestas · 22 páginas
```

`alchemer.buscar_encuestas` **baja la lista completa** —de a 100, hasta
`PAGINAS_MAXIMAS = 10` páginas, con un timeout de 20 s por página— y **recién
después filtra** por el título. Dos problemas:

1. **Se pasa del tiempo límite.** Diez páginas a ~8 s son ~80 s. Firebase Hosting
   corta todo pedido a los **60 segundos**: el navegador queda esperando una
   respuesta que no llega, aunque la función siga trabajando.
2. **Aunque terminara, la búsqueda está incompleta.** Diez páginas son 1.000 de
   2.172 encuestas. Si la API las devuelve de la más vieja a la más nueva, **las
   encuestas recientes —las que se quieren vincular— nunca se buscan**.

Y la pantalla no maneja el error: ante un corte, gira para siempre.

## 2. Qué se pide

### R-ALB.1 — Vincular pegando la URL o el id (P0)

La forma más rápida y que no depende del tamaño de la cuenta.

- Dado el formulario de la encuesta, entonces se puede **pegar la URL de la
  encuesta de Alchemer** (la de edición o la del enlace público) **o su id**.
- Dado ese dato, entonces se extrae el id y se consulta **esa sola encuesta**
  (`GET /survey/{id}`): una llamada, unos segundos.
- Dada la encuesta encontrada, entonces sigue el flujo de siempre: nombre,
  enlaces, verificación del parámetro, cuestionario.

### R-ALB.2 — Catálogo local de encuestas (P0)

Para buscar por título sin recorrer la API en cada búsqueda.

- Dado el catálogo, entonces guarda de cada encuesta: id, título, estado y fecha
  de modificación. No guarda el cuestionario ni respuestas.
- Dada la **primera sincronización**, entonces corre **en segundo plano** (Cloud
  Tasks) y recorre **todas** las páginas, sin tope de 10, con progreso visible.
  Con la cuenta actual son ~22 llamadas y unos 3 minutos.
- Dadas las siguientes, entonces son **incrementales**: traen solo lo modificado
  desde la última, si la API permite filtrar por fecha de modificación; si no,
  vuelven a recorrer todo, también en segundo plano.
- Dada una búsqueda, entonces se resuelve **contra el catálogo**, al instante, sin
  llamar a Alchemer.
- [ ] Por defecto la búsqueda muestra las encuestas **activas** y las más
      recientes primero; con un filtro, todas.
- [ ] Se ve cuándo se sincronizó por última vez, con un botón «Actualizar».
- [ ] Una encuesta vinculada se refresca individualmente al vincularla, aunque el
      catálogo esté desactualizado.

### R-ALB.3 — La pantalla nunca gira para siempre (P0)

- [ ] Todo pedido a Alchemer desde la pantalla tiene un tiempo máximo, y si se
      cumple muestra el motivo («Alchemer tardó demasiado en responder») con la
      opción de reintentar o de pegar la URL.
- [ ] Ningún pedido de la pantalla hace más de una llamada a la API de Alchemer
      salvo la sincronización, que corre en segundo plano.

### R-ALB.4 — Filtrar en Alchemer si la API lo permite (P1)

- [ ] Si la API v5 admite filtrar la lista de encuestas por estado o por fecha de
      modificación, usarlo en la sincronización incremental. Verificarlo con la
      documentación vigente antes de depender de eso.

### R-ALB.5 — Los enlaces de la encuesta (P0)

**Qué pasa (medido).** Al elegir la encuesta 9105516, que tiene **7 enlaces
activos** en Alchemer, paneles dice *«La encuesta no tiene enlaces publicados»*.
La API (`GET /v5/survey/{id}/surveycampaign`) devuelve cada enlace así:

```json
{
  "id": "26582567",
  "type": "SurveyCampaign",
  "link_type": "link",
  "subtype": "standard",
  "status": "Active",
  "name": "Default Link",
  "uri": "survey.alchemer.com/s3/9105516/AVANTI-2026-S00726-Evaluaci-n-de-lanzamientos",
  "SSL": "True",
  "link_open_date": null,
  "link_close_date": null
}
```

Dos errores en `alchemer.enlaces`:

1. **Filtra por el campo equivocado.** Exige `type == "link"`, pero `type` es el
   tipo del **objeto** (`SurveyCampaign`) en todas las campañas. El tipo de enlace
   está en **`link_type`**. Resultado: descarta los siete.
2. **La URL viene sin esquema.** `uri` no trae `https://`; el protocolo se deduce
   de `SSL`. Sin agregarlo, la URL no pasa la validación de paneles (debe empezar
   con `https://`).

**Qué se pide:**

- [ ] Filtrar por **`link_type == "link"`** (o, si falta, por `type`, para no
      depender de una sola forma de la respuesta).
- [ ] Armar la URL con **`https://`** si `SSL` es verdadero (y `http://` si no),
      y recién después agregar el parámetro. Si la URL ya trae una consulta
      (`?…`), el parámetro se agrega con `&`, no con otro `?`.
- [ ] Quitar espacios del nombre del enlace (hay uno que se llama «Panel »).
- [ ] El **selector de enlace se muestra siempre**, aunque haya uno solo, con su
      **nombre, estado y URL** a la vista. Hoy se oculta con menos de dos, y el
      enlace se elige en silencio.
- [ ] Por defecto se listan los enlaces **activos**; los cerrados, con un filtro.
- [ ] Se guarda **qué enlace** (id de la campaña) se eligió, además de la URL.
- [ ] Se puede **cambiar de enlace** después de vincular, desde la ficha de la
      encuesta: es normal crear enlaces nuevos en Alchemer a medida que avanza el
      campo.
- [ ] Al cambiar de enlace en una encuesta con mensajes de WhatsApp, avisar que
      sus plantillas apuntan a la URL anterior y van a quedar bloqueadas hasta
      revisarlas (R-CTL.1). Cada enlace tiene una URL distinta —algunos en
      `survey.alchemer.com`, otros en el dominio propio
      `encuestas.equipos.com.uy`— y la plantilla se aprueba con una sola.

## 3. Definition of Done

- [ ] Pegando la URL o el id de una encuesta, se vincula en segundos (test).
- [ ] La primera sincronización recorre las 2.172 encuestas, en segundo plano
      (test con la API simulada en 22 páginas).
- [ ] Una búsqueda por título responde al instante desde el catálogo e incluye
      encuestas de las últimas páginas de la API (test).
- [ ] Un corte de tiempo muestra un error con opciones, no un indicador girando
      (test).
- [ ] Con la respuesta real de la encuesta 9105516, se listan los 7 enlaces con
      su URL completa en `https://` (test con esa respuesta como fixture).
- [ ] Con un solo enlace, el selector se ve igual (test).
- [ ] Se puede cambiar de enlace después de vincular, y se avisa si hay
      plantillas de WhatsApp que apuntan al anterior (test).

## 4. Costos

**US$ 0.** El catálogo es una tabla chica —unas 2.000 filas de texto—. La
sincronización son ~22 llamadas a la API de Alchemer, incluidas en el plan, y
corre en segundo plano dentro de la capa gratuita.

## 5. Despliegue

Sigue las reglas del `CLAUDE.md` del repositorio: proyecto de gcloud fijado y
verificado al empezar; migración aplicada y `verificar_esquema.py` en verde
**antes** del `firebase deploy`; token de COLOQUIO regenerado inmediatamente
antes de la batería. Después del despliegue, lanzar la **primera
sincronización** y esperar a que termine antes de probar la búsqueda.
