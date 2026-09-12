"""Bloque 3A — salud del panel accionable: R3.1 a R3.6.

Una prueba por criterio del Definition of Done, más los casos borde que la
spec enumera en §5: el segmento con brecha sin salida limpia, el falso
positivo de speeder, el export sin datos de tiempo y los canjes concurrentes.
"""

import threading

import pytest

from panel_api import (
    calidad, composicion, db, encuestas, muestreo, paneles, personas,
    premios, puntos,
)
from panel_api.errores import DatosInvalidos

VERSION = "consentimiento-2026-01"


# ── Andamios ────────────────────────────────────────────────────────

def _persona(conn, panel_id, nombre, sexo="F", **extra):
    cuerpo = {
        "persona": {
            "nombre": nombre, "documento": f"doc-{nombre}", "sexo": sexo,
            "fecha_nacimiento": "1990-01-01", "localidad": "Montevideo",
            **extra,
        },
        "consentimientos": [
            {"finalidad": "contacto_participacion", "version_texto": VERSION}
        ],
        "panel_id": panel_id,
    }
    return personas.alta(conn, cuerpo)["id_persona"]


@pytest.fixture
def panel_con_brecha(conn_boveda):
    """Ocho mujeres y dos varones, con objetivo 50/50: la brecha está en M."""
    panel = paneles.crear(conn_boveda, "Panel con brecha")
    ids = {"F": [], "M": []}
    for i in range(10):
        sexo = "F" if i < 8 else "M"
        ids[sexo].append(_persona(conn_boveda, panel["id"], f"P{i:02}", sexo))
    composicion.guardar_objetivo(conn_boveda, panel["id"], [
        {"dimension": "sexo", "categoria": "F", "proporcion_objetivo": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion_objetivo": 0.5},
    ])
    conn_boveda.commit()
    return panel["id"], ids


# ── R3.1 · Muestreo ─────────────────────────────────────────────────

def test_la_propuesta_prioriza_el_segmento_con_brecha(conn_boveda, panel_con_brecha):
    panel_id, ids = panel_con_brecha
    encuesta = encuestas.crear(conn_boveda, panel_id, "Ola 1")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=2)

    assert propuesta["brecha_disponible"] is True
    assert [p["categoria"] for p in propuesta["propuesta"]] == ["M", "M"]
    assert {p["id_persona"] for p in propuesta["propuesta"]} == set(ids["M"])


def test_la_propuesta_no_convoca(conn_boveda, panel_con_brecha):
    """R3.1 — «la propuesta no convoca por sí sola»."""
    panel_id, _ = panel_con_brecha
    encuesta = encuestas.crear(conn_boveda, panel_id, "Ola 1")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=5)

    assert propuesta["convoca"] is False
    assert propuesta["propuesta"], "tiene que haber propuesto a alguien"
    convocados = db.una(
        conn_boveda,
        "select count(*)::int as n from participacion where encuesta_id = %s",
        (encuesta["id"],),
    )
    assert convocados["n"] == 0, "pedir una propuesta no puede crear participaciones"


def test_quien_no_tiene_consentimiento_nunca_aparece(conn_boveda):
    panel = paneles.crear(conn_boveda, "P")
    con = _persona(conn_boveda, panel["id"], "Con", "M")
    # Alguien en el panel sin consentimiento de contacto: se lo retira.
    sin = _persona(conn_boveda, panel["id"], "Sin", "M")
    db.ejecutar(
        conn_boveda,
        "update consentimiento set estado = 'retirado' where id_persona = %s",
        (sin,),
    )
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=10)

    propuestos = {p["id_persona"] for p in propuesta["propuesta"]}
    assert con in propuestos
    assert sin not in propuestos
    excluido = next(e for e in propuesta["excluidos"] if e["id_persona"] == sin)
    assert excluido["motivo"] == muestreo.SIN_CONSENTIMIENTO


