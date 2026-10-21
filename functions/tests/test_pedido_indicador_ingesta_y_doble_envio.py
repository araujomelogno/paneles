"""PEDIDO — Indicador de procesamiento al ingestar, guarda contra el doble
envío, y la fecha del estudio.

Tres cosas, y este archivo prueba las dos de servidor más la fecha:

* **R-ING.2** — una sola ingesta en curso por destino. La guarda es el
  índice único parcial de `boveda/0029`, así que se prueba también con dos
  pedidos **simultáneos**, cada uno en su conexión: un `select` previo en
  Python les contestaría «no hay ninguna» a los dos.
* **R-ING.3** — el pedido registra cuánto tardó cada parte de la
  preparación, en el trabajo y en el log.
* **La fecha del estudio** — la ingesta de una carga dejaba el cuestionario
  sin fecha porque leía el campo con el nombre de las encuestas
  (`fecha_campo`) en vez del de las cargas (`fecha_estudio`), y la ficha
  mostraba «sin fecha de campo».

El estado de procesando del modal (R-ING.1) se prueba en un navegador, en
`test_modal_de_ingesta_procesando.py`.

Corre contra los dos stores reales.
"""

import datetime
import threading

import pytest

from panel_api import cargas, db, diferida, ficha, ingesta, ruteo
from panel_api.errores import IngestaEnCurso

from conftest import _conectar
from test_ingesta_diferida import _filas, _plan, _procesar_todo, ola  # noqa: F401
from test_r_org_origen_y_ambitos import (
    DEMOGRAFICAS, EVIDENCIA, PREGUNTAS, _cargar, _fila,
)


@pytest.fixture
def carga(conn_boveda):
    fila = cargas.crear(conn_boveda, "Ómnibus agosto 2026",
                        fecha_estudio="2026-08-15",
                        tipo_datos_origen="encuesta",
                        modo_relevamiento="telefonico")
    conn_boveda.commit()
    return fila


def _cuerpo(filas=None, **extra):
    return {"filas": filas or [_fila(i) for i in range(1, 4)],
            "columna_id": "ID", "origen": "omnibus", "tipo_identificador": "alias",
            "modo": "crear_individuos", "preguntas": PREGUNTAS,
            "demograficas": DEMOGRAFICAS, "evidencia_consentimiento": EVIDENCIA,
            "proponer_tematicas": False, **extra}


def _pedir(ctx, actor, carga, cuerpo):
    return ruteo.despachar("POST", f"/cargas/{carga['id']}/ingesta", cuerpo, {},
                           actor("analista"), ctx)


def _activos(conn, carga):
    return db.todas(
        conn,
        """select id from ingesta_trabajo
            where destino_tipo = 'carga' and destino_id = %s
              and estado in ('encolada', 'procesando')""",
        (carga["id"],))


def _personas(conn):
    return db.una(conn, "select count(*) as n from persona")["n"]


# ════════════════════════════════════════════════════════════════════
#  R-ING.2 · Una sola ingesta en curso por carga
# ════════════════════════════════════════════════════════════════════

def test_un_segundo_pedido_con_una_ingesta_en_curso_se_rechaza(ctx, actor, carga):
    status, primera = _pedir(ctx, actor, carga, _cuerpo())
    assert status == 202

    with pytest.raises(IngestaEnCurso) as rechazo:
        _pedir(ctx, actor, carga, _cuerpo())

    # Dice que ya hay una y lleva a su seguimiento: el trabajo que existe.
    error = rechazo.value
    assert error.status == 409
    assert error.codigo == "ingesta_en_curso"
    assert "en curso" in error.mensaje
    assert error.detalle["trabajo_id"] == primera["trabajo_id"]
    assert error.detalle["destino"] == {"tipo": "carga", "id": carga["id"]}
    assert len(_activos(ctx.boveda, carga)) == 1


def test_el_rechazo_llega_antes_de_crear_a_nadie(ctx, actor, carga):
    """El segundo pedido no vuelve a leer el archivo ni a dar de alta
    personas: se rechaza al entrar."""
    _pedir(ctx, actor, carga, _cuerpo())
    antes = _personas(ctx.boveda)

    otras = [_fila(i) for i in range(10, 14)]
    with pytest.raises(IngestaEnCurso):
        _pedir(ctx, actor, carga, _cuerpo(otras))
    assert _personas(ctx.boveda) == antes


def test_con_una_ingesta_en_curso_tampoco_se_revisa(ctx, actor, carga):
    """La revisión es el primer paso del mismo pedido: con una carga en curso
    no hay qué revisar, y la pantalla va al seguimiento desde ahí."""
    _pedir(ctx, actor, carga, _cuerpo())
    with pytest.raises(IngestaEnCurso):
        _pedir(ctx, actor, carga, _cuerpo(solo_revisar=True))


