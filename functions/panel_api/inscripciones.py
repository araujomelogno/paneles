"""R3.7 — Landing de auto-registro.

Hasta ahora el panel solo crecía por alta manual de un operador. Esta es la
vía pública: una persona se inscribe y **da su propio consentimiento**, que
es distinto —y mejor— que un operador registrando que alguien consintió.

Tres decisiones que valen la pena explicar, porque es la única superficie de
la plataforma sin login:

1. **Una inscripción no es una persona.** Se guarda en `inscripcion` y no en
   `persona`. Eso hace que un pendiente no pueda ser convocado ni aparecer en
   una consulta semántica, no por una condición que alguien tiene que
   acordarse de escribir en cada consulta, sino porque no existe todavía como
   panelista. La aprobación es la que lo crea, y es un acto humano.

2. **La respuesta pública no dice nada.** Ni si la persona ya estaba
   inscripta, ni si su documento ya existe, ni si el caso fue a revisión. Un
   formulario que contesta «ya estás registrado» es un oráculo para averiguar
   quién pertenece al panel probando documentos. Adentro, la resolución de
   identidad de R1.2 corre igual y su resultado queda guardado; afuera, todos
   los envíos válidos se ven iguales.

3. **El texto de consentimiento se versiona, no se edita.** Cada versión es
   una fila nueva. Cambiar el texto no puede alterar lo que ya consintió
   alguien que se inscribió el mes pasado: lo que esa persona aceptó es un
   documento concreto, y tiene que seguir siendo recuperable tal cual.

> **Pendiente legal (spec §11).** El texto de consentimiento de la landing lo
> tiene que revisar el DPO. El sistema lo trata como dato: se carga por API,
> se versiona y se muestra el vigente. No hay ningún texto legal escrito acá
> ni sembrado en la migración, justamente para que nadie publique la landing
> creyendo que el que trae el código sirve.
"""

import json

from . import consentimiento as consent, db, dedup
from .errores import DatosInvalidos, NoEncontrado

PENDIENTE = "pendiente"
APROBADA = "aprobada"
RECHAZADA = "rechazada"
ESTADOS = (PENDIENTE, APROBADA, RECHAZADA)

CAMPOS = (
    "nombre", "documento", "email", "celular", "fecha_nacimiento",
    "sexo", "localidad",
)

# Lo mismo para todos los envíos válidos, pase lo que pase adentro.
ACUSE = {
    "estado": "recibida",
    "mensaje": (
        "Recibimos tu inscripción. Vamos a revisarla y te vamos a contactar. "
        "No hace falta que la envíes de nuevo."
    ),
}


# ── Textos de consentimiento, versionados ───────────────────────────

def texto_vigente(conn, finalidad=consent.CONTACTO):
    """La última versión activa de esa finalidad, o None si no hay ninguna."""
    fila = db.una(
        conn,
        """
        select id, finalidad, version, cuerpo, creado_en
          from texto_consentimiento
         where finalidad = %s and activo
         order by creado_en desc, id desc
         limit 1
        """,
        (finalidad,),
    )
    if not fila:
        return None
    return {
        "id": fila["id"], "finalidad": fila["finalidad"],
        "version": fila["version"], "cuerpo": fila["cuerpo"],
        "creado_en": fila["creado_en"].isoformat(),
    }


def listar_textos(conn, finalidad=None):
    filas = db.todas(
        conn,
        """
        select id, finalidad, version, cuerpo, activo, creado_por, creado_en
          from texto_consentimiento
         where (%s::text is null or finalidad = %s::text)
         order by finalidad, creado_en desc, id desc
        """,
        (finalidad, finalidad),
    )
    return [
        {
            "id": f["id"], "finalidad": f["finalidad"], "version": f["version"],
            "cuerpo": f["cuerpo"], "activo": f["activo"],
            "creado_por": f["creado_por"], "creado_en": f["creado_en"].isoformat(),
        }
        for f in filas
    ]


def publicar_texto(conn, finalidad, version, cuerpo, actor=None):
    """Publica una versión nueva. Nunca reescribe una existente.

    Si se pudiera editar el cuerpo, el `version_texto` que guarda cada
    consentimiento dejaría de identificar un documento y pasaría a ser una
    etiqueta sin contenido estable. Eso destruye el valor probatorio de todo
    el registro de consentimiento, no solo el de la landing.
    """
    consent._validar_finalidad(finalidad)
    version = (version or "").strip()
    cuerpo = (cuerpo or "").strip()
    if not version:
        raise DatosInvalidos("La versión necesita un identificador, por ejemplo «2026-09».")
    if not cuerpo:
        raise DatosInvalidos("El texto de consentimiento no puede estar vacío.")

    ya = db.una(
        conn,
        "select id, cuerpo from texto_consentimiento "
        " where finalidad = %s and version = %s",
        (finalidad, version),
    )
    if ya:
        raise DatosInvalidos(
            f"La versión «{version}» de {finalidad} ya existe y no se puede "
            f"reescribir: quien la consintió aceptó ese texto. Publicá una "
            f"versión nueva.",
            {"version_existente": version},
        )

    fila = db.una(
        conn,
        """
        insert into texto_consentimiento (finalidad, version, cuerpo, creado_por)
        values (%s, %s, %s, %s)
        returning id, creado_en
        """,
        (finalidad, version, cuerpo, getattr(actor, "uid", None)),
    )
    conn.commit()
    return {
        "id": fila["id"], "finalidad": finalidad, "version": version,
        "cuerpo": cuerpo, "activo": True,
        "creado_en": fila["creado_en"].isoformat(),
    }


