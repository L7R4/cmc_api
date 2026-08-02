import datetime
import os
import shutil
import uuid
from decimal import Decimal
from typing import NamedTuple, Optional, Sequence

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models import (
    Afiliado,
    DetalleFacturacionCMC,
    Documento,
    Especialidad,
    FacturacionCMC,
    ListadoMedico,
    NomencladorCMC,
    ObrasSociales,
    PeriodoMedicoActual,
)
from app.modules.medicos.helpers import parse_conceps_espec
from app.modules.nomenclador import service as service_nm
from app.modules.nomenclador import service_vias
from app.modules.nomenclador.service import LookupError as PrecioLookupError

from app.modules.facturacion.schemas import (
    AfiliadoCreate,
    GuardadoResponse,
    MoverPeriodoPayload,
    MoverPeriodoResponse,
    PrecioResponse,
    PrestacionesComplementariaCreate,
    PrestacionesCreate,
    PrestacionItem,
    PrestacionRead,
    PrestacionUpdate,
)

FUENTE_PRECIO = "nm_historial_precio_codigo"
CODIGOS_NO_PERMITIDOS = {"5000", "6000"}

# Estados de la cabecera `facturacion`:
#   "A" = abierta (período en carga; se crea al cargar la primera prestación).
#   "C" = cerrada (período liquidable). Los históricos importados de CMC usaban
#   "L"/"LC"; se normalizan a "C", pero los seguimos aceptando como cerrados por
#   compatibilidad con datos aún no migrados.
FACTURA_ESTADO_ABIERTA = "A"
FACTURA_ESTADO_CERRADA = "C"
FACTURA_ESTADOS_CERRADOS = ("C", "L", "LC")

# Fase MÉDICO de la cabecera (`estado_doctor`): 'A' abierta / 'C' cerrada.
DOCTOR_ABIERTA = "A"
DOCTOR_CERRADA = "C"

# Quién carga la prestación (columna `detalle_facturacion.origen_carga`).
ORIGEN_MEDICO = "medico"
ORIGEN_COLEGIO = "colegio"

# `detalle_facturacion.tipo_orden` — 'S' cuando la prestación se hizo en una clínica
# (`cod_clinica` presente), sea la clínica el prestador o sólo el ámbito. NULL cuando el
# médico factura por sí mismo sin clínica.
#
# ⚠ Es una MARCA, no el discriminador: `tipo_orden='S'` NO distingue "Sanatorio" de
# "Honorarios individuales" (ambos casos la llevan). La fuente de verdad para eso es
# `tipo` — la columna legacy queda poblada sólo para que se vea al mirar la tabla a mano,
# y nada del código depende de ella (decisión usuario 2026-07-31). Ningún módulo aguas
# abajo (liquidación, lotes, exports) la lee.
TIPO_ORDEN_SANATORIO = "S"

# `tipo` (categorización única de la prestación) y la categoría del nomenclador que
# dispara la regla de gasto 0 bajo sanatorio.
TIPO_SANATORIO = "Sanatorio"
CATEGORIA_HONORARIOS_INDIVIDUALES = "Honorarios individuales"

# Ventana por defecto cuando la OS no define `dia_corte` (20 = del 20 al 20).
DIA_CORTE_DEFAULT = 20

UPLOAD_ROOT_FACTURAS = os.path.join("uploads", "facturas")


