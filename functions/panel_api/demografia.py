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

from . import atributos, consentimiento, db
from .errores import DatosInvalidos

# ── R3.14 · Las dimensiones salen del catálogo ───────────────────────
#
# Hasta R3.14 esta lista era fija y con ella la de segmentadores posibles de
# todo el sistema. Ahora es el catálogo de `atributo_demografico` el que dice
# qué se puede filtrar, y `sexo`, `localidad`, `tramo_etario` y `edad` viven
# ahí como cualquier otro: mismas claves de siempre, mismo mecanismo.
#
# Esta constante queda como **respaldo para cuando no hay conexión**: validar
# la forma de un criterio sin tocar la base (por ejemplo al normalizar una
# definición guardada antes de ejecutarla). Con `conn` a mano se usa el
# catálogo, que es la fuente de verdad.
DIMENSIONES_BASE = ("sexo", "localidad", "tramo_etario", "edad")

# Retrocompatibilidad: había código y pruebas que leían estas dos. Siguen
# nombrando el núcleo, que es lo que siempre existe.
DIMENSIONES = {d: d for d in DIMENSIONES_BASE}
DIMENSIONES_CATEGORICAS = ("sexo", "tramo_etario", "localidad")

OPERADORES = {
    "eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
}
OPERADORES_LISTA = {"in", "not_in"}
OPERADORES_TEXTO = {"contiene"}

# Qué dimensiones se comparan como número y no como texto. `edad` siempre lo
# es; cualquier atributo `numerico` del catálogo también.
NUMERICAS = {"edad"}


def catalogo(conn, solo_activos=True):
    """`{clave: atributo}` del catálogo, que es lo que se puede filtrar."""
    return atributos.mapa_por_clave(conn, solo_activos=solo_activos)


def dimensiones_validas(conn=None):
    """Las claves filtrables. Con `conn`, las del catálogo; sin él, el núcleo."""
    if conn is None:
        return set(DIMENSIONES_BASE)
    return set(catalogo(conn)) | set(DIMENSIONES_BASE)


def categoricas(conn=None):
    """Las que sirven para armar una composición o una cuota: las que tienen
    categorías canónicas. `edad` no está: es numérica, y una cuota sobre un
    número continuo no es una cuota."""
    if conn is None:
        return list(DIMENSIONES_CATEGORICAS)
    del_catalogo = [
        a["clave"] for a in atributos.listar(conn, solo_activos=True)
        if a["tipo"] == "categorico" or a["clave"] == "tramo_etario"
    ]
    # El orden del catálogo manda; el núcleo queda primero si está.
    return del_catalogo or list(DIMENSIONES_CATEGORICAS)


def _es_numerica(dimension, catalogo_por_clave=None):
    if dimension in NUMERICAS:
        return True
    entrada = (catalogo_por_clave or {}).get(dimension)
    return bool(entrada) and entrada["tipo"] == "numerico"


def normalizar_criterio(crudo, dimensiones=None, catalogo_por_clave=None):
    """Valida un criterio demográfico y lo deja en forma canónica.

    `dimensiones` es el conjunto de claves admitidas. Quien tiene conexión lo
    pasa desde el catálogo; sin él se valida contra el núcleo, que es lo que
    siempre existe.
    """
    if not isinstance(crudo, dict):
        raise DatosInvalidos(f"Criterio demográfico mal formado: {crudo!r}.")
    dimension = (crudo.get("dimension") or "").strip().lower()
    validas = set(dimensiones) if dimensiones else set(DIMENSIONES_BASE)
    if dimension not in validas:
        raise DatosInvalidos(
            f"Dimensión demográfica desconocida: {dimension!r}.",
            {"dimensiones_validas": sorted(validas)},
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

    if _es_numerica(dimension, catalogo_por_clave):
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


def condiciones(criterios, alias="p", catalogo_por_clave=None):
    """`[criterio]` → `(fragmentos_sql, parametros)`. Sin interpolar valores.

    R3.14 — cada criterio se resuelve contra `v_atributo_persona`, que es el
    único lugar donde vive el valor efectivo de un atributo. Un filtro por
    `sexo` y uno por `nivel_educativo` se arman igual: no hay camino aparte
    para los segmentadores «de fábrica».

    Una persona **sin valor** para el atributo no tiene fila en la vista, así
    que no entra en ningún filtro positivo. Es lo que hace que «sin dato» no
    se cuele como una categoría más.
    """
    fragmentos, parametros = [], []
    for criterio in criterios:
        dimension = criterio["dimension"]
        numerica = _es_numerica(dimension, catalogo_por_clave)
        columna = "va.valor_num" if numerica else "va.valor"
        operador, valor = criterio["operador"], criterio["valor"]

        # Todos los operadores van **dentro** del exists, incluidos `ne` y
        # `not_in`. Negar el exists entero diría otra cosa: «quien no tiene F»
        # incluye a quien no tiene sexo cargado, y quien no tiene el dato no
        # es «de otra categoría», es un desconocido. Adentro, en cambio, hace
        # falta tener el valor y que sea distinto. Es también la semántica que
        # tenía el filtro sobre `v_demografia` antes de R3.14, donde un NULL
        # no pasaba ninguna comparación.
        if operador in OPERADORES_LISTA:
            tipo = "numeric[]" if numerica else "text[]"
            negado = "not " if operador == "not_in" else ""
            interno = f"{negado}({columna} = any(%s::{tipo}))"
            argumentos = [dimension, list(valor)]
        elif operador in OPERADORES_TEXTO:
            interno = "va.valor ilike %s"
            argumentos = [dimension, f"%{valor}%"]
        else:
            interno = f"{columna} {OPERADORES[operador]} %s"
            argumentos = [dimension, valor]

        fragmentos.append(
            f"exists (select 1 from v_atributo_persona va "
            f"where va.id_persona = {alias}.id_persona and va.atributo = %s "
            f"and {interno})"
        )
        parametros.extend(argumentos)
    return fragmentos, parametros


def _sql_segmento(criterios, panel_id=None, finalidad=None, solo_ids=True,
                  ids_persona=None, catalogo_por_clave=None):
    """Arma el SELECT del segmento. Todo pasa en la bóveda."""
    seleccion = "p.id_persona" if solo_ids else (
        "p.id_persona, p.nombre, p.email, d.sexo, d.localidad, d.tramo_etario, d.edad"
    )
    donde, parametros = condiciones(
        criterios or [], catalogo_por_clave=catalogo_por_clave)

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
        criterios, panel_id, finalidad, solo_ids=True, ids_persona=ids_persona,
        catalogo_por_clave=catalogo(conn),
    )
    if limite:
        sql += " limit %s"
        parametros.append(limite)
    return [str(f["id_persona"]) for f in db.todas(conn, sql, tuple(parametros))]


def contar_segmento(conn, criterios, panel_id=None, finalidad=None):
    """Cuántas personas caen en el segmento. La usa el motor de consultas
    para elegir la estrategia de puente: cuál de los dos lados es más
    selectivo."""
    sql, parametros = _sql_segmento(
        criterios, panel_id, finalidad, solo_ids=True,
        catalogo_por_clave=catalogo(conn))
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
    por_clave = catalogo(conn)
    criterios = [
        normalizar_criterio(c, dimensiones=set(por_clave) | set(DIMENSIONES_BASE),
                            catalogo_por_clave=por_clave)
        for c in (criterios or [])
    ]
    sql, parametros = _sql_segmento(criterios, panel_id, finalidad, solo_ids=False,
                                    catalogo_por_clave=por_clave)
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