@pytest.mark.parametrize("estado", ["terminada", "terminada_con_errores", "fallida"])
def test_una_ingesta_terminada_o_fallida_no_bloquea_la_siguiente(
        ctx, actor, carga, estado):
    _, primera = _pedir(ctx, actor, carga, _cuerpo())
    db.ejecutar(ctx.boveda, "update ingesta_trabajo set estado = %s where id = %s",
                (estado, primera["trabajo_id"]))
    ctx.boveda.commit()

    status, segunda = _pedir(ctx, actor, carga, _cuerpo())
    assert status == 202
    assert segunda["trabajo_id"] != primera["trabajo_id"]


def test_una_carga_procesada_entera_admite_una_nueva(ctx, actor, carga):
    """El camino real: los lotes terminan, el trabajo se cierra solo y la
    carga queda libre."""
    _pedir(ctx, actor, carga, _cuerpo())
    _procesar_todo(ctx.boveda, ctx.semantica, ctx.embeddings, ctx.encolador)
    assert not _activos(ctx.boveda, carga)

    status, _ = _pedir(ctx, actor, carga, _cuerpo())
    assert status == 202


def test_otra_carga_no_queda_bloqueada(ctx, actor, carga, conn_boveda):
    """La guarda es por destino, no global."""
    otra = cargas.crear(conn_boveda, "Tracking", tipo_datos_origen="encuesta",
                        modo_relevamiento="web")
    conn_boveda.commit()
    _pedir(ctx, actor, carga, _cuerpo())
    status, _ = _pedir(ctx, actor, otra, _cuerpo([_fila(i) for i in range(20, 23)]))
    assert status == 202


def test_si_el_select_previo_no_lo_ve_lo_frena_el_indice(conn_boveda, carga):
    """El chequeo temprano es una cortesía; la garantía es el índice. Con
    el chequeo saltado —como le pasa a un pedido que entra entre el `select`
    del otro y su `insert`— `encolar` igual rechaza, con el mismo error."""
    primera = diferida.encolar(conn_boveda, "carga", carga["id"], _plan(),
                               _filas(2), encolador=diferida.EncoladorEnMemoria())
    with pytest.raises(IngestaEnCurso) as rechazo:
        diferida.encolar(conn_boveda, "carga", carga["id"], _plan(), _filas(2),
                         encolador=diferida.EncoladorEnMemoria())
    assert rechazo.value.detalle["trabajo_id"] == primera["trabajo_id"]
    # Lo que la transacción tenía antes del rechazo sigue usable.
    assert db.una(conn_boveda, "select 1 as uno")["uno"] == 1


def test_dos_pedidos_simultaneos_dejan_un_solo_trabajo(dsn_boveda, conn_boveda, carga):
    """Dos pestañas, o un doble clic muy rápido: cada pedido en su conexión,
    largados a la vez. Gane el que gane, queda **un** trabajo y el otro
    pedido recibe el rechazo que lleva a él."""
    largada = threading.Barrier(2)
    resultados = []

    def pedir():
        conn = _conectar(dsn_boveda)
        try:
            largada.wait()
            salida = diferida.encolar(conn, "carga", carga["id"], _plan(),
                                      _filas(2),
                                      encolador=diferida.EncoladorEnMemoria())
            resultados.append(("encolada", salida["trabajo_id"]))
        except IngestaEnCurso as error:
            conn.rollback()
            resultados.append(("rechazada", error.detalle["trabajo_id"]))
        finally:
            conn.close()

    hilos = [threading.Thread(target=pedir) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)

    assert sorted(r[0] for r in resultados) == ["encolada", "rechazada"]
    encolada = next(r[1] for r in resultados if r[0] == "encolada")
    rechazada = next(r[1] for r in resultados if r[0] == "rechazada")
    assert rechazada == encolada
    assert [f["id"] for f in _activos(conn_boveda, carga)] == [encolada]


def test_un_reproceso_en_curso_tambien_cuenta(conn_boveda, carga):
    """El reproceso usa el mismo destino: dos procesos sobre las mismas
    respuestas podían pisarse, y el índice los cubre a los dos."""
    diferida.encolar(conn_boveda, "carga", carga["id"],
                     _plan(operacion="reproceso"), [1, 2],
                     encolador=diferida.EncoladorEnMemoria())
    with pytest.raises(IngestaEnCurso):
        diferida.exigir_sin_trabajo_activo(conn_boveda, "carga", carga["id"])


