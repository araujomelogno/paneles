# ADDENDUM — `paneles`: superficie de selección cualitativa

**Agrega a:** la Fase 5 (`specs/SPEC_fase5.md`), requisitos R5.9 a R5.13
**Migración:** `db/boveda/0034_superficie_seleccion_cuali.sql`
**Lo pide:** COLOQUIO, requisitos R1.14 a R1.17 de `coloquio/specs/ADDENDUM_seleccion_R1.14-R1.18.md`
**Última actualización:** 2026-11-06

---

## 1. Por qué

La superficie de la Fase 5 le dio a COLOQUIO lo justo para convocar: quién tiene consentimiento vigente, con cuatro segmentadores fijos (`sexo`, `localidad`, `tramo_etario`, `edad`) y el contacto de quien ya tiene una convocatoria declarada.

La pantalla de selección pide cuatro cosas que esa superficie no puede dar: filtrar por **cualquier** atributo del catálogo dinámico, mostrar los atributos como columnas, abrir una ficha, y ver de quién se trata antes de decidir. Las cuatro necesitan objetos nuevos en la bóveda.

Los cuatro segmentadores fijos de `f_persona_convocable()` son, además, una deuda: `persona.sexo` y `persona.localidad` quedaron obsoletas con la `0008`, el vocabulario real vive en `atributo_demografico`, y la superficie externa sigue hablando el idioma viejo.

---

## 2. Decisión tomada: COLOQUIO ve nombre y contacto antes de convocar

Está decidido y queda escrito acá, porque afloja una regla deliberada y el motivo no puede vivir en la memoria de nadie.

**Lo que la Fase 5 decía.** `contacto_para_convocatoria()` exige una convocatoria declarada y se niega con *«no hay motivo para leer su contacto»*. La idea era que el contacto no se mira: se usa cuando se convoca.

**Lo que se resolvió.** El coordinador de campo necesita decidir a quién llamar viendo quién es, y en cualitativo esa decisión se toma sobre doce personas elegidas entre cientos: operar con códigos opacos no es viable. Entonces la identidad —nombre, celular y mail— está disponible en la etapa de selección, antes de declarar la convocatoria.

**Lo que NO se afloja, y es lo que hace aceptable la decisión.** Tres límites, y los tres van en la función, no en la interfaz:

1. **Sigue siendo por sesión, no una búsqueda libre.** `identificar_candidatos()` recibe el id de la sesión de COLOQUIO además de los `id_persona`, y la sesión tiene que existir como referencia declarada. No es un endpoint para traducir cualquier uuid a una persona: es para los candidatos de una sesión concreta.
2. **Sigue auditado, una fila por persona.** Motivo propio `seleccion_cuali`, distinto de `convocatoria`, para que en una revisión se pueda separar «lo miró para elegir» de «lo miró para llamar».
3. **Sigue siendo una acción, no una carga de página.** La función existe para que la llamen sobre un conjunto tildado. Si termina llamándose con doscientos ids en cada búsqueda, la auditoría registra todo y deja de señalar nada — ahí el límite se rompió, y la métrica de §7 lo va a mostrar.

El gate de consentimiento **no se toca**: solo se identifica a quien está en `v_persona_convocable` con `contacto_participacion` vigente.

---

## 3. Requisitos

### R5.9 — Catálogo de atributos

**`v_atributo_catalogo`** — una fila por atributo activo, con sus categorías.

```
clave, etiqueta, tipo, orden, es_especial,
categorias jsonb   -- [{clave, etiqueta, orden}] de las activas, en orden
```

- Solo atributos y categorías con `activo = true`.
- Es vocabulario, no dato de persona: no hay PII y el grant no tiene costo.
- `es_especial` **se expone**. COLOQUIO decide qué hacer con la marca (su R1.14 no los ofrece por defecto), pero si la bóveda no la manda, el consumidor no puede respetarla.

**Aceptación:** un atributo nuevo creado en `paneles` aparece en la vista sin desplegar nada; uno dado de baja desaparece; `select` como `coloquio_app` funciona.

---

### R5.10 — Atributos de un conjunto de personas

**`atributos_de_personas(p_ids uuid[], p_momento timestamptz default now())`**, `security definer`.

