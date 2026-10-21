"""Ampliación del spec de temáticas — taxonomía (R-TEM.7 a R-TEM.12) y
correcciones del filtro (R-TEM.13 y R-TEM.14).

`specs/SPEC_tematicas_y_seccion_cargas.md`, «Ampliación». El archivo sigue
su Definition of Done (T5): el arranque se guarda solo al confirmar, una
carga nueva se ubica en áreas existentes en la llamada de siempre, la
taxonomía no cambia al abrir el filtro ni al consultar, la reorganización no
aplica nada sola, la fusión reapunta sin duplicar, el área especial vuelve
especiales a sus temáticas, el filtro agrupa por área y cuenta personas, y
la propuesta sigue a la marca de demográficas sin llamar a Claude.

Claude no se llama: el proveedor de verdad con un transporte doble, como en
las pruebas de temáticas.
"""

import json

import pytest

from panel_api import atributos, db, ruteo, taxonomia, tematicas
from panel_api.errores import DatosInvalidos, SinPermiso

from test_tematicas_de_panelistas import _ids, _tematicas_de, carga  # noqa: F401
from test_tematicas_y_seccion_cargas import (
    DEMOGRAFICAS, EVIDENCIA, PREGUNTAS_REVISION, _filas_revision,
)


class _Respuesta:
    def __init__(self, cuerpo, status_code=200):
        self.status_code = status_code
        self.text = json.dumps(cuerpo)
        self.headers = {}


def _herramienta(nombre, entrada):
    return _Respuesta({"stop_reason": "tool_use",
                       "usage": {"input_tokens": 500, "output_tokens": 100},
                       "content": [{"type": "tool_use", "name": nombre, "input": entrada}]})


class Transporte:
    """Devuelve, en orden, las respuestas que se le dieron; guarda lo enviado."""

    def __init__(self, *respuestas, error=None):
        self.respuestas = list(respuestas)
        self.error = error
        self.enviados = []

    def __call__(self, cuerpo, timeout):
        self.enviados.append(json.loads(cuerpo))
        if self.error:
            raise self.error
        return self.respuestas.pop(0)


def _claude(transporte):
    return tematicas.Claude(api_key="k", modelo="m", tiempo_s=30, transporte=transporte)


ARRANQUE = {"areas": [
    {"area": "Telecomunicaciones y Conectividad",
     "descripcion": "Telefonía, internet y servicios de comunicación.",
     "podria_ser_especial": False, "tematicas": ["telefonía móvil"]},
    {"area": "consumo de sustancias", "descripcion": "Tabaco, alcohol y otras.",
     "podria_ser_especial": True, "tematicas": ["Consumo de tabaco"]},
]}


def _arrancar(ctx, actor, confirmar=True):
    ctx.proponente_tematicas = _claude(Transporte(_herramienta("proponer_taxonomia", ARRANQUE)))
    _, propuesta = ruteo.despachar("POST", "/tematicas/taxonomia/propuesta", {}, {},
                                   actor("analista"), ctx)
    if confirmar:
        ruteo.despachar("PUT", "/tematicas/taxonomia", {"areas": [
            {"nombre": a["nombre"], "descripcion": a["descripcion"],
             "es_especial": a["podria_ser_especial"],
             "tematicas": [t["id"] for t in a["tematicas"]]}
            for a in propuesta["areas"]]}, {}, actor("analista"), ctx)
    return propuesta


def _area_de(conn, nombre):
    return db.una(conn, "select area_id, area from v_tematica where nombre = %s",
                  (nombre,))


def _area_id(conn, nombre):
    return db.una(conn, "select id from area_tematica where nombre = %s", (nombre,))["id"]


def _cambios(conn):
    return db.una(conn, "select count(*)::int as n from taxonomia_cambio")["n"]


# ════════════════════════════════════════════════════════════════════
#  R-TEM.8 · El arranque
# ════════════════════════════════════════════════════════════════════

