"""PEDIDO 06/11/26 · Parte 3 (D89) — selección aleatoria en las consultas.

Antes, una consulta demográfica mostraba `order by p.nombre limit 50`: las
primeras por orden alfabético, y el nombre está asociado al sexo y a la
generación. Ese sesgo pasaba directo a la audiencia de la encuesta creada
desde el resultado. El Definition of Done de la parte 3, contra Postgres:

* 1.000 personas en el segmento y 50 pedidas: 50 sorteadas, no las primeras
  por nombre.
* Misma semilla, misma selección; semilla nueva, selección distinta.
* Dos páginas consecutivas no repiten personas.
* En modo aleatorio semántico, las seleccionadas cumplen y el resultado
  informa pool, verificadas y reemplazadas (con verificación simulada).
* Una encuesta creada desde una consulta tiene como audiencia la selección
  vigente y guarda modo y semilla; desde una demográfica, también con el
  segmento completo.
* La exportación respeta la selección y el orden (R-ALE.4).
"""

import csv
import io
import json

import pytest

from panel_api import consultas, db, encuestas, ruteo

from conftest import VERSION_TEXTO, ContextoDePrueba
from test_r_cs_consultas import VerificadorPorPalabra, _sembrar

LETRAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ADULTOS = [{"tipo": "demografico", "dimension": "edad", "operador": "gte", "valor": 18}]


@pytest.fixture
def mil(conn_boveda):
    """Mil personas adultas, con nombres que recorren el abecedario y los
    dos consentimientos. Por SQL: lo que se prueba es la selección, no el
    alta."""
    db.ejecutar(
        conn_boveda,
        """insert into persona (nombre, fecha_nacimiento)
           select chr(65 + mod(i, 26)) || lpad(i::text, 4, '0'), date '1990-01-01'
             from generate_series(0, 999) as i""")
    db.ejecutar(
        conn_boveda,
        """insert into consentimiento (id_persona, finalidad, version_texto)
           select p.id_persona, f, %s from persona p,
                  unnest(array['contacto_participacion', 'uso_semantico']) as f""",
        (VERSION_TEXTO,))
    conn_boveda.commit()


def _consultar(ctx, **extra):
    return consultas.ejecutar(ctx, {"criterios": ADULTOS, "limite": 50, **extra})


def _nombres(conn, ids):
    filas = db.todas(conn, "select id_persona::text as id, nombre from persona "
                           "where id_persona = any(%s::uuid[])", (ids,))
    por_id = {f["id"]: f["nombre"] for f in filas}
    return [por_id[i] for i in ids]


def _ids(resultado):
    return [i["id_persona"] for i in resultado["items"]]


# ── R-ALE.1 · Demográfica: sorteo con semilla ─────────────────────────

def test_cincuenta_de_mil_son_sorteadas_y_no_las_primeras_por_nombre(ctx_solo_boveda, mil):
    resultado = _consultar(ctx_solo_boveda)
    assert resultado["total"] == 1000 and len(resultado["items"]) == 50
    assert resultado["seleccion"]["modo"] == "aleatorio"
    assert resultado["seleccion"]["semilla"]
    iniciales = {n[0] for n in _nombres(ctx_solo_boveda.boveda, _ids(resultado))}
    # Las primeras 50 por nombre serían todas A o B.
    assert len(iniciales) >= 10, iniciales
    assert not ctx_solo_boveda.abrio_semantica


def test_misma_semilla_misma_seleccion_y_semilla_nueva_otra(ctx_solo_boveda, mil):
    primera = _consultar(ctx_solo_boveda)
    semilla = primera["seleccion"]["semilla"]
    assert _ids(_consultar(ctx_solo_boveda, semilla=semilla)) == _ids(primera)
    otra = _consultar(ctx_solo_boveda)
    assert otra["seleccion"]["semilla"] != semilla
    assert set(_ids(otra)) != set(_ids(primera))


def test_dos_paginas_consecutivas_no_repiten_y_juntas_cubren_el_segmento(ctx_solo_boveda, mil):
    semilla = "auditable"
    vistas = []
    for pagina in range(1, 21):
        resultado = _consultar(ctx_solo_boveda, semilla=semilla, pagina=pagina)
        assert resultado["paginacion"]["paginas"] == 20
        vistas.extend(_ids(resultado))
    assert len(vistas) == len(set(vistas)) == 1000


