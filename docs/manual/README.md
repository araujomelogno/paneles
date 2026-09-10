# Manual de usuario

`Manual_de_usuario.pdf` es el entregable: 57 páginas, paso a paso de cada tarea,
con capturas de la aplicación real. Cubre las **fases 1 y 2**.

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
| `manual.html` | Índice y las doce secciones |
| `estilo.css` | Estilos, compartidos por los dos |
| `capturas/` | Las 44 capturas |
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
