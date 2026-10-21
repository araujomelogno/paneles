"""PEDIDO — Celular como identificador (parte 1 de
`docs/PEDIDO_celular_identificador_y_falso_positivo_pii.md`).

El celular ya era la tercera clave del dedup para *crear* personas sin
duplicarlas, pero no servía para *encontrarlas*: una base de un panel que
trabaja por teléfono, con el celular como único identificador, no tenía cómo
cargarse en modo «los panelistas ya existen». Ahora es un tipo de
identificador más, con las reglas del dedup: E.164 y una sola titular.

Corre contra la bóveda real (y el semántico, para la ingesta de punta a
punta).
"""

import pathlib

import pytest

from panel_api import cargas, ingesta, personas

from conftest import consentimientos

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ENCUESTAS_JS = (RAIZ / "web" / "public" / "js" / "paginas" / "encuestas.js").read_text(
    encoding="utf-8")

PREGUNTAS = [{"codigo": "P1", "texto": "¿Qué compañía de celular usa?",
              "tipo": "abierta", "orden": 1}]


def _panelista(conn, documento, celular, nombre="Panelista"):
    return personas.alta(conn, {
        "persona": {"documento": documento, "nombre": nombre, "celular": celular},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico"),
    })["id_persona"]


@pytest.fixture
def ana(conn_boveda):
    return _panelista(conn_boveda, "30-1", "099 123 456", "Ana")


def _resolver(conn, *valores):
    return ingesta.resolver_identificadores(conn, ingesta.POR_CELULAR, list(valores))


def test_el_selector_ofrece_el_celular():
    assert '<option value="celular">Trae el celular</option>' in ENCUESTAS_JS
    assert "celular" in ingesta.TIPOS_DE_IDENTIFICADOR


def test_una_fila_con_el_celular_de_un_panelista_se_le_asigna(conn_boveda, ana):
    mapa, motivos = _resolver(conn_boveda, "099123456")
    assert mapa == {"099123456": str(ana)}
    assert motivos == {}


@pytest.mark.parametrize("escrito", [
    "099 123 456", "99123456", "+598 99 123 456", "59899123456",
    "00598 99123456", "(099) 123-456",
])
def test_se_compara_normalizado(conn_boveda, ana, escrito):
    """Con espacios, sin el +598, con el 0 de larga distancia o con guiones:
    es el mismo número."""
    mapa, _ = _resolver(conn_boveda, escrito)
    assert mapa == {escrito: str(ana)}


def test_un_celular_compartido_no_se_asigna_a_nadie(conn_boveda):
    """Un celular puede ser de un hogar: con dos titulares no se elige."""
    _panelista(conn_boveda, "30-2", "098 111 222", "Madre")
    _panelista(conn_boveda, "30-3", "098 111 222", "Hijo")

    mapa, motivos = _resolver(conn_boveda, "098111222")
    assert mapa == {}
    assert motivos == {"098111222": ingesta.CELULAR_COMPARTIDO}


@pytest.mark.parametrize("crudo", ["solo WhatsApp 095093112", "....", "1", "abc"])
def test_un_celular_mal_escrito_es_formato_invalido(conn_boveda, ana, crudo):
    """«solo WhatsApp 095093112» tiene un número adentro, y el alta lo
    rescataría; para decidir de quién es una fila, no es un identificador."""
    mapa, motivos = _resolver(conn_boveda, crudo)
    assert mapa == {}
    assert motivos == {crudo: ingesta.FORMATO_INVALIDO}


def test_un_celular_que_nadie_tiene_es_no_encontrado(conn_boveda, ana):
    _, motivos = _resolver(conn_boveda, "091 000 000")
    assert motivos == {"091 000 000": ingesta.NO_ENCONTRADO}


def test_la_carga_en_modo_ya_existen_ingesta_por_celular(ctx, ana):
    """De punta a punta: la carga asigna cada fila a su panelista, informa las
    que no pudo con su motivo, y no registra alias por el celular."""
    _panelista(ctx.boveda, "30-2", "098 111 222", "Madre")
    _panelista(ctx.boveda, "30-3", "098 111 222", "Hijo")
    carga = cargas.crear(ctx.boveda, "Panel telefónico", tipo_datos_origen="encuesta",
                         modo_relevamiento="telefonico")
    filas = [
        {"CEL": "099 123 456", "P1": "Antel"},
        {"CEL": "098111222", "P1": "Claro"},
        {"CEL": "solo WhatsApp 095093112", "P1": "Movistar"},
    ]

    resultado = cargas.ingestar(
        ctx.boveda, ctx.semantica, carga["id"], PREGUNTAS, filas,
        columna_id="CEL", origen="whatsapp", proveedor=ctx.embeddings,
        tipo_identificador="celular")

    assert resultado["ids_persona_ingestados"] == [str(ana)]
    motivos = {d["id_en_origen"]: d["motivo"] for d in resultado["sin_mapear_detalle"]}
    assert motivos == {"098111222": "celular_compartido",
                       "solo WhatsApp 095093112": "formato_invalido"}
    assert ctx.boveda.execute(
        "select count(*) as n from alias_origen where origen = 'whatsapp'"
    ).fetchone()["n"] == 0


def test_la_pantalla_explica_cada_motivo():
    for motivo in (ingesta.FORMATO_INVALIDO, ingesta.NO_ENCONTRADO,
                   ingesta.SIN_ALIAS, ingesta.CELULAR_COMPARTIDO):
        assert f"  {motivo}: '" in ENCUESTAS_JS, motivo
