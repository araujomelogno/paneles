# Protocolo de calibración — Fase 2 contra corpus real

**Sistema:** Gestión de paneles y consulta semántica · Equipos Consultores
**Para:** cerrar el paso 8.3 del manual de despliegue y el DoD de latencia (§9 del spec)
**Cuándo:** con al menos un estudio ingestado y, si se puede, un analista al lado
**Duración estimada:** una sesión de 1–2 horas

---

## 0. Antes de empezar

**Precondiciones.**
- [ ] Al menos un estudio ingestado, con textos de pregunta bien escritos y códigos de cerradas mapeados (si los textos quedaron vacíos, re-ingestá antes: los embeddings serían pobres y calibrarías sobre ruido).
- [ ] `degradaciones: []` en una consulta de prueba, y `diagnostico.reranker = "voyage"` + `verificador = "claude"`. Si alguno dice `"ninguno"`, falta la clave: calibrar con el pipeline degradado no sirve.
- [ ] Un analista que conozca el estudio y pueda juzgar si un resultado es correcto. **Esta es la pieza que no se puede automatizar.**

**Parámetros de partida** (se cambian por consulta desde el botón *Parámetros*, sin desplegar): `top_n` 200 · `top_k` 25 · `umbral_distancia` 0.55.

**Registro.** Una planilla con una fila por consulta: criterio, parámetros, nº de resultados, cuántos el analista marcó correctos, la distancia del peor correcto, la distancia del mejor incorrecto, `ms_total`, y notas.

---

## 1. Las diez consultas

Elegí criterios **de tu corpus real** (los ejemplos son del estudio de bebidas; adaptalos). La mezcla importa: cada bloque prueba algo distinto.

### Bloque A — Línea de base (3 consultas)
Criterios simples, con respuesta esperada conocida. Sirven para ver si el motor funciona en el caso fácil.

1. **Valor de cerrada frecuente** — ej. "consume fernet". Esperado: los que eligieron esa opción, arriba.
2. **Valor de cerrada poco frecuente** — ej. "consume whisky". Prueba que no se pierda lo minoritario.
3. **Concepto de abierta** — ej. "le gusta el sabor amargo". Prueba la búsqueda sobre texto libre.

### Bloque B — Lo que el rerank y la verificación tienen que resolver (4 consultas)
Este bloque es **la prueba de fuego**: son los casos donde la similitud vectorial sola falla.

4. **Polaridad opuesta** — ej. "está en contra del gobierno" (o el equivalente en tu estudio). *Chequeo clave:* los que están **a favor** no deben aparecer en el ranking final, aunque estén cerca en el espacio vectorial. Si aparecen, el rerank+verificación no está haciendo su trabajo.
5. **Polaridad inversa del anterior** — la misma pregunta al revés ("está a favor de…"). Los dos rankings deberían ser casi disjuntos. Si se parecen mucho, hay problema.
6. **Discriminación de valor** — ej. "toma fernet" vs "toma whisky": corré las dos y compará. No deberían devolver el mismo grupo.
7. **Negación / abandono** — ej. "dejó de tomar fernet". Semánticamente cercano a "toma fernet", pero significa lo contrario.

### Bloque C — Bordes y combinaciones (3 consultas)
8. **Criterio sin respuesta en el corpus** — algo que nadie preguntó, ej. "practica kitesurf". Esperado: vacío o todo marcado de baja confianza. Si devuelve un ranking seguro de gente al azar, el umbral está demasiado permisivo.
9. **Criterios combinados, modo estricto** — ej. "toma fernet" + "menor de 35". Chequeá que el filtro demográfico realmente acote y que la estrategia del puente quede registrada.
10. **Los mismos criterios, modo laxo** — compará contra la 9. El laxo debería incluir gente sin dato para un criterio, pero **no** gente con evidencia contraria.

---

## 2. Cómo fijar cada parámetro

### `umbral_distancia` — el que más importa
Es el único que depende del corpus y del modelo, y su default **no está medido** contra los datos de Equipos.

1. Con las 10 consultas corridas, pedile al analista que marque cada resultado como correcto o incorrecto.
2. Por cada consulta, anotá la **distancia del peor resultado correcto**.
3. El umbral es el **máximo** de esos valores, con un poco de aire (ej. +0.02).
4. Verificá el otro lado: mirá la **distancia del mejor incorrecto**. Si es menor que tu umbral, hay solapamiento y el umbral no separa bien — anotalo como límite conocido del sistema, no lo fuerces.

### `top_n` — pool de recall
Corré la consulta 1 y la 3 con `top_n` 200, después 400, después 800.
- Si el ranking final **no cambia**, 200 alcanza.
- Si con 400 aparecen correctos que con 200 no estaban, subí y volvé a probar.
- Señal de alarma: si sigue mejorando con 800, el recall es pobre — mirá el índice (paso 6.1 del manual).

### `top_k` — cuántas personas verifica Claude
Es el caro: la verificación domina latencia y costo.
- Mirá cuántos de los 25 verificados terminan en el ranking final. Si sistemáticamente entran 8, `top_k` 25 es desperdicio: bajalo a 12–15.
- Si en cambio **todos** los 25 cumplen, el corte es muy chico: subilo, porque estás dejando correctos afuera.

---

## 3. Latencia y costo

Con las mismas 10 consultas ya tenés la muestra. De cada respuesta anotá `diagnostico.ms_total` y el desglose por etapa, y calculá p50/p95 **por forma de consulta**: demográfica pura, semántica pura, mixta.

Referencia de qué esperar: la verificación es la etapa dominante (1–4 s), el reranking va detrás (200–600 ms), y el recall y el gate son menores. La demográfica pura debería ser de otro orden — es SQL, sin llamadas externas.

**Si hay que bajar latencia**, el orden es: `top_k`, después `top_n`, y solo al final tocar el modelo de verificación.

**Costo.** Cada criterio semántico corre su propio pipeline completo (embedding + rerank + Claude). Dos criterios semánticos = el doble. Los criterios demográficos son gratis. De ahí la regla operativa más útil: **acotar por panel y por segmento antes de consultar baja el costo y mejora el resultado a la vez.**

---

## 4. Criterios de salida

- [ ] `umbral_distancia` fijado con evidencia (no el default), y anotado de dónde salió.
- [ ] `top_n` y `top_k` fijados, con la observación que los justifica.
- [ ] El caso de polaridad opuesta (consultas 4 y 5) pasa: los del bando contrario **no** aparecen en el ranking final.
- [ ] El criterio sin respuesta (consulta 8) no devuelve un ranking falsamente seguro.
- [ ] p50/p95 registrados por forma de consulta → cierra el ítem de latencia del DoD.
- [ ] Costo estimado por consulta típica, con decisión de si se abre a todo el equipo.

## 5. Qué hacer con lo que no cierre

- **Resultados correctos con distancias muy dispersas** → probablemente los textos de pregunta de algún estudio quedaron pobres. Re-ingestá ese estudio con los textos bien escritos (la ingesta es idempotente).
- **Polaridad que no se resuelve** → revisá que la verificación esté activa (`verificador: "claude"`), y mirá la evidencia que cita: si cita la respuesta correcta pero igual la deja pasar, es tema del prompt de verificación, no del rerank.
- **Solapamiento entre correctos e incorrectos** → es un límite real del enfoque, no un bug. Anotalo, y compensá acotando por segmento (los criterios demográficos filtran gratis).
- **Latencia alta con `top_k` ya bajo** → mirá el desglose: si el peso está en `recall`, es el índice; si está en `embedding`, es la red hacia Voyage.
