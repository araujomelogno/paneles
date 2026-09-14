"""Bloque 3C — fricción operativa: R3.9, R3.10 y R3.11.

El `.sav` de las pruebas se fabrica acá con `pyreadstat`, que es la misma
librería que lo lee: un archivo binario versionado en el repo se desactualiza
y nadie se entera. Se le meten a propósito los defectos que la spec enumera
como casos borde —variable labels vacías y truncadas, un id repetido, alguien
que ya existe en la bóveda— porque son los que importan.
"""

import base64
import csv
import io
import pathlib
import tempfile

import pytest

from panel_api import consultas, db, encuestas, paneles, personas, sav
from panel_api.errores import DatosInvalidos

VERSION = "consentimiento-2026-01"
ESCALA = {1.0: "Nada", 2.0: "Poco", 3.0: "Mucho"}


# ── El archivo de prueba ────────────────────────────────────────────

@pytest.fixture
def archivo_sav(tmp_path):
    """Un `.sav` con los defectos típicos de un export de campo real."""
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")

    datos = pandas.DataFrame({
        "ID":   ["a1", "a2", "a3", "a4"],
        "NOM":  ["Ana Pérez", "Beto Díaz", "Ana Pérez", "Zoe Sin"],
        "DOC":  ["111", "222", "", "444"],
        "FNAC": ["1990-01-01", "1985-05-05", "1990-01-01", "1975-03-03"],
        "MAIL": ["ana@x.uy", "beto@x.uy", "", "zoe@x.uy"],
        # R3.9 — la evidencia de consentimiento que trae el propio archivo.
        # CONS1 cubre el contacto; CONS2, el uso semántico. Beto consiente el
        # contacto y no el uso semántico; Zoe no consiente nada.
        "CONS1": [1.0, 1.0, 1.0, 2.0],
        "CONS2": [1.0, 2.0, 1.0, 2.0],
        "P1":   [1.0, 2.0, 1.0, 1.0],
        "P2":   ["me encanta el fernet", "lo detesto", "ni fu ni fa", "no sé"],
        "P5_1": [3.0, 3.0, 1.0, 2.0],
        "P5_2": [3.0, 1.0, 2.0, 2.0],
        "P5_3": [3.0, 2.0, 3.0, 2.0],
        "P5_4": [3.0, 3.0, 1.0, 2.0],
    })
    ruta = tmp_path / "campo.sav"
    pyreadstat.write_sav(
        datos, str(ruta),
        column_labels=[
            "Identificador de la plataforma de campo",
            "Nombre completo del entrevistado",
            "Documento de identidad",
            "Fecha de nacimiento",
            "Correo electrónico",
            "¿Autoriza que lo contactemos para participar del panel?",
            "¿Autoriza el uso de sus respuestas en otros estudios?",
            "¿Qué bebida preferís tomar con amigos?",
            None,                       # sin variable label: el caso feo
            "Acuerdo 1", "Acuerdo 2", "Acuerdo 3", "Acuerdo 4",
        ],
        variable_value_labels={
            "P1": {1.0: "Fernet", 2.0: "Whisky"},
            "CONS1": {1.0: "Sí", 2.0: "No"},
            "CONS2": {1.0: "Sí", 2.0: "No"},
            **{f"P5_{i}": dict(ESCALA) for i in range(1, 5)},
        },
        variable_measure={
            **{c: "nominal" for c in ("ID", "NOM", "DOC", "FNAC", "MAIL",
                                      "CONS1", "CONS2", "P1", "P2")},
            **{f"P5_{i}": "scale" for i in range(1, 5)},
        },
    )
    return ruta


# ── R3.9 · Análisis del archivo ─────────────────────────────────────

def test_precarga_codigos_textos_tipos_y_etiquetas(archivo_sav):
    analisis = sav.analizar(archivo_sav)

    por_codigo = {v["codigo"]: v for v in analisis["variables"]}
    assert analisis["filas"] == 4
    assert por_codigo["P1"]["texto"] == "¿Qué bebida preferís tomar con amigos?"
    assert por_codigo["P1"]["tipo"] == "cerrada"
    assert por_codigo["P1"]["opciones"] == {"1": "Fernet", "2": "Whisky"}
    assert por_codigo["P5_1"]["tipo"] == "escala"
    assert por_codigo["P2"]["tipo"] == "abierta"


def test_nada_se_incluye_hasta_que_alguien_lo_marque(archivo_sav):
    """R3.9 — «todo queda editable antes de confirmar»."""
    analisis = sav.analizar(archivo_sav)

    assert all(v["incluir"] is False for v in analisis["variables"])
    assert "propuesta" in analisis["nota"]


