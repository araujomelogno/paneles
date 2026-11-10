"""Los tres PEDIDOS del 06/11/26 sobre la carga, contra Postgres de verdad.

* **Cancelar una carga** (D92, `specs/PEDIDO_cancelar_carga.md`): la carga
  nace provisoria, cancelar la borra, lo abandonado se limpia a las 24 h, y
  una ingesta en curso se puede detener y reanudar.
* **Carga mixta** (D93, `specs/PEDIDO_carga_mixta.md`): sin la pregunta «¿Los
  panelistas ya están en el sistema?»; cada fila se resuelve por capas.
* **Cargas incrementales** (D94, `specs/PEDIDO_cargas_incrementales.md`): el
  origen `repetida`, la ficha agrupada por encuesta, las estadísticas de cada
  carga, la composición de una carga y el borrado que reasigna la primera.
"""

import datetime
import importlib.util
import pathlib
import uuid

import pytest

from panel_api import (
    cargas, cargas_borrado, composicion, db, diferida, encuestas, personas,
    reconocimiento, ruteo,
)
from panel_api.errores import Conflicto, DatosInvalidos, IngestaEnCurso

from conftest import consentimientos

VERSION = "campo-2026-09"
PREGUNTAS = [{"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
              "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Whisky"}, "orden": 1}]
DEMOGRAFICAS = {"NOM": "nombre", "MAIL": "email", "CEL": "celular"}
EVIDENCIA = {
    "contacto_participacion": {"variable": "CONS", "valor_afirmativo": "1",
                               "version_texto": VERSION},
    "uso_semantico": {"variable": "CONS", "valor_afirmativo": "1",
                      "version_texto": VERSION},
}
DATOS_DEL_ARCHIVO = {"tipo_datos_origen": "encuesta", "modo_relevamiento": "web"}


RAIZ = pathlib.Path(__file__).resolve().parents[2]


def _script():
    spec = importlib.util.spec_from_file_location(
        "migrar_cargas_incrementales", RAIZ / "scripts" / "migrar_cargas_incrementales.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _procesar(ctx):
    pendientes = list(ctx.encolador.encoladas)
    ctx.encolador.encoladas.clear()
    return [diferida.procesar_lote(ctx.boveda, ctx.semantica, t, i, proveedor=ctx.embeddings)
            for t, i in pendientes]


def _persona(conn, nombre, email=None, celular=None,
             finalidades=("contacto_participacion", "uso_semantico")):
    return str(personas.alta(
        conn, {"persona": {"nombre": nombre, "email": email, "celular": celular},
               "consentimientos": consentimientos(*finalidades)})["id_persona"])


def _cuerpo(filas, **extra):
    return {"filas": filas, "columna_id": "ID", "tipo_identificador": "id_persona",
            "preguntas": PREGUNTAS, "demograficas": DEMOGRAFICAS,
            "proponer_tematicas": False, **extra}


def _ingestar(ctx, actor, carga_id, filas, revisar=False, **extra):
    cuerpo = _cuerpo(filas, **extra)
    if revisar:
        cuerpo["solo_revisar"] = True
    return ruteo.despachar("POST", f"/cargas/{carga_id}/ingesta", cuerpo, {},
                           actor("admin"), ctx)


def _fila(id_persona="", nombre="", email="", celular="", cons="1", respuesta="1", **otras):
    return {"ID": id_persona, "NOM": nombre, "MAIL": email, "CEL": celular,
            "CONS": cons, "P1": respuesta, **otras}


def _nueva_carga(ctx, actor, encuesta_id=None, nombre="Ola"):
    if encuesta_id is None:
        _, carga = ruteo.despachar("POST", "/cargas", {"nombre": nombre, **DATOS_DEL_ARCHIVO},
                                   {}, actor("admin"), ctx)
    else:
        _, carga = ruteo.despachar("POST", f"/encuestas/{encuesta_id}/cargas",
                                   {"nombre": nombre, **DATOS_DEL_ARCHIVO}, {},
                                   actor("admin"), ctx)
    return carga


def _listadas(ctx, actor):
    _, c = ruteo.despachar("GET", "/cargas", None, {}, actor("admin"), ctx)
    _, e = ruteo.despachar("GET", "/encuestas", None, {}, actor("admin"), ctx)
    return {x["id"] for x in c["items"]}, {x["id"] for x in e["items"]}


def _cuantas(conn, tabla):
    return db.una(conn, f"select count(*)::int as n from {tabla}")["n"]


def _origenes(conn, id_persona):
    return [f["origen"] for f in db.todas(
        conn, """select pc.origen from persona_carga pc join carga c on c.id = pc.carga_id
                  where pc.id_persona = %s order by c.creado_en, c.id""", (id_persona,))]


# ════════════════════════════════════════════════════════════════════
#  D92 · Cancelar una carga
# ════════════════════════════════════════════════════════════════════

def test_cancelar_desde_panelistas_no_deja_carga_ni_encuesta(ctx, actor):
    carga = _nueva_carga(ctx, actor, nombre="Base que se cancela")
    assert carga["provisoria"] is True
    encuesta = db.una(ctx.boveda, "select provisoria from encuesta where id = %s",
                      (carga["encuesta_id"],))
    assert encuesta["provisoria"] is True                # R-CAN-C.1: la automática también
    listadas_c, listadas_e = _listadas(ctx, actor)
    assert carga["id"] not in listadas_c and carga["encuesta_id"] not in listadas_e
    # La revisión se puede pedir (la carga existe) y no cambia nada.
    _ingestar(ctx, actor, carga["id"], [_fila(nombre="Ana", email="ana@ej.uy")],
              revisar=True, evidencia_consentimiento=EVIDENCIA)

    status, salida = ruteo.despachar("POST", f"/cargas/{carga['id']}/cancelar", {}, {},
                                     actor("analista"), ctx)
    assert status == 200 and salida["borrada"] and salida["encuesta_borrada"]
    assert _cuantas(ctx.boveda, "carga") == 0 and _cuantas(ctx.boveda, "encuesta") == 0
    # R-CAN-C.2 — seguro de repetir.
    status, otra = ruteo.despachar("POST", f"/cargas/{carga['id']}/cancelar", {}, {},
                                   actor("analista"), ctx)
    assert status == 200 and otra["borrada"] is False and otra["motivo"] == "no_existe"


def test_cancelar_una_carga_de_una_encuesta_existente_no_toca_la_encuesta(ctx, actor):
    encuesta = encuestas.crear(ctx.boveda, None, "Ómnibus de noviembre",
                               tipo=encuestas.INDEPENDIENTE, origen="manual")
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor, encuesta["id"], nombre="Parcial")
    assert carga["provisoria"] is True
    fila = db.una(ctx.boveda, "select provisoria, nombre from encuesta where id = %s",
                  (encuesta["id"],))
    assert fila["provisoria"] is False                   # una encuesta existente no
    _, listadas_e = _listadas(ctx, actor)
    assert encuesta["id"] in listadas_e

    ruteo.despachar("POST", f"/cargas/{carga['id']}/cancelar", {}, {}, actor("analista"), ctx)
    assert _cuantas(ctx.boveda, "carga") == 0
    queda = db.una(ctx.boveda, "select nombre, provisoria from encuesta where id = %s",
                   (encuesta["id"],))
    assert queda == {"nombre": "Ómnibus de noviembre", "provisoria": False}


