from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime, date
from typing import List, Optional

# //////////////////////////////
#      Detalle Facturación
# //////////////////////////////

class FacturacionDetalleBase(BaseModel):
    periodo_id: int
    facturacion_id: int
    socio_id: int
    socio_modelo: str
    categoria: str
    nro_orden: Optional[str] = None
    nomenclador_codigo: str
    nomenclador_practica_id: Optional[int] = None
    opcion_pago: str
    sesion: int
    cantidad: int
    afiliado_id: Optional[int] = None
    nro_afiliado: Optional[str] = None
    apellido_nombre: Optional[str] = None
    tipo_servicio: str
    clinica_id: Optional[int] = None
    fecha_practica: Optional[date] = None
    tipo_orden: str
    porcentaje: float
    honorarios: Decimal
    gastos: Decimal
    ayudantes: Decimal
    valor_unitario: Optional[Decimal] = Decimal("0.00")
    total: Decimal
    recalculo_total: Decimal
    diferencia_total: Decimal

class FacturacionDetalleCreate(FacturacionDetalleBase):
    pass

class FacturacionDetalleOut(FacturacionDetalleBase):
    id: int
    created: datetime
    modified: datetime
    deleted: Optional[datetime]
    
    class Config:
        orm_mode = True



# ///////////////////////
#      Facturacion
# ///////////////////////

class FacturacionBase(BaseModel):
    periodo_id: int
    obra_social_id: int
    estado_id: int
    fact_hon_consultas: Decimal
    fact_hon_practicas: Decimal
    fact_gastos: Decimal
    fact_total: Decimal
    total: Decimal

class FacturacionCreate(FacturacionBase):
    detalles: List[FacturacionDetalleCreate]

class FacturacionUpdate(BaseModel):
    estado_id: Optional[int] = None

class FacturacionOut(FacturacionBase):
    id: int
    created: datetime
    modified: datetime
    deleted: Optional[datetime]
    detalles: List[FacturacionDetalleOut]
    class Config:
        orm_mode = True