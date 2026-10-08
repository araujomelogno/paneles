# PEDIDO — La API key web fuera de Secret Manager, y registrar el error de `verificar_clave`

**Sistema:** Gestión de paneles · portal del panelista
**Severidad:** Alta — **nadie puede entrar al portal**
**Detectado:** 2026-10-06, probando el acceso de un panelista real
**Estado:** Para corregir

---

## 1. El síntoma y la causa

Un panelista fija su contraseña por el enlace del correo —eso funciona— y al
intentar entrar recibe **«contraseña incorrecta»**, siempre, con cualquier
contraseña.

**La causa:** el secreto `FIREBASE_WEB_API_KEY` contiene el texto `AIza...` —el
placeholder del ejemplo, no la key real.

Y la razón por la que quedó así:

```
$ firebase functions:secrets:set FIREBASE_WEB_API_KEY --data-file -
Error: Invalid secret key FIREBASE_WEB_API_KEY:
- Error Key FIREBASE_WEB_API_KEY starts with a reserved prefix
  (X_GOOGLE_ FIREBASE_ EXT_ KIT_)
```

**Firebase no permite secretos cuyo nombre empiece con `FIREBASE_`.** El
`secrets:set` falló cuando se intentó cargar el valor real, y en la función
quedó el placeholder. El nombre elegido hace que ese secreto **no se pueda
cargar nunca**.

### Por qué el síntoma apunta al lugar equivocado

`verificar_clave` llama a Identity Toolkit por HTTP con esa key. Con una key
inválida la API responde 400, y el código hace:

```python
except urllib.error.HTTPError:
    # 400 es «no entró», por cualquiera de sus motivos. No se mira
    # cuál: distinguirlos acá es lo que termina filtrándose en el
    # mensaje de arriba.
    return None
```

Un **400 por API key inválida** y un **400 por contraseña incorrecta** producen
el mismo `None`, el mismo mensaje y ninguna línea de log. Un problema de
configuración se presenta como un problema de credencial del usuario.

> **Cuánto costó.** Diagnosticar esto llevó más de una hora y obligó a descartar
> a mano, una por una: largo mínimo de contraseña, restricciones de referente en
> la API key, cuenta de Auth inexistente, vínculo `uid` ↔ `id_persona` mal
> armado, límite de intentos fallidos. **El cuerpo del error de Identity Toolkit
> —«API key not valid»— decía exactamente qué pasaba desde el primer intento**,
> y se estaba descartando.

---

## 2. Qué hay que corregir

### R1 — La API key web deja de ser un secreto (P0)

Esa key **es pública por diseño**: está a la vista en el `index.html` del front,
se envía a cada navegador, y Google la documenta como un identificador de
proyecto y no como una credencial. Lo que protege el acceso es Firebase Auth y
las reglas, no el secreto de la key.

Tenerla en Secret Manager **no agrega seguridad y sí agrega una vía de falla**:
el valor no se ve en un `describe`, así que un placeholder puede quedar ahí
indefinidamente sin que nadie lo note. Como variable de entorno común, el error
habría sido visible de inmediato.

- [ ] Quitar `FIREBASE_WEB_API_KEY` del manifiesto de secretos de la función.
- [ ] Declararla como **variable de entorno** con su valor literal.
- [ ] `credenciales.api_key` sigue leyendo la misma variable: **el código que la
      consume no cambia**.
- [ ] El mensaje actual —«Falta `FIREBASE_WEB_API_KEY`: sin ella no se puede
      comprobar…»— se conserva: es correcto y útil.
- [ ] Eliminar el secreto de Secret Manager, para que no quede un valor muerto
      que alguien confunda con el vigente.

> Si por alguna razón se prefiere mantenerla como secreto, **el nombre tiene que
> cambiar** (`WEB_API_KEY`, sin el prefijo reservado) o nunca se va a poder
> cargar. Pero la opción recomendada es la variable de entorno.

### R2 — `verificar_clave` registra el error HTTP (P0)

El criterio de **no distinguir motivos al usuario** es correcto y no se toca:
cinco causas distintas tienen que devolver el mismo mensaje, o el formulario se
convierte en un buscador de panelistas. **Pero eso no implica no registrarlos.**

- [ ] Ante un `HTTPError`, registrar en el log el **código de estado** y el
      **cuerpo de la respuesta** de Identity Toolkit.
