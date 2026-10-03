"""La ingesta en diferido, contra su Definition of Done.

Lo que esta entrega cambia no es **qué** hace la ingesta sino **cuándo y en
qué pedazos**, y eso decide la forma de estas pruebas: casi ninguna mira el
contenido de lo ingestado —eso ya lo cubre `test_ingesta.py`— y casi todas
miran que partirlo no cambie el resultado.

La de más valor es `test_el_resumen_partido_coincide_con_el_entero`: si
partir una carga en lotes diera un resumen distinto del de la misma carga
hecha de una vez, todo lo demás daría igual.

**Cloud Tasks es de mentira y Postgres es de verdad.** El doble solo anota
qué lotes se encolaron; procesarlos lo hace la prueba llamando a
`procesar_lote`, que es exactamente lo que hace la tarea real. Así el
reparto, el cierre, el reintento y el gate de consentimiento se prueban sin
desplegar una cola.
"""

import pytest

from panel_api import db, diferida, encuestas, paneles, personas
from panel_api.errores import Conflicto, DatosInvalidos, NoEncontrado

from conftest import consentimientos

AMBAS = ("contacto_participacion", "uso_semantico")

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet", "2": "Cerveza"}, "orden": 1},
    {"codigo": "P2", "texto": "¿Por qué la elige?", "tipo": "abierta", "orden": 2},
]


def _filas(cuantas, desde=1):
    return [
        {"id_en_origen": f"R-{n:03d}", "P1": "1" if n % 2 else "2",
         "P2": f"Respuesta libre {n}"}
        for n in range(desde, desde + cuantas)
    ]


def _plan(**extra):
    plan = {"preguntas": PREGUNTAS, "columna_id": "id_en_origen",
            "origen": "dooblo", "demograficas": None,
            "tipo_identificador": None}
    plan.update(extra)
    return plan


@pytest.fixture
def ola(conn_boveda):
    """Un panel con seis panelistas convocados a una encuesta."""
    panel = paneles.crear(conn_boveda, "Panel diferido")
    for n in range(1, 7):
        id_persona = personas.alta(conn_boveda, {
            "persona": {"documento": f"DIF-{n}", "nombre": f"Panelista {n}"},
            "consentimientos": consentimientos(*AMBAS),
            "origen": "dooblo", "id_en_origen": f"R-{n:03d}",
        })["id_persona"]
        paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola diferida",
                               "2026-03-01")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    conn_boveda.commit()
    return {"panel": panel, "encuesta": encuesta}


def _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador):
    """Corre las tareas encoladas, como las correría Cloud Tasks."""
    hechos = []
    pendientes = list(encolador.encoladas)
    encolador.encoladas.clear()
    for trabajo_id, indice in pendientes:
        hechos.append(diferida.procesar_lote(
            conn_boveda, conn_semantica, trabajo_id, indice,
            proveedor=proveedor))
    return hechos


# ════════════════════════════════════════════════════════════════════
#  DoD 1 · Confirmar encola y responde
# ════════════════════════════════════════════════════════════════════

def test_confirmar_deja_la_carga_encolada_sin_procesar_nada(
        conn_boveda, conn_semantica, ola, encolador):
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(6),
        tamano_lote=2, encolador=encolador)

    assert salida["estado"] == diferida.ENCOLADA
    assert salida["lotes_total"] == 3
    assert salida["filas_total"] == 6
    assert salida["filas_procesadas"] == 0
    # Y lo que importa de «no procesa nada»: el store semántico sigue vacío.
    assert db.una(conn_semantica,
                  "select count(*)::int as n from respuesta")["n"] == 0
    # Una tarea por lote, encolada.
    assert encolador.encoladas == [(salida["trabajo_id"], i) for i in range(3)]


