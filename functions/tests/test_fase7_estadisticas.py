"""R7.4 y R7.5 — las columnas de la lista y los números de la base.

De R7.5, la prueba que importa es la de la brecha: los conteos sueltos se
miran una vez, y el número que se usa todas las semanas es «de mis N
panelistas, ¿sobre cuántos puedo realmente consultar?». Y dentro de la
brecha, `individuos_sin_panelista` no es una estadística sino un chequeo de
integridad que tiene que dar cero.
"""

import pytest

from panel_api import (atributos, encuestas, estadisticas, ficha, ingesta,
                       paneles, personas, ruteo)

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")


def _persona(conn, nombre, documento, finalidades=AMBAS):
    return personas.alta(conn, {
        "persona": {"nombre": nombre, "documento": documento},
        "consentimientos": consentimientos(*finalidades),
        "origen": "dooblo", "id_en_origen": documento,
    })["id_persona"]


@pytest.fixture
def base(ctx, conn_boveda, conn_semantica, proveedor):
    """Tres panelistas: una con respuestas, una sin, y una sin uso semántico."""
    panel = paneles.crear(conn_boveda, "Panel A")
    con = _persona(conn_boveda, "Con Respuestas", "5000001-1")
    sin = _persona(conn_boveda, "Sin Respuestas", "5000002-2")
    sin_semantico = _persona(conn_boveda, "Sin Semantico", "5000003-3",
                             ("contacto_participacion",))
    for p in (con, sin, sin_semantico):
        paneles.agregar_miembro(conn_boveda, panel["id"], p)

    enc = encuestas.crear(conn_boveda, panel["id"], "Ola 1")
    ingesta.ingestar(
        conn_boveda, conn_semantica, enc,
        [{"codigo": "P1", "texto": "¿Qué bebida?", "tipo": "abierta", "orden": 1}],
        [{"id_en_origen": "5000001-1", "P1": "Fernet"}],
        columna_id="id_en_origen", origen="dooblo", proveedor=proveedor)
    conn_semantica.commit()
    conn_boveda.commit()
    return {"panel": panel, "con": con, "sin": sin,
            "sin_semantico": sin_semantico}


# ── R7.5 ─────────────────────────────────────────────────────────────

def test_los_panelistas_se_cuentan_con_sus_cortes(conn_boveda, base):
    p = estadisticas.panelistas(conn_boveda)
    assert p["total"] == 3
    assert p["sin_panel"] == 0
    assert [x["nombre"] for x in p["por_panel"]] == ["Panel A"]
    assert p["por_panel"][0]["miembros"] == 3


def test_el_consentimiento_se_cuenta_por_finalidad(conn_boveda, base):
    c = estadisticas.consentimiento(conn_boveda)
    por = {f["finalidad"]: f for f in c["por_finalidad"]}
    assert por["contacto_participacion"]["vigentes"] == 3
    assert por["uso_semantico"]["vigentes"] == 2
    assert por["uso_semantico"]["sin_el"] == 1


def test_el_corpus_informa_respuestas_e_individuos(conn_semantica, base):
    c = estadisticas.corpus(conn_semantica)
    assert c["respuestas"] == 1
    assert c["individuos_con_respuestas"] == 1
    assert c["cuestionarios"] == 1


def test_la_brecha_dice_sobre_cuantos_se_puede_consultar(
        conn_boveda, conn_semantica, base):
    """El número que define si una búsqueda sirve."""
    b = estadisticas.brecha(conn_boveda, conn_semantica)
    assert b["panelistas_sin_respuestas"]["cuantos"] == 2
    assert str(base["sin"]) in b["panelistas_sin_respuestas"]["ejemplos"]
    assert b["panelistas_sin_uso_semantico"]["cuantos"] == 1
    assert b["consultables"] == 1


def test_los_individuos_huerfanos_son_cero_y_se_marcan_como_problema(
        conn_boveda, conn_semantica, base):
    b = estadisticas.brecha(conn_boveda, conn_semantica)
    assert b["individuos_sin_panelista"]["cuantos"] == 0
    assert b["individuos_sin_panelista"]["es_problema"] is False


