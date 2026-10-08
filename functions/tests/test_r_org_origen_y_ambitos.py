"""R-ORG — Ámbitos de composición y metadatos del estudio en la carga.

Las dos mitades de la spec se apoyan en una pieza que no existía: **de qué
carga vino cada persona** (`persona_carga`). El archivo sigue el Definition of
Done en el mismo orden: el vínculo, la composición por ámbito y los datos del
estudio.

Corre contra Postgres de verdad: el vínculo es una clave primaria compuesta,
un `on conflict do nothing` que no pisa y una cascada desde `persona`, y nada
de eso se prueba contra un doble.
"""

import pytest

from panel_api import (
    atributos, bajas, cargas, composicion, db, estadisticas, ficha, paneles,
    personas, revision, ruteo, sav,
)
from panel_api.errores import Conflicto, DatosInvalidos, SinPermiso

from conftest import consentimientos

VERSION = "consentimiento-omnibus-2026-08"   # publicada por el conftest

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Whisky"}, "orden": 1},
    {"codigo": "SEXO", "texto": "Sexo", "tipo": "cerrada",
     "opciones": {"1": "M", "2": "F"}, "orden": 2},
]
DEMOGRAFICAS = {"NOM": "nombre", "DOC": "documento", "SEXO": "sexo"}
EVIDENCIA = {
    "uso_semantico": {"variable": "CONS", "valor_afirmativo": "1",
                      "version_texto": VERSION},
}


def _fila(i, documento=None, sexo="2"):
    return {"ID": f"o-{i}", "NOM": f"Persona {i}",
            "DOC": documento or f"20-{i}", "SEXO": sexo, "CONS": "1", "P1": "1"}


def _cargar(ctx, carga, filas, origen="omnibus"):
    """Crear los individuos (con el vínculo) e ingestar, como la ruta."""
    creacion = cargas.crear_individuos(
        ctx.boveda, filas, sav.mapeo_por_campo(DEMOGRAFICAS),
        origen=origen, columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        opciones_por_variable={p["codigo"]: p["opciones"] for p in PREGUNTAS},
        carga_id=carga["id"])
    resultado = cargas.ingestar(
        ctx.boveda, ctx.semantica, carga["id"], PREGUNTAS, filas,
        columna_id="ID", origen=origen, proveedor=ctx.embeddings,
        demograficas=DEMOGRAFICAS)
    return creacion, resultado


def _vinculos(conn, carga_id=None):
    filas = db.todas(
        conn,
        "select id_persona::text as id_persona, carga_id, origen "
        "from persona_carga where %s::bigint is null or carga_id = %s "
        "order by carga_id, id_persona",
        (carga_id, carga_id))
    return {(f["id_persona"], f["carga_id"]): f["origen"] for f in filas}


def _panelista(conn, documento, nombre="Panelista propio", sexo=None):
    cuerpo = {"persona": {"documento": documento, "nombre": nombre},
              "consentimientos": consentimientos("contacto_participacion",
                                                 "uso_semantico")}
    if sexo:
        cuerpo["persona"]["sexo"] = sexo
    return personas.alta(conn, cuerpo)["id_persona"]


@pytest.fixture
def omnibus(conn_boveda):
    return cargas.crear(conn_boveda, "Ómnibus agosto 2026",
                        fecha_estudio="2026-08-15",
                        publico_objetivo="Población adulta de Montevideo")


# ════════════════════════════════════════════════════════════════════
#  R-ORG.1 · El vínculo persona ↔ carga
# ════════════════════════════════════════════════════════════════════

def test_una_carga_que_crea_personas_registra_el_vinculo_como_creada(
        ctx, omnibus):
    creacion, _ = _cargar(ctx, omnibus, [_fila(1), _fila(2)])

    vinculos = _vinculos(ctx.boveda, omnibus["id"])
    assert len(vinculos) == 2
    assert set(vinculos.values()) == {"creada"}
    assert creacion["vinculos_con_la_carga"] == {"creada": 2, "reutilizada": 0}


