"""R5.9 a R5.13 — la superficie de selección cualitativa de COLOQUIO
(`specs/ADDENDUM_paneles_superficie_cuali.md`, `boveda/0034`, D84).

Lo que se prueba es el Definition of Done del addendum, contra Postgres de
verdad y, donde importa, **conectado como `coloquio_app`**: lo que se quiere
saber es qué puede hacer el consumidor, no qué puede hacer el dueño.

* El catálogo refleja el vocabulario vivo.
* El filtro por atributo dinámico: un atributo creado después de la
  migración, unión dentro de un atributo, intersección entre atributos,
  derivados (edad, tramo), y error ante lo desconocido.
* Los atributos de un conjunto y la ficha: solo del gate, sin PII.
* Identificar: una fila de auditoría por persona devuelta, con motivo
  `seleccion_cuali` y sistema `coloquio`, y sus tres límites.
* `contacto_para_convocatoria()` sigue negándose sin convocatoria.
"""

import json
import pathlib
import subprocess

import psycopg
import pytest

import re

from panel_api import atributos, auditoria, db, lista_negra, personas

from conftest import consentimientos

RAIZ = pathlib.Path(__file__).resolve().parents[2]
LA_0034 = RAIZ / "db" / "boveda" / "0034_superficie_seleccion_cuali.sql"
ACTOR = "coordinadora@equipos.com.uy"
SESION = "coloquio-sesion-42"


def _persona(conn, documento, finalidades=("contacto_participacion", "uso_semantico"),
             **datos):
    return str(personas.alta(conn, {
        "persona": {"documento": documento, "nombre": f"Persona {documento}",
                    "email": f"{documento}@ej.uy", "celular": "+59899123456",
                    **datos},
        "consentimientos": consentimientos(*finalidades)})["id_persona"])


@pytest.fixture
def coloquio(dsn_boveda):
    """La conexión del consumidor, como en `verificar_coloquio.py`."""
    dsn = dsn_boveda.replace("postgres@", "coloquio_app@", 1)
    conn = psycopg.connect(dsn, row_factory=psycopg.rows.dict_row, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture
def gente(conn_boveda):
    """Cuatro convocables con sexo y fecha de nacimiento; una con solo
    `uso_semantico` (está en la vista, pero no se la puede identificar);
    una sin ningún consentimiento y una en la lista negra (fuera de la
    vista las dos)."""
    ids = {
        "ana": _persona(conn_boveda, "1-1", sexo="F", fecha_nacimiento="1990-05-01"),
        "bea": _persona(conn_boveda, "1-2", sexo="F", fecha_nacimiento="1960-05-01"),
        "carlos": _persona(conn_boveda, "1-3", sexo="M", fecha_nacimiento="1988-05-01"),
        "dani": _persona(conn_boveda, "1-4", sexo="M"),
        "sin_contacto": _persona(conn_boveda, "1-5", finalidades=("uso_semantico",),
                                 sexo="F", fecha_nacimiento="1990-05-01"),
        "en_lista_negra": _persona(conn_boveda, "1-6", sexo="F",
                                   fecha_nacimiento="1990-05-01"),
    }
    ids["sin_consentimiento"] = str(db.una(
        conn_boveda, """insert into persona (documento, nombre, fecha_nacimiento)
                        values ('1-7', 'Nunca consintió', '1990-05-01')
                     returning id_persona""")["id_persona"])
    lista_negra.agregar(conn_boveda, ids["en_lista_negra"], "calidad_respuestas",
                        "respuestas al azar")
    conn_boveda.commit()
    return ids


def _filtrar(conn, filtros, finalidad="contacto_participacion", limite=500):
    return {str(f["id_persona"]) for f in db.todas(
        conn, "select id_persona from f_persona_convocable_filtrada(%s, %s::jsonb, %s)",
        (finalidad, json.dumps(filtros), limite))}


# ════════════════════════════════════════════════════════════════════
#  La migración
# ════════════════════════════════════════════════════════════════════

def test_aplicarla_dos_veces_deja_todo_igual(dsn_boveda):
    for _ in range(2):
        subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn_boveda,
                        "-f", str(LA_0034)], check=True, capture_output=True)


