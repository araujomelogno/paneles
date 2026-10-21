"""PEDIDO — Borrar una carga, con sus panelistas y respuestas (parte 3 de
`docs/PEDIDO_celular_identificador_y_falso_positivo_pii.md`; D78).

El escenario es una carga «Ómnibus de prueba» con seis personas, una por
cada caso del pedido:

* A y F — las creó la carga y no tienen otra vida: **se dan de baja**.
* B — ya existía (panelista propio): la carga la **reutilizó**.
* C — la creó la carga y **después** entró en un panel.
* D — la creó la carga y después vino también en **otra carga**.
* E — la creó la carga y tiene **respuestas de otro estudio**.

B a E se conservan, sin el vínculo ni las respuestas de esta carga.

Las respuestas: A contestó «Whisky» (un texto que solo existe en esta
carga), las demás «Fernet», que E también dio en otro estudio. Así hay una
unidad de evidencia que se va con la carga y otra, compartida, que se queda.

Corre contra los dos stores reales, con los lotes procesados a mano como los
procesaría Cloud Tasks.
"""

import uuid

import pytest

from panel_api import (
    cargas, cargas_borrado, db, diferida, ingesta, paneles, ruteo, semantica,
)
from panel_api.errores import Conflicto, DatosInvalidos, IngestaEnCurso, SinPermiso

from test_r_org_origen_y_ambitos import PREGUNTAS, _cargar, _fila, _panelista

NOMBRE = "Ómnibus de prueba"
P1 = [p for p in PREGUNTAS if p["codigo"] == "P1"]


def _id(conn, documento):
    fila = db.una(conn, "select id_persona from persona where documento = %s",
                  (documento,))
    return str(fila["id_persona"]) if fila else None


@pytest.fixture
def escenario(ctx):
    conn = ctx.boveda
    _panelista(conn, "20-2", "Beto Propio")                       # B, ya existía
    carga = cargas.crear(conn, NOMBRE, tipo_datos_origen="encuesta",
                         modo_relevamiento="telefonico")
    filas = [_fila(i) for i in range(1, 7)]
    filas[0]["P1"] = "2"                                           # A: Whisky
    _cargar(ctx, carga, filas)

    ids = {letra: _id(conn, f"20-{i}") for i, letra in enumerate("ABCDEF", 1)}
    panel = paneles.crear(conn, "Panel general")
    paneles.agregar_miembro(conn, panel["id"], ids["C"])           # C, después
    otra = cargas.crear(conn, "Tracking", tipo_datos_origen="encuesta",
                        modo_relevamiento="web")
    cargas.registrar_vinculos(conn, otra["id"], [ids["D"]], "reutilizada")
    ingesta.ingestar(                                              # E, otro estudio
        conn, ctx.semantica,
        {"ref_estudio": str(uuid.uuid4()), "nombre": "Otro estudio",
         "fecha_campo": None, "panel_id": None},
        P1, [{"ID": "x-5", "P1": "1"}], columna_id="ID",
        proveedor=ctx.embeddings, mapa_personas={"x-5": ids["E"]})
    conn.commit()
    ctx.semantica.commit()
    return {"carga": carga, "otra": otra, "ids": ids}


def _pedir(ctx, actor, carga, rol="admin", **cuerpo):
    return ruteo.despachar("POST", f"/cargas/{carga['id']}/borrado", cuerpo, {},
                           actor(rol), ctx)


def _revisar(ctx, actor, carga):
    _, previa = _pedir(ctx, actor, carga, solo_revisar=True)
    return previa


def _procesar(ctx):
    """Corre las tareas encoladas, como Cloud Tasks."""
    pendientes = list(ctx.encolador.encoladas)
    ctx.encolador.encoladas.clear()
    return [diferida.procesar_lote(ctx.boveda, ctx.semantica, t, i,
                                   proveedor=ctx.embeddings)
            for t, i in pendientes]


def _borrar(ctx, actor, carga):
    status, salida = _pedir(ctx, actor, carga, confirmacion=carga["nombre"])
    _procesar(ctx)
    return status, salida


def _respuestas_del_estudio(conn_semantica, ref_estudio):
    return semantica.contar_estudio(conn_semantica, ref_estudio)["respuestas"]


def _registro(conn):
    return db.una(conn, "select * from carga_borrado order by id desc limit 1")