def test_un_individuo_sin_panelista_se_informa_como_problema(
        conn_boveda, conn_semantica, base):
    """Si la cascada de baja no alcanzó al store semántico, se ve acá."""
    from panel_api import db, semantica

    semantica.asegurar_individuos(
        conn_semantica, ["00000000-0000-0000-0000-0000000000ff"])
    conn_semantica.commit()

    b = estadisticas.brecha(conn_boveda, conn_semantica)
    assert b["individuos_sin_panelista"]["cuantos"] == 1
    assert b["individuos_sin_panelista"]["es_problema"] is True


def test_la_salud_del_corpus_trae_la_dimension_en_uso(conn_semantica, base):
    s = estadisticas.salud_del_corpus(conn_semantica)
    assert s["dimension_embeddings"] == 512
    assert s["tabla_bytes"] > 0


def test_la_pantalla_entera_sale_en_una_llamada(ctx, actor, base):
    status, salida = ruteo.despachar("GET", "/estadisticas", {}, {},
                                     actor("analista"), ctx)
    assert status == 200
    for bloque in ("panelistas", "consentimiento", "corpus", "salud",
                   "brecha", "cargas"):
        assert bloque in salida


def test_los_panelistas_sin_respuestas_se_pueden_listar(ctx, actor, base):
    """No solo contar: el DoD pide poder ver quiénes son."""
    status, salida = ruteo.despachar(
        "GET", "/estadisticas/sin-respuestas", {}, {}, actor("analista"), ctx)
    assert status == 200
    assert salida["cuantos"] == 2
    assert len(salida["ejemplos"]) == 2


# ── R7.4 ─────────────────────────────────────────────────────────────

def test_los_atributos_de_varias_personas_salen_en_una_consulta(
        conn_boveda, base):
    salida = atributos.valores_de_varias(
        conn_boveda, [base["con"], base["sin"]])
    assert set(salida) == {str(base["con"]), str(base["sin"])}


def test_un_atributo_especial_no_se_ofrece_como_columna(conn_boveda, base):
    atributos.crear(conn_boveda, {
        "clave": "religion", "etiqueta": "Religión", "tipo": "categorico",
        "es_especial": True,
        "categorias": [{"clave": "ninguna", "etiqueta": "Ninguna"}]})
    conn_boveda.commit()
    claves = {c["clave"] for c in atributos.columnas_ofrecibles(conn_boveda)}
    assert "religion" not in claves


def test_sin_valor_el_atributo_no_aparece_y_la_celda_se_puede_decir_vacia(
        conn_boveda, base):
    """La ausencia tiene que ser distinguible de una categoría.

    El backend devuelve el atributo **ausente** del dict en vez de una
    cadena vacía: así la pantalla no puede confundirlo con un valor, que es
    lo que el requisito pide.
    """
    salida = atributos.valores_de_varias(conn_boveda, [base["sin"]])
    assert salida[str(base["sin"])] == {}


def test_la_preferencia_de_columnas_se_recuerda(ctx, actor, base):
    quien = actor("analista")
    status, _ = ruteo.despachar(
        "PUT", "/mi/preferencias",
        {"columnas_resultado": ["sexo", "localidad"]}, {}, quien, ctx)
    assert status == 200

    status, salida = ruteo.despachar("GET", "/mi/preferencias", {}, {},
                                     quien, ctx)
    assert salida["columnas_resultado"] == ["sexo", "localidad"]


def test_pedir_atributos_no_re_ejecuta_la_consulta(
        ctx_solo_boveda, actor, base):
    """Agregar una columna no vuelve a consultar.

    Se usa el contexto cuyo store semántico **explota si alguien lo toca**:
    si la ruta lo abriera, esto falla con una excepción y no con una
    aserción sutil. Los atributos salen de la bóveda sobre los `id_persona`
    que ya están en pantalla.
    """
    status, salida = ruteo.despachar(
        "POST", "/resultados/atributos",
        {"ids_persona": [str(base["con"])]}, {}, actor("analista"),
        ctx_solo_boveda)
    assert status == 200
    assert str(base["con"]) in salida["items"]