# ── Inscripción pública ─────────────────────────────────────────────

def _limpiar(datos):
    limpio = {}
    for campo in CAMPOS:
        valor = datos.get(campo)
        if valor is None:
            continue
        valor = str(valor).strip()
        if valor:
            limpio[campo] = valor
    return limpio


def formulario(conn, finalidades=None):
    """Lo que la landing necesita para dibujarse: el texto vigente y qué pide.

    Es público y no toca ninguna tabla con datos de panelistas.
    """
    finalidades = tuple(finalidades or (consent.CONTACTO,))
    textos = []
    for finalidad in finalidades:
        texto = texto_vigente(conn, finalidad)
        if texto:
            textos.append(texto)
    return {
        "campos": list(CAMPOS),
        "obligatorios": ["nombre", "email"],
        "textos": textos,
        # Sin texto publicado la landing no puede pedir consentimiento, y sin
        # consentimiento no hay inscripción posible. Se dice explícito para
        # que el error no aparezca recién cuando alguien intente enviar.
        "puede_recibir": bool(textos),
    }


def inscribir(conn, cuerpo):
    """Recibe una inscripción del formulario público.

    No crea ninguna persona: crea una solicitud. Y devuelve siempre lo mismo.
    """
    datos = _limpiar(cuerpo.get("persona") or cuerpo)
    acepto = cuerpo.get("acepto_consentimiento")
    finalidades = cuerpo.get("finalidades") or [consent.CONTACTO]
    if isinstance(finalidades, str):
        finalidades = [finalidades]
    for finalidad in finalidades:
        consent._validar_finalidad(finalidad)

    if not datos.get("nombre"):
        raise DatosInvalidos("Necesitamos tu nombre.")
    if not datos.get("email"):
        raise DatosInvalidos("Necesitamos un correo para poder contactarte.")

    # R3.7 — sin aceptar el consentimiento, no hay inscripción. Va antes que
    # cualquier escritura: lo que no se puede es guardar los datos «mientras
    # tanto».
    if acepto is not True:
        raise DatosInvalidos(
            "Para inscribirte necesitás aceptar el consentimiento.",
            {"campo": "acepto_consentimiento"},
        )

    texto = texto_vigente(conn, finalidades[0])
    if not texto:
        raise DatosInvalidos(
            "El formulario todavía no está habilitado: no hay un texto de "
            "consentimiento publicado."
        )
    version = (cuerpo.get("version_texto") or "").strip() or texto["version"]
    if version != texto["version"]:
        # El formulario se cargó con una versión y se publicó otra en el
        # medio. Aceptarlo registraría un consentimiento a un texto que la
        # persona no leyó.
        raise DatosInvalidos(
            "El texto de consentimiento cambió mientras completabas el "
            "formulario. Volvé a cargarlo y revisá la versión nueva.",
            {"version_vigente": texto["version"]},
        )

    # La resolución de identidad de R1.2, igual que un alta interna. El
    # resultado se guarda para que quien apruebe lo vea; afuera no se dice.
    resolucion = dedup.resolver(conn, datos)
    id_previa = None
    if resolucion.accion == dedup.REUTILIZA:
        id_previa = str(resolucion.id_persona)

    db.ejecutar(
        conn,
        """
        insert into inscripcion
               (nombre, documento, email, celular, fecha_nacimiento, sexo,
                localidad, finalidades, version_texto, resolucion,
                id_persona_previa, panel_id, origen)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict do nothing
        """,
        (datos["nombre"], datos.get("documento"), datos.get("email"),
         datos.get("celular"), datos.get("fecha_nacimiento"), datos.get("sexo"),
         datos.get("localidad"), list(finalidades), version,
         resolucion.accion, id_previa, cuerpo.get("panel_id"),
         (cuerpo.get("origen") or "landing")),
    )
    conn.commit()
    # `on conflict do nothing` cubre el reenvío del mismo correo con una
    # inscripción pendiente. Se responde igual: quien reenvía no tiene por
    # qué enterarse de que ya había una.
    return dict(ACUSE)


# ── Bandeja de aprobación ───────────────────────────────────────────