```
returns table (id_persona uuid, atributo text, valor text,
               etiqueta_valor text, valor_num numeric,
               valor_fecha date, procedencia text)
```

- Es `f_atributo_persona(momento)` acotada a un conjunto de ids. El `momento` se conserva porque los valores envejecen (`0010`) y una sesión vieja tiene que poder mostrar los de entonces.
- **Devuelve solo las filas que existen.** Un atributo ausente no viene con valor nulo: no viene. Que no sepamos el nivel educativo de alguien es información, y confundirlo con un «sin dato» declarado es peor que no mostrarlo.
- Tope de `p_ids` (500) para que no se use como volcado del padrón.
- Solo personas que estén en `v_persona_convocable`: no sirve para mirar los atributos de cualquiera.

**Aceptación:** con 200 ids devuelve en una sola llamada; con un id fuera de la vista no devuelve nada; un atributo sin valor no aparece en el resultado.

---

### R5.11 — Convocables filtrados por atributo dinámico

**Es la pieza grande, y la única que no es mecánica.**

Hoy `f_persona_convocable()` devuelve cuatro columnas fijas y no sabe filtrar por un atributo arbitrario. Resolverlo en memoria no sirve: el panel tiene decenas de miles de personas y el filtro tiene que ocurrir en la base.

**`f_persona_convocable_filtrada(p_finalidad text, p_filtros jsonb, p_limite int default 500)`**, `security definer`.

Forma de `p_filtros` — un objeto por atributo, con la clave del catálogo:

```json
{
  "sexo":            { "en": ["F"] },
  "nivel_educativo": { "en": ["terciario", "posgrado"] },
  "edad":            { "min": 35, "max": 44 },
  "fecha_alta":      { "desde": "2024-01-01" }
}
```

- `en` para `categorico` y `derivado`; `min`/`max` para `numerico`; `desde`/`hasta` para `fecha`.
- Varias categorías del mismo atributo se combinan con **o**; atributos distintos, con **y**.
- Devuelve `(id_persona, finalidades)` y nada más. **Los valores de los atributos no se devuelven acá**: para eso está R5.10, y separarlo es lo que permite que agregar una columna en COLOQUIO no vuelva a ejecutar la búsqueda.
- Una clave que no está en el catálogo, o un atributo inactivo, **se rechaza con error**. No se ignora en silencio: un filtro que no se aplicó y nadie avisó devuelve más gente de la pedida, que es la peor falla posible en un muestreo.
- Mantiene intacto el gate: persona activa, sin lápida, con la finalidad vigente de ámbito persona o panel.

**Nota de implementación.** El patrón natural es un `exists` correlacionado por cada atributo filtrado, no un `join` por atributo: es la misma lección de la `0021` —un `join` que multiplica filas es un bug que aparece en producción como cuotas infladas— y además deja que el planificador use `persona_atributo_por_atributo`.

`f_persona_convocable()` **se conserva sin cambios**. Esta es una función nueva, no un reemplazo: lo que ya anda sigue andando.

**Aceptación:** filtrar por un atributo que no existía al escribir la función funciona; dos categorías del mismo atributo dan la unión; dos atributos distintos dan la intersección; una clave inventada da error; nadie sin consentimiento aparece; con el panel cargado, responde en un tiempo aceptable.

---

### R5.12 — Ficha seudónima del candidato

**`ficha_candidato(p_id_persona uuid, p_momento timestamptz default now())`**, `security definer`.

Equivalente de `ficha.seudonima()` de `panel_api`: estado, fecha de enrolamiento, atributos con procedencia, y paneles activos. **Sin un solo dato identificatorio y sin registro de reidentificación** — es la misma información que la búsqueda ya devolvió, vista de a una persona.

Solo para personas en `v_persona_convocable`.

**Aceptación:** no devuelve ningún campo de `pii.CAMPOS_PII`; no escribe en `reidentificacion`; una persona fuera de la vista da no encontrado.

---

### R5.13 — Identificación de candidatos

**`identificar_candidatos(p_ids uuid[], p_referencia text, p_actor text, p_motivo text default 'seleccion_cuali')`**, `security definer`.

```
returns table (id_persona uuid, nombre text, email text, celular text)
```

