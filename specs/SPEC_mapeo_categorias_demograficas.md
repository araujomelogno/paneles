# SPEC — Mapeo explícito de valores a categorías demográficas

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Corrige:** R3.14.c, que quedó implementado a medias
**Alcance:** pantalla de ingesta (encuesta y carga de panelistas) y su backend
**Estado:** Para desarrollo
**Última actualización:** 2026-10-01

---

## 1. Problem Statement

Al cargar panelistas desde un `.sav` se puede marcar que una variable
corresponde a un atributo demográfico del catálogo, pero **no se puede decir
qué significa cada uno de sus códigos**. El marcado que la pantalla arma es
solo `{variable: campo}`: dice *«esta variable es nivel educativo»* y nada más.

El resultado es que un archivo con `NIVEL_EDUC` en códigos `1, 2, 3` no tiene
cómo conectarse con las categorías canónicas del atributo (`primaria`,
`secundaria`, `terciaria`). Y como cada estudio codifica distinto —uno usa
`1=Primaria`, otro `1=Bajo`—, sin mapeo explícito el dato entra crudo o no
entra.

Eso incumple R3.14.c, que pide exactamente esto: *«Dada una variable mapeada a
un atributo categórico, entonces se mapean los valores del archivo a las
categorías canónicas del atributo, igual que se hace con las etiquetas de una
pregunta cerrada»*. El mecanismo ya existe para las preguntas cerradas —donde
se cargan las opciones con el formato `1=Fernet; 2=Whisky`—; falta para los
atributos demográficos.

**Consecuencia hoy:** los filtros demográficos, la composición contra objetivo
y las cuotas del muestreo trabajan sobre un atributo que puede estar vacío o
con valores sin canonizar, sin que nadie se entere en el momento de cargar.

## 2. Goals

- Poder declarar, en la carga, **a qué categoría canónica corresponde cada
  valor del archivo**, para cada variable marcada como atributo categórico.
- Que el sistema **proponga** el mapeo cuando pueda deducirlo, y que quien
  carga lo confirme o lo corrija.
- Que un valor sin mapear **se informe**, en vez de entrar crudo o perderse en
  silencio.
- Conservar el **valor crudo** junto al canónico, para poder corregir un mapeo
  sin recargar el archivo.

## 3. Non-Goals

- **No se crean categorías al vuelo desde la carga.** El vocabulario lo define
  un admin en Configuración; si falta una categoría, se agrega ahí. Permitirlo
  en la carga reintroduce el problema que el catálogo resuelve.
- **No se tocan los atributos `numerico`, `fecha` ni `derivado`**: el mapeo de
  categorías aplica solo a los `categorico`.
- **No cambia el marcado de patronímicos** (`nombre`, `documento`, `email`,
  `celular`, `contacto`): esos no tienen categorías.
- No cambia el modelo de datos de `persona_atributo` más allá de lo indicado.

## 4. User Stories

- Como analista, quiero decir que en este archivo el `1` es «primaria» y el `2`
  es «secundaria», para que el dato entre bien.
- Como analista, quiero que el sistema me proponga el mapeo cuando el archivo
  trae etiquetas, y solo confirmarlo.
- Como analista, quiero enterarme **antes de confirmar** si quedó algún valor
  sin mapear, no descubrirlo semanas después en una cuota que no cierra.
- Como responsable de datos, quiero corregir un mapeo mal hecho sin volver a
  pedir el archivo.

**Casos borde**
- Variable con *value labels* en el `.sav` cuyas etiquetas coinciden con las
  categorías del catálogo (el caso fácil).
- Variable con etiquetas que **no** coinciden (`1=Bajo` contra categorías
  `primaria/secundaria/terciaria`).
- Variable **sin** etiquetas: solo códigos crudos.
- Valor presente en el archivo que no corresponde a ninguna categoría.
- Categoría del catálogo que ningún valor del archivo usa (no es un error).
- Celdas vacías o con códigos de no respuesta (`98`, `99`).
- Un `.xlsx` en vez de un `.sav`: no hay etiquetas, todo mapeo es manual.

## 5. Requirements

### R-MAP.1 — Declarar el mapeo en la pantalla (P0)

- Dada una variable marcada como atributo demográfico **de tipo `categorico`**,
  entonces la pantalla ofrece declarar el mapeo de sus valores a las categorías
  canónicas de ese atributo.
- Dado ese mapeo, entonces se presenta **valor por valor**: para cada valor
  distinto presente en el archivo, un desplegable con las categorías del
  atributo, más la opción explícita **«no mapear»**.
- Dado un atributo que no es `categorico`, entonces no se ofrece mapeo.
- Dado que se cambia el atributo asignado a una variable, entonces el mapeo se
  reinicia contra las categorías del atributo nuevo.

> **Por qué valor por valor y no un campo de texto libre.** Las opciones de una
> pregunta cerrada se cargan como texto (`1=Fernet; 2=Whisky`) porque su destino
> es un embedding y no hay vocabulario que respetar. Acá el destino es una
> categoría canónica de un catálogo cerrado: un desplegable impide inventar
> categorías y hace visible cuáles quedaron sin asignar.

### R-MAP.2 — Sugerencia automática (P0)

