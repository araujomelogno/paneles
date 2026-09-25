"""R5.5 — la regla dura #1 hecha valer por la base, y no solo por Python.

Tres niveles, y cada prueba de acá cubre uno:

  1 · el event trigger, que rechaza el DDL en el momento;
  2 · el gate de despliegue, que lee los archivos de migración antes de que
      nadie los aplique;
  3 · el espejo entre el catálogo de la base y `pii.CAMPOS_PII`.
"""

import pathlib

import pytest

from panel_api import pii

RAIZ = pathlib.Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════════════
#  Nivel 1 · El event trigger
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("ddl", [
    "alter table respuesta add column email text",
    "alter table individuo add column documento text",
    "alter table pregunta add column celular text",
    "create table filtracion (id_persona uuid, telefono text)",
    "alter table respuesta rename column valor_texto to observaciones",
])
def test_la_base_rechaza_una_columna_de_pii(conn_semantica, ddl):
    with pytest.raises(Exception) as fallo:
        with conn_semantica.cursor() as cur:
            cur.execute(ddl)
    assert "Regla dura #1" in str(fallo.value)
    conn_semantica.rollback()


def test_el_rechazo_no_deja_la_columna(conn_semantica):
    """El trigger corre en `ddl_command_end`, o sea **después** de que la
    columna se agregó. Lo que la saca es que la transacción aborte, así que
    conviene comprobarlo y no darlo por hecho."""
    with pytest.raises(Exception):
        with conn_semantica.cursor() as cur:
            cur.execute("alter table respuesta add column email text")
    conn_semantica.rollback()
    with conn_semantica.cursor() as cur:
        cur.execute("""select count(*) as n from information_schema.columns
                        where table_name = 'respuesta' and column_name = 'email'""")
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("relacion", ["cuestionario", "pregunta", "serie"])
def test_nombre_sigue_siendo_legitimo_donde_lo_era(conn_semantica, relacion):
    """`nombre` es el del estudio, de la pregunta o de la medición: metadato,
    no persona. Si el guardia lo rechazara, habría que sacarlo, no vivir con
    él."""
    with conn_semantica.cursor() as cur:
        cur.execute(f"alter table {relacion} add column nombre_de_prueba text")
        cur.execute(f"alter table {relacion} drop column nombre_de_prueba")
        # Y la columna `nombre` de verdad sigue ahí, sin que el guardia se
        # queje al tocar la tabla por cualquier otro motivo.
        cur.execute(f"alter table {relacion} add column marca_de_prueba int")
        cur.execute(f"alter table {relacion} drop column marca_de_prueba")
    conn_semantica.rollback()


def test_una_excepcion_declarada_habilita_el_nombre(conn_semantica):
    """La salida de emergencia existe, y es una fila en `excepcion_pii` con su
    motivo escrito: no un `disable` del guardia."""
    with conn_semantica.cursor() as cur:
        cur.execute("create table nota_de_campo (id int)")
        with pytest.raises(Exception):
            cur.execute("alter table nota_de_campo add column contacto text")
    conn_semantica.rollback()

    with conn_semantica.cursor() as cur:
        cur.execute("create table nota_de_campo (id int)")
        cur.execute("""insert into excepcion_pii (relacion, campo, motivo)
                       values ('nota_de_campo', 'contacto',
                               'a quién contactar del cliente, no del panelista')""")
        cur.execute("alter table nota_de_campo add column contacto text")
    conn_semantica.rollback()


# ════════════════════════════════════════════════════════════════════
#  Nivel 2 · El gate de despliegue, sobre los archivos
# ════════════════════════════════════════════════════════════════════

def test_las_migraciones_del_store_semantico_estan_limpias():
    for archivo in sorted((RAIZ / "db" / "semantica").glob("*.sql")):
        assert pii.auditar_esquema(archivo.read_text(encoding="utf-8")) == [], \
            archivo.name


@pytest.mark.parametrize("ddl,esperado", [
    ("alter table respuesta add column email text;", ("respuesta", "email")),
    ("alter table respuesta add email text;", ("respuesta", "email")),
    ("alter table if exists individuo add column if not exists celular text;",
     ("individuo", "celular")),
    ("alter table respuesta rename column valor_texto to documento;",
     ("respuesta", "documento")),
    ("alter table respuesta rename valor_texto to documento;",
     ("respuesta", "documento")),
])
def test_el_gate_atrapa_la_migracion_antes_de_aplicarla(ddl, esperado):
    """Es la que llega a un pull request: el `create table` original está
    limpio y la PII entra por una migración posterior. El event trigger no la
    ve hasta que alguien la aplica, y para entonces ya se revisó el PR."""
    assert esperado in pii.auditar_esquema(ddl)


def test_el_gate_no_se_denuncia_a_si_mismo():
    """La 0005 explica en prosa por qué `email` no va. Un chequeo que leyera
    los comentarios se marcaría a sí mismo y habría que apagarlo, que es la
    peor manera de perder un control."""
    texto = "-- ojo: nunca agregar email acá\nalter table respuesta add column x int;"
    assert pii.auditar_esquema(texto) == []


def test_nombre_en_cuestionario_no_se_reporta():
    assert pii.auditar_esquema(
        "alter table cuestionario add column nombre text;") == []
    assert pii.auditar_esquema(
        "alter table respuesta add column nombre text;") == [("respuesta", "nombre")]


# ════════════════════════════════════════════════════════════════════
#  Nivel 3 · El espejo
# ════════════════════════════════════════════════════════════════════

def test_el_catalogo_de_la_base_y_el_de_python_no_divergen(conn_semantica):
    """Dos listas que dicen lo mismo se separan sola­s. Esta prueba es lo que
    convierte «hay que acordarse de tocar las dos» en «no compila si no»."""
    campos, excepciones = pii.catalogo_en_base(conn_semantica)
    assert campos == pii.CAMPOS_PII, (
        "el catálogo de `campo_pii` y `pii.CAMPOS_PII` se separaron: "
        f"solo en la base {sorted(campos - pii.CAMPOS_PII)}, "
        f"solo en Python {sorted(pii.CAMPOS_PII - campos)}")
    assert excepciones == pii.CONTEXTOS_QUE_PERMITEN_NOMBRE
