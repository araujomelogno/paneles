"""R7.3 y R7.6 — la ficha del panelista, y lo que puede y no puede mostrar.

Las dos pruebas que cuidan el diseño y no la funcionalidad son
`test_la_ficha_no_devuelve_ningun_dato_identificatorio` —si alguien agrega
el nombre «porque es cómodo», la seudonimización deja de ser verdad y la
auditoría de reidentificación deja de reflejar quién vio qué— y
`test_ver_las_respuestas_queda_registrado`, que es el precio de cruzar los
dos stores a propósito.
"""

import pytest

from panel_api import (auditoria, db, encuestas, ficha, ingesta, paneles,
                       personas, ruteo)
from panel_api.errores import NoEncontrado

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")


@pytest.fixture
def con_respuestas(ctx, conn_boveda, conn_semantica, proveedor):
    """Una persona con tres respuestas repartidas en dos estudios."""
    panel = paneles.crear(conn_boveda, "Panel bebidas")
    id_persona = personas.alta(conn_boveda, {
        "persona": {"nombre": "Ana Pérez", "documento": "4.123.456-7",
                    "email": "ana@ej.uy", "celular": "+59899111222"},
        "consentimientos": consentimientos(*AMBAS),
        "origen": "dooblo", "id_en_origen": "R-001",
    })["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    olas = {
        "Ola marzo": [("P1", "¿Qué bebida prefiere?", "Fernet"),
                      ("P2", "¿Por qué?", "Me gusta el amargo")],
        "Ola abril": [("Q1", "¿Qué marca fumás?", "Nevada")],
    }
    for nombre, preguntas in olas.items():
        enc = encuestas.crear(conn_boveda, panel["id"], nombre)
        resultado = ingesta.ingestar(
            conn_boveda, conn_semantica, enc,
            [{"codigo": c, "texto": x, "tipo": "abierta", "orden": i + 1}
             for i, (c, x, _) in enumerate(preguntas)],
            [dict({"id_en_origen": "R-001"},
                  **{c: v for c, _, v in preguntas})],
            columna_id="id_en_origen", origen="dooblo", proveedor=proveedor)
        # Que el fixture construya lo que dice: si la ingesta no escribió,
        # las pruebas de abajo pasarían sin probar nada.
        assert resultado["respuestas_escritas"] == len(preguntas), resultado
    conn_semantica.commit()
    conn_boveda.commit()

    assert len(ficha.estudios_con_respuestas(conn_semantica, id_persona)) == 2
    return id_persona


# ── R7.3 · la ficha seudónima ────────────────────────────────────────

def test_la_ficha_no_devuelve_ningun_dato_identificatorio(
        conn_boveda, con_respuestas):
    salida = ficha.seudonima(conn_boveda, con_respuestas)
    texto = repr(salida)
    for prohibido in ficha.CAMPOS_PROHIBIDOS:
        assert prohibido not in salida
    # Y tampoco los valores, por si viajaran con otra etiqueta.
    for valor in ("Ana Pérez", "4.123.456-7", "ana@ej.uy", "+59899111222"):
        assert valor not in texto


def test_la_ficha_trae_atributos_y_paneles(conn_boveda, con_respuestas):
    salida = ficha.seudonima(conn_boveda, con_respuestas)
    assert salida["id_persona"] == str(con_respuestas)
    assert isinstance(salida["atributos"], list)
    assert [p["nombre"] for p in salida["paneles"]] == ["Panel bebidas"]


def test_una_persona_que_no_existe_da_no_encontrado(conn_boveda):
    with pytest.raises(NoEncontrado):
        ficha.seudonima(conn_boveda, "00000000-0000-0000-0000-000000000000")


# ── R7.6 · las respuestas procesadas ─────────────────────────────────

def test_las_respuestas_traen_codigo_pregunta_y_procedencia(
        conn_semantica, con_respuestas):
    salida = ficha.respuestas(conn_semantica, con_respuestas)
    assert salida["total"] >= 1
    fila = salida["items"][0]
    for clave in ("codigo", "pregunta", "respuesta", "estudio", "fecha_campo",
                  "texto_embebido"):
        assert clave in fila


def test_el_texto_embebido_esta_disponible(conn_semantica, con_respuestas):
    """Es la única forma de ver que una cerrada quedó sin traducir."""
    salida = ficha.respuestas(conn_semantica, con_respuestas)
    embebidos = [f["texto_embebido"] for f in salida["items"]]
    assert all(e for e in embebidos)
    assert any("→" in e or "->" in e for e in embebidos)


