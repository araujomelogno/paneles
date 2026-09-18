"""Fase 4 · Bloque 4B — R4.1: historial de atributos, series y longitudinal.

El punto de partida es que **esto es una corrección, no solo una feature**.
Antes del historial, recalcular la composición de una ola de hace un año la
calculaba con la demografía de hoy y devolvía un número que no era el de esa
ola, sin avisar de nada. Así que buena parte de este archivo prueba dos cosas
a la vez: que lo nuevo anda, y que lo de siempre sigue dando igual.
"""

import datetime

import pytest

from panel_api import (
    atributos, composicion, db, demografia, paneles, personas,
)
from panel_api.errores import Conflicto, DatosInvalidos, NoEncontrado

from conftest import VERSION_TEXTO, consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")


def _persona(conn, documento, nombre=None, **datos):
    return personas.alta(
        conn,
        {
            "persona": {"documento": documento,
                        "nombre": nombre or f"Panelista {documento}", **datos},
            "consentimientos": consentimientos(*AMBAS),
        },
    )["id_persona"]


@pytest.fixture
def nse(conn_boveda, actor):
    return atributos.crear(
        conn_boveda,
        {
            "clave": "nse",
            "etiqueta": "Nivel socioeconómico",
            "tipo": "categorico",
            "categorias": [
                {"clave": "alto", "etiqueta": "Alto", "orden": 10},
                {"clave": "medio", "etiqueta": "Medio", "orden": 20},
                {"clave": "bajo", "etiqueta": "Bajo", "orden": 30},
            ],
        },
        actor=actor("admin"),
    )


def _retroceder(conn, id_persona, clave, desde, hasta=None):
    """Mueve la vigencia de un valor hacia atrás en el tiempo.

    Las pruebas necesitan un pasado, y `fijar` solo sabe escribir «desde
    ahora». Esto es el equivalente a haber cargado ese valor entonces.
    """
    db.ejecutar(
        conn,
        """
        update persona_atributo pa
           set desde = %s, hasta = %s
          from atributo_demografico a
         where a.id = pa.atributo_id and a.clave = %s
           and pa.id_persona = %s and pa.hasta is null
        """,
        (desde, hasta, clave, id_persona))


# ════════════════════════════════════════════════════════════════════
#  R4.1.a — Historial de atributos
# ════════════════════════════════════════════════════════════════════

def test_cambiar_un_valor_conserva_el_anterior_con_su_vigencia(
        conn_boveda, nse):
    """El punto central de R4.1.a: el valor viejo no se pierde."""
    id_persona = _persona(conn_boveda, "h-1")
    atributos.fijar(conn_boveda, id_persona, "nse", "bajo", origen="alta")
    conn_boveda.commit()   # el cambio es de otro momento, no de la misma carga
    resultado = atributos.fijar(
        conn_boveda, id_persona, "nse", "medio", origen="edicion")

    assert resultado["estado"] == "completado"
    assert resultado["valor_anterior"] == "bajo"

    historia = atributos.historial_de(conn_boveda, id_persona, "nse")
    assert [h["valor"] for h in historia] == ["bajo", "medio"]
    assert historia[0]["vigente"] is False and historia[0]["hasta"] is not None
    assert historia[1]["vigente"] is True and historia[1]["hasta"] is None
    # El primero vale «desde siempre»: sabemos cuándo cambió, no cuándo empezó.
    assert historia[0]["desde"] is None
    # El segundo abre donde cierra el primero: sin huecos ni solapamiento.
    assert historia[1]["desde"] == historia[0]["hasta"]


