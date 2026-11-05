"""PEDIDO — Encuestas sin convocatoria, y personas con identificadores vacíos
(`docs/PEDIDO_encuestas_sin_convocatoria.md`, D82).

Dos problemas de la misma entrega:

* Una encuesta que llegó como archivo de respuestas (o migrada desde una
  carga por la `0032`) se mostraba con una «Convocatoria» de 910 personas y
  una fecha de convocatoria cada una, cuando nadie las convocó. El dato
  (`convocado = false`) estaba bien; la presentación no. Ahora cada encuesta
  dice de dónde viene (`origen`, `boveda/0033`) y nadie no convocado tiene
  fecha de convocatoria.
* Una columna de celular sucia hizo que «...........» se aceptara como id de
  la plataforma y se creara una persona llamada «......». Ahora un valor sin
  ninguna letra ni dígito no identifica a nadie, y los que ya entraron se
  pueden listar.

Corre contra Postgres de verdad. Lo que se ve en la pantalla lo prueba
`test_pantallas_encuestas_sin_convocatoria.py`.
"""

import subprocess

import psycopg
import pytest

from panel_api import (
    cargas, db, encuestas, ingesta, lista_negra, longitudinal, paneles, personas,
    sav,
)

from test_r_org_origen_y_ambitos import (
    DEMOGRAFICAS, EVIDENCIA, PREGUNTAS, _cargar, _fila, _panelista,
)


def _id(conn, documento):
    fila = db.una(conn, "select id_persona::text as id from persona where documento = %s",
                  (documento,))
    return fila["id"] if fila else None


def _de_la_lista(conn, encuesta_id):
    return next(e for e in encuestas.listar(conn) if e["id"] == encuesta_id)


# ════════════════════════════════════════════════════════════════════
#  3.1 / 3.2 · El origen, y «—» en una encuesta sin convocatoria
# ════════════════════════════════════════════════════════════════════

def test_una_encuesta_desde_una_carga_es_de_origen_carga_y_no_tiene_convocatoria(ctx):
    carga = cargas.crear(ctx.boveda, "AVANTI")
    _cargar(ctx, carga, [_fila(1), _fila(2)])

    fila = _de_la_lista(ctx.boveda, carga["encuesta_id"])
    assert fila["origen"] == encuestas.ORIGEN_CARGA
    # «0 convocados» no es «nadie convocó»: la pantalla muestra «—».
    assert fila["con_convocatoria"] is False and fila["respondieron"] == 2
    detalle = encuestas.obtener(ctx.boveda, carga["encuesta_id"], completa=True)
    assert detalle["con_convocatoria"] is False


def test_una_encuesta_desde_una_consulta_es_de_origen_consulta(ctx):
    alguien = _panelista(ctx.boveda, "30-1")
    salida = encuestas.crear_desde_consulta(
        ctx.boveda, "Seguimiento", {"items": [{"id_persona": str(alguien)}]})
    assert salida["origen"] == encuestas.ORIGEN_CONSULTA
    fila = _de_la_lista(ctx.boveda, salida["id"])
    assert fila["con_convocatoria"] is True and fila["convocados"] == 1


def test_una_encuesta_de_panel_es_de_origen_panel(ctx):
    panel = paneles.crear(ctx.boveda, "Panel general")
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola 1")
    assert encuesta["origen"] == encuestas.ORIGEN_PANEL
    # Recién creada, sin convocar todavía: tampoco hay convocatoria.
    assert _de_la_lista(ctx.boveda, encuesta["id"])["con_convocatoria"] is False


def test_el_origen_no_puede_contradecir_al_tipo(conn_boveda):
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn_boveda.transaction():
            db.ejecutar(conn_boveda, """insert into encuesta (nombre, tipo, origen)
                                        values ('x', 'independiente', 'panel')""")


