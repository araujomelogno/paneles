"""R3.9 — Ingesta desde archivo SAV (SPSS).

El Excel ancho obliga a tipear a mano códigos, textos de pregunta y mapeos de
etiquetas que el `.sav` ya trae adentro. Este módulo los lee y los precarga.

**El parseo corre en el backend, no en el navegador** (R3.9). No hay librería
cliente confiable para `.sav` y los archivos de campo pesan; además, subir el
archivo entero al backend es lo que permite validarlo antes de escribir nada.

Dos cosas que la precarga hace y que son el punto del requisito:

1. **Todo queda editable antes de confirmar.** La metadata de SPSS suele
   venir truncada —SPSS corta los variable labels— o críptica. Y lo que se
   embebe es el texto: si el texto es «P5_1», la consulta semántica sobre
   ese estudio no va a servir para nada. Por eso `analizar()` devuelve una
   propuesta, no un hecho, y señala explícitamente qué le parece dudoso.

2. **Nada se descarta en silencio.** Las variables que no se declaran, las
   filas que no mapean a nadie, los valores sin etiqueta: todo sale
   enumerado en el resultado. Una ingesta que procesa 900 de 1000 filas y
   dice «listo» es peor que una que falla.

**La base legal viaja en el archivo.** El modo «crear los individuos en esta
carga» entraba por una puerta distinta de la del alta manual, que exige
consentimiento (R1.1). Esa puerta ya no está abierta: para crear a alguien
hay que declarar, en el momento de importar, **qué variable del `.sav`
evidencia el consentimiento y qué valor cuenta como afirmativo**, para las
dos finalidades —pertenecer al panel y uso semántico—. Pueden ser la misma
variable; lo que no puede es faltar.

De ahí salen las tres reglas del modo:

1. **Sin evidencia declarada, no se importa.** No es un default que se pueda
   omitir: la ingesta se rechaza antes de leer una sola fila.
2. **Sin evidencia en la fila, no se crea la persona.** Quien no consintió
   el contacto no entra a la bóveda. No queda «pendiente», no queda a medias:
   no entra, y se informa cuántos y por qué.
3. **Cada finalidad se evalúa por separado.** Alguien puede consentir el
   contacto y no el uso semántico. Se le registra lo que consintió y nada
   más; el gate de la ingesta semántica hace el resto.
"""

import io

from . import db, dedup, ingesta
from .errores import DatosInvalidos

# Un variable label de SPSS con menos de esto es casi seguro un código, no
# una pregunta. No se rechaza: se marca para que alguien lo mire.
LARGO_SOSPECHOSO = 12
# SPSS trunca los variable labels a 256 caracteres; uno exactamente en el
# límite probablemente perdió texto.
LARGO_TRUNCADO_SPSS = 256

CERRADA = "cerrada"
ABIERTA = "abierta"
ESCALA = "escala"
NUMERICA = "numerica"


def _leer(archivo):
    """`archivo` puede ser una ruta o los bytes del `.sav`."""
    try:
        import pyreadstat  # noqa: F401
    except ImportError:  # pragma: no cover - depende del despliegue
        raise DatosInvalidos(
            "Falta la librería para leer archivos .sav (pyreadstat). "
            "Está declarada en functions/requirements.txt: si el error "
            "aparece en producción, la función se desplegó sin instalarla."
        )
    _exigir_pandas()
    if isinstance(archivo, (bytes, bytearray)):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sav", delete=False) as temporal:
            temporal.write(archivo)
            ruta = temporal.name
        try:
            return _read_sav(ruta)
        finally:
            import os

            os.unlink(ruta)
    if isinstance(archivo, io.IOBase):
        return _read_sav(archivo.name)
    return _read_sav(str(archivo))


