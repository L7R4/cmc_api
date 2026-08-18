from typing import Optional

from pydantic import BaseModel, ConfigDict


class PlanillaOut(BaseModel):
    """Una fila de `avisos` con `AVISO_PLANILLA='P'`, ya resuelta para el front."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    #: `avisos.AVISO` — la descripción que carga el Colegio.
    descripcion: str
    #: Nombre del archivo tal como se muestra (sin el prefijo `planillas/`).
    archivo: str
    #: `avisos.FECHA` tal cual está en la base: conviven `YYYY-MM-DD` y
    #: `DD/MM/YYYY`. Se devuelve sin normalizar porque el front ya sabe leer las
    #: dos y reescribir 15 filas legacy no aporta nada.
    fecha: str
    #: Por dónde pedir el PDF. `None` cuando es una planilla vieja y el backend
    #: no tiene configurado `LEGACY_BASE_URL`: ahí el front la arma con su
    #: propia `VITE_URL_BASE_LEGACY`.
    url: Optional[str] = None
