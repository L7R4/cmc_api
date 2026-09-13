"""Endpoint propio de Nobis — no genérico, sólo esta obra social. El router
principal (`app/modules/validaciones/routes.py`) lo monta bajo `/nobis` en un
bucle sobre `obras.VALIDADORES`, no con un `include_router` a mano.
"""
from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_current_user
from app.modules.validaciones.obras.nobis import cliente as nobis

router = APIRouter()


@router.get("/afiliado")
async def consultar_afiliado(
    nro_afiliado: str = Query(..., min_length=1, max_length=30),
    user=Depends(get_current_user),
):
    """Estado del afiliado en vivo, para el aviso que el panel muestra debajo
    del campo mientras el prestador tipea — igual que el legacy
    (`nobis/api/consultar_afiliado.php` + el cartel verde/rojo de `nobis.php`).

    Es de sólo lectura: `ConsultarAfiliado` no crea ni modifica nada del lado
    de Nobis, así que no hace falta esperar al alta para avisar si el número
    está mal escrito.

    **No bloquea nada.** El legacy corre con el chequeo de activo
    deshabilitado (`$nobis_require_active = false` en `nobis.php`): el
    prestador puede cargar igual aunque el afiliado figure inactivo. Acá es
    la misma idea — el resultado es sólo informativo, `crear_prestacion()` no
    lo consulta.
    """
    res = await nobis.consultar_afiliado(numero_afiliado=nro_afiliado)
    return {
        # `nombre_afiliado` viene vacío cuando Nobis no encontró el número
        # (p.ej. "Afiliado inexistente"): es la única forma de distinguir
        # "no existe" de "existe pero está inactivo" con lo que devuelve
        # `interpretar_afiliado()`.
        "encontrado": res.nombre_afiliado is not None,
        "activo": res.autorizada,
        "nombre": res.nombre_afiliado,
        "estado": res.estado_detalle,
    }