def _exigir_pandas():
    """`pyreadstat` devuelve un DataFrame pero no instala pandas.

    Desde la 1.3 sus dependencias son `numpy` y `narwhals`, nada más. El
    resultado es un despliegue que instala bien, importa bien y recién falla
    al leer el primer archivo, con un mensaje que no menciona el despliegue:
    «You requested pandas as output_format but cannot import pandas». Se
    chequea antes de tocar el archivo para que el motivo sea el que es y no
    se confunda con un `.sav` ilegible.
    """
    try:
        import pandas  # noqa: F401
    except ImportError:  # pragma: no cover - depende del despliegue
        raise DatosInvalidos(
            "Falta pandas en la función: `pyreadstat` lo necesita para "
            "devolver los datos y desde la versión 1.3 no lo instala por su "
            "cuenta. Está declarado en functions/requirements.txt: si el "
            "error aparece en producción, la función se desplegó sin "
            "instalarlo. Redesplegá la función.",
            {"paquete": "pandas", "de_donde_sale": "functions/requirements.txt"},
        )


def diagnostico():
    """Si la función puede leer `.sav`, y si no, qué le falta.

    Existe por una falla concreta: la función desplegó bien, importó bien y
    reventó al leer el primer archivo porque `pyreadstat` no instala pandas.
    Nada en el despliegue lo anticipaba. Esta ruta lo dice antes, sin
    necesidad de subir un archivo de verdad.
    """
    faltan = []
    versiones = {}
    for paquete in ("pyreadstat", "pandas"):
        try:
            modulo = __import__(paquete)
            versiones[paquete] = getattr(modulo, "__version__", "desconocida")
        except ImportError:
            faltan.append(paquete)
    return {
        "puede_leer_sav": not faltan,
        "instalados": versiones,
        "faltan": faltan,
        "mensaje": (
            "La función puede leer archivos .sav."
            if not faltan else
            f"Falta(n) {', '.join(faltan)} en la función. Están declarados en "
            f"functions/requirements.txt: redesplegá la función."
        ),
    }


# SPSS graba en la cabecera con qué juego de caracteres se escribió el
# archivo, pero muchos exports de campo lo declaran mal. `pyreadstat` corta
# en cuanto aparece una ñ o una tilde, y en la práctica esos archivos son
# latin1. Se reintenta con esa codificación antes de darlos por perdidos.
CODIFICACIONES_DE_RESERVA = ("latin1", "cp1252")


def _read_sav(ruta):
    """`pyreadstat.read_sav` con los dos fallos que traen los `.sav` reales.

    Un archivo que el parser no puede leer es un problema **del archivo**, no
    del servidor: tiene que volver como 400 con el motivo, no como un 500
    opaco que obliga a alguien a mirar los logs para algo que el usuario
    puede resolver solo.
    """
    import pyreadstat

    try:
        return pyreadstat.read_sav(ruta)
    except MemoryError:
        # No es un archivo inválido: es que no entró en memoria. Se deja
        # subir para que el handler lo trate como lo que es.
        raise
    except Exception as primero:  # noqa: BLE001 - el parser tira de todo
        # Una dependencia que falta no es un archivo roto, y mandar a
        # reexportar desde SPSS por eso es peor que no decir nada.
        if "cannot import" in str(primero):
            _exigir_pandas()
            raise
        for codificacion in CODIFICACIONES_DE_RESERVA:
            try:
                return pyreadstat.read_sav(ruta, encoding=codificacion)
            except MemoryError:
                raise
            except Exception:  # noqa: BLE001, PERF203
                continue
        raise DatosInvalidos(
            f"No se pudo leer el archivo .sav: {primero}. Probá reexportarlo "
            f"desde SPSS con codificación UTF-8, o guardalo como .sav sin "
            f"comprimir (los .zsav comprimidos y los archivos truncados dan "
            f"este error).",
            {"error_del_parser": str(primero),
             "codificaciones_probadas": ["la del archivo", *CODIFICACIONES_DE_RESERVA]},
        ) from primero


def _tipo_de(codigo, meta, tiene_etiquetas):
    """Infiere el tipo desde `measure` y el tipo de dato, como pide R3.9.

    Es una inferencia y se corrige a mano: por eso viaja junto con el resto
    de la propuesta editable.
    """
    measure = (meta.variable_measure or {}).get(codigo, "")
    tipo_dato = (meta.readstat_variable_types or {}).get(codigo, "")
    if tiene_etiquetas:
        # Con etiquetas y measure ordinal o de escala, es una batería: el
        # tipo importa porque `calidad.baterias` agrupa por ahí para
        # detectar straightliners.
        return ESCALA if measure in ("scale", "ordinal") else CERRADA
    if tipo_dato in ("double", "float", "int8", "int16", "int32"):
        return NUMERICA
    return ABIERTA


