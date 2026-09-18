"""R3.14 — Atributos demográficos configurables.

Dos mitades, y la segunda es la que más importa:

* **La capacidad nueva**: definir un segmentador desde la app, cargarlo desde
  un archivo, filtrar y fijar cuotas por él.
* **La unificación**: `sexo`, `localidad`, `tramo_etario` y `edad` pasaron a
  vivir en el mismo catálogo. Eso toca código que ya funcionaba —consultas
  demográficas, composición, muestreo, bonos, la ficha—, así que buena parte
  de este archivo son pruebas de **no regresión**: que una consulta por sexo
  devuelva exactamente los mismos individuos que antes, que los objetivos ya
  cargados sigan resolviendo, que `v_demografia` conserve nombre y columnas.
"""

import pytest

from panel_api import (
    atributos, composicion, consultas, db, demografia, encuestas, muestreo,
    paneles, personas, puntos, sav,
)
from panel_api.errores import Conflicto, DatosInvalidos, NoEncontrado

from conftest import VERSION_TEXTO, consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")


# ── Fixtures ─────────────────────────────────────────────────────────

def _persona(conn, documento, nombre=None, **datos):
    return personas.alta(
        conn,
        {
            "persona": {"documento": documento,
                        "nombre": nombre or f"Panelista {documento}", **datos},
            "consentimientos": consentimientos(*AMBAS),
        },
    )["id_persona"]


@pytest.fixture
def nse(conn_boveda, actor):
    """Un atributo definido por un admin, que es el caso de uso de R3.14."""
    return atributos.crear(
        conn_boveda,
        {
            "clave": "nse",
            "etiqueta": "Nivel socioeconómico",
            "tipo": "categorico",
            "categorias": [
                {"clave": "alto", "etiqueta": "Alto", "orden": 10},
                {"clave": "medio", "etiqueta": "Medio", "orden": 20},
                {"clave": "bajo", "etiqueta": "Bajo", "orden": 30},
            ],
        },
        actor=actor("admin"),
    )


# ── R3.14.a · El catálogo ────────────────────────────────────────────

def test_un_admin_define_un_atributo_y_queda_disponible_en_la_carga(
        conn_boveda, nse):
    assert nse["clave"] == "nse"
    assert [c["clave"] for c in nse["categorias"]] == ["alto", "medio", "bajo"]

    # Y aparece como destino válido de una variable del archivo, que es el
    # punto: definirlo alcanza para poder cargarlo, sin tocar código.
    assert "nse" in sav.campos_demograficos(conn_boveda)
    marcado = sav.normalizar_demograficas(
        {"NSE": "nse"}, campos_validos=sav.campos_demograficos(conn_boveda))
    assert marcado == {"NSE": "nse"}


def test_un_atributo_derivado_no_se_puede_inventar_desde_la_app(conn_boveda, actor):
    """El cálculo de un derivado vive en el esquema, no en la app: dejar
    definirlo desde la pantalla prometería algo que después no se calcula."""
    with pytest.raises(DatosInvalidos, match="derivado"):
        atributos.crear(conn_boveda, {"clave": "x", "etiqueta": "X",
                                      "tipo": "derivado"}, actor=actor("admin"))


def test_no_se_puede_repetir_una_clave(conn_boveda, nse, actor):
    with pytest.raises(Conflicto, match="Ya existe"):
        atributos.crear(conn_boveda, {"clave": "nse", "etiqueta": "Otro"},
                        actor=actor("admin"))


def test_la_clave_no_se_cambia_si_ya_hay_datos(conn_boveda, nse, actor):
    """Es lo que guardan los objetivos de composición y los valores de cada
    persona: cambiarla los dejaría apuntando a nada."""
    id_persona = _persona(conn_boveda, "1-1")
    atributos.fijar(conn_boveda, id_persona, "nse", "medio", origen="edicion")

    with pytest.raises(Conflicto, match="no se puede cambiar"):
        atributos.editar(conn_boveda, nse["id"], {"clave": "nivel_socio"},
                         actor=actor("admin"))

    # La etiqueta visible sí.
    editado = atributos.editar(
        conn_boveda, nse["id"], {"etiqueta": "NSE (AMAI)"}, actor=actor("admin"))
    assert editado["etiqueta"] == "NSE (AMAI)"
    assert editado["clave"] == "nse"


