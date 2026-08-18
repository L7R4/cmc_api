from pydantic import Field, field_validator

from app.modules.validaciones.schemas import EntradaBase


class EntradaNobis(EntradaBase):
    nro_afiliado: str = Field("", max_length=30)
    # El legacy exige el token en la pantalla pero NUNCA lo manda al WS: sólo
    # lo guarda. Se mantiene el requisito para no cambiarle la regla al
    # prestador (ver `obras/nobis/validador.py`).
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
