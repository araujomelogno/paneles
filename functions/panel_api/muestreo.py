"""R3.1 — Motor de muestreo por reglas.

Propone a quién invitar a una encuesta. Es lo que convierte la composición
de la Fase 2 —que solo mira— en una decisión.

El motor es **por reglas**, no una optimización: prioriza por brecha de
cuota y descarta por fatiga y consentimiento, en ese orden. La optimización
con restricciones (cuota sujeta a fatiga y equidad de rotación) es Fase 4
(R4.2) y está fuera de alcance a propósito.

Tres cosas que la propuesta hace y que no son obvias:

1. **No convoca.** Devuelve una sugerencia; convocar sigue siendo un acto
   explícito del responsable (R3.1). Acá se decide a quién ofrecer, no a
   quién mandarle la encuesta.

2. **Explica cada exclusión.** Un panel que propone diez nombres sin decir
   por qué faltan los otros doscientos no es una herramienta de decisión, es
   una caja negra. Cada persona descartada sale con su motivo.

3. **Avisa cuando un segmento con brecha se queda sin nadie.** El caso feo
   —la brecha existe pero todos sus elegibles están quemados— es justamente
   el que hay que ver, y el que una lista más corta escondería. La spec lo
   pide explícito: informar en vez de devolver menos gente sin decir nada.
"""

from . import composicion, consentimiento, db, demografia
from .errores import DatosInvalidos, NoEncontrado

# Defaults de fatiga. Están acá y no en la base porque son el punto de
# partida documentado (R3.1); `umbral_fatiga` los pisa por panel cuando
# alguien los calibra contra los datos de Equipos, que es lo que la spec
# marca como pendiente en §11.
DEFAULTS_FATIGA = {
    "max_convocatorias_ventana": 3,
    "ventana_dias": 90,
    "max_convocatorias_total": None,   # sin tope histórico por defecto
    "dias_minimos_entre": 14,
}

# Motivos de exclusión, en el orden en que se evalúan. El orden importa para
# el informe: a alguien sin consentimiento no tiene sentido contarlo como
# «fatigado», porque no era candidato en primer lugar.
SIN_CONSENTIMIENTO = "sin_consentimiento"
PENDIENTE = "pendiente_de_consentimiento"
YA_CONVOCADO = "ya_convocado_a_esta_encuesta"
DEMASIADAS_RECIENTES = "demasiadas_convocatorias_recientes"
DEMASIADAS_TOTALES = "demasiadas_convocatorias_acumuladas"
MUY_RECIENTE = "convocado_hace_muy_poco"
CUOTA_CUBIERTA = "cuota_del_segmento_ya_cubierta"

EXPLICACIONES = {
    SIN_CONSENTIMIENTO:
        "no tiene consentimiento vigente de contacto_participacion",
    PENDIENTE:
        "fue creada por una ingesta sin base legal registrada y está "
        "pendiente de consentimiento",
    YA_CONVOCADO: "ya está convocada a esta encuesta",
    DEMASIADAS_RECIENTES: "superó el tope de convocatorias de la ventana",
    DEMASIADAS_TOTALES: "superó el tope de convocatorias acumuladas",
    MUY_RECIENTE: "fue convocada hace menos de los días mínimos entre olas",
    CUOTA_CUBIERTA: "su segmento no tiene brecha que cerrar",
}


# ── Umbrales ────────────────────────────────────────────────────────

def obtener_umbrales(conn, panel_id):
    """Los umbrales del panel, o los defaults documentados."""
    fila = db.una(
        conn, "select * from umbral_fatiga where panel_id = %s", (panel_id,)
    )
    umbrales = dict(DEFAULTS_FATIGA)
    if fila:
        for clave in DEFAULTS_FATIGA:
            umbrales[clave] = fila[clave]
        umbrales["actualizado_por"] = fila["actualizado_por"]
        umbrales["actualizado_en"] = fila["actualizado_en"]
    umbrales["son_defaults"] = fila is None
    return umbrales


