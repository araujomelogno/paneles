# SPEC — R6.1.a: Acceso del panelista con usuario y contraseña

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Reemplaza:** R6.1 (acceso por enlace de un solo uso), ya implementado
**Alcance:** portal del panelista y su backend
**Estado:** Para desarrollo
**Última actualización:** 2026-10-02

---

## 1. Problem Statement

R6.1 definió el acceso al portal por **enlace de un solo uso** enviado al correo:
cada vez que el panelista quiere entrar, pide un correo, lo espera y hace clic.
Para un portal al que se entra cada varios meses —a mirar puntos, corregir un
dato— esa fricción es la diferencia entre un portal que se usa y uno al que
nadie vuelve. Y además deja el acceso a merced de la entregabilidad del correo:
si el mensaje demora o cae en spam, el panelista no entra.

El acceso pasa a ser **con usuario y contraseña**, que es lo que la gente
espera de un portal propio y lo que le permite entrar cuando quiera.

Queda un problema a resolver: **los panelistas ya enrolados no tienen
contraseña.** Nadie se la pidió nunca. Hace falta una vía para que la
establezcan, y la misma vía sirve para los nuevos.

## 2. Goals

- Que el panelista entre **cuando quiera**, con una credencial propia.
- Que quien **ya está enrolado** pueda establecer su contraseña sin que nadie de
  Equipos intervenga ni la conozca.
- Que quien la **olvide** pueda recuperarla solo.
- Que las acciones **irreversibles** sigan exigiendo una prueba de identidad
  adicional.

## 3. Non-Goals

- **No se mantiene el acceso por enlace mágico** como vía de login. El enlace
  queda solo para **establecer** y **recuperar** la contraseña, una vez cada
  tanto, no en cada acceso.
- **No se agregan proveedores sociales** (Google, etc.).
- **No hay segundo factor** en esta versión.
- **Ningún operador de Equipos conoce ni asigna la contraseña de un panelista.**
- No cambia el vínculo cuenta ↔ persona (R6.2) ni el resto de la Fase 6.

## 4. User Stories

- Como panelista, quiero entrar con mi correo y mi contraseña, sin depender de
  que me llegue un mail.
- Como panelista ya enrolado, quiero recibir un enlace para crear mi contraseña
  la primera vez.
- Como panelista, quiero recuperar el acceso si me olvido la contraseña.
- Como panelista, quiero poder cambiar mi contraseña cuando quiera.
- Como responsable de panel, quiero poder reenviarle a alguien su enlace para
  establecer la contraseña, sin conocerla.
- Como DPO, quiero que darse de baja exija probar identidad en el momento.

**Casos borde**
- Enlace de alta reenviado a otra persona.
- Correo que no corresponde a ningún panelista (no debe revelarlo).
- Panelista con el mismo correo que un usuario interno de Equipos.
- Panelista que cambia su correo: con qué entra después.
- Panelista dado de baja que intenta entrar.
- Intentos repetidos de login con contraseñas distintas.

## 5. Requirements

### R6.1.a — Establecer la contraseña por enlace único (P0)

Mismo mecanismo que ya se usa al crear un usuario interno del sistema.

- Dado un panelista sin contraseña, entonces se le puede emitir un **enlace de
  un solo uso** para establecerla, enviado a su correo registrado.
- Dado el enlace, entonces vence (sugerido: 24 horas) y sirve **una sola vez**.
- Dado un enlace usado o vencido, entonces no permite establecer nada y ofrece
  pedir uno nuevo.
- Dada una inscripción nueva aprobada (R3.7), entonces el enlace se emite
  automáticamente.
- Dado un panelista ya enrolado antes de esta fase, entonces un responsable
  puede **emitir o reenviar** su enlace desde la administración.
- [ ] El responsable **nunca ve ni define** la contraseña: solo dispara el envío.
- [ ] La emisión queda auditada (quién la pidió y cuándo), como la de usuarios
      internos.

### R6.1.b — Entrar con usuario y contraseña (P0)

- Dado un correo y una contraseña correctos, entonces se inicia sesión.
- Dada una credencial incorrecta, entonces el mensaje es **genérico** —«correo o
  contraseña incorrectos»— sin distinguir cuál de los dos falló ni si el correo
  existe.
- Dados intentos fallidos repetidos, entonces se limita por correo y por origen.
- Dado un panelista dado de baja, entonces no puede entrar.
- Dada una sesión iniciada, entonces persiste hasta que el panelista cierre
  sesión o venza por inactividad prolongada.
- [ ] Hay un **«cerrar sesión»** visible.
- [ ] Longitud mínima de contraseña según la política de Firebase Auth; el hash
      y el almacenamiento los maneja Auth, no se implementan a mano.

> **Por qué el mensaje genérico.** Si el error distingue «ese correo no existe»
> de «contraseña incorrecta», cualquiera puede averiguar quién integra el panel
> probando direcciones. Es una filtración de datos personales aunque nunca se
> muestre un perfil.

