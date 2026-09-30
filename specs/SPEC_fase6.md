# SPEC — Fase 6: Portal del panelista

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**PRD de referencia:** `PRD_sistema_paneles_unificado.md`
**Estado:** Borrador para desarrollo · sexta fase
**Precondición:** Fases 1 a 4 desplegadas
**Última actualización:** 2026-10-02

---

## 1. Problem Statement

Todo lo que el panelista puede hacer con sus propios datos pasa hoy por una
persona de Equipos. Quiere saber cuántos puntos tiene: pregunta. Cambió de
celular: avisa y alguien lo carga. Quiere dejar de recibir WhatsApp, o salir
del panel, o que dejen de analizar sus respuestas: escribe un correo y espera.

Eso tiene tres costos distintos.

**El dato de contacto se pudre.** Los correos y celulares envejecen y nadie los
corrige salvo cuando un envío rebota. Un panel deja de ser alcanzable mucho
antes de que alguien lo note, y en el caso de WhatsApp es peor: mandar a
números muertos degrada la reputación del número emisor.

**Los derechos del titular se ejercen a mano.** Acceso, rectificación,
supresión y retiro de consentimiento son obligaciones bajo URCDP, y hoy se
atienden por correo, sin trazabilidad uniforme ni plazo garantizado.

**Y la gamificación no puede funcionar sin visibilidad.** Se otorgan puntos que
nadie ve y premios que nadie puede pedir: el incentivo que debía sostener la
participación no existe desde el lado del panelista.

Esta fase le da al panelista una puerta propia. No es «una pantalla de puntos»:
es la **interfaz de derechos del titular**, donde los puntos son lo que hace
que la gente entre.

## 2. Goals

- Que el panelista **vea y canjee** sus puntos.
- Que **mantenga actualizados** sus datos demográficos y de contacto, sin
  intermediarios.
- Que **ejerza sus derechos** —acceso, rectificación, retiro de consentimiento,
  baja— por sí mismo y de forma trazable.
- Que el acceso sea **seguro para una superficie pública** con miles de
  usuarios externos.

## 3. Non-Goals

- **No muestra las respuestas anteriores del panelista.** Contamina la
  investigación (ver lo que respondió antes condiciona lo que responde ahora) y
  complica lo prometido sobre confidencialidad.
- **No muestra a qué estudios fue convocado** ni resultados de ningún estudio.
- **No permite editar cualquier atributo**: solo los que el administrador marcó
  como editables.
- **No reemplaza el alta**: el portal es para quien ya es panelista. Inscribirse
  sigue siendo la landing (R3.7).
- **No hay chat ni soporte** dentro del portal.
- **No hay app móvil**: es web, responsive.
- No cambia el pipeline de consulta ni el store semántico.

## 4. Composición de la fase

| Bloque | Requisitos | Tema |
|---|---|---|
| **6A — Acceso** | R6.1, R6.2 | Autenticación del panelista y vínculo con su identidad |
| **6B — Puntos** | R6.3, R6.4 | Ver saldo y solicitar canje |
| **6C — Perfil** | R6.5, R6.6 | Editar atributos y datos de contacto |
| **6D — Derechos** | R6.7, R6.8, R6.9 | Preferencias de canal, retiro de finalidades, baja |

**Dependencias:** 6B depende de la gamificación (R3.3–R3.5); 6C, del catálogo de
atributos (R3.14); 6D, de las preferencias de canal (R4.4) y de la cascada de
baja (R1.3). **6A es precondición de todo lo demás.**

---

## 5. User Stories

**Panelista**
- Como panelista, quiero entrar con mi correo sin tener que recordar una clave.
- Como panelista, quiero ver cuántos puntos tengo y de dónde salieron.
- Como panelista, quiero pedir un premio del catálogo y saber en qué estado está
  mi pedido.
- Como panelista, quiero corregir mi celular cuando cambio de número.
- Como panelista, quiero actualizar mis datos cuando cambia mi situación.
- Como panelista, quiero dejar de recibir WhatsApp sin dejar de ser panelista.
- Como panelista, quiero irme del panel y que borren mis datos.

**Responsable de panel / operaciones**
- Como responsable, quiero revisar y aprobar los canjes antes de entregarlos.
- Como responsable, quiero que los datos que el panelista corrige valgan más que
  los que trae un archivo.

**DPO**
- Como DPO, quiero que cada ejercicio de derechos quede registrado con qué se
  pidió y cuándo.

**Casos borde**
- Correo que no corresponde a ningún panelista (no debe revelarlo).
- Panelista con el mismo correo que un usuario interno de Equipos.
- Enlace de acceso reenviado a otra persona.
- Canje solicitado dos veces, o con el saldo justo.
- Baja con puntos sin canjear.
- Baja mientras hay un canje pendiente de entrega.
- Atributo editado por el panelista y después traído distinto en una ingesta.
- Panelista dado de baja que vuelve a pedir acceso.