def test_una_carga_que_reutiliza_personas_lo_registra_como_reutilizada(
        ctx, omnibus):
    """El dedup la encontró por documento: ya existía, no nació acá."""
    existente = _panelista(ctx.boveda, "20-1")

    _cargar(ctx, omnibus, [_fila(1), _fila(2)])

    vinculos = _vinculos(ctx.boveda, omnibus["id"])
    assert vinculos[(str(existente), omnibus["id"])] == "reutilizada"
    assert sorted(vinculos.values()) == ["creada", "reutilizada"]


def test_el_lote_no_pisa_a_quien_la_carga_creo(ctx, omnibus):
    """La ruta registra «creada» antes de encolar y cada lote registra
    «reutilizada» sobre todo lo que resolvió. El orden no puede cambiar el
    resultado: el vínculo se fija la primera vez."""
    _cargar(ctx, omnibus, [_fila(1)])
    # Un reintento del lote, o volver a ingestar el mismo archivo.
    cargas.ingestar(ctx.boveda, ctx.semantica, omnibus["id"], PREGUNTAS,
                    [_fila(1)], columna_id="ID", origen="omnibus",
                    proveedor=ctx.embeddings, demograficas=DEMOGRAFICAS)

    assert list(_vinculos(ctx.boveda, omnibus["id"]).values()) == ["creada"]


def test_una_persona_en_dos_cargas_tiene_dos_vinculos(ctx, omnibus):
    otra = cargas.crear(ctx.boveda, "Ómnibus septiembre 2026",
                        fecha_estudio="2026-09-15")
    _cargar(ctx, omnibus, [_fila(1)])
    _cargar(ctx, otra, [_fila(1)])

    id_persona = personas.listar(ctx.boveda)["items"][0]["id_persona"]
    vinculos = _vinculos(ctx.boveda)
    assert vinculos == {(id_persona, omnibus["id"]): "creada",
                        (id_persona, otra["id"]): "reutilizada"}


def test_una_baja_elimina_los_vinculos(ctx, omnibus):
    _cargar(ctx, omnibus, [_fila(1), _fila(2)])
    id_persona = next(iter(_vinculos(ctx.boveda)))[0]

    bajas.retirar(ctx.boveda, id_persona, bajas.TODAS, actor="uid-dpo",
                  conn_semantica=ctx.semantica)

    assert all(clave[0] != id_persona for clave in _vinculos(ctx.boveda))
    assert len(_vinculos(ctx.boveda)) == 1


def test_la_ruta_de_carga_deja_el_vinculo_de_punta_a_punta(ctx, actor):
    """El camino de la pantalla: crear la carga con los datos del estudio,
    ingestar en modo «crear individuos» y procesar los lotes encolados."""
    from panel_api import diferida

    existente = _panelista(ctx.boveda, "20-2")
    _, carga = ruteo.despachar(
        "POST", "/cargas", {"nombre": "Ómnibus", "fecha_estudio": "2026-08-15",
                            "publico_objetivo": "Adultos"},
        {}, actor("operaciones"), ctx)
    _, salida = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta",
        {"filas": [_fila(1), _fila(2)], "columna_id": "ID", "origen": "omnibus",
         "tipo_identificador": "alias", "modo": "crear_individuos",
         "preguntas": PREGUNTAS, "demograficas": DEMOGRAFICAS,
         "evidencia_consentimiento": EVIDENCIA},
        {}, actor("operaciones"), ctx)
    for indice in range(len(ctx.encolador.encoladas)):
        diferida.procesar_lote(ctx.boveda, ctx.semantica, salida["trabajo_id"],
                               indice, proveedor=ctx.embeddings)

    vinculos = _vinculos(ctx.boveda, carga["id"])
    assert vinculos[(str(existente), carga["id"])] == "reutilizada"
    assert sorted(vinculos.values()) == ["creada", "reutilizada"]


def test_quien_paso_por_revision_queda_vinculado_al_resolverla(
        conn_boveda, omnibus):
    """Si no, sería el único de la carga sin origen."""
    fila = db.una(
        conn_boveda,
        """insert into alta_en_revision (datos, candidatos, motivo)
           values (%s, '[]', 'nombre_fecha_nacimiento') returning id""",
        ('{"persona": {"nombre": "En revisión", "documento": "99-9"}, '
         f'"carga_id": {omnibus["id"]}}}',))

    salida = revision.resolver(conn_boveda, fila["id"], "crear")

    assert _vinculos(conn_boveda, omnibus["id"]) == {
        (salida["id_persona"], omnibus["id"]): "creada"}


