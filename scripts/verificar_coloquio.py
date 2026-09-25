#!/usr/bin/env python3
"""Se conecta a la bóveda **como `coloquio_app`** y comprueba que se defiende sola.

La Fase 5 no termina cuando las migraciones aplican. Termina cuando algo que
no es `paneles` demuestra, desde afuera, que la bóveda hace valer sus
invariantes por su cuenta. Leer la migración no alcanza: los tres defectos más
serios de esta fase —una vista que el consumidor no podía usar, un rol sin
registrar que igual conseguía un contacto, y `execute` regalado a `public`—
solo aparecieron al conectarse de verdad con el rol del consumidor.

Este script es ese «de verdad», escrito una vez para que se repita en cada
build.

    source scripts/pg_pruebas.sh
    python3 scripts/verificar_coloquio.py

Contra la instancia real, con el Auth Proxy abierto y autenticación IAM:

    export DSN_BOVEDA="$(scripts/dsn_local.sh boveda)"          # dueño
    export DSN_BOVEDA_COLOQUIO="postgresql://coloquio-app%40...:TOKEN@127.0.0.1:5432/paneles_boveda"
    python3 scripts/verificar_coloquio.py

Hacen falta **dos** conexiones, y el motivo es el punto del script: el
escenario (una persona con consentimiento, otra sin, una convocatoria abierta)
lo arma el dueño, porque `coloquio_app` no puede escribir nada. Si pudiera, no
habría nada que verificar.

Con `--solo-lectura` no se arma escenario ni se escribe nada: corren nada más
los chequeos que no necesitan datos de prueba —privilegios efectivos, negativas
de acceso, forma de las vistas—. Es el modo para apuntarlo a producción.

Sale con 0 si pasa todo y con 1 si falla algo, así que se puede encadenar.
"""

import argparse
import os
import pathlib
import sys
import uuid

ROJO, AMARILLO, VERDE, GRIS, FIN = (
    "\033[31m", "\033[33m", "\033[32m", "\033[90m", "\033[0m"
)

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# ════════════════════════════════════════════════════════════════════
#  La lista blanca
# ════════════════════════════════════════════════════════════════════
# Vive acá, en el repo, y no en la cabeza de alguien: es la definición de «lo
# que COLOQUIO puede tocar». Una migración futura que otorgue un privilegio de
# más rompe la build en vez de descubrirse en una auditoría.
#
# Se comparan **privilegios efectivos**, no los `grant` escritos: lo que
# importa es lo que el rol puede hacer hoy, incluyendo lo que herede de
# `public` o de otro rol.

RELACIONES_PERMITIDAS = {
    # relación → privilegios que `coloquio_app` puede tener sobre ella
    "v_persona_convocable": {"SELECT"},
    "v_fatiga_panelista": {"SELECT"},
    "v_finalidad": {"SELECT"},
    "v_texto_consentimiento_activo": {"SELECT"},
}

FUNCIONES_PERMITIDAS = {
    "contacto_para_convocatoria",
    "mis_borrados_pendientes",
    "confirmar_borrado",
    "reportar_error_de_borrado",
    "sistema_de_la_conexion",
    # La función detrás de `v_persona_convocable`. No es una puerta de atrás:
    # el gate de consentimiento está adentro de ella. Se otorga porque el
    # permiso de **ejecutar** una función se chequea contra quien invoca aun
    # cuando la llamada venga de adentro de una vista, así que sin esto la
    # vista sería ilegible para el consumidor.
    "f_persona_convocable",
}

