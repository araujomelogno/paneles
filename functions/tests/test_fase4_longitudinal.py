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
    atributos, composicion, db, demografia, encuestas, longitudinal, paneles,
    personas, series,
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


# ════════════════════════════════════════════════════════════════════
#  R4.1.b — Series comparables entre olas
# ════════════════════════════════════════════════════════════════════

P_OLA1 = {"codigo": "P1", "texto": "¿Cómo evalúa la situación económica del país?",
          "tipo": "cerrada",
          "opciones": {"1": "Buena", "2": "Regular", "3": "Mala"}, "orden": 1}
P_OLA2 = {"codigo": "Q7",
          "texto": "¿Cómo calificaría hoy la situación económica del país?",
          "tipo": "cerrada",
          "opciones": {"A": "Buena", "B": "Ni buena ni mala", "C": "Mala"},
          "orden": 1}
P_DISTINTA = {"codigo": "P9", "texto": "¿Qué marca de yerba compra?",
              "tipo": "cerrada",
              "opciones": {"1": "Canarias", "2": "Sara"}, "orden": 2}


@pytest.fixture
def dos_olas(conn_boveda, conn_semantica, proveedor):
    """Dos olas que preguntan lo mismo con otras palabras y otras opciones.

    Es el caso que R4.1.b existe para resolver: el sistema no canoniza, así
    que sin una serie declarada las dos preguntas son dos cosas distintas.
    """
    panel = paneles.crear(conn_boveda, "Panel de opinión")["id"]
    gente = {}
    for indice, documento in enumerate(["s-1", "s-2", "s-3", "s-4"]):
        id_persona = _persona(conn_boveda, documento)
        paneles.agregar_miembro(conn_boveda, panel, id_persona)
        gente[documento] = id_persona

    olas = {}
    for nombre, fecha, pregunta, respuestas in [
        ("Ola 1", "2025-03-01", P_OLA1,
         {"s-1": "Buena", "s-2": "Mala", "s-3": "Regular", "s-4": "Buena"}),
        ("Ola 2", "2026-03-01", P_OLA2,
         # s-1 empeora, s-2 mejora, s-3 se queda igual, s-4 no participa.
         {"s-1": "Mala", "s-2": "Buena", "s-3": "Ni buena ni mala"}),
    ]:
        encuesta = encuestas.crear(conn_boveda, panel, nombre, fecha)
        ids = [gente[d] for d in respuestas]
        encuestas.convocar(conn_boveda, encuesta["id"], ids_persona=ids)
        encuestas.ingestar(
            conn_boveda, conn_semantica, encuesta["id"],
            [pregunta, P_DISTINTA],
            [{"id_persona": str(gente[d]), pregunta["codigo"]: valor,
              "P9": "Canarias"}
             for d, valor in respuestas.items()],
            columna_id="id_persona", tipo_identificador="id_persona",
            proveedor=proveedor)
        olas[nombre] = encuesta

    return {"panel": panel, "gente": gente, "olas": olas}


def _pregunta_id(conn_semantica, codigo, ola):
    fila = db.una(
        conn_semantica,
        "select p.id from pregunta p join cuestionario c on c.id = p.cuestionario_id "
        " where p.codigo = %s and c.nombre = %s", (codigo, ola))
    return fila["id"]


@pytest.fixture
def serie_economia(conn_semantica, conn_boveda, dos_olas, actor):
    creada = series.crear(
        conn_semantica, conn_boveda,
        {"clave": "situacion_economica", "nombre": "Situación económica",
         "categorias": [{"clave": "buena", "etiqueta": "Buena"},
                        {"clave": "intermedia", "etiqueta": "Ni buena ni mala"},
                        {"clave": "mala", "etiqueta": "Mala"}]},
        actor=actor("analista"))
    series.agregar_pregunta(
        conn_semantica, conn_boveda, "situacion_economica",
        _pregunta_id(conn_semantica, "P1", "Ola 1"),
        mapeo={"1": "buena", "2": "intermedia", "3": "mala"},
        actor=actor("analista"))
    series.agregar_pregunta(
        conn_semantica, conn_boveda, "situacion_economica",
        _pregunta_id(conn_semantica, "Q7", "Ola 2"),
        mapeo={"A": "buena", "B": "intermedia", "C": "mala"},
        actor=actor("analista"))
    return creada


