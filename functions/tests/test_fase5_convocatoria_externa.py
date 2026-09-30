"""R5.2.a — la convocatoria activa se verifica por sistema.

El hueco que estas pruebas cubren tenía una forma incómoda: la `0014`
**decía** implementar R5.2 —«una convocatoria activa en el sistema que
llama»— y en realidad implementaba solo el caso de `paneles`. Contra un
único consumidor eso no se nota: el que llama es siempre el dueño de las
tablas donde está la convocatoria. Con el segundo aparece de golpe, y del
peor modo, porque la bóveda no dice «esto no está implementado» sino «esa
persona no tiene convocatoria activa», que suena a dato y es un defecto.

Por eso la primera prueba de acá es el escenario que bloqueaba a COLOQUIO en
producción, escrito tal cual: persona que consintió, `paneles` la tiene
convocada, y el otro consumidor no puede leer su contacto.
"""

import pathlib
import sys

import pytest

from conftest import VERSION_TEXTO

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

import verificar_coloquio as vc  # noqa: E402

from panel_api import bajas, db  # noqa: E402


# ════════════════════════════════════════════════════════════════════
#  Escenario
# ════════════════════════════════════════════════════════════════════
# Hacen falta dos conexiones con roles distintos —el dueño y el consumidor—,
# así que el escenario no puede vivir en la transacción de `conn_boveda`: lo
# que una conexión no confirmó, la otra no lo ve. Se escribe con autocommit y
# se borra al final.

@pytest.fixture
def dueno(conn_boveda, dsn_boveda):
    """`conn_boveda` está por sus efectos: deja la base limpia y publica los
    textos de consentimiento, que desde R5.7.d son precondición del alta."""
    conn = vc.conectar(dsn_boveda)
    yield conn
    with conn.cursor() as cur:
        cur.execute("delete from persona where documento like 'R52A-%'")
        cur.execute("delete from panel where nombre like 'R52A-%'")
    conn.close()


@pytest.fixture
def coloquio(dsn_boveda):
    """El consumidor, con su propio rol. Autocommit, como el cliente real:
    los rechazos son la mitad de lo que se prueba acá y no tienen que dejar
    la sesión abortada."""
    conn = vc.conectar(vc._dsn_con_usuario(dsn_boveda, "coloquio_app"))
    yield conn
    conn.close()


def _persona(dueno, sufijo, con_consentimiento=True, finalidad="contacto_participacion"):
    with dueno.cursor() as cur:
        cur.execute(
            """insert into persona (documento, nombre, celular, email, estado)
               values (%s, 'Persona R5.2.a', %s, %s, 'activa')
               returning id_persona""",
            (f"R52A-{sufijo}", f"+59809900{sufijo}", f"r52a-{sufijo}@ejemplo.invalid"))
        id_persona = cur.fetchone()["id_persona"]
        if con_consentimiento:
            cur.execute(
                """insert into consentimiento
                          (id_persona, finalidad, estado, version_texto)
                   values (%s, %s, 'vigente', %s)""",
                (id_persona, finalidad, VERSION_TEXTO))
    return id_persona


def _convocar_en_paneles(dueno, id_persona):
    """Una participación en una encuesta abierta: la convocatoria tal como la
    entiende `paneles`."""
    with dueno.cursor() as cur:
        cur.execute("insert into panel (nombre) values ('R52A-panel') "
                    "returning id")
        panel_id = cur.fetchone()["id"]
        cur.execute(
            """insert into encuesta (panel_id, nombre, estado)
               values (%s, 'R52A-encuesta', 'en_campo') returning id""",
            (panel_id,))
        cur.execute("insert into participacion (encuesta_id, id_persona) "
                    "values (%s, %s)", (cur.fetchone()["id"], id_persona))


def _contacto(conn, id_persona, canal="celular"):
    with conn.cursor() as cur:
        cur.execute("select contacto_para_convocatoria(%s, %s) as dato",
                    (id_persona, canal))
        return cur.fetchone()["dato"]


# ════════════════════════════════════════════════════════════════════
#  El defecto que bloqueaba a COLOQUIO
# ════════════════════════════════════════════════════════════════════

