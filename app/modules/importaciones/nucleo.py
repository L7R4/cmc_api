"""Lo que comparten todos los importadores.

Cada obra social manda su reporte con sus columnas y sus reglas, pero el
camino de una fila es siempre el mismo: resolver el médico por matrícula,
cotizar el código, descartar lo que ya está cargado y armar la fila de
`detalle_facturacion`. Eso vive acá; lo propio de cada una, en su carpeta.
"""
import datetime
import logging
import re
from collections import Counter
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models import DetalleFacturacionCMC, FacturacionCMC, ListadoMedico
from app.modules.facturacion.service import FACTURA_ESTADOS_CERRADOS
from app.modules.importaciones.schemas import (
    FilaResultado,
    PeriodoOpcion,
    PeriodosOut,
    ResumenImportacion,
)
from app.modules.validaciones.core.periodos import periodo_actual

log = logging.getLogger(__name__)

CERO = Decimal("0.00")

# Mismos valores fijos que usa `validaciones/core/grabado.py`: una práctica de
# un reporte es una prestación simple, sin equipo, sin ayudante y al 100%.
SESION_UNICA = 1
PORCENTAJE_COMPLETO = 100
CALCULO_AUTOMATICO = "A"
DETALLE_ACTIVO = "A"
DETALLE_FUERA_DE_FACTURA = "X"

# `validacion_estado`: la obra social ya resolvió por fuera del panel; acá sólo
# se registra lo que el reporte informa.
VALIDACION_CARGADA = "cargada"
VALIDACION_RECHAZADA = "rechazada"

GRABABLE = "grabable"
GRABADA = "grabada"
DUPLICADA = "duplicada"
OMITIDA = "omitida"
# Sólo UNNE: la matrícula no tiene un único socio y hay que elegirlo a mano.
ELEGIR_SOCIO = "elegir_socio"

# La clave que identifica una práctica dentro de un período.
Clave = tuple[str, str, Optional[datetime.date]]


def matricula_int(valor: str) -> Optional[int]:
    """El número de una matrícula, venga como venga.

    Los reportes la mandan de todas las formas: "12345", " 12345 ", "MP 12345",
    "W-3972" (Swiss le antepone la provincia) o "NO INFORMADO". Interesa el
    número; la letra de provincia la evalúa cada importador por su cuenta,
    porque sólo algunos la traen.
    """
    digitos = re.sub(r"\D", "", valor or "")
    if not digitos:
        return None
    n = int(digitos)
    return n or None  # "0" no es una matrícula


async def indice_medicos(
    db: AsyncSession, matriculas: set[int]
) -> dict[int, ListadoMedico]:
    """Los médicos de esas matrículas, en una sola consulta.

    Una matrícula repetida en `listado_medico` no se adivina: se descarta y sus
    filas quedan sin médico, para que alguien lo mire. Es preferible a
    facturarle a un homónimo.
    """
    if not matriculas:
        return {}

    filas = (
        await db.execute(
            select(ListadoMedico).where(ListadoMedico.MATRICULA_PROV.in_(matriculas))
        )
    ).scalars().all()

    por_matricula: dict[int, ListadoMedico] = {}
    repetidas: set[int] = set()
    for m in filas:
        mat = m.MATRICULA_PROV
        if mat in por_matricula:
            repetidas.add(mat)
            continue
        por_matricula[mat] = m

    for mat in repetidas:
        log.warning(
            "Importación: la matrícula %s tiene más de un socio; sus filas se omiten.",
            mat,
        )
        por_matricula.pop(mat, None)

    return por_matricula


async def candidatos_por_matricula(
    db: AsyncSession, matriculas: set[int]
) -> dict[int, list[ListadoMedico]]:
    """Todos los socios de cada matrícula, sin descartar las repetidas.

    A diferencia de `indice_medicos`, acá una matrícula con dos socios devuelve los
    dos: el importador que la usa (UNNE) deja elegir cuál en la previsualización.
    """
    if not matriculas:
        return {}
    filas = (
        await db.execute(
            select(ListadoMedico)
            .where(ListadoMedico.MATRICULA_PROV.in_(matriculas))
            .order_by(ListadoMedico.NRO_SOCIO)
        )
    ).scalars().all()
    out: dict[int, list[ListadoMedico]] = {}
    for m in filas:
        out.setdefault(m.MATRICULA_PROV, []).append(m)
    return out