def test_quien_esta_sobre_convocado_queda_afuera_y_se_explica(conn_boveda):
    panel = paneles.crear(conn_boveda, "P")
    quemado = _persona(conn_boveda, panel["id"], "Quemado", "M")
    fresco = _persona(conn_boveda, panel["id"], "Fresco", "M")
    # Tres convocatorias recientes: justo el default del umbral.
    for i in range(3):
        vieja = encuestas.crear(conn_boveda, panel["id"], f"Vieja {i}")
        encuestas.convocar(conn_boveda, vieja["id"], ids_persona=[quemado])
    conn_boveda.commit()
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Nueva")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=10)

    assert [p["id_persona"] for p in propuesta["propuesta"]] == [fresco]
    excluido = next(e for e in propuesta["excluidos"] if e["id_persona"] == quemado)
    assert excluido["motivo"] == muestreo.DEMASIADAS_RECIENTES
    assert "tope" in excluido["explicacion"]


def test_un_segmento_con_brecha_sin_elegibles_se_informa(conn_boveda, panel_con_brecha):
    """El caso feo de la spec: la brecha existe y no hay a quién ofrecer.

    Devolver una lista más corta sin decir nada haría creer al responsable
    que la brecha se está cerrando.
    """
    panel_id, ids = panel_con_brecha
    # Se quema a los dos varones, que son los únicos que cierran la brecha.
    for i in range(3):
        vieja = encuestas.crear(conn_boveda, panel_id, f"Vieja {i}")
        encuestas.convocar(conn_boveda, vieja["id"], ids_persona=ids["M"])
    conn_boveda.commit()
    encuesta = encuestas.crear(conn_boveda, panel_id, "Nueva")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=4)

    aviso = next(
        a for a in propuesta["avisos"] if a["tipo"] == "segmento_sin_elegibles"
    )
    assert aviso["categoria"] == "M"
    assert aviso["elegibles_encontrados"] == 0
    assert "no se cierra" in aviso["mensaje"]
    assert propuesta["propuesta"] == [], (
        "no puede completar con gente del segmento que sobra: empeoraría la brecha"
    )


def test_los_umbrales_de_fatiga_son_configurables(conn_boveda):
    """R3.1 — configurables, no hardcodeados, con default documentado."""
    panel = paneles.crear(conn_boveda, "P")
    conn_boveda.commit()

    por_defecto = muestreo.obtener_umbrales(conn_boveda, panel["id"])
    assert por_defecto["son_defaults"] is True
    assert por_defecto["max_convocatorias_ventana"] == \
        muestreo.DEFAULTS_FATIGA["max_convocatorias_ventana"]

    guardados = muestreo.guardar_umbrales(
        conn_boveda, panel["id"], {"max_convocatorias_ventana": 1}, None
    )
    assert guardados["son_defaults"] is False
    assert guardados["max_convocatorias_ventana"] == 1
    # Lo que no se manda conserva su valor.
    assert guardados["ventana_dias"] == muestreo.DEFAULTS_FATIGA["ventana_dias"]


def test_el_umbral_configurado_es_el_que_manda(conn_boveda):
    panel = paneles.crear(conn_boveda, "P")
    persona = _persona(conn_boveda, panel["id"], "Uno", "M")
    vieja = encuestas.crear(conn_boveda, panel["id"], "Vieja")
    encuestas.convocar(conn_boveda, vieja["id"], ids_persona=[persona])
    muestreo.guardar_umbrales(
        conn_boveda, panel["id"],
        {"max_convocatorias_ventana": 1, "dias_minimos_entre": 0}, None,
    )
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Nueva")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=5)

    assert propuesta["propuesta"] == []
    assert propuesta["excluidos"][0]["motivo"] == muestreo.DEMASIADAS_RECIENTES


