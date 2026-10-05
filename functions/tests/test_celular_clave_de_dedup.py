"""El celular como clave de dedup (D64, `boveda/0022`).

Orden de `dedup.resolver`: documento → correo → **celular** → nombre + fecha
de nacimiento. El celular es más débil que el correo —puede ser de un hogar,
o de otra persona que lo usa— y por eso tiene tres cautelas, cada una con su
prueba acá:

* reutiliza solo si hay **una** titular;
* no reutiliza si un documento o un correo dicen que es otra persona;
* no reutiliza si el nombre del alta no es el de la titular.

Y lo que lo hace usable: compara en E.164, así que el mismo número escrito
de dos formas es el mismo número.
"""

import pytest

from panel_api import db, dedup, personas, resumen_ingesta, sav
from panel_api.errores import DatosInvalidos

from conftest import consentimientos
from test_fase7_revision import _plan


def _alta(conn, **persona):
    return personas.alta(
        conn,
        {"persona": persona, "consentimientos": consentimientos("contacto_participacion")},
    )


def _personas(conn):
    return db.una(conn, "select count(*)::int as n from persona")["n"]


# ════════════════════════════════════════════════════════════════════
#  dedup.resolver
# ════════════════════════════════════════════════════════════════════

def test_el_mismo_celular_reutiliza_a_la_persona(conn_boveda):
    primera = _alta(conn_boveda, nombre="Ana Pérez", celular="099 123 456")
    segunda = _alta(conn_boveda, nombre="Ana Pérez", celular="+59899123456")

    assert segunda["estado"] == "reutilizada"
    assert segunda["motivo_dedup"] == "celular"
    assert segunda["id_persona"] == primera["id_persona"]
    assert _personas(conn_boveda) == 1


def test_el_celular_se_compara_escrito_de_cualquier_forma(conn_boveda):
    """Local, con el código de país sin `+`, con `00`: es el mismo número."""
    primera = _alta(conn_boveda, nombre="Ana Pérez", celular="099123456")
    for forma in ("59899123456", "0059899123456", "099-123-456"):
        r = dedup.resolver(conn_boveda, {"nombre": "Ana Pérez", "celular": forma})
        assert r.accion == dedup.REUTILIZA, forma
        assert str(r.id_persona) == primera["id_persona"]


def test_el_celular_va_despues_del_documento_y_del_correo(conn_boveda):
    """Si el documento ya decide, el celular no se mira."""
    por_documento = _alta(conn_boveda, documento="4.123.456-7", nombre="Ana Pérez")
    _alta(conn_boveda, nombre="Ana Pérez", email="otra@ejemplo.invalid",
          celular="099123456")
    r = dedup.resolver(conn_boveda, {"documento": "4.123.456-7",
                                     "nombre": "Ana Pérez", "celular": "099123456"})
    assert r.motivo == "documento"
    assert str(r.id_persona) == por_documento["id_persona"]


def test_un_celular_que_comparten_varias_va_a_revision(conn_boveda):
    """Fusionar con cualquiera de las dos sería elegir al azar."""
    una = _alta(conn_boveda, nombre="Marta Silva", email="marta@ejemplo.invalid",
                celular="099123456")
    otra = _alta(conn_boveda, nombre="Marta Silva", email="msilva@ejemplo.invalid",
                 celular="099123456")
    assert otra["estado"] == "creada"   # el correo la distinguía

    tercera = _alta(conn_boveda, nombre="Marta Silva", celular="099123456")
    assert tercera["estado"] == "revision"
    assert tercera["motivo"] == "celular_compartido"
    assert {c["id_persona"] for c in tercera["candidatos"]} == \
        {una["id_persona"], otra["id_persona"]}
    assert _personas(conn_boveda) == 2


def test_un_documento_nuevo_completa_a_la_titular_sin_documento(conn_boveda):
    """La titular no tenía documento y el alta trae uno, con el mismo nombre:
    es la misma persona, que ahora se identifica mejor. Se reutiliza."""
    primera = _alta(conn_boveda, nombre="Marta Silva", celular="099123456")
    segunda = _alta(conn_boveda, nombre="Marta Silva", documento="1.111.111-1",
                    celular="099123456")
    assert segunda["estado"] == "reutilizada"
    assert segunda["id_persona"] == primera["id_persona"]


def test_un_documento_distinto_gana_sobre_el_celular(conn_boveda):
    """Dos personas que comparten el número: la evidencia fuerte decide, y no
    se fusiona ni se manda a revisión."""
    hijo = _alta(conn_boveda, nombre="Pablo Ruiz", documento="1.111.111-1",
                 celular="099123456")
    padre = _alta(conn_boveda, nombre="Pablo Ruiz", documento="2.222.222-2",
                  celular="099123456")
    assert padre["estado"] == "creada"
    assert padre["id_persona"] != hijo["id_persona"]


