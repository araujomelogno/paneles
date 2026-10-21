"""R-TEM.6 — Las temáticas de cada panelista.

`specs/SPEC_tematicas_y_seccion_cargas.md`, R-TEM.6. Una persona tiene la
temática Y si **respondió** al menos una de las preguntas relacionadas con Y
en alguna de sus cargas; haber estado en la carga no alcanza. El archivo
sigue el Definition of Done: quién la tiene y quién no, la no respuesta, el
recálculo al editar, el filtro de la consulta demográfica, las especiales
(ni columna ni exportación, consulta por rol) y la baja.

Corre contra Postgres de verdad: la relación pregunta ↔ temática, las
cascadas desde la persona y la carga y el filtro son SQL de la bóveda.
"""

import inspect

import pytest

from panel_api import (
    bajas, cargas, consultas, db, diferida, ruteo, tematicas,
)
from panel_api.errores import SinPermiso

from test_r_org_origen_y_ambitos import EVIDENCIA, _cargar, _fila

PREGUNTAS = [
    {"codigo": "T1", "texto": "¿Con qué compañía tiene su plan de celular?",
     "tipo": "cerrada", "opciones": {"1": "Antel", "2": "Claro", "99": "NS/NC"},
     "orden": 1},
    {"codigo": "T2", "texto": "¿Cuántas horas por día usa el celular?",
     "tipo": "abierta", "orden": 2},
    {"codigo": "F1", "texto": "¿Fuma cigarrillos?", "tipo": "cerrada",
     "opciones": {"1": "Sí", "2": "No", "98": "No sabe"}, "orden": 3},
    {"codigo": "SEXO", "texto": "Sexo", "tipo": "cerrada",
     "opciones": {"1": "M", "2": "F"}, "orden": 4},
]
DEMOGRAFICAS = {"NOM": "nombre", "DOC": "documento", "SEXO": "sexo"}

# Cuatro personas, cuatro casos.
FILAS = [
    # respondió las dos de telefonía y la de tabaco
    {"ID": "r1", "NOM": "Ana", "DOC": "30-1", "SEXO": "2", "CONS": "1",
     "T1": "1", "T2": "Unas tres horas", "F1": "2"},
    # estuvo en la carga y no respondió nada de telefonía
    {"ID": "r2", "NOM": "Bruno", "DOC": "30-2", "SEXO": "1", "CONS": "1",
     "T1": "", "T2": "", "F1": "1"},
    # respondió todo con no respuesta: código, etiqueta y texto libre
    {"ID": "r3", "NOM": "Carla", "DOC": "30-3", "SEXO": "2", "CONS": "1",
     "T1": "99", "T2": "No sabe", "F1": "98"},
    # una sola pregunta de telefonía
    {"ID": "r4", "NOM": "Diego", "DOC": "30-4", "SEXO": "1", "CONS": "1",
     "T1": "2", "T2": None, "F1": None},
]

TEMATICAS = [
    {"nombre": "telefonía móvil", "origen": "propuesta_ia",
     "preguntas": ["T1", "T2"]},
    {"nombre": "consumo de tabaco", "origen": "propuesta_ia",
     "preguntas": ["F1"], "es_especial": True},
]


def _ids(conn):
    return {f["documento"]: str(f["id_persona"]) for f in db.todas(
        conn, "select documento, id_persona from persona")}


def _tematicas_de(conn, carga_id=None):
    """`{documento: {temática: preguntas_respondidas}}`."""
    salida = {}
    for f in db.todas(
        conn,
        """select p.documento, t.nombre, pt.preguntas_respondidas
             from persona_tematica pt
             join persona p on p.id_persona = pt.id_persona
             join tematica t on t.id = pt.tematica_id
            where %s::bigint is null or pt.carga_id = %s""",
        (carga_id, carga_id)):
        salida.setdefault(f["documento"], {})[f["nombre"]] = f["preguntas_respondidas"]
    return salida


def _procesar(ctx, salida):
    for indice in range(len(ctx.encolador.encoladas)):
        diferida.procesar_lote(ctx.boveda, ctx.semantica, salida["trabajo_id"],
                               indice, proveedor=ctx.embeddings)


