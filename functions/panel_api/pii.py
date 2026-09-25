"""Guardrail R1.6 — la PII nunca se escribe en el store semántico.

Dos controles, uno estático y uno en tiempo de ejecución:

* `auditar_esquema(sql)` lee el DDL del store semántico y devuelve las
  columnas cuyo nombre delata PII. La prueba del DoD ("0 columnas de PII
  en el store semántico") corre sobre esto.
* `validar_sin_pii(payload)` inspecciona todo diccionario que esté por
  salir hacia el store semántico. Si aparece una clave de PII, la operación
  aborta: es preferible romper la ingesta a filtrar un dato.

Al store semántico solo pueden viajar: `id_persona`, `ref_estudio`, metadatos de
cuestionario y pregunta, el texto de respuesta ya despersonalizado y su
embedding.
"""

import re

from .errores import FugaDePII

# Campos de PII de la bóveda (`persona`, en db/boveda/0001_init.sql) más los
# sinónimos habituales con los que podrían colarse desde una plataforma
# externa (Dooblo, Alchemer) o desde un Excel de campo.
#
# R5.5 — desde la Fase 5 la fuente de verdad de esta lista es la tabla
# `campo_pii` del store semántico, porque el event trigger que rechaza el DDL
# la lee de ahí y un segundo sistema escribiendo embeddings no va a importar
# este módulo. Este `frozenset` es su espejo: sirve para validar payloads sin
# ir a la base, y `test_fase5_pii` falla si los dos se separan.
CAMPOS_PII = frozenset({
    "documento", "cedula", "ci", "dni", "pasaporte", "rut",
    "nombre", "nombres", "apellido", "apellidos", "nombre_completo",
    "email", "correo", "mail", "e_mail",
    "celular", "telefono", "movil", "whatsapp",
    "direccion", "domicilio",
    "fecha_nacimiento", "fecha_nac", "nacimiento", "fnac",
    "contacto", "observaciones",
})

# `nombre` es legítimo como nombre de cuestionario, de pregunta o de serie:
# son metadatos del estudio, no de la persona. Se permite solo en esos usos.
#
# Una serie (R4.1.b) es contenido y por eso vive del lado semántico. Quién la
# creó o la editó, en cambio, es una persona, y no se escribe de este lado: ese
# rastro va a `serie_auditoria`, en la bóveda.
CONTEXTOS_QUE_PERMITEN_NOMBRE = frozenset({"cuestionario", "pregunta", "serie"})


def catalogo_en_base(conn):
    """La lista de PII tal como la tiene el store semántico.

    Es la fuente de verdad desde R5.5: el event trigger la lee de ahí. Se
    devuelve `(campos, excepciones)` para poder compararla con las dos
    constantes de este módulo.
    """
    with conn.cursor() as cur:
        cur.execute("select campo from campo_pii")
        campos = {fila[0] if isinstance(fila, tuple) else fila["campo"]
                  for fila in cur.fetchall()}
        cur.execute("select relacion from excepcion_pii where campo = 'nombre'")
        excepciones = {fila[0] if isinstance(fila, tuple) else fila["relacion"]
                       for fila in cur.fetchall()}
    return frozenset(campos), frozenset(excepciones)


def _claves(objeto):
    """Todas las claves de un dict, recorriendo dicts y listas anidados."""
    if isinstance(objeto, dict):
        for clave, valor in objeto.items():
            yield str(clave)
            yield from _claves(valor)
    elif isinstance(objeto, (list, tuple, set)):
        for item in objeto:
            yield from _claves(item)


def campos_pii_en(payload, contexto=""):
    """Devuelve las claves de PII presentes en `payload`."""
    permitidas = set()
    if contexto in CONTEXTOS_QUE_PERMITEN_NOMBRE:
        permitidas = {"nombre"}
    encontradas = set()
    for clave in _claves(payload):
        normalizada = clave.strip().lower()
        if normalizada in CAMPOS_PII and normalizada not in permitidas:
            encontradas.add(normalizada)
    return sorted(encontradas)


def validar_sin_pii(payload, contexto=""):
    """Aborta si `payload` lleva PII. Se llama antes de cada escritura al store semántico."""
    encontradas = campos_pii_en(payload, contexto)
    if encontradas:
        raise FugaDePII(
            "Se intentó escribir PII en el store semántico; la operación se abortó.",
            {"contexto": contexto or "desconocido", "campos": encontradas},
        )
    return payload


_RE_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)\s*\((.*?)\n\)\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _columnas_de(cuerpo):
    """Nombres de columna de un cuerpo de CREATE TABLE."""
    columnas = []
    profundidad = 0
    linea_actual = []
    for caracter in cuerpo:
        if caracter == "(":
            profundidad += 1
        elif caracter == ")":
            profundidad -= 1
        if caracter == "," and profundidad == 0:
            columnas.append("".join(linea_actual))
            linea_actual = []
        else:
            linea_actual.append(caracter)
    columnas.append("".join(linea_actual))

    nombres = []
    for definicion in columnas:
        definicion = re.sub(r"--[^\n]*", "", definicion).strip()
        if not definicion:
            continue
        primera = definicion.split()[0].lower()
        # Restricciones de tabla, no columnas.
        if primera in {"primary", "unique", "foreign", "check", "constraint", "exclude"}:
            continue
        nombres.append(primera)
    return nombres


# R5.5 — `create table` no es el único camino. Una migración que agrega la
# columna después, o que renombra una existente, deja el esquema igual de
# sucio, y es la que más fácil se cuela en un pull request porque el `create
# table` original está limpio.
_RE_ADD_COLUMN = re.compile(
    r"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?([a-z_][a-z0-9_]*)"
    r"(.*?);",
    re.IGNORECASE | re.DOTALL,
)
_RE_COLUMNA_AGREGADA = re.compile(
    r"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
_RE_COLUMNA_RENOMBRADA = re.compile(
    r"rename\s+(?:column\s+)?[a-z_][a-z0-9_]*\s+to\s+([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)


def _es_pii(tabla, columna):
    return columna in CAMPOS_PII and not (
        tabla in CONTEXTOS_QUE_PERMITEN_NOMBRE and columna == "nombre")


def auditar_esquema(sql):
    """Columnas de PII declaradas en un DDL. Vacío = el esquema está limpio.

    Devuelve una lista de `(tabla, columna)`. Mira `create table`, `alter
    table … add column` y `alter table … rename column`: los tres caminos por
    los que una columna llega a existir.
    """
    # Los comentarios se sacan primero. Esta misma migración explica en prosa
    # por qué `email` no va, y sin esto el chequeo se denunciaría a sí mismo.
    limpio = re.sub(r"--[^\n]*", "", sql)

    hallazgos = []
    for tabla, cuerpo in _RE_CREATE_TABLE.findall(limpio):
        for columna in _columnas_de(cuerpo):
            if _es_pii(tabla.lower(), columna):
                hallazgos.append((tabla.lower(), columna))

    for tabla, acciones in _RE_ADD_COLUMN.findall(limpio):
        nombres = (_RE_COLUMNA_AGREGADA.findall(acciones)
                   + _RE_COLUMNA_RENOMBRADA.findall(acciones))
        for columna in nombres:
            columna = columna.lower()
            if _es_pii(tabla.lower(), columna):
                hallazgos.append((tabla.lower(), columna))
    return hallazgos
