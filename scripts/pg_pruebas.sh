#!/usr/bin/env bash
# Levanta un Postgres temporal para correr las pruebas y exporta los DSN.
#
#   source scripts/pg_pruebas.sh   # arranca y exporta
#   scripts/pg_pruebas.sh detener  # apaga y borra el cluster
#
# Los dos stores se crean como bases distintas del mismo cluster: alcanza
# para probar el contrato de cruce (que es por `id_persona` y `ref_estudio`,
# sin FK). En producción son dos instancias Cloud SQL separadas, y eso lo
# verifica `config.cargar()`.
set -euo pipefail

PGDIR="${PANELES_PGDIR:-/tmp/paneles-pg}"
PGPORT="${PANELES_PGPORT:-55432}"
BIN="$(ls -d /usr/lib/postgresql/*/bin | tail -1)"
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Postgres se niega a correr como root. Si estamos como root (contenedores
# de desarrollo), se delega en el usuario `postgres`.
if [ "$(id -u)" = "0" ]; then COMO_PG=(setpriv --reuid=postgres --regid=postgres --clear-groups); else COMO_PG=(); fi

detener() {
  "${COMO_PG[@]}" "$BIN/pg_ctl" -D "$PGDIR/data" -m immediate stop >/dev/null 2>&1 || true
  rm -rf "$PGDIR"
  echo "cluster de pruebas detenido"
}

if [ "${1:-}" = "detener" ]; then detener; exit 0; fi

if [ ! -d "$PGDIR/data" ]; then
  mkdir -p "$PGDIR"
  [ "$(id -u)" = "0" ] && chown -R postgres:postgres "$PGDIR"
  "${COMO_PG[@]}" "$BIN/initdb" -D "$PGDIR/data" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null
fi

if ! "$BIN/pg_ctl" -D "$PGDIR/data" status >/dev/null 2>&1; then
  "${COMO_PG[@]}" "$BIN/pg_ctl" -D "$PGDIR/data" -o "-p $PGPORT -k $PGDIR -c listen_addresses=localhost" \
    -l "$PGDIR/servidor.log" -w start >/dev/null
fi

export PGHOST=localhost PGPORT="$PGPORT" PGUSER=postgres

for base in paneles_boveda paneles_semantica; do
  psql -q -d postgres -tAc "select 1 from pg_database where datname='$base'" \
    | grep -q 1 || createdb "$base"
done

psql -q -d paneles_boveda  -v ON_ERROR_STOP=1 -f "$RAIZ/db/boveda/0001_init.sql"       >/dev/null 2>&1 || true
psql -q -d paneles_boveda  -v ON_ERROR_STOP=1 -f "$RAIZ/db/boveda/0002_revision_alta.sql" >/dev/null 2>&1 || true
psql -q -d paneles_boveda  -v ON_ERROR_STOP=1 -f "$RAIZ/db/boveda/0003_baja_persona.sql"  >/dev/null 2>&1 || true
psql -q -d paneles_semantica -v ON_ERROR_STOP=1 -f "$RAIZ/db/semantica/0001_init.sql"      >/dev/null 2>&1 || true

export DSN_BOVEDA="postgresql://postgres@localhost:$PGPORT/paneles_boveda"
export DSN_SEMANTICA="postgresql://postgres@localhost:$PGPORT/paneles_semantica"
export EMBEDDINGS_PROVEEDOR=deterministico
echo "DSN_BOVEDA=$DSN_BOVEDA"
echo "DSN_SEMANTICA=$DSN_SEMANTICA"
