"""R1.2 — Dedup de identidad: los cuatro casos del HANDOFF."""

import pytest

from panel_api import db, dedup, personas
from panel_api.errores import Conflicto, DatosInvalidos, NoEncontrado

from conftest import VERSION_TEXTO, consentimientos


def _alta(conn, **persona):
    return personas.alta(
        conn,
        {"persona": persona, "consentimientos": consentimientos("contacto_participacion")},
    )


def test_caso1_documento_exacto_reutiliza_id_persona(conn_boveda):
    primera = _alta(conn_boveda, documento="4.123.456-7", nombre="Ana Pérez", email="ana@ej.uy")
    segunda = _alta(conn_boveda, documento="4.123.456-7", nombre="Ana P.", celular="099111222")

    assert primera["estado"] == "creada"
    assert segunda["estado"] == "reutilizada"
    assert segunda["motivo_dedup"] == "documento"
    assert segunda["id_persona"] == primera["id_persona"]
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1


def test_caso2_email_case_insensitive_reutiliza_id_persona(conn_boveda):
    primera = _alta(conn_boveda, email="Ana@Ejemplo.UY", nombre="Ana Pérez")
    segunda = _alta(conn_boveda, email="ana@ejemplo.uy", nombre="Ana Pérez")

    assert segunda["estado"] == "reutilizada"
    assert segunda["motivo_dedup"] == "email"
    assert segunda["id_persona"] == primera["id_persona"]
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1


def test_caso3_nombre_y_fecha_sin_clave_fuerte_va_a_revision_y_no_fusiona(conn_boveda):
    primera = _alta(conn_boveda, nombre="Juan López", fecha_nacimiento="1985-03-12",
                    localidad="Montevideo")
    segunda = _alta(conn_boveda, nombre="juan lópez", fecha_nacimiento="1985-03-12",
                    localidad="Salto")

    assert segunda["estado"] == "revision"
    assert segunda["motivo"] == "nombre_fecha_nacimiento"
    assert [c["id_persona"] for c in segunda["candidatos"]] == [primera["id_persona"]]
    # No fusionó ni creó: la persona sigue siendo una sola y el alta espera.
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1
    assert db.una(
        conn_boveda, "select count(*)::int as n from alta_en_revision where estado='pendiente'"
    )["n"] == 1


def test_caso4_sin_match_crea_persona_nueva(conn_boveda):
    primera = _alta(conn_boveda, documento="1.111.111-1", nombre="Ana Pérez")
    segunda = _alta(conn_boveda, documento="2.222.222-2", nombre="Beatriz Silva")

    assert segunda["estado"] == "creada"
    assert segunda["id_persona"] != primera["id_persona"]
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 2


def test_documento_gana_sobre_email_cuando_ambos_matchean_distinto(conn_boveda):
    con_documento = _alta(conn_boveda, documento="3.333.333-3", nombre="Carla")
    _alta(conn_boveda, email="carla@ej.uy", nombre="Carla otra")

    resultado = _alta(conn_boveda, documento="3.333.333-3", email="carla@ej.uy", nombre="Carla")

    assert resultado["motivo_dedup"] == "documento"
    assert resultado["id_persona"] == con_documento["id_persona"]


def test_con_documento_o_email_no_hay_revision_aunque_coincida_nombre_y_fecha(conn_boveda):
    _alta(conn_boveda, nombre="Juan López", fecha_nacimiento="1985-03-12")
    resultado = _alta(conn_boveda, nombre="Juan López", fecha_nacimiento="1985-03-12",
                      documento="9.999.999-9")

    # El documento es clave fuerte: es otra persona, no un caso ambiguo.
    assert resultado["estado"] == "creada"


def test_alias_de_origen_reutiliza_sin_pasar_por_heuristicas(conn_boveda):
    primera = personas.alta(
        conn_boveda,
        {
            "persona": {"nombre": "Diego Sosa", "documento": "5.555.555-5"},
            "consentimientos": consentimientos("contacto_participacion"),
            "origen": "dooblo",
            "id_en_origen": "R-0042",
        },
    )
    segunda = personas.alta(
        conn_boveda,
        {
            "persona": {"nombre": "D. Sosa"},
            "consentimientos": consentimientos("contacto_participacion"),
            "origen": "dooblo",
            "id_en_origen": "R-0042",
        },
    )

    assert segunda["motivo_dedup"] == "alias_origen"
    assert segunda["id_persona"] == primera["id_persona"]
    assert dedup.buscar_por_alias(conn_boveda, "dooblo", "R-0042") is not None


