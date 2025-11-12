from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

class SpecialtyItemIn(BaseModel):
    id_colegio_espe: int = Field(..., description="ID_COLEGIO_ESPE de la especialidad")
    n_resolucion: Optional[str] = None
    # Aceptá tanto YYYY-MM-DD como dd/MM/yyyy: el endpoint ya parsea
    fecha_resolucion: Optional[str] = None
    adjunto: Optional[str] = None  # id de Documento o path

class RegisterIn(BaseModel):
    documentType: str
    documentNumber: str
    firstName: str
    lastName: str
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    address: str | None = None
    province: str | None = None
    locality: str | None = None
    postalCode: str | None = None
    officeAddress: str | None = None
    officePhone: str | None = None
    cuit: str | None = None
    cbu: str | None = None
    condicionImpositiva: str | None = None
    observations: str | None = None
    provincialLicense: str | None = None
    nationalLicense: str | None = None
    graduationDate: str | None = None
    specialty: str | None = None
    resolutionNumber: str | None = None
    provincialLicenseDate: str | None = None
    nationalLicenseDate: str | None = None
    resolutionDate: str | None = None
    birthDate: str | None = None
    anssal: int | None = None
    anssalExpiry: str | None = None
    malpracticeCompany: str | None = None
    malpracticeExpiry: str | None = None
    malpracticeCoverage: str | None = None
    coverageExpiry: str | None = None
    taxCondition: str | None = None
    specialties: Optional[List[SpecialtyItemIn]] = None

class RegisterOut(BaseModel):
    medico_id: int
    solicitud_id: int
    ok: bool = True