@pytest.fixture
def carga(ctx, actor):
    """El camino de la pantalla: abrir la carga, guardar las temáticas
    revisadas (antes de encolar) y confirmar la ingesta."""
    _, carga = ruteo.despachar(
        "POST", "/cargas", {"nombre": "Hábitos 2026", "fecha_estudio": "2026-09-01",
                            "tipo_datos_origen": "encuesta",
                            "modo_relevamiento": "web"},
        {}, actor("analista"), ctx)
    ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas",
                    {"tematicas": TEMATICAS}, {}, actor("analista"), ctx)
    _, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta",
        {"filas": FILAS, "columna_id": "ID", "origen": "campo",
         "tipo_identificador": "alias", "modo": "crear_individuos",
         "preguntas": PREGUNTAS, "demograficas": DEMOGRAFICAS,
         "evidencia_consentimiento": EVIDENCIA},
        {}, actor("analista"), ctx)
    _procesar(ctx, salida)
    ctx.encolador.encoladas.clear()
    return carga


# ════════════════════════════════════════════════════════════════════
#  Quién tiene la temática
# ════════════════════════════════════════════════════════════════════

def test_quien_respondio_tiene_la_tematica_y_quien_no_respondio_no(ctx, carga):
    """DoD. Bruno estuvo en la carga que cubre telefonía y no respondió
    ninguna de esas preguntas: no la tiene."""
    por_persona = _tematicas_de(ctx.boveda)
    assert por_persona["30-1"] == {"telefonía móvil": 2, "consumo de tabaco": 1}
    assert por_persona["30-2"] == {"consumo de tabaco": 1}
    assert por_persona["30-4"] == {"telefonía móvil": 1}


def test_no_sabe_o_vacio_no_cuenta_como_respuesta(ctx, carga):
    """DoD. Carla respondió todo con no respuesta —el código 99 con su
    etiqueta, «No sabe» en texto libre y el 98—: no tiene ninguna."""
    assert "30-3" not in _tematicas_de(ctx.boveda)


def test_la_regla_de_respondida():
    pregunta = PREGUNTAS[0]
    assert tematicas.respondida(pregunta, "Antel")
    assert not tematicas.respondida(pregunta, "NS/NC")
    assert not tematicas.respondida(pregunta, "99")
    assert not tematicas.respondida({"tipo": "abierta"}, "no sabe")
    assert not tematicas.respondida({"tipo": "abierta"}, "  ")
    # Un 99 en una numérica puede ser una edad.
    assert tematicas.respondida({"tipo": "numerica", "opciones": {"1": "x"}}, "99")
    # La lista de no respuesta de la carga manda.
    assert not tematicas.respondida({"tipo": "abierta"}, "Ignora", ["Ignora"])


def test_el_calculo_al_ingestar_no_lee_el_store_semantico(ctx, carga):
    """`registrar_de_lote` recibe solo la conexión a la bóveda: lo que
    necesita vino con la fila del archivo. Con los mismos datos da lo mismo
    que la ingesta."""
    assert "conn_semantica" not in inspect.signature(
        tematicas.registrar_de_lote).parameters
    ids = _ids(ctx.boveda)
    db.ejecutar(ctx.boveda, "delete from persona_tematica")
    tematicas.registrar_de_lote(
        ctx.boveda, carga["id"],
        [(ids["30-4"], "T1", "Claro"), (ids["30-3"], "T1", "NS/NC")],
        PREGUNTAS, [ids["30-4"], ids["30-3"]])
    assert _tematicas_de(ctx.boveda) == {"30-4": {"telefonía móvil": 1}}


def test_el_resumen_de_la_carga_cuenta_las_tematicas(ctx, actor, carga):
    trabajo = db.una(ctx.boveda, "select id from ingesta_trabajo order by id desc limit 1")
    assert diferida.consolidar(ctx.boveda, trabajo["id"])["tematicas_de_panelistas"] == 4


