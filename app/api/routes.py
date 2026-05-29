from fastapi import APIRouter

from app.modules.catalogs.routes_especialidades import router as especialidades_router
from app.modules.catalogs.routes_obras_sociales import router as obras_social_router
from app.modules.catalogs.routes_periodos import router as periodos_router
from app.modules.catalogs.routes_observaciones import router as observaciones_boletin_router
from app.modules.catalogs.routes_valores import router as valores_boletin_router
from app.modules.catalogs.routes_valores_eticos import router as valores_eticos_router
from app.modules.contenido.routes_noticias import router as noticias_router
from app.modules.contenido.routes_publicidad import router as publicidades_medico_router
from app.modules.deducciones.routes import router as deducciones_router
from app.modules.deducciones.routes_descuentos import router as descuentos_router
from app.modules.exports.routes import router as exports_router
from app.modules.liquidacion.routes import router as liquidacion_router
from app.modules.lotes.routes import router as lotes_router
from app.modules.medicos.routes import router as medicos_router
from app.modules.padrones.routes import router as padrones_router
from app.modules.pagos.routes import router as pagos_router
from app.modules.rbac.routes import router as rbac_router
from app.modules.solicitudes.routes import router as solicitudes_router

api_router = APIRouter()

api_router.include_router(medicos_router, prefix="/medicos", tags=["Medicos"])
api_router.include_router(padrones_router, prefix="/padrones", tags=["Padrones Médico"])
api_router.include_router(obras_social_router, prefix="/obras_social", tags=["Obras Sociales"])
api_router.include_router(especialidades_router, prefix="/especialidades", tags=["Especialidades"])
api_router.include_router(periodos_router, prefix="/periodos", tags=["Periodos"])
api_router.include_router(valores_boletin_router, prefix="/valores", tags=["ValoresBoletin"])
api_router.include_router(observaciones_boletin_router, prefix="/boletin", tags=["BoletinObservaciones"])
api_router.include_router(valores_eticos_router, prefix="/valores-eticos", tags=["ValoresEticos"])

api_router.include_router(pagos_router, prefix="/pagos", tags=["Pagos"])
api_router.include_router(lotes_router, prefix="/lotes", tags=["Lotes de Ajuste"])
api_router.include_router(liquidacion_router, prefix="/liquidacion", tags=["Liquidacion"])
api_router.include_router(deducciones_router, prefix="/deducciones", tags=["Deducciones - Generar"])
api_router.include_router(descuentos_router, prefix="/descuentos", tags=["Descuentos"])

api_router.include_router(solicitudes_router, prefix="/solicitudes", tags=["Solicitudes"])
api_router.include_router(noticias_router, prefix="/noticias", tags=["Noticias"])
api_router.include_router(publicidades_medico_router, prefix="/publicidad-medicos", tags=["publicidad-medicos"])
api_router.include_router(exports_router, prefix="/exports", tags=["exports"])

api_router.include_router(rbac_router, prefix="/admin/rbac", tags=["Rbac"])
