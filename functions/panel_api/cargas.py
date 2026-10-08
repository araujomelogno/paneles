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
"""

from . import db, encuestas, ingesta as mod_ingesta, personas, sav
from .errores import DatosInvalidos, NoEncontrado

# La finalidad que una fila tiene que evidenciar para que se cree la persona.
# **Es la inversa de la ingesta desde encuesta**, y a propósito: allá la
# obligatoria es `contacto_participacion` porque esa persona es un panelista
# al que se va a seguir convocando; acá son personas que no se van a contactar
# y cuyos datos se incorporan para análisis. Exigir consentimiento de contacto
# sería pedir base legal para algo que no se va a hacer, y no exigir el de uso
# semántico dejaría sin base lo único que sí se va a hacer.
FINALIDAD_OBLIGATORIA = sav.SEMANTICO


_COLUMNAS = ("id, nombre, descripcion, ref_estudio, fecha_estudio, "
             "publico_objetivo, creado_en, creado_por")

# R-ORG.1 — los dos orígenes del vínculo persona ↔ carga.
CREADA = "creada"
REUTILIZADA = "reutilizada"

# R-ORG.5 — la clave con la que el estudio de origen se ofrece como columna
# de los resultados (R7.4). No es un atributo del catálogo: no se puede
# filtrar ni fijar cuota con ella, solo mirarla.
COLUMNA_ESTUDIO_DE_ORIGEN = "estudio_de_origen"

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
        "fecha_estudio": (fila["fecha_estudio"].isoformat()
                          if fila.get("fecha_estudio") else None),
        "publico_objetivo": fila.get("publico_objetivo"),
        "creado_en": fila["creado_en"].isoformat(),
        "creado_por": fila["creado_por"],
    }
    for clave in ("personas_creadas", "personas_reutilizadas", "personas"):
        if clave in fila:
            salida[clave] = fila[clave]
    return salida


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


def crear(conn, nombre, descripcion=None, actor=None, fecha_estudio=None,
          publico_objetivo=None):
    """Abre una carga con los datos del estudio del que sale (R-ORG.4).

    Nombre, fecha y público objetivo se guardan **en la carga**, no en cada
    persona: una persona que aparece en tres cargas ve los tres estudios por
    el vínculo, y corregir la fecha es una fila.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosInvalidos(
            "La carga necesita un nombre: es lo que después identifica de "
            "dónde salieron esos datos (por ejemplo «Ómnibus agosto 2026»)."
        )
    fila = db.una(
        conn,
        f"""
        insert into carga (nombre, descripcion, fecha_estudio,
                           publico_objetivo, creado_por)
             values (%s, %s, %s, %s, %s)
          returning {_COLUMNAS}
        """,
        (nombre, (descripcion or "").strip() or None, _fecha(fecha_estudio),
         _texto(publico_objetivo),
         getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None)),
    )
    return _serializar(fila)


