"""R1.1 / R1.2 — Alta de panelista en la bóveda y ficha de persona.

`id_persona` (uuid aleatorio y opaco) lo emite el alta y es la clave de la
persona en toda la plataforma. La ingesta semántica lo referencia; no crea
personas.
"""

import json

from . import consentimiento, db, dedup
from .errores import Conflicto, DatosInvalidos, NoEncontrado

# Columnas de PII que acepta el alta. Se listan explícitamente para que
# nada entre por accidente y para que el orden de escritura sea evidente.
CAMPOS_PERSONA = (
    "documento", "nombre", "sexo", "fecha_nacimiento", "localidad",
    "email", "celular", "contacto", "observaciones",
)

# `documento` y `email` tienen índice único y son las claves con las que el
# dedup reconoce a una persona, así que se tratan distinto del resto: se
# pueden **completar** cuando están vacíos —eso es terminar de cargar la
# identidad, y le saca a esa persona la ambigüedad permanente en el dedup—
# pero no cambiar ni borrar una vez que tienen valor. Cambiar una clave de
# dedup no corrige un dato: cambia la identidad con la que el sistema
# reconoce a la persona, y puede partir o fusionar registros sin que se note.
CLAVES_DE_DEDUP = ("documento", "email")
CAMPOS_EDITABLES = CAMPOS_PERSONA


def _limpiar(datos):
    limpio = {}
    for campo in CAMPOS_PERSONA:
        valor = datos.get(campo)
        if isinstance(valor, str):
            valor = valor.strip() or None
        if valor is not None:
            limpio[campo] = valor
    return limpio


def _validar_consentimientos(consentimientos):
    """R1.1 — el alta sin consentimiento se rechaza."""
    if not consentimientos:
        raise DatosInvalidos(
            "El alta exige al menos un consentimiento: sin él la persona no "
            "puede ser contactada ni usada en el panel."
        )
    normalizados = []
    for entrada in consentimientos:
        finalidad = (entrada.get("finalidad") or "").strip()
        version = (entrada.get("version_texto") or "").strip()
        if finalidad not in consentimiento.FINALIDADES:
            raise DatosInvalidos(
                f"Finalidad desconocida: {finalidad!r}.",
                {"finalidades_validas": list(consentimiento.FINALIDADES)},
            )
        if not version:
            raise DatosInvalidos(
                f"Falta `version_texto` para la finalidad «{finalidad}»."
            )
        normalizados.append(
            {
                "finalidad": finalidad,
                "version_texto": version,
                "panel_id": entrada.get("panel_id"),
            }
        )
    return normalizados


def _crear(conn, datos):
    campos = list(datos.keys())
    marcadores = ", ".join(["%s"] * len(campos))
    fila = db.una(
        conn,
        f"insert into persona ({', '.join(campos)}) values ({marcadores}) "
        f"returning id_persona",
        tuple(datos[c] for c in campos),
    )
    return fila["id_persona"]


def _completar_faltantes(conn, id_persona, datos):
    """Al reutilizar una persona, rellena solo lo que estaba vacío.

    Nunca pisa un dato existente: el alta nueva puede traer información peor
    que la que ya está en la bóveda.
    """
    actual = db.una(
        conn,
        f"select {', '.join(CAMPOS_PERSONA)} from persona where id_persona = %s",
        (id_persona,),
    )
    if not actual:
        raise NoEncontrado(f"No existe la persona {id_persona}.")
    a_completar = {
        campo: valor
        for campo, valor in datos.items()
        if actual.get(campo) in (None, "")
    }
    # `documento` y `email` tienen índice único: si el valor que trae el alta
    # ya es de OTRA persona, completar rompería la base. En ese caso no se
    # completa (el dato entra en conflicto y lo resuelve una persona, no una
    # heurística).
    for campo, sql in (
        ("documento", "select 1 from persona where documento = %s and id_persona <> %s"),
        ("email", "select 1 from persona where lower(email) = lower(%s) and id_persona <> %s"),
    ):
        if campo in a_completar and db.una(conn, sql, (a_completar[campo], id_persona)):
            a_completar.pop(campo)
    if not a_completar:
        return []
    asignaciones = ", ".join(f"{c} = %s" for c in a_completar)
    db.ejecutar(
        conn,
        f"update persona set {asignaciones} where id_persona = %s",
        tuple(a_completar.values()) + (id_persona,),
    )
    return sorted(a_completar)