def test_confirmar_la_ingesta_deja_de_ser_provisoria(ctx, actor):
    carga = _nueva_carga(ctx, actor, nombre="Base confirmada")
    status, _ = _ingestar(ctx, actor, carga["id"], [_fila(nombre="Ana", email="ana@ej.uy")],
                          evidencia_consentimiento=EVIDENCIA)
    assert status == 202
    listadas_c, listadas_e = _listadas(ctx, actor)
    assert carga["id"] in listadas_c and carga["encuesta_id"] in listadas_e
    # Cancelar después de confirmar no la toca: eso es el borrado (R-EU.10).
    _, salida = ruteo.despachar("POST", f"/cargas/{carga['id']}/cancelar", {}, {},
                                actor("analista"), ctx)
    assert salida == {**salida, "borrada": False, "motivo": "confirmada"}
    assert carga["id"] in _listadas(ctx, actor)[0]


def test_una_provisoria_abandonada_se_limpia_al_iniciar_otra_carga(ctx, actor):
    vieja = _nueva_carga(ctx, actor, nombre="Abandonada")
    reciente = _nueva_carga(ctx, actor, nombre="En curso")
    db.ejecutar(ctx.boveda, "update carga set creado_en = now() - interval '25 hours' "
                            " where id = %s", (vieja["id"],))
    db.ejecutar(ctx.boveda, "update encuesta set creado_en = now() - interval '25 hours' "
                            " where id = %s", (vieja["encuesta_id"],))
    ctx.boveda.commit()

    otra = _nueva_carga(ctx, actor, nombre="Nueva")
    assert otra["limpieza"]["cargas_eliminadas"] == 1
    assert otra["limpieza"]["encuestas_eliminadas"] == 1
    ids = {f["id"] for f in db.todas(ctx.boveda, "select id from carga")}
    assert ids == {reciente["id"], otra["id"]}          # la de menos de 24 h se queda
    registro = db.todas(ctx.boveda, "select disparada_por, cargas_eliminadas "
                                    "  from limpieza_provisoria order by id")
    assert registro[-1] == {"disparada_por": "inicio_de_carga", "cargas_eliminadas": 1}


