# Despliegue — R2.12 · El enlace de acceso se puede volver a generar

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Requerimiento:** R2.12 — Gestión de usuarios de la app (`PRD_gestion_de_paneles_detallado.md`)
**Entrega:** PR #37
**Migración:** `db/boveda/0015_catalogo_acciones_usuario.sql`
**Precondición dura:** la `boveda/0014` aplicada. Ver §3 — **no se puede desplegar sola**

---

## 1 · Qué resuelve

El alta de un usuario devuelve un enlace para que la persona fije su clave, y
ese enlace se muestra **una sola vez**.

La mitad correcta de ese diseño es que **no se guarda**: un enlace de
restablecimiento guardado es una credencial guardada. Lo que estaba mal era la
otra mitad — «no se guarda» se había implementado como **«no se puede volver a
obtener»**, y son dos cosas distintas. La consecuencia operativa: quien daba el
alta y cerraba el modal antes de copiar dejaba a la persona nueva sin forma de
entrar, salvo que supiera usar «¿Olvidaste tu contraseña?» en una pantalla que
todavía no conoce.

**Ahora el padrón tiene un botón «Enlace de acceso» por fila.** No recupera el
anterior —no se guardó—: emite uno nuevo.

| | |
|---|---|
| **No baja el listón de seguridad** | Es la misma operación que «¿Olvidaste tu contraseña?» en el login, con la diferencia de que la pide un administrador |
| **Queda auditado** | Es lo único que distingue «le pasé el enlace al compañero que no podía entrar» de un intento de tomarle la cuenta a alguien. Se registra **que se generó**, nunca el enlace |
| **Un usuario desactivado no recibe enlace** | Se emitiría igual y la persona seguiría sin poder entrar, con lo que quien lo pidió creería haber resuelto algo que no resolvió. Primero se reactiva |
| **El enlace anterior sigue válido** | Generar uno nuevo no invalida el viejo: vence solo. Es el mismo comportamiento que el del login |

---

## 2 · Qué cambia en la base

`db/boveda/0015_catalogo_acciones_usuario.sql`:

| Objeto | Qué es |
|---|---|
| `accion_usuario` | El catálogo de acciones auditables. Reemplaza al `check` de `usuario_auditoria.accion` |
| `v_usuario_auditoria` | La auditoría con la etiqueta de cada acción ya resuelta |

Se retira el `check` y entra una FK a `accion_usuario`. Las filas históricas
usan los cinco códigos que la migración siembra, así que valida sin tocar nada;
si alguna tuviera un valor fuera del catálogo, **la migración falla ahí**, que
es lo que hay que querer.

> **Por qué un catálogo y no un `check` más largo.** Es el mismo razonamiento
> de la Fase 5 con las finalidades (D45). Dos motivos además de la acción
> nueva: agregar la siguiente sería otro `alter table` cada vez, y —lo que
> importa en este repo— **una migración que solo cambia una restricción es
> invisible para el diagnóstico de esquema**, así que `verificar_esquema.py` la
> daría por aplicada sin haberla mirado.

---

## 3 · La precondición que hay que leer antes de planificar la ventana

**Esta migración no se puede aplicar sola.** `v_usuario_auditoria` lee la
columna `usuario_auditoria.sistema`, que agrega la `boveda/0014` (Fase 5). Y
las migraciones se aplican en orden.

O sea: **el arreglo de usuarios queda detrás de toda la Fase 5.**

Si se intenta igual, falla así —conviene reconocer el mensaje—:

```
ERROR:  column a.sistema does not exist
LINE 4:        a.actor_uid, a.actor_email, a.detalle, a.sistema, a.c...
```

Con `--single-transaction` no deja nada a medias: se comprobó, cero objetos
creados. Alcanza con aplicar la `0014` y reintentar.

### 3.1 · Si hace falta este arreglo y COLOQUIO todavía no está listo

No hay que esperar a tener la cuenta de servicio de COLOQUIO. La `0014` solo
exige que el rol **exista**; no le importa cómo se creó ni si alguien puede
usarlo.

```sql
create role coloquio_app;   -- sin LOGIN y sin miembros
```

