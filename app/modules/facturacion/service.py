import datetime
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Afiliado,
    DetalleFacturacionCMC,
    FacturacionCMC,
    ListadoMedico,
    NomencladorCMC,
)
from app.modules.nomenclador import service as service_nm
from app.modules.nomenclador.service import LookupError as PrecioLookupError

from app.modules.facturacion.schemas import (
    AfiliadoCreate,
    GuardadoResponse,
    MoverPeriodoPayload,
    MoverPeriodoResponse,
    PrecioResponse,
    PrestacionesCreate,
    PrestacionItem,
    PrestacionUpdate,
)

FUENTE_PRECIO = "nm_historial_precio_codigo"
CODIGOS_NO_PERMITIDOS = {"5000", "6000"}

_MESES = [
    "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
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


# ── Afiliados ────────────────────────────────────────────────────────────────
async def get_afiliado_by_dni(db: AsyncSession, dni: str) -> Optional[Afiliado]:
    stmt = select(Afiliado).where(Afiliado.dni == dni)
    return (await db.execute(stmt)).scalar_one_or_none()


async def crear_afiliado(db: AsyncSession, payload: AfiliadoCreate, usuario: str) -> Afiliado:
    if await get_afiliado_by_dni(db, payload.dni):
        raise HTTPException(409, f"Ya existe un afiliado con DNI {payload.dni}")
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
            422, f"Afiliado con DNI {dni} no encontrado. Registrarlo primero."
        )
    return af.nombre


# ── Período activo de una obra social ────────────────────────────────────────
async def get_periodo_activo(db: AsyncSession, cod_obra: str) -> str:
    """Último período cerrado en `facturacion` para la OS + 1 mes."""
    stmt = select(func.max(FacturacionCMC.periodo)).where(FacturacionCMC.cod_obr == cod_obra)
    ultimo = (await db.execute(stmt)).scalar_one_or_none()
    if not ultimo:
        raise HTTPException(
            422, "No se encontró período cerrado para esta obra social"
        )
    return _shift_periodo(ultimo, 1)


# ── Mapeo de identificadores ─────────────────────────────────────────────────
def _cod_obra_to_int(cod_obra: str) -> int:
    try:
        return int(cod_obra)
    except (ValueError, TypeError):
        raise HTTPException(422, f"Obra social '{cod_obra}' no es numérica")


async def check_medico_activo(db: AsyncSession, cod_medico: str) -> ListadoMedico:
    try:
        nro = int(cod_medico)
    except (ValueError, TypeError):
        raise HTTPException(422, f"Código de médico '{cod_medico}' inválido")
    stmt = select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == nro)
    medico = (await db.execute(stmt)).scalar_one_or_none()
    if not medico or medico.EXISTE != "S":
        raise HTTPException(422, f"Médico {cod_medico} inexistente o inactivo")
    return medico


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
) -> PrecioResponse:
    """Delega precio Y habilitación en el lookup del módulo nomenclador.

    Errores de mapeo de identificadores → HTTPException (404/422).
    Falta de precio / habilitación / fecha → admitido=False + motivo.
    """
    obra_social_nro = _cod_obra_to_int(cod_obra)
    nomenclador = await _get_nomenclador_by_codigo(db, codigo)

    try:
        out = await service_nm.lookup_precio(
            nomenclador.id, obra_social_nro, fecha, medico.ID, db,
        )
    except PrecioLookupError as e:
        return PrecioResponse(
            admitido=False, motivo=e.message,
            honorarios=Decimal("0"), gastos=Decimal("0"), ayudante=Decimal("0"),
            descripcion="", fuente=FUENTE_PRECIO,
        )

    def _suma(concepto: str) -> Decimal:
        return sum(
            (c.subtotal for c in out.componentes if c.concepto == concepto),
            Decimal("0"),
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
    )


