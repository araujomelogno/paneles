# SPEC — Ámbitos de composición y metadatos del estudio en la carga

**Sistema:** Gestión de paneles · composición y carga de panelistas
**Estado:** Para desarrollo
**Última actualización:** 2026-10-06

---

## 1. Lo que los dos pedidos tienen en común

Los dos dependen de **una pieza que hoy no existe**: saber **de qué carga vino
cada persona**.

Verificado en el esquema: `carga` tiene su `id`, `nombre`, `descripcion` y
`ref_estudio` —la uuid que cruza con el store semántico— pero **ninguna tabla
relaciona `persona` con `carga`**. Una persona creada en una carga no deja
constancia de cuál fue.

Sin ese vínculo:

- No se puede **filtrar la composición por carga** (pedido 1).
- No se puede **atribuirle a un panelista los datos del estudio del que salió**
  (pedido 2).

Por eso la primera parte de esta spec es ese vínculo, y los dos pedidos se
apoyan en él.

> **El cruce por `ref_estudio` no alcanza.** Permite ir de una carga a sus
> respuestas en el store semántico, pero no a las **personas**: del lado
> semántico solo hay `id_persona` sin saber si esa persona nació en esa carga o
> ya existía. Y la composición es una pregunta de la bóveda, no del semántico.

---

## 2. R-ORG.1 — Vínculo persona ↔ carga (P0)

- Dada una carga que **crea** una persona, entonces queda registrado que esa
  persona se originó en esa carga.
- Dada una carga que **reutiliza** una persona existente (el dedup la
  encontró), entonces queda registrado que esa persona **participó** de esa
  carga, distinguiéndolo de haberse originado en ella.
- Dada una persona que aparece en varias cargas, entonces se registran
  **todas**: el vínculo es de muchos a muchos, no un campo en `persona`.

**Esquema:** tabla nueva en la bóveda, `persona_carga`: `id_persona`,
`carga_id`, `origen` (`creada` | `reutilizada`), `creado_en`. Única por
`(id_persona, carga_id)`.

- [ ] La ingesta desde encuesta registra el vínculo equivalente con la
      **encuesta**, o se decide explícitamente que no aplica. *(Hoy la encuesta
      ya tiene `participacion`, que cumple ese papel; la carga sin panel no
      tiene nada.)*
- [ ] Una baja arrastra el vínculo (`on delete cascade`), como el resto.

> **Por qué muchos a muchos y no un campo en `persona`.** Una persona puede
> aparecer en varias cargas, y un campo único obligaría a elegir entre
> sobrescribir —perdiendo de dónde vino la primera vez— o ignorar las
> siguientes. Es el mismo criterio que ya rige para membresías.

---

## 3. Pedido 1 — Ámbitos de composición

### R-ORG.2 — Elegir el ámbito (P0)

Hoy la composición se calcula **siempre por panel**. Se agregan dos ámbitos:

- [ ] **Todos los panelistas**: toda la bóveda, sin filtrar por panel.
- [ ] **Un panel** (lo actual, sin cambios).
- [ ] **Una carga**: las personas vinculadas a esa carga (R-ORG.1).

- Dada la pantalla de composición, entonces se elige el ámbito antes de
  calcular, y el ámbito elegido se muestra junto al resultado.
- Dado el ámbito «una carga», entonces el selector lista las cargas por nombre
  y fecha, no por id.

> **«Todos los panelistas» resuelve un punto ciego actual.** Las personas
> cargadas **sin panel** (R3.13) no aparecen hoy en ninguna vista de
> composición: solo se las ve en el total de panelistas. Con este ámbito, por
> primera vez se puede mirar la composición de **todo** lo que hay en la
> bóveda, no solo de lo que está organizado en paneles.

### R-ORG.3 — Qué pasa con el objetivo de cuotas (P0)

`objetivo_composicion` está atado a `panel_id`. Los ámbitos nuevos **no tienen
objetivo**, así que hay que definir qué se muestra.

- Dado un ámbito **sin objetivo cargado**, entonces se muestra el **descriptivo**
  y la brecha se informa como **no disponible**, con el motivo —no se deja en
  blanco ni se muestra un cero.
- [ ] Decidir si los objetivos pueden definirse también para «todos» y para una
      carga, o si siguen siendo exclusivos de los paneles.

> **Recomendación:** permitir objetivo para «todos los panelistas», porque la
> representatividad del conjunto es una pregunta legítima y recurrente. Para una
> **carga** probablemente no valga: una carga es un hecho del pasado —lo que
> entró, entró— y no algo que se pueda ir corrigiendo con reclutamiento. Ahí el
> descriptivo alcanza.

---

## 4. Pedido 2 — Metadatos del estudio en la carga

### R-ORG.4 — Datos del estudio al cargar (P0)

- Dada la pantalla de carga de panelistas, entonces se pueden especificar
  **nombre del estudio**, **fecha del estudio** y **público objetivo**.
- Dados esos datos, entonces se guardan **en la carga**, no copiados en cada
  persona.
- Dada una persona, entonces se puede ver de qué estudios proviene, con esos
  datos, resolviéndolo por el vínculo de R-ORG.1.

**Esquema:** agregar a `carga` los campos `fecha_estudio date` y
`publico_objetivo text`. El `nombre` ya existe.

