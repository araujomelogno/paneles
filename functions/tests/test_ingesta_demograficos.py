"""Addendum a R3.9 — Variables demográficas y códigos por tipo.

Si el archivo trae `SEXO`, `EDAD` o `LOCALIDAD`, hasta acá se precargaban
como variables cualquiera y terminaban embebidas como «Sexo → Femenino».
Eso espeja los segmentadores al store semántico por la puerta de atrás, que
es lo que el PRD descarta: quedan autoritativos en la bóveda para no sumar
cuasi-identificadores del lado limpio. Y no se gana nada: el filtro
demográfico ya se resuelve en la bóveda.

Corre contra los dos stores reales.
"""

import pytest

from panel_api import db, encuestas, paneles, personas, sav
from panel_api.errores import DatosInvalidos

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Whisky"}, "orden": 1},
    {"codigo": "SEXO", "texto": "Sexo", "tipo": "cerrada",
     "opciones": {"1": "Masculino", "2": "Femenino"}, "orden": 2},
    {"codigo": "LOCALIDAD", "texto": "Localidad", "tipo": "abierta", "orden": 3},
    {"codigo": "EDAD", "texto": "Edad", "tipo": "numerica", "orden": 4},
]

DEMOGRAFICAS = {"SEXO": "sexo", "LOCALIDAD": "localidad", "EDAD": sav.SOLO_EXCLUIR}


def _panelista(conn, documento, id_en_origen, datos=None):
    return personas.alta(
        conn,
        {
            "persona": {"documento": documento, "nombre": f"Panelista {documento}",
                        **(datos or {})},
            "consentimientos": consentimientos(*AMBAS),
            "origen": "dooblo",
            "id_en_origen": id_en_origen,
        },
    )["id_persona"]