# ════════════════════════════════════════════════════════════════════
#  R-ING.3 · Cuánto tarda cada parte de la preparación
# ════════════════════════════════════════════════════════════════════

def test_el_trabajo_registra_cuanto_tardo_cada_parte(ctx, actor, carga, capsys):
    _, salida = _pedir(ctx, actor, carga, _cuerpo())
    estado = diferida.estado(ctx.boveda, salida["trabajo_id"])

    partes = estado["preparacion"]
    for parte in ("despivotar", "crear_individuos", "armar_plan",
                  "guardar_plan", "persistir_lotes", "encolar", "total"):
        assert isinstance(partes.get(parte), int) and partes[parte] >= 0, parte
    assert partes["total"] >= max(v for k, v in partes.items() if k != "total")

    # Y al log, con números y nada más: ni filas ni nombres.
    linea = next(l for l in capsys.readouterr().out.splitlines()
                 if l.startswith("[ingesta] preparación"))
    assert f"trabajo={salida['trabajo_id']}" in linea
    assert f"destino=carga:{carga['id']}" in linea
    assert "crear_individuos=" in linea
    assert "Persona 1" not in linea and "20-1" not in linea


# ════════════════════════════════════════════════════════════════════
#  La fecha del estudio llega al cuestionario
# ════════════════════════════════════════════════════════════════════

def _fecha_del_cuestionario(conn_semantica, ref_estudio):
    fila = db.una(conn_semantica,
                  "select fecha_campo from cuestionario where ref_estudio = %s",
                  (str(ref_estudio),))
    return fila["fecha_campo"] if fila else "sin cuestionario"


def test_una_carga_ingestada_deja_su_fecha_en_el_cuestionario(ctx, carga):
    _cargar(ctx, carga, [_fila(1), _fila(2)])
    assert _fecha_del_cuestionario(ctx.semantica, carga["ref_estudio"]) \
        == datetime.date(2026, 8, 15)


def test_la_ficha_muestra_la_fecha_en_las_respuestas_de_la_carga(ctx, carga):
    creacion, _ = _cargar(ctx, carga, [_fila(1)])
    id_persona = db.una(ctx.boveda, "select id_persona from persona")["id_persona"]

    items = ficha.respuestas(ctx.semantica, id_persona)["items"]
    assert items
    assert {i["fecha_campo"] for i in items} == {"2026-08-15"}


def test_una_carga_sin_fecha_del_estudio_no_inventa_una(ctx, conn_boveda):
    sin_fecha = cargas.crear(conn_boveda, "Sin fecha", tipo_datos_origen="encuesta",
                             modo_relevamiento="web")
    _cargar(ctx, sin_fecha, [_fila(1)])
    assert _fecha_del_cuestionario(ctx.semantica, sin_fecha["ref_estudio"]) is None


def test_una_encuesta_sigue_dejando_su_fecha_de_campo(
        conn_boveda, conn_semantica, proveedor, ola, encolador):  # noqa: F811
    """No regresión: el camino de las encuestas no cambió de resultado."""
    diferida.encolar(conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(),
                     _filas(2), encolador=encolador)
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)
    assert _fecha_del_cuestionario(conn_semantica, ola["encuesta"]["ref_estudio"]) \
        == datetime.date(2026, 3, 1)


def test_el_estudio_de_cada_destino_sale_de_un_solo_lugar(conn_boveda, carga, ola):  # noqa: F811
    """Una encuesta y una carga guardan la fecha con nombres distintos; la
    traducción es `ingesta.estudio_del_destino` y devuelve lo mismo para
    las dos."""
    de_carga = ingesta.estudio_del_destino(conn_boveda, "carga", carga["id"])
    de_encuesta = ingesta.estudio_del_destino(conn_boveda, "encuesta",
                                              ola["encuesta"]["id"])
    assert set(de_carga) == set(de_encuesta) == {
        "ref_estudio", "nombre", "fecha_campo", "panel_id"}
    assert de_carga["fecha_campo"] == datetime.date(2026, 8, 15)
    assert de_carga["panel_id"] is None
    assert de_encuesta["fecha_campo"] == datetime.date(2026, 3, 1)
    assert de_encuesta["panel_id"] == ola["panel"]["id"]


def test_ninguna_ingesta_lee_el_estudio_por_nombre_de_campo():
    """El bug fue leer el dict de una carga con los nombres de una encuesta.
    Las dos ingestas piden el estudio a `estudio_del_destino` y ninguna arma
    el dict a mano."""
    import inspect

    from panel_api import encuestas

    for modulo in (cargas, encuestas):
        fuente = inspect.getsource(modulo.ingestar)
        assert "estudio_del_destino(" in fuente, modulo.__name__
        assert '"fecha_campo":' not in fuente, modulo.__name__


