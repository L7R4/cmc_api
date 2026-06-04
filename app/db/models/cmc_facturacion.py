import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DetalleFacturacionCMC(Base):
    """Tabla detalle_facturacion importada desde CMC. Solo lectura."""
    __tablename__ = "detalle_facturacion"

    id_detalle_prestaciones: Mapped[int] = mapped_column(Integer, primary_key=True)
    periodo: Mapped[str] = mapped_column(String(6), nullable=False)
    cod_med: Mapped[str] = mapped_column(String(20), nullable=False)
    categoria: Mapped[Optional[str]] = mapped_column(String(1))
    nro_orden: Mapped[Optional[str]] = mapped_column(String(30))
    cod_obr: Mapped[Optional[str]] = mapped_column(String(10))
    cod_nom: Mapped[Optional[str]] = mapped_column(String(20))
    tpo_funcion: Mapped[Optional[str]] = mapped_column(String(5))
    sesion: Mapped[Optional[int]] = mapped_column(Integer)
    cantidad: Mapped[Optional[int]] = mapped_column(Integer)
    honorarios: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    gastos: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    ayudante: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    importe_total: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    manual: Mapped[Optional[str]] = mapped_column(String(1))
    dni_p: Mapped[Optional[str]] = mapped_column(String(15))
    nom_ape_p: Mapped[Optional[str]] = mapped_column(String(100))
    tpo_serv: Mapped[Optional[str]] = mapped_column(String(1))
    cod_clinica: Mapped[Optional[int]] = mapped_column(Integer)
    fecha_practica: Mapped[Optional[datetime.date]] = mapped_column(Date)
    tipo_orden: Mapped[Optional[str]] = mapped_column(String(1))
    porc: Mapped[Optional[int]] = mapped_column(Integer)
    cod_med_indica: Mapped[Optional[str]] = mapped_column(String(20))
    codigo_oms: Mapped[Optional[str]] = mapped_column(String(20))
    diag: Mapped[Optional[str]] = mapped_column(Text)
    nro_vias: Mapped[Optional[int]] = mapped_column(Integer)
    fin_semana: Mapped[Optional[str]] = mapped_column(String(1))
    nocturno: Mapped[Optional[str]] = mapped_column(String(1))
    feriado: Mapped[Optional[str]] = mapped_column(String(2))
    urgencia: Mapped[Optional[str]] = mapped_column(String(1))
    estado: Mapped[Optional[str]] = mapped_column(String(2))
    usuario: Mapped[Optional[str]] = mapped_column(String(30))
    id_especialidad: Mapped[Optional[int]] = mapped_column(Integer)


class FacturacionCMC(Base):
    """Tabla facturacion importada desde CMC (lotes de facturación cerrados). Solo lectura."""
    __tablename__ = "facturacion"

    id_prestaciones: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_cliente: Mapped[Optional[int]] = mapped_column(Integer)
    tipo_factura: Mapped[Optional[str]] = mapped_column(String(10))
    nro_factura: Mapped[Optional[str]] = mapped_column(String(20))
    tipo_factura_2: Mapped[Optional[str]] = mapped_column(String(10))
    nro_factura_2: Mapped[Optional[str]] = mapped_column(String(20))
    tipo_factura_3: Mapped[Optional[str]] = mapped_column(String(10))
    nro_factura_3: Mapped[Optional[str]] = mapped_column(String(20))
    periodo: Mapped[Optional[str]] = mapped_column(String(6))
    cod_obr: Mapped[Optional[str]] = mapped_column(String(10))
    fecha: Mapped[Optional[datetime.date]] = mapped_column(Date)
    fecha_envio: Mapped[Optional[datetime.date]] = mapped_column(Date)
    fecha_recep: Mapped[Optional[datetime.date]] = mapped_column(Date)
    importe: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2))
    afip: Mapped[Optional[str]] = mapped_column(String(1))
    usuario: Mapped[Optional[str]] = mapped_column(String(30))
    estado: Mapped[Optional[str]] = mapped_column(String(2))
