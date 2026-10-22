# Despliegue — clientes, lista negra y mejoras de Cargas, ficha y auditoría

**Spec:** `specs/SPEC_clientes_lista_negra_y_mejoras.md` · **Decisión:** D80
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0030`** (borrado de una carga,
PR #67).
**Qué cambia:** una migración en la bóveda (`0031`) y código de backend y de
frontend. El store semántico no cambia.
**Qué NO cambia:** ningún secreto, variable de entorno, rol de base, permiso
de IAM, cola ni configuración de función.
**Permisos de aplicación nuevos** (en `auth.py`):

| Permiso | Roles | Para qué |
|---|---|---|
| `gestionar_lista_negra` | admin, operaciones | Agregar y sacar de la lista negra; incluir a alguien de la lista al crear un panel desde una consulta |
| `ver_lista_negra` | admin, operaciones, dpo | La sección Lista negra, el motivo y el historial |

**COLOQUIO: hay que avisarle.** Desde la `0031`, quien está en la lista negra
**deja de aparecer en `v_persona_convocable`**. La vista tiene las mismas
columnas y la batería sigue en 18 de 18: COLOQUIO no tiene que cambiar nada.
Pero va a ver menos personas que antes, y conviene que lo sepa antes de que
lo note por su cuenta. Las tablas nuevas no tienen `grant`.

### Orden en una línea

`0031` → verificar esquema → batería de COLOQUIO → desplegar → comprobar.

---

## Qué trae

### 1 · Clientes de cada carga (R-CLI)

- Al crear una carga (Panelistas → *Cargar panelistas*), y después desde su
  detalle en **Cargas**, se indican sus **clientes**: para quién se hizo el
  estudio. Pueden ser varios. Se escriben con autocompletado sobre los que
  ya existen.
- El nombre se normaliza: «Antel», «ANTEL» y «antel» son el mismo, y una
  variante que difiere solo en tildes reutiliza la existente.
- Cada panelista **hereda** los clientes de las cargas en que estuvo. No se
  copian: corregir los de una carga se ve en todas sus fichas.
- **Consultas → + Cliente**: participó, o no participó, en estudios para
  esos clientes. «No participó» deja afuera a quien estuvo en **alguna**
  carga del cliente.
- **Columnas → Clientes** en los resultados.
- **Confidencial:** no llega al portal del panelista, ni a COLOQUIO, ni a
  las exportaciones.

### 2 · Lista negra (R-LN)

- **Agregar a la lista negra…** desde la ficha del panelista (en Panelistas
  o desde un resultado de consulta), con motivo obligatorio:
  - una categoría: calidad de respuestas, fraude o duplicación,
    comportamiento u otro;
  - un detalle breve de **hechos**.

  Queda quién lo hizo y cuándo.
- **Efectos:**

  | Dónde | Qué pasa |
  |---|---|
  | Encuestas | No se la convoca. El resumen de la convocatoria cuenta aparte a los que quedaron afuera por la lista. Sus invitaciones pendientes se cancelan al agregarla |
  | Muestreo y optimizador | No la eligen (ya leían la vista) |
  | COLOQUIO | No aparece en `v_persona_convocable` |
  | Resultados de consulta | Aparece **marcada**, no oculta. El motivo lo ven solo admin, operaciones y dpo |
  | Crear panel desde consulta | Queda afuera por defecto y se avisa cuántos. Quien gestiona la lista puede incluirlos |
  | WhatsApp | No se le escribe (motivo «está en la lista negra») |
  | Landing | Si se reinscribe y el dedup la reconoce, la inscripción va a revisión, marcada, y no se aprueba sin elegir a la persona |

- **No le quita el consentimiento.** Sus respuestas siguen entrando al store
  semántico y apareciendo en las consultas, y las estadísticas de
  consentimiento no la cuentan como «sin consentimiento».
- **Sección Lista negra** (menú lateral), paginada y con búsqueda.
  **Sacar…** pide un motivo y no borra la entrada: el historial queda
  completo.
- **El panelista no se entera** y no lo ve en su portal. El historial de la
  persona es lo que se le entrega en un pedido de acceso. La baja se lleva
  sus entradas, hasta que se decida otra cosa con asesoría.

### 3 · Mejoras

- **Panelistas pagina** (R-PAN.1): anterior y siguiente, «Página N de M · T
  panelistas», 25, 50 o 100 por página. Los filtros se conservan al pasar de
  página; cambiar uno vuelve a la primera.
- **De dónde vienen los reutilizados** (R-CAR.3): en Cargas, el número de
  reutilizados abre un modal. Muestra cuántos vienen de cada carga anterior,
  de mayor a menor, y la fila «sin carga previa: landing, alta manual o
  encuesta».
- **Cargas en la ficha** (R-CAR.4): reemplaza a «Origen» en la ficha de la
  bóveda. Muestra:
  - cada carga, la más reciente primero;
  - si fue creado o reutilizado, los clientes de la carga y las temáticas
    que respondió en ella;
  - si entró por la landing, un alta manual o una encuesta.
- **Reidentificaciones en Cumplimiento** (R-AUD.1): cada «ver quiénes son»,
  ficha abierta o exportación con datos, agrupado por evento. Muestra quién,
  cuándo, el motivo y a cuántas personas alcanzó, con la forma de ver
  cuáles. Se filtra por usuario, fechas y panelista. Solo admin y dpo.

### Rutas nuevas o cambiadas

| Ruta | Permiso | Qué hace |
|---|---|---|
| `GET /clientes?q=` | `leer` | El vocabulario, para autocompletar |
| `POST /cargas`, `PATCH /cargas/<id>` | `ingestar` | Aceptan `clientes: [nombres]` (deja exactamente esos) |
| `GET /cargas`, `GET /cargas/<id>` | `leer` | Traen `clientes` |
| `GET /cargas/<id>/reutilizados` | `leer` | El desglose (solo conteos) |
| `POST /panelistas/<id>/lista-negra` | `gestionar_lista_negra` | Agrega, con `categoria` y `detalle`. `409` si ya está |
| `POST /panelistas/<id>/lista-negra/remocion` | `gestionar_lista_negra` | Saca, con `motivo`. `409` si no está |
| `GET /panelistas/<id>/lista-negra` | `ver_lista_negra` | El historial completo |
| `GET /lista-negra` | `ver_lista_negra` | La sección: `q`, `desde`, `limite`, `removidas` |
| `GET /reidentificacion?eventos=1` | `cumplimiento` | Por evento, con `actor`, `desde`, `hasta`, `panelista`, `limite` y `desde_fila`. Sin `eventos`, la lista por fila de siempre |
| `POST /consultas`, `GET /consultas/ejecuciones/<id>` | — | Cada ítem trae `lista_negra` |
| `GET /panelistas/<id>`, `GET /panelistas/<id>/ficha` | — | Traen `lista_negra`. La de la bóveda trae también `cargas` |
| `GET /panelistas` | — | Cada fila trae `en_lista_negra` |
| `POST /encuestas/<id>/convocatoria` | — | Devuelve `en_lista_negra` (cuántos quedaron afuera) |
| `POST /paneles/desde-consulta` | — | Acepta `incluir_lista_negra` (exige `gestionar_lista_negra`) y devuelve `excluidos_lista_negra` |
| Consultas: criterio `{dimension: "cliente", operador: "in" \| "not_in", valor: [ids o nombres]}` | `consultar` | El filtro por cliente |

---

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

## Paso 2 · Aplicar `boveda/0031` (antes de desplegar el código)

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0031_clientes_y_lista_negra.sql
```

