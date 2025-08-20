from fastapi import APIRouter

# from app.api.v1.periodos    import router as periodos_router
# from app.api.v1.medicos     import router as medicos_router
# from app.api.v1.debitos     import router as debitos_router
# from app.api.v1.descuentos  import router as descuentos_router
from app.api.v1.liquidacion import router as liquidacion_router
from app.api.v1.exports import router as exports_router



api_router = APIRouter()
# api_router.include_router(medicos_router, prefix="/v1")
# api_router.include_router(periodos_router,    prefix="/periodos",    tags=["Periodos"])
# api_router.include_router(medicos_router,     prefix="/medicos",     tags=["Medicos"])
# api_router.include_router(debitos_router,     prefix="/debitos",     tags=["Debitos"])
# api_router.include_router(descuentos_router,  prefix="/descuentos",  tags=["Descuentos"])
api_router.include_router(liquidacion_router, prefix="/liquidacion", tags=["Liquidacion"])
api_router.include_router(exports_router, prefix="/exports", tags=["exports"])
