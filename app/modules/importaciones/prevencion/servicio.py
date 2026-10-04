"""Reparte el reporte de Prevención Salud entre los médicos del Colegio.

## El camino de una fila

    matrícula del reporte → listado_medico          → sin médico, se omite
    código del reporte    → nomenclador de la 103   → sin código, se omite
    código + fecha        → resolver_precio         → sin precio, se omite
    (autorización, código, fecha) ya cargada        → duplicada, se omite
    todo lo anterior OK                             → detalle_facturacion

## Por qué no reusa `grabar_prestacion`

Es la misma tabla y los mismos valores fijos, pero `grabar_prestacion` está
hecho para el panel del prestador: da por sentado que la prestación es del
médico logueado, escribe `origen_carga='medico'` y hace un commit por fila.
Acá el médico cambia en cada fila, la carga es del Colegio y las seiscientas
filas tienen que entrar o no entrar juntas.

Lo que sí se reusa es todo lo que decide montos —`resolver_precio`,
`calcular_importe_total`, `derivar_tipo`, `tpo_funcion_derivado`— para que un
import y una carga manual del mismo código den exactamente el mismo número.
"""
import logging
import re
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models import DetalleFacturacionCMC
from app.modules.facturacion.service import (
    ORIGEN_COLEGIO,
    TIPO_SANATORIO,
    _ensure_factura_abierta,
    _gate_carga,
    _get_factura,
    calcular_importe_total,
    derivar_tipo,
    resolver_precio,
    tpo_funcion_derivado,
)
from app.modules.importaciones import nucleo
from app.modules.importaciones.nucleo import (
    CALCULO_AUTOMATICO,
    CERO,
    DETALLE_ACTIVO,
    DETALLE_FUERA_DE_FACTURA,
    DUPLICADA,
    GRABABLE,
    GRABADA,
    OMITIDA,
    PORCENTAJE_COMPLETO,
    SESION_UNICA,
    VALIDACION_CARGADA,
    VALIDACION_RECHAZADA,
)
from app.modules.importaciones.prevencion import NRO_PREVENCION
from app.modules.importaciones.schemas import (
    FilaReporte,
    FilaResultado,
    ImportacionOut,
)
from app.modules.validaciones.core.periodos import periodo_actual

log = logging.getLogger(__name__)

_RECHAZO = re.compile(r"RECHAZ|NO AUTORIZ", re.IGNORECASE)


def fue_rechazada(estado: str) -> bool:
    """Misma regla que el front (`prevencion.parser.ts`)."""
    return bool(_RECHAZO.search(estado or ""))


