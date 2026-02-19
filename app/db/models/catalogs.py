import datetime
import decimal
from typing import Optional

from sqlalchemy import DECIMAL, Date, Index, Integer, String, text
from sqlalchemy.dialects.mysql import INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Especialidad(Base):
    __tablename__ = 'especialidad'
    __table_args__ = (
        Index('ESPECIALIDAD', 'ESPECIALIDAD'),
        Index('IDCOLEGIO', 'ID_COLEGIO_ESPE')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    ID_COLEGIO_ESPE: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='COLEGIO MEDICO, EL ID DE LA ESPECIALIDAD DEL COLEGIO MEDICO')
    ESPECIALIDAD: Mapped[str] = mapped_column(String(50, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='COLEGIO MEDICO')


class ObrasSociales(Base):
    __tablename__ = 'obras_sociales'
    __table_args__ = (
        Index('MARCA', 'MARCA'),
        Index('NRO_OBRASOCIAL', 'NRO_OBRASOCIAL'),
        Index('OBRA_SOCIAL', 'OBRA_SOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_OBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    OBRA_SOCIAL: Mapped[str] = mapped_column(String(45, 'utf8_spanish2_ci'), nullable=False, server_default=text("'a'"))
    MARCA: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'N'"))
    VER_VALOR: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'N'"))


class Periodos(Base):
    __tablename__ = 'periodos'
    __table_args__ = (
        Index('ANIO', 'ANIO'),
        Index('FECHA', 'FECHA'),
        Index('MES', 'MES'),
        Index('NRO_OBRA_SOCIAL', 'NRO_OBRA_SOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    MES: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    ANIO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CERRADO: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'C'"), comment='C=CERRADO / A=ABIERTO')
    TIPO_FACT: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    NRO_FACT_1: Mapped[str] = mapped_column(String(5, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    NRO_FACT_2: Mapped[str] = mapped_column(String(8, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    NRO_OBRA_SOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    FECHA: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'-'"))
    USUARIO: Mapped[int] = mapped_column(INTEGER(10), nullable=False, server_default=text("'0'"))


class PeriodosDoctor(Base):
    __tablename__ = 'periodos_doctor'
    __table_args__ = (
        Index('ANIO_DOCTOR', 'ANIO_DOCTOR'),
        Index('CERRADO_DOCTOR', 'CERRADO_DOCTOR'),
        Index('FECHA', 'FECHA'),
        Index('MES_DOCTOR', 'MES_DOCTOR'),
        Index('NRO_OBRA_SOCIAL', 'NRO_OBRA_SOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    MES_DOCTOR: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    ANIO_DOCTOR: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CERRADO_DOCTOR: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'C'"))
    NRO_OBRA_SOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    FECHA: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'-'"))


class ValoresBoletin(Base):
    __tablename__ = 'valores_boletin'
    __table_args__ = (
        Index('CATEGORIA_A', 'CATEGORIA_A'),
        Index('CATEGORIA_B', 'CATEGORIA_B'),
        Index('CATEGORIA_C', 'CATEGORIA_C'),
        Index('FECHA_CAMBIO', 'FECHA_CAMBIO'),
        Index('NIVEL', 'NIVEL'),
        Index('NRO_OBRASOCIAL', 'NRO_OBRASOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_OBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CONSULTA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_QUIRURGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_QUIRURGICOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_PRACTICA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_RADIOLOGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_RADIOLOGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_BIOQUIMICOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    OTROS_GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_CIRUGIA_ADULTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_CIRUGIA_INFANTIL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CONSULTA_ESPECIAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CATEGORIA_A: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    CATEGORIA_B: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    CATEGORIA_C: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    NIVEL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'7'"))
    FECHA_CAMBIO: Mapped[Optional[datetime.date]] = mapped_column(Date)


class ValoresBoletinHistorial(Base):
    __tablename__ = 'valores_boletin_historial'
    __table_args__ = (
        Index('CATEGORIA_A', 'CATEGORIA_A'),
        Index('CATEGORIA_B', 'CATEGORIA_B'),
        Index('CATEGORIA_C', 'CATEGORIA_C'),
        Index('FECHA_CAMBIO', 'FECHA_CAMBIO'),
        Index('NIVEL', 'NIVEL'),
        Index('NRO_OBRASOCIAL', 'NRO_OBRASOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_OBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CONSULTA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_QUIRURGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_QUIRURGICOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_PRACTICA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_RADIOLOGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_RADIOLOGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_BIOQUIMICOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    OTROS_GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_CIRUGIA_ADULTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_CIRUGIA_INFANTIL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CONSULTA_ESPECIAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CATEGORIA_A: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    CATEGORIA_B: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    CATEGORIA_C: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    NIVEL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'7'"))
    FECHA_CAMBIO: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'-'"))


class ValoresObrasocial(Base):
    __tablename__ = 'valores_obrasocial'
    __table_args__ = (
        Index('NRO_OBRASOCIAL', 'NRO_OBRASOCIAL'),
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_OBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CONSULTA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALEANO_QUIRURGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_QUIRURGICOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_PRACTICA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_RADIOLOGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_RADIOLOGICO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_BIOQUIMICOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    OTROS_GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_CIRUGIA_ADULTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GALENO_CIRUGIA_INFANTIL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