def test_un_correo_distinto_gana_sobre_el_celular(conn_boveda):
    una = _alta(conn_boveda, nombre="Ana Pérez", email="ana@ejemplo.invalid",
                celular="099123456")
    otra = _alta(conn_boveda, nombre="Ana Pérez", email="ines@ejemplo.invalid",
                 celular="099123456")
    assert otra["estado"] == "creada"
    assert otra["id_persona"] != una["id_persona"]


def test_otro_nombre_con_el_mismo_celular_va_a_revision(conn_boveda):
    """El padre que se anota con el celular del hijo. Un nombre no alcanza
    para fusionar, pero sí para dudar."""
    hijo = _alta(conn_boveda, nombre="Pablo Ruiz", documento="1.111.111-1",
                 celular="099123456")
    padre = _alta(conn_boveda, nombre="Ernesto Ruiz", celular="099123456")
    assert padre["estado"] == "revision"
    assert padre["motivo"] == "celular_otro_nombre"
    assert [c["id_persona"] for c in padre["candidatos"]] == [hijo["id_persona"]]
    assert _personas(conn_boveda) == 1


@pytest.mark.parametrize("del_alta", ["ana perez", "Ana Pérez Silva", "ANA PÉREZ"])
def test_el_mismo_nombre_escrito_distinto_no_frena(conn_boveda, del_alta):
    """Tildes, mayúsculas y un apellido de más son la misma persona escrita
    en dos archivos."""
    primera = _alta(conn_boveda, nombre="Ana Pérez", celular="099123456")
    r = dedup.resolver(conn_boveda, {"nombre": del_alta, "celular": "099123456"})
    assert r.accion == dedup.REUTILIZA
    assert str(r.id_persona) == primera["id_persona"]


def test_un_celular_que_no_se_puede_normalizar_no_se_compara(conn_boveda):
    """«llamar a la oficina» no es un número de nadie: no reutiliza, crea."""
    _alta(conn_boveda, nombre="Ana Pérez", celular="llamar a la oficina")
    r = dedup.resolver(conn_boveda, {"nombre": "Ana Pérez",
                                     "celular": "llamar a la oficina"})
    assert r.accion == dedup.CREA


def test_sin_titular_el_celular_no_decide_y_sigue_el_dedup(conn_boveda):
    """Un celular que nadie tiene no impide que nombre + fecha de nacimiento
    mande a revisión: el paso 4 sigue corriendo."""
    _alta(conn_boveda, nombre="Juan López", fecha_nacimiento="1985-03-12")
    r = dedup.resolver(conn_boveda, {"nombre": "Juan López",
                                     "fecha_nacimiento": "1985-03-12",
                                     "celular": "099777888"})
    assert r.accion == dedup.REVISION
    assert r.motivo == "nombre_fecha_nacimiento"


def test_una_alta_en_revision_por_celular_guarda_la_persona_entera(conn_boveda):
    """La revisión se resuelve después con lo que llegó: tiene que estar."""
    _alta(conn_boveda, nombre="Pablo Ruiz", celular="099123456")
    r = _alta(conn_boveda, nombre="Ernesto Ruiz", celular="099123456",
              localidad="Salto")
    fila = db.una(conn_boveda, "select datos, motivo from alta_en_revision where id = %s",
                  (r["revision_id"],))
    assert fila["motivo"] == "celular_otro_nombre"
    assert fila["datos"]["persona"]["nombre"] == "Ernesto Ruiz"
    assert fila["datos"]["persona"]["celular"] == "+59899123456"


def test_hay_un_indice_para_buscar_por_celular(conn_boveda):
    """El alta por archivo resuelve el dedup fila por fila dentro de la
    request: sin índice, cada fila recorrería `persona` entera. Y no es
    único: un celular puede ser compartido."""
    fila = db.una(conn_boveda, """
        select indexdef from pg_indexes
         where tablename = 'persona' and indexname = 'persona_celular_idx'""")
    assert fila, "falta la 0022"
    assert "UNIQUE" not in fila["indexdef"].upper()


# ════════════════════════════════════════════════════════════════════
#  La revisión de la importación
# ════════════════════════════════════════════════════════════════════

def test_un_celular_marcado_cuenta_como_clave_en_la_revision():
    filas = [{"ID": f"r{i}", "P1": "Fernet",
              "CEL": f"099 {100000 + i}" if i < 30 else ""} for i in range(40)]
    r = resumen_ingesta.resumir(
        _plan(demograficas={"CEL": {"campo": "celular", "mapeo": {}}}), filas)
    assert "sin_clave_de_dedup" not in {a["tipo"] for a in r["advertencias"]}
    celular = next(c for c in r["dedup"] if c["clave"] == "celular")
    assert celular["cobertura"] == "30 de 40 filas"
    assert celular["valores_distintos"] == 30


