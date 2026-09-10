"""Un corpus chico pero con los casos que la Fase 2 tiene que resolver.

No es una muestra al azar: cada persona está para probar algo.

    Ana    dice que toma fernet y que le encanta          → tiene que aparecer
    Beto   dice que toma fernet y que NO le gusta         → polaridad opuesta
    Cora   habla de whisky                                → fuera de tema
    Dani   habla de cerveza                               → fuera de tema
    Elena  le encanta el fernet pero SIN uso_semantico    → gate (R2.11)

Beto y Elena son los dos casos que separan un buscador de un sistema. Beto
sale primero en el recall —«no me gusta el fernet» comparte casi todas las
palabras con el criterio— y tiene que quedar afuera del ranking final. Elena
cumpliría el criterio y no puede aparecer nunca, porque no consintió el uso
semántico entre estudios.
"""

from panel_api import encuestas, paneles, personas

VERSION = "consentimiento-2026-01"

PREGUNTAS = [
    {"codigo": "P1", "texto": "¿Qué bebida consume habitualmente?",
     "tipo": "cerrada", "opciones": {"1": "Fernet con cola", "2": "Whisky",
                                     "3": "Cerveza"}, "orden": 1},
    {"codigo": "P2", "texto": "¿Por qué la elige?", "tipo": "abierta", "orden": 2},
]

GENTE = [
    # nombre, sexo, fecha_nac, localidad, P1, P2, finalidades
    ("Ana Pérez", "F", "1990-03-15", "Montevideo", "1",
     "Porque me encanta el fernet, lo tomo siempre",
     ("contacto_participacion", "uso_semantico")),
    ("Beto Silva", "M", "1980-07-02", "Montevideo", "1",
     "No me gusta el fernet, lo detesto",
     ("contacto_participacion", "uso_semantico")),
    ("Cora Díaz", "F", "1975-11-20", "Salto", "2",
     "Por la calidad del whisky",
     ("contacto_participacion", "uso_semantico")),
    ("Dani Rodríguez", "M", "2001-01-09", "Montevideo", "3",
     "Es lo que toman mis amigos",
     ("contacto_participacion", "uso_semantico")),
    ("Elena Núñez", "F", "1995-05-05", "Colonia", "1",
     "Me encanta el fernet, es mi bebida favorita",
     ("contacto_participacion",)),   # sin uso_semantico: gate de R2.11
]

CRITERIO_FERNET = "gente a la que le gusta el fernet"


def sembrar(conn_boveda, conn_semantica, proveedor, ingestar=True):
    """Arma panel, personas, ola e ingesta. Devuelve el mapa de nombres."""
    panel = paneles.crear(conn_boveda, "Panel Nacional", "Cobertura país.")

    por_nombre = {}
    for indice, (nombre, sexo, fnac, localidad, _, _, finalidades) in enumerate(GENTE):
        alta = personas.alta(
            conn_boveda,
            {
                "persona": {
                    "nombre": nombre, "sexo": sexo, "fecha_nacimiento": fnac,
                    "localidad": localidad,
                    "email": f"{nombre.split()[0].lower()}@ejemplo.uy",
                },
                "consentimientos": [
                    {"finalidad": f, "version_texto": VERSION} for f in finalidades
                ],
                "origen": "dooblo",
                "id_en_origen": f"R-{indice + 1:03d}",
                "panel_id": panel["id"],
            },
            actor="prueba",
        )
        por_nombre[nombre] = alta["id_persona"]

    encuesta = encuestas.crear(conn_boveda, panel["id"], "Ola 1 — Bebidas", "2026-07-15")
    encuestas.convocar(conn_boveda, encuesta["id"], todo_el_panel=True)

    resultado = None
    if ingestar:
        filas = [
            {"id_en_origen": f"R-{i + 1:03d}", "P1": g[4], "P2": g[5]}
            for i, g in enumerate(GENTE)
        ]
        resultado = encuestas.ingestar(
            conn_boveda, conn_semantica, encuesta["id"], PREGUNTAS, filas,
            proveedor=proveedor,
        )

    return {
        "panel": panel,
        "encuesta": encuesta,
        "por_nombre": por_nombre,
        "por_id": {v: k for k, v in por_nombre.items()},
        "ingesta": resultado,
    }


def nombres(resultado, contexto):
    """Los nombres de los `items` de un resultado, para poder leer los tests."""
    return [contexto["por_id"][item["id_persona"]] for item in resultado["items"]]


def nombres_excluidos(resultado, contexto):
    return {
        contexto["por_id"][e["id_persona"]]: e["motivo"]
        for e in resultado["excluidos"]
    }
