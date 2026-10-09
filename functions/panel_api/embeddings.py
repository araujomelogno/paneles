"""Proveedor de embeddings, detrás de una interfaz.

Por defecto Voyage `voyage-3.5` a **512 dimensiones**, que es lo que fija
CLAUDE.md. La interfaz existe para poder cambiar de proveedor sin tocar la
ingesta: el resto del código solo conoce `embeber(textos) -> [[float]]`.

── Por qué 512 y no las 1024 del default de Voyage ──

Porque la mitad de vector es la mitad de instancia. Con 200.000 respuestas,
1024 dims son ~820 MB de vectores y ~1,5–2 GB con el índice HNSW, que no
entran en la instancia chica; 512 son ~410 MB y entran. La diferencia es de
unos US$ 20–35 por mes, para siempre.

Lo que se resigna son uno o dos puntos de calidad de recuperación según los
benchmarks de Voyage. Truncar no es cortar al azar: `voyage-3.5` está
entrenado con *Matryoshka*, así que las primeras dimensiones concentran la
mayor parte de la información. Pero esos benchmarks no son sobre respuestas
de encuesta en español rioplatense, y por eso el cambio no se da por bueno
sin la validación con corpus propio que documenta
`docs/DESPLIEGUE - 512 dimensiones.md`.

**La dimensión es un contrato con la base.** `respuesta.embedding` es
`vector(512)`: si el proveedor devolviera otra cosa, el insert falla. Por eso
`embeber()` comprueba el largo de lo que vuelve, y la ingesta compara contra
la columna antes de mandar nada a embeber (ver `esquema.desajuste_de_dimension`).
"""

import hashlib
import os
import re
import struct

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
LOTE_MAXIMO = 128

# Las dimensiones que `voyage-3.5` acepta. Está acá para poder rechazar un
# valor imposible al construir el proveedor en vez de cuando la API conteste
# un 400 a mitad de una ingesta de 200.000 respuestas.
DIMS_VALIDAS = (256, 512, 1024, 2048)
DIMS_POR_DEFECTO = 512

# ── R-CS · cambio 1 · `input_type` del criterio ──────────────────────
#
# Voyage entrena sus modelos con dos lados: el **documento** que se indexa y
# la **consulta** que lo busca. Con `input_type="query"` le antepone al texto
# un prefijo de recuperación («Represent the query for retrieving supporting
# documents: »), y un criterio —«gente que usa Xiaomi»— es una consulta, no
# un documento. Hasta R-CS el criterio se embebía como documento.
#
# No es neutral (addendum A5): los vectores de los criterios caen en otro
# lugar del espacio y **todas** las consultas cambian. Por eso:
#   · se puede volver atrás sin tocar código: `EMBEDDINGS_TIPO_CONSULTA=document`
#     en el entorno de la función (una revisión nueva, no un deploy), o
#     `tipo_embedding_criterio` en una consulta puntual;
#   · el tipo efectivo queda en el diagnóstico de cada ejecución;
#   · `scripts/diagnosticar_consulta.py input-type` mide un juego de consultas
#     conocidas con los dos tipos, antes y después.
# Las respuestas ingestadas siguen siendo `document`: no se re-embebe nada.
TIPO_DOCUMENTO, TIPO_CONSULTA = "document", "query"
TIPOS_DE_CRITERIO = (TIPO_CONSULTA, TIPO_DOCUMENTO)
TIPO_CRITERIO_POR_DEFECTO = TIPO_CONSULTA


def tipo_de_criterio(entorno=None, pedido=None):
    """El `input_type` con el que se embebe un criterio.

    `pedido` (el de la consulta) manda sobre el entorno, y el entorno sobre el
    default. Un valor que no existe no se adivina: se usa el default, y el
    diagnóstico dice cuál corrió.
    """
    entorno = os.environ if entorno is None else entorno
    for candidato in (pedido, entorno.get("EMBEDDINGS_TIPO_CONSULTA", "")):
        valor = (candidato or "").strip().lower()
        if valor in TIPOS_DE_CRITERIO:
            return valor
    return TIPO_CRITERIO_POR_DEFECTO


