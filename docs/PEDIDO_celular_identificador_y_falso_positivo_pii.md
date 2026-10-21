# PEDIDO — Celular como identificador, falso positivo de datos personales, y borrar una carga

**Sistema:** Gestión de paneles · carga de panelistas desde SAV
**Severidad:** Media — ninguno de los dos rompe una carga, pero los dos
confunden en el paso de revisión
**Detectado:** 2026-10-20, cargando una base con el celular como identificador
**Estado:** Para corregir

---

## 1. «Trae el celular» como tipo de identificador

### Qué pasa

En **«2 · Columna que identifica al respondente»**, el selector de tipo ofrece:

- Trae el id de la plataforma de campo
- Trae el id_persona del sistema (precargado)
- Trae el documento
- Trae el correo

**No ofrece el celular**, aunque desde el despliegue de «celular como clave de
dedup» el celular es la tercera clave de `dedup.resolver`, después de documento
y correo.

### Por qué hoy no rompe, y dónde sí falla

**En modo «crear los individuos» no hace falta:** el dedup lee el celular de los
datos mapeados de cada persona (`datos.get("celular")`, normalizado a E.164), así
que basta con marcar la variable como `celular` en los demográficos. Ese camino
funciona.

**En modo «los panelistas ya existen» sí falla:** si la base trae como único
identificador el celular —caso habitual en un panel que trabaja por teléfono y
WhatsApp—, no hay forma de hacer coincidir cada fila con un panelista existente.
Elegir «id de la plataforma» buscaría el número en `alias_origen`, y solo lo
encontraría si esa misma plataforma lo registró antes.

### Qué se pide

- [ ] Agregar **«Trae el celular»** al selector de tipo de identificador.
- [ ] En modo «ya existen», resolver la fila contra `persona.celular`
      **normalizado a E.164**, del mismo modo que hoy se resuelve el documento o
      el correo.
- [ ] Aplicar **las mismas cautelas que el dedup**: si el número corresponde a
      **más de una persona**, la fila **no se asigna** a ninguna; se informa
      como ambigua. Un celular puede ser de un hogar, no de una persona.
- [ ] Un valor que no se puede normalizar (`"solo WhatsApp 095093112"`,
      `"...."`, `"1"`) se informa como `formato_invalido`, igual que un
      `id_persona` mal formado.

> **La coherencia que importa.** Hoy el celular vale como clave para *crear*
> personas sin duplicarlas, pero no para *encontrarlas*. Si el sistema confía en
> él para lo primero, tiene que poder usarlo para lo segundo, con las mismas
> reglas.

---

## 2. El detector de datos personales revisa la columna de identidad

### Qué pasa

Con `var7O11` (el celular) elegida como **columna que identifica al
respondente**, el paso de revisión muestra:

> **Posibles datos personales en var7O11** — 593 respuesta(s) de «var7O11» tienen
> algo con forma de cédula, correo, teléfono. […] decidí si esta variable entra.
> *Excluir la variable · Ingestarla igual, a conciencia*

con nivel **«rompe»**.

### Por qué es un falso positivo

La advertencia de R8.6 existe para evitar que **una pregunta abierta** lleve
datos personales al **store semántico**. Pero la columna de identidad **nunca va
al store semántico**: el despivote la saltea (`if codigo == columna_id:
continue`), y si además está marcada como `celular` en los demográficos, la
regla de R3.9.d también la excluye.

O sea: el detector está advirtiendo sobre algo que no puede pasar.

### Por qué importa más de lo que parece

**Enseña a ignorar la advertencia.** Una alarma que salta sobre la columna que
*obviamente* tiene el teléfono —la que el propio analista eligió como
identificador— acostumbra a responder sin leer. El día que la advertencia sea
real —una pregunta abierta de «Otro, especificar» donde alguien escribió su
número—, va a pasar inadvertida. **El valor de esta advertencia depende de que
no salte de más.**