def test_una_persona_en_varias_cargas_acumula_las_tematicas(ctx, actor, carga):
    otra = cargas.crear(ctx.boveda, "Tracking telefonía", tipo_datos_origen="encuesta",
                        modo_relevamiento="telefonico")
    tematicas.guardar(ctx.boveda, otra["id"], [
        {"nombre": "Telefonía móvil", "preguntas": ["P1"]},
        {"nombre": "fidelidad a la compañía", "preguntas": ["P1"]}])
    # La misma Ana (documento 30-1) y alguien nuevo.
    _cargar(ctx, otra, [_fila(1, documento="30-1"), _fila(2)])
    ids = _ids(ctx.boveda)
    filas = db.todas(ctx.boveda,
                     "select t.nombre, pt.carga_id from persona_tematica pt "
                     "join tematica t on t.id = pt.tematica_id "
                     "where pt.id_persona = %s order by t.nombre, pt.carga_id",
                     (ids["30-1"],))
    assert [(f["nombre"], f["carga_id"]) for f in filas] == [
        ("consumo de tabaco", carga["id"]), ("fidelidad a la compañía", otra["id"]),
        ("telefonía móvil", carga["id"]), ("telefonía móvil", otra["id"])]

    _, ficha = ruteo.despachar("GET", f"/panelistas/{ids['30-1']}", {}, {},
                               actor("admin"), ctx)
    telefonia = [t for t in ficha["tematicas"]["items"] if t["nombre"] == "telefonía móvil"]
    assert {t["carga"] for t in telefonia} == {"Hábitos 2026", "Tracking telefonía"}


# ════════════════════════════════════════════════════════════════════
#  Sincronización
# ════════════════════════════════════════════════════════════════════

def test_editar_las_tematicas_de_la_carga_recalcula_las_de_sus_panelistas(
        ctx, actor, carga):
    """DoD. Si telefonía deja de sustentarse en T1, Diego —que solo respondió
    T1— la pierde; si se quita tabaco de la carga, se va de todos."""
    ruteo.despachar(
        "PUT", f"/cargas/{carga['id']}/tematicas",
        {"tematicas": [{"nombre": "telefonía móvil", "preguntas": ["T2"]}]},
        {}, actor("admin"), ctx)
    assert _tematicas_de(ctx.boveda) == {"30-1": {"telefonía móvil": 1}}

    # Y volver a sumar T1 la devuelve, desde lo que hay en el store
    # semántico (que es lo único que queda de la carga).
    ruteo.despachar(
        "PUT", f"/cargas/{carga['id']}/tematicas",
        {"tematicas": [{"nombre": "telefonía móvil", "preguntas": ["T1", "T2"]},
                       {"nombre": "consumo de tabaco", "preguntas": ["F1"]}]},
        {}, actor("admin"), ctx)
    assert _tematicas_de(ctx.boveda) == {
        "30-1": {"telefonía móvil": 2, "consumo de tabaco": 1},
        "30-2": {"consumo de tabaco": 1},
        "30-4": {"telefonía móvil": 1}}


def test_guardar_sin_preguntas_conserva_la_relacion(ctx, actor, carga):
    """Una lista de nombres sueltos (sin `preguntas`) no borra qué preguntas
    sustentan cada temática."""
    ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas",
                    {"tematicas": ["telefonía móvil", "consumo de tabaco"]},
                    {}, actor("admin"), ctx)
    assert _tematicas_de(ctx.boveda)["30-1"] == {"telefonía móvil": 2,
                                                 "consumo de tabaco": 1}
    _, detalle = ruteo.despachar("GET", f"/cargas/{carga['id']}/tematicas", {}, {},
                                 actor("dpo"), ctx)
    por_nombre = {t["nombre"]: t for t in detalle["tematicas"]}
    assert por_nombre["telefonía móvil"]["preguntas"] == ["T1", "T2"]
    assert por_nombre["telefonía móvil"]["personas"] == 2
    # Las preguntas que se le pueden asociar: las guardadas, sin demográficas.
    assert [p["codigo"] for p in detalle["preguntas"]] == ["T1", "T2", "F1"]


