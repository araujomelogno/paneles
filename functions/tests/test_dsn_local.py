"""`scripts/dsn_local.sh`: el DSN local sin escribir la clave.

La clave de las bases vive en Secret Manager. Escribirla a mano en un `export`
la deja en el historial de la shell, y en la práctica lo que pasó fue peor: el
marcador de posición de los ejemplos se pegó literal y la conexión falló con un
error que no decía nada de eso.

El script trae el DSN de producción del secreto, le cambia host y puerto por
los del Auth Proxy y deja intacto el resto.
"""

import os
import pathlib
import stat
import subprocess

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
GUION = RAIZ / "scripts" / "dsn_local.sh"

# Una clave con todo lo que rompe una URL si se la maneja sin cuidado.
CLAVE_INCOMODA = "P%3Fss%23wo%2Frd%40x"
SECRETOS = {
    "DSN_SEMANTICA":
        f"postgresql://app_paneles:{CLAVE_INCOMODA}@10.20.30.40:5432/paneles_semantica",
    "DSN_BOVEDA":
        "postgresql://app_paneles:simple@10.20.30.41:5432/paneles_boveda",
}


@pytest.fixture
def gcloud_de_mentira(tmp_path):
    """Un `gcloud` que devuelve los secretos, para no llamar a la nube."""
    binarios = tmp_path / "bin"
    binarios.mkdir()
    casos = "\n".join(
        f"    --secret={nombre}) printf '%s' '{dsn}'; exit 0 ;;"
        for nombre, dsn in SECRETOS.items()
    )
    falso = binarios / "gcloud"
    falso.write_text(
        "#!/bin/sh\nfor arg in \"$@\"; do\n  case \"$arg\" in\n"
        f"{casos}\n"
        "  esac\ndone\n"
        'echo "NOT_FOUND: Secret not found." >&2; exit 1\n'
    )
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    entorno = dict(os.environ, PATH=f"{binarios}{os.pathsep}{os.environ['PATH']}")
    return entorno


def _correr(entorno, *argumentos):
    return subprocess.run(
        [str(GUION), *argumentos], capture_output=True, text=True,
        env=entorno, cwd=RAIZ,
    )


def test_el_guion_existe_y_es_ejecutable():
    assert GUION.exists(), "los manuales lo mandan a correr"
    assert os.access(GUION, os.X_OK)


@pytest.mark.parametrize(
    "store, puerto, base",
    [("semantica", "5433", "paneles_semantica"), ("boveda", "5432", "paneles_boveda")],
)
def test_apunta_al_proxy_y_deja_el_resto_igual(gcloud_de_mentira, store, puerto, base):
    salida = _correr(gcloud_de_mentira, store)
    assert salida.returncode == 0, salida.stderr
    assert salida.stdout.strip() == (
        f"postgresql://app_paneles:"
        f"{CLAVE_INCOMODA if store == 'semantica' else 'simple'}"
        f"@127.0.0.1:{puerto}/{base}"
    )


def test_la_clave_no_se_decodifica_al_pasar(gcloud_de_mentira):
    """Llega percent-encoded del secreto y tiene que salir igual: si se
    decodifica, un `@` o una `/` adentro parten la URL en otro lado."""
    salida = _correr(gcloud_de_mentira, "semantica")
    assert CLAVE_INCOMODA in salida.stdout
    assert "P?ss#wo/rd@x" not in salida.stdout


def test_acepta_un_puerto_distinto(gcloud_de_mentira):
    salida = _correr(gcloud_de_mentira, "semantica", "6543")
    assert "@127.0.0.1:6543/" in salida.stdout


def test_rechaza_un_puerto_que_no_es_numero(gcloud_de_mentira):
    """El caso real: la versión anterior de esto era una función para pegar en
    la terminal, con el puerto interpolado por la shell. Un `$2` se copió
    literal y el error recién apareció en psql, como
    `invalid integer value "$2" for connection option "port"`."""
    salida = _correr(gcloud_de_mentira, "semantica", "$2")
    assert salida.returncode == 2
    assert "número" in salida.stderr
    assert not salida.stdout.strip()


@pytest.mark.parametrize("argumentos", [(), ("vault",), ("BOVEDA",)])
def test_explica_como_se_usa_cuando_el_store_no_corresponde(
    gcloud_de_mentira, argumentos
):
    salida = _correr(gcloud_de_mentira, *argumentos)
    assert salida.returncode == 2
    assert "boveda|semantica" in salida.stderr


def test_no_inventa_un_dsn_si_el_secreto_no_se_puede_leer(tmp_path):
    binarios = tmp_path / "bin"
    binarios.mkdir()
    falso = binarios / "gcloud"
    falso.write_text('#!/bin/sh\necho "PERMISSION_DENIED: nope." >&2\nexit 1\n')
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    entorno = dict(os.environ, PATH=f"{binarios}{os.pathsep}{os.environ['PATH']}")

    salida = _correr(entorno, "semantica")
    assert salida.returncode == 2
    assert not salida.stdout.strip(), "sin DSN es mejor que con uno inventado"
    assert "PERMISSION_DENIED" in salida.stderr


def test_los_manuales_lo_llaman_como_se_llama():
    """Si el script se renombra, los manuales tienen que seguirlo."""
    for manual in (RAIZ / "docs").glob("DESPLIEGUE*.md"):
        for linea in manual.read_text().splitlines():
            if "dsn_local" in linea:
                assert "scripts/dsn_local.sh" in linea, f"{manual.name}: {linea}"
