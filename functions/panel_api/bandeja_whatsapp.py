"""R-WA (D98) — la bandeja de WhatsApp: lo que la gente contesta, en paneles.

Hasta acá las respuestas de los Flows, los comentarios y los estados de
entrega llegaban a una planilla de Google por un Apps Script: Meta admite
**una sola** URL de callback por app y estaba en uso. Este módulo es el
webhook que la reemplaza, la bandeja que la muestra y el armado del conjunto
de datos para ingestar las respuestas de una encuesta por el proceso de
siempre.

Cuatro reglas que no hay que deshacer:

* **Primero se guarda, después se procesa.** El aviso se acepta solo con la
  firma `X-Hub-Signature-256` verificada contra la clave secreta de la app,
  y se guarda **crudo** (`whatsapp_aviso`) y commiteado antes de tocar nada
  más. El procesamiento corre después, en su propia transacción: si falla,
  el aviso queda `fallido` con el motivo y se reprocesa. A Meta se le
  contesta 200 igual —el aviso ya es nuestro—, porque un 5xx lo haría
  reintentar algo que no va a cambiar. Nunca se pierde.
* **Idempotente por id.** Meta reenvía. Un mensaje es único por su id
  (`whatsapp_entrante.id_mensaje`), un estado por `(mensaje, estado)`, y el
  aviso entero por la huella de sus bytes.
* **La identidad sale del envío.** `context.id` → `envio.id_mensaje_meta` →
  la persona y la encuesta. El `flow_token` (el `id_persona` con que se
  mandó) solo se compara: si no coincide, es una inconsistencia, no otra
  persona. Sin envío es una encuesta de afuera y se agrupa como en la
  planilla (`tpl=NOMBRE|…`, lo anterior a `|`, o «GENERAL»).
* **Todo vive en la bóveda**: tiene teléfonos. Lo de afuera se borra a los
  `WHATSAPP_CONSERVAR_AFUERA_DIAS`; el aviso crudo, antes
  (`WHATSAPP_CONSERVAR_AVISO_DIAS`); y la baja de un panelista se lleva lo
  suyo por `id_persona` **y** por su teléfono (`borrar_de_persona`, desde
  `bajas.retirar`).
"""

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone

from . import db, preferencias, whatsapp
from .errores import Conflicto, DatosInvalidos, NoEncontrado

GENERAL = "GENERAL"

# Cuánto se conserva lo que no es de un panelista y el aviso crudo. El
# crudo, más corto: lo procesado ya está en sus tablas y el crudo repite el
# teléfono y el nombre de perfil de WhatsApp.
CONSERVAR_AFUERA_DIAS = 180
CONSERVAR_AVISO_DIAS = 30

# Cuántos avisos pendientes de antes (un proceso que murió a mitad) se
# reintentan en cada aviso nuevo. Los `fallidos` no: esos se reprocesan a
# mano, porque reintentarlos solos en cada aviso sería un bucle.
PENDIENTES_POR_AVISO = 20

# R-WA.2 / R-CF.7 — de los estados de Meta a los de `envio`. `sent` no
# cambia nada: «aceptado» ya dice que salió.
ESTADOS_DE_META = {"delivered": "entregado", "read": "leido", "failed": "fallido"}
# De qué estado se puede pasar a cuál: nunca se retrocede (los avisos llegan
# desordenados: un `delivered` puede llegar después del `read`).
ESTADOS_PREVIOS = {
    "entregado": ("aceptado",),
    "leido": ("aceptado", "entregado"),
    "fallido": ("aceptado",),
}

INCONSISTENCIA_TOKEN = "flow_token_distinto"
TEXTO_INCONSISTENCIA = {
    INCONSISTENCIA_TOKEN: "El flow_token no coincide con la persona a la que se le "
                          "envió el mensaje. La identidad es la del envío.",
}

SIN_ETIQUETA = "sin etiqueta"


def config(entorno=None):
    entorno = os.environ if entorno is None else entorno

    def _dias(valor, defecto):
        try:
            return max(1, int(valor))
        except (TypeError, ValueError):
            return defecto

    return {
        "verify_token": (entorno.get("WHATSAPP_VERIFY_TOKEN") or "").strip(),
        "app_secret": (entorno.get("WHATSAPP_APP_SECRET") or "").strip(),
        "conservar_afuera_dias": _dias(
            entorno.get("WHATSAPP_CONSERVAR_AFUERA_DIAS") or CONSERVAR_AFUERA_DIAS,
            CONSERVAR_AFUERA_DIAS),
        "conservar_aviso_dias": _dias(
            entorno.get("WHATSAPP_CONSERVAR_AVISO_DIAS") or CONSERVAR_AVISO_DIAS,
            CONSERVAR_AVISO_DIAS),
    }


# ════════════════════════════════════════════════════════════════════
#  R-WA.1 · El webhook
# ════════════════════════════════════════════════════════════════════

def verificar_suscripcion(consulta, entorno=None):
    """La verificación de Meta al dar de alta la URL: `hub.mode=subscribe`,
    `hub.verify_token` y `hub.challenge`. Devuelve `(status, texto)`.

    Sin token configurado no verifica nada: un webhook que acepta cualquier
    token se puede dar de alta desde cualquier app.
    """
    datos = config(entorno)
    token = consulta.get("hub.verify_token") or ""
    if (consulta.get("hub.mode") == "subscribe" and datos["verify_token"]
            and hmac.compare_digest(token, datos["verify_token"])):
        return 200, str(consulta.get("hub.challenge") or "")
    return 403, "verificación rechazada"