def test_una_carga_anterior_calcula_las_tematicas_desde_el_store_semantico(
        ctx, actor):
    """R-TEM.5 + R-TEM.6 — una carga ingestada antes de las temáticas: al
    guardarlas por primera vez se calculan las de sus panelistas, leyendo por
    `ref_estudio` qué respondió cada individuo. Es la operación única."""
    vieja = cargas.crear(ctx.boveda, "Ómnibus 2025", tipo_datos_origen="encuesta",
                         modo_relevamiento="web")
    _cargar(ctx, vieja, [_fila(1), _fila(2)])
    assert _tematicas_de(ctx.boveda) == {}
    ruteo.despachar("PUT", f"/cargas/{vieja['id']}/tematicas",
                    {"tematicas": [{"nombre": "bebidas", "preguntas": ["P1"]}]},
                    {}, actor("analista"), ctx)
    assert _tematicas_de(ctx.boveda) == {"20-1": {"bebidas": 1}, "20-2": {"bebidas": 1}}


def test_una_bateria_solo_marcadas_no_cuenta_lo_no_marcado(ctx, actor):
    """La decisión sobre `Unchecked` (D75): cuenta, salvo que el analista
    haya elegido «solo lo marcado», porque ahí decidió que no marcar no es
    información y el recálculo —que solo ve lo embebido— no podría contarlo."""
    nueva = cargas.crear(ctx.boveda, "Marcas", tipo_datos_origen="encuesta",
                         modo_relevamiento="web")
    tematicas.guardar(ctx.boveda, nueva["id"], [
        {"nombre": "marcas de celular", "preguntas": ["M1", "M2"]}])
    preguntas = [
        {"codigo": "M1", "texto": "Conoce: Samsung", "tipo": "cerrada",
         "opciones": {"0": "Unchecked", "1": "Checked"}, "solo_marcadas": True},
        {"codigo": "M2", "texto": "Conoce: Xiaomi", "tipo": "cerrada",
         "opciones": {"0": "Unchecked", "1": "Checked"}},
        {"codigo": "SEXO", "texto": "Sexo", "tipo": "cerrada", "opciones": {"1": "M"}},
    ]
    filas = [dict(_fila(1), M1="0", M2="0"), dict(_fila(2), M1="0", M2=None)]
    cargas.crear_individuos(
        ctx.boveda, filas, {"nombre": "NOM", "documento": "DOC"}, origen="campo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA, carga_id=nueva["id"])
    cargas.ingestar(ctx.boveda, ctx.semantica, nueva["id"], preguntas, filas,
                    columna_id="ID", origen="campo", proveedor=ctx.embeddings,
                    demograficas={"NOM": "nombre", "DOC": "documento", "SEXO": "sexo"})
    # 20-1 vio M2 y no la marcó (sin «solo marcadas»): cuenta. 20-2 solo
    # tiene M1 sin marcar, bajo «solo marcadas»: no cuenta.
    assert _tematicas_de(ctx.boveda, nueva["id"]) == {"20-1": {"marcas de celular": 1}}
    tematicas.recalcular_carga(ctx.boveda, ctx.semantica, nueva["id"])
    assert _tematicas_de(ctx.boveda, nueva["id"]) == {"20-1": {"marcas de celular": 1}}


# ════════════════════════════════════════════════════════════════════
#  Consulta
# ════════════════════════════════════════════════════════════════════

def _consulta(ctx, actor, criterios, rol="admin"):
    _, salida = ruteo.despachar("POST", "/consultas", {"criterios": criterios},
                                {}, actor(rol), ctx)
    return salida


def test_la_consulta_demografica_filtra_por_tematica(ctx, actor, carga):
    """DoD. «Panelistas con información sobre telefonía móvil», con el
    nombre como lo escribe cualquiera. Sin tocar el store semántico."""
    ids = _ids(ctx.boveda)
    salida = _consulta(ctx, actor, [
        {"dimension": "tematica", "valor": "Telefonía Móvil"}], rol="analista")
    assert salida["store"] == "boveda"
    assert {i["id_persona"] for i in salida["items"]} == {ids["30-1"], ids["30-4"]}
    assert salida["criterios"][0]["etiqueta"] == "Con información sobre telefonía móvil"

    # Combinada con un atributo: las mujeres con información sobre telefonía.
    salida = _consulta(ctx, actor, [
        {"dimension": "tematica", "valor": "telefonía móvil"},
        {"dimension": "sexo", "valor": "F"}], rol="analista")
    assert [i["id_persona"] for i in salida["items"]] == [ids["30-1"]]


def test_filtrar_por_una_tematica_especial_es_de_quien_tiene_el_permiso(
        ctx, actor, carga):
    criterio = [{"dimension": "tematica", "operador": "in",
                 "valor": ["telefonía móvil", "consumo de tabaco"]}]
    with pytest.raises(SinPermiso, match="consumo de tabaco"):
        _consulta(ctx, actor, criterio, rol="analista")
    salida = _consulta(ctx, actor, criterio, rol="operaciones")
    assert salida["total"] == 3


def test_la_tematica_como_columna_nunca_lleva_una_especial(ctx, actor, carga):
    """DoD — una temática especial no aparece como columna."""
    _, columnas = ruteo.despachar("GET", "/atributos/columnas", {}, {},
                                  actor("admin"), ctx)
    assert {"clave": "tematicas", "etiqueta": "Temáticas con información",
            "tipo": "tematica"} in columnas["items"]
    ids = _ids(ctx.boveda)
    _, valores = ruteo.despachar(
        "POST", "/resultados/atributos", {"ids_persona": list(ids.values())}, {},
        actor("admin"), ctx)
    assert valores["items"][ids["30-1"]]["tematicas"]["valor"] == "telefonía móvil"
    # Bruno solo tiene la especial: no tiene columna.
    assert "tematicas" not in valores["items"].get(ids["30-2"], {})


def test_una_tematica_especial_no_sale_en_las_exportaciones(ctx, actor, carga):
    """DoD — ni en el CSV seudónimo ni en el identificado: sus columnas son
    fijas y no crecen con las temáticas."""
    resultado = _consulta(ctx, actor, [
        {"dimension": "tematica", "valor": "consumo de tabaco"}], rol="admin")
    seudonimo = consultas.a_csv(resultado)
    _, reid = ruteo.despachar(
        "POST", "/reidentificacion",
        {"ids_persona": [i["id_persona"] for i in resultado["items"]],
         "motivo": "consulta"}, {}, actor("admin"), ctx)
    _, identificado = ruteo.despachar(
        "POST", "/consultas/csv-identificado",
        {"reidentificacion": reid, "resultado": resultado}, {}, actor("admin"), ctx)
    for csv in (seudonimo, identificado["csv"]):
        assert "tabaco" not in csv.lower()


def test_la_ficha_muestra_las_tematicas_y_esconde_las_especiales_sin_permiso(
        ctx, actor, carga):
    ids = _ids(ctx.boveda)
    _, ficha = ruteo.despachar("GET", f"/panelistas/{ids['30-1']}", {}, {},
                               actor("analista"), ctx)
    assert [(t["nombre"], t["carga"], t["preguntas_respondidas"])
            for t in ficha["tematicas"]["items"]] == [
        ("telefonía móvil", "Hábitos 2026", 2)]
    assert ficha["tematicas"]["especiales_ocultas"] == 1

    _, ficha = ruteo.despachar("GET", f"/panelistas/{ids['30-1']}", {}, {},
                               actor("dpo"), ctx)
    assert [t["nombre"] for t in ficha["tematicas"]["items"]] == [
        "consumo de tabaco", "telefonía móvil"]
    assert ficha["tematicas"]["especiales_ocultas"] == 0


def test_la_composicion_cuenta_cuantos_tienen_cada_tematica(ctx, actor, carga):
    _, salida = ruteo.despachar("GET", "/composicion", {},
                                {"ambito": "carga", "carga_id": str(carga["id"])},
                                actor("analista"), ctx)
    assert salida["tematicas"]["total"] == 4
    assert [(t["nombre"], t["personas"], t["proporcion"])
            for t in salida["tematicas"]["items"]] == [("telefonía móvil", 2, 0.5)]

    _, salida = ruteo.despachar("GET", "/composicion", {}, {"ambito": "todos"},
                                actor("operaciones"), ctx)
    assert {t["nombre"]: t["personas"] for t in salida["tematicas"]["items"]} == {
        "telefonía móvil": 2, "consumo de tabaco": 2}


# ════════════════════════════════════════════════════════════════════
#  Especiales
# ════════════════════════════════════════════════════════════════════

def test_quitar_la_marca_de_especial_exige_el_permiso_de_los_atributos(
        ctx, actor, carga):
    sin_marca = {"tematicas": [
        {"nombre": "telefonía móvil"},
        {"nombre": "consumo de tabaco", "es_especial": False}]}
    with pytest.raises(SinPermiso):
        ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas", sin_marca, {},
                        actor("operaciones"), ctx)
    # Marcar sí puede cualquiera que edite: es la dirección prudente.
    ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas",
                    {"tematicas": [{"nombre": "telefonía móvil", "es_especial": True},
                                   {"nombre": "consumo de tabaco"}]},
                    {}, actor("analista"), ctx)
    assert {t["nombre"]: t["es_especial"] for t in tematicas.listar(ctx.boveda)} == {
        "telefonía móvil": True, "consumo de tabaco": True}
    ruteo.despachar("PUT", f"/cargas/{carga['id']}/tematicas", sin_marca, {},
                    actor("admin"), ctx)
    assert {t["nombre"]: t["es_especial"] for t in tematicas.listar(ctx.boveda)} == {
        "telefonía móvil": True, "consumo de tabaco": False}


