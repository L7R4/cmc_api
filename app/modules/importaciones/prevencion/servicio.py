"""Reparte el reporte de Prevención Salud entre los médicos del Colegio.

## El camino de una fila

    matrícula del reporte → listado_medico          → repetida o sin socio: elegir socio
    código del reporte    → nomenclador de la O.S.  → sin código, se omite
    código + fecha        → resolver_precio         → no admitido, se omite
    (autorización, código, fecha) ya cargada        → duplicada, se omite
    todo lo anterior OK                             → detalle_facturacion

Se carga en la 103 o en la 888, la obra social de prueba (`OBRAS_SOCIALES`).

Dos avisos que no impiden grabar: el código entra en $0 (no tiene precio a esa
fecha), y el médico ya tiene ese código cargado a mano en el período — la
carga manual va agrupada y sin autorización, así que el chequeo de duplicados
no la reconoce.

## Por qué no reusa `grabar_prestacion`

Es la misma tabla y los mismos valores fijos, pero `grabar_prestacion` está
hecho para el panel del prestador: da por sentado que la prestación es del
médico logueado, escribe `origen_carga='medico'` y hace un commit por fila.
Acá el médico cambia en cada fila, la carga es del Colegio y los cientos de
filas tienen que entrar o no entrar juntos.

Lo que sí se reusa es todo lo que decide montos —`resolver_precio`,
`calcular_importe_total`, `derivar_tipo`, `tpo_funcion_derivado`— para que un
import y una carga manual del mismo código den exactamente el mismo número.
"""
import logging
import re
from typing import Optional

from fastapi import HTTPException
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
from app.modules.importaciones.prevencion import NRO_PREVENCION, OBRAS_SOCIALES
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


def _obra_social(nro: Optional[int]) -> int:
    nro = nro or NRO_PREVENCION
    if nro not in OBRAS_SOCIALES:
        permitidas = ", ".join(str(n) for n in OBRAS_SOCIALES)
        raise HTTPException(422, f"El reporte de Prevención sólo se carga en las O.S. {permitidas}.")
    return nro


