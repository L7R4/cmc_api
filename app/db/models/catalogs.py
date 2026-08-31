import datetime
import decimal
from typing import List, Optional

from sqlalchemy import DECIMAL, Date, Enum, ForeignKey, Index, Integer, String, TIMESTAMP, text
from sqlalchemy.dialects.mysql import INTEGER
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

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

    # Extended fields
    cuit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    direccion_real: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    condicion_iva: Mapped[Optional[str]] = mapped_column(
        Enum('responsable_inscripto', 'exento', name='condicion_iva_enum'), nullable=True
    )
    plazo_vencimiento: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Día de corte de la ventana del período: 1 = mes completo (1 al último día del
    # mes); 20 = del 20 al 20. Define a qué período pertenece una prestación según su
    # fecha_practica y el plazo operativo de carga/cierre. Default 20 (la mayoría).
    dia_corte: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("'20'")
    )
    fecha_alta_convenio: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    obra_social_principal_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey('obras_sociales.ID', ondelete='SET NULL'), nullable=True, index=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    )

    contactos: Mapped[List["ObraSocialContacto"]] = relationship(
        "ObraSocialContacto",
        back_populates="obra_social",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    direcciones: Mapped[List["ObraSocialDireccion"]] = relationship(
        "ObraSocialDireccion",
        back_populates="obra_social",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    documentos: Mapped[List["ObraSocialDocumento"]] = relationship(
        "ObraSocialDocumento",
        back_populates="obra_social",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class ObraSocialContacto(Base):
    __tablename__ = 'obras_sociales_contactos'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    obra_social_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('obras_sociales.ID', ondelete='CASCADE'), nullable=False, index=True
    )
    tipo: Mapped[str] = mapped_column(Enum('email', 'telefono', name='contacto_tipo_enum'), nullable=False)
    valor: Mapped[str] = mapped_column(String(200), nullable=False)
    etiqueta: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)

    obra_social: Mapped["ObrasSociales"] = relationship("ObrasSociales", back_populates="contactos")


class ObraSocialDireccion(Base):
    __tablename__ = 'obras_sociales_direccion'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    obra_social_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('obras_sociales.ID', ondelete='CASCADE'), nullable=False, index=True
    )
    provincia: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    localidad: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    direccion: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    codigo_postal: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    horario: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    obra_social: Mapped["ObrasSociales"] = relationship("ObrasSociales", back_populates="direcciones")


class ObraSocialDocumento(Base):
    __tablename__ = 'obras_sociales_documentos'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    obra_social_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('obras_sociales.ID', ondelete='CASCADE'), nullable=False, index=True
    )
    tipo: Mapped[str] = mapped_column(
        Enum('convenio', 'normas', 'valores_convenidos', 'otros', name='documento_tipo_enum'), nullable=False
    )
    url: Mapped[str] = mapped_column(String(300), nullable=False)
    nombre_custom: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    obra_social: Mapped["ObrasSociales"] = relationship("ObrasSociales", back_populates="documentos")


class Periodos(Base):
    # DEPRECADO: el período del colegio se modela en la cabecera `facturacion`
    # (fase `estado` A/C). No usar en código nuevo. Ver PeriodoMedicoActual para la
    # fase médico. Se conserva solo por datos legacy.
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
    # DEPRECADO: reemplazada por `periodo_medico_actual` (PeriodoMedicoActual), que es
    # el puntero global-con-override del período de médicos. No usar en código nuevo.
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

class BoletinObservacion(Base):
    __tablename__ = "boletin_observacion"

    nro_obrasocial: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    texto: Mapped[str] = mapped_column(String(400), nullable=False, server_default=text("''"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")
    )


class BoletinObservacionPlantilla(Base):
    __tablename__ = "boletin_observacion_plantilla"

    id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True, autoincrement=True)
    texto: Mapped[str] = mapped_column(String(400), nullable=False, unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class ValorPrestacion(Base):
    __tablename__ = 'valor_prestacion'
    __table_args__ = (
        Index('CODIGOS', 'CODIGOS'),
        Index('C_P_H_S', 'C_P_H_S'),
        Index('FECHA_CAMBIO', 'FECHA_CAMBIO'),
        Index('NRO_OBRASOCIAL', 'NRO_OBRASOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGOS: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'0'"))
    NRO_OBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    HONORARIOS_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    HONORARIOS_B: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    HONORARIOS_C: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_B: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_C: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    C_P_H_S: Mapped[str] = mapped_column(String(1), nullable=False, server_default=text("'C'"))
    FECHA_CAMBIO: Mapped[Optional[datetime.date]] = mapped_column(Date)
    FECHA_FINAL: Mapped[Optional[datetime.date]] = mapped_column(Date)


class ValoresEticos(Base):
    __tablename__ = "valores_eticos"

    id: Mapped[int] = mapped_column(INTEGER(11), primary_key=True, autoincrement=True)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(500))
    observaciones: Mapped[Optional[str]] = mapped_column(String(1000))
    fecha_update: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)

class ObraSocialPago(Base):
    """Una deuda de la obra social con el Colegio: fecha, monto y estado.

    Registro deliberadamente mínimo, como lo pidió el Colegio. No lleva
    concepto, período, vencimiento ni monto cobrado: lo que hace falta es saber
    cuánto se le reclamó, cuándo, si está saldado, y poder mirar la factura.

    ## El estado se guarda

    A diferencia de otros diseños posibles, `estado` es una columna real. No hay
    de dónde derivarlo: sin un "monto cobrado" contra el cual comparar el
    adeudado, el estado es un dato propio que marca quien carga la fila.

    ## La factura

    Un archivo opcional —PDF o imagen escaneada— por fila. Va al mismo
    directorio que el resto de los adjuntos de la obra social
    (`uploads/obras_sociales/<id>/`), que ya está detrás de `/api/archivos` con
    regla de autorización propia. No hace falta una sección nueva.

    Se guarda la ruta y no el binario, igual que `ObraSocialDocumento`.
    """

    __tablename__ = "obras_sociales_pagos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # El index lo crea MySQL solo al declarar la FK; pedirlo acá lo duplicaría.
    obra_social_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("obras_sociales.ID", ondelete="CASCADE"), nullable=False
    )

    fecha: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    monto: Mapped[decimal.Decimal] = mapped_column(DECIMAL(14, 2), nullable=False)
    estado: Mapped[str] = mapped_column(
        Enum("pendiente", "parcial", "pagado", name="os_pago_estado_enum"),
        nullable=False,
        server_default="pendiente",
    )

    #: Ruta relativa del adjunto, servida por `/api/archivos/...`.
    factura_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    #: El nombre con el que se subio; es lo que se muestra.
    factura_nombre: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        TIMESTAMP, server_default=func.now()
    )
    #: `ListadoMedico.ID` de quien lo cargo o modifico. Sin FK a proposito.
    actualizado_por: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    obra_social: Mapped["ObrasSociales"] = relationship("ObrasSociales")
