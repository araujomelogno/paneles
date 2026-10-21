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


_COLUMNAS = ("id, nombre, descripcion, ref_estudio, fecha_estudio, "
             "publico_objetivo, tipo_datos_origen, tipo_datos_origen_otro, "
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
        "fecha_estudio": (fila["fecha_estudio"].isoformat()
                          if fila.get("fecha_estudio") else None),
        "publico_objetivo": fila.get("publico_objetivo"),
        **datos_de_origen(fila),
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
          modo_relevamiento_otro=None, exigir_origen=False):
    """Abre una carga con los datos del estudio del que sale (R-ORG.4).

    Nombre, fecha, público objetivo, tipo de datos y modo de relevamiento se
    guardan **en la carga**, no en cada persona: una persona que aparece en
    tres cargas ve los tres estudios por el vínculo, y corregir la fecha es
    una fila.

    `exigir_origen` lo pasa la ruta: una carga nueva abierta desde la
    pantalla tiene que declarar tipo y modo. Las anteriores a la 0026 no los
    tienen, y por eso la base los admite nulos.
    """
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
    fila = db.una(
        conn,
        f"""
        insert into carga (nombre, descripcion, fecha_estudio,
                           publico_objetivo, tipo_datos_origen,
                           tipo_datos_origen_otro, modo_relevamiento,
                           modo_relevamiento_otro, creado_por)
             values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
          returning {_COLUMNAS}
        """,
        (nombre, (descripcion or "").strip() or None, _fecha(fecha_estudio),
         _texto(publico_objetivo), *tipo, *modo,
         getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None)),
    )
    return _serializar(fila)


def sincronizar_fecha(conn_boveda, conn_semantica, carga_id, solo_si_vacia=False):
    """Lleva la fecha del estudio de la carga a su cuestionario semántico.

    Es una fecha del estudio, no de nadie: no es PII. Se copia porque la
    ficha pagina y ordena las respuestas por esa fecha **en** el store
    semántico, y la serie longitudinal y los atributos «al momento de la
    respuesta» se anclan en ella. Al editar se pisa (vaciarla también se
    propaga); al completar cargas anteriores, `solo_si_vacia`.

    Devuelve si el cuestionario cambió: una carga que todavía no se ingestó
    no tiene cuestionario, y no es un error.
    """
    estudio = mod_ingesta.estudio_del_destino(conn_boveda, "carga", carga_id)
    return semantica.fechar_cuestionario(
        conn_semantica, estudio["ref_estudio"], estudio["fecha_campo"],
        solo_si_vacia=solo_si_vacia)


def completar_fechas(conn_boveda, conn_semantica):
    """Operación única: las cargas ingestadas antes de la corrección quedaron
    con el cuestionario sin fecha. Copia la del estudio **solo donde esté
    vacía**; una fecha que ya tenga el cuestionario no se toca.

    Devuelve cuántas cargas con fecha se revisaron y cuáles se completaron.
    No hace `commit`: lo decide quien llama (el script, con `--aplicar`).
    """
    con_fecha = db.todas(
        conn_boveda,
        "select id, nombre from carga where fecha_estudio is not null order by id")
    completadas = [
        {"id": c["id"], "nombre": c["nombre"]}
        for c in con_fecha
        if sincronizar_fecha(conn_boveda, conn_semantica, c["id"],
                             solo_si_vacia=True)
    ]
    return {"revisadas": len(con_fecha), "completadas": completadas}


def editar(conn, carga_id, cambios, conn_semantica=None):
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
    if not columnas:
        raise DatosInvalidos(
            "No hay nada que cambiar. Se pueden editar nombre, descripcion, "
            "fecha_estudio, publico_objetivo, tipo_datos_origen y "
            "modo_relevamiento.")
    fila = db.una(
        conn,
        f"update carga set {', '.join(c + ' = %s' for c in columnas)} "
        f"where id = %s returning id",
        tuple(valores) + (carga_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    if "fecha_estudio" in cambios and conn_semantica is not None:
        sincronizar_fecha(conn, conn_semantica, carga_id)
    return obtener(conn, carga_id)


def obtener(conn, carga_id):
    """La carga con sus conteos y sus temáticas incorporadas (R-TEM.1)."""
    from . import tematicas

    fila = db.una(
        conn,
        f"select {_COLUMNAS}, personas_creadas, personas_reutilizadas, personas "
        f"from v_carga_resumen where id = %s",
        (carga_id,),
    )
    if not fila:
        raise NoEncontrado(f"No existe la carga {carga_id}.")
    return {**_serializar(fila),
            "tematicas": tematicas.de_carga(conn, fila["id"])}


def listar(conn, busqueda=None, tematica_id=None):
    """R-CAR.1 — las cargas, con sus conteos y sus temáticas.

    Ordenadas por **fecha del estudio**, la más reciente primero; la que no
    la declaró se ubica por la fecha en que se cargó. `busqueda` filtra por
    nombre; `tematica_id`, a las que cubren esa temática.
    """
    from . import tematicas

    condiciones, params = [], []
    if tematica_id:
        condiciones.append(
            "exists (select 1 from carga_tematica ct "
            "where ct.carga_id = r.id and ct.tematica_id = %s)")
        params.append(tematica_id)
    donde = ("where " + " and ".join(condiciones)) if condiciones else ""
    filas = db.todas(
        conn,
        f"select {', '.join('r.' + c.strip() for c in _COLUMNAS.split(','))}, "
        f"r.personas_creadas, r.personas_reutilizadas, r.personas "
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
                 if clave in tematicas.clave_de_busqueda(f["nombre"])]
    por_carga = tematicas.de_cargas(conn, [f["id"] for f in filas])
    return [{**_serializar(f), "tematicas": por_carga.get(f["id"], [])}
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
               c.tipo_datos_origen, c.tipo_datos_origen_otro,
               c.modo_relevamiento, c.modo_relevamiento_otro,
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
        select pc.id_persona, c.nombre, c.fecha_estudio,
               c.tipo_datos_origen, c.tipo_datos_origen_otro,
               c.modo_relevamiento, c.modo_relevamiento_otro
          from persona_carga pc
          join carga c on c.id = pc.carga_id
         where pc.id_persona = any(%s::uuid[])
         order by pc.id_persona, coalesce(c.fecha_estudio, c.creado_en::date), c.id
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

    # R-TEM.6 — las temáticas de cada persona del lote, con lo que la
    # ingesta acaba de escribir: la fila del archivo y su `id_persona`, sin
    # leer el store semántico. Las preguntas que sustentan cada temática se
    # leen de la bóveda en cada lote, como el gate: si el analista las
    # corrige mientras la carga avanza, el recálculo de esa edición y los
    # lotes que faltan dicen lo mismo.
    from . import tematicas

    resultado["tematicas_de_panelistas"] = tematicas.registrar_de_lote(
        conn_boveda, carga_id,
        resultado.pop("respuestas_escritas_detalle", None) or [],
        preguntas, resultado.get("ids_persona_ingestados") or [],
        (normalizacion or {}).get("valores_no_respuesta"))
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
