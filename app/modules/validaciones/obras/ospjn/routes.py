"""Endpoint propio de OSPJN — no genérico, sólo esta obra social. El router
principal (`app/modules/validaciones/routes.py`) lo monta bajo `/ospjn` en un
bucle sobre `obras.VALIDADORES`, no con un `include_router` a mano.
"""
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user
from app.core.config import settings
from app.modules.validaciones.obras.ospjn import cliente as ospjn

router = APIRouter()


@router.get("/estado")
async def estado_ospjn(user=Depends(get_current_user)):
    """Contra qué ambiente de OSPJN está apuntando el backend.

    `simulado` no manda nada a OSPJN: sirve para probar la pantalla completa
    sin gastar consultas reales. `test`/`produccion` sí hablan con el servicio
    real de OSPJN (`Ingresar` + `ValidarAfiliado`). No expone credenciales —
    sólo el modo activo y la URL destino. Pensado como smoke test manual antes
    de cargar una prestación real, igual que `GET /sancor/estado`. Ver
    docs/api/validaciones/ospjn.md.
    """
    modo = ospjn.modo_actual()
    destino = {
        ospjn.MODO_SIMULADO: "no se envía nada (respuesta armada localmente)",
        ospjn.MODO_TEST: settings.OSPJN_URL_TEST,
        ospjn.MODO_PRODUCCION: settings.OSPJN_URL_PROD,
    }.get(modo, "modo desconocido — se trata como test")
    return {
        "modo": modo,
        "destino": destino,
        "genera_validaciones_reales": modo in (ospjn.MODO_TEST, ospjn.MODO_PRODUCCION),
        # OSPJN admite 'CON'/'OTR' en `CodigoPrestacion`, pero hoy sólo se envía
        # 'CON' — ver el comentario en `validador.py::validar()`.
        "categoria_enviada": ospjn.CATEGORIA_CONSULTA,
    }
