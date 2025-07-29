from sqlalchemy import Column, Integer, String, Date
from .database import Base
from sqlalchemy import (Column,BigInteger,JSON,Integer,TIMESTAMP,String,Date,Numeric,ForeignKey,Float,Text,DateTime,func)
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


class Concepto(Base):
    __tablename__ = "conceptos"
    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    id = Column(Integer, primary_key=True)
    descripcion = Column(Text, nullable=False)
    codigo = Column(Integer)
    es_deduccion = Column(Integer, nullable=False, default=0)

    # Si luego quieres relacionarlo con deducciones o detalles:
    deducciones = relationship("Deduccion", back_populates="concepto")
    liquidacion_detalles = relationship("LiquidacionDetalle", back_populates="concepto")
    liquidacion_obra_detalles = relationship("LiquidacionObraDetalle", back_populates="concepto")


class ObraSocial(Base):
    __tablename__ = 'obra_sociales'

    created = Column(DateTime, nullable=True)
    modified = Column(DateTime, nullable=True)
    deleted = Column(DateTime, nullable=True)
    id = Column(Integer, primary_key=True)
    codigo = Column(Integer, nullable=True)
    nombre = Column(String(300), nullable=True)
    estado_id = Column(Integer, nullable=True)
    carga_nro_orden = Column(String(45), nullable=True)
    relacion_obra_social_id = Column(Integer, nullable=True)
    sin_restriccion_especialidad = Column(Integer, nullable=True)
    galeno_id = Column(Integer, nullable=True)

    debitos = relationship("Debito", back_populates="obra_social")
    facturaciones = relationship("Facturacion", back_populates="obra_social")
    liquidacion_obras = relationship("LiquidacionObra", back_populates="obra_social")


class Periodo(Base):
    __tablename__ = 'periodos'

    created = Column(DateTime, nullable=True)
    modified = Column(DateTime, nullable=True)
    id = Column(Integer, primary_key=True)
    mes = Column(Integer, nullable=True)
    anio = Column(String(45), nullable=True)
    liquidado = Column(Integer, nullable=False, default=0)

    facturaciones = relationship("Facturacion", back_populates="periodo")


class Facturacion(Base):
    __tablename__ = 'facturaciones'

    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    deleted = Column(DateTime, nullable=True)
    id = Column(Integer, primary_key=True)
    periodo_id = Column(Integer, ForeignKey('periodos.id'), nullable=False)
    obra_social_id = Column(Integer, ForeignKey('obra_sociales.id'), nullable=False)
    estado_id = Column(Integer, nullable=False, default=1)
    total = Column(Numeric(12, 2), nullable=False, default=0.00)
    fact_hon_consultas = Column(Numeric(12, 2), nullable=False, default=0.00)
    fact_hon_practicas = Column(Numeric(12, 2), nullable=False, default=0.00)
    fact_gastos = Column(Numeric(12, 2), nullable=False, default=0.00)
    fact_total = Column(Numeric(12, 2), nullable=False, default=0.00)
    liquidacion_id = Column(Integer, nullable=True)
    doble = Column(Integer, nullable=False, default=0)

    periodo = relationship("Periodo", back_populates="facturaciones")
    obra_social = relationship("ObraSocial", back_populates="facturaciones")
    detalles = relationship("FacturacionDetalle", back_populates="facturacion")
    debitos = relationship("Debito", back_populates="facturacion")
    debito_detalles = relationship("DebitoDetalle", back_populates="facturacion")