# Qué cuenta como «función que `coloquio_app` puede ejecutar». Dos exclusiones,
# las dos deliberadas:
#
# · **Las de extensiones** (`pgcrypto`, `btree_gist`, `pgvector`). Nacen con
#   `execute` a `public` y revocárselo rompería la aplicación sin cerrar nada:
#   `gen_random_uuid()` o `gbt_int4_union()` no leen datos nuestros. Se
#   excluyen por `pg_depend`, que es lo que dice de qué extensión vino cada
#   una, y no por una lista de nombres que envejecería con cada `create
#   extension`.
#
# · **Las de disparador**. Postgres se niega a invocarlas directamente
#   («trigger functions can only be called as triggers»), así que tener
#   `execute` sobre ellas no es un privilegio que se pueda usar.
#
# Lo que queda es exactamente la superficie escrita por nosotros, que es lo
# que esta prueba tiene que vigilar.
FUNCIONES_EJECUTABLES = """
    select p.proname, pg_get_function_identity_arguments(p.oid) as args
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.prokind = 'f'
       and p.prorettype <> 'pg_catalog.trigger'::regtype
       and not exists (select 1 from pg_depend d
                        where d.objid = p.oid and d.deptype = 'e')
       and has_function_privilege('coloquio_app', p.oid, 'EXECUTE')
     order by p.proname
"""

# Lo que tiene que estar negado, dicho por su nombre. La lista blanca de
# arriba ya lo cubre por omisión; esto es para que el informe diga «persona:
# negado» en vez de solo «no sobra nada».
NEGADAS_EXPLICITAS = (
    "persona", "consentimiento", "participacion", "membresia", "encuesta",
    "reidentificacion", "borrado_pendiente", "inscripcion",
    "verificacion_contacto", "persona_atributo",
)


def conectar(dsn):
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


# ════════════════════════════════════════════════════════════════════
#  El escenario
# ════════════════════════════════════════════════════════════════════

class Escenario:
    """Una persona convocable, una sin consentimiento, y una encuesta abierta.

    Se arma con la conexión del dueño y se borra al final, pase lo que pase.
    Las filas llevan una marca en el documento para que, si una corrida se
    interrumpe, se sepa de dónde salieron y se puedan barrer a mano.
    """

    MARCA = "VERIF-COLOQUIO"

    def __init__(self, conn_dueno):
        self.conn = conn_dueno
        self.convocable = None
        self.sin_consentimiento = None
        self.panel_id = None

    def __enter__(self):
        marca = f"{self.MARCA}-{uuid.uuid4().hex[:8]}"
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into panel (nombre) values (%s) returning id",
                (f"{marca} · panel de verificación",))
            self.panel_id = cur.fetchone()["id"]

            # El texto de consentimiento es precondición desde R5.7.d: sin una
            # versión activa no se puede otorgar la finalidad. Que el script
            # tenga que publicarla no es un rodeo, es la regla funcionando.
            cur.execute(
                """insert into texto_consentimiento (finalidad, version, cuerpo)
                   values ('contacto_participacion', %s,
                           'Texto de la verificación de la superficie externa.')
                   on conflict (finalidad, version) do nothing""",
                (marca,))

            self.convocable = self._persona(cur, f"{marca}-con", "099000111")
            cur.execute(
                """insert into consentimiento
                          (id_persona, finalidad, estado, version_texto)
                   values (%s, 'contacto_participacion', 'vigente', %s)""",
                (self.convocable, marca))

            # La segunda persona existe y está activa, pero nunca consintió.
            # Es contra ella que se prueba el gate: usar a la primera para eso
            # sería probar nada.
            self.sin_consentimiento = self._persona(cur, f"{marca}-sin", "099000222")

            cur.execute(
                """insert into encuesta (panel_id, nombre, estado)
                   values (%s, %s, 'en_campo') returning id""",
                (self.panel_id, f"{marca} · encuesta"))
            encuesta_id = cur.fetchone()["id"]
            # Las dos quedan convocadas: sin convocatoria activa la función de
            # contacto rechaza por otro motivo, y el chequeo del gate diría
            # que pasó cuando en realidad falló por otra cosa.
            for quien in (self.convocable, self.sin_consentimiento):
                cur.execute(
                    """insert into participacion (encuesta_id, id_persona)
                       values (%s, %s)""", (encuesta_id, quien))
                cur.execute(
                    """insert into membresia (id_persona, panel_id)
                       values (%s, %s) on conflict do nothing""",
                    (quien, self.panel_id))
        return self

    def _persona(self, cur, documento, celular):
        cur.execute(
            """insert into persona (documento, nombre, celular, email, estado)
               values (%s, 'Persona de verificación', %s, %s, 'activa')
               returning id_persona""",
            (documento, f"+598{celular}", f"{documento}@ejemplo.invalid"))
        return cur.fetchone()["id_persona"]

    def __exit__(self, *_):
        with self.conn.cursor() as cur:
            for quien in (self.convocable, self.sin_consentimiento):
                if quien:
                    # `borrado_pendiente` no cascadea: no tiene FK a `persona`
                    # a propósito, porque la baja borra la persona y el
                    # pendiente tiene que sobrevivirla.
                    cur.execute(
                        "delete from borrado_pendiente where id_persona = %s",
                        (quien,))
                    cur.execute(
                        "delete from reidentificacion where id_persona = %s",
                        (quien,))
                    cur.execute("delete from persona where id_persona = %s",
                                (quien,))
            if self.panel_id:
                cur.execute("delete from encuesta where panel_id = %s",
                            (self.panel_id,))
                cur.execute("delete from panel where id = %s", (self.panel_id,))
            cur.execute("delete from texto_consentimiento where version like %s",
                        (f"{self.MARCA}-%",))
        return False