- [ ] Registrar también los errores de red (`URLError`, timeout), que hoy ni
      siquiera se capturan.
- [ ] El valor devuelto **sigue siendo `None`** y el mensaje al usuario **no
      cambia**.
- [ ] **No registrar la contraseña ni la key.** El correo puede ir, porque ya
      está en los registros de intentos.

> La distinción que importa: **al usuario, un solo mensaje; en el log, el motivo
> exacto.** Hoy el código aplica la primera regla a las dos superficies.

### R3 — Distinguir «no entró» de «no se pudo comprobar» (P1)

Un 400 de Identity Toolkit significa que la credencial no entró. Un **403**, un
**5xx** o un timeout significan que **no se pudo comprobar** — y eso no es lo
mismo: la credencial podía ser correcta.

- [ ] Ante un fallo que **no permite comprobar** la credencial, el portal
      informa un problema técnico, no «contraseña incorrecta».
- [ ] Ese caso **no cuenta como intento fallido** para el límite de
      `_frenar_si_hay_demasiados`: una caída del servicio no debe bloquear a una
      persona por una hora.
- [ ] El mensaje sigue sin revelar si el correo existe.

---

## 3. Verificación

```bash
# La variable tiene el valor real y es visible sin acceder a un secreto
gcloud run services describe api --region=southamerica-east1 --format=yaml \
  | grep -A2 FIREBASE_WEB_API_KEY

# El login entra con una contraseña conocida
curl -s -X POST \
  "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=LA_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"…","password":"…","returnSecureToken":false}'
```

Y desde el portal: fijar la contraseña por el enlace, cerrar sesión, y **entrar
con esa contraseña**.

## 4. Definition of Done

- [ ] `FIREBASE_WEB_API_KEY` es variable de entorno con el valor real, y el
      secreto homónimo ya no existe.
- [ ] Un panelista fija su contraseña y **entra con ella** (prueba manual
      completa).
- [ ] Una contraseña incorrecta sigue dando el mismo mensaje genérico (test de
      no regresión).
- [ ] Un fallo de Identity Toolkit deja en el log el estado y el cuerpo (test
      con respuesta simulada).
- [ ] La contraseña y la key **no aparecen** en ningún registro (test).
- [ ] Un 5xx o un timeout no se presentan como «contraseña incorrecta» ni
      cuentan para el límite de intentos (test).

---

# Solicitud de cambio — Paridad de acciones entre consulta demográfica y semántica

**Severidad:** Media — no rompe nada, pero **la mitad del producto no está
disponible** según cómo se arme la consulta.

## SC1 — El problema

La pantalla de consultas resuelve los dos tipos —semántica y demográfica— pero
**el resultado demográfico es una vista sin acciones**. Verificado en
`web/public/js/paginas/consultas.js`:

```js
function pintarResultado(resultado) {
  …
  if (resultado.tipo === 'demografica') {
    caja.innerHTML = pintarDemografica(resultado);
    activarTokens(caja);
    return;                    // ← sale antes de la barra de acciones
  }
  …                            // acá viven los seis botones
```

Un `return` temprano manda el caso demográfico a `pintarDemografica` y **nunca
llega al bloque donde se arma la barra de herramientas**. Resultado: todo lo que
se construyó en las fases 2, 3 y 7 sobre los resultados existe **solo para la
consulta semántica**.

Lo que falta del lado demográfico:

| Acción | Semántica | Demográfica | Requisito |
|---|:--:|:--:|---|
| Ver la ficha del panelista | ✅ | ❌ | R7.3 |
| Elegir columnas demográficas | ✅ | ❌ | R7.4 |
| Descargar CSV seudonimizado | ✅ | ❌ | R3.10 |
| Reidentificar («ver quiénes son») | ✅ | ❌ | R3.10 |
| Descargar CSV con datos | ✅ | ❌ | R3.10 |
| Crear panel desde el resultado | ✅ | ❌ | R3.11 |

> **Por qué duele más de lo que parece.** La consulta **puramente demográfica es
> el caso más frecuente** —se decidió así al diseñar el puente: filtrar por
> segmento no toca el store semántico y es lo que más se usa—. O sea: las
> acciones están disponibles en el camino menos transitado y faltan en el más
> transitado. Y para un analista la distinción es invisible: arma una consulta,
> obtiene personas, y según qué criterios usó puede o no hacer algo con ellas.

