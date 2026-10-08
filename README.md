# Paquete de handoff — Sistema de gestión de paneles + consulta semántica

Este repositorio contiene todo lo necesario para que Claude Code arranque el desarrollo.
Empezá leyendo `CLAUDE.md`, después el PRD, y desarrollá por fases empezando por `HANDOFF_fase1.md`.

## Documentos (set definitivo)

| Archivo | Qué es | Destino en el repo |
|---|---|---|
| `CLAUDE.md` | Contexto persistente: invariantes (dos stores, PII nunca al store semántico), identidad, reglas de negocio, stack. **Léelo primero y siempre.** | raíz `/` |
| `PRD_gestion_de_paneles_detallado.md` | PRD del sistema: problema, objetivos, no-objetivos, arquitectura, y las 4 fases con requisitos (R1.x…R4.x), criterios de aceptación y DoD. Documento **autoritativo** de producto. | `docs/` |
| `PRD_consulta_semantica_cuestionarios.md` | Spec del **módulo de consulta semántica** (mecánica interna del motor: modelo vectorial, ingesta, embeddings, ranking, verificación con Claude). Es un módulo de este sistema, no un producto aparte. | `docs/` |
| `DESPLIEGUE -  Fase 1 .md` | Manual de despliegue de la Fase 1: las dos instancias de Cloud SQL, el conector de VPC, los secretos, la ingesta, verificación y problemas frecuentes. | `docs/` |
| `DESPLIEGUE - Fase 2.md` | Manual de despliegue de la Fase 2: migraciones nuevas, claves de reranking y de Claude, permisos de la cuenta de servicio, índice vectorial, calibración. | `docs/` |
| `DESPLIEGUE - Fase 3.md` | Manual de despliegue de la Fase 3: migración de la bóveda, dependencia de `pyreadstat`, la landing pública y las definiciones legales pendientes. | `docs/` |
| `DESPLIEGUE - Fase 4.md` | Manual de despliegue de la Fase 4: las tres migraciones nuevas, las credenciales de Meta y del proveedor de códigos, y el endurecimiento de la landing. | `docs/` |
| `DESPLIEGUE - Fase 6.md` | Manual de despliegue de la Fase 6, el portal del panelista: la migración `0017`, el ingreso por enlace en Firebase Auth, `PORTAL_URL`, y los dos pasos que deciden si la fase está bien desplegada —que no se filtre quién integra el panel y que un panelista no entre a la administración—. | `docs/` |
| `MANUAL_panelista.md` | Guía para los panelistas: crear la contraseña la primera vez, entrar, recuperarla, puntos y premios, mantener los datos al día, elegir canales, retirar permisos y darse de baja. Escrita para pegarse en una ayuda en línea o mandarse por correo. | `docs/manual/` |
| `DESPLIEGUE - R6.1.a acceso con contraseña.md` | **Instructivo paso a paso** del cambio que reemplaza el enlace mágico del portal por usuario y contraseña: la migración `0018`, la protección contra enumeración en Firebase Auth, la web API key (su paso 6 quedó reemplazado: ver el documento de R-ORG), y el paso 9 —que los panelistas ya enrolados creen su contraseña—, sin el cual queda arriba un portal que nadie puede usar. | `docs/` |
| `DESPLIEGUE - 512 dimensiones.md` | **Instructivo paso a paso** para bajar los embeddings de 1024 a 512: la migración `0006` del store semántico, el parámetro que había que mandarle a Voyage y nunca se mandaba, y el paso 7 —validar la calidad con 5.000 respuestas antes de cargar 200.000—, que es el que decide si el ahorro valió la pena. | `docs/` |
| `DESPLIEGUE - ingesta diferida.md` | **Instructivo paso a paso** del pase de la ingesta a diferido con Cloud Tasks: la migración `0019`, la habilitación de la API, los tres roles de IAM, el huevo-y-la-gallina de la URL de la función —que se despliega antes de que exista el valor que necesita— y el paso 9, que induce un fallo a propósito para comprobar que el reintento manual funciona antes de que haga falta. | `docs/` |
| `DESPLIEGUE - retomar la ingesta diferida.md` | **Instructivo desde el estado real** en que quedó el despliegue de la ingesta diferida tras dos intentos fallidos: doce pasos, cada afirmación sobre el estado actual con el comando que la comprueba. Incluye el paso 7, que compara el conector de VPC de las dos funciones y es el que explica una tarea que muere a los 127 segundos, y el paso 10, que destraba una carga cuyos lotes quedaron en `pendiente` y que el botón de reintentar no alcanza a recuperar. | `docs/` |
| `DESPLIEGUE - Fase 7.md` | Manual de despliegue de la Fase 7, la de usabilidad: la migración `0020` —el catálogo de motivos de reidentificación—, el despliegue de las dos partes y las cinco pantallas a probar. Cada paso trae la **salida real** de haberlo ensayado contra un Postgres llevado al estado exacto de producción, incluido lo que pasa si la migración se corre dos veces. | `docs/` |
| `DESPLIEGUE - Fase 7 completa, Fase 8 y bug convocable.md` | Manual de despliegue de lo que faltaba de la Fase 7, la Fase 8 (calidad del dato y reproceso) y el arreglo de `v_persona_convocable`: la `boveda/0021` —que cambia el contrato de COLOQUIO y por eso se coordina—, la `semantica/0007`, la batería de COLOQUIO con su chequeo nuevo de cardinalidad (17), las pantallas a probar y un rollback de la `0021` probado (`db/revertir/boveda_0021.sql`). Cada paso trae la **salida real** del ensayo, incluida la segunda corrida de cada migración. | `docs/` |
| `DESPLIEGUE - celular como clave de dedup.md` | **Instructivo paso a paso** del celular como tercera clave de dedup: la `boveda/0022` —un índice **no único**, a propósito—, las dos consultas para mirar antes los celulares fuera de E.164 y los compartidos, el despliegue de las dos partes y las tres pruebas que muestran las cautelas (reutiliza, revisión por otro nombre, la importación lo cuenta como clave). Con la salida real del ensayo. | `docs/` |
| `DESPLIEGUE - COLOQUIO Fase 0.md` | Manual de despliegue de la Fase 5: la bóveda deja de tener un solo consumidor. Migraciones, el rol IAM del segundo sistema y por qué no puede crearse con `gcloud sql users create`, la conectividad, y la verificación conectada como ese rol. | `docs/` |
| `DESPLIEGUE - R5.2.a convocatoria externa.md` | **Instructivo paso a paso** de la `boveda/0016`, sin la cual COLOQUIO no puede convocar: la convocatoria activa pasa a verificarse por sistema y un consumidor externo declara la suya. Ocho pasos, con la verificación de privilegios uno por uno. | `docs/` |
| `DESPLIEGUE - R2.12 enlace de acceso.md` | **Instructivo paso a paso** para poner en producción el enlace de acceso regenerable, desde la base tal como está hoy: trece pasos numerados, con la salida esperada de cada uno y qué hacer si no coincide. Incluye la Fase 5, de la que la `0015` depende. Los manuales por **fase** cubren una entrega entera; los que llevan **número de requerimiento** cubren un cambio que salió por su cuenta. | `docs/` |
| `DESPLIEGUE - R-ORG, paridad demográfica y API key web.md` | **Instructivo paso a paso** de tres pedidos en un solo deploy: la web API key pasa de Secret Manager a la variable `WEB_API_KEY` (el nombre `FIREBASE_…` no se puede cargar, y por eso nadie entraba al portal), el log del error de Identity Toolkit y «no se pudo comprobar» separado de «contraseña incorrecta»; la consulta demográfica con la misma barra de acciones que la semántica; y R-ORG —la `boveda/0024`, de qué carga viene cada persona, datos del estudio y composición por ámbito—. Con la salida real del ensayo de la migración, de su segunda corrida y del rollback (`db/revertir/boveda_0024.sql`). | `docs/` |
| `decisiones.md` | **Por qué el sistema está hecho así.** Las decisiones de diseño que no son obvias, con lo que se descartó, lo que cuestan y la prueba que impide revertirlas sin querer. Léelo antes de cambiar algo que parezca raro. | `docs/` |
| `manual/Manual_de_usuario.pdf` | Manual de usuario: paso a paso de cada tarea, con capturas de la aplicación. | `docs/manual/` |
| `HANDOFF_fase1.md` | Work order de la **Fase 1**: alcance, superficie de API mapeada a R1.x, lógica de dedup, máquina de estados de consentimiento, contrato de cruce entre stores, DoD. **Primer sprint.** | `docs/` |
| `db/boveda/0001_init.sql` | DDL del **store de bóveda** (Cloud SQL): bóveda de identidad (PII + demográficos) + módulo de paneles. | `db/boveda/` |
| `db/semantica/0001_init.sql` | DDL del **store semántico** (Cloud SQL + pgvector): contenido semántico (embeddings + `id_persona`). | `db/semantica/` |

