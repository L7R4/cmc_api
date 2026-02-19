import datetime
import decimal
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import DECIMAL, Date, Enum, ForeignKey, Index, Integer, String, UniqueConstraint, text
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


class LiquidacionResumen(AuditMixin, Base):
    __tablename__ = "liquidacion_resumen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mes: Mapped[int] = mapped_column(Integer)
    anio: Mapped[int] = mapped_column(Integer)
    liquidaciones: Mapped[list["Liquidacion"]] = relationship(
        back_populates="resumen",
        cascade="all, delete-orphan",
        passive_deletes=True,
        single_parent=True,
        order_by="(Liquidacion.obra_social_id, Liquidacion.anio_periodo, Liquidacion.mes_periodo)",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("anio", "mes", name="uq_liqres_anio_mes"),
    )


class Liquidacion(AuditMixin, Base):
    __tablename__ = "liquidacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resumen_id: Mapped[int] = mapped_column(ForeignKey("liquidacion_resumen.id"), nullable=False)

    obra_social_id: Mapped[int] = mapped_column(Integer, index=True)
    mes_periodo: Mapped[int] = mapped_column(Integer)
    anio_periodo: Mapped[int] = mapped_column(Integer)

    estado: Mapped[Literal["A","C"]] = mapped_column(
        Enum("A","C", name="liq_estado"), default="A", server_default="A", index=True
    )

    cierre_timestamp: Mapped[Optional[str]] = mapped_column(String(25), nullable=True)

    nro_factura: Mapped[Optional[str]] = mapped_column(String(30))
    refacturado_from: Mapped[Optional[int]] = mapped_column(ForeignKey("liquidacion.id"), nullable=True, index=True)

    total_bruto: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    total_debitos: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    total_neto: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)

    resumen: Mapped[Optional["LiquidacionResumen"]] = relationship(back_populates="liquidaciones")
    detalles: Mapped[list["DetalleLiquidacion"]] = relationship(back_populates="liquidacion", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("resumen_id", "obra_social_id", "mes_periodo", "anio_periodo", name="uq_liq_res_os_per_v2"),
        Index("idx_liq_res_os_per", "resumen_id", "obra_social_id", "mes_periodo", "anio_periodo"),
        Index("idx_liq_os_per_version", "obra_social_id", "anio_periodo", "mes_periodo"),
    )


class DetalleLiquidacion(AuditMixin, Base):
    __tablename__ = "detalle_liquidacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    liquidacion_id: Mapped[int] = mapped_column(ForeignKey("liquidacion.id"), index=True)

    medico_id: Mapped[int] = mapped_column(Integer, index=True)
    obra_social_id: Mapped[int] = mapped_column(Integer, index=True)
    prestacion_id: Mapped[str] = mapped_column(String(16))

    debito_credito_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("debito_credito.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    pagado: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=Decimal("0"))
    importe: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)

    liquidacion: Mapped[Optional["Liquidacion"]] = relationship(back_populates="detalles")
    debito_credito: Mapped[Optional["Debito_Credito"]] = relationship(
        back_populates="detalles_liquidacion",
        foreign_keys=[debito_credito_id],
    )
    __table_args__ = (
        UniqueConstraint("prestacion_id", "liquidacion_id", "medico_id", name="uq_det_prest_en_liq"),
        Index("idx_det_os_liq_med", "obra_social_id", "liquidacion_id", "medico_id"),
        Index("idx_det_prest", "prestacion_id"),
    )