def test_el_arranque_propone_areas_y_se_guarda_solo_al_confirmar(ctx, actor, carga):
    transporte = Transporte(_herramienta("proponer_taxonomia", ARRANQUE))
    ctx.proponente_tematicas = _claude(transporte)
    _, propuesta = ruteo.despachar("POST", "/tematicas/taxonomia/propuesta", {}, {},
                                   actor("analista"), ctx)

    enviado = transporte.enviados[0]
    assert enviado["tool_choice"] == {"type": "tool", "name": "proponer_taxonomia"}
    assert "Entre 8 y 20 áreas" in enviado["system"]
    assert "- telefonía móvil" in enviado["messages"][0]["content"]
    assert [(a["nombre"], a["es_nueva"], a["podria_ser_especial"],
             [t["nombre"] for t in a["tematicas"]]) for a in propuesta["areas"]] == [
        ("telecomunicaciones y conectividad", True, False, ["telefonía móvil"]),
        ("consumo de sustancias", True, True, ["consumo de tabaco"])]
    # Proponer no escribe.
    assert db.una(ctx.boveda, "select count(*)::int as n from area_tematica")["n"] == 0
    assert _area_de(ctx.boveda, "telefonía móvil")["area_id"] is None
    assert _cambios(ctx.boveda) == 0

    # El analista renombra un área y confirma.
    areas = propuesta["areas"]
    areas[0]["nombre"] = "telecomunicaciones"
    ruteo.despachar("PUT", "/tematicas/taxonomia", {"areas": [
        {"nombre": a["nombre"], "descripcion": a["descripcion"],
         "es_especial": a["podria_ser_especial"],
         "tematicas": [t["id"] for t in a["tematicas"]]} for a in areas]},
        {}, actor("analista"), ctx)
    assert _area_de(ctx.boveda, "telefonía móvil")["area"] == "telecomunicaciones"
    assert _area_de(ctx.boveda, "consumo de tabaco")["area"] == "consumo de sustancias"
    cambio = db.una(ctx.boveda, "select tipo, detalle, actor_email from taxonomia_cambio")
    assert cambio["tipo"] == "arranque" and cambio["actor_email"] == "analista@equipos.com.uy"


def test_una_tematica_no_puede_ir_en_dos_areas(ctx, actor, carga):
    tid = _ids  # noqa: F841 — solo para usar el import
    t = db.una(ctx.boveda, "select id from tematica where nombre = 'telefonía móvil'")["id"]
    with pytest.raises(DatosInvalidos, match="dos áreas"):
        ruteo.despachar("PUT", "/tematicas/taxonomia", {"areas": [
            {"nombre": "uno", "tematicas": [t]}, {"nombre": "dos", "tematicas": [t]}]},
            {}, actor("analista"), ctx)


# ════════════════════════════════════════════════════════════════════
#  R-TEM.9 · Crecimiento con cada carga
# ════════════════════════════════════════════════════════════════════

def _revisar(ctx, actor, carga_id, **extra):
    _, resumen = ruteo.despachar(
        "POST", f"/cargas/{carga_id}/ingesta",
        {"filas": extra.pop("filas", None) or _filas_revision(),
         "columna_id": "ID", "origen": "omnibus",
         "tipo_identificador": "alias", "modo": "crear_individuos",
         "preguntas": extra.pop("preguntas", PREGUNTAS_REVISION),
         "demograficas": extra.pop("demograficas", DEMOGRAFICAS),
         "evidencia_consentimiento": EVIDENCIA, "solo_revisar": True, **extra},
        {}, actor("analista"), ctx)
    return resumen


def _nueva_carga(ctx, actor, nombre="Ómnibus nuevo"):
    _, nueva = ruteo.despachar("POST", "/cargas", {
        "nombre": nombre, "tipo_datos_origen": "encuesta", "modo_relevamiento": "web"},
        {}, actor("analista"), ctx)
    return nueva


