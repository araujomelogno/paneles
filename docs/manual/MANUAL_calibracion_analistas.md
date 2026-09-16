# Manual de calibración de la búsqueda por concepto

**Para:** analistas de panel · no hace falta perfil técnico
**Duración:** una sesión de 1 a 2 horas
**Qué vas a necesitar:** la aplicación abierta, un estudio ya cargado, y esta planilla

---

## 1. Qué es esto y por qué te toca a vos

La aplicación permite buscar panelistas **por lo que dijeron**, no por en qué columna está el dato. Escribís algo como *"está descontento con el gobierno"* y te devuelve una lista de personas ordenada, de la que más se acerca a la que menos.

Para armar esa lista el sistema hace tres cosas, una atrás de la otra. Cada una tiene un número que regula cuánto trabaja. Esos números vienen con un valor de fábrica que **no está medido contra los estudios de Equipos**: están puestos para que la herramienta funcione desde el primer día, no porque sean los correctos para tus datos.

Calibrar es ajustarlos. Y te toca a vos porque la única forma de saber si un resultado está bien es que alguien que conoce el estudio lo mire y diga «esta persona sí, esta no». Eso no lo puede hacer un programa: es criterio de investigación, no de programación.

La buena noticia: **no se rompe nada**. Los números se cambian desde un botón en la misma pantalla de consulta, valen para esa consulta, y se pueden volver a cambiar todas las veces que quieras.

---

## 2. Los tres números, en criollo

Pensá en cómo buscarías a alguien para un trabajo entre muchos currículums.

### Primero: cuántos junta (`top_n`, de fábrica 200)

El sistema hace una **primera barrida rápida** por todas las respuestas guardadas y aparta las que le suenan parecidas a lo que buscaste. Es rápido y grueso: junta bastante, sin mirar mucho.

*Es como juntar 200 currículums que a primera vista tienen algo que ver.*

**Si es muy bajo:** se le escapa gente que sí servía, porque nunca la levantó del montón.
**Si es muy alto:** no empeora el resultado, solo tarda un poco más.

### Segundo: a cuántos mira con atención (`top_k`, de fábrica 25)

De todo lo que juntó, el sistema **lee con atención** a un grupo más chico y decide, uno por uno, si de verdad cumple lo que pediste. Acá es donde se dan cuenta las diferencias finas: que alguien esté *en contra* y no *a favor*, que tome fernet y no whisky.

*Es como leer en serio 25 currículums de los 200 que juntaste.*

**Este es el número caro.** Es la parte que más tarda y la que más cuesta en plata. Si una consulta demora mucho, este es el primero que hay que bajar.

**Si es muy bajo:** deja gente buena afuera sin siquiera mirarla.
**Si es muy alto:** tarda más y cuesta más, sin mejorar nada.

### Tercero: qué tan parecido cuenta (`umbral_distancia`, de fábrica 0.55)

El sistema le pone un puntaje a cada respuesta según cuánto se parece a lo que buscaste. Este número es **la línea a partir de la cual deja de confiar**: lo que queda más lejos se marca como resultado de baja confianza.

*Es como decir «de acá para abajo, estos currículums ya no tienen nada que ver».*

**Si es muy bajo (exigente):** te quedás corto, descarta gente que servía.
**Si es muy alto (permisivo):** te llena la lista de gente que no tiene nada que ver, y peor, **te la muestra como si fuera buena**.

> **De los tres, este es el más importante y el más propio de tus datos.** Los otros dos se ajustan por sentido común; este solo se puede saber probando.

---

## 3. Antes de empezar

Chequeá estas cuatro cosas. Si alguna falla, la calibración no va a servir y conviene resolverlo antes.

- [ ] **Hay al menos un estudio cargado**, con preguntas reales. Si los textos de las preguntas quedaron vacíos o abreviados al cargarlos, el sistema está trabajando a ciegas: pedí que se vuelva a cargar ese estudio antes de calibrar.
- [ ] **Conocés ese estudio.** Tenés que poder mirar una persona y su respuesta y saber si corresponde o no.
- [ ] **Hacé una consulta de prueba cualquiera.** Si aparece algún aviso de que el sistema está funcionando "en modo degradado" o "sin verificación", avisá a quien lo administra: calibrar así da números que después no valen.
- [ ] **Tenés esta planilla a mano** (sección 6) o una hoja de cálculo con esas columnas.