def test_una_serie_declara_que_dos_preguntas_son_la_misma_medicion(
        conn_semantica, serie_economia):
    serie = series.obtener(conn_semantica, "situacion_economica")
    assert [p["codigo"] for p in serie["preguntas"]] == ["P1", "Q7"]
    assert [p["ola"] for p in serie["preguntas"]] == ["Ola 1", "Ola 2"]
    # Las opciones de cada ola, distintas entre sí, llegan al mismo vocabulario.
    assert serie["preguntas"][0]["mapeo"] == {"1": "buena", "2": "intermedia",
                                              "3": "mala"}
    assert serie["preguntas"][1]["mapeo"] == {"A": "buena", "B": "intermedia",
                                              "C": "mala"}
    assert all(not p["sin_mapear"] for p in serie["preguntas"])


def test_el_sistema_sugiere_candidatas_pero_no_las_agrega_solo(
        conn_semantica, conn_boveda, dos_olas, proveedor, actor):
    """El punto del DoD: propone, y nadie entra sin que alguien la acepte."""
    series.crear(
        conn_semantica, conn_boveda,
        {"clave": "economia", "nombre": "Economía",
         "categorias": [{"clave": "buena", "etiqueta": "Buena"}]},
        actor=actor("analista"))
    series.agregar_pregunta(
        conn_semantica, conn_boveda, "economia",
        _pregunta_id(conn_semantica, "P1", "Ola 1"), actor=actor("analista"))

    sugeridas = series.sugerir(conn_semantica, "economia", proveedor=proveedor,
                               distancia_maxima=1.0)
    codigos = [s["codigo"] for s in sugeridas["sugerencias"]]

    assert sugeridas["propone_no_agrega"] is True
    assert "Q7" in codigos, "la pregunta equivalente de la otra ola"
    # La más parecida es la equivalente, no la de yerba.
    assert codigos[0] == "Q7"
    # Y sugerir no agregó nada.
    assert len(series.obtener(conn_semantica, "economia")["preguntas"]) == 1


def test_la_sugerencia_no_propone_preguntas_de_una_ola_ya_cubierta(
        conn_semantica, serie_economia, proveedor):
    """Proponer otra pregunta de una ola que la serie ya tiene duplicaría a
    esa gente al comparar."""
    sugeridas = series.sugerir(conn_semantica, "situacion_economica",
                               proveedor=proveedor, distancia_maxima=1.0)
    assert sugeridas["sugerencias"] == []


def test_aceptar_una_sugerencia_queda_marcada_como_tal(
        conn_semantica, conn_boveda, dos_olas, proveedor, actor):
    series.crear(
        conn_semantica, conn_boveda,
        {"clave": "eco2", "nombre": "Economía", "categorias": []},
        actor=actor("analista"))
    series.agregar_pregunta(
        conn_semantica, conn_boveda, "eco2",
        _pregunta_id(conn_semantica, "P1", "Ola 1"), actor=actor("analista"))
    sugerida = series.sugerir(conn_semantica, "eco2", proveedor=proveedor,
                              distancia_maxima=1.0)["sugerencias"][0]
    series.agregar_pregunta(
        conn_semantica, conn_boveda, "eco2", sugerida["pregunta_id"],
        origen="sugerida_aceptada", actor=actor("analista"))

    serie = series.obtener(conn_semantica, "eco2")
    origenes = {p["codigo"]: p["origen"] for p in serie["preguntas"]}
    assert origenes == {"P1": "declarada", "Q7": "sugerida_aceptada"}