def alta(conn, cuerpo, actor=None):
    """Alta de panelista: dedup → persona → consentimientos → membresía.

    Devuelve `{"estado": "creada"|"reutilizada"|"revision", ...}`. En
    `revision` no se crea ni se toca ninguna persona: el alta queda
    esperando decisión humana.
    """
    datos = _limpiar(cuerpo.get("persona") or cuerpo)
    consentimientos = _validar_consentimientos(cuerpo.get("consentimientos"))
    origen = (cuerpo.get("origen") or "").strip() or None
    id_en_origen = (cuerpo.get("id_en_origen") or "").strip() or None
    panel_id = cuerpo.get("panel_id")

    if not any(datos.get(c) for c in ("documento", "email", "nombre")):
        raise DatosInvalidos(
            "El alta necesita al menos documento, email o nombre para "
            "identificar a la persona."
        )

    # Si la plataforma de origen ya conoce a esta persona, ese alias manda:
    # es un match exacto, más fuerte que cualquier heurística.
    id_persona = None
    motivo = None
    if origen and id_en_origen:
        id_persona = dedup.buscar_por_alias(conn, origen, id_en_origen)
        motivo = "alias_origen" if id_persona else None

    if id_persona is None:
        resolucion = dedup.resolver(conn, datos)
        if resolucion.accion == dedup.REVISION:
            fila = db.una(
                conn,
                """
                insert into alta_en_revision (datos, candidatos, motivo)
                     values (%s, %s, %s)
                  returning id, creado_en
                """,
                (
                    json.dumps(
                        {
                            "persona": datos,
                            "consentimientos": consentimientos,
                            "origen": origen,
                            "id_en_origen": id_en_origen,
                            "panel_id": panel_id,
                        },
                        default=str,
                    ),
                    json.dumps(resolucion.candidatos),
                    resolucion.motivo,
                ),
            )
            return {
                "estado": "revision",
                "revision_id": fila["id"],
                "motivo": resolucion.motivo,
                "candidatos": resolucion.candidatos,
            }
        id_persona = resolucion.id_persona
        motivo = resolucion.motivo

    if id_persona is None:
        id_persona = _crear(conn, datos)
        estado = "creada"
        completados = []
    else:
        estado = "reutilizada"
        completados = _completar_faltantes(conn, id_persona, datos)

    if origen and id_en_origen:
        dedup.registrar_alias(conn, id_persona, origen, id_en_origen)

    otorgados = [
        consentimiento.otorgar(
            conn, id_persona, c["finalidad"], c["version_texto"], c.get("panel_id")
        )
        for c in consentimientos
    ]

    membresia = None
    if panel_id:
        from . import paneles  # import diferido: evita el ciclo de import

        membresia = paneles.agregar_miembro(conn, panel_id, id_persona)

    return {
        "estado": estado,
        "id_persona": str(id_persona),
        "motivo_dedup": motivo,
        "campos_completados": completados,
        "consentimientos": otorgados,
        "membresia": membresia,
    }


def _en_uso_por_otro(conn, campo, valor, id_persona):
    """¿Otra persona ya tiene este `documento` o `email`?

    Los dos tienen índice único, así que sin este chequeo el update
    explotaría con un error de base en vez de un mensaje entendible. Y
    además el choque es información: probablemente sean la misma persona.
    """
    sql = {
        "documento": "select id_persona, nombre from persona "
                     "where documento = %s and id_persona <> %s",
        "email": "select id_persona, nombre from persona "
                 "where lower(email) = lower(%s) and id_persona <> %s",
    }[campo]
    return db.una(conn, sql, (valor, id_persona))