def test_la_convocatoria_de_paneles_no_habilita_a_otro_sistema(dueno, coloquio):
    """El caso exacto del reporte: consintió, `paneles` la convocó, y el otro
    consumidor igual no puede leer su contacto. Antes de la `0016` esto
    **pasaba** —la participación ajena le servía a cualquiera—, y era el
    agujero: leer un contacto sin haber convocado a nadie."""
    id_persona = _persona(dueno, "001")
    _convocar_en_paneles(dueno, id_persona)

    with pytest.raises(Exception) as fallo:
        _contacto(coloquio, id_persona)
    assert "convocatoria activa" in str(fallo.value)
    # Y el mensaje nombra el sistema: sin eso, quien lo lee no sabe si le
    # falta declarar o si la persona no consintió.
    assert "coloquio" in str(fallo.value)


def test_declarar_la_convocatoria_habilita_el_contacto(dueno, coloquio):
    """La contracara: con su propia convocatoria declarada, el mismo pedido
    funciona. Es lo que desbloquea la Fase 1 de COLOQUIO."""
    id_persona = _persona(dueno, "002")
    with coloquio.cursor() as cur:
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion-7', now() + interval '2 days')",
            (id_persona,))
    assert _contacto(coloquio, id_persona) == "+59809900002"


def test_paneles_sigue_andando_como_antes(dueno):
    """La `0016` toca la función que usa el incumbente. Su camino —la
    participación en una encuesta abierta— tiene que seguir alcanzando, sin
    declarar nada."""
    id_persona = _persona(dueno, "003")
    _convocar_en_paneles(dueno, id_persona)
    assert _contacto(dueno, id_persona) == "+59809900003"


def test_una_declaracion_ajena_no_sirve(dueno, coloquio):
    """Declarada por otro sistema, para la misma persona. Si esto habilitara,
    el chequeo por sistema sería decorativo."""
    id_persona = _persona(dueno, "004")
    with dueno.cursor() as cur:
        cur.execute(
            """insert into convocatoria_externa
                      (id_persona, sistema, referencia, vence_en)
               values (%s, 'paneles', 'sesion-de-paneles', now() + interval '2 days')""",
            (id_persona,))
    with pytest.raises(Exception) as fallo:
        _contacto(coloquio, id_persona)
    assert "convocatoria activa" in str(fallo.value)


def test_una_declaracion_vencida_no_habilita(dueno, coloquio):
    """El vencimiento es lo que hace que «declarar» no sea «tener acceso»."""
    id_persona = _persona(dueno, "005")
    with coloquio.cursor() as cur:
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion-vieja', now() + interval '1 day')",
            (id_persona,))
    assert _contacto(coloquio, id_persona)  # mientras está vigente, entrega

    with dueno.cursor() as cur:
        cur.execute("update convocatoria_externa set vence_en = now() - interval '1 hour' "
                    " where id_persona = %s", (id_persona,))
    with pytest.raises(Exception) as fallo:
        _contacto(coloquio, id_persona)
    assert "convocatoria activa" in str(fallo.value)


# ════════════════════════════════════════════════════════════════════
#  Lo que la declaración no deja hacer
# ════════════════════════════════════════════════════════════════════

def test_no_se_declara_por_mas_de_sesenta_dias(dueno, coloquio):
    id_persona = _persona(dueno, "006")
    with pytest.raises(Exception) as fallo:
        with coloquio.cursor() as cur:
            cur.execute(
                "select declarar_convocatoria(%s, 'larga', now() + interval '61 days')",
                (id_persona,))
    assert "60 días" in str(fallo.value)


def test_no_se_declara_con_vencimiento_en_el_pasado(dueno, coloquio):
    """Nace muerta, y lo más probable es que sea un error de zona horaria del
    llamador. Mejor que falle al declarar que al pedir el contacto."""
    id_persona = _persona(dueno, "007")
    with pytest.raises(Exception) as fallo:
        with coloquio.cursor() as cur:
            cur.execute(
                "select declarar_convocatoria(%s, 'ayer', now() - interval '1 day')",
                (id_persona,))
    assert "pasado" in str(fallo.value)


