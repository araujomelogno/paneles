"""R-EU.7 — La migración de encuestas y cargas (boveda/0032), con datos.

La 0032 es el punto de no retorno del despliegue: cambia datos. Esta prueba
arma una bóveda **anterior** a la migración (las 0001 a 0031, en una base
nueva), la siembra como estaba producción —cargas con clientes, temáticas y
personas; una encuesta de panel con respuestas por el flujo viejo; otra sin
respuestas— y comprueba lo que dice la spec:

* toda carga tiene encuesta, con el mismo `ref_estudio` (el cuestionario
  semántico queda vinculado sin tocarlo) y los datos del estudio;
* cada encuesta con respuestas tiene una carga que las representa;
* cada persona de una carga migrada tiene su participación (`respondio`);
* los filtros de temáticas y clientes devuelven las mismas personas;
* la corrida de prueba del script dice lo que va a crear **sin escribir**,
  y la verificación pasa después de aplicar.
"""

import pathlib
import subprocess
import sys
import uuid

import psycopg
import pytest

from panel_api import db, semantica

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

import migrar_encuestas_y_cargas as migrar  # noqa: E402

MIGRACIONES = sorted((RAIZ / "db" / "boveda").glob("*.sql"))
ANTES_DE_LA_0032 = [m for m in MIGRACIONES if m.name < "0032"]
LA_0032 = RAIZ / "db" / "boveda" / "0032_encuestas_y_cargas_unificadas.sql"


def _dsn_de(dsn, base):
    return dsn.rsplit("/", 1)[0] + "/" + base


@pytest.fixture
def boveda_vieja(dsn_boveda):
    """Una bóveda nueva con las migraciones hasta la 0031."""
    base = f"prueba_0032_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(_dsn_de(dsn_boveda, "postgres"), autocommit=True)
    admin.execute(f"create database {base}")
    dsn = _dsn_de(dsn_boveda, base)
    try:
        for archivo in ANTES_DE_LA_0032:
            subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn,
                            "-f", str(archivo)], check=True, capture_output=True)
        yield dsn
    finally:
        admin.execute(f"drop database if exists {base} with (force)")
        admin.close()


def _sembrar(conn, conn_semantica, proveedor):
    """Como estaba producción antes de la 0032."""
    personas = [str(f["id_persona"]) for f in db.todas(conn, """
        insert into persona (nombre, documento)
        select 'Persona ' || g, '1-' || g from generate_series(1, 4) g
        returning id_persona""")]
    panel = db.una(conn, "insert into panel (nombre) values ('Panel') returning id")["id"]
    # Una encuesta de panel con respuestas por el flujo viejo (importación) y
    # otra sin respuestas.
    vieja = db.una(conn, """insert into encuesta (panel_id, nombre, estado)
                            values (%s, 'Ola 1', 'cerrada') returning id, ref_estudio""",
                   (panel,))
    db.una(conn, "insert into encuesta (panel_id, nombre) values (%s, 'Ola 2') returning id",
           (panel,))
    for p in personas[:2]:
        db.ejecutar(conn, """insert into participacion (encuesta_id, id_persona, origen,
                                                        respondio, respondio_en)
                             values (%s, %s, 'importacion', true, now())""",
                    (vieja["id"], p))
    # Una carga con clientes, temáticas y personas.
    carga = db.una(conn, """insert into carga (nombre, fecha_estudio, publico_objetivo)
                            values ('Centel', '2026-06-04', 'Total país')
                            returning id, ref_estudio""")
    for i, p in enumerate(personas[1:]):
        db.ejecutar(conn, "insert into persona_carga (id_persona, carga_id, origen) "
                          "values (%s, %s, %s)",
                    (p, carga["id"], "creada" if i else "reutilizada"))
    cliente = db.una(conn, "insert into cliente (nombre) values ('antel') returning id")["id"]
    db.ejecutar(conn, "insert into carga_cliente (carga_id, cliente_id) values (%s, %s)",
                (carga["id"], cliente))
    tematica = db.una(conn, "insert into tematica (nombre) values ('telefonía') "
                            "returning id")["id"]
    db.ejecutar(conn, """insert into carga_tematica (carga_id, tematica_id, origen)
                         values (%s, %s, 'manual')""", (carga["id"], tematica))
    db.ejecutar(conn, """insert into tematica_pregunta (carga_id, tematica_id, codigo_pregunta)
                         values (%s, %s, 'P1')""", (carga["id"], tematica))
    for p in personas[1:3]:
        db.ejecutar(conn, """insert into persona_tematica (id_persona, tematica_id, carga_id,
                                                           preguntas_respondidas)
                             values (%s, %s, %s, 1)""", (p, tematica, carga["id"]))
    conn.commit()

    # El store semántico: el cuestionario de la carga y el de la encuesta.
    for ref, quienes in ((carga["ref_estudio"], personas[1:]),
                         (vieja["ref_estudio"], personas[:2])):
        cuestionario = semantica.asegurar_cuestionario(conn_semantica, str(ref), "Estudio")
        pregunta = semantica.upsert_preguntas(conn_semantica, cuestionario, [
            {"codigo": "P1", "texto": "¿Qué compañía usás?", "tipo": "abierta",
             "orden": 1}])["P1"]
        individuos = semantica.asegurar_individuos(conn_semantica, quienes)
        semantica.escribir_respuestas(conn_semantica, [
            {"individuo_id": individuos[p], "pregunta_id": pregunta,
             "valor_texto": "Antel", "texto_embebido": "¿Qué compañía usás? → Antel"}
            for p in quienes], proveedor)
    conn_semantica.commit()
    return {"personas": personas, "carga": carga, "vieja": vieja,
            "cliente": cliente, "tematica": tematica}