async def socios_por_nro(db: AsyncSession, nros: set[int]) -> dict[int, ListadoMedico]:
    if not nros:
        return {}
    filas = (
        await db.execute(select(ListadoMedico).where(ListadoMedico.NRO_SOCIO.in_(nros)))
    ).scalars().all()
    return {m.NRO_SOCIO: m for m in filas}


async def ya_cargadas(
    db: AsyncSession, obra_social_nro: int, periodo: str
) -> Counter:
    """Cuántas veces está ya cargada cada (autorización, código, fecha).

    Se cuenta, no se marca presente. Un reporte puede traer la misma práctica
    más de una vez —el médico la hizo dos veces— y son dos prestaciones
    facturables. Con un conjunto de claves la segunda se perdía en silencio.

    Contando, los tres casos salen bien solos: reimportar el archivo entero las
    marca todas duplicadas, un archivo con la práctica repetida entra dos veces,
    y reimportar algo cargado a medias completa lo que falta.

    No hay índice único que lo garantice en la base —el chequeo es acá, en
    código, a propósito— así que dos importaciones simultáneas del mismo
    archivo podrían colarse. Es un riesgo aceptado frente a migrar el esquema.
    """
    filas = (
        await db.execute(
            select(
                DetalleFacturacionCMC.autorizacion,
                DetalleFacturacionCMC.cod_nom,
                DetalleFacturacionCMC.fecha_practica,
            ).where(
                DetalleFacturacionCMC.cod_obr == str(obra_social_nro),
                DetalleFacturacionCMC.periodo == periodo,
            )
        )
    ).all()
    return Counter((a or "", c or "", f) for a, c, f in filas)


def resumir(filas: list[FilaResultado], periodo: str) -> ResumenImportacion:
    grabables = [f for f in filas if f.resultado in (GRABABLE, GRABADA)]
    return ResumenImportacion(
        periodo=periodo,
        total=len(filas),
        grabables=len(grabables),
        duplicadas=sum(1 for f in filas if f.resultado == DUPLICADA),
        omitidas=sum(1 for f in filas if f.resultado == OMITIDA),
        sin_medico=sum(1 for f in filas if f.nro_socio is None),
        rechazadas=sum(
            1 for f in grabables if f.estado_detalle == DETALLE_FUERA_DE_FACTURA
        ),
        importe_total=quantize_money(sum((f.importe_total for f in grabables), CERO)),
        por_elegir=sum(1 for f in filas if f.resultado == ELEGIR_SOCIO),
        con_aviso=sum(1 for f in grabables if f.aviso),
    )


async def periodos_disponibles(db: AsyncSession, obra_social_nro: int) -> PeriodosOut:
    """Períodos entre los que puede elegir el administrativo.

    Son los que ya tienen cabecera de facturación para esa obra social, más el
    que apunta `periodo_medico_actual` (que puede no tener cabecera todavía si
    nadie cargó nada ese mes). Los cerrados se devuelven marcados, no se
    esconden: que se vean explica por qué no están disponibles.
    """
    sugerido = await periodo_actual(db, obra_social_nro)

    filas = (
        await db.execute(
            select(FacturacionCMC.periodo, FacturacionCMC.estado, FacturacionCMC.version)
            .where(FacturacionCMC.cod_obr == str(obra_social_nro))
            .order_by(FacturacionCMC.periodo.desc(), FacturacionCMC.version.desc())
        )
    ).all()

    # Un período puede tener varias versiones; manda la última, que es la
    # primera que llega con este orden.
    estados: dict[str, str] = {}
    for periodo, estado, _version in filas:
        if periodo and periodo not in estados:
            estados[periodo] = estado or ""

    if sugerido not in estados:
        estados[sugerido] = ""

    return PeriodosOut(
        sugerido=sugerido,
        periodos=[
            PeriodoOpcion(
                periodo=p,
                cerrado=estados[p] in FACTURA_ESTADOS_CERRADOS,
                sugerido=(p == sugerido),
            )
            for p in sorted(estados, reverse=True)
        ],
    )
