# Despliegue — superficie de selección cualitativa (COLOQUIO)

**Addendum:** `specs/ADDENDUM_paneles_superficie_cuali.md` (R5.9 a R5.13) ·
**Decisión:** D84
**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0033`**.

**Qué cambia:** una migración en la bóveda, la `0034`, **aditiva**. Agrega
una vista y cuatro funciones a la superficie de COLOQUIO, y el motivo de
auditoría `seleccion_cuali`. También cambia `verificar_coloquio.py` (lista
blanca y seis chequeos), más el espejo del motivo en `auditoria.py` y el
registro de esquema.

No se toca ninguna vista ni función existente: `f_persona_convocable()`,
`v_persona_convocable`, `declarar_convocatoria()` y
`contacto_para_convocatoria()` quedan igual.

**Qué NO cambia:** ningún secreto, variable de entorno, rol de base, permiso
de IAM, cola ni pantalla. El código de `paneles` no llama a nada de esto:
lo usa COLOQUIO.

**COLOQUIO: hay que avisarle**, porque es para ellos. Desde la `0034` tiene:

| Pieza | Para qué |
|---|---|
| `v_atributo_catalogo` | El vocabulario: atributos activos, con `es_especial` y sus categorías |
| `f_persona_convocable_filtrada(finalidad, filtros jsonb, límite)` | Convocables por cualquier atributo. Una clave, un operador o una categoría desconocidos son **error** |
| `atributos_de_personas(ids, momento)` | Los atributos de hasta 500 personas, para las columnas |
| `ficha_candidato(id, momento)` | La ficha seudónima, sin PII y sin auditoría |
| `identificar_candidatos(ids, referencia, actor)` | Nombre, email y celular de hasta 200 candidatos de una sesión. Audita una fila por persona con motivo `seleccion_cuali` |

Dos cosas del contrato para recordarles:

- **`referencia` y `actor` son obligatorios.** La referencia es el id de la
  sesión de COLOQUIO, y el actor es el email de la persona humana que lo
  pidió.
- **Se llama sobre los tildados, no en cada búsqueda.** La auditoría lo va a
  mostrar.

### Orden en una línea

`0034` → verificar esquema → batería de COLOQUIO → avisar a COLOQUIO.

No hace falta desplegar código de `paneles` para que COLOQUIO lo use. El
cambio de `auditoria.py` (el espejo del motivo) va con el próximo
despliegue.

---

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

## Paso 2 · Aplicar `boveda/0034`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0034_superficie_seleccion_cuali.sql
```

Es una sola transacción y es re-ejecutable: aplicarla dos veces deja todo
igual (DoD 1).

Qué hace:

- Agrega el motivo `seleccion_cuali` a `motivo_reidentificacion`
  (`on conflict do nothing`).
- Crea `v_atributo_catalogo` y las cuatro funciones. Las funciones son
  `security definer` y cada una reaplica el gate adentro
  (`v_persona_convocable`).
- Hace primero `revoke … from public` y después `grant` a `coloquio_app`.

## Paso 3 · Verificar

```bash
python3 scripts/verificar_esquema.py
python3 scripts/verificar_coloquio.py
```

- Sin la `0034`, el primero nombra como faltantes `v_atributo_catalogo` y
  las cuatro funciones.
- El segundo tiene que dar **24 de 24**. Las seis nuevas:
  - lee el catálogo de atributos;
  - la búsqueda por atributo respeta el gate;
  - los atributos son solo del gate;
  - la ficha del candidato no identifica;
  - identificar queda auditado por sesión;
  - identificar no habilita el contacto.

  Si la migración no se aplicó, fallan las dos de privilegios efectivos: la
  lista blanca promete algo que el rol no tiene.

El escenario de la batería crea personas nacidas en 1901: una edad derivada
de más de 120 años aísla al escenario aunque la bóveda tenga decenas de
miles de convocables. Se borran al final, como siempre.

## Paso 4 · Avisar a COLOQUIO

Con la tabla de arriba y el addendum.

---

## Qué mirar después (§7 del addendum)

**Volumen de `seleccion_cuali`, por usuario y por sesión.** Si se parece al
tamaño de los resultados y no al de las listas cortas, el tercer límite de
D84 no se está respetando y la auditoría dejó de servir.

```sql
select actor_email,
       contexto ->> 'referencia' as sesion,
       count(*)                   as personas_identificadas,
       min(creado_en), max(creado_en)
  from reidentificacion
 where motivo = 'seleccion_cuali'
 group by 1, 2
 order by 3 desc
 limit 50;
```

**Latencia de `f_persona_convocable_filtrada`.** En el cluster de pruebas,
con 20.000 convocables y la caché caliente:

| Pedido | Tiempo |
|---|---|
| Un atributo | ≈ 0,45 s |
| Tres atributos, uno derivado | ≈ 0,6 s |
| `atributos_de_personas` de 200 | ≈ 0,6 s |

Casi todo es evaluar `v_persona_convocable`. Medirlo contra producción con
el panel completo:

```sql
\timing on
select count(*) from f_persona_convocable_filtrada('contacto_participacion',
  '{"sexo": {"en": ["F"]}, "edad": {"min": 35, "max": 44}}', 5000);
```

**Retención de `reidentificacion`.** Crece con filas cortas. Conviene
fijarle retención antes de que importe.

---

## Pruebas

```bash
source scripts/pg_pruebas.sh
cd functions && python3 -m pytest -q tests/test_superficie_seleccion_cuali.py
```

`tests/test_superficie_seleccion_cuali.py` tiene 28 pruebas. Corren como
`coloquio_app` donde importa, y cubren:

- la migración dos veces, y el motivo en el catálogo y en su espejo;
- el catálogo sigue al vocabulario vivo: un atributo nuevo aparece y uno
  desactivado desaparece;
- **un atributo creado después de la migración se filtra de punta a punta**
  (DoD 4), con la unión dentro de un atributo y la intersección entre
  atributos;
- los derivados: la edad por `min` y el tramo por `en`;
- siete formas de filtro inválido dan error;
- un atributo dado de baja y una finalidad inventada también dan error;
- nadie fuera del gate aparece: sin la finalidad, sin consentimiento, en la
  lista negra;
- `atributos_de_personas` devuelve solo las filas que existen, 200 ids van
  en una llamada y 501 fallan;
- la ficha no trae ningún campo de `pii.CAMPOS_PII` ni escribe auditoría;
- **veinte candidatos dejan veinte filas** `seleccion_cuali` / `coloquio`
  con la sesión y el actor (DoD 5);
- el gate al identificar, sin referencia o sin actor falla, y con 201 ids
  falla;
- **`contacto_para_convocatoria()` sigue negándose sin convocatoria**
  (DoD 6);
- `coloquio_app` no lee `persona_atributo`, `f_atributo_persona` ni el
  catálogo crudo.

Ninguna prueba existente se modificó (DoD 2).

---

## Volver atrás

Es aditiva. Si hace falta sacarla:

```sql
begin;
drop function if exists identificar_candidatos(uuid[], text, text, text);
drop function if exists ficha_candidato(uuid, timestamptz);
drop function if exists f_persona_convocable_filtrada(text, jsonb, int);
drop function if exists atributos_de_personas(uuid[], timestamptz);
drop view if exists v_atributo_catalogo;
commit;
```

El motivo `seleccion_cuali` se deja en el catálogo: las filas de auditoría
que ya lo usan lo siguen necesitando para mostrarse con su etiqueta. Si se
saca la migración, también hay que volver atrás
`scripts/verificar_coloquio.py`: si no, su lista blanca promete cinco piezas
que el rol ya no tiene.
