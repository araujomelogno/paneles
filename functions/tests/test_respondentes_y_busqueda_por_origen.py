"""La lista de respondentes de una encuesta, y buscar panelistas por el id
que les dio una plataforma de campo.

* Cada fila de la participación dice si la persona es **panelista** (tiene
  una membresía activa en algún panel) o **independiente** (llegó solo por
  archivos), además de si fue convocada a esta encuesta. Desde la fila se
  abre su ficha.
* Panelistas busca también por `alias_origen.id_en_origen`, y cada fila
  dice qué id coincidió.

Corre contra Postgres de verdad. Lo que se ve en la pantalla lo prueba
`test_pantallas_respondentes_y_busqueda.py`.
"""

from panel_api import cargas, db, encuestas, paneles, personas, ruteo

from test_r_org_origen_y_ambitos import _cargar, _fila, _panelista


def test_cada_respondente_dice_si_es_panelista_o_independiente(ctx):
    panel = paneles.crear(ctx.boveda, "Panel general")
    ya_era = _panelista(ctx.boveda, "20-1", "Ya era panelista")
    paneles.agregar_miembro(ctx.boveda, panel["id"], ya_era)
    dado_de_baja = _panelista(ctx.boveda, "20-2", "Se fue del panel")
    paneles.agregar_miembro(ctx.boveda, panel["id"], dado_de_baja)
    paneles.dar_de_baja_miembro(ctx.boveda, panel["id"], dado_de_baja)
    carga = cargas.crear(ctx.boveda, "AVANTI")
    _cargar(ctx, carga, [_fila(1), _fila(2), _fila(3)])

    filas = {p["id_persona"]: p for p in encuestas.listar_participacion(
        ctx.boveda, carga["encuesta_id"])}
    nueva = db.una(ctx.boveda, "select id_persona::text as id from persona "
                               "where documento = '20-3'")["id"]
    assert filas[str(ya_era)]["es_panelista"] is True
    # Una membresía dada de baja no hace panelista a nadie.
    assert filas[str(dado_de_baja)]["es_panelista"] is False
    assert filas[nueva]["es_panelista"] is False
    # Y la convocatoria a esta encuesta es otra pregunta: nadie fue convocado.
    assert not any(p["convocado"] for p in filas.values())


def test_se_busca_por_el_id_de_la_plataforma(conn_boveda):
    ana = _panelista(conn_boveda, "40-1", "Ana")
    beto = _panelista(conn_boveda, "40-2", "Beto")
    db.ejecutar(conn_boveda, """insert into alias_origen (id_persona, origen, id_en_origen)
                                values (%s, 'dooblo', 'AV-0911'), (%s, 'dooblo', 'AV-0420')""",
                (ana, beto))

    salida = personas.listar(conn_boveda, busqueda="av-0911")
    assert [i["id_persona"] for i in salida["items"]] == [str(ana)]
    assert salida["items"][0]["ids_en_origen"] == ["dooblo: AV-0911"]
    # Parcial, como el resto de la búsqueda.
    assert {i["id_persona"] for i in personas.listar(conn_boveda, busqueda="AV-0")["items"]} \
        == {str(ana), str(beto)}
    # Una búsqueda por nombre no trae ids que no coincidieron.
    assert personas.listar(conn_boveda, busqueda="Beto")["items"][0]["ids_en_origen"] == []


def test_la_busqueda_por_id_se_combina_con_los_otros_filtros(conn_boveda):
    """El parámetro del id va en el `select`, antes del `where`: con el
    filtro por carga los dos tienen que quedar en su lugar."""
    ana = _panelista(conn_boveda, "50-1", "Ana")
    db.ejecutar(conn_boveda, "insert into alias_origen (id_persona, origen, id_en_origen) "
                             "values (%s, 'campo', 'X-77')", (ana,))
    carga = cargas.crear(conn_boveda, "Ómnibus")
    cargas.registrar_vinculos(conn_boveda, carga["id"], [str(ana)], cargas.REUTILIZADA)
    salida = personas.listar(conn_boveda, busqueda="X-77", carga_id=carga["id"])
    assert [(i["id_persona"], i["origen_en_carga"], i["ids_en_origen"])
            for i in salida["items"]] == [(str(ana), "reutilizada", ["campo: X-77"])]
    assert personas.listar(conn_boveda, busqueda="X-77", sin_panel=True)["total"] == 1


def test_la_busqueda_por_id_va_por_la_ruta_de_panelistas(ctx, actor):
    ana = _panelista(ctx.boveda, "60-1", "Ana")
    db.ejecutar(ctx.boveda, "insert into alias_origen (id_persona, origen, id_en_origen) "
                            "values (%s, 'campo', 'Z-123')", (ana,))
    _, salida = ruteo.despachar("GET", "/panelistas", {}, {"q": "z-123"},
                                actor("analista"), ctx)
    assert [i["id_persona"] for i in salida["items"]] == [str(ana)]
