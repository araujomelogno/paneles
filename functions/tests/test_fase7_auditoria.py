"""El catálogo de motivos de reidentificación, y por qué no tiene FK.

`reidentificacion.motivo` nació como texto libre y la lista de valores vivía
solo en `auditoria.MOTIVOS_REIDENTIFICACION`. La `0020` la cataloga en la
base, **sin clave foránea**, y esa ausencia es deliberada: el módulo
documenta que el registro nunca se pierde por una etiqueta desconocida, y
una FK invertiría ese intercambio justo en el registro que existe para
demostrar quién vio los datos de quién.

Lo que una FK habría garantizado gratis —que el código y el catálogo no
diverjan— lo garantiza acá una prueba, igual que `pii.CAMPOS_PII` con
`campo_pii`.
"""

import pytest

from panel_api import auditoria, db

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")


def _persona(conn, nombre, documento):
    from panel_api import personas

    return personas.alta(conn, {
        "persona": {"nombre": nombre, "documento": documento},
        "consentimientos": consentimientos(*AMBAS),
    })["id_persona"]


def _catalogo(conn):
    return {f["codigo"] for f in db.todas(
        conn, "select codigo from motivo_reidentificacion")}


def test_el_catalogo_y_el_codigo_dicen_lo_mismo(conn_boveda):
    assert _catalogo(conn_boveda) == set(auditoria.MOTIVOS_REIDENTIFICACION)


def test_el_motivo_de_la_fase_7_esta_catalogado(conn_boveda):
    """R7.6 — ver las respuestas de alguien se distingue de reidentificarlo."""
    fila = db.una(
        conn_boveda,
        "select etiqueta, expone_pii from motivo_reidentificacion "
        " where codigo = 'respuestas_panelista'")
    assert fila is not None
    # Es el único motivo que une identidad y contenido sin revelar contacto.
    assert fila["expone_pii"] is False


def test_los_demas_motivos_exponen_pii(conn_boveda):
    """Si alguno dejara de hacerlo, es un cambio de criterio y no un typo."""
    filas = db.todas(
        conn_boveda,
        "select codigo from motivo_reidentificacion where not expone_pii")
    assert [f["codigo"] for f in filas] == ["respuestas_panelista"]


def test_un_motivo_sin_catalogar_se_registra_igual(conn_boveda, actor):
    """La decisión que la FK habría roto.

    Perder el rastro es peor que guardarlo con una etiqueta rara, así que un
    motivo nuevo que nadie catalogó **escribe la fila igual**.
    """
    persona = _persona(conn_boveda, "Ana Prueba", "7000001-1")

    resultado = auditoria.registrar_reidentificacion(
        conn_boveda, [persona], actor=actor("operaciones"),
        motivo="motivo_que_nadie_catalogo")
    assert resultado["registradas"] == 1


def test_la_vista_no_esconde_un_motivo_sin_catalogar(conn_boveda, actor):
    """Y la vista tampoco: `left join`, no `join`.

    Si la vista filtrara las filas con un motivo desconocido, catalogar mal
    volvería **invisible** una reidentificación. Es el mismo razonamiento
    que el de arriba, un nivel más abajo.
    """
    persona = _persona(conn_boveda, "Beto Prueba", "7000002-2")
    auditoria.registrar_reidentificacion(
        conn_boveda, [persona], actor=actor("operaciones"),
        motivo="motivo_que_nadie_catalogo")

    fila = db.una(
        conn_boveda,
        "select motivo, motivo_etiqueta, motivo_desconocido, expone_pii "
        "  from v_reidentificacion where id_persona = %s", (persona,))
    assert fila["motivo_desconocido"] is True
    assert "sin catalogar" in fila["motivo_etiqueta"]
    # Ante la duda, se asume lo más conservador.
    assert fila["expone_pii"] is True


def test_la_vista_trae_la_etiqueta_de_un_motivo_conocido(conn_boveda, actor):
    persona = _persona(conn_boveda, "Caro Prueba", "7000003-3")
    auditoria.registrar_reidentificacion(
        conn_boveda, [persona], actor=actor("operaciones"),
        motivo="respuestas_panelista")

    fila = db.una(
        conn_boveda,
        "select motivo_etiqueta, motivo_desconocido, expone_pii "
        "  from v_reidentificacion where id_persona = %s", (persona,))
    assert fila["motivo_desconocido"] is False
    assert fila["expone_pii"] is False
    assert "respuestas" in fila["motivo_etiqueta"].lower()
