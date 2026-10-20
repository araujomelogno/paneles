"""R-TEM — Las temáticas incorporadas de una carga.

Una carga sabe **de dónde vino** —nombre, fecha, público objetivo, tipo y
modo— pero no **de qué trata**. Las temáticas son los temas que cubren las
preguntas del archivo, y son el primer paso para contestar «¿sobre qué temas
tenemos información de esta gente?» sin abrir el SAV.

Tres cosas que este módulo cuida y que no hay que deshacer:

1. **Una temática es una entidad, no texto suelto** (`tematica`,
   boveda/0027). Si cada carga guardara sus etiquetas, en pocos meses
   «telefonía móvil», «Telefonía Móvil» y «telefonia movil» serían tres
   temas y la pregunta «¿qué cargas cubrieron telefonía?» dejaría de tener
   respuesta. Por eso el nombre se **normaliza** (`normalizar`) y, antes de
   crear una nueva, se busca una existente que difiera solo en tildes
   (`_resolver_existente`). La base hace valer la normalización con un
   `check`.
2. **Claude propone, el analista confirma.** `proponer` nunca escribe: su
   salida aparece en el paso de revisión de la carga, o en el detalle de una
   carga ya confirmada, y lo único que guarda es `guardar`, con la lista que
   el analista dejó. Una propuesta que falla **no bloquea** la carga: vuelve
   vacía, con el motivo, y las temáticas se cargan a mano.
3. **A Claude viajan solo los textos de las preguntas.** Código y texto; ni
   una respuesta, ni las variables marcadas como demográficas o de identidad
   (si fueran, toda carga tendría «datos demográficos» como temática), ni la
   columna que identifica al respondente. `preguntas_para_proponer` es el
   único lugar que decide qué sale, y el tripwire de PII mira el payload
   antes de mandarlo.

Las temáticas describen **la carga**, no a cada persona: que una carga cubra
«consumo de tabaco» no dice que todos sus individuos hayan respondido esas
preguntas. No se copian en `persona` ni se ofrecen como atributo.
"""

import json
import logging
import os
import time
import unicodedata

from . import db, pii
from .errores import DatosInvalidos, NoEncontrado

log = logging.getLogger(__name__)

PROPUESTA_IA = "propuesta_ia"
MANUAL = "manual"
ORIGENES = (PROPUESTA_IA, MANUAL)

# §5 del SPEC — entre 3 y 10 temáticas por cuestionario. El mínimo es una
# instrucción para Claude; el máximo además se recorta acá.
MIN_PROPUESTAS = 3
MAX_PROPUESTAS = 10
# Por carga, contando las que agregue el analista. No es una regla de
# negocio: es el tope de lo que una lista de etiquetas sigue siendo legible.
MAX_POR_CARGA = 30
LARGO_MINIMO, LARGO_MAXIMO = 2, 80

# Estados de una propuesta. `ok` es el único con temáticas; el resto dice
# por qué vino vacía, y la pantalla lo muestra junto al editor.
OK = "ok"
SIN_PREGUNTAS = "sin_preguntas"
NO_DISPONIBLE = "no_disponible"
FALLO = "fallo"

AVISO_FALLO = (
    "No se pudieron proponer temáticas automáticamente ({motivo}). La carga "
    "sigue igual: agregalas a mano, o pedí la propuesta más tarde desde la "
    "sección Cargas.")
AVISO_SIN_PREGUNTAS = (
    "No hay preguntas que analizar: las variables de esta carga están "
    "marcadas como demográficas o de identidad, o no tienen texto. Las "
    "temáticas se pueden agregar a mano.")


# ════════════════════════════════════════════════════════════════════
#  El nombre: normalizado, y reutilizando lo que ya existe
# ════════════════════════════════════════════════════════════════════

# Lo que sobra en los bordes de una etiqueta tipeada o devuelta por el
# modelo: comillas, puntos, comas. Adentro se respeta todo.
_BORDES = " \t\n\r.,;:·-–—\"'«»“”‘’"


def normalizar(nombre):
    """`"  Telefonía   Móvil. "` → `"telefonía móvil"`. None si queda vacío.

    Es la misma regla que el `check tematica_nombre_normalizado` de la 0027
    (minúsculas, un espacio entre palabras, ninguno en los bordes), más el
    recorte de puntuación en los bordes. Las tildes se conservan: «año» y
    «ano» no son lo mismo, y el nombre es lo que se muestra.
    """
    if nombre is None:
        return None
    texto = " ".join(str(nombre).split()).strip(_BORDES)
    texto = " ".join(texto.split()).lower()
    return texto or None


