"""R-CAR.3, R-CAR.4 y R-AUD.1 (SPEC_clientes_lista_negra_y_mejoras).

* De qué otras cargas vienen los reutilizados de una carga, y cuántos no
  venían de ninguna.
* La sección «Cargas» de la ficha: en qué cargas aparece cada persona, con
  sus clientes y las temáticas que respondió en cada una, y por dónde entró
  si no fue por una carga.
* Las reidentificaciones en la auditoría, por evento y con filtros.

Corre contra Postgres de verdad.
"""

import pytest

from panel_api import auditoria, cargas, db, personas, ruteo
from panel_api.errores import DatosInvalidos, NoEncontrado, SinPermiso

from test_r_org_origen_y_ambitos import _cargar, _fila, _panelista


# ════════════════════════════════════════════════════════════════════
#  R-CAR.3 · El desglose de los reutilizados
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def tres_cargas(ctx):
    """A trae a 1, 2 y 3. B trae a 1 y 2 (reutilizados de A) y a 4. C trae
    a 1 (en A y B), a 4 (en B), a 5 (alta manual previa) y a 6 (nuevo)."""
    a = cargas.crear(ctx.boveda, "Ómnibus junio", fecha_estudio="2026-06-04")
    b = cargas.crear(ctx.boveda, "Tracking julio", fecha_estudio="2026-07-10")
    c = cargas.crear(ctx.boveda, "Centel", fecha_estudio="2026-08-01",
                     clientes=["Antel"])
    _cargar(ctx, a, [_fila(1), _fila(2), _fila(3)])
    _cargar(ctx, b, [_fila(1), _fila(2), _fila(4)])
    manual = _panelista(ctx.boveda, "20-5", nombre="Persona 5")
    _cargar(ctx, c, [_fila(1), _fila(4), _fila(5), _fila(6)])
    return {"a": a, "b": b, "c": c, "manual": str(manual)}


def test_el_desglose_cuenta_por_carga_de_mayor_a_menor(ctx, actor, tres_cargas):
    c = tres_cargas["c"]
    _, desglose = ruteo.despachar("GET", f"/cargas/{c['id']}/reutilizados", {}, {},
                                  actor("analista"), ctx)

    assert desglose["reutilizados"] == 3            # 1, 4 y 5
    # Persona 1 estaba en A y en B: cuenta en las dos filas.
    assert [(f["nombre"], f["personas"]) for f in desglose["cargas"]] == [
        ("Tracking julio", 2), ("Ómnibus junio", 1)]
    assert desglose["cargas"][0]["fecha_estudio"] == "2026-07-10"
    assert sum(f["personas"] for f in desglose["cargas"]) > desglose["reutilizados"] - 1
    assert desglose["nota"] == "Una persona puede aparecer en más de una carga."
    # Persona 5 no venía de ninguna carga: su fila propia.
    assert desglose["sin_carga_previa"] == 1
    assert "landing, alta manual o encuesta" in desglose["sin_carga_previa_etiqueta"]


def test_una_carga_posterior_no_cuenta_como_origen(ctx, actor, tres_cargas):
    """B reutilizó a 1 y 2 de A; que después estén en C no explica B."""
    _, desglose = ruteo.despachar(
        "GET", f"/cargas/{tres_cargas['b']['id']}/reutilizados", {}, {},
        actor("analista"), ctx)
    assert [(f["nombre"], f["personas"]) for f in desglose["cargas"]] == [
        ("Ómnibus junio", 2)]
    assert desglose["sin_carga_previa"] == 0


def test_el_desglose_no_lista_personas(ctx, actor, tres_cargas):
    _, desglose = ruteo.despachar(
        "GET", f"/cargas/{tres_cargas['c']['id']}/reutilizados", {}, {},
        actor("analista"), ctx)
    assert "id_persona" not in repr(desglose)
    assert "Persona" not in repr(desglose)


def test_una_carga_que_no_existe(ctx, actor):
    with pytest.raises(NoEncontrado):
        ruteo.despachar("GET", "/cargas/999999/reutilizados", {}, {}, actor("analista"), ctx)


