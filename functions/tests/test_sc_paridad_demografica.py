"""SC — Paridad de acciones entre consulta demográfica y semántica.

La pantalla de consultas resolvía los dos tipos, pero el resultado
demográfico salía por un `return` temprano y nunca llegaba a la barra de
acciones: ficha, columnas, CSV, reidentificar, CSV con datos y crear panel
existían solo para la semántica —el camino menos transitado—. Y además el
resultado demográfico traía nombre y correo en claro, sin que quedara
registrada ninguna reidentificación.

Dos mitades:

* **El backend** acepta un conjunto de `id_persona` venga de donde venga:
  columnas, CSV, reidentificación, CSV con datos y panel desde el resultado
  se prueban acá con un resultado demográfico de verdad.
* **La pantalla** usa la misma barra para los dos tipos. No hay navegador
  en las pruebas, así que se comprueba sobre el fuente de `consultas.js`,
  con el mismo método que `test_rutas_con_pantalla.py`: lo que se mira es
  que la estructura que causó el bug no pueda volver.
"""

import csv
import io
import pathlib
import re

import pytest

from panel_api import consultas, db, ficha, ruteo

from corpus import nombres, sembrar

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CONSULTAS_JS = (RAIZ / "web" / "public" / "js" / "paginas" / "consultas.js")

DEMOGRAFICA = {"criterios": [{"tipo": "demografico", "dimension": "sexo",
                              "operador": "eq", "valor": "F"}]}
SEMANTICA = {"criterios": [{"tipo": "semantico",
                            "texto": "le gusta el fernet"}]}


@pytest.fixture
def corpus(conn_boveda, conn_semantica, proveedor):
    contexto = sembrar(conn_boveda, conn_semantica, proveedor)
    conn_boveda.commit()
    conn_semantica.commit()
    return contexto


def _correr(ctx, actor, definicion, **extra):
    return ruteo.despachar("POST", "/consultas", {**definicion, **extra}, {},
                           actor("analista"), ctx)[1]


def _reidentificaciones(conn, motivo):
    return db.una(conn, "select count(*)::int as n from reidentificacion "
                        "where motivo = %s", (motivo,))["n"]


# ════════════════════════════════════════════════════════════════════
#  Backend · lo que las acciones necesitan de un resultado demográfico
# ════════════════════════════════════════════════════════════════════

def test_el_resultado_demografico_es_seudonimo(ctx, actor, corpus):
    """Como el semántico: ver quiénes son es otra acción, y queda registrada."""
    resultado = _correr(ctx, actor, DEMOGRAFICA)

    assert resultado["tipo"] == "demografica"
    assert resultado["seudonimo"] is True
    assert resultado["items"]
    for item in resultado["items"]:
        assert not set(item) & {"nombre", "email", "documento", "celular"}
    # Y ningún valor de PII se cuela por otro campo.
    texto = repr(resultado["items"])
    for nombre in nombres(resultado, corpus):
        assert nombre not in texto


def test_no_trae_puntaje_ni_evidencia_ni_veredicto(ctx, actor, corpus):
    """Las diferencias legítimas se conservan: no se inventan columnas."""
    resultado = _correr(ctx, actor, DEMOGRAFICA)
    for item in resultado["items"]:
        assert not set(item) & {"puntaje", "evidencias", "criterios", "confianza"}
    assert resultado["degradaciones"] == []


def test_las_columnas_se_resuelven_sobre_el_conjunto_sin_reejecutar(
        ctx_solo_boveda, actor, corpus):
    """`/resultados/atributos` con los ids del resultado demográfico. El
    contexto explota si alguien abre el store semántico: elegir columnas no
    corre ninguna consulta semántica."""
    resultado = consultas.ejecutar(ctx_solo_boveda, DEMOGRAFICA)
    ids = [i["id_persona"] for i in resultado["items"]]

    _, salida = ruteo.despachar("POST", "/resultados/atributos",
                                {"ids_persona": ids}, {}, actor("analista"),
                                ctx_solo_boveda)

    assert set(salida["items"]) == set(ids)
    assert all(v["sexo"]["valor"] == "F" for v in salida["items"].values())


