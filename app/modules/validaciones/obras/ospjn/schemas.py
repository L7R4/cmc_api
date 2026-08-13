from pydantic import Field, field_validator

from app.modules.validaciones.schemas import EntradaBase


class EntradaOspjn(EntradaBase):
    nro_afiliado: str = Field("", max_length=30)
    # Dígito verificador / orden familiar, opcional.
    barra_afiliado: str = Field("", max_length=2)

    @field_validator("nro_afiliado", mode="after")
    @classmethod
    def _nro_afiliado_obligatorio(cls, v: str) -> str:
        if not v:
            raise ValueError("Falta el número de afiliado.")
        return v