def _avisos_de_texto(codigo, etiqueta):
    avisos = []
    if not etiqueta:
        avisos.append(
            "sin variable label en el archivo: el texto quedó igual al código, "
            "y es el texto lo que se vectoriza"
        )
        return avisos
    if len(etiqueta) < LARGO_SOSPECHOSO:
        avisos.append(
            "el variable label es muy corto: puede ser un código y no la "
            "pregunta"
        )
    if len(etiqueta) >= LARGO_TRUNCADO_SPSS:
        avisos.append(
            "el variable label llega al límite de SPSS: probablemente esté "
            "truncado"
        )
    return avisos


def analizar(archivo):
    """Lee el `.sav` y devuelve la metadata precargada, para editar y confirmar.

    No escribe nada: es la pantalla previa.
    """
    datos, meta = _leer(archivo)
    etiquetas = dict(zip(meta.column_names, meta.column_labels or []))
    value_labels = meta.variable_value_labels or {}

    variables = []
    for orden, codigo in enumerate(meta.column_names):
        crudas = value_labels.get(codigo) or {}
        # Los códigos de SPSS son floats («1.0»). Se normalizan a texto, que
        # es como los espera el resto de la ingesta, y sin el `.0` colgando.
        opciones = {_clave(k): str(v) for k, v in crudas.items()}
        etiqueta = (etiquetas.get(codigo) or "").strip()
        variables.append({
            "codigo": codigo,
            "texto": etiqueta or codigo,
            "texto_del_archivo": etiqueta or None,
            "tipo": _tipo_de(codigo, meta, bool(opciones)),
            "opciones": opciones or None,
            "orden": orden,
            "avisos": _avisos_de_texto(codigo, etiqueta),
            # Nada se declara solo: el analista elige qué se ingesta. Una
            # variable de control administrativo no tiene por qué embeberse.
            "incluir": False,
        })

    con_avisos = [v["codigo"] for v in variables if v["avisos"]]
    return {
        "filas": int(meta.number_rows),
        "variables": variables,
        "candidatas_a_id": _candidatas_a_id(datos, meta),
        "avisos": (
            [{
                "tipo": "textos_a_revisar",
                "variables": con_avisos,
                "mensaje": (
                    f"{len(con_avisos)} variable(s) tienen un texto dudoso "
                    f"—vacío, muy corto o truncado por SPSS—. El texto es lo "
                    f"que se vectoriza, así que conviene corregirlo antes de "
                    f"confirmar."
                ),
            }] if con_avisos else []
        ),
        "nota": (
            "Es una propuesta: nada se ingestó. Todos los campos son "
            "editables y ninguna variable se incluye hasta que se la marque."
        ),
    }


def _clave(valor):
    """«1.0» → «1». Los códigos de SPSS son numéricos y las opciones se
    indexan por texto."""
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _candidatas_a_id(datos, meta):
    """Variables que podrían ser el identificador: valores únicos y no nulos.

    Es una ayuda para la pantalla, no una decisión: el analista elige.
    """
    candidatas = []
    for codigo in meta.column_names:
        columna = datos[codigo]
        if columna.isna().any():
            continue
        if columna.nunique() == len(columna) and len(columna) > 0:
            candidatas.append(codigo)
    return candidatas


def filas_de(archivo, variables=None):
    """Las filas del `.sav` como dicts, con los códigos ya normalizados a
    texto para que `ingesta.despivotar` los resuelva contra las opciones."""
    datos, meta = _leer(archivo)
    incluidas = (
        {v["codigo"] for v in variables} if variables else set(meta.column_names)
    )
    filas = []
    for registro in datos.to_dict("records"):
        fila = {}
        for codigo, valor in registro.items():
            if codigo not in incluidas:
                continue
            if valor is None or (isinstance(valor, float) and valor != valor):
                continue  # NaN: no-respuesta, y una no-respuesta no se embebe
            fila[codigo] = _clave(valor) if isinstance(valor, float) else str(valor).strip()
        filas.append(fila)
    return filas


