"""R5.8 — la bóveda vista desde afuera, con el rol del segundo consumidor.

La batería de verdad vive en `scripts/verificar_coloquio.py`, porque tiene que
poder correrse contra la instancia real el día de la puesta en marcha. Acá se
la ejecuta en cada build, más los chequeos que solo tienen sentido en el
cluster de pruebas: los que necesitan romper algo a propósito —otorgarle un
privilegio de más al rol, o dárselo a un intruso— para comprobar que la prueba
de privilegios efectivos se entera.

Una prueba de seguridad que nunca vio un fallo no prueba nada.
"""

import pathlib
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

import verificar_coloquio as vc  # noqa: E402


def _dsn(dsn_boveda, usuario):
    return vc._dsn_con_usuario(dsn_boveda, usuario)


@pytest.fixture
def coloquio(dsn_boveda):
    """Una conexión con el rol del consumidor. Autocommit, como en el cliente:
    los rechazos son la mitad de los chequeos y no tienen que dejar la sesión
    abortada."""
    conn = vc.conectar(_dsn(dsn_boveda, "coloquio_app"))
    yield conn
    conn.close()


@pytest.fixture
def dueno(dsn_boveda):
    conn = vc.conectar(dsn_boveda)
    yield conn
    conn.close()


# ════════════════════════════════════════════════════════════════════
#  La batería completa
# ════════════════════════════════════════════════════════════════════

def test_la_bateria_pasa_entera(dsn_boveda, conn_boveda):
    """El DoD de la fase, punto 4. `conn_boveda` está por el truncate: la
    batería arma y borra su propio escenario, pero corre contra la base que
    dejó la prueba anterior y conviene que arranque limpia."""
    resultados = vc.correr(
        dsn_boveda,
        _dsn(dsn_boveda, "coloquio_app"),
        _dsn(dsn_boveda, "intruso"),
        imprimir=lambda *_: None)
    fallaron = [(nombre, detalle) for nombre, ok, detalle in resultados if not ok]
    assert not fallaron, fallaron
    # Y que efectivamente corrió todo, no que la lista vino vacía.
    assert len(resultados) == len(vc.SIN_DATOS) + len(vc.CON_DATOS)


def test_el_modo_solo_lectura_no_escribe_nada(dsn_boveda, dueno):
    """Es el modo con el que se apunta a producción, así que tiene que ser
    verdad que no toca nada."""
    with dueno.cursor() as cur:
        cur.execute("select count(*) as n from persona")
        antes = cur.fetchone()["n"]
    resultados = vc.correr(dsn_boveda, _dsn(dsn_boveda, "coloquio_app"),
                           solo_lectura=True, imprimir=lambda *_: None)
    assert all(ok for _, ok, _ in resultados)
    with dueno.cursor() as cur:
        cur.execute("select count(*) as n from persona")
        assert cur.fetchone()["n"] == antes
        cur.execute("select count(*) as n from reidentificacion")
        assert cur.fetchone()["n"] == 0


# ════════════════════════════════════════════════════════════════════
#  Que la lista blanca muerda
# ════════════════════════════════════════════════════════════════════

def test_un_privilegio_de_mas_sobre_una_tabla_rompe_la_prueba(coloquio, dueno):
    with dueno.cursor() as cur:
        cur.execute("grant select on persona to coloquio_app")
    try:
        with pytest.raises(vc.Falla) as fallo:
            vc.privilegios_de_relaciones(coloquio, dueno)
        assert "persona" in str(fallo.value)
    finally:
        with dueno.cursor() as cur:
            cur.execute("revoke select on persona from coloquio_app")
    # Y una vez revocado, vuelve a pasar: la prueba mide el estado, no se
    # queda pegada.
    vc.privilegios_de_relaciones(coloquio, dueno)


def test_un_execute_de_mas_rompe_la_prueba(coloquio, dueno):
    """El caso real: una función nace con `execute` otorgado a `public`. Si la
    migración se olvida de revocarlo, el rol la puede llamar sin que nadie se
    lo haya otorgado."""
    with dueno.cursor() as cur:
        cur.execute("grant execute on function f_atributo_persona(timestamptz) "
                    "to public")
    try:
        with pytest.raises(vc.Falla) as fallo:
            vc.privilegios_de_funciones(coloquio, dueno)
        assert "f_atributo_persona" in str(fallo.value)
    finally:
        with dueno.cursor() as cur:
            cur.execute("revoke execute on function f_atributo_persona(timestamptz) "
                        "from public")
    vc.privilegios_de_funciones(coloquio, dueno)