class FacturacionDetalle(Base):
    __tablename__ = 'facturacion_detalles'

    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    deleted = Column(DateTime, nullable=True)
    id = Column(Integer, primary_key=True)
    periodo_id = Column(Integer, nullable=False)
    facturacion_id = Column(Integer, ForeignKey('facturaciones.id'), nullable=False)
    socio_id = Column(Integer, nullable=False)
    socio_modelo = Column(String(100), nullable=False)
    categoria = Column(String(1), nullable=False)
    nro_orden = Column(String(100), nullable=True)
    nomenclador_codigo = Column(String(10), nullable=False)
    nomenclador_practica_id = Column(Integer, nullable=True)
    opcion_pago = Column(String(3), nullable=False)
    sesion = Column(Integer, nullable=False)
    cantidad = Column(Integer, nullable=False)
    afiliado_id = Column(Integer, nullable=True)
    nro_afiliado = Column(String(20), nullable=True)
    apellido_nombre = Column(String(150), nullable=True)
    tipo_servicio = Column(String(1), nullable=False)
    clinica_id = Column(Integer, nullable=True)
    fecha_practica = Column(Date, nullable=True)
    tipo_orden = Column(String(1), nullable=False)
    porcentaje = Column(Float, nullable=False)
    honorarios = Column(Numeric(12, 2), nullable=False)
    gastos = Column(Numeric(12, 2), nullable=False)
    ayudantes = Column(Numeric(12, 2), nullable=False)
    valor_unitario = Column(Numeric(12, 2), nullable=False, default=0.00)
    total = Column(Numeric(12, 2), nullable=False)
    recalculo_total = Column(Numeric(12, 2), nullable=False)
    diferencia_total = Column(Numeric(12, 2), nullable=False)
    tipo_calculo = Column(String(1), nullable=False)
    matricula_profesional = Column(String(12), nullable=True)
    cie10_codigo = Column(String(10), nullable=True)
    diagnostico = Column(String(100), nullable=True)
    nro_vias = Column(Integer, nullable=True)
    fin_semana = Column(Integer, nullable=True)
    nocturno = Column(Integer, nullable=True)
    feriado = Column(Integer, nullable=True)
    urgencias = Column(Integer, nullable=True)
    nomenclador = Column(String(2), nullable=False)
    tipo_via = Column(String(1), nullable=False, default='T')
    estado_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    obra_social_socio_relacion = Column(Integer, nullable=False, default=1)
    old_detalle_id = Column(Integer, nullable=False)

    facturacion = relationship("Facturacion", back_populates="detalles")
    debito_detalles = relationship("DebitoDetalle", back_populates="facturacion_detalle")


class Debito(Base):
    __tablename__ = 'debitos'

    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    deleted = Column(DateTime, nullable=True)
    id = Column(Integer, primary_key=True)
    facturacion_id = Column(Integer, ForeignKey('facturaciones.id'), nullable=True)
    estado_id = Column(Integer, nullable=False, default=0)
    obra_social_id = Column(Integer, ForeignKey('obra_sociales.id'), nullable=True)
    mes = Column(Integer, nullable=True)
    anio = Column(Integer, nullable=True)
    liquidacion_id = Column(Integer, ForeignKey('liquidaciones.id'), nullable=True)
    grupo_id = Column(Integer, nullable=False, default=1)
    pasar = Column(Integer, nullable=False, default=0)
    tasks = Column(Integer, nullable=False, default=0)
    factura_punto_venta = Column(Integer, nullable=True)
    factura_numero = Column(Integer, nullable=True)

    facturacion = relationship("Facturacion", back_populates="debitos")
    obra_social = relationship("ObraSocial", back_populates="debitos")
    liquidacion = relationship("Liquidacion", back_populates="debitos")
    detalles = relationship("DebitoDetalle", back_populates="debito")