async def procesar(
    db: AsyncSession,
    *,
    filas: list[FilaReporte],
    periodo: Optional[str],
    usuario_carga: str,
    grabar: bool,
) -> ImportacionOut:
    """Resuelve todas las filas y, si `grabar`, las asienta en un solo commit.

    `periodo` lo elige el administrativo. Sin él se usa el puntero
    `periodo_medico_actual` de la obra social, que es el mismo que rige la
    carga manual.
    """
    periodo_destino = periodo or await periodo_actual(db, NRO_PREVENCION)

    cabecera = await _get_factura(db, str(NRO_PREVENCION), periodo_destino)
    # Actor `colegio`, no `medico`: importar es carga del Colegio, así que un
    # período con la fase médico cerrada sigue siendo válido. Lo que no se
    # puede es cargar en uno que el Colegio ya cerró o liquidó — ahí la factura
    # está emitida y meterle filas la descuadra.
    _gate_carga(cabecera, ORIGEN_COLEGIO)

    matriculas = {
        m
        for m in (nucleo.matricula_int(f.matricula) for f in filas)
        if m is not None
    }
    medicos = await nucleo.indice_medicos(db, matriculas)
    # Cuántas de cada clave ya están cargadas; se van descontando al recorrer.
    pendientes = await nucleo.ya_cargadas(db, NRO_PREVENCION, periodo_destino)

    version_destino = cabecera.version if cabecera is not None else 1

    resultados: list[FilaResultado] = []
    a_grabar: list[tuple[FilaResultado, DetalleFacturacionCMC]] = []
    hay_facturables = False

    for i, f in enumerate(filas):
        r = FilaResultado(
            indice=i,
            resultado=OMITIDA,
            nro_autorizacion=f.nro_autorizacion,
            fecha=f.fecha,
            codigo=f.codigo,
            descripcion=f.descripcion,
            afiliado=f.afiliado,
            estado_reporte=f.estado,
            matricula=f.matricula,
        )

        mat = nucleo.matricula_int(f.matricula)
        medico = medicos.get(mat) if mat is not None else None
        if medico is None:
            r.motivo = (
                "La matrícula no está informada en el reporte."
                if mat is None
                else f"Ninguna cuenta del Colegio tiene la matrícula {mat}."
            )
            resultados.append(r)
            continue

        r.nro_socio = medico.NRO_SOCIO
        r.medico = medico.NOMBRE or ""

        if not f.codigo:
            r.motivo = "La práctica vino sin código en el reporte."
            resultados.append(r)
            continue

        if f.fecha is None:
            r.motivo = "La fila no trae fecha de realización."
            resultados.append(r)
            continue

        clave = (f.nro_autorizacion or "", f.codigo, f.fecha)
        if pendientes[clave] > 0:
            # Esta ocurrencia ya está en la base; la siguiente igual, si el
            # archivo trae más de una, se evalúa contra lo que quede.
            pendientes[clave] -= 1
            r.resultado = DUPLICADA
            r.motivo = f"Ya está cargada en el período {periodo_destino}."
            resultados.append(r)
            continue

        try:
            precio = await resolver_precio(
                db, str(NRO_PREVENCION), medico, f.codigo, f.fecha
            )
        except Exception as e:  # noqa: BLE001 — el motivo va a la fila, no al log
            r.motivo = f"No se pudo cotizar el código {f.codigo}: {e}"
            resultados.append(r)
            continue

        rechazada = fue_rechazada(f.estado)

        if not rechazada and not precio.admitido:
            r.motivo = precio.motivo or f"El código {f.codigo} no está admitido."
            resultados.append(r)
            continue

        # Lo que la obra social rechazó se registra, pero vale 0 y no entra a
        # ninguna factura: es la misma regla de `grabar_prestacion`.
        honorarios = CERO if rechazada else quantize_money(precio.honorarios)
        gastos = CERO if rechazada else quantize_money(precio.gastos)
        total = calcular_importe_total(honorarios, gastos, CERO, 1, SESION_UNICA)

        r.honorarios = honorarios
        r.gastos = gastos
        r.importe_total = total
        r.estado_detalle = DETALLE_FUERA_DE_FACTURA if rechazada else DETALLE_ACTIVO
        r.resultado = GRABABLE

        if not rechazada:
            hay_facturables = True

        fila = DetalleFacturacionCMC(
            periodo=periodo_destino,
            cod_obr=str(NRO_PREVENCION),
            cod_med=str(medico.NRO_SOCIO),
            cod_nom=f.codigo,
            nro_orden="0",  # NOT NULL legacy; se iguala al PK después del flush
            tipo=await derivar_tipo(
                db, f.codigo, TIPO_SANATORIO if medico.es_organizacion else None, str(NRO_PREVENCION)
            ),
            tpo_funcion=tpo_funcion_derivado(honorarios, gastos, CERO),
            sesion=SESION_UNICA,
            cantidad=1,
            honorarios=honorarios,
            gastos=gastos,
            ayudante=CERO,
            importe_total=total,
            coseguro=CERO,
            manual=CALCULO_AUTOMATICO,
            # El reporte da el nombre del afiliado, nunca su número: `dni_p`
            # queda NULL, igual que en todo lo ya cargado de esta obra social.
            nom_ape_p=(f.afiliado or "")[:60] or None,
            fecha_practica=f.fecha,
            autorizacion=(f.nro_autorizacion or None),
            porc=PORCENTAJE_COMPLETO,
            estado=r.estado_detalle,
            origen_carga=ORIGEN_COLEGIO,
            usuario=usuario_carga[:15],
            version=version_destino,
            calculo_snapshot=precio.snapshot,
            validacion_estado=VALIDACION_RECHAZADA if rechazada else VALIDACION_CARGADA,
            validacion_detalle=(f.estado or "")[:255],
        )

        a_grabar.append((r, fila))
        resultados.append(r)

    if grabar and a_grabar:
        for _, fila in a_grabar:
            db.add(fila)
        await db.flush()
        for r, fila in a_grabar:
            fila.nro_orden = str(fila.id_detalle_prestaciones)
            r.id_detalle = fila.id_detalle_prestaciones
            r.resultado = GRABADA
        if hay_facturables:
            await _ensure_factura_abierta(
                db, str(NRO_PREVENCION), periodo_destino, usuario_carga[:15]
            )
        await db.commit()

    return ImportacionOut(
        resumen=nucleo.resumir(resultados, periodo_destino), filas=resultados
    )


async def periodos_disponibles(db: AsyncSession):
    return await nucleo.periodos_disponibles(db, NRO_PREVENCION)