# ════════════════════════════════════════════════════════════════════
#  Los chequeos
# ════════════════════════════════════════════════════════════════════

class Falla(AssertionError):
    pass


def _niega(conn, sql, parametros=None):
    """Corre algo que **tiene** que fallar y devuelve el error.

    La conexión es autocommit, así que un error no deja la sesión abortada y
    el chequeo siguiente puede correr sin un rollback de por medio.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(sql, parametros or ())
            filas = cur.fetchall() if cur.description else None
    except Exception as error:  # noqa: BLE001
        return str(error).strip().splitlines()[0]
    raise Falla(f"no falló: devolvió {filas!r}")


# ── Privilegios efectivos ────────────────────────────────────────────

def privilegios_de_relaciones(coloquio, dueno):
    """Ninguna relación fuera de la lista blanca, y ninguna con más de lo suyo."""
    with dueno.cursor() as cur:
        cur.execute(
            """
            select c.relname, p.privilegio
              from pg_class c
              join pg_namespace n on n.oid = c.relnamespace
              cross join lateral (values
                  ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'),
                  ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')) as p(privilegio)
             where n.nspname = 'public'
               and c.relkind in ('r', 'v', 'm', 'p', 'f')
               and has_table_privilege('coloquio_app', c.oid, p.privilegio)
             order by c.relname, p.privilegio
            """)
        efectivos = {}
        for fila in cur.fetchall():
            efectivos.setdefault(fila["relname"], set()).add(fila["privilegio"])

    de_mas = []
    for relacion, privilegios in sorted(efectivos.items()):
        permitidos = RELACIONES_PERMITIDAS.get(relacion, set())
        sobran = privilegios - permitidos
        if sobran:
            de_mas.append(f"{relacion}: {', '.join(sorted(sobran))}")
    if de_mas:
        raise Falla("privilegios fuera de la lista blanca — " + "; ".join(de_mas))

    faltan = [r for r in RELACIONES_PERMITIDAS if r not in efectivos]
    if faltan:
        raise Falla(
            "la lista blanca promete lectura que el rol no tiene: "
            + ", ".join(sorted(faltan))
            + " (la superficie del contrato está rota, no de más)")
    return f"{len(efectivos)} relaciones legibles, todas en la lista"


def privilegios_de_funciones(coloquio, dueno):
    """Ninguna función ejecutable fuera de la lista blanca.

    Es el chequeo que hubiera atajado el `execute` a `public`: una función
    nace con `execute` otorgado a todo el mundo, así que «no le otorgamos
    nada» y «no puede ejecutarla» son dos cosas distintas.
    """
    with dueno.cursor() as cur:
        cur.execute(FUNCIONES_EJECUTABLES)
        ejecutables = cur.fetchall()

    de_mas = sorted({f["proname"] for f in ejecutables} - FUNCIONES_PERMITIDAS)
    if de_mas:
        raise Falla("funciones ejecutables fuera de la lista blanca: "
                    + ", ".join(de_mas))
    return f"{len(ejecutables)} funciones ejecutables, todas en la lista"


def sin_escritura_en_ninguna_tabla(coloquio, dueno):
    for relacion in NEGADAS_EXPLICITAS:
        with dueno.cursor() as cur:
            cur.execute(
                """select bool_or(has_table_privilege('coloquio_app', %s, p))
                     from unnest(array['SELECT','INSERT','UPDATE','DELETE']) p""",
                (relacion,))
            if cur.fetchone()["bool_or"]:
                raise Falla(f"`coloquio_app` tiene algún privilegio sobre "
                            f"`{relacion}`, y no debería tener ninguno")
    return f"{len(NEGADAS_EXPLICITAS)} tablas sensibles, todas negadas"


# ── Lo que el consumidor sí puede hacer ──────────────────────────────

def se_identifica(coloquio, dueno):
    with coloquio.cursor() as cur:
        cur.execute("select sistema_de_la_conexion() as s, session_user as u")
        fila = cur.fetchone()
    if fila["s"] != "coloquio":
        raise Falla(f"la conexión dice ser «{fila['s']}» y no «coloquio»")
    return f"{fila['u']} → sistema «{fila['s']}»"


def lee_la_superficie(coloquio, dueno):
    leidas = {}
    with coloquio.cursor() as cur:
        for vista in RELACIONES_PERMITIDAS:
            cur.execute(f"select count(*) as n from {vista}")
            leidas[vista] = cur.fetchone()["n"]
    return ", ".join(f"{v}: {n}" for v, n in leidas.items())


def no_lee_las_tablas(coloquio, dueno):
    errores = []
    for tabla in ("persona", "consentimiento", "participacion"):
        errores.append(_niega(coloquio, f"select * from {tabla} limit 1"))
    if not all("permission denied" in e for e in errores):
        raise Falla(f"alguna negativa no fue por permisos: {errores}")
    return "persona, consentimiento y participacion: permission denied"


def no_resuelve_atributos_por_su_cuenta(coloquio, dueno):
    """`f_atributo_persona` es la resolución cruda, sin gate.

    Se la ve a través de `v_persona_convocable`, que solo devuelve gente con
    consentimiento. Directamente, no: devolvería la demografía de cualquiera.
    """
    error = _niega(coloquio, "select * from f_atributo_persona(now()) limit 1")
    if "permission denied" not in error:
        raise Falla(f"la negativa no fue por permisos: {error}")
    return "f_atributo_persona: permission denied"


def solo_ve_a_quien_consintio(coloquio, escenario):
    with coloquio.cursor() as cur:
        cur.execute("select 1 from v_persona_convocable where id_persona = %s",
                    (escenario.convocable,))
        if not cur.fetchone():
            raise Falla("quien consintió no aparece en `v_persona_convocable`")
        cur.execute("select 1 from v_persona_convocable where id_persona = %s",
                    (escenario.sin_consentimiento,))
        if cur.fetchone():
            raise Falla("quien no consintió aparece en `v_persona_convocable`")
    return "consintió: visible · no consintió: invisible"


def contacto_legitimo_queda_auditado(coloquio, escenario, dueno):
    with coloquio.cursor() as cur:
        cur.execute("select contacto_para_convocatoria(%s, 'celular') as dato",
                    (escenario.convocable,))
        dato = cur.fetchone()["dato"]
    if not dato:
        raise Falla("no devolvió el celular")
    with dueno.cursor() as cur:
        cur.execute(
            """select sistema, actor_uid, contexto
                 from reidentificacion
                where id_persona = %s order by id desc limit 1""",
            (escenario.convocable,))
        fila = cur.fetchone()
    if not fila:
        raise Falla("entregó el dato y no dejó rastro en `reidentificacion`")
    if fila["sistema"] != "coloquio":
        raise Falla(f"lo auditó como «{fila['sistema']}» y no como «coloquio»")
    if fila["actor_uid"] != "coloquio_app":
        raise Falla(f"el actor quedó como «{fila['actor_uid']}»: adentro de un "
                    "`security definer` hay que usar `session_user`")
    return f"{dato} · auditado como {fila['sistema']}/{fila['actor_uid']}"


def contacto_sin_consentimiento_es_rechazado(coloquio, escenario, dueno):
    with dueno.cursor() as cur:
        cur.execute("select count(*) as n from reidentificacion where id_persona = %s",
                    (escenario.sin_consentimiento,))
        antes = cur.fetchone()["n"]
    error = _niega(coloquio, "select contacto_para_convocatoria(%s, 'celular')",
                   (escenario.sin_consentimiento,))
    if "consentimiento vigente" not in error:
        raise Falla(f"rechazó, pero por otro motivo: {error}")
    with dueno.cursor() as cur:
        cur.execute("select count(*) as n from reidentificacion where id_persona = %s",
                    (escenario.sin_consentimiento,))
        if cur.fetchone()["n"] != antes:
            raise Falla("un rechazo dejó fila de auditoría: no se entregó nada, "
                        "no hay reidentificación que registrar")
    return "rechazado por el gate, y sin fila de auditoría"


def un_canal_por_vez(coloquio, escenario):
    error = _niega(coloquio, "select contacto_para_convocatoria(%s, 'observaciones')",
                   (escenario.convocable,))
    if "Canal desconocido" not in error:
        raise Falla(f"aceptó un canal que no es de contacto: {error}")
    return "solo `email` o `celular`"


def la_cascada_llega_y_se_cierra(coloquio, escenario, dueno):
    with dueno.cursor() as cur:
        # COLOQUIO entra al registro inactivo: se activa el día que sale a
        # producción. Para verificar la cascada hay que activarlo, y se lo
        # deja como estaba.
        cur.execute("select activo from sistema_consumidor where codigo='coloquio'")
        estaba = cur.fetchone()["activo"]
        cur.execute("update sistema_consumidor set activo = true "
                    " where codigo = 'coloquio'")
    try:
        with dueno.cursor() as cur:
            cur.execute("select generar_borrados_pendientes(%s) as n",
                        (escenario.convocable,))
            cuantos = cur.fetchone()["n"]
        if cuantos < 2:
            raise Falla(f"una baja generó {cuantos} pendiente(s): los dos "
                        "sistemas registrados y activos tienen que recibirla")

        with coloquio.cursor() as cur:
            cur.execute("select id_persona from mis_borrados_pendientes()")
            mios = [f["id_persona"] for f in cur.fetchall()]
        if escenario.convocable not in mios:
            raise Falla("el pendiente no le llegó a COLOQUIO")

        with coloquio.cursor() as cur:
            cur.execute("select confirmar_borrado(%s)", (escenario.convocable,))
            cur.execute("select count(*) as n from mis_borrados_pendientes()")
            if cur.fetchone()["n"]:
                raise Falla("confirmó y el pendiente sigue abierto")

        # Y el de `paneles` sigue abierto: confirmar por uno no confirma por
        # todos, que es lo único que hace honesta a la cascada.
        with dueno.cursor() as cur:
            cur.execute(
                """select count(*) as n from borrado_pendiente
                    where id_persona = %s and sistema = 'paneles'
                      and confirmado_en is null""",
                (escenario.convocable,))
            if not cur.fetchone()["n"]:
                raise Falla("confirmar por COLOQUIO cerró también el de `paneles`")
        return "pendiente recibido, confirmado, y el de `paneles` sigue abierto"
    finally:
        with dueno.cursor() as cur:
            cur.execute("update sistema_consumidor set activo = %s "
                        " where codigo = 'coloquio'", (estaba,))


def no_confirma_por_otro(coloquio, escenario, dueno):
    with dueno.cursor() as cur:
        cur.execute(
            """insert into borrado_pendiente (id_persona, sistema, alcance)
               values (%s, 'paneles', 'ajeno') on conflict do nothing""",
            (escenario.sin_consentimiento,))
    error = _niega(coloquio, "select confirmar_borrado(%s, 'ajeno')",
                   (escenario.sin_consentimiento,))
    if "No hay un borrado pendiente" not in error:
        raise Falla(f"falló por otro motivo: {error}")
    with dueno.cursor() as cur:
        cur.execute(
            """select confirmado_en from borrado_pendiente
                where id_persona = %s and sistema='paneles' and alcance='ajeno'""",
            (escenario.sin_consentimiento,))
        if cur.fetchone()["confirmado_en"] is not None:
            raise Falla("cerró el pendiente de otro sistema")
    return "un consumidor no cierra el pendiente de otro"


# ── El intruso ───────────────────────────────────────────────────────

def un_rol_sin_registrar_no_consigue_nada(dsn_intruso, escenario):
    """El chequeo que encontró el peor defecto de la fase.

    Un rol que se conecta y no está en `sistema_consumidor` tiene que quedar
    afuera. Antes no quedaba: `sistema_de_la_conexion()` caía en `'paneles'`
    por omisión, y como adentro de un `security definer` `current_user` es el
    dueño de la función, el intruso se llevaba el contacto **firmado como
    `paneles`**. Era lavado de origen, no una fuga menor.
    """
    intruso = conectar(dsn_intruso)
    try:
        error = _niega(intruso, "select contacto_para_convocatoria(%s, 'celular')",
                       (escenario.convocable,))
        if "permission denied" not in error and "no está en" not in error:
            raise Falla(f"el intruso llegó más lejos de lo que debía: {error}")
        return f"rechazado: {error[:70]}"
    finally:
        intruso.close()


# ════════════════════════════════════════════════════════════════════
#  La batería
# ════════════════════════════════════════════════════════════════════

# Sin escenario: no escriben nada y valen contra producción.
SIN_DATOS = (
    ("privilegios efectivos sobre relaciones", privilegios_de_relaciones),
    ("privilegios efectivos sobre funciones", privilegios_de_funciones),
    ("tablas sensibles negadas", sin_escritura_en_ninguna_tabla),
    ("la conexión se identifica sola", se_identifica),
    ("lee la superficie del contrato", lee_la_superficie),
    ("no lee las tablas", no_lee_las_tablas),
    ("no resuelve atributos por su cuenta", no_resuelve_atributos_por_su_cuenta),
)

# Con escenario: necesitan datos de prueba, así que escriben.
CON_DATOS = (
    ("solo ve a quien consintió", solo_ve_a_quien_consintio),
    ("el contacto legítimo queda auditado", contacto_legitimo_queda_auditado),
    ("el contacto sin consentimiento se rechaza",
     contacto_sin_consentimiento_es_rechazado),
    ("un canal por vez", un_canal_por_vez),
    ("la cascada de baja llega y se cierra", la_cascada_llega_y_se_cierra),
    ("no confirma el borrado de otro", no_confirma_por_otro),
    ("un rol sin registrar no consigue nada", un_rol_sin_registrar_no_consigue_nada),
)


def correr(dsn_dueno, dsn_coloquio, dsn_intruso=None, solo_lectura=False,
           imprimir=print):
    """Corre la batería y devuelve la lista de (nombre, ok, detalle)."""
    resultados = []
    dueno = conectar(dsn_dueno)
    coloquio = conectar(dsn_coloquio)

    def registrar(nombre, funcion, *argumentos):
        try:
            detalle = funcion(*argumentos)
            resultados.append((nombre, True, detalle))
            imprimir(f"  {VERDE}✓{FIN} {nombre}  {GRIS}{detalle}{FIN}")
        except Exception as error:  # noqa: BLE001
            resultados.append((nombre, False, str(error)))
            imprimir(f"  {ROJO}✗{FIN} {nombre}\n      {ROJO}{error}{FIN}")

    try:
        for nombre, funcion in SIN_DATOS:
            registrar(nombre, funcion, coloquio, dueno)

        if solo_lectura:
            imprimir(f"\n  {AMARILLO}--solo-lectura: se saltean "
                     f"{len(CON_DATOS)} chequeos que necesitan escenario.{FIN}")
            return resultados

        with Escenario(dueno) as escenario:
            for nombre, funcion in CON_DATOS:
                if funcion is un_rol_sin_registrar_no_consigue_nada:
                    if not dsn_intruso:
                        imprimir(f"  {AMARILLO}·{FIN} {nombre}  {GRIS}"
                                 f"sin DSN_BOVEDA_INTRUSO{FIN}")
                        continue
                    registrar(nombre, funcion, dsn_intruso, escenario)
                elif funcion in (solo_ve_a_quien_consintio, un_canal_por_vez):
                    registrar(nombre, funcion, coloquio, escenario)
                else:
                    registrar(nombre, funcion, coloquio, escenario, dueno)
        return resultados
    finally:
        coloquio.close()
        dueno.close()


def _dsn_con_usuario(dsn, usuario):
    """Reemplaza el usuario de un DSN. Solo para el cluster local, que es
    `trust`: contra Cloud SQL el DSN del consumidor se pasa entero."""
    import re

    if "://" not in dsn:
        return None
    esquema, resto = dsn.split("://", 1)
    resto = re.sub(r"^[^@/]*@", "", resto)
    return f"{esquema}://{usuario}@{resto}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--solo-lectura", action="store_true",
        help="no arma escenario ni escribe nada; para apuntarlo a producción")
    argumentos = parser.parse_args()

    dsn_dueno = os.environ.get("DSN_BOVEDA", "").strip()
    if not dsn_dueno:
        print(f"{ROJO}Falta DSN_BOVEDA en el entorno.{FIN}")
        print("  source scripts/pg_pruebas.sh   # cluster de pruebas")
        return 2

    dsn_coloquio = os.environ.get("DSN_BOVEDA_COLOQUIO", "").strip()
    derivado = False
    if not dsn_coloquio:
        dsn_coloquio = _dsn_con_usuario(dsn_dueno, "coloquio_app")
        derivado = True
    dsn_intruso = os.environ.get("DSN_BOVEDA_INTRUSO", "").strip() or (
        _dsn_con_usuario(dsn_dueno, "intruso"))

    try:
        import psycopg  # noqa: F401
    except ModuleNotFoundError:
        print(f"{ROJO}Falta el driver de Postgres.{FIN}\n  pip install "
              '"psycopg[binary]"')
        return 2

    print("La bóveda, vista desde COLOQUIO")
    if derivado:
        print(f"  {GRIS}DSN del consumidor derivado del de la bóveda; contra "
              f"Cloud SQL pasar DSN_BOVEDA_COLOQUIO entero.{FIN}")
    print()

    try:
        resultados = correr(dsn_dueno, dsn_coloquio, dsn_intruso,
                            solo_lectura=argumentos.solo_lectura)
    except Exception as error:  # noqa: BLE001
        print(f"\n  {ROJO}no se pudo correr la batería:{FIN} {error}")
        if "coloquio_app" in str(error):
            print(f"  {AMARILLO}El rol no existe o no puede conectarse. En el "
                  f"cluster de pruebas lo crea `scripts/pg_pruebas.sh`.{FIN}")
        return 2

    fallaron = [r for r in resultados if not r[1]]
    if fallaron:
        print(f"\n{ROJO}Fallaron {len(fallaron)} de {len(resultados)}.{FIN} "
              "La bóveda no está lista para COLOQUIO.")
        return 1
    print(f"\n{VERDE}Pasaron los {len(resultados)} chequeos.{FIN} "
          "La bóveda se defiende sola.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
