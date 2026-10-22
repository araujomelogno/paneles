"""R-CLI — Los clientes de cada carga (SPEC_clientes_lista_negra_y_mejoras).

Un cliente es para quién se hizo un estudio. Vive en la carga, como entidad
compartida con el nombre normalizado, y los clientes de cada panelista **se
derivan** de sus cargas: no se copian. Es confidencial: no llega al portal,
ni a COLOQUIO, ni a las exportaciones.

Corre contra Postgres de verdad: la normalización la hace valer un `check` y
la derivación es una vista.
"""

import pytest

from panel_api import cargas, clientes, consultas, db, demografia, portal, ruteo
from panel_api.errores import DatosInvalidos

from test_r_org_origen_y_ambitos import _cargar, _fila


def _ids(conn, criterios):
    criterios = [demografia.normalizar_criterio(c) for c in criterios]
    return set(demografia.ids_del_segmento(conn, criterios))


@pytest.fixture
def dos_cargas(ctx):
    """Antel tiene dos cargas; la segunda también es para Movistar. Persona 1
    está en las dos, persona 2 solo en la primera, persona 3 en ninguna de
    Antel."""
    a = cargas.crear(ctx.boveda, "Tracking Antel", clientes=["Antel"])
    b = cargas.crear(ctx.boveda, "Telefonía", clientes=["ANTEL", "Movistar"])
    c = cargas.crear(ctx.boveda, "Bebidas", clientes=["Coca-Cola"])
    _cargar(ctx, a, [_fila(1), _fila(2)])
    _cargar(ctx, b, [_fila(1)])
    _cargar(ctx, c, [_fila(3)])
    personas = {f["documento"]: f["id_persona"] for f in db.todas(
        ctx.boveda, "select id_persona::text as id_persona, documento from persona")}
    return {"a": a, "b": b, "c": c,
            "p1": personas["20-1"], "p2": personas["20-2"], "p3": personas["20-3"]}


# ════════════════════════════════════════════════════════════════════
#  R-CLI.1 · El vocabulario
# ════════════════════════════════════════════════════════════════════

def test_el_nombre_se_normaliza_y_las_variantes_son_el_mismo_cliente(conn_boveda):
    a = cargas.crear(conn_boveda, "Uno", clientes=["  Antel  "])
    b = cargas.crear(conn_boveda, "Dos", clientes=["ANTEL", "antel"])
    c = cargas.crear(conn_boveda, "Tres", clientes=["Teléfonica"])
    d = cargas.crear(conn_boveda, "Cuatro", clientes=["telefonica"])

    assert a["clientes"] == b["clientes"] == [{"id": a["clientes"][0]["id"],
                                               "nombre": "antel"}]
    # La variante sin tilde reutiliza la existente, no crea otra.
    assert c["clientes"] == d["clientes"]
    assert db.una(conn_boveda, "select count(*) as n from cliente")["n"] == 2


def test_la_puntuacion_de_una_razon_social_se_conserva(conn_boveda):
    carga = cargas.crear(conn_boveda, "Uno", clientes=["Antel S.A."])
    assert carga["clientes"][0]["nombre"] == "antel s.a."


def test_la_base_rechaza_un_nombre_sin_normalizar(conn_boveda):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        db.ejecutar(conn_boveda, "insert into cliente (nombre) values ('Antel')")


def test_un_nombre_demasiado_largo_se_rechaza_con_motivo(conn_boveda):
    with pytest.raises(DatosInvalidos, match="demasiado largo"):
        cargas.crear(conn_boveda, "Uno", clientes=["x" * 121])


def test_el_autocompletado_busca_sin_tildes_y_cuenta_cargas(ctx, actor, dos_cargas):
    _, salida = ruteo.despachar("GET", "/clientes", {}, {"q": "ANT"},
                                actor("analista"), ctx)
    assert salida["items"] == [{"id": dos_cargas["a"]["clientes"][0]["id"],
                       "nombre": "antel", "cargas": 2}]
    _, todos = ruteo.despachar("GET", "/clientes", {}, {}, actor("analista"), ctx)
    assert [c["nombre"] for c in todos["items"]] == ["antel", "coca-cola", "movistar"]


# ════════════════════════════════════════════════════════════════════
#  R-CLI.2 · En la carga, al crearla y después
# ════════════════════════════════════════════════════════════════════

def test_la_ruta_crea_la_carga_con_sus_clientes(ctx, actor):
    _, carga = ruteo.despachar("POST", "/cargas",
                               {"nombre": "Ómnibus", "clientes": ["Antel", "Movistar"],
                                "tipo_datos_origen": "encuesta",
                                "modo_relevamiento": "web"},
                               {}, actor("operaciones"), ctx)
    assert [c["nombre"] for c in carga["clientes"]] == ["antel", "movistar"]
    _, lista = ruteo.despachar("GET", "/cargas", {}, {}, actor("analista"), ctx)
    assert [c["nombre"] for c in lista["items"][0]["clientes"]] == ["antel", "movistar"]


def test_editar_los_clientes_deja_exactamente_los_pedidos(ctx, actor, dos_cargas):
    b = dos_cargas["b"]
    _, editada = ruteo.despachar("PATCH", f"/cargas/{b['id']}",
                                 {"clientes": ["Movistar", "Claro"]},
                                 {}, actor("operaciones"), ctx)
    assert [c["nombre"] for c in editada["clientes"]] == ["claro", "movistar"]
    # El cliente que ya no usa ninguna carga sigue en el vocabulario.
    assert {c["nombre"] for c in clientes.listar(ctx.boveda)} >= {"antel", "claro"}


