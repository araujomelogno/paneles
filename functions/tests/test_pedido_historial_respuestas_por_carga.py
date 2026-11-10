"""PEDIDO del historial de respuestas por carga (D95), contra Postgres.

Parte 1 · Borrar una carga no puede borrar respuestas que otras cargas
contienen. En una encuesta incremental la última carga escribe **todo** el
acumulado (`respuesta.carga_id`), y borrarla borraba el estudio entero aunque
las anteriores siguieran existiendo: el caso de la carga 45, que dejó el
cuestionario con 4 preguntas y 0 respuestas.

* `respuesta_carga` guarda qué traía cada carga (R-HIS.1), en el store
  semántico, sin vectores propios.
* Borrar una carga **vuelve** la respuesta al valor de la carga más reciente
  que la tiene y borra solo lo que ninguna otra contiene (R-HIS.2).
* La vista previa lo separa (R-HIS.3) y borrar la encuesta se lleva el
  historial (R-HIS.4). La ficha dice en qué cargas apareció cada respuesta
  (R-HIS.5).
"""

import importlib.util
import pathlib

import pytest

from panel_api import (
    cargas_borrado, db, encuestas, encuestas_borrado, ficha, semantica,
)

from conftest import consentimientos
from test_pedido_cancelar_mixta_incrementales import (
    _fila, _ingestar, _nueva_carga, _persona, _procesar,
)

RAIZ = pathlib.Path(__file__).resolve().parents[2]
FERNET, WHISKY = "1", "2"


def _encuesta(ctx, nombre="Encuesta Prueba - 10-11-26"):
    encuesta = encuestas.crear(ctx.boveda, None, nombre,
                               tipo=encuestas.INDEPENDIENTE, origen="manual")
    ctx.boveda.commit()
    return encuesta


def _cargar(ctx, actor, encuesta, nombre, filas):
    carga = _nueva_carga(ctx, actor, encuesta["id"], nombre=nombre)
    status, _ = _ingestar(ctx, actor, carga["id"], filas, confirmar_archivo_repetido=True)
    assert status == 202
    _procesar(ctx)
    return carga


def _borrar(ctx, actor, carga):
    cargas_borrado.lanzar(ctx.boveda, ctx.semantica, carga["id"],
                          confirmacion=carga["nombre"], actor=actor("admin"),
                          encolador=ctx.encolador)
    _procesar(ctx)


def _respuestas(conn, ref_estudio):
    """`{id_persona: (valor_texto, carga_id)}` de las respuestas vigentes."""
    return {str(f["id_persona"]): (f["valor_texto"], f["carga_id"]) for f in db.todas(
        conn,
        """select i.id_persona, r.valor_texto, r.carga_id
             from respuesta r
             join individuo i on i.id = r.individuo_id
             join pregunta p on p.id = r.pregunta_id
             join cuestionario c on c.id = p.cuestionario_id
            where c.ref_estudio = %s""", (str(ref_estudio),))}


def _cuantas(conn, tabla):
    return db.una(conn, f"select count(*)::int as n from {tabla}")["n"]


@pytest.fixture
def gente(ctx):
    ids = {n: _persona(ctx.boveda, n, email=f"{n.lower()}@ej.uy") for n in "ABCD"}
    ctx.boveda.commit()
    return ids


@pytest.fixture
def incremental(ctx, actor, gente):
    """Tres cargas incrementales —A, después A y B, después A, B y C— y una
    cuarta con las mismas personas. La tercera dice Whisky; la cuarta, Fernet."""
    encuesta = _encuesta(ctx)
    a, b, c = gente["A"], gente["B"], gente["C"]
    cargas = [
        _cargar(ctx, actor, encuesta, "Parcial 1", [_fila(a, respuesta=FERNET)]),
        _cargar(ctx, actor, encuesta, "Parcial 2",
                [_fila(a, respuesta=FERNET), _fila(b, respuesta=FERNET)]),
        _cargar(ctx, actor, encuesta, "Parcial 3",
                [_fila(x, respuesta=WHISKY) for x in (a, b, c)]),
        _cargar(ctx, actor, encuesta, "Final",
                [_fila(x, respuesta=FERNET) for x in (a, b, c)]),
    ]
    return {"encuesta": encuesta, "cargas": cargas,
            "ref": encuestas.obtener(ctx.boveda, encuesta["id"])["ref_estudio"]}


# ── R-HIS.1 · El registro de cada respuesta por carga ─────────────────

def test_cada_carga_deja_su_fila_de_historial(ctx, incremental, gente):
    # 1 + 2 + 3 + 3 filas: una por persona, pregunta y carga.
    assert _cuantas(ctx.semantica, "respuesta_carga") == 9
    vigentes = _respuestas(ctx.semantica, incremental["ref"])
    final = incremental["cargas"][3]["id"]
    assert set(vigentes.values()) == {("Fernet", final)}
    # Sin vectores propios: cada fila apunta a una unidad que existe.
    assert db.una(ctx.semantica, """select count(*)::int as n from respuesta_carga h
                                     where not exists (select 1 from unidad_evidencia u
                                                        where u.id = h.unidad_id)""")["n"] == 0