# ── Modo «crear los individuos en esta carga» ───────────────────────

# Qué variable del archivo va a qué campo de la bóveda. Todos van a
# `persona`, o sea a la bóveda y solo a la bóveda: el guardrail de R1.6
# sobre el store semántico sigue aplicando y lo prueba `pii.validar_sin_pii`
# en cada upsert semántico.
CAMPOS_PATRONIMICOS = (
    "nombre", "documento", "email", "celular", "fecha_nacimiento",
    "sexo", "localidad",
)

# Las dos finalidades que hay que evidenciar para crear a alguien desde un
# archivo. No hay una tercera y ninguna es opcional: sin contacto la persona
# no puede estar en el panel, y sin uso semántico sus respuestas no pueden
# ingestarse. Declarar las dos obliga a mirar el cuestionario de campo y
# decir dónde está cada una, que es exactamente lo que faltaba.
FINALIDADES_EVIDENCIABLES = ("contacto_participacion", "uso_semantico")


def _texto_comparable(valor):
    """Cómo se comparan los valores del archivo con lo declarado.

    Sin distinguir mayúsculas ni espacios sobrantes: en un export de campo
    conviven «Si», «SI » y «sí» para la misma respuesta, y rechazar a alguien
    por eso sería un error de importación disfrazado de falta de
    consentimiento. Los códigos numéricos ya vienen normalizados por
    `filas_de` («1.0» → «1»).
    """
    return str(valor).strip().lower() if valor is not None else ""


def normalizar_evidencia(evidencia):
    """Valida la declaración de evidencia de consentimiento y la deja canónica.

    Forma esperada, una entrada por finalidad:

        {"contacto_participacion": {"variable": "CONS1",
                                    "valor_afirmativo": "1",
                                    "version_texto": "campo-2026-09"},
         "uso_semantico":          {"variable": "CONS1", ...}}

    `valor_afirmativo` acepta un valor o una lista, porque un cuestionario
    puede codificar el sí de más de una forma. `variable` puede repetirse
    entre finalidades: una sola pregunta que cubre las dos es un caso
    normal, no un error.
    """
    if not isinstance(evidencia, dict) or not evidencia:
        raise DatosInvalidos(
            "Para crear individuos desde el archivo hay que declarar la "
            "evidencia de consentimiento: qué variable la contiene y qué "
            "valor cuenta como afirmativo, para cada finalidad.",
            {"finalidades_requeridas": list(FINALIDADES_EVIDENCIABLES),
             "ejemplo": {
                 "contacto_participacion": {
                     "variable": "CONS1", "valor_afirmativo": "1",
                     "version_texto": "consentimiento-campo-2026-09"},
                 "uso_semantico": {
                     "variable": "CONS1", "valor_afirmativo": "1",
                     "version_texto": "consentimiento-campo-2026-09"}}},
        )

    desconocidas = set(evidencia) - set(FINALIDADES_EVIDENCIABLES)
    if desconocidas:
        raise DatosInvalidos(
            f"Finalidad desconocida en la evidencia: {sorted(desconocidas)}.",
            {"finalidades_validas": list(FINALIDADES_EVIDENCIABLES)},
        )
    faltantes = [f for f in FINALIDADES_EVIDENCIABLES if f not in evidencia]
    if faltantes:
        raise DatosInvalidos(
            f"Falta declarar la evidencia de consentimiento para "
            f"{faltantes}. Si una misma variable cubre las dos finalidades, "
            f"declarala en las dos: lo que no puede es quedar sin declarar.",
            {"faltantes": faltantes},
        )

    normalizada = {}
    for finalidad in FINALIDADES_EVIDENCIABLES:
        regla = evidencia[finalidad] or {}
        variable = str(regla.get("variable") or "").strip()
        version = str(regla.get("version_texto") or "").strip()
        crudos = regla.get("valor_afirmativo")
        if isinstance(crudos, (str, int, float)) or crudos is None:
            crudos = [crudos]
        valores = {_texto_comparable(v) for v in crudos if v is not None
                   and str(v).strip() != ""}

        if not variable:
            raise DatosInvalidos(
                f"Falta la variable que evidencia «{finalidad}» en el archivo."
            )
        if not valores:
            raise DatosInvalidos(
                f"Falta el valor afirmativo de «{finalidad}»: sin él no se "
                f"puede saber qué respuesta cuenta como consentimiento."
            )
        if not version:
            raise DatosInvalidos(
                f"Falta la versión del texto consentido para «{finalidad}». "
                f"Es lo que hace demostrable qué aceptó la persona: tiene que "
                f"identificar el consentimiento que se leyó en campo."
            )
        normalizada[finalidad] = {
            "variable": variable,
            "valores_afirmativos": sorted(valores),
            "version_texto": version,
        }
    return normalizada


