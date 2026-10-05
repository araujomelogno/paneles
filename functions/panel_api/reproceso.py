"""R8.9 — reprocesar un estudio sin volver a subir el archivo.

Sin esto, un error de texto es permanente o carísimo: descubrir después de
cargar que las preguntas quedaron mal redactadas obligaba a pedir el archivo
otra vez y rehacer todo. Con el reproceso se corrige el texto, el mapeo o
las decisiones de normalización, y se re-embebe **solo lo afectado**.

── De dónde sale el dato ──

El archivo ya no está: las filas de la ingesta diferida se purgan a los
siete días (`purgar_ingestas_terminadas`). Lo que sí está es el store
semántico: cada respuesta guarda su `valor_texto` —la etiqueta con la que se
embebió— y cada pregunta sus opciones. Del `valor_texto` se recupera el
**código** invirtiendo las etiquetas (las de ahora y las originales del
archivo, que la `semantica/0007` conserva), y con el código y la
configuración nueva se recompone el texto con `ingesta.respuesta_de`, que es
lo mismo que corre al ingestar. No hay una segunda forma de componer el
texto.

Un código que no se había traducido —`11427`— está en la base como `11427`,
así que darle etiqueta en el reproceso lo traduce. Lo que **no** se puede es
recuperar lo que la ingesta dejó afuera: si se ingestó solo lo marcado de
una batería, las opciones no marcadas no están en ningún lado, y para
tenerlas hay que volver a cargar el archivo. La revisión lo avisa.

── Qué hace con cada respuesta ──

* Si el texto nuevo es **el mismo** (mismo `hash_texto`), no se toca: ni se
  re-embebe ni se re-escribe.
* Si **cambió**, se re-embebe y se actualiza.
* Si con la configuración nueva **ya no genera respuesta** —la variable se
  excluyó, o el valor pasó a ser de no respuesta o a no estar marcado—, se
  borra del store semántico.

── Por la vía diferida ──

El reproceso corre por Cloud Tasks como cualquier ingesta grande
(`diferida`), con un plan congelado: las preguntas **antes** y **después**.
El «antes» hace falta para invertir las etiquetas viejas, y congelarlo es lo
que hace que dos lotes del mismo reproceso no decidan distinto. El gate de
`uso_semantico` se re-evalúa en cada lote, igual que en la ingesta: el texto
de quien retiró el consentimiento no se manda a embeber, aunque la
respuesta todavía estuviera.

── Qué queda registrado ──

Cada reproceso deja una fila en `reproceso` (store semántico) con qué se
cambió y cuándo; quién lo pidió queda en el trabajo diferido, del lado de la
bóveda, que es donde viven los datos de los usuarios internos.
"""

import json

from . import (cargas, consentimiento, db, diferida, embeddings as mod_embeddings,
               encuestas, esquema, ingesta, pii, semantica)
from .errores import Conflicto, DatosInvalidos, NoEncontrado

OPERACION = "reproceso"
TIPOS = ("cerrada", "abierta", "escala", "numerica")
# Lo que se puede cambiar de una pregunta en un reproceso. El código no: es
# la identidad de la variable y lo que la ata a sus respuestas.
CAMPOS = ("texto", "tipo", "opciones")
CLAVES = CAMPOS + semantica.CLAVES_DE_NORMALIZACION

# Cuántas respuestas van en cada lote. Lo mismo que la ingesta: lo que se
# rehace si un lote falla.
RESPUESTAS_POR_LOTE = diferida.FILAS_POR_LOTE

# Lo que una respuesta puede ser después del reproceso.
REEMBEBER = "reembebe"
SIN_CAMBIOS = "sin_cambios"
BORRAR = "borra"


# ════════════════════════════════════════════════════════════════════
#  Leer el estudio
# ════════════════════════════════════════════════════════════════════

def ref_estudio_de(conn_boveda, destino_tipo, destino_id):
    if destino_tipo == "encuesta":
        return str(encuestas.obtener(conn_boveda, destino_id)["ref_estudio"])
    if destino_tipo == "carga":
        return str(cargas.obtener(conn_boveda, destino_id)["ref_estudio"])
    raise DatosInvalidos(f"Destino desconocido: {destino_tipo!r}.")


def _cuestionario(conn_semantica, ref_estudio):
    fila = db.una(
        conn_semantica,
        "select id, nombre, normalizacion from cuestionario where ref_estudio = %s",
        (str(ref_estudio),))
    if not fila:
        raise NoEncontrado(
            "Este estudio todavía no tiene nada en el store semántico: no hay "
            "qué reprocesar. Primero hay que ingestarlo.")
    return fila


