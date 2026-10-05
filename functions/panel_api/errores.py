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
    """Guardrail R1.6: se intentó mandar PII al store semántico."""

    status = 500
    codigo = "fuga_de_pii"


class EnvioNoConfigurado(ErrorApi):
    """R-MAIL.2 — no hay proveedor de envío y el modo desarrollo está apagado.

    Es un problema de configuración, no del usuario: se dice así, y **no** se
    ofrece un atajo que reemplace al correo. Se levanta antes de mirar si el
    destinatario existe, así que contesta igual para cualquier dirección.
    """

    status = 503
    codigo = "envio_no_configurado"


class EnvioFallido(ErrorApi):
    """R-MAIL.1 — el proveedor intentó y no pudo entregar el mensaje.

    Nunca se reporta éxito cuando el envío falló: la persona se quedaría
    esperando un correo que no va a llegar. El motivo técnico queda en
    `envio_correo`; al usuario se le dice que vuelva a intentar.
    """

    status = 503
    codigo = "envio_fallido"
