"""Lectura de las prestaciones de una factura para exportar.

Deliberadamente NO usa `select(DetalleFacturacionCMC)` (hidratar ~50 columnas de
ORM completo por fila) — para 17-20 mil filas el costo de eso es real. Trae sólo
las columnas que el export necesita, vía Core, y resuelve `tipo`/nombre de
prestador reusando exactamente la misma lógica que `facturacion.service` (no se
duplica la regla de negocio).

`cod_med` está declarado `String(20)` en el ORM pero es INT en la tabla legacy
(ver el comentario en `service.py` junto a `obtener_factura_detalle`) — por eso el
lookup de médicos sigue el mismo patrón de `IN (...)` batcheado que usa
`obtener_factura_detalle`, y no un JOIN SQL directo, que en filas legacy con
tipos desalineados podría no matchear en silencio.
"""
import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DetalleFacturacionCMC,
    Especialidad,
    FacturacionCMC,
    ListadoMedico,
    ObrasSociales,
)
from app.modules.facturacion import service
from app.modules.facturacion.equipo import CandidatoEquipo, clave_paciente, inferir_equipos
from app.modules.facturacion.export.schemas import ExportOpciones

M = DetalleFacturacionCMC

_COLUMNAS = (
    M.id_detalle_prestaciones, M.cod_med, M.cod_obr, M.cod_nom, M.nomenclador_id,
    M.via, M.sesion, M.cantidad, M.honorarios, M.gastos, M.ayudante,
    M.importe_total, M.coseguro, M.porc, M.dni_p, M.nom_ape_p,
    M.cod_clinica, M.fecha_practica, M.autorizacion, M.tipo, M.diag,
    M.grupo_equipo_id, M.id_especialidad, M.revisado, M.estado,
    M.validacion_estado, M.tpo_funcion, M.created,
)


@dataclass
class FilaExport:
    id: int
    cod_medico: str
    prestador_nombre: Optional[str]
    cod_obr: Optional[str]
    obra_social_nombre: Optional[str]
    matricula: Optional[int]
    autorizacion: Optional[str]
    fecha_practica: Optional[datetime.date]
    codigo: str
    nro_afiliado: Optional[str]
    afiliado: Optional[str]
    cantidad: int
    sesion: int
    porcentaje: Optional[int]
    honorarios: Decimal
    gastos: Decimal
    ayudante: Decimal
    coseguro: Decimal
    subtotal: Decimal
    tipo: str
    tipo_prestador: Optional[str]
    diagnostico: Optional[str]
    via: Optional[str]
    especialidad_nombre: Optional[str]
    estado_validacion: Optional[str]
    revisado: bool
    grupo_equipo_id: Optional[int]
    # Fecha/hora de CARGA (no de práctica).
    created: Optional[datetime.datetime] = None
    # Fila que los filtros dejaron afuera pero se trae igual porque es del equipo de una
    # cabeza que sí está: el equipo va siempre completo bajo su cabeza. Si la cabeza no
    # está en el documento, esta fila no se lista.
    fuera_de_filtro: bool = False
    # Clínica de la prestación (el ámbito, o el prestador en los Sanatorios).
    cod_clinica: Optional[int] = None
    clinica_nombre: Optional[str] = None