def test_una_provisoria_con_trabajo_de_ingesta_no_se_limpia(ctx, actor):
    carga = _nueva_carga(ctx, actor, nombre="Con trabajo")
    db.ejecutar(ctx.boveda, """insert into ingesta_trabajo
                                 (destino_tipo, destino_id, plan, estado, lotes_total, filas_total)
                               values ('carga', %s, '{}'::jsonb, 'fallida', 1, 1)""",
                (carga["id"],))
    db.ejecutar(ctx.boveda, "update carga set creado_en = now() - interval '3 days', "
                            "provisoria = true where id = %s", (carga["id"],))
    ctx.boveda.commit()
    salida = cargas.limpiar_provisorias(ctx.boveda)
    assert salida["cargas_eliminadas"] == 0


def test_la_limpieza_se_dispara_a_mano_desde_cumplimiento_y_queda_registrada(ctx, actor):
    vieja = _nueva_carga(ctx, actor, nombre="Abandonada")
    db.ejecutar(ctx.boveda, "update carga set creado_en = now() - interval '2 days' "
                            " where id = %s", (vieja["id"],))
    ctx.boveda.commit()
    status, salida = ruteo.despachar("POST", "/cumplimiento/provisorias/limpieza", {}, {},
                                     actor("admin"), ctx)
    assert status == 200 and salida["cargas_eliminadas"] == 1
    _, vista = ruteo.despachar("GET", "/cumplimiento/provisorias", None, {}, actor("admin"), ctx)
    assert vista["items"][0]["disparada_por"] == "manual"
    assert vista["items"][0]["actor"] == "admin@equipos.com.uy"
    assert vista["provisorias"] == 0


# ── R-CAN-C.4 · Detener una ingesta en curso ──────────────────────────

@pytest.fixture
def en_lotes(ctx, actor, monkeypatch):
    """Una ingesta de 4 filas en 4 lotes, sobre 4 personas existentes."""
    monkeypatch.setattr(diferida, "FILAS_POR_LOTE", 1)
    ids = [_persona(ctx.boveda, f"Persona {i}", email=f"p{i}@ej.uy") for i in range(4)]
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor, nombre="En lotes")
    status, salida = _ingestar(ctx, actor, carga["id"], [_fila(i) for i in ids])
    assert status == 202 and salida["lotes_total"] == 4
    return {"carga": carga, "trabajo_id": salida["trabajo_id"], "ids": ids,
            "salida": salida}


def test_detener_deja_sin_procesar_los_pendientes_y_el_trabajo_en_detenida(ctx, actor, en_lotes):
    trabajo_id = en_lotes["trabajo_id"]
    assert en_lotes["salida"]["aviso_cerrar"] == diferida.AVISO_CERRAR_NO_DETIENE
    visto = diferida.estado(ctx.boveda, trabajo_id)
    assert visto["detenible"] and visto["aviso_cerrar"]  # R-CAN-C.5

    primera = ctx.encolador.encoladas.pop(0)
    diferida.procesar_lote(ctx.boveda, ctx.semantica, *primera, proveedor=ctx.embeddings)
    status, detenida = ruteo.despachar("POST", f"/ingestas/{trabajo_id}/detener", {}, {},
                                       actor("admin"), ctx)
    assert status == 200
    assert detenida["estado"] == "detenida" and detenida["lotes_cancelados"] == 3
    assert detenida["detencion"]["lotes_procesados"] == 1
    assert detenida["detencion"]["lotes_sin_procesar"] == 3
    assert detenida["detenible"] is False

    # R-CAN-C.4 — las tareas de Cloud Tasks ya encoladas llegan igual y no
    # procesan nada.
    resultados = _procesar(ctx)
    assert {r["estado"] for r in resultados} == {"cancelado"}
    assert diferida.estado(ctx.boveda, trabajo_id)["estado"] == "detenida"
    personas_con_respuesta = db.una(ctx.semantica, "select count(*)::int as n from individuo i "
                                    "where exists (select 1 from respuesta r "
                                    "               where r.individuo_id = i.id)")["n"]
    assert personas_con_respuesta == 1


def test_un_lote_en_curso_termina_y_la_detenida_sigue_detenida(ctx, actor, en_lotes):
    trabajo_id = en_lotes["trabajo_id"]
    t, i = ctx.encolador.encoladas.pop(0)
    lote = diferida._tomar_lote(ctx.boveda, t, i)           # una tarea lo tomó
    assert lote is not None
    diferida.detener(ctx.boveda, trabajo_id)
    # Esa tarea termina su lote: no se corta a mitad.
    resultado = diferida._ingestar_el_lote(
        ctx.boveda, ctx.semantica, diferida._trabajo(ctx.boveda, t), lote["filas"],
        ctx.embeddings)
    ctx.semantica.commit()
    db.ejecutar(ctx.boveda, "update ingesta_lote set estado = 'ok', resultado = %s::jsonb "
                            " where trabajo_id = %s and indice = %s",
                (__import__("json").dumps(resultado, default=str), t, i))
    ctx.boveda.commit()
    cierre = diferida._cerrar_si_termino(ctx.boveda, trabajo_id)
    assert cierre["estado"] == "detenida"
    assert diferida.estado(ctx.boveda, trabajo_id)["lotes_ok"] == 1


