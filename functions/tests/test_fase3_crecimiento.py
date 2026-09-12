"""Bloque 3B — crecimiento y administración: R3.7 y R3.8.

**R3.8 ya estaba.** Se implementó en la Fase 2 como R2.12 y sus criterios son
los mismos, así que acá no se reimplementa nada: se comprueba que la
cobertura existente responde punto por punto al DoD de R3.8, y se agrega lo
único que la Fase 2 no verificaba (que el script de bootstrap sigue estando).
El detalle vive en `test_usuarios.py`.
"""

import pathlib

import pytest

from panel_api import auth, db, inscripciones as ins, paneles, personas
from panel_api.errores import DatosInvalidos

RAIZ = pathlib.Path(__file__).resolve().parents[2]
VERSION = "2026-09"
CUERPO = "Texto de prueba. No es un texto legal: el de producción lo redacta el DPO."


@pytest.fixture
def con_texto(conn_boveda, actor):
    ins.publicar_texto(conn_boveda, "contacto_participacion", VERSION, CUERPO,
                       actor("admin"))
    return actor("admin")


def _envio(**extra):
    cuerpo = {
        "persona": {"nombre": "Ana Pérez", "email": "ana@ejemplo.uy",
                    "documento": "1111111"},
        "acepto_consentimiento": True,
    }
    cuerpo.update(extra)
    return cuerpo


# ── R3.7 · Inscripción pública ──────────────────────────────────────

def test_una_inscripcion_registra_consentimiento_con_su_version(conn_boveda, con_texto):
    ins.inscribir(conn_boveda, _envio())

    pendiente = ins.listar(conn_boveda)[0]
    assert pendiente["version_texto"] == VERSION
    assert pendiente["finalidades"] == ["contacto_participacion"]
    assert pendiente["acepto_en"] is not None


def test_sin_aceptar_el_consentimiento_la_inscripcion_se_rechaza(conn_boveda,
                                                                 con_texto):
    with pytest.raises(DatosInvalidos, match="aceptar el consentimiento"):
        ins.inscribir(conn_boveda, _envio(acepto_consentimiento=False))

    assert ins.listar(conn_boveda) == [], "no puede guardar los datos «mientras tanto»"


def test_sin_texto_publicado_el_formulario_no_recibe(conn_boveda):
    assert ins.formulario(conn_boveda)["puede_recibir"] is False
    with pytest.raises(DatosInvalidos, match="no está habilitado"):
        ins.inscribir(conn_boveda, _envio())


def test_una_inscripcion_no_crea_ninguna_persona(conn_boveda, con_texto):
    """De acá sale, gratis, «no puede ser convocada ni aparecer en consultas
    semánticas»: no existe como panelista hasta que alguien la apruebe."""
    ins.inscribir(conn_boveda, _envio())

    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 0
    assert ins.listar(conn_boveda)[0]["estado"] == "pendiente"


def test_al_aprobar_recien_ahi_se_crea_la_persona(conn_boveda, con_texto, actor):
    panel = paneles.crear(conn_boveda, "General")
    conn_boveda.commit()
    ins.inscribir(conn_boveda, _envio())
    pendiente = ins.listar(conn_boveda)[0]

    resultado = ins.aprobar(conn_boveda, pendiente["id"], actor("operaciones"),
                            panel_id=panel["id"])

    assert resultado["estado"] == "aprobada"
    assert resultado["persona"] == "creada"
    consentimiento = db.una(
        conn_boveda,
        "select finalidad, version_texto, estado from consentimiento "
        " where id_persona = %s",
        (resultado["id_persona"],),
    )
    assert consentimiento["version_texto"] == VERSION
    assert consentimiento["estado"] == "vigente"
    assert db.una(
        conn_boveda,
        "select count(*)::int as n from membresia where id_persona = %s",
        (resultado["id_persona"],),
    )["n"] == 1


def test_la_inscripcion_de_alguien_que_ya_existe_reutiliza_su_id(
    conn_boveda, con_texto, actor
):
    """Caso borde de la spec: «inscripción pública de alguien que ya es
    panelista»."""
    panel = paneles.crear(conn_boveda, "General")
    ya = personas.alta(conn_boveda, {
        "persona": {"nombre": "Ana Pérez", "email": "ana@ejemplo.uy",
                    "documento": "1111111"},
        "consentimientos": [{"finalidad": "contacto_participacion",
                             "version_texto": "vieja"}],
        "panel_id": panel["id"],
    })
    conn_boveda.commit()

    ins.inscribir(conn_boveda, _envio())
    pendiente = ins.listar(conn_boveda)[0]
    assert pendiente["resolucion"] == "reutiliza"
    resultado = ins.aprobar(conn_boveda, pendiente["id"], actor("operaciones"))

    assert resultado["id_persona"] == ya["id_persona"]
    assert resultado["persona"] == "reutilizada"
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1


