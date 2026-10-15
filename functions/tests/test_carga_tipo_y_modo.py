"""boveda/0026 — tipo de datos de origen y modo de relevamiento de la carga.

Dos datos más del estudio, con vocabulario cerrado y una salida «otro» que
obliga a especificar. Viven en la carga, como la fecha y el público objetivo,
y llegan a cada panelista por el vínculo `persona_carga`: el archivo prueba
las dos mitades —que se validan y guardan bien, y que se ven desde cada
persona que vino de esa carga sin copiarse en ella.

Corre contra Postgres de verdad: el vocabulario y la coherencia de «otro» los
hace valer un `check` de la base, y eso no se prueba contra un doble.
"""

import psycopg
import pytest

from panel_api import (
    atributos, cargas, composicion, db, estadisticas, ficha, personas, ruteo,
)
from panel_api.errores import DatosInvalidos

from test_r_org_origen_y_ambitos import _cargar, _fila


@pytest.fixture
def telefonica(conn_boveda):
    return cargas.crear(conn_boveda, "Tracking telefónico",
                        fecha_estudio="2026-09-01",
                        tipo_datos_origen="encuesta",
                        modo_relevamiento="telefonico")


# ── El vocabulario y «otro» ──────────────────────────────────────────

def test_se_guardan_en_la_carga_con_su_etiqueta(conn_boveda, telefonica):
    fila = db.una(conn_boveda,
                  "select tipo_datos_origen, modo_relevamiento from carga "
                  "where id = %s", (telefonica["id"],))
    assert fila == {"tipo_datos_origen": "encuesta",
                    "modo_relevamiento": "telefonico"}
    assert telefonica["tipo_datos_origen_etiqueta"] == "Encuesta"
    assert telefonica["modo_relevamiento_etiqueta"] == "Telefónico"


def test_otro_exige_especificar(conn_boveda):
    with pytest.raises(DatosInvalidos, match="especificá"):
        cargas.crear(conn_boveda, "Base", tipo_datos_origen="otro",
                     modo_relevamiento="web")
    with pytest.raises(DatosInvalidos, match="especificá"):
        cargas.crear(conn_boveda, "Base", tipo_datos_origen="encuesta",
                     modo_relevamiento="otro", modo_relevamiento_otro="   ")


def test_otro_guarda_la_especificacion_y_la_muestra(conn_boveda):
    carga = cargas.crear(conn_boveda, "Base de clientes",
                         tipo_datos_origen="otro",
                         tipo_datos_origen_otro="  Base de clientes  ",
                         modo_relevamiento="otro",
                         modo_relevamiento_otro="Papel autoadministrado")
    assert carga["tipo_datos_origen_otro"] == "Base de clientes"
    assert carga["tipo_datos_origen_etiqueta"] == "Otro: Base de clientes"
    assert carga["modo_relevamiento_etiqueta"] == "Otro: Papel autoadministrado"


def test_sin_otro_la_especificacion_se_descarta(conn_boveda):
    """Una especificación colgada de «web» quedaría invisible y mentiría."""
    carga = cargas.crear(conn_boveda, "Web", tipo_datos_origen="reclutamiento",
                         tipo_datos_origen_otro="sobra",
                         modo_relevamiento="web", modo_relevamiento_otro="sobra")
    assert carga["tipo_datos_origen_otro"] is None
    assert carga["modo_relevamiento_otro"] is None


def test_un_valor_fuera_del_vocabulario_se_rechaza(conn_boveda):
    with pytest.raises(DatosInvalidos, match="cara_a_cara"):
        cargas.crear(conn_boveda, "Base", tipo_datos_origen="encuesta",
                     modo_relevamiento="mail")


def test_la_base_tambien_lo_hace_valer(conn_boveda, telefonica):
    """El `check` de la 0026: el vocabulario y la coherencia de «otro» no
    dependen de que todo el que escriba pase por `cargas.crear`."""
    for sql in (
        "update carga set modo_relevamiento = 'mail' where id = %s",
        "update carga set modo_relevamiento = 'otro' where id = %s",
        "update carga set tipo_datos_origen_otro = 'x' where id = %s",
    ):
        with conn_boveda.transaction():
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn_boveda.transaction():
                    db.ejecutar(conn_boveda, sql, (telefonica["id"],))