def test_reutilizar_completa_faltantes_pero_no_pisa_lo_que_ya_estaba(conn_boveda):
    primera = _alta(conn_boveda, documento="6.666.666-6", nombre="Elena Rodríguez")
    _alta(conn_boveda, documento="6.666.666-6", nombre="OTRO NOMBRE", celular="099888777")

    fila = db.una(
        conn_boveda,
        "select nombre, celular from persona where id_persona = %s",
        (primera["id_persona"],),
    )
    assert fila["nombre"] == "Elena Rodríguez"   # no se pisa
    assert fila["celular"] == "099888777"        # sí se completa


def test_alta_sin_dato_identificatorio_se_rechaza(conn_boveda):
    with pytest.raises(DatosInvalidos):
        personas.alta(
            conn_boveda,
            {"persona": {"localidad": "Durazno"},
             "consentimientos": consentimientos("contacto_participacion")},
        )


def test_la_ficha_muestra_los_alias_de_origen(conn_boveda):
    # El alta guarda el alias y la ingesta lo necesita para enganchar las
    # respuestas: hay que poder verlo en la ficha para verificarlo.
    resultado = personas.alta(
        conn_boveda,
        {
            "persona": {"documento": "7-7", "nombre": "Fabiana Rocha"},
            "consentimientos": consentimientos("contacto_participacion"),
            "origen": "dooblo",
            "id_en_origen": "R-0042",
        },
    )
    ficha = personas.ficha(conn_boveda, resultado["id_persona"])
    assert ficha["alias"] == [{"origen": "dooblo", "id_en_origen": "R-0042"}]


def test_la_ficha_lista_un_alias_por_plataforma(conn_boveda):
    resultado = personas.alta(
        conn_boveda,
        {
            "persona": {"documento": "8-8", "nombre": "Gonzalo Vera"},
            "consentimientos": consentimientos("contacto_participacion"),
            "origen": "dooblo", "id_en_origen": "R-100",
        },
    )
    # La misma persona, enrolada después desde otra plataforma.
    personas.alta(
        conn_boveda,
        {
            "persona": {"documento": "8-8"},
            "consentimientos": consentimientos("contacto_participacion"),
            "origen": "alchemer", "id_en_origen": "A-900",
        },
    )
    ficha = personas.ficha(conn_boveda, resultado["id_persona"])
    assert ficha["alias"] == [
        {"origen": "alchemer", "id_en_origen": "A-900"},
        {"origen": "dooblo", "id_en_origen": "R-100"},
    ]


def test_la_ficha_de_quien_no_tiene_alias_devuelve_lista_vacia(conn_boveda):
    resultado = personas.alta(
        conn_boveda,
        {
            "persona": {"documento": "9-9", "nombre": "Sin Origen"},
            "consentimientos": consentimientos("contacto_participacion"),
        },
    )
    assert personas.ficha(conn_boveda, resultado["id_persona"])["alias"] == []


# ── Edición de una persona ya enrolada ───────────────────────────────

def _enrolar(conn, **campos):
    return personas.alta(
        conn,
        {"persona": campos, "consentimientos": consentimientos("contacto_participacion")},
    )["id_persona"]


def test_editar_cambia_solo_los_campos_que_vienen(conn_boveda):
    id_persona = _enrolar(
        conn_boveda, documento="1-1", nombre="Ana Peres", email="ana@ej.uy",
        localidad="Montevideo",
    )

    resultado = personas.editar(conn_boveda, id_persona, {"nombre": "Ana Pérez"})

    assert resultado["campos_modificados"] == ["nombre"]
    ficha = personas.ficha(conn_boveda, id_persona)["persona"]
    assert ficha["nombre"] == "Ana Pérez"
    assert ficha["email"] == "ana@ej.uy"          # intacto
    assert ficha["localidad"] == "Montevideo"     # intacto


def test_editar_con_valor_vacio_borra_el_dato(conn_boveda):
    # Es la forma de sacar un dato mal cargado.
    id_persona = _enrolar(conn_boveda, documento="2-2", nombre="Beto",
                          celular="099 000 000")

    personas.editar(conn_boveda, id_persona, {"celular": "  "})

    assert personas.ficha(conn_boveda, id_persona)["persona"]["celular"] is None


def test_no_se_puede_editar_el_documento_desde_la_ficha(conn_boveda):
    # Es clave de dedup y tiene índice único: cambiarla no es corregir un
    # dato, es cambiar la identidad con la que el sistema reconoce a la
    # persona.
    id_persona = _enrolar(conn_boveda, documento="3-3", nombre="Carla")

    with pytest.raises(DatosInvalidos) as excepcion:
        personas.editar(conn_boveda, id_persona, {"documento": "3-4"})

    assert excepcion.value.detalle["campos"] == ["documento"]
    assert "documento" not in excepcion.value.detalle["editables"]
    assert personas.ficha(conn_boveda, id_persona)["persona"]["documento"] == "3-3"


