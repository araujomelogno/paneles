"""R2.12 — gestión de usuarios de la app, con sus barandas.

Estas pruebas son la razón por la que el padrón está detrás de una interfaz:
todas las reglas que importan —quién puede operar, qué no se puede hacer
sobre uno mismo, qué queda auditado— se verifican sin Firebase.
"""

import pytest

from panel_api import auditoria, auth, ruteo, usuarios
from panel_api.errores import Conflicto, DatosInvalidos, NoEncontrado, SinPermiso


@pytest.fixture
def admin(actor):
    return actor("admin", uid="uid-admin", email="jefa@equipos.com.uy")


def _alta(conn, padron, admin, email="ana@equipos.com.uy", rol="operaciones",
          nombre="Ana Pérez"):
    return usuarios.alta(
        conn, padron, {"email": email, "rol": rol, "nombre": nombre}, admin
    )


# ── Alta ─────────────────────────────────────────────────────────────

def test_un_admin_da_de_alta_un_usuario_y_queda_con_su_rol(
    conn_boveda, padron, admin
):
    """DoD: «un admin da de alta un usuario desde Configuración y esa persona
    puede ingresar con su rol».

    «Puede ingresar con su rol» se comprueba donde se decide: la resolución
    del actor a partir de la ficha del padrón. Si la ficha quedó bien, esa
    persona entra con los permisos de su rol y con ningún otro.
    """
    resultado = _alta(conn_boveda, padron, admin, rol="analista")
    conn_boveda.commit()

    assert resultado["estado"] == "creado"
    assert resultado["usuario"]["rol"] == "analista"
    assert resultado["usuario"]["activo"] is True

    # El mismo camino que sigue una request real.
    nuevo = auth.actor_de_request(
        {"Authorization": "Bearer token"},
        verificar_token=lambda _: {"uid": resultado["uid"],
                                   "email": "ana@equipos.com.uy"},
        buscar_usuario=padron.leer_ficha,
    )
    assert nuevo.rol == "analista"
    assert nuevo.puede("consultar") and nuevo.puede("fieldear")
    assert not nuevo.puede("gestionar_usuarios")
    assert not nuevo.puede("enrolar")


def test_dar_de_alta_un_email_existente_actualiza_sin_recrear_la_cuenta(
    conn_boveda, padron, admin
):
    """DoD: «dar de alta un email ya existente actualiza su ficha sin recrear
    la cuenta»."""
    primero = _alta(conn_boveda, padron, admin, rol="analista")
    segundo = _alta(conn_boveda, padron, admin, rol="operaciones",
                    nombre="Ana Pérez Bentancor")
    conn_boveda.commit()

    assert segundo["estado"] == "existente"
    assert segundo["uid"] == primero["uid"]
    assert len(padron.cuentas) == 1, "no se creó una cuenta nueva"
    assert segundo["usuario"]["rol"] == "operaciones"
    assert segundo["usuario"]["nombre"] == "Ana Pérez Bentancor"


def test_el_alta_de_un_email_existente_no_devuelve_credenciales(
    conn_boveda, padron, admin
):
    """No se toca la clave de alguien que ya tenía cuenta: la persona sigue
    entrando con la que tenía."""
    _alta(conn_boveda, padron, admin)
    segundo = _alta(conn_boveda, padron, admin, rol="analista")
    conn_boveda.commit()
    assert segundo["acceso"] is None


def test_la_clave_inicial_no_se_muestra_de_forma_persistente(
    conn_boveda, padron, admin
):
    """R2.12: «la clave inicial no se muestra de forma persistente».

    El alta no devuelve una clave: devuelve un enlace de restablecimiento
    marcado para mostrar una sola vez. La clave con la que se creó la cuenta
    es aleatoria y no se guarda en ningún lado.
    """
    resultado = _alta(conn_boveda, padron, admin)
    conn_boveda.commit()

    acceso = resultado["acceso"]
    assert acceso["metodo"] == "restablecimiento"
    assert acceso["mostrar_una_vez"] is True
    assert acceso["link"]
    assert "clave" not in acceso
    # Ni en la respuesta ni en la auditoría hay nada que se parezca a una clave.
    assert "password" not in str(resultado).lower()
    registro = auditoria.listar_usuario(conn_boveda, resultado["uid"])[0]
    assert "clave" not in str(registro["detalle"]).lower()


