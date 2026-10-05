"""BUG — la validación de claves de dedup no reconocía el correo y bloqueaba.

`specs/BUG_validacion_dedup_bloquea.md`. Tres defectos en el mismo punto:

1. **El correo desaparecía antes de llegar a la revisión.** El diagnóstico de
   la Fase 8 marcaba la columna de correo como «datos personales en texto
   libre» aunque estuviera marcada como `email` —o sea, aunque fuera a la
   bóveda y no se embebiera—, y ofrecía «Excluir la variable». Esa acción
   borraba la fila con su marcado, y la revisión decía, con razón respecto de
   lo que recibió, que no había clave. Ahora el panel no le propone nada a
   una demográfica, y ninguna propuesta puede quitar una fila demográfica.
2. **Advertir no es bloquear.** Sin clave de dedup la carga se puede ejecutar
   igual, con dos salidas explícitas, y queda constancia en el resultado.
3. **Bloquear en silencio parece un cuelgue.** El motivo de lo que frena se
   escribe junto al botón, y ninguna acción queda sin respuesta visible.

Las pruebas de pantalla son estáticas, como las de `test_rutas_con_pantalla`:
el CI no levanta un navegador. Lo que verifican es la forma del código que
hace imposible el defecto —el aviso junto al botón, el pie oculto durante la
revisión—, y el recorrido entero se verificó en el navegador al corregirlo.
"""

import pathlib
import re

import pytest

from panel_api import db, diferida, encuestas, paneles, resumen_ingesta, ruteo
from panel_api.errores import DatosInvalidos

from test_fase7_revision import _cuerpo, _filas, _plan, _sav

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ENCUESTAS_JS = (RAIZ / "web/public/js/paginas/encuestas.js").read_text(encoding="utf-8")
CALIDAD_JS = (RAIZ / "web/public/js/calidad.js").read_text(encoding="utf-8")


def _tipos(r):
    return {a["tipo"] for a in r["advertencias"]}


def _cuerpo_de_la_funcion(fuente, nombre):
    """El texto de `function nombre(...) {...}`, contando llaves."""
    inicio = re.search(rf"(async )?function {nombre}\(", fuente)
    assert inicio, nombre
    # Primero la lista de parámetros, que puede desestructurar con llaves.
    nivel, k = 1, inicio.end()
    while nivel:
        nivel += {"(": 1, ")": -1}.get(fuente[k], 0)
        k += 1
    i = fuente.index("{", k)
    nivel = 0
    for j in range(i, len(fuente)):
        nivel += {"{": 1, "}": -1}.get(fuente[j], 0)
        if nivel == 0:
            return fuente[i:j + 1]
    raise AssertionError(f"no cierra {nombre}")


# ════════════════════════════════════════════════════════════════════
#  Defecto 1 — las tres claves, en los términos del dedup
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("demograficas, columnas", [
    ({"MAIL": {"campo": "email", "mapeo": {}}}, {"MAIL": "p{i}@ejemplo.invalid"}),
    ({"DOC": {"campo": "documento", "mapeo": {}}}, {"DOC": "4{i:06d}-1"}),
    ({"NOM": {"campo": "nombre", "mapeo": {}},
      "FNAC": {"campo": "fecha_nacimiento", "mapeo": {}}},
     {"NOM": "Persona {i}", "FNAC": "1990-01-{d:02d}"}),
], ids=["email", "documento", "nombre+fecha"])
def test_cada_clave_del_dedup_cuenta_como_clave(demograficas, columnas):
    filas = [{"ID": f"r{i}", "P1": "Fernet",
              **{c: v.format(i=i, d=1 + i % 28) for c, v in columnas.items()}}
             for i in range(12)]
    r = resumen_ingesta.resumir(_plan(demograficas=demograficas), filas)
    assert "sin_clave_de_dedup" not in _tipos(r)
    assert r["sin_clave_de_dedup"] is False
    assert r["dedup"], r["dedup"]


def test_una_clave_parcial_cuenta_y_se_informa_su_cobertura():
    """«email: 300 de 1131 filas»: una clave con vacíos sirve, deduplica a
    los que la traen, y no se cuenta como inexistente."""
    filas = [{"ID": f"r{i}", "P1": "Fernet",
              "MAIL": f"p{i}@ejemplo.invalid" if i < 300 else ""}
             for i in range(1131)]
    r = resumen_ingesta.resumir(
        _plan(demograficas={"MAIL": {"campo": "email", "mapeo": {}}}), filas)
    assert "sin_clave_de_dedup" not in _tipos(r)
    email = next(c for c in r["dedup"] if c["clave"] == "email")
    assert email["filas_con_valor"] == 300
    assert email["filas_total"] == 1131
    assert email["cobertura"] == "300 de 1131 filas"


