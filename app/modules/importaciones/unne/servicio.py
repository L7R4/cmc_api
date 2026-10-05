"""Carga en `detalle_facturacion` el Excel que exporta el sistema de UNNE.

## En qué se diferencia de Swiss y Prevención

- **El importe manda.** El Excel trae lo que UNNE liquida por cada ítem y eso es
  lo que se guarda; nuestro nomenclador no recotiza. Sólo se usa para dos cosas:
  partir el importe de las filas "Hon+Gto" en honorarios y gastos (el Excel trae
  un único monto), y avisar cuando nuestro valor no coincide con el de UNNE. Se
  leen los valores vigentes del código en la O.S. 81 sin el filtro de especialidad
  del médico (`_divisiones_vigentes`): para partir un monto alcanza con que alguna
  variante sume lo mismo.
- **Matrículas con más de un socio.** No se descartan: la fila queda en
  `elegir_socio` con los candidatos y el administrativo elige en la
  previsualización; el confirmar trae su elección en `nro_socio_elegido`.
- **Duplicados por orden.** UNNE numera las órdenes (`Orden N°`) y una orden
  puede tener varios ítems (`Reg.`). Una fila ya está cargada si en la O.S. 81
  hay otra no anulada con el mismo código y esa orden en `autorizacion` (lo que
  graba este importador) o en `nro_orden` (cargas manuales y el puente legacy), en
  cualquier período.

## El camino de una fila

    provincia ≠ Corrientes                       → omitida
    matrícula → socio (único / sugerido / elegido) → si no, elegir_socio
    sin código / fecha / importe                  → omitida
    (orden, reg) repetido en el archivo           → duplicada
    (orden, código) ya cargado                    → duplicada
    todo OK                                       → detalle_facturacion
"""
import datetime
import logging
import re
from collections import Counter
from decimal import Decimal
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models import DetalleFacturacionCMC
from app.db.models.nomenclador_cmc import HistorialPrecioCodigo
from app.modules.facturacion.service import (
    ORIGEN_COLEGIO,
    TIPO_SANATORIO,
    _ensure_factura_abierta,
    _gate_carga,
    _get_factura,
    derivar_tipo,
    tpo_funcion_derivado,
)
from app.modules.importaciones import nucleo
from app.modules.importaciones.nucleo import (
    CERO,
    DETALLE_ACTIVO,
    DUPLICADA,
    GRABABLE,
    GRABADA,
    OMITIDA,
    SESION_UNICA,
    VALIDACION_CARGADA,
)
from app.modules.importaciones.schemas import (
    FilaResultado,
    FilaUnne,
    ImportacionOut,
)
from app.modules.importaciones.unne import NRO_UNNE
from app.modules.nomenclador import service as service_nm
from app.modules.validaciones.core.periodos import periodo_actual

log = logging.getLogger(__name__)

# Monto cargado a mano, no cotizado (mismo valor que las cargas manuales legacy).
CALCULO_MANUAL = "M"
FUNCION_HON_GTO = "HON+GTO"
TOLERANCIA = Decimal("0.01")
_PROVINCIA_PROPIA = "CORRIENTES"


def es_hon_gto(funcion: str) -> bool:
    return re.sub(r"\s", "", (funcion or "").upper()) == FUNCION_HON_GTO


def partir_importe(
    unitario: Decimal, hon_gto: bool, divisiones: list[tuple[Decimal, Decimal]],
) -> tuple[Decimal, Decimal, str]:
    """(honorarios, gastos, aviso) de un ítem a partir del importe unitario de UNNE.

    `divisiones`: (honorarios, gastos) de cada variante vigente del código en UNNE.
    - "Hon+Gto": si alguna variante suma el mismo importe, se respeta su división
      (prefiriendo la que tiene gastos); si no, todo a honorarios y se avisa.
    - "ESPECIALISTA": todo honorarios; si hay valores y ninguno coincide, se avisa.
    """
    unitario = quantize_money(unitario)
    cuadran = [(h, g) for h, g in divisiones if abs(quantize_money(h + g) - unitario) <= TOLERANCIA]
    if hon_gto:
        con_gastos = [(h, g) for h, g in cuadran if g > 0]
        if con_gastos:
            h = quantize_money(con_gastos[0][0])
            return h, unitario - h, ""
        if cuadran:
            return unitario, CERO, ""
        if not divisiones:
            return unitario, CERO, "Hon+Gto sin valor en nuestro nomenclador: va todo a honorarios."
    elif cuadran or not divisiones:
        return unitario, CERO, ""
    nuestros = ", ".join(sorted({f"${quantize_money(h + g):,.2f}" for h, g in divisiones}))
    return unitario, CERO, f"Importe distinto al de nuestro nomenclador ({nuestros}): va todo a honorarios."