# ── Validaciones puras ───────────────────────────────────────────────────────
def check_coherencia_servicio_orden(
    tpo_servicio: str, tipo_orden: str, cod_nomenclador: str
) -> Optional[str]:
    """§4.5 — retorna mensaje de error o None si OK.

    Solo valida la coherencia servicio↔orden. La validación por rangos de código
    (legacy) se eliminó: la categorización Consulta/Práctica/Honorarios Individuales
    ahora vive en `nm_nomenclador.categoria`, no en el rango numérico del código.
    """
    if tpo_servicio == "I" and tipo_orden in {"C", "P", "R"}:
        return "Internado solo admite orden tipo Sanatorio"
    if tpo_servicio == "A" and tipo_orden == "S":
        return "Ambulatorio no admite orden tipo Sanatorio"
    return None


def check_coherencia_codigo_categoria(
    tipo_orden: str, categoria: Optional[str]
) -> Optional[str]:
    """Coherencia código↔orden basada en `nm_nomenclador.categoria` (reemplaza la
    validación legacy por rangos). El caso claro y valioso: orden de consulta ⟺
    código de categoría 'Consulta'. Las demás categorías (Practica / Honorarios
    individuales) son demasiado gruesas para validar radiología por separado."""
    if categoria is None:
        return None  # código sin categoría → no se valida
    if tipo_orden == "C" and categoria != "Consulta":
        return f"Orden de consulta (C) requiere un código de categoría Consulta (es '{categoria}')"
    if categoria == "Consulta" and tipo_orden != "C":
        return "Un código de consulta requiere orden tipo Consulta (C)"
    return None


async def _get_categoria(db: AsyncSession, codigo: str) -> Optional[str]:
    stmt = select(NomencladorCMC.categoria).where(NomencladorCMC.codigo == codigo)
    return (await db.execute(stmt)).scalar_one_or_none()


async def check_duplicado(
    db: AsyncSession,
    periodo: str,
    cod_med: str,
    cod_obr: str,
    cod_nom: str,
    dni_p: Optional[str],
    tpo_funcion: str,
) -> Optional[DetalleFacturacionCMC]:
    """Busca fila estado='P' con la misma combinación (§4.7).

    `nro_orden` NO entra en la clave: cada fila recibe uno único, así que
    incluirlo haría que el duplicado nunca matchee.
    """
    stmt = select(DetalleFacturacionCMC).where(
        DetalleFacturacionCMC.periodo == periodo,
        DetalleFacturacionCMC.cod_med == cod_med,
        DetalleFacturacionCMC.cod_obr == cod_obr,
        DetalleFacturacionCMC.cod_nom == cod_nom,
        DetalleFacturacionCMC.tpo_funcion == tpo_funcion,
        DetalleFacturacionCMC.estado == "A",
    )
    if dni_p is not None:
        stmt = stmt.where(DetalleFacturacionCMC.dni_p == dni_p)
    else:
        stmt = stmt.where(DetalleFacturacionCMC.dni_p.is_(None))
    return (await db.execute(stmt)).scalars().first()


# ── Normalizaciones y cálculo ────────────────────────────────────────────────
def normalizar_tipo_orden(tipo_orden: str) -> str:
    return "P" if tipo_orden == "R" else tipo_orden


def normalizar_fecha_practica(
    fecha: Optional[datetime.date], periodo: str
) -> datetime.date:
    return fecha if fecha is not None else _periodo_to_date(periodo)


def normalizar_urgencia(via_quirurgica: Optional[str], sub_tipo: Optional[str]) -> str:
    if sub_tipo in {"CA", "CI"} and via_quirurgica in {"T", "L"}:
        return via_quirurgica
    return "N"


def aplicar_opcion_pago(
    honorarios_base: Decimal,
    gastos_base: Decimal,
    ayudante_base: Decimal,
    tpo_funcion: str,
    porcentaje: int,
) -> tuple[Decimal, Decimal, Decimal]:
    factor = Decimal(porcentaje) / Decimal(100)
    cero = Decimal("0")
    if tpo_funcion == "H":
        return honorarios_base * factor, cero, cero
    if tpo_funcion == "G":
        return cero, gastos_base * factor, cero
    if tpo_funcion == "HG":
        return honorarios_base * factor, gastos_base * factor, cero
    if tpo_funcion == "A":
        return cero, cero, ayudante_base * factor
    return honorarios_base, gastos_base, ayudante_base