def test_una_insercion_sin_origen_se_completa_sola(conn_boveda):
    """El código anterior a la 0033 no lo dice: de panel si es de panel, y
    si no de carga."""
    panel = paneles.crear(conn_boveda, "Panel")
    de_panel = db.una(conn_boveda, """insert into encuesta (panel_id, nombre)
                                      values (%s, 'Ola') returning origen""",
                      (panel["id"],))
    suelta = db.una(conn_boveda, """insert into encuesta (nombre, tipo)
                                    values ('Archivo', 'independiente') returning origen""")
    assert (de_panel["origen"], suelta["origen"]) == ("panel", "carga")


# ════════════════════════════════════════════════════════════════════
#  3.4 · Nadie no convocado tiene fecha de convocatoria
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def ola_con_un_no_convocado(ctx):
    """Una ola de panel con dos convocados y alguien que respondió sin estar
    convocado (un enlace reenviado, R-EU.4)."""
    panel = paneles.crear(ctx.boveda, "Panel general")
    ids = [_panelista(ctx.boveda, f"20-{i}", f"Panelista {i}") for i in range(1, 4)]
    for i in ids:
        paneles.agregar_miembro(ctx.boveda, panel["id"], i)
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola 1")
    encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=ids[:2])
    carga = encuestas.carga_por_defecto(ctx.boveda, encuesta["id"])
    cargas.ingestar(ctx.boveda, ctx.semantica, carga, PREGUNTAS[:1],
                    [_fila(i) for i in range(1, 4)], columna_id="DOC",
                    origen="omnibus", proveedor=ctx.embeddings,
                    tipo_identificador="documento")
    return encuesta, ids


def test_el_no_convocado_no_tiene_fecha_de_convocatoria(ctx, ola_con_un_no_convocado):
    encuesta, ids = ola_con_un_no_convocado
    por_persona = {p["id_persona"]: p
                   for p in encuestas.listar_participacion(ctx.boveda, encuesta["id"])}
    assert por_persona[ids[0]]["convocado"] is True
    assert por_persona[ids[0]]["convocado_en"]
    assert por_persona[ids[2]]["convocado"] is False
    assert por_persona[ids[2]]["convocado_en"] is None
    # La fila tiene su fecha igual: cuándo llegó.
    assert por_persona[ids[2]]["registrado_en"]
    assert _de_la_lista(ctx.boveda, encuesta["id"])["no_convocados"] == 1


def test_convocar_a_quien_ya_respondio_le_pone_la_fecha_de_la_convocatoria(
        ctx, ola_con_un_no_convocado):
    encuesta, ids = ola_con_un_no_convocado
    db.ejecutar(ctx.boveda, "update participacion set convocado_en = '2026-01-01' "
                            "where encuesta_id = %s and id_persona = %s",
                (encuesta["id"], ids[2]))
    encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=[ids[2]])
    fila = next(p for p in encuestas.listar_participacion(ctx.boveda, encuesta["id"])
                if p["id_persona"] == ids[2])
    assert fila["convocado"] is True and not fila["convocado_en"].startswith("2026-01-01")


def test_la_longitudinal_no_presenta_fecha_de_convocatoria_sin_convocatoria(
        ctx, ola_con_un_no_convocado, actor):
    encuesta, ids = ola_con_un_no_convocado
    olas = longitudinal.de_persona(ctx.boveda, ctx.semantica, ids[2], actor=actor(),
                                   con_respuestas=False)["olas"]
    assert [(o["convocado"], o["convocado_en"]) for o in olas] == [(False, None)]


def test_la_ficha_dice_cuando_llego_quien_vino_en_un_archivo(ctx):
    """Una participación importada sin carga (anterior a la 0032) es «otro
    ingreso»: su fecha es la de la respuesta, no una convocatoria."""
    panel = paneles.crear(ctx.boveda, "Panel general")
    persona = _panelista(ctx.boveda, "50-1")
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola vieja")
    db.ejecutar(ctx.boveda, """insert into participacion (encuesta_id, id_persona, origen,
                                   convocado, convocado_en, respondio, respondio_en)
                               values (%s, %s, 'importacion', false, '2026-01-01',
                                       true, '2026-03-15')""",
                (encuesta["id"], persona))
    ingreso = next(i for i in cargas.cargas_de_persona(ctx.boveda, persona)["otros_ingresos"]
                   if i["via"] == cargas.INGRESO_ENCUESTA)
    assert ingreso["fecha"].startswith("2026-03-15")


