"""Credenciales compartidas de los portales externos de obras sociales.

Algunas obras sociales no se autorizan desde el panel sino en su propio portal,
con un usuario y una clave que el Colegio comparte con sus prestadores (hoy,
Unión Personal). La guía del panel los mostraba escritos en el código del
front: terminaban en un archivo del sitio que cualquiera podía bajar sin
iniciar sesión, y en el repositorio.

Acá salen de la configuración del servidor y sólo se entregan a quien tiene
sesión. Los ve cualquier usuario del panel —médicos y personal del Colegio—
menos las cuentas de organización (clínicas), que no validan prestaciones.

No lleva scope propio: la ruta se declara `SOLO_AUTENTICADO` en
`app/auth/authz.py` y la regla de las organizaciones se aplica acá.
"""
from typing import Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.db.models import ListadoMedico

router = APIRouter()


class CredencialOut(BaseModel):
    """Un dato de acceso, con el rótulo con el que se muestra."""

    label: str
    valor: str


#: `slug` del portal (el mismo del front) → de dónde salen su usuario y su clave.
#: Sumar un portal es una línea acá y sus dos variables en `Settings`.
PORTALES: Dict[str, Callable[[], Tuple[SecretStr, SecretStr]]] = {
    "upcn": lambda: (settings.UPCN_PORTAL_USUARIO, settings.UPCN_PORTAL_CLAVE),
    "iosfa": lambda: (settings.IOSFA_PORTAL_USUARIO, settings.IOSFA_PORTAL_CLAVE),
    "medife": lambda: (settings.MEDIFE_PORTAL_USUARIO, settings.MEDIFE_PORTAL_CLAVE),
    "prevencion-salud": lambda: (settings.PREVENCION_PORTAL_USUARIO, settings.PREVENCION_PORTAL_CLAVE),
}


async def _es_organizacion(db: AsyncSession, user: dict) -> Optional[bool]:
    """`listado_medico.es_organizacion` del dueño del token. `None` si no existe."""
    uid = user.get("uid")
    if uid is not None:
        stmt = select(ListadoMedico.es_organizacion).where(ListadoMedico.ID == int(uid))
    else:
        # Tokens viejos sin `uid`: el JWT siempre trae el número de socio.
        try:
            nro_socio = int(user["nro_socio"])
        except (KeyError, TypeError, ValueError):
            return None
        stmt = select(ListadoMedico.es_organizacion).where(ListadoMedico.NRO_SOCIO == nro_socio)
    fila = (await db.execute(stmt)).first()
    return None if fila is None else bool(fila[0])


@router.get("/portales/{slug}/credenciales", response_model=List[CredencialOut])
async def credenciales_portal(
    slug: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Usuario y clave compartidos de un portal externo.

    Falla cerrado: si no se puede determinar quién pregunta, no se entregan.
    """
    origen = PORTALES.get(slug)
    if origen is None:
        raise HTTPException(404, "Ese portal no tiene credenciales compartidas")

    es_org = await _es_organizacion(db, user)
    if es_org is None or es_org:
        raise HTTPException(403, "Las credenciales de portales no están disponibles para esta cuenta")

    usuario, clave = (campo.get_secret_value().strip() for campo in origen())
    if not usuario:
        # Falta cargarlas en el `.env` del servidor.
        raise HTTPException(404, "Las credenciales de este portal no están cargadas")

    # Que ni el navegador ni un proxy las guarden.
    response.headers["Cache-Control"] = "no-store"
    datos = [CredencialOut(label="Usuario", valor=usuario)]
    # Hay portales que sólo piden usuario (IOSFA).
    if clave:
        datos.append(CredencialOut(label="Clave", valor=clave))
    return datos
