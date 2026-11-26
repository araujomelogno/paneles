"""SPEC_alchemer_participacion_y_bandeja_whatsapp — partes B y A, contra
Postgres de verdad y con un Alchemer de mentira.

* **B · Consultar por historia de participación** (D96): los criterios
  «convocado» y «respondió» con sí/no, ventana y veces (R-PAR.1), y la fecha
  de campo obligatoria con su migración (R-PAR.2).
* **A · Alchemer al crear la encuesta** (D97): buscar y elegir la encuesta,
  verificar que guarde el parámetro del panelista (avisa, no bloquea; se
  vuelve a verificar al salir a campo), y el cuestionario como referencia
  para proponer temáticas antes del campo (R-ALC.1 a R-ALC.4).
"""

import importlib.util
import json
import pathlib

import pytest

from panel_api import alchemer, db, demografia, encuestas, paneles, personas, ruteo
from panel_api.errores import Conflicto, DatosInvalidos

from conftest import consentimientos

RAIZ = pathlib.Path(__file__).resolve().parents[2]


def _ids(conn, criterios):
    criterios = [demografia.normalizar_criterio(c) for c in criterios]
    return set(demografia.ids_del_segmento(conn, criterios))


def _persona(conn, nombre):
    return str(personas.alta(conn, {
        "persona": {"nombre": nombre, "email": f"{nombre.lower()}@ej.uy"},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico"),
    })["id_persona"])


def _participacion(criterio):
    return {"dimension": "participacion", **criterio}


# ════════════════════════════════════════════════════════════════════
#  B · R-PAR.1 — los criterios
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def historia(conn_boveda):
    """Ana: convocada hace 8 meses y no después. Beto: convocado hace 2
    meses y respondió una encuesta con campo hace 3 meses. Caro: nunca.
    Dani: convocada tres veces este año; respondió una con campo hace 9
    meses."""
    panel = paneles.crear(conn_boveda, "Panel")
    gente = {n: _persona(conn_boveda, n) for n in ("Ana", "Beto", "Caro", "Dani")}
    for i in gente.values():
        paneles.agregar_miembro(conn_boveda, panel["id"], i)

    def encuesta(nombre, fecha, convocados, meses_atras, respondieron=()):
        e = encuestas.crear(conn_boveda, panel["id"], nombre, fecha_campo=fecha)
        encuestas.convocar(conn_boveda, e["id"], ids_persona=[gente[c] for c in convocados])
        db.ejecutar(conn_boveda, """
            update participacion set convocado_en = now() - make_interval(months => %s)
             where encuesta_id = %s""", (meses_atras, e["id"]))
        if respondieron:
            encuestas.marcar_respuesta(conn_boveda, e["id"], [gente[r] for r in respondieron])
        return e

    hoy = db.una(conn_boveda, "select current_date as d")["d"]
    import datetime

    def hace(meses):
        return (hoy - datetime.timedelta(days=30 * meses)).isoformat()

    encuesta("Ola vieja", hace(8), ["Ana"], 8)
    encuesta("Ola reciente", hace(3), ["Beto"], 2, respondieron=["Beto"])
    encuesta("Ola de Dani 1", hace(9), ["Dani"], 1, respondieron=["Dani"])
    encuesta("Ola de Dani 2", hace(1), ["Dani"], 1)
    encuesta("Ola de Dani 3", hace(1), ["Dani"], 1)
    conn_boveda.commit()
    return gente


def test_no_convocado_en_los_ultimos_6_meses_incluye_al_de_hace_8(conn_boveda, historia):
    """Criterio de aceptación: convocado hace 8 meses y no después → aparece."""
    ids = _ids(conn_boveda, [_participacion({
        "relacion": "convocado", "operador": "not_in",
        "ventana": {"tipo": "meses", "meses": 6}})])
    assert ids == {historia["Ana"], historia["Caro"]}


def test_respondio_en_los_ultimos_6_meses_mira_la_fecha_de_campo(conn_boveda, historia):
    """Criterio de aceptación: respondió una encuesta con campo hace 3 meses
    → aparece. Dani respondió una con campo hace 9: no."""
    ids = _ids(conn_boveda, [_participacion({
        "relacion": "respondio", "operador": "in",
        "ventana": {"tipo": "meses", "meses": 6}})])
    assert ids == {historia["Beto"]}


