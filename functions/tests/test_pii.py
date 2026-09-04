"""R1.6 — Guardrail de PII: nada identificatorio llega al store semántico.

Es la prueba del DoD «auditoría de esquema: 0 columnas de PII en el store
store semántico», más el control en tiempo de ejecución.
"""

import pathlib

import pytest

from panel_api import db, encuestas, paneles, personas, pii, semantica
from panel_api.errores import FugaDePII

from conftest import consentimientos

RAIZ = pathlib.Path(__file__).resolve().parents[2]
DDL_SEMANTICA = (RAIZ / "db" / "semantica" / "0001_init.sql").read_text(encoding="utf-8")
DDL_BOVEDA = (RAIZ / "db" / "boveda" / "0001_init.sql").read_text(encoding="utf-8")


# ── Auditoría estática del DDL ───────────────────────────────────────

def test_el_ddl_del_store_semantica_no_declara_ninguna_columna_de_pii():
    assert pii.auditar_esquema(DDL_SEMANTICA) == []


def test_el_auditor_si_detecta_pii_cuando_la_hay():
    # Control positivo: sobre la conn_boveda conn_boveda (que SÍ tiene PII) el auditor
    # encuentra las columnas. Sin esto, un auditor roto pasaría inadvertido.
    hallazgos = pii.auditar_esquema(DDL_BOVEDA)
    columnas_de_persona = {c for tabla, c in hallazgos if tabla == "persona"}
    assert {"documento", "email", "celular", "fecha_nacimiento", "nombre",
            "observaciones"} <= columnas_de_persona


def test_todas_las_migraciones_remotas_estan_limpias():
    for archivo in sorted((RAIZ / "db" / "semantica").glob("*.sql")):
        assert pii.auditar_esquema(archivo.read_text(encoding="utf-8")) == [], archivo.name


# ── Guardrail en tiempo de ejecución ─────────────────────────────────

@pytest.mark.parametrize(
    "payload",
    [
        {"id_persona": "abc", "email": "ana@ej.uy"},
        {"id_persona": "abc", "documento": "4.123.456-7"},
        [{"codigo": "P1", "texto": "¿Nombre?", "celular": "099"}],
        {"metadata": {"contacto": {"telefono": "2900"}}},
        {"individuos": [{"id_persona": "abc", "fecha_nacimiento": "1990-01-01"}]},
    ],
)
def test_validar_sin_pii_aborta_ante_cualquier_campo_identificatorio(payload):
    with pytest.raises(FugaDePII):
        pii.validar_sin_pii(payload)


def test_validar_sin_pii_deja_pasar_lo_que_si_puede_viajar():
    pii.validar_sin_pii(
        {
            "id_persona": "3f5b…",
            "ref_estudio": "9a1c…",
            "texto_embebido": "¿Qué bebida consume? → Fernet",
            "valor_texto": "Fernet",
        }
    )


def test_nombre_es_legitimo_como_metadato_de_estudio_pero_no_de_persona():
    pii.validar_sin_pii({"nombre": "Ola 1 bebidas"}, contexto="cuestionario")
    with pytest.raises(FugaDePII):
        pii.validar_sin_pii({"nombre": "Ana Pérez"}, contexto="individuo")


def test_el_cliente_semantica_rechaza_escribir_un_cuestionario_con_pii(conn_semantica):
    with pytest.raises(FugaDePII):
        semantica.asegurar_cuestionario(
            conn_semantica, "3f5b8a1e-0000-4000-8000-000000000001", "Ola 1",
            metadata={"email_contacto": "ana@ej.uy", "celular": "099"},
        )
    assert db.una(conn_semantica, "select count(*)::int as n from cuestionario")["n"] == 0


# ── Auditoría en vivo del store semántico ───────────────────────────────

def test_el_esquema_semantica_desplegado_no_tiene_columnas_de_pii(conn_semantica):
    assert semantica.auditar_columnas(conn_semantica) == []


def test_la_auditoria_en_vivo_detecta_una_columna_de_pii_agregada_a_mano(conn_semantica):
    # Simula el peor caso: alguien agrega una columna de PII directo en la
    # base. La auditoría tiene que verlo.
    with conn_semantica.cursor() as cur:
        cur.execute("alter table individuo add column email text")
    try:
        assert {"tabla": "individuo", "columna": "email"} in semantica.auditar_columnas(conn_semantica)
    finally:
        with conn_semantica.cursor() as cur:
            cur.execute("alter table individuo drop column email")


# ── El recorrido completo: después de ingestar, el store semántico sigue limpio ──

def test_despues_de_una_ingesta_real_el_semantica_no_tiene_pii(conn_boveda, conn_semantica, proveedor):
    panel = paneles.crear(conn_boveda, "Panel")
    id_persona = personas.alta(
        conn_boveda,
        {
            "persona": {
                "documento": "4.123.456-7",
                "nombre": "Ana Pérez",
                "email": "ana@ej.uy",
                "celular": "099111222",
                "fecha_nacimiento": "1990-05-20",
                "observaciones": "dato sensible que no puede salir",
            },
            "consentimientos": consentimientos("contacto_participacion", "uso_semantico"),
            "origen": "dooblo",
            "id_en_origen": "R-001",
        },
    )["id_persona"]
    paneles.agregar_miembro(conn_boveda, panel["id"], id_persona)
    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)
    encuestas.ingestar(
        conn_boveda,
        conn_semantica,
        encuesta["id"],
        [{"codigo": "P1", "texto": "¿Qué bebida consume?", "tipo": "abierta"}],
        [{"id_en_origen": "R-001", "P1": "Fernet"}],
        proveedor=proveedor,
    )

    # El esquema sigue limpio…
    assert semantica.auditar_columnas(conn_semantica) == []

    # …y ningún valor de PII quedó escrito en las columnas de texto libres
    # del store semántico (que es por donde se filtraría si se colara).
    valores = db.todas(
        conn_semantica,
        """
        select c.nombre as cuestionario, p.texto as pregunta,
               r.valor_texto, r.texto_embebido, i.id_persona::text as id_persona
          from respuesta r
          join individuo i on i.id = r.individuo_id
          join pregunta p on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
        """,
    )
    todo_el_texto = " ".join(str(v) for fila in valores for v in fila.values()).lower()
    for dato_pii in ("ana pérez", "ana@ej.uy", "099111222", "4.123.456-7",
                     "1990-05-20", "dato sensible"):
        assert dato_pii not in todo_el_texto

    # Lo único que identifica a la persona del lado semántico es el token opaco.
    assert valores[0]["id_persona"] == id_persona
