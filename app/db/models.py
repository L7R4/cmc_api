from sqlalchemy import Column, Integer, String, Date
from .database import Base
from sqlalchemy import (
    Column,
    BigInteger,
    SmallInteger,
    Integer,
    TIMESTAMP,
    String,
    CHAR,
    Date,
    Numeric,
    ForeignKey,
    func
)
from sqlalchemy.orm import relationship

class Medico(Base):
    __tablename__ = "listado_medico"
    id                    = Column('ID', Integer, primary_key=True, autoincrement=True)
    nro_especialidad      = Column('NRO_ESPECIALIDAD', Integer, nullable=False, index=True, default=0)
    nro_especialidad2     = Column('NRO_ESPECIALIDAD2', Integer, nullable=False, index=True, default=0)
    nro_especialidad3     = Column('NRO_ESPECIALIDAD3', Integer, nullable=False, index=True, default=0)
    nro_especialidad4     = Column('NRO_ESPECIALIDAD4', Integer, nullable=False, index=True, default=0)
    nro_especialidad5     = Column('NRO_ESPECIALIDAD5', Integer, nullable=False, index=True, default=0)
    nro_socio             = Column('NRO_SOCIO', Integer, nullable=False, index=True, default=0)
    nombre                = Column('NOMBRE', String(40), nullable=False, default="a")
    domicilio_consulta    = Column('DOMICILIO_CONSULTA', String(100), nullable=True, default="a")
    telefono_consulta     = Column('TELEFONO_CONSULTA', String(25), nullable=True, default="0")
    matricula_prov        = Column('MATRICULA_PROV', Integer, nullable=False, default=0)
    matricula_nac         = Column('MATRICULA_NAC', Integer, nullable=False, default=0)
    fecha_recibido        = Column('FECHA_RECIBIDO', Date, nullable=True)
    fecha_matricula       = Column('FECHA_MATRICULA', Date, nullable=True)
    fecha_ingreso         = Column('FECHA_INGRESO', Date, nullable=True)
    domicilio_particular  = Column('DOMICILIO_PARTICULAR', String(100), nullable=False, default="a")
    tele_particular       = Column('TELE_PARTICULAR', String(15), nullable=False, default="0")
    celular_particular    = Column('CELULAR_PARTICULAR', String(15), nullable=False, default="0")
    mail_particular       = Column('MAIL_PARTICULAR', String(50), nullable=False, default="a")
    sexo                  = Column('SEXO', String(1), nullable=False, default='M')
    tipo_doc              = Column('TIPO_DOC', String(3), nullable=False, default='DNI')
    documento             = Column('DOCUMENTO', String(8), nullable=False, default="0")
    fecha_nac             = Column('FECHA_NAC', Date, nullable=True)
    cuit                  = Column('CUIT', String(12), nullable=False, default="0")
    anssal                = Column('ANSSAL', Integer, nullable=False, default=0)
    vencimiento_anssal    = Column('VENCIMIENTO_ANSSAL', Date, nullable=True)
    malapraxis            = Column('MALAPRAXIS', String(100), nullable=False, default="A")
    vencimiento_malapraxis= Column('VENCIMIENTO_MALAPRAXIS', Date, nullable=True)
    monotributista        = Column('MONOTRIBUTISTA', String(2), nullable=False, default="NO")
    factura               = Column('FACTURA', String(2), nullable=False, default="NO")
    cobertura             = Column('COBERTURA', Integer, nullable=False, default=0)
    vencimiento_cobertura = Column('VENCIMIENTO_COBERTURA', Date, nullable=True)
    provincia             = Column('PROVINCIA', String(25), nullable=False, default="A")
    codigo_postal         = Column('CODIGO_POSTAL', String(15), nullable=False, default="0")
    vitalicio             = Column('VITALICIO', String(1), nullable=False, default="N")
    fecha_vitalicio       = Column('FECHA_VITALICIO', Date, nullable=True)
    observacion           = Column('OBSERVACION', String(200), nullable=False, default="A")
    categoria             = Column('CATEGORIA', String(1), nullable=False, default="A")
    existe                = Column('EXISTE', String(1), nullable=False, default="S")
    nro_especialidad6     = Column('NRO_ESPECIALIDAD6', Integer, nullable=False, default=0)
    excep_desde           = Column('EXCEP_DESDE', String(6), nullable=False, default="0")
    excep_hasta           = Column('EXCEP_HASTA', String(6), nullable=False, default="0")
    excep_desde2          = Column('EXCEP_DESDE2', String(6), nullable=False, default="0")
    excep_hasta2          = Column('EXCEP_HASTA2', String(6), nullable=False, default="0")
    excep_desde3          = Column('EXCEP_DESDE3', String(6), nullable=False, default="0")
    excep_hasta3          = Column('EXCEP_HASTA3', String(6), nullable=False, default="0")
    ingresar              = Column('INGRESAR', String(1), nullable=False, default="D")

    debitos       = relationship("Debito", back_populates="medico")
    descuentos    = relationship("Descuento", back_populates="medico")
    prestaciones  = relationship("Prestacion", back_populates="medico")