def test_no_se_declara_sin_referencia(dueno, coloquio):
    id_persona = _persona(dueno, "008")
    with pytest.raises(Exception) as fallo:
        with coloquio.cursor() as cur:
            cur.execute(
                "select declarar_convocatoria(%s, '   ', now() + interval '1 day')",
                (id_persona,))
    assert "referencia" in str(fallo.value)


def test_no_se_declara_a_quien_no_consintio(dueno, coloquio):
    """El gate va también al declarar, no solo al leer el contacto. Si no
    estuviera, la bóveda guardaría «tal sistema convocó a esta persona» de
    alguien que nunca aceptó que lo contacten."""
    id_persona = _persona(dueno, "009", con_consentimiento=False)
    with pytest.raises(Exception) as fallo:
        with coloquio.cursor() as cur:
            cur.execute(
                "select declarar_convocatoria(%s, 'sesion', now() + interval '1 day')",
                (id_persona,))
    assert "consentimiento vigente" in str(fallo.value)


def test_el_consumidor_no_escribe_la_tabla_a_mano(dueno, coloquio):
    """La función es la única vía. Con `insert` directo se saltearía el gate,
    el tope y la derivación del sistema desde la conexión."""
    id_persona = _persona(dueno, "010")
    with pytest.raises(Exception) as fallo:
        with coloquio.cursor() as cur:
            cur.execute(
                """insert into convocatoria_externa
                          (id_persona, sistema, referencia, vence_en)
                   values (%s, 'coloquio', 'a mano', now() + interval '999 days')""",
                (id_persona,))
    assert "permission denied" in str(fallo.value).lower()


def test_reprogramar_no_duplica_ni_estira_sin_limite(dueno, coloquio):
    """Reprogramar una sesión es volver a declarar la misma referencia. Tiene
    que mover la fecha, no dejar dos filas; y el tope se reaplica."""
    id_persona = _persona(dueno, "011")
    with coloquio.cursor() as cur:
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion-9', now() + interval '1 day')",
            (id_persona,))
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion-9', now() + interval '10 days')",
            (id_persona,))
    with dueno.cursor() as cur:
        cur.execute("""select count(*) as n,
                              max(vence_en) > now() + interval '9 days' as movida
                         from convocatoria_externa where id_persona = %s""",
                    (id_persona,))
        fila = cur.fetchone()
    assert fila["n"] == 1 and fila["movida"]

    with pytest.raises(Exception) as fallo:
        with coloquio.cursor() as cur:
            cur.execute(
                "select declarar_convocatoria(%s, 'sesion-9', now() + interval '80 days')",
                (id_persona,))
    assert "60 días" in str(fallo.value)


# ════════════════════════════════════════════════════════════════════
#  Retención: la declaración no sobrevive a la baja ni a su vencimiento
# ════════════════════════════════════════════════════════════════════

def test_el_borrado_de_la_persona_se_lleva_las_declaraciones(dueno, coloquio):
    id_persona = _persona(dueno, "012")
    with coloquio.cursor() as cur:
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion', now() + interval '1 day')",
            (id_persona,))
    with dueno.cursor() as cur:
        cur.execute("delete from persona where id_persona = %s", (id_persona,))
        cur.execute("select count(*) as n from convocatoria_externa "
                    " where id_persona = %s", (id_persona,))
        assert cur.fetchone()["n"] == 0


def test_el_retiro_de_contacto_borra_las_declaraciones(dueno, coloquio):
    """Baja parcial: la persona sigue existiendo, así que la FK no alcanza.
    La cascada tiene que ocuparse. No es que quede una puerta abierta —el
    gate de consentimiento las bloquea igual— pero conservarlas sería
    retención sin finalidad."""
    id_persona = _persona(dueno, "013")
    with coloquio.cursor() as cur:
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion', now() + interval '1 day')",
            (id_persona,))
    with dueno.cursor() as cur:
        cur.execute("select generar_borrados_pendientes(%s, 'contacto_participacion')",
                    (id_persona,))
        cur.execute("select count(*) as n from convocatoria_externa "
                    " where id_persona = %s", (id_persona,))
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from persona where id_persona = %s",
                    (id_persona,))
        assert cur.fetchone()["n"] == 1, "la baja parcial no borra a la persona"


