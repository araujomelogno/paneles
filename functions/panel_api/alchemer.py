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
# Cuántas páginas de encuestas se recorren para buscar por título. Con 100
# por página son 1000 encuestas: lo que tiene una cuenta activa, de sobra.
PAGINAS_MAXIMAS = 10
POR_PAGINA = 100

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

def _pedir(url):
    """GET a la API. Un error de Alchemer es un `Conflicto` con su mensaje;
    nunca se devuelve un valor por defecto en silencio."""
    try:
        with urllib.request.urlopen(url, timeout=20) as respuesta:
            datos = json.loads(respuesta.read().decode())
    except urllib.error.HTTPError as error:
        raise Conflicto(f"Alchemer respondió {error.code}.",
                        {"status": error.code}) from None
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise Conflicto(f"No se pudo hablar con Alchemer: {error}.") from None
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


def buscar_encuestas(q=None, entorno=None, pedir=None):
    """R-ALC.2 — las encuestas de la cuenta cuyo título contiene `q` (sin
    tildes ni mayúsculas), la modificada más recientemente primero."""
    try:
        datos = _exigir_configuracion(entorno)
    except SinConfigurar as error:
        return {"configurado": False, "items": [], "avisos": [str(error)]}
    pedir = pedir or _pedir
    clave = _clave(q)
    items = []
    for pagina in range(1, PAGINAS_MAXIMAS + 1):
        respuesta = pedir(_url(datos, "survey", page=pagina, resultsperpage=POR_PAGINA))
        for s in respuesta.get("data") or []:
            titulo = _texto(s.get("title"))
            if clave and clave not in _clave(titulo):
                continue
            items.append({"id": str(s.get("id")), "titulo": titulo,
                          "estado": s.get("status"),
                          "modificada": s.get("modified_on") or s.get("created_on")})
        if pagina >= int(respuesta.get("total_pages") or 1):
            break
    items.sort(key=lambda i: str(i["modificada"] or ""), reverse=True)
    return {"configurado": True, "items": items, "avisos": []}


def con_parametro(url, parametro):
    """El enlace de la encuesta más el parámetro del panelista: `…?pid=` (o
    `…&pid=` si el enlace ya tiene consulta). Es lo que valida
    `encuestas.url_alchemer_valida`: termina en `=`."""
    url = (url or "").strip()
    if not url:
        return None
    return f"{url}{'&' if '?' in url else '?'}{parametro}="


def enlaces(alchemer_id, entorno=None, pedir=None):
    """R-ALC.2 — los enlaces (campañas de tipo enlace) de una encuesta, con la
    URL ya armada con el parámetro. Una encuesta con más de uno obliga a
    elegir."""
    datos = _exigir_configuracion(entorno)
    respuesta = (pedir or _pedir)(_url(datos, f"survey/{alchemer_id}/surveycampaign",
                                       resultsperpage=POR_PAGINA))
    salida = []
    for c in respuesta.get("data") or []:
        if (c.get("type") or "link").lower() != "link" or not c.get("uri"):
            continue
        salida.append({"id": str(c.get("id")), "nombre": c.get("name") or "Enlace",
                       "estado": c.get("status"), "url": c.get("uri"),
                       "url_con_parametro": con_parametro(c.get("uri"), datos["parametro"])})
    return salida


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


def cuestionario(alchemer_id, entorno=None, pedir=None):
    """R-ALC.4 — el título, las preguntas normalizadas y la verificación del
    parámetro de una encuesta de Alchemer."""
    datos = _exigir_configuracion(entorno)
    pedir = pedir or _pedir
    encuesta = pedir(_url(datos, f"survey/{alchemer_id}")).get("data") or {}
    crudas = (pedir(_url(datos, f"survey/{alchemer_id}/surveyquestion",
                         resultsperpage=500)).get("data") or [])
    preguntas = []
    for q in crudas:
        for una in [q] + list(q.get("sub_questions") or []):
            normalizada = _normalizar_pregunta(una) if isinstance(una, dict) else None
            if normalizada:
                preguntas.append(normalizada)
    return {"alchemer_id": str(alchemer_id), "titulo": _texto(encuesta.get("title")),
            "preguntas": preguntas, "parametro": datos["parametro"],
            "captura_parametro": captura_parametro(crudas, datos["parametro"])}


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


def vincular(conn, encuesta_id, alchemer_id, entorno=None, pedir=None):
    """R-ALC.2/4 — deja la encuesta vinculada a la de Alchemer y guarda su
    cuestionario y la verificación del parámetro. **La encuesta se guarda
    igual** aunque Alchemer falle o la encuesta no capture el parámetro: se
    avisa. No hace `commit`."""
    alchemer_id = str(alchemer_id or "").strip()
    if not alchemer_id:
        raise DatosInvalidos("Falta el id de la encuesta de Alchemer.", {"campo": "alchemer_id"})
    if not db.una(conn, "select 1 as hay from encuesta where id = %s", (encuesta_id,)):
        raise NoEncontrado(f"No existe la encuesta {encuesta_id}.")
    db.ejecutar(conn, "update encuesta set alchemer_id = %s where id = %s",
                (alchemer_id, encuesta_id))
    try:
        traido = cuestionario(alchemer_id, entorno=entorno, pedir=pedir)
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