class ConceptoDescuento(Base):
    __tablename__ = "lista_concepto_descuento"
    id   = Column(Integer, primary_key=True, autoincrement=True)
    cod_ref     = Column(String(50), unique=True, nullable=False)
    nombre      = Column(String(100), nullable=False)
    precio      = Column(Numeric(10,2), nullable=False)
    porcentaje  = Column(Numeric(5,2), nullable=False)

    debitos     = relationship("Descuento", back_populates="concepto")  # si quieres navegar

class ObraSocial(Base):
    __tablename__ = "obras_sociales"
    id     = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(String(100), unique=True, nullable=False)

    debitos   = relationship("Debito", back_populates="obra_social")
    prestaciones = relationship("Prestacion", back_populates="obra_social")

class Periodo(Base):
    __tablename__ = "periodos"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    periodo        = Column(String(7),unique=True,nullable=False,comment="PK natural en formato YYYYMM")
    version        = Column(
                      Integer,
                      nullable=False,
                      default=1,
                      comment="Versión del período; se incrementa al reabrir"
                    )
    status         = Column(String(20), nullable=False, default="en_curso")  # en_curso, finalizado
    total_bruto    = Column(Numeric(14,2), nullable=False, default=0)
    total_debitado = Column(Numeric(14,2), nullable=False, default=0)
    total_descontado= Column(Numeric(14,2), nullable=False, default=0)
    total_neto     = Column(Numeric(14,2), nullable=False, default=0)
    created_at     = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    updated_at     = Column(
                      TIMESTAMP,
                      nullable=False,
                      server_default=func.current_timestamp(),
                      onupdate=func.current_timestamp()
                    )

    debitos = relationship("Debito",back_populates="periodo_rel",foreign_keys="[Debito.periodo]")
    descuentos = relationship("Descuento", back_populates="periodo")
    prestaciones = relationship("Prestacion", back_populates="periodo")

class Prestacion(Base):
    __tablename__ = "prestaciones"
    id                 = Column("id_prestacion", BigInteger, primary_key=True, autoincrement=True)
    periodo            = Column(String(7),ForeignKey("periodos.periodo"),nullable=False,comment="YYYYMM, FK a periodos.periodo")
    id_med             = Column(Integer, ForeignKey("listado_medico.ID"), nullable=False)
    id_os              = Column(Integer, ForeignKey("obras_sociales.id"), nullable=False)
    id_nomenclador     = Column(Integer, nullable=False)
    cantidad           = Column(Integer, nullable=False)
    honorarios         = Column(Numeric(10,2), nullable=False)
    gastos             = Column(Numeric(10,2), nullable=False)
    ayudante           = Column(Numeric(10,2), nullable=False)
    importe_total      = Column(Numeric(14,2), nullable=False)
    created_at         = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())

    medico      = relationship("Medico", back_populates="prestaciones")
    obra_social = relationship("ObraSocial", back_populates="prestaciones")
    periodo_rel = relationship("Periodo",back_populates="prestaciones",foreign_keys=[periodo],viewonly=True)
    debitos     = relationship("Debito", back_populates="prestacion")

class Debito(Base):
    __tablename__ = "debitos"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    id_med           = Column(Integer, ForeignKey("listado_medico.ID"), nullable=False)
    id_os            = Column(Integer, ForeignKey("obras_sociales.id"), nullable=False)
    periodo          = Column(String(7),ForeignKey("periodos.periodo"),nullable=False,comment="En formato YYYYMM, FK a periodos.periodo")
    id_prestacion    = Column(BigInteger, ForeignKey("prestaciones.id_prestacion"), nullable=False)
    importe          = Column(Numeric(14,2), nullable=False)
    observaciones    = Column(String(200), nullable=True)
    created_at       = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())

    medico      = relationship("Medico", back_populates="debitos")
    obra_social = relationship("ObraSocial", back_populates="debitos")
    prestacion  = relationship("Prestacion", back_populates="debitos")
    periodo_rel = relationship("Periodo",back_populates="debitos",foreign_keys=[periodo],viewonly=True)

