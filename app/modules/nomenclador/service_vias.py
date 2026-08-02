"""
Motor de ajuste de precio por vía quirúrgica (tradicional / laparoscópica).

La vía ya NO se modela como un código distinto del nomenclador (ver
NomencladorCMC, que dejó de tener `proviene_de_id`): es un atributo de la
prestación (`detalle_facturacion.via`) y el precio laparoscópico se deriva acá,
en tiempo de cotización, a partir de los mismos componentes que produce
`calcular_precio_total`/`lookup_precio`. No se crean códigos ni `nm_valores`
nuevos para la variante laparoscópica.

Reglas de negocio (hardcodeadas a pedido — solo aplican a los galenos de
cirugía adulto/infantil):
- Elegibilidad: el componente Honorarios debe ser calculable y su galeno debe
  ser `cirugia_adulto` o `cirugia_infantil`.
- Galeno de 7 niveles: se cotiza con el nivel siguiente (nivel 5 → precio de
  nivel 6). Nivel tope (7) no admite laparoscopía.
- Galeno de 10 niveles: +25% sobre TODOS los componentes (incluidos los que no
  usan el galeno de Honorarios, p. ej. Gastos).
- Cualquier otro código (Honorarios sin ese galeno, o galeno con una cantidad
  de niveles distinta de 7/10) rechaza con `ViaNoAplicableError`.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.money import quantize_money
from app.db.models.nomenclador_cmc import Galeno
from app.modules.nomenclador.schemas import ComponenteLookupOut

VIA_TRADICIONAL = "T"
VIA_LAPAROSCOPICA = "L"
VIAS_VALIDAS = (VIA_TRADICIONAL, VIA_LAPAROSCOPICA)

# Únicos galenos para los que existe regla de recargo/salto por laparoscopía.
# La elegibilidad se decide por el galeno del componente Honorarios.
GALENOS_CON_LAPAROSCOPIA = ("cirugia_adulto", "cirugia_infantil")

NIVELES_SALTO_NIVEL = 7      # galeno de 7 niveles → cotiza el nivel siguiente
NIVELES_RECARGO = 10         # galeno de 10 niveles → recargo porcentual
RECARGO_10_NIVELES = Decimal("1.25")   # +25%, aplicado a todos los componentes

_CONCEPTO_A_UNIDAD: dict[str, str] = {
    "Honorarios": "unidades_honorarios",
    "Ayudante": "unidades_ayudante",
    "Gastos": "unidades_gastos",
}


class ViaNoAplicableError(Exception):
    def __init__(self, message: str, status_code: int = 422):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def niveles_vigentes(
    db: AsyncSession, obra_social_nro: int, codigo_galeno: str
) -> List[int]:
    """Niveles distintos, activos y con vigencia abierta, de (OS, codigo_galeno).
    Ordenados ascendente. Un galeno "de N niveles" es el que tiene N filas así."""
    stmt = (
        select(Galeno.nivel)
        .where(
            Galeno.obra_social_nro == obra_social_nro,
            Galeno.codigo == codigo_galeno,
            Galeno.nivel.is_not(None),
            Galeno.vigencia_hasta.is_(None),
            Galeno.activo == True,
        )
        .distinct()
    )
    niveles = [row[0] for row in (await db.execute(stmt)).all()]
    return sorted(niveles)


async def _buscar_galeno_nivel(
    db: AsyncSession, obra_social_nro: int, codigo_galeno: str, nivel: int
) -> Optional[Galeno]:
    stmt = select(Galeno).where(
        Galeno.obra_social_nro == obra_social_nro,
        Galeno.codigo == codigo_galeno,
        Galeno.nivel == nivel,
        Galeno.vigencia_hasta.is_(None),
        Galeno.activo == True,
    )
    return (await db.execute(stmt)).scalars().first()


def _requiere_unidad(galeno: Optional[Galeno], concepto: str) -> Optional[Decimal]:
    if galeno is None:
        return None
    return getattr(galeno, _CONCEPTO_A_UNIDAD.get(concepto, ""), None)


async def ajustar_componentes_por_via(
    db: AsyncSession,
    obra_social_nro: int,
    componentes: List[ComponenteLookupOut],
    via: str,
) -> tuple[List[ComponenteLookupOut], Decimal, Optional[int]]:
    """Ajusta los componentes de un precio ya calculado según la vía solicitada.

    Puro respecto de la persistencia: no escribe nada, solo transforma la lista
    de componentes que ya produjo `calcular_precio_total`/`lookup_precio`. Sirve
    igual para cotizar, para el snapshot que se guarda en la prestación, y para
    listados (reportes/tabla de valores).

    Retorna (componentes_ajustados, precio_total, nivel_cotizado).
    Lanza `ViaNoAplicableError` si `via == VIA_LAPAROSCOPICA` y el código no admite
    laparoscopía (código fuera del universo cirugía adulto/infantil, o nivel tope).
    """
    if via != VIA_LAPAROSCOPICA:
        total = quantize_money(sum((c.subtotal for c in componentes), Decimal("0")))
        return list(componentes), total, None

    comp_hon = next((c for c in componentes if c.concepto == "Honorarios"), None)
    if comp_hon is None or comp_hon.tipo != "calculable" or not comp_hon.galeno_codigo:
        raise ViaNoAplicableError(
            "El código no admite vía laparoscópica: no tiene un componente de "
            "Honorarios calculable por galeno"
        )

    codigo_galeno = comp_hon.galeno_codigo
    if codigo_galeno not in GALENOS_CON_LAPAROSCOPIA:
        raise ViaNoAplicableError(
            f"El código no admite vía laparoscópica: Honorarios usa el galeno "
            f"'{codigo_galeno}'"
        )

    nivel_origen = comp_hon.galeno_nivel
    if nivel_origen is None:
        raise ViaNoAplicableError(
            f"El código no admite vía laparoscópica: el galeno '{codigo_galeno}' "
            f"no está nivelado"
        )

    niveles = await niveles_vigentes(db, obra_social_nro, codigo_galeno)

    if len(niveles) == NIVELES_RECARGO:
        ajustados = [
            ComponenteLookupOut(
                componente_id=c.componente_id,
                concepto=c.concepto,
                tipo=c.tipo,
                galeno_id=c.galeno_id,
                galeno_codigo=c.galeno_codigo,
                galeno_nivel=c.galeno_nivel,
                cantidad=c.cantidad,
                valor_unitario=c.valor_unitario,
                subtotal=quantize_money(c.subtotal * RECARGO_10_NIVELES),
            )
            for c in componentes
        ]
        total = quantize_money(sum((c.subtotal for c in ajustados), Decimal("0")))
        return ajustados, total, nivel_origen

    if len(niveles) == NIVELES_SALTO_NIVEL:
        if not niveles or nivel_origen >= max(niveles):
            raise ViaNoAplicableError(
                f"Nivel {nivel_origen} es el tope del galeno '{codigo_galeno}'; "
                f"no admite laparoscopía"
            )
        nivel_destino = nivel_origen + 1
        galeno_origen = await _buscar_galeno_nivel(db, obra_social_nro, codigo_galeno, nivel_origen)
        galeno_destino = await _buscar_galeno_nivel(db, obra_social_nro, codigo_galeno, nivel_destino)
        if galeno_destino is None:
            raise ViaNoAplicableError(
                f"No hay galeno vigente de '{codigo_galeno}' nivel {nivel_destino} "
                f"para cotizar la vía laparoscópica"
            )

        ajustados = []
        for c in componentes:
            if c.tipo != "calculable" or c.galeno_codigo != codigo_galeno or c.cantidad == 0:
                ajustados.append(c)
                continue
            u_origen = _requiere_unidad(galeno_origen, c.concepto)
            u_destino = _requiere_unidad(galeno_destino, c.concepto)
            if u_origen:
                cantidad = (c.cantidad * u_destino / u_origen) if u_destino else c.cantidad
            elif u_destino:
                cantidad = u_destino
            else:
                cantidad = c.cantidad
            cantidad = Decimal(cantidad).quantize(Decimal("0.0001"))
            subtotal = quantize_money(cantidad * galeno_destino.valor_unitario)
            ajustados.append(ComponenteLookupOut(
                componente_id=c.componente_id,
                concepto=c.concepto,
                tipo=c.tipo,
                galeno_id=galeno_destino.id,
                galeno_codigo=galeno_destino.codigo,
                galeno_nivel=galeno_destino.nivel,
                cantidad=cantidad,
                valor_unitario=galeno_destino.valor_unitario,
                subtotal=subtotal,
            ))
        total = quantize_money(sum((c.subtotal for c in ajustados), Decimal("0")))
        return ajustados, total, nivel_destino

    raise ViaNoAplicableError(
        f"El galeno '{codigo_galeno}' de la OS {obra_social_nro} tiene {len(niveles)} "
        f"niveles; la laparoscopía solo está definida para galenos de "
        f"{NIVELES_SALTO_NIVEL} o {NIVELES_RECARGO} niveles"
    )