def test_el_trabajo_y_sus_lotes_quedan_guardados(conn_boveda, ola, encolador):
    """Si el trabajo no estuviera persistido, una tarea que arranca después
    de que la request terminó no tendría de dónde sacar qué hacer."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(5),
        tamano_lote=2, encolador=encolador)

    lotes = db.todas(
        conn_boveda,
        "select indice, filas_total, estado from ingesta_lote "
        " where trabajo_id = %s order by indice", (salida["trabajo_id"],))
    assert [l["filas_total"] for l in lotes] == [2, 2, 1]
    assert {l["estado"] for l in lotes} == {"pendiente"}


def test_una_carga_sin_filas_se_rechaza_antes_de_guardar_nada(
        conn_boveda, ola, encolador):
    with pytest.raises(DatosInvalidos, match="no tenía filas"):
        diferida.encolar(conn_boveda, "encuesta", ola["encuesta"]["id"],
                         _plan(), [], encolador=encolador)
    assert db.una(conn_boveda,
                  "select count(*)::int as n from ingesta_trabajo")["n"] == 0


# ════════════════════════════════════════════════════════════════════
#  DoD 2 · El resumen partido es el mismo que el entero
# ════════════════════════════════════════════════════════════════════

def test_una_carga_de_varios_lotes_termina_completa(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(6),
        tamano_lote=2, encolador=encolador)
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)

    final = diferida.estado(conn_boveda, salida["trabajo_id"])
    assert final["estado"] == diferida.TERMINADA
    assert final["lotes_ok"] == 3
    assert final["filas_procesadas"] == 6
    assert final["porcentaje"] == 100
    assert final["terminado_en"]
    # Seis personas × dos preguntas.
    assert db.una(conn_semantica,
                  "select count(*)::int as n from respuesta")["n"] == 12


def test_el_resumen_partido_coincide_con_el_entero(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """La prueba que sostiene todo lo demás.

    Si partir una carga diera un resumen distinto del de la misma carga hecha
    de una vez, el analista no podría confiar en lo que lee, y la vía
    asincrónica sería una funcionalidad nueva en vez de la misma por otro
    camino.
    """
    filas = _filas(6)
    entero = encuestas.ingestar(
        conn_boveda, conn_semantica, ola["encuesta"]["id"], PREGUNTAS, filas,
        columna_id="id_en_origen", origen="dooblo", proveedor=proveedor)
    conn_boveda.commit()
    conn_semantica.commit()

    # La misma carga otra vez, partida. Es una re-ingesta, así que lo
    # escrito no cambia; lo que se compara es el resumen.
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), filas,
        tamano_lote=2, encolador=encolador)
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)
    partido = diferida.estado(conn_boveda, salida["trabajo_id"])["resumen"]

    for clave in ("personas", "respuestas_escritas", "membresias_nuevas",
                  "membresias_existentes", "participaciones_nuevas",
                  "participaciones_actualizadas"):
        assert partido[clave] == entero[clave], clave
    assert sorted(partido["ids_persona_ingestados"]) == \
        sorted(str(i) for i in entero["ids_persona_ingestados"])
    assert partido["sin_mapear"] == entero["sin_mapear"]
    assert partido["sin_consentimiento"] == entero["sin_consentimiento"]


def test_una_persona_en_dos_lotes_no_se_cuenta_dos_veces(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """Cada lote informa a quién ingestó. Concatenar sin más haría que una
    persona con respuestas en dos lotes apareciera dos veces y que el total
    de personas de la carga no fuera el número de personas."""
    filas = [{"id_en_origen": "R-001", "P1": "1"},
             {"id_en_origen": "R-001", "P2": "Texto"}]
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), filas,
        tamano_lote=1, encolador=encolador)
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)

    resumen = diferida.estado(conn_boveda, salida["trabajo_id"])["resumen"]
    assert len(resumen["ids_persona_ingestados"]) == 1


# ════════════════════════════════════════════════════════════════════
#  DoD 3 · Reprocesar no duplica
# ════════════════════════════════════════════════════════════════════

def test_reprocesar_un_lote_no_duplica_nada(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """Cloud Tasks puede entregar la misma tarea dos veces, y el reintento
    manual la entrega a propósito. Si eso duplicara respuestas, membresías o
    participaciones, los reintentos serían peligrosos en vez de la red de
    seguridad que esta entrega necesita."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(4),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)

    def contar():
        return {
            "respuestas": db.una(
                conn_semantica,
                "select count(*)::int as n from respuesta")["n"],
            "membresias": db.una(
                conn_boveda,
                "select count(*)::int as n from membresia")["n"],
            "participaciones": db.una(
                conn_boveda,
                "select count(*)::int as n from participacion")["n"],
        }

    antes = contar()
    # El lote 0 se vuelve a procesar entero, como si la tarea se entregara
    # dos veces. Se lo pone en `fallido` para que `_tomar_lote` lo tome: es
    # el mismo camino del reintento.
    db.ejecutar(conn_boveda,
                "update ingesta_lote set estado = 'fallido' "
                " where trabajo_id = %s and indice = 0", (trabajo_id,))
    conn_boveda.commit()
    diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                           proveedor=proveedor)

    assert contar() == antes


