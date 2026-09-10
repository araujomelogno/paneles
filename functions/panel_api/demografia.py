"""R2.4 — Consulta puramente demográfica, resuelta entera en la bóveda.

Los atributos demográficos son autoritativos en la bóveda (CLAUDE.md): sexo,
localidad y la fecha de nacimiento de la que sale el tramo etario. El store
semántico no tiene ninguno de los tres, y no debe tenerlos: duplicarlos ahí
sería PII del otro lado del muro.

De eso se sigue lo que este módulo garantiza: una consulta que solo pide
demografía **no abre conexión al store semántico**. Es verificable, y hay una
prueba automatizada que lo verifica —`contexto.Contexto` abre el store
semántico en forma perezosa, así que basta comprobar que la propiedad nunca
se tocó.

Las dimensiones y los operadores están en listas blancas y los valores van
como parámetros: nada de lo que escribe el usuario se interpola en el SQL.
"""

from . import consentimiento, db
from .errores import DatosInvalidos

# Dimensión → expresión SQL sobre `v_demografia` (alias `d`) o `persona` (`p`).
DIMENSIONES = {
    "sexo": "d.sexo",
    "localidad": "d.localidad",
    "tramo_etario": "d.tramo_etario",
    "edad": "d.edad",
}

# Dimensiones que sirven para armar una composición (categóricas).
DIMENSIONES_CATEGORICAS = ("sexo", "tramo_etario", "localidad")

OPERADORES = {
    "eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
}
OPERADORES_LISTA = {"in", "not_in"}
OPERADORES_TEXTO = {"contiene"}

NUMERICAS = {"edad"}


def normalizar_criterio(crudo):
    """Valida un criterio demográfico y lo deja en forma canónica."""
    if not isinstance(crudo, dict):
        raise DatosInvalidos(f"Criterio demográfico mal formado: {crudo!r}.")
    dimension = (crudo.get("dimension") or "").strip().lower()
    if dimension not in DIMENSIONES:
        raise DatosInvalidos(
            f"Dimensión demográfica desconocida: {dimension!r}.",
            {"dimensiones_validas": sorted(DIMENSIONES)},
        )
    operador = (crudo.get("operador") or "eq").strip().lower()
    if operador not in OPERADORES and operador not in OPERADORES_LISTA \
            and operador not in OPERADORES_TEXTO:
        raise DatosInvalidos(
            f"Operador desconocido: {operador!r}.",
            {"operadores_validos": sorted(
                set(OPERADORES) | OPERADORES_LISTA | OPERADORES_TEXTO
            )},
        )
    valor = crudo.get("valor")
    if operador in OPERADORES_LISTA:
        valor = list(valor or [])
        if not valor:
            raise DatosInvalidos(
                f"El operador «{operador}» sobre {dimension} necesita al menos un valor."
            )
    elif valor is None or (isinstance(valor, str) and not valor.strip()):
        raise DatosInvalidos(f"Falta el valor del criterio sobre {dimension}.")

    if dimension in NUMERICAS:
        try:
            valor = [int(v) for v in valor] if isinstance(valor, list) else int(valor)
        except (TypeError, ValueError):
            raise DatosInvalidos(f"«{dimension}» espera un número, llegó {valor!r}.")

    return {
        "tipo": "demografico",
        "dimension": dimension,
        "operador": operador,
        "valor": valor,
        # Un criterio demográfico es duro por definición: filtra, no ordena.
        # Se acepta el campo para que el cuerpo de la consulta sea uniforme,
        # pero no se puede ablandar.
        "duro": True,
        "etiqueta": crudo.get("etiqueta") or f"{dimension} {operador} {valor}",
    }


def condiciones(criterios):
    """`[criterio]` → `(fragmentos_sql, parametros)`. Sin interpolar valores."""
    fragmentos, parametros = [], []
    for criterio in criterios:
        columna = DIMENSIONES[criterio["dimension"]]
        operador, valor = criterio["operador"], criterio["valor"]
        if operador in OPERADORES_LISTA:
            tipo = "int[]" if criterio["dimension"] in NUMERICAS else "text[]"
            negado = "not " if operador == "not_in" else ""
            fragmentos.append(f"{negado}({columna} = any(%s::{tipo}))")
            parametros.append(list(valor))
        elif operador in OPERADORES_TEXTO:
            fragmentos.append(f"{columna} ilike %s")
            parametros.append(f"%{valor}%")
        else:
            fragmentos.append(f"{columna} {OPERADORES[operador]} %s")
            parametros.append(valor)
    return fragmentos, parametros