def editar(conn, carga_id, cambios):
    """Corrige los datos del estudio. Se ve en todas las fichas a la vez.

    Es la razón de guardarlos en la carga: corregir la fecha de un estudio
    no toca ninguna fila de `persona` ni de `persona_carga`.
    """
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
    if "fecha_estudio" in cambios:
        columnas.append("fecha_estudio")
        valores.append(_fecha(cambios.get("fecha_estudio")))
    if "publico_objetivo" in cambios:
        columnas.append("publico_objetivo")
        valores.append(_texto(cambios.get("publico_objetivo")))
    if not columnas:
        raise DatosInvalidos(
            "No hay nada que cambiar. Se pueden editar nombre, descripcion, "
            "fecha_estudio y publico_objetivo.")
    fila = db.una(
        conn,
        f"update carga set {', '.join(c + ' = %s' for c in columnas)} "
        f"where id = %s returning id",
        tuple(valores) + (carga_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    return obtener(conn, carga_id)


def obtener(conn, carga_id):
    fila = db.una(
        conn,
        f"select {_COLUMNAS}, personas_creadas, personas_reutilizadas, personas "
        f"from v_carga_resumen where id = %s",
        (carga_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    return _serializar(fila)


def listar(conn):
    return [
        _serializar(f)
        for f in db.todas(
            conn,
            f"select {_COLUMNAS}, personas_creadas, personas_reutilizadas, "
            f"personas from v_carga_resumen order by creado_en desc",
        )
    ]


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
    return db.ejecutar(
        conn,
        """
        insert into persona_carga (id_persona, carga_id, origen)
        select p.id_persona, %s, %s
          from persona p
         where p.id_persona = any(%s::uuid[])
        on conflict (id_persona, carga_id) do nothing
        """,
        (carga_id, origen, ids),
    )


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
        select c.id, c.nombre, c.fecha_estudio, c.publico_objetivo,
               pc.origen, pc.creado_en
          from persona_carga pc
          join carga c on c.id = pc.carga_id
         where pc.id_persona = %s
         order by coalesce(c.fecha_estudio, c.creado_en::date), c.id
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
            "origen": f["origen"],
            "vinculado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]
    return {
        "estudios": estudios,
        "aviso": None if estudios else SIN_VINCULO_REGISTRADO,
    }


def _rotulo(fila):
    partes = [fila["nombre"]]
    if fila["fecha_estudio"]:
        partes.append(fila["fecha_estudio"].isoformat())
    return " · ".join(partes)


def origen_de_varias(conn, ids_persona):
    """El estudio de origen de un conjunto, en una sola consulta (R7.4).

    Devuelve, por persona, el valor de la columna «Estudio de origen» con la
    misma forma que un atributo (`valor`, `etiqueta_valor`), para que la
    pantalla lo pinte igual. Quien no tiene vínculo no tiene entrada: la
    columna dice «sin dato», como con cualquier atributo.
    """
    ids = [str(i) for i in dict.fromkeys(i for i in (ids_persona or []) if i)]
    if not ids:
        return {}
    filas = db.todas(
        conn,
        """
        select pc.id_persona, c.nombre, c.fecha_estudio
          from persona_carga pc
          join carga c on c.id = pc.carga_id
         where pc.id_persona = any(%s::uuid[])
         order by pc.id_persona, coalesce(c.fecha_estudio, c.creado_en::date), c.id
        """,
        (ids,),
    )
    por_persona = {}
    for f in filas:
        por_persona.setdefault(str(f["id_persona"]), []).append(_rotulo(f))
    return {
        id_persona: {"valor": " | ".join(rotulos),
                     "etiqueta_valor": " | ".join(rotulos),
                     "origen": "carga", "procedencia": None}
        for id_persona, rotulos in por_persona.items()
    }


def ingestar(conn_boveda, conn_semantica, carga_id, preguntas, filas,
             columna_id="id_en_origen", origen=None, proveedor=None,
             demograficas=None, tipo_identificador=None, normalizacion=None):
    """Incorpora los individuos del archivo y sus respuestas. Sin panel.

    Es `encuestas.ingestar` menos las dos cosas que este flujo evita: no
    incorpora a ningún panel (R3.9.a) y no registra participación (R3.9.b).
    No hubo convocatoria ni encuesta fieldeada, así que no hay nada que
    registrar de esas dos.
    """
    carga = obtener(conn_boveda, carga_id)
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
    # R3.12.b — sin participaciones que consultar, la resolución es la del
    # tipo declarado y nada más. Una carga no tiene convocados.
    tipo = tipo_identificador or mod_ingesta.POR_ALIAS
    mapa, motivos = mod_ingesta.resolver_identificadores(
        conn_boveda, tipo, ids_del_archivo, origen)

    resultado = mod_ingesta.ingestar(
        conn_boveda,
        conn_semantica,
        # Frente al store semántico una carga es un estudio como cualquier
        # otro: lo único que mira es `ref_estudio`, el nombre y la fecha.
        {"ref_estudio": carga["ref_estudio"], "nombre": carga["nombre"],
         "fecha_campo": None, "panel_id": None},
        preguntas,
        filas,
        columna_id=columna_id,
        origen=origen,
        proveedor=proveedor,
        mapa_personas=mapa or None,
        motivos_sin_mapear=motivos,
        normalizacion=normalizacion,
    )
    resultado["carga_id"] = carga_id
    resultado["tipo_identificador"] = tipo

    # R-ORG.1 — todos los que este lote resolvió participaron de la carga.
    # Los que la ruta ya registró como `creada` no se pisan: el vínculo se
    # fija la primera vez (ver `registrar_vinculos`). Corre en cada lote, así
    # que el reintento de un lote no duplica nada.
    resultado["vinculos_con_la_carga"] = registrar_vinculos(
        conn_boveda, carga_id, list((mapa or {}).values()), REUTILIZADA)
    resultado["excluidas_por_demografica"] = excluidas

    # R3.9.d — los demográficos sí van a la bóveda, igual que en la ingesta
    # desde encuesta: completar lo vacío, informar lo que discrepa, no pisar.
    resultado.update(encuestas.completar_demograficos(
        conn_boveda, filas, demograficas, columna_id, mapa,
        opciones_por_variable=opciones_demograficas))

    # Y acá termina. Ni `incorporar_al_panel` ni
    # `registrar_participacion_importada`: es exactamente lo que este flujo
    # existe para no hacer.
    resultado["sin_panel"] = True
    return resultado


def crear_individuos(conn_boveda, filas, mapeo, origen, columna_id,
                     evidencia_consentimiento, actor=None,
                     opciones_por_variable=None, carga_id=None):
    """Da de alta a la gente del archivo, sin meterla en ningún panel.

    Es `sav.crear_individuos` con dos diferencias, las dos deliberadas:

    - **`panel_id` no existe como parámetro.** No se puede pasar por error.
    - **La finalidad obligatoria es `uso_semantico`** y no el contacto (ver
      `FINALIDAD_OBLIGATORIA`).

    Con `carga_id` deja además el vínculo de R-ORG.1: `creada` para quien
    se dio de alta acá y `reutilizada` para quien el dedup encontró. Quien
    queda en revisión se vincula al resolverla (`revision.resolver`).
    """
    resultado = sav.crear_individuos(
        conn_boveda, filas, mapeo, origen=origen, columna_id=columna_id,
        evidencia_consentimiento=evidencia_consentimiento, actor=actor,
        panel_id=None, opciones_por_variable=opciones_por_variable,
        finalidad_obligatoria=FINALIDAD_OBLIGATORIA, carga_id=carga_id,
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
