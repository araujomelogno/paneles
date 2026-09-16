"""Addendum a R3.9 — Membresía y participación al ingestar.

Antes, convocatoria e ingesta estaban desacopladas: convocar creaba
participaciones, ingestar escribía respuestas mapeando por `alias_origen`, y
nada las unía. Quien respondió en campo sin haber sido convocado desde el
sistema quedaba sin membresía —invisible para el muestreo, la composición y
la cuota— y sin fila en `participacion` —la ola mostraba menos respuestas de
las que hubo—.

Corre contra los dos stores reales.
"""

import pytest

from panel_api import (
    consentimiento, db, encuestas, muestreo, paneles, participacion, personas)

from conftest import consentimientos

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Whisky"}, "orden": 1},
]

AMBAS = ("contacto_participacion", "uso_semantico")


def _panelista(conn, documento, id_en_origen, finalidades=AMBAS):
    return personas.alta(
        conn,
        {
            "persona": {"documento": documento, "nombre": f"Panelista {documento}"},
            "consentimientos": consentimientos(*finalidades),
            "origen": "dooblo",
            "id_en_origen": id_en_origen,
        },
    )["id_persona"]


@pytest.fixture
def escenario(conn_boveda):
    """Un panel con una encuesta, un miembro convocado y un ajeno.

    `convocada` es miembro del panel y el sistema la convocó. `ajena` existe
    en la bóveda con su alias de campo, pero no es miembro de este panel ni
    fue convocada: es la que respondió en terreno sin pasar por el sistema.
    """
    panel = paneles.crear(conn_boveda, "Panel bebidas")
    convocada = _panelista(conn_boveda, "1-1", "R-001")
    ajena = _panelista(conn_boveda, "2-2", "R-002")
    paneles.agregar_miembro(conn_boveda, panel["id"], convocada)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1", "2026-03-01")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    return {"panel": panel, "encuesta": encuesta,
            "convocada": convocada, "ajena": ajena}


def _ingestar(ctx, escenario, filas, proveedor=None):
    return encuestas.ingestar(
        ctx.boveda, ctx.semantica, escenario["encuesta"]["id"], PREGUNTAS, filas,
        columna_id="id_en_origen", origen="dooblo",
        proveedor=proveedor or ctx.embeddings,
    )


def _membresia(conn, panel_id, id_persona):
    return db.una(
        conn,
        "select estado from membresia where panel_id = %s and id_persona = %s",
        (panel_id, id_persona),
    )


def _participacion(conn, encuesta_id, id_persona):
    return db.una(
        conn,
        "select origen, respondio, respondio_en from participacion "
        " where encuesta_id = %s and id_persona = %s",
        (encuesta_id, id_persona),
    )


FILAS = [
    {"id_en_origen": "R-001", "P1": "1"},
    {"id_en_origen": "R-002", "P1": "2"},
]


# ── R3.9.a · Membresía ───────────────────────────────────────────────

def test_quien_respondio_sin_ser_miembro_queda_dado_de_alta_en_el_panel(
    ctx, escenario
):
    """El agujero central: existía en la bóveda pero era invisible para todo
    lo que se hace con un panel."""
    assert _membresia(ctx.boveda, escenario["panel"]["id"], escenario["ajena"]) is None

    resultado = _ingestar(ctx, escenario, FILAS)

    assert _membresia(
        ctx.boveda, escenario["panel"]["id"], escenario["ajena"])["estado"] == "activo"
    assert resultado["membresias_nuevas"] == 1
    assert resultado["membresias_existentes"] == 1


def test_el_alta_automatica_la_hace_visible_para_convocar_al_panel(ctx, escenario):
    """La membresía no es decorativa: `todo_el_panel` es lo que la usa."""
    _ingestar(ctx, escenario, FILAS)

    otra = encuestas.crear(ctx.boveda, escenario["panel"]["id"], "Ola 2")
    convocatoria = encuestas.convocar(ctx.boveda, otra["id"], todo_el_panel=True)

    assert convocatoria["convocados_total"] == 2


def test_una_membresia_dada_de_baja_no_se_reactiva_por_una_ingesta(ctx, escenario):
    """Una baja fue una decisión explícita y la ingesta no la revierte de
    costado. Se informa para que alguien decida."""
    paneles.agregar_miembro(ctx.boveda, escenario["panel"]["id"], escenario["ajena"])
    paneles.dar_de_baja_miembro(
        ctx.boveda, escenario["panel"]["id"], escenario["ajena"])

    resultado = _ingestar(ctx, escenario, FILAS)

    assert _membresia(
        ctx.boveda, escenario["panel"]["id"], escenario["ajena"])["estado"] == "baja"
    assert resultado["membresias_en_baja"] == [escenario["ajena"]]
    assert resultado["membresias_nuevas"] == 0