def test_la_lista_negra_cancela_solo_convocatorias(ctx, ola_con_un_no_convocado):
    encuesta, ids = ola_con_un_no_convocado
    # Uno convocado que no respondió: es una invitación pendiente.
    pendiente = _panelista(ctx.boveda, "20-9", "Pendiente")
    encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=[pendiente])
    salida = lista_negra.agregar(ctx.boveda, pendiente, "calidad_respuestas", "al azar")
    assert [i["encuesta_id"] for i in salida["invitaciones_canceladas"]] == [encuesta["id"]]
    # Quien respondió sin estar convocado no tenía invitación que cancelar.
    salida = lista_negra.agregar(ctx.boveda, ids[2], "calidad_respuestas", "al azar")
    assert salida["invitaciones_canceladas"] == []


# ════════════════════════════════════════════════════════════════════
#  3.2 · La migración marca las encuestas de la 0032 como de carga
# ════════════════════════════════════════════════════════════════════

def test_la_0033_marca_como_carga_las_encuestas_que_creo_la_0032(
        dsn_boveda, conn_semantica, proveedor):
    import test_migracion_0032 as m

    LA_0033 = m.RAIZ / "db" / "boveda" / "0033_origen_de_la_encuesta.sql"
    base = "prueba_0033"
    admin = psycopg.connect(m._dsn_de(dsn_boveda, "postgres"), autocommit=True)
    admin.execute(f"drop database if exists {base} with (force)")
    admin.execute(f"create database {base}")
    dsn = m._dsn_de(dsn_boveda, base)
    try:
        for archivo in m.ANTES_DE_LA_0032:
            subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn, "-f",
                            str(archivo)], check=True, capture_output=True)
        with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
            datos = m._sembrar(conn, conn_semantica, proveedor)
            for archivo in (m.LA_0032, LA_0033):
                subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-d", dsn, "-f",
                                str(archivo)], check=True, capture_output=True)
            origenes = {f["nombre"]: (f["tipo"], f["origen"]) for f in db.todas(
                conn, "select nombre, tipo, origen from encuesta")}
        # La carga migrada: independiente, de carga. Las olas: de panel.
        assert origenes == {"Centel": ("independiente", "carga"),
                            "Ola 1": ("panel", "panel"),
                            "Ola 2": ("panel", "panel")}
        del datos
    finally:
        admin.execute(f"drop database if exists {base} with (force)")
        admin.close()
        conn_semantica.rollback()
        with conn_semantica.cursor() as cur:
            cur.execute("truncate respuesta, unidad_evidencia, pregunta, cuestionario, "
                        "individuo cascade")
        conn_semantica.commit()