def test_una_clave_marcada_sin_ningun_valor_no_cuenta_y_el_mensaje_la_nombra():
    """El mensaje y la condición dicen lo mismo: si el correo está marcado
    pero vacío en todas las filas, el dedup no tiene con qué comparar, y el
    aviso dice que está marcado —no que no existe—."""
    filas = [{"ID": f"r{i}", "P1": "Fernet", "MAIL": ""} for i in range(12)]
    r = resumen_ingesta.resumir(
        _plan(demograficas={"MAIL": {"campo": "email", "mapeo": {}}}), filas)
    aviso = next(a for a in r["advertencias"] if a["tipo"] == "sin_clave_de_dedup")
    assert "«email» está(n) marcada(s)" in aviso["mensaje"]


def test_el_mensaje_nombra_las_mismas_claves_que_mira_la_condicion():
    r = resumen_ingesta.resumir(
        _plan(demograficas={"NOM": {"campo": "nombre", "mapeo": {}}}), _filas(12))
    aviso = next(a for a in r["advertencias"] if a["tipo"] == "sin_clave_de_dedup")
    for nombre in ("documento", "correo", "celular",
                   "nombre con fecha de nacimiento"):
        assert nombre in aviso["mensaje"]
    assert resumen_ingesta.CLAVES_SIMPLES == ("documento", "email", "celular")
    assert resumen_ingesta.CLAVE_COMPUESTA == ("nombre", "fecha_nacimiento")


def test_el_marcado_en_la_forma_vieja_tambien_se_reconoce():
    """La ruta de filas (`.csv`, `.xlsx`) manda el marcado como viene:
    `{"MAIL": "email"}`. Antes eso reventaba el resumen."""
    filas = [{"ID": f"r{i}", "P1": "Fernet", "MAIL": f"p{i}@ejemplo.invalid"}
             for i in range(12)]
    r = resumen_ingesta.resumir(_plan(demograficas={"MAIL": "email"}), filas)
    assert [c["clave"] for c in r["dedup"]] == ["email"]
    assert "sin_clave_de_dedup" not in _tipos(r)


def test_el_panel_de_calidad_no_le_propone_nada_a_una_demografica():
    """La causa del defecto 1. El filtro vive en `calidad.soloSemanticas` y
    la pantalla lo aplica al pintar; y aunque una propuesta vieja quedara,
    `aplicarPropuesta` no toca una fila con rol demográfico."""
    assert "export function soloSemanticas" in CALIDAD_JS
    pintar = _cuerpo_de_la_funcion(ENCUESTAS_JS, "pintarCalidad")
    assert "calidad.soloSemanticas(diagnostico, esDemografica)" in pintar
    assert "calidad.panelHtml(visible" in pintar
    assert "calidad.activarPanel(destino, visible" in pintar

    aplicar = _cuerpo_de_la_funcion(ENCUESTAS_JS, "aplicarPropuesta")
    guardia = aplicar.index("if (fila.querySelector('.p-rol').value) return;")
    assert guardia < aplicar.index("quitarFila(fila)"), (
        "la guarda tiene que ir antes de quitar la fila: si no, «Excluir la "
        "variable» se lleva el marcado de correo")


# ════════════════════════════════════════════════════════════════════
#  Defecto 2 — sin clave se advierte y se puede continuar
# ════════════════════════════════════════════════════════════════════

def test_sin_clave_se_advierte_destacado_y_no_bloquea():
    r = resumen_ingesta.resumir(
        _plan(demograficas={"NOM": {"campo": "nombre", "mapeo": {}}}), _filas(12))
    aviso = next(a for a in r["advertencias"] if a["tipo"] == "sin_clave_de_dedup")
    assert aviso["grave"] is True          # destacada
    assert aviso["bloquea"] is False       # pero no frena
    assert r["bloquea"] is False
    assert aviso["acciones"] == ["volver_a_corregir", "continuar_igual"]
    assert "duplicados" in aviso["mensaje"]
    assert r["sin_clave_de_dedup"] is True


def _sav_sin_clave(tmp_path):
    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")
    ruta = tmp_path / "sin_clave.sav"
    pyreadstat.write_sav(
        pandas.DataFrame({
            "ID": [f"r{i}" for i in range(12)],
            "NOM": [f"Persona {i}" for i in range(12)],
            "CONS": [1.0] * 12,
            "P1": [1.0] * 12,
        }),
        str(ruta), variable_value_labels={"P1": {1.0: "Fernet"}})
    return ruta