def test_una_carga_nueva_ubica_sus_tematicas_en_areas_existentes(ctx, actor, carga):
    """DoD: en la misma llamada que ya proponía las temáticas; con un área
    que no existe, propone una nueva marcada como tal."""
    _arrancar(ctx, actor)
    transporte = Transporte(_herramienta("proponer_tematicas", {"tematicas": [
        {"tematica": "telefonía móvil", "es_nueva": False, "preguntas_relacionadas": ["P1"],
         "podria_ser_especial": False, "area": "otra cualquiera", "area_es_nueva": True},
        {"tematica": "planes de datos", "es_nueva": True, "preguntas_relacionadas": ["P1"],
         "podria_ser_especial": False, "area": "Telecomunicaciones y Conectividad",
         "area_es_nueva": True},
        {"tematica": "lugares de compra", "es_nueva": True, "preguntas_relacionadas": ["P2"],
         "podria_ser_especial": False, "area": "comercio minorista", "area_es_nueva": False},
    ]}))
    ctx.proponente_tematicas = _claude(transporte)
    nueva = _nueva_carga(ctx, actor)

    propuesta = _revisar(ctx, actor, nueva["id"])["tematicas"]["propuesta"]

    assert len(transporte.enviados) == 1   # la llamada de siempre, nada más
    mensaje = transporte.enviados[0]["messages"][0]["content"]
    assert "Área «telecomunicaciones y conectividad»:" in mensaje
    assert "- telefonía móvil" in mensaje
    por_nombre = {t["nombre"]: t for t in propuesta}
    # Lo ya clasificado no se toca, diga lo que diga el modelo.
    assert (por_nombre["telefonía móvil"]["area"], por_nombre["telefonía móvil"]["area_fija"],
            por_nombre["telefonía móvil"]["area_es_nueva"]) == (
        "telecomunicaciones y conectividad", True, False)
    # Una nueva en un área existente (resuelta aunque venga con mayúsculas).
    assert (por_nombre["planes de datos"]["area_id"],
            por_nombre["planes de datos"]["area_es_nueva"]) == (
        _area_id(ctx.boveda, "telecomunicaciones y conectividad"), False)
    # Un área que no existe es nueva, aunque el modelo diga que no.
    assert (por_nombre["lugares de compra"]["area"],
            por_nombre["lugares de compra"]["area_es_nueva"]) == ("comercio minorista", True)

    # Al confirmar, las nuevas quedan en su área; la ya clasificada no se mueve.
    ruteo.despachar("PUT", f"/cargas/{nueva['id']}/tematicas", {"tematicas": [
        {"nombre": t["nombre"], "origen": "propuesta_ia",
         "preguntas": [p["codigo"] for p in t["preguntas_relacionadas"]],
         "area": t["area"], "area_id": t["area_id"]} for t in propuesta]},
        {}, actor("analista"), ctx)
    assert _area_de(ctx.boveda, "planes de datos")["area"] == "telecomunicaciones y conectividad"
    assert _area_de(ctx.boveda, "lugares de compra")["area"] == "comercio minorista"
    assert _area_de(ctx.boveda, "telefonía móvil")["area"] == "telecomunicaciones y conectividad"


def test_la_taxonomia_no_cambia_al_abrir_el_filtro_ni_al_consultar(ctx, actor, carga):
    _arrancar(ctx, actor)
    antes = db.todas(ctx.boveda, "select id, nombre, area_id from tematica order by id")
    areas_antes = db.todas(ctx.boveda, "select * from area_tematica order by id")
    cambios = _cambios(ctx.boveda)
    transporte = Transporte(error=AssertionError("no se llama a Claude"))
    ctx.proponente_tematicas = _claude(transporte)

    ruteo.despachar("GET", "/tematicas/filtro", {}, {}, actor("analista"), ctx)
    ruteo.despachar("GET", "/tematicas/taxonomia", {}, {}, actor("analista"), ctx)
    ruteo.despachar("POST", "/consultas", {"criterios": [
        {"dimension": "area_tematica", "valor": [_area_id(
            ctx.boveda, "telecomunicaciones y conectividad")]}]}, {}, actor("analista"), ctx)

    assert transporte.enviados == []
    assert db.todas(ctx.boveda, "select id, nombre, area_id from tematica order by id") == antes
    assert db.todas(ctx.boveda, "select * from area_tematica order by id") == areas_antes
    assert _cambios(ctx.boveda) == cambios


# ════════════════════════════════════════════════════════════════════
#  R-TEM.10 · Reorganización a pedido
# ════════════════════════════════════════════════════════════════════

