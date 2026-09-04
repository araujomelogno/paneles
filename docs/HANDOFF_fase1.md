# Handoff técnico — Fase 1: Núcleo de personas e identidad

Work order para implementar **solo la Fase 1**. Ver contexto e invariantes en `CLAUDE.md`.
No implementar composición, consulta, muestreo ni gamificación en esta fase (sus tablas existen en `esquema_local.sql` pero quedan sin lógica).

## Objetivo

Poder enrolar panelistas (PII en la bóveda) con `id_persona` estable, deduplicar en el alta, registrar consentimiento por finalidad, gestionar paneles y membresías N:M, y fieldear una encuesta a un panel enganchando la ingesta semántica de modo que las respuestas queden asociadas al `id_persona` correcto.

## Alcance — dentro

- Tablas de la bóveda: `persona`, `alias_origen`, `panel`, `membresia`, `consentimiento`, `encuesta`, `participacion` (solo alta de convocatoria; el resultado de respuesta/calidad es de fases siguientes).
- Backend en Cloud Functions for Firebase (runtime Python) con la superficie de API de abajo (endpoints HTTP/callable).
- Enganche con la ingesta semántica ya especificada (script `ingesta.py`): al fieldear, se crea la `encuesta` con su `ref_estudio`, y la ingesta escribe en el store semántico el `cuestionario` con ese mismo `ref_estudio` y las `respuesta` con el `id_persona` de cada panelista.
- UI admin mínima para alta de panelista y gestión de paneles/membresías (puede ser posterior al backend dentro de la misma fase).

## Alcance — fuera (fases siguientes)

Composición vs objetivo, reportes de participación, consulta (demográfica/semántica/mixta), motor de muestreo, chequeos de calidad, gamificación.

## Superficie de API (mapeada a requisitos)

- `POST /panelistas` — alta. Ejecuta dedup, crea/reutiliza `persona`, registra consentimiento. → **R1.1, R1.2, R1.3**
- `GET /panelistas/{id_persona}` — ficha (datos de la bóveda).
- `POST /paneles` — crea panel. → **R1.4**
- `POST /paneles/{panel_id}/miembros` — agrega `membresia` (idempotente por `unique(panel_id, id_persona)`). → **R1.4**
- `DELETE /paneles/{panel_id}/miembros/{id_persona}` — marca membresía en `baja`.
- `POST /consentimientos/{id_persona}/retiro` — retira consentimiento y dispara la cascada (borra PII de la bóveda + pide borrado de embeddings al store semántico por `id_persona` + saca del muestreo). → soporte a **R1.3** / regla de cascada.
- `POST /encuestas` — crea `encuesta` en un panel (genera `ref_estudio`). → **R1.5**
- `POST /encuestas/{id}/ingesta` — dispara la ingesta: valida consentimiento `uso_semantico` de los panelistas, corre `ingesta.py` pasando `ref_estudio`, y asocia cada `respuesta` del store semántico al `id_persona`. → **R1.5**

**Guardrail transversal (R1.6):** ninguna ruta puede escribir PII en el store semántico. Al store semántico solo va `id_persona` (+ `ref_estudio`, embeddings, texto de respuesta ya despersonalizado).

## Lógica de dedup (alta de panelista)

Orden de resolución, se detiene en el primer match:
1. **documento** exacto → reutiliza ese `id_persona`.
2. **email** (case-insensitive) exacto → reutiliza ese `id_persona`.
3. Sin documento ni email, pero **nombre + fecha_nacimiento** coinciden con una persona existente → **no fusiona**: marca el alta como `revisión` para resolución manual.
4. Sin match → crea `persona` nueva (nuevo `id_persona`).

Registrar el alias de origen en `alias_origen` cuando el alta viene de una plataforma (Dooblo/Alchemer) con su id propio.

## Consentimiento (máquina de estados)

- Finalidades: `contacto_participacion` y `uso_semantico` (independientes).
- Otorgar: crea fila `estado = 'vigente'` con `version_texto` y `otorgado_en`.
- Retirar: `estado = 'retirado'`, `retirado_en = now()`, y dispara la cascada.
- Re-otorgar: nueva fila vigente (se conserva el historial).
- **Gate:** convocar / muestrear exige `contacto_participacion` vigente; ingestar para uso semántico exige `uso_semantico` vigente. Sin el consentimiento correspondiente, la operación se rechaza.

## Contrato de cruce entre stores

- `encuesta.ref_estudio` (bóveda) == `cuestionario.ref_estudio` (semántico): misma uuid, generada al crear la encuesta.
- La ingesta al store semántico recibe `ref_estudio` y el mapa panelista→`id_persona`; nunca recibe PII.
- Toda relación entre stores se resuelve por `id_persona` y `ref_estudio`, sin FK cruzada.

## Definition of Done (Fase 1)

- [ ] Se puede enrolar un panelista con su PII y consentimiento; el alta sin consentimiento se rechaza.
- [ ] El dedup reutiliza `id_persona` por documento/email y marca `revisión` en match ambiguo, sin crear duplicados.
- [ ] Un panelista puede pertenecer a varios paneles sin duplicarse.
- [ ] Al fieldear e ingestar una encuesta, cada respuesta del store semántico queda asociada al `id_persona` correcto y comparte `ref_estudio` con la `encuesta`.
- [ ] Retirar consentimiento borra PII de la bóveda, pide borrado de embeddings del store semántico y saca del muestreo.
- [ ] Auditoría de esquema: **0 columnas de PII** en el store semántico.
- [ ] Pruebas automatizadas cubren dedup (los 4 casos), gate de consentimiento y el guardrail de PII.

## Pendientes que NO bloquean la Fase 1 (resolver en paralelo)

- **[legal]** Alcance del consentimiento del alta frente al uso semántico entre estudios.
- **[infra]** Región de las instancias Cloud SQL (bóveda y semántica) para URCDP; proveedor de embeddings. (Auth: Firebase Auth; stores: Cloud SQL, decididos.)