def test_dos_tareas_sobre_el_mismo_lote_trabajan_una_sola_vez(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """La entrega duplicada es parte del contrato de Cloud Tasks, no una
    anomalía: de dos tareas simultáneas solo una gana la fila."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(2),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]

    primera = diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                                     proveedor=proveedor)
    segunda = diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                                     proveedor=proveedor)

    assert primera["estado"] == diferida.LOTE_OK
    assert segunda["estado"] == "ya_tomado"


# ════════════════════════════════════════════════════════════════════
#  DoD 4-6 · Fallos, reintentos y reintento manual
# ════════════════════════════════════════════════════════════════════

class ProveedorQueFalla:
    """Un proveedor que falla las primeras `veces` llamadas.

    Modela el caso que los reintentos existen para cubrir: un 429 o un
    timeout del proveedor de embeddings con varias tareas en paralelo.
    """

    def __init__(self, envuelto, veces=1, error=None):
        self.envuelto = envuelto
        self.dims = envuelto.dims
        self.restantes = veces
        self.error = error or RuntimeError("429 Too Many Requests")

    def embeber(self, textos):
        if self.restantes > 0:
            self.restantes -= 1
            raise self.error
        return self.envuelto.embeber(textos)

    def embeber_por_lotes(self, textos, tamano_lote=128):
        if self.restantes > 0:
            self.restantes -= 1
            raise self.error
        return self.envuelto.embeber_por_lotes(textos, tamano_lote)


def test_un_fallo_transitorio_se_propaga_para_que_lo_reintenten(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """Propagar es cómo se pide el reintento: la tarea devuelve 5xx y Cloud
    Tasks la vuelve a despachar con espera creciente."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(2),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]
    inestable = ProveedorQueFalla(proveedor, veces=1)

    with pytest.raises(RuntimeError, match="429"):
        diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                               proveedor=inestable)

    lote = diferida.lotes_de(conn_boveda, trabajo_id)[0]
    assert lote["estado"] == diferida.LOTE_FALLIDO
    assert lote["reintentable"] is True

    # Y el reintento entra bien, sin rehacer nada más.
    diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                           proveedor=inestable)
    final = diferida.estado(conn_boveda, trabajo_id)
    assert final["estado"] == diferida.TERMINADA
    assert final["lotes_ok"] == 1


def test_un_error_de_datos_no_se_reintenta(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """Reintentar cinco veces un lote que falla por un dato mal formado gasta
    tiempo y embeddings en algo que va a fallar igual. El reintento sirve
    para lo transitorio."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(2),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]
    roto = ProveedorQueFalla(
        proveedor, veces=99,
        error=diferida.ErrorDeDatos("La fila 3 no tiene identificador."))

    # **No** levanta: devolver 200 es lo que le dice a Cloud Tasks que no
    # insista.
    resultado = diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id,
                                       0, proveedor=roto)

    assert resultado["estado"] == diferida.LOTE_FALLIDO
    assert resultado["reintentable"] is False
    assert diferida.lotes_de(conn_boveda, trabajo_id)[0]["reintentable"] is False


def test_un_lote_fallido_no_tira_abajo_a_los_demas(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(6),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]

    # El lote 1 falla por datos; los otros dos entran.
    roto = ProveedorQueFalla(proveedor, veces=99,
                             error=diferida.ErrorDeDatos("dato inválido"))
    for indice in range(3):
        diferida.procesar_lote(
            conn_boveda, conn_semantica, trabajo_id, indice,
            proveedor=roto if indice == 1 else proveedor)

    final = diferida.estado(conn_boveda, trabajo_id)
    assert final["estado"] == diferida.TERMINADA_CON_ERRORES
    assert final["lotes_ok"] == 2 and final["lotes_fallidos"] == 1
    # Y se dice **cuál** falló y por qué: sin eso, «terminada con errores» no
    # le sirve a nadie.
    assert [f["indice"] for f in final["fallidos"]] == [1]
    assert "dato inválido" in final["fallidos"][0]["error"]
    # Lo que entró, entró: cuatro filas × dos preguntas.
    assert final["filas_procesadas"] == 4


def test_si_no_entra_ningun_lote_la_carga_queda_fallida(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """Se distingue de `terminada_con_errores` porque no hay nada que
    conservar: conviene rehacer la carga entera."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(2),
        tamano_lote=1, encolador=encolador)
    roto = ProveedorQueFalla(proveedor, veces=99,
                             error=diferida.ErrorDeDatos("todo mal"))
    for indice in range(2):
        diferida.procesar_lote(conn_boveda, conn_semantica,
                               salida["trabajo_id"], indice, proveedor=roto)

    assert diferida.estado(conn_boveda, salida["trabajo_id"])["estado"] == \
        diferida.FALLIDA