def _con_celular(ctx, actor, carga):
    """Una variante de telefonía en la misma carga (sobre T1) y en otra."""
    tematicas.guardar(ctx.boveda, carga["id"], [
        {"nombre": "telefonía móvil", "preguntas": ["T1", "T2"]},
        {"nombre": "consumo de tabaco", "preguntas": ["F1"]},
        {"nombre": "telefonía celular", "preguntas": ["T1"]}],
        conn_semantica=ctx.semantica)
    otra = _nueva_carga(ctx, actor, "Otra")
    tematicas.guardar(ctx.boveda, otra["id"], [{"nombre": "telefonía celular",
                                                "preguntas": ["X1"]}])
    return otra


def test_la_reorganizacion_propone_operaciones_y_no_aplica_ninguna(ctx, actor, carga):
    _arrancar(ctx, actor)
    _con_celular(ctx, actor, carga)
    antes = db.todas(ctx.boveda, "select id, nombre, area_id from tematica order by id")
    ctx.proponente_tematicas = _claude(Transporte(_herramienta("proponer_operaciones", {
        "operaciones": [
            {"tipo": "fusionar_tematicas", "tematicas": ["Telefonía celular"],
             "tematica_destino": "telefonía móvil", "motivo": "Son lo mismo."},
            {"tipo": "renombrar_area", "area": "consumo de sustancias",
             "nuevo_nombre": "salud y sustancias", "motivo": "Más claro."},
            {"tipo": "mover_tematica", "tematica": "no existe", "area": "x",
             "motivo": "inventada"},
        ]})))
    _, salida = ruteo.despachar("POST", "/tematicas/taxonomia/reorganizacion", {}, {},
                                actor("analista"), ctx)
    assert [o["tipo"] for o in salida["operaciones"]] == ["fusionar_tematicas",
                                                         "renombrar_area"]
    assert salida["operaciones"][0]["descripcion"] == \
        "Fusionar «telefonía celular» en «telefonía móvil»"
    assert salida["descartadas"][0]["motivo"] == "nombra una temática que no existe"
    # Nada se aplicó.
    assert db.todas(ctx.boveda, "select id, nombre, area_id from tematica order by id") == antes
    assert db.una(ctx.boveda, "select count(*)::int as n from taxonomia_cambio "
                              "where tipo <> 'arranque'")["n"] == 0


def test_fusionar_dos_tematicas_reapunta_cargas_preguntas_y_personas_sin_duplicar(
        ctx, actor, carga):
    _arrancar(ctx, actor)
    otra = _con_celular(ctx, actor, carga)
    # Antes: Ana (30-1) y Diego (30-4) tienen las dos variantes en la carga.
    assert _tematicas_de(ctx.boveda, carga["id"])["30-4"] == {
        "telefonía móvil": 1, "telefonía celular": 1}
    movil = db.una(ctx.boveda, "select id from tematica where nombre = 'telefonía móvil'")["id"]
    celular = db.una(ctx.boveda, "select id from tematica where nombre = 'telefonía celular'")["id"]

    _, salida = ruteo.despachar("POST", "/tematicas/taxonomia/operaciones", {"operacion": {
        "tipo": "fusionar_tematicas", "origen_ids": [celular], "destino_id": movil,
        "motivo": "Son lo mismo."}}, {}, actor("operaciones"), ctx)

    assert not db.una(ctx.boveda, "select 1 as hay from tematica where id = %s", (celular,))
    vinculos = db.todas(ctx.boveda, "select carga_id from carga_tematica "
                                    "where tematica_id = %s order by carga_id", (movil,))
    assert [v["carga_id"] for v in vinculos] == [carga["id"], otra["id"]]
    preguntas = db.todas(ctx.boveda, "select carga_id, codigo_pregunta from tematica_pregunta "
                                     "where tematica_id = %s order by 1, 2", (movil,))
    assert [(p["carga_id"], p["codigo_pregunta"]) for p in preguntas] == [
        (carga["id"], "T1"), (carga["id"], "T2"), (otra["id"], "X1")]
    # Una fila por persona y carga, con la cuenta recalculada.
    assert _tematicas_de(ctx.boveda, carga["id"]) == {
        "30-1": {"telefonía móvil": 2, "consumo de tabaco": 1},
        "30-2": {"consumo de tabaco": 1},
        "30-4": {"telefonía móvil": 1}}
    cambio = db.una(ctx.boveda, "select detalle from taxonomia_cambio "
                                "where tipo = 'fusionar_tematicas'")["detalle"]
    assert cambio["origenes"] == [{"id": celular, "nombre": "telefonía celular"}]
    assert cambio["destino"]["nombre"] == "telefonía móvil"
    assert salida["descripcion"] == "Fusionar «telefonía celular» en «telefonía móvil»"


