# Despliegue — el celular como clave de dedup

**Proyecto:** `gestion-paneles` · **Región:** `southamerica-east1`
**Precondición:** la bóveda al día hasta la **`0021`**. El store semántico no
se toca.
**Qué cambia:** una migración en la bóveda (`0022`, un índice) y código de
backend y de frontend.
**Qué NO cambia:** ningún secreto, rol, permiso de IAM, cola ni
configuración de función. Nada que coordinar con COLOQUIO: la superficie
externa no cambia.

---

## Qué trae

El dedup reconoce ahora a una persona por **documento → correo → celular →
nombre + fecha de nacimiento**. El celular es más cauto que el correo,
porque puede ser compartido (D64):

| Situación | Qué hace |
|---|---|
| Una sola persona tiene ese celular, nada la contradice y el nombre es compatible | La reutiliza |
| La titular tiene otro documento u otro correo | Son dos personas: el celular no decide |
| La única titular se llama de otra forma | Revisión de altas |
| Varias personas lo tienen | Revisión de altas |

Se compara en E.164: «099 123 456» y «+598 99 123 456» son el mismo número.
En la importación, el celular cuenta como clave de dedup en la revisión, y una
columna de celular de relleno (el mismo número en todo el archivo) frena la
carga, igual que un documento implausible.

---

## Cómo leer este documento

Cada paso trae el comando y la salida que se obtuvo al ensayarlo, contra una
bóveda llevada a la `0021`, con 1008 personas y 1003 celulares, tres de ellos
guardados fuera de E.164 a propósito. Donde dice «se obtuvo» es salida real.

---

## Paso 0 · Preparar la terminal

```bash
setopt INTERACTIVE_COMMENTS        # macOS con zsh
export PROYECTO="gestion-paneles"
export REGION="southamerica-east1"
gcloud config set project "$PROYECTO"
git checkout main && git pull
ls db/boveda/0022_celular_clave_de_dedup.sql
```

---

## Paso 1 · Túnel y DSN de la bóveda

```bash
cloud-sql-proxy gestion-paneles:$REGION:paneles-boveda --port 5432 &
export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
psql "$DSN_BOVEDA" -tAc "select current_database()"
```

Tiene que devolver `paneles_boveda`.

---

## Paso 2 · Confirmar qué falta

```bash
psql "$DSN_BOVEDA" -tAc "
  select case when to_regclass('persona_celular_idx') is null
              then 'falta 0022_celular_clave_de_dedup.sql'
              else 'la 0022 ya está aplicada' end"
psql "$DSN_BOVEDA" -tAc "select to_regclass('v_persona_finalidad_vigente') is not null"
```

En el ensayo:

```
falta 0022_celular_clave_de_dedup.sql
t
```

La `t` es la precondición: la `0021` está. Si dice `f`, **parar** y aplicar
antes `DESPLIEGUE - Fase 7 completa, Fase 8 y bug convocable.md`.

---

## Paso 3 · Mirar los celulares que ya hay

Antes de que el dedup empiece a usarlos, conviene saber dos cosas. Ninguna
bloquea el despliegue.

**Cuántos no están en E.164.** No van a coincidir con nada —es lo seguro: no
fusionan—, pero tampoco van a servir como clave:

```bash
psql "$DSN_BOVEDA" -c "
  select count(*) filter (where celular is not null)                         as con_celular,
         count(*) filter (where celular is not null
                            and celular !~ '^\+[0-9]{8,15}$')                as fuera_de_e164
    from persona"
```

En el ensayo:

```
 con_celular | fuera_de_e164
-------------+---------------
        1003 |             3
```

Si `fuera_de_e164` es alto, son celulares guardados antes de R4.4. Corregirlos
es editar la ficha de cada persona (la edición normaliza), y no hace falta
hacerlo para desplegar.

**Cuántos celulares comparten varias personas.** Desde el despliegue, un alta
nueva con uno de esos números va a ir a **revisión de altas** en vez de crearse
o reutilizarse:

```bash
psql "$DSN_BOVEDA" -c "
  select celular, count(*) as personas from persona
   where celular is not null group by 1 having count(*) > 1
   order by 2 desc limit 10"
```

En el ensayo no había ninguno (`0 rows`). Si en producción aparecen muchos con
el mismo número, casi seguro es un número de relleno de alguna carga vieja:
vale mirarlo antes de que empiece a mandar altas a revisión.

---

## Paso 4 · Aplicar `boveda/0022`

```bash
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 -f db/boveda/0022_celular_clave_de_dedup.sql
```

Salida completa del ensayo:

```
BEGIN
CREATE INDEX
COMMENT
COMMIT
```

Es un índice sobre `persona`. Mientras se construye, las altas esperan; con
miles de personas son milisegundos.

> **Se puede correr dos veces.** La segunda imprime
> `NOTICE: relation "persona_celular_idx" already exists, skipping` y termina
> en `COMMIT` sin cambiar nada. Se verificó en el ensayo.

Que quedó como corresponde, **no único**:

```bash
psql "$DSN_BOVEDA" -c "
  select indexname, indexdef from pg_indexes where indexname = 'persona_celular_idx'"
```

```
      indexname      |                                               indexdef
---------------------+------------------------------------------------------------------------------------------------------
 persona_celular_idx | CREATE INDEX persona_celular_idx ON public.persona USING btree (celular) WHERE (celular IS NOT NULL)
```

Si dijera `CREATE UNIQUE INDEX`, está mal: un celular compartido haría fallar
un alta.

---

## Paso 5 · Verificar esquema y privilegios

