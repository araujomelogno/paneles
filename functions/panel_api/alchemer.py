"""R-ALC (D97) — Alchemer al crear una encuesta.

Hasta ahora la URL de Alchemer se escribía a mano, sin forma de saber si
estaba bien ni si la encuesta de Alchemer **guardaba** el parámetro con el
`id_persona` —si no lo guarda, las respuestas vuelven sin identificar y la
carga no reconoce a nadie—. Y las temáticas de un estudio recién se conocían
con la primera carga, cuando el campo ya había terminado.

Lo que este módulo hace, y lo que no:

* **Lee, no escribe.** Busca encuestas, trae sus enlaces y su cuestionario.
  No crea ni edita nada en Alchemer, y **no ingesta respuestas** por la API:
  el SAV sigue siendo la entrada (a veces se trata la información a mano
  antes de cargarla).
* **El cuestionario es una referencia** (`alchemer_cuestionario`, en la
  bóveda: no tiene datos personales). No va al store semántico: los códigos
  de pregunta de la API pueden no coincidir con los nombres de variable del
  SAV, y escribirlos ahí duplicaría preguntas en la primera carga.
* **Verificar el parámetro es una heurística declarada.** La API no tiene un
  campo «variables de URL capturadas»: Alchemer las guarda con una pregunta
  oculta (*Hidden Value*) o una acción cuyo valor es el código de fusión
  `[url("pid")]`. Se busca ese código en las preguntas y acciones de la
  encuesta. Si la API no responde, el resultado es «no se pudo verificar»
  —nunca «sí»—.

Las credenciales (`ALCHEMER_API_TOKEN`, `ALCHEMER_API_SECRET`) son secretos
de Secret Manager; la región de la cuenta (`ALCHEMER_REGION`: us, eu o ca, o
`ALCHEMER_API_URL` explícita) y el parámetro del panelista
(`ALCHEMER_PARAMETRO`, por defecto `pid`) son variables comunes. Sin
credenciales el módulo se degrada de forma visible: la URL se sigue pudiendo
escribir a mano.
"""

import json
import os
import re
import socket
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import db
from .errores import Conflicto, DatosInvalidos, NoEncontrado

REGIONES = {
    "us": "https://api.alchemer.com/v5",
    "eu": "https://api.alchemer.eu/v5",
    "ca": "https://api.alchemer-ca.com/v5",
}
PARAMETRO_POR_DEFECTO = "pid"
POR_PAGINA = 100

# D99 · R-ALB.3 — cuánto se espera a Alchemer en **una** llamada. Hosting
# corta todo pedido a los 60 s y una página de la lista de encuestas tardó
# 7,8 s medida en producción: 20 s deja margen para una llamada y ninguna
# pantalla hace más de una (la sincronización corre en segundo plano).
TIEMPO_POR_DEFECTO_S = 20
MENSAJE_TIEMPO = "Alchemer tardó demasiado en responder."

AVISO_SIN_CREDENCIALES = (
    "Sin credenciales de Alchemer (`ALCHEMER_API_TOKEN` y `ALCHEMER_API_SECRET` en "
    "Secret Manager) no se pueden buscar encuestas: la URL se escribe a mano.")

# Tipos de pregunta de Alchemer → tipo de pregunta del sistema. Lo que no es
# una pregunta (textos, saltos de página, acciones) no entra al cuestionario.
TIPOS = {
    "radio": "cerrada", "menu": "cerrada", "checkbox": "cerrada",
    "rank": "cerrada", "table": "cerrada", "group": "cerrada",
    "textbox": "abierta", "essay": "abierta", "email": "abierta",
    "number": "numerica", "slider": "escala", "nps": "escala",
    "rating": "escala",
}
NO_SON_PREGUNTAS = {"instructions", "hidden", "action", "decorative", "logic",
                    "javascript", "url_redirect", "webhook", "page"}


class SinConfigurar(RuntimeError):
    """No hay credenciales de Alchemer. No es un error del usuario."""


def config(entorno=None):
    entorno = os.environ if entorno is None else entorno
    region = (entorno.get("ALCHEMER_REGION") or "us").strip().lower()
    base = (entorno.get("ALCHEMER_API_URL") or "").strip().rstrip("/") \
        or REGIONES.get(region, REGIONES["us"])
    return {
        "token": (entorno.get("ALCHEMER_API_TOKEN") or "").strip(),
        "secreto": (entorno.get("ALCHEMER_API_SECRET") or "").strip(),
        "base": base,
        "region": region,
        "parametro": (entorno.get("ALCHEMER_PARAMETRO") or "").strip()
                     or PARAMETRO_POR_DEFECTO,
    }