def _sql_segmento(criterios, panel_id=None, finalidad=None, solo_ids=True,
                  ids_persona=None):
    """Arma el SELECT del segmento. Todo pasa en la bóveda."""
    seleccion = "p.id_persona" if solo_ids else (
        "p.id_persona, p.nombre, p.email, d.sexo, d.localidad, d.tramo_etario, d.edad"
    )
    donde, parametros = condiciones(criterios or [])

    if panel_id is not None:
        donde.append(
            "exists (select 1 from membresia m where m.id_persona = p.id_persona "
            "and m.panel_id = %s and m.estado = 'activo')"
        )
        parametros.append(panel_id)

    if ids_persona is not None:
        # Restricción a un conjunto ya conocido. La usa la estrategia
        # «semántico primero»: el recall trajo estas personas y hay que
        # preguntarle a la bóveda cuáles de ellas pasan el gate y el segmento.
        donde.append("p.id_persona = any(%s::uuid[])")
        parametros.append([str(i) for i in ids_persona])

    if finalidad:
        consentimiento._validar_finalidad(finalidad)
        donde.append(
            "exists (select 1 from consentimiento c where c.id_persona = p.id_persona "
            "and c.finalidad = %s and c.estado = 'vigente')"
        )
        parametros.append(finalidad)

    sql = (
        f"select {seleccion}\n"
        "  from persona p\n"
        "  left join v_demografia d on d.id_persona = p.id_persona\n"
    )
    if donde:
        sql += " where " + "\n   and ".join(donde) + "\n"
    return sql, parametros


def ids_del_segmento(conn, criterios, panel_id=None, finalidad=None, limite=None,
                     ids_persona=None):
    """Los `id_persona` que cumplen el segmento. Es el lado «bóveda» del
    puente entre stores: lo único que se le pasa al store semántico.

    `ids_persona` acota la pregunta a un conjunto ya conocido, que es lo que
    hace falta cuando el recall corrió primero.
    """
    if ids_persona is not None and not ids_persona:
        return []
    sql, parametros = _sql_segmento(
        criterios, panel_id, finalidad, solo_ids=True, ids_persona=ids_persona
    )
    if limite:
        sql += " limit %s"
        parametros.append(limite)
    return [str(f["id_persona"]) for f in db.todas(conn, sql, tuple(parametros))]


def contar_segmento(conn, criterios, panel_id=None, finalidad=None):
    """Cuántas personas caen en el segmento. La usa el motor de consultas
    para elegir la estrategia de puente: cuál de los dos lados es más
    selectivo."""
    sql, parametros = _sql_segmento(criterios, panel_id, finalidad, solo_ids=True)
    fila = db.una(
        conn, f"select count(*)::int as n from ({sql}) as seg", tuple(parametros)
    )
    return fila["n"]


def consultar(conn, criterios, panel_id=None, finalidad=None,
              limite=200, desplazamiento=0):
    """Consulta puramente demográfica. **No toca el store semántico.**

    Devuelve las personas del segmento con sus datos de contacto: es una
    consulta de bóveda, y el operador que la corre está viendo PII. Quien
    llama es responsable de registrar la reidentificación (ver
    `auditoria.registrar_reidentificacion`).
    """
    criterios = [normalizar_criterio(c) for c in (criterios or [])]
    sql, parametros = _sql_segmento(criterios, panel_id, finalidad, solo_ids=False)
    total = contar_segmento(conn, criterios, panel_id, finalidad)
    sql += " order by p.nombre nulls last limit %s offset %s"
    filas = db.todas(conn, sql, tuple(parametros) + (limite, desplazamiento))
    return {
        "tipo": "demografica",
        "store": "boveda",
        "abrio_semantica": False,
        "criterios": criterios,
        "panel_id": panel_id,
        "finalidad_exigida": finalidad,
        "total": total,
        "items": [
            {
                "id_persona": str(f["id_persona"]),
                "nombre": f["nombre"],
                "email": f["email"],
                "sexo": f["sexo"],
                "localidad": f["localidad"],
                "tramo_etario": f["tramo_etario"],
                "edad": f["edad"],
            }
            for f in filas
        ],
    }