## Orden de lectura para Claude Code

1. `CLAUDE.md` — invariantes y stack (no violar la regla de "PII nunca al store semántico").
2. `PRD_gestion_de_paneles_detallado.md` — qué se construye y por qué; las 4 fases.
3. `PRD_consulta_semantica_cuestionarios.md` — cómo funciona el motor por dentro.
4. `db/boveda/0001_init.sql` + `db/semantica/0001_init.sql` — el modelo de datos.
5. `HANDOFF_fase1.md` — el primer sprint, acotado.
6. `functions/panel_api/ingesta.py` — el pipeline de ingesta implementado.

## Estructura del repo

```
/
├─ CLAUDE.md
├─ README.md
├─ firebase.json  .firebaserc  firestore.rules
├─ docs/                      PRDs y handoff de fase
├─ db/
│  ├─ boveda/                 migraciones de la bóveda (PII + paneles)
│  ├─ semantica/              migraciones del store semántico (pgvector)
│  └─ revertir/               rollbacks probados; no son migraciones y nada los aplica solo
├─ functions/                 Cloud Functions for Firebase (Python)
│  ├─ main.py                 punto de entrada HTTP: /api/**
│  ├─ panel_api/              el núcleo de dominio
│  └─ tests/                  pruebas del DoD, contra Postgres real
├─ scripts/                   cluster de pruebas, chequeos, DSN y verificación de esquema
└─ web/public/                SPA de administración (HTML + módulos ES)
   ├─ index.html              configuración y shell (app de administración)
   ├─ inscribirse.html        landing pública de inscripción, sin login (R3.7)
   ├─ css/estilo.css          identidad visual de Equipos
   └─ js/                     app.js, api.js, demo.js, ui.js, paginas/
```