def configurado(entorno=None):
    datos = config(entorno)
    return bool(datos["token"] and datos["secreto"])


# ── Llamadas a la API ────────────────────────────────────────────────

def _tiempo_s(entorno=None):
    entorno = os.environ if entorno is None else entorno
    try:
        return max(1, int(entorno.get("ALCHEMER_TIEMPO_S") or TIEMPO_POR_DEFECTO_S))
    except ValueError:
        return TIEMPO_POR_DEFECTO_S


def _pedir(url):
    """GET a la API. Un error de Alchemer es un `Conflicto` con su mensaje;
    nunca se devuelve un valor por defecto en silencio. Pasarse del tiempo
    es `motivo = alchemer_tiempo` (R-ALB.3): la pantalla ofrece reintentar o
    pegar la URL, y la sincronización lo reintenta."""
    try:
        with urllib.request.urlopen(url, timeout=_tiempo_s()) as respuesta:
            datos = json.loads(respuesta.read().decode())
    except urllib.error.HTTPError as error:
        raise Conflicto(f"Alchemer respondió {error.code}.",
                        {"status": error.code, "motivo": "alchemer_error"}) from None
    except (TimeoutError, socket.timeout):
        raise Conflicto(MENSAJE_TIEMPO, {"motivo": "alchemer_tiempo"}) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise Conflicto(MENSAJE_TIEMPO, {"motivo": "alchemer_tiempo"}) from None
        raise Conflicto(f"No se pudo hablar con Alchemer: {error.reason}.",
                        {"motivo": "alchemer_red"}) from None
    except ValueError as error:
        raise Conflicto(f"Alchemer devolvió algo que no es JSON: {error}.",
                        {"motivo": "alchemer_error"}) from None
    if isinstance(datos, dict) and datos.get("result_ok") is False:
        raise Conflicto(f"Alchemer: {datos.get('message') or 'pedido rechazado'}.")
    return datos


def _url(datos, camino, **consulta):
    consulta = {**consulta, "api_token": datos["token"],
                "api_token_secret": datos["secreto"]}
    return f"{datos['base']}/{camino}?{urllib.parse.urlencode(consulta)}"


def _exigir_configuracion(entorno):
    datos = config(entorno)
    if not (datos["token"] and datos["secreto"]):
        raise SinConfigurar(AVISO_SIN_CREDENCIALES)
    return datos


def _clave(texto):
    """Sin tildes ni mayúsculas, para buscar por título."""
    plano = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(plano.lower().split())


def _texto(valor):
    """Un título de Alchemer: un dict por idioma (`{"English": …}`) o un
    texto. Se prefiere el español; sin HTML."""
    if isinstance(valor, dict):
        preferido = next((v for k, v in valor.items()
                          if any(s in k.lower() for s in ("span", "espa")) and v), None)
        valor = preferido or next((v for v in valor.values() if v), "")
    limpio = re.sub(r"<[^>]+>", " ", str(valor or "")).replace("&nbsp;", " ")
    # La etiqueta que se cerró antes de un signo no deja un espacio colgando.
    return re.sub(r"\s+([?!.,;:])", r"\1", " ".join(limpio.split()))


# D99 · R-ALB.1 — de dónde se saca el id de una encuesta pegada: la URL
# pública (`…/s3/9105516/…`, también con el dominio propio), la de edición
# (`…/builder/build/id/9105516`, `…/survey/id/9105516`) o una consulta
# (`?id=9105516`). Lo que no coincide con ninguna forma no se adivina.
_FORMAS_DEL_ID = (
    re.compile(r"^\s*(\d{4,})\s*$"),
    re.compile(r"/s\d*/(\d{4,})(?:/|$|\?)"),
    re.compile(r"/(?:id|survey|surveys)/(\d{4,})(?:/|$|\?|#)"),
    re.compile(r"[?&#](?:id|sid|survey_id|surveyid)=(\d{4,})"),
)


