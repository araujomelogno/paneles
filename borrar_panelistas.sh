#!/usr/bin/env bash
#
# Borra TODOS los panelistas y todo lo relacionado con ellos, en los dos stores:
# personas, consentimientos, membresías, atributos, alias, puntos y canjes;
# las cargas y encuestas que los originaron, con sus trabajos de ingesta; y
# sus respuestas y embeddings del lado semántico.
#
# Se conservan a propósito: `panel`, el catálogo (atributos, finalidades,
# textos de consentimiento, premios), `sistema_consumidor` y la AUDITORÍA.
#
#   ⚠ IRREVERSIBLE. Pensado para limpiar cargas de prueba.
#     Si los datos son reales, hacer backup antes (ver paso 0).
#
# Uso:
#   1. Levantar los dos proxies (5432 bóveda, 5433 semántica).
#   2. export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"
#      export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
#   3. bash borrar_panelistas.sh            # solo muestra qué se borraría
#      bash borrar_panelistas.sh --ejecutar # borra de verdad
#
set -euo pipefail

EJECUTAR="${1:-}"

: "${DSN_BOVEDA:?Falta DSN_BOVEDA}"
: "${DSN_SEMANTICA:?Falta DSN_SEMANTICA}"

echo "=== Bóveda: contra qué base estoy ==="
psql "$DSN_BOVEDA" -tAc "select current_database() || ' · ' || current_user"
echo "=== Semántica: contra qué base estoy ==="
psql "$DSN_SEMANTICA" -tAc "select current_database() || ' · ' || current_user"
echo

echo "=== Qué se borraría (bóveda) ==="
psql "$DSN_BOVEDA" -c "
select 'persona'            as tabla, count(*) from persona
union all select 'consentimiento',   count(*) from consentimiento
union all select 'membresia',        count(*) from membresia
union all select 'participacion',    count(*) from participacion
union all select 'persona_atributo', count(*) from persona_atributo
union all select 'alias_origen',     count(*) from alias_origen
union all select 'persona_carga',    count(*) from persona_carga
union all select 'carga',            count(*) from carga
union all select 'encuesta',         count(*) from encuesta
union all select 'puntos_movimiento',count(*) from puntos_movimiento
union all select 'canje',            count(*) from canje
union all select 'ingesta_trabajo',  count(*) from ingesta_trabajo
union all select 'ingesta_lote',     count(*) from ingesta_lote;"

echo "=== Qué se borraría (semántica) ==="
psql "$DSN_SEMANTICA" -c "
select 'individuo'  as tabla, count(*) from individuo
union all select 'respuesta',  count(*) from respuesta;"

echo "=== Lo que NO se toca ==="
psql "$DSN_BOVEDA" -c "
select 'panel'                  as tabla, count(*) from panel
union all select 'atributo_demografico',  count(*) from atributo_demografico
union all select 'finalidad_consentimiento', count(*) from finalidad_consentimiento
union all select 'texto_consentimiento',  count(*) from texto_consentimiento
union all select 'sistema_consumidor',    count(*) from sistema_consumidor;"

if [ "$EJECUTAR" != "--ejecutar" ]; then
  echo
  echo "Modo simulación. Para borrar de verdad:"
  echo "  bash borrar_panelistas.sh --ejecutar"
  exit 0
fi

echo
read -r -p "Escribí BORRAR para confirmar: " confirma
[ "$confirma" = "BORRAR" ] || { echo "Cancelado."; exit 1; }

# ── Semántica primero ────────────────────────────────────────────────
# No tiene FK a la bóveda: se referencian por id_persona. Si se borrara
# la bóveda primero y esto fallara, quedarían embeddings de gente que ya
# no existe, sin forma de saber de quién eran.
echo
echo "→ Borrando del store semántico…"
psql "$DSN_SEMANTICA" -v ON_ERROR_STOP=1 --single-transaction -c "
delete from respuesta;
delete from individuo;"

# ── Bóveda ───────────────────────────────────────────────────────────
# Borrar persona arrastra en cascada consentimiento, membresia,
# participacion, persona_atributo y alias_origen.
# Qué se va solo y qué no:
#   · `persona_carga` tiene cascada en los dos lados → se va con `persona`.
#   · `participacion` tiene cascada con `encuesta` → se va al borrar encuestas.
#   · `puntos_movimiento` y `canje` cascadean con `persona`, pero se borran
#     explícitamente por claridad: son el ledger, y conviene verlos en el
#     inventario antes de perderlos.
#
# Las tablas de auditoría (usuario_auditoria, reidentificacion) usan
# `on delete set null` a propósito: el registro de qué se hizo sobrevive
# a la baja de la persona. **No se borran**, y es deliberado: es la prueba de
# que se atendió un pedido de baja.
echo "→ Borrando de la bóveda…"
# `ingesta_lote` antes que `ingesta_trabajo` (tiene FK), y los trabajos antes
# que `carga`: `ingesta_trabajo` apunta a su destino con `destino_tipo` +
# `destino_id`, SIN FK, así que nada se borra en cascada y hay que ser
# explícito o quedan trabajos apuntando a cargas que ya no existen.
psql "$DSN_BOVEDA" -v ON_ERROR_STOP=1 --single-transaction -c "
delete from borrado_pendiente;
delete from convocatoria_externa;
delete from canje;
delete from puntos_movimiento;
delete from ingesta_lote;
delete from ingesta_trabajo;
delete from encuesta;
delete from carga;
delete from persona;"

echo
echo "=== Después ==="
psql "$DSN_BOVEDA"    -c "
select 'persona' as tabla, count(*) from persona
union all select 'persona_carga',   count(*) from persona_carga
union all select 'carga',           count(*) from carga
union all select 'encuesta',        count(*) from encuesta
union all select 'participacion',   count(*) from participacion
union all select 'puntos_movimiento',count(*) from puntos_movimiento
union all select 'ingesta_trabajo', count(*) from ingesta_trabajo
union all select 'ingesta_lote',    count(*) from ingesta_lote;"
psql "$DSN_SEMANTICA" -c "select count(*) as individuos from individuo, lateral (select 1) _ limit 1;" 2>/dev/null \
  || psql "$DSN_SEMANTICA" -c "select count(*) as individuos from individuo;"
echo "Listo."