def _cuerpo_sin_clave(ruta, panel_id, **extra):
    cuerpo = _cuerpo(ruta, panel_id, **extra)
    cuerpo["preguntas"] = [p for p in cuerpo["preguntas"] if p["codigo"] != "DOC"]
    cuerpo["demograficas"] = {"NOM": "nombre"}
    return cuerpo


def test_una_carga_sin_ninguna_clave_se_puede_ejecutar_igual(
        ctx, actor, conn_boveda, tmp_path):
    """El DoD: muestra la advertencia **y** se ejecuta. Mismo cuerpo, con y
    sin `solo_revisar`, como hace la pantalla."""
    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")
    ruta = _sav_sin_clave(tmp_path)

    _, revision = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta",
        _cuerpo_sin_clave(ruta, panel["id"], solo_revisar=True), {},
        actor("operaciones"), ctx)
    assert "sin_clave_de_dedup" in _tipos(revision)

    status, r = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta",
        _cuerpo_sin_clave(ruta, panel["id"]), {}, actor("operaciones"), ctx)
    assert status == 202
    assert r["creacion_de_individuos"]["resumen"]["creados"] == 12
    assert r["sin_clave_de_dedup"] is True


def test_el_resultado_deja_constancia_de_que_se_cargo_sin_clave(
        ctx, actor, conn_boveda, tmp_path):
    """En el plan congelado del trabajo, que se guarda con la carga, y en lo
    que devuelve el estado: al retomar la carga otro día también se ve."""
    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")
    _, r = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta",
        _cuerpo_sin_clave(_sav_sin_clave(tmp_path), panel["id"]), {},
        actor("operaciones"), ctx)

    fila = db.una(conn_boveda,
                  "select plan->'sin_clave_de_dedup' as marca from ingesta_trabajo "
                  " where id = %s", (r["trabajo_id"],))
    assert fila["marca"] is True
    assert diferida.estado(conn_boveda, r["trabajo_id"])["sin_clave_de_dedup"] is True


def test_con_clave_no_queda_ninguna_constancia(ctx, actor, conn_boveda, tmp_path):
    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")
    _, r = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta",
        _cuerpo(_sav(tmp_path), panel["id"]), {}, actor("operaciones"), ctx)
    assert r["sin_clave_de_dedup"] is False
    assert diferida.estado(conn_boveda, r["trabajo_id"])["sin_clave_de_dedup"] is False


def test_una_carga_sin_panel_tambien_deja_constancia(
        ctx, actor, conn_boveda, tmp_path):
    from panel_api import cargas

    carga = cargas.crear(conn_boveda, "Ómnibus marzo", None)
    status, r = ruteo.despachar(
        "POST", f"/cargas/{carga['id']}/ingesta",
        _cuerpo_sin_clave(_sav_sin_clave(tmp_path), None), {},
        actor("operaciones"), ctx)
    assert status == 202
    assert r["sin_clave_de_dedup"] is True


def test_sigue_bloqueando_crear_individuos_sin_evidencia_de_consentimiento(
        ctx, actor, conn_boveda, tmp_path):
    """No regresión: ahí falta algo que ninguna decisión del analista suple,
    y bloquear es lo correcto. Nada se escribe."""
    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")
    cuerpo = _cuerpo(_sav(tmp_path), panel["id"])
    cuerpo.pop("evidencia_consentimiento")
    with pytest.raises(DatosInvalidos):
        ruteo.despachar("POST", f"/encuestas/{enc['id']}/sav/ingesta",
                        cuerpo, {}, actor("operaciones"), ctx)
    assert db.una(conn_boveda, "select count(*) as n from persona")["n"] == 0
    assert db.una(conn_boveda,
                  "select count(*) as n from ingesta_trabajo")["n"] == 0


# ════════════════════════════════════════════════════════════════════
#  Defecto 3 — lo que frena se dice junto al botón
# ════════════════════════════════════════════════════════════════════

def test_el_aviso_se_escribe_junto_al_boton():
    """`avisarEnIngesta` es el único camino de los motivos de la ingesta, y
    escribe arriba (donde va la barra de avance) **y** en el pie, al lado de
    «Ingestar»."""
    assert "alertaPie$.id = 'ing-alerta-pie'" in ENCUESTAS_JS
    assert "pie$.prepend(alertaPie$)" in ENCUESTAS_JS
    avisar = _cuerpo_de_la_funcion(ENCUESTAS_JS, "avisarEnIngesta")
    assert "alertaPie$.innerHTML = html" in avisar