# ── R-HIS.2 · Borrar una carga revierte en lugar de borrar ────────────

def test_borrar_la_cuarta_deja_las_respuestas_con_el_valor_de_la_tercera(
        ctx, actor, incremental, gente):
    """El caso de la carga 45: ya no deja el cuestionario vacío."""
    tercera, cuarta = incremental["cargas"][2], incremental["cargas"][3]
    _borrar(ctx, actor, cuarta)
    vigentes = _respuestas(ctx.semantica, incremental["ref"])
    assert vigentes == {gente[n]: ("Whisky", tercera["id"]) for n in "ABC"}
    # Su historial se fue con ella; el de las otras queda.
    assert db.una(ctx.semantica, "select count(*)::int as n from respuesta_carga "
                                 " where carga_id = %s", (cuarta["id"],))["n"] == 0
    assert _cuantas(ctx.semantica, "respuesta_carga") == 6
    # Las personas siguen en otras cargas: conservan su participación.
    respondio = {str(f["id_persona"]): f["respondio"] for f in db.todas(
        ctx.boveda, "select id_persona, respondio from participacion where encuesta_id = %s",
        (incremental["encuesta"]["id"],))}
    assert respondio == {gente[n]: True for n in "ABC"}


def test_con_el_mismo_valor_en_todas_el_estudio_no_queda_vacio(ctx, actor, gente):
    """El caso real tal cual: cargas 39, 41 y 42 incrementales y la 45 con las
    mismas personas y los mismos valores. Borrar la 45 deja 3 respuestas."""
    encuesta = _encuesta(ctx)
    a, b, c = gente["A"], gente["B"], gente["C"]
    _cargar(ctx, actor, encuesta, "39", [_fila(a)])
    _cargar(ctx, actor, encuesta, "41", [_fila(a), _fila(b)])
    cuarenta_y_dos = _cargar(ctx, actor, encuesta, "42", [_fila(a), _fila(b), _fila(c)])
    cuarenta_y_cinco = _cargar(ctx, actor, encuesta, "45", [_fila(a), _fila(b), _fila(c)])
    ref = encuestas.obtener(ctx.boveda, encuesta["id"])["ref_estudio"]
    _borrar(ctx, actor, cuarenta_y_cinco)
    vigentes = _respuestas(ctx.semantica, ref)
    assert len(vigentes) == 3
    assert {carga for _, carga in vigentes.values()} == {cuarenta_y_dos["id"]}


def test_una_respuesta_que_solo_estaba_en_la_carga_borrada_se_elimina(ctx, actor, gente):
    encuesta = _encuesta(ctx)
    a, d = gente["A"], gente["D"]
    primera = _cargar(ctx, actor, encuesta, "Uno", [_fila(a, respuesta=WHISKY)])
    segunda = _cargar(ctx, actor, encuesta, "Dos",
                      [_fila(a, respuesta=FERNET), _fila(d, respuesta=FERNET)])
    ref = encuestas.obtener(ctx.boveda, encuesta["id"])["ref_estudio"]
    _borrar(ctx, actor, segunda)
    assert _respuestas(ctx.semantica, ref) == {a: ("Whisky", primera["id"])}


def test_borrar_una_carga_anterior_no_toca_la_vigente(ctx, actor, incremental, gente):
    segunda, cuarta = incremental["cargas"][1], incremental["cargas"][3]
    _borrar(ctx, actor, segunda)
    vigentes = _respuestas(ctx.semantica, incremental["ref"])
    assert vigentes == {gente[n]: ("Fernet", cuarta["id"]) for n in "ABC"}
    assert _cuantas(ctx.semantica, "respuesta_carga") == 7


def test_el_valor_anterior_conserva_su_unidad_y_se_va_con_su_carga(ctx, actor, gente):
    """«Whisky» solo lo usa el historial de la primera carga: su unidad se
    conserva (volver a ese valor no paga otro embedding) y se borra cuando se
    borra esa carga y ya nadie la usa."""
    encuesta = _encuesta(ctx)
    a = gente["A"]
    primera = _cargar(ctx, actor, encuesta, "Uno", [_fila(a, respuesta=WHISKY)])
    _cargar(ctx, actor, encuesta, "Dos", [_fila(a, respuesta=FERNET)])

    def unidades_con(texto):
        return db.una(ctx.semantica, "select count(*)::int as n from unidad_evidencia "
                                     " where texto_embebido like %s", (f"%{texto}",))["n"]

    assert unidades_con("Whisky") == 1
    _borrar(ctx, actor, primera)
    assert unidades_con("Whisky") == 0
    assert unidades_con("Fernet") == 1


# ── R-HIS.3 · La vista previa dice qué se pierde ──────────────────────