def test_un_atributo_con_datos_no_se_borra_solo_se_desactiva(
        conn_boveda, nse, actor):
    id_persona = _persona(conn_boveda, "2-2")
    atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="edicion")

    with pytest.raises(Conflicto, match="se desactiva"):
        atributos.eliminar(conn_boveda, nse["id"], actor=actor("admin"))

    desactivado = atributos.desactivar(conn_boveda, nse["id"], actor=actor("admin"))
    assert desactivado["activo"] is False
    # Deja de ofrecerse en cargas nuevas...
    assert "nse" not in sav.campos_demograficos(conn_boveda)
    # ...y lo cargado se conserva.
    assert any(v["clave"] == "nse"
               for v in atributos.valores_de(conn_boveda, id_persona))


def test_un_atributo_sin_datos_si_se_elimina(conn_boveda, nse, actor):
    salida = atributos.eliminar(conn_boveda, nse["id"], actor=actor("admin"))
    assert salida["estado"] == "eliminado"
    with pytest.raises(NoEncontrado):
        atributos.obtener(conn_boveda, "nse")


def test_los_del_nucleo_no_se_borran_ni_se_desactivan(conn_boveda, actor):
    """Composición, cuotas y muestreo los nombran por su clave: desactivarlos
    rompería todo eso en silencio."""
    sexo = atributos.obtener(conn_boveda, "sexo")
    with pytest.raises(Conflicto, match="núcleo"):
        atributos.eliminar(conn_boveda, sexo["id"], actor=actor("admin"))
    with pytest.raises(Conflicto, match="no se puede desactivar"):
        atributos.desactivar(conn_boveda, sexo["id"], actor=actor("admin"))


def test_toda_accion_sobre_el_catalogo_queda_auditada(conn_boveda, nse, actor):
    atributos.editar(conn_boveda, nse["id"], {"etiqueta": "NSE"},
                     actor=actor("admin", email="ana@equipos.com.uy"))
    registro = atributos.auditoria(conn_boveda, atributo_id=nse["id"])
    acciones = [r["accion"] for r in registro]
    assert "alta" in acciones and "edicion" in acciones
    assert all(r["actor_email"] for r in registro)


def test_un_no_admin_no_administra_el_catalogo(actor):
    """El permiso es propio y solo de admin: definir un atributo decide con
    qué se segmenta al panel entero, y marcar uno como especial toca una
    obligación legal."""
    from panel_api.auth import PERMISOS

    assert PERMISOS["gestionar_atributos"] == {"admin"}
    for rol in ("operaciones", "analista", "dpo"):
        assert not actor(rol).puede("gestionar_atributos")
    assert actor("admin").puede("gestionar_atributos")
    # Leerlo sí puede cualquiera: las pantallas de carga y consulta lo
    # necesitan para armar sus desplegables.
    assert actor("analista").puede("leer")


# ── R3.14.b/c · Valores, canónico y crudo ────────────────────────────

def test_se_guarda_el_valor_canonico_y_el_crudo(conn_boveda, nse):
    id_persona = _persona(conn_boveda, "3-3")
    resultado = atributos.fijar(
        conn_boveda, id_persona, "nse", "Medio", origen="carga")
    assert resultado["estado"] == "completado"
    assert resultado["valor"] == "medio"

    fila = db.una(
        conn_boveda,
        "select valor, valor_crudo, origen from v_atributo_persona "
        "where id_persona = %s and atributo = 'nse'", (id_persona,))
    assert fila["valor"] == "medio"
    assert fila["valor_crudo"] == "Medio"
    assert fila["origen"] == "carga"


