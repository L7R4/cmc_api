"""
Cuenta de la retención del Impuesto a las Ganancias (concepto 300), sin base de datos.

El Colegio es agente de retención: de lo que le paga a cada socio alcanzado se queda con la
parte del impuesto y la deposita en ARCA a su nombre. Es la misma cuenta del sistema anterior
(`MigrarFacturacion2Command.php`, líneas 595–707):

- Se mira el **mes del pago**, no cada pago suelto: el mínimo no imponible y los tramos son
  mensuales. Si el socio ya cobró en otro pago del mismo mes, la base es la suma y a la
  retención del mes se le resta lo que ya se le retuvo.
- Tres tipos de socio: sociedad (CUIT que empieza con 30), clínica (`es_organizacion`) y persona.
  Cada uno tiene su escala (mínimo no imponible + tramos), que vive en la tabla
  `retencion_ganancias_escala` con fecha de vigencia.
- Si la retención del mes no llega al mínimo de la escala ($50 para personas y clínicas), no se
  retiene nada. Si da negativa (en el mes ya se retuvo de más), tampoco: no se devuelve.
"""
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

TIPO_PERSONA = "persona"
TIPO_CLINICA = "clinica"
TIPO_JURIDICA = "juridica"
TIPOS_SUJETO = (TIPO_PERSONA, TIPO_CLINICA, TIPO_JURIDICA)

DOS_DECIMALES = Decimal("0.01")
CERO = Decimal("0")


@dataclass(frozen=True)
class Tramo:
    """Tramo sobre el excedente del mínimo no imponible: retención = fijo + (excedente − desde) × %."""
    desde: Decimal
    hasta: Optional[Decimal]  # None = sin tope
    importe_fijo: Decimal
    porcentaje: Decimal


@dataclass(frozen=True)
class Escala:
    id: Optional[int]
    tipo_sujeto: str
    minimo_no_imponible: Decimal
    retencion_minima: Decimal
    tramos: tuple[Tramo, ...]


def tipo_sujeto(cuit: Optional[str], es_organizacion: bool) -> str:
    """Como el sistema anterior: el prefijo 30 del CUIT manda sobre la marca de clínica."""
    if re.sub(r"\D", "", cuit or "").startswith("30"):
        return TIPO_JURIDICA
    return TIPO_CLINICA if es_organizacion else TIPO_PERSONA


def retencion_del_mes(base_mes: Decimal, escala: Escala) -> Decimal:
    """Retención que corresponde a todo lo cobrado en el mes, sin redondear."""
    excedente = base_mes - escala.minimo_no_imponible
    if excedente <= CERO:
        return CERO
    tramo = next(
        (t for t in escala.tramos if t.desde <= excedente and (t.hasta is None or excedente < t.hasta)),
        None,
    )
    if tramo is None:  # escala mal cargada (tramos con huecos): mejor no retener que inventar
        raise ValueError(f"La escala {escala.id} ({escala.tipo_sujeto}) no tiene tramo para {excedente}")
    return tramo.importe_fijo + (excedente - tramo.desde) * tramo.porcentaje / Decimal(100)


def retencion_del_pago(base_mes: Decimal, retenido_otros_pagos: Decimal, escala: Escala) -> tuple[Decimal, Decimal]:
    """(retención del mes, lo que se retiene en este pago), los dos redondeados a centavos."""
    del_mes = retencion_del_mes(base_mes, escala)
    en_este_pago = del_mes - retenido_otros_pagos
    if del_mes < escala.retencion_minima or en_este_pago < CERO:
        en_este_pago = CERO
    return (
        del_mes.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP),
        en_este_pago.quantize(DOS_DECIMALES, rounding=ROUND_HALF_UP),
    )