## Stack (resumen; detalle en `CLAUDE.md`)

- **App:** Firebase — Auth + Cloud Functions (Python) + Hosting.
- **Datos:** dos instancias **Cloud SQL for Postgres** separadas — bóveda (PII + paneles) y semántica (vector, con pgvector). Nunca en la misma instancia.
- **Embeddings:** Voyage `voyage-3.5` a **512 dimensiones**, detrás de una interfaz para cambiar de proveedor. 512 y no las 1024 del default porque la mitad de vector es la mitad de instancia (D54); la dimensión tiene que coincidir con `respuesta.embedding` en el store semántico, y la ingesta lo comprueba antes de mandar nada a embeber.

## Cómo arrancar

### Ver la interfaz sin nada instalado

`web/public/index.html` arranca en **modo demo** mientras `firebaseConfig` tenga
los valores de ejemplo: datos en memoria, sin backend y sin base. Sirve para
recorrer la interfaz; no guarda nada.

```bash
cd web/public && python3 -m http.server 8099    # abrir http://localhost:8099
```

### Correr las pruebas

Las pruebas del backend corren contra un Postgres real con pgvector (el dedup,
el gate de consentimiento y la cascada dependen de índices únicos y de
`on delete cascade`: probarlos contra un doble no probaría nada).

```bash
pip install "psycopg[binary]" pytest
source scripts/pg_pruebas.sh          # levanta el cluster y exporta los DSN
cd functions && python3 -m pytest     # 409 pruebas
scripts/pg_pruebas.sh detener         # al terminar
```

`scripts/chequear_js.sh` chequea la sintaxis de los módulos del frontend (no hay
paso de build).

`scripts/verificar_esquema.py` compara las dos bases contra la lista de
migraciones que el código espera y nombra las que falten, con el comando para
aplicarlas. Es la misma verificación que la aplicación expone en Cumplimiento →
Esquema de las dos bases, pero desde la terminal, de modo que sirve antes de
desplegar:

```bash
# scripts/dsn_local.sh trae la clave de Secret Manager y apunta el DSN al
# Auth Proxy, así no hay que escribirla ni dejarla en el historial.
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
python3 scripts/verificar_esquema.py    # sale con 0 si están al día

# --sql no se conecta a nada, así que no necesita psycopg instalado.
# En esa tubería el que se conecta es psql: el DSN tiene que estar exportado.
python3 scripts/verificar_esquema.py --sql semantica | psql "$DSN_SEMANTICA"
```

