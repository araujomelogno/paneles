"""SPEC_encuestas_y_cargas_unificadas — la encuesta es el estudio y la carga
es un archivo de ese estudio (D81).

Las pruebas siguen el criterio de aceptación de la spec, requisito por
requisito, contra Postgres de verdad: la unicidad de las participaciones, el
`on conflict` de las respuestas y la cascada de los borrados son de la base,
y nada de eso se prueba contra un doble.
"""

import pytest

from panel_api import (
    cargas, cargas_borrado, composicion, db, diferida, encuestas,
    encuestas_borrado, lista_negra, paneles, ruteo, semantica,
)
from panel_api.errores import Conflicto, DatosInvalidos, SinPermiso

from test_r_org_origen_y_ambitos import (
    DEMOGRAFICAS, EVIDENCIA, PREGUNTAS, _cargar, _fila, _panelista,
)


def _id(conn, documento):
    fila = db.una(conn, "select id_persona::text as id from persona where documento = %s",
                  (documento,))
    return fila["id"] if fila else None


def _participaciones(conn, encuesta_id):
    return {f["id_persona"]: f for f in db.todas(
        conn, """select id_persona::text as id_persona, respondio, convocado
                   from participacion where encuesta_id = %s""", (encuesta_id,))}


def _respuestas(conn_semantica, ref_estudio):
    """`{(id_persona, codigo): (valor, carga_id)}` del estudio."""
    filas = db.todas(conn_semantica, """
        select i.id_persona::text as id_persona, p.codigo,
               r.valor_texto as valor, r.carga_id,
               count(*) over (partition by r.individuo_id, r.pregunta_id)::int as copias
          from respuesta r
          join pregunta p on p.id = r.pregunta_id
          join cuestionario c on c.id = p.cuestionario_id
          join individuo i on i.id = r.individuo_id
         where c.ref_estudio = %s""", (str(ref_estudio),))
    assert all(f["copias"] == 1 for f in filas), "una respuesta por persona y pregunta"
    return {(f["id_persona"], f["codigo"]): (f["valor"], f["carga_id"]) for f in filas}


def _procesar(ctx):
    pendientes = list(ctx.encolador.encoladas)
    ctx.encolador.encoladas.clear()
    return [diferida.procesar_lote(ctx.boveda, ctx.semantica, t, i,
                                   proveedor=ctx.embeddings)
            for t, i in pendientes]


# ════════════════════════════════════════════════════════════════════
#  R-EU.1 · Toda ingesta es una carga de una encuesta
# ════════════════════════════════════════════════════════════════════

def test_una_carga_desde_panelistas_crea_su_encuesta_independiente_y_cerrada(ctx):
    carga = cargas.crear(ctx.boveda, "Ómnibus", fecha_estudio="2026-08-15",
                         publico_objetivo="Adultos", clientes=["antel"])
    encuesta = encuestas.obtener(ctx.boveda, carga["encuesta_id"], completa=True)
    assert encuesta["tipo"] == encuestas.INDEPENDIENTE
    assert encuesta["panel_id"] is None and encuesta["estado"] == "cerrada"
    # R-EU.2 — los datos del estudio quedan en la encuesta, con el mismo
    # ref_estudio que la carga.
    assert str(encuesta["fecha_campo"]) == "2026-08-15"
    assert encuesta["publico_objetivo"] == "Adultos"
    assert [c["nombre"] for c in encuesta["clientes"]] == ["antel"]
    assert str(encuesta["ref_estudio"]) == str(carga["ref_estudio"])


def test_cada_persona_de_la_carga_queda_con_su_participacion(ctx):
    carga = cargas.crear(ctx.boveda, "Ómnibus")
    _cargar(ctx, carga, [_fila(1), _fila(2)])
    participaciones = _participaciones(ctx.boveda, carga["encuesta_id"])
    assert set(participaciones) == {_id(ctx.boveda, "20-1"), _id(ctx.boveda, "20-2")}
    # Respondieron y nadie los convocó: llegaron en un archivo.
    assert all(p["respondio"] and not p["convocado"] for p in participaciones.values())