class ErrorEmbeddings(RuntimeError):
    pass


class ProveedorEmbeddings:
    dims = DIMS_POR_DEFECTO

    def embeber(self, textos):
        raise NotImplementedError

    def embeber_criterio(self, textos, tipo=TIPO_CRITERIO_POR_DEFECTO):
        """Embebe criterios de consulta. Los proveedores que no distinguen
        lados (el de pruebas) lo resuelven igual que un documento."""
        return self.embeber(textos)

    def _controlar_largo(self, vectores):
        """Que lo que vuelve tenga la dimensión que este proveedor promete.

        Existe por un modo de falla concreto y silencioso: si el parámetro
        que pide la dimensión se escribiera mal, la API lo **ignora** y
        devuelve su default. Sin este control, el síntoma aparecería recién
        al insertar —un error de pgvector sobre un vector de largo
        equivocado— después de haber pagado el embedding del lote entero.
        """
        largos = {len(v) for v in vectores}
        if largos and largos != {self.dims}:
            raise ErrorEmbeddings(
                f"El proveedor devolvió vectores de {sorted(largos)} "
                f"dimensiones y se le pidieron {self.dims}. La base espera "
                f"{self.dims}: con otra cosa, el insert falla. Revisá "
                f"`EMBEDDINGS_DIMS` y que el modelo soporte esa dimensión.")
        return vectores

    def embeber_por_lotes(self, textos, tamano_lote=LOTE_MAXIMO):
        """Generador: `(inicio, vectores)` por cada sub-lote.

        **Reemplaza a `embeber_en_lotes()`, que acumulaba y devolvía todo.**
        No es un refactor: la forma vieja era el patrón de memoria que hacía
        caer la función con una base real. Acumulaba con `extend()` los
        vectores de cada sub-lote hasta tener la lista entera, y con 200.000
        respuestas eso son ~100 millones de números como objetos de Python
        —del orden de varios GB— contra 1 GiB configurado en `main.py`.

        Un generador no arregla por sí solo el consumo: lo arregla que quien
        llama **escriba y suelte** cada sub-lote antes de pedir el siguiente.
        Lo que hace esta forma es que no se pueda acumular sin querer. Con la
        anterior, acumular era lo que pasaba si no hacías nada.

        Se devuelve `inicio` además de los vectores para que el llamador
        pueda emparejarlos con sus filas sin llevar la cuenta aparte, que es
        justo donde se cuela un error de a uno.
        """
        for inicio in range(0, len(textos), tamano_lote):
            yield inicio, self.embeber(textos[inicio : inicio + tamano_lote])


class Voyage(ProveedorEmbeddings):
    def __init__(self, api_key, modelo="voyage-3.5", dims=DIMS_POR_DEFECTO):
        if not api_key:
            raise ErrorEmbeddings(
                "Falta EMBEDDINGS_API_KEY para Voyage (por variable de entorno; "
                "nunca en el repo)."
            )
        if dims not in DIMS_VALIDAS:
            raise ErrorEmbeddings(
                f"`voyage-3.5` no genera vectores de {dims} dimensiones. "
                f"Los valores que acepta son {list(DIMS_VALIDAS)}.")
        self.api_key = api_key
        self.modelo = modelo
        self.dims = dims

    def embeber(self, textos):
        return self._embeber(textos, TIPO_DOCUMENTO)

    def embeber_criterio(self, textos, tipo=TIPO_CRITERIO_POR_DEFECTO):
        return self._embeber(textos, tipo if tipo in TIPOS_DE_CRITERIO
                             else TIPO_CRITERIO_POR_DEFECTO)

    def _embeber(self, textos, tipo):
        import requests  # import diferido: solo hace falta con el proveedor real

        respuesta = requests.post(
            VOYAGE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input": list(textos),
                "model": self.modelo,
                "input_type": tipo,
                # Sin esto la API devuelve su default (1024) pase lo que
                # pase, y `EMBEDDINGS_DIMS` no haría nada. El nombre del
                # parámetro está verificado contra la documentación de
                # Voyage: `output_dimension`, con valores 256/512/1024/2048
                # para los modelos entrenados con Matryoshka. Si alguna vez
                # cambiara, `_controlar_largo()` lo detecta en el acto en vez
                # de dejar pasar vectores de la dimensión equivocada.
                "output_dimension": self.dims,
            },
            timeout=60,
        )
        if respuesta.status_code != 200:
            raise ErrorEmbeddings(
                f"Voyage devolvió {respuesta.status_code}: {respuesta.text[:300]}"
            )
        datos = respuesta.json().get("data", [])
        vectores = [d["embedding"] for d in sorted(datos, key=lambda d: d["index"])]
        if len(vectores) != len(textos):
            raise ErrorEmbeddings(
                f"Voyage devolvió {len(vectores)} vectores para {len(textos)} textos."
            )
        return self._controlar_largo(vectores)