Qué hace:

- Crea `cliente`, `carga_cliente` y `lista_negra`, con RLS y sin `grant`.
- Crea la vista `v_persona_cliente`.
- Reemplaza `f_persona_convocable()` con la misma firma y una condición
  más: sin entrada activa en la lista negra. `create or replace` conserva
  sus privilegios, así que el `grant` a COLOQUIO sigue igual.

Salida en el ensayo:

```
BEGIN
CREATE TABLE
COMMENT
CREATE TABLE
CREATE INDEX
COMMENT
CREATE VIEW
COMMENT
CREATE TABLE
CREATE INDEX
COMMENT
ALTER TABLE
ALTER TABLE
ALTER TABLE
REVOKE
CREATE FUNCTION
COMMENT
COMMIT
```

Se puede correr dos veces: la segunda avisa que las tablas ya existen
(`NOTICE … already exists, skipping`) y termina en `COMMIT`.

Se ensayó sobre una copia de la base de pruebas: revertirla, comprobar que
no quedaron las tablas y que la función volvió a la `0021`, y aplicarla dos
veces. Después de cada pasada, `coloquio_app` seguía con `EXECUTE` sobre
`f_persona_convocable()` y `SELECT` sobre `v_persona_convocable`.

## Paso 3 · Verificar el esquema y la superficie de COLOQUIO

```bash
python3 scripts/verificar_esquema.py
python3 scripts/verificar_coloquio.py
```

- Sin la `0031`, el primero nombra como faltantes `cliente`, `carga_cliente`,
  `v_persona_cliente` y `lista_negra`.
- El segundo tiene que dar **18 de 18**. Si algún `grant` quedara abierto en
  las tablas nuevas, falla la lista blanca de privilegios.

## Paso 4 · Desplegar

```bash
firebase deploy --only functions,hosting
```

## Paso 5 · Comprobar