def test_un_valor_sin_categoria_no_se_inventa_y_se_informa(conn_boveda, nse):
    """Inventar la categoría es exactamente lo que rompe la aritmética de las
    cuotas: aparecen veinte variantes del mismo nivel escritas distinto."""
    id_persona = _persona(conn_boveda, "4-4")
    resultado = atributos.fijar(conn_boveda, id_persona, "nse", "Medio-alto")
    assert resultado["estado"] == "sin_categoria"
    assert resultado["valor_crudo"] == "Medio-alto"
    assert not [v for v in atributos.valores_de(conn_boveda, id_persona)
                if v["clave"] == "nse"]


def test_un_valor_existente_distinto_no_se_pisa_y_se_informa(conn_boveda, nse):
    id_persona = _persona(conn_boveda, "5-5")
    atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="edicion")

    resultado = atributos.fijar(
        conn_boveda, id_persona, "nse", "bajo", origen="carga", pisar=False)
    assert resultado["estado"] == "discrepancia"
    assert resultado["en_boveda"] == "alto"
    assert resultado["en_el_archivo"] == "bajo"

    vigente = next(v for v in atributos.valores_de(conn_boveda, id_persona)
                   if v["clave"] == "nse")
    assert vigente["valor"] == "alto"


def test_una_persona_tiene_un_solo_valor_vigente_por_atributo(conn_boveda, nse):
    """R3.14.b sigue valiendo, con la precisión que trae R4.1.a: hay un solo
    valor **vigente**, y el anterior queda como historia en vez de perderse.
    Antes del historial esta prueba contaba filas; contarlas ahora sería medir
    justo lo que R4.1.a vino a cambiar."""
    id_persona = _persona(conn_boveda, "6-6")
    atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="edicion")
    atributos.fijar(conn_boveda, id_persona, "nse", "bajo", origen="edicion")
    fila = db.una(
        conn_boveda,
        "select count(*)::int as n from persona_atributo pa "
        "join atributo_demografico a on a.id = pa.atributo_id "
        "where pa.id_persona = %s and a.clave = 'nse' and pa.hasta is null",
        (id_persona,))
    assert fila["n"] == 1

    vigente = next(v for v in atributos.valores_de(conn_boveda, id_persona)
                   if v["clave"] == "nse")
    assert vigente["valor"] == "bajo"


# ── R3.14.h · Corregir un mapeo sin recargar ─────────────────────────

def test_corregido_el_vocabulario_se_recalcula_desde_el_crudo(
        conn_boveda, nse, actor):
    """La salvaguarda de la canonización: si el mapeo salió mal se arregla el
    catálogo y se vuelve a resolver, sin volver a pedir el archivo."""
    id_persona = _persona(conn_boveda, "7-7")
    fallado = atributos.fijar(conn_boveda, id_persona, "nse", "Medio-alto",
                              origen="carga")
    assert fallado["estado"] == "sin_categoria"

    # Se agrega la categoría que faltaba y se recalcula... pero esta persona
    # no llegó a guardar nada, así que primero se carga contra el catálogo ya
    # corregido para tener el crudo.
    atributos.agregar_categoria(
        conn_boveda, nse["id"], {"clave": "medio_alto", "etiqueta": "Medio-alto"},
        actor=actor("admin"))
    atributos.fijar(conn_boveda, id_persona, "nse", "Medio-alto", origen="carga")

    # Ahora el caso real: un valor guardado contra la categoría equivocada.
    otro = _persona(conn_boveda, "8-8")
    atributos.fijar(conn_boveda, otro, "nse", "medio", origen="carga",
                    crudo="Medio-alto")

    salida = atributos.recalcular(conn_boveda, nse["id"], actor=actor("admin"))
    assert salida["recalculados"] == 1
    vigente = next(v for v in atributos.valores_de(conn_boveda, otro)
                   if v["clave"] == "nse")
    assert vigente["valor"] == "medio_alto"

    # Y queda registrado con autor y cantidad.
    assert any(r["accion"] == "recalculo"
               for r in atributos.auditoria(conn_boveda, atributo_id=nse["id"]))


