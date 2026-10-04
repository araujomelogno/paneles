"""R7.2 — el resumen que se revisa antes de importar.

Importar es la operación menos reversible del sistema: crea personas en la
bóveda. Hasta ahora se confirmaba a ciegas.

La prueba que más importa de este archivo es
`test_una_variable_de_si_no_marcada_como_documento_se_grita`: reproduce el
caso real —1131 filas, una variable de «¿consumiste?» marcada como
documento— y comprueba que el resumen lo muestre como advertencia grave y no
como un número más. Ese error llegó a producción y solo no ocurrió por otro
bug.
"""

import base64

import pytest

from panel_api import db, encuestas, paneles, resumen_ingesta, ruteo, sav
from panel_api.errores import DatosInvalidos

from conftest import consentimientos

VERSION = "consentimiento-campo-2026-09"


# ── El módulo, directo ───────────────────────────────────────────────

def _plan(**extra):
    base = {
        "preguntas": [
            {"codigo": "P1", "texto": "¿Qué bebida prefiere?", "tipo": "cerrada"},
            {"codigo": "NOM", "texto": "Nombre de pila", "tipo": "abierta"},
            {"codigo": "DOC", "texto": "Cédula", "tipo": "abierta"},
        ],
        "demograficas": {
            "NOM": {"campo": "nombre", "mapeo": {}},
            "DOC": {"campo": "documento", "mapeo": {}},
        },
        "columna_id": "ID",
        "origen": "sav",
        "tipo_identificador": "alias",
        "modo": "crear_individuos",
        "evidencia_declarada": True,
    }
    base.update(extra)
    return base


def _filas(n, documento=lambda i: f"4{i:06d}-1"):
    return [{"ID": f"r{i}", "P1": "Fernet", "NOM": f"Persona {i}",
             "DOC": documento(i)} for i in range(n)]


def test_las_variables_se_agrupan_por_destino():
    r = resumen_ingesta.resumir(_plan(), _filas(12))
    assert [v["codigo"] for v in r["al_store_semantico"]["variables"]] == ["P1"]
    identidad = {v["codigo"]: v for v in r["a_la_boveda"]["identidad_y_contacto"]}
    assert set(identidad) == {"NOM", "DOC"}
    # R7.2 — el texto de la pregunta junto al código, que es lo que vuelve
    # visible un marcado equivocado.
    assert identidad["DOC"]["texto"] == "Cédula"
    assert identidad["DOC"]["campo"] == "documento"


def test_una_variable_del_archivo_sin_declarar_queda_como_excluida():
    filas = [dict(f, SOBRA="x") for f in _filas(12)]
    r = resumen_ingesta.resumir(_plan(), filas)
    excluidas = {e["codigo"]: e["motivo"] for e in r["excluidas"]}
    assert "no se declaró" in excluidas["SOBRA"]


def test_el_volumen_cuenta_respuestas_y_no_celdas_vacias():
    filas = _filas(10)
    filas[0]["P1"] = ""
    r = resumen_ingesta.resumir(_plan(), filas)
    assert r["volumen"]["filas_del_archivo"] == 10
    assert r["volumen"]["respuestas_a_escribir"] == 9


def test_la_clave_de_dedup_cuenta_distintos_y_colisiones():
    r = resumen_ingesta.resumir(_plan(), _filas(10))
    documento = next(c for c in r["dedup"] if c["clave"] == "documento")
    assert documento["filas_con_valor"] == 10
    assert documento["valores_distintos"] == 10
    assert documento["filas_que_colisionan"] == 0
    assert documento["sospechosa"] is False
    assert r["volumen"]["personas_estimadas"] == 10


