"""Verificación del esquema desplegado, y traducción de la falla.

Las migraciones se aplican a mano contra cada instancia de Cloud SQL. Una que
no se aplicó no rompe el arranque: rompe la primera pantalla que la necesita, y
lo hace con un error de Postgres que el usuario ve como «Error interno del
servidor». Este módulo es el que convierte eso en algo accionable, y estas
pruebas cubren las dos mitades: detectar lo que falta, y explicarlo.
"""

import pathlib
import re

import pytest

from panel_api import db, esquema

RAIZ = pathlib.Path(__file__).resolve().parents[2]


# ── La lista declarada no se puede desactualizar ─────────────────────

def _objetos_del_ddl(sql):
    """Tablas, vistas y columnas agregadas que declara un archivo de migración."""
    objetos = set()
    for nombre in re.findall(
        r"create\s+(?:or\s+replace\s+)?(?:table|view)\s+(?:if\s+not\s+exists\s+)?"
        r"([a-z_][a-z0-9_]*)", sql, re.I,
    ):
        objetos.add(nombre.lower())
    for tabla, columna in re.findall(
        r"alter\s+table\s+([a-z_][a-z0-9_]*)\s+add\s+column\s+"
        r"(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)", sql, re.I,
    ):
        objetos.add(f"{tabla.lower()}.{columna.lower()}")
    return objetos


@pytest.mark.parametrize("store,migraciones", [
    ("boveda", esquema.MIGRACIONES_BOVEDA),
    ("semantica", esquema.MIGRACIONES_SEMANTICA),
])
def test_lo_declarado_coincide_con_lo_que_crean_las_migraciones(store, migraciones):
    """La lista de `esquema.py` está escrita a mano porque la carpeta `db/` no
    se despliega con la función. Esta prueba es lo que impide que se
    desactualice: si alguien agrega una migración y no la declara, falla acá y
    no en producción."""
    for archivo, declarados in migraciones:
        ruta = RAIZ / "db" / store / archivo
        assert ruta.exists(), f"la migración declarada {archivo} no existe"
        reales = _objetos_del_ddl(ruta.read_text(encoding="utf-8"))
        faltan = reales - set(declarados)
        assert not faltan, (
            f"{store}/{archivo} crea {sorted(faltan)} y esquema.py no lo declara"
        )
        sobran = set(declarados) - reales
        assert not sobran, (
            f"esquema.py declara {sorted(sobran)} en {store}/{archivo} y el DDL "
            f"no lo crea"
        )


@pytest.mark.parametrize("store,migraciones", [
    ("boveda", esquema.MIGRACIONES_BOVEDA),
    ("semantica", esquema.MIGRACIONES_SEMANTICA),
])
def test_estan_declaradas_todas_las_migraciones_del_repo(store, migraciones):
    """Y al revés: una migración nueva en `db/` tiene que aparecer en la
    lista, o el diagnóstico la daría por aplicada sin haberla mirado."""
    en_disco = sorted(p.name for p in (RAIZ / "db" / store).glob("*.sql"))
    declaradas = [archivo for archivo, _ in migraciones]
    assert declaradas == en_disco


# ── Detección contra la base real ────────────────────────────────────

def test_sobre_una_base_al_dia_no_falta_nada(conn_boveda, conn_semantica):
    estado = esquema.revisar_stores(conn_boveda, conn_semantica)
    assert estado["completo"] is True, estado["como_aplicar"]
    assert estado["como_aplicar"] == []
    assert all(m["aplicada"] for m in estado["boveda"]["migraciones"])
    assert all(m["aplicada"] for m in estado["semantica"]["migraciones"])


def test_detecta_una_vista_que_falta(conn_semantica):
    """El caso real: la migración 0002 del store semántico no se aplicó y las
    consultas semánticas se caen."""
    with conn_semantica.cursor() as cur:
        cur.execute("drop view if exists v_respuesta_estudio")
    try:
        estado = esquema.verificar(conn_semantica, esquema.MIGRACIONES_SEMANTICA)
        assert estado["completo"] is False
        falta = estado["faltantes"][0]
        assert falta["objeto"] == "v_respuesta_estudio"
        assert falta["migracion"] == "0002_vista_procedencia.sql"
        assert falta["tipo"] == "relación"
        assert "consultas semánticas" in falta["para_que"]
        # El resumen ubica la falla en el archivo, que es la unidad de acción.
        por_archivo = {m["migracion"]: m["aplicada"] for m in estado["migraciones"]}
        assert por_archivo["0001_init.sql"] is True
        assert por_archivo["0002_vista_procedencia.sql"] is False
    finally:
        conn_semantica.rollback()