# ════════════════════════════════════════════════════════════════════
#  R-EU.2 / R-EU.3 · Varias cargas, un estudio, sin duplicados
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def estudio(ctx):
    encuesta = encuestas.crear(ctx.boveda, None, "Tracking telefonía",
                               fecha_campo="2026-09-01", tipo=encuestas.INDEPENDIENTE)
    ctx.boveda.commit()
    return encuesta


def _carga_de(ctx, encuesta, nombre):
    carga = cargas.crear(ctx.boveda, nombre, encuesta_id=encuesta["id"])
    # R-EU.2 — no vuelve a pedir los datos del estudio: los lee de la encuesta.
    assert carga["encuesta_id"] == encuesta["id"]
    assert str(carga["fecha_estudio"]) == "2026-09-01"
    return carga


def test_tres_cargas_de_la_misma_gente_no_duplican_nada(ctx, estudio):
    filas = [_fila(i) for i in range(1, 6)]
    ultima = None
    for n in range(3):
        ultima = _carga_de(ctx, estudio, f"Entrega {n + 1}")
        _cargar(ctx, ultima, filas)

    assert db.una(ctx.boveda, "select count(*)::int as n from persona")["n"] == 5
    assert len(_participaciones(ctx.boveda, estudio["id"])) == 5
    respuestas = _respuestas(ctx.semantica, estudio["ref_estudio"])
    # Una respuesta por persona y pregunta, y todas de la última carga.
    assert len(respuestas) == 5
    assert {carga_id for _, carga_id in respuestas.values()} == {ultima["id"]}
    # Un solo cuestionario para todo el estudio.
    assert db.una(ctx.semantica, "select count(*)::int as n from cuestionario "
                                 "where ref_estudio = %s",
                  (str(estudio["ref_estudio"]),))["n"] == 1


def test_una_parcial_y_despues_la_final_deja_lo_de_la_final(ctx, estudio):
    parcial = _carga_de(ctx, estudio, "Parcial")
    _cargar(ctx, parcial, [_fila(i) for i in range(1, 4)])
    final = _carga_de(ctx, estudio, "Final")
    filas = [_fila(i) for i in range(1, 6)]
    filas[0]["P1"] = "2"                       # la persona 1 corrigió: Whisky
    _cargar(ctx, final, filas)

    respuestas = _respuestas(ctx.semantica, estudio["ref_estudio"])
    assert len(respuestas) == 5
    assert respuestas[(_id(ctx.boveda, "20-1"), "P1")] == ("Whisky", final["id"])
    assert len(_participaciones(ctx.boveda, estudio["id"])) == 5


def test_el_mismo_archivo_dos_veces_se_avisa_antes_de_procesar(ctx, actor, estudio):
    filas = [_fila(1), _fila(2)]
    primera = _carga_de(ctx, estudio, "Primera")
    db.ejecutar(ctx.boveda, "update carga set archivo_huella = %s where id = %s",
                (cargas.huella(filas=filas), primera["id"]))
    segunda = _carga_de(ctx, estudio, "Segunda")
    ctx.boveda.commit()
    cuerpo = {"filas": filas, "columna_id": "ID", "origen": "omnibus",
              "modo": "crear_individuos", "preguntas": PREGUNTAS,
              "demograficas": DEMOGRAFICAS, "evidencia_consentimiento": EVIDENCIA,
              "proponer_tematicas": False}
    _, revision = ruteo.despachar("POST", f"/cargas/{segunda['id']}/ingesta",
                                  {**cuerpo, "solo_revisar": True}, {},
                                  actor("analista"), ctx)
    assert revision["archivo_repetido"]["carga_id"] == primera["id"]
    with pytest.raises(Conflicto) as error:
        ruteo.despachar("POST", f"/cargas/{segunda['id']}/ingesta", cuerpo, {},
                        actor("analista"), ctx)
    assert error.value.detalle["motivo"] == "archivo_repetido"
    assert not ctx.encolador.encoladas
    # Confirmado, sigue.
    status, _ = ruteo.despachar(
        "POST", f"/cargas/{segunda['id']}/ingesta",
        {**cuerpo, "confirmar_archivo_repetido": True}, {}, actor("analista"), ctx)
    assert status == 202