def _validar(nombre_crudo):
    nombre = normalizar(nombre_crudo)
    if not nombre or len(nombre) < LARGO_MINIMO:
        raise DatosInvalidos(
            f"La temática {nombre_crudo!r} está vacía o es demasiado corta.",
            {"campo": "tematicas"})
    if len(nombre) > LARGO_MAXIMO:
        raise DatosInvalidos(
            f"La temática «{nombre[:40]}…» es demasiado larga: una temática es "
            f"una frase corta, de dos a cuatro palabras (máximo "
            f"{LARGO_MAXIMO} caracteres).", {"campo": "tematicas"})
    return nombre


def _sin_tildes(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn")


def clave_de_busqueda(texto):
    """Para comparar al buscar: normalizado y sin tildes. `""` si no hay."""
    return _sin_tildes(normalizar(texto) or "")


def _vocabulario(conn):
    """`{nombre: id}` de todas las temáticas, y el índice sin tildes."""
    filas = db.todas(conn, "select id, nombre from tematica")
    exactos = {f["nombre"]: f["id"] for f in filas}
    sin_tildes = {}
    for f in sorted(filas, key=lambda f: f["id"]):
        sin_tildes.setdefault(_sin_tildes(f["nombre"]), f["nombre"])
    return exactos, sin_tildes


def _resolver_existente(nombre, vocabulario):
    """El nombre existente que corresponde a `nombre`, o None si es nueva.

    Primero la coincidencia exacta (ya normalizada), después la que difiere
    solo en tildes: «telefonia movil» reutiliza «telefonía móvil» en vez de
    crear la variante. Es R-TEM.3 aplicado también a lo que se tipea.
    """
    exactos, sin_tildes = vocabulario
    if nombre in exactos:
        return nombre
    return sin_tildes.get(_sin_tildes(nombre))


def _asegurar(conn, nombre, vocabulario):
    """El id de la temática, creándola si no existe. `(id, nombre, nueva)`."""
    existente = _resolver_existente(nombre, vocabulario)
    if existente is not None:
        return vocabulario[0][existente], existente, False
    fila = db.una(
        conn,
        """
        insert into tematica (nombre) values (%s)
        on conflict (nombre) do update set nombre = excluded.nombre
        returning id, nombre
        """,
        (nombre,))
    vocabulario[0][fila["nombre"]] = fila["id"]
    vocabulario[1].setdefault(_sin_tildes(fila["nombre"]), fila["nombre"])
    return fila["id"], fila["nombre"], True


# ════════════════════════════════════════════════════════════════════
#  Leer
# ════════════════════════════════════════════════════════════════════

def listar(conn):
    """El vocabulario completo, con cuántas cargas usa cada temática.

    Es lo que alimenta el autocompletado y el filtro del listado de cargas.
    """
    return [
        {"id": f["id"], "nombre": f["nombre"], "cargas": f["cargas"]}
        for f in db.todas(
            conn,
            """
            select t.id, t.nombre, count(ct.carga_id)::int as cargas
              from tematica t
              left join carga_tematica ct on ct.tematica_id = t.id
             group by t.id
             order by t.nombre
            """)
    ]


def _fila(f):
    return {"id": f["tematica_id"], "nombre": f["nombre"],
            "origen": f["origen"], "creado_en": f["creado_en"].isoformat()}


def de_cargas(conn, ids_carga):
    """`{carga_id: [temática, …]}` para varias cargas, en una consulta."""
    ids = [int(i) for i in dict.fromkeys(ids_carga or []) if i is not None]
    if not ids:
        return {}
    salida = {i: [] for i in ids}
    for f in db.todas(
        conn,
        """
        select ct.carga_id, ct.tematica_id, t.nombre, ct.origen, ct.creado_en
          from carga_tematica ct
          join tematica t on t.id = ct.tematica_id
         where ct.carga_id = any(%s)
         order by ct.carga_id, t.nombre
        """,
        (ids,),
    ):
        salida[f["carga_id"]].append(_fila(f))
    return salida


def de_carga(conn, carga_id):
    return de_cargas(conn, [carga_id]).get(int(carga_id), [])


# ════════════════════════════════════════════════════════════════════
#  Guardar: la lista que dejó el analista
# ════════════════════════════════════════════════════════════════════

def _item(crudo):
    """Una entrada de la lista: texto suelto o `{nombre, origen}`."""
    if isinstance(crudo, dict):
        nombre = crudo.get("nombre") or crudo.get("tematica")
        origen = (crudo.get("origen") or MANUAL).strip().lower()
    else:
        nombre, origen = crudo, MANUAL
    if origen not in ORIGENES:
        raise DatosInvalidos(
            f"Origen de temática desconocido: {origen!r}. Puede ser "
            f"{', '.join(ORIGENES)}.", {"campo": "tematicas"})
    return _validar(nombre), origen


def guardar(conn, carga_id, items):
    """Deja a la carga con **esas** temáticas: agrega las nuevas y quita las
    que no vinieron. Devuelve la lista como quedó.

    Es el único camino que escribe `carga_tematica`, antes y después de
    confirmar la carga (R-TEM.4). El origen se fija la primera vez: volver a
    guardar una propuesta ya aceptada no la convierte en manual.
    """
    if items is None or not isinstance(items, list):
        raise DatosInvalidos(
            "Mandá la lista de temáticas (`tematicas`), aunque sea vacía.",
            {"campo": "tematicas"})
    if not db.una(conn, "select 1 as hay from carga where id = %s", (carga_id,)):
        raise NoEncontrado(f"No existe la carga {carga_id}.")

    elegidas = {}
    for crudo in items:
        nombre, origen = _item(crudo)
        elegidas.setdefault(nombre, origen)
    if len(elegidas) > MAX_POR_CARGA:
        raise DatosInvalidos(
            f"Son {len(elegidas)} temáticas: el máximo por carga es "
            f"{MAX_POR_CARGA}. Una temática es un tema de investigación, no "
            f"una pregunta.", {"campo": "tematicas"})

    vocabulario = _vocabulario(conn)
    vinculos = {}
    for nombre, origen in elegidas.items():
        tematica_id, _, _ = _asegurar(conn, nombre, vocabulario)
        # Dos variantes de la misma temática en la lista quedan en una.
        vinculos.setdefault(tematica_id, origen)

    db.ejecutar(
        conn,
        "delete from carga_tematica where carga_id = %s "
        "and not (tematica_id = any(%s))",
        (carga_id, list(vinculos)))
    for tematica_id, origen in vinculos.items():
        db.ejecutar(
            conn,
            """
            insert into carga_tematica (carga_id, tematica_id, origen)
            values (%s, %s, %s)
            on conflict (carga_id, tematica_id) do nothing
            """,
            (carga_id, tematica_id, origen))
    return de_carga(conn, carga_id)


# ════════════════════════════════════════════════════════════════════
#  Proponer con Claude (R-TEM.2, R-TEM.3)
# ════════════════════════════════════════════════════════════════════

def preguntas_para_proponer(preguntas, demograficas=None, columna_id=None):
    """Las preguntas que pueden salir hacia Claude: `[{codigo, texto}]`.

    Es el único lugar que lo decide. Afuera quedan:

    - las variables marcadas como **demográficas o de identidad** —si
      viajaran, toda carga tendría «datos demográficos» como temática—;
    - la **columna que identifica** al respondente;
    - las **excluidas** por la calidad del dato y las que no tienen texto;
    - cualquier variable cuyo código sea un campo de PII.

    Y de cada una, **solo** código y texto: ni opciones, ni una respuesta.
    """
    marcadas = set(demograficas or {})
    excluir = marcadas | {columna_id} if columna_id else marcadas
    salida, vistos = [], set()
    for p in preguntas or []:
        codigo = str(p.get("codigo") or "").strip()
        texto = " ".join(str(p.get("texto") or "").split())
        if (not codigo or not texto or codigo in excluir or codigo in vistos
                or p.get("excluida") or p.get("excluir")
                or codigo.lower() in pii.CAMPOS_PII):
            continue
        vistos.add(codigo)
        salida.append({"codigo": codigo, "texto": texto})
    return salida


class FalloPropuesta(Exception):
    """La propuesta no se pudo obtener. El mensaje es el motivo, sin datos."""


INSTRUCCIONES = """\
Vas a recibir las preguntas de un cuestionario de investigación de mercado, \
en español rioplatense. Tu tarea es identificar los temas que el \
cuestionario cubre: no clasificar cada pregunta, sino decir de qué trata \
el estudio.

Reglas:
- Entre 3 y 10 temáticas por cuestionario (menos si el cuestionario es muy \
corto).
- Cada temática es una frase nominal corta en español, de dos a cuatro \
palabras, en minúsculas: «telefonía móvil», «consumo de tabaco», «hábitos \
de compra de ropa».
- Preferí el nivel de tema de investigación y no el de pregunta: \
«servicios de streaming» y no «Netflix».
- No propongas temáticas demográficas (edad, sexo, nivel socioeconómico, \
lugar de residencia) ni de datos de contacto o identificación.
- Te paso la lista de temáticas que ya existen en el sistema. Si alguna \
corresponde a lo que cubren las preguntas, reutilizala textualmente, \
escrita exactamente igual, con es_nueva = false. Proponé una nueva \
(es_nueva = true) solo si ninguna existente corresponde. No inventes \
variantes de una existente («telefonía celular» si ya está «telefonía \
móvil»).
- En preguntas_relacionadas poné los códigos de las preguntas de las que \
sale cada temática, tal como aparecen en la lista.

Registrá tu propuesta con la herramienta proponer_tematicas."""

HERRAMIENTA = {
    "name": "proponer_tematicas",
    "description": "Registra las temáticas que cubre el cuestionario.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tematicas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tematica": {
                            "type": "string",
                            "description": "Frase nominal corta, de dos a "
                                           "cuatro palabras, en minúsculas.",
                        },
                        "es_nueva": {
                            "type": "boolean",
                            "description": "false si es una de las temáticas "
                                           "existentes, escrita igual.",
                        },
                        "preguntas_relacionadas": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Códigos de las preguntas de las "
                                           "que sale la temática.",
                        },
                    },
                    "required": ["tematica", "es_nueva", "preguntas_relacionadas"],
                },
            },
        },
        "required": ["tematicas"],
    },
}