def test_una_variable_sin_label_se_marca_en_vez_de_pasar_desapercibida(archivo_sav):
    """El texto es lo que se vectoriza: si queda «P2», la consulta semántica
    sobre ese estudio no sirve."""
    analisis = sav.analizar(archivo_sav)

    p2 = next(v for v in analisis["variables"] if v["codigo"] == "P2")
    assert p2["texto"] == "P2", "sin label, el texto cae al código"
    assert p2["texto_del_archivo"] is None
    assert any("sin variable label" in a for a in p2["avisos"])
    assert analisis["avisos"], "y el resumen lo levanta"


def test_un_label_truncado_por_spss_se_marca(tmp_path):
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")

    ruta = tmp_path / "truncado.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({"P1": [1.0]}), str(ruta),
        column_labels=["x" * sav.LARGO_TRUNCADO_SPSS],
    )

    variable = sav.analizar(ruta)["variables"][0]
    assert any("truncado" in a for a in variable["avisos"])


def test_las_candidatas_a_id_se_sugieren_sin_decidir(archivo_sav):
    analisis = sav.analizar(archivo_sav)
    assert "ID" in analisis["candidatas_a_id"]


def test_analizar_no_escribe_nada(archivo_sav, conn_boveda):
    sav.analizar(archivo_sav)
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 0


# ── R3.9 · Modo «crear los individuos» ──────────────────────────────

MAPEO = {"nombre": "NOM", "documento": "DOC", "fecha_nacimiento": "FNAC",
         "email": "MAIL"}

VERSION_CAMPO = "consentimiento-campo-2026-09"
EVIDENCIA = {
    "contacto_participacion": {
        "variable": "CONS1", "valor_afirmativo": "1",
        "version_texto": VERSION_CAMPO,
    },
    "uso_semantico": {
        "variable": "CONS2", "valor_afirmativo": "1",
        "version_texto": VERSION_CAMPO,
    },
}


