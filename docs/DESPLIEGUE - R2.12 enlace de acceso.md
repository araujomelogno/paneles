# Despliegue — R2.12 · El enlace de acceso se puede volver a generar

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Requerimiento:** R2.12 — Gestión de usuarios de la app (`PRD_gestion_de_paneles_detallado.md`)
**Migración que trae:** `db/boveda/0015_catalogo_acciones_usuario.sql`
**Duración estimada:** 30 a 40 minutos · **sin ventana de caída**: cada paso deja el sistema funcionando

---

## Lo primero: esto no se despliega solo

La `0015` lee la columna `usuario_auditoria.sistema`, que **agrega la `0014` de
la Fase 5**. Y las migraciones se aplican en orden. Así que si la Fase 5 todavía
no está en producción —que es el caso hoy—, este despliegue **es el de la Fase 5
más la `0015` al final**.

Este documento lo lleva de punta a punta, desde la base tal como está hoy. No
hace falta leer ningún otro manual para ejecutarlo. `DESPLIEGUE - COLOQUIO Fase
0.md` sigue siendo la referencia para entender **por qué** cada cosa de la Fase 5
es como es, y es obligatorio leerlo el día que COLOQUIO se conecte de verdad;
para hoy, alcanza con esto.

### El camino, en una mirada

| # | Paso | Dónde |
|---|---|---|
| 1 | Abrir los dos túneles y exportar los DSN | Terminal |
| 2 | Ver en qué estado está cada base | Terminal |
| 3 | Chequeo previo: textos de consentimiento | Bóveda |
| 4 | Aplicar `boveda/0012` y `boveda/0013` | Bóveda |
| 5 | Aplicar `semantica/0005` | Semántica |
| 6 | Crear el rol `coloquio_app` | Bóveda |
| 7 | Aplicar `boveda/0014` | Bóveda |
| 8 | Comprobar el registro de consumidores | Bóveda |
| 9 | Aplicar `boveda/0015` | Bóveda |
| 10 | Verificar el esquema completo | Terminal |
| 11 | Comprobar la base a mano | Bóveda |
| 12 | Desplegar funciones y hosting | Firebase |
| 13 | Probar en la aplicación | Navegador |

**Los pasos 1 a 11 y el rollback están ensayados** contra un Postgres levantado
al estado actual de producción; las salidas que aparecen abajo son las que dio
ese ensayo, no ejemplos escritos a mano. Los pasos 12 y 13 son de Firebase y del
navegador, y no se pueden ensayar desde el repositorio.

### Antes de empezar

- El repositorio en `main`, actualizado (`git pull`).
- `gcloud` autenticado contra `gestion-paneles`, con permiso de lectura sobre
  los secretos (`roles/secretmanager.secretAccessor`).
- `cloud-sql-proxy`, `psql` y `python3` disponibles.
- Nadie usando Configuración → Usuarios durante el paso 9, por prolijidad.

---

## Paso 1 · Abrir los dos túneles y exportar los DSN

```bash
cd <el repositorio>

cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-boveda    --port 5432 &
cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433 &

export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
```

**Comprobar que los dos DSN quedaron cargados** antes de seguir. Un DSN vacío no
da error: `psql` cae en sus valores por omisión y termina hablándole a otra base.

```bash
psql "$DSN_BOVEDA"    -tAc "select current_database()"   # → paneles_boveda
psql "$DSN_SEMANTICA" -tAc "select current_database()"   # → paneles_semantica
```

Si alguno responde otra cosa, o `psql` se queja del socket local, volvé acá: el
resto del documento supone estas dos variables bien puestas.

---

## Paso 2 · Ver en qué estado está cada base

```bash
python3 scripts/verificar_esquema.py
```

Partiendo de hoy, tiene que decir que **faltan cinco migraciones**:

```
    ✓ 0011_fase4_inteligencia.sql
    ✗ 0012_fase5_catalogo_finalidades.sql
        falta finalidad_consentimiento  (el catálogo de finalidades; sin él no se puede otorgar ninguna)
        …
    ✗ 0013_fase5_finalidades_cualitativo.sql
    ✗ 0014_fase5_superficie_externa.sql
    ✗ 0015_catalogo_acciones_usuario.sql
        falta accion_usuario
        falta v_usuario_auditoria

  semantica
    ✗ 0005_fase5_prohibicion_pii.sql

Faltan 5 migración(es).
```