def test_el_motivo_seleccion_cuali_esta_en_el_catalogo_y_en_su_espejo(conn_boveda):
    assert "seleccion_cuali" in auditoria.MOTIVOS_REIDENTIFICACION
    assert db.una(conn_boveda, "select 1 as hay from motivo_reidentificacion "
                               "where codigo = 'seleccion_cuali'")


# ════════════════════════════════════════════════════════════════════
#  R5.9 · El catálogo
# ════════════════════════════════════════════════════════════════════

def test_el_catalogo_sigue_al_vocabulario_vivo(conn_boveda, coloquio):
    nivel = atributos.crear(conn_boveda, {
        "clave": "nivel_educativo", "etiqueta": "Nivel educativo",
        "categorias": [{"clave": "terciario", "etiqueta": "Terciario"},
                       {"clave": "posgrado", "etiqueta": "Posgrado"}],
        "es_especial": False})
    conn_boveda.commit()
    catalogo = {f["clave"]: f for f in db.todas(coloquio,
                                                "select * from v_atributo_catalogo")}
    # En orden (`orden`, y a igual orden por clave).
    assert [c["clave"] for c in catalogo["nivel_educativo"]["categorias"]] \
        == ["posgrado", "terciario"]
    assert catalogo["nivel_educativo"]["es_especial"] is False
    assert {"sexo", "edad", "tramo_etario"} <= set(catalogo)

    atributos.desactivar(conn_boveda, nivel["id"])
    conn_boveda.commit()
    assert "nivel_educativo" not in {
        f["clave"] for f in db.todas(coloquio, "select clave from v_atributo_catalogo")}


# ════════════════════════════════════════════════════════════════════
#  R5.11 · Convocables filtrados por atributo dinámico
# ════════════════════════════════════════════════════════════════════

def test_un_atributo_creado_despues_de_la_migracion_se_puede_filtrar(
        conn_boveda, coloquio, gente):
    """DoD 4: de punta a punta, como `coloquio_app`."""
    atributos.crear(conn_boveda, {
        "clave": "nivel_educativo", "etiqueta": "Nivel educativo",
        "categorias": [{"clave": "terciario", "etiqueta": "Terciario"},
                       {"clave": "posgrado", "etiqueta": "Posgrado"},
                       {"clave": "secundario", "etiqueta": "Secundario"}]})
    atributos.fijar(conn_boveda, gente["ana"], "nivel_educativo", "terciario")
    atributos.fijar(conn_boveda, gente["bea"], "nivel_educativo", "posgrado")
    atributos.fijar(conn_boveda, gente["carlos"], "nivel_educativo", "secundario")
    atributos.fijar(conn_boveda, gente["sin_contacto"], "nivel_educativo", "terciario")
    conn_boveda.commit()

    # Dos categorías del mismo atributo: la unión.
    assert _filtrar(coloquio, {"nivel_educativo": {"en": ["terciario", "posgrado"]}}) \
        == {gente["ana"], gente["bea"]}
    # Dos atributos distintos: la intersección.
    assert _filtrar(coloquio, {"nivel_educativo": {"en": ["terciario", "posgrado"]},
                               "sexo": {"en": ["F"]},
                               "edad": {"min": 30, "max": 44}}) == {gente["ana"]}


def test_los_derivados_salen_de_la_precedencia_de_siempre(coloquio, gente):
    """Edad y tramo no están en `persona_atributo`: se calculan de la fecha
    de nacimiento (`f_atributo_persona`)."""
    assert _filtrar(coloquio, {"edad": {"min": 60}}) == {gente["bea"]}
    assert _filtrar(coloquio, {"tramo_etario": {"en": ["35-44"]}}) \
        == {gente["ana"], gente["carlos"]}


def test_sin_filtros_es_el_gate_con_su_finalidad(coloquio, gente):
    todos = _filtrar(coloquio, {})
    assert todos == {gente["ana"], gente["bea"], gente["carlos"], gente["dani"]}
    assert gente["sin_contacto"] in _filtrar(coloquio, {}, finalidad="uso_semantico")
    filas = db.todas(coloquio, "select * from f_persona_convocable_filtrada("
                               "'contacto_participacion', '{}', 2)")
    assert len(filas) == 2
    # Devuelve id y finalidades, nada más.
    assert set(filas[0]) == {"id_persona", "finalidades"}
    assert "contacto_participacion" in filas[0]["finalidades"]


