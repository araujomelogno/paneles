# Sistema de gestión de paneles + consulta semántica

Plataforma para administrar paneles de investigación de mercado y consultar el contenido de las encuestas por significado.
Referencia de producto: `PRD_gestion_de_paneles_detallado.md` y `PRD_consulta_semantica_cuestionarios.md`.

## Invariante central: dos stores

- **Bóveda** (`db/boveda/`): Cloud SQL for Postgres, instancia separada de la semántica. PII + módulo de paneles (paneles, membresías, consentimiento, participación, muestreo, gamificación). Los atributos demográficos (sexo, localidad, fecha de nacimiento → tramo etario) son **autoritativos acá**.
- **Semántico** (`db/semantica/`): Cloud SQL for Postgres + pgvector. **Solo** embeddings + `id_persona`. Contenido puro.

**Regla dura #1: la PII nunca se escribe en el store semántico.** Al store semántico solo viaja `id_persona`. Cualquier código que intente persistir nombre, email, celular, documento, fecha exacta u observaciones del lado semántico está mal.

Desde la Fase 5 esa regla **la hace valer la base**: un event trigger en `ddl_command_end` rechaza el `alter table` en el momento, y el catálogo de campos prohibidos vive en `campo_pii` (store semántico). `pii.CAMPOS_PII` es su espejo y hay una prueba que falla si divergen. Las excepciones legítimas (`nombre` en `cuestionario`, `pregunta` y `serie`) se declaran en `excepcion_pii`, con su motivo, en una migración: nunca apagando el guardia.

Cruce entre stores: por conjuntos de `id_persona`. Los dos lados comparten `ref_estudio` (uuid) para vincular `encuesta` (bóveda) ↔ `cuestionario` (semántico). No hay FK entre stores; es una referencia lógica.

## Identidad

- `id_persona` (uuid) es la clave de la persona en **toda** la plataforma. La emite el **enrolamiento** (bóveda), no la ingesta.
- El panelista **es** la `persona` de la bóveda. La ingesta semántica referencia ese `id_persona`; no crea personas.

## La bóveda tiene más de un consumidor

Desde la Fase 5, `paneles` **no es el único** programa que le habla a la bóveda: COLOQUIO (investigación cualitativa) es el segundo, y el registro `sistema_consumidor` está hecho para que sumar un tercero cueste una fila y un `grant`.

La consecuencia para quien escribe código acá: **una invariante de cumplimiento escrita en Python es una promesa repetida en dos bases de código**, y la primera que se rompa lo va a hacer en silencio. Por eso:

- El gate de consentimiento es `v_persona_convocable`, **no** un `select` en Python. `consentimiento.esta_vigente()` y `filtrar_con_consentimiento()` leen esa vista; no vuelvan a calcular la regla.
- Leer un dato de contacto es `contacto_para_convocatoria()`: un canal, con el gate reaplicado y la auditoría en la misma transacción. No hay un segundo camino.
- Una baja genera pendientes para **todos** los consumidores activos (`generar_borrados_pendientes()`), y la bóveda nunca espera a ninguno.
- La fatiga es la excepción deliberada: se expone como **hechos** (`v_fatiga_panelista`) y cada consumidor pone su umbral. El consentimiento es legal y no se negocia; la fatiga es negocio.
- El origen de cada fila de auditoría se deriva de la conexión (`sistema_de_la_conexion()`, sobre `session_user`), nunca de un parámetro del llamador.

`scripts/verificar_coloquio.py` se conecta **como `coloquio_app`** y comprueba todo eso, incluida una lista blanca de privilegios efectivos que vive en el repo. Si una migración abre un acceso de más, esa prueba rompe. Correrlo después de tocar cualquier `grant`, vista o función de la superficie externa.

## Reglas de negocio que el código debe respetar