def guardar_umbrales(conn, panel_id, cambios, actor=None):
    """Configura los umbrales de un panel. Solo lo que venga: lo que no,
    conserva su valor (o el default si nunca se configuró)."""
    if not db.una(conn, "select 1 from panel where id = %s", (panel_id,)):
        raise NoEncontrado(f"No existe el panel {panel_id}.")

    actuales = obtener_umbrales(conn, panel_id)
    valores = {}
    for clave in DEFAULTS_FATIGA:
        crudo = cambios.get(clave, actuales[clave])
        if crudo is None and clave == "max_convocatorias_total":
            valores[clave] = None
            continue
        try:
            valores[clave] = int(crudo)
        except (TypeError, ValueError):
            raise DatosInvalidos(
                f"«{clave}» tiene que ser un número entero, no {crudo!r}."
            )
        if valores[clave] < 0:
            raise DatosInvalidos(f"«{clave}» no puede ser negativo.")
    if valores["ventana_dias"] <= 0:
        raise DatosInvalidos("«ventana_dias» tiene que ser mayor que cero.")

    db.ejecutar(
        conn,
        """
        insert into umbral_fatiga
               (panel_id, max_convocatorias_ventana, ventana_dias,
                max_convocatorias_total, dias_minimos_entre,
                actualizado_por, actualizado_en)
        values (%s, %s, %s, %s, %s, %s, now())
        on conflict (panel_id) do update set
            max_convocatorias_ventana = excluded.max_convocatorias_ventana,
            ventana_dias              = excluded.ventana_dias,
            max_convocatorias_total   = excluded.max_convocatorias_total,
            dias_minimos_entre        = excluded.dias_minimos_entre,
            actualizado_por           = excluded.actualizado_por,
            actualizado_en            = now()
        """,
        (panel_id, valores["max_convocatorias_ventana"], valores["ventana_dias"],
         valores["max_convocatorias_total"], valores["dias_minimos_entre"],
         getattr(actor, "uid", None)),
    )
    conn.commit()
    return obtener_umbrales(conn, panel_id)


# ── Candidatos ──────────────────────────────────────────────────────

def _candidatos(conn, panel_id, encuesta_id, dimension, umbrales):
    """Todos los miembros activos del panel, con lo que hace falta para
    decidir: su categoría en la dimensión de cuota y su historial de
    convocatorias.

    Una sola consulta y no una por persona: un panel de veinte mil miembros
    haría veinte mil viajes.
    """
    columna = demografia.DIMENSIONES[dimension]
    return db.todas(
        conn,
        f"""
        select
            m.id_persona,
            p.estado                                     as estado_persona,
            coalesce({columna}::text, '(sin dato)')      as categoria,
            -- Convocatorias dentro de la ventana.
            count(*) filter (
                where pa.convocado_en >= now() - make_interval(days => %s)
                  and pa.encuesta_id <> %s
            )::int                                       as recientes,
            -- Acumuladas de toda la vida.
            count(pa.id) filter (where pa.encuesta_id <> %s)::int as totales,
            max(pa.convocado_en) filter (where pa.encuesta_id <> %s)
                                                         as ultima_convocatoria,
            bool_or(pa.encuesta_id = %s)                 as ya_en_esta,
            count(*) filter (where pa.respondio and pa.encuesta_id <> %s)::int
                                                         as respondidas
          from membresia m
          join persona p       on p.id_persona = m.id_persona
          left join v_demografia d on d.id_persona = m.id_persona
          left join participacion pa on pa.id_persona = m.id_persona
         where m.panel_id = %s and m.estado = 'activo'
         group by m.id_persona, p.estado, {columna}
        """,
        (umbrales["ventana_dias"], encuesta_id, encuesta_id, encuesta_id,
         encuesta_id, encuesta_id, panel_id),
    )


def _dias_desde(fecha, ahora):
    if fecha is None:
        return None
    return (ahora - fecha).days


def _motivo_de_exclusion(candidato, umbrales, con_consentimiento, ahora):
    """El primer motivo por el que esta persona no puede ser propuesta, o
    None si es elegible. El orden es el de `EXPLICACIONES`."""
    if candidato["estado_persona"] == "pendiente_consentimiento":
        return PENDIENTE
    if str(candidato["id_persona"]) not in con_consentimiento:
        return SIN_CONSENTIMIENTO
    if candidato["ya_en_esta"]:
        return YA_CONVOCADO
    if candidato["recientes"] >= umbrales["max_convocatorias_ventana"]:
        return DEMASIADAS_RECIENTES
    tope = umbrales["max_convocatorias_total"]
    if tope is not None and candidato["totales"] >= tope:
        return DEMASIADAS_TOTALES
    dias = _dias_desde(candidato["ultima_convocatoria"], ahora)
    if dias is not None and dias < umbrales["dias_minimos_entre"]:
        return MUY_RECIENTE
    return None


