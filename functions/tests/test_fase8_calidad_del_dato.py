"""Fase 8 — calidad del dato semántico (`specs/SPEC_fase8.md`).

Las pruebas siguen el Definition of Done de la spec, bloque por bloque. El
archivo de la primera carga real no está en el repo —trae datos de 1.131
personas—, así que se arma uno con **su misma estructura**: la batería de
dicotómicas `var138O132x` con labels «Opción:Pregunta» y el par
`Unchecked`/`Checked`, una `EDAD` con etiquetas iguales al código, una
cerrada con un código sin traducir (`11427`) y uno de no respuesta, su
«Otro: especificar» con un teléfono adentro, una variable vacía y una casi
constante.

Hay una propiedad que atraviesa todo el archivo y conviene leer primero:
**nada se aplica solo**. El análisis propone; si la pantalla no manda la
decisión, la ingesta hace exactamente lo que hacía antes de la Fase 8.
"""

import base64

import pytest

from panel_api import (calidad_dato, db, diferida, encuestas, ingesta, paneles,
                       personas, reproceso, ruteo, sav, semantica)
from panel_api.errores import Conflicto

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")
PREGUNTA_BATERIA = ("Pensando en el ÚLTIMO mes, ¿has consumido alguno de estos "
                    "productos? Seleccione los que correpondan")
BATERIA = {
    "var138O1320": "Cigarrillos",
    "var138O1321": "Vaporizador",
    "var138O1322": "Tabaco para armar",
}
N = 24


def _valor_bateria(i, j):
    """Quién marcó qué. La persona 0 no marcó ninguna."""
    if i == 0:
        return 0.0
    return 1.0 if (i + j) % 3 == 0 else 0.0


@pytest.fixture
def archivo(tmp_path):
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")

    columnas = {"ID": [f"R-{i:03d}" for i in range(N)]}
    etiquetas = {"ID": "Identificador"}
    value_labels = {}
    for j, (codigo, opcion) in enumerate(BATERIA.items()):
        columnas[codigo] = [_valor_bateria(i, j) for i in range(N)]
        etiquetas[codigo] = f"{opcion}:{PREGUNTA_BATERIA}"
        value_labels[codigo] = {0.0: "Unchecked", 1.0: "Checked"}
    columnas["EDAD"] = [float(16 + i % 40) for i in range(N)]
    etiquetas["EDAD"] = "Edad en años"
    value_labels["EDAD"] = {float(k): str(k) for k in range(16, 60)}
    # La cerrada con un código sin traducir y uno de no respuesta.
    columnas["MARCA"] = [1.0 if i % 4 == 0 else 2.0 if i % 4 == 1
                         else 11427.0 if i % 4 == 2 else 99.0 for i in range(N)]
    etiquetas["MARCA"] = "¿Qué marca fumás habitualmente?"
    value_labels["MARCA"] = {1.0: "Nevada", 2.0: "Coronado", 99.0: "No sabe"}
    # Su «Otro: especificar», con datos personales en dos respuestas.
    columnas["MARCAOthr"] = [
        "Nevada Blue" if i == 3 else "llamame al 099 123 456" if i == 7
        else "escribime a juan.perez@gmail.com" if i == 11 else ""
        for i in range(N)]
    etiquetas["MARCAOthr"] = "Otra marca (especificar)"
    columnas["VACIA"] = [float("nan")] * N
    etiquetas["VACIA"] = "Pregunta que nadie contestó"
    columnas["CONST"] = ["Sí"] * N
    etiquetas["CONST"] = "¿Acepta participar?"

    ruta = tmp_path / "campo.sav"
    pyreadstat.write_sav(
        pandas.DataFrame(columnas), str(ruta),
        column_labels=[etiquetas[c] for c in columnas],
        variable_value_labels=value_labels)
    return ruta


@pytest.fixture
def analisis(archivo):
    return sav.analizar(archivo)


def _hallazgos(analisis, tipo):
    """De un análisis de `.sav` (que lo trae en `calidad`) o de un
    diagnóstico directo."""
    diagnostico = analisis.get("calidad") or analisis
    return [h for h in diagnostico["hallazgos"] if h["tipo"] == tipo]


def _variable(analisis, codigo):
    return next(v for v in analisis["variables"] if v["codigo"] == codigo)


def _pregunta(analisis, codigo, **cambios):
    v = _variable(analisis, codigo)
    return dict({"codigo": codigo, "texto": v["texto"],
                 "texto_original": v["texto_del_archivo"], "tipo": v["tipo"],
                 "opciones": v["opciones"], "orden": v["orden"]}, **cambios)


def _aplicar(pregunta, hallazgo, accion=0):
    """Lo que hace la pantalla cuando el analista elige una acción."""
    return dict(pregunta, **hallazgo["acciones"][accion]["propuesta"]
                .get(pregunta["codigo"], {}))


# ════════════════════════════════════════════════════════════════════
#  8A · Interpretar el archivo
# ════════════════════════════════════════════════════════════════════

