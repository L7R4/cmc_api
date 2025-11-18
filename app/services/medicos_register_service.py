# app/services/medicos_register_service.py
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db.models import ListadoMedico, SolicitudRegistro, Especialidad
from app.core.passwords import hash_password
from app.utils.main import _parse_date

def _int_or_zero(v: Optional[str]) -> int:
    try:
        if v is None: return 0
        s = str(v).strip()
        return int(s) if s != "" else 0
    except Exception:
        return 0

async def build_spec_items(db: AsyncSession, body) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if getattr(body, "specialties", None):
        for it in (body.specialties or []):
            if not it or not getattr(it, "id_colegio_espe", None):
                continue
            items.append({
                "id_colegio": int(it.id_colegio_espe),
                "n_resolucion": (it.n_resolucion or None),
                "fecha_resolucion": (_parse_date(it.fecha_resolucion) or None),
                "adjunto": (it.adjunto or None),
            })
    else:
        # Soporte legacy (si en algún caso sólo envías 'specialty')
        try:
            id_colegio = int(body.specialty) if body.specialty else None
        except Exception:
            id_colegio = None
        if id_colegio:
            items.append({
                "id_colegio": id_colegio,
                "n_resolucion": (body.resolutionNumber or None),
                "fecha_resolucion": (_parse_date(body.resolutionDate) or None),
                "adjunto": None,
            })
    return items[:6]

async def create_medico_and_solicitud(db: AsyncSession, body, *, existe="N"):
    nombre = f"{(body.firstName or '').strip()} {(body.lastName or '').strip()}".strip()

    # Licencias
    matricula_prov = _int_or_zero(getattr(body, "provincialLicense", None))
    matricula_nac = _int_or_zero(getattr(body, "nationalLicense", None))
    fecha_matricula = (_parse_date(getattr(body, "provincialLicenseDate", None))
                       or _parse_date(getattr(body, "nationalLicenseDate", None))
                       or _parse_date(getattr(body, "graduationDate", None)))

    # Especialidades
    spec_items = await build_spec_items(db, body)
    nro_especs = [int(x["id_colegio"]) for x in spec_items]
    while len(nro_especs) < 6:
        nro_especs.append(0)

    medico = ListadoMedico(
        NOMBRE = nombre or "-",
        TIPO_DOC = getattr(body, "documentType", "DNI"),
        DOCUMENTO = str(getattr(body, "documentNumber", "0")),
        DOMICILIO_PARTICULAR = getattr(body, "address", "a") or "a",
        PROVINCIA = getattr(body, "province", "A") or "A",
        CODIGO_POSTAL = getattr(body, "postalCode", "0") or "0",
        TELE_PARTICULAR = getattr(body, "phone", "0") or "0",
        CELULAR_PARTICULAR = getattr(body, "mobile", "0") or "0",
        DOMICILIO_CONSULTA = getattr(body, "officeAddress", "a") or "a",
        TELEFONO_CONSULTA = getattr(body, "officePhone", "0") or "0",
        MAIL_PARTICULAR = getattr(body, "email", "a") or "a",
        CUIT = getattr(body, "cuit", "0") or "0",
        OBSERVACION = getattr(body, "observations", "A") or "A",
        EXISTE = existe,
        ANSSAL = _int_or_zero(getattr(body, "anssal", None)),
        # Especialidades en columnas
        NRO_ESPECIALIDAD  = nro_especs[0],
        NRO_ESPECIALIDAD2 = nro_especs[1],
        NRO_ESPECIALIDAD3 = nro_especs[2],
        NRO_ESPECIALIDAD4 = nro_especs[3],
        NRO_ESPECIALIDAD5 = nro_especs[4],
        NRO_ESPECIALIDAD6 = nro_especs[5],
        # Matrículas
        MATRICULA_PROV = matricula_prov,
        MATRICULA_NAC = matricula_nac,
        FECHA_MATRICULA = fecha_matricula,
        # Fechas varias
        FECHA_NAC = _parse_date(getattr(body, "birthDate", None)),
        VENCIMIENTO_ANSSAL = _parse_date(getattr(body, "anssalExpiry", None)),
        VENCIMIENTO_MALAPRAXIS = _parse_date(getattr(body, "malpracticeExpiry", None)),
        VENCIMIENTO_COBERTURA = _parse_date(getattr(body, "coverageExpiry", None)),
        FECHA_RECIBIDO = _parse_date(getattr(body, "graduationDate", None)),
        # Cobertura
        COBERTURA = _int_or_zero(getattr(body, "malpracticeCoverage", None)),
        MALAPRAXIS = getattr(body, "malpracticeCompany", "A") or "A",
        nro_resolucion = getattr(body, "resolutionNumber", None),
        fecha_resolucion = _parse_date(getattr(body, "resolutionDate", None)),
        # snake_case extra
        apellido = getattr(body, "lastName", None),
        nombre_ = getattr(body, "firstName", None),
        localidad = getattr(body, "locality", None),
        cbu = getattr(body, "cbu", None),
        condicion_impositiva = (getattr(body, "condicionImpositiva", None) or getattr(body, "taxCondition", None)),
        titulo = getattr(body, "specialty", None),
        # SEXO normalizado
        SEXO = (getattr(body, "gender", None) or "M")[:1].upper(),
    )
    medico.hashed_password = hash_password(str(getattr(body, "documentNumber", "")))

    medico.conceps_espec = {
        "conceps": [],
        "espec": [
            {
                "id_colegio": it["id_colegio"],
                "n_resolucion": it["n_resolucion"],
                "fecha_resolucion": (it["fecha_resolucion"].isoformat() if it["fecha_resolucion"] else None),
                "adjunto": it["adjunto"],
            }
            for it in spec_items
        ]
    }

    db.add(medico)
    await db.commit()
    await db.refresh(medico)

    solicitud = SolicitudRegistro(
        estado="pendiente",
        medico_id=medico.ID,
        observaciones=None,
    )
    db.add(solicitud)
    await db.commit()
    await db.refresh(solicitud)

    return medico, solicitud