def calcular_importe_total(
    h: Decimal, g: Decimal, a: Decimal, cantidad: int, sesion: int
) -> Decimal:
    return (h + g + a) * Decimal(cantidad) * Decimal(sesion)


async def _base_nro_orden(db: AsyncSession) -> int:
    """MAX(nro_orden) GLOBAL — el nro_orden es único en toda detalle_facturacion,
    sin importar obra social ni período (evita colisiones al mover de período)."""
    stmt = select(func.max(cast(DetalleFacturacionCMC.nro_orden, Integer)))
    return (await db.execute(stmt)).scalar() or 0


async def _montos_de_item(
    db: AsyncSession,
    item: PrestacionItem | PrestacionUpdate,
    cod_obra: str,
    medico: ListadoMedico,
    cod_nomenclador: str,
    tipo_calculo: str,
    fecha: datetime.date,
) -> tuple[Decimal, Decimal, Decimal, Optional[list[dict]]]:
    """Resuelve los montos base (h, g, a) y el snapshot según el modo de cálculo."""
    if tipo_calculo == "A":
        precio = await resolver_precio(
            db, cod_obra, medico, cod_nomenclador, fecha,
        )
        if not precio.admitido:
            raise HTTPException(422, precio.motivo)
        # Por presupuesto: el lookup admite con H/G/A en 0; el monto lo informa la
        # OS y lo carga el operador a mano (reusa el modo manual). El check
        # importe_total > 0 obliga a que el presupuesto venga cargado.
        if precio.por_presupuesto:
            return (
                item.honorarios or Decimal("0"),
                item.gastos or Decimal("0"),
                item.ayudante or Decimal("0"),
                precio.snapshot,
            )
        return precio.honorarios, precio.gastos, precio.ayudante, precio.snapshot
    return (
        item.honorarios or Decimal("0"),
        item.gastos or Decimal("0"),
        item.ayudante or Decimal("0"),
        None,
    )


