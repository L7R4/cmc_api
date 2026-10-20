"""Documentación digital en el panel del prestador: arma lo que el listado devuelve de cada
prestación (sus archivos y si le faltan). El alta, el reemplazo y la baja de los archivos están
en `app/modules/facturacion/documentacion.py`."""
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetalleFacturacionCMC
from app.modules.facturacion import documentacion


async def enriquecer(
    db: AsyncSession, filas: Sequence[DetalleFacturacionCMC], dicts: Sequence[dict],
) -> None:
    """Agrega `documentos` y `documentacion` a cada dict (misma posición que su fila).

    `orden` se conserva para el front anterior: es el primer documento."""
    ids = [f.id_detalle_prestaciones for f in filas]
    docs = await documentacion.por_detalle(db, ids)
    exigen = await documentacion.exigen_en_bloque(db, filas)
    for f, d in zip(filas, dicts):
        lista = [documentacion.doc_out(x) for x in docs.get(f.id_detalle_prestaciones, [])]
        d["documentos"] = lista
        d["documentacion"] = documentacion.estado_de(f.id_detalle_prestaciones in exigen, len(lista))
        d["orden"] = lista[0]["url"] if lista else d.get("orden")
