"""R3.12 — Identificación del respondente en el trabajo de campo.

El mapeo se apoyaba siempre en `alias_origen`, o sea en el id que la
plataforma de campo le puso al respondente. Eso asume que ese id es estable
por persona entre estudios, y no lo es: cada encuesta genera ids nuevos para
el mismo individuo. Al ingestar un estudio nuevo no matcheaba ninguna fila y
todas caían en `sin_mapear`, con la ingesta en cero y nada roto.

La salida es que el identificador del sistema viaje **hacia** el campo en vez
de adivinarlo a la vuelta.

Corre contra los dos stores reales.
"""

import pytest

from panel_api import db, encuestas, ingesta, paneles, personas, ruteo
from panel_api.errores import DatosInvalidos, SinPermiso

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Whisky"}, "orden": 1},
]


def _panelista(conn, documento, id_en_origen=None, email=None):
    cuerpo = {
        "persona": {"documento": documento, "nombre": f"Panelista {documento}",
                    **({"email": email} if email else {})},
        "consentimientos": consentimientos(*AMBAS),
    }
    if id_en_origen:
        cuerpo.update({"origen": "dooblo", "id_en_origen": id_en_origen})
    return personas.alta(conn, cuerpo)["id_persona"]


@pytest.fixture
def ola(conn_boveda):
    """Una ola convocada. `sin_alias` no tiene alias de plataforma: es el
    caso de un panelista al que nunca se le cargó el id de campo."""
    panel = paneles.crear(conn_boveda, "Panel bebidas")
    con_alias = _panelista(conn_boveda, "1-1", "R-001", "ana@x.uy")
    sin_alias = _panelista(conn_boveda, "2-2", None, "beto@x.uy")
    for id_persona in (con_alias, sin_alias):
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1", "2026-03-01")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    return {"panel": panel, "encuesta": encuesta,
            "con_alias": con_alias, "sin_alias": sin_alias}


def _ingestar(ctx, ola, filas, **extra):
    return encuestas.ingestar(
        ctx.boveda, ctx.semantica, ola["encuesta"]["id"], PREGUNTAS, filas,
        columna_id="ID", proveedor=ctx.embeddings, **extra)


# ── R3.12.a · Exportar la muestra ────────────────────────────────────

def test_la_muestra_seudonima_trae_solo_el_id_persona(conn_boveda, ola):
    """El archivo que va a campo no necesita PII: con el `id_persona`
    precargado, el mapeo a la vuelta es directo."""
    muestra = encuestas.exportar_muestra(conn_boveda, ola["encuesta"]["id"])

    assert muestra["columnas"] == ["id_persona"]
    assert muestra["personas"] == 2
    assert muestra["contiene_datos_personales"] is False
    assert ola["con_alias"] in muestra["csv"]
    for pii in ("Panelista", "ana@x.uy", "1-1"):
        assert pii not in muestra["csv"], f"«{pii}» no tiene por qué ir a campo"


def test_la_muestra_con_contacto_trae_los_datos_y_se_marca(conn_boveda, ola):
    muestra = encuestas.exportar_muestra(
        conn_boveda, ola["encuesta"]["id"], con_contacto=True)

    assert muestra["contiene_datos_personales"] is True
    assert "ATENCIÓN" in muestra["csv"].splitlines()[0]
    assert "CON-DATOS-PERSONALES" in muestra["nombre_archivo"]
    assert "ana@x.uy" in muestra["csv"]
    # Sin fecha de nacimiento exacta ni observaciones, como R3.10.
    assert "fecha_nacimiento" not in muestra["columnas"]
    assert "observaciones" not in muestra["columnas"]


def test_exportar_con_contacto_queda_registrado(ctx, actor, ola):
    _, respuesta = ruteo.despachar(
        "GET", f"/encuestas/{ola['encuesta']['id']}/muestra", {},
        {"con_contacto": "1"}, actor("operaciones"), ctx)

    assert respuesta["contiene_datos_personales"] is True
    registros = db.todas(
        ctx.boveda, "select id_persona, motivo, actor_uid from reidentificacion")
    assert len(registros) == 2
    assert {r["motivo"] for r in registros} == {"exportacion"}


def test_exportar_la_muestra_seudonima_no_registra_nada(ctx, actor, ola):
    """No es una reidentificación: no hay PII que auditar."""
    ruteo.despachar(
        "GET", f"/encuestas/{ola['encuesta']['id']}/muestra", {}, {},
        actor("operaciones"), ctx)

    assert db.una(
        ctx.boveda, "select count(*)::int as n from reidentificacion")["n"] == 0


