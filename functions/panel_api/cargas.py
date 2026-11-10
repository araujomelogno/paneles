"""R3.13 — Incorporar individuos con sus respuestas, sin panel.

La única forma de cargar individuos con sus respuestas era desde una
encuesta, y toda encuesta pertenece a un panel: dar de alta gente implicaba
necesariamente meterla en un panel, con lo cual aparecía en convocatorias, en
la composición y en el muestreo. Eso bloquea un caso real —un ómnibus, un
estudio de terceros, una base histórica— donde esa gente **no es panelista**:
no fue reclutada, no va a ser convocada, y meterla en un panel distorsionaría
todos sus indicadores. Pero sus respuestas sí interesa poder consultarlas por
concepto.

Este módulo separa las dos cosas que venían pegadas: **incorporar individuos
y sus respuestas** por un lado, y **hacerlos miembros de un panel** por otro.

Una `carga` cumple frente al store semántico exactamente el mismo papel que
una `encuesta`: agrupa el cuestionario, sus preguntas y sus respuestas bajo un
`ref_estudio`. La diferencia es lo que **no** hace: no crea membresías ni
participaciones.

Todo lo demás se reutiliza tal cual —el mapeo de variables a preguntas, el
marcado de demográficos (R3.9.d), el dedup de identidad (R1.2), el tipo de
identificador (R3.12.b), el gate de `uso_semantico` y el guardrail de PII
(R1.6)—. Lo que cambia es el contexto, no el pipeline.

**Desde D81 (`boveda/0032`) toda ingesta es una carga de una encuesta.** La
encuesta es el estudio —nombre, fecha, público objetivo, clientes,
temáticas, audiencia—; la carga es un archivo: cuándo, quién, modo de
relevamiento, tipo de datos, su huella y el origen creado/reutilizado de cada
persona. Una carga desde Panelistas crea su encuesta (independiente,
cerrada, sin audiencia); una desde Encuestas se suma a la existente. Todas
las cargas de una encuesta escriben en el **mismo** cuestionario (el
`ref_estudio` de la carga es el de su encuesta, y lo garantiza la base), así
que una persona tiene una sola respuesta por pregunta y gana la última carga
(R-EU.3). Cada carga registra la participación de sus personas en la
encuesta (`respondio`), marcando a quien no estaba convocado (R-EU.4).
"""

from . import db, encuestas, ingesta as mod_ingesta, personas, sav, semantica
from .errores import DatosInvalidos, NoEncontrado

# La finalidad que una fila tiene que evidenciar para que se cree la persona.
# **Es la inversa de la ingesta desde encuesta**, y a propósito: allá la
# obligatoria es `contacto_participacion` porque esa persona es un panelista
# al que se va a seguir convocando; acá son personas que no se van a contactar
# y cuyos datos se incorporan para análisis. Exigir consentimiento de contacto
# sería pedir base legal para algo que no se va a hacer, y no exigir el de uso
# semántico dejaría sin base lo único que sí se va a hacer.
FINALIDAD_OBLIGATORIA = sav.SEMANTICO


# Lo que se lee de una carga: lo suyo y, por `v_carga_resumen`, los datos del
# estudio de su encuesta (la fecha y el público objetivo son de la encuesta).
_COLUMNAS = ("id, nombre, descripcion, ref_estudio, encuesta_id, encuesta, "
             "encuesta_tipo, fecha_estudio, publico_objetivo, archivo_huella, "
             "tipo_datos_origen, tipo_datos_origen_otro, "
             "modo_relevamiento, modo_relevamiento_otro, creado_en, creado_por")

# boveda/0026 — qué tipo de datos dio origen a la carga y cómo se relevaron.
# Vocabulario cerrado con una salida «otro» que obliga a especificar. Las
# claves son las del `check` de la migración; las etiquetas, lo que muestran
# la pantalla y las fichas. La pantalla las recibe de `opciones()` y no las
# repite: dos listas iguales en dos lugares terminan siendo distintas.
OTRO = "otro"
TIPOS_DATOS_ORIGEN = (
    ("encuesta", "Encuesta"),
    ("reclutamiento", "Reclutamiento"),
    (OTRO, "Otro"),
)
MODOS_RELEVAMIENTO = (
    ("cara_a_cara", "Cara a cara"),
    ("telefonico", "Telefónico"),
    ("whatsapp", "WhatsApp"),
    ("web", "Web"),
    (OTRO, "Otro"),
)
# campo → (vocabulario, cómo se nombra en un mensaje)
_CLASIFICACIONES = {
    "tipo_datos_origen": (TIPOS_DATOS_ORIGEN, "el tipo de datos de origen"),
    "modo_relevamiento": (MODOS_RELEVAMIENTO, "el modo de relevamiento"),
}

# R-ORG.1 — los dos orígenes del vínculo persona ↔ carga.
CREADA = "creada"
REUTILIZADA = "reutilizada"
# D94 · R-INC.1 — la persona ya estaba en una carga **anterior de esta misma
# encuesta**: una encuesta de Alchemer se carga varias veces y cada SAV trae
# el acumulado. No la decide quien llama: la decide `registrar_vinculos`.
REPETIDA = "repetida"
ORIGENES = (CREADA, REUTILIZADA, REPETIDA)

# R-ORG.5 — la clave con la que el estudio de origen se ofrece como columna
# de los resultados (R7.4). No es un atributo del catálogo: no se puede
# filtrar ni fijar cuota con ella, solo mirarla.
COLUMNA_ESTUDIO_DE_ORIGEN = "estudio_de_origen"
# boveda/0026 — el tipo y el modo de la carga, como columnas de la misma
# naturaleza: salen del vínculo persona ↔ carga, no de la persona.
COLUMNA_TIPO_DATOS_ORIGEN = "tipo_datos_origen"
COLUMNA_MODO_RELEVAMIENTO = "modo_relevamiento"
COLUMNAS_DE_PROCEDENCIA = (
    (COLUMNA_ESTUDIO_DE_ORIGEN, "Estudio de origen"),
    (COLUMNA_TIPO_DATOS_ORIGEN, "Tipo de datos de origen"),
    (COLUMNA_MODO_RELEVAMIENTO, "Modo de relevamiento"),
)

# R-ORG §7 — lo que la ficha dice de una persona sin vínculo. No es «sin
# origen»: el vínculo existe desde esta versión y lo cargado antes no dejó
# constancia de qué carga fue.
SIN_VINCULO_REGISTRADO = (
    "No hay registro de la carga de la que proviene. El vínculo entre "
    "personas y cargas se registra desde la versión R-ORG; lo que se cargó "
    "antes no dejó constancia, y no se reconstruye para no inventar un "
    "origen."
)


def _serializar(fila):
    salida = {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "descripcion": fila["descripcion"],
        "ref_estudio": str(fila["ref_estudio"]),
        "encuesta_id": fila.get("encuesta_id"),
        "encuesta": fila.get("encuesta"),
        "encuesta_tipo": fila.get("encuesta_tipo"),
        "archivo_huella": fila.get("archivo_huella"),
        "fecha_estudio": (fila["fecha_estudio"].isoformat()
                          if fila.get("fecha_estudio") else None),
        "publico_objetivo": fila.get("publico_objetivo"),
        **datos_de_origen(fila),
        "creado_en": fila["creado_en"].isoformat(),
        "creado_por": fila["creado_por"],
    }
    for clave in ("personas_creadas", "personas_reutilizadas", "personas",
                  "personas_repetidas", "provisoria"):
        if clave in fila:
            salida[clave] = fila[clave]
    if "personas_repetidas" in fila:
        salida["resumen_personas"] = resumen_de_personas(fila)
    return salida