def mensaje(existentes, preguntas):
    """El texto que va a Claude: las existentes y las preguntas, nada más."""
    lineas = ["Temáticas existentes en el sistema "
              "(reutilizalas textualmente cuando correspondan):"]
    lineas += [f"- {n}" for n in existentes] or ["(todavía no hay ninguna)"]
    lineas += ["", "Preguntas del cuestionario (código: texto):"]
    lineas += [f"{p['codigo']}: {p['texto']}" for p in preguntas]
    return "\n".join(lineas)


class Claude:
    """La propuesta con la API de Claude, con la herramienta forzada.

    Comparte con la verificación la clave, el modelo y la API, y no el
    resto: acá es **una** llamada por carga, sin lotes ni reintentos. Si
    falla, la carga sigue y las temáticas se cargan a mano; un reintento
    solo alargaría la espera de quien está mirando la revisión.
    """

    nombre = "claude"
    disponible = True

    def __init__(self, api_key, modelo, tiempo_s, max_tokens=2048, transporte=None):
        if not api_key:
            raise FalloPropuesta("falta CLAUDE_API_KEY")
        self.api_key = api_key
        self.modelo = modelo
        self.tiempo_s = tiempo_s
        self.max_tokens = max_tokens
        # Para las pruebas: `(cuerpo_json, timeout) -> respuesta` con
        # `status_code` y `text`. En producción es `requests.post`.
        self._transporte = transporte

    def cuerpo(self, existentes, preguntas):
        return {
            "model": self.modelo,
            "max_tokens": self.max_tokens,
            "system": INSTRUCCIONES,
            "messages": [{"role": "user", "content": mensaje(existentes, preguntas)}],
            "tools": [HERRAMIENTA],
            "tool_choice": {"type": "tool", "name": HERRAMIENTA["name"]},
        }

    def _enviar(self, cuerpo):
        if self._transporte is not None:
            return self._transporte(cuerpo, self.tiempo_s)
        import requests  # import diferido: solo hace falta con el proveedor real

        from .verificacion import CLAUDE_URL, VERSION_API

        return requests.post(
            CLAUDE_URL,
            headers={"x-api-key": self.api_key, "anthropic-version": VERSION_API,
                     "content-type": "application/json"},
            data=cuerpo.encode("utf-8"), timeout=self.tiempo_s)

    def proponer(self, existentes, preguntas):
        """`[{tematica, es_nueva, preguntas_relacionadas}]` tal como vino.

        Levanta `FalloPropuesta` con el motivo —sin contenido— ante
        cualquier cosa que no sea una herramienta completa: error HTTP, red,
        timeout, respuesta truncada (`stop_reason` se mira **antes** de leer
        la herramienta, como en la verificación) o ilegible.
        """
        # Tripwire, como en la verificación: lo que sale no lleva claves de
        # PII. Hoy son código y texto de preguntas; si alguien agrega un
        # campo al payload, salta acá y no en producción.
        pii.validar_sin_pii({"preguntas": preguntas}, contexto="tematicas")
        cuerpo = json.dumps(self.cuerpo(existentes, preguntas), ensure_ascii=False)
        arranque = time.monotonic()
        try:
            respuesta = self._enviar(cuerpo)
            estado, texto = respuesta.status_code, respuesta.text
        except Exception as error:  # noqa: BLE001 — red, timeout, DNS
            raise FalloPropuesta(
                f"no se pudo hablar con la API ({type(error).__name__})") from None
        ms = int((time.monotonic() - arranque) * 1000)
        if estado != 200:
            raise FalloPropuesta(f"la API respondió HTTP {estado}")
        try:
            datos = json.loads(texto)
        except (TypeError, ValueError):
            raise FalloPropuesta("la respuesta no es JSON") from None
        uso = (datos or {}).get("usage") or {}
        if datos.get("stop_reason") == "max_tokens":
            raise FalloPropuesta("la respuesta se cortó por el límite de salida")
        bloque = next(
            (b for b in datos.get("content") or []
             if isinstance(b, dict) and b.get("type") == "tool_use"
             and b.get("name") == HERRAMIENTA["name"]), None)
        entrada = (bloque or {}).get("input")
        crudas = entrada.get("tematicas") if isinstance(entrada, dict) else None
        if not isinstance(crudas, list):
            raise FalloPropuesta("la respuesta no trae la herramienta de temáticas")
        # Números, nunca contenido: como `[verificacion]`.
        log.info("[tematicas] preguntas=%d existentes=%d propuestas=%d ms=%d "
                 "entrada=%s salida=%s", len(preguntas), len(existentes),
                 len(crudas), ms, uso.get("input_tokens"), uso.get("output_tokens"))
        return crudas