# ════════════════════════════════════════════════════════════════════
#  R-CAR.4 · Las cargas en la ficha
# ════════════════════════════════════════════════════════════════════

def _id(conn, documento):
    return str(db.una(conn, "select id_persona from persona where documento = %s",
                      (documento,))["id_persona"])


def test_la_ficha_muestra_sus_cargas_la_mas_reciente_primero(ctx, actor, tres_cargas):
    id_persona = _id(ctx.boveda, "20-1")
    _, ficha = ruteo.despachar("GET", f"/panelistas/{id_persona}", {}, {},
                               actor("operaciones"), ctx)
    filas = ficha["cargas"]["cargas"]
    assert [(f["nombre"], f["origen"]) for f in filas] == [
        ("Centel", "reutilizada"), ("Tracking julio", "reutilizada"),
        ("Ómnibus junio", "creada")]
    assert [c["nombre"] for c in filas[0]["clientes"]] == ["antel"]
    assert filas[1]["clientes"] == []
    assert filas[0]["fecha_estudio"] == "2026-08-01"
    # La creó una carga: no hay otro ingreso que contar.
    assert ficha["cargas"]["otros_ingresos"] == []


def test_las_tematicas_van_en_la_carga_en_que_las_respondio(ctx, tres_cargas):
    id_persona = _id(ctx.boveda, "20-1")
    tematica = db.una(ctx.boveda, "insert into tematica (nombre) values ('bebidas') "
                                  "returning id")["id"]
    db.ejecutar(ctx.boveda,
                "insert into carga_tematica (carga_id, tematica_id, origen) "
                "values (%s, %s, 'manual')", (tres_cargas["b"]["id"], tematica))
    db.ejecutar(ctx.boveda,
                "insert into persona_tematica (id_persona, tematica_id, carga_id, "
                "preguntas_respondidas) values (%s, %s, %s, 1)",
                (id_persona, tematica, tres_cargas["b"]["id"]))
    salida = cargas.cargas_de_persona(ctx.boveda, id_persona)
    por_carga = {f["nombre"]: [t["nombre"] for t in f["tematicas"]] for f in salida["cargas"]}
    assert por_carga == {"Centel": [], "Tracking julio": ["bebidas"], "Ómnibus junio": []}


def test_quien_entro_por_alta_manual_lo_dice(ctx, tres_cargas):
    salida = cargas.cargas_de_persona(ctx.boveda, tres_cargas["manual"])
    assert [f["origen"] for f in salida["cargas"]] == ["reutilizada"]
    assert [i["via"] for i in salida["otros_ingresos"]] == [cargas.INGRESO_ALTA_MANUAL]


def test_quien_entro_por_la_landing_lo_dice(ctx, tres_cargas):
    db.ejecutar(ctx.boveda, """
        insert into inscripcion (nombre, email, finalidades, version_texto, estado,
                                 id_persona, resuelto_en)
        values ('Persona 5', 'p5@ej.uy', '{contacto_participacion}', 'v', 'aprobada',
                %s, now())""", (tres_cargas["manual"],))
    salida = cargas.cargas_de_persona(ctx.boveda, tres_cargas["manual"])
    assert [i["via"] for i in salida["otros_ingresos"]] == [cargas.INGRESO_LANDING]


def test_sin_cargas_ni_rastro_es_alta_manual_y_avisa(conn_boveda):
    id_persona = _panelista(conn_boveda, "30-1")
    salida = cargas.cargas_de_persona(conn_boveda, id_persona)
    assert salida["cargas"] == [] and salida["aviso"] == cargas.SIN_VINCULO_REGISTRADO
    assert salida["otros_ingresos"][0]["etiqueta"] == "Alta manual"