def _preguntas(conn_semantica, cuestionario_id):
    filas = db.todas(
        conn_semantica,
        """select p.id, p.codigo, p.texto, p.tipo, p.opciones, p.orden,
                  p.texto_original, p.opciones_originales, p.normalizacion,
                  (select count(*) from respuesta r where r.pregunta_id = p.id)::int
                    as respuestas
             from pregunta p
            where p.cuestionario_id = %s
            order by p.orden nulls last, p.codigo""",
        (cuestionario_id,))
    preguntas = []
    for f in filas:
        pregunta = {
            "id": f["id"], "codigo": f["codigo"], "texto": f["texto"],
            "tipo": f["tipo"], "opciones": f["opciones"] or None,
            "orden": f["orden"], "texto_original": f["texto_original"],
            "opciones_originales": f["opciones_originales"] or None,
            "respuestas": f["respuestas"],
        }
        pregunta.update(f["normalizacion"] or {})
        preguntas.append(pregunta)
    return preguntas


def crudo_de(antes, valor_texto):
    """El código del archivo detrás de una etiqueta ya embebida.

    Se invierten las opciones de ahora y, después, las originales del
    archivo: así una respuesta que se embebió como «Checked» y cuya pregunta
    ya dice «Sí» se sigue reconociendo como el código `1`. Lo que no es una
    etiqueta conocida —una abierta, un número, un código sin traducir— ya es
    el valor crudo.
    """
    valor = str(valor_texto or "").strip()
    for fuente in (antes.get("opciones"), antes.get("opciones_originales")):
        for codigo, etiqueta in (fuente or {}).items():
            if str(etiqueta).strip() == valor:
                return str(codigo)
    return valor


def distribuciones(conn_semantica, cuestionario_id, preguntas):
    """`{codigo: {valor_crudo: respuestas}}` de lo ya ingestado.

    Es la misma forma que arma un `.sav` al analizarse, así que la detección
    de la Fase 8 corre igual sobre un estudio cargado que sobre un archivo.
    """
    por_codigo = {p["codigo"]: p for p in preguntas}
    filas = db.todas(
        conn_semantica,
        """select p.codigo, r.valor_texto, count(*)::int as n
             from respuesta r join pregunta p on p.id = r.pregunta_id
            where p.cuestionario_id = %s
            group by p.codigo, r.valor_texto""",
        (cuestionario_id,))
    salida = {}
    for f in filas:
        crudo = crudo_de(por_codigo.get(f["codigo"]) or {}, f["valor_texto"])
        por_valor = salida.setdefault(f["codigo"], {})
        por_valor[crudo] = por_valor.get(crudo, 0) + f["n"]
    return salida


def _trabajo_abierto(conn_boveda, destino_tipo, destino_id):
    return db.una(
        conn_boveda,
        """select trabajo_id, estado from v_ingesta_progreso
            where destino_tipo = %s and destino_id = %s and not terminal
            order by creado_en desc limit 1""",
        (destino_tipo, destino_id))