@pytest.fixture
def ola(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel bebidas")
    persona = _panelista(conn_boveda, "1-1", "R-001")
    paneles.agregar_miembro(conn_boveda, panel["id"], persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1", "2026-03-01")
    return {"panel": panel, "encuesta": encuesta, "persona": persona}


def _ingestar(ctx, ola, filas, demograficas=DEMOGRAFICAS, preguntas=None):
    return encuestas.ingestar(
        ctx.boveda, ctx.semantica, ola["encuesta"]["id"],
        preguntas if preguntas is not None else PREGUNTAS, filas,
        columna_id="id_en_origen", origen="dooblo",
        proveedor=ctx.embeddings, demograficas=demograficas,
    )


FILA = {"id_en_origen": "R-001", "P1": "1", "SEXO": "2",
        "LOCALIDAD": "Montevideo", "EDAD": "38"}


def _textos_del_store(conn_semantica, ref_estudio):
    return [
        f["texto_embebido"]
        for f in db.todas(
            conn_semantica,
            """
            select r.texto_embebido
              from respuesta r
              join pregunta p on p.id = r.pregunta_id
              join cuestionario c on c.id = p.cuestionario_id
             where c.ref_estudio = %s
            """,
            (ref_estudio,),
        )
    ]


# ── R3.9.d · No llegan al store semántico ────────────────────────────

def test_una_variable_demografica_no_genera_pregunta_ni_respuesta_ni_vector(
    ctx, ola
):
    """El punto del addendum. Es el guardrail de privacidad, no una
    preferencia de presentación."""
    resultado = _ingestar(ctx, ola, [FILA])

    ref = ola["encuesta"]["ref_estudio"]
    codigos = [
        f["codigo"] for f in db.todas(
            ctx.semantica,
            "select p.codigo from pregunta p join cuestionario c on c.id = p.cuestionario_id"
            " where c.ref_estudio = %s", (ref,))
    ]
    assert codigos == ["P1"], "solo la pregunta del estudio"

    textos = _textos_del_store(ctx.semantica, ref)
    assert textos, "sin respuestas ingestadas no se está probando nada"
    for prohibido in ("Femenino", "Montevideo", "Sexo", "Localidad", "Edad"):
        assert not any(prohibido in t for t in textos), (
            f"«{prohibido}» se espejó al store semántico")

    assert resultado["respuestas_escritas"] == 1


def test_el_resultado_dice_que_variables_se_excluyeron(ctx, ola):
    """La exclusión tiene que ser visible: una variable que desaparece en
    silencio es indistinguible de una que se olvidaron de cargar."""
    resultado = _ingestar(ctx, ola, [FILA])
    assert resultado["excluidas_por_demografica"] == ["EDAD", "LOCALIDAD", "SEXO"]


def test_sin_marcarlas_se_embeben_como_cualquier_variable(ctx, ola):
    """El contraejemplo: es lo que pasaba antes, y lo que pasa si nadie
    marca nada. Sirve para que la prueba de arriba no pase sola."""
    _ingestar(ctx, ola, [FILA], demograficas=None)

    textos = _textos_del_store(ctx.semantica, ola["encuesta"]["ref_estudio"])
    assert any("Femenino" in t for t in textos)


# ── R3.9.d · Efecto sobre la bóveda ──────────────────────────────────

def test_un_campo_vacio_de_la_boveda_se_completa_con_el_archivo(ctx, ola):
    antes = personas.ficha(ctx.boveda, ola["persona"])["persona"]
    assert antes["sexo"] is None and antes["localidad"] is None

    resultado = _ingestar(ctx, ola, [FILA])

    despues = personas.ficha(ctx.boveda, ola["persona"])["persona"]
    # «2» en el archivo, «Femenino» en los value labels, «F» en la bóveda:
    # es lo que espera `v_demografia` y lo que ofrece el alta manual.
    assert despues["sexo"] == "F"
    assert despues["localidad"] == "Montevideo"
    assert resultado["demograficos_completados"] == 2


def test_un_campo_ya_cargado_con_otro_valor_no_se_pisa(ctx, conn_boveda):
    """El archivo de un estudio no es autoridad sobre la ficha del panelista:
    puede traer un dato viejo, mal tipeado o de otra persona."""
    panel = paneles.crear(conn_boveda, "Panel")
    persona = _panelista(
        conn_boveda, "2-2", "R-002", {"localidad": "Salto", "sexo": "F"})
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    ola = {"panel": panel, "encuesta": encuesta, "persona": persona}

    resultado = _ingestar(
        ctx, ola,
        [{"id_en_origen": "R-002", "P1": "1", "SEXO": "M",
          "LOCALIDAD": "Montevideo", "EDAD": "40"}])

    ficha = personas.ficha(ctx.boveda, persona)["persona"]
    assert ficha["localidad"] == "Salto", "no se pisa"
    assert ficha["sexo"] == "F"

    campos = {d["campo"] for d in resultado["discrepancias_demograficas"]}
    assert campos == {"localidad", "sexo"}
    discrepancia = next(
        d for d in resultado["discrepancias_demograficas"] if d["campo"] == "localidad")
    assert discrepancia["en_boveda"] == "salto"
    assert discrepancia["en_el_archivo"] == "montevideo"
    assert discrepancia["id_persona"] == persona


def test_el_mismo_valor_escrito_distinto_no_es_una_discrepancia(ctx, conn_boveda):
    """Si cada « Montevideo» con un espacio de más fuera una discrepancia, el
    informe se llenaría de ruido y nadie lo miraría."""
    panel = paneles.crear(conn_boveda, "Panel")
    persona = _panelista(conn_boveda, "3-3", "R-003", {"localidad": "Montevideo"})
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")

    resultado = _ingestar(
        ctx, {"panel": panel, "encuesta": encuesta, "persona": persona},
        [{"id_en_origen": "R-003", "P1": "1", "LOCALIDAD": " montevideo "}])

    assert resultado["discrepancias_demograficas"] == []


def test_una_variable_marcada_no_guardar_no_toca_la_boveda(ctx, ola):
    """`EDAD` no tiene dónde ir: la bóveda guarda fecha de nacimiento y
    deriva el tramo. Igual hay que poder sacarla del store semántico."""
    _ingestar(ctx, ola, [FILA])

    ficha = personas.ficha(ctx.boveda, ola["persona"])["persona"]
    assert "38" not in str(ficha.values())
    textos = _textos_del_store(ctx.semantica, ola["encuesta"]["ref_estudio"])
    assert not any("38" in t for t in textos)


# ── R3.9.d · Validación del marcado ──────────────────────────────────

def test_un_campo_que_no_existe_en_la_boveda_se_rechaza():
    with pytest.raises(DatosInvalidos, match="no es un campo demográfico"):
        sav.normalizar_demograficas({"SEXO": "estado_civil"})


def test_dos_variables_para_el_mismo_campo_se_rechazan():
    """Cuál gana es una decisión, no un detalle de implementación."""
    with pytest.raises(DatosInvalidos, match="mismo campo"):
        sav.normalizar_demograficas({"LOC1": "localidad", "LOC2": "localidad"})


def test_dos_variables_pueden_ser_no_guardar_a_la_vez():
    marcado = sav.normalizar_demograficas(
        {"EDAD": sav.SOLO_EXCLUIR, "NSE": sav.SOLO_EXCLUIR})
    assert marcado == {"EDAD": sav.SOLO_EXCLUIR, "NSE": sav.SOLO_EXCLUIR}


def test_marcar_una_variable_que_no_esta_en_el_archivo_se_detecta():
    with pytest.raises(DatosInvalidos, match="no están en el archivo"):
        sav.normalizar_demograficas({"SEXO": "sexo"}, {"P1", "P2"})


def test_el_mapeo_a_campos_deja_afuera_las_que_no_se_guardan():
    mapeo = sav.mapeo_por_campo(
        {"SEXO": "sexo", "EDAD": sav.SOLO_EXCLUIR, "NOM": "nombre"})
    assert mapeo == {"sexo": "SEXO", "nombre": "NOM"}


# ── R3.9.d · Sugerencia, nunca automática ────────────────────────────

def test_el_analisis_sugiere_pero_no_decide(tmp_path):
    """Una variable no se excluye del estudio sin que alguien lo confirme:
    lo que vuelve es una sugerencia y el aviso que lo dice."""
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")

    ruta = tmp_path / "campo.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({"SEXO": [1.0], "EDAD": [38.0], "P1": [1.0]}),
        str(ruta),
        column_labels=["Sexo", "Edad en años cumplidos", "¿Qué bebida prefiere?"],
    )

    analisis = sav.analizar(ruta)
    sugeridas = {s["codigo"]: s["campo"] for s in analisis["demograficas_sugeridas"]}

    assert sugeridas == {"SEXO": "sexo", "EDAD": sav.SOLO_EXCLUIR}
    assert any(a["tipo"] == "demograficas_a_confirmar" for a in analisis["avisos"])
    # Y la propuesta sigue sin incluir nada por su cuenta.
    assert all(v["incluir"] is False for v in analisis["variables"])


def test_la_sugerencia_tambien_mira_el_label(tmp_path):
    """Muchos exports nombran las variables `V1`, `V2` y dejan el sentido
    solo en el variable label."""
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")

    ruta = tmp_path / "v.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({"V1": ["Montevideo"]}), str(ruta),
        column_labels=["Localidad de residencia"])

    analisis = sav.analizar(ruta)
    assert analisis["variables"][0]["demografica_sugerida"] == "localidad"