def test_claude_indica_cuales_podrian_ser_especiales(conn_boveda, carga):
    from test_tematicas_y_seccion_cargas import Transporte, _claude, _respuesta_con

    transporte = Transporte(_respuesta_con([
        {"tematica": "salud mental", "es_nueva": True,
         "preguntas_relacionadas": ["T2"], "podria_ser_especial": True},
        {"tematica": "telefonía móvil", "es_nueva": False,
         "preguntas_relacionadas": ["T1"], "podria_ser_especial": False},
        # Ya está marcada: sigue siéndolo aunque el modelo no lo vea.
        {"tematica": "consumo de tabaco", "es_nueva": False,
         "preguntas_relacionadas": ["F1"], "podria_ser_especial": False},
    ]))
    salida = tematicas.proponer(conn_boveda, _claude(transporte),
                                tematicas.preguntas_para_proponer(PREGUNTAS))
    assert [(t["nombre"], t["podria_ser_especial"]) for t in salida["propuesta"]] == [
        ("salud mental", True), ("telefonía móvil", False), ("consumo de tabaco", True)]
    assert "podria_ser_especial = true" in transporte.enviados[0]["system"]


# ════════════════════════════════════════════════════════════════════
#  Baja
# ════════════════════════════════════════════════════════════════════

def test_una_baja_elimina_las_tematicas_de_la_persona(ctx, actor, carga):
    """DoD. La total por la cascada desde `persona`; el retiro del uso
    semántico, en el acto: la temática se derivó de un contenido que se
    está borrando."""
    ids = _ids(ctx.boveda)
    salida = bajas.retirar(ctx.boveda, ids["30-1"], "uso_semantico",
                           conn_semantica=ctx.semantica)
    assert salida["tematicas_borradas"] == 2
    assert "30-1" not in _tematicas_de(ctx.boveda)

    bajas.retirar(ctx.boveda, ids["30-4"], conn_semantica=ctx.semantica)
    assert "30-4" not in _tematicas_de(ctx.boveda)
    assert db.una(ctx.boveda, "select count(*) as n from persona_tematica "
                              "where id_persona = %s", (ids["30-4"],))["n"] == 0


def test_borrar_la_carga_se_lleva_las_tematicas_de_sus_panelistas(ctx, carga):
    db.ejecutar(ctx.boveda, "delete from carga where id = %s", (carga["id"],))
    assert _tematicas_de(ctx.boveda) == {}
    assert db.una(ctx.boveda, "select count(*) as n from tematica_pregunta")["n"] == 0