# ── R3.14.d · Consultas, composición y cuotas ────────────────────────

def test_una_consulta_demografica_filtra_por_un_atributo_del_catalogo(
        ctx_solo_boveda, conn_boveda, nse):
    alto = _persona(conn_boveda, "9-1")
    bajo = _persona(conn_boveda, "9-2")
    atributos.fijar(conn_boveda, alto, "nse", "alto", origen="edicion")
    atributos.fijar(conn_boveda, bajo, "nse", "bajo", origen="edicion")

    resultado = consultas.ejecutar(
        ctx_solo_boveda,
        {"criterios": [{"tipo": "demografico", "dimension": "nse",
                        "operador": "eq", "valor": "alto"}]},
    )
    ids = [i["id_persona"] for i in resultado["items"]]
    assert ids == [alto]


def test_quien_no_tiene_el_dato_no_entra_en_ningun_filtro(
        ctx_solo_boveda, conn_boveda, nse):
    """«Sin dato» no es una categoría más: no se lo puede contar como alto ni
    como no-alto, porque no se sabe."""
    alto = _persona(conn_boveda, "10-1")
    sin_dato = _persona(conn_boveda, "10-2")
    atributos.fijar(conn_boveda, alto, "nse", "alto", origen="edicion")

    positiva = consultas.ejecutar(ctx_solo_boveda, {"criterios": [
        {"dimension": "nse", "operador": "eq", "valor": "alto"}]})
    negativa = consultas.ejecutar(ctx_solo_boveda, {"criterios": [
        {"dimension": "nse", "operador": "ne", "valor": "alto"}]})

    assert [i["id_persona"] for i in positiva["items"]] == [alto]
    assert sin_dato not in [i["id_persona"] for i in negativa["items"]]


def test_una_dimension_desconocida_se_rechaza_nombrando_las_validas(
        ctx_solo_boveda, conn_boveda, nse):
    with pytest.raises(DatosInvalidos) as error:
        consultas.ejecutar(ctx_solo_boveda, {"criterios": [
            {"dimension": "signo_zodiacal", "operador": "eq", "valor": "aries"}]})
    assert "nse" in error.value.detalle["dimensiones_validas"]


def test_se_puede_fijar_una_cuota_por_un_atributo_y_ver_su_brecha(
        conn_boveda, nse):
    panel = paneles.crear(conn_boveda, "Panel")
    for i, categoria in enumerate(["alto", "alto", "bajo"]):
        id_persona = _persona(conn_boveda, f"11-{i}")
        atributos.fijar(conn_boveda, id_persona, "nse", categoria, origen="edicion")
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    composicion.guardar_objetivo(conn_boveda, panel["id"], [
        {"dimension": "nse", "categoria": "alto", "proporcion": 0.3},
        {"dimension": "nse", "categoria": "medio", "proporcion": 0.4},
        {"dimension": "nse", "categoria": "bajo", "proporcion": 0.3},
    ])
    salida = composicion.composicion(conn_boveda, panel["id"], dimensiones=["nse"])
    dimension = salida["dimensiones"][0]
    assert dimension["brecha_disponible"] is True
    por_categoria = {c["categoria"]: c for c in dimension["categorias"]}
    assert por_categoria["alto"]["observados"] == 2
    # Falta el segmento «medio» entero: es la brecha que el muestreo prioriza.
    assert por_categoria["medio"]["observados"] == 0
    assert por_categoria["medio"]["brecha"] == -0.4