def test_el_dedup_de_r12_se_aplica_y_los_ambiguos_van_a_revision(
    conn_boveda, archivo_sav
):
    """R3.9 — «la ingesta no puede crear duplicados que el alta manual habría
    evitado»."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()
    filas = sav.filas_de(archivo_sav)

    resultado = sav.crear_individuos(
        conn_boveda, filas, MAPEO, origen="dooblo", columna_id="ID",
        evidencia_consentimiento=EVIDENCIA, panel_id=panel["id"],
    )

    assert resultado["resumen"]["creados"] == 2
    assert resultado["resumen"]["en_revision"] == 1
    revision = resultado["en_revision"][0]
    assert revision["id_en_origen"] == "a3"
    assert revision["motivo"] == "nombre_fecha_nacimiento"
    assert revision["candidatos"], "tiene que decir con quién colisionó"
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 2, (
        "el ambiguo no se fusiona ni se crea: espera decisión humana"
    )


def test_quien_ya_existe_se_reutiliza(conn_boveda, archivo_sav):
    panel = paneles.crear(conn_boveda, "SAV")
    ya = personas.alta(conn_boveda, {
        "persona": {"nombre": "Beto Díaz", "documento": "222"},
        "consentimientos": [{"finalidad": "contacto_participacion",
                             "version_texto": VERSION}],
        "panel_id": panel["id"],
    })
    conn_boveda.commit()

    resultado = sav.crear_individuos(
        conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        panel_id=panel["id"],
    )

    reutilizado = next(r for r in resultado["reutilizados"])
    assert reutilizado["id_persona"] == ya["id_persona"]
    assert reutilizado["motivo"] == "documento"


def test_sin_declarar_la_evidencia_no_se_importa(conn_boveda, archivo_sav):
    """R3.9 — la base legal viaja en el archivo, y declararla es obligatorio.

    No es un default que se pueda omitir: sin la declaración la importación
    se rechaza antes de leer una sola fila.
    """
    filas = sav.filas_de(archivo_sav)

    with pytest.raises(DatosInvalidos, match="evidencia de consentimiento"):
        sav.crear_individuos(conn_boveda, filas, MAPEO, origen="dooblo",
                             columna_id="ID", evidencia_consentimiento=None)

    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 0


@pytest.mark.parametrize("evidencia, falla", [
    ({"contacto_participacion": {"variable": "CONS1", "valor_afirmativo": "1",
                                 "version_texto": "v"}},
     "Falta declarar"),                                    # falta una finalidad
    ({**{f: {"variable": "", "valor_afirmativo": "1", "version_texto": "v"}
         for f in sav.FINALIDADES_EVIDENCIABLES}},
     "Falta la variable"),
    ({**{f: {"variable": "CONS1", "valor_afirmativo": "", "version_texto": "v"}
         for f in sav.FINALIDADES_EVIDENCIABLES}},
     "valor afirmativo"),
    ({**{f: {"variable": "CONS1", "valor_afirmativo": "1", "version_texto": ""}
         for f in sav.FINALIDADES_EVIDENCIABLES}},
     "versión del texto"),
])
def test_una_declaracion_incompleta_se_rechaza(evidencia, falla):
    with pytest.raises(DatosInvalidos, match=falla):
        sav.normalizar_evidencia(evidencia)


def test_una_variable_de_consentimiento_que_no_esta_en_el_archivo_se_detecta(
    conn_boveda, archivo_sav
):
    """Un typo en el código de la variable haría que ninguna fila evidencie
    consentimiento y que la importación termine con cero altas sin explicar
    por qué. Se detecta antes de escribir nada."""
    evidencia = {
        **EVIDENCIA,
        "contacto_participacion": {**EVIDENCIA["contacto_participacion"],
                                   "variable": "CONS_QUE_NO_EXISTE"},
    }
    with pytest.raises(DatosInvalidos, match="no están en el archivo"):
        sav.crear_individuos(
            conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
            columna_id="ID", evidencia_consentimiento=evidencia,
        )


def test_una_misma_variable_puede_cubrir_las_dos_finalidades(conn_boveda,
                                                             archivo_sav):
    """El caso normal: una sola pregunta de consentimiento en el
    cuestionario. Declararla dos veces no es redundancia, es decir
    explícitamente que esa pregunta cubre las dos finalidades."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()
    una_sola = {
        finalidad: {"variable": "CONS1", "valor_afirmativo": "1",
                    "version_texto": VERSION_CAMPO}
        for finalidad in sav.FINALIDADES_EVIDENCIABLES
    }

    resultado = sav.crear_individuos(
        conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
        columna_id="ID", evidencia_consentimiento=una_sola,
        panel_id=panel["id"],
    )

    assert resultado["resumen"]["creados"] == 2      # Ana y Beto
    assert resultado["aviso_sin_uso_semantico"] is None, (
        "con una sola variable, quien consiente el contacto consiente las dos"
    )
    finalidades = {
        f["finalidad"] for f in db.todas(
            conn_boveda, "select distinct finalidad from consentimiento")
    }
    assert finalidades == set(sav.FINALIDADES_EVIDENCIABLES)


def test_la_persona_creada_nace_activa_y_con_su_consentimiento(conn_boveda,
                                                               archivo_sav):
    """El cambio de fondo: el archivo prueba la base legal, así que no queda
    nada pendiente de regularizar."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()

    resultado = sav.crear_individuos(
        conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        panel_id=panel["id"],
    )

    estados = {e["estado"] for e in db.todas(conn_boveda,
                                             "select estado from persona")}
    assert estados == {"activa"}, "ya no se crea nadie pendiente"

    ana = next(c for c in resultado["creados"] if c["id_en_origen"] == "a1")
    consentimientos = db.todas(
        conn_boveda,
        "select finalidad, version_texto, estado from consentimiento "
        " where id_persona = %s order by finalidad",
        (ana["id_persona"],),
    )
    assert [c["finalidad"] for c in consentimientos] == [
        "contacto_participacion", "uso_semantico"]
    assert {c["version_texto"] for c in consentimientos} == {VERSION_CAMPO}
    assert {c["estado"] for c in consentimientos} == {"vigente"}


def test_quien_no_consiente_el_contacto_no_se_crea(conn_boveda, archivo_sav):
    """La regla que cierra el agujero legal. Zoe (a4) dice que no: no entra a
    la bóveda, no se le registra alias, no queda nada a medias."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()

    resultado = sav.crear_individuos(
        conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        panel_id=panel["id"],
    )

    rechazada = resultado["sin_consentimiento"]
    assert [r["id_en_origen"] for r in rechazada] == ["a4"]
    assert rechazada[0]["variable"] == "CONS1"
    assert rechazada[0]["valor_en_el_archivo"] == "2"
    assert resultado["aviso_sin_consentimiento"]["personas"] == 1

    nombres = {p["nombre"] for p in db.todas(conn_boveda,
                                             "select nombre from persona")}
    assert "Zoe Sin" not in nombres
    assert db.una(
        conn_boveda,
        "select count(*)::int as n from alias_origen where id_en_origen = 'a4'",
    )["n"] == 0


