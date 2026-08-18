"""Importación del padrón de OSPM (`clientes_ospm`) desde el CSV/TXT que manda
la obra social. Es una operación del Colegio, no del prestador — ver el scope
`padron:editar` en `app/auth/authz.py`.
"""
import csv

from fastapi import HTTPException, UploadFile
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ClientesOspm
from app.db.models.padron_ospm import OSPM_ACTIVO, OSPM_INACTIVO


def _parsear(contenido: bytes) -> list[dict]:
    """Parsea el CSV/TXT del padrón que manda OSPM.

    Formato heredado de `importar_padron_ospm.php`: columnas
    `AFILIADO, DU, CUIT, ACTIVO`, separador `;` o `,` (se detecta con la primera
    línea), encabezado opcional que arranca con `AYN` o `AFILIADO`, y texto en
    ISO-8859-1. Se intenta UTF-8 primero porque los archivos nuevos ya vienen
    así; si falla, latin-1, que nunca rompe.
    """
    try:
        texto = contenido.decode("utf-8")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")

    lineas = [ln for ln in texto.splitlines() if ln.strip()]
    if not lineas:
        raise HTTPException(422, "El archivo del padrón está vacío.")

    delimitador = ";" if ";" in lineas[0] else ","
    filas: list[dict] = []
    vistos: set[str] = set()

    for i, linea in enumerate(csv.reader(lineas, delimiter=delimitador)):
        if not linea:
            continue
        primera = (linea[0] or "").strip().upper()
        if i == 0 and primera in ("AYN", "AFILIADO"):
            continue

        nombre = (linea[0] or "").strip() if len(linea) > 0 else ""
        documento = (linea[1] or "").strip() if len(linea) > 1 else ""
        cuit = (linea[2] or "").strip() if len(linea) > 2 else ""
        activo = (linea[3] or "").strip().upper() if len(linea) > 3 else ""

        if not documento:
            continue  # fila sin DNI: no se puede buscar por ella, no sirve
        if documento in vistos:
            continue  # el padrón trae repetidos; gana el primero
        vistos.add(documento)

        # Se mapea a las columnas del legacy (`clientes_ospm`), con SUS límites:
        # DU varchar(8), CUIT varchar(11), AFILIADO varchar(30). Todas NOT NULL.
        filas.append(
            {
                "DU": documento[:8],
                "CUIT": cuit[:11],
                "AFILIADO": (nombre[:30] or "SIN NOMBRE"),
                "ACTIVO": OSPM_ACTIVO if activo == "S" else OSPM_INACTIVO,
            }
        )

    if not filas:
        raise HTTPException(
            422, "No se encontró ninguna fila válida. Se espera AFILIADO, DU, CUIT, ACTIVO."
        )
    return filas


async def importar_padron_ospm(db: AsyncSession, archivo: UploadFile) -> dict:
    """Reemplaza el padrón de OSPM con el del archivo.

    A diferencia del legacy —que hace `TRUNCATE` y recién después parsea, así
    que un archivo malo deja el padrón vacío y nadie valida— acá se parsea
    **primero** y el borrado va en la misma transacción que la carga: si algo
    falla, el padrón anterior queda intacto.
    """
    contenido = await archivo.read()
    filas = _parsear(contenido)

    await db.execute(delete(ClientesOspm))
    db.add_all([ClientesOspm(**f) for f in filas])
    await db.commit()

    activos = sum(1 for f in filas if f["ACTIVO"] == OSPM_ACTIVO)
    return {
        "importados": len(filas),
        "activos": activos,
        "inactivos": len(filas) - activos,
    }