def test_reanudar_completa_los_lotes_cancelados(ctx, actor, en_lotes):
    trabajo_id = en_lotes["trabajo_id"]
    diferida.procesar_lote(ctx.boveda, ctx.semantica, *ctx.encolador.encoladas.pop(0),
                           proveedor=ctx.embeddings)
    diferida.detener(ctx.boveda, trabajo_id)
    _procesar(ctx)                                        # las viejas se descartan
    status, vuelta = ruteo.despachar("POST", f"/ingestas/{trabajo_id}/reanudar", {}, {},
                                     actor("admin"), ctx)
    assert status == 200 and vuelta["reencolados"] == [1, 2, 3]
    _procesar(ctx)
    final = diferida.estado(ctx.boveda, trabajo_id)
    assert final["estado"] == "terminada" and final["lotes_ok"] == 4
    assert final["resumen"]["respuestas_escritas"] == 4


def test_un_trabajo_detenido_no_bloquea_una_ingesta_nueva_para_el_mismo_destino(
        ctx, actor, en_lotes):
    carga, trabajo_id = en_lotes["carga"], en_lotes["trabajo_id"]
    diferida.detener(ctx.boveda, trabajo_id)
    ctx.encolador.encoladas.clear()
    status, nueva = _ingestar(ctx, actor, carga["id"], [_fila(en_lotes["ids"][0])],
                              confirmar_archivo_repetido=True)
    assert status == 202 and nueva["trabajo_id"] != trabajo_id
    # Y reanudar la vieja con otra en curso no la deja: una sola activa.
    with pytest.raises(IngestaEnCurso):
        diferida.reanudar(ctx.boveda, trabajo_id, encolador=ctx.encolador)
    assert diferida.estado(ctx.boveda, trabajo_id)["estado"] == "detenida"


def test_detener_lo_que_no_esta_en_curso_no_hace_nada(ctx, actor, en_lotes):
    trabajo_id = en_lotes["trabajo_id"]
    _procesar(ctx)
    with pytest.raises(Conflicto):
        diferida.detener(ctx.boveda, trabajo_id)
    with pytest.raises(Conflicto):
        diferida.reanudar(ctx.boveda, trabajo_id)


# ════════════════════════════════════════════════════════════════════
#  D93 · Carga mixta
# ════════════════════════════════════════════════════════════════════

def test_un_archivo_mixto_carga_a_las_existentes_y_crea_a_las_nuevas(ctx, actor):
    existentes = [_persona(ctx.boveda, f"Existente {i}", email=f"e{i}@ej.uy") for i in range(5)]
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor)
    filas = ([_fila(i) for i in existentes]
             + [_fila(nombre=f"Nueva {i}", email=f"n{i}@ej.uy") for i in range(3)])
    _, revision = _ingestar(ctx, actor, carga["id"], filas, revisar=True)
    r = revision["reconocimiento"]
    assert (r["existentes"], r["nuevas"], r["a_crear"], r["id_desconocido"]) == (5, 3, 3, 0)
    assert r["texto"] == "8 filas · 5 existentes · 3 nuevas"
    assert r["requiere_consentimiento"] is True
    assert revision["identidad"]["crea_personas"] is True

    # Sin evidencia no se crea nadie (R-MIX.4): la ruta lo rechaza.
    with pytest.raises(DatosInvalidos, match="consentimiento"):
        _ingestar(ctx, actor, carga["id"], filas)
    ctx.boveda.rollback()
    assert _cuantas(ctx.boveda, "persona") == 5

    status, salida = _ingestar(ctx, actor, carga["id"], filas,
                               evidencia_consentimiento=EVIDENCIA)
    assert status == 202
    assert salida["creacion_de_individuos"]["resumen"]["creados"] == 3
    _procesar(ctx)
    final = diferida.estado(ctx.boveda, salida["trabajo_id"])
    assert final["estado"] == "terminada"
    assert final["resumen"]["respuestas_escritas"] == 8   # con sus respuestas
    assert _cuantas(ctx.boveda, "persona") == 8