Este paso es el que dice dónde estás parado. Si alguna de las cinco ya figura en
**✓**, saltá su paso y seguí con el siguiente: todas son idempotentes en el
sentido que importa —reaplicar una ya aplicada falla sin dejar nada a medias—,
pero no hay por qué correrla dos veces.

---

## Paso 3 · Chequeo previo: textos de consentimiento

**Este es el único paso que puede obligar a posponer el despliegue**, así que va
antes de tocar nada.

```bash
psql "$DSN_BOVEDA" -c "
select distinct c.finalidad, c.version_texto
  from consentimiento c
 where c.estado = 'vigente'
   and not exists (select 1 from texto_consentimiento t
                    where t.finalidad = c.finalidad
                      and t.version = c.version_texto and t.activo);"
```

**Esperado:**

```
 finalidad | version_texto
-----------+---------------
(0 rows)
```

**Si devuelve filas, no sigas.** Cada fila es un consentimiento vigente que
apunta a una versión de texto que nunca se publicó, y la `0013` se va a negar a
aplicar por eso. Antes hay que publicar esos textos —Panelistas → Inscripciones
→ Textos de consentimiento, o por SQL:

```sql
insert into texto_consentimiento (finalidad, version, cuerpo, activo)
values ('contacto_participacion', '<la versión que devolvió la consulta>',
        '…el texto real…', true);
```

Después volvé a correr la consulta hasta que dé cero filas.

---

## Paso 4 · Aplicar `boveda/0012` y `boveda/0013`

```bash
for m in 0012_fase5_catalogo_finalidades 0013_fase5_finalidades_cualitativo; do
  psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction -f "db/boveda/${m}.sql"
done
```

**Esperado:** una lista de `CREATE TABLE`, `ALTER TABLE`, `CREATE VIEW`,
`CREATE FUNCTION`, `CREATE TRIGGER` y ningún `ERROR`.

> **`--single-transaction` no es opcional en ningún paso de este documento.**
> Sin él, una migración que falla a mitad deja confirmado lo que ya ejecutó y
> hay que reconstruir el estado a mano. Con él, revierte entera.

---

## Paso 5 · Aplicar `semantica/0005`

Es la otra base. Va contra `$DSN_SEMANTICA`, no contra la bóveda.

```bash
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/semantica/0005_fase5_prohibicion_pii.sql
```

**Esperado:** termina con `CREATE EVENT TRIGGER` y un `DO`. Desde este momento la
Regla dura #1 la hace valer la base: un `alter table` que intente meter una
columna de PII del lado semántico se rechaza en el acto.

---

## Paso 6 · Crear el rol `coloquio_app`

La `0014` le otorga privilegios a este rol, y **se niega a aplicar si no
existe**. Como COLOQUIO todavía no está listo, se crea el rol **sin `LOGIN` y sin
miembros**: la superficie externa queda creada e inalcanzable.

```bash
psql "$DSN_BOVEDA" -c "create role coloquio_app;"
```

**Esperado:** `CREATE ROLE`.

> **Por qué así, y no con `gcloud sql users create`.** Ese camino otorga
> `cloudsqlsuperuser`, y con él `CREATEROLE`: el rol podría devolverse a sí mismo
> todo lo que la `0014` le niega. Un rol de grupo sin `LOGIN` no se conecta a
> nada, así que no hay nada que endurecer todavía.
>
> **Si la cuenta de servicio de COLOQUIO ya existe**, entonces este paso es otro:
> seguí §3.0 a §3.3 de `DESPLIEGUE - COLOQUIO Fase 0.md` —incluido el flag
> `cloudsql.iam_authentication`, que reinicia la instancia— y volvé acá al
> paso 7.

---