def test_la_bateria_se_detecta_y_se_agrupa(analisis):
    """R8.1 — las tres señales: prefijo del código, texto después de los dos
    puntos y el mismo par de opciones."""
    baterias = analisis["calidad"]["baterias"]
    assert len(baterias) == 1
    bateria = baterias[0]
    assert [v["codigo"] for v in bateria["variables"]] == list(BATERIA)
    assert [v["opcion"] for v in bateria["variables"]] == list(BATERIA.values())
    assert bateria["valores_marcados"] == ["1"]
    marcadas = sum(_valor_bateria(i, j) for i in range(N) for j in range(3))
    assert bateria["respuestas_marcadas"] == marcadas
    assert bateria["respuestas_no_marcadas"] == 3 * N - marcadas


def test_la_bateria_se_ofrece_sugerida_y_no_aplicada(analisis):
    hallazgo = _hallazgos(analisis, "bateria")[0]
    assert hallazgo["acciones"][0]["etiqueta"] == "Ingestar solo lo marcado"
    # Sugerida, no aplicada: las variables vuelven como estaban.
    for codigo in BATERIA:
        assert "solo_marcadas" not in _variable(analisis, codigo)


def test_una_pregunta_del_tipo_opcion_y_pregunta_se_propone_autocontenida(analisis):
    """R8.2 — el caso exacto de la spec."""
    hallazgo = _hallazgos(analisis, "texto_propuesto")[0]
    # Agrupado: un solo hallazgo para todos los textos, con la lista.
    assert set(BATERIA) <= set(hallazgo["variables"])
    propuesta = hallazgo["acciones"][0]["propuesta"]["var138O1320"]["texto"]
    assert propuesta == "Pensando en el último mes, ¿has consumido cigarrillos?"
    # Editable y no aplicada: el texto de la variable sigue siendo el label.
    assert _variable(analisis, "var138O1320")["texto"].startswith("Cigarrillos:")


def test_las_consignas_y_la_numeracion_se_proponen_quitar():
    assert calidad_dato.proponer_texto(
        "P5: ¿Qué marca fumás? (ESPONTÁNEA)")["texto"] == "¿Qué marca fumás?"
    assert calidad_dato.proponer_texto(
        "¿Qué bebidas tomó? Marque todas las que correspondan"
    )["texto"] == "¿Qué bebidas tomó?"


def test_un_label_truncado_se_marca():
    truncado = "¿Cuánto confía usted en las instituciones del Estado y en el Pod"
    assert calidad_dato.parece_truncado(truncado)
    assert calidad_dato.parece_truncado("x" * 256)
    assert not calidad_dato.parece_truncado("¿Qué edad tiene?")
    analisis = calidad_dato.diagnosticar(
        [{"codigo": "C", "texto": truncado, "tipo": "abierta"}],
        {"C": {"mucho": 3}})
    assert _hallazgos(analisis, "texto_truncado")[0]["variables"] == ["C"]


def test_el_original_queda_guardado_junto_al_editado(
        conn_boveda, conn_semantica, proveedor, analisis, ola):
    """R8.2 — toda propuesta es editable, y el texto original queda guardado
    para poder volver."""
    editada = _pregunta(analisis, "var138O1320",
                        texto="Pensando en el último mes, ¿consumió cigarrillos?")
    _ingestar(conn_boveda, conn_semantica, proveedor, ola, [editada])
    fila = db.una(conn_semantica,
                  "select texto, texto_original from pregunta where codigo = %s",
                  ("var138O1320",))
    assert fila["texto"] == "Pensando en el último mes, ¿consumió cigarrillos?"
    assert fila["texto_original"].startswith("Cigarrillos:Pensando")


def test_etiquetas_iguales_al_codigo_se_proponen_numericas(analisis):
    """R8.3 — `EDAD` con 16: "16", 17: "17"…"""
    hallazgo = next(h for h in _hallazgos(analisis, "tipo")
                    if "EDAD" in h["variables"])
    assert hallazgo["acciones"][0]["propuesta"]["EDAD"] == {
        "tipo": "numerica", "opciones": None}


def test_una_escala_ordinal_se_propone_como_escala():
    analisis = calidad_dato.diagnosticar(
        [{"codigo": "S", "texto": "¿Qué tan de acuerdo está?", "tipo": "cerrada",
          "opciones": {"1": "Muy de acuerdo", "2": "De acuerdo",
                       "3": "En desacuerdo", "4": "Muy en desacuerdo"}}],
        {"S": {"1": 3, "2": 4}})
    assert _hallazgos(analisis, "tipo")[0]["acciones"][0]["propuesta"]["S"] == {
        "tipo": "escala"}


def test_el_otro_especificar_se_ofrece_fusionar_con_su_cerrada(analisis):
    """R8.3 — «¿Qué marca fumás? → Otra: Nevada Blue» dice más que una
    abierta sin contexto."""
    hallazgo = _hallazgos(analisis, "fusion_otro")[0]
    propuesta = hallazgo["acciones"][0]["propuesta"]["MARCAOthr"]
    assert propuesta["fusionada_con"] == "MARCA"
    assert propuesta["texto"] == "¿Qué marca fumás habitualmente?"
    pregunta = _pregunta(analisis, "MARCAOthr", **propuesta)
    _etiqueta, texto, _motivo = ingesta.respuesta_de(pregunta, "Nevada Blue")
    assert texto == "¿Qué marca fumás habitualmente? → Otro: Nevada Blue"