class DebitoDetalle(Base):
    __tablename__ = 'debito_detalles'

    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    deleted = Column(DateTime, nullable=True)
    id = Column(Integer, primary_key=True)
    debito_id = Column(Integer, ForeignKey('debitos.id'), nullable=False)
    facturacion_id = Column(Integer, ForeignKey('facturaciones.id'), nullable=True)
    facturacion_detalle_id = Column(Integer, ForeignKey('facturacion_detalles.id'), nullable=True)
    socio_id = Column(Integer, nullable=False)
    socio_modelo = Column(String(10), nullable=False)
    corr = Column(String(10), nullable=True)
    paciente = Column(Text, nullable=False)
    cantidad = Column(Integer, nullable=False)
    nomenclador_codigo = Column(String(10), nullable=False)
    nro_orden = Column(String(100), nullable=True)
    fecha_practica = Column(Date, nullable=True)
    honorarios = Column(Numeric(12, 2), nullable=False, default=0.00)
    gastos = Column(Numeric(12, 2), nullable=False, default=0.00)
    antiguedad = Column(Numeric(12, 2), nullable=False, default=0.00)
    porcentaje = Column(Numeric(10, 2), nullable=False, default=100.00)
    clinica_id = Column(Integer, nullable=True)
    tipo_movimiento = Column(String(1), nullable=False)
    tipo = Column(Integer, nullable=False, default=1)
    grupo_id = Column(Integer, nullable=False, default=1)
    estado_id = Column(Integer, nullable=False, default=0)
    tiene_facturacion = Column(Integer, nullable=False, default=1)
    linea_indice = Column(Integer, nullable=False, default=0)

    debito = relationship("Debito", back_populates="detalles")
    facturacion = relationship("Facturacion", back_populates="debito_detalles")
    facturacion_detalle = relationship("FacturacionDetalle", back_populates="debito_detalles")


class Concepto(Base):
    __tablename__ = 'conceptos'

    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    id = Column(Integer, primary_key=True)
    descripcion = Column(Text, nullable=False)
    codigo = Column(Integer, nullable=True)
    es_deduccion = Column(Integer, nullable=False, default=0)


class Liquidacion(Base):
    __tablename__ = "liquidaciones"
    created = Column(DateTime)
    modified = Column(DateTime)
    id = Column(Integer, primary_key=True)
    mes = Column(Integer)
    anio = Column(String(45))
    dgi_mes = Column(Integer, nullable=False, default=0)
    dgi_anio = Column(Integer, nullable=False, default=0)
    nro_liquidacion = Column(Integer, nullable=False, default=0)
    estado_id = Column(Integer, nullable=False, default=1)
    proceso_id = Column(Integer, nullable=False, default=0)
    calculo_deducciones = Column(Integer, nullable=False, default=0)
    fecha_calculo = Column(DateTime)
    resumen = Column(Text)
    proceso_cerrar_id = Column(Integer, nullable=False, default=0)
    fecha_cierre = Column(DateTime)
    data_socio_grupo = Column(JSON)
    es_visible = Column(Integer, nullable=False, default=1)
    nro_inicio_cheque = Column(Integer, nullable=False, default=0)
    santander_nro_inicio_cheque = Column(Integer, nullable=False, default=0)
    proceso_pagos_id = Column(Integer, nullable=False, default=0)

    debitos = relationship("Debito", back_populates="liquidacion")
    deducciones = relationship("Deduccion", back_populates="liquidacion")
    liquidacion_detalles = relationship(
        "LiquidacionDetalle",
        back_populates="liquidacion"
    )
    liquidacion_obras = relationship(
        "LiquidacionObra",
        back_populates="liquidacion"
    )