@pytest.mark.parametrize("rol", ["superadmin", "ADMIN_TOTAL", "", None, "root"])
def test_un_rol_fuera_de_la_lista_se_rechaza(conn_boveda, padron, admin, rol):
    """R2.12: «rechaza roles fuera del conjunto».

    Un rol inventado no falla al guardarse: falla después, cuando la persona
    entra y ningún permiso le aplica.
    """
    with pytest.raises(DatosInvalidos) as error:
        usuarios.alta(conn_boveda, padron,
                      {"email": "x@equipos.com.uy", "rol": rol}, admin)
    assert error.value.detalle["roles_validos"] == list(auth.ROLES)
    assert padron.cuentas == {}


@pytest.mark.parametrize("email", ["", None, "sin-arroba", "   "])
def test_un_email_invalido_se_rechaza(conn_boveda, padron, admin, email):
    with pytest.raises(DatosInvalidos):
        usuarios.alta(conn_boveda, padron, {"email": email, "rol": "analista"}, admin)


def test_el_email_se_normaliza_a_minusculas(conn_boveda, padron, admin):
    """Si no, `Ana@…` y `ana@…` serían dos cuentas para la misma persona y la
    idempotencia del alta no serviría de nada."""
    _alta(conn_boveda, padron, admin, email="Ana@Equipos.com.uy")
    segundo = _alta(conn_boveda, padron, admin, email="ana@equipos.com.uy")
    conn_boveda.commit()
    assert segundo["estado"] == "existente"
    assert segundo["usuario"]["email"] == "ana@equipos.com.uy"


# ── Cambios de rol y estado ──────────────────────────────────────────

def test_un_admin_no_puede_quitarse_su_propio_rol_de_admin(
    conn_boveda, padron, admin
):
    """DoD: «un admin no puede quitarse su propio rol admin ni desactivarse».

    No es paternalismo: si el último admin se degrada, no queda nadie que
    pueda dar de alta a nadie y la única salida es el script de emergencia
    con credenciales de GCP.
    """
    propio = usuarios.alta(
        conn_boveda, padron,
        {"email": admin.email, "rol": "admin", "nombre": "Jefa"},
        admin,
    )
    conn_boveda.commit()
    uid = propio["uid"]
    mismo = auth.Actor(uid=uid, email=admin.email, rol="admin")

    with pytest.raises(SinPermiso) as error:
        usuarios.cambiar(conn_boveda, padron, uid, {"rol": "analista"}, mismo)
    assert "tu propio rol" in str(error.value)
    assert padron.leer_ficha(uid)["rol"] == "admin", "no cambió nada"


def test_un_admin_no_puede_desactivarse_a_si_mismo(conn_boveda, padron, admin):
    propio = usuarios.alta(
        conn_boveda, padron, {"email": admin.email, "rol": "admin"}, admin
    )
    conn_boveda.commit()
    uid = propio["uid"]
    mismo = auth.Actor(uid=uid, email=admin.email, rol="admin")

    with pytest.raises(SinPermiso) as error:
        usuarios.cambiar(conn_boveda, padron, uid, {"activo": False}, mismo)
    assert "te quedarías afuera" in str(error.value)
    assert padron.leer_ficha(uid)["activo"] is True


def test_otro_admin_si_puede_degradar_y_desactivar(conn_boveda, padron, admin):
    """La baranda es sobre uno mismo, no sobre el rol: dos admins pueden
    administrarse entre ellos, que es lo que hace que la baranda sea segura."""
    otro = _alta(conn_boveda, padron, admin, email="otro@equipos.com.uy",
                 rol="admin")
    conn_boveda.commit()

    degradado = usuarios.cambiar(
        conn_boveda, padron, otro["uid"], {"rol": "analista"}, admin
    )
    desactivado = usuarios.cambiar(
        conn_boveda, padron, otro["uid"], {"activo": False}, admin
    )
    conn_boveda.commit()

    assert degradado["usuario"]["rol"] == "analista"
    assert desactivado["usuario"]["estado"] == "desactivado"