def estado(conn_boveda, conn_semantica, destino_tipo, destino_id):
    """Lo que la pantalla de reproceso necesita: las preguntas como están,
    una muestra de sus valores para la vista previa, el diagnóstico de
    calidad sobre lo ya cargado y los reprocesos anteriores."""
    from . import calidad_dato, sav

    ref_estudio = ref_estudio_de(conn_boveda, destino_tipo, destino_id)
    cuestionario = _cuestionario(conn_semantica, ref_estudio)
    preguntas = _preguntas(conn_semantica, cuestionario["id"])
    distribucion = distribuciones(conn_semantica, cuestionario["id"], preguntas)
    activas = [p for p in preguntas if not p.get("excluida")]
    normalizacion = cuestionario["normalizacion"] or {}

    for pregunta in preguntas:
        pregunta["muestra"] = sav._muestra(distribucion.get(pregunta["codigo"]) or {})
        pregunta.pop("id", None)

    historial = db.todas(
        conn_semantica,
        """select id, creado_en, cambios, respuestas_a_procesar, trabajo_id
             from reproceso where cuestionario_id = %s
            order by creado_en desc limit 20""",
        (cuestionario["id"],))
    estados = {}
    ids_trabajo = [h["trabajo_id"] for h in historial if h["trabajo_id"]]
    if ids_trabajo:
        estados = {f["trabajo_id"]: f for f in db.todas(
            conn_boveda,
            """select trabajo_id, estado, estado_etiqueta, terminal
                 from v_ingesta_progreso where trabajo_id = any(%s)""",
            (ids_trabajo,))}
    abierto = _trabajo_abierto(conn_boveda, destino_tipo, destino_id)
    return {
        "ref_estudio": ref_estudio,
        "estudio": cuestionario["nombre"],
        "preguntas": preguntas,
        "normalizacion": normalizacion,
        "calidad": calidad_dato.diagnosticar(
            activas, distribucion,
            valores_no_respuesta=normalizacion.get("valores_no_respuesta")),
        "reprocesos": [
            {"id": h["id"], "creado_en": h["creado_en"].isoformat(),
             "cambios": h["cambios"],
             "respuestas_a_procesar": h["respuestas_a_procesar"],
             "trabajo_id": h["trabajo_id"],
             "estado": (estados.get(h["trabajo_id"]) or {}).get("estado_etiqueta")
                       or ("Sin respuestas que re-embeber" if not h["trabajo_id"]
                           else None)}
            for h in historial],
        "trabajo_abierto": (dict(abierto) if abierto else None),
    }


# ════════════════════════════════════════════════════════════════════
#  Planificar: qué cambia
# ════════════════════════════════════════════════════════════════════

def _validar(pedida, actual):
    codigo = actual["codigo"]
    if "texto" in pedida and not str(pedida.get("texto") or "").strip():
        raise DatosInvalidos(
            f"La pregunta «{codigo}» quedaría sin texto, y el texto es lo que "
            f"se embebe.", {"codigo": codigo})
    if "tipo" in pedida and pedida.get("tipo") not in TIPOS:
        raise DatosInvalidos(f"Tipo desconocido para «{codigo}»: {pedida.get('tipo')!r}.",
                             {"tipos_validos": list(TIPOS)})
    if "opciones" in pedida and pedida.get("opciones") is not None \
            and not isinstance(pedida.get("opciones"), dict):
        raise DatosInvalidos(f"Las opciones de «{codigo}» son un objeto "
                             f"«código → etiqueta».", {"codigo": codigo})
    for lista in ("valores_marcados", "excluir_valores"):
        if lista in pedida and pedida.get(lista) is not None \
                and not isinstance(pedida.get(lista), list):
            raise DatosInvalidos(f"«{lista}» de «{codigo}» es una lista.",
                                 {"codigo": codigo})


def _comparable(pregunta):
    """Los campos que importan para decidir si algo cambió, en forma
    canónica: lo falso, lo vacío y lo ausente son lo mismo."""
    salida = {}
    for clave in CLAVES:
        valor = pregunta.get(clave)
        if valor in (None, "", [], {}, False):
            continue
        if clave == "opciones":
            valor = {str(k): str(v) for k, v in valor.items()}
        if clave in ("valores_marcados", "excluir_valores"):
            valor = sorted(str(v) for v in valor)
        salida[clave] = valor
    return salida


