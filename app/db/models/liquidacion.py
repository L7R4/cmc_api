import datetime
import decimal
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import DECIMAL, Date, DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import INTEGER
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditMixin, Base


class GuardarAtencion(Base):
    __tablename__ = 'guardar_atencion'
    __table_args__ = (
        Index('ANIO_PERIODO', 'ANIO_PERIODO'),
        Index('AYUDANTE_2', 'AYUDANTE_2'),
        Index('CATEGORIA_A_B_C', 'CATEGORIA_A_B_C'),
        Index('CODIGO_PRESTACION', 'CODIGO_PRESTACION'),
        Index('CON_HONO_SANA', 'CON_HONO_SANA'),
        Index('FECHA_CARGA', 'FECHA_CARGA'),
        Index('FECHA_PRESTACION', 'FECHA_PRESTACION'),
        Index('MAT_AYUDANTE_2', 'MAT_AYUDANTE_2'),
        Index('MES_PERIODO', 'MES_PERIODO'),
        Index('NOMBRE_AFILIADO', 'NOMBRE_AFILIADO'),
        Index('NOMBRE_AYUDANTE', 'NOMBRE_AYUDANTE'),
        Index('NOMBRE_AYUDANTE_2', 'NOMBRE_AYUDANTE_2'),
        Index('NOMBRE_PRESTADOR', 'NOMBRE_PRESTADOR'),
        Index('NRO_DOCUMENTO', 'NRO_DOCUMENTO'),
        Index('NRO_ESPECIALIDAD', 'NRO_ESPECIALIDAD'),
        Index('NRO_MATRICULA', 'NRO_MATRICULA'),
        Index('NRO_SOCIO', 'NRO_SOCIO'),
        Index('SANATORIO', 'SANATORIO')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_SOCIO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='SOCIO DEL COLEGIO MEDICO')
    CODIGO_PRESTACION: Mapped[str] = mapped_column(String(8, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"))
    NRO_MATRICULA: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='matricula prov. colegio medico y judicial')
    NOMBRE_PRESTADOR: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='campo colegio medico y judicial')
    ESTADODESCRIPCION: Mapped[str] = mapped_column(String(100, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"))
    MENSAJE: Mapped[str] = mapped_column(String(5, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"))
    NOMBRE_AFILIADO: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='CAMPO COLEGIO Y judicial')
    NRO_AFILIADO: Mapped[str] = mapped_column(String(20, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='CAMPO JUDICIAL')
    BARRA_AFILIADO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_CONSULTA: Mapped[str] = mapped_column(String(16, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"))
    NRO_DOCUMENTO: Mapped[str] = mapped_column(String(13, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"))
    RESULTADO: Mapped[str] = mapped_column(String(5, 'utf8_spanish_ci'), nullable=False, server_default=text("'false'"), comment='true / false - campo judicial')
    FECHASUSPENSION: Mapped[str] = mapped_column(String(10, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='campo judicial')
    NRO_OBRA_SOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='COLEGIO MEDICO')
    IMPORTE_COLEGIO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"), comment='COLEGIO MEDICO')
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    NRO_ESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CANTIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'1'"))
    EXISTE: Mapped[str] = mapped_column(String(1, 'utf8_spanish_ci'), nullable=False, server_default=text("'S'"), comment='N=ELIMINADO / S=EXISTE')
    NOMBRE_ARCHIVO: Mapped[str] = mapped_column(String(100, 'utf8_spanish_ci'), nullable=False, server_default=text("'A1'"))
    MES_PERIODO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    ANIO_PERIODO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CANT_TRATAMIENTO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    AYUDANTE_ACTUAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CATEGORIA_A_B_C: Mapped[str] = mapped_column(String(1, 'utf8_spanish_ci'), nullable=False, server_default=text("'-'"))
    PORCENTAJE: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    SANATORIO: Mapped[str] = mapped_column(String(50, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"))
    PACIENTE: Mapped[str] = mapped_column(String(50, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"))
    CODIGO_PRESTACION_2: Mapped[str] = mapped_column(String(8, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"))
    CIRUJANO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    PORCENTAJE_CIRUJANO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NOMBRE_AYUDANTE: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"))
    MAT_AYUDANTE: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    PORCENTAJE_AYUDANTE: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    VALOR_CIRUJIA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    VALOR_AYUDANTE: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CON_HONO_SANA: Mapped[str] = mapped_column(String(1, 'utf8_spanish_ci'), nullable=False, server_default=text("'C'"), comment='CON=CONSULTA HONO=HONORARIO/ SANA=SANATORIO')
    TOKEN: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    USUARIO_COLEGIO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    AYUDANTE_2: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NOMBRE_AYUDANTE_2: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'a'"))
    MAT_AYUDANTE_2: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    PORCENTAJE_AYUDANTE_2: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    VALOR_AYUDANTE_2: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CODIGO_PRESTACION_3: Mapped[str] = mapped_column(String(6, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"))
    COSEGURO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    IMPORTE_AYUDANTE_2: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    FECHA_PRESTACION: Mapped[Optional[datetime.date]] = mapped_column(Date, comment='COLEGIO MEDICO')
    FECHA_CARGA: Mapped[Optional[datetime.date]] = mapped_column(Date)
    FECHA_CIRUGIA: Mapped[Optional[datetime.date]] = mapped_column(Date)


class Pago(AuditMixin, Base):
    """Corrida de liquidación global (antes LiquidacionResumen)."""
    __tablename__ = "pago"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int] = mapped_column(Integer, nullable=False)
    descripcion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estado: Mapped[Literal["A", "C"]] = mapped_column(
        Enum("A", "C", name="pago_estado"), default="A", server_default="A"
    )
    cierre_timestamp: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    liquidaciones: Mapped[list["Liquidacion"]] = relationship(
        back_populates="pago",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    lotes: Mapped[list["LoteAjuste"]] = relationship(
        back_populates="pago",
        foreign_keys="[LoteAjuste.pago_id]",
    )
    recibos: Mapped[list["Recibo"]] = relationship(
        back_populates="pago",
    )
    pagos_medico: Mapped[list["PagoMedico"]] = relationship(
        back_populates="pago",
        cascade="all, delete-orphan",
    )

    # SIN unique en (anio, mes) — múltiples pagos por período permitidos


class Liquidacion(AuditMixin, Base):
    __tablename__ = "liquidacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pago_id: Mapped[int] = mapped_column(ForeignKey("pago.id"), nullable=False, index=True)

    obra_social_id: Mapped[int] = mapped_column(Integer, index=True)
    mes_periodo: Mapped[int] = mapped_column(Integer)
    anio_periodo: Mapped[int] = mapped_column(Integer)

    nro_factura: Mapped[Optional[str]] = mapped_column(String(30))

    total_bruto: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=0)
    total_debitos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=0)
    total_creditos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=0)
    total_neto: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=0)

    pago: Mapped[Optional["Pago"]] = relationship(back_populates="liquidaciones")
    detalles: Mapped[list["DetalleLiquidacion"]] = relationship(
        back_populates="liquidacion",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("pago_id", "obra_social_id", "mes_periodo", "anio_periodo", name="uq_liq_pago_os_per"),
        Index("idx_liq_pago_os_per", "pago_id", "obra_social_id", "mes_periodo", "anio_periodo"),
    )


class DetalleLiquidacion(AuditMixin, Base):
    __tablename__ = "detalle_liquidacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    liquidacion_id: Mapped[int] = mapped_column(ForeignKey("liquidacion.id"), index=True)

    medico_id: Mapped[int] = mapped_column(Integer, index=True)
    obra_social_id: Mapped[int] = mapped_column(Integer, index=True)

    prestacion_id: Mapped[int] = mapped_column(
        ForeignKey("guardar_atencion.ID", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    pagado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    honorarios: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"), server_default="0.00")
    gastos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"), server_default="0.00")
    importe_total: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"), server_default="0.00")

    liquidacion: Mapped[Optional["Liquidacion"]] = relationship(back_populates="detalles")

    __table_args__ = (
        UniqueConstraint("prestacion_id", "liquidacion_id", "medico_id", name="uq_det_prest_en_liq"),
    )


class PagoMedico(AuditMixin, Base):
    """Resumen por médico para una corrida de pago (antes LiquidacionMedico)."""
    __tablename__ = "pago_medico"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pago_id: Mapped[int] = mapped_column(
        ForeignKey("pago.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    medico_id: Mapped[int] = mapped_column(
        ForeignKey("listado_medico.ID", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    bruto: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    debitos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    creditos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    reconocido: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    deducciones: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))
    neto_a_pagar: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"))

    estado: Mapped[Literal["pendiente", "liquidado", "pagado"]] = mapped_column(
        Enum("pendiente", "liquidado", "pagado", name="pagomed_estado"),
        default="pendiente",
        server_default="pendiente",
        nullable=False,
    )

    pago: Mapped[Optional["Pago"]] = relationship(back_populates="pagos_medico")

    __table_args__ = (
        UniqueConstraint("pago_id", "medico_id", name="uq_pagomed_pago_med"),
    )


class Recibo(AuditMixin, Base):
    """Recibo emitido a un médico para un pago."""
    __tablename__ = "recibo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nro_recibo: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    pago_id: Mapped[int] = mapped_column(
        ForeignKey("pago.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    medico_id: Mapped[int] = mapped_column(
        ForeignKey("listado_medico.ID", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    total_neto: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=Decimal("0"), nullable=False)
    emision_timestamp: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    estado: Mapped[Literal["emitido", "anulado", "pagado"]] = mapped_column(
        Enum("emitido", "anulado", "pagado", name="recibo_estado"),
        default="emitido",
        server_default="emitido",
        nullable=False,
    )

    pago: Mapped[Optional["Pago"]] = relationship(back_populates="recibos")

    __table_args__ = (
        UniqueConstraint("pago_id", "medico_id", name="uq_recibo_pago_med"),
        Index("idx_recibo_pago", "pago_id"),
        Index("idx_recibo_med", "medico_id"),
    )
