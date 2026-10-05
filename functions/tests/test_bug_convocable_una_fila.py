"""BUG — `v_persona_convocable` devolvía una fila por consentimiento.

`specs/BUG_v_persona_convocable_duplica.md`. Con las dos finalidades
vigentes —el caso normal— cada persona aparecía dos veces, y todo lo que un
consumidor externo contara sobre la vista (muestra, cuotas, a quién invitar)
salía al doble sin que nada fallara. La `boveda/0021` la deja en una fila por
persona con `exists` en vez de `join`.

Las pruebas son el Definition of Done del reporte, más las vistas y funciones
de la `0014` que el reporte pedía revisar contra el mismo patrón.
"""

import pathlib
import sys
import uuid

import pytest

from conftest import VERSION_TEXTO

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

import verificar_coloquio as vc  # noqa: E402

from panel_api import consentimiento, db, estadisticas, personas  # noqa: E402

CONTACTO = "contacto_participacion"
SEMANTICO = "uso_semantico"


def _alta(conn, alta_basica, documento, finalidades):
    return str(personas.alta(
        conn, alta_basica(documento=documento, finalidades=finalidades)
    )["id_persona"])


def _filas(conn, id_persona):
    return db.todas(
        conn, "select * from v_persona_convocable where id_persona = %s",
        (id_persona,))


# ════════════════════════════════════════════════════════════════════
#  Definition of Done
# ════════════════════════════════════════════════════════════════════

def test_count_igual_a_count_distinct(conn_boveda, alta_basica):
    """El chequeo literal del reporte, sobre una base con los tres casos."""
    _alta(conn_boveda, alta_basica, "7100001", (CONTACTO, SEMANTICO))
    _alta(conn_boveda, alta_basica, "7100002", (CONTACTO,))
    _alta(conn_boveda, alta_basica, "7100003", (SEMANTICO,))
    fila = db.una(
        conn_boveda,
        """select count(*) = count(distinct id_persona) as una_por_persona,
                  count(*) as filas
             from v_persona_convocable""")
    assert fila["una_por_persona"]
    assert fila["filas"] == 3


def test_con_las_dos_finalidades_aparece_una_sola_vez(conn_boveda, alta_basica):
    id_persona = _alta(conn_boveda, alta_basica, "7100011", (CONTACTO, SEMANTICO))
    filas = _filas(conn_boveda, id_persona)
    assert len(filas) == 1
    assert filas[0]["finalidades"] == [CONTACTO, SEMANTICO]


def test_con_una_sola_finalidad_aparece_una_vez(conn_boveda, alta_basica):
    id_persona = _alta(conn_boveda, alta_basica, "7100021", (CONTACTO,))
    filas = _filas(conn_boveda, id_persona)
    assert len(filas) == 1
    assert filas[0]["finalidades"] == [CONTACTO]


def test_un_reotorgamiento_no_la_repite(conn_boveda, alta_basica):
    """Re-otorgar con una versión nueva agrega fila sin pisar el historial.
    Con el `join` viejo eso era una tercera fila; con `exists`, nada."""
    id_persona = _alta(conn_boveda, alta_basica, "7100031", (CONTACTO, SEMANTICO))
    consentimiento.otorgar(conn_boveda, id_persona, CONTACTO,
                           "consentimiento-2026-02")
    filas = _filas(conn_boveda, id_persona)
    assert len(filas) == 1
    assert filas[0]["finalidades"] == [CONTACTO, SEMANTICO]


def test_sin_la_finalidad_requerida_no_aparece(conn_boveda, alta_basica):
    """No regresión del gate: la corrección es de cardinalidad, no de
    criterio. Quien solo tiene `uso_semantico` no es convocable."""
    solo_semantico = _alta(conn_boveda, alta_basica, "7100041", (SEMANTICO,))
    convocables = {
        str(f["id_persona"]) for f in db.todas(
            conn_boveda,
            "select id_persona from v_persona_convocable "
            " where 'contacto_participacion' = any(finalidades)")}
    assert solo_semantico not in convocables
    assert not consentimiento.esta_vigente(conn_boveda, solo_semantico, CONTACTO)
    assert consentimiento.esta_vigente(conn_boveda, solo_semantico, SEMANTICO)


def test_sin_ningun_consentimiento_vigente_no_aparece(conn_boveda, alta_basica):
    id_persona = _alta(conn_boveda, alta_basica, "7100051", (CONTACTO,))
    consentimiento.marcar_retirado(conn_boveda, id_persona)
    assert _filas(conn_boveda, id_persona) == []