**Y la acción ofrecida es engañosa.** «Ingestarla igual, a conciencia» sugiere
que la variable iría al store semántico. No va. Si alguien la elige creyendo que
hace falta para usarla como identificador, no pasa nada —pero aprendió algo
falso sobre cómo funciona el sistema—.

### Qué se pide

- [ ] El detector de R8.6 **saltea**:
  - la **columna de identidad** elegida en el paso 2;
  - las variables marcadas como **demográficas o de identidad y contacto**
    (`nombre`, `documento`, `email`, `celular`, `contacto`), que por R3.9.d
    nunca van al store semántico.
- [ ] Si esa marca se agrega **después** de que el detector corrió, la
      advertencia correspondiente **desaparece**: el resumen se recalcula con
      la configuración vigente.
- [ ] El detector **sigue** revisando todo lo que **sí** va a ir al store
      semántico, en particular las abiertas de «Otro, especificar».

> **El criterio general.** Un chequeo de calidad tiene que mirar **lo que se va
> a ingestar**, no todo lo que hay en el archivo. Analizar el archivo entero
> produce advertencias sobre columnas que el propio flujo ya descartó.

---

## 3. Borrar una carga, con sus panelistas y respuestas

### Qué se pide

Desde el detalle de una carga en la sección **Cargas**, poder **borrarla**, y
que se borren con ella sus panelistas y sus respuestas. El caso típico: una
carga hecha con un mapeo equivocado, o de prueba, que no debería existir.

### El alcance correcto: qué se borra y qué no

**Una carga no es dueña de todos sus panelistas.** En modo «crear los
individuos», el dedup **reutiliza** a las personas que ya existían. El vínculo
`persona_carga` lo registra con `origen = 'creada'` o `'reutilizada'`. Una
persona reutilizada puede venir de otra carga, estar en paneles, tener respuestas
de otros estudios, consentimientos, puntos o participaciones. Borrarla por borrar
esta carga sería eliminar datos que no tienen nada que ver con ella.

Por eso:

| Qué | Qué pasa |
|---|---|
| **Respuestas de este estudio** (store semántico, por `ref_estudio`) | Se borran **todas**, de todas las personas |
| **Cuestionario y preguntas** de este estudio | Se borran |
| **Unidades de evidencia** que quedan **sin ninguna respuesta** | Se borran; las compartidas con otros estudios **se conservan** |
| `carga_tematica`, `tematica_pregunta`, `persona_tematica` de esta carga | Se borran |
| `persona_carga` de esta carga | Se borran |
| **Personas creadas por esta carga y sin ningún otro vínculo** | Se **dan de baja** (ver abajo) |
| **Personas reutilizadas**, o creadas aquí pero con otros vínculos | **Se conservan**; solo pierden su vínculo y sus respuestas de esta carga |
| Temáticas que quedan sin ninguna carga | Se conservan en el vocabulario; dejan de aparecer en el filtro por tener cero personas (R-TEM.14) |

**«Sin ningún otro vínculo»** significa: ninguna otra carga en `persona_carga`,
ninguna membresía en un panel que no venga de esta carga, ninguna participación
en encuestas, ninguna respuesta de otro estudio, ningún movimiento de puntos ni
canje.

> **Por qué así.** El pedido es «que se borren los panelistas de la carga». El
> riesgo es leerlo como «los panelistas **que aparecen** en la carga». Lo que se
> quiere es deshacer lo que **la carga trajo**: las personas que nacieron con
> ella y no tienen otra vida en el sistema. Las demás se quedan, sin lo que la
> carga les agregó.

### Las personas se borran por el camino de la baja

- [ ] El borrado de cada persona usa **el mismo mecanismo que la baja de un
      panelista**: genera el `borrado_pendiente` hacia los consumidores
      (COLOQUIO), para que el borrado les llegue.
- [ ] Queda registrado en la auditoría: quién borró la carga, cuándo, cuántas
      personas se dieron de baja, cuántas se conservaron y cuántas respuestas se
      eliminaron. La auditoría **no se borra** (`on delete set null`).

