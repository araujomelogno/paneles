#!/usr/bin/env python3
"""Comprueba que la región esté declarada igual en todos lados.

La región aparece en tres lugares que tienen que coincidir, y cada uno se
edita por su cuenta:

* `functions/main.py` — dónde se despliega la función.
* `firebase.json` — a qué región apunta el rewrite de `/api/**`. Si no
  coincide con la anterior, Hosting reescribe hacia una función que no
  existe y toda la API devuelve 404.
* El **conector de Acceso a VPC** — tiene que estar en la misma región que
  la función. Si no, el deploy falla; y si llegara a pasar, la función no
  alcanza las instancias de Cloud SQL.

Se corre solo en el `predeploy` de `firebase.json`. La comprobación del
conector solo ocurre si está `VPC_CONNECTOR` en el entorno y `gcloud`
disponible: sin eso avisa, pero no frena el deploy.

    python3 scripts/verificar_region.py
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent

ROJO, AMARILLO, VERDE, FIN = "\033[31m", "\033[33m", "\033[32m", "\033[0m"


def region_de_main():
    texto = (RAIZ / "functions" / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^REGION\s*=\s*"([^"]+)"', texto, re.M)
    if not match:
        raise SystemExit(f"{ROJO}No se encontró REGION en functions/main.py{FIN}")
    return match.group(1)


def region_del_rewrite():
    config = json.loads((RAIZ / "firebase.json").read_text(encoding="utf-8"))
    for rewrite in config.get("hosting", {}).get("rewrites", []):
        destino = rewrite.get("function")
        if isinstance(destino, dict) and destino.get("functionId") == "api":
            return destino.get("region")
        if destino == "api":
            # Forma antigua, sin región: la CLI la deduce del código y eso ya
            # es ambiguo. Se pide la forma explícita.
            return None
    raise SystemExit(
        f"{ROJO}firebase.json no tiene un rewrite de /api/** hacia la función "
        f"`api`.{FIN}"
    )


def region_del_conector(nombre):
    """Región del conector de VPC, si se puede averiguar."""
    if not shutil.which("gcloud"):
        return None, "gcloud no está instalado"
    try:
        salida = subprocess.run(
            ["gcloud", "compute", "networks", "vpc-access", "connectors", "list",
             "--format=value(name,region)"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        return None, f"no se pudo consultar gcloud ({error.__class__.__name__})"

    for linea in salida.splitlines():
        partes = linea.split()
        if partes and partes[0].split("/")[-1] == nombre:
            return partes[-1].split("/")[-1], None
    return None, f"el conector «{nombre}» no existe en este proyecto"


def main():
    problemas = []
    avisos = []

    main_py = region_de_main()
    rewrite = region_del_rewrite()

    print(f"functions/main.py   REGION = {main_py}")
    print(f"firebase.json       rewrite /api/** -> {rewrite or '(sin región)'}")

    if rewrite is None:
        problemas.append(
            "El rewrite de /api/** no declara región. Poné la forma explícita:\n"
            '      "function": { "functionId": "api", "region": "%s" }' % main_py
        )
    elif rewrite != main_py:
        problemas.append(
            f"La función se despliega en «{main_py}» pero el rewrite apunta a "
            f"«{rewrite}».\n"
            f"      Hosting va a reescribir /api/** hacia una función que no "
            f"existe y toda la API va a dar 404."
        )

    nombre_conector = os.environ.get("VPC_CONNECTOR", "").strip()
    if not nombre_conector:
        avisos.append(
            "VPC_CONNECTOR no está en el entorno: la función se va a desplegar "
            "SIN conector de VPC y no va a poder llegar a Cloud SQL.\n"
            "      Si es a propósito (por ejemplo `--only hosting`), ignoralo."
        )
    else:
        print(f"VPC_CONNECTOR       {nombre_conector}")
        conector, motivo = region_del_conector(nombre_conector)
        if conector is None:
            avisos.append(f"No se pudo verificar la región del conector: {motivo}.")
        else:
            print(f"conector            región = {conector}")
            if conector != main_py:
                problemas.append(
                    f"El conector «{nombre_conector}» está en «{conector}» y la "
                    f"función en «{main_py}».\n"
                    f"      Un conector de VPC solo sirve a funciones de su misma "
                    f"región: el deploy va a fallar."
                )

    print()
    for aviso in avisos:
        print(f"{AMARILLO}aviso:{FIN} {aviso}")
    if problemas:
        for problema in problemas:
            print(f"{ROJO}error:{FIN} {problema}")
        print(f"\n{ROJO}La región no está declarada igual en todos lados. "
              f"Deploy cancelado.{FIN}")
        return 1

    print(f"{VERDE}Región coherente: {main_py}{FIN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
