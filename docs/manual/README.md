# Manual de usuario

`Manual_de_usuario.pdf` es el entregable: paso a paso de cada tarea, con
capturas de la aplicación real. Cubre las **fases 1 y 2**, tres cosas de la
fase 3 —la ingesta de archivos `.sav` con alta de panelistas (R3.9), la carga
de individuos sin panel (R3.13) y el catálogo de atributos demográficos
(R3.14)— y la **fase 4** completa: el bloque de contacto (canales, formulario
público endurecido y envío por WhatsApp; R4.3, R4.4, R4.5) y el de
inteligencia (series comparables entre olas, evolución, la historia de una
persona y el optimizador de muestreo; R4.1, R4.2).

La sección de **Muestreo** se escribió recién ahora, con el optimizador: la
pantalla existe desde la fase 3 y el manual nunca la había cubierto. La parte
de reglas se documenta breve, porque lo que se agregó en esta fase es el
optimizador.

> **Ámbitos de composición, origen de cada persona, consultas demográficas
> y acceso al portal · 2026-10-08.** La 3.5 suma la tarjeta **Origen** (de
> qué estudios proviene la persona, creada o reutilizada, y qué significa
> «no hay registro»); la 3.6, los **datos del estudio** al cargar (fecha y
> público objetivo), el **filtro por carga** del listado y **Datos del
> estudio** para corregirlos. La 6.3 suma la columna **Estudio de origen** y
> la **6.10, Consultas solo demográficas**, es nueva: la misma barra de
> acciones que el ranking y el resultado seudónimo. La 7 explica el
> desplegable **Ámbito** y la **7.5** es nueva (todos los panelistas, con su
> propio universo de referencia, y una carga, siempre descriptiva). La 13
> distingue «correo o contraseña incorrectos» de **«no pudimos comprobar tu
> contraseña por un problema técnico»**; la 14 suma **Estudios de origen**.
> Cuatro preguntas frecuentes y tres términos de glosario nuevos.
> `MANUAL_panelista.md` explica el mensaje técnico del portal.
>
> **Capturas nuevas, de la 77 a la 87, y recapturadas la 02, 37, 46 y 47**
> (las pantallas cambiaron: el filtro por carga, el formulario de carga y el
> ámbito de composición). Con el método de la 62: un servidor mínimo que
> sirve `web/public` y despacha `/api/**` a `ruteo.despachar` con un actor
> administrador, contra un Postgres de pruebas sembrado con un panel, dos
> cargas con sus estudios y personas creadas y reutilizadas.

> **Correo por Workspace y verificación por lotes · 2026-10-05.** La 6.3
> suma el veredicto **Sin verificar** y la marca **verificación
> incompleta**; la 6.4, el apartado «Si la verificación quedó incompleta»
> (qué es, en qué se diferencia de *dudoso*, qué hacer); la 6.9, la tabla
> **Verificación por lotes** del diagnóstico y «Ver el intercambio con el
> verificador» (solo administradores, con el modo de depuración). La 11 suma
> la **11.4, El envío de correos**: el estado del envío en Cumplimiento →
> Contacto, el **correo de prueba** y los **envíos fallidos**. La 3.8 deja de
> decir que sin proveedor el código se muestra en pantalla —ya no se muestra
> nunca— y avisa que el celular todavía no se puede verificar; la 12.1 dice
> que el enlace de un usuario nuevo además llega por correo; la 13 explica de
> dónde salen los correos del portal y los dos avisos de envío. Dos preguntas
> frecuentes y cuatro términos de glosario nuevos. `MANUAL_panelista.md`
> dice el remitente y que el celular nuevo todavía no se confirma desde el
> portal.
>
> **Capturas nuevas, de la 72 a la 76**: el aviso de verificación
> incompleta, el ranking con una respuesta sin verificar, la tabla de lotes
> del diagnóstico, el intercambio con Claude y la tarjeta de envío de
> correos. Se tomaron con el método de la 62 a la 71 —servidor mínimo contra
> `ruteo.despachar` y un Postgres de pruebas, con Firebase Auth simulado en
> Playwright—, con un verificador `Claude` cuyo transporte simula una tanda
> truncada y una con error 529, y un SMTP simulado con una dirección que
> rebota. Las 22 y 28 siguen siendo las anteriores.