def id_de(texto):
    """R-ALB.1 — el id de la encuesta a partir de lo que se pegó: el id solo,
    la URL pública o la de edición. `DatosInvalidos` si no se reconoce."""
    texto = str(texto or "").strip()
    for forma in _FORMAS_DEL_ID:
        coincidencia = forma.search(texto)
        if coincidencia:
            return coincidencia.group(1)
    raise DatosInvalidos(
        "No se reconoce una encuesta de Alchemer en lo que se pegó. Pegá el id "
        "(por ejemplo 9105516), la URL del enlace (…/s3/9105516/…) o la de edición.",
        {"campo": "alchemer"})


def con_parametro(url, parametro):
    """El enlace de la encuesta más el parámetro del panelista: `…?pid=` (o
    `…&pid=` si el enlace ya tiene consulta). Es lo que valida
    `encuestas.url_alchemer_valida`: termina en `=`."""
    url = (url or "").strip()
    if not url:
        return None
    return f"{url}{'&' if '?' in url else '?'}{parametro}="


def _verdadero(valor):
    return str(valor).strip().lower() in ("true", "1", "yes", "si", "sí")


def url_de_enlace(campania):
    """R-ALB.5 — la URL completa de una campaña. Alchemer manda `uri` **sin
    esquema** (`survey.alchemer.com/s3/…`) y el protocolo en `SSL`: sin
    agregarlo, la URL no pasaba la validación de paneles."""
    uri = str(campania.get("uri") or "").strip()
    if not uri:
        return None
    if re.match(r"^https?://", uri, re.IGNORECASE):
        return uri
    esquema = "https" if _verdadero(campania.get("SSL", True)) else "http"
    return f"{esquema}://{uri.lstrip('/')}"


def _es_enlace(campania):
    """`link_type` dice el tipo de enlace; `type` es el tipo del **objeto**
    (`SurveyCampaign` en todas). Se mira `type` solo si falta `link_type`,
    para no depender de una sola forma de la respuesta."""
    tipo = campania.get("link_type")
    if tipo in (None, ""):
        tipo = campania.get("type")
    return str(tipo or "").strip().lower() == "link"


ESTADOS_DE_ENLACE_ACTIVOS = ("active", "activo", "open", "launched")


def enlaces(alchemer_id, entorno=None, pedir=None):
    """R-ALB.5 — los enlaces de una encuesta (una llamada), con la URL
    completa y armada con el parámetro. Todos, con `activo` para filtrar en
    la pantalla: por defecto se muestran los activos."""
    datos = _exigir_configuracion(entorno)
    respuesta = (pedir or _pedir)(_url(datos, f"survey/{alchemer_id}/surveycampaign",
                                       resultsperpage=POR_PAGINA))
    salida = []
    for c in respuesta.get("data") or []:
        url = url_de_enlace(c)
        if not _es_enlace(c) or not url:
            continue
        estado = str(c.get("status") or "").strip()
        salida.append({
            "id": str(c.get("id")), "nombre": " ".join(str(c.get("name") or "Enlace").split()),
            "estado": estado or None,
            "activo": not estado or estado.lower() in ESTADOS_DE_ENLACE_ACTIVOS,
            "url": url, "seguro": url.lower().startswith("https://"),
            "url_con_parametro": con_parametro(url, datos["parametro"])})
    salida.sort(key=lambda e: (not e["activo"], e["nombre"].lower()))
    return salida


def enlace(alchemer_id, enlace_id, entorno=None, pedir=None):
    """Un enlace por su id (la campaña), releído de Alchemer: lo que se
    guarda no sale de lo que mandó la pantalla."""
    for e in enlaces(alchemer_id, entorno=entorno, pedir=pedir):
        if e["id"] == str(enlace_id):
            return e
    raise NoEncontrado(f"La encuesta {alchemer_id} de Alchemer no tiene el enlace {enlace_id}.")


def _normalizar_pregunta(q):
    """Una pregunta de la API → `{id, codigo, texto, tipo, opciones}`, o None
    si no es una pregunta (textos, ocultas, acciones)."""
    subtipo = (q.get("_subtype") or q.get("type") or "").lower()
    base = (q.get("base_type") or "").lower()
    if subtipo in NO_SON_PREGUNTAS or base in ("action", "decorative", "logic"):
        return None
    texto = _texto(q.get("title"))
    codigo = (q.get("shortname") or "").strip() or f"Q{q.get('id')}"
    opciones = {}
    for o in q.get("options") or []:
        etiqueta = _texto(o.get("title")) or str(o.get("value") or "")
        valor = str(o.get("value") if o.get("value") not in (None, "") else o.get("id"))
        opciones[valor] = etiqueta
    return {"id": q.get("id"), "codigo": codigo, "texto": texto,
            "tipo": TIPOS.get(subtipo, "cerrada" if opciones else "abierta"),
            "subtipo": subtipo, "opciones": opciones}