def test_los_sin_dato_se_informan_aparte_y_no_inflan_ninguna_categoria(
        conn_boveda, nse):
    """Si los sin dato engrosaran una categoría real, la brecha de esa
    categoría mentiría; y si contaran en el denominador, todas las
    proporciones bajarían y el panel parecería peor de lo que es."""
    panel = paneles.crear(conn_boveda, "Panel")
    for i, categoria in enumerate(["alto", "bajo", None, None]):
        id_persona = _persona(conn_boveda, f"12-{i}")
        if categoria:
            atributos.fijar(conn_boveda, id_persona, "nse", categoria,
                            origen="edicion")
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    dimension = composicion.composicion(
        conn_boveda, panel["id"], dimensiones=["nse"])["dimensiones"][0]

    assert dimension["sin_dato"] == 2
    assert dimension["con_dato"] == 2
    assert composicion.SIN_DATO not in [c["categoria"] for c in dimension["categorias"]]
    # Dos de cuatro miembros tienen el dato, y entre ellos «alto» es la mitad.
    por_categoria = {c["categoria"]: c for c in dimension["categorias"]}
    assert por_categoria["alto"]["proporcion_observada"] == 0.5
    assert dimension["aviso_sin_dato"]


def test_el_muestreo_prioriza_una_brecha_de_un_atributo_del_catalogo(
        conn_boveda, nse):
    """R3.14.f — el muestreo se construyó sobre el catálogo, así que una cuota
    por un atributo definido por el usuario funciona igual que una por sexo."""
    panel = paneles.crear(conn_boveda, "Panel")
    for i, categoria in enumerate(["alto", "alto", "alto", "bajo"]):
        id_persona = _persona(conn_boveda, f"13-{i}")
        atributos.fijar(conn_boveda, id_persona, "nse", categoria, origen="edicion")
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    composicion.guardar_objetivo(conn_boveda, panel["id"], [
        {"dimension": "nse", "categoria": "alto", "proporcion": 0.25},
        {"dimension": "nse", "categoria": "bajo", "proporcion": 0.75},
    ])
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")

    propuesta = muestreo.proponer(
        conn_boveda, encuesta["id"], dimension="nse", cantidad=1)
    assert propuesta["propuesta"][0]["categoria"] == "bajo"


def test_un_bono_puede_dirigirse_a_un_atributo_del_catalogo(conn_boveda, nse):
    panel = paneles.crear(conn_boveda, "Panel")
    bono = puntos.crear_bono(conn_boveda, panel["id"], "nse", "bajo", 50)
    assert bono["dimension"] == "nse"


# ── R3.14.e · Categorías especiales ──────────────────────────────────

def test_marcar_un_atributo_como_especial_advierte_y_lo_separa(
        conn_boveda, actor):
    religion = atributos.crear(
        conn_boveda,
        {"clave": "religion", "etiqueta": "Religión", "es_especial": True,
         "categorias": [{"clave": "catolica", "etiqueta": "Católica"}]},
        actor=actor("admin"))
    assert religion["es_especial"] is True
    assert "18.331" in religion["advertencia"]

    # Se lista aparte: quien revisa cumplimiento tiene que poder ver que
    # existe sin recorrer el catálogo entero.
    sin_especiales = atributos.listar(conn_boveda, incluir_especiales=False)
    assert "religion" not in [a["clave"] for a in sin_especiales]


def test_un_atributo_especial_no_sale_en_las_exportaciones_con_datos(
        conn_boveda, actor):
    """La lista de campos de la exportación es fija: que el catálogo crezca no
    puede hacer crecer solo lo que sale del sistema en un archivo."""
    atributos.crear(
        conn_boveda,
        {"clave": "salud", "etiqueta": "Condición de salud", "es_especial": True,
         "categorias": [{"clave": "cronica", "etiqueta": "Crónica"}]},
        actor=actor("admin"))
    assert "salud" not in consultas.CAMPOS_IDENTIFICADOS
    assert "salud" not in encuestas.CAMPOS_MUESTRA_CON_CONTACTO