# ════════════════════════════════════════════════════════════════════
#  R-EU.4 · Quien responde sin estar convocado
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def con_audiencia(ctx):
    panel = paneles.crear(ctx.boveda, "Panel general")
    ids = [_panelista(ctx.boveda, f"20-{i}", f"Panelista {i}") for i in range(1, 4)]
    for i in ids:
        paneles.agregar_miembro(ctx.boveda, panel["id"], i)
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola 1")
    encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=ids[:2])
    ctx.boveda.commit()
    return encuesta, ids


def test_la_revision_muestra_a_los_no_convocados(ctx, con_audiencia):
    encuesta, _ = con_audiencia
    filas = [_fila(i) for i in range(1, 5)]           # 3 existe sin convocar; 4 no existe
    salida = cargas.no_convocados(ctx.boveda, encuesta["id"], filas, "DOC",
                                  "documento", "omnibus")
    assert salida["cuantos"] == 2
    assert salida["existentes"] == 1 and salida["sin_identificar"] == 1
    assert set(salida["ids_en_origen"]) == {"20-3", "20-4"}


def test_sin_audiencia_no_hay_no_convocados(ctx, estudio):
    assert cargas.no_convocados(ctx.boveda, estudio["id"], [_fila(1)], "DOC",
                                "documento", "omnibus") is None


def test_el_no_convocado_queda_marcado_y_se_puede_excluir(ctx, con_audiencia):
    encuesta, ids = con_audiencia
    filas = [_fila(i) for i in range(1, 4)]
    carga = {"id": encuestas.carga_por_defecto(ctx.boveda, encuesta["id"])}
    cargas.ingestar(ctx.boveda, ctx.semantica, carga["id"], PREGUNTAS[:1], filas,
                    columna_id="DOC", origen="omnibus", proveedor=ctx.embeddings,
                    tipo_identificador="documento")
    participaciones = _participaciones(ctx.boveda, encuesta["id"])
    assert participaciones[ids[0]]["convocado"] and participaciones[ids[0]]["respondio"]
    assert not participaciones[ids[2]]["convocado"] and participaciones[ids[2]]["respondio"]
    assert encuestas.conteos(ctx.boveda, encuesta["id"]) == {
        "convocados": 2, "respondieron": 3, "no_convocados": 1}

    fuera = cargas.ids_no_convocados(ctx.boveda, encuesta["id"], filas, "DOC",
                                     "documento", "omnibus")
    assert fuera == ["20-3"]
    assert [f["DOC"] for f in cargas.excluir_ids(filas, "DOC", fuera)] == ["20-1", "20-2"]


# ════════════════════════════════════════════════════════════════════
#  R-EU.5 · Una encuesta independiente desde una consulta
# ════════════════════════════════════════════════════════════════════

def test_la_encuesta_desde_una_consulta_es_la_foto_sin_lista_negra_ni_sin_contacto(ctx):
    convocable = _panelista(ctx.boveda, "30-1", "Convocable")
    vetada = _panelista(ctx.boveda, "30-2", "En la lista negra")
    lista_negra.agregar(ctx.boveda, vetada, "calidad_respuestas", "respuestas al azar")
    sin_contacto = _id(ctx.boveda, "30-3")
    if sin_contacto is None:
        from panel_api import personas
        from conftest import consentimientos
        sin_contacto = personas.alta(ctx.boveda, {
            "persona": {"documento": "30-3", "nombre": "Solo semántico"},
            "consentimientos": consentimientos("uso_semantico")})["id_persona"]
    resultado = {"items": [{"id_persona": str(i)}
                           for i in (convocable, vetada, sin_contacto)]}

    salida = encuestas.crear_desde_consulta(ctx.boveda, "Seguimiento a quienes "
                                            "hablaron de Antel", resultado)
    assert salida["tipo"] == encuestas.INDEPENDIENTE and salida["panel_id"] is None
    assert salida["audiencia"]["en_el_resultado"] == 3
    assert salida["audiencia"]["convocados"] == 1
    assert salida["audiencia"]["en_lista_negra"] == 1
    assert salida["audiencia"]["sin_consentimiento"] == 1
    assert set(_participaciones(ctx.boveda, salida["id"])) == {str(convocable)}


