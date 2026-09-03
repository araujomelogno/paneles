"""R1.6 — Guardrail de PII: nada identificatorio llega al store remoto.

Es la prueba del DoD «auditoría de esquema: 0 columnas de PII en el store
remoto», más el control en tiempo de ejecución.
"""

import pathlib

import pytest

from panel_api import db, encuestas, paneles, personas, pii, remoto
from panel_api.errores import FugaDePII

from conftest import consentimientos

RAIZ = pathlib.Path(__file__).resolve().parents[2]
DDL_REMOTO = (RAIZ / "db" / "remoto" / "0001_init.sql").read_text(encoding="utf-8")
DDL_LOCAL = (RAIZ / "db" / "local" / "0001_init.sql").read_text(encoding="utf-8")


# ── Auditoría estática del DDL ───────────────────────────────────────

def test_el_ddl_del_store_remoto_no_declara_ninguna_columna_de_pii():
    assert pii.auditar_esquema(DDL_REMOTO) == []


def test_el_auditor_si_detecta_pii_cuando_la_hay():
    # Control positivo: sobre la bóveda local (que SÍ tiene PII) el auditor
    # encuentra las columnas. Sin esto, un auditor roto pasaría inadvertido.
    hallazgos = pii.auditar_esquema(DDL_LOCAL)
    columnas_de_persona = {c for tabla, c in hallazgos if tabla == "persona"}
    assert {"documento", "email", "celular", "fecha_nacimiento", "nombre",
            "observaciones"} <= columnas_de_persona


def test_todas_las_migraciones_remotas_estan_limpias():
    for archivo in sorted((RAIZ / "db" / "remoto").glob("*.sql")):
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


def test_el_cliente_remoto_rechaza_escribir_un_cuestionario_con_pii(remota):
    with pytest.raises(FugaDePII):
        remoto.asegurar_cuestionario(
            remota, "3f5b8a1e-0000-4000-8000-000000000001", "Ola 1",
            metadata={"email_contacto": "ana@ej.uy", "celular": "099"},
        )
    assert db.una(remota, "select count(*)::int as n from cuestionario")["n"] == 0


# ── Auditoría en vivo del store remoto ───────────────────────────────

def test_el_esquema_remoto_desplegado_no_tiene_columnas_de_pii(remota):
    assert remoto.auditar_columnas(remota) == []


def test_la_auditoria_en_vivo_detecta_una_columna_de_pii_agregada_a_mano(remota):
    # Simula el peor caso: alguien agrega una columna de PII directo en la
    # base. La auditoría tiene que verlo.
    with remota.cursor() as cur:
        cur.execute("alter table individuo add column email text")
    try:
        assert {"tabla": "individuo", "columna": "email"} in remoto.auditar_columnas(remota)
    finally:
        with remota.cursor() as cur:
            cur.execute("alter table individuo drop column email")


# ── El recorrido completo: después de ingestar, el remoto sigue limpio ──

def test_despues_de_una_ingesta_real_el_remoto_no_tiene_pii(local, remota, proveedor):
    panel = paneles.crear(local, "Panel")
    id_persona = personas.alta(
        local,
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
    paneles.agregar_miembro(local, panel["id"], id_persona)
    encuesta = encuestas.crear(local, panel["id"], "Ola 1")
    encuestas.convocar(local, encuesta["id"], todo_el_panel=True)
    encuestas.ingestar(
        local,
        remota,
        encuesta["id"],
        [{"codigo": "P1", "texto": "¿Qué bebida consume?", "tipo": "abierta"}],
        [{"id_en_origen": "R-001", "P1": "Fernet"}],
        proveedor=proveedor,
    )

    # El esquema sigue limpio…
    assert remoto.auditar_columnas(remota) == []

    # …y ningún valor de PII quedó escrito en las columnas de texto libres
    # del remoto (que es por donde se filtraría si se colara).
    valores = db.todas(
        remota,
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

    # Lo único que identifica a la persona del lado remoto es el token opaco.
    assert valores[0]["id_persona"] == id_persona
