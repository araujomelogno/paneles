"""Superficie de API de la Fase 2: los contratos del spec, por el router.

Las pruebas de los módulos verifican el comportamiento; estas verifican que
esté colgado de la ruta correcta, con el permiso correcto y con la forma de
respuesta que el contrato promete.
"""

import csv
import io

import pytest

from panel_api import composicion, ruteo
from panel_api.errores import DatosInvalidos, NoEncontrado, SinPermiso

from conftest import ContextoDePrueba
from corpus import CRITERIO_FERNET, sembrar

# El contrato propuesto en specs/SPEC_fase2.md §6, con el permiso que le toca.
CONTRATO = [
    ("POST", "/consultas", "consultar"),
    ("GET", "/paneles/1/composicion", "leer"),
    ("PUT", "/paneles/1/objetivo", "gestionar_paneles"),
    ("GET", "/paneles/1/participacion", "leer"),
    ("GET", "/usuarios", "gestionar_usuarios"),
    ("POST", "/usuarios", "gestionar_usuarios"),
    ("PATCH", "/usuarios/uid-1", "gestionar_usuarios"),
]


@pytest.mark.parametrize("metodo,camino,permiso", CONTRATO)
def test_el_contrato_del_spec_esta_completo(metodo, camino, permiso):
    funcion, permiso_real, _ = ruteo.resolver(metodo, camino)
    assert callable(funcion)
    assert permiso_real == permiso


@pytest.fixture
def api(conn_boveda, conn_semantica, proveedor):
    """Un despachador contra los dos stores reales."""
    contexto = sembrar(conn_boveda, conn_semantica, proveedor)
    conn_boveda.commit()
    conn_semantica.commit()
    ctx = ContextoDePrueba(conn_boveda, conn_semantica, proveedor)

    def _pedir(metodo, camino, quien, cuerpo=None, consulta=None):
        return ruteo.despachar(
            metodo, camino, cuerpo or {}, consulta or {}, quien, ctx
        )

    _pedir.contexto = contexto
    _pedir.ctx = ctx
    return _pedir


# ── Consultas ────────────────────────────────────────────────────────

def test_correr_una_consulta_por_la_ruta(api, actor):
    status, resultado = api("POST", "/consultas", actor("analista"),
                            {"criterios": [CRITERIO_FERNET]})
    assert status == 200
    assert resultado["items"]
    assert resultado["puente"]["estrategia"]
    assert resultado["diagnostico"]["ms_total"] >= 0


@pytest.mark.parametrize("rol", ["admin", "operaciones", "analista"])
def test_quien_consulta_puede_consultar(api, actor, rol):
    status, _ = api("POST", "/consultas", actor(rol),
                    {"criterios": [CRITERIO_FERNET]})
    assert status == 200


def test_el_dpo_no_corre_consultas_semanticas(api, actor):
    """Consultar mueve el corpus por el reranker y por la API de Claude: es el
    trabajo del analista, no del cumplimiento."""
    with pytest.raises(SinPermiso):
        api("POST", "/consultas", actor("dpo"), {"criterios": [CRITERIO_FERNET]})


def test_el_formato_csv_devuelve_el_archivo_dentro_del_json(api, actor):
    """P1 — exportación. La API sigue siendo «JSON siempre»: el CSV viaja como
    texto dentro de la respuesta y el navegador arma la descarga."""
    status, resultado = api("POST", "/consultas", actor("analista"), {
        "criterios": [CRITERIO_FERNET], "formato": "csv",
    })
    assert status == 200
    assert resultado["formato"] == "csv"
    assert resultado["nombre_archivo"].endswith(".csv")
    filas = list(csv.reader(io.StringIO(resultado["csv"])))
    assert filas[0] == ["id_persona", "puntaje", "evidencia"]
    assert len(filas) - 1 == resultado["filas"]
    # El diagnóstico y el puente viajan igual: la exportación no es una vía
    # para saltearse la trazabilidad de la consulta.
    assert resultado["puente"]["estrategia"]
    assert "degradaciones" in resultado


def test_el_ciclo_completo_de_una_consulta_guardada(api, actor):
    quien = actor("analista")
    status, guardada = api("POST", "/consultas/guardadas", quien, {
        "nombre": "Fernet", "descripcion": "Para la ola de bebidas",
        "definicion": {"criterios": [CRITERIO_FERNET]},
    })
    assert status == 201

    _, listado = api("GET", "/consultas/guardadas", quien)
    assert [c["nombre"] for c in listado["items"]] == ["Fernet"]

    _, una = api("GET", f"/consultas/guardadas/{guardada['id']}", quien)
    assert una["definicion"]["criterios"][0]["texto"] == CRITERIO_FERNET

    api("DELETE", f"/consultas/guardadas/{guardada['id']}", quien)
    _, vacio = api("GET", "/consultas/guardadas", quien)
    assert vacio["items"] == []