def test_cada_finalidad_se_evalua_por_separado(conn_boveda, archivo_sav):
    """Beto (a2) consiente el contacto y no el uso semántico: entra al panel
    con un solo consentimiento, y se avisa."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()

    resultado = sav.crear_individuos(
        conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        panel_id=panel["id"],
    )

    beto = next(c for c in resultado["creados"] if c["id_en_origen"] == "a2")
    assert beto["consintio"] == {"contacto_participacion": True,
                                 "uso_semantico": False}
    finalidades = [
        f["finalidad"] for f in db.todas(
            conn_boveda,
            "select finalidad from consentimiento where id_persona = %s",
            (beto["id_persona"],))
    ]
    assert finalidades == ["contacto_participacion"]
    assert resultado["aviso_sin_uso_semantico"]["personas"] == 1


def test_quien_no_consiente_el_uso_semantico_no_se_ingesta(
    conn_boveda, conn_semantica, archivo_sav, proveedor
):
    """El aviso no alcanza: la consecuencia tiene que ser real. El gate de
    R1.3 deja las respuestas de Beto fuera del store semántico."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()
    filas = sav.filas_de(archivo_sav)
    creacion = sav.crear_individuos(
        conn_boveda, filas, MAPEO, origen="dooblo", columna_id="ID",
        evidencia_consentimiento=EVIDENCIA, panel_id=panel["id"],
    )
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola SAV")
    conn_boveda.commit()
    preguntas = [{"codigo": "P2", "texto": "¿Por qué la elige?",
                  "tipo": "abierta", "opciones": None, "orden": 1}]

    encuestas.ingestar(conn_boveda, conn_semantica, encuesta["id"], preguntas,
                       filas, columna_id="ID", origen="dooblo",
                       proveedor=proveedor)

    beto = next(c for c in creacion["creados"] if c["id_en_origen"] == "a2")
    ana = next(c for c in creacion["creados"] if c["id_en_origen"] == "a1")
    con_respuestas = {
        str(f["id_persona"]) for f in db.todas(
            conn_semantica, "select id_persona from individuo")
    }
    assert ana["id_persona"] in con_respuestas
    assert beto["id_persona"] not in con_respuestas, (
        "consintió el contacto pero no el uso semántico"
    )


def test_reimportar_el_mismo_archivo_no_duplica_consentimientos(conn_boveda,
                                                                archivo_sav):
    """`consentimiento.otorgar` agrega una fila cada vez a propósito, para no
    pisar el historial. Pero re-ingestar el mismo archivo no es un
    consentimiento nuevo: es el mismo dato otra vez, y llenar la tabla de
    filas idénticas haría ilegible el registro que sirve de prueba."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()
    filas = sav.filas_de(archivo_sav)

    primera = sav.crear_individuos(
        conn_boveda, filas, MAPEO, origen="dooblo", columna_id="ID",
        evidencia_consentimiento=EVIDENCIA, panel_id=panel["id"])
    cuantos = db.una(
        conn_boveda, "select count(*)::int as n from consentimiento")["n"]

    segunda = sav.crear_individuos(
        conn_boveda, filas, MAPEO, origen="dooblo", columna_id="ID",
        evidencia_consentimiento=EVIDENCIA, panel_id=panel["id"])

    assert segunda["resumen"]["creados"] == 0
    assert segunda["resumen"]["reutilizados"] == primera["resumen"]["creados"]
    assert db.una(
        conn_boveda, "select count(*)::int as n from consentimiento")["n"] == cuantos


def test_el_si_del_archivo_se_compara_sin_distinguir_mayusculas(conn_boveda):
    """En un export de campo conviven «Si», «SI » y «sí» para la misma
    respuesta. Rechazar a alguien por eso sería un error de importación
    disfrazado de falta de consentimiento."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()
    evidencia = {
        f: {"variable": "CONS", "valor_afirmativo": "sí",
            "version_texto": VERSION_CAMPO}
        for f in sav.FINALIDADES_EVIDENCIABLES
    }
    filas = [
        {"ID": "x1", "NOM": "Uno", "DOC": "d1", "CONS": "Sí"},
        {"ID": "x2", "NOM": "Dos", "DOC": "d2", "CONS": "SÍ "},
        {"ID": "x3", "NOM": "Tres", "DOC": "d3", "CONS": "No"},
    ]

    resultado = sav.crear_individuos(
        conn_boveda, filas, {"nombre": "NOM", "documento": "DOC"},
        origen="dooblo", columna_id="ID", evidencia_consentimiento=evidencia,
        panel_id=panel["id"],
    )

    assert resultado["resumen"]["creados"] == 2
    assert [r["id_en_origen"] for r in resultado["sin_consentimiento"]] == ["x3"]