# ════════════════════════════════════════════════════════════════════
#  R-ORG.2/3 · Composición por ámbito
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def bóveda_mixta(ctx, omnibus):
    """Un panel con dos miembros y una carga con tres personas sin panel."""
    panel = paneles.crear(ctx.boveda, "Panel Nacional")
    for documento, sexo in (("1-1", "F"), ("1-2", "M")):
        paneles.agregar_miembro(
            ctx.boveda, panel["id"], _panelista(ctx.boveda, documento, sexo=sexo))
    _cargar(ctx, omnibus, [_fila(1, sexo="2"), _fila(2, sexo="2"),
                           _fila(3, sexo="1")])
    ctx.boveda.commit()
    return panel


def _sexo(salida):
    dimension = next(d for d in salida["dimensiones"] if d["dimension"] == "sexo")
    return {c["categoria"]: c["observados"] for c in dimension["categorias"]}, dimension


def test_todos_incluye_a_quienes_no_estan_en_ningun_panel(ctx, bóveda_mixta):
    salida = composicion.composicion_de_ambito(ctx.boveda, "todos")

    assert salida["miembros"] == 5
    assert salida["sin_panel"] == 3
    assert salida["ambito"]["tipo"] == "todos"
    assert _sexo(salida)[0] == {"F": 3, "M": 2}


def test_se_puede_calcular_la_composicion_de_una_carga(ctx, bóveda_mixta, omnibus):
    salida = composicion.composicion_de_ambito(ctx.boveda, "carga", omnibus["id"])

    assert salida["miembros"] == 3
    assert _sexo(salida)[0] == {"F": 2, "M": 1}
    assert salida["ambito"] == {
        "tipo": "carga", "id": omnibus["id"], "nombre": "Ómnibus agosto 2026",
        "fecha_estudio": "2026-08-15",
        "publico_objetivo": "Población adulta de Montevideo",
        "creado_en": omnibus["creado_en"]}
    # §7 — la pantalla dice que mira el presente, no un registro histórico.
    assert composicion.AVISO_CARGA in salida["avisos"]


def test_un_ambito_sin_objetivo_muestra_la_brecha_como_no_disponible(
        ctx, bóveda_mixta, omnibus):
    """No como cero ni en blanco: con el motivo."""
    for ambito, referencia in (("todos", None), ("carga", omnibus["id"])):
        salida = composicion.composicion_de_ambito(ctx.boveda, ambito, referencia)
        _, dimension = _sexo(salida)
        assert salida["objetivo_cargado"] is False
        assert dimension["brecha_disponible"] is False
        assert dimension["motivo_sin_brecha"]
        assert dimension["disimilitud"] is None
        assert all(c["brecha"] is None for c in dimension["categorias"])


def test_una_carga_no_admite_objetivo_y_lo_dice(ctx, bóveda_mixta, omnibus):
    salida = composicion.composicion_de_ambito(ctx.boveda, "carga", omnibus["id"])
    _, dimension = _sexo(salida)
    assert salida["objetivo_admitido"] is False
    assert dimension["motivo_sin_brecha"] == composicion.MOTIVO_SIN_OBJETIVO_CARGA


