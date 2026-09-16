# SPEC — Identificación del respondente en el trabajo de campo

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Spec de referencia:** `specs/SPEC_fase3.md` · R3.9 (ingesta desde archivo SAV)
**Requisito nuevo:** R3.12
**Estado:** Para desarrollo
**Última actualización:** 2026-09-15

---

## 1. Problem Statement

El mapeo de respuestas a personas se apoya hoy en `alias_origen`: el id que la plataforma de campo (Dooblo, Alchemer) le asignó al respondente, guardado en la bóveda al enrolar. Eso asume que **ese id es estable por persona a través de estudios**, y ese supuesto no se cumple: cada encuesta genera ids nuevos para el mismo individuo. La consecuencia es directa y grave — al ingestar un estudio nuevo, ninguna fila matchea y todas caen en `sin_mapear`, dejando la ingesta en cero sin que nada esté roto.

El sistema tiene una ventaja que hoy no usa: **la muestra sale de él**. La convocatoria se decide acá, así que el identificador correcto puede viajar *hacia* el campo en vez de intentar adivinarlo *a la vuelta*. Precargar el `id_persona` en el instrumento elimina el problema de raíz, y de paso mantiene el archivo de campo libre de datos identificatorios: hoy la alternativa sería usar documento o email como llave, lo que metería PII en un export que circula por la plataforma, por la computadora de quien lo baja y por correo.

## 2. Goals

- **Que el identificador del sistema viaje al campo** y vuelva en el archivo, haciendo el mapeo directo y sin ambigüedad.
- **Que la ingesta sepa qué tipo de identificador está leyendo**, en vez de asumir siempre alias de plataforma.
- **Mantener el archivo de campo seudónimo**: sin documento ni email cuando se puede evitar.
- **Conservar una vía de respaldo** para estudios donde no se pudo precargar.

## 3. Non-Goals

- **No se integra con la API de Dooblo ni de Alchemer.** El sistema exporta la muestra; cargarla en la plataforma es un paso manual del equipo de campo.
- **No se cambia el dedup de identidad (R1.2)**, que sigue siendo documento → email → nombre+fecha de nacimiento para el alta de personas.
- **No se elimina `alias_origen`**: sigue siendo válido y es el modo por defecto, para no romper las cargas actuales.
- **No se resuelven identificadores mezclados** en un mismo archivo: cada carga declara un solo tipo.
- No cambia el pipeline de consulta ni el modelo de embeddings.

## 4. User Stories

- Como responsable de panel, quiero exportar la muestra de una encuesta con el identificador del sistema, para precargarla en la plataforma de campo.
- Como analista, quiero declarar al ingestar qué tipo de identificador trae la columna, para que el mapeo no dependa de un supuesto.
- Como analista, quiero que si mapeo por documento o email, el sistema registre el alias de esa plataforma, para que la próxima carga del mismo estudio no dependa de la llave natural.
- Como DPO, quiero que el archivo que va a campo no lleve datos identificatorios cuando no hace falta.

**Casos borde**
- Fila con `id_persona` que no existe en la bóveda (typo, o persona borrada por baja).
- Fila con `id_persona` mal formado (no es un uuid).
- Estudio donde no se pudo precargar y hay que caer a documento o email.
- Persona que ya tiene alias registrado para esa plataforma y llega por documento (no debe duplicarse el alias).
- Muestra exportada, y entre la exportación y la ingesta alguien retiró el consentimiento.

## 5. Requirements

### R3.12.a — Exportar la muestra con el identificador del sistema (P0)

- Dada una encuesta con convocatoria realizada, cuando se exporta la muestra, entonces se genera un archivo (CSV/XLSX) con una fila por convocado y una columna `id_persona`.
- Dada la exportación, entonces incluye por defecto **solo** el `id_persona`: es lo necesario para precargar el instrumento.
- Dado que el equipo de campo necesita datos de contacto para operar (llamar, enviar el link), entonces puede pedirse una exportación **con contacto**, que se trata como una reidentificación: se registra en `reidentificacion` con motivo propio, con actor, fecha y cantidad de personas, igual que R3.10.
- Dada la exportación con contacto, entonces incluye los mismos campos que la reidentificación devuelve, **sin** fecha de nacimiento exacta ni observaciones.
- [ ] El archivo lleva una marca visible cuando contiene datos personales.

### R3.12.b — Tipo de identificador declarado en la ingesta (P0)