def planificar(preguntas_actuales, pedidas):
    """El plan del reproceso: `{preguntas: {codigo: {antes, despues}},
    excluidas, cambios, avisos}`. No toca nada.

    `pedidas` es el estado deseado de cada pregunta que se quiere cambiar;
    las que no vienen quedan como están, y dentro de una pregunta, los campos
    que no vienen también. `excluir: true` saca la variable del store
    semántico.
    """
    por_codigo = {p["codigo"]: p for p in preguntas_actuales}
    if not isinstance(pedidas, list) or not pedidas:
        raise DatosInvalidos("Hace falta la lista de preguntas a corregir.")
    plan, cambios, excluidas, avisos = {}, [], [], []
    for pedida in pedidas:
        codigo = str((pedida or {}).get("codigo") or "").strip()
        actual = por_codigo.get(codigo)
        if not actual:
            raise DatosInvalidos(
                f"«{codigo}» no es una pregunta de este estudio. En un "
                f"reproceso no se agregan variables: para eso hay que volver a "
                f"ingestar el archivo.", {"codigo": codigo})
        _validar(pedida, actual)

        antes = {c: actual.get(c) for c in CLAVES}
        antes["opciones_originales"] = actual.get("opciones_originales")
        antes["codigo"] = codigo
        if pedida.get("excluir"):
            if not actual.get("excluida"):
                excluidas.append(codigo)
                cambios.append({"codigo": codigo, "campo": "excluida",
                                "antes": False, "despues": True})
            continue

        despues = dict(antes)
        despues.update({c: pedida[c] for c in CLAVES if c in pedida})
        despues["excluida"] = False
        if despues.get("opciones"):
            despues["opciones"] = {str(k): str(v)
                                   for k, v in despues["opciones"].items()}
        a, d = _comparable(antes), _comparable(despues)
        diferencias = [c for c in CLAVES if a.get(c) != d.get(c)]
        if not diferencias:
            continue
        for campo in diferencias:
            cambios.append({"codigo": codigo, "campo": campo,
                            "antes": a.get(campo), "despues": d.get(campo)})
        if antes.get("solo_marcadas") and not despues.get("solo_marcadas"):
            avisos.append(
                f"«{codigo}» se había ingestado solo con lo marcado: las "
                f"opciones no marcadas no están en la base y el reproceso no "
                f"las puede recuperar. Para tenerlas hay que volver a cargar el "
                f"archivo.")
        excluir_antes = set(antes.get("excluir_valores") or [])
        if excluir_antes - set(despues.get("excluir_valores") or []):
            avisos.append(
                f"«{codigo}»: los valores que se habían excluido al ingestar "
                f"({', '.join(sorted(excluir_antes))}) no están en la base; "
                f"dejar de excluirlos solo vale para la próxima ingesta.")
        plan[codigo] = {"antes": antes, "despues": despues}
    return {"preguntas": plan, "excluidas": excluidas, "cambios": cambios,
            "avisos": avisos}


# ════════════════════════════════════════════════════════════════════
#  Lo que se decide con cada respuesta
# ════════════════════════════════════════════════════════════════════

def decidir(plan, fila):
    """`(accion, etiqueta, texto)` para una respuesta.

    La misma función para la simulación de la revisión y para el lote: lo
    que la pantalla dice que va a pasar es lo que pasa.
    """
    codigo = fila["codigo"]
    if codigo in plan["excluidas"]:
        return BORRAR, None, None
    conf = plan["preguntas"].get(codigo)
    if not conf:
        return SIN_CAMBIOS, None, None
    crudo = crudo_de(conf["antes"], fila["valor_texto"])
    etiqueta, texto, motivo = ingesta.respuesta_de(conf["despues"], crudo)
    if motivo:
        return BORRAR, None, None
    if semantica.hash_texto(texto) == (fila.get("hash_texto")
                                       or semantica.hash_texto(fila["texto_embebido"])):
        return SIN_CAMBIOS, etiqueta, texto
    return REEMBEBER, etiqueta, texto


def _respuestas(conn_semantica, cuestionario_id=None, codigos=None, ids=None):
    """Las respuestas a decidir, con lo justo: nunca el `embedding`."""
    if ids is not None:
        donde, parametros = "r.id = any(%s::bigint[])", (list(ids),)
    else:
        donde = "p.cuestionario_id = %s and p.codigo = any(%s)"
        parametros = (cuestionario_id, list(codigos))
    return db.todas(
        conn_semantica,
        f"""select r.id, r.individuo_id, r.pregunta_id, r.valor_texto,
                   r.texto_embebido, r.hash_texto, p.codigo,
                   i.id_persona::text as id_persona
              from respuesta r
              join pregunta p  on p.id = r.pregunta_id
              join individuo i on i.id = r.individuo_id
             where {donde}
             order by r.id""",
        parametros)


def simular(plan, filas):
    """Cuántas respuestas se re-embeben, quedan igual o se borran, con
    algunos ejemplos de antes y después. No escribe nada."""
    conteo = {REEMBEBER: 0, SIN_CAMBIOS: 0, BORRAR: 0}
    ejemplos = []
    for fila in filas:
        accion, _etiqueta, texto = decidir(plan, fila)
        conteo[accion] += 1
        if accion != SIN_CAMBIOS and len(ejemplos) < 8 and not any(
                e["codigo"] == fila["codigo"] and e["accion"] == accion
                for e in ejemplos):
            ejemplos.append({"codigo": fila["codigo"], "accion": accion,
                             "antes": fila["texto_embebido"], "despues": texto})
    return {"reembeben": conteo[REEMBEBER], "sin_cambios": conteo[SIN_CAMBIOS],
            "se_borran": conteo[BORRAR], "ejemplos": ejemplos}