def test_un_id_persona_inexistente_no_crea_a_nadie(ctx, actor):
    ana = _persona(ctx.boveda, "Ana", email="ana@ej.uy")
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor)
    roto = str(uuid.uuid4())
    filas = [_fila(ana), _fila(roto, nombre="Fantasma", email="fantasma@ej.uy")]
    _, revision = _ingestar(ctx, actor, carga["id"], filas, revisar=True)
    r = revision["reconocimiento"]
    assert r["id_desconocido"] == 1 and r["a_crear"] == 0
    assert r["texto"] == "2 filas · 1 existentes · 0 nuevas · 1 con id desconocido"
    assert any(a["tipo"] == "id_desconocido" and roto in a["ejemplos"]
               for a in r["advertencias"])
    status, salida = _ingestar(ctx, actor, carga["id"], filas)   # sin evidencia: no hace falta
    assert status == 202
    _procesar(ctx)
    assert _cuantas(ctx.boveda, "persona") == 1               # no se creó a nadie
    assert diferida.estado(ctx.boveda, salida["trabajo_id"])["resumen"]["respuestas_escritas"] == 1


def test_una_fila_nueva_con_correo_ya_registrado_se_reutiliza(ctx, actor):
    ana = _persona(ctx.boveda, "Ana", email="ana@ej.uy")
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor)
    filas = [_fila(nombre="Ana P.", email="ANA@ej.uy")]
    _, revision = _ingestar(ctx, actor, carga["id"], filas, revisar=True)
    r = revision["reconocimiento"]
    assert (r["nuevas"], r["reconocidas_por_dedup"], r["a_crear"]) == (1, 1, 0)
    assert r["requiere_consentimiento"] is False
    _ingestar(ctx, actor, carga["id"], filas)
    _procesar(ctx)
    assert _cuantas(ctx.boveda, "persona") == 1
    assert _origenes(ctx.boveda, ana) == ["reutilizada"]


def test_con_id_de_respuesta_una_nueva_se_reconoce_en_la_carga_siguiente(ctx, actor):
    primera = _nueva_carga(ctx, actor, nombre="Ola 1")
    sin_contacto = [_fila(nombre="Juan", RID="R-17")]
    _, revision = _ingestar(ctx, actor, primera["id"], sin_contacto, revisar=True,
                            columna_respuesta="RID")
    assert revision["reconocimiento"]["sin_clave"] == 0      # el ID de respuesta es clave
    _ingestar(ctx, actor, primera["id"], sin_contacto, columna_respuesta="RID",
              evidencia_consentimiento=EVIDENCIA)
    _procesar(ctx)
    assert _cuantas(ctx.boveda, "persona") == 1
    alias = db.una(ctx.boveda, "select origen, id_en_origen from alias_origen")
    assert alias["origen"].startswith("respuesta:") and alias["id_en_origen"] == "R-17"

    segunda = _nueva_carga(ctx, actor, primera["encuesta_id"], nombre="Ola 2")
    _, revision = _ingestar(ctx, actor, segunda["id"], sin_contacto, revisar=True,
                            columna_respuesta="RID")
    r = revision["reconocimiento"]
    assert (r["existentes"], r["por_id_respuesta"], r["nuevas"]) == (1, 1, 0)
    _ingestar(ctx, actor, segunda["id"], sin_contacto, columna_respuesta="RID",
              confirmar_archivo_repetido=True)
    _procesar(ctx)
    assert _cuantas(ctx.boveda, "persona") == 1                # no se creó de nuevo


def test_el_id_de_respuesta_es_de_la_encuesta_y_no_cruza_a_otra(ctx, actor):
    una = _nueva_carga(ctx, actor, nombre="Estudio A")
    _ingestar(ctx, actor, una["id"], [_fila(nombre="Juan", RID="1")],
              columna_respuesta="RID", evidencia_consentimiento=EVIDENCIA)
    otra = _nueva_carga(ctx, actor, nombre="Estudio B")
    _, revision = _ingestar(ctx, actor, otra["id"], [_fila(nombre="Pedro", RID="1")],
                            revisar=True, columna_respuesta="RID")
    # La respuesta 1 de otro estudio no es la misma persona.
    assert revision["reconocimiento"]["a_crear"] == 1


def test_sin_id_de_respuesta_la_vista_previa_advierte_las_nuevas_sin_clave(ctx, actor):
    carga = _nueva_carga(ctx, actor)
    filas = [_fila(nombre="Juan"), _fila(nombre="Sofía", email="sofia@ej.uy")]
    _, revision = _ingestar(ctx, actor, carga["id"], filas, revisar=True)
    r = revision["reconocimiento"]
    assert r["sin_clave"] == 1
    [aviso] = [a for a in r["advertencias"] if a["tipo"] == "nuevas_sin_clave"]
    assert aviso["mensaje"].startswith("1 persona(s) nueva(s) no tienen correo ni celular: "
                                       "si aparecen en una carga posterior, se van a crear de nuevo")
    assert aviso["bloquea"] is False