def test_sin_objetivo_cargado_lo_dice_en_vez_de_fingir_que_prioriza(conn_boveda):
    panel = paneles.crear(conn_boveda, "Sin objetivo")
    _persona(conn_boveda, panel["id"], "Uno", "F")
    _persona(conn_boveda, panel["id"], "Dos", "M")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=2)

    assert propuesta["brecha_disponible"] is False
    assert any(a["tipo"] == "sin_objetivo" for a in propuesta["avisos"])
    assert len(propuesta["propuesta"]) == 2


# ── R3.2 · Calidad ──────────────────────────────────────────────────

def test_el_speeder_se_marca_con_su_motivo():
    es_speeder, detalle = calidad.evaluar_speeder(45, 120)
    assert es_speeder is True
    assert detalle["evaluado"] is True and detalle["umbral"] == 120


def test_sin_datos_de_tiempo_no_se_evalua_y_se_dice():
    """R3.2 — «silencio ≠ aprobado»."""
    veredicto, detalle = calidad.evaluar_speeder(None, 120)
    assert veredicto is None, "no evaluado no es lo mismo que aprobado"
    assert detalle["evaluado"] is False
    assert "no trajo duración" in detalle["motivo"]


def test_el_straightliner_se_detecta_por_varianza():
    recto, detalle = calidad.evaluar_straightliner([[3, 3, 3, 3]], 0.25)
    assert recto is True
    assert detalle["baterias"][0]["varianza"] == 0.0


def test_quien_responde_variado_no_es_straightliner():
    recto, _ = calidad.evaluar_straightliner([[1, 4, 2, 5]], 0.25)
    assert recto is False


def test_sin_bateria_el_straightliner_no_se_evalua():
    veredicto, detalle = calidad.evaluar_straightliner([[3, 3]], 0.25)
    assert veredicto is None
    assert detalle["evaluado"] is False


def test_una_bateria_sin_codigos_numericos_se_evalua_igual():
    """Sin números no hay varianza, pero «todas iguales» es la misma idea."""
    recto, detalle = calidad.evaluar_straightliner(
        [["Mucho", "Mucho", "Mucho", "Mucho"]], 0.25
    )
    assert recto is True
    assert detalle["baterias"][0]["todas_iguales"] is True


def test_los_duplicados_del_archivo_se_detectan():
    filas = [{"ID": "a1"}, {"ID": "a2"}, {"ID": "a1"}, {"ID": ""}]
    assert calidad.detectar_duplicados_en_filas(filas, "ID") == {"a1": 2}


def test_los_umbrales_de_calidad_son_por_estudio():
    """R3.2 — lo que es rápido en un cuestionario de 5 minutos no lo es en
    uno de 30."""
    default = calidad.umbrales_de({"umbral_speeder_segundos": None,
                                   "umbral_straightliner": None})
    assert default["speeder_segundos"] == calidad.SPEEDER_SEGUNDOS
    assert default["son_defaults"] is True

    propio = calidad.umbrales_de({"umbral_speeder_segundos": 600,
                                  "umbral_straightliner": 0.1})
    assert propio["speeder_segundos"] == 600
    assert propio["son_defaults"] is False


def test_marcar_es_reversible_y_queda_quien_lo_hizo(conn_boveda, actor):
    """R3.2 — un falso positivo de speeder le cuesta puntos a alguien real."""
    panel = paneles.crear(conn_boveda, "P")
    persona = _persona(conn_boveda, panel["id"], "Rapida", "F")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    encuestas.convocar(conn_boveda, encuesta["id"], ids_persona=[persona])
    db.ejecutar(
        conn_boveda,
        "update participacion set respondio = true, calidad_estado = 'sospechoso', "
        "motivo_calidad = 'speeder' where encuesta_id = %s",
        (encuesta["id"],),
    )
    conn_boveda.commit()
    fila = db.una(
        conn_boveda,
        "select id from participacion where encuesta_id = %s", (encuesta["id"],),
    )

    revisada = calidad.revisar(
        conn_boveda, fila["id"], "ok", actor("operaciones"),
        motivo="respondió rápido pero las abiertas están trabajadas",
    )

    assert revisada["estado_anterior"] == "sospechoso"
    assert revisada["estado"] == "ok"
    assert revisada["revisada_por"] == "uid-operaciones"
    assert revisada["habilita_liquidacion"] is True
    guardada = db.una(
        conn_boveda,
        "select calidad_estado, calidad_revisada_por, calidad_motivo_revision "
        "  from participacion where id = %s",
        (fila["id"],),
    )
    assert guardada["calidad_estado"] == "ok"
    assert guardada["calidad_revisada_por"] == "uid-operaciones"
    assert "trabajadas" in guardada["calidad_motivo_revision"]


