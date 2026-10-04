"""El modo «crear los individuos» viaja, se registra y se puede auditar.

Salió de un bug de producción (trabajos 2 y 3): se eligió ese modo, no se
creó nadie, y **no había forma de saber con qué modo se había corrido la
carga**. El plan persistido no lo llevaba, así que la investigación no podía
distinguir «el modo no llegó al backend» de «llegó y falló otra cosa».

Las pruebas de acá cubren las tres capas que el reporte pedía verificar: que
el cuerpo lo transporte, que el backend lo use, y que el plan lo refleje.

Lo que ya existía —`sav.crear_individuos` en sus veinte variantes— vive en
`test_fase3_operacion.py`. Acá se prueba **la ruta**, que es donde se perdía.
"""

import base64

import pytest

from panel_api import db, encuestas, ingesta, paneles, ruteo
from panel_api.errores import DatosInvalidos

VERSION = "consentimiento-campo-2026-09"   # publicada por el conftest


def _procesar_lotes(ctx, trabajo_id):
    """Corre las tareas encoladas, como lo haría Cloud Tasks."""
    from panel_api import diferida

    for indice in range(len(ctx.encolador.encoladas)):
        diferida.procesar_lote(ctx.boveda, ctx.semantica, trabajo_id, indice,
                               proveedor=ctx.embeddings)


def _sav(tmp_path):
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")
    ruta = tmp_path / "campo.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({
            "ID": ["a1", "a2"],
            "NOM": ["Ana Pérez", "Beto Gómez"],
            "DOC": ["4111111-1", "4222222-2"],
            "CONS": [1.0, 1.0],
            "P1": [1.0, 1.0],
        }),
        str(ruta),
        variable_value_labels={"P1": {1.0: "Fernet"}},
    )
    return ruta


def _evidencia():
    regla = {"variable": "CONS", "valor_afirmativo": "1",
             "version_texto": VERSION}
    return {"contacto_participacion": dict(regla), "uso_semantico": dict(regla)}


def _cuerpo(ruta, panel_id, modo, con_evidencia=True):
    cuerpo = {
        "archivo_base64": base64.b64encode(ruta.read_bytes()).decode(),
        "columna_id": "ID",
        "origen": "sav",
        "panel_id": panel_id,
        # El caso real lo mandaba, y era la diferencia con la única prueba de
        # ruta que existía. No cambia el modo: dice qué clase de
        # identificador trae la columna (R3.12).
        "tipo_identificador": "alias",
        "preguntas": [
            {"codigo": "P1", "texto": "¿Qué bebida prefiere?",
             "tipo": "cerrada", "opciones": {"1": "Fernet"}, "orden": 1},
            {"codigo": "NOM", "texto": "Nombre", "tipo": "abierta", "orden": 2},
            {"codigo": "DOC", "texto": "Documento", "tipo": "abierta", "orden": 3},
        ],
        "demograficas": {"NOM": "nombre", "DOC": "documento"},
    }
    if modo:
        cuerpo["modo"] = modo
    if con_evidencia:
        cuerpo["evidencia_consentimiento"] = _evidencia()
    return cuerpo


def _ingestar(ctx, actor, encuesta_id, cuerpo):
    return ruteo.despachar(
        "POST", f"/encuestas/{encuesta_id}/sav/ingesta", cuerpo, {},
        actor("operaciones"), ctx)


