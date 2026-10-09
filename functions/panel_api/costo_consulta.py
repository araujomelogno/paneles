"""R-CS · A2 — cuánto cuesta una consulta, antes y después de correrla.

El addendum lo dice sin vueltas: «completo» es lo más caro que se propuso
hasta ahora, y la diferencia con la exploración no es de grado sino de dos
órdenes de magnitud. Con un corpus de 24.935 respuestas, una ejecución
completa pasa de **una** llamada a Claude por criterio a **cientos**. Eso es
una decisión de producto —quién la lanza, cuántas veces— y para tomarla hace
falta el número **en la pantalla, antes de confirmar**, no en el log.

Este módulo hace tres cosas, y ninguna llama a un proveedor:

* **Estima** (`estimar`): unidades → llamadas → tokens → dólares, por
  criterio y en total, con los supuestos a la vista. Es una cota, y se dice.
* **Calibra** (`calibracion`): si hay ejecuciones completas terminadas, usa
  los tokens que **de verdad** consumió cada unidad en vez de la heurística.
* **Cobra** (`costo`): pasa los tokens que informó cada proveedor a dólares,
  para registrar el costo real por ejecución y por lote.

Las tarifas son de configuración (`COSTO_*`), no del código: cambian, y una
tarifa vieja escrita acá daría estimaciones falsas sin que nada falle.
Los valores por omisión son los públicos vigentes al escribir esto
(octubre 2026): Claude Sonnet 5 a US$ 2 / US$ 10 por millón de tokens de
entrada / salida, Voyage `rerank-2.5` a US$ 0,05 y `voyage-3.5` a US$ 0,06.
"""

import math
import os

from . import db

# ── Tarifas por omisión (US$ por millón de tokens) ───────────────────
TARIFA_CLAUDE_ENTRADA = 2.0
TARIFA_CLAUDE_SALIDA = 10.0
TARIFA_RERANK = 0.05
TARIFA_EMBEDDING = 0.06

# ── Heurística de tokens, a falta de calibración ─────────────────────
CARACTERES_POR_TOKEN = 3.5
"""Español con algo de puntuación. Se redondea hacia arriba: la estimación
es una cota, y quedarse corto es el error que no se puede permitir."""
TOKENS_FIJOS_POR_LLAMADA = 750
"""Instrucciones del verificador + esquema de la herramienta + criterio."""
TOKENS_ENTRADA_POR_UNIDAD_SIN_TEXTO = 40
"""Para cuando se estima sin haber leído los textos (solo con el conteo)."""
TOKENS_SALIDA_POR_VEREDICTO = 45
"""Índice, veredicto y una oración de razón, en JSON de la herramienta."""
TOKENS_SALIDA_FIJOS_POR_LLAMADA = 30
MARGEN = 1.25
"""La estimación que se muestra es la central × este margen: los
reintentos y las subdivisiones de un lote truncado se pagan."""

MUESTRAS_MINIMAS_PARA_CALIBRAR = 3


def _float(valor, por_defecto):
    try:
        numero = float(str(valor).strip())
    except (TypeError, ValueError):
        return por_defecto
    return numero if numero >= 0 else por_defecto


class Tarifas:
    """US$ por millón de tokens, por proveedor."""

    def __init__(self, claude_entrada=TARIFA_CLAUDE_ENTRADA,
                 claude_salida=TARIFA_CLAUDE_SALIDA, rerank=TARIFA_RERANK,
                 embedding=TARIFA_EMBEDDING):
        self.claude_entrada = claude_entrada
        self.claude_salida = claude_salida
        self.rerank = rerank
        self.embedding = embedding

    @classmethod
    def desde_entorno(cls, entorno=None):
        entorno = os.environ if entorno is None else entorno
        return cls(
            claude_entrada=_float(entorno.get("COSTO_CLAUDE_ENTRADA_USD_MTOK", ""),
                                  TARIFA_CLAUDE_ENTRADA),
            claude_salida=_float(entorno.get("COSTO_CLAUDE_SALIDA_USD_MTOK", ""),
                                 TARIFA_CLAUDE_SALIDA),
            rerank=_float(entorno.get("COSTO_RERANK_USD_MTOK", ""), TARIFA_RERANK),
            embedding=_float(entorno.get("COSTO_EMBEDDING_USD_MTOK", ""), TARIFA_EMBEDDING),
        )

    def como_dict(self):
        return {"claude_entrada_usd_mtok": self.claude_entrada,
                "claude_salida_usd_mtok": self.claude_salida,
                "rerank_usd_mtok": self.rerank,
                "embedding_usd_mtok": self.embedding}


def tokens_de(texto):
    """Tokens aproximados de un texto. Hacia arriba, nunca cero."""
    return max(1, math.ceil(len(texto or "") / CARACTERES_POR_TOKEN))


def costo(tarifas, tokens_entrada=0, tokens_salida=0, tokens_rerank=0,
          tokens_embedding=0):
    """Tokens → US$. Es lo que se registra como costo real."""
    return round(
        (tokens_entrada * tarifas.claude_entrada
         + tokens_salida * tarifas.claude_salida
         + tokens_rerank * tarifas.rerank
         + tokens_embedding * tarifas.embedding) / 1_000_000, 6)


# ════════════════════════════════════════════════════════════════════
#  Calibración con lo que de verdad se gastó
# ════════════════════════════════════════════════════════════════════

