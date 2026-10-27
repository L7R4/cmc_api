"""
Retención de Ganancias (concepto 300) dentro del pago, y el listado mensual que sale de ella.

Mientras el pago está abierto, `recalcular_retencion_ganancias` (la llama
`generar_y_recalcular_porcentuales`, igual que a los porcentuales) deja para cada socio
asignado al concepto 300 que participa del pago:

- una fila en `retencion_ganancias` con la cuenta (base del pago, del mes, retención del mes,
  lo retenido en otros pagos cerrados del mes y lo que toca en este), y
- la deducción automática del pago (`cuota_nro` = id del pago) por lo que toca retener, que el
  cierre cobra con prioridad, como las contribuciones. Si da cero no hay deducción: así la
  vista previa no se llena de renglones en $0.

Si el operador quitó la deducción (cancelada/eliminada), no vuelve y la fila registra que en
este pago no se retuvo nada: el próximo pago del mes retiene lo que falte.
Al cerrar el pago la fila queda fija; al reabrirlo se vuelve a calcular.
"""
import datetime
from collections import defaultdict
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    Conceptos,
    Deduccion,
    DeduccionAplicacion,
    Institucion,
    ListadoMedico,
    Pago,
    RetencionGanancias,
    RetencionGananciasEscala,
    SocioDescuento,
)
from app.modules.deducciones.helpers import NRO_COLEGIO_GANANCIAS, _base_retenciones_por_medico_en_pago
from app.modules.retenciones.calculo import (
    CERO,
    Escala,
    Tramo,
    retencion_del_pago,
    tipo_sujeto,
)

ESTADOS_PAGO_CERRADO = ("C", "P")


async def concepto_ganancias(db: AsyncSession) -> Optional[Conceptos]:
    return await db.scalar(select(Conceptos).where(Conceptos.nro_colegio == NRO_COLEGIO_GANANCIAS))


async def escala_vigente(db: AsyncSession, tipo: str, fecha: datetime.date) -> Optional[Escala]:
    fila = await db.scalar(
        select(RetencionGananciasEscala)
        .options(selectinload(RetencionGananciasEscala.tramos))
        .where(RetencionGananciasEscala.tipo_sujeto == tipo, RetencionGananciasEscala.vigente_desde <= fecha)
        .order_by(RetencionGananciasEscala.vigente_desde.desc())
        .limit(1)
    )
    if fila is None:
        return None
    return Escala(
        id=fila.id,
        tipo_sujeto=fila.tipo_sujeto,
        minimo_no_imponible=Decimal(fila.minimo_no_imponible),
        retencion_minima=Decimal(fila.retencion_minima),
        tramos=tuple(
            Tramo(Decimal(t.desde), None if t.hasta is None else Decimal(t.hasta), Decimal(t.importe_fijo), Decimal(t.porcentaje))
            for t in fila.tramos
        ),
    )


async def _acumulado_otros_pagos(
    db: AsyncSession, pago: Pago, medicos: set[int]
) -> dict[int, tuple[Decimal, Decimal]]:
    """{medico_id: (base, retenido)} de los otros pagos CERRADOS del mismo mes (como el sistema
    anterior: un pago abierto todavía puede cambiar, no cuenta)."""
    if not medicos:
        return {}
    rows = (await db.execute(
        select(
            RetencionGanancias.medico_id,
            func.coalesce(func.sum(RetencionGanancias.base_pago), 0),
            func.coalesce(func.sum(RetencionGanancias.retencion_pago), 0),
        )
        .join(Pago, Pago.id == RetencionGanancias.pago_id)
        .where(
            RetencionGanancias.mes == pago.mes,
            RetencionGanancias.anio == pago.anio,
            RetencionGanancias.pago_id != pago.id,
            Pago.estado.in_(ESTADOS_PAGO_CERRADO),
            RetencionGanancias.medico_id.in_(medicos),
        )
        .group_by(RetencionGanancias.medico_id)
    )).all()
    return {int(m): (Decimal(b), Decimal(r)) for m, b, r in rows}