def test_una_serie_es_editable_sin_rehacer_las_olas(
        conn_semantica, conn_boveda, serie_economia, actor):
    series.editar(conn_semantica, conn_boveda, "situacion_economica",
                  {"nombre": "Situación económica del país"},
                  actor=actor("analista"))
    series.mapear(
        conn_semantica, conn_boveda, "situacion_economica",
        _pregunta_id(conn_semantica, "Q7", "Ola 2"),
        {"B": "mala"}, actor=actor("analista"))

    serie = series.obtener(conn_semantica, "situacion_economica")
    assert serie["nombre"] == "Situación económica del país"
    q7 = next(p for p in serie["preguntas"] if p["codigo"] == "Q7")
    assert q7["mapeo"]["B"] == "mala"


def test_cada_cambio_de_una_serie_queda_auditado_en_la_boveda(
        conn_semantica, conn_boveda, serie_economia, actor):
    """La serie vive del lado semántico porque es contenido. Quién la editó
    no: eso es una persona, y ninguna persona se escribe de ese lado."""
    series.editar(conn_semantica, conn_boveda, "situacion_economica",
                  {"descripcion": "Serie de referencia"}, actor=actor("analista"))
    rastro = series.auditoria(conn_boveda, "situacion_economica")

    assert [r["accion"] for r in rastro][-1] == "alta"
    assert "pregunta_agregada" in {r["accion"] for r in rastro}
    assert all(r["actor"] == "analista@equipos.com.uy" for r in rastro)

    # Y del lado semántico no hay ninguna columna que nombre a una persona.
    columnas = db.todas(
        conn_semantica,
        "select column_name from information_schema.columns "
        " where left(table_name, 5) = 'serie'")
    assert not {c["column_name"] for c in columnas} & {
        "actor_email", "creada_por", "agregada_por", "email", "nombre_actor"}


def test_una_opcion_no_mapeada_se_informa_en_vez_de_esconderse(
        conn_semantica, conn_boveda, dos_olas, actor):
    series.crear(
        conn_semantica, conn_boveda,
        {"clave": "parcial", "nombre": "Parcial",
         "categorias": [{"clave": "buena", "etiqueta": "Buena"}]},
        actor=actor("analista"))
    serie = series.agregar_pregunta(
        conn_semantica, conn_boveda, "parcial",
        _pregunta_id(conn_semantica, "P1", "Ola 1"),
        mapeo={"1": "buena"}, actor=actor("analista"))
    assert serie["preguntas"][0]["sin_mapear"] == ["2", "3"]


def test_no_se_puede_mapear_a_una_categoria_que_no_existe(
        conn_semantica, conn_boveda, dos_olas, actor):
    series.crear(conn_semantica, conn_boveda,
                 {"clave": "sin_categorias", "nombre": "Sin categorías",
                  "categorias": []},
                 actor=actor("analista"))
    with pytest.raises(DatosInvalidos):
        series.agregar_pregunta(
            conn_semantica, conn_boveda, "sin_categorias",
            _pregunta_id(conn_semantica, "P1", "Ola 1"),
            mapeo={"1": "inventada"}, actor=actor("analista"))


def test_agregar_dos_preguntas_de_la_misma_ola_avisa(
        conn_semantica, conn_boveda, dos_olas, actor):
    """No se bloquea —un cuestionario puede preguntar lo mismo dos veces—,
    pero al comparar olas esa gente se contaría dos veces."""
    series.crear(conn_semantica, conn_boveda,
                 {"clave": "dup", "nombre": "Dup", "categorias": []},
                 actor=actor("analista"))
    series.agregar_pregunta(
        conn_semantica, conn_boveda, "dup",
        _pregunta_id(conn_semantica, "P1", "Ola 1"), actor=actor("analista"))
    salida = series.agregar_pregunta(
        conn_semantica, conn_boveda, "dup",
        _pregunta_id(conn_semantica, "P9", "Ola 1"), actor=actor("analista"))
    assert "dos veces" in salida["aviso"]


# ════════════════════════════════════════════════════════════════════
#  R4.1.c — Vista longitudinal
# ════════════════════════════════════════════════════════════════════