def firma_valida(cuerpo, encabezado, secreto):
    """`X-Hub-Signature-256: sha256=<hex>` sobre los bytes **crudos** del
    cuerpo. Sin secreto configurado, nada es válido (falla cerrado)."""
    if not secreto or not encabezado:
        return False
    esperado = "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, encabezado.strip())


def guardar_aviso(conn, cuerpo):
    """Guarda el aviso crudo. Devuelve `(aviso_id, nuevo)`.

    La huella es de los bytes: el mismo aviso reenviado no se guarda dos
    veces. Un cuerpo que no es JSON se guarda igual (como texto) y queda
    `fallido`: perderlo sería peor que guardarlo raro.
    """
    huella = hashlib.sha256(cuerpo).hexdigest()
    texto = cuerpo.decode("utf-8", errors="replace")
    try:
        contenido, estado, error = json.loads(texto), "pendiente", None
        if not isinstance(contenido, dict):
            raise ValueError("no es un objeto")
    except ValueError:
        contenido, estado, error = {"no_json": texto[:20000]}, "fallido", "El aviso no es JSON."
    fila = db.una(conn, """
        insert into whatsapp_aviso (huella, cuerpo, estado, error)
             values (%s, %s::jsonb, %s, %s)
        on conflict (huella) do nothing
        returning id
    """, (huella, json.dumps(contenido, ensure_ascii=False), estado, error))
    if fila:
        return fila["id"], True
    existente = db.una(conn, "select id from whatsapp_aviso where huella = %s", (huella,))
    return existente["id"], False


def atender(conn, metodo, consulta, cuerpo, encabezados, entorno=None):
    """El webhook entero, sin Flask: `(status, texto)`.

    `conn` es la bóveda. Lo usa `main.whatsappwebhook` y lo usan las pruebas.
    """
    if metodo == "GET":
        return verificar_suscripcion(consulta or {}, entorno)
    if metodo != "POST":
        return 405, "método no admitido"
    datos = config(entorno)
    firma = (encabezados.get("X-Hub-Signature-256")
             or encabezados.get("x-hub-signature-256") or "")
    if not firma_valida(cuerpo or b"", firma, datos["app_secret"]):
        # Sin contenido en el log: el cuerpo tiene teléfonos.
        print("[whatsapp] aviso con firma inválida, rechazado")
        return 403, "firma inválida"

    aviso_id, nuevo = guardar_aviso(conn, cuerpo)
    conn.commit()
    if nuevo:
        procesar_aviso(conn, aviso_id)
    # Lo que quedó a medias de antes (un proceso que murió). Nunca bloquea
    # la respuesta a Meta por un error: el aviso de hoy ya está guardado.
    try:
        procesar_pendientes(conn, excepto=aviso_id)
        purgar(conn, entorno)
        conn.commit()
    except Exception as error:  # noqa: BLE001
        conn.rollback()
        print(f"[whatsapp] mantenimiento fallido: {type(error).__name__}")
    return 200, "ok"


def procesar_aviso(conn, aviso_id):
    """Procesa un aviso guardado, en su transacción. Nunca levanta: lo que
    falla queda `fallido` con el motivo y el crudo sigue ahí."""
    fila = db.una(conn, "select cuerpo from whatsapp_aviso where id = %s", (aviso_id,))
    if not fila:
        return {"estado": "no_existe"}
    try:
        resumen = _procesar_cuerpo(conn, fila["cuerpo"] or {}, aviso_id)
        db.ejecutar(conn, """
            update whatsapp_aviso set estado = 'procesado', error = null,
                   procesado_en = now() where id = %s""", (aviso_id,))
        conn.commit()
        return {"estado": "procesado", **resumen}
    except Exception as error:  # noqa: BLE001
        conn.rollback()
        # El motivo, sin contenido: el tipo y el mensaje de la excepción no
        # traen el cuerpo, y el cuerpo ya está guardado.
        motivo = f"{type(error).__name__}: {str(error)[:300]}"
        db.ejecutar(conn, """
            update whatsapp_aviso set estado = 'fallido', error = %s,
                   procesado_en = now() where id = %s""", (motivo, aviso_id))
        conn.commit()
        print(f"[whatsapp] aviso {aviso_id} fallido: {type(error).__name__}")
        return {"estado": "fallido", "error": motivo}


def procesar_pendientes(conn, excepto=None, limite=PENDIENTES_POR_AVISO):
    ids = [f["id"] for f in db.todas(conn, """
        select id from whatsapp_aviso
         where estado = 'pendiente' and id <> coalesce(%s, -1)
         order by recibido_en limit %s
    """, (excepto, limite))]
    return [procesar_aviso(conn, i) for i in ids]


def reprocesar(conn, ids=None):
    """R-WA.1 — vuelve a procesar los avisos fallidos (o los indicados). Es
    seguro de repetir: todo lo que escribe es idempotente por id."""
    if ids:
        filas = db.todas(conn, "select id from whatsapp_aviso where id = any(%s) order by id",
                         ([int(i) for i in ids],))
    else:
        filas = db.todas(conn, """
            select id from whatsapp_aviso where estado <> 'procesado' order by id""")
    resultados = [procesar_aviso(conn, f["id"]) for f in filas]
    return {"reprocesados": len(resultados),
            "procesados": sum(1 for r in resultados if r["estado"] == "procesado"),
            "fallidos": sum(1 for r in resultados if r["estado"] == "fallido")}