async def recalcular_retencion_ganancias(db: AsyncSession, pago: Pago) -> dict:
    """Deja al día la retención de Ganancias del pago abierto. Sin commit."""
    vacio = {"socios": 0, "con_retencion": 0, "total": "0.00"}
    if pago.estado != "A":
        return vacio
    concepto = await concepto_ganancias(db)
    if concepto is None:
        return vacio

    asignados = set((await db.execute(
        select(SocioDescuento.medico_id).where(
            SocioDescuento.descuento_id == concepto.id, SocioDescuento.fecha_baja.is_(None)
        )
    )).scalars().all())
    bases = await _base_retenciones_por_medico_en_pago(db, pago.id)
    alcanzados = asignados & set(bases)

    filas = {
        f.medico_id: f
        for f in (await db.execute(
            select(RetencionGanancias).where(RetencionGanancias.pago_id == pago.id)
        )).scalars().all()
    }
    deds: dict[int, Deduccion] = {}
    for d in (await db.execute(
        select(Deduccion)
        .where(
            Deduccion.descuento_id == concepto.id,
            Deduccion.origen == "automatico",
            Deduccion.cuota_nro == pago.id,
        )
        .order_by(Deduccion.id)
    )).scalars().all():
        deds.setdefault(d.medico_id, d)

    otros = await _acumulado_otros_pagos(db, pago, alcanzados)
    sujetos = {
        m: (cuit, bool(org))
        for m, cuit, org in (await db.execute(
            select(ListadoMedico.ID, ListadoMedico.CUIT, ListadoMedico.es_organizacion)
            .where(ListadoMedico.ID.in_(alcanzados))
        )).all()
    } if alcanzados else {}

    fecha = datetime.date(pago.anio, pago.mes, 1)
    escalas: dict[str, Escala] = {}
    con_retencion = 0
    total = CERO

    for medico_id in sorted(alcanzados):
        base = bases[medico_id]
        cuit, es_org = sujetos.get(medico_id, (None, False))
        tipo = tipo_sujeto(cuit, es_org)
        if tipo not in escalas:
            escala = await escala_vigente(db, tipo, fecha)
            if escala is None:
                raise HTTPException(
                    409,
                    f"No hay escala de retención de Ganancias para «{tipo}» vigente al {fecha:%d/%m/%Y}. "
                    "Cargala en retencion_ganancias_escala antes de seguir con el pago.",
                )
            escalas[tipo] = escala
        escala = escalas[tipo]

        base_otros, retenido_otros = otros.get(medico_id, (CERO, CERO))
        base_mes = base_otros + base["importe"]
        retencion_mes, en_este_pago = retencion_del_pago(base_mes, retenido_otros, escala)

        ded = deds.get(medico_id)
        if ded is not None and ded.estado in ("cancelado", "eliminado"):
            en_este_pago = CERO  # el operador la quitó de este pago: no vuelve
        elif ded is not None and ded.monto_aplicado > 0:
            # Ya se cobró (en parte) en un pago posterior: no se toca, se respeta lo que quedó.
            en_este_pago = Decimal(ded.calculado_total)
        elif en_este_pago > 0:
            if ded is None:
                ded = Deduccion(
                    medico_id=medico_id,
                    descuento_id=concepto.id,
                    calculado_total=en_este_pago,
                    porcentaje_aplicado=Decimal("0.00"),
                    monto_aplicado=Decimal("0.00"),
                    origen="automatico",
                    # Una retención sale siempre del pago del propio socio: ni caja ni pagador.
                    paga_por_caja=False,
                    estado="en_pago",
                    cuota_nro=pago.id,
                    mes_aplicar=pago.mes,
                    anio_aplicar=pago.anio,
                    generado_en_pago_id=pago.id,
                )
                db.add(ded)
                deds[medico_id] = ded
            else:
                ded.calculado_total = en_este_pago
                if ded.estado == "pendiente":
                    ded.estado = "en_pago"
        elif ded is not None:
            await _quitar_deduccion(db, filas.get(medico_id), ded)
            deds.pop(medico_id)
            ded = None

        if ded is not None and ded.id is None:
            await db.flush()

        fila = filas.get(medico_id)
        if fila is None:
            fila = RetencionGanancias(pago_id=pago.id, medico_id=medico_id)
            db.add(fila)
        fila.mes, fila.anio = pago.mes, pago.anio
        fila.tipo_sujeto = tipo
        fila.escala_id = escala.id
        fila.honorarios = base["honorarios"]
        fila.gastos = base["gastos"]
        fila.base_pago = base["importe"]
        fila.base_mes = base_mes
        fila.retencion_mes = retencion_mes
        fila.retenido_otros_pagos = retenido_otros
        fila.retencion_pago = en_este_pago
        fila.deduccion_id = ded.id if ded is not None else None

        if en_este_pago > 0:
            con_retencion += 1
            total += en_este_pago

    # Socios que ya no participan (se sacó su factura) o que dejaron de estar asignados.
    for medico_id in set(filas) - alcanzados:
        await db.delete(filas[medico_id])
    for medico_id in set(deds) - alcanzados:
        ded = deds[medico_id]
        if ded.estado in ("en_pago", "pendiente") and ded.monto_aplicado == 0:
            await _quitar_deduccion(db, None, ded)

    await db.flush()
    return {"socios": len(alcanzados), "con_retencion": con_retencion, "total": str(total)}


async def _quitar_deduccion(db: AsyncSession, fila: Optional[RetencionGanancias], ded: Deduccion) -> None:
    """Borra la deducción de Ganancias de un pago abierto que ya no corresponde (nunca se cobró)."""
    if fila is not None:
        fila.deduccion_id = None
        await db.flush()
    await db.delete(ded)
    await db.flush()


