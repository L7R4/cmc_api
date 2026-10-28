import datetime
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import DECIMAL, BigInteger, Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, SmallInteger, String, UniqueConstraint, func
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

    # Factura de la que cuelga el lote: facturacion.id_prestaciones (sin FK, tabla de CMC).
    # Una OS+período puede tener la original y complementarias; NULL en los lotes
    # anteriores a esta columna y en los sin_factura.
    factura_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

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
        # uq_lote_origen (UniqueConstraint sobre snap_origen_id) se sacó — limitaba
        # a una sola refacturación por lote origen (ver diagnóstico M6).
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

    # En sistema CMC: almacena id_detalle_prestaciones de detalle_facturacion (nullable)
    id_atencion: Mapped[Optional[int]] = mapped_column(
        Integer,
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


class Conceptos(AuditMixin, Base):
    __tablename__ = "conceptos"
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
    descuento_id: Mapped[int] = mapped_column(ForeignKey("conceptos.id"), index=True, nullable=False)

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
    descuento_id: Mapped[Optional[int]] = mapped_column(ForeignKey("conceptos.id"), nullable=True, index=True)

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
    # Ledger real: acumulado de lo efectivamente cobrado (DeduccionAplicacion).
    # Solo lo tocan _persistir_aplicaciones (cierre), pagar_deduccion y
    # cambiar_estado_item (cobro en caja) — nunca la preview.
    monto_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    # Preview descartable: "cuánto se cobraría si el pago abierto cerrara
    # ahora". La recalcula _recalcular_montos_aplicados_en_pago en cada
    # refresco del pago y no representa cobro real hasta el cierre.
    monto_aplicado_preview: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

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


class LiquidacionAdelanto(AuditMixin, Base):
    """Adelanto: prestaciones de UNA factura cerrada que se pagan antes que el resto.

    Funciona como un lote de débitos y créditos: se arma (A), se cierra (C), se pasa al pago
    abierto (L, asigna `pago_id`) y queda aplicado al cerrar el pago (AP). No crea
    `liquidacion` ni `detalle_liquidacion`: sus importes suman a la producción del médico
    en el pago por `pago_id`, como los ajustes.
    """
    __tablename__ = "liquidacion_adelanto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # facturacion.id_prestaciones (sin FK: tabla de CMC). Original o complementaria.
    factura_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Copiados de la factura para filtrar y mostrar sin cruzar tablas.
    obra_social_id: Mapped[int] = mapped_column(
        ForeignKey("obras_sociales.NRO_OBRASOCIAL"), nullable=False, index=True
    )
    mes_periodo: Mapped[int] = mapped_column(Integer, nullable=False)
    anio_periodo: Mapped[int] = mapped_column(Integer, nullable=False)

    estado: Mapped[Literal["A", "C", "L", "AP"]] = mapped_column(
        Enum("A", "C", "L", "AP", name="adelanto_estado"),
        nullable=False,
        default="A",
        server_default="A",
    )
    # Pago al que se pasó; NULL en A y C.
    pago_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pago.id"), nullable=True, index=True
    )
    observacion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    items: Mapped[list["LiquidacionAdelantoItem"]] = relationship(
        back_populates="adelanto",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_adel_os_per", "obra_social_id", "anio_periodo", "mes_periodo"),
        Index("idx_adel_estado", "estado"),
    )


class LiquidacionAdelantoItem(AuditMixin, Base):
    """Una prestación (fila de detalle_facturacion) dentro de un adelanto."""
    __tablename__ = "liquidacion_adelanto_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adelanto_id: Mapped[int] = mapped_column(
        ForeignKey("liquidacion_adelanto.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # detalle_facturacion.id_detalle_prestaciones. ÚNICO: una prestación no puede estar en
    # dos adelantos. Sin FK: tabla compartida con CMC.
    detalle_facturacion_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Quien cobra esa fila (médico, ayudante o gastos): listado_medico.ID
    medico_id: Mapped[int] = mapped_column(
        ForeignKey("listado_medico.ID"), nullable=False, index=True
    )
    # Mismo reparto que `detalle_liquidacion` (según la función, × cantidad × sesión).
    honorarios: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    gastos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")
    importe_total: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, default=Decimal("0.00"), server_default="0.00")

    adelanto: Mapped["LiquidacionAdelanto"] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("detalle_facturacion_id", name="uq_adel_item_detalle"),
    )