def test_sin_nuevas_no_se_pide_evidencia_y_con_nuevas_se_aplica_solo_a_ellas(ctx, actor):
    # Ana existe solo con contacto: el «1» de su fila no le da uso semántico.
    ana = _persona(ctx.boveda, "Ana", email="ana@ej.uy", finalidades=("contacto_participacion",))
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor)
    _, solo_existentes = _ingestar(ctx, actor, carga["id"], [_fila(ana)], revisar=True)
    assert solo_existentes["reconocimiento"]["requiere_consentimiento"] is False
    assert solo_existentes["consentimiento"]["aplica"] is False

    filas = [_fila(ana), _fila(nombre="Nueva", email="nueva@ej.uy")]
    _, con_nuevas = _ingestar(ctx, actor, carga["id"], filas, revisar=True,
                              evidencia_consentimiento=EVIDENCIA)
    assert con_nuevas["reconocimiento"]["requiere_consentimiento"] is True
    assert con_nuevas["consentimiento"]["aplica"] is True
    _ingestar(ctx, actor, carga["id"], filas, evidencia_consentimiento=EVIDENCIA)
    _procesar(ctx)
    finalidades = {f["finalidad"] for f in db.todas(
        ctx.boveda, "select finalidad from consentimiento where id_persona = %s", (ana,))}
    assert finalidades == {"contacto_participacion"}           # a Ana no se le registró nada
    nueva = db.una(ctx.boveda, "select id_persona from persona where email = 'nueva@ej.uy'")
    assert {f["finalidad"] for f in db.todas(
        ctx.boveda, "select finalidad from consentimiento where id_persona = %s",
        (nueva["id_persona"],))} == {"contacto_participacion", "uso_semantico"}


def test_una_proporcion_inesperada_de_nuevas_se_destaca(ctx, actor):
    ana = _persona(ctx.boveda, "Ana", email="ana@ej.uy")
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor)
    filas = [_fila(ana)] + [_fila(nombre=f"N{i}", email=f"n{i}@ej.uy") for i in range(3)]
    _, revision = _ingestar(ctx, actor, carga["id"], filas, revisar=True)
    r = revision["reconocimiento"]
    assert r["proporcion_inesperada"] is True
    [aviso] = [a for a in r["advertencias"] if a["tipo"] == "proporcion_inesperada"]
    assert aviso["destacada"] and aviso["grave"] and not aviso["bloquea"]
    assert "columna identificadora o su tipo" in aviso["mensaje"]
    # Una base nueva, sin id_persona ni audiencia, no esperaba encontrar a nadie.
    otra = _nueva_carga(ctx, actor, nombre="Base nueva")
    _, nueva = _ingestar(ctx, actor, otra["id"], filas[1:], revisar=True,
                         tipo_identificador="alias")
    assert nueva["reconocimiento"]["proporcion_inesperada"] is False


def test_en_una_encuesta_con_audiencia_las_nuevas_quedan_no_convocado(ctx, actor):
    ana = _persona(ctx.boveda, "Ana", email="ana@ej.uy")
    luis = _persona(ctx.boveda, "Luis", email="luis@ej.uy")
    encuesta = encuestas.crear(ctx.boveda, None, "Con audiencia",
                               tipo=encuestas.INDEPENDIENTE, origen="manual")
    encuestas.convocar(ctx.boveda, encuesta["id"], [ana], a_mano=True)
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor, encuesta["id"])
    # Luis existe pero no vino con el enlace: el dedup lo reconoce y no está
    # convocado. La nueva tampoco.
    filas = [_fila(ana), _fila(nombre="Luis", email="luis@ej.uy"),
             _fila(nombre="Nueva", email="nueva@ej.uy")]
    _, revision = _ingestar(ctx, actor, carga["id"], filas, revisar=True,
                            evidencia_consentimiento=EVIDENCIA)
    nc = revision["no_convocados"]
    assert (nc["cuantos"], nc["existentes"], nc["sin_identificar"]) == (2, 0, 2)
    _ingestar(ctx, actor, carga["id"], filas, evidencia_consentimiento=EVIDENCIA)
    _procesar(ctx)
    convocado = {str(f["id_persona"]): f["convocado"] for f in db.todas(
        ctx.boveda, "select id_persona, convocado from participacion where encuesta_id = %s",
        (encuesta["id"],))}
    nueva = str(db.una(ctx.boveda, "select id_persona from persona "
                                   " where email = 'nueva@ej.uy'")["id_persona"])
    assert convocado == {ana: True, luis: False, nueva: False}


