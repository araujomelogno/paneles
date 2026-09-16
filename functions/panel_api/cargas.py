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


def _serializar(fila):
    return {
        "id": fila["id"],
        "nombre": fila["nombre"],
        "descripcion": fila["descripcion"],
        "ref_estudio": str(fila["ref_estudio"]),
        "creado_en": fila["creado_en"].isoformat(),
        "creado_por": fila["creado_por"],
    }


def crear(conn, nombre, descripcion=None, actor=None):
    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosInvalidos(
            "La carga necesita un nombre: es lo que después identifica de "
            "dónde salieron esos datos (por ejemplo «Ómnibus agosto 2026»)."
        )
    fila = db.una(
        conn,
        """
        insert into carga (nombre, descripcion, creado_por)
             values (%s, %s, %s)
          returning id, nombre, descripcion, ref_estudio, creado_en, creado_por
        """,
        (nombre, (descripcion or "").strip() or None,
         getattr(actor, "uid", None) or (actor if isinstance(actor, str) else None)),
    )
    return _serializar(fila)


def obtener(conn, carga_id):
    fila = db.una(
        conn,
        "select id, nombre, descripcion, ref_estudio, creado_en, creado_por "
        "from carga where id = %s",
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
            "select id, nombre, descripcion, ref_estudio, creado_en, creado_por "
            "from carga order by creado_en desc",
        )
    ]


def ingestar(conn_boveda, conn_semantica, carga_id, preguntas, filas,
             columna_id="id_en_origen", origen=None, proveedor=None,
             demograficas=None, tipo_identificador=None):
    """Incorpora los individuos del archivo y sus respuestas. Sin panel.

    Es `encuestas.ingestar` menos las dos cosas que este flujo evita: no
    incorpora a ningún panel (R3.9.a) y no registra participación (R3.9.b).
    No hubo convocatoria ni encuesta fieldeada, así que no hay nada que
    registrar de esas dos.
    """
    carga = obtener(conn_boveda, carga_id)
    demograficas = sav.normalizar_demograficas(demograficas)

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
    )
    resultado["carga_id"] = carga_id
    resultado["tipo_identificador"] = tipo
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
                     opciones_por_variable=None):
    """Da de alta a la gente del archivo, sin meterla en ningún panel.

    Es `sav.crear_individuos` con dos diferencias, las dos deliberadas:

    - **`panel_id` no existe como parámetro.** No se puede pasar por error.
    - **La finalidad obligatoria es `uso_semantico`** y no el contacto (ver
      `FINALIDAD_OBLIGATORIA`).
    """
    return sav.crear_individuos(
        conn_boveda, filas, mapeo, origen=origen, columna_id=columna_id,
        evidencia_consentimiento=evidencia_consentimiento, actor=actor,
        panel_id=None, opciones_por_variable=opciones_por_variable,
        finalidad_obligatoria=FINALIDAD_OBLIGATORIA,
    )


def sin_panel(conn, limite=50, desplazamiento=0):
    """Los individuos que no son miembros de ningún panel.

    R3.13.e. El atajo del filtro de la pantalla de panelistas; la consulta
    vive en `personas.listar`.
    """
    return personas.listar(
        conn, limite=limite, desplazamiento=desplazamiento, sin_panel=True)