def test_no_se_puede_editar_el_email_desde_la_ficha(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="4-4", email="dora@ej.uy")

    with pytest.raises(DatosInvalidos) as excepcion:
        personas.editar(conn_boveda, id_persona, {"email": "otra@ej.uy"})

    assert excepcion.value.detalle["campos"] == ["email"]
    assert personas.ficha(conn_boveda, id_persona)["persona"]["email"] == "dora@ej.uy"


def test_un_cambio_mixto_se_rechaza_entero_sin_escribir_nada(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="5-5", nombre="Elena")

    with pytest.raises(DatosInvalidos):
        personas.editar(
            conn_boveda, id_persona, {"nombre": "Elena Rodríguez", "email": "e@ej.uy"}
        )

    # El nombre no se tocó: o entra todo, o no entra nada.
    assert personas.ficha(conn_boveda, id_persona)["persona"]["nombre"] == "Elena"


def test_reescribir_el_mismo_valor_no_cuenta_como_cambio(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="7-7", localidad="Salto")
    resultado = personas.editar(conn_boveda, id_persona, {"localidad": "Salto"})
    assert resultado["campos_modificados"] == []


def test_editar_un_campo_que_no_existe_se_rechaza(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="8-8")
    with pytest.raises(DatosInvalidos) as excepcion:
        personas.editar(conn_boveda, id_persona, {"puntos": 500})
    assert excepcion.value.detalle["campos"] == ["puntos"]


def test_editar_una_persona_inexistente_da_404(conn_boveda):
    with pytest.raises(NoEncontrado):
        personas.editar(
            conn_boveda, "3f5b8a1e-0000-4000-8000-000000000099", {"nombre": "X"}
        )


def test_editar_no_toca_consentimientos_ni_membresias(conn_boveda):
    from panel_api import paneles as mod_paneles

    panel = mod_paneles.crear(conn_boveda, "Panel")
    id_persona = _enrolar(conn_boveda, documento="9-9", nombre="Hugo")
    mod_paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    personas.editar(conn_boveda, id_persona, {"nombre": "Hugo Ramírez"})

    ficha = personas.ficha(conn_boveda, id_persona)
    assert len(ficha["consentimientos"]) == 1
    assert [p["panel_id"] for p in ficha["paneles"]] == [panel["id"]]


# ── Alias de origen ──────────────────────────────────────────────────

def test_agregar_un_alias_despues_del_alta(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="10-10", nombre="Irene")

    personas.agregar_alias(conn_boveda, id_persona, "dooblo", "R-500")

    assert personas.ficha(conn_boveda, id_persona)["alias"] == [
        {"origen": "dooblo", "id_en_origen": "R-500"}
    ]


def test_un_id_de_plataforma_no_puede_apuntar_a_dos_personas(conn_boveda):
    # Si pudiera, la ingesta no sabría a quién asignarle la respuesta.
    uno = _enrolar(conn_boveda, documento="11-11")
    dos = _enrolar(conn_boveda, documento="12-12")
    personas.agregar_alias(conn_boveda, uno, "dooblo", "R-600")

    with pytest.raises(Conflicto) as excepcion:
        personas.agregar_alias(conn_boveda, dos, "dooblo", "R-600")
    assert excepcion.value.detalle["id_persona_en_conflicto"] == uno


def test_agregar_el_mismo_alias_dos_veces_a_la_misma_persona_es_idempotente(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="13-13")
    personas.agregar_alias(conn_boveda, id_persona, "dooblo", "R-700")
    personas.agregar_alias(conn_boveda, id_persona, "dooblo", "R-700")
    assert len(personas.ficha(conn_boveda, id_persona)["alias"]) == 1


def test_quitar_un_alias_mal_cargado(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="14-14")
    personas.agregar_alias(conn_boveda, id_persona, "dooblo", "R-800")

    personas.quitar_alias(conn_boveda, id_persona, "dooblo", "R-800")

    assert personas.ficha(conn_boveda, id_persona)["alias"] == []


def test_quitar_un_alias_que_no_tiene_da_404(conn_boveda):
    id_persona = _enrolar(conn_boveda, documento="15-15")
    with pytest.raises(NoEncontrado):
        personas.quitar_alias(conn_boveda, id_persona, "dooblo", "R-999")
