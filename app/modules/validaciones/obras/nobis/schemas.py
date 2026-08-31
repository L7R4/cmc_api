from pydantic import Field, field_validator

from app.modules.validaciones.schemas import EntradaBase


class EntradaNobis(EntradaBase):
    nro_afiliado: str = Field("", max_length=30)
    # Viaja en `<Token>` dentro de la orden y Nobis lo valida ("Token
    # incorrecto" aparece 21 veces en el log real). Son de 6 dígitos, no de 4
    # como los de Sancor — por eso acá no hay un regex de largo fijo.
    token: str = Field("", max_length=8)

    @field_validator("nro_afiliado", mode="after")
    @classmethod
    def _nro_afiliado_obligatorio(cls, v: str) -> str:
        if not v:
            raise ValueError("Falta el número de afiliado.")
        return v

    @field_validator("token", mode="after")
    @classmethod
    def _token_obligatorio(cls, v: str) -> str:
        if not v:
            raise ValueError("Nobis exige el token de la credencial del afiliado.")
        return v