# ── Guardado ─────────────────────────────────────────────────────────────────
async def guardar_prestaciones(
    db: AsyncSession,
    payload: PrestacionesCreate,
    usuario: str,
    confirmar_duplicado: bool = False,
) -> GuardadoResponse:
    periodo = await get_periodo_activo(db, payload.obra_social)
    items = payload.prestaciones

    # Validaciones de equipo quirúrgico
    if len(items) > 1:
        cirujanos = [i for i in items if i.tpo_funcion in {"H", "HG"}]
        if len(cirujanos) != 1:
            raise HTTPException(
                422, "El equipo quirúrgico debe tener exactamente un cirujano (H/HG)"
            )
        cods = [i.cod_medico for i in items]
        if len(set(cods)) != len(cods):
            raise HTTPException(422, "No se permite repetir cod_medico en el equipo")

    base = await _base_nro_orden(db)

    ids: list[int] = []
    total_acum = Decimal("0")

    for i, item in enumerate(items):
        medico = await check_medico_activo(db, item.cod_medico)
        tipo_orden_norm = normalizar_tipo_orden(item.tipo_orden)
        fecha_norm = normalizar_fecha_practica(item.fecha_practica, periodo)
        nro_orden_item = str(base + i + 1)

        err = check_coherencia_servicio_orden(
            item.tpo_servicio, item.tipo_orden, item.cod_nomenclador
        )
        if err:
            raise HTTPException(422, err)

        err_cat = check_coherencia_codigo_categoria(
            item.tipo_orden, await _get_categoria(db, item.cod_nomenclador)
        )
        if err_cat:
            raise HTTPException(422, err_cat)

        if item.cod_nomenclador in CODIGOS_NO_PERMITIDOS:
            raise HTTPException(422, "Código no permitido")

        dup = await check_duplicado(
            db, periodo, item.cod_medico, payload.obra_social,
            item.cod_nomenclador, item.dni_paciente, item.tpo_funcion,
        )
        if dup and not confirmar_duplicado:
            raise HTTPException(
                409,
                {"mensaje": "Prestación duplicada", "duplicado": dup.id_detalle_prestaciones},
            )

        nombre_paciente = await _nombre_desde_afiliado(db, item.dni_paciente)

        h_base, g_base, a_base, snapshot = await _montos_de_item(
            db, item, payload.obra_social, medico,
            item.cod_nomenclador, item.tipo_calculo, fecha_norm,
        )

        h, g, a = aplicar_opcion_pago(h_base, g_base, a_base, item.tpo_funcion, item.porcentaje)
        total = calcular_importe_total(h, g, a, item.cantidad, item.sesion)
        if total <= 0:
            raise HTTPException(422, "El importe total debe ser mayor a 0")

        row = DetalleFacturacionCMC(
            calculo_snapshot=snapshot,
            periodo=periodo,
            cod_obr=payload.obra_social,
            cod_med=item.cod_medico,
            nro_orden=nro_orden_item,
            cod_nom=item.cod_nomenclador,
            tpo_funcion=item.tpo_funcion,
            sesion=item.sesion,
            cantidad=item.cantidad,
            honorarios=h,
            gastos=g,
            ayudante=a,
            importe_total=total,
            manual=item.tipo_calculo,
            dni_p=item.dni_paciente,
            nom_ape_p=nombre_paciente,
            tpo_serv=item.tpo_servicio,
            cod_clinica=item.cod_clinica,
            fecha_practica=fecha_norm,
            tipo_orden=tipo_orden_norm,
            porc=item.porcentaje,
            urgencia=normalizar_urgencia(item.via_quirurgica, item.sub_tipo_nomenclador),
            estado="A",
            usuario=usuario,
        )
        db.add(row)
        await db.flush()
        ids.append(row.id_detalle_prestaciones)
        total_acum += total

    await db.commit()
    return GuardadoResponse(ids=ids, importe_total=total_acum)


# ── Listado ──────────────────────────────────────────────────────────────────
async def listar_prestaciones(
    db: AsyncSession,
    *,
    cod_obra: Optional[str] = None,
    periodo: Optional[str] = None,
    cod_medico: Optional[str] = None,
    cod_nomenclador: Optional[str] = None,
    nro_orden: Optional[str] = None,
    estado: Optional[str] = None,
    tpo_funcion: Optional[str] = None,
    tpo_servicio: Optional[str] = None,
    tipo_orden: Optional[str] = None,
    dni_paciente: Optional[str] = None,
    nombre_paciente: Optional[str] = None,
    fecha_desde: Optional[datetime.date] = None,
    fecha_hasta: Optional[datetime.date] = None,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[DetalleFacturacionCMC], int]:
    M = DetalleFacturacionCMC
    filtros = []
    if cod_obra is not None:
        filtros.append(M.cod_obr == cod_obra)
    if periodo is not None:
        filtros.append(M.periodo == periodo)
    if cod_medico is not None:
        filtros.append(M.cod_med == cod_medico)
    if cod_nomenclador is not None:
        filtros.append(M.cod_nom == cod_nomenclador)
    if nro_orden is not None:
        filtros.append(M.nro_orden == nro_orden)
    if estado is not None:
        filtros.append(M.estado == estado)
    if tpo_funcion is not None:
        filtros.append(M.tpo_funcion == tpo_funcion)
    if tpo_servicio is not None:
        filtros.append(M.tpo_serv == tpo_servicio)
    if tipo_orden is not None:
        filtros.append(M.tipo_orden == tipo_orden)
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
    rows = list((await db.execute(stmt)).scalars().all())
    return rows, total


