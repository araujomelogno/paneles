"""R-MAP — Mapeo explícito de valores del archivo a categorías canónicas.

R3.14.c pedía que los valores de una variable marcada como atributo
categórico se mapearan a las categorías del catálogo «igual que se hace con
las etiquetas de una pregunta cerrada», y quedó implementado a medias: la
pantalla podía decir *«esta variable es nivel educativo»* y nada más. Con eso,
un archivo con códigos `1, 2, 3` no tenía cómo conectarse con `primaria`,
`secundaria`, `terciaria`, y como cada estudio codifica distinto el dato
entraba crudo o no entraba.

Lo que se rompía era silencioso, que es lo peor: los filtros demográficos, la
composición contra objetivo y las cuotas del muestreo trabajaban sobre un
atributo vacío sin que nadie se enterara en el momento de cargar.
"""

import pytest

from panel_api import atributos, encuestas, paneles, personas, sav
from panel_api.errores import DatosInvalidos

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")


# ── Escenario ────────────────────────────────────────────────────────

def _persona(conn, documento):
    return personas.alta(
        conn,
        {"persona": {"documento": documento, "nombre": f"Panelista {documento}"},
         "consentimientos": consentimientos(*AMBAS)},
    )["id_persona"]


@pytest.fixture
def educacion(conn_boveda, actor):
    """El caso del reporte: un atributo cuyas categorías no se parecen a los
    códigos que trae ningún archivo."""
    return atributos.crear(
        conn_boveda,
        {"clave": "nivel_educativo", "etiqueta": "Nivel educativo",
         "tipo": "categorico",
         "categorias": [
             {"clave": "primaria", "etiqueta": "Primaria", "orden": 10},
             {"clave": "secundaria", "etiqueta": "Secundaria", "orden": 20},
             {"clave": "terciaria", "etiqueta": "Terciaria", "orden": 30},
         ]},
        actor=actor("admin"))


def _ingestar(ctx, conn_boveda, filas, demograficas, opciones=None):
    """Una ingesta mínima con una variable demográfica y una pregunta."""
    from panel_api import dedup

    panel = paneles.crear(conn_boveda, "Panel R-MAP")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    gente = []
    for numero, fila in enumerate(filas, start=1):
        id_persona = _persona(conn_boveda, f"MAP-{numero}")
        gente.append(id_persona)
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
        dedup.registrar_alias(conn_boveda, id_persona, "dooblo", fila["id_en_origen"])
    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"],
        [{"codigo": "P1", "texto": "¿Qué bebida prefiere?", "tipo": "abierta"},
         {"codigo": "EDU", "texto": "Nivel educativo", "tipo": "cerrada",
          "opciones": opciones}],
        filas, origen="dooblo", proveedor=ctx.embeddings,
        demograficas=demograficas)
    resultado["_gente"] = gente
    return resultado


# ════════════════════════════════════════════════════════════════════
#  R-MAP.1 · Declarar el mapeo
# ════════════════════════════════════════════════════════════════════

def test_el_marcado_acepta_el_mapeo_de_valores(conn_boveda, educacion):
    marcado = sav.normalizar_demograficas(
        {"EDU": {"campo": "nivel_educativo",
                 "mapeo": {"1": "primaria", "2": "secundaria"}}},
        campos_validos=sav.campos_demograficos(conn_boveda), conn=conn_boveda)
    assert marcado == {"EDU": {"campo": "nivel_educativo",
                               "mapeo": {"1": "primaria", "2": "secundaria"}}}


def test_la_forma_vieja_del_marcado_sigue_andando(conn_boveda, educacion):
    """No regresión. Las llamadas que ya existen mandan `{variable: campo}` y
    tienen que seguir significando «sin mapeo declarado»."""
    marcado = sav.normalizar_demograficas(
        {"EDU": "nivel_educativo"},
        campos_validos=sav.campos_demograficos(conn_boveda), conn=conn_boveda)
    assert sav.campo_de(marcado["EDU"]) == "nivel_educativo"
    assert sav.mapeo_de(marcado["EDU"]) == {}
    assert sav.mapeo_por_campo(marcado) == {"nivel_educativo": "EDU"}


