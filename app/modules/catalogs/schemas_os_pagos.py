"""Entrada y salida del registro de deuda de una obra social.

Tres campos y un adjunto: fecha, monto y estado. El Colegio pidió el registro
mínimo, así que acá no hay concepto, período, vencimiento ni monto cobrado.
"""
import datetime
import decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

EstadoPago = Literal["pendiente", "parcial", "pagado"]


class PagoIn(BaseModel):
    """Los datos de la deuda. **No incluye la factura**: se sube aparte.

    Separarlos evita el problema clásico de los formularios con adjunto: editar
    el monto con un form que manda el campo de archivo vacío y borrar la factura
    sin querer.
    """

    fecha: datetime.date
    monto: decimal.Decimal = Field(..., ge=0, max_digits=14, decimal_places=2)
    estado: EstadoPago = "pendiente"


class PagoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    obra_social_id: int

    fecha: datetime.date
    monto: decimal.Decimal
    estado: EstadoPago

    #: URL autorizada del adjunto (`/api/archivos/…`), o `null` si no hay.
    factura_url: Optional[str] = None
    factura_nombre: Optional[str] = None

    created_at: Optional[datetime.datetime] = None


class ResumenPagos(BaseModel):
    """Totales para el encabezado de la pestaña.

    Se calculan del lado del servidor y no sumando en el front lo que se ve: si
    mañana la lista se pagina, sumar la página daría un total que cambia al
    pasar de página.
    """

    #: Todo lo registrado, en cualquier estado.
    total: decimal.Decimal = decimal.Decimal("0")
    #: Lo que sigue sin saldarse — `pendiente` y `parcial`.
    adeudado: decimal.Decimal = decimal.Decimal("0")
    #: Lo marcado como `pagado`.
    pagado: decimal.Decimal = decimal.Decimal("0")
    #: Cuántas filas no están en `pagado`.
    pendientes: int = 0


class PagosPage(BaseModel):
    items: list[PagoOut] = []
    resumen: ResumenPagos = ResumenPagos()