class Descuento(Base):
    __tablename__ = "descuentos"
    id                           = Column(Integer, primary_key=True, autoincrement=True)
    created_at                   = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
    id_lista_concepto_descuento  = Column(Integer, ForeignKey("lista_concepto_descuento.id"), nullable=False)
    id_med                       = Column(Integer, ForeignKey("listado_medico.ID"), nullable=False)
    periodo                      = Column(String(7),ForeignKey("periodos.periodo"),nullable=False,comment="YYYYMM, FK a periodos.periodo")

    concepto    = relationship("ConceptoDescuento", back_populates="debitos")
    medico      = relationship("Medico", back_populates="descuentos")
    periodo_rel  = relationship("Periodo",back_populates="descuentos",foreign_keys=[periodo],viewonly=True)

# class Nomenclador(Base):
#     pass
class OtrosDescuentos(Base):
    __tablename__ = "otros_descuentos"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    id_med     = Column(Integer, ForeignKey("listado_medico.ID"), nullable=False)
    concepto    = Column(String(100), nullable=False)
    importe     = Column(Numeric(10,2), nullable=False)
    periodo     = Column(String(7),ForeignKey("periodos.periodo"),nullable=False,comment="YYYYMM, FK a periodos.periodo")
    observacion = Column(String(200), nullable=True)
    created_at  = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())

    medico      = relationship("Medico")
    periodo_rel = relationship("Periodo",back_populates="otros_descuentos",foreign_keys=[periodo],viewonly=True)


# class DetalleFacturacion(Base):
#     __tablename__ = "detalle_facturacion"
#     id_detalle_prestaciones = Column(
#         BigInteger, primary_key=True, autoincrement=True
#     )
#     periodo = Column(String(6), nullable=False)
#     cod_med = Column(BigInteger, nullable=False)
#     categoria = Column(CHAR(1), nullable=False)
#     id_especialidad = Column(SmallInteger, nullable=False)
#     nro_orden = Column(BigInteger, nullable=False)
#     cod_obr = Column(SmallInteger, nullable=False)
#     cod_nom = Column(String(8), nullable=False)
#     tpo_funcion = Column(String(2), nullable=False)
#     sesion = Column(Integer,nullable=False,comment="Cantidad de sesiones = X")
#     cantidad = Column(Integer, nullable=False)
#     dni_p = Column(String(20),nullable=False,comment="OSDE_nro_afiliado")
#     nom_ape_p = Column(String(60), nullable=False)
#     tpo_serv = Column(CHAR(1), nullable=False)
#     cod_clinica = Column(SmallInteger, nullable=False)
#     fecha_practica = Column(Date, nullable=False)
#     tipo_orden = Column(CHAR(1), nullable=False)
#     porc = Column(Integer, nullable=False)
#     honorarios = Column(Numeric(10, 2), nullable=False) # double(10,2)
#     gastos = Column(Numeric(10, 2), nullable=False) # double(10,2)
#     ayudante = Column(Numeric(10, 2), nullable=False) # double(10,2)
#     importe_total = Column(Numeric(10, 2), nullable=False) # double(10,2)
#     manual = Column(CHAR(1), nullable=False)
#     cod_med_indica = Column(String(12),nullable=False,comment="OSDE_codigo_prestador")
#     codigo_oms = Column(String(12),nullable=False,comment="OSDE_codigo_prestador")
#     diag = Column(String(100), nullable=False, comment="OSDE_nro_transaccion")
#     nro_vias = Column(String(2), nullable=False, comment="OSDE_tipo_de_orden")
#     fin_semana = Column(String(6), nullable=False, comment="OSDE_nro_plan")
#     nocturno = Column(String(2), nullable=False, comment="Osde_Tipo_prestacion")
#     feriado = Column(CHAR(2), nullable=False, comment="	Campo de tipo nomenclador: NN, CA, CI, NC.")
#     urgencia = Column(CHAR(1), nullable=False, comment="Campo deVia__T=Tradicional, L=LaParoscopica	")
#     estado = Column(CHAR(1), nullable=False, default="A")
#     usuario = Column(String(15), nullable=False, default="")
#     created = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
#     ck_practica_id = Column(Integer, nullable=True)
#     ck_revisar = Column(Integer, nullable=True)
#     ck_estado_id = Column(Integer, nullable=True)

