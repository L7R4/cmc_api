from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from . import models
from app import schemas


# # -----------------------
# #  MEDICO
# # -----------------------
# def get_medico(db: Session, medico_id: int) -> Optional[models.ListadoMedico]:
#     return db.query(models.ListadoMedico).filter(models.ListadoMedico.id == medico_id).first()

# def get_medicos(db: Session, skip: int = 0, limit: int = 100) -> List[models.Medico]:
#     return db.query(models.Medico).offset(skip).limit(limit).all()

# def create_medico(db: Session, obj_in: schemas.MedicoCreate) -> models.Medico:
#     db_obj = models.Medico(**obj_in.dict())
#     db.add(db_obj)
#     db.commit()
#     db.refresh(db_obj)
#     return db_obj

# def update_medico(db: Session, medico_id: int, obj_in: schemas.MedicoUpdate) -> models.Medico:
#     db_obj = get_medico(db, medico_id)
#     if not db_obj:
#         return None
#     for field, value in obj_in.dict(exclude_unset=True).items():
#         setattr(db_obj, field, value)
#     db.commit()
#     db.refresh(db_obj)
#     return db_obj

# def delete_medico(db: Session, medico_id: int) -> None:
#     db_obj = get_medico(db, medico_id)
#     if db_obj:
#         db.delete(db_obj)
#         db.commit()


# # -----------------------
# #  PERIODO
# # -----------------------
# def get_periodo(db: Session, periodo_id: int) -> Optional[models.Periodo]:
#     return db.query(models.Periodo).filter(models.Periodo.id == periodo_id).first()

# def get_periodos(db: Session, skip: int = 0, limit: int = 100) -> List[models.Periodo]:
#     return db.query(models.Periodo).offset(skip).limit(limit).all()

# def create_periodo(db: Session, obj_in: schemas.PeriodoCreate) -> models.Periodo:
#     db_obj = models.Periodo(**obj_in.dict())
#     db.add(db_obj)
#     db.commit()
#     db.refresh(db_obj)
#     return db_obj

# def update_periodo(
#     db: Session,
#     periodo_id: int,
#     obj_in: schemas.PeriodoUpdate
# ) -> Optional[models.Periodo]:
#     db_obj = get_periodo(db, periodo_id)
#     if not db_obj:
#         return None

#     # Si se reabre, incrementa versión
#     nuevo_estado = obj_in.estado
#     if nuevo_estado == "en_curso" and getattr(db_obj, "liquidado", None) == 1:
#         # suponiendo que `liquidado=1` equivale a estado finalizado
#         db_obj.version = getattr(db_obj, "version", 1) + 1

#     for field, value in obj_in.dict(exclude_unset=True).items():
#         setattr(db_obj, field, value)

#     db.commit()
#     db.refresh(db_obj)
#     return db_obj

# def delete_periodo(db: Session, periodo_id: int) -> None:
#     db_obj = get_periodo(db, periodo_id)
#     if db_obj:
#         db.delete(db_obj)
#         db.commit()


# # -----------------------
# #  OBRA SOCIAL
# # -----------------------
# def get_obra_social(db: Session, obra_id: int) -> Optional[models.ObraSocial]:
#     return db.query(models.ObraSocial).filter(models.ObraSocial.id == obra_id).first()

# def get_obras_sociales(db: Session, skip: int = 0, limit: int = 100) -> List[models.ObraSocial]:
#     return db.query(models.ObraSocial).offset(skip).limit(limit).all()

# def create_obra_social(db: Session, obj_in: schemas.ObraSocialCreate) -> models.ObraSocial:
#     db_obj = models.ObraSocial(**obj_in.dict())
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj

# def update_obra_social(
#     db: Session,
#     obra_id: int,
#     obj_in: schemas.ObraSocialUpdate
# ) -> Optional[models.ObraSocial]:
#     db_obj = get_obra_social(db, obra_id)
#     if not db_obj:
#         return None
#     for field, value in obj_in.dict(exclude_unset=True).items():
#         setattr(db_obj, field, value)
#     db.commit(); db.refresh(db_obj)
#     return db_obj

# def delete_obra_social(db: Session, obra_id: int) -> None:
#     db_obj = get_obra_social(db, obra_id)
#     if db_obj:
#         db.delete(db_obj); db.commit()