Materializa la decisión de §2 y sus tres límites:

- **`p_referencia` es obligatoria** — el id de la sesión de COLOQUIO. Sin ella, falla. Es lo que hace que esto sea «los candidatos de esta sesión» y no «traducime estos uuid».
- **`p_actor` es obligatorio** y es el email de la persona humana que lo pidió, igual que en `contacto_para_convocatoria()`. Si no hay alguien detrás, no hay motivo.
- **Reaplica el gate**: solo personas en `v_persona_convocable` con `contacto_participacion` vigente. Las demás no vienen en el resultado, y no se avisa cuáles faltaron por qué.
- **Escribe una fila en `reidentificacion` por persona efectivamente devuelta, antes de devolver**, con `motivo`, `sistema_de_la_conexion()` y la referencia en el `contexto`.
- Tope de 200 ids por llamada.
- `contacto_para_convocatoria()` **no se toca**: sigue exigiendo convocatoria declarada y sigue siendo la vía del momento de convocar. Esta función es para la etapa anterior y tiene su propio motivo, justamente para poder distinguirlas después.

**Aceptación:** devuelve los tres campos; escribe exactamente una fila de auditoría por persona devuelta; sin `p_referencia` falla; sin `p_actor` falla; una persona sin consentimiento no viene; con 201 ids falla; `contacto_para_convocatoria()` sigue comportándose igual que antes.

---

## 4. Privilegios

```sql
grant select  on v_atributo_catalogo                      to coloquio_app;
grant execute on function atributos_de_personas(uuid[], timestamptz)        to coloquio_app;
grant execute on function f_persona_convocable_filtrada(text, jsonb, int)   to coloquio_app;
grant execute on function ficha_candidato(uuid, timestamptz)                to coloquio_app;
grant execute on function identificar_candidatos(uuid[], text, text, text)  to coloquio_app;
```

Las funciones nuevas nacen con `execute` a `public`: **`revoke` primero, `grant` después**, igual que la `0014` y la `0021`. Y la migración entera va en una transacción y es re-ejecutable, por la lección de la `0021`: una segunda corrida que falla a mitad de camino dejó a COLOQUIO sin acceso a la superficie.

`scripts/verificar_coloquio.py` suma las cinco a su lista blanca. Si la lista promete algo que el rol no tiene, el script rompe — que es como nos enteramos a tiempo.

---

## 5. Lo que NO entra

- No se tocan `f_persona_convocable()`, `v_persona_convocable`, `declarar_convocatoria()` ni `contacto_para_convocatoria()`. Todo lo de acá es aditivo.
- No se expone `persona_atributo` ni `f_atributo_persona` directo: el consumidor pasa por funciones acotadas, que es por lo que la `0014` las puso en `security definer`.
- No se cambia la propiedad de las tablas (`app_paneles` sigue siendo dueño). Sigue en P2.

---

## 6. Definition of Done

1. La migración aplica, y aplicarla dos veces deja todo igual.
2. Las pruebas de `paneles` siguen pasando sin modificar ninguna.
3. `scripts/verificar_coloquio.py` pasa con las cinco piezas en la lista blanca.
4. Un filtro por un atributo que no existía al escribir la migración funciona de punta a punta.
5. Identificar veinte candidatos escribe veinte filas en `reidentificacion` con motivo `seleccion_cuali` y sistema `coloquio`.
6. `contacto_para_convocatoria()` sigue negándose sin convocatoria declarada.
7. `decisiones.md` suma la decisión de §2 con su motivo y sus tres límites.

## 7. Qué mirar después

- **Volumen de `reidentificacion` con motivo `seleccion_cuali`, por usuario y por sesión.** Si se parece al tamaño de los resultados en vez de al tamaño de las listas cortas, el tercer límite de §2 no se respetó y la auditoría dejó de servir. Es la métrica que dice si la decisión de §2 envejeció bien.
- Latencia de `f_persona_convocable_filtrada` con tres o cuatro atributos filtrados sobre el panel completo.

## 8. Costo

Nulo. Vistas, funciones y `grant` sobre instancias que ya existen. Lo único que crece es `reidentificacion`, con filas de texto corto; conviene fijarle retención antes de que importe, no después.
