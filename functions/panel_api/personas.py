"""R1.1 / R1.2 — Alta de panelista en la bóveda y ficha de persona.

`id_persona` (uuid aleatorio y opaco) lo emite el alta y es la clave de la
persona en toda la plataforma. La ingesta semántica lo referencia; no crea
personas.
"""

import json

from . import consentimiento, db, dedup
from .errores import DatosInvalidos, NoEncontrado

# Columnas de PII que acepta el alta. Se listan explícitamente para que
# nada entre por accidente y para que el orden de escritura sea evidente.
CAMPOS_PERSONA = (
    "documento", "nombre", "sexo", "fecha_nacimiento", "localidad",
    "email", "celular", "contacto", "observaciones",
)


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