def test_el_ambiguo_se_lleva_su_consentimiento_a_la_revision(conn_boveda,
                                                             archivo_sav):
    """Quien resuelve la revisión no tiene que volver al archivo para saber
    qué consintió esa persona."""
    import json

    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()

    sav.crear_individuos(
        conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        panel_id=panel["id"],
    )

    revision = db.una(conn_boveda, "select datos from alta_en_revision")
    datos = revision["datos"]
    if isinstance(datos, str):
        datos = json.loads(datos)
    finalidades = {c["finalidad"] for c in datos["consentimientos"]}
    assert finalidades == set(sav.FINALIDADES_EVIDENCIABLES)
    assert all(c["version_texto"] == VERSION_CAMPO
               for c in datos["consentimientos"])
    assert "CONS1" in datos["nota"]


def test_ya_no_se_crea_nadie_pendiente_de_consentimiento(conn_boveda,
                                                         archivo_sav):
    """El estado sigue existiendo para las personas creadas por la versión
    anterior del módulo, pero la ingesta ya no lo produce."""
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()

    sav.crear_individuos(
        conn_boveda, sav.filas_de(archivo_sav), MAPEO, origen="dooblo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        panel_id=panel["id"],
    )

    assert db.una(
        conn_boveda,
        "select count(*)::int as n from persona "
        " where estado = 'pendiente_consentimiento'",
    )["n"] == 0


def test_regularizar_sigue_estando_para_las_altas_viejas(conn_boveda):
    """Transitoria: las personas creadas antes de este cambio quedaron en
    `pendiente_consentimiento` y hay que poder sacarlas de ahí."""
    panel = paneles.crear(conn_boveda, "SAV")
    fila = db.una(
        conn_boveda,
        "insert into persona (nombre, documento, estado) "
        "values ('Vieja', 'v1', 'pendiente_consentimiento') returning id_persona",
    )
    conn_boveda.commit()
    id_persona = str(fila["id_persona"])

    sav.regularizar(conn_boveda, [id_persona], "contacto_participacion",
                    VERSION_CAMPO)

    assert db.una(
        conn_boveda, "select estado from persona where id_persona = %s",
        (id_persona,),
    )["estado"] == "activa"


def test_una_fila_sin_datos_suficientes_se_informa(conn_boveda):
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()
    filas = [{"ID": "z1", "NOM": "", "DOC": "", "MAIL": "",
              "CONS1": "1", "CONS2": "1"}]

    resultado = sav.crear_individuos(
        conn_boveda, filas, MAPEO, origen="dooblo", columna_id="ID",
        evidencia_consentimiento=EVIDENCIA, panel_id=panel["id"],
    )

    assert resultado["sin_datos_suficientes"] == ["z1"]
    assert resultado["resumen"]["creados"] == 0


def test_un_mapeo_a_un_campo_inexistente_se_rechaza(conn_boveda):
    with pytest.raises(DatosInvalidos, match="no existen en la bóveda"):
        sav.crear_individuos(
            conn_boveda, [], {"apodo": "NOM"}, origen="x", columna_id="ID",
            evidencia_consentimiento=EVIDENCIA,
        )


def test_sin_columna_de_id_no_se_ingesta(conn_boveda):
    with pytest.raises(DatosInvalidos, match="identifica a cada individuo"):
        sav.crear_individuos(conn_boveda, [], MAPEO, origen="x", columna_id="",
                             evidencia_consentimiento=EVIDENCIA)


# ── R3.9 · El guardrail de PII sigue valiendo ───────────────────────

