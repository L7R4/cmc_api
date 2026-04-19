import datetime
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import DECIMAL, Boolean, Date, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.ext.hybrid import hybrid_property
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

    tipo: Mapped[Literal["normal", "refacturacion", "sin_factura"]] = mapped_column(
        Enum("normal", "refacturacion", "sin_factura", name="lote_tipo"),
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

    estado: Mapped[Literal["A", "C", "L", "AP"]] = mapped_column(
        Enum("A", "C", "L", "AP", name="lote_estado"),
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

    honorarios: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    gastos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    observacion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    @hybrid_property
    def total(self) -> Decimal:
        return self.honorarios + self.gastos

    @total.expression  # type: ignore[no-redef]
    @classmethod
    def total(cls):
        return cls.honorarios + cls.gastos

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

    # Quién paga este descuento; NULL = el propio médico
    pagador_medico_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listado_medico.ID"),
        nullable=True,
        index=True,
    )

    # TRUE = el médico paga en caja (no se descuenta de liquidación)
    paga_por_caja: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    __table_args__ = (
        UniqueConstraint("medico_id", "descuento_id", name="uq_socio_descuento"),
        Index("idx_med_desc", "medico_id", "descuento_id"),
    )


class Deduccion(AuditMixin, Base):
    __tablename__ = "deducciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), nullable=False, index=True)
    descuento_id: Mapped[Optional[int]] = mapped_column(ForeignKey("descuentos.id"), nullable=True, index=True)

    # Pago en el que fue generada esta deducción (bulk_generar_descuento) o
    # en el que fue enrolada automáticamente (auto_enrolar_pendientes).
    # NULL para deducciones manuales paga_por_caja=True o creadas sin pago abierto.
    # SET NULL si el pago es eliminado desde la DB (la app maneja el rollback antes).
    generado_en_pago_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pago.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Amounts — auto rows use calculado_total; manual rows set calculado_total = monto_cuota for display
    calculado_total: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    porcentaje_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    # Acumulado de pagos parciales ya cobrados
    monto_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

    origen: Mapped[Literal["manual", "automatico"]] = mapped_column(
        Enum("manual", "automatico", name="ded_origen"),
        nullable=False,
        default="automatico",
        server_default="automatico",
    )
    estado: Mapped[Literal["pendiente", "en_pago", "aplicado", "cancelado", "eliminado"]] = mapped_column(
        Enum("pendiente", "en_pago", "aplicado", "cancelado", "eliminado", name="ded_estado"),
        nullable=False,
        default="pendiente",
        server_default="pendiente",
    )

    # Manual / cuota fields — NULL for automatico rows
    monto_total: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2), nullable=True)
    monto_cuota: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2), nullable=True)
    cuotas_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 0 = automatico row; 1..N = cuota number for manual rows
    cuota_nro: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cuotificado: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    mes_aplicar: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    anio_aplicar: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pagador_medico_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listado_medico.ID"), nullable=True, index=True
    )

    # Snapshot del flag paga_por_caja del SocioDescuento al momento de generar.
    # Para manuales: lo elige el operador en la creación.
    # False = se descuenta de la liquidación electrónica; True = pago en caja (no pasa por pago).
    paga_por_caja: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    aplicaciones: Mapped[list["DeduccionAplicacion"]] = relationship(
        back_populates="deduccion",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Estado incluido en la llave para permitir que una fila 'eliminado' coexista
        # con una nueva fila activa del mismo período (ej: tras rollback de pago).
        # Solo puede haber un row por (medico, descuento, mes, anio, cuota_nro, estado).
        UniqueConstraint(
            "medico_id", "descuento_id", "mes_aplicar", "anio_aplicar", "cuota_nro", "estado",
            name="uq_ded_med_desc_per_cuota_estado",
        ),
        Index("idx_ded_origen_estado", "origen", "estado"),
        Index("idx_ded_periodo", "mes_aplicar", "anio_aplicar"),
    )


class DeduccionAplicacion(AuditMixin, Base):
    __tablename__ = "deduccion_aplicacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # NULL para deducciones paga_por_caja=True aplicadas en caja (sin pago electrónico asociado).
    pago_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pago.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    deduccion_id: Mapped[int] = mapped_column(
        ForeignKey("deducciones.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

    deduccion: Mapped[Optional["Deduccion"]] = relationship(back_populates="aplicaciones")

    __table_args__ = (
        UniqueConstraint("pago_id", "deduccion_id", name="uq_dedapli_pago_ded"),
        Index("idx_apl_pago", "pago_id"),
        Index("idx_apl_deduccion", "deduccion_id"),
    )