def test_la_vista_previa_separa_las_que_vuelven_de_las_que_se_eliminan(ctx, actor, gente):
    encuesta = _encuesta(ctx)
    a, b, d = gente["A"], gente["B"], gente["D"]
    _cargar(ctx, actor, encuesta, "Uno", [_fila(a), _fila(b)])
    segunda = _cargar(ctx, actor, encuesta, "Dos", [_fila(a), _fila(b), _fila(d)])
    _, previa = cargas_borrado.lanzar(ctx.boveda, ctx.semantica, segunda["id"],
                                      solo_revisar=True)
    assert previa["respuestas_que_vuelven"] == 2
    assert previa["respuestas_a_borrar"] == 1                # la de D, solo en «Dos»
    assert previa["resumen_respuestas"] == (
        "2 respuesta(s) vuelven al valor de una carga anterior · 1 se eliminan porque "
        "solo estaban en esta carga")


# ── R-HIS.4 · Borrar una encuesta ─────────────────────────────────────

def test_borrar_la_encuesta_elimina_su_historial(ctx, actor, incremental):
    otra = _encuesta(ctx, "Otra")
    _cargar(ctx, actor, otra, "Única", [_fila(_persona(ctx.boveda, "E", email="e@ej.uy"))])
    ctx.boveda.commit()
    encuestas_borrado.lanzar(ctx.boveda, ctx.semantica, incremental["encuesta"]["id"],
                             confirmacion=incremental["encuesta"]["nombre"])
    # Queda solo el historial de la otra encuesta.
    assert _cuantas(ctx.semantica, "respuesta_carga") == 1


# ── Lo que también se lleva el historial ──────────────────────────────

def test_el_retiro_de_uso_semantico_de_un_estudio_se_lleva_su_historial(
        ctx, incremental, gente):
    semantica.borrar_respuestas_de_estudio(ctx.semantica, gente["A"], incremental["ref"])
    quedan = db.una(ctx.semantica, """select count(*)::int as n from respuesta_carga h
                                        join individuo i on i.id = h.individuo_id
                                       where i.id_persona = %s""", (gente["A"],))["n"]
    assert quedan == 0
    assert _cuantas(ctx.semantica, "respuesta_carga") == 5


def test_el_recall_exacto_no_devuelve_unidades_que_solo_usa_el_historial(ctx, actor, gente):
    encuesta = _encuesta(ctx)
    _cargar(ctx, actor, encuesta, "Uno", [_fila(gente["A"], respuesta=WHISKY)])
    _cargar(ctx, actor, encuesta, "Dos", [_fila(gente["A"], respuesta=FERNET)])
    vector = db.una(ctx.semantica, "select embedding::text as v from unidad_evidencia "
                                   " where texto_embebido like %s", ("%Whisky",))["v"]
    vecinos = semantica._vecinos_exactos(ctx.semantica, vector, 10, None)
    con_respuesta = {f["unidad_id"] for f in db.todas(
        ctx.semantica, "select unidad_id from respuesta")}
    assert {v["id"] for v in vecinos} <= con_respuesta


# ── R-HIS.5 · En qué cargas apareció cada respuesta ───────────────────

def test_la_ficha_dice_en_que_cargas_aparecio_y_si_cambio(ctx, actor, incremental, gente):
    salida = ficha.respuestas_con_registro(ctx.boveda, ctx.semantica, gente["A"],
                                           actor=actor("admin"))
    [respuesta] = salida["items"]
    assert [h["nombre"] for h in respuesta["cargas"]] == [
        "Parcial 1", "Parcial 2", "Parcial 3", "Final"]
    assert [h["valor_texto"] for h in respuesta["cargas"]] == [
        "Fernet", "Fernet", "Whisky", "Fernet"]
    assert respuesta["cambio_entre_cargas"] is True
    c = ficha.respuestas(ctx.semantica, gente["C"])["items"][0]
    assert len(c["cargas"]) == 2 and c["cambio_entre_cargas"] is True


# ── Migración ─────────────────────────────────────────────────────────

def _script():
    spec = importlib.util.spec_from_file_location(
        "migrar_historial_de_respuestas", RAIZ / "scripts" / "migrar_historial_de_respuestas.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_migracion_crea_el_historial_de_las_vigentes_y_es_reejecutable(
        ctx, incremental):
    db.ejecutar(ctx.semantica, "delete from respuesta_carga")
    ctx.semantica.commit()
    script = _script()
    sql = script._sin_transaccion(script.MIGRACION.read_text(encoding="utf-8"))
    for _ in range(2):
        db.ejecutar(ctx.semantica, sql)
    ctx.semantica.commit()
    # Una fila por respuesta vigente, en su carga actual: lo anterior no se
    # guardó y no se inventa.
    assert _cuantas(ctx.semantica, "respuesta_carga") == 3
    assert script.sin_historial(ctx.semantica) == 0
