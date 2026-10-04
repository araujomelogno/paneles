# BUG — `v_persona_convocable` devuelve una fila por consentimiento, no por persona

**Sistema:** Gestión de paneles · bóveda · superficie externa (migración `0014`)
**Severidad:** Alta — **todo conteo que haga un consumidor externo queda inflado**
**Detectado:** 2026-10-04, con la primera carga real de panelistas
**Estado:** Para corregir

---

## 1. Qué pasa

`v_persona_convocable` devuelve **una fila por cada consentimiento vigente de
cada persona**, en vez de una fila por persona. Con dos finalidades vigentes
(`contacto_participacion` y `uso_semantico`), cada persona aparece **dos veces**.

## 2. Evidencia

```
select count(*) as filas, count(distinct id_persona) as personas
  from v_persona_convocable;

 filas | personas
-------+----------
  2016 |     1008
```

Y la causa queda a la vista:

```
select finalidad, count(*) from consentimiento where estado='vigente' group by 1;

       finalidad        | count
------------------------+-------
 contacto_participacion |  1008
 uso_semantico          |  1008
```

1008 personas × 2 consentimientos = 2016 filas. La bóveda tiene exactamente
1008 personas (`select count(*) from persona` → 1008), así que **no hay
duplicados de personas**: el duplicado lo introduce la vista.

Se detectó porque la batería de verificación de COLOQUIO reportó
`v_persona_convocable: 2016` donde el store semántico tiene 1008 individuos.

## 3. Por qué importa

Esta vista es **la única superficie desde la que un consumidor externo ve
personas** — es el centro del diseño de acceso acotado de la fase 5. Todo lo
que COLOQUIO haga a partir de ella hereda el error:

- El **tamaño de la muestra** disponible se ve al doble.
- Las **cuotas** por segmento se calculan sobre un universo inflado.
- Un listado para invitar trae **cada persona repetida**, y si el consumidor no
  deduplica por su cuenta, puede contactar dos veces a la misma gente.
- Cualquier métrica que publique COLOQUIO sobre su panel será incorrecta.

**No es una fuga de seguridad** —no expone nada que no deba verse— pero sí un
error de datos que se propaga silenciosamente: los valores son correctos, solo
que repetidos, así que nada falla de forma visible.

## 4. Causa probable

La vista hace un `join` con `consentimiento` para aplicar el gate, y ese join
multiplica las filas cuando hay más de una fila de consentimiento vigente por
persona. El gate funciona bien —solo aparece quien consintió— pero la
cardinalidad es la equivocada.

## 5. Qué hay que corregir

- [ ] `v_persona_convocable` devuelve **exactamente una fila por persona**.
- [ ] El gate de consentimiento se mantiene igual: solo aparece quien tiene la
      finalidad requerida vigente. La corrección es de cardinalidad, **no de
      criterio**.
- [ ] Cambiar el `join` por un `exists` (o equivalente) en vez de resolverlo con
      un `distinct`: el `exists` expresa la intención —«existe un consentimiento
      vigente»— y no depende de que ninguna otra columna rompa la unicidad más
      adelante.

> **Por qué `exists` y no `distinct`.** Un `distinct` tapa el síntoma: si
> mañana la vista suma una columna que varía entre las filas duplicadas, el
> `distinct` deja de colapsarlas y el bug vuelve, otra vez en silencio. El
> `exists` no multiplica nunca.

## 6. Revisar también

El mismo patrón puede estar en las otras vistas y funciones de la `0014` que
consultan consentimiento:

- [ ] `v_fatiga_panelista` — hoy devuelve 0 filas, así que el problema no se ve;
      conviene verificarlo cuando haya participaciones registradas.
- [ ] `f_persona_convocable()` — si devuelve un booleano por persona, no está
      afectada; si devuelve filas, sí.
- [ ] `contacto_para_convocatoria()` — verificar que no entregue el contacto más
      de una vez ni genere más de una fila de auditoría por entrega.

## 7. Definition of Done

- [ ] `select count(*) = count(distinct id_persona) from v_persona_convocable`
      es verdadero (test).
- [ ] Una persona con **las dos** finalidades vigentes aparece **una sola vez**
      (test).
- [ ] Una persona con **una sola** finalidad vigente aparece una vez (test).
- [ ] Una persona **sin** la finalidad requerida **no aparece** (test de no
      regresión: el gate no cambia).
- [ ] La batería de COLOQUIO reporta un conteo de `v_persona_convocable` igual
      al número de personas convocables reales.
- [ ] Las demás vistas y funciones de la `0014` verificadas contra el mismo
      patrón.

## 8. Nota sobre la verificación

La batería (`scripts/verificar_coloquio.py`) **no detectó esto**: chequea que la
superficie sea legible y que el gate funcione, pero no que la cardinalidad sea
la correcta. Se encontró comparando a mano contra el store semántico.

- [ ] Agregar a la batería un chequeo de cardinalidad: ninguna vista de la
      superficie externa debe devolver más de una fila por persona.