def test_todos_admite_su_propio_objetivo_sin_tocar_el_del_panel(
        ctx, bóveda_mixta):
    composicion.guardar_objetivo(
        ctx.boveda, bóveda_mixta["id"],
        [{"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
         {"dimension": "sexo", "categoria": "M", "proporcion": 0.5}])
    composicion.guardar_objetivo_de_todos(
        ctx.boveda,
        [{"dimension": "sexo", "categoria": "F", "proporcion": 0.52},
         {"dimension": "sexo", "categoria": "M", "proporcion": 0.48}])

    todos = composicion.composicion_de_ambito(ctx.boveda, "todos")
    _, dimension = _sexo(todos)
    assert todos["objetivo_cargado"] is True
    assert {c["categoria"]: c["proporcion_objetivo"]
            for c in dimension["categorias"]} == {"F": 0.52, "M": 0.48}
    # §7 — que no se confunda con el del panel nacional.
    assert composicion.AVISO_TODOS in todos["avisos"]
    # Y el del panel sigue siendo el suyo.
    assert composicion.obtener_objetivo(
        ctx.boveda, bóveda_mixta["id"])["dimensiones"] == {
            "sexo": {"F": 0.5, "M": 0.5}}

    composicion.borrar_objetivo_de_todos(ctx.boveda, "sexo")
    assert composicion.obtener_objetivo_de_todos(ctx.boveda)["dimensiones"] == {}
    assert composicion.obtener_objetivo(
        ctx.boveda, bóveda_mixta["id"])["dimensiones"] != {}


def test_el_objetivo_de_todos_valida_igual_que_el_de_un_panel(ctx):
    with pytest.raises(DatosInvalidos, match="suman"):
        composicion.guardar_objetivo_de_todos(
            ctx.boveda, [{"dimension": "sexo", "categoria": "F", "proporcion": 0.8}])


def test_la_composicion_por_panel_sigue_dando_lo_mismo(ctx, bóveda_mixta):
    """No regresión: el ámbito «panel» es la composición de siempre, con
    solo los miembros, y los cargados sin panel no la mueven."""
    directa = composicion.composicion(ctx.boveda, bóveda_mixta["id"],
                                      cruce_de=["sexo", "tramo_etario"])
    por_ambito = composicion.composicion_de_ambito(
        ctx.boveda, "panel", bóveda_mixta["id"], cruce_de=["sexo", "tramo_etario"])

    assert directa == por_ambito
    assert directa["miembros"] == 2
    assert _sexo(directa)[0] == {"F": 1, "M": 1}
    assert sum(c["observados"] for c in directa["cruce"]["celdas"]) == 2


def test_la_composicion_a_fecha_queda_para_los_paneles(ctx, bóveda_mixta):
    with pytest.raises(DatosInvalidos, match="solo por panel"):
        composicion.composicion_de_ambito(ctx.boveda, "todos",
                                          momento="2026-01-01")


def test_el_cruce_tambien_va_por_ambito(ctx, bóveda_mixta, omnibus):
    salida = composicion.composicion_de_ambito(
        ctx.boveda, "carga", omnibus["id"], cruce_de=["sexo", "tramo_etario"])
    assert salida["cruce"]["miembros"] == 3
    assert sum(c["observados"] for c in salida["cruce"]["celdas"]) == 3


def test_las_rutas_de_ambito(ctx, actor, bóveda_mixta, omnibus):
    _, todos = ruteo.despachar("GET", "/composicion", {}, {"ambito": "todos"},
                               actor("analista"), ctx)
    _, carga = ruteo.despachar(
        "GET", "/composicion", {},
        {"ambito": "carga", "carga_id": str(omnibus["id"])}, actor("analista"), ctx)
    _, panel = ruteo.despachar(
        "GET", "/composicion", {},
        {"ambito": "panel", "panel_id": str(bóveda_mixta["id"])},
        actor("analista"), ctx)
    assert (todos["miembros"], carga["miembros"], panel["miembros"]) == (5, 3, 2)

    status, _ = ruteo.despachar(
        "PUT", "/composicion/todos/objetivo",
        {"objetivos": [{"dimension": "sexo", "categoria": "F", "proporcion": 1}]},
        {}, actor("operaciones"), ctx)
    assert status == 200
    with pytest.raises(SinPermiso):
        # Cargar un universo es gestionar paneles: un analista no puede.
        ruteo.despachar(
            "PUT", "/composicion/todos/objetivo",
            {"objetivos": [{"dimension": "sexo", "categoria": "F", "proporcion": 1}]},
            {}, actor("analista"), ctx)


# ════════════════════════════════════════════════════════════════════
#  R-ORG.4/5/6 · Los datos del estudio, en la carga
# ════════════════════════════════════════════════════════════════════

def test_nombre_fecha_y_publico_se_guardan_en_la_carga(conn_boveda, omnibus):
    fila = db.una(conn_boveda,
                  "select nombre, fecha_estudio::text as fecha, publico_objetivo "
                  "from carga where id = %s", (omnibus["id"],))
    assert fila == {"nombre": "Ómnibus agosto 2026", "fecha": "2026-08-15",
                    "publico_objetivo": "Población adulta de Montevideo"}
    # Y no copiados en la persona: `persona` no tiene dónde guardarlos.
    columnas = {f["column_name"] for f in db.todas(
        conn_boveda,
        "select column_name from information_schema.columns "
        "where table_name = 'persona'")}
    assert not columnas & {"fecha_estudio", "publico_objetivo", "carga_id"}


def test_una_fecha_que_no_es_fecha_se_rechaza(conn_boveda):
    with pytest.raises(DatosInvalidos, match="AAAA-MM-DD"):
        cargas.crear(conn_boveda, "Ómnibus", fecha_estudio="15 de agosto")


def test_el_publico_objetivo_es_texto_libre(conn_boveda):
    """R-ORG.6 — documenta la procedencia; no es un vocabulario."""
    carga = cargas.crear(conn_boveda, "Ómnibus",
                         publico_objetivo="  adultos montevideanos (18+)  ")
    assert carga["publico_objetivo"] == "adultos montevideanos (18+)"


def test_la_ficha_muestra_los_estudios_de_los_que_proviene(ctx, omnibus):
    _cargar(ctx, omnibus, [_fila(1)])
    id_persona = personas.listar(ctx.boveda)["items"][0]["id_persona"]

    origen = personas.ficha(ctx.boveda, id_persona)["origen"]

    assert origen["aviso"] is None
    assert origen["estudios"] == [{
        "carga_id": omnibus["id"], "nombre": "Ómnibus agosto 2026",
        "fecha_estudio": "2026-08-15",
        "publico_objetivo": "Población adulta de Montevideo",
        "origen": "creada",
        "vinculado_en": origen["estudios"][0]["vinculado_en"],
    }]
    # Y la ficha seudónima de los resultados también lo trae.
    assert ficha.seudonima(ctx.boveda, id_persona)["origen"] == origen


def test_una_persona_de_tres_cargas_muestra_las_tres(ctx, omnibus):
    segunda = cargas.crear(ctx.boveda, "Ómnibus septiembre",
                           fecha_estudio="2026-09-15")
    tercera = cargas.crear(ctx.boveda, "Estudio de bebidas",
                           fecha_estudio="2026-10-01")
    for carga in (omnibus, segunda, tercera):
        _cargar(ctx, carga, [_fila(1)])
    id_persona = personas.listar(ctx.boveda)["items"][0]["id_persona"]

    estudios = personas.ficha(ctx.boveda, id_persona)["origen"]["estudios"]

    assert [e["nombre"] for e in estudios] == [
        "Ómnibus agosto 2026", "Ómnibus septiembre", "Estudio de bebidas"]
    assert [e["origen"] for e in estudios] == ["creada", "reutilizada", "reutilizada"]


def test_corregir_la_fecha_se_ve_en_todas_las_fichas_sin_tocar_personas(
        ctx, actor, omnibus):
    _cargar(ctx, omnibus, [_fila(1), _fila(2)])
    ctx.boveda.commit()
    ids = [i["id_persona"] for i in personas.listar(ctx.boveda)["items"]]
    # `xmin` cambia con cada `update` de la fila: si sigue igual, nadie la
    # tocó.
    antes = {f["id_persona"]: f["xmin"] for f in db.todas(
        ctx.boveda, "select id_persona::text as id_persona, xmin::text "
                    "from persona")}

    status, salida = ruteo.despachar(
        "PATCH", f"/cargas/{omnibus['id']}", {"fecha_estudio": "2026-08-20"},
        {}, actor("operaciones"), ctx)

    assert status == 200 and salida["fecha_estudio"] == "2026-08-20"
    for id_persona in ids:
        estudios = personas.ficha(ctx.boveda, id_persona)["origen"]["estudios"]
        assert estudios[0]["fecha_estudio"] == "2026-08-20"
    despues = {f["id_persona"]: f["xmin"] for f in db.todas(
        ctx.boveda, "select id_persona::text as id_persona, xmin::text "
                    "from persona")}
    assert antes == despues


def test_editar_sin_cambios_se_rechaza(conn_boveda, omnibus):
    with pytest.raises(DatosInvalidos, match="nada que cambiar"):
        cargas.editar(conn_boveda, omnibus["id"], {})


def test_el_listado_se_puede_filtrar_por_carga(ctx, actor, omnibus):
    propio = _panelista(ctx.boveda, "1-1")
    _cargar(ctx, omnibus, [_fila(1), _fila(2)])

    _, salida = ruteo.despachar(
        "GET", "/panelistas", {}, {"carga_id": str(omnibus["id"])},
        actor("analista"), ctx)

    assert salida["total"] == 2
    assert propio not in {i["id_persona"] for i in salida["items"]}
    assert personas.listar(ctx.boveda)["total"] == 3


def test_sin_vinculo_la_ficha_lo_explica_en_vez_de_decir_sin_origen(conn_boveda):
    """§7 — las personas anteriores a R-ORG no tienen vínculo y no se les
    inventa uno."""
    id_persona = _panelista(conn_boveda, "1-1")
    origen = personas.ficha(conn_boveda, id_persona)["origen"]
    assert origen["estudios"] == []
    assert origen["aviso"] == cargas.SIN_VINCULO_REGISTRADO


def test_el_estudio_de_origen_es_una_columna_de_los_resultados(
        ctx, actor, omnibus):
    """R7.4 — se ofrece en el selector y se resuelve en la misma llamada que
    los atributos."""
    _cargar(ctx, omnibus, [_fila(1)])
    sin_carga = _panelista(ctx.boveda, "1-1")
    id_persona = next(i["id_persona"] for i in personas.listar(ctx.boveda)["items"]
                      if i["id_persona"] != sin_carga)

    _, columnas = ruteo.despachar("GET", "/atributos/columnas", {}, {},
                                  actor("analista"), ctx)
    assert cargas.COLUMNA_ESTUDIO_DE_ORIGEN in {c["clave"] for c in columnas["items"]}

    _, valores = ruteo.despachar(
        "POST", "/resultados/atributos", {"ids_persona": [id_persona, sin_carga]},
        {}, actor("analista"), ctx)
    celda = valores["items"][id_persona][cargas.COLUMNA_ESTUDIO_DE_ORIGEN]
    assert celda["valor"] == "Ómnibus agosto 2026 · 2026-08-15"
    # Sin vínculo no hay entrada: la pantalla muestra «sin dato».
    assert cargas.COLUMNA_ESTUDIO_DE_ORIGEN not in valores["items"][sin_carga]


def test_la_clave_de_la_columna_no_la_puede_tomar_un_atributo(conn_boveda):
    assert cargas.COLUMNA_ESTUDIO_DE_ORIGEN in atributos.CLAVES_RESERVADAS
    with pytest.raises(Conflicto, match="reservada"):
        atributos.crear(conn_boveda, {"clave": "estudio_de_origen",
                                      "etiqueta": "Estudio", "tipo": "categorico"})


def test_estadisticas_lista_las_cargas_con_creadas_y_reutilizadas(ctx, omnibus):
    _panelista(ctx.boveda, "20-1")
    _cargar(ctx, omnibus, [_fila(1), _fila(2), _fila(3)])

    salida = estadisticas.estudios_de_origen(ctx.boveda)

    assert salida["items"][0]["nombre"] == "Ómnibus agosto 2026"
    assert salida["items"][0]["fecha_estudio"] == "2026-08-15"
    assert (salida["items"][0]["personas_creadas"],
            salida["items"][0]["personas_reutilizadas"]) == (2, 1)
    assert "estudios_de_origen" in estadisticas.todo(ctx.boveda, ctx.semantica)


def test_el_listado_de_cargas_trae_los_datos_del_estudio(ctx, actor, omnibus):
    """Es lo que arma los selectores «por nombre y fecha, no por id»."""
    _, salida = ruteo.despachar("GET", "/cargas", {}, {}, actor("analista"), ctx)
    item = salida["items"][0]
    assert (item["nombre"], item["fecha_estudio"]) == (
        "Ómnibus agosto 2026", "2026-08-15")
    assert item["personas"] == 0