def test_la_respuesta_publica_no_revela_si_la_persona_ya_estaba(
    conn_boveda, con_texto
):
    """El formulario no puede ser un oráculo para averiguar quién es
    panelista probando documentos."""
    panel = paneles.crear(conn_boveda, "General")
    personas.alta(conn_boveda, {
        "persona": {"nombre": "Ana Pérez", "email": "ana@ejemplo.uy",
                    "documento": "1111111"},
        "consentimientos": [{"finalidad": "contacto_participacion",
                             "version_texto": "vieja"}],
        "panel_id": panel["id"],
    })
    conn_boveda.commit()

    conocida = ins.inscribir(conn_boveda, _envio())
    desconocida = ins.inscribir(conn_boveda, _envio(persona={
        "nombre": "Zoe Nueva", "email": "zoe@ejemplo.uy", "documento": "9999999"
    }))

    assert conocida == desconocida, (
        "las dos respuestas tienen que ser idénticas: la diferencia sería "
        "una filtración"
    )
    for clave in ("id_persona", "resolucion", "id"):
        assert clave not in conocida


def test_el_caso_ambiguo_va_a_revision_y_no_se_fusiona(conn_boveda, con_texto, actor):
    panel = paneles.crear(conn_boveda, "General")
    personas.alta(conn_boveda, {
        "persona": {"nombre": "Juan Gómez", "fecha_nacimiento": "1990-01-01",
                    "localidad": "Salto"},
        "consentimientos": [{"finalidad": "contacto_participacion",
                             "version_texto": "vieja"}],
        "panel_id": panel["id"],
    })
    conn_boveda.commit()

    ins.inscribir(conn_boveda, {
        "persona": {"nombre": "Juan Gómez", "email": "juan2@ejemplo.uy",
                    "fecha_nacimiento": "1990-01-01"},
        "acepto_consentimiento": True,
    })
    pendiente = ins.listar(conn_boveda)[0]
    # El email lo desambigua en el envío; se lo saca para forzar el caso.
    db.ejecutar(conn_boveda, "update inscripcion set email = null where id = %s",
                (pendiente["id"],))
    conn_boveda.commit()

    resultado = ins.aprobar(conn_boveda, pendiente["id"], actor("operaciones"))

    assert resultado["estado"] == "revision"
    assert resultado["revision_id"] is not None
    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 1
    assert ins.obtener(conn_boveda, pendiente["id"])["estado"] == "pendiente"


def test_el_texto_se_versiona_y_no_se_reescribe(conn_boveda, con_texto):
    with pytest.raises(DatosInvalidos, match="no se puede reescribir"):
        ins.publicar_texto(conn_boveda, "contacto_participacion", VERSION,
                           "otro cuerpo", None)


def test_publicar_una_version_nueva_no_altera_lo_ya_consentido(
    conn_boveda, con_texto, actor
):
    """R3.7 — «cambiar el texto no altera lo que ya consintieron los
    inscriptos anteriores»."""
    ins.inscribir(conn_boveda, _envio())
    aprobada = ins.aprobar(conn_boveda, ins.listar(conn_boveda)[0]["id"],
                           actor("operaciones"))
    antes = db.una(
        conn_boveda,
        "select version_texto from consentimiento where id_persona = %s",
        (aprobada["id_persona"],),
    )["version_texto"]

    ins.publicar_texto(conn_boveda, "contacto_participacion", "2026-10",
                       "Texto nuevo.", actor("dpo"))

    despues = db.una(
        conn_boveda,
        "select version_texto from consentimiento where id_persona = %s",
        (aprobada["id_persona"],),
    )["version_texto"]
    assert antes == despues == VERSION
    assert ins.texto_vigente(conn_boveda)["version"] == "2026-10"
    # Y la versión vieja sigue siendo recuperable: es lo que esa persona firmó.
    versiones = {t["version"]: t["cuerpo"] for t in ins.listar_textos(conn_boveda)}
    assert versiones[VERSION] == CUERPO


def test_un_envio_con_una_version_vieja_se_rechaza(conn_boveda, con_texto, actor):
    """El formulario se cargó con una versión y se publicó otra en el medio:
    aceptarlo registraría un consentimiento a un texto que nadie leyó."""
    ins.publicar_texto(conn_boveda, "contacto_participacion", "2026-10",
                       "Texto nuevo.", actor("dpo"))

    with pytest.raises(DatosInvalidos, match="cambió mientras completabas"):
        ins.inscribir(conn_boveda, _envio(version_texto=VERSION))


