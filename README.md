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
| `esquema_local.sql` | DDL del **store local** (Cloud SQL): bóveda de identidad (PII + demográficos) + módulo de paneles. | `db/local/0001_init.sql` |
| `esquema_cuestionarios.sql` | DDL del **store remoto** (Cloud SQL + pgvector): contenido semántico (embeddings + `id_persona`). | `db/remoto/0001_init.sql` |
| `ingesta.py` | Script de ingesta (ancho → largo, resolución código/etiqueta, embeddings en lotes, upsert). Referencia de la mecánica del módulo semántico. | `ingesta/` |

## Orden de lectura para Claude Code

1. `CLAUDE.md` — invariantes y stack (no violar la regla de "PII nunca al remoto").
2. `PRD_gestion_de_paneles_detallado.md` — qué se construye y por qué; las 4 fases.
3. `PRD_consulta_semantica_cuestionarios.md` — cómo funciona el motor por dentro.
4. `esquema_local.sql` + `esquema_cuestionarios.sql` — el modelo de datos.
5. `HANDOFF_fase1.md` — el primer sprint, acotado.
6. `ingesta.py` — referencia al implementar el enganche de ingesta.

## Estructura de repo sugerida

```
/
├─ CLAUDE.md
├─ README.md
├─ docs/
│  ├─ PRD_gestion_de_paneles_detallado.md
│  ├─ PRD_consulta_semantica_cuestionarios.md
│  └─ HANDOFF_fase1.md
├─ db/
│  ├─ local/    0001_init.sql   (= esquema_local.sql)
│  └─ remoto/   0001_init.sql   (= esquema_cuestionarios.sql)
├─ functions/   # Cloud Functions for Firebase (runtime Python)
│  ├─ main.py
│  └─ requirements.txt
├─ ingesta/     # ingesta.py y utilidades de carga
└─ web/         # SPA admin (React) para Firebase Hosting
```

## Stack (resumen; detalle en `CLAUDE.md`)

- **App:** Firebase — Auth + Cloud Functions (Python) + Hosting.
- **Datos:** dos instancias **Cloud SQL for Postgres** separadas — local (bóveda + paneles) y remota (vector, con pgvector). Nunca en la misma instancia.
- **Embeddings:** Voyage `voyage-3.5` (1024 dims) por defecto, detrás de una interfaz para cambiar de proveedor.

## Cómo arrancar

1. Provisionar las dos instancias Cloud SQL y aplicar las migraciones (`db/local`, `db/remoto`).
2. Configurar el proyecto Firebase (Auth, Functions Python, Hosting) y el conector de Cloud SQL desde las funciones.
3. Secrets por variables de entorno (DSN de cada store, API key de embeddings); nunca en el repo.
4. Tomar `HANDOFF_fase1.md` como primer sprint. No pasar a la fase siguiente hasta cerrar su Definition of Done.

## Decisiones pendientes (no bloquean el arranque de la Fase 1)

- **[legal]** Alcance del consentimiento del alta frente al uso semántico entre estudios (bloquea las partes semánticas).
- **[legal/finanzas]** Tratamiento fiscal del canje de premios en Uruguay (bloquea la gamificación, Fase 3).
- **[infra]** Región de las instancias Cloud SQL (relevante para URCDP: ahí vive la PII) y proveedor de embeddings definitivo.