def test_los_datos_patronimicos_del_sav_no_llegan_al_store_semantico(
    conn_boveda, conn_semantica, archivo_sav, proveedor
):
    """DoD del bloque 3C, y regla dura #1 de CLAUDE.md.

    Se ingesta el archivo entero —incluidas las variables patronímicas, que
    es el error que alguien va a cometer— y se revisa que del lado semántico
    no haya quedado ni un nombre, ni un documento, ni un correo.
    """
    panel = paneles.crear(conn_boveda, "SAV")
    conn_boveda.commit()
    filas = sav.filas_de(archivo_sav)
    sav.crear_individuos(conn_boveda, filas, MAPEO, origen="dooblo",
                         columna_id="ID", evidencia_consentimiento=EVIDENCIA,
                         panel_id=panel["id"])
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola SAV")
    conn_boveda.commit()

    analisis = sav.analizar(archivo_sav)
    preguntas = [
        {"codigo": v["codigo"], "texto": v["texto"], "tipo": v["tipo"],
         "opciones": v["opciones"], "orden": v["orden"]}
        for v in analisis["variables"]
        if v["codigo"] in ("P1", "P2", "P5_1", "P5_2", "P5_3", "P5_4")
    ]
    escritas = encuestas.ingestar(
        conn_boveda, conn_semantica, encuesta["id"], preguntas, filas,
        columna_id="ID", origen="dooblo", proveedor=proveedor,
    )

    textos = db.todas(
        conn_semantica,
        "select valor_texto, texto_embebido from respuesta",
    )
    # Sin esto la prueba pasa en vacío: si la ingesta no escribió nada, no
    # hay dónde buscar PII y el guardrail queda sin verificar. Pasó: la
    # llamada tenía `columna_id` y `origen` invertidos y esta prueba no se
    # enteró.
    assert escritas["respuestas_escritas"] > 0
    assert textos, "sin respuestas ingestadas no se está probando nada"

    todo = " ".join(
        f"{f['valor_texto']} {f['texto_embebido']}" for f in textos
    ).lower()
    for dato in ("ana pérez", "beto díaz", "111", "222", "ana@x.uy", "beto@x.uy",
                 "1990-01-01"):
        assert dato.lower() not in todo, f"se filtró «{dato}» al store semántico"
    # Y la auditoría en vivo del esquema, que es la garantía de fondo.
    from panel_api import semantica

    assert semantica.auditar_columnas(conn_semantica) == []


def test_una_variable_patronimica_declarada_como_pregunta_la_frena_el_guardrail(
    conn_semantica
):
    """Si alguien marca «NOM» como pregunta, el guardrail tiene que gritar
    antes de escribir, no después."""
    from panel_api import semantica

    with pytest.raises(Exception) as error:
        semantica.upsert_respuestas(conn_semantica, [{
            "individuo_id": 1, "pregunta_id": 1,
            "valor_texto": "Ana Pérez",
            "texto_embebido": "Nombre completo → Ana Pérez",
            "email": "ana@x.uy",
            "embedding": [0.0] * 1024,
        }])
    assert "pii" in str(error.value).lower() or "email" in str(error.value).lower()


# ── R3.10 · Exportación identificada ────────────────────────────────

REIDENTIFICACION = {
    "total": 2,
    "items": [
        {"id_persona": "11111111-1111-1111-1111-111111111111",
         "nombre": "Ana Pérez", "documento": "111", "email": "ana@x.uy",
         "celular": "099111", "contacto": None, "sexo": "F",
         "localidad": "Montevideo", "tramo_etario": "35-44"},
        {"id_persona": "22222222-2222-2222-2222-222222222222",
         "nombre": "Beto Díaz", "documento": "222", "email": "beto@x.uy",
         "celular": None, "contacto": None, "sexo": "M",
         "localidad": "Salto", "tramo_etario": "35-44"},
    ],
    "no_encontrados": [],
}

RESULTADO = {
    "items": [
        {"id_persona": "11111111-1111-1111-1111-111111111111", "puntaje": 0.91,
         "evidencias": [{"estudio": "Ola 1", "pregunta_codigo": "P2",
                         "valor_texto": "me encanta el fernet"}]},
        {"id_persona": "22222222-2222-2222-2222-222222222222", "puntaje": 0.77,
         "evidencias": []},
    ],
}


def _leer_csv(texto):
    lineas = [l for l in texto.splitlines() if not l.startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lineas))))


def test_el_csv_identificado_trae_los_campos_de_la_reidentificacion():
    salida = consultas.a_csv_identificado(REIDENTIFICACION, RESULTADO)

    filas = _leer_csv(salida)
    assert filas[0]["nombre"] == "Ana Pérez"
    assert filas[0]["documento"] == "111"
    assert filas[0]["tramo_etario"] == "35-44"
    assert filas[0]["puntaje"] == "0.91"
    assert "fernet" in filas[0]["evidencia"]


