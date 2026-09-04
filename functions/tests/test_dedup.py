"""R1.2 — Dedup de identidad: los cuatro casos del HANDOFF."""

import pytest

from panel_api import db, dedup, personas
from panel_api.errores import DatosInvalidos

from conftest import VERSION_TEXTO, consentimientos


def _alta(conn, **persona):
    return personas.alta(
        conn,
        {"persona": persona, "consentimientos": consentimientos("contacto_participacion")},
    )


def test_caso1_documento_exacto_reutiliza_id_persona(local):
    primera = _alta(local, documento="4.123.456-7", nombre="Ana Pérez", email="ana@ej.uy")
    segunda = _alta(local, documento="4.123.456-7", nombre="Ana P.", celular="099111222")

    assert primera["estado"] == "creada"
    assert segunda["estado"] == "reutilizada"
    assert segunda["motivo_dedup"] == "documento"
    assert segunda["id_persona"] == primera["id_persona"]
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 1


def test_caso2_email_case_insensitive_reutiliza_id_persona(local):
    primera = _alta(local, email="Ana@Ejemplo.UY", nombre="Ana Pérez")
    segunda = _alta(local, email="ana@ejemplo.uy", nombre="Ana Pérez")

    assert segunda["estado"] == "reutilizada"
    assert segunda["motivo_dedup"] == "email"
    assert segunda["id_persona"] == primera["id_persona"]
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 1


def test_caso3_nombre_y_fecha_sin_clave_fuerte_va_a_revision_y_no_fusiona(local):
    primera = _alta(local, nombre="Juan López", fecha_nacimiento="1985-03-12",
                    localidad="Montevideo")
    segunda = _alta(local, nombre="juan lópez", fecha_nacimiento="1985-03-12",
                    localidad="Salto")

    assert segunda["estado"] == "revision"
    assert segunda["motivo"] == "nombre_fecha_nacimiento"
    assert [c["id_persona"] for c in segunda["candidatos"]] == [primera["id_persona"]]
    # No fusionó ni creó: la persona sigue siendo una sola y el alta espera.
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 1
    assert db.una(
        local, "select count(*)::int as n from alta_en_revision where estado='pendiente'"
    )["n"] == 1


def test_caso4_sin_match_crea_persona_nueva(local):
    primera = _alta(local, documento="1.111.111-1", nombre="Ana Pérez")
    segunda = _alta(local, documento="2.222.222-2", nombre="Beatriz Silva")

    assert segunda["estado"] == "creada"
    assert segunda["id_persona"] != primera["id_persona"]
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 2


def test_documento_gana_sobre_email_cuando_ambos_matchean_distinto(local):
    con_documento = _alta(local, documento="3.333.333-3", nombre="Carla")
    _alta(local, email="carla@ej.uy", nombre="Carla otra")

    resultado = _alta(local, documento="3.333.333-3", email="carla@ej.uy", nombre="Carla")

    assert resultado["motivo_dedup"] == "documento"
    assert resultado["id_persona"] == con_documento["id_persona"]


def test_con_documento_o_email_no_hay_revision_aunque_coincida_nombre_y_fecha(local):
    _alta(local, nombre="Juan López", fecha_nacimiento="1985-03-12")
    resultado = _alta(local, nombre="Juan López", fecha_nacimiento="1985-03-12",
                      documento="9.999.999-9")

    # El documento es clave fuerte: es otra persona, no un caso ambiguo.
    assert resultado["estado"] == "creada"


def test_alias_de_origen_reutiliza_sin_pasar_por_heuristicas(local):
    primera = personas.alta(
        local,
        {
            "persona": {"nombre": "Diego Sosa", "documento": "5.555.555-5"},
            "consentimientos": consentimientos("contacto_participacion"),
            "origen": "dooblo",
            "id_en_origen": "R-0042",
        },
    )
    segunda = personas.alta(
        local,
        {
            "persona": {"nombre": "D. Sosa"},
            "consentimientos": consentimientos("contacto_participacion"),
            "origen": "dooblo",
            "id_en_origen": "R-0042",
        },
    )

    assert segunda["motivo_dedup"] == "alias_origen"
    assert segunda["id_persona"] == primera["id_persona"]
    assert dedup.buscar_por_alias(local, "dooblo", "R-0042") is not None


def test_reutilizar_completa_faltantes_pero_no_pisa_lo_que_ya_estaba(local):
    primera = _alta(local, documento="6.666.666-6", nombre="Elena Rodríguez")
    _alta(local, documento="6.666.666-6", nombre="OTRO NOMBRE", celular="099888777")

    fila = db.una(
        local,
        "select nombre, celular from persona where id_persona = %s",
        (primera["id_persona"],),
    )
    assert fila["nombre"] == "Elena Rodríguez"   # no se pisa
    assert fila["celular"] == "099888777"        # sí se completa


def test_alta_sin_dato_identificatorio_se_rechaza(local):
    with pytest.raises(DatosInvalidos):
        personas.alta(
            local,
            {"persona": {"localidad": "Durazno"},
             "consentimientos": consentimientos("contacto_participacion")},
        )
