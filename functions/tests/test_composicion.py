"""R2.2 y R2.3 — universo de referencia, composición y brecha."""

import pytest

from panel_api import composicion, paneles, personas
from panel_api.errores import DatosInvalidos, NoEncontrado

from conftest import consentimientos


@pytest.fixture
def panel_con_gente(conn_boveda):
    """Cuatro miembros: 2 F y 2 M, todos de Montevideo salvo uno."""
    panel = paneles.crear(conn_boveda, "Panel Nacional")
    gente = [
        ("Ana", "F", "1990-01-01", "Montevideo"),
        ("Bea", "F", "1970-01-01", "Salto"),
        ("Caro", "M", "1985-01-01", "Montevideo"),
        ("Dario", "M", "2000-01-01", "Montevideo"),
    ]
    for nombre, sexo, fnac, localidad in gente:
        alta = personas.alta(conn_boveda, {
            "persona": {"nombre": nombre, "sexo": sexo, "fecha_nacimiento": fnac,
                        "localidad": localidad, "email": f"{nombre.lower()}@ej.uy"},
            "consentimientos": consentimientos(),
            "panel_id": panel["id"],
        })
    conn_boveda.commit()
    return panel


def _dimension(salida, nombre):
    return next(d for d in salida["dimensiones"] if d["dimension"] == nombre)


def _categoria(dimension, nombre):
    return next(c for c in dimension["categorias"] if c["categoria"] == nombre)


# ── R2.3: sin objetivo, solo descriptivo ─────────────────────────────

def test_sin_objetivo_la_composicion_es_descriptiva_y_la_brecha_no_esta_disponible(
    conn_boveda, panel_con_gente
):
    """DoD: «sin objetivo, solo descriptivo».

    La diferencia que importa es entre «no hay brecha» y «no se puede
    calcular la brecha»: la respuesta dice explícitamente la segunda.
    """
    salida = composicion.composicion(conn_boveda, panel_con_gente["id"])

    assert salida["miembros"] == 4
    assert salida["objetivo_cargado"] is False

    sexo = _dimension(salida, "sexo")
    assert sexo["brecha_disponible"] is False
    assert "no se puede calcular" in sexo["motivo_sin_brecha"]
    assert sexo["disimilitud"] is None
    # El descriptivo sí está.
    assert _categoria(sexo, "F")["observados"] == 2
    assert _categoria(sexo, "F")["proporcion_observada"] == 0.5
    assert _categoria(sexo, "F")["brecha"] is None


# ── R2.2: carga del universo de referencia ───────────────────────────

def test_con_objetivo_cargado_aparece_la_brecha(conn_boveda, panel_con_gente):
    """DoD: «composición muestra descriptivo y brecha contra un objetivo
    cargado»."""
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.75},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.25},
    ])
    conn_boveda.commit()

    salida = composicion.composicion(conn_boveda, panel_con_gente["id"],
                                     dimensiones=["sexo"])
    sexo = _dimension(salida, "sexo")
    assert sexo["brecha_disponible"] is True
    assert sexo["motivo_sin_brecha"] is None

    mujeres = _categoria(sexo, "F")
    assert mujeres["proporcion_observada"] == 0.5
    assert mujeres["proporcion_objetivo"] == 0.75
    assert mujeres["brecha"] == -0.25          # falta gente
    assert mujeres["esperados"] == 3
    assert mujeres["faltan"] == 1

    varones = _categoria(sexo, "M")
    assert varones["brecha"] == 0.25           # sobra gente
    assert varones["sobran"] == 1
    assert sexo["disimilitud"] == 0.25


def test_las_proporciones_de_una_dimension_se_validan_contra_uno(
    conn_boveda, panel_con_gente
):
    """R2.2: «las proporciones de una dimensión se validan (suman ~1)».

    Un universo que suma 0,8 no es un universo incompleto: hace que todas las
    brechas de esa dimensión estén mal. Se rechaza al cargarlo.
    """
    with pytest.raises(DatosInvalidos) as error:
        composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
            {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
            {"dimension": "sexo", "categoria": "M", "proporcion": 0.3},
        ])
    assert error.value.detalle["suma"] == pytest.approx(0.8)
    assert error.value.detalle["dimension"] == "sexo"
    # Y no quedó nada a medio cargar.
    assert composicion.obtener_objetivo(conn_boveda, panel_con_gente["id"])["items"] == []


def test_se_le_perdona_el_redondeo_a_dos_decimales(conn_boveda, panel_con_gente):
    """Siete tramos etarios redondeados no suman exactamente 1 casi nunca."""
    tramos = {"18-24": 0.14, "25-34": 0.19, "35-44": 0.18,
              "45-54": 0.17, "55-64": 0.15, "65+": 0.17}
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "tramo_etario", "categoria": c, "proporcion": p}
        for c, p in tramos.items()
    ])
    conn_boveda.commit()
    assert len(composicion.obtener_objetivo(
        conn_boveda, panel_con_gente["id"]
    )["items"]) == 6