def resumen_de_personas(fila):
    """D94 · R-INC.5 — «487 en el archivo · 120 nuevos · 15 reutilizados de
    otros estudios · 352 ya estaban en cargas anteriores de esta encuesta».
    Los repetidos no son reutilizados: no vienen de otro estudio."""
    partes = [f"{fila['personas']} en el archivo",
              f"{fila['personas_creadas']} nuevos",
              f"{fila['personas_reutilizadas']} reutilizados de otros estudios"]
    if fila.get("personas_repetidas"):
        partes.append(f"{fila['personas_repetidas']} ya estaban en cargas "
                      f"anteriores de esta encuesta")
    return " · ".join(partes)


def _fecha(crudo):
    """`fecha_estudio` como `date`, o None. Texto ISO (`2026-08-15`)."""
    import datetime

    if crudo in (None, ""):
        return None
    if isinstance(crudo, datetime.date):
        return crudo
    try:
        return datetime.date.fromisoformat(str(crudo).strip()[:10])
    except ValueError:
        raise DatosInvalidos(
            f"La fecha del estudio no es una fecha válida: {crudo!r}. Usá el "
            f"formato AAAA-MM-DD.") from None


def _texto(crudo):
    return (str(crudo).strip() or None) if crudo is not None else None


def opciones():
    """Los dos vocabularios, para que la pantalla arme los selectores."""
    return {
        campo: [{"clave": clave, "etiqueta": etiqueta}
                for clave, etiqueta in vocabulario]
        for campo, (vocabulario, _) in _CLASIFICACIONES.items()
    }


def etiqueta_de(campo, valor, especificacion=None):
    """«Telefónico», o «Otro: panel online» cuando es «otro». None sin dato."""
    if not valor:
        return None
    vocabulario, _ = _CLASIFICACIONES[campo]
    etiqueta = dict(vocabulario).get(valor, valor)
    if valor == OTRO and especificacion:
        return f"{etiqueta}: {especificacion}"
    return etiqueta


def datos_de_origen(fila):
    """Las cuatro columnas de la 0026 y su etiqueta, para cualquier salida."""
    salida = {}
    for campo in _CLASIFICACIONES:
        valor, otro = fila.get(campo), fila.get(f"{campo}_otro")
        salida[campo] = valor
        salida[f"{campo}_otro"] = otro
        salida[f"{campo}_etiqueta"] = etiqueta_de(campo, valor, otro)
    return salida


def _clasificacion(campo, valor, especificacion, exigir):
    """Valida un dato de la 0026 y devuelve `(valor, especificación)`.

    Fuera del vocabulario se rechaza diciendo cuáles valen; «otro» exige
    especificar, y cualquier otro valor descarta la especificación (la base
    no admite una colgada de un valor que no es «otro»).
    """
    vocabulario, nombre = _CLASIFICACIONES[campo]
    valor = (str(valor).strip().lower() or None) if valor is not None else None
    if valor is None:
        if exigir:
            raise DatosInvalidos(
                f"Falta indicar {nombre} de la carga.", {"campo": campo})
        return None, None
    claves = [clave for clave, _ in vocabulario]
    if valor not in claves:
        raise DatosInvalidos(
            f"{nombre[0].upper()}{nombre[1:]} no es válido: {valor!r}. Puede "
            f"ser {', '.join(claves)}.", {"campo": campo})
    if valor != OTRO:
        return valor, None
    especificacion = _texto(especificacion)
    if not especificacion:
        raise DatosInvalidos(
            f"Elegiste «Otro» para {nombre}: especificá cuál.",
            {"campo": f"{campo}_otro"})
    return valor, especificacion


def crear(conn, nombre, descripcion=None, actor=None, fecha_estudio=None,
          publico_objetivo=None, tipo_datos_origen=None,
          tipo_datos_origen_otro=None, modo_relevamiento=None,
          modo_relevamiento_otro=None, exigir_origen=False, clientes=None,
          encuesta_id=None, archivo_huella=None, url_alchemer=None,
          provisoria=False):
    """Abre una carga: una ingesta de una encuesta (R-EU.1).

    D92 · R-CAN-C.1 — con `provisoria` (lo que pasa la pantalla: la carga se
    crea en el primer paso, antes de subir el archivo) la carga queda
    provisoria hasta que se confirma su ingesta, y la encuesta automática
    también. Una encuesta **existente** no se vuelve provisoria: solo la
    carga.

    Con `encuesta_id` la carga se suma a ese estudio y **no vuelve a pedir**
    sus datos (R-EU.2): fecha, público objetivo y clientes ya están en la
    encuesta, y los que vengan acá se ignoran.

    Sin `encuesta_id` —una carga desde la sección Panelistas— **crea su
    encuesta**: independiente, cerrada, sin convocatoria, con los datos del
    estudio (nombre, fecha, público objetivo, descripción, clientes y, desde
    D90, la URL de Alchemer: los mismos que pide «Nueva encuesta», R-AU.3).

    Lo propio de la carga es lo del archivo: el tipo de datos de origen y el
    modo de relevamiento (`exigir_origen` lo pasa la ruta: una carga nueva
    abierta desde la pantalla los declara; las anteriores a la 0026 no los
    tienen). El `ref_estudio` es el de la encuesta, y lo pone la base.
    """
    from . import encuestas

    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosInvalidos(
            "La carga necesita un nombre: es lo que después identifica de "
            "dónde salieron esos datos (por ejemplo «Ómnibus agosto 2026»)."
        )
    tipo = _clasificacion("tipo_datos_origen", tipo_datos_origen,
                          tipo_datos_origen_otro, exigir_origen)
    modo = _clasificacion("modo_relevamiento", modo_relevamiento,
                          modo_relevamiento_otro, exigir_origen)
    if encuesta_id is None:
        encuesta_id = encuestas.crear(
            conn, None, nombre, fecha_campo=_fecha(fecha_estudio),
            tipo=encuestas.INDEPENDIENTE, origen=encuestas.ORIGEN_CARGA,
            publico_objetivo=publico_objetivo,
            descripcion=descripcion, clientes=clientes or [],
            estado="cerrada", url_alchemer=url_alchemer)["id"]
        if provisoria:
            db.ejecutar(conn, "update encuesta set provisoria = true where id = %s",
                        (encuesta_id,))
    else:
        encuestas.obtener(conn, encuesta_id)
    fila = db.una(
        conn,
        """
        insert into carga (nombre, descripcion, encuesta_id, archivo_huella,
                           tipo_datos_origen, tipo_datos_origen_otro,
                           modo_relevamiento, modo_relevamiento_otro, creado_por,
                           provisoria)
             values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
          returning id
        """,
        (nombre, (descripcion or "").strip() or None, encuesta_id, archivo_huella,
         *tipo, *modo,
         getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None),
         bool(provisoria)),
    )
    return obtener(conn, fila["id"])


def sincronizar_fecha(conn_boveda, conn_semantica, carga_id, solo_si_vacia=False):
    """Lleva la fecha del estudio al cuestionario semántico. Desde D81 la
    fecha es de la encuesta (`encuestas.sincronizar_fecha`)."""
    from . import encuestas

    return encuestas.sincronizar_fecha(
        conn_boveda, conn_semantica, _encuesta_de(conn_boveda, carga_id),
        solo_si_vacia=solo_si_vacia)


def completar_fechas(conn_boveda, conn_semantica):
    """Operación única: los estudios ingestados antes de la corrección
    quedaron con el cuestionario sin fecha. Copia la de la encuesta **solo
    donde esté vacía**; una fecha que ya tenga el cuestionario no se toca.

    Devuelve cuántos estudios con fecha se revisaron y cuáles se
    completaron. No hace `commit`: lo decide quien llama (el script).
    """
    from . import encuestas

    con_fecha = db.todas(
        conn_boveda,
        "select id, nombre from encuesta where fecha_campo is not null order by id")
    completadas = [
        {"id": e["id"], "nombre": e["nombre"]}
        for e in con_fecha
        if encuestas.sincronizar_fecha(conn_boveda, conn_semantica, e["id"],
                                       solo_si_vacia=True)
    ]
    return {"revisadas": len(con_fecha), "completadas": completadas}


