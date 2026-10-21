"""R-LN — La lista negra (SPEC_clientes_lista_negra_y_mejoras, D80).

Quien está en la lista negra no se convoca —ni desde `paneles` ni desde
COLOQUIO— y eso lo decide la base (`v_persona_convocable`, boveda/0031).
Lo que **no** hace: quitarle el consentimiento (su contenido sigue entrando
al store semántico), ocultarlo de los resultados (se marca) ni avisarle.

Corre contra Postgres de verdad: la exclusión es una vista, la cancelación
de invitaciones un `delete`, y la historia una restricción de unicidad
parcial.
"""

import pathlib
import sys

import pytest

from panel_api import (
    bajas, consentimiento, db, encuestas, inscripciones as ins, lista_negra,
    paneles, personas, portal, preferencias, ruteo,
)
from panel_api.errores import Conflicto, DatosInvalidos, SinPermiso

from test_fase3_crecimiento import _inscribir, con_texto  # noqa: F401

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

import verificar_coloquio as vc  # noqa: E402

DETALLE = "Respondió en 40 segundos un cuestionario de 15 minutos."


@pytest.fixture
def gente(conn_boveda, alta_basica):
    """Tres personas con las dos finalidades, en un panel."""
    panel = paneles.crear(conn_boveda, "Panel Nacional")
    ids = []
    for i, sexo in enumerate(("F", "F", "M"), start=1):
        alta = personas.alta(conn_boveda, alta_basica(
            nombre=f"Persona {i}", documento=f"1.111.11{i}-1", sexo=sexo,
            email=f"p{i}@ejemplo.uy",
            finalidades=("contacto_participacion", "uso_semantico")))
        paneles.agregar_miembro(conn_boveda, panel["id"], alta["id_persona"])
        ids.append(str(alta["id_persona"]))
    conn_boveda.commit()
    return {"panel": panel, "ids": ids}


def _agregar(ctx, actor, id_persona, rol="operaciones", **cuerpo):
    return ruteo.despachar(
        "POST", f"/panelistas/{id_persona}/lista-negra",
        {"categoria": "calidad_respuestas", "detalle": DETALLE, **cuerpo},
        {}, actor(rol), ctx)


# ════════════════════════════════════════════════════════════════════
#  R-LN.1 · Agregar
# ════════════════════════════════════════════════════════════════════

def test_agregar_registra_motivo_quien_y_cuando(ctx, actor, gente):
    status, entrada = _agregar(ctx, actor, gente["ids"][0])
    assert status == 201
    assert entrada["categoria"] == "calidad_respuestas"
    assert entrada["detalle"] == DETALLE
    assert entrada["agregado_por"] == "operaciones@equipos.com.uy"
    assert entrada["agregado_en"] and entrada["activa"]


@pytest.mark.parametrize("cuerpo, campo", [
    ({"categoria": None}, "categoria"),
    ({"categoria": "mala_persona"}, "categoria"),
    ({"detalle": ""}, "detalle"),
    ({"detalle": "  mal "}, "detalle"),
])
def test_el_motivo_es_obligatorio(ctx, actor, gente, cuerpo, campo):
    with pytest.raises(DatosInvalidos) as error:
        _agregar(ctx, actor, gente["ids"][0], **cuerpo)
    assert error.value.detalle["campo"] == campo
    assert lista_negra.activa_de(ctx.boveda, gente["ids"][0]) is None


def test_la_base_tambien_exige_el_detalle(conn_boveda, gente):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        db.ejecutar(conn_boveda,
                    "insert into lista_negra (id_persona, categoria, detalle) "
                    "values (%s, 'otro', '  ')", (gente["ids"][0],))


@pytest.mark.parametrize("rol", ["analista", "dpo"])
def test_agregar_es_de_operaciones_o_administracion(ctx, actor, gente, rol):
    with pytest.raises(SinPermiso):
        _agregar(ctx, actor, gente["ids"][0], rol=rol)


def test_no_se_agrega_dos_veces(ctx, actor, gente):
    _agregar(ctx, actor, gente["ids"][0])
    with pytest.raises(Conflicto):
        _agregar(ctx, actor, gente["ids"][0], rol="admin")


# ════════════════════════════════════════════════════════════════════
#  R-LN.2 · Efectos
# ════════════════════════════════════════════════════════════════════

