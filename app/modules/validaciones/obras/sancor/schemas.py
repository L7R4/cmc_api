import re

from pydantic import Field, field_validator

from app.modules.validaciones.schemas import EntradaBase

# Formato que exige Sancor (mismo patrón que valida `sancor_1.php` en el
# formulario del legacy).
_TOKEN_RE = re.compile(r"^\d{4}$")
_NRO_AFILIADO_RE = re.compile(r"^\d{1,10}$")
_BARRA_AFILIADO_RE = re.compile(r"^\d{1,2}$")


class EntradaSancor(EntradaBase):
    token: str = Field("", max_length=8)
    nro_afiliado: str = Field("", max_length=30)
    # Dígito verificador / orden familiar, opcional.
    barra_afiliado: str = Field("", max_length=2)

    @field_validator("token", mode="after")
    @classmethod
    def _token_valido(cls, v: str) -> str:
        if not v:
            raise ValueError("Sancor exige el token de la credencial del afiliado.")
        if not _TOKEN_RE.match(v):
            # Mismo caso que M117 en la respuesta real de Sancor ("El Token es
            # inválido" / "debe ser de 4 dígitos"): se corta antes de gastar el
            # request si ya se sabe que va a fallar.
            raise ValueError("El token de Sancor tiene que ser de 4 dígitos.")
        return v

    @field_validator("nro_afiliado", mode="after")
    @classmethod
    def _nro_afiliado_valido(cls, v: str) -> str:
        if not v:
            raise ValueError("Falta el número de afiliado.")
        if not _NRO_AFILIADO_RE.match(v):
            raise ValueError("El número de afiliado tiene que ser numérico, de hasta 10 dígitos.")
        return v

    @field_validator("barra_afiliado", mode="after")
    @classmethod
    def _barra_afiliado_valida(cls, v: str) -> str:
        if v and not _BARRA_AFILIADO_RE.match(v):
            raise ValueError("La barra del afiliado tiene que ser numérica, de hasta 2 dígitos.")
        return v
