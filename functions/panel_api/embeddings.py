"""Proveedor de embeddings, detrás de una interfaz.

Por defecto Voyage `voyage-3.5` (1024 dims), que es lo que fija CLAUDE.md.
La interfaz existe para poder cambiar de proveedor sin tocar la ingesta:
el resto del código solo conoce `embeber(textos) -> [[float]]`.
"""

import hashlib
import os
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


class Deterministico(ProveedorEmbeddings):
    """Proveedor sin red, para pruebas y para el emulador.

    Deriva el vector de un hash del texto: no tiene sentido semántico, pero
    es estable y de la dimensión correcta, que es lo que necesitan las
    pruebas de ingesta y de cruce entre stores.
    """

    def __init__(self, dims=1024):
        self.dims = dims

    def embeber(self, textos):
        vectores = []
        for texto in textos:
            semilla = hashlib.sha256(texto.encode("utf-8")).digest()
            crudo = (semilla * ((self.dims * 4) // len(semilla) + 1))[: self.dims * 4]
            valores = [
                struct.unpack_from(">I", crudo, i * 4)[0] / 2**32 - 0.5
                for i in range(self.dims)
            ]
            norma = sum(v * v for v in valores) ** 0.5 or 1.0
            vectores.append([v / norma for v in valores])
        return vectores


def crear(cfg=None, entorno=None):
    """Elige el proveedor según la configuración."""
    entorno = os.environ if entorno is None else entorno
    proveedor = (
        cfg.proveedor_embeddings if cfg else entorno.get("EMBEDDINGS_PROVEEDOR", "voyage")
    ).lower()
    dims = cfg.dims_embeddings if cfg else int(entorno.get("EMBEDDINGS_DIMS", "1024"))

    if proveedor in ("deterministico", "fake", "test"):
        return Deterministico(dims=dims)
    if proveedor == "voyage":
        return Voyage(
            api_key=cfg.api_key_embeddings if cfg else entorno.get("EMBEDDINGS_API_KEY", ""),
            modelo=cfg.modelo_embeddings if cfg else entorno.get("EMBEDDINGS_MODELO", "voyage-3.5"),
            dims=dims,
        )
    raise ErrorEmbeddings(f"Proveedor de embeddings desconocido: {proveedor!r}.")
