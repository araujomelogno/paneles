"""R3.13 — Cargar panelistas sin asociarlos a un panel.

La única forma de incorporar individuos con sus respuestas era desde una
encuesta, y toda encuesta pertenece a un panel: dar de alta gente implicaba
meterla en un panel, y entonces aparecía en convocatorias, en la composición
y en el muestreo. Este flujo separa las dos cosas.

Lo que se prueba acá es sobre todo lo que **no** pasa: ni membresía, ni
participación, ni indicadores movidos.

Corre contra los dos stores reales.
"""

import pytest

from panel_api import (
    cargas, composicion, db, encuestas, muestreo, paneles, personas, sav)
from panel_api.errores import DatosInvalidos

from conftest import consentimientos

VERSION = "consentimiento-omnibus-2026-08"

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Whisky"}, "orden": 1},
    {"codigo": "SEXO", "texto": "Sexo", "tipo": "cerrada",
     "opciones": {"1": "Masculino", "2": "Femenino"}, "orden": 2},
]

DEMOGRAFICAS = {"NOM": "nombre", "DOC": "documento", "SEXO": "sexo"}

# CONS_SEM cubre el uso semántico —la obligatoria en este flujo—; CONS_CONT
# el contacto, que acá es opcional.
EVIDENCIA = {
    "uso_semantico": {"variable": "CONS_SEM", "valor_afirmativo": "1",
                      "version_texto": VERSION},
    "contacto_participacion": {"variable": "CONS_CONT", "valor_afirmativo": "1",
                               "version_texto": VERSION},
}

FILAS = [
    # Consiente las dos.
    {"ID": "o-1", "NOM": "Ana Externa", "DOC": "10-1", "SEXO": "2",
     "CONS_SEM": "1", "CONS_CONT": "1", "P1": "1"},
    # Consiente el uso semántico y no el contacto: se crea igual.
    {"ID": "o-2", "NOM": "Beto Externo", "DOC": "10-2", "SEXO": "1",
     "CONS_SEM": "1", "CONS_CONT": "2", "P1": "2"},
    # No consiente el uso semántico: no se crea.
    {"ID": "o-3", "NOM": "Zoe Externa", "DOC": "10-3", "SEXO": "2",
     "CONS_SEM": "2", "CONS_CONT": "1", "P1": "1"},
]


@pytest.fixture
def carga(conn_boveda):
    return cargas.crear(conn_boveda, "Ómnibus agosto 2026", "Base de terceros")


def _crear_y_cargar(ctx, carga, filas=None, evidencia=None):
    """El flujo completo: crear los individuos y después ingestar."""
    filas = FILAS if filas is None else filas
    creacion = cargas.crear_individuos(
        ctx.boveda, filas, sav.mapeo_por_campo(DEMOGRAFICAS),
        origen="omnibus", columna_id="ID",
        evidencia_consentimiento=EVIDENCIA if evidencia is None else evidencia,
        opciones_por_variable={
            p["codigo"]: p.get("opciones") or {} for p in PREGUNTAS},
    )
    resultado = cargas.ingestar(
        ctx.boveda, ctx.semantica, carga["id"], PREGUNTAS, filas,
        columna_id="ID", origen="omnibus", proveedor=ctx.embeddings,
        demograficas=DEMOGRAFICAS)
    return creacion, resultado


# ── R3.13.b · La carga y su ref_estudio ──────────────────────────────

def test_una_carga_nace_con_su_ref_estudio(conn_boveda):
    """Cumple frente al store semántico el mismo papel que una encuesta."""
    carga = cargas.crear(conn_boveda, "Ómnibus agosto 2026", "Base de terceros")

    assert carga["ref_estudio"]
    assert carga["nombre"] == "Ómnibus agosto 2026"
    otra = cargas.crear(conn_boveda, "Otra")
    assert otra["ref_estudio"] != carga["ref_estudio"]


