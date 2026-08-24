"""Endpoint propio de Sancor — no genérico, sólo esta obra social. El router
principal (`app/modules/validaciones/routes.py`) lo monta bajo `/sancor` en un
bucle sobre `obras.VALIDADORES`, no con un `include_router` a mano.
"""
from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user
from app.core.config import settings
from app.modules.validaciones.obras.sancor import cliente as sancor
from app.modules.validaciones.obras.sancor import homologador

router = APIRouter()


@router.get("/estado")
async def estado_sancor(user=Depends(get_current_user)):
    """Contra qué autorizador de Sancor está apuntando el backend.

    `simulado` no manda nada a Sancor: sirve para probar la pantalla completa.
    `test`/`produccion` sí generan autorizaciones reales — `produccion` consume
    el token del afiliado de verdad.

    También expone los códigos bloqueados y las homologaciones, que antes vivían
    hardcodeadas en el front (`sancor_1.php`) sin que el backend supiera nada de
    ellas — un solo lugar de verdad para las dos puntas. Ver
    docs/api/validaciones/sancor.md.
    """
    modo = sancor.modo_actual()
    destino = {
        sancor.MODO_SIMULADO: "no se envía nada (respuesta armada localmente)",
        sancor.MODO_TEST: settings.SANCOR_URL_TEST,
        sancor.MODO_PRODUCCION: settings.SANCOR_URL_PROD,
    }.get(modo, "modo desconocido — se trata como test")
    return {
        "modo": modo,
        "destino": destino,
        "genera_autorizaciones_reales": modo in (sancor.MODO_TEST, sancor.MODO_PRODUCCION),
        "codigos_bloqueados": sorted(sancor.CODIGOS_NO_ADMITIDOS),
        "codigos_gestion_presencial": sorted(sancor.CODIGOS_GESTION_PRESENCIAL),
        # Qué código recibe Sancor cuando el médico elige otro. Sólo cambia lo
        # que se transmite: lo que se cotiza, se graba y se factura es siempre
        # el código del Colegio. `especialidad: null` = aplica a todas.
        "homologaciones": [
            {
                "codigo": codigo,
                "especialidad": entrada["especialidad"],
                "se_envia": entrada["codigo_homologado"],
            }
            for codigo, entradas in homologador.HOMOLOGACIONES.items()
            for entrada in entradas
        ],
    }
