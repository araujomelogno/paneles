"""R7.1 — la versión del consentimiento se elige de lo publicado.

El desplegable vive en la pantalla, pero lo que lo hace posible es que el
servidor sepa contestar «¿qué versiones activas hay de esta finalidad?».
Eso es lo que se prueba acá, más lo que no tiene que cambiar: la cadena que
viaja al backend es la misma de siempre.

El trigger `consentimiento_texto` ya rechaza una versión no publicada, así
que tipear mal **hoy** produce un error. El desplegable no agrega una
validación: elimina la posibilidad del error.
"""

import pytest

from panel_api import db, inscripciones, personas, ruteo
from panel_api.errores import DatosInvalidos

from conftest import consentimientos

CONTACTO = "contacto_participacion"
SEMANTICO = "uso_semantico"


def _publicar(conn, finalidad, version, cuerpo="Texto de prueba."):
    return inscripciones.publicar_texto(conn, finalidad, version, cuerpo)


def test_solo_se_listan_las_versiones_de_esa_finalidad(conn_boveda):
    """Una versión de contacto no es elegible para uso semántico: son
    finalidades distintas y mezclarlas sería declarar que alguien aceptó
    algo que no leyó."""
    _publicar(conn_boveda, CONTACTO, "contacto-2027-01")
    _publicar(conn_boveda, SEMANTICO, "semantico-2027-01")
    conn_boveda.commit()

    deContacto = inscripciones.listar_textos(conn_boveda, CONTACTO)
    assert {t["finalidad"] for t in deContacto} == {CONTACTO}
    assert "semantico-2027-01" not in {t["version"] for t in deContacto}


def test_una_version_desactivada_no_se_ofrece(conn_boveda):
    """El desplegable muestra lo **publicado y activo**: ofrecer una
    desactivada sería ofrecer algo que la base después rechaza."""
    _publicar(conn_boveda, CONTACTO, "vieja-2020-01")
    conn_boveda.commit()
    db.ejecutar(conn_boveda,
                "update texto_consentimiento set activo = false "
                " where version = %s", ("vieja-2020-01",))
    conn_boveda.commit()

    activas = [t["version"] for t in
               inscripciones.listar_textos(conn_boveda, CONTACTO) if t["activo"]]
    assert "vieja-2020-01" not in activas


def test_una_finalidad_sin_version_activa_devuelve_lista_vacia(
        ctx, actor, conn_boveda):
    """Y la lista vacía es lo que la pantalla convierte en «no hay ninguna,
    se publica en Inscripciones → Textos de consentimiento». El servidor no
    inventa una versión por defecto: no existe tal cosa."""
    db.ejecutar(conn_boveda, "delete from texto_consentimiento "
                             " where finalidad = %s", (SEMANTICO,))
    conn_boveda.commit()

    status, salida = ruteo.despachar(
        "GET", "/textos-consentimiento", {}, {"finalidad": SEMANTICO},
        actor("operaciones"), ctx)
    assert status == 200
    assert [t for t in salida["items"] if t["activo"]] == []


def test_el_texto_completo_viaja_con_la_version(ctx, actor, conn_boveda):
    """«Ver el texto antes de confirmar» necesita el cuerpo, no solo el
    código: lo que la persona aceptó es el texto."""
    _publicar(conn_boveda, CONTACTO, "con-cuerpo-2027",
              "Autorizo a Equipos a contactarme para estudios.")
    conn_boveda.commit()

    _, salida = ruteo.despachar(
        "GET", "/textos-consentimiento", {}, {"finalidad": CONTACTO},
        actor("operaciones"), ctx)
    fila = next(t for t in salida["items"] if t["version"] == "con-cuerpo-2027")
    assert "Autorizo a Equipos" in fila["cuerpo"]


def test_el_contrato_con_el_backend_no_cambia(conn_boveda):
    """No regresión: el alta sigue recibiendo la **cadena** de versión.

    Lo que cambió es de dónde sale —un desplegable en vez de un campo de
    texto—, no qué se manda. Si esto fallara, el cambio de pantalla habría
    arrastrado un cambio de contrato, que es justo lo que R7.1 no quiere.
    """
    _publicar(conn_boveda, CONTACTO, "contrato-2027-01")
    conn_boveda.commit()

    id_persona = personas.alta(conn_boveda, {
        "persona": {"nombre": "Contrato Intacto", "documento": "6000001-1"},
        "consentimientos": [{"finalidad": CONTACTO,
                             "version_texto": "contrato-2027-01"}],
    })["id_persona"]

    fila = db.una(conn_boveda,
                  "select version_texto from consentimiento "
                  " where id_persona = %s", (id_persona,))
    assert fila["version_texto"] == "contrato-2027-01"


def test_una_version_inventada_la_sigue_rechazando_la_base(conn_boveda):
    """Y por eso el desplegable importa: el error existe, solo que ahora no
    hay cómo cometerlo desde la pantalla."""
    with pytest.raises(Exception) as fallo:
        personas.alta(conn_boveda, {
            "persona": {"nombre": "Version Falsa", "documento": "6000002-2"},
            "consentimientos": [{"finalidad": CONTACTO,
                                 "version_texto": "no-publicada-jamas"}],
        })
    assert "no-publicada-jamas" in str(fallo.value)