def calibracion(conn, limite=200):
    """Tokens reales por unidad, de los últimos lotes completos verificados.

    Devuelve `None` si no hay muestras suficientes (o si la bóveda/0025 no
    está aplicada): entonces se estima con la heurística, y la estimación lo
    dice. No levanta: calibrar es una mejora, no una condición.
    """
    try:
        fila = db.una(
            conn,
            """select count(*) as lotes,
                      coalesce(sum(unidades_total), 0) as unidades,
                      coalesce(sum(tokens_entrada), 0) as entrada,
                      coalesce(sum(tokens_salida), 0) as salida,
                      coalesce(sum(tokens_rerank), 0) as rerank,
                      coalesce(sum(llamadas), 0) as llamadas
                 from (select unidades_total, tokens_entrada, tokens_salida,
                              tokens_rerank, llamadas
                         from consulta_lote
                        where estado = 'ok' and tokens_entrada > 0
                        order by actualizado_en desc
                        limit %s) recientes""",
            (limite,))
    except Exception:  # noqa: BLE001 — sin la 0025 no hay con qué calibrar
        conn.rollback()
        return None
    if not fila or fila["lotes"] < MUESTRAS_MINIMAS_PARA_CALIBRAR or not fila["unidades"]:
        return None
    unidades = float(fila["unidades"])
    return {
        "lotes": int(fila["lotes"]),
        "unidades": int(fila["unidades"]),
        "entrada_por_unidad": float(fila["entrada"]) / unidades,
        "salida_por_unidad": float(fila["salida"]) / unidades,
        "rerank_por_unidad": float(fila["rerank"]) / unidades,
    }


# ════════════════════════════════════════════════════════════════════
#  Estimación
# ════════════════════════════════════════════════════════════════════

def estimar_criterio(criterio, unidades, tam_lote, tarifas, tokens_textos=None,
                     calibrado=None, con_rerank=True):
    """El costo de verificar `unidades` unidades de un criterio.

    `tokens_textos` es la suma de tokens de los textos, si se leyeron; si no,
    se usa un promedio fijo. Con `calibrado` se usan los tokens observados.
    """
    unidades = int(unidades)
    llamadas = math.ceil(unidades / max(1, int(tam_lote))) if unidades else 0
    criterio_tokens = tokens_de(criterio)
    if calibrado:
        entrada = unidades * calibrado["entrada_por_unidad"]
        salida = unidades * calibrado["salida_por_unidad"]
        rerank = unidades * calibrado["rerank_por_unidad"] if con_rerank else 0
    else:
        textos = (tokens_textos if tokens_textos is not None
                  else unidades * TOKENS_ENTRADA_POR_UNIDAD_SIN_TEXTO)
        entrada = llamadas * (TOKENS_FIJOS_POR_LLAMADA + criterio_tokens) + textos + unidades * 6
        salida = (llamadas * TOKENS_SALIDA_FIJOS_POR_LLAMADA
                  + unidades * TOKENS_SALIDA_POR_VEREDICTO)
        # Voyage cobra el criterio una vez por documento más el documento.
        rerank = (textos + unidades * criterio_tokens) if con_rerank else 0
    entrada, salida, rerank = (int(math.ceil(x)) for x in (entrada, salida, rerank))
    usd = costo(tarifas, entrada, salida, rerank, criterio_tokens)
    return {
        "criterio": criterio,
        "unidades": unidades,
        "llamadas": llamadas,
        "tokens_entrada": entrada,
        "tokens_salida": salida,
        "tokens_rerank": rerank,
        "usd": usd,
    }


def estimar(por_criterio, tam_lote, tarifas=None, calibrado=None, con_rerank=True):
    """Suma la estimación de cada criterio y la presenta con sus supuestos.

    `por_criterio`: `[{criterio, unidades, tokens_textos?}]`. Devuelve el
    total central y el total con margen, que es el que se compara con el
    presupuesto.
    """
    tarifas = tarifas or Tarifas.desde_entorno()
    detalle = [
        estimar_criterio(c["criterio"], c["unidades"], tam_lote, tarifas,
                         tokens_textos=c.get("tokens_textos"),
                         calibrado=calibrado, con_rerank=con_rerank)
        for c in por_criterio
    ]
    central = round(sum(d["usd"] for d in detalle), 6)
    return {
        "por_criterio": detalle,
        "unidades": sum(d["unidades"] for d in detalle),
        "llamadas": sum(d["llamadas"] for d in detalle),
        "usd_central": central,
        "usd": round(central * MARGEN, 4),
        "margen": MARGEN,
        "tarifas": tarifas.como_dict(),
        "tam_lote": int(tam_lote),
        "calibrada": bool(calibrado),
        "calibracion": calibrado,
        "supuestos": (
            "Tokens observados en ejecuciones anteriores."
            if calibrado else
            f"Heurística: {CARACTERES_POR_TOKEN} caracteres por token, "
            f"{TOKENS_FIJOS_POR_LLAMADA} tokens fijos por llamada y "
            f"{TOKENS_SALIDA_POR_VEREDICTO} de salida por veredicto. Se "
            f"calibra sola cuando haya ejecuciones completas terminadas."),
    }


def estimar_exploratoria(criterios, top_k, max_evidencias, tam_lote, tarifas=None):
    """La cota de una exploratoria: a lo sumo `top_k × max_evidencias`
    unidades por criterio (en general menos: las unidades repetidas se
    verifican una vez). Sirve para comparar con la completa en la pantalla."""
    return estimar(
        [{"criterio": c, "unidades": top_k * max_evidencias} for c in criterios],
        tam_lote, tarifas)