def test_la_muestra_con_contacto_exige_el_permiso(ctx, actor, ola):
    """El analista puede ver la muestra seudónima y no la que lleva PII."""
    ruteo.despachar(
        "GET", f"/encuestas/{ola['encuesta']['id']}/muestra", {}, {},
        actor("analista"), ctx)

    with pytest.raises(SinPermiso):
        ruteo.despachar(
            "GET", f"/encuestas/{ola['encuesta']['id']}/muestra", {},
            {"con_contacto": "1"}, actor("analista"), ctx)


# ── R3.12.b · Tipo de identificador declarado ────────────────────────

def test_declarando_id_persona_el_mapeo_es_directo(ctx, ola):
    """El caso que R3.12 viene a habilitar: la muestra se precargó, el
    archivo vuelve con el `id_persona` y no hace falta ningún alias."""
    resultado = _ingestar(
        ctx, ola,
        [{"ID": ola["sin_alias"], "P1": "1"}],
        tipo_identificador="id_persona")

    assert resultado["sin_mapear"] == []
    assert resultado["tipo_identificador"] == "id_persona"
    assert ola["sin_alias"] in resultado["ids_persona_ingestados"]


def test_por_id_persona_no_se_consulta_alias_origen(ctx, ola, monkeypatch):
    """No es una optimización: si cayera al alias, el tipo declarado no
    estaría mandando y volveríamos al supuesto que falla."""
    def explotar(*a, **k):
        raise AssertionError("no tendría que mirar alias_origen")

    monkeypatch.setattr(ingesta, "mapear_a_id_persona", explotar)

    resultado = _ingestar(
        ctx, ola, [{"ID": ola["con_alias"], "P1": "1"}],
        tipo_identificador="id_persona", origen="dooblo")

    assert resultado["sin_mapear"] == []


def test_un_uuid_mal_formado_y_uno_inexistente_se_distinguen(ctx, ola):
    """Un typo y una persona borrada por baja se arreglan distinto."""
    resultado = _ingestar(
        ctx, ola,
        [{"ID": "no-soy-un-uuid", "P1": "1"},
         {"ID": "00000000-0000-4000-8000-000000000000", "P1": "1"},
         {"ID": ola["con_alias"], "P1": "2"}],
        tipo_identificador="id_persona")

    motivos = {d["id_en_origen"]: d["motivo"]
               for d in resultado["sin_mapear_detalle"]}
    assert motivos == {
        "no-soy-un-uuid": "formato_invalido",
        "00000000-0000-4000-8000-000000000000": "no_encontrado",
    }
    assert sorted(resultado["sin_mapear"]) == sorted(motivos)


def test_un_uuid_mal_formado_no_voltea_la_ingesta(ctx, ola):
    """Postgres aborta la transacción entera ante un uuid inválido. Si el
    filtro por formato no corriera antes de consultar, una fila con un typo
    se llevaría puesta toda la carga."""
    resultado = _ingestar(
        ctx, ola,
        [{"ID": "???", "P1": "1"}, {"ID": ola["con_alias"], "P1": "2"}],
        tipo_identificador="id_persona")

    assert resultado["respuestas_escritas"] == 1, "la fila buena se ingesta igual"


def test_se_puede_mapear_por_documento(ctx, ola):
    resultado = _ingestar(
        ctx, ola, [{"ID": "2-2", "P1": "1"}],
        tipo_identificador="documento", origen="alchemer")

    assert resultado["sin_mapear"] == []
    assert ola["sin_alias"] in resultado["ids_persona_ingestados"]


def test_se_puede_mapear_por_email_sin_distinguir_mayusculas(ctx, ola):
    resultado = _ingestar(
        ctx, ola, [{"ID": "  BETO@X.UY ", "P1": "1"}],
        tipo_identificador="email", origen="alchemer")

    assert resultado["sin_mapear"] == []
    assert ola["sin_alias"] in resultado["ids_persona_ingestados"]


def test_sin_declarar_tipo_se_comporta_como_alias(ctx, ola):
    """No regresión: las cargas existentes siguen andando sin tocar nada."""
    resultado = _ingestar(
        ctx, ola, [{"ID": "R-001", "P1": "1"}], origen="dooblo")

    assert resultado["tipo_identificador"] == "alias"
    assert resultado["sin_mapear"] == []
    assert ola["con_alias"] in resultado["ids_persona_ingestados"]