def test_las_operaciones_sobre_areas_y_su_registro(ctx, actor, carga):
    _arrancar(ctx, actor)
    tele = _area_id(ctx.boveda, "telecomunicaciones y conectividad")
    sustancias = _area_id(ctx.boveda, "consumo de sustancias")
    movil = db.una(ctx.boveda, "select id from tematica where nombre = 'telefonía móvil'")["id"]

    def aplicar(operacion, rol="analista"):
        return ruteo.despachar("POST", "/tematicas/taxonomia/operaciones",
                               {"operacion": operacion}, {}, actor(rol), ctx)[1]

    aplicar({"tipo": "renombrar_area", "area_id": tele, "nombre": "Telecomunicaciones"})
    assert _area_de(ctx.boveda, "telefonía móvil")["area"] == "telecomunicaciones"
    with pytest.raises(DatosInvalidos, match="fusionalas"):
        aplicar({"tipo": "renombrar_area", "area_id": tele, "nombre": "consumo de sustancias"})

    aplicar({"tipo": "mover_tematica", "tematica_id": movil,
             "area_nueva": {"nombre": "servicios"}})
    assert _area_de(ctx.boveda, "telefonía móvil")["area"] == "servicios"

    aplicar({"tipo": "fusionar_areas", "origen_ids": [sustancias],
             "destino_id": _area_id(ctx.boveda, "servicios")})
    # La marca especial de la que se fue suma a la que queda.
    assert db.una(ctx.boveda, "select es_especial from area_tematica "
                              "where nombre = 'servicios'")["es_especial"] is True
    servicios = _area_id(ctx.boveda, "servicios")
    tabaco = db.una(ctx.boveda, "select id from tematica where nombre = 'consumo de tabaco'")["id"]
    aplicar({"tipo": "dividir_area", "area_id": servicios, "nuevas": [
        {"nombre": "tabaco", "tematica_ids": [tabaco]},
        {"nombre": "telefonía", "tematica_ids": [movil]}]})
    assert not db.una(ctx.boveda, "select 1 as hay from area_tematica where id = %s",
                      (servicios,))
    # Las nuevas heredan la sensibilidad de la que se dividió.
    assert db.una(ctx.boveda, "select es_especial from area_tematica "
                              "where nombre = 'telefonía'")["es_especial"] is True
    tipos = [f["tipo"] for f in db.todas(ctx.boveda,
                                        "select tipo from taxonomia_cambio order by id")]
    assert tipos == ["arranque", "renombrar_area", "mover_tematica", "fusionar_areas",
                     "dividir_area"]
    _, historial = ruteo.despachar("GET", "/tematicas/taxonomia/cambios", {}, {},
                                   actor("dpo"), ctx)
    assert historial["items"][0]["tipo"] == "dividir_area"


def test_gestionar_la_taxonomia_tiene_permiso_propio(ctx, actor, carga):
    for metodo, camino in (("POST", "/tematicas/taxonomia/propuesta"),
                           ("POST", "/tematicas/taxonomia/reorganizacion"),
                           ("POST", "/tematicas/taxonomia/operaciones"),
                           ("PUT", "/tematicas/taxonomia")):
        with pytest.raises(SinPermiso):
            ruteo.despachar(metodo, camino, {}, {}, actor("dpo"), ctx)


# ════════════════════════════════════════════════════════════════════
#  R-TEM.11 · Sensibilidad en el área
# ════════════════════════════════════════════════════════════════════