def test_una_encuesta_independiente_no_tiene_muestreo(ctx, estudio):
    with pytest.raises(Conflicto):
        encuestas.exigir_de_panel(ctx.boveda, estudio["id"])


# ════════════════════════════════════════════════════════════════════
#  R-EU.6 · El consentimiento, en la vista previa
# ════════════════════════════════════════════════════════════════════

def test_la_vista_previa_dice_quien_queda_sin_cada_finalidad_y_que_implica(ctx, estudio):
    filas = [_fila(1), _fila(2)]
    filas[1]["CONS"] = "2"                      # la 2 no consintió el uso semántico
    evidencia = {"uso_semantico": {"variable": "CONS", "valores_afirmativos": ["1"]}}
    salida = {f["finalidad"]: f for f in cargas.consentimiento_por_finalidad(
        ctx.boveda, estudio["id"], filas, "ID", "alias", "omnibus",
        evidencia=evidencia, crea_personas=True)}
    assert salida["uso_semantico"]["sin_consentimiento"] == 1
    assert "no entran al store semántico" in salida["uso_semantico"]["implica"]
    assert salida["contacto_participacion"]["sin_consentimiento"] == 2
    assert "No se los convocará" in salida["contacto_participacion"]["implica"]


def test_quien_no_consiente_no_va_a_la_lista_negra(ctx, estudio):
    carga = _carga_de(ctx, estudio, "Entrega")
    _cargar(ctx, carga, [_fila(1)])
    assert db.una(ctx.boveda, "select count(*)::int as n from lista_negra")["n"] == 0


# ════════════════════════════════════════════════════════════════════
#  R-EU.8 · Archivar paneles
# ════════════════════════════════════════════════════════════════════

def test_un_panel_archivado_no_recibe_encuestas_y_se_oculta(ctx):
    panel = paneles.crear(ctx.boveda, "Panel viejo")
    paneles.archivar(ctx.boveda, panel["id"])
    assert panel["id"] not in {p["id"] for p in paneles.listar(ctx.boveda)}
    assert panel["id"] in {p["id"] for p in paneles.listar(ctx.boveda,
                                                            incluir_archivados=True)}
    with pytest.raises(Conflicto):
        encuestas.crear(ctx.boveda, panel["id"], "Ola nueva")
    # Se reactiva.
    paneles.archivar(ctx.boveda, panel["id"], paneles.ACTIVO)
    assert encuestas.crear(ctx.boveda, panel["id"], "Ola nueva")["panel_id"] == panel["id"]


def test_no_se_archiva_un_panel_con_encuestas_en_campo(ctx, con_audiencia):
    encuesta, _ = con_audiencia
    assert encuestas.obtener(ctx.boveda, encuesta["id"])["estado"] == "en_campo"
    with pytest.raises(Conflicto) as error:
        paneles.archivar(ctx.boveda, encuesta["panel_id"])
    assert error.value.detalle["motivo"] == "encuestas_en_campo"


def test_un_panel_archivado_no_convoca(ctx, con_audiencia):
    encuesta, ids = con_audiencia
    encuestas.cambiar_estado(ctx.boveda, encuesta["id"], "cerrada")
    paneles.archivar(ctx.boveda, encuesta["panel_id"])
    with pytest.raises(Conflicto):
        encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=ids[2:])


# ════════════════════════════════════════════════════════════════════
#  R-EU.9 · Borrar una encuesta
# ════════════════════════════════════════════════════════════════════

