# Informe — Fase 7: qué quedó implementado y qué falta

**Sistema:** Gestión de paneles · `araujomelogno/paneles`
**Verificado contra:** código del repositorio, rama `main`, 2026-10-04
**Para:** Claude Code
**Método:** se cruzaron los requisitos de `specs/SPEC_fase7.md` con las rutas
declaradas en `functions/panel_api/ruteo.py` y con el consumo real en
`web/public/js/`.

---

## 1. Resumen: el backend está, el frontend falta

**El patrón es consistente en toda la fase.** Las rutas existen, están
declaradas con su `requisito="R7.x"`, y la lógica de servidor está escrita —en
algunos casos con bastante cuidado—. Lo que falta es que **las pantallas las
usen**: cuatro de los seis endpoints solo aparecen en `web/public/js/api.js`
(la capa que declara las llamadas) y en ninguna página.

| Req | Backend | Frontend | Estado |
|---|---|---|---|
| R7.1 · Versión de consentimiento | n/a | ⚠ a verificar | **Parcial** |
| R7.2 · Paso de revisión | ✅ `resumen_ingesta.py` | ✅ `solo_revisar` | **Completo** |
| R7.3 · Ficha desde resultados | ⚠ parcial | ❌ solo en `api.js` | **Falta UI + parte del backend** |
| R7.4 · Elegir columnas | ✅ 4 rutas | ❌ solo en `api.js` | **Falta UI** |
| R7.5 · Estadísticas | ✅ `/estadisticas` | ✅ `estadisticas.js` | **Completo** |
| R7.6 · Respuestas del panelista | ✅ 2 rutas | ❌ solo en `api.js` | **Falta UI** |

**Tres requisitos a medio camino (R7.3, R7.4, R7.6).** La API funciona y se
puede llamar con `curl`; ninguna pantalla la invoca, así que para el usuario el
requisito no existe.

---

## 2. Lo que está bien hecho (no tocar)

Vale decirlo para que no se rehaga.

**R7.2 — El paso de revisión está completo**, incluido el conteo de claves de
dedup. `resumen_ingesta.py` calcula valores distintos, filas que colisionan y
personas a crear, con un umbral de advertencia cuando una clave colisiona
demasiado. El comentario del módulo documenta el caso que motivó el requisito
(una variable de sí/no mapeada a `documento`).

**R7.4 y R7.6 — el backend cumple el requisito, verificado línea por línea.**
R7.4 excluye las categorías especiales de las columnas ofrecibles, resuelve los
atributos **en una sola consulta** sobre los `id_persona` ya en pantalla (sin
re-ejecutar la consulta semántica) y guarda las preferencias con `merge`.
R7.6 pagina en la base, trae procedencia y `texto_embebido`, y **no selecciona
el `embedding`** a propósito. En los dos casos **solo falta conectarlos a una
pantalla**.

**R7.5 — Las estadísticas están completas**, con la pantalla y la **brecha
entre stores** como eje, no como apéndice: panelistas, consentimiento, corpus,
salud, brecha y cargas.

---

## 3. Lo que falta, por requisito

### R7.3 — Ficha del panelista desde los resultados de consulta

**Backend: incompleto.** `GET /panelistas/<id_persona>/ficha` existe y
`ficha.seudonima()` devuelve lo correcto en materia de privacidad —ningún dato
identificatorio— pero **le falta la mitad del requisito**:

```python
return {
    "id_persona": …,
    "estado": …,
    "enrolado_en": …,
    "atributos": …,      # ✅ demográficos, con `momento` para R4.1.a
    "paneles": …,        # ✅
}                        # ❌ falta la evidencia del resultado
```

R7.3 pide, textual: *«Dada la ficha, entonces muestra también **la evidencia del
resultado**: qué respondió y de qué estudio, que es lo que ya trae el
ranking.»* Eso no está.

- [ ] La ficha debe incluir la **evidencia**: la respuesta que hizo que esa
      persona apareciera en el resultado, con su pregunta y su estudio.
- [ ] Como la evidencia depende de **cuál consulta** se está mirando, hay que
      decidir cómo llega: pasarla desde el resultado que ya la tiene (el ranking
      la devuelve), o recibir el criterio como parámetro del endpoint. Lo
      primero evita recalcular y es consistente con lo que el usuario está
      viendo.

**Y falta la UI:** la pantalla de consultas no llama al endpoint.

- [ ] En la lista de resultados, cada individuo es **clickeable** y abre la
      ficha en una capa emergente, sin perder el resultado ni re-consultar.
- [ ] La ficha muestra **atributos demográficos** con su procedencia (derivado,
      envejecido, cargado) y la **evidencia del resultado** (qué respondió y de
      qué estudio) — esto último requiere además el cambio de backend de arriba.
- [ ] **No muestra** nombre, documento, correo ni celular. Para eso está la
      reidentificación, que ya existe y queda auditada.
- [ ] Cerrar la ficha devuelve al resultado en el mismo estado.

### R7.4 — Elegir las columnas de la lista de resultados