def test_un_area_especial_vuelve_especiales_a_sus_tematicas(ctx, actor, carga):
    _arrancar(ctx, actor)
    tele = _area_id(ctx.boveda, "telecomunicaciones y conectividad")
    ruteo.despachar("POST", "/tematicas/taxonomia/operaciones", {"operacion": {
        "tipo": "marcar_area_especial", "area_id": tele, "es_especial": True}},
        {}, actor("analista"), ctx)
    ids = _ids(ctx.boveda)

    # Sin columna.
    _, valores = ruteo.despachar("POST", "/resultados/atributos",
                                 {"ids_persona": list(ids.values())}, {}, actor("admin"), ctx)
    assert all("tematicas" not in v for v in valores["items"].values())
    # Consulta restringida por rol.
    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", "/consultas", {"criterios": [
            {"dimension": "tematica", "valor": "telefonía móvil"}]}, {},
            actor("analista"), ctx)
    # Ficha.
    _, ficha = ruteo.despachar("GET", f"/panelistas/{ids['30-1']}", {}, {},
                               actor("analista"), ctx)
    assert ficha["tematicas"]["items"] == []
    # La propia marca de la temática no se tocó: la herencia está en la vista.
    assert db.una(ctx.boveda, "select es_especial from tematica "
                              "where nombre = 'telefonía móvil'")["es_especial"] is False
    # Desmarcar el área es de quien administra los atributos.
    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", "/tematicas/taxonomia/operaciones", {"operacion": {
            "tipo": "marcar_area_especial", "area_id": tele, "es_especial": False}},
            {}, actor("operaciones"), ctx)


def test_una_tematica_especial_en_un_area_comun_conserva_su_marca(ctx, actor, carga):
    """La herencia suma, no resta: tabaco es especial por sí mismo."""
    _arrancar(ctx, actor)
    tele = _area_id(ctx.boveda, "telecomunicaciones y conectividad")
    tabaco = db.una(ctx.boveda, "select id from tematica where nombre = 'consumo de tabaco'")["id"]
    ruteo.despachar("POST", "/tematicas/taxonomia/operaciones", {"operacion": {
        "tipo": "mover_tematica", "tematica_id": tabaco, "area_id": tele}},
        {}, actor("analista"), ctx)
    assert db.una(ctx.boveda, "select es_especial from v_tematica where id = %s",
                  (tabaco,))["es_especial"] is True


# ════════════════════════════════════════════════════════════════════
#  R-TEM.12 / R-TEM.14 · El filtro
# ════════════════════════════════════════════════════════════════════

def test_el_filtro_agrupa_por_area_y_cuenta_personas(ctx, actor, carga):
    _arrancar(ctx, actor)
    _, admin = ruteo.despachar("GET", "/tematicas/filtro", {}, {}, actor("admin"), ctx)
    assert [(a["nombre"], a["personas"], [(t["nombre"], t["personas"]) for t in a["tematicas"]])
            for a in admin["areas"]] == [
        ("consumo de sustancias", 2, [("consumo de tabaco", 2)]),
        ("telecomunicaciones y conectividad", 2, [("telefonía móvil", 2)])]
    # Sin el permiso, las especiales no se ofrecen.
    _, analista = ruteo.despachar("GET", "/tematicas/filtro", {}, {}, actor("analista"), ctx)
    assert [a["nombre"] for a in analista["areas"]] == ["telecomunicaciones y conectividad"]


def test_un_area_entera_equivale_a_cualquiera_de_sus_tematicas(ctx, actor, carga):
    _arrancar(ctx, actor)
    tele = _area_id(ctx.boveda, "telecomunicaciones y conectividad")
    ids = _ids(ctx.boveda)
    _, salida = ruteo.despachar("POST", "/consultas", {"criterios": [
        {"dimension": "area_tematica", "valor": [tele],
         "etiqueta": "Con información sobre telecomunicaciones y conectividad"}]},
        {}, actor("analista"), ctx)
    assert {i["id_persona"] for i in salida["items"]} == {ids["30-1"], ids["30-4"]}

    # Renombrar el área no rompe el filtro: va por id.
    ruteo.despachar("POST", "/tematicas/taxonomia/operaciones", {"operacion": {
        "tipo": "renombrar_area", "area_id": tele, "nombre": "conectividad"}},
        {}, actor("analista"), ctx)
    _, otra_vez = ruteo.despachar("POST", "/consultas", {"criterios": [
        {"dimension": "tematica", "valor": [], "areas": [tele]}]}, {}, actor("analista"), ctx)
    assert otra_vez["total"] == 2