def test_las_opciones_coinciden_con_el_check_de_la_base(conn_boveda):
    """Espejo: cada clave que ofrece la pantalla la acepta la base."""
    for campo, vocabulario in cargas.opciones().items():
        for opcion in vocabulario:
            otro = "algo" if opcion["clave"] == cargas.OTRO else None
            with conn_boveda.transaction():
                db.ejecutar(
                    conn_boveda,
                    f"insert into carga (nombre, {campo}, {campo}_otro) "
                    f"values ('espejo', %s, %s)", (opcion["clave"], otro))


def test_las_opciones_son_las_pedidas():
    opciones = cargas.opciones()
    assert [o["etiqueta"] for o in opciones["tipo_datos_origen"]] == [
        "Encuesta", "Reclutamiento", "Otro"]
    assert [o["etiqueta"] for o in opciones["modo_relevamiento"]] == [
        "Cara a cara", "Telefónico", "WhatsApp", "Web", "Otro"]


# ── La ruta: una carga nueva los declara ─────────────────────────────

def test_la_ruta_exige_tipo_y_modo_en_una_carga_nueva(ctx, actor):
    with pytest.raises(DatosInvalidos, match="tipo de datos"):
        ruteo.despachar("POST", "/cargas", {"nombre": "Ómnibus",
                                            "modo_relevamiento": "web"},
                        {}, actor("operaciones"), ctx)
    with pytest.raises(DatosInvalidos, match="modo de relevamiento"):
        ruteo.despachar("POST", "/cargas", {"nombre": "Ómnibus",
                                            "tipo_datos_origen": "encuesta"},
                        {}, actor("operaciones"), ctx)

    status, carga = ruteo.despachar(
        "POST", "/cargas", {"nombre": "Ómnibus", "tipo_datos_origen": "encuesta",
                            "modo_relevamiento": "cara_a_cara"},
        {}, actor("operaciones"), ctx)
    assert status == 201
    assert carga["modo_relevamiento_etiqueta"] == "Cara a cara"


def test_el_listado_de_cargas_trae_las_opciones(ctx, actor, telefonica):
    _, salida = ruteo.despachar("GET", "/cargas", {}, {}, actor("analista"), ctx)
    assert salida["opciones"] == cargas.opciones()
    assert salida["items"][0]["modo_relevamiento"] == "telefonico"


def test_una_carga_anterior_queda_sin_dato(conn_boveda):
    """Las cargas previas a la 0026 no los declararon y no se les inventa."""
    carga = cargas.crear(conn_boveda, "Ómnibus viejo")
    assert carga["tipo_datos_origen"] is None
    assert carga["modo_relevamiento_etiqueta"] is None


def test_editar_corrige_y_no_vacia(ctx, actor, telefonica):
    status, salida = ruteo.despachar(
        "PATCH", f"/cargas/{telefonica['id']}",
        {"modo_relevamiento": "otro", "modo_relevamiento_otro": "Mixto"},
        {}, actor("operaciones"), ctx)
    assert status == 200
    assert salida["modo_relevamiento_etiqueta"] == "Otro: Mixto"
    # El tipo no se mandó: queda como estaba.
    assert salida["tipo_datos_origen"] == "encuesta"

    with pytest.raises(DatosInvalidos, match="Falta indicar"):
        cargas.editar(ctx.boveda, telefonica["id"], {"modo_relevamiento": None})
    with pytest.raises(DatosInvalidos, match="mandá también"):
        cargas.editar(ctx.boveda, telefonica["id"],
                      {"modo_relevamiento_otro": "Otro texto"})


# ── Llegan a cada panelista de la carga ──────────────────────────────

def test_cada_panelista_de_la_carga_los_ve_en_su_ficha(ctx, telefonica):
    _cargar(ctx, telefonica, [_fila(1), _fila(2)])
    ids = [i["id_persona"] for i in personas.listar(ctx.boveda)["items"]]
    assert len(ids) == 2

    for id_persona in ids:
        estudio, = personas.ficha(ctx.boveda, id_persona)["origen"]["estudios"]
        assert (estudio["tipo_datos_origen_etiqueta"],
                estudio["modo_relevamiento_etiqueta"]) == ("Encuesta", "Telefónico")
        # La ficha seudónima de los resultados dice lo mismo.
        assert ficha.seudonima(ctx.boveda, id_persona)["origen"]["estudios"] == [estudio]