class NoDisponible:
    """Sin proponente: la propuesta vuelve vacía con el motivo."""

    nombre = "ninguno"
    disponible = False

    def __init__(self, motivo):
        self.motivo = motivo

    def proponer(self, existentes, preguntas):
        raise FalloPropuesta(self.motivo)


def _segundos(valor, por_defecto):
    try:
        return max(5, int(str(valor).strip()))
    except (TypeError, ValueError):
        return por_defecto


def crear(cfg=None, entorno=None):
    """El proponente. Nunca levanta excepción: sin clave, `NoDisponible`.

    Modelo y tiempo de espera son los de la verificación (`CLAUDE_MODELO`,
    `VERIFICACION_PRESUPUESTO_S`), y cada uno se puede separar con su propia
    variable (`TEMATICAS_MODELO`, `TEMATICAS_TIEMPO_S`) sin redesplegar.
    """
    from . import verificacion

    entorno = os.environ if entorno is None else entorno
    proveedor = (entorno.get("TEMATICAS_PROVEEDOR") or "claude").strip().lower()
    if proveedor in ("ninguno", "off", "no"):
        return NoDisponible("la propuesta de temáticas está desactivada por configuración")
    if proveedor != "claude":
        return NoDisponible(f"proveedor de temáticas desconocido: {proveedor!r}")
    modelo = (entorno.get("TEMATICAS_MODELO")
              or getattr(cfg, "modelo_claude", None)
              or entorno.get("CLAUDE_MODELO")
              or verificacion.MODELO_POR_DEFECTO)
    tiempo = _segundos(
        entorno.get("TEMATICAS_TIEMPO_S")
        or entorno.get("VERIFICACION_PRESUPUESTO_S")
        or verificacion.PRESUPUESTO_S_POR_DEFECTO,
        verificacion.PRESUPUESTO_S_POR_DEFECTO)
    try:
        return Claude(
            api_key=(getattr(cfg, "api_key_claude", None)
                     or entorno.get("CLAUDE_API_KEY", "")),
            modelo=modelo, tiempo_s=tiempo)
    except FalloPropuesta as error:
        return NoDisponible(str(error))