def _prioridad(candidato):
    """Dentro de un mismo segmento, a quién ofrecer primero.

    Menos convocado antes que más convocado —eso es la rotación—, y a
    igualdad de convocatorias, quien más respondió: si hay que gastar una
    convocatoria, mejor en alguien que suele contestar. El `id_persona`
    final es solo para que el orden sea estable entre corridas.
    """
    return (candidato["recientes"], candidato["totales"],
            -candidato["respondidas"], str(candidato["id_persona"]))


# ── Propuesta ───────────────────────────────────────────────────────

def proponer(conn, encuesta_id, dimension="sexo", cantidad=100, estado="activo"):
    """Propone a quién invitar, priorizando los segmentos con más brecha.

    `dimension` es la dimensión de cuota sobre la que se equilibra. Es una
    sola y no las tres a la vez: equilibrar simultáneamente por sexo, tramo
    y localidad es un problema de optimización, no de reglas, y es Fase 4.
    """
    encuesta = db.una(
        conn,
        "select id, panel_id, nombre, estado from encuesta where id = %s",
        (encuesta_id,),
    )
    if not encuesta:
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    if dimension not in demografia.DIMENSIONES_CATEGORICAS:
        raise DatosInvalidos(
            f"«{dimension}» no es una dimensión de cuota.",
            {"dimensiones_validas": list(demografia.DIMENSIONES_CATEGORICAS)},
        )
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        raise DatosInvalidos(f"«cantidad» tiene que ser un número: {cantidad!r}.")
    if cantidad <= 0:
        raise DatosInvalidos("«cantidad» tiene que ser mayor que cero.")

    panel_id = encuesta["panel_id"]
    umbrales = obtener_umbrales(conn, panel_id)

    # Cuánto falta en cada categoría, según el universo de referencia.
    estado_composicion = composicion.composicion(
        conn, panel_id, dimensiones=[dimension], estado=estado
    )
    detalle = estado_composicion["dimensiones"][0]
    faltan = {
        c["categoria"]: (c["faltan"] or 0)
        for c in detalle["categorias"]
    }
    hay_objetivo = detalle["brecha_disponible"]

    candidatos = _candidatos(conn, panel_id, encuesta_id, dimension, umbrales)
    ids = [c["id_persona"] for c in candidatos]
    habilitadas, _ = consentimiento.filtrar_con_consentimiento(
        conn, ids, consentimiento.CONTACTO
    )
    con_consentimiento = set(habilitadas)

    ahora = db.una(conn, "select now() as ahora")["ahora"]
    elegibles_por_categoria = {}
    excluidos = []
    conteo_exclusiones = {}
    for candidato in candidatos:
        motivo = _motivo_de_exclusion(
            candidato, umbrales, con_consentimiento, ahora
        )
        if motivo:
            conteo_exclusiones[motivo] = conteo_exclusiones.get(motivo, 0) + 1
            excluidos.append({
                "id_persona": str(candidato["id_persona"]),
                "categoria": candidato["categoria"],
                "motivo": motivo,
                "explicacion": EXPLICACIONES[motivo],
                "convocatorias_recientes": candidato["recientes"],
                "convocatorias_totales": candidato["totales"],
            })
            continue
        elegibles_por_categoria.setdefault(candidato["categoria"], []).append(candidato)

    for lista in elegibles_por_categoria.values():
        lista.sort(key=_prioridad)

    propuesta, avisos = _repartir(
        elegibles_por_categoria, faltan, hay_objetivo, cantidad, dimension
    )

    # Quien era elegible y no entró por no tener brecha su segmento: es una
    # exclusión distinta de las anteriores —no está impedido, simplemente no
    # hace falta— y por eso se cuenta aparte.
    elegidos = {p["id_persona"] for p in propuesta}
    for categoria, lista in elegibles_por_categoria.items():
        for candidato in lista:
            if str(candidato["id_persona"]) in elegidos:
                continue
            conteo_exclusiones[CUOTA_CUBIERTA] = \
                conteo_exclusiones.get(CUOTA_CUBIERTA, 0) + 1
            excluidos.append({
                "id_persona": str(candidato["id_persona"]),
                "categoria": categoria,
                "motivo": CUOTA_CUBIERTA,
                "explicacion": EXPLICACIONES[CUOTA_CUBIERTA],
                "convocatorias_recientes": candidato["recientes"],
                "convocatorias_totales": candidato["totales"],
            })

    return {
        "encuesta": {
            "id": encuesta["id"], "nombre": encuesta["nombre"],
            "panel_id": panel_id, "estado": encuesta["estado"],
        },
        "dimension": dimension,
        "cantidad_pedida": cantidad,
        "umbrales": umbrales,
        "brecha_disponible": hay_objetivo,
        "propuesta": propuesta,
        "avisos": avisos,
        "excluidos": excluidos,
        "resumen_exclusiones": [
            {"motivo": motivo, "personas": n, "explicacion": EXPLICACIONES[motivo]}
            for motivo, n in sorted(
                conteo_exclusiones.items(), key=lambda par: -par[1]
            )
        ],
        # R3.1 — la propuesta no convoca. Se dice en la respuesta para que no
        # haya duda de qué pasó al pedirla.
        "convoca": False,
        "nota": (
            "Es una sugerencia: nadie fue convocado. Para convocar, mandar "
            "los id_persona elegidos a POST /encuestas/{id}/convocar."
        ),
    }