def estado_de_avisos(conn):
    fila = db.una(conn, """
        select count(*) filter (where estado = 'pendiente') as pendientes,
               count(*) filter (where estado = 'fallido')   as fallidos,
               max(recibido_en)                              as ultimo
          from whatsapp_aviso
    """)
    return dict(fila or {"pendientes": 0, "fallidos": 0, "ultimo": None})


# ════════════════════════════════════════════════════════════════════
#  R-WA.2 / R-WA.3 · Tres tipos de aviso, y a quién pertenecen
# ════════════════════════════════════════════════════════════════════

def _procesar_cuerpo(conn, cuerpo, aviso_id):
    if "no_json" in cuerpo:
        raise DatosInvalidos("El aviso no es JSON.")
    mensajes = estados = 0
    for entrada in cuerpo.get("entry") or []:
        for cambio in entrada.get("changes") or []:
            valor = cambio.get("value") or {}
            for mensaje in valor.get("messages") or []:
                mensajes += _guardar_mensaje(conn, mensaje, aviso_id)
            for estado in valor.get("statuses") or []:
                estados += _guardar_estado(conn, estado)
    return {"mensajes": mensajes, "estados": estados}


def grupo_de_token(token):
    """Como la planilla: `tpl=NOMBRE|…` → NOMBRE; si no, lo anterior a `|`;
    si no, «GENERAL»."""
    token = (token or "").strip()
    if not token:
        return GENERAL
    coincidencia = re.search(r"(?:^|\|)\s*tpl=([^|]+)", token)
    if coincidencia and coincidencia.group(1).strip():
        return coincidencia.group(1).strip()
    if "|" in token:
        return token.split("|", 1)[0].strip() or GENERAL
    return GENERAL


def _telefono(crudo):
    """El `from` de Meta viene en dígitos sin `+`. Se guarda en E.164, que es
    como está el celular en la bóveda (así la baja lo encuentra)."""
    digitos = re.sub(r"\D", "", str(crudo or ""))
    if not digitos:
        return ""
    return preferencias.normalizar_celular("+" + digitos) or ("+" + digitos)


def _momento(timestamp):
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _contenido(mensaje):
    """`(tipo, respuesta, flow_token, texto)` de un mensaje de Meta."""
    tipo_meta = mensaje.get("type")
    interactivo = mensaje.get("interactive") or {}
    if tipo_meta == "interactive" and interactivo.get("type") == "nfm_reply":
        crudo = (interactivo.get("nfm_reply") or {}).get("response_json") or "{}"
        respuesta = json.loads(crudo) if isinstance(crudo, str) else dict(crudo)
        token = respuesta.pop("flow_token", None)
        return "flow", respuesta, (str(token) if token is not None else None), None
    if tipo_meta == "text":
        return "comentario", None, None, (mensaje.get("text") or {}).get("body")
    if tipo_meta == "button":
        boton = mensaje.get("button") or {}
        return "comentario", None, None, boton.get("text") or boton.get("payload")
    if tipo_meta == "interactive":
        elegido = interactivo.get(interactivo.get("type") or "") or {}
        return "comentario", None, None, elegido.get("title") or elegido.get("id")
    # Una foto, un audio, una ubicación: se registra que llegó, sin bajarlo.
    return "comentario", None, None, f"[{tipo_meta or 'mensaje'}]"


def _envio_de(conn, contexto_id):
    if not contexto_id:
        return None
    return db.una(conn, """
        select e.id, e.id_persona, e.encuesta_mensaje_id, m.encuesta_id
          from envio e join encuesta_mensaje m on m.id = e.encuesta_mensaje_id
         where e.id_mensaje_meta = %s
    """, (contexto_id,))


def _guardar_mensaje(conn, mensaje, aviso_id):
    id_mensaje = mensaje.get("id")
    if not id_mensaje:
        return 0
    tipo, respuesta, token, texto = _contenido(mensaje)
    contexto_id = (mensaje.get("context") or {}).get("id")
    envio = _envio_de(conn, contexto_id)
    inconsistencia = grupo = None
    if envio:
        # R-WA.3 — la identidad es la del envío. El token solo se compara.
        if tipo == "flow" and token and token != str(envio["id_persona"]):
            inconsistencia = INCONSISTENCIA_TOKEN
    else:
        grupo = grupo_de_token(token)
    return db.ejecutar(conn, """
        insert into whatsapp_entrante
               (id_mensaje, tipo, telefono, enviado_en, contexto_id, envio_id,
                id_persona, encuesta_id, encuesta_mensaje_id, grupo, flow_token,
                respuesta, texto, inconsistencia, aviso_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
        on conflict (id_mensaje) do nothing
    """, (id_mensaje, tipo, _telefono(mensaje.get("from")), _momento(mensaje.get("timestamp")),
          contexto_id, envio["id"] if envio else None,
          str(envio["id_persona"]) if envio else None,
          envio["encuesta_id"] if envio else None,
          envio["encuesta_mensaje_id"] if envio else None,
          grupo, token,
          json.dumps(respuesta, ensure_ascii=False) if respuesta is not None else None,
          texto, inconsistencia, aviso_id))


