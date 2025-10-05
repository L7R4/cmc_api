from typing import Literal, Optional
import datetime
import decimal

from sqlalchemy import DECIMAL, JSON, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import INTEGER, LONGTEXT, VARCHAR
from decimal import Decimal
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func

class Base(DeclarativeBase):
    pass

class AuditMixin:
    # UTC y con zona para portabilidad
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),    # Postgres: NOW(); MySQL: CURRENT_TIMESTAMP()
        nullable=False
    )
    # NO obligatorio → nullable=True
    created_by_user: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listado_medico.ID"),
        nullable=True,
        index=True
    )

class Avisos(Base):
    __tablename__ = 'avisos'
    __table_args__ = (
        Index('FECHA', 'FECHA'),
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    ARCHIVO: Mapped[str] = mapped_column(String(50, 'utf8_spanish2_ci'), nullable=False, server_default=text("'#'"))
    FECHA: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'--'"))
    EXISTE: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'S'"))
    AVISO: Mapped[Optional[str]] = mapped_column(LONGTEXT)


class Clinicas(Base):
    __tablename__ = 'clinicas'

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CLINICA: Mapped[str] = mapped_column(String(50, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))


class CodigoDescripcion(Base):
    __tablename__ = 'codigo_descripcion'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('C_P_H_S', 'C_P_H_S'),
        Index('DESCRIPCION', 'DESCRIPCION')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'0'"))
    DESCRIPCION: Mapped[str] = mapped_column(String(210), nullable=False, server_default=text("'0'"))
    C_P_H_S: Mapped[str] = mapped_column(String(1), nullable=False, server_default=text("'C'"))


class CodigoNomenclador(Base):
    __tablename__ = 'codigo_nomenclador'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('NROESPECIALIDAD', 'NROESPECIALIDAD')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    NROESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    HONORARIOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"), comment='UNIDAD DE HONORARIOS, CALCULO CON VALORES NOMCLADOS')
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"), comment='UNIDAD DE GASTOS. CALCULO CON LA TABLA VALORES NOMENCLADOS')
    CODIGOJUDICIALES: Mapped[str] = mapped_column(String(3, 'utf8_spanish2_ci'), nullable=False, server_default=text("'OTR'"))
    OBSERVACION: Mapped[str] = mapped_column(String(50, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    EXCEPCION: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'N'"), comment='S=SI / N=NO\r\nEXCEPCION ES CUANDO TOMO EL VALOR CARGADO POR GRACIELA')


class Codigoprestacionswiss(Base):
    __tablename__ = 'codigoprestacionswiss'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('C_P_H_S', 'C_P_H_S'),
        Index('DESCRIPCION', 'DESCRIPCION')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(VARCHAR(8), nullable=False, server_default=text("''"))
    DESCRIPCION: Mapped[str] = mapped_column(VARCHAR(100), nullable=False, server_default=text("'a'"))
    C_P_H_S: Mapped[str] = mapped_column(VARCHAR(1), nullable=False, server_default=text("'C'"))


class Consulta(Base):
    __tablename__ = 'consulta'
    __table_args__ = (
        Index('CONSULTAS', 'CONSULTAS'),
        Index('IDOBRASOCIAL', 'IDOBRASOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CONSULTAS: Mapped[str] = mapped_column(String(115, 'utf8_spanish2_ci'), nullable=False, server_default=text("'a'"), comment='NOMBRE DE LAS CONSULTAS')
    IDOBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='INDENTIFICADOR DE LAS OBRA SOCIALES')


class EspeCod(Base):
    __tablename__ = 'espe_cod'

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    ID_ESPE: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CODIGO: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'0'"))


class EspeCodSwiss(Base):
    __tablename__ = 'espe_cod_swiss'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('C_P_H_S', 'C_P_H_S')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(8, 'utf8_spanish2_ci'), nullable=False, server_default=text("''"))
    C_P_H_S: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'C'"))


class Especialidad(Base):
    __tablename__ = 'especialidad'
    __table_args__ = (
        Index('ESPECIALIDAD', 'ESPECIALIDAD'),
        Index('IDCOLEGIO', 'ID_COLEGIO_ESPE')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    ID_COLEGIO_ESPE: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='COLEGIO MEDICO, EL ID DE LA ESPECIALIDAD DEL COLEGIO MEDICO')
    ESPECIALIDAD: Mapped[str] = mapped_column(String(50, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='COLEGIO MEDICO')


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
    ESTADODESCRIPCION: Mapped[str] = mapped_column(String(100, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='DESCRIPCION DEL ESTADO DEL AFILIADO - campo judicial')
    MENSAJE: Mapped[str] = mapped_column(String(5, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='EN CASO QUE LA PROPIEDAD "RESULTADO" SE FALSE. ESTA PROPIEDAD CONTENDRA LA DETALLE DEL MISMO. - campo judicial')
    NOMBRE_AFILIADO: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='CAMPO COLEGIO Y judicial')
    NRO_AFILIADO: Mapped[str] = mapped_column(String(20, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='CAMPO JUDICIAL')
    BARRA_AFILIADO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_CONSULTA: Mapped[str] = mapped_column(String(16, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='campo judicial/ validacion o autorizacion de las obra sociales')
    NRO_DOCUMENTO: Mapped[str] = mapped_column(String(13, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='tambien se graba cuit swiss')
    RESULTADO: Mapped[str] = mapped_column(String(5, 'utf8_spanish_ci'), nullable=False, server_default=text("'false'"), comment='true / false - campo judicial')
    FECHASUSPENSION: Mapped[str] = mapped_column(String(10, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='campo judicial')
    NRO_OBRA_SOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='COLEGIO MEDICO')
    IMPORTE_COLEGIO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"), comment='COLEGIO MEDICO')
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    NRO_ESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CANTIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'1'"), comment='ALGUNAS OBRA SOCIAL TIENEN CANTIDAD DE LA MISMA PRESTACION DEL MISMO AFILIADO CON EL MISMO DOCTOR EN EL DIA')
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


class GuardarIoscor(Base):
    __tablename__ = 'guardar_ioscor'
    __table_args__ = (
        Index('ANIO_PERIODO', 'ANIO_PERIODO'),
        Index('FECHA_CARGA', 'FECHA_CARGA'),
        Index('MES_PERIODO', 'MES_PERIODO'),
        Index('NRO_DOCUMENTO', 'NRO_DOCUMENTO'),
        Index('NRO_ESPECIALIDAD', 'NRO_ESPECIALIDAD'),
        Index('NRO_MATRICULA', 'NRO_MATRICULA'),
        Index('NRO_SOCIO', 'NRO_SOCIO')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_SOCIO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='SOCIO DEL COLEGIO MEDICO')
    CODIGO_PRESTACION: Mapped[str] = mapped_column(String(10, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='campo judicial')
    NRO_MATRICULA: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='matricula prov. colegio medico y judicial')
    NRO_DOCUMENTO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='campo judicial')
    NRO_OBRA_SOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='COLEGIO MEDICO')
    IMPORTE_COLEGIO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"), comment='COLEGIO MEDICO')
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    NRO_ESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CANTIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'1'"), comment='ALGUNAS OBRA SOCIAL TIENEN CANTIDAD DE LA MISMA PRESTACION DEL MISMO AFILIADO CON EL MISMO DOCTOR EN EL DIA')
    EXISTE: Mapped[str] = mapped_column(String(1, 'utf8_spanish_ci'), nullable=False, server_default=text("'S'"), comment='N=ELIMINADO / S=EXISTE')
    MES_PERIODO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    ANIO_PERIODO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CANT_TRATAMIENTO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    AYUDANTE_ACTUAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    FECHA_CARGA: Mapped[Optional[datetime.date]] = mapped_column(Date)


class GuardarRefacturacion(Base):
    __tablename__ = 'guardar_refacturacion'
    __table_args__ = (
        Index('ANIO_PERIODO', 'ANIO_PERIODO'),
        Index('CATEGORIA_A_B_C', 'CATEGORIA_A_B_C'),
        Index('CODIGO_PRESTACION', 'CODIGO_PRESTACION'),
        Index('CON_HONO_SANA', 'CON_HONO_SANA'),
        Index('FECHA_CARGA', 'FECHA_CARGA'),
        Index('FECHA_PRESTACION', 'FECHA_PRESTACION'),
        Index('MES_PERIODO', 'MES_PERIODO'),
        Index('NOMBRE_AFILIADO', 'NOMBRE_AFILIADO'),
        Index('NOMBRE_AYUDANTE', 'NOMBRE_AYUDANTE'),
        Index('NOMBRE_PRESTADOR', 'NOMBRE_PRESTADOR'),
        Index('NRO_DOCUMENTO', 'NRO_DOCUMENTO'),
        Index('NRO_ESPECIALIDAD', 'NRO_ESPECIALIDAD'),
        Index('NRO_MATRICULA', 'NRO_MATRICULA'),
        Index('NRO_SOCIO', 'NRO_SOCIO'),
        Index('SANATORIO', 'SANATORIO')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_SOCIO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='SOCIO DEL COLEGIO MEDICO')
    CODIGO_PRESTACION: Mapped[str] = mapped_column(String(6, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='campo judicial')
    NRO_MATRICULA: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='matricula prov. colegio medico y judicial')
    NOMBRE_PRESTADOR: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='campo colegio medico y judicial')
    ESTADODESCRIPCION: Mapped[str] = mapped_column(String(100, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='DESCRIPCION DEL ESTADO DEL AFILIADO - campo judicial')
    MENSAJE: Mapped[str] = mapped_column(String(100, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='EN CASO QUE LA PROPIEDAD "RESULTADO" SE FALSE. ESTA PROPIEDAD CONTENDRA LA DETALLE DEL MISMO. - campo judicial')
    NOMBRE_AFILIADO: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='CAMPO COLEGIO Y judicial')
    NRO_AFILIADO: Mapped[str] = mapped_column(String(15, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='CAMPO JUDICIAL')
    BARRA_AFILIADO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_CONSULTA: Mapped[str] = mapped_column(String(16, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='campo judicial/ validacion o autorizacion de las obra sociales')
    NRO_DOCUMENTO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='campo judicial')
    RESULTADO: Mapped[str] = mapped_column(String(5, 'utf8_spanish_ci'), nullable=False, server_default=text("'false'"), comment='true / false - campo judicial')
    FECHASUSPENSION: Mapped[str] = mapped_column(String(10, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='campo judicial')
    NRO_OBRA_SOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='COLEGIO MEDICO')
    IMPORTE_COLEGIO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"), comment='COLEGIO MEDICO')
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    NRO_ESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CANTIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'1'"), comment='ALGUNAS OBRA SOCIAL TIENEN CANTIDAD DE LA MISMA PRESTACION DEL MISMO AFILIADO CON EL MISMO DOCTOR EN EL DIA')
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
    CODIGO_PRESTACION_2: Mapped[str] = mapped_column(String(6, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"))
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
    FECHA_PRESTACION: Mapped[Optional[datetime.date]] = mapped_column(Date, comment='COLEGIO MEDICO')
    FECHA_CARGA: Mapped[Optional[datetime.date]] = mapped_column(Date)
    FECHA_CIRUGIA: Mapped[Optional[datetime.date]] = mapped_column(Date)


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
    EXISTE: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'S'"))
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
        default=lambda: {"conceps": [], "espec": []}  # ← default client-side
    )


class MedicoObraSocial(Base):
    __tablename__ = 'medico_obra_social'
    __table_args__ = (
        Index('CATEGORIA', 'CATEGORIA'),
        Index('ESPECIALIDAD', 'ESPECIALIDAD'),
        Index('NOMBRE', 'NOMBRE'),
        Index('NRO_OBRASOCIAL', 'NRO_OBRASOCIAL'),
        Index('NRO_SOCIO', 'NRO_SOCIO')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_SOCIO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NOMBRE: Mapped[str] = mapped_column(String(40, 'utf8_spanish2_ci'), nullable=False, server_default=text("'a'"))
    MATRICULA_PROV: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    MATRICULA_NAC: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_OBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CATEGORIA: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    ESPECIALIDAD: Mapped[str] = mapped_column(String(50, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    TELEFONO_CONSULTA: Mapped[str] = mapped_column(String(25, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    MARCA: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'N'"))


class Nomenclador(Base):
    __tablename__ = 'nomenclador'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('DESCRIPCION', 'DESCRIPCION')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    DESCRIPCION: Mapped[str] = mapped_column(String(300, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    CODIGOJUDICIALES: Mapped[str] = mapped_column(String(3, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))


class NomencladorIoscor(Base):
    __tablename__ = 'nomenclador_ioscor'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('DETALLE', 'DETALLE')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(11, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    DETALLE: Mapped[str] = mapped_column(VARCHAR(200), nullable=False, server_default=text("'A'"))
    HONORARIOS_ANTERIOR: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_ANTERIOR: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_ANTERIOR: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    HONORARIOS_ACTUAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_ACTUAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS_ACTUAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    PORCEN_HONORARIOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    PORCEN_AYUDANTE: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    PORCEN_GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))


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


class Paciente(Base):
    __tablename__ = 'paciente'
    __table_args__ = (
        Index('NOMBRE', 'NOMBRE'),
        Index('NRO_AFILIADO', 'NRO_AFILIADO'),
        Index('NRO_DOCUMENTO', 'NRO_DOCUMENTO')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NOMBRE: Mapped[str] = mapped_column(VARCHAR(40), nullable=False, server_default=text("'A'"))
    NRO_AFILIADO: Mapped[str] = mapped_column(VARCHAR(15), nullable=False, server_default=text("'0'"))
    NRO_DOCUMENTO: Mapped[str] = mapped_column(VARCHAR(13), nullable=False, server_default=text("'0'"))


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


class UnidadNomenclador(Base):
    __tablename__ = 'unidad_nomenclador'

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGOS: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'0'"))
    CIRUJANO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    ANESTESISTA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    OPERATORIO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CANTIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    ANESTESIA: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    INSTRUMENTO: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))


class UnidadNomenclador10(Base):
    __tablename__ = 'unidad_nomenclador_10'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('NIVEL', 'NIVEL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'0'"))
    NIVEL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    UQ: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    AYUDANTES: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))


class UnidadNomenclador7(Base):
    __tablename__ = 'unidad_nomenclador_7'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'0'"))
    UNIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))


class UnidadNomencladorInf(Base):
    __tablename__ = 'unidad_nomenclador_inf'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(VARCHAR(4), nullable=False, server_default=text("'0'"))
    NIVEL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))


class UsuarioColegio(Base):
    __tablename__ = 'usuario_colegio'

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    _10: Mapped[str] = mapped_column('10', String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"))
    CLAVE: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    ADMINISTRA: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'N'"), comment='VA A LUGARES DETERMINADOS T=TODOS. A=AUTORIA/ R=REFACTURACION. ETC')
    INGRESAR: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'1'"))


class ValidarUsuario(Base):
    __tablename__ = 'validar_usuario'
    __table_args__ = (
        Index('FECHA', 'FECHA'),
        Index('IDOBRASOCIAL', 'IDOBRASOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    REQUESTID: Mapped[str] = mapped_column(String(40, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"), comment='campo judicial')
    TOKEN: Mapped[str] = mapped_column(String(535, 'utf8_spanish2_ci'), nullable=False, server_default=text("'A'"), comment='campo judicial')
    IDOBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='campo judicial')
    FECHA: Mapped[Optional[datetime.date]] = mapped_column(Date)


class ValorFijo(Base):
    __tablename__ = 'valor_fijo'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
        Index('NROESPECIALIDAD', 'NRO_ESPECIALIDAD'),
        Index('NRO_OBRA_SOCIAL', 'NRO_OBRA_SOCIAL')
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_OBRA_SOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CODIGO: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
    CATEGORIA_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CATEGORIA_B: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CATEGORIA_C: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_B: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_C: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    NRO_ESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    FECHA_CAMBIO: Mapped[str] = mapped_column(String(10, 'utf8_spanish2_ci'), nullable=False, server_default=text("'-'"))


class ValorNomencladoFijo(Base):
    __tablename__ = 'valor_nomenclado_fijo'

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    NRO_OBRASOCIAL: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    CODIGO: Mapped[str] = mapped_column(String(8, 'utf8_spanish2_ci'), nullable=False, server_default=text("'0'"))
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
    CONSULTA_ESPECIAL: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    CATEGORIA_A: Mapped[str] = mapped_column(VARCHAR(1), nullable=False, server_default=text("'A'"))
    CATEGORIA_B: Mapped[str] = mapped_column(VARCHAR(1), nullable=False, server_default=text("'B'"))
    CATEGORIA_C: Mapped[str] = mapped_column(VARCHAR(1), nullable=False, server_default=text("'C'"))
    HONORARIOS_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    HONORARIOS_B: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    HONORARIOS_C: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_B: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_C: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    NOMENCLADO: Mapped[str] = mapped_column(VARCHAR(1), nullable=False, server_default=text("'N'"))
    C_P_H_S: Mapped[str] = mapped_column(String(1, 'utf8_spanish2_ci'), nullable=False, server_default=text("'C'"))
    NRO_ESPECIALIDAD: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD2: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD3: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD4: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_ESPECIALIDAD5: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))


class ValorNomencladoSwiss(Base):
    __tablename__ = 'valor_nomenclado_swiss'

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    CODIGO: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'0'"))
    HONORARIOS_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    GASTOS: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    AYUDANTE_A: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False, server_default=text("'0.00'"))
    C_P_H_S: Mapped[Optional[str]] = mapped_column(String(1))


class ValorNomencladorNacional(Base):
    __tablename__ = 'valor_nomenclador_nacional'
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
    C_P_H_S: Mapped[str] = mapped_column(String(1), nullable=False, server_default=text("'P'"))
    FECHA_CAMBIO: Mapped[Optional[datetime.date]] = mapped_column(Date)


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


class ValorPrestacion10(Base):
    __tablename__ = 'valor_prestacion_10'
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


class ValorPrestacion7(Base):
    __tablename__ = 'valor_prestacion_7'
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


class ValorPrestacionInf(Base):
    __tablename__ = 'valor_prestacion_inf'
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


class LiquidacionResumen(AuditMixin,Base):
    __tablename__ = "liquidacion_resumen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mes: Mapped[int] = mapped_column(Integer)                 # 1..12
    anio: Mapped[int] = mapped_column(Integer)                # 1900..3000
    # nros_liquidacion: Mapped[Optional[str]] = mapped_column(Text)  # CSV o JSON de números/facturas
    total_bruto: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    total_debitos: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    total_deduccion: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    # estado: Mapped[Literal["a","c","e"]] = mapped_column(Enum("a","c","e", name="liqres_estado"), default="a")
    # cierre_timestamp: Mapped[Optional[str]] = mapped_column(String(25))  # o DateTime si preferís

    liquidaciones: Mapped[list["Liquidacion"]] = relationship(
        back_populates="resumen",
        cascade="all, delete-orphan",    # borra hijas al borrar del collection
        passive_deletes=True,            # respeta ondelete="CASCADE" en DB
        single_parent=True,              # requerido para delete-orphan “fuerte”
        order_by="(Liquidacion.obra_social_id, Liquidacion.anio_periodo, Liquidacion.mes_periodo)",  # ordena las hijas
        lazy="selectin",
    )
    
    __table_args__ = (
    UniqueConstraint("anio", "mes", name="uq_liqres_anio_mes"),
)

class Liquidacion(AuditMixin,Base):
    __tablename__ = "liquidacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resumen_id: Mapped[int] = mapped_column(ForeignKey("liquidacion_resumen.id"), nullable=False)

    obra_social_id: Mapped[int] = mapped_column(Integer, index=True)
    mes_periodo: Mapped[int] = mapped_column(Integer)
    anio_periodo: Mapped[int] = mapped_column(Integer)

    # NUEVO: versión de reliquidación (0 = primera)
    version: Mapped[int] = mapped_column(Integer, default=0)
    
    # NUEVO: estado A/C + timestamp opcional
    estado: Mapped[Literal["A","C"]] = mapped_column(
        Enum("A","C", name="liq_estado"), default="A", server_default="A", index=True
    )
    cierre_timestamp: Mapped[Optional[str]] = mapped_column(String(25), nullable=True)

    
    nro_liquidacion: Mapped[Optional[str]] = mapped_column(String(30))

    total_bruto: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    total_debitos: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    total_neto: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)

    resumen: Mapped[Optional["LiquidacionResumen"]] = relationship(back_populates="liquidaciones")
    detalles: Mapped[list["DetalleLiquidacion"]] = relationship(back_populates="liquidacion", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint("resumen_id", "obra_social_id", "mes_periodo", "anio_periodo","version", name="uq_liq_res_os_per_v2"),
        Index("idx_liq_res_os_per", "resumen_id", "obra_social_id", "mes_periodo", "anio_periodo"),
        Index("idx_liq_os_per_version", "obra_social_id", "anio_periodo", "mes_periodo", "version"),
    )


class DetalleLiquidacion(AuditMixin,Base):
    __tablename__ = "detalle_liquidacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    liquidacion_id: Mapped[int] = mapped_column(ForeignKey("liquidacion.id"), index=True)

    medico_id: Mapped[int] = mapped_column(Integer, index=True)
    obra_social_id: Mapped[int] = mapped_column(Integer, index=True)
    prestacion_id: Mapped[str] = mapped_column(String(16))

    # NUEVO: encadenamiento con el detalle anterior de la misma prestación (si existió)
    prev_detalle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("detalle_liquidacion.id"), nullable=True)
    prev_detalle: Mapped[Optional["DetalleLiquidacion"]] = relationship(remote_side="DetalleLiquidacion.id")

    # vínculo opcional a un débito/crédito aplicado a ESTA fila (en esta liq/reliq)
    debito_credito_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("debito_credito.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    # cuánto se paga en ESTA liquidación/reliquidación por esta prestación
    pagado: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=Decimal("0"))

    # renombraste a importe (perfecto)
    importe: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)

    liquidacion: Mapped["Liquidacion"] = relationship(back_populates="detalles")
    debito_credito: Mapped["Debito_Credito"] = relationship(back_populates="detalles_liquidacion")

    __table_args__ = (
        # Ahora se puede reliquidar la misma prestación en otra liquidación, pero NO duplicarla dentro de la misma
        UniqueConstraint("prestacion_id", "liquidacion_id", "medico_id", name="uq_det_prest_en_liq"),
        Index("idx_det_os_liq_med", "obra_social_id", "liquidacion_id", "medico_id"),
        Index("idx_det_prest", "prestacion_id"),
    )

class Debito_Credito(AuditMixin,Base):
    __tablename__ = "debito_credito"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo: Mapped[Literal["d","c"]] = mapped_column(Enum("d","c", name="debcre_tipo"))  # d=debito, c=crédito
    id_atencion: Mapped[int] = mapped_column(ForeignKey("guardar_atencion.ID", ondelete="CASCADE") ,index=True)
    obra_social_id: Mapped[int] = mapped_column(ForeignKey("obras_sociales.NRO_OBRASOCIAL"), index=True)
    observacion: Mapped[str] = mapped_column(String(255), nullable=True)
    monto: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=0)
    periodo : Mapped[str] = mapped_column(String(7), index=True) 
    detalles_liquidacion: Mapped[list["DetalleLiquidacion"]] = relationship(back_populates="debito_credito", passive_deletes=True)


class Descuentos(AuditMixin,Base):
    __tablename__ = "descuentos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nro_colegio : Mapped[int] = mapped_column(Integer, nullable= False)
    nombre: Mapped[str] = mapped_column(String(200), nullable= False)
    precio: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default = 0)
    porcentaje: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default = 0)

class DeduccionColegio(AuditMixin,Base):
    __tablename__ = "deducciones_colegio"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), index=True, nullable=False)
    resumen_id: Mapped[int] = mapped_column(ForeignKey("liquidacion_resumen.id", ondelete="CASCADE"), index=True, nullable=False)

    # uno u otro:
    descuento_id: Mapped[Optional[int]] = mapped_column(ForeignKey("descuentos.id"), nullable=True, index=True)
    especialidad_id: Mapped[Optional[int]] = mapped_column(ForeignKey("especialidad.ID"), nullable=True, index=True)

    # snapshot de lo aplicado
    monto_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=Decimal("0.00"))
    porcentaje_aplicado: Mapped[Decimal] = mapped_column(DECIMAL(10,2), default=Decimal("0.00"))

    # Cargo calculado ese mes (monto + %*base_mes_del_medico)
    calculado_total: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=Decimal("0.00"))

    # Uniques por tipo (MySQL permite múltiples NULL; por eso 2 uniques separados)
    __table_args__ = (
        UniqueConstraint("medico_id", "resumen_id", "descuento_id", name="uq_med_res_desc"),
        UniqueConstraint("medico_id", "resumen_id", "especialidad_id", name="uq_med_res_esp2"),
        Index("idx_med_res", "medico_id", "resumen_id"),
    )