def test_una_variable_cualquiera_no_se_sugiere(tmp_path):
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")

    ruta = tmp_path / "p.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({"P1": [1.0]}), str(ruta),
        column_labels=["¿Qué bebida consume habitualmente?"])

    analisis = sav.analizar(ruta)
    assert analisis["variables"][0]["demografica_sugerida"] is None
    assert analisis["demograficas_sugeridas"] == []


# ── El código crudo no llega a la bóveda ─────────────────────────────

def test_el_codigo_de_spss_se_traduce_antes_de_guardarlo(ctx, ola):
    """Sin esto, `persona.sexo` se llena de «1» y «2», la composición por
    sexo queda inservible y el muestreo por cuota también — y nada falla,
    que es lo peor que puede pasar."""
    _ingestar(ctx, ola, [FILA])

    fila = db.una(
        ctx.boveda, "select sexo from persona where id_persona = %s",
        (ola["persona"],))
    assert fila["sexo"] == "F"
    assert fila["sexo"] != "2"


@pytest.mark.parametrize("etiqueta,esperado", [
    ("Femenino", "F"), ("femenina", "F"), ("Mujer", "F"), ("F", "F"),
    ("Masculino", "M"), ("Hombre", "M"), ("Varón", "M"), ("m", "M"),
    ("Otro", "X"), ("No binarie", "X"),
])
def test_las_formas_de_escribir_el_sexo_se_normalizan(etiqueta, esperado):
    assert sav.valor_demografico("sexo", "1", {"1": etiqueta}) == esperado


def test_mujer_no_se_confunde_con_masculino():
    """La razón de que la tabla sea explícita: adivinar por la primera letra
    manda a todas las mujeres a «M»."""
    assert sav.valor_demografico("sexo", "1", {"1": "Mujer"}) == "F"
    assert sav.valor_demografico("sexo", "2", {"2": "Masculino"}) == "M"


