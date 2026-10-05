# Corregir los dos chequeos defectuosos de `verificar_coloquio.py`

**Sistema:** Gestión de paneles · bóveda · verificación de la superficie externa
**Archivo:** `scripts/verificar_coloquio.py`
**Severidad:** Baja — no afecta al sistema, **sí a la confianza en la batería**
**Estado:** Para corregir

---

## 1. El problema

La batería reporta **15 de 17 en verde y 2 fallos**, y cierra con *«La bóveda no
está lista para COLOQUIO»*. Pero los dos fallos **no son defectos de la bóveda**:
uno es un test mal escrito y el otro no es verificable en este entorno.

Esto se arrastra desde la fase 0 y ya está documentado en §7.1.1 de
`DESPLIEGUE - COLOQUIO Fase 0.md`, pero nunca se corrigió. El costo no es
técnico sino de confianza: **una batería que falla siempre deja de leerse**. Ya
pasó: en el último despliegue los dos fallos se ignoraron por rutina, y si uno
de ellos hubiera sido real no se habría notado.

---

## 2. Chequeo 1 — «el contacto legítimo queda auditado»

### Qué reporta

```
✗ el contacto legítimo queda auditado
    el actor quedó como «coloquio-app@gestion-paneles.iam»:
    adentro de un `security definer` hay que usar `session_user`
```

### Por qué está mal el test, no la función

`contacto_para_convocatoria()` registra `coalesce(p_actor, session_user)`: usa
el actor que informa el llamador, y cae al rol de la conexión solo si no viene
ninguno. **Eso es correcto.**

El test llama a la función pasándole **la cuenta de servicio** como `p_actor`, y
después se queja de que el actor registrado es la cuenta de servicio. Está
verificando que la función ignore lo que él mismo le pasó.

El contrato está definido en `HANDOFF_coloquio_fase1.md`:

> **`p_actor` es el email del usuario humano de COLOQUIO** que pidió el
> contacto, no la cuenta de servicio.

### Qué hay que cambiar

- [ ] El test pasa como `p_actor` un **email de usuario** (p. ej.
      `analista@equipos.com.uy`), no la cuenta de servicio ni `None`.
- [ ] Verifica que **ese email** quede registrado en la auditoría.
- [ ] **La migración `0014` no se toca.**

### Y agregar el caso que hoy no se prueba

El `coalesce` tiene un comportamiento que ningún chequeo cubre y que es el
riesgo real del contrato: si COLOQUIO **omite** `p_actor`, la auditoría registra
el sistema y **se pierde quién fue la persona** — justo el dato que una
reidentificación necesita tener.

- [ ] Nuevo chequeo: llamando **sin** `p_actor`, la auditoría registra
      `session_user` y la fila queda marcada de forma que se vea que no hubo
      actor humano.

> Esto no es un capricho del test: es la diferencia entre poder demostrar quién
> accedió a un dato de contacto y no poder. Que esté cubierto por un chequeo
> hace visible la obligación del cliente.

---

## 3. Chequeo 2 — «un rol sin registrar no consigue nada»

### Qué reporta

```
✗ un rol sin registrar no consigue nada
    connection failed: … fe_sendauth: no password supplied
```

### Por qué no es verificable acá

El chequeo intenta conectarse con un rol no registrado **sin credenciales**.
Contra un cluster local de pruebas eso funciona (Postgres suele permitir
`trust`), pero **Cloud SQL exige credenciales en toda conexión**: el intento
muere en la autenticación y nunca llega a probar lo que quiere probar —que un
rol desconocido no obtiene nada de la superficie—.

No es un fallo: es un chequeo que **no aplica a este entorno**.

### Qué hay que cambiar

- [ ] Detectar si la conexión va contra **Cloud SQL** (por ejemplo, porque el
      DSN usa autenticación IAM o apunta al Auth Proxy) y en ese caso marcar el
      chequeo como **omitido**, con el motivo.
- [ ] Un chequeo omitido **no cuenta como fallo** en el total ni cambia el
      veredicto final.
- [ ] Contra el cluster local, el chequeo **sigue corriendo igual** que hoy: ahí
      sí tiene sentido y es el único lugar donde puede probarse.

---

## 4. El veredicto final tiene que distinguir tres estados

Hoy el resumen es binario: pasó o falló. Con un chequeo legítimamente omitido,
eso ya no alcanza.

- [ ] El resumen informa **pasados / fallidos / omitidos**, por separado.
- [ ] Si no hay fallos, el veredicto es afirmativo **aunque haya omitidos**, y
      aclara cuáles quedaron sin verificar y por qué.
- [ ] El mensaje *«La bóveda no está lista para COLOQUIO»* aparece **solo si hay
      fallos reales**.

Ejemplo del resultado esperado tras estos cambios:

```
17 chequeos · 16 pasados · 0 fallidos · 1 omitido
  ○ un rol sin registrar no consigue nada
      omitido: no verificable contra Cloud SQL (toda conexión exige
      credenciales). Corre contra el cluster local de pruebas.

La bóveda está lista para COLOQUIO.
```

---

## 5. Definition of Done

- [ ] El chequeo de auditoría pasa un email de usuario y verifica que ese email
      quede registrado.
- [ ] Existe el chequeo del caso **sin `p_actor`**, y pasa.
- [ ] El chequeo del rol sin registrar se omite contra Cloud SQL, con motivo, y
      corre contra el cluster local.
- [ ] El resumen distingue pasados, fallidos y omitidos.
- [ ] Contra Cloud SQL, con la bóveda sana, la batería termina **sin fallos**.
- [ ] Ningún archivo de `db/` se modifica (test de no regresión: las migraciones
      quedan intactas).

## 6. Nota aparte

La batería **no detectó** el bug de `v_persona_convocable` que duplicaba filas;
se encontró comparando a mano contra el store semántico. El chequeo de
cardinalidad que se agregó después («una fila por persona en cada vista») lo
cubre, pero **pasa de forma trivial cuando las vistas están vacías**: con la
bóveda sin datos, «ninguna clave repetida» es cierto sin probar nada.

- [ ] Que el chequeo de cardinalidad informe cuando corre sobre un conjunto
      vacío, para que no se lea como una verificación efectiva.