def editar(conn, id_persona, cambios, actor=None):
    """Corrige los datos de una persona ya enrolada.

    Solo toca los campos presentes en `cambios`: lo que no viene, no se
    modifica. Un campo presente con valor vacío **borra** el dato (queda en
    null), que es la única forma de sacar un dato mal cargado.

    `documento` y `email` solo se pueden **completar** cuando están vacíos;
    una vez cargados no se cambian ni se borran (ver CLAVES_DE_DEDUP).

    No mueve consentimientos ni membresías: cada uno tiene su propio flujo.
    """
    actual = db.una(
        conn,
        f"select {', '.join(CAMPOS_PERSONA)} from persona where id_persona = %s",
        (id_persona,),
    )
    if not actual:
        raise NoEncontrado(f"No existe la persona {id_persona}.")

    desconocidos = sorted(set(cambios) - set(CAMPOS_EDITABLES))
    if desconocidos:
        raise DatosInvalidos(
            "Hay campos que no se pueden editar desde acá.",
            {"campos": desconocidos, "editables": list(CAMPOS_EDITABLES)},
        )

    # Las claves de dedup solo se pueden completar, no cambiar ni borrar.
    for campo in CLAVES_DE_DEDUP:
        if campo not in cambios:
            continue
        nuevo = cambios[campo]
        if isinstance(nuevo, str):
            nuevo = nuevo.strip() or None
        if actual[campo] in (None, ""):
            continue                      # está vacío: completar es válido
        if nuevo == actual[campo]:
            continue                      # el mismo valor: no es un cambio
        if nuevo is None:
            raise DatosInvalidos(
                f"No se puede borrar el {campo}: es una de las claves con las "
                f"que el sistema reconoce a la persona.",
                {"campo": campo, "valor_actual": actual[campo]},
            )
        raise DatosInvalidos(
            f"El {campo} ya está cargado y no se puede cambiar: es una de las "
            f"claves con las que el sistema reconoce a la persona. Se puede "
            f"completar cuando está vacío, no reemplazar.",
            {"campo": campo, "valor_actual": actual[campo]},
        )

    a_guardar = {}
    for campo, valor in cambios.items():
        if isinstance(valor, str):
            valor = valor.strip() or None
        if valor != actual[campo]:
            a_guardar[campo] = valor

    # Completar una clave con un valor que ya es de otra persona: se avisa
    # antes de escribir, y se dice con quién choca, porque lo más probable es
    # que sean la misma persona y haya que fusionarlas.
    for campo in CLAVES_DE_DEDUP:
        if a_guardar.get(campo):
            otro = _en_uso_por_otro(conn, campo, a_guardar[campo], id_persona)
            if otro:
                raise Conflicto(
                    f"Ya hay otro panelista con ese {campo}. Si es la misma "
                    f"persona, hay que unificar los dos registros.",
                    {
                        "campo": campo,
                        "id_persona_en_conflicto": str(otro["id_persona"]),
                        "nombre_en_conflicto": otro["nombre"],
                    },
                )

    if not a_guardar:
        return {"id_persona": str(id_persona), "campos_modificados": []}

    asignaciones = ", ".join(f"{c} = %s" for c in a_guardar)
    db.ejecutar(
        conn,
        f"update persona set {asignaciones} where id_persona = %s",
        tuple(a_guardar.values()) + (id_persona,),
    )
    return {
        "id_persona": str(id_persona),
        "campos_modificados": sorted(a_guardar),
    }


def agregar_alias(conn, id_persona, origen, id_en_origen):
    """Suma un alias de plataforma de campo a una persona ya enrolada.

    Si ese par (origen, id) ya está apuntando a OTRA persona, se rechaza:
    un mismo id de plataforma no puede referirse a dos panelistas, porque
    entonces la ingesta no sabría a quién asignarle la respuesta.
    """
    origen = (origen or "").strip()
    id_en_origen = (id_en_origen or "").strip()
    if not origen or not id_en_origen:
        raise DatosInvalidos("Hacen falta el origen y el id en el origen.")
    if not db.una(conn, "select 1 from persona where id_persona = %s", (id_persona,)):
        raise NoEncontrado(f"No existe la persona {id_persona}.")

    duenio = db.una(
        conn,
        "select id_persona from alias_origen where origen = %s and id_en_origen = %s",
        (origen, id_en_origen),
    )
    if duenio and str(duenio["id_persona"]) != str(id_persona):
        raise Conflicto(
            f"El id «{id_en_origen}» de {origen} ya está asignado a otro panelista.",
            {"id_persona_en_conflicto": str(duenio["id_persona"])},
        )

    dedup.registrar_alias(conn, id_persona, origen, id_en_origen)
    return {"origen": origen, "id_en_origen": id_en_origen}