class LiquidacionDetalle(Base):
    __tablename__ = "liquidacion_detalles"
    created = Column(DateTime)
    modified = Column(DateTime)
    id = Column(BigInteger, primary_key=True)
    socio_id = Column(Integer)
    socio_modelo = Column(String(10), nullable=False)
    mes = Column(Integer, nullable=False, default=0)
    anio = Column(Integer, nullable=False, default=0)
    facturacion_id = Column(Integer)
    liquidacion_obra_id = Column(Integer, nullable=False, default=0)
    liquidacion_id = Column(Integer, ForeignKey("liquidaciones.id"))
    concepto_id = Column(Integer, ForeignKey("conceptos.id"))
    estado_id = Column(Integer, nullable=False, default=1)
    liquidacion_estado_id = Column(Integer, nullable=False, default=0)
    tipo_movimiento = Column(String(1), nullable=False)
    fact_honorarios = Column(Numeric(12,2), nullable=False, default=0.00)
    fact_gastos = Column(Numeric(12,2), nullable=False, default=0.00)
    fact_antiguedad = Column(Numeric(12,2), nullable=False, default=0.00)
    fact_total = Column(Numeric(12,2), nullable=False, default=0.00)
    porcentaje = Column(Numeric(10,2), nullable=False, default=100.00)
    liq_honorarios = Column(Numeric(12,2), nullable=False, default=0.00)
    liq_gastos = Column(Numeric(10,2), nullable=False, default=0.00)
    liq_antiguedad = Column(Numeric(12,2), nullable=False, default=0.00)
    liq_total = Column(Numeric(12,2), nullable=False, default=0.00)
    viene_de_id = Column(Integer, nullable=False, default=0)
    debito_detalle_id = Column(Integer)
    debito_id = Column(Integer)
    obra_social_id = Column(Integer)
    acarrea_liquidacion_id = Column(Integer)
    reintegro_id = Column(Integer)
    ultimo_descuento = Column(Integer, nullable=False, default=0)
    tabla_relacionada = Column(String(50))
    tabla_relacionada_id = Column(Integer)
    paga_por_caja = Column(Integer, nullable=False, default=0)
    orden = Column(Integer, nullable=False, default=0)
    especialidad_id = Column(Integer)
    paciente_descripcion = Column(Text)
    clase = Column(Integer)
    cbl_procesado = Column(Integer, nullable=False, default=0)
    sis_viene_de = Column(String(50))
    fecha_liq_obra = Column(DateTime)
    orden_liq_socios = Column(Integer, nullable=False, default=0)

    liquidacion = relationship("Liquidacion", back_populates="liquidacion_detalles")
    concepto = relationship("Concepto", back_populates="liquidacion_detalles")


class LiquidacionObra(Base):
    __tablename__ = "liquidacion_obras"
    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    deleted = Column(DateTime)
    id = Column(Integer, primary_key=True)
    facturacion_id = Column(Integer, nullable=False, default=0)
    obra_social_id = Column(Integer, ForeignKey("obra_sociales.id"))
    mes = Column(Integer)
    anio = Column(Integer)
    estado_id = Column(Integer, nullable=False, default=1)
    porcentaje = Column(Numeric(10,2), nullable=False, default=0.00)
    tipo = Column(Integer, nullable=False, default=0)
    liquidaciones = Column(Text)
    registros_total = Column(Integer, nullable=False, default=0)
    registros_usados = Column(Integer, nullable=False, default=0)
    modificaciones = Column(Text)
    cantidad_modificaciones = Column(Integer, nullable=False, default=0)
    bruto_registros = Column(Integer, nullable=False, default=0)
    bruto_total = Column(Numeric(12,2), nullable=False, default=0.00)
    es_visible = Column(Integer, nullable=False, default=1)
    calculo_totales = Column(JSON)
    pago_doble = Column(Integer, nullable=False, default=0)

    obra_social = relationship("ObraSocial", back_populates="liquidacion_obras")
    liquidacion = relationship("Liquidacion", back_populates="liquidacion_obras")
    detalles = relationship(
        "LiquidacionObraDetalle",
        back_populates="liquidacion_obra"
    )