# ════════════════════════════════════════════════════════════════════
#  8B · Normalizar valores
# ════════════════════════════════════════════════════════════════════

def test_checked_unchecked_se_propone_como_si_no(analisis):
    hallazgo = _hallazgos(analisis, "par_conocido")[0]
    assert set(BATERIA) <= set(hallazgo["variables"])
    assert hallazgo["acciones"][0]["propuesta"]["var138O1320"]["opciones"] == {
        "0": "No", "1": "Sí"}


def test_uno_y_cero_sin_etiquetas_se_proponen_como_si_no():
    par = calidad_dato.clasificar_par(None, ["0", "1"])
    assert par["normalizar"] == {"1": "Sí", "0": "No"}


def test_una_etiqueta_sin_traducir_se_advierte_antes_de_ingestar(analisis):
    hallazgo = _hallazgos(analisis, "sin_traducir")[0]
    assert hallazgo["variables"] == ["MARCA"]
    assert hallazgo["severidad"] == calidad_dato.ROMPE
    assert hallazgo["detalle"]["valores"] == {"11427": N // 4}
    assert "11427" in hallazgo["mensaje"]


def test_la_etiqueta_sin_traducir_aparece_en_el_resumen_de_revision(
        ctx, actor, archivo, analisis, ola):
    """Antes de ingestar: en el paso de revisión (R7.2), no al final."""
    status, resumen = _ingesta_por_ruta(
        ctx, actor, archivo, ola, [_pregunta(analisis, "MARCA")],
        solo_revisar=True)
    assert status == 200
    assert any(a["tipo"] == "sin_traducir" for a in resumen["advertencias"])


def test_los_espacios_y_las_mayusculas_se_normalizan_sin_cambiar_el_contenido():
    analisis = calidad_dato.diagnosticar(
        [{"codigo": "M", "texto": "¿Marca?", "tipo": "cerrada",
          "opciones": {"1": "  NEVADA  ", "2": "Coronado"}}],
        {"M": {"1": 2, "2": 1}})
    hallazgo = _hallazgos(analisis, "etiquetas_inconsistentes")[0]
    assert hallazgo["acciones"][0]["propuesta"]["M"]["opciones"] == {
        "1": "Nevada", "2": "Coronado"}


def test_los_valores_de_no_respuesta_se_detectan(analisis):
    hallazgo = _hallazgos(analisis, "no_respuesta")[0]
    item = next(i for i in hallazgo["detalle"]["items"] if i["codigo"] == "MARCA")
    assert [d["valor"] for d in item["valores"]] == ["99"]
    assert hallazgo["acciones"][0]["propuesta"]["MARCA"] == {
        "excluir_valores": ["99"]}


def test_los_valores_de_no_respuesta_se_pueden_excluir(
        conn_boveda, conn_semantica, proveedor, analisis, ola):
    pregunta = _aplicar(_pregunta(analisis, "MARCA"),
                        _hallazgos(analisis, "no_respuesta")[0])
    resultado = _ingestar(conn_boveda, conn_semantica, proveedor, ola, [pregunta])
    assert resultado["descartadas_no_respuesta"] == N // 4
    textos = _textos(conn_semantica, "MARCA")
    assert not any("No sabe" in t for t in textos)


def test_si_se_ingestan_se_embeben_con_su_etiqueta_y_no_con_el_codigo():
    """Un 99 sin etiqueta: la acción alternativa le pone una."""
    analisis = calidad_dato.diagnosticar(
        [{"codigo": "M", "texto": "¿Marca?", "tipo": "cerrada",
          "opciones": {"1": "Nevada"}}],
        {"M": {"1": 5, "99": 2}})
    hallazgo = _hallazgos(analisis, "no_respuesta")[0]
    etiquetar = hallazgo["acciones"][1]["propuesta"]["M"]
    pregunta = {"codigo": "M", "texto": "¿Marca?", **etiquetar}
    assert ingesta.respuesta_de(pregunta, "99")[1] == "¿Marca? → No sabe / No contesta"


def test_la_lista_de_no_respuesta_es_configurable():
    preguntas = [{"codigo": "A", "texto": "¿Por qué?", "tipo": "abierta"}]
    distribucion = {"A": {"Ninguna idea": 3, "Porque sí": 4}}
    sin = calidad_dato.diagnosticar(preguntas, distribucion)
    con = calidad_dato.diagnosticar(preguntas, distribucion,
                                    valores_no_respuesta=["Ninguna idea"])
    assert not _hallazgos(sin, "no_respuesta")
    assert _hallazgos(con, "no_respuesta")[0]["cuantos"] == 3
    assert con["valores_no_respuesta"] == ["Ninguna idea"]


# ════════════════════════════════════════════════════════════════════
#  8C · Detectar problemas
# ════════════════════════════════════════════════════════════════════

def test_una_abierta_con_correos_o_telefonos_dispara_la_advertencia(analisis):
    """R8.6 — con conteo y ejemplos, enmascarados, y como indicio."""
    hallazgo = _hallazgos(analisis, "pii_en_texto_libre")[0]
    assert hallazgo["variables"] == ["MARCAOthr"]
    assert hallazgo["cuantos"] == 2
    assert hallazgo["detalle"]["por_tipo"] == {"telefono": 1, "correo": 1}
    ejemplos = " ".join(e["texto"] for e in hallazgo["detalle"]["ejemplos"])
    assert "juan.perez@gmail.com" not in ejemplos
    assert "099 123 456" not in ejemplos
    assert "indicio" in hallazgo["mensaje"]
    etiquetas = [a["etiqueta"] for a in hallazgo["acciones"]]
    assert etiquetas == ["Excluir la variable", "Ingestarla igual, a conciencia"]


def test_la_cedula_se_detecta_con_su_digito_verificador():
    assert [h[0] for h in calidad_dato.buscar_pii("mi ci 1.234.567-2")] == ["cedula"]
    # Un número de ocho cifras cualquiera no pasa el verificador.
    assert calidad_dato.buscar_pii("código 1.234.567-3") == []
    assert [h[0] for h in calidad_dato.buscar_pii("ver www.ejemplo.com")] == ["url"]


def test_la_advertencia_de_pii_no_bloquea_la_ingesta(
        conn_boveda, conn_semantica, proveedor, analisis, ola):
    """Ni aceptada ni sin aceptar: es una decisión informada, no una
    prohibición."""
    resultado = _ingestar(conn_boveda, conn_semantica, proveedor, ola,
                          [_pregunta(analisis, "MARCAOthr")])
    assert resultado["respuestas_escritas"] == 3
    aceptada = _pregunta(analisis, "MARCAOthr", pii_aceptada=True)
    resultado = _ingestar(conn_boveda, conn_semantica, proveedor, ola, [aceptada])
    assert resultado["respuestas_escritas"] == 3
    fila = db.una(conn_semantica,
                  "select normalizacion from pregunta where codigo = 'MARCAOthr'")
    assert fila["normalizacion"]["pii_aceptada"] is True


def test_una_variable_sin_respuestas_se_propone_excluir(analisis):
    hallazgo = _hallazgos(analisis, "sin_respuestas")[0]
    assert hallazgo["variables"] == ["VACIA"]
    assert hallazgo["acciones"][0]["propuesta"]["VACIA"] == {"incluir": False}


def test_una_variable_casi_constante_se_informa(analisis):
    hallazgo = _hallazgos(analisis, "casi_constante")[0]
    assert hallazgo["variables"] == ["CONST"]
    assert hallazgo["severidad"] == calidad_dato.INFO
    # Las de la batería no: ahí casi todo «no» es lo esperable.
    assert not any(set(h["variables"]) & set(BATERIA)
                   for h in _hallazgos(analisis, "casi_constante"))


def test_el_resumen_dice_cuantas_respuestas_genera_cada_variable(analisis):
    variables = analisis["calidad"]["variables"]
    assert variables["MARCA"]["respuestas"] == N
    assert variables["VACIA"]["respuestas"] == 0
    assert variables["MARCAOthr"]["genera"] == 3


def test_lo_que_rompe_va_primero(analisis):
    severidades = [h["severidad"] for h in analisis["calidad"]["hallazgos"]]
    orden = {"rompe": 0, "mejora": 1, "info": 2}
    assert severidades == sorted(severidades, key=orden.get)
    assert severidades[0] == "rompe"


# ════════════════════════════════════════════════════════════════════
#  R8.1 en la ingesta
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def ola(conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel Fase 8")
    for i in range(N):
        id_persona = personas.alta(conn_boveda, {
            "persona": {"documento": f"F8-{i}", "nombre": f"Persona {i}"},
            "consentimientos": consentimientos(*AMBAS),
            "origen": "sav", "id_en_origen": f"R-{i:03d}",
        })["id_persona"]
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola tabaco", "2026-09-01")
    conn_boveda.commit()
    return encuesta


def _ingestar(conn_boveda, conn_semantica, proveedor, encuesta, preguntas,
              archivo_filas=None):
    filas = archivo_filas or _FILAS
    resultado = encuestas.ingestar(
        conn_boveda, conn_semantica, encuesta["id"], preguntas, filas,
        columna_id="ID", origen="sav", proveedor=proveedor)
    conn_semantica.commit()
    conn_boveda.commit()
    return resultado


_FILAS = None


@pytest.fixture(autouse=True)
def _filas_del_archivo(archivo):
    global _FILAS
    _FILAS = sav.filas_de(archivo)
    yield
    _FILAS = None


def _textos(conn_semantica, codigo):
    return [f["texto_embebido"] for f in db.todas(
        conn_semantica,
        """select r.texto_embebido from respuesta r
             join pregunta p on p.id = r.pregunta_id
            where p.codigo = %s order by r.id""", (codigo,))]


def test_ingestar_solo_lo_marcado_descarta_y_lo_informa(
        conn_boveda, conn_semantica, proveedor, analisis, ola):
    hallazgo = _hallazgos(analisis, "bateria")[0]
    preguntas = [_aplicar(_pregunta(analisis, c), hallazgo) for c in BATERIA]
    resultado = _ingestar(conn_boveda, conn_semantica, proveedor, ola, preguntas)
    bateria = analisis["calidad"]["baterias"][0]
    assert resultado["respuestas_escritas"] == bateria["respuestas_marcadas"]
    assert resultado["descartadas_no_marcadas"] == bateria["respuestas_no_marcadas"]
    # Quien no marcó ninguna no genera respuestas para la batería.
    individuos = {f["id_persona"] for f in db.todas(
        conn_semantica,
        """select distinct i.id_persona::text from respuesta r
             join individuo i on i.id = r.individuo_id""")}
    persona_0 = db.una(conn_boveda,
                       "select id_persona::text from alias_origen "
                       " where id_en_origen = 'R-000'")["id_persona"]
    assert persona_0 not in individuos


def test_sin_decidir_la_ingesta_hace_lo_de_siempre(
        conn_boveda, conn_semantica, proveedor, analisis, ola):
    """Nada se aplica solo: sin decisiones en la pregunta, entra todo, con el
    texto del archivo, como antes de la Fase 8."""
    preguntas = [_pregunta(analisis, c) for c in BATERIA]
    resultado = _ingestar(conn_boveda, conn_semantica, proveedor, ola, preguntas)
    assert resultado["respuestas_escritas"] == 3 * N
    assert resultado["descartadas_no_marcadas"] == 0
    assert any(t.endswith("→ Unchecked") for t in _textos(conn_semantica, "var138O1320"))


# ════════════════════════════════════════════════════════════════════
#  8D · Vista previa
# ════════════════════════════════════════════════════════════════════

def test_la_vista_previa_muestra_el_texto_embebido_real(analisis):
    """R8.8 — con valores del archivo, el más frecuente y uno poco frecuente."""
    marca = _variable(analisis, "MARCA")
    previa = calidad_dato.vista_previa(
        _pregunta(analisis, "MARCA"),
        {m["valor"]: m["filas"] for m in marca["muestra"]})
    ejemplos = [e for e in previa["ejemplos"] if "texto_embebido" in e]
    assert len(ejemplos) >= 2
    frecuencias = [e["filas"] for e in ejemplos]
    assert max(frecuencias) == max(m["filas"] for m in marca["muestra"])
    assert ejemplos[0]["texto_embebido"].startswith("¿Qué marca fumás habitualmente? → ")


def test_la_vista_previa_se_actualiza_al_corregir(ctx, actor, analisis):
    """Por la ruta, que es lo que llama la pantalla en cada corrección."""
    # Dos valores: el más frecuente y el código sin traducir, que es el que
    # la corrección de abajo traduce.
    muestras = {"MARCA": [{"valor": "1", "filas": 9}, {"valor": "11427", "filas": 2}]}

    def previa(**cambios):
        _status, salida = ruteo.despachar(
            "POST", "/calidad/vista-previa",
            {"preguntas": [_pregunta(analisis, "MARCA", **cambios)],
             "muestras": muestras}, {}, actor("analista"), ctx)
        return {e["valor"]: e.get("texto_embebido")
                for e in salida["items"]["MARCA"]["ejemplos"]}

    antes = previa()
    despues = previa(texto="¿Qué marca de cigarrillos fuma?",
                     opciones={"1": "Nevada", "2": "Coronado", "99": "No sabe",
                               "11427": "Nevada Blue"})
    assert antes != despues
    assert all(t.startswith("¿Qué marca de cigarrillos fuma? → ")
               for t in despues.values() if t)
    assert "¿Qué marca de cigarrillos fuma? → Nevada Blue" in despues.values()


def test_la_vista_previa_coincide_con_lo_que_se_escribe(
        conn_boveda, conn_semantica, proveedor, analisis, ola):
    pregunta = _pregunta(analisis, "MARCA")
    previa = calidad_dato.vista_previa(
        pregunta, {m["valor"]: m["filas"]
                   for m in _variable(analisis, "MARCA")["muestra"]})
    _ingestar(conn_boveda, conn_semantica, proveedor, ola, [pregunta])
    escritos = set(_textos(conn_semantica, "MARCA"))
    for ejemplo in previa["ejemplos"]:
        if ejemplo.get("texto_embebido"):
            assert ejemplo["texto_embebido"] in escritos


def test_el_resumen_de_revision_trae_vista_previa_y_descartes(
        ctx, actor, archivo, analisis, ola):
    hallazgo = _hallazgos(analisis, "bateria")[0]
    preguntas = [_aplicar(_pregunta(analisis, c), hallazgo) for c in BATERIA]
    status, resumen = _ingesta_por_ruta(ctx, actor, archivo, ola, preguntas,
                                        solo_revisar=True)
    assert status == 200
    bateria = analisis["calidad"]["baterias"][0]
    assert resumen["volumen"]["respuestas_a_escribir"] == bateria["respuestas_marcadas"]
    assert resumen["volumen"]["descartadas_no_marcadas"] == bateria["respuestas_no_marcadas"]
    assert set(resumen["vista_previa"]) == set(BATERIA)
    assert resumen["al_store_semantico"]["variables"][0]["normalizacion"][
        "solo_marcadas"] is True


def _ingesta_por_ruta(ctx, actor, archivo, encuesta, preguntas, **extra):
    cuerpo = {
        "archivo_base64": base64.b64encode(archivo.read_bytes()).decode(),
        "preguntas": preguntas, "columna_id": "ID", "origen": "sav",
        **extra,
    }
    return ruteo.despachar("POST", f"/encuestas/{encuesta['id']}/sav/ingesta",
                           cuerpo, {}, actor("admin"), ctx)


# ════════════════════════════════════════════════════════════════════
#  8D · Reproceso
# ════════════════════════════════════════════════════════════════════

class Contador:
    """Envuelve al proveedor y cuenta qué textos se mandaron a embeber."""

    def __init__(self, envuelto):
        self.envuelto = envuelto
        self.dims = envuelto.dims
        self.textos = []

    def embeber(self, textos):
        self.textos.extend(textos)
        return self.envuelto.embeber(textos)

    def embeber_por_lotes(self, textos, tamano_lote=128):
        self.textos.extend(textos)
        yield from self.envuelto.embeber_por_lotes(textos, tamano_lote)


def _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador):
    resultados = []
    pendientes = list(encolador.encoladas)
    encolador.encoladas.clear()
    for trabajo_id, indice in pendientes:
        resultados.append(diferida.procesar_lote(
            conn_boveda, conn_semantica, trabajo_id, indice, proveedor=proveedor))
    return resultados


@pytest.fixture
def cargado(conn_boveda, conn_semantica, proveedor, analisis, ola):
    """El estudio ingestado tal cual vino, sin corregir nada."""
    preguntas = [_pregunta(analisis, c) for c in (*BATERIA, "MARCA", "MARCAOthr")]
    _ingestar(conn_boveda, conn_semantica, proveedor, ola, preguntas)
    return ola


def _estado_de_respuestas(conn_semantica):
    return {f["id"]: (f["codigo"], f["hash_texto"], f["xmin"], f["embedding"])
            for f in db.todas(
                conn_semantica,
                """select r.id, p.codigo, r.hash_texto, r.xmin::text as xmin,
                          r.embedding::text as embedding
                     from respuesta r join pregunta p on p.id = r.pregunta_id""")}


def test_el_reproceso_reembebe_solo_lo_que_cambio(
        conn_boveda, conn_semantica, proveedor, cargado):
    """R8.9 — con `hash_texto`: lo que no cambió no se re-embebe **ni se
    re-escribe** (su `xmin` no se mueve)."""
    antes = _estado_de_respuestas(conn_semantica)
    encolador = diferida.EncoladorEnMemoria()
    status, salida = reproceso.lanzar(
        conn_boveda, conn_semantica, "encuesta", cargado["id"],
        [{"codigo": "MARCA", "texto": "¿Qué marca de cigarrillos fuma?"}],
        encolador=encolador)
    assert status == 202
    contador = Contador(proveedor)
    _procesar_todo(conn_boveda, conn_semantica, contador, encolador)
    conn_semantica.commit()

    despues = _estado_de_respuestas(conn_semantica)
    de_marca = [i for i, v in antes.items() if v[0] == "MARCA"]
    otras = [i for i, v in antes.items() if v[0] != "MARCA"]
    assert len(contador.textos) == len(de_marca)
    assert all(t.startswith("¿Qué marca de cigarrillos fuma? → ")
               for t in contador.textos)
    for i in de_marca:
        assert despues[i][1] != antes[i][1]
    for i in otras:
        assert despues[i] == antes[i]

    trabajo = diferida.estado(conn_boveda, salida["trabajo_id"])
    assert trabajo["estado"] == diferida.TERMINADA
    assert trabajo["operacion"] == "reproceso"
    assert trabajo["resumen"]["reembebidas"] == len(de_marca)


def test_un_cambio_de_mapeo_solo_toca_los_codigos_afectados(
        conn_boveda, conn_semantica, proveedor, cargado):
    """El 11427 sin traducir se traduce, sin volver a subir el archivo, y los
    demás valores de la misma pregunta quedan como estaban."""
    antes = _estado_de_respuestas(conn_semantica)
    encolador = diferida.EncoladorEnMemoria()
    reproceso.lanzar(
        conn_boveda, conn_semantica, "encuesta", cargado["id"],
        [{"codigo": "MARCA", "opciones": {"1": "Nevada", "2": "Coronado",
                                          "99": "No sabe", "11427": "Nevada Blue"}}],
        encolador=encolador)
    contador = Contador(proveedor)
    _procesar_todo(conn_boveda, conn_semantica, contador, encolador)
    conn_semantica.commit()
    assert contador.textos == ["¿Qué marca fumás habitualmente? → Nevada Blue"] * (N // 4)
    despues = _estado_de_respuestas(conn_semantica)
    cambiadas = [i for i in antes if antes[i] != despues.get(i)]
    assert len(cambiadas) == N // 4


def test_excluir_una_variable_en_un_reproceso_borra_sus_respuestas(
        conn_boveda, conn_semantica, proveedor, cargado):
    encolador = diferida.EncoladorEnMemoria()
    reproceso.lanzar(conn_boveda, conn_semantica, "encuesta", cargado["id"],
                     [{"codigo": "MARCAOthr", "excluir": True}],
                     encolador=encolador)
    assert len(_textos(conn_semantica, "MARCAOthr")) == 3   # todavía no
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)
    conn_semantica.commit()
    assert _textos(conn_semantica, "MARCAOthr") == []
    fila = db.una(conn_semantica,
                  "select normalizacion from pregunta where codigo = 'MARCAOthr'")
    assert fila["normalizacion"]["excluida"] is True


def test_aplicar_solo_lo_marcado_en_un_reproceso_borra_lo_no_marcado(
        conn_boveda, conn_semantica, proveedor, cargado, analisis):
    hallazgo = _hallazgos(analisis, "bateria")[0]
    encolador = diferida.EncoladorEnMemoria()
    reproceso.lanzar(
        conn_boveda, conn_semantica, "encuesta", cargado["id"],
        [hallazgo["acciones"][0]["propuesta"][c] | {"codigo": c} for c in BATERIA],
        encolador=encolador)
    contador = Contador(proveedor)
    _procesar_todo(conn_boveda, conn_semantica, contador, encolador)
    conn_semantica.commit()
    # Lo marcado ya decía «→ Checked»: el texto no cambia, no se re-embebe.
    assert contador.textos == []
    textos = [t for c in BATERIA for t in _textos(conn_semantica, c)]
    assert len(textos) == analisis["calidad"]["baterias"][0]["respuestas_marcadas"]
    assert all(t.endswith("→ Checked") for t in textos)


def test_el_reproceso_corre_por_la_via_diferida(
        conn_boveda, conn_semantica, cargado):
    """Lanzar encola y no procesa: hasta que corren las tareas, el texto
    embebido es el de antes."""
    antes = _textos(conn_semantica, "MARCA")
    encolador = diferida.EncoladorEnMemoria()
    status, salida = reproceso.lanzar(
        conn_boveda, conn_semantica, "encuesta", cargado["id"],
        [{"codigo": "MARCA", "texto": "Otra redacción"}], encolador=encolador,
        tamano_lote=2)
    assert status == 202
    assert salida["lotes_total"] == (N + 1) // 2
    assert len(encolador.encoladas) == salida["lotes_total"]
    assert _textos(conn_semantica, "MARCA") == antes
    plan = db.una(conn_boveda, "select plan from ingesta_trabajo where id = %s",
                  (salida["trabajo_id"],))["plan"]
    assert plan["operacion"] == "reproceso"
    assert plan["preguntas"]["MARCA"]["antes"]["texto"] == "¿Qué marca fumás habitualmente?"


def test_queda_registrado_que_se_cambio_y_cuando(
        conn_boveda, conn_semantica, cargado):
    reproceso.lanzar(conn_boveda, conn_semantica, "encuesta", cargado["id"],
                     [{"codigo": "MARCA", "texto": "Otra redacción"}],
                     encolador=diferida.EncoladorEnMemoria())
    fila = db.una(conn_semantica,
                  "select creado_en, cambios, trabajo_id from reproceso")
    assert fila["creado_en"] is not None
    assert fila["trabajo_id"] is not None
    assert fila["cambios"] == [{"codigo": "MARCA", "campo": "texto",
                                "antes": "¿Qué marca fumás habitualmente?",
                                "despues": "Otra redacción"}]
    # Y el original sigue estando, para volver.
    pregunta = db.una(conn_semantica,
                      "select texto, texto_original from pregunta where codigo = 'MARCA'")
    assert pregunta == {"texto": "Otra redacción",
                        "texto_original": "¿Qué marca fumás habitualmente?"}


def test_revisar_un_reproceso_no_escribe_nada(
        ctx, actor, conn_boveda, conn_semantica, cargado):
    antes = _estado_de_respuestas(conn_semantica)
    status, revision = ruteo.despachar(
        "POST", f"/encuestas/{cargado['id']}/reproceso",
        {"preguntas": [{"codigo": "MARCAOthr", "excluir": True},
                       {"codigo": "MARCA", "texto": "Otra redacción"}],
         "solo_revisar": True}, {}, actor("admin"), ctx)
    assert status == 200
    assert revision["se_borran"] == 3
    assert revision["reembeben"] == N
    assert revision["sin_cambios"] == 0
    assert _estado_de_respuestas(conn_semantica) == antes
    assert db.una(conn_semantica, "select count(*) as n from reproceso")["n"] == 0


def test_dos_reprocesos_a_la_vez_no_se_pisan(conn_boveda, conn_semantica, cargado):
    encolador = diferida.EncoladorEnMemoria()
    reproceso.lanzar(conn_boveda, conn_semantica, "encuesta", cargado["id"],
                     [{"codigo": "MARCA", "texto": "Uno"}], encolador=encolador)
    with pytest.raises(Conflicto):
        reproceso.lanzar(conn_boveda, conn_semantica, "encuesta", cargado["id"],
                         [{"codigo": "MARCA", "texto": "Dos"}], encolador=encolador)


def test_sin_cambios_no_hay_nada_que_encolar(conn_boveda, conn_semantica, cargado):
    status, salida = reproceso.lanzar(
        conn_boveda, conn_semantica, "encuesta", cargado["id"],
        [{"codigo": "MARCA", "texto": "¿Qué marca fumás habitualmente?"}],
        encolador=diferida.EncoladorEnMemoria())
    assert status == 200 and salida["sin_cambios"]


def test_el_reproceso_reaplica_el_gate_de_consentimiento(
        conn_boveda, conn_semantica, proveedor, cargado):
    """El texto de quien retiró `uso_semantico` no se manda a embeber,
    aunque su respuesta todavía estuviera en la base."""
    from panel_api import consentimiento

    id_persona = db.una(conn_boveda,
                        "select id_persona::text from alias_origen "
                        " where id_en_origen = 'R-000'")["id_persona"]
    consentimiento.marcar_retirado(conn_boveda, id_persona, "uso_semantico")
    conn_boveda.commit()
    encolador = diferida.EncoladorEnMemoria()
    reproceso.lanzar(conn_boveda, conn_semantica, "encuesta", cargado["id"],
                     [{"codigo": "MARCA", "texto": "Otra redacción"}],
                     encolador=encolador)
    contador = Contador(proveedor)
    resultados = _procesar_todo(conn_boveda, conn_semantica, contador, encolador)
    assert len(contador.textos) == N - 1
    assert sum(r["resultado"]["sin_consentimiento_reproceso"]
               for r in resultados) == 1


def test_una_etiqueta_normalizada_se_sigue_reconociendo():
    """Lo embebido como «Checked» con la pregunta que ya dice «Sí» se
    reconoce igual como el código 1: por eso se guardan las originales."""
    antes = {"opciones": {"0": "No", "1": "Sí"},
             "opciones_originales": {"0": "Unchecked", "1": "Checked"}}
    assert reproceso.crudo_de(antes, "Checked") == "1"
    assert reproceso.crudo_de(antes, "Sí") == "1"
    assert reproceso.crudo_de(antes, "11427") == "11427"


def test_la_pantalla_de_reproceso_trae_preguntas_y_diagnostico(
        ctx, actor, cargado):
    status, salida = ruteo.despachar(
        "GET", f"/encuestas/{cargado['id']}/preguntas", {}, {},
        actor("analista"), ctx)
    assert status == 200
    codigos = [p["codigo"] for p in salida["preguntas"]]
    assert set(codigos) == {*BATERIA, "MARCA", "MARCAOthr"}
    marca = next(p for p in salida["preguntas"] if p["codigo"] == "MARCA")
    # La muestra trae códigos crudos, no etiquetas: es lo que la vista previa
    # vuelve a pasar por la configuración nueva.
    assert {m["valor"] for m in marca["muestra"]} == {"1", "2", "11427", "99"}
    assert salida["calidad"]["baterias"]
    assert any(h["tipo"] == "sin_traducir" for h in salida["calidad"]["hallazgos"])


def test_el_diagnostico_de_un_csv_sale_de_sus_filas(ctx, actor):
    status, salida = ruteo.despachar(
        "POST", "/calidad/diagnostico",
        {"preguntas": [{"codigo": "P1", "texto": "¿Por qué?", "tipo": "abierta"}],
         "filas": [{"ID": "1", "P1": "mandame un mail a a.b@c.com"},
                   {"ID": "2", "P1": "porque sí"}]},
        {}, actor("admin"), ctx)
    assert status == 200
    assert salida["hallazgos"][0]["tipo"] == "pii_en_texto_libre"
    assert salida["filas"] == 2


def test_las_rutas_de_la_fase_8_no_estan_abiertas_a_cualquiera(ctx, actor):
    """Diagnosticar y reprocesar piden el permiso de ingestar."""
    from panel_api.errores import ErrorApi

    with pytest.raises(ErrorApi):
        ruteo.despachar("POST", "/calidad/vista-previa",
                        {"preguntas": [{"codigo": "A", "texto": "x"}]}, {},
                        actor("dpo"), ctx)


def test_la_bateria_no_se_sugiere_como_documento(analisis):
    """Lo encontró el archivo de esta prueba, que copia la estructura del
    real: la sugerencia de marcado demográfico buscaba `^ci` y «Cigarrillos:
    Pensando…» empieza así. Es el marcado que casi fusiona 1131 personas en
    dos. El prefijo tiene que ser una palabra entera."""
    sugeridas = {s["codigo"]: s["campo"] for s in analisis["demograficas_sugeridas"]}
    assert "var138O1320" not in sugeridas
    assert sav._sugerir_demografica("CI_NUM", "") == "documento"
    assert sav._sugerir_demografica("V1", "Televisión: ¿mira?") is None
    assert sav._sugerir_demografica("V2", "Agenda cultural") is None


def test_el_resumen_de_una_encuesta_dice_a_que_panel_van(
        ctx, actor, archivo, analisis, ola):
    """Encontrado al recorrer la pantalla: en el modo «ya existen» la
    pantalla no manda `panel_id`, y el resumen decía «no quedan asociados a
    ningún panel» cuando la ingesta igual los incorpora al de la encuesta."""
    _status, resumen = _ingesta_por_ruta(
        ctx, actor, archivo, ola, [_pregunta(analisis, "MARCA")],
        solo_revisar=True)
    assert resumen["panel"]["sin_panel"] is False
    assert resumen["panel"]["panel_id"] == ola["panel_id"]