def test_una_escritura_otorgada_por_error_rompe_la_prueba(coloquio, dueno):
    with dueno.cursor() as cur:
        cur.execute("grant insert on consentimiento to coloquio_app")
    try:
        with pytest.raises(vc.Falla) as fallo:
            vc.sin_escritura_en_ninguna_tabla(coloquio, dueno)
        assert "consentimiento" in str(fallo.value)
    finally:
        with dueno.cursor() as cur:
            cur.execute("revoke insert on consentimiento from coloquio_app")


# ════════════════════════════════════════════════════════════════════
#  R5.6 — el origen se deriva, no se declara
# ════════════════════════════════════════════════════════════════════

def test_un_rol_sin_registrar_no_puede_ni_decir_quien_es(dsn_boveda, dueno):
    """Defensa en profundidad, y las dos capas importan.

    La primera es el `revoke … from public`: el intruso ni siquiera puede
    llamar a la función. La segunda es que, aun pudiendo, `sistema_de_la_conexion()`
    no lo reconoce. Acá se le otorga el `execute` a propósito para llegar a la
    segunda, porque es la que falló antes: la función caía en `'paneles'` por
    omisión y el intruso se llevaba el contacto **firmado como `paneles`**.
    """
    intruso = vc.conectar(_dsn(dsn_boveda, "intruso"))
    try:
        with dueno.cursor() as cur:
            cur.execute("grant usage on schema public to intruso")
            cur.execute("grant execute on function sistema_de_la_conexion() "
                        "to intruso")
        try:
            with intruso.cursor() as cur:
                with pytest.raises(Exception) as fallo:
                    cur.execute("select sistema_de_la_conexion()")
            assert "no está en" in str(fallo.value)
            assert "intruso" in str(fallo.value)
        finally:
            with dueno.cursor() as cur:
                cur.execute("revoke execute on function sistema_de_la_conexion() "
                            "from intruso")
                cur.execute("revoke usage on schema public from intruso")
    finally:
        intruso.close()


def test_el_sistema_no_es_declarable_por_el_llamador(coloquio, dueno):
    """`contacto_para_convocatoria` recibe un `p_actor` de texto libre, que es
    útil para saber qué usuario del otro sistema pidió el dato. Lo que no
    puede es cambiar de qué **sistema** vino."""
    import inspect  # noqa: F401  (sólo para dejar claro que esto mira la firma)

    with dueno.cursor() as cur:
        cur.execute("""select pg_get_function_arguments(p.oid) as args
                         from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                        where n.nspname='public'
                          and p.proname='contacto_para_convocatoria'""")
        args = cur.fetchone()["args"]
    assert "sistema" not in args, (
        "si el sistema fuera un parámetro, el consumidor podría firmarse como "
        "otro y la auditoría de R5.6 no valdría nada")


# ════════════════════════════════════════════════════════════════════
#  R5.1 — el gate es de la base, no de Python
# ════════════════════════════════════════════════════════════════════

def test_la_vista_y_el_gate_de_python_dicen_lo_mismo(conn_boveda, alta_basica):
    """La métrica de la fase es «implementaciones del gate: de dos a una».
    Esta prueba es la que lo sostiene: si alguien vuelve a escribir el gate en
    Python, las dos respuestas se van a separar en algún caso y esto lo ve."""
    from panel_api import consentimiento, db, personas

    con = personas.alta(conn_boveda, alta_basica(documento="5111111"))["id_persona"]
    # Con `uso_semantico` y sin `contacto_participacion`: es el caso que hace
    # valer la limitación de finalidad, y el que un gate que mirara «¿tiene
    # algún consentimiento?» dejaría pasar.
    sin = personas.alta(
        conn_boveda,
        alta_basica(documento="5222222", finalidades=("uso_semantico",))
    )["id_persona"]

    habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        conn_boveda, [con, sin], consentimiento.CONTACTO)
    assert habilitadas == [str(con)]
    assert bloqueadas == [str(sin)]

    de_la_vista = {
        str(f["id_persona"]) for f in db.todas(
            conn_boveda,
            "select id_persona from v_persona_convocable "
            " where finalidad = 'contacto_participacion'")}
    assert set(habilitadas) <= de_la_vista
    assert str(sin) not in de_la_vista


