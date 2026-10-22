"""R-LN — La lista negra (SPEC_clientes_lista_negra_y_mejoras, D80).

Quien está en la lista negra **no se convoca**: ni desde `paneles` ni desde
COLOQUIO. Eso no lo decide este módulo sino la base: `v_persona_convocable`
(boveda/0031) deja afuera a quien tiene una entrada activa, y todo lo que
convoca —encuestas, muestreo, optimizador, COLOQUIO— ya lee esa vista.
Escribirlo en Python sería repetir la regla en cada consumidor.

Lo que **no** hace la lista negra, y es a propósito:

* No le quita a nadie el consentimiento. La ingesta y el reproceso siguen
  usando su contenido (`uso_semantico` se mira en
  `v_persona_finalidad_vigente`), y en los resultados de consulta la persona
  aparece **marcada**, no oculta.
* No se le avisa al panelista ni se ve en su portal (R-LN.4).

Una entrada es una fila; estar en la lista es tener una **activa** (sin
`removido_en`). Sacar a alguien completa la remoción y no borra nada: si
vuelve a entrar, se ve la historia completa. La baja se lleva todas sus
filas (cascada desde `persona`), hasta que se decida otra cosa con
asesoría.

El motivo lo van a leer otras personas del equipo y es un dato sobre alguien
identificado: la pantalla pide **hechos**, no juicios. Y entra en el derecho
de acceso: `historial()` es lo que se le entrega a quien pide sus datos.
"""

import json

from . import db
from .errores import Conflicto, DatosInvalidos, NoEncontrado

CATEGORIAS = {
    "calidad_respuestas": "Calidad de respuestas",
    "fraude_duplicacion": "Fraude o duplicación",
    "comportamiento": "Comportamiento",
    "otro": "Otro",
}
LARGO_MINIMO = 5
LARGO_MAXIMO = 1000


def _texto(valor, campo, que):
    texto = " ".join(str(valor or "").split())
    if len(texto) < LARGO_MINIMO:
        raise DatosInvalidos(
            f"Falta {que}: describí los hechos en una frase (por ejemplo, "
            f"«respondió en 40 segundos un cuestionario de 15 minutos»).",
            {"campo": campo})
    if len(texto) > LARGO_MAXIMO:
        raise DatosInvalidos(f"{que[0].upper()}{que[1:]} es demasiado largo "
                             f"(máximo {LARGO_MAXIMO} caracteres).", {"campo": campo})
    return texto


def _serializar(fila):
    return {
        "id": fila["id"],
        "id_persona": str(fila["id_persona"]),
        "categoria": fila["categoria"],
        "categoria_etiqueta": CATEGORIAS.get(fila["categoria"], fila["categoria"]),
        "detalle": fila["detalle"],
        "agregado_por": fila["agregado_por"],
        "agregado_en": fila["agregado_en"].isoformat(),
        "invitaciones_canceladas": fila["invitaciones_canceladas"] or [],
        "activa": fila["removido_en"] is None,
        "removido_por": fila["removido_por"],
        "removido_en": fila["removido_en"].isoformat() if fila["removido_en"] else None,
        "motivo_remocion": fila["motivo_remocion"],
    }


def _cancelar_invitaciones(conn, id_persona):
    """R-LN.2 — las invitaciones pendientes se cancelan: convocatorias que
    emitió el sistema, sin respuesta, de encuestas que no están cerradas. Lo
    que ya respondió no se toca (es un hecho) ni lo que vino de un archivo de
    campo (`origen = 'importacion'`)."""
    filas = db.todas(
        conn,
        """delete from participacion pa
            using encuesta e
            where e.id = pa.encuesta_id
              and pa.id_persona = %s
              and pa.origen = 'convocatoria'
              and not pa.respondio
              and e.estado <> 'cerrada'
        returning pa.encuesta_id, e.nombre, pa.convocado_en""",
        (str(id_persona),))
    return [{"encuesta_id": f["encuesta_id"], "encuesta": f["nombre"],
             "convocado_en": f["convocado_en"].isoformat()}
            for f in sorted(filas, key=lambda f: f["encuesta_id"])]