def test_por_alias_el_motivo_dice_que_falta_el_alias(ctx, ola):
    resultado = _ingestar(
        ctx, ola, [{"ID": "R-999", "P1": "1"}], origen="dooblo")

    assert resultado["sin_mapear_detalle"] == [
        {"id_en_origen": "R-999", "motivo": "sin_alias_para_ese_origen"}]


def test_un_tipo_desconocido_se_rechaza():
    with pytest.raises(DatosInvalidos, match="no es un tipo de identificador"):
        ingesta.resolver_identificadores(None, "cedula", ["1"])


def test_por_alias_sin_origen_se_rechaza():
    with pytest.raises(DatosInvalidos, match="declarar el origen"):
        ingesta.resolver_identificadores(None, "alias", ["R-1"])


# ── R3.12.c · Registro automático del alias ──────────────────────────

def test_cargar_por_documento_deja_sembrado_el_alias(ctx, ola):
    """La segunda vuelta del mismo estudio ya no necesita la llave natural,
    y el archivo de campo deja de necesitar PII."""
    resultado = _ingestar(
        ctx, ola, [{"ID": "2-2", "P1": "1"}],
        tipo_identificador="documento", origen="alchemer")

    assert resultado["alias_registrados"] == 1
    fila = db.una(
        ctx.boveda,
        "select id_persona from alias_origen where origen = %s and id_en_origen = %s",
        ("alchemer", "2-2"))
    assert str(fila["id_persona"]) == ola["sin_alias"]


def test_el_alias_sembrado_sirve_en_la_carga_siguiente(ctx, ola):
    """El punto de sembrarlo."""
    _ingestar(ctx, ola, [{"ID": "2-2", "P1": "1"}],
              tipo_identificador="documento", origen="alchemer")

    segunda = _ingestar(ctx, ola, [{"ID": "2-2", "P1": "2"}], origen="alchemer")

    assert segunda["tipo_identificador"] == "alias"
    assert segunda["sin_mapear"] == []


def test_un_alias_que_ya_existia_no_se_duplica(ctx, ola):
    _ingestar(ctx, ola, [{"ID": "2-2", "P1": "1"}],
              tipo_identificador="documento", origen="alchemer")
    segunda = _ingestar(ctx, ola, [{"ID": "2-2", "P1": "2"}],
                        tipo_identificador="documento", origen="alchemer")

    assert segunda["alias_registrados"] == 0
    assert db.una(
        ctx.boveda,
        "select count(*)::int as n from alias_origen where origen = %s",
        ("alchemer",))["n"] == 1


def test_sin_origen_declarado_no_se_siembra_nada(ctx, ola):
    """El par `(origen, id_en_origen)` es lo que identifica al alias: la
    mitad sola no sirve para nada."""
    resultado = _ingestar(
        ctx, ola, [{"ID": "2-2", "P1": "1"}], tipo_identificador="documento")

    assert resultado["alias_registrados"] == 0
    assert resultado["sin_mapear"] == [], "igual mapea: el alias es un extra"


def test_mapear_por_id_persona_no_siembra_alias(ctx, ola):
    """No hay id de plataforma que registrar: el archivo trajo el nuestro."""
    resultado = _ingestar(
        ctx, ola, [{"ID": ola["con_alias"], "P1": "1"}],
        tipo_identificador="id_persona", origen="dooblo")

    assert resultado["alias_registrados"] == 0


# ── El resto del flujo no cambia ─────────────────────────────────────

def test_el_gate_de_uso_semantico_sigue_aplicando(ctx, conn_boveda):
    """R3.12 cambia cómo se encuentra a la persona, no qué se puede hacer
    con ella."""
    from panel_api import consentimiento

    panel = paneles.crear(conn_boveda, "Panel")
    persona = _panelista(conn_boveda, "9-9")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    consentimiento.marcar_retirado(conn_boveda, persona, consentimiento.SEMANTICO)

    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"], PREGUNTAS,
        [{"ID": persona, "P1": "1"}],
        columna_id="ID", proveedor=ctx.embeddings,
        tipo_identificador="id_persona")

    assert persona in resultado["sin_consentimiento"]
    assert resultado["respuestas_escritas"] == 0


def test_la_membresia_y_la_participacion_se_registran_igual(ctx, conn_boveda):
    """Addendum de R3.9, con el identificador nuevo."""
    panel = paneles.crear(conn_boveda, "Panel")
    persona = _panelista(conn_boveda, "8-8")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")

    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"], PREGUNTAS,
        [{"ID": persona, "P1": "1"}],
        columna_id="ID", proveedor=ctx.embeddings,
        tipo_identificador="id_persona")

    assert resultado["membresias_nuevas"] == 1
    assert resultado["participaciones_nuevas"] == 1