# # -----------------------
# #  CONCEPTO
# # -----------------------
# def get_concepto(db: Session, concepto_id: int) -> Optional[models.Concepto]:
#     return db.query(models.Concepto).filter(models.Concepto.id == concepto_id).first()

# def get_conceptos(db: Session, skip: int = 0, limit: int = 100) -> List[models.Concepto]:
#     return db.query(models.Concepto).offset(skip).limit(limit).all()

# def create_concepto(db: Session, obj_in: schemas.ConceptoCreate) -> models.Concepto:
#     db_obj = models.Concepto(**obj_in.dict())
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj


# # -----------------------
# #  FACTURACION
# # -----------------------
# def get_facturacion(db: Session, fact_id: int) -> Optional[models.Facturacion]:
#     return db.query(models.Facturacion).filter(models.Facturacion.id == fact_id).first()

# def get_facturaciones(db: Session, skip: int = 0, limit: int = 100) -> List[models.Facturacion]:
#     return db.query(models.Facturacion).offset(skip).limit(limit).all()

# def create_facturacion(db: Session, obj_in: schemas.FacturacionCreate) -> models.Facturacion:
#     db_obj = models.Facturacion(**obj_in.dict(exclude={"detalles"}))
#     for det in obj_in.detalles:
#         db_obj.detalles.append(models.FacturacionDetalle(**det.dict()))
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj

# def delete_facturacion(db: Session, fact_id: int) -> None:
#     db_obj = get_facturacion(db, fact_id)
#     if db_obj:
#         db.delete(db_obj); db.commit()


# # -----------------------
# #  FACTURACION DETALLE
# # -----------------------
# def get_facturacion_detalle(db: Session, det_id: int) -> Optional[models.FacturacionDetalle]:
#     return db.query(models.FacturacionDetalle).filter(models.FacturacionDetalle.id == det_id).first()

# def create_facturacion_detalle(
#     db: Session, obj_in: schemas.FacturacionDetalleCreate
# ) -> models.FacturacionDetalle:
#     db_obj = models.FacturacionDetalle(**obj_in.dict())
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj


# # -----------------------
# #  DEBITO
# # -----------------------
# def get_debito(db: Session, deb_id: int) -> Optional[models.Debito]:
#     return db.query(models.Debito).filter(models.Debito.id == deb_id).first()

# def get_debitos(
#     db: Session,
#     obra_social_id: Optional[int] = None,
#     mes: Optional[int] = None,
#     anio: Optional[int] = None,
#     skip: int = 0,
#     limit: int = 100
# ) -> List[models.Debito]:
#     q = db.query(models.Debito)
#     if obra_social_id:
#         q = q.filter(models.Debito.obra_social_id == obra_social_id)
#     if mes:
#         q = q.filter(models.Debito.mes == mes)
#     if anio:
#         q = q.filter(models.Debito.anio == anio)
#     return q.offset(skip).limit(limit).all()

# def create_debito(db: Session, obj_in: schemas.DebitoCreate) -> models.Debito:
#     db_obj = models.Debito(**obj_in.dict(exclude={"detalles"}))
#     for det in obj_in.detalles:
#         db_obj.detalles.append(models.DebitoDetalle(**det.dict()))
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj

# def delete_debito(db: Session, deb_id: int) -> None:
#     db_obj = get_debito(db, deb_id)
#     if db_obj:
#         db.delete(db_obj); db.commit()


# # -----------------------
# #  DEBITO DETALLE
# # -----------------------
# def get_debito_detalle(db: Session, det_id: int) -> Optional[models.DebitoDetalle]:
#     return db.query(models.DebitoDetalle).filter(models.DebitoDetalle.id == det_id).first()

# def create_debito_detalle(
#     db: Session, obj_in: schemas.DebitoDetalleCreate
# ) -> models.DebitoDetalle:
#     db_obj = models.DebitoDetalle(**obj_in.dict())
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj


# # -----------------------
# #  DEDUCCION
# # -----------------------
# def get_deduccion(db: Session, ded_id: int) -> Optional[models.Deduccion]:
#     return db.query(models.Deduccion).filter(models.Deduccion.id == ded_id).first()

# def get_deducciones(db: Session, skip: int = 0, limit: int = 100) -> List[models.Deduccion]:
#     return db.query(models.Deduccion).offset(skip).limit(limit).all()

