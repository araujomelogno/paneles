# Sistema de gestión de paneles + consulta semántica

Plataforma para administrar paneles de investigación de mercado y consultar el contenido de las encuestas por significado.
Referencia de producto: `PRD_gestion_de_paneles_detallado.md` y `PRD_consulta_semantica_cuestionarios.md`.

## Invariante central: dos stores

- **Local** (`esquema_local.sql`): Cloud SQL for Postgres, instancia separada de la remota. Bóveda de PII + módulo de paneles (paneles, membresías, consentimiento, participación, muestreo, gamificación). Los atributos demográficos (sexo, localidad, fecha de nacimiento → tramo etario) son **autoritativos acá**.
- **Remoto** (`esquema_cuestionarios.sql`): Cloud SQL for Postgres + pgvector. **Solo** embeddings + `id_persona`. Contenido puro.

**Regla dura #1: la PII nunca se escribe en el store remoto.** Al remoto solo viaja `id_persona`. Cualquier código que intente persistir nombre, email, celular, documento, fecha exacta u observaciones del lado remoto está mal.

Cruce entre stores: por conjuntos de `id_persona`. Los dos lados comparten `ref_estudio` (uuid) para vincular `encuesta` (local) ↔ `cuestionario` (remoto). No hay FK entre stores; es una referencia lógica.

## Identidad

- `id_persona` (uuid) es la clave de la persona en **toda** la plataforma. La emite el **enrolamiento** (bóveda), no la ingesta.
- El panelista **es** la `persona` de la bóveda. La ingesta semántica referencia ese `id_persona`; no crea personas.

## Reglas de negocio que el código debe respetar

- No se puede convocar ni incluir en muestreo a una persona sin `consentimiento` **vigente** para la finalidad correspondiente.
- El uso semántico entre estudios exige la finalidad `uso_semantico` vigente, **distinta** de `contacto_participacion`.
- Baja / retiro de consentimiento ⇒ **borrado en cascada**: PII (local) + embeddings (remoto) + salida del muestreo.
- Saldo de puntos siempre **>= 0**; sin sobregiro en canje; puntos con vencimiento. (No hay constraint DDL: se valida en la app / trigger.)
- Se ganan puntos solo por participación de **calidad** (`respondio = true` y `calidad_estado = 'ok'`).

## Stack

Capa de aplicación en **Firebase**; los datos en **Postgres**. Firebase NO reemplaza a los stores (ver "Por qué los datos siguen en Postgres").

- **Auth:** Firebase Auth (app admin y, más adelante, la landing de panelistas). Resuelve el modelo de auth.
- **Backend / lógica:** Cloud Functions for Firebase, runtime **Python** (continuidad con `ingesta.py`); acceso a las bases vía el conector de Cloud SQL; `pgvector-python` para vectores.
- **Frontend admin:** SPA (React) en Firebase Hosting.
- **Store remoto (vector):** Cloud SQL for Postgres + pgvector (full GCP).
- **Store local (bóveda + paneles):** Cloud SQL for Postgres, en una instancia dedicada y separada (proyecto/VPC aparte, acceso bloqueado). "Separado" = control de acceso, misma nube.
- **Ambos stores en Cloud SQL**, en instancias distintas: nunca en la misma instancia (la separación local/remoto es parte del diseño de privacidad).
- **Embeddings:** Voyage `voyage-3.5` (1024 dims) por defecto, detrás de una interfaz para cambiar de proveedor.

### Por qué los datos siguen en Postgres (no Firestore)
La búsqueda semántica depende de **pgvector** y de consultas relacionales (joins respuesta↔pregunta↔persona, rollup a individuo, puente local↔remoto por `id_persona`, distancia a fuerza bruta sobre subconjuntos). Firestore no cubre ese patrón: su vector search es un KNN plano, sin joins ni agregación. Y el módulo de paneles necesita integridad relacional (membresías N:M, estados de consentimiento, ledger de puntos con saldo ≥ 0, índices únicos de dedup). Mover los datos a Firestore sería deshacer el diseño. Firebase se usa para auth, funciones y hosting; los datos, en Postgres.

## Convenciones

- Identificadores de esquema y dominio en **español** (ya reflejado en los `.sql`).
- Migraciones versionadas; no editar el esquema a mano en la base.
- Secretos por variables de entorno; nunca en el repo.
- **Trabajar por fases.** Empezar por la Fase 1 (`HANDOFF_fase1.md`). No implementar una fase posterior hasta cerrar el Definition of Done de la anterior.
