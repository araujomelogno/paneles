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

> **Pendiente legal (spec §11).** El modo «crear los individuos en esta
> carga» entra por una puerta distinta de la del alta manual, que exige
> consentimiento (R1.1). Mientras no haya una definición de base legal, este
> módulo crea esas personas en estado `pendiente_consentimiento`: existen en
> la bóveda, pero el muestreo las excluye y no pueden ser convocadas. Es la
> opción conservadora de las dos que plantea la spec. Si más adelante se
> decide que el archivo puede traer evidencia de consentimiento, el cambio
> es acá y en `personas.alta`.
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
        import pyreadstat
    except ImportError:  # pragma: no cover - depende del despliegue
        raise DatosInvalidos(
            "Falta la librería para leer archivos .sav (pyreadstat). "
            "Está declarada en functions/requirements.txt: si el error "
            "aparece en producción, la función se desplegó sin instalarla."
        )
    if isinstance(archivo, (bytes, bytearray)):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sav", delete=False) as temporal:
            temporal.write(archivo)
            ruta = temporal.name
        try:
            return pyreadstat.read_sav(ruta)
        finally:
            import os

            os.unlink(ruta)
    if isinstance(archivo, io.IOBase):
        return pyreadstat.read_sav(archivo.name)
    return pyreadstat.read_sav(str(archivo))


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


def crear_individuos(conn_boveda, filas, mapeo, origen, columna_id, actor=None,
                     panel_id=None):
    """Da de alta a la gente del archivo, con el dedup de R1.2.

    `mapeo`: `{"nombre": "V1", "documento": "V2", ...}` — qué columna del
    archivo trae cada dato patronímico.

    La ingesta no puede crear duplicados que el alta manual habría evitado
    (R3.9), así que pasa por el mismo `dedup.resolver`. Un caso ambiguo va a
    la cola de revisión: no se fusiona, igual que en R1.2.
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

    creados, reutilizados, en_revision, sin_datos = [], [], [], []
    for fila in filas:
        id_en_origen = str(fila.get(columna_id) or "").strip()
        if not id_en_origen:
            continue
        datos = {}
        for campo, columna in mapeo.items():
            valor = fila.get(columna)
            if valor is not None and str(valor).strip():
                datos[campo] = str(valor).strip()
        if not any(datos.get(c) for c in ("documento", "email", "nombre")):
            sin_datos.append(id_en_origen)
            continue

        # Si ya conocemos ese id en esta plataforma, no hay nada que resolver.
        id_persona = dedup.buscar_por_alias(conn_boveda, origen, id_en_origen)
        if id_persona:
            reutilizados.append(
                {"id_en_origen": id_en_origen, "id_persona": str(id_persona),
                 "motivo": "alias_origen"}
            )
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
                        "persona": datos, "consentimientos": [],
                        "origen": origen, "id_en_origen": id_en_origen,
                        "panel_id": panel_id,
                        "nota": (
                            "Alta por ingesta SAV. Sin consentimiento "
                            "registrado: al resolverla, la persona queda "
                            "pendiente de consentimiento."
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
            })
            continue

        if resolucion.accion == dedup.REUTILIZA:
            id_persona = str(resolucion.id_persona)
            dedup.registrar_alias(conn_boveda, id_persona, origen, id_en_origen)
            reutilizados.append({
                "id_en_origen": id_en_origen, "id_persona": id_persona,
                "motivo": resolucion.motivo,
            })
            continue

        # Alta nueva, en estado pendiente de consentimiento: la base legal de
        # esta puerta es lo que la spec deja abierto (§11).
        columnas = [c for c in CAMPOS_PATRONIMICOS if c in datos]
        nueva = db.una(
            conn_boveda,
            f"""
            insert into persona ({', '.join(columnas)}, estado)
            values ({', '.join(['%s'] * len(columnas))}, 'pendiente_consentimiento')
            returning id_persona
            """,
            tuple(datos[c] for c in columnas),
        )
        id_persona = str(nueva["id_persona"])
        dedup.registrar_alias(conn_boveda, id_persona, origen, id_en_origen)
        if panel_id:
            db.ejecutar(
                conn_boveda,
                "insert into membresia (panel_id, id_persona) values (%s, %s) "
                "on conflict do nothing",
                (panel_id, id_persona),
            )
        creados.append({"id_en_origen": id_en_origen, "id_persona": id_persona})

    conn_boveda.commit()
    return {
        "creados": creados,
        "reutilizados": reutilizados,
        "en_revision": en_revision,
        "sin_datos_suficientes": sin_datos,
        "resumen": {
            "creados": len(creados),
            "reutilizados": len(reutilizados),
            "en_revision": len(en_revision),
            "sin_datos_suficientes": len(sin_datos),
        },
        "aviso_consentimiento": (
            {
                "personas": len(creados),
                "estado": "pendiente_consentimiento",
                "mensaje": (
                    f"Se crearon {len(creados)} personas sin consentimiento "
                    f"registrado. Quedan en «pendiente_consentimiento»: el "
                    f"muestreo las excluye y no pueden ser convocadas hasta "
                    f"que se registre una base legal. Regularizarlas es "
                    f"registrarles el consentimiento como en un alta normal."
                ),
            } if creados else None
        ),
    }


def regularizar(conn, ids_persona, finalidad, version_texto, actor=None):
    """Saca de `pendiente_consentimiento` a quienes ya tienen base legal.

    Registra el consentimiento y activa a la persona, en una sola operación,
    porque separarlas dejaría el estado y el consentimiento en desacuerdo.
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