def test_la_tabla_pagina_en_la_base(conn_semantica, con_respuestas):
    """No se trae todo y se recorta: el límite viaja en el SQL."""
    pagina1 = ficha.respuestas(conn_semantica, con_respuestas, tamano=1,
                               pagina=1)
    assert len(pagina1["items"]) == 1
    assert pagina1["total"] >= 2
    assert pagina1["paginas"] == pagina1["total"]

    pagina2 = ficha.respuestas(conn_semantica, con_respuestas, tamano=1,
                               pagina=2)
    assert pagina2["items"][0] != pagina1["items"][0]


def test_se_puede_filtrar_por_estudio(conn_semantica, con_respuestas):
    estudios = ficha.estudios_con_respuestas(conn_semantica, con_respuestas)
    assert len(estudios) >= 1
    uno = estudios[0]
    salida = ficha.respuestas(conn_semantica, con_respuestas,
                              ref_estudio=uno["ref_estudio"])
    assert salida["total"] == uno["respuestas"]
    assert {f["estudio"] for f in salida["items"]} == {uno["estudio"]}


def test_se_puede_buscar_por_texto(conn_semantica, con_respuestas):
    salida = ficha.respuestas(conn_semantica, con_respuestas, busqueda="bebida")
    assert salida["total"] >= 1
    assert all("bebida" in f["pregunta"].lower() for f in salida["items"])


def test_un_panelista_sin_respuestas_lo_dice(conn_boveda, conn_semantica):
    id_persona = personas.alta(conn_boveda, {
        "persona": {"nombre": "Sin Datos", "documento": "9000001-1"},
        "consentimientos": consentimientos(*AMBAS),
    })["id_persona"]
    salida = ficha.respuestas(conn_semantica, id_persona)
    assert salida["total"] == 0
    assert salida["items"] == []
    assert salida["sin_respuestas"] is True


def test_una_busqueda_sin_resultados_no_se_confunde_con_no_tener_nada(
        conn_semantica, con_respuestas):
    """«No encontré eso» y «no tiene respuestas» son pantallas distintas."""
    salida = ficha.respuestas(conn_semantica, con_respuestas,
                              busqueda="zzz-no-existe")
    assert salida["total"] == 0
    assert salida["sin_respuestas"] is False


def test_ver_las_respuestas_queda_registrado(
        ctx, actor, conn_boveda, conn_semantica, con_respuestas):
    """El precio de cruzar los dos stores, y con su motivo propio."""
    antes = len(auditoria.listar_reidentificaciones(
        conn_boveda, id_persona=str(con_respuestas)))

    ficha.respuestas_con_registro(
        conn_boveda, conn_semantica, con_respuestas,
        actor=actor("analista"))

    filas = auditoria.listar_reidentificaciones(
        conn_boveda, id_persona=str(con_respuestas))
    assert len(filas) == antes + 1
    assert filas[0]["motivo"] == ficha.MOTIVO_RESPUESTAS
    assert filas[0]["motivo"] != "ficha"


def test_la_ficha_seudonima_no_registra_reidentificacion(
        conn_boveda, con_respuestas):
    """Mirar demográficos no es reidentificar, y anotarlo como si lo fuera
    llenaría de ruido el registro que tiene que poder leerse."""
    antes = len(auditoria.listar_reidentificaciones(
        conn_boveda, id_persona=str(con_respuestas)))
    ficha.seudonima(conn_boveda, con_respuestas)
    despues = len(auditoria.listar_reidentificaciones(
        conn_boveda, id_persona=str(con_respuestas)))
    assert despues == antes


# ── Por la ruta ──────────────────────────────────────────────────────

def test_por_la_ruta_la_ficha_no_trae_pii(ctx, actor, con_respuestas):
    status, salida = ruteo.despachar(
        "GET", f"/panelistas/{con_respuestas}/ficha", {}, {},
        actor("analista"), ctx)
    assert status == 200
    assert "Ana Pérez" not in repr(salida)


def test_por_la_ruta_las_respuestas_registran(ctx, actor, conn_boveda,
                                              con_respuestas):
    status, salida = ruteo.despachar(
        "GET", f"/panelistas/{con_respuestas}/respuestas", {}, {},
        actor("analista"), ctx)
    assert status == 200
    assert salida["total"] >= 1
    filas = auditoria.listar_reidentificaciones(
        conn_boveda, id_persona=str(con_respuestas))
    assert filas[0]["motivo"] == ficha.MOTIVO_RESPUESTAS


# ── R7.3 · la evidencia del resultado ────────────────────────────────
#
# El informe de la Fase 7 (`specs/INFORME_fase7_que_falta.md`) encontró que
# la ruta estaba declarada con `requisito="R7.3"` y no devolvía la mitad del
# requisito: «muestra también la evidencia del resultado: qué respondió y de
# qué estudio». Estas pruebas son esa mitad.