def test_nunca_respondio_y_convocado_entre_dos_fechas(conn_boveda, historia):
    nunca = _ids(conn_boveda, [_participacion({"relacion": "respondio", "operador": "no",
                                               "ventana": {"tipo": "siempre"}})])
    assert nunca == {historia["Ana"], historia["Caro"]}
    hoy = db.una(conn_boveda, "select current_date as d")["d"]
    import datetime
    entre = _ids(conn_boveda, [_participacion({
        "relacion": "convocado", "operador": "si",
        "ventana": {"tipo": "entre",
                    "desde": (hoy - datetime.timedelta(days=300)).isoformat(),
                    "hasta": (hoy - datetime.timedelta(days=200)).isoformat()}})])
    assert entre == {historia["Ana"]}


def test_veces_cuenta_las_participaciones_de_la_ventana(conn_boveda, historia):
    """P1 — «convocado al menos 3 veces en los últimos 6 meses»."""
    criterio = demografia.normalizar_criterio(_participacion({
        "relacion": "convocado", "operador": "in", "veces": 3,
        "ventana": {"tipo": "meses", "meses": 6}}))
    assert criterio["etiqueta"] == "Convocado al menos 3 veces en los últimos 6 meses"
    assert set(demografia.ids_del_segmento(conn_boveda, [criterio])) == {historia["Dani"]}


def test_se_combina_con_otros_criterios(conn_boveda, historia):
    """Combinable con los demás criterios de la consulta (R-PAR.1)."""
    ids = _ids(conn_boveda, [
        _participacion({"relacion": "respondio", "operador": "not_in",
                        "ventana": {"tipo": "meses", "meses": 6}}),
        _participacion({"relacion": "convocado", "operador": "in",
                        "ventana": {"tipo": "siempre"}}),
    ])
    assert ids == {historia["Ana"], historia["Dani"]}


@pytest.mark.parametrize("crudo, etiqueta", [
    ({"relacion": "convocado", "operador": "not_in", "ventana": {"tipo": "meses", "meses": 6}},
     "No convocado en los últimos 6 meses"),
    ({"relacion": "respondio", "operador": "not_in", "ventana": {"tipo": "siempre"}},
     "Nunca respondió"),
    ({"relacion": "respondio", "operador": "in", "ventana": {"tipo": "siempre"}},
     "Respondió alguna vez"),
])
def test_la_etiqueta_dice_lo_que_filtra(crudo, etiqueta):
    assert demografia.normalizar_criterio(_participacion(crudo))["etiqueta"] == etiqueta


@pytest.mark.parametrize("crudo", [
    {"relacion": "invitado"},
    {"relacion": "convocado", "operador": "gt"},
    {"relacion": "convocado", "ventana": {"tipo": "meses"}},
    {"relacion": "convocado", "ventana": {"tipo": "entre", "desde": "2026-05-01",
                                          "hasta": "2026-01-01"}},
    {"relacion": "convocado", "veces": 0},
])
def test_lo_mal_formado_es_error_nunca_un_filtro_ignorado(crudo):
    with pytest.raises(DatosInvalidos):
        demografia.normalizar_criterio(_participacion(crudo))


# ════════════════════════════════════════════════════════════════════
#  B · R-PAR.2 — la fecha de campo obligatoria
# ════════════════════════════════════════════════════════════════════

def test_crear_una_encuesta_o_una_carga_exige_la_fecha_de_campo(ctx, actor):
    status, error = _intentar(ctx, actor, "POST", "/encuestas", {"nombre": "Sin fecha", "tipo": "independiente"})
    assert status == 400 and error["detalle"]["campo"] == "fecha_campo"
    status, error = _intentar(ctx, actor, "POST", "/cargas",
                              {"nombre": "Sin fecha", "tipo_datos_origen": "encuesta",
                               "modo_relevamiento": "web"})
    assert status == 400 and error["detalle"]["campo"] == "fecha_estudio"

    status, encuesta = ruteo.despachar("POST", "/encuestas",
                                       {"nombre": "Con fecha", "tipo": "independiente",
                                        "fecha_campo": "2026-11-01"},
                                       {}, actor("admin"), ctx)
    assert status == 201
    status, error = _intentar(ctx, actor, "PATCH", f"/encuestas/{encuesta['id']}",
                              {"fecha_campo": ""})
    assert status == 400 and error["detalle"]["campo"] == "fecha_campo"


def _intentar(ctx, actor, metodo, camino, cuerpo):
    try:
        return ruteo.despachar(metodo, camino, cuerpo, {}, actor("admin"), ctx)
    except DatosInvalidos as error:
        ctx.boveda.rollback()
        return error.status, error.como_dict()


