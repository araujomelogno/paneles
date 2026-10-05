"""R7.5 — los números de la base, los dos stores en una pantalla.

Para saber cuántos panelistas o cuántas respuestas hay había que consultar
la base a mano, y la información está repartida entre dos instancias que
nadie mira juntas.

── Lo que importa de verdad es la brecha ──

Los conteos sueltos —«1.008 panelistas», «24.935 respuestas»— se miran una
vez. El número que se usa todas las semanas es *«de mis 1.131 panelistas,
¿sobre cuántos puedo realmente consultar?»*: eso define si una búsqueda
sirve, y hoy no se puede saber sin cruzar las dos bases a mano.

Por eso la sección de brecha no es un apéndice: es la razón de la pantalla.
Y uno de sus tres números —individuos semánticos sin panelista en la
bóveda— **tiene que ser cero**. Si no lo es, hay datos huérfanos y es un
problema de integridad, no una estadística.

── Pocas consultas, no una por tarjeta ──

Cada bloque se resuelve en **una** consulta con varios `count(*) filter
(where …)`, no en una por número. Con el corpus creciendo, veinte
`count(*)` sobre `respuesta` son veinte recorridos de la tabla.

El cruce entre stores no se puede hacer en SQL —son instancias distintas,
sin FK— así que se resuelve como todo lo demás acá: **por conjuntos de
`id_persona`**, trayendo los dos lados y restando en Python.
"""

from . import consentimiento as consent
from . import db

# Cuántos ejemplos acompañan a un número que se puede desglosar. Listar los
# 1.131 panelistas sin respuestas no ayuda a nadie; ver los primeros y el
# total, sí. La lista completa es su propia ruta.
EJEMPLOS = 50


def panelistas(conn_boveda):
    """Un recorrido de `persona`, con todos los cortes de una vez."""
    fila = db.una(
        conn_boveda,
        """
        select count(*)                                            as total,
               count(*) filter (where estado = 'activo')           as activos,
               count(*) filter (where email is not null)           as con_email,
               count(*) filter (where celular is not null)         as con_celular,
               count(*) filter (where creado_en >= now() - interval '30 days')
                                                                   as altas_30_dias
          from persona
        """)
    sin_panel = db.una(
        conn_boveda,
        """select count(*) as n from persona p
            where not exists (select 1 from membresia m
                               where m.id_persona = p.id_persona
                                 and m.estado = 'activo')""")["n"]
    por_panel = db.todas(
        conn_boveda,
        """select p.id, p.nombre, count(m.id_persona) as miembros
             from panel p
             left join membresia m on m.panel_id = p.id and m.estado = 'activo'
            group by p.id, p.nombre
            order by miembros desc, p.nombre""")
    por_mes = db.todas(
        conn_boveda,
        """select to_char(date_trunc('month', creado_en), 'YYYY-MM') as mes,
                  count(*) as altas
             from persona
            group by 1 order by 1 desc limit 12""")
    return {
        "total": fila["total"],
        "activos": fila["activos"],
        "sin_panel": sin_panel,
        "con_email": fila["con_email"],
        "con_celular": fila["con_celular"],
        "altas_30_dias": fila["altas_30_dias"],
        "por_panel": [
            {"id": p["id"], "nombre": p["nombre"], "miembros": p["miembros"]}
            for p in por_panel],
        "altas_por_mes": [
            {"mes": m["mes"], "altas": m["altas"]} for m in reversed(por_mes)],
    }


def consentimiento(conn_boveda):
    """Cuántos tienen vigente cada finalidad, leído de la vista y no a mano.

    `v_persona_convocable` es el gate de verdad: contar con un `select`
    propio daría un número parecido y, el día que la regla cambie, uno
    equivocado. Es la misma razón por la que el gate no se recalcula en
    Python en ningún lado.

    Y se cuenta **por persona**, no por fila de consentimiento. Antes se
    contaba sobre `consentimiento` directo: un re-otorgamiento (versión nueva
    del texto, que agrega fila sin pisar el historial) contaba dos veces a la
    misma persona, y «sin el» podía dar negativo. Es la misma clase de error
    que `specs/BUG_v_persona_convocable_duplica.md`, del lado de la pantalla.
    """
    filas = db.todas(
        conn_boveda,
        """select f.finalidad, count(*)::int as vigentes
             from v_persona_convocable v
             cross join lateral unnest(v.finalidades) as f(finalidad)
            group by f.finalidad order by f.finalidad""")
    total = db.una(conn_boveda, "select count(*) as n from persona")["n"]
    vigentes = {f["finalidad"]: f["vigentes"] for f in filas}
    # Las dos finalidades que trata esta aplicación se muestran siempre,
    # aunque nadie las tenga: un cero es información, una fila ausente no.
    for propia in (consent.CONTACTO, consent.SEMANTICO):
        vigentes.setdefault(propia, 0)
    return {
        "total_personas": total,
        "por_finalidad": [
            {"finalidad": finalidad, "vigentes": n, "sin_el": total - n}
            for finalidad, n in sorted(vigentes.items())],
    }