# ════════════════════════════════════════════════════════════════════
#  Lanzar
# ════════════════════════════════════════════════════════════════════

def _aplicar_a_las_preguntas(conn_semantica, cuestionario_id, plan):
    """Escribe la configuración nueva en `pregunta`. El original se conserva:
    si la pregunta no lo tenía (se cargó antes de la Fase 8), el original es
    lo que tenía antes de este primer cambio."""
    for codigo, conf in plan["preguntas"].items():
        antes, despues = conf["antes"], conf["despues"]
        pregunta = {**despues, "codigo": codigo}
        normalizacion = semantica.normalizacion_de(pregunta)
        # El guardia de PII, igual que en `semantica.upsert_preguntas`: lo que
        # va a `pregunta` no puede llevar una clave de PII.
        pii.validar_sin_pii({"texto": despues.get("texto"),
                             "opciones": despues.get("opciones"),
                             "normalizacion": normalizacion},
                            contexto="pregunta")
        db.ejecutar(
            conn_semantica,
            """update pregunta
                  set texto = %s, tipo = %s, opciones = %s::jsonb,
                      normalizacion = %s::jsonb,
                      texto_original = coalesce(texto_original, %s),
                      opciones_originales = coalesce(opciones_originales, %s::jsonb),
                      embedding_texto = case when texto is distinct from %s
                                             then null else embedding_texto end
                where cuestionario_id = %s and codigo = %s""",
            (despues.get("texto"), despues.get("tipo"),
             json.dumps(despues.get("opciones")) if despues.get("opciones") else None,
             json.dumps(normalizacion),
             antes.get("texto"),
             json.dumps(antes.get("opciones")) if antes.get("opciones") else None,
             despues.get("texto"),
             cuestionario_id, codigo))
    for codigo in plan["excluidas"]:
        # La pregunta se queda, marcada: una serie (Fase 4) puede apuntarle, y
        # el registro de que existió y se excluyó es parte de la historia del
        # estudio. Sus respuestas las borran los lotes.
        db.ejecutar(
            conn_semantica,
            """update pregunta
                  set normalizacion = normalizacion || '{"excluida": true}'::jsonb
                where cuestionario_id = %s and codigo = %s""",
            (cuestionario_id, codigo))


def lanzar(conn_boveda, conn_semantica, destino_tipo, destino_id, pedidas,
           actor=None, encolador=None, solo_revisar=False, forzar=False,
           tamano_lote=None):
    """Planifica el reproceso y, salvo `solo_revisar`, lo encola.

    `forzar` vuelve a pasar **todas** las respuestas del estudio por la
    configuración actual aunque no se haya pedido ningún cambio. Es la
    salida si un reproceso anterior quedó a medias (las preguntas se
    actualizaron y el encolado falló): el `hash_texto` hace que solo se
    re-embeba lo que de verdad quedó desactualizado.
    """
    ref_estudio = ref_estudio_de(conn_boveda, destino_tipo, destino_id)
    cuestionario = _cuestionario(conn_semantica, ref_estudio)
    actuales = _preguntas(conn_semantica, cuestionario["id"])

    if forzar and not pedidas:
        plan = {"preguntas": {
                    p["codigo"]: {"antes": {**{c: p.get(c) for c in CLAVES},
                                            "opciones_originales": p.get("opciones_originales"),
                                            "codigo": p["codigo"]},
                                  "despues": {**{c: p.get(c) for c in CLAVES},
                                              "codigo": p["codigo"]}}
                    for p in actuales if not p.get("excluida")},
                "excluidas": [], "cambios": [], "avisos": []}
    else:
        plan = planificar(actuales, pedidas)
    plan["operacion"] = OPERACION
    plan["ref_estudio"] = ref_estudio

    afectadas = list(plan["preguntas"]) + list(plan["excluidas"])
    if not afectadas:
        return 200, {"sin_cambios": True, "cambios": [],
                     "mensaje": "No hay nada distinto de lo que ya está cargado."}

    filas = _respuestas(conn_semantica, cuestionario["id"], codigos=afectadas)
    simulacion = simular(plan, filas)
    revision = {"cambios": plan["cambios"], "avisos": plan["avisos"],
                "respuestas_a_revisar": len(filas), **simulacion}
    if solo_revisar:
        return 200, revision

    abierto = _trabajo_abierto(conn_boveda, destino_tipo, destino_id)
    if abierto:
        raise Conflicto(
            "Hay una carga o un reproceso de este estudio en curso. Esperá a "
            "que termine: dos procesos sobre las mismas respuestas podrían "
            "pisarse.", {"trabajo_id": abierto["trabajo_id"]})

    # Primero las preguntas y el registro, del lado semántico. Los lotes no
    # leen `pregunta`: deciden con el plan congelado, así que el orden no
    # cambia el resultado, y deja el cambio registrado aunque no haya
    # respuestas que procesar.
    _aplicar_a_las_preguntas(conn_semantica, cuestionario["id"], plan)
    registro = db.una(
        conn_semantica,
        """insert into reproceso (cuestionario_id, cambios, respuestas_a_procesar)
           values (%s, %s::jsonb, %s) returning id""",
        (cuestionario["id"], json.dumps(plan["cambios"] or [{"forzado": True}],
                                        default=str), len(filas)))
    conn_semantica.commit()

    if not filas:
        return 200, {**revision, "reproceso_id": registro["id"],
                     "mensaje": "Las preguntas se actualizaron. No había "
                                "respuestas que re-embeber."}

    salida = diferida.encolar(
        conn_boveda, destino_tipo, destino_id, plan,
        [f["id"] for f in filas], actor=actor,
        tamano_lote=tamano_lote or RESPUESTAS_POR_LOTE, encolador=encolador)
    db.ejecutar(conn_semantica,
                "update reproceso set trabajo_id = %s where id = %s",
                (salida["trabajo_id"], registro["id"]))
    conn_semantica.commit()
    salida.update({"reproceso_id": registro["id"], "revision": revision})
    return 202, salida


