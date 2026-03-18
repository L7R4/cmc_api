import datetime
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import DECIMAL, Boolean, Date, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditMixin, Base


class LoteAjuste(AuditMixin, Base):
    """Agrupación de ajustes (antes debito_credito_snap) para una OS+período."""
    __tablename__ = "lote_ajuste"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identifica a qué factura (OS+período) aplican estos ajustes
    obra_social_id: Mapped[int] = mapped_column(
        ForeignKey("obras_sociales.NRO_OBRASOCIAL"),
        nullable=False,
        index=True,
    )
    mes_periodo: Mapped[int] = mapped_column(Integer, nullable=False)
    anio_periodo: Mapped[int] = mapped_column(Integer, nullable=False)

    tipo: Mapped[Literal["normal", "refacturacion"]] = mapped_column(
        Enum("normal", "refacturacion", name="lote_tipo"),
        nullable=False,
        default="normal",
        server_default="normal",
    )

    # Para refacturaciones: qué lote está siendo corregido por este
    snap_origen_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lote_ajuste.id"),
        nullable=True,
        index=True,
    )

    estado: Mapped[Literal["A", "C", "L"]] = mapped_column(
        Enum("A", "C", "L", name="lote_estado"),
        nullable=False,
        default="A",
        server_default="A",
    )

    # Se setea al "pasar a liquidaciones" — qué pago incluye este lote
    pago_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pago.id"),
        nullable=True,
        index=True,
    )

    total_debitos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=0)
    total_creditos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), default=0)

    pago: Mapped[Optional["Pago"]] = relationship(
        back_populates="lotes",
        foreign_keys=[pago_id],
    )
    origen: Mapped[Optional["LoteAjuste"]] = relationship(
        foreign_keys="[LoteAjuste.snap_origen_id]",
        primaryjoin="LoteAjuste.snap_origen_id == LoteAjuste.id",
        remote_side="LoteAjuste.id",
    )
    ajustes: Mapped[list["Ajuste"]] = relationship(
        back_populates="lote",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("snap_origen_id", name="uq_lote_origen"),
        Index("idx_lote_os_per", "obra_social_id", "mes_periodo", "anio_periodo"),
        Index("idx_lote_pago", "pago_id"),
    )


class Ajuste(AuditMixin, Base):
    """Ajuste individual dentro de un lote (antes Debito_Credito)."""
    __tablename__ = "ajuste"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    lote_id: Mapped[int] = mapped_column(
        ForeignKey("lote_ajuste.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tipo: Mapped[Literal["d", "c"]] = mapped_column(
        Enum("d", "c", name="ajuste_tipo"),
        nullable=False,
    )

    # Prestación de origen (nullable: ajustes sin prestación específica)
    id_atencion: Mapped[Optional[int]] = mapped_column(
        ForeignKey("guardar_atencion.ID", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    medico_id: Mapped[int] = mapped_column(
        ForeignKey("listado_medico.ID"),
        nullable=False,
        index=True,
    )
    obra_social_id: Mapped[int] = mapped_column(
        ForeignKey("obras_sociales.NRO_OBRASOCIAL"),
        nullable=False,
        index=True,
    )

    monto: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    observacion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 'manual' = creado por operador, 'importado' = migración legacy
    origen: Mapped[Literal["manual", "importado"]] = mapped_column(
        Enum("manual", "importado", name="ajuste_origen"),
        nullable=False,
        default="manual",
        server_default="manual",
    )

    # Para idempotencia en importaciones
    ext_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    lote: Mapped[Optional["LoteAjuste"]] = relationship(back_populates="ajustes")

    __table_args__ = (
        UniqueConstraint("ext_id", name="uq_ajuste_ext_id"),
        Index("idx_ajuste_lote", "lote_id"),
        Index("idx_ajuste_medico", "medico_id"),
    )


class Descuentos(AuditMixin, Base):
    __tablename__ = "descuentos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nro_colegio: Mapped[int] = mapped_column(Integer, nullable=False)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    precio: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    porcentaje: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=0)
    aplica_a_todos: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")


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

    # Referencia al pago (antes resumen_id)
    pago_id: Mapped[int] = mapped_column(
        ForeignKey("pago.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    descuento_id: Mapped[int | None] = mapped_column(ForeignKey("descuentos.id"), nullable=True, index=True)

    calculado_total: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    porcentaje_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    monto_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

    pagado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("pago_id", "medico_id", "descuento_id", name="uq_ded_pago_med_desc"),
        Index("idx_ded_pago_med", "pago_id", "medico_id"),
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

    # pago_id reemplaza resumen_id
    pago_id: Mapped[int] = mapped_column(
        ForeignKey("pago.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), nullable=False, index=True)

    concepto_tipo: Mapped[Literal["desc","esp"]] = mapped_column(
        Enum("desc","esp", name="ded_apl_tipo"),
        nullable=False,
        index=True,
    )
    concepto_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

    __table_args__ = (
        UniqueConstraint("pago_id", "medico_id", "concepto_tipo", "concepto_id", name="uq_dedapli_pago_med_conc"),
        Index("idx_apl_pago_med", "pago_id", "medico_id"),
        Index("idx_apl_conc", "concepto_tipo", "concepto_id"),
    )