class DeduccionSaldo(AuditMixin,Base):
    __tablename__ = "deduccion_saldo"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), index=True, nullable=False)
    concepto_tipo: Mapped[Literal["desc","esp"]] = mapped_column(Enum("desc","esp", name="ded_saldo_tipo"), index=True)
    concepto_id: Mapped[int] = mapped_column(Integer, index=True)  # id de descuentos.id o especialidad.ID

    saldo: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=Decimal("0.00"))

    __table_args__ = (
        UniqueConstraint("medico_id", "concepto_tipo", "concepto_id", name="uq_saldo_med_concepto"),
    )


class DeduccionAplicacion(AuditMixin,Base):
    __tablename__ = "deduccion_aplicacion"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    resumen_id: Mapped[int] = mapped_column(ForeignKey("liquidacion_resumen.id", ondelete="CASCADE"), index=True, nullable=False)
    medico_id: Mapped[int] = mapped_column(ForeignKey("listado_medico.ID"), index=True, nullable=False)

    concepto_tipo: Mapped[Literal["desc","esp"]] = mapped_column(Enum("desc","esp", name="ded_apl_tipo"), index=True)
    concepto_id: Mapped[int] = mapped_column(Integer, index=True)

    aplicado: Mapped[Decimal] = mapped_column(DECIMAL(14,2), default=Decimal("0.00"))

    __table_args__ = (
        UniqueConstraint("resumen_id", "medico_id", "concepto_tipo", "concepto_id", name="uq_apl_res_med_concepto"),
    )