import datetime
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import DECIMAL, Boolean, Date, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditMixin, Base


class Debito_Credito(AuditMixin, Base):
    __tablename__ = "debito_credito"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo: Mapped[Literal["d","c"]] = mapped_column(Enum("d","c", name="debcre_tipo"))
    id_atencion: Mapped[int] = mapped_column(ForeignKey("guardar_atencion.ID", ondelete="CASCADE"), index=True)
    obra_social_id: Mapped[int] = mapped_column(ForeignKey("obras_sociales.NRO_OBRASOCIAL"), index=True)
    observacion: Mapped[str] = mapped_column(String(255), nullable=True)
    monto: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    periodo: Mapped[str] = mapped_column(String(7), index=True)
    detalles_liquidacion: Mapped[list["DetalleLiquidacion"]] = relationship(back_populates="debito_credito", passive_deletes=True)


class Descuentos(AuditMixin, Base):
    __tablename__ = "descuentos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nro_colegio: Mapped[int] = mapped_column(Integer, nullable=False)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    precio: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    porcentaje: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=0)


class SocioDescuento(AuditMixin, Base):
    __tablename__ = "socio_descuento"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), index=True, nullable=False)
    descuento_id: Mapped[int] = mapped_column(ForeignKey("descuentos.id"), index=True, nullable=False)

    fecha_alta: Mapped[datetime.date] = mapped_column(Date, default=datetime.date.today, nullable=True)
    fecha_baja: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)

    __table_args__ = (
        UniqueConstraint("medico_id", "descuento_id", name="uq_socio_descuento"),
        Index("idx_med_desc", "medico_id", "descuento_id"),
    )


class Deduccion(AuditMixin, Base):
    __tablename__ = "deducciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), nullable=False, index=True)
    anio: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mes: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    descuento_id: Mapped[int | None] = mapped_column(ForeignKey("descuentos.id"), nullable=True, index=True)

    calculado_total: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    porcentaje_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    monto_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

    pagado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("medico_id", "anio", "mes", "descuento_id", name="uq_ded_med_per_desc"),
        UniqueConstraint("medico_id", "descuento_id", "anio", "mes", name="uq_deducc_med_desc_period"),
        Index("idx_ded_med_per", "medico_id", "anio", "mes"),
    )


class DeduccionSaldo(AuditMixin, Base):
    __tablename__ = "deduccion_saldo"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), index=True, nullable=False)
    concepto_tipo: Mapped[Literal["desc","esp"]] = mapped_column(Enum("desc","esp", name="ded_saldo_tipo"), index=True)
    concepto_id: Mapped[int] = mapped_column(Integer, index=True)

    saldo: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=Decimal("0.00"))

    __table_args__ = (
        UniqueConstraint("medico_id", "concepto_tipo", "concepto_id", name="uq_saldo_med_concepto"),
    )


class DeduccionAplicacion(AuditMixin, Base):
    __tablename__ = "deduccion_aplicacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    anio: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    mes: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), nullable=False, index=True)

    descuento_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

    __table_args__ = (
        UniqueConstraint("anio", "mes", "medico_id", "descuento_id", name="uq_dedapli_med_desc_period"),
        Index("idx_apl_per_med", "anio", "mes", "medico_id"),
    )