def _guardar_estado(conn, estado):
    """R-WA.2 / R-CF.7 — el estado de un mensaje enviado. Se guarda siempre
    (también los de mensajes que no son de paneles) y actualiza `envio`
    solo hacia adelante."""
    id_meta, crudo = estado.get("id"), (estado.get("status") or "").lower()
    if not id_meta or not crudo:
        return 0
    errores = estado.get("errors") or []
    error = "; ".join(str(e.get("title") or e.get("message") or e.get("code") or "")
                      for e in errores if isinstance(e, dict)) or None
    nuevos = db.ejecutar(conn, """
        insert into whatsapp_estado (id_mensaje_meta, estado, ocurrido_en, error)
             values (%s, %s, %s, %s)
        on conflict (id_mensaje_meta, estado) do nothing
    """, (id_meta, crudo, _momento(estado.get("timestamp")), error))
    nuevo = ESTADOS_DE_META.get(crudo)
    if nuevo:
        db.ejecutar(conn, """
            update envio set estado = %s, actualizado_en = now(),
                   error = case when %s = 'fallido' then coalesce(%s, error) else error end
             where id_mensaje_meta = %s and estado = any(%s)
        """, (nuevo, nuevo, error, id_meta, list(ESTADOS_PREVIOS[nuevo])))
    return nuevos


# ════════════════════════════════════════════════════════════════════
#  R-WA.5 · Conservación y baja
# ════════════════════════════════════════════════════════════════════

def purgar(conn, entorno=None):
    """Lo de afuera (sin persona de paneles) vence a los N días; el aviso
    crudo procesado, antes. Corre con cada aviso y al abrir la bandeja: no
    hace falta un proceso programado."""
    datos = config(entorno)
    afuera = db.ejecutar(conn, """
        delete from whatsapp_entrante
         where id_persona is null and recibido_en < now() - make_interval(days => %s)
    """, (datos["conservar_afuera_dias"],))
    avisos = db.ejecutar(conn, """
        delete from whatsapp_aviso
         where (estado = 'procesado' and recibido_en < now() - make_interval(days => %s))
            or recibido_en < now() - make_interval(days => %s)
    """, (datos["conservar_aviso_dias"], datos["conservar_afuera_dias"]))
    return {"entrantes": afuera, "avisos": avisos}


def borrar_de_persona(conn, id_persona):
    """R-WA.5 — la baja se lleva lo de la bandeja: por `id_persona` y por su
    teléfono (lo que llegó de una encuesta de afuera desde su número), y los
    avisos crudos que lo nombran. Se llama desde `bajas.retirar`, antes del
    `delete from persona`."""
    fila = db.una(conn, "select celular from persona where id_persona = %s", (str(id_persona),))
    telefono = preferencias.normalizar_celular((fila or {}).get("celular")) if fila else None
    entrantes = db.ejecutar(conn, """
        delete from whatsapp_entrante
         where id_persona = %s or (%s::text is not null and telefono = %s)
    """, (str(id_persona), telefono, telefono))
    avisos = 0
    if telefono:
        digitos = re.sub(r"\D", "", telefono)
        avisos = db.ejecutar(conn, """
            delete from whatsapp_aviso where strpos(cuerpo::text, %s) > 0
        """, (f'"{digitos}"',))
    return {"entrantes": entrantes, "avisos": avisos}


# ════════════════════════════════════════════════════════════════════
#  R-WA.4 · La bandeja
# ════════════════════════════════════════════════════════════════════

def grupos(conn):
    """Las encuestas y plantillas con algo recibido, con sus cuentas."""
    filas = db.todas(conn, """
        select w.encuesta_id, e.nombre as encuesta, w.grupo,
               count(*) filter (where w.tipo = 'flow')       as respuestas,
               count(*) filter (where w.tipo = 'comentario') as comentarios,
               count(*) filter (where w.inconsistencia is not null) as inconsistencias,
               max(w.recibido_en) as ultima
          from whatsapp_entrante w
          left join encuesta e on e.id = w.encuesta_id
         group by w.encuesta_id, e.nombre, w.grupo
         order by max(w.recibido_en) desc
    """)
    salida = []
    for f in filas:
        de_paneles = f["encuesta_id"] is not None
        salida.append({
            "clave": f"encuesta:{f['encuesta_id']}" if de_paneles else f"grupo:{f['grupo']}",
            "tipo": "encuesta" if de_paneles else "grupo",
            "encuesta_id": f["encuesta_id"],
            "nombre": f["encuesta"] if de_paneles else f["grupo"],
            "respuestas": f["respuestas"], "comentarios": f["comentarios"],
            "inconsistencias": f["inconsistencias"], "ultima": f["ultima"],
        })
    return salida


def _filtro(encuesta_id=None, grupo=None, desde=None, hasta=None, tipo="flow"):
    condiciones, params = ["w.tipo = %s"], [tipo]
    if encuesta_id not in (None, ""):
        condiciones.append("w.encuesta_id = %s")
        params.append(int(encuesta_id))
    elif grupo not in (None, ""):
        condiciones.append("w.encuesta_id is null and w.grupo = %s")
        params.append(str(grupo))
    if desde:
        condiciones.append("w.recibido_en >= %s::date")
        params.append(str(desde))
    if hasta:
        condiciones.append("w.recibido_en < %s::date + 1")
        params.append(str(hasta))
    return " and ".join(condiciones), params


