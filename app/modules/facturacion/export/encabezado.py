"""Encabezado institucional del detalle de facturación (PDF y Excel).

Formato fijo pedido por el Colegio, reproduciendo el membrete legacy:

    COLEGIO MEDICO DE CORRIENTES
    CARLOS PELLEGRINI 1785 - (3400) - CORRIENTES - TEL.:(0379) 4427421 - 4422066
    Tipo y Nº de Factura/s: X - xxxxx-xxxxxxxx
    Facturación correspondiente a: Mes Año
    Obra social (<nro>) <nombre>

Se arma una única vez en `routes.py` y cada builder lo renderiza a su manera:
el PDF lo repite en cada página, el Excel lo escribe una sola vez en las
primeras filas de la hoja (no es una fila "repetible" de impresión).

No comparte código con el membrete de `caratula.py` a propósito: ese usa una
`Institucion` con teléfonos etiquetados en líneas separadas para una carátula
formal; acá es una única línea de domicilio+teléfono para un encabezado de
listado. Forzar una abstracción común entre dos formatos que hoy no se parecen
sólo complicaría ambos.
"""
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FacturacionCMC, Institucion, ListadoMedico, ObrasSociales
from app.modules.facturacion import service

RAZON_SOCIAL_FALLBACK = "COLEGIO MEDICO DE CORRIENTES"
DOMICILIO_FALLBACK = "CARLOS PELLEGRINI 1785 - (3400) - CORRIENTES"
TELEFONOS_FALLBACK = ["(0379) 4427421", "4422066"]


@dataclass
class EncabezadoExport:
    lineas: list[str]  # siempre 5, en el orden del formato de arriba


async def _linea_institucion(db: AsyncSession) -> tuple[str, str]:
    """(razón social, "domicilio - TEL.:nro - nro"). La tabla `institucion`
    hoy está vacía en producción, así que cae al literal legacy; en cuanto
    alguien la complete, esto pasa a usar el dato real solo."""
    inst = (await db.execute(select(Institucion).limit(1))).scalars().first()
    if inst is None or not inst.razon_social:
        return RAZON_SOCIAL_FALLBACK, f"{DOMICILIO_FALLBACK} - TEL.:{' - '.join(TELEFONOS_FALLBACK)}"
    partes_domicilio = [p for p in (inst.domicilio, inst.localidad, inst.codigo_postal) if p]
    domicilio = " - ".join(partes_domicilio) or DOMICILIO_FALLBACK
    numeros = [t.numero for t in inst.telefonos]
    linea_domicilio = f"{domicilio} - TEL.:{' - '.join(numeros)}" if numeros else domicilio
    return inst.razon_social, linea_domicilio


def _linea_facturas(factura: FacturacionCMC) -> str:
    pares = [
        (factura.tipo_factura, factura.nro_factura),
        (factura.tipo_factura_2, factura.nro_factura_2),
        (factura.tipo_factura_3, factura.nro_factura_3),
    ]
    textos = [f"{tipo} - {nro}" for tipo, nro in pares if tipo and nro]
    return "Tipo y Nº de Factura/s: " + (", ".join(textos) if textos else "-")


async def _nombre_obra_social(db: AsyncSession, cod_obr: Optional[str]) -> str:
    if not cod_obr:
        return ""
    try:
        cod_obr_int = int(cod_obr)
    except (TypeError, ValueError):
        return cod_obr
    os_row = (await db.execute(
        select(ObrasSociales).where(ObrasSociales.NRO_OBRASOCIAL == cod_obr_int)
    )).scalars().first()
    return os_row.OBRA_SOCIAL if os_row else cod_obr


async def construir_encabezado(db: AsyncSession, factura: FacturacionCMC) -> EncabezadoExport:
    razon_social, linea_domicilio = await _linea_institucion(db)
    obra_social_nombre = await _nombre_obra_social(db, factura.cod_obr)
    periodo = service.periodo_label(factura.periodo) if factura.periodo else "-"
    return EncabezadoExport(lineas=[
        razon_social,
        linea_domicilio,
        _linea_facturas(factura),
        f"Facturación correspondiente a: {periodo}",
        f"Obra social ({factura.cod_obr}) {obra_social_nombre}",
    ])


async def construir_encabezado_por_medico(
    db: AsyncSession, cod_medico: str, periodo: str,
) -> EncabezadoExport:
    """Mismo membrete institucional que `construir_encabezado`, adaptado al
    "Detalle por médico": donde el export de factura pone el Nº de factura y una
    única obra social, acá van la identidad del médico y la leyenda de que cruza
    todas las obras sociales (el detalle es por socio, no por factura)."""
    razon_social, linea_domicilio = await _linea_institucion(db)
    periodo_label = service.periodo_label(periodo) if periodo else "-"

    medico = None
    try:
        cod_int = int(cod_medico)
    except (TypeError, ValueError):
        cod_int = None
    if cod_int is not None:
        medico = (await db.execute(
            select(ListadoMedico).where(ListadoMedico.NRO_SOCIO == cod_int)
        )).scalars().first()

    if medico is not None:
        linea_medico = f"Médico: {medico.NOMBRE or '-'} - Socio {cod_medico}"
        if medico.MATRICULA_PROV is not None:
            linea_medico += f" - Matrícula {medico.MATRICULA_PROV}"
    else:
        linea_medico = f"Médico: Socio {cod_medico}"

    return EncabezadoExport(lineas=[
        razon_social,
        linea_domicilio,
        linea_medico,
        f"Facturación correspondiente a: {periodo_label}",
        "Detalle por médico - todas las obras sociales",
    ])
