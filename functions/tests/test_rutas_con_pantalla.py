"""Una ruta que nadie llama es indistinguible de una ruta que no existe.

`specs/INFORME_fase7_que_falta.md` encontró tres requisitos de la Fase 7 con
el backend terminado, declarado con su `requisito="R7.x"` y llamado desde
`api.js`… y ninguna pantalla que lo usara. Para quien usa el sistema, esos
requisitos no existían. La recomendación del informe fue que el Definition of
Done de un requisito con pantalla incluya que **alguna página use el
endpoint**, no solo que exista la ruta y esté declarada en `api.js`.

Esta prueba es esa recomendación hecha regla, para las fases que tienen
pantalla desde ahí en adelante (7 y 8): por cada ruta declarada con uno de
esos requisitos busca la función de `api.js` que la llama, y exige que esa
función se use en algún módulo del frontend que no sea `api.js`.

Lo que no puede saber es si la pantalla **muestra** lo que la ruta devuelve
—ese fue el otro hallazgo del informe con R7.3—, y para eso están las pruebas
de cada requisito. Esta cuida que el cable esté conectado.
"""

import pathlib
import re

import pytest

from panel_api import ruteo

RAIZ = pathlib.Path(__file__).resolve().parents[2]
JS = RAIZ / "web" / "public" / "js"

# Las fases a las que se les exige pantalla. Las anteriores tienen rutas
# operativas sin pantalla a propósito (diagnóstico, jobs) y se escribieron
# antes de esta regla.
FASES_CON_PANTALLA = ("R7.", "R8.")

# Rutas de estas fases que **no** tienen pantalla, con el motivo. Vacío hoy;
# si alguna vez hace falta, el motivo va escrito acá y no en la memoria de
# nadie.
SIN_PANTALLA = {}

# `nombre: (args) => VERBO(` seguido del camino entre comillas o backticks.
_LLAMADA = re.compile(
    r"(?P<nombre>[A-Za-z_]\w*)\s*:\s*\([^)]*\)\s*=>\s*"
    r"(?:\n\s*)?(?P<verbo>GET|POST|PUT|PATCH|DELETE|pedir)\(\s*"
    r"(?:'(?P<metodo>[A-Z]+)'\s*,\s*)?[`'\"](?P<camino>[^`'\"]+)[`'\"]")


def _normalizar(camino):
    """`/panelistas/${idPersona}/ficha` y `/panelistas/<id_persona>/ficha`
    pasan a ser el mismo patrón."""
    camino = re.sub(r"\$\{[^}]+\}", "*", camino)
    camino = re.sub(r"<[^>]+>", "*", camino)
    return camino.rstrip("/")


def _llamadas_de_api():
    """`{(metodo, camino): {objeto.funcion, …}}` de todo `api.js`.

    Se recorre por bloques de objeto para saber a cuál pertenece cada
    función: el mismo nombre (`listar`) existe en veinte objetos.
    """
    texto = (JS / "api.js").read_text(encoding="utf-8")
    llamadas = {}
    bloques = re.split(r"(?m)^export const (\w+) = \{", texto)
    for i in range(1, len(bloques), 2):
        objeto, cuerpo = bloques[i], bloques[i + 1]
        for m in _LLAMADA.finditer(cuerpo):
            metodo = m.group("metodo") or m.group("verbo")
            clave = (metodo, _normalizar(m.group("camino")))
            llamadas.setdefault(clave, set()).add(f"{objeto}.{m.group('nombre')}")
    return llamadas


def _usos_fuera_de_api():
    return "\n".join(
        p.read_text(encoding="utf-8")
        for p in JS.rglob("*.js")
        if p.name not in ("api.js", "demo.js"))


def _rutas_de_fases_con_pantalla():
    # `ruteo.RUTAS` guarda `(metodo, expresion, permiso, funcion, patron)`.
    for metodo, _expresion, _permiso, funcion, patron in ruteo.RUTAS:
        requisito = getattr(funcion, "requisito", None) or ""
        if any(f in requisito for f in FASES_CON_PANTALLA):
            yield metodo, patron, requisito


RUTAS = sorted(set(_rutas_de_fases_con_pantalla()))


def test_hay_rutas_que_revisar():
    """Si el recorrido no encontrara ninguna, la prueba de abajo pasaría sin
    mirar nada."""
    assert len(RUTAS) >= 8, RUTAS


@pytest.mark.parametrize("metodo,patron,requisito", RUTAS)
def test_la_ruta_la_usa_alguna_pantalla(metodo, patron, requisito):
    clave = (metodo, _normalizar(patron))
    if clave in SIN_PANTALLA:
        return
    llamadas = _llamadas_de_api()
    funciones = llamadas.get(clave)
    assert funciones, (
        f"{metodo} {patron} ({requisito}) no tiene ninguna función en api.js "
        f"que la llame")
    usos = _usos_fuera_de_api()
    usadas = [f for f in funciones if re.search(
        r"\b" + re.escape(f.split(".", 1)[0]) + r"\." + re.escape(f.split(".", 1)[1])
        + r"\s*\(", usos)]
    assert usadas, (
        f"{metodo} {patron} ({requisito}) se llama desde {sorted(funciones)} "
        f"en api.js, pero ninguna pantalla usa esa función: para quien usa el "
        f"sistema, el requisito no existe")