def test_la_validacion_es_por_dimension_no_sobre_el_total(
    conn_boveda, panel_con_gente
):
    """Cargar solo `sexo` tiene que poder hacerse: el total del panel es 1, no 2."""
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.5},
    ])
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "localidad", "categoria": "Montevideo", "proporcion": 0.4},
        {"dimension": "localidad", "categoria": "Salto", "proporcion": 0.6},
    ])
    conn_boveda.commit()
    objetivo = composicion.obtener_objetivo(conn_boveda, panel_con_gente["id"])
    assert set(objetivo["dimensiones"]) == {"sexo", "localidad"}


def test_recargar_una_dimension_la_reemplaza_y_no_toca_las_otras(
    conn_boveda, panel_con_gente
):
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.5},
    ])
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "localidad", "categoria": "Montevideo", "proporcion": 1.0},
    ])
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.6},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.4},
    ])
    conn_boveda.commit()
    objetivo = composicion.obtener_objetivo(conn_boveda, panel_con_gente["id"])
    assert objetivo["dimensiones"]["sexo"] == {"F": 0.6, "M": 0.4}
    assert objetivo["dimensiones"]["localidad"] == {"Montevideo": 1.0}


@pytest.mark.parametrize("objetivos", [
    [{"dimension": "inventada", "categoria": "x", "proporcion": 1.0}],
    [{"dimension": "sexo", "categoria": "", "proporcion": 1.0}],
    [{"dimension": "sexo", "categoria": "F", "proporcion": 75}],      # porcentaje
    [{"dimension": "sexo", "categoria": "F", "proporcion": "muchas"}],
    [{"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
     {"dimension": "sexo", "categoria": "F", "proporcion": 0.5}],     # repetida
])
def test_un_objetivo_mal_formado_se_rechaza(conn_boveda, panel_con_gente, objetivos):
    with pytest.raises(DatosInvalidos):
        composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], objetivos)


def test_borrar_el_objetivo_vuelve_a_dejar_la_composicion_descriptiva(
    conn_boveda, panel_con_gente
):
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.5},
    ])
    composicion.borrar_objetivo(conn_boveda, panel_con_gente["id"], "sexo")
    conn_boveda.commit()
    salida = composicion.composicion(conn_boveda, panel_con_gente["id"],
                                     dimensiones=["sexo"])
    assert _dimension(salida, "sexo")["brecha_disponible"] is False


def test_una_categoria_del_objetivo_sin_nadie_aparece_con_su_faltante(
    conn_boveda, panel_con_gente
):
    """El caso que hace útil la brecha: la categoría que falta por completo
    tiene que aparecer, no desaparecer por no tener observados."""
    composicion.guardar_objetivo(conn_boveda, panel_con_gente["id"], [
        {"dimension": "localidad", "categoria": "Montevideo", "proporcion": 0.5},
        {"dimension": "localidad", "categoria": "Salto", "proporcion": 0.25},
        {"dimension": "localidad", "categoria": "Rivera", "proporcion": 0.25},
    ])
    conn_boveda.commit()
    localidad = _dimension(
        composicion.composicion(conn_boveda, panel_con_gente["id"],
                                dimensiones=["localidad"]),
        "localidad",
    )
    rivera = _categoria(localidad, "Rivera")
    assert rivera["observados"] == 0
    assert rivera["faltan"] == 1


def test_el_objetivo_de_un_panel_que_no_existe_da_404(conn_boveda):
    with pytest.raises(NoEncontrado):
        composicion.guardar_objetivo(conn_boveda, 9999, [
            {"dimension": "sexo", "categoria": "F", "proporcion": 1.0},
        ])


# ── P1: cruce de dos dimensiones ─────────────────────────────────────

def test_el_cruce_de_dos_dimensiones_muestra_las_celdas(conn_boveda, panel_con_gente):
    """P1 — «composición por cruce de dos dimensiones (sexo × tramo etario)».

    Es donde aparecen los huecos que las marginales esconden.
    """
    salida = composicion.composicion(
        conn_boveda, panel_con_gente["id"], cruce_de=["sexo", "localidad"]
    )
    cruce = salida["cruce"]
    assert cruce["dimensiones"] == ["sexo", "localidad"]
    assert cruce["miembros"] == 4
    assert cruce["brecha_disponible"] is False
    assert "marginales" in cruce["motivo_sin_brecha"]

    celdas = {(c["sexo"], c["localidad"]): c["observados"] for c in cruce["celdas"]}
    assert celdas[("F", "Montevideo")] == 1
    assert celdas[("F", "Salto")] == 1
    assert celdas[("M", "Montevideo")] == 2
    assert ("M", "Salto") not in celdas       # el hueco


def test_el_cruce_exige_dos_dimensiones_distintas_y_conocidas(
    conn_boveda, panel_con_gente
):
    with pytest.raises(DatosInvalidos):
        composicion.cruce(conn_boveda, panel_con_gente["id"], "sexo", "sexo")
    with pytest.raises(DatosInvalidos):
        composicion.cruce(conn_boveda, panel_con_gente["id"], "sexo", "inventada")


def test_los_miembros_dados_de_baja_no_cuentan_en_la_composicion(
    conn_boveda, panel_con_gente
):
    miembros = paneles.listar_miembros(conn_boveda, panel_con_gente["id"])
    paneles.dar_de_baja_miembro(
        conn_boveda, panel_con_gente["id"], miembros[0]["id_persona"]
    )
    conn_boveda.commit()
    assert composicion.composicion(
        conn_boveda, panel_con_gente["id"]
    )["miembros"] == 3
