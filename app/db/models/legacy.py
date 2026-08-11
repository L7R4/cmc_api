import datetime
import decimal
from typing import Optional

from sqlalchemy import DECIMAL, Date, Index, String, text
from sqlalchemy.dialects.mysql import INTEGER, LONGTEXT, VARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


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
    # El modelo declaraba `C_P_H_S`, que NO existe en la tabla real (rompía todo
    # SELECT con error 1054), y le faltaba `ID_ESPE`. Las columnas reales son las
    # tres de abajo.
    __tablename__ = 'espe_cod_swiss'
    __table_args__ = (
        Index('CODIGO', 'CODIGO'),
    )

    ID: Mapped[int] = mapped_column(INTEGER(11), primary_key=True)
    ID_ESPE: Mapped[int] = mapped_column(INTEGER(11), nullable=False)
    CODIGO: Mapped[str] = mapped_column(String(8, 'utf8_spanish2_ci'), nullable=False, server_default=text("''"))


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
    MENSAJE: Mapped[str] = mapped_column(String(100, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"))
    NOMBRE_AFILIADO: Mapped[str] = mapped_column(String(40, 'utf8_spanish_ci'), nullable=False, server_default=text("'A'"), comment='CAMPO COLEGIO Y judicial')
    NRO_AFILIADO: Mapped[str] = mapped_column(String(15, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"), comment='CAMPO JUDICIAL')
    BARRA_AFILIADO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"))
    NRO_CONSULTA: Mapped[str] = mapped_column(String(16, 'utf8_spanish_ci'), nullable=False, server_default=text("'0'"))
    NRO_DOCUMENTO: Mapped[int] = mapped_column(INTEGER(11), nullable=False, server_default=text("'0'"), comment='campo judicial')
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
