"""Fase 4 · Bloque 4B — R4.2: optimizador de muestreo.

Las reglas de R3.1 alcanzan cuando hay holgura. Este archivo prueba lo que
pasa cuando no la hay, que es donde el optimizador existe para servir: cerrar
cuota y cuidar a la gente tiran para lados opuestos y hay que negociar.

Dos cosas se prueban a la vez en casi todos los casos: que la selección sea la
correcta, y que sea **explicable**. Una selección óptima que nadie puede
defender ante un investigador no sirve para decidir.
"""

import datetime

import pytest

from panel_api import (
    atributos, composicion, db, encuestas, muestreo, optimizador, paneles,
    personas, preferencias,
)
from panel_api.errores import DatosInvalidos, NoEncontrado

from conftest import VERSION_TEXTO, consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")


def _persona(conn, documento, sexo, **datos):
    id_persona = personas.alta(
        conn,
        {
            "persona": {"documento": documento, "nombre": f"P {documento}",
                        **datos},
            "consentimientos": consentimientos(*AMBAS),
        },
    )["id_persona"]
    atributos.fijar(conn, id_persona, "sexo", sexo, origen="alta")
    return id_persona


def _convocatorias(conn, id_persona, panel, cuantas, dias_atras=30):
    """Le inventa a esta persona un historial de convocatorias.

    Es lo que hace que la fatiga tenga algo que medir: sin historial todos los
    candidatos son idénticos y el optimizador no se distingue de las reglas.
    """
    for i in range(cuantas):
        encuesta = encuestas.crear(
            conn, panel, f"Vieja {id_persona}-{i}", "2026-01-01")
        db.ejecutar(
            conn,
            "insert into participacion (encuesta_id, id_persona, convocado_en, "
            "                           origen) "
            "values (%s, %s, now() - make_interval(days => %s), 'convocatoria')",
            (encuesta["id"], id_persona, dias_atras + i))


@pytest.fixture
def panel_tenso(conn_boveda):
    """Un panel donde la cuota y la fatiga se contradicen.

    Objetivo 50/50. Las mujeres están frescas; los tres varones vienen muy
    convocados. Cerrar la cuota exige recaer justo sobre los quemados.
    """
    panel = paneles.crear(conn_boveda, "Panel tenso")["id"]
    gente = {}
    for i in range(6):
        id_persona = _persona(conn_boveda, f"f-{i}", "F")
        paneles.agregar_miembro(conn_boveda, panel, id_persona)
        gente[f"f-{i}"] = id_persona
    for i in range(3):
        id_persona = _persona(conn_boveda, f"m-{i}", "M")
        paneles.agregar_miembro(conn_boveda, panel, id_persona)
        gente[f"m-{i}"] = id_persona
        # Dos de tres permitidas: elegibles, pero caros.
        _convocatorias(conn_boveda, id_persona, panel, 2, dias_atras=40)

    composicion.guardar_objetivo(conn_boveda, panel, [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.5},
    ])
    encuesta = encuestas.crear(conn_boveda, panel, "Ola nueva", "2026-10-01")
    return {"panel": panel, "encuesta": encuesta["id"], "gente": gente}


# ── Qué optimiza ─────────────────────────────────────────────────────

def test_cierra_la_brecha_respetando_las_restricciones_duras(
        conn_boveda, panel_tenso):
    salida = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=6)

    por_categoria = {}
    for persona in salida["propuesta"]:
        por_categoria[persona["categoria"]] = \
            por_categoria.get(persona["categoria"], 0) + 1
    # 50/50 sobre 6 = tres y tres, y los tres varones elegibles alcanzan justo.
    assert por_categoria == {"F": 3, "M": 3}
    assert salida["factible"] is True
    assert salida["convoca"] is False


def test_sin_consentimiento_no_entra_por_mucho_que_cierre_la_cuota(
        conn_boveda, panel_tenso):
    """El consentimiento es duro: no lo compra ningún peso."""
    from panel_api import consentimiento
    sin_consentimiento = _persona(conn_boveda, "m-9", "M")
    paneles.agregar_miembro(conn_boveda, panel_tenso["panel"], sin_consentimiento)
    consentimiento.marcar_retirado(conn_boveda, sin_consentimiento)

    salida = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=8,
        pesos={"peso_brecha": 1000.0, "peso_fatiga": 0.0, "peso_equidad": 0.0})

    assert str(sin_consentimiento) not in {
        p["id_persona"] for p in salida["propuesta"]}
    motivo = next(e["motivo"] for e in salida["excluidos"]
                  if e["id_persona"] == str(sin_consentimiento))
    assert motivo in (muestreo.SIN_CONSENTIMIENTO, muestreo.PENDIENTE)


