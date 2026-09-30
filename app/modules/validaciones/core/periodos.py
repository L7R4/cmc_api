"""Período de carga del médico, compartido por todas las obras sociales.

El período no es el mes calendario ni depende de la fecha de la prestación:
sale del mismo puntero `periodo_medico_actual` que usa la carga del médico
desde facturación (override por obra social → global), para que todo caiga en
el mismo período. El cierre es el de facturación
(`facturacion.estado_doctor`/`facturacion.estado`), no una marca propia de
este módulo.
"""
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.facturacion.service import (
    ORIGEN_COLEGIO,
    ORIGEN_MEDICO,
    _gate_carga,
    _get_factura,
    asegurar_periodo_medico_vigente,
    get_periodo_medico,
)


def partes_periodo(periodo: str) -> tuple[int, int]:
    """'YYYYMM' → (mes, anio)."""
    return (int(periodo[4:6]), int(periodo[0:4]))


async def periodo_actual(db: AsyncSession, obra_social_id: int) -> str:
    """Período en el que el médico está cargando para esa obra social.

    Sale del puntero `periodo_medico_actual`: primero el override de la O.S., y
    si no tiene, la fila global. NO es el mes calendario ni depende de la fecha
    de la prestación — es el mismo puntero con el que el médico carga desde
    facturación, para que todo caiga en el mismo período.

    `asegurar_periodo_medico_vigente` avanza el puntero si ya venció el
    `dia_corte` de la O.S., por si el cron de cierre no corrió.
    """
    cod_obra = str(obra_social_id)
    await asegurar_periodo_medico_vigente(db, cod_obra)
    return await get_periodo_medico(db, cod_obra)


def actor_para(user: dict, socio: int) -> str:
    """`ORIGEN_MEDICO` si el socio operado es el dueño del token, `ORIGEN_COLEGIO`
    si es personal del Colegio operando en nombre de otro médico (mismo criterio
    que `socio_objetivo`: pedir un socio distinto del propio ya exigió el scope
    administrativo antes de llegar acá)."""
    propio = user.get("nro_socio")
    if propio is not None and int(propio) == int(socio):
        return ORIGEN_MEDICO
    return ORIGEN_COLEGIO


async def periodo_cerrado(
    db: AsyncSession, obra_social_id: int, periodo: str, actor: str = ORIGEN_MEDICO,
) -> bool:
    """True si `actor` ya no puede operar en ese período de esa obra social.

    Mismo criterio que facturación (`_gate_carga`): la fase colegio cierra para
    cualquiera; la fase médico solo cierra para el actor médico — el Colegio
    operando en nombre de un médico sigue pudiendo mientras su propia fase siga
    abierta. Sin cabecera → abierto (se crea con la primera prestación).
    """
    try:
        await gate_periodo(db, obra_social_id, periodo, actor)
    except HTTPException:
        return True
    return False


async def gate_periodo(
    db: AsyncSession, obra_social_id: int, periodo: str, actor: str = ORIGEN_MEDICO,
) -> None:
    """Corta con 409 si el período está cerrado para `actor`. Se llama **antes**
    de consultar al validador de la O.S.: no tiene sentido consumir el token de
    la credencial del afiliado para una prestación que después no vamos a poder
    grabar."""
    _gate_carga(await _get_factura(db, str(obra_social_id), periodo), actor)