def test_el_csv_identificado_no_lleva_fecha_exacta_ni_observaciones():
    """R3.10 — la fecha de nacimiento exacta es un identificador fino y las
    observaciones son texto libre donde cae dato sensible."""
    salida = consultas.a_csv_identificado(REIDENTIFICACION, RESULTADO)

    encabezado = _leer_csv(salida)[0].keys()
    assert "fecha_nacimiento" not in encabezado
    assert "observaciones" not in encabezado
    assert set(consultas.CAMPOS_IDENTIFICADOS).isdisjoint(
        {"fecha_nacimiento", "observaciones"}
    )


def test_el_archivo_lleva_marca_visible_de_que_tiene_datos_personales():
    salida = consultas.a_csv_identificado(REIDENTIFICACION, RESULTADO)

    assert salida.splitlines()[0].startswith("#")
    assert "datos personales" in salida.splitlines()[0]
    assert "CON-DATOS-PERSONALES" in consultas.nombre_archivo_identificado()


def test_el_csv_seudonimizado_sigue_igual():
    """R3.10 — «se mantiene sin cambios y sigue siendo la opción por defecto»."""
    salida = consultas.a_csv(RESULTADO)

    encabezado = salida.splitlines()[0]
    assert encabezado == "id_persona,puntaje,evidencia"
    assert "Ana Pérez" not in salida
    assert not salida.startswith("#")


def test_exportar_sin_reidentificar_no_esta_disponible(ctx, actor):
    from panel_api import ruteo

    with pytest.raises(DatosInvalidos, match="ya reidentificado"):
        ruteo.despachar(
            "POST", "/consultas/csv-identificado",
            {"resultado": RESULTADO}, {}, actor("operaciones"), ctx,
        )


def test_exportar_queda_registrado_con_motivo_propio(ctx, actor):
    """R3.10 — «se registra con motivo propio (exportacion)», distinto de
    haberlo visto en pantalla."""
    from panel_api import ruteo

    status, respuesta = ruteo.despachar(
        "POST", "/consultas/csv-identificado",
        {"reidentificacion": REIDENTIFICACION, "resultado": RESULTADO},
        {}, actor("operaciones"), ctx,
    )

    assert status == 200
    assert respuesta["contiene_datos_personales"] is True
    registros = db.todas(
        ctx.boveda, "select id_persona, motivo, actor_uid from reidentificacion"
    )
    assert len(registros) == 2
    assert {r["motivo"] for r in registros} == {"exportacion"}
    assert {r["actor_uid"] for r in registros} == {"uid-operaciones"}


def test_exportar_no_vuelve_a_consultar_la_boveda(ctx, actor):
    """R3.10 — «se usa el resultado ya resuelto». Las personas del payload no
    existen en esta base: si el CSV sale igual, es que no fue a buscarlas."""
    from panel_api import ruteo

    assert db.una(ctx.boveda, "select count(*)::int as n from persona")["n"] == 0

    _, respuesta = ruteo.despachar(
        "POST", "/consultas/csv-identificado",
        {"reidentificacion": REIDENTIFICACION, "resultado": RESULTADO},
        {}, actor("operaciones"), ctx,
    )

    assert "Ana Pérez" in respuesta["csv"]


# ── R3.11 · Panel desde una consulta ────────────────────────────────

@pytest.fixture
def gente(conn_boveda):
    panel = paneles.crear(conn_boveda, "Origen")
    ids = []
    for i in range(3):
        alta = personas.alta(conn_boveda, {
            "persona": {"nombre": f"P{i}", "documento": f"d{i}", "sexo": "F",
                        "fecha_nacimiento": "1990-01-01"},
            "consentimientos": [{"finalidad": "contacto_participacion",
                                 "version_texto": VERSION}],
            "panel_id": panel["id"],
        })
        ids.append(alta["id_persona"])
    conn_boveda.commit()
    return panel["id"], ids


def _resultado(ids):
    return {"items": [{"id_persona": i, "puntaje": 0.9} for i in ids]}


def test_crear_un_panel_desde_una_consulta_da_de_alta_las_membresias(
    conn_boveda, gente, actor
):
    _, ids = gente

    nuevo = paneles.desde_consulta(
        conn_boveda, "Tomadores de fernet", _resultado(ids),
        definicion={"criterios": [{"tipo": "semantico", "texto": "fernet"}]},
        actor=actor("operaciones"),
    )

    assert nuevo["miembros"] == 3
    assert nuevo["altas"] == 3
    assert db.una(
        conn_boveda,
        "select count(*)::int as n from membresia where panel_id = %s",
        (nuevo["id"],),
    )["n"] == 3