def test_la_corrida_de_prueba_dice_que_va_a_crear_y_no_escribe(
        boveda_vieja, conn_semantica, proveedor, tmp_path, capsys):
    with psycopg.connect(boveda_vieja, row_factory=psycopg.rows.dict_row) as conn:
        datos = _sembrar(conn, conn_semantica, proveedor)
        foto = tmp_path / "antes.json"
        assert migrar.prueba(conn, conn_semantica, guardar=str(foto)) == 0
        salida = capsys.readouterr().out
        assert "encuestas a crear ............ 1" in salida
        assert "cargas a crear ............... 1" in salida
        assert "Los números cierran." in salida
        # No escribió nada: la carga sigue sin encuesta (no hay ni columna).
        assert not migrar._aplicada(conn)
        assert foto.exists()
        del datos


def test_despues_de_migrar_todo_cierra(boveda_vieja, conn_semantica, proveedor,
                                       tmp_path, capsys):
    with psycopg.connect(boveda_vieja, row_factory=psycopg.rows.dict_row) as conn:
        datos = _sembrar(conn, conn_semantica, proveedor)
        foto = tmp_path / "antes.json"
        migrar.prueba(conn, conn_semantica, guardar=str(foto))
        subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", boveda_vieja,
                        "-f", str(LA_0032)], check=True, capture_output=True)

        carga = db.una(conn, "select * from carga where id = %s", (datos["carga"]["id"],))
        encuesta = db.una(conn, "select * from encuesta where id = %s",
                          (carga["encuesta_id"],))
        # La encuesta de la carga: independiente, cerrada, mismo ref_estudio,
        # con los datos del estudio, sus clientes y sus temáticas.
        assert encuesta["tipo"] == "independiente" and encuesta["panel_id"] is None
        assert encuesta["estado"] == "cerrada"
        assert encuesta["ref_estudio"] == datos["carga"]["ref_estudio"]
        assert str(encuesta["fecha_campo"]) == "2026-06-04"
        assert encuesta["publico_objetivo"] == "Total país"
        assert db.una(conn, "select cliente_id from encuesta_cliente where encuesta_id = %s",
                      (encuesta["id"],))["cliente_id"] == datos["cliente"]
        assert db.una(conn, "select tematica_id from encuesta_tematica "
                            "where encuesta_id = %s",
                      (encuesta["id"],))["tematica_id"] == datos["tematica"]
        # Cada persona de la carga, con su participación.
        participaron = {str(f["id_persona"]) for f in db.todas(
            conn, "select id_persona from participacion where encuesta_id = %s "
                  "and respondio and not convocado", (encuesta["id"],))}
        assert participaron == set(datos["personas"][1:])

        # La encuesta vieja con respuestas tiene una carga que las representa;
        # la que no tenía respuestas, no.
        de_la_vieja = db.todas(conn, "select * from carga where encuesta_id = %s",
                               (datos["vieja"]["id"],))
        assert len(de_la_vieja) == 1
        assert de_la_vieja[0]["ref_estudio"] == datos["vieja"]["ref_estudio"]
        vinculados = {str(f["id_persona"]) for f in db.todas(
            conn, "select id_persona, origen from persona_carga where carga_id = %s",
            (de_la_vieja[0]["id"],))}
        assert vinculados == set(datos["personas"][:2])
        assert db.una(conn, "select count(*)::int as n from carga c join encuesta e "
                            "on e.id = c.encuesta_id where e.nombre = 'Ola 2'")["n"] == 0
        # Lo de la convocatoria: lo que vino de un archivo no fue convocado.
        assert not db.una(conn, "select bool_or(convocado) as c from participacion "
                                "where encuesta_id = %s", (datos["vieja"]["id"],))["c"]

        conn.commit()
        assert migrar.asignar_cargas(conn, conn_semantica, aplicar=True) == 0
        capsys.readouterr()
        assert migrar.verificar(conn, conn_semantica, antes=str(foto)) == 0
        assert "✓" in capsys.readouterr().out
        # Y en el semántico, cada respuesta con su carga.
        assert db.una(conn_semantica, "select count(*)::int as n from respuesta "
                                      "where carga_id is null")["n"] == 0