def test_una_carga_sin_nombre_se_rechaza(conn_boveda):
    """El nombre es lo que después identifica de dónde salieron los datos."""
    with pytest.raises(DatosInvalidos, match="necesita un nombre"):
        cargas.crear(conn_boveda, "   ")


def test_las_respuestas_quedan_del_lado_semantico_bajo_ese_ref(ctx, carga):
    _, resultado = _crear_y_cargar(ctx, carga)

    assert resultado["respuestas_escritas"] > 0
    individuos = db.una(
        ctx.semantica,
        """
        select count(distinct i.id_persona)::int as n
          from respuesta r
          join individuo i on i.id = r.individuo_id
          join pregunta p on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where c.ref_estudio = %s
        """,
        (carga["ref_estudio"],))
    assert individuos["n"] == 2, "Ana y Beto; Zoe no se creó"


# ── R3.13.b · Lo que NO hace ─────────────────────────────────────────

def test_nadie_queda_como_miembro_de_ningun_panel(ctx, carga, conn_boveda):
    """El punto del requisito."""
    paneles.crear(conn_boveda, "Panel Nacional")

    _crear_y_cargar(ctx, carga)

    assert db.una(
        ctx.boveda, "select count(*)::int as n from membresia")["n"] == 0


def test_no_se_crea_ninguna_participacion(ctx, carga):
    """No hubo convocatoria ni encuesta fieldeada: no hay nada que registrar."""
    _crear_y_cargar(ctx, carga)

    assert db.una(
        ctx.boveda, "select count(*)::int as n from participacion")["n"] == 0


def test_el_resultado_no_informa_membresias_ni_participaciones(ctx, carga):
    """Informar «0 membresías nuevas» sugeriría que podría haber habido."""
    _, resultado = _crear_y_cargar(ctx, carga)

    for clave in ("membresias_nuevas", "membresias_existentes",
                  "participaciones_nuevas", "participaciones_actualizadas"):
        assert clave not in resultado
    assert resultado["sin_panel"] is True