def test_la_linea_de_tiempo_muestra_las_olas_y_las_respuestas(
        conn_boveda, conn_semantica, dos_olas, actor):
    id_persona = dos_olas["gente"]["s-1"]
    linea = longitudinal.de_persona(
        conn_boveda, conn_semantica, id_persona, actor=actor("operaciones"))

    assert [o["encuesta"] for o in linea["olas"]] == ["Ola 1", "Ola 2"]
    assert [o["fecha_campo"] for o in linea["olas"]] == ["2025-03-01",
                                                         "2026-03-01"]
    respuestas = {o["encuesta"]: {r["codigo"]: r["respuesta"]
                                  for r in o["respuestas"]}
                  for o in linea["olas"]}
    assert respuestas["Ola 1"]["P1"] == "Buena"
    assert respuestas["Ola 2"]["Q7"] == "Mala"
    assert linea["aviso"] is None


def test_ver_la_linea_de_tiempo_queda_registrado_como_reidentificacion(
        conn_boveda, conn_semantica, dos_olas, actor):
    """Es justo la operación que deshace la seudonimización: hacerla más
    cómoda sin registrarla sería aflojar el diseño por la puerta de atrás."""
    id_persona = dos_olas["gente"]["s-1"]
    longitudinal.de_persona(conn_boveda, conn_semantica, id_persona,
                            actor=actor("operaciones"))
    fila = db.una(
        conn_boveda,
        "select motivo, contexto, actor_email from reidentificacion "
        " where id_persona = %s order by creado_en desc limit 1",
        (str(id_persona),))
    assert fila is not None
    assert fila["contexto"]["vista"] == "longitudinal"
    assert fila["actor_email"] == "operaciones@equipos.com.uy"


def test_una_persona_de_una_sola_ola_se_muestra_como_tal(
        conn_boveda, conn_semantica, dos_olas, actor):
    """Sin inventar continuidad."""
    linea = longitudinal.de_persona(
        conn_boveda, conn_semantica, dos_olas["gente"]["s-4"],
        actor=actor("operaciones"))
    assert len(linea["olas"]) == 1
    assert "una sola ola" in linea["aviso"]


def test_las_transiciones_muestran_el_movimiento_entre_categorias(
        conn_semantica, serie_economia):
    """s-1 empeora, s-2 mejora, s-3 se queda igual. s-4 no está en la
    segunda ola y por eso no entra en la matriz."""
    matriz = longitudinal.transiciones(conn_semantica, "situacion_economica")

    celdas = {(c["desde"], c["hasta"]): c["personas"] for c in matriz["celdas"]}
    assert celdas[("buena", "mala")] == 1        # s-1
    assert celdas[("mala", "buena")] == 1        # s-2
    assert celdas[("intermedia", "intermedia")] == 1   # s-3
    assert matriz["en_las_dos_olas"] == 3
    assert matriz["estables"] == 1
    assert matriz["se_movieron"] == 2
    assert matriz["desde"]["ola"] == "Ola 1" and matriz["hasta"]["ola"] == "Ola 2"


def test_quien_esta_en_una_sola_ola_no_entra_en_la_matriz(
        conn_semantica, serie_economia):
    """Ponerla en la diagonal diría que no cambió, que es una afirmación que
    nadie hizo."""
    matriz = longitudinal.transiciones(conn_semantica, "situacion_economica")
    assert matriz["solo_en_una"]["solo_al_inicio"] == 1   # s-4
    assert matriz["solo_en_una"]["solo_al_final"] == 0
    assert "diagonal" in matriz["solo_en_una"]["aviso"]


def test_una_serie_de_una_sola_ola_no_puede_mostrar_movimiento(
        conn_semantica, conn_boveda, dos_olas, actor):
    series.crear(conn_semantica, conn_boveda,
                 {"clave": "sola", "nombre": "Sola", "categorias": []},
                 actor=actor("analista"))
    series.agregar_pregunta(
        conn_semantica, conn_boveda, "sola",
        _pregunta_id(conn_semantica, "P1", "Ola 1"), actor=actor("analista"))
    with pytest.raises(DatosInvalidos) as error:
        longitudinal.transiciones(conn_semantica, "sola")
    assert "dos olas" in str(error.value.mensaje)
