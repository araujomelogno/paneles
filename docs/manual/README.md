# Manual de usuario

`Manual_de_usuario.pdf` es el entregable: 32 páginas, paso a paso de cada tarea,
con capturas de la aplicación real.

## Cómo se rehace

Las capturas se toman recorriendo la app de verdad en **modo demo**, que es el
que trae datos sembrados. No hay mockups: lo que se ve en el manual es lo que
hace la aplicación.

```bash
npm install playwright-core pdf-lib
pip install Pillow
```

### 1 · Preparar una copia en modo demo

```bash
cp -r web/public /tmp/demo
# dejar apiKey en "TU_API_KEY" para que arranque en modo demo
sed -i 's/apiKey: *"[^"]*"/apiKey: "TU_API_KEY"/' /tmp/demo/index.html
# ocultar el banner de demo: el manual documenta la app de producción
sed -i 's/const banner = api.estado.demo ?/const banner = false ?/' /tmp/demo/js/app.js
cd /tmp/demo && python3 -m http.server 8099
```

### 2 · Capturar

```bash
node docs/manual/capturar.mjs docs/manual/capturas
```

Recorre login, alta, deduplicación, revisión de altas, paneles, encuestas,
convocatoria, ingesta, cruce entre stores y cumplimiento. Son 23 capturas.

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
| `manual.html` | Índice y las ocho secciones |
| `estilo.css` | Estilos, compartidos por los dos |
| `capturas/` | Las 23 capturas |
| `tipografia/` | Montserrat local, para que el PDF salga igual sin red |
| `capturar.mjs` | Toma las capturas |
| `generar_pdf.mjs` | Maqueta el PDF |

Portada y cuerpo se imprimen por separado —la portada va a sangre, el cuerpo con
márgenes y pie de página— y se pegan al final: Chromium aplica una sola
configuración de página por impresión.