async def _resolver_especialidades(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    ids = {i for i in ids if i}  # 0 = "sin especialidad", no busca fila
    if not ids:
        return {}
    filas = (await db.execute(
        select(Especialidad.ID_COLEGIO_ESPE, Especialidad.ESPECIALIDAD)
        .where(Especialidad.ID_COLEGIO_ESPE.in_(ids))
    )).all()
    # Puede haber duplicados de ID_COLEGIO_ESPE (no es UNIQUE en la tabla) — se
    # queda con el primero, es sólo una etiqueta de exportación.
    out: dict[int, str] = {}
    for id_colegio, nombre in filas:
        out.setdefault(id_colegio, nombre)
    return out


def _cod_medico_a_int(cod_med) -> Optional[int]:
    try:
        return int(cod_med)
    except (TypeError, ValueError):
        return None


async def _resolver_obras_sociales(db: AsyncSession, cods: set[str]) -> dict[str, str]:
    """Nombre de obra social por `cod_obr` — mismo patrón que `_nombre_obra_social`
    de `encabezado.py`, pero en batch: acá una fila por código, no una sola."""
    nros: set[int] = set()
    for c in cods:
        try:
            nros.add(int(c))
        except (TypeError, ValueError):
            continue
    if not nros:
        return {}
    filas = (await db.execute(
        select(ObrasSociales.NRO_OBRASOCIAL, ObrasSociales.OBRA_SOCIAL)
        .where(ObrasSociales.NRO_OBRASOCIAL.in_(nros))
    )).all()
    return {str(nro): nombre for nro, nombre in filas}


def _tipo_de(
    tipo_col: Optional[str], cod_clinica, es_organizacion: bool, tipo_sin_clinica: Optional[str],
) -> str:
    """Misma regla que el closure `_tipo_de` de `service.obtener_factura_detalle`
    — reproducida acá porque ahí vive como closure, no exportable. Sanatorio si el
    médico es organización → Honorarios individuales si hay clínica → si no, la
    categoría del código en la OS (`service.tipos_sin_clinica`)."""
    if tipo_col is not None:
        return tipo_col
    if es_organizacion:
        return service.TIPO_SANATORIO
    if cod_clinica:
        return service.CATEGORIA_HONORARIOS_INDIVIDUALES
    return tipo_sin_clinica or service.TIPO_PRACTICA


async def _equipos_inferidos(db: AsyncSession, factura: FacturacionCMC) -> dict[int, int]:
    """Ayudantes/pediatras cargados SIN grupo → su médico de cabecera (ver `equipo.py`).
    Se calcula sobre TODA la factura, sin los filtros del usuario: la cabeza decide si el
    equipo entra, así que hay que conocerla aunque el filtro la deje afuera. Sólo se
    hace si hay algún integrante suelto, y con las columnas mínimas."""
    base = [
        M.cod_obr == factura.cod_obr, M.periodo == factura.periodo,
        M.version == factura.version, M.estado != "X",
    ]
    hay_sueltos = (await db.execute(
        select(M.id_detalle_prestaciones)
        .where(*base, M.grupo_equipo_id.is_(None), or_(M.ayudante > 0, M.tpo_funcion == service.TPO_FUNCION_PEDIATRA))
        .limit(1)
    )).first()
    if hay_sueltos is None:
        return {}
    filas = (await db.execute(select(
        M.id_detalle_prestaciones, M.cod_med, M.cod_clinica, M.cod_nom, M.honorarios, M.gastos,
        M.ayudante, M.tpo_funcion, M.dni_p, M.nom_ape_p, M.fecha_practica, M.grupo_equipo_id,
    ).where(*base))).all()
    return inferir_equipos([
        CandidatoEquipo(
            id=r.id_detalle_prestaciones, cod_medico=str(r.cod_med),
            cod_clinica=_cod_medico_a_int(r.cod_clinica) or None,
            codigo=str(r.cod_nom) if r.cod_nom is not None else None,
            tipo_prestador=service._derivar_tipo_prestador(
                service._dec(r.honorarios), service._dec(r.gastos), service._dec(r.ayudante), r.tpo_funcion,
            ),
            paciente=clave_paciente(r.dni_p, r.nom_ape_p), fecha_practica=r.fecha_practica,
            grupo_equipo_id=r.grupo_equipo_id,
        )
        for r in filas
    ])


def _grupo_efectivo(
    id_: int, grupo: Optional[int], virtual: dict[int, int], cabezas_virtuales: set[int],
) -> Optional[int]:
    """`grupo_equipo_id` real, o el inferido: el integrante suelto apunta a su cabeza y la
    cabeza inferida a sí misma (igual que una cabeza real)."""
    if grupo is not None:
        return grupo
    if id_ in virtual:
        return virtual[id_]
    return id_ if id_ in cabezas_virtuales else None


async def obtener_filas_export(
    db: AsyncSession, factura: FacturacionCMC, opciones: ExportOpciones,
) -> list[FilaExport]:
    condiciones = [
        M.cod_obr == factura.cod_obr,
        M.periodo == factura.periodo,
        M.version == factura.version,
        M.estado != "X",
    ]
    if opciones.fecha_desde is not None:
        condiciones.append(M.fecha_practica >= opciones.fecha_desde)
    if opciones.fecha_hasta is not None:
        condiciones.append(M.fecha_practica <= opciones.fecha_hasta)
    if opciones.id_especialidad is not None:
        condiciones.append(M.id_especialidad == opciones.id_especialidad)
    if opciones.cod_medicos:
        condiciones.append(M.cod_med.in_(opciones.cod_medicos))
    if opciones.revisado is not None:
        condiciones.append(M.revisado == opciones.revisado)

    stmt = select(*_COLUMNAS).where(*condiciones)

    filas_raw = []
    result = await db.stream(stmt.execution_options(yield_per=1000))
    async for row in result:
        filas_raw.append(row)

    # Equipo quirúrgico: si un filtro (ej. cod_medicos) dejó afuera al ayudante
    # o a la cabeza, se completa el equipo entero — el usuario decidió que el
    # equipo se ve agrupado siempre, no recortado por los filtros de fila.
    virtual = await _equipos_inferidos(db, factura)
    cabezas_virtuales = set(virtual.values())
    grupos_presentes = {
        g for r in filas_raw
        if (g := _grupo_efectivo(r.id_detalle_prestaciones, r.grupo_equipo_id, virtual, cabezas_virtuales)) is not None
    }
    ids_presentes = {r.id_detalle_prestaciones for r in filas_raw}
    ids_extra: set[int] = set()
    if grupos_presentes:
        integrantes_virtuales = [m for m, cabeza in virtual.items() if cabeza in grupos_presentes]
        extra_stmt = select(*_COLUMNAS).where(
            M.cod_obr == factura.cod_obr, M.periodo == factura.periodo,
            M.version == factura.version, M.estado != "X",
            or_(
                M.grupo_equipo_id.in_(grupos_presentes),
                M.id_detalle_prestaciones.in_(grupos_presentes),  # la cabeza inferida
                M.id_detalle_prestaciones.in_(integrantes_virtuales or [0]),
            ),
            M.id_detalle_prestaciones.notin_(ids_presentes),
        )
        extra = (await db.execute(extra_stmt)).all()
        ids_extra = {r.id_detalle_prestaciones for r in extra}
        filas_raw.extend(extra)

    if not filas_raw:
        return []
    return await _materializar_filas(db, filas_raw, opciones, ids_extra, virtual)


async def obtener_filas_export_por_medico(
    db: AsyncSession, cod_medico: str, periodo: str, opciones: ExportOpciones,
) -> list[FilaExport]:
    """Filas de UN médico en un período, cruzando todas las obras sociales y
    versiones — la fuente del export de "Detalle por médico". A diferencia de
    `obtener_filas_export` (atada a una factura: cod_obr + periodo + version),
    filtra por `cod_med` + `periodo` y NO completa equipos: el detalle es de
    este socio, no arrastra las filas de ayudantes/gastos que son de OTROS
    socios (mismo criterio que la pantalla `listar_prestaciones` por médico)."""
    condiciones = [
        M.cod_med == cod_medico,
        M.periodo == periodo,
        M.estado != "X",
    ]
    if opciones.fecha_desde is not None:
        condiciones.append(M.fecha_practica >= opciones.fecha_desde)
    if opciones.fecha_hasta is not None:
        condiciones.append(M.fecha_practica <= opciones.fecha_hasta)
    if opciones.id_especialidad is not None:
        condiciones.append(M.id_especialidad == opciones.id_especialidad)
    if opciones.revisado is not None:
        condiciones.append(M.revisado == opciones.revisado)

    stmt = select(*_COLUMNAS).where(*condiciones)
    filas_raw = []
    result = await db.stream(stmt.execution_options(yield_per=1000))
    async for row in result:
        filas_raw.append(row)

    if not filas_raw:
        return []
    return await _materializar_filas(db, filas_raw, opciones)


async def _materializar_filas(
    db: AsyncSession, filas_raw: list, opciones: ExportOpciones,
    ids_extra: Optional[set[int]] = None, virtual: Optional[dict[int, int]] = None,
) -> list[FilaExport]:
    """Convierte las filas crudas (Core) en `FilaExport`: resuelve el prestador
    (socio/matrícula/nombre), la especialidad y el tipo, y aplica el filtro de
    tipos de `opciones`. Compartido por el export por factura y el por médico."""
    # Batch de médicos (mismo patrón que obtener_factura_detalle, service.py:1560)
    nros: set[int] = set()
    for r in filas_raw:
        v = _cod_medico_a_int(r.cod_med)
        if v:
            nros.add(v)
        c = _cod_medico_a_int(r.cod_clinica)  # 0 = sentinel legacy "sin clínica"
        if c:
            nros.add(c)
    medicos: dict[str, ListadoMedico] = {}
    if nros:
        med_rows = (await db.execute(
            select(ListadoMedico).where(ListadoMedico.NRO_SOCIO.in_(nros))
        )).scalars().all()
        medicos = {str(m.NRO_SOCIO): m for m in med_rows}

    especialidades = await _resolver_especialidades(
        db, {r.id_especialidad for r in filas_raw if r.id_especialidad}
    )
    obras_sociales = await _resolver_obras_sociales(
        db, {str(r.cod_obr) for r in filas_raw if r.cod_obr}
    )

    tipos_legacy = await service.tipos_sin_clinica(
        db, {(r.cod_nom, str(r.cod_obr) if r.cod_obr is not None else None) for r in filas_raw if r.tipo is None},
    )

    virtual = virtual or {}
    cabezas_virtuales = set(virtual.values())
    filas: list[FilaExport] = []
    for r in filas_raw:
        cod_medico = str(r.cod_med)
        # `cod_obr` tiene el mismo desalineo de tipos que `cod_med` (declarado
        # String en el ORM, INT en la tabla legacy — ver el módulo docstring):
        # normalizar a str acá, no sólo al armar el set de arriba, porque el
        # lookup de abajo compara contra las claves str de `obras_sociales`.
        cod_obr = str(r.cod_obr) if r.cod_obr is not None else None
        medico = medicos.get(cod_medico)
        h, g, a = service._dec(r.honorarios), service._dec(r.gastos), service._dec(r.ayudante)
        tipo = _tipo_de(
            r.tipo, r.cod_clinica, bool(medico.es_organizacion) if medico else False,
            tipos_legacy.get((r.cod_nom, cod_obr)),
        )
        cod_clinica = _cod_medico_a_int(r.cod_clinica) or None
        clinica = medicos.get(str(cod_clinica)) if cod_clinica else None
        filas.append(FilaExport(
            id=r.id_detalle_prestaciones,
            cod_medico=cod_medico,
            prestador_nombre=medico.NOMBRE if medico else None,
            cod_obr=cod_obr,
            obra_social_nombre=obras_sociales.get(cod_obr) if cod_obr else None,
            matricula=medico.MATRICULA_PROV if medico else None,
            autorizacion=r.autorizacion,
            fecha_practica=r.fecha_practica,
            codigo=r.cod_nom,
            nro_afiliado=r.dni_p,
            afiliado=r.nom_ape_p,
            cantidad=r.cantidad or 0,
            sesion=r.sesion or 1,
            porcentaje=r.porc,
            honorarios=h,
            gastos=g,
            ayudante=a,
            coseguro=service._dec(r.coseguro),
            subtotal=service._dec(r.importe_total),
            tipo=tipo,
            tipo_prestador=service._derivar_tipo_prestador(h, g, a, r.tpo_funcion),
            diagnostico=r.diag,
            via=r.via,
            especialidad_nombre=especialidades.get(r.id_especialidad),
            estado_validacion=r.validacion_estado,
            revisado=bool(r.revisado),
            grupo_equipo_id=_grupo_efectivo(r.id_detalle_prestaciones, r.grupo_equipo_id, virtual, cabezas_virtuales),
            created=r.created,
            fuera_de_filtro=bool(ids_extra) and r.id_detalle_prestaciones in ids_extra,
            cod_clinica=cod_clinica,
            clinica_nombre=clinica.NOMBRE if clinica else None,
        ))

    if opciones.tipos:
        tipos_set = set(opciones.tipos)
        # Igual que la vista: el filtro de tipo se aplica fila por fila. Las que no matchean
        # y son de un equipo se conservan marcadas, para poder mostrarlas bajo su cabeza.
        for f in filas:
            if f.tipo not in tipos_set:
                f.fuera_de_filtro = True
        filas = [f for f in filas if not f.fuera_de_filtro or f.grupo_equipo_id is not None]

    return filas


async def obtener_factura(db: AsyncSession, factura_id: int) -> FacturacionCMC:
    factura = await db.get(FacturacionCMC, factura_id)
    if factura is None:
        raise HTTPException(404, "Factura no encontrada")
    return factura