def _respuesta_ids(conn_semantica, id_persona):
    return [f["respuesta_id"] for f in db.todas(
        conn_semantica,
        "select respuesta_id from v_respuesta_estudio where id_persona = %s "
        " order by respuesta_id", (str(id_persona),))]


def test_la_ficha_trae_la_evidencia_que_se_le_pasa(
        ctx, actor, conn_semantica, con_respuestas):
    ids = _respuesta_ids(conn_semantica, con_respuestas)[:2]
    status, salida = ruteo.despachar(
        "GET", f"/panelistas/{con_respuestas}/ficha", {},
        {"respuestas": ",".join(str(i) for i in ids)}, actor("analista"), ctx)
    assert status == 200
    evidencia = salida["evidencia"]
    assert [e["respuesta_id"] for e in evidencia["items"]] == ids
    primera = evidencia["items"][0]
    # Qué respondió y de qué estudio, que es lo que pide el requisito.
    assert primera["respuesta"] and primera["pregunta"] and primera["estudio"]
    assert "→" in primera["texto_embebido"]
    assert evidencia["descartadas"] == 0
    # Y sigue sin un solo dato identificatorio.
    assert "Ana Pérez" not in repr(salida)
    assert "ana@ej.uy" not in repr(salida)


def test_la_evidencia_respeta_el_orden_del_ranking(conn_semantica,
                                                   con_respuestas):
    ids = list(reversed(_respuesta_ids(conn_semantica, con_respuestas)))
    salida = ficha.evidencia(conn_semantica, con_respuestas, ids)
    assert [e["respuesta_id"] for e in salida["items"]] == ids


def test_la_ficha_no_sirve_para_leer_respuestas_de_otro(
        conn_boveda, conn_semantica, con_respuestas, proveedor):
    """Un `respuesta_id` de otra persona se descarta: la ficha no es una vía
    lateral para leer contenido ajeno sin pasar por R7.6, que registra."""
    otra = personas.alta(conn_boveda, {
        "persona": {"nombre": "Otra", "documento": "5.555.555-5"},
        "consentimientos": consentimientos(*AMBAS),
        "origen": "dooblo", "id_en_origen": "R-002",
    })["id_persona"]
    panel = paneles.crear(conn_boveda, "Panel otra")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola de otra")
    ingesta.ingestar(
        conn_boveda, conn_semantica, enc,
        [{"codigo": "Z1", "texto": "¿Algo?", "tipo": "abierta", "orden": 1}],
        [{"id_en_origen": "R-002", "Z1": "Secreto"}],
        columna_id="id_en_origen", origen="dooblo", proveedor=proveedor)
    ajena = _respuesta_ids(conn_semantica, otra)
    propia = _respuesta_ids(conn_semantica, con_respuestas)[:1]

    salida = ficha.evidencia(conn_semantica, con_respuestas, propia + ajena)
    assert [e["respuesta_id"] for e in salida["items"]] == propia
    assert salida["descartadas"] == len(ajena)
    assert "Secreto" not in repr(salida)


def test_sin_evidencia_la_ficha_no_abre_el_store_semantico(
        ctx, actor, con_respuestas):
    ctx.abrio_semantica = False
    status, salida = ruteo.despachar(
        "GET", f"/panelistas/{con_respuestas}/ficha", {}, {},
        actor("analista"), ctx)
    assert status == 200
    assert "evidencia" not in salida
    assert not ctx.abrio_semantica


def test_la_evidencia_no_registra_reidentificacion(
        ctx, actor, conn_boveda, conn_semantica, con_respuestas):
    """Es contenido atado a un id opaco —lo mismo que ya mostraba la lista—,
    no identidad más contenido. Lo que se audita es R7.6."""
    ids = _respuesta_ids(conn_semantica, con_respuestas)
    ruteo.despachar(
        "GET", f"/panelistas/{con_respuestas}/ficha", {},
        {"respuestas": ",".join(map(str, ids))}, actor("analista"), ctx)
    assert auditoria.listar_reidentificaciones(
        conn_boveda, id_persona=str(con_respuestas)) == []


def test_ids_de_evidencia_ignora_basura_y_tiene_tope():
    assert ficha.ids_de_evidencia("3, x, 3, 7") == [3, 7]
    assert ficha.ids_de_evidencia(None) == []
    muchos = ",".join(str(i) for i in range(100))
    assert len(ficha.ids_de_evidencia(muchos)) == ficha.EVIDENCIAS_MAXIMAS