def test_una_variable_de_si_no_marcada_como_documento_se_grita():
    """El caso real, con sus números: 1131 filas y dos valores.

    El resumen tiene que decir que se crearían **dos** personas, y decirlo
    como advertencia grave. Es el único momento en que ese error todavía se
    puede evitar.
    """
    filas = _filas(1131, documento=lambda i: str(i % 2))
    r = resumen_ingesta.resumir(_plan(), filas)

    documento = next(c for c in r["dedup"] if c["clave"] == "documento")
    assert documento["filas_con_valor"] == 1131
    assert documento["valores_distintos"] == 2
    assert documento["filas_que_colisionan"] == 1131
    assert documento["sospechosa"] is True

    grave = [a for a in r["advertencias"] if a["grave"]]
    assert any(a["tipo"] == "clave_de_dedup_sospechosa" for a in grave)
    assert "1131" in grave[0]["mensaje"] and "2" in grave[0]["mensaje"]
    # Y el número que lo resume todo.
    assert r["volumen"]["personas_estimadas"] == 2


def test_sin_ninguna_clave_de_dedup_se_advierte():
    plan = _plan(demograficas={"NOM": {"campo": "nombre", "mapeo": {}}})
    r = resumen_ingesta.resumir(plan, _filas(12))
    assert r["dedup"] == []
    assert any(a["tipo"] == "sin_clave_de_dedup" and a["grave"]
               for a in r["advertencias"])


def test_la_clave_compuesta_solo_cuenta_si_estan_las_dos_variables():
    plan = _plan(
        preguntas=[{"codigo": "NOM", "texto": "Nombre", "tipo": "abierta"},
                   {"codigo": "FNAC", "texto": "Nacimiento", "tipo": "abierta"}],
        demograficas={"NOM": {"campo": "nombre", "mapeo": {}},
                      "FNAC": {"campo": "fecha_nacimiento", "mapeo": {}}})
    filas = [{"ID": f"r{i}", "NOM": "Natalia", "FNAC": "1993-09-30"}
             for i in range(20)]
    r = resumen_ingesta.resumir(plan, filas)
    compuesta = next(c for c in r["dedup"]
                     if c["clave"] == "nombre + fecha de nacimiento")
    assert compuesta["valores_distintos"] == 1
    assert compuesta["sospechosa"] is True
    assert r["volumen"]["personas_estimadas"] == 1


def test_una_fila_sin_ninguna_clave_cuenta_como_persona_propia():
    """Es lo que va a pasar al crearla: no se puede agrupar con nadie."""
    filas = _filas(5) + [{"ID": "r9", "P1": "Fernet", "NOM": "", "DOC": ""}]
    r = resumen_ingesta.resumir(_plan(), filas)
    assert r["volumen"]["filas_sin_clave_de_dedup"] == 1
    assert r["volumen"]["personas_estimadas"] == 6


def test_los_valores_sin_mapear_se_avisan_en_la_revision(conn_boveda):
    """R7.2 — las advertencias que hoy aparecen recién al final."""
    plan = _plan(
        preguntas=[{"codigo": "NEDU", "texto": "Nivel educativo",
                    "tipo": "cerrada"}],
        demograficas={"NEDU": {"campo": "nivel_educativo",
                               "mapeo": {"1": "primaria"}}})
    filas = [{"ID": f"r{i}", "NEDU": str(1 + i % 3)} for i in range(12)]
    r = resumen_ingesta.resumir(plan, filas)
    aviso = next(a for a in r["advertencias"] if a["tipo"] == "valores_sin_mapear")
    assert aviso["detalle"][0]["variable"] == "NEDU"
    assert set(aviso["detalle"][0]["valores"]) == {"2", "3"}


def test_el_modo_que_no_crea_personas_no_pide_consentimiento():
    r = resumen_ingesta.resumir(_plan(modo="existen"), _filas(12))
    assert r["consentimiento"]["aplica"] is False
    assert r["identidad"]["crea_personas"] is False


# ── Por la ruta: nada se escribe hasta confirmar ─────────────────────