> **Por qué no un `delete` directo.** COLOQUIO pudo haber visto a esas personas
> —están en `v_persona_convocable` desde que se cargaron— y haber guardado su
> contacto. Un borrado que no le avisa deja datos de gente que ya no existe.

### Seguridad del borrado

- [ ] Antes de confirmar, una **vista previa** con los números: cuántas personas
      se darán de baja, cuántas se conservan y por qué, cuántas respuestas se
      borran.
- [ ] Confirmación fuerte: **escribir el nombre de la carga** para habilitar el
      botón.
- [ ] El texto dice explícitamente que es **irreversible**.
- [ ] **Bloqueado** si la carga tiene una ingesta en curso (la guarda de
      R-ING.2).
- [ ] Solo para roles con permiso de administración.

### Ejecución

El borrado toca **dos bases** sin una transacción común. Conviene que sea un
**trabajo reanudable**, como la ingesta diferida:

- [ ] Por pasos idempotentes y registrados: si se corta a mitad de camino, se
      reanuda desde donde quedó, sin dejar respuestas huérfanas ni vínculos
      colgados.
- [ ] Orden: primero el store semántico (respuestas, cuestionario, unidades
      huérfanas), después la bóveda (vínculos y bajas), y al final la carga.
      Si algo falla en el medio, lo que queda son personas sin respuestas de esa
      carga —recuperable con un reintento—, nunca respuestas que apuntan a
      personas inexistentes.
- [ ] Para cargas grandes, por la vía diferida (Cloud Tasks), con progreso
      visible.

> **Respaldo.** Antes de borrar una carga real con muchos panelistas, conviene
> tener un respaldo reciente de las dos bases. El borrado es correcto, pero no
> tiene vuelta atrás.

## 4. Definition of Done

**Celular como identificador**
- [ ] El selector ofrece «Trae el celular» (test).
- [ ] En modo «ya existen», una fila con el celular de un panelista se asigna a
      ese panelista (test).
- [ ] Un celular compartido por dos personas no se asigna y se informa como
      ambiguo (test).
- [ ] Un celular con formato inválido se informa como `formato_invalido`
      (test).
- [ ] Un celular escrito con espacios o sin `+598` encuentra igual al panelista,
      porque se compara normalizado (test).

**Detector de datos personales**
- [ ] La columna de identidad no genera la advertencia de datos personales,
      aunque tenga teléfonos o correos (test).
- [ ] Una variable marcada como `celular`, `email` o `documento` no la genera
      (test).
- [ ] Una abierta que **sí** va al store semántico y tiene teléfonos **sigue**
      generándola (test de no regresión).
- [ ] Marcar una variable como demográfica después del análisis hace desaparecer
      su advertencia (test).

**Borrar una carga**
- [ ] Una persona **creada** por la carga y sin otros vínculos se da de baja
      (test).
- [ ] Una persona **reutilizada** se conserva, sin su vínculo ni sus respuestas
      de esa carga (test).
- [ ] Una persona creada por la carga pero que **después** entró en otra carga o
      panel se conserva (test).
- [ ] Se borran todas las respuestas de ese estudio en el store semántico (test).
- [ ] Una unidad de evidencia compartida con otro estudio se conserva; una que
      queda sin respuestas se borra (test).
- [ ] La baja de cada persona genera el `borrado_pendiente` hacia COLOQUIO
      (test).
- [ ] El borrado queda auditado con sus conteos (test).
- [ ] No se puede borrar una carga con una ingesta en curso (test).
- [ ] Un borrado interrumpido se reanuda sin dejar respuestas huérfanas (test).
- [ ] La vista previa coincide con lo que finalmente se borra (test).

## 5. Costos

**US$ 0.** Es lógica de la carga y del borrado, sin infraestructura ni llamadas
a APIs. Borrar respuestas libera espacio en el store semántico, aunque Postgres
no lo devuelve al disco hasta un `vacuum` (se reutiliza en cargas siguientes).
