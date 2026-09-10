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

## Una limitación conocida

Los campos de fecha (`<input type="date">`) salen en formato de EE.UU. en las
capturas. El Chromium headless ignora tanto el `locale` del contexto como
`--lang`, y no hay forma de forzarlo desde el script. En un navegador normal, en
Uruguay, se ven en dd/mm/aaaa. Está aclarado en el pie de la primera figura
donde aparece un campo de fecha.
