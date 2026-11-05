# PEDIDO — Encuestas sin convocatoria: cómo se muestran, y personas con identificadores vacíos

**Sistema:** Gestión de paneles · sección Encuestas
**Severidad:** Media — **no afecta el funcionamiento** (verificado: muestreo y
fatiga cuentan solo participaciones de origen `convocatoria`), pero la pantalla
muestra datos que se contradicen
**Detectado:** 2026-11-05, después de desplegar encuestas y cargas unificadas
**Estado:** Para corregir

---

## 1. Qué se ve

En una encuesta migrada desde una carga —por ejemplo AVANTI—:

- El **listado** dice **0 convocados** y **910 respondieron**.
- El **detalle**, en la sección **«Convocatoria»**, lista a las 910 personas con
  la columna **«Convocado: 26/10/2026»**.
- El texto de la sección dice *«La audiencia de una encuesta independiente es la
  foto de la consulta con que se creó»*, pero esta encuesta **no se creó desde una
  consulta**: llegó como archivo de respuestas.

## 2. Por qué pasa

**La columna «Convocado» muestra `convocado_en`**, una fecha, sin mirar
`participacion.convocado`:

```js
<td class="small">${fechaCorta(p.convocado_en)}</td>
```

**Y `convocado_en` es obligatorio** (`not null default now()` desde la `0001`).
Por eso la migración `0032`, al crear las participaciones de quienes vinieron en
una carga, tuvo que ponerle una fecha: la de la carga.

```sql
insert into participacion (..., convocado, convocado_en)
select ..., false, pc.creado_en
```

El dato `convocado = false` es correcto. Lo engañoso es mostrar una fecha de
convocatoria para alguien que no fue convocado.

> **Por qué no se rompe nada.** `v_fatiga_panelista` y el muestreo
> (`muestreo.py`) filtran por `pa.origen = 'convocatoria'`, así que estas
> participaciones de origen `importacion` no cuentan como contacto reciente. El
> problema es solo de presentación, pero cualquier código nuevo que lea
> `convocado_en` sin mirar `convocado` u `origen` va a caer en lo mismo.

## 3. Qué se pide

### 3.1 · El listado de encuestas

- [ ] Para una encuesta **sin convocatoria** —ninguna participación de origen
      `convocatoria`—, la columna **Convocados** muestra **«—»**, no 0.

### 3.2 · Dos tipos de encuesta independiente

Hoy una encuesta independiente puede venir de dos lados, y la pantalla no los
distingue:

| Origen | Tiene audiencia | Qué corresponde mostrar |
|---|---|---|
| **Desde una consulta** («Crear encuesta con estos panelistas») | Sí | Sección **Convocatoria**, como hoy |
| **Desde una carga** (archivo de respuestas, o migrada) | No | Sección **Respondentes** |

- [ ] Agregar a `encuesta` un campo explícito de origen —`panel`, `consulta`,
      `carga`—, que se fije al crearla. La migración marca como `carga` las ocho
      encuestas que creó la `0032`.
- [ ] Mostrar en el listado ese origen en la columna Audiencia, en vez de
      «INDEPENDIENTE» para todas.

### 3.3 · El detalle de una encuesta sin convocatoria

- [ ] La sección se llama **«Respondentes»**, no «Convocatoria».
- [ ] El texto explica lo que es: *«Esta encuesta llegó como archivo de
      respuestas: nadie fue convocado desde el sistema. Se listan quienes
      respondieron.»*
- [ ] **Sin columna «Convocado».** En su lugar, la fecha en que respondió o se
      cargó.

### 3.4 · En cualquier encuesta: no mostrar fecha de convocatoria a quien no fue convocado

Aun en una encuesta con convocatoria pueden aparecer personas **no convocadas**
—quien respondió por un enlace reenviado (R-EU.4)—.

- [ ] La columna «Convocado» se basa en `participacion.convocado`: si es falso,
      muestra **«no convocado»**, no una fecha.
- [ ] Revisar los demás lugares que leen `convocado_en` —el historial de la
      ficha (`cargas.py`), la longitudinal, la lista negra— para que ninguno lo
      presente como fecha de convocatoria cuando `convocado` es falso.

> **Alternativa más limpia, para evaluar.** Hacer `convocado_en` nullable y
> dejarlo en nulo cuando `convocado` es falso. Elimina la ambigüedad de raíz,
> pero toca varias consultas que hoy lo usan como fecha de referencia. No es
> necesario para este pedido; con los ajustes de arriba alcanza.

---

## 4. Personas con identificadores vacíos

### Qué se ve

En la lista de AVANTI, la primera fila es una persona cuyo **nombre es
«......»** y cuyo **identificador de origen es «...........»**.

### Por qué pasa

La columna de celular del archivo original venía sucia —puntos, guiones,
«solo WhatsApp», etc.—. Con el identificador de la plataforma de campo como
clave, el sistema aceptó **«...........»** como un identificador válido y creó
una persona con él.

### Qué se pide

- [ ] Rechazar como identificador, en cualquier tipo, un valor **sin ninguna
      letra ni dígito** (`...........`, `-----`, vacío con espacios). La fila se
      informa en el resultado como `identificador_invalido`, igual que un
      documento mal formado.
- [ ] Lo mismo para el nombre: un nombre sin letras se guarda como vacío, no
      como «......».
- [ ] **Revisar las personas ya creadas** con identificador o nombre sin letras
      ni dígitos: listarlas para que el equipo decida si darlas de baja. No
      borrarlas automáticamente: pueden tener respuestas válidas y otros
      identificadores buenos.

## 5. Definition of Done

- [ ] Una encuesta sin convocatoria muestra «—» en convocados (test).
- [ ] Una encuesta desde carga muestra la sección «Respondentes», sin columna de
      convocatoria (test).
- [ ] Una encuesta desde consulta sigue mostrando «Convocatoria» (test de no
      regresión).
- [ ] En una encuesta con convocatoria, un no convocado aparece como «no
      convocado» y no con fecha (test).
- [ ] Las ocho encuestas de la `0032` quedan con origen `carga`.
- [ ] Un identificador «...........» se rechaza al cargar (test).
- [ ] Existe una consulta o pantalla que lista las personas con identificador o
      nombre inválidos ya cargadas.

## 6. Costos

**US$ 0.** Interfaz, un campo nuevo y una validación.