> **Fase 7 completa y Fase 8 · 2026-10-05.** La sección 5 suma la **5.8,
> La calidad del dato** (lo que el sistema detecta antes de ingestar y la
> vista previa del texto embebido) y la **5.9, Corregir y reprocesar un
> estudio ya cargado**, sin volver a subir el archivo. La 5.4 explica lo que
> el resumen de revisión agrega (descartes, respuestas que genera cada
> variable, texto embebido). La 3.5 suma las respuestas procesadas y
> «Otorgar finalidad» con el desplegable de versión; la 6.3, la evidencia en
> la ficha desde un resultado y «Ver quién es»; la 14, el botón
> «Reprocesar» de las últimas cargas. Hay tres preguntas frecuentes y cuatro
> términos de glosario nuevos.
>
> **Esta vez sí hay capturas nuevas: de la 62 a la 71.** No salen del modo
> demo —`demo.js` no simula las rutas de las fases 7 y 8— sino de la
> aplicación real servida contra el ruteo de verdad y un Postgres de
> pruebas, con un `.sav` de ejemplo que copia la estructura de la primera
> carga real (una batería `var138O132x`, una cerrada con un código sin
> traducir, su «Otro» con un teléfono). Es el mismo método que la 51: un
> servidor mínimo que sirve `web/public` y despacha `/api/**` a
> `ruteo.despachar` con un actor administrador, y Playwright recorriendo las
> pantallas. La 62 y la 67 se recortaron para que entren en una página.

> **Fase 7 · 2026-10-04.** Cuatro cambios de pantalla y una sección nueva.
> La **14, Estadísticas de la base**, es nueva —y corre la numeración de
> Preguntas frecuentes a 15 y del Glosario a 16—. La 5.4 suma el **paso de
> revisión antes de importar**, con el recuadro de claves de deduplicación
> que es lo que detecta un marcado equivocado. La 5.5 explica que la
> **versión del consentimiento se elige de una lista** y ya no se escribe. Y
> la 6.3 suma las **columnas elegibles del ranking** y la **ficha del
> panelista** con sus respuestas procesadas.
>
> Es texto: no hizo falta recapturar ninguna pantalla. Las capturas de las
> pantallas nuevas quedan **pendientes** —la sección 14 y el paso de
> revisión se describen sin imagen—, y conviene tomarlas la próxima vez que
> se recorra la copia en modo demo.

> **Revisión 2026-10-04.** La sección 5.5 suma «Revisá qué variable
> marcaste como documento»: el sistema ahora rechaza la carga si la variable
> marcada como documento trae muy pocos valores distintos, porque el dedup
> resuelve primero por documento y un marcado equivocado fusionaría el
> archivo entero en dos fichas. Es texto: no hizo falta recapturar ninguna
> pantalla.

> **Revisión 2026-10-03.** La sección 5.4 suma «Al confirmar, la carga queda
> en proceso» y «Si algún lote no entra»: la ingesta pasó a diferido, así que
> confirmar ya no cuelga la pantalla, se puede cerrar la pestaña y un lote
> fallido se reintenta solo. La 3.6 y la 5.5 remiten a eso, y hay dos
> preguntas frecuentes nuevas. Es texto: no hizo falta recapturar ninguna
> pantalla.

> **Revisión 2026-10-02.** La sección 13 (el portal del panelista) pasa del
> ingreso por enlace al **ingreso con contraseña** (R6.1.a): cómo se crea la
> primera vez, cómo se reenvía el enlace desde la ficha de un panelista, y
> las tres acciones que piden la contraseña otra vez aunque la sesión esté
> abierta. Es texto: no hizo falta recapturar ninguna pantalla.

> **Revisión 2026-10-01.** La sección de ingesta suma «Qué significa cada
> código del archivo»: el mapeo valor por valor de una variable marcada como
> atributo categórico, el aviso previo de lo que quedó sin mapear y qué pasa
> con esos valores (R-MAP). Es texto: no hizo falta recapturar ninguna
> pantalla, solo regenerar el PDF.

## Cómo se rehace

Las capturas se toman recorriendo la app de verdad en **modo demo**, que es el
que trae datos sembrados. No hay mockups: lo que se ve en el manual es lo que
hace la aplicación.

```bash
npm install playwright-core pdf-lib
pip install Pillow
```

### 1 · Preparar y servir la copia en modo demo

```bash
docs/manual/preparar_demo.sh          # prepara /tmp/demo-manual y lo sirve en :8099
```

El script hace cinco retoques sobre la copia —ninguno sobre el repo— y los
explica en su encabezado. Los dos que importan entender:

* Se ocultan el banner y el aviso de degradación de **modo demo**. El manual
  documenta la aplicación de producción, y esos dos carteles existen solo
  porque la copia no tiene backend.
* Se ponen los nombres de proveedor que informa producción (`voyage`,
  `claude`) en el diagnóstico de la consulta, en vez de los de la copia.

Los tiempos, los puntajes y los datos siguen siendo los de la copia: el manual
lo dice en su primera página.

Para apagarlo: `docs/manual/preparar_demo.sh detener`.

### 2 · Capturar

```bash
node docs/manual/capturar.mjs docs/manual/capturas
```

Recorre login, alta, deduplicación, revisión de altas, paneles, encuestas,
convocatoria, ingesta, cruce entre stores, cumplimiento, **consultas
semánticas, composición, participación y gestión de usuarios**. Son 44
capturas.