def test_editar_la_fecha_del_estudio_actualiza_el_cuestionario(ctx, actor, carga):
    _cargar(ctx, carga, [_fila(1)])
    ctx.semantica.commit()

    ruteo.despachar("PATCH", f"/cargas/{carga['id']}",
                    {"fecha_estudio": "2026-09-01"}, {}, actor("analista"), ctx)
    assert _fecha_del_cuestionario(ctx.semantica, carga["ref_estudio"]) \
        == datetime.date(2026, 9, 1)

    # Vaciarla también se propaga: la ficha no puede seguir mostrando una
    # fecha que el analista borró.
    ruteo.despachar("PATCH", f"/cargas/{carga['id']}",
                    {"fecha_estudio": None}, {}, actor("analista"), ctx)
    assert _fecha_del_cuestionario(ctx.semantica, carga["ref_estudio"]) is None


def test_editar_otra_cosa_no_toca_el_store_semantico(ctx, actor, carga):
    _cargar(ctx, carga, [_fila(1)])
    ctx.semantica.commit()
    ctx.abrio_semantica = False

    ruteo.despachar("PATCH", f"/cargas/{carga['id']}",
                    {"publico_objetivo": "Mayores de 18"}, {}, actor("analista"), ctx)
    assert not ctx.abrio_semantica


def test_editar_la_fecha_de_una_carga_sin_ingestar_no_falla(ctx, actor, carga):
    _, salida = ruteo.despachar("PATCH", f"/cargas/{carga['id']}",
                                {"fecha_estudio": "2026-09-01"}, {},
                                actor("analista"), ctx)
    assert salida["fecha_estudio"] == "2026-09-01"


def test_completar_las_cargas_anteriores_sin_pisar_fechas(ctx, carga, conn_boveda):
    """La operación única: lo ingestado antes de la corrección quedó sin
    fecha. Se completa donde está vacía y nada más."""
    vieja = cargas.crear(conn_boveda, "Ingestada antes", fecha_estudio="2026-05-10",
                         tipo_datos_origen="encuesta", modo_relevamiento="web")
    con_fecha = cargas.crear(conn_boveda, "Ya tenía fecha", fecha_estudio="2026-06-01",
                             tipo_datos_origen="encuesta", modo_relevamiento="web")
    _cargar(ctx, vieja, [_fila(1)])
    _cargar(ctx, con_fecha, [_fila(2)], origen="otro")
    _cargar(ctx, carga, [_fila(3)], origen="tercero")
    # Como quedaban antes: la vieja sin fecha; la otra, con una distinta a
    # la de su carga, que alguien puso a mano y no se pisa.
    db.ejecutar(ctx.semantica, "update cuestionario set fecha_campo = null "
                "where ref_estudio = %s", (str(vieja["ref_estudio"]),))
    db.ejecutar(ctx.semantica, "update cuestionario set fecha_campo = '2026-06-05' "
                "where ref_estudio = %s", (str(con_fecha["ref_estudio"]),))

    salida = cargas.completar_fechas(ctx.boveda, ctx.semantica)

    assert [c["id"] for c in salida["completadas"]] == [vieja["id"]]
    assert _fecha_del_cuestionario(ctx.semantica, vieja["ref_estudio"]) \
        == datetime.date(2026, 5, 10)
    assert _fecha_del_cuestionario(ctx.semantica, con_fecha["ref_estudio"]) \
        == datetime.date(2026, 6, 5)
    # Correrla de nuevo no encuentra nada.
    assert cargas.completar_fechas(ctx.boveda, ctx.semantica)["completadas"] == []


def test_una_preparacion_larga_se_avisa_en_el_log(conn_boveda, carga, capsys,
                                                    monkeypatch):
    """Lo que crece con la base es `crear_individuos` (D77). Si una
    preparación se acerca al límite de la API, el log lo dice y nombra la
    parte que más tardó."""
    monkeypatch.setattr(diferida, "AVISO_PREPARACION_MS", 0)
    cronometro = diferida.Cronometro()
    cronometro.partes["crear_individuos"] = 5000
    diferida.encolar(conn_boveda, "carga", carga["id"], _plan(), _filas(2),
                     encolador=diferida.EncoladorEnMemoria(), cronometro=cronometro)
    salida = capsys.readouterr().out
    assert "[ingesta] aviso:" in salida
    assert "crear_individuos = 5 s" in salida