def corpus(conn_semantica):
    """El store semántico: cuánto hay y de cuántos estudios."""
    fila = db.una(
        conn_semantica,
        """select (select count(*) from respuesta)    as respuestas,
                  (select count(*) from individuo)    as individuos,
                  (select count(*) from pregunta)     as preguntas,
                  (select count(*) from cuestionario) as cuestionarios""")
    con_respuestas = db.una(
        conn_semantica,
        "select count(distinct individuo_id) as n from respuesta")["n"]
    estudios = db.todas(
        conn_semantica,
        """select c.ref_estudio, c.nombre, c.fecha_campo,
                  count(r.id) as respuestas
             from cuestionario c
             left join pregunta p on p.cuestionario_id = c.id
             left join respuesta r on r.pregunta_id = p.id
            group by c.ref_estudio, c.nombre, c.fecha_campo
            order by c.fecha_campo desc nulls last, c.nombre
            limit 20""")
    return {
        "respuestas": fila["respuestas"],
        "individuos": fila["individuos"],
        "individuos_con_respuestas": con_respuestas,
        "preguntas": fila["preguntas"],
        "cuestionarios": fila["cuestionarios"],
        "respuestas_por_individuo": (
            round(fila["respuestas"] / con_respuestas, 1)
            if con_respuestas else 0),
        "estudios": [
            {"ref_estudio": str(e["ref_estudio"]), "nombre": e["nombre"],
             "fecha_campo": (e["fecha_campo"].isoformat()
                             if e["fecha_campo"] else None),
             "respuestas": e["respuestas"]}
            for e in estudios],
    }


def salud_del_corpus(conn_semantica):
    """Tamaño de la tabla y del índice vectorial, y la dimensión en uso.

    El tamaño del índice comparado con la memoria de la instancia es lo que
    dice cuándo conviene subir de tier (`COSTOS.md` §4). Es el número que
    nadie mira hasta que las consultas se ponen lentas.
    """
    tamanos = db.una(
        conn_semantica,
        """select pg_total_relation_size('respuesta')          as tabla_bytes,
                  coalesce(sum(pg_relation_size(indexrelid)), 0) as indice_bytes
             from pg_index
            where indrelid = 'respuesta'::regclass""")
    dimension = db.una(
        conn_semantica,
        """select atttypmod as dimension from pg_attribute
            where attrelid = 'respuesta'::regclass and attname = 'embedding'""")
    return {
        "tabla_bytes": int(tamanos["tabla_bytes"]),
        "indice_bytes": int(tamanos["indice_bytes"]),
        "dimension_embeddings": (dimension or {}).get("dimension"),
    }


def brecha(conn_boveda, conn_semantica, ejemplos=EJEMPLOS):
    """Lo que ninguno de los dos stores puede contestar solo.

    Se cruza por conjuntos de `id_persona`, que es como se cruza todo en
    esta plataforma: no hay FK entre instancias y no la va a haber.
    """
    en_boveda = {
        str(f["id_persona"]) for f in db.todas(
            conn_boveda, "select id_persona from persona")}
    con_respuestas = {
        str(f["id_persona"]) for f in db.todas(
            conn_semantica,
            """select distinct i.id_persona
                 from individuo i join respuesta r on r.individuo_id = i.id""")}
    en_semantico = {
        str(f["id_persona"]) for f in db.todas(
            conn_semantica, "select id_persona from individuo")}

    sin_uso_semantico = {
        str(f["id_persona"]) for f in db.todas(
            conn_boveda,
            # Del gate y no de `consentimiento` a mano: quien no está activa
            # tampoco puede aparecer en un resultado aunque tenga la fila.
            # Es un `except` y no un `not exists` correlacionado porque la
            # vista es una función `security definer`: correlacionada se
            # evaluaría una vez por persona.
            """select id_persona from persona
               except
               select id_persona from v_persona_convocable
                where 'uso_semantico' = any(finalidades)""")}

    sin_respuestas = en_boveda - con_respuestas
    huerfanos = en_semantico - en_boveda

    return {
        "panelistas_sin_respuestas": {
            "cuantos": len(sin_respuestas),
            "ejemplos": sorted(sin_respuestas)[:ejemplos],
        },
        "panelistas_sin_uso_semantico": {
            "cuantos": len(sin_uso_semantico),
            "ejemplos": sorted(sin_uso_semantico)[:ejemplos],
        },
        # Tiene que ser cero. No es una estadística: es un chequeo.
        "individuos_sin_panelista": {
            "cuantos": len(huerfanos),
            "ejemplos": sorted(huerfanos)[:ejemplos],
            "es_problema": len(huerfanos) > 0,
            "nota": ("Un individuo del store semántico sin panelista en la "
                     "bóveda son respuestas de alguien que ya no existe: la "
                     "cascada de baja no las alcanzó. Tiene que ser cero."),
        },
        "consultables": len(con_respuestas - sin_uso_semantico),
    }


def cargas_recientes(conn_boveda, limite=10):
    """Las últimas ingestas con su estado, y las que quedaron colgadas.

    Hoy un trabajo fallido solo se ve consultando la base — que es
    exactamente lo que esta pantalla vino a evitar.
    """
    filas = db.todas(
        conn_boveda,
        """select trabajo_id, destino_tipo, destino_id, estado,
                  estado_etiqueta, terminal, lotes_total, lotes_ok,
                  lotes_fallidos, filas_total, filas_procesadas, creado_en
             from v_ingesta_progreso
            order by trabajo_id desc limit %s""", (limite,))
    return {
        "items": [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v)
             for k, v in f.items()}
            for f in filas
        ],
        "con_problemas": [
            f["trabajo_id"] for f in filas
            if f["lotes_fallidos"] or (not f["terminal"] and f["lotes_ok"] == 0)
        ],
    }


def todo(conn_boveda, conn_semantica):
    """La pantalla entera. Sin parámetros: se abre y muestra."""
    return {
        "panelistas": panelistas(conn_boveda),
        "consentimiento": consentimiento(conn_boveda),
        "corpus": corpus(conn_semantica),
        "salud": salud_del_corpus(conn_semantica),
        "brecha": brecha(conn_boveda, conn_semantica),
        "cargas": cargas_recientes(conn_boveda),
    }