def _script():
    spec = importlib.util.spec_from_file_location(
        "migrar_fecha_de_campo", RAIZ / "scripts" / "migrar_fecha_de_campo.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_la_migracion_completa_con_la_primera_carga_o_la_creacion_y_lista(conn_boveda):
    """Se simula una base anterior (la columna admite nulos) dentro de la
    transacción de la prueba, que se deshace al final."""
    script = _script()
    db.ejecutar(conn_boveda, "alter table encuesta alter column fecha_campo drop not null")
    con_carga = encuestas.crear(conn_boveda, None, "Con carga")
    sin_carga = encuestas.crear(conn_boveda, None, "Sin carga")
    con_fecha = encuestas.crear(conn_boveda, None, "Con fecha", fecha_campo="2026-03-01")
    db.ejecutar(conn_boveda, "update encuesta set fecha_campo = null, "
                             "creado_en = '2026-01-10' where id in (%s, %s)",
                (con_carga["id"], sin_carga["id"]))
    db.ejecutar(conn_boveda, """
        insert into carga (nombre, encuesta_id, creado_en) values
          ('Primera', %s, '2026-02-05'), ('Segunda', %s, '2026-04-05')
    """, (con_carga["id"], con_carga["id"]))

    db.ejecutar(conn_boveda, script._sin_transaccion(script.MIGRACION.read_text(encoding="utf-8")))

    fechas = {f["id"]: str(f["fecha_campo"]) for f in db.todas(
        conn_boveda, "select id, fecha_campo from encuesta")}
    assert fechas[con_carga["id"]] == "2026-02-05"          # la primera carga
    assert fechas[sin_carga["id"]] == "2026-01-10"          # la creación
    assert fechas[con_fecha["id"]] == "2026-03-01"          # no se toca
    listadas = {f["encuesta_id"]: f["fuente"] for f in script._completadas(conn_boveda)}
    assert listadas == {con_carga["id"]: "primera_carga", sin_carga["id"]: "creacion"}
    assert script.verificar(conn_boveda) == 0               # sin nulos y not null


# ════════════════════════════════════════════════════════════════════
#  A · Alchemer
# ════════════════════════════════════════════════════════════════════

ENTORNO = {"ALCHEMER_API_TOKEN": "tok", "ALCHEMER_API_SECRET": "sec"}


class AlchemerFalso:
    """Dos encuestas: 7001 guarda `pid` en una pregunta oculta; 7002 no."""

    def __init__(self):
        self.pedidos = []
        self.captura = {"7001": True, "7002": False}

    def __call__(self, url):
        self.pedidos.append(url)
        camino = url.split("/v5/", 1)[1].split("?", 1)[0]
        if camino == "survey":
            return {"result_ok": True, "total_pages": 1, "data": [
                {"id": 7001, "title": "Ola 4 — Consumo de bebidas", "status": "Launched",
                 "modified_on": "2026-11-15"},
                {"id": 7002, "title": "Tracking telefonía", "status": "Launched",
                 "modified_on": "2026-11-10"}]}
        partes = camino.split("/")
        sid = partes[1]
        if len(partes) == 2:
            return {"data": {"id": sid, "title": {"Spanish": f"Encuesta {sid}"}}}
        if partes[2] == "surveycampaign":
            return {"data": [
                {"id": 1, "type": "link", "name": "Web", "uri": f"https://s.alchemer.com/s3/{sid}/w"},
                {"id": 2, "type": "link", "name": "WhatsApp", "uri": f"https://s.alchemer.com/s3/{sid}/wa"},
                {"id": 3, "type": "email", "name": "Correo", "uri": None}]}
        preguntas = [
            {"id": 2, "_subtype": "radio", "shortname": "P1",
             "title": {"Spanish": "¿Qué bebida consume <b>habitualmente</b>?"},
             "options": [{"id": 10, "value": "1", "title": {"Spanish": "Fernet"}},
                         {"id": 11, "value": "2", "title": {"Spanish": "Whisky"}}]},
            {"id": 3, "_subtype": "essay", "shortname": "P2",
             "title": {"Spanish": "¿Por qué la elige?"}},
            {"id": 4, "_subtype": "instructions", "title": {"Spanish": "Gracias"}},
        ]
        if self.captura[sid]:
            preguntas.append({"id": 9, "_subtype": "hidden", "title": {"Spanish": "pid"},
                              "properties": {"defaulttext": '[url("pid")]'}})
        return {"data": preguntas}


@pytest.fixture
def api_alchemer(monkeypatch):
    falso = AlchemerFalso()
    for clave, valor in ENTORNO.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setattr(alchemer, "_pedir", falso)
    return falso


def test_sin_credenciales_la_busqueda_avisa_y_la_url_se_escribe_a_mano(ctx, actor, monkeypatch):
    for clave in ENTORNO:
        monkeypatch.delenv(clave, raising=False)
    status, salida = ruteo.despachar("GET", "/alchemer/encuestas", None, {"q": "ola"},
                                     actor("analista"), ctx)
    assert status == 200 and salida["configurado"] is False and salida["items"] == []
    assert "a mano" in salida["avisos"][0]


def test_la_region_elige_la_api_y_ningun_nombre_usa_un_prefijo_reservado():
    assert alchemer.config({"ALCHEMER_REGION": "eu"})["base"] == alchemer.REGIONES["eu"]
    assert alchemer.config({})["base"] == alchemer.REGIONES["us"]
    assert alchemer.config({"ALCHEMER_API_URL": "https://otra/v5/"})["base"] == "https://otra/v5"
    for nombre in ("ALCHEMER_API_TOKEN", "ALCHEMER_API_SECRET", "ALCHEMER_REGION"):
        assert not nombre.startswith(("FIREBASE_", "X_GOOGLE_", "EXT_", "KIT_"))


def test_buscar_y_ver_una_encuesta_de_alchemer(ctx, actor, api_alchemer):
    """Desde D99 la búsqueda es contra el catálogo (sincronizado en segundo
    plano) y ver una encuesta y sus enlaces son dos pedidos de una llamada
    cada uno (`test_pedido_busqueda_alchemer.py`)."""
    from panel_api import alchemer_catalogo

    _, salida = ruteo.despachar("POST", "/alchemer/sincronizacion", {}, {}, actor("admin"), ctx)
    alchemer_catalogo.procesar_pagina(ctx.boveda, salida["id"], 1,
                                      encolador=ctx.encolador_alchemer)
    status, salida = ruteo.despachar("GET", "/alchemer/encuestas", None, {"q": "BEBIDAS"},
                                     actor("analista"), ctx)
    assert status == 200 and [i["id"] for i in salida["items"]] == ["7001"]
    # Las credenciales viajan en la consulta a Alchemer, nunca de vuelta.
    assert "tok" not in json.dumps(salida, default=str)

    status, vista = ruteo.despachar("POST", "/alchemer/resolver", {"texto": "7002"}, {},
                                    actor("analista"), ctx)
    assert status == 200
    assert vista["captura_parametro"] is False and "no guarda el parámetro" in vista["aviso"]
    assert vista["preguntas"] == 2                      # sin las instrucciones
    # Dos enlaces (el de correo no es un enlace).
    _, enlaces = ruteo.despachar("GET", "/alchemer/encuestas/7002/enlaces", None, {},
                                 actor("analista"), ctx)
    assert [e["url_con_parametro"] for e in enlaces["items"]] == [
        "https://s.alchemer.com/s3/7002/w?pid=", "https://s.alchemer.com/s3/7002/wa?pid="]


def test_una_encuesta_que_no_guarda_el_parametro_se_avisa_y_se_guarda_igual(
        ctx, actor, api_alchemer):
    """Criterio de aceptación de R-ALC.3."""
    status, encuesta = ruteo.despachar("POST", "/encuestas", {
        "nombre": "Tracking", "tipo": "independiente", "fecha_campo": "2026-11-20", "alchemer_id": "7002",
        "url_alchemer": "https://s.alchemer.com/s3/7002/w?pid="}, {}, actor("analista"), ctx)
    assert status == 201
    assert "no guarda el parámetro" in encuesta["alchemer"]["aviso"]
    status, leida = ruteo.despachar("GET", f"/encuestas/{encuesta['id']}", None, {},
                                    actor("analista"), ctx)
    assert leida["alchemer_id"] == "7002"
    assert leida["alchemer"]["captura_parametro"] is False
    assert [p["codigo"] for p in leida["alchemer"]["preguntas"]] == ["P1", "P2"]
    assert leida["alchemer"]["preguntas"][0]["texto"] == "¿Qué bebida consume habitualmente?"


def test_salir_a_campo_vuelve_a_verificar(ctx, actor, api_alchemer):
    status, encuesta = ruteo.despachar("POST", "/encuestas", {
        "nombre": "Ola 4", "tipo": "independiente", "fecha_campo": "2026-11-20", "alchemer_id": "7001"},
        {}, actor("admin"), ctx)
    assert encuesta["alchemer"]["aviso"] is None
    # Alguien sacó la pregunta oculta en Alchemer después de vincularla.
    api_alchemer.captura["7001"] = False
    status, salida = ruteo.despachar("PATCH", f"/encuestas/{encuesta['id']}",
                                     {"estado": "en_campo"}, {}, actor("admin"), ctx)
    assert status == 200 and "no guarda el parámetro" in salida["aviso_alchemer"]
    assert alchemer.de_encuesta(ctx.boveda, encuesta["id"])["captura_parametro"] is False


def test_si_alchemer_no_contesta_no_se_dice_que_captura(ctx, actor, api_alchemer, monkeypatch):
    """«No se pudo verificar» nunca es «sí»."""
    status, encuesta = ruteo.despachar("POST", "/encuestas", {
        "nombre": "Ola 4", "tipo": "independiente", "fecha_campo": "2026-11-20", "alchemer_id": "7001"},
        {}, actor("admin"), ctx)

    def caido(url):
        raise Conflicto("Alchemer respondió 503.")

    monkeypatch.setattr(alchemer, "_pedir", caido)
    status, salida = ruteo.despachar("PATCH", f"/encuestas/{encuesta['id']}",
                                     {"estado": "en_campo"}, {}, actor("admin"), ctx)
    assert "No se pudo verificar" in salida["aviso_alchemer"]
    # Vincular con Alchemer caído deja la encuesta vinculada, con el aviso.
    otra = encuestas.crear(ctx.boveda, None, "Otra", fecha_campo="2026-11-20")
    vinculo = alchemer.vincular(ctx.boveda, otra["id"], "7002")
    assert vinculo["vinculada"] and "no se pudo traer" in vinculo["aviso"]


class Proponente:
    def __init__(self):
        self.recibidas = None

    def proponer(self, existentes, preguntas):
        self.recibidas = preguntas
        return [{"tematica": "consumo de bebidas", "es_nueva": True,
                 "preguntas_relacionadas": ["P1"]}]


def test_las_tematicas_se_proponen_con_el_cuestionario_de_alchemer(ctx, actor, api_alchemer):
    """R-ALC.4 — antes del campo, sin SAV. Solo código y texto; no escribe."""
    ctx.proponente_tematicas = Proponente()
    status, encuesta = ruteo.despachar("POST", "/encuestas", {
        "nombre": "Ola 4", "tipo": "independiente", "fecha_campo": "2026-11-20", "alchemer_id": "7001"},
        {}, actor("admin"), ctx)
    status, salida = ruteo.despachar("POST", f"/encuestas/{encuesta['id']}/alchemer/tematicas",
                                     {}, {}, actor("analista"), ctx)
    assert status == 200
    enviadas = ctx.proponente_tematicas.recibidas
    assert enviadas and all(set(p) <= {"codigo", "texto"} for p in enviadas)
    assert {p["codigo"] for p in enviadas} == {"P1", "P2"}
    assert db.una(ctx.boveda, "select count(*)::int as n from encuesta_tematica")["n"] == 0


def test_actualizar_desde_alchemer_trae_el_cuestionario_de_nuevo(ctx, actor, api_alchemer):
    status, encuesta = ruteo.despachar("POST", "/encuestas", {
        "nombre": "Ola 4", "tipo": "independiente", "fecha_campo": "2026-11-20", "alchemer_id": "7002"},
        {}, actor("admin"), ctx)
    api_alchemer.captura["7002"] = True
    status, salida = ruteo.despachar("POST", f"/encuestas/{encuesta['id']}/alchemer", {}, {},
                                     actor("analista"), ctx)
    assert status == 200 and salida["aviso"] is None
    assert salida["cuestionario"]["captura_parametro"] is True


def test_el_cuestionario_vive_en_la_boveda_y_no_en_el_store_semantico(ctx, actor, api_alchemer):
    ruteo.despachar("POST", "/encuestas", {
        "nombre": "Ola 4", "tipo": "independiente", "fecha_campo": "2026-11-20", "alchemer_id": "7001"},
        {}, actor("admin"), ctx)
    assert db.una(ctx.boveda, "select count(*)::int as n from alchemer_cuestionario")["n"] == 1
    assert db.una(ctx.semantica, "select count(*)::int as n from pregunta")["n"] == 0
