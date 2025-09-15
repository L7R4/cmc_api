from fastapi import APIRouter

# from app.api.v1.periodos    import router as periodos_router
from app.api.v1.medicos     import router as medicos_router
from app.api.v1.debitos     import router_dc as debitos_router
from app.api.v1.obra_social import router as obras_social_router
from app.api.v1.descuentos  import router as descuentos_router
from app.api.v1.liquidacion import router as liquidacion_router
from app.api.v1.exports import router as exports_router
from app.api.v1.especialidades import router as especialidades_router
from app.api.v1.asignaciones import router as asignaciones_router
from app.api.v1.deducciones import router as deducciones_router





api_router = APIRouter()
# api_router.include_router(medicos_router, prefix="/v1")
api_router.include_router(especialidades_router,    prefix="/especialidades", tags=["Especialidades"])
api_router.include_router(deducciones_router,    prefix="/deducciones", tags=["Deducciones - Generar"])
api_router.include_router(medicos_router,     prefix="/medicos",     tags=["Medicos"])
api_router.include_router(obras_social_router, prefix="/obras_social", tags=["Obras Sociales"])
api_router.include_router(debitos_router,     prefix="/debitos_creditos",     tags=["Debitos / Creditos"])
api_router.include_router(descuentos_router,  prefix="/descuentos",  tags=["Descuentos"])
api_router.include_router(exports_router, prefix="/exports", tags=["exports"])
api_router.include_router(liquidacion_router, prefix="/liquidacion", tags=["Liquidacion"])
api_router.include_router(asignaciones_router,    prefix="/medicos", tags=["Asignaciones Médico"])