def test_corregir_el_modo_se_ve_en_todas_las_fichas_sin_tocar_personas(
        ctx, actor, telefonica):
    _cargar(ctx, telefonica, [_fila(1), _fila(2)])
    ctx.boveda.commit()
    antes = {f["id_persona"]: f["xmin"] for f in db.todas(
        ctx.boveda, "select id_persona::text as id_persona, xmin::text from persona")}

    ruteo.despachar("PATCH", f"/cargas/{telefonica['id']}",
                    {"modo_relevamiento": "whatsapp"}, {}, actor("operaciones"), ctx)

    for id_persona in antes:
        estudio, = personas.ficha(ctx.boveda, id_persona)["origen"]["estudios"]
        assert estudio["modo_relevamiento_etiqueta"] == "WhatsApp"
    despues = {f["id_persona"]: f["xmin"] for f in db.todas(
        ctx.boveda, "select id_persona::text as id_persona, xmin::text from persona")}
    assert antes == despues


def test_no_se_copian_en_la_persona(conn_boveda):
    columnas = {f["column_name"] for f in db.todas(
        conn_boveda,
        "select column_name from information_schema.columns "
        "where table_name = 'persona'")}
    assert not columnas & {"tipo_datos_origen", "modo_relevamiento"}


def test_son_columnas_de_los_resultados(ctx, actor, telefonica):
    web = cargas.crear(ctx.boveda, "Online", fecha_estudio="2026-10-01",
                       tipo_datos_origen="reclutamiento", modo_relevamiento="web")
    otra_telefonica = cargas.crear(ctx.boveda, "Tracking octubre",
                                   fecha_estudio="2026-10-02",
                                   tipo_datos_origen="encuesta",
                                   modo_relevamiento="telefonico")
    for carga in (telefonica, web, otra_telefonica):
        _cargar(ctx, carga, [_fila(1)])
    id_persona = personas.listar(ctx.boveda)["items"][0]["id_persona"]

    _, columnas = ruteo.despachar("GET", "/atributos/columnas", {}, {},
                                  actor("analista"), ctx)
    claves = {c["clave"] for c in columnas["items"]}
    assert {cargas.COLUMNA_TIPO_DATOS_ORIGEN,
            cargas.COLUMNA_MODO_RELEVAMIENTO} <= claves

    _, valores = ruteo.despachar(
        "POST", "/resultados/atributos", {"ids_persona": [id_persona]},
        {}, actor("analista"), ctx)
    celdas = valores["items"][id_persona]
    # Tres cargas: el estudio las lista todas; tipo y modo, sin repetir.
    assert celdas[cargas.COLUMNA_ESTUDIO_DE_ORIGEN]["valor"].count(" | ") == 2
    assert celdas[cargas.COLUMNA_TIPO_DATOS_ORIGEN]["valor"] == "Encuesta | Reclutamiento"
    assert celdas[cargas.COLUMNA_MODO_RELEVAMIENTO]["valor"] == "Telefónico | Web"


def test_sin_dato_en_la_carga_no_hay_columna(ctx, actor):
    vieja = cargas.crear(ctx.boveda, "Ómnibus viejo")
    _cargar(ctx, vieja, [_fila(1)])
    id_persona = personas.listar(ctx.boveda)["items"][0]["id_persona"]

    celdas = cargas.origen_de_varias(ctx.boveda, [id_persona])[id_persona]

    assert cargas.COLUMNA_ESTUDIO_DE_ORIGEN in celdas
    assert cargas.COLUMNA_MODO_RELEVAMIENTO not in celdas


def test_las_claves_de_las_columnas_estan_reservadas():
    for clave, _ in cargas.COLUMNAS_DE_PROCEDENCIA:
        assert clave in atributos.CLAVES_RESERVADAS


def test_estadisticas_y_composicion_los_muestran(ctx, telefonica):
    _cargar(ctx, telefonica, [_fila(1)])

    item = estadisticas.estudios_de_origen(ctx.boveda)["items"][0]
    assert item["modo_relevamiento_etiqueta"] == "Telefónico"

    salida = composicion.composicion_de_ambito(
        ctx.boveda, composicion.CARGA, telefonica["id"])
    assert salida["ambito"]["tipo_datos_origen_etiqueta"] == "Encuesta"
