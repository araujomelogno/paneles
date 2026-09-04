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
CAMPOS_PII = frozenset({
    "documento", "cedula", "ci", "dni", "pasaporte", "rut",
    "nombre", "nombres", "apellido", "apellidos", "nombre_completo",
    "email", "correo", "mail", "e_mail",
    "celular", "telefono", "movil", "whatsapp",
    "direccion", "domicilio",
    "fecha_nacimiento", "fecha_nac", "nacimiento", "fnac",
    "contacto", "observaciones",
})

# `nombre` es legítimo como nombre de cuestionario o de pregunta: son
# metadatos del estudio, no de la persona. Se permite solo en esos usos.
CONTEXTOS_QUE_PERMITEN_NOMBRE = frozenset({"cuestionario", "pregunta"})


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


def auditar_esquema(sql):
    """Columnas de PII declaradas en un DDL. Vacío = el esquema está limpio.

    Devuelve una lista de `(tabla, columna)`.
    """
    hallazgos = []
    for tabla, cuerpo in _RE_CREATE_TABLE.findall(sql):
        for columna in _columnas_de(cuerpo):
            if columna in CAMPOS_PII and not (
                tabla.lower() in CONTEXTOS_QUE_PERMITEN_NOMBRE and columna == "nombre"
            ):
                hallazgos.append((tabla.lower(), columna))
    return hallazgos
