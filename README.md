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
│  └─ semantica/              migraciones del store semántico (pgvector)
├─ functions/                 Cloud Functions for Firebase (Python)
│  ├─ main.py                 punto de entrada HTTP: /api/**
│  ├─ panel_api/              el núcleo de dominio
│  └─ tests/                  pruebas del DoD, contra Postgres real
├─ scripts/                   cluster de pruebas, chequeos, DSN y verificación de esquema
└─ web/public/                SPA de administración (HTML + módulos ES)
   ├─ index.html              configuración y shell
   ├─ css/estilo.css          identidad visual de Equipos
   └─ js/                     app.js, api.js, demo.js, ui.js, paginas/
```

## Stack (resumen; detalle en `CLAUDE.md`)

- **App:** Firebase — Auth + Cloud Functions (Python) + Hosting.
- **Datos:** dos instancias **Cloud SQL for Postgres** separadas — bóveda (PII + paneles) y semántica (vector, con pgvector). Nunca en la misma instancia.
- **Embeddings:** Voyage `voyage-3.5` (1024 dims) por defecto, detrás de una interfaz para cambiar de proveedor.

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
cd functions && python3 -m pytest     # 275 pruebas
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

### Puesta en marcha real

El instructivo completo —las dos instancias de Cloud SQL, el conector de VPC,
los secretos, Voyage, el padrón de usuarios y la verificación paso a paso—
está en **[`docs/DESPLIEGUE -  Fase 1 .md`](docs/DESPLIEGUE%20-%20%20Fase%201%20.md)**, y
lo que agrega la Fase 2 en
**[`docs/DESPLIEGUE - Fase 2.md`](docs/DESPLIEGUE%20-%20Fase%202.md)**.

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