def _patron_del_parametro(parametro):
    """El código de fusión con que Alchemer guarda una variable de URL:
    `[url("pid")]`, con comillas simples o dobles y espacios, o `[url:pid]`."""
    p = re.escape(parametro)
    return re.compile(rf"\[\s*url\s*(\(\s*\\?[\"']?\s*{p}\s*\\?[\"']?\s*\)|:\s*{p})\s*\]",
                      re.IGNORECASE)


def captura_parametro(preguntas_crudas, parametro):
    """R-ALC.3 — ¿alguna pregunta oculta o acción guarda la variable de URL?
    Se busca el código de fusión en todo lo que devolvió la API (título,
    propiedades, valor por defecto), porque Alchemer lo guarda en lugares
    distintos según cómo se armó."""
    patron = _patron_del_parametro(parametro)
    texto = json.dumps(preguntas_crudas or [], ensure_ascii=False)
    return bool(patron.search(texto))


def _preguntas_de_las_paginas(encuesta):
    """Las preguntas que `GET /survey/{id}` trae adentro de sus páginas, o
    None si la respuesta no trae páginas."""
    paginas = encuesta.get("pages")
    if not isinstance(paginas, list):
        return None
    return [q for p in paginas if isinstance(p, dict)
            for q in (p.get("questions") or []) if isinstance(q, dict)]


def cuestionario(alchemer_id, entorno=None, pedir=None):
    """R-ALC.4 — el título, las preguntas normalizadas y la verificación del
    parámetro de una encuesta de Alchemer.

    D99 · R-ALB.3 — **una llamada**: `GET /survey/{id}` trae las páginas con
    sus preguntas. Solo si la respuesta no trae páginas se piden aparte
    (`surveyquestion`), y eso se informa en `llamadas`."""
    datos = _exigir_configuracion(entorno)
    pedir = pedir or _pedir
    encuesta = pedir(_url(datos, f"survey/{alchemer_id}")).get("data") or {}
    crudas = _preguntas_de_las_paginas(encuesta)
    llamadas = 1
    if crudas is None:
        crudas = (pedir(_url(datos, f"survey/{alchemer_id}/surveyquestion",
                             resultsperpage=500)).get("data") or [])
        llamadas = 2
    preguntas = []
    for q in crudas:
        for una in [q] + list(q.get("sub_questions") or []):
            normalizada = _normalizar_pregunta(una) if isinstance(una, dict) else None
            if normalizada:
                preguntas.append(normalizada)
    return {"alchemer_id": str(encuesta.get("id") or alchemer_id),
            "titulo": _texto(encuesta.get("title")),
            "estado": encuesta.get("status"),
            "creada": encuesta.get("created_on"), "modificada": encuesta.get("modified_on"),
            "preguntas": preguntas, "parametro": datos["parametro"],
            "captura_parametro": captura_parametro(crudas, datos["parametro"]),
            "llamadas": llamadas}


def aviso_de_parametro(captura, parametro):
    """El aviso de R-ALC.3, o None si captura."""
    if captura is True:
        return None
    if captura is None:
        return (f"No se pudo verificar si esta encuesta guarda el parámetro `{parametro}`: "
                f"revisalo en Alchemer antes de convocar.")
    return (f"Esta encuesta no guarda el parámetro `{parametro}`: las respuestas no van "
            f"a traer el id_persona.")


# ── Con la base ──────────────────────────────────────────────────────

def _serializar(fila):
    if not fila:
        return None
    return {**fila,
            "verificado_en": fila["verificado_en"].isoformat() if fila.get("verificado_en") else None,
            "actualizado_en": fila["actualizado_en"].isoformat(),
            "aviso": aviso_de_parametro(fila.get("captura_parametro"), fila.get("parametro"))}


def de_encuesta(conn, encuesta_id):
    """El cuestionario guardado de una encuesta, o None."""
    return _serializar(db.una(
        conn, "select * from alchemer_cuestionario where encuesta_id = %s", (encuesta_id,)))


