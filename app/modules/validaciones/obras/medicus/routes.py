"""Endpoints propios de Medicus. El router principal los monta bajo `/medicus`
en el bucle sobre `obras.VALIDADORES`, no con un `include_router` a mano.
"""
from fastapi import APIRouter, Depends, Query

from app.auth.deps import get_current_user
from app.modules.validaciones.obras._traditum import cliente as traditum
from app.modules.validaciones.obras.medicus import cliente as medicus

router = APIRouter()


@router.get("/estado")
async def estado_medicus(user=Depends(get_current_user)):
    """Contra qué está apuntando el backend.

    `simulado` no manda nada: sirve para probar la pantalla entera sin tocar
    Medicus. `test`/`produccion` generan autorizaciones reales.

    Es lo primero que mira la mesa de ayuda cuando algo no anda, así que expone
    también el modo del canal: con Traditum en simulado no sale nada aunque
    Medicus figure en produccion.
    """
    modo = medicus.modo_actual()
    modo_canal = traditum.modo_actual()
    return {
        "modo": modo,
        "modo_canal": modo_canal,
        "destino": (
            "no se envía nada (respuesta armada localmente)"
            if modo == medicus.MODO_SIMULADO or not traditum.sale_a_la_red()
            else traditum.base_url()
        ),
        "genera_autorizaciones_reales": (
            modo in (medicus.MODO_TEST, medicus.MODO_PRODUCCION)
            and traditum.sale_a_la_red()
        ),
        "iin": medicus.mensajes.IIN,
        # El entorno de prueba del canal sólo responde en días y horas hábiles;
        # sin esto, una prueba un sábado parece una caída del servicio.
        "nota_entorno_test": (
            "El entorno de testing de Traditum está activo de lunes a viernes de 8 a 20."
        ),
    }


@router.get("/elegibilidad")
async def elegibilidad_medicus(
    nro_afiliado: str = Query(..., min_length=6, max_length=13),
    user=Depends(get_current_user),
):
    """ZQI^Z01 — ¿el afiliado está activo?

    No genera consumo ni autorización: es sólo una consulta, y es lo que alimenta
    el cartel en vivo del formulario de carga (mismo patrón que Nobis). Nunca
    bloquea el alta; si falla, el front sigue dejando cargar.
    """
    try:
        res = await medicus.consultar_elegibilidad(nro_afiliado=nro_afiliado)
    except medicus.MedicusError as e:
        # 200 con `activo: false` y no un 5xx: para el formulario esto es
        # información, no un error que deba cortar nada.
        return {"activo": False, "nombre": "", "estado": str(e), "consultado": False}

    return {
        "activo": res.autorizada,
        "nombre": res.nombre_afiliado,
        "plan": res.plan,
        "estado": res.estado_detalle,
        "consultado": True,
    }