def test_un_area_comun_no_alcanza_por_la_puerta_de_atras_una_especial(ctx, actor, carga):
    """Tabaco (especial) en un área común: sin permiso, filtrar por el área
    no trae a quien solo tiene tabaco (Bruno)."""
    _arrancar(ctx, actor)
    tele = _area_id(ctx.boveda, "telecomunicaciones y conectividad")
    tabaco = db.una(ctx.boveda, "select id from tematica where nombre = 'consumo de tabaco'")["id"]
    ruteo.despachar("POST", "/tematicas/taxonomia/operaciones", {"operacion": {
        "tipo": "mover_tematica", "tematica_id": tabaco, "area_id": tele}},
        {}, actor("analista"), ctx)
    ids = _ids(ctx.boveda)
    criterio = [{"dimension": "area_tematica", "valor": [tele],
                 "incluir_especiales": True}]   # el cuerpo no decide esto
    _, analista = ruteo.despachar("POST", "/consultas", {"criterios": criterio}, {},
                                  actor("analista"), ctx)
    assert ids["30-2"] not in {i["id_persona"] for i in analista["items"]}
    _, operaciones = ruteo.despachar("POST", "/consultas", {"criterios": [
        {"dimension": "area_tematica", "valor": [tele]}]}, {}, actor("operaciones"), ctx)
    assert ids["30-2"] in {i["id_persona"] for i in operaciones["items"]}


def test_una_tematica_con_cero_personas_no_aparece_en_el_filtro(ctx, actor, carga):
    """DoD — «nivel educativo»: está en la carga y nadie la tiene."""
    tematicas.guardar(ctx.boveda, carga["id"], [
        {"nombre": "telefonía móvil"}, {"nombre": "consumo de tabaco"},
        {"nombre": "nivel educativo", "preguntas": ["EDU"]}], conn_semantica=ctx.semantica)
    _, filtro = ruteo.despachar("GET", "/tematicas/filtro", {}, {}, actor("admin"), ctx)
    ofrecidas = [t["nombre"] for a in filtro["areas"] for t in a["tematicas"]]
    assert "nivel educativo" not in ofrecidas
    assert "telefonía móvil" in ofrecidas
    # Sigue en la carga, marcada sin personas, y en la revisión general.
    _, detalle = ruteo.despachar("GET", f"/cargas/{carga['id']}", {}, {}, actor("admin"), ctx)
    assert {t["nombre"]: t["personas"] for t in detalle["tematicas"]}["nivel educativo"] == 0
    _, sin = ruteo.despachar("GET", "/tematicas/sin-personas", {}, {}, actor("dpo"), ctx)
    assert [(i["carga"], i["tematica"]) for i in sin["items"]] == [
        ("Hábitos 2026", "nivel educativo")]


# ════════════════════════════════════════════════════════════════════
#  R-TEM.13 · La propuesta sigue a la marca de demográficas
# ════════════════════════════════════════════════════════════════════

FILAS_CON_EDU = [dict(f, EDU="2") for f in _filas_revision()]

PREGUNTAS_CON_EDU = PREGUNTAS_REVISION + [
    {"codigo": "EDU", "texto": "¿Cuál es el máximo nivel educativo que alcanzó?",
     "tipo": "cerrada", "opciones": {"1": "Primaria", "2": "Secundaria"}, "orden": 5}]