def quitar_alias(conn, id_persona, origen, id_en_origen):
    """Saca un alias mal cargado.

    Las respuestas ya ingestadas no se tocan: viven del lado semántico
    referenciadas por `id_persona`, no por el alias.
    """
    borrados = db.ejecutar(
        conn,
        "delete from alias_origen "
        "where id_persona = %s and origen = %s and id_en_origen = %s",
        (id_persona, origen, id_en_origen),
    )
    if not borrados:
        raise NoEncontrado(
            f"La persona no tiene el alias {origen}/{id_en_origen}."
        )
    return {"origen": origen, "id_en_origen": id_en_origen, "estado": "borrado"}


def ficha(conn, id_persona):
    """Ficha de la bóveda: PII + demografía derivada + panel y consentimientos."""
    persona = db.una(
        conn,
        f"select id_persona, {', '.join(CAMPOS_PERSONA)}, creado_en "
        f"from persona where id_persona = %s",
        (id_persona,),
    )
    if not persona:
        raise NoEncontrado(f"No existe la persona {id_persona}.")

    demografia = db.una(
        conn,
        "select edad, tramo_etario from v_demografia where id_persona = %s",
        (id_persona,),
    )
    paneles_de = db.todas(
        conn,
        """
        select p.id, p.nombre, m.estado, m.fecha_alta, m.fecha_baja
          from membresia m join panel p on p.id = m.panel_id
         where m.id_persona = %s
         order by p.nombre
        """,
        (id_persona,),
    )
    alias = db.todas(
        conn,
        """
        select origen, id_en_origen
          from alias_origen
         where id_persona = %s
         order by origen, id_en_origen
        """,
        (id_persona,),
    )
    convocatorias = db.una(
        conn,
        """
        select count(*)::int as convocatorias,
               count(*) filter (where respondio)::int as respondidas,
               max(convocado_en) as ultimo_contacto
          from participacion
         where id_persona = %s
        """,
        (id_persona,),
    )

    return {
        "id_persona": str(persona["id_persona"]),
        "persona": {
            c: (persona[c].isoformat() if hasattr(persona[c], "isoformat") else persona[c])
            for c in CAMPOS_PERSONA
        },
        "creado_en": persona["creado_en"].isoformat(),
        "demografia": {
            "edad": demografia["edad"] if demografia else None,
            "tramo_etario": demografia["tramo_etario"] if demografia else None,
        },
        "paneles": [
            {
                "panel_id": p["id"],
                "nombre": p["nombre"],
                "estado": p["estado"],
                "fecha_alta": p["fecha_alta"].isoformat(),
                "fecha_baja": p["fecha_baja"].isoformat() if p["fecha_baja"] else None,
            }
            for p in paneles_de
        ],
        # Cómo la nombró cada plataforma de campo. Es lo que engancha sus
        # respuestas al ingestar, así que hay que poder verlo y verificarlo.
        "alias": [
            {"origen": a["origen"], "id_en_origen": a["id_en_origen"]} for a in alias
        ],
        "consentimientos": consentimiento.listar(conn, id_persona),
        "participacion": {
            "convocatorias": convocatorias["convocatorias"],
            "respondidas": convocatorias["respondidas"],
            "ultimo_contacto": (
                convocatorias["ultimo_contacto"].isoformat()
                if convocatorias["ultimo_contacto"]
                else None
            ),
        },
    }


