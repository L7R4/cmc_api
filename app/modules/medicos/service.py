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


def _nn(v, fallback):
    """string nullable → string no vacío"""
    v = (v or "").strip()
    return v if v else fallback

def _first(v, default=None):
    return v if v is not None else default

async def save_medico_admin_draft(
    db: AsyncSession,
    body,  # Pydantic con los mismos nombres del público (firstName, lastName, etc.)
    *,
    medico_id: Optional[int] = None,
) -> ListadoMedico:
    """
    - Si viene medico_id: PATCH parcial sobre esa fila.
    - Si NO viene: busca por DOCUMENTO; si no existe, CREA una fila con defaults seguros (sin solicitud).
    - NUNCA crea SolicitudRegistro.
    """
    # ----------- helpers de nombre / licencias / fechas (igual que create_medico_and_solicitud) -----------
    nombre = f"{(getattr(body, 'firstName', '') or '').strip()} {(getattr(body, 'lastName', '') or '').strip()}".strip()

    matricula_prov = _int_or_zero(getattr(body, "provincialLicense", None))
    matricula_nac  = _int_or_zero(getattr(body, "nationalLicense", None))
    fecha_matricula = (
        _parse_date(getattr(body, "provincialLicenseDate", None))
        or _parse_date(getattr(body, "nationalLicenseDate", None))
        or _parse_date(getattr(body, "graduationDate", None))
    )

    # Especialidades (aunque en admin las cargues en otra pestaña, si vinieran, no estorban)
    spec_items = await build_spec_items(db, body)
    nro_especs = [int(x["id_colegio"]) for x in spec_items]
    while len(nro_especs) < 6:
        nro_especs.append(0)

    # ----------- localizar o construir -----------
    med: Optional[ListadoMedico] = None

    if medico_id:
        med = await db.get(ListadoMedico, medico_id)
        if not med:
            raise ValueError("Médico no encontrado")

    if med is None:
        doc = _nn(getattr(body, "documentNumber", ""), "0")
        if doc and not medico_id:
            q = select(ListadoMedico).where(ListadoMedico.DOCUMENTO == doc)
            med = (await db.execute(q)).scalar_one_or_none()

    if med is None:
        # CREAR con defaults "seguros" (idéntico criterio al público)
        med = ListadoMedico(
            NOMBRE = (nombre or "-"),
            TIPO_DOC = _first(getattr(body, "documentType", None), "DNI"),
            DOCUMENTO = _nn(getattr(body, "documentNumber", None), "0"),

            # Domicilios / contacto (rellenos para NOT NULL legacy)
            DOMICILIO_PARTICULAR = _nn(getattr(body, "address", None), "a"),
            PROVINCIA = _nn(getattr(body, "province", None), "A"),
            CODIGO_POSTAL = _nn(getattr(body, "postalCode", None), "0"),
            TELE_PARTICULAR = _nn(getattr(body, "phone", None), "0"),
            CELULAR_PARTICULAR = _nn(getattr(body, "mobile", None), "0"),
            DOMICILIO_CONSULTA = _nn(getattr(body, "officeAddress", None), "a"),
            TELEFONO_CONSULTA = _nn(getattr(body, "officePhone", None), "0"),
            MAIL_PARTICULAR = _nn(getattr(body, "email", None), "a"),

            CUIT = _nn(getattr(body, "cuit", None), "0"),
            OBSERVACION = _nn(getattr(body, "observations", None), "A"),
            EXISTE = "N",

            # Especialidades en columnas
            NRO_ESPECIALIDAD  = nro_especs[0],
            NRO_ESPECIALIDAD2 = nro_especs[1],
            NRO_ESPECIALIDAD3 = nro_especs[2],
            NRO_ESPECIALIDAD4 = nro_especs[3],
            NRO_ESPECIALIDAD5 = nro_especs[4],
            NRO_ESPECIALIDAD6 = nro_especs[5],

            # Matrículas / Fechas
            MATRICULA_PROV   = matricula_prov,
            MATRICULA_NAC    = matricula_nac,
            FECHA_MATRICULA  = fecha_matricula,
            FECHA_NAC        = _parse_date(getattr(body, "birthDate", None)),
            VENCIMIENTO_ANSSAL      = _parse_date(getattr(body, "anssalExpiry", None)),
            VENCIMIENTO_MALAPRAXIS  = _parse_date(getattr(body, "malpracticeExpiry", None)),
            VENCIMIENTO_COBERTURA   = _parse_date(getattr(body, "coverageExpiry", None)),
            FECHA_RECIBIDO          = _parse_date(getattr(body, "graduationDate", None)),

            COBERTURA = _int_or_zero(getattr(body, "malpracticeCoverage", None)),
            MALAPRAXIS = _nn(getattr(body, "malpracticeCompany", None), "A"),
            nro_resolucion = _first(getattr(body, "resolutionNumber", None), None),
            fecha_resolucion = _parse_date(getattr(body, "resolutionDate", None)),

            # snake_case extra
            apellido = _first(getattr(body, "lastName", None), None),
            nombre_  = _first(getattr(body, "firstName", None), None),
            localidad = _first(getattr(body, "locality", None), None),
            cbu = _first(getattr(body, "cbu", None), None),
            condicion_impositiva = (
                getattr(body, "condicionImpositiva", None)
                or getattr(body, "taxCondition", None)
            ),
            titulo = _first(getattr(body, "specialty", None), None),

            # Sexo
            SEXO = (_nn(getattr(body, "gender", None), "M")[:1]).upper(),
        )

        # hashed_password igual que público (usa DNI). Si no hay DNI, hasheá "0"
        med.hashed_password = hash_password(_nn(getattr(body, "documentNumber", None), "0"))

        # conceps_espec default
        med.conceps_espec = {
            "conceps": [],
            "espec": [
                {
                    "id_colegio": it["id_colegio"],
                    "n_resolucion": it["n_resolucion"],
                    "fecha_resolucion": (it["fecha_resolucion"].isoformat() if it["fecha_resolucion"] else None),
                    "adjunto": it["adjunto"],
                }
                for it in spec_items
            ],
        }

        db.add(med)
        await db.flush()   # med.ID listo

    # ----------- PATCH parcial (solo pisa si viene algo en body) -----------
    # Nombre / DNI
    if getattr(body, "firstName", None) is not None or getattr(body, "lastName", None) is not None:
        med.nombre_ = _first(getattr(body, "firstName", None), med.nombre_)
        med.apellido = _first(getattr(body, "lastName", None), med.apellido)
        full = f"{(med.nombre_ or '').strip()} {(med.apellido or '').strip()}".strip()
        med.NOMBRE = full or med.NOMBRE

    if getattr(body, "documentType", None) is not None:
        med.TIPO_DOC = _nn(getattr(body, "documentType", None), med.TIPO_DOC or "DNI")
    if getattr(body, "documentNumber", None) is not None:
        new_doc = _nn(getattr(body, "documentNumber", None), med.DOCUMENTO or "0")
        med.DOCUMENTO = new_doc
        # si no tenía hash, generalo ahora
        if not getattr(med, "hashed_password", None):
            med.hashed_password = hash_password(new_doc)

    # Contacto
    for attr, src in [
        ("DOMICILIO_PARTICULAR", "address"),
        ("PROVINCIA", "province"),
        ("CODIGO_POSTAL", "postalCode"),
        ("TELE_PARTICULAR", "phone"),
        ("CELULAR_PARTICULAR", "mobile"),
        ("DOMICILIO_CONSULTA", "officeAddress"),
        ("TELEFONO_CONSULTA", "officePhone"),
        ("MAIL_PARTICULAR", "email"),
        ("localidad", "locality"),
        ("cbu", "cbu"),
    ]:
        if getattr(body, src, None) is not None:
            setattr(med, attr, _nn(getattr(body, src), getattr(med, attr, "") or ("a" if "DOMICILIO" in attr else "0")))

    # Fiscales / Seguros
    if getattr(body, "cuit", None) is not None:
        med.CUIT = _nn(getattr(body, "cuit", None), med.CUIT or "0")
    if getattr(body, "taxCondition", None) is not None or getattr(body, "condicionImpositiva", None) is not None:
        med.condicion_impositiva = (
            getattr(body, "condicionImpositiva", None) or getattr(body, "taxCondition", None) or med.condicion_impositiva
        )
    if getattr(body, "anssal", None) is not None:
        med.ANSSAL = _int_or_zero(getattr(body, "anssal", None))
    if getattr(body, "anssalExpiry", None) is not None:
        med.VENCIMIENTO_ANSSAL = _parse_date(getattr(body, "anssalExpiry", None))
    if getattr(body, "malpracticeCompany", None) is not None:
        med.MALAPRAXIS = _nn(getattr(body, "malpracticeCompany", None), med.MALAPRAXIS or "A")
    if getattr(body, "malpracticeExpiry", None) is not None:
        med.VENCIMIENTO_MALAPRAXIS = _parse_date(getattr(body, "malpracticeExpiry", None))
    if getattr(body, "malpracticeCoverage", None) is not None:
        med.COBERTURA = _int_or_zero(getattr(body, "malpracticeCoverage", None))

    # Matrículas / Fechas
    if getattr(body, "provincialLicense", None) is not None:
        med.MATRICULA_PROV = _int_or_zero(getattr(body, "provincialLicense", None))
    if getattr(body, "nationalLicense", None) is not None:
        med.MATRICULA_NAC = _int_or_zero(getattr(body, "nationalLicense", None))
    if any(getattr(body, k, None) is not None for k in ("provincialLicenseDate","nationalLicenseDate","graduationDate")):
        med.FECHA_MATRICULA = (
            _parse_date(getattr(body, "provincialLicenseDate", None))
            or _parse_date(getattr(body, "nationalLicenseDate", None))
            or _parse_date(getattr(body, "graduationDate", None))
        )
    if getattr(body, "birthDate", None) is not None:
        med.FECHA_NAC = _parse_date(getattr(body, "birthDate", None))

    # Resolución / título
    if getattr(body, "resolutionNumber", None) is not None:
        med.nro_resolucion = _first(getattr(body, "resolutionNumber", None), med.nro_resolucion)
    if getattr(body, "resolutionDate", None) is not None:
        med.fecha_resolucion = _parse_date(getattr(body, "resolutionDate", None))
    if getattr(body, "specialty", None) is not None:
        med.titulo = _first(getattr(body, "specialty", None), med.titulo)

    # Observaciones / Sexo
    if getattr(body, "observations", None) is not None:
        med.OBSERVACION = _nn(getattr(body, "observations", None), med.OBSERVACION or "A")
    if getattr(body, "gender", None) is not None:
        med.SEXO = (_nn(getattr(body, "gender", None), med.SEXO or "M")[:1]).upper()

    # conceps_espec (merge simple)
    if spec_items:
        med.conceps_espec = {
            "conceps": med.conceps_espec.get("conceps", []) if getattr(med, "conceps_espec", None) else [],
            "espec": [
                {
                    "id_colegio": it["id_colegio"],
                    "n_resolucion": it["n_resolucion"],
                    "fecha_resolucion": (it["fecha_resolucion"].isoformat() if it["fecha_resolucion"] else None),
                    "adjunto": it["adjunto"],
                }
                for it in spec_items
            ],
        }

    await db.commit()
    await db.refresh(med)
    return med