def orden_de_campos(claves):
    """Las de pantalla (`screen_N_<Componente>_M`) por número de pantalla y,
    dentro de ella, por el número del componente, que es como las numera el
    constructor de Meta; el resto, después, en el orden en que aparecen.

    No se puede usar el orden de llegada: `jsonb` no lo conserva."""
    vistas = list(dict.fromkeys(claves))

    def clave(par):
        posicion, nombre = par
        coincidencia = re.match(r"screen_(\d+)_(?:.*?_(\d+)$)?", nombre)
        if not coincidencia:
            return (1, 0, 0, posicion)
        numero = int(coincidencia.group(2)) if coincidencia.group(2) else 10 ** 6
        return (0, int(coincidencia.group(1)), numero, posicion)

    return [n for _, n in sorted(enumerate(vistas), key=clave)]


def _valor_de_celda(valor):
    if isinstance(valor, list):
        return ", ".join(str(v) for v in valor)
    if isinstance(valor, dict):
        return json.dumps(valor, ensure_ascii=False)
    return "" if valor is None else str(valor)


def respuestas(conn, encuesta_id=None, grupo=None, desde=None, hasta=None, limite=1000):
    where, params = _filtro(encuesta_id, grupo, desde, hasta, "flow")
    filas = db.todas(conn, f"""
        select w.id, w.recibido_en, w.enviado_en, w.telefono, w.flow_token,
               w.respuesta, w.inconsistencia, w.id_persona, w.encuesta_id,
               coalesce(e.nombre, w.grupo) as origen, m.nombre as mensaje
          from whatsapp_entrante w
          left join encuesta e on e.id = w.encuesta_id
          left join encuesta_mensaje m on m.id = w.encuesta_mensaje_id
         where {where}
         order by w.recibido_en desc
         limit %s
    """, (*params, int(limite) + 1))
    recortado = len(filas) > int(limite)
    filas = filas[:int(limite)]
    columnas = orden_de_campos([c for f in reversed(filas) for c in (f["respuesta"] or {})])
    return {
        "columnas": columnas,
        "filas": [{
            "id": f["id"], "recibido_en": f["recibido_en"], "enviado_en": f["enviado_en"],
            "telefono": f["telefono"], "token": f["flow_token"],
            "origen": f["origen"], "mensaje": f["mensaje"],
            "id_persona": str(f["id_persona"]) if f["id_persona"] else None,
            "inconsistencia": TEXTO_INCONSISTENCIA.get(f["inconsistencia"], f["inconsistencia"]),
            "campos": {c: _valor_de_celda((f["respuesta"] or {}).get(c)) for c in columnas},
        } for f in filas],
        "recortado": recortado,
    }


def comentarios(conn, encuesta_id=None, grupo=None, desde=None, hasta=None, limite=1000):
    where, params = _filtro(encuesta_id, grupo, desde, hasta, "comentario")
    filas = db.todas(conn, f"""
        select w.id, w.recibido_en, w.telefono, w.texto, w.id_persona,
               coalesce(e.nombre, w.grupo) as origen, m.nombre as mensaje
          from whatsapp_entrante w
          left join encuesta e on e.id = w.encuesta_id
          left join encuesta_mensaje m on m.id = w.encuesta_mensaje_id
         where {where}
         order by w.recibido_en desc
         limit %s
    """, (*params, int(limite)))
    return {"filas": [{**dict(f), "id_persona": str(f["id_persona"]) if f["id_persona"] else None}
                      for f in filas]}


def exportar(conn, filtro, actor=None):
    """R-WA.4 — el CSV de lo que la bandeja muestra con ese filtro. Tiene
    teléfonos: cada exportación deja su fila en `whatsapp_exportacion`."""
    filtro = {k: v for k, v in (filtro or {}).items()
              if k in ("encuesta_id", "grupo", "desde", "hasta", "tipo") and v not in (None, "")}
    tipo = filtro.get("tipo") or "flow"
    if tipo not in ("flow", "comentario"):
        raise DatosInvalidos("El tipo de exportación es `flow` o `comentario`.", {"campo": "tipo"})
    args = {k: filtro.get(k) for k in ("encuesta_id", "grupo", "desde", "hasta")}
    salida = io.StringIO()
    escritor = csv.writer(salida)
    if tipo == "flow":
        datos = respuestas(conn, **args, limite=100000)
        escritor.writerow(["fecha", "telefono", "encuesta_o_plantilla", "token",
                           "inconsistencia", *datos["columnas"]])
        for f in datos["filas"]:
            escritor.writerow([f["recibido_en"], f["telefono"], f["origen"], f["token"] or "",
                               f["inconsistencia"] or "",
                               *[f["campos"][c] for c in datos["columnas"]]])
    else:
        datos = comentarios(conn, **args, limite=100000)
        escritor.writerow(["fecha", "telefono", "encuesta_o_plantilla", "mensaje", "texto"])
        for f in datos["filas"]:
            escritor.writerow([f["recibido_en"], f["telefono"], f["origen"],
                               f["mensaje"] or "", f["texto"] or ""])
    filas = len(datos["filas"])
    db.ejecutar(conn, """
        insert into whatsapp_exportacion (actor, actor_email, filtro, filas)
             values (%s, %s, %s::jsonb, %s)
    """, (getattr(actor, "uid", None), getattr(actor, "email", None),
          json.dumps(filtro, ensure_ascii=False, default=str), filas))
    return {"csv": salida.getvalue(), "filas": filas,
            "nombre": f"whatsapp_{tipo}_{datetime.now(timezone.utc):%Y%m%d}.csv"}