# =============================================================================
# Listado mensual (lo que el contador presenta) y comprobantes
# =============================================================================

async def listado_mensual(db: AsyncSession, anio: int, mes: int) -> dict:
    """
    Retenciones de Ganancias del mes, por socio, con los pagos CERRADOS del mes (como el
    «D.G.I. Impuesto a las Ganancias» del sistema anterior).

    - Base (honorarios, gastos, monto imponible): la suma de las filas de retencion_ganancias.
    - Retenido: lo que de verdad se descontó en esos pagos por el concepto 300
      (deduccion_aplicacion), incluido lo que se debía de un mes anterior y se cobró ahora.
      Es lo que va en el comprobante.
    - Calculado: lo que correspondía retener; si no alcanzó la plata, la diferencia queda como
      deuda del socio y se retiene en un pago siguiente.
    Sólo figuran los socios con algo calculado o retenido.
    """
    pagos = (await db.execute(
        select(Pago).where(Pago.anio == anio, Pago.mes == mes).order_by(Pago.id)
    )).scalars().all()
    cerrados = [p.id for p in pagos if p.estado in ESTADOS_PAGO_CERRADO]
    abiertos = [p.id for p in pagos if p.estado not in ESTADOS_PAGO_CERRADO]

    filas_out: list[dict] = []
    if cerrados:
        bases = (await db.execute(
            select(
                RetencionGanancias.medico_id,
                func.max(RetencionGanancias.tipo_sujeto),
                func.sum(RetencionGanancias.honorarios),
                func.sum(RetencionGanancias.gastos),
                func.sum(RetencionGanancias.base_pago),
                func.sum(RetencionGanancias.retencion_pago),
            )
            .where(RetencionGanancias.pago_id.in_(cerrados))
            .group_by(RetencionGanancias.medico_id)
        )).all()
        concepto = await concepto_ganancias(db)
        retenido: dict[int, Decimal] = defaultdict(Decimal)
        if concepto is not None:
            for m, aplicado in (await db.execute(
                select(Deduccion.medico_id, func.sum(DeduccionAplicacion.aplicado))
                .join(Deduccion, Deduccion.id == DeduccionAplicacion.deduccion_id)
                .where(DeduccionAplicacion.pago_id.in_(cerrados), Deduccion.descuento_id == concepto.id)
                .group_by(Deduccion.medico_id)
            )).all():
                retenido[int(m)] = Decimal(aplicado or 0)

        por_medico = {
            int(m): {"tipo_sujeto": t, "honorarios": Decimal(h or 0), "gastos": Decimal(g or 0),
                     "base": Decimal(b or 0), "calculado": Decimal(c or 0)}
            for m, t, h, g, b, c in bases
        }
        medicos = {m for m, v in por_medico.items() if v["calculado"] > 0} | {m for m, r in retenido.items() if r > 0}
        info = {
            r.ID: r
            for r in (await db.execute(
                select(ListadoMedico.ID, ListadoMedico.NRO_SOCIO, ListadoMedico.NOMBRE, ListadoMedico.CUIT,
                       ListadoMedico.es_organizacion)
                .where(ListadoMedico.ID.in_(medicos))
            )).all()
        } if medicos else {}
        for m in medicos:
            v = por_medico.get(m)
            med = info.get(m)
            filas_out.append({
                "medico_id": m,
                "nro_socio": med.NRO_SOCIO if med else None,
                "nombre": (med.NOMBRE or "").strip() if med else "",
                "cuit": med.CUIT if med else None,
                "tipo_sujeto": v["tipo_sujeto"] if v else tipo_sujeto(med.CUIT if med else None, bool(med.es_organizacion) if med else False),
                "honorarios": v["honorarios"] if v else CERO,
                "gastos": v["gastos"] if v else CERO,
                "base": v["base"] if v else CERO,
                "calculado": v["calculado"] if v else CERO,
                "retenido": retenido.get(m, CERO),
            })
        filas_out.sort(key=lambda f: (f["nombre"].upper(), f["nro_socio"] or 0))

    inst = await db.scalar(select(Institucion).limit(1))
    return {
        "anio": anio,
        "mes": mes,
        "pagos_incluidos": cerrados,
        "pagos_abiertos": abiertos,
        "agente": {
            "razon_social": getattr(inst, "razon_social", None),
            "cuit": getattr(inst, "cuit", None),
            "domicilio": getattr(inst, "domicilio", None),
            "localidad": getattr(inst, "localidad", None),
            "nro_agente_retencion": getattr(inst, "agente_retencion_ganancias", None),
        },
        "filas": filas_out,
        "totales": {
            k: sum((f[k] for f in filas_out), CERO)
            for k in ("honorarios", "gastos", "base", "calculado", "retenido")
        },
    }