# ── R3.3 · Ledger ───────────────────────────────────────────────────

def test_el_saldo_es_la_suma_de_los_movimientos(conn_boveda):
    panel = paneles.crear(conn_boveda, "P")
    persona = _persona(conn_boveda, panel["id"], "Ana")
    conn_boveda.commit()

    puntos.registrar(conn_boveda, persona, "ajuste", 100, motivo="a")
    puntos.registrar(conn_boveda, persona, "earn", 50, motivo="b")
    puntos.registrar(conn_boveda, persona, "canje", -30, motivo="c")
    conn_boveda.commit()

    assert puntos.saldo(conn_boveda, persona) == 120
    movimientos = puntos.movimientos(conn_boveda, persona)
    assert sum(m["puntos"] for m in movimientos) == 120, "reconstruible"


def test_el_saldo_nunca_queda_negativo(conn_boveda):
    panel = paneles.crear(conn_boveda, "P")
    persona = _persona(conn_boveda, panel["id"], "Ana")
    puntos.registrar(conn_boveda, persona, "ajuste", 10, motivo="poco")
    conn_boveda.commit()

    with pytest.raises(DatosInvalidos, match="Saldo insuficiente"):
        puntos.registrar(conn_boveda, persona, "canje", -50, motivo="mucho")
    conn_boveda.rollback()

    assert puntos.saldo(conn_boveda, persona) == 10


def test_el_vencimiento_descuenta_sin_borrar_movimientos(conn_boveda):
    """R3.3 — «no se borran movimientos»."""
    panel = paneles.crear(conn_boveda, "P")
    persona = _persona(conn_boveda, panel["id"], "Ana")
    conn_boveda.commit()
    db.ejecutar(
        conn_boveda,
        "insert into puntos_movimiento (id_persona, tipo, puntos, motivo, vence_en) "
        "values (%s, 'earn', 100, 'viejo', now() - interval '1 day')",
        (persona,),
    )
    conn_boveda.commit()
    antes = len(puntos.movimientos(conn_boveda, persona))

    resultado = puntos.vencer(conn_boveda, persona)

    assert resultado["total_descontado"] == 100
    assert puntos.saldo(conn_boveda, persona) == 0
    movimientos = puntos.movimientos(conn_boveda, persona)
    assert len(movimientos) == antes + 1, "el earn original sigue estando"
    vencimiento = next(m for m in movimientos if m["tipo"] == "vencimiento")
    assert vencimiento["puntos"] == -100
    assert vencimiento["origen_movimiento_id"] is not None


def test_vencer_no_cobra_dos_veces_lo_ya_gastado(conn_boveda):
    panel = paneles.crear(conn_boveda, "P")
    persona = _persona(conn_boveda, panel["id"], "Ana")
    conn_boveda.commit()
    db.ejecutar(
        conn_boveda,
        "insert into puntos_movimiento (id_persona, tipo, puntos, motivo, vence_en) "
        "values (%s, 'earn', 100, 'viejo', now() - interval '1 day')",
        (persona,),
    )
    conn_boveda.commit()
    puntos.registrar(conn_boveda, persona, "canje", -100, motivo="ya lo gastó")
    conn_boveda.commit()

    resultado = puntos.vencer(conn_boveda, persona)

    assert resultado["vencidos"][0]["descontados"] == 0
    assert puntos.saldo(conn_boveda, persona) == 0, "no puede quedar en -100"