def test_no_aparece_en_la_vista_de_convocables_ni_para_coloquio(
        ctx, actor, gente, dsn_boveda):
    """El punto que el pedido no traía: COLOQUIO convoca leyendo esta
    vista, con su propio rol."""
    en_lista = gente["ids"][0]
    _agregar(ctx, actor, en_lista)

    propias = {str(f["id_persona"]) for f in db.todas(
        ctx.boveda, "select id_persona from v_persona_convocable")}
    assert en_lista not in propias and set(gente["ids"][1:]) <= propias

    conn = vc.conectar(vc._dsn_con_usuario(dsn_boveda, "coloquio_app"))
    try:
        with conn.cursor() as cur:
            cur.execute("select id_persona from v_persona_convocable")
            desde_coloquio = {str(f["id_persona"]) for f in cur.fetchall()}
    finally:
        conn.close()
    assert en_lista not in desde_coloquio
    assert set(gente["ids"][1:]) <= desde_coloquio


def test_coloquio_no_ve_la_tabla(conn_boveda):
    assert not db.una(
        conn_boveda,
        "select has_table_privilege('coloquio_app', 'lista_negra', 'SELECT') as puede",
    )["puede"]


def test_el_contacto_se_corta_y_el_uso_semantico_no(ctx, actor, gente):
    """D80 — la lista negra no le quita a nadie el consentimiento: su
    contenido sigue entrando a las consultas. Lo que no se puede es
    convocarla."""
    en_lista = gente["ids"][0]
    _agregar(ctx, actor, en_lista)

    assert not consentimiento.esta_vigente(ctx.boveda, en_lista, consentimiento.CONTACTO)
    assert consentimiento.esta_vigente(ctx.boveda, en_lista, consentimiento.SEMANTICO)
    habilitadas, _ = consentimiento.filtrar_con_consentimiento(
        ctx.boveda, gente["ids"], consentimiento.SEMANTICO)
    assert en_lista in habilitadas


def test_las_estadisticas_de_consentimiento_no_la_cuentan_como_sin_el(
        ctx, actor, gente):
    from panel_api import estadisticas
    _agregar(ctx, actor, gente["ids"][0])
    por_finalidad = {f["finalidad"]: f for f in
                     estadisticas.consentimiento(ctx.boveda)["por_finalidad"]}
    assert por_finalidad["contacto_participacion"]["vigentes"] == 3
    assert por_finalidad["uso_semantico"]["vigentes"] == 3


def test_la_convocatoria_la_deja_afuera_y_dice_por_que(ctx, actor, gente):
    en_lista = gente["ids"][0]
    _agregar(ctx, actor, en_lista)
    encuesta = encuestas.crear(ctx.boveda, gente["panel"]["id"], "Ola 1")

    salida = encuestas.convocar(ctx.boveda, encuesta["id"], todo_el_panel=True)

    assert salida["en_lista_negra"] == 1
    assert salida["sin_consentimiento"] == []          # no se cuenta como eso
    assert salida["convocados_total"] == 2
    assert not db.una(ctx.boveda,
                      "select 1 from participacion where id_persona = %s", (en_lista,))


def test_al_agregarla_se_cancelan_sus_invitaciones_pendientes(ctx, actor, gente):
    en_lista = gente["ids"][0]
    abierta = encuestas.crear(ctx.boveda, gente["panel"]["id"], "Abierta")
    respondida = encuestas.crear(ctx.boveda, gente["panel"]["id"], "Respondida")
    cerrada = encuestas.crear(ctx.boveda, gente["panel"]["id"], "Cerrada")
    for encuesta in (abierta, respondida, cerrada):
        encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=[en_lista])
    db.ejecutar(ctx.boveda,
                "update participacion set respondio = true where encuesta_id = %s",
                (respondida["id"],))
    encuestas.cambiar_estado(ctx.boveda, cerrada["id"], "cerrada")

    _, entrada = _agregar(ctx, actor, en_lista)

    quedan = {f["encuesta_id"] for f in db.todas(
        ctx.boveda, "select encuesta_id from participacion where id_persona = %s",
        (en_lista,))}
    # Lo respondido es un hecho y lo cerrado ya no es una invitación.
    assert quedan == {respondida["id"], cerrada["id"]}
    assert [i["encuesta_id"] for i in entrada["invitaciones_canceladas"]] == [abierta["id"]]
    assert entrada["invitaciones_canceladas"][0]["encuesta"] == "Abierta"


def test_whatsapp_no_le_escribe(ctx, actor, gente):
    en_lista = gente["ids"][0]
    _agregar(ctx, actor, en_lista)
    _, excluidos = preferencias.filtrar_contactables(ctx.boveda, [en_lista], "whatsapp")
    assert excluidos == {"lista_negra": [en_lista]}


def _consulta(ctx, actor, rol):
    _, salida = ruteo.despachar(
        "POST", "/consultas",
        {"criterios": [{"tipo": "demografico", "dimension": "sexo",
                        "operador": "eq", "valor": "F"}]},
        {}, actor(rol), ctx)
    return {i["id_persona"]: i for i in salida["items"]}


