"""Reproduce lo que hace `_distancias` del script de diagnóstico, paso a paso,
mostrando qué devuelve cada parte. Correr desde la raíz del repo:

    export EMBEDDINGS_API_KEY="$(gcloud secrets versions access latest \
        --secret=EMBEDDINGS_API_KEY --project=gestion-paneles)"
    python3 probar_distancias.py
"""
import os
import sys

sys.path.insert(0, "functions")

from panel_api import embeddings, semantica          # noqa: E402
from panel_api import db                             # noqa: E402

import psycopg                                        # noqa: E402

DSN = os.environ["DSN_SEMANTICA"]
PATRON = "%xiaomi%"

conn = psycopg.connect(DSN, row_factory=psycopg.rows.dict_row)

print("== 1. ¿A qué base estoy conectado? ==")
print(db.una(conn, "select current_database() as base, count(*) as respuestas "
                   "from respuesta")) 

print("\n== 2. Solo el WHERE, sin vector ==")
print(db.una(conn,
             "select count(*) as n from respuesta r "
             " where r.valor_texto ilike %s or r.texto_embebido ilike %s",
             (PATRON, PATRON)))

print("\n== 3. ¿Cuántas filas tienen embedding nulo? ==")
print(db.una(conn, "select count(*) as nulos from respuesta "
                   "where embedding is null"))

print("\n== 4. La consulta completa, con un vector de ceros ==")
cero = semantica._vector([0.0] * 512)
try:
    filas = db.todas(conn,
                     "select r.id, r.valor_texto, r.embedding <=> %s::vector as distancia "
                     "  from respuesta r "
                     " where r.valor_texto ilike %s or r.texto_embebido ilike %s "
                     " order by 3 limit %s",
                     (cero, PATRON, PATRON, 5))
    print(f"filas: {len(filas)}")
    for f in filas[:3]:
        print("  ", f)
except Exception as e:                                # noqa: BLE001
    print("ERROR:", type(e).__name__, e)

print("\n== 5. Lo mismo, con el vector real del criterio ==")
try:
    prov = embeddings.crear()
    vec = prov.embeber_criterio(["gente que usa un celular xiaomi"],
                                tipo="query")[0]
    print("dims:", len(vec))
    filas = db.todas(conn,
                     "select r.id, r.valor_texto, r.embedding <=> %s::vector as distancia "
                     "  from respuesta r "
                     " where r.valor_texto ilike %s or r.texto_embebido ilike %s "
                     " order by 3 limit %s",
                     (semantica._vector(vec), PATRON, PATRON, 5))
    print(f"filas: {len(filas)}")
    for f in filas[:3]:
        print("  ", f)
except Exception as e:                                # noqa: BLE001
    print("ERROR:", type(e).__name__, e)

conn.close()