## Paso 7 · Aplicar `boveda/0014`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/boveda/0014_fase5_superficie_externa.sql
```

**Esperado:** termina con varios `GRANT` y un `DO`, sin `ERROR`.

Si en vez de eso aparece `ERROR: No existe el rol` + `coloquio_app`, es que el
paso 6 no se ejecutó contra esta base. Volvé al 6.

---

## Paso 8 · Comprobar el registro de consumidores

```bash
psql "$DSN_BOVEDA" -c "select codigo, rol_bd, activo from sistema_consumidor order by codigo;"
```

**Esperado — y prestale atención a la `f` de la última columna:**

```
  codigo  |    rol_bd    | activo
----------+--------------+--------
 coloquio | coloquio_app | f
 paneles  | app_paneles  | t
(2 rows)
```

`coloquio` entra **inactivo**, y así se queda hasta que COLOQUIO salga a
producción. Mientras esté en `f` no recibe pendientes de baja, que es
exactamente lo que se quiere de un consumidor que todavía no existe.

---

## Paso 9 · Aplicar `boveda/0015` — la de R2.12

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/boveda/0015_catalogo_acciones_usuario.sql
```

**Esperado**, en este orden: `CREATE TABLE`, `COMMENT`, `INSERT 0 6`, dos
`ALTER TABLE` y `CREATE VIEW`.

Crea dos cosas: `accion_usuario` (el catálogo de las seis acciones auditables,
que reemplaza al `check` viejo) y `v_usuario_auditoria` (la auditoría con la
etiqueta de cada acción ya resuelta, que es lo que lee la pantalla).

---

## Paso 10 · Verificar el esquema completo

```bash
python3 scripts/verificar_esquema.py
```

**Esperado, textual:**

```
PII del lado semántico
  ✓ ninguna migración declara una columna de PII

Las dos bases están al día.
```

El comando sale con código 0 cuando está todo. Si todavía muestra alguna en
**✗**, andá al paso que le corresponde; no sigas al 12 con esto en rojo.

---

## Paso 11 · Comprobar la base a mano

Tres consultas que confirman que la `0015` quedó como tiene que quedar.

```bash
# 1 · Las seis acciones, con la nueva al final
psql "$DSN_BOVEDA" -c "select codigo, etiqueta from accion_usuario order by orden;"
```

```
    codigo     |     etiqueta
---------------+------------------
 alta          | Alta
 cambio_rol    | Cambio de rol
 actualizacion | Actualización
 desactivacion | Desactivación
 reactivacion  | Reactivación
 enlace_acceso | Enlace de acceso
(6 rows)
```

```bash
# 2 · El `check` salió y entró la FK
psql "$DSN_BOVEDA" -tAc "
select conname||' → '||pg_get_constraintdef(oid) from pg_constraint
 where conrelid='usuario_auditoria'::regclass and conname like '%accion%';"
```

```
usuario_auditoria_accion_fk → FOREIGN KEY (accion) REFERENCES accion_usuario(codigo)
```

```bash
# 3 · La superficie externa está creada pero es inalcanzable
psql "postgresql://coloquio_app@127.0.0.1:5432/paneles_boveda" -c "select 1"
```

```
psql: error: … FATAL:  role "coloquio_app" is not permitted to log in
```

Ese error **es el resultado correcto** del paso 6: el rol existe, la `0014` le
otorgó lo suyo, y nadie puede usarlo.

---

## Paso 12 · Desplegar funciones y hosting

```bash
firebase deploy --only functions,hosting
```

No hay secretos nuevos en esta entrega: la lista `SECRETOS` de
`functions/main.py` no cambia, así que no hay nada que declarar ni que guardar en
Secret Manager antes de desplegar.

---

## Paso 13 · Probar en la aplicación

En orden, y con una cuenta que tenga el permiso `gestionar_usuarios`:

1. **Configuración → Usuarios.** La tabla de auditoría de abajo tiene que
   listar. Si acá falla, la `0015` no se aplicó: esa consulta lee
   `v_usuario_auditoria`.
2. **Dar de alta a una persona de prueba** y cerrar el modal **sin** copiar el
   enlace. (Así era como quedaba trabada antes.)