@pytest.fixture
def coloquio_activo(conn_boveda):
    db.ejecutar(conn_boveda,
                "update sistema_consumidor set activo = true where codigo = 'coloquio'")
    conn_boveda.commit()
    yield
    conn_boveda.rollback()
    db.ejecutar(conn_boveda,
                "update sistema_consumidor set activo = false where codigo = 'coloquio'")
    conn_boveda.commit()


# ════════════════════════════════════════════════════════════════════
#  La vista previa
# ════════════════════════════════════════════════════════════════════

def test_la_vista_previa_dice_quien_se_va_quien_se_queda_y_por_que(
        ctx, actor, escenario):
    previa = _revisar(ctx, actor, escenario["carga"])

    assert previa["personas"] == 6
    assert previa["se_dan_de_baja"] == 2
    assert previa["se_conservan"] == 4
    assert {m["motivo"]: m["personas"] for m in previa["conservadas_por_motivo"]} == {
        "reutilizada": 1, "panel": 1, "otra_carga": 1,
        "respuestas_de_otro_estudio": 1}
    assert previa["respuestas_a_borrar"] == 6
    assert previa["unidades_que_se_borran"] == 1       # «Whisky»; «Fernet» se queda
    assert previa["irreversible"] is True
    assert previa["borrado_en_curso"] is None


def test_la_vista_previa_no_toca_nada(ctx, actor, escenario):
    _revisar(ctx, actor, escenario["carga"])
    assert cargas.obtener(ctx.boveda, escenario["carga"]["id"])["personas"] == 6
    assert _respuestas_del_estudio(ctx.semantica, escenario["carga"]["ref_estudio"]) == 6
    assert not db.todas(ctx.boveda, "select 1 from carga_borrado")


# ════════════════════════════════════════════════════════════════════
#  Qué se borra y qué no
# ════════════════════════════════════════════════════════════════════

def test_las_creadas_sin_otros_vinculos_se_dan_de_baja(ctx, actor, escenario):
    _borrar(ctx, actor, escenario["carga"])
    ids = escenario["ids"]
    for letra in "AF":
        assert not db.una(ctx.boveda, "select 1 from persona where id_persona = %s",
                          (ids[letra],)), letra
        lapida = db.una(ctx.boveda,
                        "select motivo from persona_borrada where id_persona = %s",
                        (ids[letra],))
        assert lapida["motivo"] == "borrado_de_carga"


def test_las_demas_se_conservan_sin_lo_que_la_carga_les_agrego(ctx, actor, escenario):
    _borrar(ctx, actor, escenario["carga"])
    ids = escenario["ids"]
    for letra in "BCDE":
        assert db.una(ctx.boveda, "select 1 from persona where id_persona = %s",
                      (ids[letra],)), f"{letra} tenía que conservarse"
    # C sigue en su panel, D en la otra carga, E con su otro estudio.
    assert db.una(ctx.boveda, "select 1 from membresia where id_persona = %s",
                  (ids["C"],))
    assert db.todas(ctx.boveda,
                    "select carga_id from persona_carga where id_persona = %s",
                    (ids["D"],)) == [{"carga_id": escenario["otra"]["id"]}]
    assert semantica.con_respuestas_de_otro_estudio(
        ctx.semantica, [ids["E"]], escenario["carga"]["ref_estudio"]) == {ids["E"]}


def test_se_borran_todas_las_respuestas_del_estudio_y_la_carga(ctx, actor, escenario):
    carga = escenario["carga"]
    _borrar(ctx, actor, carga)
    assert _respuestas_del_estudio(ctx.semantica, carga["ref_estudio"]) == 0
    assert not db.una(ctx.semantica, "select 1 from cuestionario where ref_estudio = %s",
                      (str(carga["ref_estudio"]),))
    assert not db.una(ctx.boveda, "select 1 from carga where id = %s", (carga["id"],))
    assert not db.una(ctx.boveda, "select 1 from persona_carga where carga_id = %s",
                      (carga["id"],))


def test_la_unidad_compartida_se_conserva_y_la_propia_se_borra(ctx, actor, escenario):
    textos = lambda: {f["texto_embebido"] for f in db.todas(  # noqa: E731
        ctx.semantica, "select texto_embebido from unidad_evidencia")}
    antes = textos()
    assert any("Whisky" in t for t in antes) and any("Fernet" in t for t in antes)

    _borrar(ctx, actor, escenario["carga"])
    despues = textos()
    assert not any("Whisky" in t for t in despues)
    assert any("Fernet" in t for t in despues)