def _limpiar(crudas, preguntas, vocabulario):
    """La salida del modelo → la propuesta que ve el analista.

    - El nombre se normaliza y se resuelve contra el vocabulario: si difiere
      de una existente solo en mayúsculas, espacios o tildes, **es** esa.
    - `es_nueva` lo decide la base, no el modelo: lo que dijo Claude se
      descarta. Un «false» sobre un nombre que no existe crearía una
      temática sin que la revisión la marcara como nueva.
    - Las preguntas relacionadas se filtran a las que se mandaron, y vuelven
      con su texto para mostrar de dónde salió cada temática.
    - Sin repetidas, y como mucho `MAX_PROPUESTAS`.
    """
    por_codigo = {p["codigo"]: p for p in preguntas}
    salida, vistas = [], set()
    for cruda in crudas:
        if not isinstance(cruda, dict):
            continue
        nombre = normalizar(cruda.get("tematica") or cruda.get("nombre"))
        if not nombre or not (LARGO_MINIMO <= len(nombre) <= LARGO_MAXIMO):
            continue
        existente = _resolver_existente(nombre, vocabulario)
        nombre = existente or nombre
        if nombre in vistas:
            continue
        vistas.add(nombre)
        relacionadas = [
            por_codigo[c] for c in dict.fromkeys(
                str(c).strip() for c in (cruda.get("preguntas_relacionadas") or []))
            if c in por_codigo]
        salida.append({"nombre": nombre, "es_nueva": existente is None,
                       "preguntas_relacionadas": relacionadas})
        if len(salida) >= MAX_PROPUESTAS:
            break
    return salida


