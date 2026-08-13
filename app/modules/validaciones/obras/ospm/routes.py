"""Endpoint propio de OSPM — no genérico, sólo esta obra social. El router
principal (`app/modules/validaciones/routes.py`) lo monta bajo `/ospm` en un
bucle sobre `obras.VALIDADORES`, no con un `include_router` a mano.
"""
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.database import get_db
from app.modules.validaciones.obras.ospm.padron import importar_padron_ospm

router = APIRouter()


@router.post("/padron")
async def importar_padron(
    archivo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Reemplaza el padrón de OSPM con el CSV/TXT que manda la obra social.

    Formato: `AFILIADO, DU, CUIT, ACTIVO`, separador `;` o `,`, con encabezado
    opcional. Es una operación del Colegio, no del prestador: pide
    `padron:editar` — igual que el resto de las mutaciones de padrón
    (`app/auth/authz.py`), que es donde se declara y se hace cumplir para toda
    ruta vía `enforce_authz` (`app/main.py`). No hace falta re-chequearlo acá.

    Reemplaza el contenido de `clientes_ospm`, el mismo padrón que carga
    `importar_padron_ospm.php`: el padrón es uno solo, así que el legacy y la API
    validan siempre contra el mismo dato.
    """
    return await importar_padron_ospm(db, archivo)