async def procesar(
    db: AsyncSession,
    *,
    filas: list[FilaReporte],
    periodo: Optional[str],
    usuario_carga: str,
    grabar: bool,
    obra_social: Optional[int] = None,
) -> ImportacionOut:
    """Resuelve todas las filas y, si `grabar`, las asienta en un solo commit.

    `periodo` lo elige el administrativo. Sin él se usa el puntero
    `periodo_medico_actual` de la obra social, que es el mismo que rige la
    carga manual.
    """
    nro = _obra_social(obra_social)
    cod_obr = str(nro)
    periodo_destino = periodo or await periodo_actual(db, nro)

    cabecera = await _get_factura(db, cod_obr, periodo_destino)
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
    socios = await nucleo.indice_socios(
        db,
        matriculas=matriculas,
        elegidos={f.nro_socio_elegido for f in filas if f.nro_socio_elegido},
        obra_social_nro=nro,
        etiqueta_os=f"la O.S. {nro}",
    )
    # Cuántas de cada clave ya están cargadas; se van descontando al recorrer.
    pendientes = await nucleo.ya_cargadas(db, nro, periodo_destino)
    a_mano = await nucleo.cargadas_a_mano(db, nro, periodo_destino)

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
        resultados.append(r)

        # Sin matrícula no se ofrece elegir: la elección vale para todas las
        # filas de una matrícula, y "NO INFORMADO" mezcla médicos distintos.
        if nucleo.matricula_int(f.matricula) is None:
            r.motivo = "La matrícula no está informada en el reporte."
            continue

        medico = socios.resolver(r, f.matricula, f.nro_socio_elegido)
        if medico is None:
            continue

        if not f.codigo:
            r.motivo = "La práctica vino sin código en el reporte."
            continue

        if f.fecha is None:
            r.motivo = "La fila no trae fecha de realización."
            continue

        clave = (f.nro_autorizacion or "", f.codigo, f.fecha)
        if pendientes[clave] > 0:
            # Esta ocurrencia ya está en la base; la siguiente igual, si el
            # archivo trae más de una, se evalúa contra lo que quede.
            pendientes[clave] -= 1
            r.resultado = DUPLICADA
            r.motivo = f"Ya está cargada en el período {periodo_destino}."
            continue

        try:
            precio = await resolver_precio(db, cod_obr, medico, f.codigo, f.fecha)
        except Exception as e:  # noqa: BLE001 — el motivo va a la fila, no al log
            r.motivo = f"No se pudo cotizar el código {f.codigo}: {getattr(e, 'detail', e)}"
            continue

        rechazada = fue_rechazada(f.estado)

        if not rechazada and not precio.admitido:
            r.motivo = precio.motivo or f"El código {f.codigo} no está admitido."
            continue

        # Lo que la obra social rechazó se registra, pero vale 0 y no entra a
        # ninguna factura: es la misma regla de `grabar_prestacion`.
        honorarios = CERO if rechazada else quantize_money(precio.honorarios)
        gastos = CERO if rechazada else quantize_money(precio.gastos)
        total = calcular_importe_total(honorarios, gastos, CERO, 1, SESION_UNICA)

        avisos = [r.aviso] if r.aviso else []
        sin_precio = bool(precio.sin_precio) and not rechazada
        if sin_precio:
            avisos.append(
                f"Entra en $0: el código no tiene precio en la O.S. {nro} a esa fecha. "
                "Queda «sin valorizar» y se revaloriza al cargar el precio."
            )
        elif not rechazada and total <= 0:
            avisos.append(f"Entra en $0: el precio cargado en la O.S. {nro} es $0.")
        manual = a_mano.get((str(medico.NRO_SOCIO), f.codigo))
        if manual:
            n, cant = manual
            avisos.append(
                "El médico ya tiene este código cargado a mano en el período "
                f"({n} {'fila' if n == 1 else 'filas'}, cantidad {cant}): revisá que no se duplique."
            )
        r.aviso = " ".join(avisos)

        r.honorarios = honorarios
        r.gastos = gastos
        r.importe_total = total
        r.estado_detalle = DETALLE_FUERA_DE_FACTURA if rechazada else DETALLE_ACTIVO
        r.resultado = GRABABLE

        if not rechazada:
            hay_facturables = True

        fila = DetalleFacturacionCMC(
            periodo=periodo_destino,
            cod_obr=cod_obr,
            cod_med=str(medico.NRO_SOCIO),
            cod_nom=f.codigo,
            nro_orden="0",  # NOT NULL legacy; se iguala al PK después del flush
            tipo=await derivar_tipo(
                db, f.codigo, TIPO_SANATORIO if medico.es_organizacion else None, cod_obr
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
            # queda NULL.
            nom_ape_p=(f.afiliado or "")[:60] or None,
            fecha_practica=f.fecha,
            autorizacion=(f.nro_autorizacion or None),
            porc=PORCENTAJE_COMPLETO,
            estado=r.estado_detalle,
            origen_carga=ORIGEN_COLEGIO,
            usuario=usuario_carga[:15],
            version=version_destino,
            calculo_snapshot=precio.snapshot,
            # Igual que la carga manual: lo que entra sin precio se marca para
            # que «Revalorizar» lo cotice cuando el precio exista.
            sin_valorizar="HG" if sin_precio else None,
            validacion_estado=VALIDACION_RECHAZADA if rechazada else VALIDACION_CARGADA,
            validacion_detalle=(f.estado or "")[:255],
        )

        a_grabar.append((r, fila))

    if grabar:
        nucleo.falta_elegir(resultados)
    if grabar and a_grabar:
        for _, fila in a_grabar:
            db.add(fila)
        await db.flush()
        for r, fila in a_grabar:
            fila.nro_orden = str(fila.id_detalle_prestaciones)
            r.id_detalle = fila.id_detalle_prestaciones
            r.resultado = GRABADA
        if hay_facturables:
            await _ensure_factura_abierta(db, cod_obr, periodo_destino, usuario_carga[:15])
        await db.commit()

    return ImportacionOut(
        resumen=nucleo.resumir(resultados, periodo_destino), filas=resultados
    )


async def periodos_disponibles(db: AsyncSession, obra_social: Optional[int] = None):
    return await nucleo.periodos_disponibles(db, _obra_social(obra_social))