def test_dos_cambios_en_la_misma_transaccion_no_inventan_un_periodo(
        conn_boveda, nse):
    """Una carga que corrige un valor que acaba de escribir no vivió nunca
    afuera de la transacción. Registrarlo como un período diría que ese valor
    rigió «desde siempre hasta ahora», y eso envenenaría toda composición
    retroactiva."""
    id_persona = _persona(conn_boveda, "h-2b")
    atributos.fijar(conn_boveda, id_persona, "nse", "bajo", origen="carga")
    atributos.fijar(conn_boveda, id_persona, "nse", "medio", origen="carga")

    historia = atributos.historial_de(conn_boveda, id_persona, "nse")
    assert [h["valor"] for h in historia] == ["medio"]
    assert historia[0]["desde"] is None and historia[0]["vigente"] is True


def test_una_consulta_sin_fecha_usa_el_valor_vigente(conn_boveda, nse):
    """El comportamiento de siempre, sin cambios."""
    id_persona = _persona(conn_boveda, "h-2")
    atributos.fijar(conn_boveda, id_persona, "nse", "bajo", origen="alta")
    conn_boveda.commit()
    atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="edicion")

    vigente = next(v for v in atributos.valores_de(conn_boveda, id_persona)
                   if v["clave"] == "nse")
    assert vigente["valor"] == "alto"


def test_una_consulta_a_una_fecha_usa_el_valor_de_entonces(conn_boveda, nse):
    id_persona = _persona(conn_boveda, "h-3")
    atributos.fijar(conn_boveda, id_persona, "nse", "bajo", origen="alta")
    conn_boveda.commit()
    atributos.fijar(conn_boveda, id_persona, "nse", "alto",
                    origen="edicion", vigencia_desde="2025-06-01")

    def nse_a(momento):
        valores = atributos.valores_de(conn_boveda, id_persona, momento=momento)
        return next((v["valor"] for v in valores if v["clave"] == "nse"), None)

    assert nse_a("2025-01-01") == "bajo"
    assert nse_a("2025-06-02") == "alto"
    assert nse_a(None) == "alto"


def test_el_borrado_de_un_valor_cierra_la_vigencia_y_no_pierde_la_historia(
        conn_boveda, nse):
    """Que hoy no tenga valor no significa que nunca lo haya tenido."""
    id_persona = _persona(conn_boveda, "h-4")
    atributos.fijar(conn_boveda, id_persona, "nse", "medio", origen="alta")
    conn_boveda.commit()   # el valor existió de verdad, no solo acá adentro

    atributos.borrar_valor(conn_boveda, id_persona, "nse")

    assert not [v for v in atributos.valores_de(conn_boveda, id_persona)
                if v["clave"] == "nse"]
    historia = atributos.historial_de(conn_boveda, id_persona, "nse")
    assert len(historia) == 1 and historia[0]["valor"] == "medio"
    ayer = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    assert [v["valor"] for v in
            atributos.valores_de(conn_boveda, id_persona, momento=ayer)
            if v["clave"] == "nse"] == ["medio"]


def test_la_edad_derivada_tambien_es_la_de_entonces(conn_boveda):
    """El otro lado del mismo error: sin parametrizar los derivados, una ola
    de 2020 se recalculaba con la edad de hoy."""
    id_persona = _persona(conn_boveda, "h-5", fecha_nacimiento="1990-06-15")

    hoy = {v["clave"]: v["valor"]
           for v in atributos.valores_de(conn_boveda, id_persona)}
    entonces = {v["clave"]: v["valor"] for v in atributos.valores_de(
        conn_boveda, id_persona, momento="2020-01-01")}

    assert hoy["edad"] == "36" and hoy["tramo_etario"] == "35-44"
    assert entonces["edad"] == "29" and entonces["tramo_etario"] == "25-34"


def test_no_se_puede_insertar_un_valor_anterior_al_vigente(conn_boveda, nse):
    """Reescribir el pasado no es cerrar un período, y no hay forma correcta
    de adivinar dónde encaja: se informa y decide una persona."""
    id_persona = _persona(conn_boveda, "h-6")
    atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="alta")
    _retroceder(conn_boveda, id_persona, "nse", "2025-01-01")

    with pytest.raises(Conflicto) as error:
        atributos.fijar(conn_boveda, id_persona, "nse", "bajo",
                        origen="edicion", vigencia_desde="2024-01-01")
    assert "anterior" in str(error.value.mensaje)