def _encuesta_de(conn, carga_id):
    fila = db.una(conn, "select encuesta_id from carga where id = %s", (carga_id,))
    if not fila:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    return fila["encuesta_id"]


# R-EU.2 — lo que una edición de la carga cambia en su encuesta.
_DEL_ESTUDIO = ("fecha_estudio", "publico_objetivo", "clientes")


def editar(conn, carga_id, cambios, conn_semantica=None):
    """Corrige una carga. Lo del archivo (nombre, descripción, tipo de datos,
    modo de relevamiento) va a la carga; lo del estudio (fecha, público
    objetivo, clientes) a su encuesta, y se ve en todas sus cargas y fichas
    sin tocar a nadie.
    """
    from . import encuestas

    cambios = cambios or {}
    columnas, valores = [], []
    if "nombre" in cambios:
        nombre = (cambios.get("nombre") or "").strip()
        if not nombre:
            raise DatosInvalidos("La carga necesita un nombre.")
        columnas.append("nombre")
        valores.append(nombre)
    if "descripcion" in cambios:
        columnas.append("descripcion")
        valores.append(_texto(cambios.get("descripcion")))
    for campo in _CLASIFICACIONES:
        if f"{campo}_otro" in cambios and campo not in cambios:
            # La especificación sola no se puede validar: depende de si el
            # valor es «otro», y el valor viaja con ella.
            raise DatosInvalidos(
                f"Para cambiar {campo}_otro mandá también {campo}.",
                {"campo": campo})
        if campo in cambios:
            # Una vez declarado no se vacía: se corrige por otro valor.
            columnas += [campo, f"{campo}_otro"]
            valores += _clasificacion(campo, cambios.get(campo),
                                      cambios.get(f"{campo}_otro"), exigir=True)
    del_estudio = {k: cambios[k] for k in _DEL_ESTUDIO if k in cambios}
    if not columnas and not del_estudio:
        raise DatosInvalidos(
            "No hay nada que cambiar. Se pueden editar nombre, descripcion, "
            "fecha_estudio, publico_objetivo, tipo_datos_origen, "
            "modo_relevamiento y clientes.")
    encuesta_id = _encuesta_de(conn, carga_id)
    if columnas:
        db.ejecutar(
            conn,
            f"update carga set {', '.join(c + ' = %s' for c in columnas)} "
            f"where id = %s",
            tuple(valores) + (carga_id,))
    if del_estudio:
        # Una carga creada desde Panelistas es la única de su estudio y lleva
        # su nombre: renombrarla renombra el estudio, que es lo que se ve.
        if "nombre" in cambios and _unica_de_su_encuesta(conn, carga_id):
            del_estudio["nombre"] = cambios["nombre"]
        encuestas.editar(conn, encuesta_id, del_estudio,
                         conn_semantica=conn_semantica)
    elif "nombre" in cambios and _unica_de_su_encuesta(conn, carga_id):
        encuestas.editar(conn, encuesta_id, {"nombre": cambios["nombre"]})
    return obtener(conn, carga_id)


def _unica_de_su_encuesta(conn, carga_id):
    fila = db.una(
        conn,
        """select count(*)::int as n from carga
            where encuesta_id = (select encuesta_id from carga where id = %s)""",
        (carga_id,))
    return fila["n"] == 1