PROPUESTA_CON_EDU = {"tematicas": [
    {"tematica": "consumo de bebidas", "es_nueva": True, "preguntas_relacionadas": ["P1", "P2"],
     "podria_ser_especial": True, "area": "consumo de sustancias", "area_es_nueva": False},
    {"tematica": "nivel educativo", "es_nueva": True, "preguntas_relacionadas": ["EDU"],
     "podria_ser_especial": False, "area": "educación", "area_es_nueva": True},
    {"tematica": "trayectoria y compras", "es_nueva": True,
     "preguntas_relacionadas": ["EDU", "P2"], "podria_ser_especial": False,
     "area": "consumo masivo", "area_es_nueva": True},
]}


def test_marcar_una_demografica_retira_las_tematicas_que_salian_solo_de_ella(
        ctx, actor, conn_boveda):
    """DoD — sin volver a llamar a Claude; desmarcar la devuelve."""
    atributos.crear(conn_boveda, {"clave": "nivel_educativo", "etiqueta": "Nivel educativo",
                                  "tipo": "categorico"})
    transporte = Transporte(_herramienta("proponer_tematicas", PROPUESTA_CON_EDU))
    ctx.proponente_tematicas = _claude(transporte)
    nueva = _nueva_carga(ctx, actor)

    primera = _revisar(ctx, actor, nueva["id"], preguntas=PREGUNTAS_CON_EDU, filas=FILAS_CON_EDU)["tematicas"]
    assert [t["nombre"] for t in primera["propuesta"]] == [
        "consumo de bebidas", "nivel educativo", "trayectoria y compras"]

    # Se marca EDU como demográfica y se vuelve a revisar con la propuesta
    # que ya estaba.
    marcada = _revisar(ctx, actor, nueva["id"], preguntas=PREGUNTAS_CON_EDU, filas=FILAS_CON_EDU,
                       demograficas={**DEMOGRAFICAS, "EDU": "nivel_educativo"},
                       proponer_tematicas=False,
                       propuesta_previa=primera["propuesta"])["tematicas"]
    assert len(transporte.enviados) == 1
    assert marcada["estado"] == "ajustada"
    assert [t["nombre"] for t in marcada["propuesta"]] == [
        "consumo de bebidas", "trayectoria y compras"]
    assert marcada["retiradas"] == ["nivel educativo"]
    # La que tenía otras preguntas se queda, sin la excluida.
    assert [p["codigo"] for p in marcada["propuesta"][1]["preguntas_relacionadas"]] == ["P2"]

    # Lo editado por el analista sigue la misma regla.
    editadas = _revisar(ctx, actor, nueva["id"], preguntas=PREGUNTAS_CON_EDU, filas=FILAS_CON_EDU,
                        demograficas={**DEMOGRAFICAS, "EDU": "nivel_educativo"},
                        proponer_tematicas=False, tematicas_editadas=[
                            {"nombre": "nivel educativo", "preguntas": ["EDU"]},
                            {"nombre": "a mano, sin preguntas", "preguntas": []}])["tematicas"]
    assert [t["nombre"] for t in editadas["editadas"]] == ["a mano, sin preguntas"]

    # Desmarcar la variable la devuelve.
    desmarcada = _revisar(ctx, actor, nueva["id"], preguntas=PREGUNTAS_CON_EDU, filas=FILAS_CON_EDU,
                          proponer_tematicas=False,
                          propuesta_previa=primera["propuesta"])["tematicas"]
    assert "nivel educativo" in [t["nombre"] for t in desmarcada["propuesta"]]
    assert len(transporte.enviados) == 1


def test_ajustar_a_preguntas_es_puro():
    vigentes, retiradas = tematicas.ajustar_a_preguntas(
        [{"nombre": "a", "preguntas": ["X"]}, {"nombre": "b", "preguntas": ["X", "Y"]},
         {"nombre": "c", "preguntas": []}],
        [{"codigo": "Y"}])
    assert [t["nombre"] for t in vigentes] == ["b", "c"]
    assert vigentes[0]["preguntas"] == ["Y"]
    assert [t["nombre"] for t in retiradas] == ["a"]


def test_taxonomia_listar_reporta_las_sin_area(ctx, actor, carga):
    salida = taxonomia.listar(ctx.boveda)
    assert salida["arrancada"] is False
    assert {t["nombre"] for t in salida["sin_area"]} == {"telefonía móvil",
                                                         "consumo de tabaco"}