**Backend:** existen las cuatro rutas —`GET /atributos/columnas`,
`POST /resultados/atributos`, `GET` y `PUT /mi/preferencias`—.
**Falta:** ningún selector en la pantalla de consultas.

- [ ] Un selector de columnas que lista los atributos disponibles.
- [ ] Las columnas elegidas **se recuerdan** entre consultas, usando
      `/mi/preferencias`.
- [ ] Un individuo sin valor muestra **«sin dato»**, no una celda vacía que se
      confunda con una categoría.
- [ ] Los atributos de **categoría especial** no se ofrecen como columna.
- [ ] Agregar una columna **no re-ejecuta la consulta**: los atributos se
      resuelven con `POST /resultados/atributos` sobre el conjunto ya obtenido.
- [ ] Las columnas por defecto se mantienen como hoy.

### R7.6 — Ver las respuestas procesadas de un panelista

**Backend:** existen `GET /panelistas/<id>/respuestas` y
`GET /panelistas/<id>/respuestas/estudios`, con el registro de auditoría con
motivo propio (`respuestas_panelista`) ya implementado.
**Falta:** la ficha del panelista no tiene la sección.

- [ ] En la ficha, una sección con la tabla: **código, texto de la pregunta,
      respuesta y procedencia** (estudio y fecha).
- [ ] **Paginada en la base**, no trayendo todo y recortando en el cliente.
- [ ] **Filtro por estudio** (usando `/respuestas/estudios`) y búsqueda por
      texto.
- [ ] Opción de ver el **texto embebido** (`"pregunta → respuesta"`).
- [ ] Un panelista sin respuestas muestra el mensaje correspondiente, no una
      tabla vacía.

> **Por qué el texto embebido no es un detalle.** Es la única forma de detectar
> que los códigos de una cerrada no se tradujeron: *«¿Qué marca fumás? →
> 11427»* en vez de *«→ Nevada»*. Esa respuesta está en la base, cuenta como
> respuesta, y es inútil para la búsqueda semántica. Sin esta vista no hay
> manera de verlo.

### R7.1 — Elegir la versión del consentimiento

**Estado: a verificar.** En `panelistas.js` hay una función `VERSION(finalidad,
contenedor)` que resuelve la versión por finalidad, y el alta ya valida que
exista texto publicado antes de enviar, con un mensaje que apunta a dónde
publicarlo. Eso cubre buena parte del requisito.

Queda por confirmar, y completar si no está:

- [ ] Que sea un **desplegable** y no un campo de texto, en **todos** los
      puntos donde se declara la versión: alta manual **y** evidencia de
      consentimiento en la importación.
- [ ] Que se pueda **ver el texto completo** de la versión elegida antes de
      confirmar.
- [ ] Que si hay **una sola** versión activa, venga preseleccionada.

---

## 4. Verificación sugerida

Para confirmar que lo que falta es solo la interfaz, cada endpoint responde hoy
con `curl` y un token de Firebase:

```bash
TOKEN="…"   # await firebase.auth().currentUser.getIdToken() en la consola
BASE="https://api-igvukn3rwq-rj.a.run.app/api"
ID="…"      # select id_persona from persona limit 1;

curl -s "$BASE/panelistas/$ID/ficha"              -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/panelistas/$ID/respuestas?limite=5" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/atributos/columnas"                 -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/mi/preferencias"                    -H "Authorization: Bearer $TOKEN"
```

Si devuelven datos, la tarea es exclusivamente de frontend.

---

## 5. Orden sugerido

1. **R7.6** — la sección de respuestas en la ficha. Es la que más valor
   inmediato tiene con el corpus ya cargado: permite ver si los textos
   embebidos quedaron bien antes de calibrar.
2. **R7.3** — la ficha desde resultados. **Incluye trabajo de backend**
   (agregar la evidencia), no solo de interfaz. Reutiliza buena parte de R7.6.
3. **R7.4** — el selector de columnas.
4. **R7.1** — verificar y completar lo que falte del desplegable.

## 6. Nota sobre el proceso

Dos de los requisitos incompletos (R7.4 y R7.6) tienen el mismo patrón: **el
backend quedó terminado y declarado, y la pantalla no se conectó**. R7.3 además
tiene el backend a medias: la ruta existe y está declarada con su requisito,
pero no devuelve todo lo que el requisito pide.

> **Que una ruta esté declarada con `requisito="R7.3"` no significa que cumpla
> R7.3.** Conviene contrastar lo que devuelve contra el texto del requisito, y
> no tomar la anotación como prueba de cumplimiento. Conviene que el criterio
de «implementado» para un requisito con interfaz incluya que **alguna página lo
use**, no solo que exista la ruta y esté declarada en `api.js`. Una ruta que
nadie llama es indistinguible de una ruta que no existe, desde el punto de vista
de quien usa el sistema.

- [ ] Para el Definition of Done de requisitos con pantalla: verificar que el
      endpoint aparezca en algún archivo de `web/public/js/paginas/`, no solo en
      `api.js`.