def agregar(conn, id_persona, categoria, detalle, actor=None):
    """Agrega a la persona a la lista negra. El motivo es obligatorio:
    categoría y un detalle breve. Cancela sus invitaciones pendientes."""
    categoria = (categoria or "").strip()
    if categoria not in CATEGORIAS:
        raise DatosInvalidos(
            "Elegí la categoría del motivo.",
            {"campo": "categoria", "categorias": list(CATEGORIAS)})
    detalle = _texto(detalle, "detalle", "el detalle del motivo")
    if not db.una(conn, "select 1 from persona where id_persona = %s", (str(id_persona),)):
        raise NoEncontrado(f"No existe la persona {id_persona}.")
    if activa_de(conn, id_persona):
        raise Conflicto("La persona ya está en la lista negra.",
                        {"motivo": "ya_en_lista_negra"})
    canceladas = _cancelar_invitaciones(conn, id_persona)
    fila = db.una(
        conn,
        """insert into lista_negra (id_persona, categoria, detalle, agregado_por,
                                    invitaciones_canceladas)
           values (%s, %s, %s, %s, %s::jsonb)
        returning *""",
        (str(id_persona), categoria, detalle, actor, json.dumps(canceladas)))
    return _serializar(fila)


def sacar(conn, id_persona, motivo, actor=None):
    """Saca a la persona de la lista negra, con el motivo de la remoción. La
    entrada queda en el historial."""
    motivo = _texto(motivo, "motivo", "el motivo para sacarla")
    fila = db.una(
        conn,
        """update lista_negra
              set removido_en = now(), removido_por = %s, motivo_remocion = %s
            where id_persona = %s and removido_en is null
        returning *""",
        (actor, motivo, str(id_persona)))
    if not fila:
        raise Conflicto("La persona no está en la lista negra.",
                        {"motivo": "no_en_lista_negra"})
    return _serializar(fila)


def activa_de(conn, id_persona):
    fila = db.una(
        conn, "select * from lista_negra where id_persona = %s and removido_en is null",
        (str(id_persona),))
    return _serializar(fila) if fila else None


def historial(conn, id_persona):
    """Todas las entradas de la persona, la más reciente primero: lo que se
    entrega en un pedido de acceso (R-LN.4) y lo que ve quien la vuelve a
    agregar (R-LN.3)."""
    return [_serializar(f) for f in db.todas(
        conn,
        "select * from lista_negra where id_persona = %s order by agregado_en desc, id desc",
        (str(id_persona),))]


def activas_de(conn, ids_persona):
    """`{id_persona: entrada}` de las que están en la lista negra hoy, de
    una vez."""
    ids = [str(i) for i in dict.fromkeys(i for i in (ids_persona or []) if i)]
    if not ids:
        return {}
    return {str(f["id_persona"]): _serializar(f) for f in db.todas(
        conn,
        """select * from lista_negra
            where id_persona = any(%s::uuid[]) and removido_en is null""",
        (ids,))}


def marcar(conn, items, con_motivo=False):
    """R-LN.2 — marca los resultados de una consulta: no los oculta. Cada
    ítem lleva `lista_negra`: `None`, o la marca; el motivo, solo para quien
    tiene el permiso de verlo."""
    activas = activas_de(conn, [i.get("id_persona") for i in items or []])
    for item in items or []:
        entrada = activas.get(str(item.get("id_persona")))
        if not entrada:
            item["lista_negra"] = None
        elif con_motivo:
            item["lista_negra"] = {
                k: entrada[k] for k in ("categoria", "categoria_etiqueta", "detalle",
                                        "agregado_por", "agregado_en")}
        else:
            item["lista_negra"] = {"en_lista_negra": True}
    return items


def listar(conn, busqueda=None, limite=50, desde=0, incluir_removidas=False):
    """R-LN.3 — la sección: quiénes están, con su motivo, quién y cuándo.
    Paginada, con búsqueda por nombre, documento o correo, como Panelistas."""
    filtro = ""
    parametros = []
    if busqueda:
        filtro = "and (p.nombre ilike %s or p.documento ilike %s or p.email ilike %s)"
        patron = f"%{busqueda.strip()}%"
        parametros += [patron, patron, patron]
    estado = "" if incluir_removidas else "and ln.removido_en is null"
    base = f"""from lista_negra ln join persona p on p.id_persona = ln.id_persona
               where true {estado} {filtro}"""
    total = db.una(conn, f"select count(*)::int as n {base}", tuple(parametros))["n"]
    filas = db.todas(
        conn,
        f"""select ln.*, p.nombre, p.documento,
                   (select count(*) from lista_negra o
                     where o.id_persona = ln.id_persona)::int as entradas
              {base}
             order by ln.agregado_en desc, ln.id desc
             limit %s offset %s""",
        tuple(parametros) + (int(limite), int(desde)))
    return {
        "total": total, "limite": int(limite), "desde": int(desde),
        "items": [{**_serializar(f), "nombre": f["nombre"], "documento": f["documento"],
                   "entradas": f["entradas"]} for f in filas],
    }