class BolsaDePalabras(ProveedorEmbeddings):
    """Proveedor sin red, para pruebas y para el emulador.

    A cada palabra le asigna una dirección fija, derivada de su hash, y
    devuelve la suma normalizada de las palabras del texto. Es la técnica
    vieja de *random indexing*: dos textos que comparten palabras salen
    cerca, dos que no comparten ninguna salen casi ortogonales.

    Eso importa para poder probar la Fase 2 sin red. La consulta semántica se
    apoya en que la distancia signifique algo: si el vector saliera de un
    hash del texto completo —estable, de la dimensión correcta, y sin ninguna
    relación con el contenido— «me encanta el fernet» quedaría tan lejos de
    «gente a la que le gusta el fernet» como cualquier otra frase, y las
    pruebas de recall no estarían probando el recall.

    No es un modelo de lenguaje: no sabe de sinónimos, ni de negación, ni de
    orden de palabras. Es un piso, deliberadamente parecido a lo que hacía la
    recuperación de información antes de los embeddings. En producción va
    Voyage.
    """

    def __init__(self, dims=DIMS_POR_DEFECTO):
        self.dims = dims

    def _direccion(self, palabra):
        """La dirección fija de una palabra. Determinística y sin estado."""
        semilla = hashlib.sha256(palabra.encode("utf-8")).digest()
        crudo = (semilla * ((self.dims * 4) // len(semilla) + 1))[: self.dims * 4]
        return [
            struct.unpack_from(">I", crudo, i * 4)[0] / 2**32 - 0.5
            for i in range(self.dims)
        ]

    def embeber(self, textos):
        vectores = []
        for texto in textos:
            palabras = re.findall(r"[\wñáéíóúü]+", (texto or "").lower(), re.UNICODE)
            acumulado = [0.0] * self.dims
            for palabra in palabras or [""]:
                for i, valor in enumerate(self._direccion(palabra)):
                    acumulado[i] += valor
            norma = sum(v * v for v in acumulado) ** 0.5 or 1.0
            vectores.append([v / norma for v in acumulado])
        return vectores


# Nombre con el que se lo venía usando en las pruebas de la Fase 1.
Deterministico = BolsaDePalabras


def crear(cfg=None, entorno=None):
    """Elige el proveedor según la configuración."""
    entorno = os.environ if entorno is None else entorno
    proveedor = (
        cfg.proveedor_embeddings if cfg else entorno.get("EMBEDDINGS_PROVEEDOR", "voyage")
    ).lower()
    dims = (cfg.dims_embeddings if cfg
            else int(entorno.get("EMBEDDINGS_DIMS", str(DIMS_POR_DEFECTO))))

    if proveedor in ("deterministico", "bolsa", "fake", "test"):
        return BolsaDePalabras(dims=dims)
    if proveedor == "voyage":
        return Voyage(
            api_key=cfg.api_key_embeddings if cfg else entorno.get("EMBEDDINGS_API_KEY", ""),
            modelo=cfg.modelo_embeddings if cfg else entorno.get("EMBEDDINGS_MODELO", "voyage-3.5"),
            dims=dims,
        )
    raise ErrorEmbeddings(f"Proveedor de embeddings desconocido: {proveedor!r}.")