# ── R3.9.b · Participación ───────────────────────────────────────────

def test_quien_nunca_fue_convocado_igual_queda_registrado_como_que_respondio(
    ctx, escenario
):
    assert _participacion(
        ctx.boveda, escenario["encuesta"]["id"], escenario["ajena"]) is None

    resultado = _ingestar(ctx, escenario, FILAS)

    fila = _participacion(
        ctx.boveda, escenario["encuesta"]["id"], escenario["ajena"])
    assert fila["origen"] == "importacion"
    assert fila["respondio"] is True
    assert fila["respondio_en"] is not None
    assert resultado["participaciones_nuevas"] == 1


def test_a_quien_convoco_el_sistema_se_le_actualiza_la_fila_que_ya_tenia(
    ctx, escenario
):
    """Unicidad `(encuesta_id, id_persona)`: no se crea una segunda."""
    resultado = _ingestar(ctx, escenario, FILAS)

    fila = _participacion(
        ctx.boveda, escenario["encuesta"]["id"], escenario["convocada"])
    assert fila["origen"] == "convocatoria", "no se le pisa el origen"
    assert fila["respondio"] is True
    assert resultado["participaciones_actualizadas"] == 1

    cuantas = db.una(
        ctx.boveda,
        "select count(*)::int as n from participacion "
        " where encuesta_id = %s and id_persona = %s",
        (escenario["encuesta"]["id"], escenario["convocada"]),
    )
    assert cuantas["n"] == 1


def test_la_participacion_importada_nace_pendiente_de_evaluar(ctx, escenario):
    """La calidad es de R3.2, no de este addendum."""
    _ingestar(ctx, escenario, FILAS)

    fila = db.una(
        ctx.boveda,
        "select calidad_estado from participacion "
        " where encuesta_id = %s and id_persona = %s",
        (escenario["encuesta"]["id"], escenario["ajena"]),
    )
    assert fila["calidad_estado"] == "pendiente"


def test_la_tasa_de_respuesta_cuenta_a_quien_no_fue_convocado(ctx, escenario):
    """El síntoma que se veía en la pantalla: la ola mostraba menos
    respuestas de las que realmente hubo."""
    _ingestar(ctx, escenario, FILAS)

    ola = next(e for e in encuestas.listar(ctx.boveda)
               if e["id"] == escenario["encuesta"]["id"])
    assert ola["convocados"] == 2
    assert ola["respondieron"] == 2


# ── R3.9.c · Idempotencia ────────────────────────────────────────────

def test_reingestar_el_mismo_archivo_no_duplica_nada(ctx, escenario):
    _ingestar(ctx, escenario, FILAS)
    primera = _participacion(
        ctx.boveda, escenario["encuesta"]["id"], escenario["ajena"])

    segunda_corrida = _ingestar(ctx, escenario, FILAS)

    filas = db.una(
        ctx.boveda,
        "select count(*)::int as n from participacion where encuesta_id = %s",
        (escenario["encuesta"]["id"],),
    )
    membresias = db.una(
        ctx.boveda,
        "select count(*)::int as n from membresia where panel_id = %s",
        (escenario["panel"]["id"],),
    )
    assert filas["n"] == 2
    assert membresias["n"] == 2
    assert segunda_corrida["membresias_nuevas"] == 0
    assert segunda_corrida["membresias_existentes"] == 2
    assert segunda_corrida["participaciones_nuevas"] == 0
    assert segunda_corrida["participaciones_actualizadas"] == 2

    segunda = _participacion(
        ctx.boveda, escenario["encuesta"]["id"], escenario["ajena"])
    assert segunda["respondio_en"] == primera["respondio_en"], (
        "una re-ingesta no debería mover la fecha de la primera respuesta")


# ── El gate de consentimiento ────────────────────────────────────────

def test_convocar_sigue_exigiendo_consentimiento_de_contacto(ctx, escenario):
    """No regresión. La membresía y la participación que crea la ingesta no
    habilitan a nadie a ser contactado: ese gate es de `convocar()` y no se
    tocó."""
    _ingestar(ctx, escenario, FILAS)
    consentimiento.marcar_retirado(
        ctx.boveda, escenario["ajena"], consentimiento.CONTACTO)

    otra = encuestas.crear(ctx.boveda, escenario["panel"]["id"], "Ola 3")
    convocatoria = encuestas.convocar(ctx.boveda, otra["id"], todo_el_panel=True)

    assert escenario["ajena"] in convocatoria["sin_consentimiento"]
    assert convocatoria["convocados_total"] == 1
    assert _participacion(ctx.boveda, otra["id"], escenario["ajena"]) is None


