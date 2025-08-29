from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from db import models
from app import schemas

# -----------------------
#  DEBITO
# -----------------------

def get_debito(db: Session, deb_id: int) -> Optional[models.Debito]:
    return db.query(models.Debito).filter(models.Debito.id == deb_id).first()

def get_debitos(
    db: Session,
    obra_social_id: Optional[int] = None,
    mes: Optional[int] = None,
    anio: Optional[int] = None,
    skip: int = 0,
    limit: int = 100
) -> List[models.Debito]:
    q = db.query(models.Debito)
    if obra_social_id:
        q = q.filter(models.Debito.obra_social_id == obra_social_id)
    if mes:
        q = q.filter(models.Debito.mes == mes)
    if anio:
        q = q.filter(models.Debito.anio == anio)
    return q.offset(skip).limit(limit).all()

def create_debito(db: Session, obj_in: schemas.DebitoCreate) -> models.Debito:
    db_obj = models.Debito(**obj_in.dict(exclude={"detalles"}))
    for det in obj_in.detalles:
        db_obj.detalles.append(models.DebitoDetalle(**det.dict()))
    db.add(db_obj); db.commit(); db.refresh(db_obj)
    return db_obj

def delete_debito(db: Session, deb_id: int) -> None:
    db_obj = get_debito(db, deb_id)
    if db_obj:
        db.delete(db_obj); db.commit()