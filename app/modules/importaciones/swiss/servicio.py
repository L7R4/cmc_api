"""Reparte el reporte de liquidación de Swiss Medical entre los médicos.

Mismo camino que Prevención (ver `importaciones/nucleo.py`), con cuatro
diferencias que vienen del reporte:

  * **El código hay que traducirlo.** Swiss manda ocho dígitos y el Colegio usa
    seis. Ver `homologador.py`.
  * **La cantidad viene en el archivo**, no se asume 1: hay ítems con más de
    una unidad.
  * **El copago es coseguro ya cobrado al afiliado.** Se descuenta del importe,
    como en Boreal: los conceptos quedan en el valor del nomenclador y el
    descuento va sobre el total.
  * **La matrícula trae la provincia** ("W-3972"). Las que no son de Corrientes
    no se resuelven contra `listado_medico` sólo por el número, así que se
    omiten en vez de arriesgar el homónimo de otra provincia.

La clave de duplicados es el `transacción_item`, que Swiss garantiza único en
todo el reporte y que el front manda en `nro_autorizacion`. La columna
`autorización` del reporte viene vacía en el 99% de las filas, así que sola
daría 254 claves para 1.127 prácticas.
"""
import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models import DetalleFacturacionCMC
from app.modules.facturacion.service import (
    ORIGEN_COLEGIO,
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
)
from app.modules.importaciones.schemas import (
    FilaReporte,
    FilaResultado,
    ImportacionOut,
)
from app.modules.importaciones.swiss import NRO_SWISS
from app.modules.importaciones.swiss import homologador
from app.modules.validaciones.core.periodos import periodo_actual

log = logging.getLogger(__name__)

# Letra de provincia en la matrícula del efector.
CORRIENTES = "W"


def _provincia(matricula: str) -> str:
    """"W-3972" → "W". Vacío si la matrícula no la declara."""
    m = (matricula or "").strip()
    return m[0].upper() if len(m) > 1 and m[1:2] == "-" and m[0].isalpha() else ""