def test_la_ejecucion_guarda_el_modo_y_la_semilla(ctx_solo_boveda, mil):
    resultado = _consultar(ctx_solo_boveda)
    fila = db.una(ctx_solo_boveda.boveda,
                  "select seleccion, semilla, diagnostico from consulta_ejecucion "
                  "where id = %s", (resultado["diagnostico"]["ejecucion_id"],))
    assert (fila["seleccion"], fila["semilla"]) == (
        "aleatorio", resultado["seleccion"]["semilla"])
    assert fila["diagnostico"]["seleccion"]["semilla"] == resultado["seleccion"]["semilla"]


def test_la_semilla_tiene_que_ser_legible(ctx_solo_boveda, mil):
    from panel_api.errores import DatosInvalidos

    with pytest.raises(DatosInvalidos):
        _consultar(ctx_solo_boveda, semilla="'; drop table persona; --")


def test_el_sorteo_en_python_es_el_mismo_que_en_sql(ctx_solo_boveda, mil):
    resultado = _consultar(ctx_solo_boveda, semilla="misma", limite=200)
    todos = [str(f["id_persona"]) for f in db.todas(ctx_solo_boveda.boveda,
                                                     "select id_persona from persona")]
    assert consultas.sortear(todos, "misma")[:200] == _ids(resultado)


# ── R-ALE.4 · La exportación ──────────────────────────────────────────

def test_el_csv_respeta_la_seleccion_y_el_orden(ctx_solo_boveda, actor, mil):
    resultado = _consultar(ctx_solo_boveda, semilla="exportar", pagina=2)
    _, salida = ruteo.despachar(
        "POST", "/consultas", {"criterios": ADULTOS, "limite": 50, "semilla": "exportar",
                               "pagina": 2, "formato": "csv"}, {},
        actor("analista"), ctx_solo_boveda)
    filas = list(csv.DictReader(io.StringIO(salida["csv"])))
    assert [f["id_persona"] for f in filas] == _ids(resultado)


# ── R-ALE.3 · Crear una encuesta o un panel desde el resultado ────────

def test_la_encuesta_toma_la_seleccion_vigente_y_guarda_modo_y_semilla(
        ctx_solo_boveda, actor, mil):
    resultado = _consultar(ctx_solo_boveda)
    _, encuesta = ruteo.despachar(
        "POST", "/encuestas/desde-consulta",
        {"nombre": "Estudio a medida", "resultado": resultado,
         "definicion": {"criterios": ADULTOS}}, {}, actor("operaciones"), ctx_solo_boveda)
    convocados = {str(f["id_persona"]) for f in db.todas(
        ctx_solo_boveda.boveda, "select id_persona from participacion where encuesta_id = %s",
        (encuesta["id"],))}
    assert convocados == set(_ids(resultado))
    assert encuesta["audiencia"]["modo"] == "aleatorio"
    [paso] = encuestas.pasos_de_audiencia(ctx_solo_boveda.boveda, encuesta["id"])
    assert (paso["modo"], paso["semilla"]) == ("aleatorio", resultado["seleccion"]["semilla"])
    assert paso["via"] == "crear_desde_consulta" and paso["agregadas"] == 50


def test_desde_una_demografica_se_crea_con_el_segmento_completo(ctx_solo_boveda, actor, mil):
    resultado = _consultar(ctx_solo_boveda)
    _, encuesta = ruteo.despachar(
        "POST", "/encuestas/desde-consulta",
        {"nombre": "Todo el segmento", "resultado": resultado, "segmento_completo": True,
         "definicion": {"criterios": ADULTOS}}, {}, actor("operaciones"), ctx_solo_boveda)
    assert encuesta["audiencia"]["convocados"] == 1000
    assert encuesta["audiencia"]["modo"] == "segmento_completo"