def test_desactivar_apaga_el_acceso_sin_borrar_el_historial(
    conn_boveda, padron, admin
):
    """R2.12: «un usuario desactivado no puede operar, sin borrar su
    historial»."""
    creado = _alta(conn_boveda, padron, admin)
    uid = creado["uid"]
    usuarios.cambiar(conn_boveda, padron, uid, {"activo": False}, admin)
    conn_boveda.commit()

    # La ficha sigue existiendo, y la auditoría también.
    assert padron.leer_ficha(uid) is not None
    assert padron.leer_ficha(uid)["rol"] == "operaciones"
    assert len(auditoria.listar_usuario(conn_boveda, uid)) == 2
    # La cuenta de Auth queda apagada: no alcanza con rechazar por la ficha.
    assert padron.cuentas[uid]["desactivado"] is True

    # Y ya no puede operar: el rechazo pasa al resolver el actor.
    with pytest.raises(SinPermiso):
        auth.actor_de_request(
            {"Authorization": "Bearer token"},
            verificar_token=lambda _: {"uid": uid},
            buscar_usuario=padron.leer_ficha,
        )


def test_reactivar_vuelve_a_dejar_entrar(conn_boveda, padron, admin):
    creado = _alta(conn_boveda, padron, admin)
    usuarios.cambiar(conn_boveda, padron, creado["uid"], {"activo": False}, admin)
    usuarios.cambiar(conn_boveda, padron, creado["uid"], {"activo": True}, admin)
    conn_boveda.commit()

    actor = auth.actor_de_request(
        {"Authorization": "Bearer token"},
        verificar_token=lambda _: {"uid": creado["uid"]},
        buscar_usuario=padron.leer_ficha,
    )
    assert actor.rol == "operaciones"
    acciones = [r["accion"] for r in auditoria.listar_usuario(conn_boveda, creado["uid"])]
    assert acciones == ["reactivacion", "desactivacion", "alta"]


def test_dar_de_alta_de_nuevo_a_alguien_desactivado_lo_reactiva(
    conn_boveda, padron, admin
):
    creado = _alta(conn_boveda, padron, admin)
    usuarios.cambiar(conn_boveda, padron, creado["uid"], {"activo": False}, admin)
    de_nuevo = _alta(conn_boveda, padron, admin, rol="analista")
    conn_boveda.commit()

    assert de_nuevo["usuario"]["activo"] is True
    assert padron.cuentas[creado["uid"]]["desactivado"] is False


def test_el_cambio_de_rol_vale_desde_la_proxima_operacion(
    conn_boveda, padron, admin
):
    """R2.12: «un cambio de rol se aplica a la siguiente operación».

    El rol se resuelve contra la ficha en cada request, así que no hace falta
    que la persona vuelva a entrar.
    """
    creado = _alta(conn_boveda, padron, admin, rol="analista")
    conn_boveda.commit()

    resolver = lambda: auth.actor_de_request(  # noqa: E731
        {"Authorization": "Bearer token"},
        verificar_token=lambda _: {"uid": creado["uid"]},
        buscar_usuario=padron.leer_ficha,
    )
    assert not resolver().puede("enrolar")

    resultado = usuarios.cambiar(
        conn_boveda, padron, creado["uid"], {"rol": "operaciones"}, admin
    )
    conn_boveda.commit()
    assert "próxima operación" in resultado["vigencia"]
    assert resolver().puede("enrolar")


def test_cambiar_a_un_usuario_que_no_existe_da_404(conn_boveda, padron, admin):
    with pytest.raises(NoEncontrado):
        usuarios.cambiar(conn_boveda, padron, "uid-fantasma", {"rol": "admin"}, admin)


def test_un_cambio_que_no_cambia_nada_avisa(conn_boveda, padron, admin):
    creado = _alta(conn_boveda, padron, admin)
    conn_boveda.commit()
    with pytest.raises(Conflicto):
        usuarios.cambiar(
            conn_boveda, padron, creado["uid"], {"rol": "operaciones"}, admin
        )


def test_un_rol_invalido_en_un_cambio_se_rechaza(conn_boveda, padron, admin):
    creado = _alta(conn_boveda, padron, admin)
    conn_boveda.commit()
    with pytest.raises(DatosInvalidos):
        usuarios.cambiar(conn_boveda, padron, creado["uid"], {"rol": "root"}, admin)


# ── Auditoría ────────────────────────────────────────────────────────