def test_un_sexo_que_no_se_reconoce_se_deja_como_vino():
    """Mejor que se vea raro en la ficha a que se lo etiquete mal."""
    assert sav.valor_demografico("sexo", "9", {"9": "Sin dato"}) == "Sin dato"


def test_una_variable_sin_value_labels_pasa_tal_cual():
    assert sav.valor_demografico("localidad", "Montevideo", None) == "Montevideo"
    assert sav.valor_demografico("localidad", " Salto ", {}) == "Salto"


# ── La ruta completa: el marcado es la única fuente del mapeo ────────

def test_por_la_ruta_el_marcado_reemplaza_al_mapeo_patronimico(ctx, actor, conn_boveda, tmp_path):
    """El marcado vale para los dos modos y es de donde sale el mapeo
    patronímico: el bloque aparte desapareció y no puede volver a aparecer
    por accidente."""
    import base64

    from panel_api import ruteo

    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")

    ruta = tmp_path / "campo.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({
            "ID": ["a1"], "NOM": ["Ana Pérez"], "DOC": ["111"],
            "SEXO": [2.0], "LOCALIDAD": ["Salto"], "EDAD": [38.0],
            "CONS": [1.0], "P1": [1.0],
        }),
        str(ruta),
        variable_value_labels={"SEXO": {1.0: "Masculino", 2.0: "Femenino"},
                               "P1": {1.0: "Fernet"}},
    )

    panel = paneles.crear(conn_boveda, "Panel de calle")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola de calle")
    version = "consentimiento-campo-2026-09"

    status, respuesta = ruteo.despachar(
        "POST", f"/encuestas/{encuesta['id']}/sav/ingesta",
        {
            "archivo_base64": base64.b64encode(ruta.read_bytes()).decode(),
            "columna_id": "ID",
            "origen": "calle",
            "modo": "crear_individuos",
            "panel_id": panel["id"],
            "preguntas": [
                {"codigo": "P1", "texto": "¿Qué bebida prefiere?",
                 "tipo": "cerrada", "opciones": {"1": "Fernet"}, "orden": 1},
                {"codigo": "SEXO", "texto": "Sexo", "tipo": "cerrada",
                 "opciones": {"1": "Masculino", "2": "Femenino"}, "orden": 2},
                {"codigo": "LOCALIDAD", "texto": "Localidad",
                 "tipo": "abierta", "orden": 3},
                {"codigo": "NOM", "texto": "Nombre", "tipo": "abierta", "orden": 4},
                {"codigo": "DOC", "texto": "Documento", "tipo": "abierta", "orden": 5},
                {"codigo": "EDAD", "texto": "Edad", "tipo": "numerica", "orden": 6},
            ],
            "demograficas": {
                "NOM": "nombre", "DOC": "documento", "SEXO": "sexo",
                "LOCALIDAD": "localidad", "EDAD": sav.SOLO_EXCLUIR,
            },
            "evidencia_consentimiento": {
                "contacto_participacion": {
                    "variable": "CONS", "valor_afirmativo": "1",
                    "version_texto": version},
                "uso_semantico": {
                    "variable": "CONS", "valor_afirmativo": "1",
                    "version_texto": version},
            },
        },
        {}, actor("operaciones"), ctx,
    )

    assert status == 200
    assert respuesta["creacion_de_individuos"]["resumen"]["creados"] == 1

    # La persona quedó con sus demográficos, ya traducidos.
    fila = db.una(
        ctx.boveda,
        "select nombre, documento, sexo, localidad from persona limit 1")
    assert fila["nombre"] == "Ana Pérez"
    assert fila["documento"] == "111"
    assert fila["sexo"] == "F"
    assert fila["localidad"] == "Salto"

    # Y del lado semántico solo la pregunta del estudio.
    codigos = [
        f["codigo"] for f in db.todas(
            ctx.semantica,
            "select p.codigo from pregunta p join cuestionario c on c.id = p.cuestionario_id"
            " where c.ref_estudio = %s", (encuesta["ref_estudio"],))
    ]
    assert codigos == ["P1"]
    assert set(respuesta["excluidas_por_demografica"]) == {
        "NOM", "DOC", "SEXO", "LOCALIDAD", "EDAD"}