---

## 6. Requirements

### Bloque 6A — Acceso

#### R6.1 — Autenticación por enlace de correo (P0)

- Dado un correo, cuando se solicita acceso, entonces se envía un **enlace de un
  solo uso** a esa dirección.
- Dada la solicitud, entonces la respuesta es **idéntica** exista o no ese
  correo en la bóveda: «si esa dirección corresponde a un panelista, va a
  recibir un enlace». **Nunca revelar si existe.**
- Dado un enlace, entonces vence (sugerido: 15 minutos) y sirve **una sola vez**.
- Dada una tasa inusual de solicitudes desde un mismo origen, entonces se limita.
- [ ] Sin clave: no se crean contraseñas de panelistas.
- [ ] El portal no ofrece registro: quien no es panelista, no entra (se inscribe
      por la landing).

> **Por qué la respuesta tiene que ser idéntica.** Si el sistema responde
> distinto ante un correo que existe y uno que no, cualquiera puede averiguar
> quién integra el panel probando direcciones. Eso es una filtración de datos
> personales aunque nunca se muestre un perfil.

#### R6.2 — Vínculo entre la cuenta y la persona (P0)

- Dada una autenticación exitosa, entonces la sesión se resuelve a un
  `id_persona` mediante un vínculo `uid` ↔ `id_persona`.
- Dado un primer acceso, entonces el vínculo se establece **solo si el correo
  autenticado coincide** con el que la bóveda tiene para esa persona.
- Dada una sesión, entonces solo puede ver y modificar **su propia** persona:
  ninguna ruta acepta un `id_persona` del cliente.
- Dado un panelista dado de baja, entonces no puede acceder.
- [ ] Los panelistas **no** obtienen ningún rol del padrón interno: el portal y
      la app de administración son superficies separadas, aunque compartan
      Firebase Auth.
- [ ] Si el correo coincide con un usuario interno, eso no le da acceso a la
      administración ni al revés.

---

### Bloque 6B — Puntos

#### R6.3 — Ver saldo y movimientos (P0)

- Dado un panelista, entonces ve su **saldo** y el detalle de sus movimientos
  (fecha, tipo, cantidad, motivo).
- Dados puntos con vencimiento, entonces se muestra cuándo vencen.
- Dado que ganó puntos por participación, entonces el motivo lo explica en
  términos comprensibles, no con códigos internos.
- [ ] El saldo se calcula desde el ledger, nunca de un campo acumulado.

#### R6.4 — Solicitar canje, con aprobación (P0)

- Dado el catálogo, entonces se muestran los premios activos con su costo y si
  están al alcance del saldo.
- Dada una solicitud con saldo suficiente, entonces se registra el canje en
  estado **`solicitado`** y **los puntos se reservan** (se descuentan del saldo
  disponible).
- Dado saldo insuficiente o premio sin stock, entonces se rechaza sin modificar
  el saldo.
- Dadas dos solicitudes concurrentes con saldo para una sola, entonces solo una
  prospera: **el saldo nunca queda negativo**.
- Dado un canje, entonces el panelista ve su estado: solicitado → aprobado →
  entregado, o cancelado.
- Dada una cancelación, entonces los puntos vuelven como movimiento nuevo (no se
  borra el movimiento original).
- [ ] La aprobación y la entrega las hace una persona de Equipos desde la
      administración: el portal **solicita**, no resuelve.
- [ ] Se agrega el estado `aprobado` entre `solicitado` y `entregado`, para
      separar «lo revisé» de «lo entregué».

---

### Bloque 6C — Perfil

#### R6.5 — Editar los atributos marcados como editables (P0)

- Dado el catálogo de atributos, entonces el administrador puede marcar cada uno
  como **editable por el panelista** (nuevo campo en `atributo_demografico`).
- Dado el portal, entonces muestra **solo** los atributos editables, con sus
  categorías canónicas como opciones.
- Dado un atributo no editable, entonces no se muestra ni se acepta su
  modificación, aunque venga forzada en el pedido.
- Dado un cambio, entonces se guarda con `origen = 'panelista'` y, por el
  historial (R4.1.a), crea una **vigencia nueva** conservando la anterior.
- [ ] Los atributos de tipo `derivado` nunca son editables.
- [ ] Un atributo marcado como **categoría especial** (R3.14.e) no debería ser
      editable sin una decisión explícita: son datos con exigencias propias.

