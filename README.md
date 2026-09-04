# Paquete de handoff — Sistema de gestión de paneles + consulta semántica

Este repositorio contiene todo lo necesario para que Claude Code arranque el desarrollo.
Empezá leyendo `CLAUDE.md`, después el PRD, y desarrollá por fases empezando por `HANDOFF_fase1.md`.

## Documentos (set definitivo)

| Archivo | Qué es | Destino en el repo |
|---|---|---|
| `CLAUDE.md` | Contexto persistente: invariantes (dos stores, PII nunca al remoto), identidad, reglas de negocio, stack. **Léelo primero y siempre.** | raíz `/` |
| `PRD_gestion_de_paneles_detallado.md` | PRD del sistema: problema, objetivos, no-objetivos, arquitectura, y las 4 fases con requisitos (R1.x…R4.x), criterios de aceptación y DoD. Documento **autoritativo** de producto. | `docs/` |
| `PRD_consulta_semantica_cuestionarios.md` | Spec del **módulo de consulta semántica** (mecánica interna del motor: modelo vectorial, ingesta, embeddings, ranking, verificación con Claude). Es un módulo de este sistema, no un producto aparte. | `docs/` |
| `HANDOFF_fase1.md` | Work order de la **Fase 1**: alcance, superficie de API mapeada a R1.x, lógica de dedup, máquina de estados de consentimiento, contrato de cruce entre stores, DoD. **Primer sprint.** | `docs/` |
| `db/local/0001_init.sql` | DDL del **store local** (Cloud SQL): bóveda de identidad (PII + demográficos) + módulo de paneles. | `db/local/` |
| `db/remoto/0001_init.sql` | DDL del **store remoto** (Cloud SQL + pgvector): contenido semántico (embeddings + `id_persona`). | `db/remoto/` |

## Orden de lectura para Claude Code

1. `CLAUDE.md` — invariantes y stack (no violar la regla de "PII nunca al remoto").
2. `PRD_gestion_de_paneles_detallado.md` — qué se construye y por qué; las 4 fases.
3. `PRD_consulta_semantica_cuestionarios.md` — cómo funciona el motor por dentro.
4. `db/local/0001_init.sql` + `db/remoto/0001_init.sql` — el modelo de datos.
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
│  ├─ local/                  migraciones del store local (bóveda + paneles)
│  └─ remoto/                 migraciones del store remoto (pgvector)
├─ functions/                 Cloud Functions for Firebase (Python)
│  ├─ main.py                 punto de entrada HTTP: /api/**
│  ├─ panel_api/              el núcleo de dominio
│  └─ tests/                  pruebas del DoD, contra Postgres real
├─ scripts/                   cluster de pruebas y chequeo de sintaxis JS
└─ web/public/                SPA de administración (HTML + módulos ES)
   ├─ index.html              configuración y shell
   ├─ css/estilo.css          identidad visual de Equipos
   └─ js/                     app.js, api.js, demo.js, ui.js, paginas/
```

## Stack (resumen; detalle en `CLAUDE.md`)

- **App:** Firebase — Auth + Cloud Functions (Python) + Hosting.
- **Datos:** dos instancias **Cloud SQL for Postgres** separadas — local (bóveda + paneles) y remota (vector, con pgvector). Nunca en la misma instancia.
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
cd functions && python3 -m pytest     # 88 pruebas
scripts/pg_pruebas.sh detener         # al terminar
```

`scripts/chequear_js.sh` chequea la sintaxis de los módulos del frontend (no hay
paso de build).

### Puesta en marcha real

1. **Dos instancias Cloud SQL for Postgres**, separadas — nunca la misma. En la
   remota, `create extension vector`. Aplicar las migraciones en orden:

   ```bash
   psql "$DSN_LOCAL"  -f db/local/0001_init.sql
   psql "$DSN_LOCAL"  -f db/local/0002_revision_alta.sql
   psql "$DSN_LOCAL"  -f db/local/0003_baja_persona.sql
   psql "$DSN_REMOTO" -f db/remoto/0001_init.sql
   ```

2. **Proyecto Firebase `gestion-paneles`** (uno solo, ya fijado en
   `.firebaserc`). Crearlo y habilitar lo que usa la app:

   ```bash
   firebase login
   firebase projects:create gestion-paneles --display-name "Gestión de paneles"
   firebase use gestion-paneles
   firebase apps:create web "Admin de paneles"     # devuelve apiKey, senderId y appId
   ```

   En la consola: **Authentication** → habilitar *Correo electrónico/contraseña*;
   **Firestore** → crear la base (modo producción; las reglas de este repo la
   dejan cerrada salvo `usuarios/{uid}` de solo lectura).

   Después pegar `apiKey`, `messagingSenderId` y `appId` en
   `web/public/index.html`: el resto de la config ya está completa. Con la
   `apiKey` puesta, la app deja el modo demo y pasa a pegarle al backend.

3. **Secretos** por Secret Manager, nunca en el repo:

   ```bash
   firebase functions:secrets:set DSN_LOCAL
   firebase functions:secrets:set DSN_REMOTO
   firebase functions:secrets:set EMBEDDINGS_API_KEY
   ```

4. **Padrón de usuarios**: por cada persona de Equipos que use la app, un
   usuario en Firebase Auth y un documento `usuarios/{uid}` en Firestore con
   `{ nombre, email, rol, activo: true }`. Roles: `admin`, `operaciones`
   (responsable de panel), `analista`, `dpo` (cumplimiento). Firestore no
   guarda ningún dato de panelista.

   ```bash
   npm install firebase-admin
   gcloud auth application-default login
   node scripts/alta_usuario.js ana@equipos.com.uy admin "Ana Pérez"
   ```

5. **Desplegar**: `firebase deploy`.

## Fase 1 — qué está implementado

| Requisito | Dónde |
|---|---|
| R1.1 alta en la bóveda | `panel_api/personas.py` · `POST /api/panelistas` |
| R1.2 dedup de identidad | `panel_api/dedup.py` + `panel_api/revision.py` |
| R1.3 consentimiento por finalidad | `panel_api/consentimiento.py` |
| R1.4 paneles y membresía N:M | `panel_api/paneles.py` |
| R1.5 fielding y vínculo de respuestas | `panel_api/encuestas.py` + `ingesta.py` |
| R1.6 separación de stores | `panel_api/pii.py` + `panel_api/remoto.py` |
| Cascada de baja | `panel_api/bajas.py` |

## Decisiones pendientes (no bloquean el arranque de la Fase 1)

- **[legal]** Alcance del consentimiento del alta frente al uso semántico entre estudios (bloquea las partes semánticas).
- **[legal/finanzas]** Tratamiento fiscal del canje de premios en Uruguay (bloquea la gamificación, Fase 3).
- **[infra]** Región de las instancias Cloud SQL (relevante para URCDP: ahí vive la PII) y proveedor de embeddings definitivo.
