import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.mysql import INTEGER
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class ListadoMedico(Base):
    __tablename__ = 'listado_medico'
    __table_args__ = (
        Index('CATEGORIA', 'CATEGORIA'),
        Index('NOMBRE', 'NOMBRE'),
        Index('NRO_ESPECIALIDAD', 'NRO_ESPECIALIDAD'),
        Index('NRO_ESPECIALIDAD2', 'NRO_ESPECIALIDAD2'),
        Index('NRO_ESPECIALIDAD3', 'NRO_ESPECIALIDAD3'),
        Index('NRO_ESPECIALIDAD4', 'NRO_ESPECIALIDAD4'),
        Index('NRO_ESPECIALIDAD5', 'NRO_ESPECIALIDAD5'),
        Index('NRO_SOCIO', 'NRO_SOCIO')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_ESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD2: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD3: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD4: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD5: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_SOCIO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NOMBRE: Mapped[str] = mapped_column(String(40, 'utf8_spanish2_ci'), nullable=False, server_default=text("'a'"))
    DOMICILIO_CONSULTA: Mapped[str] = mapped_column(String(100, 'utf8_spanish2_ci'), nullable=False, server_default=text("'a'"))
    TELEFONO_CONSULTA: Mapped[str] = mapped_column(String(25, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    MATRICULA_PROV: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    MATRICULA_NAC: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    DOMICILIO_PARTICULAR: Mapped[str] = mapped_column(String(100, 'utf8_spanish2_ci'), nullable=False, server_default=text("'a'"))
    TELE_PARTICULAR: Mapped[str] = mapped_column(String(15, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    CELULAR_PARTICULAR: Mapped[str] = mapped_column(String(15, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    MAIL_PARTICULAR: Mapped[str] = mapped_column(String(50, 'utf8_spanish2_ci'), nullable=False, server_default=text("'a'"))
    SEXO: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'M'"))
    TIPO_DOC: Mapped[str] = mapped_column(String(3, 'utf8_spanish2_ci'), nullable=False, server_default=text("'DNI'"))
    DOCUMENTO: Mapped[str] = mapped_column(String(8, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    CUIT: Mapped[str] = mapped_column(String(12, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    ANSSAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    MALAPRAXIS: Mapped[str] = mapped_column(String(100, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    MONOTRIBUTISTA: Mapped[str] = mapped_column(String(2, 'utf8_spanish2_ci'), nullable=False, server_default=text("'NO'"))
    FACTURA: Mapped[str] = mapped_column(String(2, 'utf8_spanish2_ci'), nullable=False, server_default=text("'NO'"))
    COBERTURA: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    PROVINCIA: Mapped[str] = mapped_column(String(25, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    CODIGO_POSTAL: Mapped[str] = mapped_column(String(15, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    VITALICIO: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'N'"))
    OBSERVACION: Mapped[str] = mapped_column(String(200, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    CATEGORIA: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    EXISTE: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'N'"))
    NRO_ESPECIALIDAD6: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    EXCEP_DESDE: Mapped[str] = mapped_column(String(6, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    EXCEP_HASTA: Mapped[str] = mapped_column(String(6, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    EXCEP_DESDE2: Mapped[str] = mapped_column(String(6, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    EXCEP_HASTA2: Mapped[str] = mapped_column(String(6, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    EXCEP_DESDE3: Mapped[str] = mapped_column(String(6, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    EXCEP_HASTA3: Mapped[str] = mapped_column(String(6, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    INGRESAR: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'D'"), comment='D=DOCTOR / E=EMPLEADOS DEL COLEGIO / A ADMINISTRADOR')
    FECHA_RECIBIDO: Mapped[Optional[datetime.date]] = mapped_column(Date)
    FECHA_MATRICULA: Mapped[Optional[datetime.date]] = mapped_column(Date)
    FECHA_INGRESO: Mapped[Optional[datetime.date]] = mapped_column(Date)
    FECHA_NAC: Mapped[Optional[datetime.date]] = mapped_column(Date)
    VENCIMIENTO_ANSSAL: Mapped[Optional[datetime.date]] = mapped_column(Date)
    VENCIMIENTO_MALAPRAXIS: Mapped[Optional[datetime.date]] = mapped_column(Date)
    VENCIMIENTO_COBERTURA: Mapped[Optional[datetime.date]] = mapped_column(Date)
    FECHA_VITALICIO: Mapped[Optional[datetime.date]] = mapped_column(Date)

    conceps_espec: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {"conceps": [], "espec": []}
    )
    cbu: Mapped[str] = mapped_column(String(50, 'utf8_spanish2_ci'), nullable=True)
    es_organizacion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    cuenta_bancaria: Mapped[Optional[str]] = mapped_column(String(20, 'utf8_spanish2_ci'), nullable=True)
    adherente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    interior: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    nro_resolucion: Mapped[str] = mapped_column(String(70, 'utf8_spanish2_ci'), nullable=True)
    fecha_resolucion:Mapped[Optional[datetime.date]] = mapped_column(Date)
    apellido:  Mapped[str] = mapped_column(String(70, 'utf8_spanish2_ci'), nullable=True)
    nombre_:  Mapped[str] = mapped_column(String(70, 'utf8_spanish2_ci'), nullable=True)
    titulo:  Mapped[str] = mapped_column(String(70, 'utf8_spanish2_ci'), nullable=True)
    localidad:  Mapped[str] = mapped_column(String(70, 'utf8_spanish2_ci'), nullable=True)
    condicion_impositiva: Mapped[str] = mapped_column(String(70, 'utf8_spanish2_ci'), nullable=True)
    attach_titulo                 = Column(String(512), nullable=True)
    attach_matricula_nac          = Column(String(512), nullable=True)
    attach_matricula_prov         = Column(String(512), nullable=True)
    attach_resolucion             = Column(String(512), nullable=True)
    attach_habilitacion_municipal = Column(String(512), nullable=True)
    attach_cuit                   = Column(String(512), nullable=True)
    attach_condicion_impositiva   = Column(String(512), nullable=True)
    attach_anssal                 = Column(String(512), nullable=True)
    attach_malapraxis             = Column(String(512), nullable=True)
    attach_cbu                    = Column(String(512), nullable=True)
    attach_dni                    = Column(String(512), nullable=True)

    documentos = relationship("Documento", back_populates="medico", cascade="all, delete-orphan")
    hashed_password = Column(String(255), nullable=False)

    # Toda cuenta nueva nace con app.core.passwords.PASSWORD_INICIAL, que es
    # pública. El flag es lo que hace que eso no sea un agujero: el front lo lee
    # del login y de /auth/me y manda a la pantalla de cambio. Ver A2/A3.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    # Corte de sesiones (A7/A12). Todo access token emitido ANTES de esta marca
    # queda inválido, aunque no haya vencido. Se pisa con NOW() al cambiar la
    # contraseña y al tocar roles o permisos del usuario; ver app/auth/sessions.py.
    # NULL = nunca se revocó nada.
    tokens_valid_from: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True
    )


class Documento(Base):
    __tablename__ = "documentos"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    medico_id     = Column(Integer, ForeignKey("listado_medico.ID", ondelete="CASCADE"), index=True, nullable=False)

    label         = Column(String(50),  nullable=True)
    original_name = Column(String(255), nullable=False)
    filename      = Column(String(255), nullable=False)
    content_type  = Column(String(100), nullable=True)
    size          = Column(Integer, nullable=True)
    path          = Column(String(512), nullable=False)

    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    medico        = relationship("ListadoMedico", back_populates="documentos")