1. **Clientes.**
   1. Crear una carga de prueba con los clientes «Antel» y «ANTEL S.A.».
      Tienen que quedar `antel` y `antel s.a.`.
   2. En su detalle, cambiarlos y guardar.
   3. En Consultas, **+ Cliente** → «No participaron en estudios para…» →
      antel. Quien estuvo en esa carga no aparece.
2. **Lista negra**, con un panelista de prueba que tenga una invitación
   pendiente:
   1. Desde su ficha, **Agregar a la lista negra…** sin detalle: no deja.
   2. Con motivo: se agrega y avisa que se canceló la invitación.
   3. Convocar a su panel: el resumen dice «En lista negra: 1».
   4. Una consulta que lo incluya lo muestra con la marca.
   5. En la sección **Lista negra** aparece. **Sacar…** con motivo, y
      **Incluir las removidas** muestra la entrada cerrada.
   6. Por SQL, que dejó de ser convocable y volvió:

      ```sql
      select ln.id_persona, ln.categoria, ln.agregado_por, ln.removido_en,
             exists (select 1 from v_persona_convocable v
                      where v.id_persona = ln.id_persona) as convocable
        from lista_negra ln order by ln.id desc limit 5;
      ```

3. **Panelistas** pagina; **Cargas** → clic en «reutilizados» abre el
   desglose; la ficha muestra la tarjeta **Cargas**.
4. **Cumplimiento**, con admin o dpo: la tarjeta **Reidentificaciones** lista
   los eventos. Filtrar por el documento de alguien cuya ficha se acaba de
   abrir tiene que mostrar ese evento.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
pip install playwright        # para las pruebas de pantalla, en un Chromium
cd functions && python3 -m pytest -q
```

- `tests/test_clientes.py` (19 pruebas):
  - normalización y variantes, en la app y en la base, y puntuación
    conservada;
  - autocompletado;
  - alta y edición por la ruta;
  - derivación a cada panelista, sin copias;
  - filtro de incluir y excluir (excluir saca a quien estuvo en una carga
    que también es de otro cliente);
  - el criterio por la ruta de Consultas;
  - columna de resultados;
  - confidencialidad: sin privilegio para COLOQUIO, nada en el portal y
    nada en las exportaciones.
- `tests/test_lista_negra.py` (27 pruebas):
  - motivo obligatorio, en la ruta y en la base;
  - permisos de agregar, ver y sacar; no se agrega dos veces;
  - fuera de `v_persona_convocable`, también conectado como `coloquio_app`;
  - el contacto se corta y `uso_semantico` no;
  - las estadísticas no la cuentan como sin consentimiento;
  - la convocatoria la excluye y la cuenta aparte;
  - las invitaciones pendientes se cancelan y lo respondido y lo cerrado no;
  - WhatsApp;
  - resultados marcados, con y sin motivo según el rol;
  - las fichas y la grilla;
  - el panel desde consulta, por defecto y con `incluir_lista_negra`;
  - la reinscripción por la landing;
  - sacar con motivo y el historial completo;
  - la sección paginada y con búsqueda;
  - el portal no la muestra;
  - la baja se la lleva.
- `tests/test_cargas_ficha_y_auditoria.py` (16 pruebas):
  - el desglose de reutilizados, de mayor a menor, con «sin carga previa»;
    una carga posterior no cuenta, y no lista personas;
  - las cargas de la ficha, la más reciente primero, con clientes y
    temáticas por carga, y los ingresos por alta manual o landing;
  - los eventos de reidentificación, con sus filtros, paginación y permiso;
  - la paginación de Panelistas.
- `tests/test_pantallas_clientes_lista_negra.py` (7 pruebas, en Chromium):
  - la grilla que pagina, conserva filtros y vuelve a la primera;
  - el modal de la lista negra, que no cierra sin motivo;
  - el desglose de reutilizados y su enlace;
  - **+ Cliente**;
  - las reidentificaciones en Cumplimiento, con sus filtros, y que no
    aparecen sin permiso.

---

## Volver atrás

Primero, volver a desplegar el código anterior. **Después:**

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/revertir/boveda_0031.sql
```

- Vuelve `f_persona_convocable()` a la definición de la `0021`: quien estaba
  en la lista negra **vuelve a ser convocable**, también para COLOQUIO.
- Borra las tablas: **se pierden los clientes cargados y la lista negra con
  su historial**. Si hay que conservarlos, exportarlos antes:

  ```sql
  \copy (select c.id as carga_id, c.nombre as carga, cl.nombre as cliente
           from carga_cliente cc join carga c on c.id = cc.carga_id
           join cliente cl on cl.id = cc.cliente_id) to 'clientes.csv' csv header
  \copy (select * from lista_negra) to 'lista_negra.csv' csv header
  ```

- Las mejoras de Panelistas, Cargas, ficha y auditoría son solo código: se
  van con el despliegue anterior.