def test_la_base_no_admite_dos_valores_vigentes_a_la_vez(conn_boveda, nse):
    """La restricción de exclusión: un error de código que produjera dos
    valores vigentes a la misma fecha haría que la composición retroactiva
    contara a la persona dos veces, en dos categorías distintas."""
    id_persona = _persona(conn_boveda, "h-7")
    atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="alta")
    fila = db.una(
        conn_boveda,
        "select id from atributo_demografico where clave = 'nse'")
    categoria = db.una(
        conn_boveda,
        "select id from atributo_categoria where atributo_id = %s "
        "and clave = 'bajo'", (fila["id"],))

    import psycopg
    with pytest.raises(psycopg.errors.ExclusionViolation):
        db.ejecutar(
            conn_boveda,
            "insert into persona_atributo "
            "(id_persona, atributo_id, categoria_id, desde, hasta) "
            "values (%s, %s, %s, '2020-01-01', '2030-01-01')",
            (id_persona, fila["id"], categoria["id"]))
    conn_boveda.rollback()


# ── La composición retroactiva, que es el motivo de todo esto ────────

def test_la_composicion_de_una_ola_pasada_usa_los_valores_de_entonces(
        conn_boveda, nse):
    """El caso que la spec llama «sencillamente incorrecto»."""
    panel = paneles.crear(conn_boveda, "Panel longitudinal")["id"]
    for i, (documento, antes, ahora) in enumerate([
        ("c-1", "bajo", "medio"),
        ("c-2", "bajo", "medio"),
        ("c-3", "alto", "alto"),
    ]):
        id_persona = _persona(conn_boveda, documento)
        paneles.agregar_miembro(conn_boveda, panel, id_persona)
        atributos.fijar(conn_boveda, id_persona, "nse", antes, origen="alta")
        _retroceder(conn_boveda, id_persona, "nse", "-infinity", "2025-06-01")
        atributos.fijar(conn_boveda, id_persona, "nse", ahora,
                        origen="edicion", vigencia_desde="2025-06-01")
    # Los tres ya eran del panel en 2025: la composición de entonces los cuenta.
    db.ejecutar(conn_boveda,
                "update membresia set fecha_alta = '2024-01-01' "
                " where panel_id = %s", (panel,))

    def por_categoria(salida):
        dimension = next(d for d in salida["dimensiones"]
                         if d["dimension"] == "nse")
        return {c["categoria"]: c["observados"] for c in dimension["categorias"]}

    hoy = composicion.composicion(conn_boveda, panel, dimensiones=["nse"])
    antes = composicion.composicion(conn_boveda, panel, dimensiones=["nse"],
                                    momento="2025-01-01")

    assert por_categoria(hoy) == {"medio": 2, "alto": 1}
    assert por_categoria(antes) == {"bajo": 2, "alto": 1}
    assert antes["retroactiva"] is True


def test_la_composicion_retroactiva_excluye_a_quien_todavia_no_era_miembro(
        conn_boveda, nse):
    """Quien se incorporó después no estaba en el panel ese día."""
    panel = paneles.crear(conn_boveda, "Panel que creció")["id"]
    viejo = _persona(conn_boveda, "m-1")
    nuevo = _persona(conn_boveda, "m-2")
    for _p in (viejo, nuevo):
        paneles.agregar_miembro(conn_boveda, panel, _p)
    for id_persona in (viejo, nuevo):
        atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="alta")
    db.ejecutar(
        conn_boveda,
        "update membresia set fecha_alta = '2024-01-01' "
        " where panel_id = %s and id_persona = %s", (panel, viejo))
    db.ejecutar(
        conn_boveda,
        "update membresia set fecha_alta = '2026-01-01' "
        " where panel_id = %s and id_persona = %s", (panel, nuevo))

    assert composicion.contar_miembros(conn_boveda, panel) == 2
    assert composicion.contar_miembros(
        conn_boveda, panel, momento="2025-01-01") == 1


