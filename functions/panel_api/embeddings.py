"""Proveedor de embeddings, detrás de una interfaz.

Por defecto Voyage `voyage-3.5` (1024 dims), que es lo que fija CLAUDE.md.
La interfaz existe para poder cambiar de proveedor sin tocar la ingesta:
el resto del código solo conoce `embeber(textos) -> [[float]]`.
"""

import hashlib
import os
import re
import struct

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
LOTE_MAXIMO = 128


class ErrorEmbeddings(RuntimeError):
    pass


class ProveedorEmbeddings:
    dims = 1024

    def embeber(self, textos):
        raise NotImplementedError

    def embeber_en_lotes(self, textos, tamano_lote=LOTE_MAXIMO):
        vectores = []
        for inicio in range(0, len(textos), tamano_lote):
            vectores.extend(self.embeber(textos[inicio : inicio + tamano_lote]))
        return vectores


class Voyage(ProveedorEmbeddings):
    def __init__(self, api_key, modelo="voyage-3.5", dims=1024):
        if not api_key:
            raise ErrorEmbeddings(
                "Falta EMBEDDINGS_API_KEY para Voyage (por variable de entorno; "
                "nunca en el repo)."
            )
        self.api_key = api_key
        self.modelo = modelo
        self.dims = dims

    def embeber(self, textos):
        import requests  # import diferido: solo hace falta con el proveedor real

        respuesta = requests.post(
            VOYAGE_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"input": list(textos), "model": self.modelo, "input_type": "document"},
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
        return vectores


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

    def __init__(self, dims=1024):
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
    dims = cfg.dims_embeddings if cfg else int(entorno.get("EMBEDDINGS_DIMS", "1024"))

    if proveedor in ("deterministico", "bolsa", "fake", "test"):
        return BolsaDePalabras(dims=dims)
    if proveedor == "voyage":
        return Voyage(
            api_key=cfg.api_key_embeddings if cfg else entorno.get("EMBEDDINGS_API_KEY", ""),
            modelo=cfg.modelo_embeddings if cfg else entorno.get("EMBEDDINGS_MODELO", "voyage-3.5"),
            dims=dims,
        )
    raise ErrorEmbeddings(f"Proveedor de embeddings desconocido: {proveedor!r}.")