def test_borrar_una_encuesta_borra_el_estudio_y_conserva_a_la_gente(ctx, actor, estudio):
    for nombre, filas in (("Uno", [_fila(1), _fila(2)]), ("Dos", [_fila(2), _fila(3)])):
        _cargar(ctx, _carga_de(ctx, estudio, nombre), filas)
    ctx.boveda.commit()
    ctx.semantica.commit()
    ruta = f"/encuestas/{estudio['id']}/borrado"

    _, previa = ruteo.despachar("POST", ruta, {"solo_revisar": True}, {}, actor("dpo"), ctx)
    assert previa["respuestas_a_borrar"] == 3
    assert previa["participaciones_a_borrar"] == 3
    assert previa["cargas_a_borrar"] == 2
    assert previa["personas_que_se_conservan"] == 3

    with pytest.raises(DatosInvalidos):
        ruteo.despachar("POST", ruta, {"confirmacion": "otra"}, {}, actor("admin"), ctx)
    with pytest.raises(SinPermiso):
        ruteo.despachar("POST", ruta, {"confirmacion": estudio["nombre"]}, {},
                        actor("analista"), ctx)

    _, salida = ruteo.despachar("POST", ruta, {"confirmacion": estudio["nombre"]}, {},
                                actor("admin"), ctx)
    assert salida["estado"] == "terminado"
    assert semantica.contar_estudio(ctx.semantica, estudio["ref_estudio"])["respuestas"] == 0
    assert db.una(ctx.boveda, "select count(*)::int as n from encuesta where id = %s",
                  (estudio["id"],))["n"] == 0
    assert db.una(ctx.boveda, "select count(*)::int as n from carga")["n"] == 0
    assert db.una(ctx.boveda, "select count(*)::int as n from participacion")["n"] == 0
    # Las personas, intactas.
    assert db.una(ctx.boveda, "select count(*)::int as n from persona")["n"] == 3
    auditoria = db.una(ctx.boveda, "select * from encuesta_borrado")
    assert auditoria["encuesta_id"] is None              # sobrevive a la encuesta
    assert auditoria["actor_email"] == "admin@equipos.com.uy"
    assert auditoria["respuestas_borradas"] == 3


def test_borrar_una_encuesta_a_medias_se_termina_volviendo_a_pedirlo(
        ctx, estudio, monkeypatch):
    _cargar(ctx, _carga_de(ctx, estudio, "Uno"), [_fila(1)])
    ctx.boveda.commit()
    ctx.semantica.commit()

    def falla(*_args, **_kwargs):
        raise RuntimeError("se cayó la conexión")

    original = encuestas_borrado.terminar
    monkeypatch.setattr(encuestas_borrado, "terminar", falla)
    with pytest.raises(RuntimeError):
        encuestas_borrado.lanzar(ctx.boveda, ctx.semantica, estudio["id"],
                                 confirmacion=estudio["nombre"])
    monkeypatch.setattr(encuestas_borrado, "terminar", original)
    _, salida = encuestas_borrado.lanzar(ctx.boveda, ctx.semantica, estudio["id"],
                                         confirmacion=estudio["nombre"])
    assert salida["estado"] == "terminado"
    assert db.una(ctx.boveda, "select count(*)::int as n from encuesta_borrado")["n"] == 1


def test_no_se_borra_una_encuesta_con_una_ingesta_en_curso(ctx, estudio):
    carga = _carga_de(ctx, estudio, "Uno")
    diferida.encolar(ctx.boveda, "carga", carga["id"], {}, [_fila(1)],
                     encolador=diferida.EncoladorEnMemoria())
    with pytest.raises(Conflicto):
        encuestas_borrado.lanzar(ctx.boveda, ctx.semantica, estudio["id"],
                                 confirmacion=estudio["nombre"])


# ════════════════════════════════════════════════════════════════════
#  R-EU.10 · Borrar una carga dentro de una encuesta
# ════════════════════════════════════════════════════════════════════

def test_borrar_una_carga_deshace_solo_lo_que_esa_carga_escribio(ctx, actor, estudio):
    uno = _carga_de(ctx, estudio, "Uno")
    _cargar(ctx, uno, [_fila(1), _fila(2)])
    dos = _carga_de(ctx, estudio, "Dos")
    _cargar(ctx, dos, [_fila(2), _fila(3)])
    ctx.boveda.commit()
    ctx.semantica.commit()
    a, b, c = (_id(ctx.boveda, f"20-{i}") for i in (1, 2, 3))

    _, previa = cargas_borrado.lanzar(ctx.boveda, ctx.semantica, dos["id"],
                                      solo_revisar=True)
    assert previa["unica_de_su_encuesta"] is False
    assert previa["respuestas_a_borrar"] == 2            # las de B y C, de «Dos»
    assert previa["se_dan_de_baja"] == 0                 # sin la opción, nadie

    cargas_borrado.lanzar(ctx.boveda, ctx.semantica, dos["id"],
                          confirmacion="Dos", actor=actor("admin"),
                          encolador=ctx.encolador)
    _procesar(ctx)

    respuestas = _respuestas(ctx.semantica, estudio["ref_estudio"])
    # Solo queda lo que escribió «Uno»: la de B la había pisado «Dos».
    assert set(respuestas) == {(a, "P1")}
    participaciones = _participaciones(ctx.boveda, estudio["id"])
    assert a in participaciones and b in participaciones
    assert c not in participaciones                      # solo estaba en «Dos»
    # Nadie se dio de baja, y la encuesta sigue con su otra carga.
    assert db.una(ctx.boveda, "select count(*)::int as n from persona")["n"] == 3
    assert [x["id"] for x in cargas.listar(ctx.boveda, encuesta_id=estudio["id"])] == [uno["id"]]