def test_la_composicion_retroactiva_cuenta_a_quien_despues_se_dio_de_baja(
        conn_boveda, nse):
    """A una fecha pasada, «activo» no es el estado de hoy sino el de
    entonces. Contarlo como baja deja a la ola con menos gente de la que
    tuvo."""
    panel = paneles.crear(conn_boveda, "Panel con bajas")["id"]
    id_persona = _persona(conn_boveda, "b-1")
    paneles.agregar_miembro(conn_boveda, panel, id_persona)
    db.ejecutar(
        conn_boveda,
        "update membresia set fecha_alta = '2024-01-01', "
        "       fecha_baja = '2026-01-01', estado = 'baja' "
        " where panel_id = %s", (panel,))

    assert composicion.contar_miembros(conn_boveda, panel) == 0
    assert composicion.contar_miembros(
        conn_boveda, panel, momento="2025-01-01") == 1


def test_la_composicion_retroactiva_avisa_que_el_objetivo_no_se_historiza(
        conn_boveda, nse):
    """Un número que se entiende en vez de uno que engaña: la foto es de
    entonces, el universo de referencia es el de hoy."""
    panel = paneles.crear(conn_boveda, "Panel con cuota")["id"]
    composicion.guardar_objetivo(conn_boveda, panel, [
        {"dimension": "nse", "categoria": "alto", "proporcion": 0.3},
        {"dimension": "nse", "categoria": "medio", "proporcion": 0.4},
        {"dimension": "nse", "categoria": "bajo", "proporcion": 0.3},
    ])
    salida = composicion.composicion(conn_boveda, panel, dimensiones=["nse"],
                                     momento="2025-01-01")
    assert "no se historizan" in salida["aviso_objetivo"]


# ── No regresión: lo de siempre sigue dando lo mismo ─────────────────

def test_las_consultas_demograficas_devuelven_lo_mismo_que_antes(
        conn_boveda, nse):
    """El punto del DoD: las consultas existentes no cambian de resultado."""
    esperados = set()
    for documento, valor in [("r-1", "alto"), ("r-2", "alto"), ("r-3", "bajo")]:
        id_persona = _persona(conn_boveda, documento)
        atributos.fijar(conn_boveda, id_persona, "nse", valor, origen="alta")
        if valor == "alto":
            esperados.add(id_persona)

    criterios = [{"dimension": "nse", "operador": "eq", "valor": "alto"}]
    obtenidos = set(demografia.ids_del_segmento(conn_boveda, criterios))
    assert obtenidos == esperados


def test_una_consulta_a_fecha_filtra_por_el_valor_de_entonces(
        conn_boveda, nse):
    """«Quiénes eran de nivel bajo cuando salimos a campo» deja de ser la
    misma pregunta que «quiénes lo son hoy»."""
    se_movio = _persona(conn_boveda, "f-1")
    atributos.fijar(conn_boveda, se_movio, "nse", "bajo", origen="alta")
    conn_boveda.commit()
    atributos.fijar(conn_boveda, se_movio, "nse", "alto",
                    origen="edicion", vigencia_desde="2025-06-01")

    criterios = [{"dimension": "nse", "operador": "eq", "valor": "bajo"}]
    donde, parametros = demografia.condiciones(criterios, momento="2025-01-01")
    fila = db.una(
        conn_boveda,
        "select count(*)::int as n from persona p where " + donde[0],
        tuple(parametros))
    assert fila["n"] == 1

    donde_hoy, parametros_hoy = demografia.condiciones(criterios)
    fila_hoy = db.una(
        conn_boveda,
        "select count(*)::int as n from persona p where " + donde_hoy[0],
        tuple(parametros_hoy))
    assert fila_hoy["n"] == 0
