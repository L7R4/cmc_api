"""
Padrón de afiliados de OSPM (O.S. 433), para el validador del panel.

OSPM no expone ningún servicio: la validación es contra este padrón, que el
Colegio importa periódicamente desde el CSV que manda la obra social. Sin
padrón cargado no valida nadie.

Tabla NUEVA — no confundir con `clientes_ospm`, que es la del sistema legacy
(`importar_padron_ospm.php`) y sigue existiendo en paralelo para el PHP viejo.
Esta no se toca desde el legacy ni al revés: se cargan por separado. Columnas en
lower_case, como el resto de las tablas nuevas.
"""
import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base


class PadronOspm(Base):
    __tablename__ = "padron_ospm"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # DNI del afiliado — la clave con la que busca el validador. UNIQUE: el
    # padrón trae una fila por afiliado y la importación reemplaza el lote
    # entero, así que un duplicado es un archivo mal armado y conviene que falle.
    documento: Mapped[str] = mapped_column(String(15), nullable=False, unique=True)
    cuit: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    # El legacy recortaba a 20 al grabar la prestación; acá se guarda completo y
    # el recorte queda donde corresponde (al escribir en detalle_facturacion).
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    # Sólo un afiliado activo valida. El CSV trae 'S'/'N'; se normaliza a bool
    # en la importación para no arrastrar el varchar(1) del legacy.
    activo: Mapped[bool] = mapped_column(nullable=False, default=False)
    # De qué importación vino cada fila. Permite auditar "¿con qué padrón se
    # validó esto?" cuando la obra social discute una prestación meses después.
    importado_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # El validador busca por documento y filtra por activo.
        Index("ix_padron_ospm_documento_activo", "documento", "activo"),
    )