def vincular(conn, encuesta_id, alchemer_id, entorno=None, pedir=None, enlace_id=None):
    """R-ALC.2/4 — deja la encuesta vinculada a la de Alchemer y guarda su
    cuestionario y la verificación del parámetro. **La encuesta se guarda
    igual** aunque Alchemer falle o la encuesta no capture el parámetro: se
    avisa. No hace `commit`.

    D99 — acepta también lo que se pegó (la URL o el id, R-ALB.1), guarda
    qué enlace se eligió (R-ALB.5) y refresca la fila del catálogo con la
    misma respuesta, aunque el catálogo esté desactualizado (R-ALB.2)."""
    alchemer_id = str(alchemer_id or "").strip()
    if not alchemer_id:
        raise DatosInvalidos("Falta el id de la encuesta de Alchemer.", {"campo": "alchemer_id"})
    alchemer_id = id_de(alchemer_id)
    if not db.una(conn, "select 1 as hay from encuesta where id = %s", (encuesta_id,)):
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    db.ejecutar(conn, """update encuesta set alchemer_id = %s,
                                alchemer_enlace_id = coalesce(%s, case
                                  when alchemer_id is distinct from %s then null
                                  else alchemer_enlace_id end)
                          where id = %s""",
                (alchemer_id, str(enlace_id) if enlace_id else None, alchemer_id, encuesta_id))
    try:
        traido = cuestionario(alchemer_id, entorno=entorno, pedir=pedir)
        from . import alchemer_catalogo

        alchemer_catalogo.refrescar(conn, traido)
    except (SinConfigurar, Conflicto) as error:
        mensaje = getattr(error, "mensaje", None) or str(error)
        return {"vinculada": True, "cuestionario": de_encuesta(conn, encuesta_id),
                "aviso": f"La encuesta quedó vinculada, pero no se pudo traer el "
                         f"cuestionario de Alchemer: {mensaje}"}
    db.ejecutar(
        conn,
        """insert into alchemer_cuestionario
                  (encuesta_id, alchemer_id, titulo, preguntas, parametro,
                   captura_parametro, verificado_en, actualizado_en)
           values (%s, %s, %s, %s::jsonb, %s, %s, now(), now())
           on conflict (encuesta_id) do update
              set alchemer_id = excluded.alchemer_id, titulo = excluded.titulo,
                  preguntas = excluded.preguntas, parametro = excluded.parametro,
                  captura_parametro = excluded.captura_parametro,
                  verificado_en = now(), actualizado_en = now()""",
        (encuesta_id, alchemer_id, traido["titulo"], json.dumps(traido["preguntas"]),
         traido["parametro"], traido["captura_parametro"]))
    guardado = de_encuesta(conn, encuesta_id)
    return {"vinculada": True, "cuestionario": guardado, "aviso": guardado["aviso"]}


def verificar_para_campo(conn, encuesta_id, entorno=None, pedir=None):
    """R-ALC.3 — al pasar a «en campo» se vuelve a verificar: la encuesta de
    Alchemer pudo cambiar después de vincularla. Devuelve el aviso, o None
    (también si no está vinculada). No hace `commit`."""
    encuesta = db.una(conn, "select alchemer_id from encuesta where id = %s", (encuesta_id,))
    if not encuesta or not encuesta["alchemer_id"]:
        return None
    datos = config(entorno)
    try:
        traido = cuestionario(encuesta["alchemer_id"], entorno=entorno, pedir=pedir)
        captura = traido["captura_parametro"]
    except (SinConfigurar, Conflicto):
        captura = None
    db.ejecutar(conn, """update alchemer_cuestionario
                            set captura_parametro = %s, verificado_en = now()
                          where encuesta_id = %s""", (captura, encuesta_id))
    return aviso_de_parametro(captura, datos["parametro"])


def preguntas_para_tematicas(conn, encuesta_id):
    """R-ALC.4 — las preguntas del cuestionario de Alchemer, como las
    recibe la propuesta de temáticas (R-TEM): solo código y texto, por
    `tematicas.preguntas_para_proponer`, que es el único lugar que lo decide."""
    from . import tematicas

    guardado = de_encuesta(conn, encuesta_id)
    if not guardado:
        raise Conflicto("Esta encuesta no tiene un cuestionario de Alchemer: vinculala "
                        "primero.", {"encuesta_id": encuesta_id})
    return tematicas.preguntas_para_proponer(
        [{"codigo": p.get("codigo"), "texto": p.get("texto")} for p in guardado["preguntas"]])