def test_sin_contacto_vigente_igual_se_registra_lo_que_ya_paso(ctx):
    """Registrar una respuesta no es contactar a nadie.

    El caso de «ya existen» con panelistas viejos: alguien que retiró el
    consentimiento de contacto pero respondió en campo antes. Bloquear el
    registro no protege a nadie y sí distorsiona la tasa de respuesta.
    """
    panel = paneles.crear(ctx.boveda, "Panel viejo")
    persona = _panelista(ctx.boveda, "9-9", "R-009")
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola vieja")
    consentimiento.marcar_retirado(ctx.boveda, persona, consentimiento.CONTACTO)

    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"], PREGUNTAS,
        [{"id_en_origen": "R-009", "P1": "1"}],
        columna_id="id_en_origen", origen="dooblo", proveedor=ctx.embeddings,
    )

    assert resultado["participaciones_nuevas"] == 1
    assert resultado["membresias_nuevas"] == 1
    assert _participacion(ctx.boveda, encuesta["id"], persona)["respondio"] is True


def test_quien_no_tiene_uso_semantico_no_entra_por_esta_puerta(ctx):
    """Sus respuestas no se ingestan, así que no hay hecho que registrar.

    Es la decisión del addendum: la membresía y la participación acompañan a
    lo que efectivamente se ingestó.
    """
    panel = paneles.crear(ctx.boveda, "Panel sin semántico")
    persona = _panelista(
        ctx.boveda, "8-8", "R-008", finalidades=("contacto_participacion",))
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola")

    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"], PREGUNTAS,
        [{"id_en_origen": "R-008", "P1": "1"}],
        columna_id="id_en_origen", origen="dooblo", proveedor=ctx.embeddings,
    )

    assert persona in resultado["sin_consentimiento"]
    assert resultado["membresias_nuevas"] == 0
    assert resultado["participaciones_nuevas"] == 0
    assert _membresia(ctx.boveda, panel["id"], persona) is None


# ── El mapeo que lo hacía imposible ──────────────────────────────────

def test_un_convocado_con_alias_ya_no_tapa_a_los_que_vienen_del_archivo(
    ctx, escenario
):
    """El bug que bloqueaba todo el addendum.

    `ingestar` armaba el mapa con las participaciones y, si salía no vacío,
    la ingesta ya no miraba `alias_origen`. Con un solo convocado con alias
    —o sea, siempre— todo el que respondió sin haber sido convocado caía en
    `sin_mapear` y no se ingestaba nada suyo.
    """
    resultado = _ingestar(ctx, escenario, FILAS)

    assert resultado["sin_mapear"] == []
    assert escenario["ajena"] in resultado["ids_persona_ingestados"]
    assert resultado["personas"] == 2


# ── La fatiga no es una respuesta ────────────────────────────────────

def test_una_participacion_importada_no_cuenta_como_convocatoria_para_la_fatiga(
    ctx, escenario
):
    """Consecuencia del addendum que hay que contener.

    La fatiga mide cuánto se molestó a alguien. Si una participación deducida
    de un archivo contara como convocatoria, la persona quedaría fuera del
    muestreo por contactos que nunca ocurrieron.
    """
    _ingestar(ctx, escenario, FILAS)

    otra = encuestas.crear(ctx.boveda, escenario["panel"]["id"], "Ola 4")
    propuesta = muestreo.proponer(ctx.boveda, otra["id"], cantidad=10)

    propuestos = {p["id_persona"] for p in propuesta["propuesta"]}
    assert escenario["ajena"] in propuestos, (
        "respondió en campo, pero nadie la convocó: no tiene fatiga")


def test_el_tablero_no_le_inventa_un_contacto_a_quien_respondio_en_campo(
    ctx, escenario
):
    _ingestar(ctx, escenario, FILAS)

    tablero = participacion.tablero(ctx.boveda, escenario["panel"]["id"])

    # Se la cuenta como miembro y como alguien que respondió, pero no como
    # alguien a quien se haya convocado.
    assert tablero["miembros"] == 2
    assert tablero["respuesta"]["convocatorias_emitidas"] == 1
    assert tablero["respuesta"]["miembros_que_respondieron_alguna"] == 2
    assert tablero["respuesta"]["miembros_nunca_convocados"] == 1


def test_la_ficha_cuenta_la_respuesta_sin_contar_un_contacto(ctx, escenario):
    _ingestar(ctx, escenario, FILAS)

    ficha = personas.ficha(ctx.boveda, escenario["ajena"])
    assert ficha["participacion"]["convocatorias"] == 0
    assert ficha["participacion"]["respondidas"] == 1