def test_el_filtro_en_lote_no_repite_personas(conn_boveda, alta_basica):
    a = _alta(conn_boveda, alta_basica, "7100061", (CONTACTO, SEMANTICO))
    b = _alta(conn_boveda, alta_basica, "7100062", (CONTACTO,))
    habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        conn_boveda, [a, b], CONTACTO)
    assert habilitadas == [a, b]
    assert bloqueadas == []


# ════════════════════════════════════════════════════════════════════
#  Las finalidades de ámbito estudio
# ════════════════════════════════════════════════════════════════════

def test_una_finalidad_de_estudio_no_entra_en_el_arreglo(conn_boveda, alta_basica):
    """`grabacion_av` para un estudio no autoriza a grabar en otro: en el
    arreglo de la persona daría a entender lo contrario. Se consulta en la
    relación por consentimiento, con su estudio."""
    id_persona = _alta(conn_boveda, alta_basica, "7100071", (CONTACTO,))
    estudio = str(uuid.uuid4())
    db.ejecutar(
        conn_boveda,
        """insert into consentimiento
                  (id_persona, finalidad, estado, version_texto, ref_estudio)
           values (%s, 'grabacion_av', 'vigente', %s, %s)""",
        (id_persona, VERSION_TEXTO, estudio))

    assert _filas(conn_boveda, id_persona)[0]["finalidades"] == [CONTACTO]
    por_consentimiento = db.todas(
        conn_boveda,
        """select finalidad, ref_estudio::text as ref_estudio
             from v_persona_finalidad_vigente where id_persona = %s
            order by finalidad""", (id_persona,))
    assert por_consentimiento == [
        {"finalidad": CONTACTO, "ref_estudio": None},
        {"finalidad": "grabacion_av", "ref_estudio": estudio},
    ]


def test_la_relacion_por_consentimiento_no_repite_su_clave(conn_boveda,
                                                           alta_basica):
    id_persona = _alta(conn_boveda, alta_basica, "7100081", (CONTACTO, SEMANTICO))
    consentimiento.otorgar(conn_boveda, id_persona, CONTACTO,
                           "consentimiento-2026-02")
    fila = db.una(
        conn_boveda,
        """select count(*) as filas,
                  (select count(*) from (select distinct id_persona, finalidad,
                                                ref_estudio
                                           from v_persona_finalidad_vigente) k)
                    as claves
             from v_persona_finalidad_vigente""")
    assert fila["filas"] == fila["claves"] == 2


# ════════════════════════════════════════════════════════════════════
#  «Revisar también»: lo demás de la 0014
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def dueno(conn_boveda, dsn_boveda):
    conn = vc.conectar(dsn_boveda)
    yield conn
    with conn.cursor() as cur:
        cur.execute("delete from reidentificacion where actor_uid = 'bug-0021'")
        cur.execute("delete from persona where documento like 'BUG21-%'")
        cur.execute("delete from panel where nombre like 'BUG21-%'")
    conn.close()


def _persona_con_dos_finalidades(dueno, sufijo):
    with dueno.cursor() as cur:
        cur.execute(
            """insert into persona (documento, nombre, celular, estado)
               values (%s, 'Persona BUG21', %s, 'activa')
               returning id_persona""",
            (f"BUG21-{sufijo}", f"+59809811{sufijo}"))
        id_persona = cur.fetchone()["id_persona"]
        for finalidad in (CONTACTO, SEMANTICO):
            cur.execute(
                """insert into consentimiento
                          (id_persona, finalidad, estado, version_texto)
                   values (%s, %s, 'vigente', %s)""",
                (id_persona, finalidad, VERSION_TEXTO))
    return id_persona


def test_el_contacto_se_entrega_y_audita_una_sola_vez(dueno):
    """`contacto_para_convocatoria` devuelve un escalar y escribe con
    `insert … values`: aunque la persona tenga dos finalidades, una lectura es
    una entrega y una fila de auditoría."""
    id_persona = _persona_con_dos_finalidades(dueno, "001")
    with dueno.cursor() as cur:
        cur.execute("insert into panel (nombre) values ('BUG21-panel') "
                    "returning id")
        panel_id = cur.fetchone()["id"]
        cur.execute("""insert into encuesta (panel_id, nombre, estado)
                       values (%s, 'BUG21-encuesta', 'en_campo') returning id""",
                    (panel_id,))
        cur.execute("insert into participacion (encuesta_id, id_persona) "
                    "values (%s, %s)", (cur.fetchone()["id"], id_persona))
        cur.execute(
            "select contacto_para_convocatoria(%s, 'celular', 'convocatoria', "
            "'bug-0021') as dato", (id_persona,))
        entregas = cur.fetchall()
        cur.execute("select count(*) as n from reidentificacion "
                    " where id_persona = %s and actor_uid = 'bug-0021'",
                    (id_persona,))
        auditadas = cur.fetchone()["n"]
    assert [e["dato"] for e in entregas] == ["+59809811001"]
    assert auditadas == 1