- No se puede convocar ni incluir en muestreo a una persona sin `consentimiento` **vigente** para la finalidad correspondiente.
- El uso semántico entre estudios exige la finalidad `uso_semantico` vigente, **distinta** de `contacto_participacion`.
- Baja / retiro de consentimiento ⇒ **borrado en cascada**: PII (bóveda) + embeddings (semántico) + salida del muestreo.
- Saldo de puntos siempre **>= 0**; sin sobregiro en canje; puntos con vencimiento. (No hay constraint DDL: se valida en la app / trigger.)
- Se ganan puntos solo por participación de **calidad** (`respondio = true` y `calidad_estado = 'ok'`).

## Stack

Capa de aplicación en **Firebase**; los datos en **Postgres**. Firebase NO reemplaza a los stores (ver "Por qué los datos siguen en Postgres").

- **Auth:** Firebase Auth (app admin y, más adelante, la landing de panelistas). Resuelve el modelo de auth.
- **Backend / lógica:** Cloud Functions for Firebase, runtime **Python** (continuidad con `ingesta.py`); acceso a las bases vía el conector de Cloud SQL; `pgvector-python` para vectores.
- **Frontend admin:** SPA (React) en Firebase Hosting.
- **Store semántico (vector):** Cloud SQL for Postgres + pgvector (full GCP).
- **Bóveda (PII + paneles):** Cloud SQL for Postgres, en una instancia dedicada y separada (proyecto/VPC aparte, acceso bloqueado). "Separado" = control de acceso, misma nube.
- **Ambos stores en Cloud SQL**, en instancias distintas: nunca en la misma instancia (la separación bóveda/semántico es parte del diseño de privacidad).
- **Embeddings:** Voyage `voyage-3.5` (1024 dims) por defecto, detrás de una interfaz para cambiar de proveedor.

### Por qué los datos siguen en Postgres (no Firestore)
La búsqueda semántica depende de **pgvector** y de consultas relacionales (joins respuesta↔pregunta↔persona, rollup a individuo, puente bóveda↔semántico por `id_persona`, distancia a fuerza bruta sobre subconjuntos). Firestore no cubre ese patrón: su vector search es un KNN plano, sin joins ni agregación. Y el módulo de paneles necesita integridad relacional (membresías N:M, estados de consentimiento, ledger de puntos con saldo ≥ 0, índices únicos de dedup). Mover los datos a Firestore sería deshacer el diseño. Firebase se usa para auth, funciones y hosting; los datos, en Postgres.

## Convenciones

- Identificadores de esquema y dominio en **español** (ya reflejado en los `.sql`).
- Migraciones versionadas; no editar el esquema a mano en la base.
- Secretos por variables de entorno; nunca en el repo. Además de guardarlos en Secret Manager hay que **declararlos** en la lista `SECRETOS` de `functions/main.py`: `firebase deploy` solo monta los declarados, y uno que falta no rompe nada —hace que el sistema se comporte como si la credencial no existiera—. `functions/tests/test_main.py` lo verifica en las dos direcciones.
- **Trabajar por fases.** Empezar por la Fase 1 (`HANDOFF_fase1.md`). No implementar una fase posterior hasta cerrar el Definition of Done de la anterior.

## Antes de pushear: verificar si el PR ya está mergeado

La rama de trabajo se reutiliza entre entregas, así que **siempre** hay que
comprobar el estado del PR antes de pushear. Un PR mergeado no admite trabajo
nuevo: apilar commits encima de historia ya mergeada deja la rama divergida y
el trabajo invisible.

```bash
gh pr view --json state,mergedAt        # o el equivalente por API
```

Si está mergeado, el trabajo que sigue es un cambio **nuevo**: rebasar los
commits sin mergear sobre el `main` actual, conservando el nombre de la rama, y
abrir **otro PR**.

```bash
git fetch origin main
git checkout -B <rama> origin/main
git cherry-pick <commits-sin-mergear>
git push --force-with-lease -u origin <rama>
```
