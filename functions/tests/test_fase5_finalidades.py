"""R5.7 — el modelo de consentimiento que el cualitativo necesita.

Cuatro reglas nuevas, y las cuatro las hace valer la base:

  · las finalidades son un catálogo, no un `check`;
  · una finalidad de ámbito `estudio` exige `ref_estudio`, y una de ámbito
    `persona` lo prohíbe;
  · no se otorga una finalidad sin una versión activa de su texto;
  · el retiro nunca se bloquea, aunque el texto se haya desactivado.
"""

import uuid

import pytest

from panel_api import consentimiento, db, personas


CUALITATIVAS = ("grabacion_av", "moderacion_automatizada",
                "uso_semantico_cuali", "difusion_verbatim")


# ════════════════════════════════════════════════════════════════════
#  El catálogo
# ════════════════════════════════════════════════════════════════════

def test_el_catalogo_tiene_las_seis_finalidades(conn_boveda):
    codigos = {f["codigo"] for f in db.todas(
        conn_boveda, "select codigo from finalidad_consentimiento")}
    assert {"contacto_participacion", "uso_semantico", *CUALITATIVAS} == codigos


def test_las_que_paneles_sabe_tratar_siguen_en_el_catalogo(conn_boveda):
    """`consentimiento.FINALIDADES` no es el catálogo: es lo que esta
    aplicación sabe tratar. Pero tiene que ser un subconjunto suyo, y activo:
    si alguien desactivara `contacto_participacion`, el alta dejaría de
    funcionar y conviene enterarse acá."""
    activas = {f["codigo"] for f in db.todas(
        conn_boveda, "select codigo from finalidad_consentimiento where activa")}
    assert set(consentimiento.FINALIDADES) <= activas


def test_una_finalidad_inexistente_la_rechaza_la_base(conn_boveda, alta_basica):
    """El guardia de verdad es la FK, no el `if` de Python: un consumidor que
    escriba por SQL no pasa por `consentimiento.py`."""
    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="6000001"))["id_persona"]
    with pytest.raises(Exception) as fallo:
        db.ejecutar(
            conn_boveda,
            """insert into consentimiento (id_persona, finalidad, version_texto)
               values (%s, 'telepatia', 'v1')""", (id_persona,))
    assert "finalidad" in str(fallo.value).lower()
    conn_boveda.rollback()


# ════════════════════════════════════════════════════════════════════
#  Ámbito
# ════════════════════════════════════════════════════════════════════

def _otorgar(conn, id_persona, finalidad, version, ref_estudio=None):
    return db.ejecutar(
        conn,
        """insert into consentimiento
                  (id_persona, finalidad, version_texto, ref_estudio)
           values (%s, %s, %s, %s)""",
        (id_persona, finalidad, version, ref_estudio))


@pytest.fixture
def con_textos(conn_boveda):
    """Publica una versión de cada finalidad. Es precondición desde R5.7.d, y
    en producción también lo es: el texto se publica antes de pedir el
    consentimiento, no después."""
    for finalidad in CUALITATIVAS:
        db.ejecutar(
            conn_boveda,
            """insert into texto_consentimiento (finalidad, version, cuerpo)
               values (%s, 'cuali-v1', 'Texto de prueba.')
               on conflict (finalidad, version) do nothing""", (finalidad,))
    return "cuali-v1"


def test_una_finalidad_de_estudio_exige_ref_estudio(conn_boveda, alta_basica,
                                                    con_textos):
    """Consentir que te graben «en general» no es consentimiento. Consentir
    que te graben en *este* grupo, sí."""
    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="6000002"))["id_persona"]
    with pytest.raises(Exception) as fallo:
        _otorgar(conn_boveda, id_persona, "grabacion_av", con_textos)
    assert "ref_estudio" in str(fallo.value)
    conn_boveda.rollback()