> **Por qué en la carga y no como propiedad copiada en cada panelista.** Pedir
> que «quede como propiedad de cada panelista cargado» es el resultado que se
> busca, pero copiarlo 1.131 veces trae tres problemas: una persona que aparece
> en tres cargas tendría tres valores —o el último pisaría a los anteriores,
> perdiendo de dónde vino—; corregir un dato del estudio obligaría a actualizar
> todas las filas; y sería información del estudio viviendo en la ficha de la
> persona.
>
> **Con el vínculo, el resultado es el mismo y mejor:** la ficha del panelista
> muestra «proviene de: Ómnibus agosto 2026 · 2026-08-15 · población adulta de
> Montevideo», resuelto por join. Si una persona viene de tres estudios, se ven
> los tres. Y corregir la fecha del estudio se hace en un solo lugar.

### R-ORG.5 — Dónde se ven (P0)

- [ ] **Ficha del panelista:** una sección «Origen» con los estudios de los que
      proviene, cada uno con nombre, fecha y público objetivo, y si fue creado o
      reutilizado en esa carga.
- [ ] **Listado de panelistas:** poder **filtrar por carga**.
- [ ] **Columna disponible** en los resultados de consulta (R7.4), para ver de
      qué estudio salió cada persona.
- [ ] **Estadísticas** (R7.5): cargas con su nombre, fecha y cuántas personas
      creó y reutilizó cada una.

### R-ORG.6 — Público objetivo: texto, no vocabulario (P1)

- Dado el campo **público objetivo**, entonces es **texto libre** en esta
  versión.

> **Qué no hacer con él.** Es tentador convertirlo en un atributo del catálogo o
> en un criterio de filtro estructurado, pero describe **al estudio**, no a las
> personas: dos cargas pueden decir «adultos de Montevideo» y «población adulta
> montevideana» y significar lo mismo. Si más adelante hace falta filtrar por
> él, ahí se evalúa un vocabulario controlado —con el mismo criterio que el
> catálogo de atributos—. Por ahora es documentación de procedencia, y vale como
> tal.

---

## 5. Definition of Done

**Vínculo**
- [ ] Una carga que crea personas registra el vínculo con `origen = 'creada'`
      (test).
- [ ] Una carga que reutiliza personas lo registra con `origen = 'reutilizada'`
      (test).
- [ ] Una persona en dos cargas tiene dos vínculos (test).
- [ ] Una baja elimina los vínculos (test).

**Composición**
- [ ] Se puede calcular la composición de **todos los panelistas**, incluidos
      los que no pertenecen a ningún panel (test).
- [ ] Se puede calcular la composición de **una carga** (test).
- [ ] Un ámbito sin objetivo muestra el descriptivo y la brecha como **no
      disponible**, no como cero (test).
- [ ] La composición por panel sigue dando lo mismo que antes (test de no
      regresión).

**Metadatos**
- [ ] Nombre, fecha y público objetivo se piden al cargar y se guardan en la
      carga (test).
- [ ] La ficha del panelista muestra los estudios de los que proviene, con esos
      datos (test).
- [ ] Una persona de tres cargas muestra las tres (test).
- [ ] Corregir la fecha de un estudio se refleja en todas las fichas, sin
      actualizar personas (test).
- [ ] El listado de panelistas se puede filtrar por carga (test).

## 6. Costos

| Concepto | Costo |
|---|---|
| Vínculo `persona_carga` | **Marginal**: una fila por persona y carga. Con 1.131 personas, 1.131 filas de pocos bytes. |
| Metadatos en `carga` | **US$ 0**: dos columnas en una tabla con pocas filas. |
| Ámbitos de composición | **US$ 0 de infraestructura.** A vigilar: «todos los panelistas» agrega una consulta de agregación sobre toda la bóveda. Con `db-f1-micro` y volúmenes mayores conviene medirla; si pesa, se resuelve con un índice o cacheando el resultado. |

> **Lo que esto evita gastar:** copiar los metadatos en cada persona habría
> multiplicado los datos por la cantidad de panelistas y obligado a reescribir
> todas las filas ante cualquier corrección. El vínculo cuesta lo mismo y no
> tiene esa deuda.

## 7. Riesgos y preguntas abiertas

- **[datos]** Las personas **ya cargadas** no tienen vínculo con su carga: la
  información no existe en ningún lado. Para las cargas ya hechas puede
  reconstruirse parcialmente por `alias_origen` y `ref_estudio`, pero no con
  certeza. Decidir si se intenta una reconstrucción retroactiva o si el vínculo
  arranca de cero desde esta versión —y en ese caso, que la ficha lo diga en
  vez de mostrar «sin origen» como si no lo tuviera.
- **[producto]** Si se habilitan objetivos de cuota para «todos los
  panelistas», conviene aclarar en la pantalla contra qué universo se compara:
  un objetivo del panel nacional y uno del total de la bóveda son cosas
  distintas y es fácil confundirlos.
- **[producto]** La composición de una carga mira **el pasado**: muestra cómo
  entró ese grupo, no cómo está hoy. Si alguien se dio de baja, ya no aparece.
  Vale que la pantalla lo diga, para que no se lea como un registro histórico
  fiel de lo que se cargó.