def test_no_corre_con_una_ingesta_en_curso(boveda_vieja):
    with psycopg.connect(boveda_vieja, row_factory=psycopg.rows.dict_row) as conn:
        carga = db.una(conn, "insert into carga (nombre) values ('X') returning id")
        db.ejecutar(conn, """insert into ingesta_trabajo
                                 (destino_tipo, destino_id, plan, estado, lotes_total, filas_total)
                             values ('carga', %s, '{}'::jsonb, 'procesando', 1, 1)""",
                    (carga["id"],))
        conn.commit()
    resultado = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", boveda_vieja, "-f", str(LA_0032)],
        capture_output=True, text=True)
    assert resultado.returncode != 0
    assert "Hay ingestas en curso" in resultado.stderr


def test_la_verificacion_falla_si_un_filtro_cambia(boveda_vieja, conn_semantica,
                                                   proveedor, tmp_path, capsys):
    """El punto de control 3 tiene que poder fallar: si después de migrar
    alguien queda con una temática que no tenía, lo dice."""
    with psycopg.connect(boveda_vieja, row_factory=psycopg.rows.dict_row) as conn:
        datos = _sembrar(conn, conn_semantica, proveedor)
        foto = tmp_path / "antes.json"
        migrar.prueba(conn, conn_semantica, guardar=str(foto))
        subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", boveda_vieja,
                        "-f", str(LA_0032)], check=True, capture_output=True)
        encuesta = db.una(conn, "select encuesta_id from carga where id = %s",
                          (datos["carga"]["id"],))["encuesta_id"]
        db.ejecutar(conn, """insert into persona_tematica (id_persona, tematica_id,
                                 encuesta_id, preguntas_respondidas)
                             values (%s, %s, %s, 1)""",
                    (datos["personas"][3], datos["tematica"], encuesta))
        conn.commit()
        migrar.asignar_cargas(conn, conn_semantica, aplicar=True)
        capsys.readouterr()
        assert migrar.verificar(conn, conn_semantica, antes=str(foto)) == 1
        assert "FALLÓ" in capsys.readouterr().out