def obtener(conn, carga_id):
    """La carga con sus conteos, los datos de su estudio y las temáticas y
    clientes del estudio (R-TEM.1, R-CLI.1, D81)."""
    from . import clientes as mod_clientes, tematicas

    fila = db.una(
        conn,
        f"select {_COLUMNAS}, personas_creadas, personas_reutilizadas, personas, "
        f"personas_repetidas, provisoria "
        f"from v_carga_resumen where id = %s",
        (carga_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    return {**_serializar(fila),
            "tematicas": tematicas.de_encuesta(conn, fila["encuesta_id"]),
            "clientes": mod_clientes.de_encuesta(conn, fila["encuesta_id"])}


def listar(conn, busqueda=None, tematica_id=None, encuesta_id=None):
    """R-CAR.1 — las cargas, con sus conteos y las temáticas y clientes de su
    estudio.

    Ordenadas por **fecha del estudio**, la más reciente primero; la que no
    la declaró se ubica por la fecha en que se cargó. `busqueda` filtra por
    nombre (de la carga o del estudio); `tematica_id`, a las de estudios que
    cubren esa temática; `encuesta_id`, a las de un estudio.
    """
    from . import clientes as mod_clientes, tematicas

    # D92 · R-CAN-C.1 — una carga provisoria no aparece en ningún listado.
    condiciones, params = ["not r.provisoria"], []
    if tematica_id:
        condiciones.append(
            "exists (select 1 from encuesta_tematica et "
            "where et.encuesta_id = r.encuesta_id and et.tematica_id = %s)")
        params.append(tematica_id)
    if encuesta_id:
        condiciones.append("r.encuesta_id = %s")
        params.append(encuesta_id)
    donde = ("where " + " and ".join(condiciones)) if condiciones else ""
    filas = db.todas(
        conn,
        f"select {', '.join('r.' + c.strip() for c in _COLUMNAS.split(','))}, "
        f"r.personas_creadas, r.personas_reutilizadas, r.personas, "
        f"r.personas_repetidas "
        f"from v_carga_resumen r {donde} "
        f"order by coalesce(r.fecha_estudio, r.creado_en::date) desc, "
        f"r.creado_en desc",
        tuple(params),
    )
    # El nombre se busca acá y no con `ilike`: sin tildes ni mayúsculas
    # («omnibus» encuentra «Ómnibus»), y sin depender de la configuración
    # regional de la base, que en `C` no pliega «Ó». Son decenas de cargas.
    clave = tematicas.clave_de_busqueda(busqueda)
    if clave:
        filas = [f for f in filas
                 if clave in tematicas.clave_de_busqueda(f["nombre"])
                 or clave in tematicas.clave_de_busqueda(f["encuesta"])]
    estudios = [f["encuesta_id"] for f in filas]
    por_estudio = tematicas.de_encuestas(conn, estudios)
    clientes_de = mod_clientes.de_encuestas(conn, estudios)
    return [{**_serializar(f), "tematicas": por_estudio.get(f["encuesta_id"], []),
             "clientes": clientes_de.get(f["encuesta_id"], [])}
            for f in filas]


# ── R-ORG.1 · El vínculo persona ↔ carga ─────────────────────────────

def registrar_vinculos(conn, carga_id, ids_persona, origen):
    """Deja constancia de que esas personas vinieron de esa carga.

    Idempotente y **sin pisar**: si la persona ya estaba vinculada, el
    vínculo queda como estaba. Es lo que hace que el orden no importe —la
    ruta registra `creada` antes de encolar y cada lote registra
    `reutilizada` sobre todo lo que resolvió—: quien la carga creó sigue
    figurando como creada aunque un lote la vuelva a encontrar.
    """
    if origen not in (CREADA, REUTILIZADA):
        raise DatosInvalidos(f"Origen de vínculo desconocido: {origen!r}.")
    ids = [str(i) for i in dict.fromkeys(i for i in (ids_persona or []) if i)]
    if not ids or carga_id is None:
        return 0
    # D94 · R-INC.1 — quien ya tiene un vínculo con una carga **anterior** de
    # la misma encuesta queda `repetida`: no viene de otro estudio, viene del
    # acumulado. «Anterior» es por orden de creación de la carga, igual que
    # en la migración (0037) y en la ficha.
    insertados = db.ejecutar(
        conn,
        """
        insert into persona_carga (id_persona, carga_id, origen)
        select p.id_persona, esta.id,
               case when exists (
                      select 1 from persona_carga o
                        join carga oc on oc.id = o.carga_id
                       where o.id_persona = p.id_persona
                         and oc.encuesta_id = esta.encuesta_id
                         and (oc.creado_en, oc.id) < (esta.creado_en, esta.id))
                    then 'repetida' else %s end
          from persona p, carga esta
         where esta.id = %s and p.id_persona = any(%s::uuid[])
        on conflict (id_persona, carga_id) do nothing
        """,
        (origen, carga_id, ids),
    )
    # Y al revés: si esta carga es la primera de la persona en la encuesta
    # pero llegó después (una revisión que se resolvió tarde), las
    # posteriores pasan a `repetida`. Dentro de una encuesta, a lo sumo un
    # vínculo que no es repetida por persona.
    db.ejecutar(
        conn,
        """
        update persona_carga pc set origen = 'repetida'
          from carga oc, carga esta
         where esta.id = %s and oc.id = pc.carga_id
           and oc.encuesta_id = esta.encuesta_id
           and (oc.creado_en, oc.id) > (esta.creado_en, esta.id)
           and pc.origen <> 'repetida'
           and pc.id_persona = any(%s::uuid[])
           and exists (select 1 from persona_carga mia
                        where mia.carga_id = esta.id
                          and mia.id_persona = pc.id_persona
                          and mia.origen <> 'repetida')
        """,
        (carga_id, ids),
    )
    return insertados


def reasignar_primera_aparicion(conn, carga_id, ids_persona=None):
    """D94 · R-INC.3 — antes de quitar el vínculo de una carga que se
    borra: cada persona que tenía en ella su **primera** aparición en la
    encuesta y sigue en una carga **posterior** pasa a tener esa siguiente
    como primera, con origen `reutilizada` —la persona ya existía cuando
    entró esa carga—. Sin `ids_persona`, todas las de la carga.

    No decide nada más del borrado: «sin otra vida» sigue mirando **todos**
    los vínculos, incluidos los `repetida` (`cargas_borrado._clasificar`).
    """
    return db.ejecutar(
        conn,
        """
        with primeras as (
          select pc.id_persona, esta.encuesta_id
            from persona_carga pc join carga esta on esta.id = pc.carga_id
           where pc.carga_id = %s and pc.origen <> 'repetida'
             and (%s::uuid[] is null or pc.id_persona = any(%s::uuid[]))
        ),
        siguiente as (
          select distinct on (o.id_persona) o.id_persona, o.carga_id
            from primeras p
            join persona_carga o on o.id_persona = p.id_persona
            join carga oc on oc.id = o.carga_id
           where oc.encuesta_id = p.encuesta_id and o.carga_id <> %s
           order by o.id_persona, oc.creado_en, oc.id
        )
        update persona_carga pc set origen = 'reutilizada'
          from siguiente s
         where pc.id_persona = s.id_persona and pc.carga_id = s.carga_id
           and pc.origen = 'repetida'
        """,
        (carga_id, [str(i) for i in ids_persona] if ids_persona is not None else None,
         [str(i) for i in ids_persona] if ids_persona is not None else None,
         carga_id))


# ── D92 · R-CAN-C · La carga nace provisoria ─────────────────────────

# Cuánto se espera a una carga provisoria antes de darla por abandonada.
HORAS_PARA_ABANDONADA = 24
LIMPIEZA_AL_INICIAR = "inicio_de_carga"
LIMPIEZA_MANUAL = "manual"


def confirmar_provisoria(conn, carga_id):
    """La carga y su encuesta automática dejan de ser provisorias. No hace
    `commit`: lo llama `diferida.encolar` en la transacción que crea el
    trabajo de ingesta (R-CAN-C.1)."""
    db.ejecutar(conn, "update carga set provisoria = false "
                      " where id = %s and provisoria", (carga_id,))
    db.ejecutar(
        conn,
        """update encuesta set provisoria = false
            where provisoria
              and id = (select encuesta_id from carga where id = %s)""",
        (carga_id,))


def cancelar_provisoria(conn, carga_id):
    """R-CAN-C.2 — «Cancelar» o cerrar el modal antes de confirmar: borra la
    carga provisoria y, si la hubo, su encuesta automática provisoria.

    Seguro de repetir: si ya no existe, no falla. Una carga que **no** es
    provisoria —se confirmó su ingesta— no se toca: se borra con su borrado
    (R-EU.10), que deshace lo que trajo."""
    carga = db.una(conn, "select id, encuesta_id, provisoria from carga where id = %s",
                   (carga_id,))
    if not carga:
        return {"carga_id": carga_id, "borrada": False, "motivo": "no_existe",
                "encuesta_borrada": False}
    if not carga["provisoria"]:
        return {"carga_id": carga_id, "borrada": False, "motivo": "confirmada",
                "encuesta_borrada": False}
    db.ejecutar(conn, "delete from carga where id = %s and provisoria", (carga_id,))
    encuesta = _borrar_encuesta_provisoria_vacia(conn, carga["encuesta_id"])
    return {"carga_id": carga_id, "borrada": True, "motivo": None,
            "encuesta_borrada": encuesta}


def _borrar_encuesta_provisoria_vacia(conn, encuesta_id):
    return bool(db.ejecutar(
        conn,
        """delete from encuesta e
            where e.id = %s and e.provisoria
              and not exists (select 1 from carga c where c.encuesta_id = e.id)""",
        (encuesta_id,)))


def limpiar_provisorias(conn, disparada_por=LIMPIEZA_AL_INICIAR, actor=None,
                        horas=HORAS_PARA_ABANDONADA):
    """R-CAN-C.3 — borra las cargas provisorias con más de `horas` y **sin
    trabajo de ingesta**, junto con su encuesta automática provisoria.

    Cerrar la pestaña o perder la conexión no avisa al servidor, así que el
    cancelar explícito no alcanza. Corre al iniciar cada carga nueva —sin un
    proceso programado— y a mano desde Cumplimiento. Cada corrida deja su
    fila en `limpieza_provisoria`, también cuando no borró nada. No hace
    `commit`."""
    if disparada_por not in (LIMPIEZA_AL_INICIAR, LIMPIEZA_MANUAL):
        raise DatosInvalidos(f"Disparador de limpieza desconocido: {disparada_por!r}.")
    borradas = db.todas(
        conn,
        """delete from carga c
            where c.provisoria
              and c.creado_en < now() - make_interval(hours => %s)
              and not exists (select 1 from ingesta_trabajo t
                               where t.destino_tipo = 'carga' and t.destino_id = c.id)
        returning c.encuesta_id""",
        (horas,))
    # La encuesta automática de una de ellas, y cualquier encuesta provisoria
    # vieja que haya quedado sin cargas.
    encuestas = db.ejecutar(
        conn,
        """delete from encuesta e
            where e.provisoria
              and (e.id = any(%s) or e.creado_en < now() - make_interval(hours => %s))
              and not exists (select 1 from carga c where c.encuesta_id = e.id)""",
        (list({b["encuesta_id"] for b in borradas}), horas))
    actor_id = getattr(actor, "email", None) or getattr(actor, "uid", None) \
        or (actor if isinstance(actor, str) else None)
    fila = db.una(
        conn,
        """insert into limpieza_provisoria
                  (disparada_por, actor, cargas_eliminadas, encuestas_eliminadas)
           values (%s, %s, %s, %s)
        returning id, ejecutada_en""",
        (disparada_por, actor_id, len(borradas), encuestas))
    if borradas or encuestas:
        print(f"[cargas] limpieza de provisorias ({disparada_por}): "
              f"cargas={len(borradas)} encuestas={encuestas}", flush=True)
    return {"id": fila["id"], "ejecutada_en": fila["ejecutada_en"].isoformat(),
            "disparada_por": disparada_por, "cargas_eliminadas": len(borradas),
            "encuestas_eliminadas": encuestas, "horas": horas}


def limpiezas(conn, limite=20):
    """Las últimas corridas de la limpieza: cuántas borró y cuándo."""
    filas = db.todas(
        conn,
        """select id, ejecutada_en, disparada_por, actor, cargas_eliminadas,
                  encuestas_eliminadas
             from limpieza_provisoria order by ejecutada_en desc, id desc limit %s""",
        (limite,))
    pendientes = db.una(
        conn,
        """select count(*)::int as provisorias,
                  count(*) filter (where creado_en < now() - make_interval(hours => %s))::int
                    as abandonadas
             from carga where provisoria""", (HORAS_PARA_ABANDONADA,))
    return {"items": [{**f, "ejecutada_en": f["ejecutada_en"].isoformat()} for f in filas],
            "provisorias": pendientes["provisorias"],
            "abandonadas": pendientes["abandonadas"],
            "horas": HORAS_PARA_ABANDONADA}


def vincular_creacion(conn, carga_id, creacion):
    """Los vínculos que deja el alta por archivo: creados y reutilizados."""
    creacion = creacion or {}
    return {
        CREADA: registrar_vinculos(
            conn, carga_id,
            [c["id_persona"] for c in creacion.get("creados") or []], CREADA),
        REUTILIZADA: registrar_vinculos(
            conn, carga_id,
            [c["id_persona"] for c in creacion.get("reutilizados") or []],
            REUTILIZADA),
    }


def origen_de(conn, id_persona):
    """R-ORG.5 — los estudios de los que proviene una persona, por join.

    Con los datos del estudio tal como están **hoy** en la carga: corregir
    la fecha se ve acá sin tocar a la persona.
    """
    filas = db.todas(
        conn,
        """
        select c.id, c.nombre, e.fecha_campo as fecha_estudio, e.publico_objetivo,
               c.tipo_datos_origen, c.tipo_datos_origen_otro,
               c.modo_relevamiento, c.modo_relevamiento_otro,
               pc.origen, pc.creado_en
          from persona_carga pc
          join carga c on c.id = pc.carga_id
          join encuesta e on e.id = c.encuesta_id
         where pc.id_persona = %s
           -- D94 — un estudio una vez: su primera carga, no el acumulado.
           and pc.origen <> 'repetida'
         order by coalesce(e.fecha_campo, c.creado_en::date), c.id
        """,
        (str(id_persona),),
    )
    estudios = [
        {
            "carga_id": f["id"],
            "nombre": f["nombre"],
            "fecha_estudio": (f["fecha_estudio"].isoformat()
                              if f["fecha_estudio"] else None),
            "publico_objetivo": f["publico_objetivo"],
            **datos_de_origen(f),
            "origen": f["origen"],
            "vinculado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]
    return {
        "estudios": estudios,
        "aviso": None if estudios else SIN_VINCULO_REGISTRADO,
    }


# R-CAR.4 — por dónde entró alguien que no vino de una carga. Una persona
# puede tener varios; si no hay rastro de ninguno, es un alta manual.
INGRESO_LANDING = "landing"
INGRESO_ENCUESTA = "encuesta"
INGRESO_ALTA_MANUAL = "alta_manual"
ETIQUETAS_INGRESO = {
    INGRESO_LANDING: "Se inscribió por la landing",
    INGRESO_ENCUESTA: "Llegó en el archivo de campo de una encuesta",
    INGRESO_ALTA_MANUAL: "Alta manual",
}


def _otros_ingresos(conn, id_persona, creada_por_carga):
    """Lo que no es una carga: la inscripción aprobada, la encuesta de cuyo
    archivo salió, o —si no hay rastro de nada— el alta manual."""
    ingresos = [
        {"via": INGRESO_LANDING, "etiqueta": ETIQUETAS_INGRESO[INGRESO_LANDING],
         "fecha": f["resuelto_en"].isoformat() if f["resuelto_en"] else None,
         "detalle": None}
        for f in db.todas(
            conn,
            """select resuelto_en from inscripcion
                where id_persona = %s and estado = 'aprobada' order by resuelto_en""",
            (str(id_persona),))
    ] + [
        {"via": INGRESO_ENCUESTA, "etiqueta": ETIQUETAS_INGRESO[INGRESO_ENCUESTA],
         "fecha": f["llego_en"].isoformat(), "detalle": f["nombre"],
         "encuesta_id": f["encuesta_id"]}
        for f in db.todas(
            conn,
            """select pa.encuesta_id, e.nombre,
                      -- Cuándo llegó en el archivo, no una convocatoria:
                      -- quien vino en un archivo no fue convocado.
                      coalesce(pa.respondio_en, pa.convocado_en) as llego_en
                 from participacion pa join encuesta e on e.id = pa.encuesta_id
                where pa.id_persona = %s and pa.origen = 'importacion'
                  -- D81: si llegó en una carga de esa encuesta, ya figura
                  -- entre las cargas y no es «otro ingreso».
                  and not exists (
                      select 1 from persona_carga pc join carga c on c.id = pc.carga_id
                       where pc.id_persona = pa.id_persona
                         and c.encuesta_id = pa.encuesta_id)
                order by llego_en""",
            (str(id_persona),))
    ]
    if not ingresos and not creada_por_carga:
        fila = db.una(conn, "select creado_en from persona where id_persona = %s",
                      (str(id_persona),))
        ingresos.append({
            "via": INGRESO_ALTA_MANUAL, "etiqueta": ETIQUETAS_INGRESO[INGRESO_ALTA_MANUAL],
            "fecha": fila["creado_en"].isoformat() if fila else None, "detalle": None})
    return ingresos


def cargas_de_persona(conn, id_persona, incluir_especiales=False):
    """R-CAR.4 — la sección «Cargas» de la ficha: en qué cargas aparece, si
    la creó o la reutilizó, los clientes de cada carga y las temáticas que
    respondió **en ella**. La más reciente primero. Y, aparte, si entró por
    la landing, un alta manual o una encuesta.

    Los clientes y las temáticas se leen por join, como el resto de los
    datos del estudio: no se copian en la persona."""
    from . import clientes as mod_clientes, tematicas as mod_tematicas

    # D94 · R-INC.4 — cada encuesta **una vez**, con la primera carga en que
    # apareció la persona y su origen ahí; las cargas posteriores de esa
    # encuesta (el acumulado de una encuesta incremental) van como nota.
    todas = db.todas(
        conn,
        """
        select c.id, c.nombre, c.encuesta_id, e.nombre as encuesta,
               e.fecha_campo as fecha_estudio, c.creado_en as carga_creada_en,
               pc.origen, pc.creado_en
          from persona_carga pc join carga c on c.id = pc.carga_id
          join encuesta e on e.id = c.encuesta_id
         where pc.id_persona = %s
         order by c.encuesta_id, c.creado_en, c.id
        """,
        (str(id_persona),))
    filas, posteriores = [], {}
    for f in todas:
        if f["encuesta_id"] in posteriores:
            posteriores[f["encuesta_id"]].append(
                {"carga_id": f["id"], "nombre": f["nombre"],
                 "vinculado_en": f["creado_en"].isoformat()})
        else:
            posteriores[f["encuesta_id"]] = []
            filas.append(f)
    filas.sort(key=lambda f: (f["fecha_estudio"] or f["carga_creada_en"].date(),
                              f["id"]), reverse=True)
    # Clientes y temáticas son del estudio (D81): los de la encuesta de cada
    # carga, y las temáticas que la persona respondió en ese estudio.
    por_estudio = mod_clientes.de_encuestas(conn, [f["encuesta_id"] for f in filas])
    tematicas = {}
    for t in mod_tematicas.de_persona(
            conn, id_persona, incluir_especiales=incluir_especiales)["items"]:
        tematicas.setdefault(t["encuesta_id"], []).append(
            {"id": t["id"], "nombre": t["nombre"], "es_especial": t["es_especial"]})
    cargas = [{
        "carga_id": f["id"], "nombre": f["nombre"],
        "encuesta_id": f["encuesta_id"], "encuesta": f["encuesta"],
        "fecha_estudio": f["fecha_estudio"].isoformat() if f["fecha_estudio"] else None,
        "origen": f["origen"], "vinculado_en": f["creado_en"].isoformat(),
        "clientes": por_estudio.get(f["encuesta_id"], []),
        "tematicas": tematicas.get(f["encuesta_id"], []),
        "posteriores": posteriores[f["encuesta_id"]],
        "nota_posteriores": nota_de_posteriores(len(posteriores[f["encuesta_id"]])),
    } for f in filas]
    return {
        "cargas": cargas,
        "otros_ingresos": _otros_ingresos(
            conn, id_persona, any(c["origen"] == CREADA for c in cargas)),
        "aviso": None if cargas else SIN_VINCULO_REGISTRADO,
    }


def nota_de_posteriores(n):
    """R-INC.4 — «también en 2 cargas posteriores de esta encuesta»."""
    if not n:
        return None
    return (f"también en {n} carga{'s' if n != 1 else ''} posterior"
            f"{'es' if n != 1 else ''} de esta encuesta")


SIN_CARGA_PREVIA = "sin carga previa: landing, alta manual o encuesta"


def reutilizados(conn, carga_id):
    """R-CAR.3 — de dónde vienen los reutilizados de una carga: para cada
    **otra** carga en la que esas personas ya estaban (vinculadas antes que
    a esta), cuántas son. Una persona puede estar en varias, así que la suma
    puede superar el total. Quien no estaba en ninguna carga anterior va en
    una fila propia, para que nadie quede sin explicar."""
    carga = db.una(conn, "select id from carga where id = %s", (carga_id,))
    if not carga:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    total = db.una(
        conn,
        """select count(*)::int as n from persona_carga
            where carga_id = %s and origen = 'reutilizada'""",
        (carga_id,))["n"]
    filas = db.todas(
        conn,
        """
        select c.id, c.nombre, e.fecha_campo as fecha_estudio, count(*)::int as personas
          from persona_carga esta
          join persona_carga otra
            on otra.id_persona = esta.id_persona
           and otra.carga_id <> esta.carga_id
           -- «Antes»: por cuándo se vinculó; a igual momento (una misma
           -- transacción), la carga más vieja.
           and (otra.creado_en, otra.carga_id) < (esta.creado_en, esta.carga_id)
          join carga c on c.id = otra.carga_id
          join encuesta e on e.id = c.encuesta_id
         where esta.carga_id = %s and esta.origen = 'reutilizada'
         group by c.id, e.id
         order by count(*) desc, coalesce(e.fecha_campo, c.creado_en::date) desc, c.id
        """,
        (carga_id,))
    sin_previa = db.una(
        conn,
        """
        select count(*)::int as n
          from persona_carga esta
         where esta.carga_id = %s and esta.origen = 'reutilizada'
           and not exists (select 1 from persona_carga otra
                            where otra.id_persona = esta.id_persona
                              and otra.carga_id <> esta.carga_id
                              and (otra.creado_en, otra.carga_id)
                                  < (esta.creado_en, esta.carga_id))
        """,
        (carga_id,))["n"]
    return {
        "carga_id": carga_id,
        "reutilizados": total,
        "cargas": [{
            "carga_id": f["id"], "nombre": f["nombre"],
            "fecha_estudio": f["fecha_estudio"].isoformat() if f["fecha_estudio"] else None,
            "personas": f["personas"],
        } for f in filas],
        "sin_carga_previa": sin_previa,
        "sin_carga_previa_etiqueta": SIN_CARGA_PREVIA,
        "nota": "Una persona puede aparecer en más de una carga.",
    }


def _rotulo(fila):
    partes = [fila["nombre"]]
    if fila["fecha_estudio"]:
        partes.append(fila["fecha_estudio"].isoformat())
    return " · ".join(partes)


def origen_de_varias(conn, ids_persona):
    """La procedencia de un conjunto, en una sola consulta (R7.4).

    Devuelve, por persona, las columnas de `COLUMNAS_DE_PROCEDENCIA` —el
    estudio de origen, el tipo de datos y el modo de relevamiento— con la
    misma forma que un atributo (`valor`, `etiqueta_valor`), para que la
    pantalla las pinte igual. Quien no tiene vínculo no tiene entrada, y una
    carga anterior a la 0026 no aporta tipo ni modo: la columna dice «sin
    dato», como con cualquier atributo.

    Con varias cargas, el estudio las lista todas; el tipo y el modo, los
    valores distintos (dos encuestas telefónicas son «Telefónico», una vez).
    """
    ids = [str(i) for i in dict.fromkeys(i for i in (ids_persona or []) if i)]
    if not ids:
        return {}
    filas = db.todas(
        conn,
        """
        select pc.id_persona, c.nombre, e.fecha_campo as fecha_estudio,
               c.tipo_datos_origen, c.tipo_datos_origen_otro,
               c.modo_relevamiento, c.modo_relevamiento_otro
          from persona_carga pc
          join carga c on c.id = pc.carga_id
          join encuesta e on e.id = c.encuesta_id
         where pc.id_persona = any(%s::uuid[])
           -- D94 — el estudio de origen, una vez por encuesta.
           and pc.origen <> 'repetida'
         order by pc.id_persona, coalesce(e.fecha_campo, c.creado_en::date), c.id
        """,
        (ids,),
    )
    por_persona = {}
    for f in filas:
        rotulos = por_persona.setdefault(
            str(f["id_persona"]), {clave: [] for clave, _ in COLUMNAS_DE_PROCEDENCIA})
        rotulos[COLUMNA_ESTUDIO_DE_ORIGEN].append(_rotulo(f))
        for columna in (COLUMNA_TIPO_DATOS_ORIGEN, COLUMNA_MODO_RELEVAMIENTO):
            etiqueta = etiqueta_de(columna, f[columna], f[f"{columna}_otro"])
            if etiqueta and etiqueta not in rotulos[columna]:
                rotulos[columna].append(etiqueta)
    return {
        id_persona: {
            columna: {"valor": " | ".join(valores),
                      "etiqueta_valor": " | ".join(valores),
                      "origen": "carga", "procedencia": None}
            for columna, valores in columnas.items() if valores
        }
        for id_persona, columnas in por_persona.items()
    }


def ingestar(conn_boveda, conn_semantica, carga_id, preguntas, filas,
             columna_id="id_en_origen", origen=None, proveedor=None,
             demograficas=None, tipo_identificador=None, normalizacion=None,
             canales_aceptados=None, canales_declarados_por=None, trabajo_id=None,
             canales_para=None):
    """Incorpora el archivo de una carga a su encuesta. **La** ingesta (D81).

    Es lo que hacían, por separado, la ingesta desde una encuesta y la carga
    sin panel, ahora en un solo camino: el que usan la ruta, los lotes
    diferidos y `encuestas.ingestar`.

    - Resuelve la columna de identidad con las participaciones de la
      encuesta y `alias_origen` (`encuestas.resolver_identidades`).
    - Escribe en **el cuestionario de la encuesta** (su `ref_estudio`): una
      sola respuesta por persona y pregunta, gana la última carga, y cada
      respuesta guarda qué carga la escribió (`respuesta.carga_id`).
    - Las temáticas de cada persona son las del estudio (R-TEM.6).
    - Registra el vínculo persona ↔ carga (R-ORG.1) y la **participación**
      de cada persona en la encuesta (`respondio`); quien no estaba
      convocado queda con `convocado = false` (R-EU.4).
    - Los demográficos van a la bóveda (R3.9.d) y, si la encuesta es de
      panel, sus respondentes se incorporan al panel (R3.9.a). Una
      independiente no tiene panel al que incorporar.
    - R-CAN.1 (D88) — con `canales_aceptados` (`{canal: versión del texto}`,
      del plan congelado), las personas del lote quedan con esos canales,
      salvo quien lo revocó (`canales_carga.aplicar`), y queda auditado.
    """
    from . import encuestas, tematicas

    carga = obtener(conn_boveda, carga_id)
    encuesta = encuestas.obtener(conn_boveda, carga["encuesta_id"])
    demograficas = sav.normalizar_demograficas(
        demograficas, campos_validos=sav.campos_demograficos(conn_boveda),
        conn=conn_boveda)

    # R3.9.d — lo marcado como demográfico no se ingesta como pregunta; su
    # valor va a la ficha. El filtro corre acá y no solo en la pantalla: es
    # una regla de privacidad.
    marcadas = set(demograficas)
    excluidas = sorted(
        {p.get("codigo") for p in preguntas if p.get("codigo") in marcadas})
    opciones_demograficas = {
        p.get("codigo"): (p.get("opciones") or {})
        for p in preguntas if p.get("codigo") in marcadas
    }
    preguntas = [p for p in preguntas if p.get("codigo") not in marcadas]

    ids_del_archivo = [
        i for i in dict.fromkeys(
            str(f.get(columna_id) or "").strip() for f in filas)
        if i
    ]
    tipo = tipo_identificador or mod_ingesta.POR_ALIAS
    mapa, motivos = encuestas.resolver_identidades(
        conn_boveda, encuesta["id"], tipo, ids_del_archivo, origen)
    if not mapa and tipo == mod_ingesta.POR_ALIAS and not origen:
        raise DatosInvalidos(
            "No hay forma de saber a qué panelista corresponde cada fila del "
            "archivo: registrá el `alias_origen` de los convocados, indicá "
            "el `origen` de la plataforma de campo, o declará otro tipo de "
            "identificador.",
            {"tipos_validos": list(mod_ingesta.TIPOS_DE_IDENTIFICADOR)},
        )

    resultado = mod_ingesta.ingestar(
        conn_boveda,
        conn_semantica,
        # El estudio es la encuesta; la carga viaja para que cada respuesta
        # diga cuál la escribió (R-EU.3).
        mod_ingesta.estudio_del_destino(conn_boveda, "carga", carga_id),
        preguntas,
        filas,
        columna_id=columna_id,
        origen=origen,
        proveedor=proveedor,
        mapa_personas=mapa or None,
        motivos_sin_mapear=motivos,
        normalizacion=normalizacion,
        con_respuestas=True,
    )
    resultado["carga_id"] = carga_id
    resultado["encuesta_id"] = encuesta["id"]

    # R-TEM.6 — las temáticas de cada persona del lote, con lo que la
    # ingesta acaba de escribir: la fila del archivo y su `id_persona`, sin
    # leer el store semántico. Son las del estudio: las preguntas que las
    # sustentan se leen de la bóveda en cada lote, como el gate.
    resultado["tematicas_de_panelistas"] = tematicas.registrar_de_lote(
        conn_boveda, encuesta["id"],
        resultado.pop("respuestas_escritas_detalle", None) or [],
        preguntas, resultado.get("ids_persona_ingestados") or [],
        (normalizacion or {}).get("valores_no_respuesta"))
    resultado["tipo_identificador"] = tipo

    # R3.12.c — una carga por llave natural deja sembrado el alias.
    resultado["alias_registrados"] = encuestas._sembrar_alias(
        conn_boveda, tipo, mapa, origen)

    # R-ORG.1 — todos los que este lote resolvió vinieron de esta carga. Los
    # que la ruta ya registró como `creada` no se pisan.
    personas_del_lote = list(dict.fromkeys(
        mapa[i] for i in ids_del_archivo if i in (mapa or {})))
    resultado["vinculos_con_la_carga"] = registrar_vinculos(
        conn_boveda, carga_id, personas_del_lote, REUTILIZADA)
    resultado["excluidas_por_demografica"] = excluidas

    # R3.9.d — los demográficos van a la bóveda: completar lo vacío,
    # informar lo que discrepa, no pisar.
    resultado.update(encuestas.completar_demograficos(
        conn_boveda, filas, demograficas, columna_id, mapa,
        opciones_por_variable=opciones_demograficas))

    # R3.9.a — en una encuesta de panel, quien respondió pertenece al panel.
    if encuesta["tipo"] == encuestas.DE_PANEL:
        resultado.update(encuestas.incorporar_al_panel(
            conn_boveda, encuesta, resultado.get("ids_persona_ingestados") or []))

    # R-EU.1 — la participación de cada persona del archivo: respondió. Una
    # sola por persona y encuesta, aunque aparezca en varias cargas.
    resultado.update(encuestas.registrar_participacion_importada(
        conn_boveda, encuesta["id"], personas_del_lote))
    if encuestas.tiene_audiencia(conn_boveda, encuesta["id"]):
        resultado["no_convocados"] = db.una(
            conn_boveda,
            """select count(*)::int as n from participacion
                where encuesta_id = %s and not convocado
                  and id_persona = any(%s::uuid[])""",
            (encuesta["id"], personas_del_lote))["n"]
    resultado["sin_panel"] = encuesta["tipo"] != encuestas.DE_PANEL
    if canales_aceptados:
        from . import canales_carga

        # R-MIX.4 (D93) — desde la ingesta mixta, los canales del archivo se
        # declaran para las personas que la carga crea (`canales_para`).
        destinatarios = (personas_del_lote if canales_para is None else
                         [p for p in personas_del_lote
                          if p in {str(i) for i in canales_para}])
        resultado["canales"] = canales_carga.aplicar(
            conn_boveda, carga_id, destinatarios, canales_aceptados,
            canales_carga.VIA_INGESTA, actor=canales_declarados_por,
            trabajo_id=trabajo_id)
    return resultado


def crear_individuos(conn_boveda, filas, mapeo, origen, columna_id,
                     evidencia_consentimiento, actor=None,
                     opciones_por_variable=None, carga_id=None, alias_de=None):
    """Da de alta a la gente del archivo, sin meterla en ningún panel.

    Es `sav.crear_individuos` con dos diferencias, las dos deliberadas:

    - **`panel_id` no existe como parámetro.** No se puede pasar por error.
    - **La finalidad obligatoria es `uso_semantico`** y no el contacto (ver
      `FINALIDAD_OBLIGATORIA`).

    Con `carga_id` deja además el vínculo de R-ORG.1: `creada` para quien
    se dio de alta acá y `reutilizada` para quien el dedup encontró. Quien
    queda en revisión se vincula al resolverla (`revision.resolver`).
    """
    finalidad, panel_id = FINALIDAD_OBLIGATORIA, None
    if carga_id is not None:
        # D81 — en una encuesta de panel la gente es panelista y se la va a
        # seguir convocando: como en la ingesta de encuesta de siempre, la
        # obligatoria es el contacto y se la da de alta en el panel.
        estudio = db.una(
            conn_boveda,
            """select e.tipo, e.panel_id from carga c
                 join encuesta e on e.id = c.encuesta_id where c.id = %s""",
            (carga_id,))
        if estudio and estudio["tipo"] == "panel":
            finalidad, panel_id = sav.CONTACTO, estudio["panel_id"]
    resultado = sav.crear_individuos(
        conn_boveda, filas, mapeo, origen=origen, columna_id=columna_id,
        evidencia_consentimiento=evidencia_consentimiento, actor=actor,
        panel_id=panel_id, opciones_por_variable=opciones_por_variable,
        finalidad_obligatoria=finalidad, carga_id=carga_id, alias_de=alias_de,
    )
    if carga_id is not None:
        resultado["vinculos_con_la_carga"] = vincular_creacion(
            conn_boveda, carga_id, resultado)
    return resultado


def sin_panel(conn, limite=50, desplazamiento=0):
    """Los individuos que no son miembros de ningún panel.

    R3.13.e. El atajo del filtro de la pantalla de panelistas; la consulta
    vive en `personas.listar`.
    """
    return personas.listar(
        conn, limite=limite, desplazamiento=desplazamiento, sin_panel=True)


# ════════════════════════════════════════════════════════════════════
#  D81 · Lo que la revisión de una carga dice antes de confirmar
# ════════════════════════════════════════════════════════════════════

def huella(contenido=None, filas=None):
    """R-EU.3 — sha256 del archivo; si el archivo se leyó en el navegador
    (`.csv`, `.xlsx`), de sus filas en forma canónica. Dos subidas del mismo
    archivo dan la misma huella."""
    import hashlib
    import json

    if contenido is not None:
        datos = contenido if isinstance(contenido, bytes) else str(contenido).encode()
    else:
        datos = json.dumps(filas or [], sort_keys=True, ensure_ascii=False,
                           default=str).encode()
    return hashlib.sha256(datos).hexdigest()


def archivo_repetido(conn, encuesta_id, huella_archivo, excepto=None):
    """La carga de esa encuesta que ya trajo un archivo idéntico, o None."""
    if not huella_archivo:
        return None
    fila = db.una(
        conn,
        """select id, nombre, creado_en from carga
            where encuesta_id = %s and archivo_huella = %s
              and (%s::bigint is null or id <> %s::bigint)
            order by creado_en desc, id desc limit 1""",
        (encuesta_id, huella_archivo, excepto, excepto))
    if not fila:
        return None
    return {"carga_id": fila["id"], "nombre": fila["nombre"],
            "creado_en": fila["creado_en"].isoformat()}


def _ids_de(filas, columna_id):
    return [i for i in dict.fromkeys(
        str(f.get(columna_id) or "").strip() for f in filas or []) if i]


def no_convocados(conn, encuesta_id, filas, columna_id, tipo, origen):
    """R-EU.4 — quiénes del archivo **no** estaban en la audiencia de la
    encuesta. Sin audiencia (nadie convocado) no aplica y devuelve None.

    Una fila que no se puede atribuir a nadie del sistema tampoco estaba
    convocada: si se crea, se crea como no convocada. Se informa aparte
    porque es la señal más clara de un enlace reenviado."""
    from . import encuestas

    if not encuestas.tiene_audiencia(conn, encuesta_id):
        return None
    fuera, mapa = _no_convocados(conn, encuesta_id, filas, columna_id, tipo, origen)
    tipo = tipo or mod_ingesta.POR_ALIAS
    etiqueta = mod_ingesta.ETIQUETAS_DE_IDENTIFICADOR.get(tipo, tipo)
    sin_identificar = sum(1 for i in fuera if i not in mapa)
    return {
        "cuantos": len(fuera),
        "existentes": sum(1 for i in fuera if i in mapa),
        "sin_identificar": sin_identificar,
        # R-CTL.4 — con qué tipo se intentó identificar: «no se pudieron
        # identificar como id de la plataforma de campo» no es «no existen».
        "tipo_identificador": tipo,
        "tipo_etiqueta": etiqueta,
        "ids_en_origen": fuera[:200],
        "mas": max(0, len(fuera) - 200),
        "mensaje": (
            f"{len(fuera)} persona(s) del archivo no estaban convocadas a esta "
            f"encuesta. Se incorporan marcadas «no convocado»; en una encuesta "
            f"con audiencia, casi siempre es un enlace reenviado. Podés "
            f"excluirlas de la carga."
            + (f" {sin_identificar} no se pudieron identificar como {etiqueta}."
               if sin_identificar else "")) if fuera else None,
    }


def _no_convocados(conn, encuesta_id, filas, columna_id, tipo, origen):
    from . import encuestas

    ids = _ids_de(filas, columna_id)
    mapa, _ = encuestas.resolver_identidades(
        conn, encuesta_id, tipo or mod_ingesta.POR_ALIAS, ids, origen)
    convocadas = {str(f["id_persona"]) for f in db.todas(
        conn, "select id_persona from participacion where encuesta_id = %s and convocado",
        (encuesta_id,))}
    return [i for i in ids if str(mapa.get(i)) not in convocadas], mapa


def ids_no_convocados(conn, encuesta_id, filas, columna_id, tipo, origen):
    """Los ids del archivo que no estaban convocados (todos, sin recortar).
    Sin audiencia, ninguno: no hay a quién comparar."""
    from . import encuestas

    if not encuestas.tiene_audiencia(conn, encuesta_id):
        return []
    return _no_convocados(conn, encuesta_id, filas, columna_id, tipo, origen)[0]


def excluir_ids(filas, columna_id, ids):
    """Las filas sin esos ids (R-EU.4: excluir a los no convocados)."""
    fuera = {str(i) for i in ids or []}
    return [f for f in filas if str(f.get(columna_id) or "").strip() not in fuera]


# R-EU.6 — lo que implica, por finalidad, quedar sin consentimiento.
IMPLICANCIAS = {
    sav.CONTACTO: "No se los convocará en el futuro.",
    sav.SEMANTICO: ("Sus respuestas no entran al store semántico: no se van a "
                    "poder consultar."),
}


def consentimiento_por_finalidad(conn, encuesta_id, filas, columna_id, tipo,
                                 origen, evidencia=None, crea_personas=False):
    """R-EU.6 — quiénes del archivo quedan sin cada finalidad y qué implica.

    Para quien ya existe, lo que tiene vigente hoy. Para quien se va a
    crear, lo que declara su fila según la evidencia de consentimiento
    —sin evidencia declarada para una finalidad, nadie nuevo la tiene—. Se
    registra tal como vino: quien no consiente no va a la lista negra (la
    lista negra es para fraude, calidad o comportamiento)."""
    from . import encuestas

    ids = _ids_de(filas, columna_id)
    mapa, _ = encuestas.resolver_identidades(
        conn, encuesta_id, tipo or mod_ingesta.POR_ALIAS, ids, origen)
    existentes = list(dict.fromkeys(str(v) for v in mapa.values()))
    vigentes = {}
    for f in db.todas(
            conn,
            """select finalidad, id_persona::text as id_persona
                 from v_persona_finalidad_vigente
                where ref_estudio is null and id_persona = any(%s::uuid[])""",
            (existentes,)):
        vigentes.setdefault(f["finalidad"], set()).add(f["id_persona"])
    nuevas = [f for f in filas or []
              if str(f.get(columna_id) or "").strip() not in mapa] if crea_personas else []
    salida = []
    for finalidad in (sav.CONTACTO, sav.SEMANTICO):
        sin_existentes = len(set(existentes) - vigentes.get(finalidad, set()))
        regla = (evidencia or {}).get(finalidad)
        if regla:
            afirmativos = {str(v) for v in regla.get("valores_afirmativos") or []}
            sin_nuevas = sum(1 for f in nuevas
                             if str(f.get(regla["variable"]) or "").strip()
                             not in afirmativos)
        else:
            sin_nuevas = len(nuevas)
        total = sin_existentes + sin_nuevas
        salida.append({
            "finalidad": finalidad,
            "sin_consentimiento": total,
            "existentes_sin": sin_existentes,
            "nuevas_sin": sin_nuevas,
            "implica": IMPLICANCIAS[finalidad] if total else None,
        })
    return salida