def test_los_valores_especiales_se_pueden_ocultar_de_la_ficha(
        conn_boveda, actor):
    atributos.crear(
        conn_boveda,
        {"clave": "ideologia", "etiqueta": "Ideología", "es_especial": True,
         "categorias": [{"clave": "a", "etiqueta": "A"}]},
        actor=actor("admin"))
    id_persona = _persona(conn_boveda, "14-1")
    atributos.fijar(conn_boveda, id_persona, "ideologia", "a", origen="edicion")

    con = atributos.valores_de(conn_boveda, id_persona, incluir_especiales=True)
    sin = atributos.valores_de(conn_boveda, id_persona, incluir_especiales=False)
    assert "ideologia" in [v["clave"] for v in con]
    assert "ideologia" not in [v["clave"] for v in sin]


# ── R3.14.f · La unificación, y su no regresión ──────────────────────

def test_el_nucleo_existe_en_el_catalogo_tras_la_migracion(conn_boveda):
    claves = {a["clave"] for a in atributos.listar(conn_boveda)}
    assert {"sexo", "localidad", "tramo_etario", "edad"} <= claves

    sexo = atributos.obtener(conn_boveda, "sexo")
    assert {c["clave"] for c in sexo["categorias"]} == {"F", "M", "X"}
    tramo = atributos.obtener(conn_boveda, "tramo_etario")
    assert "65+" in {c["clave"] for c in tramo["categorias"]}
    localidad = atributos.obtener(conn_boveda, "localidad")
    assert "Montevideo" in {c["clave"] for c in localidad["categorias"]}


def test_v_demografia_conserva_nombre_y_columnas(conn_boveda):
    """Es lo que permite que composición, participación, exportaciones y ficha
    sigan andando sin tocarlas durante la transición."""
    columnas = {f["column_name"] for f in db.todas(
        conn_boveda,
        "select column_name from information_schema.columns "
        "where table_name = 'v_demografia'")}
    assert columnas == {"id_persona", "sexo", "localidad", "edad", "tramo_etario"}


def test_una_consulta_por_sexo_devuelve_los_mismos_de_siempre(
        ctx_solo_boveda, conn_boveda):
    """La prueba de no regresión de la unificación: lo que antes resolvía
    `persona.sexo` ahora lo resuelve el catálogo, y tiene que dar igual."""
    mujer = _persona(conn_boveda, "15-1", sexo="F", localidad="Montevideo")
    varon = _persona(conn_boveda, "15-2", sexo="M", localidad="Salto")

    por_sexo = consultas.ejecutar(ctx_solo_boveda, {"criterios": [
        {"dimension": "sexo", "operador": "eq", "valor": "F"}]})
    por_localidad = consultas.ejecutar(ctx_solo_boveda, {"criterios": [
        {"dimension": "localidad", "operador": "eq", "valor": "Salto"}]})

    assert [i["id_persona"] for i in por_sexo["items"]] == [mujer]
    assert [i["id_persona"] for i in por_localidad["items"]] == [varon]
    # Y la consulta demográfica sigue sin abrir el store semántico (R2.4).
    assert por_sexo["abrio_semantica"] is False


def test_la_ficha_sigue_mostrando_sexo_y_localidad_donde_siempre(conn_boveda):
    id_persona = _persona(conn_boveda, "16-1", sexo="F", localidad="Colonia")
    ficha = personas.ficha(conn_boveda, id_persona)
    assert ficha["persona"]["sexo"] == "F"
    assert ficha["persona"]["localidad"] == "Colonia"
    # Y ahora además trae el catálogo completo.
    assert "sexo" in [a["clave"] for a in ficha["atributos"]]