async def _guardar_documento_factura(factura_id: int, archivo: Optional[UploadFile]) -> Optional[str]:
    """Guarda el comprobante de una factura en uploads/facturas/{id}/ (mismo patrón
    que ObraSocialDocumento / Documento médico). Devuelve el path relativo servido por
    /uploads, o None si no vino archivo."""
    if not archivo or not archivo.filename:
        return None
    dest_dir = os.path.join(UPLOAD_ROOT_FACTURAS, str(factura_id))
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(archivo.filename)[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(dest_dir, filename).replace("\\", "/")

    def _write():
        archivo.file.seek(0)
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(archivo.file, f, length=1024 * 1024)

    await run_in_threadpool(_write)
    return dest_path


_MESES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


# ── Autocomplete de médicos (con especialidades + condición impositiva) ──────
async def buscar_medicos(db: AsyncSession, q: str, limit: int) -> list[dict]:
    """Autocomplete de médicos por nombre, NRO_SOCIO o matrícula (MATRICULA_PROV).
    Además del cod/nombre/matrícula de siempre, resuelve `condicion_impositiva` y las
    especialidades del médico (mismo shape que `GET /medicos/{id}/especialidades`),
    en batch (sin N+1)."""
    M = ListadoMedico
    cond = M.NOMBRE.ilike(f"%{q}%")
    if q.isdigit():
        cond = or_(M.NRO_SOCIO == int(q), M.MATRICULA_PROV == int(q), cond)
    rows = list((await db.execute(select(M).where(cond).limit(limit))).scalars().all())

    # Especialidades de cada médico (desde conceps_espec), + ids a resolver en batch.
    espec_por_medico: dict[int, list[dict]] = {}
    ids_especialidad: set[int] = set()
    ids_adjunto: set[int] = set()
    for m in rows:
        obj = parse_conceps_espec(m.conceps_espec)
        items = []
        for it in obj["espec"]:
            if not isinstance(it, dict):
                continue
            try:
                eid = int(it.get("id_colegio"))
            except (TypeError, ValueError):
                continue
            adj_raw = it.get("adjunto")
            adj_id = int(adj_raw) if str(adj_raw or "").strip().isdigit() else None
            ids_especialidad.add(eid)
            if adj_id:
                ids_adjunto.add(adj_id)
            items.append({
                "id": eid,
                "n_resolucion": it.get("n_resolucion") or None,
                "fecha_resolucion": it.get("fecha_resolucion") or None,
                "adjunto_id": adj_id,
            })
        espec_por_medico[m.ID] = items

    nombre_map: dict[int, str] = {}
    if ids_especialidad:
        esp_rows = (await db.execute(
            select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
            .where(Especialidad.ID_COLEGIO_ESPE.in_(ids_especialidad))
        )).all()
        nombre_map = {int(eid): nombre for eid, nombre in esp_rows}

    path_map: dict[int, str] = {}
    if ids_adjunto:
        doc_rows = (await db.execute(
            select(Documento.id, Documento.path).where(Documento.id.in_(ids_adjunto))
        )).all()
        path_map = {int(did): f"/{str(p).lstrip('/')}" for did, p in doc_rows if p}

    out: list[dict] = []
    for m in rows:
        especialidades = [
            {
                "id": it["id"],
                "nombre": nombre_map.get(it["id"]),
                "n_resolucion": it["n_resolucion"],
                "fecha_resolucion": it["fecha_resolucion"],
                "adjunto_id": it["adjunto_id"],
                "adjunto_url": path_map.get(it["adjunto_id"]) if it["adjunto_id"] else None,
            }
            for it in espec_por_medico.get(m.ID, [])
        ]
        out.append({
            "cod": str(m.NRO_SOCIO),
            "nombre": m.NOMBRE,
            "matricula": m.MATRICULA_PROV,
            "categoria": m.CATEGORIA,
            "condicion_impositiva": m.condicion_impositiva,
            "es_organizacion": bool(m.es_organizacion),
            "especialidades": especialidades,
        })
    return out


async def buscar_clinicas(db: AsyncSession, q: str, limit: int) -> list[dict]:
    """Autocomplete de clínicas/organizaciones — misma tabla `listado_medico` que
    `buscar_medicos`, filtrada por `es_organizacion=1`. Por nombre, NRO_SOCIO,
    documento o CUIT."""
    M = ListadoMedico
    filtros = [M.es_organizacion == True]  # noqa: E712 (comparación explícita, columna bool)
    if q.isdigit():
        filtros.append(or_(
            M.NRO_SOCIO == int(q), M.NOMBRE.ilike(f"%{q}%"),
            M.DOCUMENTO == q, M.CUIT == q,
        ))
    else:
        filtros.append(M.NOMBRE.ilike(f"%{q}%"))
    rows = list((await db.execute(select(M).where(*filtros).limit(limit))).scalars().all())
    return [
        {
            "cod": m.NRO_SOCIO,
            "nombre": m.NOMBRE,
            # DOCUMENTO/CUIT declarados String en el modelo pero vuelven como int del
            # ORM en filas legacy (columna tipada numérica en la DB) → castear a str
            # explícito; Pydantic v2 (a diferencia de v1) no coerciona int→str solo.
            "documento": str(m.DOCUMENTO) if m.DOCUMENTO else None,
            "cuit": str(m.CUIT) if m.CUIT else None,
            "localidad": m.localidad or None,
        }
        for m in rows
    ]


# ── Helpers de período (formato "YYYYMM") ────────────────────────────────────
def _periodo_to_date(periodo: str) -> datetime.date:
    try:
        return datetime.date(int(periodo[:4]), int(periodo[4:6]), 1)
    except (ValueError, IndexError):
        raise HTTPException(422, f"Período inválido: '{periodo}' (se espera YYYYMM)")


def _shift_periodo(periodo: str, months: int) -> str:
    d = _periodo_to_date(periodo)
    total = d.year * 12 + (d.month - 1) + months
    y, m = divmod(total, 12)
    return f"{y}{m + 1:02d}"


def periodo_label(periodo: str) -> str:
    d = _periodo_to_date(periodo)
    return f"{_MESES[d.month]} {d.year}"


def _add_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    y, m = divmod(total, 12)
    return y, m + 1


# ── Ventana por OS (dia_corte) ───────────────────────────────────────────────
def fecha_a_periodo(fecha: datetime.date, dia_corte: int) -> str:
    """Período "YYYYMM" al que pertenece una fecha de servicio según la ventana de
    la OS.

    - `dia_corte <= 1` (mes completo): el período es el mes calendario de la fecha.
    - `dia_corte = D > 1` (del D al D, convención "hacia atrás"): el período del mes
      M abarca ``[día D del mes M-1, día D del mes M)``. Por lo tanto una fecha con
      día >= D pertenece al mes siguiente. Ej. (D=20): 10/jul → 202607; 25/jun →
      202607; 10/jun → 202606. La ventana de 202607 es [20/jun, 20/jul).
    """
    y, m = fecha.year, fecha.month
    if dia_corte > 1 and fecha.day >= dia_corte:
        y, m = _add_month(y, m, 1)
    return f"{y}{m:02d}"


def ventana_periodo(periodo: str, dia_corte: int) -> tuple[datetime.date, datetime.date]:
    """Rango de fechas de servicio del período ``[inicio, fin)`` según la ventana.
    Sirve tanto para ubicar la prestación como para el plazo operativo de carga/cierre."""
    y = int(periodo[:4])
    m = int(periodo[4:6])
    if dia_corte <= 1:
        inicio = datetime.date(y, m, 1)
        fy, fm = _add_month(y, m, 1)
        fin = datetime.date(fy, fm, 1)
    else:
        py, pm = _add_month(y, m, -1)
        inicio = datetime.date(py, pm, dia_corte)
        fin = datetime.date(y, m, dia_corte)
    return inicio, fin


async def _get_dia_corte(db: AsyncSession, cod_obra: str) -> int:
    """`dia_corte` configurado en la OS (por NRO_OBRASOCIAL). Default 20 si no existe."""
    try:
        nro = _cod_obra_to_int(cod_obra)
    except HTTPException:
        return DIA_CORTE_DEFAULT
    dc = (await db.execute(
        select(ObrasSociales.dia_corte).where(ObrasSociales.NRO_OBRASOCIAL == nro)
    )).scalar_one_or_none()
    return int(dc) if dc else DIA_CORTE_DEFAULT


# ── Auto-cierre del período médico por dia_corte ─────────────────────────────
def periodo_medico_por_fecha(hoy: datetime.date, dia_corte: int) -> str:
    """Período que le toca cargar al médico HOY, según el `dia_corte` de la OS.

    El corte se evalúa en `dia_corte + 1`: el médico carga el período viejo TODO el
    día de corte inclusive, y recién pasa al siguiente al día siguiente. Mes completo
    (`dia_corte <= 1`) usa directamente el mes calendario de hoy."""
    if dia_corte <= 1:
        return f"{hoy.year}{hoy.month:02d}"
    y, m = hoy.year, hoy.month
    if hoy.day >= dia_corte + 1:
        y, m = _add_month(y, m, 1)
    return f"{y}{m:02d}"


async def _avanzar_puntero_a_objetivo(
    db: AsyncSession, row: PeriodoMedicoActual, dia_corte: int, hoy: datetime.date,
) -> int:
    """Avanza UN puntero hasta el período que le toca hoy (monotónico: nunca retrocede
    un avance manual ya adelantado). Cierra `estado_doctor` A→C de las cabeceras que
    quedan atrás, con el mismo scoping que `avanzar_periodo_medico` (si es un override,
    solo esa OS; si es el global, excluye las OS con override propio). Devuelve la
    cantidad de cabeceras cerradas (0 si no hubo que avanzar)."""
    objetivo = periodo_medico_por_fecha(hoy, dia_corte)
    if objetivo <= row.periodo:
        return 0

    cond = [
        FacturacionCMC.periodo < objetivo,
        FacturacionCMC.estado_doctor == DOCTOR_ABIERTA,
    ]
    if row.obra_social_id is not None:
        cond.append(FacturacionCMC.cod_obr == str(row.obra_social_id))
    else:
        overrides = (await db.execute(
            select(PeriodoMedicoActual.obra_social_id)
            .where(PeriodoMedicoActual.obra_social_id.is_not(None))
        )).scalars().all()
        if overrides:
            cond.append(FacturacionCMC.cod_obr.notin_([str(o) for o in overrides]))

    cabeceras = list((await db.execute(select(FacturacionCMC).where(*cond))).scalars().all())
    for c in cabeceras:
        c.estado_doctor = DOCTOR_CERRADA

    row.periodo = objetivo
    row.updated_at = datetime.datetime.utcnow()
    return len(cabeceras)


async def asegurar_periodo_medico_vigente(db: AsyncSession, cod_obra: str) -> str:
    """Safety-net lazy: antes de cargar/consultar, avanza el puntero de la OS si ya
    venció su `dia_corte`. Garantiza que un médico nunca cargue en un período vencido
    aunque el cron de cierre no haya corrido. Devuelve el período vigente."""
    nro = _cod_obra_to_int(cod_obra)
    row = (await db.execute(
        select(PeriodoMedicoActual).where(PeriodoMedicoActual.obra_social_id == nro)
    )).scalar_one_or_none()
    if row is None:
        # Sin override propio: hereda del global, pero no crea fila — solo el global
        # importa para esta OS hasta que alguien la fije explícitamente.
        row = (await db.execute(
            select(PeriodoMedicoActual).where(PeriodoMedicoActual.obra_social_id.is_(None))
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(422, "No hay período de médico configurado")

    dia_corte = await _get_dia_corte(db, cod_obra)
    await _avanzar_puntero_a_objetivo(db, row, dia_corte, datetime.date.today())
    await db.commit()
    return row.periodo


async def cerrar_periodos_medico_vencidos(db: AsyncSession) -> dict:
    """Recorre TODOS los punteros (global + overrides) y avanza los que ya vencieron
    según el `dia_corte` de su OS. Pensado para un cron diario; idempotente (correrlo
    de más no hace nada si ya está al día)."""
    hoy = datetime.date.today()
    rows = list((await db.execute(select(PeriodoMedicoActual))).scalars().all())

    detalle: list[dict] = []
    total_cabeceras = 0
    avanzadas = 0
    for row in rows:
        saliente = row.periodo
        if row.obra_social_id is not None:
            dia_corte = await _get_dia_corte(db, str(row.obra_social_id))
        else:
            dia_corte = DIA_CORTE_DEFAULT
        cerradas = await _avanzar_puntero_a_objetivo(db, row, dia_corte, hoy)
        if row.periodo != saliente:
            avanzadas += 1
            total_cabeceras += cerradas
            detalle.append({
                "obra_social_id": row.obra_social_id,
                "periodo_saliente": saliente,
                "periodo_nuevo": row.periodo,
                "cabeceras_cerradas": cerradas,
            })

    await db.commit()
    return {
        "os_avanzadas": avanzadas,
        "cabeceras_cerradas": total_cabeceras,
        "detalle": detalle,
    }


# ── Afiliados ────────────────────────────────────────────────────────────────
async def get_afiliado_by_dni(db: AsyncSession, dni: str) -> Optional[Afiliado]:
    stmt = select(Afiliado).where(Afiliado.dni == dni)
    return (await db.execute(stmt)).scalar_one_or_none()


async def buscar_afiliados(db: AsyncSession, q: str, limit: int) -> list[Afiliado]:
    """Autocomplete de afiliados por nombre (contiene) o DNI (prefijo)."""
    A = Afiliado
    cond = or_(A.nombre.ilike(f"%{q}%"), A.dni.ilike(f"{q}%"))
    stmt = select(A).where(cond).order_by(A.nombre.asc()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def crear_afiliado(db: AsyncSession, payload: AfiliadoCreate, usuario: str) -> Afiliado:
    if await get_afiliado_by_dni(db, payload.dni):
        raise HTTPException(409, f"Ya existe un afiliado con identificador {payload.dni}")
    af = Afiliado(dni=payload.dni, nombre=payload.nombre, usuario=usuario)
    db.add(af)
    await db.commit()
    await db.refresh(af)
    return af


async def _nombre_desde_afiliado(db: AsyncSession, dni: Optional[str]) -> Optional[str]:
    if not dni:
        return None
    af = await get_afiliado_by_dni(db, dni)
    if af is None:
        raise HTTPException(
            422, f"Afiliado '{dni}' no encontrado. Registrarlo primero."
        )
    return af.nombre


# ── Período activo de una obra social ────────────────────────────────────────
async def get_periodo_activo(db: AsyncSession, cod_obra: str) -> str:
    """Último período **cerrado** en `facturacion` para la OS + 1 mes.

    Sólo cuentan las cabeceras cerradas: las abiertas ("A") se crean al cargar
    prestaciones y no deben adelantar el período activo.
    """
    stmt = select(func.max(FacturacionCMC.periodo)).where(
        FacturacionCMC.cod_obr == cod_obra,
        FacturacionCMC.estado.in_(FACTURA_ESTADOS_CERRADOS),
    )
    ultimo = (await db.execute(stmt)).scalar_one_or_none()
    if not ultimo:
        raise HTTPException(
            422, "No se encontró período cerrado para esta obra social"
        )
    return _shift_periodo(ultimo, 1)


async def resolver_periodo_colegio_carga(
    db: AsyncSession, cod_obra: str, periodo_override: Optional[str]
) -> str:
    """Período destino de una carga del **colegio**.

    Sin override → automático (`get_periodo_activo` = último cerrado + 1). Con override
    (botón "editar período" del front) → el período elegido por el operador, para saltar
    meses sin movimiento (ej. última cerrada abril, mayo sin movimiento → cargar junio).

    Validación: el override debe ser >= el automático. Un período **anterior** se rechaza
    acá (si ya está cerrado, la reapertura se hace con un complemento). El caso de un
    período ya cerrado igual queda cubierto aguas abajo por `_gate_carga` (409).
    """
    automatico = await get_periodo_activo(db, cod_obra)
    if not periodo_override:
        return automatico
    if _periodo_to_date(periodo_override) < _periodo_to_date(automatico):
        raise HTTPException(
            422,
            f"El período {periodo_override} es anterior al primero disponible "
            f"({automatico}) para esta obra social. Si ya está cerrado, cargá un complemento.",
        )
    return periodo_override


# ── Punteros de período: médico (global + override) y colegio (derivado) ─────
async def get_periodo_medico(db: AsyncSession, cod_obra: str) -> str:
    """Período abierto para carga de médicos. Global con override por OS: primero se
    busca el override de la OS; si no hay, el global (`obra_social_id = NULL`)."""
    nro = _cod_obra_to_int(cod_obra)
    override = (await db.execute(
        select(PeriodoMedicoActual.periodo).where(PeriodoMedicoActual.obra_social_id == nro)
    )).scalar_one_or_none()
    if override:
        return override
    glob = (await db.execute(
        select(PeriodoMedicoActual.periodo).where(PeriodoMedicoActual.obra_social_id.is_(None))
    )).scalar_one_or_none()
    if glob:
        return glob
    raise HTTPException(422, "No hay período de médico configurado")


async def get_periodo_colegio(db: AsyncSession, cod_obra: str) -> str:
    """Período que el colegio está trabajando para la OS: el más antiguo liberado por
    los médicos (`estado_doctor='C'`) y aún abierto para el colegio (`estado='A'`). Si
    no hay ninguno, cae al último cerrado por el colegio + 1."""
    p = (await db.execute(
        select(func.min(FacturacionCMC.periodo)).where(
            FacturacionCMC.cod_obr == cod_obra,
            FacturacionCMC.estado_doctor == DOCTOR_CERRADA,
            FacturacionCMC.estado == FACTURA_ESTADO_ABIERTA,
        )
    )).scalar_one_or_none()
    if p:
        return p
    return await get_periodo_activo(db, cod_obra)


# ── Mapeo de identificadores ─────────────────────────────────────────────────
def _cod_obra_to_int(cod_obra: str) -> int:
    try:
        return int(cod_obra)
    except (ValueError, TypeError):
        raise HTTPException(422, f"Obra social '{cod_obra}' no es numérica")


async def check_medico_activo(db: AsyncSession, cod_medico: str) -> ListadoMedico:
    """Valida existencia + actividad de un NRO_SOCIO. Acepta médico O clínica
    (organización): no filtra `es_organizacion` porque `cod_med` (el payee) puede ser
    cualquiera de los dos. El llamador decide según `medico.es_organizacion`."""
    try:
        nro = int(cod_medico)
    except (ValueError, TypeError):
        raise HTTPException(422, f"Código de médico '{cod_medico}' inválido")
    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico or medico.EXISTE != "S":
        raise HTTPException(422, f"Médico {cod_medico} inexistente o inactivo")
    return medico


async def check_medico_ejecutor(db: AsyncSession, cod_ejecutor: str) -> ListadoMedico:
    """El ejecutor debe ser un médico REAL (no una organización), activo. Su especialidad
    determina el precio cuando el payee es una clínica."""
    medico = await check_medico_activo(db, cod_ejecutor)
    if medico.es_organizacion:
        raise HTTPException(
            422, f"El ejecutor {cod_ejecutor} es una clínica/organización; debe ser un médico"
        )
    return medico


async def check_clinica(db: AsyncSession, cod_clinica: int) -> ListadoMedico:
    """La clínica enviada en `cod_clinica` debe ser una organización activa."""
    org = await check_medico_activo(db, str(cod_clinica))
    if not org.es_organizacion:
        raise HTTPException(
            422, f"El código {cod_clinica} no es una clínica/organización"
        )
    return org


class PrestadorResuelto(NamedTuple):
    """Cómo se persiste una prestación a partir de lo que seleccionó el front."""
    cod_med: str                    # → detalle_facturacion.cod_med (SIEMPRE el médico)
    cod_clinica: Optional[int]      # → detalle_facturacion.cod_clinica (None si no hay)
    tipo_orden: Optional[str]       # → 'S' si hay clínica (marca legacy, ver constante)
    tipo: Optional[str]             # → `tipo` forzado; None = usar la categoría del código
    medico: ListadoMedico           # el médico — cobra Y cotiza (por su especialidad)

    @property
    def es_sanatorio(self) -> bool:
        """La clínica fue el PRESTADOR (no sólo el ámbito). Se lee de `tipo`, no de
        `tipo_orden` — este último vale 'S' en los dos casos con clínica."""
        return self.tipo == TIPO_SANATORIO


async def resolver_prestador(
    db: AsyncSession,
    cod_medico: str,
    cod_medico_ejecutor: Optional[str],
    cod_clinica: Optional[int] = None,
) -> PrestadorResuelto:
    """Traduce la selección del front a las columnas de la fila.

    El **médico es siempre quien cobra**, así que `cod_med` termina siendo siempre un
    médico real. Hay tres combinaciones:

    1. **`cod_medico` es una clínica** (`es_organizacion=1`) → SANATORIO.
       `cod_medico_ejecutor` es obligatorio y pasa a `cod_med` (cobra y cotiza con su
       especialidad); la clínica baja a `cod_clinica`; **`tipo='Sanatorio'`**.
    2. **`cod_medico` es un médico, sin `cod_clinica`** → flujo de siempre. `cod_med` es
       él mismo, sin clínica; `tipo` = la categoría del código.
    3. **`cod_medico` es un médico + viene `cod_clinica`** → el médico factura por sí
       mismo pero la prestación se hizo en esa clínica: `cod_med` es él, la clínica se
       guarda en `cod_clinica` y **`tipo='Honorarios individuales'`** (forzado, no importa
       la categoría del código) — la clínica es sólo el ámbito, no un sanatorio.

    **`tipo` es el discriminador** entre 1 y 3. `tipo_orden='S'` se estampa en AMBOS
    (marca legacy de "hubo clínica", ver `TIPO_ORDEN_SANATORIO`) y no decide nada.

    `cod_med_ejecutor` NO se persiste en ningún caso: es solo la señal de entrada.
    """
    seleccionado = await check_medico_activo(db, cod_medico)

    # Caso 1 — la clínica es el prestador seleccionado.
    if seleccionado.es_organizacion:
        if not cod_medico_ejecutor:
            raise HTTPException(
                422,
                "Cuando la prestación se factura bajo una clínica se debe indicar el "
                "médico ejecutor, que es quien cobra",
            )
        medico = await check_medico_ejecutor(db, cod_medico_ejecutor)
        return PrestadorResuelto(
            cod_med=str(medico.NRO_SOCIO),
            cod_clinica=int(seleccionado.NRO_SOCIO),
            tipo_orden=TIPO_ORDEN_SANATORIO,
            tipo=TIPO_SANATORIO,
            medico=medico,
        )

    if cod_medico_ejecutor and str(cod_medico_ejecutor) != str(cod_medico):
        raise HTTPException(
            422,
            "El médico ejecutor solo se indica cuando el prestador seleccionado es una "
            "clínica",
        )

    # Caso 3 — médico que factura por sí mismo, con la clínica como ámbito. Lleva la
    # misma marca `tipo_orden='S'` que el caso 1 (hubo clínica); los separa `tipo`.
    if cod_clinica:  # 0 = sentinel legacy "sin clínica"
        await check_clinica(db, cod_clinica)
        return PrestadorResuelto(
            cod_med=str(seleccionado.NRO_SOCIO),
            cod_clinica=int(cod_clinica),
            tipo_orden=TIPO_ORDEN_SANATORIO,
            tipo=CATEGORIA_HONORARIOS_INDIVIDUALES,
            medico=seleccionado,
        )

    # Caso 2 — médico solo.
    return PrestadorResuelto(
        cod_med=str(seleccionado.NRO_SOCIO),
        cod_clinica=None,
        tipo_orden=None,
        tipo=None,
        medico=seleccionado,
    )


async def _get_nomenclador_by_codigo(db: AsyncSession, codigo: str) -> NomencladorCMC:
    stmt = select(NomencladorCMC).where(NomencladorCMC.codigo == codigo)
    nom = (await db.execute(stmt)).scalar_one_or_none()
    if not nom:
        raise HTTPException(404, f"Código '{codigo}' no encontrado en el nomenclador")
    return nom


# ── Resolución de precio — wrapper sobre lookup_precio ───────────────────────
async def resolver_precio(
    db: AsyncSession,
    cod_obra: str,
    medico: ListadoMedico,
    codigo: str,
    fecha: datetime.date,
    via: str = service_vias.VIA_TRADICIONAL,
) -> PrecioResponse:
    """Delega precio Y habilitación en el lookup del módulo nomenclador.

    Errores de mapeo de identificadores → HTTPException (404/422).
    Falta de precio / habilitación / fecha / vía no aplicable → admitido=False + motivo.
    """
    obra_social_nro = _cod_obra_to_int(cod_obra)
    nomenclador = await _get_nomenclador_by_codigo(db, codigo)

    try:
        out = await service_nm.lookup_precio(
            nomenclador.id, obra_social_nro, fecha, medico.ID, db, via=via,
        )
    except PrecioLookupError as e:
        return PrecioResponse(
            admitido=False, motivo=e.message,
            honorarios=Decimal("0"), gastos=Decimal("0"), ayudante=Decimal("0"),
            descripcion="", fuente=FUENTE_PRECIO, via=via,
        )

    def _suma(concepto: str) -> Decimal:
        return quantize_money(sum(
            (c.subtotal for c in out.componentes if c.concepto == concepto),
            Decimal("0"),
        ))

    cantidad_ayudantes = await service_nm.get_cantidad_ayudantes(
        db, obra_social_nro, nomenclador.id, fecha
    )

    return PrecioResponse(
        honorarios=_suma("Honorarios"),
        gastos=_suma("Gastos"),
        ayudante=_suma("Ayudante"),
        descripcion=out.descripcion or "",
        fuente=FUENTE_PRECIO,
        nivel=out.nivel,
        snapshot=[c.model_dump(mode="json") for c in out.componentes],
        admitido=True,
        por_presupuesto=out.por_presupuesto,
        cantidad_ayudantes=cantidad_ayudantes,
        via=out.via,
        nivel_cotizado=out.nivel_cotizado,
    )


async def codigos_habilitados_medico(
    db: AsyncSession, cod_medico: str, q: Optional[str] = None,
) -> list[dict]:
    """Códigos que un médico puede facturar. Delega el alcance completo (especialidad +
    excepciones individuales + sin restricción) en el módulo nomenclador — mismo
    patrón de delegación que `resolver_precio` hacia `lookup_precio`."""
    medico = await check_medico_activo(db, cod_medico)
    return await service_nm.listar_codigos_habilitados(db, medico, q)


# ── Tope de ayudantes por (código, OS) ───────────────────────────────────────
# ── Categorización (`tipo`) ──────────────────────────────────────────────────
async def _get_categoria(db: AsyncSession, codigo: str) -> Optional[str]:
    stmt = select(NomencladorCMC.categoria).where(NomencladorCMC.codigo == codigo)
    return (await db.execute(stmt)).scalar_one_or_none()


async def derivar_tipo(
    db: AsyncSession, cod_nomenclador: str, tipo_forzado: Optional[str]
) -> Optional[str]:
    """`tipo` único de la prestación. Lo decide el prestador
    (`resolver_prestador`) y, si éste no fuerza nada, la `categoria` del código
    (Consulta | Practica | Honorarios individuales); NULL si el código no tiene.

    Los dos casos que fuerzan `tipo` (ver `resolver_prestador`): la clínica como
    prestador → 'Sanatorio'; el médico con clínica como ámbito → 'Honorarios
    individuales'.

    Nota (2026-07-31): antes el driver era `payee.es_organizacion`. Hoy el payee
    (`cod_med`) es siempre un médico y la clínica vive en `cod_clinica`.
    """
    if tipo_forzado:
        return tipo_forzado
    return await _get_categoria(db, cod_nomenclador)


async def _gasto_forzado_a_cero(
    db: AsyncSession, cod_nomenclador: str, es_sanatorio: bool, tipo_calculo: str
) -> bool:
    """True → los gastos de la fila deben guardarse en 0.

    Bajo sanatorio los gastos los factura la clínica, no el médico. Aplica sólo a los
    códigos de categoría 'Honorarios individuales' y sólo en cálculo automático — en
    manual el operador manda (decisión usuario 2026-07-31).

    El front ya envía `gastos=0` en ese escenario; esto lo hace cumplir del lado del
    backend para que ningún camino (edición, carga del médico, API directa) lo saltee.
    """
    if not es_sanatorio or tipo_calculo != "A":
        return False
    return await _get_categoria(db, cod_nomenclador) == CATEGORIA_HONORARIOS_INDIVIDUALES



# ── Normalizaciones y cálculo ────────────────────────────────────────────────
def fecha_para_precio(fecha: Optional[datetime.date]) -> datetime.date:
    """Fecha a usar para cotizar (lookup de precio + habilitación).

    Cuando la carga es por `cantidad` (equipo/lote) el operador no puede asignarle
    una fecha de práctica a cada unidad — en ese caso `fecha_practica` se guarda en
    NULL (no se inventa un valor) y para el precio se usa HOY, así siempre trae el
    valor VIGENTE más actualizado en vez del que regía al primer día del período
    (que podría estar desactualizado si hubo un cambio de precio a mitad de mes).
    """
    return fecha if fecha is not None else datetime.date.today()


def _dec(x) -> Decimal:
    """Coerce a Decimal y redondea a 2 decimales. Las columnas de montos son `double`
    en la DB legacy y vuelven como float al leer del ORM (con ruido de precisión
    binaria) → `quantize_money` evita tanto errores float↔Decimal como decimales de más."""
    return quantize_money(x)


def _aplicar_porcentaje(
    h: Decimal, g: Decimal, a: Decimal, porcentaje: int
) -> tuple[Decimal, Decimal, Decimal]:
    """Escala los montos por el porcentaje (opción de pago)."""
    f = Decimal(porcentaje) / Decimal(100)
    return (
        quantize_money(_dec(h) * f),
        quantize_money(_dec(g) * f),
        quantize_money(_dec(a) * f),
    )


def tpo_funcion_derivado(h: Decimal, g: Decimal, a: Decimal) -> str:
    """Deriva el `tpo_funcion` legacy (H/HG/G/A) de qué montos son > 0, solo para que
    liquidación y lotes (que aún leen esa columna) sigan funcionando durante la
    coexistencia. El modelo nuevo no usa este campo en la API."""
    if h > 0 and g > 0:
        return "HG"
    if h > 0:
        return "H"
    if g > 0:
        return "G"
    if a > 0:
        return "A"
    return "H"


def _derivar_tipo_prestador(h: Decimal, g: Decimal, a: Decimal) -> Optional[str]:
    """Badge "Tipo de prestador" de una fila, según qué monto está en >0. Misma
    prioridad que `tpo_funcion_derivado` (ayudante gana sobre honorarios/gastos)."""
    if a > 0:
        return "Ayudante"
    if h > 0:
        return "Medico"
    if g > 0:
        return "Gastos"
    return None


def calcular_importe_total(
    h: Decimal, g: Decimal, a: Decimal, cantidad: int, sesion: int
) -> Decimal:
    return quantize_money(
        (_dec(h) + _dec(g) + _dec(a)) * Decimal(cantidad) * Decimal(sesion)
    )




async def _montos_de_item(
    db: AsyncSession,
    item: PrestacionItem | PrestacionUpdate,
    cod_obra: str,
    medico: ListadoMedico,
    cod_nomenclador: str,
    tipo_calculo: str,
    fecha: datetime.date,
) -> tuple[Decimal, Decimal, Decimal, Optional[list[dict]]]:
    """Resuelve los montos base (h, g, a) SIN porcentaje, y el snapshot.

    El concepto que el cliente manda en > 0 es el que se factura (médico/gastos/ayudante
    queda implícito). En modo automático el monto sale del lookup para cada concepto
    marcado en > 0; en manual se usan los montos enviados tal cual.
    """
    hi = item.honorarios or Decimal("0")
    gi = item.gastos or Decimal("0")
    ai = item.ayudante or Decimal("0")
    if tipo_calculo == "A":
        via = item.via or service_vias.VIA_TRADICIONAL
        precio = await resolver_precio(db, cod_obra, medico, cod_nomenclador, fecha, via=via)
        if not precio.admitido:
            raise HTTPException(422, precio.motivo)
        # Por presupuesto: el lookup admite con H/G/A en 0; el monto lo informa la OS
        # y lo carga el operador a mano (montos del item).
        if precio.por_presupuesto:
            return hi, gi, ai, precio.snapshot
        # Para cada concepto marcado en > 0 por el front, usar el valor autoritativo del lookup.
        h = precio.honorarios if hi > 0 else Decimal("0")
        g = precio.gastos if gi > 0 else Decimal("0")
        a = precio.ayudante if ai > 0 else Decimal("0")
        return h, g, a, precio.snapshot
    return hi, gi, ai, None


# ── Guardado ─────────────────────────────────────────────────────────────────
async def guardar_prestaciones(
    db: AsyncSession,
    payload: PrestacionesCreate,
    usuario: str,
    actor: str = ORIGEN_COLEGIO,
) -> GuardadoResponse:
    if actor == ORIGEN_MEDICO:
        # El médico NO elige período: carga siempre en el período de médicos vigente
        # (puntero `periodo_medico_actual`, global/override). Cuando se le cierra el
        # período, el puntero avanza y sus cargas caen automáticamente en el siguiente.
        # La fecha_practica es solo dato del servicio, no determina el período.
        # Safety-net: si el puntero ya venció su dia_corte, se avanza acá mismo — así
        # el médico nunca carga en un período vencido aunque el cron no haya corrido.
        await asegurar_periodo_medico_vigente(db, payload.obra_social)
        periodo = await get_periodo_medico(db, payload.obra_social)
    else:
        # Colegio: por defecto "última factura cerrada + 1"; con `payload.periodo` (botón
        # "editar período") el operador puede saltar a un período posterior sin movimiento
        # intermedio. La fecha_practica NO determina el período. Para cargar DENTRO de un
        # complemento existente, usar `guardar_prestaciones_complementaria`.
        periodo = await resolver_periodo_colegio_carga(
            db, payload.obra_social, payload.periodo
        )

    # La fase del actor debe estar abierta para cargar. Si la última versión de la
    # factura está cerrada, `_gate_carga` corta con 409: hay que abrir un complemento
    # (`abrir_complemento`) antes de poder cargar de nuevo en ese período.
    cabecera_actual = await _get_factura(db, payload.obra_social, periodo)
    _gate_carga(cabecera_actual, actor)
    # Versión destino: la de la cabecera abierta actual (siempre v1 en este flujo,
    # salvo el caso borde de una cabecera recién creada); 1 si aún no hay cabecera.
    version_destino = cabecera_actual.version if cabecera_actual is not None else 1

    return await _insertar_prestaciones(
        db, cod_obra=payload.obra_social, periodo=periodo, version_destino=version_destino,
        items=payload.prestaciones, usuario=usuario, actor=actor,
    )


async def guardar_prestaciones_complementaria(
    db: AsyncSession,
    payload: PrestacionesComplementariaCreate,
    usuario: str,
) -> GuardadoResponse:
    """Carga del Colegio DENTRO de una factura complementaria ya creada con
    `POST /facturas/complemento`. A diferencia de la carga normal, la cabecera NO se
    crea sola con la primera prestación — ya existe de antes, así que el front la
    referencia directamente por `factura_id` (el `id_prestaciones` que devolvió
    `POST /facturas/complemento`, o `GET /facturas?solo_complementos=true`)."""
    factura = await db.get(FacturacionCMC, payload.factura_id)
    if factura is None:
        raise HTTPException(404, "Factura no encontrada")
    if factura.version <= 1:
        raise HTTPException(
            422, "Esta factura no es un complemento — para cargar en la factura "
                 "original usá POST /prestaciones",
        )
    if factura.estado != FACTURA_ESTADO_ABIERTA:
        raise HTTPException(409, "La factura complementaria ya está cerrada")

    return await _insertar_prestaciones(
        db, cod_obra=factura.cod_obr, periodo=factura.periodo, version_destino=factura.version,
        items=payload.prestaciones, usuario=usuario, actor=ORIGEN_COLEGIO,
    )


async def _insertar_prestaciones(
    db: AsyncSession,
    *,
    cod_obra: str,
    periodo: str,
    version_destino: int,
    items: list[PrestacionItem],
    usuario: str,
    actor: str,
) -> GuardadoResponse:
    """Núcleo de inserción compartido por `guardar_prestaciones` (carga normal) y
    `guardar_prestaciones_complementaria` (carga en complemento). El llamador ya
    resolvió y validó `periodo`/`version_destino` (gate de fase, existencia de
    cabecera, etc.) — acá solo se arman y persisten las filas."""
    # En carga múltiple (equipo) no se permite repetir el mismo prestador. La identidad
    # del prestador es (payee, ejecutor): bajo una misma clínica pueden convivir varios
    # médicos ejecutores distintos, pero no dos filas idénticas.
    if len(items) > 1:
        cods = [(i.cod_medico, i.cod_medico_ejecutor) for i in items]
        if len(set(cods)) != len(cods):
            raise HTTPException(422, "No se permite repetir el mismo prestador en el equipo")

    # NOTA: la cantidad de ayudantes NO se valida como tope. El máximo de
    # `nm_valores.cantidad_ayudantes` es solo una REFERENCIA que se muestra al operador
    # (viene en la respuesta del precio); el operador puede cargar los ayudantes que
    # necesite sin bloqueo (decisión usuario 2026-07-19).

    ids: list[int] = []
    total_acum = Decimal("0")
    filas: list[DetalleFacturacionCMC] = []
    cabeza_id: Optional[int] = None  # fila del médico (honorarios > 0) = cabeza del equipo

    for i, item in enumerate(items):
        # Traduce la selección del front a columnas: el médico siempre termina en
        # `cod_med` (cobra y cotiza) y la clínica, si la hubo, en `cod_clinica`.
        prestador = await resolver_prestador(
            db, item.cod_medico, item.cod_medico_ejecutor, item.cod_clinica
        )
        medico_precio = prestador.medico
        # Cotiza con la fecha informada o, si no vino (carga por cantidad), con HOY —
        # nunca se inventa una fecha de práctica para guardar (ver `fecha_para_precio`).
        fecha_precio = fecha_para_precio(item.fecha_practica)

        if item.cod_nomenclador in CODIGOS_NO_PERMITIDOS:
            raise HTTPException(422, "Código no permitido")

        nombre_paciente = await _nombre_desde_afiliado(db, item.dni_paciente)

        # El precio sale de la especialidad del médico ejecutor (= el propio médico si el
        # payee no es una clínica).
        h_base, g_base, a_base, snapshot = await _montos_de_item(
            db, item, cod_obra, medico_precio,
            item.cod_nomenclador, item.tipo_calculo, fecha_precio,
        )
        # Sanatorio + Honorarios individuales + automático ⇒ los gastos los factura la
        # clínica: se fuerzan a 0 antes de aplicar el porcentaje.
        if await _gasto_forzado_a_cero(
            db, item.cod_nomenclador, prestador.es_sanatorio, item.tipo_calculo
        ):
            g_base = Decimal("0")
        h, g, a = _aplicar_porcentaje(h_base, g_base, a_base, item.porcentaje)
        total = calcular_importe_total(h, g, a, item.cantidad, item.sesion)
        # Importe 0 permitido: un concepto en 0 no afecta la suma de la liquidación.

        tipo = await derivar_tipo(db, item.cod_nomenclador, prestador.tipo)

        row = DetalleFacturacionCMC(
            calculo_snapshot=snapshot,
            periodo=periodo,
            cod_obr=cod_obra,
            # Siempre el médico (nunca la clínica) — es quien recibe la plata.
            cod_med=prestador.cod_med,
            # `cod_med_ejecutor` ya no se persiste: era solo la señal de entrada.
            cod_med_ejecutor=None,
            nro_orden="0",  # placeholder — la columna es NOT NULL; se espeja el PK abajo
            cod_nom=item.cod_nomenclador,
            via=item.via,
            tipo=tipo,
            grupo_equipo_id=item.grupo_equipo_id,
            # tpo_funcion derivado SOLO para coexistencia (liquidación/lotes lo leen).
            tpo_funcion=tpo_funcion_derivado(h, g, a),
            sesion=item.sesion,
            cantidad=item.cantidad,
            honorarios=h,
            gastos=g,
            ayudante=a,
            importe_total=total,
            manual=item.tipo_calculo,
            dni_p=item.dni_paciente,
            nom_ape_p=nombre_paciente,
            # Derivados del prestador seleccionado: la clínica y su marca 'S'.
            cod_clinica=prestador.cod_clinica,
            tipo_orden=prestador.tipo_orden,
            # NULL cuando el operador no la cargó (carga por cantidad) — no se rellena
            # con el primer día del período, eso solo se usaba para cotizar (arriba).
            fecha_practica=item.fecha_practica,
            autorizacion=item.autorizacion,
            porc=item.porcentaje,
            estado="A",
            origen_carga=actor,
            usuario=usuario,
            version=version_destino,
        )
        db.add(row)
        await db.flush()  # asigna id_detalle_prestaciones (PK)
        # `nro_orden` ya no es un contador propio (el PK alcanza como identificador
        # único) — se deja igual al PK solo para no dejar NULL una columna NOT NULL
        # legacy que liquidación (`nro_orden_cmc`) y lotes todavía leen para mostrar.
        row.nro_orden = str(row.id_detalle_prestaciones)
        ids.append(row.id_detalle_prestaciones)
        total_acum += total
        filas.append(row)
        if cabeza_id is None and h > 0:
            cabeza_id = row.id_detalle_prestaciones

    # Vínculo de equipo: en POST multi-ítem, las filas sin grupo explícito apuntan a la
    # fila del médico (cabeza). El médico también queda con grupo = su propio id, así
    # `WHERE grupo_equipo_id = <id>` trae todo el equipo.
    if len(items) > 1 and cabeza_id is not None:
        for row in filas:
            if row.grupo_equipo_id is None:
                row.grupo_equipo_id = cabeza_id

    # Cabecera de facturación abierta para la OS+período. Idempotente: para la carga
    # normal la crea si es la primera prestación del período; para la complementaria
    # ya existe (se creó con POST /facturas/complemento), así que no hace nada.
    await _ensure_factura_abierta(db, cod_obra, periodo, usuario)

    await db.commit()
    return GuardadoResponse(ids=ids, importe_total=total_acum, periodo=periodo)


# ── Listado de facturas / períodos ───────────────────────────────────────────
async def listar_facturas(
    db: AsyncSession,
    *,
    cod_obra: Optional[str] = None,
    periodo: Optional[str] = None,
    usuario: Optional[str] = None,
    estado: Optional[str] = None,
    solo_complementos: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[FacturacionCMC], int]:
    """Cabeceras de `facturacion`, de la más reciente a la más antigua (por
    `created`). Soporta filtros por obra social, período, operador (usuario),
    estado, versión (`solo_complementos`), búsqueda por nro de factura (`q`) y
    paginación. Devuelve las filas y el total de coincidencias."""
    F = FacturacionCMC
    filtros = []
    if cod_obra is not None:
        filtros.append(F.cod_obr == cod_obra)
    if periodo is not None:
        filtros.append(F.periodo == periodo)
    if usuario is not None:
        filtros.append(F.usuario == usuario)
    if estado is not None:
        filtros.append(F.estado == estado)
    if solo_complementos is True:
        filtros.append(F.version > 1)
    elif solo_complementos is False:
        filtros.append(F.version == 1)
    if q:
        like = f"%{q}%"
        filtros.append(
            F.nro_factura.ilike(like)
            | F.nro_factura_2.ilike(like)
            | F.nro_factura_3.ilike(like)
        )

    total = (
        await db.execute(select(func.count()).select_from(F).where(*filtros))
    ).scalar_one()

    stmt = (
        select(F).where(*filtros)
        .order_by(F.created.desc(), F.id_prestaciones.desc())
        .limit(limit).offset(offset)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def calcular_importes_abiertos(
    db: AsyncSession, rows: list[FacturacionCMC],
) -> dict[int, Decimal]:
    """Total EN VIVO (suma de `detalle_facturacion` con estado 'A') para las cabeceras
    de `rows` que están abiertas. `FacturacionCMC.importe` (persistido) solo se escribe
    al cerrar (`cerrar_periodo`) — mientras la factura está abierta vale siempre 0, así
    que `listar_facturas` necesita este cálculo aparte para no mostrar $0 en una
    factura (normal o complemento) con carga en curso. Devuelve `{id_prestaciones:
    total}` solo para las abiertas; las cerradas no se tocan, se usa su `importe`
    persistido tal cual (ya es el total real, fijado al cerrar)."""
    abiertas = [r for r in rows if r.estado == FACTURA_ESTADO_ABIERTA]
    if not abiertas:
        return {}
    M = DetalleFacturacionCMC
    claves = {(r.cod_obr, r.periodo, r.version) for r in abiertas}
    stmt = (
        select(M.cod_obr, M.periodo, M.version, func.coalesce(func.sum(M.importe_total), 0))
        .where(tuple_(M.cod_obr, M.periodo, M.version).in_(claves), M.estado == "A")
        .group_by(M.cod_obr, M.periodo, M.version)
    )
    sumas = {
        (cod_obr, periodo, version): Decimal(str(total or 0))
        for cod_obr, periodo, version, total in (await db.execute(stmt)).all()
    }
    return {
        r.id_prestaciones: sumas.get((r.cod_obr, r.periodo, r.version), Decimal("0"))
        for r in abiertas
    }


# ── Detalle de factura agrupado por prestador ────────────────────────────────
async def obtener_factura_detalle(db: AsyncSession, factura_id: int) -> dict:
    """Detalle de una factura (cabecera `facturacion`): sus prestaciones
    (`detalle_facturacion` de la misma OS+período **y versión**, excluyendo anuladas)
    agrupadas por prestador (`cod_med`), con datos de socio/matrícula y totales recalculados
    on-the-fly — nunca se confía en un total persistido. El filtro por `version` aísla cada
    factura de sus complementos (todas quedan en 'C' y comparten cod_obr+periodo)."""
    factura = await db.get(FacturacionCMC, factura_id)
    if factura is None:
        raise HTTPException(404, "Factura no encontrada")

    M = DetalleFacturacionCMC
    stmt = (
        select(M)
        .where(
            M.cod_obr == factura.cod_obr, M.periodo == factura.periodo,
            M.version == factura.version, M.estado != "X",
        )
        .order_by(M.fecha_practica.asc(), M.id_detalle_prestaciones.asc())
    )
    rows = list((await db.execute(stmt)).scalars().all())

    # Batch de médicos (socio/matrícula) para todos los cod_med (el médico que cobra),
    # las cod_clinica (organizaciones, para mostrar su nombre) y los cod_med_ejecutor
    # legacy que quedaron persistidos — un solo IN para los tres.
    nros: set[int] = set()
    for r in rows:
        for cod in (r.cod_med, r.cod_clinica, r.cod_med_ejecutor):
            if not cod:  # None y el sentinel legacy `cod_clinica = 0` ("sin clínica")
                continue
            try:
                nros.add(int(cod))
            except (TypeError, ValueError):
                continue
    medicos: dict[str, ListadoMedico] = {}
    if nros:
        med_rows = (await db.execute(
            select(ListadoMedico).where(ListadoMedico.NRO_SOCIO.in_(nros))
        )).scalars().all()
        medicos = {str(m.NRO_SOCIO): m for m in med_rows}

    # Batch de categorías del nomenclador — fallback para filas legacy con `tipo` NULL
    # (la columna se puebla recién al cargar por el módulo nuevo; el histórico de CMC
    # nunca la tuvo). Se deriva on-the-fly con la misma regla que `derivar_tipo`, sin
    # tocar la fila persistida.
    # cod_clinica == 0 = "sin clínica" (sentinel legacy, no clínica id real) — misma
    # regla truthy que `derivar_tipo`.
    codigos = {r.cod_nom for r in rows if r.tipo is None and not r.cod_clinica and r.cod_nom}
    categorias: dict[str, str] = {}
    if codigos:
        cat_rows = (await db.execute(
            select(NomencladorCMC.codigo, NomencladorCMC.categoria)
            .where(NomencladorCMC.codigo.in_(codigos))
        )).all()
        categorias = {codigo: cat for codigo, cat in cat_rows if cat}

    def _tipo_de(r: DetalleFacturacionCMC) -> Optional[str]:
        if r.tipo is not None:
            return r.tipo
        if r.cod_clinica:
            return "Sanatorio"
        return categorias.get(r.cod_nom)

    # Agrupar por cod_med preservando el orden de aparición (ya viene ordenado por fecha).
    # cod_med puede volver como int en filas legacy (columna declarada String pero
    # tipada INT en la DB) → normalizar a str antes de usarlo como clave/lookup.
    grupos: dict[str, dict] = {}
    orden_grupos: list[str] = []
    for r in rows:
        cod_med = str(r.cod_med)
        if cod_med not in grupos:
            medico = medicos.get(cod_med)
            grupos[cod_med] = {
                "cod_medico": cod_med,
                "nombre": medico.NOMBRE if medico else None,
                "matricula": medico.MATRICULA_PROV if medico else None,
                # Legacy: el payee ya nunca es una clínica (el médico es siempre quien
                # cobra). Queda en False salvo en filas históricas cargadas con el
                # modelo viejo. La clínica ahora se informa por prestación (`cod_clinica`).
                "pago_a_clinica": bool(medico.es_organizacion) if medico else False,
                "cantidad_prestaciones": 0,
                "total_cantidad": 0,
                "total_honorarios": Decimal("0"),
                "total_gastos": Decimal("0"),
                "total_subtotal": Decimal("0"),
                "prestaciones": [],
            }
            orden_grupos.append(cod_med)
        g = grupos[cod_med]
        h, ga, a = _dec(r.honorarios), _dec(r.gastos), _dec(r.ayudante)
        subtotal = _dec(r.importe_total)
        g["cantidad_prestaciones"] += 1
        g["total_cantidad"] += r.cantidad or 0
        g["total_honorarios"] += h
        g["total_gastos"] += ga
        g["total_subtotal"] += subtotal
        ejecutor = medicos.get(str(r.cod_med_ejecutor)) if r.cod_med_ejecutor else None
        clinica = medicos.get(str(r.cod_clinica)) if r.cod_clinica else None
        g["prestaciones"].append({
            "id": r.id_detalle_prestaciones,
            "periodo": r.periodo,
            "autorizacion": r.autorizacion,
            "fecha_practica": r.fecha_practica,
            "codigo": r.cod_nom,
            "nro_afiliado": r.dni_p,
            # Médico ejecutor — legacy, sólo en filas viejas (hoy el ejecutor ES cod_med).
            "cod_medico_ejecutor": str(r.cod_med_ejecutor) if r.cod_med_ejecutor else None,
            "nombre_ejecutor": ejecutor.NOMBRE if ejecutor else None,
            # Clínica bajo la que se ejecutó (tipo_orden='S'); None si el médico factura solo.
            "cod_clinica": r.cod_clinica or None,
            "nombre_clinica": clinica.NOMBRE if clinica else None,
            "tipo_orden": r.tipo_orden,
            "cantidad": r.cantidad,
            "sesion": r.sesion,
            "porcentaje": r.porc,
            "honorarios": r.honorarios,
            "gastos": r.gastos,
            "tipo_prestador": _derivar_tipo_prestador(h, ga, a),
            "subtotal": r.importe_total,
            "tipo": _tipo_de(r),
            "revisado": r.revisado,
            "estado": r.estado,
        })

    # Orden final de grupos: por nombre del médico, fallback cod_medico.
    orden_grupos.sort(key=lambda c: (grupos[c]["nombre"] or "", c))
    prestadores = [grupos[c] for c in orden_grupos]

    return {
        "id_factura": factura.id_prestaciones,
        "periodo": factura.periodo,
        "periodo_label": periodo_label(factura.periodo) if factura.periodo else "",
        "cod_obra": factura.cod_obr,
        "estado": factura.estado,
        "estado_doctor": factura.estado_doctor,
        "version": factura.version,
        "es_complemento": factura.version > 1,
        "total_prestaciones": len(rows),
        "total_importe": sum((g["total_subtotal"] for g in prestadores), Decimal("0")),
        "prestadores": prestadores,
    }


# ── Listado ──────────────────────────────────────────────────────────────────
async def listar_prestaciones(
    db: AsyncSession,
    *,
    cod_obra: Optional[str] = None,
    periodo: Optional[str] = None,
    cod_medico: Optional[str] = None,
    cod_nomenclador: Optional[str] = None,
    estado: Optional[str] = None,
    tipo: Optional[str] = None,
    grupo_equipo_id: Optional[int] = None,
    dni_paciente: Optional[str] = None,
    nombre_paciente: Optional[str] = None,
    fecha_desde: Optional[datetime.date] = None,
    fecha_hasta: Optional[datetime.date] = None,
    revisado: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PrestacionRead], int]:
    M = DetalleFacturacionCMC
    filtros = []
    if revisado is not None:
        filtros.append(M.revisado == revisado)
    if cod_obra is not None:
        filtros.append(M.cod_obr == cod_obra)
    if periodo is not None:
        filtros.append(M.periodo == periodo)
    if cod_medico is not None:
        filtros.append(M.cod_med == cod_medico)
    if cod_nomenclador is not None:
        filtros.append(M.cod_nom == cod_nomenclador)
    if estado is not None:
        filtros.append(M.estado == estado)
    if tipo is not None:
        filtros.append(M.tipo == tipo)
    if grupo_equipo_id is not None:
        filtros.append(M.grupo_equipo_id == grupo_equipo_id)
    if dni_paciente is not None:
        filtros.append(M.dni_p == dni_paciente)
    if nombre_paciente is not None:
        filtros.append(M.nom_ape_p.ilike(f"%{nombre_paciente}%"))
    if fecha_desde is not None:
        filtros.append(M.fecha_practica >= fecha_desde)
    if fecha_hasta is not None:
        filtros.append(M.fecha_practica <= fecha_hasta)
    if q:
        like = f"%{q}%"
        filtros.append(
            M.cod_med.ilike(like) | M.cod_nom.ilike(like)
            | M.nom_ape_p.ilike(like) | M.nro_orden.ilike(like)
        )

    total = (
        await db.execute(select(func.count()).select_from(M).where(*filtros))
    ).scalar_one()

    stmt = (
        select(M).where(*filtros)
        .order_by(M.fecha_practica.desc(), M.id_detalle_prestaciones.desc())
        .limit(limit).offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return await _to_prestacion_read_list(db, rows), total


async def prestaciones_recientes(
    db: AsyncSession, cod_obra: str, usuario: Optional[str] = None
) -> list[PrestacionRead]:
    periodo = await get_periodo_activo(db, cod_obra)
    M = DetalleFacturacionCMC
    filtros = [M.cod_obr == cod_obra, M.periodo == periodo, M.estado == "A"]
    if usuario is not None:
        filtros.append(M.usuario == usuario)
    stmt = (
        select(M).where(*filtros)
        # 30 (no 10): la tabla bajo el formulario de carga tiene que mostrar la tanda
        # completa que viene cargando el operador, no sólo las últimas diez.
        .order_by(M.id_detalle_prestaciones.desc()).limit(30)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return await _to_prestacion_read_list(db, rows)


# ── Detalle de una prestación (precarga de formulario de edición) ────────────
def _tipo_prestador_de(row: DetalleFacturacionCMC) -> Optional[str]:
    return _derivar_tipo_prestador(
        _dec(row.honorarios), _dec(row.gastos), _dec(row.ayudante)
    )


async def _to_prestacion_read_list(
    db: AsyncSession, rows: Sequence[DetalleFacturacionCMC],
) -> list[PrestacionRead]:
    """Convierte filas ORM a `PrestacionRead` completando `tipo_prestador` (no viene del
    ORM), `nombre_obra_social` (batch contra `obras_sociales`) y `nombre_clinica` (batch
    contra `listado_medico`), sin N+1. Mismo criterio que `obtener_prestacion` — reusado
    acá para que el listado también lo traiga."""
    nros_os: set[int] = set()
    nros_clinica: set[int] = set()
    for row in rows:
        try:
            nros_os.add(int(row.cod_obr))
        except (TypeError, ValueError):
            pass
        if row.cod_clinica:  # 0 = sentinel legacy "sin clínica"
            nros_clinica.add(int(row.cod_clinica))
    nombres_os: dict[int, str] = {}
    if nros_os:
        os_rows = (await db.execute(
            select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
            .where(ObrasSociales.NRO_OBRASOCIAL.in_(nros_os))
        )).all()
        nombres_os = {nro: nombre for nro, nombre in os_rows}
    nombres_clinica: dict[int, str] = {}
    if nros_clinica:
        cl_rows = (await db.execute(
            select(ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE)
            .where(ListadoMedico.NRO_SOCIO.in_(nros_clinica))
        )).all()
        nombres_clinica = {nro: nombre for nro, nombre in cl_rows}

    out = []
    for row in rows:
        item = PrestacionRead.model_validate(row)
        item.tipo_prestador = _tipo_prestador_de(row)
        try:
            item.nombre_obra_social = nombres_os.get(int(row.cod_obr))
        except (TypeError, ValueError):
            pass
        if row.cod_clinica:
            item.nombre_clinica = nombres_clinica.get(int(row.cod_clinica))
        out.append(item)
    return out


async def obtener_prestacion(db: AsyncSession, prestacion_id: int) -> PrestacionRead:
    """Detalle de una prestación + su equipo. `grupo` trae los demás integrantes que
    comparten `grupo_equipo_id` (ayudantes/gastos/cabeza), para que el front pueda cargar
    todo el equipo al editar — simétrico: pedir el detalle de un ayudante también trae la
    cabecera, y viceversa. `[]` si la prestación no pertenece a un equipo.

    Cada fila (principal e integrantes de `grupo`) trae `tipo_prestador`
    ("Medico"/"Ayudante"/"Gastos") para que el front identifique cuál es la cabecera sin
    interpretar el código legacy `tpo_funcion`."""
    row = await db.get(DetalleFacturacionCMC, prestacion_id)
    if row is None:
        raise HTTPException(404, "Prestación no encontrada")

    principal = PrestacionRead.model_validate(row)
    principal.tipo_prestador = _tipo_prestador_de(row)
    principal.grupo = []
    if row.grupo_equipo_id is not None:
        siblings = (await db.execute(
            select(DetalleFacturacionCMC)
            .where(
                DetalleFacturacionCMC.grupo_equipo_id == row.grupo_equipo_id,
                DetalleFacturacionCMC.id_detalle_prestaciones != prestacion_id,
                DetalleFacturacionCMC.estado != "X",
            )
            .order_by(DetalleFacturacionCMC.id_detalle_prestaciones.asc())
        )).scalars().all()
        grupo = []
        for s in siblings:
            item = PrestacionRead.model_validate(s)
            item.tipo_prestador = _tipo_prestador_de(s)
            grupo.append(item)
        principal.grupo = grupo
    return principal


# ── Edición ──────────────────────────────────────────────────────────────────
async def editar_prestacion(
    db: AsyncSession, prestacion_id: int, payload: PrestacionUpdate
) -> DetalleFacturacionCMC:
    row = await db.get(DetalleFacturacionCMC, prestacion_id)
    if row is None:
        raise HTTPException(404, "Prestación no encontrada")
    if row.estado != "A":
        raise HTTPException(409, "Prestación cerrada/liquidada, no se puede editar")
    # La fase de la cabecera (según quién cargó la prestación) debe seguir abierta.
    _gate_carga(await _get_factura(db, row.cod_obr, row.periodo), row.origen_carga)

    data = payload.model_dump(exclude_unset=True)

    # Campo paciente: relee nombre del padrón si cambió el DNI
    if "dni_paciente" in data:
        row.dni_p = data["dni_paciente"]
        row.nom_ape_p = await _nombre_desde_afiliado(db, data["dni_paciente"])

    # Mapeo de campos simples. `cod_medico`/`cod_medico_ejecutor` NO van acá: no se
    # copian tal cual a la fila, se resuelven más abajo (el médico puede terminar en
    # `cod_med` y la clínica en `cod_clinica`).
    simples = {
        "cod_nomenclador": "cod_nom",
        "via": "via",
        "autorizacion": "autorizacion",
        "grupo_equipo_id": "grupo_equipo_id",
        "cantidad": "cantidad",
        "sesion": "sesion",
        "porcentaje": "porc",
        "fecha_practica": "fecha_practica",
        "tipo_calculo": "manual",
        "honorarios": "honorarios",
        "gastos": "gastos",
        "ayudante": "ayudante",
    }
    for src, dst in simples.items():
        if src in data:
            setattr(row, dst, data[src])

    # Prestador: se reconstruye la selección tal como la habría enviado el front —
    # desde la fila si el PATCH no la toca, con lo que vino si sí. `resolver_prestador`
    # reasigna cod_med / cod_clinica / tipo_orden / tipo en los tres casos.
    campos_prestador = {"cod_medico", "cod_medico_ejecutor", "cod_clinica"}
    cambio_prestador = bool(campos_prestador & data.keys())
    # Selección persistida. El discriminador es `tipo`, NO `tipo_orden` (que vale 'S' en
    # los dos casos con clínica).
    if row.cod_med_ejecutor:
        # Fila legacy (2026-07-17→30): `cod_med` era la clínica-payee y el ejecutor iba
        # aparte. Se reconstruye con esa semántica vieja y el resolver la reacomoda al
        # layout nuevo — editar una de estas filas la migra sola.
        prev_cod, prev_ejecutor, prev_clinica = str(row.cod_med), str(row.cod_med_ejecutor), None
    elif row.tipo == TIPO_SANATORIO and row.cod_clinica:
        # Caso 1: el prestador fue la clínica; el médico que cobra es el ejecutor.
        prev_cod, prev_ejecutor, prev_clinica = str(row.cod_clinica), str(row.cod_med), None
    else:
        # Casos 2 y 3: el prestador fue el médico; la clínica (si hay) es el ámbito.
        prev_cod, prev_ejecutor, prev_clinica = str(row.cod_med), None, row.cod_clinica

    sel_cod = data.get("cod_medico", prev_cod)
    sel_ejecutor = data.get("cod_medico_ejecutor", prev_ejecutor)
    sel_clinica = data.get("cod_clinica", prev_clinica)

    prestador = await resolver_prestador(db, sel_cod, sel_ejecutor, sel_clinica)
    medico_precio = prestador.medico
    row.cod_med = prestador.cod_med
    row.cod_clinica = prestador.cod_clinica
    row.tipo_orden = prestador.tipo_orden
    row.cod_med_ejecutor = None  # ya no se persiste (ver resolver_prestador)
    es_sanatorio = prestador.es_sanatorio

    # Recalcular `tipo` si cambió el prestador (clínica/ámbito) o el código (su categoría).
    if cambio_prestador or "cod_nomenclador" in data:
        row.tipo = await derivar_tipo(db, row.cod_nom, prestador.tipo)

    # Recalcular importe con el estado resultante
    tipo_calculo = row.manual or "A"
    fecha = fecha_para_precio(row.fecha_practica)

    # Inputs de precio que disparan recotizar. Clave para NO re-factorizar (doble
    # descuento) cuando solo se edita cantidad/sesión/fecha: los montos ya están factorizados.
    # cod_medico/cod_medico_ejecutor entran porque cambiar el payee o el ejecutor cambia
    # la especialidad que cotiza → puede cambiar el precio.
    pricing_keys = {"honorarios", "gastos", "ayudante", "porcentaje", "via",
                    "cod_nomenclador", "tipo_calculo", "cod_medico", "cod_medico_ejecutor"}
    if pricing_keys & data.keys():
        # Markers: qué conceptos están en > 0 tras aplicar el PATCH (rol implícito).
        hi = row.honorarios or Decimal("0")
        gi = row.gastos or Decimal("0")
        ai = row.ayudante or Decimal("0")
        if tipo_calculo == "A":
            via = row.via or service_vias.VIA_TRADICIONAL
            precio = await resolver_precio(db, row.cod_obr, medico_precio, row.cod_nom, fecha, via=via)
            if not precio.admitido:
                raise HTTPException(422, precio.motivo)
            if precio.por_presupuesto:
                hb, gb, ab = hi, gi, ai
            else:
                hb = precio.honorarios if hi > 0 else Decimal("0")
                gb = precio.gastos if gi > 0 else Decimal("0")
                ab = precio.ayudante if ai > 0 else Decimal("0")
            row.calculo_snapshot = precio.snapshot
        else:  # manual
            hb, gb, ab = hi, gi, ai
            row.calculo_snapshot = None
        h, g, a = _aplicar_porcentaje(hb, gb, ab, row.porc or 100)
        row.honorarios, row.gastos, row.ayudante = h, g, a
        row.tpo_funcion = tpo_funcion_derivado(h, g, a)

    # Misma regla que en la carga: bajo sanatorio los gastos los factura la clínica.
    # Va FUERA del bloque de recotización — si no, un PATCH que no toca ningún campo de
    # precio (ej. solo `cantidad`) dejaría gastos viejos en una fila que pasó a sanatorio.
    # Forzar a 0 es idempotente y no depende del porcentaje (0 escalado sigue siendo 0).
    if await _gasto_forzado_a_cero(db, row.cod_nom, es_sanatorio, tipo_calculo):
        row.gastos = Decimal("0")
        row.tpo_funcion = tpo_funcion_derivado(
            row.honorarios or Decimal("0"), Decimal("0"), row.ayudante or Decimal("0")
        )

    # importe_total siempre se recalcula con los montos y cantidad/sesión. (0 permitido.)
    row.importe_total = calcular_importe_total(
        row.honorarios or Decimal("0"), row.gastos or Decimal("0"),
        row.ayudante or Decimal("0"), row.cantidad or 1, row.sesion or 1,
    )

    # Sin tope de ayudantes: el máximo del Valor es solo referencia, no se valida (ver
    # nota en `_insertar_prestaciones`).

    await db.commit()
    await db.refresh(row)
    return row


# ── Auditoría (checkbox "revisado") ──────────────────────────────────────────
async def marcar_revisado(
    db: AsyncSession, marcados: list[int], desmarcados: list[int]
) -> list[DetalleFacturacionCMC]:
    """Actualiza en bloque el checkbox de auditoría.

    Se verifica toda la lista antes de modificar filas para que un ID inexistente
    no produzca una actualización parcial. La auditoría admite prestaciones en
    cualquier estado o fase de la cabecera.
    """
    ids = [*marcados, *desmarcados]
    rows = list((await db.execute(
        select(DetalleFacturacionCMC).where(
            DetalleFacturacionCMC.id_detalle_prestaciones.in_(ids)
        )
    )).scalars().all())
    rows_por_id = {row.id_detalle_prestaciones: row for row in rows}
    faltantes = [prestacion_id for prestacion_id in ids if prestacion_id not in rows_por_id]
    if faltantes:
        raise HTTPException(404, {"mensaje": "Prestación no encontrada", "ids": faltantes})

    for prestacion_id in marcados:
        rows_por_id[prestacion_id].revisado = True
    for prestacion_id in desmarcados:
        rows_por_id[prestacion_id].revisado = False

    await db.commit()
    for row in rows:
        await db.refresh(row)
    return [rows_por_id[prestacion_id] for prestacion_id in ids]


# ── Soft-delete ──────────────────────────────────────────────────────────────
async def anular_prestacion(db: AsyncSession, prestacion_id: int, usuario: str) -> None:
    row = await db.get(DetalleFacturacionCMC, prestacion_id)
    if row is None:
        raise HTTPException(404, "Prestación no encontrada")
    if row.estado != "A":
        raise HTTPException(409, "Prestación cerrada/liquidada, no se puede anular")
    if row.usuario != usuario:
        raise HTTPException(403, "Solo el usuario que cargó la prestación puede anularla")
    # La fase de la cabecera (según quién cargó la prestación) debe seguir abierta.
    _gate_carga(await _get_factura(db, row.cod_obr, row.periodo), row.origen_carga)
    cod_obra, periodo = row.cod_obr, row.periodo
    row.estado = "X"
    await db.flush()
    # Si era la última prestación abierta del período, se elimina la cabecera abierta.
    await _cleanup_factura_si_vacia(db, cod_obra, periodo)
    await db.commit()


# ── Movimiento de período ────────────────────────────────────────────────────
async def _periodo_cerrado(db: AsyncSession, cod_obra: str, periodo: str) -> bool:
    """True si la **última** versión de la factura del par (cod_obr, periodo) está cerrada.
    Con complementos, la v1 cerrada no basta: lo que gatea la carga/cierre y decide si hay
    que abrir un complemento es el estado de la versión más nueva. Sin cabecera → False."""
    cabecera = await _get_factura(db, cod_obra, periodo)
    return cabecera is not None and cabecera.estado in FACTURA_ESTADOS_CERRADOS


# ── Cabecera de facturación abierta (invariante: existe ⟺ hay ≥1 prestación 'A') ──
async def _get_factura(
    db: AsyncSession, cod_obra: str, periodo: str
) -> Optional[FacturacionCMC]:
    """Cabecera de la **última** versión del par (cod_obr, periodo). Un período puede
    tener varias versiones (original + complementos); todos los gates/cierre operan sobre
    la de mayor `version`, que es la única que puede estar abierta."""
    stmt = select(FacturacionCMC).where(
        FacturacionCMC.cod_obr == cod_obra, FacturacionCMC.periodo == periodo
    ).order_by(FacturacionCMC.version.desc()).limit(1)
    return (await db.execute(stmt)).scalars().first()


async def _estado_doctor_inicial(db: AsyncSession, cod_obra: str, periodo: str) -> str:
    """Fase médico de una cabecera nueva, DERIVADA del puntero de médicos (no de quién
    la crea): 'A' si el período es >= al que los médicos están cargando (van a llegar
    ahí ahora o cuando avance el puntero); 'C' si el puntero ya lo dejó atrás. Que una
    cabecera esté en 'A' no significa que el médico cargue ahí: el médico carga SOLO
    donde apunta `periodo_medico_actual`. Sin puntero configurado → 'C'."""
    try:
        actual = await get_periodo_medico(db, cod_obra)
    except HTTPException:
        return DOCTOR_CERRADA
    return DOCTOR_ABIERTA if periodo >= actual else DOCTOR_CERRADA


async def _ensure_factura_abierta(
    db: AsyncSession, cod_obra: str, periodo: str, usuario: str,
) -> FacturacionCMC:
    """Garantiza una cabecera `facturacion` para la OS+período. Si no existe ninguna,
    la crea abierta en versión 1 (fase colegio 'A'; fase médico derivada del puntero — ver
    `_estado_doctor_inicial`). No pisa una cabecera existente. Nunca crea versiones > 1:
    los complementos se crean sólo vía `abrir_complemento`."""
    existente = await _get_factura(db, cod_obra, periodo)
    if existente is not None:
        return existente
    hoy = datetime.date.today()
    estado_doctor = await _estado_doctor_inicial(db, cod_obra, periodo)
    # Varias columnas son NOT NULL sin default: se completan con valores neutros
    # (la cabecera del Colegio no usa numeración AFIP).
    cabecera = FacturacionCMC(
        id_cliente=0,
        tipo_factura="", nro_factura="",
        tipo_factura_2="", nro_factura_2="",
        tipo_factura_3="", nro_factura_3="",
        cod_obr=cod_obra,
        periodo=periodo,
        fecha=hoy,
        fecha_envio=hoy,
        fecha_recep=hoy,
        importe=Decimal("0"),
        afip="N",
        estado=FACTURA_ESTADO_ABIERTA,
        estado_doctor=estado_doctor,
        usuario=usuario,
        version=1,
    )
    db.add(cabecera)
    await db.flush()
    return cabecera


def _gate_carga(cabecera: Optional[FacturacionCMC], actor: str) -> None:
    """Valida que la fase correspondiente al actor esté abierta antes de cargar/editar.
    `cabecera=None` (aún no existe) se permite: se creará abierta."""
    if cabecera is None:
        return
    if cabecera.estado in FACTURA_ESTADOS_CERRADOS:
        raise HTTPException(409, "El período está cerrado por el colegio")
    if actor == ORIGEN_MEDICO and cabecera.estado_doctor == DOCTOR_CERRADA:
        raise HTTPException(409, "El período está cerrado para carga de médicos")


async def _cleanup_factura_si_vacia(
    db: AsyncSession, cod_obra: str, periodo: str
) -> None:
    """Elimina la cabecera **abierta** de la última versión si ya no quedan prestaciones
    abiertas ('A') en esa versión del par (cod_obr, periodo). Nunca toca una cerrada."""
    cabecera = await _get_factura(db, cod_obra, periodo)
    if cabecera is None or cabecera.estado != FACTURA_ESTADO_ABIERTA:
        return
    restantes = (await db.execute(
        select(func.count()).select_from(DetalleFacturacionCMC).where(
            DetalleFacturacionCMC.cod_obr == cod_obra,
            DetalleFacturacionCMC.periodo == periodo,
            DetalleFacturacionCMC.version == cabecera.version,
            DetalleFacturacionCMC.estado == "A",
        )
    )).scalar_one()
    if restantes:
        return
    await db.delete(cabecera)


async def mover_prestaciones_periodo(
    db: AsyncSession, payload: MoverPeriodoPayload
) -> MoverPeriodoResponse:
    delta = 1 if payload.direccion == "siguiente" else -1
    periodo_destino = _shift_periodo(payload.periodo_origen, delta)

    if await _periodo_cerrado(db, payload.cod_obra, payload.periodo_origen):
        raise HTTPException(409, "El período origen está cerrado, no se pueden mover prestaciones")
    if await _periodo_cerrado(db, payload.cod_obra, periodo_destino):
        raise HTTPException(409, "El período destino ya tiene factura cerrada")

    M = DetalleFacturacionCMC
    stmt = select(M).where(
        M.cod_obr == payload.cod_obra,
        M.periodo == payload.periodo_origen,
        M.id_detalle_prestaciones.in_(payload.ids),
        M.estado == "A",
    )
    rows = list((await db.execute(stmt)).scalars().all())

    encontrados = {r.id_detalle_prestaciones for r in rows}
    faltantes = [i for i in payload.ids if i not in encontrados]
    if faltantes:
        raise HTTPException(
            422,
            {"mensaje": "Algunas prestaciones no existen o no están en estado 'A' (abierto)",
             "faltantes": faltantes},
        )

    # La versión es propia de cada período: al mover, las filas se reetiquetan con la
    # versión abierta del destino (la de su cabecera actual, o 1 si aún no tiene). Sin
    # esto, una fila de un complemento (version=2) caería en el destino con una versión
    # que no matchea su cabecera y quedaría fuera del cierre/detalle de ese período.
    cabecera_destino = await _get_factura(db, payload.cod_obra, periodo_destino)
    version_destino = cabecera_destino.version if cabecera_destino is not None else 1
    for r in rows:
        r.periodo = periodo_destino
        r.version = version_destino
    await db.flush()

    # Mantener el invariante cabecera ⟺ prestaciones abiertas: crear la del destino
    # y eliminar la del origen si quedó vacío.
    await _ensure_factura_abierta(db, payload.cod_obra, periodo_destino, rows[0].usuario)
    await _cleanup_factura_si_vacia(db, payload.cod_obra, payload.periodo_origen)

    await db.commit()
    return MoverPeriodoResponse(
        ids_movidos=[r.id_detalle_prestaciones for r in rows],
        periodo_destino=periodo_destino,
    )


# ── Cierre de período (A → C) ────────────────────────────────────────────────
async def preview_cierre(db: AsyncSession, cod_obra: str, periodo: str) -> dict:
    """Totales de las prestaciones abiertas ('A') de la última versión de la OS+período,
    sin cerrar."""
    M = DetalleFacturacionCMC
    cabecera = await _get_factura(db, cod_obra, periodo)
    version_actual = cabecera.version if cabecera is not None else 1
    cantidad, total = (await db.execute(
        select(func.count(), func.coalesce(func.sum(M.importe_total), 0)).where(
            M.cod_obr == cod_obra, M.periodo == periodo,
            M.version == version_actual, M.estado == "A",
        )
    )).one()
    return {
        "cod_obra": cod_obra,
        "periodo": periodo,
        "cantidad": int(cantidad or 0),
        "importe_total": Decimal(str(total or 0)),  # str() evita ruido float→Decimal
        "cerrado": await _periodo_cerrado(db, cod_obra, periodo),
    }


async def cerrar_periodo(
    db: AsyncSession, cod_obra: str, periodo: str, usuario: str,
    archivo: Optional[UploadFile] = None, nro_factura: Optional[str] = None,
) -> dict:
    """Cierra un período: crea la cabecera en `facturacion` (estado 'C') y pasa las
    prestaciones de la OS+período de 'A' → 'C'. A partir de acá liquidación las toma
    (`build_detalles_from_cmc` lee estado 'C') y dejan de ser editables. Si viene
    `archivo`, se guarda como comprobante de la factura (`documento_url`). Si viene
    `nro_factura`, se persiste tal cual (texto libre, ej. "00031-00009999") —
    antes de este fix el endpoint no lo aceptaba y quedaba siempre en `""`."""
    if await _periodo_cerrado(db, cod_obra, periodo):
        raise HTTPException(409, "El período ya tiene factura cerrada para esta obra social")

    # Reutiliza la cabecera abierta (última versión: v1 original o un complemento) creada
    # al cargar prestaciones; la sella en 'C'. Si por algún motivo no existe (datos previos
    # a la creación eager), la crea en v1.
    cabecera = await _get_factura(db, cod_obra, periodo)
    version_actual = cabecera.version if cabecera is not None else 1

    M = DetalleFacturacionCMC
    rows = list((await db.execute(
        select(M).where(
            M.cod_obr == cod_obra, M.periodo == periodo,
            M.version == version_actual, M.estado == "A",
        )
    )).scalars().all())
    if not rows:
        raise HTTPException(422, "No hay prestaciones abiertas para cerrar en esta OS+período")

    # importe_total puede volver como float en filas legacy → coercionar a Decimal
    total = sum((Decimal(str(r.importe_total or 0)) for r in rows), Decimal("0"))
    hoy = datetime.date.today()
    if cabecera is None:
        # `facturacion` (co-propiedad CMC) tiene varias columnas NOT NULL sin default:
        # se completan con valores neutros (la cabecera del Colegio no usa numeración AFIP).
        cabecera = FacturacionCMC(
            id_cliente=0,
            tipo_factura="", nro_factura="",
            tipo_factura_2="", nro_factura_2="",
            tipo_factura_3="", nro_factura_3="",
            cod_obr=cod_obra,
            periodo=periodo,
            version=version_actual,
        )
        db.add(cabecera)
        await db.flush()  # asigna id_prestaciones — se necesita para la carpeta del archivo
    cabecera.fecha = hoy
    cabecera.fecha_envio = hoy
    cabecera.fecha_recep = hoy
    cabecera.importe = total
    cabecera.afip = "N"
    cabecera.estado = FACTURA_ESTADO_CERRADA
    # Al cerrar el colegio, la fase médico ya no puede quedar abierta.
    cabecera.estado_doctor = DOCTOR_CERRADA
    cabecera.usuario = usuario
    if nro_factura is not None:
        cabecera.nro_factura = nro_factura
    for r in rows:
        r.estado = "C"

    documento_url = await _guardar_documento_factura(cabecera.id_prestaciones, archivo)
    if documento_url:
        cabecera.documento_url = documento_url

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(cabecera)
    return {
        "id_factura": cabecera.id_prestaciones,
        "cod_obra": cod_obra,
        "periodo": periodo,
        "cantidad": len(rows),
        "importe_total": total,
        "documento_url": cabecera.documento_url,
        "nro_factura": cabecera.nro_factura,
    }


async def abrir_complemento(
    db: AsyncSession, cod_obra: str, periodo: str, usuario: str,
) -> FacturacionCMC:
    """Abre una factura complementaria (nueva versión) para un período+OS ya cerrado.

    Caso de uso: tras cerrar y enviar el período a la obra social, llegan por excepción
    prestaciones rezagadas que hay que reenviar aparte (la nota de crédito es externa al
    sistema). Reglas:
    - Debe existir una factura previa para el par (si no, no hay nada que complementar → 404).
    - La última versión debe estar **cerrada**: si hay una abierta se carga en esa, no se
      abre un complemento (409). Invariante: una sola versión abierta a la vez.

    El complemento nace en fase colegio abierta ('A') y fase médico cerrada ('C') → sólo el
    Colegio carga en él (`_gate_carga` corta al actor médico).
    """
    cabecera = await _get_factura(db, cod_obra, periodo)
    if cabecera is None:
        raise HTTPException(404, "No hay factura previa para este período y obra social")
    if cabecera.estado not in FACTURA_ESTADOS_CERRADOS:
        raise HTTPException(
            409,
            "Ya hay una factura abierta para este período: cargá ahí en lugar de abrir un complemento",
        )

    hoy = datetime.date.today()
    # Mismos valores neutros que `_ensure_factura_abierta` (la cabecera del Colegio no usa
    # numeración AFIP), pero fase médico cerrada: el complemento es carga exclusiva del Colegio.
    nueva = FacturacionCMC(
        id_cliente=0,
        tipo_factura="", nro_factura="",
        tipo_factura_2="", nro_factura_2="",
        tipo_factura_3="", nro_factura_3="",
        cod_obr=cod_obra,
        periodo=periodo,
        fecha=hoy,
        fecha_envio=hoy,
        fecha_recep=hoy,
        importe=Decimal("0"),
        afip="N",
        estado=FACTURA_ESTADO_ABIERTA,
        estado_doctor=DOCTOR_CERRADA,
        usuario=usuario,
        version=cabecera.version + 1,
    )
    db.add(nueva)
    await db.commit()
    await db.refresh(nueva)
    return nueva


# ── Punteros de período médico: fijar y listar (N punteros, 1 por OS) ────────
async def set_periodo_medico(
    db: AsyncSession, periodo: str, usuario: str, cod_obra: Optional[str] = None
) -> dict:
    """Fija (crea o actualiza) un puntero de carga de médicos: el global si `cod_obra`
    es None, o el de esa OS. Hay N punteros posibles, uno por OS + el global de
    respaldo, para soportar cadencias distintas (ej. 151 mes completo cierra el 30 vs
    el resto 20-20 cierra el 20). NO avanza ni cierra fases: solo define en qué período
    cargan los médicos de ese alcance."""
    _periodo_to_date(periodo)  # valida formato YYYYMM (lanza 422 si es inválido)
    nro = _cod_obra_to_int(cod_obra) if cod_obra is not None else None
    cond = (
        PeriodoMedicoActual.obra_social_id == nro if nro is not None
        else PeriodoMedicoActual.obra_social_id.is_(None)
    )
    row = (await db.execute(
        select(PeriodoMedicoActual).where(cond)
    )).scalar_one_or_none()
    if row is None:
        row = PeriodoMedicoActual(obra_social_id=nro, periodo=periodo)
        db.add(row)
    else:
        row.periodo = periodo
        row.updated_at = datetime.datetime.utcnow()
    await db.commit()
    return {"alcance": cod_obra or "global", "periodo": periodo}


async def listar_periodos_medico(db: AsyncSession) -> list[dict]:
    """Todos los punteros de carga de médicos (global + los de cada OS)."""
    P, O = PeriodoMedicoActual, ObrasSociales
    stmt = (
        select(P, O.OBRA_SOCIAL)
        .outerjoin(O, O.NRO_OBRASOCIAL == P.obra_social_id)
        .order_by(P.obra_social_id.is_(None).desc(), P.obra_social_id)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "obra_social_id": p.obra_social_id,
            "obra_social": nombre,
            "es_global": p.obra_social_id is None,
            "periodo": p.periodo,
            "periodo_label": periodo_label(p.periodo),
            "updated_at": p.updated_at,
        }
        for p, nombre in rows
    ]


# ── Fase médico: cierre y avance del período ─────────────────────────────────
async def cerrar_doctor(
    db: AsyncSession, cod_obra: str, periodo: str, usuario: str
) -> dict:
    """Cierra la fase médico de una OS+período (`estado_doctor` A→C) y, si ese período
    era donde el puntero tenía cargando a los médicos de la OS, avanza el puntero
    (override) al mes siguiente — así el médico pasa a cargar automáticamente en el
    período siguiente y nunca queda sin período de carga."""
    cabecera = await _get_factura(db, cod_obra, periodo)
    if cabecera is None:
        raise HTTPException(422, "No hay facturación abierta para esta OS+período")
    if cabecera.estado_doctor != DOCTOR_ABIERTA:
        raise HTTPException(409, "El período ya está cerrado para carga de médicos")
    cabecera.estado_doctor = DOCTOR_CERRADA
    cabecera.usuario = usuario

    # Si el puntero de la OS apuntaba a este período, avanzarlo al siguiente.
    periodo_medico_nuevo: Optional[str] = None
    try:
        actual = await get_periodo_medico(db, cod_obra)
    except HTTPException:
        actual = None
    if actual == periodo:
        nro = _cod_obra_to_int(cod_obra)
        nuevo = _shift_periodo(periodo, 1)
        override = (await db.execute(
            select(PeriodoMedicoActual).where(PeriodoMedicoActual.obra_social_id == nro)
        )).scalar_one_or_none()
        if override is None:
            db.add(PeriodoMedicoActual(obra_social_id=nro, periodo=nuevo))
        else:
            override.periodo = nuevo
            override.updated_at = datetime.datetime.utcnow()
        periodo_medico_nuevo = nuevo

    await db.commit()
    return {
        "cod_obra": cod_obra,
        "periodo": periodo,
        "estado_doctor": DOCTOR_CERRADA,
        "periodo_medico_nuevo": periodo_medico_nuevo,
    }


async def avanzar_periodo_medico(
    db: AsyncSession, usuario: str, cod_obra: Optional[str] = None
) -> dict:
    """Avanza el período abierto para médicos un mes. Si `cod_obra` es None avanza el
    global; si se pasa, crea/actualiza el override de esa OS. Cierra la fase médico
    (`estado_doctor` A→C) de todas las cabeceras que el puntero deja atrás
    (período <= saliente) en el alcance elegido."""
    if cod_obra is not None:
        nro = _cod_obra_to_int(cod_obra)
        row = (await db.execute(
            select(PeriodoMedicoActual).where(PeriodoMedicoActual.obra_social_id == nro)
        )).scalar_one_or_none()
        if row is None:
            actual = await get_periodo_medico(db, cod_obra)  # hereda del global
            row = PeriodoMedicoActual(obra_social_id=nro, periodo=actual)
            db.add(row)
    else:
        row = (await db.execute(
            select(PeriodoMedicoActual).where(PeriodoMedicoActual.obra_social_id.is_(None))
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(422, "No hay período de médico global configurado")

    saliente = row.periodo
    nuevo = _shift_periodo(saliente, 1)

    # Cerrar la fase médico de todo lo que queda detrás del puntero nuevo.
    cond = [
        FacturacionCMC.periodo <= saliente,
        FacturacionCMC.estado_doctor == DOCTOR_ABIERTA,
    ]
    if cod_obra is not None:
        cond.append(FacturacionCMC.cod_obr == cod_obra)
    else:
        # El avance global no toca las OS con override propio: su puntero (y por lo
        # tanto su fase médico) avanza por separado.
        overrides = (await db.execute(
            select(PeriodoMedicoActual.obra_social_id)
            .where(PeriodoMedicoActual.obra_social_id.is_not(None))
        )).scalars().all()
        if overrides:
            cond.append(FacturacionCMC.cod_obr.notin_([str(o) for o in overrides]))
    cabeceras = list((await db.execute(select(FacturacionCMC).where(*cond))).scalars().all())
    for c in cabeceras:
        c.estado_doctor = DOCTOR_CERRADA

    row.periodo = nuevo
    row.updated_at = datetime.datetime.utcnow()
    await db.commit()
    return {
        "alcance": cod_obra or "global",
        "periodo_saliente": saliente,
        "periodo_nuevo": nuevo,
        "cabeceras_cerradas": len(cabeceras),
    }