async def _divisiones_vigentes(
    db: AsyncSession, nomenclador_id: int, fecha: datetime.date,
) -> list[tuple[Decimal, Decimal]]:
    """(honorarios, gastos) de cada variante del código vigente a `fecha` en UNNE.

    Lee el historial de precios como `lookup_precio`, pero sin validar la
    especialidad del médico ni la antigüedad de la fecha: acá no se cotiza, sólo
    se busca cómo partir un importe que ya viene dado.
    """
    H = HistorialPrecioCodigo
    filas = (await db.execute(
        select(H).where(
            H.nomenclador_id == nomenclador_id,
            H.obra_social_nro == NRO_UNNE,
            H.vigencia_desde <= fecha,
            (H.vigencia_hasta.is_(None)) | (H.vigencia_hasta >= fecha),
        ).order_by(H.vigencia_desde.desc())
    )).scalars().all()
    por_variante: dict = {}
    for f in filas:
        por_variante.setdefault((f.origen, f.especialidad_id_colegio), f)

    def _suma(snapshot, concepto: str) -> Decimal:
        return quantize_money(sum(
            (Decimal(str(c.get("subtotal") or 0)) for c in (snapshot or []) if c.get("concepto") == concepto),
            Decimal("0"),
        ))

    return [(_suma(f.componentes_snapshot, "Honorarios"), _suma(f.componentes_snapshot, "Gastos"))
            for f in por_variante.values()]


async def _ya_cargadas(db: AsyncSession, ordenes: set[str]) -> dict[tuple[str, str], list[tuple[int, str]]]:
    """(orden, código) → [(id, período)] de lo que ya está en la O.S. 81, sin anuladas."""
    if not ordenes:
        return {}
    M = DetalleFacturacionCMC
    filas = (await db.execute(
        select(M.id_detalle_prestaciones, M.periodo, M.autorizacion, M.nro_orden, M.cod_nom).where(
            M.cod_obr == str(NRO_UNNE),
            M.estado != "X",
            or_(M.autorizacion.in_(ordenes), M.nro_orden.in_(ordenes)),
        )
    )).all()
    out: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for id_, periodo, autorizacion, nro_orden, cod in filas:
        orden = str(autorizacion) if str(autorizacion or "") in ordenes else str(nro_orden)
        out.setdefault((orden, str(cod or "")), []).append((id_, periodo))
    return out