async def procesar(
    db: AsyncSession,
    *,
    filas: list[FilaReporte],
    periodo: Optional[str],
    usuario_carga: str,
    grabar: bool,
) -> ImportacionOut:
    """Resuelve todas las filas y, si `grabar`, las asienta en un solo commit."""
    periodo_destino = periodo or await periodo_actual(db, NRO_SWISS)

    cabecera = await _get_factura(db, str(NRO_SWISS), periodo_destino)
    # Actor `colegio`: importar es carga del Colegio, así que un período con la
    # fase médico cerrada sigue valiendo. Lo que no se puede es cargar en uno
    # que el Colegio ya cerró o liquidó.
    _gate_carga(cabecera, ORIGEN_COLEGIO)
    version_destino = cabecera.version if cabecera is not None else 1

    matriculas = {
        m
        for m in (nucleo.matricula_int(f.matricula) for f in filas)
        if m is not None
    }
    medicos = await nucleo.indice_medicos(db, matriculas)
    pendientes = await nucleo.ya_cargadas(db, NRO_SWISS, periodo_destino)

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

        provincia = _provincia(f.matricula)
        if provincia and provincia != CORRIENTES:
            r.motivo = (
                f"La matrícula es de otra provincia ({provincia}): no se puede "
                "resolver contra el padrón del Colegio."
            )
            resultados.append(r)
            continue

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

        if f.fecha is None:
            r.motivo = "La fila no trae fecha de prestación."
            resultados.append(r)
            continue

        codigo = homologador.homologar(f.codigo)
        if not codigo:
            r.motivo = f"El código {f.codigo!r} de Swiss no tiene equivalente en el nomenclador."
            resultados.append(r)
            continue

        clave = (f.nro_autorizacion or "", codigo, f.fecha)
        if pendientes[clave] > 0:
            pendientes[clave] -= 1
            r.resultado = DUPLICADA
            r.motivo = f"Ya está cargada en el período {periodo_destino}."
            resultados.append(r)
            continue

        try:
            precio = await resolver_precio(db, str(NRO_SWISS), medico, codigo, f.fecha)
        except Exception as e:  # noqa: BLE001 — el motivo va a la fila, no al log
            r.motivo = f"No se pudo cotizar el código {codigo}: {e}"
            resultados.append(r)
            continue

        if not precio.admitido:
            r.motivo = precio.motivo or f"El código {codigo} no está admitido."
            resultados.append(r)
            continue

        cantidad = max(1, f.cantidad or 1)
        honorarios = quantize_money(precio.honorarios)
        gastos = quantize_money(precio.gastos)
        coseguro = quantize_money(Decimal(str(f.coseguro or 0)))

        total = calcular_importe_total(honorarios, gastos, CERO, cantidad, SESION_UNICA)
        # El afiliado ya pagó el copago en el consultorio: a la obra social se
        # le factura el neto. Mismo criterio que Boreal en validaciones.
        total = quantize_money(total - coseguro)

        # Un precio en cero no es facturable, pero la prestación existió: se
        # registra fuera de la factura para que quede constancia y se vea en la
        # revisión, en vez de desaparecer del reporte.
        # Un copago mayor que el valor de la práctica daría un importe
        # negativo: se corta en cero y se avisa, porque es un dato del
        # reporte que alguien tiene que mirar.
        if total < CERO:
            r.motivo = (
                f"El copago ({coseguro}) supera el valor de la práctica: "
                "queda en cero, fuera de la factura."
            )
            total = CERO

        factura = total > CERO
        if not factura and not r.motivo:
            r.motivo = (
                "La obra social no tiene valor cargado para este código: "
                "queda registrada en cero, fuera de la factura."
            )

        r.honorarios = honorarios
        r.gastos = gastos
        r.importe_total = total if factura else CERO
        r.estado_detalle = DETALLE_ACTIVO if factura else DETALLE_FUERA_DE_FACTURA
        r.resultado = GRABABLE
        if factura:
            hay_facturables = True

        fila = DetalleFacturacionCMC(
            periodo=periodo_destino,
            cod_obr=str(NRO_SWISS),
            cod_med=str(medico.NRO_SOCIO),
            cod_nom=codigo,
            nro_orden="0",  # NOT NULL legacy; se iguala al PK después del flush
            tipo=await derivar_tipo(
                db, codigo, bool(medico.es_organizacion), str(NRO_SWISS)
            ),
            tpo_funcion=tpo_funcion_derivado(honorarios, gastos, CERO),
            sesion=SESION_UNICA,
            cantidad=cantidad,
            honorarios=honorarios if factura else CERO,
            gastos=gastos if factura else CERO,
            ayudante=CERO,
            importe_total=r.importe_total,
            coseguro=coseguro if factura else CERO,
            manual=CALCULO_AUTOMATICO,
            # Swiss sí manda el número de afiliado: es la credencial.
            dni_p=(f.nro_afiliado or "")[:20] or None,
            nom_ape_p=(f.afiliado or "")[:60] or None,
            fecha_practica=f.fecha,
            autorizacion=(f.nro_autorizacion or None),
            porc=PORCENTAJE_COMPLETO,
            estado=r.estado_detalle,
            origen_carga=ORIGEN_COLEGIO,
            usuario=usuario_carga[:15],
            version=version_destino,
            calculo_snapshot=precio.snapshot,
            validacion_estado=VALIDACION_CARGADA,
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
                db, str(NRO_SWISS), periodo_destino, usuario_carga[:15]
            )
        await db.commit()

    return ImportacionOut(
        resumen=nucleo.resumir(resultados, periodo_destino), filas=resultados
    )


async def periodos_disponibles(db: AsyncSession):
    return await nucleo.periodos_disponibles(db, NRO_SWISS)
