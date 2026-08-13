from pydantic import Field

from app.modules.validaciones.schemas import EntradaBase


class EntradaOspm(EntradaBase):
    """OSPM no pide número de afiliado: valida por DNI contra el padrón propio.
    El formulario manda el DNI en `nro_afiliado` (mismo campo que usan las
    demás obras sociales) — acá se lo llama por lo que es."""

    documento: str = Field("", validation_alias="nro_afiliado")