class LiquidacionObraDetalle(Base):
    __tablename__ = "liquidacion_obra_detalles"
    created = Column(DateTime, nullable=False)
    modified = Column(DateTime, nullable=False)
    id = Column(Integer, primary_key=True)
    facturacion_id = Column(Integer, nullable=False, default=0)
    fact_honorarios = Column(Numeric(12,2), nullable=False, default=0.00)
    fact_gastos = Column(Numeric(12,2), nullable=False, default=0.00)
    fact_antiguedad = Column(Numeric(12,2), nullable=False, default=0.00)
    fact_total = Column(Numeric(12,2), nullable=False, default=0.00)
    liquidacion_obra_id = Column(Integer, ForeignKey("liquidacion_obras.id"), nullable=False)
    concepto_id = Column(Integer, ForeignKey("conceptos.id"), nullable=False)
    liq_honorarios = Column(Numeric(12,2), nullable=False, default=0.00)
    liq_gastos = Column(Numeric(12,2), nullable=False, default=0.00)
    liq_antiguedad = Column(Numeric(12,2), nullable=False, default=0.00)
    liq_total = Column(Numeric(12,2), nullable=False, default=0.00)
    porcentaje = Column(Numeric(10,2), nullable=False, default=100.00)
    tipo_movimiento = Column(String(1), nullable=False)
    estado_id = Column(Integer, nullable=False, default=0)

    liquidacion_obra = relationship("LiquidacionObra", back_populates="detalles")
    concepto = relationship("Concepto", back_populates="liquidacion_obra_detalles")


# class Descuento(Base):
#     __tablename__ = "descuentos"
#     id                           = Column(Integer, primary_key=True, autoincrement=True)
#     created_at                   = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())
#     id_lista_concepto_descuento  = Column(Integer, ForeignKey("lista_concepto_descuento.id"), nullable=False)
#     id_med                       = Column(Integer, ForeignKey("listado_medico.ID"), nullable=False)
#     periodo                      = Column(String(7),ForeignKey("periodos.periodo"),nullable=False,comment="YYYYMM, FK a periodos.periodo")

#     concepto    = relationship("ConceptoDescuento", back_populates="debitos")
#     medico      = relationship("Medico", back_populates="descuentos")
#     periodo_rel  = relationship("Periodo",back_populates="descuentos",foreign_keys=[periodo],viewonly=True)

# class OtrosDescuentos(Base):
#     __tablename__ = "otros_descuentos"
#     id          = Column(Integer, primary_key=True, autoincrement=True)
#     id_med     = Column(Integer, ForeignKey("listado_medico.ID"), nullable=False)
#     concepto    = Column(String(100), nullable=False)
#     importe     = Column(Numeric(10,2), nullable=False)
#     periodo     = Column(String(7),ForeignKey("periodos.periodo"),nullable=False,comment="YYYYMM, FK a periodos.periodo")
#     observacion = Column(String(200), nullable=True)
#     created_at  = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())

#     medico      = relationship("Medico")
#     periodo_rel = relationship("Periodo",back_populates="otros_descuentos",foreign_keys=[periodo],viewonly=True)

# class Prestacion(Base):
#     __tablename__ = "prestaciones"
#     id                 = Column("id_prestacion", BigInteger, primary_key=True, autoincrement=True)
#     periodo            = Column(String(7),ForeignKey("periodos.periodo"),nullable=False,comment="YYYYMM, FK a periodos.periodo")
#     id_med             = Column(Integer, ForeignKey("listado_medico.ID"), nullable=False)
#     id_os              = Column(Integer, ForeignKey("obras_sociales.id"), nullable=False)
#     id_nomenclador     = Column(Integer, nullable=False)
#     cantidad           = Column(Integer, nullable=False)
#     honorarios         = Column(Numeric(10,2), nullable=False)
#     gastos             = Column(Numeric(10,2), nullable=False)
#     ayudante           = Column(Numeric(10,2), nullable=False)
#     importe_total      = Column(Numeric(14,2), nullable=False)
#     created_at         = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp())

#     medico      = relationship("Medico", back_populates="prestaciones")
#     obra_social = relationship("ObraSocial", back_populates="prestaciones")
#     periodo_rel = relationship("Periodo",back_populates="prestaciones",foreign_keys=[periodo],viewonly=True)
#     debitos     = relationship("Debito", back_populates="prestacion")
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