# ════════════════════════════════════════════════════════════════════
#  El lote
# ════════════════════════════════════════════════════════════════════

def procesar_lote(conn_boveda, conn_semantica, plan, ids, proveedor=None):
    """Lo que corre cada tarea de un reproceso. Devuelve el resultado parcial
    que `diferida.consolidar` suma."""
    filas = _respuestas(conn_semantica, ids=ids)
    personas = {f["id_persona"] for f in filas}
    _habilitadas, bloqueadas = consentimiento.filtrar_con_consentimiento(
        conn_boveda, personas, consentimiento.SEMANTICO)
    bloqueadas = set(bloqueadas)

    a_borrar, a_embeber = [], []
    sin_cambios = sin_consentimiento = 0
    for fila in filas:
        accion, etiqueta, texto = decidir(plan, fila)
        if accion == BORRAR:
            a_borrar.append(fila["id"])
        elif accion == SIN_CAMBIOS:
            sin_cambios += 1
        elif fila["id_persona"] in bloqueadas:
            # El gate, igual que en la ingesta: el texto de quien no tiene
            # `uso_semantico` vigente no se manda al proveedor.
            sin_consentimiento += 1
        else:
            a_embeber.append({
                "individuo_id": fila["individuo_id"],
                "pregunta_id": fila["pregunta_id"],
                "valor_texto": etiqueta, "texto_embebido": texto,
                "hash_texto": semantica.hash_texto(texto), "embedding": None,
            })

    if a_borrar:
        db.ejecutar(conn_semantica,
                    "delete from respuesta where id = any(%s::bigint[])",
                    (a_borrar,))

    reembebidas = 0
    if a_embeber:
        proveedor = proveedor or mod_embeddings.crear()
        desajuste = esquema.desajuste_de_dimension(
            conn_semantica, getattr(proveedor, "dims", None))
        if desajuste:
            raise DatosInvalidos(desajuste, {"motivo": "dimension_de_embeddings"})
        textos = [f["texto_embebido"] for f in a_embeber]
        # Sub-lote a sub-lote, igual que la ingesta (R-ASYNC.2.b): nunca hay
        # más de un sub-lote de vectores vivo.
        for inicio, vectores in proveedor.embeber_por_lotes(textos):
            sublote = a_embeber[inicio:inicio + len(vectores)]
            for fila, vector in zip(sublote, vectores):
                fila["embedding"] = vector
            reembebidas += semantica.upsert_respuestas(conn_semantica, sublote)
            for fila in sublote:
                fila["embedding"] = None

    return {
        "operacion": OPERACION,
        "ref_estudio": plan.get("ref_estudio"),
        "reembebidas": reembebidas,
        "sin_cambios": sin_cambios,
        "borradas": len(a_borrar),
        "sin_consentimiento_reproceso": sin_consentimiento,
    }