@pytest.mark.parametrize("filtros, mensaje", [
    ({"no_existe": {"en": ["x"]}}, "no está en el catálogo"),
    ({"sexo": {"min": 3}}, "admite"),
    ({"edad": {"desde": "2020-01-01"}}, "admite"),
    ({"sexo": {"en": ["Femenina"]}}, re.escape("no tiene la(s) categoría(s)")),
    ({"sexo": {"en": []}}, "no se puede leer"),
    ({"sexo": {}}, "un objeto con un operador"),
    ({"edad": {"min": "treinta"}}, "no se puede leer"),
])
def test_lo_desconocido_es_error_y_no_un_filtro_ignorado(coloquio, filtros, mensaje):
    with pytest.raises(psycopg.Error, match=mensaje):
        _filtrar(coloquio, filtros)


def test_un_atributo_dado_de_baja_no_se_puede_filtrar(conn_boveda, coloquio):
    nivel = atributos.crear(conn_boveda, {"clave": "zona", "etiqueta": "Zona",
                                          "categorias": [{"clave": "norte",
                                                          "etiqueta": "Norte"}]})
    atributos.desactivar(conn_boveda, nivel["id"])
    conn_boveda.commit()
    with pytest.raises(psycopg.Error, match="dado de baja"):
        _filtrar(coloquio, {"zona": {"en": ["norte"]}})


def test_una_finalidad_inventada_es_error(coloquio):
    with pytest.raises(psycopg.Error, match="Finalidad desconocida"):
        _filtrar(coloquio, {}, finalidad="contactarlos")


def test_nadie_sin_consentimiento_aparece(coloquio, gente):
    mujeres = _filtrar(coloquio, {"sexo": {"en": ["F"]}})
    assert gente["sin_contacto"] not in mujeres          # sin la finalidad pedida
    assert gente["en_lista_negra"] not in mujeres        # fuera del gate (D80)


# ════════════════════════════════════════════════════════════════════
#  R5.10 · Atributos de un conjunto
# ════════════════════════════════════════════════════════════════════

def test_los_atributos_de_un_conjunto_son_solo_los_que_existen(coloquio, gente):
    filas = db.todas(coloquio, "select * from atributos_de_personas(%s::uuid[])",
                     ([gente["ana"], gente["dani"], gente["sin_consentimiento"],
                       gente["en_lista_negra"]],))
    por = {}
    for f in filas:
        por.setdefault(str(f["id_persona"]), {})[f["atributo"]] = f
    assert set(por[gente["ana"]]) == {"sexo", "edad", "tramo_etario"}
    assert por[gente["ana"]]["edad"]["procedencia"] == "derivado"
    # Dani no tiene fecha de nacimiento: sin edad ni tramo, y no vienen nulos.
    assert set(por[gente["dani"]]) == {"sexo"}
    # Fuera de `v_persona_convocable`: nada.
    assert gente["sin_consentimiento"] not in por
    assert gente["en_lista_negra"] not in por


def test_doscientos_ids_en_una_llamada_y_tope_de_quinientos(coloquio, gente):
    ids = [gente["ana"]] + [str(u["u"]) for u in db.todas(
        coloquio, "select gen_random_uuid() as u from generate_series(1, 199)")]
    filas = db.todas(coloquio, "select * from atributos_de_personas(%s::uuid[])", (ids,))
    assert {str(f["id_persona"]) for f in filas} == {gente["ana"]}
    with pytest.raises(psycopg.Error, match="tope es 500"):
        db.todas(coloquio, "select * from atributos_de_personas("
                           "array(select gen_random_uuid() from generate_series(1, 501)))")


# ════════════════════════════════════════════════════════════════════
#  R5.12 · La ficha seudónima
# ════════════════════════════════════════════════════════════════════