def test_la_baja_le_avisa_a_coloquio(ctx, actor, escenario, coloquio_activo):
    _borrar(ctx, actor, escenario["carga"])
    for letra in "AF":
        pendiente = db.una(
            ctx.boveda,
            "select alcance from borrado_pendiente where id_persona = %s and sistema = 'coloquio'",
            (escenario["ids"][letra],))
        assert pendiente and pendiente["alcance"] == "total", letra
    # A quien se conserva no se le avisa nada: sigue existiendo.
    assert not db.una(ctx.boveda,
                      "select 1 from borrado_pendiente where id_persona = %s",
                      (escenario["ids"]["B"],))


# ════════════════════════════════════════════════════════════════════
#  Auditoría y vista previa = resultado
# ════════════════════════════════════════════════════════════════════

def test_queda_auditado_con_sus_conteos_y_sobrevive_a_la_carga(ctx, actor, escenario):
    _borrar(ctx, actor, escenario["carga"])
    registro = _registro(ctx.boveda)
    assert registro["estado"] == "terminado"
    assert registro["carga_id"] is None                 # on delete set null
    assert registro["carga_nombre"] == NOMBRE
    assert str(registro["ref_estudio"]) == str(escenario["carga"]["ref_estudio"])
    assert registro["actor"] and registro["terminado_en"]
    assert (registro["personas_dadas_de_baja"], registro["personas_conservadas"],
            registro["respuestas_borradas"]) == (2, 4, 6)


def test_la_vista_previa_coincide_con_lo_que_se_borra(ctx, actor, escenario):
    previa = _revisar(ctx, actor, escenario["carga"])
    _borrar(ctx, actor, escenario["carga"])
    registro = _registro(ctx.boveda)
    assert registro["personas_dadas_de_baja"] == previa["se_dan_de_baja"]
    assert registro["personas_conservadas"] == previa["se_conservan"]
    assert registro["respuestas_borradas"] == previa["respuestas_a_borrar"]
    assert registro["previa"]["se_dan_de_baja"] == previa["se_dan_de_baja"]


# ════════════════════════════════════════════════════════════════════
#  Seguridad
# ════════════════════════════════════════════════════════════════════

def test_sin_el_nombre_exacto_no_borra(ctx, actor, escenario):
    for confirmacion in (None, "", "omnibus de prueba", "Otra"):
        with pytest.raises(DatosInvalidos):
            _pedir(ctx, actor, escenario["carga"], confirmacion=confirmacion)
    assert cargas.obtener(ctx.boveda, escenario["carga"]["id"])


@pytest.mark.parametrize("rol", ["operaciones", "analista", "dpo"])
def test_solo_administracion_puede_borrar(ctx, actor, escenario, rol):
    with pytest.raises(SinPermiso):
        _pedir(ctx, actor, escenario["carga"], rol=rol, solo_revisar=True)


def test_no_se_borra_una_carga_con_una_ingesta_en_curso(ctx, actor, escenario):
    carga = escenario["carga"]
    en_curso = diferida.encolar(ctx.boveda, "carga", carga["id"],
                                {"preguntas": P1, "columna_id": "ID"},
                                [_fila(9)], encolador=diferida.EncoladorEnMemoria())
    for cuerpo in ({"solo_revisar": True}, {"confirmacion": NOMBRE}):
        with pytest.raises(IngestaEnCurso) as rechazo:
            _pedir(ctx, actor, carga, **cuerpo)
        assert rechazo.value.detalle["trabajo_id"] == en_curso["trabajo_id"]
    assert not db.todas(ctx.boveda, "select 1 from carga_borrado")


def test_mientras_se_borra_no_se_puede_ingestar(ctx, actor, escenario):
    carga = escenario["carga"]
    _pedir(ctx, actor, carga, confirmacion=NOMBRE)     # encolado, sin procesar
    with pytest.raises(IngestaEnCurso):
        diferida.exigir_sin_trabajo_activo(ctx.boveda, "carga", carga["id"])


def test_dos_borrados_a_la_vez_no(ctx, actor, escenario, monkeypatch):
    """El segundo pedido llega cuando el primero ya registró el suyo: el
    índice de la 0030 lo frena."""
    carga = escenario["carga"]
    db.ejecutar(ctx.boveda,
                """insert into carga_borrado (carga_id, carga_nombre, ref_estudio, previa)
                   values (%s, %s, %s, '{}')""",
                (carga["id"], NOMBRE, str(carga["ref_estudio"])))
    ctx.boveda.commit()
    monkeypatch.setattr(cargas_borrado, "_borrado_en_curso", lambda *a: None)
    with pytest.raises(Conflicto):
        _pedir(ctx, actor, carga, confirmacion=NOMBRE)