def test_una_finalidad_de_persona_prohibe_ref_estudio(conn_boveda, alta_basica,
                                                      con_textos):
    """Y al revés: un `uso_semantico_cuali` atado a un estudio se retiraría
    por estudio, que no es lo que significa."""
    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="6000003"))["id_persona"]
    with pytest.raises(Exception):
        _otorgar(conn_boveda, id_persona, "uso_semantico_cuali", con_textos,
                 ref_estudio=str(uuid.uuid4()))
    conn_boveda.rollback()


def test_el_consentimiento_de_un_estudio_no_cruza_a_otro(conn_boveda,
                                                         alta_basica, con_textos):
    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="6000004"))["id_persona"]
    estudio_a, estudio_b = str(uuid.uuid4()), str(uuid.uuid4())
    _otorgar(conn_boveda, id_persona, "grabacion_av", con_textos, estudio_a)

    def vigente_para(estudio):
        return bool(db.una(
            conn_boveda,
            """select 1 from v_persona_convocable
                where id_persona = %s and finalidad = 'grabacion_av'
                  and ref_estudio = %s""", (id_persona, estudio)))

    assert vigente_para(estudio_a)
    assert not vigente_para(estudio_b)


# ════════════════════════════════════════════════════════════════════
#  Texto publicado
# ════════════════════════════════════════════════════════════════════

def test_no_se_otorga_sin_una_version_activa_del_texto(conn_boveda, alta_basica):
    """Es lo que hace demostrable al consentimiento: sin el texto publicado,
    «consintió la versión 2026-01» no lo puede verificar nadie."""
    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="6000005"))["id_persona"]
    with pytest.raises(Exception) as fallo:
        _otorgar(conn_boveda, id_persona, "uso_semantico_cuali",
                 "una-version-que-nadie-publico")
    assert "texto" in str(fallo.value).lower()
    conn_boveda.rollback()


def test_una_version_desactivada_tampoco_sirve_para_otorgar(conn_boveda,
                                                            alta_basica, con_textos):
    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="6000006"))["id_persona"]
    db.ejecutar(conn_boveda,
                "update texto_consentimiento set activo = false "
                " where finalidad = 'uso_semantico_cuali' and version = %s",
                (con_textos,))
    with pytest.raises(Exception):
        _otorgar(conn_boveda, id_persona, "uso_semantico_cuali", con_textos)
    conn_boveda.rollback()


def test_el_retiro_no_se_bloquea_aunque_el_texto_ya_no_este_activo(
        conn_boveda, alta_basica, con_textos):
    """La regla vale al insertar y no al actualizar, a propósito: una regla de
    cumplimiento que impida cumplir está mal escrita."""
    id_persona = personas.alta(
        conn_boveda, alta_basica(documento="6000007"))["id_persona"]
    _otorgar(conn_boveda, id_persona, "uso_semantico_cuali", con_textos)
    db.ejecutar(conn_boveda,
                "update texto_consentimiento set activo = false "
                " where finalidad = 'uso_semantico_cuali' and version = %s",
                (con_textos,))

    retirados = db.ejecutar(
        conn_boveda,
        """update consentimiento set estado = 'retirado', retirado_en = now()
            where id_persona = %s and finalidad = 'uso_semantico_cuali'
              and estado = 'vigente'""", (id_persona,))
    assert retirados == 1


def test_la_vista_de_textos_activos_da_la_ultima_de_cada_finalidad(conn_boveda):
    """Es lo que la pantalla de alta consulta para saber qué versión mostrar.
    Si diera la primera, el formulario ofrecería un texto viejo."""
    db.ejecutar(
        conn_boveda,
        """insert into texto_consentimiento (finalidad, version, cuerpo)
           values ('uso_semantico_cuali', 'cuali-v9', 'La más nueva.')""")
    fila = db.una(
        conn_boveda,
        """select version from v_texto_consentimiento_activo
            where finalidad = 'uso_semantico_cuali'""")
    assert fila["version"] == "cuali-v9"