def exportaciones(conn, limite=50):
    return db.todas(conn, """
        select id, creado_en, actor_email, filtro, filas from whatsapp_exportacion
         order by creado_en desc limit %s""", (int(limite),))


# ════════════════════════════════════════════════════════════════════
#  R-WA.6 · Ingestar las respuestas de una encuesta como un SAV
# ════════════════════════════════════════════════════════════════════

# Los componentes de un Flow que juntan un dato, y cómo se tratan.
TIPOS_DE_COMPONENTE = {
    "RadioButtonsGroup": "cerrada", "Dropdown": "cerrada",
    "CheckboxGroup": "multiple", "ChipsSelector": "multiple",
    "TextInput": "abierta", "TextArea": "abierta",
    "DatePicker": "abierta", "CalendarPicker": "abierta",
    "OptIn": "optin",
}
_TEXTOS = ("TextHeading", "TextSubheading", "TextBody", "TextCaption", "RichText")
_EXPRESION = re.compile(r"^\$\{(.+)\}$")


def _texto_fijo(valor):
    """Una etiqueta escrita en el Flow; una dinámica (`${data.…}`) no sirve
    de diccionario."""
    if isinstance(valor, list):
        valor = " ".join(str(v) for v in valor)
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    return None if not valor or _EXPRESION.match(valor) else valor


def _recorrer(nodo, pantalla, componentes, acciones, previo):
    """Junta los componentes con nombre y las acciones con payload."""
    if isinstance(nodo, list):
        for hijo in nodo:
            previo = _recorrer(hijo, pantalla, componentes, acciones, previo)
        return previo
    if not isinstance(nodo, dict):
        return previo
    tipo = nodo.get("type")
    for clave, valor in nodo.items():
        if clave.endswith("-action") and isinstance(valor, dict) and isinstance(valor.get("payload"), dict):
            acciones.append((pantalla["id"], valor.get("name"), valor["payload"]))
    if tipo in TIPOS_DE_COMPONENTE and nodo.get("name"):
        opciones = {}
        for opcion in nodo.get("data-source") if isinstance(nodo.get("data-source"), list) else []:
            if isinstance(opcion, dict) and opcion.get("id") is not None:
                titulo = _texto_fijo(opcion.get("title"))
                opciones[str(opcion["id"])] = titulo
        etiqueta = _texto_fijo(nodo.get("label")) or _texto_fijo(nodo.get("text"))
        componentes[(pantalla["id"], nodo["name"])] = {
            "nombre": nodo["name"], "pantalla": pantalla["id"],
            "pantalla_titulo": pantalla.get("title") or pantalla["id"],
            "tipo": TIPOS_DE_COMPONENTE[tipo], "componente": tipo,
            "etiqueta": etiqueta, "texto_previo": previo, "opciones": opciones,
        }
        previo = None
    elif tipo in _TEXTOS:
        previo = _texto_fijo(nodo.get("text")) or previo
    for clave in ("children", "layout"):
        if clave in nodo:
            previo = _recorrer(nodo[clave], pantalla, componentes, acciones, previo)
    return previo


def diccionario(definicion):
    """De la definición del Flow (su `flow.json`) al diccionario de la
    ingesta: `{campo: {texto, tipo, opciones, sin_etiqueta}}`.

    El campo es la clave con que vuelve la respuesta (`screen_0_Radio_1`),
    que es la del payload de la acción que la manda; su valor dice de qué
    componente sale (`${form.Radio_1}`, `${screen.X.form.Radio_1}` o
    `${data.screen_0_Radio_1}`, pasado de una pantalla a otra).
    """
    componentes, acciones = {}, []
    pantallas = (definicion or {}).get("screens") or []
    for pantalla in pantallas:
        if isinstance(pantalla, dict) and pantalla.get("id"):
            _recorrer(pantalla.get("layout") or {}, pantalla, componentes, acciones, None)

    expresiones = {}
    for pantalla_id, _nombre, payload in acciones:
        for clave, valor in payload.items():
            if isinstance(valor, str) and _EXPRESION.match(valor.strip()):
                expresiones.setdefault(clave, (pantalla_id, valor.strip()))

    def resolver(clave, vistos=()):
        if clave not in expresiones or clave in vistos:
            return None
        pantalla_id, expresion = expresiones[clave]
        interior = _EXPRESION.match(expresion).group(1)
        partes = interior.split(".")
        if partes[0] == "form" and len(partes) == 2:
            return componentes.get((pantalla_id, partes[1]))
        if partes[0] == "screen" and len(partes) == 4 and partes[2] == "form":
            return componentes.get((partes[1], partes[3]))
        if partes[0] == "data" and len(partes) == 2:
            return resolver(partes[1], (*vistos, clave))
        return None

    por_nombre = {}
    for comp in componentes.values():
        por_nombre.setdefault(comp["nombre"], []).append(comp)
    indice_de_pantalla = {p.get("id"): i for i, p in enumerate(pantallas) if isinstance(p, dict)}

    def por_convencion(clave):
        # El constructor de Meta nombra `screen_<n>_<Nombre>`.
        coincidencia = re.match(r"screen_(\d+)_(.+)$", clave)
        candidatos = por_nombre.get(coincidencia.group(2) if coincidencia else clave) or []
        if coincidencia:
            candidatos = [c for c in candidatos
                          if indice_de_pantalla.get(c["pantalla"]) == int(coincidencia.group(1))] \
                or candidatos
        return candidatos[0] if len(candidatos) == 1 else None

    claves = list(dict.fromkeys([*expresiones, *(c["nombre"] for c in componentes.values())]))
    salida = {}
    usados = set()
    for clave in claves:
        comp = resolver(clave) or por_convencion(clave)
        if not comp:
            continue
        if clave not in expresiones and (comp["pantalla"], comp["nombre"]) in usados:
            continue
        usados.add((comp["pantalla"], comp["nombre"]))
        salida[clave] = comp
    # El texto: la etiqueta, o el texto que la precede en la pantalla; con
    # la pantalla delante si dos campos dirían lo mismo.
    textos = {}
    for clave, comp in salida.items():
        textos[clave] = comp["etiqueta"] or comp["texto_previo"]
    repetidos = {t for t in textos.values() if t and list(textos.values()).count(t) > 1}
    diccionario_final = {}
    for clave, comp in salida.items():
        texto = textos[clave]
        if texto in repetidos:
            texto = f"{comp['pantalla_titulo']}: {texto}"
        diccionario_final[clave] = {
            "texto": texto, "tipo": comp["tipo"], "opciones": comp["opciones"],
            "pantalla": comp["pantalla_titulo"],
        }
    return diccionario_final