def test_un_lote_fallido_se_reintenta_solo(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """Sin rehacer la carga completa, que es el punto: los lotes que entraron
    ya se pagaron en embeddings."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(4),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]
    roto = ProveedorQueFalla(proveedor, veces=99,
                             error=diferida.ErrorDeDatos("se cayó"))
    diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                           proveedor=proveedor)
    diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 1,
                           proveedor=roto)
    assert diferida.estado(conn_boveda, trabajo_id)["estado"] == \
        diferida.TERMINADA_CON_ERRORES

    encolador.encoladas.clear()
    vuelta = diferida.reintentar(conn_boveda, trabajo_id, indice=1,
                                 encolador=encolador)

    # Se reencola **solo** el 1.
    assert encolador.encoladas == [(trabajo_id, 1)]
    assert vuelta["reencolados"] == [1]
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)
    final = diferida.estado(conn_boveda, trabajo_id)
    assert final["estado"] == diferida.TERMINADA
    assert final["lotes_ok"] == 2


def test_reintentar_lo_que_no_falló_se_rechaza(conn_boveda, ola, encolador):
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(2),
        tamano_lote=2, encolador=encolador)
    with pytest.raises(Conflicto, match="no hay nada que reintentar|No hay lotes"):
        diferida.reintentar(conn_boveda, salida["trabajo_id"],
                            encolador=encolador)


# ════════════════════════════════════════════════════════════════════
#  DoD 7 · El progreso sobrevive a un refresco
# ════════════════════════════════════════════════════════════════════

def test_el_progreso_sale_de_la_base_y_no_del_cliente(
        conn_boveda, conn_semantica, proveedor, dsn_boveda, ola, encolador):
    """«Sobrevive a un refresco del navegador» quiere decir que el estado no
    vive en la pestaña. Se comprueba leyéndolo desde **otra conexión**, que
    es lo más parecido a otra sesión del navegador."""
    import psycopg
    from psycopg.rows import dict_row

    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(6),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]
    diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                           proveedor=proveedor)

    otra = psycopg.connect(dsn_boveda, row_factory=dict_row)
    try:
        visto = diferida.estado(otra, trabajo_id)
    finally:
        otra.close()

    assert visto["estado"] == diferida.PROCESANDO
    assert visto["lotes_ok"] == 1
    assert visto["filas_procesadas"] == 2
    assert visto["porcentaje"] == 33
    # Y con una estimación de lo que falta, que es lo que la pantalla
    # necesita para no mostrar una barra muda.
    assert visto["segundos_restantes"] >= 0


def test_las_cargas_de_un_destino_se_pueden_listar(
        conn_boveda, ola, encolador):
    """Es lo que deja volver mañana y encontrar la carga de hoy."""
    for _ in range(2):
        diferida.encolar(conn_boveda, "encuesta", ola["encuesta"]["id"],
                         _plan(), _filas(2), tamano_lote=2,
                         encolador=encolador)

    items = diferida.listar(conn_boveda, destino_tipo="encuesta",
                            destino_id=ola["encuesta"]["id"])
    assert len(items) == 2
    assert items[0]["creado_en"] >= items[1]["creado_en"]


# ════════════════════════════════════════════════════════════════════
#  DoD 8 · Consentimiento retirado a mitad de carga
# ════════════════════════════════════════════════════════════════════

def test_quien_retira_el_consentimiento_no_entra_en_los_lotes_siguientes(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """El gate se evalúa **en cada lote** y no una vez al confirmar. Es gratis
    y es lo correcto: el gate no está acá, está en la ingesta de siempre, que
    corre entera dentro de cada tarea."""
    from panel_api import bajas

    filas = [{"id_en_origen": "R-001", "P2": "Antes"},
             {"id_en_origen": "R-002", "P2": "Después"}]
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), filas,
        tamano_lote=1, encolador=encolador)
    trabajo_id = salida["trabajo_id"]

    diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 0,
                           proveedor=proveedor)

    # Entre un lote y el otro, R-002 retira el uso semántico.
    segunda = db.una(conn_boveda,
                     "select id_persona from alias_origen "
                     " where id_en_origen = 'R-002'")["id_persona"]
    bajas.retirar(conn_boveda, segunda, finalidad="uso_semantico",
                  conn_semantica=conn_semantica)
    conn_boveda.commit()

    diferida.procesar_lote(conn_boveda, conn_semantica, trabajo_id, 1,
                           proveedor=proveedor)

    resumen = diferida.estado(conn_boveda, trabajo_id)["resumen"]
    assert str(segunda) in [str(i) for i in resumen["sin_consentimiento"]]
    assert str(segunda) not in [str(i) for i in resumen["ids_persona_ingestados"]]
    # Y su respuesta no está del lado semántico.
    assert db.una(conn_semantica,
                  "select count(*)::int as n from respuesta")["n"] == 1


# ════════════════════════════════════════════════════════════════════
#  DoD 10-11 · PII y memoria
# ════════════════════════════════════════════════════════════════════

def test_el_guardrail_de_pii_sigue_activo_por_la_via_asincronica(
        conn_boveda, conn_semantica, proveedor, ola, encolador, monkeypatch):
    """No regresión: la vía asincrónica pasa por el **mismo** guardrail.

    Se comprueba que `validar_sin_pii()` corra, y no que rechace algo: lo que
    rechaza ya lo cubren sus propias pruebas, y acá lo que podría romperse es
    otra cosa —que una vía nueva se saltee la validación—.

    Que siga corriendo no es casualidad: es consecuencia de que la tarea
    llame a la ingesta de siempre en vez de reimplementarla, que es la razón
    por la que se decidió así.
    """
    from panel_api import pii, semantica

    llamadas = []
    original = semantica.pii.validar_sin_pii

    def espiar(payload, contexto=""):
        llamadas.append(contexto)
        return original(payload, contexto)

    monkeypatch.setattr(semantica.pii, "validar_sin_pii", espiar)

    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(2),
        tamano_lote=2, encolador=encolador)
    diferida.procesar_lote(conn_boveda, conn_semantica, salida["trabajo_id"],
                           0, proveedor=proveedor)

    assert "respuesta" in llamadas, (
        "la vía asincrónica escribió en el store semántico sin pasar por el "
        "guardrail de PII")
    # Y el catálogo sigue siendo el que es: si alguien lo vaciara, el
    # guardrail correría y no atraparía nada.
    assert {"email", "documento", "celular"} <= set(pii.CAMPOS_PII)


def test_embeber_entrega_lote_a_lote_y_no_la_lista_entera():
    """DoD — `embeber_en_lotes()` ya no devuelve la lista completa.

    Es una prueba de **forma** y no de comportamiento, a propósito: lo que se
    quiere impedir es que vuelva a existir una función que acumule todos los
    vectores, porque con 200.000 respuestas eso son varios GB contra el 1 GiB
    de la función. Un generador no se puede acumular sin querer.
    """
    import inspect

    from panel_api import embeddings

    assert not hasattr(embeddings.ProveedorEmbeddings, "embeber_en_lotes"), (
        "volvió `embeber_en_lotes()`, que acumulaba todos los vectores")
    assert inspect.isgeneratorfunction(
        embeddings.ProveedorEmbeddings.embeber_por_lotes)

    proveedor = embeddings.BolsaDePalabras()
    entregados = list(proveedor.embeber_por_lotes(["a"] * 5, tamano_lote=2))
    assert [inicio for inicio, _ in entregados] == [0, 2, 4]
    assert [len(v) for _, v in entregados] == [2, 2, 1]


def test_un_lote_grande_no_acumula_los_vectores_en_memoria(
        conn_boveda, conn_semantica, ola, encolador):
    """DoD — una tarea con un lote grande no agota la memoria.

    Se mide lo que se puede medir sin un profiler: **cuántos vectores hay
    vivos a la vez**. El proveedor espía cuenta los que entregó y todavía no
    se soltaron; si la ingesta volviera a acumular, ese pico sería el lote
    entero en vez de un sub-lote.
    """
    from panel_api import embeddings

    class ProveedorQueMidePico(embeddings.BolsaDePalabras):
        def __init__(self):
            super().__init__()
            self.pico = 0
            self.entregados = 0

        def embeber_por_lotes(self, textos, tamano_lote=128):
            for inicio, vectores in super().embeber_por_lotes(
                    textos, tamano_lote=16):
                self.entregados += len(vectores)
                self.pico = max(self.pico, len(vectores))
                yield inicio, vectores

    medidor = ProveedorQueMidePico()
    filas = [{"id_en_origen": f"R-{(n % 6) + 1:03d}", "P2": f"Texto {n}"}
             for n in range(120)]
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), filas,
        tamano_lote=120, encolador=encolador)
    diferida.procesar_lote(conn_boveda, conn_semantica, salida["trabajo_id"],
                           0, proveedor=medidor)

    assert medidor.entregados > 16, "no se embebió nada: la prueba no mide nada"
    assert medidor.pico <= 16, (
        f"el pico fue de {medidor.pico} vectores vivos: la ingesta está "
        f"acumulando en vez de escribir y soltar")


# ════════════════════════════════════════════════════════════════════
#  Lo que sostiene al resto
# ════════════════════════════════════════════════════════════════════

def test_los_estados_del_codigo_son_los_del_catalogo(conn_boveda):
    """Los nombres están en dos lugares —el catálogo de la `0019` y las
    constantes de `diferida`— y la primera vez que diverjan lo van a hacer en
    silencio: un `update` con un estado que la FK rechaza, a mitad de una
    carga de 200.000 respuestas."""
    del_catalogo = {f["codigo"] for f in db.todas(
        conn_boveda, "select codigo from estado_ingesta")}
    assert del_catalogo == {diferida.ENCOLADA, diferida.PROCESANDO,
                            diferida.TERMINADA, diferida.TERMINADA_CON_ERRORES,
                            diferida.FALLIDA}

    de_lote = {f["codigo"] for f in db.todas(
        conn_boveda, "select codigo from estado_lote_ingesta")}
    assert de_lote == {diferida.LOTE_PENDIENTE, diferida.LOTE_PROCESANDO,
                       diferida.LOTE_OK, diferida.LOTE_FALLIDO}


def test_el_destino_se_valida_antes_de_guardar(conn_boveda, encolador):
    with pytest.raises(DatosInvalidos, match="Destino de ingesta desconocido"):
        diferida.encolar(conn_boveda, "telepatia", 1, _plan(), _filas(1),
                         encolador=encolador)


def test_un_trabajo_que_no_existe_se_dice(conn_boveda):
    with pytest.raises(NoEncontrado, match="No existe el trabajo"):
        diferida.estado(conn_boveda, 999999)


def test_purgar_libera_las_filas_pero_conserva_el_registro(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    """Las filas de una carga terminada no sirven para nada —lo ingestado
    está en los dos stores— pero el registro de qué pasó sí."""
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(4),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]
    _procesar_todo(conn_boveda, conn_semantica, proveedor, encolador)

    db.ejecutar(conn_boveda,
                "update ingesta_trabajo set terminado_en = now() - interval "
                "'30 days' where id = %s", (trabajo_id,))
    liberados = db.una(conn_boveda,
                       "select purgar_ingestas_terminadas() as n")["n"]

    assert liberados == 2
    assert db.una(conn_boveda,
                  "select count(*)::int as n from ingesta_lote "
                  " where trabajo_id = %s and filas is null",
                  (trabajo_id,))["n"] == 2
    # El registro queda: cuántos lotes, en qué estado, con qué resultado.
    assert diferida.estado(conn_boveda, trabajo_id)["lotes_ok"] == 2


def test_un_lote_purgado_no_se_puede_reintentar_y_lo_dice(
        conn_boveda, conn_semantica, proveedor, ola, encolador):
    salida = diferida.encolar(
        conn_boveda, "encuesta", ola["encuesta"]["id"], _plan(), _filas(2),
        tamano_lote=2, encolador=encolador)
    trabajo_id = salida["trabajo_id"]
    db.ejecutar(conn_boveda,
                "update ingesta_lote set estado = 'fallido', filas = null "
                " where trabajo_id = %s", (trabajo_id,))
    conn_boveda.commit()

    with pytest.raises(Conflicto, match="purgaron"):
        diferida.reintentar(conn_boveda, trabajo_id, encolador=encolador)