def proponer(conn, proponente, preguntas):
    """La propuesta de temáticas para estas preguntas. **No escribe nada.**

    `preguntas` ya pasaron por `preguntas_para_proponer`. Devuelve siempre
    `{estado, aviso, propuesta, preguntas_enviadas}`: si Claude falla o
    tarda, `propuesta` vacía y el aviso dice por qué; la carga no se frena
    por esto (R-TEM.2).
    """
    base = {"preguntas_enviadas": len(preguntas or []),
            "proveedor": getattr(proponente, "nombre", None)}
    if not preguntas:
        return {**base, "estado": SIN_PREGUNTAS, "aviso": AVISO_SIN_PREGUNTAS,
                "propuesta": []}
    vocabulario = _vocabulario(conn)
    existentes = sorted(vocabulario[0])
    try:
        crudas = proponente.proponer(existentes, preguntas)
    except FalloPropuesta as error:
        log.warning("[tematicas] sin propuesta: %s", error)
        estado = NO_DISPONIBLE if not getattr(proponente, "disponible", True) else FALLO
        return {**base, "estado": estado,
                "aviso": AVISO_FALLO.format(motivo=str(error)), "propuesta": []}
    propuesta = _limpiar(crudas, preguntas, vocabulario)
    if not propuesta:
        return {**base, "estado": FALLO,
                "aviso": AVISO_FALLO.format(motivo="la respuesta no trajo "
                                                   "ninguna temática válida"),
                "propuesta": []}
    return {**base, "estado": OK, "aviso": None, "propuesta": propuesta}


def preguntas_guardadas(conn_semantica, ref_estudio):
    """R-TEM.5 — las preguntas de una carga ya confirmada, por `ref_estudio`.

    Del store semántico, que tiene exactamente las que se ingestaron: las
    demográficas y de identidad nunca llegaron ahí (R3.9.d), así que no hay
    nada que excluir por ese lado. Solo código y texto.
    """
    filas = db.todas(
        conn_semantica,
        """
        select p.codigo, p.texto, p.normalizacion
          from pregunta p
          join cuestionario c on c.id = p.cuestionario_id
         where c.ref_estudio = %s
         order by p.orden nulls last, p.codigo
        """,
        (str(ref_estudio),))
    return preguntas_para_proponer(
        [{"codigo": f["codigo"], "texto": f["texto"],
          "excluida": (f["normalizacion"] or {}).get("excluida")} for f in filas])