def definicion_del_flow(flow_id, entorno=None, pedir=None):
    """El `flow.json` de un Flow, bajado de Meta (`/{flow_id}/assets`)."""
    datos = whatsapp.config(entorno)
    if not datos["token"]:
        raise whatsapp.SinConfigurar("No hay credenciales de WhatsApp configuradas.")
    pedir = pedir or whatsapp._pedir
    activos = pedir(f"{whatsapp.API}/{flow_id}/assets", token=datos["token"])
    for activo in activos.get("data") or []:
        if (activo.get("asset_type") or "").upper() == "FLOW_JSON" and activo.get("download_url"):
            return pedir(activo["download_url"], token=datos["token"])
    raise Conflicto(f"Meta no devolvió la definición del Flow {flow_id}.",
                    {"motivo": "flow_sin_definicion"})


def resumen_de_encuesta(conn, encuesta_id):
    """Cuántas respuestas de Flow tiene la encuesta y cuántas son **nuevas
    desde la última carga**: personas que todavía no están en ninguna carga
    de esta encuesta (las otras quedan como «repetida» al ingestar, D94)."""
    fila = db.una(conn, """
        with ultimas as (
          select distinct on (w.id_persona) w.id_persona, w.inconsistencia, w.recibido_en
            from whatsapp_entrante w
           where w.encuesta_id = %s and w.tipo = 'flow' and w.id_persona is not null
           order by w.id_persona, w.recibido_en desc
        )
        select count(*) as personas,
               count(*) filter (where not exists (
                 select 1 from persona_carga pc join carga c on c.id = pc.carga_id
                  where pc.id_persona = u.id_persona and c.encuesta_id = %s
                    and not c.provisoria)) as nuevas,
               count(*) filter (where u.inconsistencia is not null) as inconsistencias,
               max(u.recibido_en) as ultima
          from ultimas u
    """, (encuesta_id, encuesta_id))
    total = db.una(conn, """
        select count(*) as n from whatsapp_entrante
         where encuesta_id = %s and tipo = 'flow'""", (encuesta_id,))
    sin_persona = db.una(conn, """
        select count(*) as n from whatsapp_entrante
         where encuesta_id = %s and tipo = 'flow' and id_persona is null""", (encuesta_id,))
    personas = fila["personas"] if fila else 0
    return {"respuestas": total["n"], "personas": personas,
            "nuevas": fila["nuevas"] if fila else 0,
            "repetidas": personas - (fila["nuevas"] if fila else 0),
            "inconsistencias": fila["inconsistencias"] if fila else 0,
            "sin_persona": sin_persona["n"], "ultima": fila["ultima"] if fila else None}


def _nombre_spss(crudo, usados):
    nombre = re.sub(r"[^A-Za-z0-9_]", "_", str(crudo))[:60] or "v"
    if not nombre[0].isalpha():
        nombre = "v" + nombre
    base, n = nombre, 2
    while nombre.lower() in usados:
        nombre = f"{base[:56]}_{n}"
        n += 1
    usados.add(nombre.lower())
    return nombre


