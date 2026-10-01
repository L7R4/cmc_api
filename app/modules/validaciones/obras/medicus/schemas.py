import re

from pydantic import Field, field_validator

from app.modules.validaciones.schemas import EntradaBase

# La credencial de Medicus es numérica. Los afiliados de prueba del anexo tienen
# 11 dígitos ("09227263000"); se deja margen hasta 13 porque el anexo no fija el
# largo y el reporte de Swiss muestra credenciales más largas en otras obras.
_CREDENCIAL_RE = re.compile(r"^\d{6,13}$")


class EntradaMedicus(EntradaBase):
    """Lo que hace falta para pedir una autorización a Medicus.

    No pide token —a diferencia de Sancor, Medicus no usa el token de la
    credencial— ni diagnóstico: ese va fijo desde `mensajes.py`.
    """

    nro_afiliado: str = Field("", max_length=30)

    @field_validator("nro_afiliado", mode="after")
    @classmethod
    def _credencial_valida(cls, v: str) -> str:
        if not v:
            raise ValueError("Falta el número de afiliado de Medicus.")
        if not _CREDENCIAL_RE.match(v):
            # Se corta antes de gastar el request si ya se sabe que va a fallar,
            # mismo criterio que el token de Sancor.
            raise ValueError(
                "El número de afiliado de Medicus tiene que ser numérico, "
                "de 6 a 13 dígitos."
            )
        return v
