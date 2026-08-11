"""Control de calidad del padrón de socios ("sanity check").

**Es de SOLO LECTURA.** No corrige, no borra y no marca nada: sólo señala qué
filas tienen problemas para que alguien las revise. Esa es la decisión de diseño
central — un arreglo automático sobre 4.500 legajos, con datos que vienen
migrados de un sistema de treinta años, haría más daño que bien.

Cada chequeo es una consulta contra `listado_medico`, con su severidad:

    alta   → rompe algo o es imposible (duplicados, fechas del futuro)
    media  → falta un dato necesario para operar
    baja   → dato con formato dudoso, no bloquea nada
    info   → no es un error, es una situación que conviene ver

Los contadores salen todos de `COUNT(*)`: nunca se traen las filas para contarlas
en Python.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Fecha centinela que el sistema viejo usaba como "sin dato". Son 1.168 filas:
# tratarlas como "persona de 126 años" sería un falso positivo enorme que
# taparía las 4 fechas que sí están mal cargadas.
FECHA_CENTINELA = "1900-01-01"

# Techo de filas por chequeo. El detalle es para revisar a mano, no para
# exportar el padrón entero.
MAX_FILAS = 200


@dataclass(frozen=True)
class Chequeo:
    id: str
    titulo: str
    descripcion: str
    severidad: str          # alta | media | baja | info
    # WHERE que aísla las filas con el problema. Se usa igual para contar y
    # para listar, así el número del resumen y el detalle no pueden discrepar.
    where: str
    # Chequeos que se resuelven agrupando (duplicados) en vez de filtrando.
    group_by: Optional[str] = None


CHEQUEOS: tuple[Chequeo, ...] = (
    Chequeo(
        id="dni_duplicado",
        titulo="DNI repetido",
        descripcion=(
            "Dos o más legajos comparten el mismo documento. Suele ser la misma "
            "persona cargada dos veces, y hace que la facturación se le impute "
            "a un legajo distinto del que cobra."
        ),
        severidad="alta",
        where="DOCUMENTO > 0",
        group_by="DOCUMENTO",
    ),
    Chequeo(
        id="socio_duplicado",
        titulo="Número de socio repetido",
        descripcion=(
            "El número de socio identifica al profesional en toda la "
            "liquidación: si está repetido, no se sabe a quién corresponde cada "
            "prestación."
        ),
        severidad="alta",
        where="NRO_SOCIO IS NOT NULL",
        group_by="NRO_SOCIO",
    ),
    Chequeo(
        id="sin_nombre",
        titulo="Sin nombre",
        descripcion="El legajo no tiene nombre cargado.",
        severidad="alta",
        where="NOMBRE IS NULL OR TRIM(NOMBRE) = ''",
    ),
    Chequeo(
        id="nacimiento_futuro",
        titulo="Fecha de nacimiento en el futuro",
        descripcion=(
            "La fecha de nacimiento es posterior a hoy. Es un error de carga "
            "seguro, casi siempre un año mal tipeado."
        ),
        severidad="alta",
        where="FECHA_NAC IS NOT NULL AND FECHA_NAC > CURDATE()",
    ),
    Chequeo(
        id="edad_imposible",
        titulo="Edad imposible",
        descripcion=(
            "Más de 105 años según la fecha de nacimiento, sin contar la fecha "
            "centinela 01/01/1900 que el sistema viejo usaba como 'sin dato'."
        ),
        severidad="alta",
        where=(
            f"FECHA_NAC IS NOT NULL AND FECHA_NAC <> '{FECHA_CENTINELA}' "
            "AND FECHA_NAC <= CURDATE() "
            "AND TIMESTAMPDIFF(YEAR, FECHA_NAC, CURDATE()) > 105"
        ),
    ),
    Chequeo(
        id="edad_menor",
        titulo="Edad demasiado baja",
        descripcion=(
            "Menos de 20 años. No es imposible en sí, pero no se condice con un "
            "título de médico: casi siempre es la fecha mal cargada."
        ),
        severidad="media",
        where=(
            "FECHA_NAC IS NOT NULL AND FECHA_NAC <= CURDATE() "
            "AND TIMESTAMPDIFF(YEAR, FECHA_NAC, CURDATE()) < 20"
        ),
    ),
    Chequeo(
        id="nacimiento_centinela",
        titulo="Fecha de nacimiento sin cargar (01/01/1900)",
        descripcion=(
            "Traen la fecha centinela del sistema viejo. No es un error de "
            "datos: es un dato que nunca se cargó y conviene completar."
        ),
        severidad="baja",
        where=f"FECHA_NAC = '{FECHA_CENTINELA}'",
    ),
    Chequeo(
        id="sin_dni",
        titulo="Sin documento",
        descripcion=(
            "Sin DNI no se puede emitir la credencial ni cruzar el legajo con "
            "los padrones de las obras sociales."
        ),
        severidad="media",
        where="DOCUMENTO IS NULL OR DOCUMENTO <= 0",
    ),
    Chequeo(
        id="sin_matricula",
        titulo="Sin matrícula provincial",
        descripcion=(
            "La matrícula es lo que valida al profesional ante las obras "
            "sociales; varias integraciones la exigen para autorizar."
        ),
        severidad="media",
        where="MATRICULA_PROV IS NULL OR MATRICULA_PROV <= 0",
    ),
    Chequeo(
        id="sin_especialidad",
        titulo="Sin especialidad",
        descripcion=(
            "Sin especialidad no se resuelve qué códigos puede facturar ni qué "
            "valores le corresponden."
        ),
        severidad="media",
        where="NRO_ESPECIALIDAD IS NULL OR NRO_ESPECIALIDAD = 0",
    ),
    Chequeo(
        id="sin_contacto",
        titulo="Sin ningún dato de contacto",
        descripcion=(
            "No tiene teléfono, ni celular, ni e-mail: no hay forma de "
            "avisarle nada."
        ),
        severidad="media",
        where=(
            "COALESCE(NULLIF(TRIM(TELE_PARTICULAR), ''), "
            "NULLIF(TRIM(CELULAR_PARTICULAR), ''), "
            "NULLIF(TRIM(MAIL_PARTICULAR), '')) IS NULL"
        ),
    ),
    Chequeo(
        id="email_invalido",
        titulo="E-mail con formato inválido",
        descripcion="Tiene e-mail cargado pero no parece una dirección válida.",
        severidad="baja",
        where=(
            "MAIL_PARTICULAR IS NOT NULL AND TRIM(MAIL_PARTICULAR) <> '' "
            "AND MAIL_PARTICULAR NOT LIKE '%_@_%.__%'"
        ),
    ),
    Chequeo(
        id="cuit_malformado",
        titulo="CUIT con formato inválido",
        descripcion="Tiene CUIT cargado pero no son 11 dígitos.",
        severidad="baja",
        where=(
            "CUIT IS NOT NULL AND TRIM(CUIT) <> '' "
            "AND TRIM(CUIT) NOT REGEXP '^[0-9]{11}$'"
        ),
    ),
    Chequeo(
        id="inactivos",
        titulo="Legajos inactivos",
        descripcion=(
            "Marcados como inactivos. No es un error: se listan para poder "
            "revisar si corresponde darlos de baja o reactivarlos."
        ),
        severidad="info",
        where="EXISTE <> 'S' OR EXISTE IS NULL",
    ),
)

POR_ID = {c.id: c for c in CHEQUEOS}

# Columnas que se muestran en el detalle. Son las que hacen falta para
# identificar el legajo y entender el problema, nada más.
_COLS = (
    "ID, NRO_SOCIO, NOMBRE, DOCUMENTO, MATRICULA_PROV, "
    "FECHA_NAC, MAIL_PARTICULAR, EXISTE"
)


def _fecha(v) -> Optional[str]:
    """La fecha como texto ISO, venga como `date` o ya como `str`."""
    if not v:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


async def contar(db: AsyncSession) -> list[dict]:
    """Cuántos legajos afecta cada chequeo. Un COUNT por chequeo, sin traer filas."""
    salida = []
    for c in CHEQUEOS:
        if c.group_by:
            # Duplicados: cuántas FILAS están involucradas (no cuántos grupos),
            # que es lo que el operador tiene que revisar.
            sql = text(
                f"SELECT COUNT(*) FROM listado_medico WHERE {c.where} "
                f"AND {c.group_by} IN (SELECT {c.group_by} FROM listado_medico "
                f"WHERE {c.where} GROUP BY {c.group_by} HAVING COUNT(*) > 1)"
            )
        else:
            sql = text(f"SELECT COUNT(*) FROM listado_medico WHERE {c.where}")

        n = int((await db.execute(sql)).scalar_one() or 0)
        salida.append(
            {
                "id": c.id,
                "titulo": c.titulo,
                "descripcion": c.descripcion,
                "severidad": c.severidad,
                "casos": n,
            }
        )
    return salida


async def detalle(db: AsyncSession, chequeo_id: str, limit: int = 50) -> list[dict]:
    """Los legajos afectados por un chequeo, para revisarlos uno por uno."""
    c = POR_ID.get(chequeo_id)
    if c is None:
        return []

    tope = max(1, min(int(limit or 50), MAX_FILAS))

    if c.group_by:
        sql = text(
            f"SELECT {_COLS} FROM listado_medico WHERE {c.where} "
            f"AND {c.group_by} IN (SELECT {c.group_by} FROM listado_medico "
            f"WHERE {c.where} GROUP BY {c.group_by} HAVING COUNT(*) > 1) "
            # Las filas duplicadas van juntas: si no, el operador tiene que
            # buscar el par a ojo entre 200 resultados.
            f"ORDER BY {c.group_by}, NRO_SOCIO LIMIT {tope}"
        )
    else:
        sql = text(
            f"SELECT {_COLS} FROM listado_medico WHERE {c.where} "
            f"ORDER BY NRO_SOCIO LIMIT {tope}"
        )

    filas = (await db.execute(sql)).mappings().all()
    return [
        {
            "id": f["ID"],
            "nro_socio": f["NRO_SOCIO"],
            "nombre": f["NOMBRE"],
            "documento": f["DOCUMENTO"],
            "matricula_prov": f["MATRICULA_PROV"],
            # Con SQL crudo el driver puede devolver `date` o `str` según la
            # columna y la configuración: se contemplan los dos.
            "fecha_nac": _fecha(f["FECHA_NAC"]),
            "email": f["MAIL_PARTICULAR"],
            "activo": (f["EXISTE"] or "").upper() == "S",
        }
        for f in filas
    ]
