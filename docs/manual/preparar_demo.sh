#!/usr/bin/env bash
# Prepara una copia de la SPA en modo demo para capturar las pantallas del
# manual, y la sirve en http://localhost:8099.
#
#   docs/manual/preparar_demo.sh            # prepara y sirve
#   docs/manual/preparar_demo.sh detener    # apaga el servidor
#
# Tres retoques sobre la copia, ninguno sobre el repo:
#
#   1. `apiKey` en TU_API_KEY  → la app arranca en MODO DEMO, con datos
#      sembrados. Es lo que hace que el manual muestre pantallas con
#      contenido en vez de listas vacías.
#   2. Se oculta el banner celeste de modo demo: el manual documenta la
#      aplicación de producción, y ese cartel solo aparece en la copia.
#   3. Se hace que `?login` muestre la pantalla de ingreso. En modo demo la
#      app entra sola —no hay a quién autenticar— así que sin esto la
#      primera captura del manual, que es justamente el login, no se puede
#      tomar.
#   4. Se saca el aviso de degradación «modo_demo» que la consulta agrega a
#      su respuesta. Es el mismo caso que el banner: existe solo porque la
#      copia no tiene embeddings, cross-encoder ni API de Claude. En
#      producción esas tres etapas corren, así que ese aviso no aparece y
#      dejarlo en el manual mostraría una degradación que el usuario no va a
#      ver. Los demás avisos de degradación —los de verdad, cuando falta una
#      clave— quedan como están.
#   5. Se ponen los nombres de proveedor que informa producción (`voyage` y
#      `claude`) en lugar de los de la copia (`lexico (demo)`). El manual
#      explica qué significa cada etapa del diagnóstico, y ver ahí un
#      proveedor que no existe en producción confunde. Los tiempos y los
#      puntajes siguen siendo los de la copia, como todo el resto de los
#      datos de ejemplo.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESTINO="${MANUAL_DEMO_DIR:-/tmp/demo-manual}"
PUERTO="${MANUAL_DEMO_PUERTO:-8099}"

if [ "${1:-}" = "detener" ]; then
  pkill -f "http.server $PUERTO" 2>/dev/null || true
  echo "servidor detenido"
  exit 0
fi

rm -rf "$DESTINO"
cp -r "$RAIZ/web/public" "$DESTINO"

sed -i 's/apiKey: *"[^"]*"/apiKey: "TU_API_KEY"/' "$DESTINO/index.html"
sed -i 's/const banner = api.estado.demo ?/const banner = false ?/' "$DESTINO/js/app.js"
sed -i "s|    // Sin proyecto Firebase: se entra directo al modo demo.|    if (location.search.includes('login')) { renderLogin(); return; }|" \
  "$DESTINO/js/app.js"
sed -i "s|^    degradaciones: \[{|    degradaciones: [].concat(false ? [{|" "$DESTINO/js/demo.js"
sed -i "s|^    }],$|    }] : []),|" "$DESTINO/js/demo.js"
sed -i "s|aplicado: true, proveedor: 'lexico (demo)'|aplicado: true, proveedor: 'voyage'|" "$DESTINO/js/demo.js"
sed -i "s|aplicada: true, proveedor: 'lexico (demo)'|aplicada: true, proveedor: 'claude'|" "$DESTINO/js/demo.js"
sed -i "s|reranker: 'lexico (demo)', verificador: 'lexico (demo)'|reranker: 'voyage', verificador: 'claude'|" "$DESTINO/js/demo.js"

for patron in 'TU_API_KEY' 'const banner = false' "location.search.includes('login')"; do
  grep -q "$patron" "$DESTINO/index.html" "$DESTINO/js/app.js" \
    || { echo "ERROR: no se aplicó el retoque «$patron»"; exit 1; }
done
grep -q 'degradaciones: \[\].concat(false' "$DESTINO/js/demo.js" \
  || { echo "ERROR: no se aplicó el retoque del aviso de modo demo"; exit 1; }
grep -q "lexico (demo)" "$DESTINO/js/demo.js" \
  && { echo "ERROR: quedó algún «lexico (demo)» sin reemplazar"; exit 1; }
node --check "$DESTINO/js/demo.js" \
  || { echo "ERROR: los retoques rompieron demo.js"; exit 1; }

pkill -f "http.server $PUERTO" 2>/dev/null || true
sleep 0.5
cd "$DESTINO" && nohup python3 -m http.server "$PUERTO" > "$DESTINO/servidor.log" 2>&1 &
sleep 1.5

curl -sf -o /dev/null "http://localhost:$PUERTO/index.html" \
  || { echo "ERROR: el servidor no responde en el puerto $PUERTO"; exit 1; }
echo "copia en modo demo servida en http://localhost:$PUERTO ($DESTINO)"