3. En su fila del padrón, apretar **«Enlace de acceso»**. Tiene que abrir el
   modal con un enlace nuevo y el aviso de que no queda guardado.
4. La auditoría de abajo tiene que sumar una fila **«Enlace de acceso»**, con tu
   usuario y la fecha. Con la etiqueta en castellano, no con el código.
5. **Desactivar** a esa persona: el botón queda deshabilitado y explica por qué
   al pasar el mouse. (Un enlace para alguien desactivado no sirve de nada, y
   haría creer que el problema se resolvió.)
6. Borrar la persona de prueba, si corresponde.

---

## Si algo sale mal

| Lo que ves | Qué pasó | Qué hacer |
|---|---|---|
| `column a.sistema does not exist` al aplicar la `0015` | Se salteó la `0014` | Hacé los pasos 6 y 7, y repetí el 9. Con `--single-transaction` la `0015` no dejó nada creado: se comprobó |
| `ERROR: No existe el rol …coloquio_app…` (el mensaje viene con `\n` literales, es así) | Falta el paso 6 | Paso 6 y repetí el 7 |
| `Hay consentimientos vigentes cuya versión de texto no está publicada y activa: …` | El paso 3 no se hizo, o se hizo y quedaron filas | Publicá esos textos y repetí el paso 4. La `0013` no dejó nada a medias |
| Configuración → Usuarios no lista la auditoría | Se desplegaron las funciones sin aplicar la `0015` | Paso 9. No hace falta volver a desplegar |
| `psql: connection to server on socket "/tmp/.s.PGSQL.5432" failed` | El DSN está vacío, o el túnel se cayó | Paso 1, incluida la comprobación de `current_database()` |
| Una migración falla por otra cosa | — | No la reintentes a mano por partes: corregí la causa y volvé a correr el archivo entero. Las migraciones son versionadas y no se editan en la base |

---

## Volver atrás

La `0015` es reversible **sin perder auditoría**, que es lo que importa: no se
toca ninguna fila, solo cambia la restricción que las valida. Ensayado, con una
fila `enlace_acceso` ya escrita: sobrevive.

```sql
begin;
  drop view if exists v_usuario_auditoria;
  alter table usuario_auditoria drop constraint if exists usuario_auditoria_accion_fk;
  -- El `check` vuelve CON el código nuevo adentro, a propósito: las filas
  -- `enlace_acceso` que se hayan escrito tienen que seguir siendo válidas.
  -- Borrar auditoría para poder revertir es justo lo que esta tabla existe
  -- para impedir.
  alter table usuario_auditoria
    add constraint usuario_auditoria_accion_check
        check (accion in ('alta','cambio_rol','desactivacion',
                          'reactivacion','actualizacion','enlace_acceso'));
  drop table if exists accion_usuario;
commit;
```

Después, redesplegar la versión anterior de las funciones. La `0015` se puede
volver a aplicar encima de este rollback sin tocar nada: también está ensayado.

**La Fase 5 no se revierte con esto**, y no debería: las `0012`–`0014` son
aditivas y dejan el sistema funcionando.

---

## Checklist

- [ ] **1** · Túneles abiertos y los dos DSN comprobados con `current_database()`
- [ ] **2** · `verificar_esquema.py` corrido: sé qué falta
- [ ] **3** · El chequeo de textos de consentimiento devuelve **cero filas**
- [ ] **4** · `boveda/0012` y `boveda/0013` aplicadas
- [ ] **5** · `semantica/0005` aplicada
- [ ] **6** · Rol `coloquio_app` creado, **sin `LOGIN`**
- [ ] **7** · `boveda/0014` aplicada
- [ ] **8** · `sistema_consumidor` tiene `coloquio` con `activo = f`
- [ ] **9** · `boveda/0015` aplicada
- [ ] **10** · `verificar_esquema.py`: «Las dos bases están al día»
- [ ] **11** · Seis acciones, la FK en lugar del `check`, y `coloquio_app` no puede conectarse
- [ ] **12** · `firebase deploy --only functions,hosting`
- [ ] **13** · Las seis comprobaciones en la aplicación