---

## 4. Cómo es la sesión

Vas a correr **diez consultas**, anotar qué salió, y de ahí salen los tres números.

Por cada consulta hacés siempre lo mismo:

1. Escribís el criterio y consultás.
2. Mirás la lista que devuelve.
3. **Por cada persona de la lista, decidís: ¿corresponde o no?** Para eso mirá la respuesta que el sistema muestra como justificación (dice de qué estudio y qué pregunta salió). Esa es tu materia prima.
4. Anotás en la planilla: cuántos salieron, cuántos corresponden, y el puntaje del **peor de los que sí corresponden** y el del **mejor de los que no**.

Esos dos últimos números son los que después definen el umbral. Son la parte más importante de la anotación, así que no la saltees aunque parezca tediosa.

---

## 5. Las diez consultas

Usá criterios **de tus propios estudios**. Los ejemplos son de un estudio de bebidas: cambialos por lo que corresponda.

### Grupo 1 — Para ver si lo básico funciona (3 consultas)

**1. Algo frecuente y claro.** Ejemplo: *"consume fernet"*.
Esperás que aparezcan arriba los que efectivamente lo eligieron.

**2. Algo poco frecuente.** Ejemplo: *"consume whisky"*.
Sirve para ver que no se pierda lo minoritario entre lo mayoritario.

**3. Algo que la gente contó con sus palabras.** Ejemplo: *"le gusta el sabor amargo"*.
Prueba la búsqueda sobre respuestas escritas libremente.

### Grupo 2 — La prueba de fuego (4 consultas)

Este grupo es el que más importa. Son los casos donde la máquina se confunde sola y donde se ve si el sistema realmente entiende o solo empareja palabras parecidas.

**4. Una postura.** Ejemplo: *"está en contra del gobierno"*.
**Lo que hay que mirar:** que **no aparezcan los que están a favor**. Para una computadora, "a favor" y "en contra" se parecen muchísimo, porque hablan de lo mismo. Si en tu lista aparecen los del bando contrario, ese es el problema más serio que puede tener el sistema y hay que avisarlo.

**5. La postura inversa.** La misma pregunta al revés: *"está a favor del gobierno"*.
**Lo que hay que mirar:** que las dos listas sean **casi distintas**. Si te devuelve más o menos la misma gente que la consulta 4, el sistema no está distinguiendo nada.

**6. Dos cosas parecidas pero distintas.** Corré *"toma fernet"* y *"toma whisky"* y compará.
**Lo que hay que mirar:** que no devuelvan el mismo grupo de gente.

**7. Algo que dejó de pasar.** Ejemplo: *"dejó de tomar fernet"*.
Para una computadora esto se parece muchísimo a "toma fernet", aunque signifique lo contrario.

### Grupo 3 — Los bordes (3 consultas)

**8. Algo que nadie preguntó nunca.** Ejemplo: *"practica kitesurf"*, si ningún estudio tocó deportes.
**Lo que hay que mirar:** que devuelva vacío, o todo marcado como baja confianza. **Si te devuelve una lista de gente con aire de segura, eso es un problema grave**: significa que el sistema te va a inventar resultados cuando no tenga la información. Es el error más peligroso, porque parece un resultado válido.

**9. Dos condiciones juntas, modo estricto.** Ejemplo: *"toma fernet"* + *"menor de 35"*.
Mirá que el filtro de edad realmente achique la lista.

**10. Las mismas dos condiciones, modo flexible.** Compará con la 9.
El modo flexible debería sumar gente de la que **no se sabe** una de las dos cosas. Lo que **no** debería hacer es sumar gente de la que se sabe lo contrario.

---

## 6. La planilla

Una fila por consulta:

| # | Qué busqué | Cuántos salieron | Cuántos corresponden | Puntaje del peor correcto | Puntaje del mejor incorrecto | Cuánto tardó | Observaciones |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | |
| 2 | | | | | | | |
| … | | | | | | | |

**Dónde ves los puntajes y el tiempo:** en la misma pantalla de resultados. Si no los encontrás, preguntá a quien administra el sistema dónde se muestran; están, solo puede cambiar el lugar.

---

## 7. Cómo sale cada número

### El umbral (el importante)

1. Mirá la columna **"puntaje del peor correcto"** de las diez consultas.
2. Agarrá **el más alto de todos** (o sea, el resultado correcto que peor puntaje sacó en toda la sesión).
3. Ese, redondeado un poquito para arriba, es tu umbral.

*Lógica: si bajaras de ahí, estarías dejando afuera a alguien que sí correspondía.*

4. **Después mirá la otra columna**, "mejor incorrecto". Si hay consultas donde el mejor incorrecto tiene **mejor puntaje** que el peor correcto, significa que los buenos y los malos están mezclados y ningún número los separa bien. **No fuerces el umbral para tapar eso**: anotalo como una limitación conocida y comentalo. Es información valiosa, no un fracaso.

### Cuántos junta

Tomá una consulta del grupo 1 y corrérela con 200, después con 400.
- Si **sale lo mismo**, con 200 alcanza. Listo.
- Si con 400 **aparecen correctos nuevos**, subilo y probá una vez más.

### A cuántos mira con atención

Fijate cuántos de los que miró con atención terminaron en la lista final:
- Si de 25 terminan entrando siempre unos **8**, estás pagando de más: bajalo a 12 o 15.
- Si entran **casi todos los 25**, te estás quedando corto: subilo, porque hay gente buena que ni miró.

---

## 8. Si algo se ve mal

| Lo que ves | Qué suele ser | Qué hacer |
|---|---|---|
| Aparecen los de la postura contraria (consultas 4 y 5) | El sistema no está haciendo la revisión con atención | Avisá a quien administra: probablemente falte una configuración |
| Devuelve gente segura para algo que nadie preguntó (consulta 8) | El umbral está muy permisivo | Bajalo y volvé a probar esa consulta |
| Los puntajes de los correctos están muy desparejos entre consultas | Algún estudio se cargó con las preguntas mal escritas | Identificá cuál y pedí que se vuelva a cargar |
| Todo tarda mucho | Está mirando con atención a demasiada gente | Bajá el segundo número (`top_k`) |
| Las listas vienen casi vacías siempre | Puede ser poco corpus, o umbral muy exigente | Probá subiendo el umbral; si sigue, avisá |

---

## 9. Cuándo llamar a alguien técnico

No intentes resolver estas solo; anotalas y pasalas:

- Un aviso de que el sistema trabaja **sin verificación** o **degradado**.
- Que aparezcan **personas que no deberían estar** (por ejemplo, gente dada de baja).
- Que las listas vengan **vacías siempre**, incluso para cosas que sabés que están en el estudio.
- Que los tiempos sean de **más de 10 o 15 segundos** aun bajando los números.
- Cualquier mensaje de error.

---

## 10. Cuando terminaste

Dejá anotado y comunicado:

- [ ] Los tres números que quedaron, y de dónde salió cada uno.
- [ ] Las limitaciones que encontraste (por ejemplo, criterios donde el sistema se confunde).
- [ ] Cuánto tardan las consultas en general.
- [ ] Qué tipo de preguntas funcionan bien y cuáles no, para que el resto del equipo lo sepa.

> **Un consejo que vale más que los tres números:** cuando puedas, **acotá antes por panel o por segmento** (edad, zona, sexo). Esos filtros no le cuestan nada al sistema y hacen dos cosas a la vez: la consulta sale más rápida y más barata, y el resultado es más preciso, porque busca sobre menos gente y más pertinente.

Y algo para tener presente siempre: esto devuelve un **orden de aproximación**, no una lista exacta. Sirve para encontrar y explorar, no para afirmar «estos son todos los que cumplen». Cuando presentes resultados a un cliente, conviene decirlo así.
