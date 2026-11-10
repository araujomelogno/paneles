"""PEDIDO 06/11/26 · Parte 4 (D90) — audiencias, listados y composición.

El Definition of Done de la parte 4, contra Postgres:

* R-ENC.1 — las listas de respondentes y de convocatoria paginan de a 50 en
  el servidor, con búsqueda y filtros.
* R-ENC.2 — el listado de encuestas filtra por estado y por cliente.
* R-COMP.1 — los cruces se ven en cantidad, % de columna, % de fila y % del
  total, con sus bases y las bases chicas marcadas.
* R-AU.1 — el criterio «está en la audiencia / respondió la encuesta X»
  incluye y excluye.
* R-AU.2 — agregar audiencia desde una consulta excluye a quienes ya
  estaban, sortea con semilla, pasa por el camino de convocar y queda
  registrado.
* R-AU.3 — una encuesta independiente se crea vacía desde la sección
  Encuestas, con los datos del estudio.
"""

import pytest

from panel_api import (
    composicion, consultas, db, encuestas, lista_negra, paneles, personas, ruteo,
)
from panel_api.errores import Conflicto

from conftest import VERSION_TEXTO, consentimientos
from test_pedido_seleccion_aleatoria import ADULTOS, mil  # noqa: F401


def _personas(conn, cuantas, prefijo="P"):
    filas = db.todas(
        conn,
        """insert into persona (nombre, email, fecha_nacimiento)
           select %s || lpad(i::text, 4, '0'), lower(%s) || i || '@ej.uy', date '1990-01-01'
             from generate_series(1, %s) as i
        returning id_persona::text as id""", (prefijo, prefijo, cuantas))
    ids = [f["id"] for f in filas]
    db.ejecutar(conn, """insert into consentimiento (id_persona, finalidad, version_texto)
                         select unnest(%s::uuid[]), 'contacto_participacion', %s""",
                (ids, VERSION_TEXTO))
    return ids


# ── R-ENC.1 · Las listas, paginadas en el servidor ────────────────────

@pytest.fixture
def antel(conn_boveda):
    """Una encuesta con 120 personas: 70 respondieron. A 30 de las que no
    respondieron se les mandó la «Invitación»."""
    encuesta = encuestas.crear(conn_boveda, None, "ANTEL", tipo="independiente",
                               origen="consulta")
    ids = _personas(conn_boveda, 120)
    db.ejecutar(conn_boveda, """insert into participacion (encuesta_id, id_persona, convocado,
                                                           respondio)
                                select %s, unnest(%s::uuid[]), true, false""",
                (encuesta["id"], ids))
    encuestas.marcar_respuesta(conn_boveda, encuesta["id"], ids[:70])
    mensaje = db.una(conn_boveda, """insert into encuesta_mensaje (encuesta_id, nombre, canal,
                                                                   plantilla)
                                     values (%s, 'Invitación', 'whatsapp', 'inv')
                                     returning id""", (encuesta["id"],))["id"]
    db.ejecutar(conn_boveda, """insert into envio (encuesta_mensaje_id, id_persona, canal, estado)
                                select %s, unnest(%s::uuid[]), 'whatsapp', 'aceptado'""",
                (mensaje, ids[70:100]))
    conn_boveda.commit()
    return {"encuesta": encuesta, "ids": ids, "mensaje": mensaje}


def _pagina(ctx, actor, encuesta_id, **consulta):
    _, salida = ruteo.despachar("GET", f"/encuestas/{encuesta_id}/participacion", {},
                                {k: str(v) for k, v in consulta.items()},
                                actor("analista"), ctx)
    return salida


def test_la_lista_pagina_de_a_50_en_el_servidor(ctx, actor, antel):
    primera = _pagina(ctx, actor, antel["encuesta"]["id"])
    assert (len(primera["items"]), primera["total"], primera["paginas"]) == (50, 120, 3)
    tercera = _pagina(ctx, actor, antel["encuesta"]["id"], pagina=3)
    assert len(tercera["items"]) == 20
    vistas = {i["id_persona"] for i in primera["items"] + tercera["items"]}
    assert len(vistas) == 70
    # Los conteos de arriba son de toda la encuesta, no de la página.
    assert primera["conteos"] == {"convocados": 120, "respondieron": 70, "no_convocados": 0}


def test_se_busca_por_nombre_o_identificador(ctx, actor, antel):
    por_nombre = _pagina(ctx, actor, antel["encuesta"]["id"], buscar="P0042")
    assert [i["nombre"] for i in por_nombre["items"]] == ["P0042"]
    por_id = _pagina(ctx, actor, antel["encuesta"]["id"], buscar=antel["ids"][7][:13])
    assert antel["ids"][7] in {i["id_persona"] for i in por_id["items"]}
    por_correo = _pagina(ctx, actor, antel["encuesta"]["id"], buscar="p5@ej.uy")
    assert [i["email"] for i in por_correo["items"]] == ["p5@ej.uy"]