def _sav(tmp_path):
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")
    ruta = tmp_path / "campo.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({
            "ID": [f"r{i}" for i in range(12)],
            "NOM": [f"Persona {i}" for i in range(12)],
            "DOC": [f"4{i:06d}-1" for i in range(12)],
            "CONS": [1.0] * 12,
            "P1": [1.0] * 12,
        }),
        str(ruta), variable_value_labels={"P1": {1.0: "Fernet"}})
    return ruta


def _cuerpo(ruta, panel_id, **extra):
    cuerpo = {
        "archivo_base64": base64.b64encode(ruta.read_bytes()).decode(),
        "columna_id": "ID", "origen": "sav", "panel_id": panel_id,
        "modo": "crear_individuos", "tipo_identificador": "alias",
        "preguntas": [
            {"codigo": "P1", "texto": "¿Qué bebida?", "tipo": "cerrada",
             "opciones": {"1": "Fernet"}, "orden": 1},
            {"codigo": "NOM", "texto": "Nombre", "tipo": "abierta", "orden": 2},
            {"codigo": "DOC", "texto": "Cédula", "tipo": "abierta", "orden": 3},
        ],
        "demograficas": {"NOM": "nombre", "DOC": "documento"},
        "evidencia_consentimiento": {
            f: {"variable": "CONS", "valor_afirmativo": "1",
                "version_texto": VERSION}
            for f in ("contacto_participacion", "uso_semantico")},
    }
    cuerpo.update(extra)
    return cuerpo


def test_revisar_no_escribe_nada(ctx, actor, conn_boveda, tmp_path):
    """El punto del DoD: nada se escribe hasta confirmar en el resumen."""
    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")

    status, r = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta",
        _cuerpo(_sav(tmp_path), panel["id"], solo_revisar=True), {},
        actor("operaciones"), ctx)

    assert status == 200
    assert "trabajo_id" not in r
    assert db.una(conn_boveda, "select count(*) as n from persona")["n"] == 0
    assert db.una(conn_boveda,
                  "select count(*) as n from ingesta_trabajo")["n"] == 0
    assert len(ctx.encolador.encoladas) == 0


def test_el_resumen_por_la_ruta_trae_las_siete_secciones(
        ctx, actor, conn_boveda, tmp_path):
    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")
    _, r = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta",
        _cuerpo(_sav(tmp_path), panel["id"], solo_revisar=True), {},
        actor("operaciones"), ctx)

    for seccion in ("identidad", "consentimiento", "al_store_semantico",
                    "a_la_boveda", "excluidas", "panel", "volumen",
                    "dedup", "advertencias"):
        assert seccion in r, seccion
    assert r["identidad"]["modo"] == "crear_individuos"
    assert r["consentimiento"]["aplica"] is True
    assert r["consentimiento"]["finalidades"][0]["version_texto"] == VERSION
    assert r["volumen"]["filas_del_archivo"] == 12


def test_confirmar_despues_de_revisar_si_escribe(
        ctx, actor, conn_boveda, tmp_path):
    """Volver atrás y confirmar con el mismo cuerpo, sin la bandera."""
    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")
    ruta = _sav(tmp_path)

    ruteo.despachar("POST", f"/encuestas/{enc['id']}/sav/ingesta",
                    _cuerpo(ruta, panel["id"], solo_revisar=True), {},
                    actor("operaciones"), ctx)
    status, r = ruteo.despachar("POST", f"/encuestas/{enc['id']}/sav/ingesta",
                                _cuerpo(ruta, panel["id"]), {},
                                actor("operaciones"), ctx)
    assert status == 202
    assert r["creacion_de_individuos"]["resumen"]["creados"] == 12


def test_la_revision_de_una_carga_sin_panel_lo_dice(
        ctx, actor, conn_boveda, tmp_path):
    from panel_api import cargas

    carga = cargas.crear(conn_boveda, "Ómnibus marzo", None)
    _, r = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta",
        _cuerpo(_sav(tmp_path), None, solo_revisar=True), {},
        actor("operaciones"), ctx)
    assert r["panel"]["sin_panel"] is True
    assert r["panel"]["destino_tipo"] == "carga"