def _repartir(elegibles, faltan, hay_objetivo, cantidad, dimension):
    """Reparte los cupos entre las categorías, priorizando las que más
    lejos están de su cuota.

    Sin universo de referencia no hay brecha que priorizar, así que se
    reparte parejo y se dice —una propuesta sin objetivo cargado no es
    inválida, pero tampoco es lo que R3.1 promete—.
    """
    avisos = []
    propuesta = []

    if not hay_objetivo:
        avisos.append({
            "tipo": "sin_objetivo",
            "mensaje": (
                f"El panel no tiene universo de referencia cargado para "
                f"«{dimension}», así que no hay brecha que priorizar. La "
                f"propuesta reparte parejo entre las categorías; para que "
                f"priorice, cargá el objetivo de composición."
            ),
        })

    # Cuánto se pide de cada categoría. Con objetivo y con brecha, en
    # proporción a lo que falta. Sin brecha —o sin objetivo— parejo.
    total_faltante = sum(faltan.values())
    sin_brecha = hay_objetivo and total_faltante == 0
    if sin_brecha:
        # El panel ya calza con su universo de referencia. Es una buena
        # noticia y hay que decirla: sin esto, la propuesta terminaba
        # avisando «este segmento tiene brecha (0 personas) y no alcanza»,
        # que es literalmente una contradicción.
        avisos.append({
            "tipo": "sin_brecha",
            "mensaje": (
                f"El panel ya calza con su universo de referencia en "
                f"«{dimension}»: no hay brecha que priorizar. La propuesta "
                f"reparte parejo entre las categorías para no desbalancearlo."
            ),
        })

    if hay_objetivo and total_faltante:
        cupos = {
            categoria: round(cantidad * falta / total_faltante)
            for categoria, falta in faltan.items() if falta > 0
        }
    else:
        categorias = sorted(elegibles) or sorted(faltan)
        por_cabeza = cantidad // len(categorias) if categorias else 0
        cupos = {categoria: por_cabeza for categoria in categorias}

    # Las categorías con más brecha primero: si el cupo total no alcanza, el
    # recorte tiene que caer en las que menos falta les hace.
    orden = sorted(cupos, key=lambda c: (-faltan.get(c, 0), c))

    for categoria in orden:
        cupo = cupos[categoria]
        disponibles = elegibles.get(categoria, [])
        tomados = disponibles[:cupo]
        for candidato in tomados:
            propuesta.append({
                "id_persona": str(candidato["id_persona"]),
                "categoria": categoria,
                "convocatorias_recientes": candidato["recientes"],
                "convocatorias_totales": candidato["totales"],
                "respondidas": candidato["respondidas"],
                "ultima_convocatoria": (
                    candidato["ultima_convocatoria"].isoformat()
                    if candidato["ultima_convocatoria"] else None
                ),
                "motivo_prioridad": (
                    f"faltan {faltan.get(categoria, 0)} en «{categoria}»"
                    if hay_objetivo else "reparto parejo (sin objetivo cargado)"
                ),
            })

        # El caso que la spec pide informar explícitamente: hay brecha y no
        # hay a quién ofrecer, o no alcanza. Devolver una lista más corta sin
        # decir nada haría que el responsable crea que la brecha se cierra.
        #
        # Solo aplica donde efectivamente falta gente: avisar de un segmento
        # con brecha cero no informa nada y contradice al propio mensaje.
        if cupo > 0 and len(tomados) < cupo and faltan.get(categoria, 0) > 0:
            avisos.append({
                "tipo": "segmento_sin_elegibles" if not tomados
                        else "segmento_con_elegibles_insuficientes",
                "dimension": dimension,
                "categoria": categoria,
                "faltan_en_el_panel": faltan.get(categoria, 0),
                "cupo_pedido": cupo,
                "elegibles_encontrados": len(tomados),
                "mensaje": (
                    f"«{categoria}» tiene brecha ({faltan.get(categoria, 0)} "
                    f"personas) y "
                    + ("no hay ningún miembro elegible: todos están excluidos "
                       "por fatiga, consentimiento o ya convocados."
                       if not tomados else
                       f"solo hay {len(tomados)} elegibles para los {cupo} "
                       f"que harían falta.")
                    + " La brecha no se cierra con esta propuesta; cerrarla "
                      "requiere enrolar gente de ese segmento o revisar los "
                      "umbrales de fatiga."
                ),
            })

    # Si sobró cupo, se completa **solo con segmentos que todavía tienen
    # brecha**. La tentación es rellenar con quien haya para entregar la
    # cantidad pedida, pero eso agregaría gente del segmento que sobra y
    # empeoraría exactamente la brecha que la propuesta viene a cerrar. Con
    # objetivo cargado, una propuesta más corta es la respuesta correcta, y
    # el aviso de más arriba ya dice por qué.
    ya = {p["id_persona"] for p in propuesta}
    if len(propuesta) < cantidad:
        candidatas = (
            [c for c in orden if faltan.get(c, 0) > 0] if hay_objetivo
            else (orden or sorted(elegibles))
        )
        resto = [
            candidato
            for categoria in candidatas
            for candidato in elegibles.get(categoria, [])
            if str(candidato["id_persona"]) not in ya
        ]
        resto.sort(key=_prioridad)
        for candidato in resto[: cantidad - len(propuesta)]:
            propuesta.append({
                "id_persona": str(candidato["id_persona"]),
                "categoria": candidato["categoria"],
                "convocatorias_recientes": candidato["recientes"],
                "convocatorias_totales": candidato["totales"],
                "respondidas": candidato["respondidas"],
                "ultima_convocatoria": (
                    candidato["ultima_convocatoria"].isoformat()
                    if candidato["ultima_convocatoria"] else None
                ),
                "motivo_prioridad": (
                    f"completa el cupo dentro de «{candidato['categoria']}», "
                    f"que sigue con brecha" if hay_objetivo
                    else "completa el cupo (reparto parejo)"
                ),
            })

    # Pedir 100 y recibir 40 sin explicación se lee como una falla. Con
    # objetivo cargado es la conducta correcta —no hay más gente a quien
    # ofrecer sin empeorar la representatividad— pero hay que decirlo.
    if len(propuesta) < cantidad:
        avisos.append({
            "tipo": "propuesta_mas_corta_que_lo_pedido",
            "pedidas": cantidad,
            "propuestas": len(propuesta),
            "mensaje": (
                f"Se pidieron {cantidad} y la propuesta trae {len(propuesta)}. "
                + ("No hay más miembros elegibles en el panel: mirá las "
                   "exclusiones para ver qué los frena."
                   if sin_brecha else
                   "No se completó con gente de segmentos sin brecha a "
                   "propósito: sumarlos alejaría al panel de su universo de "
                   "referencia. Revisá los avisos por segmento y las "
                   "exclusiones para ver qué lo limita."
                   if hay_objetivo else
                   "No hay más miembros elegibles: mirá las exclusiones.")
            ),
        })

    return propuesta, avisos