def test_borrar_una_consulta_guardada_que_no_existe_da_404(api, actor):
    with pytest.raises(NoEncontrado):
        api("DELETE", "/consultas/guardadas/9999", actor("analista"))


# ── Composición y objetivo ───────────────────────────────────────────

def test_cargar_y_leer_el_objetivo_por_la_ruta(api, actor):
    panel_id = api.contexto["panel"]["id"]
    status, guardado = api("PUT", f"/paneles/{panel_id}/objetivo",
                           actor("operaciones"), {
        "objetivos": [
            {"dimension": "sexo", "categoria": "F", "proporcion": 0.5},
            {"dimension": "sexo", "categoria": "M", "proporcion": 0.5},
        ],
    })
    assert status == 200
    assert guardado["dimensiones"]["sexo"] == {"F": 0.5, "M": 0.5}

    _, leido = api("GET", f"/paneles/{panel_id}/objetivo", actor("analista"))
    assert leido["dimensiones"] == guardado["dimensiones"]

    _, salida = api("GET", f"/paneles/{panel_id}/composicion", actor("analista"),
                    consulta={"dimensiones": "sexo"})
    sexo = salida["dimensiones"][0]
    assert sexo["brecha_disponible"] is True


def test_un_analista_no_puede_cargar_el_objetivo(api, actor):
    """Cargar el universo de referencia cambia todas las brechas del panel:
    es gestión de panel, no consulta."""
    panel_id = api.contexto["panel"]["id"]
    with pytest.raises(SinPermiso):
        api("PUT", f"/paneles/{panel_id}/objetivo", actor("analista"), {
            "objetivos": [{"dimension": "sexo", "categoria": "F", "proporcion": 1.0}],
        })


def test_el_cruce_se_pide_con_dos_dimensiones_separadas_por_coma(api, actor):
    panel_id = api.contexto["panel"]["id"]
    _, salida = api("GET", f"/paneles/{panel_id}/composicion", actor("analista"),
                    consulta={"cruce": "sexo,localidad"})
    assert salida["cruce"]["dimensiones"] == ["sexo", "localidad"]


def test_un_cruce_de_una_sola_dimension_se_rechaza(api, actor):
    panel_id = api.contexto["panel"]["id"]
    with pytest.raises(DatosInvalidos):
        api("GET", f"/paneles/{panel_id}/composicion", actor("analista"),
            consulta={"cruce": "sexo"})


# ── Participación ────────────────────────────────────────────────────

def test_el_tablero_de_participacion_por_la_ruta(api, actor):
    panel_id = api.contexto["panel"]["id"]
    _, tablero = api("GET", f"/paneles/{panel_id}/participacion", actor("analista"))
    assert tablero["miembros"] == 5
    assert "tasa_respuesta" in tablero["respuesta"]
    assert "distribucion" in tablero["ultimo_contacto"]
    assert "distribucion" in tablero["convocatorias"]


def test_las_olas_se_listan_filtrando_por_panel(api, actor):
    panel_id = api.contexto["panel"]["id"]
    _, salida = api("GET", "/participacion/olas", actor("analista"),
                    consulta={"panel_id": str(panel_id)})
    assert [o["nombre"] for o in salida["items"]] == ["Ola 1 — Bebidas"]


# ── Usuarios ─────────────────────────────────────────────────────────

def test_el_alta_de_usuario_devuelve_201_y_el_re_alta_200(api, actor):
    admin = actor("admin", uid="uid-admin")
    cuerpo = {"email": "nueva@equipos.com.uy", "rol": "analista", "nombre": "Nueva"}
    status, primero = api("POST", "/usuarios", admin, cuerpo)
    assert status == 201 and primero["estado"] == "creado"

    status, segundo = api("POST", "/usuarios", admin, cuerpo)
    assert status == 200 and segundo["estado"] == "existente"


def test_la_auditoria_de_usuarios_se_lee_por_la_ruta(api, actor):
    admin = actor("admin", uid="uid-admin")
    api("POST", "/usuarios", admin,
        {"email": "nueva@equipos.com.uy", "rol": "analista"})
    _, salida = api("GET", "/usuarios/auditoria", admin)
    assert [r["accion"] for r in salida["items"]] == ["alta"]
    assert salida["items"][0]["actor_uid"] == "uid-admin"


def test_la_ruta_de_auditoria_no_se_confunde_con_un_uid(api, actor):
    """`/usuarios/auditoria` y `/usuarios/<uid>` comparten forma: la primera
    tiene que ganar en GET."""
    funcion, _, params = ruteo.resolver("GET", "/usuarios/auditoria")
    assert funcion.__name__ == "auditoria_usuarios"
    assert params == {}
