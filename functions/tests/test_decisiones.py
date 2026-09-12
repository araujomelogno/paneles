"""`docs/decisiones.md` tiene que seguir siendo cierto.

Un documento de decisiones que envejece mal es peor que no tenerlo: alguien
lo lee, confía, y actúa sobre algo que cambió hace seis meses. Estas pruebas
atan sus afirmaciones verificables al código, de modo que quien cambie una
decisión se entere de que hay un documento que la explica.

No se comprueba la prosa —eso no se puede— sino lo que el documento afirma
en concreto: rutas de archivos, valores de constantes y las garantías que
enumera.
"""

import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
DOCUMENTO = RAIZ / "docs" / "decisiones.md"


@pytest.fixture(scope="module")
def texto():
    assert DOCUMENTO.exists(), "falta docs/decisiones.md"
    return DOCUMENTO.read_text(encoding="utf-8")


def test_todos_los_archivos_que_nombra_existen(texto):
    """«Dónde vive» es la parte que se usa: una ruta rota hace que el lector
    desconfíe de todo lo demás."""
    rutas = {
        r.strip()
        for r in re.findall(r"`([A-Za-z0-9_./ -]+\.(?:py|js|sql|md|sh|json))`", texto)
        if "/" in r
    }
    faltan = sorted(r for r in rutas if not (RAIZ / r).exists())
    assert not faltan, f"decisiones.md nombra archivos que no existen: {faltan}"
    assert len(rutas) > 20, "el documento tendría que citar el código que explica"


def test_los_valores_de_la_tabla_de_defaults_son_los_del_codigo(texto):
    """D31 lista los defaults con su valor. Si alguien calibra un umbral y no
    actualiza la tabla, el documento pasa a mentir sobre el estado real."""
    from panel_api import calidad, consultas, muestreo, puntos

    esperados = {
        "`max_convocatorias_ventana` / `ventana_dias`":
            f"{muestreo.DEFAULTS_FATIGA['max_convocatorias_ventana']} / "
            f"{muestreo.DEFAULTS_FATIGA['ventana_dias']}",
        "`dias_minimos_entre`": str(muestreo.DEFAULTS_FATIGA["dias_minimos_entre"]),
        "`SPEEDER_SEGUNDOS`": str(calidad.SPEEDER_SEGUNDOS),
        "`STRAIGHTLINER_VARIANZA`": str(calidad.STRAIGHTLINER_VARIANZA),
        "`MIN_ITEMS_BATERIA`": str(calidad.MIN_ITEMS_BATERIA),
        "`PUNTOS_POR_PARTICIPACION`": str(puntos.PUNTOS_POR_PARTICIPACION),
        "`MESES_DE_VIGENCIA`": str(puntos.MESES_DE_VIGENCIA),
        "`TOP_N_POR_DEFECTO` / `TOP_K_POR_DEFECTO`":
            f"{consultas.TOP_N_POR_DEFECTO} / {consultas.TOP_K_POR_DEFECTO}",
        "`UMBRAL_DISTANCIA`": str(consultas.UMBRAL_DISTANCIA),
        "`UMBRAL_SEGMENTO`": str(consultas.UMBRAL_SEGMENTO),
    }
    filas = {
        celdas[1].strip(): celdas[2].strip()
        for linea in texto.splitlines()
        if linea.startswith("| `") and len(celdas := linea.split("|")) > 3
    }
    for constante, valor in esperados.items():
        assert constante in filas, f"D31 no lista {constante}"
        assert filas[constante] == valor, (
            f"decisiones.md dice que {constante} vale «{filas[constante]}» "
            f"y el código dice «{valor}»"
        )


def test_las_garantias_que_afirma_siguen_siendo_ciertas():
    """Las afirmaciones puntuales del documento, contra el código."""
    from panel_api import auth, puntos, ruteo

    # D29 — las rutas públicas son exactamente dos.
    assert ruteo.PUBLICAS == frozenset({
        ("GET", "/inscripciones/formulario"),
        ("POST", "/inscripciones"),
    })
    assert not ruteo.es_publica("GET", "/inscripciones"), (
        "la bandeja de aprobación lista datos de personas: no puede ser pública"
    )

    # D12 — reidentificar y exportar son permisos distintos, y el analista
    # consulta pero no tiene ninguno de los dos.
    assert "reidentificar" in auth.PERMISOS and "exportar_identificado" in auth.PERMISOS
    assert "analista" in auth.PERMISOS["consultar"]
    assert "analista" not in auth.PERMISOS["reidentificar"]
    assert "analista" not in auth.PERMISOS["exportar_identificado"]

    # D13 — los roles siguen siendo cuatro.
    assert auth.ROLES == ("admin", "operaciones", "analista", "dpo")

    # D19 — los cuatro tipos de movimiento del ledger.
    assert puntos.TIPOS == ("earn", "canje", "ajuste", "vencimiento")


def test_no_existe_ninguna_columna_saldo(conn_boveda):
    """D19 — «no hay ninguna columna `saldo` en ninguna tabla». Es la
    afirmación más fuerte del documento y la más fácil de romper: alguien
    agrega un campo denormalizado «para que sea más rápido» y el ledger deja
    de ser la única fuente de verdad."""
    from panel_api import db

    filas = db.todas(
        conn_boveda,
        """
        select table_name, column_name
          from information_schema.columns
         where table_schema = 'public' and column_name = 'saldo'
        """,
    )
    assert filas == [], f"apareció una columna saldo: {filas}"


def test_el_indice_para_el_index_de_decisiones_esta_completo(texto):
    """Cada decisión del cuerpo tiene que estar en el índice, y viceversa:
    una entrada que no figura no se lee."""
    en_el_cuerpo = set(re.findall(r'<a id="(d\d+)"></a>', texto))
    en_el_indice = set(re.findall(r"\| \[D(\d+)\]\(#d\d+\)", texto))
    en_el_indice = {f"d{n}" for n in en_el_indice}
    assert en_el_cuerpo == en_el_indice, (
        f"solo en el cuerpo: {sorted(en_el_cuerpo - en_el_indice)}; "
        f"solo en el índice: {sorted(en_el_indice - en_el_cuerpo)}"
    )
    assert len(en_el_cuerpo) >= 25