def _consintio(fila, regla):
    return _texto_comparable(fila.get(regla["variable"])) in set(
        regla["valores_afirmativos"]
    )


def _otorgar_si_falta(conn, id_persona, finalidad, version):
    """Registra el consentimiento salvo que ya esté el mismo, vigente.

    `consentimiento.otorgar` agrega una fila cada vez a propósito: el
    historial no se pisa. Pero re-ingestar el mismo archivo no es un
    consentimiento nuevo, es el mismo dato otra vez, y llenar la tabla de
    filas idénticas haría ilegible justamente el registro que tiene que
    servir de prueba.
    """
    from . import consentimiento as consent

    ya = db.una(
        conn,
        """
        select 1 from consentimiento
         where id_persona = %s and finalidad = %s and estado = 'vigente'
           and version_texto = %s
        """,
        (str(id_persona), finalidad, version),
    )
    if ya:
        return False
    consent.otorgar(conn, str(id_persona), finalidad, version)
    return True


def crear_individuos(conn_boveda, filas, mapeo, origen, columna_id,
                     evidencia_consentimiento, actor=None, panel_id=None):
    """Da de alta a la gente del archivo, con el dedup de R1.2 y con la
    evidencia de consentimiento que trae el propio archivo.

    `mapeo`: `{"nombre": "V1", "documento": "V2", ...}` — qué columna del
    archivo trae cada dato patronímico.

    `evidencia_consentimiento`: ver `normalizar_evidencia`. Es obligatorio.
    Quien no evidencia el consentimiento de contacto **no se crea**: la
    ingesta no puede ser una puerta lateral para poblar la bóveda sin base
    legal, que es justo lo que el alta manual (R1.1) impide.

    La ingesta tampoco puede crear duplicados que el alta manual habría
    evitado (R3.9), así que pasa por el mismo `dedup.resolver`. Un caso
    ambiguo va a la cola de revisión: no se fusiona, igual que en R1.2, y se
    lleva consigo el consentimiento evidenciado para que quien resuelva la
    revisión no tenga que volver al archivo.
    """
    import json

    desconocidos = set(mapeo) - set(CAMPOS_PATRONIMICOS)
    if desconocidos:
        raise DatosInvalidos(
            f"El mapeo tiene campos que no existen en la bóveda: "
            f"{sorted(desconocidos)}.",
            {"campos_validos": list(CAMPOS_PATRONIMICOS)},
        )
    if not columna_id:
        raise DatosInvalidos(
            "Falta indicar qué variable identifica a cada individuo en el "
            "archivo: sin eso no se pueden vincular las respuestas."
        )

    evidencia = normalizar_evidencia(evidencia_consentimiento)

    # Que la variable declarada exista en el archivo. Un typo acá haría que
    # ninguna fila evidencie consentimiento y que la importación termine con
    # cero altas y sin explicar por qué: se detecta antes de escribir nada.
    columnas = set()
    for fila in filas:
        columnas.update(fila)
    ausentes = sorted({
        r["variable"] for r in evidencia.values() if r["variable"] not in columnas
    })
    if ausentes and columnas:
        raise DatosInvalidos(
            f"La(s) variable(s) de consentimiento {ausentes} no están en el "
            f"archivo. Revisá el código exacto en el análisis del `.sav`.",
            {"variables_del_archivo": sorted(columnas)[:50]},
        )

    creados, reutilizados, en_revision = [], [], []
    sin_datos, sin_consentimiento = [], []
    otorgados = {finalidad: 0 for finalidad in FINALIDADES_EVIDENCIABLES}

    for fila in filas:
        id_en_origen = str(fila.get(columna_id) or "").strip()
        if not id_en_origen:
            continue

        consintio = {
            finalidad: _consintio(fila, regla)
            for finalidad, regla in evidencia.items()
        }

        # Sin consentimiento de contacto no hay persona. Es la regla que
        # cierra el agujero legal: no se crea, no se registra alias, no
        # queda nada a medias.
        if not consintio["contacto_participacion"]:
            regla = evidencia["contacto_participacion"]
            sin_consentimiento.append({
                "id_en_origen": id_en_origen,
                "variable": regla["variable"],
                "valor_en_el_archivo": fila.get(regla["variable"]),
                "valores_afirmativos": regla["valores_afirmativos"],
            })
            continue

        datos = {}
        for campo, columna in mapeo.items():
            valor = fila.get(columna)
            if valor is not None and str(valor).strip():
                datos[campo] = str(valor).strip()
        if not any(datos.get(c) for c in ("documento", "email", "nombre")):
            sin_datos.append(id_en_origen)
            continue

        consentimientos = [
            {"finalidad": finalidad,
             "version_texto": evidencia[finalidad]["version_texto"]}
            for finalidad in FINALIDADES_EVIDENCIABLES if consintio[finalidad]
        ]

        # Si ya conocemos ese id en esta plataforma, no hay nada que
        # resolver: pero el consentimiento que trae el archivo es evidencia
        # nueva y se registra igual.
        id_persona = dedup.buscar_por_alias(conn_boveda, origen, id_en_origen)
        if id_persona:
            for entrada in consentimientos:
                if _otorgar_si_falta(conn_boveda, id_persona,
                                     entrada["finalidad"], entrada["version_texto"]):
                    otorgados[entrada["finalidad"]] += 1
            reutilizados.append({
                "id_en_origen": id_en_origen, "id_persona": str(id_persona),
                "motivo": "alias_origen",
                "consintio": consintio,
            })
            continue

        resolucion = dedup.resolver(conn_boveda, datos)
        if resolucion.accion == dedup.REVISION:
            fila_revision = db.una(
                conn_boveda,
                """
                insert into alta_en_revision (datos, candidatos, motivo)
                     values (%s, %s, %s)
                  returning id
                """,
                (
                    json.dumps({
                        "persona": datos,
                        # El consentimiento evidenciado viaja con la revisión:
                        # al resolverla, la persona se crea con lo que el
                        # archivo probó, sin volver a abrirlo.
                        "consentimientos": consentimientos,
                        "origen": origen, "id_en_origen": id_en_origen,
                        "panel_id": panel_id,
                        "nota": (
                            f"Alta por ingesta SAV. Consentimiento evidenciado "
                            f"en la variable "
                            f"«{evidencia['contacto_participacion']['variable']}» "
                            f"del archivo."
                        ),
                    }, default=str),
                    json.dumps(resolucion.candidatos),
                    resolucion.motivo,
                ),
            )
            en_revision.append({
                "id_en_origen": id_en_origen,
                "revision_id": fila_revision["id"],
                "motivo": resolucion.motivo,
                "candidatos": resolucion.candidatos,
                "consintio": consintio,
            })
            continue

        if resolucion.accion == dedup.REUTILIZA:
            id_persona = str(resolucion.id_persona)
            dedup.registrar_alias(conn_boveda, id_persona, origen, id_en_origen)
            for entrada in consentimientos:
                if _otorgar_si_falta(conn_boveda, id_persona,
                                     entrada["finalidad"], entrada["version_texto"]):
                    otorgados[entrada["finalidad"]] += 1
            reutilizados.append({
                "id_en_origen": id_en_origen, "id_persona": id_persona,
                "motivo": resolucion.motivo,
                "consintio": consintio,
            })
            continue

        # Alta nueva. La persona nace `activa`: el archivo probó su base
        # legal, así que no hay nada pendiente que regularizar.
        campos = [c for c in CAMPOS_PATRONIMICOS if c in datos]
        nueva = db.una(
            conn_boveda,
            f"""
            insert into persona ({', '.join(campos)})
            values ({', '.join(['%s'] * len(campos))})
            returning id_persona
            """,
            tuple(datos[c] for c in campos),
        )
        id_persona = str(nueva["id_persona"])
        dedup.registrar_alias(conn_boveda, id_persona, origen, id_en_origen)
        for entrada in consentimientos:
            if _otorgar_si_falta(conn_boveda, id_persona,
                                 entrada["finalidad"], entrada["version_texto"]):
                otorgados[entrada["finalidad"]] += 1
        if panel_id:
            db.ejecutar(
                conn_boveda,
                "insert into membresia (panel_id, id_persona) values (%s, %s) "
                "on conflict do nothing",
                (panel_id, id_persona),
            )
        creados.append({
            "id_en_origen": id_en_origen, "id_persona": id_persona,
            "consintio": consintio,
        })

    conn_boveda.commit()

    solo_contacto = [
        c for c in creados + reutilizados if not c["consintio"]["uso_semantico"]
    ]
    return {
        "creados": creados,
        "reutilizados": reutilizados,
        "en_revision": en_revision,
        "sin_datos_suficientes": sin_datos,
        "sin_consentimiento": sin_consentimiento,
        "evidencia_declarada": evidencia,
        "consentimientos_registrados": otorgados,
        "resumen": {
            "creados": len(creados),
            "reutilizados": len(reutilizados),
            "en_revision": len(en_revision),
            "sin_datos_suficientes": len(sin_datos),
            "sin_consentimiento": len(sin_consentimiento),
        },
        "aviso_sin_consentimiento": (
            {
                "personas": len(sin_consentimiento),
                "variable": evidencia["contacto_participacion"]["variable"],
                "valores_afirmativos":
                    evidencia["contacto_participacion"]["valores_afirmativos"],
                "mensaje": (
                    f"{len(sin_consentimiento)} fila(s) no evidencian el "
                    f"consentimiento de contacto en la variable "
                    f"«{evidencia['contacto_participacion']['variable']}»: "
                    f"no se creó ninguna persona con ellas y sus respuestas "
                    f"no se van a poder vincular a nadie. Si eso es un error "
                    f"de codificación del archivo, corregí el valor "
                    f"afirmativo declarado y volvé a importar."
                ),
            } if sin_consentimiento else None
        ),
        "aviso_sin_uso_semantico": (
            {
                "personas": len(solo_contacto),
                "mensaje": (
                    f"{len(solo_contacto)} persona(s) consintieron el contacto "
                    f"pero no el uso semántico. Están en el panel y se las "
                    f"puede convocar; sus respuestas no se ingestan al store "
                    f"semántico, que es el gate de R1.3 haciendo su trabajo."
                ),
            } if solo_contacto else None
        ),
    }