Con eso:

- La `0014` aplica. Comprobado: las quince migraciones de la bóveda pasan con
  el rol en `NOLOGIN`.
- La superficie externa queda creada e **inalcanzable**: intentar conectarse
  con ese rol da `FATAL: role "coloquio_app" is not permitted to log in`.
- COLOQUIO entra al registro con `activo = false`, así que tampoco recibe
  pendientes de baja.

El día que exista la cuenta de servicio se hacen los §3.1 y §3.3 de
`DESPLIEGUE - COLOQUIO Fase 0.md` y se la agrega como miembro del rol, sin
volver a tocar ninguna migración.

---

## 4 · Aplicar

Con el Auth Proxy abierto contra la bóveda, y **después** de la `0014`:

```bash
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"

psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction \
  -f db/boveda/0015_catalogo_acciones_usuario.sql
```

> **`--single-transaction` no es opcional.** Sin él, una migración que falla a
> mitad deja confirmado lo ya ejecutado y hay que reconstruir el estado a mano.

Después:

```bash
python3 scripts/verificar_esquema.py            # las dos bases al día
firebase deploy --only functions,hosting
```

---

## 5 · Verificar

### 5.1 · Desde la base

```sql
-- Las seis acciones, con la nueva.
select codigo, etiqueta from accion_usuario order by orden;

-- El `check` salió y entró la FK.
select pg_get_constraintdef(oid)
  from pg_constraint where conname like 'usuario_auditoria_accion%';
-- esperado: FOREIGN KEY (accion) REFERENCES accion_usuario(codigo)
```

### 5.2 · Desde la aplicación

1. **Configuración → Usuarios.** La tabla de auditoría tiene que listar. Si la
   `0015` no se aplicó y las funciones sí se redesplegaron, acá falla: la
   consulta lee `v_usuario_auditoria`.
2. **Dar de alta a alguien** y cerrar el modal **sin** copiar el enlace.
3. En su fila del padrón, **«Enlace de acceso»**. Tiene que abrir el modal con
   un enlace nuevo.
4. La auditoría de abajo tiene que sumar una fila **«Enlace de acceso»**, con
   tu usuario y la fecha.
5. **Desactivar** a esa persona: el botón queda deshabilitado, y explica por
   qué al pasar el mouse.

---

## 6 · Si hay que volver atrás

La migración es reversible y no pierde datos: la auditoría no se toca, solo
cambia la restricción que la valida.

```sql
begin;
  drop view if exists v_usuario_auditoria;
  alter table usuario_auditoria drop constraint if exists usuario_auditoria_accion_fk;
  -- Las filas `enlace_acceso` que hayan quedado no entran en el `check` viejo.
  -- Se conservan: borrar auditoría para poder revertir es exactamente lo que
  -- esta tabla existe para impedir. Por eso el `check` vuelve con el código
  -- nuevo adentro.
  alter table usuario_auditoria
    add constraint usuario_auditoria_accion_check
        check (accion in ('alta','cambio_rol','desactivacion',
                          'reactivacion','actualizacion','enlace_acceso'));
  drop table if exists accion_usuario;
commit;
```

Y redesplegar la versión anterior de las funciones. Ojo: el frontend viejo
etiqueta `enlace_acceso` como `enlace_acceso` a secas en la auditoría, porque
su diccionario no lo tenía. Es cosmético.

---

## 7 · Checklist

- [ ] `boveda/0014` aplicada (o el rol de grupo de §3.1 creado y la `0014` aplicada)
- [ ] `boveda/0015` aplicada con `--single-transaction`
- [ ] `select codigo from accion_usuario` devuelve seis filas, con `enlace_acceso`
- [ ] `python3 scripts/verificar_esquema.py` en verde
- [ ] Funciones y hosting redesplegados
- [ ] Configuración → Usuarios: la auditoría lista
- [ ] Alta → cerrar el modal → «Enlace de acceso» devuelve uno nuevo
- [ ] La auditoría suma la fila «Enlace de acceso»
- [ ] Con el usuario desactivado, el botón queda deshabilitado
