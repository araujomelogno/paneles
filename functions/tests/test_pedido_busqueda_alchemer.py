"""PEDIDO de búsqueda y enlaces de Alchemer (D99), contra Postgres de verdad y
con una API de Alchemer simulada del tamaño de la cuenta real: 2.172
encuestas en 22 páginas, la más reciente en la última.

Definition of Done del pedido:

* Pegando la URL o el id se vincula en segundos: **una** llamada (R-ALB.1).
* La primera sincronización recorre las 22 páginas en segundo plano, una por
  tarea; la búsqueda responde desde el catálogo, sin llamar a Alchemer, e
  incluye encuestas de las últimas páginas (R-ALB.2).
* Un corte de tiempo es un error con su motivo (R-ALB.3).
* Con la respuesta real de la encuesta 9105516 se listan sus 7 enlaces con la
  URL completa en `https://` (R-ALB.5), y se puede cambiar de enlace después
  de vincular, con el aviso de los mensajes de WhatsApp.
"""

import datetime
import json
import math
import urllib.parse

import pytest

from panel_api import alchemer, alchemer_catalogo, db, diferida, encuestas, ruteo
from panel_api.errores import Conflicto, DatosInvalidos

ENTORNO = {"ALCHEMER_API_TOKEN": "tok", "ALCHEMER_API_SECRET": "sec"}
AVANTI = 9105516
TITULO_AVANTI = "AVANTI 2026 - S00726 - Evaluación de lanzamientos"

# La respuesta real de `GET /v5/survey/9105516/surveycampaign` (los siete
# enlaces activos), más una campaña de correo y un enlace cerrado.
CAMPANIAS_9105516 = [
    {"id": "26582567", "type": "SurveyCampaign", "link_type": "link", "subtype": "standard",
     "status": "Active", "name": "Default Link",
     "uri": "survey.alchemer.com/s3/9105516/AVANTI-2026-S00726-Evaluaci-n-de-lanzamientos",
     "SSL": "True", "link_open_date": None, "link_close_date": None},
    {"id": "26582601", "type": "SurveyCampaign", "link_type": "link", "status": "Active",
     "name": "Panel ", "uri": "encuestas.equipos.com.uy/s3/9105516/avanti-panel", "SSL": "True"},
    {"id": "26582602", "type": "SurveyCampaign", "link_type": "link", "status": "Active",
     "name": "WhatsApp", "uri": "encuestas.equipos.com.uy/s3/9105516/avanti-wa", "SSL": "True"},
    {"id": "26582603", "type": "SurveyCampaign", "link_type": "link", "status": "Active",
     "name": "Redes", "uri": "survey.alchemer.com/s3/9105516/avanti-redes?origen=ig",
     "SSL": "True"},
    {"id": "26582604", "type": "SurveyCampaign", "link_type": "link", "status": "Active",
     "name": "Correo masivo", "uri": "survey.alchemer.com/s3/9105516/avanti-mail", "SSL": "True"},
    {"id": "26582605", "type": "SurveyCampaign", "link_type": "link", "status": "Active",
     "name": "Telefónico", "uri": "encuestas.equipos.com.uy/s3/9105516/avanti-cati",
     "SSL": "True"},
    {"id": "26582606", "type": "SurveyCampaign", "link_type": "link", "status": "Active",
     "name": "Piloto", "uri": "survey.alchemer.com/s3/9105516/avanti-piloto", "SSL": "True"},
    {"id": "26582607", "type": "SurveyCampaign", "link_type": "link", "status": "Closed",
     "name": "Prueba interna", "uri": "survey.alchemer.com/s3/9105516/avanti-prueba",
     "SSL": "True"},
    {"id": "26582608", "type": "SurveyCampaign", "link_type": "email", "status": "Active",
     "name": "Invitación por correo", "uri": None, "SSL": "True"},
]

BASE = datetime.datetime(2019, 1, 1)