# def create_deduccion(db: Session, obj_in: schemas.DeduccionCreate) -> models.Deduccion:
#     db_obj = models.Deduccion(**obj_in.dict())
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj

# def generate_global_deduccion(db: Session, ded_id: int) -> None:
#     ded = get_deduccion(db, ded_id)
#     if not ded:
#         return
#     # Asume periodos con liquidado=0 son activos
#     per = db.query(models.Periodo).filter(models.Periodo.liquidado == 0).first()
#     socios = db.query(models.Medico).all()
#     for soc in socios:
#         liq = db.query(models.Liquidacion).filter_by(
#             medico_id=soc.id, periodo_id=per.id
#         ).first()
#         if not liq:
#             liq = models.Liquidacion(medico_id=soc.id, periodo_id=per.id)
#             db.add(liq); db.flush()
#         liq.deducciones.append(
#             models.LiquidacionDetalle(
#                 concepto=str(ded.concepto_id),  # o ded.tabla_relacionada
#                 monto=(ded.adeudado or 0)
#             )
#         )
#     db.commit()


# # -----------------------
# #  LIQUIDACION
# # -----------------------
# def get_liquidacion(db: Session, liq_id: int) -> Optional[models.Liquidacion]:
#     return db.query(models.Liquidacion).filter(models.Liquidacion.id == liq_id).first()

# def get_liquidaciones(
#     db: Session,
#     medico_id: Optional[int] = None,
#     periodo_id: Optional[int] = None,
#     skip: int = 0,
#     limit: int = 100
# ) -> List[models.Liquidacion]:
#     q = db.query(models.Liquidacion)
#     if medico_id:
#         q = q.filter(models.Liquidacion.medico_id == medico_id)
#     if periodo_id:
#         q = q.filter(models.Liquidacion.periodo_id == periodo_id)
#     return q.offset(skip).limit(limit).all()

# def create_liquidacion(db: Session, obj_in: schemas.LiquidacionCreate) -> models.Liquidacion:
#     db_obj = models.Liquidacion(**obj_in.dict(exclude={"detalles", "obras"}))
#     for det in obj_in.detalles:
#         db_obj.liquidacion_detalles.append(models.LiquidacionDetalle(**det.dict()))
#     for o in obj_in.obras:
#         db_obj.liquidacion_obras.append(models.LiquidacionObraDetalle(**o.dict()))
#     db.add(db_obj); db.commit(); db.refresh(db_obj)
#     return db_obj

# def delete_liquidacion(db: Session, liq_id: int) -> None:
#     db_obj = get_liquidacion(db, liq_id)
#     if db_obj:
#         db.delete(db_obj); db.commit()


# # -----------------------
# #  RESÚMENES / TOTALES
# # -----------------------
# def resumen_liquidacion(
#     db: Session, medico_id: int, periodo_id: int
# ) -> schemas.ResumenLiquidacion:
#     liq = get_liquidacion(db, medico_id)  # o filtrar por medico_id y periodo_id
#     bruto = sum(d.fact_total for obra in liq.liquidacion_obras for d in obra.detalles)
#     total_desc = sum(d.liq_total for d in liq.liquidacion_detalles)
#     neto = bruto - total_desc
#     return schemas.ResumenLiquidacion(
#         bruto=bruto,
#         neto=neto,
#         detalles_descuentos=liq.liquidacion_detalles,
#         detalles_obras=[d for obra in liq.liquidacion_obras for d in obra.detalles]
#     )

# def total_por_obra_social(
#     db: Session, obra_id: int, periodo_id: int
# ) -> float:
#     return (
#         db.query(func.sum(models.LiquidacionObra.bruto_total))
#           .filter(
#               models.LiquidacionObra.obra_social_id == obra_id,
#               models.LiquidacionObra.periodo_id == periodo_id
#           )
#           .scalar() or 0.0
#     )

# def total_por_concepto(
#     db: Session, concepto_id: int, periodo_id: int
# ) -> float:
#     return (
#         db.query(func.sum(models.LiquidacionDetalle.liq_total))
#           .filter(
#               models.LiquidacionDetalle.concepto_id == concepto_id,
#               models.LiquidacionDetalle.periodo_id == periodo_id
#           )
#           .scalar() or 0.0
#     )