### R6.1.c — Recuperar y cambiar la contraseña (P0)

- Dado un panelista que olvidó su contraseña, entonces puede pedir un enlace de
  recuperación a su correo.
- Dada esa solicitud, entonces la respuesta es **idéntica** exista o no ese
  correo: «si esa dirección corresponde a un panelista, va a recibir un enlace».
- Dado el enlace de recuperación, entonces vence y sirve una sola vez.
- Dado un panelista con sesión iniciada, entonces puede cambiar su contraseña
  indicando la actual.
- [ ] Un cambio de contraseña cierra las demás sesiones abiertas de esa persona.

### R6.1.d — Reautenticación para acciones sensibles (P0)

- Dadas las acciones **darse de baja**, **retirar una finalidad** y **cambiar el
  correo**, entonces se exige **volver a ingresar la contraseña**, aunque la
  sesión esté vigente.
- Dadas las demás acciones —ver puntos, solicitar un canje, editar atributos,
  cambiar preferencias de canal—, entonces alcanza con la sesión.
- Dada una reautenticación exitosa, entonces la acción continúa sin perder lo
  que el panelista estaba haciendo.

> **El criterio.** La sesión sirve para mirar y para lo reversible; lo que
> destruye datos pide probar de nuevo que sos vos. Revocar un canal se deshace
> volviéndolo a activar; darse de baja, no. Con contraseña esto es barato: es un
> campo más, no un correo que esperar.

### R6.1.e — Corte de sesión y acceso tras la baja (P0)

- Dada una baja efectiva, entonces **todas las sesiones** de esa persona se
  invalidan de inmediato, incluidas las de otros dispositivos.
- Dada una baja, entonces la credencial queda inutilizable: no puede volver a
  entrar ni recuperar la contraseña.

### R6.1.f — Cambio de correo (P0)

- Dado un cambio de correo (R6.6), entonces **el correo nuevo pasa a ser el
  usuario** con el que entra, conservando la misma contraseña.
- Dado que el correo nuevo todavía no fue verificado, entonces el acceso sigue
  funcionando con el anterior.

## 6. Cambios de esquema

Ninguno en la bóveda. La credencial la administra Firebase Auth; el vínculo
`uid` ↔ `id_persona` ya existe (R6.2). El registro de enlaces emitidos puede
reusar el mecanismo de usuarios internos.

## 7. Definition of Done

- [ ] Un panelista sin contraseña recibe un enlace y la establece (test).
- [ ] El enlace usado dos veces falla la segunda, y uno vencido no sirve (test).
- [ ] Un responsable puede reenviar el enlace sin ver ni definir la contraseña
      (test), y la emisión queda auditada.
- [ ] El login con credencial correcta entra; con una incorrecta da un mensaje
      genérico que no revela si el correo existe (test).
- [ ] Intentos fallidos repetidos se limitan (test).
- [ ] Un panelista dado de baja no entra ni recupera la contraseña (test).
- [ ] «Olvidé mi contraseña» responde igual exista o no el correo (test).
- [ ] Cambiar la contraseña cierra las otras sesiones (test).
- [ ] Darse de baja, retirar una finalidad y cambiar el correo exigen volver a
      ingresar la contraseña (test de cada una).
- [ ] Tras reautenticar, la acción continúa sin volver a empezar (test).
- [ ] Una baja invalida las sesiones abiertas en otros dispositivos (test).
- [ ] Un panelista no obtiene ningún permiso de la administración (test de no
      regresión).

## 8. Costos

**Ninguno adicional, y reduce los envíos de correo respecto de R6.1.** Con
enlace mágico había un correo por visita; ahora hay uno al establecer la
contraseña y otro cada vez que alguien la olvida. Eso aleja el tope de
Workspace (2.000 diarios por cuenta) y hace menos probable necesitar un servicio
transaccional pago (~US$ 15–20/mes).

Firebase Auth no cobra por autenticación con correo y contraseña en los
volúmenes de un panel.

## 9. Riesgos y preguntas abiertas

- **[seguridad]** Las contraseñas traen lo que el enlace evitaba: gente que
  reutiliza la misma de otros servicios, y un objetivo que robar. Mitigado por
  el límite de intentos y porque Auth maneja el hash; si en algún momento el
  portal expone más datos, vale evaluar un segundo factor.
- **[operación]** Entrar cada varios meses y olvidar la contraseña va a ser
  frecuente. El flujo de recuperación tiene que ser visible y simple: si
  recuperar cuesta más que entrar, la gente abandona.
- **[producto]** El enlace de alta depende del correo igual que antes: si no
  llega, el panelista no puede establecer la contraseña. De ahí que un
  responsable pueda reenviarlo.
- **[datos]** Un panelista cuyo correo registrado está desactualizado no puede
  recibir el enlace y queda sin acceso. Conviene detectar esos casos antes de
  anunciar el portal.
