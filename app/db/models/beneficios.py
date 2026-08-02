"""
Beneficios / convenios para socios (descuentos en comercios, servicios, etc.).

Los administra la web (app/modules/beneficios) y los consume la app móvil
(GET /api/mobile/beneficios, solo los activos). Los nombres de columna son
lower_case: es tabla nueva, no legacy.
"""
import datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base

# Categorías válidas. Es la misma lista que muestra el selector del admin y la
# que la app usa para agrupar/filtrar: si se agrega una, hay que agregarla
# también en el app (BENEFICIO_CATEGORIAS de su types.ts).
CATEGORIAS_BENEFICIO: tuple[str, ...] = (
    "Salud",
    "Gastronomía",
    "Educación",
    "Turismo",
    "Bienestar",
    "Comercios",
    "Automotor",
    "Servicios",
)


class Beneficio(Base):
    __tablename__ = "beneficios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    titulo: Mapped[str] = mapped_column(String(160), nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    # Texto libre, no un número: "20%", "2x1", "Tarifa socio".
    descuento: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    # Sin Enum a propósito: sumar una categoría no debe requerir migración.
    # La validación vive en los schemas (CATEGORIAS_BENEFICIO).
    categoria: Mapped[str] = mapped_column(String(40), nullable=False)
    # Color de acento de la tarjeta en la app (hex "#RRGGBB") o NULL = color por
    # defecto del app. Es sólo estético (llama la atención del socio); se guarda
    # el hex para que el app decida cómo aplicarlo (borde, fondo tenue, etc.).
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)
    ubicacion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    vigencia_hasta: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Cubre el listado de la app (WHERE activo=1 ORDER BY categoria, titulo)
        # sin tocar la tabla.
        Index("ix_beneficios_activo_categoria", "activo", "categoria"),
    )