def test_los_cambios_de_rol_y_las_desactivaciones_quedan_con_autor_y_fecha(
    conn_boveda, padron, admin
):
    """DoD: «los cambios de rol y las desactivaciones quedan registrados con
    autor y fecha»."""
    creado = _alta(conn_boveda, padron, admin, rol="analista")
    usuarios.cambiar(conn_boveda, padron, creado["uid"], {"rol": "operaciones"}, admin)
    usuarios.cambiar(conn_boveda, padron, creado["uid"], {"activo": False}, admin)
    conn_boveda.commit()

    registros = auditoria.listar_usuario(conn_boveda, creado["uid"])
    assert [r["accion"] for r in registros] == ["desactivacion", "cambio_rol", "alta"]
    for registro in registros:
        assert registro["actor_uid"] == "uid-admin"
        assert registro["actor_email"] == "jefa@equipos.com.uy"
        assert registro["creado_en"]
        assert registro["email_objetivo"] == "ana@equipos.com.uy"

    cambio = registros[1]
    assert cambio["rol_anterior"] == "analista"
    assert cambio["rol_nuevo"] == "operaciones"


def test_un_cambio_rechazado_no_deja_rastro_de_auditoria(conn_boveda, padron, admin):
    """La auditoría registra lo que pasó, no lo que se intentó y se rechazó:
    si registrara los rechazos, «hay una desactivación en el log» dejaría de
    significar «alguien fue desactivado»."""
    propio = usuarios.alta(
        conn_boveda, padron, {"email": admin.email, "rol": "admin"}, admin
    )
    conn_boveda.commit()
    mismo = auth.Actor(uid=propio["uid"], email=admin.email, rol="admin")
    with pytest.raises(SinPermiso):
        usuarios.cambiar(conn_boveda, padron, propio["uid"], {"activo": False}, mismo)
    conn_boveda.rollback()

    acciones = [r["accion"] for r in auditoria.listar_usuario(conn_boveda, propio["uid"])]
    assert "desactivacion" not in acciones


# ── Permisos ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("rol", ["operaciones", "analista", "dpo"])
def test_un_no_admin_no_puede_operar_la_gestion_de_usuarios(actor, rol):
    """DoD: «un no-admin no puede acceder ni operar la gestión de usuarios».

    Se comprueba sobre la tabla de rutas, que es donde se decide: las cuatro
    rutas de `/usuarios` exigen `gestionar_usuarios`, y ese permiso lo tiene
    solo `admin`.
    """
    quien = actor(rol)
    assert not quien.puede("gestionar_usuarios")

    rutas_de_usuarios = [
        (metodo, patron, permiso)
        for metodo, _, permiso, _, patron in ruteo.RUTAS
        if patron.startswith("/usuarios")
    ]
    assert len(rutas_de_usuarios) == 4, "cambió la superficie de /usuarios"
    for metodo, patron, permiso in rutas_de_usuarios:
        assert permiso == "gestionar_usuarios", f"{metodo} {patron}"
        with pytest.raises(SinPermiso):
            quien.exigir(permiso)


def test_solo_admin_tiene_gestionar_usuarios():
    assert auth.PERMISOS["gestionar_usuarios"] == {"admin"}


# ── Padrón ───────────────────────────────────────────────────────────

def test_el_padron_lista_a_todos_con_su_rol_y_estado(conn_boveda, padron, admin):
    _alta(conn_boveda, padron, admin, email="ana@equipos.com.uy", rol="operaciones")
    _alta(conn_boveda, padron, admin, email="beto@equipos.com.uy", rol="analista",
          nombre="Beto Silva")
    conn_boveda.commit()

    listado = usuarios.listar(padron)
    assert listado["total"] == 2
    assert listado["roles"] == list(auth.ROLES)
    assert {u["email"]: u["rol"] for u in listado["items"]} == {
        "ana@equipos.com.uy": "operaciones",
        "beto@equipos.com.uy": "analista",
    }
    assert all(u["estado"] == "activo" for u in listado["items"])


def test_un_usuario_con_rol_mal_cargado_se_ve_marcado_en_vez_de_esconderse(
    padron
):
    """Si quedó uno mal cargado por el script de emergencia, hay que poder
    verlo y arreglarlo desde la app; esconderlo lo haría inarreglable."""
    padron.escribir_ficha("uid-viejo", {
        "nombre": "Alguien", "email": "viejo@equipos.com.uy",
        "rol": "superadmin", "activo": True,
    })
    item = usuarios.listar(padron)["items"][0]
    assert item["rol"] == "superadmin"
    assert item["rol_valido"] is False