```bash
PYTHONPATH=functions python3 -c "
from panel_api import esquema, db, config
cfg = config.cargar()
with db.boveda(cfg) as b, db.semantica(cfg) as s:
    d = esquema.revisar_stores(b, s)
for store in ('boveda', 'semantica'):
    r = d[store]
    print(store, 'completo' if r['completo'] else 'FALTAN: %s' % r['faltantes'])"
```

En el ensayo, con la `0022` aplicada:

```
boveda completo
semantica completo
```

Y sin aplicar, que es lo que importa de este paso: la `0022` solo crea un
índice, y hasta este cambio un índice no se veía desde el verificador. Ahora
se ve. Con el índice tirado a propósito, el ensayo dio:

```
boveda FALTAN: [('0022_celular_clave_de_dedup.sql', 'persona_celular_idx')]
```

y lo mismo la consulta SQL de `scripts/verificar_esquema.py --sql boveda`.

La batería de COLOQUIO no tendría por qué cambiar —un índice no toca
permisos—, pero se corre igual:

```bash
python3 scripts/verificar_coloquio.py
```

Contra el cluster de ensayo dio `18 chequeos · 18 pasados · 0 fallidos · 0
omitidos`. Contra Cloud SQL lo esperado es `18 · 17 · 0 · 1 omitido` (el
intruso, ver `DESPLIEGUE - COLOQUIO Fase 0.md` §7.1.1).

---

## Paso 6 · Desplegar

**Después** de la `0022`. Al revés no rompe nada —el dedup busca igual, solo
que recorriendo la tabla—, pero un alta por archivo grande podría tardar.

```bash
export VPC_CONNECTOR="$(gcloud functions describe api \
  --gen2 --region="$REGION" --project="$PROYECTO" \
  --format='value(serviceConfig.vpcConnector)')"
printf 'Conector: %s\n' "$VPC_CONNECTOR"
```

**Si imprime vacío, parar** (la función se desplegaría sin conector y moriría
a los 127 segundos).

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"

for f in api procesaringesta; do
  printf '%s: ' "$f"
  gcloud functions describe "$f" --gen2 --region="$REGION" --project="$PROYECTO" \
    --format='value(serviceConfig.vpcConnector,serviceConfig.vpcConnectorEgressSettings)'
done
```

Las dos con el mismo conector y `PRIVATE_RANGES_ONLY`. Recargá con
`Cmd+Shift+R`.

---

## Paso 7 · Probar

Con un panelista de prueba que tenga celular y **sin** documento ni correo:

1. **Alta manual** con el mismo nombre y el celular escrito de otra forma
   («099-123-456» si estaba como «+59899123456»). Tiene que decir que **ya
   estaba enrolada**, por celular.
2. **Alta manual** con el mismo celular y **otro nombre**. Tiene que quedar en
   **Revisión de altas**, con el motivo «Celular de otra persona registrada
   con otro nombre».
3. **Importación** con una columna marcada como **Celular** y sin documento ni
   correo: la revisión muestra el celular entre las claves de deduplicación,
   con su cobertura, y no avisa que falte clave.

Las dos altas de prueba se descartan desde Revisión de altas y desde la ficha.

---

## Rollback

El código primero, la base después:

```bash
firebase deploy --only functions,hosting --project="$PROYECTO"   # desde el commit anterior
```

```sql
drop index if exists persona_celular_idx;
```

El código anterior no usa el índice, así que dejarlo tampoco molesta. Lo que
no se deshace: las personas que el dedup reutilizó por celular mientras
estuvo activo siguen siendo una sola, y las altas que mandó a revisión siguen
en la bandeja.

---

## Errores y correcciones

| Lo que se ve | Qué pasó | Qué hacer |
|---|---|---|
| `NOTICE: … already exists, skipping` | La `0022` ya estaba | Nada |
| `boveda FALTAN: … persona_celular_idx` | No se aplicó la `0022` | Paso 4 |
| El índice dice `CREATE UNIQUE INDEX` | Alguien lo creó a mano distinto | `drop index persona_celular_idx;` y repetir el paso 4 |
| Muchas altas nuevas en revisión con «Celular que ya tienen varias personas» | Hay un número de relleno en la base | Paso 3: encontrarlo y corregir esas fichas |
| Una importación se frena con «está marcada como celular, pero … trae solo N número(s) distinto(s)» | La columna de celular es de relleno | Desmarcarla como celular o corregir el archivo. Es la guarda funcionando |
| Un celular igual no reutiliza a la persona | Está guardado fuera de E.164 (paso 3), o la titular tiene otro documento/correo, o otro nombre | Ver D64: es la cautela, no un error |

---

## Checklist

- [ ] 0 · En `main`, existe la `0022`
- [ ] 1 · Proxy abierto, `DSN_BOVEDA` → `paneles_boveda`
- [ ] 2 · Falta la `0022` y está la `0021`
- [ ] 3 · Mirados los celulares fuera de E.164 y los compartidos
- [ ] 4 · `0022` aplicada (`BEGIN … COMMIT`), índice **no único**
- [ ] 5 · Los dos stores `completo`; COLOQUIO sin fallidos
- [ ] 6 · `VPC_CONNECTOR` antes del deploy; `functions` y `hosting`; conector verificado
- [ ] 7 · Celular escrito distinto → reutiliza; otro nombre → revisión; importación lo cuenta como clave

---

## Referencias

* `docs/decisiones.md` — D64.
* `docs/manual/manual.html` — §3.3 y §3.4.
* `functions/panel_api/dedup.py` — el paso 3 y sus cautelas, en el docstring.