def test_un_atributo_que_no_es_categorico_no_admite_mapeo(conn_boveda):
    """La edad es un número: mapear «1 → primaria» sobre ella no es un typo,
    es una confusión sobre qué significa mapear."""
    with pytest.raises(DatosInvalidos, match="no tiene categorías"):
        sav.normalizar_demograficas(
            {"EDAD": {"campo": "edad", "mapeo": {"1": "primaria"}}},
            campos_validos=sav.campos_demograficos(conn_boveda), conn=conn_boveda)


def test_un_patronimico_tampoco_admite_mapeo(conn_boveda):
    with pytest.raises(DatosInvalidos, match="no es un atributo del catálogo"):
        sav.normalizar_demograficas(
            {"NOM": {"campo": "nombre", "mapeo": {"1": "primaria"}}},
            campos_validos=sav.campos_demograficos(conn_boveda), conn=conn_boveda)


def test_el_backend_rechaza_una_categoria_que_no_existe(conn_boveda, educacion):
    """Es la última línea contra un mapeo inventado: la pantalla ofrece un
    desplegable, pero la pantalla no es la autoridad."""
    with pytest.raises(DatosInvalidos, match="no es una categoría"):
        sav.normalizar_demograficas(
            {"EDU": {"campo": "nivel_educativo", "mapeo": {"1": "posgrado"}}},
            campos_validos=sav.campos_demograficos(conn_boveda), conn=conn_boveda)


# ════════════════════════════════════════════════════════════════════
#  R-MAP.2 · La sugerencia
# ════════════════════════════════════════════════════════════════════

def test_las_etiquetas_que_coinciden_se_proponen(conn_boveda, educacion):
    """El caso fácil: el `.sav` trae «Primaria» y el catálogo también."""
    r = sav.sugerir_mapeo(
        conn_boveda, "nivel_educativo", ["1", "2"],
        etiquetas={"1": "Primaria", "2": "Secundaria"})
    assert r["mapeo"] == {"1": "primaria", "2": "secundaria"}
    assert r["sin_sugerencia"] == []


def test_la_sugerencia_ignora_mayusculas_y_acentos(conn_boveda, actor):
    atributos.crear(
        conn_boveda,
        {"clave": "region", "etiqueta": "Región", "tipo": "categorico",
         "categorias": [{"clave": "area_metropolitana",
                         "etiqueta": "Área metropolitana"}]},
        actor=actor("admin"))
    r = sav.sugerir_mapeo(conn_boveda, "region", ["1"],
                          etiquetas={"1": "AREA METROPOLITANA"})
    assert r["mapeo"] == {"1": "area_metropolitana"}


def test_las_etiquetas_que_no_coinciden_quedan_pendientes(conn_boveda, educacion):
    """`1=Bajo` contra `primaria/secundaria/terciaria`: no hay nada que
    proponer, y proponer cualquier cosa sería peor que no proponer."""
    r = sav.sugerir_mapeo(conn_boveda, "nivel_educativo", ["1", "2"],
                          etiquetas={"1": "Bajo", "2": "Alto"})
    assert r["mapeo"] == {"1": None, "2": None}
    assert r["sin_sugerencia"] == ["1", "2"]


def test_sin_etiquetas_no_se_propone_nada(conn_boveda, educacion):
    """Un `.xlsx`, o un `.sav` exportado sin value labels: solo códigos."""
    r = sav.sugerir_mapeo(conn_boveda, "nivel_educativo", ["1", "2", "3"])
    assert set(r["sin_sugerencia"]) == {"1", "2", "3"}


