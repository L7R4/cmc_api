"""Graba la prestación validada en `detalle_facturacion` y la traduce a la
vista que consume el panel del prestador. Punto de encuentro único de todas
las obras sociales: cualquiera sea el validador, todas embudan acá.
"""
import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.files import url_archivo
from app.common.money import quantize_money
from app.db.models import DetalleFacturacionCMC, ListadoMedico
from app.modules.facturacion.service import (
    ORIGEN_MEDICO,
    _ensure_factura_abierta,
    _get_factura,
    calcular_importe_total,
    derivar_tipo,
    tpo_funcion_derivado,
)
from app.modules.nomenclador import service as service_nm
from app.modules.validaciones.core.periodos import partes_periodo

CERO = Decimal("0.00")

# Estado que devolvió la obra social (columna `validacion_estado`).
#   autorizada → el validador de la OS la aprobó en línea
#   rechazada  → el validador la rechazó
#   pendiente  → requiere gestión del afiliado en la OS
#   cargada    → carga manual: la OS autorizó por fuera, acá se registró
ESTADOS_FACTURABLES = ("autorizada", "cargada")

# `detalle_facturacion.estado`: 'A' entra a la factura, 'X' no. Las validaciones
# no autorizadas nacen en 'X' para no tocar ningún filtro del resto del sistema.
DETALLE_ACTIVO = "A"
DETALLE_FUERA_DE_FACTURA = "X"

# Valores fijos con los que este módulo asienta en `detalle_facturacion`. Una
# validación es siempre una prestación simple del propio médico: sin equipo, sin
# ayudante, sin clínica, una sesión, al 100% y con el precio del nomenclador.
SESION_UNICA = 1
PORCENTAJE_COMPLETO = 100
CALCULO_AUTOMATICO = "A"  # `manual` = 'A': el monto lo puso el lookup, no el operador


def to_dict(f: DetalleFacturacionCMC, descripcion: str = "") -> dict:
    """Vista de la prestación para el panel del prestador."""
    mes, anio = partes_periodo(f.periodo)
    return {
        "id": f.id_detalle_prestaciones,
        "fecha": f.fecha_practica,
        "codigo": f.cod_nom or "",
        "descripcion": descripcion,
        "nro_afiliado": f.dni_p or "",
        "nombre_afiliado": f.nom_ape_p or "",
        "nro_validacion": f.autorizacion,
        "estado": f.validacion_estado or "cargada",
        "estado_detalle": f.validacion_detalle or "",
        "cantidad": f.cantidad or 1,
        "honorarios": quantize_money(f.honorarios or 0),
        "gastos": quantize_money(f.gastos or 0),
        "coseguro": quantize_money(f.coseguro or 0),
        "total": quantize_money(f.importe_total or 0),
        "mes": mes,
        "anio": anio,
        "obra_social": int(f.cod_obr),
        # La orden es del paciente: sale por el endpoint autorizado, nunca
        # por /uploads. Ver app/common/files.py::url_archivo.
        "orden": url_archivo(f.orden_path),
    }