def test_borrar_la_unica_carga_ofrece_dar_de_baja_a_las_que_creo(ctx, actor):
    carga = cargas.crear(ctx.boveda, "Ómnibus")
    _cargar(ctx, carga, [_fila(1), _fila(2)])
    ctx.boveda.commit()
    ctx.semantica.commit()
    _, previa = cargas_borrado.lanzar(ctx.boveda, ctx.semantica, carga["id"],
                                      solo_revisar=True, dar_de_baja_creadas=True)
    assert previa["se_dan_de_baja"] == 2
    cargas_borrado.lanzar(ctx.boveda, ctx.semantica, carga["id"],
                          confirmacion="Ómnibus", actor=actor("admin"),
                          encolador=ctx.encolador, dar_de_baja_creadas=True)
    _procesar(ctx)
    assert db.una(ctx.boveda, "select count(*)::int as n from persona")["n"] == 0
    # La encuesta independiente que quedó vacía se va con su única carga.
    assert db.una(ctx.boveda, "select count(*)::int as n from encuesta where id = %s",
                  (carga["encuesta_id"],))["n"] == 0


# ════════════════════════════════════════════════════════════════════
#  R-EU.11 · Composición de una encuesta: convocados vs respondentes
# ════════════════════════════════════════════════════════════════════

def test_la_composicion_de_una_encuesta_tiene_la_tasa_de_respuesta_por_segmento(ctx):
    panel = paneles.crear(ctx.boveda, "Panel general")
    mujeres = [_panelista(ctx.boveda, f"40-{i}", sexo="F") for i in range(2)]
    hombres = [_panelista(ctx.boveda, f"41-{i}", sexo="M") for i in range(2)]
    for i in mujeres + hombres:
        paneles.agregar_miembro(ctx.boveda, panel["id"], i)
    encuesta = encuestas.crear(ctx.boveda, panel["id"], "Ola 1")
    encuestas.convocar(ctx.boveda, encuesta["id"], ids_persona=mujeres + hombres)
    encuestas.marcar_respuesta(ctx.boveda, encuesta["id"], [mujeres[0]] + hombres)

    salida = composicion.composicion_de_ambito(ctx.boveda, "encuesta", encuesta["id"],
                                               dimensiones=["sexo"])
    assert salida["con_audiencia"] and salida["convocados"] == 4
    assert salida["miembros"] == 3 and salida["tasa_respuesta"] == 0.75
    por = {c["categoria"]: c for c in salida["dimensiones"][0]["categorias"]}
    assert por["F"]["convocados"] == 2 and por["F"]["tasa_respuesta"] == 0.5
    assert por["M"]["convocados"] == 2 and por["M"]["tasa_respuesta"] == 1.0
    assert salida["objetivo_admitido"] is False


def test_sin_audiencia_la_composicion_es_solo_de_respondentes(ctx):
    carga = cargas.crear(ctx.boveda, "Ómnibus")
    _cargar(ctx, carga, [_fila(1), _fila(2, sexo="1")])
    salida = composicion.composicion_de_ambito(ctx.boveda, "encuesta",
                                               carga["encuesta_id"], dimensiones=["sexo"])
    assert salida["con_audiencia"] is False and salida["tasa_respuesta"] is None
    assert salida["miembros"] == 2
    assert all("convocados" not in c for c in salida["dimensiones"][0]["categorias"])