def test_un_objetivo_ya_cargado_sigue_resolviendo_contra_las_mismas_claves(
        conn_boveda):
    panel = paneles.crear(conn_boveda, "Panel")
    for i, sexo in enumerate(["F", "F", "M"]):
        paneles.agregar_miembro(
            conn_boveda, panel["id"], _persona(conn_boveda, f"17-{i}", sexo=sexo))
    # Un objetivo escrito con las claves de siempre.
    db.ejecutar(
        conn_boveda,
        "insert into objetivo_composicion (panel_id, dimension, categoria, "
        "proporcion_objetivo) values (%s,'sexo','F',0.5), (%s,'sexo','M',0.5)",
        (panel["id"], panel["id"]))

    dimension = composicion.composicion(
        conn_boveda, panel["id"], dimensiones=["sexo"])["dimensiones"][0]
    por_categoria = {c["categoria"]: c for c in dimension["categorias"]}
    assert por_categoria["F"]["observados"] == 2
    assert por_categoria["M"]["observados"] == 1


def test_el_alta_rechaza_una_localidad_que_no_esta_en_el_catalogo(conn_boveda):
    """Es el costo de canonizar, y es deliberado: sin vocabulario cerrado las
    cuotas geográficas no cierran. El alta avisa en el momento en vez de
    guardar una variante nueva en silencio."""
    resultado = personas.alta(
        conn_boveda,
        {"persona": {"documento": "18-1", "nombre": "Sin localidad",
                     "localidad": "Ciudad de la Costa"},
         "consentimientos": consentimientos(*AMBAS)})
    assert resultado["estado"] == "creada"
    sin_guardar = {c["clave"] for c in resultado["atributos_sin_guardar"]}
    assert sin_guardar == {"localidad"}


# ── R3.14.g · Edad sin fecha de nacimiento ───────────────────────────

def test_con_fecha_de_nacimiento_el_tramo_se_deriva_de_ella(conn_boveda):
    id_persona = _persona(conn_boveda, "19-1", fecha_nacimiento="1990-05-10")
    # Y ningún valor cargado la reemplaza: la fecha gana siempre.
    atributos.fijar(conn_boveda, id_persona, "edad", 20, origen="carga",
                    fecha_referencia="2026-01-01")

    ficha = personas.ficha(conn_boveda, id_persona)
    assert ficha["demografia"]["edad"] >= 35
    assert ficha["demografia"]["procedencia_tramo"] == "derivado"


def test_una_edad_declarada_se_envejece_desde_su_fecha_de_referencia(conn_boveda):
    """Un panel vive años. Con el valor congelado, alguien cargado como
    «25-34» en 2019 seguiría contando ahí, y la cuota se calcularía sobre una
    edad que ya no es."""
    id_persona = _persona(conn_boveda, "20-1")
    atributos.fijar(conn_boveda, id_persona, "edad", 32, origen="carga",
                    fecha_referencia="2019-06-01")

    ficha = personas.ficha(conn_boveda, id_persona)
    assert ficha["demografia"]["edad"] > 32
    assert ficha["demografia"]["tramo_etario"] == "35-44"
    assert ficha["demografia"]["procedencia_tramo"] == "envejecido"


def test_un_tramo_cargado_a_mano_es_el_ultimo_recurso(conn_boveda):
    id_persona = _persona(conn_boveda, "21-1")
    atributos.fijar(conn_boveda, id_persona, "tramo_etario", "45-54",
                    origen="carga")
    ficha = personas.ficha(conn_boveda, id_persona)
    assert ficha["demografia"]["tramo_etario"] == "45-54"
    assert ficha["demografia"]["edad"] is None
    assert ficha["demografia"]["procedencia_tramo"] == "cargado"


def test_obtener_despues_la_fecha_de_nacimiento_manda_sin_recargar_nada(
        conn_boveda):
    id_persona = _persona(conn_boveda, "22-1")
    atributos.fijar(conn_boveda, id_persona, "edad", 70, origen="carga",
                    fecha_referencia="2020-01-01")
    assert personas.ficha(conn_boveda, id_persona)["demografia"]["tramo_etario"] == "65+"

    personas.editar(conn_boveda, id_persona, {"fecha_nacimiento": "2000-03-01"})
    ficha = personas.ficha(conn_boveda, id_persona)
    assert ficha["demografia"]["procedencia_tramo"] == "derivado"
    assert ficha["demografia"]["tramo_etario"] == "25-34"