def test_el_csv_seudonimizado_tiene_el_mismo_contrato(ctx, actor, corpus):
    salida = _correr(ctx, actor, DEMOGRAFICA, formato="csv")
    filas = list(csv.reader(io.StringIO(salida["csv"])))

    assert filas[0] == ["id_persona", "puntaje", "evidencia"]
    assert len(filas) - 1 == salida["filas"]
    # Sin puntaje ni evidencia: celdas vacías, no un cero inventado.
    assert all(f[1] == "" and f[2] == "" for f in filas[1:])
    for nombre in nombres(_correr(ctx, actor, DEMOGRAFICA), corpus):
        assert nombre not in salida["csv"]


def test_reidentificar_queda_registrado_y_habilita_el_csv_con_datos(
        ctx, actor, corpus):
    resultado = _correr(ctx, actor, DEMOGRAFICA)
    ids = [i["id_persona"] for i in resultado["items"]]

    _, resuelto = ruteo.despachar(
        "POST", "/reidentificacion", {"ids_persona": ids, "motivo": "consulta"},
        {}, actor("operaciones"), ctx)
    assert {p["nombre"] for p in resuelto["items"]} == set(nombres(resultado, corpus))
    assert _reidentificaciones(ctx.boveda, "consulta") == len(ids)

    # Sin la reidentificación no hay CSV con datos: no es un segundo camino.
    with pytest.raises(Exception, match="reidentificado"):
        ruteo.despachar("POST", "/consultas/csv-identificado",
                        {"resultado": resultado}, {}, actor("admin"), ctx)

    _, exportado = ruteo.despachar(
        "POST", "/consultas/csv-identificado",
        {"reidentificacion": resuelto, "resultado": resultado}, {},
        actor("admin"), ctx)
    assert exportado["personas"] == len(ids)
    # Con motivo propio, distinto de haberla visto en pantalla.
    assert _reidentificaciones(ctx.boveda, "exportacion") == len(ids)


def test_crear_panel_desde_el_resultado_es_idempotente_y_registra_su_origen(
        ctx, actor, corpus):
    resultado = _correr(ctx, actor, DEMOGRAFICA)
    ids = [i["id_persona"] for i in resultado["items"]]
    # El mismo individuo dos veces no da dos membresías.
    repetido = {**resultado, "items": resultado["items"] + resultado["items"][:1]}

    _, panel = ruteo.despachar(
        "POST", "/paneles/desde-consulta",
        {"nombre": "Mujeres del panel", "resultado": repetido,
         "definicion": DEMOGRAFICA}, {}, actor("operaciones"), ctx)

    assert panel["altas"] == len(ids)
    assert panel["registrado_como_reidentificacion"] is True
    fila = db.una(ctx.boveda,
                  "select origen, origen_definicion from panel where id = %s",
                  (panel["id"],))
    assert fila["origen"] == "consulta"
    assert fila["origen_definicion"]["criterios"] == DEMOGRAFICA["criterios"]
    miembros = db.una(ctx.boveda, "select count(*)::int as n from membresia "
                                  "where panel_id = %s", (panel["id"],))["n"]
    assert miembros == len(ids)


def test_la_ficha_de_un_resultado_demografico_no_trae_evidencia(
        ctx_solo_boveda, corpus):
    """Sin `respuestas` la ficha no abre el store semántico ni inventa una
    sección de evidencia vacía."""
    resultado = consultas.ejecutar(ctx_solo_boveda, DEMOGRAFICA)
    salida = ficha.seudonima(ctx_solo_boveda.boveda,
                             resultado["items"][0]["id_persona"])
    assert "evidencia" not in salida
    assert not set(salida) & {"nombre", "documento", "email", "celular"}