# _DATE_INPUTS = ("%Y-%m-%d", "%d/%m/%Y")

# def _norm_str(v: Optional[str]) -> Optional[str]:
#     if v is None:
#         return None
#     s = str(v).strip()
#     return s or None

# def _norm_date(v: Optional[str]) -> Optional[str]:
#     """Normaliza a 'YYYY-MM-DD' si viene en 'YYYY-MM-DD' o 'DD/MM/YYYY'; sino, devuelve tal cual o None."""
#     v = _norm_str(v)
#     if not v:
#         return None
#     for fmt in _DATE_INPUTS:
#         try:
#             dt = datetime.strptime(v, fmt).date()
#             return dt.isoformat()
#         except ValueError:
#             continue
#     # si ya viene con T/u otra cosa, devolvemos sin romper
#     return v

# def _set_first_existing_attr(obj, candidates, value):
#     """Intenta setear el primer atributo existente de la lista de candidatos."""
#     for name in candidates:
#         if hasattr(obj, name):
#             setattr(obj, name, value)
#             return True
#     return False

# def _apply_partial(med: ListadoMedico, body) -> ListadoMedico:
    # IDENTIFICACIÓN
    if body.documentNumber is not None:
        _set_first_existing_attr(med, ["DOCUMENTO", "documento", "dni"], _norm_str(body.documentNumber))
    if (body.firstName is not None) or (body.lastName is not None):
        # nombres desagregados
        nombre_  = _norm_str(body.firstName) or getattr(med, "nombre_", None)
        apellido = _norm_str(body.lastName)  or getattr(med, "apellido", None)
        _set_first_existing_attr(med, ["nombre_","nombre","NOMBRE_PILA"], nombre_)
        _set_first_existing_attr(med, ["apellido","APELLIDO"], apellido)
        # NOMBRE completo "Apellido Nombre"
        full = f"{apellido or ''} {nombre_ or ''}".strip()
        if full:
            _set_first_existing_attr(med, ["NOMBRE","nombre_completo","DISPLAY_NAME"], full)
    if body.documentType is not None:
        _set_first_existing_attr(med, ["TIPODOC","tipo_documento"], _norm_str(body.documentType))
    if body.gender is not None:
        _set_first_existing_attr(med, ["SEXO","genero"], _norm_str(body.gender))
    if body.birthDate is not None:
        _set_first_existing_attr(med, ["FECHA_NAC","fecha_nac","fechaNacimiento"], _norm_date(body.birthDate))

    # CONTACTO
    if body.phone is not None:
        _set_first_existing_attr(med, ["TELEFONO","telefono"], _norm_str(body.phone))
    if body.altPhone is not None:
        _set_first_existing_attr(med, ["TELEFONO_ALT","telefono_alt","telefono2"], _norm_str(body.altPhone))
    if body.email is not None:
        _set_first_existing_attr(med, ["EMAIL","email"], _norm_str(body.email))

    # DOMICILIO
    if body.address is not None:
        _set_first_existing_attr(med, ["DOMICILIO","domicilio","direccion","CALLE"], _norm_str(body.address))
    if body.addressNumber is not None:
        _set_first_existing_attr(med, ["NRO","domicilio_numero","NUMERO"], _norm_str(body.addressNumber))
    if body.addressFloor is not None:
        _set_first_existing_attr(med, ["PISO","domicilio_piso"], _norm_str(body.addressFloor))
    if body.addressDept is not None:
        _set_first_existing_attr(med, ["DEPTO","domicilio_depto","departamento"], _norm_str(body.addressDept))
    if body.province is not None:
        _set_first_existing_attr(med, ["PROVINCIA","provincia"], _norm_str(body.province))
    if body.locality is not None:
        _set_first_existing_attr(med, ["LOCALIDAD","localidad"], _norm_str(body.locality))
    if body.postalCode is not None:
        _set_first_existing_attr(med, ["COD_POSTAL","cp","codigo_postal"], _norm_str(body.postalCode))

    # PROFESIONALES
    if body.matriculaProv is not None:
        _set_first_existing_attr(med, ["MATRICULA_PROV","matricula_prov"], _norm_str(body.matriculaProv))
    if body.matriculaNac is not None:
        _set_first_existing_attr(med, ["MATRICULA_NAC","matricula_nac"], _norm_str(body.matriculaNac))
    if body.joinDate is not None:
        _set_first_existing_attr(med, ["FECHA_INGRESO","fecha_ingreso"], _norm_date(body.joinDate))

    # FISCAL / SEGUROS
    if body.cuit is not None:
        _set_first_existing_attr(med, ["CUIT","cuit"], _norm_str(body.cuit))
    if body.taxCondition is not None:
        _set_first_existing_attr(med, ["COND_IMPOSITIVA","cond_impositiva","condicion_iva"], _norm_str(body.taxCondition))
    if body.anssal is not None:
        _set_first_existing_attr(med, ["ANSSAL","anssal"], _norm_str(body.anssal))
    if body.anssalExpiry is not None:
        _set_first_existing_attr(med, ["ANSSAL_VENC","anssal_venc"], _norm_date(body.anssalExpiry))
    if body.malpracticeCompany is not None:
        _set_first_existing_attr(med, ["MALAPRACTICA_COMPANIA","seguro_mala_praxis"], _norm_str(body.malpracticeCompany))
    if body.malpracticeExpiry is not None:
        _set_first_existing_attr(med, ["MALAPRACTICA_VENC","mala_praxis_venc"], _norm_date(body.malpracticeExpiry))
    if body.cbu is not None:
        _set_first_existing_attr(med, ["CBU","cbu"], _norm_str(body.cbu))

    # OTROS
    if body.observations is not None:
        _set_first_existing_attr(med, ["OBSERVACIONES","observaciones"], _norm_str(body.observations))

    # NOTA: specialties se gestiona en pestaña aparte (no tocamos acá).
    return med