# ════════════════════════════════════════════════════════════════════
#  4 · Identificadores y nombres sin letras ni dígitos
# ════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("valor", ["...........", "-----", "   ", "·-·"])
def test_un_identificador_sin_letras_ni_digitos_se_rechaza_al_crear(conn_boveda, valor):
    filas = [{**_fila(1), "ID": valor}, _fila(2)]
    salida = sav.crear_individuos(
        conn_boveda, filas, sav.mapeo_por_campo(DEMOGRAFICAS), origen="campo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        finalidad_obligatoria=sav.SEMANTICO)
    assert [c["id_en_origen"] for c in salida["creados"]] == ["o-2"]
    assert salida["identificadores_invalidos"] == [
        {"id_en_origen": valor, "motivo": "identificador_invalido"}]
    assert salida["resumen"]["identificadores_invalidos"] == 1
    assert _id(conn_boveda, "20-1") is None


@pytest.mark.parametrize("tipo", ["alias", "documento", "email", "celular", "id_persona"])
def test_al_resolver_un_valor_sin_contenido_es_identificador_invalido(conn_boveda, tipo):
    mapa, motivos = ingesta.resolver_identificadores(
        conn_boveda, tipo, ["...........", "-----"], origen="campo")
    assert mapa == {}
    assert motivos == {"...........": "identificador_invalido",
                       "-----": "identificador_invalido"}


def test_un_alias_sin_contenido_ya_registrado_no_se_vuelve_a_usar(ctx):
    """El caso de AVANTI: «...........» quedó como alias de una persona. Una
    carga posterior con esa fila no se la atribuye."""
    persona = _panelista(ctx.boveda, "60-1")
    db.ejecutar(ctx.boveda, "insert into alias_origen (id_persona, origen, id_en_origen) "
                            "values (%s, 'omnibus', '...........')", (persona,))
    carga = cargas.crear(ctx.boveda, "Otra entrega")
    resultado = cargas.ingestar(
        ctx.boveda, ctx.semantica, carga["id"], PREGUNTAS[:1],
        [{"ID": "...........", "P1": "1"}], columna_id="ID", origen="omnibus",
        proveedor=ctx.embeddings)
    assert resultado["sin_mapear_detalle"] == [
        {"id_en_origen": "...........", "motivo": "identificador_invalido"}]
    assert not db.una(ctx.boveda, "select 1 from participacion where id_persona = %s",
                      (persona,))


def test_un_nombre_sin_letras_se_guarda_vacio(conn_boveda):
    filas = [{**_fila(1), "NOM": "......"}, {**_fila(2), "NOM": "1234"}]
    sav.crear_individuos(
        conn_boveda, filas, sav.mapeo_por_campo(DEMOGRAFICAS), origen="campo",
        columna_id="ID", evidencia_consentimiento=EVIDENCIA,
        finalidad_obligatoria=sav.SEMANTICO)
    nombres = {f["documento"]: f["nombre"] for f in db.todas(
        conn_boveda, "select documento, nombre from persona")}
    assert nombres == {"20-1": None, "20-2": None}


def test_los_ya_cargados_se_listan_para_revisarlos(conn_boveda):
    """No se borran solos: pueden tener respuestas válidas y otros
    identificadores buenos. Se listan, con qué tienen mal."""
    malo = db.una(conn_boveda, """insert into persona (nombre, documento, email)
                                  values ('......', '1-1', 'ok@ej.uy')
                               returning id_persona::text as id""")["id"]
    con_alias = _panelista(conn_boveda, "70-1", "Persona bien")
    db.ejecutar(conn_boveda, "insert into alias_origen (id_persona, origen, id_en_origen) "
                             "values (%s, 'campo', '-----')", (con_alias,))
    _panelista(conn_boveda, "70-2", "Ñandú Pérez")          # bien, con ñ y tilde

    salida = personas.listar(conn_boveda, identificador_invalido=True)
    por_id = {i["id_persona"]: i["campos_invalidos"] for i in salida["items"]}
    assert por_id == {malo: ["nombre"], str(con_alias): ["id_en_origen"]}
    assert salida["total"] == 2
    # Sin el filtro, cada fila dice igual qué tiene mal (vacío si nada).
    todos = {i["id_persona"]: i["campos_invalidos"]
             for i in personas.listar(conn_boveda)["items"]}
    assert todos[malo] == ["nombre"] and len([v for v in todos.values() if not v]) == 1


def test_el_filtro_va_por_la_ruta_de_panelistas(ctx, actor):
    from panel_api import ruteo

    db.ejecutar(ctx.boveda, "insert into persona (nombre, documento) "
                            "values ('Alguien', '.........')")
    _, salida = ruteo.despachar("GET", "/panelistas", {},
                                {"identificador_invalido": "1"}, actor("operaciones"), ctx)
    assert [i["campos_invalidos"] for i in salida["items"]] == [["documento"]]