def test_en_los_resultados_aparece_marcada_no_oculta(ctx, actor, gente):
    en_lista, otra = gente["ids"][0], gente["ids"][1]
    _agregar(ctx, actor, en_lista)

    para_analista = _consulta(ctx, actor, "analista")
    assert set(para_analista) == {en_lista, otra}
    assert para_analista[en_lista]["lista_negra"] == {"en_lista_negra": True}
    assert para_analista[otra]["lista_negra"] is None

    para_operaciones = _consulta(ctx, actor, "operaciones")
    marca = para_operaciones[en_lista]["lista_negra"]
    assert marca["detalle"] == DETALLE
    assert marca["categoria_etiqueta"] == "Calidad de respuestas"


def test_las_fichas_la_marcan_con_el_motivo_segun_el_permiso(ctx, actor, gente):
    en_lista = gente["ids"][0]
    _agregar(ctx, actor, en_lista)

    _, ficha = ruteo.despachar("GET", f"/panelistas/{en_lista}", {}, {},
                               actor("operaciones"), ctx)
    assert ficha["lista_negra"]["detalle"] == DETALLE
    _, seudonima = ruteo.despachar("GET", f"/panelistas/{en_lista}/ficha", {}, {},
                                   actor("analista"), ctx)
    assert seudonima["lista_negra"] == {"en_lista_negra": True}
    _, grilla = ruteo.despachar("GET", "/panelistas", {}, {}, actor("analista"), ctx)
    marcas = {i["id_persona"]: i["en_lista_negra"] for i in grilla["items"]}
    assert marcas[en_lista] is True and marcas[gente["ids"][1]] is False


def test_crear_un_panel_desde_la_consulta_la_excluye_y_lo_avisa(ctx, actor, gente):
    en_lista = gente["ids"][0]
    _agregar(ctx, actor, en_lista)
    resultado = {"items": [{"id_persona": i} for i in gente["ids"]]}

    _, panel = ruteo.despachar("POST", "/paneles/desde-consulta",
                               {"nombre": "Desde consulta", "resultado": resultado},
                               {}, actor("operaciones"), ctx)
    assert panel["excluidos_lista_negra"] == 1
    assert panel["miembros"] == 2
    miembros = {str(f["id_persona"]) for f in db.todas(
        ctx.boveda, "select id_persona from membresia where panel_id = %s", (panel["id"],))}
    assert en_lista not in miembros


def test_incluirla_en_el_panel_es_decision_de_quien_gestiona_la_lista(
        ctx, actor, gente):
    _agregar(ctx, actor, gente["ids"][0])
    resultado = {"items": [{"id_persona": i} for i in gente["ids"]]}
    cuerpo = {"nombre": "Con todos", "resultado": resultado, "incluir_lista_negra": True}

    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", "/paneles/desde-consulta", cuerpo, {},
                        actor("analista"), ctx)
    _, panel = ruteo.despachar("POST", "/paneles/desde-consulta", cuerpo, {},
                               actor("operaciones"), ctx)
    assert panel["miembros"] == 3 and panel["incluidos_en_lista_negra"] == 1


def test_si_se_reinscribe_por_la_landing_va_a_revision_marcada(
        ctx, actor, con_texto):  # noqa: F811
    """El dedup la reconoce por documento: sin la lista negra, sería
    `reutiliza` y se aprobaría de un clic."""
    alta = personas.alta(ctx.boveda, {
        "persona": {"nombre": "Ana Pérez", "documento": "1111111",
                    "email": "ana@ejemplo.uy"},
        "consentimientos": [{"finalidad": "contacto_participacion",
                             "version_texto": "2026-09"}]})
    id_persona = str(alta["id_persona"])
    _agregar(ctx, actor, id_persona)

    _inscribir(ctx.boveda)

    inscripcion = ins.listar(ctx.boveda)[0]
    assert inscripcion["resolucion"] == "revision"
    assert inscripcion["en_lista_negra"] is True
    assert inscripcion["id_persona_previa"] == id_persona
    detalle = ins.obtener(ctx.boveda, inscripcion["id"])
    assert [c["en_lista_negra"] for c in detalle["candidatos"]] == [True]

    # No se aprueba sola; eligiendo a la persona, sí, y sigue en la lista.
    with pytest.raises(Conflicto) as error:
        ins.aprobar(ctx.boveda, inscripcion["id"], actor("operaciones"))
    assert error.value.detalle["motivo"] == "en_lista_negra"
    ins.aprobar(ctx.boveda, inscripcion["id"], actor("operaciones"),
                id_persona=id_persona, enviar_acceso=lambda *a, **k: None)
    assert lista_negra.activa_de(ctx.boveda, id_persona) is not None