def _fecha(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class AlchemerFalso:
    """La cuenta: 2.172 encuestas, de la más vieja a la más nueva (la API no
    promete otro orden), con la 9105516 al final. `filtro` dice si aplica el
    `filter[...]` por fecha de modificación."""

    def __init__(self, cantidad=2172, filtro=True):
        self.filtro = filtro
        self.pedidos = []
        self.lentas = set()
        self.fallan = {}
        self.encuestas = [
            {"id": 7000000 + i, "title": f"Estudio {i:04d}",
             "status": "Closed" if i % 3 == 0 else "Launched",
             "created_on": _fecha(BASE + datetime.timedelta(hours=i)),
             "modified_on": _fecha(BASE + datetime.timedelta(hours=i))}
            for i in range(cantidad - 1)]
        self.encuestas.append({"id": AVANTI, "title": TITULO_AVANTI, "status": "Launched",
                               "created_on": "2026-11-01 09:00:00",
                               "modified_on": "2026-11-24 18:30:00"})

    def __call__(self, url):
        partes = urllib.parse.urlsplit(url)
        camino = partes.path.split("/v5/", 1)[1]
        consulta = dict(urllib.parse.parse_qsl(partes.query))
        self.pedidos.append(camino)
        if camino in self.lentas:
            raise Conflicto(alchemer.MENSAJE_TIEMPO, {"motivo": "alchemer_tiempo"})
        if camino in self.fallan:
            raise Conflicto("Alchemer respondió 400.",
                            {"status": self.fallan[camino], "motivo": "alchemer_error"})
        if camino == "survey":
            todas = self.encuestas
            if self.filtro and consulta.get("filter[field][0]") == "modified_on":
                todas = [e for e in todas if e["modified_on"] >= consulta["filter[value][0]"]]
            por = int(consulta.get("resultsperpage", 50))
            pagina = int(consulta.get("page", 1))
            return {"result_ok": True, "total_count": len(todas), "page": pagina,
                    "total_pages": max(1, math.ceil(len(todas) / por)),
                    "results_per_page": por,
                    "data": todas[(pagina - 1) * por: pagina * por]}
        trozos = camino.split("/")
        sid = int(trozos[1])
        if len(trozos) == 3 and trozos[2] == "surveycampaign":
            return {"result_ok": True, "data": CAMPANIAS_9105516 if sid == AVANTI else []}
        encuesta = next(e for e in self.encuestas if e["id"] == sid)
        return {"result_ok": True, "data": {
            **encuesta, "pages": [{"id": 1, "questions": [
                {"id": 2, "_subtype": "radio", "shortname": "P1",
                 "title": {"Spanish": "¿Conocía el lanzamiento?"},
                 "options": [{"value": "1", "title": {"Spanish": "Sí"}},
                             {"value": "2", "title": {"Spanish": "No"}}]},
                {"id": 9, "_subtype": "hidden", "title": {"Spanish": "pid"},
                 "properties": {"defaulttext": '[url("pid")]'}}]}]}}


@pytest.fixture
def api(monkeypatch):
    falso = AlchemerFalso()
    for clave, valor in ENTORNO.items():
        monkeypatch.setenv(clave, valor)
    monkeypatch.setattr(alchemer, "_pedir", falso)
    return falso


def _sincronizar(ctx, api, actor):
    """«Actualizar» y después lo que haría Cloud Tasks: una tarea por página,
    contando las llamadas de cada una."""
    status, salida = ruteo.despachar("POST", "/alchemer/sincronizacion", {}, {},
                                     actor("analista"), ctx)
    assert status == 202
    llamadas_por_tarea = []
    while ctx.encolador_alchemer.encoladas:
        sid, pagina = ctx.encolador_alchemer.encoladas.pop(0)
        antes = len(api.pedidos)
        alchemer_catalogo.procesar_pagina(ctx.boveda, sid, pagina,
                                          encolador=ctx.encolador_alchemer)
        llamadas_por_tarea.append(len(api.pedidos) - antes)
    return salida, llamadas_por_tarea


# ════════════════════════════════════════════════════════════════════
#  R-ALB.1 · Pegar la URL o el id
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("texto", [
    "9105516", " 9105516 ",
    "https://survey.alchemer.com/s3/9105516/AVANTI-2026-S00726-Evaluaci-n-de-lanzamientos",
    "survey.alchemer.com/s3/9105516/AVANTI",
    "https://encuestas.equipos.com.uy/s3/9105516/avanti-wa?pid=",
    "https://app.alchemer.com/builder/build/id/9105516",
    "https://app.alchemer.com/survey/id/9105516/results",
    "https://app.alchemer.com/projects/survey?id=9105516",
])
def test_el_id_sale_de_la_url_publica_la_de_edicion_o_el_id_solo(texto):
    assert alchemer.id_de(texto) == "9105516"


@pytest.mark.parametrize("texto", ["", "avanti", "https://alchemer.com/precios", "12"])
def test_lo_que_no_es_una_encuesta_no_se_adivina(texto):
    with pytest.raises(DatosInvalidos):
        alchemer.id_de(texto)


def test_pegar_la_url_resuelve_la_encuesta_con_una_sola_llamada(ctx, actor, api):
    status, salida = ruteo.despachar(
        "POST", "/alchemer/resolver",
        {"texto": "https://survey.alchemer.com/s3/9105516/AVANTI-2026"}, {},
        actor("analista"), ctx)
    assert status == 200 and salida["id"] == "9105516"
    assert salida["titulo"] == TITULO_AVANTI and salida["captura_parametro"] is True
    assert api.pedidos == ["survey/9105516"]


def test_vincular_pegando_la_url_guarda_el_enlace_y_refresca_el_catalogo(ctx, actor, api):
    status, encuesta = ruteo.despachar("POST", "/encuestas", {
        "nombre": "Avanti", "tipo": "independiente", "fecha_campo": "2026-11-25",
        "alchemer_id": "https://survey.alchemer.com/s3/9105516/AVANTI",
        "alchemer_enlace_id": "26582602",
        "url_alchemer": "https://encuestas.equipos.com.uy/s3/9105516/avanti-wa?pid="},
        {}, actor("analista"), ctx)
    assert status == 201
    fila = db.una(ctx.boveda, "select alchemer_id, alchemer_enlace_id from encuesta where id = %s",
                  (encuesta["id"],))
    assert (fila["alchemer_id"], fila["alchemer_enlace_id"]) == ("9105516", "26582602")
    # El catálogo estaba vacío y la encuesta vinculada queda en él.
    catalogo = db.todas(ctx.boveda, "select id, titulo, estado from alchemer_encuesta")
    assert [(c["id"], c["estado"]) for c in catalogo] == [("9105516", "Launched")]
    # Vincular es una llamada: la encuesta trae sus preguntas en las páginas.
    assert api.pedidos == ["survey/9105516"]


# ════════════════════════════════════════════════════════════════════
#  R-ALB.2 · El catálogo
# ════════════════════════════════════════════════════════════════════

def test_la_primera_sincronizacion_recorre_las_22_paginas_de_a_una(ctx, actor, api):
    salida, llamadas = _sincronizar(ctx, api, actor)
    assert salida["modo"] == "completa" and salida["estado"] == "en_curso"
    assert llamadas == [1] * 22                    # una llamada por tarea, sin tope
    assert db.una(ctx.boveda, "select count(*)::int as n from alchemer_encuesta")["n"] == 2172
    estado = alchemer_catalogo.estado(ctx.boveda)["ultima"]
    assert estado["estado"] == "terminada" and estado["paginas_total"] == 22
    assert estado["paginas_procesadas"] == 22 and estado["avance"] == 1.0


def test_la_busqueda_responde_desde_el_catalogo_e_incluye_las_ultimas_paginas(
        ctx, actor, api):
    _sincronizar(ctx, api, actor)
    api.pedidos.clear()
    status, salida = ruteo.despachar("GET", "/alchemer/encuestas", None,
                                     {"q": "evaluacion de LANZAMIENTOS"}, actor("analista"), ctx)
    assert status == 200 and api.pedidos == []          # sin llamar a Alchemer
    assert [i["id"] for i in salida["items"]] == ["9105516"]   # estaba en la página 22
    assert salida["sincronizacion"]["ultima_terminada"]["estado"] == "terminada"

    # Por defecto, activas y las más recientes primero; con el filtro, todas.
    _, activas = ruteo.despachar("GET", "/alchemer/encuestas", None, {"q": "estudio"},
                                 actor("analista"), ctx)
    assert all(i["estado"] != "Closed" for i in activas["items"])
    assert activas["items"][0]["titulo"] == "Estudio 2170"
    assert activas["hay_mas"] is True and len(activas["items"]) == 50
    _, todas = ruteo.despachar("GET", "/alchemer/encuestas", None,
                               {"q": "estudio 2169", "todas": "1"}, actor("analista"), ctx)
    assert [i["estado"] for i in todas["items"]] == ["Closed"]


def test_sin_catalogo_la_busqueda_lo_dice_y_ofrece_pegar_la_url(ctx, actor, api):
    _, salida = ruteo.despachar("GET", "/alchemer/encuestas", None, {"q": "x"},
                                actor("analista"), ctx)
    assert salida["items"] == [] and "pegá la URL" in salida["avisos"][0]
    assert api.pedidos == []


def test_la_siguiente_es_incremental_si_alchemer_aplica_el_filtro(ctx, actor, api):
    _sincronizar(ctx, api, actor)
    api.encuestas[5]["modified_on"] = "2026-11-25 10:00:00"
    api.encuestas[5]["title"] = "Estudio 0005 corregido"
    api.pedidos.clear()
    salida, llamadas = _sincronizar(ctx, api, actor)
    assert salida["modo"] == "incremental"
    assert llamadas == [1]                               # dos encuestas modificadas: 1 página
    ultima = alchemer_catalogo.estado(ctx.boveda)["ultima"]
    assert (ultima["estado"], ultima["filtro"], ultima["eliminadas"]) == \
        ("terminada", "aplicado", 0)
    assert db.una(ctx.boveda, "select titulo from alchemer_encuesta where id = '7000005'")[
        "titulo"] == "Estudio 0005 corregido"
    assert db.una(ctx.boveda, "select count(*)::int as n from alchemer_encuesta")["n"] == 2172


def test_si_alchemer_ignora_el_filtro_recorre_todo(ctx, actor, api):
    """R-ALB.4 — no se confía en el filtro: si vuelve una encuesta anterior
    a la fecha pedida, la corrida pasa a completa y lo dice."""
    _sincronizar(ctx, api, actor)
    api.filtro = False
    del api.encuestas[10]                               # se borró en Alchemer
    salida, llamadas = _sincronizar(ctx, api, actor)
    ultima = alchemer_catalogo.estado(ctx.boveda)["ultima"]
    assert (ultima["modo"], ultima["filtro"], ultima["estado"]) == \
        ("completa", "ignorado", "terminada")
    assert llamadas[0] == 2 and llamadas[1:] == [1] * 21   # la 1 se pidió de nuevo, sin filtro
    # Una completa borra lo que ya no existe en Alchemer.
    assert ultima["eliminadas"] == 1
    assert not db.una(ctx.boveda, "select 1 as hay from alchemer_encuesta where id = '7000010'")


def test_si_alchemer_rechaza_el_filtro_tambien_recorre_todo(ctx, actor, api):
    _sincronizar(ctx, api, actor)
    original = api.__call__

    def rechaza_filtro(url):
        if "filter" in url:
            raise Conflicto("Alchemer respondió 400.", {"status": 400, "motivo": "alchemer_error"})
        return original(url)

    ctx.boveda.commit()
    salida, _ = ruteo.despachar("POST", "/alchemer/sincronizacion", {}, {}, actor("admin"), ctx)
    while ctx.encolador_alchemer.encoladas:
        sid, pagina = ctx.encolador_alchemer.encoladas.pop(0)
        alchemer_catalogo.procesar_pagina(ctx.boveda, sid, pagina,
                                          encolador=ctx.encolador_alchemer, pedir=rechaza_filtro)
    ultima = alchemer_catalogo.estado(ctx.boveda)["ultima"]
    assert (ultima["modo"], ultima["filtro"], ultima["estado"]) == \
        ("completa", "rechazado", "terminada")


def test_una_pagina_lenta_se_reintenta_y_una_rechazada_no_borra_nada(ctx, actor, api):
    status, salida = ruteo.despachar("POST", "/alchemer/sincronizacion", {}, {},
                                     actor("admin"), ctx)
    sid = salida["id"]
    alchemer_catalogo.procesar_pagina(ctx.boveda, sid, 1, encolador=ctx.encolador_alchemer)
    ctx.encolador_alchemer.encoladas.clear()
    # Lo transitorio se propaga: Cloud Tasks reintenta.
    api.lentas.add("survey")
    with pytest.raises(Conflicto):
        alchemer_catalogo.procesar_pagina(ctx.boveda, sid, 5)
    ctx.boveda.rollback()
    api.lentas.clear()
    # Lo que no es transitorio deja la página fallida y la corrida sigue.
    api.fallan["survey"] = 400
    alchemer_catalogo.procesar_pagina(ctx.boveda, sid, 7)
    api.fallan.clear()
    for pagina in range(2, 23):
        if pagina != 7:
            alchemer_catalogo.procesar_pagina(ctx.boveda, sid, pagina)
    ultima = alchemer_catalogo.estado(ctx.boveda)["ultima"]
    assert ultima["estado"] == "fallida" and ultima["paginas_fallidas"] == 1
    assert ultima["eliminadas"] == 0                     # con una falla, no se borra nada
    assert "1 página(s)" in ultima["error"]


def test_una_sola_en_curso_y_la_estancada_se_reanuda(ctx, actor, api):
    _, primera = ruteo.despachar("POST", "/alchemer/sincronizacion", {}, {}, actor("admin"), ctx)
    _, segunda = ruteo.despachar("POST", "/alchemer/sincronizacion", {}, {}, actor("admin"), ctx)
    assert segunda["id"] == primera["id"] and segunda["ya_en_curso"] is True
    assert len(ctx.encolador_alchemer.encoladas) == 1
    # La base no admite dos en curso aunque alguien se saltee `lanzar`.
    with pytest.raises(Exception, match="una_en_curso"):
        db.ejecutar(ctx.boveda, "insert into alchemer_sincronizacion (modo) values ('completa')")
    ctx.boveda.rollback()
    # Nadie tomó la página 1 en 20 minutos: «Actualizar» la vuelve a encolar.
    db.ejecutar(ctx.boveda, """update alchemer_sincronizacion
                                  set iniciada_en = now() - interval '20 minutes'""")
    ctx.boveda.commit()
    assert alchemer_catalogo.estado(ctx.boveda)["ultima"]["estancada"] is True
    _, tercera = ruteo.despachar("POST", "/alchemer/sincronizacion", {}, {}, actor("admin"), ctx)
    assert tercera["reanudada"] is True and tercera["paginas_reencoladas"] == 1
    assert tercera["estancada"] is False
    assert len(ctx.encolador_alchemer.encoladas) == 2


def test_la_tarea_va_por_la_cola_de_la_ingesta_con_su_tipo():
    class Cliente:
        def __init__(self):
            self.tareas = []

        def task_path(self, *partes):
            return "/".join(partes)

        def queue_path(self, *partes):
            return "/".join(partes)

        def create_task(self, request):
            self.tareas.append(request["task"])

    cliente = Cliente()
    encolador = diferida.EncoladorCloudTasks(proyecto="p", url="https://f", cuenta="c",
                                             cliente=cliente, prefijo="alchemer",
                                             tipo=alchemer_catalogo.TIPO_TAREA)
    encolador.encolar(3, 7)
    tarea = cliente.tareas[0]
    assert json.loads(tarea["http_request"]["body"])["data"] == {
        "trabajo_id": 3, "indice": 7, "tipo": "alchemer_catalogo"}
    assert tarea["name"].endswith("alchemer-3-7-0")
    assert encolador.cola == "procesaringesta"
    import pathlib
    main = (pathlib.Path(__file__).resolve().parents[1] / "main.py").read_text()
    assert 'datos.get("tipo") == "alchemer_catalogo"' in main


# ════════════════════════════════════════════════════════════════════
#  R-ALB.3 · Nada gira para siempre
# ════════════════════════════════════════════════════════════════════

def test_un_corte_de_tiempo_es_un_error_con_su_motivo(ctx, actor, api):
    api.lentas.add("survey/9105516")
    with pytest.raises(Conflicto) as error:
        ruteo.despachar("POST", "/alchemer/resolver", {"texto": "9105516"}, {},
                        actor("analista"), ctx)
    assert error.value.detalle["motivo"] == "alchemer_tiempo"
    assert str(error.value.mensaje) == "Alchemer tardó demasiado en responder."


def test_el_pedido_real_tiene_tiempo_maximo(monkeypatch):
    import socket
    import urllib.request

    vistos = {}

    def urlopen(url, timeout=None):
        vistos["timeout"] = timeout
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(Conflicto) as error:
        alchemer._pedir("https://api.alchemer.com/v5/survey/1")
    assert vistos["timeout"] == alchemer.TIEMPO_POR_DEFECTO_S < 60
    assert error.value.detalle["motivo"] == "alchemer_tiempo"


# ════════════════════════════════════════════════════════════════════
#  R-ALB.5 · Los enlaces
# ════════════════════════════════════════════════════════════════════

def test_los_siete_enlaces_reales_con_la_url_completa(ctx, actor, api):
    status, salida = ruteo.despachar("GET", "/alchemer/encuestas/9105516/enlaces", None, {},
                                     actor("analista"), ctx)
    assert api.pedidos == ["survey/9105516/surveycampaign"]
    activos = [e for e in salida["items"] if e["activo"]]
    assert len(activos) == 7 and len(salida["items"]) == 8   # el de correo no es un enlace
    assert all(e["url"].startswith("https://") for e in salida["items"])
    por_nombre = {e["nombre"]: e for e in salida["items"]}
    assert "Panel" in por_nombre                              # sin el espacio del final
    assert por_nombre["Default Link"]["url_con_parametro"] == (
        "https://survey.alchemer.com/s3/9105516/"
        "AVANTI-2026-S00726-Evaluaci-n-de-lanzamientos?pid=")
    assert por_nombre["Redes"]["url_con_parametro"].endswith("?origen=ig&pid=")
    assert por_nombre["WhatsApp"]["url"].startswith("https://encuestas.equipos.com.uy/")
    assert por_nombre["Prueba interna"]["activo"] is False
    assert salida["items"][-1]["nombre"] == "Prueba interna"   # los cerrados, al final


def test_sin_ssl_el_enlace_va_en_http_y_type_vale_si_falta_link_type():
    assert alchemer.url_de_enlace({"uri": "s.alchemer.com/s3/1/x", "SSL": "False"}) == \
        "http://s.alchemer.com/s3/1/x"
    assert alchemer._es_enlace({"type": "link"}) is True
    assert alchemer._es_enlace({"type": "SurveyCampaign", "link_type": "link"}) is True
    assert alchemer._es_enlace({"type": "SurveyCampaign"}) is False


def test_cambiar_de_enlace_avisa_por_las_plantillas_de_whatsapp(ctx, actor, api):
    url_vieja = ("https://survey.alchemer.com/s3/9105516/"
                 "AVANTI-2026-S00726-Evaluaci-n-de-lanzamientos?pid=")
    status, encuesta = ruteo.despachar("POST", "/encuestas", {
        "nombre": "Avanti", "tipo": "independiente", "fecha_campo": "2026-11-25",
        "alchemer_id": "9105516", "alchemer_enlace_id": "26582567",
        "url_alchemer": url_vieja}, {}, actor("analista"), ctx)
    db.ejecutar(ctx.boveda, """
        insert into encuesta_mensaje (encuesta_id, nombre, canal, plantilla, plantilla_idioma,
                                      boton, url_boton)
        values (%s, 'Invitación', 'whatsapp', 'invitacion_avanti', 'es', 'url', %s)
    """, (encuesta["id"], url_vieja))
    ctx.boveda.commit()
    camino = f"/encuestas/{encuesta['id']}/alchemer/enlace"

    status, previa = ruteo.despachar("POST", camino,
                                     {"enlace_id": "26582602", "solo_revisar": True}, {},
                                     actor("analista"), ctx)
    nueva = "https://encuestas.equipos.com.uy/s3/9105516/avanti-wa?pid="
    assert (previa["url_actual"], previa["url_nueva"], previa["cambia"]) == \
        (url_vieja, nueva, True)
    assert previa["avisos"][0]["motivo"] == "mensajes_bloqueados_por_url"
    assert "«Invitación»" in previa["avisos"][0]["mensaje"]
    # La revisión no guardó nada.
    assert encuestas.obtener(ctx.boveda, encuesta["id"])["url_alchemer"] == url_vieja

    status, salida = ruteo.despachar("POST", camino, {"enlace_id": "26582602"}, {},
                                     actor("analista"), ctx)
    assert salida["url_alchemer"] == nueva and salida["alchemer_enlace_id"] == "26582602"
    assert salida["avisos"][0]["motivo"] == "mensajes_bloqueados_por_url"
    leida = encuestas.obtener(ctx.boveda, encuesta["id"])
    assert leida["alchemer_enlace_id"] == "26582602"

    # Un id de enlace que no es de esa encuesta no se guarda.
    with pytest.raises(Exception, match="no tiene el enlace"):
        ruteo.despachar("POST", camino, {"enlace_id": "999"}, {}, actor("analista"), ctx)
