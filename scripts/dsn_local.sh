#!/usr/bin/env bash
# Imprime el DSN de un store apuntado al Auth Proxy local.
#
# La clave de la base vive en Secret Manager y no tiene por qué salir de ahí:
# este script trae el DSN de producción, le cambia host y puerto por los del
# proxy y deja intacto el resto —usuario, clave y nombre de base—.
#
#   cloud-sql-proxy gestion-paneles:southamerica-east1:paneles-semantica --port 5433
#   export DSN_SEMANTICA="$(scripts/dsn_local.sh semantica)"
#   python3 scripts/verificar_esquema.py --sql semantica | psql "$DSN_SEMANTICA"
#
# Existe como script y no como una función para pegar en la terminal porque
# esa función tenía comillas anidadas y expansión de shell adentro del código
# Python: al copiarla, un `$2` se pegó literal y terminó de puerto. Acá el
# puerto viaja como argumento de Python, no como interpolación de shell, así
# que no hay nada que se pueda escapar mal.
set -euo pipefail

usar() {
  cat >&2 <<'FIN'
Uso: scripts/dsn_local.sh <boveda|semantica> [puerto]

  Puertos por omisión: bóveda 5432, semántica 5433 —los mismos que usan los
  ejemplos del despliegue—.

  Requiere el Auth Proxy abierto contra esa instancia y permiso de lectura
  sobre el secreto (roles/secretmanager.secretAccessor).
FIN
  exit 2
}

case "${1:-}" in
  boveda)    puerto_por_omision=5432 ;;
  semantica) puerto_por_omision=5433 ;;
  *)         usar ;;
esac
store="$1"
puerto="${2:-$puerto_por_omision}"

case "$puerto" in
  ''|*[!0-9]*) echo "El puerto tiene que ser un número: «$puerto»." >&2; exit 2 ;;
esac

if ! command -v gcloud >/dev/null 2>&1; then
  echo "No está gcloud en el PATH; es de donde sale la clave." >&2
  exit 2
fi

secreto="DSN_$(printf '%s' "$store" | tr '[:lower:]' '[:upper:]')"

if ! dsn="$(gcloud secrets versions access latest --secret="$secreto" 2>&1)"; then
  echo "No se pudo leer el secreto $secreto:" >&2
  printf '  %s\n' "$dsn" >&2
  echo "Revisá el proyecto activo (gcloud config get project) y los permisos." >&2
  exit 2
fi

# El puerto entra por argv, no interpolado en el código: el Python va entre
# comillas simples y la shell no toca nada de lo que hay adentro.
printf '%s' "$dsn" | python3 -c '
import sys, urllib.parse as u

partes = u.urlsplit(sys.stdin.read().strip())
if not partes.hostname or not partes.username:
    sys.exit("El secreto no tiene forma de DSN (postgresql://usuario:clave@host/base).")
# La clave se reinserta tal como vino, sin decodificar: llega
# percent-encoded y así sobrevive un @, una / o un # adentro.
autoridad = f"{partes.username}:{partes.password}@127.0.0.1:{sys.argv[1]}"
print(u.urlunsplit((partes.scheme, autoridad, partes.path, "", "")))
' "$puerto"