La 45 —la declaración de evidencia de consentimiento al importar un `.sav`—
se toma aparte: la copia en modo demo no parsea SPSS, así que hay que
darle una respuesta de ejemplo al endpoint `POST /encuestas/:id/sav/analizar`
para llegar a ese formulario.

Las 46 y 47 —el filtro «sin panel» y el inicio de una carga de panelistas
(R3.13)— y las 48 a 50 —el catálogo de atributos, el alta de uno y la tarjeta
de atributos de la ficha (R3.14)— también se toman aparte, con el mismo
recorrido que `capturar.mjs`: se agregaron después y no hacía falta rehacer
las 44 para sumarlas.

Las 51 a 53 y la 60 son del bloque 4A. La 51 —el formulario público con la
verificación— **no se puede tomar de la copia en modo demo**: la landing habla
con la API real y `demo.js` no la cubre. Se toma levantando un servidor mínimo
contra el ruteo real y un Postgres de pruebas.

La 61 —el enlace del formulario público con su estado— sale de la pantalla de
Inscripciones en la copia demo. Desde este cambio la copia sirve el formulario
también en `inscribirse/index.html`, para que el botón «Abrir» no dé 404:
`python3 -m http.server` no hace los rewrites de Firebase Hosting, que es de
donde sale `/inscribirse` en producción.

La 43b —el enlace de acceso generado de nuevo desde el padrón— sale del mismo
recorrido de `capturar.mjs`, que ya la toma. Cuando se agregó hubo que rehacer
también la 41, la 43 y la 44: la fila del padrón tiene un botón más y el texto
del modal cambió, así que las viejas mostraban una pantalla que ya no existe.

Las 54 a 59 son del bloque 4B: las sugerencias de una serie, la serie con sus
dos olas mapeadas, la matriz de evolución, la línea de tiempo de una persona,
el optimizador con su comparación contra las reglas y las tres alternativas
ante una cuota infactible. Se toman de la copia en modo demo, **armando la
serie desde la pantalla**: crear la serie, agregar la primera pregunta a mano,
mapear sus opciones, buscar candidatas y aceptar la equivalente de la otra ola.
La demo trae una cuarta ola del mismo panel que vuelve a preguntar lo mismo con
otra redacción y otras opciones, que es lo que hace demostrable todo el
bloque.

### 3 · Optimizar

Salen en PNG a 2× para que se lean nítidas; en el PDF entran a ~1×, así que
conviene pasarlas a JPEG:

```bash
python3 - <<'PY'
import pathlib
from PIL import Image
d = pathlib.Path('docs/manual/capturas')
for png in sorted(d.glob('*.png')):
    img = Image.open(png).convert('RGB')
    if img.width > 1700:
        img = img.resize((1700, round(img.height * 1700 / img.width)), Image.LANCZOS)
    img.save(png.with_suffix('.jpg'), 'JPEG', quality=88, optimize=True, progressive=True)
    png.unlink()
PY
```

### 4 · Generar el PDF

```bash
node docs/manual/generar_pdf.mjs
```

## Qué hay en esta carpeta

| Archivo | Qué es |
|---|---|
| `Manual_de_usuario.pdf` | El entregable |
| `portada.html` | Portada, a sangre |
| `manual.html` | Índice y las catorce secciones |
| `estilo.css` | Estilos, compartidos por los dos |
| `capturas/` | Las capturas, de la 01 a la 87 |
| `tipografia/` | Montserrat local, para que el PDF salga igual sin red |
| `preparar_demo.sh` | Arma y sirve la copia en modo demo |
| `capturar.mjs` | Toma las capturas |
| `generar_pdf.mjs` | Maqueta el PDF |

Portada y cuerpo se imprimen por separado —la portada va a sangre, el cuerpo con
márgenes y pie de página— y se pegan al final: Chromium aplica una sola
configuración de página por impresión.

## Detalles que el script resuelve y conviene no deshacer

* **El idioma del navegador.** El formato de `<input type="date">` no lo decide
  el `locale` del contexto de Playwright ni el flag `--lang`: lo decide el
  idioma de la interfaz del navegador, que sale del entorno del proceso. Por eso
  `capturar.mjs` lanza Chromium con `LANG=es_UY.UTF-8`. Sin eso las fechas salen
  en formato de EE. UU.
* **La ventana es alta (1280 × 1180).** Los modales se cortan solos en 88 vh y
  con una ventana baja las capturas de los más largos —la ingesta, el universo
  de referencia— salen truncadas.
* **Los toasts se limpian antes de cada captura.** Duran casi cuatro segundos y
  se apilan; en la captura del paso siguiente aparecen como un cartel colgado
  que no viene al caso. Las dos capturas que sí son de un toast lo piden
  expresamente.
* **Se quita el foco antes de disparar.** Un campo enfocado sale con el borde
  naranja y, si es de fecha, con un tramo seleccionado en azul.