def test_un_retiro_de_otra_finalidad_no_las_toca(dueno, coloquio):
    """Retirar `uso_semantico` no dice nada sobre el contacto. Borrar acá
    sería una cascada que se pasa de largo."""
    id_persona = _persona(dueno, "014")
    with dueno.cursor() as cur:
        cur.execute(
            """insert into consentimiento
                      (id_persona, finalidad, estado, version_texto)
               values (%s, 'uso_semantico', 'vigente', %s)""",
            (id_persona, VERSION_TEXTO))
    with coloquio.cursor() as cur:
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion', now() + interval '1 day')",
            (id_persona,))
    with dueno.cursor() as cur:
        cur.execute("select generar_borrados_pendientes(%s, 'uso_semantico')",
                    (id_persona,))
        cur.execute("select count(*) as n from convocatoria_externa "
                    " where id_persona = %s", (id_persona,))
        assert cur.fetchone()["n"] == 1


def test_la_purga_se_lleva_las_vencidas_y_deja_las_demas(dueno, coloquio):
    vigente = _persona(dueno, "015")
    recien = _persona(dueno, "016")
    vieja = _persona(dueno, "017")
    with coloquio.cursor() as cur:
        for quien in (vigente, recien, vieja):
            cur.execute(
                "select declarar_convocatoria(%s, 'sesion', now() + interval '1 day')",
                (quien,))
    with dueno.cursor() as cur:
        # Una venció hace una semana —todavía se conserva, para que la
        # ventana quede auditable— y otra hace cuarenta días.
        cur.execute("update convocatoria_externa set vence_en = now() - interval '7 days'"
                    " where id_persona = %s", (recien,))
        cur.execute("update convocatoria_externa set vence_en = now() - interval '40 days'"
                    " where id_persona = %s", (vieja,))
        cur.execute("select purgar_convocatorias_externas() as n")
        assert cur.fetchone()["n"] == 1

        cur.execute("select id_persona from convocatoria_externa order by declarada_en")
        quedaron = {f["id_persona"] for f in cur.fetchall()}
    assert quedaron == {vigente, recien}


def test_la_baja_de_paneles_deja_la_tabla_limpia(conn_boveda, dueno, coloquio):
    """El camino real de una baja, por `bajas.retirar()`, no por la función
    de base a mano."""
    id_persona = _persona(dueno, "018")
    with coloquio.cursor() as cur:
        cur.execute(
            "select declarar_convocatoria(%s, 'sesion', now() + interval '1 day')",
            (id_persona,))

    bajas.retirar(conn_boveda, id_persona, actor="dpo@equipos.com.uy")
    conn_boveda.commit()

    assert db.una(
        conn_boveda,
        "select count(*) as n from convocatoria_externa where id_persona = %s",
        (id_persona,))["n"] == 0


# ════════════════════════════════════════════════════════════════════
#  La superficie
# ════════════════════════════════════════════════════════════════════

def test_la_purga_no_se_le_otorga_al_consumidor(coloquio, dueno):
    """Quien declara no tiene por qué poder borrar declaraciones: ni las
    suyas —borrar el rastro de lo que pidió— ni las de otro."""
    with dueno.cursor() as cur:
        cur.execute("""select has_function_privilege(
                         'coloquio_app', 'purgar_convocatorias_externas(int)',
                         'EXECUTE') as puede""")
        assert cur.fetchone()["puede"] is False


def test_el_create_or_replace_no_reabrio_el_contacto_a_public(dueno):
    """La `0016` reemplaza `contacto_para_convocatoria` con `create or
    replace`, que conserva los privilegios. «Conserva» es exactamente la
    clase de cosa que uno cree: si en algún momento pasara a recrearse con
    `drop`+`create`, la función nacería con `execute` para `public` y
    cualquier rol conectado leería contactos."""
    with dueno.cursor() as cur:
        cur.execute("""select
              has_function_privilege('public', %s, 'EXECUTE')       as publico,
              has_function_privilege('coloquio_app', %s, 'EXECUTE') as consumidor""",
                    ("contacto_para_convocatoria(uuid,text,text,text)",) * 2)
        fila = cur.fetchone()
    assert fila["publico"] is False
    assert fila["consumidor"] is True