def listar(conn, busqueda=None, panel_id=None, limite=50, desplazamiento=0):
    """Listado de panelistas para la grilla de administración."""
    condiciones = []
    params = []
    if busqueda:
        condiciones.append(
            "(p.nombre ilike %s or p.email ilike %s or p.documento ilike %s)"
        )
        patron = f"%{busqueda.strip()}%"
        params += [patron, patron, patron]
    if panel_id:
        condiciones.append(
            "exists (select 1 from membresia m where m.id_persona = p.id_persona "
            "and m.panel_id = %s and m.estado = 'activo')"
        )
        params.append(panel_id)
    donde = ("where " + " and ".join(condiciones)) if condiciones else ""

    total = db.una(
        conn, f"select count(*)::int as n from persona p {donde}", tuple(params)
    )["n"]
    filas = db.todas(
        conn,
        f"""
        select p.id_persona, p.nombre, p.documento, p.email, p.sexo, p.localidad,
               d.tramo_etario,
               exists (select 1 from consentimiento c
                        where c.id_persona = p.id_persona
                          and c.finalidad = 'contacto_participacion'
                          and c.estado = 'vigente') as consiente_contacto,
               exists (select 1 from consentimiento c
                        where c.id_persona = p.id_persona
                          and c.finalidad = 'uso_semantico'
                          and c.estado = 'vigente') as consiente_semantico,
               (select count(*) from membresia m
                 where m.id_persona = p.id_persona and m.estado = 'activo')::int as paneles
          from persona p left join v_demografia d on d.id_persona = p.id_persona
          {donde}
         order by p.creado_en desc
         limit %s offset %s
        """,
        tuple(params) + (limite, desplazamiento),
    )
    return {
        "total": total,
        "items": [
            {
                "id_persona": str(f["id_persona"]),
                "nombre": f["nombre"],
                "documento": f["documento"],
                "email": f["email"],
                "sexo": f["sexo"],
                "localidad": f["localidad"],
                "tramo_etario": f["tramo_etario"],
                "consiente_contacto": f["consiente_contacto"],
                "consiente_semantico": f["consiente_semantico"],
                "paneles": f["paneles"],
            }
            for f in filas
        ],
    }


def reidentificar(conn, ids_persona):
    """Traduce `id_persona` → datos de contacto, para una lista de ids.

    Es la operación que deshace la seudonimización: el resultado de una
    consulta semántica es una lista de tokens opacos, y para convocar a esa
    gente hay que saber quién es. Está acá, en la bóveda, porque es el único
    lugar donde puede estar.

    Dos cosas que le corresponden a quien la llama, no a esta función:
    exigir el permiso `reidentificar`, y registrar la traducción con
    `auditoria.registrar_reidentificacion`. La ruta hace las dos (ver
    `ruteo.py`); si alguien usa esto desde otro lado, le toca hacerlas.

    Los ids que no existen no son un error: se devuelven aparte. Una persona
    que se dio de baja después de la consulta desaparece de la bóveda, y eso
    es el sistema funcionando bien.
    """
    ids = [str(i) for i in dict.fromkeys(ids_persona or []) if i]
    if not ids:
        return {"items": [], "no_encontrados": [], "total": 0}

    filas = db.todas(
        conn,
        """
        select p.id_persona, p.nombre, p.documento, p.email, p.celular,
               p.contacto, d.sexo, d.localidad, d.tramo_etario,
               exists (select 1 from consentimiento c
                        where c.id_persona = p.id_persona
                          and c.finalidad = 'contacto_participacion'
                          and c.estado = 'vigente') as consiente_contacto,
               exists (select 1 from consentimiento c
                        where c.id_persona = p.id_persona
                          and c.finalidad = 'uso_semantico'
                          and c.estado = 'vigente') as consiente_semantico
          from persona p left join v_demografia d on d.id_persona = p.id_persona
         where p.id_persona = any(%s::uuid[])
        """,
        (ids,),
    )
    por_id = {
        str(f["id_persona"]): {
            "id_persona": str(f["id_persona"]),
            "nombre": f["nombre"],
            "documento": f["documento"],
            "email": f["email"],
            "celular": f["celular"],
            "contacto": f["contacto"],
            "sexo": f["sexo"],
            "localidad": f["localidad"],
            "tramo_etario": f["tramo_etario"],
            "consiente_contacto": f["consiente_contacto"],
            "consiente_semantico": f["consiente_semantico"],
        }
        for f in filas
    }
    return {
        "total": len(por_id),
        # Se respeta el orden en que llegaron los ids: es el del ranking.
        "items": [por_id[i] for i in ids if i in por_id],
        "no_encontrados": [i for i in ids if i not in por_id],
    }