def test_la_preferencia_de_canal_es_dura_cuando_se_sabe_por_donde_se_convoca(
        conn_boveda, panel_tenso):
    """Una muestra óptima a la que no se le puede mandar nada no es una
    muestra (R4.4 + R4.2)."""
    acepta = panel_tenso["gente"]["f-0"]
    db.ejecutar(conn_boveda,
                "update persona set celular = '+59899111222' "
                " where id_persona = %s", (acepta,))
    preferencias.otorgar(conn_boveda, acepta, "whatsapp",
                         version_texto="wa-2026-01", origen="alta_manual")

    salida = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=6,
        canal="whatsapp")

    assert {p["id_persona"] for p in salida["propuesta"]} == {str(acepta)}
    assert any(e["motivo"] == "sin_preferencia_whatsapp"
               for e in salida["excluidos"])


def test_penaliza_la_fatiga_en_vez_de_prohibirla(conn_boveda, panel_tenso):
    """La diferencia con una restricción dura: con la cuota de por medio, los
    quemados entran igual; sin ella, se prefiere a los frescos."""
    sin_presion = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=3,
        pesos={"peso_brecha": 0.0, "peso_fatiga": 5.0, "peso_equidad": 0.0})
    # Sin brecha que cerrar, gana la gente fresca: las mujeres.
    assert {p["categoria"] for p in sin_presion["propuesta"]} == {"F"}

    con_presion = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=6)
    assert any(p["categoria"] == "M" for p in con_presion["propuesta"])


def test_los_pesos_son_configurables_y_cambian_la_seleccion(
        conn_boveda, panel_tenso, actor):
    por_defecto = optimizador.obtener_pesos(conn_boveda, panel_tenso["panel"])
    assert por_defecto["configurados"] is False
    assert por_defecto["peso_fatiga"] == optimizador.PESOS_POR_DEFECTO["peso_fatiga"]

    guardados = optimizador.guardar_pesos(
        conn_boveda, panel_tenso["panel"],
        {"peso_fatiga": 50.0}, actor=actor("operaciones"))
    assert guardados["configurados"] is True
    assert guardados["peso_fatiga"] == 50.0
    assert guardados["actualizado_por"] == "operaciones@equipos.com.uy"

    # Con la fatiga carísima, ya no vale la pena tocar a los quemados para
    # cerrar la cuota.
    salida = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=6)
    assert all(p["categoria"] == "F" for p in salida["propuesta"])


def test_un_peso_negativo_se_rechaza(conn_boveda, panel_tenso, actor):
    """Invertiría el sentido de la restricción: premiaría convocar a los más
    convocados."""
    with pytest.raises(DatosInvalidos) as error:
        optimizador.guardar_pesos(
            conn_boveda, panel_tenso["panel"], {"peso_fatiga": -1.0},
            actor=actor("operaciones"))
    assert "negativo" in str(error.value.mensaje)


# ── Explicabilidad ───────────────────────────────────────────────────

def test_cada_individuo_incluido_es_explicable(conn_boveda, panel_tenso):
    """El punto del DoD: por qué segmento entró y qué peso tuvo."""
    salida = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=6)

    for persona in salida["propuesta"]:
        porque = persona["porque"]
        assert persona["categoria"] in ("F", "M")
        assert set(porque) == {
            "deficit_del_segmento_al_entrar", "aporte_a_la_brecha",
            "costo_fatiga", "costo_equidad", "puntaje"}
        # El aporte a la brecha reproduce la cuenta: 2·déficit − 1, escalado.
        esperado = (salida["pesos"]["peso_brecha"]
                    * (2 * porque["deficit_del_segmento_al_entrar"] - 1) / 6)
        assert porque["aporte_a_la_brecha"] == pytest.approx(esperado, abs=1e-4)
        assert porque["puntaje"] == pytest.approx(
            porque["aporte_a_la_brecha"] - porque["costo_fatiga"]
            - porque["costo_equidad"], abs=1e-4)
    # Y el orden en que entraron está registrado.
    assert [p["orden"] for p in salida["propuesta"]] == list(range(1, 7))