def test_excluir_no_convocados_no_crea_a_las_nuevas(ctx, actor):
    ana = _persona(ctx.boveda, "Ana", email="ana@ej.uy")
    encuesta = encuestas.crear(ctx.boveda, None, "Con audiencia",
                               tipo=encuestas.INDEPENDIENTE, origen="manual")
    encuestas.convocar(ctx.boveda, encuesta["id"], [ana], a_mano=True)
    ctx.boveda.commit()
    carga = _nueva_carga(ctx, actor, encuesta["id"])
    filas = [_fila(ana), _fila(nombre="Nueva", email="nueva@ej.uy")]
    _, salida = _ingestar(ctx, actor, carga["id"], filas, excluir_no_convocados=True,
                          evidencia_consentimiento=EVIDENCIA)
    assert salida["excluidas_no_convocadas"] == 1
    _procesar(ctx)
    assert _cuantas(ctx.boveda, "persona") == 1


def test_la_pantalla_ya_no_pregunta_si_los_panelistas_existen():
    pantalla = (RAIZ / "web" / "public" / "js" / "paginas" / "encuestas.js").read_text(
        encoding="utf-8")
    assert "¿Los panelistas ya están en el sistema?" not in pantalla
    assert 'id="modo-sav"' not in pantalla
    assert 'id="columna-respuesta"' in pantalla


# ════════════════════════════════════════════════════════════════════
#  D94 · Cargas incrementales
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def incremental(ctx, actor):
    """Tres cargas de una encuesta, cada SAV con el acumulado: X en las
    tres, Y desde la segunda, Z en la tercera. Y viene además de otro
    estudio (reutilizada en su primera carga)."""
    otra = _nueva_carga(ctx, actor, nombre="Otro estudio")
    _ingestar(ctx, actor, otra["id"], [_fila(nombre="Y", email="y@ej.uy")],
              evidencia_consentimiento=EVIDENCIA)
    _procesar(ctx)
    x, y, z = (_fila(nombre=n, email=f"{n.lower()}@ej.uy") for n in ("X", "Y", "Z"))
    una = _nueva_carga(ctx, actor, nombre="Ola · carga 1")
    _ingestar(ctx, actor, una["id"], [x], evidencia_consentimiento=EVIDENCIA)
    _procesar(ctx)
    dos = _nueva_carga(ctx, actor, una["encuesta_id"], nombre="Ola · carga 2")
    _ingestar(ctx, actor, dos["id"], [x, y], evidencia_consentimiento=EVIDENCIA)
    _procesar(ctx)
    tres = _nueva_carga(ctx, actor, una["encuesta_id"], nombre="Ola · carga 3")
    _ingestar(ctx, actor, tres["id"], [x, y, z], evidencia_consentimiento=EVIDENCIA)
    _procesar(ctx)
    ids = {n: str(db.una(ctx.boveda, "select id_persona from persona where email = %s",
                         (f"{n.lower()}@ej.uy",))["id_persona"]) for n in "XYZ"}
    return {"cargas": [una, dos, tres], "otra": otra, "ids": ids}


def test_tres_cargas_incrementales_marcan_repetida_a_partir_de_la_segunda(ctx, incremental):
    ids = incremental["ids"]
    assert _origenes(ctx.boveda, ids["X"]) == ["creada", "repetida", "repetida"]
    assert _origenes(ctx.boveda, ids["Y"]) == ["creada", "reutilizada", "repetida"]
    assert _origenes(ctx.boveda, ids["Z"]) == ["creada"]
    # Ninguna persona tiene más de un vínculo no `repetida` dentro de una encuesta.
    script = _script()
    assert db.todas(ctx.boveda, script.CONSULTA_DUPLICADOS) == []


def test_la_ficha_muestra_cada_encuesta_una_vez_con_la_nota_de_las_posteriores(
        ctx, incremental):
    ficha = cargas.cargas_de_persona(ctx.boveda, incremental["ids"]["X"])
    [entrada] = ficha["cargas"]
    assert entrada["nombre"] == "Ola · carga 1" and entrada["origen"] == "creada"
    assert [p["nombre"] for p in entrada["posteriores"]] == ["Ola · carga 2", "Ola · carga 3"]
    assert entrada["nota_posteriores"] == "también en 2 cargas posteriores de esta encuesta"
    # Y una persona en dos encuestas: dos entradas.
    assert len(cargas.cargas_de_persona(ctx.boveda, incremental["ids"]["Y"])["cargas"]) == 2


def test_el_detalle_de_una_carga_separa_nuevos_reutilizados_y_ya_estaban(ctx, actor, incremental):
    tres = incremental["cargas"][2]
    _, detalle = ruteo.despachar("GET", f"/cargas/{tres['id']}", None, {}, actor("admin"), ctx)
    assert (detalle["personas"], detalle["personas_creadas"],
            detalle["personas_reutilizadas"], detalle["personas_repetidas"]) == (3, 1, 0, 2)
    assert detalle["resumen_personas"] == (
        "3 en el archivo · 1 nuevos · 0 reutilizados de otros estudios · "
        "2 ya estaban en cargas anteriores de esta encuesta")
    _, listado = ruteo.despachar("GET", "/cargas", None, {}, actor("admin"), ctx)
    dos = next(c for c in listado["items"] if c["id"] == incremental["cargas"][1]["id"])
    assert (dos["personas_reutilizadas"], dos["personas_repetidas"]) == (1, 1)