def test_los_indicadores_de_un_panel_no_se_mueven(ctx, conn_boveda, carga):
    """Es la razón de ser del requisito: si esta gente entrara al panel,
    distorsionaría su composición y su muestreo."""
    panel = paneles.crear(conn_boveda, "Panel Nacional")
    propio = personas.alta(conn_boveda, {
        "persona": {"documento": "1-1", "nombre": "Panelista propio", "sexo": "F"},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico"),
    })["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], propio)
    antes = composicion.contar_miembros(conn_boveda, panel["id"])

    _crear_y_cargar(ctx, carga)

    despues = composicion.contar_miembros(conn_boveda, panel["id"])
    assert despues == antes == 1

    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    propuesta = muestreo.proponer(conn_boveda, encuesta["id"], cantidad=50)
    propuestos = {p["id_persona"] for p in propuesta["propuesta"]}
    assert propuestos == {propio}, "el muestreo solo ve al panel"


# ── R3.13.c · Resolución de individuos ───────────────────────────────

def test_quien_ya_existe_se_reutiliza_y_conserva_sus_membresias(
    ctx, conn_boveda, carga
):
    """Caso borde del spec: una persona del archivo que ya es panelista."""
    panel = paneles.crear(conn_boveda, "Panel Nacional")
    ya_estaba = personas.alta(conn_boveda, {
        "persona": {"documento": "10-1", "nombre": "Ana Externa"},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico"),
    })["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], ya_estaba)

    creacion, _ = _crear_y_cargar(ctx, carga)

    assert creacion["resumen"]["reutilizados"] == 1
    assert creacion["resumen"]["creados"] == 1, "solo Beto es nuevo"
    membresia = db.una(
        ctx.boveda,
        "select estado from membresia where panel_id = %s and id_persona = %s",
        (panel["id"], ya_estaba))
    assert membresia["estado"] == "activo", "su membresía no se toca"


def test_los_demograficos_del_archivo_completan_la_ficha(ctx, carga):
    """R3.9.d aplica igual: se completa lo vacío y se traduce el código."""
    _, resultado = _crear_y_cargar(ctx, carga)

    ana = db.una(
        ctx.boveda, "select sexo, nombre from persona where documento = %s", ("10-1",))
    assert ana["sexo"] == "F", "«2» en el archivo, «Femenino» en la etiqueta"
    # Solo se «excluye» lo que venía declarado como pregunta: NOM y DOC ni
    # siquiera llegaron a la lista, SEXO sí y se sacó.
    assert resultado["excluidas_por_demografica"] == ["SEXO"]


def test_los_demograficos_no_llegan_al_store_semantico(ctx, carga):
    """El guardrail de R1.6, en el flujo nuevo."""
    _, resultado = _crear_y_cargar(ctx, carga)

    textos = [
        f["texto_embebido"] for f in db.todas(
            ctx.semantica,
            """
            select r.texto_embebido from respuesta r
              join pregunta p on p.id = r.pregunta_id
              join cuestionario c on c.id = p.cuestionario_id
             where c.ref_estudio = %s
            """, (carga["ref_estudio"],))
    ]
    assert textos, "sin respuestas ingestadas no se está probando nada"
    for prohibido in ("Ana Externa", "Beto Externo", "10-1", "Femenino", "Sexo"):
        assert not any(prohibido in t for t in textos), (
            f"«{prohibido}» llegó al store semántico")


# ── R3.13.d · Consentimiento, con la obligación invertida ────────────

def test_sin_uso_semantico_no_se_crea_la_persona(ctx, carga):
    """La finalidad obligatoria de este flujo: es la base de lo único que se
    va a hacer con estos datos."""
    creacion, _ = _crear_y_cargar(ctx, carga)

    assert creacion["resumen"]["sin_consentimiento"] == 1
    assert creacion["sin_consentimiento"][0]["id_en_origen"] == "o-3"
    assert db.una(
        ctx.boveda, "select 1 from persona where documento = %s", ("10-3",)) is None


def test_sin_consentimiento_de_contacto_la_persona_se_crea_igual(ctx, carga):
    """La inversión respecto de R3.9: acá el contacto es opcional, porque a
    esta gente no se la va a convocar."""
    _crear_y_cargar(ctx, carga)

    beto = db.una(
        ctx.boveda, "select id_persona from persona where documento = %s", ("10-2",))
    assert beto is not None

    finalidades = {
        f["finalidad"] for f in db.todas(
            ctx.boveda,
            "select finalidad from consentimiento "
            " where id_persona = %s and estado = 'vigente'",
            (beto["id_persona"],))
    }
    assert finalidades == {"uso_semantico"}


def test_quien_no_consintio_el_contacto_no_puede_ser_convocado(
    ctx, conn_boveda, carga
):
    """El gate de R1.3 sigue intacto: la carga no habilita a contactar."""
    _crear_y_cargar(ctx, carga)
    beto = str(db.una(
        conn_boveda, "select id_persona from persona where documento = %s",
        ("10-2",))["id_persona"])

    panel = paneles.crear(conn_boveda, "Panel")
    paneles.agregar_miembro(conn_boveda, panel["id"], beto)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    convocatoria = encuestas.convocar(
        conn_boveda, encuesta["id"], todo_el_panel=True)

    assert beto in convocatoria["sin_consentimiento"]
    assert convocatoria["convocados_total"] == 0


def test_declarar_solo_el_uso_semantico_alcanza(ctx, carga):
    """El contacto es opcional: no declararlo no rechaza la carga."""
    creacion, _ = _crear_y_cargar(
        ctx, carga,
        evidencia={"uso_semantico": {"variable": "CONS_SEM",
                                     "valor_afirmativo": "1",
                                     "version_texto": VERSION}})

    assert creacion["resumen"]["creados"] == 2


def test_no_declarar_el_uso_semantico_rechaza_la_carga(ctx, carga):
    """Es la obligatoria: sin ella la importación no arranca."""
    with pytest.raises(DatosInvalidos, match="uso_semantico"):
        _crear_y_cargar(
            ctx, carga,
            evidencia={"contacto_participacion": {"variable": "CONS_CONT",
                                                  "valor_afirmativo": "1",
                                                  "version_texto": VERSION}})


# ── R3.13.b · Idempotencia ───────────────────────────────────────────

def test_recargar_el_mismo_archivo_no_duplica_nada(ctx, carga):
    _crear_y_cargar(ctx, carga)
    personas_antes = db.una(ctx.boveda, "select count(*)::int as n from persona")["n"]

    _crear_y_cargar(ctx, carga)

    assert db.una(ctx.boveda, "select count(*)::int as n from persona")["n"] == personas_antes
    preguntas = db.una(
        ctx.semantica,
        "select count(*)::int as n from pregunta p "
        "  join cuestionario c on c.id = p.cuestionario_id where c.ref_estudio = %s",
        (carga["ref_estudio"],))
    respuestas = db.una(
        ctx.semantica,
        """
        select count(*)::int as n from respuesta r
          join pregunta p on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
         where c.ref_estudio = %s
        """, (carga["ref_estudio"],))
    assert preguntas["n"] == 1, "SEXO es demográfica: solo P1"
    assert respuestas["n"] == 2


# ── R3.13.e · Visibilidad y camino a panel ───────────────────────────

def test_se_pueden_filtrar_los_que_no_tienen_panel(ctx, conn_boveda, carga):
    panel = paneles.crear(conn_boveda, "Panel Nacional")
    propio = personas.alta(conn_boveda, {
        "persona": {"documento": "1-1", "nombre": "Panelista propio"},
        "consentimientos": consentimientos("contacto_participacion"),
    })["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], propio)

    _crear_y_cargar(ctx, carga)

    todos = personas.listar(ctx.boveda)
    solos = personas.listar(ctx.boveda, sin_panel=True)

    assert todos["total"] == 3
    assert solos["total"] == 2
    assert propio not in {i["id_persona"] for i in solos["items"]}
    assert all(i["paneles"] == 0 for i in solos["items"])


def test_despues_se_los_puede_incorporar_a_un_panel(ctx, conn_boveda, carga):
    """El camino previsto: cargar → consultar → crear panel con R3.11."""
    _crear_y_cargar(ctx, carga)
    ids = [i["id_persona"] for i in personas.listar(ctx.boveda, sin_panel=True)["items"]]

    nuevo = paneles.desde_consulta(
        conn_boveda, "Panel del ómnibus",
        {"items": [{"id_persona": i} for i in ids]},
        definicion={"desde": "carga"})

    assert nuevo["altas"] == 2
    assert personas.listar(ctx.boveda, sin_panel=True)["total"] == 0


# ── No regresión ─────────────────────────────────────────────────────

def test_la_ingesta_desde_encuesta_sigue_creando_membresia_y_participacion(
    ctx, conn_boveda
):
    """R3.13 no toca el otro camino."""
    panel = paneles.crear(conn_boveda, "Panel")
    persona = personas.alta(conn_boveda, {
        "persona": {"documento": "7-7", "nombre": "Panelista"},
        "consentimientos": consentimientos("contacto_participacion", "uso_semantico"),
        "origen": "dooblo", "id_en_origen": "R-007",
    })["id_persona"]
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")

    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"],
        [{"codigo": "P1", "texto": "¿Qué bebida prefiere?", "tipo": "abierta",
          "orden": 1}],
        [{"id_en_origen": "R-007", "P1": "fernet"}],
        columna_id="id_en_origen", origen="dooblo", proveedor=ctx.embeddings)

    assert resultado["membresias_nuevas"] == 1
    assert resultado["participaciones_nuevas"] == 1
    assert db.una(
        ctx.boveda,
        "select 1 from membresia where panel_id = %s and id_persona = %s",
        (panel["id"], persona)) is not None