## SC2 — Qué se pide

**Todo lo que se puede hacer con un resultado semántico se puede hacer con uno
demográfico.** La barra de acciones es la misma; lo que cambia es el contenido
de la lista, no lo que se puede hacer con ella.

- [ ] **Ficha del panelista** (R7.3): clic en una fila abre la ficha, sin
      perder el resultado.
- [ ] **Elegir columnas** (R7.4): el mismo selector, con las mismas reglas
      —«sin dato» explícito, sin atributos de categoría especial, y sin
      re-ejecutar la consulta—.
- [ ] **Descargar CSV seudonimizado** (R3.10), con el mismo contrato.
- [ ] **Reidentificar** y **descargar CSV con datos** (R3.10), con las mismas
      condiciones: el segundo solo se habilita después de reidentificar, y queda
      registrado con motivo propio.
- [ ] **Crear panel desde el resultado** (R3.11), con la misma idempotencia y el
      mismo registro de origen.

**Las diferencias legítimas se conservan**, no se fuerzan:

- [ ] Un resultado demográfico **no tiene puntaje, evidencia ni veredicto**: la
      ficha muestra los atributos, y donde el semántico muestra la evidencia, el
      demográfico no muestra nada en lugar de inventar una columna vacía.
- [ ] Las degradaciones, el diagnóstico del puente y el aviso de verificación
      incompleta **siguen siendo del semántico**: no aplican acá.
- [ ] El orden no es un ranking: es el orden del listado.

## SC3 — Cómo conviene hacerlo

- [ ] **Extraer la barra de acciones a un bloque común** que los dos caminos
      usen, en vez de duplicar los seis botones en `pintarDemografica`.
      Duplicarlos garantiza que la próxima acción que se agregue vuelva a
      quedar en un solo lado.
- [ ] Verificar que los endpoints que esas acciones usan —`/resultados/atributos`,
      `/paneles/desde-consulta`, el CSV, la reidentificación— **aceptan un
      conjunto de `id_persona` sin depender de que venga de una consulta
      semántica**. Si alguno exige criterios semánticos, hay que ajustarlo.

## SC4 — Definition of Done

- [ ] Una consulta **solo demográfica** muestra la misma barra de acciones que
      una semántica (test).
- [ ] Clic en una fila abre la ficha y al cerrarla el resultado sigue igual
      (test).
- [ ] Elegir columnas funciona y **no re-ejecuta** la consulta (test).
- [ ] El CSV seudonimizado descarga con el mismo contrato (test).
- [ ] «Ver quiénes son» reidentifica y queda registrado; el CSV con datos se
      habilita recién después (test).
- [ ] Crear panel da de alta las membresías, es idempotente y registra su origen
      (test).
- [ ] El resultado demográfico **no muestra** columnas de puntaje, evidencia ni
      veredicto (test).
- [ ] La consulta semántica sigue funcionando igual (test de no regresión).

## SC5 — Costos

**US$ 0.** Es reutilizar interfaz y endpoints que ya existen. La única carga
adicional es resolver atributos y crear membresías sobre el conjunto
demográfico, que son las mismas consultas que ya se hacen del lado semántico.

---

# Anexo — Costos y proceso del pedido principal

## A1. Costos

**US$ 0.** Son cambios de configuración y de código, sin infraestructura nueva.
Sacar el valor de Secret Manager incluso quita una operación de lectura de
secreto por arranque de instancia, que es irrelevante pero no negativo.

## A2. Nota de proceso

Dos decisiones razonables combinadas produjeron un fallo opaco: **guardar como
secreto algo que no lo es**, y **no registrar un error para no filtrarlo al
usuario**. Ninguna es incorrecta por separado; juntas hicieron que un valor sin
cargar fuera indistinguible de una contraseña mal escrita.

- [ ] Revisar qué otros valores están en Secret Manager **sin ser secretos**
      (identificadores de proyecto, URLs, nombres de cuentas). Cada uno es un
      valor que nadie puede verificar de un vistazo.
- [ ] Revisar si hay otros `except` que devuelven un valor por defecto **sin
      registrar nada**. Son los que convierten un problema de configuración en
      un comportamiento inexplicable.