def test_la_revision_cuenta_el_mismo_numero_escrito_distinto_como_uno():
    """El resumen compara como el dedup: en E.164."""
    filas = [{"ID": "r1", "CEL": "099 123 456"}, {"ID": "r2", "CEL": "+59899123456"},
             {"ID": "r3", "CEL": "099654321"}]
    r = resumen_ingesta.resumir(
        _plan(preguntas=[], demograficas={"CEL": {"campo": "celular", "mapeo": {}}}),
        filas)
    celular = next(c for c in r["dedup"] if c["clave"] == "celular")
    assert celular["valores_distintos"] == 2
    assert celular["filas_que_colisionan"] == 2
    assert r["volumen"]["personas_estimadas"] == 2


def test_un_celular_que_no_se_puede_normalizar_no_es_clave_en_la_revision():
    filas = [{"ID": f"r{i}", "CEL": "sin dato"} for i in range(12)]
    r = resumen_ingesta.resumir(
        _plan(preguntas=[], demograficas={"CEL": {"campo": "celular", "mapeo": {}}}),
        filas)
    assert r["sin_clave_de_dedup"] is True


# ════════════════════════════════════════════════════════════════════
#  El celular de relleno se frena, como el documento implausible
# ════════════════════════════════════════════════════════════════════

def test_un_celular_de_relleno_en_todo_el_archivo_se_frena():
    filas = [{"ID": f"r{i}", "CEL": "099 000 000"} for i in range(40)]
    with pytest.raises(DatosInvalidos) as error:
        sav._controlar_celular_plausible(filas, {"celular": "CEL"})
    assert "fusionaría" in error.value.mensaje
    assert resumen_ingesta.VALIDACIONES["celular_implausible"][0] == \
        resumen_ingesta.BLOQUEA


def test_celulares_de_verdad_pasan():
    filas = [{"ID": f"r{i}", "CEL": f"099{100000 + i}"} for i in range(40)]
    sav._controlar_celular_plausible(filas, {"celular": "CEL"})


def test_un_archivo_chico_o_sin_celulares_normalizables_no_se_frena():
    sav._controlar_celular_plausible(
        [{"CEL": "099000000"} for _ in range(5)], {"celular": "CEL"})
    sav._controlar_celular_plausible(
        [{"CEL": "no tiene"} for _ in range(40)], {"celular": "CEL"})


# ════════════════════════════════════════════════════════════════════
#  Por la ruta de importación
# ════════════════════════════════════════════════════════════════════

def test_una_importacion_con_celular_como_unica_clave(ctx, actor, conn_boveda, tmp_path):
    """Sin documento ni correo: la revisión no advierte falta de clave, y dos
    filas con el mismo celular escrito distinto quedan en una persona."""
    import base64

    from panel_api import encuestas, paneles, ruteo

    from test_fase7_revision import VERSION

    pyreadstat = pytest.importorskip("pyreadstat")
    pandas = pytest.importorskip("pandas")
    ruta = tmp_path / "con_celular.sav"
    pyreadstat.write_sav(pandas.DataFrame({
        "ID": [f"r{i}" for i in range(12)],
        "NOM": ["Ana Pérez", "ana perez"] + [f"Persona {i}" for i in range(2, 12)],
        "CEL": ["099 123 456", "+59899123456"] + [f"099{200000 + i}" for i in range(2, 12)],
        "CONS": [1.0] * 12,
        "P1": [1.0] * 12,
    }), str(ruta), variable_value_labels={"P1": {1.0: "Fernet"}})

    panel = paneles.crear(conn_boveda, "Panel")
    enc = encuestas.crear(conn_boveda, panel["id"], "Ola")
    cuerpo = {
        "archivo_base64": base64.b64encode(ruta.read_bytes()).decode(),
        "columna_id": "ID", "origen": "sav", "panel_id": panel["id"],
        "modo": "crear_individuos", "tipo_identificador": "alias",
        "preguntas": [{"codigo": "P1", "texto": "¿Qué bebida?", "tipo": "cerrada",
                       "opciones": {"1": "Fernet"}, "orden": 1}],
        "demograficas": {"NOM": "nombre", "CEL": "celular"},
        "evidencia_consentimiento": {
            f: {"variable": "CONS", "valor_afirmativo": "1", "version_texto": VERSION}
            for f in ("contacto_participacion", "uso_semantico")},
    }
    _, revision = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta", dict(cuerpo, solo_revisar=True),
        {}, actor("operaciones"), ctx)
    assert revision["sin_clave_de_dedup"] is False
    assert revision["volumen"]["personas_estimadas"] == 11

    status, r = ruteo.despachar(
        "POST", f"/encuestas/{enc['id']}/sav/ingesta", cuerpo, {},
        actor("operaciones"), ctx)
    assert status == 202
    assert r["sin_clave_de_dedup"] is False
    resumen = r["creacion_de_individuos"]["resumen"]
    assert resumen["creados"] == 11
    assert resumen["reutilizados"] == 1
    assert _personas(conn_boveda) == 11