# ════════════════════════════════════════════════════════════════════
#  Reanudable
# ════════════════════════════════════════════════════════════════════

def _sin_huerfanas(ctx, ref_estudio):
    """Ninguna respuesta del estudio, y ninguna respuesta de nadie que ya no
    exista en la bóveda."""
    assert _respuestas_del_estudio(ctx.semantica, ref_estudio) == 0
    con_respuestas = {str(f["id_persona"]) for f in db.todas(
        ctx.semantica,
        "select distinct i.id_persona from individuo i join respuesta r on r.individuo_id = i.id")}
    existentes = {str(f["id_persona"]) for f in db.todas(
        ctx.boveda, "select id_persona from persona")}
    assert con_respuestas <= existentes


def test_un_lote_interrumpido_se_reanuda_sin_dejar_huerfanas(
        ctx, actor, escenario, monkeypatch):
    carga = escenario["carga"]
    monkeypatch.setattr(cargas_borrado, "PERSONAS_POR_LOTE", 2)
    _pedir(ctx, actor, carga, confirmacion=NOMBRE)
    pendientes = list(ctx.encolador.encoladas)
    ctx.encolador.encoladas.clear()
    assert len(pendientes) == 3

    # El primero entra; el segundo se corta en la bóveda, **después** de
    # haber commiteado su parte del semántico —el peor momento—.
    diferida.procesar_lote(ctx.boveda, ctx.semantica, *pendientes[0],
                           proveedor=ctx.embeddings)
    original = cargas_borrado._clasificar
    monkeypatch.setattr(cargas_borrado, "_clasificar",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("se cortó")))
    with pytest.raises(RuntimeError):
        diferida.procesar_lote(ctx.boveda, ctx.semantica, *pendientes[1],
                               proveedor=ctx.embeddings)
    monkeypatch.setattr(cargas_borrado, "_clasificar", original)
    diferida.procesar_lote(ctx.boveda, ctx.semantica, *pendientes[2],
                           proveedor=ctx.embeddings)

    # A medias: la carga sigue, el borrado en curso.
    assert cargas.obtener(ctx.boveda, carga["id"])
    assert _registro(ctx.boveda)["estado"] == "en_curso"

    # Volver a pedirlo lo reanuda: reintenta el lote que falló y termina.
    status, salida = _pedir(ctx, actor, carga, confirmacion=NOMBRE)
    assert status == 202 and salida["reanudado"] == "reintento"
    _procesar(ctx)

    registro = _registro(ctx.boveda)
    assert registro["estado"] == "terminado"
    assert (registro["personas_dadas_de_baja"], registro["personas_conservadas"],
            registro["respuestas_borradas"]) == (2, 4, 6)
    _sin_huerfanas(ctx, carga["ref_estudio"])


def test_si_se_corta_al_final_volver_a_pedirlo_lo_termina(
        ctx, actor, escenario, monkeypatch):
    carga = escenario["carga"]
    original = cargas_borrado.semantica.borrar_estudio
    monkeypatch.setattr(cargas_borrado.semantica, "borrar_estudio",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("se cortó")))
    _borrar(ctx, actor, carga)
    assert _registro(ctx.boveda)["estado"] == "en_curso"
    assert cargas.obtener(ctx.boveda, carga["id"])

    monkeypatch.setattr(cargas_borrado.semantica, "borrar_estudio", original)
    status, salida = _pedir(ctx, actor, carga, confirmacion=NOMBRE)
    assert status == 200 and salida["estado"] == "terminado"
    assert not db.una(ctx.boveda, "select 1 from carga where id = %s", (carga["id"],))
    _sin_huerfanas(ctx, carga["ref_estudio"])


def test_una_carga_sin_personas_se_borra_en_el_acto(ctx, actor):
    vacia = cargas.crear(ctx.boveda, "Vacía", tipo_datos_origen="encuesta",
                         modo_relevamiento="web")
    ctx.boveda.commit()
    status, salida = _pedir(ctx, actor, vacia, confirmacion="Vacía")
    assert status == 200 and salida["estado"] == "terminado"
    assert not db.una(ctx.boveda, "select 1 from carga where id = %s", (vacia["id"],))