def test_la_landing_no_expone_datos_de_otros_panelistas(conn_boveda, con_texto):
    panel = paneles.crear(conn_boveda, "General")
    personas.alta(conn_boveda, {
        "persona": {"nombre": "Secreta", "email": "secreta@ejemplo.uy",
                    "documento": "5555555", "celular": "099111222"},
        "consentimientos": [{"finalidad": "contacto_participacion",
                             "version_texto": "vieja"}],
        "panel_id": panel["id"],
    })
    conn_boveda.commit()

    formulario = ins.formulario(conn_boveda)

    serializado = repr(formulario)
    for dato in ("Secreta", "secreta@ejemplo.uy", "5555555", "099111222"):
        assert dato not in serializado


def test_rechazar_una_inscripcion_no_crea_persona(conn_boveda, con_texto, actor):
    ins.inscribir(conn_boveda, _envio())
    pendiente = ins.listar(conn_boveda)[0]

    ins.rechazar(conn_boveda, pendiente["id"], actor("operaciones"), "datos falsos")

    assert db.una(conn_boveda, "select count(*)::int as n from persona")["n"] == 0
    assert ins.obtener(conn_boveda, pendiente["id"])["estado"] == "rechazada"


def test_una_inscripcion_ya_resuelta_no_se_aprueba_dos_veces(
    conn_boveda, con_texto, actor
):
    ins.inscribir(conn_boveda, _envio())
    pendiente = ins.listar(conn_boveda)[0]
    ins.aprobar(conn_boveda, pendiente["id"], actor("operaciones"))

    with pytest.raises(DatosInvalidos, match="ya está"):
        ins.aprobar(conn_boveda, pendiente["id"], actor("operaciones"))


def test_las_rutas_publicas_son_solo_esas_dos():
    """R3.7 — la landing es la única superficie sin login, y tiene que
    seguir siéndolo: cualquier ruta nueva que caiga acá sin querer es un
    agujero."""
    from panel_api import ruteo

    assert ruteo.PUBLICAS == frozenset({
        ("GET", "/inscripciones/formulario"),
        ("POST", "/inscripciones"),
    })
    assert ruteo.es_publica("POST", "/inscripciones") is True
    assert ruteo.es_publica("GET", "/inscripciones") is False, (
        "la bandeja de aprobación no es pública"
    )
    assert ruteo.es_publica("GET", "/panelistas") is False


# ── R3.8 · Gestión de usuarios ──────────────────────────────────────

def test_los_criterios_de_r38_ya_los_cubre_la_fase_2():
    """R3.8 es R2.12 con otro número. Esta prueba deja anotada la
    equivalencia para que nadie lo reimplemente, y falla si desaparece
    alguna de las pruebas que lo respaldan."""
    fuente = (RAIZ / "functions" / "tests" / "test_usuarios.py").read_text()
    for criterio, prueba in {
        "el padrón se ve con nombre, email, rol y estado":
            "test_el_padron_lista_a_todos_con_su_rol_y_estado",
        "el alta crea la cuenta y su ficha":
            "test_un_admin_da_de_alta_un_usuario_y_queda_con_su_rol",
        "un email que ya existe no recrea la cuenta":
            "test_dar_de_alta_un_email_existente_actualiza_sin_recrear_la_cuenta",
        "un rol inválido se rechaza":
            "test_un_rol_fuera_de_la_lista_se_rechaza",
        "un desactivado no opera y su histórico queda":
            "test_desactivar_apaga_el_acceso_sin_borrar_el_historial",
        "solo admin administra usuarios":
            "test_un_no_admin_no_puede_operar_la_gestion_de_usuarios",
        "un admin no puede quitarse el rol":
            "test_un_admin_no_puede_quitarse_su_propio_rol_de_admin",
        "un admin no puede desactivarse":
            "test_un_admin_no_puede_desactivarse_a_si_mismo",
        "todo queda auditado con autor y fecha":
            "test_los_cambios_de_rol_y_las_desactivaciones_quedan_con_autor_y_fecha",
        "la clave inicial no se muestra de forma persistente":
            "test_la_clave_inicial_no_se_muestra_de_forma_persistente",
    }.items():
        assert f"def {prueba}(" in fuente, f"falta la prueba de: {criterio}"


def test_el_script_de_bootstrap_del_primer_admin_se_conserva():
    """R3.8 — «el script de línea de comandos se conserva para el bootstrap
    del primer admin». Es lo único del DoD que no dependía de código de la
    app: sin él, un sistema recién desplegado no tiene por dónde entrar.
    """
    script = RAIZ / "scripts" / "alta_usuario.js"
    assert script.exists()
    assert "admin" in script.read_text()