async def procesar(
    db: AsyncSession,
    *,
    filas: list[FilaUnne],
    periodo: Optional[str],
    usuario_carga: str,
    grabar: bool,
) -> ImportacionOut:
    """Resuelve todas las filas y, si `grabar`, las asienta en un solo commit."""
    periodo_destino = periodo or await periodo_actual(db, NRO_UNNE)
    cabecera = await _get_factura(db, str(NRO_UNNE), periodo_destino)
    # Carga del Colegio: vale aunque la fase médico esté cerrada, no si el Colegio cerró.
    _gate_carga(cabecera, ORIGEN_COLEGIO)
    version_destino = cabecera.version if cabecera is not None else 1

    matriculas = {m for m in (nucleo.matricula_int(f.matricula) for f in filas) if m is not None}
    socios = await nucleo.indice_socios(
        db, matriculas=matriculas, obra_social_nro=NRO_UNNE, etiqueta_os="UNNE",
        elegidos={f.nro_socio_elegido for f in filas if f.nro_socio_elegido},
    )
    existentes = await _ya_cargadas(db, {f.orden.strip() for f in filas if f.orden.strip()})
    # Cuántas veces está cargado cada (orden, código); se va descontando al recorrer.
    pendientes = Counter({k: len(v) for k, v in existentes.items()})
    vistos: set[tuple[str, int]] = set()
    nomencladores: dict[str, Optional[int]] = {}
    divisiones: dict[tuple[int, datetime.date], list[tuple[Decimal, Decimal]]] = {}

    resultados: list[FilaResultado] = []
    a_grabar: list[tuple[FilaResultado, DetalleFacturacionCMC]] = []

    for i, f in enumerate(filas):
        orden = f.orden.strip()
        r = FilaResultado(
            indice=i, resultado=OMITIDA, nro_autorizacion=orden, reg=f.reg, fecha=f.fecha,
            codigo=f.codigo, descripcion=f.descripcion, afiliado=f.paciente,
            estado_reporte=f.funcion, matricula=f.matricula,
        )
        resultados.append(r)

        if f.provincia and f.provincia.strip().upper() != _PROVINCIA_PROPIA:
            r.motivo = f"Prestación de otra provincia ({f.provincia.strip()})."
            continue

        # ── Socio ──
        medico = socios.resolver(r, f.matricula, f.nro_socio_elegido)
        if medico is None:
            continue

        if not f.codigo or f.fecha is None or not orden:
            r.motivo = "Falta el código, la fecha o el número de orden."
            continue
        cantidad = max(f.cantidad or 1, 1)
        importe = quantize_money(f.importe or CERO)
        if importe <= 0:
            r.motivo = "La fila no trae importe."
            continue

        # ── Duplicados ──
        if (orden, f.reg) in vistos:
            r.resultado = DUPLICADA
            r.motivo = f"La orden {orden} ítem {f.reg} está repetida en el archivo."
            continue
        vistos.add((orden, f.reg))
        clave = (orden, f.codigo)
        if pendientes[clave] > 0:
            pendientes[clave] -= 1
            id_, per = existentes[clave][pendientes[clave]]
            r.resultado = DUPLICADA
            r.motivo = f"Ya está cargada (prestación {id_}, período {per})."
            continue

        # ── Montos ──
        if f.codigo not in nomencladores:
            nom = await service_nm.resolver_nomenclador(db, f.codigo, NRO_UNNE)
            nomencladores[f.codigo] = nom.id if nom is not None else None
        nom_id = nomencladores[f.codigo]
        if nom_id is not None and (nom_id, f.fecha) not in divisiones:
            divisiones[(nom_id, f.fecha)] = await _divisiones_vigentes(db, nom_id, f.fecha)
        honorarios, gastos, aviso = partir_importe(
            importe / cantidad, es_hon_gto(f.funcion),
            divisiones.get((nom_id, f.fecha), []) if nom_id is not None else [],
        )
        if nom_id is None:
            aviso = " ".join(x for x in (aviso, "El código no está en nuestro catálogo.") if x)
        if aviso:
            r.aviso = " ".join(x for x in (r.aviso, aviso) if x)

        r.honorarios, r.gastos, r.importe_total = honorarios, gastos, importe
        r.estado_detalle = DETALLE_ACTIVO
        r.resultado = GRABABLE

        a_grabar.append((r, DetalleFacturacionCMC(
            periodo=periodo_destino,
            cod_obr=str(NRO_UNNE),
            cod_med=str(medico.NRO_SOCIO),
            cod_nom=f.codigo,
            nomenclador_id=nomencladores[f.codigo],
            nro_orden="0",  # NOT NULL legacy; se iguala al PK después del flush
            tipo=await derivar_tipo(
                db, f.codigo, TIPO_SANATORIO if medico.es_organizacion else None, str(NRO_UNNE),
            ),
            tpo_funcion=tpo_funcion_derivado(honorarios, gastos, CERO),
            sesion=SESION_UNICA,
            cantidad=cantidad,
            honorarios=honorarios,
            gastos=gastos,
            ayudante=CERO,
            # El de UNNE tal cual (puede diferir en centavos de unitario × cantidad).
            importe_total=importe,
            coseguro=CERO,
            manual=CALCULO_MANUAL,
            dni_p=(f.dni or "").strip()[:20] or None,
            nom_ape_p=(f.paciente or "").strip()[:60] or None,
            fecha_practica=f.fecha,
            autorizacion=orden[:30],
            porc=f.porcentaje or 100,
            estado=DETALLE_ACTIVO,
            origen_carga=ORIGEN_COLEGIO,
            usuario=usuario_carga[:15],
            version=version_destino,
            validacion_estado=VALIDACION_CARGADA,
            validacion_detalle=f"Importado de UNNE: orden {orden}, ítem {f.reg}"[:255],
        )))

    if grabar:
        nucleo.falta_elegir(resultados)
        if a_grabar:
            for _, fila in a_grabar:
                db.add(fila)
            await db.flush()
            for r, fila in a_grabar:
                fila.nro_orden = str(fila.id_detalle_prestaciones)
                r.id_detalle = fila.id_detalle_prestaciones
                r.resultado = GRABADA
            await _ensure_factura_abierta(db, str(NRO_UNNE), periodo_destino, usuario_carga[:15])
            await db.commit()

    return ImportacionOut(resumen=nucleo.resumir(resultados, periodo_destino), filas=resultados)


async def periodos_disponibles(db: AsyncSession):
    return await nucleo.periodos_disponibles(db, NRO_UNNE)
