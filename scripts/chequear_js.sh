#!/usr/bin/env bash
# Chequeo de sintaxis de los módulos ES del frontend (no hay build step).
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fallos=0
while IFS= read -r archivo; do
  cp "$archivo" "$TMP/m.mjs"
  if node --check "$TMP/m.mjs" 2>"$TMP/err"; then
    echo "ok   ${archivo#"$RAIZ"/}"
  else
    echo "FALLA ${archivo#"$RAIZ"/}"; cat "$TMP/err"; fallos=$((fallos+1))
  fi
done < <(find "$RAIZ/web/public/js" -name '*.js' | sort)
[ "$fallos" -eq 0 ] || exit 1