# ════════════════════════════════════════════════════════════════════
#  R-CLI.2 · Los de cada panelista se derivan
# ════════════════════════════════════════════════════════════════════

def test_los_clientes_de_una_persona_salen_de_sus_cargas(ctx, dos_cargas):
    p1 = clientes.de_persona(ctx.boveda, dos_cargas["p1"])
    assert [c["nombre"] for c in p1] == ["antel", "movistar"]
    antel = next(c for c in p1 if c["nombre"] == "antel")
    assert sorted(antel["cargas"]) == sorted([dos_cargas["a"]["id"],
                                              dos_cargas["b"]["id"]])
    assert [c["nombre"] for c in clientes.de_persona(ctx.boveda, dos_cargas["p2"])] \
        == ["antel"]


def test_corregir_la_carga_se_ve_en_sus_panelistas_sin_tocarlos(ctx, dos_cargas):
    """No hay copia: la corrección llega sola a todas las fichas."""
    clientes.guardar_de_carga(ctx.boveda, dos_cargas["b"]["id"], ["Claro"])
    assert [c["nombre"] for c in clientes.de_persona(ctx.boveda, dos_cargas["p1"])] \
        == ["antel", "claro"]
    columnas = {r["column_name"] for r in db.todas(
        ctx.boveda, "select column_name from information_schema.columns "
                    "where table_name = 'persona'")}
    assert not columnas & {"cliente", "clientes"}


# ════════════════════════════════════════════════════════════════════
#  R-CLI.3 · Filtro y columna
# ════════════════════════════════════════════════════════════════════

def test_el_filtro_incluye_por_cliente(ctx, dos_cargas):
    ids = _ids(ctx.boveda, [{"dimension": "cliente", "operador": "in",
                             "valor": ["Antel"]}])
    assert ids == {dos_cargas["p1"], dos_cargas["p2"]}


def test_el_filtro_excluye_a_quien_participo_en_alguna_carga_del_cliente(
        ctx, dos_cargas):
    """«No participó en estudios para Antel»: p1 está en una carga de Antel
    que también es de Movistar y en otra; no alcanza con que tenga otro
    cliente para colarse."""
    ids = _ids(ctx.boveda, [{"dimension": "cliente", "operador": "not_in",
                             "valor": ["antel"]}])
    assert ids == {dos_cargas["p3"]}


def test_el_filtro_acepta_el_id_y_dice_lo_que_filtra(ctx, dos_cargas):
    antel = dos_cargas["a"]["clientes"][0]["id"]
    criterio = demografia.normalizar_criterio(
        {"dimension": "cliente", "operador": "ne", "valor": [antel]})
    assert criterio["operador"] == "not_in"
    assert "No participó en estudios para" in criterio["etiqueta"]
    assert set(demografia.ids_del_segmento(ctx.boveda, [criterio])) == {dos_cargas["p3"]}


def test_un_operador_que_no_es_de_conjunto_se_rechaza(conn_boveda):
    with pytest.raises(DatosInvalidos):
        demografia.normalizar_criterio(
            {"dimension": "cliente", "operador": "gt", "valor": ["a"]})


def test_los_clientes_son_una_columna_de_los_resultados(ctx, actor, dos_cargas):
    _, columnas = ruteo.despachar("GET", "/atributos/columnas", {}, {},
                                  actor("analista"), ctx)
    assert clientes.COLUMNA in {c["clave"] for c in columnas["items"]}

    _, valores = ruteo.despachar(
        "POST", "/resultados/atributos",
        {"ids_persona": [dos_cargas["p1"], dos_cargas["p3"]]},
        {}, actor("analista"), ctx)
    assert valores["items"][dos_cargas["p1"]][clientes.COLUMNA]["valor"] \
        == "antel | movistar"
    assert valores["items"][dos_cargas["p3"]][clientes.COLUMNA]["valor"] == "coca-cola"


def test_cliente_es_una_clave_reservada():
    from panel_api import atributos
    assert {"cliente", "clientes"} <= atributos.CLAVES_RESERVADAS


# ════════════════════════════════════════════════════════════════════
#  R-CLI.4 · Confidencial
# ════════════════════════════════════════════════════════════════════

def test_coloquio_no_ve_los_clientes(conn_boveda):
    for objeto in ("cliente", "carga_cliente", "v_persona_cliente"):
        assert not db.una(
            conn_boveda,
            "select has_table_privilege('coloquio_app', %s, 'SELECT') as puede",
            (objeto,))["puede"], objeto


def test_el_portal_no_muestra_los_clientes(ctx, dos_cargas):
    perfil = portal.perfil(ctx.boveda, dos_cargas["p1"])
    assert "antel" not in repr(perfil).lower()
    assert "cliente" not in repr(perfil).lower()


def test_las_exportaciones_no_llevan_clientes():
    assert not {"cliente", "clientes"} & set(consultas.CAMPOS_IDENTIFICADOS)
    csv = consultas.a_csv({"items": [{"id_persona": "x", "puntaje": 1,
                                      "clientes": "antel"}]})
    assert "antel" not in csv


def test_una_consulta_demografica_filtra_por_cliente(ctx, actor, dos_cargas):
    """De punta a punta por la ruta de Consultas, con lo que manda la
    pantalla: el id del cliente y la etiqueta."""
    antel = dos_cargas["a"]["clientes"][0]["id"]
    _, salida = ruteo.despachar(
        "POST", "/consultas",
        {"criterios": [{"tipo": "demografico", "dimension": "cliente",
                        "operador": "not_in", "valor": [antel],
                        "etiqueta": "No participó en estudios para antel"}]},
        {}, actor("analista"), ctx)
    assert {i["id_persona"] for i in salida["items"]} == {dos_cargas["p3"]}