# ── R3.4 · Ganar por calidad ────────────────────────────────────────

@pytest.fixture
def encuesta_respondida(conn_boveda):
    """Tres panelistas: una ok, uno sospechoso, una que no respondió."""
    panel = paneles.crear(conn_boveda, "P")
    ids = {
        nombre: _persona(conn_boveda, panel["id"], nombre, sexo)
        for nombre, sexo in (("Ana", "F"), ("Beto", "M"), ("Cora", "F"))
    }
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1")
    encuestas.convocar(conn_boveda, encuesta["id"], ids_persona=list(ids.values()))
    db.ejecutar(
        conn_boveda,
        "update participacion set respondio = true, calidad_estado = 'ok' "
        " where id_persona = %s",
        (ids["Ana"],),
    )
    db.ejecutar(
        conn_boveda,
        "update participacion set respondio = true, calidad_estado = 'sospechoso', "
        "motivo_calidad = 'speeder' where id_persona = %s",
        (ids["Beto"],),
    )
    conn_boveda.commit()
    return panel["id"], encuesta["id"], ids


def test_solo_gana_puntos_la_participacion_de_calidad(conn_boveda, encuesta_respondida):
    _, encuesta_id, ids = encuesta_respondida

    puntos.liquidar(conn_boveda, encuesta_id)

    assert puntos.saldo(conn_boveda, ids["Ana"]) == puntos.PUNTOS_POR_PARTICIPACION
    assert puntos.saldo(conn_boveda, ids["Beto"]) == 0, "sospechoso no gana"
    assert puntos.saldo(conn_boveda, ids["Cora"]) == 0, "no respondió, no gana"


def test_no_se_puede_liquidar_dos_veces(conn_boveda, encuesta_respondida):
    _, encuesta_id, ids = encuesta_respondida
    puntos.liquidar(conn_boveda, encuesta_id)
    saldo = puntos.saldo(conn_boveda, ids["Ana"])

    segunda = puntos.liquidar(conn_boveda, encuesta_id)

    assert segunda["liquidados"] == []
    assert puntos.saldo(conn_boveda, ids["Ana"]) == saldo


def test_el_indice_unico_impide_pagar_dos_veces_aunque_se_fuerce(
    conn_boveda, encuesta_respondida
):
    """La garantía no es la comprobación en Python: es el índice."""
    _, encuesta_id, ids = encuesta_respondida
    puntos.liquidar(conn_boveda, encuesta_id)

    import psycopg

    with pytest.raises(psycopg.errors.UniqueViolation):
        db.ejecutar(
            conn_boveda,
            "insert into puntos_movimiento (id_persona, tipo, puntos, encuesta_id) "
            "values (%s, 'earn', 100, %s)",
            (ids["Ana"], encuesta_id),
        )
    conn_boveda.rollback()


def test_revertido_a_ok_se_puede_liquidar(conn_boveda, encuesta_respondida, actor):
    """R3.4 — «una participación que pasa de sospechoso a ok tras revisión»."""
    _, encuesta_id, ids = encuesta_respondida
    puntos.liquidar(conn_boveda, encuesta_id)
    assert puntos.saldo(conn_boveda, ids["Beto"]) == 0

    fila = db.una(
        conn_boveda,
        "select id from participacion where encuesta_id = %s and id_persona = %s",
        (encuesta_id, ids["Beto"]),
    )
    calidad.revisar(conn_boveda, fila["id"], "ok", actor("operaciones"),
                    motivo="revisado a mano")
    puntos.liquidar(conn_boveda, encuesta_id)

    assert puntos.saldo(conn_boveda, ids["Beto"]) == puntos.PUNTOS_POR_PARTICIPACION


# ── R3.5 · Premios y canje ──────────────────────────────────────────