def test_sin_fecha_sin_edad_y_sin_tramo_queda_sin_dato(conn_boveda):
    """Y sin dato no se cuenta en ninguna categoría etaria: el muestreo no
    puede usarlo para cerrar una brecha de edad porque no sabe dónde va."""
    panel = paneles.crear(conn_boveda, "Panel")
    id_persona = _persona(conn_boveda, "23-1")
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)

    ficha = personas.ficha(conn_boveda, id_persona)
    assert ficha["demografia"]["tramo_etario"] is None
    assert ficha["demografia"]["procedencia_tramo"] is None

    dimension = composicion.composicion(
        conn_boveda, panel["id"], dimensiones=["tramo_etario"])["dimensiones"][0]
    assert dimension["sin_dato"] == 1
    assert dimension["categorias"] == []


# ── El guardrail de siempre ──────────────────────────────────────────

def test_ningun_valor_de_atributo_llega_al_store_semantico(
        ctx, conn_boveda, conn_semantica, nse):
    """Regla dura #1: los segmentadores son autoritativos en la bóveda y el
    store semántico no los tiene. Que ahora sean configurables no cambia de
    qué lado del muro viven."""
    panel = paneles.crear(conn_boveda, "Panel")
    id_persona = _persona(conn_boveda, "24-1")
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    atributos.fijar(conn_boveda, id_persona, "nse", "alto", origen="edicion")
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    from panel_api import dedup
    dedup.registrar_alias(conn_boveda, id_persona, "dooblo", "R-1")

    encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"],
        [{"codigo": "P1", "texto": "¿Qué bebida prefiere?", "tipo": "abierta"},
         {"codigo": "NSE", "texto": "Nivel socioeconómico", "tipo": "cerrada",
          "opciones": {"1": "Alto"}}],
        [{"id_en_origen": "R-1", "P1": "Fernet", "NSE": "1"}],
        origen="dooblo",
        proveedor=ctx.embeddings,
        demograficas={"NSE": "nse"},
    )

    textos = [f["texto_embebido"] for f in db.todas(
        conn_semantica, "select texto_embebido from respuesta")]
    assert all("Alto" not in (t or "") for t in textos)
    assert all("socioeconómico" not in (t or "").lower() for t in textos)
    # Y el valor sí llegó a la bóveda.
    assert next(v for v in atributos.valores_de(conn_boveda, id_persona)
                if v["clave"] == "nse")["valor"] == "alto"


def test_la_carga_informa_los_valores_que_no_matchearon_ninguna_categoria(
        ctx, conn_boveda, nse):
    """Sin este informe, una carga con el vocabulario mal alineado deja a
    media base sin el segmentador y nadie se entera."""
    panel = paneles.crear(conn_boveda, "Panel")
    id_persona = _persona(conn_boveda, "25-1")
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola")
    from panel_api import dedup
    dedup.registrar_alias(conn_boveda, id_persona, "dooblo", "R-1")

    resultado = encuestas.ingestar(
        ctx.boveda, ctx.semantica, encuesta["id"],
        [{"codigo": "P1", "texto": "¿Qué bebida prefiere?", "tipo": "abierta"},
         {"codigo": "NSE", "texto": "NSE", "tipo": "cerrada",
          "opciones": {"1": "Medio-alto"}}],
        [{"id_en_origen": "R-1", "P1": "Fernet", "NSE": "1"}],
        origen="dooblo",
        proveedor=ctx.embeddings,
        demograficas={"NSE": "nse"},
    )
    sin_categoria = resultado["valores_sin_categoria"]
    assert sin_categoria
    assert sin_categoria[0]["atributo"] == "nse"
    assert "Medio-alto" in sin_categoria[0]["valores"]