def test_la_fatiga_es_una_fila_por_persona_y_panel(conn_boveda, alta_basica):
    """`v_fatiga_panelista` es por (persona, panel) a propósito —el umbral es
    por panel—. Con participaciones en dos encuestas del mismo panel y una
    membresía en un segundo panel, no repite el par y no infla los conteos."""
    from panel_api import encuestas, paneles

    id_persona = _alta(conn_boveda, alta_basica, "7100091", (CONTACTO, SEMANTICO))
    panel_a = paneles.crear(conn_boveda, "Panel A")["id"]
    panel_b = paneles.crear(conn_boveda, "Panel B")["id"]
    for panel_id in (panel_a, panel_b):
        db.ejecutar(conn_boveda,
                    "insert into membresia (panel_id, id_persona) values (%s, %s)",
                    (panel_id, id_persona))
    for nombre in ("Ola 1", "Ola 2"):
        encuesta = encuestas.crear(conn_boveda, panel_a, nombre)
        db.ejecutar(
            conn_boveda,
            """insert into participacion (encuesta_id, id_persona, origen,
                                          respondio)
               values (%s, %s, 'convocatoria', true)""",
            (encuesta["id"], id_persona))

    filas = {f["panel_id"]: f for f in db.todas(
        conn_boveda,
        "select * from v_fatiga_panelista where id_persona = %s", (id_persona,))}
    assert set(filas) == {panel_a, panel_b}
    assert filas[panel_a]["totales"] == 2
    assert filas[panel_a]["recientes"] == 2
    assert filas[panel_a]["respondidas"] == 2
    assert filas[panel_b]["totales"] == 0


def test_las_estadisticas_cuentan_personas_y_no_filas(conn_boveda, alta_basica):
    """La pantalla de estadísticas contaba filas de `consentimiento`: un
    re-otorgamiento contaba dos veces a la misma persona."""
    id_persona = _alta(conn_boveda, alta_basica, "7100101", (CONTACTO, SEMANTICO))
    _alta(conn_boveda, alta_basica, "7100102", (CONTACTO,))
    consentimiento.otorgar(conn_boveda, id_persona, CONTACTO,
                           "consentimiento-2026-02")
    por = {f["finalidad"]: f for f in
           estadisticas.consentimiento(conn_boveda)["por_finalidad"]}
    assert por[CONTACTO]["vigentes"] == 2
    assert por[CONTACTO]["sin_el"] == 0
    assert por[SEMANTICO]["vigentes"] == 1
    assert por[SEMANTICO]["sin_el"] == 1


# ════════════════════════════════════════════════════════════════════
#  La batería ahora mira la cardinalidad
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def coloquio(dsn_boveda):
    conn = vc.conectar(vc._dsn_con_usuario(dsn_boveda, "coloquio_app"))
    yield conn
    conn.close()


def test_la_bateria_pasa_con_personas_de_dos_finalidades(dueno, coloquio):
    _persona_con_dos_finalidades(dueno, "002")
    detalle = vc.una_fila_por_clave(coloquio, dueno)
    assert "v_persona_convocable" in detalle


def test_la_bateria_detecta_una_vista_que_repite(dueno, coloquio, monkeypatch):
    """Si la clave declarada fuera más chica que la real —que es exactamente
    lo que le pasaba a `v_persona_convocable`—, el chequeo falla."""
    _persona_con_dos_finalidades(dueno, "003")
    monkeypatch.setitem(vc.CLAVE_POR_RELACION, "v_persona_finalidad_vigente",
                        ("id_persona",))
    with pytest.raises(vc.Falla) as fallo:
        vc.una_fila_por_clave(coloquio, dueno)
    assert "v_persona_finalidad_vigente" in str(fallo.value)


def test_toda_vista_con_personas_tiene_clave_declarada():
    """La próxima vista que se sume a la superficie no se puede saltear la
    pregunta: si tiene `id_persona` y no está en `CLAVE_POR_RELACION`, el
    chequeo falla. Acá se comprueba al menos que las de hoy estén."""
    con_personas = {"v_persona_convocable", "v_persona_finalidad_vigente",
                    "v_fatiga_panelista"}
    assert con_personas <= set(vc.CLAVE_POR_RELACION)
    assert con_personas <= set(vc.RELACIONES_PERMITIDAS)