@pytest.fixture
def con_saldo(conn_boveda):
    panel = paneles.crear(conn_boveda, "P")
    persona = _persona(conn_boveda, panel["id"], "Ana")
    conn_boveda.commit()
    puntos.registrar(conn_boveda, persona, "ajuste", 100, motivo="carga")
    conn_boveda.commit()
    premio = premios.crear_premio(
        conn_boveda, {"nombre": "Auricular", "costo_puntos": 100, "stock": 3}
    )
    return persona, premio


def test_el_canje_descuenta_y_registra(conn_boveda, con_saldo):
    persona, premio = con_saldo

    canje = premios.canjear(conn_boveda, persona, premio["id"])

    assert canje["estado"] == "solicitado"
    assert canje["saldo_restante"] == 0
    assert premios.listar_premios(conn_boveda)[0]["stock"] == 2


def test_sin_saldo_el_canje_se_rechaza_y_el_saldo_no_cambia(conn_boveda, con_saldo):
    persona, premio = con_saldo
    premios.editar_premio(conn_boveda, premio["id"], {"costo_puntos": 500})

    with pytest.raises(DatosInvalidos, match="Saldo insuficiente"):
        premios.canjear(conn_boveda, persona, premio["id"])

    assert puntos.saldo(conn_boveda, persona) == 100
    assert premios.listar_canjes(conn_boveda) == []