@pytest.fixture
def ola(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel de calle")
    return panel, encuestas.crear(conn_boveda, panel["id"], "Ola de calle")


def _plan(ctx, trabajo_id):
    return db.una(ctx.boveda, "select plan from ingesta_trabajo where id = %s",
                  (trabajo_id,))["plan"]


# ── Lo que el reporte pedía que quedara registrado ───────────────────

def test_el_modo_queda_en_el_plan_persistido(ctx, actor, ola, tmp_path):
    """Sin esto, una carga que salió mal no se puede diagnosticar.

    Es el punto 3 del reporte: «el plan persistido lo refleja, de modo que
    sea verificable después». Mirar la carga tiene que alcanzar para saber
    con qué modo corrió.
    """
    panel, enc = ola
    status, resp = _ingestar(ctx, actor, enc["id"],
                             _cuerpo(tmp_path and _sav(tmp_path), panel["id"],
                                     "crear_individuos"))
    assert status == 202
    plan = _plan(ctx, resp["trabajo_id"])
    assert plan["modo"] == "crear_individuos"
    assert plan["evidencia_declarada"] is True


def test_sin_modo_el_plan_dice_que_fue_el_de_siempre(ctx, actor, ola, tmp_path):
    """Un cuerpo que no manda modo quedó registrado como `existen`, que es
    lo que el backend hace. El plan no puede quedar en silencio: «no dice
    nada» es justo el estado del que salió este bug."""
    panel, enc = ola
    status, resp = _ingestar(ctx, actor, enc["id"],
                             _cuerpo(_sav(tmp_path), panel["id"], None,
                                     con_evidencia=False))
    assert status == 202
    plan = _plan(ctx, resp["trabajo_id"])
    assert plan["modo"] == "existen"
    assert plan["evidencia_declarada"] is False


# ── Que el modo haga lo que dice ─────────────────────────────────────

def test_en_modo_crear_con_evidencia_se_crean_las_personas(
        ctx, actor, ola, tmp_path):
    """Y las respuestas de esas personas entran: el alta registra el alias,
    así que el lote que viene después las encuentra."""
    panel, enc = ola
    status, resp = _ingestar(ctx, actor, enc["id"],
                             _cuerpo(_sav(tmp_path), panel["id"],
                                     "crear_individuos"))
    assert status == 202
    assert resp["creacion_de_individuos"]["resumen"]["creados"] == 2

    _procesar_lotes(ctx, resp["trabajo_id"])
    resumen = db.una(ctx.boveda,
                     "select resumen from ingesta_trabajo where id = %s",
                     (resp["trabajo_id"],))["resumen"]
    assert resumen["personas"] == 2
    assert resumen["sin_mapear"] == []


def test_en_modo_crear_sin_evidencia_no_se_crea_nadie_y_lo_dice(
        ctx, actor, ola, tmp_path):
    """Y lo dice por lo que es —falta la base legal del alta— y no
    disfrazado de «sin_mapear», que manda a revisar la columna
    identificadora."""
    panel, enc = ola
    with pytest.raises(DatosInvalidos) as fallo:
        _ingestar(ctx, actor, enc["id"],
                  _cuerpo(_sav(tmp_path), panel["id"],
                          "crear_individuos", con_evidencia=False))

    assert "evidencia de consentimiento" in str(fallo.value)
    assert db.una(ctx.boveda, "select count(*) as n from persona")["n"] == 0
    # Y no quedó una carga encolada a medias: si el alta se rechaza, no hay
    # nada que procesar.
    assert db.una(ctx.boveda,
                  "select count(*) as n from ingesta_trabajo")["n"] == 0


def test_el_modo_existen_sigue_comportandose_igual(ctx, actor, ola, tmp_path):
    """No regresión: con la bóveda vacía, nadie matchea y no se crea nadie.
    Es exactamente lo que hizo el trabajo 2 del reporte, y estaba bien."""
    panel, enc = ola
    status, resp = _ingestar(ctx, actor, enc["id"],
                             _cuerpo(_sav(tmp_path), panel["id"], "existen",
                                     con_evidencia=False))
    assert status == 202
    assert "creacion_de_individuos" not in resp

    _procesar_lotes(ctx, resp["trabajo_id"])
    resumen = db.una(ctx.boveda,
                     "select resumen from ingesta_trabajo where id = %s",
                     (resp["trabajo_id"],))["resumen"]
    assert resumen["personas"] == 0
    assert db.una(ctx.boveda, "select count(*) as n from persona")["n"] == 0


# ── El aviso que mandaba a buscar el problema donde no estaba ────────

def test_el_aviso_de_cien_por_ciento_sin_mapear_sugiere_el_modo():
    """«Ninguna respuesta quedó habilitada para ingestar» era verdad y no
    servía. Cuando el 100% de las filas cae por falta de alias, el sistema
    tiene todo para decir qué hacer."""
    aviso = ingesta._por_que_no_entro_nada(
        ["a1", "a2", "a3"], ["a1", "a2", "a3"],
        {i: ingesta.SIN_ALIAS for i in ["a1", "a2", "a3"]}, [])
    assert "3" in aviso
    assert "crear los individuos" in aviso


def test_el_aviso_distingue_el_consentimiento_de_la_falta_de_match():
    """Son dos problemas opuestos: uno se arregla eligiendo otro modo y el
    otro no se arregla —esas respuestas no se conservan, y está bien—."""
    aviso = ingesta._por_que_no_entro_nada([], [], {}, ["p1", "p2"])
    assert "consentimiento" in aviso
    assert "crear los individuos" not in aviso


def test_el_aviso_nombra_el_motivo_cuando_no_es_el_alias():
    """Un uuid mal formado no se arregla cambiando de modo."""
    aviso = ingesta._por_que_no_entro_nada(
        ["x"], ["x"], {"x": ingesta.FORMATO_INVALIDO}, [])
    assert ingesta.FORMATO_INVALIDO in aviso
    assert "crear los individuos" not in aviso


# ── La guarda que el arreglo del modo hizo necesaria ─────────────────
#
# El bug del modo evitó por casualidad un desastre: en la carga real,
# `var138O1320` —«¿has consumido alguno de estos productos?», valores 0/1—
# estaba marcada como `documento`. Como el dedup resuelve primero por
# documento, crear esas 1131 personas las habría fusionado en dos. Arreglar
# el modo quita la casualidad, así que la guarda va junto con el arreglo.

def test_un_documento_con_dos_valores_distintos_no_crea_a_nadie(
        ctx, actor, conn_boveda):
    from panel_api import sav
    from panel_api.errores import DatosInvalidos

    # `CONS` va porque el control de la variable de consentimiento corre
    # antes: sin ella se frena por eso y no se llega a mirar el documento.
    filas = [{"ID": f"r{i}", "NOM": f"Persona {i}", "FUMA": str(i % 2),
              "CONS": "1"}
             for i in range(40)]
    with pytest.raises(DatosInvalidos) as fallo:
        sav.crear_individuos(
            conn_boveda, filas, {"nombre": "NOM", "documento": "FUMA"},
            origen="sav", columna_id="ID",
            evidencia_consentimiento=_evidencia(),
            opciones_por_variable={"FUMA": {"0": "Unchecked", "1": "Checked"}})

    mensaje = str(fallo.value)
    assert "FUMA" in mensaje and "documento" in mensaje
    # Las etiquetas son lo que vuelve obvio el error de marcado.
    assert "Unchecked" in mensaje
    assert db.una(conn_boveda, "select count(*) as n from persona")["n"] == 0


def test_un_padron_chico_con_documentos_de_verdad_pasa(ctx, actor, conn_boveda):
    """La guarda no puede volverse un estorbo: tres documentos distintos en
    un archivo chico son un archivo chico, no un error de marcado."""
    from panel_api import sav

    filas = [{"ID": f"r{i}", "NOM": f"Persona {i}",
              "DOC": f"4{i:06d}-1", "CONS": "1"} for i in range(12)]
    resultado = sav.crear_individuos(
        conn_boveda, filas, {"nombre": "NOM", "documento": "DOC"},
        origen="sav", columna_id="ID",
        evidencia_consentimiento=_evidencia())
    assert resultado["resumen"]["creados"] == 12


def test_un_archivo_de_dos_filas_no_dispara_la_guarda(ctx, actor, conn_boveda):
    """Con dos filas, dos documentos distintos es el 100%: no hay nada
    sospechoso y frenarlo sería ruido."""
    from panel_api import sav

    filas = [{"ID": "r1", "NOM": "Ana", "DOC": "4111111-1", "CONS": "1"},
             {"ID": "r2", "NOM": "Beto", "DOC": "4222222-2", "CONS": "1"}]
    resultado = sav.crear_individuos(
        conn_boveda, filas, {"nombre": "NOM", "documento": "DOC"},
        origen="sav", columna_id="ID",
        evidencia_consentimiento=_evidencia())
    assert resultado["resumen"]["creados"] == 2