def test_la_sugerencia_no_se_aplica_sola(ctx, conn_boveda, educacion):
    """Lo más importante de R-MAP.2: propone, no decide. Una ingesta que no
    declara mapeo no usa la sugerencia —queda sin mapear— y eso es correcto:
    quien carga tiene que confirmarla."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "1"}],
        demograficas={"EDU": "nivel_educativo"},
        opciones={"1": "Bajo"})
    assert resultado["valores_sin_categoria"]
    assert resultado["demograficos_completados"] == 0


def test_no_se_sugiere_mapeo_de_un_atributo_sin_categorias(conn_boveda):
    with pytest.raises(DatosInvalidos, match="no tiene categorías"):
        sav.sugerir_mapeo(conn_boveda, "edad", ["30"])


# ════════════════════════════════════════════════════════════════════
#  R-MAP.3 y R-MAP.4 · Lo que entra, lo que no, y lo que queda guardado
# ════════════════════════════════════════════════════════════════════

def test_el_mapeo_declarado_entra_como_categoria_canonica(ctx, conn_boveda,
                                                          educacion):
    """El caso que R3.14.c prometía y no cumplía: `1` en el archivo,
    `primaria` en la bóveda."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "1"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}})
    assert resultado["demograficos_completados"] == 1

    id_persona = resultado["_gente"][0]
    valores = {v["clave"]: v for v in atributos.valores_de(conn_boveda, id_persona)}
    assert valores["nivel_educativo"]["valor"] == "primaria"
    # R-MAP.4 — la categoría **y** el crudo: sin el crudo no se puede
    # corregir un mapeo mal hecho sin volver a pedir el archivo.
    assert valores["nivel_educativo"]["valor_crudo"] == "1"


def test_el_mapeo_gana_sobre_las_etiquetas_del_archivo(ctx, conn_boveda,
                                                       educacion):
    """El archivo dice `1=Bajo` y quien carga dice que el `1` es primaria.
    Manda lo que dijo quien carga: para eso se lo preguntó."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "1"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}},
        opciones={"1": "Bajo"})
    assert resultado["demograficos_completados"] == 1
    id_persona = resultado["_gente"][0]
    valores = {v["clave"]: v["valor"]
               for v in atributos.valores_de(conn_boveda, id_persona)}
    assert valores["nivel_educativo"] == "primaria"


def test_un_valor_sin_mapear_deja_a_la_persona_sin_el_atributo(ctx, conn_boveda,
                                                               educacion):
    """Lo que **no** tiene que pasar: que el código crudo entre como si fuera
    una categoría. Eso rompe la aritmética de las cuotas en silencio."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "99"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}})
    assert resultado["demograficos_completados"] == 0

    id_persona = resultado["_gente"][0]
    claves = {v["clave"] for v in atributos.valores_de(conn_boveda, id_persona)}
    assert "nivel_educativo" not in claves


def test_el_valor_sin_mapear_igual_queda_guardado(ctx, conn_boveda, educacion):
    """R-MAP.4 — la persona no tiene el atributo, pero el archivo dijo algo y
    eso se conserva: es lo que permite corregir el mapeo después."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "99"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}})
    id_persona = resultado["_gente"][0]
    from panel_api import db

    crudo = db.una(
        conn_boveda,
        """select pa.valor_crudo, pa.categoria_id
             from persona_atributo pa
             join atributo_demografico a on a.id = pa.atributo_id
            where pa.id_persona = %s and a.clave = 'nivel_educativo'""",
        (id_persona,))
    assert crudo["valor_crudo"] == "99"
    assert crudo["categoria_id"] is None


def test_la_ingesta_informa_mapeados_y_sin_mapear(ctx, conn_boveda, educacion):
    """R-MAP.3 — por atributo, con el conteo de personas afectadas. Sin el
    conteo, «el 99 quedó sin mapear» no alcanza para decidir si se corrige."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "1"},
         {"id_en_origen": "R-2", "P1": "Whisky", "EDU": "99"},
         {"id_en_origen": "R-3", "P1": "Grapa", "EDU": "99"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}})
    informe = resultado["mapeo_de_categorias"]
    assert len(informe) == 1
    assert informe[0]["atributo"] == "nivel_educativo"
    assert informe[0]["mapeados"] == [
        {"valor": "1", "categoria": "primaria", "personas": 1}]
    assert informe[0]["sin_mapear"] == [{"valor": "99", "personas": 2}]


def test_una_categoria_que_ningun_valor_usa_no_es_un_error(ctx, conn_boveda,
                                                           educacion):
    """El catálogo tiene tres niveles y el archivo trae uno. Es lo normal."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "1"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria", "2": "secundaria",
                                        "3": "terciaria"}}})
    assert resultado["demograficos_completados"] == 1
    assert not resultado["mapeo_de_categorias"][0]["sin_mapear"]


def test_una_celda_vacia_no_cuenta_como_valor_sin_mapear(ctx, conn_boveda,
                                                         educacion):
    """Una celda vacía no es un código que alguien se olvidó de mapear."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": ""}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}})
    assert resultado["mapeo_de_categorias"] == []