> **Precedencia: la persona gana.** Un valor con `origen = 'panelista'` es la
> fuente autoritativa sobre sí misma y **no se sobrescribe** por una ingesta
> posterior ni por un operador. La jerarquía es **panelista > operador >
> archivo**. Una ingesta que traiga otro valor lo informa como discrepancia,
> igual que hoy hace con un campo ya cargado.
>
> Efecto secundario valioso: como el historial guarda cada vigencia, se aprende
> **cuándo** cambió la situación de alguien, no solo cuál es hoy.

#### R6.6 — Actualizar datos de contacto (P0)

- Dado el portal, entonces el panelista puede actualizar su correo y su celular.
- Dado un cambio de celular, entonces se normaliza a E.164 y se **verifica**
  antes de darlo por válido (mismo mecanismo que R4.3).
- Dado un cambio de correo, entonces se verifica la dirección nueva antes de
  reemplazar la anterior; hasta entonces el acceso sigue por la vieja.
- [ ] Un celular sin verificar no habilita la preferencia de WhatsApp.

---

### Bloque 6D — Derechos

#### R6.7 — Gestionar preferencias de canal (P0)

- Dado el portal, entonces el panelista ve por qué canales aceptó ser contactado
  y puede **activar o revocar cada uno** por separado.
- Dada una revocación, entonces se registra con fecha y origen `panelista`, y
  deja de ser contactable por ese canal, sin afectar los otros.
- [ ] Revocar WhatsApp no implica darse de baja: son cosas distintas y la
      interfaz tiene que dejarlo claro.

#### R6.8 — Retirar una finalidad (P0)

- Dado el portal, entonces el panelista ve qué consintió, con qué texto y
  cuándo, y puede **retirar cada finalidad por separado**.
- Dado el retiro de `uso_semantico`, entonces sus respuestas salen del store
  semántico y deja de aparecer en consultas semánticas, **sin dejar el panel**.
- Dado el retiro de `contacto_participacion`, entonces deja de ser convocable.
- Dado cualquier retiro, entonces dispara la cascada que ya existe.

> **Granular, no todo o nada.** El sistema ya modela finalidades y canales por
> separado; el portal tiene que respetarlo. Alguien puede querer salir del
> análisis entre estudios y seguir participando, o dejar WhatsApp y seguir
> recibiendo correos. Colapsar todo en «darse de baja» pierde una distinción que
> la arquitectura ya sostiene.

#### R6.9 — Darse de baja (P0)

- Dado el portal, entonces el panelista puede solicitar la baja completa.
- Dada la solicitud, entonces **antes de confirmar** se le informa, en texto
  claro: que sus datos van a ser eliminados, que **los puntos acumulados se
  pierden** (con el saldo a la vista), y que los canjes pendientes se cancelan.
- Dada la confirmación, entonces se ejecuta la cascada de baja existente (R1.3):
  PII de la bóveda, embeddings del store semántico, salida del muestreo y
  pendientes para cada sistema consumidor.
- Dada la baja, entonces se le confirma que **el pedido quedó registrado y se
  está ejecutando** — no que ya terminó: la cascada incluye consumidores
  externos que confirman de forma asincrónica.
- Dada la baja, entonces el acceso al portal se corta de inmediato.
- [ ] Los puntos **se pierden** con la baja. Es la regla definida y tiene que
      estar escrita en la confirmación, no en una letra chica.
- [ ] La lápida (`persona_borrada`) se crea como en cualquier baja: prueba que
      se atendió el pedido sin conservar PII.

---

## 7. Cambios de esquema

- **`atributo_demografico`**: campo `editable_por_panelista boolean not null
  default false`. Por defecto nada es editable: habilitarlo es una decisión.
- **Vínculo cuenta ↔ persona**: tabla nueva con `uid`, `id_persona`, fecha de
  vinculación y último acceso. Única por `uid` y por `id_persona`.
- **`canje`**: agregar `aprobado` al ciclo de estados, con quién aprobó y cuándo.
- **`persona_atributo`**: sin cambios (`origen` ya existe y admite `panelista`).
- **Solicitudes de acceso**: registro de enlaces emitidos, su estado y
  vencimiento (puede reusarse el de verificación de R4.3).

Todas aditivas.

## 8. Dependencias

- **Envío de correo operativo.** Es la dependencia dura: sin eso no hay acceso.
  Ver §10 por el costo y el límite de volumen.
- **Gamificación (R3.3–R3.5)** con puntos ya otorgándose.
- **Catálogo de atributos (R3.14)** con categorías definidas.
- **Preferencias de canal (R4.4)** y **cascada de baja (R1.3)**.
- **Historial de atributos (R4.1.a)** para que la edición cree vigencias en vez
  de sobrescribir.
- **[legal]** Revisión del texto que se muestra al dar de baja, en particular la
  pérdida de puntos.