class Compensacion(AuditMixin, Base):
    """Movimiento de plata a favor (+) o en contra (−) de un socio, sin un concepto del catálogo:
    un regalo de cumpleaños, un reintegro, una rotura. Puede ir en cuotas: una fila por cuota.

    No es producción: no entra en las bases de los porcentuales. Suma (o resta) al reconocido
    del socio en el pago (`pago_id`, estado en_pago/pagada), como un crédito o un débito.
    También puede pagarse en caja, fuera de cualquier pago (`en_caja`).
    Una cuota con mes entra sola al pago abierto cuando su mes llegó (ver
    services/compensacion_update.enrolar_cuotas_del_mes).
    """
    __tablename__ = "compensacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Cualquier fila del padrón (médico, adherente u organización): listado_medico.ID
    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), nullable=False, index=True)
    # En cuotas: cada cuota es una fila; las de un plan comparten plan_id (el id de la cuota 1).
    plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    cuota_nro: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    cuotas_total: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    # Con signo: positivo a favor del socio, negativo en contra. Nunca cero. Es el de esta cuota;
    # importe_total, el de todo el plan.
    importe: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    importe_total: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2), nullable=True)
    # Lo que dice el recibo (la ve el socio) y una nota solo para el Colegio.
    leyenda: Mapped[str] = mapped_column(String(160), nullable=False)
    nota_interna: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    estado: Mapped[Literal["pendiente", "en_pago", "pagada", "en_caja", "anulada"]] = mapped_column(
        Enum("pendiente", "en_pago", "pagada", "en_caja", "anulada", name="compensacion_estado"),
        nullable=False,
        default="pendiente",
        server_default="pendiente",
    )
    # Mes desde el que la cuota entra sola al pago abierto. NULL (las anteriores a las cuotas):
    # entra a mano.
    mes_aplicar: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    anio_aplicar: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    pago_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pago.id"), nullable=True, index=True)
    fecha_caja: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    motivo_anulacion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("idx_comp_estado", "estado"),
    )


class RetencionGananciasEscala(Base):
    """Escala de la retención de Ganancias de un tipo de socio, vigente desde una fecha.

    Los números los fija ARCA y cambian: cuando cambian se carga una escala nueva con su
    `vigente_desde` y la vieja queda para los pagos anteriores. Cada pago usa la vigente al
    primer día de su mes. Ver app/modules/retenciones/calculo.py.
    """
    __tablename__ = "retencion_ganancias_escala"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo_sujeto: Mapped[Literal["persona", "clinica", "juridica"]] = mapped_column(
        Enum("persona", "clinica", "juridica", name="retencion_tipo_sujeto"), nullable=False
    )
    vigente_desde: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    minimo_no_imponible: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    # Si la retención del mes no llega a esto, no se retiene nada.
    retencion_minima: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    observacion: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    tramos: Mapped[list["RetencionGananciasTramo"]] = relationship(
        back_populates="escala", cascade="all, delete-orphan", order_by="RetencionGananciasTramo.desde"
    )

    __table_args__ = (
        UniqueConstraint("tipo_sujeto", "vigente_desde", name="uq_ret_gan_escala_tipo_vigencia"),
    )


class RetencionGananciasTramo(Base):
    """Tramo sobre el excedente del mínimo no imponible: retención = fijo + (excedente − desde) × %."""
    __tablename__ = "retencion_ganancias_tramo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    escala_id: Mapped[int] = mapped_column(
        ForeignKey("retencion_ganancias_escala.id", ondelete="CASCADE"), nullable=False, index=True
    )
    desde: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    hasta: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(14, 2), nullable=True)  # NULL = sin tope
    importe_fijo: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    porcentaje: Mapped[Decimal] = mapped_column(DECIMAL(6, 3), nullable=False)

    escala: Mapped["RetencionGananciasEscala"] = relationship(back_populates="tramos")

    __table_args__ = (
        UniqueConstraint("escala_id", "desde", name="uq_ret_gan_tramo_desde"),
    )


class RetencionGanancias(Base):
    """La cuenta de la retención de Ganancias de un socio en un pago (una fila por pago y socio).

    Es lo que el sistema anterior guardaba en `afip_deducciones`: la base y lo retenido de cada
    pago, para que el próximo pago del mismo mes acumule (el mínimo y los tramos son mensuales),
    y de donde salen el listado mensual y los comprobantes. Mientras el pago está abierto se
    recalcula; al cerrarse queda fija. Lo retenido de verdad es la deducción (`deduccion_id`).
    """
    __tablename__ = "retencion_ganancias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pago_id: Mapped[int] = mapped_column(ForeignKey("pago.id", ondelete="CASCADE"), nullable=False)
    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), nullable=False, index=True)
    # Mes del pago: es el mes en que se acumula y el de la declaración jurada.
    mes: Mapped[int] = mapped_column(Integer, nullable=False)
    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo_sujeto: Mapped[Literal["persona", "clinica", "juridica"]] = mapped_column(
        Enum("persona", "clinica", "juridica", name="retencion_tipo_sujeto"), nullable=False
    )
    escala_id: Mapped[int] = mapped_column(ForeignKey("retencion_ganancias_escala.id"), nullable=False)
    # Base de este pago: lo producido (facturas + adelantos) menos débitos más créditos de los
    # lotes. Las compensaciones no cuentan (no son producción).
    honorarios: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    gastos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    base_pago: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    # La cuenta: base de todo el mes, retención del mes, lo ya retenido en otros pagos cerrados
    # del mes y lo que toca en este.
    base_mes: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    retencion_mes: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    retenido_otros_pagos: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    retencion_pago: Mapped[Decimal] = mapped_column(DECIMAL(14, 2), nullable=False, server_default="0.00")
    deduccion_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("deducciones.id", ondelete="SET NULL"), nullable=True
    )
    actualizado_en: Mapped[datetime.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("pago_id", "medico_id", name="uq_ret_gan_pago_medico"),
        Index("idx_ret_gan_periodo", "anio", "mes"),
    )