def test_el_consentimiento_no_alcanza_si_la_persona_no_esta_activa(
        conn_boveda, alta_basica):
    """La vista pide tres cosas, no una: consentimiento vigente, persona
    `activa`, y sin lápida en `persona_borrada`. Las dos últimas son las que
    el gate de Python no miraba, y son las que evitan convocar a alguien cuya
    alta quedó en revisión o cuya baja se está ejecutando."""
    from panel_api import db, personas

    def convocable(id_persona):
        return bool(db.una(
            conn_boveda,
            "select 1 from v_persona_convocable where id_persona = %s",
            (id_persona,)))

    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="5333333"))["id_persona"]
    assert convocable(id_persona)

    db.ejecutar(
        conn_boveda,
        "update persona set estado = 'pendiente_consentimiento' "
        " where id_persona = %s", (id_persona,))
    assert not convocable(id_persona)

    db.ejecutar(conn_boveda,
                "update persona set estado = 'activa' where id_persona = %s",
                (id_persona,))
    assert convocable(id_persona)

    # La lápida se escribe antes del `delete`. En esa ventana la persona
    # todavía existe y no tiene que aparecer.
    db.ejecutar(
        conn_boveda,
        """insert into persona_borrada (id_persona, motivo, finalidad)
           values (%s, 'retiro_consentimiento', 'todas')""",
        (str(id_persona),))
    assert not convocable(id_persona)


# ════════════════════════════════════════════════════════════════════
#  R5.3 — la cascada, desde la aplicación
# ════════════════════════════════════════════════════════════════════

def test_una_baja_le_deja_pendiente_a_cada_consumidor_activo(
        conn_boveda, conn_semantica, alta_basica):
    from panel_api import bajas, db, personas

    db.ejecutar(conn_boveda,
                "update sistema_consumidor set activo = true where codigo='coloquio'")
    try:
        id_persona = personas.alta(
            conn_boveda, alta_basica(documento="5444444"))["id_persona"]
        resultado = bajas.retirar(conn_boveda, id_persona, actor="dpo@equipos.com.uy",
                                  conn_semantica=conn_semantica)
        # `paneles` borró su store semántico en el acto y cerró el suyo;
        # COLOQUIO queda abierto hasta que confirme.
        assert resultado["consumidores_notificados"] == 2
        abiertos = {p["sistema"] for p in resultado["borrados_sin_confirmar"]}
        assert abiertos == {"coloquio"}, (
            "el pendiente de `paneles` tiene que cerrarse solo cuando el "
            "borrado semántico salió bien, y el de COLOQUIO quedar abierto")
    finally:
        db.ejecutar(
            conn_boveda,
            "update sistema_consumidor set activo = false where codigo='coloquio'")


def test_un_consumidor_inactivo_no_recibe_pendientes(
        conn_boveda, conn_semantica, alta_basica):
    """COLOQUIO entra al registro inactivo y se activa el día que sale a
    producción. Mientras tanto no puede recibir bajas: no hay nadie del otro
    lado que las confirme, y el tablero del DPO nacería lleno de ruido."""
    from panel_api import bajas, personas

    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="5555555"))["id_persona"]
    resultado = bajas.retirar(conn_boveda, id_persona,
                              conn_semantica=conn_semantica)
    assert resultado["consumidores_notificados"] == 1
    assert resultado["borrados_sin_confirmar"] == []


def test_un_borrado_semantico_fallido_deja_el_pendiente_abierto(
        conn_boveda, alta_basica):
    """La bóveda no espera al consumidor: la baja se completa igual y el
    pendiente queda para reintentar."""
    from panel_api import bajas, personas

    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="5666666"))["id_persona"]
    # Sin conexión semántica el borrado de embeddings queda pendiente.
    resultado = bajas.retirar(conn_boveda, id_persona, conn_semantica=None)
    assert resultado["semantica"]["estado"] == "pendiente"
    assert resultado["pii_borrada"] is True
    abiertos = {p["sistema"] for p in resultado["borrados_sin_confirmar"]}
    assert abiertos == {"paneles"}