def test_toda_salida_temprana_de_correr_dice_por_que():
    """Cada `return` de `correr` antes de mandar la carga tiene su motivo
    escrito con `avisarEnIngesta`, o es una decisión del usuario (cancelar un
    confirm, volver a corregir). Ninguno es silencioso."""
    correr = _cuerpo_de_la_funcion(ENCUESTAS_JS, "correr")
    antes_de_enviar = correr[:correr.index("const avance = panelDeAvance(")]
    lineas = antes_de_enviar.splitlines()
    for i, linea in enumerate(lineas):
        if not re.search(r"\breturn;", linea):
            continue
        contexto = "\n".join(lineas[max(0, i - 2):i + 1])
        assert ("avisarEnIngesta(" in contexto
                or "confirmarPendientes" in contexto
                or "revisarAntesDeImportar" in contexto), contexto


def test_ingestar_no_deja_un_error_sin_mostrar():
    """Si `correr` revienta por algo que nadie previó, el motivo aparece
    junto al botón en vez de perderse como «uncaught in promise»."""
    assert re.search(
        r"onClick: \(\) => correr\(\)\.catch\(\(error\) => avisarEnIngesta\(",
        ENCUESTAS_JS)


def test_durante_la_revision_el_pie_del_modal_no_esta_a_mano():
    """El «cuelgue» del bug: con la revisión abierta, el «Ingestar» del pie
    seguía activo, volvía a pedir la revisión y dejaba la anterior colgada.
    Ahora el pie se esconde al revisar y vuelve al cerrar la revisión."""
    revisar = _cuerpo_de_la_funcion(ENCUESTAS_JS, "revisarAntesDeImportar")
    assert "pie$.classList.add('hidden')" in revisar
    cerrar = revisar[revisar.index("const cerrar = (confirmado) =>"):]
    assert "pie$.classList.remove('hidden')" in cerrar.split("resolver(confirmado)")[0]


def test_sin_clave_las_dos_salidas_tienen_nombre():
    revisar = _cuerpo_de_la_funcion(ENCUESTAS_JS, "revisarAntesDeImportar")
    assert "Volver a corregir el mapeo" in revisar
    assert "Continuar igual, sin clave de dedup" in revisar
    assert "resumen.sin_clave_de_dedup" in revisar


def test_el_resultado_muestra_la_constancia():
    seguir = _cuerpo_de_la_funcion(ENCUESTAS_JS, "seguirIngesta")
    assert "sin_clave_de_dedup: Boolean(estado.sin_clave_de_dedup)" in seguir
    mostrar = _cuerpo_de_la_funcion(ENCUESTAS_JS, "mostrarResumenDeIngesta")
    assert "resultado.sin_clave_de_dedup" in mostrar


# ════════════════════════════════════════════════════════════════════
#  §6 — el inventario: qué bloquea y qué advierte
# ════════════════════════════════════════════════════════════════════

def test_toda_advertencia_del_resumen_esta_inventariada_y_no_bloquea(conn_boveda):
    """Cada cosa que el resumen puede decir tiene su clase y su motivo en
    `VALIDACIONES`, y ninguna advertencia del resumen frena."""
    for clase, (tipo, motivo) in resumen_ingesta.VALIDACIONES.items():
        assert tipo in (resumen_ingesta.BLOQUEA, resumen_ingesta.ADVIERTE), clase
        assert len(motivo) > 20, clase

    casos = [
        resumen_ingesta.resumir(
            _plan(demograficas={"NOM": {"campo": "nombre", "mapeo": {}}}), _filas(12)),
        resumen_ingesta.resumir(_plan(), _filas(1131, documento=lambda i: str(i % 2))),
        resumen_ingesta.resumir(
            _plan(preguntas=[{"codigo": "P1", "texto": "", "tipo": "cerrada"},
                             {"codigo": "NEDU", "texto": "Nivel", "tipo": "cerrada"}],
                  demograficas={"NEDU": {"campo": "nivel_educativo",
                                         "mapeo": {"1": "primaria"}}}),
            [{"ID": f"r{i}", "P1": "x", "NEDU": str(1 + i % 3)} for i in range(12)]),
    ]
    vistas = set()
    for r in casos:
        assert r["bloquea"] is False
        for aviso in r["advertencias"]:
            assert aviso["bloquea"] is False, aviso
            assert aviso["clase"] in resumen_ingesta.VALIDACIONES, aviso
            assert resumen_ingesta.VALIDACIONES[aviso["clase"]][0] == \
                resumen_ingesta.ADVIERTE, aviso
            vistas.add(aviso["clase"])
    assert {"sin_clave_de_dedup", "clave_de_dedup_sospechosa",
            "pregunta_sin_texto", "valores_sin_mapear"} <= vistas