def _serializar(fila):
    return {
        "id": fila["id"], "nombre": fila["nombre"],
        "documento": fila["documento"], "email": fila["email"],
        "celular": fila["celular"],
        "fecha_nacimiento": (
            fila["fecha_nacimiento"].isoformat() if fila["fecha_nacimiento"] else None
        ),
        "sexo": fila["sexo"], "localidad": fila["localidad"],
        "finalidades": list(fila["finalidades"]),
        "version_texto": fila["version_texto"],
        "acepto_en": fila["acepto_en"].isoformat(),
        "resolucion": fila["resolucion"],
        "id_persona_previa": (
            str(fila["id_persona_previa"]) if fila["id_persona_previa"] else None
        ),
        "estado": fila["estado"],
        "id_persona": str(fila["id_persona"]) if fila["id_persona"] else None,
        "panel_id": fila["panel_id"],
        "resuelto_por": fila["resuelto_por"],
        "resuelto_en": fila["resuelto_en"].isoformat() if fila["resuelto_en"] else None,
        "motivo_rechazo": fila["motivo_rechazo"],
        "origen": fila["origen"],
        "creado_en": fila["creado_en"].isoformat(),
    }


def listar(conn, estado=PENDIENTE, limite=200):
    filas = db.todas(
        conn,
        """
        select * from inscripcion
         where (%s::text is null or estado = %s::text)
         order by creado_en desc, id desc
         limit %s
        """,
        (estado, estado, int(limite)),
    )
    return [_serializar(f) for f in filas]


def obtener(conn, inscripcion_id):
    fila = db.una(conn, "select * from inscripcion where id = %s", (inscripcion_id,))
    if not fila:
        raise NoEncontrado(f"No existe la inscripción {inscripcion_id}.")
    return _serializar(fila)


def aprobar(conn, inscripcion_id, actor, panel_id=None):
    """Convierte una inscripción en panelista.

    Recién acá se crea la persona, y con ella el consentimiento que el
    titular dio en su momento, con la versión que aceptó —no la vigente hoy—.
    """
    from . import personas

    fila = db.una(
        conn, "select * from inscripcion where id = %s for update", (inscripcion_id,)
    )
    if not fila:
        raise NoEncontrado(f"No existe la inscripción {inscripcion_id}.")
    if fila["estado"] != PENDIENTE:
        raise DatosInvalidos(
            f"La inscripción {inscripcion_id} ya está «{fila['estado']}».",
            {"estado": fila["estado"]},
        )

    # `fecha_nacimiento` es una columna `date` y vuelve como `datetime.date`;
    # el dedup y el alta trabajan con texto. Convertir acá y no allá evita
    # que cada consumidor tenga que acordarse.
    datos = {}
    for campo in CAMPOS:
        valor = fila[campo]
        if valor is None:
            continue
        datos[campo] = valor.isoformat() if hasattr(valor, "isoformat") else valor
    cuerpo = {
        "persona": datos,
        # La versión que aceptó el titular, no la de hoy: es lo que hace que
        # cambiar el texto no altere consentimientos anteriores (R3.7).
        "consentimientos": [
            {"finalidad": f, "version_texto": fila["version_texto"]}
            for f in fila["finalidades"]
        ],
        "panel_id": panel_id if panel_id is not None else fila["panel_id"],
        "origen": fila["origen"],
    }
    resultado = personas.alta(conn, cuerpo, actor=actor)

    if resultado["estado"] == "revision":
        # El caso ambiguo no se fusiona solo (R1.2): queda en la cola de
        # revisión y la inscripción sigue pendiente, esperándola.
        conn.commit()
        return {
            "estado": "revision",
            "inscripcion_id": inscripcion_id,
            "revision_id": resultado["revision_id"],
            "motivo": resultado["motivo"],
            "candidatos": resultado["candidatos"],
            "mensaje": (
                "La inscripción coincide de forma ambigua con alguien que ya "
                "está en el panel, así que no se aprobó: quedó en la cola de "
                "revisión de altas. Resolvé esa revisión y volvé a aprobarla."
            ),
        }

    db.ejecutar(
        conn,
        """
        update inscripcion
           set estado = 'aprobada', id_persona = %s, resuelto_por = %s,
               resuelto_en = now(), panel_id = coalesce(%s, panel_id)
         where id = %s
        """,
        (resultado["id_persona"], getattr(actor, "uid", None), panel_id,
         inscripcion_id),
    )
    conn.commit()
    return {
        "estado": "aprobada",
        "inscripcion_id": inscripcion_id,
        "id_persona": resultado["id_persona"],
        "persona": resultado["estado"],   # 'creada' | 'reutilizada'
        "panel_id": cuerpo["panel_id"],
    }


def rechazar(conn, inscripcion_id, actor, motivo=None):
    fila = db.una(
        conn, "select estado from inscripcion where id = %s", (inscripcion_id,)
    )
    if not fila:
        raise NoEncontrado(f"No existe la inscripción {inscripcion_id}.")
    if fila["estado"] != PENDIENTE:
        raise DatosInvalidos(
            f"La inscripción {inscripcion_id} ya está «{fila['estado']}»."
        )
    db.ejecutar(
        conn,
        """
        update inscripcion
           set estado = 'rechazada', resuelto_por = %s, resuelto_en = now(),
               motivo_rechazo = %s
         where id = %s
        """,
        (getattr(actor, "uid", None), (motivo or "").strip() or None, inscripcion_id),
    )
    conn.commit()
    return {"estado": "rechazada", "inscripcion_id": inscripcion_id,
            "motivo": motivo}