def test_es_idempotente_con_quien_ya_era_miembro(conn_boveda, gente, actor):
    _, ids = gente
    primero = paneles.desde_consulta(
        conn_boveda, "Panel A", _resultado(ids), actor=actor("operaciones")
    )

    repetido = paneles.desde_consulta(
        conn_boveda, "Panel B", _resultado(ids), actor=actor("operaciones")
    )
    # Y volver a agregarlos al mismo panel tampoco duplica.
    for id_persona in ids:
        db.ejecutar(
            conn_boveda,
            "insert into membresia (panel_id, id_persona) values (%s, %s) "
            "on conflict (panel_id, id_persona) do nothing",
            (primero["id"], id_persona),
        )
    conn_boveda.commit()

    assert repetido["altas"] == 3
    assert db.una(
        conn_boveda,
        "select count(*)::int as n from membresia where panel_id = %s",
        (primero["id"],),
    )["n"] == 3


def test_el_panel_registra_de_donde_salio_su_composicion(conn_boveda, gente, actor):
    _, ids = gente
    definicion = {"criterios": [{"tipo": "semantico", "texto": "fernet"}],
                  "modo": "estricto"}

    nuevo = paneles.desde_consulta(
        conn_boveda, "Fernet", _resultado(ids), definicion=definicion,
        actor=actor("operaciones"),
    )

    fila = db.una(
        conn_boveda,
        "select origen, origen_definicion, creado_por from panel where id = %s",
        (nuevo["id"],),
    )
    assert fila["origen"] == "consulta"
    assert fila["origen_definicion"]["modo"] == "estricto"
    assert fila["creado_por"] == "uid-operaciones"


def test_materializar_una_consulta_semantica_deja_registro(conn_boveda, gente, actor):
    """R3.11 — «deja registro equivalente al de reidentificación»: fijar una
    lista de personas es una decisión sobre personas concretas."""
    _, ids = gente

    paneles.desde_consulta(
        conn_boveda, "Fernet", _resultado(ids),
        definicion={"criterios": [{"tipo": "semantico", "texto": "fernet"}]},
        actor=actor("operaciones"),
    )

    registros = db.todas(
        conn_boveda, "select motivo, contexto from reidentificacion"
    )
    assert len(registros) == 3
    assert {r["motivo"] for r in registros} == {"panel_desde_consulta"}


def test_sin_individuos_no_se_crea_un_panel_vacio(conn_boveda, actor):
    with pytest.raises(DatosInvalidos, match="ningún individuo"):
        paneles.desde_consulta(conn_boveda, "Vacío", {"items": []},
                               actor=actor("operaciones"))


def test_quien_no_puede_ser_convocado_es_miembro_igual_pero_se_avisa(
    conn_boveda, gente, actor
):
    """R3.11 — «la membresía puede existir pero no puede ser convocado (el
    gate de R1.3 sigue aplicando)»."""
    _, ids = gente
    db.ejecutar(
        conn_boveda,
        "update consentimiento set estado = 'retirado' where id_persona = %s",
        (ids[0],),
    )
    conn_boveda.commit()

    nuevo = paneles.desde_consulta(
        conn_boveda, "Con uno sin consentimiento", _resultado(ids),
        actor=actor("operaciones"),
    )

    assert nuevo["miembros"] == 3
    assert nuevo["no_convocables"] == [ids[0]]
    assert nuevo["aviso"]["personas"] == 1
    # El gate de verdad: convocar a todo el panel deja afuera a esa persona.
    encuesta = encuestas.crear(conn_boveda, nuevo["id"], "Ola")
    conn_boveda.commit()
    convocatoria = encuestas.convocar(conn_boveda, encuesta["id"],
                                      todo_el_panel=True)
    convocados = db.todas(
        conn_boveda,
        "select id_persona from participacion where encuesta_id = %s",
        (encuesta["id"],),
    )
    assert ids[0] not in [str(c["id_persona"]) for c in convocados]


def test_quien_se_dio_de_baja_entre_la_consulta_y_el_panel_se_informa(
    conn_boveda, gente, actor
):
    _, ids = gente
    fantasma = "99999999-9999-9999-9999-999999999999"

    nuevo = paneles.desde_consulta(
        conn_boveda, "Con un fantasma", _resultado(ids + [fantasma]),
        actor=actor("operaciones"),
    )

    assert nuevo["no_encontrados"] == [fantasma]
    assert nuevo["miembros"] == 3