def test_detecta_una_columna_que_falta(conn_semantica):
    with conn_semantica.cursor() as cur:
        cur.execute("alter table respuesta drop column hash_texto")
    try:
        estado = esquema.verificar(conn_semantica, esquema.MIGRACIONES_SEMANTICA)
        falta = next(f for f in estado["faltantes"] if "hash_texto" in f["objeto"])
        assert falta["tipo"] == "columna"
        assert falta["migracion"] == "0003_hash_texto.sql"
    finally:
        conn_semantica.rollback()


def test_las_instrucciones_nombran_solo_las_migraciones_que_faltan(
    conn_boveda, conn_semantica
):
    with conn_semantica.cursor() as cur:
        cur.execute("drop view if exists v_respuesta_estudio")
    try:
        estado = esquema.revisar_stores(conn_boveda, conn_semantica)
        assert len(estado["como_aplicar"]) == 1
        assert "db/semantica/0002_vista_procedencia.sql" in estado["como_aplicar"][0]
        assert "paneles_semantica" in estado["como_aplicar"][0]
    finally:
        conn_semantica.rollback()


# ── Traducción del error de Postgres ─────────────────────────────────

def test_traduce_el_error_real_que_se_vio_en_produccion():
    mensaje = esquema.explicar_error(
        'relation "v_respuesta_estudio" does not exist\n'
        'LINE 5:           from v_respuesta_estudio'
    )
    assert "0002_vista_procedencia.sql" in mensaje
    assert "semantica" in mensaje
    assert "consultas semánticas" in mensaje


def test_traduce_una_columna_aunque_venga_con_el_alias_de_la_consulta():
    """Postgres nombra la columna con el alias: «column r.hash_texto does not
    exist». Sin contemplarlo, el caso más común de la Fase 2 no se
    reconocería."""
    mensaje = esquema.explicar_error("column r.hash_texto does not exist")
    assert "0003_hash_texto.sql" in mensaje


@pytest.mark.parametrize("error", [
    'relation "tabla_inventada" does not exist',
    "column p.columna_inventada does not exist",
    "division by zero",
    "connection refused",
])
def test_no_inventa_explicaciones_para_errores_que_no_son_de_esquema(error):
    """Si no se sabe qué migración crea eso, se devuelve None y el error sigue
    su camino: es preferible un mensaje genérico a uno seguro y equivocado."""
    assert esquema.explicar_error(error) is None


def test_la_explicacion_no_puede_filtrar_datos_de_una_persona():
    """El resto de los errores no se le muestran al cliente porque pueden traer
    fragmentos de PII. Esta explicación sí se muestra, así que solo puede
    contener el nombre del objeto y el del archivo."""
    mensaje = esquema.explicar_error(
        'relation "v_respuesta_estudio" does not exist\n'
        "DETAIL: la fila era nombre=Ana Pérez, email=ana@ejemplo.uy"
    )
    assert "Ana Pérez" not in mensaje
    assert "ana@ejemplo.uy" not in mensaje


# ── Por la ruta ──────────────────────────────────────────────────────

def test_la_ruta_devuelve_500_cuando_falta_una_migracion(
    conn_boveda, conn_semantica, proveedor, actor
):
    from conftest import ContextoDePrueba
    from panel_api import ruteo

    ctx = ContextoDePrueba(conn_boveda, conn_semantica, proveedor)
    status, cuerpo = ruteo.despachar(
        "GET", "/diagnostico/esquema", {}, {}, actor("analista"), ctx
    )
    assert status == 200 and cuerpo["completo"] is True

    with conn_semantica.cursor() as cur:
        cur.execute("drop view if exists v_respuesta_estudio")
    try:
        status, cuerpo = ruteo.despachar(
            "GET", "/diagnostico/esquema", {}, {}, actor("analista"), ctx
        )
        assert status == 500
        assert cuerpo["completo"] is False
        assert cuerpo["como_aplicar"]
    finally:
        conn_semantica.rollback()