def test_el_panel_desde_el_resultado_guarda_como_se_eligio(ctx_solo_boveda, actor, mil):
    resultado = _consultar(ctx_solo_boveda)
    _, panel = ruteo.despachar(
        "POST", "/paneles/desde-consulta",
        {"nombre": "Panel sorteado", "resultado": resultado,
         "definicion": {"criterios": ADULTOS}}, {}, actor("operaciones"), ctx_solo_boveda)
    fila = db.una(ctx_solo_boveda.boveda,
                  "select origen_definicion from panel where id = %s", (panel["id"],))
    assert fila["origen_definicion"]["seleccion_aplicada"]["semilla"] == \
        resultado["seleccion"]["semilla"]


# ── R-ALE.2 · Semántica: aleatorio entre los que cumplen ──────────────

@pytest.fixture
def celulares(conn_boveda, conn_semantica, proveedor):
    """Treinta personas: diez cumplen («Tengo un Xiaomi»), diez dicen lo
    contrario y diez no dicen nada útil."""
    filas = ([("", "Tengo un Xiaomi")] * 10 + [("", "No uso xiaomi")] * 10
             + [("", "Uso un Samsung")] * 10)
    return _sembrar(conn_boveda, conn_semantica, proveedor, filas)


def _aleatoria(conn_boveda, conn_semantica, proveedor, **extra):
    verificador = VerificadorPorPalabra()
    ctx = ContextoDePrueba(conn_boveda, conn_semantica, proveedor, verificador=verificador)
    definicion = {"criterios": ["gente que usa un celular xiaomi"], "seleccion": "aleatorio",
                  "umbral_distancia": 2.0, "limite": 5, **extra}
    return consultas.ejecutar(ctx, definicion), verificador


def test_aleatorio_semantico_elige_solo_a_quien_cumple_e_informa(
        conn_boveda, conn_semantica, proveedor, celulares):
    resultado, verificador = _aleatoria(conn_boveda, conn_semantica, proveedor)
    seleccion = resultado["seleccion"]
    assert seleccion["modo"] == "aleatorio" and seleccion["semilla"]
    assert len(resultado["items"]) == seleccion["quedaron"] == 5
    assert seleccion["pool"] == 30
    assert seleccion["verificadas"] == 5 + seleccion["reemplazadas"]
    for item in resultado["items"]:
        assert item["estado"] == "confirmada"
        assert all(c["veredicto"] == "cumple" for c in item["criterios"])
        assert "xiaomi" in item["evidencias"][0]["valor_texto"].lower()
    # Van en el orden del sorteo.
    assert [i["sorteo"] for i in resultado["items"]] == sorted(
        i["sorteo"] for i in resultado["items"])
    # Una unidad ya juzgada no se vuelve a verificar: hay tres textos.
    assert len(verificador.vistos) <= 3


def test_aleatorio_semantico_dice_cuando_el_pool_se_agota(
        conn_boveda, conn_semantica, proveedor, celulares):
    resultado, _ = _aleatoria(conn_boveda, conn_semantica, proveedor, limite=15)
    seleccion = resultado["seleccion"]
    assert seleccion["quedaron"] == 10
    assert seleccion["verificadas"] == 30
    assert any("Solo hay 10 persona(s) que cumplen" in a for a in seleccion["avisos"])


def test_aleatorio_semantico_con_la_misma_semilla_es_la_misma_seleccion(
        conn_boveda, conn_semantica, proveedor, celulares):
    primera, _ = _aleatoria(conn_boveda, conn_semantica, proveedor, semilla="ola-2")
    segunda, _ = _aleatoria(conn_boveda, conn_semantica, proveedor, semilla="ola-2")
    assert _ids(primera) == _ids(segunda)
    fila = db.una(conn_boveda, "select seleccion, semilla from consulta_ejecucion "
                               "where id = %s", (primera["diagnostico"]["ejecucion_id"],))
    assert (fila["seleccion"], fila["semilla"]) == ("aleatorio", "ola-2")


def test_por_ranking_sigue_siendo_lo_de_siempre(conn_boveda, conn_semantica, proveedor,
                                                celulares):
    ctx = ContextoDePrueba(conn_boveda, conn_semantica, proveedor,
                           verificador=VerificadorPorPalabra())
    resultado = consultas.ejecutar(ctx, {"criterios": ["gente que usa un celular xiaomi"]})
    assert resultado["seleccion"] == {"modo": "ranking", "semilla": None}
    assert json.dumps(resultado, default=str)