def test_los_quemados_cuestan_mas_que_los_frescos(conn_boveda, panel_tenso):
    salida = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=6)
    frescos = [p for p in salida["propuesta"] if p["categoria"] == "F"]
    quemados = [p for p in salida["propuesta"] if p["categoria"] == "M"]
    assert max(p["porque"]["costo_fatiga"] for p in frescos) < \
           min(p["porque"]["costo_fatiga"] for p in quemados)


def test_cada_exclusion_sale_con_su_motivo(conn_boveda, panel_tenso):
    salida = optimizador.optimizar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=4)
    assert salida["excluidos"], "un panel de nueve con cuatro elegidos"
    assert all(e["motivo"] and e["explicacion"] for e in salida["excluidos"])
    assert salida["resumen_exclusiones"]


# ── Infactibilidad: lo dice, con alternativas cuantificadas ──────────

@pytest.fixture
def panel_infactible(conn_boveda):
    """Cuota 50/50 y solo un varón, y encima quemado hasta el tope."""
    panel = paneles.crear(conn_boveda, "Panel imposible")["id"]
    for i in range(6):
        id_persona = _persona(conn_boveda, f"i-f-{i}", "F")
        paneles.agregar_miembro(conn_boveda, panel, id_persona)
    unico = _persona(conn_boveda, "i-m-0", "M")
    paneles.agregar_miembro(conn_boveda, panel, unico)
    _convocatorias(conn_boveda, unico, panel, 3, dias_atras=40)

    composicion.guardar_objetivo(conn_boveda, panel, [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.5},
    ])
    encuesta = encuestas.crear(conn_boveda, panel, "Ola imposible", "2026-10-01")
    return {"panel": panel, "encuesta": encuesta["id"], "unico": unico}


def test_una_cuota_infactible_se_informa_en_vez_de_violarse(
        conn_boveda, panel_infactible):
    """El punto del DoD: lo dice explícitamente y no elige por su cuenta."""
    salida = optimizador.optimizar(
        conn_boveda, panel_infactible["encuesta"], dimension="sexo", cantidad=6)

    assert salida["factible"] is False
    assert salida["sin_cubrir"].get("M", 0) > 0
    assert "no se puede llenar la cuota" in salida["motivo"].lower()
    assert salida["elige_el_sistema"] is False
    # El único varón está quemado hasta el tope: no se lo saltea.
    assert str(panel_infactible["unico"]) not in {
        p["id_persona"] for p in salida["propuesta"]}


def test_las_tres_alternativas_vienen_cuantificadas(
        conn_boveda, panel_infactible):
    salida = optimizador.optimizar(
        conn_boveda, panel_infactible["encuesta"], dimension="sexo", cantidad=6)
    opciones = {a["opcion"]: a for a in salida["alternativas"]}

    assert set(opciones) == {"reducir_el_tamano", "aflojar_la_fatiga",
                             "aceptar_la_brecha"}
    # Reducir: el número concreto que sí se puede armar hoy.
    assert opciones["reducir_el_tamano"]["tamano"] == len(salida["propuesta"])
    # Aflojar: cuántos más pasan a ser convocables, medido corriendo el motor.
    aflojar = opciones["aflojar_la_fatiga"]
    assert aflojar["max_convocatorias_ventana"] > \
           salida["umbrales"]["max_convocatorias_ventana"]
    assert aflojar["elegibles"] >= salida["elegibles"]
    # Aceptar: cuánto se desvía la muestra del universo.
    assert opciones["aceptar_la_brecha"]["disimilitud"] >= 0
    assert all(a["cuesta"] for a in salida["alternativas"])


def test_aflojar_el_umbral_suma_gente_pero_no_inventa_la_que_no_hay(
        conn_boveda, panel_infactible):
    """Subir el tope hace elegible al único varón, y la alternativa lo dice
    con el número. Lo que ninguna relajación puede hacer es conjurar los dos
    varones que el panel no tiene: por eso `alcanza` sigue en falso, que es la
    respuesta honesta y no un fracaso del cálculo."""
    salida = optimizador.optimizar(
        conn_boveda, panel_infactible["encuesta"], dimension="sexo", cantidad=6)
    aflojar = next(a for a in salida["alternativas"]
                   if a["opcion"] == "aflojar_la_fatiga")
    assert aflojar["elegibles"] > salida["elegibles"]
    assert aflojar["alcanza"] is False
    assert aflojar["sin_cubrir"].get("M", 0) > 0

    relajada = optimizador.optimizar(
        conn_boveda, panel_infactible["encuesta"], dimension="sexo", cantidad=6,
        max_convocatorias_ventana=aflojar["max_convocatorias_ventana"])
    assert str(panel_infactible["unico"]) in {
        p["id_persona"] for p in relajada["propuesta"]}