def regularizar(conn, ids_persona, finalidad, version_texto, actor=None):
    """Saca de `pendiente_consentimiento` a quienes ya tienen base legal.

    Registra el consentimiento y activa a la persona, en una sola operación,
    porque separarlas dejaría el estado y el consentimiento en desacuerdo.

    **Es transitoria.** Desde que la evidencia de consentimiento es
    obligatoria en la importación, la ingesta por SAV ya no produce personas
    en `pendiente_consentimiento`: o el archivo prueba el consentimiento y la
    persona nace activa, o no se crea. Esta función queda para las que se
    hayan creado con la versión anterior; cuando no quede ninguna, se puede
    retirar junto con el estado.
    """
    from . import consentimiento as consent

    consent._validar_finalidad(finalidad)
    if not (version_texto or "").strip():
        raise DatosInvalidos("Hace falta la versión del texto consentido.")
    regularizadas = []
    for id_persona in ids_persona:
        fila = db.una(
            conn,
            "select estado from persona where id_persona = %s", (str(id_persona),),
        )
        if not fila:
            continue
        consent.otorgar(conn, str(id_persona), finalidad, version_texto.strip())
        db.ejecutar(
            conn,
            "update persona set estado = 'activa' where id_persona = %s",
            (str(id_persona),),
        )
        regularizadas.append(str(id_persona))
    conn.commit()
    return {"regularizadas": regularizadas, "finalidad": finalidad,
            "version_texto": version_texto.strip()}
