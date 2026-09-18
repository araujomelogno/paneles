"""El contrato entre el código y el deploy.

Hay una clase de falla que no se ve en ninguna prueba de lógica y que en
producción se disfraza de otra cosa: un secreto que está en Secret Manager
pero que la función **no declara**. `firebase deploy` solo monta los que
figuran en `SECRETOS`, así que el runtime no lo ve, y como todos los módulos
de este sistema se degradan de forma visible cuando falta una credencial, el
síntoma es «no hay token configurado» **justo después de haberlo cargado**.

Eso pasó de verdad con los secretos de la Fase 4: estaban guardados, el deploy
salió bien, y la pantalla de WhatsApp decía que faltaban. Estas pruebas son
para que no vuelva a pasar en silencio.
"""

import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
FUENTES = sorted((RAIZ / "functions" / "panel_api").glob("*.py"))

# Lo que el código lee del entorno y **no** es un secreto: nombres de
# proveedor, modelos y dimensiones. Van en `functions/.env`, no en Secret
# Manager, y por eso no tienen que estar declarados en `SECRETOS`.
#
# La lista es explícita a propósito. Si alguien agrega una variable nueva, la
# prueba falla y hay que decidir de qué lado va, que es exactamente la
# decisión que se saltea cuando esto no se chequea.
NO_SON_SECRETOS = frozenset({
    "EMBEDDINGS_PROVEEDOR", "EMBEDDINGS_MODELO", "EMBEDDINGS_DIMS",
    "RERANKER_PROVEEDOR", "RERANKER_MODELO",
    "VERIFICACION_PROVEEDOR", "CLAUDE_MODELO",
    "PADRON_USUARIOS",
    # R4.3 — el nombre del proveedor no es secreto; su clave sí, y esa es
    # `DESAFIO_SECRETO`, que va declarada.
    "DESAFIO_PROVEEDOR",
    # R4.3 — idem: `log` o `ninguno`, no una credencial.
    "VERIFICACION_ENVIO_PROVEEDOR",
})


def _secretos_declarados():
    """`SECRETOS` de `main.py`, leído del archivo.

    Se lee el texto en vez de importar `main`: importarlo arrastra
    `firebase_functions` y `firebase_admin`, que no hacen falta para
    comprobar una lista.
    """
    texto = (RAIZ / "functions" / "main.py").read_text(encoding="utf-8")
    bloque = re.search(r"SECRETOS = \[(.*?)\n\]", texto, re.S)
    assert bloque, "no se encontró la lista SECRETOS en main.py"
    return set(re.findall(r'"([A-Z_]+)"', bloque.group(1)))


def _variables_leidas():
    """Toda variable de entorno que lee `panel_api`."""
    leidas = set()
    for fuente in FUENTES:
        texto = fuente.read_text(encoding="utf-8")
        leidas |= set(re.findall(
            r'(?:os\.environ|entorno|environ)\.get\(\s*"([A-Z_]+)"', texto))
    return leidas


def test_todo_secreto_que_el_codigo_lee_esta_declarado_en_main():
    """El que falta no rompe el deploy: hace que el sistema se comporte como
    si la credencial no existiera. Es la falla más difícil de diagnosticar de
    todas las que este repo puede tener."""
    sin_declarar = _variables_leidas() - NO_SON_SECRETOS - _secretos_declarados()
    assert not sin_declarar, (
        f"{sorted(sin_declarar)} se lee(n) del entorno y no está(n) en "
        f"SECRETOS de functions/main.py. Sin declararlo, `firebase deploy` no "
        f"lo monta y el runtime lo ve vacío aunque esté en Secret Manager. "
        f"Si no es un secreto, agregalo a NO_SON_SECRETOS."
    )


def test_no_se_declara_un_secreto_que_nadie_lee():
    """Al revés: `firebase deploy` **falla** si declara un secreto que no
    existe en Secret Manager, así que cada nombre de más obliga a crear un
    secreto que no sirve para nada."""
    de_mas = _secretos_declarados() - _variables_leidas()
    assert not de_mas, (
        f"{sorted(de_mas)} está(n) declarado(s) en SECRETOS y nadie lo(s) lee. "
        f"Cada uno tiene que existir en Secret Manager o el deploy falla."
    )


@pytest.mark.parametrize("variable", sorted(NO_SON_SECRETOS))
def test_lo_que_no_es_secreto_tiene_un_default_usable(variable):
    """Una variable de configuración sin default convierte un `.env`
    incompleto en un 500 en la primera request que la necesite.

    Valen las dos formas que el código usa: el segundo argumento de `.get()`
    y el `or` que sigue. La segunda es la habitual acá porque además
    normaliza la cadena vacía, que es lo que deja un secreto cargado sin
    valor.
    """
    # `.get("X", …)` → hay default. `.get("X") or …` → también.
    patron = re.compile(rf'\.get\(\s*"{variable}"\s*(?:,[^)]*)?\)(\s*or\b)?')
    encontrados = 0
    for fuente in FUENTES:
        texto = fuente.read_text(encoding="utf-8")
        for coincidencia in patron.finditer(texto):
            encontrados += 1
            tiene_segundo_argumento = "," in coincidencia.group(0)
            tiene_or = bool(coincidencia.group(1))
            assert tiene_segundo_argumento or tiene_or, (
                f"{fuente.name}: `{variable}` se lee sin valor por defecto "
                f"—ni segundo argumento de `.get()` ni `or`—, así que un "
                f"`.env` sin esa línea la deja en `None`.")
    assert encontrados, f"`{variable}` no se lee en ningún lado: sobra de la lista."