def test_calcular_las_alternativas_no_se_recurre_infinitamente(
        conn_boveda, panel_infactible):
    """Cuantificar «aflojar la fatiga» corre el mismo motor: la corrida
    interna no vuelve a buscarse alternativas."""
    interna = optimizador.optimizar(
        conn_boveda, panel_infactible["encuesta"], dimension="sexo",
        cantidad=6, con_alternativas=False)
    assert interna["factible"] is False
    assert interna["alternativas"] == []


# ── Contra las reglas de R3.1 ────────────────────────────────────────

def test_se_puede_ver_la_diferencia_con_las_reglas(conn_boveda, panel_tenso):
    """Poder mostrarla es lo que permite justificar el cambio de método."""
    comparacion = optimizador.comparar(
        conn_boveda, panel_tenso["encuesta"], dimension="sexo", cantidad=6)

    assert comparacion["reglas"]["metodo"] == "reglas (R3.1)"
    assert comparacion["optimizador"]["metodo"] == "optimizador (R4.2)"
    assert set(comparacion["reglas"]["por_categoria"]) <= {"F", "M"}
    assert isinstance(comparacion["coinciden"], bool)
    assert comparacion["en_las_dos"] + len(comparacion["solo_en_reglas"]) == \
           comparacion["reglas"]["personas"]


def test_con_holgura_los_dos_metodos_dan_lo_mismo(conn_boveda):
    """La spec lo dice: con holgura coinciden. La diferencia aparece justo
    donde duele, y por eso conviene mantener las reglas de respaldo."""
    panel = paneles.crear(conn_boveda, "Panel holgado")["id"]
    for i in range(4):
        for sexo in ("F", "M"):
            id_persona = _persona(conn_boveda, f"h-{sexo}-{i}", sexo)
            paneles.agregar_miembro(conn_boveda, panel, id_persona)
    composicion.guardar_objetivo(conn_boveda, panel, [
        {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion": 0.5},
    ])
    encuesta = encuestas.crear(conn_boveda, panel, "Ola holgada", "2026-10-01")

    comparacion = optimizador.comparar(
        conn_boveda, encuesta["id"], dimension="sexo", cantidad=4)
    assert comparacion["reglas"]["por_categoria"] == \
           comparacion["optimizador"]["por_categoria"] == {"F": 2, "M": 2}


# ── Validaciones ─────────────────────────────────────────────────────

@pytest.mark.parametrize("cantidad", [0, -3, "muchas"])
def test_una_cantidad_invalida_se_rechaza(conn_boveda, panel_tenso, cantidad):
    with pytest.raises(DatosInvalidos):
        optimizador.optimizar(conn_boveda, panel_tenso["encuesta"],
                              cantidad=cantidad)


def test_una_dimension_que_no_es_de_cuota_se_rechaza(conn_boveda, panel_tenso):
    with pytest.raises(DatosInvalidos) as error:
        optimizador.optimizar(conn_boveda, panel_tenso["encuesta"],
                              dimension="inventada")
    assert "dimensiones_validas" in (error.value.detalle or {})


def test_una_encuesta_que_no_existe_se_rechaza(conn_boveda):
    with pytest.raises(NoEncontrado):
        optimizador.optimizar(conn_boveda, 99999)


def test_si_aflojar_la_fatiga_no_suma_a_nadie_se_dice_una_vez(conn_boveda):
    """Repetir tres pasos que dicen todos «0 personas más» es ruido: cuando
    el cuello de botella no es la fatiga, la alternativa se informa una vez
    y con ese motivo."""
    panel = paneles.crear(conn_boveda, "Panel chico y fresco")["id"]
    for i in range(2):
        id_persona = _persona(conn_boveda, f"ch-{i}", "F")
        paneles.agregar_miembro(conn_boveda, panel, id_persona)
    encuesta = encuestas.crear(conn_boveda, panel, "Ola chica", "2026-10-01")

    salida = optimizador.optimizar(
        conn_boveda, encuesta["id"], dimension="sexo", cantidad=50)
    aflojar = [a for a in salida["alternativas"]
               if a["opcion"] == "aflojar_la_fatiga"]

    assert salida["factible"] is False
    assert len(aflojar) == 1
    assert "no cambia nada" in aflojar[0]["descripcion"]
    assert "no es la fatiga" in aflojar[0]["cuesta"]