En la pantalla de carga, junto a la columna que identifica al respondente, se declara **qué tipo de identificador** contiene:

| Tipo | Resolución | Uso previsto |
|---|---|---|
| `id_persona` | Directo contra `persona.id_persona` | **Preferido**, cuando se precargó la muestra |
| `alias` | Contra `alias_origen` (origen + id) | Comportamiento actual; **default**, por compatibilidad |
| `documento` | Contra `persona.documento` | Respaldo |
| `email` | Contra `persona.email` (case-insensitive) | Respaldo |

- Dada una carga, entonces el tipo de identificador es obligatorio y tiene `alias` como valor por defecto: las cargas existentes siguen funcionando sin cambios.
- Dado el tipo `alias`, entonces también se declara el origen (plataforma), como hoy.
- Dado el tipo `id_persona`, entonces cada valor se resuelve directamente; un valor que no es un uuid válido o que no existe en la bóveda va a `sin_mapear` **con el motivo diferenciado** (`formato_invalido` vs `no_encontrado`).
- Dado el tipo `documento` o `email`, entonces se resuelve contra el campo correspondiente de la bóveda; los que no matchean van a `sin_mapear`.
- Dado cualquier tipo, entonces el resto del flujo no cambia: gate de `uso_semantico`, membresía y participación (addendum R3.9.a/b), y guardrail de PII siguen igual.

### R3.12.c — Registro automático del alias (P0)

- Dada una fila resuelta por `documento` o `email`, y declarado el origen de la plataforma, entonces se registra el alias `(origen, id_en_origen)` de esa persona si no existía.
- Dado un alias ya existente para ese par, entonces no se duplica.
- Dado el resultado, entonces informa cuántos alias nuevos se registraron.

> **Por qué.** Que una carga por llave natural deje sembrado el alias significa que una re-ingesta del mismo estudio ya no depende de documento ni email — y que el archivo de campo deja de necesitar PII a partir de la segunda vuelta.

### R3.12.d — Informe de resultados (P0)

Al bloque de resultado de la ingesta se agrega, para `sin_mapear`, el **motivo** por fila: `formato_invalido`, `no_encontrado`, o `sin_alias_para_ese_origen`. Hoy la lista no distingue, y las tres causas se arreglan de forma distinta.

## 6. Cambios de esquema

Ninguno obligatorio. `alias_origen` ya soporta el registro automático (unicidad por `(origen, id_en_origen)`), y la resolución por `documento`/`email` usa índices que ya existen en `persona`.

## 7. Definition of Done

- [ ] Se puede exportar la muestra de una encuesta con `id_persona` y precargarla en la plataforma de campo.
- [ ] La exportación con datos de contacto queda registrada en `reidentificacion` (test).
- [ ] Una carga declarando tipo `id_persona` mapea directo y no consulta `alias_origen`.
- [ ] Un `id_persona` mal formado y uno inexistente aparecen en `sin_mapear` con motivos distintos (test).
- [ ] Una carga declarando `documento` resuelve y registra el alias de esa plataforma (test).
- [ ] Un alias ya existente no se duplica en una segunda carga (test).
- [ ] Una carga sin declarar tipo se comporta como `alias`, igual que hoy (test de no regresión).
- [ ] El informe de `sin_mapear` trae el motivo por fila.

## 8. Riesgos y preguntas abiertas

- **[privacidad]** Precargar el `id_persona` significa que la plataforma de campo pasa a tener ese token. Es opaco y no reidentifica por sí solo, pero es la misma clave con la que está indexado el store semántico. Una alternativa más conservadora es emitir un **código por ola** y traducirlo a `id_persona` en la ingesta; cuesta una tabla de mapeo más y no cambia el flujo del equipo de campo. Decidir antes de implementar: `id_persona` directo (simple) o código por ola (defensa en profundidad).
- **[operación]** ¿Dooblo y Alchemer permiten precargar una variable oculta por respondente en el flujo que usa hoy el equipo? Si no, R3.12.a pierde sentido y el peso cae en documento/email. *Verificar antes de construir.*
- **[producto]** Consentimiento retirado entre la exportación de la muestra y la ingesta: hoy el gate de `uso_semantico` lo filtra al ingestar, así que la respuesta ya recogida no entra. Confirmar que ese es el comportamiento deseado.
- **[datos]** Si un estudio se carga por `documento`, el archivo de campo contuvo PII. Vale definir una política de retención/borrado de esos archivos fuera del sistema.