# ════════════════════════════════════════════════════════════════════
#  R-LN.3 · La sección, sacar e historial
# ════════════════════════════════════════════════════════════════════

def test_sacar_pide_motivo_y_conserva_la_historia(ctx, actor, gente):
    id_persona = gente["ids"][0]
    _agregar(ctx, actor, id_persona)
    remocion = f"/panelistas/{id_persona}/lista-negra/remocion"
    with pytest.raises(DatosInvalidos):
        ruteo.despachar("POST", remocion, {"motivo": ""}, {}, actor("operaciones"), ctx)

    _, sacada = ruteo.despachar("POST", remocion,
                                {"motivo": "Se revisó el caso: era un error de campo."},
                                {}, actor("admin"), ctx)
    assert sacada["activa"] is False and sacada["removido_por"] == "admin@equipos.com.uy"
    assert id_persona in {str(f["id_persona"]) for f in db.todas(
        ctx.boveda, "select id_persona from v_persona_convocable")}

    # Vuelve a entrar: la historia completa se ve, la más reciente primero.
    _agregar(ctx, actor, id_persona, categoria="fraude_duplicacion",
             detalle="Se inscribió dos veces con documentos distintos.")
    _, historial = ruteo.despachar("GET", f"/panelistas/{id_persona}/lista-negra",
                                   {}, {}, actor("dpo"), ctx)
    assert [(e["categoria"], e["activa"]) for e in historial["items"]] == [
        ("fraude_duplicacion", True), ("calidad_respuestas", False)]
    assert historial["items"][1]["motivo_remocion"].startswith("Se revisó")


def test_sacar_a_quien_no_esta_es_un_conflicto(ctx, actor, gente):
    with pytest.raises(Conflicto):
        ruteo.despachar("POST", f"/panelistas/{gente['ids'][0]}/lista-negra/remocion",
                        {"motivo": "No corresponde ya."}, {}, actor("admin"), ctx)


def test_la_seccion_pagina_y_busca(ctx, actor, gente):
    for id_persona in gente["ids"]:
        _agregar(ctx, actor, id_persona)
    _, pagina = ruteo.despachar("GET", "/lista-negra", {}, {"limite": "2"},
                                actor("dpo"), ctx)
    assert pagina["total"] == 3 and len(pagina["items"]) == 2
    _, resto = ruteo.despachar("GET", "/lista-negra", {}, {"limite": "2", "desde": "2"},
                               actor("dpo"), ctx)
    assert len(resto["items"]) == 1
    _, buscada = ruteo.despachar("GET", "/lista-negra", {}, {"q": "persona 2"},
                                 actor("operaciones"), ctx)
    assert [i["nombre"] for i in buscada["items"]] == ["Persona 2"]
    assert {c["clave"] for c in buscada["categorias"]} == set(lista_negra.CATEGORIAS)
    with pytest.raises(SinPermiso):
        ruteo.despachar("GET", "/lista-negra", {}, {}, actor("analista"), ctx)


def test_la_seccion_puede_mostrar_tambien_las_removidas(ctx, actor, gente):
    id_persona = gente["ids"][0]
    _agregar(ctx, actor, id_persona)
    lista_negra.sacar(ctx.boveda, id_persona, "Ya no corresponde.", actor="admin")
    _, activas = ruteo.despachar("GET", "/lista-negra", {}, {}, actor("dpo"), ctx)
    _, todas = ruteo.despachar("GET", "/lista-negra", {}, {"removidas": "1"},
                               actor("dpo"), ctx)
    assert activas["total"] == 0 and todas["total"] == 1


# ════════════════════════════════════════════════════════════════════
#  R-LN.4 · Privacidad y baja
# ════════════════════════════════════════════════════════════════════

def test_el_portal_no_lo_muestra(ctx, actor, gente):
    _agregar(ctx, actor, gente["ids"][0])
    perfil = portal.perfil(ctx.boveda, gente["ids"][0])
    assert "lista" not in repr(perfil).lower()
    assert DETALLE not in repr(perfil)
    fuente = (RAIZ / "functions" / "panel_api" / "portal.py").read_text(encoding="utf-8")
    assert "lista_negra" not in fuente


def test_la_baja_se_lleva_la_entrada(ctx, actor, gente):
    id_persona = gente["ids"][0]
    _agregar(ctx, actor, id_persona)
    bajas.retirar(ctx.boveda, id_persona, bajas.TODAS, actor="uid-dpo",
                  conn_semantica=ctx.semantica)
    assert not db.todas(ctx.boveda, "select 1 from lista_negra")