def test_los_cuatro_roles_siguen_siendo_los_mismos():
    """R3.8 — «no incluye SSO, MFA ni permisos por panel: son los cuatro
    roles existentes»."""
    assert auth.ROLES == ("admin", "operaciones", "analista", "dpo")


@pytest.mark.parametrize("permiso, roles", [
    ("muestrear", {"admin", "operaciones"}),
    ("gamificacion", {"admin", "operaciones"}),
    ("aprobar_inscripciones", {"admin", "operaciones"}),
    ("publicar_consentimiento", {"admin", "dpo"}),
    ("exportar_identificado", {"admin", "operaciones"}),
    ("revisar_calidad", {"admin", "operaciones", "analista"}),
])
def test_los_permisos_nuevos_tienen_los_roles_que_les_corresponden(permiso, roles):
    assert auth.PERMISOS[permiso] == roles


def test_exportar_con_datos_no_lo_puede_el_analista(actor):
    """R3.10 se separa de `reidentificar` a propósito: ver en pantalla y
    llevarse un archivo con nombres no son el mismo riesgo."""
    assert actor("analista").puede("consultar") is True
    assert actor("analista").puede("exportar_identificado") is False
    assert actor("operaciones").puede("exportar_identificado") is True


# ── El contrato de rutas ────────────────────────────────────────────

def _rutas_registradas():
    from panel_api import ruteo

    return {(metodo, patron) for metodo, _, _, _, patron in ruteo.RUTAS}


@pytest.mark.parametrize("metodo, patron, requisito", [
    ("POST", "/encuestas/<encuesta_id>/muestreo", "R3.1"),
    ("GET", "/paneles/<panel_id>/umbrales-fatiga", "R3.1"),
    ("PUT", "/paneles/<panel_id>/umbrales-fatiga", "R3.1"),
    ("POST", "/encuestas/<encuesta_id>/calidad", "R3.2"),
    ("PATCH", "/participacion/<participacion_id>/calidad", "R3.2"),
    ("GET", "/panelistas/<id_persona>/puntos", "R3.3"),
    ("POST", "/puntos/liquidar", "R3.4"),
    ("GET", "/premios", "R3.5"),
    ("POST", "/premios", "R3.5"),
    ("PATCH", "/premios/<premio_id>", "R3.5"),
    ("POST", "/canjes", "R3.5"),
    ("PATCH", "/canjes/<canje_id>", "R3.5"),
    ("POST", "/paneles/<panel_id>/bonos", "R3.6"),
    ("POST", "/inscripciones", "R3.7"),
    ("GET", "/inscripciones", "R3.7"),
    ("POST", "/inscripciones/<inscripcion_id>/aprobar", "R3.7"),
    ("GET", "/usuarios", "R3.8"),
    ("POST", "/usuarios", "R3.8"),
    ("PATCH", "/usuarios/<uid>", "R3.8"),
    ("POST", "/encuestas/<encuesta_id>/sav/analizar", "R3.9"),
    ("POST", "/encuestas/<encuesta_id>/sav/ingesta", "R3.9"),
    ("POST", "/consultas/csv-identificado", "R3.10"),
    ("POST", "/paneles/desde-consulta", "R3.11"),
])
def test_el_contrato_de_api_de_la_spec_esta_completo(metodo, patron, requisito):
    """Las rutas propuestas en §7 de `specs/SPEC_fase3.md`, una por una."""
    assert (metodo, patron) in _rutas_registradas(), (
        f"falta {metodo} {patron} ({requisito})"
    )


def test_toda_ruta_que_nombra_el_despliegue_existe():
    """Escribí en el manual de despliegue una ruta que no existía. Esta
    prueba es lo que impide que vuelva a pasar: si el documento manda a
    llamar algo, tiene que estar registrado."""
    import re

    manual = (RAIZ / "docs" / "DESPLIEGUE - Fase 3.md").read_text()
    registradas = _rutas_registradas()
    nombradas = set(re.findall(
        r"(?:^|\s)(GET|POST|PUT|PATCH|DELETE)\s+/api(/[a-z0-9/{}<>_-]+)", manual
    ))
    assert nombradas, "el manual tiene que nombrar alguna ruta"
    for metodo, camino in nombradas:
        # El manual escribe los parámetros como {id}; el ruteo, como <id>.
        patron = re.sub(r"\{[a-z_]+\}", "<param>", camino).rstrip("/")
        candidatos = {
            (m, re.sub(r"<[a-z_]+>", "<param>", p).rstrip("/"))
            for m, p in registradas
        }
        assert (metodo, patron) in candidatos, (
            f"el despliegue nombra {metodo} /api{camino} y no existe esa ruta"
        )