Conectarse sí necesita el driver (`pip install "psycopg[binary]"`); el script lo
dice si falta, y ofrece la vía `--sql`.

Con `--pii` no se conecta a ninguna base: lee los archivos de migración del
store semántico y sale con 1 si alguno declara una columna con nombre de PII.
Es el segundo nivel de la regla dura #1 —el primero es un event trigger en la
base—, y existe porque el trigger no ve una migración que todavía no se
aplicó, que es justamente la que llega a un pull request. Corre en CI.

`scripts/verificar_coloquio.py` se conecta **con el rol del segundo
consumidor** y comprueba que la bóveda se defiende sola: que lee la superficie
del contrato y nada más, que un contacto legítimo queda auditado y uno sin
consentimiento se rechaza, que una baja le llega y se puede cerrar, y que
ningún privilegio quedó otorgado fuera de la lista blanca que vive en el repo.
Es el sustituto honesto de «lo revisamos»:

```bash
source scripts/pg_pruebas.sh
python3 scripts/verificar_coloquio.py

# Contra producción, sin escribir nada:
python3 scripts/verificar_coloquio.py --solo-lectura
```

### Puesta en marcha real

El instructivo completo —las dos instancias de Cloud SQL, el conector de VPC,
los secretos, Voyage, el padrón de usuarios y la verificación paso a paso—
está en **[`docs/DESPLIEGUE -  Fase 1 .md`](docs/DESPLIEGUE%20-%20%20Fase%201%20.md)**, y
lo que agrega cada fase siguiente en
**[`docs/DESPLIEGUE - Fase 2.md`](docs/DESPLIEGUE%20-%20Fase%202.md)**,
**[`docs/DESPLIEGUE - Fase 3.md`](docs/DESPLIEGUE%20-%20Fase%203.md)**,
**[`docs/DESPLIEGUE - Fase 4.md`](docs/DESPLIEGUE%20-%20Fase%204.md)** y
**[`docs/DESPLIEGUE - COLOQUIO Fase 0.md`](docs/DESPLIEGUE%20-%20COLOQUIO%20Fase%200.md)**.

El resumen, para ubicarse:

1. Dos instancias **Cloud SQL for Postgres** separadas (nunca la misma), sin
   IP pública. En la semántica, `create extension vector`.
2. Aplicar las migraciones: `db/boveda/` (tres) y `db/semantica/` (dos).
3. Un **conector de Acceso a VPC**: es lo que le permite a la función llegar
   a las IP privadas de las bases.
4. Tres secretos en Secret Manager: `DSN_BOVEDA`, `DSN_SEMANTICA` y
   `EMBEDDINGS_API_KEY`. Tienen que existir **antes** del primer deploy.
5. Auth con correo/contraseña, Firestore en modo producción, y el padrón de
   usuarios con `scripts/alta_usuario.js`.
6. `export VPC_CONNECTOR=paneles-conn && firebase deploy`.

## Fase 1 — qué está implementado

| Requisito | Dónde |
|---|---|
| R1.1 alta en la bóveda | `panel_api/personas.py` · `POST /api/panelistas` |
| R1.2 dedup de identidad | `panel_api/dedup.py` + `panel_api/revision.py` |
| R1.3 consentimiento por finalidad | `panel_api/consentimiento.py` |
| R1.4 paneles y membresía N:M | `panel_api/paneles.py` |
| R1.5 fielding y vínculo de respuestas | `panel_api/encuestas.py` + `ingesta.py` |
| R1.6 separación de stores | `panel_api/pii.py` + `panel_api/semantica.py` |
| Cascada de baja | `panel_api/bajas.py` |

## Decisiones pendientes (no bloquean el arranque de la Fase 1)

- **[legal]** Alcance del consentimiento del alta frente al uso semántico entre estudios (bloquea las partes semánticas).
- **[legal/finanzas]** Tratamiento fiscal del canje de premios en Uruguay (bloquea la gamificación, Fase 3).
- **[infra]** Región de las instancias Cloud SQL (relevante para URCDP: ahí vive la PII) y proveedor de embeddings definitivo.