def test_la_consulta_semantica_sigue_igual(ctx, actor, corpus):
    """No regresión: puntaje, criterios y evidencia siguen viniendo."""
    resultado = _correr(ctx, actor, SEMANTICA)
    assert resultado.get("tipo") != "demografica"
    assert resultado["items"]
    assert all({"puntaje", "criterios", "evidencias"} <= set(i)
               for i in resultado["items"])
    salida = _correr(ctx, actor, SEMANTICA, formato="csv")
    filas = list(csv.reader(io.StringIO(salida["csv"])))
    assert filas[0] == ["id_persona", "puntaje", "evidencia"]
    assert all(f[1] for f in filas[1:])


# ════════════════════════════════════════════════════════════════════
#  Pantalla · una sola barra para los dos tipos
# ════════════════════════════════════════════════════════════════════

FUENTE = CONSULTAS_JS.read_text(encoding="utf-8")
ACCIONES = ("ver-nombres", "bajar-csv", "bajar-csv-pii", "elegir-columnas",
            "crear-panel")


def _funcion(nombre):
    """El cuerpo de una función de nivel superior del módulo."""
    inicio = re.search(rf"(?m)^(?:async )?function {nombre}\(", FUENTE)
    assert inicio, f"no está la función {nombre}"
    resto = FUENTE[inicio.end():]
    fin = re.search(r"(?m)^(?:async )?function |^/\* ── ", resto)
    return resto[:fin.start()] if fin else resto


def test_la_demografica_no_sale_antes_de_la_barra_de_acciones():
    """El bug era un `return` dentro del `if (… 'demografica')` de
    `pintarResultado`, antes de armar los botones."""
    cuerpo = _funcion("pintarResultado")
    assert "barraDeAcciones(resultado)" in cuerpo
    assert "engancharAcciones(caja, resultado)" in cuerpo
    assert not re.search(r"demografica'\)\s*\{[^}]*return", cuerpo), (
        "pintarResultado vuelve a salir antes de la barra para la demográfica")


def test_las_acciones_existen_una_sola_vez():
    """Duplicar los botones en cada tabla es lo que garantiza que la próxima
    acción vuelva a quedar de un solo lado."""
    for accion in ACCIONES:
        assert FUENTE.count(f'id="{accion}"') == 1, accion
        assert f"'#{accion}', caja" in _funcion("engancharAcciones"), accion
    assert "id=\"ver-nombres\"" in _funcion("barraDeAcciones")


def test_cada_fila_demografica_abre_la_ficha():
    assert "data-ficha=" in _funcion("filaDemografica")
    assert "[data-ficha]" in _funcion("engancharAcciones")


def test_la_tabla_demografica_no_muestra_puntaje_evidencia_ni_veredicto():
    tabla = _funcion("tablaDemografica") + _funcion("filaDemografica")
    for columna in ("Puntaje", "Confianza", "Criterios", "Evidencia",
                    "VEREDICTOS", "puntaje"):
        assert columna not in tabla, columna


def test_la_tabla_demografica_no_pinta_pii_del_resultado():
    """El nombre aparece solo después de «Ver quiénes son»: sale de
    `nombresResueltos`, nunca del ítem."""
    fila = _funcion("filaDemografica")
    assert "item.nombre" not in fila and "item.email" not in fila
    assert "nombresResueltos[item.id_persona]" in fila


def test_la_ficha_de_un_resultado_demografico_no_muestra_evidencia():
    cuerpo = _funcion("fichaHtml")
    assert "conEvidencia" in cuerpo
    assert re.search(r"conEvidencia \? `<h4[^`]*Por qué aparece", cuerpo)


def test_degradaciones_puente_y_verificacion_siguen_siendo_del_semantico():
    cuerpo = _funcion("pintarResultado")
    rama_semantica = cuerpo.split("esDemografica ? avisoDemografica(resultado) :", 1)[1]
    for funcion in ("pintarVerificacionIncompleta", "pintarDegradaciones",
                    "pintarPuente"):
        assert funcion in rama_semantica.split("<div class=\"card\">", 1)[0]
    assert "esDemografica ? '' : `${pintarExcluidos(resultado)}" in cuerpo