# ════════════════════════════════════════════════════════════════════
#  R-MAP.5 · Remapear sin recargar
# ════════════════════════════════════════════════════════════════════

def test_se_corrige_el_mapeo_y_se_recalcula_sin_el_archivo(ctx, conn_boveda,
                                                           educacion, actor):
    """El escenario completo: se cargó mal, se corrige, y la gente queda
    bien sin volver a pedirle el `.sav` a nadie."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "9"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}})
    id_persona = resultado["_gente"][0]
    assert "nivel_educativo" not in {
        v["clave"] for v in atributos.valores_de(conn_boveda, id_persona)}

    salida = atributos.recalcular(
        conn_boveda, "nivel_educativo", actor=actor("admin"),
        mapeo={"9": "terciaria"})
    assert salida["recalculados"] == 1
    assert salida["mapeo_aplicado"] == {"9": "terciaria"}

    valores = {v["clave"]: v["valor"]
               for v in atributos.valores_de(conn_boveda, id_persona)}
    assert valores["nivel_educativo"] == "terciaria"


def test_el_recalculo_queda_auditado_con_autor_y_cantidad(conn_boveda,
                                                          educacion, actor):
    atributos.recalcular(conn_boveda, "nivel_educativo", actor=actor("admin"),
                         mapeo={"9": "terciaria"})
    ultima = atributos.auditoria(conn_boveda, educacion["id"])[0]
    assert ultima["accion"] == "recalculo"
    assert ultima["actor_uid"] or ultima["actor_email"]
    assert ultima["detalle"]["mapeo_aplicado"] == {"9": "terciaria"}


def test_el_recalculo_no_inventa_categorias(conn_boveda, educacion, actor):
    with pytest.raises(DatosInvalidos, match="no es una categoría"):
        atributos.recalcular(conn_boveda, "nivel_educativo",
                             actor=actor("admin"), mapeo={"9": "posgrado"})


def test_un_valor_bueno_posterior_completa_la_fila_pendiente(ctx, conn_boveda,
                                                             educacion):
    """Una fila pendiente no es un valor: si después llega uno bueno, tiene
    que completarla, no leerse como una discrepancia contra una nada."""
    resultado = _ingestar(
        ctx, conn_boveda,
        [{"id_en_origen": "R-1", "P1": "Fernet", "EDU": "99"}],
        demograficas={"EDU": {"campo": "nivel_educativo",
                              "mapeo": {"1": "primaria"}}})
    id_persona = resultado["_gente"][0]

    escrito = personas.completar_desde_archivo(
        conn_boveda, id_persona, {"nivel_educativo": "secundaria"},
        origen="ingesta")
    assert escrito["completados"] == ["nivel_educativo"]
    assert not escrito["discrepancias"]
    valores = {v["clave"]: v["valor"]
               for v in atributos.valores_de(conn_boveda, id_persona)}
    assert valores["nivel_educativo"] == "secundaria"


# ════════════════════════════════════════════════════════════════════
#  Lo que la pantalla necesita del análisis
# ════════════════════════════════════════════════════════════════════

def test_el_analisis_devuelve_los_valores_distintos_con_su_conteo():
    """Sin esto la pantalla solo podría ofrecer mapeo para los valores que
    tienen etiqueta, y las variables sin etiquetas son justo las que más lo
    necesitan."""
    import pandas

    datos = pandas.DataFrame({"EDU": [1.0, 1.0, 2.0, None]})
    valores = sav._valores_distintos(datos, ["EDU"])
    assert valores["EDU"] == [{"valor": "1", "filas": 2},
                              {"valor": "2", "filas": 1}]


def test_una_variable_con_demasiados_valores_no_ofrece_mapeo():
    """Media lista invita a mapear la mitad y creer que está completo."""
    import pandas

    datos = pandas.DataFrame(
        {"TXT": [str(n) for n in range(sav.MAX_VALORES_POR_VARIABLE + 5)]})
    assert sav._valores_distintos(datos, ["TXT"])["TXT"] == []
