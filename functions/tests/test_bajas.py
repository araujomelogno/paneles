"""Cascada de retiro de consentimiento: PII local + embeddings remotos +
salida del muestreo."""

import pytest

from panel_api import bajas, consentimiento, db, encuestas, paneles, personas
from panel_api.errores import DatosInvalidos, NoEncontrado

from conftest import consentimientos

PREGUNTAS = [{"codigo": "P1", "texto": "¿Qué bebida consume?", "tipo": "abierta"}]
AMBAS = ("contacto_participacion", "uso_semantico")


@pytest.fixture
def panelista_con_datos(local, remota, proveedor):
    """Un panelista enrolado, en un panel, convocado y con respuestas
    ingestadas de los dos lados."""
    panel = paneles.crear(local, "Panel bebidas")
    id_persona = personas.alta(
        local,
        {
            "persona": {"documento": "4.123.456-7", "nombre": "Ana Pérez",
                        "email": "ana@ej.uy"},
            "consentimientos": consentimientos(*AMBAS),
            "origen": "dooblo",
            "id_en_origen": "R-001",
        },
    )["id_persona"]
    paneles.agregar_miembro(local, panel["id"], id_persona)
    encuesta = encuestas.crear(local, panel["id"], "Ola 1")
    encuestas.convocar(local, encuesta["id"], todo_el_panel=True)
    encuestas.ingestar(
        local, remota, encuesta["id"], PREGUNTAS,
        [{"id_en_origen": "R-001", "P1": "Fernet"}], proveedor=proveedor,
    )
    return {"panel": panel, "encuesta": encuesta, "id_persona": id_persona}


def _cuenta_local(conn, tabla, id_persona):
    return db.una(
        conn, f"select count(*)::int as n from {tabla} where id_persona = %s", (id_persona,)
    )["n"]


def _respuestas_remotas(conn, id_persona):
    return db.una(
        conn,
        """
        select count(*)::int as n
          from respuesta r join individuo i on i.id = r.individuo_id
         where i.id_persona = %s
        """,
        (str(id_persona),),
    )["n"]


def test_retiro_total_borra_pii_local_embeddings_remotos_y_saca_del_muestreo(
    local, remota, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]
    assert _respuestas_remotas(remota, id_persona) == 1

    resultado = bajas.retirar(local, id_persona, bajas.TODAS, actor="uid-dpo",
                              conn_remoto=remota)

    assert resultado["pii_borrada"]
    assert resultado["remoto"]["estado"] == "ok"
    # PII local: no queda nada.
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 0
    for tabla in ("alias_origen", "membresia", "consentimiento", "participacion"):
        assert _cuenta_local(local, tabla, id_persona) == 0
    # Embeddings remotos: tampoco.
    assert _respuestas_remotas(remota, id_persona) == 0
    assert db.una(
        remota, "select count(*)::int as n from individuo where id_persona = %s",
        (str(id_persona),),
    )["n"] == 0


def test_el_retiro_total_deja_lapida_auditable_sin_pii(local, remota, panelista_con_datos):
    id_persona = panelista_con_datos["id_persona"]
    bajas.retirar(local, id_persona, bajas.TODAS, actor="uid-dpo", conn_remoto=remota)

    lapida = db.una(
        local, "select * from persona_borrada where id_persona = %s", (id_persona,)
    )
    assert lapida["motivo"] == "retiro_consentimiento"
    assert lapida["solicitado_por"] == "uid-dpo"
    assert lapida["borrado_remoto_en"] is not None
    # La lápida solo guarda el token opaco: ningún dato identificatorio.
    assert set(lapida) == {
        "id_persona", "motivo", "finalidad", "solicitado_por",
        "borrado_local_en", "borrado_remoto_en", "remoto_error",
    }


def test_retirar_solo_uso_semantico_borra_embeddings_y_deja_a_la_persona_en_el_panel(
    local, remota, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]

    resultado = bajas.retirar(
        local, id_persona, consentimiento.SEMANTICO, conn_remoto=remota
    )

    assert not resultado["pii_borrada"]
    assert _respuestas_remotas(remota, id_persona) == 0
    assert db.una(local, "select count(*)::int as n from persona")["n"] == 1
    # Sigue en el panel y sigue pudiendo ser convocada.
    assert len(paneles.listar_miembros(local, panelista_con_datos["panel"]["id"])) == 1
    assert consentimiento.esta_vigente(local, id_persona, consentimiento.CONTACTO)
    assert not consentimiento.esta_vigente(local, id_persona, consentimiento.SEMANTICO)


def test_retirar_solo_contacto_lo_saca_del_muestreo_y_deja_los_embeddings(
    local, remota, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]

    resultado = bajas.retirar(
        local, id_persona, consentimiento.CONTACTO, conn_remoto=remota
    )

    assert resultado["membresias_dadas_de_baja"] == 1
    assert paneles.listar_miembros(local, panelista_con_datos["panel"]["id"]) == []
    assert _respuestas_remotas(remota, id_persona) == 1
    assert consentimiento.esta_vigente(local, id_persona, consentimiento.SEMANTICO)


def test_quien_se_dio_de_baja_no_puede_volver_a_ser_convocado(
    local, remota, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]
    encuesta = panelista_con_datos["encuesta"]
    bajas.retirar(local, id_persona, consentimiento.CONTACTO, conn_remoto=remota)

    resultado = encuestas.convocar(local, encuesta["id"], ids_persona=[id_persona])
    assert resultado["convocados_total"] == 0
    assert resultado["sin_consentimiento"] == [id_persona]


def test_si_el_remoto_falla_el_retiro_local_igual_se_completa_y_queda_pendiente(
    local, remota, panelista_con_datos
):
    id_persona = panelista_con_datos["id_persona"]

    # Sin conexión al remoto: la baja local no puede quedar esperando.
    resultado = bajas.retirar(local, id_persona, bajas.TODAS, conn_remoto=None)

    assert resultado["pii_borrada"]
    assert resultado["remoto"]["estado"] == "pendiente"
    pendientes = bajas.pendientes_de_borrado_remoto(local)
    assert [p["id_persona"] for p in pendientes] == [id_persona]

    # El reintento cierra el círculo.
    bajas.reintentar_borrado_remoto(local, remota)
    assert bajas.pendientes_de_borrado_remoto(local) == []
    assert _respuestas_remotas(remota, id_persona) == 0


def test_retiro_sobre_persona_inexistente_da_404(local):
    with pytest.raises(NoEncontrado):
        bajas.retirar(local, "3f5b8a1e-0000-4000-8000-000000000099")


def test_finalidad_desconocida_se_rechaza(local, panelista_con_datos):
    with pytest.raises(DatosInvalidos):
        bajas.retirar(local, panelista_con_datos["id_persona"], "marketing")