- Dado un `.sav` cuya variable trae *value labels*, entonces el sistema
  **propone** el mapeo haciendo coincidir cada etiqueta con la categoría del
  catálogo cuya etiqueta o clave coincida (sin distinguir mayúsculas ni
  acentos).
- Dada una sugerencia, entonces **nunca se aplica sola**: queda precargada en
  el desplegable y quien carga confirma o corrige.
- Dado un valor sin etiqueta, o cuya etiqueta no coincide con ninguna
  categoría, entonces queda sin proponer y visible como pendiente.

### R-MAP.3 — Valores sin mapear: visibles, nunca silenciosos (P0)

- Dado que al confirmar quedan valores sin mapear, entonces **se avisa antes de
  ingestar**, listando cuáles son y cuántas filas afecta cada uno.
- Dado ese aviso, entonces se puede continuar igual (es una decisión válida:
  puede tratarse de códigos de no respuesta), pero **de forma consciente**.
- Dado un valor sin mapear, entonces esa persona queda **sin valor** para ese
  atributo: no se inventa categoría y no se guarda el código crudo como si
  fuera una.
- Dado el resultado de la ingesta, entonces informa por atributo: cuántos
  valores se mapearon, cuántos quedaron sin mapear y cuáles.

### R-MAP.4 — Persistencia y corrección (P0)

- Dado un valor mapeado, entonces se guarda la **categoría canónica** y el
  **valor crudo tal como vino del archivo** (`persona_atributo.valor_crudo`, que
  ya existe).
- Dado un valor sin mapear, entonces se guarda el crudo sin categoría, de modo
  que un remapeo posterior pueda resolverlo.
- Dadas las reglas de escritura, entonces son las del addendum R3.9.d y no
  cambian: si el atributo está vacío se completa, si ya tiene un valor distinto
  **no se sobrescribe** y se informa la discrepancia.

### R-MAP.5 — Remapear sin recargar (P1)

- Dado un atributo con valores crudos guardados, entonces se puede corregir el
  mapeo y **recalcular** las categorías a partir de los crudos, sin volver a
  subir el archivo.
- Dada esa corrección, entonces queda registrada con autor, fecha y cantidad de
  valores afectados.

*(Es R3.14.h del spec de atributos, que depende de esto para ser útil.)*

## 6. Contrato de la API

El cuerpo de la ingesta hoy lleva el marcado como `{variable: campo}`. Pasa a
admitir, además, el mapeo por variable. Forma sugerida:

```jsonc
{
  "marcado_demografico": {
    "SEXO":       { "campo": "sexo" },
    "NIVEL_EDUC": {
      "campo": "nivel_educativo",
      "mapeo": { "1": "primaria", "2": "secundaria", "3": "terciaria" }
    },
    "EDAD":       { "campo": "edad_declarada" }
  }
}
```

- **Compatibilidad hacia atrás:** aceptar también la forma vieja
  (`"SEXO": "sexo"`) e interpretarla como `{ "campo": "sexo" }` sin mapeo.
- Un valor ausente del objeto `mapeo` es un valor **sin mapear** (R-MAP.3).
- El backend **valida** que cada categoría del mapeo exista en el catálogo para
  ese atributo, y rechaza el pedido si no: es la última línea contra un mapeo
  inventado.

## 7. Cambios de esquema

Ninguno. `persona_atributo` ya tiene `categoria_id` y `valor_crudo`.

## 8. Definition of Done

- [ ] Una variable marcada como atributo categórico ofrece mapeo valor por
      valor, con desplegable de categorías y opción «no mapear».
- [ ] Un atributo `numerico`, `fecha` o `derivado` no ofrece mapeo (test).
- [ ] Un `.sav` con *value labels* que coinciden con el catálogo llega con el
      mapeo precargado, sin aplicarlo solo (test).
- [ ] Etiquetas que no coinciden quedan sin proponer y visibles como
      pendientes.
- [ ] Al confirmar con valores sin mapear, se avisa antes de ingestar, con la
      lista y el conteo de filas (test).
- [ ] Un valor sin mapear deja a esa persona sin valor para el atributo: no se
      guarda el código crudo como categoría (test).
- [ ] Cada valor mapeado guarda categoría **y** crudo (test).
- [ ] El backend rechaza un mapeo que apunte a una categoría inexistente
      (test).
- [ ] La forma vieja del marcado (`{variable: campo}`) sigue funcionando (test
      de no regresión).
- [ ] El resultado de la ingesta informa, por atributo, mapeados y sin mapear.

## 9. Riesgos

- **[producto]** El mapeo valor por valor alarga la pantalla en archivos con
  muchas variables categóricas. Conviene que las sugerencias resuelvan el caso
  común y que solo lo pendiente pida atención visual.
- **[datos]** Si el catálogo no tiene la categoría que el archivo necesita, la
  carga se traba hasta que un admin la agregue en Configuración. Es deliberado
  —protege el vocabulario— pero conviene que el aviso lo diga con esas palabras,
  para que quien carga sepa a quién pedirle qué.
- **[datos]** Cargas ya hechas antes de este cambio pueden tener atributos con
  valores crudos sin categoría. R-MAP.5 es lo que permite repararlas; sin eso,
  hay que recargar.