def test_el_desglose_de_reutilizados_no_incluye_a_los_repetidos(ctx, incremental):
    dos = incremental["cargas"][1]
    desglose = cargas.reutilizados(ctx.boveda, dos["id"])
    assert desglose["reutilizados"] == 1                     # Y, del otro estudio
    assert [c["carga_id"] for c in desglose["cargas"]] == [incremental["otra"]["id"]]
    tres = cargas.reutilizados(ctx.boveda, incremental["cargas"][2]["id"])
    assert tres["reutilizados"] == 0 and tres["cargas"] == []


def test_la_composicion_de_una_carga_excluye_a_los_repetidos(ctx, incremental):
    tres = incremental["cargas"][2]
    salida = composicion.composicion_de_ambito(ctx.boveda, "carga", tres["id"])
    assert salida["miembros"] == 1                           # solo Z la sumó
    assert composicion.NOTA_CARGA_SUMA in salida["avisos"]
    # La de la encuesta no cambia: las tres personas.
    encuesta = composicion.composicion_de_ambito(ctx.boveda, "encuesta", tres["encuesta_id"])
    assert encuesta["miembros"] == 3


def test_borrar_la_primera_carga_reasigna_la_siguiente_y_no_da_de_baja(ctx, actor, incremental):
    una, dos, _ = incremental["cargas"]
    ids = incremental["ids"]
    _, previa = cargas_borrado.lanzar(ctx.boveda, ctx.semantica, una["id"],
                                      solo_revisar=True, dar_de_baja_creadas=True)
    assert previa["se_pueden_dar_de_baja"] == 0              # X sigue en otras cargas
    cargas_borrado.lanzar(ctx.boveda, ctx.semantica, una["id"], confirmacion=una["nombre"],
                          actor=actor("admin"), encolador=ctx.encolador,
                          dar_de_baja_creadas=True)
    _procesar(ctx)
    assert _origenes(ctx.boveda, ids["X"]) == ["reutilizada", "repetida"]
    assert db.una(ctx.boveda, "select 1 as hay from persona where id_persona = %s",
                  (ids["X"],))
    script = _script()
    assert db.todas(ctx.boveda, script.CONSULTA_DUPLICADOS) == []


def test_la_migracion_marca_repetida_lo_ya_cargado(ctx, actor):
    """R-INC.2 — lo cargado antes de la 0037: los vínculos posteriores a la
    primera carga de cada encuesta pasan a `repetida`. Re-ejecutable."""
    ana = _persona(ctx.boveda, "Ana", email="ana@ej.uy")
    encuesta = encuestas.crear(ctx.boveda, None, "Vieja", tipo=encuestas.INDEPENDIENTE,
                               origen="manual")
    c1 = cargas.crear(ctx.boveda, "C1", encuesta_id=encuesta["id"])
    c2 = cargas.crear(ctx.boveda, "C2", encuesta_id=encuesta["id"])
    for carga, origen in ((c1, "creada"), (c2, "reutilizada")):
        db.ejecutar(ctx.boveda, "insert into persona_carga (id_persona, carga_id, origen) "
                                "values (%s, %s, %s)", (ana, carga["id"], origen))
    ctx.boveda.commit()
    script = _script()
    sql = script._sin_transaccion(script.MIGRACION.read_text(encoding="utf-8"))
    for _ in range(2):
        db.ejecutar(ctx.boveda, sql)
    ctx.boveda.commit()
    assert _origenes(ctx.boveda, ana) == ["creada", "repetida"]


def test_reconocer_no_escribe_nada(ctx, actor):
    carga = _nueva_carga(ctx, actor)
    encuesta = encuestas.obtener(ctx.boveda, carga["encuesta_id"])
    antes = (_cuantas(ctx.boveda, "persona"), _cuantas(ctx.boveda, "alias_origen"))
    reconocimiento.reconocer(ctx.boveda, encuesta, [_fila(nombre="Nueva", email="x@ej.uy",
                                                          RID="9")],
                             "ID", "id_persona", "carga", columna_respuesta="RID",
                             demograficas=DEMOGRAFICAS)
    assert (_cuantas(ctx.boveda, "persona"), _cuantas(ctx.boveda, "alias_origen")) == antes


def test_las_fechas_de_la_limpieza_son_de_hoy(ctx):
    salida = cargas.limpiar_provisorias(ctx.boveda, cargas.LIMPIEZA_MANUAL, actor="dpo@x")
    assert salida["ejecutada_en"][:10] == datetime.date.today().isoformat()
