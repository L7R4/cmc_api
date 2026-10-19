"""TXT de prestaciones de DASUTEN (O.S. 137 — UTN) de una factura — SOLO LECTURA.

`GET /api/facturacion/facturas/{id}/export/dasuten.txt` (Herramientas → TXT DASUTEN).
Reemplaza al `listado_dasuten_txt.php` del legacy. Sin encabezado, una línea por prestación
(129 caracteres, latin-1, CRLF también en la última):

    0   2  tipo de documento, siempre "DU"
    2  10  nº de afiliado (los primeros 9 caracteres), a la izquierda
    12  3  rubro, siempre "002"
    15  9  código de la práctica (los primeros 8 caracteres)
    24 10  fecha de la práctica DD/MM/AAAA
    34 30  descripción del código (los primeros 29 caracteres)
    64  3  cantidad
    67 12  monto en centavos, completado con ceros a la derecha (como el legacy)
    79 30  nombre del médico en mayúsculas (los primeros 29 caracteres)
    109 2  "0"
    111 5  "0"
    116 3  plan, siempre "1"
    119 10 coseguro en centavos, completado con ceros a la derecha (como el legacy)

- Monto = (honorarios + gastos) x cantidad x sesión; en las filas de ayudante (`tpo_funcion = 'A'`)
  se usa el valor del ayudante en lugar de los honorarios. No descuenta el coseguro.
- Coseguro = el campo `coseguro` de la prestación, tal cual (sin multiplicar).
- El afiliado sale de `dni_p`; en las prestaciones migradas del legacy (donde está vacío) se lee de
  `guardar_atencion` por `ga_id`.
- Orden: categoría del médico, nombre del médico, código (el del legacy).

OJO: el monto y el coseguro se completan con ceros a la DERECHA (`STR_PAD_RIGHT` del legacy), de
modo que $ 24.802,82 sale como `248028200000` y no como `000002480282`. Es a pedido: es lo que
venía entregando el legacy. Para pasar a ceros a la izquierda alcanza con cambiar `_centavos_txt`.
"""
import datetime
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FacturacionCMC
from app.modules.facturacion.export.omint_xlsx import descripciones_de

ANCHO = 129
OBRA_SOCIAL = 137

SQL_FILAS = """
    select d.id_detalle_prestaciones as id, coalesce(nullif(nullif(trim(d.dni_p), ''), '0'), nullif(nullif(trim(g.NRO_AFILIADO), ''), '0')) as beneficiario, d.cod_nom, d.fecha_practica, d.cantidad, d.sesion,
           d.coseguro, d.honorarios, d.gastos, d.ayudante, d.tpo_funcion, d.importe_total, d.version, d.cod_med, m.NOMBRE as prestador
    from detalle_facturacion d
    left join listado_medico m on m.NRO_SOCIO = d.cod_med
    left join guardar_atencion g on g.ID = d.ga_id
    where d.cod_obr = :os and d.periodo = :periodo and d.estado <> 'X'
    order by coalesce(nullif(d.categoria, ''), m.CATEGORIA, ''), m.NOMBRE, d.cod_nom, d.id_detalle_prestaciones
"""


def _texto(valor, largo: int) -> str:
    """Como el `fchar` del legacy: espacios colapsados, recortado a `largo` y relleno a la derecha."""
    return " ".join(str(valor if valor is not None else "").split())[:largo].ljust(largo)


def _centavos(importe) -> int:
    return int((Decimal(importe or 0) * 100).to_integral_value())


def _centavos_txt(importe, ancho: int) -> str:
    # Ceros a la derecha, igual que el legacy (no es un número alineado a la derecha).
    return str(_centavos(importe)).ljust(ancho, "0")[:ancho]


def _unidades(r) -> int:
    return max(1, int(r["cantidad"] or 0)) * max(1, int(r["sesion"] or 0))


def _monto(r) -> Decimal:
    # Los ayudantes son filas propias: su valor está en `ayudante`, no en `honorarios`.
    base = r["ayudante"] if (r["tpo_funcion"] or "").upper() == "A" else r["honorarios"]
    return (Decimal(base or 0) + Decimal(r["gastos"] or 0)) * _unidades(r)


def linea(r, descripcion: str) -> str:
    """Una línea del archivo. `r` trae la fila del detalle y el nombre del médico."""
    fecha = r["fecha_practica"]
    s = (
        "DU"
        + _texto(str(r["beneficiario"] or "").strip()[:9], 10)
        + "002"
        + _texto(str(r["cod_nom"] or "").strip()[:8], 9)
        + (fecha.strftime("%d/%m/%Y") if isinstance(fecha, datetime.date) else " " * 10)
        + _texto(descripcion[:29], 30)
        + _texto(max(1, int(r["cantidad"] or 0)), 3)
        + _centavos_txt(_monto(r), 12)
        + _texto(str(r["prestador"] or "").upper()[:29], 30)
        + _texto("0", 2)
        + _texto("0", 5)
        + _texto("1", 3)
        + _centavos_txt(r["coseguro"], 10)
    )
    assert len(s) == ANCHO, (len(s), r["id"])
    return s


@dataclass
class TxtDasuten:
    contenido: bytes
    nombre_archivo: str
    lineas: int
    total: Decimal
    coseguro: Decimal


async def generar(db: AsyncSession, factura: FacturacionCMC) -> TxtDasuten:
    """Arma el TXT de `factura` (período cerrado o abierto)."""
    if str(factura.cod_obr).strip() != str(OBRA_SOCIAL):
        raise HTTPException(422, f"El TXT de DASUTEN es sólo de la obra social {OBRA_SOCIAL}.")
    periodo = factura.periodo
    filas = (await db.execute(text(SQL_FILAS), {"os": OBRA_SOCIAL, "periodo": periodo})).mappings().all()
    filas = [f for f in filas if f["version"] == factura.version]
    if not filas:
        raise HTTPException(404, "La factura no tiene prestaciones para exportar.")
    # Sin nº de afiliado (ni en el detalle ni en el legacy) la línea no sirve.
    incompletas = [f["id"] for f in filas if not str(f["beneficiario"] or "").strip() or f["fecha_practica"] is None]
    if incompletas:
        raise HTTPException(
            422,
            f"{len(incompletas)} prestaciones sin nº de afiliado o sin fecha "
            f"(id de detalle: {', '.join(map(str, incompletas[:20]))}). Completalas para armar el archivo.",
        )
    descripciones = await descripciones_de(db, {str(f["cod_nom"] or "").strip() for f in filas})
    lineas = [linea(f, descripciones.get(str(f["cod_nom"] or "").strip(), "")) for f in filas]
    return TxtDasuten(
        contenido="".join(l + "\r\n" for l in lineas).encode("latin-1", errors="replace"),
        nombre_archivo=f"prestacionesDASUTEN_{periodo}.txt",
        lineas=len(lineas),
        total=sum((_monto(f) for f in filas), Decimal(0)),
        coseguro=sum((Decimal(f["coseguro"] or 0) for f in filas), Decimal(0)),
    )