def test_los_filtros_se_combinan_y_traen_lo_que_recibio_cada_uno(ctx, actor, antel):
    salida = _pagina(ctx, actor, antel["encuesta"]["id"], respondio="no",
                     mensaje_id=antel["mensaje"])
    assert salida["total"] == 30
    assert all(not i["respondio"] for i in salida["items"])
    assert all(i["mensajes"][0]["mensaje"] == "Invitación" for i in salida["items"])
    sin_mensaje = _pagina(ctx, actor, antel["encuesta"]["id"], respondio="no",
                          mensaje_id=antel["mensaje"], recibio="no")
    assert sin_mensaje["total"] == 20
    assert _pagina(ctx, actor, antel["encuesta"]["id"], respondio="si")["total"] == 70
    assert _pagina(ctx, actor, antel["encuesta"]["id"], convocado="no")["total"] == 0


# ── R-ENC.2 · El listado de encuestas ─────────────────────────────────

def test_el_listado_filtra_por_estado_y_por_cliente(ctx, actor, conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    en_campo = encuestas.crear(conn_boveda, panel["id"], "En campo ANTEL", clientes=["ANTEL"])
    encuestas.cambiar_estado(conn_boveda, en_campo["id"], "en_campo")
    encuestas.crear(conn_boveda, panel["id"], "Borrador ANTEL", clientes=["ANTEL"])
    otra = encuestas.crear(conn_boveda, panel["id"], "En campo UTE", clientes=["UTE"])
    encuestas.cambiar_estado(conn_boveda, otra["id"], "en_campo")
    conn_boveda.commit()
    cliente = db.una(conn_boveda, "select id from cliente where nombre = 'antel'")["id"]

    def nombres(**consulta):
        _, salida = ruteo.despachar("GET", "/encuestas", {}, consulta, actor("analista"), ctx)
        return sorted(e["nombre"] for e in salida["items"])

    assert nombres(estado="en_campo") == ["En campo ANTEL", "En campo UTE"]
    assert nombres(cliente_id=str(cliente)) == ["Borrador ANTEL", "En campo ANTEL"]
    assert nombres(estado="en_campo", cliente_id=str(cliente)) == ["En campo ANTEL"]
    assert nombres(estado="cerrada") == []


# ── R-COMP.1 · Cruces en cantidad o en porcentaje ─────────────────────

def test_las_cuatro_vistas_de_un_cruce_con_sus_bases():
    celdas = [{"sexo": "F", "tramo": "18-29", "observados": 6},
              {"sexo": "F", "tramo": "30-44", "observados": 40},
              {"sexo": "M", "tramo": "18-29", "observados": 6},
              {"sexo": "M", "tramo": "30-44", "observados": 48}]
    tabla = composicion.tabla_de_cruce(celdas, "sexo", "tramo", umbral=30)
    vistas = tabla["vistas"]
    assert vistas["n"]["valores"] == [[6, 40], [6, 48]]
    columna = vistas["columna"]
    for j in range(2):
        assert sum(fila[j] for fila in columna["valores"]) == pytest.approx(1.0)
    assert columna["bases"]["columnas"] == [12, 88]
    # Una columna con 12 personas: base chica.
    assert columna["bases_chicas"]["columnas"] == [True, False]
    fila = vistas["fila"]
    assert [sum(f) for f in fila["valores"]] == [pytest.approx(1.0)] * 2
    assert fila["bases"]["filas"] == [46, 54]
    total = vistas["total"]
    assert sum(sum(f) for f in total["valores"]) == pytest.approx(1.0)
    assert total["bases"]["total"] == 100 and total["bases_chicas"]["total"] is False


def test_el_umbral_de_base_chica_es_configurable(monkeypatch):
    monkeypatch.setenv("COMPOSICION_BASE_CHICA", "50")
    assert composicion.umbral_base_chica() == 50
    celdas = [{"a": "x", "b": "y", "observados": 40}]
    assert composicion.tabla_de_cruce(celdas, "a", "b")["vistas"]["total"]["bases_chicas"] \
        == {"total": True}


def test_la_composicion_trae_la_tabla_del_cruce_en_todos_los_ambitos(ctx, actor, conn_boveda):
    for i in range(12):
        personas.alta(conn_boveda, {
            "persona": {"documento": f"5-{i}", "nombre": f"P{i}", "sexo": "F" if i % 3 else "M",
                        "fecha_nacimiento": "1990-01-01" if i % 2 else "1960-01-01"},
            "consentimientos": consentimientos("contacto_participacion")})
    conn_boveda.commit()
    _, salida = ruteo.despachar("GET", "/composicion", {},
                                {"ambito": "todos", "cruce": "sexo,tramo_etario",
                                 "base_chica": "5"}, actor("analista"), ctx)
    tabla = salida["cruce"]["tabla"]
    assert tabla["umbral_base_chica"] == 5
    assert tabla["totales"]["total"] == 12
    assert set(salida["vistas_de_cruce"]) == {"n", "columna", "fila", "total"}


def test_en_una_encuesta_el_cruce_de_convocados_tambien_se_lee_en_porcentaje(
        ctx, actor, conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    ids = []
    for i in range(6):
        ids.append(personas.alta(conn_boveda, {
            "persona": {"documento": f"6-{i}", "nombre": f"Q{i}", "sexo": "F" if i % 2 else "M",
                        "fecha_nacimiento": "1990-01-01"},
            "consentimientos": consentimientos("contacto_participacion")})["id_persona"])
        paneles.agregar_miembro(conn_boveda, panel["id"], ids[-1])
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    encuestas.marcar_respuesta(conn_boveda, encuesta["id"], ids[:3])
    conn_boveda.commit()
    _, salida = ruteo.despachar("GET", "/composicion", {},
                                {"ambito": "encuesta", "encuesta_id": str(encuesta["id"]),
                                 "cruce": "sexo,tramo_etario"}, actor("analista"), ctx)
    assert salida["cruce"]["tabla"]["totales"]["total"] == 3
    assert salida["cruce_convocados"]["tabla"]["totales"]["total"] == 6


# ── R-AU.1 · Criterio de participación en una encuesta ───────────────

@pytest.fixture
def ola_anterior(conn_boveda, mil):  # noqa: F811
    """La ola anterior: 200 personas en su audiencia, 80 respondieron."""
    encuesta = encuestas.crear(conn_boveda, None, "Ola anterior", tipo="independiente",
                               origen="consulta")
    ids = [str(f["id_persona"]) for f in db.todas(
        conn_boveda, "select id_persona from persona order by nombre limit 200")]
    encuestas.convocar(conn_boveda, encuesta["id"], ids, _desde_consulta=True)
    encuestas.marcar_respuesta(conn_boveda, encuesta["id"], ids[:80])
    conn_boveda.commit()
    return {"encuesta": encuesta, "ids": ids}


def _criterio(encuesta_id, operador, relacion="audiencia"):
    return {"tipo": "demografico", "dimension": "encuesta", "valor": encuesta_id,
            "operador": operador, "relacion": relacion}


def test_el_criterio_de_encuesta_incluye_y_excluye(ctx_solo_boveda, ola_anterior):
    encuesta_id = ola_anterior["encuesta"]["id"]
    contar = lambda *criterios: consultas.ejecutar(  # noqa: E731
        ctx_solo_boveda, {"criterios": list(criterios), "limite": 10})["total"]
    assert contar(_criterio(encuesta_id, "in")) == 200
    assert contar(_criterio(encuesta_id, "not_in")) == 800
    assert contar(_criterio(encuesta_id, "in", "respondio")) == 80
    assert contar(_criterio(encuesta_id, "not_in", "respondio")) == 920
    # Combinable con lo demás.
    assert contar(*ADULTOS, _criterio(encuesta_id, "in", "respondio")) == 80


# ── R-AU.2 · Agregar audiencia desde una consulta ─────────────────────

def test_agregar_audiencia_excluye_a_los_que_estaban_sortea_y_queda_registrado(
        ctx_solo_boveda, actor, ola_anterior):
    encuesta_id = ola_anterior["encuesta"]["id"]
    definicion = {"criterios": [*ADULTOS, _criterio(encuesta_id, "not_in")], "limite": 50}
    resultado = consultas.ejecutar(ctx_solo_boveda, definicion)
    assert resultado["total"] == 800
    assert not set(i["id_persona"] for i in resultado["items"]) & set(ola_anterior["ids"])

    vetada = resultado["items"][0]["id_persona"]
    lista_negra.agregar(ctx_solo_boveda.boveda, vetada, "fraude_duplicacion", "duplicado")
    ctx_solo_boveda.boveda.commit()

    _, salida = ruteo.despachar(
        "POST", f"/encuestas/{encuesta_id}/audiencia",
        {"resultado": resultado, "definicion": definicion, "cantidad": 100}, {},
        actor("operaciones"), ctx_solo_boveda)
    assert salida["en_la_seleccion"] == 100
    assert salida["agregadas"] == 99
    assert salida["excluidas"]["en_lista_negra"] == 1
    assert salida["convocados"] == 299
    assert salida["semilla"] == resultado["seleccion"]["semilla"]

    # Las 100 son las primeras del sorteo, no las primeras por nombre.
    sorteadas = consultas.sortear(
        [str(f["id_persona"]) for f in db.todas(ctx_solo_boveda.boveda,
                                                "select id_persona from persona")
         if str(f["id_persona"]) not in set(ola_anterior["ids"])],
        resultado["seleccion"]["semilla"])[:100]
    nuevas = {str(f["id_persona"]) for f in db.todas(
        ctx_solo_boveda.boveda, "select id_persona from participacion where encuesta_id = %s",
        (encuesta_id,))} - set(ola_anterior["ids"])
    assert nuevas == set(sorteadas) - {vetada}

    _, pasos = ruteo.despachar("GET", f"/encuestas/{encuesta_id}/audiencia", {}, {},
                               actor("analista"), ctx_solo_boveda)
    [paso] = pasos["items"]
    assert (paso["via"], paso["modo"], paso["pedidas"], paso["agregadas"]) == (
        "agregar_desde_consulta", "aleatorio", 100, 99)
    assert paso["actor"] == "operaciones@equipos.com.uy"
    assert paso["definicion"]["criterios"][1]["dimension"] == "encuesta"


def test_agregar_todo_el_segmento_y_no_dos_veces(ctx_solo_boveda, actor, ola_anterior):
    encuesta_id = ola_anterior["encuesta"]["id"]
    definicion = {"criterios": [*ADULTOS, _criterio(encuesta_id, "in", "respondio")]}
    resultado = consultas.ejecutar(ctx_solo_boveda, definicion)
    salida = encuestas.agregar_audiencia_desde_consulta(
        ctx_solo_boveda.boveda, encuesta_id, resultado, definicion,
        segmento_completo=True)
    assert salida["modo"] == "segmento_completo"
    assert (salida["en_la_seleccion"], salida["agregadas"]) == (80, 0)
    assert salida["excluidas"]["ya_estaban"] == 80


def test_una_encuesta_cerrada_no_admite_agregar_audiencia(ctx_solo_boveda, ola_anterior):
    encuesta_id = ola_anterior["encuesta"]["id"]
    encuestas.cambiar_estado(ctx_solo_boveda.boveda, encuesta_id, "cerrada")
    resultado = consultas.ejecutar(ctx_solo_boveda, {"criterios": ADULTOS})
    with pytest.raises(Conflicto) as error:
        encuestas.agregar_audiencia_desde_consulta(
            ctx_solo_boveda.boveda, encuesta_id, resultado, {"criterios": ADULTOS},
            cantidad=10)
    assert error.value.detalle["motivo"] == "encuesta_cerrada"


# ── R-AU.3 · Crear una encuesta desde la sección Encuestas ────────────

def test_una_independiente_se_crea_vacia_con_los_datos_del_estudio(ctx, actor):
    _, encuesta = ruteo.despachar(
        "POST", "/encuestas",
        {"tipo": "independiente", "nombre": "Estudio en pasos", "fecha_campo": "2026-12-01",
         "publico_objetivo": "Adultos", "clientes": ["ANTEL"],
         "url_alchemer": "https://survey.alchemer.com/s3/1/x?pid=",
         "tematicas": [{"nombre": "telefonía móvil", "origen": "manual"}]}, {},
        actor("operaciones"), ctx)
    assert (encuesta["tipo"], encuesta["origen"], encuesta["panel_id"]) == (
        "independiente", "manual", None)
    assert encuesta["url_alchemer"].endswith("?pid=")
    assert [t["nombre"] for t in encuesta["tematicas"]] == ["telefonía móvil"]
    detalle = encuestas.obtener(ctx.boveda, encuesta["id"], completa=True)
    assert detalle["convocados"] == 0 and not detalle["con_convocatoria"]
    assert [c["nombre"] for c in detalle["clientes"]] == ["antel"]
    # Se completa a mano, como cualquier independiente con convocatoria.
    persona = personas.alta(ctx.boveda, {
        "persona": {"documento": "8-1", "nombre": "Ana", "email": "ana@ej.uy"},
        "consentimientos": consentimientos("contacto_participacion")})
    salida = encuestas.agregar_panelistas(ctx.boveda, encuesta["id"], "ana@ej.uy")
    assert salida["conteo"]["agregado"] == 1
    assert persona["id_persona"]