## 9. Definition of Done

**6A**
- [ ] La respuesta a «pedir acceso» es idéntica para un correo que existe y uno
      que no (test).
- [ ] Un enlace usado dos veces falla la segunda (test).
- [ ] Un enlace vencido no sirve (test).
- [ ] Una sesión no puede leer ni modificar a otra persona, aunque mande otro
      `id_persona` (test).
- [ ] Un panelista dado de baja no puede entrar (test).
- [ ] Un panelista no obtiene ningún permiso de la administración (test).

**6B**
- [ ] El saldo mostrado coincide con la suma del ledger (test).
- [ ] Dos canjes concurrentes con saldo para uno: solo uno prospera, saldo nunca
      negativo (test).
- [ ] Un canje queda en `solicitado` y no se entrega sin aprobación (test).
- [ ] Cancelar devuelve los puntos como movimiento nuevo (test).

**6C**
- [ ] Solo se muestran y aceptan los atributos marcados como editables; uno no
      editable se rechaza aunque venga forzado (test).
- [ ] Un atributo `derivado` nunca es editable (test).
- [ ] Un cambio del panelista crea vigencia nueva con `origen = 'panelista'`
      (test).
- [ ] Una ingesta posterior **no sobrescribe** un valor de origen `panelista` y
      lo informa como discrepancia (test).
- [ ] Un celular nuevo sin verificar no habilita WhatsApp (test).

**6D**
- [ ] Revocar un canal no afecta los otros ni implica baja (test).
- [ ] Retirar `uso_semantico` saca los embeddings y deja el panel intacto
      (test).
- [ ] La confirmación de baja muestra el saldo de puntos que se pierde (test).
- [ ] La baja dispara la cascada completa y crea la lápida (test).
- [ ] Tras la baja, el acceso se corta (test).

## 10. Costos

| Concepto | Costo |
|---|---|
| Envío de enlaces con Firebase Auth (dominio de Firebase) | **US$ 0** |
| Envío desde `@equipos.com.uy` vía Workspace (SMTP propio) | US$ 0, **pero con tope de 2.000 envíos diarios por cuenta** |
| Servicio transaccional (SendGrid, Mailgun) para volumen o mejor entregabilidad | ~US$ 15–20/mes |
| Verificación de celular por SMS (R6.6) | por mensaje — depende del proveedor |
| Hosting del portal (sitio adicional en el mismo proyecto) | US$ 0 |
| Infraestructura nueva | **Ninguna**: mismo proyecto, mismas bases, mismo conector |

> **Recomendación:** arrancar con el envío de Firebase, que es gratis y resuelve
> entregabilidad. Pasar a dominio propio si la tasa de apertura lo justifica, y
> a un servicio transaccional solo si el volumen supera el tope de Workspace.
>
> **A vigilar:** el portal puede aumentar la carga sobre la bóveda (hoy en
> `db-f1-micro`, 0.6 GB). Miles de panelistas entrando a la vez es un patrón de
> uso distinto del interno. Ver los umbrales de `COSTOS.md` §4.

## 11. Riesgos y preguntas abiertas

- **[seguridad]** Esta fase **da vuelta la postura de seguridad del sistema**:
  hasta acá la bóveda solo la tocaban empleados de Equipos; ahora autentica a
  miles de externos contra el store que tiene toda la PII. Amerita revisión de
  seguridad propia, no solo los tests del DoD.
- **[seguridad]** Enumeración de correos: mitigada por R6.1, pero es el vector
  más probable. Vale probarlo explícitamente.
- **[producto]** Mostrar los puntos crea expectativa y presión: hay que
  sostener un circuito de canje que cumpla. Un premio que nunca llega es peor
  que no tener premios.
- **[producto]** Efecto perverso de la visibilidad: puntos a la vista → la gente
  optimiza por puntos → baja la calidad de respuesta. Por eso importa que el
  earn sea por **participación de calidad** (R3.4) y que se entienda así.
- **[producto]** Gameo de atributos editables: se evaluó y se consideró poco
  probable en la práctica. Queda anotado: si algún atributo pasa a definir
  cuotas o premios, conviene auditar sus cambios o limitar su frecuencia.
- **[legal]** La pérdida de puntos al darse de baja está decidida, pero tiene
  que quedar clara **antes** de confirmar. Es el tipo de cosa que genera
  reclamos si aparece después.
- **[operación]** Baja con canje pendiente de entrega: se cancela. Conviene que
  el aviso previo lo diga.
- **[datos]** Un panelista que corrige su correo cambia la llave con la que
  entra: si se equivoca al tipear el nuevo, pierde el acceso. De ahí que el
  correo nuevo se verifique antes de reemplazar al viejo (R6.6).
