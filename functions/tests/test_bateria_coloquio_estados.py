"""La batería de COLOQUIO distingue pasado, fallido y omitido.

`specs`/pedido «corregir los dos chequeos defectuosos de verificar_coloquio»:
contra Cloud SQL la batería daba siempre dos fallos que no eran de la bóveda
—un chequeo de auditoría mal escrito y uno que ahí no puede correr—, y una
batería que falla siempre deja de leerse. Estas pruebas sostienen el arreglo:

* el chequeo de auditoría manda un actor humano y verifica que quede escrito,
  y hay uno nuevo para el caso sin actor;
* el del intruso se **omite** contra Cloud SQL, con motivo, y sigue corriendo
  en el cluster local;
* la cardinalidad no pasa en silencio sobre vistas vacías;
* el veredicto tiene tres estados, y «no está lista» sale solo con fallos.
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
    conn = vc.conectar(_dsn(dsn_boveda, "coloquio_app"))
    yield conn
    conn.close()


@pytest.fixture
def dueno(dsn_boveda):
    conn = vc.conectar(dsn_boveda)
    yield conn
    conn.close()


def _correr(dsn_boveda, **opciones):
    return vc.correr(dsn_boveda, _dsn(dsn_boveda, "coloquio_app"),
                     _dsn(dsn_boveda, "intruso"), imprimir=lambda *_: None,
                     **opciones)


def _por_nombre(resultados):
    return {nombre: (estado, detalle) for nombre, estado, detalle in resultados}


INTRUSO = "un rol sin registrar no consigue nada"


# ════════════════════════════════════════════════════════════════════
#  El veredicto
# ════════════════════════════════════════════════════════════════════

def test_el_veredicto_con_un_omitido_es_afirmativo_y_dice_cual():
    """El ejemplo del pedido, tal cual: 16 pasados y 1 omitido es «lista»,
    y el omitido aparece con su motivo."""
    resultados = [(f"chequeo {i}", vc.PASADO, "ok") for i in range(16)]
    resultados.append((INTRUSO, vc.OMITIDO, vc.MOTIVO_INTRUSO_EN_CLOUD_SQL))
    lineas, hay_fallos = vc.veredicto(resultados)
    assert not hay_fallos
    assert lineas[0] == "17 chequeos · 16 pasados · 0 fallidos · 1 omitido"
    assert f"  ○ {INTRUSO}" in lineas
    assert any("omitido: no verificable contra Cloud SQL" in l for l in lineas)
    assert lineas[-1] == "La bóveda está lista para COLOQUIO."
    assert not any("no está lista" in l for l in lineas)


def test_el_veredicto_sin_omitidos_no_lista_nada_de_mas():
    resultados = [(f"chequeo {i}", vc.PASADO, "ok") for i in range(3)]
    lineas, hay_fallos = vc.veredicto(resultados)
    assert not hay_fallos
    assert lineas == ["3 chequeos · 3 pasados · 0 fallidos · 0 omitidos", "",
                      "La bóveda está lista para COLOQUIO."]


def test_solo_un_fallo_real_dice_que_no_esta_lista():
    resultados = [
        ("a", vc.PASADO, "ok"),
        ("b", vc.OMITIDO, "por algo"),
        ("c", vc.FALLIDO, "la vista repite filas"),
    ]
    lineas, hay_fallos = vc.veredicto(resultados)
    assert hay_fallos
    assert lineas[0] == "3 chequeos · 1 pasado · 1 fallido · 1 omitido"
    assert "  ✗ c" in lineas and "      la vista repite filas" in lineas
    assert lineas[-1] == "La bóveda no está lista para COLOQUIO."


# ════════════════════════════════════════════════════════════════════
#  El intruso: omitido contra Cloud SQL, corre en el cluster local
# ════════════════════════════════════════════════════════════════════

def test_el_cluster_local_no_es_cloud_sql(dueno):
    """Si esto diera `True`, el chequeo del intruso dejaría de correr en el
    único lugar donde se puede probar."""
    assert vc.es_cloud_sql(dueno) is False


def test_el_intruso_corre_en_el_cluster_local(dsn_boveda, conn_boveda):
    estado, detalle = _por_nombre(
        _correr(dsn_boveda, intruso_derivado=True))[INTRUSO]
    assert estado == vc.PASADO, detalle


def test_contra_cloud_sql_el_intruso_se_omite_y_no_cuenta_como_fallo(
        dsn_boveda, conn_boveda, monkeypatch):
    """El DoD: contra Cloud SQL, con la bóveda sana, la batería termina sin
    fallos. El intruso queda omitido con el motivo, y el veredicto es
    afirmativo."""
    monkeypatch.setattr(vc, "es_cloud_sql", lambda _conn: True)
    resultados = _correr(dsn_boveda, intruso_derivado=True)
    por = _por_nombre(resultados)
    estado, motivo = por[INTRUSO]
    assert estado == vc.OMITIDO
    assert "Cloud SQL" in motivo and "credenciales" in motivo
    assert not [r for r in resultados if r[1] == vc.FALLIDO], resultados
    assert [n for n, e, _ in resultados if e == vc.OMITIDO] == [INTRUSO]

    lineas, hay_fallos = vc.veredicto(resultados)
    assert not hay_fallos
    total = len(vc.SIN_DATOS) + len(vc.CON_DATOS)
    assert lineas[0] == (f"{total} chequeos · {total - 1} pasados · "
                         "0 fallidos · 1 omitido")
    assert lineas[-1] == "La bóveda está lista para COLOQUIO."


def test_con_un_intruso_con_credenciales_corre_aunque_sea_cloud_sql(
        dsn_boveda, conn_boveda, monkeypatch):
    """Lo que no se puede probar contra Cloud SQL es conectarse **sin**
    credenciales. Si alguien pasa `DSN_BOVEDA_INTRUSO` con las de un rol sin
    registrar, el chequeo tiene sentido y corre."""
    monkeypatch.setattr(vc, "es_cloud_sql", lambda _conn: True)
    estado, detalle = _por_nombre(
        _correr(dsn_boveda, intruso_derivado=False))[INTRUSO]
    assert estado == vc.PASADO, detalle


# ════════════════════════════════════════════════════════════════════
#  La auditoría del contacto
# ════════════════════════════════════════════════════════════════════

def test_los_dos_chequeos_de_auditoria_pasan_y_dicen_que_verificaron(
        dsn_boveda, conn_boveda):
    por = _por_nombre(_correr(dsn_boveda))
    estado, detalle = por["el contacto legítimo queda auditado"]
    assert estado == vc.PASADO, detalle
    assert vc.ACTOR_DE_PRUEBA in detalle
    estado, detalle = por["el contacto sin actor queda marcado"]
    assert estado == vc.PASADO, detalle
    assert "coloquio_app" in detalle and "sin actor_email" in detalle


def test_el_actor_de_prueba_es_un_email_de_usuario_y_no_el_rol():
    """El contrato de `HANDOFF_coloquio_fase1.md`: `p_actor` es el email del
    usuario humano, no la cuenta de servicio. Si el chequeo volviera a mandar
    el rol, verificaría que la función ignore lo que él mismo le pasó."""
    assert "@" in vc.ACTOR_DE_PRUEBA
    assert "coloquio" not in vc.ACTOR_DE_PRUEBA.split("@")[0].replace("-", "_")
    assert not vc.ACTOR_DE_PRUEBA.endswith(".iam")


def test_el_contrato_de_auditoria_que_los_chequeos_suponen_sigue_igual(dueno):
    """No regresión del lado de la base: el arreglo fue del test, no de la
    función, y las migraciones no se tocaron. Los dos chequeos de auditoría
    dependen de que `contacto_para_convocatoria` registre
    `coalesce(p_actor, session_user)` como actor y `p_actor` tal cual como
    email —nulo si no vino—. Si alguien cambia eso, esta prueba lo dice antes
    que la batería contra producción."""
    with dueno.cursor() as cur:
        cur.execute("""select p.prosrc from pg_proc p
                         join pg_namespace n on n.oid = p.pronamespace
                        where n.nspname = 'public'
                          and p.proname = 'contacto_para_convocatoria'""")
        cuerpo = " ".join(cur.fetchone()["prosrc"].split())
    assert "values (p_id_persona, coalesce(p_actor, session_user), p_actor," in cuerpo


# ════════════════════════════════════════════════════════════════════
#  La cardinalidad no pasa en silencio sobre vistas vacías
# ════════════════════════════════════════════════════════════════════

def test_la_cardinalidad_sobre_vistas_vacias_se_omite(conn_boveda, coloquio, dueno):
    """Con la bóveda vacía, «ninguna clave repetida» es cierto sin probar
    nada. Se informa como omitido, no como verificado."""
    with pytest.raises(vc.Omitido) as motivo:
        vc.una_fila_por_clave(coloquio, dueno)
    assert "vacías" in str(motivo.value)


def test_la_cardinalidad_dice_cuales_vistas_no_pudo_verificar(
        conn_boveda, coloquio, dueno, alta_basica):
    """Con una persona convocable y sin membresías, la convocable se verifica
    y la de fatiga no: el detalle lo dice en vez de sumarla como verificada."""
    from panel_api import personas

    personas.alta(conn_boveda, alta_basica(documento="5333333"))
    conn_boveda.commit()
    detalle = vc.una_fila_por_clave(coloquio, dueno)
    assert "v_persona_convocable: 1" in detalle
    assert "v_fatiga_panelista: vacía, sin verificar" in detalle


def test_en_la_bateria_la_cardinalidad_corre_sobre_el_escenario(
        dsn_boveda, conn_boveda):
    """Por eso está entre los chequeos con escenario: ahí hay una persona con
    las dos finalidades —el caso del bug— y las vistas no están vacías."""
    estado, detalle = _por_nombre(
        _correr(dsn_boveda))["una fila por persona en cada vista"]
    assert estado == vc.PASADO, detalle
    assert "vacía" not in detalle


def test_en_solo_lectura_lo_que_necesita_escenario_queda_omitido(
        dsn_boveda, conn_boveda):
    resultados = _correr(dsn_boveda, solo_lectura=True)
    assert len(resultados) == len(vc.SIN_DATOS) + len(vc.CON_DATOS)
    omitidos = {n: d for n, e, d in resultados if e == vc.OMITIDO}
    assert "el contacto legítimo queda auditado" in omitidos
    assert all("solo-lectura" in d or "vacías" in d for d in omitidos.values())
    assert not [r for r in resultados if r[1] == vc.FALLIDO], resultados


def test_los_omitidos_con_el_mismo_motivo_van_juntos():
    resultados = [("a", vc.OMITIDO, "uno"), ("b", vc.OMITIDO, "uno"),
                  ("c", vc.OMITIDO, "otro")]
    lineas, _ = vc.veredicto(resultados)
    assert lineas[1:6] == ["  ○ a", "  ○ b", "      omitido: uno",
                           "  ○ c", "      omitido: otro"]
