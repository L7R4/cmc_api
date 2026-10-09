"""TXT de facturación de UNNE (O.S. 81) de una factura — SOLO LECTURA.

`GET /api/facturacion/facturas/{id}/export/unne.txt` (Herramientas → TXT UNNE). El tipo y el
número de factura salen de la cabecera; si todavía no está numerada (período abierto) se
pasan por parámetro.

## Formato (254 caracteres por línea, latin-1, CRLF también en la última)

    0   4  "99" + nº de obra social ("81")
    4   1  tipo de factura (A/B/C)
    5  14  nº de factura  (00031-00005130)
    19  6  período AAAAMM
    25  8  matrícula provincial del médico, con ceros a la izquierda
    33  3  función: 001 honorarios · 002 ayudante · 100 honorarios+gastos
    36 15  nº de orden de UNNE (cero a la izquierda)
    51  8  DNI del afiliado (cero a la izquierda)
    59 40  nombre del afiliado
    99 20  (en blanco)
   119  1  servicio: 1 ambulatorio · 2 internación/clínica
   120  8  fecha de la práctica DDMMAAAA
   128  6  código de la práctica
   134 12  "001100000000" fijo
   146  9  importe total de la línea, en centavos (ya multiplicado por cantidad y sesión)
   155  8  "00000000" fijo
   163 84  (en blanco)
   247  1  nº de vías        248 1 fin de semana (S/N)   249 1 nocturno (S/N)
   250  2  feriado (NN)     252 1 urgencia / vía (T/N)   253 1 categoría del médico (A/B/C)

Se dedujo comparando el `202608_81.txt` de agosto (1766 líneas) con `detalle_facturacion`: se
reproduce byte a byte salvo 40 fechas y 3 importes que se corrigieron en el sistema después de
armar ese archivo.

Las prestaciones nuevas no traen categoría, vías, urgencia, fin de semana ni feriado: se completa
con la categoría del médico y 1 / N / N / NN / N.
"""
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FacturacionCMC

ANCHO = 254
OBRA_SOCIAL = 81

FUNCION = {"H": "001", "A": "002", "HG": "100"}
FUNCION_G = "100"  # gastos solos: UNNE no tiene código propio conocido; va con Hon+Gto

SQL_FILAS = """
    select d.*, m.MATRICULA_PROV as matricula, m.CATEGORIA as categoria_medico
    from detalle_facturacion d
    left join listado_medico m on m.NRO_SOCIO = d.cod_med
    where d.cod_obr = :os and d.periodo = :periodo and d.estado <> 'X'
    order by d.id_detalle_prestaciones
"""


def _num(valor, ancho: int) -> str:
    return str(int(valor or 0)).zfill(ancho)[-ancho:]


def _centavos(importe) -> int:
    return int((Decimal(importe or 0) * 100).to_integral_value())


def _fecha(f) -> str:
    return f"{f.day:02d}{f.month:02d}{f.year:04d}"


def _sn(valor, defecto: str = "N") -> str:
    v = str(valor).strip() if valor is not None else ""
    return v[:1] or defecto


def linea(r: dict, prefijo: str) -> str:
    """Una línea del archivo. `r` trae la fila de detalle y los datos del médico."""
    tpo = (r["tpo_funcion"] or "H").upper()
    funcion = FUNCION.get(tpo, FUNCION_G if tpo == "G" else FUNCION["H"])
    # El importador de UNNE guarda el nº de orden de UNNE en `autorizacion` (y deja en
    # `nro_orden` el id de la fila); las cargas del legacy lo traen en `nro_orden`.
    orden = (r["autorizacion"] or "").strip()
    if not orden.isdigit():
        orden = str(r["nro_orden"] or "")
    internacion = (r["tpo_serv"] or "").upper() in ("H", "I") or (r["tpo_serv"] is None and r["tipo_orden"] == "S")
    categoria = (r["categoria"] or r["categoria_medico"] or " ").strip()[:1] or " "
    s = (
        prefijo
        + _num(r["matricula"], 8)
        + funcion
        + _num(orden, 15)
        + _num(r["dni_p"], 8)
        + (r["nom_ape_p"] or "").strip()[:40].ljust(40)
        + " " * 20
        + ("2" if internacion else "1")
        + _fecha(r["fecha_practica"])
        + (r["cod_nom"] or "").strip()[:6].ljust(6)
        + "001100000000"
        + _num(_centavos(r["importe_total"]), 9)
        + "00000000"
        + " " * 84
        + _sn(r["nro_vias"], "1")
        + _sn(r["fin_semana"])
        + _sn(r["nocturno"])
        + (r["feriado"] or "NN").ljust(2)[:2]
        + _sn(r["urgencia"])
        + categoria
    )
    assert len(s) == ANCHO, (len(s), r["id_detalle_prestaciones"])
    return s


@dataclass
class TxtUnne:
    contenido: bytes
    nombre_archivo: str
    lineas: int
    total: Decimal


async def generar(
    db: AsyncSession, factura: FacturacionCMC, tipo: str | None = None, nro_factura: str | None = None,
) -> TxtUnne:
    """Arma el TXT de `factura`. `tipo`/`nro_factura` pisan los de la cabecera."""
    if str(factura.cod_obr).strip() != str(OBRA_SOCIAL):
        raise HTTPException(422, f"El TXT de UNNE es sólo de la obra social {OBRA_SOCIAL}.")
    tipo = (tipo or factura.tipo_factura or "").strip().upper()
    nro = (nro_factura or factura.nro_factura or "").strip()
    if not tipo or not nro:
        raise HTTPException(
            422, "La factura todavía no tiene tipo ni número: indicalos para armar el archivo.",
        )
    if len(tipo) != 1 or not tipo.isalpha():
        raise HTTPException(422, "El tipo de factura es una sola letra (A, B, C…).")
    if len(nro) > 14:
        raise HTTPException(422, "El número de factura tiene como máximo 14 caracteres (00031-00005130).")

    periodo = factura.periodo
    filas = (await db.execute(text(SQL_FILAS), {"os": OBRA_SOCIAL, "periodo": periodo})).mappings().all()
    filas = [f for f in filas if f["version"] == factura.version]
    if not filas:
        raise HTTPException(404, "La factura no tiene prestaciones para exportar.")
    sin_matricula = sorted({f["cod_med"] for f in filas if not f["matricula"]})
    if sin_matricula:
        raise HTTPException(
            422, f"Médicos sin matrícula provincial (nº de socio): {', '.join(map(str, sin_matricula[:20]))}.",
        )
    prefijo = f"99{OBRA_SOCIAL}{tipo}{nro.ljust(14)}{periodo}"
    lineas = [linea(dict(f), prefijo) for f in filas]
    return TxtUnne(
        contenido="".join(l + "\r\n" for l in lineas).encode("latin-1", errors="replace"),
        nombre_archivo=f"{periodo}_{OBRA_SOCIAL}.txt",
        lineas=len(lineas),
        total=sum((Decimal(f["importe_total"] or 0) for f in filas), Decimal(0)),
    )