async def prestaciones_recientes(
    db: AsyncSession, cod_obra: str, usuario: Optional[str] = None
) -> list[DetalleFacturacionCMC]:
    periodo = await get_periodo_activo(db, cod_obra)
    M = DetalleFacturacionCMC
    filtros = [M.cod_obr == cod_obra, M.periodo == periodo, M.estado == "A"]
    if usuario is not None:
        filtros.append(M.usuario == usuario)
    stmt = (
        select(M).where(*filtros)
        .order_by(M.id_detalle_prestaciones.desc()).limit(10)
    )
    return list((await db.execute(stmt)).scalars().all())


# ── Edición ──────────────────────────────────────────────────────────────────
async def editar_prestacion(
    db: AsyncSession, prestacion_id: int, payload: PrestacionUpdate
) -> DetalleFacturacionCMC:
    row = await db.get(DetalleFacturacionCMC, prestacion_id)
    if row is None:
        raise HTTPException(404, "Prestación no encontrada")
    if row.estado != "A":
        raise HTTPException(409, "Prestación cerrada/liquidada, no se puede editar")

    data = payload.model_dump(exclude_unset=True)

    # Campo paciente: relee nombre del padrón si cambió el DNI
    if "dni_paciente" in data:
        row.dni_p = data["dni_paciente"]
        row.nom_ape_p = await _nombre_desde_afiliado(db, data["dni_paciente"])

    # Mapeo de campos simples
    simples = {
        "cod_medico": "cod_med",
        "cod_nomenclador": "cod_nom",
        "tpo_servicio": "tpo_serv",
        "cod_clinica": "cod_clinica",
        "cantidad": "cantidad",
        "sesion": "sesion",
        "tpo_funcion": "tpo_funcion",
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

    if "tipo_orden" in data:
        row.tipo_orden = normalizar_tipo_orden(data["tipo_orden"])
    if "via_quirurgica" in data or "sub_tipo_nomenclador" in data:
        via = data.get("via_quirurgica")
        sub = data.get("sub_tipo_nomenclador")
        row.urgencia = normalizar_urgencia(via, sub)

    # Recalcular importe con el estado resultante
    medico = await check_medico_activo(db, row.cod_med)
    tipo_calculo = row.manual or "A"
    fecha = row.fecha_practica or datetime.date.today()

    # Inputs de precio que disparan re-aplicar la opción de pago. Clave para no
    # re-factorizar (doble descuento) cuando solo se edita cantidad/sesión/fecha:
    # los montos guardados ya están factorizados.
    pricing_keys = {"honorarios", "gastos", "ayudante", "tpo_funcion", "porcentaje",
                    "cod_nomenclador", "tipo_calculo"}
    recotizar = bool(pricing_keys & data.keys())

    if tipo_calculo == "A":
        if recotizar:
            # Automático: la base sale del lookup (sin factor) y se factoriza una vez.
            h_base, g_base, a_base, snapshot = await _montos_de_item(
                db, payload, row.cod_obr, medico, row.cod_nom, tipo_calculo, fecha,
            )
            h, g, a = aplicar_opcion_pago(
                h_base, g_base, a_base, row.tpo_funcion or "H", row.porc or 100
            )
            row.honorarios, row.gastos, row.ayudante = h, g, a
            row.calculo_snapshot = snapshot
    else:  # manual
        if recotizar:
            # El PATCH trae montos base nuevos (o cambió tpo_funcion/porcentaje):
            # los campos enviados ya quedaron en row por el mapeo simples; se factoriza una vez.
            h, g, a = aplicar_opcion_pago(
                row.honorarios or Decimal("0"), row.gastos or Decimal("0"),
                row.ayudante or Decimal("0"), row.tpo_funcion or "H", row.porc or 100,
            )
            row.honorarios, row.gastos, row.ayudante = h, g, a

    # importe_total siempre se recalcula con los montos (ya factorizados) y cantidad/sesión.
    row.importe_total = calcular_importe_total(
        row.honorarios or Decimal("0"), row.gastos or Decimal("0"),
        row.ayudante or Decimal("0"), row.cantidad or 1, row.sesion or 1,
    )
    if row.importe_total <= 0:
        raise HTTPException(422, "El importe total debe ser mayor a 0")

    await db.commit()
    await db.refresh(row)
    return row


# ── Soft-delete ──────────────────────────────────────────────────────────────
async def anular_prestacion(db: AsyncSession, prestacion_id: int, usuario: str) -> None:
    row = await db.get(DetalleFacturacionCMC, prestacion_id)
    if row is None:
        raise HTTPException(404, "Prestación no encontrada")
    if row.estado != "A":
        raise HTTPException(409, "Prestación cerrada/liquidada, no se puede anular")
    if row.usuario != usuario:
        raise HTTPException(403, "Solo el usuario que cargó la prestación puede anularla")
    row.estado = "X"
    await db.commit()


# ── Movimiento de período ────────────────────────────────────────────────────
async def _periodo_cerrado(db: AsyncSession, cod_obra: str, periodo: str) -> bool:
    stmt = select(FacturacionCMC.id_prestaciones).where(
        FacturacionCMC.cod_obr == cod_obra, FacturacionCMC.periodo == periodo
    ).limit(1)
    return (await db.execute(stmt)).first() is not None


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
        M.nro_orden.in_(payload.nro_ordenes),
        M.estado == "A",
    )
    rows = list((await db.execute(stmt)).scalars().all())

    encontrados = {r.nro_orden for r in rows}
    faltantes = [n for n in payload.nro_ordenes if n not in encontrados]
    if faltantes:
        raise HTTPException(
            422,
            {"mensaje": "Algunos nro_orden no existen o no están en estado 'A' (abierto)",
             "faltantes": faltantes},
        )

    for r in rows:
        r.periodo = periodo_destino

    await db.commit()
    return MoverPeriodoResponse(
        ids_movidos=[r.id_detalle_prestaciones for r in rows],
        periodo_destino=periodo_destino,
    )