def test_dos_canjes_concurrentes_con_saldo_para_uno_solo(dsn_boveda, con_saldo):
    """R3.5 y DoD — «solo una prospera (sin saldo negativo)».

    Se abren conexiones de verdad y se sincronizan con una barrera: el bug
    que esto busca solo aparece cuando las dos transacciones se solapan, y
    con una sola conexión no se puede reproducir.
    """
    import psycopg
    from psycopg.rows import dict_row

    persona, premio = con_saldo
    resultados, barrera = [], threading.Barrier(2)

    def intentar():
        conn = psycopg.connect(dsn_boveda, row_factory=dict_row)
        try:
            barrera.wait(timeout=10)
            premios.canjear(conn, persona, premio["id"])
            resultados.append("ok")
        except DatosInvalidos:
            resultados.append("rechazado")
        finally:
            conn.close()

    hilos = [threading.Thread(target=intentar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=20)

    assert sorted(resultados) == ["ok", "rechazado"]

    conn = psycopg.connect(dsn_boveda, row_factory=dict_row)
    try:
        assert puntos.saldo(conn, persona) == 0
        assert puntos.saldo(conn, persona) >= 0
        assert len(premios.listar_canjes(conn)) == 1
    finally:
        conn.close()


def test_cancelar_devuelve_los_puntos_como_movimiento_nuevo(conn_boveda, con_saldo):
    persona, premio = con_saldo
    canje = premios.canjear(conn_boveda, persona, premio["id"])

    cancelado = premios.resolver(conn_boveda, canje["id"], "cancelado",
                                 nota="sin stock real")

    assert cancelado["estado"] == "cancelado"
    assert cancelado["saldo_restante"] == 100
    tipos = [m["tipo"] for m in puntos.movimientos(conn_boveda, persona)]
    assert tipos.count("canje") == 1, "el descuento original no se borra"
    assert "ajuste" in tipos, "la devolución es un movimiento nuevo"
    assert premios.listar_premios(conn_boveda)[0]["stock"] == 3


def test_un_canje_entregado_no_se_cancela(conn_boveda, con_saldo):
    persona, premio = con_saldo
    canje = premios.canjear(conn_boveda, persona, premio["id"])
    premios.resolver(conn_boveda, canje["id"], "entregado")

    with pytest.raises(DatosInvalidos, match="no puede pasar"):
        premios.resolver(conn_boveda, canje["id"], "cancelado")


def test_sin_stock_no_se_canjea(conn_boveda, con_saldo):
    persona, premio = con_saldo
    premios.editar_premio(conn_boveda, premio["id"], {"stock": 0})

    with pytest.raises(DatosInvalidos, match="stock"):
        premios.canjear(conn_boveda, persona, premio["id"])


# ── R3.6 · Bono dirigido ────────────────────────────────────────────

def test_el_bono_dirigido_solo_alcanza_a_su_segmento(conn_boveda, encuesta_respondida):
    panel_id, encuesta_id, ids = encuesta_respondida
    puntos.crear_bono(conn_boveda, panel_id, "sexo", "F", 50)
    db.ejecutar(
        conn_boveda,
        "update participacion set respondio = true, calidad_estado = 'ok' "
        " where encuesta_id = %s",
        (encuesta_id,),
    )
    conn_boveda.commit()

    resultado = puntos.liquidar(conn_boveda, encuesta_id)

    por_persona = {l["id_persona"]: l for l in resultado["liquidados"]}
    assert por_persona[ids["Ana"]]["bono"] == 50    # F
    assert por_persona[ids["Cora"]]["bono"] == 50   # F
    assert por_persona[ids["Beto"]]["bono"] == 0    # M


def test_un_bono_vencido_deja_de_aplicarse(conn_boveda, encuesta_respondida):
    panel_id, encuesta_id, ids = encuesta_respondida
    db.ejecutar(
        conn_boveda,
        "insert into bono_puntos (panel_id, dimension, categoria, puntos_extra, "
        "desde, hasta) values (%s, 'sexo', 'F', 50, now() - interval '2 day', "
        "now() - interval '1 day')",
        (panel_id,),
    )
    conn_boveda.commit()

    resultado = puntos.liquidar(conn_boveda, encuesta_id)

    assert all(l["bono"] == 0 for l in resultado["liquidados"])
    assert puntos.saldo(conn_boveda, ids["Ana"]) == puntos.PUNTOS_POR_PARTICIPACION


def test_un_bono_vencido_no_toca_los_puntos_ya_otorgados(conn_boveda,
                                                         encuesta_respondida):
    panel_id, encuesta_id, ids = encuesta_respondida
    bono = puntos.crear_bono(conn_boveda, panel_id, "sexo", "F", 50)
    puntos.liquidar(conn_boveda, encuesta_id)
    ganado = puntos.saldo(conn_boveda, ids["Ana"])

    db.ejecutar(
        conn_boveda, "update bono_puntos set hasta = now() where id = %s",
        (bono["id"],),
    )
    conn_boveda.commit()

    assert puntos.saldo(conn_boveda, ids["Ana"]) == ganado == 150


def test_un_panel_que_ya_calza_no_avisa_de_una_brecha_de_cero(conn_boveda):
    """Encontrado recorriendo la interfaz: con el objetivo ya cumplido, la
    propuesta avisaba «este segmento tiene brecha (0 personas) y no
    alcanza», que se contradice a sí mismo.
    """
    panel = paneles.crear(conn_boveda, "Panel que calza")
    for i in range(4):
        _persona(conn_boveda, panel["id"], f"P{i}", "F" if i < 2 else "M")
    composicion.guardar_objetivo(conn_boveda, panel["id"], [
        {"dimension": "sexo", "categoria": "F", "proporcion_objetivo": 0.5},
        {"dimension": "sexo", "categoria": "M", "proporcion_objetivo": 0.5},
    ])
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    conn_boveda.commit()

    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], "sexo", cantidad=50)

    tipos = {a["tipo"] for a in propuesta["avisos"]}
    assert "sin_brecha" in tipos
    assert "segmento_sin_elegibles" not in tipos
    assert "segmento_con_elegibles_insuficientes" not in tipos
    for aviso in propuesta["avisos"]:
        assert "brecha (0 personas)" not in aviso["mensaje"]
    # Y aun así propone a los cuatro: pedir gente sigue siendo válido.
    assert len(propuesta["propuesta"]) == 4