async def grabar_prestacion(
    db: AsyncSession,
    *,
    medico: ListadoMedico,
    obra_social_id: int,
    periodo: str,
    codigo: str,
    precio,
    cantidad: int,
    coseguro: Decimal,
    nro_afiliado: str,
    nombre_afiliado: str,
    nro_autorizacion: Optional[str],
    validacion_estado: str,
    validacion_detalle: str,
    validacion_respuesta: Optional[dict],
    fecha: datetime.date,
    usuario_carga: int,
) -> DetalleFacturacionCMC:
    """Graba la prestación validada en `detalle_facturacion`.

    Mapeo de campos (el resto de las columnas son legacy y quedan en NULL):

    | detalle_facturacion | de dónde sale |
    |---|---|
    | `origen_carga`      | siempre `'medico'` — la prestación es del médico, la tipee él o el Colegio en su nombre. Es lo que hace que la controle la fase médico del período |
    | `periodo`           | puntero `periodo_medico_actual` (override por O.S. → global) |
    | `cod_med` / `cod_obr` / `cod_nom` | NRO_SOCIO del prestador / NRO_OBRASOCIAL / código del nomenclador |
    | `dni_p` / `nom_ape_p` | nro y nombre de afiliado (`dni_p` es lo que liquidación y lotes ya leen como "nro de afiliado") |
    | `autorizacion`      | nro de autorización que devolvió (o que registró) la O.S. |
    | `honorarios` / `gastos` | lookup del nomenclador nuevo, igual que facturación |
    | `importe_total`     | (honorarios + gastos) × cantidad, menos el coseguro en las O.S. que lo descuentan. **0 si la O.S. no autorizó** |
    | `manual`            | 'A': el monto lo puso el lookup, no un operador |
    | `estado`            | 'A' si la O.S. autorizó; 'X' si no (no entra a ninguna factura) |
    | `usuario`           | NRO_SOCIO de **quien tipeó** la carga. Cuando el Colegio carga en nombre de un médico, `cod_med` es el médico y `usuario` el operador: ahí queda registrada la diferencia |
    | `validacion_*`      | qué respondió la O.S. y la traza cruda |

    Sin ayudante, sin clínica, sin equipo y al 100%: una validación es siempre
    una prestación simple del propio médico.
    """
    factura = validacion_estado in ESTADOS_FACTURABLES

    # Lo que la O.S. no autorizó vale 0: no se le puede facturar.
    honorarios = quantize_money(precio.honorarios) if factura else CERO
    gastos = quantize_money(precio.gastos) if factura else CERO
    coseguro = quantize_money(coseguro) if factura else CERO
    total = calcular_importe_total(honorarios, gastos, CERO, cantidad, SESION_UNICA)
    # Boreal: el afiliado ya pagó el coseguro de su bolsillo, así que a la obra
    # social se le factura el neto. Los conceptos quedan en el valor del
    # nomenclador (es lo que vale la práctica) y el descuento va sobre el total.
    total = quantize_money(total - coseguro)

    cabecera = await _get_factura(db, str(obra_social_id), periodo)
    version_destino = cabecera.version if cabecera is not None else 1

    # Fila del catálogo con la que se cotizó: el código puede ser propio de esta OS.
    nomenclador = await service_nm.resolver_nomenclador(db, codigo, obra_social_id)

    fila = DetalleFacturacionCMC(
        periodo=periodo,
        cod_obr=str(obra_social_id),
        cod_med=str(medico.NRO_SOCIO),
        cod_nom=codigo,
        nomenclador_id=nomenclador.id if nomenclador else None,
        nro_orden="0",  # placeholder — la columna es NOT NULL; se espeja el PK abajo
        tipo=await derivar_tipo(
            db, codigo, bool(medico.es_organizacion), str(obra_social_id)
        ),
        # tpo_funcion derivado SOLO para coexistencia (liquidación/lotes lo leen).
        tpo_funcion=tpo_funcion_derivado(honorarios, gastos, CERO),
        sesion=SESION_UNICA,
        cantidad=cantidad,
        honorarios=honorarios,
        gastos=gastos,
        ayudante=CERO,
        importe_total=total,
        coseguro=coseguro,
        manual=CALCULO_AUTOMATICO,
        dni_p=(nro_afiliado or "")[:20] or None,
        nom_ape_p=(nombre_afiliado or "")[:60] or None,
        fecha_practica=fecha,
        autorizacion=nro_autorizacion,
        porc=PORCENTAJE_COMPLETO,
        estado=DETALLE_ACTIVO if factura else DETALLE_FUERA_DE_FACTURA,
        origen_carga=ORIGEN_MEDICO,
        usuario=str(usuario_carga)[:15],
        version=version_destino,
        calculo_snapshot=precio.snapshot,
        validacion_estado=validacion_estado,
        validacion_detalle=validacion_detalle[:255],
        validacion_respuesta=validacion_respuesta,
    )
    db.add(fila)
    await db.flush()  # asigna id_detalle_prestaciones (PK)
    # `nro_orden` no es un contador propio (el PK alcanza como identificador
    # único) — se deja igual al PK sólo para no dejar NULL una columna NOT NULL
    # legacy que liquidación (`nro_orden_cmc`) y lotes todavía leen para mostrar.
    fila.nro_orden = str(fila.id_detalle_prestaciones)

    if factura:
        # Cabecera abierta para la OS+período; la crea si es la primera del período.
        # Las no autorizadas no abren factura: no hay nada que facturar.
        await _ensure_factura_abierta(
            db, str(obra_social_id), periodo, str(usuario_carga)[:15]
        )

    await db.commit()
    await db.refresh(fila)
    return fila