# ── Cierre de período (A → C) ────────────────────────────────────────────────
async def preview_cierre(db: AsyncSession, cod_obra: str, periodo: str) -> dict:
    """Totales de las prestaciones abiertas ('A') de la OS+período, sin cerrar."""
    M = DetalleFacturacionCMC
    cantidad, total = (await db.execute(
        select(func.count(), func.coalesce(func.sum(M.importe_total), 0)).where(
            M.cod_obr == cod_obra, M.periodo == periodo, M.estado == "A"
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
    db: AsyncSession, cod_obra: str, periodo: str, usuario: str
) -> dict:
    """Cierra un período: crea la cabecera en `facturacion` (estado 'C') y pasa las
    prestaciones de la OS+período de 'A' → 'C'. A partir de acá liquidación las toma
    (`build_detalles_from_cmc` lee estado 'C') y dejan de ser editables."""
    if await _periodo_cerrado(db, cod_obra, periodo):
        raise HTTPException(409, "El período ya tiene factura cerrada para esta obra social")

    M = DetalleFacturacionCMC
    rows = list((await db.execute(
        select(M).where(M.cod_obr == cod_obra, M.periodo == periodo, M.estado == "A")
    )).scalars().all())
    if not rows:
        raise HTTPException(422, "No hay prestaciones abiertas para cerrar en esta OS+período")

    # importe_total puede volver como float en filas legacy → coercionar a Decimal
    total = sum((Decimal(str(r.importe_total or 0)) for r in rows), Decimal("0"))
    hoy = datetime.date.today()
    # `facturacion` (co-propiedad CMC) tiene varias columnas NOT NULL sin default:
    # se completan con valores neutros (la cabecera del Colegio no usa numeración AFIP).
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
        importe=total,
        afip="N",
        estado="C",
        usuario=usuario,
    )
    db.add(cabecera)
    for r in rows:
        r.estado = "C"
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
    }