def armar_conjunto(filas, dicc):
    """De las respuestas (`[{id_persona, respuesta}]`) y el diccionario a las
    columnas de un SAV: `(columnas, etiquetas, valores_etiquetados, sin_etiqueta)`.

    - cerrada → códigos 1..n con la etiqueta de cada opción;
    - selección múltiple → una variable por opción (1 = marcada), como las
      baterías del SAV de Alchemer (`Pregunta:Opción`);
    - abierta → texto;
    - sin diccionario → texto, **sin etiqueta**, para corregirlo en la revisión.
    """
    usados = {"id_persona"}
    columnas = {"id_persona": [str(f["id_persona"]) for f in filas]}
    etiquetas = {"id_persona": "id_persona"}
    valores = {}
    sin_etiqueta = []
    campos = orden_de_campos([c for f in filas for c in (f["respuesta"] or {})])
    for campo in campos:
        crudos = [(f["respuesta"] or {}).get(campo) for f in filas]
        entrada = dicc.get(campo)
        texto = (entrada or {}).get("texto")
        tipo = (entrada or {}).get("tipo")
        opciones = dict((entrada or {}).get("opciones") or {})
        if tipo == "multiple":
            marcadas = [set(map(str, v)) if isinstance(v, list)
                        else ({str(v)} if v not in (None, "") else set()) for v in crudos]
            ids = list(opciones) + sorted({i for m in marcadas for i in m} - set(opciones))
            for opcion in ids:
                nombre = _nombre_spss(f"{campo}_{opcion}", usados)
                titulo = opciones.get(opcion)
                columnas[nombre] = [1.0 if opcion in m else None for m in marcadas]
                valores[nombre] = {1.0: titulo or opcion}
                etiquetas[nombre] = f"{texto}:{titulo or opcion}" if texto else ""
                if not texto or not titulo:
                    sin_etiqueta.append({"codigo": nombre, "campo": campo, "opcion": opcion,
                                         "motivo": "pregunta" if not texto else "opcion"})
            continue
        nombre = _nombre_spss(campo, usados)
        etiquetas[nombre] = texto or ""
        if not texto:
            sin_etiqueta.append({"codigo": nombre, "campo": campo, "motivo": "pregunta"})
        if tipo == "optin":
            columnas[nombre] = [None if v in (None, "") else (1.0 if v in (True, "true", "True", 1) else 2.0)
                                for v in crudos]
            valores[nombre] = {1.0: "Sí", 2.0: "No"}
            continue
        if tipo == "cerrada":
            ids = list(opciones) + sorted({str(v) for v in crudos if v not in (None, "")}
                                          - set(opciones))
            codigos = {i: float(n) for n, i in enumerate(ids, start=1)}
            columnas[nombre] = [codigos[str(v)] if v not in (None, "") else None for v in crudos]
            valores[nombre] = {codigos[i]: (opciones.get(i) or i) for i in ids}
            for i in ids:
                if not opciones.get(i):
                    sin_etiqueta.append({"codigo": nombre, "campo": campo, "opcion": i,
                                         "motivo": "opcion"})
            continue
        columnas[nombre] = [_valor_de_celda(v) for v in crudos]
    return columnas, etiquetas, valores, sin_etiqueta


def escribir_sav(columnas, etiquetas, valores):
    import pandas as pd
    import pyreadstat

    marco = pd.DataFrame(columnas)
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = os.path.join(carpeta, "whatsapp.sav")
        pyreadstat.write_sav(marco, ruta, column_labels=[etiquetas.get(c, "") for c in marco.columns],
                             variable_value_labels=valores)
        with open(ruta, "rb") as archivo:
            return archivo.read()


def conjunto_de_encuesta(conn, encuesta_id, entorno=None, pedir=None):
    """R-WA.6 — el SAV de las respuestas de WhatsApp de una encuesta, para
    abrirlo en el modal de ingesta como cualquier otro: análisis, revisión,
    calidad, temáticas, consentimiento, carga incremental e historial.

    Una fila por persona (su última respuesta); la identidad es la del
    envío. El diccionario sale de la definición de los Flows de los mensajes
    de la encuesta; lo que no se puede traducir va **sin etiqueta**.
    """
    filas = db.todas(conn, """
        select distinct on (w.id_persona) w.id_persona, w.respuesta, w.encuesta_mensaje_id
          from whatsapp_entrante w
         where w.encuesta_id = %s and w.tipo = 'flow' and w.id_persona is not null
         order by w.id_persona, w.recibido_en desc
    """, (encuesta_id,))
    if not filas:
        raise NoEncontrado("Esta encuesta no tiene respuestas de WhatsApp.")
    flows = [f["flow_id"] for f in db.todas(conn, """
        select distinct m.flow_id from encuesta_mensaje m
         where m.encuesta_id = %s and m.flow_id is not null
           and m.id in (select distinct encuesta_mensaje_id from whatsapp_entrante
                         where encuesta_id = %s and tipo = 'flow')
         order by m.flow_id
    """, (encuesta_id, encuesta_id))]
    avisos, dicc = [], {}
    for flow_id in flows:
        try:
            for campo, entrada in diccionario(definicion_del_flow(flow_id, entorno, pedir)).items():
                dicc.setdefault(campo, entrada)
        except (whatsapp.SinConfigurar, Conflicto) as error:
            avisos.append(f"No se pudo bajar la definición del Flow {flow_id}: "
                          f"{getattr(error, 'mensaje', None) or error}")
    if not flows:
        avisos.append("Los mensajes de esta encuesta no dicen qué Flow llevan: las preguntas "
                      "van sin etiqueta.")
    columnas, etiquetas, valores, sin_etiqueta = armar_conjunto(filas, dicc)
    if sin_etiqueta:
        avisos.append(f"{len({s['codigo'] for s in sin_etiqueta})} variable(s) {SIN_ETIQUETA}: "
                      "el Flow no dice su texto o el de alguna opción. Corregilas en la "
                      "revisión antes de ingestar.")
    contenido = escribir_sav(columnas, etiquetas, valores)
    return {
        **resumen_de_encuesta(conn, encuesta_id),
        "archivo_base64": base64.b64encode(contenido).decode(),
        "nombre": f"whatsapp_encuesta_{encuesta_id}.sav",
        "filas": len(filas), "flows": flows,
        "sin_etiqueta": sin_etiqueta, "avisos": avisos,
    }
