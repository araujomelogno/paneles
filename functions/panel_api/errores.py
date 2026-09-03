"""Errores de dominio, con el código HTTP que les corresponde."""


class ErrorApi(Exception):
    """Error esperable: se traduce a una respuesta JSON con su status."""

    status = 400
    codigo = "error"

    def __init__(self, mensaje, detalle=None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.detalle = detalle

    def como_dict(self):
        cuerpo = {"error": self.codigo, "mensaje": self.mensaje}
        if self.detalle is not None:
            cuerpo["detalle"] = self.detalle
        return cuerpo


class DatosInvalidos(ErrorApi):
    status = 400
    codigo = "datos_invalidos"


class NoAutenticado(ErrorApi):
    status = 401
    codigo = "no_autenticado"


class SinPermiso(ErrorApi):
    status = 403
    codigo = "sin_permiso"


class NoEncontrado(ErrorApi):
    status = 404
    codigo = "no_encontrado"


class Conflicto(ErrorApi):
    status = 409
    codigo = "conflicto"


class ConsentimientoFaltante(ErrorApi):
    """Gate de consentimiento: la operación exige una finalidad vigente."""

    status = 403
    codigo = "consentimiento_faltante"


class FugaDePII(ErrorApi):
    """Guardrail R1.6: se intentó mandar PII al store remoto."""

    status = 500
    codigo = "fuga_de_pii"