# ════════════════════════════════════════════════════════════════════
#  R-AUD.1 · Las reidentificaciones en la auditoría
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def registro(conn_boveda, actor):
    ids = [str(_panelista(conn_boveda, f"40-{i}", nombre=f"P{i}")) for i in range(3)]
    auditoria.registrar_reidentificacion(
        conn_boveda, ids, actor=actor("analista", email="ana@equipos.com.uy"),
        motivo="consulta", contexto={"ruta": "POST /reidentificacion"})
    auditoria.registrar_reidentificacion(
        conn_boveda, ids[:1], actor=actor("operaciones", email="op@equipos.com.uy"),
        motivo="ficha", contexto={"ruta": "GET /panelistas/{id_persona}"})
    conn_boveda.commit()
    return ids


def _eventos(ctx, fabrica, rol="dpo", **consulta):
    return ruteo.despachar("GET", "/reidentificacion", {},
                           {"eventos": "1", **consulta}, fabrica(rol), ctx)[1]


def test_cada_evento_dice_quien_cuando_motivo_y_a_cuantos(ctx, actor, registro):
    salida = _eventos(ctx, actor)
    assert salida["total"] == 2
    por_motivo = {e["motivo"]: e for e in salida["items"]}
    consulta = por_motivo["consulta"]
    assert consulta["actor_email"] == "ana@equipos.com.uy"
    assert consulta["personas"] == 3 and sorted(consulta["ids_persona"]) == sorted(registro)
    assert consulta["motivo_etiqueta"] and consulta["creado_en"]
    assert por_motivo["ficha"]["personas"] == 1


def test_filtra_por_usuario_fecha_y_panelista(ctx, actor, registro):
    filtro_actor = {"actor": "op@"}
    assert [e["motivo"] for e in _eventos(ctx, actor, **filtro_actor)["items"]] == ["ficha"]
    # «¿Quién vio los datos de esta persona?», por id o por documento.
    assert _eventos(ctx, actor, panelista=registro[0])["total"] == 2
    assert _eventos(ctx, actor, panelista="40-2")["total"] == 1
    # El evento sale completo: a cuántos alcanzó, no solo a esa persona.
    assert _eventos(ctx, actor, panelista="40-2")["items"][0]["personas"] == 3
    hoy = db.una(ctx.boveda, "select current_date::text as d")["d"]
    assert _eventos(ctx, actor, desde=hoy, hasta=hoy)["total"] == 2
    assert _eventos(ctx, actor, hasta="2000-01-01")["total"] == 0
    with pytest.raises(DatosInvalidos):
        _eventos(ctx, actor, desde="ayer")


def test_pagina(ctx, actor, registro):
    primera = _eventos(ctx, actor, limite="1")
    segunda = _eventos(ctx, actor, limite="1", desde_fila="1")
    assert primera["total"] == 2 and len(primera["items"]) == 1
    assert primera["items"][0]["evento_id"] != segunda["items"][0]["evento_id"]


@pytest.mark.parametrize("rol", ["analista", "operaciones"])
def test_solo_administracion_y_proteccion_de_datos(ctx, actor, registro, rol):
    with pytest.raises(SinPermiso):
        _eventos(ctx, actor, rol=rol)
    assert _eventos(ctx, actor, rol="admin")["total"] == 2


def test_sigue_la_lista_por_fila_sin_la_bandera(ctx, actor, registro):
    _, salida = ruteo.despachar("GET", "/reidentificacion", {}, {}, actor("dpo"), ctx)
    assert len(salida["items"]) == 4


def test_la_paginacion_de_panelistas_es_estable(ctx, actor):
    """R-PAN.1 — la pantalla pide páginas con `limite` y `desde`; el total
    no cambia entre páginas y no se repite nadie."""
    for i in range(7):
        _panelista(ctx.boveda, f"50-{i}", nombre=f"Q{i}")
    vistos = []
    for desde in (0, 3, 6):
        _, pagina = ruteo.despachar("GET", "/panelistas", {},
                                    {"limite": "3", "desde": str(desde)},
                                    actor("analista"), ctx)
        assert pagina["total"] == 7
        vistos += [p["id_persona"] for p in pagina["items"]]
    assert len(vistos) == len(set(vistos)) == 7
    assert personas.listar(ctx.boveda, limite=3, desplazamiento=6)["total"] == 7