def test_la_ficha_no_identifica_ni_audita(conn_boveda, coloquio, gente):
    from panel_api import pii

    ficha = db.una(coloquio, "select ficha_candidato(%s) as f", (gente["ana"],))["f"]
    assert ficha["estado"] == "activa" and ficha["enrolado_en"]
    assert {a["atributo"] for a in ficha["atributos"]} == {"sexo", "edad", "tramo_etario"}
    texto = json.dumps(ficha)
    for campo in pii.CAMPOS_PII:
        assert f'"{campo}"' not in texto, campo
    assert "Persona 1-1" not in texto and "1-1@ej.uy" not in texto
    assert db.una(conn_boveda, "select count(*)::int as n from reidentificacion")["n"] == 0
    for fuera in (gente["sin_consentimiento"], gente["en_lista_negra"]):
        with pytest.raises(psycopg.Error, match="No hay un candidato"):
            db.una(coloquio, "select ficha_candidato(%s)", (fuera,))


# ════════════════════════════════════════════════════════════════════
#  R5.13 · Identificar candidatos (D84)
# ════════════════════════════════════════════════════════════════════

def _identificar(conn, ids, referencia=SESION, actor=ACTOR):
    return db.todas(conn, "select * from identificar_candidatos(%s::uuid[], %s, %s)",
                    (ids, referencia, actor))


def test_veinte_candidatos_dejan_veinte_filas_de_auditoria(conn_boveda, coloquio):
    """DoD 5: motivo `seleccion_cuali`, sistema `coloquio`, la sesión en el
    contexto y quién lo pidió."""
    ids = [_persona(conn_boveda, f"2-{i}") for i in range(20)]
    conn_boveda.commit()
    filas = _identificar(coloquio, ids)
    assert len(filas) == 20
    assert all(f["nombre"] and f["email"] and f["celular"] for f in filas)
    auditoria_ = db.todas(conn_boveda, "select * from reidentificacion")
    assert len(auditoria_) == 20
    assert {str(a["id_persona"]) for a in auditoria_} == set(ids)
    assert {(a["motivo"], a["sistema"], a["actor_uid"], a["actor_email"],
             a["contexto"]["referencia"]) for a in auditoria_} == {
        ("seleccion_cuali", "coloquio", ACTOR, ACTOR, SESION)}


def test_identificar_reaplica_el_gate_sin_decir_por_que(conn_boveda, coloquio, gente):
    # Sin contacto_participacion, sin consentimiento, en la lista negra: no
    # vienen, y no se dice por qué.
    filas = _identificar(coloquio, [gente["ana"], gente["sin_contacto"],
                                    gente["sin_consentimiento"], gente["en_lista_negra"]])
    assert [str(f["id_persona"]) for f in filas] == [gente["ana"]]
    assert db.una(conn_boveda, "select count(*)::int as n from reidentificacion")["n"] == 1


@pytest.mark.parametrize("referencia, actor, mensaje", [
    (None, ACTOR, "referencia de la sesión"),
    ("  ", ACTOR, "referencia de la sesión"),
    (SESION, None, "Hace falta el actor"),
    (SESION, "", "Hace falta el actor"),
])
def test_sin_referencia_o_sin_actor_falla(conn_boveda, coloquio, gente,
                                          referencia, actor, mensaje):
    with pytest.raises(psycopg.Error, match=mensaje):
        _identificar(coloquio, [gente["ana"]], referencia, actor)
    assert db.una(conn_boveda, "select count(*)::int as n from reidentificacion")["n"] == 0


def test_con_201_ids_falla(coloquio):
    with pytest.raises(psycopg.Error, match="tope es 200"):
        db.todas(coloquio, "select * from identificar_candidatos("
                           "array(select gen_random_uuid() from generate_series(1, 201)),"
                           " %s, %s)", (SESION, ACTOR))


def test_el_contacto_sigue_negandose_sin_convocatoria(coloquio, gente):
    """DoD 6: identificar es la etapa anterior; leer el contacto para
    convocar sigue exigiendo la convocatoria declarada."""
    _identificar(coloquio, [gente["ana"]])
    with pytest.raises(psycopg.Error, match="no hay motivo para leer su contacto"):
        db.una(coloquio, "select contacto_para_convocatoria(%s, 'celular', p_actor => %s)",
               (gente["ana"], ACTOR))


def test_coloquio_no_ve_las_tablas_detras(coloquio):
    for sql in ("select * from persona_atributo limit 1",
                "select * from f_atributo_persona(now()) limit 1",
                "select * from atributo_demografico limit 1"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.todas(coloquio, sql)
